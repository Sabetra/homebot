"""
Content Extractor V2 - Orchestrator

Main orchestrator that coordinates extraction strategies.
"""
import logging
from typing import Dict, Any, List, Optional
from urllib.parse import urlparse

from .base import ContentExtractor
from .models import ExtractionContext, ExtractionResult

logger = logging.getLogger(__name__)


class ModernContentOrchestrator:
    """
    Orchestrates content extraction using multiple strategies.
    
    Tries strategies in priority order until one succeeds.
    Target CC: <8
    """
    
    def __init__(self, strategies: Optional[List[ContentExtractor]] = None):
        """
        Initialize orchestrator with extraction strategies.
        
        Args:
            strategies: List of extraction strategies (if None, uses default set)
        """
        self._strategies: List[ContentExtractor] = strategies or []
        self._sort_strategies()
    
    def add_strategy(self, strategy: ContentExtractor) -> None:
        """Add a new extraction strategy."""
        self._strategies.append(strategy)
        self._sort_strategies()
    
    def _sort_strategies(self) -> None:
        """Sort strategies by priority (lower = higher priority)."""
        self._strategies.sort(key=lambda s: s.priority)
    
    def extract(
        self,
        url: str,
        timeout: int = 6,
        query: Optional[str] = None,
        accept_language: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Extract content from URL using available strategies.
        
        Args:
            url: URL to extract content from
            timeout: Request timeout in seconds
            query: Optional search query context
            accept_language: Optional Accept-Language header
            
        Returns:
            Dict with extracted content and metadata
        """
        # Create extraction context
        context = ExtractionContext(
            url=url,
            timeout=timeout,
            query=query,
            accept_language=accept_language
        )
        
        # Try each strategy in priority order
        for strategy in self._strategies:
            if not strategy.can_handle(context):
                continue
            
            try:
                result = strategy.extract(context)
                
                # Check if extraction was successful
                if result.success and result.text_len > 0:
                    logger.info(
                        f"✅ Content extracted successfully using {strategy.name}: "
                        f"{result.text_len} chars, quality={result.quality_score:.2f}"
                    )
                    return self._format_result(result)
                    
            except Exception as e:
                logger.warning(
                    f"⚠️ Strategy {strategy.name} failed: {e.__class__.__name__}: {e}"
                )
                continue
        
        # All strategies failed
        logger.error(f"❌ All extraction strategies failed for URL: {url}")
        return self._format_fallback_result(url)
    
    def _format_result(self, result: ExtractionResult) -> Dict[str, Any]:
        """
        Format successful extraction result.
        
        Args:
            result: Extraction result from strategy
            
        Returns:
            Dict compatible with legacy format
        """
        return {
            "url": result.canonical_url,
            "title": result.title or "",
            "snippet": result.snippet,
            "full_text": result.full_text,
            "text_len": result.text_len,
            "success": True,
            "method": result.extraction_method,
            "quality": result.quality_score,
            "domain": result.domain,
            "language": result.language,
            "meta": result.meta
        }
    
    def _format_fallback_result(self, url: str) -> Dict[str, Any]:
        """
        Format fallback result when all strategies fail.
        
        Args:
            url: Original URL
            
        Returns:
            Dict with minimal fallback data
        """
        domain = urlparse(url).netloc or url
        return {
            "url": url,
            "title": "",
            "snippet": "",
            "full_text": "",
            "text_len": 0,
            "success": False,
            "method": "none",
            "quality": 0.0,
            "domain": domain,
            "language": None,
            "meta": {}
        }
