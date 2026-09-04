"""
VRAM Monitor — SOTA GPU Memory Observability
==============================================

Provides real-time VRAM monitoring for systems running multiple GPU-resident
models (LLM + Embedding + optional others).

Features:
  - Snapshot: one-shot VRAM usage report
  - Threshold alerts: log WARNING when VRAM exceeds configurable percentage
  - Defragmentation: trigger torch.cuda.empty_cache() when fragmentation high
  - Per-model attribution (when possible via torch memory tracking)

Design decisions (SOTA rationale):
    - Uses NVML (via pynvml) for system-level VRAM — covers ALL processes
    - Falls back to torch.cuda for process-level stats if pynvml unavailable
    - Zero overhead when GPU is not available (no-op pattern)
    - Thread-safe singleton for shared monitoring across modules
    - Calls nvmlShutdown() on process exit when NVML was initialized

References:
  - PyTorch Memory Management: pytorch.org/docs/stable/notes/cuda.html
  - NVIDIA NVML: developer.nvidia.com/nvidia-management-library-nvml

Author: VRAM Optimization Suite
Date: 2026-02
"""

from __future__ import annotations

import atexit
import logging
import os
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, cast

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════════════
# Data Structures
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class VRAMSnapshot:
    """Single point-in-time VRAM measurement."""
    timestamp: float
    total_gb: float
    used_gb: float
    free_gb: float
    utilization_pct: float
    # PyTorch-specific (process-level)
    torch_allocated_gb: float = 0.0
    torch_reserved_gb: float = 0.0
    torch_fragmentation_gb: float = 0.0  # reserved - allocated
    source: str = "unknown"  # "nvml" or "torch"

    def __repr__(self) -> str:
        return (
            f"VRAM[{self.utilization_pct:.1f}% | "
            f"used={self.used_gb:.2f}/{self.total_gb:.2f} GB | "
            f"torch_alloc={self.torch_allocated_gb:.2f} GB | "
            f"frag={self.torch_fragmentation_gb:.2f} GB | "
            f"src={self.source}]"
        )


@dataclass
class VRAMThresholds:
    """Adaptive thresholds used by alerting and defragmentation."""

    alert_pct: float
    pressure_pct: float
    defrag_frag_gb: float
    source: str
    reason: str


# ═══════════════════════════════════════════════════════════════════════════════
# nvidia-smi CLI (systemweites Fallback ohne pynvml)
# ═══════════════════════════════════════════════════════════════════════════════

_NVIDIA_SMI_TIMEOUT_SEC = 5.0


