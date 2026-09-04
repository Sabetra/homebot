"""Finance-only chat loop with strict tool grounding.

The engine is intentionally isolated from generic RAG/web paths. It only
permits finance tools (and optional code_executor) and always tries to
produce a final grounded synthesis from tool outputs.
"""

from __future__ import annotations

import ast
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from agent.tool_schemas import get_finance_tool_schemas
from finance.query_planner import FinanceQueryPlanner
from finance.query_reflector import FinanceQueryReflector
from utils.followup_question_extractor import extract_followup_questions

logger = logging.getLogger(__name__)


_FINANCE_SYSTEM_PROMPT = (
    "Du bist ein praeziser Finanz-Assistent fuer einen privaten Nutzer. "
    "Du arbeitest ausschliesslich mit den Daten in der Finanz-DB des Nutzers.\n\n"
    "Regeln:\n"
    "- Erfinde keine Zahlen; nutze ausschliesslich Tool-Ergebnisse.\n"
    "- Fuer freie Cluster + Summenfragen nutze deterministische Aggregation.\n"
    "- Nutze finance_get_schema_context, wenn DB-Struktur unklar ist.\n"
    "- Nutze finance_sql_query fuer read-only SQL-Analysen (SELECT/WITH).\n"
    "- Nutze finance_search_transactions fuer Recall/Exploration von Buchungen.\n"
    "- Nutze finance_sum_counterparty_costs fuer Fragen nach Ausgaben/Kosten bei "
    "oder fuer eine konkrete Gegenseite oder Marke.\n"
    "- Nutze finance_sum_category_costs fuer Ausgaben/Kosten zu einer oder "
    "mehreren Kategorien.\n"
    "- Nutze finance_cost_structure_analysis fuer Fixkosten vs variable Kosten, "
    "Kostentreiber und Kostenentwicklung.\n"
    "- Nutze finance_recurring_expense_analysis fuer wiederkehrende Kosten/Abos/"
    "regelmaessige Belastungen.\n"
    "- Nutze finance_expense_forecast fuer Prognosen der kommenden "
    "Ausgabenmonate.\n"
    "- Nutze finance_expense_anomaly_detection fuer Ausreisser/ungewoehnliche "
    "Ausgaben.\n"
    "- Nutze finance_budget_status fuer Soll/Ist-Budgetstatus in einem Monat.\n"
    "- Nutze finance_budget_vs_actual_analysis fuer Budget-vs-Ist ueber mehrere "
    "Monate.\n"
    "- Nutze finance_savings_potential_analysis fuer priorisierte "
    "Sparpotenzial-Analysen.\n"
    "- Nutze finance_expense_trend_break_detection fuer strukturelle "
    "Trendbrueche in Ausgaben.\n"
    "- Wenn der Nutzer eine tabellarische Auflistung einzelner Buchungen "
    "verlangt, nutze finance_query_transactions als Datenquelle fuer die Tabelle.\n"
    "- Wenn eine Frage nach Ausgaben/Kosten/Summen fragt, gib den Betrag als "
    "positive Ausgabensumme an, auch wenn die DB negative Cashflow-Werte "
    "speichert.\n"
    "- Wenn Daten fehlen, sage das klar.\n"
    "- Keine externen Quellen, kein RAG, kein Internet.\n"
    "- Wenn der Nutzer eine tabellarische Auflistung verlangt, liefere eine "
    "kompakte Markdown-Tabelle aus den Tool-Daten.\n"
    "- Antworte knapp und sachlich."
)

_PYTHON_TOOL_NAME = "code_executor"

# Single source of truth fuer die Tools, die das System-Prompt aktiv an das
# LLM routet. Andere ``finance_*``-Tools (Admin/Setup/Listing) liegen ebenfalls
# im Schema-Katalog, werden aber implizit ueber ihre Schema-Beschreibung
# gewaehlt -- ohne explizite Prompt-Regel. Wenn ein neuer Routing-Tool
# hinzugefuegt wird, muss er sowohl hier als auch im Prompt erscheinen, sonst
# schlaegt ``_validate_finance_prompt_coverage`` beim Modul-Load fehl.
_FINANCE_CHAT_ROUTED_TOOLS: frozenset[str] = frozenset({
    "finance_get_schema_context",
    "finance_sql_query",
    "finance_search_transactions",
    "finance_sum_counterparty_costs",
    "finance_sum_category_costs",
    "finance_cost_structure_analysis",
    "finance_recurring_expense_analysis",
    "finance_expense_forecast",
    "finance_expense_anomaly_detection",
    "finance_budget_status",
    "finance_budget_vs_actual_analysis",
    "finance_savings_potential_analysis",
    "finance_expense_trend_break_detection",
    "finance_query_transactions",
})

