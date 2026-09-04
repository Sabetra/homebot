"""Progressive Disclosure — Phase 1 Tests (2026-08-24)
======================================================

Gedeckt (ohne LLM, ohne Streaming, deterministisch):
  - Finance-Intent-Erkennung (Regex, DE+EN, recall-bias)
  - Capability-Gap-Erkennung (Gap-Phrasierung + Finance-Bezug)
  - ReActAgent._resolve_tool_pool_names:
      * Profil-Gating pro tab_mode (main_chat / finance_tab / Fallback)
      * Finance-Intent-Override (nur ERWEITERUNG, nie Reduktion)
      * tool_pool-Override (Capability-Gap-Retry-Semantik)
      * Finance-Write-Tools kommen NIEMALS in einen ReAct-Pool
  - ReActAgent._tool_schemas_for_state (Retrieval als Identity-Passthrough):
      * Routing-Semantik bleibt erhalten (agent_no_rag, Code-Profil)
      * Leerer Pool → expliziter Rückfall auf volles Tool-Set
  - ReActAgent._maybe_capability_gap_retry:
      * Trigger-Matrix (max 1x, kein Retry bei Streaming/Intent-Mangel/
        bereits Finance-Tools im Pool/keiner Gap-Antwort)
      * Retry-Fehler → Original-Ergebnis bleibt erhalten
      * Trace-Anreicherung (capability_gap_retry beobachtbar)
"""

import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from agent.tool_profiles import (
    FINANCE_CORE,
    FINANCE_WRITE_TOOLS,
    get_profile,
)


# ── Deterministische Fake-Registry (unabhängig vom echten Toolkit) ────────

CORE_TOOLS = [
    "calculator", "canvas", "code_executor", "file_reader", "file_writer",
    "list_directory", "pdf_extract", "rag_search", "search_files", "web_search",
]
DUMMY_TOOLS = [f"tool_{c}" for c in "abcdefgh"]


def _schema(name: str, desc: str = "") -> Dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": desc or f"Tool {name}.",
            "parameters": {"type": "object", "properties": {}},
        },
    }


FULL_REGISTRY: List[Dict[str, Any]] = [
    _schema(n) for n in CORE_TOOLS + DUMMY_TOOLS
]
FINANCE_REGISTRY: List[Dict[str, Any]] = FULL_REGISTRY + [
    _schema(n, "Finance tool.") for n in FINANCE_CORE
]


def _names(schemas: List[Dict[str, Any]]) -> set:
    return {s["function"]["name"] for s in schemas}


# ═══════════════════════════════════════════════════════════════════════════
# 1. Finance-Intent-Erkennung (pure Regex)
# ═══════════════════════════════════════════════════════════════════════════


class TestFinanceIntentRegex:
    """Deterministische Intent-Prüfung: breit (recall), kein Problem bei
    False-Positiven, weil sie den Pool nur ERWEITERT (harmlos)."""

    def test_german_positive(self):
        from agent.react_agent import _has_finance_intent

        assert _has_finance_intent("Wie hoch ist mein Kontostand?")
        assert _has_finance_intent("Zeig mir meine letzten Transaktionen.")
        assert _has_finance_intent("Wie ist mein Budgetstatus?")
        assert _has_finance_intent("Welche Einzahlungen habe ich gehabt?")

    def test_english_positive(self):
        from agent.react_agent import _has_finance_intent

        assert _has_finance_intent("What is my account balance?")
        assert _has_finance_intent("Show my recent transactions.")
        assert _has_finance_intent("What is my spending budget?")

    def test_negative(self):
        from agent.react_agent import _has_finance_intent

        assert not _has_finance_intent("Schreib ein Python Skript.")
        assert not _has_finance_intent("Was ist das Wetter heute?")
        assert not _has_finance_intent("")
        assert not _has_finance_intent(None)  # type: ignore[arg-type]

    def test_recognizes_intent_in_sentence(self):
        from agent.react_agent import _has_finance_intent

        # Intent mitten im Satz, umgebende Wörter sind irrelevant
        assert _has_finance_intent("Kannst du mir bitte meinen IBAN-Zugang zeigen?")


