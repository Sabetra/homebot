from __future__ import annotations

from typing import Any

import pytest

from scripts.model_loader import ModelLoader


class _ClosableStream:
    def __init__(self, chunks: list[dict[str, Any]]) -> None:
        self._chunks = iter(chunks)
        self.closed = False

    def __iter__(self):
        return self

    def __next__(self):
        return next(self._chunks)

    def close(self) -> None:
        self.closed = True


class _FakeLlama:
    def __init__(self, stream: _ClosableStream) -> None:
        self.stream = stream
        self.kwargs: dict[str, Any] = {}

    def tokenize(self, *_args: Any, **_kwargs: Any) -> list[int]:
        return [1, 2, 3]

    def create_completion(self, **kwargs: Any) -> _ClosableStream:
        self.kwargs = kwargs
        return self.stream


def _loader(fake_llm: _FakeLlama | None) -> ModelLoader:
    loader = ModelLoader.__new__(ModelLoader)
    loader.llm = fake_llm
    loader._llm_call_count = 0
    loader._cached_n_ctx = 2048
    loader._stop_sequences = ["<end>"]
    loader._render_chat_template = lambda _messages, tools=None: "prompt"
    return loader


def test_stream_forwards_sampling_and_closes_iterator() -> None:
    stream = _ClosableStream([
        {"choices": [{"text": "Hal"}]},
        {"choices": [{"text": "lo"}]},
    ])
    fake_llm = _FakeLlama(stream)
    loader = _loader(fake_llm)

    result = list(loader.generate_response_stream(
        [{"role": "user", "content": "Hallo"}],
        min_p=0.1,
        stop=["CUSTOM"],
    ))

    assert result == ["Hal", "lo"]
    assert fake_llm.kwargs["stream"] is True
    assert fake_llm.kwargs["min_p"] == 0.1
    assert fake_llm.kwargs["stop"] == ["<end>", "CUSTOM"]
    assert stream.closed


def test_stream_cancellation_stops_and_closes_iterator() -> None:
    stream = _ClosableStream([
        {"choices": [{"text": "first"}]},
        {"choices": [{"text": "second"}]},
    ])
    fake_llm = _FakeLlama(stream)
    loader = _loader(fake_llm)
    cancelled = False

    def is_cancelled() -> bool:
        return cancelled

    iterator = loader.generate_response_stream(
        [{"role": "user", "content": "Hallo"}],
        is_cancelled=is_cancelled,
    )
    assert next(iterator) == "first"
    cancelled = True
    assert list(iterator) == []
    assert stream.closed
    assert fake_llm.kwargs["stopping_criteria"] is not None


def test_stream_raises_instead_of_emitting_error_text() -> None:
    loader = _loader(None)

    with pytest.raises(RuntimeError, match="nicht geladen"):
        list(loader.generate_response_stream([]))