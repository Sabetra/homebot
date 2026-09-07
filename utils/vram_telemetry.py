"""Optional VRAM telemetry export backends (Prometheus + OpenTelemetry).

This module is intentionally fail-open: if dependencies are unavailable,
monitoring continues without telemetry export.
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def _enabled_from_env(name: str, default: str = "1") -> bool:
    value = os.environ.get(name, default).strip().lower()
    return value not in {"0", "false", "off", "no"}


class VRAMTelemetry:
    """Exports low-cardinality VRAM metrics to optional backends."""

    def __init__(self, device_id: int) -> None:
        self.device_id = str(device_id)
        self._prometheus_ready = False
        self._otel_ready = False
        self._alert_webhook_url = os.environ.get("VRAM_ALERT_WEBHOOK_URL", "").strip()

        self._prom_gauges: Dict[str, Any] = {}
        self._prom_counters: Dict[str, Any] = {}
        self._otel_instruments: Dict[str, Any] = {}

        self._init_prometheus()
        self._init_otel()

    def _init_prometheus(self) -> None:
        if not _enabled_from_env("VRAM_METRICS_PROMETHEUS", "1"):
            return
        try:
            from prometheus_client import Counter, Gauge, start_http_server

            labels = ["device_id", "source", "model_family", "workload"]
            self._prom_gauges["utilization_pct"] = Gauge(
                "vram_utilization_pct",
                "Current VRAM utilization in percent",
                labelnames=labels,
            )
            self._prom_gauges["used_gb"] = Gauge(
                "vram_used_gb",
                "Current used VRAM in GiB",
                labelnames=labels,
            )
            self._prom_gauges["free_gb"] = Gauge(
                "vram_free_gb",
                "Current free VRAM in GiB",
                labelnames=labels,
            )
            self._prom_gauges["torch_fragmentation_gb"] = Gauge(
                "vram_torch_fragmentation_gb",
                "Current PyTorch CUDA allocator fragmentation in GiB",
                labelnames=labels,
            )
            self._prom_gauges["alert_threshold_pct"] = Gauge(
                "vram_alert_threshold_pct",
                "Current adaptive alert threshold in percent",
                labelnames=labels,
            )
            self._prom_gauges["defrag_threshold_gb"] = Gauge(
                "vram_defrag_threshold_gb",
                "Current adaptive defragmentation threshold in GiB",
                labelnames=labels,
            )

            self._prom_counters["alerts_total"] = Counter(
                "vram_alerts_total",
                "Number of VRAM alert events",
                labelnames=["device_id", "event", "model_family", "workload"],
            )
            self._prom_counters["defrags_total"] = Counter(
                "vram_defragmentations_total",
                "Number of VRAM defragmentation events",
                labelnames=["device_id", "event", "model_family", "workload"],
            )

            port_raw = os.environ.get("VRAM_METRICS_HTTP_PORT")
            if port_raw:
                try:
                    port = int(port_raw)
                    start_http_server(port)
                    logger.info("[VRAM] Prometheus metrics server started on port %s", port)
                except Exception as exc:
                    logger.warning("[VRAM] Could not start Prometheus metrics server: %s", exc)

            self._prometheus_ready = True
        except Exception as exc:
            logger.debug("[VRAM] Prometheus backend unavailable: %s", exc)
            self._prometheus_ready = False

    def _init_otel(self) -> None:
        if not _enabled_from_env("VRAM_METRICS_OTEL", "1"):
            return
        try:
            from opentelemetry import metrics

            meter = metrics.get_meter("vram_monitor", "1.0.0")
            self._otel_instruments["utilization_pct"] = meter.create_histogram(
                "vram.utilization.pct",
                unit="%",
                description="Observed VRAM utilization percentage",
            )
            self._otel_instruments["used_gb"] = meter.create_histogram(
                "vram.used.gib",
                unit="GiBy",
                description="Observed used VRAM in GiB",
            )
            self._otel_instruments["fragmentation_gb"] = meter.create_histogram(
                "vram.fragmentation.gib",
                unit="GiBy",
                description="Observed PyTorch fragmentation in GiB",
            )
            self._otel_instruments["alerts_total"] = meter.create_counter(
                "vram.alerts.total",
                description="Total VRAM alert events",
            )
            self._otel_instruments["defrags_total"] = meter.create_counter(
                "vram.defrags.total",
                description="Total VRAM defragmentation events",
            )

            self._otel_ready = True
        except Exception as exc:
            logger.debug("[VRAM] OpenTelemetry backend unavailable: %s", exc)
            self._otel_ready = False

    @staticmethod
    def _attrs(profile: Dict[str, Any], source: str, device_id: str) -> Dict[str, str]:
        model_family = str(profile.get("model_family", "unknown"))
        workload = str(profile.get("workload", "general"))
        return {
            "device_id": device_id,
            "source": source,
            "model_family": model_family,
            "workload": workload,
        }

    def observe_snapshot(self, snapshot: Any, thresholds: Any, profile: Dict[str, Any]) -> None:
        attrs = self._attrs(profile=profile, source=str(snapshot.source), device_id=self.device_id)

        if self._prometheus_ready:
            gauge_labels = (
                attrs["device_id"],
                attrs["source"],
                attrs["model_family"],
                attrs["workload"],
            )
            self._prom_gauges["utilization_pct"].labels(*gauge_labels).set(float(snapshot.utilization_pct))
            self._prom_gauges["used_gb"].labels(*gauge_labels).set(float(snapshot.used_gb))
            self._prom_gauges["free_gb"].labels(*gauge_labels).set(float(snapshot.free_gb))
            self._prom_gauges["torch_fragmentation_gb"].labels(*gauge_labels).set(
                float(snapshot.torch_fragmentation_gb)
            )
            self._prom_gauges["alert_threshold_pct"].labels(*gauge_labels).set(float(thresholds.alert_pct))
            self._prom_gauges["defrag_threshold_gb"].labels(*gauge_labels).set(float(thresholds.defrag_frag_gb))

        if self._otel_ready:
            self._otel_instruments["utilization_pct"].record(float(snapshot.utilization_pct), attributes=attrs)
            self._otel_instruments["used_gb"].record(float(snapshot.used_gb), attributes=attrs)
            self._otel_instruments["fragmentation_gb"].record(
                float(snapshot.torch_fragmentation_gb),
                attributes=attrs,
            )

    def observe_event(self, event_name: str, payload: Dict[str, Any], profile: Dict[str, Any]) -> None:
        source = str(payload.get("source", "runtime"))
        attrs = self._attrs(profile=profile, source=source, device_id=self.device_id)

        if self._prometheus_ready:
            label_values = (
                self.device_id,
                event_name,
                attrs["model_family"],
                attrs["workload"],
            )
            if event_name == "vram_alert":
                self._prom_counters["alerts_total"].labels(*label_values).inc()
            elif event_name == "vram_defragment":
                self._prom_counters["defrags_total"].labels(*label_values).inc()

        if self._otel_ready:
            attrs_with_event = dict(attrs)
            attrs_with_event["event"] = event_name
            if event_name == "vram_alert":
                self._otel_instruments["alerts_total"].add(1, attributes=attrs_with_event)
            elif event_name == "vram_defragment":
                self._otel_instruments["defrags_total"].add(1, attributes=attrs_with_event)

        if event_name == "vram_alert":
            self._notify_alert_channel(payload=payload, profile=profile)

    def _notify_alert_channel(self, payload: Dict[str, Any], profile: Dict[str, Any]) -> None:
        if not self._alert_webhook_url:
            return
        try:
            import requests

            body = {
                "event": "vram_alert",
                "device_id": self.device_id,
                "profile": {
                    "model_family": str(profile.get("model_family", "unknown")),
                    "workload": str(profile.get("workload", "general")),
                    "n_ctx": profile.get("n_ctx"),
                },
                "payload": payload,
            }
            requests.post(self._alert_webhook_url, json=body, timeout=2.0)
        except Exception as exc:
            logger.debug("[VRAM] webhook alert channel skipped: %s", exc)


_telemetry_singletons: Dict[str, VRAMTelemetry] = {}
_telemetry_lock = threading.Lock()


def get_vram_telemetry(device_id: int = 0) -> VRAMTelemetry:
    key = str(device_id)
    if key in _telemetry_singletons:
        return _telemetry_singletons[key]
    with _telemetry_lock:
        if key in _telemetry_singletons:
            return _telemetry_singletons[key]
        _telemetry_singletons[key] = VRAMTelemetry(device_id=device_id)
        return _telemetry_singletons[key]


__all__ = ["VRAMTelemetry", "get_vram_telemetry"]
