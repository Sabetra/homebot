"""
PII protection decorator — ``@pii_protected`` auto-anonymizes return values.

Usage::

    from pii_protection.decorators import pii_protected

    @pii_protected(fields=["content", "notes"])
    def get_session_data(session_id: str) -> dict:
        return {"content": "Hans said ...", "notes": "Patient Hans Müller..."}
    
    # Return value is auto-anonymized before caller sees it
"""

from __future__ import annotations

import functools
import logging
from typing import Any, Callable, Dict, List, Optional, TypeVar, cast

logger = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable[..., Any])

# Lazy pipeline singleton
_pipeline: Optional[Any] = None


def _get_pipeline() -> Any:
    """Lazy-init a default PIIProtectionPipeline."""
    global _pipeline
    if _pipeline is None:
        from pii_protection.pipeline import PIIProtectionPipeline

        _pipeline = PIIProtectionPipeline(languages=["de", "en"], strategy="replace")
    return _pipeline


def pii_protected(
    fields: Optional[List[str]] = None,
    strategy: str = "replace",
) -> Callable[[F], F]:
    """Decorator that auto-anonymizes string return values.

    Works on functions returning:
    - ``str``  → anonymized string
    - ``dict`` → anonymized specified *fields* (or all string values)
    - ``list[dict]`` → each dict anonymized
    """

    def decorator(fn: F) -> F:
        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            result = fn(*args, **kwargs)
            pipeline = _get_pipeline()

            if isinstance(result, str):
                return pipeline.protect(result)

            if isinstance(result, dict):
                return pipeline.protect_dict(result, fields=fields)

            if isinstance(result, list) and result and isinstance(result[0], dict):
                return [pipeline.protect_dict(item, fields=fields) for item in result]

            return result

        return cast(F, wrapper)

    return decorator
