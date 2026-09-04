"""
Mood Progression Provider for user context building.

Analyzes and formats mood progression data from KG triples.
"""

from typing import Optional, List, Dict, Any
import logging
from user_context_builder.base import BaseContextProvider
from user_context_builder.models import MoodProgressionData, UserContextRequest

logger = logging.getLogger(__name__)


class MoodProgressionProvider(BaseContextProvider):
    """Provider for analyzing mood progression from knowledge graph."""
    
    def __init__(
        self,
        max_mood_triples: int = 20,
        priority: int = 30,
    ):
        """
        Initialize the Mood Progression Provider.
        
        Args:
            max_mood_triples: Maximum number of mood-related triples to analyze
            priority: Provider priority (lower = higher priority)
        """
        super().__init__(name="mood_progression", priority=priority)
        self.max_mood_triples = max_mood_triples
    
    def provide(
        self,
        request: UserContextRequest,
        session_manager: Any
    ) -> Optional[MoodProgressionData]:
        """
        Fetch mood-related triples and analyze progression.
        
        Args:
            request: User context request
            session_manager: Session manager with KG access
            
        Returns:
            MoodProgressionData or empty data if no mood info available
        """
        mood_tracker = self._resolve_mood_tracker(session_manager)

        if not mood_tracker:
            logger.warning("Mood tracker not available")
            return MoodProgressionData()
        
        try:
            mood_data = mood_tracker.get_progression_for_session(request.current_session_id)

            if not mood_data or not isinstance(mood_data, dict):
                logger.debug(f"No mood progression found for session {request.current_session_id}")
                return MoodProgressionData()

            return MoodProgressionData(
                current_mood=mood_data.get("current_mood"),
                trend=mood_data.get("trend"),
                average_valence=mood_data.get("average_valence"),
                confidence=mood_data.get("confidence"),
                significant_change=mood_data.get("significant_change", False),
                related_triples=mood_data.get("related_triples", []),
            )
        
        except Exception as e:
            logger.error(f"Error analyzing mood progression: {e}", exc_info=True)
            return MoodProgressionData()

    def _resolve_mood_tracker(self, session_manager: Any) -> Optional[Any]:
        mood_tracker = getattr(session_manager, 'mood_tracker', None)
        if mood_tracker is not None:
            return mood_tracker

        manager = getattr(session_manager, 'manager', None)
        if manager is not None:
            return getattr(manager, 'mood_tracker', None)

        return None
