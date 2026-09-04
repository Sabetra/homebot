"""
Observability package — OpenTelemetry instrumentation for the psychological session system.

Provides:
- ``tracing``: Distributed tracing with spans for all critical paths
- ``metrics``: Counters, histograms for latency, throughput, error rates
- ``decorators``: ``@traced``, ``@metered`` decorators for easy instrumentation
- ``setup``: One-call initialization for the full OTel pipeline

Usage::

    from observability import setup_observability, traced, metered

    # Initialize once at app startup
    setup_observability(service_name="psych-session", endpoint="http://localhost:4317")

    # Decorate functions
    @traced("build_context")
    @metered("build_context")
    def build_context(user_id: str) -> dict:
        ...
"""

from observability.setup import setup_observability, get_tracer, get_meter, shutdown_observability
from observability.decorators import traced, traced_async, metered, metered_async
from observability.context_propagation import inject_context, extract_context

__all__ = [
    "setup_observability",
    "shutdown_observability",
    "get_tracer",
    "get_meter",
    "traced",
    "traced_async",
    "metered",
    "metered_async",
    "inject_context",
    "extract_context",
]
