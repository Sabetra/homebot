"""
Message handling for psychological sessions.

This module handles the processing of psychological messages including:
- Message routing and validation
- Emotional analysis integration
- Crisis detection
- Response enhancement
- Session context management

Extracted from wellbeing_session_interface.py as part of Phase 4 refactoring.
"""

import logging
from typing import Dict, Any, List, Callable, Optional
from datetime import datetime, timezone
import streamlit as st

logger = logging.getLogger(__name__)


class MessageHandler:
    """Handles message processing for psychological sessions."""
    
    def __init__(
        self, 
        session_manager: Any, 
        emotional_analyzer: Any, 
        chat_logic: Optional[Any] = None, 
        profile_cache: Optional[Any] = None
    ) -> None:
        """
        Initialize message handler.
        
        Args:
            session_manager: Session manager instance
            emotional_analyzer: Emotional analyzer instance
            chat_logic: Optional chat logic instance
            profile_cache: Optional profile cache instance
        """
        self.session_manager = session_manager
        self.emotional_analyzer = emotional_analyzer
        self.chat_logic = chat_logic
        self.profile_cache = profile_cache
        
    def handle_wellbeing_message(
        self, 
        user_message: str, 
        ai_response: str,
        build_context_func: Callable[..., Dict[str, Any]],
        format_context_func: Callable[[Dict[str, Any]], str]
    ) -> str:
        """
        Process psychological messages with session context.
        
        Args:
            user_message: User message
            ai_response: Standard AI response
            build_context_func: Function to build comprehensive user context
            format_context_func: Function to format context for LLM
            
        Returns:
            Enhanced AI response with psychological context
        """
        if not st.session_state.wellbeing_enabled or not st.session_state.wellbeing_current_session:
            return ai_response
        
        try:
            session_id = st.session_state.wellbeing_current_session
            
            # Analyze emotional markers
            emotional_markers = self.extract_emotional_markers(user_message)
            is_crisis = self.detect_crisis(user_message)
            
            user_write = self.session_manager.add_message_with_result(
                session_id=session_id,
                role="user",
                content=user_message,
                emotional_markers=emotional_markers,
                is_crisis=is_crisis
            )
            if not user_write.success or not user_write.session_id:
                logger.error("User-Nachricht konnte nicht gespeichert werden: %s", user_write.error)
                return ai_response

            session_id = user_write.session_id
            is_crisis = user_write.is_crisis
            if st.session_state.wellbeing_current_session != session_id:
                st.session_state.wellbeing_current_session = session_id
            
            # Get session summary
            session_summary = self.session_manager.get_session_summary(session_id)
            
            # Build comprehensive context (including KG search)
            try:
                user_id = session_summary.get('user_id') if session_summary else None
                if user_id:
                    context_data = build_context_func(
                        user_id=user_id,
                        current_session_id=session_id,
                        user_input=user_message
                    )
                    # Format context for LLM
                    contextual_prompt = format_context_func(context_data)
                    logger.info(
                        f"✅ [KG-CONTEXT] Comprehensive context built with "
                        f"{len(context_data.get('kg_triples', []))} KG triples"
                    )
                else:
                    # Fallback: session summary only
                    contextual_prompt = (
                        f"Session-Kontext: {session_summary.get('session_summary', '')}" 
                        if session_summary else ""
                    )
            except Exception as e:
                logger.warning(f"⚠️ [KG-CONTEXT] Failed to build comprehensive context: {e}")
                import traceback
                traceback.print_exc()
                # Fallback: session summary only
                contextual_prompt = (
                    f"Session-Kontext: {session_summary.get('session_summary', '')}" 
                    if session_summary else ""
                )
            
            # Enhance AI response with psychological context (now with KG)
            enhanced_response = self.enhance_ai_response(
                ai_response,
                contextual_prompt,
                is_crisis,
                risk_level=user_write.risk_level,
                safety_action=user_write.safety_action,
            )

            # SOTA: optional self-supervision review pass. Triggered by the
            # manager only when risk≥elevated, stage_changed, or every Nth turn.
            try:
                inner = (
                    getattr(self.session_manager, 'manager', None)
                    or self.session_manager
                )
                tm = getattr(inner, 'treatment_manager', None)
                summary_uid = (
                    session_summary.get("user_id") if session_summary else None
                )
                if tm is not None and summary_uid:
                    turn_idx = int(
                        session_summary.get("interaction_count", 0) or 0
                    )
                    verdict = tm.maybe_review(
                        user_id=summary_uid,
                        session_id=session_id,
                        turn_idx=turn_idx,
                        user_message=user_message,
                        draft=enhanced_response,
                    )
                    if (
                        verdict
                        and not verdict.accept
                        and verdict.suggested_revision
                    ):
                        logger.info(
                            "[reviewer] Replacing draft "
                            "(alignment=%.2f, issues=%s)",
                            verdict.plan_alignment,
                            verdict.issues[:2],
                        )
                        enhanced_response = verdict.suggested_revision
            except Exception as exc:  # noqa: BLE001
                logger.debug("[reviewer] skipped: %s", exc)

            assistant_write = self.session_manager.add_message_with_result(
                session_id=session_id,
                role="assistant",
                content=enhanced_response
            )
            if not assistant_write.success or not assistant_write.session_id:
                logger.error("Assistant-Antwort konnte nicht gespeichert werden: %s", assistant_write.error)
                return enhanced_response

            session_id = assistant_write.session_id
            if st.session_state.wellbeing_current_session != session_id:
                st.session_state.wellbeing_current_session = session_id
            
            # Invalidate persistent profile after new interaction
            self._invalidate_profile_cache(session_id)
            
            return enhanced_response
            
        except Exception as e:
            logger.error(f"❌ Fehler bei psychologischer Nachrichtenverarbeitung: {e}")
            return ai_response
    
    def extract_emotional_markers(self, message: str) -> List[str]:
        """
        LLM-based emotional marker extraction.
        
        Args:
            message: Message to analyze
            
        Returns:
            List of emotional markers
        """
        try:
            # Use LLM-based emotional analyzer
            analysis = self.emotional_analyzer.analyze_emotional_state(message)
            
            # Extract primary emotions above threshold
            primary_emotions = analysis.get_primary_emotions(threshold=0.3)
            
            logger.debug(
                f"🧠 LLM-Emotionsanalyse: {analysis.dominant_emotion} "
                f"(Konfidenz: {analysis.confidence:.2f})"
            )
            
            return list(primary_emotions)
            
        except Exception as e:
            logger.warning(f"⚠️ LLM-Emotionsanalyse fehlgeschlagen: {e}")
            # Fallback: return neutral emotion
            return ['neutral']
    
    def detect_crisis(self, message: str) -> bool:
        """
        LLM-based crisis detection.
        
        Args:
            message: Message to analyze
            
        Returns:
            True if crisis detected, False otherwise
        """
        try:
            # Use LLM-based emotional analyzer
            analysis = self.emotional_analyzer.analyze_emotional_state(message)
            
            # Crisis indicators from LLM analysis
            is_crisis = analysis.crisis_indicators
            
            logger.debug(f"🚨 Krisenanalyse: {is_crisis} (Emotion: {analysis.dominant_emotion})")
            
            return bool(is_crisis)
            
        except Exception as e:
            logger.warning(f"⚠️ LLM-Krisenanalyse fehlgeschlagen: {e}")
            # Fallback: structured CarePlanManager risk classifier (no keywords).
            try:
                inner = getattr(self.session_manager, 'manager', None) or self.session_manager
                tm = getattr(inner, 'treatment_manager', None)
                if tm is not None:
                    from wellbeing.care_plans.models import RiskLevel
                    risk = tm.risk.assess(
                        session_id="_handler_fallback", turn_idx=0,
                        user_message=message,
                    )
                    return risk.level == RiskLevel.ACUTE
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"⚠️ Fallback-Risikoklassifikator nicht verfügbar: {exc}")
            # Last resort: when all classifiers fail, assume NO crisis.
            # The session_manager_adapter only triggers on `acute` risk, so
            # a parser failure must not produce false-positive crisis alerts.
            return False
    
    def enhance_ai_response(
        self,
        original_response: str,
        contextual_prompt: str,
        is_crisis: bool,
        risk_level: Optional[str] = None,
        safety_action: str = "normal",
    ) -> str:
        """
        Enhance AI response with psychological context.

        Kein Chat-Block: Risk-Information wird ignoriert — der Care-
        Kernprompt reagiert selbst auf erhöhte Belastung empathisch.

        Args:
            original_response: Original AI response
            contextual_prompt: Contextual prompt with session info
            is_crisis: Whether crisis was detected (deprecated — nicht mehr blockierend)
            risk_level: Risk level (deprecated — nur noch Logging)
            safety_action: Safety action (deprecated — nicht mehr blockierend)

        Returns:
            Enhanced response
        """
        # Logging für erhöhtes Risiko (nicht blockierend)
        if is_crisis or risk_level in ("elevated", "acute"):
            logger.warning(
                "Erhöhtes Risiko im Kontext (risk=%s) — Care-Kernprompt "
                "reagiert empathisch ohne Blockade",
                risk_level,
            )

        # Add session continuity note
        continuity_note = (
            f"\n\n💭 *Diese Antwort berücksichtigt unsere bisherigen Gespräche "
            f"in dieser Session.*"
        )
        
        return original_response + continuity_note
    
    def _invalidate_profile_cache(self, session_id: str) -> None:
        """Invalidate persistent profile cache after new interaction."""
        if self.profile_cache is None:
            return

        try:
            session_summary = self.session_manager.get_session_summary(session_id)
            user_id = session_summary.get('user_id') if session_summary else None

            if user_id:
                self.profile_cache.invalidate_profile(
                    user_id=user_id,
                    trigger_type='new_interaction',
                    trigger_source_id=session_id
                )
                logger.debug(
                    f"🔄 [PERSISTENT-PROFILE] Invalidated profile for "
                    f"user={user_id[:12]}... (new interaction)"
                )

        except Exception as e:
            logger.warning(
                "[PERSISTENT-PROFILE] Failed to invalidate profile for session=%s: %s",
                session_id,
                e,
            )

