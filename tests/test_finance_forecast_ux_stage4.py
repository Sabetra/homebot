"""Forecast-UX AP3 Stage 4: Geschätzte Kadenz, Prognose-Unterdrückung, manuelle Serien.

Validiert die AP3-Änderungen in ``finance/tools.py`` (forecast-only:
KEINE Buchungen, KEINE Mutation bestätigter Serien):

* Geschätzte Kadenz (konservativ, None-sicher):
  - ``upcoming_bills`` / ``subscription_audit``: ``estimated_*``-Felder +
    ``cadence_source`` (series > estimated > None).
  - 3 Beobachtungen (Monat, Tag 15) => 'monthly', confidence 'medium'.
  - 2 Beobachtungen => confidence 'low', aber geschätzt.
  - Unregelmäßige Gruppe => alle ``estimated_*`` None, ``cadence_source``
    None (Legacy-Heuristik ``next_due`` bleibt erhalten).
  - Bestätigte Serie => ``cadence_source='series'`` (priorisiert).
* Prognose-Unterdrückung (reversibel, forecast-only):
  - ``suppress_forecast`` entfernt die Gruppe aus ``upcoming_bills`` und
    ``subscription_audit``; Buchungen und bestätigte Serien bleiben aktiv.
  - Idempotent: zweiter Aufruf aktualisiert currency/reason (keine Duplikate).
  - ``restore_forecast`` reaktiviert (Zeile bleibt, active=0).
  - ``list_forecast_suppressions``: Default aktiv, ``include_inactive``,
    IBAN-Filter; Fehlerklassen (invalid_param / not_found).
  - Scope: Unterdrückung auf IBAN A trifft nicht IBAN B.
* Manuelle Serien (Create/Update):
  - ``create_manual_series``: Happy-Path, Dict-Form, Projektion in
    ``upcoming_bills`` (Fenster-Abhängigkeit), Validierung.
  - ``update_manual_series``: Feld-Updates, Revision-Inkrement,
    Optimistic Locking (stale ``expected_revision`` => 'conflict'),
    nicht existierende Serie => 'not_found'.

Deterministisch (kein LLM, keine echten DB-Schreibzugriffe):
"""
from __future__ import annotations

import sys
import types
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from finance.db_schema import FinanceDB  # noqa: E402
from finance.tools import FinanceTools  # noqa: E402

CH_IBAN = "CH9300762011623852957"
DE_IBAN = "DE89370400440532013000"
REF = "2026-08-28"  # Referenzdatum (konsistent mit Stage 2/3)
REF_DATE = date.fromisoformat(REF)


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


def _count_rows(db: FinanceDB, table: str) -> int:
    with db._connect() as conn:
        return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


@pytest.fixture
def ap3_db(tmp_path) -> FinanceDB:
    """Drei Ausgabengruppen: geschätzbar (3x), unregelmäßig, geschätzbar (2x).

    * Net:    2026-05-15/06-15/07-15 je -9.90  => 'monthly', confidence 'medium'
    * Bau:    2026-03-05/06-20/07-30 variabel   => keine Kadenz-Schätzung
    * Stream: 2026-07-12/08-12 je -13.99        => 'monthly', confidence 'low'
    """
    db = FinanceDB(str(tmp_path / "ap3_s4.db"))
    _import(
        db,
        iban=CH_IBAN,
        currency="CHF",
        transactions=[
            _tx("2026-05-15", -9.90, "Net"),
            _tx("2026-06-15", -9.90, "Net"),
            _tx("2026-07-15", -9.90, "Net"),
            _tx("2026-03-05", -250.00, "Bau"),
            _tx("2026-06-20", -400.00, "Bau"),
            _tx("2026-07-30", -120.00, "Bau"),
            _tx("2026-07-12", -13.99, "Stream"),
            _tx("2026-08-12", -13.99, "Stream"),
        ],
        tag="ap3-history",
        period_start="2026-03-01",
        opening_balance=10000.0,
    )
    return db


@pytest.fixture
def ap3_tools(ap3_db: FinanceDB) -> FinanceTools:
    return FinanceTools(db=ap3_db)


