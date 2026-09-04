"""Legal/ethical compliance for web-sourced RAG content (2026-08-30).

Ergänzt die lokale, personal-use RAG-Persistierung (siehe docs/18_LEGAL_WEB_PERSIST.md)
um ein konservatives Compliance-Modell:

1. **robots.txt** — via stdlib ``urllib.robotparser``. Pro-Domain-Cache mit
   ~1 h TTL (negatives Cache bei Fetch-Fehler: 60 s, verhindert Hammerschlag).
2. **Response-Header** — ``X-Robots-Tag`` / ``Googlebot`` (noindex/nofollow/...)
   sowie ``Cache-Control`` / ``Pragma: no-store``.
3. **HTML-``<meta>``** — ``<meta name="robots" ...>`` / ``name="googlebot"``.

**Fail-open:** Schlägt der robots.txt-Fetch fehl (Netzwerk, Timeout, HTTP-Fehler),
wird die URL mit WARNING-Log erlaubt — eine nicht erreichbare robots.txt darf die
Persistierung nicht brechen. Explizite Opt-Out-Signale (robots-Disallow,
noindex, no-store) blocken dagegen hart.

**Retention:** Web-sourced Chunks bekommen bei der Persistierung ein
``retention_until``-Metadata-Feld (Default 30 Tage, ``WEB_RETENTION_DAYS``,
``0`` = unbegrenzt). Das eigentliche Löschen macht
``UnifiedRagStore.prune_web_content()`` (lokal, nur ``source_type LIKE 'web%'``).

Konfiguration (ENV):
    WEB_COMPLIANCE_ENABLED  Master-Switch (Default: aktiv; "0"/"false"/"off"/"no" deaktiviert)
    WEB_RETENTION_DAYS      Retentionsfenster in Tagen (Default: 30; 0 = unbegrenzt)

Nur Python-Stdlib — keine neuen Abhängigkeiten, keine DB-Schema-Änderungen.
"""

from __future__ import annotations

import logging
import os
import re
import threading
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

logger = logging.getLogger(__name__)

# ── Konfiguration ────────────────────────────────────────────────────────────
ENV_COMPLIANCE_ENABLED = "WEB_COMPLIANCE_ENABLED"
ENV_RETENTION_DAYS = "WEB_RETENTION_DAYS"

DEFAULT_RETENTION_DAYS = 30
DEFAULT_ROBOTS_TTL_SECONDS = 3600.0        # ~1 h pro-Domain-Cache
NEGATIVE_CACHE_TTL_SECONDS = 60.0          # nach Fetch-Fehler: kürzer neu probieren
ROBOTS_FETCH_TIMEOUT_SECONDS = 5.0
ROBOTS_MAX_BYTES = 1024 * 1024             # 1 MB robots.txt reichen völlig
DEFAULT_USER_AGENT = "bot6-local-rag/1.0 (personal-use; local RAG store)"

# Direktiven, die für die Persistierung blockierend sind.
_BLOCKING_ROBOTS_DIRECTIVES = {
    "noindex", "nofollow", "noarchive", "nosnippet", "noimageindex", "nocache",
}


# ── ENV-Helfer ───────────────────────────────────────────────────────────────
def _env_flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or not str(raw).strip():
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def is_compliance_enabled() -> bool:
    """Master-Switch für die gesamte Compliance-Schicht (Default: aktiv)."""
    return _env_flag(ENV_COMPLIANCE_ENABLED, True)


def get_retention_days() -> Optional[int]:
    """WEB_RETENTION_DAYS → int-Tage; ``0``/negativ → None (unbegrenzt).

    Ungültige Werte fallen auf den Default (30) zurück und werden gewarnt.
    """
    raw = os.getenv(ENV_RETENTION_DAYS)
    if raw is None or not str(raw).strip():
        return DEFAULT_RETENTION_DAYS
    try:
        days = int(str(raw).strip())
    except ValueError:
        logger.warning(
            "[WEB-COMPLIANCE] Ungültiger WEB_RETENTION_DAYS-Wert %r – nutze Default %d",
            raw, DEFAULT_RETENTION_DAYS,
        )
        return DEFAULT_RETENTION_DAYS
    if days < 0:
        logger.warning(
            "[WEB-COMPLIANCE] WEB_RETENTION_DAYS=%d negativ – Retention auf unbegrenzt gesetzt",
            days,
        )
        return None
    return days


