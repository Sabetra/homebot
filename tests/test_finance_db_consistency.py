"""Finance-DB-Konsistenztests — Persistenz-Ebene für Konsistenz-/Review-Zustände.

Ergänzt ``tests/test_finance_consistency.py`` (Engine-Ebene) um die
DB-Schicht: die ``statements``-Spalten ``consistency_passed``,
``consistency_errors`` und ``needs_review`` in frischen und migrierten
Datenbanken.

Abgedeckt:
  - frisches Schema enthält alle drei Spalten + Review-Index
  - Legacy-Migration ergänzt fehlende Spalten (idempotent)
  - Round-Trip der Zustandsfelder über ``persist_statement_import``
  - partielle Updates via ``update_statement_consistency``
  - Filter + Sortierung von ``find_review_needed_statements``

Bewusst OHNE Transaktionen: ``persist_statement_import`` mit leerer
Transaktionsliste löst ``_refresh_transaction_search_docs_within_conn``
und damit das Embedding-Modell nicht aus (nur bei neuen Tx-IDs),
damit die DB-Schicht isoliert und deterministisch testbar bleibt.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from typing import Any, Optional

import pytest

from finance.db_schema import FinanceDB, Statement


# ============================================================================
# Hilfsfunktionen
# ============================================================================


def _statement_columns(db: FinanceDB) -> dict[str, Any]:
    """Gibt ``{name: type}`` der ``statements``-Spalten zurück (PRAGMA)."""
    with sqlite3.connect(db.db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("PRAGMA table_info(statements)").fetchall()
    return {row["name"]: row["type"] for row in rows}


def _review_index_exists(db: FinanceDB) -> bool:
    """True, wenn der Review-Index ``idx_stmt_review`` existiert."""
    with sqlite3.connect(db.db_path) as conn:
        row = conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'index' AND name = 'idx_stmt_review'"
        ).fetchone()
    return row is not None


def _insert_statement(
    db: FinanceDB,
    pdf_hash: str,
    *,
    opening_balance: Optional[float] = 1000.0,
    closing_balance: Optional[float] = 1200.0,
    total_credits: Optional[float] = None,
    total_debits: Optional[float] = None,
    consistency_passed: Optional[bool] = None,
    consistency_errors: Optional[str] = None,
    needs_review: bool = False,
) -> int:
    """Persistiert ein Statement OHNE Transaktionen; liefert die id.

    Leere Transaktionsliste => kein Such-Docs-Refresh, kein Embedding-Load.
    """
    _, _, statement_id, inserted, duplicates = db.persist_statement_import(
        bank_name="Consistency Test Bank",
        bank_bic="TESTDEFF",
        bank_country_code="DE",
        iban="DE89370400440532013000",
        account_holder="Test User",
        currency="EUR",
        account_type="checking",
        source_pdf_hash=pdf_hash,
        source_filename=f"{pdf_hash}.pdf",
        period_start="2026-03-01",
        period_end="2026-03-31",
        opening_balance=opening_balance,
        closing_balance=closing_balance,
        total_credits=total_credits,
        total_debits=total_debits,
        consistency_passed=consistency_passed,
        consistency_errors=consistency_errors,
        needs_review=needs_review,
        transactions=[],
    )
    assert (inserted, duplicates) == (0, 0)
    return statement_id


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture()
def fresh_db(tmp_path: pytest.TempPath) -> Iterator[FinanceDB]:
    """Isolierte, frisch angelegte Finance-DB."""
    return FinanceDB(str(tmp_path / "finance_fresh.db"))


def _create_legacy_db_file(db_path: str) -> None:
    """Erzeugt eine Legacy-DB mit dem historischen ``statements``-Schema.

    Stand vor der Konsistenz-Migration: ohne ``total_credits_cents``,
    ``total_debits_cents``, ``consistency_passed``, ``consistency_errors``
    und ``needs_review``; enthält zwei bereits importierte Zeilen.
    """
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            CREATE TABLE statements (
                id                    INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id            INTEGER NOT NULL,
                period_start          TEXT,
                period_end            TEXT,
                opening_balance_cents INTEGER,
                closing_balance_cents INTEGER,
                source_pdf_hash       TEXT NOT NULL UNIQUE,
                source_filename       TEXT,
                imported_at           TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )
        conn.execute(
            """
            INSERT INTO statements (
                account_id, period_start, period_end,
                opening_balance_cents, closing_balance_cents,
                source_pdf_hash, source_filename
            ) VALUES (
                1, '2026-01-01', '2026-01-31', 100000, 120000,
                'legacy_statement_1', 'legacy_1.pdf'
            )
            """
        )
        conn.execute(
            """
            INSERT INTO statements (
                account_id, period_start, period_end,
                opening_balance_cents, closing_balance_cents,
                source_pdf_hash, source_filename
            ) VALUES (
                1, '2026-02-01', '2026-02-28', 120000, 150000,
                'legacy_statement_2', 'legacy_2.pdf'
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


@pytest.fixture()
def legacy_db(tmp_path: pytest.TempPath) -> Iterator[FinanceDB]:
    """Legacy-DB, deren Init die Konsistenz-Migration ausgelöst hat."""
    db_path = str(tmp_path / "finance_legacy.db")
    _create_legacy_db_file(db_path)
    return FinanceDB(db_path)  # __init__ -> _init_schema -> Migration


# ============================================================================
# Frisches Schema
# ============================================================================


class TestFreshSchema:
    """Frisch angelegte DBs: Spalten, Typen und Index vorhanden."""

    def test_statements_table_has_consistency_columns(self, fresh_db: FinanceDB) -> None:
        cols = _statement_columns(fresh_db)
        for expected in (
            "consistency_passed",
            "consistency_errors",
            "needs_review",
            "total_credits_cents",
            "total_debits_cents",
        ):
            assert expected in cols

    def test_consistency_column_types(self, fresh_db: FinanceDB) -> None:
        cols = _statement_columns(fresh_db)
        assert cols["consistency_passed"] == "INTEGER"
        assert cols["consistency_errors"] == "TEXT"
        assert cols["needs_review"] == "INTEGER"

    def test_review_index_exists(self, fresh_db: FinanceDB) -> None:
        assert _review_index_exists(fresh_db) is True

    def test_ddl_default_needs_review_zero(self, fresh_db: FinanceDB) -> None:
        """DDL-Default: Zeile ohne ``needs_review`` -> 0, ``consistency_passed`` NULL."""
        with sqlite3.connect(fresh_db.db_path) as conn:
            conn.execute(
                "INSERT INTO statements (account_id, source_pdf_hash) "
                "VALUES (9999, 'ddl-default-row')"
            )
            conn.commit()
        with sqlite3.connect(fresh_db.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT needs_review, consistency_passed "
                "FROM statements WHERE source_pdf_hash = 'ddl-default-row'"
            ).fetchone()
        assert row is not None
        assert row["needs_review"] == 0
        assert row["consistency_passed"] is None


# ============================================================================
# Legacy-Migration
# ============================================================================


class TestLegacyMigration:
    """Bestehende DBs: fehlende Spalten werden idempotent ergänzt."""

    def test_missing_columns_are_added(self, legacy_db: FinanceDB) -> None:
        cols = _statement_columns(legacy_db)
        for expected in (
            "consistency_passed",
            "consistency_errors",
            "needs_review",
            "total_credits_cents",
            "total_debits_cents",
        ):
            assert expected in cols

    def test_no_duplicate_columns(self, legacy_db: FinanceDB) -> None:
        names = list(_statement_columns(legacy_db))
        assert len(names) == len(set(names))

    def test_review_index_created(self, legacy_db: FinanceDB) -> None:
        assert _review_index_exists(legacy_db) is True

    def test_legacy_rows_get_neutral_defaults(self, legacy_db: FinanceDB) -> None:
        st = legacy_db.find_statement_by_pdf_hash("legacy_statement_1")
        assert st is not None
        # Neutralzustand: ungeprueft, keine Review, keine Fehler.
        assert st.consistency_passed is None
        assert st.consistency_errors is None
        assert st.needs_review is False
        # Vorhandene Werte bleiben erhalten.
        assert st.opening_balance_cents == 100000
        assert st.closing_balance_cents == 120000
        assert st.period_start == "2026-01-01"

    def test_migration_is_idempotent_across_reopens(self, legacy_db: FinanceDB) -> None:
        again = FinanceDB(legacy_db.db_path)
        names = list(_statement_columns(again))
        assert "consistency_passed" in names
        assert len(names) == len(set(names))
        st = again.find_statement_by_pdf_hash("legacy_statement_2")
        assert st is not None
        assert st.needs_review is False
        assert st.consistency_passed is None


# ============================================================================
# Round-Trip-Persistenz
# ============================================================================


class TestConsistencyPersistence:
    """``persist_statement_import`` -> ``get_statement``/``find_*`` Round-Trip."""

    def test_failed_state_round_trips(self, fresh_db: FinanceDB) -> None:
        sid = _insert_statement(
            fresh_db,
            "failed-import",
            consistency_passed=False,
            consistency_errors="balance_chain: expected 1200.00, got 1300.00",
            needs_review=True,
        )
        st = fresh_db.get_statement(sid)
        assert st is not None
        assert st.consistency_passed is False
        assert st.consistency_errors == "balance_chain: expected 1200.00, got 1300.00"
        assert st.needs_review is True

    def test_passed_state_round_trips(self, fresh_db: FinanceDB) -> None:
        sid = _insert_statement(
            fresh_db,
            "passed-import",
            consistency_passed=True,
            consistency_errors="",
            needs_review=False,
        )
        st = fresh_db.get_statement(sid)
        assert st is not None
        assert st.consistency_passed is True
        assert st.consistency_errors == ""
        assert st.needs_review is False

    def test_unchecked_state_round_trips_as_none(self, fresh_db: FinanceDB) -> None:
        """Standardpfad: ``consistency_passed`` bleibt NULL, Review aus."""
        sid = _insert_statement(fresh_db, "unchecked-import")
        st = fresh_db.get_statement(sid)
        assert st is not None
        assert st.consistency_passed is None
        assert st.consistency_errors is None
        assert st.needs_review is False

    def test_cent_values_round_trip(self, fresh_db: FinanceDB) -> None:
        sid = _insert_statement(
            fresh_db,
            "cent-roundtrip",
            opening_balance=1000.55,
            closing_balance=1200.99,
            total_credits=500.25,
            total_debits=300.10,
            consistency_passed=False,
            consistency_errors="sum_of_transactions: mismatch",
            needs_review=True,
        )
        st = fresh_db.get_statement(sid)
        assert st is not None
        assert st.opening_balance_cents == 100055
        assert st.closing_balance_cents == 120099
        assert st.total_credits_cents == 50025
        assert st.total_debits_cents == 30010
        assert st.consistency_passed is False
        assert st.consistency_errors == "sum_of_transactions: mismatch"
        assert st.needs_review is True

    def test_find_statement_by_pdf_hash_exposes_state(self, fresh_db: FinanceDB) -> None:
        """Idempotenzpfad: PDF-Hash-Lookup liefert den Review-Zustand mit."""
        sid = _insert_statement(
            fresh_db,
            "hash-lookup",
            consistency_passed=False,
            consistency_errors="prior_balance: mismatch",
            needs_review=True,
        )
        st = fresh_db.find_statement_by_pdf_hash("hash-lookup")
        assert st is not None
        assert st.id == sid
        assert st.consistency_passed is False
        assert st.consistency_errors == "prior_balance: mismatch"
        assert st.needs_review is True


# ============================================================================
# update_statement_consistency
# ============================================================================


class TestUpdateStatementConsistency:
    """Partielle Updates: nur explizit uebergebene Felder werden geschrieben."""

    def test_full_update_from_unchecked(self, fresh_db: FinanceDB) -> None:
        sid = _insert_statement(fresh_db, "update-target")
        assert fresh_db.get_statement(sid).consistency_passed is None

        updated = fresh_db.update_statement_consistency(
            sid,
            consistency_passed=False,
            consistency_errors="sum_of_transactions: totals mismatch",
            needs_review=True,
        )
        assert updated is True
        st = fresh_db.get_statement(sid)
        assert st is not None
        assert st.consistency_passed is False
        assert st.consistency_errors == "sum_of_transactions: totals mismatch"
        assert st.needs_review is True

    def test_clearing_review_on_repair(self, fresh_db: FinanceDB) -> None:
        """Reparaturpfad: fehlgeschlagener Zustand wird durchgeprueft ersetzt."""
        sid = _insert_statement(
            fresh_db,
            "repair-target",
            consistency_passed=False,
            consistency_errors="balance_chain: mismatch",
            needs_review=True,
        )
        updated = fresh_db.update_statement_consistency(
            sid,
            consistency_passed=True,
            consistency_errors="",
            needs_review=False,
        )
        assert updated is True
        st = fresh_db.get_statement(sid)
        assert st is not None
        assert st.consistency_passed is True
        assert st.consistency_errors == ""
        assert st.needs_review is False
        assert fresh_db.find_review_needed_statements() == []

    def test_partial_update_preserves_untouched_fields(self, fresh_db: FinanceDB) -> None:
        sid = _insert_statement(
            fresh_db,
            "partial-target",
            consistency_passed=False,
            consistency_errors="initial error",
            needs_review=True,
        )
        updated = fresh_db.update_statement_consistency(sid, consistency_errors="")
        assert updated is True
        st = fresh_db.get_statement(sid)
        assert st is not None
        assert st.consistency_errors == ""
        # Unberuehrte Felder bleiben unveraendert.
        assert st.consistency_passed is False
        assert st.needs_review is True

    def test_consistency_passed_none_leaves_column(self, fresh_db: FinanceDB) -> None:
        """``consistency_passed=None`` bedeutet 'nicht gesetzt', kein Reset."""
        sid = _insert_statement(fresh_db, "untouched-target", consistency_passed=True)
        updated = fresh_db.update_statement_consistency(sid, consistency_errors="x")
        assert updated is True
        st = fresh_db.get_statement(sid)
        assert st is not None
        assert st.consistency_passed is True  # bleibt unangetastet
        assert st.consistency_errors == "x"

    def test_no_fields_returns_false(self, fresh_db: FinanceDB) -> None:
        sid = _insert_statement(
            fresh_db,
            "noop-target",
            consistency_passed=False,
            consistency_errors="keep",
            needs_review=True,
        )
        assert fresh_db.update_statement_consistency(sid) is False
        st = fresh_db.get_statement(sid)
        assert st is not None
        assert st.consistency_passed is False
        assert st.consistency_errors == "keep"
        assert st.needs_review is True

    def test_unknown_statement_returns_false(self, fresh_db: FinanceDB) -> None:
        assert (
            fresh_db.update_statement_consistency(999_999, consistency_passed=True)
            is False
        )


# ============================================================================
# Review-Queue-Abfrage
# ============================================================================


class TestFindReviewNeeded:
    """``find_review_needed_statements``: Filter + Sortierung."""

    def test_returns_only_review_needed(self, fresh_db: FinanceDB) -> None:
        failed_id = _insert_statement(
            fresh_db,
            "review-failed",
            consistency_passed=False,
            consistency_errors="balance_chain: mismatch",
            needs_review=True,
        )
        warning_id = _insert_statement(
            fresh_db,
            "review-warning",
            consistency_passed=False,
            consistency_errors="prior_balance: skipped (missing data)",
            needs_review=True,
        )
        clean_id = _insert_statement(
            fresh_db,
            "review-clean",
            consistency_passed=True,
            consistency_errors="",
            needs_review=False,
        )

        review_needed = fresh_db.find_review_needed_statements()
        ids = {s.id for s in review_needed}
        assert ids == {failed_id, warning_id}
        assert clean_id not in ids

    def test_ordering_newest_first(self, fresh_db: FinanceDB) -> None:
        """``ORDER BY imported_at DESC, id DESC`` -> neueres Statement zuerst."""
        first = _insert_statement(
            fresh_db, "order-first", consistency_passed=False, needs_review=True
        )
        second = _insert_statement(
            fresh_db, "order-second", consistency_passed=False, needs_review=True
        )
        review_needed = fresh_db.find_review_needed_statements()
        assert [s.id for s in review_needed] == [second, first]

    def test_empty_when_none_need_review(self, fresh_db: FinanceDB) -> None:
        _insert_statement(
            fresh_db, "clean-only", consistency_passed=True, needs_review=False
        )
        assert fresh_db.find_review_needed_statements() == []

    def test_review_needed_rows_expose_statement_model(self, fresh_db: FinanceDB) -> None:
        """Ergebniszeilen sind vollstaendige ``Statement``-Modelle."""
        _insert_statement(
            fresh_db, "model-check", consistency_passed=False, needs_review=True
        )
        review_needed = fresh_db.find_review_needed_statements()
        assert len(review_needed) == 1
        st = review_needed[0]
        assert isinstance(st, Statement)
        assert st.source_pdf_hash == "model-check"
        assert st.needs_review is True





