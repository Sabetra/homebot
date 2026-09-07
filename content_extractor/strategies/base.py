"""
Content Extractor V2 - Base Strategy

Base class for extraction strategies.
"""
import logging
from typing import Optional, Tuple
import urllib.request

from ..base import BaseContentExtractor
from ..models import ExtractionContext, ExtractionResult
from ..utils import QualityScorer, ResultFormatter

logger = logging.getLogger(__name__)


class BaseExtractorStrategy(BaseContentExtractor):
    """
    Base class for content extraction strategies.
    
    Provides common functionality for HTTP requests and result formatting.
    """
    
    # User agents for rotation
    USER_AGENTS = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"
    ]
    
    def __init__(self, name: str, priority: int = 50):
        """Initialize base strategy."""
        super().__init__(name, priority)
        self._ua_index = 0
    
    def _http_get(
        self,
        url: str,
        timeout: int = 6,
        accept_language: Optional[str] = None
    ) -> Tuple[Optional[int], Optional[str], Optional[str]]:
        """
        Fetch HTML content from URL.
        
        Args:
            url: URL to fetch
            timeout: Request timeout in seconds
            accept_language: Optional Accept-Language header
            
        Returns:
            Tuple of (status_code, html_content, final_url)
        """
        try:
            headers = {"User-Agent": self._get_user_agent()}
            if accept_language:
                headers["Accept-Language"] = accept_language
            
            req = urllib.request.Request(url, headers=headers)
            
            with urllib.request.urlopen(req, timeout=timeout) as response:
                status = response.getcode()
                html = response.read().decode('utf-8', errors='ignore')
                final_url = response.geturl()
                
                return status, html, final_url
                
        except Exception as e:
            logger.debug(f"HTTP GET failed for {url}: {e}")
            return None, None, None
    
    def _get_user_agent(self) -> str:
        """Get rotating user agent."""
        ua = self.USER_AGENTS[self._ua_index]
        self._ua_index = (self._ua_index + 1) % len(self.USER_AGENTS)
        return ua
    
    def _create_error_result(
        self,
        url: str,
        error: Exception
    ) -> ExtractionResult:
        """
        Create error result.
        
        Args:
            url: Source URL
            error: Exception that occurred
            
        Returns:
            ExtractionResult with error information
        """
        return ResultFormatter.create_result(
            url=url,
            success=False,
            method=self.name,
            error=str(error)
        )
