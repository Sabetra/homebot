"""
User Context Builder V2 - Base Classes and Protocols

Base classes and protocols for the user context building system.
"""
from abc import ABC, abstractmethod
from typing import Protocol, runtime_checkable, Any, Optional

from .models import UserContextRequest


@runtime_checkable
class ContextProvider(Protocol):
    """Protocol for context data providers."""
    
    @property
    def name(self) -> str:
        """Unique name of the provider."""
        ...
    
    @property
    def priority(self) -> int:
        """Priority level (lower = higher priority)."""
        ...
    
    def can_handle(self, request: UserContextRequest) -> bool:
        """Check if this provider can handle the given request."""
        ...
    
    def provide(self, request: UserContextRequest, session_manager: Any) -> Optional[Any]:
        """Provide context data for the request."""
        ...


class BaseContextProvider(ABC):
    """Base class for context data providers."""
    
    def __init__(self, name: str, priority: int = 50):
        """
        Initialize base provider.
        
        Args:
            name: Unique name of the provider
            priority: Priority level (lower = higher priority, default: 50)
        """
        self._name = name
        self._priority = priority
    
    @property
    def name(self) -> str:
        """Unique name of the provider."""
        return self._name
    
    @property
    def priority(self) -> int:
        """Priority level (lower = higher priority)."""
        return self._priority
    
    def can_handle(self, request: UserContextRequest) -> bool:
        """
        Check if this provider can handle the given request.
        
        Default implementation returns True (all providers try all requests).
        Override for specific filtering logic.
        """
        return True
    
    @abstractmethod
    def provide(self, request: UserContextRequest, session_manager: Any) -> Optional[Any]:
        """
        Provide context data for the request.
        
        Args:
            request: User context request
            session_manager: Session manager instance for data access
            
        Returns:
            Provider-specific data or None if unavailable
        """
        pass
