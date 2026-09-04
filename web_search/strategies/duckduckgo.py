"""
DuckDuckGo Search Strategy
===========================

Implements SearchStrategy for DuckDuckGo search engine.

Author: Phase 2 Tech-Debt Cleanup
Date: 2026-02-13
"""

import logging
import time
from typing import List, Dict, Any, Optional
from ..base import SearchStrategy, SearchParams, SearchResult
from ..rate_limiter import get_rate_limiter

logger = logging.getLogger(__name__)


class DuckDuckGoStrategy(SearchStrategy):
    """
    DuckDuckGo search implementation with robust error handling.
    
    Features:
    - News search for news-related queries
    - Text search fallback
    - Executor shutdown retry logic
    - Rate limiting (SOTA v2)
    - Comprehensive error handling
    """
    
    def __init__(self) -> None:
        self._available: Optional[bool] = None
        self._rate_limiter = get_rate_limiter()
    
    def is_available(self) -> bool:
        """Check if DDGS module is available"""
        if self._available is None:
            try:
                from ddgs import DDGS  # noqa: F401
                self._available = True
                logger.debug("DuckDuckGo module available")
            except ImportError:
                logger.warning("DuckDuckGo module not available (pip install duckduckgo-search)")
                self._available = False
        return self._available
    
    def search(self, params: SearchParams) -> List[SearchResult]:
        """
        Execute DuckDuckGo search with automatic news/text routing.
        
        Args:
            params: Search parameters
            
        Returns:
            List of search results
            
        Raises:
            RuntimeError: If DDGS is not available
        """
        if not self.is_available():
            raise RuntimeError("DuckDuckGo module not available")
        
        # Determine search type
        prefer_news = self._should_prefer_news(params.query)
        
        # Try news first if applicable
        if prefer_news:
            try:
                results = self._search_news(params)
                if results:
                    logger.debug(f"Found {len(results)} news results for '{params.query[:50]}'")
                    return results
            except Exception as e:
                logger.debug(f"News search failed, falling back to text: {e}")
        
        # Fallback to text search
        results = self._search_text(params)
        logger.debug(f"Found {len(results)} text results for '{params.query[:50]}'")
        return results
    
    def _should_prefer_news(self, query: str) -> bool:
        """
        Detect if query is news-related.
        
        Args:
            query: Search query
            
        Returns:
            True if news search is preferred
        """
        news_keywords = [
            "news", "aktuell", "aktuelle", "heute", "gestern",
            "latest", "breaking", "nachrichten", "neuigkeiten"
        ]
        query_lower = query.lower()
        return any(keyword in query_lower for keyword in news_keywords)
    
    def _search_news(self, params: SearchParams) -> List[SearchResult]:
        """
        Execute DuckDuckGo news search.
        
        Args:
            params: Search parameters
            
        Returns:
            List of news results (empty if failed)
        """
        from ddgs import DDGS
        results = []
        
        try:
            self._rate_limiter.acquire()
            with DDGS() as ddgs:
                for item in ddgs.news(
                    params.query,
                    max_results=params.num_results,
                    region=params.region,
                    safesearch=params.safesearch,
                    timelimit=params.timelimit
                ):
                    results.append(self._parse_news_item(item))
                    
        except (ConnectionError, TimeoutError) as e:
            logger.warning(f"DDG News connection error for '{params.query}': {e}")
        except (ValueError, TypeError) as e:
            logger.warning(f"DDG News input error for '{params.query}': {e}")
        except RuntimeError as e:
            if "cannot schedule new futures after shutdown" in str(e):
                logger.warning(f"DDG News executor shutdown for '{params.query}', will try text")
            else:
                logger.error(f"DDG News runtime error for '{params.query}': {e}")
        except Exception as e:
            logger.error(f"DDG News unexpected error for '{params.query}': {type(e).__name__}: {e}")
        
        return results
    
    def _search_text(self, params: SearchParams) -> List[SearchResult]:
        """
        Execute DuckDuckGo text search with retry logic.
        
        Args:
            params: Search parameters
            
        Returns:
            List of text results
            
        Raises:
            Exception: If search fails after retry
        """
        # First attempt
        try:
            return self._do_text_search(params)
        except RuntimeError as e:
            if "cannot schedule new futures after shutdown" not in str(e):
                raise
            
            # Executor shutdown - retry with fresh instance
            logger.warning(f"DDG executor shutdown for '{params.query}', retrying...")
            return self._retry_text_search(params)
    
    def _do_text_search(self, params: SearchParams) -> List[SearchResult]:
        """Execute single text search attempt"""
        from ddgs import DDGS
        results = []
        
        self._rate_limiter.acquire()
        with DDGS() as ddgs:
            for item in ddgs.text(
                params.query,
                max_results=params.num_results,
                region=params.region,
                safesearch=params.safesearch,
                timelimit=params.timelimit
            ):
                results.append(self._parse_text_item(item))
        
        return results
    
    def _retry_text_search(self, params: SearchParams) -> List[SearchResult]:
        """
        Retry text search with fresh DDGS instance.
        
        Args:
            params: Search parameters
            
        Returns:
            List of text results
            
        Raises:
            Exception: If retry fails
        """
        time.sleep(0.3)  # Brief pause before retry
        
        try:
            results = self._do_text_search(params)
            logger.info(f"✅ DDG retry successful for '{params.query[:50]}'")
            return results
        except Exception as e:
            logger.error(f"❌ DDG retry failed for '{params.query}': {e}")
            raise
    
    def _parse_news_item(self, item: Dict[str, Any]) -> SearchResult:
        """
        Parse DuckDuckGo news API item to SearchResult.
        
        Args:
            item: Raw news API response item
            
        Returns:
            Parsed SearchResult
        """
        return SearchResult(
            title=item.get("title", ""),
            url=item.get("url") or item.get("link") or item.get("href", ""),
            snippet=item.get("body") or item.get("excerpt") or item.get("source", ""),
            date=item.get("date") or item.get("published") or item.get("pubDate")
        )
    
    def _parse_text_item(self, item: Dict[str, Any]) -> SearchResult:
        """
        Parse DuckDuckGo text API item to SearchResult.
        
        Args:
            item: Raw text API response item
            
        Returns:
            Parsed SearchResult
        """
        return SearchResult(
            title=item.get("title", ""),
            url=item.get("href") or item.get("url", ""),
            snippet=item.get("body") or item.get("excerpt", "")
        )


__all__ = ["DuckDuckGoStrategy"]
