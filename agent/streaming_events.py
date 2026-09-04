"""Typed, local-only event contract for chat and agent streaming."""

from __future__ import annotations

import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Annotated, Any, Callable, Iterator, Literal, TypeAlias, cast

from pydantic import BaseModel, ConfigDict, Field, model_validator


ChatRoute: TypeAlias = Literal["simple", "plan_execute", "react", "vision", "cache"]


class StreamEvent(BaseModel):
    """Common immutable envelope shared by every streamed event."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    type: str
    run_id: str
    session_id: str
    sequence: int = Field(ge=1)
    timestamp: datetime
    route: ChatRoute | None = None


class RunStarted(StreamEvent):
    type: Literal["run_started"] = "run_started"
    message_id: str


class RouteSelected(StreamEvent):
    type: Literal["route_selected"] = "route_selected"
    selected_route: ChatRoute


class StepStarted(StreamEvent):
    type: Literal["step_started"] = "step_started"
    step_id: str
    label: str


class StepFinished(StreamEvent):
    type: Literal["step_finished"] = "step_finished"
    step_id: str
    status: Literal["completed", "cancelled", "failed"] = "completed"
    duration_ms: int | None = Field(default=None, ge=0)


class ToolStarted(StreamEvent):
    type: Literal["tool_started"] = "tool_started"
    tool_call_id: str
    tool_name: str


class ToolFinished(StreamEvent):
    type: Literal["tool_finished"] = "tool_finished"
    tool_call_id: str
    tool_name: str
    success: bool
    summary: str | None = None


class TextStarted(StreamEvent):
    type: Literal["text_started"] = "text_started"
    message_id: str


class TextDelta(StreamEvent):
    type: Literal["text_delta"] = "text_delta"
    message_id: str
    delta: str = Field(min_length=1)


class TextFinished(StreamEvent):
    type: Literal["text_finished"] = "text_finished"
    message_id: str


class SourcesUpdated(StreamEvent):
    type: Literal["sources_updated"] = "sources_updated"
    sources: list[dict[str, Any]] = Field(default_factory=list)


class UsageUpdated(StreamEvent):
    type: Literal["usage_updated"] = "usage_updated"
    prompt_tokens: int | None = Field(default=None, ge=0)
    completion_tokens: int | None = Field(default=None, ge=0)
    tokens_per_second: float | None = Field(default=None, ge=0)
    ttft_ms: int | None = Field(default=None, ge=0)


class GraphicArtifact(BaseModel):
    """A locally generated image delivered by path or encoded bytes."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    type: Literal["image"] = "image"
    path: str | None = None
    data_base64: str | None = None
    media_type: Literal["image/png", "image/jpeg", "image/webp", "image/svg+xml"] = "image/png"
    caption: str = "Generiertes Diagramm"
    diagram_type: str | None = None
    backend: str | None = None

    @model_validator(mode="after")
    def validate_single_payload(self) -> "GraphicArtifact":
        if (self.path is None) == (self.data_base64 is None):
            raise ValueError("GraphicArtifact requires exactly one of path or data_base64")
        return self


class FileArtifact(BaseModel):
    """A generated file that the user can download from chat history."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str = Field(min_length=1)
    name: str = Field(min_length=1)
    size: int = Field(ge=0)
    media_type: str = "application/octet-stream"
    caption: str = "Erzeugte Datei"


class ChatRunResult(BaseModel):
    """Canonical result persisted after a successful run."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    text: str
    sources: list[dict[str, Any]] = Field(default_factory=list)
    followup_questions: list[str] = Field(default_factory=list)
    graphics: list[GraphicArtifact] = Field(default_factory=list)
    files: list[FileArtifact] = Field(default_factory=list)
    generated_image: str | None = None
    internet_image: str | None = None
    trace: dict[str, Any] | None = None
    finish_reason: str = "stop"
    metrics: dict[str, int | float | str | None] = Field(default_factory=dict)


class ChatEventConsumer:
    """Consume events by discriminator, independent of Python class identity."""

    def __init__(self) -> None:
        self.result: ChatRunResult | None = None
        self.failure_code: str | None = None
        self.failure_message: str | None = None
        self.was_cancelled = False
        self.observed_types: list[str] = []
        self._terminal_type: str | None = None

    @property
    def terminal_type(self) -> str | None:
        return self._terminal_type

    def observe(self, event: object) -> str | None:
        """Update terminal state and return a visible text delta when present."""
        event_type = getattr(event, "type", None)
        if not isinstance(event_type, str):
            raise TypeError("Chat event has no string discriminator")
        self.observed_types.append(event_type)

        if event_type == "text_delta":
            delta = getattr(event, "delta", None)
            if not isinstance(delta, str) or not delta:
                raise ValueError("text_delta event has no visible delta")
            return delta

        if event_type not in {"run_completed", "run_cancelled", "run_failed"}:
            return None
        if self._terminal_type is not None:
            raise RuntimeError(
                f"Multiple terminal chat events: {self._terminal_type}, {event_type}"
            )
        self._terminal_type = event_type

        if event_type == "run_completed":
            raw_result = getattr(event, "result", None)
            if isinstance(raw_result, ChatRunResult):
                self.result = raw_result
            elif hasattr(raw_result, "model_dump"):
                self.result = ChatRunResult.model_validate(raw_result.model_dump())
            else:
                self.result = ChatRunResult.model_validate(raw_result)
        elif event_type == "run_cancelled":
            self.was_cancelled = True
        else:
            self.failure_code = str(getattr(event, "error_code", "unknown_error"))
            self.failure_message = str(getattr(event, "message", ""))
        return None


