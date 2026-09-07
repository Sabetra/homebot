from __future__ import annotations

import pytest

from langchain_core.messages import HumanMessage, SystemMessage

from wellbeing_session.workflow.langchain_adapter import LocalLlamaCppChat


class _FakeLoader:
    def __init__(self) -> None:
        self.last_generate_kwargs = None

    def generate_response(self, **kwargs):
        self.last_generate_kwargs = kwargs
        return "adapter-ok"

    def count_tokens(self, text: str) -> int:
        return len(text.split())


class _FailingLoader(_FakeLoader):
    def generate_response(self, **kwargs):
        raise RuntimeError("boom")


class _StreamingLoader(_FakeLoader):
    def generate_response_stream(self, **kwargs):
        yield "hello"
        yield " "
        yield "world"


def test_generate_maps_messages_and_params() -> None:
    loader = _FakeLoader()
    chat = LocalLlamaCppChat(model_loader=loader)

    result = chat._generate(
        messages=[
            SystemMessage(content="System prompt"),
            HumanMessage(content="How are you?"),
        ],
        stop=["STOP"],
        max_tokens=123,
        temperature=0.2,
        top_p=0.8,
        top_k=22,
        repeat_penalty=1.05,
    )

    assert result.generations[0].message.content == "adapter-ok"
    assert loader.last_generate_kwargs is not None
    assert loader.last_generate_kwargs["messages"] == [
        {"role": "system", "content": "System prompt"},
        {"role": "user", "content": "How are you?"},
    ]
    assert loader.last_generate_kwargs["stop"] == ["STOP"]
    assert loader.last_generate_kwargs["max_tokens"] == 123


def test_generate_wraps_loader_failure() -> None:
    chat = LocalLlamaCppChat(model_loader=_FailingLoader())

    with pytest.raises(RuntimeError, match="generation failed"):
        chat._generate(messages=[HumanMessage(content="hi")])


def test_stream_uses_streaming_loader() -> None:
    chat = LocalLlamaCppChat(model_loader=_StreamingLoader())
    chunks = list(chat._stream(messages=[HumanMessage(content="hi")]))
    merged = "".join(chunk.message.content for chunk in chunks)

    assert merged == "hello world"
