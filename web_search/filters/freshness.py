"""
Freshness Boost Filter
========================

Boosts recently published results for time-sensitive queries.
Reorders results to prioritize fresh content when query suggests
temporal relevance.

SOTA References:
    - Google's QDF (Query Deserves Freshness) algorithm concept
    - Dong et al. (2010): "Time is of the Essence" — freshness in web search

Author: SOTA Web Search Upgrade
Date: 2026-03-08
"""

import logging
import re
from datetime import datetime, timedelta
from typing import List, Optional, Tuple
from ..base import FilterStrategy, SearchResult

logger = logging.getLogger(__name__)


class FreshnessBoostFilter(FilterStrategy):
    """
    Reorder results to prioritize fresh content for time-sensitive queries.
    
    Algorithm:
    1. Detect if query is time-sensitive (QDF heuristic)
    2. Parse dates from result metadata
    3. Apply freshness score as tiebreaker within results
    
    Only activates for time-sensitive queries (news, trends, "aktuell", etc.)
    For non-temporal queries, results pass through unchanged.
    """
    
    _TEMPORAL_KEYWORDS = frozenset([
        # German
        "aktuell", "aktuelle", "neueste", "neuste", "neu",
        "heute", "gestern", "diese woche", "dieser monat",
        "nachrichten", "news", "neuigkeiten",
        "trend", "entwicklung", "update",
        # English
        "latest", "recent", "new", "current",
        "today", "yesterday", "this week", "this month",
        "breaking", "trending", "update",
        # Years (common in research queries)
        "2024", "2025", "2026",
    ])
    
    def __init__(self) -> None:
        self._query: str = ""
    
    def set_query(self, query: str) -> None:
        """Set the current query for freshness detection."""
        self._query = query
    
    def _is_time_sensitive(self, query: str) -> bool:
        """
        Detect if query is time-sensitive using QDF heuristic.
        
        Args:
            query: Search query
            
        Returns:
            True if query likely needs fresh results
        """
        q_lower = query.lower()
        return any(kw in q_lower for kw in self._TEMPORAL_KEYWORDS)
    
    def _parse_date(self, result: SearchResult) -> Optional[datetime]:
        """
        Extract date from result metadata.
        
        Tries multiple date formats commonly found in web search results.
        
        Args:
            result: Search result
            
        Returns:
            Parsed datetime or None
        """
        date_str = result.date or result.metadata.get("date") or ""
        if not date_str:
            return None
        
        # Common date formats in DDG results
        formats = [
            "%Y-%m-%dT%H:%M:%S",      # ISO 8601
            "%Y-%m-%dT%H:%M:%SZ",     # ISO 8601 UTC
            "%Y-%m-%dT%H:%M:%S.%fZ",  # ISO with microseconds
            "%Y-%m-%d %H:%M:%S",      # Standard datetime
            "%Y-%m-%d",               # Date only
            "%d.%m.%Y",               # German format
            "%d/%m/%Y",               # European format
            "%B %d, %Y",              # English format (January 15, 2026)
            "%b %d, %Y",              # Short month (Jan 15, 2026)
        ]
        
        # Clean up date string
        clean = date_str.strip()
        # Remove timezone info like +00:00
        clean = re.sub(r'[+-]\d{2}:\d{2}$', '', clean)
        
        for fmt in formats:
            try:
                return datetime.strptime(clean, fmt)
            except ValueError:
                continue
        
        # Try extracting just a year-month-day pattern
        match = re.search(r'(\d{4})-(\d{1,2})-(\d{1,2})', clean)
        if match:
            try:
                return datetime(
                    int(match.group(1)),
                    int(match.group(2)),
                    int(match.group(3))
                )
            except ValueError:
                pass
        
        return None
    
    def _freshness_score(self, dt: Optional[datetime]) -> float:
        """
        Calculate freshness score (0.0 = old, 1.0 = very fresh).
        
        Decay function: exponential decay over 30 days.
        
        Args:
            dt: Publication datetime
            
        Returns:
            Freshness score between 0.0 and 1.0
        """
        if dt is None:
            return 0.5  # Unknown date gets neutral score
        
        now = datetime.now()
        age_hours = max(0, (now - dt).total_seconds() / 3600)
        
        # Exponential decay: half-life = 7 days (168 hours)
        half_life_hours = 168.0
        freshness = 0.5 ** (age_hours / half_life_hours)
        
        return max(0.0, min(1.0, freshness))
    
    def filter(self, results: List[SearchResult]) -> List[SearchResult]:
        """
        Boost fresh results for time-sensitive queries.
        
        For non-temporal queries, results pass through unchanged.
        For temporal queries, results with dates are boosted via a
        combined score: original_rank * 0.6 + freshness * 0.4
        """
        if not results or not self._query:
            return results
        
        if not self._is_time_sensitive(self._query):
            return results
        
        # Score results by freshness
        scored: List[Tuple[SearchResult, float, float]] = []
        for i, result in enumerate(results):
            dt = self._parse_date(result)
            f_score = self._freshness_score(dt)
            
            # Combined score: position weight + freshness weight
            # Position score: 1.0 for rank 1, decaying
            pos_score = 1.0 / (i + 1)
            combined = pos_score * 0.6 + f_score * 0.4
            
            scored.append((result, combined, f_score))
        
        # Sort by combined score (descending)
        scored.sort(key=lambda x: x[1], reverse=True)
        
        # Log freshness impact
        original_order = [r.url[:40] for r in results[:3]]
        new_order = [r.url[:40] for r, _, _ in scored[:3]]
        if original_order != new_order:
            logger.info(
                f"Freshness boost reordered top results for '{self._query[:40]}'"
            )
        
        return [r for r, _, _ in scored]


__all__ = ["FreshnessBoostFilter"]
