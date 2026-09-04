"""
Tests für den bounded LRU-Cache von ModelLoader.count_tokens (2026-08-28, P1).

Verwendet ein Fake-`llm`-Objekt (deterministischer Tokenizer, 1 Token pro
2 Bytes) — keine GPU, kein echtes Modell. ModelLoader ist ein Singleton;
die `llm`-Referenz wird pro Test getauscht und danach wiederhergestellt
(Pattern aus tests/test_model_loader_vram_precheck.py).
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from scripts.model_loader import ModelLoader


class _FakeLLM:
    """Deterministischer Fake-Tokenizer: 1 Token pro 2 Bytes."""

    def __init__(self) -> None:
        self.tokenize_calls = 0

    def tokenize(self, text_bytes: bytes):
        self.tokenize_calls += 1
        return list(range(len(text_bytes) // 2))


class _FakeLLMThatFails:
    def tokenize(self, text_bytes: bytes):
        raise RuntimeError("tokenizer kaputt")


def _fresh_loader() -> ModelLoader:
    """Singleton-Instanz (ModelLoader.__new__ liefert immer dasselbe Objekt)."""
    loader = ModelLoader()
    assert loader is ModelLoader()
    return loader


def _with_fake_llm(loader: ModelLoader, fake):
    """Kontextmanager: tauscht loader.llm aus und stellt ihn wieder her."""
    import contextlib

    @contextlib.contextmanager
    def _swap():
        original = loader.llm
        loader._invalidate_token_cache()  # Einträge aus fremden Tests weg
        loader.llm = fake
        try:
            yield
        finally:
            loader.llm = original
            loader._invalidate_token_cache()
    return _swap()


def test_count_tokens_cache_hit_avoids_retokenization():
    loader = _fresh_loader()
    fake = _FakeLLM()
    with _with_fake_llm(loader, fake):
        first = loader.count_tokens("hallo welt")
        second = loader.count_tokens("hallo welt")
        assert first == second == 5  # 10 Bytes // 2
        assert fake.tokenize_calls == 1, "zweiter Call muss ein Cache-Hit sein"


def test_count_tokens_cache_hit_is_stable_across_budget_checks():
    """Typischer Hot-Spot: dieselben Nachrichten werden in Trim-Loops
    immer wieder gezählt — nur der erste Call darf tokenisieren."""
    loader = _fresh_loader()
    fake = _FakeLLM()
    with _with_fake_llm(loader, fake):
        messages = [
            {"role": "user", "content": "frage " * 50},
            {"role": "assistant", "content": "antwort " * 80},
        ]
        totals = [loader.count_messages_tokens(messages) for _ in range(10)]
        assert len(set(totals)) == 1
        assert fake.tokenize_calls == 2  # genau eine Textzählung pro Message


def test_count_tokens_cache_eviction_respects_max():
    loader = _fresh_loader()
    fake = _FakeLLM()
    with _with_fake_llm(loader, fake):
        loader._token_cache_max = 3
        try:
            for i in range(5):
                loader.count_tokens(f"text number {i} padding")
        finally:
            loader._token_cache_max = 1024
        assert len(loader._token_cache) <= 3
        assert "text number 4 padding" in loader._token_cache
        assert "text number 0 padding" not in loader._token_cache


def test_count_tokens_long_text_not_cached():
    loader = _fresh_loader()
    fake = _FakeLLM()
    with _with_fake_llm(loader, fake):
        long_text = "x" * (loader._token_cache_max_len + 1)
        first = loader.count_tokens(long_text)
        second = loader.count_tokens(long_text)
        assert first == second
        assert fake.tokenize_calls == 2, "lange Texte dürfen nicht gecacht werden"
        assert long_text not in loader._token_cache


def test_invalidate_token_cache_forces_retokenization():
    """Derselbe Hook, den load_model/unload_model aufrufen."""
    loader = _fresh_loader()
    fake = _FakeLLM()
    with _with_fake_llm(loader, fake):
        loader.count_tokens("cache me")
        assert fake.tokenize_calls == 1
        loader._invalidate_token_cache()
        loader.count_tokens("cache me")
        assert fake.tokenize_calls == 2
        assert loader._token_cache["cache me"] == 4


def test_count_tokens_fallback_without_model():
    loader = _fresh_loader()
    original = loader.llm
    loader.llm = None
    try:
        assert loader.count_tokens("abcd1234") == 2  # max(1, 8 // 4)
        assert loader.count_tokens("abc") == 1  # max(1, 0) = 1
    finally:
        loader.llm = original


def test_count_tokens_fallback_when_tokenizer_raises():
    loader = _fresh_loader()
    with _with_fake_llm(loader, _FakeLLMThatFails()):
        assert loader.count_tokens("abcd1234") == 2  # Heuristik-Fallback