# ═══════════════════════════════════════════════════════════════════════════
# 2. Capability-Gap-Erkennung (Gap-Phrasierung + Finance-Bezug)
# ═══════════════════════════════════════════════════════════════════════════


class TestCapabilityGapDetection:
    """Gap-Prüfung braucht BEIDES: Gap-Phrasierung UND Finance-Bezug
    (Domain in Antwort ODER Intent in Query) — sonst False-Positive bei
    normalen 'Das ist nicht möglich'-Antworten."""

    def test_gap_with_finance_domain_in_answer(self):
        from agent.react_agent import _looks_like_capability_gap

        answer = ("Das ist leider nicht möglich, ich habe keinen Zugriff "
                  "auf Ihren Kontostand.")
        assert _looks_like_capability_gap("Zeig mir meinen Kontostand", answer)

    def test_gap_via_query_intent_fallback(self):
        from agent.react_agent import _looks_like_capability_gap

        # Antwort ohne klare Finance-Domain, aber Query hat Intent → zählt
        answer = "Das ist leider nicht möglich."
        assert _looks_like_capability_gap("Mein Budget bitte", answer)

    def test_gap_phrase_without_finance_no_match(self):
        from agent.react_agent import _looks_like_capability_gap

        # Gap-Phrasierung, aber kein Finance-Bezug → KEIN False-Positive
        assert not _looks_like_capability_gap(
            "Wie wird ein Brötchen gebacken?", "Das ist leider nicht möglich."
        )

    def test_no_gap_phrase_no_match(self):
        from agent.react_agent import _looks_like_capability_gap

        answer = "Alles klar, hier sind Ihre Kontodaten."
        assert not _looks_like_capability_gap("Kontoauszug bitte", answer)

    def test_empty_answer_no_match(self):
        from agent.react_agent import _looks_like_capability_gap



# ═══════════════════════════════════════════════════════════════════════════
# 3. Pool-Auflösung: Profil + Intent-Override + Override + Write-Safety
# ═══════════════════════════════════════════════════════════════════════════


class _PoolStub:
    """Minimaler Stub: bindet nur die zu prüfende Methode."""

    def __init__(self, registry: List[Dict[str, Any]]):
        from agent.react_agent import ReActAgent

        self.tool_schemas = registry
        self._resolve_tool_pool_names = ReActAgent._resolve_tool_pool_names.__get__(self)


