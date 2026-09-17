"""Phase-1 "Monarch-Core" Finance-Tests (SOTA-Haushaltsprognosen).

Validiert die drei neuen Finance-Tools (siehe docs/03_FINANCE_MODULE.md):

* ``upcoming_bills``     - deterministischer Faelligkeits-Kalender
* ``cash_flow_forecast`` - Schedule-first-Hybrid mit Bootstrap-KI
* ``subscription_audit`` - Repeating-Audit inkl. Preisveraenderung

Gepruefte Invarianten:
* Determinismus (gleiche Eingabe -> gleiches Ergebnis, fester Bootstrap-Seed)
* Kein Future-Leak (Buchungen nach dem Referenzdatum werden nie gelesen)
* Guthaben-Kettenrichtigkeit (balance[m] = balance[m-1] + net[m])
* Bands enthalten den Punktwaert (low <= value <= high)
* Multi-Currency: Summen bleiben None, Guthaben wird pro Waehrung getrennt
* Transfer-Ausschluss (verlinkte Transfers tauchen nirgends auf)
* Saubere Fehlerbehandlung (unbekanntes IBAN, ungültiges Datum, keine Daten)
"""
from __future__ import annotations

import datetime
import sys
import types
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from finance.db_schema import FinanceDB  # noqa: E402
from finance.tools import FinanceTools  # noqa: E402

CH_IBAN = "CH9300762011623852957"
EUR_IBAN = "DE89370400440532013000"
REF = "2026-08-28"  # Freitag, Referenzdatum aller Szenarien


@pytest.fixture(autouse=True)
def _stub_finance_embeddings(monkeypatch):
    """Import-Kette abfangen (embedding-Stack braucht kein echter Modell-Laden)."""
    stub = types.ModuleType("finance.embeddings")

    class _StubEmbeddingClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

    class _StubFinanceEmbeddingClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

    class _StubFinanceEmbeddingService:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

    class _StubFinanceEmbeddingIndex:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

    stub.FinanceEmbeddingClient = _StubEmbeddingClient
    stub.FinanceEmbeddingIndex = _StubFinanceEmbeddingIndex
    stub.FinanceEmbeddingService = _StubFinanceEmbeddingService
    stub.get_finance_embedding_client = lambda *args, **kwargs: _StubEmbeddingClient()
    stub.get_finance_embedding_index = lambda *args, **kwargs: _StubFinanceEmbeddingIndex()
    stub.get_finance_embedding_service = lambda *args, **kwargs: _StubFinanceEmbeddingService()
    monkeypatch.setitem(sys.modules, "finance.embeddings", stub)
    yield


def _tx(booking_date: str, amount: float, counterparty: str, currency: str = "CHF") -> Dict[str, Any]:
    return {
        "booking_date": booking_date,
        "amount": amount,
        "currency": currency,
        "counterparty": counterparty,
    }


def _import(
    db: FinanceDB,
    *,
    iban: str,
    currency: str,
    transactions: List[Dict[str, Any]],
    tag: str,
    period_start: str,
    opening_balance: Optional[float] = 0.0,
) -> int:
    """Synthetischen Kontoauszug importieren und auf sauberen Import pruefen."""
    _, account_id, _, inserted, duplicates = db.persist_statement_import(
        bank_name="Synthetic Bank",
        bank_bic=None,
        bank_country_code=None,
        iban=iban,
        account_holder="Test Account",
        currency=currency,
        account_type=None,
        source_pdf_hash=tag,
        source_filename=None,
        period_start=period_start,
        period_end=None,
        opening_balance=opening_balance,
        closing_balance=0.0,
        transactions=transactions,
    )
    assert duplicates == 0, f"{tag}: unerwartete Duplikate"
    assert inserted == len(transactions), f"{tag}: nicht alle Buchungen eingefuegt"
    return account_id


def _months_back(end_year: int, end_month: int, count: int) -> List[tuple]:
    months = []
    year, month = end_year, end_month
    for _ in range(count):
        months.append((year, month))
        if month == 1:
            year, month = year - 1, 12
        else:
            month -= 1
    return list(reversed(months))


