"""
Chat input handling for psychological sessions.

This module handles chat input processing including:
- User input validation
- Emotional analysis
- AI response generation
- Context management
- Session updates

Extracted from wellbeing_session_interface.py as part of Phase 4 refactoring.
"""

import logging
from typing import Dict, Any, List, Callable, Optional
import streamlit as st
from i18n import t as i18n_t

logger = logging.getLogger(__name__)

# ─── User feedback detection for risk assessment pause ───────────────────
# Phrases that signal the user wants to stop risk assessment interruptions.
# These are checked case-insensitively as substrings.
_RISK_PAUSE_PHRASES_DE = [
    "lass das",
    "nicht wieder",
    "hör auf mit den fragen",
    "hör auf mit der abfrage",
    "mach nicht immer diese abfrage",
    "lass mich einfach reden",
    "ich brauche das nicht",
    "das nervt",
    "das stört",
    "keine risikoprüfung",
    "keine sicherheitsabfrage",
]

_RISK_PAUSE_PHRASES_EN = [
    "stop asking",
    "stop checking",
    "leave me alone",
    "don't ask me that",
    "i don't need this",
    "that's annoying",
    "no risk check",
    "no safety check",
]


def _should_pause_risk_assessment(user_input: str) -> bool:
    """Check if the user wants to pause risk assessment interruptions."""
    lower = user_input.lower()
    for phrase in _RISK_PAUSE_PHRASES_DE + _RISK_PAUSE_PHRASES_EN:
        if phrase in lower:
            return True
    return False


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


def build_crisis_response(risk_level: Optional[str]) -> str:
    """Build the localized safety response without free-form generation."""
    immediate = _tr(
        "wellbeing.crisis.immediate",
        "Wenn unmittelbare Gefahr besteht, rufen Sie jetzt den lokalen Notruf an oder gehen Sie in die nächste Notaufnahme.",
    )
    header = _tr(
        "wellbeing.crisis.header",
        "**Krisenhinweis:** Ihre Sicherheit hat jetzt Vorrang.",
    )
    intro = _tr(
        "wellbeing.crisis.intro",
        "Bitte nehmen Sie jetzt Kontakt zu professioneller Hilfe auf:",
    )
    line1 = (
        f"- **{_tr('wellbeing.crisis.line1_name', 'Krisenhilfe')}**: "
        f"{_tr('wellbeing.crisis.line1_number', 'lokaler Krisendienst')} "
        f"({_tr('wellbeing.crisis.line1_desc', '24/7')})"
    )
    line2 = (
        f"- **{_tr('wellbeing.crisis.line2_name', 'Notfallhilfe')}**: "
        f"{_tr('wellbeing.crisis.line2_number', 'lokaler Notruf')}"
    )
    line3 = (
        f"- **{_tr('wellbeing.crisis.line3_name', 'Weitere Hilfen')}**: "
        f"{_tr('wellbeing.crisis.line3_url', 'findahelpline.com')}"
    )
    safety_question = _tr(
        "wellbeing.crisis.safety_question",
        "Sind Sie im Moment unmittelbar in Gefahr, und können Sie jetzt eine vertraute Person zu sich holen?",
    )
    closing = _tr(
        "wellbeing.crisis.closing",
        "Sie müssen damit nicht allein bleiben.",
    )
    severity = immediate + "\n\n" if risk_level == "acute" else ""
    return (
        f"{header}\n\n{severity}{intro}\n{line1}\n{line2}\n{line3}\n\n"
        f"{safety_question}\n\n{closing}"
    )


def build_safety_check_response() -> str:
    """Build the single concise check used for an elevated safety episode."""
    intro = _tr(
        "wellbeing.crisis.check_intro",
        "Bevor wir bei Ihrem aktuellen Thema bleiben, möchte ich einmal kurz Ihre unmittelbare Sicherheit klären.",
    )
    question = _tr(
        "wellbeing.crisis.safety_question",
        "Sind Sie im Moment unmittelbar in Gefahr, und können Sie jetzt eine vertraute Person zu sich holen?",
    )
    return f"{intro}\n\n{question}"


