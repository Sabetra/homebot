"""
Answer-Focused Snippet Extraction
====================================

Extracts the most relevant passage from enriched HTML content
that directly answers the search query.

SOTA References:
    - Extractive QA: Rajpurkar et al. (2016) — SQuAD
    - Passage retrieval: Karpukhin et al. (2020) — DPR
    - Google's Featured Snippets algorithm concept

Instead of using the generic DDG snippet, this module:
1. Takes the full enriched HTML text
2. Splits it into sentences/passages  
3. Uses cross-encoder to find the best answering passage
4. Replaces the snippet with a more informative one

Author: SOTA Web Search Upgrade
Date: 2026-03-08
"""

import logging
import re
from typing import List, Optional, Any

logger = logging.getLogger(__name__)


class AnswerSnippetExtractor:
    """
    Extracts the most relevant passage from enriched content
    to create a high-quality answer snippet.
    
    Uses the cross-encoder reranker to score passage-query pairs.
    Falls back to longest-sentence heuristic if reranker unavailable.
    """
    
    def __init__(self) -> None:
        self._reranker: Optional[Any] = None
        self._reranker_checked = False
    
    def _ensure_reranker(self) -> bool:
        """Lazy-load reranker."""
        if not self._reranker_checked:
            self._reranker_checked = True
            try:
                from agent.reranker import get_reranker
                reranker = get_reranker()
                if reranker and reranker.is_available:
                    self._reranker = reranker
            except ImportError:
                pass
        return self._reranker is not None
    
    def _split_into_passages(self, text: str, max_passage_len: int = 300) -> List[str]:
        """
        Split text into sentence-based passages suitable for snippet extraction.
        
        Strategy:
        1. Split by sentence boundaries
        2. Merge short sentences into passages (~200-300 chars)
        3. Filter out boilerplate (too short, navigation text, etc.)
        
        Args:
            text: Full text content
            max_passage_len: Maximum passage length in characters
            
        Returns:
            List of passage strings
        """
        if not text or len(text) < 20:
            return []
        
        # Split on sentence boundaries
        sentences = re.split(r'(?<=[.!?])\s+', text)
        
        # Filter out garbage
        clean_sentences = []
        for s in sentences:
            s = s.strip()
            if len(s) < 15:  # Too short
                continue
            if len(s) > 1000:  # Too long — likely not a sentence
                # Try to split further on newlines
                for sub in s.split('\n'):
                    sub = sub.strip()
                    if 15 <= len(sub) <= 1000:
                        clean_sentences.append(sub)
                continue
            # Skip navigation/boilerplate patterns
            if re.match(r'^(Home|Menu|Menü|Navigation|Cookie|Accept|Impressum|Datenschutz|©)', s, re.I):
                continue
            clean_sentences.append(s)
        
        # Merge short sentences into passages
        passages = []
        current = ""
        for sent in clean_sentences:
            if len(current) + len(sent) + 1 <= max_passage_len:
                current = (current + " " + sent).strip()
            else:
                if current:
                    passages.append(current)
                current = sent
        if current:
            passages.append(current)
        
        return passages
    
    def extract_best_snippet(
        self,
        query: str,
        full_text: str,
        current_snippet: str = "",
        max_len: int = 300,
    ) -> str:
        """
        Extract the best answer-focused snippet from enriched content.
        
        Args:
            query: Original search query
            full_text: Full enriched text content
            current_snippet: Current snippet (DDG's default)
            max_len: Maximum snippet length
            
        Returns:
            Best snippet string (may be the original if nothing better found)
        """
        if not full_text or len(full_text) < 30:
            return current_snippet
        
        passages = self._split_into_passages(full_text, max_passage_len=max_len)
        
        if not passages:
            return current_snippet
        
        # If we have the reranker, use CE scoring
        if self._ensure_reranker() and self._reranker is not None:
            return self._ce_extract(query, passages, current_snippet)
        
        # Fallback: keyword overlap heuristic
        return self._keyword_extract(query, passages, current_snippet)
    
    def _ce_extract(
        self,
        query: str,
        passages: List[str],
        fallback: str,
    ) -> str:
        """
        Extract best snippet using cross-encoder scoring.
        
        Scores each passage against the query and returns the highest-scored one.
        """
        try:
            passage_dicts = [{"text": p} for p in passages[:20]]  # Limit for performance
            
            reranked = self._reranker.rerank(
                query=query,
                passages=passage_dicts,
                text_key="text",
            )
            
            if reranked:
                best = reranked[0]
                score = float(best.get("rerank_score", 0.0))
                text = best.get("text", "")
                
                # Use if at least marginally relevant
                if score > 0.05 and text:
                    logger.debug(
                        f"CE snippet extraction: score={score:.3f}, "
                        f"len={len(text)}"
                    )
                    return text
            
            return fallback
            
        except Exception as e:
            logger.debug(f"CE snippet extraction failed: {e}")
            return fallback
    
    def _keyword_extract(
        self,
        query: str,
        passages: List[str],
        fallback: str,
    ) -> str:
        """
        Extract best snippet using keyword overlap heuristic.
        
        Scores passages by how many query keywords they contain.
        """
        query_words = set(
            w.lower() for w in re.findall(r'\w+', query)
            if len(w) > 2
        )
        
        if not query_words:
            return fallback
        
        best_passage = fallback
        best_overlap = 0
        
        for passage in passages:
            passage_words = set(w.lower() for w in re.findall(r'\w+', passage))
            overlap = len(query_words & passage_words)
            
            # Normalize by query length
            score = overlap / len(query_words)
            
            if score > best_overlap:
                best_overlap = score
                best_passage = passage
        
        # Only replace if significantly better overlap
        if best_overlap > 0.3 and best_passage != fallback:
            logger.debug(
                f"Keyword snippet extraction: overlap={best_overlap:.2f}, "
                f"len={len(best_passage)}"
            )
            return best_passage
        
        return fallback


# Singleton
_extractor: Optional[AnswerSnippetExtractor] = None


def get_snippet_extractor() -> AnswerSnippetExtractor:
    """Get or create singleton AnswerSnippetExtractor."""
    global _extractor
    if _extractor is None:
        _extractor = AnswerSnippetExtractor()
    return _extractor


__all__ = ["AnswerSnippetExtractor", "get_snippet_extractor"]
