"""
Web Search Module
=================

Modular web search system with pluggable strategies.

Author: Phase 2 Tech-Debt Cleanup
Date: 2026-02-13
"""

from .base import (
    SearchParams,
    SearchResult,
    SearchResponse,
    SearchStrategy,
    EnrichmentStrategy,
    FilterStrategy,
)
from .strategies import DuckDuckGoStrategy
from .filters import (
    BlacklistFilter, PrivacyFilter, DeduplicationFilter,
    RelevanceFilter, SourceDiversityFilter, FreshnessBoostFilter,
)
from .enrichment import HTMLEnrichment, AIEnrichment, AnswerSnippetExtractor, get_snippet_extractor
from .orchestrator import WebSearchOrchestrator
from .cache import SearchCache, get_search_cache
from .query_expansion import QueryExpander, reciprocal_rank_fusion
from .rate_limiter import RateLimiter, get_rate_limiter

__version__ = "2.1.0"  # SOTA Upgrade 2026-03-08 (diversity, freshness, snippet extraction)

__all__ = [
    # Base Classes
    "SearchParams",
    "SearchResult",
    "SearchResponse",
    "SearchStrategy",
    "EnrichmentStrategy",
    "FilterStrategy",
    
    # Strategies
    "DuckDuckGoStrategy",
    
    # Filters
    "BlacklistFilter",
    "PrivacyFilter",
    "DeduplicationFilter",
    "RelevanceFilter",
    "SourceDiversityFilter",
    "FreshnessBoostFilter",
    
    # Enrichment
    "HTMLEnrichment",
    "AIEnrichment",
    "AnswerSnippetExtractor",
    "get_snippet_extractor",
    
    # Orchestrator
    "WebSearchOrchestrator",
    
    # SOTA: Caching, Query Expansion, Rate Limiting
    "SearchCache",
    "get_search_cache",
    "QueryExpander",
    "reciprocal_rank_fusion",
    "RateLimiter",
    "get_rate_limiter",
]
