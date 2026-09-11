"""Real-Daten-Tests: Vorzeitraum-Saldo, Repair-Pfad, Cross-Account-Settlement.

Ergaenzt ``tests/test_finance_prior_balance.py`` (DB-Lookup auf Einheitenebene)
um realistische Auszugs-Ketten, die per CPU-only PDF-Inspektion bestaetigt
wurden:

  Bank (checking)
      Anfangssaldo 4886.51, Guthaben 13734.00, Belastungen 6573.22
      4886.51 + 13734.00 - 6573.22 = 12047.29 (Endsaldo)
      Vorzeitraum-Endsaldo = 4886.51  ->  prior_balance_link besteht

  Kreditkarte (credit_card)
      Anfangssaldo 408.45, Guthaben 4490.39, Belastungen 4908.45
      408.45 + 4490.39 - 4908.45 = -9.61 (cents-exakt)
      Vorzeitraum-Endsaldo = 408.45  ->  prior_balance_link besteht

  Cross-Account-Abwicklung (Bank -> Kreditkarte)
      Bank-Belastung "LADUNG KREDITKARTENKONTO" -1000.00
      Kreditkarten-Auszug mit Endsaldo -1000.00  ->  Auto-Settlement-Link

Isolierung (kein LLM, kein Embedding-Modell im VRAM):
  * Statements laufen ueber die oeffentliche API (``upsert_bank`` /
    ``upsert_account`` / ``insert_statement``) -- ohne Transaktionen, also
    ohne Refresh des Transaktions-Suchindex.
  * Transaktionen (Repair-/Settlement-Tests) werden per rohem SQL via
    ``db._connect()`` gelegt (Muster aus ``tests/test_finance_consistency_db.py``)
    -> keine Regel-Engine, kein ``EmbeddingSingleton``.
  * Repair-Pfad: ``DoclingProcessor`` wird als Fake in ``sys.modules``
    injiziert, ``_extract_header`` wird gemonkeypatchet und
    ``FinanceExtractor`` ohne ``__init__`` gebaut (kein
    ``LLMStructuredWrapper``, keine Token-Budget-Aufloesung).
"""

from __future__ import annotations

import sys
import types
import uuid
from collections.abc import Iterator
from typing import Any, Optional

import pytest

from finance.consistency import (
    STATUS_FAILED,
    STATUS_PASSED,
    STATUS_PASSED_WITH_WARNINGS,
    evaluate_statement_consistency,
)
from finance.db_schema import FinanceDB

# ---------------------------------------------------------------------------
# IBANs (zwei getrennte Konten)
# ---------------------------------------------------------------------------
IBAN_BANK = "DE89370400440532013000"
IBAN_CARD = "DE42500105175487229900"

# ---------------------------------------------------------------------------
# Reale Auszugs-Daten (PDF-Inspektion)
# ---------------------------------------------------------------------------
BANK_PRIOR_CLOSING = 4886.51     # Endsaldo des Vorzeitraum-Auszugs
BANK_OPENING = 4886.51           # == Vorzeitraum-Saldo -> prior_balance_link
BANK_CREDITS = 13734.00
BANK_DEBITS = 6573.22
BANK_CLOSING = 12047.29          # 4886.51 + 13734.00 - 6573.22
BANK_TX_IN = 13734.00
BANK_TX_OUT = -6573.22

CC_PRIOR_CLOSING = 408.45        # Endsaldo des Vorzeitraum-Kartenauszugs
CC_OPENING = 408.45
CC_CREDITS = 4490.39
CC_DEBITS = 4908.45
CC_CLOSING_EXACT = -9.61         # 408.45 + 4490.39 - 4908.45 (cents-exakt)
CC_CLOSING_REAL = -9.60          # ausgewiesener Endsaldo (1-Cent-Abweichung)

SETTLE_AMOUNT = 1000.00          # LADUNG KREDITKARTENKONTO

# ---------------------------------------------------------------------------
# Fixtures / Helfer
# ---------------------------------------------------------------------------


