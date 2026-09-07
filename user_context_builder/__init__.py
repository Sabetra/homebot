"""
User Context Builder V2 Package

Modular, low-CC system for building comprehensive user context
from multiple data sources (KG, sessions, mood, goals, profile).

Usage:
    from user_context_builder import UserContextBuilder, UserContextRequest
    from user_context_builder.providers import (
        KnowledgeGraphProvider,
        SessionSummariesProvider,
        MoodProgressionProvider,
        CareGoalsProvider,
        PersistentProfileProvider
    )
    
    # Create providers
    providers = [
        KnowledgeGraphProvider(max_triples=50, priority=10),
        SessionSummariesProvider(max_sessions=10, priority=20),
        MoodProgressionProvider(max_mood_triples=20, priority=30),
        CareGoalsProvider(max_goals=10, priority=40),
        PersistentProfileProvider(priority=50),
    ]
    
    # Build context
    builder = UserContextBuilder(providers=providers)
    request = UserContextRequest(
        user_id="user123",
        current_session_id="session456",
        user_input="I'm feeling anxious today",
        max_tokens=2000
    )
    
    result = builder.build(request, session_manager)
    context_dict = result.to_dict()  # Legacy format
"""

from user_context_builder.models import (
    UserContextRequest,
    UserContextResult,
    KnowledgeGraphData,
    SessionSummaryData,
    MoodProgressionData,
    CareGoalsData,
    ContextSection,
)
from user_context_builder.builder import UserContextBuilder
from user_context_builder.base import BaseContextProvider, ContextProvider

__all__ = [
    "UserContextBuilder",
    "UserContextRequest",
    "UserContextResult",
    "KnowledgeGraphData",
    "SessionSummaryData",
    "MoodProgressionData",
    "CareGoalsData",
    "ContextSection",
    "BaseContextProvider",
    "ContextProvider",
]

__version__ = "2.0.0"