def query_nvidia_smi(timeout_sec: float = _NVIDIA_SMI_TIMEOUT_SEC) -> Optional[List[Dict[str, Any]]]:
    """Systemweite GPU-Zustände per nvidia-smi CLI (Fallback ohne pynvml).

    Liefert eine Liste je sichtbarer GPU (NVML-Reihenfolge) mit:
        nvml_index, name, total_bytes, used_bytes, free_bytes, temp_c

    Returns:
        Liste von GPU-Dicts oder None, wenn nvidia-smi fehlt/fehlschlägt.
    """
    import shutil
    import subprocess

    exe = shutil.which("nvidia-smi")
    if not exe and os.name == "nt":
        windows_default = r"C:\Windows\System32\nvidia-smi.exe"
        if os.path.isfile(windows_default):
            exe = windows_default
    if not exe:
        return None

    try:
        result = subprocess.run(
            [
                exe,
                "--query-gpu=index,name,memory.total,memory.used,memory.free,temperature.gpu",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.debug("[VRAM] nvidia-smi CLI nicht ausführbar: %s", exc)
        return None
    if result.returncode != 0:
        logger.debug(
            "[VRAM] nvidia-smi CLI Fehler (rc=%d): %s",
            result.returncode,
            (result.stderr or "").strip(),
        )
        return None

    gpus: List[Dict[str, Any]] = []
    for line in result.stdout.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 5:
            continue
        try:
            nvml_index = int(parts[0])
            total_bytes = int(float(parts[2])) * 1024**2
            used_bytes = int(float(parts[3])) * 1024**2
            free_bytes = int(float(parts[4])) * 1024**2
        except ValueError:
            continue
        try:
            temp_c: Optional[int] = int(parts[5])
        except (IndexError, ValueError):
            temp_c = None
        gpus.append(
            {
                "nvml_index": nvml_index,
                "name": parts[1],
                "total_bytes": total_bytes,
                "used_bytes": used_bytes,
                "free_bytes": free_bytes,
                "temp_c": temp_c,
            }
        )
    return gpus or None


def get_all_gpu_snapshots() -> List[Dict[str, Any]]:
    """Systemweites Snapshot aller sichtbaren GPUs inkl. LLM/AUX-Rolle.

    Backend: nvidia-smi CLI (deckt beide GPUs ab, unabhängig von Torch/pynvml).
    Rollen-/Index-Mapping: utils.gpu_devices.get_placement() (Single Source of Truth).

    Returns:
        Liste je GPU mit: nvml_index, cuda_index, role, name, total_gb,
        used_gb, free_gb, utilization_pct, temp_c. Leere Liste ohne nvidia-smi.
    """
    raw = query_nvidia_smi()
    if not raw:
        return []

    role_by_nvml: Dict[int, str] = {}
    cuda_by_nvml: Dict[int, int] = {}
    try:
        from utils.gpu_devices import get_placement

        p = get_placement()
        if p.llm_nvml == p.aux_nvml:
            # Single-GPU: LLM und AUX teilen dieselbe GPU — zusammengesetzte
            # Rolle, damit Konsumenten die GPU nicht verfehlen (2026-09-04).
            role_by_nvml = {p.llm_nvml: "LLM+AUX"}
        else:
            role_by_nvml = {p.llm_nvml: "LLM", p.aux_nvml: "AUX"}
        cuda_by_nvml = {p.llm_nvml: p.llm_cuda, p.aux_nvml: p.aux_cuda}
    except Exception:
        pass

    out: List[Dict[str, Any]] = []
    for g in raw:
        total = g["total_bytes"]
        out.append(
            {
                "nvml_index": g["nvml_index"],
                "cuda_index": cuda_by_nvml.get(g["nvml_index"]),
                "role": role_by_nvml.get(g["nvml_index"], "GPU"),
                "name": g["name"],
                "total_gb": total / (1024**3),
                "used_gb": g["used_bytes"] / (1024**3),
                "free_gb": g["free_bytes"] / (1024**3),
                "utilization_pct": (g["used_bytes"] / total * 100.0) if total > 0 else 0.0,
                "temp_c": g["temp_c"],
                "source": "nvidia-smi",
            }
        )
    return out


# ═══════════════════════════════════════════════════════════════════════════════
# VRAM Monitor (Singleton)
# ═══════════════════════════════════════════════════════════════════════════════

_monitor_lock = threading.Lock()
_monitor_instance: Optional["VRAMMonitor"] = None


def get_vram_monitor() -> "VRAMMonitor":
    """Get or create the singleton VRAMMonitor."""
    global _monitor_instance
    if _monitor_instance is not None:
        return _monitor_instance
    with _monitor_lock:
        if _monitor_instance is not None:
            return _monitor_instance
        _monitor_instance = VRAMMonitor()
        return _monitor_instance


class VRAMMonitor:
    """
    GPU VRAM Monitor with alerting and defragmentation.
    
    Usage:
        monitor = get_vram_monitor()
        
        # One-shot snapshot
        snap = monitor.snapshot()
        print(snap)  # VRAM[85.3% | used=20.47/24.00 GB | ...]
        
        # Check + alert if threshold exceeded
        monitor.check_and_alert(threshold_pct=90.0)
        
        # Defragment if fragmentation > threshold
        monitor.defragment_if_needed(frag_threshold_gb=0.5)
    """

    def __init__(self, device_id: int = 0) -> None:
        self.device_id = device_id
        # CUDA-Runtime-Index ≠ NVML-Index (UUID-basierte Auflösung, siehe utils.gpu_devices)
        self._nvml_index = self._resolve_nvml_index(device_id)
        self._cli_available = False
        self._pid = os.getpid()
        self._state_lock = threading.RLock()
        self._cuda_available = False
        self._nvml_available = False
        self._nvml_initialized = False
        self._nvml_handle: Any = None
        self._torch: Any = None
        self._pynvml: Any = None
        self._history: List[VRAMSnapshot] = []
        self._max_history = 100
        self._alert_cooldown_sec = 60.0
        self._last_alert_time = 0.0
        self._warned: set[str] = set()
        self._latest_snapshot: Optional[VRAMSnapshot] = None

        # Adaptive threshold state
        self._adaptive_alpha = 0.2
        self._ema_utilization_pct: Optional[float] = None
        self._ema_fragmentation_gb: Optional[float] = None
        self._runtime_profile: Dict[str, Any] = {
            "model_family": "unknown",
            "n_ctx": None,
            "workload": "general",
        }

        # Optional telemetry backend (prometheus/otel), lazy and fail-open.
        self._telemetry: Any = None
        self._telemetry_lock = threading.Lock()

        self._init_backends()
        atexit.register(self.close)

    def _warn_once(self, key: str, message: str) -> None:
        if key in self._warned:
            return
        self._warned.add(key)
        logger.warning(message)

    @staticmethod
    def _resolve_nvml_index(cuda_index: int) -> int:
        """CUDA-Runtime-Index → NVML-Index (UUID-basiert, nie-failing).

        Auf diesem System sind beide Reihenfolgen vertauscht
        (CUDA 0 = RTX 4090 = NVML 1, CUDA 1 = RTX 3060 Ti = NVML 0).
        Ohne Mapping würde NVML/pynvml die falsche GPU befragen.
        Single Source of Truth: utils.gpu_devices.get_placement().
        """
        try:
            from utils.gpu_devices import get_placement

            p = get_placement()
            if cuda_index == p.llm_cuda:
                return p.llm_nvml
            if cuda_index == p.aux_cuda:
                return p.aux_nvml
        except Exception as exc:
            logger.debug("[VRAM] NVML-Index-Auflösung fehlgeschlagen (%s); Identity-Mapping", exc)
        return cuda_index

    def _try_init_telemetry(self) -> None:
        """Initialize optional telemetry backend exactly once."""
        if self._telemetry is not None:
            return
        with self._telemetry_lock:
            if self._telemetry is not None:
                return
            try:
                from utils.vram_telemetry import get_vram_telemetry

                self._telemetry = get_vram_telemetry(device_id=self.device_id)
            except Exception as exc:
                self._telemetry = False
                logger.debug("[VRAM] telemetry backend unavailable: %s", exc)

    def _emit_telemetry_snapshot(self, snapshot: VRAMSnapshot, thresholds: VRAMThresholds) -> None:
        self._try_init_telemetry()
        backend = self._telemetry
        if not backend or backend is False:
            return
        try:
            backend.observe_snapshot(snapshot=snapshot, thresholds=thresholds, profile=dict(self._runtime_profile))
        except Exception as exc:
            logger.debug("[VRAM] telemetry snapshot export skipped: %s", exc)

    def _emit_telemetry_event(self, event_name: str, payload: Dict[str, Any]) -> None:
        self._try_init_telemetry()
        backend = self._telemetry
        if not backend or backend is False:
            return
        try:
            backend.observe_event(event_name=event_name, payload=payload, profile=dict(self._runtime_profile))
        except Exception as exc:
            logger.debug("[VRAM] telemetry event export skipped: %s", exc)

    def _update_adaptive_state(self, snapshot: VRAMSnapshot) -> None:
        """Update EMA state for adaptive thresholding."""
        if self._ema_utilization_pct is None:
            self._ema_utilization_pct = snapshot.utilization_pct
        else:
            self._ema_utilization_pct = (
                (1.0 - self._adaptive_alpha) * self._ema_utilization_pct
                + self._adaptive_alpha * snapshot.utilization_pct
            )

        frag = snapshot.torch_fragmentation_gb
        if self._ema_fragmentation_gb is None:
            self._ema_fragmentation_gb = frag
        else:
            self._ema_fragmentation_gb = (
                (1.0 - self._adaptive_alpha) * self._ema_fragmentation_gb
                + self._adaptive_alpha * frag
            )

    def set_runtime_profile(
        self,
        *,
        model_family: Optional[str] = None,
        n_ctx: Optional[int] = None,
        workload: Optional[str] = None,
    ) -> None:
        """Set runtime context used by adaptive thresholding and telemetry labels.

        Low-cardinality dimensions only, to keep metrics backend stable.
        """
        with self._state_lock:
            if model_family:
                self._runtime_profile["model_family"] = str(model_family)
            if n_ctx is not None:
                try:
                    self._runtime_profile["n_ctx"] = int(n_ctx)
                except Exception:
                    pass
            if workload:
                self._runtime_profile["workload"] = str(workload)

    def get_adaptive_thresholds(self) -> VRAMThresholds:
        """Compute adaptive thresholds using workload + EMA trends.

        Strategy:
          - Start from safe baseline thresholds.
          - Tighten under large context windows or interactive workloads.
          - Tighten when EMA trend indicates sustained pressure.
          - Keep values clamped to stable operating ranges.
        """
        with self._state_lock:
            n_ctx = self._runtime_profile.get("n_ctx")
            workload = str(self._runtime_profile.get("workload", "general"))

            base_alert = 90.0
            base_defrag = 0.50
            reason_parts: List[str] = ["baseline"]

            if isinstance(n_ctx, int) and n_ctx >= 32768:
                base_alert -= 3.0
                base_defrag -= 0.15
                reason_parts.append("high_ctx>=32768")
            elif isinstance(n_ctx, int) and n_ctx >= 16384:
                base_alert -= 1.0
                base_defrag -= 0.05
                reason_parts.append("mid_ctx>=16384")

            if workload in {"interactive", "react_agent"}:
                base_alert -= 1.0
                base_defrag -= 0.05
                reason_parts.append("interactive_workload")

            if self._ema_utilization_pct is not None:
                trend_alert = self._ema_utilization_pct + 6.0
                base_alert = min(base_alert, trend_alert)
                reason_parts.append("ema_util")

            if self._ema_fragmentation_gb is not None:
                trend_defrag = max(0.20, self._ema_fragmentation_gb * 0.85)
                base_defrag = min(base_defrag, trend_defrag)
                reason_parts.append("ema_frag")

            alert_pct = max(84.0, min(94.0, base_alert))
            defrag_frag_gb = max(0.20, min(1.20, base_defrag))
            pressure_pct = max(alert_pct + 2.0, min(98.0, alert_pct + 4.0))

            source = "adaptive" if len(reason_parts) > 1 else "fixed"
            return VRAMThresholds(
                alert_pct=alert_pct,
                pressure_pct=pressure_pct,
                defrag_frag_gb=defrag_frag_gb,
                source=source,
                reason=",".join(reason_parts),
            )

    def _init_backends(self) -> None:
        """Initialize NVML and/or PyTorch CUDA backends."""
        # Try PyTorch CUDA
        try:
            import torch
            self._torch = torch
            if torch.cuda.is_available():
                self._cuda_available = True
                logger.debug("[VRAM] PyTorch CUDA backend available")
        except ImportError:
            logger.debug("[VRAM] PyTorch not available; CUDA monitoring disabled")

        # Try NVML (system-level, covers all processes)
        try:
            import pynvml  # type: ignore[import-untyped]
            self._pynvml = pynvml
            pynvml.nvmlInit()
            device_count = pynvml.nvmlDeviceGetCount()
            if self._nvml_index < 0 or self._nvml_index >= device_count:
                raise ValueError(
                    f"nvml_index {self._nvml_index} (cuda:{self.device_id}) out of range "
                    f"for {device_count} detected CUDA device(s)"
                )
            self._nvml_handle = pynvml.nvmlDeviceGetHandleByIndex(self._nvml_index)
            self._nvml_available = True
            self._nvml_initialized = True
            logger.debug("[VRAM] NVML backend available (system-level monitoring)")
        except ImportError:
            logger.debug("[VRAM] pynvml not available, using PyTorch-only monitoring")
        except Exception as e:
            self._nvml_handle = None
            self._nvml_available = False
            self._nvml_initialized = False
            self._warn_once(
                "nvml_init_failed",
                f"[VRAM] NVML backend unavailable: {e}. Falling back to torch stats when possible.",
            )

        # nvidia-smi CLI (systemweites Fallback ohne pynvml, deckt beide GPUs ab)
        try:
            self._cli_available = query_nvidia_smi() is not None
            if self._cli_available:
                logger.debug("[VRAM] nvidia-smi CLI backend available (system-level fallback)")
        except Exception:
            self._cli_available = False

        if not self.is_available:
            self._warn_once(
                "vram_unavailable",
                "[VRAM] GPU monitoring unavailable: neither NVML, nvidia-smi CLI nor PyTorch CUDA is usable.",
            )

    @property
    def is_available(self) -> bool:
        """Whether any GPU monitoring backend is available."""
        return self._cuda_available or self._nvml_available or self._cli_available

    def snapshot(self) -> Optional[VRAMSnapshot]:
        """Take a VRAM usage snapshot.
        
        Returns:
            VRAMSnapshot or None if no GPU available.
        """
        with self._state_lock:
            if not self.is_available:
                return None

            snap = VRAMSnapshot(
                timestamp=time.time(),
                total_gb=0.0,
                used_gb=0.0,
                free_gb=0.0,
                utilization_pct=0.0,
            )

            # System-level via NVML (preferred — covers all processes)
            if self._nvml_available and self._pynvml is not None and self._nvml_handle is not None:
                try:
                    mem_info = self._pynvml.nvmlDeviceGetMemoryInfo(self._nvml_handle)
                    if mem_info.total <= 0:
                        raise ValueError("NVML reported zero total memory")
                    snap.total_gb = mem_info.total / (1024**3)
                    snap.used_gb = mem_info.used / (1024**3)
                    snap.free_gb = mem_info.free / (1024**3)
                    snap.utilization_pct = (mem_info.used / mem_info.total) * 100
                    snap.source = "nvml"
                except Exception as e:
                    self._nvml_available = False
                    self._nvml_initialized = False
                    self._nvml_handle = None
                    self._warn_once(
                        "nvml_snapshot_failed",
                        f"[VRAM] NVML snapshot failed: {e}. Falling back to torch-only monitoring.",
                    )

            # System-level via nvidia-smi CLI (Fallback ohne pynvml, systemweit)
            if snap.total_gb <= 0 and self._cli_available:
                try:
                    gpus = query_nvidia_smi() or []
                    entry = next(
                        (g for g in gpus if g["nvml_index"] == self._nvml_index),
                        gpus[0] if gpus else None,
                    )
                    if entry is not None and entry["total_bytes"] > 0:
                        snap.total_gb = entry["total_bytes"] / (1024**3)
                        snap.used_gb = entry["used_bytes"] / (1024**3)
                        snap.free_gb = entry["free_bytes"] / (1024**3)
                        snap.utilization_pct = (entry["used_bytes"] / entry["total_bytes"]) * 100.0
                        snap.source = "nvidia-smi"
                except Exception as e:
                    self._cli_available = False
                    self._warn_once(
                        "cli_snapshot_failed",
                        f"[VRAM] nvidia-smi CLI snapshot failed: {e}. Falling back to torch-only monitoring.",
                    )

            # Process-level via PyTorch (always collect if available)
            if self._cuda_available and self._torch is not None:
                try:
                    snap.torch_allocated_gb = self._torch.cuda.memory_allocated(self.device_id) / (1024**3)
                    snap.torch_reserved_gb = self._torch.cuda.memory_reserved(self.device_id) / (1024**3)
                    snap.torch_fragmentation_gb = max(0.0, snap.torch_reserved_gb - snap.torch_allocated_gb)

                    # If no system-level backend provided totals, use torch stats
                    if not self._nvml_available and snap.total_gb <= 0:
                        total = self._torch.cuda.get_device_properties(self.device_id).total_memory
                        if total <= 0:
                            raise ValueError("PyTorch reported zero total GPU memory")
                        snap.total_gb = total / (1024**3)
                        snap.used_gb = snap.torch_reserved_gb
                        snap.free_gb = snap.total_gb - snap.used_gb
                        snap.utilization_pct = (snap.used_gb / snap.total_gb) * 100 if snap.total_gb > 0 else 0.0
                        snap.source = "torch"
                except Exception as e:
                    self._cuda_available = False
                    self._warn_once(
                        "torch_snapshot_failed",
                        f"[VRAM] PyTorch snapshot failed: {e}. GPU process-level stats disabled.",
                    )

            if snap.total_gb <= 0:
                return None

            # Store in history (ring buffer)
            self._history.append(snap)
            if len(self._history) > self._max_history:
                self._history = self._history[-self._max_history:]
            self._latest_snapshot = snap
            self._update_adaptive_state(snap)

            thresholds = self.get_adaptive_thresholds()
            self._emit_telemetry_snapshot(snapshot=snap, thresholds=thresholds)

            return snap

    def check_and_alert(self, threshold_pct: Optional[float] = None) -> Optional[VRAMSnapshot]:
        """Take a snapshot and log a WARNING if utilization exceeds threshold.
        
        Args:
            threshold_pct: Optional fixed threshold. If None, adaptive thresholding is used.
            
        Returns:
            The snapshot taken, or None if no GPU.
        """
        snap = self.snapshot()
        if snap is None:
            return None

        now = time.time()
        thresholds = self.get_adaptive_thresholds()
        effective_threshold = threshold_pct if threshold_pct is not None else thresholds.alert_pct
        if snap.utilization_pct >= effective_threshold:
            if (now - self._last_alert_time) >= self._alert_cooldown_sec:
                logger.warning(
                    f"⚠️ [VRAM ALERT] GPU memory at {snap.utilization_pct:.1f}% "
                    f"({snap.used_gb:.2f}/{snap.total_gb:.2f} GB) — "
                    f"threshold: {effective_threshold:.0f}% ({thresholds.source}) | "
                    f"torch_alloc={snap.torch_allocated_gb:.2f} GB, "
                    f"frag={snap.torch_fragmentation_gb:.2f} GB"
                )
                self._last_alert_time = now
                self._emit_telemetry_event(
                    event_name="vram_alert",
                    payload={
                        "utilization_pct": snap.utilization_pct,
                        "threshold_pct": effective_threshold,
                        "source": thresholds.source,
                    },
                )
        else:
            logger.debug(
                f"[VRAM] OK: {snap.utilization_pct:.1f}% "
                f"({snap.used_gb:.2f}/{snap.total_gb:.2f} GB)"
            )

        return snap

    def defragment_if_needed(self, frag_threshold_gb: Optional[float] = None) -> bool:
        """Run torch.cuda.empty_cache() if fragmentation exceeds threshold.
        
        Fragmentation = torch.cuda.memory_reserved() - torch.cuda.memory_allocated()
        This represents memory that PyTorch's caching allocator is holding
        but not actively using.
        
        Args:
            frag_threshold_gb: Optional fixed threshold. If None, adaptive thresholding is used.
            
        Returns:
            True if defragmentation was performed.
        """
        if not self._cuda_available or self._torch is None:
            return False

        try:
            allocated = self._torch.cuda.memory_allocated(self.device_id) / (1024**3)
            reserved = self._torch.cuda.memory_reserved(self.device_id) / (1024**3)
            fragmentation = max(0.0, reserved - allocated)

            thresholds = self.get_adaptive_thresholds()
            effective_frag_threshold = (
                frag_threshold_gb if frag_threshold_gb is not None else thresholds.defrag_frag_gb
            )

            if fragmentation >= effective_frag_threshold:
                self._torch.cuda.empty_cache()
                new_reserved = self._torch.cuda.memory_reserved(self.device_id) / (1024**3)
                freed = reserved - new_reserved
                logger.info(
                    f"🧹 [VRAM] Defragmented: freed {freed:.2f} GB "
                    f"(was {fragmentation:.2f} GB fragmented, "
                    f"threshold={effective_frag_threshold:.2f} GB {thresholds.source})"
                )
                self._emit_telemetry_event(
                    event_name="vram_defragment",
                    payload={
                        "fragmentation_gb": fragmentation,
                        "threshold_gb": effective_frag_threshold,
                        "freed_gb": freed,
                    },
                )
                return True
        except Exception as e:
            self._warn_once(
                "defragmentation_failed",
                f"[VRAM] Defragmentation failed: {e}",
            )

        return False

    def get_process_usage(self, limit: int = 20) -> List[Dict[str, Any]]:
        """Return per-process VRAM consumers (best effort, NVML-only).

        The function prefers v3 API, then v2, then legacy API to stay
        compatible across driver versions.
        """
        with self._state_lock:
            if not self._nvml_available or self._pynvml is None or self._nvml_handle is None:
                return []

            getter = (
                getattr(self._pynvml, "nvmlDeviceGetComputeRunningProcesses_v3", None)
                or getattr(self._pynvml, "nvmlDeviceGetComputeRunningProcesses_v2", None)
                or getattr(self._pynvml, "nvmlDeviceGetComputeRunningProcesses", None)
            )
            if not callable(getter):
                return []

            try:
                processes = getter(self._nvml_handle)
            except Exception as exc:
                self._warn_once(
                    "nvml_process_usage_failed",
                    f"[VRAM] NVML process query failed: {exc}",
                )
                return []

        process_iter = cast(Iterable[Any], processes or [])
        usage: List[Dict[str, Any]] = []
        for proc in process_iter:
            used_bytes = getattr(proc, "usedGpuMemory", 0)
            if used_bytes in (-1, None):
                used_bytes = 0
            pid = int(getattr(proc, "pid", -1))
            item: Dict[str, Any] = {
                "pid": pid,
                "used_gb": float(used_bytes) / (1024**3),
                "is_current_process": pid == self._pid,
            }
            usage.append(item)

        usage.sort(key=lambda x: x["used_gb"], reverse=True)
        return usage[: max(1, limit)]

    def close(self) -> None:
        """Release backend resources if NVML was initialized."""
        with self._state_lock:
            if not self._nvml_initialized or self._pynvml is None:
                return

            try:
                self._pynvml.nvmlShutdown()
            except Exception as e:
                logger.debug(f"[VRAM] NVML shutdown ignored: {e}")
            finally:
                self._nvml_initialized = False
                self._nvml_available = False
                self._nvml_handle = None

    def get_summary(self) -> Dict[str, Any]:
        """Get a summary dict suitable for logging or API response."""
        snap = self.snapshot()
        if snap is None:
            return {"gpu_available": False}

        thresholds = self.get_adaptive_thresholds()

        return {
            "gpu_available": True,
            "total_gb": round(snap.total_gb, 2),
            "used_gb": round(snap.used_gb, 2),
            "free_gb": round(snap.free_gb, 2),
            "utilization_pct": round(snap.utilization_pct, 1),
            "torch_allocated_gb": round(snap.torch_allocated_gb, 2),
            "torch_reserved_gb": round(snap.torch_reserved_gb, 2),
            "fragmentation_gb": round(snap.torch_fragmentation_gb, 2),
            "source": snap.source,
            "adaptive_alert_pct": round(thresholds.alert_pct, 1),
            "adaptive_pressure_pct": round(thresholds.pressure_pct, 1),
            "adaptive_defrag_gb": round(thresholds.defrag_frag_gb, 2),
            "adaptive_source": thresholds.source,
            "runtime_profile": dict(self._runtime_profile),
        }

    def log_status(self, label: str = "") -> None:
        """Log current VRAM status at INFO level."""
        snap = self.snapshot()
        if snap is None:
            return
        prefix = f"[{label}] " if label else ""
        logger.info(f"📊 {prefix}VRAM: {snap}")


__all__ = [
    "VRAMMonitor",
    "VRAMSnapshot",
    "VRAMThresholds",
    "get_vram_monitor",
    "query_nvidia_smi",
    "get_all_gpu_snapshots",
]