def retention_until_iso() -> Optional[str]:
    """ISO-8601-Zeitstempel (UTC) für jetzt+Retention, oder None bei unbegrenzt."""
    days = get_retention_days()
    if not days:
        return None
    return (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()


# ── Entscheidungstyp ─────────────────────────────────────────────────────────
@dataclass(frozen=True)
class ComplianceDecision:
    """Ergebnis einer Compliance-Prüfung.

    ``reasons`` enthält menschenlesbare Block-Gründe (leer, wenn erlaubt).
    """

    allowed: bool
    reasons: Tuple[str, ...] = field(default_factory=tuple)

    def detail(self) -> str:
        if self.allowed or not self.reasons:
            return "allowed"
        return "; ".join(self.reasons)


# ── robots.txt ───────────────────────────────────────────────────────────────
class RobotsChecker:
    """Thread-sicherer robots.txt-Checker mit pro-Domain-Cache (~1 h TTL).

    Fail-open: Bei Fetch-Fehlern (Netzwerk, Timeout, HTTP-Fehler) wird die
    URL mit WARNING-Log erlaubt. Negative Cache-Einträge (Fetch-Fehler)
    nutzen eine kürzere TTL, damit Transientes nicht 1 h wirkt.

    ``fetcher`` ist injizierbar für Tests (Signatur: ``(robots_url) -> str``).
    """

    def __init__(
        self,
        ttl_seconds: float = DEFAULT_ROBOTS_TTL_SECONDS,
        fetcher: Optional[Callable[[str], str]] = None,
        user_agent: str = DEFAULT_USER_AGENT,
    ) -> None:
        self._ttl = max(0.0, float(ttl_seconds))
        self._user_agent = user_agent
        self._fetcher: Callable[[str], str] = fetcher or self._default_fetcher
        self._cache: Dict[str, Tuple[float, Optional[Any], bool]] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _default_fetcher(robots_url: str) -> str:
        """Lädt robots.txt via stdlib urllib (kein requests-Import nötig)."""
        req = urllib.request.Request(
            robots_url, headers={"User-Agent": DEFAULT_USER_AGENT}
        )
        with urllib.request.urlopen(req, timeout=ROBOTS_FETCH_TIMEOUT_SECONDS) as resp:
            status = int(getattr(resp, "status", 200) or 200)
            if status in (401, 403):
                raise PermissionError(f"robots.txt HTTP {status}")
            raw = resp.read(ROBOTS_MAX_BYTES)
        return raw.decode("utf-8", errors="replace")

    @staticmethod
    def _domain_of(url: str) -> Optional[str]:
        from urllib.parse import urlparse

        if not url or not isinstance(url, str):
            return None
        try:
            parsed = urlparse(url)
        except (ValueError, TypeError):
            return None
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            return None
        return parsed.netloc.lower()

    @staticmethod
    def _normalize_robots_text(text: str) -> str:
        """Robustifiziert robots.txt gegen eine CPython-Quirk.

        ``urllib.robotparser`` verwirft Allow/Disallow-Direktiven still, wenn
        sie vor dem ersten ``User-agent``-Block stehen. Viele kleine Sites
        publizieren eine robots.txt mit nur ``Disallow: /`` (ohne
        User-Agent), was „für alle verboten“ bedeutet. Vor dem ersten
        User-agent-Block stehende Direktiven werden mit ``User-agent: *``
        versehen, damit sie wirksam werden.
        """
        seen_user_agent = False
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            key = stripped.split(":", 1)[0].strip().lower()
            if key == "user-agent":
                seen_user_agent = True
                continue
            if key in ("disallow", "allow") and not seen_user_agent:
                return "User-agent: *\n" + text
        return text

    def _fetch_parser(self, domain: str) -> Tuple[Optional[Any], bool]:
        """Lädt + parst robots.txt. Returns (parser, fetch_failed)."""
        from urllib import robotparser

        robots_url = f"https://{domain}/robots.txt"
        try:
            text = self._fetcher(robots_url)
        except Exception as exc:  # noqa: BLE001 — Fail-open ist hier Design
            logger.warning(
                "[WEB-COMPLIANCE] robots.txt-Fetch fehlgeschlagen (%s): %s — fail-open",
                domain, exc,
            )
            return None, True
        parser = robotparser.RobotFileParser()
        try:
            parser.parse(self._normalize_robots_text(text).splitlines())
        except Exception as exc:  # noqa: BLE001 — kaputte robots.txt ≠ Verbot
            logger.warning(
                "[WEB-COMPLIANCE] robots.txt-Parse fehlgeschlagen (%s): %s — fail-open",
                domain, exc,
            )
            return None, True
        return parser, False

    def is_allowed(self, url: str) -> Tuple[bool, str]:
        """Prüft robots.txt für ``url``. Returns ``(allowed, reason)``.

        Reason-Werte: ``skipped`` (keine http(s)-URL), ``fail_open`` (Fetch-
        fehler, bewusst erlaubt), ``robots_allow``, ``robots_disallow``.

        Regelauflösung: erst Agent-spezifische Entries (Dateireihenfolge),
        dann die ``*``-Default-Entry; unter den passenden Regeln gewinnt die
        spezifischste (längste) Regel — RFC 9309 §3. CPython selbst nimmt die
        erste passende Regel, was z. B. ``Disallow: /`` vor
        ``Allow: /public`` falsch blockt — daher die eigene Regelwahl.
        """
        domain = self._domain_of(url)
        if domain is None:
            return True, "skipped"

        now = time.monotonic()
        with self._lock:
            entry = self._cache.get(domain)
            if entry is not None and entry[0] > now:
                _, parser, _ = entry
            else:
                parser, failed = self._fetch_parser(domain)
                ttl = NEGATIVE_CACHE_TTL_SECONDS if failed else self._ttl
                self._cache[domain] = (now + ttl, parser, failed)

        if parser is None:
            return True, "fail_open"
        try:
            entry = self._select_entry(parser)
            if entry is None:
                return True, "robots_allow"
            path = urllib.parse.urlparse(url).path or "/"
            rule = self._most_specific_rule(entry, path)
            if rule is None:
                return True, "robots_allow"
            if rule.allowance:
                return True, "robots_allow"
            return False, "robots_disallow"
        except Exception as exc:  # noqa: BLE001
            logger.warning("[WEB-COMPLIANCE] Robots-Entscheidung fehlgeschlagen (%s): %s — fail-open", url, exc)
            return True, "fail_open"

    def _select_entry(self, parser: Any) -> Optional[Any]:
        """Entry-Auswahl wie CPython: zuerst Agent-spezifische Entries in
        Dateireihenfolge (``parser.entries``), dann die ``*``-Default-Entry.
        """
        for entry in parser.entries:
            if entry.applies_to(self._user_agent):
                return entry
        return parser.default_entry

    @staticmethod
    def _most_specific_rule(entry: Any, url_path: str) -> Optional[Any]:
        """RFC 9309 §3: die spezifischste (längste) passende Regel gewinnt;
        bei gleicher Länge die erste im Datei. CPythons
        ``Entry.allowance()`` nimmt stattdessen die erste passende Regel,
        was z. B. ``Disallow: /`` vor ``Allow: /public`` falsch blockt.
        """
        best = None
        for rule in entry.rulelines:
            if rule.path != "*" and not url_path.startswith(rule.path):
                continue
            if best is None or len(rule.path) > len(best.path):
                best = rule
        return best


_DEFAULT_CHECKER: Optional[RobotsChecker] = None
_CHECKER_LOCK = threading.Lock()


def default_robots_checker() -> RobotsChecker:
    """Lazy Singleton-Checker (Produktivpfad)."""
    global _DEFAULT_CHECKER
    with _CHECKER_LOCK:
        if _DEFAULT_CHECKER is None:
            _DEFAULT_CHECKER = RobotsChecker()
        return _DEFAULT_CHECKER


# ── Header / Meta-Prüfung ────────────────────────────────────────────────────
def _parse_directives(value: Optional[str]) -> set:
    if not value:
        return set()
    return {part.strip().lower() for part in re.split(r"[,\s]+", value.lower()) if part.strip()}


def check_response_headers(headers: Optional[Mapping[str, str]]) -> ComplianceDecision:
    """Prüft Response-Header auf Persistierungs-Opt-outs.

    Blockierend: ``X-Robots-Tag``/``Googlebot`` mit noindex/nofollow/... und
    ``Cache-Control``/``Pragma: no-store``. Alles andere (inkl. ``None``/leer)
    ist erlaubt.
    """
    if not headers:
        return ComplianceDecision(True)
    reasons: List[str] = []
    normalized = {str(k).lower(): str(v) for k, v in headers.items()}

    for header_name in ("x-robots-tag", "googlebot"):
        value = normalized.get(header_name)
        if not value:
            continue
        blocking = _parse_directives(value) & _BLOCKING_ROBOTS_DIRECTIVES
        if blocking:
            reasons.append(f"{header_name}: {', '.join(sorted(blocking))}")

    for header_name in ("cache-control", "pragma"):
        value = normalized.get(header_name)
        if value and "no-store" in _parse_directives(value):
            reasons.append(f"{header_name}: no-store")

    if reasons:
        return ComplianceDecision(False, tuple(reasons))
    return ComplianceDecision(True)


_META_ROBOTS_TAG_RE = re.compile(
    r"<meta\b[^>]*\bname\s*=\s*[\"']?(robots|googlebot)[\"']?[^>]*>",
    re.IGNORECASE,
)
_META_CONTENT_RE = re.compile(r"\bcontent\s*=\s*[\"']([^\"']*)[\"']", re.IGNORECASE)


def check_html_meta(html: Optional[str]) -> ComplianceDecision:
    """Prüft HTML auf ``<meta name="robots" ...>`` / ``name="googlebot"`` Opt-outs.

    Nur Standardbibliothek (Regex) — kein BeautifulSoup-Import im Compliance-Pfad.
    """
    if not html:
        return ComplianceDecision(True)
    reasons: List[str] = []
    for match in _META_ROBOTS_TAG_RE.finditer(html):
        tag = match.group(0)
        content_match = _META_CONTENT_RE.search(tag)
        if not content_match:
            continue
        blocking = _parse_directives(content_match.group(1)) & _BLOCKING_ROBOTS_DIRECTIVES
        if blocking:
            reasons.append(f"meta[{match.group(1).lower()}]: {', '.join(sorted(blocking))}")
    if reasons:
        return ComplianceDecision(False, tuple(reasons))
    return ComplianceDecision(True)


# ── Zusammengesetzte Entscheidung + Gate ─────────────────────────────────────
def decide(
    url: str,
    headers: Optional[Mapping[str, str]] = None,
    html: Optional[str] = None,
    robots_checker: Optional[RobotsChecker] = None,
) -> ComplianceDecision:
    """Komplette Compliance-Entscheidung für eine Persistierung.

    Reihenfolge: robots.txt → Response-Header → HTML-Meta. Alle blockierenden
    Gründe werden aggregiert. Bei ``WEB_COMPLIANCE_ENABLED=0`` wird die
    Schicht komplett übersprungen (bewusste Entscheidung, wird geloggt).
    """
    if not is_compliance_enabled():
        return ComplianceDecision(True, ("disabled:WEB_COMPLIANCE_ENABLED=0",))

    reasons: List[str] = []

    checker = robots_checker or default_robots_checker()
    allowed, reason = checker.is_allowed(url)
    if not allowed:
        reasons.append(f"robots.txt disallows path ({reason})")

    header_decision = check_response_headers(headers)
    if not header_decision.allowed:
        reasons.extend(header_decision.reasons)

    meta_decision = check_html_meta(html)
    if not meta_decision.allowed:
        reasons.extend(meta_decision.reasons)

    if reasons:
        return ComplianceDecision(False, tuple(reasons))
    return ComplianceDecision(True)


def gate_persistence(
    context: str,
    url: str,
    *,
    headers: Optional[Mapping[str, str]] = None,
    html: Optional[str] = None,
) -> bool:
    """Einzelpunkt-Gate für Persistierungs-Callsites.

    Returns ``True`` wenn die Persistierung erlaubt ist; bei Block wird ein
    WARNING mit Kontext + URL + Gründen geloggt und ``False`` zurückgegeben.
    """
    if not url:
        return True
    decision = decide(url, headers=headers, html=html)
    if decision.allowed:
        return True
    logger.warning(
        "[WEB-COMPLIANCE] BLOCKED persistence | context=%s | url=%s | %s",
        context, url, decision.detail(),
    )
    return False