class ChatInputHandler:
    """Handles chat input processing for psychological sessions."""
    
    def __init__(
        self, 
        session_manager: Any, 
        emotional_analyzer: Any, 
        context_manager: Any, 
        chat_logic: Optional[Any] = None
    ) -> None:
        """
        Initialize chat input handler.
        
        Args:
            session_manager: Session manager instance
            emotional_analyzer: Emotional analyzer instance
            context_manager: Context manager instance
            chat_logic: Optional chat logic instance
        """
        self.session_manager = session_manager
        self.emotional_analyzer = emotional_analyzer
        self.context_manager = context_manager
        self.chat_logic = chat_logic
        
    def handle_wellbeing_chat_input(
        self, 
        user_input: str, 
        generate_response_func: Callable[[str], str]
    ) -> None:
        """
        Process psychological chat inputs with context management.

        Kein Chat-Block: Risk-Information fließt nur als Kontext in den
        Care-Dialog ein — der Bot begleitet durch Krisen statt
        abzublocken.

        Args:
            user_input: User input text
            generate_response_func: Function to generate psychological response
        """
        try:
            # Check context status before processing
            self._check_context_status()
            
            # Check for user feedback to pause risk assessment
            if _should_pause_risk_assessment(user_input):
                logger.info(
                    "User-Feedback erkannt: Risikobewertung pausieren (session=%s...)",
                    session_id[:12] if (session_id := st.session_state.wellbeing_current_session) else "N/A",
                )
                try:
                    treatment_mgr = getattr(self.session_manager, "treatment_manager", None)
                    if treatment_mgr is not None:
                        session_id_cur = st.session_state.wellbeing_current_session
                        if session_id_cur:
                            treatment_mgr.pause_risk_assessment(session_id_cur, turns=10)
                except Exception as exc:  # noqa: BLE001
                    logger.debug("[chat_handler] pause_risk_assessment failed: %s", exc)
            
            # Perform emotional analysis FIRST (for user message)
            user_emotional_markers = self._analyze_user_emotion(user_input)
            
            session_id = st.session_state.wellbeing_current_session
            user_write = self.session_manager.add_message_with_result(
                session_id,
                role="user",
                content=user_input,
                emotional_markers=user_emotional_markers
            )
            if not user_write.success or not user_write.session_id:
                logger.error("User-Nachricht konnte nicht gespeichert werden: %s", user_write.error)
                st.error(
                    _tr(
                        "wellbeing_ui.chat.user_save_failed",
                        "Die Nachricht konnte nicht gespeichert werden. Bitte starten Sie die Session neu und versuchen Sie es erneut.",
                    )
                )
                return

            session_id = user_write.session_id
            if st.session_state.wellbeing_current_session != session_id:
                st.session_state.wellbeing_current_session = session_id
                logger.warning("Psychologische Session nach User-Write auf %s... neu gebunden", session_id[:12])
            
            # ── Kein Block: Risk-Info fließt nur als Kontext ein ──
            # Der Bot reagiert empathisch auf Risiko, blockiert aber nicht.
            risk_level = user_write.risk_level
            if risk_level in ("elevated", "acute"):
                logger.warning(
                    "Erhöhtes Risiko erkannt (risk=%s, session=%s...) — empathischer Begleitungspfad",
                    risk_level,
                    session_id[:12],
                )
            
            with st.spinner("🧠 Psychologische Analyse und Antwort..."):
                ai_response = generate_response_func(user_input)
            
            # Validate that a response was generated
            if not self._validate_response(ai_response):
                return
            
            logger.info(f"✅ AI-Antwort generiert: {len(ai_response)} Zeichen")
            
            # Emotional analysis for assistant response
            assistant_emotional_markers = self._analyze_assistant_emotion(ai_response)
            
            assistant_write = self.session_manager.add_message_with_result(
                session_id,
                role="assistant",
                content=ai_response,
                emotional_markers=assistant_emotional_markers
            )
            if not assistant_write.success or not assistant_write.session_id:
                logger.error("Assistant-Antwort konnte nicht gespeichert werden: %s", assistant_write.error)
                st.error(
                    _tr(
                        "wellbeing_ui.chat.assistant_save_failed",
                        "Die Antwort wurde erzeugt, konnte aber nicht gespeichert werden.",
                    )
                )
                with st.chat_message("assistant"):
                    st.markdown(ai_response)
                return

            if st.session_state.wellbeing_current_session != assistant_write.session_id:
                st.session_state.wellbeing_current_session = assistant_write.session_id
                logger.warning(
                    "Psychologische Session nach Assistant-Write auf %s... neu gebunden",
                    assistant_write.session_id[:12],
                )
            
            logger.info(f"✅ Psychologische Antwort generiert und gespeichert ({len(ai_response)} chars)")
            
            # IMPORTANT: Reload UI so the new message from DB is displayed
            # Streamlit renders chat history BEFORE input is processed,
            # so st.rerun() must be called to show the new message
            st.rerun()
            
        except Exception as e:
            st.error(_tr("wellbeing_ui.chat.error_processing", "❌ Fehler bei der Nachrichtenverarbeitung: {error}", error=e))
            logger.error(f"Psychological chat input error: {e}")

    def _build_crisis_response(self, risk_level: Optional[str]) -> str:
        """Build the localized safety response without free-form generation."""
        return build_crisis_response(risk_level)
    
    def _check_context_status(self) -> None:
        """Check context status before processing."""
        try:
            messages = self.session_manager.get_session_context(
                st.session_state.wellbeing_current_session, 
                max_messages=50
            )
            
            if messages:
                # Convert to LLM format for analysis
                llm_messages = []
                for msg in messages:
                    llm_messages.append({
                        'role': msg.get('role', 'user'),
                        'content': msg.get('content', '')
                    })
                
                # Check if summarization is needed
                if self.context_manager.should_summarize(llm_messages):
                    st.info(
                        _tr(
                            "wellbeing_ui.chat.context_optimization",
                            "🔄 **Context-Optimierung:** Ihr Gespraech wird automatisch zusammengefasst, um die Qualitaet der Antworten zu gewaehrleisten.",
                        )
                    )
                    
        except Exception as e:
            logger.warning(f"Context check failed: {e}")
    
    def _analyze_user_emotion(self, user_input: str) -> List[str]:
        """
        Analyze user emotion from input.
        
        Args:
            user_input: User input text
            
        Returns:
            List of emotional markers
        """
        user_emotional_markers = []
        try:
            # Ensure chat_logic is set (lazy init)
            if not self.emotional_analyzer.chat_logic and self.chat_logic:
                self.emotional_analyzer.chat_logic = self.chat_logic
                logger.info("🔧 Chat Logic in Emotional Analyzer gesetzt (Lazy Init)")
            
            # Analyze user message
            user_emotion_analysis = self.emotional_analyzer.analyze_emotional_state(user_input)
            user_emotional_markers = user_emotion_analysis.get_primary_emotions(threshold=0.3)
            
            logger.info(
                f"🎭 User-Emotionsanalyse: {user_emotion_analysis.dominant_emotion} "
                f"(Konfidenz: {user_emotion_analysis.confidence:.2f})"
            )
        except Exception as e:
            logger.warning(f"⚠️ User-Emotionsanalyse fehlgeschlagen: {e}")
            # Fallback to empty list (not None!)
            user_emotional_markers = []
        
        return user_emotional_markers
    
    def _validate_response(self, ai_response: str) -> bool:
        """
        Validate that a valid response was generated.
        
        Args:
            ai_response: AI response to validate
            
        Returns:
            True if valid, False otherwise
        """
        if not ai_response or not isinstance(ai_response, str) or len(ai_response.strip()) == 0:
            logger.error(f"❌ Keine gültige AI-Antwort generiert! ai_response={ai_response!r}")
            st.error(_tr("wellbeing_ui.chat.no_llm_response", "❌ Fehler: Keine Antwort vom LLM erhalten. Bitte versuchen Sie es erneut."))
            return False
        return True
    
    def _analyze_assistant_emotion(self, ai_response: str) -> List[str]:
        """
        Analyze assistant emotion from response.
        
        Args:
            ai_response: AI response text
            
        Returns:
            List of emotional markers
        """
        assistant_emotional_markers = []
        try:
            # Analyze the generated AI response for emotional tone
            assistant_emotion_analysis = self.emotional_analyzer.analyze_emotional_state(ai_response)
            assistant_emotional_markers = assistant_emotion_analysis.get_primary_emotions(threshold=0.3)
            
            # Show emotion analysis in UI (optional, only with high confidence)
            if assistant_emotion_analysis.confidence > 0.7:
                st.info(
                    _tr(
                        "wellbeing_ui.chat.response_tone",
                        "🎭 **Antwort-Tonfall:** {emotion} ({intensity} Intensitaet)",
                        emotion=assistant_emotion_analysis.dominant_emotion.title(),
                        intensity=assistant_emotion_analysis.intensity_level,
                    )
                )
            
            logger.info(
                f"🎭 Assistant-Emotionsanalyse: {assistant_emotion_analysis.dominant_emotion} "
                f"(Konfidenz: {assistant_emotion_analysis.confidence:.2f})"
            )
            
        except Exception as e:
            logger.warning(f"⚠️ Assistant-Emotionsanalyse fehlgeschlagen: {e}")
            # Fallback to empty list (not None!)
            assistant_emotional_markers = []
        
        return assistant_emotional_markers

