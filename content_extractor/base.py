"""
Content Extractor V2 - Base Classes and Protocols

Base classes and protocols for the content extraction system.
"""
from abc import ABC, abstractmethod
from typing import Protocol, runtime_checkable

from .models import ExtractionContext, ExtractionResult


@runtime_checkable
class ContentExtractor(Protocol):
    """Protocol for content extraction strategies."""
    
    @property
    def name(self) -> str:
        """Unique name of the extraction strategy."""
        ...
    
    @property
    def priority(self) -> int:
        """Priority level (lower = higher priority)."""
        ...
    
    def can_handle(self, context: ExtractionContext) -> bool:
        """Check if this strategy can handle the given context."""
        ...
    
    def extract(self, context: ExtractionContext) -> ExtractionResult:
        """Extract content from the given context."""
        ...


class BaseContentExtractor(ABC):
    """Base class for content extraction strategies."""
    
    def __init__(self, name: str, priority: int = 50):
        """
        Initialize base extractor.
        
        Args:
            name: Unique name of the extraction strategy
            priority: Priority level (lower = higher priority, default: 50)
        """
        self._name = name
        self._priority = priority
    
    @property
    def name(self) -> str:
        """Unique name of the extraction strategy."""
        return self._name
    
    @property
    def priority(self) -> int:
        """Priority level (lower = higher priority)."""
        return self._priority
    
    def can_handle(self, context: ExtractionContext) -> bool:
        """
        Check if this strategy can handle the given context.
        
        Default implementation returns True (all strategies try all URLs).
        Override for specific domain/URL filtering.
        """
        return True
    
    @abstractmethod
    def extract(self, context: ExtractionContext) -> ExtractionResult:
        """
        Extract content from the given context.
        
        Args:
            context: Extraction context with URL and options
            
        Returns:
            ExtractionResult with extracted data or error information
        """
        pass