@pytest.fixture
def clean_db(tmp_path) -> FinanceDB:
    """Leeres Konto (1 Gehalts-Buchung als Kontobezug, keine Ausgabengruppen)."""
    db = FinanceDB(str(tmp_path / "ap3_s4_clean.db"))
    _import(
        db,
        iban=CH_IBAN,
        currency="CHF",
        transactions=[_tx("2026-08-01", 5000.0, "Example Employer")],
        tag="ap3-clean-seed",
        period_start="2026-08-01",
        opening_balance=5000.0,
    )
    return db


@pytest.fixture
def clean_tools(clean_db: FinanceDB) -> FinanceTools:
    return FinanceTools(db=clean_db)


@pytest.fixture
def scope_db(tmp_path) -> FinanceDB:
    """Gleiche Gegenpartei 'Net' auf zwei Konten/Währungen (F03-Isolation)."""
    db = FinanceDB(str(tmp_path / "ap3_s4_scope.db"))
    _import(
        db,
        iban=CH_IBAN,
        currency="CHF",
        transactions=[
            _tx("2026-05-15", -9.90, "Net"),
            _tx("2026-06-15", -9.90, "Net"),
            _tx("2026-07-15", -9.90, "Net"),
        ],
        tag="ap3-scope-ch",
        period_start="2026-05-01",
    )
    _import(
        db,
        iban=DE_IBAN,
        currency="EUR",
        transactions=[
            _tx("2026-05-15", -9.90, "Net", currency="EUR"),
            _tx("2026-06-15", -9.90, "Net", currency="EUR"),
            _tx("2026-07-15", -9.90, "Net", currency="EUR"),
        ],
        tag="ap3-scope-de",
        period_start="2026-05-01",
    )
    return db


@pytest.fixture
def scope_tools(scope_db: FinanceDB) -> FinanceTools:
    return FinanceTools(db=scope_db)

# ---------------------------------------------------------------------------
# 1) Geschätzte Kadenz (estimated_*) + cadence_source
# ---------------------------------------------------------------------------


class TestAP3EstimatedCadenceUpcomingBills:
    def test_confident_monthly_estimate(self, ap3_tools: FinanceTools) -> None:
        """3 Beobachtungen (Tag 15) => 'monthly', 'medium', Gitter-Fälligkeit."""
        result = ap3_tools.upcoming_bills(
            {"iban": CH_IBAN, "reference_date": REF, "days_ahead": 30}
        )
        assert result["success"] is True
        net = next(b for b in result["bills"] if b["counterparty"] == "Net")
        assert net["estimated_cadence"] == "monthly"
        assert net["estimated_period_n"] is None  # monthly => kein N
        assert net["estimated_anchor"] == "2026-05-15"
        # Anker 05-15, Referenz 08-28 => nächstes Gitterdatum 2026-09-15
        assert net["estimated_next_due"] == "2026-09-15"
        assert net["estimated_monthly"] == pytest.approx(9.9, abs=0.01)
        assert net["estimated_annual"] == pytest.approx(118.8, abs=0.01)
        assert net["estimated_confidence"] == "medium"
        assert net["cadence_source"] == "estimated"

    def test_two_observations_low_confidence(self, ap3_tools: FinanceTools) -> None:
        """2 Beobachtungen => geschätzt, aber confidence 'low'."""
        result = ap3_tools.upcoming_bills(
            {"iban": CH_IBAN, "reference_date": REF, "days_ahead": 30}
        )
        stream = next(b for b in result["bills"] if b["counterparty"] == "Stream")
        assert stream["estimated_cadence"] == "monthly"
        assert stream["estimated_anchor"] == "2026-07-12"
        assert stream["estimated_next_due"] == "2026-09-12"
        assert stream["estimated_monthly"] == pytest.approx(13.99, abs=0.01)
        assert stream["estimated_annual"] == pytest.approx(167.88, abs=0.01)
        assert stream["estimated_confidence"] == "low"
        assert stream["cadence_source"] == "estimated"

    def test_irregular_group_stays_none(self, ap3_tools: FinanceTools) -> None:
        """Unregelmäßige Gruppe => KEIN Fallback, Legacy-Heuristik bleibt."""
        result = ap3_tools.upcoming_bills(
            {"iban": CH_IBAN, "reference_date": REF, "days_ahead": 30}
        )
        bau = next(b for b in result["bills"] if b["counterparty"] == "Bau")
        assert bau["estimated_cadence"] is None
        assert bau["estimated_period_n"] is None
        assert bau["estimated_anchor"] is None
        assert bau["estimated_next_due"] is None
        assert bau["estimated_monthly"] is None
        assert bau["estimated_annual"] is None
        assert bau["estimated_confidence"] is None
        assert bau["cadence_source"] is None
        # Legacy-Feld bleibt vorhanden (Median-Tages-Heuristik, Tag 20):
        assert bau["next_due"] == "2026-09-20"
        assert bau["amount"] == pytest.approx(256.67, abs=0.01)

    def test_estimated_helper_single_observation_returns_empty(self) -> None:
        """Helper-Ebene: < 2 Ausgaben => leerer Dict (None-safe)."""
        facts = [
            {
                "counterparty": "Solo",
                "currency": "CHF",
                "booking_date": "2026-07-01",
                "amount_cents": -5000,
            }
        ]
        info = FinanceTools._estimated_cadence_info(
            facts,
            counterparty="Solo",
            currency="CHF",
            reference=REF_DATE,
            amount=50.0,
        )
        assert info["estimated_cadence"] is None
        assert info["estimated_next_due"] is None
        assert info["estimated_monthly"] is None


