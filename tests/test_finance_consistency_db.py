"""Deterministische Cents-Konsistenzpruefung: DB-Ebene (Persistenz + Review).

Ergaenzt ``tests/test_finance_consistency.py`` (Engine, ohne DB) um die
Persistenz der Konsistenz-/Review-Felder in ``finance.db_schema.FinanceDB``:

  * Frisch-Schema: statements-Tabella hat die Konsistenz-Spalten + Review-Index;
    ``needs_review`` ist NOT NULL mit Default 0.
  * Insert/Read-Round-Trip: ``consistency_passed`` / ``consistency_errors`` /
    ``needs_review`` / ``total_credits_cents`` / ``total_debits_cents`` werden
    exakt persistiert und zurueckgegeben.
  * ``find_review_needed_statements``: liefert genau die review-pflichtigen.
  * ``update_statement_consistency``: selektives Update (None = nicht anfaessen),
    rowcount-Semantik, unbekannte Id -> False.
  * Legacy-Migration: Alt-DB ohne Konsistenz-Spalten wird idempotent migriert
    (Spalten angehaengt, ``needs_review`` bekommt Default 0).

Bewusst OHNE Transaktionen: das Einfuegen von Buchungen triggert den
Transaktions-Suchindex (Embedding-Modell via ``EmbeddingSingleton``), der hier
nicht relevant ist und die Tests nicht-deterministisch machen wuerde. Die
Konsistenz-/Review-Persistenz ist eine Eigenschaft der statements-Zeile und
vollstaendig ohne Transaktionen pruefbar.
"""

from __future__ import annotations

import sqlite3

import pytest

from finance.db_schema import FinanceDB, Statement

# Spalten, die das Konsistenz-Feature in statements einfuehrt.
_CONSISTENCY_COLUMNS = {
    "total_credits_cents",
    "total_debits_cents",
    "consistency_passed",
    "consistency_errors",
    "needs_review",
}


# ---------------------------------------------------------------------------
# Fixtures / Helfer
# ---------------------------------------------------------------------------


@pytest.fixture()
def db(tmp_path) -> FinanceDB:
    """Frische, isolierte FinanceDB (eigene Datei, kein Singleton)."""
    return FinanceDB(db_path=str(tmp_path / "finance_test.db"))


def _account_id(db: FinanceDB, iban: str = "DE02120300000000202051") -> int:
    """Erzeugt Bank + Konto und liefert die account_id."""
    bank_id = db.upsert_bank("Testbank", bic="TESTDEFF", country_code="DE")
    return db.upsert_account(
        bank_id, iban, account_holder="Test", currency="EUR", account_type="checking"
    )


def _statement_columns(db: FinanceDB) -> dict:
    with db._connect() as conn:
        rows = conn.execute("PRAGMA table_info(statements)").fetchall()
    return {row["name"]: row for row in rows}


def _index_names(db: FinanceDB) -> set:
    with db._connect() as conn:
        rows = conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'index' AND tbl_name = 'statements'"
        ).fetchall()
    return {row["name"] for row in rows}


def _insert_statement(db: FinanceDB, account_id: int, *, pdf_hash: str, **overrides) -> int:
    params = dict(
        account_id=account_id,
        source_pdf_hash=pdf_hash,
        source_filename="test.pdf",
        period_start="2026-01-01",
        period_end="2026-01-31",
        opening_balance=500.00,
        closing_balance=1100.00,
    )
    params.update(overrides)
    return db.insert_statement(**params)


# ---------------------------------------------------------------------------
# Frisch-Schema
# ---------------------------------------------------------------------------


def test_fresh_schema_statements_has_consistency_columns(db: FinanceDB) -> None:
    cols = set(_statement_columns(db))
    missing = _CONSISTENCY_COLUMNS - cols
    assert not missing, f"statements fehlt/fehlen: {sorted(missing)}"


def test_fresh_schema_needs_review_not_null_default_zero(db: FinanceDB) -> None:
    col = _statement_columns(db)["needs_review"]
    assert int(col["notnull"]) == 1
    assert col["dflt_value"] == "0"


