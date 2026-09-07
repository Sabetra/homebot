"""
Instrumentation decorators — ``@traced`` / ``@metered`` for sync and async.

Usage::

    from observability.decorators import traced, metered, traced_async, metered_async

    @traced("service.build_context")
    @metered("service.build_context")
    def build_context(user_id: str) -> dict: ...

    @traced_async("service.async_cleanup")
    async def cleanup() -> None: ...

All decorators are **zero-overhead no-ops** when the OTel SDK has not been
initialized (the global ``NoOpTracer`` / ``NoOpMeter`` handle this).
"""

from __future__ import annotations

import functools
import time
import logging
from typing import Any, Callable, Optional, TypeVar, cast

from opentelemetry import trace, metrics
from opentelemetry.trace import StatusCode

logger = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable[..., Any])

# ---------------------------------------------------------------------------
# Module-level instruments (lazy-init on first use)
# ---------------------------------------------------------------------------

_meter: Optional[metrics.Meter] = None
_latency_histogram: Optional[Any] = None
_call_counter: Optional[Any] = None
_error_counter: Optional[Any] = None


def _ensure_instruments() -> None:
    global _meter, _latency_histogram, _call_counter, _error_counter
    if _meter is not None:
        return
    _meter = metrics.get_meter("psych_session.decorators")
    _latency_histogram = _meter.create_histogram(
        name="psych_session.operation.duration",
        description="Latency of decorated operations in milliseconds",
        unit="ms",
    )
    _call_counter = _meter.create_counter(
        name="psych_session.operation.calls",
        description="Number of calls to decorated operations",
    )
    _error_counter = _meter.create_counter(
        name="psych_session.operation.errors",
        description="Number of errors in decorated operations",
    )


# ---------------------------------------------------------------------------
# Sync decorators
# ---------------------------------------------------------------------------


def traced(
    span_name: Optional[str] = None,
    *,
    record_args: bool = False,
    attributes: Optional[dict[str, str]] = None,
) -> Callable[[F], F]:
    """Add an OpenTelemetry span around a **sync** function.

    Args:
        span_name: Custom span name (defaults to ``module.function``).
        record_args: If *True*, log function arguments as span attributes.
        attributes: Static attributes to attach to every span.
    """

    def decorator(fn: F) -> F:
        _name = span_name or f"{fn.__module__}.{fn.__qualname__}"
        tracer = trace.get_tracer(fn.__module__)

        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            with tracer.start_as_current_span(_name) as span:
                if attributes:
                    for k, v in attributes.items():
                        span.set_attribute(k, v)
                if record_args:
                    for i, a in enumerate(args):
                        span.set_attribute(f"arg.{i}", repr(a)[:256])
                    for k, v in kwargs.items():
                        span.set_attribute(f"kwarg.{k}", repr(v)[:256])
                try:
                    result = fn(*args, **kwargs)
                    span.set_status(StatusCode.OK)
                    return result
                except Exception as exc:
                    span.set_status(StatusCode.ERROR, str(exc))
                    span.record_exception(exc)
                    raise

        return cast(F, wrapper)

    return decorator


def metered(
    operation_name: Optional[str] = None,
) -> Callable[[F], F]:
    """Record call count + latency histogram for a **sync** function.

    Args:
        operation_name: Metric label (defaults to ``module.function``).
    """

    def decorator(fn: F) -> F:
        _name = operation_name or f"{fn.__module__}.{fn.__qualname__}"

        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            _ensure_instruments()
            labels = {"operation": _name}
            assert _call_counter is not None
            _call_counter.add(1, labels)

            t0 = time.perf_counter()
            try:
                result = fn(*args, **kwargs)
                return result
            except Exception:
                assert _error_counter is not None
                _error_counter.add(1, labels)
                raise
            finally:
                elapsed_ms = (time.perf_counter() - t0) * 1000
                assert _latency_histogram is not None
                _latency_histogram.record(elapsed_ms, labels)

        return cast(F, wrapper)

    return decorator


# ---------------------------------------------------------------------------
# Async decorators
# ---------------------------------------------------------------------------


def traced_async(
    span_name: Optional[str] = None,
    *,
    record_args: bool = False,
    attributes: Optional[dict[str, str]] = None,
) -> Callable[[F], F]:
    """Add an OpenTelemetry span around an **async** function."""

    def decorator(fn: F) -> F:
        _name = span_name or f"{fn.__module__}.{fn.__qualname__}"
        tracer = trace.get_tracer(fn.__module__)

        @functools.wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            with tracer.start_as_current_span(_name) as span:
                if attributes:
                    for k, v in attributes.items():
                        span.set_attribute(k, v)
                if record_args:
                    for i, a in enumerate(args):
                        span.set_attribute(f"arg.{i}", repr(a)[:256])
                    for k, v in kwargs.items():
                        span.set_attribute(f"kwarg.{k}", repr(v)[:256])
                try:
                    result = await fn(*args, **kwargs)
                    span.set_status(StatusCode.OK)
                    return result
                except Exception as exc:
                    span.set_status(StatusCode.ERROR, str(exc))
                    span.record_exception(exc)
                    raise

        return cast(F, wrapper)

    return decorator


def metered_async(
    operation_name: Optional[str] = None,
) -> Callable[[F], F]:
    """Record call count + latency histogram for an **async** function."""

    def decorator(fn: F) -> F:
        _name = operation_name or f"{fn.__module__}.{fn.__qualname__}"

        @functools.wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            _ensure_instruments()
            labels = {"operation": _name}
            assert _call_counter is not None
            _call_counter.add(1, labels)

            t0 = time.perf_counter()
            try:
                result = await fn(*args, **kwargs)
                return result
            except Exception:
                assert _error_counter is not None
                _error_counter.add(1, labels)
                raise
            finally:
                elapsed_ms = (time.perf_counter() - t0) * 1000
                assert _latency_histogram is not None
                _latency_histogram.record(elapsed_ms, labels)

        return cast(F, wrapper)

    return decorator