class TestAP3EstimatedCadenceAudit:
    def test_audit_estimated_fields_and_source(self, ap3_tools: FinanceTools) -> None:
        result = ap3_tools.subscription_audit({"iban": CH_IBAN, "reference_date": REF})
        assert result["success"] is True
        assert result["count"] == 3
        groups = {g["counterparty"]: g for g in result["groups"]}
        net = groups["Net"]
        assert net["cadence"] is None  # unbestätigt (F02-Bestand)
        assert net["estimated_cadence"] == "monthly"
        assert net["estimated_confidence"] == "medium"
        assert net["estimated_monthly"] == pytest.approx(9.9, abs=0.01)
        assert net["estimated_annual"] == pytest.approx(118.8, abs=0.01)
        assert net["cadence_source"] == "estimated"
        bau = groups["Bau"]
        assert bau["estimated_cadence"] is None
        assert bau["cadence_source"] is None
        stream = groups["Stream"]
        assert stream["estimated_confidence"] == "low"
        assert stream["cadence_source"] == "estimated"

    def test_series_confirmed_takes_priority(
        self, ap3_db: FinanceDB, ap3_tools: FinanceTools
    ) -> None:
        """Bestätigte Serie => cadence_source='series' (bills UND audit)."""
        series = ap3_db.create_series(
            iban=CH_IBAN,
            direction="expense",
            cadence="monthly",
            period_n=1,
            anchor_date="2026-07-15",
            amount_cents=990,
            currency="CHF",
            counterparty="Net",
            status="active",
            source="manual",
        )
        bills = ap3_tools.upcoming_bills(
            {"iban": CH_IBAN, "reference_date": REF, "days_ahead": 30}
        )
        net_bill = next(b for b in bills["bills"] if b["counterparty"] == "Net")
        assert net_bill["cadence_source"] == "series"
        # Serie liefert die exakte Expansion (keine Heuristik-Doppelzählung):
        net_occ = [o for o in bills["window_occurrences"] if o["counterparty"] == "Net"]
        assert [(o["date"], o["source"]) for o in net_occ] == [("2026-09-15", "series")]
        # Übrige Gruppen bleiben korrekt klassifiziert:
        others = {b["counterparty"]: b for b in bills["bills"]}
        assert others["Stream"]["cadence_source"] == "estimated"
        assert others["Bau"]["cadence_source"] is None

        audit = ap3_tools.subscription_audit({"iban": CH_IBAN, "reference_date": REF})
        net_audit = next(g for g in audit["groups"] if g["counterparty"] == "Net")
        assert net_audit["cadence_source"] == "series"
        assert net_audit["cadence"] == "monthly"
        assert net_audit["series_ids"] == [int(series.id)]
        assert net_audit["payment_per_period"] == pytest.approx(9.9, abs=0.01)
        assert net_audit["monthly_equivalent"] == pytest.approx(9.9, abs=0.01)
        assert net_audit["annual_equivalent"] == pytest.approx(118.8, abs=0.01)
        assert net_audit["next_due_date"] == "2026-09-15"
        assert audit["confirmed_count"] == 1


