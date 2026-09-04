from __future__ import annotations

from typing import Any

import pytest

from agent.streaming_events import StreamingCancelled, StreamingContext
from chatbot_logic import ChatbotLogic


class _ContextManager:
    def should_summarize(self, _messages: list[dict[str, Any]]) -> bool:
        return False


class _StreamingLoader:
    is_multimodal = False

    def __init__(self, chunks: list[str]) -> None:
        self.chunks = chunks

    def generate_response_stream(self, **kwargs: Any):
        for chunk in self.chunks:
            if kwargs["is_cancelled"] is not None and kwargs["is_cancelled"]():
                break
            yield chunk


def _logic(chunks: list[str]) -> ChatbotLogic:
    logic = ChatbotLogic.__new__(ChatbotLogic)
    logic.model_loader = _StreamingLoader(chunks)
    logic.settings = {"max_tokens": 128, "temperature": 0.1}
    logic.system_prompt = "system"
    logic.message_history = []
    logic.debug_mode = False
    logic.debug_to_console = False
    logic.context_manager = _ContextManager()
    return logic


def test_base_chat_commits_only_filtered_visible_text() -> None:
    logic = _logic([
        "[THINK]private[/THINK]A sufficiently long ans",
        "wer\nUSER: injected",
    ])
    visible: list[str] = []

    response = logic.chat("question", stream_callback=visible.append)

    assert response == "".join(visible) == "A sufficiently long answer\n"
    assert logic.message_history[-1] == {
        "role": "assistant",
        "content": response,
    }


def test_base_chat_cancellation_does_not_mutate_history() -> None:
    logic = _logic(["A sufficiently long first chunk", "second chunk"])
    context = StreamingContext(session_id="session-1")

    def cancel_after_first_chunk(_chunk: str) -> None:
        context.cancel()

    with pytest.raises(StreamingCancelled):
        logic.chat(
            "question",
            stream_callback=cancel_after_first_chunk,
            stream_context=context,
        )

    assert logic.message_history == []


def test_base_chat_separates_streamed_followups_from_canonical_text() -> None:
    logic = _logic([
        "Das Streamen funktioniert.\n\n[FOLLOW_",
        "UP]Wie funktioniert Token-Streaming?|Welche Grenzen hat es?[/FOLLOW_UP]",
    ])
    visible: list[str] = []

    response = logic.chat("Ich teste Streaming.", stream_callback=visible.append)

    assert response == "".join(visible) == "Das Streamen funktioniert.\n\n"
    assert logic.last_followup_questions == [
        "Wie funktioniert Token-Streaming?",
        "Welche Grenzen hat es?",
    ]
    assert logic.message_history[-1]["content"] == response