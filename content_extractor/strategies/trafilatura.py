"""
Content Extractor V2 - Trafilatura Strategy

Extraction using Trafilatura library (best for news/articles).
Priority: 10 (highest)
"""
import logging
from typing import Optional

from ..models import ExtractionContext, ExtractionResult, ContentMetadata
from ..utils import QualityScorer, ResultFormatter
from .base import BaseExtractorStrategy

logger = logging.getLogger(__name__)


class TrafilaturaExtractor(BaseExtractorStrategy):
    """
    Trafilatura-based content extraction strategy.
    
    Best for: News articles, blog posts, structured content
    Priority: 10 (highest)
    Target CC: <8
    """
    
    def __init__(self) -> None:
        super().__init__(name="trafilatura", priority=10)
        self._trafilatura_available = self._check_availability()
    
    def _check_availability(self) -> bool:
        """Check if Trafilatura is available."""
        try:
            import trafilatura
            return True
        except ImportError:
            logger.warning("⚠️ Trafilatura not installed, strategy disabled")
            return False
    
    def can_handle(self, context: ExtractionContext) -> bool:
        """Check if strategy can handle the context."""
        return self._trafilatura_available
    
    def extract(self, context: ExtractionContext) -> ExtractionResult:
        """
        Extract content using Trafilatura.
        
        Args:
            context: Extraction context
            
        Returns:
            ExtractionResult with extracted data
        """
        if not self._trafilatura_available:
            return self._create_error_result(
                context.url,
                ImportError("Trafilatura not installed")
            )
        
        try:
            import trafilatura
            
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
            
            # Extract clean content
            clean_text = trafilatura.extract(
                html,
                include_comments=False,
                include_tables=True,
                include_links=False,
                include_formatting=False,
                output_format='txt'
            )
            
            if not clean_text or len(clean_text.strip()) < 100:
                return self._create_error_result(
                    context.url,
                    ValueError("Extracted text too short or empty")
                )
            
            # Extract metadata
            metadata_obj = trafilatura.extract_metadata(html)
            metadata = None
            
            if metadata_obj:
                metadata = ContentMetadata(
                    author=metadata_obj.author,
                    date=metadata_obj.date,
                    description=metadata_obj.description,
                    language=metadata_obj.language
                )
            
            # Calculate quality score
            quality_score = QualityScorer.score(
                clean_text,
                title=metadata_obj.title if metadata_obj else None,
                has_metadata=metadata is not None
            )
            
            # Create result
            return ResultFormatter.create_result(
                url=final_url or context.url,
                success=True,
                text=clean_text,
                title=metadata_obj.title if metadata_obj else None,
                quality_score=quality_score,
                method="trafilatura",
                metadata=metadata
            )
            
        except ImportError as e:
            logger.debug(f"Trafilatura import error for {context.url}: {e}")
            return self._create_error_result(context.url, e)
        except (AttributeError, ValueError, TypeError) as e:
            logger.warning(f"Trafilatura parameter error for {context.url}: {e}")
            return self._create_error_result(context.url, e)
        except (ConnectionError, TimeoutError, OSError) as e:
            logger.warning(f"Trafilatura network error for {context.url}: {e}")
            return self._create_error_result(context.url, e)
        except Exception as e:
            logger.error(f"Trafilatura unexpected error for {context.url}: {e}")
            return self._create_error_result(context.url, e)