class TestResolveToolPoolNames:
    def test_main_chat_pool_is_profile_tools(self):
        stub = _PoolStub(FULL_REGISTRY)
        pool = stub._resolve_tool_pool_names(
            {"tab_mode": "main_chat", "query": "Was ist das Wetter?"}
        )
        assert pool == set(CORE_TOOLS)  # nur Profil-Tools, keine Dummies

    def test_unknown_tab_mode_falls_back_to_main_chat(self):
        stub = _PoolStub(FULL_REGISTRY)
        pool = stub._resolve_tool_pool_names(
            {"tab_mode": "komisch_unbekannt", "query": "Hallo"}
        )
        assert pool == set(CORE_TOOLS)

    def test_missing_tab_mode_defaults_to_main_chat(self):
        stub = _PoolStub(FULL_REGISTRY)
        pool = stub._resolve_tool_pool_names({"query": "Hallo"})
        assert pool == set(CORE_TOOLS)

    def test_finance_tab_pool_contains_core_finance_tools(self):
        stub = _PoolStub(FINANCE_REGISTRY)
        pool = stub._resolve_tool_pool_names(
            {"tab_mode": "finance_tab", "query": "Hallo"}
        )
        assert set(FINANCE_CORE) <= pool
        assert pool <= _names(FINANCE_REGISTRY)

    def test_finance_intent_expands_main_chat_pool(self):
        """Klar-finanzielle Query erweitert den main_chat-Pool um FINANCE_CORE."""
        stub = _PoolStub(FINANCE_REGISTRY)
        pool = stub._resolve_tool_pool_names(
            {"tab_mode": "main_chat", "query": "Wie hoch ist mein Kontostand?"}
        )
        assert set(FINANCE_CORE) <= pool
        # und die normalen Main-Chat-Tools bleiben erhalten
        assert "web_search" in pool
        assert "calculator" in pool

    def test_finance_intent_never_shrinks_pool(self):
        """Nicht-finanzielle Query im finance_tab → voller Pool (keine Reduktion)."""
        stub = _PoolStub(FINANCE_REGISTRY)
        base = stub._resolve_tool_pool_names(
            {"tab_mode": "finance_tab", "query": "Hallo"}
        )
        expanded = stub._resolve_tool_pool_names(
            {"tab_mode": "finance_tab", "query": "Mein Kontostand?"}
        )
        assert base <= expanded  # nur Erweiterung möglich

    def test_intent_extension_only_for_registered_tools(self):
        """Intent-Override addiert nur FINANCE_CORE-Tools, die in der Registry
        existieren (keine Phantom-Namen im Pool)."""
        stub = _PoolStub(FULL_REGISTRY)  # keine Finance-Tools registriert
        pool = stub._resolve_tool_pool_names(
            {"tab_mode": "main_chat", "query": "Kontostand?"}
        )
        assert pool <= _names(FULL_REGISTRY)
        assert set(FINANCE_CORE) & pool == set()

    def test_tool_pool_override_takes_precedence(self):
        stub = _PoolStub(FINANCE_REGISTRY)
        pool = stub._resolve_tool_pool_names(
            {
                "tab_mode": "finance_tab",
                "query": "Kontostand?",
                "tool_pool": ["calculator", "web_search"],
            }
        )
        assert pool == {"calculator", "web_search"}

    def test_override_filters_unknown_names(self):
        stub = _PoolStub(FINANCE_REGISTRY)
        pool = stub._resolve_tool_pool_names(
            {"tab_mode": "main_chat", "query": "x", "tool_pool": ["bogus", "calculator"]}
        )
        assert pool == {"calculator"}

    def test_override_plus_finance_intent_extends(self):
        """Retry-Semantik: Override-Pool + Intent → FINANCE_CORE wird ergänzt."""
        stub = _PoolStub(FINANCE_REGISTRY)
        pool = stub._resolve_tool_pool_names(
            {
                "tab_mode": "main_chat",
                "query": "Kontostand?",
                "tool_pool": ["calculator"],
            }
        )
        assert "calculator" in pool
        assert set(FINANCE_CORE) <= pool


class TestFinanceWriteSafety:
    """Finance-Write/Admin-Tools dürfen NIEMALS in einen ReAct-Pool gelangen
    (Read-Only-Prinzip; Mutationen laufen nur über die Finanz-Oberfläche)."""

    @pytest.mark.parametrize("tab_mode", ["main_chat", "finance_tab", "psych_tab"])
    @pytest.mark.parametrize("query", ["Kontostand?", "Mein Budget bitte", "Hallo"])
    def test_write_tools_never_in_pool(self, tab_mode: str, query: str):
        stub = _PoolStub(FINANCE_REGISTRY)
        pool = stub._resolve_tool_pool_names({"tab_mode": tab_mode, "query": query})
        assert set(FINANCE_WRITE_TOOLS) & pool == set()

    def test_finance_core_and_write_are_disjoint(self):
        """Strukturelle Garantie: die Intent-Erweiterung kann nichts
        Schreibendes einbringen, weil CORE und WRITE disjunkt sind."""
        assert set(FINANCE_CORE) & set(FINANCE_WRITE_TOOLS) == set()


# ═══════════════════════════════════════════════════════════════════════════
# 4. Schema-Filterung (_tool_schemas_for_state, Identity-Retrieval)
# ═══════════════════════════════════════════════════════════════════════════


class _SchemaStub:
    """Routing-Semantik ohne Retrieval (Identity-Passthrough)."""

    def __init__(self, registry: List[Dict[str, Any]]):
        from agent.react_agent import ReActAgent

        self.tool_schemas = registry
        self._resolve_tool_pool_names = ReActAgent._resolve_tool_pool_names.__get__(self)
        self._apply_tool_retrieval = lambda state, active: active