# ---------------------------------------------------------------------------
# 2) Prognose-Unterdrückung (reversibel, forecast-only)
# ---------------------------------------------------------------------------


class TestAP3ForecastSuppression:
    def test_suppress_hides_from_upcoming_bills(
        self, ap3_db: FinanceDB, ap3_tools: FinanceTools
    ) -> None:
        res = ap3_tools.suppress_forecast(
            {"iban": CH_IBAN, "counterparty": "Net", "currency": "CHF", "reason": "Test"}
        )
        assert res["success"] is True
        assert res["suppression"]["active"] is True
        assert res["suppression"]["counterparty"] == "Net"

        bills = ap3_tools.upcoming_bills(
            {"iban": CH_IBAN, "reference_date": REF, "days_ahead": 30}
        )
        assert [b["counterparty"] for b in bills["bills"]] == ["Stream", "Bau"]
        assert all(o["counterparty"] != "Net" for o in bills["window_occurrences"])
        sup = next(s for s in bills["suppressed"] if s["counterparty"] == "Net")
        assert sup["active"] is True
        assert sup["reason"] == "Test"

    def test_suppress_hides_from_subscription_audit(self, ap3_tools: FinanceTools) -> None:
        assert ap3_tools.suppress_forecast({"iban": CH_IBAN, "counterparty": "Net"})[
            "success"
        ] is True
        audit = ap3_tools.subscription_audit({"iban": CH_IBAN, "reference_date": REF})
        assert [g["counterparty"] for g in audit["groups"]] == ["Bau", "Stream"]
        assert audit["count"] == 2
        assert any(s["counterparty"] == "Net" for s in audit["suppressed"])

    def test_bookings_untouched(self, ap3_db: FinanceDB, ap3_tools: FinanceTools) -> None:
        """Unterdrückung + Wiederherstellung ändern KEINE Buchungen."""
        before = _count_rows(ap3_db, "transactions")
        assert ap3_tools.suppress_forecast({"iban": CH_IBAN, "counterparty": "Net"})[
            "success"
        ] is True
        assert ap3_tools.restore_forecast({"iban": CH_IBAN, "counterparty": "Net"})[
            "success"
        ] is True
        assert _count_rows(ap3_db, "transactions") == before

    def test_suppress_idempotent_updates_reason(
        self, ap3_db: FinanceDB, ap3_tools: FinanceTools
    ) -> None:
        assert ap3_tools.suppress_forecast(
            {"iban": CH_IBAN, "counterparty": "Net", "reason": "r1"}
        )["success"] is True
        res = ap3_tools.suppress_forecast(
            {"iban": CH_IBAN, "counterparty": "Net", "reason": "r2"}
        )
        assert res["success"] is True
        rows = ap3_tools.list_forecast_suppressions({"iban": CH_IBAN})
        assert rows["count"] == 1
        assert rows["suppressions"][0]["reason"] == "r2"
        assert rows["suppressions"][0]["id"] == res["suppression"]["id"]

    def test_restore_reactivates_and_keeps_history(
        self, ap3_db: FinanceDB, ap3_tools: FinanceTools
    ) -> None:
        assert ap3_tools.suppress_forecast({"iban": CH_IBAN, "counterparty": "Net"})[
            "success"
        ] is True
        res = ap3_tools.restore_forecast({"iban": CH_IBAN, "counterparty": "Net"})
        assert res["success"] is True
        assert res["suppression"]["active"] is False

        # Gruppe erscheint wieder in der Prognose:
        bills = ap3_tools.upcoming_bills(
            {"iban": CH_IBAN, "reference_date": REF, "days_ahead": 30}
        )
        assert any(b["counterparty"] == "Net" for b in bills["bills"])
        assert not any(s["counterparty"] == "Net" for s in bills["suppressed"])

        # Historie bleibt (Zeile nicht gelöscht, active=0):
        active = ap3_tools.list_forecast_suppressions({"iban": CH_IBAN})
        assert active["count"] == 0
        all_rows = ap3_tools.list_forecast_suppressions(
            {"iban": CH_IBAN, "include_inactive": True}
        )
        assert all_rows["count"] == 1
        assert all_rows["suppressions"][0]["active"] is False

    def test_restore_without_suppression_not_found(self, ap3_tools: FinanceTools) -> None:
        res = ap3_tools.restore_forecast({"iban": CH_IBAN, "counterparty": "Net"})
        assert res["success"] is False
        assert res["error_class"] == "not_found"

    @pytest.mark.parametrize(
        "params",
        [
            {"counterparty": "Net"},  # iban fehlt
            {"iban": CH_IBAN},  # counterparty fehlt
            {"iban": CH_IBAN, "counterparty": "  "},  # leer
        ],
        ids=["missing-iban", "missing-counterparty", "blank-counterparty"],
    )
    def test_suppress_invalid_params(self, params: Dict[str, Any], ap3_tools: FinanceTools) -> None:
        res = ap3_tools.suppress_forecast(params)
        assert res["success"] is False
        assert res["error_class"] == "invalid_param"

    def test_scope_isolation_between_accounts(self, scope_tools: FinanceTools) -> None:
        """Unterdrückung auf CH-Konto trifft das DE-Konto nicht."""
        assert scope_tools.suppress_forecast(
            {"iban": CH_IBAN, "counterparty": "Net"}
        )["success"] is True
        ch = scope_tools.upcoming_bills(
            {"iban": CH_IBAN, "reference_date": REF, "days_ahead": 30}
        )
        assert all(b["counterparty"] != "Net" for b in ch["bills"])
        de = scope_tools.upcoming_bills(
            {"iban": DE_IBAN, "reference_date": REF, "days_ahead": 30}
        )
        assert any(b["counterparty"] == "Net" for b in de["bills"])
        # IBAN-Filter der Auflistung:
        rows = scope_tools.list_forecast_suppressions({"iban": CH_IBAN})
        assert rows["count"] == 1
        assert rows["suppressions"][0]["iban"] == CH_IBAN

    def test_suppress_does_not_touch_confirmed_series(
        self, ap3_db: FinanceDB, ap3_tools: FinanceTools
    ) -> None:
        """Forecast-only: bestätigte Serie bleibt aktiv; nur Projektion fällt weg."""
        series = ap3_db.create_series(
            iban=CH_IBAN,
            direction="expense",
            cadence="monthly",
            period_n=1,
            anchor_date="2026-07-15",
            amount_cents=990,
            currency="CHF",
            counterparty="Net",
            status="active",
            source="manual",
        )
        before = ap3_tools.upcoming_bills(
            {"iban": CH_IBAN, "reference_date": REF, "days_ahead": 30}
        )
        assert any(
            o["counterparty"] == "Net" and o["source"] == "series"
            for o in before["window_occurrences"]
        )

        assert ap3_tools.suppress_forecast({"iban": CH_IBAN, "counterparty": "Net"})[
            "success"
        ] is True
        after = ap3_tools.upcoming_bills(
            {"iban": CH_IBAN, "reference_date": REF, "days_ahead": 30}
        )
        assert all(b["counterparty"] != "Net" for b in after["bills"])
        assert all(o["counterparty"] != "Net" for o in after["window_occurrences"])

        # Serie selbst bleibt aktiv (KEINE Mutation):
        listed = ap3_tools.list_series({"iban": CH_IBAN})
        net_series = [s for s in listed["series"] if s["counterparty"] == "Net"]
        assert len(net_series) == 1
        assert net_series[0]["status"] == "active"
        assert net_series[0]["id"] == int(series.id)

        # Wiederherstellung: Projektion kehrt zurück.
        assert ap3_tools.restore_forecast({"iban": CH_IBAN, "counterparty": "Net"})[
            "success"
        ] is True
        restored = ap3_tools.upcoming_bills(
            {"iban": CH_IBAN, "reference_date": REF, "days_ahead": 30}
        )
        assert any(
            o["counterparty"] == "Net" and o["source"] == "series"
            for o in restored["window_occurrences"]
        )


