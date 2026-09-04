# -*- coding: utf-8 -*-
"""Tests: Single-GPU-Policy der GPU-Validierung (2026-09-04).

Policy (scripts/validate_gpu_placement.py):
  - genau eine GPU (LLM + AUX geteilt)        -> PASS (Info-Warnung)
  - zwei GPUs, LLM + AUX getrennt             -> PASS
  - zwei GPUs, explizit beide auf derselben   -> PASS (Warnung, User-Intention)
  - keine nutzbare GPU (VRAM=0)               -> FAIL
  - env-Override auf unsichtbares Device      -> FAIL
  - env-Override nicht-numerisch              -> FAIL
  - env-Override auf sichtbares Device        -> PASS
  - Single-GPU: VRAM-Snapshot Rolle "LLM+AUX" -> PASS
  - Single-GPU: VRAM-Snapshot ohne Rolle      -> FAIL
  - Dual-GPU: beide Rollen LLM/AUX            -> PASS

Die Checks werden isoliert per monkeypatch ausgeführt — kein echter
NVIDIA-Stack nötig, kein LM Studio.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import scripts.validate_gpu_placement as vgp  # noqa: E402


# --------------------------------------------------------------------------- #
# Helfer
# --------------------------------------------------------------------------- #

def _gpu(cuda_index: int, nvml_index: int, name: str = "NVIDIA GeForce RTX 4090",
         vram_gb: float = 24.0, mem_used: float = 2.0) -> SimpleNamespace:
    return SimpleNamespace(
        name=name,
        uuid=f"GPU-UUID-{cuda_index}",
        uuid_field=lambda: f"GPU-UUID-{cuda_index}",
        vram_gb=vram_gb,
        mem_used_mb=int(mem_used * 1024),
        cuda_index=cuda_index,
        nvml_index=nvml_index,
        driver="570.00",
        source="mock",
    )


def _placement(gpus: list, llm_idx: int, aux_idx: int,
               single_gpu: "bool | None" = None) -> SimpleNamespace:
    return SimpleNamespace(
        llm=gpus[llm_idx],
        aux=gpus[aux_idx],
        llm_cuda=llm_idx,
        aux_cuda=aux_idx,
        llm_nvml=gpus[llm_idx].nvml_index,
        aux_nvml=gpus[aux_idx].nvml_index,
        single_gpu=(llm_idx == aux_idx) if single_gpu is None else single_gpu,
        llm_device_string=f"cuda:{llm_idx}",
        aux_device_string=f"cuda:{aux_idx}",
        llm_provider="torch",
        aux_provider="onnx",
        llm_fallback="CPU",
        aux_fallback="CPU",
        aux_on_gpu=False,
    )


def _stub_gpu_stack(monkeypatch, gpus: list, llm_idx: int, aux_idx: int,
                    single_gpu: "bool | None" = None):
    """utils.gpu_devices auf isolierte Fakes umleiten (kein echter NVML-Stack)."""
    import utils.gpu_devices as gd

    monkeypatch.setattr(gd, "detect_gpus", lambda: gpus, raising=False)
    monkeypatch.setattr(
        gd, "get_placement",
        lambda: _placement(gpus, llm_idx, aux_idx, single_gpu),
        raising=False,
    )


# --------------------------------------------------------------------------- #
# check_placement
# --------------------------------------------------------------------------- #

def test_single_gpu_passes_with_info_warning(monkeypatch, capsys):
    gpu = _gpu(0, 0, "NVIDIA GeForce RTX 4090", 24.0)
    _stub_gpu_stack(monkeypatch, [gpu], 0, 0)

    assert vgp.check_placement() is True

    out = capsys.readouterr().out
    assert "!!" not in out
    assert "[LLM+AUX]" in out


def test_dual_gpu_passes(monkeypatch, capsys):
    llm = _gpu(0, 1, "NVIDIA GeForce RTX 4090", 24.0)
    aux = _gpu(1, 0, "NVIDIA GeForce RTX 3060 Ti", 8.0)
    _stub_gpu_stack(monkeypatch, [llm, aux], 0, 1)

    assert vgp.check_placement() is True

    out = capsys.readouterr().out
    assert "[LLM]" in out
    assert "[AUX]" in out


def test_dual_gpus_but_same_assignment_warns_but_passes(monkeypatch, capsys):
    """Explizites Zwingen von AUX auf die LLM-GPU (z.B. um die 8-GB-AUX-Limite
    zu umgehen) ist erlaubt: Warnung statt Fehler (2026-09-04)."""
    llm = _gpu(0, 1, "NVIDIA GeForce RTX 4090", 24.0)
    aux = _gpu(1, 0, "NVIDIA GeForce RTX 3060 Ti", 8.0)
    _stub_gpu_stack(monkeypatch, [llm, aux], 0, 0, single_gpu=False)  # AUX-GPU ungenutzt

    assert vgp.check_placement() is True

    out = capsys.readouterr().out
    assert "!!" in out


def test_no_usable_gpu_fails(monkeypatch, capsys):
    gpus = [_gpu(0, 0, vram_gb=0.0), _gpu(1, 1, vram_gb=0.0)]
    _stub_gpu_stack(monkeypatch, gpus, 0, 1)

    assert vgp.check_placement() is False

    out = capsys.readouterr().out
    assert "Keine nutzbare GPU" in out


@pytest.mark.parametrize("env", ["BOT_LLM_CUDA_DEVICE", "BOT_AUX_CUDA_DEVICE"])
def test_env_override_invisible_device_fails(monkeypatch, capsys, env):
    gpu = _gpu(0, 0)
    _stub_gpu_stack(monkeypatch, [gpu], 0, 0)
    monkeypatch.setenv(env, "5")

    assert vgp.check_placement() is False

    out = capsys.readouterr().out
    assert env in out
    assert "nicht sichtbar" in out


@pytest.mark.parametrize("env", ["BOT_LLM_CUDA_DEVICE", "BOT_AUX_CUDA_DEVICE"])
def test_env_override_non_numeric_fails(monkeypatch, capsys, env):
    gpu = _gpu(0, 0)
    _stub_gpu_stack(monkeypatch, [gpu], 0, 0)
    monkeypatch.setenv(env, "cuda:0")  # nicht-numerisch

    assert vgp.check_placement() is False

    out = capsys.readouterr().out
    assert "kein Integer" in out


@pytest.mark.parametrize("env,value", [
    ("BOT_LLM_CUDA_DEVICE", "0"),
    ("BOT_AUX_CUDA_DEVICE", " 1 "),  # whitespace wird toleriert
])
def test_env_override_visible_device_passes(monkeypatch, capsys, env, value):
    llm = _gpu(0, 1, "NVIDIA GeForce RTX 4090", 24.0)
    aux = _gpu(1, 0, "NVIDIA GeForce RTX 3060 Ti", 8.0)
    _stub_gpu_stack(monkeypatch, [llm, aux], 0, 1)
    monkeypatch.setenv(env, value)

    assert vgp.check_placement() is True

    out = capsys.readouterr().out
    assert "!!" not in out


def test_env_override_whitespace_only_is_ignored(monkeypatch, capsys):
    gpu = _gpu(0, 0)
    _stub_gpu_stack(monkeypatch, [gpu], 0, 0)
    monkeypatch.setenv("BOT_LLM_CUDA_DEVICE", "   ")

    assert vgp.check_placement() is True


# --------------------------------------------------------------------------- #
# check_vram (Rollen-Mapping)
# --------------------------------------------------------------------------- #

def _snap(nvml: int, cuda: int, role: str, name: str = "RTX 4090") -> dict:
    return {"nvml_index": nvml, "cuda_index": cuda, "role": role, "name": name,
            "used_gb": 10.0, "total_gb": 24.0, "utilization_pct": 5.0, "source": "mock"}


def _stub_vram(monkeypatch, snaps: list):
    import utils.vram_monitor as vm

    monkeypatch.setattr(vm, "get_all_gpu_snapshots", lambda: snaps, raising=False)


def test_vram_single_gpu_composite_role_passes(monkeypatch, capsys):
    import utils.gpu_devices as gd

    gpu = _gpu(0, 0)
    _stub_gpu_stack(monkeypatch, [gpu], 0, 0)
    _stub_vram(monkeypatch, [_snap(0, 0, "LLM+AUX")])

    assert vgp.check_vram() is True
    assert "LLM+AUX" in capsys.readouterr().out


def test_vram_single_gpu_without_role_fails(monkeypatch, capsys):
    import utils.gpu_devices as gd

    gpu = _gpu(0, 0)
    _stub_gpu_stack(monkeypatch, [gpu], 0, 0)
    _stub_vram(monkeypatch, [_snap(0, 0, "NONE")])

    assert vgp.check_vram() is False
    assert "!!" in capsys.readouterr().out


def test_vram_dual_gpu_both_roles_pass(monkeypatch, capsys):
    import utils.gpu_devices as gd

    llm = _gpu(0, 1, "NVIDIA GeForce RTX 4090", 24.0)
    aux = _gpu(1, 0, "NVIDIA GeForce RTX 3060 Ti", 8.0)
    _stub_gpu_stack(monkeypatch, [llm, aux], 0, 1)
    _stub_vram(monkeypatch, [_snap(1, 0, "LLM", "RTX 4090"),
                             _snap(0, 1, "AUX", "RTX 3060 Ti")])

    assert vgp.check_vram() is True
    out = capsys.readouterr().out
    assert "[LLM]" in out
    assert "[AUX]" in out


def test_vram_dual_gpu_missing_role_fails(monkeypatch, capsys):
    import utils.gpu_devices as gd

    llm = _gpu(0, 1, "NVIDIA GeForce RTX 4090", 24.0)
    aux = _gpu(1, 0, "NVIDIA GeForce RTX 3060 Ti", 8.0)
    _stub_gpu_stack(monkeypatch, [llm, aux], 0, 1)
    _stub_vram(monkeypatch, [_snap(1, 0, "LLM", "RTX 4090")])

    assert vgp.check_vram() is False
    assert "!!" in capsys.readouterr().out