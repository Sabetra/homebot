"""
Session Summaries Provider for user context building.

Fetches and formats session summaries with relevance ranking.
"""

from typing import Optional, List, Dict, Any
import logging
from user_context_builder.base import BaseContextProvider
from user_context_builder.models import SessionSummaryData, UserContextRequest

logger = logging.getLogger(__name__)


class SessionSummariesProvider(BaseContextProvider):
    """Provider for fetching session summaries about the user."""
    
    def __init__(
        self,
        max_sessions: int = 10,
        priority: int = 20,
    ):
        """
        Initialize the Session Summaries Provider.
        
        Args:
            max_sessions: Maximum number of session summaries to fetch
            priority: Provider priority (lower = higher priority)
        """
        super().__init__(name="session_summaries", priority=priority)
        self.max_sessions = max_sessions
    
    def provide(
        self,
        request: UserContextRequest,
        session_manager: Any
    ) -> Optional[SessionSummaryData]:
        """
        Fetch session summaries for the user.
        
        Args:
            request: User context request
            session_manager: Session manager with DB access
            
        Returns:
            SessionSummaryData or empty data if no summaries found
        """
        db_manager = self._resolve_db_manager(session_manager)
        
        if not db_manager:
            logger.warning("DB manager not available")
            return SessionSummaryData()
        
        try:
            # Fetch session summaries, excluding current session
            summaries = self._get_session_summaries(
                db_manager,
                request.user_id,
                request.current_session_id
            )
            
            # Defensive: ensure summaries is a list
            if not isinstance(summaries, list):
                summaries = list(summaries) if summaries else []
            
            if not summaries:
                logger.debug(f"No session summaries found for user {request.user_id}")
                return SessionSummaryData()
            
            return SessionSummaryData(
                summaries=summaries,
                ranking_method="chronological",
                total_available=len(summaries),
                generated_on_the_fly=0
            )
        
        except Exception as e:
            logger.error(f"Error fetching session summaries: {e}", exc_info=True)
            # Re-raise so the builder can record the error and continue
            raise

    def _resolve_db_manager(self, session_manager: Any) -> Optional[Any]:
        db_manager = getattr(session_manager, 'db_manager', None)
        if db_manager is not None:
            return db_manager

        manager = getattr(session_manager, 'manager', None)
        if manager is not None:
            return getattr(manager, 'db', None)

        return None
    
    def _get_session_summaries(
        self,
        db_manager: Any,
        user_id: str,
        current_session_id: str
    ) -> List[Dict[str, Any]]:
        """
        Get session summaries from database.
        
        Args:
            db_manager: Database manager instance
            user_id: User identifier
            current_session_id: Current session to exclude
            
        Returns:
            List of session summary dictionaries
        """
        if hasattr(db_manager, 'get_user_sessions'):
            sessions = db_manager.get_user_sessions(user_id=user_id, status=None)
            
            summaries = []
            sorted_sessions = sorted(
                sessions or [],
                key=lambda s: s.get('updated_at', s.get('created_at', '')),
                reverse=True,
            )

            for session in sorted_sessions:
                # Handle both dict and object-based sessions
                if isinstance(session, dict):
                    session_id = session.get('session_id') or session.get('id')
                    summary_text = session.get('summary') or session.get('session_summary') or session.get('content', '')
                    timestamp = session.get('timestamp') or session.get('created_at') or session.get('updated_at')
                else:
                    # Object-based session (e.g., SQLAlchemy model)
                    session_id = getattr(session, 'id', None) or getattr(session, 'session_id', None)
                    summary_text = getattr(session, 'session_summary', None) or getattr(session, 'summary', None)
                    timestamp = getattr(session, 'created_at', None) or getattr(session, 'timestamp', None)
                
                # Skip current session
                if session_id == current_session_id:
                    continue
                
                if summary_text and isinstance(summary_text, str) and len(summary_text.strip()) > 20:
                    summaries.append({
                        'session_id': session_id,
                        'summary': summary_text[:500],
                        'timestamp': timestamp
                    })

                if len(summaries) >= self.max_sessions:
                    break
            
            return summaries[:self.max_sessions]
        
        logger.warning("DB manager has no method to retrieve session summaries")
        return []
