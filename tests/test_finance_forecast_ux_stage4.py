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