"""
Session lifecycle management for psychological sessions.

This module handles the complete lifecycle of psychological sessions:
- Session creation and startup
- Session cleanup and orphaned session handling
- Session ending and finalization
- Insight extraction during session end

Extracted from wellbeing_session_interface.py as part of Phase 6 refactoring.
"""

import logging
import sqlite3
import streamlit as st
from contextlib import contextmanager
from i18n import t as i18n_t
from pathlib import Path
from typing import Optional, Dict, Any, Callable, Iterator

from wellbeing_session.services.startup_service import StartupService

logger = logging.getLogger(__name__)


def _tr(key: str, default: str, **kwargs) -> str:
    translated = i18n_t(key, **kwargs)
    if translated == key:
        if kwargs:
            try:
                return default.format(**kwargs)
            except Exception:
                return default
        return default
    return translated


def _insight_type_label(insight_type: str) -> str:
    normalized = str(insight_type or "unknown").strip().lower()
    default_label = normalized.replace("_", " ").title()
    return _tr(f"wellbeing_ui.insight_types.{normalized}", default_label)

def _resolve_db_path() -> str:
    """Resolve wellbeing_store.db to an absolute path (SOTA: CWD-independent)."""
    try:
        from utils.db_path_resolver import get_wellbeing_path
        return str(get_wellbeing_path())
    except ImportError:
        pass
    # Fallback: project-root relative resolution
    _project_root = Path(__file__).resolve().parent.parent.parent.parent
    return str(_project_root / "wellbeing_store.db")


