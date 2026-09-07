"""VRAM-Pre-Check für den LLM-Load (scripts/model_loader.py).

Der Check liefert Telemetrie und warnt bei knappem VRAM. Er blockiert den
Ladeversuch nicht anhand einer Schätzung, weil llama.cpp die tatsächliche
Speicherverteilung und mögliche Offloads erst beim Kontext-Init bestimmt.

Die Tests sind deterministisch: `get_all_gpu_snapshots` und `Llama` werden
monkeypatched, und die Modelldatei meldet die reale Modellgröße (6.5 GB)
per gezielter `os.path.getsize`-Patch (NTFS meldet hier Sparse-Files via
seek+leerer Write als 0 Byte — verifiziert 2026-08-26).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

import scripts.model_loader as model_loader_module
import utils.vram_monitor as vram_monitor_module
from scripts.model_loader import ModelLoader, _classify_load_failure

_MODEL_SIZE_BYTES = int(6.5 * 1024**3)  # reale Größe gemma-4-12B-it-QAT-Q4_0.gguf


def _make_loader() -> tuple[ModelLoader, list[str]]:
    """ModelLoader-Instanz mit capturendem _log_progress (Pattern aus
    tests/test_model_loader_streaming.py: __new__-Bypass, Attribut-Setup)."""
    loader = ModelLoader.__new__(ModelLoader)
    loader.llm = None
    loader.chat_handler = None
    loader.model_path = None
    loader.mmproj_path = None
    loader.is_multimodal = False
    loader.progress_callback = None
    loader.is_loading = False
    loader.current_model_id = None
    captured: list[str] = []
    loader._log_progress = captured.append  # type: ignore[method-assign]
    return loader, captured


def _fake_model_file(tmp_path: Path) -> Path:
    """Kleine echte Modelldatei; die realistische Größe (6.5 GB) wird im
    Test per gezielter os.path.getsize-Patch gemeldet (NTFS meldet hier
    sparse Files via seek+leerer Write als 0 Byte — verifiziert 2026-08-26)."""
    model_file = tmp_path / "gemma-fake-12B.gguf"
    model_file.write_bytes(b"\x00" * 1024)
    return model_file


def _patch_model_size(monkeypatch: pytest.MonkeyPatch) -> None:
    """Nur die Fake-Modelldatei meldet die reale 12B-Modellgröße."""
    import os
    real_getsize = os.path.getsize

    def fake_getsize(path: Any, *args: Any, **kwargs: Any) -> int:
        if Path(str(path)).name == "gemma-fake-12B.gguf":
            return _MODEL_SIZE_BYTES
        return real_getsize(path, *args, **kwargs)

    monkeypatch.setattr(os.path, "getsize", fake_getsize)


def _patch_snapshots(monkeypatch: pytest.MonkeyPatch, free_gb: float) -> None:
    snapshots = [
        {"nvml_index": 0, "cuda_index": 1, "role": "AUX",
         "name": "NVIDIA GeForce RTX 3060 Ti", "free_gb": 7.5},
        {"nvml_index": 1, "cuda_index": 0, "role": "LLM",
         "name": "NVIDIA GeForce RTX 4090", "free_gb": free_gb},
    ]
    monkeypatch.setattr(vram_monitor_module, "get_all_gpu_snapshots",
                        lambda: snapshots)


class _LlamaSentinel:
    """Wirft bei Konstruktion — so endet der Load-Fluss kontrolliert direkt
    nach `Llama(...)` und es wird protokolliert, ob er aufgerufen wurde."""

    constructed = 0
    last_kwargs: dict[str, Any] = {}

    def __init__(self, **kwargs: Any) -> None:
        type(self).constructed += 1
        type(self).last_kwargs = kwargs
        raise RuntimeError("SENTINEL: Llama-Konstruktion abgefangen")


@pytest.fixture()
def _gpu_required() -> None:
    import torch
    if not (hasattr(torch, "cuda") and torch.cuda.is_available()):
        pytest.skip("CUDA nicht verfügbar — GPU-Zweig des Pre-Checks nicht erreichbar")


@pytest.fixture(autouse=True)
def _reset_sentinel() -> None:
    _LlamaSentinel.constructed = 0
    _LlamaSentinel.last_kwargs = {}


def test_precheck_warns_but_attempts_load_when_estimate_exceeds_free_vram(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _gpu_required: None
) -> None:
    """Eine konservative Schätzung darf den nativen Ladeversuch nicht sperren."""
    model_file = _fake_model_file(tmp_path)
    _patch_model_size(monkeypatch)
    _patch_snapshots(monkeypatch, free_gb=5.2)
    monkeypatch.setattr(model_loader_module, "Llama", _LlamaSentinel)

    loader, captured = _make_loader()
    result = loader.load_model(str(model_file))

    assert result is False
    assert _LlamaSentinel.constructed == 1, "Die Schätzung darf Llama() nicht blockieren"
    assert loader.is_loading is False, "is_loading muss im finally-Block zurückgesetzt sein"

    joined = "\n".join(captured)
    assert "[INFO] VRAM-Check LLM-GPU (NVIDIA GeForce RTX 4090)" in joined
    assert "[WARNING] Geschätzter VRAM-Bedarf übersteigt den freien VRAM" in joined
    assert "Ladeversuch wird fortgesetzt" in joined


def test_precheck_allows_load_when_vram_sufficient(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _gpu_required: None
) -> None:
    """23.9 GB frei > 6.5 GB Modell + 2 GB Puffer → Pre-Check lässt durch,
    der Load-Fluss erreicht `Llama(...)` (Sentinel fängt kontrolliert ab)."""
    model_file = _fake_model_file(tmp_path)
    _patch_model_size(monkeypatch)
    _patch_snapshots(monkeypatch, free_gb=23.9)
    monkeypatch.setattr(model_loader_module, "Llama", _LlamaSentinel)

    loader, captured = _make_loader()
    result = loader.load_model(str(model_file))

    assert _LlamaSentinel.constructed == 1, "Bei ausreichendem VRAM muss Llama() erreicht werden"
    assert (
        _LlamaSentinel.last_kwargs["split_mode"]
        == model_loader_module.llama_cpp.LLAMA_SPLIT_MODE_NONE
    )
    assert result is False  # Sentinel-Ausnahme → erwarteter kontrollierter Abbruch
    assert "Geschätzter VRAM-Bedarf übersteigt" not in "\n".join(captured)


def test_illegal_instruction_is_classified_as_cpu_isa() -> None:
    error = OSError(-1073741795, "Windows Error 0xc000001d")

    assert _classify_load_failure(error) == "cpu_isa"


# ── KV-Quantisierungs-Retry (Sicherheitsnetz, scripts/model_loader.py) ─────
# llama.cpp-Builds können den KV-Cache-Typ (type_k/type_v) ablehnen. Der
# Loader retryt genau EINMAL ohne type_k/type_v (f16-Default). Regression:
# (1) Ein breit gefasster except hatte früher JEDEN Load-Fehler (z. B.
#     Architektur) als KV-Ablehnung fehlinterpretiert und die echte Root
#     Cause verschluckt.
# (2) notes.append() auf dem frozen TokenScalingProposal (notes=Tuple) warf
#     einen AttributeError, der den erfolgreichen f16-Retry nachträglich in
#     einen Load-Fehler verwandelte.


class _LlamaKvRejecting:
    """Simuliert einen llama.cpp-Build, der die KV-Quantisierung
    (type_k/type_v) ablehnt und ohne diese (f16-Default) erfolgreich lädt."""

    constructed = 0
    calls: list[dict[str, Any]] = []

    def __init__(self, **kwargs: Any) -> None:
        type(self).constructed += 1
        type(self).calls.append(kwargs)
        if "type_k" in kwargs or "type_v" in kwargs:
            raise RuntimeError("unknown kv cache type: 8 (GGML_TYPE_Q8_0)")
        # Minimal-Realität für den Post-Load-Pfad (Special-Token-Resolution,
        # Test-Inferenz): wie ein erfolgreich geladener llama-cpp-LLM.
        self.metadata: dict[str, Any] = {}

    def n_ctx(self) -> int:
        return 8192

    def create_chat_completion(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return {"choices": [{"message": {"role": "assistant", "content": "Hallo!"}}]}


class _LlamaArchitectureFail:
    """Simuliert einen Architektur-Fehler (unabhängig von der KV-Quantisierung,
    z. B. zu alter llama.cpp-Build). Darf KEIN KV-Retry auslösen."""

    constructed = 0

    def __init__(self, **kwargs: Any) -> None:
        type(self).constructed += 1
        raise RuntimeError("unknown model architecture: qwen35")


@pytest.fixture(autouse=True)
def _reset_kv_fakes() -> None:
    _LlamaKvRejecting.constructed = 0
    _LlamaKvRejecting.calls = []
    _LlamaArchitectureFail.constructed = 0


def _patch_propose_q8_0(monkeypatch: pytest.MonkeyPatch, n_ctx: int = 8192) -> None:
    """Zwingt den Token-Skalierungs-Vorschlag auf kv_quant='q8_0', damit der
    Loader type_k/type_v an Llama() übergibt (Voraussetzung für den Retry-Zweig)."""
    from utils import token_scaling as ts_module

    def fake_propose(*args: Any, **kwargs: Any) -> Any:
        return ts_module.TokenScalingProposal(
            n_ctx=n_ctx,
            kv_quant="q8_0",
            output_budget=2048,
            thinking_budget=0,
            reasoning_effort="medium",
        )

    monkeypatch.setattr(ts_module, "propose", fake_propose)


def test_kv_type_rejection_triggers_f16_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _gpu_required: None
) -> None:
    """Lehnt llama.cpp den KV-Quantisierungs-Typ ab, retryt der Loader genau
    EINMAL ohne type_k/type_v (f16-Default) — der Load gelingt und kein
    app-crash (Sicherheitsnetz)."""
    model_file = _fake_model_file(tmp_path)
    _patch_model_size(monkeypatch)
    _patch_snapshots(monkeypatch, free_gb=23.9)
    _patch_propose_q8_0(monkeypatch)
    monkeypatch.delenv("LLM_CACHE_BYTES", raising=False)
    monkeypatch.setattr(model_loader_module, "Llama", _LlamaKvRejecting)

    loader, captured = _make_loader()
    result = loader.load_model(str(model_file))

    assert result is True, "Der f16-Retry muss den Load abschließen"
    assert _LlamaKvRejecting.constructed == 2, (
        "Erster Versuch (mit KV-Quant) + genau EIN Retry (ohne type_k/type_v)"
    )
    first, second = _LlamaKvRejecting.calls
    assert first.get("type_k") is not None and first.get("type_v") is not None
    assert "type_k" not in second and "type_v" not in second, (
        "Der Retry muss ohne type_k/type_v laufen (llama.cpp-Default = f16)"
    )
    assert loader.llm is not None
    # Der erfolgreiche Retry darf keine Fehler-Protokolleingabe auslösen.
    joined = "\n".join(captured)
    assert "[SUCCESS] Modell geladen" in joined
    assert "[SUCCESS] Modell-Test erfolgreich!" in joined


def test_non_kv_error_is_not_silently_retried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _gpu_required: None
) -> None:
    """Regression (2026-09-04): Ein Fehler, der NICHT die KV-Quantisierung
    betrifft (hier: Architektur), darf KEINEN Retry auslösen — sonst verschluckt
    der Loader die echte Root Cause (Silent-Fallback, verboten laut AGENTS.md).
    Llama() wird genau EINMAL aufgerufen und load_model() gibt False zurück."""
    model_file = _fake_model_file(tmp_path)
    _patch_model_size(monkeypatch)
    _patch_snapshots(monkeypatch, free_gb=23.9)
    _patch_propose_q8_0(monkeypatch)
    monkeypatch.delenv("LLM_CACHE_BYTES", raising=False)
    monkeypatch.setattr(model_loader_module, "Llama", _LlamaArchitectureFail)

    loader, captured = _make_loader()
    result = loader.load_model(str(model_file))

    assert result is False, "Architektur-Fehler muss nicht verschluckt werden"
    assert _LlamaArchitectureFail.constructed == 1, (
        "Genau EINE Llama()-Aufruf — kein Retry bei Nicht-KV-Fehlern"
    )
    # Die echte Root Cause bleibt im Protokoll (Klassifizierung sichtbar).
    joined = "\n".join(captured).lower()
    assert "architecture" in joined or "architektur" in joined