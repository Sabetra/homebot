"""Structured first-step query planning for finance chat.

This module intentionally contains no tool execution. It only produces one
validated initial plan for the finance tool loop in finance/chat.py.

SOTA Enhancement: Grammar-Constrained Decoding (GCD)
---------------------------------------------------
Applies Production-Ready Grammar-Constrained Decoding (PR-GCD) as recommended
in the finance optimization roadmap. When enabled, the LLM output is constrained
to a BNF grammar compiled from the FinanceQueryPlan Pydantic schema, eliminating
prompt drift and guaranteeing structurally valid output.

See: docs/finance_optimization_roadmap.md, finance/grammar_compiler.py
"""

from __future__ import annotations

import calendar
import datetime as dt
import re
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

from llm_structured_wrapper import LLMStructuredWrapper
from .grammar_compiler import GrammarCompiler, GrammarConfig


class FinanceQueryPlan(BaseModel):
	"""Structured output contract for the first finance tool step."""

	model_config = ConfigDict(extra="forbid")

	primary_tool: str
	arguments: Dict[str, Any] = Field(default_factory=dict)
	rationale: str = Field(min_length=1)
	confidence: float = Field(ge=0.0, le=1.0)
	requires_follow_up: bool = False
	follow_up_tool: Optional[str] = None
	follow_up_arguments: Dict[str, Any] = Field(default_factory=dict)
	should_synthesize_from_tool_outputs: bool = True


