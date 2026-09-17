"""AP2 Finance Recurring Series — Tools-API-Tests (FORECAST_UX, 2026-09-17).

Validiert die 12 Serien-Tools in ``finance/tools.py`` (``FinanceTools``)
end-to-end gegen eine echte SQLite-DB:

* Read-Tools:  ``list_series`` / ``list_series_candidates`` / ``series_calendar``
* Detection:   ``detect_series_candidates`` (list_analysis_facts -> Engine -> DAO)
* Lifecycle:   ``confirm_candidate`` / ``reject_candidate``
* Status:      ``pause_series`` / ``resume_series`` / ``end_series``
* Ausnahmen:   ``skip_occurrence`` / ``move_occurrence`` / ``change_occurrence_amount``

Konvention: ``{"success": bool, ...}``; DAO-ValueError -> ``error_class``
(``invalid_param`` / ``not_found`` / ``conflict``) — Fail-Fast, kein stiller
Fallback. Kandidaten bleiben 'pending' (nie auto-confirmed).

DAO-Basis-Tests: :mod:`tests.test_finance_series_dao` (AP2 S1, 58/58).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from finance.db_schema import FinanceDB
from finance.tools import FinanceTools

EUR_IBAN = "DE89370400440532013000"
COUNTERPARTY = "Streaming Music GmbH"


# ---------------------------------------------------------------------------
# Fixtures / Helper
# ---------------------------------------------------------------------------


@pytest.fixture()
def db(tmp_path: Path) -> FinanceDB:
    """Frische Serien-DB mit einem EUR-Konto (keine Produktiv-Instanz)."""
    d = FinanceDB(tmp_path / "series_tools.db")
    bank_id = d.upsert_bank("Test Bank", bic="TESTDEFF", country_code="DE")
    d.upsert_account(bank_id, EUR_IBAN, account_holder="Test", currency="EUR")
    return d


@pytest.fixture()
def tools(db: FinanceDB) -> FinanceTools:
    return FinanceTools(db)


def make_series(db: FinanceDB, **overrides):
    """Aktive monatliche Test-Serie (15. des Monats, 9,90 EUR expense)."""
    kwargs = dict(
        iban=EUR_IBAN,
        currency="EUR",
        direction="expense",
        cadence="monthly",
        anchor_date="2026-07-15",
        amount_cents=990,
        counterparty=COUNTERPARTY,
        title="Streaming-Abo",
    )
    kwargs.update(overrides)
    return db.create_series(**kwargs)


def account_id_for(db: FinanceDB, iban: str = EUR_IBAN) -> int:
    with db._connect() as conn:
        row = conn.execute("SELECT id FROM accounts WHERE iban = ?", (iban,)).fetchone()
    assert row is not None
    return int(row["id"])


def make_txn(
    db: FinanceDB,
    account_id: int,
    txn_date: str,
    amount_cents: int,
    counterparty: str = COUNTERPARTY,
    seq: int = 0,
) -> int:
    """Buchung mit Counterparty (für list_analysis_facts / Detection)."""
    with db._connect() as conn:
        cur = conn.execute(
            "INSERT INTO statements (account_id, source_pdf_hash) VALUES (?, ?)",
            (account_id, f"test-stmt-{seq}-{txn_date}"),
        )
        stmt_id = int(cur.lastrowid or 0)
        cur = conn.execute(
            "INSERT INTO transactions "
            "(statement_id, account_id, booking_date, amount_cents, currency, "
            " counterparty, dedup_hash) VALUES (?, ?, ?, ?, 'EUR', ?, ?)",
            (
                stmt_id,
                account_id,
                txn_date,
                amount_cents,
                counterparty,
                f"test-dedup-{seq}-{txn_date}-{amount_cents}",
            ),
        )
        return int(cur.lastrowid or 0)


def run_detect(db: FinanceDB, tools: FinanceTools):
    """Drei monatliche Ausgaben am 15. + Detection (ein 'monthly'-Kandidat)."""
    acct = account_id_for(db)
    make_txn(db, acct, "2026-05-15", -990, seq=1)
    make_txn(db, acct, "2026-06-15", -990, seq=2)
    make_txn(db, acct, "2026-07-15", -990, seq=3)
    result = tools.detect_series_candidates({})
    assert result["success"] is True
    assert result["count"] >= 1, result
    return result


# ---------------------------------------------------------------------------
# list_series
# ---------------------------------------------------------------------------


class TestSeriesList:
    def test_list_empty(self, tools: FinanceTools):
        result = tools.list_series({})
        assert result["success"] is True
        assert result["count"] == 0
        assert result["series"] == []

    def test_list_returns_created_series(self, db: FinanceDB, tools: FinanceTools):
        series = make_series(db)
        result = tools.list_series({})
        assert result["success"] is True
        assert result["count"] == 1
        item = result["series"][0]
        assert item["id"] == series.id
        assert item["iban"] == EUR_IBAN
        assert item["currency"] == "EUR"
        assert item["direction"] == "expense"
        assert item["cadence"] == "monthly"
        assert item["period_n"] == 1
        assert item["anchor_day"] == 15
        assert item["amount"] == 9.9
        assert item["counterparty"] == COUNTERPARTY
        assert item["title"] == "Streaming-Abo"
        assert item["status"] == "active"
        assert item["source"] == "manual"

    def test_list_filter_status(self, db: FinanceDB, tools: FinanceTools):
        make_series(db, title="Serie A")
        s2 = make_series(db, title="Serie B")
        tools.pause_series({"series_id": s2.id})

        active = tools.list_series({"status": "active"})
        assert active["success"] is True
        assert active["count"] == 1
        assert active["series"][0]["title"] == "Serie A"

        paused = tools.list_series({"status": "paused"})
        assert paused["count"] == 1
        assert paused["series"][0]["id"] == s2.id

    def test_list_invalid_status(self, tools: FinanceTools):
        result = tools.list_series({"status": "closed"})
        assert result["success"] is False
        assert result["error_class"] == "invalid_param"

    def test_list_invalid_direction(self, tools: FinanceTools):
        result = tools.list_series({"direction": "neutral"})
        assert result["success"] is False
        assert result["error_class"] == "invalid_param"

    def test_list_unknown_iban_is_empty(self, db: FinanceDB, tools: FinanceTools):
        make_series(db)
        result = tools.list_series({"iban": "DE00000000000000000000"})
        assert result["success"] is True
        assert result["count"] == 0


# ---------------------------------------------------------------------------
# series_calendar
# ---------------------------------------------------------------------------


class TestSeriesCalendar:
    def test_calendar_empty(self, tools: FinanceTools):
        result = tools.series_calendar({"reference_date": "2026-08-01", "days_ahead": 30})
        assert result["success"] is True
        assert result["count"] == 0
        assert result["occurrences"] == []
        assert result["window_start"] == "2026-08-01"
        assert result["window_end"] == "2026-08-31"

    def test_calendar_active_occurrence(self, db: FinanceDB, tools: FinanceTools):
        s = make_series(db)
        result = tools.series_calendar({"reference_date": "2026-08-01", "days_ahead": 30})
        assert result["success"] is True
        assert result["count"] == 1
        occ = result["occurrences"][0]
        assert occ["date"] == "2026-08-15"
        assert occ["series_id"] == s.id
        assert occ["amount"] == 9.9
        assert occ["direction"] == "expense"
        assert occ["currency"] == "EUR"
        assert occ["counterparty"] == COUNTERPARTY
        assert occ["exception"] is None

    def test_calendar_paused_excluded_by_default(self, db: FinanceDB, tools: FinanceTools):
        s = make_series(db)
        tools.pause_series({"series_id": s.id})

        default = tools.series_calendar({"reference_date": "2026-08-01", "days_ahead": 30})
        assert default["success"] is True
        assert default["count"] == 0

        included = tools.series_calendar(
            {"reference_date": "2026-08-01", "days_ahead": 30, "include_paused": True}
        )
        assert included["count"] == 1
        assert included["occurrences"][0]["date"] == "2026-08-15"

    def test_calendar_ended_excluded(self, db: FinanceDB, tools: FinanceTools):
        s = make_series(db)
        tools.end_series({"series_id": s.id})
        result = tools.series_calendar(
            {"reference_date": "2026-08-01", "days_ahead": 30, "include_paused": True}
        )
        assert result["count"] == 0

    def test_calendar_invalid_days_ahead(self, tools: FinanceTools):
        for bad in (0, 181, "abc"):
            result = tools.series_calendar(
                {"reference_date": "2026-08-01", "days_ahead": bad}
            )
            assert result["success"] is False, bad
            assert result["error_class"] == "invalid_param"

    def test_calendar_invalid_reference_date(self, tools: FinanceTools):
        result = tools.series_calendar({"reference_date": "17.09.2026", "days_ahead": 30})
        assert result["success"] is False
        assert result["error_class"] == "invalid_param"


# ---------------------------------------------------------------------------
# detect_series_candidates
# ---------------------------------------------------------------------------


class TestDetectCandidatesTool:
    def test_detect_finds_monthly_pattern(self, db: FinanceDB, tools: FinanceTools):
        result = run_detect(db, tools)
        cand = result["candidates"][0]
        assert cand["status"] == "pending"
        assert cand["iban"] == EUR_IBAN
        assert cand["direction"] == "expense"
        assert cand["counterparty"] == COUNTERPARTY
        assert cand["cadence"] == "monthly"
        assert cand["anchor_day"] == 15
        assert cand["amount"] == 9.9
        assert len(cand["evidence"]) == 3
        assert cand["evidence"][0]["date"] == "2026-05-15"
        assert cand["evidence"][0]["amount"] == 9.9

    def test_detect_persists_pending_candidate(self, db: FinanceDB, tools: FinanceTools):
        result = run_detect(db, tools)
        fp = result["candidates"][0]["fingerprint"]

        listing = tools.list_series_candidates({})
        assert listing["success"] is True
        assert listing["count"] == 1
        assert listing["candidates"][0]["fingerprint"] == fp
        assert listing["candidates"][0]["status"] == "pending"

        # Kein Auto-Confirm: keine Serie entsteht.
        series = tools.list_series({})
        assert series["count"] == 0

    def test_detect_idempotent(self, db: FinanceDB, tools: FinanceTools):
        first = run_detect(db, tools)
        assert first["new_candidates"] == 1

        second = tools.detect_series_candidates({})
        assert second["success"] is True
        assert second["new_candidates"] == 0
        assert second["count"] == 1
        assert second["candidates"][0]["fingerprint"] == first["candidates"][0]["fingerprint"]

    def test_detect_single_txn_no_candidate(self, db: FinanceDB, tools: FinanceTools):
        acct = account_id_for(db)
        make_txn(db, acct, "2026-07-15", -990, seq=1)
        result = tools.detect_series_candidates({})
        assert result["success"] is True
        assert result["count"] == 0
        assert result["candidates"] == []

    def test_detect_income_direction(self, db: FinanceDB, tools: FinanceTools):
        acct = account_id_for(db)
        make_txn(db, acct, "2026-05-15", 25000, counterparty="Arbeitgeber AG", seq=1)
        make_txn(db, acct, "2026-06-15", 25000, counterparty="Arbeitgeber AG", seq=2)
        make_txn(db, acct, "2026-07-15", 25000, counterparty="Arbeitgeber AG", seq=3)
        result = tools.detect_series_candidates({})
        assert result["success"] is True
        assert result["count"] >= 1
        cand = result["candidates"][0]
        assert cand["counterparty"] == "Arbeitgeber AG"
        assert cand["direction"] == "income"
        assert cand["amount"] == 250.0

    def test_detect_invalid_lookback(self, tools: FinanceTools):
        result = tools.detect_series_candidates({"lookback_months": 2})
        assert result["success"] is False
        assert result["error_class"] == "invalid_param"

    def test_detect_invalid_min_occurrences(self, tools: FinanceTools):
        result = tools.detect_series_candidates({"min_occurrences": 1})
        assert result["success"] is False
        assert result["error_class"] == "invalid_param"

    def test_detect_unknown_iban(self, tools: FinanceTools):
        result = tools.detect_series_candidates({"iban": "DE00000000000000000000"})
        assert result["success"] is False
        assert result["error_class"] == "not_found"


# ---------------------------------------------------------------------------
# confirm_candidate / reject_candidate
# ---------------------------------------------------------------------------


class TestCandidateLifecycle:
    def test_confirm_creates_detected_series(self, db: FinanceDB, tools: FinanceTools):
        fp = run_detect(db, tools)["candidates"][0]["fingerprint"]

        result = tools.confirm_candidate({"fingerprint": fp})
        assert result["success"] is True
        series = result["series"]
        assert series["status"] == "active"
        assert series["source"] == "detected"
        assert series["iban"] == EUR_IBAN
        assert series["cadence"] == "monthly"
        assert series["amount"] == 9.9
        assert result["candidate"]["status"] == "confirmed"
        assert result["candidate"]["series_id"] == series["id"]

        listing = tools.list_series({"source": "detected"})
        assert listing["count"] == 1
        assert listing["series"][0]["id"] == series["id"]

    def test_confirm_with_corrections(self, db: FinanceDB, tools: FinanceTools):
        fp = run_detect(db, tools)["candidates"][0]["fingerprint"]
        result = tools.confirm_candidate(
            {
                "fingerprint": fp,
                "amount": 12.5,
                "counterparty": "Neuer Partner UG",
                "title": "Korrigiertes Abo",
            }
        )
        assert result["success"] is True
        series = result["series"]
        assert series["amount"] == 12.5
        assert series["counterparty"] == "Neuer Partner UG"
        assert series["title"] == "Korrigiertes Abo"

    def test_confirm_default_status_paused(self, db: FinanceDB, tools: FinanceTools):
        fp = run_detect(db, tools)["candidates"][0]["fingerprint"]
        result = tools.confirm_candidate({"fingerprint": fp, "status": "paused"})
        assert result["success"] is True
        assert result["series"]["status"] == "paused"

    def test_confirm_invalid_status(self, db: FinanceDB, tools: FinanceTools):
        fp = run_detect(db, tools)["candidates"][0]["fingerprint"]
        result = tools.confirm_candidate({"fingerprint": fp, "status": "ended"})
        assert result["success"] is False
        assert result["error_class"] == "invalid_param"

    def test_confirm_missing_fingerprint(self, tools: FinanceTools):
        result = tools.confirm_candidate({})
        assert result["success"] is False
        assert result["error_class"] == "invalid_param"

    def test_confirm_unknown_fingerprint(self, tools: FinanceTools):
        result = tools.confirm_candidate({"fingerprint": "fp-unknown-123"})
        assert result["success"] is False
        assert result["error_class"] == "not_found"

    def test_confirm_twice_conflict(self, db: FinanceDB, tools: FinanceTools):
        fp = run_detect(db, tools)["candidates"][0]["fingerprint"]
        assert tools.confirm_candidate({"fingerprint": fp})["success"] is True
        second = tools.confirm_candidate({"fingerprint": fp})
        assert second["success"] is False
        assert second["error_class"] == "conflict"

    def test_reject_candidate(self, db: FinanceDB, tools: FinanceTools):
        fp = run_detect(db, tools)["candidates"][0]["fingerprint"]
        result = tools.reject_candidate({"fingerprint": fp})
        assert result["success"] is True
        assert result["candidate"]["status"] == "rejected"
        assert result["candidate"]["series_id"] is None
        assert tools.list_series({})["count"] == 0

    def test_reject_twice_conflict(self, db: FinanceDB, tools: FinanceTools):
        fp = run_detect(db, tools)["candidates"][0]["fingerprint"]
        assert tools.reject_candidate({"fingerprint": fp})["success"] is True
        second = tools.reject_candidate({"fingerprint": fp})
        assert second["success"] is False
        assert second["error_class"] == "conflict"

    def test_list_candidates_filters_status(self, db: FinanceDB, tools: FinanceTools):
        fp = run_detect(db, tools)["candidates"][0]["fingerprint"]
        tools.confirm_candidate({"fingerprint": fp})

        pending = tools.list_series_candidates({"status": "pending"})
        assert pending["count"] == 0

        confirmed = tools.list_series_candidates({"status": "confirmed"})
        assert confirmed["count"] == 1
        assert confirmed["candidates"][0]["fingerprint"] == fp

    def test_list_candidates_invalid_status(self, tools: FinanceTools):
        result = tools.list_series_candidates({"status": "open"})
        assert result["success"] is False
        assert result["error_class"] == "invalid_param"


# ---------------------------------------------------------------------------
# pause / resume / end
# ---------------------------------------------------------------------------


class TestSeriesStatusTools:
    def test_pause_series(self, db: FinanceDB, tools: FinanceTools):
        s = make_series(db)
        result = tools.pause_series({"series_id": s.id})
        assert result["success"] is True
        assert result["series"]["status"] == "paused"
        assert tools.list_series({"status": "paused"})["count"] == 1

    def test_resume_series(self, db: FinanceDB, tools: FinanceTools):
        s = make_series(db)
        tools.pause_series({"series_id": s.id})
        result = tools.resume_series({"series_id": s.id})
        assert result["success"] is True
        assert result["series"]["status"] == "active"

    def test_end_series(self, db: FinanceDB, tools: FinanceTools):
        s = make_series(db)
        result = tools.end_series({"series_id": s.id})
        assert result["success"] is True
        assert result["series"]["status"] == "ended"

    def test_pause_invalid_series_id(self, tools: FinanceTools):
        for bad in ("abc", 0, -5):
            result = tools.pause_series({"series_id": bad})
            assert result["success"] is False, bad
            assert result["error_class"] == "invalid_param"

    def test_pause_missing_series_id(self, tools: FinanceTools):
        result = tools.pause_series({})
        assert result["success"] is False
        assert result["error_class"] == "invalid_param"

    def test_pause_unknown_series(self, tools: FinanceTools):
        result = tools.pause_series({"series_id": 999999})
        assert result["success"] is False
        assert result["error_class"] == "not_found"


# ---------------------------------------------------------------------------
# skip / move / change amount (Ausnahmen)
# ---------------------------------------------------------------------------


class TestOccurrenceExceptionTools:
    def test_skip_occurrence(self, db: FinanceDB, tools: FinanceTools):
        s = make_series(db)
        result = tools.skip_occurrence(
            {"series_id": s.id, "due_date": "2026-08-15", "note": "Urlaub"}
        )
        assert result["success"] is True
        exc = result["exception"]
        assert exc["exception_type"] == "skip"
        assert exc["original_due_date"] == "2026-08-15"
        assert exc["note"] == "Urlaub"

        cal = tools.series_calendar({"reference_date": "2026-08-01", "days_ahead": 30})
        assert cal["success"] is True
        assert cal["count"] == 0  # einziges Vorkommen im Fenster übersprungen

    def test_move_occurrence(self, db: FinanceDB, tools: FinanceTools):
        s = make_series(db)
        result = tools.move_occurrence(
            {"series_id": s.id, "due_date": "2026-08-15", "new_due_date": "2026-08-20"}
        )
        assert result["success"] is True
        assert result["exception"]["exception_type"] == "move"
        assert result["exception"]["new_due_date"] == "2026-08-20"

        cal = tools.series_calendar({"reference_date": "2026-08-01", "days_ahead": 30})
        assert cal["count"] == 1
        occ = cal["occurrences"][0]
        assert occ["date"] == "2026-08-20"
        assert occ["original_date"] == "2026-08-15"
        assert occ["exception"] == "move"
        assert occ["amount"] == 9.9

    def test_change_occurrence_amount(self, db: FinanceDB, tools: FinanceTools):
        s = make_series(db)
        result = tools.change_occurrence_amount(
            {"series_id": s.id, "due_date": "2026-08-15", "amount": 12.5}
        )
        assert result["success"] is True
        assert result["exception"]["exception_type"] == "amount"
        assert result["exception"]["amount"] == 12.5

        cal = tools.series_calendar({"reference_date": "2026-08-01", "days_ahead": 30})
        assert cal["count"] == 1
        assert cal["occurrences"][0]["date"] == "2026-08-15"
        assert cal["occurrences"][0]["amount"] == 12.5

    def test_skip_missing_due_date(self, db: FinanceDB, tools: FinanceTools):
        s = make_series(db)
        result = tools.skip_occurrence({"series_id": s.id})
        assert result["success"] is False
        assert result["error_class"] == "invalid_param"

    def test_move_missing_new_due_date(self, db: FinanceDB, tools: FinanceTools):
        s = make_series(db)
        result = tools.move_occurrence({"series_id": s.id, "due_date": "2026-08-15"})
        assert result["success"] is False
        assert result["error_class"] == "invalid_param"

    def test_move_invalid_new_due_date(self, db: FinanceDB, tools: FinanceTools):
        s = make_series(db)
        result = tools.move_occurrence(
            {"series_id": s.id, "due_date": "2026-08-15", "new_due_date": "20.08.2026"}
        )
        assert result["success"] is False
        assert result["error_class"] == "invalid_param"

    def test_change_amount_missing(self, db: FinanceDB, tools: FinanceTools):
        s = make_series(db)
        result = tools.change_occurrence_amount(
            {"series_id": s.id, "due_date": "2026-08-15"}
        )
        assert result["success"] is False
        assert result["error_class"] == "invalid_param"

    def test_change_amount_non_positive(self, db: FinanceDB, tools: FinanceTools):
        s = make_series(db)
        for bad in (0, -5):
            result = tools.change_occurrence_amount(
                {"series_id": s.id, "due_date": "2026-08-15", "amount": bad}
            )
            assert result["success"] is False, bad
            assert result["error_class"] == "invalid_param"

    def test_exception_unknown_series(self, tools: FinanceTools):
        result = tools.skip_occurrence({"series_id": 999999, "due_date": "2026-08-15"})
        assert result["success"] is False
        assert result["error_class"] == "not_found"

    def test_exception_overwrite_same_date(self, db: FinanceDB, tools: FinanceTools):
        """Gleiche Serie + Original-Termin: zweite Ausnahme aktualisiert (T11)."""
        s = make_series(db)
        first = tools.skip_occurrence({"series_id": s.id, "due_date": "2026-08-15"})
        assert first["success"] is True
        second = tools.change_occurrence_amount(
            {"series_id": s.id, "due_date": "2026-08-15", "amount": 7.7}
        )
        assert second["success"] is True
        assert second["exception"]["exception_type"] == "amount"

        rows = db.list_series_exceptions(s.id)
        assert len(rows) == 1
        assert rows[0].exception_type == "amount"
        assert rows[0].amount_cents == 770
