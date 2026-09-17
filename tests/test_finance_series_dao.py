"""AP2 Finance Recurring Series — DAO-Integrationstests (FORECAST_UX, 2026-09-17).

Validiert die Serien-DAO-Schicht (``finance/db_schema.py``) end-zu-end gegen
eine echte SQLite-Datei — bewusst NICHT nur Unit-Tests um Einzelmethoden:

* CRUD: create/get/list/update/status/delete mit Fail-Fast-Validierung
* Optimistic Locking: ``SeriesRevisionConflict`` bei abweichender Revision
* Idempotenz: ``client_token`` verhindert Duplikate (UI-Doppel-Click)
* Kandidaten-Lifecycle: save (upsert)/get/list/confirm/reject —
  NIE automatische Bestätigung (Prompt §5.4, T15)
* Ausnahmen: skip/move/amount pro ORIGINAL-Termin, upsert + remove
* Occurrence-Links: strikt 1:1 pro Serie+Datum, link/unlink/list
* Journal + Undo: created/updated/status_changed/deleted/restored,
  Journal ueberlebt die Serien-Loeschung, Undo rollt zurueck
* Engine-Integration: ``series_engine.expand_series`` mit DAO-Ausnahmen,
  ``detect_candidates`` -> DAO save -> confirm -> usable SeriesSpec

Regressionsabdeckung fuer die in dieser Session gefundenen DAO-Bugs:
* INSERT/UPDATE Spaltenname ``evidence_json`` (nicht ``evidence``)
* ``confirm_candidate`` liest ``cand.evidence_json``
* Mapper-Name ``_series_link_from_row`` (link_occurrence/list_occurrence_links)
* NOT-NULL ``series_candidates.evidence_json`` (evidence=None => "")
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from finance.db_schema import (  # noqa: E402
    DEFAULT_CURRENCY,
    FinanceDB,
    RecurringSeries,
    SeriesExceptionRow,
    SeriesRevisionConflict,
)
from finance import series_engine  # noqa: E402

EUR_IBAN = "DE89370400440532013000"
OTHER_IBAN = "DE42500105175487360033"
COUNTERPARTY = "Streaming Music GmbH"


# ---------------------------------------------------------------------------
# Fixtures / Helpers
# ---------------------------------------------------------------------------


@pytest.fixture()
def db(tmp_path: Path):
    """Frische DB mit je einem Konto auf beiden IBANs."""
    d = FinanceDB(tmp_path / "series_test.db")
    bank_id = d.upsert_bank("Test Bank", bic="TESTDEFF", country_code="DE")
    d.upsert_account(bank_id, EUR_IBAN, account_holder="Test", currency="EUR")
    d.upsert_account(bank_id, OTHER_IBAN, account_holder="Test2", currency="EUR")
    return d


def make_series(d: FinanceDB, **overrides: Any) -> RecurringSeries:
    kwargs: Dict[str, Any] = dict(
        iban=EUR_IBAN,
        direction="expense",
        cadence="monthly",
        anchor_date="2026-07-15",
        amount_cents=990,
        counterparty=COUNTERPARTY,
        title="Streaming-Abo",
    )
    kwargs.update(overrides)
    return d.create_series(**kwargs)


def make_txn(d: FinanceDB, account_id: int, txn_date: str, amount: int, seq: int = 0) -> int:
    """Echte Transaktion im produktiven Schema (Statement + Transaction)."""
    with d._connect() as conn:
        cur = conn.execute(
            "INSERT INTO statements (account_id, source_pdf_hash) VALUES (?, ?)",
            (account_id, f"test-statement-{seq}-{txn_date}"),
        )
        stmt_id = int(cur.lastrowid or 0)
        cur = conn.execute(
            "INSERT INTO transactions "
            "(statement_id, account_id, booking_date, amount_cents, dedup_hash) "
            "VALUES (?, ?, ?, ?, ?)",
            (stmt_id, account_id, txn_date, amount, f"test-dedup-{seq}-{txn_date}-{amount}"),
        )
        return int(cur.lastrowid or 0)


def account_id_for(d: FinanceDB, iban: str) -> int:
    with d._connect() as conn:
        row = conn.execute("SELECT id FROM accounts WHERE iban = ?", (iban,)).fetchone()
    return int(row["id"])
# ---------------------------------------------------------------------------
# CRUD + Validierung
# ---------------------------------------------------------------------------


class TestSeriesCrud:
    def test_create_defaults(self, db: FinanceDB) -> None:
        s = make_series(db)
        assert s.iban == EUR_IBAN
        assert s.currency == DEFAULT_CURRENCY  # DB-Default (Konto-Waehrung nicht uebernommen)
        assert s.anchor_day == 15  # aus anchor_date abgeleitet
        assert s.period_n == 1
        assert s.status == "active"
        assert s.source == "manual"
        assert s.revision == 1
        assert s.title == "Streaming-Abo"
        assert s.counterparty == COUNTERPARTY

    def test_create_unknown_account(self, db: FinanceDB) -> None:
        with pytest.raises(ValueError, match="unknown account"):
            db.create_series(
                iban="DE00999999999999999999",
                direction="expense",
                cadence="monthly",
                anchor_date="2026-07-15",
                amount_cents=100,
            )

    @pytest.mark.parametrize(
        "overrides",
        [
            dict(cadence="fortnightly"),
            dict(direction="transfer"),
            dict(amount_cents=0),
            dict(amount_cents=-5),
            dict(amount_cents="990"),
            dict(period_n=0),
            dict(cadence="n_months"),  # fehlt period_n
            dict(cadence="n_months", period_n=1),  # 2..11
            dict(cadence="n_months", period_n=12),  # 2..11
            dict(cadence="n_weeks", period_n=53),  # 2..52
            dict(cadence="monthly", period_n=2),  # monthly => 1
            dict(anchor_day=0),
            dict(anchor_day=32),
            dict(anchor_date="15.07.2026"),
            dict(effective_from="2026-08-01", effective_to="2026-07-01"),
            dict(status="frozen"),
            dict(source="auto"),
        ],
    )
    def test_create_invalid_fields(self, db: FinanceDB, overrides: Dict[str, Any]) -> None:
        with pytest.raises(ValueError):
            make_series(db, **overrides)

    def test_get_unknown(self, db: FinanceDB) -> None:
        with pytest.raises(ValueError, match="not found"):
            db.get_series(999)

    def test_list_filters(self, db: FinanceDB) -> None:
        a = make_series(db)
        b = make_series(
            db,
            iban=OTHER_IBAN,
            direction="income",
            cadence="n_months",
            period_n=2,
            anchor_date="2026-07-03",
            amount_cents=15000,
            counterparty="Arbeitgeber AG",
        )
        assert {s.id for s in db.list_series()} == {a.id, b.id}
        assert len(db.list_series(status="active")) == 2
        assert [s.id for s in db.list_series(iban=OTHER_IBAN)] == [b.id]
        assert [s.id for s in db.list_series(direction="income")] == [b.id]
        with pytest.raises(ValueError):
            db.list_series(status="bogus")

    def test_client_token_idempotency(self, db: FinanceDB) -> None:
        first = make_series(db, client_token="ui-token-1")
        second = make_series(db, client_token="ui-token-1")
        assert first.id == second.id
        assert len(db.list_series()) == 1
# ---------------------------------------------------------------------------
# Update + Optimistic Locking + Status + Delete
# ---------------------------------------------------------------------------


class TestSeriesUpdateLocking:
    def test_update_and_revision(self, db: FinanceDB) -> None:
        s = make_series(db)
        updated = db.update_series(s.id, s.revision, amount_cents=1190, title="Streaming Abo")
        assert updated.revision == 2
        assert updated.amount_cents == 1190
        assert db.get_series(s.id).title == "Streaming Abo"

    def test_update_stale_revision_conflict(self, db: FinanceDB) -> None:
        s = make_series(db)
        db.update_series(s.id, s.revision, amount_cents=1190)
        with pytest.raises(SeriesRevisionConflict) as exc:
            db.update_series(s.id, s.revision, amount_cents=1290)
        assert exc.value.actual_revision == 2
        # Ueberschreibung verboten: letzter erfolgreicher Wert bleibt
        assert db.get_series(s.id).amount_cents == 1190

    def test_update_no_fields(self, db: FinanceDB) -> None:
        s = make_series(db)
        with pytest.raises(ValueError, match="no fields"):
            db.update_series(s.id, s.revision)

    def test_set_status_cycle(self, db: FinanceDB) -> None:
        s = make_series(db)
        assert db.set_series_status(s.id, "paused").status == "paused"
        assert db.set_series_status(s.id, "ended").status == "ended"
        assert db.list_series(status="ended")[0].id == s.id
        with pytest.raises(ValueError):
            db.set_series_status(s.id, "cancelled")

    def test_delete_removes_row_keeps_journal(self, db: FinanceDB) -> None:
        s = make_series(db)
        db.update_series(s.id, s.revision, amount_cents=1090)
        deleted = db.delete_series(s.id)
        assert deleted.id == s.id
        assert db.list_series() == []
        with pytest.raises(ValueError, match="not found"):
            db.get_series(s.id)
        journal = db.get_series_journal(s.id)
        actions = [j.action for j in journal]
        assert "deleted" in actions
        assert actions.count("updated") == 1

    def test_delete_unknown(self, db: FinanceDB) -> None:
        with pytest.raises(ValueError, match="not found"):
            db.delete_series(42)


# ---------------------------------------------------------------------------
# Ausnahmen (skip / move / amount)
# ---------------------------------------------------------------------------


class TestSeriesExceptions:
    def test_skip_upsert_and_list(self, db: FinanceDB) -> None:
        s = make_series(db)
        row1 = db.set_series_exception(s.id, "2026-08-15", "skip")
        assert row1.exception_type == "skip"
        # Upsert auf (series, original_due_date): kein Duplikat
        row2 = db.set_series_exception(s.id, "2026-08-15", "skip")
        assert row2.id == row1.id
        rows = db.list_series_exceptions(s.id)
        assert len(rows) == 1
        assert rows[0].original_due_date == "2026-08-15"

    def test_move_requires_new_date(self, db: FinanceDB) -> None:
        s = make_series(db)
        with pytest.raises(ValueError):
            db.set_series_exception(s.id, "2026-08-15", "move")
        row = db.set_series_exception(s.id, "2026-08-15", "move", new_due_date="2026-08-20")
        assert row.new_due_date == "2026-08-20"

    def test_amount_requires_cents(self, db: FinanceDB) -> None:
        s = make_series(db)
        with pytest.raises(ValueError):
            db.set_series_exception(s.id, "2026-08-15", "amount")
        row = db.set_series_exception(s.id, "2026-08-15", "amount", amount_cents=1490)
        assert row.amount_cents == 1490

    def test_skip_tolerates_extra_fields(self, db: FinanceDB) -> None:
        # DAO-Design: 'skip' darf zusaetzliche Felder tragen (upsert-lenient);
        # die Engine ignoriert new_due_date/amount bei skip (Slot wird entfernt)
        s = make_series(db)
        row = db.set_series_exception(s.id, "2026-08-15", "skip", new_due_date="2026-08-20")
        assert row.exception_type == "skip"

    def test_invalid_type(self, db: FinanceDB) -> None:
        s = make_series(db)
        with pytest.raises(ValueError):
            db.set_series_exception(s.id, "2026-08-15", "holiday")

    def test_remove_and_remove_missing(self, db: FinanceDB) -> None:
        s = make_series(db)
        created = db.set_series_exception(s.id, "2026-08-15", "skip")
        removed = db.remove_series_exception(s.id, "2026-08-15")
        assert removed.id == created.id
        assert db.list_series_exceptions(s.id) == []
        with pytest.raises(ValueError, match="no exception"):
            db.remove_series_exception(s.id, "2026-08-15")

    def test_exceptions_unknown_series(self, db: FinanceDB) -> None:
        with pytest.raises(ValueError, match="not found"):
            db.set_series_exception(999, "2026-08-15", "skip")

    def test_note_roundtrip_and_update(self, db: FinanceDB) -> None:
        # AP2 S1: series_exceptions.note (Freitext-Begründung, T11)
        s = make_series(db)
        row1 = db.set_series_exception(s.id, "2026-08-15", "skip", note="Urlaub")
        assert row1.note == "Urlaub"
        # Upsert desselben ORIGINAL-Termins: note wird aktualisiert, Revision +1
        row2 = db.set_series_exception(s.id, "2026-08-15", "skip", note="Urlaub verlängert")
        assert row2.id == row1.id
        assert row2.note == "Urlaub verlängert"
        assert row2.revision == row1.revision + 1
        assert db.list_series_exceptions(s.id)[0].note == "Urlaub verlängert"

    def test_note_normalization(self, db: FinanceDB) -> None:
        s = make_series(db)
        # Whitespace-only => NULL (keine leeren Strings in der DB)
        row = db.set_series_exception(s.id, "2026-08-15", "skip", note="   ")
        assert row.note is None
        # None bleibt NULL
        row2 = db.set_series_exception(s.id, "2026-08-15", "skip", note=None)
        assert row2.note is None
        # Unicode/Umbruch wird normalisiert (stripped)
        row3 = db.set_series_exception(s.id, "2026-08-15", "skip", note="  Krank  ")
        assert row3.note == "Krank"

    def test_note_in_journal_payload(self, db: FinanceDB) -> None:
        # note fließt in die before/after-Snapshots des Journals (Undo-Basis)
        s = make_series(db)
        db.set_series_exception(s.id, "2026-08-15", "skip", note="Urlaub")
        db.set_series_exception(s.id, "2026-08-15", "skip", note="Krank")
        journal = db.get_series_journal(s.id)
        last = journal[0]
        assert last.action == "exception_updated"
        after = json.loads(last.after_json)
        before = json.loads(last.before_json)
        assert after["note"] == "Krank"
        assert before["note"] == "Urlaub"


# ---------------------------------------------------------------------------
# Kandidaten-Lifecycle (T15: NIE auto-bestätigen)
# ---------------------------------------------------------------------------


class TestCandidateLifecycle:
    def _save(self, db: FinanceDB, fp: str = "fp-stream-1", **overrides: Any):
        kwargs: Dict[str, Any] = dict(
            iban=EUR_IBAN,
            currency="EUR",
            direction="expense",
            counterparty=COUNTERPARTY,
            cadence="monthly",
            period_n=1,
            anchor_day=15,
            anchor_date="2026-07-15",
            amount_cents=990,
            evidence="txns: 11, 45, 78",
        )
        kwargs.update(overrides)
        return db.save_series_candidate(fingerprint=fp, **kwargs)

    def test_save_created_then_upsert(self, db: FinanceDB) -> None:
        row, created = self._save(db)
        assert created is True
        assert row.status == "pending"
        assert row.confidence == "low"
        up, created2 = self._save(db, evidence="txns: 11, 45, 78, 102")
        assert created2 is False
        assert up.id == row.id
        fresh = db.get_candidate(row.fingerprint)
        assert fresh.evidence_json == "txns: 11, 45, 78, 102"
        assert len(db.list_candidates(status="pending")) == 1

    def test_save_without_evidence_not_null(self, db: FinanceDB) -> None:
        # Regression: evidence=None => "" (DDL NOT NULL)
        row, created = self._save(db, fp="fp-no-evidence", evidence=None)
        assert created is True
        assert db.get_candidate(row.fingerprint).evidence_json == ""

    def test_confirm_creates_detected_series(self, db: FinanceDB) -> None:
        row, _ = self._save(db)
        cand, series = db.confirm_candidate(row.fingerprint, title="Streaming Abo", amount_cents=1190)
        assert cand.status == "confirmed"
        assert cand.series_id == series.id
        assert series.source == "detected"
        assert series.candidate_fingerprint == row.fingerprint
        # Evidenz wird in die Serie uebernommen (Review-Kontext, T15)
        assert series.evidence_json == "txns: 11, 45, 78"
        # Explizite Review-Werte haben Vorrang, Rest kommt vom Kandidaten
        assert series.amount_cents == 1190
        assert series.counterparty == COUNTERPARTY
        assert series.cadence == "monthly"
        assert series.anchor_day == 15
        assert db.get_series(series.id).id == series.id

    def test_confirm_twice_rejected(self, db: FinanceDB) -> None:
        row, _ = self._save(db)
        db.confirm_candidate(row.fingerprint)
        with pytest.raises(ValueError, match="already confirmed"):
            db.confirm_candidate(row.fingerprint)

    def test_reject_then_confirm_fails(self, db: FinanceDB) -> None:
        row, _ = self._save(db)
        rejected = db.reject_candidate(row.fingerprint)
        assert rejected.status == "rejected"
        with pytest.raises(ValueError, match="only 'pending' can be confirmed"):
            db.confirm_candidate(row.fingerprint)
        with pytest.raises(ValueError, match="only 'pending' can be rejected"):
            db.reject_candidate(row.fingerprint)

    def test_confirm_reject_unknown(self, db: FinanceDB) -> None:
        with pytest.raises(ValueError, match="not found"):
            db.confirm_candidate("nope")
        with pytest.raises(ValueError, match="not found"):
            db.reject_candidate("nope")

    def test_list_candidates_filters(self, db: FinanceDB) -> None:
        p1, _ = self._save(db, fp="fp-a")
        p2, _ = self._save(db, fp="fp-b", counterparty="Other AG")
        db.reject_candidate(p2.fingerprint)
        assert {c.fingerprint for c in db.list_candidates(status="pending")} == {"fp-a"}
        assert {c.fingerprint for c in db.list_candidates(status="rejected")} == {"fp-b"}
        with pytest.raises(ValueError):
            db.list_candidates(status="weird")

    def test_invalid_candidate_fields(self, db: FinanceDB) -> None:
        with pytest.raises(ValueError):
            self._save(db, fp="fp-bad", cadence="biweekly")
        with pytest.raises(ValueError):
            self._save(db, fp="fp-bad", anchor_day=40)
        with pytest.raises(ValueError):
            self._save(db, fp="fp-bad", confidence="extreme")
        with pytest.raises(ValueError):
            self._save(db, fp="")
# ---------------------------------------------------------------------------
# Occurrence-Links (konservativ 1:1, T14/T16)
# ---------------------------------------------------------------------------


class TestOccurrenceLinks:
    def test_link_update_and_list(self, db: FinanceDB) -> None:
        s = make_series(db)
        aid = account_id_for(db, EUR_IBAN)
        txn = make_txn(db, aid, "2026-08-15", -990)
        link = db.link_occurrence(s.id, "2026-08-15", txn, status="matched")
        assert link.transaction_id == txn
        # Gleiche Serie+Datum => Update statt Duplikat (strikt 1:1)
        link2 = db.link_occurrence(s.id, "2026-08-15", txn, status="confirmed")
        assert link2.id == link.id
        assert link2.status == "confirmed"
        links = db.list_occurrence_links(s.id)
        assert len(links) == 1
        assert links[0].status == "confirmed"

    def test_unlink(self, db: FinanceDB) -> None:
        s = make_series(db)
        aid = account_id_for(db, EUR_IBAN)
        txn = make_txn(db, aid, "2026-08-15", -990)
        db.link_occurrence(s.id, "2026-08-15", txn)
        assert db.unlink_occurrence(s.id, "2026-08-15") is True
        assert db.list_occurrence_links(s.id) == []
        assert db.unlink_occurrence(s.id, "2026-08-15") is False

    def test_link_unknown_series(self, db: FinanceDB) -> None:
        aid = account_id_for(db, EUR_IBAN)
        txn = make_txn(db, aid, "2026-08-15", -990)
        with pytest.raises(ValueError, match="not found"):
            db.link_occurrence(4242, "2026-08-15", txn)

    def test_invalid_link_status(self, db: FinanceDB) -> None:
        s = make_series(db)
        aid = account_id_for(db, EUR_IBAN)
        txn = make_txn(db, aid, "2026-08-15", -990)
        with pytest.raises(ValueError):
            db.link_occurrence(s.id, "2026-08-15", txn, status="guessed")


# ---------------------------------------------------------------------------
# Journal + Undo
# ---------------------------------------------------------------------------


class TestJournalUndo:
    def test_journal_order_and_limit(self, db: FinanceDB) -> None:
        s = make_series(db)
        db.update_series(s.id, 1, amount_cents=1090)
        db.set_series_status(s.id, "paused")
        journal = db.get_series_journal(s.id)
        assert [j.action for j in journal] == ["status_changed", "updated", "created"]
        assert db.get_series_journal(s.id, limit=1)[0].action == "status_changed"
        with pytest.raises(ValueError):
            db.get_series_journal(s.id, limit=0)

    def test_undo_reverts_update(self, db: FinanceDB) -> None:
        s = make_series(db, amount_cents=990)
        db.update_series(s.id, 1, amount_cents=1490)
        restored = db.undo_series_change(s.id)
        assert restored.amount_cents == 990
        assert restored.revision == 3
        actions = [j.action for j in db.get_series_journal(s.id)]
        assert "restored" in actions

    def test_undo_without_changes(self, db: FinanceDB) -> None:
        s = make_series(db)
        with pytest.raises(ValueError, match="no changes to undo"):
            db.undo_series_change(s.id)

    def test_undo_restores_deleted_series(self, db: FinanceDB) -> None:
        s = make_series(db, amount_cents=990)
        db.update_series(s.id, 1, amount_cents=1290)
        db.delete_series(s.id)
        assert db.list_series() == []
        restored = db.undo_series_change(s.id)
        # T10: Restore auf den letzten Feld-/Status-Vor-Zustand (990,
        # VOR dem Update); die Loeschung selbst bleibt im Journal
        assert restored.id == s.id
        assert restored.amount_cents == 990
        assert db.get_series(s.id).id == s.id

    def test_undo_invalid_steps(self, db: FinanceDB) -> None:
        s = make_series(db)
        with pytest.raises(ValueError):
            db.undo_series_change(s.id, steps=0)
        with pytest.raises(ValueError):
            db.undo_series_change(s.id, steps=True)
# ---------------------------------------------------------------------------
# Engine <-> DAO Integration (Forecast-Pfad)
# ---------------------------------------------------------------------------


def series_to_spec(s: RecurringSeries) -> series_engine.SeriesSpec:
    return series_engine.SeriesSpec(
        key=f"series:{s.id}",
        direction=s.direction,
        cadence=s.cadence,
        period_n=s.period_n,
        anchor_day=s.anchor_day,
        anchor_date=s.anchor_date,
        amount_cents=s.amount_cents,
        effective_from=s.effective_from,
        effective_to=s.effective_to,
        counterparty=s.counterparty,
        currency=s.currency,
        iban=s.iban,
    )


def dao_exceptions(rows: List[SeriesExceptionRow], key: str) -> Dict[str, series_engine.SeriesException]:
    return {
        r.original_due_date: series_engine.SeriesException(
            key=key,
            original_due_date=r.original_due_date,
            exception_type=r.exception_type,
            new_due_date=r.new_due_date,
            amount_cents=r.amount_cents,
        )
        for r in rows
    }


class TestEngineDaoIntegration:
    def test_expand_with_dao_exceptions(self, db: FinanceDB) -> None:
        s = make_series(db, anchor_date="2026-06-15", amount_cents=990)
        spec = series_to_spec(s)
        # Basis: 15.06, 15.07, 15.08
        base = series_engine.expand_series(spec, window_start="2026-06-01", window_end="2026-08-31")
        assert [o.date for o in base] == ["2026-06-15", "2026-07-15", "2026-08-15"]

        # Ausnahmen aus der DAO: skip 07/15, move 08/15 -> 08/20, amount 06/15
        db.set_series_exception(s.id, "2026-07-15", "skip")
        db.set_series_exception(s.id, "2026-08-15", "move", new_due_date="2026-08-20")
        db.set_series_exception(s.id, "2026-06-15", "amount", amount_cents=1290)
        exc = dao_exceptions(db.list_series_exceptions(s.id), spec.key)

        out = series_engine.expand_series(
            spec, exceptions=exc, window_start="2026-06-01", window_end="2026-08-31"
        )
        by_orig = {o.original_date: o for o in out}
        assert "2026-07-15" not in by_orig  # skip
        assert by_orig["2026-08-15"].date == "2026-08-20"  # move (Identitaet erhalten)
        assert by_orig["2026-08-15"].exception == "move"
        assert by_orig["2026-06-15"].amount_cents == 1290  # amount
        # Sortiert nach wirksamem Datum
        assert [o.date for o in out] == ["2026-06-15", "2026-08-20"]

    def test_detect_to_dao_roundtrip(self, db: FinanceDB) -> None:
        # 3 monatl. Ausgaben, gleiche Gegenpartei/Konto/Waehrung => Kandidat
        facts = [
            {"iban": EUR_IBAN, "currency": "EUR", "counterparty": COUNTERPARTY,
             "transaction_id": "t1", "date": "2026-05-15", "amount_cents": -990},
            {"iban": EUR_IBAN, "currency": "EUR", "counterparty": COUNTERPARTY,
             "transaction_id": "t2", "date": "2026-06-15", "amount_cents": -990},
            {"iban": EUR_IBAN, "currency": "EUR", "counterparty": COUNTERPARTY,
             "transaction_id": "t3", "date": "2026-07-15", "amount_cents": -1190},
        ]
        cands = series_engine.detect_candidates(facts)
        assert len(cands) == 1
        cand = cands[0]
        assert cand.cadence == "monthly"
        assert cand.period_n == 1
        assert cand.anchor_day == 15
        assert cand.amount_cents == 1190  # letzter (aktueller) Betrag, F02

        # In die DAO persistieren (Evidence als JSON-String)
        row, created = db.save_series_candidate(
            fingerprint=cand.fingerprint,
            iban=cand.iban,
            currency=cand.currency,
            direction=cand.direction,
            counterparty=cand.counterparty,
            cadence=cand.cadence,
            period_n=cand.period_n,
            anchor_day=cand.anchor_day,
            anchor_date=cand.anchor_date,
            amount_cents=cand.amount_cents,
            evidence=json.dumps(
                [[tid, d, amt] for tid, d, amt in cand.evidence], sort_keys=True
            ),
            confidence=cand.confidence,
        )
        assert created is True
        assert db.get_candidate(cand.fingerprint).status == "pending"

        # Bestaetigen => nutzbare, expandierbare Serie
        _c, series = db.confirm_candidate(cand.fingerprint)
        spec = series_to_spec(series)
        assert spec.key == f"series:{series.id}"
        occ = series_engine.expand_series(spec, window_start="2026-08-01", window_end="2026-09-30")
        assert [o.date for o in occ] == ["2026-08-15", "2026-09-15"]
        assert all(o.amount_cents == 1190 for o in occ)
        assert all(o.exception is None for o in occ)

    def test_detect_ignores_single_observation(self) -> None:
        facts = [
            {"iban": EUR_IBAN, "currency": "EUR", "counterparty": COUNTERPARTY,
             "transaction_id": "t1", "date": "2026-07-15", "amount_cents": -990},
        ]
        assert series_engine.detect_candidates(facts) == []


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))