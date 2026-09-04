# -*- coding: utf-8 -*-
"""Tests: Single-GPU-Rollen-Mapping (2026-09-04).

Auf Single-GPU-Systemen (LLM + AUX auf derselben GPU) muss der VRAM-Snapshot
die zusammengesetzte Rolle "LLM+AUX" tragen — sonst verfehlen Konsumenten mit
``role == "LLM"`` die LLM-GPU (scripts/model_loader.py, utils/token_scaling.py).

Isolierte Tests: utils.gpu_devices wird vor dem Import von utils.vram_monitor
per monkeypatch gesteuert, kein echter NVIDIA-Stack nötig.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _gpu(cuda_index: int, nvml_index: int, name: str = "NVIDIA GeForce RTX 4090",
         vram_gb: float = 24.0) -> SimpleNamespace:
    return SimpleNamespace(
        name=name,
        uuid=f"GPU-UUID-{cuda_index}",
        uuid_field=lambda: f"GPU-UUID-{cuda_index}",
        vram_gb=vram_gb,
        mem_used_mb=2048,
        cuda_index=cuda_index,
        nvml_index=nvml_index,
        driver="570.00",
        source="mock",
    )


def _placement(gpus: list, llm_idx: int, aux_idx: int) -> SimpleNamespace:
    return SimpleNamespace(
        llm=gpus[llm_idx],
        aux=gpus[aux_idx],
        llm_cuda=llm_idx,
        aux_cuda=aux_idx,
        llm_nvml=gpus[llm_idx].nvml_index,
        aux_nvml=gpus[aux_idx].nvml_index,
        single_gpu=(llm_idx == aux_idx),
        llm_device_string=f"cuda:{llm_idx}",
        aux_device_string=f"cuda:{aux_idx}",
        llm_provider="torch",
        aux_provider="onnx",
        llm_fallback="CPU",
        aux_fallback="CPU",
        aux_on_gpu=False,
    )


def _stub_placement(monkeypatch, gpus: list, llm_idx: int, aux_idx: int):
    import utils.gpu_devices as gd

    monkeypatch.setattr(gd, "detect_gpus", lambda: gpus, raising=False)
    monkeypatch.setattr(gd, "get_placement", lambda: _placement(gpus, llm_idx, aux_idx), raising=False)


def _raw_nvml(nvml_index: int, name: str, total_gb: float = 24.0,
              used_gb: float = 10.0) -> dict:
    """Raw-Format von utils.vram_monitor.query_nvidia_smi() (nvidia-smi-CLI)."""
    total_b = int(total_gb * 1024**3)
    used_b = int(used_gb * 1024**3)
    return {"nvml_index": nvml_index, "name": name, "total_bytes": total_b,
            "used_bytes": used_b, "free_bytes": total_b - used_b, "temp_c": 40}


def test_single_gpu_snapshot_has_composite_role(monkeypatch):
    import utils.gpu_devices as gd

    gpu = _gpu(0, 0)
    _stub_placement(monkeypatch, [gpu], 0, 0)

    import utils.vram_monitor as vm
    # Datenquelle von get_all_gpu_snapshots: nvidia-smi-CLI (raw-Format faken)
    monkeypatch.setattr(
        vm, "query_nvidia_smi",
        lambda: [_raw_nvml(0, "NVIDIA GeForce RTX 4090")],
        raising=False,
    )

    snaps = vm.get_all_gpu_snapshots()
    assert len(snaps) == 1
    assert snaps[0]["role"] == "LLM+AUX"
    assert snaps[0]["cuda_index"] == 0
    assert snaps[0]["nvml_index"] == 0


def test_single_gpu_role_is_recognised_as_llm_role(monkeypatch):
    """Der Pre-Check-Helfer in scripts/model_loader.py akzeptiert "LLM+AUX"."""
    import scripts.model_loader as ml

    assert ml._is_llm_role("LLM") is True
    assert ml._is_llm_role("LLM+AUX") is True
    assert ml._is_llm_role("AUX") is False
    assert ml._is_llm_role(None) is False


def test_dual_gpu_snapshots_keep_separate_roles(monkeypatch):
    import utils.gpu_devices as gd

    llm = _gpu(0, 1, "NVIDIA GeForce RTX 4090", 24.0)
    aux = _gpu(1, 0, "NVIDIA GeForce RTX 3060 Ti", 8.0)
    _stub_placement(monkeypatch, [llm, aux], 0, 1)

    import utils.vram_monitor as vm
    monkeypatch.setattr(
        vm, "query_nvidia_smi",
        lambda: [_raw_nvml(1, "NVIDIA GeForce RTX 4090", 24.0, 10.0),
                 _raw_nvml(0, "NVIDIA GeForce RTX 3060 Ti", 8.0, 1.5)],
        raising=False,
    )

    snaps = vm.get_all_gpu_snapshots()
    assert {s["role"] for s in snaps} == {"LLM", "AUX"}