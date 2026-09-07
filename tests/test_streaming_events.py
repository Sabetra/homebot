from __future__ import annotations

import pytest
from pydantic import TypeAdapter, ValidationError

from agent.streaming_events import (
    ActiveRunRegistry,
    ChatEvent,
    ChatEventConsumer,
    ChatRunResult,
    RunCancelled,
    RunCompleted,
    RunStarted,
    StreamingContext,
    TextDelta,
)


def test_discriminated_event_roundtrip_and_ordering() -> None:
    captured: list[ChatEvent] = []
    context = StreamingContext(
        session_id="session-1",
        run_id="run-1",
        message_id="message-1",
        sink=captured.append,
    )

    started = context.emit(RunStarted, message_id=context.message_id)
    delta = context.emit(TextDelta, message_id=context.message_id, delta="Hallo")
    completed = context.emit(
        RunCompleted,
        result=ChatRunResult(text="Hallo"),
    )

    assert [event.sequence for event in captured] == [1, 2, 3]
    assert [event.type for event in captured] == [
        "run_started",
        "text_delta",
        "run_completed",
    ]
    assert started.run_id == delta.run_id == completed.run_id == "run-1"
    parsed = TypeAdapter(ChatEvent).validate_python(completed.model_dump())
    assert isinstance(parsed, RunCompleted)
    assert parsed.result.text == "Hallo"


def test_terminal_event_closes_context() -> None:
    context = StreamingContext(session_id="session-1")
    context.emit(RunCancelled, reason="user_cancelled", partial_text="Teil")

    assert context.is_terminal
    with pytest.raises(RuntimeError, match="after the run terminated"):
        context.emit(TextDelta, message_id=context.message_id, delta="zu spaet")


def test_event_models_are_strict_and_immutable() -> None:
    context = StreamingContext(session_id="session-1")
    event = context.emit(RunStarted, message_id=context.message_id)

    with pytest.raises(ValidationError):
        RunStarted.model_validate({**event.model_dump(), "unknown": True})
    with pytest.raises(ValidationError):
        event.sequence = 99  # type: ignore[misc]


def test_registry_cancels_previous_run_without_cross_session_bleed() -> None:
    registry = ActiveRunRegistry()
    first = StreamingContext(session_id="session-1")
    replacement = StreamingContext(session_id="session-1")
    other = StreamingContext(session_id="session-2")

    registry.register(first)
    registry.register(other)
    registry.register(replacement)

    assert first.is_cancelled
    assert not replacement.is_cancelled
    assert not other.is_cancelled
    assert registry.get("session-1") is replacement
    assert registry.cancel("session-1")
    assert replacement.is_cancelled
    assert not other.is_cancelled

    registry.finish(replacement)
    assert registry.get("session-1") is None


def test_consumer_accepts_terminal_event_from_reloaded_class_identity() -> None:
    class ReloadedRunCompleted:
        type = "run_completed"

        def __init__(self) -> None:
            self.result = ChatRunResult(text="reload-safe")

    consumer = ChatEventConsumer()

    consumer.observe(ReloadedRunCompleted())

    assert consumer.terminal_type == "run_completed"
    assert consumer.result == ChatRunResult(text="reload-safe")


def test_consumer_rejects_multiple_terminal_events() -> None:
    context = StreamingContext(session_id="session-1")
    event = context.emit(RunCancelled)
    consumer = ChatEventConsumer()
    consumer.observe(event)

    with pytest.raises(RuntimeError, match="Multiple terminal chat events"):
        consumer.observe(event)