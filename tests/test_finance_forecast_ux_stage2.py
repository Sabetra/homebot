"""Forecast-UX AP2 Stage 2: F01/F02/F08 (additive, byte-kompatible Default).

Validiert die Stage-2-Änderungen in ``finance/tools.py``:

* F01 ``upcoming_bills``:
  - Bestands-Keys bleiben byte-kompatibel (pinned: Miete 1200,
    Lebensmittel-Mittel 625, Netflix 13.99, total_in_window 1838.99).
  - Neu: ``window_occurrences`` = ALLE Vorkommen im Fenster (nicht nur
    die nächste Fälligkeit je Gruppe); Serien liefern die exakte
    Expansion (Rhythmus/Ausnahmen/Geltung) und unterdrücken ihre
    Heuristik (keine Doppelzählung).
* F02 ``subscription_audit``:
  - Bestands-Keys unverändert; unbestätigte Gruppen tragen cadence=None.
  - Neu: Rhythmus-Äquivalente bestätigter Serien
    (120/Quartal => 40/Monat, 480/Jahr), next_due_date (Ausnahmen
    exakt einmal), confirmed_count + Summen.
* F08 ``cash_flow_forecast``:
  - ``include_series`` strikt opt-in (Default False => byte-kompatible
    Ausgabe, keine Serien-Keys).
  - Opt-in: Serien-Plan pro Monat (series_plan/net_with_series/
    balance_with_series), payload.series, und die Serien-Paare fallen
    aus dem statistischen Rest (keine Doppelzählung; Gehalt planbar;
    gekündigte Serien nicht projiziert).
"""
from __future__ import annotations

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
REF = "2026-08-28"  # Freitag, Referenzdatum (identisch zur Monarch-Kern-Suite)


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
    """Synthetischen Kontoauszug importieren und auf sauberen Import prüfen."""
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
    assert inserted == len(transactions), f"{tag}: nicht alle Buchungen eingefügt"
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


def _make_series(db: FinanceDB, **overrides: Any) -> Any:
    """Bestätigte Serie erzeugen (Defaults konservativ, Override je Test)."""
    base: Dict[str, Any] = dict(
        iban=CH_IBAN,
        direction="expense",
        cadence="monthly",
        period_n=1,
        anchor_date="2026-08-15",
        amount_cents=990,
        currency="CHF",
        counterparty="Example Utility",
        status="active",
        source="manual",
    )
    base.update(overrides)
    return db.create_series(**base)


@pytest.fixture
def monarch_db(tmp_path) -> FinanceDB:
    db = FinanceDB(str(tmp_path / "monarch_s2.db"))
    _seed_monarch_history(db)
    return db


@pytest.fixture
def monarch_tools(monarch_db: FinanceDB) -> FinanceTools:
    return FinanceTools(db=monarch_db)


@pytest.fixture
def quarterly_db(tmp_path) -> FinanceDB:
    """Quartals-Versicherung: 2 Zahlungen (05-15, 08-15) je 120.00."""
    db = FinanceDB(str(tmp_path / "quarterly_s2.db"))
    _import(
        db,
        iban=CH_IBAN,
        currency="CHF",
        transactions=[
            _tx("2026-05-15", -120.0, "Example Insurer"),
            _tx("2026-08-15", -120.0, "Example Insurer"),
        ],
        tag="quarterly-history",
        period_start="2026-05-01",
    )
    return db


@pytest.fixture
def quarterly_tools(quarterly_db: FinanceDB, quarterly_series_id: int) -> FinanceTools:
    _ = quarterly_series_id
    return FinanceTools(db=quarterly_db)


@pytest.fixture
def quarterly_series_id(quarterly_db: FinanceDB) -> int:
    series = _make_series(
        quarterly_db,
        direction="expense",
        cadence="n_months",
        period_n=3,
        anchor_date="2026-05-15",
        amount_cents=12000,
        counterparty="Example Insurer",
    )
    return int(series.id)


