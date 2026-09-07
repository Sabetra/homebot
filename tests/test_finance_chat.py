from __future__ import annotations

from finance.query_planner import FinanceQueryPlan
from agent.tool_schemas import get_finance_tool_schemas
from finance.chat import FinanceChatEngine, FinanceChatResult, FinanceChatTrace
from finance.tools import FinanceTools


class _DummyPlanner:
    def plan(self, **_kwargs):
        return None


class _DummyToolkit:
    def execute_tool(self, *_args, **_kwargs):
        raise AssertionError("execute_tool should not be called in this test")


class _DummyLLM:
    def generate_with_tools(self, **_kwargs):
        return {
            "content": (
                "Die Auswertung ist abgeschlossen.\n\n"
                "[FOLLOW_UP]\n"
                "1. Wie entwickeln sich die Kosten im naechsten Monat?\n"
                "2. Welche Kategorien treiben die Ausgaben am staerksten?\n"
                "[/FOLLOW_UP]"
            ),
            "finish_reason": "stop",
            "tool_calls": [],
        }


class _TestableFinanceChatEngine(FinanceChatEngine):
    def __init__(self) -> None:
        super().__init__(_DummyLLM(), _DummyToolkit(), max_tokens=64, allow_python=False)

    def _get_planner(self):
        return _DummyPlanner()

    def _load_schema_context(self):
        return {}


def test_finance_chat_result_exposes_followup_questions_field():
    result = FinanceChatResult(
        answer="Antwort",
        messages=[],
        trace=FinanceChatTrace(),
    )

    assert result.followup_questions == []


def test_finance_chat_engine_extracts_followup_questions_into_result():
    engine = _TestableFinanceChatEngine()

    result = engine.respond("Wie geht es weiter?")

    assert result.answer == "Die Auswertung ist abgeschlossen."
    assert result.followup_questions == [
        "Wie entwickeln sich die Kosten im naechsten Monat?",
        "Welche Kategorien treiben die Ausgaben am staerksten?",
    ]


def test_finance_chat_without_python_exposes_only_finance_tools():
    engine = _TestableFinanceChatEngine()

    exposed_names = {
        tool["function"]["name"]
        for tool in engine._tools
    }

    assert exposed_names
    assert all(name.startswith("finance_") for name in exposed_names)
    assert "code_executor" not in exposed_names
    assert not engine._is_allowed_tool("code_executor")


def test_every_exposed_finance_tool_has_an_implementation():
    exposed_names = {
        tool["function"]["name"]
        for tool in get_finance_tool_schemas()
    }

    missing = sorted(
        name
        for name in exposed_names
        if not hasattr(FinanceTools, name.removeprefix("finance_"))
    )

    assert missing == []


def test_successful_initial_direct_plan_finalizes_without_more_tool_calls():
    class _DirectPlanner:
        def plan(self, **_kwargs):
            return FinanceQueryPlan(
                primary_tool="finance_sum_counterparty_costs",
                arguments={"counterparty": "Example Grocer"},
                rationale="Direct deterministic aggregation.",
                confidence=1.0,
            )

    class _DirectToolkit:
        def __init__(self):
            self.calls = []

        def execute_tool(self, name, args):
            self.calls.append((name, args))
            return {"success": True, "expense": 800.0, "currency": "CHF"}

    class _DirectLLM:
        def __init__(self):
            self.tool_choices = []

        def generate_with_tools(self, **kwargs):
            self.tool_choices.append(kwargs["tool_choice"])
            return {
                "content": "Im Juni wurden 800,00 CHF ausgegeben.",
                "finish_reason": "stop",
                "tool_calls": [],
            }

    toolkit = _DirectToolkit()
    llm = _DirectLLM()
    engine = FinanceChatEngine(llm, toolkit, max_tokens=64, allow_python=False)
    engine._planner = _DirectPlanner()
    engine._load_schema_context = lambda: {}

    result = engine.respond("Was habe ich bei Example Grocer ausgegeben?")

    assert result.answer == "Im Juni wurden 800,00 CHF ausgegeben."
    assert toolkit.calls == [
        ("finance_sum_counterparty_costs", {"counterparty": "Example Grocer"})
    ]
    assert llm.tool_choices == ["none"]
    assert result.trace.finish_reason == "planned_tool_complete"
