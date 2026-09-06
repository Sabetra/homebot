"""
Vulnerability Enrichment & Risk Prioritization (SOTA 2026)
============================================================

Erweitert die vom Scanner gefundenen Schwachstellen um zwei zentrale
Risiko-Signale und berechnet daraus eine risikobasierte Priorisierung (P0-P3):

  1. CISA KEV  - Known Exploited Vulnerabilities
                 (CVEs, die aktiv in der Wildnis ausgenutzt wurden)
                 Quelle: CISA KEV-JSON-Feed (offiziell, lokal gecacht, TTL 24h)
  2. EPSS      - Exploit Prediction Scoring System (FIRST.org)
                 P(Ausnutzung innerhalb der naechsten 30 Tage), 0..1, taeglich
                 Quelle: api.first.org/data/v1/epss (Batch, lokal gecacht)

Warum SOTA (Recherche 2026-09-05):
    2026 ist der Standard im Vulnerability Management RISIKO-BASIERTE
    PRIORISIERUNG, nicht bloes "alle CVEs auflisten". Die "Prioritization
    Trinity" der Fachquellen (safeguard.sh, devsecopsatlas.com,
    augmentcode.com, secrails.com) ist: CISA KEV + EPSS (+ Reachability).
    KEV = "wird bereits ausgenutzt" (staerkstes Signal), EPSS = "Wahrschein-
    lichkeit von Ausnutzung". Beides schraeft eine lange CVE-Liste auf die
    wenigen ein, die SOFORTHANDLUNG brauchen.

Local-First / Privacy:
    - KEV-JSON wird einmalig geladen (~2 MB) und lokal gecacht (TTL 24h).
    - EPSS bekommt NUR die gefundenen CVE-IDs im Batch (oeffentliche
      Identifikatoren; KEINE PII, KEIN Code, KEINE Paketnamen, keine Telemetrie).
    - --offline: KEIN Netzwerk; nur Cache. Ohne Cache bleiben die Felder null
      (kein False-Negative, keine Erfindung).
    - Alle Calls sind best-effort: ein Enrichment-Fehler faellt den Scan NIE.

Nutzung:
    from vuln_enrich import KEVCatalog, EPSSClient, prioritize, compute_risk
    kev  = KEVCatalog(cache_dir, offline=off)
    epss = EPSSClient(cache_dir, offline=off)
    stats = prioritize(vuln_list, kev, epss)   # setzt kev/epss/risk_* auf vulns

Referenzen:
    - FIRST.org EPSS:  https://www.first.org/epss/  (API: api.first.org/data/v1/epss)
    - CISA KEV:        https://www.cisa.gov/known-exploited-vulnerabilities-catalog
    - SOTA 2026:       KEV + EPSS + Reachability als Priorisierungsstandard
"""

import json
import logging
import re
import time
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ============================================================================
# CONSTANTS (SOTA-Quellen, Recherche 2026-09-05)
# ============================================================================

KEV_URL = (
    "https://www.cisa.gov/sites/default/files/feeds/"
    "known_exploited_vulnerabilities.json"
)
EPSS_URL = "https://api.first.org/data/v1/epss"
KEV_TIMEOUT_SECONDS = 25
EPSS_TIMEOUT_SECONDS = 20
EPSS_CHUNK_SIZE = 100          # EPSS-API: max. ~100 CVEs pro Request
EPSS_CHUNK_DELAY_SECONDS = 0.2  # Rate-Limit-Abschirmung zwischen Chunks
DEFAULT_ENRICH_TTL_HOURS = 24

# Identifizierbarer, lokaler User-Agent (keine Telemetrie, nur Kennzeichnung).
USER_AGENT = "homebot-vuln-scanner/1.0 (local-first, privacy-preserving)"

# CVE-Muster (CVE-JJJJ-NNNN+). Nicht alle OSV-Ids sind CVEs (GHSA-, PYSEC-...);
# KEV/EPSS funktionieren nur mit CVEs, daher noetig.
CVE_RE = re.compile(r"^CVE-\d{4}-\d{4,}$", re.IGNORECASE)


# ============================================================================
# PURE HELPERS (leicht testbar)
# ============================================================================

def is_cve(vuln_id: Optional[str]) -> bool:
    """True, wenn vuln_id ein gueltiges CVE-Id ist (case-insensitiv)."""
    return bool(vuln_id and CVE_RE.match(vuln_id.strip()))


