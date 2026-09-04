#!/usr/bin/env python3
"""
TOKEN BUDGET MANAGER — Priority-Based Context Window Allocation
================================================================

Ensures the final LLM prompt fits within n_ctx by measuring token count
of each component and truncating lowest-priority parts first.

Components are ranked by care relevance:
    PRIO 1 (MUST): System prompt + current user message
    PRIO 2 (HIGH): Session history (recent turns)
    PRIO 3 (HIGH): Enriched context (KG, profile, mood — in user message)
    PRIO 4 (MED):  RAG evidence
    PRIO 5 (LOW):  Web search results

Uses the real tokenizer from ModelLoader (not heuristic).
Falls back to len(text) // 4 if tokenizer unavailable.
"""

import copy
import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ── Budget Allocation ────────────────────────────────────────────────────────
# Reserve tokens for generation output.
# The total prompt must fit in: n_ctx - generation_reserve
_DEFAULT_GENERATION_RESERVE = 3072  # Reserve 3K tokens for the response

# Maximum fraction of remaining budget each component may use
_MAX_FRACTION_SESSION_HISTORY = 0.40
_MAX_FRACTION_ENRICHED_CONTEXT = 0.35
_MAX_FRACTION_RAG = 0.20
_MAX_FRACTION_WEB = 0.05


class TokenBudgetExceededError(RuntimeError):
    """Raised when immutable safety instructions and current input cannot fit."""


