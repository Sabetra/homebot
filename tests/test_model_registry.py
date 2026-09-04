from __future__ import annotations

from pathlib import Path

import pytest

from utils.model_registry import (
    find_model_by_path,
    models_root,
    scan_models,
)

GB = 1024 ** 3


def _write(path: Path, size: int) -> Path:
    """Erzeugt eine SPARSE Datei mit der vorgegebenen logischen Größe.

    Die Registry liest ausschließlich stat().st_size (nie den Inhalt) —
    eine 1-Byte-Sparse-Datei genügt vollständig. Das vermeidet GB-große
    RAM-/Disk-Allokationen (b\"\\x00\" * size = bis zu 8 GB), die unter
    Disk-Druck transienten OSError-Fehlschlag verursachten.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as fh:
        if size > 0:
            fh.seek(size - 1)
            fh.write(b"\x00")
    return path


def test_missing_root_returns_empty(tmp_path: Path) -> None:
    assert scan_models(tmp_path / "does-not-exist") == []


def test_models_root_env_override(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("BOT_MODELS_DIR", str(tmp_path))
    assert models_root() == tmp_path
    monkeypatch.delenv("BOT_MODELS_DIR")
    assert str(models_root()).endswith("lmstudio-community")


def test_discovers_nested_model_with_vision(tmp_path: Path) -> None:
    _write(tmp_path / "gemma" / "gemma-4-12B" / "gemma-4-12B-it-Q4_K_M.gguf", 6 * GB)
    _write(tmp_path / "gemma" / "gemma-4-12B" / "mmproj-gemma-4-12B-F16.gguf", 1 * GB)

    models = scan_models(tmp_path)
    assert len(models) == 1
    m = models[0]
    assert m.is_vision is True
    assert m.mmproj_path.endswith("mmproj-gemma-4-12B-F16.gguf")
    assert m.model_id == "gemma-gemma-4-12b-gemma-4-12b-it-q4-k-m"
    assert m.folder_rel == "gemma/gemma-4-12B"
    assert m.size_gb == pytest.approx(6.0, abs=0.01)


def test_text_model_without_mmproj(tmp_path: Path) -> None:
    _write(tmp_path / "nemotron" / "Nemotron-3-Nano-4B-Q4.gguf", 2 * GB)
    models = scan_models(tmp_path)
    assert len(models) == 1
    assert models[0].is_vision is False
    assert models[0].mmproj_path is None


def test_multiple_quantizations_yield_multiple_entries(tmp_path: Path) -> None:
    folder = tmp_path / "model"
    _write(folder / "model-Q4_K_M.gguf", 4 * GB)
    _write(folder / "model-Q8_0.gguf", 8 * GB)
    _write(folder / "mmproj-F16.gguf", 1 * GB)

    models = scan_models(tmp_path)
    # Sortierung: Größe absteigend → Q8_0 vor Q4_K_M
    assert [m.display_name for m in models] == ["model-Q8_0", "model-Q4_K_M"]
    assert {m.model_id for m in models} == {"model-model-q4-k-m", "model-model-q8-0"}
    assert all(m.is_vision for m in models)
    assert all(Path(m.mmproj_path).name == "mmproj-F16.gguf" for m in models)


def test_shards_only_folder_is_ignored(tmp_path: Path) -> None:
    folder = tmp_path / "big"
    _write(folder / "model-00001-of-00002.gguf", 10 * GB)
    _write(folder / "model-00002-of-00002.gguf", 10 * GB)
    _write(folder / "mmproj.gguf", 1 * GB)
    assert scan_models(tmp_path) == []


def test_root_level_gguf_is_discovered(tmp_path: Path) -> None:
    _write(tmp_path / "loose-model.gguf", 1 * GB)
    models = scan_models(tmp_path)
    assert [m.model_id for m in models] == ["loose-model"]
    assert models[0].folder_rel == "."


def test_new_folder_appears_without_restart(tmp_path: Path) -> None:
    assert scan_models(tmp_path) == []
    _write(tmp_path / "new" / "new-model.gguf", 1 * GB)
    assert [m.display_name for m in scan_models(tmp_path)] == ["new-model"]


def test_sorted_by_folder_then_size_desc(tmp_path: Path) -> None:
    _write(tmp_path / "b" / "small.gguf", 1 * GB)
    _write(tmp_path / "b" / "big.gguf", 5 * GB)
    _write(tmp_path / "a" / "mid.gguf", 3 * GB)
    assert [m.display_name for m in scan_models(tmp_path)] == ["mid", "big", "small"]


def test_find_model_by_path(tmp_path: Path) -> None:
    target = _write(tmp_path / "fam" / "m.gguf", 1 * GB)
    info = find_model_by_path(target, root=tmp_path)
    assert info is not None and info.model_path == str(target)
    assert find_model_by_path(tmp_path / "nope.gguf", root=tmp_path) is None


def test_find_model_by_path_missing_root(tmp_path: Path) -> None:
    assert find_model_by_path(tmp_path / "ghost.gguf", root=tmp_path) is None