@pytest.fixture()
def fresh_db(tmp_path: pytest.TempPath) -> Iterator[FinanceDB]:
    """Frische, isolierte FinanceDB (eigene Datei)."""
    return FinanceDB(str(tmp_path / "finance_real_data.db"))


def _account(db: FinanceDB, iban: str, account_type: str = "checking") -> int:
    bank_id = db.upsert_bank("Real Bank", bic="REALDEFF", country_code="DE")
    return db.upsert_account(
        bank_id,
        iban,
        account_holder="Real User",
        currency="EUR",
        account_type=account_type,
    )


def _stmt(
    db: FinanceDB,
    account_id: int,
    pdf_hash: str,
    *,
    period_start: str,
    period_end: str,
    opening: Optional[float] = None,
    closing: Optional[float] = None,
    credits: Optional[float] = None,
    debits: Optional[float] = None,
    consistency_passed: Optional[bool] = None,
    consistency_errors: Optional[str] = None,
    needs_review: bool = False,
) -> int:
    """Statement via oeffentliche API (ohne Transaktionen -> keine Side-Effects)."""
    return db.insert_statement(
        account_id=account_id,
        source_pdf_hash=pdf_hash,
        source_filename=f"{pdf_hash}.pdf",
        period_start=period_start,
        period_end=period_end,
        opening_balance=opening,
        closing_balance=closing,
        total_credits=credits,
        total_debits=debits,
        consistency_passed=consistency_passed,
        consistency_errors=consistency_errors,
        needs_review=needs_review,
    )


