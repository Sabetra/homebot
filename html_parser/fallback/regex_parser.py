"""
HTML Parser V2 - Regex Fallback Parser

Minimal HTML parser using regex (when BeautifulSoup unavailable).
Target CC: <8
"""
import logging
from typing import List
from html_parser.models import FallbackParseResult


class RegexFallbackParser:
    """
    Minimal regex-based HTML parser (fallback when bs4 unavailable).
    
    Strategy: Simple string operations to extract <title> and basic text
    Target CC: 7
    """
    
    def parse(self, html: str, url: str) -> FallbackParseResult:
        """
        Parse HTML using minimal regex approach.
        
        Args:
            html: Raw HTML string
            url: Original URL (for error logging)
            
        Returns:
            FallbackParseResult with title and text
        """
        errors: List[str] = []
        
        # Extract title
        title = self._extract_title(html, errors)
        
        # Extract text (just whitespace-normalized HTML)
        text = self._extract_text(html)
        
        return FallbackParseResult(title=title, text=text, errors=errors)
    
    def _extract_title(self, html: str, errors: list) -> str:
        """Extract title from <title> tag using string search."""
        try:
            html_lower = (html or "").lower()
            start = html_lower.find("<title>")
            end = html_lower.find("</title>")
            
            if start != -1 and end != -1 and end > start:
                # Extract title text
                title = html[start + 7:end].strip()
                if title:
                    return title
        
        except Exception as e:
            logging.debug(f"Fallback title extraction error: {e}")
            errors.append(f"Title extraction error: {e}")
        
        return ""
    
    def _extract_text(self, html: str) -> str:
        """Extract text by collapsing whitespace."""
        try:
            # Simple whitespace normalization
            return " ".join((html or "").split())
        except Exception as e:
            logging.debug(f"Fallback text extraction error: {e}")
            return ""
