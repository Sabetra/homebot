"""
Content Extractor V2 - Jina AI Reader Strategy

Extraction using Jina AI Reader API (API-based, requires opt-in).
Priority: 40 (lowest)
"""
import logging
import os
import urllib.request
from typing import Optional

from ..models import ExtractionContext, ExtractionResult
from ..utils import QualityScorer, ResultFormatter
from .base import BaseExtractorStrategy

logger = logging.getLogger(__name__)


class JinaAIExtractor(BaseExtractorStrategy):
    """
    Jina AI Reader-based content extraction strategy.
    
    Best for: Generic web content (API-based)
    Priority: 40 (lowest, API call overhead)
    Requires: JINA_API_ENABLED environment variable
    Target CC: <7
    """
    
    def __init__(self) -> None:
        super().__init__(name="jina_ai", priority=40)
        self._jina_enabled = self._check_availability()
    
    def _check_availability(self) -> bool:
        """Check if Jina AI Reader is enabled."""
        enabled = os.getenv("JINA_API_ENABLED", "").lower() in {"1", "true", "yes", "on"}
        if not enabled:
            logger.debug("ℹ️ Jina AI Reader disabled (set JINA_API_ENABLED=true to enable)")
        return enabled
    
    def can_handle(self, context: ExtractionContext) -> bool:
        """Check if strategy can handle the context."""
        return self._jina_enabled
    
    def extract(self, context: ExtractionContext) -> ExtractionResult:
        """
        Extract content using Jina AI Reader.
        
        Args:
            context: Extraction context
            
        Returns:
            ExtractionResult with extracted data
        """
        if not self._jina_enabled:
            return self._create_error_result(
                context.url,
                RuntimeError("Jina AI Reader not enabled")
            )
        
        try:
            jina_url = f"https://r.jina.ai/{context.url}"
            
            req = urllib.request.Request(
                jina_url,
                headers={"User-Agent": self._get_user_agent()}
            )
            
            with urllib.request.urlopen(req, timeout=context.timeout) as response:
                clean_content = response.read().decode('utf-8', errors='ignore')
                
                if not clean_content or len(clean_content.strip()) < 100:
                    return self._create_error_result(
                        context.url,
                        ValueError("Extracted text too short or empty")
                    )
                
                # Calculate quality score
                quality_score = QualityScorer.score(clean_content)
                
                # Create result
                return ResultFormatter.create_result(
                    url=context.url,
                    success=True,
                    text=clean_content,
                    quality_score=quality_score,
                    method="jina_ai"
                )
                
        except (ConnectionError, TimeoutError, OSError) as e:
            logger.warning(f"Jina AI Reader network error for {context.url}: {e}")
            return self._create_error_result(context.url, e)
        except (ValueError, TypeError) as e:
            logger.warning(f"Jina AI Reader parameter error for {context.url}: {e}")
            return self._create_error_result(context.url, e)
        except Exception as e:
            logger.error(f"Jina AI Reader unexpected error for {context.url}: {e}")
            return self._create_error_result(context.url, e)