# Mapping fuer alle Reflector-Continuation-Actions, deren Dispatch keine
# spezielle Argument-Massage benoetigt (im Gegensatz zu ``retry_search``,
# ``retry_sql``, ``retry_counterparty_costs``, die unten inline behandelt
# werden). Single source of truth fuer den Action->Tool-Mapping der
# einfachen Retry-Pfade; ``_validate_reflector_dispatch_coverage`` stellt
# sicher, dass jeder im Reflector deklarierte Action-Catalog-Eintrag auch
# tatsaechlich dispatchet wird.
_SIMPLE_RETRY_DISPATCH: Dict[str, str] = {
    "retry_category_costs": "finance_sum_category_costs",
    "retry_cost_structure": "finance_cost_structure_analysis",
    "retry_recurring_expense": "finance_recurring_expense_analysis",
    "retry_expense_forecast": "finance_expense_forecast",
    "retry_expense_anomaly": "finance_expense_anomaly_detection",
    "retry_budget_status": "finance_budget_status",
    "retry_budget_vs_actual": "finance_budget_vs_actual_analysis",
    "retry_savings_potential": "finance_savings_potential_analysis",
    "retry_expense_trend_break": "finance_expense_trend_break_detection",
}
_INLINE_RETRY_DISPATCH: frozenset[str] = frozenset({
    "retry_search",
    "retry_sql",
    "retry_counterparty_costs",
})

# Zweites verstecktes System-Prompt fuer die finale Synthese (mode="none").
# Bewusst als Modul-Konstante, damit der Inventory-Generator und der Coverage-
# Validator das Prompt introspektieren koennen statt es im Methodenrumpf zu
# verstecken.
_FINANCE_FINAL_SYNTHESIS_PROMPT = (
    "Erzeuge jetzt eine finale Antwort NUR aus den vorhandenen "
    "Tool-Ergebnissen. Keine erfundenen Daten, kein Python-Code, "
    "kein SQL im Antworttext. "
    "Wenn finance_sql_query-Ergebnisse vorhanden sind, nutze sie "
    "als autoritative Basis fuer Aggregatwerte. "
    "Vorzeichen-Regel (gilt fuer ALLE Ausgaben/Kosten): Formuliere "
    "Ausgabebetraege immer positiv, sowohl bei Gesamtsummen als "
    "auch bei einzelnen Buchungsbetraegen in Tabellen. Zeige also "
    "in einer Buchungstabelle den Betrag '87,10' statt '-87,10', "
    "wenn es sich um eine Ausgabe handelt. Gutschriften und "
    "Einnahmen behalten ihr positives Vorzeichen. "
    "Wenn finance_query_transactions-Ergebnisse vorhanden sind, "
    "behandle sie als vollstaendige Buchungsliste fuer die "
    "angefragten Filter und beschreibe sie nicht als Stichprobe. "
    "Wenn die Frage explizit nach den N groessten Kostentreibern "
    "oder Top-N-Positionen fragt, gib am Ende der Antwort ein "
    "explizites nummeriertes Ranking aus -- nicht nur eine rohe "
    "Tabelle. "
    "Kein weiterer Tool-Call. "
    "Wenn ein Tool-Ergebnis truncated_possible=true signalisiert, "
    "behandle es als unvollstaendige Stichprobe und extrapoliere "
    "nicht auf den Gesamtbestand."
)


