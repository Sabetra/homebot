"""
Content Extractor V2 - Result Formatter

Utility for formatting extraction results.
"""
from typing import Optional, Dict, Any
from urllib.parse import urlparse

from ..models import ExtractionResult, ContentMetadata


class ResultFormatter:
    """
    Formats extraction results into standardized structures.
    
    Target CC: <5
    """
    
    @staticmethod
    def create_result(
        url: str,
        success: bool,
        text: str = "",
        title: Optional[str] = None,
        quality_score: float = 0.0,
        method: str = "unknown",
        metadata: Optional[ContentMetadata] = None,
        error: Optional[str] = None
    ) -> ExtractionResult:
        """
        Create a standardized extraction result.
        
        Args:
            url: Source URL
            success: Whether extraction succeeded
            text: Extracted text content
            title: Optional title
            quality_score: Quality score (0.0-1.0)
            method: Extraction method name
            metadata: Optional content metadata
            error: Optional error message
            
        Returns:
            Formatted ExtractionResult
        """
        domain = ResultFormatter._extract_domain(url)
        snippet = ResultFormatter._create_snippet(text)
        
        meta_dict = {}
        language = None
        
        if metadata:
            meta_dict = {
                "author": metadata.author,
                "date": metadata.date,
                "description": metadata.description,
                "keywords": metadata.keywords,
                "summary": metadata.summary
            }
            language = metadata.language
        
        return ExtractionResult(
            success=success,
            canonical_url=url,
            title=title,
            snippet=snippet,
            full_text=text,
            text_len=len(text),
            quality_score=quality_score,
            extraction_method=method,
            domain=domain,
            language=language,
            meta=meta_dict,
            error=error,
            error_class=None  # error is str, not Exception
        )
    
    @staticmethod
    def _extract_domain(url: str) -> str:
        """Extract domain from URL."""
        try:
            return urlparse(url).netloc or url
        except Exception:
            return url
    
    @staticmethod
    def _create_snippet(text: str, max_length: int = 300) -> str:
        """
        Create a snippet from text.
        
        Args:
            text: Full text
            max_length: Maximum snippet length
            
        Returns:
            Text snippet
        """
        if not text:
            return ""
        
        text = text.strip()
        if len(text) <= max_length:
            return text
        
        return text[:max_length].rsplit(" ", 1)[0] + "..."