def extract_cve(
    vuln_id: Optional[str], aliases: Optional[List[str]] = None
) -> Optional[str]:
    """
    Loest die kanonische CVE-Id aus id + OSV-Aliassen.

    OSV liefert bei PyPI-Paketen oft GHSA-/PYSEC-Ids; die dazugehoerige CVE
    steckt dann in `aliases`. KEV/EPSS brauchen die CVE.

    Returns:
        CVE-Id (grossgeschrieben) oder None, wenn keine gefunden.
    """
    candidates: List[str] = []
    if vuln_id and is_cve(vuln_id):
        candidates.append(vuln_id.strip().upper())
    for alias in aliases or []:
        if isinstance(alias, str) and is_cve(alias):
            candidates.append(alias.strip().upper())
    return candidates[0] if candidates else None

def _kev_ransomware(kev_entry: Optional[Dict[str, Any]]) -> bool:
    """True, wenn der KEV-Eintrag bekannte Ransomware-Nutzung flaggt."""
    if not kev_entry:
        return False
    return (
        kev_entry.get("ransomwareCampaign") is True
        or kev_entry.get("knownRansomwareCampaignUse") == "Known"
    )


def compute_risk(
    severity: str,
    kev_entry: Optional[Dict[str, Any]],
    epss: Optional[float],
) -> Tuple[str, float, List[str]]:
    """
    Berechnet die risikobasierte Priorisierung (P0-P3) einer Schwachstelle.

    Modell (SOTA 2026: CISA KEV + EPSS + Severity):
        P0  = aktiv ausgenutzt (CISA KEV)                    -> SOFORT (0-24h)
        P1  = kritische Severity ODER high + EPSS >= 0.3     -> 7 Tage
        P2  = hohe/mittlere Severity                         -> 30 Tage
        P3  = alles andere                                   -> Quartalszyklus

    Score-Modell (hoeher = dringlicher):
        critical=10, high=5, medium=2, low=1, unknown=0
        + 5.0  bei CISA-KEV
        + EPSS * 2.0  (Score 0..1)

    Args:
        severity:  "critical" | "high" | "medium" | "low" | "unknown"
        kev_entry: KEV-Eintrag (dict) oder None, wenn nicht im Katalog
        epss:      EPSS-Score 0..1 oder None (unverfuegbar)

    Returns:
        (tier, score, reasons) - tier in {"P0","P1","P2","P3"}, score (float,
        hoeher = dringlicher), reasons (menschenlesbare Begruendung).
    """
    sev = (severity or "unknown").strip().lower()
    sev_base = {"critical": 10.0, "high": 5.0, "medium": 2.0, "low": 1.0}.get(sev, 0.0)
    score = sev_base
    reasons: List[str] = []
    if sev in ("critical", "high", "medium", "low"):
        reasons.append(sev)

    kev = kev_entry is not None
    if kev:
        score += 5.0
        reasons.append("CISA KEV (aktiv ausgenutzt)")
        if _kev_ransomware(kev_entry):
            reasons.append("bekannte Ransomware-Kampagne")

    epss_clamped: Optional[float] = None
    if epss is not None:
        epss_clamped = max(0.0, min(1.0, float(epss)))
        score += epss_clamped * 2.0
        reasons.append(f"EPSS={epss_clamped:.3f}")

    if kev:
        tier = "P0"
    elif sev == "critical":
        tier = "P1"
    elif sev == "high" and epss_clamped is not None and epss_clamped >= 0.3:
        tier = "P1"
    elif sev in ("high", "medium"):
        tier = "P2"
    else:
        tier = "P3"

    return tier, round(score, 3), reasons


# ============================================================================
# HTTP HELPER (robust, timeout, kein Crash)
# ============================================================================

