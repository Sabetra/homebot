"""Explicit, i18n-ready error messages for missing local models.

The reranker (``agent/reranker.py``) and the NLI verifier
(``agent/verification_manager.py``) both *degrade silently* when a required
local model is absent from the Hugging Face cache: the cross-encoder falls back
to nothing and RAG / verification quality drops with only a generic log line.

These helpers turn that silent failure into an operator-actionable, i18n-ready
message that names the missing model and points at the bootstrap tool
(``scripts/setup_models.py``). Both call sites share this module so the wording
(and its i18n keys) stay in one place.
"""
from __future__ import annotations

from typing import Optional

__all__ = ["build_missing_model_message"]


def build_missing_model_message(
    canonical: str,
    i18n_key: str,
    *,
    default: Optional[str] = None,
) -> str:
    """Return an i18n-ready message for a missing required model.

    Tries the i18n key ``i18n_key`` (with a ``{model}`` placeholder) for the
    active language; when the key is absent in every locale *or* the i18n
    manager is unavailable, falls back to ``default`` (or a built-in English
    message). Never raises — this is a logging path, not a control path.

    Args:
        canonical: Human-readable model id that is missing (e.g.
            ``"BAAI/bge-reranker-v2-m3"``).
        i18n_key: Dotted i18n key, e.g. ``"models.reranker_missing"``.
        default: Optional full fallback string (model name already baked in).

    Returns:
        A non-empty, operator-actionable English/translated message.
    """
    fallback = default or (
        f"Required model not available locally: {canonical}. "
        "Check status: `python scripts/setup_models.py --status`. "
        "Fix: `python scripts/setup_models.py --fetch` (online) or "
        "pre-populate the Hugging Face cache (offline)."
    )
    try:
        from i18n.i18n_manager import t as _t

        msg = _t(i18n_key, model=canonical)
        # ``t`` returns the raw key when it is not found in any locale.
        if msg and msg != i18n_key:
            return msg
    except Exception:  # pragma: no cover - i18n must never break logging
        pass
    return fallback