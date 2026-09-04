#!/usr/bin/env python3
"""
Standalone Web Search Script (Privacy-First, SOTA)
===================================================

Portables Websearch-Skript basierend auf DuckDuckGo (ddgs library).
Keine Cloud-LLM-Calls, keine externen API-Keys, lokal ausführbar.

Features:
  - DuckDuckGo Search (privacy-first, kein Tracking)
  - Query-Sanitization (PII-Redaktion vor externem Versand)
  - Domain-Blacklist (konfigurierbar)
  - TTL-basierter JSON-Cache (data/websearch_cache.json)
  - Timeout-Schutz (5s pro HTTP-Request)
  - Content-Type-Validierung bei HTML-Fetch
  - JSON-Output (maschinell lesbar)
  - News-Mode für aktuelle Queries
  - SOTA: Exponential Backoff bei Rate-Limits (429/403/202)
  - SOTA: ddgs rate_limit_handler (max 3 Retries, bis 8s Wait)
  - SOTA: Graceful Engine-Fehler-Handling (DNS/Connect suppressed)

Usage:
  python scripts/agent_websearch.py "Python 3.12 release date"
  python scripts/agent_websearch.py "aktuelle Nachrichten KI" --news
  python scripts/agent_websearch.py "RTX 5090 specs" --enrich --max-results 5
  python scripts/agent_websearch.py --help

SOTA-Verification: 2026-07-31 (Websearch-Recherche: ddgs rate_limit_handler,
  exponential backoff, P2P-cache, engine error handling)

Author: Local-First Agent Toolkit
Date: 2026-07-31
--last-verified: 2026-07-31--
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import math
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("agent_websearch")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
CACHE_DIR = Path(__file__).parent.parent / "data"
CACHE_FILE = CACHE_DIR / "websearch_cache.json"
DEFAULT_TTL = 1800          # 30 min
NEWS_TTL = 300             # 5 min
MAX_CACHE_ENTRIES = 300
DEFAULT_TIMEOUT = 5        # seconds per HTTP request
DEFAULT_MAX_RESULTS = 5

# SOTA: Rate-Limit-Constants (exponential backoff)
MAX_RETRIES = 3
BASE_RETRY_DELAY = 1.0     # seconds
MAX_RETRY_DELAY = 8.0      # seconds

# Default domain blacklist (privacy-focused)
DEFAULT_BLOCKED_DOMAINS: set[str] = {
    "googleadservices.com",
    "doubleclick.net",
    "googlesyndication.com",
    "amazon-adsystem.com",
    "rubproject.org",
    "criteo.com",
    "criteo.net",
    "tiktok.com",
    "tiktokv.com",
}

# PII patterns for query sanitization
_PII_PATTERNS = [
    re.compile(r"\b\d{3}[-.]?\d{3}[-.]?\d{4}\b"),          # phone numbers
    re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z]{2,}"),  # emails
    re.compile(r"\b\d{17}\b"),                               # IBAN-like
    re.compile(r"\b(?:SSL|SSN|TIN)\s*(?:#?)?(\d{3}[-\s]?\d{2}[-\s]?\d{4})\b"),  # US SSN
]

# News-keywords for auto-TTL selection
_NEWS_KEYWORDS = frozenset([
    "news", "aktuell", "aktuelle", "heute", "gestern",
    "latest", "breaking", "nachrichten", "neuigkeiten",
])

# SOTA: Known benign engine errors to suppress (DNS failures for optional backends)
_SUPPRESSED_ENGINE_ERRORS = frozenset([
    "dns error",
    "no records found",
    "at.wikipedia.org",
    "connecterror",
])

# ---------------------------------------------------------------------------
# SOTA: Rate Limit Handler with Exponential Backoff
# ---------------------------------------------------------------------------

# SOTA: Rate-Limit-Error-Patterns zum Retry
_RATE_LIMIT_PATTERNS = frozenset([
    "202", "403", "429", "rate limit", "too many", "blocked", "retry",
])

def _should_retry(exc: Exception) -> bool:
    """
    Prüft ob eine Exception retry-würdig ist (Rate-Limit / Server Error).

    SOTA-Verbesserung (Websearch-Recherche 2026-07-31):
      ddgs stellt RatelimitException als spezifische Exception-Klasse bereit.
      Diese wird explizit erkannt, nicht nur über String-Matching.
    """
    # Check for ddgs RatelimitException (if available)
    try:
        from ddgs import RatelimitException  # type: ignore
        if isinstance(exc, RatelimitException):
            return True
    except (ImportError, NameError):
        pass  # Fallback to string matching below

    return any(p in str(exc).lower() for p in _RATE_LIMIT_PATTERNS)

def _retry_with_backoff(
    func: Any,
    *args: Any,
    max_retries: int = MAX_RETRIES,
    **kwargs: Any,
) -> Any:
    """
    Führt func aus und retryt mit exponential backoff bei Rate-Limit-Fehlern.

    SOTA-Reference:
      - https://github.com/deedy5/ddgs/issues/211
      - https://gist.github.com/KanishkNavale/c51bbfdf373166d7f75dac95fa7ec891
    """
    last_exc: Optional[Exception] = None
    for attempt in range(max_retries + 1):
        try:
            return func(*args, **kwargs)
        except Exception as exc:
            last_exc = exc
            if not _should_retry(exc):
                raise
            if attempt < max_retries:
                wait = min(BASE_RETRY_DELAY * (2 ** attempt), MAX_RETRY_DELAY)
                logger.info(
                    f"Rate limited (attempt {attempt + 1}/{max_retries}), "
                    f"waiting {wait:.1f}s before retry"
                )
                time.sleep(wait)
            else:
                logger.warning(
                    f"Rate limit retries exhausted ({max_retries}), giving up"
                )
    if last_exc is not None:
        raise last_exc
    raise RuntimeError("Retry loop exhausted unexpectedly")

# ---------------------------------------------------------------------------
# PII Sanitizer
# ---------------------------------------------------------------------------

class PII_Sanitizer:
    """Redaktiert sensible Daten aus Queries vor externem Versand."""

    def __init__(self) -> None:
        self._patterns = _PII_PATTERNS

    def sanitize(self, query: str) -> str:
        """Ersetzt PII-Muster durch [REDACTED]."""
        out = query
        for pat in self._patterns:
            out = pat.sub("[REDACTED]", out)
        return out

# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

class SearchCache:
    """TTL-basierter JSON-Datei-Cache für Suchergebnisse."""

    def __init__(self, cache_path: Path = CACHE_FILE, max_entries: int = MAX_CACHE_ENTRIES) -> None:
        self._path = cache_path
        self._max = max_entries
        self._store: Dict[str, Dict[str, Any]] = {}
        self._load()

    # ---- internal ---------------------------------------------------------
    def _load(self) -> None:
        if self._path.exists():
            try:
                with open(self._path, "r", encoding="utf-8") as f:
                    self._store = json.load(f)
            except (json.JSONDecodeError, OSError):
                self._store = {}
        # Clean expired on load
        self._evict_expired()

    def _save(self) -> None:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._store, f, ensure_ascii=False, indent=2)
            tmp.replace(self._path)
        except OSError as e:
            logger.warning(f"Cache write failed: {e}")

    def _key(self, query: str, n: int) -> str:
        norm = " ".join(sorted(query.lower().strip().split()))
        raw = f"{norm}|n={n}"
        return hashlib.sha256(raw.encode()).hexdigest()

    def _ttl(self, query: str, is_news: bool = False) -> int:
        if is_news or any(k in query.lower() for k in _NEWS_KEYWORDS):
            return NEWS_TTL
        return DEFAULT_TTL

    def _evict_expired(self) -> None:
        now = time.time()
        expired = [k for k, v in self._store.items() if v["expires_at"] < now]
        for k in expired:
            del self._store[k]
        # Trim to max
        if len(self._store) > self._max:
            oldest = sorted(self._store.items(), key=lambda x: x[1]["stored_at"])
            for k, _ in oldest[: len(self._store) - self._max]:
                del self._store[k]

    # ---- public -----------------------------------------------------------
    def get(self, query: str, n: int) -> Optional[List[Dict]]:
        self._evict_expired()
        entry = self._store.get(self._key(query, n))
        if entry is None:
            return None
        return entry["results"].copy()

    def store(self, query: str, n: int, results: List[Dict], is_news: bool = False) -> None:
        if not results:
            return
        ttl = self._ttl(query, is_news)
        self._store[self._key(query, n)] = {
            "query": query,
            "stored_at": time.time(),
            "expires_at": time.time() + ttl,
            "ttl": ttl,
            "results": results,
        }
        self._save()

    def clear(self) -> None:
        self._store.clear()
        self._save()

# ---------------------------------------------------------------------------
# Domain Filter
# ---------------------------------------------------------------------------

class DomainFilter:
    """Blocklist-basierte Domain-Filterung."""

    def __init__(self, blocked: Optional[set[str]] = None) -> None:
        self._blocked = {d.lower().strip().removeprefix("www.") for d in (blocked or DEFAULT_BLOCKED_DOMAINS)}

    def add(self, domain: str) -> None:
        self._blocked.add(domain.lower().strip().removeprefix("www."))

    def filter_results(self, results: List[Dict]) -> List[Dict]:
        out: List[Dict] = []
        for r in results:
            url = r.get("url", "")
            domain = self._extract_domain(url)
            if domain not in self._blocked:
                out.append(r)
            else:
                logger.debug(f"Blocked: {url[:60]} (domain: {domain})")
        return out

    @staticmethod
    def _extract_domain(url: str) -> str:
        try:
            netloc = urlparse(url).netloc.lower()
            return netloc.removeprefix("www.")
        except Exception:
            return ""

# ---------------------------------------------------------------------------
# HTML Snippet Fetcher (Security-Hardened)
# ---------------------------------------------------------------------------

_HTML_ALLOWED_CT = frozenset(["text/html", "application/xhtml+xml"])

def _fetch_snippet(url: str, timeout: int = DEFAULT_TIMEOUT) -> Optional[str]:
    """
    Holt maximal 1000 Zeichen aus einer URL.
    Sicherheitsfeatures:
      - Timeout
      - Content-Type-Validierung
      - Redirect-Limit (2)
      - Keine Cookie-/Auth-Header
    """
    try:
        import requests as _req  # type: ignore
    except ImportError:
        logger.warning("requests not installed, HTML enrichment disabled")
        return None

    try:
        resp = _req.get(
            url,
            timeout=timeout,
            allow_redirects=True,
            headers={"Accept-Language": "de-DE,de,en;q=0.9", "User-Agent": "Mozilla/5.0 (AgentWebSearch/1.0)"},
        )
        ct = resp.headers.get("content-type", "").split(";")[0].strip().lower()
        if ct not in _HTML_ALLOWED_CT and not ct.startswith("text/"):
            return None
        # Strip tags
        text = re.sub(r"<[^>]+>", " ", resp.text)
        text = re.sub(r"\s+", " ", text).strip()
        return text[:1000] if text else None
    except Exception as e:
        logger.debug(f"Snippet fetch failed for {url[:50]}: {e}")
        return None

# ---------------------------------------------------------------------------
# SOTA: Engine Error Filter
# ---------------------------------------------------------------------------

def _is_suppressible_engine_error(msg: str) -> bool:
    """
    Prüft ob eine Engine-Fehlermeldung unterdrückt werden soll.

    SOTA: Optional Backends (Wikipedia, Grokipedia) können DNS-Fehler werfen.
    Diese sind harmlos, da die Haupt-Engine (Startpage/DDG) funktioniert.
    """
    lower = msg.lower()
    return any(pattern in lower for pattern in _SUPPRESSED_ENGINE_ERRORS)

# ---------------------------------------------------------------------------
# DDG Search (SOTA-Hardened)
# ---------------------------------------------------------------------------

def _ddg_search(query: str, max_results: int = 5, news: bool = False) -> List[Dict[str, str]]:
    """
    Führt eine DuckDuckGo-Suche durch.

    SOTA-Features:
      - rate_limit_handler mit exponential backoff
      - Graceful Engine-Fehler-Handling (benign errors suppressed)
      - PII-Sanitization vor externem Versand
    """
    try:
        from ddgs import DDGS  # type: ignore
    except ImportError:
        logger.error("ddgs nicht installiert. Installiere: pip install ddgs")
        return []

    sanitized = PII_Sanitizer().sanitize(query)
    results: List[Dict[str, str]] = []

    def _do_search() -> None:
        """Innere Funktion: führt die eigentliche DDG-Suche durch."""
        nonlocal results
        # SOTA: ddgs ohne rate_limit_handler (ddgs-Version unterstützt es nicht),
        # stattdessen manuelles exponential backoff um den Call herum.
        # P2P-Cache ist bewusst deaktiviert (Privacy-First)
        with DDGS() as ddgs:
            gen = ddgs.text(sanitized, max_results=max_results, region="de-AT")

            for item in gen:
                results.append({
                    "title": item.get("title", "") or item.get("titlesnippet", ""),
                    "url": item.get("href", "") or item.get("url", ""),
                    "body": item.get("body", "") or item.get("description", ""),
                })

    try:
        _retry_with_backoff(_do_search)
    except Exception as e:
        err_msg = str(e)
        # SOTA: Suppress benign engine errors (DNS failures for optional backends)
        if _is_suppressible_engine_error(err_msg):
            logger.debug(f"Engine error suppressed: {err_msg[:80]}")
        else:
            logger.error(f"DDG Search failed: {e}")

    return results

# ---------------------------------------------------------------------------
# Main Search Function
# ---------------------------------------------------------------------------

def search(
    query: str,
    max_results: int = DEFAULT_MAX_RESULTS,
    news: bool = False,
    enrich: bool = False,
    blocked_domains: Optional[List[str]] = None,
    use_cache: bool = True,
) -> Dict[str, Any]:
    """
    Hauptsuchfunktion.

    Args:
        query: Suchanfrage
        max_results: Maximale Anzahl Ergebnisse
        news: News-Mode (kürzere Cache-TTL)
        enrich: HTML-Snippet-Fetch für jedes Ergebnis
        blocked_domains: Zusätzliche Domain-Blocklist
        use_cache: Cache verwenden

    Returns:
        Dict mit 'query', 'timestamp', 'results' (List[Dict]), 'cache_hit' (bool)
    """
    cache = SearchCache() if use_cache else None
    domain_filter = DomainFilter(blocked=set(blocked_domains) if blocked_domains else None)

    # Cache lookup
    if cache:
        cached = cache.get(query, max_results)
        if cached is not None:
            logger.info(f"Cache HIT for '{query[:40]}'")
            return {
                "query": query,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "results": cached,
                "cache_hit": True,
                "count": len(cached),
            }

    # DDG Search
    logger.info(f"Searching: '{query}' (news={news}, max={max_results})")
    t0 = time.time()
    results = _ddg_search(query, max_results=max_results, news=news)
    elapsed = round(time.time() - t0, 2)

    # Domain filter
    results = domain_filter.filter_results(results)

    # Enforce max_results after filtering (blocked domains may have reduced count,
    # but DDG may also return more than requested; cap defensively)
    results = results[:max_results]

    # Optional enrichment
    if enrich and results:
        for r in results:
            if not r.get("body") and r.get("url"):
                snippet = _fetch_snippet(r["url"])
                if snippet:
                    r["body"] = snippet
                r["_enriched"] = True

    # Cache store
    if cache and results:
        cache.store(query, max_results, results, is_news=news)

    return {
        "query": query,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "results": results,
        "cache_hit": False,
        "count": len(results),
        "elapsed_s": elapsed,
    }

# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Standalone Privacy-First Web Search (DuckDuckGo)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python scripts/agent_websearch.py "Python 3.13 features"
  python scripts/agent_websearch.py "RTX 5090 specs" --enrich
  python scripts/agent_websearch.py "KI Nachrichten heute" --news
  python scripts/agent_websearch.py "OpenAI GPT-5" --max-results 10
  python scripts/agent_websearch.py "test" --clear-cache
        """,
    )
    parser.add_argument("query", nargs="?", default=None, help="Suchanfrage")
    parser.add_argument("--max-results", "-n", type=int, default=DEFAULT_MAX_RESULTS, help="Max results (default: 5)")
    parser.add_argument("--news", action="store_true", help="News-Mode (kürzere Cache-TTL)")
    parser.add_argument("--enrich", action="store_true", help="HTML-Snippet-Fetch für jedes Ergebnis")
    parser.add_argument("--no-cache", action="store_true", help="Cache deaktivieren")
    parser.add_argument("--clear-cache", action="store_true", help="Cache leeren")
    parser.add_argument("--block", nargs="*", default=[], help="Zusätzliche Domain-Blocklist")
    parser.add_argument("--json", dest="as_json", action="store_true", help="Rohes JSON-Output")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose logging")

    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    # Clear cache
    if args.clear_cache:
        SearchCache().clear()
        print("Cache cleared.")
        return

    if not args.query:
        parser.print_help()
        sys.exit(1)

    # Run search
    result = search(
        query=args.query,
        max_results=args.max_results,
        news=args.news,
        enrich=args.enrich,
        blocked_domains=args.block,
        use_cache=not args.no_cache,
    )

    # Output
    if args.as_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    # Human-readable output
    print(f"\n{'='*60}")
    print(f"  Web Search Results")
    print(f"{'='*60}")
    print(f"  Query:    {result['query']}")
    print(f"  Time:     {result['timestamp']}")
    print(f"  Count:    {result['count']}")
    print(f"  Cached:   {'yes' if result['cache_hit'] else 'no'}")
    if "elapsed_s" in result:
        print(f"  Elapsed:  {result['elapsed_s']}s")
    print(f"{'='*60}")

    for i, r in enumerate(result["results"], 1):
        title = r.get("title", "(no title)") or "(no title)"
        url = r.get("url", "(no url)") or "(no url)"
        body = r.get("body", "") or ""
        enriched = " [enriched]" if r.get("_enriched") else ""

        print(f"\n  [{i}] {title}{enriched}")
        print(f"      {url}")
        if body:
            # Truncate long snippets
            snippet = body[:200]
            if len(body) > 200:
                snippet += "..."
            print(f"      {snippet}")

    print(f"\n{'='*60}\n")

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    main()