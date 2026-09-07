"""
AI Enrichment Strategy
=======================

Enriches search results with AI-powered content extraction.

Author: Phase 2 Tech-Debt Cleanup
Date: 2026-02-13
"""

import logging
from typing import Any, Callable, Dict, Optional
from ..base import EnrichmentStrategy, SearchResult

logger = logging.getLogger(__name__)


class AIEnrichment(EnrichmentStrategy):
    """
    Enrich results with AI-powered content extraction.
    
    Integrates with existing AgentToolkit._ai_extract_content method.
    """
    
    def __init__(self, ai_callback: Callable[..., Any], web_policy: Any = None) -> None:
        """
        Initialize AI enrichment.
        
        Args:
            ai_callback: Function to extract content with AI (e.g., _ai_extract_content)
            web_policy: WebFetchPolicy instance for failure tracking (optional)
        """
        self.ai_callback = ai_callback
        self.web_policy = web_policy
        logger.debug("AIEnrichment initialized")
    
    def enrich(
        self,
        result: SearchResult,
        timeout: int,
        **kwargs: Any
    ) -> SearchResult:
        """
        Enrich result with AI-powered extraction.
        
        Args:
            result: Original search result
            timeout: HTTP timeout in seconds
            **kwargs: Additional parameters
            
        Returns:
            Enriched search result
        """
        url = result.url.strip()
        
        if not url:
            logger.debug("Skipping AI enrichment for empty URL")
            return result
        
        try:
            # AI extraction
            ai_data = self.ai_callback(url, timeout=timeout)
            
            if ai_data.get("success"):
                # Add AI-generated data to metadata
                result = self._add_ai_metadata(result, ai_data)
                logger.debug(f"AI enriched: {url[:50]}")
            else:
                # Record failure
                if self.web_policy:
                    self.web_policy.record_failure(
                        url,
                        status=ai_data.get("status"),
                        error_class=ai_data.get("error_class")
                    )
                
                result.enrich_error = ai_data.get("error") or ai_data.get("error_class")
                logger.debug(f"AI enrichment failed for {url[:50]}: {result.enrich_error}")
        
        except Exception as e:
            logger.warning(f"AI enrichment exception for {url[:50]}: {type(e).__name__}: {e}")
            result.enrich_error = str(e)
        
        return result
    
    def _add_ai_metadata(
        self,
        result: SearchResult,
        ai_data: Dict[str, Any]
    ) -> SearchResult:
        """
        Add AI-generated data to result metadata.
        
        Args:
            result: Original result
            ai_data: AI extraction data
            
        Returns:
            Updated result
        """
        metadata = result.metadata or {}
        metadata.update({
            "ai_summary": ai_data.get("summary"),
            "ai_keywords": ai_data.get("keywords"),
        })
        result.metadata = metadata
        
        return result


__all__ = ["AIEnrichment"]
