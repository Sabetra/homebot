"""Integrationstest: P0.5 Tool-Result Eviction im echten _node_agent_step.

Instanziiert ReActAgent OHNE __init__ (object.__new__) und stubbt nur die
Attribute, die der Tool-Call-Pfad berührt. Der Stub-ModelLoader zeichnet die
tatsächlich an den LLM-Call übergebenen Messages auf — so wird verifiziert,
dass der Eviction-Hook im Produktionspfad greift (und nicht nur in der
Unit-Test-Isolation).
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.react_agent import ReActAgent
from agent.tool_result_eviction import PLACEHOLDER_MARKER

ARG = {"file_path": "C:\\proj\\modul.py"}
BIG = "z" * 3000  # ~790 Tokens pro Resultat


class _StubModelLoader:
    def __init__(self):
        self.captured_messages = None
        self.calls = 0

    def generate_with_tools(self, **kwargs):
        self.calls += 1
        self.captured_messages = kwargs["messages"]
        return {
            "tool_calls": [{
                "id": "call_next",
                "type": "function",
                "function": {"name": "file_reader", "arguments": ARG},
            }],
            "content": None,
            "finish_reason": "tool_calls",
        }


def _make_agent() -> ReActAgent:
    agent = object.__new__(ReActAgent)
    agent.max_iterations = 8
    agent.summarizer_max_tokens = 2048
    agent._enforce_context_window = lambda msgs, state: msgs  # Identity
    agent._tool_schemas_for_state = lambda state: []
    agent.model_loader = _StubModelLoader()
    return agent


def _state_with_tool_results(results):
    """results: List[(tool_name, content)] → State mit Iteration 1."""
    calls = [
        {"id": f"call_{i}", "type": "function",
         "function": {"name": name, "arguments": ARG}}
        for i, (name, _c) in enumerate(results)
    ]
    messages = [
        {"role": "system", "content": "Du bist ein Coding-Agent."},
        {"role": "user", "content": "Lies die Module und fasse zusammen"},
        {"role": "assistant", "content": "", "tool_calls": calls},
    ]
    for i, (_name, content) in enumerate(results):
        messages.append({"role": "tool", "tool_call_id": f"call_{i}", "content": content})
    return {"messages": messages, "iteration": 1, "max_iterations": 8, "trace": {}}


class TestNodeAgentStepEvictionHook:
    def test_hook_evicts_before_llm_call(self):
        results = [
            ("file_reader", BIG),      # c0 → evictiert
            ("file_reader", BIG),      # c1 → evictiert
            ("file_reader", BIG),      # c2 → intakt
            ("file_reader", BIG),      # c3 → intakt
            ("search_files", BIG),     # c4 → intakt (nur 1, K=2)
        ]
        agent = _make_agent()
        state = _state_with_tool_results(results)

        out = agent._node_agent_step(state)

        # LLM wurde genau 1x aufgerufen
        assert agent.model_loader.calls == 1
        sent = agent.model_loader.captured_messages

        # Tool-Messages: system=0, user=1, assistant=2, tool=3..7
        assert sent[3]["content"].startswith(PLACEHOLDER_MARKER)  # c0
        assert sent[4]["content"].startswith(PLACEHOLDER_MARKER)  # c1
        assert sent[5]["content"] == BIG  # c2
        assert sent[6]["content"] == BIG  # c3
        assert sent[7]["content"] == BIG  # c4 (search_files)

        # Struktur erhalten
        assert [m["role"] for m in sent] == [m["role"] for m in state["messages"]]
        assert sent[3]["tool_call_id"] == "call_0"
        assert sent[4]["tool_call_id"] == "call_1"

        # Platzhalter sind kompakt (Original ~790 Tokens → <100 Tokens)
        assert len(sent[3]["content"]) < 300

        # Agent-Fortschritt: Tool-Call-Pfad aktiv
        assert out["should_continue"] is True
        assert out["pending_tool_calls"][0]["id"] == "call_next"

    def test_state_messages_not_mutated(self):
        results = [("file_reader", BIG) for _ in range(4)]
        agent = _make_agent()
        state = _state_with_tool_results(results)
        original = [dict(m) for m in state["messages"]]

        agent._node_agent_step(state)

        assert state["messages"] == original  # State bleibt unverändert

    def test_small_context_no_eviction_in_node(self):
        # 2 × 100 Zeichen ≈ 53 Tokens < Trigger → alles intakt
        results = [("file_reader", "k" * 100) for _ in range(2)]
        agent = _make_agent()
        state = _state_with_tool_results(results)

        agent._node_agent_step(state)

        sent = agent.model_loader.captured_messages
        assert sent[3]["content"] == "k" * 100
        assert sent[4]["content"] == "k" * 100
        assert not any(
            isinstance(m.get("content"), str) and m["content"].startswith(PLACEHOLDER_MARKER)
            for m in sent
        )