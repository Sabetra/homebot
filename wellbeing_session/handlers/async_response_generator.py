"""
Async Response Generator — async variant of ResponseGenerator.

Provides non-blocking AI response generation using the async DB pool
and async context-building pipeline.  Falls back to the sync
``ResponseGenerator`` when async infrastructure is unavailable.

✅ Phase 9: Full async migration of handler layer.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Coroutine, Dict, List, Optional

logger = logging.getLogger(__name__)


def _normalize_role_alternation(messages: List[Dict[str, str]]) -> List[Dict[str, str]]:
    """
    Ensure strict user/assistant alternation by merging consecutive same-role messages.

    Root cause of 'Conversation roles must alternate' errors:
    - DB may store consecutive messages with identical roles (e.g., two assistant turns
      when the bot retries, or two user turns when the user sends quickly).
    - LLM APIs require strict alternation after the optional system message.

    Strategy:
    - Iterate through messages; if a message has the same role as the previous one,
      append its content to the previous message with a separator.
    - System messages are allowed at the start only; subsequent system messages
      are converted to assistant role (they are bot-generated meta-context).

    Args:
        messages: List of {'role': str, 'content': str} dicts (first may be 'system').

    Returns:
        New list with strict role alternation.
    """
    if not messages:
        return messages

    result: List[Dict[str, str]] = []
    first_is_system = messages[0].get('role') == 'system'

    # Allow system message at position 0
    if first_is_system:
        result.append(messages[0])

    start_idx = 1 if first_is_system else 0

    for i in range(start_idx, len(messages)):
        msg = messages[i]
        role = msg.get('role', 'user')
        content = msg.get('content', '')

        # Skip empty messages
        if not content.strip():
            continue

        # Convert stray system messages (after pos 0) to assistant
        if role == 'system':
            role = 'assistant'

        if result and result[-1]['role'] == role:
            # Merge: append content to previous message
            result[-1]['content'] += '\n\n' + content
        else:
            result.append({'role': role, 'content': content})

    return result

# Optional async pool
_ASYNC_AVAILABLE = False
try:
    from database.async_connection_pool import AsyncConnectionPool, get_async_pool
    from database.async_db_operations import get_kg_triples, get_session_interactions
    _ASYNC_AVAILABLE = True
except ImportError:
    pass

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


class AsyncResponseGenerator:
    """Generates AI responses asynchronously for psychological sessions.

    Mirrors the sync ``ResponseGenerator`` API but uses ``await`` for all
    I/O-bound operations (DB reads, context building).
    """

    def __init__(
        self,
        session_manager: Any,
        context_manager: Any,
        chat_logic: Optional[Any] = None,
        async_pool: Optional[Any] = None,
    ) -> None:
        self.session_manager = session_manager
        self.context_manager = context_manager
        self.chat_logic = chat_logic
        self._pool = async_pool

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    @traced_async("handler.generate_response")
    @metered_async("handler.generate_response")
    async def generate_psychological_response(
        self,
        user_input: str,
        session_id: str,
        user_id: str,
        build_context_func: Callable[..., Dict[str, Any]],
        format_context_func: Callable[[Dict[str, Any]], str],
        load_history_func: Optional[Callable[..., None]] = None,
    ) -> str:
        """Generate a psychological AI response asynchronously.

        Args:
            user_input: User message text.
            session_id: Active session ID.
            user_id: Current user ID.
            build_context_func: Sync context builder (called in executor if needed).
            format_context_func: Context formatter.
            load_history_func: Optional history loader for chat_logic.

        Returns:
            Generated AI response string.
        """
        comprehensive_context: Dict[str, Any] = {}
        optimized_messages: List[Dict[str, str]] = []
        messages: List[Dict[str, Any]] = []

        try:
            # 1. Build comprehensive context (may be sync — safe to call directly)
            comprehensive_context = build_context_func(
                user_id=user_id,
                current_session_id=session_id,
                user_input=user_input,
            )

            # 2. Format for LLM
            formatted_context = format_context_func(comprehensive_context)
            context_token_estimate = comprehensive_context.get("context_token_estimate", 0)

            # 3. Adaptive message limit
            max_messages = self._calculate_adaptive_message_limit(context_token_estimate)

            # 4. Session context (sync call — small & fast)
            messages = self.session_manager.get_session_context(
                session_id, max_messages=max_messages
            )

            if messages:
                system_prompt = self._build_system_prompt(formatted_context)
                llm_messages: List[Dict[str, str]] = [{"role": "system", "content": system_prompt}]

                for msg in messages:
                    llm_messages.append({
                        "role": msg.get("role", "user"),
                        "content": msg.get("content", ""),
                    })

                # ✅ RC-3 FIX: Normalize role alternation before LLM call.
                # Consecutive same-role messages (e.g., two assistant turns) cause
                # "Conversation roles must alternate user/assistant/..." errors.
                llm_messages = _normalize_role_alternation(llm_messages)

                optimized_messages, summary_info = self.context_manager.manage_context(llm_messages)

                if summary_info:
                    logger.info("🔄 Async: Chat-Kontext automatisch zusammengefasst")

                current_prompt = self._build_current_prompt(user_input)
                optimized_messages.append({"role": "user", "content": current_prompt})

        except Exception as exc:
            logger.warning("Async response generation context error: %s", exc)
            optimized_messages = self._build_fallback_messages(user_input)

        # 5. LLM call
        if self.chat_logic:
            return self._generate_with_chat_logic(
                optimized_messages, user_input, user_id, session_id,
                comprehensive_context, messages, load_history_func,
            )

        return self._build_fallback_response(user_input)

    # ------------------------------------------------------------------
    # Async KG enrichment (new in Phase 9)
    # ------------------------------------------------------------------

    @traced_async("handler.enrich_kg_async")
    async def enrich_context_with_kg_async(
        self, user_id: str, limit: int = 100
    ) -> List[Dict[str, Any]]:
        """Fetch KG triples asynchronously (zero-copy from pool)."""
        if not _ASYNC_AVAILABLE or self._pool is None:
            return []
        async with self._pool.get_connection() as conn:
            return await get_kg_triples(conn, user_id, limit)

    @traced_async("handler.get_interactions_async")
    async def get_session_interactions_async(
        self, session_id: str, limit: int = 50
    ) -> List[Dict[str, Any]]:
        """Fetch session interactions asynchronously."""
        if not _ASYNC_AVAILABLE or self._pool is None:
            return []
        async with self._pool.get_connection() as conn:
            return await get_session_interactions(conn, session_id, limit)

    # ------------------------------------------------------------------
    # Private helpers (sync — CPU-bound, no I/O)
    # ------------------------------------------------------------------

    def _calculate_adaptive_message_limit(self, context_token_estimate: int) -> int:
        if context_token_estimate > 1500:
            return 20
        elif context_token_estimate > 800:
            return 30
        return 50

    def _build_system_prompt(self, formatted_context: str) -> str:
        return (
            "Sie sind ein einfühlsamer, professioneller psychologischer Berater.\n"
            "Führen Sie ein kontinuierliches, unterstützendes Gespräch.\n\n"
            f"{formatted_context}\n\n"
            "ANWEISUNGEN:\n"
            "- Antworten Sie empathisch und professionell\n"
            "- Berücksichtigen Sie den bisherigen Gesprächsverlauf\n"
            "- Stellen Sie hilfreiche Nachfragen\n"
            "- Verwenden Sie eine warme, unterstützende Sprache"
        )

    def _build_current_prompt(self, user_input: str) -> str:
        return (
            f'AKTUELLE BENUTZER-NACHRICHT: "{user_input}"\n\n'
            "Bitte antworten Sie als empathischer psychologischer Berater."
        )

    def _build_fallback_messages(self, user_input: str) -> List[Dict[str, str]]:
        return [
            {"role": "system", "content": "Sie sind ein einfühlsamer psychologischer Berater."},
            {"role": "user", "content": f'Benutzer sagt: "{user_input}". Bitte antworten Sie empathisch.'},
        ]

    def _build_fallback_response(self, user_input: str) -> str:
        return (
            f'Ich höre Ihnen zu und verstehe, dass Sie über "{user_input}" '
            "sprechen möchten.\n\nKönnen Sie mir mehr darüber erzählen?"
        )

    def _generate_with_chat_logic(
        self,
        optimized_messages: List[Dict[str, str]],
        user_input: str,
        user_id: str,
        session_id: str,
        comprehensive_context: Dict[str, Any],
        messages: List[Dict[str, Any]],
        load_history_func: Optional[Callable[..., None]],
    ) -> str:
        try:
            if load_history_func and messages:
                load_history_func(messages, self.chat_logic)

            session_context = self._convert_to_session_context(
                comprehensive_context, user_id, session_id,
            )
            response = self.chat_logic.psychological_chat(
                user_input, session_context=session_context,
            )
            return str(response)
        except Exception as exc:
            logger.error("Async generate_with_chat_logic error: %s", exc)
            return "Entschuldigung, technischer Fehler bei der Antwortgenerierung."

    def _convert_to_session_context(
        self, ctx: Dict[str, Any], user_id: str, session_id: str,
    ) -> Dict[str, Any]:
        return {
            "user_id": user_id,
            "session_id": session_id,
            "mood": ctx.get("mood_progression", {}).get("current_mood", "neutral"),
            "goals": ctx.get("care_goals", []),
            "knowledge_graph": ctx.get("knowledge_graph", []),
            "previous_sessions": ctx.get("session_summaries", []),
            "mood_progression": ctx.get("mood_progression"),
            "user_insights": ctx.get("user_insights", []),
        }
