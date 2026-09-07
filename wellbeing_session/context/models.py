"""
Pydantic V2 Models for Context Building.

This module defines type-safe data models for psychological context building operations.
All models use Pydantic V2 for runtime validation and serialization.
"""

from typing import Dict, Any, Optional
from pydantic import BaseModel, Field, field_validator


class ContextBuildRequest(BaseModel):
    """
    Request parameters for building psychological context.
    
    Attributes:
        user_id: Unique identifier for the user
        current_session_id: ID of the active session
        user_input: Current user message/query
        use_v2_builder: Whether to use V2 modular builder (default: False)
    
    Examples:
        >>> request = ContextBuildRequest(
        ...     user_id="user_123",
        ...     current_session_id="session_456",
        ...     user_input="I feel anxious today"
        ... )
        >>> request.user_id
        'user_123'
    """
    
    user_id: str = Field(
        ...,
        min_length=1,
        description="User identifier (required, non-empty)"
    )
    current_session_id: str = Field(
        ...,
        min_length=1,
        description="Active session identifier (required, non-empty)"
    )
    user_input: str = Field(
        ...,
        description="Current user input/message"
    )
    use_v2_builder: bool = Field(
        default=False,
        description="Use V2 modular context builder if available"
    )
    
    @field_validator('user_id', 'current_session_id')
    @classmethod
    def validate_ids(cls, v: str) -> str:
        """Validate that IDs are non-empty after stripping whitespace."""
        if not v or not v.strip():
            raise ValueError("ID must not be empty or whitespace")
        return v.strip()
    
    model_config = {
        "frozen": False,
        "extra": "forbid",
        "str_strip_whitespace": True
    }


class ContextBuildResult(BaseModel):
    """
    Result of a context building operation.
    
    Contains the built context along with metadata about the build process,
    including performance metrics and data source information.
    
    Attributes:
        context: The built user context dictionary
        builder_version: Which builder was used ('legacy' or 'v2')
        duration_ms: Time taken to build context in milliseconds
        token_estimate: Estimated token count for the context
        sources_used: List of data sources that provided data
        success: Whether the build was successful
        error: Error message if build failed (None on success)
    
    Examples:
        >>> result = ContextBuildResult(
        ...     context={'knowledge_graph': []},
        ...     builder_version='legacy',
        ...     duration_ms=123.45,
        ...     token_estimate=500,
        ...     sources_used=['knowledge_graph', 'session_summaries']
        ... )
        >>> result.success
        True
    """
    
    context: Dict[str, Any] = Field(
        default_factory=dict,
        description="Built user context with all gathered data"
    )
    builder_version: str = Field(
        ...,
        description="Builder type: 'legacy' or 'v2'"
    )
    duration_ms: float = Field(
        ...,
        ge=0.0,
        description="Build time in milliseconds (>= 0)"
    )
    token_estimate: int = Field(
        default=0,
        ge=0,
        description="Estimated token count for context (>= 0)"
    )
    sources_used: list[str] = Field(
        default_factory=list,
        description="Names of data sources that contributed data"
    )
    success: bool = Field(
        default=True,
        description="Whether context build succeeded"
    )
    error: Optional[str] = Field(
        default=None,
        description="Error message if build failed"
    )
    
    @field_validator('builder_version')
    @classmethod
    def validate_builder_version(cls, v: str) -> str:
        """Ensure builder version is either 'legacy' or 'v2'."""
        if v not in ['legacy', 'v2']:
            raise ValueError("builder_version must be 'legacy' or 'v2'")
        return v
    
    model_config = {
        "frozen": False,  # Allow modifications for monitoring
        "extra": "allow",  # Allow extra fields for extensibility
    }
