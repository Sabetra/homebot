"""
OpenTelemetry setup — one-call initialization of Traces + Metrics.

Supports three export modes:
- ``"otlp"``  → gRPC OTLP exporter (production: Jaeger / Tempo / Collector)
- ``"console"`` → stdout for local development
- ``"noop"``   → no-op exporter (zero overhead in tests)

All objects (TracerProvider, MeterProvider) are registered globally
so any code that calls ``get_tracer()`` / ``get_meter()`` gets the
same pre-configured instance.

Usage::

    from observability.setup import setup_observability, get_tracer

    setup_observability(
        service_name="psych-session",
        export_mode="otlp",
        endpoint="http://localhost:4317",
    )

    tracer = get_tracer(__name__)
    with tracer.start_as_current_span("my_operation") as span:
        span.set_attribute("user.id", user_id)
        ...
"""

from __future__ import annotations

import logging
import os
from typing import Optional

from opentelemetry import trace, metrics
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    ConsoleSpanExporter,
    SimpleSpanProcessor,
)
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import (
    ConsoleMetricExporter,
    PeriodicExportingMetricReader,
)
from opentelemetry.sdk.resources import Resource, SERVICE_NAME

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module state
# ---------------------------------------------------------------------------

_initialized: bool = False
_tracer_provider: Optional[TracerProvider] = None
_meter_provider: Optional[MeterProvider] = None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def setup_observability(
    service_name: str = "psychological-session",
    export_mode: str = "noop",
    endpoint: str = "http://localhost:4317",
    *,
    service_version: str = "1.0.0",
    environment: str = "development",
) -> None:
    """Initialize the OpenTelemetry tracing + metrics pipeline.

    Args:
        service_name: Logical name of the service.
        export_mode: One of ``"otlp"``, ``"console"``, ``"noop"``.
        endpoint: OTLP gRPC endpoint (only used when *export_mode* is ``"otlp"``).
        service_version: SemVer for the service.
        environment: Deployment environment tag.
    """
    global _initialized, _tracer_provider, _meter_provider

    if _initialized:
        logger.debug("OpenTelemetry already initialized — skipping")
        return

    resource = Resource.create(
        {
            SERVICE_NAME: service_name,
            "service.version": service_version,
            "deployment.environment": environment,
        }
    )

    # --- Tracing -----------------------------------------------------------
    _tracer_provider = TracerProvider(resource=resource)

    if export_mode == "otlp":
        try:
            from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter

            otlp_exporter = OTLPSpanExporter(endpoint=endpoint, insecure=True)
            _tracer_provider.add_span_processor(BatchSpanProcessor(otlp_exporter))
            logger.info("✅ OTel tracing: OTLP gRPC → %s", endpoint)
        except ImportError:
            logger.warning("⚠️ OTLP exporter not available — falling back to console")
            _tracer_provider.add_span_processor(
                SimpleSpanProcessor(ConsoleSpanExporter())
            )
    elif export_mode == "console":
        _tracer_provider.add_span_processor(
            SimpleSpanProcessor(ConsoleSpanExporter())
        )
        logger.info("✅ OTel tracing: Console exporter")
    else:
        # noop — no processor added → spans are created but not exported
        logger.info("✅ OTel tracing: NoOp (zero overhead)")

    trace.set_tracer_provider(_tracer_provider)

    # --- Metrics -----------------------------------------------------------
    if export_mode == "otlp":
        try:
            from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter

            metric_reader = PeriodicExportingMetricReader(
                OTLPMetricExporter(endpoint=endpoint, insecure=True),
                export_interval_millis=15_000,
            )
        except ImportError:
            metric_reader = PeriodicExportingMetricReader(
                ConsoleMetricExporter(), export_interval_millis=60_000
            )
    elif export_mode == "console":
        metric_reader = PeriodicExportingMetricReader(
            ConsoleMetricExporter(), export_interval_millis=30_000
        )
    else:
        metric_reader = None  # type: ignore[assignment]

    if metric_reader is not None:
        _meter_provider = MeterProvider(resource=resource, metric_readers=[metric_reader])
    else:
        _meter_provider = MeterProvider(resource=resource)

    metrics.set_meter_provider(_meter_provider)

    _initialized = True
    logger.info(
        "✅ OpenTelemetry initialized: service=%s  mode=%s  env=%s",
        service_name,
        export_mode,
        environment,
    )


def shutdown_observability() -> None:
    """Flush and shut down all OTel providers.  Call on app exit."""
    global _initialized, _tracer_provider, _meter_provider

    if _tracer_provider is not None:
        _tracer_provider.shutdown()
    if _meter_provider is not None:
        _meter_provider.shutdown()
    _initialized = False
    logger.info("🔚 OpenTelemetry shut down")


def get_tracer(name: str = __name__) -> trace.Tracer:
    """Return a ``Tracer`` from the global provider (safe even before init)."""
    return trace.get_tracer(name)


def get_meter(name: str = __name__) -> metrics.Meter:
    """Return a ``Meter`` from the global provider (safe even before init)."""
    return metrics.get_meter(name)
