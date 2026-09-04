from __future__ import annotations
from typing import List, Dict, Any, Optional, TYPE_CHECKING
import logging

if TYPE_CHECKING:
    from scripts.model_loader import ModelLoader

# Fallback heuristic -- used ONLY when no real tokenizer is available.
CHARS_PER_TOKEN = 4
logger = logging.getLogger(__name__)

class ContextManager:
    def __init__(self, n_ctx: int, reserve: int = 512, model_loader: Optional["ModelLoader"] = None):
        self.n_ctx = max(512, int(n_ctx))
        self.reserve = max(128, int(reserve))
        self._model_loader = model_loader

    def set_model_loader(self, model_loader: "ModelLoader") -> None:
        """Inject model_loader at runtime (avoids circular import)."""
        self._model_loader = model_loader

    def estimate_tokens(self, messages: List[Dict[str, Any]]) -> int:
        """Count tokens -- uses real tokenizer when available, falls back to heuristic."""
        if self._model_loader is not None:
            try:
                return self._model_loader.count_messages_tokens(messages)
            except Exception as exc:
                logger.debug(f"ContextManager tokenizer unavailable, using heuristic fallback: {exc}")
        text = "\n".join([
            (m.get("content") if isinstance(m.get("content"), str) else str(m.get("content"))) or ""
            for m in messages
        ])
        return max(1, len(text) // CHARS_PER_TOKEN)

    def trim_history(self, history: List[Dict[str, Any]], max_tokens: int) -> List[Dict[str, Any]]:
        # Simple front-trim: keep the last turns until budget fits.
        #
        # PERFORMANCE (2026-08-28, Codebase-Audit Phase 3): The original loop
        # re-estimated the ENTIRE remaining list on every iteration, which is
        # O(blocks * n) and -- with the real tokenizer -- re-tokenizes every
        # remaining message (GPU, under cuda_lock) on each iteration.
        #
        # When the active estimator is ADDITIVE (real tokenizer:
        # count_messages_tokens == sum of per-message costs), we compute each
        # message's cost ONCE and subtract the dropped block's cost from a
        # running total -> O(n), with EXACTLY the same output.
        # The heuristic fallback ("\n".join(...) // CHARS_PER_TOKEN) is NOT
        # additive, so for that path we keep the original loop unchanged.
        costs = self._per_message_costs(history)
        if costs is None:
            # Heuristic path (non-additive estimator): original algorithm.
            trimmed = list(history)
            while trimmed and self._estimate_list(trimmed) > max_tokens:
                # drop the oldest pair (try to drop a user+assistant block)
                # remove until we drop a user message
                idx = 0
                while idx < len(trimmed) and trimmed[idx].get("role") != "user":
                    idx += 1
                if idx < len(trimmed):
                    del trimmed[: idx + 1]
                else:
                    # fallback: drop first
                    trimmed.pop(0)
            return trimmed

        # Additive path (real tokenizer): O(n) with a running total.
        trimmed = list(history)
        total = sum(costs)
        start = 0  # index into the ORIGINAL list of the first kept message
        n = len(history)
        while start < n and total > max_tokens:
            # drop the oldest user+assistant block (find the first user message)
            idx = 0
            while idx < (n - start) and trimmed[idx].get("role") != "user":
                idx += 1
            drop = idx + 1 if idx < (n - start) else 1
            total -= sum(costs[start: start + drop])
            start += drop
            del trimmed[:drop]
        return trimmed

    def _per_message_costs(self, history: List[Dict[str, Any]]) -> Optional[List[int]]:
        """Per-message token costs when the active estimator is additive
        (real tokenizer via model_loader), else ``None``.

        Mirrors ``ModelLoader.count_messages_tokens()`` exactly:
            cost(msg) = count_tokens(content) + 4   if content is a non-empty str
                       = 4                           otherwise
        Returns ``None`` when no tokenizer is available (or on any error) so the
        caller falls back to the original -- correct but O(n^2) -- loop, because
        the heuristic ``estimate_tokens()`` fallback is NOT additive.
        """
        if self._model_loader is None:
            return None
        try:
            costs: List[int] = []
            for msg in history:
                content = msg.get("content", "")
                cost = 0
                if isinstance(content, str) and content:
                    cost = self._model_loader.count_tokens(content)
                costs.append(cost + 4)
            return costs
        except Exception:
            return None

    def _estimate_list(self, messages: List[Dict[str, Any]]) -> int:
        return self.estimate_tokens(messages)