class TokenBudgetManager:
    """
    Manages token budget allocation for the psychological chat pipeline.
    
    Usage:
        budget = TokenBudgetManager(model_loader)
        budget.set_system_prompt(system_prompt_text)
        budget.set_user_query(user_query_text)
        
        # Get available budget for enrichment
        available = budget.remaining_for_enrichment()
        
        # Trim context components to fit
        trimmed_history = budget.trim_session_history(history_messages)
        trimmed_context = budget.trim_enriched_context(context_text)
        trimmed_rag = budget.trim_rag_context(rag_text)
        
        # Final validation
        messages = budget.build_final_messages(...)
        assert budget.validate(messages)
    """
    
    def __init__(
        self,
        model_loader: Any = None,
        n_ctx: int = 16384,
        generation_reserve: int = _DEFAULT_GENERATION_RESERVE,
    ):
        self._model_loader = model_loader
        self._generation_reserve = generation_reserve
        
        # Determine actual n_ctx from model if possible
        if model_loader and hasattr(model_loader, 'get_max_context_tokens'):
            actual_n_ctx = model_loader.get_max_context_tokens()
            if actual_n_ctx and actual_n_ctx > 0:
                self._n_ctx = actual_n_ctx
            else:
                self._n_ctx = n_ctx
        else:
            self._n_ctx = n_ctx
        
        self._prompt_budget = self._n_ctx - self._generation_reserve
        
        # Track allocated tokens
        self._system_tokens = 0
        self._query_tokens = 0
        self._system_prompt = ""
        self._user_query = ""
        
        logger.info(
            f"📊 [TOKEN-BUDGET] n_ctx={self._n_ctx}, "
            f"reserve={self._generation_reserve}, "
            f"prompt_budget={self._prompt_budget}"
        )
    
    def count_tokens(self, text: str) -> int:
        """Count tokens using the model's tokenizer (or heuristic fallback)."""
        if not text:
            return 0
        if self._model_loader and hasattr(self._model_loader, 'count_tokens'):
            try:
                return self._model_loader.count_tokens(text)
            except Exception:
                pass
        # Heuristic fallback: ~4 chars per token for German text
        return max(1, len(text) // 4)
    
    def count_messages_tokens(self, messages: List[Dict[str, str]]) -> int:
        """Count total tokens in a message list."""
        total = 0
        for msg in messages:
            content = msg.get('content', '')
            if isinstance(content, str) and content:
                total += self.count_tokens(content)
            total += 4  # Per-message overhead (role, formatting)
        return total
    
    def set_system_prompt(self, text: str, *, immutable_text: Optional[str] = None) -> int:
        """Register the full prompt and its immutable safety portion."""
        self._system_prompt = text if immutable_text is None else immutable_text
        self._system_tokens = self.count_tokens(text) + 4
        return self._system_tokens
    
    def set_user_query(self, text: str) -> int:
        """Register the user query and return its token count."""
        self._user_query = text
        self._query_tokens = self.count_tokens(text) + 4
        return self._query_tokens
    
    @property
    def remaining_budget(self) -> int:
        """Tokens remaining after system prompt and user query."""
        return max(0, self._prompt_budget - self._system_tokens - self._query_tokens)
    
    def budget_for_history(self) -> int:
        """Maximum tokens allocated for session history."""
        return int(self.remaining_budget * _MAX_FRACTION_SESSION_HISTORY)
    
    def budget_for_context(self) -> int:
        """Maximum tokens allocated for enriched context block."""
        return int(self.remaining_budget * _MAX_FRACTION_ENRICHED_CONTEXT)
    
    def budget_for_rag(self) -> int:
        """Maximum tokens allocated for RAG evidence."""
        return int(self.remaining_budget * _MAX_FRACTION_RAG)
    
    def budget_for_web(self) -> int:
        """Maximum tokens allocated for web results."""
        return int(self.remaining_budget * _MAX_FRACTION_WEB)
    
    def trim_session_history(
        self,
        messages: List[Dict[str, str]],
    ) -> List[Dict[str, str]]:
        """
        Trim session history to fit within budget.
        Keeps most recent messages (preserves conversation continuity).
        
        Returns:
            Trimmed message list
        """
        budget = self.budget_for_history()
        if not messages:
            return []
        
        # Work backwards from most recent
        result = []
        tokens_used = 0
        
        for msg in reversed(messages):
            msg_tokens = self.count_tokens(msg.get('content', '')) + 4
            if tokens_used + msg_tokens > budget:
                break
            result.insert(0, msg)
            tokens_used += msg_tokens
        
        if len(result) < len(messages):
            logger.info(
                f"📊 [TOKEN-BUDGET] Session-History getrimmt: "
                f"{len(messages)} → {len(result)} Messages "
                f"({tokens_used}/{budget} tokens)"
            )
        
        return result
    
    def trim_text_to_budget(self, text: str, budget_tokens: int, label: str = "text") -> str:
        """
        Trim text to fit within a token budget.
        Cuts from the end, preserving the beginning (highest priority content).
        
        Tries to cut at paragraph/line boundaries for cleaner truncation.
        """
        if not text:
            return text
        
        current_tokens = self.count_tokens(text)
        if current_tokens <= budget_tokens:
            return text
        
        # Binary search for the right cut point
        # (faster than character-by-character for long texts)
        low, high = 0, len(text)
        best = 0
        
        while low <= high:
            mid = (low + high) // 2
            candidate = text[:mid]
            tokens = self.count_tokens(candidate)
            
            if tokens <= budget_tokens:
                best = mid
                low = mid + 1
            else:
                high = mid - 1
        
        trimmed = text[:best]
        
        # Try to cut at a clean boundary (newline)
        last_newline = trimmed.rfind('\n', max(0, best - 200), best)
        if last_newline > best * 0.7:  # Don't lose more than 30%
            trimmed = trimmed[:last_newline]
        
        trimmed_tokens = self.count_tokens(trimmed)
        logger.info(
            f"📊 [TOKEN-BUDGET] {label} getrimmt: "
            f"{current_tokens} → {trimmed_tokens} tokens "
            f"(Budget: {budget_tokens})"
        )
        
        return trimmed + "\n[... gekürzt wegen Token-Limit ...]"
    
    def trim_enriched_context(self, context_text: str) -> str:
        """Trim enriched context block to fit within budget."""
        return self.trim_text_to_budget(
            context_text, self.budget_for_context(), "Enriched-Context"
        )
    
    def trim_rag_context(self, rag_text: str) -> str:
        """Trim RAG evidence to fit within budget."""
        return self.trim_text_to_budget(
            rag_text, self.budget_for_rag(), "RAG-Context"
        )
    
    def trim_web_context(self, web_text: str) -> str:
        """Trim web results to fit within budget."""
        return self.trim_text_to_budget(
            web_text, self.budget_for_web(), "Web-Context"
        )
    
    def validate_messages(self, messages: List[Dict[str, str]]) -> Tuple[bool, int]:
        """
        Validate that the final message list fits within prompt budget.
        
        Returns:
            (is_valid, total_tokens)
        """
        total = self.count_messages_tokens(messages)
        is_valid = total <= self._prompt_budget
        
        if not is_valid:
            logger.warning(
                f"⚠️ [TOKEN-BUDGET] Prompt ÜBERSCHREITET Budget! "
                f"{total} > {self._prompt_budget} tokens "
                f"(n_ctx={self._n_ctx}, reserve={self._generation_reserve})"
            )
        else:
            utilization = total / self._prompt_budget * 100 if self._prompt_budget > 0 else 0
            logger.info(
                f"✅ [TOKEN-BUDGET] Prompt OK: {total}/{self._prompt_budget} tokens "
                f"({utilization:.0f}% Auslastung)"
            )
        
        return is_valid, total

    def emergency_trim_messages(
        self,
        messages: List[Dict[str, str]],
    ) -> List[Dict[str, str]]:
        """
        Remove optional context and oldest history while preserving the exact
        safety system prompt and current user query registered via ``set_*``.

        Returns:
            A deep-copied message list guaranteed to fit.

        Raises:
            TokenBudgetExceededError: immutable content alone exceeds budget.
        """
        if not messages:
            return messages

        trimmed = copy.deepcopy(messages)
        system_index = next(
            (index for index, message in enumerate(trimmed) if message.get("role") == "system"),
            None,
        )
        user_index = next(
            (index for index in range(len(trimmed) - 1, -1, -1)
             if trimmed[index].get("role") == "user"),
            None,
        )
        if system_index is None or user_index is None:
            raise TokenBudgetExceededError(
                "Cannot preserve prompt: system or current user message is missing"
            )

        trimmed[system_index]["content"] = self._system_prompt
        trimmed[user_index]["content"] = self._user_query
        immutable_messages = [trimmed[system_index], trimmed[user_index]]
        immutable_valid, immutable_tokens = self.validate_messages(immutable_messages)
        if not immutable_valid:
            raise TokenBudgetExceededError(
                "Immutable system prompt and current user query exceed prompt budget "
                f"({immutable_tokens} > {self._prompt_budget})"
            )

        protected = {system_index, user_index}

        # ★ PERF (2026-08-28, P2): Additive token accounting.
        # The full count is computed ONCE; each removed message's contribution is
        # subtracted. Previously the whole list was re-tokenized on every removal
        # step (validate_messages per iteration → O(n·k) tokenizer calls).
        # Removal order (oldest optional first), the preserved immutable pair and
        # the fail-closed errors are unchanged.
        def _msg_tokens(msg: Dict[str, Any]) -> int:
            content = msg.get("content", "")
            if isinstance(content, str) and content:
                return self.count_tokens(content)
            return 0

        optional_indices = [
            index for index in range(len(trimmed)) if index not in protected
        ]
        total = self.count_messages_tokens(trimmed)
        drop = 0
        while drop < len(optional_indices) and total > self._prompt_budget:
            # optional_indices is ascending and every previously removed message
            # lies below the current candidate → each of them shifts its position
            # in the shrinking list down by exactly one.
            remove_index = optional_indices[drop] - drop
            total -= _msg_tokens(trimmed[remove_index]) + 4
            drop += 1

        if total > self._prompt_budget:
            raise TokenBudgetExceededError(
                f"Emergency trim failed to satisfy prompt budget ({total} > {self._prompt_budget})"
            )

        if drop:
            dropped = {optional_indices[d] for d in range(drop)}
            trimmed = [
                message for index, message in enumerate(trimmed)
                if index not in dropped
            ]
        return trimmed
