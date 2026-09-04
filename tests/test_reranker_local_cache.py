from pathlib import Path

import agent.reranker as reranker_module


def test_resolve_local_model_uses_hf_home_hub(monkeypatch, tmp_path):
    hf_home = tmp_path / "huggingface"
    expected_snapshot = hf_home / "hub" / "models--BAAI--bge-reranker-v2-m3" / "snapshots" / "commit"
    calls = []

    def fake_snapshot_download(**kwargs):
        calls.append(kwargs)
        return str(expected_snapshot)

    monkeypatch.setenv("HF_HOME", str(hf_home))
    monkeypatch.setenv("SENTENCE_TRANSFORMERS_HOME", str(tmp_path / "sentence_transformers"))
    monkeypatch.setattr("huggingface_hub.snapshot_download", fake_snapshot_download)

    resolved = reranker_module._resolve_local_model_path("BAAI/bge-reranker-v2-m3")

    assert resolved == str(expected_snapshot)
    assert calls == [
        {
            "repo_id": "BAAI/bge-reranker-v2-m3",
            "cache_dir": str(hf_home / "hub"),
            "local_files_only": True,
        }
    ]


def test_resolve_local_model_preserves_explicit_directory(monkeypatch, tmp_path):
    model_dir = tmp_path / "model"
    model_dir.mkdir()

    def fail_if_called(**_kwargs):
        raise AssertionError("Explicit local model paths must not query the hub cache")

    monkeypatch.setattr("huggingface_hub.snapshot_download", fail_if_called)

    assert reranker_module._resolve_local_model_path(str(model_dir)) == str(model_dir)