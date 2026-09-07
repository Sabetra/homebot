"""
HTML Parser V2 - Metadata Extractor

Extract OpenGraph and meta tags from HTML.
Target CC: <8
"""
import logging
from typing import Any, Dict, List

from html_parser.models import MetadataResult
from html_parser.extractors.canonical_url import CanonicalURLExtractor


class MetadataExtractor:
    """
    Extract metadata from HTML (canonical, OpenGraph, meta tags).
    
    Target CC: 7
    """
    
    def __init__(self) -> None:
        """Initialize with canonical URL extractor."""
        self.canonical_extractor = CanonicalURLExtractor()
    
    def extract(self, soup: Any, url: str) -> MetadataResult:
        """
        Extract all metadata from soup.
        
        Args:
            soup: BeautifulSoup object
            url: Base URL for canonical resolution
            
        Returns:
            MetadataResult with canonical, og, and meta tags
        """
        errors: List[str] = []
        
        # Extract canonical URL
        canonical = self._extract_canonical(soup, url, errors)
        
        # Extract og and meta tags
        og, meta = self._extract_tags(soup, errors)
        
        return MetadataResult(
            canonical_url=canonical,
            og=og,
            meta=meta,
            errors=errors
        )
    
    def _extract_canonical(self, soup: Any, url: str, errors: list) -> str | None:
        """Extract canonical URL with error handling."""
        try:
            return self.canonical_extractor.extract(soup, url)
        except Exception as e:
            errors.append(f"Canonical extraction error: {e}")
            return None
    
    def _extract_tags(self, soup: Any, errors: list) -> tuple[Dict[str, Any], Dict[str, Any]]:
        """Extract OpenGraph and meta tags."""
        og: Dict[str, Any] = {}
        meta: Dict[str, Any] = {}
        
        try:
            from bs4.element import Tag
            
            for m in soup.find_all("meta"):
                if not isinstance(m, Tag):
                    continue
                
                # Extract and validate key-value pair
                kv = self._extract_meta_kv(m)
                if not kv:
                    continue
                
                k, v = kv
                meta[k] = v
                
                # Also store in og dict if og:* tag
                if k.startswith("og:"):
                    og[k] = v
        
        except Exception as e:
            logging.debug(f"Meta tags extraction error: {e}")
            errors.append(f"Meta extraction error: {e}")
        
        return og, meta
    
    def _extract_meta_kv(self, tag: Any) -> tuple[str, str] | None:
        """Extract key-value pair from meta tag."""
        # Get key (property or name)
        k_raw = tag.get("property") or tag.get("name") or ""
        k = str(k_raw).lower().strip()
        
        # Get value (content)
        v_raw = tag.get("content") or ""
        v = str(v_raw).strip()
        
        if not k or not v:
            return None
        
        return k, v
