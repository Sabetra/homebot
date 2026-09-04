"""
Type-safe data structures for psychological session management.

This module defines TypedDict classes for all major data structures,
replacing loose Dict[str, Any] with strictly typed dictionaries.

Benefits:
- IDE autocomplete
- Type checking at compile time
- Self-documenting code
- Prevents KeyError bugs
"""

from typing import TypedDict, List, Optional, Any
from datetime import datetime


# ==============================================================================
# SESSION DATA STRUCTURES
# ==============================================================================

class SessionMessage(TypedDict):
    """A single message in a psychological session."""
    message_id: str
    session_id: str
    timestamp: datetime
    role: str  # 'user' or 'assistant'
    content: str
    emotional_markers: List[str]
    is_crisis: bool


class SessionSummary(TypedDict):
    """Summary of a psychological session."""
    session_id: str
    user_id: str
    start_time: datetime
    last_activity: datetime
    message_count: int
    key_topics: List[str]
    emotional_state: str
    session_summary: str
    is_active: bool


class SessionData(TypedDict):
    """Complete session data including metadata and messages."""
    id: str
    user_id: str
    created_at: datetime
    last_activity: datetime
    status: str  # 'active' or 'closed'
    session_summary: Optional[str]
    messages: List[SessionMessage]
    metadata: dict[str, Any]


# ==============================================================================
# KNOWLEDGE GRAPH STRUCTURES
# ==============================================================================

class KGTriple(TypedDict):
    """Knowledge Graph triple with metadata."""
    subject: str
    predicate: str
    object: str
    confidence: float
    similarity: float  # Semantic similarity score
    source_date: str
    session_id: Optional[str]
    user_id: str
    created_at: datetime


class KGSearchResult(TypedDict):
    """Result from KG semantic search."""
    triples: List[KGTriple]
    total_count: int
    search_duration_ms: float


# ==============================================================================
# PROFILE STRUCTURES
# ==============================================================================

class UserInsight(TypedDict):
    """A single user insight extracted from data."""
    insight: str
    category: str  # 'personality', 'behavior', 'concern', etc.
    confidence: float
    source: str  # 'db', 'kg', 'llm'
    created_at: datetime


class WellbeingProfile(TypedDict):
    """Complete psychological profile for a user."""
    user_id: str
    overall_confidence: float
    personality_traits: List[str]
    concerns: List[str]
    coping_strategies: List[str]
    relationship_patterns: List[str]
    emotional_regulation: str
    insights: List[UserInsight]
    data_sources: dict[str, int]  # Source counts
    generated_at: datetime
    cache_valid_until: datetime


# ==============================================================================
# MOOD & EMOTION STRUCTURES
# ==============================================================================

class MoodEntry(TypedDict):
    """Single mood/emotion entry."""
    timestamp: datetime
    mood: str
    valence: float  # -1.0 to 1.0
    arousal: float  # 0.0 to 1.0
    confidence: float
    context: str


class MoodProgression(TypedDict):
    """Mood progression over time for a session."""
    current_mood: str
    trend: str  # 'improving', 'declining', 'stable'
    average_valence: float
    confidence: float
    significant_change: bool
    related_triples: List[KGTriple]
    history: List[MoodEntry]


# ==============================================================================
# CARE STRUCTURES
# ==============================================================================

class CareGoal(TypedDict):
    """A care goal with progress tracking."""
    goal: str
    status: str  # 'active', 'completed', 'paused'
    created: datetime
    progress_triples: List[KGTriple]
    has_progress: bool


class CarePrompt(TypedDict):
    """A care prompt for a specific context."""
    prompt: str
    category: str
    subcategory: str
    confidence_level: str
    metadata: dict[str, Any]


# ==============================================================================
# CONTEXT BUILDING STRUCTURES
# ==============================================================================

class UserContext(TypedDict):
    """Complete user context for LLM."""
    user_id: str
    current_session_id: str
    knowledge_graph: List[KGTriple]
    session_summaries: List[SessionSummary]
    mood_progression: MoodProgression
    user_insights: List[UserInsight]
    care_goals: List[CareGoal]
    persistent_profile: Optional[WellbeingProfile]
    context_token_estimate: int


# ==============================================================================
# DATABASE QUERY RESULTS
# ==============================================================================

class DBSessionRow(TypedDict):
    """Raw session row from database."""
    id: str
    user_id: str
    created_at: str  # ISO format
    last_activity: str  # ISO format
    status: str
    session_summary: Optional[str]


class DBInteractionRow(TypedDict):
    """Raw interaction row from database."""
    id: str
    session_id: str
    timestamp: str  # ISO format
    user_message: str
    bot_response: str
    emotional_markers: Optional[str]  # JSON string


class DBTripleRow(TypedDict):
    """Raw KG triple row from database."""
    id: int
    subject: str
    predicate: str
    object: str
    confidence: float
    user_id: str
    session_id: Optional[str]
    interaction_date: Optional[str]  # ISO format
    created_at: str  # ISO format


# ==============================================================================
# RESPONSE STRUCTURES
# ==============================================================================

class LLMResponse(TypedDict):
    """Structured LLM response."""
    content: str
    confidence: float
    reasoning: Optional[str]
    metadata: dict[str, Any]


class EmotionalAnalysisResult(TypedDict):
    """Result of emotional analysis."""
    primary_emotion: str
    secondary_emotions: List[str]
    valence: float
    arousal: float
    confidence: float
    crisis_indicators: List[str]
    is_crisis: bool


# ==============================================================================
# EXPORT ALL
# ==============================================================================

__all__ = [
    # Session
    'SessionMessage',
    'SessionSummary',
    'SessionData',
    # Knowledge Graph
    'KGTriple',
    'KGSearchResult',
    # Profile
    'UserInsight',
    'WellbeingProfile',
    # Mood
    'MoodEntry',
    'MoodProgression',
    # Care
    'CareGoal',
    'CarePrompt',
    # Context
    'UserContext',
    # Database
    'DBSessionRow',
    'DBInteractionRow',
    'DBTripleRow',
    # Response
    'LLMResponse',
    'EmotionalAnalysisResult',
]
