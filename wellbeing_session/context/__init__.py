"""
Context building modules.

This package contains helper functions and utilities for building
comprehensive user context from various data sources.

Phase 3: Context helpers (calculate relevance, adaptive selection, ranking)
Phase 6a: Context builder orchestrator (unified context building interface)
Phase 6b: Session context builder, insight extractor, context formatter
"""
from .context_helpers import (
    calculate_relevance_score,
    adaptive_triple_selection,
    rank_summaries_by_relevance,
)

import logging

logger = logging.getLogger(__name__)

# Phase 6a exports
from .models import (
    ContextBuildRequest,
    ContextBuildResult,
)

from .context_builder import (
    WellbeingContextBuilder,
    SessionManagerProtocol,
    create_context_builder,
)

# Phase 6b exports
try:
    from .session_context_builder import SessionContextBuilder
except ModuleNotFoundError as exc:
    # Keep core context utilities importable in non-UI environments where
    # streamlit is intentionally not installed (e.g. CI unit tests).
    if exc.name == "streamlit":
        SessionContextBuilder = None  # type: ignore[assignment]
        logger.debug(
            "SessionContextBuilder skipped because optional dependency is missing: %s",
            exc,
        )
    else:
        raise
from .insight_extractor import UserInsightExtractor
from .context_formatter import ContextFormatter

# Family entity boost (shared utility for KG retrieval)
from .family_entity_boost import detect_family_entities, family_entity_kg_boost

__all__ = [
    # Phase 3: Context helpers
    'calculate_relevance_score',
    'adaptive_triple_selection',
    'rank_summaries_by_relevance',
    
    # Phase 6a: Models
    'ContextBuildRequest',
    'ContextBuildResult',
    
    # Phase 6a: Builder
    'WellbeingContextBuilder',
    'SessionManagerProtocol',
    'create_context_builder',
    
    # Phase 6b: Session context, insights, formatter
    'SessionContextBuilder',
    'UserInsightExtractor',
    'ContextFormatter',
    
    # Family entity boost
    'detect_family_entities',
    'family_entity_kg_boost',
]

