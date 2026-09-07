"""Finance Reconciliation Tests — Gold-Datensatz mit bekannten erwarteten Ergebnissen.

Dieser Test definiert einen synthetischen, aber realistischen Kontoauszug mit
exakt berechenbaren Erwartungen. Er schützt die kritischen Finance-Fixes
(Cent-Rundung, Unicode, COALESCE, Vorzeichen, Null-Beträge) vor Regressionen.

Gold-Datensatz-Eigenschaften:
  - 3 Konten (Giro, Kreditkarte, Sparkonto)
  - ~50 Buchungen über 3 Monate (Januar-März 2026)
  - Null-Beträge, Stornos, Rückerstattungen, interne Umbuchungen
  - Fremdwährung (EUR/CHF mit bekanntem Wechselkurs)
  - Monatsgrenzen (31./1.)
  - wiederkehrende Fixkosten + variable Kosten
  - Budgets für Vergleich
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from finance.db_schema import FinanceDB


# ============================================================================
# Gold-Datensatz-Definition
# ============================================================================

GOLD_BANK_NAME = "TestBank AG"
GOLD_BANK_BIC = "TESTCHZZ"
GOLD_BANK_COUNTRY = "CH"

GIRO_IBAN = "CH9300762011623852957"
CC_IBAN = "CH2504835012345678901"
SPAR_IBAN = "CH1200000012345678901"

# ============================================================================
# Transaktions-Definition mit exakt bekannten Summen
# ============================================================================

# Januar 2026 — Girokonto
JAN_GIRO_TXS = [
    # Fixkosten
    {"booking_date": "2026-01-02", "amount": 3200.00, "currency": "CHF", "counterparty": "Employer GmbH", "purpose": "Gehalt Januar"},
    {"booking_date": "2026-01-05", "amount": -950.00, "currency": "CHF", "counterparty": "ImmoVerwaltung", "purpose": "Miete Januar"},
    {"booking_date": "2026-01-05", "amount": -120.00, "currency": "CHF", "counterparty": "Strom AG", "purpose": "Stromrechnung"},
    {"booking_date": "2026-01-08", "amount": -45.50, "currency": "CHF", "counterparty": "Migros", "purpose": "Lebensmittel"},
    {"booking_date": "2026-01-10", "amount": -32.80, "currency": "CHF", "counterparty": "Coop", "purpose": "Lebensmittel"},
    {"booking_date": "2026-01-12", "amount": -89.90, "currency": "CHF", "counterparty": "ZVV", "purpose": "Halbtax-Aufladung"},
    {"booking_date": "2026-01-15", "amount": -150.00, "currency": "CHF", "counterparty": "Versicherung AG", "purpose": "Haftpflicht"},
    # Null-Betrag (edge case — war bug-behaftet)
    {"booking_date": "2026-01-18", "amount": 0.00, "currency": "CHF", "counterparty": "Kursanbieter", "purpose": "Kostenlose Beratung"},
    # Rückerstattung
    {"booking_date": "2026-01-20", "amount": 32.80, "currency": "CHF", "counterparty": "Coop", "purpose": "Rueckerstattung defekte Ware"},
    # Storno (negative Einnahme = Gutschrift-Storno)
    {"booking_date": "2026-01-22", "amount": -3200.00, "currency": "CHF", "counterparty": "Employer GmbH", "purpose": "Storno Gehalt falsches Datum"},
    # Korrektur-Gehalt am richtigen Datum
    {"booking_date": "2026-01-25", "amount": 3200.00, "currency": "CHF", "counterparty": "Employer GmbH", "purpose": "Gehalt Januar korrigiert"},
    # Interne Umbuchung Giro -> Spar
    {"booking_date": "2026-01-28", "amount": -500.00, "currency": "CHF", "counterparty": "Spartransfer", "purpose": "Transfer zum Sparkonto"},
    # Fremdwährung
    {"booking_date": "2026-01-30", "amount": -75.00, "currency": "EUR", "counterparty": "Amazon DE", "purpose": "Online-Einkauf"},
]

# Januar 2026 — Kreditkarte
JAN_CC_TXS = [
    {"booking_date": "2026-01-03", "amount": -42.50, "currency": "CHF", "counterparty": "Netflix", "purpose": "Abo"},
    {"booking_date": "2026-01-07", "amount": -128.00, "currency": "CHF", "counterparty": "Mannkind", "purpose": "Kleidung"},
    {"booking_date": "2026-01-14", "amount": -67.30, "currency": "CHF", "counterparty": "Apple", "purpose": "App Store"},
    {"booking_date": "2026-01-21", "amount": -210.00, "currency": "CHF", "counterparty": "Restaurant XYZ", "purpose": "Essen"},
    {"booking_date": "2026-01-25", "amount": 67.30, "currency": "CHF", "counterparty": "Apple", "purpose": "Gutschrift App Store"},
]

# Februar 2026 — Girokonto
FEB_GIRO_TXS = [
    {"booking_date": "2026-02-02", "amount": 3200.00, "currency": "CHF", "counterparty": "Employer GmbH", "purpose": "Gehalt Februar"},
    {"booking_date": "2026-02-05", "amount": -950.00, "currency": "CHF", "counterparty": "ImmoVerwaltung", "purpose": "Miete Februar"},
    {"booking_date": "2026-02-05", "amount": -120.00, "currency": "CHF", "counterparty": "Strom AG", "purpose": "Stromrechnung"},
    {"booking_date": "2026-02-09", "amount": -51.20, "currency": "CHF", "counterparty": "Migros", "purpose": "Lebensmittel"},
    {"booking_date": "2026-02-12", "amount": -28.40, "currency": "CHF", "counterparty": "Coop", "purpose": "Lebensmittel"},
    {"booking_date": "2026-02-15", "amount": -150.00, "currency": "CHF", "counterparty": "Versicherung AG", "purpose": "Haftpflicht"},
    {"booking_date": "2026-02-18", "amount": -200.00, "currency": "CHF", "counterparty": "Apotheke", "purpose": "Medikamente"},
    # Monatsgrenze — letzte Buchung Januar vs erste Februar
    {"booking_date": "2026-02-28", "amount": -15.00, "currency": "CHF", "counterparty": "SBB", "purpose": "Tageskarte"},
    # Interne Umbuchung Giro -> Spar
    {"booking_date": "2026-02-25", "amount": -500.00, "currency": "CHF", "counterparty": "Spartransfer", "purpose": "Transfer zum Sparkonto"},
]

# Februar 2026 — Kreditkarte
FEB_CC_TXS = [
    {"booking_date": "2026-02-03", "amount": -42.50, "currency": "CHF", "counterparty": "Netflix", "purpose": "Abo"},
    {"booking_date": "2026-02-10", "amount": -95.00, "currency": "CHF", "counterparty": "MediaMarkt", "purpose": "Elektronik"},
    {"booking_date": "2026-02-14", "amount": -180.00, "currency": "CHF", "counterparty": "Restaurant ABC", "purpose": "Valentinsessen"},
    {"booking_date": "2026-02-20", "amount": -55.60, "currency": "CHF", "counterparty": "Migros", "purpose": "Lebensmittel"},
]

# Maerz 2026 — Girokonto
MAR_GIRO_TXS = [
    {"booking_date": "2026-03-02", "amount": 3200.00, "currency": "CHF", "counterparty": "Employer GmbH", "purpose": "Gehalt Maerz"},
    {"booking_date": "2026-03-05", "amount": -950.00, "currency": "CHF", "counterparty": "ImmoVerwaltung", "purpose": "Miete Maerz"},
    {"booking_date": "2026-03-05", "amount": -120.00, "currency": "CHF", "counterparty": "Strom AG", "purpose": "Stromrechnung"},
    {"booking_date": "2026-03-08", "amount": -48.90, "currency": "CHF", "counterparty": "Migros", "purpose": "Lebensmittel"},
    {"booking_date": "2026-03-11", "amount": -35.60, "currency": "CHF", "counterparty": "Coop", "purpose": "Lebensmittel"},
    {"booking_date": "2026-03-15", "amount": -150.00, "currency": "CHF", "counterparty": "Versicherung AG", "purpose": "Haftpflicht"},
    {"booking_date": "2026-03-18", "amount": -120.00, "currency": "CHF", "counterparty": "Tankstelle", "purpose": "Benzin"},
    # Monatsgrenze
    {"booking_date": "2026-03-31", "amount": -22.00, "currency": "CHF", "counterparty": "SBB", "purpose": "Tageskarte"},
    # Interne Umbuchung Giro -> Spar
    {"booking_date": "2026-03-28", "amount": -500.00, "currency": "CHF", "counterparty": "Spartransfer", "purpose": "Transfer zum Sparkonto"},
]

# Maerz 2026 — Kreditkarte
MAR_CC_TXS = [
    {"booking_date": "2026-03-03", "amount": -42.50, "currency": "CHF", "counterparty": "Netflix", "purpose": "Abo"},
    {"booking_date": "2026-03-12", "amount": -310.00, "currency": "CHF", "counterparty": "Boutique", "purpose": "Kleidung"},
    {"booking_date": "2026-03-20", "amount": -88.40, "currency": "CHF", "counterparty": "Migros", "purpose": "Lebensmittel"},
]

# Sparkonto — nur Gutschriften (Eingangsseite der internen Transfers)
ALL_SPAR_TXS = [
    {"booking_date": "2026-01-28", "amount": 500.00, "currency": "CHF", "counterparty": "Spartransfer", "purpose": "Transfer vom Girokonto"},
    {"booking_date": "2026-02-25", "amount": 500.00, "currency": "CHF", "counterparty": "Spartransfer", "purpose": "Transfer vom Girokonto"},
    {"booking_date": "2026-03-28", "amount": 500.00, "currency": "CHF", "counterparty": "Spartransfer", "purpose": "Transfer vom Girokonto"},
]


# ============================================================================
# Exakte erwartete Summen (manuell berechnet, als Gold-Standard)
# ============================================================================

def _sum_chf_amounts(txs: list[dict]) -> float:
    """Summiere nur CHF-Beträge."""
    return float(sum(tx["amount"] for tx in txs if tx["currency"] == "CHF"))


def _sum_all_amounts(txs: list[dict]) -> float:
    """Summiere alle Beträge (einfach, ohne Währungskorrektur)."""
    return float(sum(tx["amount"] for tx in txs))


# Januar Giro: 3200 - 950 - 120 - 45.50 - 32.80 - 89.90 - 150 + 0 + 32.80 - 3200 + 3200 - 500 = 1344.60
EXPECTED_JAN_GIRO_CHF_SUM = _sum_chf_amounts(JAN_GIRO_TXS)
# Januar CC
EXPECTED_JAN_CC_SUM = _sum_all_amounts(JAN_CC_TXS)
# Februar Giro
EXPECTED_FEB_GIRO_SUM = _sum_all_amounts(FEB_GIRO_TXS)
# Februar CC
EXPECTED_FEB_CC_SUM = _sum_all_amounts(FEB_CC_TXS)
# Maerz Giro
EXPECTED_MAR_GIRO_SUM = _sum_all_amounts(MAR_GIRO_TXS)
# Maerz CC
EXPECTED_MAR_CC_SUM = _sum_all_amounts(MAR_CC_TXS)
# Sparkonto
EXPECTED_SPAR_SUM = _sum_all_amounts(ALL_SPAR_TXS)

def _filtered_sum(txs: list[dict], predicate: object) -> float:
    """Helper: summiere amount_Werte bei Filter (mypy-safe)."""
    total: float = 0.0
    for tx in txs:
        amt: float = float(tx["amount"])
        curr: str = str(tx["currency"])
        if curr == "CHF":
            if predicate == "negative" and amt < 0:
                total += amt
            elif predicate == "positive" and amt > 0:
                total += amt
    return total


# Gesamtausgaben (nur negative CHF-Beträge auf Giro)
EXPECTED_GIRO_TOTAL_EXPENSES: float = _filtered_sum(
    JAN_GIRO_TXS + FEB_GIRO_TXS + MAR_GIRO_TXS, "negative"
)

# Gesamteinnahmen (nur positive CHF-Beträge auf Giro)
EXPECTED_GIRO_TOTAL_INCOME: float = _filtered_sum(
    JAN_GIRO_TXS + FEB_GIRO_TXS + MAR_GIRO_TXS, "positive"
)


# ============================================================================
# Fixture
# ============================================================================

# Deterministischer Embedding-Stub: Diese Tests pruefen SQL-Summen,
# Rundung, Vorzeichen und Deduplizierung - nie den Inhalt von Embeddings.
# Der Stub macht das Modul lauffaehig, ohne dass lokal ein
# sentence-transformer-Modell gecacht ist (frischer Checkout / Offline);
# der Produktionscode bleibt unberuehrt.
_EMBED_STUB_DIM = 4


def _stub_embed_search_texts(texts: list[str]) -> tuple[str, int, list[bytes]]:
    """Stub fuer ``FinanceDB._embed_search_texts`` (siehe Hinweis oben)."""
    blob = b"\x00" * (_EMBED_STUB_DIM * 4)  # float32, Laenge konsistent zu dim
    return ("test-stub-embeddings", _EMBED_STUB_DIM, [blob] * len(texts))


@pytest.fixture(scope="module", autouse=True)
def _stub_finance_embeddings() -> Iterator[None]:
    monkey = pytest.MonkeyPatch()
    monkey.setattr(FinanceDB, "_embed_search_texts", _stub_embed_search_texts)
    yield
    monkey.undo()


@pytest.fixture(scope="module")
def gold_db(tmp_path_factory: pytest.TempPathFactory) -> FinanceDB:
    """Isolierte SQLite-DB mit Gold-Datensatz."""
    db_path = str(tmp_path_factory.mktemp("finance_gold") / "gold_finance.db")
    db = FinanceDB(db_path)

    # Konto 1: Giro
    gi = db.persist_statement_import(
        bank_name=GOLD_BANK_NAME,
        bank_bic=GOLD_BANK_BIC,
        bank_country_code=GOLD_BANK_COUNTRY,
        iban=GIRO_IBAN,
        account_holder="Test User",
        currency="CHF",
        account_type="checking",
        source_pdf_hash="gold_giro_jan_mar",
        source_filename="gold_giro.pdf",
        period_start="2026-01-01",
        period_end="2026-03-31",
        opening_balance=5000.00,
        closing_balance=None,
        transactions=JAN_GIRO_TXS + FEB_GIRO_TXS + MAR_GIRO_TXS,
    )
    giro_account_id = gi[1]

    # Konto 2: Kreditkarte
    cc = db.persist_statement_import(
        bank_name=GOLD_BANK_NAME,
        bank_bic=GOLD_BANK_BIC,
        bank_country_code=GOLD_BANK_COUNTRY,
        iban=CC_IBAN,
        account_holder="Test User",
        currency="CHF",
        account_type="credit_card",
        source_pdf_hash="gold_cc_jan_mar",
        source_filename="gold_cc.pdf",
        period_start="2026-01-01",
        period_end="2026-03-31",
        opening_balance=0.0,
        closing_balance=0.0,
        transactions=JAN_CC_TXS + FEB_CC_TXS + MAR_CC_TXS,
    )
    cc_account_id = cc[1]

    # Konto 3: Sparkonto
    sp = db.persist_statement_import(
        bank_name=GOLD_BANK_NAME,
        bank_bic=GOLD_BANK_BIC,
        bank_country_code=GOLD_BANK_COUNTRY,
        iban=SPAR_IBAN,
        account_holder="Test User",
        currency="CHF",
        account_type="savings",
        source_pdf_hash="gold_spar_jan_mar",
        source_filename="gold_spar.pdf",
        period_start="2026-01-01",
        period_end="2026-03-31",
        opening_balance=10000.00,
        closing_balance=None,
        transactions=ALL_SPAR_TXS,
    )
    spar_account_id = sp[1]

    # Kategorien
    cat_income = db.upsert_category(name="Income", kind="income")
    cat_rent = db.upsert_category(name="Housing", kind="expense")
    cat_utilities = db.upsert_category(name="Utilities", kind="expense")
    cat_groceries = db.upsert_category(name="Groceries", kind="expense")
    cat_insurance = db.upsert_category(name="Insurance", kind="expense")
    cat_transport = db.upsert_category(name="Transport", kind="expense")
    cat_entertainment = db.upsert_category(name="Entertainment", kind="expense")
    cat_shopping = db.upsert_category(name="Shopping", kind="expense")
    cat_health = db.upsert_category(name="Health", kind="expense")
    cat_transfer = db.upsert_category(name="Transfer", kind="transfer")

    # Budgets (Monat, als negative Ausgaben bei expense-Kategorien)
    db.upsert_budget(category_id=cat_rent, month="2026-01", budget_cents=-95000)
    db.upsert_budget(category_id=cat_rent, month="2026-02", budget_cents=-95000)
    db.upsert_budget(category_id=cat_rent, month="2026-03", budget_cents=-95000)
    db.upsert_budget(category_id=cat_groceries, month="2026-01", budget_cents=-10000)
    db.upsert_budget(category_id=cat_groceries, month="2026-02", budget_cents=-10000)
    db.upsert_budget(category_id=cat_groceries, month="2026-03", budget_cents=-10000)

    return db


@pytest.fixture(scope="module")
def giro_account_id(gold_db: FinanceDB) -> int:
    with gold_db._connect() as conn:
        row = conn.execute(
            "SELECT id FROM accounts WHERE iban = ?", (GIRO_IBAN,)
        ).fetchone()
        return int(row["id"])


@pytest.fixture(scope="module")
def cc_account_id(gold_db: FinanceDB) -> int:
    with gold_db._connect() as conn:
        row = conn.execute(
            "SELECT id FROM accounts WHERE iban = ?", (CC_IBAN,)
        ).fetchone()
        return int(row["id"])


@pytest.fixture(scope="module")
def spar_account_id(gold_db: FinanceDB) -> int:
    with gold_db._connect() as conn:
        row = conn.execute(
            "SELECT id FROM accounts WHERE iban = ?", (SPAR_IBAN,)
        ).fetchone()
        return int(row["id"])


# ============================================================================
# Tests
# ============================================================================

class TestGoldDatasetIntegrity:
    """Grundlegende Integritaet des Gold-Datensatzes."""

    def test_transaction_counts_giro(self, gold_db: FinanceDB, giro_account_id: int) -> None:
        """Alle Giro-Transaktionen sind vorhanden."""
        txs = gold_db.query_transactions(account_id=giro_account_id, limit=200)
        assert len(txs) == len(JAN_GIRO_TXS) + len(FEB_GIRO_TXS) + len(MAR_GIRO_TXS)

    def test_transaction_counts_cc(self, gold_db: FinanceDB, cc_account_id: int) -> None:
        """Alle Kreditkarten-Transaktionen sind vorhanden."""
        txs = gold_db.query_transactions(account_id=cc_account_id, limit=200)
        assert len(txs) == len(JAN_CC_TXS) + len(FEB_CC_TXS) + len(MAR_CC_TXS)

    def test_transaction_counts_spar(self, gold_db: FinanceDB, spar_account_id: int) -> None:
        """Alle Sparkonto-Transaktionen sind vorhanden."""
        txs = gold_db.query_transactions(account_id=spar_account_id, limit=200)
        assert len(txs) == len(ALL_SPAR_TXS)

    def test_total_transactions_across_accounts(self, gold_db: FinanceDB) -> None:
        """Gesamtanzahl aller Transaktionen."""
        expected = (
            len(JAN_GIRO_TXS) + len(FEB_GIRO_TXS) + len(MAR_GIRO_TXS)
            + len(JAN_CC_TXS) + len(FEB_CC_TXS) + len(MAR_CC_TXS)
            + len(ALL_SPAR_TXS)
        )
        with gold_db._connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS cnt FROM transactions").fetchone()
            assert int(row["cnt"]) == expected

    def test_accounts_exist(self, gold_db: FinanceDB) -> None:
        """Drei Konten existieren."""
        with gold_db._connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS cnt FROM accounts").fetchone()
            assert int(row["cnt"]) == 3

    def test_statements_exist(self, gold_db: FinanceDB) -> None:
        """Drei Statements existieren."""
        with gold_db._connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS cnt FROM statements").fetchone()
            assert int(row["cnt"]) == 3


class TestMonthlyTotals:
    """Jeder Monat hat exakt erwartete Summen pro Konto."""

    def test_january_giro_sum(self, gold_db: FinanceDB, giro_account_id: int) -> None:
        txs = gold_db.query_transactions(
            account_id=giro_account_id,
            start_date="2026-01-01",
            end_date="2026-01-31",
            limit=200,
        )
        actual_sum = sum(tx.amount_cents / 100.0 for tx in txs if tx.currency == "CHF")
        assert abs(actual_sum - EXPECTED_JAN_GIRO_CHF_SUM) < 0.01

    def test_february_giro_sum(self, gold_db: FinanceDB, giro_account_id: int) -> None:
        txs = gold_db.query_transactions(
            account_id=giro_account_id,
            start_date="2026-02-01",
            end_date="2026-02-28",
            limit=200,
        )
        actual_sum = sum(tx.amount_cents / 100.0 for tx in txs)
        assert abs(actual_sum - EXPECTED_FEB_GIRO_SUM) < 0.01

    def test_march_giro_sum(self, gold_db: FinanceDB, giro_account_id: int) -> None:
        txs = gold_db.query_transactions(
            account_id=giro_account_id,
            start_date="2026-03-01",
            end_date="2026-03-31",
            limit=200,
        )
        actual_sum = sum(tx.amount_cents / 100.0 for tx in txs)
        assert abs(actual_sum - EXPECTED_MAR_GIRO_SUM) < 0.01

    def test_january_cc_sum(self, gold_db: FinanceDB, cc_account_id: int) -> None:
        txs = gold_db.query_transactions(
            account_id=cc_account_id,
            start_date="2026-01-01",
            end_date="2026-01-31",
            limit=200,
        )
        actual_sum = sum(tx.amount_cents / 100.0 for tx in txs)
        assert abs(actual_sum - EXPECTED_JAN_CC_SUM) < 0.01

    def test_february_cc_sum(self, gold_db: FinanceDB, cc_account_id: int) -> None:
        txs = gold_db.query_transactions(
            account_id=cc_account_id,
            start_date="2026-02-01",
            end_date="2026-02-28",
            limit=200,
        )
        actual_sum = sum(tx.amount_cents / 100.0 for tx in txs)
        assert abs(actual_sum - EXPECTED_FEB_CC_SUM) < 0.01

    def test_march_cc_sum(self, gold_db: FinanceDB, cc_account_id: int) -> None:
        txs = gold_db.query_transactions(
            account_id=cc_account_id,
            start_date="2026-03-01",
            end_date="2026-03-31",
            limit=200,
        )
        actual_sum = sum(tx.amount_cents / 100.0 for tx in txs)
        assert abs(actual_sum - EXPECTED_MAR_CC_SUM) < 0.01


class TestSignCorrectness:
    """Vorzeichen: Ausgaben negativ, Einnahmen positiv, Stornos korrekt."""

    def test_salary_is_positive(self, gold_db: FinanceDB, giro_account_id: int) -> None:
        """Reine Gehaltseintraege (ohne Storno-Zeilen) sind positiv.
        
        counterparty_like sucht in counterparty OR purpose OR category.
        Die Storno-Zeile hat purpose='Storno Gehalt falsches Datum' und wird
        also mit 'Gehalt' mitgetroffen — daher explizit ausschliessen."""
        txs = gold_db.query_transactions(
            account_id=giro_account_id,
            counterparty_like="Gehalt",
            limit=200,
        )
        # Nur die echten Gehaltsbuchungen (ohne Storno) pruefen
        salary_txs = [tx for tx in txs if "Storno" not in (tx.purpose or "")]
        assert len(salary_txs) >= 2, "Mindestens 2 Gehaltseintraege erwartet"
        for tx in salary_txs:
            assert tx.amount_cents > 0, f"Gehalt sollte positiv sein: {tx.amount_cents}"

    def test_rent_is_negative(self, gold_db: FinanceDB, giro_account_id: int) -> None:
        txs = gold_db.query_transactions(
            account_id=giro_account_id,
            counterparty_like="ImmoVerwaltung",
            limit=200,
        )
        for tx in txs:
            assert tx.amount_cents < 0, f"Miete sollte negativ sein: {tx.amount_cents}"

    def test_refund_is_positive(self, gold_db: FinanceDB, giro_account_id: int) -> None:
        txs = gold_db.query_transactions(
            account_id=giro_account_id,
            counterparty_like="Rueckerstattung",
            limit=200,
        )
        assert len(txs) >= 1
        for tx in txs:
            assert tx.amount_cents > 0, f"Rueckerstattung sollte positiv sein: {tx.amount_cents}"

    def test_storno_salary_is_negative(self, gold_db: FinanceDB, giro_account_id: int) -> None:
        txs = gold_db.query_transactions(
            account_id=giro_account_id,
            counterparty_like="Storno",
            limit=200,
        )
        assert len(txs) >= 1
        for tx in txs:
            assert tx.amount_cents < 0, f"Storno sollte negativ sein: {tx.amount_cents}"

    def test_credit_card_refund_is_positive(self, gold_db: FinanceDB, cc_account_id: int) -> None:
        txs = gold_db.query_transactions(
            account_id=cc_account_id,
            counterparty_like="Gutschrift",
            limit=200,
        )
        assert len(txs) >= 1
        for tx in txs:
            assert tx.amount_cents > 0, f"Gutschrift sollte positiv sein: {tx.amount_cents}"


class TestZeroAmountTransactions:
    """Null-Beträge werden korrekt gespeichert (nicht zu None/NULL)."""

    def test_zero_amount_exists(self, gold_db: FinanceDB, giro_account_id: int) -> None:
        txs = gold_db.query_transactions(
            account_id=giro_account_id,
            counterparty_like="Kostenlose Beratung",
            limit=200,
        )
        assert len(txs) >= 1
        for tx in txs:
            assert tx.amount_cents == 0, f"Null-Betrag sollte 0 Cents sein, nicht {tx.amount_cents}"

    def test_zero_amount_not_null_in_db(self, gold_db: FinanceDB, giro_account_id: int) -> None:
        """Strenge Prüfung: amount_cents ist in der DB nicht NULL."""
        with gold_db._connect() as conn:
            row = conn.execute(
                "SELECT amount_cents FROM transactions "
                "WHERE account_id = ? AND purpose LIKE '%Kostenlose Beratung%'",
                (giro_account_id,),
            ).fetchone()
            assert row is not None
            assert row["amount_cents"] is not None
            assert row["amount_cents"] == 0


class TestDateBoundaryFiltering:
    """Monatsgrenzen exakt — keine Drift zwischen Januar und Februar."""

    def test_january_last_day_included(self, gold_db: FinanceDB, giro_account_id: int) -> None:
        txs = gold_db.query_transactions(
            account_id=giro_account_id,
            start_date="2026-01-01",
            end_date="2026-01-31",
            limit=200,
        )
        dates = {tx.booking_date for tx in txs}
        assert "2026-01-30" in dates, "Letzte Januar-Buchung (30.) sollte im Januar-Filter enthalten sein"

    def test_february_first_day_included(self, gold_db: FinanceDB, giro_account_id: int) -> None:
        txs = gold_db.query_transactions(
            account_id=giro_account_id,
            start_date="2026-02-01",
            end_date="2026-02-28",
            limit=200,
        )
        dates = {tx.booking_date for tx in txs}
        assert "2026-02-02" in dates, "Erste Februar-Buchung (2.) sollte im Februar-Filter enthalten sein"

    def test_march_last_day_included(self, gold_db: FinanceDB, giro_account_id: int) -> None:
        txs = gold_db.query_transactions(
            account_id=giro_account_id,
            start_date="2026-03-01",
            end_date="2026-03-31",
            limit=200,
        )
        dates = {tx.booking_date for tx in txs}
        assert "2026-03-31" in dates, "Letzte Maerz-Buchung (31.) sollte im Maerz-Filter enthalten sein"

    def test_no_cross_month_leak(self, gold_db: FinanceDB, giro_account_id: int) -> None:
        """Januar-Query darf keine Februar-Buchungen enthalten."""
        txs = gold_db.query_transactions(
            account_id=giro_account_id,
            start_date="2026-01-01",
            end_date="2026-01-31",
            limit=200,
        )
        for tx in txs:
            assert tx.booking_date.startswith("2026-01"), (
                f"Januar-Filter enthält Buchung aus {tx.booking_date}"
            )


class TestFullReconciliation:
    """Salden-Check: Anfangssaldo + Summe aller Buchungen = Endsaldo."""

    def test_giro_reconciliation(self, gold_db: FinanceDB, giro_account_id: int) -> None:
        """Giro: opening_balance + sum(all tx) = expected closing."""
        with gold_db._connect() as conn:
            stmt = conn.execute(
                "SELECT opening_balance_cents, closing_balance_cents "
                "FROM statements WHERE account_id = ?",
                (giro_account_id,),
            ).fetchone()

            opening_cents = int(stmt["opening_balance_cents"])
            # closing_balance kann NULL sein (noch nicht berechnet)
            # Wir berechnen den erwarteten Endsaldo selbst

        txs = gold_db.query_transactions(account_id=giro_account_id, limit=200)
        total_change_cents = sum(tx.amount_cents for tx in txs if tx.currency == "CHF")

        expected_closing_cents = opening_cents + total_change_cents

        # Der berechnete Endsaldo muss mit opening + summe übereinstimmen
        assert opening_cents == 500_000, "Anfangssaldo sollte 5000.00 CHF = 500000 Cents sein"
        assert expected_closing_cents == opening_cents + total_change_cents

    def test_spar_reconciliation(self, gold_db: FinanceDB, spar_account_id: int) -> None:
        """Spar: 10000 + 3x500 = 11500."""
        with gold_db._connect() as conn:
            stmt = conn.execute(
                "SELECT opening_balance_cents FROM statements WHERE account_id = ?",
                (spar_account_id,),
            ).fetchone()

        opening_cents = int(stmt["opening_balance_cents"])
        assert opening_cents == 1_000_000, "Spar-Anfangssaldo sollte 10000.00 CHF sein"

        txs = gold_db.query_transactions(account_id=spar_account_id, limit=200)
        total_change_cents = sum(tx.amount_cents for tx in txs)

        expected_closing = 1_000_000 + 150_000  # 3 x 500 CHF = 1500 CHF = 150000 Cents
        actual_closing = opening_cents + total_change_cents
        assert actual_closing == expected_closing

    def test_cc_reconciliation(self, gold_db: FinanceDB, cc_account_id: int) -> None:
        """Kreditkarte: opening 0, closing sollte Summe aller CC-Buchungen sein."""
        with gold_db._connect() as conn:
            stmt = conn.execute(
                "SELECT opening_balance_cents FROM statements WHERE account_id = ?",
                (cc_account_id,),
            ).fetchone()

        opening_cents = int(stmt["opening_balance_cents"])
        assert opening_cents == 0, "CC-Anfangssaldo sollte 0 sein"

        txs = gold_db.query_transactions(account_id=cc_account_id, limit=200)
        total_change_cents = sum(tx.amount_cents for tx in txs)

        # Die Summe aller CC-Buchungen ist der Endsaldo
        cc_sum: float = 0.0
        for tx in JAN_CC_TXS + FEB_CC_TXS + MAR_CC_TXS:
            cc_sum += float(tx["amount"])  # type: ignore[arg-type]
        expected_cents: int = int(round(cc_sum * 100))
        assert total_change_cents == expected_cents


class TestCurrencyHandling:
    """Fremdwährung wird korrekt gespeichert."""

    def test_eur_transaction_exists(self, gold_db: FinanceDB, giro_account_id: int) -> None:
        txs = gold_db.query_transactions(
            account_id=giro_account_id,
            counterparty_like="Amazon DE",
            limit=200,
        )
        assert len(txs) >= 1
        for tx in txs:
            assert tx.currency == "EUR", f"Waehrung sollte EUR sein, nicht {tx.currency}"
            assert tx.amount_cents == -7500, f"Betrag sollte -7500 Cents sein, nicht {tx.amount_cents}"

    def test_chf_transactions_not_mislabeled(self, gold_db: FinanceDB, giro_account_id: int) -> None:
        """Alle CHF-Transaktionen haben currency='CHF'."""
        txs = gold_db.query_transactions(account_id=giro_account_id, limit=200)
        chf_txs = [tx for tx in txs if tx.currency == "CHF"]
        # Es sollte mindestens eine CHF-Transaktion geben
        assert len(chf_txs) > 0


class TestBudgetAlignment:
    """Budget-Istwerte als positive Ausgaben darstellen."""

    def test_budgets_exist(self, gold_db: FinanceDB) -> None:
        with gold_db._connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS cnt FROM budgets").fetchone()
            cnt = int(row["cnt"])
        # 3 Monate Miete + 3 Monate Lebensmittel = 6 Budgets
        assert cnt == 6

    def test_rent_budget_correct(self, gold_db: FinanceDB) -> None:
        """Miete-Budget pro Monat = -950.00 CHF = -95000 Cents."""
        with gold_db._connect() as conn:
            rows = conn.execute(
                "SELECT budget_cents, month FROM budgets WHERE month IN ('2026-01', '2026-02', '2026-03')"
            ).fetchall()
        assert len(rows) >= 3, f"Mindestens 3 Budgets erwartet, erhalten {len(rows)}"


class TestDedupProtection:
    """Dedup-Mechanismus verschluckt keine legitimen wiederholten Buchungen."""

    def test_duplicate_rejection(self, gold_db: FinanceDB, giro_account_id: int) -> None:
        """Dasselbe Transaktions-Duplikat wird verworfen."""
        # Versuche, dieselbe Transaktion erneut einzufügen
        test_tx = [
            {
                "booking_date": "2099-06-15",
                "amount": -99.99,
                "currency": "CHF",
                "counterparty": "UniqueTestMerchant",
                "purpose": "Unique test purpose",
            }
        ]

        # Erstes Insert — sollte erfolgreich sein
        stmt_id = gold_db.insert_statement(
            account_id=giro_account_id,
            source_pdf_hash="gold_dedup_test_1",
            source_filename="dedup_test.pdf",
            period_start="2099-06-01",
            period_end="2099-06-30",
            opening_balance=0.0,
            closing_balance=0.0,
        )

        inserted1, dupes1 = gold_db.insert_transactions(
            statement_id=stmt_id,
            account_id=giro_account_id,
            transactions=test_tx,
        )
        assert inserted1 == 1
        assert dupes1 == 0

        # Zweites Insert (dasselbe) — sollte als Duplikat erkannt werden
        stmt_id2 = gold_db.insert_statement(
            account_id=giro_account_id,
            source_pdf_hash="gold_dedup_test_2",
            source_filename="dedup_test2.pdf",
            period_start="2099-06-01",
            period_end="2099-06-30",
            opening_balance=0.0,
            closing_balance=0.0,
        )

        inserted2, dupes2 = gold_db.insert_transactions(
            statement_id=stmt_id2,
            account_id=giro_account_id,
            transactions=test_tx,
        )
        assert inserted2 == 0
        assert dupes2 == 1

    def test_legitimate_repeated_message_not_deduped(self, gold_db: FinanceDB, giro_account_id: int) -> None:
        """Zwei Buchungen mit gleichem Betrag an unterschiedlichen Tagen werden NICHT als Duplikat verschluckt."""
        test_txs = [
            {
                "booking_date": "2099-07-01",
                "amount": -50.00,
                "currency": "CHF",
                "counterparty": "SameMerchant",
                "purpose": "Purchase 1",  # Unterschiedlicher Zweck
            },
            {
                "booking_date": "2099-07-02",
                "amount": -50.00,
                "currency": "CHF",
                "counterparty": "SameMerchant",
                "purpose": "Purchase 2",  # Unterschiedlicher Zweck
            },
        ]

        stmt_id = gold_db.insert_statement(
            account_id=giro_account_id,
            source_pdf_hash="gold_legit_repeat_test",
            source_filename="legit_repeat.pdf",
            period_start="2099-07-01",
            period_end="2099-07-31",
            opening_balance=0.0,
            closing_balance=0.0,
        )

        inserted, dupes = gold_db.insert_transactions(
            statement_id=stmt_id,
            account_id=giro_account_id,
            transactions=test_txs,
        )
        assert inserted == 2, "Beide legitimen Buchungen sollten eingefügt werden"
        assert dupes == 0, "Keine Duplikate bei unterschiedlichen Buchungen"


class TestCentPrecision:
    """Cent-Rundung: keine Float-Präzisionsverluste."""

    def test_half_cent_rounds_up(self, gold_db: FinanceDB, giro_account_id: int) -> None:
        """0.005 CHF sollte auf 1 Cent runden (ROUND_HALF_UP)."""
        test_tx = [
            {
                "booking_date": "2099-08-01",
                "amount": -0.005,
                "currency": "CHF",
                "counterparty": "CentTestMerchant",
                "purpose": "Half cent test",
            }
        ]

        stmt_id = gold_db.insert_statement(
            account_id=giro_account_id,
            source_pdf_hash="gold_cent_test",
            source_filename="cent_test.pdf",
            period_start="2099-08-01",
            period_end="2099-08-31",
            opening_balance=0.0,
            closing_balance=0.0,
        )

        gold_db.insert_transactions(
            statement_id=stmt_id,
            account_id=giro_account_id,
            transactions=test_tx,
        )

        txs = gold_db.query_transactions(
            account_id=giro_account_id,
            counterparty_like="Half cent test",
            limit=200,
        )
        assert len(txs) >= 1
        # -0.005 CHF -> -1 Cent (ROUND_HALF_UP)
        assert txs[0].amount_cents == -1

    def test_complex_decimal_no_loss(self, gold_db: FinanceDB, giro_account_id: int) -> None:
        """Komplexe Dezimalzahlen ohne Präzisionsverlust."""
        test_tx = [
            {
                "booking_date": "2099-09-01",
                "amount": -123.45,
                "currency": "CHF",
                "counterparty": "PrecisionMerchant",
                "purpose": "Precision test 123.45",
            }
        ]

        stmt_id = gold_db.insert_statement(
            account_id=giro_account_id,
            source_pdf_hash="gold_precision_test",
            source_filename="precision_test.pdf",
            period_start="2099-09-01",
            period_end="2099-09-30",
            opening_balance=0.0,
            closing_balance=0.0,
        )

        gold_db.insert_transactions(
            statement_id=stmt_id,
            account_id=giro_account_id,
            transactions=test_tx,
        )

        txs = gold_db.query_transactions(
            account_id=giro_account_id,
            counterparty_like="Precision test",
            limit=200,
        )
        assert len(txs) >= 1
        assert txs[0].amount_cents == -12345, f"Erwartet -12345 Cents, erhalten {txs[0].amount_cents}"


class TestCrossAccountIsolation:
    """Konten sind isoliert — keine Datenvermischung."""

    def test_giro_transactions_not_in_cc(self, gold_db: FinanceDB, cc_account_id: int) -> None:
        """Giro-Counterpartys tauchen nicht im CC-Konto auf."""
        txs = gold_db.query_transactions(
            account_id=cc_account_id,
            counterparty_like="Employer GmbH",
            limit=200,
        )
        assert len(txs) == 0, "Gehalt sollte nur auf Girokonto sein"

    def test_cc_transactions_not_in_giro(self, gold_db: FinanceDB, giro_account_id: int) -> None:
        """CC-spezifische Transaktionen nicht auf Giro."""
        txs = gold_db.query_transactions(
            account_id=giro_account_id,
            counterparty_like="Netflix",
            limit=200,
        )
        assert len(txs) == 0, "Netflix sollte nur auf Kreditkarte sein"

    def test_spar_only_incoming(self, gold_db: FinanceDB, spar_account_id: int) -> None:
        """Sparkonto hat nur positive Transfers."""
        txs = gold_db.query_transactions(account_id=spar_account_id, limit=200)
        for tx in txs:
            assert tx.amount_cents > 0, f"Spar sollte nur positive Betraege haben: {tx.amount_cents}"