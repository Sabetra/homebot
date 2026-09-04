"""
Context propagation helpers — inject / extract OTel context for cross-service boundaries.

Useful when:
- Passing context from Streamlit → background async tasks
- Embedding trace context in DB records for correlation
- Cross-process propagation (e.g. via HTTP headers or message queues)

Usage::

    from observability.context_propagation import inject_context, extract_context

    # Producer side
    carrier: dict = {}
    inject_context(carrier)
    # ... send carrier as HTTP headers / message attributes ...

    # Consumer side
    ctx = extract_context(carrier)
    with tracer.start_as_current_span("process_message", context=ctx):
        ...
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from opentelemetry import context as otel_context
from opentelemetry.propagate import inject, extract


def inject_context(carrier: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Inject the current OTel context into a carrier dict.

    Args:
        carrier: Mutable dict to inject into.  If *None*, a new dict is created.

    Returns:
        The carrier with propagation headers injected.
    """
    if carrier is None:
        carrier = {}
    inject(carrier)
    return carrier


def extract_context(carrier: Dict[str, Any]) -> otel_context.Context:
    """Extract an OTel context from a carrier dict.

    Args:
        carrier: Dict containing propagation headers (e.g. ``traceparent``).

    Returns:
        An ``opentelemetry.context.Context`` that can be used as the
        ``context=`` argument to ``tracer.start_as_current_span()``.
    """
    return extract(carrier)
