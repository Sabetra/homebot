"""Shared token budget helpers for finance modules.

Single source of truth for resolving model context size in finance paths.
"""

from __future__ import annotations

import logging
from typing import Any


DEFAULT_FINANCE_CONTEXT_TOKENS = 16384
logger = logging.getLogger(__name__)


def resolve_context_tokens(llm_client: Any, default_tokens: int = DEFAULT_FINANCE_CONTEXT_TOKENS) -> int:
    """Resolve context size from the active model client deterministically.

    Resolution order:
    1) ``llm_client.get_max_context_tokens()`` if available and valid.
    2) cached ``llm_client._cached_n_ctx`` if available and valid.
    3) provided default.
    """
    getter = getattr(llm_client, "get_max_context_tokens", None)
    if callable(getter):
        try:
            value = int(getter())
            if value > 1024:
                return value
        except Exception as exc:
            logger.debug(f"resolve_context_tokens: get_max_context_tokens failed: {exc}")

    cached = getattr(llm_client, "_cached_n_ctx", None)
    if isinstance(cached, int) and cached > 1024:
        return cached

    return int(default_tokens)


__all__ = ["DEFAULT_FINANCE_CONTEXT_TOKENS", "resolve_context_tokens"]
