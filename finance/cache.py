"""In-memory LRU cache for finance query results with warmup and stale eviction.

SOTA Features:
- TTL-based stale eviction: Entries expire after a configurable time-to-live
- Cache warmup: Preloads common finance queries at startup to reduce cold-start latency
- Async-safe: Uses asyncio locks for thread safety in async contexts
- Metrics: Tracks hit/miss ratios for observability
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from collections import OrderedDict
from typing import Any, Awaitable, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

# Default TTL: 10 minutes (600 seconds) - finance data is relatively static within a session
DEFAULT_TTL_SECONDS = 600

# Common query patterns for cache warmup
WARMUP_QUERIES = [
    "Wie viel habe ich im letzten Monat ausgegeben?",
    "Zeig mir meine aktuellen Budget-Verbräuche",
    "Welche Kosten waren im letzten Monat am höchsten?",
    "Zeig mir meine letzten Buchungen",
    "Wie sieht meine Kostenentwicklung aus?",
]


class _CacheEntry:
    __slots__ = ("value", "created_at", "ttl")

    def __init__(self, value: Any, ttl: float) -> None:
        self.value = value
        self.created_at = time.monotonic()
        self.ttl = ttl

    @property
    def is_stale(self) -> bool:
        return time.monotonic() - self.created_at > self.ttl


class FinanceQueryCache:
    """Thread-safe LRU cache with TTL-based stale eviction and warmup support."""

    def __init__(self, max_size: int = 512, ttl_seconds: float = DEFAULT_TTL_SECONDS) -> None:
        self._max_size = max_size
        self._ttl_seconds = ttl_seconds
        self._store: OrderedDict[str, _CacheEntry] = OrderedDict()
        self._lock = asyncio.Lock()
        # Metrics
        self._hits = 0
        self._misses = 0
        self._evictions = 0
        self._warmup_done = False
        # Warmup query cache (populated on first warmup)
        self._warmup_results: Dict[str, Any] = {}

    @property
    def size(self) -> int:
        return len(self._store)

    @property
    def capacity(self) -> int:
        return self._max_size

    @property
    def ttl_seconds(self) -> float:
        return self._ttl_seconds

    def _make_key(self, query: str, params: Dict[str, Any]) -> str:
        """Create a deterministic cache key from query + params."""
        raw = f"{query}|{frozenset(params.items())}"
        return hashlib.sha256(raw.encode("utf-8", errors="replace")).hexdigest()[:32]

    async def fetch(
        self,
        query: str,
        params: Optional[Dict[str, Any]] = None,
    ) -> Optional[Any]:
        """Return cached result if fresh, else None."""
        params = params or {}
        key = self._make_key(query, params)
        async with self._lock:
            entry = self._store.get(key)
            if entry is None:
                self._misses += 1
                return None
            if entry.is_stale:
                # Evict stale entry
                del self._store[key]
                self._evictions += 1
                self._misses += 1
                logger.debug(f"Cache stale eviction: {key[:8]}...")
                return None
            # Hit — move to end (most recently used)
            self._store.move_to_end(key)
            self._hits += 1
            return entry.value

    async def store(self, query: str, params: Dict[str, Any], value: Any) -> None:
        """Cache a result, evicting LRU entries when at capacity."""
        key = self._make_key(query, params)
        async with self._lock:
            if key in self._store:
                # Update in-place, keep position
                self._store[key] = _CacheEntry(value, self._ttl_seconds)
                self._store.move_to_end(key)
            else:
                # Evict LRU entries until room
                while len(self._store) >= self._max_size:
                    evicted_key, evicted_entry = self._store.popitem(last=False)
                    self._evictions += 1
                    logger.debug(f"Cache LRU eviction: {evicted_key[:8]}...")
                self._store[key] = _CacheEntry(value, self._ttl_seconds)

    async def warmup(
        self,
        execute_query_fn: Optional[Callable[[str], Awaitable[Any]]] = None,
    ) -> int:
        """Pre-populate cache with common query patterns.

        Args:
            execute_query_fn: Optional async callable(query: str) -> result.
                If provided, actual queries are executed. Otherwise, placeholder
                entries are stored to reserve cache slots.

        Returns:
            Number of entries warmed up.
        """
        async with self._lock:
            if self._warmup_done:
                return 0

            warmed_up = 0
            for query in WARMUP_QUERIES:
                key = self._make_key(query, {})
                if key not in self._store:
                    if execute_query_fn is not None:
                        try:
                            result = await execute_query_fn(query)
                            self._store[key] = _CacheEntry(result, self._ttl_seconds)
                        except Exception as exc:
                            logger.warning(f"Cache warmup failed for query: {exc}")
                            # Store a placeholder to avoid repeated attempts
                            self._store[key] = _CacheEntry(None, self._ttl_seconds)
                    else:
                        # Store placeholder — marks slot as reserved
                        self._store[key] = _CacheEntry(None, self._ttl_seconds)
                    warmed_up += 1

            self._warmup_done = True
            logger.info(f"Cache warmup complete: {warmed_up} entries preloaded")
            return warmed_up

    async def evict_stale(self) -> int:
        """Remove all stale entries. Call periodically or on demand."""
        async with self._lock:
            stale_keys: List[str] = []
            for key, entry in self._store.items():
                if entry.is_stale:
                    stale_keys.append(key)

            for key in stale_keys:
                del self._store[key]
                self._evictions += 1

            if stale_keys:
                logger.info(f"Evicted {len(stale_keys)} stale cache entries")
            return len(stale_keys)

    async def clear(self) -> None:
        """Remove all entries and reset metrics."""
        async with self._lock:
            self._store.clear()
            self._hits = 0
            self._misses = 0
            self._evictions = 0
            self._warmup_done = False
            self._warmup_results.clear()

    def stats(self) -> Dict[str, Any]:
        """Return cache metrics."""
        total = self._hits + self._misses
        hit_rate = self._hits / total if total > 0 else 0.0
        return {
            "size": self.size,
            "capacity": self.capacity,
            "ttl_seconds": self._ttl_seconds,
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": round(hit_rate, 4),
            "evictions": self._evictions,
            "warmup_done": self._warmup_done,
        }

    @property
    def hit_rate(self) -> float:
        """Convenience: return current hit rate."""
        return self.stats()["hit_rate"]


__all__ = ["FinanceQueryCache"]