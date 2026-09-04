from __future__ import annotations
from dataclasses import dataclass
from typing import Optional, Tuple
from urllib.parse import urlparse, urlunparse
import re
import sqlite3
import time

# Lightweight, persistent negative cache / circuit-breaker for web fetching.
# - Tracks failures per-URL and per-domain (eTLD+1 approximation)
# - Blacklists after N consecutive failures with exponential backoff, max 30 days
# - Resets on success
# - Designed to be called before attempting to fetch a URL
#
# NOTE: Domain extraction uses a simple heuristic (last two labels). For better
# accuracy with public suffixes like co.uk, integrate tldextract if desired.

SECONDS = 1.0
MINUTE = 60 * SECONDS
HOUR = 60 * MINUTE
DAY = 24 * HOUR

BACKOFF_SCHEDULE = [1 * HOUR, 6 * HOUR, 24 * HOUR, 3 * DAY, 7 * DAY, 14 * DAY, 30 * DAY]
MAX_BACKOFF = 30 * DAY


@dataclass
class FetchDecision:
    allow: bool
    reason: str = ""
    retry_at_unix: Optional[int] = None


class WebFetchPolicy:
    def __init__(self, db_path: Optional[str] = None, threshold: int = 3, window_days: int = 7) -> None:
        # Root-Cause-Fix 2026-08-10: Der frühere Literal-Default "web_policy.db"
        # wurde relativ zum Startverzeichnis (CWD) aufgelöst — jedes andere
        # Startverzeichnis hätte stillschweigend eine neue Kopie angelegt.
        if db_path is None:
            from utils.db_path_resolver import get_web_policy_path
            db_path = str(get_web_policy_path())
        self.db_path = db_path
        self.threshold = max(1, int(threshold))
        self.window_seconds = int(max(1, window_days) * DAY)
        self._ensure_schema()

    # --- Public API ---
    def should_fetch(self, url: str, now_unix: Optional[int] = None) -> FetchDecision:
        now = int(now_unix or time.time())
        curl = self._canon(url)
        domain = self._domain(curl)
        con = self._conn()
        try:
            cur = con.cursor()
            # Per-URL gate
            cur.execute("SELECT consecutive_failures, next_retry_at FROM url_failures WHERE url = ?", (curl,))
            row = cur.fetchone()
            if row is not None:
                consec, next_retry_at = int(row[0] or 0), int(row[1] or 0)
                if consec >= self.threshold and next_retry_at and now < next_retry_at:
                    return FetchDecision(False, f"url_blacklisted_{consec}", next_retry_at)
            # Per-domain gate (coarse)
            cur.execute("SELECT consecutive_failures, next_retry_at FROM domain_failures WHERE domain = ?", (domain,))
            row = cur.fetchone()
            if row is not None:
                d_consec, d_next = int(row[0] or 0), int(row[1] or 0)
                if d_consec >= self.threshold and d_next and now < d_next:
                    return FetchDecision(False, f"domain_blacklisted_{d_consec}", d_next)
            return FetchDecision(True, "ok", None)
        finally:
            con.close()

    def record_failure(self, url: str, *, status: Optional[int] = None, error_class: Optional[str] = None, retry_after_seconds: Optional[int] = None, now_unix: Optional[int] = None) -> None:
        """Record a failed attempt to fetch a URL.
        error_class examples: 'timeout', 'dns', 'connect', 'http_4xx', 'http_5xx', 'robots', 'unknown'
        - 404/410 and 'robots' are treated as quasi-permanent (max backoff)
        - 429 honors retry_after_seconds when provided
        - network classes ('timeout','dns','connect') increment domain counters as well
        """
        now = int(now_unix or time.time())
        curl = self._canon(url)
        domain = self._domain(curl)
        con = self._conn()
        try:
            perma = bool(status in (404, 410) or (error_class == "robots"))
            backoff = self._next_backoff(con, table="url_failures", key_field="url", key_val=curl, perma=perma, retry_after_seconds=retry_after_seconds)
            next_retry_at = min(now + backoff, now + int(MAX_BACKOFF))
            # upsert URL row
            con.execute(
                """
                INSERT INTO url_failures(url, domain, fail_count, consecutive_failures, last_failure_at, next_retry_at, last_status, error_class, cooldown_level)
                VALUES(?, ?, 1, 1, ?, ?, ?, ?, CASE WHEN ? THEN 100 ELSE 1 END)
                ON CONFLICT(url) DO UPDATE SET
                    domain = excluded.domain,
                    fail_count = url_failures.fail_count + 1,
                    consecutive_failures = CASE WHEN excluded.cooldown_level=100 THEN url_failures.consecutive_failures+1 ELSE url_failures.consecutive_failures+1 END,
                    last_failure_at = excluded.last_failure_at,
                    next_retry_at = ?,
                    last_status = excluded.last_status,
                    error_class = excluded.error_class,
                    cooldown_level = CASE WHEN ? THEN 100 ELSE url_failures.cooldown_level + 1 END
                """,
                (curl, domain, now, next_retry_at, status, error_class, perma, next_retry_at, perma)
            )
            # Domain-level for network-ish failures
            if error_class in {"timeout", "dns", "connect", "http_5xx", "unknown"} or (status is not None and int(status) >= 500):
                d_backoff = self._next_backoff(con, table="domain_failures", key_field="domain", key_val=domain, perma=False, retry_after_seconds=retry_after_seconds)
                d_next = min(now + d_backoff, now + int(MAX_BACKOFF))
                con.execute(
                    """
                    INSERT INTO domain_failures(domain, fail_count, consecutive_failures, last_failure_at, next_retry_at, last_status, error_class, cooldown_level)
                    VALUES(?, 1, 1, ?, ?, ?, ?, 1)
                    ON CONFLICT(domain) DO UPDATE SET
                        fail_count = domain_failures.fail_count + 1,
                        consecutive_failures = domain_failures.consecutive_failures + 1,
                        last_failure_at = excluded.last_failure_at,
                        next_retry_at = ?,
                        last_status = excluded.last_status,
                        error_class = excluded.error_class,
                        cooldown_level = domain_failures.cooldown_level + 1
                    """,
                    (domain, now, d_next, status, error_class, d_next)
                )
            con.commit()
        finally:
            con.close()

    def record_success(self, url: str) -> None:
        curl = self._canon(url)
        domain = self._domain(curl)
        con = self._conn()
        try:
            con.execute("DELETE FROM url_failures WHERE url = ?", (curl,))
            # Soften domain: decrement consecutive and keep aggregate count
            con.execute(
                """
                UPDATE domain_failures
                SET consecutive_failures = CASE WHEN consecutive_failures>0 THEN consecutive_failures-1 ELSE 0 END,
                    next_retry_at = 0
                WHERE domain = ?
                """,
                (domain,)
            )
            con.commit()
        finally:
            con.close()

    def purge_old(self, older_than_days: int = 90) -> int:
        cutoff = int(time.time() - older_than_days * DAY)
        con = self._conn()
        try:
            cur = con.cursor()
            cur.execute("DELETE FROM url_failures WHERE last_failure_at < ?", (cutoff,))
            n1 = cur.rowcount if cur.rowcount is not None else 0
            cur.execute("DELETE FROM domain_failures WHERE last_failure_at < ?", (cutoff,))
            n2 = cur.rowcount if cur.rowcount is not None else 0
            con.commit()
            return int(n1 + n2)
        finally:
            con.close()

    # --- Internals ---
    def _conn(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.db_path, timeout=5.0, isolation_level=None)
        con.execute("PRAGMA journal_mode=WAL;")
        con.execute("PRAGMA synchronous=NORMAL;")
        return con

    def _ensure_schema(self) -> None:
        con = self._conn()
        try:
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS url_failures (
                    url TEXT PRIMARY KEY,
                    domain TEXT,
                    fail_count INTEGER DEFAULT 0,
                    consecutive_failures INTEGER DEFAULT 0,
                    last_failure_at INTEGER DEFAULT 0,
                    next_retry_at INTEGER DEFAULT 0,
                    last_status INTEGER,
                    error_class TEXT,
                    cooldown_level INTEGER DEFAULT 0
                )
                """
            )
            con.execute("CREATE INDEX IF NOT EXISTS idx_url_failures_domain ON url_failures(domain)")
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS domain_failures (
                    domain TEXT PRIMARY KEY,
                    fail_count INTEGER DEFAULT 0,
                    consecutive_failures INTEGER DEFAULT 0,
                    last_failure_at INTEGER DEFAULT 0,
                    next_retry_at INTEGER DEFAULT 0,
                    last_status INTEGER,
                    error_class TEXT,
                    cooldown_level INTEGER DEFAULT 0
                )
                """
            )
        finally:
            con.close()

    def _next_backoff(self, con: sqlite3.Connection, *, table: str, key_field: str, key_val: str, perma: bool, retry_after_seconds: Optional[int]) -> int:
        if perma:
            return int(MAX_BACKOFF)
        if retry_after_seconds is not None and retry_after_seconds > 0:
            return int(min(retry_after_seconds, MAX_BACKOFF))
        # Read current cooldown_level to choose next slot
        cur = con.cursor()
        cur.execute(f"SELECT cooldown_level FROM {table} WHERE {key_field} = ?", (key_val,))
        row = cur.fetchone()
        lvl = int((row[0] or 0)) if row is not None and row[0] is not None else 0
        lvl = min(lvl, len(BACKOFF_SCHEDULE))
        # jitter +/- 10%
        base = BACKOFF_SCHEDULE[lvl] if lvl < len(BACKOFF_SCHEDULE) else MAX_BACKOFF
        jitter = max(60, int(base * 0.1))
        return int(min(MAX_BACKOFF, base + (int(time.time()) % (2 * jitter) - jitter)))

    _utm_re = re.compile(r"(^|&)(utm_[^=]+=[^&]*)")

    def _canon(self, url: str) -> str:
        try:
            p = urlparse(url)
            scheme = (p.scheme or "http").lower()
            netloc = (p.netloc or "").lower()
            if netloc.startswith("www."):
                netloc = netloc[4:]
            path = p.path or "/"
            # drop fragment
            fragment = ""
            # normalize query: drop utm_* params
            q = p.query or ""
            if q:
                parts = [kv for kv in q.split("&") if not kv.startswith("utm_")]
                q = "&".join(parts)
            return urlunparse((scheme, netloc, path, "", q, fragment))
        except Exception:
            return url

    def _domain(self, url: str) -> str:
        try:
            netloc = urlparse(url).netloc.lower()
            if netloc.startswith("www."):
                netloc = netloc[4:]
            parts = netloc.split(".")
            if len(parts) >= 2:
                return ".".join(parts[-2:])
            return netloc
        except Exception:
            return url