def _validate_finance_prompt_coverage() -> None:
    """Fail-fast: jeder als ``chat-routed`` deklarierte Tool muss im
    System-Prompt erwaehnt sein und umgekehrt, und alle muessen tatsaechlich
    als Schema existieren.

    Verhindert drei Drift-Szenarien:

    1. Neu hinzugefuegter Routing-Tool ohne Prompt-Update (LLM kennt das Tool
       zwar aus dem Schema, aber das Prompt routet nie dorthin).
    2. Veralteter Prompt referenziert ein entferntes/umbenanntes Tool.
    3. Liste der Routing-Tools enthaelt einen Tool-Namen, der gar nicht im
       Schema-Katalog existiert.

    Single Source of Truth fuer den Routing-Katalog ist
    ``_FINANCE_CHAT_ROUTED_TOOLS``; weitere ``finance_*``-Tools (Admin-,
    Setup-, Listing-Tools) existieren im Schema-Katalog und werden ueber
    Schema-Beschreibungen geroutet, ohne explizite Prompt-Regel.
    """
    schema_names = {
        s["function"]["name"]
        for s in get_finance_tool_schemas(include_code_executor=False)
    }
    mentioned_finance = set(re.findall(r"\bfinance_[a-z_]+", _FINANCE_SYSTEM_PROMPT))

    missing_in_prompt = _FINANCE_CHAT_ROUTED_TOOLS - mentioned_finance
    stale_in_prompt = mentioned_finance - _FINANCE_CHAT_ROUTED_TOOLS
    missing_in_schemas = _FINANCE_CHAT_ROUTED_TOOLS - schema_names

    if missing_in_prompt or stale_in_prompt or missing_in_schemas:
        raise RuntimeError(
            "Finance system prompt out of sync with tool catalog: "
            f"chat-routed tools missing from prompt={sorted(missing_in_prompt)}, "
            f"prompt references non-routed/unknown tools={sorted(stale_in_prompt)}, "
            f"routed tools missing from schemas={sorted(missing_in_schemas)}. "
            "Update _FINANCE_SYSTEM_PROMPT and/or _FINANCE_CHAT_ROUTED_TOOLS "
            "in finance/chat.py."
        )


def _validate_reflector_dispatch_coverage() -> None:
    """Fail-fast: jede Reflector-Continuation-Action MUSS entweder im
    Inline-Dispatch oder im ``_SIMPLE_RETRY_DISPATCH`` behandelt werden,
    und jeder Eintrag im ``_SIMPLE_RETRY_DISPATCH`` MUSS auf einen real
    existierenden Tool-Schemanamen verweisen.

    Verhindert die zuvor entdeckte Layer-2-Drift (Literal mit nur 4
    Actions, Prompt mit 13, Dispatch mit 3).
    """
    from finance.query_reflector import _REFLECTOR_ACTIONS

    catalog = set(_REFLECTOR_ACTIONS)
    handled = _INLINE_RETRY_DISPATCH | set(_SIMPLE_RETRY_DISPATCH) | {"done"}
    missing = catalog - handled
    extra = handled - catalog
    if missing or extra:
        raise RuntimeError(
            "Reflector dispatch out of sync with action catalog: "
            f"missing dispatch for actions={sorted(missing)}, "
            f"dispatch references unknown actions={sorted(extra)}. "
            "Update _SIMPLE_RETRY_DISPATCH / _INLINE_RETRY_DISPATCH in finance/chat.py."
        )

    schema_names = {
        s["function"]["name"]
        for s in get_finance_tool_schemas(include_code_executor=False)
    }
    unknown_tools = {
        tool for tool in _SIMPLE_RETRY_DISPATCH.values() if tool not in schema_names
    }
    if unknown_tools:
        raise RuntimeError(
            "Reflector dispatch references finance tools that do not exist "
            f"in the schema catalog: {sorted(unknown_tools)}."
        )


_validate_finance_prompt_coverage()
_validate_reflector_dispatch_coverage()


@dataclass
class FinanceChatTrace:
    iterations: int = 0
    tool_calls: List[Dict[str, Any]] = field(default_factory=list)
    rejected_tools: List[str] = field(default_factory=list)
    finish_reason: str = "stop"


@dataclass
class FinanceChatResult:
    answer: str
    messages: List[Dict[str, Any]]
    trace: FinanceChatTrace
    followup_questions: List[str] = field(default_factory=list)