class TestToolSchemasForState:
    @pytest.fixture(scope="class")
    @classmethod
    def method(cls):
        from agent.react_agent import ReActAgent

        return ReActAgent._tool_schemas_for_state

    @pytest.fixture(scope="class")
    @classmethod
    def stub(cls):
        return _SchemaStub(FINANCE_REGISTRY)

    def test_main_chat_active_schemas_are_profile_tools(self, method, stub):
        result = method(stub, {"tab_mode": "main_chat", "query": "Was ist das Wetter?"})
        assert _names(result) == set(CORE_TOOLS)

    def test_main_chat_finance_intent_adds_finance_schemas(self, method, stub):
        result = method(
            stub, {"tab_mode": "main_chat", "query": "Wie hoch ist mein Guthaben?"}
        )
        names = _names(result)
        assert set(FINANCE_CORE) <= names
        assert "web_search" in names  # Erweiterung, keine Reduktion

    def test_agent_no_rag_keeps_web_search_drops_rag(self, method, stub):
        """Bestehende Semantik (Regression 2026-08-21) bleibt erhalten."""
        result = method(
            stub,
            {
                "tab_mode": "main_chat",
                "route": "agent_no_rag",
                "query": "Wie viele Spieler Champions League 2025?",
            },
        )
        names = _names(result)
        assert "web_search" in names
        assert "rag_search" not in names
        assert "list_directory" in names

    def test_agent_no_rag_code_profile(self, method, stub):
        result = method(
            stub,
            {
                "tab_mode": "main_chat",
                "route": "agent_no_rag",
                "query": "Schreib ein python skript",
            },
        )
        assert _names(result) == {"code_executor", "web_search"}

    def test_empty_pool_falls_back_to_full_registry(self, method):
        """Leerer Pool wird NIE still akzeptiert → volles Tool-Set."""
        stub = _SchemaStub(FULL_REGISTRY)
        result = method(
            stub,
            {
                "tab_mode": "main_chat",
                "query": "x",
                "tool_pool": ["garbage_name"],
            },
        )
        assert _names(result) == _names(FULL_REGISTRY)



# ═══════════════════════════════════════════════════════════════════════════
# 5. Capability-Gap-Retry (Fake-Graph, max 1x, Fehler-Safety)
# ═══════════════════════════════════════════════════════════════════════════


class _FakeGraph:
    """Deterministischer LangGraph-Ersatz: zeichnet invoke-Zustände auf."""

    def __init__(self, result: Optional[Dict[str, Any]] = None,
                 raise_on_invoke: bool = False):
        self.result = result or {"final_answer": "Retry-Antwort.", "trace": {}}
        self.raise_on_invoke = raise_on_invoke
        self.states: List[Dict[str, Any]] = []

    def invoke(self, state: Dict[str, Any]) -> Dict[str, Any]:
        if self.raise_on_invoke:
            raise RuntimeError("Simulierter Retry-Fehler")
        self.states.append(dict(state))
        return dict(self.result)


class _RetryStub:
    def __init__(self, registry: List[Dict[str, Any]], graph: _FakeGraph):
        from agent.react_agent import ReActAgent

        self.tool_schemas = registry
        self.max_iterations = 8
        self.graph = graph
        self._resolve_tool_pool_names = ReActAgent._resolve_tool_pool_names.__get__(self)
        self._maybe_capability_gap_retry = ReActAgent._maybe_capability_gap_retry.__get__(self)


GAP_ANSWER = ("Das ist leider nicht möglich, ich habe keinen Zugriff "
              "auf Ihren Kontostand.")
FIN_QUERY = "Wie hoch ist mein Kontostand?"


def _initial_state(**overrides) -> Dict[str, Any]:
    state: Dict[str, Any] = {
        "query": FIN_QUERY,
        "history": [],
        "image_path": None,
        "correlation_id": "test-correlation",
        "stream_callback": None,
        "max_iterations": 8,
        "settings": None,
        "tab_mode": "main_chat",
        "tool_pool": None,
        "capability_gap_retry": False,
        "start_time": 1234.5,
    }
    state.update(overrides)
    return state


