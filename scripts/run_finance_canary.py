#!/usr/bin/env python3
"""Run a strict local Gemma4 Finance canary against synthetic data."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ["APP_LOCAL_ONLY"] = "1"

from agent.tool_schemas import get_finance_tool_schemas
from finance.chat import FinanceChatEngine
from finance.db_schema import FinanceDB
from finance.grammar_compiler import GrammarCompiler
from finance.query_planner import FinanceQueryPlan, FinanceQueryPlanner
from finance.query_reflector import FinanceContinuationDecision, FinanceQueryReflector
from finance.tools import FinanceTools
from scripts.model_loader import DEFAULT_MODEL, ModelLoader


class _FinanceToolkit:
    def __init__(self, tools: FinanceTools) -> None:
        self._tools = tools
        self.calls: List[Dict[str, Any]] = []

    def execute_tool(self, name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        if not name.startswith("finance_"):
            result = {
                "success": False,
                "error": f"Tool not allowed in Finance canary: {name}",
                "error_class": "forbidden_tool",
            }
        else:
            method = getattr(self._tools, name.removeprefix("finance_"), None)
            if not callable(method):
                result = {
                    "success": False,
                    "error": f"Finance tool is not implemented: {name}",
                    "error_class": "missing_implementation",
                }
            else:
                result = method(arguments)
        self.calls.append({"name": name, "arguments": arguments, "result": result})
        return result


def _seed_database(db_path: Path) -> FinanceDB:
    db = FinanceDB(str(db_path))
    transactions: List[Dict[str, Any]] = []
    grocery_amounts = [-200.0, -220.0, -210.0, -205.0, -215.0, -800.0, -225.0, -230.0]
    for month, grocery_amount in enumerate(grocery_amounts, start=1):
        transactions.extend(
            [
                {
                    "booking_date": f"2026-{month:02d}-01",
                    "amount": 3000.0,
                    "currency": "CHF",
                    "counterparty": "Example Employer",
                    "purpose": "Salary",
                },
                {
                    "booking_date": f"2026-{month:02d}-03",
                    "amount": -1000.0,
                    "currency": "CHF",
                    "counterparty": "Example Landlord",
                    "purpose": "Monthly rent",
                },
                {
                    "booking_date": f"2026-{month:02d}-10",
                    "amount": grocery_amount,
                    "currency": "CHF",
                    "counterparty": "Example Grocer",
                    "purpose": "Groceries",
                },
            ]
        )
    transactions.append(
        {
            "booking_date": "2026-03-15",
            "amount": 20.0,
            "currency": "CHF",
            "counterparty": "Example Grocer",
            "purpose": "Groceries refund",
        }
    )
    _, account_id, _, inserted, duplicates = db.persist_statement_import(
        bank_name="Synthetic Bank",
        bank_bic=None,
        bank_country_code="CH",
        iban="CH9300762011623852957",
        account_holder="Canary User",
        currency="CHF",
        account_type="checking",
        source_pdf_hash="finance-canary-fixture",
        source_filename="finance-canary-fixture.pdf",
        period_start="2026-01-01",
        period_end="2026-08-31",
        opening_balance=0.0,
        closing_balance=0.0,
        transactions=transactions,
    )
    if inserted != len(transactions) or duplicates:
        raise RuntimeError("Synthetic Finance fixture was not inserted cleanly")

    categories = {
        "Housing": db.upsert_category(name="Housing", kind="expense"),
        "Groceries": db.upsert_category(name="Groceries", kind="expense"),
        "Income": db.upsert_category(name="Income", kind="income"),
    }
    for transaction in db.query_transactions(account_id=account_id, limit=100):
        if transaction.counterparty == "Example Landlord":
            category = "Housing"
        elif transaction.counterparty == "Example Grocer":
            category = "Groceries"
        else:
            category = "Income"
        db.assign_category(transaction.id, categories[category])
    db.upsert_budget(
        category_id=categories["Groceries"],
        month="2026-01",
        budget_cents=25_000,
    )
    return db


def _close_to(expected: float, *, tolerance: float = 0.01) -> Callable[[Dict[str, Any]], bool]:
    return lambda result: abs(float(result.get("expense", -1)) - expected) <= tolerance


def _planner_cases() -> List[Dict[str, Any]]:
    return [
        {
            "case_id": "counterparty-june",
            "question": "Wie viel habe ich im Juni 2026 bei Example Grocer ausgegeben?",
            "expected_tool": "finance_sum_counterparty_costs",
            "validate": _close_to(800.0),
        },
        {
            "case_id": "category-total",
            "question": "Wie viel habe ich insgesamt in der Kategorie Groceries ausgegeben?",
            "expected_tool": "finance_sum_category_costs",
            "validate": _close_to(2305.0),
        },
        {
            "case_id": "expense-forecast",
            "question": "Prognostiziere meine Ausgaben fuer die naechsten zwei Monate aus den letzten drei Monaten.",
            "expected_tool": "finance_expense_forecast",
            "validate": lambda result: len(result.get("forecast") or []) == 2,
        },
        {
            "case_id": "budget-comparison",
            "question": "Vergleiche mein Budget mit den Ist-Ausgaben fuer Januar 2026.",
            "expected_tool": "finance_budget_vs_actual_analysis",
            "validate": lambda result: any(
                row.get("category") == "Groceries"
                and abs(float(row.get("actual", -1)) - 200.0) <= 0.01
                for row in result.get("categories") or []
            ),
        },
    ]


def _compile_grammars() -> Dict[str, Any]:
    planner = GrammarCompiler.compile_for_schema(FinanceQueryPlan)
    reflector = GrammarCompiler.compile_for_schema(FinanceContinuationDecision)
    return {
        "planner_compiled": bool(planner.strip()),
        "reflector_compiled": bool(reflector.strip()),
        "planner_chars": len(planner),
        "reflector_chars": len(reflector),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run strict local Finance canary")
    parser.add_argument("--model-id", default=DEFAULT_MODEL)
    parser.add_argument("--out", default="")
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()

    model_loader = ModelLoader()
    if not model_loader.load_model_by_config(args.model_id):
        raise RuntimeError(f"Model could not be loaded: {args.model_id}")
    loaded_model = model_loader.get_current_model_id()
    if loaded_model != args.model_id:
        raise RuntimeError(
            f"Model load drift: requested={args.model_id}, loaded={loaded_model}"
        )

    grammar = _compile_grammars()
    schemas = get_finance_tool_schemas(include_code_executor=False)
    gate_failures: List[str] = []
    planner_results: List[Dict[str, Any]] = []

    with tempfile.TemporaryDirectory(prefix="finance_canary_") as temp_dir:
        db = _seed_database(Path(temp_dir) / "finance_canary.db")
        tools = FinanceTools(db=db)
        toolkit = _FinanceToolkit(tools)
        schema_context = tools.get_schema_context(
            {"max_tables": 40, "include_relationships": True}
        )
        planner = FinanceQueryPlanner(model_loader, grammar_constrained=True)

        for case in _planner_cases():
            plan = planner.plan(
                question=case["question"],
                schema_context=schema_context,
                available_tools=schemas,
                reference_date=date(2026, 8, 31),
            )
            result = toolkit.execute_tool(plan.primary_tool, plan.arguments)
            tool_ok = plan.primary_tool == case["expected_tool"]
            result_ok = bool(result.get("success")) and bool(case["validate"](result))
            passed = tool_ok and result_ok
            if not passed:
                gate_failures.append(case["case_id"])
            planner_results.append(
                {
                    "case_id": case["case_id"],
                    "expected_tool": case["expected_tool"],
                    "actual_tool": plan.primary_tool,
                    "arguments": plan.arguments,
                    "confidence": plan.confidence,
                    "used_fallback": planner.used_fallback,
                    "planner_error": planner.last_error,
                    "tool_ok": tool_ok,
                    "result_ok": result_ok,
                    "passed": passed,
                }
            )

        reflector = FinanceQueryReflector(model_loader, grammar_constrained=True)
        reflection = reflector.decide(
            question="Wie viel habe ich im Juni 2026 bei Example Grocer ausgegeben?",
            schema_context=schema_context,
            tool_trace=[{"name": "finance_sum_counterparty_costs", "ok": True}],
            recent_tool_outputs=[
                {
                    "tool": "finance_sum_counterparty_costs",
                    "output": {"success": True, "expense": 800.0, "currency": "CHF"},
                }
            ],
            available_tools=schemas,
        )
        reflector_passed = reflection.action == "done"
        if not reflector_passed:
            gate_failures.append("reflector-done")

        toolkit.calls.clear()
        engine = FinanceChatEngine(
            llm_client=model_loader,
            toolkit=toolkit,
            max_tokens=1024,
            temperature=0.0,
            allow_python=False,
        )
        chat_result = engine.respond(
            "Wie viel habe ich im Juni 2026 bei Example Grocer ausgegeben?"
        )
        executed_tools = [call["name"] for call in toolkit.calls]
        full_chat_passed = (
            bool(chat_result.answer.strip())
            and not chat_result.trace.rejected_tools
            and bool(chat_result.trace.tool_calls)
            and all(call.get("ok") for call in chat_result.trace.tool_calls)
            and "finance_sum_counterparty_costs" in executed_tools
        )
        if not full_chat_passed:
            gate_failures.append("full-chat")

    if not grammar["planner_compiled"] or not grammar["reflector_compiled"]:
        gate_failures.append("grammar-compilation")

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "model_id": loaded_model,
        "local_only": os.environ.get("APP_LOCAL_ONLY") == "1",
        "synthetic_database": True,
        "grammar": grammar,
        "planner_results": planner_results,
        "reflector": {
            "action": reflection.action,
            "confidence": reflection.confidence,
            "used_fallback": reflector.used_fallback,
            "reflector_error": reflector.last_error,
            "passed": reflector_passed,
        },
        "full_chat": {
            "passed": full_chat_passed,
            "answer": chat_result.answer,
            "executed_tools": executed_tools,
            "trace": chat_result.trace.tool_calls,
            "rejected_tools": chat_result.trace.rejected_tools,
        },
        "gate_failures": gate_failures,
        "gate_passed": not gate_failures,
    }
    output_path = Path(args.out) if args.out else (
        Path("monitoring")
        / "finance"
        / f"finance_canary_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "model_id": loaded_model,
                "gate_passed": report["gate_passed"],
                "gate_failures": gate_failures,
                "planner_passed": sum(item["passed"] for item in planner_results),
                "planner_total": len(planner_results),
                "reflector_passed": reflector_passed,
                "full_chat_passed": full_chat_passed,
                "output": str(output_path),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    if args.strict and gate_failures:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())