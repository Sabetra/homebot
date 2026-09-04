"""
Content Extractor V2 - Pydantic V2 Models

Structured data models for modern content extraction operations.
All models are frozen and immutable for thread safety.
"""
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field, ConfigDict, field_validator


class ExtractionContext(BaseModel):
    """Input context for content extraction operation."""
    model_config = ConfigDict(frozen=True, strict=True)
    
    url: str
    timeout: int = 6
    query: Optional[str] = None
    accept_language: Optional[str] = None
    
    @field_validator("url")
    @classmethod
    def validate_url(cls, v: str) -> str:
        """Validate URL is not empty."""
        if not v or not v.strip():
            raise ValueError("URL cannot be empty")
        return v.strip()
    
    @field_validator("timeout")
    @classmethod
    def validate_timeout(cls, v: int) -> int:
        """Validate timeout is positive."""
        if v <= 0:
            raise ValueError("Timeout must be positive")
        return v


class ContentMetadata(BaseModel):
    """Metadata extracted from content."""
    model_config = ConfigDict(frozen=True)
    
    author: Optional[str] = None
    date: Optional[str] = None
    description: Optional[str] = None
    keywords: List[str] = Field(default_factory=list)
    summary: Optional[str] = None
    language: Optional[str] = None


class ExtractionResult(BaseModel):
    """Result of content extraction attempt."""
    model_config = ConfigDict(frozen=True)
    
    success: bool
    canonical_url: str
    title: Optional[str] = None
    snippet: str = ""
    full_text: str = ""
    text_len: int = 0
    quality_score: float = Field(ge=0.0, le=1.0, default=0.0)
    extraction_method: str = "unknown"
    domain: str = ""
    language: Optional[str] = None
    meta: Dict[str, Any] = Field(default_factory=dict)
    error: Optional[str] = None
    error_class: Optional[str] = None
    
    @field_validator("quality_score")
    @classmethod
    def validate_quality(cls, v: float) -> float:
        """Ensure quality is between 0 and 1."""
        return max(0.0, min(1.0, v))
