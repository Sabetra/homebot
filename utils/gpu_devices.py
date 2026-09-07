"""
Dual-GPU-Platzierung — Single Source of Truth für LLM-/AUX-GPU-Zuordnung.
===========================================================================

Setup (Windows 11, 2× NVIDIA):
    LLM-GPU:   größte GPU   → RTX 4090 (24 GB)   — llama.cpp (main_gpu), explizit kein Split
    AUX-GPU:   kleinere GPU → RTX 3060 Ti (8 GB) — Embeddings, Reranker, NLI, OCR, Docling

WICHTIG: CUDA-Runtime-Index (Torch / llama.cpp / ONNX Runtime) und NVML-Index
(nvidia-smi / pynvml) sind NICHT dieselbe Reihenfolge. Diese Auflösung erfolgt
über die GPU-UUIDs, nicht über die Positionsnummer:

    torch.cuda.get_device_properties(i).uuid  ↔  nvmlDeviceGetUUID(handle)

Overrides (Umgebungsvariablen, Integer = CUDA-Runtime-Index):
    BOT_LLM_CUDA_DEVICE  z. B. "0"  (Default: GPU mit dem meisten VRAM)
    BOT_AUX_CUDA_DEVICE  z. B. "1"  (Default: GPU mit dem wenigsten VRAM)

Der Modul ist nie-failing: Bei fehlendem Torch/pynvml oder Fehlern wird
konservativ (LLM=cuda:0, AUX=cuda:0, single_gpu) zurückgefallen und
gewarnt — statt die App-Initialisierung zu brechen.
"""

from __future__ import annotations

import logging
import os
import sys
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class GPUInfo:
    """Eine sichtbare GPU aus Sicht der CUDA-Runtime."""

    cuda_index: int      # Index in CUDA-Runtime-Reihenfolge (Torch/llama.cpp/ONNX)
    name: str            # z. B. "NVIDIA GeForce RTX 4090"
    vram_gb: float       # Gesamt-VRAM in GB
    uuid: str = ""       # NVML-UUID ("GPU-..."), für CUDA↔NVML-Mapping


@dataclass(frozen=True)
class GPUPlacement:
    """Zuordnung LLM→GPU und AUX→GPU inkl. beider Index-Systeme."""

    llm_cuda: int
    aux_cuda: int
    llm_nvml: int
    aux_nvml: int
    llm: GPUInfo
    aux: GPUInfo
    single_gpu: bool

    @property
    def aux_device_string(self) -> str:
        """Device-String für Torch/SentenceTransformers/EasyOCR auf der AUX-GPU."""
        return "cuda" if self.single_gpu else f"cuda:{self.aux_cuda}"

    @property
    def llm_device_string(self) -> str:
        return "cuda" if self.single_gpu else f"cuda:{self.llm_cuda}"

    def describe(self) -> str:
        """Menschenlesbare Ein-Zeilen-Zusammenfassung (Logs/Startup)."""
        if self.single_gpu:
            return (
                f"GPU-Platzierung (Single-GPU): LLM+AUX → cuda:{self.llm_cuda} "
                f"({self.llm.name}, {self.llm.vram_gb:.0f} GB)"
            )
        return (
            f"GPU-Platzierung: LLM → cuda:{self.llm_cuda} "
            f"({self.llm.name}, {self.llm.vram_gb:.0f} GB, NVML:{self.llm_nvml}) | "
            f"AUX → cuda:{self.aux_cuda} "
            f"({self.aux.name}, {self.aux.vram_gb:.0f} GB, NVML:{self.aux_nvml})"
        )


_PLACEMENT_CACHE: Optional[GPUPlacement] = None


# ── Detektion ──────────────────────────────────────────────────────────────

def _detect_via_torch() -> list[GPUInfo]:
    """GPU-Liste in CUDA-Runtime-Reihenfolge (via Torch)."""
    import torch

    if not torch.cuda.is_available():
        raise RuntimeError("torch.cuda nicht verfügbar")

    gpus: list[GPUInfo] = []
    for i in range(torch.cuda.device_count()):
        props = torch.cuda.get_device_properties(i)
        try:
            uuid = str(getattr(props, "uuid", "") or "")
        except Exception:
            uuid = ""
        gpus.append(
            GPUInfo(
                cuda_index=i,
                name=str(torch.cuda.get_device_name(i)),
                vram_gb=round(props.total_memory / (1024 ** 3), 1),
                uuid=uuid,
            )
        )
    return gpus


