"""
Active session UI rendering for psychological sessions.

This module handles the UI rendering for active psychological sessions including:
- Session metrics and status
- Chat history display
- Chat input handling
- Session action buttons

Extracted from wellbeing_session_interface.py as part of Phase 5 refactoring.
"""

from datetime import datetime, timezone
from typing import Callable, Optional, Any, Dict, List, Union
import streamlit as st
from i18n import t as i18n_t
from wellbeing_session.utils.datetime_utils import normalize_datetime
from wellbeing_session.ui.goal_progress_renderer import GoalProgressRenderer


def _tr(key: str, default: str, **kwargs: Any) -> str:
    translated = i18n_t(key, **kwargs)
    if translated == key:
        if kwargs:
            try:
                return default.format(**kwargs)
            except Exception:
                return default
        return default
    return translated


class ActiveSessionRenderer:
    """Renders active session UI components."""
    
    def __init__(self, session_manager: Any, context_manager: Any) -> None:
        """
        Initialize active session renderer.
        
        Args:
            session_manager: Session manager instance
            context_manager: Context manager instance
        """
        self.session_manager = session_manager
        self.context_manager = context_manager
        self.goal_progress_renderer = GoalProgressRenderer(session_manager=session_manager)
        
    def render_active_session_interface(
        self,
        handle_input_func: Callable[[str], None],
        end_session_func: Callable[[], None],
        show_session_notes_func: Optional[Callable[[], None]] = None
    ) -> None:
        """
        Render the active session interface.
        
        Args:
            handle_input_func: Function to handle user input
            end_session_func: Function to end session
            show_session_notes_func: Optional function to show session notes
        """
        # Check if session-end dialog should be shown
        if st.session_state.get('show_end_session_dialog', False):
            end_session_func()
            return  # Show only end dialog, not normal interface
        
        st.subheader(_tr("wellbeing_ui.active.subheader", "💬 Wellbeing-Session: {id}...", id=st.session_state.psych_current_session[:8]))
        
        # Session info with context status
        self._render_session_metrics()

        # Care goal progress (active session owner only)
        self.goal_progress_renderer.render(
            session_id=st.session_state.psych_current_session,
            current_user_name=str(st.session_state.get('psych_current_user', '')),
        )
        
        # Chat interface
        st.markdown(_tr("wellbeing_ui.active.chat_header", "### 💭 Dein Wellbeing-Chat"))
        
        # Display psychological chat history
        self._display_psychological_chat_history()
        
        # Chat input
        user_input = st.chat_input(
            _tr("wellbeing_ui.active.chat_input", "Teilen Sie Ihre Gedanken und Gefuehle mit..."),
            key="psych_chat_input"
        )
        
        if user_input:
            handle_input_func(user_input)
        
        # Session actions
        self._render_session_actions(end_session_func, show_session_notes_func)
    
    def _render_session_metrics(self) -> None:
        """Render session metrics and status."""
        session_id = str(st.session_state.get('psych_current_session', '')).strip()
        if not session_id:
            return

        session_summary = self.session_manager.get_session_summary(session_id)
        if not session_summary:
            return

        col1, col2, col3, col4 = st.columns(4)

        with col1:
            st.metric(_tr("wellbeing_ui.active.metric_messages", "📊 Nachrichten"), session_summary.get('message_count', 0))

        with col2:
            st.metric(_tr("wellbeing_ui.active.metric_emotion", "😊 Emotionaler Zustand"), session_summary.get('emotional_state', _tr("wellbeing_ui.active.neutral", "Neutral")))

        with col3:
            start_time = normalize_datetime(
                session_summary.get('start_time', datetime.now(timezone.utc))
            )
            current_time = datetime.now(timezone.utc)
            duration = current_time - start_time
            total_minutes = int(duration.total_seconds() // 60)
            st.metric(_tr("wellbeing_ui.active.metric_duration", "⏱️ Dauer"), _tr("wellbeing_ui.active.duration_value", "{minutes}min", minutes=total_minutes))

        with col4:
            self._render_context_status()

        session_summary_text = session_summary.get('session_summary', '')
        if session_summary_text:
            with st.expander(_tr("wellbeing_ui.active.summary_expander", "📝 Session-Zusammenfassung")):
                st.markdown(session_summary_text)
    
    def _render_context_status(self) -> None:
        """Render context window usage status."""
        if self.context_manager is None:
            st.metric(_tr("wellbeing_ui.active.metric_context", "🧠 Context"), "N/A")
            return

        messages = self.session_manager.get_session_context(
            st.session_state.psych_current_session,
            max_messages=50
        )

        if not messages:
            st.metric(_tr("wellbeing_ui.active.metric_context", "🧠 Context"), "0%")
            return

        llm_messages = [
            {'role': msg.get('role', 'user'), 'content': msg.get('content', '')}
            for msg in messages
        ]
        analysis = self.context_manager.analyze_context_usage(llm_messages)

        st.metric(
            _tr("wellbeing_ui.active.metric_context", "🧠 Context"),
            f"{analysis['usage_percentage']:.0f}%",
            help=(
                _tr(
                    "wellbeing_ui.active.context_help",
                    "Context-Fenster-Auslastung: {used} von {max} Token",
                    used=analysis['total_tokens'],
                    max=self.context_manager.max_context_tokens,
                )
            )
        )
    
    def _display_psychological_chat_history(self) -> None:
        """Display chat history for current psychological session."""
        if not st.session_state.psych_current_session:
            return

        messages = self.session_manager.get_session_context(
            st.session_state.psych_current_session,
            max_messages=20
        )

        if not messages:
            st.info(_tr("wellbeing_ui.active.empty_chat", "💭 Beginnen Sie Ihr Gespraech..."))
            return

        for msg in messages:
            self._render_chat_message(msg)
    
    def _render_chat_message(self, msg: Dict[str, Any]) -> None:
        """
        Render a single chat message.
        
        Args:
            msg: Message dict with role, content, timestamp, etc.
        """
        role = msg.get('role', 'user')
        content = msg.get('content', '')
        timestamp = msg.get('timestamp', datetime.now(timezone.utc))
        emotional_markers = msg.get('emotional_markers', [])
        
        if role == "user":
            with st.chat_message("user"):
                st.write(content)
                if isinstance(timestamp, datetime):
                    st.caption(f"🕐 {timestamp.strftime('%H:%M')}")
        else:
            with st.chat_message("assistant"):
                st.write(content)
                if isinstance(timestamp, datetime):
                    st.caption(f"🕐 {timestamp.strftime('%H:%M')}")
                
                # Display emotional markers
                if emotional_markers:
                    self._render_emotional_markers(emotional_markers)
    
    def _render_emotional_markers(self, emotional_markers: Union[str, List[Dict[str, Any]], Dict[str, Any]]) -> None:
        """
        Render emotional markers for a message.
        
        Args:
            emotional_markers: Emotional markers (string, list, or dict)
        """
        display_markers = []
        
        # Handle different formats
        if isinstance(emotional_markers, str):
            try:
                import json
                # Handle Python dict string representation (single quotes)
                if ("'" in emotional_markers and '"' not in emotional_markers) or "{'" in emotional_markers:
                    import ast
                    parsed = ast.literal_eval(emotional_markers)
                else:
                    parsed = json.loads(emotional_markers)
                
                # Check if it's structured output format
                if isinstance(parsed, dict) and 'emotional_markers' in parsed:
                    obj = parsed.get('emotional_markers', [])
                    if isinstance(obj, list):
                        display_markers = [str(x) for x in obj]
                    else:
                        display_markers = [str(obj)]
                elif isinstance(parsed, dict) and 'markers' in parsed:
                    display_markers = [m.get('emotion', str(m)) if isinstance(m, dict) else str(m) for m in parsed.get('markers', [])]
                elif isinstance(parsed, list):
                    display_markers = [
                        str(item.get('emotion', item)) if isinstance(item, dict) else str(item) 
                        for item in parsed
                    ]
                else:
                    display_markers = [
                        parsed.get('emotion', str(parsed)) if isinstance(parsed, dict) else str(parsed)
                    ]
            except (json.JSONDecodeError, ValueError, TypeError):
                # Fallback: treat as simple string
                display_markers = [emotional_markers]
        elif isinstance(emotional_markers, list):
            display_markers = [str(x) for x in emotional_markers]
        
        if display_markers:
            st.info(_tr("wellbeing_ui.active.emotion_markers", "😊 Emotionale Marker: {markers}", markers=", ".join(display_markers)))
    
    def _render_session_actions(
        self,
        end_session_func: Callable[[], None],
        show_session_notes_func: Optional[Callable[[], None]]
    ) -> None:
        """
        Render session action buttons.
        
        Args:
            end_session_func: Function to end session
            show_session_notes_func: Optional function to show session notes
        """
        st.divider()
        col1, col2, col3 = st.columns(3)
        
        with col1:
            if st.button(_tr("wellbeing_ui.active.pause_button", "⏸️ Session pausieren"), key="pause_psych_session"):
                st.session_state.psych_enabled = False
                st.success(_tr("wellbeing_ui.active.pause_success", "Session pausiert. Sie koennen jederzeit fortfahren."))
                st.rerun()
        
        with col2:
            if show_session_notes_func and st.button(_tr("wellbeing_ui.active.notes_button", "📝 Session-Notizen"), key="session_notes"):
                show_session_notes_func()
        
        with col3:
            if st.button(_tr("wellbeing_ui.active.end_button", "🛑 Session beenden"), key="end_psych_session"):
                # Set flag for session end dialog
                st.session_state.show_end_session_dialog = True
                st.rerun()

