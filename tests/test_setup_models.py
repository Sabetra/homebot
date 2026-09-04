"""Unit tests for scripts/setup_models.py (model bootstrap / status / check CLI).

Hermetic by design: presence detection runs against temp-directory cache roots
(``CacheRoots``), so no network access and no real model files are required.
The ``--fetch`` path is exercised with an injected ``snapshot_downloader``.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from scripts.setup_models import (
    CacheRoots,
    FetchRefusedError,
    ManifestError,
    all_entries,
    check_entry,
    collect_status,
    fetchable_missing,
    is_offline,
    load_manifest,
    main,
    resolve_candidates,
    run_check,
    run_fetch,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
MANIFEST = REPO_ROOT / "models" / "manifest.json"


def _empty_roots(tmp_path: Path) -> CacheRoots:
    return CacheRoots(
        lm_studio_root=tmp_path / "lm",
        st_home=tmp_path / "st",
        hf_home=tmp_path / "hf",
        easyocr_dir=tmp_path / "easyocr",
        repo_root=tmp_path,
    )


def _hf_entry() -> dict:
    return {
        "id": "bge-reranker-v2-m3",
        "required": True,
        "cache_strategy": "hf_hub",
        "hf_repo": "BAAI/bge-reranker-v2-m3",
    }


# --- manifest loading -------------------------------------------------------

def test_load_manifest_real_manifest():
    m = load_manifest(str(MANIFEST))
    assert m["version"]
    assert m["llm"]["models"], "manifest must list at least one LLM"
    assert m["aux"]["models"], "manifest must list at least one AUX model"


def test_load_manifest_missing_raises(tmp_path):
    with pytest.raises(ManifestError):
        load_manifest(str(tmp_path / "nope.json"))


def test_load_manifest_invalid_json_raises(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{ not valid json", encoding="utf-8")
    with pytest.raises(ManifestError):
        load_manifest(str(bad))


def test_load_manifest_missing_section_raises(tmp_path):
    p = tmp_path / "partial.json"
    p.write_text('{"version": "1", "llm": {"models": [{"id": "x"}]}}', encoding="utf-8")
    with pytest.raises(ManifestError):
        load_manifest(str(p))


# --- offline policy ---------------------------------------------------------

def test_is_offline_detects_flags():
    assert is_offline({"APP_LOCAL_ONLY": "1"}) is True
    assert is_offline({"HF_HUB_OFFLINE": "true"}) is True
    assert is_offline({"TRANSFORMERS_OFFLINE": "yes"}) is True


def test_is_offline_false_when_unset_or_falsey():
    assert is_offline({}) is False
    assert is_offline({"APP_LOCAL_ONLY": "0"}) is False
    assert is_offline({"HF_HUB_OFFLINE": ""}) is False


# --- candidate resolution ---------------------------------------------------

def test_resolve_candidates_hf_hub(tmp_path):
    roots = _empty_roots(tmp_path)
    assert resolve_candidates(_hf_entry(), roots) == [
        roots.hf_home / "hub" / "models--BAAI--bge-reranker-v2-m3"
    ]


def test_resolve_candidates_llm_folder_file(tmp_path):
    roots = _empty_roots(tmp_path)
    entry = {"id": "gemma", "cache_strategy": "lm_studio", "folder": "Gemma4", "file": "gemma.gguf"}
    assert resolve_candidates(entry, roots) == [roots.lm_studio_root / "Gemma4" / "gemma.gguf"]



# --- check exit code --------------------------------------------------------

def test_run_check_returns_one_when_required_missing(tmp_path, capsys):
    m = load_manifest(str(MANIFEST))
    roots = _empty_roots(tmp_path)
    assert run_check(m, roots) == 1  # empty roots -> required models missing


# --- fetch ------------------------------------------------------------------

def test_run_fetch_refuses_when_offline(tmp_path):
    m = load_manifest(str(MANIFEST))
    roots = _empty_roots(tmp_path)
    with pytest.raises(FetchRefusedError):
        run_fetch(m, roots, offline=True)


def test_run_fetch_uses_injected_downloader(tmp_path):
    m = load_manifest(str(MANIFEST))
    roots = _empty_roots(tmp_path)
    calls = []

    def fake_downloader(repo_id, cache_dir, revision, local_files_only):
        calls.append(repo_id)
        return "ok"

    rc = run_fetch(m, roots, offline=False, only="bge-reranker-v2-m3", snapshot_downloader=fake_downloader)
    assert rc == 0
    assert calls == ["BAAI/bge-reranker-v2-m3"]


def test_run_fetch_nothing_to_fetch(tmp_path):
    m = load_manifest(str(MANIFEST))
    roots = _empty_roots(tmp_path)
    calls = []
    rc = run_fetch(
        m, roots, offline=False, only="does-not-exist",
        snapshot_downloader=lambda *a, **k: calls.append(1),
    )
    assert rc == 0
    assert calls == []


def test_fetchable_missing_filters_by_only(tmp_path):
    m = load_manifest(str(MANIFEST))
    roots = _empty_roots(tmp_path)
    only = fetchable_missing(m, roots, only="bge-reranker-v2-m3")
    assert [e["id"] for e in only] == ["bge-reranker-v2-m3"]


# --- main() CLI -------------------------------------------------------------

def test_main_status_returns_zero(capsys):
    assert main(["--status"]) == 0
    assert "MODEL" in capsys.readouterr().out


def test_main_check_returns_valid_code():
    assert main(["--check"]) in (0, 1)


def test_main_bad_manifest_returns_two(tmp_path):
    assert main(["--status", "--manifest", str(tmp_path / "missing.json")]) == 2

def test_resolve_candidates_unknown_strategy_empty(tmp_path):
    roots = _empty_roots(tmp_path)
    assert resolve_candidates({"id": "x"}, roots) == []


# --- presence detection -----------------------------------------------------

def test_check_entry_missing(tmp_path):
    roots = _empty_roots(tmp_path)
    row = check_entry(_hf_entry(), roots)
    assert row["present"] is False
    assert row["required"] is True
    assert row["id"] == "bge-reranker-v2-m3"


def test_check_entry_present_after_materialization(tmp_path):
    roots = _empty_roots(tmp_path)
    target = roots.hf_home / "hub" / "models--BAAI--bge-reranker-v2-m3"
    target.mkdir(parents=True, exist_ok=True)
    row = check_entry(_hf_entry(), roots)
    assert row["present"] is True
    assert row["path"] == str(target)


# --- status collection ------------------------------------------------------

def test_collect_status_covers_all_models():
    m = load_manifest(str(MANIFEST))
    roots = CacheRoots.from_env(REPO_ROOT, environ={})
    rows = collect_status(m, roots)
    expected = len(m["llm"]["models"]) + len(m["aux"]["models"])
    assert len(rows) == expected
    assert all({"id", "required", "present", "section", "path"} <= set(r) for r in rows)


def test_all_entries_pairs_section():
    m = load_manifest(str(MANIFEST))
    sections = {section for section, _ in all_entries(m)}
    assert sections == {"llm", "aux"}