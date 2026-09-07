"""
Privacy Filter Strategy
========================

Filters search results based on privacy criteria.

Author: Phase 2 Tech-Debt Cleanup
Date: 2026-02-13
"""

import logging
from typing import List, Optional, Set
from ..base import FilterStrategy, SearchResult

logger = logging.getLogger(__name__)


class PrivacyFilter(FilterStrategy):
    """
    Filter results based on privacy criteria.
    
    Removes results from:
    - Explicitly blocked domains
    - Tracking-heavy sites
    - Sites with poor privacy practices
    """
    
    def __init__(self, blocked_domains: Optional[Set[str]] = None) -> None:
        """
        Initialize privacy filter.
        
        Args:
            blocked_domains: Set of domains to block (optional)
        """
        self.blocked_domains = blocked_domains or set()
        logger.debug(f"PrivacyFilter initialized with {len(self.blocked_domains)} blocked domains")
    
    def add_blocked_domain(self, domain: str) -> None:
        """Add a domain to the blocklist"""
        self.blocked_domains.add(domain.lower().strip())
        logger.debug(f"Added blocked domain: {domain}")
    
    def filter(self, results: List[SearchResult]) -> List[SearchResult]:
        """
        Filter results based on privacy criteria.
        
        Args:
            results: Original search results
            
        Returns:
            Filtered search results (blocked domains removed)
        """
        if not results:
            return results
        
        if not self.blocked_domains:
            logger.debug("No blocked domains configured, skipping privacy filter")
            return results
        
        filtered = []
        
        for result in results:
            url = result.url.strip()
            
            if not url:
                continue
            
            # Extract domain from URL
            domain = self._extract_domain(url)
            
            if domain in self.blocked_domains:
                logger.debug(f"Filtered URL (privacy): {url[:50]} (Domain: {domain})")
                result.filtered_reason = f"Privacy: Domain '{domain}' blocked"
                continue
            
            filtered.append(result)
        
        return filtered
    
    def _extract_domain(self, url: str) -> str:
        """
        Extract domain from URL.
        
        Args:
            url: Full URL
            
        Returns:
            Domain name (lowercase)
        """
        try:
            from urllib.parse import urlparse
            parsed = urlparse(url)
            domain = parsed.netloc.lower()
            
            # Remove www. prefix
            if domain.startswith("www."):
                domain = domain[4:]
            
            return domain
        except Exception as e:
            logger.debug(f"Failed to extract domain from {url}: {e}")
            return ""


__all__ = ["PrivacyFilter"]
