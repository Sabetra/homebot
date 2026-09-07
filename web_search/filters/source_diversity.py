"""
Source Diversity Filter
========================

Ensures search results come from diverse domains.
Prevents domination by a single domain (e.g., 4/5 results from wikipedia.org).

SOTA References:
    - Carbonell & Goldstein (1998): MMR (Maximal Marginal Relevance)
    - Santos et al. (2010): Explicit result diversification
    - Commercial search engines limit to 2 results per domain

Author: SOTA Web Search Upgrade
Date: 2026-03-08
"""

import logging
from typing import List, Dict
from urllib.parse import urlparse
from ..base import FilterStrategy, SearchResult

logger = logging.getLogger(__name__)


class SourceDiversityFilter(FilterStrategy):
    """
    Limit results per domain to enforce source diversity.
    
    Default: max 2 results per domain.
    This is standard practice in commercial search engines
    (Google, Bing each limit site-grouping to ~2 results).
    
    Algorithm:
    1. Parse domain from each result URL
    2. Allow up to max_per_domain results from each domain
    3. Excess results are moved to end (not removed entirely)
       to maintain result count
    """
    
    def __init__(self, max_per_domain: int = 2) -> None:
        """
        Args:
            max_per_domain: Maximum results from a single domain (default: 2)
        """
        self._max_per_domain = max_per_domain
    
    def _extract_domain(self, url: str) -> str:
        """
        Extract effective domain from URL.
        
        Normalizes:
        - Remove www. prefix
        - Handle subdomains (keep 2nd-level domain)
        
        Args:
            url: Result URL
            
        Returns:
            Normalized domain string
        """
        try:
            parsed = urlparse(url)
            host = (parsed.hostname or "").lower()
            host = host.removeprefix("www.")
            
            # For known TLDs with country codes (e.g., bbc.co.uk),
            # keep the full second-level domain
            parts = host.split(".")
            if len(parts) >= 3 and len(parts[-2]) <= 3:
                # e.g., bbc.co.uk → bbc.co.uk
                return ".".join(parts[-3:])
            elif len(parts) >= 2:
                # e.g., en.wikipedia.org → wikipedia.org
                return ".".join(parts[-2:])
            return host
        except Exception:
            return url.lower()
    
    def filter(self, results: List[SearchResult]) -> List[SearchResult]:
        """
        Enforce source diversity by limiting results per domain.
        
        Results exceeding the per-domain limit are moved to the end
        of the list (preserved but deprioritized).
        """
        if not results or len(results) <= self._max_per_domain:
            return results
        
        domain_counts: Dict[str, int] = {}
        primary: List[SearchResult] = []
        overflow: List[SearchResult] = []
        
        for result in results:
            domain = self._extract_domain(result.url)
            count = domain_counts.get(domain, 0)
            
            if count < self._max_per_domain:
                primary.append(result)
                domain_counts[domain] = count + 1
            else:
                overflow.append(result)
                domain_counts[domain] = count + 1
        
        if overflow:
            domains_limited = set(
                d for d, c in domain_counts.items() 
                if c > self._max_per_domain
            )
            logger.info(
                f"Source diversity: {len(overflow)} results deprioritized "
                f"from domains: {domains_limited}"
            )
        
        return primary + overflow


__all__ = ["SourceDiversityFilter"]