def _tx(
    db: FinanceDB,
    statement_id: int,
    account_id: int,
    booking_date: str,
    amount_cents: int,
    *,
    counterparty: Optional[str] = None,
    purpose: Optional[str] = None,
    raw_text: Optional[str] = None,
) -> int:
    """Roher SQL-INSERT -- vermeidet ``persist_statement_import`` bewusst, da
    nicht-leere Transaktionen den Suchindex / ``EmbeddingSingleton`` triggern
    (VRAM). ``db._connect()`` ist das etablierte Muster in
    ``tests/test_finance_consistency_db.py``."""
    with db._connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO transactions
                (statement_id, account_id, booking_date, amount_cents,
                 counterparty, purpose, raw_text, transaction_nature, dedup_hash)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'ordinary', ?)
            """,
            (
                statement_id,
                account_id,
                booking_date,
                amount_cents,
                counterparty,
                purpose,
                raw_text,
                uuid.uuid4().hex,
            ),
        )
        return int(cur.lastrowid)

# ---------------------------------------------------------------------------
# Engine-Ebene: reale Auszugs-Ketten (ohne DB)
# ---------------------------------------------------------------------------


class TestRealDataEngine:
    """``evaluate_statement_consistency`` mit realen Auszugs-Ketten."""

    def test_bank_chain_and_prior_link_pass(self) -> None:
        report = evaluate_statement_consistency(
            opening_balance=BANK_OPENING,
            closing_balance=BANK_CLOSING,
            total_credits=BANK_CREDITS,
            total_debits=BANK_DEBITS,
            transactions=[{"amount": BANK_TX_IN}, {"amount": BANK_TX_OUT}],
            prior_closing_balance=BANK_PRIOR_CLOSING,
        )
        assert report.status == STATUS_PASSED
        assert report.needs_review is False
        assert all(c.status == "passed" for c in report.checks)
        assert report.prior_closing_balance_cents == 488651
        assert report.errors == []

    def test_credit_card_exact_chain_and_prior_link_pass(self) -> None:
        report = evaluate_statement_consistency(
            opening_balance=CC_OPENING,
            closing_balance=CC_CLOSING_EXACT,
            total_credits=CC_CREDITS,
            total_debits=CC_DEBITS,
            transactions=[{"amount": -CC_DEBITS}, {"amount": CC_CREDITS}],
            prior_closing_balance=CC_PRIOR_CLOSING,
        )
        assert report.status == STATUS_PASSED
        assert report.needs_review is False
        assert report.prior_closing_balance_cents == 40845

    def test_credit_card_real_closing_one_cent_off_requires_review(self) -> None:
        """Reales Endsaldo (-9.60) weicht 1 Cent von exakter Summe (-9.61)
        ab -> balance_chain faellt, Review bleibt stehen."""
        report = evaluate_statement_consistency(
            opening_balance=CC_OPENING,
            closing_balance=CC_CLOSING_REAL,
            total_credits=CC_CREDITS,
            total_debits=CC_DEBITS,
            transactions=[{"amount": -CC_DEBITS}, {"amount": CC_CREDITS}],
            prior_closing_balance=CC_PRIOR_CLOSING,
        )
        assert report.status == STATUS_FAILED
        assert report.needs_review is True
        by_name = {c.name: c for c in report.checks}
        assert by_name["balance_chain"].status == "failed"
        assert by_name["prior_balance_link"].status == "passed"
        assert "Saldo-Kette weicht ab" in "; ".join(report.errors)

    def test_inconsistent_prior_balance_requires_review(self) -> None:
        report = evaluate_statement_consistency(
            opening_balance=BANK_OPENING,
            closing_balance=BANK_CLOSING,
            total_credits=BANK_CREDITS,
            total_debits=BANK_DEBITS,
            transactions=[{"amount": BANK_TX_IN}, {"amount": BANK_TX_OUT}],
            prior_closing_balance=508.51,  # falscher Vorzeitraum-Saldo
        )
        assert report.status == STATUS_FAILED
        assert report.needs_review is True
        by_name = {c.name: c for c in report.checks}
        assert by_name["prior_balance_link"].status == "failed"
        assert by_name["balance_chain"].status == "passed"
        assert "Anfangssaldo" in "; ".join(report.errors)

    def test_missing_prior_balance_is_conservative_warning(self) -> None:
        """Ohne Vorzeitraum-Auszug: alle Pruefungen bestehen, aber der
        Vorzeitraum-Link ist nicht verifizierbar -> 'passed_with_warnings',
        Review bleibt (konservativ: kein impliziter Pass)."""
        report = evaluate_statement_consistency(
            opening_balance=BANK_OPENING,
            closing_balance=BANK_CLOSING,
            total_credits=BANK_CREDITS,
            total_debits=BANK_DEBITS,
            transactions=[{"amount": BANK_TX_IN}, {"amount": BANK_TX_OUT}],
            prior_closing_balance=None,
        )
        assert report.status == STATUS_PASSED_WITH_WARNINGS
        assert report.needs_review is True
        by_name = {c.name: c for c in report.checks}
        assert by_name["prior_balance_link"].status == "skipped"

# ---------------------------------------------------------------------------
# DB-Ebene: persistierte reale Daten + Prior-Lookup + Engine
# ---------------------------------------------------------------------------


class TestRealDataDbRoundTrip:
    """Prior-Lookup und Konsistenz-Engine auf persistierten realen Daten."""

    def test_bank_prior_lookup_and_pass(self, fresh_db: FinanceDB) -> None:
        account_id = _account(fresh_db, IBAN_BANK, "checking")
        _stmt(
            fresh_db, account_id, "bank-prior",
            period_start="2025-12-01", period_end="2025-12-31",
            opening=1200.00, closing=BANK_PRIOR_CLOSING,
        )
        sid = _stmt(
            fresh_db, account_id, "bank-current",
            period_start="2026-01-01", period_end="2026-01-31",
            opening=BANK_OPENING, closing=BANK_CLOSING,
            credits=BANK_CREDITS, debits=BANK_DEBITS,
        )

        prior = fresh_db.get_prior_closing_balance(account_id, "2026-01-01")
        assert prior == BANK_PRIOR_CLOSING

        report = evaluate_statement_consistency(
            opening_balance=BANK_OPENING,
            closing_balance=BANK_CLOSING,
            total_credits=BANK_CREDITS,
            total_debits=BANK_DEBITS,
            transactions=[{"amount": BANK_TX_IN}, {"amount": BANK_TX_OUT}],
            prior_closing_balance=prior,
        )
        assert report.status == STATUS_PASSED

        stmt = fresh_db.get_statement(sid)
        assert stmt.opening_balance_cents == 488651
        assert stmt.closing_balance_cents == 1204729

    def test_credit_card_prior_lookup_and_pass(self, fresh_db: FinanceDB) -> None:
        account_id = _account(fresh_db, IBAN_CARD, "credit_card")
        _stmt(
            fresh_db, account_id, "cc-prior",
            period_start="2025-12-01", period_end="2025-12-31",
            closing=CC_PRIOR_CLOSING,
        )
        _stmt(
            fresh_db, account_id, "cc-current",
            period_start="2026-01-01", period_end="2026-01-31",
            opening=CC_OPENING, closing=CC_CLOSING_EXACT,
            credits=CC_CREDITS, debits=CC_DEBITS,
        )

        prior = fresh_db.get_prior_closing_balance(account_id, "2026-01-01")
        assert prior == CC_PRIOR_CLOSING

        report = evaluate_statement_consistency(
            opening_balance=CC_OPENING,
            closing_balance=CC_CLOSING_EXACT,
            total_credits=CC_CREDITS,
            total_debits=CC_DEBITS,
            transactions=[{"amount": -CC_DEBITS}, {"amount": CC_CREDITS}],
            prior_closing_balance=prior,
        )
        assert report.status == STATUS_PASSED
        assert report.needs_review is False

    def test_missing_prior_statement_is_conservative(
        self, fresh_db: FinanceDB
    ) -> None:
        """Nur EIN Auszug vorhanden -> kein Vorzeitraum -> konservatives
        'passed_with_warnings' statt implizitem Pass."""
        account_id = _account(fresh_db, IBAN_BANK, "checking")
        _stmt(
            fresh_db, account_id, "bank-only",
            period_start="2026-01-01", period_end="2026-01-31",
            opening=BANK_OPENING, closing=BANK_CLOSING,
            credits=BANK_CREDITS, debits=BANK_DEBITS,
        )

        prior = fresh_db.get_prior_closing_balance(account_id, "2025-12-01")
        assert prior is None

        report = evaluate_statement_consistency(
            opening_balance=BANK_OPENING,
            closing_balance=BANK_CLOSING,
            total_credits=BANK_CREDITS,
            total_debits=BANK_DEBITS,
            transactions=[{"amount": BANK_TX_IN}, {"amount": BANK_TX_OUT}],
            prior_closing_balance=None,
        )
        assert report.status == STATUS_PASSED_WITH_WARNINGS
        assert report.needs_review is True

# ---------------------------------------------------------------------------
# Repair-Pfad: mocked Docling + Header (keine PDF, kein LLM, kein GPU)
# ---------------------------------------------------------------------------


class _FakeDoclingProcessor:
    """Stand-in fuer ``utils.docling_processor.DoclingProcessor`` (keine PDF)."""

    @staticmethod
    def get_instance() -> "_FakeDoclingProcessor":
        return _FakeDoclingProcessor()

    def convert_file(self, pdf_path: str) -> Any:
        return types.SimpleNamespace(success=True, text="FAKE-MARKDOWN", error=None)


def _install_fake_docling(monkeypatch: pytest.MonkeyPatch) -> None:
    module = types.ModuleType("utils.docling_processor")
    module.DoclingProcessor = _FakeDoclingProcessor
    monkeypatch.setitem(sys.modules, "utils.docling_processor", module)


def _make_extractor(
    monkeypatch: pytest.MonkeyPatch, db: FinanceDB, header: Any
) -> Any:
    """``FinanceExtractor`` ohne ``__init__`` (kein ``LLMStructuredWrapper``,
    keine Token-Budget-Aufloesung) + gepatchter ``_extract_header``."""
    from finance.extractor import FinanceExtractor

    extractor = FinanceExtractor.__new__(FinanceExtractor)
    extractor.db = db
    monkeypatch.setattr(
        FinanceExtractor, "_extract_header", lambda self, text: header
    )
    return extractor


def _header(
    opening: Optional[float],
    closing: Optional[float],
    *,
    period_start: Optional[str],
    period_end: Optional[str],
    credits: Optional[float] = None,
    debits: Optional[float] = None,
) -> Any:
    return types.SimpleNamespace(
        opening_balance=opening,
        closing_balance=closing,
        period_start=period_start,
        period_end=period_end,
        total_credits=credits,
        total_debits=debits,
    )


class TestRepairPath:
    """``repair_statement_header`` mit Fake-Docling (keine GPU/LLM)."""

    @staticmethod
    def _dummy_pdf(tmp_path: pytest.TempPath) -> str:
        pdf = tmp_path / "statement.pdf"
        pdf.write_bytes(b"%PDF-1.4 fake")
        return str(pdf)

    def test_consistent_header_with_prior_clears_review(
        self, fresh_db: FinanceDB, tmp_path: pytest.TempPath, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        account_id = _account(fresh_db, IBAN_BANK, "checking")
        _stmt(
            fresh_db, account_id, "bank-prior",
            period_start="2025-12-01", period_end="2025-12-31",
            closing=BANK_PRIOR_CLOSING,
        )
        sid = _stmt(
            fresh_db, account_id, "bank-current",
            period_start="2026-01-01", period_end="2026-01-31",
            needs_review=True, consistency_passed=False,
            consistency_errors="legacy import",
        )
        _tx(fresh_db, sid, account_id, "2026-01-15", 1373400, raw_text="GUTSCHRIFT")
        _tx(fresh_db, sid, account_id, "2026-01-20", -657322, raw_text="LASTSCHRIFT")

        _install_fake_docling(monkeypatch)
        header = _header(
            BANK_OPENING, BANK_CLOSING,
            period_start="2026-01-01", period_end="2026-01-31",
            credits=BANK_CREDITS, debits=BANK_DEBITS,
        )
        extractor = _make_extractor(monkeypatch, fresh_db, header)

        assert extractor.repair_statement_header(sid, self._dummy_pdf(tmp_path)) is True

        stmt = fresh_db.get_statement(sid)
        assert stmt.needs_review is False
        assert stmt.consistency_passed is True
        assert (stmt.consistency_errors or "") == ""
        assert stmt.opening_balance_cents == 488651
        assert stmt.closing_balance_cents == 1204729
        assert stmt.total_credits_cents == 1373400
        assert stmt.total_debits_cents == 657322
        assert all(s.id != sid for s in fresh_db.find_review_needed_statements())

    def test_inconsistent_header_preserves_review(
        self, fresh_db: FinanceDB, tmp_path: pytest.TempPath, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        account_id = _account(fresh_db, IBAN_BANK, "checking")
        sid = _stmt(
            fresh_db, account_id, "bank-broken",
            period_start="2026-01-01", period_end="2026-01-31",
            needs_review=True, consistency_passed=False,
        )
        _tx(fresh_db, sid, account_id, "2026-01-15", 1373400, raw_text="GUTSCHRIFT")
        _tx(fresh_db, sid, account_id, "2026-01-20", -657322, raw_text="LASTSCHRIFT")

        _install_fake_docling(monkeypatch)
        # Endsaldo weicht von Anfangssaldo + Summe ab -> balance_chain faellt.
        header = _header(
            BANK_OPENING, 9999.99,
            period_start="2026-01-01", period_end="2026-01-31",
            credits=BANK_CREDITS, debits=BANK_DEBITS,
        )
        extractor = _make_extractor(monkeypatch, fresh_db, header)

        assert extractor.repair_statement_header(sid, self._dummy_pdf(tmp_path)) is True

        stmt = fresh_db.get_statement(sid)
        assert stmt.needs_review is True
        assert stmt.consistency_passed is False
        assert stmt.consistency_errors
        assert sid in [s.id for s in fresh_db.find_review_needed_statements()]

    def test_repair_prior_lookup_excludes_repaired_statement(
        self, fresh_db: FinanceDB, tmp_path: pytest.TempPath, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Selbstreferenz-Guard: der Vorzeitraum-Lookup muss das reparierte
        Statement ausschliessen. Ist das reparierte Statement bereits mit
        ``period_end < before_period`` und einem Endsaldo befuellt, waere
        ohne Exklusion EIGENES Endsaldo der 'Vorzeitraum-Saldo' und der
        Link trivial / falsch."""
        account_id = _account(fresh_db, IBAN_BANK, "checking")
        _stmt(
            fresh_db, account_id, "bank-prev",
            period_start="2025-12-01", period_end="2025-12-31",
            closing=BANK_PRIOR_CLOSING,
        )
        # Defekter Import: Endsaldo vorhanden, Review markiert.
        sid = _stmt(
            fresh_db, account_id, "bank-current",
            period_start="2026-01-01", period_end="2026-01-31",
            closing=BANK_CLOSING,
            needs_review=True,
        )
        _tx(fresh_db, sid, account_id, "2026-01-15", 1373400, raw_text="GUTSCHRIFT")
        _tx(fresh_db, sid, account_id, "2026-01-20", -657322, raw_text="LASTSCHRIFT")

        # Direkter API-Nachweis der Exklusions-Semantik:
        #   ohne Exklusion: eigenes (veraltetes) Endsaldo wird zum
        #   'Vorzeitraum-Saldo' -> Selbstreferenz.
        assert fresh_db.get_prior_closing_balance(account_id, "2026-02-01") == BANK_CLOSING
        #   mit Exklusion: der echte Vorzeitraum wird gefunden.
        assert (
            fresh_db.get_prior_closing_balance(
                account_id, "2026-02-01", exclude_statement_id=sid
            )
            == BANK_PRIOR_CLOSING
        )

        # Repair-Ebene: selbst wenn der Header-Extraktor die Periode
        # falsch liest (2026-02), vergleicht die Exklusion den Link
        # gegen den echten Vorzeitraum (4886.51 == Anfangssaldo) ->
        # die Reparatur besteht und klaert die Review.
        _install_fake_docling(monkeypatch)
        header = _header(
            BANK_OPENING, BANK_CLOSING,
            period_start="2026-02-01", period_end="2026-02-28",
            credits=BANK_CREDITS, debits=BANK_DEBITS,
        )
        extractor = _make_extractor(monkeypatch, fresh_db, header)

        assert extractor.repair_statement_header(sid, self._dummy_pdf(tmp_path)) is True

        stmt = fresh_db.get_statement(sid)
        assert stmt.needs_review is False
        assert stmt.consistency_passed is True