def _seed_monarch_history(db: FinanceDB) -> int:
    """12 Monate (2025-09..2026-08): Gehalt, Miete, Netflix, saisonale Lebensmittel."""
    transactions: List[Dict[str, Any]] = []
    for year, month in _months_back(2026, 8, 12):
        ym = f"{year:04d}-{month:02d}"
        transactions.append(_tx(f"{ym}-01", 5000.0, "Example Employer"))
        transactions.append(_tx(f"{ym}-03", -1200.0, "Example Landlord"))
        transactions.append(_tx(f"{ym}-10", -(300.0 + 50.0 * month), "Example Grocer"))
        transactions.append(_tx(f"{ym}-12", -13.99, "Netflix"))
    return _import(
        db,
        iban=CH_IBAN,
        currency="CHF",
        transactions=transactions,
        tag="monarch-history",
        period_start="2025-09-01",
        opening_balance=10000.0,
    )


@pytest.fixture
def monarch_db(tmp_path) -> FinanceDB:
    db = FinanceDB(str(tmp_path / "monarch.db"))
    _seed_monarch_history(db)
    return db


@pytest.fixture
def monarch_tools(monarch_db: FinanceDB) -> FinanceTools:
    return FinanceTools(db=monarch_db)


@pytest.fixture
def empty_tools(tmp_path) -> FinanceTools:
    return FinanceTools(db=FinanceDB(str(tmp_path / "empty.db")))


@pytest.fixture
def multi_db(tmp_path) -> FinanceDB:
    """Zwei Konten in derselben DB: CHF (12 Monate) + EUR (4 Monate)."""
    db = FinanceDB(str(tmp_path / "multi.db"))
    chf: List[Dict[str, Any]] = []
    for year, month in _months_back(2026, 8, 12):
        ym = f"{year:04d}-{month:02d}"
        chf.append(_tx(f"{ym}-01", 5000.0, "Example Employer"))
        chf.append(_tx(f"{ym}-03", -1200.0, "Example Landlord"))
        chf.append(_tx(f"{ym}-10", -400.0, "Example Grocer"))
    eur: List[Dict[str, Any]] = []
    for month in (5, 6, 7, 8):
        ym = f"2026-{month:02d}"
        eur.append(_tx(f"{ym}-01", 1500.0, "Example Employer EU", "EUR"))
        eur.append(_tx(f"{ym}-03", -600.0, "Example Landlord EU", "EUR"))
    eur.append(_tx("2026-07-20", -50.0, "Example Market", "EUR"))
    _import(db, iban=CH_IBAN, currency="CHF", transactions=chf, tag="multi-chf", period_start="2025-09-01")
    _import(db, iban=EUR_IBAN, currency="EUR", transactions=eur, tag="multi-eur", period_start="2026-05-01")
    return db


@pytest.fixture
def price_db(tmp_path) -> FinanceDB:
    """Abo mit Preisveraenderung (3x 30.00, dann 45.00)."""
    db = FinanceDB(str(tmp_path / "price.db"))
    transactions = [
        _tx("2026-05-15", -30.0, "Example Telecom"),
        _tx("2026-06-15", -30.0, "Example Telecom"),
        _tx("2026-07-15", -30.0, "Example Telecom"),
        _tx("2026-08-15", -45.0, "Example Telecom"),
    ]
    _import(db, iban=CH_IBAN, currency="CHF", transactions=transactions, tag="price-history", period_start="2026-05-01")
    return db


