"""
HTML Parser V2 - Canonical URL Extractor

Extract canonical URL from HTML link tags.
Target CC: <5
"""
import logging
import urllib.parse
from typing import Optional, Any


class CanonicalURLExtractor:
    """
    Extract canonical URL from <link rel="canonical"> tags.
    
    Priority: First valid canonical link found.
    Target CC: 4
    """
    
    def extract(self, soup: Any, base_url: str) -> Optional[str]:
        """
        Extract canonical URL from soup.
        
        Args:
            soup: BeautifulSoup object
            base_url: Base URL for resolving relative links
            
        Returns:
            Canonical URL or None if not found
        """
        try:
            from bs4.element import Tag
            
            for link in soup.find_all("link"):
                if not isinstance(link, Tag):
                    continue
                
                # Check if rel="canonical"
                if self._is_canonical(link):
                    href = link.get("href")
                    if href:
                        return urllib.parse.urljoin(base_url, str(href))
            
            return None
            
        except Exception as e:
            logging.debug(f"Canonical URL extraction failed: {e}")
            return None
    
    def _is_canonical(self, link: Any) -> bool:
        """Check if link tag has rel='canonical'."""
        rel_val = link.get("rel")
        
        # Handle list or string
        if isinstance(rel_val, list):
            rels = [str(x).lower().strip() for x in rel_val if x]
        elif rel_val:
            rels = [str(rel_val).lower().strip()]
        else:
            return False
        
        return any(r == "canonical" for r in rels)