class SessionLifecycleManager:
    """Manages the complete lifecycle of psychological sessions."""
    
    _DB_PATH = _resolve_db_path()
    
    def __init__(
        self, 
        session_manager: Any, 
        insight_extractor: Optional[Any] = None
    ) -> None:
        """
        Initialize session lifecycle manager.
        
        Args:
            session_manager: Session manager instance
            insight_extractor: Optional insight extractor for session end
        """
        self.session_manager = session_manager
        self.insight_extractor = insight_extractor

    def cleanup_orphaned_sessions_on_startup(self) -> tuple[int, int, int]:
        """
        Automatic cleanup of orphaned sessions on app start.
        
        Cleans up sessions that were left without end_time and summary due to crashes:
        1. Empty sessions (0 messages): Deleted
        2. Sessions with messages: End-time is set
        3. Ended sessions without summary: Summary is generated
        
        Executed only once per app start.
        
        Returns:
            Tuple of (deleted_empty, closed_with_msgs, status_fixed)
        """
        service = StartupService(
            session_manager=self.session_manager,
            orphan_cutoff_hours=1,
            db_path=self._DB_PATH,
        )
        return service.run_startup_cleanup(generate_summaries=False)
    
    def create_and_start_new_session(self, user_name: str) -> Optional[str]:
        """
        Create and start a new psychological session.
        
        Args:
            user_name: Name of the user
            
        Returns:
            Session ID if successful, None otherwise
        """
        try:
            session_id = self.session_manager.create_session(user_name)
            
            st.session_state.psych_current_session = session_id
            st.session_state.psych_enabled = True
            
            st.success(_tr("wellbeing_ui.lifecycle.new_success", "✅ Neue Wellbeing-Session erstellt: {id}...", id=session_id[:8]))
            st.rerun()  # Never returns - raises exception to restart app
            # Note: return after st.rerun() is unreachable
        
        except Exception as e:
            st.error(_tr("wellbeing_ui.lifecycle.new_error", "❌ Fehler beim Erstellen der Session: {error}", error=e))
            logger.error(f"Error creating session: {e}")
            return None
    
    def end_current_session(
        self,
        session_id: str,
        extract_insights: bool = True,
        extract_insights_func: Optional[Callable[[str], Dict[str, Any]]] = None
    ) -> bool:
        """
        End the current session and optionally extract user insights.
        
        Args:
            session_id: Session ID to end
            extract_insights: Whether to extract insights
            extract_insights_func: Optional function to extract insights
            
        Returns:
            True if successful, False otherwise
        """
        try:
            # Show session end dialog
            st.markdown("---")
            st.markdown(_tr("wellbeing_ui.lifecycle.end_header", "### 📊 Session-Abschluss"))
            
            col1, col2 = st.columns([3, 1])
            
            with col1:
                extract_insights_checkbox = st.checkbox(
                    _tr("wellbeing_ui.lifecycle.extract_checkbox", "🧠 User Insights automatisch extrahieren"),
                    value=extract_insights,
                    help=_tr("wellbeing_ui.lifecycle.extract_help", "Analysiert die Session und erkennt wichtige Muster (life_events, coping_mechanisms, etc.)"),
                )
            
            with col2:
                if st.button(_tr("wellbeing_ui.lifecycle.info_button", "ℹ️ Info"), key="insight_info_btn"):
                    st.session_state.show_insight_info = not st.session_state.get('show_insight_info', False)
            
            # Info text
            if st.session_state.get('show_insight_info', False):
                st.info(_tr(
                    "wellbeing_ui.lifecycle.info_text",
                    "**User Insights** sind langfristige Muster, die automatisch erkannt werden:\n\n- 🎯 **Life Events**: Wichtige Lebensereignisse (Job, Beziehung, etc.)\n- 🛠️ **Coping Mechanisms**: Bewaeltigungsstrategien\n- 🔄 **Recurring Themes**: Wiederkehrende Themen\n- 💭 **Emotional Patterns**: Emotionale Muster\n\nDiese Insights helfen mir, dich langfristig besser zu unterstuetzen.",
                ))
            
            # Buttons: Cancel and Confirm
            col_cancel, col_confirm = st.columns([1, 1])
            
            with col_cancel:
                if st.button(_tr("wellbeing_ui.lifecycle.back_button", "↩️ Zurueck zur Session"), key="cancel_end_session", width='stretch'):
                    st.session_state.show_end_session_dialog = False
                    st.rerun()
            
            with col_confirm:
                if st.button(_tr("wellbeing_ui.lifecycle.confirm_button", "✅ Session jetzt beenden"), type="primary", key="confirm_end_session", width='stretch'):
                    # Optional: Extract insights
                    if extract_insights_checkbox and extract_insights_func:
                        with st.spinner(_tr("wellbeing_ui.lifecycle.extract_spinner", "📊 Analysiere Session und extrahiere Insights...")):
                            insights_dict = extract_insights_func(session_id)
                        
                        if insights_dict:
                            # Count all insights
                            total_insights = sum(len(insights) for insights in insights_dict.values())
                            
                            if total_insights > 0:
                                st.success(_tr("wellbeing_ui.lifecycle.success_with_insights", "✅ Session beendet! {count} neue Insights erkannt.", count=total_insights))
                                
                                # Show top insights by type
                                with st.expander(_tr("wellbeing_ui.lifecycle.insights_expander", "🔍 Erkannte Insights nach Kategorie")):
                                    for insight_type, insights in insights_dict.items():
                                        if insights:
                                            icon = {
                                                'life_event': '🎯',
                                                'coping_mechanism': '🛠️',
                                                'personality': '🧩',
                                                'personality_trait': '🧩',
                                                'behavioral_pattern': '🔄',
                                                'emotional_state': '💭',
                                                'relationship_dynamic': '👥',
                                                'cognitive_pattern': '💡'
                                            }.get(insight_type, '📌')
                                            
                                            st.markdown(f"**{icon} {_insight_type_label(insight_type)}:**")
                                            for i, insight in enumerate(insights[:3], 1):
                                                content = (
                                                    getattr(insight, 'content', None)
                                                    or getattr(insight, 'description', None)
                                                    or getattr(insight, 'value', None)
                                                    or str(insight)
                                                )
                                                confidence = getattr(insight, 'confidence', 0.0)
                                                st.markdown(_tr("wellbeing_ui.lifecycle.insight_item", "{idx}. {content} (Konfidenz: {confidence})", idx=i, content=content, confidence=f"{confidence:.0%}"))
                            else:
                                st.info(_tr("wellbeing_ui.lifecycle.no_insights", "ℹ️ Session beendet. Keine neuen hochqualitativen Insights erkannt (Session evtl. zu kurz)."))
                        else:
                            st.info(_tr("wellbeing_ui.lifecycle.no_insights", "ℹ️ Session beendet. Keine neuen hochqualitativen Insights erkannt (Session evtl. zu kurz)."))
                    else:
                        st.success(_tr("wellbeing_ui.lifecycle.success_no_extract", "✅ Session erfolgreich beendet (ohne Insight-Extraktion)."))
                    
                    # End session - with improved error handling
                    logger.info(f"🔚 Beende Session {session_id}")
                    self.session_manager.end_session(session_id)
                    
                    # Reset session state
                    st.session_state.psych_current_session = None
                    st.session_state.psych_enabled = False
                    st.session_state.show_insight_info = False
                    st.session_state.show_end_session_dialog = False
                    
                    logger.info("✅ Session erfolgreich beendet - Session State zurückgesetzt")
                    
                    st.info(_tr("wellbeing_ui.lifecycle.after_info", "💡 Sie koennen jederzeit eine neue Session starten oder eine vorherige fortsetzen."))
                    
                    # Trigger fallback summary generation
                    self._end_session_fallback(session_id)
                    
                    st.rerun()  # Never returns - raises exception to restart app
                    # Note: return after st.rerun() is unreachable
            
            return False
            
        except Exception as e:
            st.error(_tr("wellbeing_ui.lifecycle.end_error", "❌ Fehler beim Beenden der Session: {error}", error=e))
            logger.error(f"Error ending session: {e}")
            return False
    
    @contextmanager
    def _get_db_connection(self) -> Iterator[sqlite3.Connection]:
        """Yield a correctly scoped connection with foreign keys enabled.

        Mirrors :meth:`StartupService._connection` so that the fallback path
        shares the same connection-pool strategy (pool-first, raw sqlite3
        fallback).
        """
        try:
            from database.connection_pool import get_pool
            with get_pool(self._DB_PATH).get_connection() as conn:
                conn.execute("PRAGMA foreign_keys=ON")
                yield conn
            return
        except ImportError:
            pass

        conn = sqlite3.connect(self._DB_PATH)
        try:
            conn.execute("PRAGMA foreign_keys=ON")
            yield conn
        finally:
            conn.close()

    def _end_session_fallback(self, session_id: str) -> None:
        """
        Fallback for ending session - ensures session is properly closed.

        Args:
            session_id: Session ID to end
        """
        try:
            with self._get_db_connection() as conn:
                cursor = conn.cursor()

                # Ensure session is marked as ended
                cursor.execute('''
                    UPDATE wellbeing_sessions
                    SET end_time = COALESCE(end_time, CURRENT_TIMESTAMP),
                        status = 'ended',
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                ''', (session_id,))

                conn.commit()

            logger.info("✅ Fallback: Session %s... marked as ended", session_id[:12])

        except Exception as e:
            logger.error("❌ Fallback error for session %s: %s", session_id, e)