class TestPureHelpers:
    """Reine Helper: deterministisch, ohne DB."""

    def test_next_due_on_or_after_same_and_next_month(self):
        assert FinanceTools._next_due_on_or_after(datetime.date(2026, 8, 28), 3) == datetime.date(2026, 9, 3)
        assert FinanceTools._next_due_on_or_after(datetime.date(2026, 3, 5), 5) == datetime.date(2026, 3, 5)
        assert FinanceTools._next_due_on_or_after(datetime.date(2026, 3, 6), 5) == datetime.date(2026, 4, 5)

    def test_next_due_clamps_short_months(self):
        # Anker-Tag 31 clamped auf Monatsletzten
        assert FinanceTools._next_due_on_or_after(datetime.date(2026, 1, 31), 31) == datetime.date(2026, 2, 28)
        assert FinanceTools._next_due_on_or_after(datetime.date(2024, 1, 31), 31) == datetime.date(2024, 2, 29)

    def test_next_month_key_year_rollover(self):
        assert FinanceTools._next_month_key((2026, 12), 1) == (2027, 1)
        assert FinanceTools._next_month_key((2026, 1), -1) == (2025, 12)
        assert FinanceTools._month_key("2026-08") == (2026, 8)

    def test_fit_trend_flat_slope_and_degenerate(self):
        assert FinanceTools._fit_trend([1.0, 1.0, 1.0]) == (1.0, 0.0)
        assert FinanceTools._fit_trend([1.0, 2.0, 3.0]) == (1.0, 1.0)
        assert FinanceTools._fit_trend([7.0]) == (7.0, 0.0)
        assert FinanceTools._fit_trend([]) == (0.0, 0.0)

    def test_seasonal_index_degrades_to_one(self):
        index = FinanceTools._seasonal_index([(1, 100.0), (2, 200.0)])
        assert set(index.values()) == {1.0}

    def test_bootstrap_interval_deterministic_and_containing(self):
        first = FinanceTools._bootstrap_interval(100.0, [1.0, -1.0, 2.0], 0.9)
        second = FinanceTools._bootstrap_interval(100.0, [1.0, -1.0, 2.0], 0.9)
        assert first == second
        assert first[0] <= 100.0 <= first[1]

    def test_bootstrap_interval_empty_residuals(self):
        assert FinanceTools._bootstrap_interval(100.0, [], 0.9) == (100.0, 100.0)


