"""
HTML Parser V2 - Base Classes and Protocols

Defines protocols and base classes for all parser components.
"""
from typing import Protocol, Any
from html_parser.models import (
    HTMLParseContext,
    CleanupResult,
    MetadataResult,
    ContentResult,
    FallbackParseResult
)


class BaseExtractor(Protocol):
    """Protocol for all HTML extractors."""
    
    def extract(self, context: Any) -> Any:
        """Extract data from context.
        
        Args:
            context: Input context (varies by extractor)
            
        Returns:
            Extraction result (varies by extractor)
        """
        ...


class BaseCleanup(Protocol):
    """Protocol for HTML cleanup strategies."""
    
    def cleanup(self, soup: Any, url: str) -> CleanupResult:
        """Clean up HTML soup by removing noisy elements.
        
        Args:
            soup: BeautifulSoup object
            url: Original URL (for error logging)
            
        Returns:
            CleanupResult with cleaned soup
        """
        ...


class BaseFallback(Protocol):
    """Protocol for fallback parsers."""
    
    def parse(self, html: str, url: str) -> FallbackParseResult:
        """Parse HTML using fallback strategy (no BeautifulSoup).
        
        Args:
            html: Raw HTML string
            url: Original URL (for error logging)
            
        Returns:
            FallbackParseResult with minimal parsing
        """
        ...


class ParsingState:
    """Enum-like class for parsing states."""
    CLEANUP = "cleanup"
    METADATA = "metadata"
    CONTENT = "content"
    POST_PROCESS = "post_process"
    FALLBACK = "fallback"
    DONE = "done"
