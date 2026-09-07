"""
Search Result Cache
====================

TTL-based in-memory cache for web search results.
Avoids redundant DDG API calls for identical/similar queries.

SOTA Reference:
    - Semantic caching with normalized query keys
    - Configurable TTL per query type (news: 5min, general: 30min)

Author: SOTA Web Search Upgrade
Date: 2026-03-08
"""

import hashlib
import logging
import time
import threading
from typing import Dict, Any, Optional, List

logger = logging.getLogger(__name__)


class SearchCache:
    """
    Thread-safe in-memory cache for web search results.
    
    Features:
    - Normalized query keys (lowercase, stripped, sorted words)
    - Configurable TTL (time-to-live) per query type
    - Automatic cleanup of expired entries
    - Memory-bounded (max entries)
    - Cache statistics tracking
    """
    
    # TTL in seconds per query characteristic
    DEFAULT_TTL = 1800       # 30 min for general queries
    NEWS_TTL = 300           # 5 min for news queries
    FACTUAL_TTL = 3600       # 60 min for factual queries (opening hours etc.)
    MAX_ENTRIES = 500        # Memory bound
    
    _NEWS_KEYWORDS = frozenset([
        "news", "aktuell", "aktuelle", "heute", "gestern",
        "latest", "breaking", "nachrichten", "neuigkeiten",
        "2026", "2025",
    ])
    
    def __init__(self, max_entries: int = MAX_ENTRIES) -> None:
        self._cache: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()
        self._max_entries = max_entries
        self._stats = {
            "hits": 0,
            "misses": 0,
            "evictions": 0,
            "stores": 0,
        }
        logger.debug(f"SearchCache initialized (max_entries={max_entries})")
    
    def _normalize_key(self, query: str, num_results: int = 3) -> str:
        """
        Normalize query to cache key.
        
        Normalization:
        - Lowercase
        - Strip whitespace
        - Sort words (order-independent matching)
        - Include num_results in key
        
        Args:
            query: Original search query
            num_results: Number of requested results
            
        Returns:
            Normalized cache key (MD5 hash)
        """
        normalized = " ".join(sorted(query.lower().strip().split()))
        raw_key = f"{normalized}|n={num_results}"
        return hashlib.md5(raw_key.encode("utf-8")).hexdigest()
    
    def _get_ttl(self, query: str) -> int:
        """
        Determine TTL based on query content.
        
        Args:
            query: Search query
            
        Returns:
            TTL in seconds
        """
        query_lower = query.lower()
        
        if any(kw in query_lower for kw in self._NEWS_KEYWORDS):
            return self.NEWS_TTL
        
        # Factual queries (opening hours, prices, etc.) -- longer TTL
        factual_keywords = [
            "öffnungszeit", "preis", "kosten", "adresse", "telefon",
            "opening hour", "price", "cost", "address", "phone",
        ]
        if any(kw in query_lower for kw in factual_keywords):
            return self.FACTUAL_TTL
        
        return self.DEFAULT_TTL
    
    def get(self, query: str, num_results: int = 3) -> Optional[Dict[str, Any]]:
        """
        Retrieve cached results if available and not expired.
        
        Args:
            query: Search query
            num_results: Number of requested results
            
        Returns:
            Cached result dict or None if miss/expired
        """
        key = self._normalize_key(query, num_results)
        
        with self._lock:
            entry = self._cache.get(key)
            
            if entry is None:
                self._stats["misses"] += 1
                return None
            
            # Check expiry
            if time.time() > entry["expires_at"]:
                del self._cache[key]
                self._stats["misses"] += 1
                self._stats["evictions"] += 1
                logger.debug(f"Cache expired for: '{query[:40]}'")
                return None
            
            self._stats["hits"] += 1
            logger.debug(
                f"Cache HIT for: '{query[:40]}' "
                f"(age: {time.time() - entry['stored_at']:.0f}s)"
            )
            
            # Return deep copy to prevent mutation
            result = entry["result"].copy()
            result["_cache_hit"] = True
            result["_cache_age_s"] = time.time() - entry["stored_at"]
            return result
    
    def store(self, query: str, num_results: int, result: Dict[str, Any]) -> None:
        """
        Store search results in cache.
        
        Only caches successful results with at least 1 result.
        
        Args:
            query: Search query
            num_results: Number of requested results
            result: Search result dict to cache
        """
        # Don't cache failures or empty results
        if not result.get("success") or not result.get("results"):
            return
        
        key = self._normalize_key(query, num_results)
        ttl = self._get_ttl(query)
        
        with self._lock:
            # Evict oldest entries if at capacity
            if len(self._cache) >= self._max_entries:
                self._evict_oldest()
            
            self._cache[key] = {
                "query": query,
                "result": result.copy(),
                "stored_at": time.time(),
                "expires_at": time.time() + ttl,
                "ttl": ttl,
            }
            self._stats["stores"] += 1
            
            logger.debug(f"Cached: '{query[:40]}' (TTL: {ttl}s, entries: {len(self._cache)})")
    
    def _evict_oldest(self) -> None:
        """Evict the oldest cache entry."""
        if not self._cache:
            return
        
        oldest_key = min(self._cache, key=lambda k: self._cache[k]["stored_at"])
        del self._cache[oldest_key]
        self._stats["evictions"] += 1
    
    def clear(self) -> None:
        """Clear all cache entries."""
        with self._lock:
            count = len(self._cache)
            self._cache.clear()
            logger.info(f"Cache cleared ({count} entries removed)")
    
    def get_stats(self) -> Dict[str, Any]:
        """Get cache statistics."""
        with self._lock:
            total = self._stats["hits"] + self._stats["misses"]
            hit_rate = self._stats["hits"] / total if total > 0 else 0.0
            
            return {
                **self._stats,
                "total_queries": total,
                "hit_rate": hit_rate,
                "current_entries": len(self._cache),
                "max_entries": self._max_entries,
            }
    
    @property
    def size(self) -> int:
        """Current number of cached entries."""
        return len(self._cache)


# Singleton instance
_cache_instance: Optional[SearchCache] = None
_cache_lock = threading.Lock()


def get_search_cache() -> SearchCache:
    """Get or create the singleton SearchCache instance."""
    global _cache_instance
    if _cache_instance is None:
        with _cache_lock:
            if _cache_instance is None:
                _cache_instance = SearchCache()
    return _cache_instance


__all__ = ["SearchCache", "get_search_cache"]
