from __future__ import annotations

from types import MethodType
from typing import Any

from agent.streaming_events import (
    RunCancelled,
    RunCompleted,
    StepFinished,
    StepStarted,
    TextDelta,
)
from agent_chatbot_logic import AgentChatbotLogic
class _SemanticProgramRouterLoader:
    def __init__(self) -> None:
        self.prompt = ""

    def generate_response(self, **kwargs):
        self.prompt = kwargs["messages"][0]["content"]
        if "vollständiges ausführbares Programm" in self.prompt:
            return "FINAL_MODE=REACT"
        return "FINAL_MODE=PLAN_EXECUTE"


def test_reusable_program_requests_are_semantically_routed_to_react() -> None:
    logic = AgentChatbotLogic.__new__(AgentChatbotLogic)
    logic.agent_mode_enabled = True
    logic.chat_routing_config = {"use_llm_routing": True}
    logic.settings = {"use_react_agent": True}
    logic.model_loader = _SemanticProgramRouterLoader()

    mode = logic._select_agent_execution_mode("Erstelle ein nutzbares Tetris-Spiel als Python-Datei")

    assert mode == "REACT"
    assert "strukturierten Tool-Argumenten" in logic.model_loader.prompt


def test_filesystem_intent_is_forced_to_plan_execute() -> None:
    logic = AgentChatbotLogic.__new__(AgentChatbotLogic)
    logic.agent_mode_enabled = True
    logic.chat_routing_config = {"use_llm_routing": True}
    logic.settings = {"use_react_agent": True}
    # Deliberately set a loader that would classify as SIMPLE if called.
    class _SimpleLoader:
        def generate_response(self, **kwargs):
            return "FINAL_MODE=SIMPLE"

    logic.model_loader = _SimpleLoader()

    mode = logic._select_agent_execution_mode(
        'kannst du diese Datei suchen: "C:\\Dokumente\\geladen_chat.pdf"'
    )

    assert mode == "PLAN_EXECUTE"
    assert "filesystem_intent" in logic._last_routing_debug


def test_response_cache_is_limited_to_simple_successful_text() -> None:
    logic = AgentChatbotLogic.__new__(AgentChatbotLogic)
    logic.cache_enabled = True
    logic.cache_max_size = 10
    logic.response_cache = {}

    logic._cache_response("error", "[ERROR] Tool-Call fehlgeschlagen")
    logic._cache_response("ok", "Eine direkte Antwort")

    assert logic._can_use_response_cache("SIMPLE")
    assert not logic._can_use_response_cache("REACT")
    assert not logic._can_use_response_cache("PLAN_EXECUTE")
    assert "error" not in logic.response_cache
    assert logic.response_cache["ok"] == "Eine direkte Antwort"



def _logic_with_chat(fake_chat: Any) -> AgentChatbotLogic:
    logic = AgentChatbotLogic.__new__(AgentChatbotLogic)
    logic.last_sources = []
    logic.last_followup_questions = []
    logic.last_trace = None
    logic.message_history = [{"role": "assistant", "content": "before"}]
    logic.chat = MethodType(fake_chat, logic)  # type: ignore[method-assign]
    return logic


def test_stream_adapter_preserves_visible_and_canonical_text() -> None:
    def fake_chat(self: AgentChatbotLogic, _prompt: str, **kwargs: Any) -> str:
        kwargs["progress_callback"]("Routing", "SIMPLE")
        kwargs["stream_callback"]("Hal")
        kwargs["stream_callback"]("lo")
        return "Hallo"

    logic = _logic_with_chat(fake_chat)
    events = list(logic.stream_chat_events("Hallo", session_id="session-1"))

    visible_text = "".join(
        event.delta for event in events if isinstance(event, TextDelta)
    )
    terminal = events[-1]
    assert isinstance(terminal, RunCompleted)
    assert visible_text == terminal.result.text == "Hallo"
    assert sum(isinstance(event, RunCompleted) for event in events) == 1
    started_steps = [event for event in events if isinstance(event, StepStarted)]
    finished_steps = [event for event in events if isinstance(event, StepFinished)]
    assert [event.label for event in started_steps] == ["Routing: SIMPLE"]
    assert [event.step_id for event in finished_steps] == [started_steps[0].step_id]
    assert finished_steps[0].status == "completed"


def test_stream_adapter_cancellation_never_completes() -> None:
    def fake_chat(self: AgentChatbotLogic, _prompt: str, **kwargs: Any) -> str:
        kwargs["stream_callback"]("Teil")
        self.message_history.append({"role": "assistant", "content": "partial"})
        assert self.cancel_stream("session-cancel")
        kwargs["stream_callback"](" darf nicht erscheinen")
        return "Teil darf nicht erscheinen"

    logic = _logic_with_chat(fake_chat)
    events = list(logic.stream_chat_events(
        "Abbrechen",
        session_id="session-cancel",
    ))

    deltas = [event.delta for event in events if isinstance(event, TextDelta)]
    assert deltas == ["Teil"]
    assert isinstance(events[-1], RunCancelled)
    assert events[-1].partial_text == "Teil"
    assert not any(isinstance(event, RunCompleted) for event in events)
    assert logic.message_history == [{"role": "assistant", "content": "before"}]


def test_chat_resets_stale_run_state_before_each_run() -> None:
    """Regression (2026-09-12): Ein Run darf keinen `last_*`-Zustand aus dem
    vorherigen Run erben — sonst landet z. B. die AgentTrace eines
    PLAN_EXECUTE-Runs in der Telemetrie eines SIMPLE-Runs (beobachtet:
    identische `trace_summary` in 3 Folge-Runs)."""
    from agent.agent_types import AgentTrace

    logic = AgentChatbotLogic.__new__(AgentChatbotLogic)
    # Alter Zustand aus einem "früheren" Run:
    logic.last_trace = AgentTrace(planner_ms=18032, tools_ms=151994)
    logic.last_sources = [{"title": "stale-source"}]
    logic.last_graphics = [{"type": "image"}]
    logic.last_files = [{"name": "stale.py"}]
    logic.last_followup_questions = ["stale?"]
    # Kurze Antwort → Follow-Up-Post-Processing wird übersprungen.
    logic._chat_core = lambda *args, **kwargs: "kurze Antwort"  # type: ignore[assignment]

    logic.chat("Hallo")  # type: ignore[arg-type]

    assert logic.last_trace is None
    assert logic.last_sources == []
    assert logic.last_graphics == []
    assert logic.last_files == []


def test_stream_completed_result_has_no_stale_trace() -> None:
    """E2E: Stale-Trace aus dem Vor-Run darf nicht im RunCompleted-Result
    auftauchen, wenn der aktuelle Pfad keine Trace erzeugt."""
    from agent.agent_types import AgentTrace

    logic = AgentChatbotLogic.__new__(AgentChatbotLogic)
    logic.message_history = [{"role": "assistant", "content": "before"}]
    logic.last_sources = []
    logic.last_followup_questions = []
    logic.last_trace = AgentTrace(planner_ms=999999, ran_tools=["stale_tool"])
    logic.last_graphics = []
    logic.last_files = []
    logic._chat_core = lambda *args, **kwargs: "Antwort ohne Trace"  # type: ignore[assignment]

    events = list(logic.stream_chat_events("Hallo", session_id="session-stale"))
    terminal = events[-1]

    assert isinstance(terminal, RunCompleted)
    assert terminal.result.trace is None
    assert terminal.result.sources == []