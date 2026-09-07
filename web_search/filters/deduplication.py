"""
Deduplication Filter
=====================

Removes duplicate search results based on URL normalization.

SOTA Reference:
    - URL canonicalization (strip www, utm params, fragments, trailing slash)
    - Content-hash based dedup for same content at different URLs

Author: SOTA Web Search Upgrade
Date: 2026-03-08
"""

import logging
import re
from typing import List, Set
from urllib.parse import urlparse, urlunparse, parse_qs, urlencode
from ..base import FilterStrategy, SearchResult

logger = logging.getLogger(__name__)


class DeduplicationFilter(FilterStrategy):
    """
    Remove duplicate search results based on normalized URLs.
    
    Normalization steps:
    1. Lowercase scheme and host
    2. Strip 'www.' prefix
    3. Remove UTM/tracking parameters
    4. Remove fragment (#)
    5. Remove trailing slash
    6. Normalize scheme to https
    
    Also detects near-duplicate snippets via Jaccard similarity.
    """
    
    # URL parameters to strip (tracking/analytics)
    _STRIP_PARAMS = frozenset([
        "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
        "fbclid", "gclid", "gclsrc", "dclid", "msclkid",
        "ref", "source", "mc_cid", "mc_eid",
        "pk_source", "pk_medium", "pk_campaign",
        "_ga", "_gl", "yclid",
    ])
    
    def __init__(self, snippet_similarity_threshold: float = 0.95) -> None:
        """
        Args:
            snippet_similarity_threshold: Jaccard similarity threshold for 
                near-duplicate snippet detection (0-1, default: 0.95).
                0.95 = only near-exact duplicates. Lower values (0.8) risk
                removing topically similar but distinct results.
        """
        self._snippet_threshold = snippet_similarity_threshold
    
    def filter(self, results: List[SearchResult]) -> List[SearchResult]:
        """
        Remove duplicate results.
        
        Args:
            results: Search results (may contain duplicates)
            
        Returns:
            Deduplicated results (order preserved, first occurrence kept)
        """
        if not results:
            return results
        
        seen_urls: Set[str] = set()
        seen_snippets: List[Set[str]] = []  # Tokenized snippets for Jaccard
        deduped: List[SearchResult] = []
        removed_count = 0
        
        for result in results:
            # URL-based dedup
            norm_url = self._normalize_url(result.url)
            if norm_url in seen_urls:
                removed_count += 1
                continue
            
            # Snippet-based near-dedup
            snippet_tokens = self._tokenize(result.snippet)
            if snippet_tokens and self._is_near_duplicate(snippet_tokens, seen_snippets):
                removed_count += 1
                continue
            
            seen_urls.add(norm_url)
            if snippet_tokens:
                seen_snippets.append(snippet_tokens)
            deduped.append(result)
        
        if removed_count > 0:
            logger.info(f"Deduplication: {len(results)} → {len(deduped)} (-{removed_count})")
        
        return deduped
    
    def _normalize_url(self, url: str) -> str:
        """
        Normalize URL for deduplication.
        
        Args:
            url: Raw URL
            
        Returns:
            Normalized URL string
        """
        if not url:
            return ""
        
        try:
            parsed = urlparse(url.strip())
            
            # Normalize scheme
            scheme = "https"
            
            # Normalize host
            host = (parsed.hostname or "").lower()
            if host.startswith("www."):
                host = host[4:]
            
            # Strip tracking params
            if parsed.query:
                params = parse_qs(parsed.query, keep_blank_values=False)
                clean_params = {
                    k: v for k, v in params.items()
                    if k.lower() not in self._STRIP_PARAMS
                }
                query = urlencode(clean_params, doseq=True) if clean_params else ""
            else:
                query = ""
            
            # Normalize path (strip trailing slash, but keep root "/")
            path = parsed.path.rstrip("/") or "/"
            
            # Reconstruct without fragment
            normalized = urlunparse((scheme, host, path, "", query, ""))
            return normalized
            
        except Exception:
            # Fallback: simple normalization
            return url.strip().rstrip("/").lower().replace("www.", "").replace("http://", "https://")
    
    def _tokenize(self, text: str) -> Set[str]:
        """Tokenize text for Jaccard similarity."""
        if not text:
            return set()
        # Simple word tokenization
        words = re.findall(r'\w+', text.lower())
        return set(words) if len(words) >= 3 else set()
    
    def _is_near_duplicate(
        self,
        tokens: Set[str],
        seen_list: List[Set[str]]
    ) -> bool:
        """
        Check if tokens are near-duplicate of any seen snippet.
        
        Uses Jaccard similarity (|A∩B| / |A∪B|).
        """
        for seen in seen_list:
            if not seen:
                continue
            intersection = len(tokens & seen)
            union = len(tokens | seen)
            if union > 0:
                similarity = intersection / union
                if similarity >= self._snippet_threshold:
                    return True
        return False


__all__ = ["DeduplicationFilter"]
