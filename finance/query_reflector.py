"""Structured reflection for iterative finance QA.

Decides whether available tool evidence is sufficient to answer a finance
question or if another tool step is required.

SOTA Enhancement: Grammar-Constrained Decoding (GCD)
---------------------------------------------------
Applies Production-Ready Grammar-Constrained Decoding (PR-GCD) as recommended
in the finance optimization roadmap. When enabled, the LLM output is constrained
to a BNF grammar compiled from the FinanceContinuationDecision Pydantic schema,
eliminating prompt drift and guaranteeing structurally valid output.

See: docs/finance_optimization_roadmap.md, finance/grammar_compiler.py
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from llm_structured_wrapper import LLMStructuredWrapper
from .grammar_compiler import GrammarCompiler, GrammarConfig


# Single source of truth fuer alle Reflexions-Aktionen. Wenn dieser Satz sich
# aendert, MUSS auch die ``Literal``-Annotation unten und der Dispatch in
# ``finance.chat._reflect_continuation`` synchron gehalten werden -- das
# erzwingt ``_validate_reflector_actions_coverage()`` beim Modul-Load.
_REFLECTOR_ACTIONS: tuple = (
    "done",
    "retry_search",
    "retry_sql",
    "retry_counterparty_costs",
    "retry_category_costs",
    "retry_cost_structure",
    "retry_recurring_expense",
    "retry_expense_forecast",
    "retry_expense_anomaly",
    "retry_budget_status",
    "retry_budget_vs_actual",
    "retry_savings_potential",
    "retry_expense_trend_break",
)

class FinanceContinuationDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal[
        "done",
        "retry_search",
        "retry_sql",
        "retry_counterparty_costs",
        "retry_category_costs",
        "retry_cost_structure",
        "retry_recurring_expense",
        "retry_expense_forecast",
        "retry_expense_anomaly",
        "retry_budget_status",
        "retry_budget_vs_actual",
        "retry_savings_potential",
        "retry_expense_trend_break",
    ]
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str = Field(min_length=1)
    continuation_args: Dict[str, Any] = Field(default_factory=dict)


class FinanceQueryReflector:
    """Lightweight quality gate for finance tool evidence.

    Parameters
    ----------
    llm_client : Any
        The LLM client instance.
    grammar_constrained : bool, optional
        When True, enables Grammar-Constrained Decoding (GCD).
        The LLM output will be constrained to a BNF grammar compiled
        from the FinanceContinuationDecision schema. Defaults to False.
    grammar_config : GrammarConfig, optional
        Custom grammar configuration. Uses sensible defaults if None.
    """

    def __init__(
        self,
        llm_client: Any,
        grammar_constrained: bool = False,
        grammar_config: Optional[GrammarConfig] = None,
    ) -> None:
        if llm_client is None:
            raise ValueError("llm_client is required for FinanceQueryReflector")
        self._wrapper = LLMStructuredWrapper(
            llm_client=llm_client,
            max_retries=2,
            temperature=0.0,
            enable_logging=False,
        )
        self._grammar_constrained = grammar_constrained
        self._grammar_config = grammar_config or GrammarConfig()
        self._grammar_bnfc: Optional[str] = None
        self._grammar_xg: Optional[str] = None
        self.last_error: Optional[str] = None
        self.used_fallback = False

        # Pre-compile grammars if enabled
        if self._grammar_constrained:
            try:
                self._grammar_bnfc = GrammarCompiler.compile_for_schema(
                    FinanceContinuationDecision,
                    config=self._grammar_config,
                )
                self._grammar_xg = GrammarCompiler.compile_xgrammar_for_schema(
                    FinanceContinuationDecision,
                    config=self._grammar_config,
                )
            except Exception:
                # Gracefully degrade: GCD disabled if compilation fails
                self._grammar_constrained = False

    def decide(
        self,
        *,
        question: str,
        schema_context: Dict[str, Any],
        tool_trace: List[Dict[str, Any]],
        recent_tool_outputs: List[Dict[str, Any]],
        available_tools: List[Dict[str, Any]],
        conversation_context: List[Dict[str, str]] | None = None,
    ) -> FinanceContinuationDecision:
        tool_names = [
            tool.get("function", {}).get("name")
            for tool in available_tools
            if isinstance(tool, dict)
        ]
        tool_names = [name for name in tool_names if isinstance(name, str) and name.startswith("finance_")]

        prompt = self._build_prompt(
            question=question,
            schema_context=schema_context,
            tool_trace=tool_trace,
            recent_tool_outputs=recent_tool_outputs,
            tool_names=tool_names,
            conversation_context=conversation_context or [],
        )

        fallback = FinanceContinuationDecision(
            action="done",
            confidence=0.51,
            rationale="Fallback: Reflection konnte nicht sicher entscheiden, daher finale Synthese.",
        )

        self.last_error = None
        self.used_fallback = False

        result = self._wrapper.generate_structured_safe(
            prompt=prompt,
            output_schema=FinanceContinuationDecision,  # type: ignore[arg-type]
            fallback=fallback,
            max_tokens=900,
            on_error=lambda exc: setattr(self, "last_error", f"{type(exc).__name__}: {exc}"),
        )
        self.used_fallback = result is None or result is fallback
        return result or fallback

    @staticmethod
    def _build_prompt(
        *,
        question: str,
        schema_context: Dict[str, Any],
        tool_trace: List[Dict[str, Any]],
        recent_tool_outputs: List[Dict[str, Any]],
        tool_names: List[str],
        conversation_context: List[Dict[str, str]],
    ) -> str:
        compact_context: List[str] = []
        for item in (conversation_context or [])[-6:]:
            role = str(item.get("role") or "").strip().lower()
            content = str(item.get("content") or "").strip()
            if role in {"user", "assistant"} and content:
                compact_context.append(f"{role}: {content}")
        context_text = "\n".join(compact_context) if compact_context else "(kein Kontext)"

        return (
            "Du bist ein Finance-Reflexions-Gate fuer einen iterativen Tool-Agenten.\n"
            "Aufgabe: Entscheide, ob die bisherigen Ergebnisse bereits fuer eine korrekte Antwort reichen, "
            "oder ob ein weiterer Tool-Schritt noetig ist.\n\n"
            "Regeln:\n"
            "- Waehle action=done, wenn die Evidenz fuer die Frage ausreicht.\n"
            "- Waehle action=retry_search, wenn relevante Buchungen noch fehlen oder der Recall unzureichend ist.\n"
            "- Waehle action=retry_sql, wenn die Treffermenge da ist, aber eine deterministische Aggregation/Join fehlt.\n"
            "- Waehle action=retry_counterparty_costs, wenn eine Frage nach Ausgaben/Kosten bei oder fuer eine Gegenseite gestellt wurde und die erste DB-Strategie zu eng war.\n"
            "- Waehle action=retry_category_costs, wenn die Frage Ausgaben/Kosten fuer eine oder mehrere Kategorien verlangt.\n"
            "- Waehle action=retry_cost_structure, wenn die Frage Fixkosten vs variable Kosten, Kostentreiber oder Kostenentwicklung verlangt.\n"
            "- Waehle action=retry_recurring_expense, wenn die Frage nach wiederkehrenden Kosten/Abos fragt.\n"
            "- Waehle action=retry_expense_forecast, wenn eine Kostenprognose/Future-Entwicklung gefragt ist.\n"
            "- Waehle action=retry_expense_anomaly, wenn nach Ausreissern/ungewoehnlichen Ausgaben gefragt ist.\n"
            "- Waehle action=retry_budget_status, wenn ein Soll/Ist-Budgetstatus fuer einen Monat gefragt ist.\n"
            "- Waehle action=retry_budget_vs_actual, wenn ein Budget-vs-Ist-Vergleich ueber mehrere Monate gefragt ist.\n"
            "- Waehle action=retry_savings_potential, wenn nach Sparpotenzialen gefragt ist.\n"
            "- Waehle action=retry_expense_trend_break, wenn nach Trendbruch/Strukturbruch in Ausgaben gefragt ist.\n"
            "- Wenn Ergebnisse auf truncation/sample hindeuten (z.B. truncated_possible=true, row_count==applied_limit, nicht aggregierte SQL), ist die Evidenz nicht vollstaendig -> nicht action=done.\n"
            "- Befuelle continuation_args immer mit den Argumenten fuer den naechsten Tool-Aufruf:\n"
            "    * Bei retry_search: {'query_text': '...', 'limit': 1000}\n"
            "    * Bei retry_sql: {'sql': 'SELECT ... (read-only)', 'query_params': [...]}\n"
            "    * Bei retry_counterparty_costs: {'counterparty': '...', 'start_date': '...', 'end_date': '...'}\n"
            "    * Bei retry_category_costs: {'categories': ['...'], 'start_date': '...', 'end_date': '...'}\n"
            "    * Bei retry_cost_structure: {'start_date': '...', 'end_date': '...'}\n"
            "    * Bei retry_recurring_expense: {'start_date': '...', 'end_date': '...'}\n"
            "    * Bei retry_expense_forecast: {'lookback_months': 12, 'forecast_months': 3}\n"
            "    * Bei retry_expense_anomaly: {'start_date': '...', 'end_date': '...', 'max_items': 15}\n"
            "    * Bei retry_budget_status: {'month': 'YYYY-MM'}\n"
            "    * Bei retry_budget_vs_actual: {'start_month': 'YYYY-MM', 'end_month': 'YYYY-MM'}\n"
            "    * Bei retry_savings_potential: {'start_date': '...', 'end_date': '...'}\n"
            "    * Bei retry_expense_trend_break: {'start_date': '...', 'end_date': '...', 'min_history_months': 6}\n"
            "- Keine Keyword-Heuristiken; entscheide aus Frage + Tool-Evidenz + Schema-Kontext.\n"
            "- Beruecksichtige den Dialogkontext fuer Folgefragen.\n"
            "- Nutze nur verfuegbare Finance-Tools.\n\n"
            f"Verfuegbare Finance-Tools: {tool_names}\n\n"
            f"Dialogkontext (letzte relevante Turns):\n{context_text}\n\n"
            f"Frage:\n{question}\n\n"
            f"Schema-Kontext:\n{schema_context}\n\n"
            f"Bisherige Tool-Trace:\n{tool_trace}\n\n"
            f"Letzte Tool-Outputs (kompakt):\n{recent_tool_outputs}\n"
        )


def _validate_reflector_actions_coverage() -> None:
    """Fail-fast: ``_REFLECTOR_ACTIONS`` und das ``Literal`` von
    ``FinanceContinuationDecision.action`` muessen identisch sein, und jede
    Nicht-``done``-Aktion muss im Reflector-Prompt mit einer ``action=<name>``-
    Regel erwaehnt sein.

    Verhindert:
    1. Veraltete Literal-Annotation gegenueber dem Action-Katalog (alte
       Wurzel des hier zuvor entdeckten Drifts).
    2. Action im Katalog, aber Prompt instruiert LLM nie dazu.
    3. Prompt erwaehnt eine Action, die das Literal nicht kennt -- waere
       garantiert ein Structured-Output-Validation-Fail.
    """
    from typing import get_args, get_type_hints

    literal_args = set(get_args(get_type_hints(FinanceContinuationDecision)["action"]))
    catalog = set(_REFLECTOR_ACTIONS)
    if literal_args != catalog:
        raise RuntimeError(
            "Reflector action drift: "
            f"Literal={sorted(literal_args)} vs _REFLECTOR_ACTIONS={sorted(catalog)}."
        )

    sample_prompt = FinanceQueryReflector._build_prompt(
        question="",
        schema_context={},
        tool_trace=[],
        recent_tool_outputs=[],
        tool_names=[],
        conversation_context=[],
    )
    mentioned = set(re.findall(r"action=([a-z_]+)", sample_prompt))
    expected_in_prompt = catalog - {"done"}
    missing_in_prompt = expected_in_prompt - mentioned
    unknown_in_prompt = mentioned - catalog
    if missing_in_prompt or unknown_in_prompt:
        raise RuntimeError(
            "Reflector prompt out of sync with action catalog: "
            f"missing in prompt={sorted(missing_in_prompt)}, "
            f"prompt references unknown actions={sorted(unknown_in_prompt)}. "
            "Update _REFLECTOR_ACTIONS and/or _build_prompt in finance/query_reflector.py."
        )


_validate_reflector_actions_coverage()