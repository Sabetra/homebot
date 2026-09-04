"""
HTML Enrichment Strategy
=========================

Enriches search results by fetching and parsing HTML content.

Author: Phase 2 Tech-Debt Cleanup
Date: 2026-02-13
"""

import logging
from typing import Any, Callable, Dict, Optional
from ..base import EnrichmentStrategy, SearchResult

logger = logging.getLogger(__name__)


class HTMLEnrichment(EnrichmentStrategy):
    """
    Enrich results by fetching and parsing HTML content.
    
    Integrates with existing AgentToolkit._fetch_and_parse method.
    """
    
    def __init__(self, fetch_callback: Callable[..., Any], web_policy: Any = None) -> None:
        """
        Initialize HTML enrichment.
        
        Args:
            fetch_callback: Function to fetch and parse HTML (e.g., _fetch_and_parse)
            web_policy: WebFetchPolicy instance for failure tracking (optional)
        """
        self.fetch_callback = fetch_callback
        self.web_policy = web_policy
        logger.debug("HTMLEnrichment initialized")
    
    def enrich(
        self,
        result: SearchResult,
        timeout: int,
        **kwargs: Any
    ) -> SearchResult:
        """
        Enrich result by fetching HTML content.
        
        Args:
            result: Original search result
            timeout: HTTP timeout in seconds
            **kwargs: Additional parameters (query, accept_language)
            
        Returns:
            Enriched search result
        """
        url = result.url.strip()
        
        if not url:
            logger.debug("Skipping enrichment for empty URL")
            return result
        
        try:
            # Fetch and parse HTML
            enriched_data = self.fetch_callback(
                url,
                timeout=timeout,
                query=kwargs.get("query"),
                accept_language=kwargs.get("accept_language")
            )
            
            if enriched_data.get("success"):
                # Update result with enriched data
                result = self._merge_enriched_data(result, enriched_data)
                logger.debug(f"Successfully enriched: {url[:50]}")
            else:
                # Record failure in policy
                if self.web_policy:
                    self.web_policy.record_failure(
                        url,
                        status=enriched_data.get("status"),
                        error_class=enriched_data.get("error_class")
                    )
                
                result.enrich_error = enriched_data.get("error") or enriched_data.get("error_class")
                logger.debug(f"Enrichment failed for {url[:50]}: {result.enrich_error}")
        
        except Exception as e:
            logger.warning(f"Enrichment exception for {url[:50]}: {type(e).__name__}: {e}")
            result.enrich_error = str(e)
        
        return result
    
    def _merge_enriched_data(
        self,
        result: SearchResult,
        enriched_data: Dict[str, Any]
    ) -> SearchResult:
        """
        Merge enriched data into search result.
        
        Args:
            result: Original result
            enriched_data: Enriched data from fetch_callback
            
        Returns:
            Updated result
        """
        # Update metadata
        metadata = result.metadata or {}
        metadata.update({
            "canonical_url": enriched_data.get("canonical_url") or result.url,
            "domain": enriched_data.get("domain"),
            "detected_lang": enriched_data.get("language"),
            "content_length": enriched_data.get("text_len"),
            "og": enriched_data.get("og", {}),
            "meta": enriched_data.get("meta", {}),
            "full_text": enriched_data.get("full_text", ""),  # SOTA v2.1: for snippet extraction
        })
        result.metadata = metadata
        
        # Update title if better
        if enriched_data.get("title") and not result.title:
            result.title = str(enriched_data.get("title"))
        
        # Update snippet if better (longer)
        enriched_snippet: str = str(enriched_data.get("snippet", "") or "")
        if enriched_snippet and (not result.snippet or len(result.snippet) < 40):
            result.snippet = enriched_snippet
        
        # Normalize URL if canonical provided
        if enriched_data.get("canonical_url"):
            result.url = str(enriched_data.get("canonical_url"))
        
        return result


__all__ = ["HTMLEnrichment"]
