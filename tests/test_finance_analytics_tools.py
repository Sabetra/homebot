from __future__ import annotations

from pathlib import Path

import pytest

from finance.db_schema import FinanceDB
from finance.tools import FinanceTools


# Deterministic stub: these tests assert SQL sums, rounding and
# deduplication - never the content of embeddings. The stub keeps the
# module runnable without a locally cached sentence-transformer model
# (fresh checkout / offline); production code stays untouched.
_EMBED_STUB_DIM = 4


def _stub_embed_search_texts(texts: list[str]) -> tuple[str, int, list[bytes]]:
    """Stub for ``FinanceDB._embed_search_texts`` (see note above)."""
    blob = b"\x00" * (_EMBED_STUB_DIM * 4)  # float32, length consistent with dim
    return ("test-stub-embeddings", _EMBED_STUB_DIM, [blob] * len(texts))


@pytest.fixture(autouse=True)
def _stub_finance_embeddings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(FinanceDB, "_embed_search_texts", _stub_embed_search_texts)


@pytest.fixture()
def analytics_tools(tmp_path: Path) -> FinanceTools:
    db = FinanceDB(str(tmp_path / "finance_analytics.db"))
    transactions = []
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
        source_pdf_hash="analytics-fixture",
        source_filename="analytics-fixture.pdf",
        period_start="2026-01-01",
        period_end="2026-08-31",
        opening_balance=0.0,
        closing_balance=0.0,
        transactions=transactions,
    )
    assert inserted == len(transactions)
    assert duplicates == 0

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
    return FinanceTools(db=db)


def test_sum_category_costs_separates_expenses_and_refunds(
    analytics_tools: FinanceTools,
) -> None:
    result = analytics_tools.sum_category_costs({"categories": "Groceries"})

    assert result["success"]
    assert result["matched_categories"] == ["Groceries"]
    assert result["expense"] == pytest.approx(2305.0)
    assert result["refunds"] == pytest.approx(20.0)
    assert result["net_cost"] == pytest.approx(2285.0)
    assert result["currency"] == "CHF"


def test_cost_structure_and_recurring_expenses_are_deterministic(
    analytics_tools: FinanceTools,
) -> None:
    structure = analytics_tools.cost_structure_analysis({})
    recurring = analytics_tools.recurring_expense_analysis({"min_occurrences": 4})

    assert structure["success"]
    assert structure["fixed_expense"] == pytest.approx(8000.0)
    assert structure["variable_expense"] == pytest.approx(2305.0)
    landlord = next(item for item in recurring["recurring_expenses"] if item["counterparty"] == "Example Landlord")
    assert landlord["occurrences"] == 8
    assert landlord["months_covered"] == 8
    assert landlord["average_expense"] == pytest.approx(1000.0)
    assert landlord["amount_stability"] == pytest.approx(1.0)


def test_forecast_and_anomaly_detection_use_monthly_expenses(
    analytics_tools: FinanceTools,
) -> None:
    forecast = analytics_tools.expense_forecast(
        {"lookback_months": 3, "forecast_months": 2}
    )
    anomalies = analytics_tools.expense_anomaly_detection({"z_threshold": 2.0})

    assert forecast["success"]
    assert [item["month"] for item in forecast["forecast"]] == ["2026-09", "2026-10"]
    assert forecast["forecast"][0]["expense"] == pytest.approx(
        (1800 + 1225 + 1230) / 3,
        abs=0.01,
    )
    assert anomalies["success"]
    assert any(item["month"] == "2026-06" for item in anomalies["anomalies"])


def test_budget_savings_and_trend_tools_return_actionable_results(
    analytics_tools: FinanceTools,
) -> None:
    budget = analytics_tools.budget_vs_actual_analysis(
        {"start_month": "2026-01", "end_month": "2026-01"}
    )
    savings = analytics_tools.savings_potential_analysis({"max_categories": 2})
    trend = analytics_tools.expense_trend_break_detection(
        {"min_history_months": 6}
    )

    grocery_budget = next(item for item in budget["categories"] if item["category"] == "Groceries")
    assert budget["currency"] == grocery_budget["currency"] == "CHF"
    assert grocery_budget["budget"] == pytest.approx(250.0)
    assert grocery_budget["actual"] == pytest.approx(200.0)
    assert grocery_budget["remaining"] == pytest.approx(50.0)
    assert savings["success"]
    assert savings["estimated_savings"] > 0
    assert savings["opportunities"]
    assert trend["success"]
    assert trend["status"] == "trend_break_detected"
    assert trend["direction"] == "increase"
    assert trend["change_percent"] > 10.0


@pytest.mark.parametrize("budget_cents", [25_000, -25_000])
def test_budget_limit_normalizes_legacy_signs(analytics_tools, budget_cents):
    db = analytics_tools._db
    category = next(category for category in db.list_categories() if category.name == "Groceries")
    db.upsert_budget(category_id=category.id, month="2026-01", budget_cents=budget_cents)
    status = next(row for row in db.budget_status("2026-01") if row["category"] == "Groceries")
    assert status["budget_cents"] == 25_000
    assert status["actual_cents"] == 20_000
    assert status["remaining_cents"] == 5_000


