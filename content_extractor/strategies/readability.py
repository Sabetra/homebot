"""
Content Extractor V2 - Readability Strategy

Extraction using Readability library (good for structured content).
Priority: 20
"""
import logging
from typing import Optional

from ..models import ExtractionContext, ExtractionResult
from ..utils import QualityScorer, ResultFormatter
from .base import BaseExtractorStrategy

logger = logging.getLogger(__name__)


class ReadabilityExtractor(BaseExtractorStrategy):
    """
    Readability-based content extraction strategy.
    
    Best for: Structured web content, well-formatted articles
    Priority: 20
    Target CC: <8
    """
    
    def __init__(self) -> None:
        super().__init__(name="readability", priority=20)
        self._readability_available = self._check_availability()
    
    def _check_availability(self) -> bool:
        """Check if Readability and BeautifulSoup are available."""
        try:
            from readability import Document
            from bs4 import BeautifulSoup
            return True
        except ImportError:
            logger.warning("⚠️ Readability/BeautifulSoup not installed, strategy disabled")
            return False
    
    def can_handle(self, context: ExtractionContext) -> bool:
        """Check if strategy can handle the context."""
        return self._readability_available
    
    def extract(self, context: ExtractionContext) -> ExtractionResult:
        """
        Extract content using Readability.
        
        Args:
            context: Extraction context
            
        Returns:
            ExtractionResult with extracted data
        """
        if not self._readability_available:
            return self._create_error_result(
                context.url,
                ImportError("Readability/BeautifulSoup not installed")
            )
        
        try:
            from readability import Document
            from bs4 import BeautifulSoup
            
            # Fetch HTML
            status, html, final_url = self._http_get(
                context.url,
                timeout=context.timeout,
                accept_language=context.accept_language
            )
            
            if not status or status >= 400 or not html:
                return self._create_error_result(
                    context.url,
                    ValueError(f"HTTP request failed: status={status}")
                )
            
            # Extract using Readability
            doc = Document(html)
            title = doc.title()
            clean_content = doc.summary()
            
            # Parse HTML to plain text
            soup = BeautifulSoup(clean_content, "html.parser")
            clean_text = soup.get_text(separator=" ", strip=True)
            
            if not clean_text or len(clean_text.strip()) < 100:
                return self._create_error_result(
                    context.url,
                    ValueError("Extracted text too short or empty")
                )
            
            # Calculate quality score
            quality_score = QualityScorer.score(
                clean_text,
                title=title,
                has_metadata=bool(title)
            )
            
            # Create result
            return ResultFormatter.create_result(
                url=final_url or context.url,
                success=True,
                text=clean_text,
                title=title,
                quality_score=quality_score,
                method="readability"
            )
            
        except ImportError as e:
            logger.debug(f"Readability import error for {context.url}: {e}")
            return self._create_error_result(context.url, e)
        except (AttributeError, ValueError, TypeError) as e:
            logger.warning(f"Readability parameter error for {context.url}: {e}")
            return self._create_error_result(context.url, e)
        except (ConnectionError, TimeoutError, OSError) as e:
            logger.warning(f"Readability network error for {context.url}: {e}")
            return self._create_error_result(context.url, e)
        except Exception as e:
            logger.error(f"Readability unexpected error for {context.url}: {e}")
            return self._create_error_result(context.url, e)
