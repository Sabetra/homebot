import sys
import types
from pathlib import Path

import pytest


class FakeSentenceTransformer:
    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs

    def get_embedding_dimension(self):
        return 1024

    def half(self):
        return None


@pytest.fixture(autouse=True)
def _reset_embedding_singleton():
    from utils import embedding_singleton

    embedding_singleton.EmbeddingSingleton._instance = None
    embedding_singleton.EmbeddingSingleton._initialized = False
    embedding_singleton._embedding_model_instance = None
    yield
    embedding_singleton.EmbeddingSingleton._instance = None
    embedding_singleton.EmbeddingSingleton._initialized = False
    embedding_singleton._embedding_model_instance = None


def test_load_model_uses_local_cache_when_available(monkeypatch, tmp_path):
    fake_sentence_transformers = types.ModuleType("sentence_transformers")
    captured = {}

    def fake_sentence_transformer(model_name, *args, **kwargs):
        captured["model_name"] = model_name
        captured["args"] = args
        captured["kwargs"] = kwargs
        return FakeSentenceTransformer(model_name, *args, **kwargs)

    fake_sentence_transformers.SentenceTransformer = fake_sentence_transformer
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake_sentence_transformers)

    fake_torch = types.ModuleType("torch")
    fake_torch.cuda = types.SimpleNamespace(is_available=lambda: False)
    fake_torch.float16 = "float16"
    monkeypatch.setitem(sys.modules, "torch", fake_torch)

    cache_dir = tmp_path / "sentence_transformers"
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / "intfloat_multilingual-e5-large").mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("SENTENCE_TRANSFORMERS_HOME", str(cache_dir))
    monkeypatch.delenv("EMBEDDING_MODEL_NAME", raising=False)

    from utils.embedding_singleton import EmbeddingSingleton

    singleton = EmbeddingSingleton()
    result = singleton.load_model("intfloat/multilingual-e5-large", force_reload=True)

    assert result is True
    assert captured["kwargs"]["cache_folder"] == str(cache_dir)
    assert captured["kwargs"]["local_files_only"] is True
