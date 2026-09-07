from __future__ import annotations

import json
import time
from types import MethodType
from typing import Any

from agent.react_agent import ReActAgent
class _StrictAlternatingToolLoader:
    def __init__(self) -> None:
        self.messages = []

    def generate_with_tools(self, **kwargs):
        self.messages = kwargs["messages"]
        roles = [message["role"] for message in self.messages]
        assert roles[0] == "system"
        assert all(left != right for left, right in zip(roles[1:], roles[2:]))
        return {"content": "Direkte Antwort", "tool_calls": [], "finish_reason": "stop"}


class _TextResponseLoader:
    def __init__(self, content: str) -> None:
        self.content = content
        self.tools = []

    def generate_with_tools(self, **kwargs):
        self.tools = kwargs["tools"]
        return {"content": self.content, "tool_calls": [], "finish_reason": "stop"}


def test_initial_react_context_uses_single_system_turn_for_gguf_templates() -> None:
    agent = ReActAgent.__new__(ReActAgent)
    agent.max_iterations = 8
    agent.summarizer_max_tokens = 4096
    agent.model_loader = _StrictAlternatingToolLoader()
    agent.tool_schemas = []
    agent._enforce_context_window = lambda messages, _state: messages
    agent._build_token_aware_history = lambda _history, _state: []
    agent._format_working_memory = lambda _memory: "[MEMORY] verified fact"
    agent._detect_simulated_tool_results = lambda _content: False

    result = agent._node_agent_step({
        "query": "Erstelle ein Programm",
        "messages": [],
        "iteration": 0,
        "max_iterations": 8,
        "plan_steps": ["Code erzeugen", "Code testen"],
        "rag_prefetch_context": "[RAG] context",
        "working_memory": ["verified fact"],
        "history": [],
        "tool_results": [],
        "trace": {"iterations": [], "total_tool_calls": 0, "tools_used": []},
    })

    assert [message["role"] for message in agent.model_loader.messages] == ["system", "user"]
    system_content = agent.model_loader.messages[0]["content"]
    assert "[PLAN" in system_content
    assert "[RAG] context" in system_content
    assert "[MEMORY] verified fact" in system_content
    assert result["final_answer"] == "Direkte Antwort"



def test_react_callback_receives_only_redacted_canonical_answer() -> None:
    agent = ReActAgent.__new__(ReActAgent)
    agent._append_source_citations = MethodType(
        lambda self, text, _state: f"{text} [1]",
        agent,
    )
    agent._verify_answer = MethodType(
        lambda self, _text, _state: {"grounding_score": 1.0},
        agent,
    )
    agent._apply_verification_feedback = MethodType(
        lambda self, text, _verification, _state: f"{text} verified",
        agent,
    )
    agent._redact_pii = MethodType(lambda self, _text: "SAFE FINAL", agent)
    emitted: list[str] = []
    state: dict[str, Any] = {
        "final_answer": "Draft containing private@example.com and enough text.",
        "stream_callback": emitted.append,
        "trace": {},
        "tool_results": [],
        "start_time": time.perf_counter(),
    }

    result = agent._node_synthesize(state)

    assert emitted == ["SAFE FINAL"]
    assert result["final_answer"] == "SAFE FINAL"


def test_successful_code_execution_is_not_rewritten_for_low_source_grounding() -> None:
    agent = ReActAgent.__new__(ReActAgent)
    original_answer = "Das Programm wurde erfolgreich ausgeführt und steht als Datei bereit."

    result = agent._apply_verification_feedback(
        original_answer,
        {"grounding_score": 0.01, "hallucination_risk": 0.0},
        {
            "query": "Berechne und teste das Ergebnis mit Python.",
            "tool_results": [
                {
                    "tool": "code_executor",
                    "success": True,
                    "result": "Programm erfolgreich ausgeführt.",
                }
            ],
        },
    )

    assert result == original_answer


def test_code_recovery_preserves_requested_program_delivery() -> None:
    agent = ReActAgent.__new__(ReActAgent)
    content = """```python
import tkinter as tk

def main():
    root = tk.Tk()
    root.mainloop()

main()
```"""

    calls = agent._extract_code_from_text_response(
        content,
        query="Erstelle ein Spiel und stelle tetris.py als Download bereit.",
    )

    assert calls is not None
    arguments = json.loads(calls[0]["function"]["arguments"])
    assert arguments["deliver_to_user"] is True
    assert arguments["artifact_name"] == "tetris.py"
    assert arguments.get("detached") is None

    gui_calls = agent._extract_code_from_text_response(
        content,
        query="Starte das GUI und stelle tetris.py als Download bereit.",
    )
    assert gui_calls is not None
    gui_arguments = json.loads(gui_calls[0]["function"]["arguments"])
    assert gui_arguments["detached"] is True


def _make_text_response_agent(content: str) -> ReActAgent:
    agent = ReActAgent.__new__(ReActAgent)
    agent.max_iterations = 4
    agent.summarizer_max_tokens = 4096
    agent.model_loader = _TextResponseLoader(content)
    agent.tool_schemas = []
    agent._enforce_context_window = lambda messages, _state: messages
    agent._build_token_aware_history = lambda _history, _state: []
    agent._format_working_memory = lambda _memory: ""
    agent._detect_simulated_tool_results = lambda _content: False
    agent._detect_stalled_tool_call = lambda _content: False
    agent._content_is_raw_tool_call = lambda _content: False
    return agent