# ---------------------------------------------------------------------------
# 3) Manuelle Serien (create / update)
# ---------------------------------------------------------------------------


def _create_kfz(params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    base: Dict[str, Any] = {
        "iban": CH_IBAN,
        "amount": 120.0,
        "cadence": "yearly",
        "anchor_date": "2026-10-01",
        "counterparty": "Kfz-Steuer",
        "title": "Kfz-Steuer 2026",
        "currency": "CHF",
    }
    if params is not None:
        base.update(params)
    return base


class TestAP3CreateManualSeries:
    def test_happy_path(self, clean_tools: FinanceTools) -> None:
        res = clean_tools.create_manual_series(_create_kfz())
        assert res["success"] is True
        s = res["series"]
        assert s["id"] > 0
        assert s["iban"] == CH_IBAN
        assert s["currency"] == "CHF"
        assert s["direction"] == "expense"
        assert s["cadence"] == "yearly"
        assert s["period_n"] == 1
        assert s["anchor_day"] == 1
        assert s["anchor_date"] == "2026-10-01"
        assert s["amount"] == pytest.approx(120.0, abs=0.01)
        assert s["counterparty"] == "Kfz-Steuer"
        assert s["title"] == "Kfz-Steuer 2026"
        assert s["status"] == "active"
        assert s["source"] == "manual"
        assert s["revision"] >= 1

    def test_no_bookings_created(self, clean_db: FinanceDB, clean_tools: FinanceTools) -> None:
        """Eine Serie ist eine Prognose-Annahme — KEINE Buchungen."""
        assert clean_tools.create_manual_series(_create_kfz())["success"] is True
        assert _count_rows(clean_db, "transactions") == 1  # nur die Seed-Buchung

    def test_projected_in_upcoming_bills_window(self, clean_tools: FinanceTools) -> None:
        assert clean_tools.create_manual_series(_create_kfz())["success"] is True
        # 60-Tage-Fenster (bis 2026-10-27) enthält 2026-10-01:
        wide = clean_tools.upcoming_bills(
            {"iban": CH_IBAN, "reference_date": REF, "days_ahead": 60}
        )
        occ = [o for o in wide["window_occurrences"] if o["counterparty"] == "Kfz-Steuer"]
        assert len(occ) == 1
        assert occ[0]["date"] == "2026-10-01"
        assert occ[0]["amount"] == pytest.approx(120.0, abs=0.01)
        assert occ[0]["source"] == "series"
        # 30-Tage-Fenster (bis 2026-09-27) nicht:
        narrow = clean_tools.upcoming_bills(
            {"iban": CH_IBAN, "reference_date": REF, "days_ahead": 30}
        )
        assert all(
            o["counterparty"] != "Kfz-Steuer" for o in narrow["window_occurrences"]
        )

    def test_no_unintended_suppression_side_effects(
        self, clean_db: FinanceDB, clean_tools: FinanceTools
    ) -> None:
        assert clean_tools.create_manual_series(_create_kfz())["success"] is True
        assert _count_rows(clean_db, "forecast_suppressions") == 0
        rows = clean_tools.list_forecast_suppressions({"include_inactive": True})
        assert rows["count"] == 0

    @pytest.mark.parametrize(
        "params",
        [
            {"iban": CH_IBAN, "cadence": "yearly", "anchor_date": "2026-10-01"},  # amount fehlt
            {"iban": CH_IBAN, "amount": 0, "cadence": "yearly", "anchor_date": "2026-10-01"},
            {"iban": CH_IBAN, "amount": -5.0, "cadence": "yearly", "anchor_date": "2026-10-01"},
            {"iban": CH_IBAN, "amount": 120.0, "anchor_date": "2026-10-01"},  # cadence fehlt
            {"iban": CH_IBAN, "amount": 120.0, "cadence": "daily", "anchor_date": "2026-10-01"},
            {"iban": CH_IBAN, "amount": 120.0, "cadence": "yearly"},  # anchor fehlt
            {"iban": CH_IBAN, "amount": 120.0, "cadence": "yearly", "anchor_date": "2026-13-40"},
            {"amount": 120.0, "cadence": "yearly", "anchor_date": "2026-10-01"},  # iban fehlt
        ],
        ids=[
            "missing-amount",
            "zero-amount",
            "negative-amount",
            "missing-cadence",
            "invalid-cadence",
            "missing-anchor",
            "invalid-anchor",
            "missing-iban",
        ],
    )
    def test_invalid_params(self, params: Dict[str, Any], clean_tools: FinanceTools) -> None:
        res = clean_tools.create_manual_series(params)
        assert res["success"] is False
        assert res["error_class"] == "invalid_param"


class TestAP3UpdateManualSeries:
    @pytest.fixture
    def series_id(self, clean_tools: FinanceTools) -> int:
        res = clean_tools.create_manual_series(_create_kfz())
        assert res["success"] is True
        return int(res["series"]["id"])

    def test_update_amount_increments_revision(
        self, clean_tools: FinanceTools, series_id: int
    ) -> None:
        before = clean_tools.list_series({"iban": CH_IBAN})["series"][0]
        res = clean_tools.update_manual_series({"series_id": series_id, "amount": 150.0})
        assert res["success"] is True
        assert res["series"]["amount"] == pytest.approx(150.0, abs=0.01)
        assert res["series"]["revision"] == before["revision"] + 1
        # Ungeänderte Felder bleiben erhalten:
        assert res["series"]["cadence"] == "yearly"
        assert res["series"]["counterparty"] == "Kfz-Steuer"

    def test_update_cadence_and_period(self, clean_tools: FinanceTools, series_id: int) -> None:
        res = clean_tools.update_manual_series(
            {"series_id": series_id, "cadence": "n_months", "period_n": 2}
        )
        assert res["success"] is True
        assert res["series"]["cadence"] == "n_months"
        assert res["series"]["period_n"] == 2

    def test_update_title(self, clean_tools: FinanceTools, series_id: int) -> None:
        res = clean_tools.update_manual_series(
            {"series_id": series_id, "title": "Kfz-Steuer (neu)"}
        )
        assert res["success"] is True
        assert res["series"]["title"] == "Kfz-Steuer (neu)"

    def test_stale_revision_conflict(self, clean_tools: FinanceTools, series_id: int) -> None:
        created = clean_tools.list_series({"iban": CH_IBAN})["series"][0]
        rev0 = created["revision"]
        # Paralleländerung simulieren:
        assert clean_tools.update_manual_series(
            {"series_id": series_id, "amount": 130.0}
        )["success"] is True
        # Stale-Revision => Konflikt:
        stale = clean_tools.update_manual_series(
            {"series_id": series_id, "amount": 140.0, "expected_revision": rev0}
        )
        assert stale["success"] is False
        assert stale["error_class"] == "conflict"
        # Aktuelle Revision => Erfolg:
        ok = clean_tools.update_manual_series(
            {"series_id": series_id, "amount": 140.0, "expected_revision": rev0 + 1}
        )
        assert ok["success"] is True
        assert ok["series"]["amount"] == pytest.approx(140.0, abs=0.01)

    @pytest.mark.parametrize(
        "params",
        [
            {"series_id": 1},  # keine Felder
            {"series_id": 1, "amount": 0},
            {"series_id": 1, "amount": "abc"},
            {"amount": 120.0},  # series_id fehlt
            {"series_id": -3, "amount": 120.0},
        ],
        ids=["no-fields", "zero-amount", "bad-amount", "missing-series-id", "negative-series-id"],
    )
    def test_invalid_params(self, params: Dict[str, Any], clean_tools: FinanceTools) -> None:
        res = clean_tools.update_manual_series(params)
        assert res["success"] is False
        assert res["error_class"] == "invalid_param"

    def test_unknown_series_not_found(self, clean_tools: FinanceTools) -> None:
        res = clean_tools.update_manual_series(
            {"series_id": 999999, "amount": 120.0}
        )
        assert res["success"] is False
        assert res["error_class"] == "not_found"