def _detect_via_nvml() -> list[GPUInfo]:
    """Fallback ohne Torch: pynvml (NVML-Reihenfolge, nur Näherung!).

    Ohne CUDA-Runtime-Ordnung kann hier nicht garantiert werden, dass
    Index i in pynvml auch cuda:i ist. Daher wird nur als Notnagel genutzt
    und entsprechend gewarnt.
    """
    import pynvml

    pynvml.nvmlInit()
    gpus: list[GPUInfo] = []
    for i in range(pynvml.nvmlDeviceGetCount()):
        h = pynvml.nvmlDeviceGetHandleByIndex(i)
        gpus.append(
            GPUInfo(
                cuda_index=i,  # ACHTUNG: NVML-Index, nicht zwingend CUDA-Index
                name=str(pynvml.nvmlDeviceGetName(h)),
                vram_gb=round(pynvml.nvmlDeviceGetTotalMemory(h) / (1024 ** 3), 1),
                uuid=str(pynvml.nvmlDeviceGetUUID(h)),
            )
        )
    logger.warning(
        "gpu_devices: GPU-Detektion via pynvml (keine CUDA-Runtime-Ordnung) — "
        "Index-Zuordnung ist eine Näherung."
    )
    return gpus


def detect_gpus() -> list[GPUInfo]:
    """Alle sichtbaren GPUs, bevorzugt in CUDA-Runtime-Reihenfolge.

    Nie-failing: liefert mindestens eine (unbekannte) GPU.
    """
    for detector, label in ((_detect_via_torch, "torch"), (_detect_via_nvml, "pynvml")):
        try:
            gpus = detector()
            if gpus:
                return gpus
        except Exception as e:  # noqa: BLE001 — Deliberat: Fallback-Kette, nie werfen
            logger.debug("gpu_devices: Detektion via %s fehlgeschlagen: %s", label, e)
    return [GPUInfo(cuda_index=0, name="unknown", vram_gb=0.0)]


# ── CUDA ↔ NVML Mapping ───────────────────────────────────────────────────

def _norm_uuid(u: str) -> str:
    """Normalisiert GPU-UUIDs: 'GPU-xxx' / 'xxx' → kleingeschriebener Kern."""
    s = str(u or "").strip().lower()
    return s[4:] if s.startswith("gpu-") else s


def _nvml_uuids_via_nvidia_smi() -> dict[str, int]:
    """UUID → NVML-Index via `nvidia-smi` CLI (ohne pynvml, immer verfügbar)."""
    import subprocess

    out = subprocess.run(
        ["nvidia-smi", "--query-gpu=index,uuid", "--format=csv,noheader"],
        capture_output=True, text=True, timeout=15, check=True,
    ).stdout
    result: dict[str, int] = {}
    for line in out.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) >= 2 and parts[0].isdigit():
            result[_norm_uuid(parts[1])] = int(parts[0])
    return result


def _cuda_to_nvml_map(gpus: list[GPUInfo]) -> dict[int, int]:
    """Mappe CUDA-Runtime-Index → NVML-Index über gemeinsame GPU-UUIDs.

    Reihenfolge: nvidia-smi-CLI (Primary) → pynvml → Identität (mit Warnung).
    Identität ist auf Single-GPU-Systemen korrekt, auf Multi-GPU-Systemen nur
    dann, wenn beide Ordnungen zufällig übereinstimmen.
    """
    nvml_uuid_to_index: dict[str, int] = {}

    # 1) nvidia-smi CLI
    try:
        nvml_uuid_to_index = _nvml_uuids_via_nvidia_smi()
    except Exception as e:  # noqa: BLE001
        logger.debug("gpu_devices: nvidia-smi-CLI nicht verfügbar: %s", e)

    # 2) pynvml (falls installiert)
    if not nvml_uuid_to_index:
        try:
            import pynvml

            pynvml.nvmlInit()
            for i in range(pynvml.nvmlDeviceGetCount()):
                h = pynvml.nvmlDeviceGetHandleByIndex(i)
                nvml_uuid_to_index[_norm_uuid(pynvml.nvmlDeviceGetUUID(h))] = i
        except Exception as e:  # noqa: BLE001
            logger.debug("gpu_devices: pynvml nicht verfügbar: %s", e)

    mapping: dict[int, int] = {}
    for g in gpus:
        if _norm_uuid(g.uuid):
            hit = nvml_uuid_to_index.get(_norm_uuid(g.uuid))
            if hit is not None:
                mapping[g.cuda_index] = hit

    if not nvml_uuid_to_index:
        logger.warning(
            "gpu_devices: CUDA↔NVML-Mapping nicht auflösbar (kein nvidia-smi/pynvml) — "
            "verwende Identität (Monitoring zeigt ggf. die 'falsche' GPU an)."
        )
    elif len(mapping) != len(gpus):
        missing = [g.cuda_index for g in gpus if g.cuda_index not in mapping]
        logger.warning("gpu_devices: unvollständiges Mapping, fehlend: %s", missing)

    for g in gpus:
        mapping.setdefault(g.cuda_index, g.cuda_index)
    return mapping