class FinanceChatEngine:
    MAX_ITERATIONS = 6
    MAX_TOOL_RESULT_CHARS = 6000

    def __init__(
        self,
        llm_client: Any,
        toolkit: Any,
        *,
        max_tokens: int = 2048,
        temperature: float = 0.2,
        allow_python: bool = True,
    ) -> None:
        if llm_client is None:
            raise ValueError("llm_client is required")
        if toolkit is None:
            raise ValueError("toolkit is required")
        self._llm = llm_client
        self._toolkit = toolkit
        self._max_tokens = max_tokens
        self._temperature = temperature
        self._allow_python = bool(allow_python)
        self._tools = get_finance_tool_schemas(include_code_executor=self._allow_python)
        if not self._tools:
            raise RuntimeError("Keine finance_*-Tool-Schemas gefunden")
        self._tool_names = {
            tool.get("function", {}).get("name")
            for tool in self._tools
            if isinstance(tool, dict)
        }
        self._planner: Optional[FinanceQueryPlanner] = None
        self._reflector: Optional[FinanceQueryReflector] = None

    def respond(
        self,
        user_message: str,
        history: Optional[List[Dict[str, Any]]] = None,
    ) -> FinanceChatResult:
        if not user_message or not user_message.strip():
            return FinanceChatResult(
                answer="(leere Frage)",
                messages=[],
                trace=FinanceChatTrace(finish_reason="empty_input"),
            )

        messages: List[Dict[str, Any]] = [{"role": "system", "content": _FINANCE_SYSTEM_PROMPT}]
        if history:
            messages.extend(self._sanitize_history(history))
        messages.append({"role": "user", "content": user_message.strip()})

        trace = FinanceChatTrace()
        last_text = ""
        reflection_retries: set[str] = set()
        schema_context = self._load_schema_context()
        planner = self._get_planner()
        initial_plan = planner.plan(
            question=user_message.strip(),
            schema_context=schema_context,
            available_tools=self._tools,
        )
        if initial_plan and self._is_allowed_tool(initial_plan.primary_tool):
            self._append_planned_tool_call(
                tool_name=initial_plan.primary_tool,
                arguments=initial_plan.arguments,
                messages=messages,
                trace=trace,
                iteration=0,
                user_message=user_message,
            )
            if initial_plan.requires_follow_up and initial_plan.follow_up_tool:
                self._append_planned_tool_call(
                    tool_name=initial_plan.follow_up_tool,
                    arguments=initial_plan.follow_up_arguments,
                    messages=messages,
                    trace=trace,
                    iteration=0,
                    user_message=user_message,
                )

        if self._should_finalize_after_tool_calls(trace):
            last_text = self._final_synthesis(messages=messages, trace=trace)
            if last_text:
                trace.finish_reason = "planned_tool_complete"

        for it in range(0 if last_text else self.MAX_ITERATIONS):
            trace.iterations = it + 1
            response = self._generate_with_tools(messages=messages, mode="auto")
            tool_calls = response.get("tool_calls") or []
            content = response.get("content") or ""
            trace.finish_reason = response.get("finish_reason", "stop")
            last_text = content

            if not tool_calls:
                recovered_calls = self._recover_tool_calls_from_text(content)
                if recovered_calls:
                    self._append_and_dispatch_recovered_calls(
                        recovered_calls=recovered_calls,
                        messages=messages,
                        trace=trace,
                        iteration=it,
                        assistant_content=content,
                        user_message=user_message,
                    )
                    continue

                if trace.tool_calls:
                    reflection = self._reflect_continuation(
                        user_message=user_message,
                        schema_context=schema_context,
                        messages=messages,
                        trace=trace,
                    )
                    if reflection and reflection.get("tool_name") and reflection.get("args"):
                        signature = self._tool_signature(
                            name=reflection["tool_name"],
                            args=reflection["args"],
                        )
                        if signature not in reflection_retries:
                            reflection_retries.add(signature)
                            self._append_planned_tool_call(
                                tool_name=reflection["tool_name"],
                                arguments=reflection["args"],
                                messages=messages,
                                trace=trace,
                                iteration=it,
                                user_message=user_message,
                            )
                            continue

                    final_text = self._final_synthesis(messages=messages, trace=trace)
                    if final_text:
                        last_text = final_text
                        break

                last_text = self._strip_pseudo_tool_text(content)
                break

            messages.append({"role": "assistant", "content": content, "tool_calls": tool_calls})
            for tc in tool_calls:
                self._dispatch_single_tool_call(
                    tc=tc,
                    messages=messages,
                    trace=trace,
                    iteration=it,
                    user_message=user_message,
                )

            if self._should_finalize_after_tool_calls(trace):
                final_text = self._final_synthesis(messages=messages, trace=trace)
                if final_text:
                    last_text = final_text
                break

        if not (last_text or "").strip() and trace.tool_calls:
            final_text = self._final_synthesis(messages=messages, trace=trace)
            if final_text:
                last_text = final_text

        if not (last_text or "").strip() and trace.tool_calls:
            success_count = sum(1 for tc in trace.tool_calls if tc.get("ok"))
            last_text = (
                "Ich konnte die Auswertung nicht sauber in Text finalisieren, "
                f"obwohl {success_count} Tool-Aufruf(e) erfolgreich waren."
            )

        cleaned_answer, followup_questions = extract_followup_questions(last_text.strip())

        return FinanceChatResult(
            answer=cleaned_answer.strip() or "(Keine Antwort erzeugt.)",
            messages=messages,
            trace=trace,
            followup_questions=followup_questions,
        )

    def _get_planner(self) -> FinanceQueryPlanner:
        if self._planner is None:
            self._planner = FinanceQueryPlanner(self._llm)
        return self._planner

    def _get_reflector(self) -> FinanceQueryReflector:
        if self._reflector is None:
            self._reflector = FinanceQueryReflector(self._llm)
        return self._reflector

    def _reflect_continuation(
        self,
        *,
        user_message: str,
        schema_context: Dict[str, Any],
        messages: List[Dict[str, Any]],
        trace: FinanceChatTrace,
    ) -> Optional[Dict[str, Any]]:
        reflector = self._get_reflector()
        decision = reflector.decide(
            question=user_message,
            schema_context=schema_context,
            tool_trace=trace.tool_calls,
            recent_tool_outputs=self._summarize_recent_tool_outputs(messages=messages),
            available_tools=self._tools,
            conversation_context=self._extract_conversation_context(messages=messages),
        )
        action = decision.action
        if action == "done":
            return None
        if action == "retry_search":
            args = self._ensure_search_args(args=decision.continuation_args, user_message=user_message)
            return {"tool_name": "finance_search_transactions", "args": args}
        if action == "retry_sql" and isinstance(decision.continuation_args, dict):
            args = self._normalize_sql_args(decision.continuation_args)
            if isinstance(args.get("sql"), str) and args.get("sql", "").strip():
                return {"tool_name": "finance_sql_query", "args": args}
            return None
        if action == "retry_counterparty_costs":
            args = dict(decision.continuation_args or {})
            counterparty = args.get("counterparty")
            if not isinstance(counterparty, str) or not counterparty.strip():
                counterparty = user_message.strip()
            args["counterparty"] = counterparty
            return {"tool_name": "finance_sum_counterparty_costs", "args": args}
        tool_name = _SIMPLE_RETRY_DISPATCH.get(action)
        if tool_name is not None:
            return {"tool_name": tool_name, "args": dict(decision.continuation_args or {})}
        # Sollte durch _validate_reflector_dispatch_coverage() unmoeglich sein.
        logger.error("Reflector returned unhandled action %r", action)
        return None

    @staticmethod
    def _extract_conversation_context(
        *,
        messages: List[Dict[str, Any]],
        max_turns: int = 6,
    ) -> List[Dict[str, str]]:
        """Letzte ``max_turns`` user/assistant-Turns als dialog-context."""
        slim: List[Dict[str, str]] = []
        for msg in messages:
            role = msg.get("role")
            if role not in {"user", "assistant"}:
                continue
            content = msg.get("content")
            if not isinstance(content, str) or not content.strip():
                continue
            slim.append({"role": role, "content": content.strip()})
        return slim[-max_turns:]

    def _should_finalize_after_tool_calls(self, trace: FinanceChatTrace) -> bool:
        if not trace.tool_calls:
            return False
        last_call = trace.tool_calls[-1]
        return bool(last_call.get("ok")) and last_call.get("name") in {
            "finance_aggregate",
            "finance_sum_counterparty_costs",
        }

    @staticmethod
    def _summarize_recent_tool_outputs(
        *,
        messages: List[Dict[str, Any]],
        max_items: int = 4,
        max_chars: int = 1200,
    ) -> List[Dict[str, Any]]:
        summaries: List[Dict[str, Any]] = []
        tool_name_by_id: Dict[str, str] = {}
        for msg in messages:
            if msg.get("role") != "assistant":
                continue
            for tc in msg.get("tool_calls") or []:
                if not isinstance(tc, dict):
                    continue
                tc_id = tc.get("id")
                fn = tc.get("function") or {}
                name = fn.get("name")
                if isinstance(tc_id, str) and isinstance(name, str):
                    tool_name_by_id[tc_id] = name

        for msg in reversed(messages):
            if msg.get("role") != "tool":
                continue
            content = msg.get("content")
            if not isinstance(content, str) or not content.strip():
                continue
            compact: Any = content[:max_chars]
            try:
                parsed = json.loads(content)
                if isinstance(parsed, dict):
                    keep = {
                        k: parsed.get(k)
                        for k in ("success", "count", "row_count", "error", "error_class", "query_text")
                        if k in parsed
                    }
                    if isinstance(parsed.get("matches"), list):
                        keep["matches_count"] = len(parsed.get("matches") or [])
                    if isinstance(parsed.get("rows"), list):
                        keep["rows_count"] = len(parsed.get("rows") or [])
                    compact = keep or parsed
            except Exception:
                pass

            tool_call_id = msg.get("tool_call_id")
            tool_name = tool_name_by_id.get(tool_call_id, "unknown") if isinstance(tool_call_id, str) else "unknown"
            summaries.append({"tool": tool_name, "output": compact})
            if len(summaries) >= max_items:
                break
        summaries.reverse()
        return summaries

    @staticmethod
    def _tool_signature(*, name: str, args: Dict[str, Any]) -> str:
        try:
            payload = json.dumps(args, sort_keys=True, ensure_ascii=False, default=str)
        except Exception:
            payload = str(args)
        return f"{name}:{payload}"

    def _load_schema_context(self) -> Dict[str, Any]:
        try:
            result = self._toolkit.execute_tool(
                "finance_get_schema_context",
                {"max_tables": 40, "include_relationships": True},
            )
            if not isinstance(result, dict) or not result.get("success"):
                return {}
            raw_tables = result.get("tables")
            tables: Dict[str, Any] = raw_tables if isinstance(raw_tables, dict) else {}
            compact_tables = {}
            for name, meta in tables.items():
                if not isinstance(meta, dict):
                    continue
                raw_cols = meta.get("columns")
                cols: List[Any] = raw_cols if isinstance(raw_cols, list) else []
                col_names = [c.get("name") for c in cols if isinstance(c, dict) and c.get("name")]
                compact_tables[name] = {
                    "columns": col_names,
                    "foreign_keys": meta.get("foreign_keys") or [],
                    "semantic_hint": meta.get("semantic_hint") or {},
                }
            return {
                "schema_hash": result.get("schema_hash"),
                "generated_at": result.get("generated_at"),
                "table_count": result.get("table_count"),
                "tables": compact_tables,
                "relationships": result.get("relationships") or [],
            }
        except Exception:
            logger.debug("finance schema context load failed", exc_info=True)
            return {}

    def _append_planned_tool_call(
        self,
        *,
        tool_name: str,
        arguments: Dict[str, Any],
        messages: List[Dict[str, Any]],
        trace: FinanceChatTrace,
        iteration: int,
        user_message: str,
    ) -> None:
        if not self._is_allowed_tool(tool_name):
            trace.rejected_tools.append(tool_name)
            return
        args = arguments if isinstance(arguments, dict) else {}
        if tool_name == "finance_search_transactions":
            args = self._ensure_search_args(args=args, user_message=user_message)
        elif tool_name == "finance_sql_query":
            args = self._normalize_sql_args(args)
        tool_call_id = f"planned_{iteration}_{len(trace.tool_calls)}"
        messages.append(
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": tool_call_id,
                        "type": "function",
                        "function": {
                            "name": tool_name,
                            "arguments": json.dumps(args, ensure_ascii=False),
                        },
                    }
                ],
            }
        )
        self._dispatch_single_tool_call(
            tc={
                "id": tool_call_id,
                "type": "function",
                "function": {
                    "name": tool_name,
                    "arguments": json.dumps(args, ensure_ascii=False),
                },
            },
            messages=messages,
            trace=trace,
            iteration=iteration,
            user_message=user_message,
        )

    def _final_synthesis(self, *, messages: List[Dict[str, Any]], trace: FinanceChatTrace) -> str:
        synth_messages = list(messages)
        synth_messages.append({"role": "system", "content": _FINANCE_FINAL_SYNTHESIS_PROMPT})
        final_response = self._generate_with_tools(messages=synth_messages, mode="none")
        final_text = (final_response.get("content") or "").strip()
        if final_text:
            trace.finish_reason = final_response.get("finish_reason", trace.finish_reason)
        return final_text

    def _append_and_dispatch_recovered_calls(
        self,
        *,
        recovered_calls: List[Dict[str, Any]],
        messages: List[Dict[str, Any]],
        trace: FinanceChatTrace,
        iteration: int,
        assistant_content: str,
        user_message: str,
    ) -> None:
        messages.append({"role": "assistant", "content": assistant_content, "tool_calls": recovered_calls})
        for tc in recovered_calls:
            self._dispatch_single_tool_call(
                tc=tc,
                messages=messages,
                trace=trace,
                iteration=iteration,
                user_message=user_message,
            )

    def _dispatch_single_tool_call(
        self,
        *,
        tc: Dict[str, Any],
        messages: List[Dict[str, Any]],
        trace: FinanceChatTrace,
        iteration: int,
        user_message: str,
    ) -> None:
        fn = tc.get("function") or {}
        name = fn.get("name") or ""
        tool_call_id = tc.get("id") or f"call_{iteration}_{len(trace.tool_calls)}"
        args = self._parse_args(fn.get("arguments"))

        if name == "finance_search_transactions":
            args = self._ensure_search_args(args=args, user_message=user_message)
        elif name == "finance_sql_query":
            args = self._normalize_sql_args(args)

        if not self._is_allowed_tool(name):
            trace.rejected_tools.append(name)
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call_id,
                    "content": (
                        f"[ERROR] Tool '{name}' ist im Finanz-Chat nicht verfuegbar. "
                        f"Erlaubt sind finance_*-Tools"
                        + (" plus code_executor." if self._allow_python else ".")
                    ),
                }
            )
            return

        result = self._toolkit.execute_tool(name, args)

        if (
            name == "finance_search_transactions"
            and isinstance(result, dict)
            and not result.get("success")
            and result.get("error_class") == "missing_param"
        ):
            retry_args = self._ensure_search_args(args={}, user_message=user_message)
            retry_result = self._toolkit.execute_tool(name, retry_args)
            if isinstance(retry_result, dict) and retry_result.get("success"):
                args = retry_args
                result = retry_result

        result_str = self._stringify_result(result)
        trace_entry: Dict[str, Any] = {
            "name": name,
            "args": args,
            "ok": bool(result.get("success", True)) if isinstance(result, dict) else False,
        }
        if isinstance(result, dict):
            if isinstance(result.get("row_count"), int):
                trace_entry["row_count"] = int(result["row_count"])
            if isinstance(result.get("count"), int):
                trace_entry["count"] = int(result["count"])
        trace.tool_calls.append(trace_entry)
        messages.append({"role": "tool", "tool_call_id": tool_call_id, "content": result_str})

    @staticmethod
    def _normalize_sql_args(args: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(args, dict):
            return {}
        if args.get("sql"):
            return args
        sql_query = args.get("sql_query")
        if isinstance(sql_query, str) and sql_query.strip():
            patched = dict(args)
            patched["sql"] = patched.pop("sql_query")
            return patched
        return args

    @staticmethod
    def _ensure_search_args(*, args: Dict[str, Any], user_message: str) -> Dict[str, Any]:
        if not isinstance(args, dict):
            args = {}
        patched = dict(args)
        query_text = patched.get("query_text")
        if not isinstance(query_text, str) or not query_text.strip():
            patched["query_text"] = (user_message or "").strip()
        if "limit" not in patched or patched.get("limit") in (None, ""):
            patched["limit"] = 500
        if "include_transfers" not in patched:
            patched["include_transfers"] = False
        return patched


    def _recover_tool_calls_from_text(self, content: str) -> Optional[List[Dict[str, Any]]]:
        text = (content or "").strip()
        if not text:
            return None

        recover = getattr(self._llm, "recover_tool_calls", None)
        if callable(recover):
            try:
                parsed = recover(text, self._tools)
                if parsed and isinstance(parsed, list):
                    return parsed
            except Exception:
                logger.debug("finance chat recovery: recover_tool_calls failed", exc_info=True)

        parser = getattr(self._llm, "_parse_tool_calls", None)
        if callable(parser):
            try:
                parsed = parser(text, self._tools)
                if parsed and isinstance(parsed, list):
                    return parsed
            except Exception:
                logger.debug("finance chat recovery: _parse_tool_calls failed", exc_info=True)
        return None

    @staticmethod
    def _strip_pseudo_tool_text(content: str) -> str:
        if not content:
            return ""
        lines = []
        for line in content.splitlines():
            l = line.strip()
            if l.startswith("<|tool_call>"):
                continue
            if l.startswith("call:"):
                continue
            lines.append(line)
        return "\n".join(lines).strip()

    def _generate_with_tools(self, *, messages: List[Dict[str, Any]], mode: str) -> Dict[str, Any]:
        if mode not in ("auto", "required", "none"):
            raise ValueError(f"Unsupported tool mode: {mode}")
        return self._llm.generate_with_tools(
            messages=messages,
            tools=self._tools,
            tool_choice=mode,
            max_tokens=self._max_tokens,
            temperature=self._temperature,
        )

    def _is_allowed_tool(self, name: str) -> bool:
        if name in self._tool_names:
            return True
        if self._allow_python and name == _PYTHON_TOOL_NAME:
            return True
        return False

    @staticmethod
    def _parse_args(raw: Any) -> Dict[str, Any]:
        if raw is None:
            return {}
        if isinstance(raw, dict):
            return raw
        if isinstance(raw, str):
            try:
                parsed = json.loads(raw)
                return parsed if isinstance(parsed, dict) else {}
            except json.JSONDecodeError:
                try:
                    parsed = ast.literal_eval(raw)
                    if isinstance(parsed, dict):
                        return parsed
                except (ValueError, SyntaxError):
                    pass
                return FinanceChatEngine._parse_loose_kv_args(raw)
        return {}

    @staticmethod
    def _parse_loose_kv_args(raw: str) -> Dict[str, Any]:
        text = (raw or "").strip()
        if not text:
            return {}
        if text.startswith("{") and text.endswith("}"):
            text = text[1:-1]

        pattern = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(\"[^\"]*\"|'[^']*'|[^,\n}]+)")
        out: Dict[str, Any] = {}
        for key, value_raw in pattern.findall(text):
            v = value_raw.strip()
            if (v.startswith('"') and v.endswith('"')) or (v.startswith("'") and v.endswith("'")):
                out[key] = v[1:-1]
                continue

            low = v.lower()
            if low in {"true", "false"}:
                out[key] = low == "true"
                continue

            try:
                if "." in v:
                    out[key] = float(v)
                else:
                    out[key] = int(v)
            except ValueError:
                out[key] = v
        return out

    def _stringify_result(self, result: Any) -> str:
        try:
            text = json.dumps(result, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            text = str(result)
        if len(text) > self.MAX_TOOL_RESULT_CHARS:
            text = text[: self.MAX_TOOL_RESULT_CHARS] + f"... [truncated, {len(text)} chars total]"
        return text

    @staticmethod
    def _sanitize_history(history: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        clean: List[Dict[str, Any]] = []
        for m in history:
            role = m.get("role")
            content = m.get("content")
            if role in ("user", "assistant") and isinstance(content, str) and content.strip():
                clean.append({"role": role, "content": content})
        return clean


__all__ = ["FinanceChatEngine", "FinanceChatResult", "FinanceChatTrace"]