def test_fresh_schema_has_review_index(db: FinanceDB) -> None:
    assert "idx_stmt_review" in _index_names(db)


def test_fresh_schema_raw_insert_uses_defaults(db: FinanceDB) -> None:
    """Roher INSERT ohne needs_review -> DDL-Default 0 muss greifen."""
    acct = _account_id(db)
    with db._connect() as conn:
        cur = conn.execute(
            "INSERT INTO statements (account_id, source_pdf_hash) VALUES (?, ?)",
            (acct, "default-hash"),
        )
        sid = int(cur.lastrowid)
    stmt = db.get_statement(sid)
    assert stmt is not None
    assert stmt.needs_review is False
    assert stmt.consistency_passed is None
    assert stmt.consistency_errors is None
    assert stmt.total_credits_cents is None
    assert stmt.total_debits_cents is None


# ---------------------------------------------------------------------------
# Insert/Read-Round-Trip
# ---------------------------------------------------------------------------


def test_roundtrip_preserves_passed_state(db: FinanceDB) -> None:
    acct = _account_id(db)
    sid = _insert_statement(
        db,
        acct,
        pdf_hash="passed-1",
        total_credits=1000.00,
        total_debits=400.00,
        consistency_passed=True,
        consistency_errors=None,
        needs_review=False,
    )
    stmt = db.get_statement(sid)
    assert stmt is not None
    assert stmt.opening_balance_cents == 50000
    assert stmt.closing_balance_cents == 110000
    assert stmt.total_credits_cents == 100000
    assert stmt.total_debits_cents == 40000
    assert stmt.consistency_passed is True
    assert stmt.consistency_errors is None
    assert stmt.needs_review is False


def test_roundtrip_preserves_failed_review_state(db: FinanceDB) -> None:
    acct = _account_id(db)
    sid = _insert_statement(
        db,
        acct,
        pdf_hash="failed-1",
        closing_balance=999.00,
        total_credits=1000.00,
        total_debits=400.00,
        consistency_passed=False,
        consistency_errors="credits total mismatch; balance chain mismatch",
        needs_review=True,
    )
    stmt = db.get_statement(sid)
    assert stmt is not None
    assert stmt.consistency_passed is False
    assert stmt.consistency_errors == "credits total mismatch; balance chain mismatch"
    assert stmt.needs_review is True

# ---------------------------------------------------------------------------
# find_review_needed_statements
# ---------------------------------------------------------------------------


def test_find_review_needed_empty(db: FinanceDB) -> None:
    assert db.find_review_needed_statements() == []


def test_find_review_needed_returns_only_review(db: FinanceDB) -> None:
    acct = _account_id(db)
    review = _insert_statement(
        db,
        acct,
        pdf_hash="review-1",
        consistency_passed=False,
        consistency_errors="x",
        needs_review=True,
    )
    ok = _insert_statement(
        db,
        acct,
        pdf_hash="ok-1",
        consistency_passed=True,
        needs_review=False,
    )
    result = db.find_review_needed_statements()
    assert all(isinstance(s, Statement) for s in result)
    assert [s.id for s in result] == [review]
    assert ok not in {s.id for s in result}


def test_find_review_needed_orders_newest_first(db: FinanceDB) -> None:
    acct = _account_id(db)
    first = _insert_statement(db, acct, pdf_hash="order-1", needs_review=True)
    second = _insert_statement(db, acct, pdf_hash="order-2", needs_review=True)
    third = _insert_statement(db, acct, pdf_hash="order-3", needs_review=True)
    result = db.find_review_needed_statements()
    # imported_at ist nicht-dekrementierend; bei Gleichstand id DESC -> [3, 2, 1]
    assert [s.id for s in result] == [third, second, first]


# ---------------------------------------------------------------------------
# update_statement_consistency
# ---------------------------------------------------------------------------


def _review_statement(db: FinanceDB) -> int:
    acct = _account_id(db)
    return _insert_statement(
        db,
        acct,
        pdf_hash="update-1",
        consistency_passed=False,
        consistency_errors="initial error",
        needs_review=True,
    )