class TestF01UpcomingBills:
    def test_pinned_legacy_keys_unchanged(self, monarch_tools: FinanceTools) -> None:
        """Pinned-Default: alle Bestands-Keys exakt wie vor Stage 2."""
        result = monarch_tools.upcoming_bills(
            {"iban": CH_IBAN, "reference_date": REF, "days_ahead": 30}
        )
        assert result["success"] is True
        assert result["count"] == 3
        assert [b["counterparty"] for b in result["bills"]] == [
            "Example Landlord",
            "Example Grocer",
            "Netflix",
        ]
        landlord, grocer, netflix = result["bills"]
        assert landlord["next_due"] == "2026-09-03"
        assert landlord["amount"] == 1200.0
        assert landlord["is_fixed"] is True
        assert landlord["subscription_like"] is False
        assert grocer["next_due"] == "2026-09-10"
        assert grocer["amount"] == 625.0
        assert netflix["next_due"] == "2026-09-12"
        assert netflix["amount"] == 13.99
        assert netflix["subscription_like"] is True
        assert result["total_in_window"] == pytest.approx(1838.99, abs=0.01)
        # Neu in Stage 2 (additive):
        assert "window_occurrence_count" in result
        assert isinstance(result["window_occurrences"], list)

    def test_window_occurrences_full_calendar(self, monarch_tools: FinanceTools) -> None:
        """60-Tage-Fenster: Netflix zahlt 09-12 UND 10-12 (beide Vorkommen)."""
        result = monarch_tools.upcoming_bills(
            {"iban": CH_IBAN, "reference_date": REF, "days_ahead": 60}
        )
        assert result["success"] is True
        occ = result["window_occurrences"]
        assert [(o["date"], o["counterparty"]) for o in occ] == [
            ("2026-09-03", "Example Landlord"),
            ("2026-09-10", "Example Grocer"),
            ("2026-09-12", "Netflix"),
            ("2026-10-03", "Example Landlord"),
            ("2026-10-10", "Example Grocer"),
            ("2026-10-12", "Netflix"),
        ]
        assert result["window_occurrence_count"] == 6
        assert result["window_bills_total"] == pytest.approx(
            2 * (1200.0 + 625.0 + 13.99), abs=0.01
        )
        # Bestands-Keys bleiben bei "nächste Fälligkeit" (byte-kompatibel):
        assert result["count"] == 3
        assert result["total_in_window"] == pytest.approx(1838.99, abs=0.01)

    def test_series_suppresses_heuristic_no_double_count(
        self, monarch_db: FinanceDB, monarch_tools: FinanceTools
    ) -> None:
        """Bestätigte Miete-Serie: exakte Expansion, Heuristik wird gedämpft."""
        series = _make_series(
            monarch_db,
            direction="expense",
            cadence="monthly",
            anchor_date="2026-08-03",
            amount_cents=120000,
            counterparty="Example Landlord",
        )
        result = monarch_tools.upcoming_bills(
            {"iban": CH_IBAN, "reference_date": REF, "days_ahead": 30}
        )
        assert result["success"] is True
        occ = result["window_occurrences"]
        landlord_occ = [o for o in occ if o["counterparty"] == "Example Landlord"]
        assert len(landlord_occ) == 1
        assert landlord_occ[0]["date"] == "2026-09-03"
        assert landlord_occ[0]["amount"] == 1200.0
        assert landlord_occ[0]["source"] == "series"
        assert landlord_occ[0]["series_id"] == int(series.id)
        assert landlord_occ[0]["exception"] is None
        # Keine Doppelzählung: genau 3 Vorkommen (Miete, Lebensmittel, Netflix).
        assert result["window_occurrence_count"] == 3
        assert result["window_bills_total"] == pytest.approx(1838.99, abs=0.01)

    def test_quarterly_series_occurrence_in_window(
        self, quarterly_tools: FinanceTools
    ) -> None:
        """120/Quartal: Vorkommen 2026-11-15 (Anker 15., n_months=3)."""
        result = quarterly_tools.upcoming_bills(
            {"iban": CH_IBAN, "reference_date": REF, "days_ahead": 120}
        )
        assert result["success"] is True
        occ = result["window_occurrences"]
        assert [(o["date"], o["source"]) for o in occ] == [
            ("2026-11-15", "series")
        ]
        assert occ[0]["amount"] == 120.0
        assert occ[0]["counterparty"] == "Example Insurer"
        assert result["window_bills_total"] == pytest.approx(120.0, abs=0.01)

    def test_skip_exception_excluded_from_occurrences(self, monarch_tools: FinanceTools) -> None:
        """skip-Ausnahme 09-15: nur 10-15 erscheint (exakt einmal)."""
        db = monarch_tools._db
        series = _make_series(
            db,
            direction="expense",
            cadence="monthly",
            anchor_date="2026-06-15",
            amount_cents=990,
            counterparty="Example Utility",
        )
        db.set_series_exception(int(series.id), "2026-09-15", "skip")
        result = monarch_tools.upcoming_bills(
            {"iban": CH_IBAN, "reference_date": REF, "days_ahead": 60}
        )
        assert result["success"] is True
        utility_occ = [
            o for o in result["window_occurrences"] if o["counterparty"] == "Example Utility"
        ]
        assert [(o["date"], o["source"]) for o in utility_occ] == [
            ("2026-10-15", "series")
        ]

    def test_ended_series_not_projected(self, monarch_tools: FinanceTools) -> None:
        """effective_to in der Vergangenheit: kein Vorkommen mehr, Heuristik gedämpft."""
        db = monarch_tools._db
        series = _make_series(
            db,
            direction="expense",
            cadence="monthly",
            anchor_date="2026-06-15",
            amount_cents=990,
            counterparty="Example Utility",
            effective_to="2026-07-01",
        )
        result = monarch_tools.upcoming_bills(
            {"iban": CH_IBAN, "reference_date": REF, "days_ahead": 60}
        )
        assert result["success"] is True
        assert not [
            o for o in result["window_occurrences"] if o["counterparty"] == "Example Utility"
        ]