def test_existing_category_kind_can_be_preserved(analytics_tools):
    db = analytics_tools._db
    db.upsert_category("Groceries", kind="income", overwrite_kind=False)
    assert next(category for category in db.list_categories() if category.name == "Groceries").kind == "expense"
    db.upsert_category("Groceries", kind="income")
    assert next(category for category in db.list_categories() if category.name == "Groceries").kind == "income"


@pytest.fixture()
def mixed_currency_db(analytics_tools):
    db = analytics_tools._db
    category = next(category for category in db.list_categories() if category.name == "Groceries")
    for currency, amount in [("CHF", -30.0), ("EUR", -40.0)]:
        _, account_id, _, _, _ = db.persist_statement_import(
            bank_name="Synthetic Additional Bank", bank_bic=None, bank_country_code="CH",
            iban=f"SYNTHETIC_{currency}", account_holder="Synthetic", currency=currency,
            account_type="checking", source_pdf_hash=f"mixed-{currency}",
            source_filename="synthetic.pdf", period_start="2026-01-01",
            period_end="2026-01-31", opening_balance=1000, closing_balance=1000 + amount,
            transactions=[{"booking_date": "2026-01-05", "amount": amount,
                           "currency": currency, "counterparty": "Additional Grocer"}],
        )
        for transaction in db.query_transactions(account_id=account_id):
            db.assign_category(transaction.id, category.id)
    return db


def test_aggregates_separate_currencies(mixed_currency_db):
    rows = mixed_currency_db.aggregate(group_by="month", end_date="2026-01-31")
    assert {row["currency"]: row["expense_cents"] for row in rows} == {"CHF": -123_000, "EUR": -4_000}
    assert mixed_currency_db.list_transaction_currencies() == ["CHF", "EUR"]


def test_monthly_report_and_budget_respect_account_scope(mixed_currency_db):
    account = next(account for account in mixed_currency_db.list_accounts() if account.bank_name == "Synthetic Bank")
    report = mixed_currency_db.monthly_report("2026-01", account_id=account.id)
    grocery = next(row for row in report["budget_status"] if row["category"] == "Groceries")
    assert report["currency"] == "CHF"
    assert report["expense_cents"] == -120_000
    assert grocery["actual_cents"] == 20_000
    global_grocery = next(row for row in mixed_currency_db.budget_status("2026-01") if row["category"] == "Groceries")
    assert global_grocery["actual_cents"] == 23_000


def test_monthly_report_requires_currency_for_mixed_totals(mixed_currency_db):
    with pytest.raises(ValueError, match="Multiple currencies"):
        mixed_currency_db.monthly_report("2026-01")
    report = mixed_currency_db.monthly_report("2026-01", currency="EUR")
    assert report["expense_cents"] == -4_000
    assert report["budget_status"] == []


def test_legacy_negative_budget_is_normalized_on_read(analytics_tools):
    db = analytics_tools._db
    with db._lock, db._connect() as connection:
        connection.execute("UPDATE budgets SET budget_cents = -25000")
    assert db.list_budgets()[0].budget_cents == 25_000
    grocery = next(row for row in db.budget_status("2026-01") if row["category"] == "Groceries")
    assert grocery["remaining_cents"] == 5_000


def test_category_assignment_tool_preserves_expense_category_for_refund(analytics_tools):
    db = analytics_tools._db
    refund = next(transaction for transaction in db.query_transactions() if transaction.amount_cents == 2000)
    result = analytics_tools.assign_category({"transaction_id": refund.id, "category": "Groceries", "kind": "income"})
    assert result["success"]
    assert next(category for category in db.list_categories() if category.name == "Groceries").kind == "expense"


def test_tools_expose_currency_and_reject_ambiguous_report(mixed_currency_db):
    tools = FinanceTools(mixed_currency_db)
    aggregate = tools.aggregate({"group_by": "month", "end_date": "2026-01-31"})
    assert {group["currency"] for group in aggregate["groups"]} == {"CHF", "EUR"}
    assert tools.monthly_report({"month": "2026-01"})["success"] is False
    report = tools.monthly_report({"month": "2026-01", "currency": "EUR"})
    assert report["success"] and report["currency"] == "EUR"
    assert report["expense"] == -40


def test_budget_tool_returns_canonical_amount_and_validation_error(analytics_tools):
    result = analytics_tools.set_budget({"category": "Groceries", "month": "2026-01", "amount": -250})
    assert result["success"] and result["amount"] == 250 and result["currency"] == "CHF"
    result = analytics_tools.set_budget({"category": "Groceries", "month": "2026-13", "amount": 250})
    assert result["success"] is False