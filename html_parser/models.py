"""
HTML Parser V2 - Pydantic V2 Models

Structured data models for HTML parsing operations.
All models are frozen and immutable for thread safety.
"""
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field, ConfigDict, field_validator


class HTMLParseContext(BaseModel):
    """Input context for HTML parsing operation."""
    model_config = ConfigDict(frozen=True, strict=True)
    
    url: str
    html: str
    query: Optional[str] = None
    
    @field_validator("url")
    @classmethod
    def validate_url(cls, v: str) -> str:
        """Validate URL is not empty."""
        if not v or not v.strip():
            raise ValueError("URL cannot be empty")
        return v.strip()
    
    @field_validator("html")
    @classmethod
    def validate_html(cls, v: str) -> str:
        """Validate HTML is not empty."""
        if not v or not v.strip():
            raise ValueError("HTML cannot be empty")
        return v


class HTMLParseResult(BaseModel):
    """Final result of HTML parsing operation."""
    model_config = ConfigDict(frozen=True)
    
    url: str
    canonical_url: str
    domain: str
    title: str = ""
    snippet: str = ""
    text_len: int = 0
    language: Optional[str] = None
    og: Dict[str, Any] = Field(default_factory=dict)
    meta: Dict[str, Any] = Field(default_factory=dict)
    
    # Quality metadata
    extraction_quality: float = Field(ge=0.0, le=1.0, default=0.0)
    fallback_used: bool = False
    errors: List[str] = Field(default_factory=list)
    full_text: str = ""  # SOTA v2.1: Full text for snippet extraction
    
    @field_validator("extraction_quality")
    @classmethod
    def validate_quality(cls, v: float) -> float:
        """Ensure quality is between 0 and 1."""
        return max(0.0, min(1.0, v))


class CleanupResult(BaseModel):
    """Result of HTML cleanup stage."""
    model_config = ConfigDict(arbitrary_types_allowed=True)
    
    soup: Any  # BeautifulSoup object (not serializable, but needed for processing)
    removed_tags: int = 0
    errors: List[str] = Field(default_factory=list)


class MetadataResult(BaseModel):
    """Result of metadata extraction stage."""
    model_config = ConfigDict(frozen=True)
    
    canonical_url: Optional[str] = None
    og: Dict[str, Any] = Field(default_factory=dict)
    meta: Dict[str, Any] = Field(default_factory=dict)
    errors: List[str] = Field(default_factory=list)


class ContentResult(BaseModel):
    """Result of content extraction stage."""
    model_config = ConfigDict(frozen=True)
    
    title: Optional[str] = None
    text: str = ""
    snippet: str = ""
    language: Optional[str] = None
    text_len: int = 0
    errors: List[str] = Field(default_factory=list)


class FallbackParseResult(BaseModel):
    """Result of fallback parsing (minimal regex-based)."""
    model_config = ConfigDict(frozen=True)
    
    title: str = ""
    text: str = ""
    errors: List[str] = Field(default_factory=list)
