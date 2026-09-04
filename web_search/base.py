"""
Web Search Base Classes
========================

Abstract base classes for modular web search system.

Author: Phase 2 Tech-Debt Cleanup
Date: 2026-02-13
"""

from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional, Protocol, runtime_checkable
from dataclasses import dataclass
from pydantic import BaseModel, Field


# ============================================================================
# DATA MODELS
# ============================================================================

@dataclass
class SearchParams:
    """
    Search parameters (dataclass for performance).
    
    Using dataclass instead of Pydantic for params
    since validation is handled in orchestrator.
    """
    query: str
    num_results: int = 3
    region: str = "de-de"
    timelimit: Optional[str] = None  # d, w, m, y
    safesearch: str = "Moderate"  # On|Moderate|Off
    timeout: int = 6


class SearchResult(BaseModel):
    """
    Single search result with Pydantic validation.
    """
    title: str = Field(description="Result title")
    url: str = Field(description="Result URL")
    snippet: str = Field(description="Result snippet/description")
    date: Optional[str] = Field(default=None, description="Publication date (if available)")
    metadata: Dict[str, Any] = Field(
        default_factory=dict,
        description="Additional metadata (OG tags, canonical URL, etc.)"
    )
    probe_status: Optional[int] = Field(
        default=None,
        description="HTTP status from probe (HEAD request)"
    )
    probe_error_class: Optional[str] = Field(
        default=None,
        description="Error class if probe failed"
    )
    filtered_reason: Optional[str] = Field(
        default=None,
        description="Reason if result was filtered"
    )
    retry_at_unix: Optional[float] = Field(
        default=None,
        description="Unix timestamp when URL can be retried"
    )
    enrich_error: Optional[str] = Field(
        default=None,
        description="Error message if enrichment failed"
    )
    
    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "title": "Example Article",
                    "url": "https://example.com/article",
                    "snippet": "This is a sample article...",
                    "date": "2026-02-13",
                    "metadata": {
                        "domain": "example.com",
                        "content_length": 1500
                    }
                }
            ]
        }
    }


class SearchResponse(BaseModel):
    """
    Complete search response with Pydantic validation.
    """
    success: bool = Field(description="Whether search was successful")
    query: str = Field(description="Original query")
    results: List[SearchResult] = Field(
        default_factory=list,
        description="List of search results"
    )
    message: str = Field(description="Status message")
    error: Optional[str] = Field(
        default=None,
        description="Error message if search failed"
    )


# ============================================================================
# ABSTRACT BASE CLASSES
# ============================================================================

class SearchStrategy(ABC):
    """
    Abstract base class for search engine strategies.
    
    Implement this to add new search engines (Brave, Google, etc.)
    """
    
    @abstractmethod
    def search(self, params: SearchParams) -> List[SearchResult]:
        """
        Execute search and return results.
        
        Args:
            params: Search parameters
            
        Returns:
            List of search results
            
        Raises:
            RuntimeError: If search engine is not available
            Exception: For other search errors
        """
        pass
    
    @abstractmethod
    def is_available(self) -> bool:
        """
        Check if this search strategy is available.
        
        Returns:
            True if strategy can be used, False otherwise
        """
        pass
    
    @property
    def name(self) -> str:
        """Strategy name for logging"""
        return self.__class__.__name__


class EnrichmentStrategy(ABC):
    """
    Abstract base class for content enrichment strategies.
    
    Enrichment adds additional data to search results
    (e.g., full content, AI summaries, metadata).
    """
    
    @abstractmethod
    def enrich(
        self,
        result: SearchResult,
        timeout: int,
        **kwargs: Any
    ) -> SearchResult:
        """
        Enrich a search result with additional content.
        
        Args:
            result: Original search result
            timeout: HTTP timeout in seconds
            **kwargs: Additional parameters
            
        Returns:
            Enriched search result
            
        Raises:
            Exception: If enrichment fails
        """
        pass
    
    @property
    def name(self) -> str:
        """Strategy name for logging"""
        return self.__class__.__name__


class FilterStrategy(ABC):
    """
    Abstract base class for result filtering strategies.
    
    Filters remove unwanted results based on criteria
    (e.g., blacklist, privacy, quality).
    """
    
    @abstractmethod
    def filter(self, results: List[SearchResult]) -> List[SearchResult]:
        """
        Filter results based on strategy.
        
        Args:
            results: Original search results
            
        Returns:
            Filtered search results
        """
        pass
    
    @property
    def name(self) -> str:
        """Strategy name for logging"""
        return self.__class__.__name__


@runtime_checkable
class QueryAwareFilter(Protocol):
    """Structural contract for filters that need query context."""

    def set_query(self, query: str) -> None:
        """Provide the active query before filtering."""
        ...


# ============================================================================
# EXPORTS
# ============================================================================

__all__ = [
    # Data Models
    "SearchParams",
    "SearchResult",
    "SearchResponse",
    
    # Abstract Base Classes
    "SearchStrategy",
    "EnrichmentStrategy",
    "FilterStrategy",
    "QueryAwareFilter",
]