# ── Öffentliche API ───────────────────────────────────────────────────────

def _env_int(name: str) -> Optional[int]:
    raw = os.environ.get(name)
    if raw is None or not str(raw).strip():
        return None
    try:
        return int(str(raw).strip())
    except ValueError:
        logger.warning("gpu_devices: %s=%r ist kein Integer — ignoriert", name, raw)
        return None


def _resolve_placement(gpus: list[GPUInfo]) -> GPUPlacement:
    """LLM=größte GPU, AUX=kleinste GPU (+ env-Overrides, NVML-Mapping)."""
    if len(gpus) == 1:
        g = gpus[0]
        return GPUPlacement(
            llm_cuda=0, aux_cuda=0, llm_nvml=0, aux_nvml=0,
            llm=g, aux=g, single_gpu=True,
        )

    nvml_map = _cuda_to_nvml_map(gpus)
    by_index = {g.cuda_index: g for g in gpus}

    # Auto: LLM = max VRAM, AUX = min VRAM (bei Gleichstand: niedrigerer Index)
    llm_idx = max(gpus, key=lambda g: (g.vram_gb, -g.cuda_index)).cuda_index
    aux_idx = min(gpus, key=lambda g: (g.vram_gb, g.cuda_index)).cuda_index

    # Env-Overrides
    llm_override = _env_int("BOT_LLM_CUDA_DEVICE")
    if llm_override is not None:
        if llm_override in by_index:
            llm_idx = llm_override
        else:
            logger.warning(
                "gpu_devices: BOT_LLM_CUDA_DEVICE=%d nicht sichtbar (0..%d) — auto bleibt",
                llm_override, len(gpus) - 1,
            )
    aux_override = _env_int("BOT_AUX_CUDA_DEVICE")
    if aux_override is not None:
        if aux_override in by_index:
            aux_idx = aux_override
        else:
            logger.warning(
                "gpu_devices: BOT_AUX_CUDA_DEVICE=%d nicht sichtbar (0..%d) — auto bleibt",
                aux_override, len(gpus) - 1,
            )

    if llm_idx == aux_idx:
        # Beide auf derselben GPU: zulassen, aber warnen (AUX frisst LLM-VRAM)
        logger.warning(
            "gpu_devices: LLM und AUX liegen auf derselben GPU (cuda:%d) — "
            "AUX-Modelle reduzieren die verfügbare LLM-VRAM.", llm_idx
        )

    return GPUPlacement(
        llm_cuda=llm_idx,
        aux_cuda=aux_idx,
        llm_nvml=nvml_map.get(llm_idx, llm_idx),
        aux_nvml=nvml_map.get(aux_idx, aux_idx),
        llm=by_index[llm_idx],
        aux=by_index[aux_idx],
        single_gpu=False,
    )


def get_placement(force: bool = False) -> GPUPlacement:
    """Liefert die (gecachte) LLM/AUX-GPU-Zuordnung. Nie-failing."""
    global _PLACEMENT_CACHE
    if _PLACEMENT_CACHE is not None and not force:
        return _PLACEMENT_CACHE

    gpus = detect_gpus()
    placement = _resolve_placement(gpus)
    _PLACEMENT_CACHE = placement
    logger.info("🎯 %s", placement.describe())
    return placement


def get_llm_cuda_index() -> int:
    """CUDA-Runtime-Index der LLM-GPU (llama.cpp main_gpu)."""
    return get_placement().llm_cuda


def get_aux_cuda_index() -> int:
    """CUDA-Runtime-Index der AUX-GPU (ONNX device_id, Torch)."""
    return get_placement().aux_cuda


def get_aux_device_string() -> str:
    """Device-String für AUX-Modelle: 'cuda:1' (bzw. 'cuda' bei Single-GPU)."""
    return get_placement().aux_device_string


def clear_cache() -> None:
    """Test-Hilfe: Zwischenspeicher leeren (z. B. nach Env-Override)."""
    global _PLACEMENT_CACHE
    _PLACEMENT_CACHE = None


def main() -> None:
    """CLI-Diagnose: python -m utils.gpu_devices"""
    # Konsolen-Codepage (z. B. CP1252) kodiert Unicode (→, 🎯) nicht —
    # ohne UTF-8 wirft print() hier UnicodeEncodeError.
    for _stream in (sys.stdout, sys.stderr):
        if hasattr(_stream, "reconfigure"):
            try:
                _stream.reconfigure(encoding="utf-8")
            except (OSError, ValueError):
                pass
    logging.basicConfig(level=logging.INFO)
    for g in detect_gpus():
        print(f"cuda:{g.cuda_index}  {g.name:<32} {g.vram_gb:>6.1f} GB  {g.uuid}")
    print(get_placement().describe())


if __name__ == "__main__":
    main()