def test_code_recovery_allows_a_second_attempt_after_execution_failure() -> None:
    content = """```python
import tkinter as tk

def main():
    root = tk.Tk()
    root.mainloop()

main()
```"""
    agent = _make_text_response_agent(content)

    result = agent._node_agent_step({
        "query": "Erstelle tetris.py als Download.",
        "messages": [],
        "iteration": 1,
        "max_iterations": 4,
        "history": [],
        "tool_results": [{"tool": "code_executor", "success": False}],
        "trace": {
            "iterations": [],
            "total_tool_calls": 1,
            "tools_used": ["code_executor"],
            "code_auto_extractions": 1,
        },
    })

    assert result["should_continue"] is True
    assert result["pending_tool_calls"][0]["function"]["name"] == "code_executor"


def test_failed_code_execution_cannot_end_with_a_success_claim() -> None:
    agent = _make_text_response_agent(
        "Das Programm wurde erfolgreich getestet und tetris.py steht zum Download bereit."
    )

    result = agent._node_agent_step({
        "query": "Erstelle tetris.py als Download.",
        "messages": [],
        "iteration": 3,
        "max_iterations": 4,
        "history": [],
        "tool_results": [{"tool": "code_executor", "success": False}],
        "trace": {"iterations": [], "total_tool_calls": 1, "tools_used": ["code_executor"]},
    })

    assert result["should_continue"] is False
    assert "keine getestete Datei" in result["final_answer"]
    assert "erfolgreich getestet" not in result["final_answer"]


def test_agent_no_rag_route_does_not_offer_retrieval_tools() -> None:
    """agent_no_rag entfernt NUR das RAG-Store-Tool (rag_search).

    SOTA 2026-08-21 (Root-Cause-Fix): `agent_no_rag` ist KEIN
    Web-Deaktivierer mehr -- `web_search` bleibt verfügbar. Die Code-Query
    triggert das Code-Profil, das `code_executor` + `web_search` offeriert
    (damit Bibliotheks-/Doku-Fragen nicht in FS-Tool-Halluzinationen wie
    `list_directory(q=..., num=...)` umgelenkt werden).
    """
    agent = _make_text_response_agent("Direkte Antwort ohne Recherche.")
    agent.tool_schemas = [
        {"type": "function", "function": {"name": "rag_search"}},
        {"type": "function", "function": {"name": "web_search"}},
        {"type": "function", "function": {"name": "code_executor"}},
    ]

    agent._node_agent_step({
        "query": "Erstelle ein Python-Spiel.",
        "route": "agent_no_rag",
        "messages": [],
        "iteration": 0,
        "max_iterations": 4,
        "history": [],
        "tool_results": [],
        "trace": {"iterations": [], "total_tool_calls": 0, "tools_used": []},
    })

    offered_names = {
        schema["function"]["name"] for schema in agent.model_loader.tools
    }
    # Kern-Intent (unverändert): Das RAG-Store-Tool entfällt bei agent_no_rag.
    assert "rag_search" not in offered_names
    # Neu 2026-08-21: web_search bleibt, Code-Profil = code_executor + web_search.
    assert offered_names == {"code_executor", "web_search"}


def test_python_creation_route_offers_only_code_executor() -> None:
    agent = _make_text_response_agent("Direkte Antwort.")
    agent.tool_schemas = [
        {"type": "function", "function": {"name": "finance_apply_rules"}},
        {"type": "function", "function": {"name": "create_diagram"}},
        {"type": "function", "function": {"name": "code_executor"}},
    ]

    schemas = agent._tool_schemas_for_state({
        "route": "agent_no_rag",
        "query": "Erstelle ein vollständiges Python-Programm.",
    })

    assert [schema["function"]["name"] for schema in schemas] == ["code_executor"]


def test_requested_download_requires_file_artifact_for_reflection_success() -> None:
    agent = ReActAgent.__new__(ReActAgent)
    agent.max_iterations = 4
    missing_file_state = {
        "query": "Erstelle tetris.py als Download-Datei.",
        "tool_results": [{"tool": "code_executor", "success": True, "result": "292"}],
        "artifacts": [],
    }

    missing_result = agent._node_reflect(missing_file_state)
    assert missing_result["reflection_confidence"] == 0.0
    assert "deliver_to_user=true" in missing_result["reflection_guidance"]
    assert agent._edge_after_reflect({
        **missing_file_state,
        **missing_result,
        "iteration": 1,
        "max_iterations": 4,
    }) == "retry"

    delivered_result = agent._node_reflect({
        **missing_file_state,
        "artifacts": [{"type": "file", "path": "code_sandbox/user_programs/tetris.py"}],
    })
    assert delivered_result["reflection_confidence"] == 1.0


def test_low_grounding_delivery_answer_requires_real_file_artifact() -> None:
    agent = ReActAgent.__new__(ReActAgent)
    agent.model_loader = _TextResponseLoader("Ohne Datei nicht verifiziert.")
    agent.summarizer_max_tokens = 256
    answer = "Die Datei wurde erfolgreich erstellt."
    state = {
        "query": "Erstelle tetris.py als Download-Datei.",
        "tool_results": [{"tool": "code_executor", "success": True, "result": "292"}],
        "artifacts": [],
    }

    result = agent._apply_verification_feedback(
        answer,
        {"grounding_score": 0.01, "hallucination_risk": 0.0},
        state,
    )

    assert result != answer