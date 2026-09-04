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

import json
import logging
import re
from statistics import mean, pstdev
from typing import Any, Dict, List, Optional, Sequence, Tuple

from finance.db_schema import (
    FinanceDB,
    _from_cents,
    _normalize_iban,
    _normalize_like_needle,
    _to_cents,
)
from finance.models import DEFAULT_CURRENCY

logger = logging.getLogger(__name__)


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
        )
        return {
            "success": True,
            "group_by": group_by,
            "groups": [
                {
                    "key": r["key"],
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
        cat_id = self._db.upsert_category(name=category_name, kind=kind)
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
        cat_id = self._db.upsert_category(name=category_name, kind=kind)
        bid = self._db.upsert_budget(category_id=cat_id, month=month, budget_cents=cents)
        return {
            "success": True,
            "budget_id": bid,
            "category": category_name,
            "month": month,
            "amount": _from_cents(cents),
        }

    def budget_status(self, params: Dict[str, Any]) -> Dict[str, Any]:
        month = (params.get("month") or "").strip()
        if not month:
            return {"success": False, "error": "month (YYYY-MM) required", "error_class": "missing_param"}
        rows = self._db.budget_status(month)
        return {
            "success": True,
            "month": month,
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
        report = self._db.monthly_report(month, account_id=account_id)
        return {
            "success": True,
            "month": report["month"],
            "account_id": report["account_id"],
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
