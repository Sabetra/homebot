"""
Welcome interface UI rendering for psychological sessions.

This module handles the UI rendering for the welcome interface when
no session is active.

Extracted from wellbeing_session_interface.py as part of Phase 5 refactoring.
"""

import logging
from typing import Any, Callable, Optional
import streamlit as st
from i18n import t as i18n_t

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


class WelcomeRenderer:
    """Renders welcome interface UI components."""
    
    def __init__(self) -> None:
        """Initialize welcome renderer."""
        pass
        
    def render_welcome_interface(
        self,
        create_session_func: Optional[Callable[[], None]] = None
    ) -> None:
        """
        Render the welcome interface when no session is active.
        
        Args:
            create_session_func: Optional function to create and start new session
        """
        st.markdown(_tr("wellbeing_ui.welcome.header", "### 🌟 Willkommen bei Wellbeing & Reflexion"))
        
        st.markdown(
            _tr(
                "wellbeing_ui.welcome.intro",
                """**Hier findest du einfühlsame Unterstützung für Wellbeing & Reflexion:**

🧠 **Persönliche Gespräche** - deine Sessions werden individuell gespeichert
💬 **Kontinuierlicher Dialog** - Förderung früherer Gespräche möglich
🔒 **Vollständige Privatsphäre** - deine Daten bleiben sicher, lokal und vertraulich
📊 **Emotionales Tracking** - Beobachtung deines emotionalen Fortschritts

**So beginnst du:**
1. Gib oben deinen Namen ein
2. Wähle eine bestehende Session oder starte eine neue
3. Beginne dein vertrauliches Gespräch"""
            )
        )
        
        # Quick start option
        if st.session_state.wellbeing_current_user and create_session_func:
            st.markdown(_tr("wellbeing_ui.welcome.quickstart", "### 🚀 Schnellstart"))
            if st.button(_tr("wellbeing_ui.welcome.start_button", "💬 Neue Wellbeing-Session starten"), key="quick_start_psych"):
                create_session_func()

