"""
Relevance Threshold Filter
============================

Filters out search results below a minimum relevance score.
Uses cross-encoder to score query-result pairs.

SOTA Reference:
    - Nogueira & Cho (2019): Passage Re-ranking with BERT
    - Adaptive threshold based on score distribution

Author: SOTA Web Search Upgrade
Date: 2026-03-08
"""

import logging
from typing import List, Any, Optional
from ..base import FilterStrategy, SearchResult

logger = logging.getLogger(__name__)


class RelevanceFilter(FilterStrategy):
    """
    Filter search results by cross-encoder relevance score.
    
    Scores each result against the query using the cross-encoder
    and removes results below the threshold.
    
    Features:
    - Absolute threshold (remove results below min score)
    - Adaptive threshold (remove results < mean - 1*std)
    - Graceful degradation if reranker unavailable
    """
    
    def __init__(
        self,
        min_score: float = 0.1,
        adaptive: bool = True,
        min_results: int = 1,
    ) -> None:
        """
        Args:
            min_score: Absolute minimum CE score (0-1 range, default: 0.1)
            adaptive: Use adaptive threshold (mean - 1*std) instead of fixed
            min_results: Always keep at least this many results
        """
        self._min_score = min_score
        self._adaptive = adaptive
        self._min_results = min_results
        self._reranker: Optional[Any] = None
        self._query: str = ""  # Set before filter() call
    
    def set_query(self, query: str) -> None:
        """Set the current query for relevance scoring."""
        self._query = query
    
    def filter(self, results: List[SearchResult]) -> List[SearchResult]:
        """
        Filter results by relevance score.
        
        NOTE: This filter is a no-op if:
        - No query is set
        - Reranker is unavailable
        - Results are empty
        
        The orchestrator's _rerank_results already handles reranking;
        this filter adds an additional quality gate by removing 
        clearly irrelevant results.
        """
        if not results or not self._query:
            return results
        
        # Lazy-load reranker
        if self._reranker is None:
            try:
                from agent.reranker import get_reranker
                self._reranker = get_reranker()
            except ImportError:
                logger.debug("Reranker not available for relevance filtering")
                return results
        
        if not self._reranker or not self._reranker.is_available:
            return results
        
        # Score all results
        scored = []
        for r in results:
            text = f"{r.title}. {r.snippet}".strip()
            if not text or text == ".":
                scored.append((r, 0.0))
                continue
            
            try:
                reranked = self._reranker.rerank(
                    query=self._query,
                    passages=[{"text": text}],
                    text_key="text",
                )
                score = float(reranked[0].get("rerank_score", 0.0)) if reranked else 0.0
                scored.append((r, score))
            except Exception:
                scored.append((r, 0.0))
        
        if not scored:
            return results
        
        # Determine threshold
        scores = [s for _, s in scored]
        
        if self._adaptive and len(scores) >= 3:
            mean = sum(scores) / len(scores)
            variance = sum((s - mean) ** 2 for s in scores) / len(scores)
            std = variance ** 0.5
            threshold = max(self._min_score, mean - std)
            logger.debug(f"Adaptive threshold: {threshold:.3f} (mean={mean:.3f}, std={std:.3f})")
        else:
            threshold = self._min_score
        
        # Apply threshold
        filtered = [(r, s) for r, s in scored if s >= threshold]
        
        # Ensure minimum results
        if len(filtered) < self._min_results:
            # Keep top-N by score
            sorted_scored = sorted(scored, key=lambda x: x[1], reverse=True)
            filtered = sorted_scored[:self._min_results]
        
        removed = len(results) - len(filtered)
        if removed > 0:
            logger.info(
                f"Relevance filter: {len(results)} → {len(filtered)} "
                f"(-{removed}, threshold={threshold:.3f})"
            )
        
        return [r for r, _ in filtered]


__all__ = ["RelevanceFilter"]