class TestF02SubscriptionAudit:
    def test_quarterly_equivalents_40_and_480(
        self, quarterly_tools: FinanceTools, quarterly_series_id: int
    ) -> None:
        """120/Quartal => 40/Monat, 480/Jahr (bestätigte Serie)."""
        result = quarterly_tools.subscription_audit({"iban": CH_IBAN, "reference_date": REF})
        assert result["success"] is True
        audit = [a for a in result["groups"] if a["counterparty"] == "Example Insurer"]
        assert len(audit) == 1
        a = audit[0]
        # Bestands-Keys:
        assert a["occurrences"] == 2
        assert a["monthly_cost"] == pytest.approx(120.0, abs=0.01)
        assert a["annual_cost"] == pytest.approx(1440.0, abs=0.01)
        assert a["next_due_date"] == "2026-11-15"
        # Neue Rhythmus-Keys:
        assert a["cadence"] == "n_months"
        assert a["period_n"] == 3
        assert a["payment_per_period"] == pytest.approx(120.0, abs=0.01)
        assert a["monthly_equivalent"] == pytest.approx(40.0, abs=0.01)
        assert a["annual_equivalent"] == pytest.approx(480.0, abs=0.01)
        assert a["series_ids"] == [quarterly_series_id]
        # Top-Level (Stage 2, additive):
        assert result["confirmed_count"] == 1
        assert result["series_monthly_equivalent"] == pytest.approx(40.0, abs=0.01)
        assert result["series_annual_equivalent"] == pytest.approx(480.0, abs=0.01)
        assert result["total_monthly"] == pytest.approx(120.0, abs=0.01)
        assert result["total_annual"] == pytest.approx(1440.0, abs=0.01)

    def test_unconfirmed_group_has_none_equivalents(
        self, monarch_tools: FinanceTools
    ) -> None:
        """Ohne bestätigte Serie: cadence=None, Äquivalente None (konservativ)."""
        result = monarch_tools.subscription_audit({"iban": CH_IBAN, "reference_date": REF})
        assert result["success"] is True
        netflix = [a for a in result["groups"] if a["counterparty"] == "Netflix"][0]
        assert netflix["monthly_cost"] == pytest.approx(13.99, abs=0.01)
        assert netflix["annual_cost"] == pytest.approx(167.88, abs=0.01)
        assert netflix["cadence"] is None
        assert netflix["period_n"] is None
        assert netflix["payment_per_period"] is None
        assert netflix["monthly_equivalent"] is None
        assert netflix["annual_equivalent"] is None
        assert netflix["next_due_date"] is None
        assert netflix["series_ids"] == []
        assert result["confirmed_count"] == 0
        assert result["series_monthly_equivalent"] == 0.0
        assert result["series_annual_equivalent"] == 0.0

    def test_skip_exception_shifts_next_due(self, quarterly_db: FinanceDB) -> None:
        """skip 11-15: next_due = 2027-02-15, Ausnahme exakt einmal."""
        tools = FinanceTools(db=quarterly_db)
        series = _make_series(
            quarterly_db,
            direction="expense",
            cadence="n_months",
            period_n=3,
            anchor_date="2026-05-15",
            amount_cents=12000,
            counterparty="Example Insurer",
        )
        quarterly_db.set_series_exception(int(series.id), "2026-11-15", "skip")
        result = tools.subscription_audit({"iban": CH_IBAN, "reference_date": REF})
        audit = [a for a in result["groups"] if a["counterparty"] == "Example Insurer"]
        assert len(audit) == 1
        assert audit[0]["next_due_date"] == "2027-02-15"

    def test_pinned_netflix_legacy_keys(self, monarch_tools: FinanceTools) -> None:
        """Netflix (13.99/Monat): Bestands-Keys byte-kompatibel."""
        result = monarch_tools.subscription_audit({"iban": CH_IBAN, "reference_date": REF})
        netflix = [a for a in result["groups"] if a["counterparty"] == "Netflix"][0]
        assert netflix["monthly_cost"] == pytest.approx(13.99, abs=0.01)
        assert netflix["annual_cost"] == pytest.approx(167.88, abs=0.01)
        assert netflix["subscription_like"] is True
        # Neue Stage-2-Keys (unbestaetigt: keine Serie):
        assert netflix["cadence"] is None
        assert netflix["next_due_date"] is None
        assert netflix["series_ids"] == []


