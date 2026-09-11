"""Regressions-Tests: UNIQUE-Konflikte auf ``transfer_links`` (2026-09-11).

Reales Incident: Mehrdeutige Kandidaten (eine incoming-Tx, mehrere
outgoing-Partners) wurden in ``detect_transfer_candidates`` korrekt
mehrfach gelistet; verknüpfte der User zwei davon, traf das rohe
``sqlite3.IntegrityError`` die UI (Finance-Tab crash) und die Agent-Tools,
weil nur ``ValueError`` gefangen wurde.

Abgedeckt:

* ``FinanceDB.link_transfer`` liefert bei bereits verknüpfter Tx einen
  klaren ``ValueError`` (statt rohem IntegrityError) inkl. der ID der
  bestehenden Verknüpfung -- auf beiden Seiten (outgoing/incoming).
* ``FinanceDB.detect_transfer_candidates`` macht Mehrdeutigkeit sichtbar
  (``incoming_alternatives`` / ``outgoing_alternatives``).
* Das UI-Helfer ``finance.tab._apply_marked_links`` fängt
  ``sqlite3.DatabaseError`` pro Zeile ab und crasht nicht mehr.
"""
from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from typing import Any

import pandas as pd
import pytest

from finance import tab as finance_tab
from finance.db_schema import FinanceDB

IBAN_A = "DE89370400440532013000"
IBAN_B = "DE42500105175487229900"


@pytest.fixture()
def fresh_db(tmp_path: pytest.TempPath) -> Iterator[FinanceDB]:
    """Isolierte Finanz-DB in tmp_path (kein produktiver DB-Zugriff)."""
    yield FinanceDB(str(tmp_path / "finance_transfer_conflict.db"))


def _setup_accounts(db: FinanceDB) -> tuple[int, int]:
    bank_id = db.upsert_bank(
        "Conflict Test Bank", bic="CONFBDEFF", country_code="DE"
    )
    acct_a = db.upsert_account(
        bank_id, IBAN_A, account_holder="Holder A", account_type="checking"
    )
    acct_b = db.upsert_account(
        bank_id, IBAN_B, account_holder="Holder B", account_type="checking"
    )
    return int(acct_a), int(acct_b)


def _make_statement(db: FinanceDB, account_id: int, tag: str) -> int:
    return int(
        db.insert_statement(
            account_id=account_id,
            source_pdf_hash=f"stmt-{tag}",
            source_filename=f"{tag}.pdf",
            period_start="2026-01-01",
            period_end="2026-01-31",
            opening_balance=0.0,
            closing_balance=0.0,
        )
    )


