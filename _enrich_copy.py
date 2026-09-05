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

def compute_risk(
    severity: str,
    kev: bool,
    epss: Optional[float],
) -> Tuple[str, float, List[str]]:
    """
    Berechnet die risikobasierte Priorisierung (P0-P3) einer Schwachstelle.

    Modell (SOTA 2026: CISA KEV + EPSS + Severity):
        P0  = aktiv ausgenutzt (CISA KEV)            -> SOFORT (0-24h)
        P1  = kritische Severity ODER hohe EPSS       -> 7 Tage
        P2  = hohe/mittlere Severity                  -> 30 Tage
        P3  = alles andere                            -> Quartalszyklus

    Args:
        severity: "critical" | "high" | "medium" | "low" | "unknown"
        kev:      True, wenn die CVE im CISA KEV-Katalog ist
        epss:     EPSS-Score 0..1 oder None (unverfuegbar)

    Returns:
        (tier, score, reasons) - tier in {"P0","P1","P2","P3"}, score (float,
        hoeher = dringlicher), reasons (menschenlesbare Begruendung).
    """
    sev = (severity or "unknown").lower()
    sev_score = {"critical": 4, "high": 3, "medium": 2, "low": 1}.get(sev, 1)
    score = float(sev_score)
    reasons = [f"severity={sev}"]

    if kev:
        score += 5.0
        reasons.append("CISA-KEV (aktiv ausgenutzt)")

    if epss is not None:
        clamped = max(0.0, min(1.0, float(epss)))
        score += clamped * 3.0
        reasons.append(f"EPSS={clamped:.3f}")

    if kev:
        tier = "P0"
    elif sev_score == 4 or (epss is not None and epss >= 0.3):
        tier = "P1"
    elif sev_score >= 2:
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
    """

    def __init__(
        self,
        cache_dir: Path,
        ttl_hours: int = DEFAULT_ENRICH_TTL_HOURS,
        offline: bool = False,
        refresh: bool = False,
    ):
        self.cache_dir = Path(cache_dir)
        self.cache_file = self.cache_dir / "kev" / "known_exploited_vulnerabilities.json"
        self.ttl = timedelta(hours=ttl_hours)
        self.offline = offline
        self.refresh = refresh
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
        Laedt KEV aus Cache oder (online) vom Feed.

        Logik (konsistent mit dem OSV-Cache des Scanners):
            1. Frischer Cache  -> nutzen, fertig.
            2. Cache fehlt/stale + ONLINE -> Fetch; Erfolg -> nutzen.
            3. Fetch fehlgeschlagen + staler Cache -> stalen Cache nutzen.
            4. OFFLINE ohne Cache -> nicht verfuegbar (False).

        Returns:
            True, wenn KEV-Daten verfuegbar sind (fresh, fetched oder stale),
            sonst False.
        """
        cached = self._read_cache()
        if cached is not None and self._fresh() and not self.refresh:
            self._apply(cached)
            return True

        if not self.offline:
            fetched = self._fetch()
            if fetched is not None:
                self._apply(fetched)
                return True

        if cached is not None:
            # Staler Cache als Fallback (online-Fetch fehlgeschlagen oder offline).
            self._apply(cached)
            return True

        return False

    # -- Intern -------------------------------------------------------------

    def _apply(self, data: Dict[str, Any]) -> None:
        self._by_cve = {
            entry["cveID"].strip().upper(): entry
            for entry in (data.get("vulnerabilities") or [])
            if isinstance(entry, dict) and entry.get("cveID")
        }

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
            return payload.get("data")
        except (json.JSONDecodeError, OSError, TypeError):
            return None

    def _write_cache(self, data: Dict[str, Any]) -> None:
        try:
            self.cache_file.parent.mkdir(parents=True, exist_ok=True)
            self.cache_file.write_text(
                json.dumps(
                    {"cached_at": datetime.now().isoformat(), "data": data},
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
        except OSError as e:
            logger.debug(f"KEV-Cache-Write fehlgeschlagen: {e}")

    def _fetch(self) -> Optional[Dict[str, Any]]:
        data = _http_get_json(KEV_URL, KEV_TIMEOUT_SECONDS)
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
        # -> {"CVE-2021-44228": {"epss": 0.99999, "percentile": 1.0, "date": "..."}}
    """

    def __init__(
        self,
        cache_dir: Path,
        ttl_hours: int = DEFAULT_ENRICH_TTL_HOURS,
        offline: bool = False,
        refresh: bool = False,
    ):
        self.cache_dir = Path(cache_dir)
        self.cache_file = self.cache_dir / "epss" / "epss_scores.json"
        self.ttl = timedelta(hours=ttl_hours)
        self.offline = offline
        self.refresh = refresh

    def get_scores(self, cve_ids: List[str]) -> Dict[str, Dict[str, Any]]:
        """
        Liefert EPSS-Scores fuer die angegebenen CVE-Ids (Cache + API).

        Nur CVE-Ids werden beruecksichtigt (GHSA-/PYSEC-Ids werden ignoriert).
        Fehlende CVEs fehlen im Ergebnis (kein Erfinden, kein False-Negative).
        """
        cve_ids = [c.strip().upper() for c in cve_ids if c and c.strip() and is_cve(c)]
        if not cve_ids:
            return {}

        cache = self._read_cache() or {}
        result: Dict[str, Dict[str, Any]] = {}
        missing: List[str] = []
        for cve in dict.fromkeys(cve_ids):  # dedupe, Reihenfolge erhalten
            if cve in cache:
                result[cve] = cache[cve]
            else:
                missing.append(cve)

        if missing and not self.offline:
            fetched = self._fetch(missing)
            if fetched:
                cache.update(fetched)
                result.update(fetched)
                self._write_cache(cache)

        return result

    # -- Intern -------------------------------------------------------------

    def _read_cache(self) -> Optional[Dict[str, Dict[str, Any]]]:
        if not self.cache_file.exists():
            return None
        try:
            payload = json.loads(self.cache_file.read_text(encoding="utf-8"))
            return payload.get("scores")
        except (json.JSONDecodeError, OSError, TypeError):
            return None

    def _write_cache(self, scores: Dict[str, Dict[str, Any]]) -> None:
        try:
            self.cache_file.parent.mkdir(parents=True, exist_ok=True)
            self.cache_file.write_text(
                json.dumps(
                    {"cached_at": datetime.now().isoformat(), "scores": scores},
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
        except OSError as e:
            logger.debug(f"EPSS-Cache-Write fehlgeschlagen: {e}")

    def _fetch(self, cve_ids: List[str]) -> Dict[str, Dict[str, Any]]:
        """
        Holt EPSS-Scores von der API (in Chunks von EPSS_CHUNK_SIZE).

        Returns:
            {CVE: {"epss": float, "percentile": float, "date": str}}.
        """
        out: Dict[str, Dict[str, Any]] = {}
        for i in range(0, len(cve_ids), EPSS_CHUNK_SIZE):
            chunk = cve_ids[i : i + EPSS_CHUNK_SIZE]
            url = f"{EPSS_URL}?cve={','.join(chunk)}"
            data = _http_get_json(url, EPSS_TIMEOUT_SECONDS)
            if isinstance(data, dict):
                for row in data.get("data") or []:
                    cve = row.get("cve")
                    if not cve:
                        continue
                    try:
                        epss = float(row.get("epss", 0.0))
                        percentile = float(row.get("percentile", 0.0))
                    except (TypeError, ValueError):
                        continue
                    out[cve.strip().upper()] = {
                        "epss": epss,
                        "percentile": percentile,
                        "date": row.get("date", ""),
                    }
            # Rate-Limit-Abschirmung zwischen Chunks (letzter Chunk: kein Sleep).
            if i + EPSS_CHUNK_SIZE < len(cve_ids):
                time.sleep(EPSS_CHUNK_DELAY_SECONDS)
        return out

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
    kev_ok = kev.load()
    cve_ids = [v.cve_id for v in vulns if getattr(v, "cve_id", None)]
    epss_scores = epss.get_scores(cve_ids) if cve_ids else {}

    tier_counts: Dict[str, int] = {"P0": 0, "P1": 0, "P2": 0, "P3": 0}
    enriched = 0

    for v in vulns:
        cve_id = getattr(v, "cve_id", None)
        if not cve_id:
            # Keine CVE (z.B. GHSA-only) -> keine KEV/EPSS, nur Severity-basiert.
            v_kev = False
            kev_entry = None
            epss_row = None
        else:
            v_kev = kev.is_kev(cve_id) if kev_ok else False
            kev_entry = kev.entry(cve_id) if kev_ok else None
            epss_row = epss_scores.get(cve_id)

        epss_val = epss_row["epss"] if epss_row else None
        tier, score, reasons = compute_risk(v.severity, v_kev, epss_val)

        if _can_set(v, "kev"):
            v.kev = v_kev
            v.kev_date_added = (kev_entry.get("dateAdded") if kev_entry else None)
            v.kev_ransomware = bool(
                kev_entry
                and kev_entry.get("knownRansomwareCampaignUse") == "Known"
            )
            v.epss = epss_val
            v.epss_percentile = (epss_row["percentile"] if epss_row else None)
            v.epss_date = (epss_row.get("date") if epss_row else None)
            v.risk_tier = tier
            v.risk_score = score
            v.risk_reasons = reasons
            enriched += 1

        tier_counts[tier] = tier_counts.get(tier, 0) + 1

    return {
        "tiers": tier_counts,
        "kev_available": bool(kev_ok),
        "epss_available": bool(epss_scores),
        "enriched": enriched,
        "total": len(vulns),
    }

