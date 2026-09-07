"""
Session End Service — Encapsulates the session-ending workflow.

Extracted from ``WellbeingSessionInterface._end_current_session``
(115 lines of mixed UI + business logic) into a dedicated service.

Responsibilities:
1. Render the session-end confirmation UI (Streamlit widgets).
2. Optionally extract user insights before closing.
3. End the session via the session manager.
4. Reset relevant ``st.session_state`` flags.

This service is Streamlit-aware by design (renders UI), but all
business logic (insight extraction, session closing) is delegated to
injected dependencies so they can be tested independently.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Dict, List, Optional

import streamlit as st
from i18n import t as i18n_t

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


class SessionEndService:
    """Manages the session-ending workflow (UI + business logic)."""

    def __init__(
        self,
        session_manager: Any,
        insight_extractor_fn: Optional[Callable[[str], Dict[str, Any]]] = None,
        profile_cache: Optional[Any] = None,
    ) -> None:
        """
        Args:
            session_manager: ``SessionManagerAdapter`` with ``.end_session()``.
            insight_extractor_fn: Callable that takes a ``session_id`` and returns
                a dict of ``{insight_type: [insights…]}``.  *None* disables insight
                extraction.
            profile_cache: ``ProfileCacheManager`` for cache invalidation on
                session end.  *None* disables invalidation.
        """
        self._session_manager = session_manager
        self._extract_insights = insight_extractor_fn
        self._profile_cache = profile_cache

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def render_end_session_dialog(self) -> None:
        """Render the session-end confirmation dialog.

        Reads/writes ``st.session_state`` keys:
        - ``wellbeing_current_session``
        - ``wellbeing_enabled``
        - ``show_insight_info``
        - ``show_end_session_dialog``
        """
        session_id: Optional[str] = st.session_state.get("wellbeing_current_session")
        if not session_id:
            return

        st.markdown("---")
        st.markdown(_tr("wellbeing_ui.end.header", "### 📊 Session-Abschluss"))

        # ------ options row ------
        col1, col2 = st.columns([3, 1])
        with col1:
            extract_insights = st.checkbox(
                _tr("wellbeing_ui.end.extract_checkbox", "🧠 User Insights automatisch extrahieren"),
                value=True,
                help=_tr(
                    "wellbeing_ui.end.extract_help",
                    "Analysiert die Session und erkennt wichtige Muster (life_events, coping_mechanisms, etc.).",
                ),
            )
        with col2:
            if st.button(_tr("wellbeing_ui.end.info_button", "ℹ️ Info"), key="insight_info_btn"):
                st.session_state.show_insight_info = not st.session_state.get(
                    "show_insight_info", False
                )

        # ------ info text ------
        if st.session_state.get("show_insight_info", False):
            st.info(
                _tr(
                    "wellbeing_ui.end.info_text",
                    "**User Insights** sind langfristige Muster, die automatisch erkannt werden:\n\n"
                    "- 🎯 **Life Events**: Wichtige Lebensereignisse (Job, Beziehung, etc.)\n"
                    "- 🛠️ **Coping Mechanisms**: Bewaeltigungsstrategien\n"
                    "- 🔄 **Recurring Themes**: Wiederkehrende Themen\n"
                    "- 💭 **Emotional Patterns**: Emotionale Muster\n\n"
                    "Diese Insights helfen mir, dich langfristig besser zu unterstuetzen."
                )
            )

        # ------ action buttons ------
        col_cancel, col_confirm = st.columns([1, 1])

        with col_cancel:
            if st.button(
                _tr("wellbeing_ui.end.back_button", "↩️ Zurueck zur Session"),
                key="cancel_end_session",
                width='stretch',
            ):
                st.session_state.show_end_session_dialog = False
                st.rerun()

        with col_confirm:
            if st.button(
                _tr("wellbeing_ui.end.confirm_button", "✅ Session jetzt beenden"),
                type="primary",
                key="confirm_end_session",
                width='stretch',
            ):
                self._execute_session_end(session_id, extract_insights)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _execute_session_end(self, session_id: str, extract_insights: bool) -> None:
        """Run insight extraction (optional) → close session → reset state."""
        try:
            # 1. Optionally extract insights
            if extract_insights and self._extract_insights is not None:
                self._show_insights(session_id)
            elif not extract_insights:
                st.success(_tr("wellbeing_ui.end.success_no_extract", "✅ Session erfolgreich beendet (ohne Insight-Extraktion)."))

            # 2. Close the session
            logger.info(f"🔚 Ending session {session_id}")
            self._session_manager.end_session(session_id)

            # 3. Invalidate profile cache so next session re-synthesizes
            self._invalidate_profile_cache(session_id)

            # 4. Reset session state
            self._reset_session_state()

            logger.info("✅ Session ended successfully — state reset")
            st.info(_tr("wellbeing_ui.end.after_info", "💡 Sie koennen jederzeit eine neue Session starten oder eine vorherige fortsetzen."))

            time.sleep(0.5)
            st.rerun()

        except Exception as exc:
            logger.error(f"❌ Error ending session: {exc}", exc_info=True)
            st.error(_tr("wellbeing_ui.end.error", "❌ Fehler beim Beenden: {error}", error=exc))
            self._reset_session_state()
            st.rerun()

    def _show_insights(self, session_id: str) -> None:
        """Extract and display insights for *session_id*."""
        assert self._extract_insights is not None

        with st.spinner(_tr("wellbeing_ui.end.extract_spinner", "📊 Analysiere Session und extrahiere Insights...")):
            insights_dict = self._extract_insights(session_id)

        if not insights_dict:
            st.info(_tr("wellbeing_ui.end.no_insights", "ℹ️ Session beendet. Keine neuen hochqualitativen Insights erkannt (Session evtl. zu kurz)."))
            return

        total = sum(len(v) for v in insights_dict.values())
        if total == 0:
            st.info(_tr("wellbeing_ui.end.no_insights", "ℹ️ Session beendet. Keine neuen hochqualitativen Insights erkannt (Session evtl. zu kurz)."))
            return

        st.success(_tr("wellbeing_ui.end.success_with_insights", "✅ Session beendet! {count} neue Insights erkannt.", count=total))

        _ICONS = {
            "life_event": "🎯",
            "coping_mechanism": "🛠️",
            "personality": "🧩",
            "personality_trait": "🧩",
            "behavioral_pattern": "🔄",
            "emotional_state": "💭",
            "relationship_dynamic": "👥",
            "cognitive_pattern": "💡",
        }

        with st.expander(_tr("wellbeing_ui.end.insights_expander", "🔍 Erkannte Insights nach Kategorie")):
            for itype, insights in insights_dict.items():
                if not insights:
                    continue
                icon = _ICONS.get(itype, "📌")
                st.markdown(f"**{icon} {_insight_type_label(itype)}:**")
                for idx, insight in enumerate(insights[:3], 1):
                    confidence = getattr(insight, "confidence", 0.0)
                    content = (
                        getattr(insight, "content", None)
                        or getattr(insight, "description", None)
                        or getattr(insight, "value", None)
                        or str(insight)
                    )
                    st.markdown(_tr("wellbeing_ui.end.insight_item", "{idx}. {content} (Konfidenz: {confidence})", idx=idx, content=content, confidence=f"{confidence:.0%}"))

    def _invalidate_profile_cache(self, session_id: str) -> None:
        """Invalidate persistent profile cache so next session re-synthesizes."""
        if self._profile_cache is None:
            return
        try:
            user_id = st.session_state.get('wellbeing_current_user_id')
            if not user_id:
                logger.info("ℹ️ Kein user_id in session_state — Profile-Invalidierung übersprungen")
                return
            self._profile_cache.invalidate_profile(
                user_id=user_id,
                trigger_type='session_end',
                trigger_source_id=session_id,
            )
            logger.info(f"✅ Profil-Cache invalidiert für user {user_id} (Session {session_id[:12]}...)")
        except Exception as exc:
            logger.warning(f"⚠️ Profil-Cache-Invalidierung fehlgeschlagen: {exc}")

    @staticmethod
    def _reset_session_state() -> None:
        """Reset all session-state keys related to the active session."""
        st.session_state.wellbeing_current_session = None
        st.session_state.wellbeing_enabled = False
        st.session_state.show_insight_info = False
        st.session_state.show_end_session_dialog = False