class RunCompleted(StreamEvent):
    type: Literal["run_completed"] = "run_completed"
    result: ChatRunResult


class RunCancelled(StreamEvent):
    type: Literal["run_cancelled"] = "run_cancelled"
    reason: str = "user_cancelled"
    partial_text: str = ""


class RunFailed(StreamEvent):
    type: Literal["run_failed"] = "run_failed"
    error_code: str
    message: str
    partial_text: str = ""


ChatEvent: TypeAlias = Annotated[
    (
    RunStarted
    | RouteSelected
    | StepStarted
    | StepFinished
    | ToolStarted
    | ToolFinished
    | TextStarted
    | TextDelta
    | TextFinished
    | SourcesUpdated
    | UsageUpdated
    | RunCompleted
    | RunCancelled
    | RunFailed
    ),
    Field(discriminator="type"),
]
TerminalEvent: TypeAlias = RunCompleted | RunCancelled | RunFailed
EventSink: TypeAlias = Callable[[ChatEvent], None]


class StreamingCancelled(Exception):
    """Internal control signal raised before partial output can be committed."""

    def __init__(self, partial_text: str = "") -> None:
        super().__init__("Chat run cancelled")
        self.partial_text = partial_text


class StreamingContext:
    """Request-scoped sequencing, timing, emission and cancellation state."""

    def __init__(
        self,
        *,
        session_id: str,
        run_id: str | None = None,
        message_id: str | None = None,
        sink: EventSink | None = None,
    ) -> None:
        self.session_id = session_id
        self.run_id = run_id or uuid.uuid4().hex
        self.message_id = message_id or uuid.uuid4().hex
        self.cancel_event = threading.Event()
        self.started_at = time.perf_counter()
        self.first_text_at: float | None = None
        self._sink = sink
        self._sequence = 0
        self._terminal = False
        self._lock = threading.Lock()

    @property
    def is_cancelled(self) -> bool:
        return self.cancel_event.is_set()

    @property
    def is_terminal(self) -> bool:
        return self._terminal

    def cancel(self) -> None:
        self.cancel_event.set()

    def elapsed_ms(self) -> int:
        return int((time.perf_counter() - self.started_at) * 1000)

    def emit(self, event_type: type[StreamEvent], /, **payload: Any) -> ChatEvent:
        """Build and deliver one ordered event; terminal events close the run."""
        with self._lock:
            if self._terminal:
                raise RuntimeError("Cannot emit an event after the run terminated")
            self._sequence += 1
            event = cast(ChatEvent, event_type(
                run_id=self.run_id,
                session_id=self.session_id,
                sequence=self._sequence,
                timestamp=datetime.now(timezone.utc),
                **payload,
            ))
            if isinstance(event, TextDelta) and self.first_text_at is None:
                self.first_text_at = time.perf_counter()
            if isinstance(event, (RunCompleted, RunCancelled, RunFailed)):
                self._terminal = True
        if self._sink is not None:
            self._sink(event)
        return event

    def collect(self, producer: Callable[[StreamingContext], Iterator[ChatEvent]]) -> list[ChatEvent]:
        """Materialize an event producer for blocking compatibility callers."""
        return list(producer(self))


class ActiveRunRegistry:
    """Thread-safe registry used to cancel the active run of a UI session."""

    def __init__(self) -> None:
        self._runs: dict[str, StreamingContext] = {}
        self._lock = threading.Lock()

    def register(self, context: StreamingContext) -> None:
        with self._lock:
            previous = self._runs.get(context.session_id)
            if previous is not None and not previous.is_terminal:
                previous.cancel()
            self._runs[context.session_id] = context

    def cancel(self, session_id: str) -> bool:
        with self._lock:
            context = self._runs.get(session_id)
            if context is None or context.is_terminal:
                return False
            context.cancel()
            return True

    def finish(self, context: StreamingContext) -> None:
        with self._lock:
            if self._runs.get(context.session_id) is context:
                self._runs.pop(context.session_id, None)

    def get(self, session_id: str) -> StreamingContext | None:
        with self._lock:
            return self._runs.get(session_id)
