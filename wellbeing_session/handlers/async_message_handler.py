"""
Async Message Handler — async variant of MessageHandler.

Non-blocking message processing with async emotional analysis
and async context enrichment.

✅ Phase 9: Full async migration of handler layer.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

# Optional OTel
try:
    from observability.decorators import traced_async, metered_async
except ImportError:
    def traced_async(*a: Any, **kw: Any) -> Any:  # type: ignore[misc]
        def d(fn: Any) -> Any: return fn
        if a and callable(a[0]): return a[0]
        return d
    def metered_async(*a: Any, **kw: Any) -> Any:  # type: ignore[misc]
        def d(fn: Any) -> Any: return fn
        if a and callable(a[0]): return a[0]
        return d


class AsyncMessageHandler:
    """Processes psychological messages asynchronously.

    Mirrors the sync ``MessageHandler`` API with ``async`` entry points.
    """

    def __init__(
        self,
        session_manager: Any,
        emotional_analyzer: Any,
        chat_logic: Optional[Any] = None,
        profile_cache: Optional[Any] = None,
    ) -> None:
        self.session_manager = session_manager
        self.emotional_analyzer = emotional_analyzer
        self.chat_logic = chat_logic
        self.profile_cache = profile_cache

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    @traced_async("handler.handle_message")
    @metered_async("handler.handle_message")
    async def handle_psychological_message(
        self,
        user_message: str,
        ai_response: str,
        session_id: str,
        psych_enabled: bool,
        build_context_func: Callable[..., Dict[str, Any]],
        format_context_func: Callable[[Dict[str, Any]], str],
    ) -> str:
        """Process a psychological message asynchronously.

        Args:
            user_message: User input text.
            ai_response: Standard AI response (pre-generated).
            session_id: Active session ID.
            psych_enabled: Whether psych mode is active.
            build_context_func: Sync context builder.
            format_context_func: Context formatter.

        Returns:
            Enhanced AI response with psychological context.
        """
        if not psych_enabled or not session_id:
            return ai_response

        try:
            # Emotional analysis (sync — CPU-bound)
            emotional_markers = self.extract_emotional_markers(user_message)
            is_crisis = self.detect_crisis(user_message)

            user_write = self.session_manager.add_message_with_result(
                session_id=session_id,
                role="user",
                content=user_message,
                emotional_markers=emotional_markers,
                is_crisis=is_crisis,
            )
            if not user_write.success or not user_write.session_id:
                logger.error("Async user message persistence failed: %s", user_write.error)
                return ai_response
            session_id = user_write.session_id
            is_crisis = user_write.is_crisis

            # Build context
            session_summary = self.session_manager.get_session_summary(session_id)
            contextual_prompt = ""

            try:
                user_id = session_summary.get("user_id") if session_summary else None
                if user_id:
                    context_data = build_context_func(
                        user_id=user_id,
                        current_session_id=session_id,
                        user_input=user_message,
                    )
                    contextual_prompt = format_context_func(context_data)
                    logger.info(
                        "✅ [ASYNC-KG-CONTEXT] Comprehensive context built with %d KG triples",
                        len(context_data.get("kg_triples", [])),
                    )
            except Exception as exc:
                logger.warning("⚠️ Async context build failed: %s", exc)
                contextual_prompt = (
                    f"Session-Kontext: {session_summary.get('session_summary', '')}"
                    if session_summary else ""
                )

            # Enhance response
            enhanced = self.enhance_ai_response(
                ai_response,
                contextual_prompt,
                is_crisis,
                risk_level=user_write.risk_level,
                safety_action=user_write.safety_action,
            )

            # SOTA: optional self-supervision review pass. Only triggers when
            # risk is elevated/acute or on a cadence (gate inside the manager).
            try:
                inner = getattr(self.session_manager, 'manager', None) or self.session_manager
                tm = getattr(inner, 'treatment_manager', None)
                summary_uid = session_summary.get("user_id") if session_summary else None
                if tm is not None and summary_uid:
                    turn_idx = int(session_summary.get("interaction_count", 0) or 0)
                    verdict = tm.maybe_review(
                        user_id=summary_uid,
                        session_id=session_id,
                        turn_idx=turn_idx,
                        user_message=user_message,
                        draft=enhanced,
                    )
                    if verdict and not verdict.accept and verdict.suggested_revision:
                        logger.info(
                            "[reviewer] Replacing draft (alignment=%.2f, issues=%s)",
                            verdict.plan_alignment, verdict.issues[:2],
                        )
                        enhanced = verdict.suggested_revision
            except Exception as exc:  # noqa: BLE001
                logger.debug("[reviewer] skipped: %s", exc)

            assistant_write = self.session_manager.add_message_with_result(
                session_id=session_id,
                role="assistant",
                content=enhanced,
            )
            if not assistant_write.success or not assistant_write.session_id:
                logger.error("Async assistant response persistence failed: %s", assistant_write.error)
                return enhanced
            session_id = assistant_write.session_id

            # Invalidate profile cache
            self._invalidate_profile_cache(session_id)

            return enhanced

        except Exception as exc:
            logger.error("❌ Async message handler error: %s", exc)
            return ai_response

    # ------------------------------------------------------------------
    # Sync helpers (CPU-bound — no I/O)
    # ------------------------------------------------------------------

    def extract_emotional_markers(self, message: str) -> List[str]:
        """Extract emotional markers using the LLM analyzer."""
        try:
            analysis = self.emotional_analyzer.analyze_emotional_state(message)
            return list(analysis.get_primary_emotions(threshold=0.3))
        except Exception:
            return ["neutral"]

    def detect_crisis(self, message: str) -> bool:
        """Detect crisis indicators in a message.

        Primary path: LLM-based emotional analyzer.
        Fallback path: structured CarePlanManager risk classifier (no keywords).
        Last resort: do not invent a crisis state without classifier evidence.
        """
        try:
            analysis = self.emotional_analyzer.analyze_emotional_state(message)
            return bool(analysis.crisis_indicators)
        except Exception as primary_exc:
            logger.warning("⚠️ LLM-Krisenanalyse fehlgeschlagen: %s", primary_exc)

        # Fallback: structured risk classifier on the CarePlanManager.
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
            logger.warning("⚠️ Fallback-Risikoklassifikator nicht verfügbar: %s", exc)

        return False

    def enhance_ai_response(
        self,
        original: str,
        contextual_prompt: str,
        is_crisis: bool,
        risk_level: Optional[str] = None,
        safety_action: str = "normal",
    ) -> str:
        """Enhance an AI response with psychological context.

        Kein Chat-Block: Risk-Information wird nur geloggt — der Care-
        Kernprompt reagiert selbst auf erhöhte Belastung empathisch.
        """
        if is_crisis or risk_level in ("elevated", "acute"):
            logger.warning(
                "Erhöhtes Risiko im Kontext (risk=%s) — Care-Kernprompt "
                "reagiert empathisch ohne Blockade",
                risk_level,
            )
        return original + "\n\n💭 *Diese Antwort berücksichtigt unsere bisherigen Gespräche.*"

    def _invalidate_profile_cache(self, session_id: str) -> None:
        """Invalidate profile cache after new interaction."""
        if self.profile_cache is None:
            return
        try:
            summary = self.session_manager.get_session_summary(session_id)
            uid = summary.get("user_id") if summary else None
            if uid:
                self.profile_cache.invalidate_profile(
                    user_id=uid,
                    trigger_type="new_interaction",
                    trigger_source_id=session_id,
                )
        except Exception as exc:
            logger.warning(
                "[PERSISTENT-PROFILE] Failed to invalidate profile for session=%s: %s",
                session_id,
                exc,
            )
