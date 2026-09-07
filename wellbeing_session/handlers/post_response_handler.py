#!/usr/bin/env python3
"""
POST-RESPONSE HANDLER — Decoupled Mood/Goal Tracking
=====================================================

Extracts the post-response processing (mood tracking, goal extraction)
out of wellbeing_chat() into a standalone handler.

This eliminates the 4-level-deep import chain:
    wellbeing_chat → WellbeingSessionInterface → SessionManager → MoodTracker/GoalManager

Instead, the handler receives the SessionManager directly (dependency injection).

RC-7 FIX: Responsibility inversion — LLM layer no longer reaches into
          session management layer.
"""

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class PostResponseHandler:
    """
    Handles post-response processing (mood tracking, goal extraction).
    
    Designed to be called AFTER wellbeing_chat() returns,
    typically by ResponseGenerator or the session interface.
    
    Usage:
        handler = PostResponseHandler(session_manager)
        handler.process(user_message, session_id)
    """
    
    def __init__(self, session_manager: Any = None):
        """
        Args:
            session_manager: The psychological session manager instance
                            (has .manager.mood_tracker)
        """
        self._session_manager = session_manager
        self._mood_tracker = None

        # Extract sub-components once (avoid repeated hasattr chains)
        if session_manager:
            manager = getattr(session_manager, 'manager', None)
            if manager:
                self._mood_tracker = getattr(manager, 'mood_tracker', None)

        logger.debug(
            f"[POST-RESPONSE] Initialized: "
            f"mood_tracker={'yes' if self._mood_tracker else 'no'}"
        )
    
    def process(
        self,
        user_message: str,
        session_id: str,
    ) -> Optional[Dict[str, Any]]:
        """
        Run all post-response processing.

        Goal tracking is owned by ``CarePlanManager.process_turn`` (cadence-
        managed, SOTA pipeline) and does not belong here.

        Args:
            user_message: The user's original query
            session_id: Current session ID

        Returns:
            The mood-tracking result dict (or ``None`` if no tracker / failure).
        """
        if not session_id or not user_message:
            return None

        return self._track_mood(user_message, session_id)
    
    def _track_mood(
        self, user_message: str, session_id: str
    ) -> Optional[Dict[str, Any]]:
        """Analyze and store mood from user message."""
        if not self._mood_tracker:
            return None
        
        try:
            mood_result = self._mood_tracker.analyze_and_store_mood(
                user_message=user_message,
                session_id=session_id,
            )
            if mood_result:
                logger.info(
                    f"✅ [MOOD-TRACKING] Mood: "
                    f"{mood_result.get('detected_mood', 'N/A')}"
                )
            return mood_result
        except Exception as e:
            logger.warning(f"⚠️ [MOOD-TRACKING] Fehler: {e}")
            return None