def _insert_tx(
    db: FinanceDB,
    *,
    account_id: int,
    statement_id: int,
    amount_cents: int,
    booking_date: str,
    tag: str,
) -> int:
    """Direkte SQL-Insert (ohne Import-/Embedding-Pfad) für Test-Transaktionen."""
    conn = sqlite3.connect(db.db_path)
    try:
        cur = conn.execute(
            "INSERT INTO transactions "
            "(statement_id, account_id, booking_date, amount_cents, dedup_hash) "
            "VALUES (?, ?, ?, ?, ?)",
            (statement_id, account_id, booking_date, amount_cents, f"conflict-test-{tag}"),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


class TestLinkTransferConflicts:
    """UNIQUE(outgoing_tx_id) / UNIQUE(incoming_tx_id) -> klarer ValueError."""

    def test_link_valid_pair_succeeds(self, fresh_db: FinanceDB) -> None:
        acct_a, acct_b = _setup_accounts(fresh_db)
        stmt_a = _make_statement(fresh_db, acct_a, "a")
        stmt_b = _make_statement(fresh_db, acct_b, "b")
        out_tx = _insert_tx(
            fresh_db, account_id=acct_a, statement_id=stmt_a,
            amount_cents=-10000, booking_date="2026-01-05", tag="out-1",
        )
        in_tx = _insert_tx(
            fresh_db, account_id=acct_b, statement_id=stmt_b,
            amount_cents=10000, booking_date="2026-01-05", tag="in-1",
        )
        link_id = fresh_db.link_transfer(
            outgoing_tx_id=out_tx, incoming_tx_id=in_tx, source="user"
        )
        assert link_id > 0

    def test_incoming_already_linked_raises_valueerror(
        self, fresh_db: FinanceDB
    ) -> None:
        acct_a, acct_b = _setup_accounts(fresh_db)
        stmt_a = _make_statement(fresh_db, acct_a, "a")
        stmt_b = _make_statement(fresh_db, acct_b, "b")
        out_1 = _insert_tx(
            fresh_db, account_id=acct_a, statement_id=stmt_a,
            amount_cents=-10000, booking_date="2026-01-05", tag="out-1",
        )
        out_2 = _insert_tx(
            fresh_db, account_id=acct_a, statement_id=stmt_a,
            amount_cents=-10000, booking_date="2026-01-06", tag="out-2",
        )
        in_tx = _insert_tx(
            fresh_db, account_id=acct_b, statement_id=stmt_b,
            amount_cents=10000, booking_date="2026-01-05", tag="in-1",
        )
        fresh_db.link_transfer(outgoing_tx_id=out_1, incoming_tx_id=in_tx)
        with pytest.raises(ValueError, match=r"incoming transaction .* already linked"):
            fresh_db.link_transfer(outgoing_tx_id=out_2, incoming_tx_id=in_tx)

    def test_outgoing_already_linked_raises_valueerror(
        self, fresh_db: FinanceDB
    ) -> None:
        acct_a, acct_b = _setup_accounts(fresh_db)
        stmt_a = _make_statement(fresh_db, acct_a, "a")
        stmt_b = _make_statement(fresh_db, acct_b, "b")
        out_tx = _insert_tx(
            fresh_db, account_id=acct_a, statement_id=stmt_a,
            amount_cents=-10000, booking_date="2026-01-05", tag="out-1",
        )
        in_1 = _insert_tx(
            fresh_db, account_id=acct_b, statement_id=stmt_b,
            amount_cents=10000, booking_date="2026-01-05", tag="in-1",
        )
        in_2 = _insert_tx(
            fresh_db, account_id=acct_b, statement_id=stmt_b,
            amount_cents=10000, booking_date="2026-01-06", tag="in-2",
        )
        fresh_db.link_transfer(outgoing_tx_id=out_tx, incoming_tx_id=in_1)
        with pytest.raises(ValueError, match=r"outgoing transaction .* already linked"):
            fresh_db.link_transfer(outgoing_tx_id=out_tx, incoming_tx_id=in_2)

    def test_error_message_names_existing_link(self, fresh_db: FinanceDB) -> None:
        acct_a, acct_b = _setup_accounts(fresh_db)
        stmt_a = _make_statement(fresh_db, acct_a, "a")
        stmt_b = _make_statement(fresh_db, acct_b, "b")
        out_1 = _insert_tx(
            fresh_db, account_id=acct_a, statement_id=stmt_a,
            amount_cents=-10000, booking_date="2026-01-05", tag="out-1",
        )
        out_2 = _insert_tx(
            fresh_db, account_id=acct_a, statement_id=stmt_a,
            amount_cents=-10000, booking_date="2026-01-06", tag="out-2",
        )
        in_tx = _insert_tx(
            fresh_db, account_id=acct_b, statement_id=stmt_b,
            amount_cents=10000, booking_date="2026-01-05", tag="in-1",
        )
        link_id = fresh_db.link_transfer(
            outgoing_tx_id=out_1, incoming_tx_id=in_tx, source="user"
        )
        with pytest.raises(ValueError, match=f"link id {link_id}"):
            fresh_db.link_transfer(outgoing_tx_id=out_2, incoming_tx_id=in_tx)

    def test_relink_after_unlink_succeeds(self, fresh_db: FinanceDB) -> None:
        acct_a, acct_b = _setup_accounts(fresh_db)
        stmt_a = _make_statement(fresh_db, acct_a, "a")
        stmt_b = _make_statement(fresh_db, acct_b, "b")
        out_1 = _insert_tx(
            fresh_db, account_id=acct_a, statement_id=stmt_a,
            amount_cents=-10000, booking_date="2026-01-05", tag="out-1",
        )
        out_2 = _insert_tx(
            fresh_db, account_id=acct_a, statement_id=stmt_a,
            amount_cents=-10000, booking_date="2026-01-06", tag="out-2",
        )
        in_tx = _insert_tx(
            fresh_db, account_id=acct_b, statement_id=stmt_b,
            amount_cents=10000, booking_date="2026-01-05", tag="in-1",
        )
        link_id = fresh_db.link_transfer(
            outgoing_tx_id=out_1, incoming_tx_id=in_tx, source="user"
        )
        assert fresh_db.unlink_transfer(link_id) is True
        new_link = fresh_db.link_transfer(
            outgoing_tx_id=out_2, incoming_tx_id=in_tx, source="user"
        )
        assert new_link > 0
        assert new_link != link_id


class TestDetectCandidatesAmbiguity:
    """Mehrdeutige Kandidaten müssen Alternativen sichtbar machen."""

    def test_ambiguous_pair_exposes_alternatives(
        self, fresh_db: FinanceDB
    ) -> None:
        acct_a, acct_b = _setup_accounts(fresh_db)
        stmt_a = _make_statement(fresh_db, acct_a, "a")
        stmt_b = _make_statement(fresh_db, acct_b, "b")
        out_1 = _insert_tx(
            fresh_db, account_id=acct_a, statement_id=stmt_a,
            amount_cents=-10000, booking_date="2026-01-05", tag="out-1",
        )
        out_2 = _insert_tx(
            fresh_db, account_id=acct_a, statement_id=stmt_a,
            amount_cents=-10000, booking_date="2026-01-06", tag="out-2",
        )
        in_tx = _insert_tx(
            fresh_db, account_id=acct_b, statement_id=stmt_b,
            amount_cents=10000, booking_date="2026-01-05", tag="in-1",
        )
        cands = fresh_db.detect_transfer_candidates(max_days=5)
        pairs = {(c["outgoing_tx_id"], c["incoming_tx_id"]) for c in cands}
        assert (out_1, in_tx) in pairs
        assert (out_2, in_tx) in pairs
        for c in cands:
            if c["incoming_tx_id"] == in_tx:
                assert c["incoming_alternatives"] == 1
            assert c["outgoing_alternatives"] == 0

    def test_unique_pair_reports_zero_alternatives(
        self, fresh_db: FinanceDB
    ) -> None:
        acct_a, acct_b = _setup_accounts(fresh_db)
        stmt_a = _make_statement(fresh_db, acct_a, "a")
        stmt_b = _make_statement(fresh_db, acct_b, "b")
        out_tx = _insert_tx(
            fresh_db, account_id=acct_a, statement_id=stmt_a,
            amount_cents=-10000, booking_date="2026-01-05", tag="out-1",
        )
        in_tx = _insert_tx(
            fresh_db, account_id=acct_b, statement_id=stmt_b,
            amount_cents=10000, booking_date="2026-01-05", tag="in-1",
        )
        cands = fresh_db.detect_transfer_candidates(max_days=5)
        assert len(cands) == 1
        assert cands[0]["incoming_alternatives"] == 0
        assert cands[0]["outgoing_alternatives"] == 0


class TestApplyMarkedLinks:
    """UI-Helfer: DB-Fehler pro Zeile statt Tab-Crash (2026-09-11)."""

    def test_integrity_error_reported_per_row_not_raised(self) -> None:
        class _FlakyDB:
            def __init__(self) -> None:
                self.calls = 0

            def link_transfer(
                self, *, outgoing_tx_id: int, incoming_tx_id: int, source: str
            ) -> int:
                self.calls += 1
                if self.calls == 2:
                    raise sqlite3.IntegrityError(
                        "UNIQUE constraint failed: transfer_links.incoming_tx_id"
                    )
                return self.calls

        rows = pd.DataFrame(
            [
                {"out_id": 1, "in_id": 10, "Verknüpfen": True},
                {"out_id": 2, "in_id": 10, "Verknüpfen": True},
                {"out_id": 3, "in_id": 11, "Verknüpfen": False},
            ]
        )
        db = _FlakyDB()
        linked, errors = finance_tab._apply_marked_links(db, rows)
        assert db.calls == 2
        assert linked == 1
        assert len(errors) == 1
        assert "out=2/in=10" in errors[0]
        assert "UNIQUE constraint failed" in errors[0]

    def test_valueerror_reported_per_row(self) -> None:
        class _RejectingDB:
            def link_transfer(self, **_kwargs: Any) -> int:
                raise ValueError(
                    "incoming transaction 10 is already linked (link id 1)"
                )

        rows = pd.DataFrame([{"out_id": 1, "in_id": 10, "Verknüpfen": True}])
        linked, errors = finance_tab._apply_marked_links(_RejectingDB(), rows)
        assert linked == 0
        assert len(errors) == 1
        assert "already linked" in errors[0]

    def test_unmarked_rows_are_skipped(self) -> None:
        class _SpyDB:
            def __init__(self) -> None:
                self.calls = 0

            def link_transfer(self, **_kwargs: Any) -> int:
                self.calls += 1
                return 1

        rows = pd.DataFrame(
            [
                {"out_id": 1, "in_id": 10, "Verknüpfen": False},
                {"out_id": 2, "in_id": 11, "Verknüpfen": False},
            ]
        )
        db = _SpyDB()
        linked, errors = finance_tab._apply_marked_links(db, rows)
        assert linked == 0
        assert errors == []
        assert db.calls == 0

    def test_mixed_rows_keep_going_after_failure(self) -> None:
        class _FailOnceDB:
            def __init__(self) -> None:
                self.calls = 0

            def link_transfer(
                self, *, outgoing_tx_id: int, incoming_tx_id: int, source: str
            ) -> int:
                self.calls += 1
                if outgoing_tx_id == 2:
                    raise ValueError("outgoing transaction 2 is already linked")
                return 100 + self.calls

        rows = pd.DataFrame(
            [
                {"out_id": 1, "in_id": 10, "Verknüpfen": True},
                {"out_id": 2, "in_id": 11, "Verknüpfen": True},
                {"out_id": 3, "in_id": 12, "Verknüpfen": True},
            ]
        )
        db = _FailOnceDB()
        linked, errors = finance_tab._apply_marked_links(db, rows)
        assert linked == 2
        assert len(errors) == 1
        assert "out=2/in=11" in errors[0]