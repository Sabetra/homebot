from __future__ import annotations

from pathlib import Path

import pytest

from scripts.model_loader import ModelLoader


@pytest.fixture()
def loader() -> ModelLoader:
    """Isolierte Instanz via __new__-Bypass (Pattern aus
    tests/test_model_loader_vram_precheck.py) — das Singleton bleibt unangetastet."""
    instance = ModelLoader.__new__(ModelLoader)
    instance.llm = None
    instance.chat_handler = None
    instance.model_path = None
    instance.mmproj_path = None
    instance.is_multimodal = False
    instance.progress_callback = None
    instance.is_loading = False
    instance._initialized = True
    instance.current_model_id = None
    instance._llm_call_count = 0
    instance._model_family = "gemma"
    instance._bos_token = "<bos>"
    instance._eos_token = "<eos>"
    instance._stop_sequences = []
    instance._supports_system_role = None
    instance._supports_cache_prompt_arg = None
    instance._cached_n_ctx = None
    return instance


def _make_gguf(tmp_path: Path, name: str, size: int = 4096) -> Path:
    p = tmp_path / name
    p.write_bytes(b"\x00" * size)
    return p


def _patch_load(monkeypatch, calls: list) -> None:
    def fake_load_model(self, model_path, mmproj_path=None, n_gpu_layers=-1, n_ctx=8192,
                        token_scaling_overrides=None):
        calls.append({"model_path": model_path, "mmproj_path": mmproj_path})
        self.llm = object()  # wie echtes load_model: Modell "geladen"
        return True

    monkeypatch.setattr(ModelLoader, "load_model", fake_load_model)


def test_load_model_by_path_missing_file_returns_false(loader: ModelLoader) -> None:
    assert loader.load_model_by_path(r"C:\definitely\missing\model.gguf") is False
    assert loader.is_model_loaded() is False


def test_load_model_by_path_success(
    loader: ModelLoader, monkeypatch, tmp_path: Path
) -> None:
    gguf = _make_gguf(tmp_path, "gemma-4-12B-it-Q4_K_M.gguf")
    mmproj = _make_gguf(tmp_path, "mmproj-F16.gguf")
    calls: list = []
    _patch_load(monkeypatch, calls)

    assert loader.load_model_by_path(str(gguf), mmproj_path=str(mmproj)) is True
    assert loader.current_model_id == "custom:gemma-4-12B-it-Q4_K_M.gguf"
    assert loader.is_model_loaded() is True
    assert calls[0]["model_path"] == str(gguf)
    assert calls[0]["mmproj_path"] == str(mmproj)
    assert loader.get_current_model_name() == "gemma-4-12B-it-Q4_K_M"


def test_switch_model_unloads_previous(
    loader: ModelLoader, monkeypatch, tmp_path: Path
) -> None:
    a = _make_gguf(tmp_path, "a.gguf")
    b = _make_gguf(tmp_path, "b.gguf")
    calls: list = []
    unloaded: list = []
    _patch_load(monkeypatch, calls)
    monkeypatch.setattr(
        ModelLoader, "unload_model", lambda self: unloaded.append(True)
    )

    loader.load_model_by_path(str(a))
    assert unloaded == []  # Erstladung: nichts zu entladen
    loader.load_model_by_path(str(b))
    assert len(unloaded) == 1  # Modellwechsel: vorheriges wird entladen
    assert loader.current_model_id == "custom:b.gguf"


def test_reload_same_model_does_not_unload(
    loader: ModelLoader, monkeypatch, tmp_path: Path
) -> None:
    a = _make_gguf(tmp_path, "a.gguf")
    calls: list = []
    unloaded: list = []
    _patch_load(monkeypatch, calls)
    monkeypatch.setattr(
        ModelLoader, "unload_model", lambda self: unloaded.append(True)
    )

    loader.load_model_by_path(str(a))
    loader.load_model_by_path(str(a))  # dasselbe Modell erneut wählen
    assert unloaded == []
    assert len(calls) == 2


def test_get_current_model_name_custom_id(loader: ModelLoader) -> None:
    loader.current_model_id = "custom:qwen3.8-27b-it-Q4_K_M.gguf"
    assert loader.get_current_model_name() == "qwen3.8-27b-it-Q4_K_M"
