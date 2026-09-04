"""
HTML Parser V2 - Refactored HTML Parsing with State Machine

Main entry point for V2 HTML parser.
Reduces cyclomatic complexity from 86 to <10 using Chain of Responsibility pattern.

Usage:
    from html_parser import parse_html_v2
    
    result = parse_html_v2(url, html, query="search term")
    # Returns dict matching legacy format
"""
from html_parser.orchestrator import HTMLParserOrchestrator
from html_parser.models import (
    HTMLParseContext,
    HTMLParseResult,
    CleanupResult,
    MetadataResult,
    ContentResult,
    FallbackParseResult
)

__version__ = "2.0.0"
__all__ = [
    "parse_html_v2",
    "HTMLParserOrchestrator",
    "HTMLParseContext",
    "HTMLParseResult",
    "CleanupResult",
    "MetadataResult",
    "ContentResult",
    "FallbackParseResult",
]


def parse_html_v2(url: str, html: str, *, query: str | None = None) -> dict:
    """
    Parse HTML using V2 parser (state machine).
    
    Args:
        url: URL of the HTML page
        html: Raw HTML content
        query: Optional search query for snippet highlighting
        
    Returns:
        Dict with parsed HTML data (matches legacy format)
        
    Example:
        >>> result = parse_html_v2("https://example.com", "<html>...</html>")
        >>> result["title"]
        "Example Page"
    """
    orchestrator = HTMLParserOrchestrator()
    return orchestrator.parse(url, html, query=query)
