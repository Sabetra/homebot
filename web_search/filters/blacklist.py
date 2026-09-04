"""
Blacklist Filter Strategy
==========================

Filters search results based on URL blacklist policy.

Author: Phase 2 Tech-Debt Cleanup
Date: 2026-02-13
"""

import logging
from typing import Any, Callable, List, Optional
from ..base import FilterStrategy, SearchResult

logger = logging.getLogger(__name__)


class BlacklistFilter(FilterStrategy):
    """
    Filter results based on URL blacklist/policy.
    
    Integrates with existing WebFetchPolicy to check:
    - Blacklisted URLs
    - Temporarily failed URLs with retry logic
    - URL reachability
    """
    
    def __init__(self, web_policy: Any = None, probe_callback: Optional[Callable[..., Any]] = None) -> None:
        """
        Initialize blacklist filter.
        
        Args:
            web_policy: WebFetchPolicy instance (optional)
            probe_callback: Function to probe URL reachability (optional)
        """
        self.web_policy = web_policy
        self.probe_callback = probe_callback
        logger.debug("BlacklistFilter initialized")
    
    def filter(self, results: List[SearchResult]) -> List[SearchResult]:
        """
        Filter results based on blacklist policy.
        
        Args:
            results: Original search results
            
        Returns:
            Filtered search results (blacklisted URLs removed)
        """
        if not results:
            return results
        
        if self.web_policy is None:
            logger.debug("No web policy configured, skipping blacklist filter")
            return results
        
        filtered = []
        
        for result in results:
            url = result.url.strip()
            
            if not url:
                logger.debug("Skipping result with empty URL")
                continue
            
            # Check if URL is allowed by policy
            decision = self.web_policy.should_fetch(url)
            
            if not decision.allow:
                # URL is blacklisted or temporarily blocked
                logger.debug(
                    f"Filtered URL: {url[:50]} (Reason: {decision.reason})"
                )
                result.filtered_reason = decision.reason
                result.retry_at_unix = decision.retry_at_unix
                continue
            
            # Optional: Probe URL reachability
            if self.probe_callback:
                try:
                    probe_ok, probe_err, probe_status = self.probe_callback(url)
                    result.probe_status = probe_status
                    
                    if not probe_ok:
                        result.probe_error_class = probe_err
                        logger.debug(
                            f"URL probe failed: {url[:50]} (Status: {probe_status}, Error: {probe_err})"
                        )
                except Exception as e:
                    logger.debug(f"Probe callback failed for {url[:50]}: {e}")
            
            filtered.append(result)
        
        return filtered


__all__ = ["BlacklistFilter"]