class TestUpcomingBills:
    def test_projects_anchor_days_within_window(self, monarch_tools):
        result = monarch_tools.upcoming_bills(
            {"iban": CH_IBAN, "reference_date": REF, "days_ahead": 30}
        )
        assert result["success"] is True
        assert result["method"] == "recurring_projection"
        assert result["reference_date"] == REF
        assert result["window_end"] == "2026-09-27"
        assert result["currency"] == "CHF"
        assert result["count"] == 3

        by_cpty = {bill["counterparty"]: bill for bill in result["bills"]}
        assert set(by_cpty) == {"Example Landlord", "Example Grocer", "Netflix"}

        landlord = by_cpty["Example Landlord"]
        assert landlord["next_due"] == "2026-09-03"
        assert landlord["days_until"] == 6
        assert landlord["amount"] == 1200.0
        assert landlord["is_fixed"] is True
        assert landlord["subscription_like"] is False

        grocer = by_cpty["Example Grocer"]
        assert grocer["next_due"] == "2026-09-10"
        assert grocer["days_until"] == 13
        assert grocer["amount"] == pytest.approx(625.0, abs=0.01)  # 12-Monats-Mittel

        netflix = by_cpty["Netflix"]
        assert netflix["next_due"] == "2026-09-12"
        assert netflix["days_until"] == 15
        assert netflix["amount"] == 13.99
        assert netflix["subscription_like"] is True

        assert result["total_in_window"] == pytest.approx(1200.0 + 625.0 + 13.99, abs=0.01)

    def test_window_filters_far_bills(self, monarch_tools):
        result = monarch_tools.upcoming_bills(
            {"iban": CH_IBAN, "reference_date": REF, "days_ahead": 7}
        )
        assert result["success"] is True
        assert result["count"] == 1
        assert result["bills"][0]["counterparty"] == "Example Landlord"
        assert result["total_in_window"] == 1200.0

    def test_days_ahead_clamped_to_max_window(self, monarch_tools):
        result = monarch_tools.upcoming_bills(
            {"iban": CH_IBAN, "reference_date": REF, "days_ahead": 999}
        )
        assert result["success"] is True
        assert result["window_end"] == "2027-02-24"  # REF + 180 Tage

    def test_invalid_reference_date(self, monarch_tools):
        result = monarch_tools.upcoming_bills(
            {"iban": CH_IBAN, "reference_date": "2026-13-40"}
        )
        assert result["success"] is False
        assert result["error_class"] == "invalid_reference_date"

    def test_unknown_iban(self, monarch_tools):
        result = monarch_tools.upcoming_bills(
            {"iban": "XX0000000000000000000000", "reference_date": REF}
        )
        assert result["success"] is False
        assert result["error_class"] == "unknown_iban"

    def test_empty_db_returns_no_bills(self, empty_tools):
        result = empty_tools.upcoming_bills({"reference_date": REF, "days_ahead": 30})
        assert result["success"] is True
        assert result["count"] == 0
        assert result["bills"] == []
        assert result["total_in_window"] is None

    def test_no_future_leakage(self, monarch_db, monarch_tools):
        """Buchungen nach dem Referenzdatum duerfen keine neue 'Recurring'-Gruppe erfinden.

        'Example Gym' hat genau 2 Vorkommen (2026-07-05 + 2026-09-05); die
        September-Buchung liegt NACH dem Referenzdatum. Ohne Leak waere Gym
        eine valide Gruppe (Anker-Tag 5 -> Faelligkeit 2026-09-05 im Fenster).
        """
        before = monarch_tools.upcoming_bills(
            {"iban": CH_IBAN, "reference_date": REF, "days_ahead": 30}
        )
        _import(
            monarch_db,
            iban=CH_IBAN,
            currency="CHF",
            transactions=[
                _tx("2026-07-05", -50.0, "Example Gym"),
                _tx("2026-09-05", -50.0, "Example Gym"),
            ],
            tag="leak-bills",
            period_start="2026-07-01",
        )
        after = monarch_tools.upcoming_bills(
            {"iban": CH_IBAN, "reference_date": REF, "days_ahead": 30}
        )
        assert after["count"] == before["count"] == 3
        assert after["total_in_window"] == before["total_in_window"]
        assert all(bill["counterparty"] != "Example Gym" for bill in after["bills"])