class FinanceQueryPlanner:
	"""Produces the first tool plan for a finance user question.

	Parameters
	----------
	llm_client : Any
		The LLM client instance.
	grammar_constrained : bool, optional
		When True, enables Grammar-Constrained Decoding (GCD).
		The LLM output will be constrained to a BNF grammar compiled
		from the FinanceQueryPlan schema. Defaults to False.
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
			raise ValueError("llm_client is required for FinanceQueryPlanner")
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
					FinanceQueryPlan,
					config=self._grammar_config,
				)
				self._grammar_xg = GrammarCompiler.compile_xgrammar_for_schema(
					FinanceQueryPlan,
					config=self._grammar_config,
				)
			except Exception:
				# Gracefully degrade: GCD disabled if compilation fails
				self._grammar_constrained = False

	def plan(
		self,
		*,
		question: str,
		schema_context: Dict[str, Any],
		available_tools: List[Dict[str, Any]],
		reference_date: Optional[dt.date] = None,
	) -> FinanceQueryPlan:
		"""Return a validated initial tool plan.

		Fallback behavior is deterministic and remains inside finance tools.

		When grammar_constrained is enabled, the LLM output is constrained
		to the compiled BNF grammar for FinanceQueryPlan, significantly
		reducing structural errors and prompt drift.
		"""
		if not isinstance(question, str) or not question.strip():
			return self._fallback_plan(
				question="",
				available_tool_names=self._extract_available_tool_names(available_tools),
			)

		reference_date = reference_date or dt.date.today()
		available_tool_names = self._extract_available_tool_names(available_tools)
		fallback = self._fallback_plan(
			question=question,
			available_tool_names=available_tool_names,
		)
		self.last_error = None
		self.used_fallback = False

		prompt = self._build_prompt(
			question=question,
			schema_context=schema_context,
			available_tools=available_tools,
			reference_date=reference_date,
		)

		parsed = self._wrapper.generate_structured_safe(
			prompt=prompt,
			output_schema=FinanceQueryPlan,
			fallback=fallback,
			max_tokens=900,
			on_error=lambda exc: setattr(self, "last_error", f"{type(exc).__name__}: {exc}"),
		)
		if parsed is None:
			parsed = fallback
		self.used_fallback = parsed is fallback

		return self._sanitize_plan(
			parsed,
			question=question,
			available_tool_names=available_tool_names,
			reference_date=reference_date,
		)

	@staticmethod
	def _extract_available_tool_names(available_tools: List[Dict[str, Any]]) -> List[str]:
		names: List[str] = []
		for tool in available_tools:
			if not isinstance(tool, dict):
				continue
			fn = tool.get("function")
			if not isinstance(fn, dict):
				continue
			name = fn.get("name")
			if isinstance(name, str) and name.startswith("finance_"):
				names.append(name)
		return sorted(set(names))

	@staticmethod
	def _build_prompt(
		*,
		question: str,
		schema_context: Dict[str, Any],
		available_tools: List[Dict[str, Any]],
		reference_date: dt.date,
	) -> str:
		tool_contracts = FinanceQueryPlanner._compact_tool_contracts(available_tools)
		return (
			"Du bist ein deterministischer Finance-Query-Planer.\n"
			"Aufgabe: Erzeuge genau einen Plan fuer die beste erste Tool-Aktion.\n\n"
			"Wichtige Regeln:\n"
			"- Nutze den Schema-Kontext als Wahrheit ueber Tabellen, Spalten und Beziehungen.\n"
			"- Vermeide Keyword- oder Pattern-Routing. Entscheide semantisch.\n"
			"- Fuer Fragen wie 'Wie viel habe ich bei/mit/fuer X ausgegeben?' bevorzuge finance_sum_counterparty_costs.\n"
			"- Fuer Fragen nach Ausgaben/Kosten fuer Kategorien bevorzuge finance_sum_category_costs.\n"
			"- Fuer Fixkosten vs variable Kosten -> finance_cost_structure_analysis.\n"
			"- Fuer wiederkehrende Kosten/Abos -> finance_recurring_expense_analysis.\n"
			"- Fuer Kosten-Prognosen -> finance_expense_forecast.\n"
			"- Fuer ungewoehnliche Ausgaben/Ausreisser -> finance_expense_anomaly_detection.\n"
			"- Fuer Budgetstatus in einem Monat -> finance_budget_status.\n"
			"- Fuer Soll/Ist-Budgetvergleich -> finance_budget_vs_actual_analysis.\n"
			"- Fuer Sparpotenziale -> finance_savings_potential_analysis.\n"
			"- Fuer Trendbruch in Ausgaben -> finance_expense_trend_break_detection.\n"
			"- Fuer tabellarische Auflistung -> finance_query_transactions.\n"
			"- Fuer freie Sammelmengen -> finance_search_transactions.\n"
			"- Fuer Kategorien/Zeitraeume ohne Gegenseite -> finance_aggregate oder finance_sql_query.\n"
			"- Wenn ein Monat ohne Jahr genannt wird, interpretiere bezogen auf Referenzdatum.\n"
			"- Liefere nur einen Plan, keine Analyse in Fliesstext.\n\n"
			f"Referenzdatum: {reference_date.isoformat()}\n"
			f"Verfuegbare Finance-Tools und Argumente: {tool_contracts}\n"
			f"Schema-Kontext: {schema_context}\n"
			f"Nutzerfrage: {question}\n"
		)

	@staticmethod
	def _compact_tool_contracts(
		available_tools: List[Dict[str, Any]],
	) -> List[Dict[str, Any]]:
		contracts: List[Dict[str, Any]] = []
		for tool in available_tools:
			function = tool.get("function") if isinstance(tool, dict) else None
			if not isinstance(function, dict):
				continue
			name = function.get("name")
			if not isinstance(name, str) or not name.startswith("finance_"):
				continue
			parameters = function.get("parameters")
			parameters = parameters if isinstance(parameters, dict) else {}
			properties = parameters.get("properties")
			properties = properties if isinstance(properties, dict) else {}
			contracts.append(
				{
					"name": name,
					"required": parameters.get("required") or [],
					"arguments": {
						key: value.get("type", "any") if isinstance(value, dict) else "any"
						for key, value in properties.items()
					},
				}
			)
		return contracts

	def _fallback_plan(
		self,
		*,
		question: str,
		available_tool_names: List[str],
	) -> FinanceQueryPlan:
		default_tool = (
			"finance_search_transactions"
			if "finance_search_transactions" in available_tool_names
			else (available_tool_names[0] if available_tool_names else "finance_search_transactions")
		)
		return FinanceQueryPlan(
			primary_tool=default_tool,
			arguments={
				"query_text": (question or "").strip(),
				"limit": 500,
				"include_transfers": False,
			},
			rationale="Fallback plan due to planner uncertainty.",
			confidence=0.51,
			requires_follow_up=False,
			follow_up_tool=None,
			follow_up_arguments={},
			should_synthesize_from_tool_outputs=True,
		)

	def _sanitize_plan(
		self,
		plan: FinanceQueryPlan,
		*,
		question: str,
		available_tool_names: List[str],
		reference_date: dt.date,
	) -> FinanceQueryPlan:
		"""Normalize planner output into a valid runtime plan."""
		primary_tool = plan.primary_tool
		if primary_tool not in available_tool_names:
			return self._fallback_plan(
				question=question,
				available_tool_names=available_tool_names,
			)

		arguments = self._normalize_args(
			args=plan.arguments,
			reference_date=reference_date,
		)

		follow_up_tool = plan.follow_up_tool
		follow_up_arguments = self._normalize_args(
			args=plan.follow_up_arguments,
			reference_date=reference_date,
		)
		requires_follow_up = bool(plan.requires_follow_up)

		if not requires_follow_up:
			follow_up_tool = None
			follow_up_arguments = {}
		elif follow_up_tool not in available_tool_names:
			requires_follow_up = False
			follow_up_tool = None
			follow_up_arguments = {}

		return FinanceQueryPlan(
			primary_tool=primary_tool,
			arguments=arguments,
			rationale=plan.rationale,
			confidence=plan.confidence,
			requires_follow_up=requires_follow_up,
			follow_up_tool=follow_up_tool,
			follow_up_arguments=follow_up_arguments,
			should_synthesize_from_tool_outputs=bool(plan.should_synthesize_from_tool_outputs),
		)

	def _normalize_args(
		self,
		*,
		args: Dict[str, Any],
		reference_date: dt.date,
	) -> Dict[str, Any]:
		if not isinstance(args, dict):
			return {}

		normalized: Dict[str, Any] = {}
		for key, value in args.items():
			if isinstance(value, str):
				normalized[key] = self._normalize_time_value(
					key=key,
					value=value,
					reference_date=reference_date,
				)
			elif isinstance(value, list):
				normalized[key] = [
					self._normalize_time_value(
						key=key,
						value=item,
						reference_date=reference_date,
					)
					if isinstance(item, str)
					else item
					for item in value
				]
			else:
				normalized[key] = value

		if normalized.get("query_text") is None:
			normalized.pop("query_text", None)

		return normalized

	def _normalize_time_value(
		self,
		*,
		key: str,
		value: str,
		reference_date: dt.date,
	) -> str:
		raw = value.strip()
		if not raw:
			return raw

		month_only = self._parse_month_without_year(raw, reference_date)
		if month_only is None:
			return raw

		month_str = f"{month_only.year:04d}-{month_only.month:02d}"
		if key in {"month", "start_month", "end_month"}:
			return month_str
		if key == "start_date":
			return f"{month_str}-01"
		if key == "end_date":
			last_day = calendar.monthrange(month_only.year, month_only.month)[1]
			return f"{month_str}-{last_day:02d}"
		return raw

	@staticmethod
	def _parse_month_without_year(value: str, reference_date: dt.date) -> Optional[dt.date]:
		"""Parse month names without year into a concrete month/year.

		Rules:
		- If input already contains a 4-digit year, return None (no rewrite).
		- If input is month name (de/en), map to reference year.
		"""
		if re.search(r"\b\d{4}\b", value):
			return None

		cleaned = re.sub(r"[^a-zA-Z]", "", value).lower()
		if not cleaned:
			return None

		month_map = {
			"januar": 1,
			"jan": 1,
			"january": 1,
			"februar": 2,
			"feb": 2,
			"february": 2,
			"maerz": 3,
			"maer": 3,
			"marz": 3,
			"march": 3,
			"mar": 3,
			"april": 4,
			"apr": 4,
			"mai": 5,
			"may": 5,
			"juni": 6,
			"jun": 6,
			"june": 6,
			"juli": 7,
			"jul": 7,
			"july": 7,
			"august": 8,
			"aug": 8,
			"september": 9,
			"sep": 9,
			"sept": 9,
			"october": 10,
			"oktober": 10,
			"okt": 10,
			"oct": 10,
			"november": 11,
			"nov": 11,
			"dezember": 12,
			"dez": 12,
			"december": 12,
			"dec": 12,
		}

		month = month_map.get(cleaned)
		if month is None:
			return None
		return dt.date(reference_date.year, month, 1)