ORIGINAL_RESULT = {"final_answer": GAP_ANSWER, "trace": {"iterations": []}}


class TestCapabilityGapRetry:
    def test_fires_once_when_finance_tools_missing(self):
        """Hauptfall: keine Finance-Tools in Registry, Finance-Intent +
        Gap-Antwort → genau EIN Retry mit erweitertem Pool, Trace angereichert."""
        graph = _FakeGraph()
        stub = _RetryStub(FULL_REGISTRY, graph)  # Registry OHNE Finance-Tools
        result = stub._maybe_capability_gap_retry(
            _initial_state(), dict(ORIGINAL_RESULT), "test-correlation"
        )
        assert len(graph.states) == 1, "max 1x Retry"
        assert result["final_answer"] == "Retry-Antwort."
        retry_state = graph.states[0]
        assert retry_state["capability_gap_retry"] is True
        assert retry_state["query"] == FIN_QUERY
        assert retry_state["tab_mode"] == "main_chat"
        # Retry-Pool = alter Pool ∪ FINANCE_CORE
        original_pool = _PoolStub(
            FULL_REGISTRY
        )._resolve_tool_pool_names(_initial_state())
        assert set(retry_state["tool_pool"]) >= original_pool
        assert set(FINANCE_CORE) <= set(retry_state["tool_pool"])
        # Observierbarkeit
        retry_trace = result["trace"]["capability_gap_retry"]
        assert retry_trace["triggered"] is True
        assert retry_trace["original_pool_size"] == len(original_pool)
        assert retry_trace["retry_pool_size"] >= len(original_pool)

    def test_no_retry_when_pool_already_has_finance_tools(self):
        graph = _FakeGraph()
        stub = _RetryStub(FINANCE_REGISTRY, graph)
        result = stub._maybe_capability_gap_retry(
            _initial_state(), dict(ORIGINAL_RESULT), "c"
        )
        assert graph.states == []
        assert result["final_answer"] == GAP_ANSWER

    def test_no_retry_without_finance_intent(self):
        graph = _FakeGraph()
        stub = _RetryStub(FULL_REGISTRY, graph)
        result = stub._maybe_capability_gap_retry(
            _initial_state(query="Wie wird ein Brötchen gebacken?"),
            dict(ORIGINAL_RESULT),
            "c",
        )
        assert graph.states == []

    def test_no_retry_when_answer_is_not_a_gap(self):
        graph = _FakeGraph()
        stub = _RetryStub(FULL_REGISTRY, graph)
        result = stub._maybe_capability_gap_retry(
            _initial_state(),
            {"final_answer": "Alles klar, hier ist Ihr Kontostand.", "trace": {}},
            "c",
        )
        assert graph.states == []

    def test_no_retry_when_already_a_retry(self):
        graph = _FakeGraph()
        stub = _RetryStub(FULL_REGISTRY, graph)
        result = stub._maybe_capability_gap_retry(
            _initial_state(capability_gap_retry=True),
            dict(ORIGINAL_RESULT),
            "c",
        )
        assert graph.states == [], "zweiter Retry ist strukturell unmöglich"

    def test_no_retry_when_streaming_active(self):
        """Doppel-Streaming verhindern: Stream aktiv → Retry übersprungen."""
        graph = _FakeGraph()
        stub = _RetryStub(FULL_REGISTRY, graph)
        result = stub._maybe_capability_gap_retry(
            _initial_state(stream_callback=lambda t: None),
            dict(ORIGINAL_RESULT),
            "c",
        )
        assert graph.states == []

    def test_retry_error_preserves_original_result(self):
        graph = _FakeGraph(raise_on_invoke=True)
        stub = _RetryStub(FULL_REGISTRY, graph)
        original = dict(ORIGINAL_RESULT)
        result = stub._maybe_capability_gap_retry(_initial_state(), original, "c")
        assert result is original, "Original-Ergebnis bleibt bei Retry-Fehler erhalten"
