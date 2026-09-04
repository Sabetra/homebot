"""
HTML Parser V2 - Noisy Tags Cleanup

Remove noisy HTML elements (script, style, etc.) with safe fallback.
Target CC: <6
"""
import logging
from typing import Any

from html_parser.models import CleanupResult


class NoisyTagsCleanup:
    """
    Remove noisy tags from HTML soup.
    
    Removes: script, style, noscript, template, iframe, svg
    Strategy: decompose() with extract() fallback
    Target CC: 5
    """
    
    NOISY_TAGS = ["script", "style", "noscript", "template", "iframe", "svg"]
    
    def cleanup(self, html: str, url: str) -> CleanupResult:
        """
        Clean up HTML by removing noisy tags.
        
        Args:
            html: Raw HTML string
            url: Original URL (for error logging)
            
        Returns:
            CleanupResult with cleaned soup
        """
        try:
            from bs4 import BeautifulSoup
            
            soup = BeautifulSoup(html or "", "html.parser")
            removed = 0
            errors = []
            
            # Remove each noisy tag
            for tag in soup(self.NOISY_TAGS):
                if self._safe_remove(tag, url):
                    removed += 1
                else:
                    errors.append(f"Failed to remove {tag.name} tag")
            
            return CleanupResult(soup=soup, removed_tags=removed, errors=errors)
            
        except Exception as e:
            logging.error(f"HTML cleanup failed for {url}: {e}")
            # Return minimal soup
            from bs4 import BeautifulSoup
            return CleanupResult(
                soup=BeautifulSoup("", "html.parser"),
                removed_tags=0,
                errors=[f"Cleanup error: {e}"]
            )
    
    def _safe_remove(self, tag: Any, url: str) -> bool:
        """Safely remove tag with fallback strategies."""
        # Try decompose() first
        try:
            tag.decompose()
            return True
        except AttributeError:
            pass
        
        # Fallback: extract()
        try:
            tag.extract()
            return True
        except Exception as e:
            logging.debug(f"Tag removal failed for {url}: {e}")
            return False
