"""Finance-DB-Tests für die Prior-Saldo-Lookups (Konsistenz-Verknüpfung).

Ergänzt ``tests/test_finance_db_consistency.py`` um die beiden Read-only
Helfer, die der Import-/Repair-Pfad nutzt, um den Endsaldo des nächsten
vorhergehenden Statements desselben Kontos zu bestimmen:

  - ``find_account_id_by_iban``   Account-Lookup (Normalisierung, None-Fälle)
  - ``get_prior_closing_balance`` nächster Vorstatement-Schlussaldo
                                  (Ordering, None-Fälle, ``exclude_statement_id``,
                                   Account-Isolation)

Bewusst OHNE Transaktionen (leere Tx-Liste): ``persist_statement_import``
mit leerer Liste löst weder ``_refresh_transaction_search_docs_within_conn``
noch ein Embedding-Modell aus -- die DB-Schicht bleibt dadurch isoliert,
deterministisch und ohne GPU testbar.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Optional

import pytest

from finance.db_schema import FinanceDB

# Zwei gültige DE-IBAN-Strukturen (getrennte Konten). ``_norm_iban``
# normalisiert zu Grossbuchstaben ohne Leerstellen.
IBAN_A = "DE89370400440532013000"
IBAN_B = "DE42500105175487229900"


# ============================================================================
# Fixtures & Helfer
# ============================================================================


@pytest.fixture()
def fresh_db(tmp_path: pytest.TempPath) -> Iterator[FinanceDB]:
    return FinanceDB(str(tmp_path / "finance_prior_balance.db"))


def _insert(
    db: FinanceDB,
    pdf_hash: str,
    iban: str,
    *,
    period_start: Optional[str] = "2026-01-01",
    period_end: Optional[str] = "2026-01-31",
    opening_balance: Optional[float] = None,
    closing_balance: Optional[float] = None,
) -> tuple[int, int]:
    """Persistiert ein Statement (ohne Tx) und liefert ``(account_id, statement_id)``.

    Leere Transaktionsliste => kein Such-Docs-Refresh, kein Embedding-Load.
    """
    _, account_id, statement_id, inserted, duplicates = db.persist_statement_import(
        bank_name="Prior Bank",
        bank_bic="PRIORDEFF",
        bank_country_code="DE",
        iban=iban,
        account_holder="Prior User",
        currency="EUR",
        account_type="checking",
        source_pdf_hash=pdf_hash,
        source_filename=f"{pdf_hash}.pdf",
        period_start=period_start,
        period_end=period_end,
        opening_balance=opening_balance,
        closing_balance=closing_balance,
        transactions=[],
    )
    assert (inserted, duplicates) == (0, 0)
    return account_id, statement_id


# ============================================================================
# find_account_id_by_iban
# ============================================================================


class TestFindAccountIdByIban:
    """``find_account_id_by_iban``: Read-only Account-Lookup."""

    def test_returns_account_id_for_known_iban(self, fresh_db: FinanceDB) -> None:
        account_id, _ = _insert(fresh_db, "find-known", IBAN_A)
        assert fresh_db.find_account_id_by_iban(IBAN_A) == account_id

    def test_normalizes_iban_case_and_spaces(self, fresh_db: FinanceDB) -> None:
        account_id, _ = _insert(fresh_db, "find-norm", IBAN_A)
        # Kleinschreibung -> normalisiert zu Gross
        assert fresh_db.find_account_id_by_iban(IBAN_A.lower()) == account_id
        # Leerstellen -> entfernt
        spaced = " ".join(IBAN_A[i : i + 4] for i in range(0, len(IBAN_A), 4))
        assert fresh_db.find_account_id_by_iban(spaced) == account_id

    def test_unknown_iban_returns_none(self, fresh_db: FinanceDB) -> None:
        _insert(fresh_db, "find-other", IBAN_A)
        assert fresh_db.find_account_id_by_iban(IBAN_B) is None

    def test_blank_iban_returns_none(self, fresh_db: FinanceDB) -> None:
        _insert(fresh_db, "find-blank", IBAN_A)
        assert fresh_db.find_account_id_by_iban("") is None
        assert fresh_db.find_account_id_by_iban("   ") is None
        # Defensiv: None wird als leer behandelt (kein Crash)
        assert fresh_db.find_account_id_by_iban(None) is None  # type: ignore[arg-type]


# ============================================================================
# get_prior_closing_balance
# ============================================================================


class TestGetPriorClosingBalance:
    """``get_prior_closing_balance``: nächster Vorstatement-Schlussaldo."""

    def test_returns_prior_statement_closing(self, fresh_db: FinanceDB) -> None:
        account_id, _ = _insert(
            fresh_db,
            "prior-1",
            IBAN_A,
            period_start="2026-01-01",
            period_end="2026-01-31",
            closing_balance=1000.0,
        )
        assert fresh_db.get_prior_closing_balance(account_id, "2026-02-01") == 1000.0

    def test_returns_nearest_prior_by_period_end(self, fresh_db: FinanceDB) -> None:
        account_id, _ = _insert(
            fresh_db,
            "prior-old",
            IBAN_A,
            period_start="2025-12-01",
            period_end="2025-12-31",
            closing_balance=500.0,
        )
        # Nächstes (späteres) Vorstatement
        _insert(
            fresh_db,
            "prior-new",
            IBAN_A,
            period_start="2026-01-01",
            period_end="2026-01-31",
            closing_balance=1000.0,
        )
        # before = 2026-02-01 -> nächstes ist 2026-01-31 (1000.0), nicht 2025-12-31 (500.0)
        assert fresh_db.get_prior_closing_balance(account_id, "2026-02-01") == 1000.0

    def test_strictly_before_period_end(self, fresh_db: FinanceDB) -> None:
        # period_end == before ist NICHT "< before" -> wird ignoriert
        account_id, _ = _insert(
            fresh_db,
            "prior-same",
            IBAN_A,
            period_start="2026-01-01",
            period_end="2026-02-01",
            closing_balance=1000.0,
        )
        assert fresh_db.get_prior_closing_balance(account_id, "2026-02-01") is None

    def test_no_prior_returns_none(self, fresh_db: FinanceDB) -> None:
        account_id, _ = _insert(
            fresh_db,
            "prior-none",
            IBAN_A,
            period_start="2026-01-01",
            period_end="2026-01-31",
            closing_balance=1000.0,
        )
        # before liegt VOR dem einzigen Statement -> kein Vorstatement
        assert fresh_db.get_prior_closing_balance(account_id, "2025-12-01") is None

    def test_none_or_blank_before_period_returns_none(self, fresh_db: FinanceDB) -> None:
        account_id, _ = _insert(
            fresh_db,
            "prior-null-before",
            IBAN_A,
            period_start="2025-12-01",
            period_end="2025-12-31",
            closing_balance=1000.0,
        )
        assert fresh_db.get_prior_closing_balance(account_id, None) is None
        assert fresh_db.get_prior_closing_balance(account_id, "") is None
        assert fresh_db.get_prior_closing_balance(account_id, "   ") is None

    def test_null_closing_is_not_a_prior(self, fresh_db: FinanceDB) -> None:
        # Statement mit NULL-Endsaldo zählt nicht als Vorstatement
        account_id, _ = _insert(
            fresh_db,
            "prior-null-close",
            IBAN_A,
            period_start="2025-12-01",
            period_end="2025-12-31",
            closing_balance=None,
        )
        assert fresh_db.get_prior_closing_balance(account_id, "2026-01-01") is None
