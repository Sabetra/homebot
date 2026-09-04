from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from finance.chat import FinanceChatEngine, FinanceChatTrace


class _DummyPlanner:
    def plan(self, **_kwargs):
        return None


class _DummyToolkit:
    def execute_tool(self, *_args, **_kwargs):
        return {"success": True}


class _DummyLLM:
    def generate_with_tools(self, **_kwargs):
        return {"content": "", "finish_reason": "stop", "tool_calls": []}


@dataclass
class _Decision:
    action: str
    continuation_args: Optional[Dict[str, Any]] = None


class _StubReflector:
    def __init__(self, decision: _Decision):
        self._decision = decision

    def decide(self, **_kwargs):
        return self._decision


class _TestableFinanceChatEngine(FinanceChatEngine):
    def __init__(self, decision: _Decision) -> None:
        super().__init__(_DummyLLM(), _DummyToolkit(), max_tokens=64, allow_python=False)
        self._decision = decision

    def _get_planner(self):
        return _DummyPlanner()

    def _load_schema_context(self):
        return {}

    def _get_reflector(self):
        return _StubReflector(self._decision)


def _base_messages() -> List[Dict[str, Any]]:
    return [
        {"role": "system", "content": "finance system"},
        {"role": "user", "content": "Was habe ich bei Rewe ausgegeben?"},
    ]


def test_reflector_retry_search_fills_missing_query_text_and_defaults() -> None:
    engine = _TestableFinanceChatEngine(
        _Decision(action="retry_search", continuation_args={"query_text": "   "})
    )

    call = engine._reflect_continuation(
        user_message="Ausgaben im Supermarkt",
        schema_context={},
        messages=_base_messages(),
        trace=FinanceChatTrace(),
    )

    assert call is not None
    assert call["tool_name"] == "finance_search_transactions"
    assert call["args"]["query_text"] == "Ausgaben im Supermarkt"
    assert call["args"]["limit"] == 500
    assert call["args"]["include_transfers"] is False


def test_reflector_retry_sql_normalizes_sql_query_field() -> None:
    engine = _TestableFinanceChatEngine(
        _Decision(action="retry_sql", continuation_args={"sql_query": "SELECT 1"})
    )

    call = engine._reflect_continuation(
        user_message="irrelevant",
        schema_context={},
        messages=_base_messages(),
        trace=FinanceChatTrace(),
    )

    assert call is not None
    assert call["tool_name"] == "finance_sql_query"
    assert call["args"]["sql"] == "SELECT 1"


def test_reflector_retry_counterparty_uses_user_message_when_missing() -> None:
    engine = _TestableFinanceChatEngine(
        _Decision(action="retry_counterparty_costs", continuation_args={"counterparty": "  "})
    )

    call = engine._reflect_continuation(
        user_message="Netflix",
        schema_context={},
        messages=_base_messages(),
        trace=FinanceChatTrace(),
    )

    assert call is not None
    assert call["tool_name"] == "finance_sum_counterparty_costs"
    assert call["args"]["counterparty"] == "Netflix"


def test_reflector_unknown_action_is_safely_ignored() -> None:
    engine = _TestableFinanceChatEngine(
        _Decision(action="retry_unknown_action", continuation_args={"x": 1})
    )

    call = engine._reflect_continuation(
        user_message="foo",
        schema_context={},
        messages=_base_messages(),
        trace=FinanceChatTrace(),
    )

    assert call is None
