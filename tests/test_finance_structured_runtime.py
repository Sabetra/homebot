from __future__ import annotations

import datetime as dt
import json
from typing import Any, Dict, List

from agent.tool_schemas import get_finance_tool_schemas
from finance.query_planner import FinanceQueryPlanner
from finance.query_reflector import FinanceQueryReflector


class _StructuredLLM:
    def __init__(self, responses: List[Dict[str, Any]]) -> None:
        self._responses = list(responses)
        self.calls: List[Dict[str, Any]] = []

    def generate_response(self, **kwargs: Any) -> str:
        self.calls.append(kwargs)
        return json.dumps(self._responses.pop(0))

    def get_max_context_tokens(self) -> int:
        return 8192


def test_planner_runtime_grammar_does_not_leak_custom_keyword() -> None:
    llm = _StructuredLLM(
        [
            {
                "primary_tool": "finance_sum_counterparty_costs",
                "arguments": {"counterparty": "Example Grocer"},
                "rationale": "Direct deterministic counterparty aggregation.",
                "confidence": 0.9,
                "requires_follow_up": False,
                "follow_up_tool": None,
                "follow_up_arguments": {},
                "should_synthesize_from_tool_outputs": True,
            }
        ]
    )
    planner = FinanceQueryPlanner(llm, grammar_constrained=True)

    plan = planner.plan(
        question="Was habe ich bei Example Grocer ausgegeben?",
        schema_context={},
        available_tools=get_finance_tool_schemas(),
        reference_date=dt.date(2026, 7, 27),
    )

    assert plan.primary_tool == "finance_sum_counterparty_costs"
    assert "grammar_constraint" not in llm.calls[0]
    rendered_messages = json.dumps(llm.calls[0]["messages"], ensure_ascii=False)
    assert "counterparty" in rendered_messages
    assert "finance_sum_counterparty_costs" in rendered_messages


def test_reflector_runtime_grammar_does_not_leak_custom_keyword() -> None:
    llm = _StructuredLLM(
        [
            {
                "action": "done",
                "confidence": 0.95,
                "rationale": "The deterministic result answers the question.",
                "continuation_args": {},
            }
        ]
    )
    reflector = FinanceQueryReflector(llm, grammar_constrained=True)

    decision = reflector.decide(
        question="Was habe ich bei Example Grocer ausgegeben?",
        schema_context={},
        tool_trace=[{"name": "finance_sum_counterparty_costs", "ok": True}],
        recent_tool_outputs=[{"expense": 800.0, "currency": "CHF"}],
        available_tools=get_finance_tool_schemas(),
    )

    assert decision.action == "done"
    assert "grammar_constraint" not in llm.calls[0]