class TestF08CashFlowForecast:
    def test_default_byte_compatible(self, monarch_tools: FinanceTools) -> None:
        """Default (ohne include_series): byte-kompatible Ausgabe, keine Serien-Keys."""
        default = monarch_tools.cash_flow_forecast(
            {"iban": CH_IBAN, "reference_date": REF, "forecast_months": 6, "confidence_level": 0.8}
        )
        explicit_off = monarch_tools.cash_flow_forecast(
            {
                "iban": CH_IBAN,
                "reference_date": REF,
                "forecast_months": 6,
                "confidence_level": 0.8,
                "include_series": False,
            }
        )
        assert default == explicit_off
        assert "series" not in default
        entry = default["results"][0]
        for month in entry["months"]:
            assert "series_plan" not in month
            assert "net_with_series" not in month
            assert "balance_with_series" not in month
        # Pinned-Werte (pro currency-Entry):
        for month in entry["months"]:
            assert month["income"] == pytest.approx(5000.0, abs=0.01)
            assert month["variable"] > 0
        assert entry["recurring_monthly"] == pytest.approx(1213.99, abs=0.01)
        avg_variable = sum(m["variable"] for m in entry["months"]) / len(entry["months"])
        assert avg_variable == pytest.approx(625.0, abs=1.0)
        assert entry["months_used"] == 12

    def test_byte_compatible_even_with_series_in_db(self, quarterly_tools: FinanceTools) -> None:
        """Bestehende Serie im DB ändert das Default (include_series=False) NICHT."""
        with_series = quarterly_tools.cash_flow_forecast(
            {"iban": CH_IBAN, "reference_date": REF, "forecast_months": 6, "confidence_level": 0.8}
        )
        explicit_off = quarterly_tools.cash_flow_forecast(
            {
                "iban": CH_IBAN,
                "reference_date": REF,
                "forecast_months": 6,
                "confidence_level": 0.8,
                "include_series": False,
            }
        )
        assert with_series == explicit_off
        assert "series" not in with_series
        # Statistischer Fit bleibt unangetastet (2×120 fixed => 240/12):
        assert with_series["results"][0]["recurring_monthly"] == pytest.approx(20.0, abs=0.01)

    def test_series_plan_opt_in_quarterly(
        self, quarterly_tools: FinanceTools, quarterly_series_id: int
    ) -> None:
        """include_series=True: 120/Quartal landet exakt im November-Plan."""
        result = quarterly_tools.cash_flow_forecast(
            {
                "iban": CH_IBAN,
                "reference_date": REF,
                "forecast_months": 6,
                "confidence_level": 0.8,
                "include_series": True,
            }
        )
        assert result["success"] is True
        entry = result["results"][0]
        assert [m["month"] for m in entry["months"]] == [
            "2026-09", "2026-10", "2026-11", "2026-12", "2027-01", "2027-02"
        ]
        plans = {m["month"]: m["series_plan"] for m in entry["months"]}
        assert plans == {
            "2026-09": 0.0,
            "2026-10": 0.0,
            "2026-11": -120.0,
            "2026-12": 0.0,
            "2027-01": 0.0,
            "2027-02": 0.0,
        }
        nov = entry["months"][2]
        assert nov["net_with_series"] == pytest.approx(nov["net"] - 120.0, abs=0.01)
        assert result["series"]["count"] == 1
        assert result["series"]["occurrences_in_window"] == 1
        series_row = result["series"]["series"][0]
        assert series_row["id"] == quarterly_series_id
        assert series_row["counterparty"] == "Example Insurer"
        assert series_row["currency"] == "CHF"
        assert series_row["direction"] == "expense"
        assert series_row["cadence"] == "n_months"
        assert series_row["period_n"] == 3
        assert series_row["amount"] == pytest.approx(120.0, abs=0.01)
        assert series_row["status"] == "active"
        # Keine Doppelzählung: Serien-Paar fällt aus dem statistischen Rest.
        assert entry["recurring_monthly"] == pytest.approx(0.0, abs=0.01)

    def test_salary_is_plannable_not_residual(
        self, monarch_db: FinanceDB, monarch_tools: FinanceTools
    ) -> None:
        """Gehalt-Serie (Income): planbar, kein statistischer Rest mehr."""
        _make_series(
            monarch_db,
            direction="income",
            cadence="monthly",
            anchor_date="2026-08-01",
            amount_cents=500000,
            counterparty="Example Employer",
        )
        default = monarch_tools.cash_flow_forecast(
            {"iban": CH_IBAN, "reference_date": REF, "forecast_months": 6, "confidence_level": 0.8}
        )
        planned = monarch_tools.cash_flow_forecast(
            {
                "iban": CH_IBAN,
                "reference_date": REF,
                "forecast_months": 6,
                "confidence_level": 0.8,
                "include_series": True,
            }
        )
        assert default["results"][0]["months"][0]["income"] == pytest.approx(5000.0, abs=0.01)
        assert planned["results"][0]["months"][0]["income"] == pytest.approx(0.0, abs=0.01)
        for month in planned["results"][0]["months"]:
            assert month["series_plan"] == pytest.approx(5000.0, abs=0.01)
            assert month["net"] == pytest.approx(-1838.99, abs=0.01)
            assert month["net_with_series"] == pytest.approx(3161.01, abs=0.01)
        assert planned["results"][0]["recurring_monthly"] == pytest.approx(1213.99, abs=0.01)

    def test_cancelled_series_not_projected(
        self, monarch_db: FinanceDB, monarch_tools: FinanceTools
    ) -> None:
        """gekündigte Serie (effective_to vergangen): nicht im Plan, nicht im Fit."""
        _make_series(
            monarch_db,
            direction="expense",
            cadence="monthly",
            anchor_date="2026-08-03",
            amount_cents=120000,
            counterparty="Example Landlord",
            effective_to="2026-07-31",
        )
        default = monarch_tools.cash_flow_forecast(
            {"iban": CH_IBAN, "reference_date": REF, "forecast_months": 6, "confidence_level": 0.8}
        )
        planned = monarch_tools.cash_flow_forecast(
            {
                "iban": CH_IBAN,
                "reference_date": REF,
                "forecast_months": 6,
                "confidence_level": 0.8,
                "include_series": True,
            }
        )
        assert default["results"][0]["recurring_monthly"] == pytest.approx(1213.99, abs=0.01)
        # Opt-in: gekündigte Serie zählt NICHT als planbar => aus dem Rest gefiltert.
        assert planned["results"][0]["recurring_monthly"] == pytest.approx(13.99, abs=0.01)
        assert planned["series"] == {"count": 0, "series": [], "occurrences_in_window": 0}
        for month in planned["results"][0]["months"]:
            assert month["series_plan"] == 0.0

    def test_balance_with_series_chain(
        self, quarterly_tools: FinanceTools
    ) -> None:
        """balance_with_series = Balance + kumulierter Serien-Plan (exakt)."""
        result = quarterly_tools.cash_flow_forecast(
            {
                "iban": CH_IBAN,
                "reference_date": REF,
                "forecast_months": 6,
                "confidence_level": 0.8,
                "include_series": True,
            }
        )
        account_id = quarterly_tools._db.find_account_id_by_iban(CH_IBAN)
        expected_start = quarterly_tools._db.balance_at(account_id, REF)["balance"]
        assert result["balance"]["start_balance"] == pytest.approx(expected_start, abs=0.01)
        cumulative = 0.0
        for month in result["results"][0]["months"]:
            cumulative += month["series_plan"]
            assert month["balance_with_series"] == pytest.approx(
                month["balance"] + cumulative, abs=0.01
            )