# ---------------------------------------------------------------------------
# Cross-Account-Settlement: Bank -> Kreditkarte (LADUNG KREDITKARTENKONTO)
# ---------------------------------------------------------------------------
#
# Reale Abwicklung: Die Bank bucht die Kreditkarten-Abrechnung als
# Sammelbelastung ("LADUNG KREDITKARTENKONTO" -1000.00). Das passende
# Kreditkarten-Statement erkennt man am Endsaldo, dessen Betrag der
# Belastung entspricht. Die Link-Erkennung ist strikt:
#
#   * Belastung auf einem NICHT-Kreditkarten-Konto (amount < 0)
#   * Kreditkarten-Statement mit |Endsaldo| == |Belastung|
#   * Buchungstag innerhalb [period_end, period_end + 45d]
#   * genau EIN Kandidat (sonst keine Auto-Linkung)
#
# ``relink_all_transfers()`` und ``detect_statement_settlement_gaps()``
# sind rein SQL-basiert (keine Embeddings, kein LLM) -> sicher im Test.


class TestCrossAccountSettlement:
    """Bank -> Kreditkarte: Sammelbelastung wird am Endsaldo verlinkt."""

    @staticmethod
    def _cc_statement(fresh_db: FinanceDB, closing: float, *,
                      period_end: str = "2026-01-31",
                      pdf_hash: str = "cc-stmt") -> int:
        card_id = _account(fresh_db, IBAN_CARD, "credit_card")
        return _stmt(
            fresh_db, card_id, pdf_hash,
            period_start="2026-01-01", period_end=period_end,
            closing=closing,
        )

    @staticmethod
    def _bank_charge(fresh_db: FinanceDB, amount_cents: int,
                     booking_date: str) -> int:
        bank_id = _account(fresh_db, IBAN_BANK, "checking")
        sid = _stmt(
            fresh_db, bank_id, "bank-stmt",
            period_start="2026-01-01", period_end="2026-01-31",
            opening=BANK_OPENING, closing=BANK_CLOSING,
            credits=BANK_CREDITS, debits=BANK_DEBITS,
        )
        return _tx(
            fresh_db, sid, bank_id, booking_date, amount_cents,
            purpose="LADUNG KREDITKARTENKONTO",
            raw_text=f"LADUNG KREDITKARTENKONTO {abs(amount_cents) / 100:.2f}",
        )

    def test_settlement_link_and_nature_update(
        self, fresh_db: FinanceDB
    ) -> None:
        """Belastung -1000.00 (3 Tage nach period_end) -> Auto-Link mit
        Confidence 0.8 und Nature-Update auf internal_transfer."""
        cc_sid = self._cc_statement(fresh_db, -SETTLE_AMOUNT)
        tx_id = self._bank_charge(fresh_db, -100000, "2026-02-03")

        linked = fresh_db.relink_all_transfers()
        assert linked == 1  # genau ein Settlement-Link, keine Paare

        with fresh_db._connect() as conn:
            row = conn.execute(
                "SELECT * FROM statement_settlements"
            ).fetchone()
        assert row is not None
        assert int(row["transaction_id"]) == tx_id
        assert int(row["statement_id"]) == cc_sid
        assert row["source"] == "auto"
        # 3 Tage Distanz -> Confidence-Stufe 0.8 (2.5 < 3.0 < 7.5)
        assert float(row["confidence"]) == pytest.approx(0.8)

        tx = fresh_db.get_transaction(tx_id)
        assert tx is not None
        assert tx.transaction_nature == "internal_transfer"

    def test_settlement_same_day_booking_full_confidence(
        self, fresh_db: FinanceDB
    ) -> None:
        """Buchung am selben Tag wie period_end -> Confidence 1.0."""
        cc_sid = self._cc_statement(fresh_db, -SETTLE_AMOUNT)
        tx_id = self._bank_charge(fresh_db, -100000, "2026-01-31")

        assert fresh_db.relink_all_transfers() == 1
        with fresh_db._connect() as conn:
            row = conn.execute("SELECT * FROM statement_settlements").fetchone()
        assert row is not None
        assert int(row["transaction_id"]) == tx_id
        assert int(row["statement_id"]) == cc_sid
        assert float(row["confidence"]) == pytest.approx(1.0)

    def test_no_auto_link_outside_window_gap_classifies(
        self, fresh_db: FinanceDB
    ) -> None:
        """Belastung 90 Tage nach period_end: kein Auto-Link, aber die
        Gap-Diagnose klassifiziert es als candidate_out_of_window."""
        cc_sid = self._cc_statement(fresh_db, -SETTLE_AMOUNT)
        tx_id = self._bank_charge(fresh_db, -100000, "2026-05-01")

        assert fresh_db.relink_all_transfers() == 0
        with fresh_db._connect() as conn:
            assert conn.execute(
                "SELECT COUNT(*) AS c FROM statement_settlements"
            ).fetchone()["c"] == 0
        # Nature bleibt 'ordinary' (kein Link).
        assert fresh_db.get_transaction(tx_id).transaction_nature == "ordinary"

        gaps = fresh_db.detect_statement_settlement_gaps()
        assert len(gaps) == 1
        gap = gaps[0]
        assert gap["statement_id"] == cc_sid
        assert gap["status"] == "candidate_out_of_window"
        assert gap["in_window_candidates"] == []
        assert len(gap["extended_candidates"]) == 1
        assert gap["extended_candidates"][0]["transaction_id"] == tx_id

    def test_real_residual_amount_mismatch_no_candidate(
        self, fresh_db: FinanceDB
    ) -> None:
        """Reales Restfael: Kartensaldo -9.61 wird NICHT durch die
        1000.00er-Belastung ausgeglichen (Betraege differieren) ->
        kein Link, Gap-Diagnose 'no_candidate'."""
        cc_sid = self._cc_statement(fresh_db, CC_CLOSING_EXACT)
        tx_id = self._bank_charge(fresh_db, -100000, "2026-02-03")

        assert fresh_db.relink_all_transfers() == 0
        assert fresh_db.get_transaction(tx_id).transaction_nature == "ordinary"

        gaps = fresh_db.detect_statement_settlement_gaps()
        assert len(gaps) == 1
        assert gaps[0]["statement_id"] == cc_sid
        assert gaps[0]["status"] == "no_candidate"
        assert gaps[0]["in_window_candidates"] == []
        assert gaps[0]["extended_candidates"] == []

    def test_credit_card_own_charge_not_linked(
        self, fresh_db: FinanceDB
    ) -> None:
        """Eine Belastung AUF dem Kreditkartenkonto selbst ist keine
        Sammelbelastung und darf nicht verlinkt werden."""
        card_id = _account(fresh_db, IBAN_CARD, "credit_card")
        cc_sid = _stmt(
            fresh_db, card_id, "cc-stmt",
            period_start="2026-01-01", period_end="2026-01-31",
            closing=-SETTLE_AMOUNT,
        )
        # Eigenbelastung auf dem Kartenkonto (z.B. Maengelbelastung).
        tx_id = _tx(
            fresh_db, cc_sid, card_id, "2026-02-03", -100000,
            purpose="LADUNG KREDITKARTENKONTO",
            raw_text="LADUNG KREDITKARTENKONTO 1000.00",
        )

        assert fresh_db.relink_all_transfers() == 0
        with fresh_db._connect() as conn:
            assert conn.execute(
                "SELECT COUNT(*) AS c FROM statement_settlements"
            ).fetchone()["c"] == 0
        assert fresh_db.get_transaction(tx_id).transaction_nature == "ordinary"

    def test_gap_disappears_after_link(
        self, fresh_db: FinanceDB
    ) -> None:
        """Read-only-Diagnose vor der Linkung: single_candidate_in_window;
        nach ``relink_all_transfers()`` verschwindet der offene Fall."""
        cc_sid = self._cc_statement(fresh_db, -SETTLE_AMOUNT)
        tx_id = self._bank_charge(fresh_db, -100000, "2026-02-03")

        gaps = fresh_db.detect_statement_settlement_gaps()
        assert len(gaps) == 1
        assert gaps[0]["status"] == "single_candidate_in_window"
        assert len(gaps[0]["in_window_candidates"]) == 1
        assert gaps[0]["in_window_candidates"][0]["transaction_id"] == tx_id
        assert gaps[0]["in_window_candidates"][0]["day_diff"] == pytest.approx(3.0)

        assert fresh_db.relink_all_transfers() == 1
        assert fresh_db.detect_statement_settlement_gaps() == []

    def test_relink_is_idempotent(
        self, fresh_db: FinanceDB
    ) -> None:
        """Zweiter Durchlauf erzeugt keine doppelte Linkung."""
        self._cc_statement(fresh_db, -SETTLE_AMOUNT)
        self._bank_charge(fresh_db, -100000, "2026-02-03")

        assert fresh_db.relink_all_transfers() == 1
        assert fresh_db.relink_all_transfers() == 0
        with fresh_db._connect() as conn:
            assert conn.execute(
                "SELECT COUNT(*) AS c FROM statement_settlements"
            ).fetchone()["c"] == 1