class TestCashFlowForecast:
    def _params(self, **overrides) -> Dict[str, Any]:
        params = {
            "iban": CH_IBAN,
            "reference_date": REF,
            "lookback_months": 12,
            "forecast_months": 3,
            "confidence_level": 0.9,
        }
        params.update(overrides)
        return params

    def test_shape_and_values(self, monarch_tools):
        result = monarch_tools.cash_flow_forecast(self._params())
        assert result["success"] is True
        assert result["method"] == "schedule_first_hybrid"
        assert result["forecast_months"] == 3
        assert result["lookback_months"] == 12
        assert result["confidence_level"] == 0.9
        assert result["currencies"] == ["CHF"]

        entry = result["results"][0]
        assert entry["currency"] == "CHF"
        assert entry["months_used"] == 12
        assert entry["seasonality_applied"] is True  # saisonale Lebensmittel
        assert entry["recurring_monthly"] == pytest.approx(1213.99, abs=0.02)
        assert [month["month"] for month in entry["months"]] == ["2026-09", "2026-10", "2026-11"]

        for month in entry["months"]:
            assert month["income"] == pytest.approx(5000.0, abs=0.5)  # flaches Gehalt
            assert month["recurring"] == pytest.approx(1213.99, abs=0.02)
            assert month["variable"] > 0
            assert month["expense_total"] == pytest.approx(month["recurring"] + month["variable"], abs=0.02)
            assert month["net"] == pytest.approx(month["income"] - month["expense_total"], abs=0.02)

    def test_balance_chain_and_bands(self, monarch_db, monarch_tools):
        result = monarch_tools.cash_flow_forecast(self._params())
        account_id = monarch_db.find_account_id_by_iban(CH_IBAN)
        assert account_id is not None
        expected_start = monarch_db.balance_at(account_id, REF)["balance"]

        assert result["balance"] is not None
        assert result["balance"]["start_balance"] == pytest.approx(expected_start, abs=0.02)

        months = result["results"][0]["months"]
        assert months[0]["balance"] == pytest.approx(expected_start + months[0]["net"], abs=0.02)
        for prev, cur in zip(months, months[1:]):
            assert cur["balance"] == pytest.approx(prev["balance"] + cur["net"], abs=0.02)

        for month in months:
            assert month["balance_low"] <= month["balance"] <= month["balance_high"]
            assert month["income_low"] <= month["income"] <= month["income_high"]
            assert month["variable"] <= month["variable_high"]

    def test_determinism(self, monarch_tools):
        first = monarch_tools.cash_flow_forecast(self._params())
        second = monarch_tools.cash_flow_forecast(self._params())
        assert first["results"] == second["results"]
        assert first["balance"] == second["balance"]

    def test_clamped_and_error_params(self, monarch_tools):
        clamped = monarch_tools.cash_flow_forecast(
            self._params(forecast_months=100, confidence_level=0.1)
        )
        assert clamped["success"] is True
        assert clamped["forecast_months"] == 24
        assert clamped["confidence_level"] == 0.5

        bad_date = monarch_tools.cash_flow_forecast({"reference_date": "2026-13-40"})
        assert bad_date["success"] is False
        assert bad_date["error_class"] == "invalid_reference_date"

        unknown = monarch_tools.cash_flow_forecast({"iban": "XX0000000000000000000000"})
        assert unknown["success"] is False
        assert unknown["error_class"] == "unknown_iban"

    def test_no_data(self, empty_tools):
        result = empty_tools.cash_flow_forecast({"reference_date": REF})
        assert result["success"] is False
        assert result["error_class"] == "no_data"

    def test_no_future_leakage(self, monarch_db, monarch_tools):
        """Große Buchung nach dem Referenzdatum darf Prognose NICHT verandern."""
        before = monarch_tools.cash_flow_forecast(self._params())
        _import(
            monarch_db,
            iban=CH_IBAN,
            currency="CHF",
            transactions=[_tx("2026-09-15", -5000.0, "Example Grocer")],
            tag="leak-forecast",
            period_start="2026-09-01",
        )
        after = monarch_tools.cash_flow_forecast(self._params())
        assert after["results"] == before["results"]
        assert after["balance"] == before["balance"]

    def test_transfer_exclusion(self, monarch_db, monarch_tools):
        """Transfers affect the bank balance but not the recurring cashflow fit."""
        before = monarch_tools.cash_flow_forecast(self._params())
        account_id = monarch_db.find_account_id_by_iban(CH_IBAN)
        assert account_id is not None

        _import(
            monarch_db,
            iban=CH_IBAN,
            currency="CHF",
            transactions=[_tx("2026-08-05", -500.0, "Example Savings")],
            tag="transfer-out",
            period_start="2026-08-01",
            # opening_balance=None: nur die einzelne Buchung injizieren, OHNE
            # falschen Saldo-Anker (0.0) zu erzeugen -- der 2025-09-Auszug
            # (opening 10000) bleibt der Anker fuer balance_at.
            opening_balance=None,
        )
        eur_account_id = _import(
            monarch_db,
            iban=EUR_IBAN,
            currency="EUR",
            transactions=[_tx("2026-08-20", 500.0, "Example Savings", "EUR")],
            tag="transfer-in",
            period_start="2026-08-01",
            opening_balance=None,
        )

        unlinked = monarch_tools.cash_flow_forecast(self._params())
        assert unlinked["results"] != before["results"]  # -500 zahlt jetzt als variable Ausgabe

        outgoing = monarch_db.query_transactions(account_id=account_id, counterparty_like="savings")
        incoming = monarch_db.query_transactions(account_id=eur_account_id, counterparty_like="savings")
        assert len(outgoing) == 1
        assert len(incoming) == 1
        monarch_db.link_transfer(outgoing_tx_id=outgoing[0].id, incoming_tx_id=incoming[0].id)

        linked = monarch_tools.cash_flow_forecast(self._params())
        assert linked["balance"]["start_balance"] == pytest.approx(
            monarch_db.balance_at(account_id, REF)["balance"]
        )
        assert linked["balance"]["start_balance"] == pytest.approx(before["balance"]["start_balance"] - 500)
        for original, adjusted in zip(before["results"][0]["months"], linked["results"][0]["months"]):
            for key in original:
                if key.startswith("balance"):
                    assert adjusted[key] == pytest.approx(original[key] - 500)
                else:
                    assert adjusted[key] == original[key]


