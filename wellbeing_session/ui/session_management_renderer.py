"""
Session management UI rendering for psychological sessions.

This module handles the UI rendering for session management including:
- User selection
- Session creation and management
- Current session information display
- Session history display

Extracted from wellbeing_session_interface.py as part of Phase 5 refactoring.
"""

import logging
from typing import Any, Dict, List, Optional
from datetime import datetime, timezone
import streamlit as st
from i18n import t as i18n_t
from wellbeing_session.utils.datetime_utils import normalize_datetime

logger = logging.getLogger(__name__)


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


def _insight_type_label(insight_type: str) -> str:
    normalized = str(insight_type or "unknown").strip().lower()
    default_label = normalized.replace("_", " ").title()
    return _tr(f"wellbeing_ui.insight_types.{normalized}", default_label)


class SessionManagementRenderer:
    """Renders session management UI components."""
    
    def __init__(self, session_manager: Any, profile_cache: Optional[Any] = None) -> None:
        """
        Initialize session management renderer.
        
        Args:
            session_manager: Session manager instance
            profile_cache: ProfileCacheManager for cache invalidation on user switch
        """
        self.session_manager = session_manager
        self._profile_cache = profile_cache
        
    def render_session_management_ui(self) -> bool:
        """
        Render the session management UI.
        
        Returns:
            True if session is active and enabled, False otherwise
        """
        st.header(_tr("wellbeing_ui.session.header", "🧠 Wellbeing & Reflexion - Sitzungsverwaltung"))
        
        # User selection
        user_name = self._render_user_selection()
        
        if not user_name:
            st.info(_tr("wellbeing_ui.session.enter_name_info", "👆 Bitte geben Sie zuerst Ihren Namen ein, um eine Session zu starten."))
            return False
        
        # Session management buttons
        st.markdown("---")
        self._render_session_buttons(user_name)
        
        # Current session info
        if st.session_state.wellbeing_current_session:
            self._display_current_session_info()
        
        # Session history
        self._display_session_history(user_name)
        self._display_user_insights(user_name)
        
        # Explicit bool cast for type safety
        wellbeing_enabled = bool(st.session_state.wellbeing_enabled) if hasattr(st.session_state, 'wellbeing_enabled') else False
        wellbeing_current = bool(st.session_state.wellbeing_current_session) if hasattr(st.session_state, 'wellbeing_current_session') else False
        return wellbeing_enabled and wellbeing_current
    
    def _render_user_selection(self) -> Optional[str]:
        """
        Render user selection input.
        
        Returns:
            Selected user name or None
        """
        col1, col2 = st.columns([2, 1])
        
        with col1:
            user_name = st.text_input(
                _tr("wellbeing_ui.session.name_label", "👤 Ihr Name fuer diese Session:"),
                value=st.session_state.wellbeing_current_user,
                placeholder=_tr("wellbeing_ui.session.name_placeholder", "Geben Sie Ihren Namen ein..."),
                help=_tr("wellbeing_ui.session.name_help", "Ihr Name wird verwendet, um Ihre Sessions zu verwalten und von anderen zu trennen."),
            )
        
        with col2:
            st.markdown(_tr("wellbeing_ui.session.privacy_header", "### 🔒 Datenschutz"))
            st.caption(_tr("wellbeing_ui.session.privacy_local", "✅ Alle Daten bleiben lokal"))
            st.caption(_tr("wellbeing_ui.session.privacy_separation", "✅ Sichere Session-Trennung"))
            st.caption(_tr("wellbeing_ui.session.privacy_no_cloud", "✅ Keine Cloud-Uebertragung"))
        
        # Handle user name change
        if user_name and user_name != st.session_state.wellbeing_current_user:
            old_user_id = st.session_state.get('wellbeing_current_user_id', '')
            # ✅ SOTA: Invalidate cached profile for previous user BEFORE switching,
            # otherwise User B starts session with cached User A profile until TTL expires.
            if old_user_id and self._profile_cache is not None:
                try:
                    self._profile_cache.invalidate_profile(
                        user_id=old_user_id,
                        trigger_type='user_switch',
                    )
                    logger.info(f"🔑 Profile cache invalidated on user switch (old={old_user_id[:12]}...)")
                except Exception as exc:
                    logger.warning(f"⚠️ Profile cache invalidation on user switch failed: {exc}")
            st.session_state.wellbeing_current_user = user_name
            st.session_state.wellbeing_current_user_id = self._resolve_user_id(user_name)
            st.session_state.wellbeing_current_session = ""
            st.rerun()

        if user_name and not st.session_state.get("wellbeing_current_user_id"):
            st.session_state.wellbeing_current_user_id = self._resolve_user_id(user_name)
        
        return user_name if user_name else None
    
    def _render_session_buttons(self, user_name: str) -> None:
        """
        Render session management buttons.
        
        Args:
            user_name: Current user name
        """
        col1, col2, col3 = st.columns([2, 1, 1])
        
        with col1:
            if st.button(_tr("wellbeing_ui.session.new_button", "🚀 Neue Session starten"), type="primary"):
                self._start_new_session(user_name)
        
        with col2:
            if st.button(_tr("wellbeing_ui.session.restore_button", "📋 Bestehende Session")):
                self._restore_session(user_name)
        
        with col3:
            if st.session_state.wellbeing_current_session:
                if st.button(_tr("wellbeing_ui.session.end_button", "⏹️ Session beenden"), type="secondary"):
                    self._end_session()
    
    def _start_new_session(self, user_name: str) -> None:
        """Start a new psychological session."""
        try:
            # Force new session (no restoration)
            session_id = self.session_manager.create_session(user_name, force_new=True)
            st.session_state.wellbeing_current_user_id = self._resolve_user_id(user_name)
            st.session_state.wellbeing_current_session = session_id
            st.session_state.wellbeing_enabled = True
            st.session_state.show_end_session_dialog = False
            st.success(_tr("wellbeing_ui.session.new_success", "✅ Neue Session gestartet: {id}...", id=session_id[:8]))
            st.rerun()
        except Exception as e:
            st.error(_tr("wellbeing_ui.session.new_error", "❌ Fehler beim Starten der Session: {error}", error=e))
    
    def _restore_session(self, user_name: str) -> None:
        """Restore existing session."""
        try:
            # Standard behavior: restore existing session
            session_id = self.session_manager.get_or_create_session(user_name, force_new=False)
            st.session_state.wellbeing_current_user_id = self._resolve_user_id(user_name)
            st.session_state.wellbeing_current_session = session_id
            st.session_state.wellbeing_enabled = True
            st.session_state.show_end_session_dialog = False
            st.success(_tr("wellbeing_ui.session.restore_success", "✅ Session wiederhergestellt: {id}...", id=session_id[:8]))
            st.rerun()
        except Exception as e:
            st.error(_tr("wellbeing_ui.session.restore_error", "❌ Fehler beim Wiederherstellen: {error}", error=e))
    
    def _end_session(self) -> None:
        """Open the unified session-end workflow instead of closing directly."""
        st.session_state.show_end_session_dialog = True
        st.rerun()
    
    def _display_current_session_info(self) -> None:
        """Display information about current session."""
        session_id = st.session_state.wellbeing_current_session
        
        try:
            summary = self.session_manager.get_session_summary(session_id)
            if not summary:
                return

            if summary.get('user_id'):
                st.session_state.wellbeing_current_user_id = summary.get('user_id')
            
            st.markdown(_tr("wellbeing_ui.session.current_header", "### 📊 Aktuelle Session"))
            
            # Metrics row
            col1, col2, col3, col4 = st.columns(4)
            
            with col1:
                st.metric(_tr("wellbeing_ui.session.metric_messages", "💬 Nachrichten"), summary.get('message_count', 0))
            
            with col2:
                start_time = normalize_datetime(summary.get('start_time', datetime.now(timezone.utc)))
                current_time = datetime.now(timezone.utc)
                duration = current_time - start_time
                hours = int(duration.total_seconds() // 3600)
                minutes = int((duration.total_seconds() % 3600) // 60)
                st.metric(_tr("wellbeing_ui.session.metric_duration", "⏱️ Dauer"), _tr("wellbeing_ui.session.duration_value", "{hours}h {minutes}m", hours=hours, minutes=minutes))
            
            with col3:
                st.metric(_tr("wellbeing_ui.session.metric_mood", "😊 Stimmung"), summary.get('emotional_state', 'neutral'))
            
            with col4:
                st.metric(_tr("wellbeing_ui.session.metric_session", "🆔 Session"), session_id[:8] + "...")
            
            # Key topics
            key_topics = summary.get('key_topics', [])
            if key_topics:
                st.markdown(_tr("wellbeing_ui.session.topics_header", "**🏷️ Hauptthemen:**"))
                for topic in key_topics:
                    st.badge(topic)
            
            # Session summary text
            session_summary_text = summary.get('session_summary', '')
            if session_summary_text:
                st.markdown(_tr("wellbeing_ui.session.summary_line", "**📝 Zusammenfassung:** {summary}", summary=session_summary_text))
            
        except Exception as e:
            st.error(_tr("wellbeing_ui.session.summary_error", "❌ Fehler beim Laden der Session-Info: {error}", error=e))
    
    def _display_session_history(self, user_name: str) -> None:
        """
        Display session history for user.
        
        Args:
            user_name: User name to get history for
        """
        try:
            sessions = self.session_manager.get_user_sessions(user_name, limit=5)
            
            if not sessions:
                return
            
            st.markdown(_tr("wellbeing_ui.session.history_header", "### 📚 Ihre letzten Sessions"))
            
            for session in sessions:
                # Dictionary access instead of attribute
                is_active = not session.get('end_time')
                start_time_str = session.get('created_at', session.get('start_time', ''))
                
                # Parse datetime if string (timezone-aware)
                if isinstance(start_time_str, str):
                    try:
                        start_time = datetime.fromisoformat(start_time_str.replace('Z', '+00:00'))
                    except (ValueError, TypeError):
                        start_time = datetime.now(timezone.utc)
                else:
                    start_time = normalize_datetime(start_time_str)
                
                # Format display
                status_emoji = "🟢" if is_active else "⚪"
                session_identifier = session.get('id') or session.get('session_id') or 'N/A'
                st.write(
                    f"{status_emoji} **Session {session_identifier[:8]}...** "
                    f"({start_time.strftime('%Y-%m-%d %H:%M')})"
                )
                
                # Show message count if available
                message_count = session.get('interaction_count', session.get('message_count', 0))
                if message_count > 0:
                    st.caption(_tr("wellbeing_ui.session.history_messages", "💬 {count} Nachrichten", count=message_count))
            
        except Exception as e:
            logger.error(f"❌ Fehler beim Laden der Session-Historie: {e}")

    def _display_user_insights(self, user_name: str) -> None:
        """Display persisted insights for exactly the currently selected user."""
        try:
            resolved_user_id = self._resolve_user_id(user_name)
            if not resolved_user_id:
                return

            st.markdown(_tr("wellbeing_ui.session.insights_header", "### 🧠 Gespeicherte Insights"))
            st.caption(
                _tr(
                    "wellbeing_ui.session.insights_caption",
                    "Es werden ausschliesslich Insights des aktuell ausgewaehlten Users geladen. Geschlossene Sessions bleiben hier weiterhin einsehbar.",
                )
            )

            all_sessions = self.session_manager.get_user_sessions(user_name, limit=100, status=None)
            all_insights = self.session_manager.get_user_insights(user_name, limit=None)

            if not all_insights:
                st.info(_tr("wellbeing_ui.session.no_insights", "ℹ️ Fuer diesen User sind bisher keine gespeicherten Insights vorhanden."))
                return

            session_map = {
                session.get('id') or session.get('session_id'): session
                for session in all_sessions
                if session.get('id') or session.get('session_id')
            }
            insights_per_session = self._count_insights_by_session(all_insights)

            session_options: Dict[str, Optional[str]] = {"Alle Sessions": None}
            for session_id, count in insights_per_session.items():
                session = session_map.get(session_id, {'id': session_id})
                label = self._format_session_filter_label(session, count)
                session_options[label] = session_id

            selected_label = st.selectbox(
                _tr("wellbeing_ui.session.scope_label", "📂 Insight-Scope"),
                options=list(session_options.keys()),
                key=f"wellbeing_insight_scope_{resolved_user_id}",
                help=_tr("wellbeing_ui.session.scope_help", "Filtert die gespeicherten Insights auf eine konkrete Session dieses Users."),
            )
            selected_session_id = session_options[selected_label]

            insights = (
                all_insights
                if selected_session_id is None
                else self.session_manager.get_user_insights(
                    user_name,
                    session_id=selected_session_id,
                    limit=None,
                )
            )

            if not insights:
                st.info(_tr("wellbeing_ui.session.scope_empty", "ℹ️ Fuer den gewaehlten Scope wurden keine Insights gefunden."))
                return

            self._render_insight_metrics(insights)
            self._render_grouped_insights(insights, session_map)

        except Exception as e:
            logger.error(f"❌ Fehler beim Laden der gespeicherten Insights: {e}", exc_info=True)
            st.error(_tr("wellbeing_ui.session.insights_error", "❌ Fehler beim Laden der gespeicherten Insights: {error}", error=e))

    def _resolve_user_id(self, user_name: str) -> str:
        """Resolve the selected user name to the canonical stored user id."""
        if hasattr(self.session_manager, 'resolve_user_id'):
            return str(self.session_manager.resolve_user_id(user_name))
        return str(user_name)

    def _count_insights_by_session(self, insights: list[dict[str, Any]]) -> dict[str, int]:
        """Count insights per session for filter labels and overview."""
        counts: dict[str, int] = {}
        for insight in insights:
            session_id = insight.get('session_id')
            if not session_id:
                continue
            counts[session_id] = counts.get(session_id, 0) + 1
        return counts

    def _format_session_filter_label(self, session: dict[str, Any], insight_count: int) -> str:
        """Create a readable selectbox label for one session."""
        session_id = session.get('id') or session.get('session_id') or 'N/A'
        created_raw = session.get('created_at') or session.get('start_time')
        created = normalize_datetime(created_raw) if created_raw else datetime.now(timezone.utc)
        is_active = not session.get('end_time')
        status = "🟢 aktiv" if is_active else "⚪ beendet"
        return (
            f"{status} · {created.strftime('%Y-%m-%d %H:%M')} · "
            f"{session_id[:8]}... · {insight_count} Insights"
        )

    def _render_insight_metrics(self, insights: list[dict[str, Any]]) -> None:
        """Render compact overview metrics for the selected user scope."""
        total_insights = len(insights)
        session_count = len({insight.get('session_id') for insight in insights if insight.get('session_id')})
        avg_confidence = sum(float(insight.get('confidence') or 0.0) for insight in insights) / total_insights
        timestamps = [
            normalize_datetime(insight.get('created_at'))
            for insight in insights
            if insight.get('created_at')
        ]
        latest_timestamp = max(timestamps) if timestamps else datetime.now(timezone.utc)

        col1, col2, col3, col4 = st.columns(4)
        with col1:
            st.metric(_tr("wellbeing_ui.session.metric_insights", "🧠 Insights"), total_insights)
        with col2:
            st.metric(_tr("wellbeing_ui.session.metric_sessions", "📚 Sessions"), session_count)
        with col3:
            st.metric(_tr("wellbeing_ui.session.metric_confidence", "🎯 Ø Konfidenz"), f"{avg_confidence:.0%}")
        with col4:
            st.metric(_tr("wellbeing_ui.session.metric_latest", "🕒 Neueste"), latest_timestamp.strftime('%Y-%m-%d'))

    def _render_grouped_insights(
        self,
        insights: list[dict[str, Any]],
        session_map: dict[str, dict[str, Any]],
    ) -> None:
        """Render normalized stored insights grouped by type."""
        grouped: dict[str, list[dict[str, Any]]] = {}
        for insight in insights:
            insight_type = str(insight.get('insight_type') or 'unknown')
            grouped.setdefault(insight_type, []).append(insight)

        icons = {
            'life_event': '🎯',
            'coping_mechanism': '🛠️',
            'personality': '🧩',
            'personality_trait': '🧩',
            'behavioral_pattern': '🔄',
            'emotional_state': '💭',
            'relationship_dynamic': '👥',
            'cognitive_pattern': '💡',
        }

        for insight_type in sorted(grouped.keys()):
            items = sorted(
                grouped[insight_type],
                key=lambda item: normalize_datetime(item.get('created_at')),
                reverse=True,
            )
            icon = icons.get(insight_type, '📌')
            with st.expander(f"{icon} {_insight_type_label(insight_type)} ({len(items)})", expanded=False):
                for index, insight in enumerate(items, start=1):
                    self._render_single_insight(index, insight, session_map)

    def _render_single_insight(
        self,
        index: int,
        insight: dict[str, Any],
        session_map: dict[str, dict[str, Any]],
    ) -> None:
        """Render one persisted insight entry with all relevant metadata."""
        session_id = insight.get('session_id', '')
        session = session_map.get(session_id, {'id': session_id})
        session_label = self._format_session_reference(session)

        description = insight.get('description') or insight.get('value') or ''
        confidence = float(insight.get('confidence') or 0.0)
        temporal_context = insight.get('temporal_context') or 'unknown'
        created_at = normalize_datetime(insight.get('created_at')) if insight.get('created_at') else None
        evidence = insight.get('evidence') or []

        st.markdown(f"**{index}. {description}**")
        meta_parts = [
            _tr("wellbeing_ui.session.meta_confidence", "Konfidenz: {value}", value=f"{confidence:.0%}"),
            _tr("wellbeing_ui.session.meta_context", "Kontext: {value}", value=temporal_context),
            _tr("wellbeing_ui.session.meta_session", "Session: {value}", value=session_label),
        ]
        if created_at is not None:
            meta_parts.append(_tr("wellbeing_ui.session.meta_detected", "Erkannt: {value}", value=created_at.strftime('%Y-%m-%d %H:%M')))
        st.caption(" · ".join(meta_parts))

        category = insight.get('category')
        if category:
            st.caption(_tr("wellbeing_ui.session.meta_category", "Kategorie: {value}", value=category))

        if evidence:
            st.markdown(_tr("wellbeing_ui.session.evidence_header", "**Belege:**"))
            for evidence_item in evidence:
                st.markdown(f"- {evidence_item}")

        session_summary = session.get('session_summary') or insight.get('session_summary')
        if session_summary:
            st.caption(_tr("wellbeing_ui.session.summary_caption", "Session-Zusammenfassung: {value}", value=session_summary))

        st.markdown("---")

    def _format_session_reference(self, session: dict[str, Any]) -> str:
        """Create a compact session reference label for insight rows."""
        session_id = session.get('id') or session.get('session_id') or 'N/A'
        created_raw = session.get('created_at') or session.get('start_time')
        if created_raw:
            created = normalize_datetime(created_raw)
            return f"{session_id[:8]}... ({created.strftime('%Y-%m-%d')})"
        return f"{session_id[:8]}..."

