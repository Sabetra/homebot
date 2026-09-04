"""
Web Search Orchestrator (SOTA v2)
===================================

Main coordinator for web search with strategy fallback,
filtering, cross-encoder reranking, enrichment, caching,
query expansion, and rate limiting.

References:
    - Nogueira & Cho (2019): Passage Re-ranking with BERT
    - Raudaschl (2023): RAG-Fusion Multi-Query
    - Cormack et al. (2009): Reciprocal Rank Fusion

Author: Phase 2 Tech-Debt Cleanup + SOTA Upgrade v2
Date: 2026
"""

import logging
import os
from typing import List, Dict, Any, Optional, Sequence, Callable
from .base import (
    SearchParams,
    SearchResult,
    SearchStrategy,
    EnrichmentStrategy,
    FilterStrategy,
    QueryAwareFilter,
)
from .cache import get_search_cache
from .rate_limiter import get_rate_limiter
from .filters.deduplication import DeduplicationFilter
from .filters.source_diversity import SourceDiversityFilter
from .filters.freshness import FreshnessBoostFilter

# Cross-Encoder Reranker (SOTA)
_RERANKER_AVAILABLE = False
_get_reranker = None
try:
    from agent.reranker import get_reranker as _get_reranker  # type: ignore
    _RERANKER_AVAILABLE = True
except ImportError:
    try:
        from reranker import get_reranker as _get_reranker  # type: ignore
        _RERANKER_AVAILABLE = True
    except ImportError:
        pass

# Query Expansion (optional -- only used if model_loader is provided)
_QUERY_EXPANSION_AVAILABLE = False
_QueryExpanderCls: Optional[type[Any]] = None
_reciprocal_rank_fusion_fn: Optional[
    Callable[[List[List[dict]], int, str], List[dict]]
] = None
try:
    from .query_expansion import (
        QueryExpander as _ImportedQueryExpander,
        reciprocal_rank_fusion as _imported_reciprocal_rank_fusion,
    )
    _QueryExpanderCls = _ImportedQueryExpander
    _reciprocal_rank_fusion_fn = _imported_reciprocal_rank_fusion
    _QUERY_EXPANSION_AVAILABLE = True
except ImportError:
    pass

logger = logging.getLogger(__name__)


