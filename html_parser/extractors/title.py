"""
HTML Parser V2 - Title Extractor

Extract title from HTML with priority chain: h1 > og:title > <title>
Target CC: <7
"""
import logging
from typing import Optional, Any, Dict


class TitleExtractor:
    """
    Extract title from HTML using priority chain.
    
    Priority: h1 > og:title > <title>
    Target CC: 6
    """
    
    def extract(self, soup: Any, og_tags: Optional[Dict[str, str]] = None) -> str:
        """
        Extract title from soup using priority chain.
        
        Args:
            soup: BeautifulSoup object
            og_tags: Optional OpenGraph tags dict (for og:title)
            
        Returns:
            Extracted title (empty string if not found)
        """
        # Priority 1: h1 tag
        title = self._extract_h1(soup)
        if title:
            return title
        
        # Priority 2: og:title
        if og_tags:
            title = og_tags.get("og:title", "")
            if title:
                return title
        
        # Priority 3: <title> tag
        title = self._extract_title_tag(soup)
        if title:
            return title
        
        return ""
    
    def _extract_h1(self, soup: Any) -> str:
        """Extract text from first h1 tag."""
        try:
            from bs4.element import Tag
            
            h1 = soup.find("h1")
            if isinstance(h1, Tag):
                text = h1.get_text(strip=True)
                if text:
                    return text
        except Exception as e:
            logging.debug(f"H1 extraction error: {e}")
        
        return ""
    
    def _extract_title_tag(self, soup: Any) -> str:
        """Extract text from <title> tag."""
        try:
            from bs4.element import Tag
            
            title_tag = soup.find("title")
            if isinstance(title_tag, Tag):
                text = title_tag.get_text(strip=True)
                if text:
                    return text
        except Exception as e:
            logging.debug(f"Title tag extraction error: {e}")
        
        return ""
