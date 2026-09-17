"""Finanz-Tools für den Agent-Orchestrator.

Stellt deterministische, auf der Finanz-DB operierende Funktionen bereit.
Werden von ``AgentToolkit`` ber das normale Dispatch registriert. Alle
Methoden sind reine SQL-Queries -- KEIN LLM-Aufruf, KEIN Web-Zugriff
(Ausnahme: ``suggest_categories`` ist explizit LLM-gesttzt und braucht
einen LLM-Client aus dem Toolkit-Kontext).

Konvention der Rckgaben: ``{"success": bool, ...payload}`` analog zu
allen anderen Toolkit-Methoden.
"""

from __future__ import annotations

import calendar
import json
import logging
import math
import random
import re
import sqlite3
from datetime import date, timedelta
from statistics import mean, median, pstdev
from typing import Any, Dict, List, Optional, Sequence, Tuple

from finance.db_schema import (
    FinanceDB,
    Goal,
    _from_cents,
    _normalize_iban,
    _normalize_like_needle,
    _to_cents,
)
from finance.models import DEFAULT_CURRENCY, VALID_GOAL_STATUSES
from finance.series_engine import (
    VALID_CADENCES,
    SeriesException,
    SeriesSpec,
    detect_candidates,
    expand_series,
)

logger = logging.getLogger(__name__)

# Heuristische "vielleicht-Abo"-Klassifikation (Phase-1-SOTA), bewusst
# konservativ: Treffer sind Hinweise, keine Fakten (UI/Chat formulieren
# entsprechend als "vermutliches Abo").
SUBSCRIPTION_NAME_HINTS = (
    "netflix", "spotify", "prime video", "apple.com/bill", "google one",
    "youtube premium", "youtube music", "icloud", "onedrive", "office 365",
    "office365", "dropbox", "adobe", "openai", "chatgpt", "github",
    "notion", "slack", "zoom", "canva", "figma", "nordvpn", "mullvad",
    "surfshark", "expressvpn", "deezer", "tidal", "patreon", "substack",
    "zeit online", "t-online", "vodafone", "telekom", "magenta", "web.de",
    "gmx", "ionos", "hetzner", "strato", "all-ink", "congstar", "fritz",
    "kagi", "protonmail", "protonvpn",
)
SUBSCRIPTION_CATEGORY_HINTS = (
    "abonn", "subscri", "membership", "stream", "saas", "software",
    "cloud", "medien", "media", "digital",
)