class WebSearchOrchestrator:
    """
    Orchestrates web search with multiple strategies, filters, and enrichment.
    
    Features (SOTA v2):
    - Strategy fallback (try multiple search engines)
    - Query sanitization (privacy)
    - Query expansion with RRF merging (RAG-Fusion style)
    - Result caching with TTL (30min general, 5min news)
    - URL + snippet deduplication
    - Relevance-based filtering
    - Cross-encoder reranking
    - Content enrichment (HTML, AI)
    - Rate limiting for API protection
    - Backward-compatible Dict API
    """
    
    def __init__(
        self,
        strategies: Optional[Sequence[SearchStrategy]] = None,
        enrichment: Optional[EnrichmentStrategy] = None,
        filters: Optional[Sequence[FilterStrategy]] = None
    ) -> None:
        """
        Initialize orchestrator.
        
        Args:
            strategies: List of search strategies (fallback order)
            enrichment: Enrichment strategy (optional)
            filters: List of filter strategies (optional)
        """
        self.strategies = list(strategies) if strategies else []
        self.enrichment = enrichment
        self.filters = list(filters) if filters else []
        self.privacy_handler = None
        self.language_detector = None
        self.model_loader = None  # For query expansion
        
        # SOTA v2: Add deduplication filter if not already present
        has_dedup = any(isinstance(f, DeduplicationFilter) for f in self.filters)
        if not has_dedup:
            self.filters.append(DeduplicationFilter())
        
        # SOTA v2.1: Add source diversity filter
        has_diversity = any(isinstance(f, SourceDiversityFilter) for f in self.filters)
        if not has_diversity:
            self.filters.append(SourceDiversityFilter(max_per_domain=3))
        
        # SOTA v2.1: Add freshness boost filter (query-aware, set before search)
        has_freshness = any(isinstance(f, FreshnessBoostFilter) for f in self.filters)
        if not has_freshness:
            self.filters.append(FreshnessBoostFilter())
        
        # SOTA v2.1: Answer snippet extractor (lazy init)
        self._snippet_extractor = None
        
        # SOTA v2: Cache and rate limiter (singletons)
        self._cache = get_search_cache()
        self._rate_limiter = get_rate_limiter()
        
        # SOTA v2: Query expander (lazy init, needs model_loader)
        self._query_expander: Optional[Any] = None
        
        logger.debug(
            f"Orchestrator v2 initialized: {len(self.strategies)} strategies, "
            f"{len(self.filters)} filters, "
            f"enrichment={'Yes' if enrichment else 'No'}, "
            f"cache={'Yes'}, rate_limit={'Yes'}"
        )
    
    def set_privacy_handler(self, handler: Any) -> None:
        """Set privacy handler for query sanitization"""
        self.privacy_handler = handler
        logger.debug("Privacy handler configured")
    
    def set_language_detector(self, detector: Any) -> None:
        """Set language detector for query analysis"""
        self.language_detector = detector
        logger.debug("Language detector configured")
    
    def set_model_loader(self, model_loader: Any) -> None:
        """Set model loader for LLM-based query expansion."""
        self.model_loader = model_loader
        if _QUERY_EXPANSION_AVAILABLE and _QueryExpanderCls is not None:
            self._query_expander = _QueryExpanderCls(model_loader=model_loader)
            logger.info("✅ Query Expander initialized (LLM-based)")
        else:
            logger.debug("Query expansion module not available")
    
    def search(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """
        Execute web search with given parameters.
        
        SOTA v2.1 Pipeline:
        1. Parse and validate params
        2. Check cache (return immediately on hit)
        3. Sanitize query (privacy)
        4. Set query context on filters (freshness, relevance)
        5. Rate limit check
        6. Query expansion (generate alternative queries)
        7. Execute search with fallback (for each query variant)
        8. Merge results via RRF (if expanded)
        9. Filter results (blacklist, dedup, diversity, freshness)
        10. Cross-Encoder Reranking
        11. Enrich results (default: on) + snippet extraction
        12. Cache results
        13. Build response
        
        Args:
            params: Search parameters dict (legacy format)
            
        Returns:
            Search response dict (legacy format)
        """
        try:
            # 1. Parse and validate params
            search_params = self._parse_params(params)
            logger.debug(f"Search initiated: '{search_params.query[:50]}'")
            
            # 2. SOTA: Check cache
            cached = self._cache.get(search_params.query, search_params.num_results)
            if cached is not None:
                logger.info(f"⚡ Cache HIT for: '{search_params.query[:50]}' "
                           f"(age: {cached.get('_cache_age_s', 0):.0f}s)")
                return cached
            
            # 3. Sanitize query (privacy)
            search_params = self._sanitize_query(search_params)
            
            # 4. SOTA v2.1: Set query context on query-aware filters
            for f in self.filters:
                if isinstance(f, QueryAwareFilter):
                    f.set_query(search_params.query)
            
            # 5. SOTA: Rate limiting
            if not self._rate_limiter.acquire(timeout=15.0):
                logger.warning("Rate limit timeout -- proceeding anyway")
            
            # 6+7. SOTA: Query expansion + search
            use_expansion = (
                params.get("expand", True)  # Can be disabled per-call
                and self._query_expander is not None
                and len(search_params.query) >= 10  # Only for non-trivial queries
            )
            
            if use_expansion:
                results = self._search_with_expansion(search_params)
            else:
                # Standard single-query search
                results = self._execute_search_with_fallback(search_params)
            
            # 8. Filter results (blacklist, dedup, diversity, freshness)
            results = self._apply_filters(results)
            
            # 9. SOTA: Cross-Encoder Reranking
            results = self._rerank_results(search_params.query, results)
            
            # 10. Enrich results + SOTA v2.1: snippet extraction
            # SOTA: Always enrich by default for maximum snippet quality.
            # Raw DDG snippets are often too short/generic → CE scores suffer.
            # Caller can disable with enrich=False for speed-critical cases.
            do_enrich = params.get("enrich", True)
            if self.enrichment and do_enrich:
                fetch_top = params.get("fetch_top", 5)
                results = self._enrich_results(
                    results,
                    fetch_top=fetch_top,
                    timeout=search_params.timeout,
                    query=search_params.query
                )
                # SOTA v2.1: Extract answer-focused snippets from enriched content
                results = self._extract_answer_snippets(search_params.query, results)
            
            # 11. Build response (legacy format)
            response = self._build_legacy_response(search_params, results)
            
            # 12. SOTA: Cache successful results
            self._cache.store(search_params.query, search_params.num_results, response)
            
            return response
            
        except ValueError as e:
            logger.error(f"Validation error: {e}")
            return {
                "success": False,
                "query": params.get("query", ""),
                "results": [],
                "error": str(e)
            }
        except Exception as e:
            logger.error(f"Search failed: {type(e).__name__}: {e}")
            return {
                "success": False,
                "query": params.get("query", ""),
                "results": [],
                "error": str(e)
            }
    
    def _parse_params(self, params: Dict[str, Any]) -> SearchParams:
        """
        Parse and validate search parameters.
        
        Args:
            params: Raw parameters dict
            
        Returns:
            Validated SearchParams
            
        Raises:
            ValueError: If parameters are invalid
        """
        query = (params.get("query") or "").strip()
        
        if not query:
            raise ValueError("Empty search query")
        
        # Validate query length
        if len(query) > 1000:
            logger.error(
                f"🚨 PRIVACY ERROR: Query too long ({len(query)} chars)! "
                "Possibly entire prompt was passed!"
            )
            query = query[:200]
            logger.warning(f"🔧 Query truncated to: '{query}'")
        
        # Detect system prompts in query
        system_markers = ["SYSTEM:", "ASSISTANT:", "USER:", "CONTEXT:"]
        if any(marker in query for marker in system_markers):
            logger.error("🚨 PRIVACY ERROR: System prompt markers in query!")
            raise ValueError("Invalid query (contains system prompts)")
        
        # Parse num_results with validation
        try:
            num_results = max(1, int(params.get("num_results", 3)))
        except (ValueError, TypeError):
            logger.debug("Invalid num_results, using default (3)")
            num_results = 3
        
        # Parse timeout with validation
        try:
            timeout = max(2, int(params.get("timeout", os.getenv("WEB_FETCH_TIMEOUT", "6"))))
        except (ValueError, TypeError):
            logger.debug("Invalid timeout, using default (6)")
            timeout = 6
        
        return SearchParams(
            query=query,
            num_results=num_results,
            region=(params.get("region") or "de-de").lower(),
            timelimit=params.get("timelimit"),
            safesearch=params.get("safesearch") or "Moderate",
            timeout=timeout
        )
    
    def _sanitize_query(self, params: SearchParams) -> SearchParams:
        """
        Sanitize query using privacy handler.
        
        Args:
            params: Original search parameters
            
        Returns:
            Search parameters with sanitized query
        """
        if self.privacy_handler is None:
            logger.warning("⚠️ Privacy handler not initialized - query not sanitized")
            return params
        
        original_query = params.query
        
        try:
            sanitized_query = self.privacy_handler.extract_safe_query_for_web_search(
                original_query
            )
            
            if sanitized_query != original_query:
                logger.info(
                    f"🔒 Query sanitized: '{original_query[:50]}...' → '{sanitized_query[:50]}...'"
                )
                params.query = sanitized_query
        except Exception as e:
            logger.warning(f"Query sanitization failed: {e}")
        
        return params
    
    def _search_with_expansion(
        self,
        params: SearchParams
    ) -> List[SearchResult]:
        """
        SOTA: Execute search with query expansion and RRF merging.
        
        1. Generate 2 alternative query formulations
        2. Search for original + expanded queries
        3. Merge results via Reciprocal Rank Fusion (RRF)
        
        Args:
            params: Search parameters (original query)
            
        Returns:
            Merged and RRF-ranked results
        """
        if self._query_expander is None or _reciprocal_rank_fusion_fn is None:
            return self._execute_search_with_fallback(params)
        
        # Generate expanded queries
        try:
            expanded = self._query_expander.expand(params.query, max_expansions=2)
        except Exception as e:
            logger.warning(f"Query expansion failed: {e}")
            expanded = []
        
        # Search original query
        original_results = self._execute_search_with_fallback(params)
        
        if not expanded:
            return original_results
        
        logger.info(f"🔄 Query expansion: +{len(expanded)} variants for '{params.query[:40]}'")
        
        # Search expanded queries
        all_result_lists = [original_results]
        
        for exp_query in expanded:
            # Rate limit between expansion queries
            self._rate_limiter.acquire(timeout=5.0)
            
            exp_params = SearchParams(
                query=exp_query,
                num_results=params.num_results,
                region=params.region,
                timelimit=params.timelimit,
                safesearch=params.safesearch,
                timeout=params.timeout,
            )
            
            try:
                exp_results = self._execute_search_with_fallback(exp_params)
                if exp_results:
                    all_result_lists.append(exp_results)
                    logger.debug(f"  Expansion '{exp_query[:40]}': {len(exp_results)} results")
            except Exception as e:
                logger.debug(f"  Expansion '{exp_query[:40]}' failed: {e}")
        
        # Merge via RRF
        if len(all_result_lists) <= 1:
            return original_results
        
        # Convert SearchResult to dicts for RRF
        dict_lists = []
        for result_list in all_result_lists:
            dict_lists.append([r.model_dump() for r in result_list])
        
        fused_dicts = _reciprocal_rank_fusion_fn(dict_lists, 60, "url")
        
        # Convert back to SearchResult
        fused_results = []
        for d in fused_dicts:
            try:
                fused_results.append(SearchResult(**{
                    k: v for k, v in d.items()
                    if k in SearchResult.model_fields
                }))
            except Exception:
                continue
        
        logger.info(
            f"🔀 RRF merged: {sum(len(l) for l in all_result_lists)} total → "
            f"{len(fused_results)} unique results"
        )
        
        return fused_results
    
    def _execute_search_with_fallback(
        self,
        params: SearchParams
    ) -> List[SearchResult]:
        """
        Execute search with strategy fallback.
        
        Tries each strategy in order until one succeeds.
        
        Args:
            params: Search parameters
            
        Returns:
            List of search results (may be empty)
        """
        if not self.strategies:
            logger.warning("No search strategies configured!")
            return []
        
        last_error = None
        
        for strategy in self.strategies:
            # Skip unavailable strategies
            if not strategy.is_available():
                logger.debug(f"Strategy {strategy.name} not available, skipping")
                continue
            
            try:
                logger.debug(f"Trying strategy: {strategy.name}")
                results = strategy.search(params)
                
                if results:
                    logger.info(
                        f"✅ {strategy.name} returned {len(results)} results for '{params.query[:50]}'"
                    )
                    return results
                else:
                    logger.debug(f"{strategy.name} returned no results")
                    
            except Exception as e:
                logger.warning(f"{strategy.name} failed: {type(e).__name__}: {e}")
                last_error = str(e)
                continue
        
        # All strategies failed or returned no results
        if last_error:
            logger.error(f"All search strategies failed. Last error: {last_error}")
        else:
            logger.warning("All strategies returned no results")
        
        return []
    
    def _apply_filters(self, results: List[SearchResult]) -> List[SearchResult]:
        """
        Apply all filter strategies to results.
        
        Args:
            results: Original search results
            
        Returns:
            Filtered search results
        """
        if not self.filters:
            return results
        
        filtered = results
        original_count = len(results)
        
        for filter_strategy in self.filters:
            before_count = len(filtered)
            filtered = filter_strategy.filter(filtered)
            after_count = len(filtered)
            
            if after_count < before_count:
                logger.debug(
                    f"{filter_strategy.name} filtered {before_count - after_count} results "
                    f"({after_count} remaining)"
                )
        
        if len(filtered) < original_count:
            logger.info(
                f"Filtering: {original_count} → {len(filtered)} results "
                f"({original_count - len(filtered)} filtered)"
            )
        
        return filtered
    
    def _enrich_results(
        self,
        results: List[SearchResult],
        fetch_top: int,
        timeout: int,
        query: str
    ) -> List[SearchResult]:
        """
        Enrich top-N results with additional content using concurrent fetching.
        
        SOTA: Uses ThreadPoolExecutor for parallel HTTP fetches (~3-4x faster
        than sequential for 5 results).
        
        Args:
            results: Search results
            fetch_top: Number of results to enrich
            timeout: HTTP timeout in seconds
            query: Original search query
            
        Returns:
            Enriched search results
        """
        if not self.enrichment:
            logger.debug("No enrichment strategy configured")
            return results
        
        if not results:
            return results
        
        # Determine how many to enrich
        to_enrich_count = min(fetch_top, len(results))
        if to_enrich_count <= 0:
            return results

        to_enrich = results[:to_enrich_count]
        remaining = results[to_enrich_count:]
        enrichment = self.enrichment
        if enrichment is None:
            return results
        
        logger.debug(f"Enriching top {to_enrich_count} results (concurrent)")
        
        # Detect language once for all results
        accept_language = None
        if self.language_detector:
            try:
                accept_language = self.language_detector(query)
            except Exception as e:
                logger.debug(f"Language detection failed: {e}")
        
        def _enrich_single(result: SearchResult) -> SearchResult:
            """Enrich a single result (thread-safe)."""
            try:
                return enrichment.enrich(
                    result,
                    timeout=timeout,
                    query=query,
                    accept_language=accept_language
                )
            except Exception as e:
                logger.warning(
                    f"Enrichment failed for {result.url}: {type(e).__name__}: {e}"
                )
                result.enrich_error = str(e)
                return result
        
        # Concurrent enrichment: parallel HTTP fetches
        from concurrent.futures import ThreadPoolExecutor, as_completed
        enriched_by_index: Dict[int, SearchResult] = {}
        
        with ThreadPoolExecutor(max_workers=min(5, to_enrich_count)) as executor:
            future_to_idx = {
                executor.submit(_enrich_single, result): i
                for i, result in enumerate(to_enrich)
            }
            for future in as_completed(future_to_idx):
                idx = future_to_idx[future]
                try:
                    enriched_by_index[idx] = future.result()
                except Exception as e:
                    logger.warning(f"Enrichment future failed: {e}")
                    enriched_by_index[idx] = to_enrich[idx]
        
        # Combine enriched + remaining (preserve order)
        enriched = [enriched_by_index.get(i, to_enrich[i]) for i in range(to_enrich_count)]
        return enriched + remaining
    
    def _extract_answer_snippets(
        self,
        query: str,
        results: List[SearchResult],
    ) -> List[SearchResult]:
        """
        SOTA v2.1: Extract answer-focused snippets from enriched content.
        
        For each enriched result that has full content in metadata,
        use the AnswerSnippetExtractor to find the best passage
        that directly answers the query.
        
        Args:
            query: Original search query
            results: Enriched search results
            
        Returns:
            Results with improved snippets
        """
        if not results:
            return results
        
        # Lazy init snippet extractor
        if self._snippet_extractor is None:
            try:
                from .enrichment.snippet_extractor import get_snippet_extractor
                self._snippet_extractor = get_snippet_extractor()
            except ImportError:
                logger.debug("Snippet extractor module not available")
                return results
        
        improved = 0
        for result in results:
            # Get full text from enrichment metadata
            full_text = (result.metadata or {}).get("full_text", "")
            if not full_text or len(full_text) < 50:
                continue
            
            original_snippet = result.snippet
            better_snippet = self._snippet_extractor.extract_best_snippet(
                query=query,
                full_text=full_text,
                current_snippet=original_snippet,
                max_len=300,
            )
            
            if better_snippet and better_snippet != original_snippet:
                result.snippet = better_snippet
                improved += 1
        
        if improved:
            logger.info(f"📝 Answer snippets improved: {improved}/{len(results)}")
        
        return results
    
    def _rerank_results(
        self,
        query: str,
        results: List[SearchResult],
    ) -> List[SearchResult]:
        """
        SOTA: Rerank search results using cross-encoder.
        
        Combines title + snippet as passage text and scores against query
        for much more accurate relevance ordering than search-engine ranking.
        
        Args:
            query: Original search query
            results: Filtered search results
            
        Returns:
            Reranked results (or original if reranker unavailable)
        """
        if not results or not _RERANKER_AVAILABLE or not _get_reranker:
            return results
        
        try:
            reranker = _get_reranker()
            if not reranker or not reranker.is_available:
                return results
            
            # Convert SearchResult models to dicts for reranker
            result_dicts = []
            for r in results:
                d = r.model_dump()
                d["_rerank_text"] = f"{r.title}. {r.snippet}".strip()
                result_dicts.append(d)
            
            # Rerank
            reranked_dicts = reranker.rerank(
                query=query,
                passages=result_dicts,
                text_key="_rerank_text",
            )
            
            # Convert back to SearchResult models
            reranked_results = []
            for d in reranked_dicts:
                d.pop("_rerank_text", None)
                d.pop("rerank_score", None)
                d.pop("original_score", None)
                d.pop("score", None)
                try:
                    reranked_results.append(SearchResult(**{
                        k: v for k, v in d.items()
                        if k in SearchResult.model_fields
                    }))
                except Exception:
                    # If reconstruction fails, skip
                    continue
            
            if reranked_results:
                logger.debug(
                    f"🏆 Reranked {len(results)} web results for '{query[:50]}'"
                )
                return reranked_results
            
            return results
            
        except Exception as e:
            logger.warning(f"⚠️ Web reranking failed: {e}")
            return results
    
    def _build_legacy_response(
        self,
        params: SearchParams,
        results: List[SearchResult]
    ) -> Dict[str, Any]:
        """
        Build legacy Dict response format for backward compatibility.
        
        Args:
            params: Search parameters
            results: Search results
            
        Returns:
            Response dict (legacy format)
        """
        if not results:
            return {
                "success": True,
                "query": params.query,
                "results": [],
                "message": f"No results for '{params.query}' (all filtered)",
                "error": None
            }
        
        # Limit results to requested number
        limited_results = results[:params.num_results]
        
        return {
            "success": True,
            "query": params.query,
            "results": [r.model_dump() for r in limited_results],
            "message": f"Found: {len(limited_results)} results for '{params.query}'"
        }


__all__ = ["WebSearchOrchestrator"]
