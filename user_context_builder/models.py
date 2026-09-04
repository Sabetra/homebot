"""
User Context Builder V2 - Pydantic V2 Models

Structured data models for user context building operations.
All models are frozen and immutable for thread safety.
"""
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field, ConfigDict, field_validator


class UserContextRequest(BaseModel):
    """Input request for context building."""
    model_config = ConfigDict(frozen=True, strict=True)
    
    user_id: str
    current_session_id: str
    user_input: str
    max_tokens: int = 2000
    
    @field_validator("user_id", "current_session_id")
    @classmethod
    def validate_ids(cls, v: str) -> str:
        """Validate IDs are not empty."""
        if not v or not v.strip():
            raise ValueError("ID cannot be empty")
        return v.strip()
    
    @field_validator("max_tokens")
    @classmethod
    def validate_max_tokens(cls, v: int) -> int:
        """Validate max_tokens is positive."""
        if v <= 0:
            raise ValueError("max_tokens must be positive")
        return v


class KnowledgeGraphData(BaseModel):
    """Knowledge graph triples with metadata."""
    model_config = ConfigDict(frozen=True)
    
    triples: List[Dict[str, Any]] = Field(default_factory=list)
    insights: List[Dict[str, Any]] = Field(default_factory=list)
    selection_info: Dict[str, Any] = Field(default_factory=dict)
    total_retrieved: int = 0
    total_selected: int = 0


class SessionSummaryData(BaseModel):
    """Session summaries with ranking."""
    model_config = ConfigDict(frozen=True)
    
    summaries: List[Dict[str, Any]] = Field(default_factory=list)
    ranking_method: str = "relevance"
    total_available: int = 0
    generated_on_the_fly: int = 0


class MoodProgressionData(BaseModel):
    """Mood progression tracking."""
    model_config = ConfigDict(frozen=True)
    
    current_mood: Optional[str] = None
    trend: Optional[str] = None  # improving/declining/stable
    average_valence: Optional[float] = None
    confidence: Optional[float] = None
    significant_change: bool = False
    related_triples: List[Dict[str, Any]] = Field(default_factory=list)


class CareGoalsData(BaseModel):
    """Care goals with progress."""
    model_config = ConfigDict(frozen=True)
    
    goals: List[Dict[str, Any]] = Field(default_factory=list)
    goals_with_progress: int = 0


class ContextSection(BaseModel):
    """A formatted section of user context."""
    model_config = ConfigDict(frozen=True)
    
    title: str
    content: str
    token_estimate: int
    metadata: Dict[str, Any] = Field(default_factory=dict)


class UserContextResult(BaseModel):
    """Complete user context result."""
    model_config = ConfigDict(frozen=True)
    
    user_id: str
    current_session_id: str
    knowledge_graph: KnowledgeGraphData
    session_summaries: SessionSummaryData
    mood_progression: MoodProgressionData
    care_goals: CareGoalsData
    persistent_profile: Optional[Dict[str, Any]] = None
    user_insights: List[Dict[str, Any]] = Field(default_factory=list)
    context_token_estimate: int = 0
    
    # Metadata
    build_time_ms: float = 0.0
    sources_used: List[str] = Field(default_factory=list)
    errors: List[str] = Field(default_factory=list)
    
    def to_dict(self) -> Dict[str, Any]:
        """
        Convert to legacy format dict.
        
        Returns:
            Dict compatible with legacy _build_comprehensive_user_context format
        """
        return {
            'user_id': self.user_id,
            'current_session_id': self.current_session_id,
            'knowledge_graph': self.knowledge_graph.triples,
            'session_summaries': self.session_summaries.summaries,
            'mood_progression': {
                'current_mood': self.mood_progression.current_mood,
                'trend': self.mood_progression.trend,
                'average_valence': self.mood_progression.average_valence,
                'confidence': self.mood_progression.confidence,
                'significant_change': self.mood_progression.significant_change,
                'related_triples': self.mood_progression.related_triples
            } if self.mood_progression.current_mood else {},
            'care_goals': self.care_goals.goals,
            'persistent_profile': self.persistent_profile,
            'user_insights': self.user_insights,
            'context_token_estimate': self.context_token_estimate
        }