def test_update_sets_all_fields(db: FinanceDB) -> None:
    sid = _review_statement(db)
    assert (
        db.update_statement_consistency(
            sid,
            needs_review=False,
            consistency_passed=True,
            consistency_errors="",  # leer = Fehler loeschen (None wuerde es belassen)
        )
        is True
    )
    stmt = db.get_statement(sid)
    assert stmt.needs_review is False
    assert stmt.consistency_passed is True
    assert stmt.consistency_errors == ""


def test_update_only_needs_review_leaves_rest(db: FinanceDB) -> None:
    """None-Felder bleiben unangetastet (selektives Update)."""
    sid = _review_statement(db)
    assert db.update_statement_consistency(sid, needs_review=False) is True
    stmt = db.get_statement(sid)
    assert stmt.needs_review is False
    assert stmt.consistency_passed is False
    assert stmt.consistency_errors == "initial error"


def test_update_no_fields_returns_false_and_is_noop(db: FinanceDB) -> None:
    sid = _review_statement(db)
    assert db.update_statement_consistency(sid) is False
    stmt = db.get_statement(sid)
    assert stmt.needs_review is True
    assert stmt.consistency_passed is False
    assert stmt.consistency_errors == "initial error"


def test_update_unknown_id_returns_false(db: FinanceDB) -> None:
    assert db.update_statement_consistency(999999, needs_review=True) is False


# ---------------------------------------------------------------------------
# Legacy-Migration (Alt-DB ohne Konsistenz-Spalten)
# ---------------------------------------------------------------------------


def _create_legacy_statements_db(path) -> None:
    """Erzeugt eine Alt-DB: statements OHNE die 5 Konsistenz-Spalten."""
    conn = sqlite3.connect(str(path))
    try:
        conn.execute(
            """
            CREATE TABLE statements (
                id                      INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id              INTEGER NOT NULL,
                period_start            TEXT,
                period_end              TEXT,
                opening_balance_cents   INTEGER,
                closing_balance_cents   INTEGER,
                source_pdf_hash         TEXT NOT NULL UNIQUE,
                source_filename         TEXT,
                imported_at             TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )
        conn.execute(
            "INSERT INTO statements "
            "(account_id, source_pdf_hash, source_filename, "
            " opening_balance_cents, closing_balance_cents) "
            "VALUES (1, 'legacy-hash', 'legacy.pdf', 50000, 110000)"
        )
        conn.commit()
    finally:
        conn.close()


def test_legacy_migration_adds_consistency_columns(tmp_path) -> None:
    db_file = tmp_path / "legacy.db"
    _create_legacy_statements_db(db_file)
    db = FinanceDB(db_path=str(db_file))
    missing = _CONSISTENCY_COLUMNS - set(_statement_columns(db))
    assert not missing, f"Migration hat nicht angehaengt: {sorted(missing)}"


def test_legacy_migration_old_rows_get_defaults(tmp_path) -> None:
    """Bestehende Zeilen: needs_review=0 (Default), uebrige Konsistenz NULL."""
    db_file = tmp_path / "legacy.db"
    _create_legacy_statements_db(db_file)
    db = FinanceDB(db_path=str(db_file))
    stmt = db.get_statement(1)
    assert stmt is not None
    assert stmt.needs_review is False
    assert stmt.consistency_passed is None
    assert stmt.consistency_errors is None
    assert stmt.total_credits_cents is None
    assert stmt.total_debits_cents is None
    # Bestehende Daten bleiben erhalten
    assert stmt.opening_balance_cents == 50000
    assert stmt.closing_balance_cents == 110000
    assert stmt.source_pdf_hash == "legacy-hash"


def test_legacy_migration_is_idempotent(tmp_path) -> None:
    """Zweites Oeffnen derselben DB darf nicht fehlschlagen (kein doppeltes ALTER)."""
    db_file = tmp_path / "legacy.db"
    _create_legacy_statements_db(db_file)
    FinanceDB(db_path=str(db_file))
    db2 = FinanceDB(db_path=str(db_file))
    assert _CONSISTENCY_COLUMNS <= set(_statement_columns(db2))
    assert db2.get_statement(1) is not None