"""
Content Extractor V2 - Newspaper3k Strategy

Extraction using Newspaper3k library (good for news articles).
Priority: 30
"""
import logging
from typing import Optional

from ..models import ExtractionContext, ExtractionResult, ContentMetadata
from ..utils import QualityScorer, ResultFormatter
from .base import BaseExtractorStrategy

logger = logging.getLogger(__name__)


class Newspaper3kExtractor(BaseExtractorStrategy):
    """
    Newspaper3k-based content extraction strategy.
    
    Best for: News articles, journalism content
    Priority: 30
    Target CC: <8
    """
    
    def __init__(self) -> None:
        super().__init__(name="newspaper3k", priority=30)
        self._newspaper_available = self._check_availability()
    
    def _check_availability(self) -> bool:
        """Check if Newspaper3k is available."""
        try:
            import newspaper
            return True
        except ImportError:
            logger.warning("⚠️ Newspaper3k not installed, strategy disabled")
            return False
    
    def can_handle(self, context: ExtractionContext) -> bool:
        """Check if strategy can handle the context."""
        return self._newspaper_available
    
    def extract(self, context: ExtractionContext) -> ExtractionResult:
        """
        Extract content using Newspaper3k.
        
        Args:
            context: Extraction context
            
        Returns:
            ExtractionResult with extracted data
        """
        if not self._newspaper_available:
            return self._create_error_result(
                context.url,
                ImportError("Newspaper3k not installed")
            )
        
        try:
            import newspaper
            
            # Create and download article
            article = newspaper.Article(context.url)
            article.download()
            article.parse()
            
            if not article.text or len(article.text.strip()) < 100:
                return self._create_error_result(
                    context.url,
                    ValueError("Extracted text too short or empty")
                )
            
            # Build metadata
            metadata = None
            if article.authors or article.publish_date or article.keywords:
                metadata = ContentMetadata(
                    author=", ".join(article.authors) if article.authors else None,
                    date=str(article.publish_date) if article.publish_date else None,
                    keywords=article.keywords or [],
                    summary=article.summary if hasattr(article, 'summary') else None
                )
            
            # Calculate quality score
            quality_score = QualityScorer.score(
                article.text,
                title=article.title,
                has_metadata=metadata is not None
            )
            
            # Create result
            return ResultFormatter.create_result(
                url=article.canonical_link or context.url,
                success=True,
                text=article.text,
                title=article.title,
                quality_score=quality_score,
                method="newspaper3k",
                metadata=metadata
            )
            
        except ImportError as e:
            logger.debug(f"Newspaper3k import error for {context.url}: {e}")
            return self._create_error_result(context.url, e)
        except (AttributeError, ValueError, TypeError) as e:
            logger.warning(f"Newspaper3k parameter error for {context.url}: {e}")
            return self._create_error_result(context.url, e)
        except (ConnectionError, TimeoutError, OSError) as e:
            logger.warning(f"Newspaper3k network error for {context.url}: {e}")
            return self._create_error_result(context.url, e)
        except Exception as e:
            logger.error(f"Newspaper3k unexpected error for {context.url}: {e}")
            return self._create_error_result(context.url, e)