def _goal_draw_schedule_cents(
    target_cents: int,
    saved_cents: int,
    rate_cents: int,
    target_date: Optional[str],
    reference: date,
    forecast_months: int,
) -> List[int]:
    """Deterministische Monats-Ziehung (Cents) für ein einzelnes Sparziel.

    Cap (DoD#6): Ziehungen sind auf den Restbetrag (``target - saved``)
    begrenzt und optional durch ``target_date`` abgegrenzt (keine Ziehung
    ab dem Folgemonat des Zieltermins). Schritt ``k`` entspricht dem
    (k-1)-ten Folgemonat des Referenzdatums (gleiche Konvention wie
    :meth:`FinanceTools.project_goal`).

    Rückgabe: Liste der Länge ``forecast_months`` (ab Erreichen des
    Ziels null), Summe = min(Restbetrag, Rate * Anzahl_Schritte).
    """
    schedule = [0] * max(0, int(forecast_months))
    remaining = max(0, int(target_cents) - int(saved_cents))
    if remaining <= 0 or rate_cents <= 0:
        return schedule
    last_step = -(-int(remaining) // int(rate_cents))  # Deckel: Monate bis gefüllt
    if target_date:
        try:
            target = date.fromisoformat(str(target_date))
            months_to_target = (target.year - reference.year) * 12 + (
                target.month - reference.month
            )
            last_step = min(last_step, months_to_target)
        except ValueError:
            pass  # ungültiges target_date: Cap nur über den Betrag (DAO prüft ISO)
    for step in range(1, max(0, min(last_step, len(schedule))) + 1):
        schedule[step - 1] = min(int(rate_cents), int(remaining) - int(rate_cents) * (step - 1))
    return schedule


class FinanceTools:
    """Sammlung der Finance-Tool-Methoden, von ``AgentToolkit`` delegiert."""

    def __init__(
        self,
        db: Optional[FinanceDB] = None,
        *,
        llm_client: Optional[Any] = None,
    ) -> None:
        self._db = db or FinanceDB.get_instance()
        self._llm_client = llm_client

    # -- list accounts -----------------------------------------------

    def list_accounts(self, params: Dict[str, Any]) -> Dict[str, Any]:
        accounts = self._db.list_accounts()
        return {
            "success": True,
            "count": len(accounts),
            "accounts": [
                {
                    "account_id": a.id,
                    "iban": a.iban,
                    "bank_name": a.bank_name,
                    "account_holder": a.account_holder,
                    "currency": a.currency,
                    "account_type": a.account_type,
                }
                for a in accounts
            ],
        }

    def get_schema_context(self, params: Dict[str, Any]) -> Dict[str, Any]:
        max_tables = self._coerce_int_param(params.get("max_tables"), default=30)
        if max_tables is None:
            max_tables = 30
        max_tables = max(1, min(int(max_tables), 200))

        include_relationships = bool(params.get("include_relationships", True))
        context = self._db.get_schema_context()
        raw_tables = context.get("tables")
        tables: Dict[str, Any] = raw_tables if isinstance(raw_tables, dict) else {}
        table_items = sorted(tables.items(), key=lambda kv: kv[0])
        selected_tables = dict(table_items[:max_tables])

        payload = {
            "version": context.get("version"),
            "db_path": context.get("db_path"),
            "schema_hash": context.get("schema_hash"),
            "generated_at": context.get("generated_at"),
            "table_count": len(tables),
            "tables": selected_tables,
        }
        if include_relationships:
            payload["relationships"] = context.get("relationships") or []

        return {
            "success": True,
            **payload,
        }

    # -- query transactions ------------------------------------------

    def query_transactions(self, params: Dict[str, Any]) -> Dict[str, Any]:
        account_id = self._resolve_account_id(params.get("iban"))
        if params.get("iban") and account_id is None:
            return {
                "success": False,
                "error": f"Unknown IBAN: {params.get('iban')!r}",
                "error_class": "unknown_iban",
            }
        try:
            limit = int(params.get("limit", 100))
        except (TypeError, ValueError):
            limit = 100
        limit = max(1, min(limit, 1000))

        rows = self._db.query_transactions(
            account_id=account_id,
            start_date=params.get("start_date"),
            end_date=params.get("end_date"),
            counterparty_like=params.get("counterparty_like"),
            category=params.get("category"),
            limit=limit,
        )
        return {
            "success": True,
            "count": len(rows),
            "transactions": [
                {
                    "transaction_id": t.id,
                    "account_id": t.account_id,
                    "booking_date": t.booking_date,
                    "value_date": t.value_date,
                    "amount": _from_cents(t.amount_cents),
                    "currency": t.currency,
                    "counterparty": t.counterparty,
                    "counterparty_iban": t.counterparty_iban,
                    "purpose": t.purpose,
                    "booking_type": t.booking_type,
                    "transaction_nature": t.transaction_nature,
                    "category": t.category,
                }
                for t in rows
            ],
        }

    def sql_query(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Fuehrt eine read-only SQL-Abfrage gegen die Finance-DB aus.

        Sicherheitsrahmen:
        - nur SELECT/WITH/PRAGMA table_info
        - keine mutierenden Statements
        - hartes Ergebnislimit
        """
        sql = (params.get("sql") or "").strip()
        if not sql:
            return {
                "success": False,
                "error": "sql required",
                "error_class": "missing_param",
            }

        limit = self._coerce_int_param(params.get("limit"), default=100)
        if limit is None:
            return {
                "success": False,
                "error": "limit must be an integer",
                "error_class": "invalid_limit",
            }
        safe_limit = max(1, min(int(limit), 500))

        sql = sql.strip().rstrip(";").strip()
        if not sql:
            return {
                "success": False,
                "error": "sql must be non-empty",
                "error_class": "invalid_sql",
            }

        lowered = sql.lower()
        if not (
            lowered.startswith("select")
            or lowered.startswith("with")
            or re.match(r'^\s*pragma\s+table_info\s*\(', lowered)
        ):
            return {
                "success": False,
                "error": "Only read-only SELECT/CTE/PRAGMA table_info queries are allowed",
                "error_class": "forbidden_sql",
            }

        forbidden = [
            " insert ", " update ", " delete ", " drop ", " alter ", " create ",
            " replace ", " truncate ", " attach ", " detach ", " vacuum ",
            " reindex ", " analyze ", " begin ", " commit ", " rollback ",
            " writable_schema ",
        ]
        padded = f" {lowered} "
        if any(token in padded for token in forbidden):
            return {
                "success": False,
                "error": "Mutating or administrative SQL is not allowed",
                "error_class": "forbidden_sql",
            }

        if not re.search(r"\blimit\s+\d+\b", lowered):
            sql = f"{sql} LIMIT {safe_limit}"

        raw_params = params.get("query_params")
        query_params = raw_params if isinstance(raw_params, list) else []

        try:
            with self._db._lock, self._db._connect() as conn:
                rows = conn.execute(sql, query_params).fetchall()
        except Exception as exc:
            return {
                "success": False,
                "error": f"SQL execution failed: {exc}",
                "error_class": type(exc).__name__,
            }

        out_rows = []
        for row in rows:
            item: Dict[str, Any] = {}
            for key in row.keys():
                value = row[key]
                if isinstance(value, bytes):
                    item[key] = f"<BLOB {len(value)} bytes>"
                else:
                    item[key] = value
            out_rows.append(item)

        columns = list(out_rows[0].keys()) if out_rows else []
        return {
            "success": True,
            "sql": sql,
            "query_params": query_params,
            "row_count": len(out_rows),
            "columns": columns,
            "rows": out_rows,
            "preview_json": json.dumps(out_rows[:10], ensure_ascii=False),
        }

    def search_transactions(self, params: Dict[str, Any]) -> Dict[str, Any]:
        query_text = (params.get("query_text") or "").strip()
        if not query_text:
            return {
                "success": False,
                "error": "query_text required",
                "error_class": "missing_param",
            }
        account_id = self._resolve_account_id(params.get("iban"))
        if params.get("iban") and account_id is None:
            return {
                "success": False,
                "error": f"Unknown IBAN: {params.get('iban')!r}",
                "error_class": "unknown_iban",
            }
        limit = self._coerce_int_param(params.get("limit"), default=500)
        if limit is None:
            return {
                "success": False,
                "error": "limit must be an integer",
                "error_class": "invalid_limit",
            }

        rows = self._db.search_transactions_text(
            query_text=query_text,
            account_id=account_id,
            start_date=self._normalize_date_param(params.get("start_date")),
            end_date=self._normalize_date_param(params.get("end_date")),
            limit=limit,
            include_transfers=bool(params.get("include_transfers", False)),
        )
        return {
            "success": True,
            "query_text": query_text,
            "count": len(rows),
            "matches": [
                {
                    "transaction_id": r["transaction"].id,
                    "booking_date": r["transaction"].booking_date,
                    "amount": _from_cents(r["transaction"].amount_cents),
                    "currency": r["transaction"].currency,
                    "counterparty": r["transaction"].counterparty,
                    "purpose": r["transaction"].purpose,
                    "category": r["transaction"].category,
                    "transaction_nature": r["transaction"].transaction_nature,
                    "match_source": r.get("match_source"),
                    "phrase_match": bool(r.get("phrase_match", False)),
                    "semantic_score": r.get("semantic_score"),
                    "fused_score": r.get("fused_score"),
                    "lexical_rank": r.get("lexical_rank"),
                    "semantic_rank": r.get("semantic_rank"),
                    "search_text": r.get("search_text"),
                }
                for r in rows
            ],
        }

    # -- aggregate ---------------------------------------------------

    def aggregate(self, params: Dict[str, Any]) -> Dict[str, Any]:
        group_by = params.get("group_by")
        if group_by not in {"month", "account", "counterparty", "category"}:
            return {
                "success": False,
                "error": f"group_by must be one of month/account/counterparty/category, got {group_by!r}",
                "error_class": "invalid_group_by",
            }
        account_id = self._resolve_account_id(params.get("iban"))
        if params.get("iban") and account_id is None:
            return {
                "success": False,
                "error": f"Unknown IBAN: {params.get('iban')!r}",
                "error_class": "unknown_iban",
            }
        rows = self._db.aggregate(
            group_by=group_by,
            account_id=account_id,
            start_date=self._normalize_date_param(params.get("start_date")),
            end_date=self._normalize_date_param(params.get("end_date")),
            currency=params.get("currency"),
        )
        return {
            "success": True,
            "group_by": group_by,
            "groups": [
                {
                    "key": r["key"],
                    "currency": r["currency"],
                    "income": _from_cents(r["income_cents"]),
                    "expense": _from_cents(r["expense_cents"]),
                    "net": _from_cents(r["net_cents"]),
                    "count": r["count"],
                }
                for r in rows
            ],
        }

    def sum_counterparty_costs(self, params: Dict[str, Any]) -> Dict[str, Any]:
        counterparty_like = _normalize_like_needle(params.get("counterparty"))
        if not counterparty_like:
            return {
                "success": False,
                "error": "counterparty required",
                "error_class": "missing_param",
            }
        account_id = self._resolve_account_id(params.get("iban"))
        if params.get("iban") and account_id is None:
            return {
                "success": False,
                "error": f"Unknown IBAN: {params.get('iban')!r}",
                "error_class": "unknown_iban",
            }

        summary = self._db.summarize_counterparty_costs(
            counterparty_like=counterparty_like,
            account_id=account_id,
            start_date=self._normalize_date_param(params.get("start_date")),
            end_date=self._normalize_date_param(params.get("end_date")),
            include_transfers=bool(params.get("include_transfers", False)),
        )
        return {
            "success": True,
            "counterparty": summary["counterparty_like"],
            "expense": _from_cents(summary["expense_abs_cents"]),
            "refunds": _from_cents(summary["refund_cents"]),
            "net": _from_cents(summary["net_cents"]),
            "tx_count": summary["tx_count"],
            "by_currency": [
                {
                    "currency": r["currency"],
                    "expense": _from_cents(r["expense_abs_cents"]),
                    "refunds": _from_cents(r["refund_cents"]),
                    "net": _from_cents(r["net_cents"]),
                    "tx_count": r["tx_count"],
                }
                for r in summary["by_currency"]
            ],
        }

    def sum_category_costs(self, params: Dict[str, Any]) -> Dict[str, Any]:
        raw_categories = params.get("categories")
        if isinstance(raw_categories, str):
            requested = [item.strip() for item in raw_categories.split(",") if item.strip()]
        elif isinstance(raw_categories, list):
            requested = [str(item).strip() for item in raw_categories if str(item).strip()]
        else:
            requested = []
        if not requested:
            return {"success": False, "error": "categories required", "error_class": "missing_param"}

        available = [category.name for category in self._db.list_categories()]
        matched: List[str] = []
        unmatched: List[str] = []
        for requested_name in requested:
            resolved = self._resolve_category_name(requested_name, available)
            if resolved is None:
                unmatched.append(requested_name)
            elif resolved not in matched:
                matched.append(resolved)
        if not matched:
            return {
                "success": False,
                "error": f"Unknown categories: {unmatched}",
                "error_class": "unknown_category",
                "available_categories": available,
            }

        facts, error = self._analysis_facts(params)
        if error is not None:
            return error
        selected = [fact for fact in facts if fact["category"] in matched]
        summaries = self._cost_summaries_by_currency(selected)
        result: Dict[str, Any] = {
            "success": True,
            "matched_categories": matched,
            "unmatched_categories": unmatched,
            "by_currency": summaries,
            "tx_count": len(selected),
        }
        self._promote_single_currency_costs(result, summaries)
        return result

    def cost_structure_analysis(self, params: Dict[str, Any]) -> Dict[str, Any]:
        facts, error = self._analysis_facts(params)
        if error is not None:
            return error
        expense_facts = [fact for fact in facts if fact["amount_cents"] < 0]
        recurring = self._recurring_groups(expense_facts, min_occurrences=2)
        fixed_keys = {
            (item["currency"], item["counterparty"])
            for item in recurring
            if item["is_fixed"]
        }
        totals: Dict[str, Dict[str, int]] = {}
        drivers: Dict[Tuple[str, str], int] = {}
        for fact in expense_facts:
            currency = fact["currency"]
            amount = abs(int(fact["amount_cents"]))
            bucket = totals.setdefault(currency, {"fixed_cents": 0, "variable_cents": 0})
            target = "fixed_cents" if (currency, fact["counterparty"]) in fixed_keys else "variable_cents"
            bucket[target] += amount
            driver_key = (currency, fact["counterparty"])
            drivers[driver_key] = drivers.get(driver_key, 0) + amount

        by_currency = []
        for currency, values in sorted(totals.items()):
            total_cents = values["fixed_cents"] + values["variable_cents"]
            by_currency.append(
                {
                    "currency": currency,
                    "fixed_expense": _from_cents(values["fixed_cents"]),
                    "variable_expense": _from_cents(values["variable_cents"]),
                    "total_expense": _from_cents(total_cents),
                    "fixed_share": round(values["fixed_cents"] / total_cents, 6) if total_cents else 0.0,
                }
            )
        top_drivers = [
            {
                "currency": currency,
                "counterparty": counterparty,
                "expense": _from_cents(amount_cents),
            }
            for (currency, counterparty), amount_cents in sorted(
                drivers.items(), key=lambda item: item[1], reverse=True
            )[:10]
        ]
        result = {
            "success": True,
            "by_currency": by_currency,
            "top_cost_drivers": top_drivers,
            "classification": {
                "minimum_month_coverage": 0.6,
                "minimum_amount_stability": 0.8,
            },
        }
        if len(by_currency) == 1:
            result.update(by_currency[0])
        return result

    def recurring_expense_analysis(self, params: Dict[str, Any]) -> Dict[str, Any]:
        min_occurrences = self._coerce_int_param(params.get("min_occurrences"), default=2)
        if min_occurrences is None or min_occurrences < 2:
            return {
                "success": False,
                "error": "min_occurrences must be an integer >= 2",
                "error_class": "invalid_param",
            }
        facts, error = self._analysis_facts(params)
        if error is not None:
            return error
        recurring = self._recurring_groups(
            [fact for fact in facts if fact["amount_cents"] < 0],
            min_occurrences=min_occurrences,
        )
        return {
            "success": True,
            "count": len(recurring),
            "recurring_expenses": recurring,
        }

    def expense_forecast(self, params: Dict[str, Any]) -> Dict[str, Any]:
        raw_lookback = params.get("lookback_months", params.get("history_months"))
        lookback = self._coerce_int_param(raw_lookback, default=6)
        forecast_months = self._coerce_int_param(params.get("forecast_months"), default=3)
        if lookback is None or forecast_months is None or lookback < 1 or not 1 <= forecast_months <= 24:
            return {"success": False, "error": "Invalid forecast window", "error_class": "invalid_param"}
        facts, error = self._analysis_facts(params)
        if error is not None:
            return error
        monthly = self._monthly_expenses(facts)
        forecasts: List[Dict[str, Any]] = []
        for currency, values in sorted(monthly.items()):
            months = sorted(values)
            if not months:
                continue
            history = months[-lookback:]
            average_cents = mean(values[month] for month in history)
            for offset in range(1, forecast_months + 1):
                forecasts.append(
                    {
                        "month": self._shift_month(months[-1], offset),
                        "currency": currency,
                        "expense": round(_from_cents(int(round(average_cents))), 2),
                        "history_months_used": history,
                        "method": "rolling_monthly_mean",
                    }
                )
        return {"success": True, "forecast": forecasts, "count": len(forecasts)}

    # -- Phase 1 "Monarch-Core" (SOTA-Haushaltsprognosen) -------------
    #
    # Schedule-first-Hybrid (siehe docs/03_FINANCE_MODULE.md):
    # deterministische Termine (Recurring: Rechnungen, Abos, Gehalt)
    # werden aus dem echten Zahlungshistorien-Anchortag projiziert; der
    # stochastische variable Rest wird mit OLS-Trend x Kalendermonats-
    # Saisonalitaet + Residual-Bootstrap-KI (fester Seed) geschätzt;
    # das Guthaben wird vom tatsaechlichen ``balance_at`` fortgeschrieben.
    # Reine Stdlib, deterministisch, fail-fast.

    @staticmethod
    def _parse_reference_date(value: Any) -> date:
        """'YYYY-MM-DD'-Referenzdatum parsen (None/leer -> heute)."""
        if value is None or value == "":
            return date.today()
        if isinstance(value, date):
            return value
        if isinstance(value, str):
            return date.fromisoformat(value.strip())
        raise ValueError("reference_date must be 'YYYY-MM-DD'")

    def _facts_up_to(
        self, iban: Optional[str], reference: date
    ) -> Tuple[List[Dict[str, Any]], Optional[Dict[str, Any]]]:
        """Analyse-Fakten bis (inklusive) Referenzdatum.

        Zukünftige Buchungen werden nie gelesen (kein Future-Leak in
        Prognosen); Transfers bleiben ausgeschlossen wie in allen
        übrigen Finanz-Tools.
        """
        facts, error = self._analysis_facts({"iban": iban})
        if error is not None:
            return [], error
        ref_iso = reference.isoformat()
        return [fact for fact in facts if fact["booking_date"] <= ref_iso], None

    @staticmethod
    def _month_key(month: str) -> Tuple[int, int]:
        """'YYYY-MM' -> (Jahr, Monat)."""
        year, mon = month.split("-", 1)
        return int(year), int(mon)

    @staticmethod
    def _next_month_key(key: Tuple[int, int], steps: int) -> Tuple[int, int]:
        """Kalendermonat-Schritt (steps > 0: Zukunft, < 0: Vergangenheit)."""
        absolute = key[0] * 12 + key[1] - 1 + steps
        return absolute // 12, absolute % 12 + 1

    @staticmethod
    def _next_due_on_or_after(reference: date, anchor_day: int) -> date:
        """Erste Faelligkeit ab ``reference`` fuer den Anker-Tag.

        Kuerzere Monate clampen auf den Monatsletzten (Anker 31 im
        Februar -> 28/29). Monatsende-Anker (Tag 29-31) gelten am
        Referenztag selbst als faellig — die naechste Faelligkeit ist
        dann der Folgemonat (31.01. + Tag 31 -> 28.02.); natuerliche
        Anker (Tag 1-28) enthalten den Referenztag selbst.
        Deterministisch, max. 14 Iterationen.
        """
        year, month = reference.year, reference.month
        for _ in range(14):
            last_day = calendar.monthrange(year, month)[1]
            candidate = date(year, month, min(anchor_day, last_day))
            if candidate > reference or (candidate == reference and anchor_day <= 28):
                return candidate
            year, month = (year + 1, 1) if month == 12 else (year, month + 1)
        return reference  # defensiv; mathematisch unerreichbar

    @staticmethod
    def _fit_trend(values: List[float]) -> Tuple[float, float]:
        """OLS-Fit y = a + b*t (t = 0..n-1); n < 3 -> flacher Mittelwert."""
        n = len(values)
        if n == 0:
            return 0.0, 0.0
        if n < 3:
            return mean(values), 0.0
        t_mean = (n - 1) / 2.0
        y_mean = mean(values)
        numerator = sum((i - t_mean) * (value - y_mean) for i, value in enumerate(values))
        denominator = sum((i - t_mean) ** 2 for i in range(n))
        slope = numerator / denominator if denominator else 0.0
        return y_mean - slope * t_mean, slope

    @staticmethod
    def _seasonal_index(observed: Sequence[Tuple[int, float]]) -> Dict[int, float]:
        """Multiplikative Saisonalitäts-Indizes pro Kalendermonat (1..12).

        Nur aktiv, wenn mindestens 12 Monatsbeobachtungen alle 12
        Kalendermonate abdecken; sonst neutraler Index 1.0 (dokumentierte
        Degradation bei wenig Daten).
        """
        by_month: Dict[int, List[float]] = {}
        for cal_month, value in observed:
            by_month.setdefault(cal_month, []).append(value)
        if len(by_month) < 12 or sum(len(values) for values in by_month.values()) < 12:
            return {month: 1.0 for month in range(1, 13)}
        all_values = [value for values in by_month.values() for value in values]
        overall_mean = mean(all_values)
        if overall_mean <= 0:
            return {month: 1.0 for month in range(1, 13)}
        return {
            month: (mean(by_month[month]) / overall_mean if month in by_month else 1.0)
            for month in range(1, 13)
        }

    @staticmethod
    def _bootstrap_interval(
        point: float,
        residuals: Sequence[float],
        confidence: float,
        seed: int = 42,
        draws: int = 1000,
    ) -> Tuple[float, float]:
        """Residual-Bootstrap-Konfidenzintervall um den Punkt-Schätzer.

        Deterministisch (fester Seed). Leere Residuals -> Punktintervall.
        """
        if not residuals:
            return point, point
        rng = random.Random(seed)
        alpha = max(0.01, min(0.5, (1.0 - confidence) / 2.0))
        lower_q = alpha
        upper_q = 1.0 - alpha
        n = len(residuals)
        sums = []
        for _ in range(draws):
            total = 0.0
            for _ in range(n):
                total += residuals[rng.randrange(n)]
            sums.append(total / n)
        sums.sort()
        lower = point + sums[int(lower_q * (draws - 1))]
        upper = point + sums[int(upper_q * (draws - 1))]
        return min(lower, upper), max(lower, upper)

    @staticmethod
    def _expense_recurring_pairs(
        facts: Sequence[Dict[str, Any]],
        min_occurrences: int,
        fixed_only: bool = False,
    ) -> set:
        """(currency, counterparty)-Paare, die wiederkehrende Ausgaben sind.

        ``fixed_only=True`` schraeft auf konstante Betraege ein
        (deterministischer Plan: Miete, Abo, Gehalt); variable
        Wiederkehrende (z. B. Lebensmittel) bleiben im stochastischen
        Rest der Prognose.
        """
        grouped: Dict[Tuple[str, str], List[int]] = {}
        for fact in facts:
            if int(fact["amount_cents"]) >= 0:
                continue
            key = (fact["currency"], fact["counterparty"])
            grouped.setdefault(key, []).append(abs(int(fact["amount_cents"])))
        return {
            key
            for key, amounts in grouped.items()
            if len(amounts) >= min_occurrences and (not fixed_only or len(set(amounts)) == 1)
        }

    @staticmethod
    def _detect_price_change(
        amounts: Sequence[float], last_date: str
    ) -> Tuple[Optional[Dict[str, Any]], Optional[float]]:
        """Echte Preisveraenderung einer wiederkehrenden Ausgabengruppe.

        Qualifiziert nur, wenn der alte Preis stabil war (mindestens 2
        aufeinanderfolgende Zahlungen zu diesem Betrag) und der neue
        Preis die letzte Zahlung ist. Normale Streuung (z. B.
        Lebensmittel) liefert ``(None, None)``; die Monatskosten werden
        dann der Durchschnitt. Liefert ``(change | None, new_price | None)``.
        """
        last = amounts[-1]
        index = len(amounts) - 2
        while index >= 0 and amounts[index] == last:
            index -= 1
        if index < 0:
            return None, None
        old = amounts[index]
        if index < 1 or amounts[index - 1] != old:
            return None, None
        change = {
            "old": round(old, 2),
            "new": round(last, 2),
            "change_pct": round((last - old) / old * 100.0, 1) if old else None,
            "date": last_date,
        }
        return change, round(last, 2)

    @classmethod
    def _is_subscription_like(
        cls, counterparty: str, category: Optional[str] = None
    ) -> bool:
        """Heuristischer "vielleicht-Abo"-Marker (Name- oder Kategorie-Hinweis)."""
        name = (counterparty or "").casefold()
        if any(hint in name for hint in SUBSCRIPTION_NAME_HINTS):
            return True
        cat = (category or "").casefold()
        return any(hint in cat for hint in SUBSCRIPTION_CATEGORY_HINTS)

    def upcoming_bills(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Deterministischer Fälligkeits-Kalender für kommende Zahlungen.

        Projeziert den nächsten Fälligkeitstag jeder wiederkehrenden
        Ausgabengruppe (>= 2 Buchungen) aus dem Median der letzten 5
        Buchungs-Tage (Anchortag). Phase-1-SOTA ("Monarch-Core"):
        Basis für die Cashflow-Prognose und Fälligkeits-Erinnerungen.

        Parameters
        ----------
        days_ahead:     1-180 (Default 30)
        iban:           optionales Konten-Filter (IBAN)
        reference_date: 'YYYY-MM-DD' (Default heute; testbar injizierbar)
        """
        try:
            reference = self._parse_reference_date(params.get("reference_date"))
        except ValueError as exc:
            return {"success": False, "error": str(exc), "error_class": "invalid_reference_date"}

        days_ahead = self._coerce_int_param(params.get("days_ahead"), default=30)
        if days_ahead is None:
            return {"success": False, "error": "days_ahead must be an integer", "error_class": "invalid_param"}
        days_ahead = max(1, min(int(days_ahead), 180))

        iban = params.get("iban") or None
        facts, error = self._facts_up_to(iban, reference)
        if error is not None:
            return error

        expense_pairs = self._expense_recurring_pairs(facts, min_occurrences=2)
        groups = [
            group
            for group in self._recurring_groups(facts, min_occurrences=2)
            if (group["currency"], group["counterparty"]) in expense_pairs
        ]

        window_end = reference + timedelta(days=days_ahead)
        bills = []
        for group in groups:
            counterparty = group["counterparty"]
            expense_dates = sorted(
                fact["booking_date"]
                for fact in facts
                if fact["counterparty"] == counterparty and int(fact["amount_cents"]) < 0
            )
            if not expense_dates:
                continue
            anchor_day = int(round(median([int(day[8:10]) for day in expense_dates[-5:]])))
            next_due = self._next_due_on_or_after(reference, anchor_day)
            if next_due > window_end:
                continue
            category = next(
                (fact["category"] for fact in facts if fact["counterparty"] == counterparty), ""
            )
            bills.append(
                {
                    "counterparty": counterparty,
                    "category": category,
                    "currency": group["currency"],
                    "amount": group["average_expense"],
                    "next_due": next_due.isoformat(),
                    "days_until": (next_due - reference).days,
                    "last_seen": expense_dates[-1],
                    "occurrences": group["occurrences"],
                    "is_fixed": group["is_fixed"],
                    "subscription_like": self._is_subscription_like(counterparty, category),
                }
            )
        bills.sort(key=lambda bill: (bill["next_due"], bill["counterparty"]))
        currencies = {bill["currency"] for bill in bills}
        single_currency = currencies.pop() if len(currencies) == 1 else None
        return {
            "success": True,
            "method": "recurring_projection",
            "reference_date": reference.isoformat(),
            "window_end": window_end.isoformat(),
            "currency": single_currency,
            "count": len(bills),
            "bills": bills,
            "total_in_window": (
                round(sum(bill["amount"] for bill in bills), 2) if single_currency else None
            ),
        }

    def cash_flow_forecast(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Cashflow- und Guthaben-Prognose (Phase 1 "Monarch-Core").

        Schedule-first-Hybrid: wiederkehrende Zahlungen werden aus dem
        Zahlungshistorien-Monatswert projiziert (deterministischer
        Plan); variable Ausgaben und Einnahmen via OLS-Trend x
        Kalendermonats-Saisonalitaet + Residual-Bootstrap-KI; Guthaben
        wird vom tatsaechlichen ``balance_at`` fortgeschrieben
        (IBAN + Einzelwaehrung). Deterministisch, kein Future-Leak;
        interne Transfers werden nur aus dem Cashflow-Fit ausgeschlossen.

        Parameters: forecast_months 1-24 (6), lookback_months 3-36 (12),
        iban, confidence_level 0.5-0.99 (0.8), include_balance (True),
        include_goals (False), reference_date 'YYYY-MM-DD' (heute).

        include_goals=True ueberlagert die geplante Monatsrate aktiver
        Ziele (Sinking Funds) als deterministische planmaessige Ziehung auf
        die Monats-Prognosen (goals_draw, net_with_goals,
        balance_with_goals) -- opt-in, damit das Phase-1-Verhalten
        unveraendert bleibt. Ziehungen sind pro Ziel capped: Restbetrag
        (target - saved) und optional target_date (keine Ziehung ab dem
        Folgemonat des Zieltermins).

        include_manual_plan=True (Forecast UX AP1) ueberlagert aktive
        Plan-Items (Tabelle forecast_plan_items, keine Buchungen) als
        planmaessigen Netto-Betrag: plan.count/items/beyond_horizon,
        rest_month (Teilmonat mit Referenzdatum, deterministisch; bei
        Einzelwaehrung mit Guthaben: balance_with_plan) und pro
        Prognose-Monat manual_plan/net_with_plan/balance_with_plan.
        manual_start_balance (optional, wirkt auch ohne
        include_manual_plan) ersetzt den saldenbasierten Startsaldo
        (Kennzeichnung balance.start_balance_source = "manual").
        Defaults bleiben unveraendert: ohne die Opt-ins tauchen diese
        Keys nicht auf (byte-kompatibles Phase-1-Verhalten).
        """
        try:
            reference = self._parse_reference_date(params.get("reference_date"))
        except ValueError as exc:
            return {"success": False, "error": str(exc), "error_class": "invalid_reference_date"}

        forecast_months = self._coerce_int_param(params.get("forecast_months"), default=6)
        if forecast_months is None:
            return {"success": False, "error": "forecast_months must be an integer", "error_class": "invalid_param"}
        forecast_months = max(1, min(int(forecast_months), 24))

        lookback_months = self._coerce_int_param(params.get("lookback_months"), default=12)
        if lookback_months is None:
            lookback_months = 12
        lookback_months = max(3, min(int(lookback_months), 36))

        try:
            confidence = float(params.get("confidence_level", 0.8))
        except (TypeError, ValueError):
            return {"success": False, "error": "confidence_level must be a number", "error_class": "invalid_param"}
        confidence = max(0.5, min(confidence, 0.99))

        include_balance = bool(params.get("include_balance", True))
        include_goals = bool(params.get("include_goals", False))
        include_manual_plan = bool(params.get("include_manual_plan", False))
        manual_start_balance: Optional[float] = None
        if params.get("manual_start_balance") is not None:
            try:
                manual_start_balance = float(params.get("manual_start_balance"))
            except (TypeError, ValueError):
                return {
                    "success": False,
                    "error": "manual_start_balance must be a number",
                    "error_class": "invalid_param",
                }
            if not math.isfinite(manual_start_balance):
                return {
                    "success": False,
                    "error": "manual_start_balance must be a finite number",
                    "error_class": "invalid_param",
                }
        iban = params.get("iban") or None
        facts, error = self._facts_up_to(iban, reference)
        if error is not None:
            return error

        reference_key = (reference.year, reference.month)
        currencies = sorted({fact["currency"] for fact in facts})
        if not currencies:
            return {"success": False, "error": "Keine Buchungsdaten für die Prognose verfügbar", "error_class": "no_data"}

        notes: List[str] = []
        account_id = self._resolve_account_id(iban) if iban else None
        balance_start: Optional[float] = None
        if account_id is not None and len(currencies) == 1 and include_balance:
            balance_row = self._db.balance_at(
                account_id, reference.isoformat()
            )
            balance_start = balance_row.get("balance")
        elif iban and len(currencies) > 1:
            notes.append("Guthaben-Prognose entfällt: Konto führt mehrere Währungen")
        balance_start_source: Optional[str] = None
        if (
            manual_start_balance is not None
            and account_id is not None
            and len(currencies) == 1
            and include_balance
        ):
            # Datierbare Prognose-Annahme (AP1): ersetzt den
            # saldenbasierten Startwert; keine Buchung, kein Saldo-Write.
            balance_start = manual_start_balance
            balance_start_source = "manual"

        goals_overlay: Optional[Dict[str, Any]] = None
        goals_draw_steps: Dict[str, List[int]] = {}
        goals_draw_by_currency: Dict[str, int] = {}
        if include_goals:
            # Sinking Funds: aktiven Zielen mit geplanter Monatsrate wird eine
            # deterministische planmaessige Ziehung pro Prognose-Monat
            # zugerechnet (Status "active" + Rate > 0; kein Future-Leak,
            # Fortschritt nur bis Referenzdatum).
            # Cap pro Ziel (DoD#6): Ziehung endet, wenn der Restbetrag
            # (target - saved) aufgebraucht ist oder target_date erreicht
            # wurde (keine Ziehung ab dem Folgemonat des Zieltermins).
            active_goals = [
                goal
                for goal in self._db.list_goals(status="active")
                if goal.monthly_rate_cents and int(goal.monthly_rate_cents) > 0
            ]
            if iban:
                iban_norm = _normalize_iban(str(iban))
                active_goals = [
                    goal
                    for goal in active_goals
                    if _normalize_iban(goal.iban or "") == iban_norm
                ]
            goals: List[Dict[str, Any]] = []
            for goal in sorted(active_goals, key=lambda item: (item.currency or "", item.name)):
                assert goal.monthly_rate_cents is not None
                progress = self._db.goal_progress_asof(goal.id, reference)
                saved_cents = int(progress["saved_cents"])
                schedule = _goal_draw_schedule_cents(
                    goal.target_cents,
                    saved_cents,
                    int(goal.monthly_rate_cents),
                    goal.target_date,
                    reference,
                    forecast_months,
                )
                last_draw_month = max(
                    (i for i, cents in enumerate(schedule, start=1) if cents > 0), default=0
                )
                goals.append(
                    {
                        "goal_id": goal.id,
                        "name": goal.name,
                        "currency": goal.currency,
                        "iban": goal.iban,
                        "monthly_rate": round(_from_cents(int(goal.monthly_rate_cents)), 2),
                        "target_amount": round(_from_cents(goal.target_cents), 2),
                        "target_date": goal.target_date,
                        "saved": round(_from_cents(saved_cents), 2),
                        "remaining": round(
                            _from_cents(max(0, goal.target_cents - saved_cents)), 2
                        ),
                        "last_draw_month": last_draw_month,
                    }
                )
                if goal.currency:
                    cur_steps = goals_draw_steps.setdefault(goal.currency, [0] * forecast_months)
                    for idx in range(len(cur_steps)):
                        cur_steps[idx] += schedule[idx]
            goals_draw_by_currency = {
                cur: steps[0] for cur, steps in goals_draw_steps.items() if steps and steps[0] > 0
            }
            goals_overlay = {
                "count": len(goals),
                "goals": goals,
                "monthly_draw_by_currency": {
                    cur: round(_from_cents(cents), 2)
                    for cur, cents in sorted(goals_draw_by_currency.items())
                },
            }
            notes.append(
                "Goals-Overlay: geplante Monatsraten aktiver Ziele (capped durch "
                "Restbetrag/Zieltermin) als planmaessige Ziehung eingerechnet"
                if goals
                else "Goals-Overlay: keine aktiven Ziele mit geplanter Monatsrate"
            )

        plan_overlay: Optional[Dict[str, Any]] = None
        rest_month: Optional[Dict[str, Any]] = None
        plan_net_by_currency_month: Dict[str, Dict[str, int]] = {}
        if include_manual_plan:
            # Manual-Plan-Overlay (Forecast UX AP1): aktive Plan-Items aus
            # der eigenen Tabelle forecast_plan_items (keine Buchungen,
            # keine goal_contributions) mit due_date ab Referenzdatum.
            # Pro Waehrung/Monat als planmaessiger Netto-Betrag aggregiert
            # (Einnahme +, Ausgabe -); Items vor dem Referenzdatum werden
            # ignoriert (kein Future-Leak, D4), Items hinter dem Horizont
            # werden nur gezählt (beyond_horizon). Deterministisch: feste
            # DAO-Sortierung (due_date, iban, id), keine Zufallsquellen.
            active_items = (
                self._db.list_plan_items(status="active", iban=str(iban))
                if iban
                else self._db.list_plan_items(status="active")
            )
            horizon_end_key = self._next_month_key(reference_key, forecast_months)
            included_items: List[Any] = []
            beyond_horizon = 0
            rest_cents_by_currency: Dict[str, int] = {}
            for item in active_items:
                due = (item.due_date or "").strip()
                if not due or due < reference.isoformat():
                    continue
                item_key = (int(due[:4]), int(due[5:7]))
                if item_key > horizon_end_key:
                    beyond_horizon += 1
                    continue
                included_items.append(item)
                if item.currency:
                    cents = int(item.amount_cents) * (
                        1 if item.kind == "income" else -1
                    )
                    month_label = f"{item_key[0]:04d}-{item_key[1]:02d}"
                    cur_map = plan_net_by_currency_month.setdefault(item.currency, {})
                    cur_map[month_label] = cur_map.get(month_label, 0) + cents
                    if item_key == reference_key:
                        rest_cents_by_currency[item.currency] = (
                            rest_cents_by_currency.get(item.currency, 0) + cents
                        )
            plan_overlay = {
                "count": len(included_items),
                "items": [
                    {
                        "id": item.id,
                        "title": item.title,
                        "kind": item.kind,
                        "amount": round(_from_cents(int(item.amount_cents)), 2),
                        "currency": item.currency,
                        "iban": item.iban,
                        "due_date": item.due_date,
                        "source_type": item.source_type,
                        "status": item.status,
                        "revision": item.revision,
                    }
                    for item in included_items
                ],
                "beyond_horizon": beyond_horizon,
            }
            # Restmonat (Teilmonat mit Referenzdatum): die Prognose-Monate
            # beginnen erst im Folgemonat; Restmonat ist ein eigenes,
            # deterministisches Objekt (keine History-Proration, kein
            # Zufall): planmaessiger Netto-Betrag + Restlaufzeit, bei
            # Single-Currency mit Guthaben auch die Kette vom Startsaldo.
            last_day = calendar.monthrange(reference.year, reference.month)[1]
            period_end = reference.replace(day=last_day)
            rest_month = {
                "period_start": reference.isoformat(),
                "period_end": period_end.isoformat(),
                "days_remaining": (period_end - reference).days + 1,
            }
            if len(currencies) == 1:
                rest_cents = rest_cents_by_currency.get(currencies[0], 0)
                rest_net = round(_from_cents(rest_cents), 2)
                rest_month["manual_plan"] = rest_net
                rest_month["net_with_plan"] = rest_net
                if balance_start is not None:
                    rest_month["balance_with_plan"] = round(
                        balance_start + rest_net, 2
                    )
            notes.append(
                "Manual-Plan: aktive Plan-Items ab Referenzdatum als "
                "planmaessiger Netto-Betrag (Einnahmen +, Ausgaben -) "
                "eingerechnet"
                if included_items
                else "Manual-Plan: keine aktiven Plan-Items ab Referenzdatum"
            )

        results = []
        for currency in currencies:
            fit_state = self._fit_currency_series(facts, currency, reference_key, lookback_months)
            if fit_state is None:
                notes.append(f"{currency}: keine Buchungen im Fit-Fenster")
                continue
            months = self._project_months(
                fit_state, reference_key, forecast_months, confidence, balance_start
            )
            if include_goals and goals_draw_by_currency.get(currency):
                steps = goals_draw_steps[currency]
                cumulative_cents = 0
                for step, month in enumerate(months, start=1):
                    draw_cents = steps[step - 1] if step - 1 < len(steps) else 0
                    cumulative_cents += draw_cents
                    draw = round(_from_cents(draw_cents), 2)
                    month["goals_draw"] = draw
                    month["net_with_goals"] = round(month["net"] - draw, 2)
                    if "balance" in month:
                        cum = round(_from_cents(cumulative_cents), 2)
                        month["balance_with_goals"] = round(month["balance"] - cum, 2)
                        month["balance_low_with_goals"] = round(month["balance_low"] - cum, 2)
                        month["balance_high_with_goals"] = round(month["balance_high"] - cum, 2)
            if include_manual_plan:
                cur_plan = plan_net_by_currency_month.get(currency, {})
                cumulative_cents = 0
                for month in months:
                    month_cents = cur_plan.get(month["month"], 0)
                    cumulative_cents += month_cents
                    plan_net = round(_from_cents(month_cents), 2)
                    month["manual_plan"] = plan_net
                    month["net_with_plan"] = round(month["net"] + plan_net, 2)
                    if "balance" in month:
                        cum = round(_from_cents(cumulative_cents), 2)
                        month["balance_with_plan"] = round(month["balance"] + cum, 2)
                        month["balance_low_with_plan"] = round(month["balance_low"] + cum, 2)
                        month["balance_high_with_plan"] = round(month["balance_high"] + cum, 2)
            results.append(
                {
                    "currency": currency,
                    "months_used": fit_state["n"],
                    "seasonality_applied": fit_state["seasonality_applied"],
                    "recurring_monthly": round(fit_state["recurring_monthly"], 2),
                    "months": months,
                }
            )

        payload: Dict[str, Any] = {
            "success": True,
            "method": "schedule_first_hybrid",
            "reference_date": reference.isoformat(),
            "forecast_months": forecast_months,
            "lookback_months": lookback_months,
            "confidence_level": confidence,
            "currencies": currencies,
            "results": results,
            "goals": goals_overlay,
        }
        if include_manual_plan:
            # Additive Opt-in-Keys (D3: rest_month erscheint immer, auch
            # mit 0.0 -- stabiles Schema fuer UI/Tests).
            payload["plan"] = plan_overlay
            payload["rest_month"] = rest_month
        if balance_start is not None:
            balance_payload: Dict[str, Any] = {
                "start_balance": round(balance_start, 2),
                "currency": currencies[0],
            }
            if balance_start_source:
                balance_payload["start_balance_source"] = balance_start_source
            payload["balance"] = balance_payload
        else:
            payload["balance"] = None
        payload["notes"] = notes
        return payload

    # ------------------------------------------------------------------
    # AP2 S1-Finale: Serien-Tools (Forecast UX)
    #
    # Deterministische Lese-/Schreib-Tools ueber die Serien-Engine
    # (finance/series_engine.py) und die Serien-DAO (finance/db_schema.py).
    # Konvention wie alle Finance-Tools: {"success": bool, ...payload};
    # DAO-ValueError -> success=False + error_class (kein stiller Fallback).
    # Kandidaten bleiben 'pending' (nie auto-confirmed). Keine LLM-Calls,
    # keine Aenderungen an Bank-/Transaktions-Daten.
    # ------------------------------------------------------------------

    @staticmethod
    def _series_to_dict(series) -> Dict[str, Any]:
        """RecurringSeries-Dataclass -> LLM-/UI-Dict (Betraege als Dezimal)."""
        return {
            "id": series.id,
            "iban": series.iban,
            "currency": series.currency,
            "direction": series.direction,
            "cadence": series.cadence,
            "period_n": series.period_n,
            "anchor_day": series.anchor_day,
            "anchor_date": series.anchor_date,
            "amount": _from_cents(series.amount_cents),
            "counterparty": series.counterparty,
            "category_id": series.category_id,
            "title": series.title,
            "status": series.status,
            "source": series.source,
            "effective_from": series.effective_from,
            "effective_to": series.effective_to,
            "revision": series.revision,
        }

    @staticmethod
    def _candidate_to_dict(cand) -> Dict[str, Any]:
        """SeriesCandidateRow -> LLM-/UI-Dict (evidence als Liste geparst)."""
        evidence: List[Dict[str, Any]] = []
        if cand.evidence_json:
            try:
                parsed = json.loads(cand.evidence_json)
                if isinstance(parsed, list):
                    for item in parsed:
                        if isinstance(item, (list, tuple)) and len(item) == 3:
                            evidence.append(
                                {
                                    "transaction_id": int(item[0]),
                                    "date": str(item[1]),
                                    "amount": _from_cents(int(item[2])),
                                }
                            )
            except (TypeError, ValueError):
                evidence = []
        return {
            "fingerprint": cand.fingerprint,
            "iban": cand.iban,
            "currency": cand.currency,
            "direction": cand.direction,
            "counterparty": cand.counterparty,
            "cadence": cand.cadence,
            "period_n": cand.period_n,
            "anchor_day": cand.anchor_day,
            "anchor_date": cand.anchor_date,
            "amount": _from_cents(cand.amount_cents),
            "confidence": cand.confidence,
            "status": cand.status,
            "series_id": cand.series_id,
            "detected_at": cand.detected_at,
            "reviewed_at": cand.reviewed_at,
            "evidence": evidence,
        }

    @staticmethod
    def _exception_to_dict(exc) -> Dict[str, Any]:
        """SeriesExceptionRow -> LLM-/UI-Dict (Betrag als Dezimal oder None)."""
        return {
            "original_due_date": exc.original_due_date,
            "exception_type": exc.exception_type,
            "new_due_date": exc.new_due_date,
            "amount": (
                _from_cents(exc.amount_cents) if exc.amount_cents is not None else None
            ),
            "note": exc.note,
            "revision": exc.revision,
        }

    @staticmethod
    def _series_error_class(exc: ValueError) -> str:
        """DAO-ValueError -> stabiles error_class (Fail-Fast-Konvention)."""
        msg = str(exc).lower()
        if "not found" in msg:
            return "not_found"
        if "already" in msg or "only 'pending'" in msg or "no journal" in msg:
            return "conflict"
        return "invalid_param"

    @staticmethod
    def _coerce_series_id(params: Dict[str, Any]) -> Optional[int]:
        """series_id-Parameter (int > 0) normalisieren; ungültig -> None."""
        raw = params.get("series_id")
        if raw is None or isinstance(raw, bool):
            return None
        try:
            value = int(raw)
        except (TypeError, ValueError):
            return None
        return value if value > 0 else None

    @staticmethod
    def _coerce_iso_date(value: Any) -> Optional[str]:
        """ISO-Datum (YYYY-MM-DD) normalisieren; ungültig/leer -> None."""
        text = str(value or "").strip()
        if not text:
            return None
        try:
            return date.fromisoformat(text).isoformat()
        except ValueError:
            return None

    def list_series(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Wiederkehrende Serien auflisten (Filter: status/iban/direction/source).

        Read-Tool (AP2 S1-Finale). Beträge sind positive Dezimalzahlen;
        die Richtung (income/expense) steht im Feld ``direction``.
        """
        status = params.get("status")
        if status is not None:
            status = str(status).strip().lower()
            if status not in ("active", "paused", "ended"):
                return {
                    "success": False,
                    "error": "status muss 'active', 'paused' oder 'ended' sein",
                    "error_class": "invalid_param",
                }
        direction = params.get("direction")
        if direction is not None:
            direction = str(direction).strip().lower()
            if direction not in ("income", "expense"):
                return {
                    "success": False,
                    "error": "direction muss 'income' oder 'expense' sein",
                    "error_class": "invalid_param",
                }
        source = params.get("source")
        if source is not None:
            source = str(source).strip().lower()
            if source not in ("manual", "detected"):
                return {
                    "success": False,
                    "error": "source muss 'manual' oder 'detected' sein",
                    "error_class": "invalid_param",
                }
        iban = _normalize_iban(str(params.get("iban") or ""))
        try:
            series = self._db.list_series(
                status=status, iban=iban or None, direction=direction, source=source
            )
        except ValueError as exc:
            return {"success": False, "error": str(exc), "error_class": "invalid_param"}
        return {
            "success": True,
            "count": len(series),
            "series": [self._series_to_dict(s) for s in series],
        }

    def list_series_candidates(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Erkennungs-Kandidaten auflisten (Filter: status, Default 'pending').

        Read-Tool. Kandidaten sind Vorschläge — Bestätigung explizit via
        ``confirm_candidate`` (nie auto-confirmed).
        """
        status = params.get("status")
        if status is None:
            status = "pending"
        else:
            status = str(status).strip().lower()
            if status not in ("pending", "confirmed", "rejected"):
                return {
                    "success": False,
                    "error": "status muss 'pending', 'confirmed' oder 'rejected' sein",
                    "error_class": "invalid_param",
                }
        try:
            candidates = self._db.list_candidates(status=status)
        except ValueError as exc:
            return {"success": False, "error": str(exc), "error_class": "invalid_param"}
        return {
            "success": True,
            "count": len(candidates),
            "candidates": [self._candidate_to_dict(c) for c in candidates],
        }

    def series_calendar(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Planvorkommen aller Serien im Zeitfenster (AP2 F01: ALLE Vorkommen).

        Read-Tool. ``reference_date`` (Default heute) + ``days_ahead``
        (Default 30, 1–180) bilden das Fenster; aktive Serien werden über
        ``expand_series`` deterministisch expandiert; Ausnahmen (skip/move/
        amount) werden pro Original-Termin angewendet. ``include_paused``
        (Default False) enthält zusätzlich pausierte Serien.
        """
        try:
            reference = self._parse_reference_date(params.get("reference_date"))
        except ValueError:
            return {
                "success": False,
                "error": "Ungueltiges reference_date (erwartet YYYY-MM-DD)",
                "error_class": "invalid_param",
            }
        days_ahead = self._coerce_int_param(params.get("days_ahead"), default=30)
        if days_ahead is None or not 1 <= days_ahead <= 180:
            return {
                "success": False,
                "error": "days_ahead muss eine Ganzzahl zwischen 1 und 180 sein",
                "error_class": "invalid_param",
            }
        iban = _normalize_iban(str(params.get("iban") or ""))
        include_paused = bool(params.get("include_paused", False))
        window_start = reference
        window_end = reference + timedelta(days=days_ahead)
        try:
            all_series: List[Any] = []
            for st in (["active", "paused"] if include_paused else ["active"]):
                all_series.extend(self._db.list_series(status=st, iban=iban or None))
        except ValueError as exc:
            return {"success": False, "error": str(exc), "error_class": "invalid_param"}
        occurrences: List[Dict[str, Any]] = []
        for series in all_series:
            spec = SeriesSpec(
                key=f"series:{series.id}",
                iban=series.iban,
                currency=series.currency,
                direction=series.direction,
                cadence=series.cadence,
                period_n=series.period_n,
                anchor_day=series.anchor_day,
                anchor_date=series.anchor_date,
                amount_cents=series.amount_cents,
                counterparty=series.counterparty,
                effective_from=series.effective_from,
                effective_to=series.effective_to,
            )
            exceptions = {
                e.original_due_date: SeriesException(
                    key=spec.key,
                    original_due_date=e.original_due_date,
                    exception_type=e.exception_type,
                    new_due_date=e.new_due_date,
                    amount_cents=e.amount_cents,
                )
                for e in self._db.list_series_exceptions(series.id)
            }
            try:
                occs = expand_series(
                    spec,
                    exceptions=exceptions,
                    window_start=window_start.isoformat(),
                    window_end=window_end.isoformat(),
                )
            except ValueError as exc:
                return {
                    "success": False,
                    "error": str(exc),
                    "error_class": self._series_error_class(exc),
                }
            for occ in occs:
                occurrences.append(
                    {
                        "date": occ.date,
                        "original_date": occ.original_date,
                        "series_id": series.id,
                        "counterparty": series.counterparty,
                        "direction": series.direction,
                        "amount": _from_cents(occ.amount_cents),
                        "currency": series.currency,
                        "iban": series.iban,
                        "exception": occ.exception,
                    }
                )
        occurrences.sort(key=lambda item: (item["date"], int(item["series_id"] or 0)))
        return {
            "success": True,
            "reference_date": window_start.isoformat(),
            "window_start": window_start.isoformat(),
            "window_end": window_end.isoformat(),
            "count": len(occurrences),
            "occurrences": occurrences,
        }

    def detect_series_candidates(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Wiederkehrende Zahlungs-Muster aus den letzten Buchungen erkennen.

        Write-Tool (Ergebnis = Vorschlag). Scannt die Konten (optional via
        ``iban`` begrenzt) im Lookback-Fenster (``lookback_months``, Default
        24, 3–36; ``min_occurrences``, Default 2), wendet die
        deterministische ``detect_candidates``-Engine an und speichert die
        Kandidaten mit status 'pending'. Neue Kandidaten werden erstellt,
        bereits bekannte Fingerprint nur aktualisiert (Idempotenz).
        """
        lookback_months = self._coerce_int_param(params.get("lookback_months"), default=24)
        if lookback_months is None or not 3 <= lookback_months <= 36:
            return {
                "success": False,
                "error": "lookback_months muss eine Ganzzahl zwischen 3 und 36 sein",
                "error_class": "invalid_param",
            }
        min_occurrences = self._coerce_int_param(params.get("min_occurrences"), default=2)
        if min_occurrences is None or not 2 <= min_occurrences <= 24:
            return {
                "success": False,
                "error": "min_occurrences muss eine Ganzzahl zwischen 2 und 24 sein",
                "error_class": "invalid_param",
            }
        iban = _normalize_iban(str(params.get("iban") or ""))
        accounts = self._db.list_accounts()
        if iban:
            accounts = [a for a in accounts if (a.iban or "").upper() == iban.upper()]
            if not accounts:
                return {
                    "success": False,
                    "error": f"IBAN {iban} ist keinem Konto zugeordnet",
                    "error_class": "not_found",
                }
        reference = date.today()
        months_back = reference.year * 12 + reference.month - 1 - lookback_months
        start_date = date(months_back // 12, months_back % 12 + 1, 1).isoformat()
        end_date = reference.isoformat()
        facts: List[Dict[str, Any]] = []
        for acct in accounts:
            for row in self._db.list_analysis_facts(
                account_id=acct.id, start_date=start_date, end_date=end_date
            ):
                facts.append(
                    {
                        "iban": row.get("iban") or acct.iban,
                        "currency": row.get("currency") or acct.currency,
                        "counterparty": row.get("counterparty"),
                        "transaction_id": row.get("transaction_id"),
                        "date": row.get("booking_date"),
                        "amount_cents": row.get("amount_cents"),
                    }
                )
        if not facts:
            return {
                "success": True,
                "count": 0,
                "new_candidates": 0,
                "candidates": [],
                "note": "Keine Buchungen im Lookback-Fenster — nichts zu erkennen.",
            }
        candidates = detect_candidates(facts, min_occurrences=min_occurrences)
        if not candidates:
            return {
                "success": True,
                "count": 0,
                "new_candidates": 0,
                "candidates": [],
                "note": "Kein wiederkehrendes Muster gefunden (2+ identische Beobachtungen).",
            }
        rows: List[Any] = []
        created = 0
        for cand in candidates:
            evidence_json = json.dumps(
                [[tid, dt, amt] for (tid, dt, amt) in cand.evidence]
            )
            try:
                row, is_new = self._db.save_series_candidate(
                    fingerprint=cand.fingerprint,
                    iban=cand.iban,
                    currency=cand.currency,
                    direction=cand.direction,
                    counterparty=cand.counterparty,
                    cadence=cand.cadence,
                    period_n=cand.period_n,
                    anchor_day=cand.anchor_day,
                    anchor_date=cand.anchor_date,
                    amount_cents=cand.amount_cents,
                    evidence=evidence_json,
                    confidence=cand.confidence,
                )
            except ValueError as exc:
                return {
                    "success": False,
                    "error": str(exc),
                    "error_class": self._series_error_class(exc),
                }
            if is_new:
                created += 1
            rows.append(row)
        return {
            "success": True,
            "count": len(rows),
            "new_candidates": created,
            "candidates": [self._candidate_to_dict(r) for r in rows],
            "note": (
                "Kandidaten sind Vorschläge (status 'pending') — bitte prüfen und "
                "via confirm_candidate bzw. reject_candidate entscheiden."
            ),
        }

    def confirm_candidate(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Erkennungs-Kandidat als Serie bestätigen (optionale Korrekturen).

        Write-Tool. Der Kandidat muss status 'pending' sein. Optionale
        Korrekturfelder (``iban``, ``currency``, ``direction``, ``cadence``,
        ``period_n``, ``anchor_day``, ``anchor_date``, ``amount``,
        ``counterparty``, ``title``) überschreiben die erkannten Werte;
        ``status`` (Default 'active'), ``effective_from``/``effective_to``
        sind optional. Die Serie wird mit source='detected' angelegt und
        die Kandidaten-Verbindung (series_id) festgeschrieben.
        """
        fingerprint = str(params.get("fingerprint") or "").strip()
        if not fingerprint:
            return {
                "success": False,
                "error": "fingerprint ist erforderlich",
                "error_class": "invalid_param",
            }
        corrections: Dict[str, Any] = {}
        for field in ("iban", "currency", "direction", "cadence", "counterparty", "title"):
            value = params.get(field)
            if value is not None:
                text = str(value).strip()
                if text:
                    corrections[field] = text
        if "iban" in corrections:
            corrections["iban"] = _normalize_iban(corrections["iban"])
        if "currency" in corrections:
            corrections["currency"] = corrections["currency"].upper()
        if "direction" in corrections:
            corrections["direction"] = corrections["direction"].lower()
        if "cadence" in corrections and corrections["cadence"] not in VALID_CADENCES:
            return {
                "success": False,
                "error": f"cadence muss ein Wert aus {sorted(VALID_CADENCES)} sein",
                "error_class": "invalid_param",
            }
        period_n = self._coerce_int_param(params.get("period_n"))
        if period_n is not None:
            if period_n < 1 or period_n > 120:
                return {
                    "success": False,
                    "error": "period_n muss eine Ganzzahl zwischen 1 und 120 sein",
                    "error_class": "invalid_param",
                }
            corrections["period_n"] = period_n
        anchor_day = self._coerce_int_param(params.get("anchor_day"))
        if anchor_day is not None:
            if not 1 <= anchor_day <= 28:
                return {
                    "success": False,
                    "error": "anchor_day muss eine Ganzzahl zwischen 1 und 28 sein",
                    "error_class": "invalid_param",
                }
            corrections["anchor_day"] = anchor_day
        for field in ("anchor_date", "effective_from", "effective_to"):
            value = params.get(field)
            if value is not None:
                iso = self._coerce_iso_date(value)
                if iso is None:
                    return {
                        "success": False,
                        "error": f"{field} muss ein ISO-Datum (YYYY-MM-DD) sein",
                        "error_class": "invalid_param",
                    }
                corrections[field] = iso
        amount = params.get("amount")
        if amount is not None:
            try:
                cents = _to_cents(float(amount))
            except (TypeError, ValueError):
                cents = None
            if cents is None:
                return {
                    "success": False,
                    "error": "amount muss eine positive Zahl sein",
                    "error_class": "invalid_param",
                }
            corrections["amount_cents"] = cents
        status = params.get("status")
        if status is None:
            status = "active"
        else:
            status = str(status).strip().lower()
            if status not in ("active", "paused"):
                return {
                    "success": False,
                    "error": "status muss 'active' oder 'paused' sein",
                    "error_class": "invalid_param",
                }
        try:
            candidate, series = self._db.confirm_candidate(
                fingerprint, status=status, **corrections
            )
        except ValueError as exc:
            return {"success": False, "error": str(exc), "error_class": self._series_error_class(exc)}
        return {
            "success": True,
            "candidate": self._candidate_to_dict(candidate),
            "series": self._series_to_dict(series),
        }

    def reject_candidate(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Erkennungs-Kandidat ablehnen (keine Serie wird angelegt).

        Write-Tool. Der Kandidat muss status 'pending' sein; die Ablehnung
        ist final (reviewed_at wird gesetzt).
        """
        fingerprint = str(params.get("fingerprint") or "").strip()
        if not fingerprint:
            return {
                "success": False,
                "error": "fingerprint ist erforderlich",
                "error_class": "invalid_param",
            }
        try:
            candidate = self._db.reject_candidate(fingerprint)
        except ValueError as exc:
            return {"success": False, "error": str(exc), "error_class": self._series_error_class(exc)}
        return {"success": True, "candidate": self._candidate_to_dict(candidate)}

    def _set_series_status(
        self, params: Dict[str, Any], target_status: str
    ) -> Dict[str, Any]:
        """Gemeinsame Logik für pause/resume/end (series_id -> Zielstatus)."""
        series_id = self._coerce_series_id(params)
        if series_id is None:
            return {
                "success": False,
                "error": "series_id ist erforderlich (positive ganze Zahl)",
                "error_class": "invalid_param",
            }
        try:
            series = self._db.set_series_status(series_id, target_status)
        except ValueError as exc:
            return {"success": False, "error": str(exc), "error_class": self._series_error_class(exc)}
        return {"success": True, "series": self._series_to_dict(series)}

    def pause_series(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Aktive Serie pausieren (status 'paused'; Fortführung via resume_series)."""
        return self._set_series_status(params, "paused")

    def resume_series(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Pausierte Serie wieder aktivieren (status 'active')."""
        return self._set_series_status(params, "active")

    def end_series(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Serie beenden (status 'ended'; nicht mehr im Kalender, nicht umkehrbar)."""
        return self._set_series_status(params, "ended")

    def _set_occurrence_exception(
        self, params: Dict[str, Any], exception_type: str, **extra: Any
    ) -> Dict[str, Any]:
        """Gemeinsame Logik für skip/move/amount-Ausnahmen auf ein Vorkommen."""
        series_id = self._coerce_series_id(params)
        if series_id is None:
            return {
                "success": False,
                "error": "series_id ist erforderlich (positive ganze Zahl)",
                "error_class": "invalid_param",
            }
        due_date = self._coerce_iso_date(params.get("due_date"))
        if due_date is None:
            return {
                "success": False,
                "error": "due_date ist erforderlich (ISO-Datum YYYY-MM-DD)",
                "error_class": "invalid_param",
            }
        note = params.get("note")
        note = str(note).strip() if note is not None else None
        try:
            exception = self._db.set_series_exception(
                series_id=series_id,
                original_due_date=due_date,
                exception_type=exception_type,
                note=note,
                **extra,
            )
        except ValueError as exc:
            return {"success": False, "error": str(exc), "error_class": self._series_error_class(exc)}
        return {
            "success": True,
            "series_id": series_id,
            "exception": self._exception_to_dict(exception),
        }

    def skip_occurrence(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Einzelnes Vorkommen überspringen (F02, exception_type 'skip').

        Write-Tool. ``due_date`` ist der Original-Termin des Vorkommens;
        das Vorkommen verschwindet aus dem Kalender (optional mit ``note``).
        """
        return self._set_occurrence_exception(params, "skip")

    def move_occurrence(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Einzelnes Vorkommen auf neuen Termin verschieben (F02, 'move').

        Write-Tool. ``due_date`` = Original-Termin, ``new_due_date`` =
        Zieltermin (beide ISO); optional ``note``.
        """
        new_due_date = self._coerce_iso_date(params.get("new_due_date"))
        if new_due_date is None:
            return {
                "success": False,
                "error": "new_due_date ist erforderlich (ISO-Datum YYYY-MM-DD)",
                "error_class": "invalid_param",
            }
        return self._set_occurrence_exception(params, "move", new_due_date=new_due_date)

    def change_occurrence_amount(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Betrag eines einzelnen Vorkommens ändern (F02, 'amount').

        Write-Tool. ``due_date`` = Original-Termin, ``amount`` = neuer Betrag
        (positive Zahl, Währung der Serie); optional ``note``.
        """
        amount = params.get("amount")
        if amount is None:
            return {
                "success": False,
                "error": "amount ist erforderlich (positive Zahl)",
                "error_class": "invalid_param",
            }
        try:
            cents = _to_cents(float(amount))
        except (TypeError, ValueError):
            cents = None
        if cents is None:
            return {
                "success": False,
                "error": "amount muss eine positive Zahl sein",
                "error_class": "invalid_param",
            }
        return self._set_occurrence_exception(params, "amount", amount_cents=cents)

    def _fit_currency_series(
        self,
        facts: List[Dict[str, Any]],
        currency: str,
        reference_key: Tuple[int, int],
        lookback_months: int,
    ) -> Optional[Dict[str, Any]]:
        """Waehrungs-Monatsreihen in recurring/variable/Einnahmen zerlegen und fitten.

        Wiederkehrende Paare mit konstantem Betrag sind der
        deterministische Plan; variable Wiederkehrende (z. B.
        Lebensmittel) gehoren in den stochastischen Rest.
        Liefert None, wenn im Fit-Fenster keine Buchungen existieren.
        """
        currency_facts = [fact for fact in facts if fact["currency"] == currency]
        expense_pairs = self._expense_recurring_pairs(
            currency_facts, min_occurrences=2, fixed_only=True
        )

        monthly_variable: Dict[str, float] = {}
        monthly_income: Dict[str, float] = {}
        monthly_recurring: Dict[str, float] = {}
        for fact in currency_facts:
            month = fact["month"]
            cents = int(fact["amount_cents"])
            if cents > 0:
                monthly_income[month] = monthly_income.get(month, 0.0) + _from_cents(cents)
            elif (fact["currency"], fact["counterparty"]) in expense_pairs:
                monthly_recurring[month] = monthly_recurring.get(month, 0.0) + _from_cents(-cents)
            else:
                monthly_variable[month] = monthly_variable.get(month, 0.0) + _from_cents(-cents)

        all_months = set(monthly_variable) | set(monthly_income) | set(monthly_recurring)
        if not all_months:
            return None
        first_data_key = min(self._month_key(month) for month in all_months)
        window = [
            self._next_month_key(reference_key, -offset)
            for offset in range(lookback_months - 1, -1, -1)
        ]
        fit_months = [key for key in window if key >= first_data_key]
        fit_labels = [f"{key[0]:04d}-{key[1]:02d}" for key in fit_months]
        cal_months = [key[1] for key in fit_months]
        variable_series = [monthly_variable.get(label, 0.0) for label in fit_labels]
        income_series = [monthly_income.get(label, 0.0) for label in fit_labels]
        recurring_series = [monthly_recurring.get(label, 0.0) for label in fit_labels]

        n = len(fit_months)
        var_a, var_b = self._fit_trend(variable_series)
        inc_a, inc_b = self._fit_trend(income_series)
        var_index = self._seasonal_index(list(zip(cal_months, variable_series)))
        inc_index = self._seasonal_index(list(zip(cal_months, income_series)))
        var_fit = [(var_a + var_b * i) * var_index[cal_months[i]] for i in range(n)]
        inc_fit = [(inc_a + inc_b * i) * inc_index[cal_months[i]] for i in range(n)]
        return {
            "n": n,
            "var": (var_a, var_b, var_index),
            "inc": (inc_a, inc_b, inc_index),
            "var_residuals": [observed - fitted for observed, fitted in zip(variable_series, var_fit)],
            "inc_residuals": [observed - fitted for observed, fitted in zip(income_series, inc_fit)],
            "recurring_monthly": sum(recurring_series) / n if n else 0.0,
            "seasonality_applied": (
                any(value != 1.0 for value in var_index.values())
                or any(value != 1.0 for value in inc_index.values())
            ),
        }

    def _project_months(
        self,
        fit_state: Dict[str, Any],
        reference_key: Tuple[int, int],
        forecast_months: int,
        confidence: float,
        balance_start: Optional[float],
    ) -> List[Dict[str, Any]]:
        """Prognose-Monate (Punkt + Bootstrap-KI + optionale Guthaben-Kette)."""
        var_a, var_b, var_index = fit_state["var"]
        inc_a, inc_b, inc_index = fit_state["inc"]
        n = fit_state["n"]
        recurring = fit_state["recurring_monthly"]
        months = []
        running = balance_start
        for step in range(1, forecast_months + 1):
            key = self._next_month_key(reference_key, step)
            trend_index = n + step - 1
            variable_point = max(0.0, (var_a + var_b * trend_index) * var_index[key[1]])
            income_point = max(0.0, (inc_a + inc_b * trend_index) * inc_index[key[1]])
            variable_low, variable_high = self._bootstrap_interval(
                variable_point, fit_state["var_residuals"], confidence
            )
            income_low, income_high = self._bootstrap_interval(
                income_point, fit_state["inc_residuals"], confidence
            )
            payload: Dict[str, Any] = {
                "month": f"{key[0]:04d}-{key[1]:02d}",
                "income": round(income_point, 2),
                "recurring": round(recurring, 2),
                "variable": round(variable_point, 2),
                "variable_low": round(variable_low, 2),
                "variable_high": round(variable_high, 2),
                "income_low": round(income_low, 2),
                "income_high": round(income_high, 2),
                "expense_total": round(recurring + variable_point, 2),
                "net": round(income_point - recurring - variable_point, 2),
            }
            if balance_start is not None and running is not None:
                payload["balance"] = round(running + payload["net"], 2)
                payload["balance_low"] = round(
                    running + income_low - recurring - variable_high, 2
                )
                payload["balance_high"] = round(
                    running + income_high - recurring - variable_low, 2
                )
                running += payload["net"]
            months.append(payload)
        return months

    def subscription_audit(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Abo-/Recurring-Audit (Phase 1 SOTA): Kosten, Trends, Preisänderungen.

        Wiederkehrende Ausgabengruppen (>= 2 Buchungen) werden nach
        monatlichen Durchschnitt, Trend, letzter Preisänderung,
        Jahres-/Monatskosten und Abo-Heuristik zusammengefasst.
        Währungsumrechnung ist bewusst NICHT enthalten (keine Kursdaten
        lokal verfügbar) -> Summen nur bei einheitlicher Währung.

        Parameters
        ----------
        iban: optionales Konten-Filter (IBAN)
        """
        iban = params.get("iban") or None
        try:
            reference = self._parse_reference_date(params.get("reference_date"))
        except ValueError as exc:
            return {"success": False, "error": str(exc), "error_class": "invalid_reference_date"}
        facts, error = self._facts_up_to(iban, reference)
        if error is not None:
            return error

        groups = self._recurring_groups(facts, min_occurrences=2)
        expense_pairs = self._expense_recurring_pairs(facts, min_occurrences=2)
        audits = []
        for group in groups:
            if (group["currency"], group["counterparty"]) not in expense_pairs:
                continue
            counterparty = group["counterparty"]
            expenses = sorted(
                (
                    fact
                    for fact in facts
                    if fact["counterparty"] == counterparty and int(fact["amount_cents"]) < 0
                ),
                key=lambda fact: fact["booking_date"],
            )
            amounts = [abs(_from_cents(int(fact["amount_cents"]))) for fact in expenses]
            price_change, changed_price = self._detect_price_change(
                amounts, expenses[-1]["booking_date"]
            )
            category = next(
                (fact["category"] for fact in facts if fact["counterparty"] == counterparty),
                None,
            )
            if category in (None, "", "(uncategorized)"):
                category = None
            # Nach echter Preisveraenderung gilt der aktuelle Preis als
            # Monatskosten; sonst der Durchschnitt (variable Wiederkehrende).
            monthly = changed_price if changed_price is not None else group["average_expense"]
            last_amount = amounts[-1]
            audits.append(
                {
                    "counterparty": counterparty,
                    "category": category,
                    "currency": group["currency"],
                    "monthly_cost": monthly,
                    "annual_cost": round(monthly * 12, 2),
                    "last_amount": last_amount,
                    "price_change": price_change,
                    "occurrences": group["occurrences"],
                    "last_seen": expenses[-1]["booking_date"],
                    "is_fixed": group["is_fixed"],
                    "subscription_like": self._is_subscription_like(counterparty, category),
                }
            )
        audits.sort(key=lambda item: (-item["monthly_cost"], item["counterparty"]))
        single_currency = next(
            (item["currency"] for item in audits if len({a["currency"] for a in audits}) == 1),
            None,
        )
        return {
            "success": True,
            "method": "recurring_audit",
            "count": len(audits),
            "total_monthly": (
                round(sum(item["monthly_cost"] for item in audits), 2) if single_currency else None
            ),
            "total_annual": (
                round(sum(item["annual_cost"] for item in audits), 2) if single_currency else None
            ),
            "currency": single_currency,
            "groups": audits,
        }

    def set_goal_status(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Aendert den Status eines Sparziels (active/paused/achieved/archived)."""
        goal_id = self._coerce_int_param(params.get("goal_id"))
        if goal_id is None:
            return {"success": False, "error": "goal_id required (int)", "error_class": "missing_param"}
        status = str(params.get("status") or "").strip().lower()
        if status not in VALID_GOAL_STATUSES:
            return {
                "success": False,
                "error": f"status must be one of {sorted(VALID_GOAL_STATUSES)}",
                "error_class": "invalid_param",
            }
        if self._db.get_goal(goal_id) is None:
            return {"success": False, "error": f"goal {goal_id} not found", "error_class": "goal_not_found"}
        self._db.set_goal_status(goal_id, status)
        goal = self._db.get_goal(goal_id)
        return {"success": True, "goal_id": goal_id, "status": status, "goal": self._goal_dict(goal)}

    def delete_goal(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Loescht ein Sparziel (Ziel-Beitraege fallen per Cascade ab)."""
        goal_id = self._coerce_int_param(params.get("goal_id"))
        if goal_id is None:
            return {"success": False, "error": "goal_id required (int)", "error_class": "missing_param"}
        if self._db.get_goal(goal_id) is None:
            return {"success": False, "error": f"goal {goal_id} not found", "error_class": "goal_not_found"}
        removed = len(self._db.list_contributions(goal_id))
        self._db.delete_goal(goal_id)
        return {"success": True, "goal_id": goal_id, "removed_contributions": removed}

    def assign_goal_contribution(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Ordnet eine Buchung als Beitrag einem Sparziel zu (1:1)."""
        goal_id = self._coerce_int_param(params.get("goal_id"))
        transaction_id = self._coerce_int_param(params.get("transaction_id"))
        if goal_id is None or transaction_id is None:
            return {
                "success": False,
                "error": "goal_id and transaction_id required (int)",
                "error_class": "missing_param",
            }
        if self._db.get_goal(goal_id) is None:
            return {"success": False, "error": f"goal {goal_id} not found", "error_class": "goal_not_found"}
        with self._db._connect() as conn:
            tx = conn.execute(
                "SELECT id, counterparty, booking_date, amount_cents "
                "FROM transactions WHERE id = ?",
                (transaction_id,),
            ).fetchone()
        if tx is None:
            return {
                "success": False,
                "error": f"transaction {transaction_id} not found",
                "error_class": "tx_not_found",
            }
        with self._db._connect() as conn:
            existing = conn.execute(
                "SELECT goal_id FROM goal_contributions WHERE transaction_id = ?",
                (transaction_id,),
            ).fetchone()
        if existing is not None:
            return {
                "success": False,
                "error": f"transaction {transaction_id} already assigned to goal {existing['goal_id']}",
                "error_class": "conflict",
            }
        try:
            contribution_id = self._db.assign_contribution(goal_id, transaction_id)
        except ValueError as exc:
            return {"success": False, "error": str(exc), "error_class": "conflict"}
        progress = self._db.goal_progress(goal_id)
        return {
            "success": True,
            "contribution_id": contribution_id,
            "goal_id": goal_id,
            "transaction_id": transaction_id,
            "progress": progress,
        }

    def unassign_goal_contribution(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Entfernt einen Ziel-Beitrag (die Buchung selbst bleibt erhalten).

        Referenz entweder per ``contribution_id`` (exakt) oder per
        ``goal_id`` + ``transaction_id`` (Pair-Auflösung -- entspricht dem
        Tool-Schema und ist LLM-freundlich, weil kein Zwischen-Liste-Call
        nötig ist).
        """
        contribution_id = self._coerce_int_param(params.get("contribution_id"))
        goal_id = self._coerce_int_param(params.get("goal_id"))
        transaction_id = self._coerce_int_param(params.get("transaction_id"))
        if contribution_id is None and (goal_id is None or transaction_id is None):
            return {
                "success": False,
                "error": (
                    "contribution_id required (int) -- oder goal_id + transaction_id"
                ),
                "error_class": "missing_param",
            }
        if contribution_id is None:
            assert goal_id is not None and transaction_id is not None
            contribution_id = self._db.find_contribution_id(goal_id, transaction_id)
            if contribution_id is None:
                return {
                    "success": False,
                    "error": (
                        f"no contribution for goal {goal_id} and "
                        f"transaction {transaction_id}"
                    ),
                    "error_class": "contribution_not_found",
                }
        try:
            self._db.unassign_contribution(contribution_id)
        except ValueError as exc:
            return {"success": False, "error": str(exc), "error_class": "contribution_not_found"}
        return {"success": True, "contribution_id": contribution_id}

    def list_goal_contributions(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Alle einer Ziel zugeordneten Buchungen inkl. Fortschritt."""
        goal_id = self._coerce_int_param(params.get("goal_id"))
        if goal_id is None:
            return {"success": False, "error": "goal_id required (int)", "error_class": "missing_param"}
        if self._db.get_goal(goal_id) is None:
            return {"success": False, "error": f"goal {goal_id} not found", "error_class": "goal_not_found"}
        contributions = self._db.list_contributions(goal_id)
        tx_map: Dict[int, Dict[str, Any]] = {}
        tx_ids = [c.transaction_id for c in contributions]
        if tx_ids:
            placeholders = ",".join("?" * len(tx_ids))
            with self._db._connect() as conn:
                rows = conn.execute(
                    "SELECT id, counterparty, booking_date, amount_cents "
                    f"FROM transactions WHERE id IN ({placeholders})",
                    tx_ids,
                ).fetchall()
            for r in rows:
                tx_map[int(r["id"])] = {
                    "transaction_id": int(r["id"]),
                    "counterparty": r["counterparty"],
                    "booking_date": r["booking_date"],
                    "amount": _from_cents(int(r["amount_cents"])),
                }
        items = [
            {
                "contribution_id": c.id,
                "transaction_id": c.transaction_id,
                "amount": _from_cents(c.amount_cents),
                "source": c.source,
                "created_at": c.created_at,
                "transaction": tx_map.get(c.transaction_id),
            }
            for c in contributions
        ]
        progress = self._db.goal_progress(goal_id)
        return {
            "success": True,
            "goal_id": goal_id,
            "count": len(items),
            "contributions": items,
            "progress": progress,
        }

    # ------------------------------------------------------------------
    # Goals / Sinking Funds (Finance SOTA Phase 2, 2026-09)
    # Deterministisch: reine DB-Fakten + lineare Fortschritts-Projektion.
    # ------------------------------------------------------------------

    def _goal_dict(self, goal: Any) -> Dict[str, Any]:
        """Goal-Datenklasse -> JSON-serialisierbarer Payload (Cents -> Betrag)."""
        return {
            "goal_id": goal.id,
            "name": goal.name,
            "iban": goal.iban,
            "currency": goal.currency,
            "target_amount": _from_cents(goal.target_cents),
            "target_date": goal.target_date,
            "monthly_rate": (
                _from_cents(goal.monthly_rate_cents)
                if goal.monthly_rate_cents is not None
                else None
            ),
            "status": goal.status,
            "notes": goal.notes,
            "created_at": goal.created_at,
            "updated_at": goal.updated_at,
        }

    def _goal_projection(self, goal: Any, progress: Dict[str, Any]) -> Dict[str, Any]:
        """Deterministische Ziel-Projektion (Sinking-Fund-Tempo).

        months_to_target = Restziel / Monatsrate (Ceiling, ganze Monate);
        projected_month = heute + months_to_target (Kalenderarithmetik);
        on_track = projected_month <= target_date (nur wenn beide gesetzt).
        """
        remaining = int(progress.get("remaining_cents") or 0)
        rate = int(goal.monthly_rate_cents or 0)
        if remaining <= 0:
            months_to_target: Optional[int] = 0
        elif rate > 0:
            months_to_target = -(-remaining // rate)  # ceil, ganz
        else:
            months_to_target = None
        projected_month: Optional[str] = None
        if months_to_target is not None:
            today = date.today()
            total = (today.month - 1) + months_to_target
            projected_month = f"{today.year + total // 12:04d}-{total % 12 + 1:02d}"
        target_date = goal.target_date
        on_track: Optional[bool] = None
        if target_date and projected_month is not None:
            on_track = projected_month[:7] <= target_date[:7]
        return {
            "months_to_target": months_to_target,
            "projected_month": projected_month,
            "on_track": on_track,
        }

    @staticmethod
    def _coerce_money_amount(value: Any) -> Optional[float]:
        """LLM-Parameter -> Float-Betrag (kein stiller Fallback, None bei Fehlschlag).

        Akzeptiert Zahlen und Strings wie '1234.56', '1234,56',
        '1.234,56' (de-Format) bzw. '1,234.56' (us-Format).
        """
        if isinstance(value, bool) or value is None:
            return None
        if isinstance(value, (int, float)):
            return float(value)
        text = str(value).strip().replace("<|\"|>", "").strip('"').strip("'")
        text = text.replace(" ", "").replace("\u00a0", "")
        if "," in text and "." in text:
            if text.rfind(",") > text.rfind("."):
                text = text.replace(".", "").replace(",", ".")
            else:
                text = text.replace(",", "")
        elif "," in text:
            text = text.replace(",", ".")
        try:
            return float(text)
        except ValueError:
            return None

    def upsert_goal(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Erstellt oder aktualisiert ein Sparziel (Schluessel: name + iban)."""
        name = str(params.get("name") or "").strip()
        iban = _normalize_iban(str(params.get("iban") or ""))
        if not name or not iban:
            return {
                "success": False,
                "error": "name and iban are required",
                "error_class": "missing_param",
            }
        target_amount = self._coerce_money_amount(params.get("target_amount"))
        if target_amount is None or target_amount <= 0:
            return {
                "success": False,
                "error": "target_amount must be a positive number (Betrag in der Wahrung)",
                "error_class": "invalid_param",
            }
        monthly_rate = self._coerce_money_amount(params.get("monthly_rate"))
        if params.get("monthly_rate") is not None and monthly_rate is None:
            return {
                "success": False,
                "error": "monthly_rate must be a number >= 0",
                "error_class": "invalid_param",
            }
        if monthly_rate is not None and monthly_rate < 0:
            return {
                "success": False,
                "error": "monthly_rate must be >= 0",
                "error_class": "invalid_param",
            }
        status = str(params.get("status") or "active").strip().lower()
        if status not in VALID_GOAL_STATUSES:
            return {
                "success": False,
                "error": f"status must be one of {sorted(VALID_GOAL_STATUSES)}",
                "error_class": "invalid_param",
            }
        target_date = str(params["target_date"]).strip() if params.get("target_date") else None
        currency = str(params["currency"]).strip().upper() if params.get("currency") else None
        try:
            goal_id = self._db.upsert_goal(
                name=name,
                iban=iban,
                target_cents=_to_cents(target_amount),
                currency=currency,
                target_date=target_date,
                monthly_rate_cents=_to_cents(monthly_rate) if monthly_rate is not None else None,
                status=status,
                notes=str(params["notes"]).strip() if params.get("notes") else None,
            )
        except ValueError as exc:
            return {"success": False, "error": str(exc), "error_class": "invalid_goal"}
        goal = self._db.get_goal(goal_id)
        progress = self._db.goal_progress(goal_id)
        return {
            "success": True,
            "goal_id": goal_id,
            "goal": self._goal_dict(goal),
            "progress": progress,
            "projection": self._goal_projection(goal, progress),
        }

    def list_goals(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Liste aller Sparziele, optional nach Status und Konto gefiltert."""
        status = str(params["status"]).strip().lower() if params.get("status") else None
        if status is not None and status not in VALID_GOAL_STATUSES:
            return {
                "success": False,
                "error": f"status must be one of {sorted(VALID_GOAL_STATUSES)}",
                "error_class": "invalid_param",
            }
        iban = _normalize_iban(str(params["iban"])) if params.get("iban") else None
        goals = self._db.list_goals(status=status, iban=iban)
        items: List[Dict[str, Any]] = []
        for goal in goals:
            progress = self._db.goal_progress(goal.id)
            items.append(
                {
                    "goal": self._goal_dict(goal),
                    "progress": progress,
                    "projection": self._goal_projection(goal, progress),
                }
            )
        return {"success": True, "count": len(items), "goals": items}

    def get_goal(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Detailansicht eines Sparziels inkl. Fortschritt und Projektion."""
        goal_id = self._coerce_int_param(params.get("goal_id"))
        if goal_id is None:
            return {"success": False, "error": "goal_id required (int)", "error_class": "missing_param"}
        goal = self._db.get_goal(goal_id)
        if goal is None:
            return {
                "success": False,
                "error": f"goal {goal_id} not found",
                "error_class": "goal_not_found",
            }
        progress = self._db.goal_progress(goal_id)
        return {
            "success": True,
            "goal": self._goal_dict(goal),
            "progress": progress,
            "projection": self._goal_projection(goal, progress),
        }

    # =================================================================
    # Finance SOTA Phase 2: Ziel-Projektion + Kandidaten-Erkennung
    # (deterministisch, kein Future-Leak, Referenzdatum explizit)
    # =================================================================

    def _history_rate_cents(
        self, goal: "Goal", reference: date
    ) -> Optional[int]:
        """Durchschnittliche Monatsrate aus der Ziel-Beitrags-Historie.

        Nur Buchungsmonate bis (inklusive) des Referenzmonats fließen ein
        (kein Future-Leak); max. die letzten 12 Monate werden gemittelt.
        ``None`` wenn keine Historie existiert.
        """
        monthly: Dict[Tuple[int, int], int] = {}
        with self._db._connect() as conn:
            rows = conn.execute(
                "SELECT t.booking_date, gc.amount_cents "
                "FROM goal_contributions gc "
                "JOIN transactions t ON t.id = gc.transaction_id "
                "WHERE gc.goal_id = ?",
                (goal.id,),
            ).fetchall()
        for row in rows:
            try:
                tx_date = date.fromisoformat(str(row["booking_date"]))
            except ValueError:
                continue
            key = (tx_date.year, tx_date.month)
            if tx_date > reference:
                continue  # nach Referenzdatum -> kein Future-Leak
            monthly[key] = monthly.get(key, 0) + int(row["amount_cents"])
        keys = sorted(monthly)
        if not keys:
            return None
        recent = keys[-12:]
        total = sum(monthly[key] for key in recent)
        return int(round(total / len(recent)))

    def project_goal(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Deterministische Ziel-Projektion (Finance SOTA Phase 2).

        Projektion der Zielentwicklung aus dem Stand zum Referenzdatum
        (``reference_date``, default heute; Beiträge NACH dem Referenzdatum
        fließen nie ein) und der Sparrate -- Priorität:
        ``rate`` (explizit) > geplante ``monthly_rate`` des Ziels >
        Durchschnitt der Beitrags-Historie (max. 12 Monate). Liefert
        Erreichbarkeits-Monat, verbleibende Monate, erforderliche Rate
        für ``target_date``, On-/Off-Track und die Monats-Serie
        (Horizon ``horizon_months``, 1-60, default 24).

        Parameters
        ----------
        goal_id: Ziel-ID (int) -- ODER name + iban zur Auflösung
        rate: optionale explizite Monatsrate (positiv, Zielwährung)
        reference_date: 'YYYY-MM-DD' (default heute)
        horizon_months: 1-60 (default 24)
        """
        goal_id = self._coerce_int_param(params.get("goal_id"))
        name = str(params.get("name") or "").strip()
        iban = _normalize_iban(str(params.get("iban") or ""))
        if goal_id is None and not (name and iban):
            return {
                "success": False,
                "error": "goal_id required (int) -- oder name + iban",
                "error_class": "missing_param",
            }
        if goal_id is not None:
            goal = self._db.get_goal(goal_id)
        else:
            goal = next(
                (
                    g
                    for g in self._db.list_goals()
                    if g.name == name and _normalize_iban(g.iban) == iban
                ),
                None,
            )
        if goal is None:
            return {
                "success": False,
                "error": "goal not found",
                "error_class": "goal_not_found",
            }

        try:
            reference = self._parse_reference_date(params.get("reference_date"))
        except ValueError as exc:
            return {"success": False, "error": str(exc), "error_class": "invalid_reference_date"}

        horizon = self._coerce_int_param(params.get("horizon_months"), default=24)
        if horizon is None or not 1 <= int(horizon) <= 60:
            return {
                "success": False,
                "error": "horizon_months must be 1..60",
                "error_class": "invalid_param",
            }
        horizon = int(horizon)

        # Referenzdatum-Fortschritt (deterministisch, kein Future-Leak)
        progress = self._db.goal_progress_asof(goal.id, reference)
        saved = int(progress["saved_cents"])
        target = int(progress["target_cents"])
        remaining = max(0, target - saved)

        # Rate-Auflösung: explizit > geplant > Historie
        rate_cents: Optional[int] = None
        method = "none"
        if params.get("rate") is not None:
            rate_value = self._coerce_money_amount(params.get("rate"))
            if rate_value is None or rate_value <= 0:
                return {
                    "success": False,
                    "error": "rate must be a positive number",
                    "error_class": "invalid_param",
                }
            rate_cents = _to_cents(rate_value)
            method = "explicit"
        elif goal.monthly_rate_cents is not None and int(goal.monthly_rate_cents) > 0:
            rate_cents = int(goal.monthly_rate_cents)
            method = "planned"
        else:
            history_cents = self._history_rate_cents(goal, reference)
            if history_cents is not None and history_cents > 0:
                rate_cents = history_cents
                method = "history"

        # Ziel-Termin-Bewertung (deterministisch)
        overdue = False
        months_until_target_date: Optional[int] = None
        required_rate_for_target_date: Optional[float] = None
        if goal.target_date:
            target_date = date.fromisoformat(goal.target_date)
            months_until_target_date = (
                (target_date.year - reference.year) * 12
                + (target_date.month - reference.month)
            )
            if target_date < reference:
                overdue = True
            else:
                if remaining > 0:
                    if months_until_target_date == 0:
                        # Fällig diesen Monat: Restbetrag sofort erforderlich
                        required_rate_for_target_date = round(_from_cents(remaining), 2)
                    else:
                        required_rate_for_target_date = round(
                            _from_cents(int(round(remaining / months_until_target_date))),
                            2,
                        )

        # Monats-Serie (deterministische Kettenfortschreibung)
        series: List[Dict[str, Any]] = []
        achieved_month: Optional[str] = reference.strftime("%Y-%m") if remaining == 0 else None
        months_left_at_rate: Optional[int] = 0 if remaining == 0 else None
        if rate_cents is not None and rate_cents > 0:
            balance = saved
            for step in range(1, horizon + 1):
                key = self._next_month_key((reference.year, reference.month), step)
                balance += rate_cents
                month_label = f"{key[0]:04d}-{key[1]:02d}"
                if balance >= target and achieved_month is None:
                    achieved_month = month_label
                series.append(
                    {"month": month_label, "balance": round(_from_cents(balance), 2)}
                )
            if remaining > 0:
                months_left_at_rate = int(math.ceil(remaining / rate_cents))

        achieved_now = remaining <= 0
        if goal.target_date and not overdue:
            on_track: Optional[bool] = (
                True
                if achieved_now
                else (achieved_month is not None and achieved_month <= goal.target_date[:7])
            )
        else:
            on_track = None if not achieved_now else True

        return {
            "success": True,
            "goal": self._goal_dict(goal),
            "reference_date": reference.isoformat(),
            "progress": progress,
            "projection": {
                "method": method,
                "rate": round(_from_cents(rate_cents), 2) if rate_cents is not None else None,
                "remaining": round(_from_cents(remaining), 2),
                "target_amount": round(_from_cents(target), 2),
                "currency": goal.currency or "EUR",
                "achieved": achieved_now,
                "achieved_month": achieved_month,
                "months_left_at_rate": months_left_at_rate,
                "target_date": goal.target_date,
                "months_until_target_date": months_until_target_date,
                "required_rate_for_target_date": required_rate_for_target_date,
                "overdue": overdue,
                "on_track": on_track,
                "horizon_months": horizon,
                "series": series,
            },
        }

    def suggest_goal_candidates(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Deterministische Kandidaten-Erkennung für Sparziele (nur lesend).

        Wiederkehrende Auszahlungen mit Periode > 45 Tage (halbjährliche,
        jährliche, Spar-Übergänge), stabile Beträge (Variationskoeffizient
        <= 15 %) und OHNE bestehende Ziel-Zuordnung werden als Kandidaten
        vorgeschlagen. Monatliche Ausgaben sind KEINE Kandidaten (Phase-2-
        Arbeitshypothesen DoD #5). Währungen werden nicht umgerechnet;
        Aggregatsummen nur bei einheitlicher Währung.

        Parameters
        ----------
        iban: optionales Konten-Filter (IBAN)
        reference_date: 'YYYY-MM-DD' (default heute; spätere Buchungen
            fließen nie ein)
        min_occurrences: min. Vorkommen (default 3, min 2)
        """
        iban = _normalize_iban(str(params.get("iban") or "")) or None
        try:
            reference = self._parse_reference_date(params.get("reference_date"))
        except ValueError as exc:
            return {"success": False, "error": str(exc), "error_class": "invalid_reference_date"}
        min_occurrences = self._coerce_int_param(params.get("min_occurrences"), default=3)
        if min_occurrences is None or int(min_occurrences) < 2:
            return {
                "success": False,
                "error": "min_occurrences must be >= 2",
                "error_class": "invalid_param",
            }
        min_occurrences = int(min_occurrences)

        facts, error = self._facts_up_to(iban, reference)
        if error is not None:
            return error
        expenses = [fact for fact in facts if int(fact["amount_cents"]) < 0]
        groups = self._recurring_groups(expenses, min_occurrences=min_occurrences)

        assigned_ids = set(self._db.assigned_transaction_ids())
        candidates: List[Dict[str, Any]] = []
        for group in groups:
            rows = sorted(
                (
                    fact
                    for fact in expenses
                    if fact["counterparty"] == group["counterparty"]
                    and fact["currency"] == group["currency"]
                ),
                key=lambda fact: fact["booking_date"],
            )
            valid_dates: List[date] = []
            for fact in rows:
                try:
                    valid_dates.append(date.fromisoformat(fact["booking_date"]))
                except ValueError:
                    continue
            if len(valid_dates) < 2:
                continue
            gaps = [
                (later - earlier).days
                for earlier, later in zip(valid_dates, valid_dates[1:])
                if (later - earlier).days > 0
            ]
            if not gaps:
                continue
            avg_period_days = sum(gaps) / len(gaps)
            if avg_period_days <= 45:
                continue  # monatlich/wöchentlich sind KEINE Spar-Kandidaten
            amounts = [abs(_from_cents(int(fact["amount_cents"]))) for fact in rows]
            average = mean(amounts)
            if average <= 0:
                continue
            cv = pstdev(amounts) / average
            if cv > 0.15:
                continue  # unstabile Beträge (σ > 15 % von μ)
            if any(int(fact["transaction_id"]) in assigned_ids for fact in rows):
                continue  # bereits einem Ziel zugeordnet
            candidates.append(
                {
                    "counterparty": group["counterparty"],
                    "currency": group["currency"],
                    "occurrences": len(rows),
                    "average_period_days": round(avg_period_days, 1),
                    "average_amount": round(average, 2),
                    "amount_stability_cv": round(cv, 4),
                    "monthly_equivalent": round(average / (avg_period_days / 30.44), 2),
                    "annual_equivalent": round(average * 365.0 / avg_period_days, 2),
                    "first_seen": valid_dates[0].isoformat(),
                    "last_seen": valid_dates[-1].isoformat(),
                    "already_assigned": False,
                    "suggestion": (
                        "Als Sparziel anlegen und diese Buchung je Periode "
                        "dem Ziel zuordnen"
                    ),
                }
            )
        candidates.sort(key=lambda item: (-item["monthly_equivalent"], item["counterparty"]))
        single_currency = len({item["currency"] for item in candidates}) == 1
        return {
            "success": True,
            "method": "recurring_period_gt_45d_stable_unassigned",
            "reference_date": reference.isoformat(),
            "count": len(candidates),
            "candidates": candidates,
            "total_monthly_equivalent": (
                round(sum(item["monthly_equivalent"] for item in candidates), 2)
                if single_currency
                else None
            ),
        }

    def expense_anomaly_detection(self, params: Dict[str, Any]) -> Dict[str, Any]:
        try:
            threshold = float(params.get("z_threshold", 2.0))
        except (TypeError, ValueError):
            return {"success": False, "error": "z_threshold must be numeric", "error_class": "invalid_param"}
        if threshold <= 0:
            return {"success": False, "error": "z_threshold must be > 0", "error_class": "invalid_param"}
        facts, error = self._analysis_facts(params)
        if error is not None:
            return error
        anomalies: List[Dict[str, Any]] = []
        monthly = self._monthly_expenses(facts)
        for currency, values in sorted(monthly.items()):
            amounts = list(values.values())
            if len(amounts) < 3:
                continue
            average = mean(amounts)
            deviation = pstdev(amounts)
            if deviation == 0:
                continue
            for month, amount_cents in sorted(values.items()):
                score = (amount_cents - average) / deviation
                if abs(score) >= threshold:
                    anomalies.append(
                        {
                            "month": month,
                            "currency": currency,
                            "expense": _from_cents(amount_cents),
                            "z_score": round(score, 6),
                            "direction": "high" if score > 0 else "low",
                        }
                    )
        anomalies.sort(key=lambda item: abs(item["z_score"]), reverse=True)
        return {"success": True, "count": len(anomalies), "anomalies": anomalies}

    def budget_vs_actual_analysis(self, params: Dict[str, Any]) -> Dict[str, Any]:
        start_month = str(params.get("start_month") or "").strip()
        end_month = str(params.get("end_month") or "").strip()
        try:
            months = self._month_range(start_month, end_month)
        except ValueError as exc:
            return {"success": False, "error": str(exc), "error_class": "invalid_param"}
        categories: List[Dict[str, Any]] = []
        for month in months:
            for row in self._db.budget_status(month):
                categories.append(
                    {
                        "month": month,
                        "category": row["category"],
                        "kind": row["kind"],
                        "currency": row["currency"],
                        "budget": _from_cents(row["budget_cents"]),
                        "actual": _from_cents(row["actual_cents"]),
                        "remaining": _from_cents(row["remaining_cents"]),
                        "tx_count": row["tx_count"],
                        "within_budget": row["actual_cents"] <= row["budget_cents"],
                    }
                )
        return {
            "success": True,
            "start_month": start_month,
            "end_month": end_month,
            "currency": DEFAULT_CURRENCY,
            "categories": categories,
            "budget": round(sum(item["budget"] for item in categories), 2),
            "actual": round(sum(item["actual"] for item in categories), 2),
            "remaining": round(sum(item["remaining"] for item in categories), 2),
        }

    def savings_potential_analysis(self, params: Dict[str, Any]) -> Dict[str, Any]:
        max_categories = self._coerce_int_param(params.get("max_categories"), default=5)
        if max_categories is None or max_categories < 1:
            return {"success": False, "error": "max_categories must be >= 1", "error_class": "invalid_param"}
        facts, error = self._analysis_facts(params)
        if error is not None:
            return error
        totals: Dict[Tuple[str, str], int] = {}
        for fact in facts:
            if fact["amount_cents"] >= 0:
                continue
            key = (fact["currency"], fact["category"])
            totals[key] = totals.get(key, 0) + abs(int(fact["amount_cents"]))
        opportunities = []
        for (currency, category), expense_cents in sorted(
            totals.items(), key=lambda item: item[1], reverse=True
        )[:max_categories]:
            estimated_cents = int(round(expense_cents * 0.10))
            opportunities.append(
                {
                    "currency": currency,
                    "category": category,
                    "expense": _from_cents(expense_cents),
                    "estimated_savings": _from_cents(estimated_cents),
                    "assumption": "10_percent_reduction_scenario",
                }
            )
        currencies = {item["currency"] for item in opportunities}
        estimated = (
            round(sum(item["estimated_savings"] for item in opportunities), 2)
            if len(currencies) <= 1
            else None
        )
        return {
            "success": True,
            "opportunities": opportunities,
            "estimated_savings": estimated,
            "by_currency": self._sum_field_by_currency(opportunities, "estimated_savings"),
        }

    def expense_trend_break_detection(self, params: Dict[str, Any]) -> Dict[str, Any]:
        min_history = self._coerce_int_param(params.get("min_history_months"), default=6)
        if min_history is None or min_history < 4:
            return {"success": False, "error": "min_history_months must be >= 4", "error_class": "invalid_param"}
        facts, error = self._analysis_facts(params)
        if error is not None:
            return error
        monthly = self._monthly_expenses(facts)
        results = []
        for currency, values in sorted(monthly.items()):
            months = sorted(values)
            if len(months) < min_history:
                results.append({"currency": currency, "status": "insufficient_data", "months": len(months)})
                continue
            midpoint = len(months) // 2
            before_cents = mean(values[month] for month in months[:midpoint])
            after_cents = mean(values[month] for month in months[midpoint:])
            change = ((after_cents - before_cents) / before_cents * 100.0) if before_cents else 0.0
            results.append(
                {
                    "currency": currency,
                    "status": "trend_break_detected" if abs(change) >= 10.0 else "stable",
                    "direction": "increase" if change > 0 else "decrease" if change < 0 else "stable",
                    "change_percent": round(change, 6),
                    "before_average": round(_from_cents(int(round(before_cents))), 2),
                    "after_average": round(_from_cents(int(round(after_cents))), 2),
                    "months": len(months),
                }
            )
        response: Dict[str, Any] = {"success": True, "by_currency": results}
        if len(results) == 1:
            response.update(results[0])
        return response

    def top_counterparty_expenses(self, params: Dict[str, Any]) -> Dict[str, Any]:
        account_id = self._resolve_account_id(params.get("iban"))
        if params.get("iban") and account_id is None:
            return {
                "success": False,
                "error": f"Unknown IBAN: {params.get('iban')!r}",
                "error_class": "unknown_iban",
            }
        limit = self._coerce_int_param(params.get("limit"), default=5)
        if limit is None:
            return {
                "success": False,
                "error": "limit must be an integer",
                "error_class": "invalid_limit",
            }

        rows = self._db.top_counterparty_expenses(
            account_id=account_id,
            start_date=self._normalize_date_param(params.get("start_date")),
            end_date=self._normalize_date_param(params.get("end_date")),
            limit=limit,
            include_transfers=bool(params.get("include_transfers", False)),
        )
        return {
            "success": True,
            "count": len(rows),
            "top_counterparties": [
                {
                    "counterparty": r["counterparty"],
                    "expense": _from_cents(r["expense_abs_cents"]),
                    "currency": r["currency"],
                    "tx_count": r["tx_count"],
                }
                for r in rows
            ],
        }

    # -- balance -----------------------------------------------------

    def balance_at(self, params: Dict[str, Any]) -> Dict[str, Any]:
        iban = params.get("iban")
        as_of_date = params.get("as_of_date")
        if not iban or not as_of_date:
            return {
                "success": False,
                "error": "Both 'iban' and 'as_of_date' are required",
                "error_class": "missing_param",
            }
        account_id = self._resolve_account_id(iban)
        if account_id is None:
            return {
                "success": False,
                "error": f"Unknown IBAN: {iban!r}",
                "error_class": "unknown_iban",
            }
        result = self._db.balance_at(account_id=account_id, as_of_date=as_of_date)
        return {
            "success": True,
            "iban": _normalize_iban(iban),
            "as_of_date": result["as_of_date"],
            "balance": result["balance"],
            "currency": self._currency_for(account_id),
        }

    # -- categories --------------------------------------------------

    def list_categories(self, params: Dict[str, Any]) -> Dict[str, Any]:
        cats = self._db.list_categories()
        return {
            "success": True,
            "count": len(cats),
            "categories": [
                {
                    "category_id": c.id,
                    "name": c.name,
                    "kind": c.kind,
                    "color": c.color,
                    "parent_id": c.parent_id,
                }
                for c in cats
            ],
        }

    def assign_category(self, params: Dict[str, Any]) -> Dict[str, Any]:
        raw_tx_id = params.get("transaction_id")
        try:
            tx_id = int(raw_tx_id)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return {"success": False, "error": "transaction_id required (int)", "error_class": "missing_param"}
        category_name = (params.get("category") or "").strip()
        if not category_name:
            return {"success": False, "error": "category required", "error_class": "missing_param"}
        kind = (params.get("kind") or "expense").strip()
        if kind not in ("expense", "income", "transfer"):
            return {"success": False, "error": "kind must be expense|income|transfer", "error_class": "invalid_kind"}
        create_rule = bool(params.get("create_rule", False))
        tx = self._db.get_transaction(tx_id)
        if tx is None:
            return {"success": False, "error": f"Unknown transaction_id: {tx_id}", "error_class": "unknown_tx"}
        cat_id = self._db.upsert_category(name=category_name, kind=kind, overwrite_kind=False)
        self._db.assign_category(tx_id, cat_id, source="user", confidence=1.0)
        rule_created = False
        applied_extra = 0
        if create_rule:
            tx = self._db.get_transaction(tx_id)
            if tx is None:
                return {"success": False, "error": f"Unknown transaction_id: {tx_id}", "error_class": "unknown_tx"}
            if tx.counterparty_iban:
                self._db.upsert_rule(category_id=cat_id, match_iban=tx.counterparty_iban)
                rule_created = True
            elif tx.counterparty:
                self._db.upsert_rule(category_id=cat_id, match_counterparty=tx.counterparty)
                rule_created = True
            if rule_created:
                applied_extra = self._db.apply_rules(only_uncategorized=True)
        return {
            "success": True,
            "transaction_id": tx_id,
            "category": category_name,
            "category_id": cat_id,
            "rule_created": rule_created,
            "rules_applied_extra": applied_extra,
        }

    # -- LLM-gesttzte Kategorisierungs-Vorschlge ---------------------

    def suggest_categories(self, params: Dict[str, Any]) -> Dict[str, Any]:
        if self._llm_client is None:
            return {
                "success": False,
                "error": "LLM client not available in this context (only in chat orchestrator)",
                "error_class": "no_llm_client",
            }
        from finance.categorizer import FinanceCategorizer

        account_id = self._resolve_account_id(params.get("iban"))
        if params.get("iban") and account_id is None:
            return {"success": False, "error": "Unknown IBAN", "error_class": "unknown_iban"}
        try:
            limit = int(params.get("limit", 25))
        except (TypeError, ValueError):
            limit = 25
        limit = max(1, min(limit, 100))
        apply_now = bool(params.get("apply", False))

        cat = FinanceCategorizer(llm_client=self._llm_client, db=self._db)
        suggestions = cat.suggest(
            account_id=account_id,
            start_date=params.get("start_date"),
            end_date=params.get("end_date"),
            limit=limit,
        )
        out: Dict[str, Any] = {
            "success": True,
            "count": len(suggestions),
            "suggestions": [s.model_dump() for s in suggestions],
        }
        if apply_now and suggestions:
            outcome = cat.apply(suggestions, create_rules=True)
            out["applied"] = {
                "assigned": outcome.assigned,
                "rules_created": outcome.rules_created,
                "rules_applied_extra": outcome.rules_applied_extra,
            }
        return out

    # -- counterparty rules ------------------------------------------

    def list_rules(self, params: Dict[str, Any]) -> Dict[str, Any]:
        rules = self._db.list_rules()
        return {
            "success": True,
            "count": len(rules),
            "rules": [
                {
                    "rule_id": r.id,
                    "match_iban": r.match_iban,
                    "match_counterparty": r.match_counterparty,
                    "category_id": r.category_id,
                    "category": r.category_name,
                }
                for r in rules
            ],
        }

    def apply_rules(self, params: Dict[str, Any]) -> Dict[str, Any]:
        only_uncategorized = bool(params.get("only_uncategorized", True))
        account_id = self._resolve_account_id(params.get("iban"))
        if params.get("iban") and account_id is None:
            return {"success": False, "error": "Unknown IBAN", "error_class": "unknown_iban"}
        applied = self._db.apply_rules(
            only_uncategorized=only_uncategorized,
            account_id=account_id,
        )
        return {"success": True, "applied": applied}

    # -- budgets -----------------------------------------------------

    def set_budget(self, params: Dict[str, Any]) -> Dict[str, Any]:
        category_name = (params.get("category") or "").strip()
        month = (params.get("month") or "").strip()
        amount = params.get("amount")
        if not category_name or not month or amount is None:
            return {
                "success": False,
                "error": "category, month (YYYY-MM) and amount are required",
                "error_class": "missing_param",
            }
        try:
            cents = _to_cents(float(amount))
        except (TypeError, ValueError):
            return {"success": False, "error": "amount must be numeric", "error_class": "invalid_amount"}
        kind = (params.get("kind") or "expense").strip()
        try:
            self._db._validate_iso_date(f"{month}-01", field="month")
            cat_id = self._db.upsert_category(name=category_name, kind=kind, overwrite_kind=False)
            bid = self._db.upsert_budget(category_id=cat_id, month=month, budget_cents=cents)
        except ValueError as exc:
            return {"success": False, "error": str(exc), "error_class": "invalid_budget"}
        return {
            "success": True,
            "budget_id": bid,
            "category": category_name,
            "month": month,
            "amount": _from_cents(abs(cents)),
            "currency": DEFAULT_CURRENCY,
        }

    def budget_status(self, params: Dict[str, Any]) -> Dict[str, Any]:
        month = (params.get("month") or "").strip()
        if not month:
            return {"success": False, "error": "month (YYYY-MM) required", "error_class": "missing_param"}
        try:
            rows = self._db.budget_status(month)
        except ValueError as exc:
            return {"success": False, "error": str(exc), "error_class": "invalid_month"}
        return {
            "success": True,
            "month": month,
            "currency": DEFAULT_CURRENCY,
            "categories": [
                {
                    "category": r["category"],
                    "kind": r["kind"],
                    "budget": _from_cents(r["budget_cents"]),
                    "actual": _from_cents(r["actual_cents"]),
                    "remaining": _from_cents(r["remaining_cents"]),
                    "tx_count": r["tx_count"],
                }
                for r in rows
            ],
        }

    # -- monthly report ----------------------------------------------

    def monthly_report(self, params: Dict[str, Any]) -> Dict[str, Any]:
        month = (params.get("month") or "").strip()
        if not month:
            return {"success": False, "error": "month (YYYY-MM) required", "error_class": "missing_param"}
        account_id = self._resolve_account_id(params.get("iban"))
        if params.get("iban") and account_id is None:
            return {"success": False, "error": "Unknown IBAN", "error_class": "unknown_iban"}
        try:
            report = self._db.monthly_report(month, account_id=account_id, currency=params.get("currency"))
        except ValueError as exc:
            return {"success": False, "error": str(exc), "error_class": "invalid_report_scope"}
        return {
            "success": True,
            "month": report["month"],
            "account_id": report["account_id"],
            "currency": report["currency"],
            "budget_currency": report["budget_currency"],
            "income": _from_cents(report["income_cents"]),
            "expense": _from_cents(report["expense_cents"]),
            "net": _from_cents(report["net_cents"]),
            "tx_count": report["tx_count"],
            "by_category": [
                {
                    "category": r["category"],
                    "kind": r["kind"],
                    "sum": _from_cents(r["sum_cents"]),
                    "count": r["count"],
                }
                for r in report["by_category"]
            ],
            "top_counterparties": [
                {
                    "counterparty": r["counterparty"],
                    "sum": _from_cents(r["sum_cents"]),
                    "count": r["count"],
                }
                for r in report["top_counterparties"]
            ],
            "budget_status": [
                {
                    "category": r["category"],
                    "kind": r["kind"],
                    "budget": _from_cents(r["budget_cents"]),
                    "actual": _from_cents(r["actual_cents"]),
                    "remaining": _from_cents(r["remaining_cents"]),
                    "tx_count": r["tx_count"],
                }
                for r in report["budget_status"]
            ],
        }

    # -- transfer linking --------------------------------------------

    def list_transfer_candidates(self, params: Dict[str, Any]) -> Dict[str, Any]:
        max_days = int(params.get("max_days") or 5)
        cands = self._db.detect_transfer_candidates(max_days=max_days)
        return {
            "success": True,
            "count": len(cands),
            "candidates": [
                {
                    "outgoing_tx_id": c["outgoing_tx_id"],
                    "incoming_tx_id": c["incoming_tx_id"],
                    "amount": _from_cents(c["amount_cents"]),
                    "outgoing_date": c["outgoing_date"],
                    "incoming_date": c["incoming_date"],
                    "outgoing_iban": c["outgoing_iban"],
                    "incoming_iban": c["incoming_iban"],
                    "outgoing_counterparty": c["outgoing_counterparty"],
                    "incoming_counterparty": c["incoming_counterparty"],
                    "day_diff": c["day_diff"],
                }
                for c in cands
            ],
        }

    def link_transfer(self, params: Dict[str, Any]) -> Dict[str, Any]:
        out_id = params.get("outgoing_tx_id")
        in_id = params.get("incoming_tx_id")
        if not isinstance(out_id, int) or not isinstance(in_id, int):
            return {
                "success": False,
                "error": "outgoing_tx_id and incoming_tx_id (int) required",
                "error_class": "missing_param",
            }
        try:
            link_id = self._db.link_transfer(
                outgoing_tx_id=out_id, incoming_tx_id=in_id, source="user"
            )
        except ValueError as exc:
            return {"success": False, "error": str(exc), "error_class": "invalid"}
        except sqlite3.IntegrityError as exc:
            # Safety-Net (Root-Cause-Fix 2026-09-11): UNIQUE-Konflikt auf
            # transfer_links als strukturierter Fehler, kein Crash.
            return {
                "success": False,
                "error": f"transfer link conflict: {exc}",
                "error_class": "conflict",
            }
        return {"success": True, "link_id": link_id}

    def unlink_transfer(self, params: Dict[str, Any]) -> Dict[str, Any]:
        link_id = params.get("link_id")
        if not isinstance(link_id, int):
            return {
                "success": False,
                "error": "link_id (int) required",
                "error_class": "missing_param",
            }
        ok = self._db.unlink_transfer(link_id)
        return {"success": ok, "link_id": link_id}

    def list_transfer_links(self, params: Dict[str, Any]) -> Dict[str, Any]:
        links = self._db.list_transfer_links()
        return {
            "success": True,
            "count": len(links),
            "links": [
                {
                    "link_id": link.id,
                    "outgoing_tx_id": link.outgoing_tx_id,
                    "incoming_tx_id": link.incoming_tx_id,
                    "amount": _from_cents(link.outgoing_amount_cents or 0),
                    "outgoing_date": link.outgoing_booking_date,
                    "incoming_date": link.incoming_booking_date,
                    "outgoing_iban": link.outgoing_account_iban,
                    "incoming_iban": link.incoming_account_iban,
                    "outgoing_counterparty": link.outgoing_counterparty,
                    "incoming_counterparty": link.incoming_counterparty,
                    "confidence": link.confidence,
                    "source": link.source,
                }
                for link in links
            ],
        }

    def detect_statement_settlement_gaps(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Diagnostiziert offene Kreditkarten-Statement-Ausgleichsfaelle.

        Read-only Analyse zur Ursachenklaerung: zeigt, ob fuer offene
        Statements keine Kandidaten, nur Kandidaten ausserhalb des Fensters,
        mehrdeutige oder eindeutige Kandidaten existieren.
        """
        try:
            max_days_after_statement = int(params.get("max_days_after_statement") or 45)
        except (TypeError, ValueError):
            max_days_after_statement = 45
        try:
            extended_search_days = int(params.get("extended_search_days") or 180)
        except (TypeError, ValueError):
            extended_search_days = 180

        try:
            gaps = self._db.detect_statement_settlement_gaps(
                max_days_after_statement=max_days_after_statement,
                extended_search_days=extended_search_days,
            )
        except ValueError as exc:
            return {"success": False, "error": str(exc), "error_class": "invalid_param"}

        counts = {
            "no_candidate": 0,
            "candidate_out_of_window": 0,
            "ambiguous_in_window": 0,
            "single_candidate_in_window": 0,
        }
        for row in gaps:
            status = str(row.get("status") or "")
            if status in counts:
                counts[status] += 1

        return {
            "success": True,
            "count": len(gaps),
            "status_counts": counts,
            "gaps": gaps,
        }

    # -- statement repair --------------------------------------------

    def list_statements_with_incomplete_balances(
        self, params: Dict[str, Any]
    ) -> Dict[str, Any]:
        stmts = self._db.find_statements_with_incomplete_balances()
        return {
            "success": True,
            "count": len(stmts),
            "statements": [
                {
                    "statement_id": s.id,
                    "account_id": s.account_id,
                    "source_filename": s.source_filename,
                    "period_start": s.period_start,
                    "period_end": s.period_end,
                    "opening_balance": (
                        s.opening_balance_cents / 100.0
                        if s.opening_balance_cents is not None
                        else None
                    ),
                    "closing_balance": (
                        s.closing_balance_cents / 100.0
                        if s.closing_balance_cents is not None
                        else None
                    ),
                    "imported_at": s.imported_at,
                }
                for s in stmts
            ],
        }

    def check_statement_import_completeness(self, params: Dict[str, Any]) -> Dict[str, Any]:
        raw_statement_id = params.get("statement_id")
        try:
            statement_id = int(raw_statement_id)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return {
                "success": False,
                "error": "statement_id (int) required",
                "error_class": "missing_param",
            }
        try:
            settlement_window_days = int(params.get("settlement_window_days") or 45)
        except (TypeError, ValueError):
            settlement_window_days = 45
        try:
            statement_lookback_days = int(params.get("statement_lookback_days") or 15)
        except (TypeError, ValueError):
            statement_lookback_days = 15

        try:
            result = self._db.evaluate_statement_import_completeness(
                statement_id=statement_id,
                settlement_window_days=settlement_window_days,
                statement_lookback_days=statement_lookback_days,
            )
        except ValueError as exc:
            return {"success": False, "error": str(exc), "error_class": "invalid_param"}

        return {"success": True, "completeness": result}

    def repair_statement_header(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Re-extrahiert Kopfdaten (Salden, Periode) fuer ein Statement aus
        seiner Original-PDF und aktualisiert die DB-Zeile.
        """
        try:
            statement_id = int(params["statement_id"])
        except (KeyError, TypeError, ValueError):
            return {
                "success": False,
                "error": "statement_id (int) required",
                "error_class": "missing_param",
            }
        pdf_path = (params.get("pdf_path") or "").strip()
        if not pdf_path:
            return {
                "success": False,
                "error": "pdf_path required",
                "error_class": "missing_param",
            }
        if not self._llm_client:
            return {
                "success": False,
                "error": "LLM client not available for header re-extraction",
                "error_class": "no_llm",
            }
        from finance.extractor import FinanceExtractor

        extractor = FinanceExtractor(self._llm_client, db=self._db)
        try:
            updated = extractor.repair_statement_header(statement_id, pdf_path)
        except (FileNotFoundError, RuntimeError) as exc:
            return {
                "success": False,
                "error": str(exc),
                "error_class": type(exc).__name__,
            }
        return {"success": True, "statement_id": statement_id, "updated": updated}

    # -- transfer relink ---------------------------------------------

    def relink_transfers(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Fuehrt Transfer-Auto-Erkennung fuer alle noch unverlinkten
        Buchungen durch (z.B. nach nachtraeglichem Kreditkarten-Import).
        """
        try:
            max_days = int(params.get("max_days") or 5)
        except (TypeError, ValueError):
            max_days = 5
        linked = self._db.relink_all_transfers(max_days=max_days)
        return {"success": True, "newly_linked": linked}

    # -- helpers -----------------------------------------------------

    def _resolve_account_id(self, iban: Optional[str]) -> Optional[int]:
        if not iban:
            return None
        target = _normalize_iban(iban)
        for a in self._db.list_accounts():
            if a.iban == target:
                return a.id
        return None

    def _currency_for(self, account_id: int) -> str:
        for a in self._db.list_accounts():
            if a.id == account_id:
                return a.currency
        return DEFAULT_CURRENCY

    def _analysis_facts(
        self, params: Dict[str, Any]
    ) -> Tuple[List[Dict[str, Any]], Optional[Dict[str, Any]]]:
        account_id = self._resolve_account_id(params.get("iban"))
        if params.get("iban") and account_id is None:
            return [], {
                "success": False,
                "error": f"Unknown IBAN: {params.get('iban')!r}",
                "error_class": "unknown_iban",
            }
        facts = self._db.list_analysis_facts(
            account_id=account_id,
            start_date=self._normalize_date_param(params.get("start_date")),
            end_date=self._normalize_date_param(params.get("end_date")),
            include_transfers=bool(params.get("include_transfers", False)),
        )
        return facts, None

    @staticmethod
    def _category_variants(value: str) -> set[str]:
        key = re.sub(r"[^a-z0-9]", "", value.casefold())
        variants = {key}
        for suffix in ("en", "es", "er", "e", "s"):
            if key.endswith(suffix) and len(key) > len(suffix) + 2:
                variants.add(key[: -len(suffix)])
        return variants

    @classmethod
    def _resolve_category_name(cls, requested: str, available: Sequence[str]) -> Optional[str]:
        requested_variants = cls._category_variants(requested)
        matches = [
            name
            for name in available
            if requested_variants.intersection(cls._category_variants(name))
        ]
        return matches[0] if len(matches) == 1 else None

    @staticmethod
    def _cost_summaries_by_currency(facts: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
        totals: Dict[str, Dict[str, int]] = {}
        for fact in facts:
            currency = fact["currency"]
            bucket = totals.setdefault(currency, {"expense": 0, "refunds": 0, "count": 0})
            amount = int(fact["amount_cents"])
            if amount < 0:
                bucket["expense"] += abs(amount)
            elif amount > 0:
                bucket["refunds"] += amount
            bucket["count"] += 1
        return [
            {
                "currency": currency,
                "expense": _from_cents(values["expense"]),
                "refunds": _from_cents(values["refunds"]),
                "net_cost": _from_cents(values["expense"] - values["refunds"]),
                "tx_count": values["count"],
            }
            for currency, values in sorted(totals.items())
        ]

    @staticmethod
    def _promote_single_currency_costs(
        result: Dict[str, Any], summaries: Sequence[Dict[str, Any]]
    ) -> None:
        if len(summaries) == 1:
            summary = summaries[0]
            result.update(
                {
                    "currency": summary["currency"],
                    "expense": summary["expense"],
                    "refunds": summary["refunds"],
                    "net_cost": summary["net_cost"],
                }
            )
        else:
            result.update({"currency": None, "expense": None, "refunds": None, "net_cost": None})

    @staticmethod
    def _recurring_groups(
        facts: Sequence[Dict[str, Any]], *, min_occurrences: int
    ) -> List[Dict[str, Any]]:
        months_total = len({fact["month"] for fact in facts})
        grouped: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
        for fact in facts:
            grouped.setdefault((fact["currency"], fact["counterparty"]), []).append(fact)
        results = []
        for (currency, counterparty), rows in grouped.items():
            if len(rows) < min_occurrences:
                continue
            amounts = [abs(int(row["amount_cents"])) for row in rows]
            months_covered = len({row["month"] for row in rows})
            average = mean(amounts)
            stability = 1.0 if average == 0 else max(0.0, 1.0 - min(1.0, pstdev(amounts) / average))
            coverage = months_covered / months_total if months_total else 0.0
            results.append(
                {
                    "currency": currency,
                    "counterparty": counterparty,
                    "occurrences": len(rows),
                    "months_covered": months_covered,
                    "month_coverage": round(coverage, 6),
                    "average_expense": round(_from_cents(int(round(average))), 2),
                    "total_expense": _from_cents(sum(amounts)),
                    "amount_stability": round(stability, 6),
                    "is_fixed": coverage >= 0.6 and stability >= 0.8,
                }
            )
        results.sort(key=lambda item: item["total_expense"], reverse=True)
        return results

    @staticmethod
    def _monthly_expenses(facts: Sequence[Dict[str, Any]]) -> Dict[str, Dict[str, int]]:
        monthly: Dict[str, Dict[str, int]] = {}
        for fact in facts:
            amount = int(fact["amount_cents"])
            if amount >= 0:
                continue
            currency = fact["currency"]
            month = fact["month"]
            currency_months = monthly.setdefault(currency, {})
            currency_months[month] = currency_months.get(month, 0) + abs(amount)
        return monthly

    @staticmethod
    def _shift_month(month: str, offset: int) -> str:
        year, month_number = (int(part) for part in month.split("-"))
        absolute = year * 12 + month_number - 1 + offset
        return f"{absolute // 12:04d}-{absolute % 12 + 1:02d}"

    @classmethod
    def _month_range(cls, start_month: str, end_month: str) -> List[str]:
        if not re.fullmatch(r"\d{4}-\d{2}", start_month) or not re.fullmatch(r"\d{4}-\d{2}", end_month):
            raise ValueError("start_month and end_month must use YYYY-MM")
        if not 1 <= int(start_month[5:]) <= 12 or not 1 <= int(end_month[5:]) <= 12:
            raise ValueError("start_month and end_month must contain valid months")
        months = []
        current = start_month
        while current <= end_month:
            months.append(current)
            if len(months) > 120:
                raise ValueError("Budget analysis range is limited to 120 months")
            current = cls._shift_month(current, 1)
        if not months:
            raise ValueError("start_month must not be after end_month")
        return months

    @staticmethod
    def _sum_field_by_currency(
        rows: Sequence[Dict[str, Any]], field: str
    ) -> List[Dict[str, Any]]:
        totals: Dict[str, float] = {}
        for row in rows:
            currency = str(row["currency"])
            totals[currency] = totals.get(currency, 0.0) + float(row[field])
        return [
            {"currency": currency, field: round(value, 2)}
            for currency, value in sorted(totals.items())
        ]

    @staticmethod
    def _normalize_date_param(value: Any) -> Optional[str]:
        if value is None:
            return None
        text = str(value).strip().replace('<|"|>', '')
        text = text.strip('"').strip("'").strip()
        match = re.search(r"\d{4}-\d{2}-\d{2}", text)
        return match.group(0) if match else (text or None)

    @staticmethod
    def _coerce_int_param(value: Any, *, default: Optional[int] = None) -> Optional[int]:
        if value is None:
            return default
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, int):
            return value
        text = str(value).strip().replace('<|"|>', '')
        text = text.strip('"').strip("'").strip()
        try:
            return int(text)
        except ValueError:
            match = re.search(r"-?\d+", text)
            if match:
                try:
                    return int(match.group(0))
                except ValueError:
                    return None
            return None


__all__ = ["FinanceTools"]
