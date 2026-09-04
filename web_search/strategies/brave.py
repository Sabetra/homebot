"""
Brave Search Strategy
======================

Implements SearchStrategy for Brave Search API.

Brave Search provides high-quality web results with a generous free tier (2000 queries/month).
Serves as a complementary engine to DuckDuckGo for engine-fusion diversity.

Requires:
    - BRAVE_API_KEY environment variable
    - requests library (standard in most Python environments)
"""

import logging
import os
import time
from typing import List, Optional

from ..base import SearchStrategy, SearchParams, SearchResult

logger = logging.getLogger(__name__)


class BraveSearchStrategy(SearchStrategy):
    """
    Brave Search API implementation.
    
    Features:
    - Web search with regional support
    - Automatic language detection
    - Rate limiting awareness
    - Comprehensive error handling with structured results
    
    Configuration:
        Set BRAVE_API_KEY environment variable to enable.
        Free tier: 2000 queries/month (sufficient for most bots).
    """
    
    BRAVE_SEARCH_URL = "https://api.search.brave.com/res/v1/web/search"
    
    def __init__(self, api_key: Optional[str] = None) -> None:
        self._api_key = api_key or os.environ.get("BRAVE_API_KEY", "")
        self._available: Optional[bool] = None
        self._last_request_time: float = 0.0
        self._min_request_interval: float = 1.0  # 1 second between requests (free tier: 1 req/sec)
    
    def is_available(self) -> bool:
        """Check if Brave Search API is available (API key configured + requests installed)."""
        if self._available is None:
            if not self._api_key:
                logger.debug("Brave Search not available: BRAVE_API_KEY not set")
                self._available = False
            else:
                try:
                    import requests  # noqa: F401
                    self._available = True
                    logger.info("✅ Brave Search API available")
                except ImportError:
                    logger.warning("Brave Search not available: 'requests' package not installed")
                    self._available = False
        return self._available
    
    def search(self, params: SearchParams) -> List[SearchResult]:
        """
        Execute Brave Search API query.
        
        Args:
            params: Search parameters
            
        Returns:
            List of search results
            
        Raises:
            RuntimeError: If API key not configured
        """
        if not self.is_available():
            raise RuntimeError("Brave Search API not available (check BRAVE_API_KEY)")
        
        import requests
        
        # Rate limiting
        self._rate_limit()
        
        # Build request
        headers = {
            "Accept": "application/json",
            "Accept-Encoding": "gzip",
            "X-Subscription-Token": self._api_key,
        }
        
        # Map region format: "de-de" → country="DE"
        country = params.region.split("-")[0].upper() if params.region else "DE"
        
        api_params = {
            "q": params.query,
            "count": min(params.num_results, 20),  # Brave max is 20
            "country": country,
            "search_lang": country.lower(),
            "safesearch": self._map_safesearch(params.safesearch),
        }
        
        # Time filter
        if params.timelimit:
            freshness_map = {
                "d": "pd",   # past day
                "w": "pw",   # past week
                "m": "pm",   # past month
                "y": "py",   # past year
            }
            api_params["freshness"] = freshness_map.get(params.timelimit, "")
        
        try:
            response = requests.get(
                self.BRAVE_SEARCH_URL,
                headers=headers,
                params=api_params,
                timeout=params.timeout,
            )
            
            if response.status_code == 429:
                logger.warning("Brave Search rate limited (429) — backing off")
                self._min_request_interval = min(self._min_request_interval * 2, 10.0)
                return []
            
            response.raise_for_status()
            data = response.json()
            
            return self._parse_results(data)
            
        except requests.exceptions.Timeout:
            logger.warning(f"Brave Search timeout after {params.timeout}s")
            return []
        except requests.exceptions.RequestException as e:
            logger.error(f"Brave Search request failed: {e}", exc_info=True)
            return []
        except Exception as e:
            logger.error(f"Brave Search unexpected error: {e}", exc_info=True)
            return []
    
    def _parse_results(self, data: dict) -> List[SearchResult]:
        """Parse Brave Search API response into SearchResult objects."""
        results = []
        
        web_results = data.get("web", {}).get("results", [])
        
        for item in web_results:
            try:
                result = SearchResult(
                    title=item.get("title", ""),
                    url=item.get("url", ""),
                    snippet=item.get("description", ""),
                    date=item.get("page_age", None),
                    metadata={
                        "source_engine": "brave",
                        "language": item.get("language", ""),
                        "family_friendly": item.get("family_friendly", True),
                        "extra_snippets": item.get("extra_snippets", []),
                    },
                )
                results.append(result)
            except Exception as e:
                logger.debug(f"Failed to parse Brave result: {e}")
                continue
        
        return results
    
    def _rate_limit(self) -> None:
        """Enforce minimum interval between requests."""
        now = time.time()
        elapsed = now - self._last_request_time
        if elapsed < self._min_request_interval:
            wait = self._min_request_interval - elapsed
            time.sleep(wait)
        self._last_request_time = time.time()
    
    @staticmethod
    def _map_safesearch(safesearch: str) -> str:
        """Map safesearch setting to Brave API format."""
        mapping = {
            "On": "strict",
            "Moderate": "moderate",
            "Off": "off",
        }
        return mapping.get(safesearch, "moderate")
    
    @property
    def name(self) -> str:
        return "BraveSearch"