def _http_get_json(url: str, timeout: int) -> Optional[Any]:
    """GET url -> JSON-Objekt, oder None bei jedem Fehler (kein Crash)."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
            return json.loads(body)
    except Exception as e:  # noqa: BLE001 - Netzwerkfehler abfangen, kein Crash
        logger.debug(f"HTTP-GET fehlgeschlagen ({url}): {e}")
        return None

# ============================================================================
# CISA KEV CATALOG
# ============================================================================

class KEVCatalog:
    """
    CISA Known Exploited Vulnerabilities - lokale Referenz.

    Laedt den KEV-Feed (Cache + Fetch) und liefert Lookup-Funktionen:
        kev.is_kev("CVE-XXXX-YYYY") -> bool
        kev.entry("CVE-XXXX-YYYY")  -> dict | None
        kev.available               -> bool (Daten geladen?)

    Cache-Format (kev/kev_cache.json):
        {"cached_at": ISO-Timestamp, "kev": {CVE-Id: Eintrag-Dict}}
    """

    def __init__(
        self,
        cache_dir: Path,
        ttl_hours: int = DEFAULT_ENRICH_TTL_HOURS,
        offline: bool = False,
        refresh: bool = False,
    ):
        self.cache_dir = Path(cache_dir)
        self.cache_file = self.cache_dir / "kev" / "kev_cache.json"
        self.ttl = timedelta(hours=ttl_hours)
        self.offline = offline
        self.refresh = refresh
        self.available = False
        self._by_cve: Dict[str, Dict[str, Any]] = {}

    # -- Lookup -------------------------------------------------------------

    def is_kev(self, vuln_id: Optional[str]) -> bool:
        """True, wenn die (CVE-)Id im KEV-Katalog ist."""
        if not vuln_id:
            return False
        return vuln_id.strip().upper() in self._by_cve

    def entry(self, vuln_id: Optional[str]) -> Optional[Dict[str, Any]]:
        """Gibt den KEV-Eintrag (dict) zurueck oder None."""
        if not vuln_id:
            return None
        return self._by_cve.get(vuln_id.strip().upper())

    # -- Load ---------------------------------------------------------------

    def load(self) -> bool:
        """
        Laedt KEV aus Cache oder (online) vom Feed (idempotent).

        Logik (konsistent mit dem OSV-Cache des Scanners):
            1. Frischer Cache  -> nutzen, fertig (kein Netz).
            2. Cache fehlt/stale + ONLINE -> Fetch; Erfolg -> nutzen.
            3. Fetch fehlgeschlagen + staler Cache -> stalen Cache nutzen.
            4. OFFLINE oder Fetch-Fehler ohne Cache -> nicht verfuegbar.

        Returns:
            True, wenn KEV-Daten verfuegbar sind (fresh, fetched oder stale),
            sonst False. Setzt gleichzeitig self.available.
        """
        if self._by_cve:
            # Bereits geladen (frueherer load()-Aufruf) - keine Wiederholung.
            self.available = True
            return True

        cached = self._read_cache()
        if cached is not None and self._fresh() and not self.refresh:
            self._apply(cached)
            self.available = True
            return True

        if not self.offline:
            fetched = self._fetch()
            if fetched is not None:
                self._apply(fetched)
                self.available = True
                return True

        if cached is not None:
            # Staler Cache als Fallback (online-Fetch fehlgeschlagen oder offline).
            self._apply(cached)
            self.available = True
            return True

        self.available = False
        return False

    # -- Intern -------------------------------------------------------------

    def _apply(self, data: Dict[str, Any]) -> None:
        """
        Indexiert KEV-Eintraege nach CVE-Id (Groesse-agnostic, dedupliziert).

        Unterstuetzt beide Formate:
          - Feed : {"vulnerabilities": [Eintrag, ...]}
          - Cache: {CVE-Id: Eintrag}
        """
        pairs: List[Tuple[str, Dict[str, Any]]] = []
        if isinstance(data, dict):
            vulns = data.get("vulnerabilities")
            if isinstance(vulns, list):
                for entry in vulns:
                    if isinstance(entry, dict) and entry.get("cveID"):
                        pairs.append((str(entry["cveID"]), entry))
            else:
                for key, entry in data.items():
                    if isinstance(entry, dict):
                        pairs.append((str(key), entry))
        self._by_cve = {k.strip().upper(): e for k, e in pairs if k}

    def _fresh(self) -> bool:
        if not self.cache_file.exists():
            return False
        try:
            payload = json.loads(self.cache_file.read_text(encoding="utf-8"))
            cached_at = datetime.fromisoformat(payload.get("cached_at", ""))
            return (datetime.now() - cached_at) < self.ttl
        except (json.JSONDecodeError, ValueError, TypeError):
            return False

    def _read_cache(self) -> Optional[Dict[str, Any]]:
        if not self.cache_file.exists():
            return None
        try:
            payload = json.loads(self.cache_file.read_text(encoding="utf-8"))
            kev = payload.get("kev")
            return kev if isinstance(kev, dict) else None
        except (json.JSONDecodeError, OSError, TypeError):
            return None

    def _write_cache(self, data: Dict[str, Any]) -> None:
        """Schreibt den KEV-Katalog als {CVE-Id: Eintrag}-Cache."""
        by_cve: Dict[str, Dict[str, Any]] = {}
        if isinstance(data, dict):
            vulns = data.get("vulnerabilities")
            if isinstance(vulns, list):
                for entry in vulns:
                    if isinstance(entry, dict) and entry.get("cveID"):
                        by_cve[str(entry["cveID"]).strip().upper()] = entry
            else:
                for key, entry in data.items():
                    if isinstance(entry, dict):
                        by_cve[str(key).strip().upper()] = entry
        try:
            self.cache_file.parent.mkdir(parents=True, exist_ok=True)
            self.cache_file.write_text(
                json.dumps(
                    {"cached_at": datetime.now().isoformat(), "kev": by_cve},
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
        except OSError as e:
            logger.debug(f"KEV-Cache-Write fehlgeschlagen: {e}")

    def _fetch(self) -> Optional[Dict[str, Any]]:
        """Holt den KEV-Feed (rohes Feed-Format) oder None bei jedem Fehler."""
        try:
            data = _http_get_json(KEV_URL, KEV_TIMEOUT_SECONDS)
        except Exception as e:  # noqa: BLE001 - Netzwerkfehler abfangen, kein Crash
            logger.debug(f"KEV-Fetch fehlgeschlagen: {e}")
            return None
        if isinstance(data, dict) and data.get("vulnerabilities"):
            self._write_cache(data)
            return data
        return None

# ============================================================================
# EPSS CLIENT
# ============================================================================

class EPSSClient:
    """
    EPSS-Scores (FIRST.org) - Batch-Query + lokaler per-CVE-Cache.

        epss = EPSSClient(cache_dir, offline=off)
        scores = epss.get_scores(["CVE-2021-44228", ...])
        # -> {"CVE-2021-44228": 0.99999, ...}  (floats, nur gefundene CVEs)

    Cache-Format (epss/epss_cache.json):
        {"cached_at": ISO-Timestamp, "scores": {CVE-Id: float}, "date": str}

    Attribute:
        available  - True, wenn EPSS-Daten verfuegbar sind (Cache oder Fetch)
        last_date  - Datenstand der EPSS-Scores (str) oder None
    """

    def __init__(
        self,
        cache_dir: Path,
        ttl_hours: int = DEFAULT_ENRICH_TTL_HOURS,
        offline: bool = False,
        refresh: bool = False,
    ):
        self.cache_dir = Path(cache_dir)
        self.cache_file = self.cache_dir / "epss" / "epss_cache.json"
        self.ttl = timedelta(hours=ttl_hours)
        self.offline = offline
        self.refresh = refresh
        self.available = False
        self.last_date: Optional[str] = None

    def get_scores(self, cve_ids: List[str]) -> Dict[str, float]:
        """
        Liefert EPSS-Scores (floats) fuer die angegebenen CVE-Ids.

        Nur CVE-Ids werden beruecksichtigt (GHSA-/PYSEC-Ids werden ignoriert).
        Fehlende CVEs fehlen im Ergebnis (kein Erfinden, kein False-Negative).

        Ein frischer Cache wird ohne Netzwerk genutzt; nur bei stale/fehlendem
        Cache und ONLINE werden die fehlenden CVEs nachgeholt (in Chunks).
        """
        cves = [
            c.strip().upper()
            for c in cve_ids
            if c and c.strip() and is_cve(c)
        ]
        cves = list(dict.fromkeys(cves))  # dedupe, Reihenfolge erhalten
        if not cves:
            return {}

        cache, cache_date = self._read_cache()
        cache = cache or {}
        if cache_date:
            self.last_date = cache_date

        result: Dict[str, float] = {}
        for cve in cves:
            if cve in cache:
                try:
                    result[cve] = float(cache[cve])
                except (TypeError, ValueError):
                    continue

        if self._fresh() and not self.refresh:
            # Frischer Cache: fertig (kein Netzwerk, keine staler Daten).
            self.available = bool(result)
            return result

        missing = [c for c in cves if c not in result]
        if missing and not self.offline:
            fetched, fetch_date = self._fetch(missing)
            if fetch_date:
                self.last_date = fetch_date
            for cve, value in fetched.items():
                if cve not in result:
                    result[cve] = value
            if fetched:
                merged = dict(cache)
                merged.update(fetched)
                self._write_cache(merged, fetch_date or cache_date)

        self.available = bool(result)
        return result

    # -- Intern -------------------------------------------------------------

    def _read_cache(self) -> Tuple[Dict[str, Any], Optional[str]]:
        if not self.cache_file.exists():
            return {}, None
        try:
            payload = json.loads(self.cache_file.read_text(encoding="utf-8"))
            scores = payload.get("scores")
            scores = scores if isinstance(scores, dict) else {}
            date = payload.get("date")
            return scores, (date if isinstance(date, str) else None)
        except (json.JSONDecodeError, OSError, TypeError):
            return {}, None

    def _write_cache(self, scores: Dict[str, float], date: Optional[str]) -> None:
        try:
            self.cache_file.parent.mkdir(parents=True, exist_ok=True)
            self.cache_file.write_text(
                json.dumps(
                    {
                        "cached_at": datetime.now().isoformat(),
                        "scores": scores,
                        "date": date,
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
        except OSError as e:
            logger.debug(f"EPSS-Cache-Write fehlgeschlagen: {e}")

    def _fetch(
        self, cve_ids: List[str]
    ) -> Tuple[Dict[str, float], Optional[str]]:
        """
        Holt EPSS-Scores von der API (in Chunks von EPSS_CHUNK_SIZE).

        Returns:
            ({CVE: float}, Datenstand-Date) - leeres Dict bei Fehlern.
        """
        out: Dict[str, float] = {}
        latest_date: Optional[str] = None
        for i in range(0, len(cve_ids), EPSS_CHUNK_SIZE):
            chunk = cve_ids[i : i + EPSS_CHUNK_SIZE]
            url = f"{EPSS_URL}?cve={','.join(chunk)}"
            try:
                data = _http_get_json(url, EPSS_TIMEOUT_SECONDS)
            except Exception as e:  # noqa: BLE001 - Netzwerkfehler abfangen, kein Crash
                logger.debug(f"EPSS-Fetch fehlgeschlagen: {e}")
                break
            if isinstance(data, dict):
                for row in data.get("data") or []:
                    cve = row.get("cve")
                    if not cve:
                        continue
                    try:
                        out[cve.strip().upper()] = float(row.get("epss"))
                    except (TypeError, ValueError):
                        continue
                    date = row.get("date")
                    if isinstance(date, str) and date > (latest_date or ""):
                        latest_date = date
            # Rate-Limit-Abschirmung zwischen Chunks (letzter Chunk: kein Sleep).
            if i + EPSS_CHUNK_SIZE < len(cve_ids):
                time.sleep(EPSS_CHUNK_DELAY_SECONDS)
        return out, latest_date

# ============================================================================
# ORCHESTRATOR
# ============================================================================

def _can_set(obj: Any, attr: str) -> bool:
    """True, wenn das Objekt das Attribut schreiben kann (kein __slots__-Fehler)."""
    slots = getattr(type(obj), "__slots__", None)
    if slots is None:
        return True
    return attr in slots


def prioritize(
    vulns: List[Any],
    kev: KEVCatalog,
    epss: EPSSClient,
) -> Dict[str, Any]:
    """
    Erreichbarkeitsunaehgige Risiko-Priorisierung einer Vuln-Liste.

    Setzt auf jedem Vulnerability-Objekt die Felder:
        kev, kev_date_added, kev_ransomware,
        epss, epss_percentile, epss_date,
        risk_tier, risk_score, risk_reasons
    (nur wenn das Objekt die Attribute akzeptiert; sonst wird es uebersprungen.)

    Returns:
        Statistik-Dict:
            {"tiers": {"P0": n, "P1": n, "P2": n, "P3": n},
             "kev_available": bool,
             "epss_available": bool,
             "enriched": int, "total": int}
    """
    kev.load()
    cve_ids = [v.cve_id for v in vulns if getattr(v, "cve_id", None)]
    epss_scores = epss.get_scores(cve_ids) if cve_ids else {}

    tier_counts: Dict[str, int] = {"P0": 0, "P1": 0, "P2": 0, "P3": 0}
    enriched = 0

    for v in vulns:
        cve_id = getattr(v, "cve_id", None)
        kev_entry = kev.entry(cve_id) if (kev.available and cve_id) else None
        epss_val = epss_scores.get(cve_id) if cve_id else None

        tier, score, reasons = compute_risk(v.severity, kev_entry, epss_val)

        if _can_set(v, "kev"):
            v.kev = kev_entry is not None
            v.kev_date_added = (kev_entry.get("dateAdded") if kev_entry else None)
            v.kev_ransomware = _kev_ransomware(kev_entry)
            v.epss = epss_val
            v.epss_percentile = None  # Cache speichert nur den Score (float)
            v.epss_date = epss.last_date if epss_val is not None else None
            v.risk_tier = tier
            v.risk_score = score
            v.risk_reasons = reasons

        if kev_entry is not None or epss_val is not None:
            enriched += 1
        tier_counts[tier] = tier_counts.get(tier, 0) + 1

    return {
        "tiers": tier_counts,
        "kev_available": bool(kev.available),
        "epss_available": bool(epss.available),
        "enriched": enriched,
        "total": len(vulns),
    }

