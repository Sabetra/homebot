"""
LLM-based JSON classifier helpers.

A thin, reusable wrapper around the project-wide ``parse_llm_json`` utility
that adds:

- structured prompt templating
- numeric clamping to the documented value ranges
- transparent failure (returns ``None`` instead of guessing)

These helpers replace **every** keyword/regex-based decision in the
psychological pipeline (crisis detection, stage-of-change, MBC scoring,
goal-equivalence verification, …).
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

# Sentinel returned by parse_llm_json on failure (we use {} so that
# downstream code can treat 'no keys' as a hard failure).
_PARSE_ERROR_DEFAULT: Dict[str, Any] = {}


def call_llm_json(
    llm_function: Callable[..., str],
    prompt: str,
    *,
    schema_validator: Optional[Callable[[Dict[str, Any]], bool]] = None,
    debug_label: str = "llm_json",
) -> Optional[Dict[str, Any]]:
    """Call the LLM and parse a JSON response.

    Returns ``None`` if the LLM produced an empty response or the JSON could
    not be parsed/validated. Callers must treat ``None`` as "no information"
    — never as "false" or "0".
    """
    if not callable(llm_function):
        logger.warning("[%s] No llm_function configured — skipping", debug_label)
        return None

    try:
        response = llm_function(prompt, image_path=None)
    except TypeError:
        # Some llm_function signatures are positional-only.
        response = llm_function(prompt)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[%s] LLM call failed: %s", debug_label, exc)
        return None

    if not response or not str(response).strip():
        logger.debug("[%s] Empty LLM response", debug_label)
        return None

    try:
        from utils.llm_json_parser import parse_llm_json
    except ImportError:
        logger.error("[%s] utils.llm_json_parser missing — fail closed", debug_label)
        return None

    parsed = parse_llm_json(
        str(response),
        schema_validator=schema_validator,
        default_on_error=_PARSE_ERROR_DEFAULT,
    )
    if not parsed:
        logger.debug("[%s] JSON parsing yielded empty dict", debug_label)
        return None
    return parsed


def clamp01(value: Any, default: float = 0.0) -> float:
    """Clamp a value into the [0, 1] interval; use ``default`` if not numeric."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    if v != v:  # NaN
        return default
    return max(0.0, min(1.0, v))


def safe_str_list(value: Any, *, max_items: int = 8, max_len: int = 240) -> List[str]:
    """Coerce arbitrary LLM output into a clean ``List[str]``."""
    if value is None:
        return []
    if isinstance(value, str):
        # Split on bullet/newline; the LLM occasionally returns prose.
        items = [s.strip("•-* ").strip() for s in value.splitlines() if s.strip()]
    elif isinstance(value, list):
        items = [str(item).strip() for item in value if item is not None]
    else:
        items = [str(value).strip()]
    cleaned: List[str] = []
    for item in items:
        if not item:
            continue
        cleaned.append(item[:max_len])
        if len(cleaned) >= max_items:
            break
    return cleaned