class TestSubscriptionAudit:
    def test_detects_and_ranks(self, monarch_tools):
        result = monarch_tools.subscription_audit({"iban": CH_IBAN})
        assert result["success"] is True
        assert result["currency"] == "CHF"
        assert result["count"] == 3

        groups = result["groups"]
        # absteigend nach Monatskosten sortiert
        costs = [group["monthly_cost"] for group in groups]
        assert costs == sorted(costs, reverse=True)
        assert groups[0]["counterparty"] == "Example Landlord"

        by_cpty = {group["counterparty"]: group for group in groups}
        assert by_cpty["Example Landlord"]["monthly_cost"] == 1200.0
        assert by_cpty["Example Landlord"]["subscription_like"] is False
        assert by_cpty["Example Grocer"]["monthly_cost"] == pytest.approx(625.0, abs=0.01)
        assert by_cpty["Netflix"]["monthly_cost"] == 13.99
        assert by_cpty["Netflix"]["subscription_like"] is True
        assert by_cpty["Netflix"]["price_change"] is None

        total = 1200.0 + 625.0 + 13.99
        assert result["total_monthly"] == pytest.approx(total, abs=0.01)
        assert result["total_annual"] == pytest.approx(total * 12, abs=0.05)

    def test_price_change_detection(self, price_db):
        tools = FinanceTools(db=price_db)
        result = tools.subscription_audit({"iban": CH_IBAN})
        assert result["success"] is True
        assert result["count"] == 1

        group = result["groups"][0]
        assert group["counterparty"] == "Example Telecom"
        assert group["occurrences"] == 4
        assert group["monthly_cost"] == 45.0  # aktueller Preis
        assert group["price_change"] == {
            "old": 30.0,
            "new": 45.0,
            "change_pct": 50.0,
            "date": "2026-08-15",
        }
        assert result["total_monthly"] == 45.0

    def test_empty_db(self, empty_tools):
        result = empty_tools.subscription_audit({})
        assert result["success"] is True
        assert result["count"] == 0
        assert result["groups"] == []
        assert result["total_monthly"] is None


class TestMultiCurrency:
    def test_forecast_splits_currencies_and_drops_balance(self, multi_db):
        tools = FinanceTools(db=multi_db)
        result = tools.cash_flow_forecast(
            {"reference_date": REF, "lookback_months": 12, "forecast_months": 2}
        )
        assert result["success"] is True
        assert result["currencies"] == ["CHF", "EUR"]
        assert result["balance"] is None  # keine einheitliche Start-Balance
        assert [entry["currency"] for entry in result["results"]] == ["CHF", "EUR"]
        assert result["results"][0]["months_used"] == 12
        assert result["results"][1]["months_used"] == 4
        assert result["results"][1]["recurring_monthly"] == 600.0

    def test_bills_and_audit_totals_are_none(self, multi_db):
        tools = FinanceTools(db=multi_db)
        bills = tools.upcoming_bills({"reference_date": REF, "days_ahead": 30})
        assert bills["success"] is True
        assert bills["count"] == 3
        assert bills["currency"] is None
        assert bills["total_in_window"] is None

        audit = tools.subscription_audit({})
        assert audit["success"] is True
        assert audit["count"] == 3
        assert audit["total_monthly"] is None
        assert audit["total_annual"] is None
