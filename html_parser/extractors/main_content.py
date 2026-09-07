"""
HTML Parser V2 - Main Content Extractor

Extract main text content from HTML with priority: <article> > <main> > <body>
Target CC: <10
"""
import logging
from typing import Any


class MainContentExtractor:
    """
    Extract main text content from HTML.
    
    Priority: <article> > <main> > <body> paragraphs
    Target CC: 8
    """
    
    def extract(self, soup: Any) -> str:
        """
        Extract main text content from soup.
        
        Args:
            soup: BeautifulSoup object
            
        Returns:
            Extracted text (empty string if not found)
        """
        # Priority 1: <article> tag
        text = self._extract_from_tag(soup, "article")
        if text:
            return text
        
        # Priority 2: <main> tag
        text = self._extract_from_tag(soup, "main")
        if text:
            return text
        
        # Priority 3: <body> paragraphs
        text = self._extract_from_tag(soup, "body")
        if text:
            return text
        
        # Fallback: entire soup
        return self._extract_all(soup)
    
    def _extract_from_tag(self, soup: Any, tag_name: str) -> str:
        """Extract text from specific tag."""
        try:
            from bs4.element import Tag
            
            tag = soup.find(tag_name)
            if not isinstance(tag, Tag):
                return ""
            
            # Try to extract paragraphs and list items
            return self._extract_paragraphs(tag)
            
        except Exception as e:
            logging.debug(f"Text extraction from {tag_name} error: {e}")
            return ""
    
    def _extract_paragraphs(self, tag: Any) -> str:
        """Extract text from paragraphs and list items."""
        try:
            from bs4.element import Tag
            
            paras = [
                p.get_text(" ", strip=True)
                for p in tag.find_all(["p", "li"])
                if isinstance(p, Tag) and p.get_text(strip=True)
            ]
            
            if paras:
                return "\n".join(paras)
            
            # Fallback: all text from tag
            return str(tag.get_text(" ", strip=True))
            
        except Exception as e:
            logging.debug(f"Paragraph extraction error: {e}")
            # Fallback: all text from tag
            try:
                return str(tag.get_text(" ", strip=True))
            except Exception:
                return ""
    
    def _extract_all(self, soup: Any) -> str:
        """Fallback: extract all text from soup."""
        try:
            return str(soup.get_text(" ", strip=True))
        except Exception as e:
            logging.debug(f"Full text extraction error: {e}")
            return ""
