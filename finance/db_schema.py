"""Finanz-DB: Schema, DAO und Migrations.

Strikt getrennt von ``rag_store.db``/``wellbeing_db``. Persistenz-Pfad
default ``database/finance.db``; berschreibbar via ``FINANCE_DB_PATH``-Env.

Schema-Prinzipien:
* IBAN ist Single Source of Truth für Konto-Identifikation (ISO 13616).
* ``transactions.dedup_hash`` macht Re-Imports desselben Auszugs idempotent.
* ``statements.source_pdf_hash`` blockt erneutes Einlesen identischer PDFs.
* Alle Betrge werden als ganzzahlige Cents (``INTEGER``) gespeichert;
  Float-Arithmetik auf Geldbetrgen ist nicht akzeptabel (Rundungsfehler
  bei Aggregation).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Sequence, Tuple

from finance.models import (
    ACCOUNT_TYPE_MIGRATIONS,
    DEFAULT_CURRENCY,
    VALID_ACCOUNT_TYPES,
    VALID_GOAL_STATUSES,
    VALID_PLAN_ITEM_KINDS,
    VALID_PLAN_ITEM_SOURCES,
    VALID_PLAN_ITEM_STATUSES,
    VALID_TRANSACTION_NATURES,
)

logger = logging.getLogger(__name__)

_IBAN_RE = re.compile(r"^[A-Z]{2}\d{2}[A-Z0-9]{11,30}$")


def _is_valid_iban(identifier: Optional[str]) -> bool:
    value = "".join((identifier or "").split()).upper()
    if not _IBAN_RE.match(value):
        return False
    rearranged = f"{value[4:]}{value[:4]}"
    digits: List[str] = []
    for ch in rearranged:
        if ch.isdigit():
            digits.append(ch)
        else:
            digits.append(str(ord(ch) - 55))
    remainder = 0
    for c in "".join(digits):
        remainder = (remainder * 10 + int(c)) % 97
    return remainder == 1


# Default-Pfad: project_root/database/finance.db
_DEFAULT_DB_PATH = (
    Path(__file__).resolve().parent.parent / "database" / "finance.db"
)


# ---------------------------------------------------------------------------
# Datenklassen (read-only Views aus der DB)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Bank:
    id: int
    name: str
    bic: Optional[str]
    country_code: Optional[str]


@dataclass(frozen=True)
class Account:
    id: int
    bank_id: int
    iban: str
    account_holder: Optional[str]
    currency: str
    account_type: Optional[str]
    bank_name: Optional[str] = None  # JOIN-Feld für UI-Komfort


@dataclass(frozen=True)
class Statement:
    id: int
    account_id: int
    period_start: Optional[str]
    period_end: Optional[str]
    opening_balance_cents: Optional[int]
    closing_balance_cents: Optional[int]
    total_credits_cents: Optional[int]
    total_debits_cents: Optional[int]
    consistency_passed: Optional[bool]
    consistency_errors: Optional[str]
    needs_review: bool
    source_pdf_hash: str
    source_filename: Optional[str]
    imported_at: str


@dataclass(frozen=True)
class Transaction:
    id: int
    statement_id: int
    account_id: int
    booking_date: str  # ISO YYYY-MM-DD
    value_date: Optional[str]
    amount_cents: int  # signed
    currency: str
    counterparty: Optional[str]
    counterparty_iban: Optional[str]
    purpose: Optional[str]
    booking_type: Optional[str]
    transaction_nature: str
    category: Optional[str]  # JOIN-Feld
    dedup_hash: str


@dataclass(frozen=True)
class Category:
    id: int
    name: str
    parent_id: Optional[int]
    kind: str        # 'expense' | 'income' | 'transfer'
    color: Optional[str]


@dataclass(frozen=True)
class CounterpartyRule:
    id: int
    match_iban: Optional[str]
    match_counterparty: Optional[str]
    category_id: int
    category_name: Optional[str]  # JOIN-Feld


@dataclass(frozen=True)
class Budget:
    id: int
    category_id: int
    category_name: Optional[str]   # JOIN-Feld
    month: str                     # YYYY-MM
    budget_cents: int


@dataclass(frozen=True)
class TransferLink:
    """Verknpfung zweier Buchungen als logisch eine Geldbewegung.

    Z.B. Kreditkarten-Sammelbelastung auf dem Girokonto (-450) gepaart mit
    der Zahlungseingangs-Buchung auf dem Kreditkarten-Konto (+450). Beide
    Buchungen werden in Cashflow-Aggregationen ausgeschlossen, damit es
    keine Doppelzhlung gibt.
    """
    id: int
    outgoing_tx_id: int           # die negative (Belastung)
    incoming_tx_id: int           # die positive (Gutschrift / Zahlungseingang)
    confidence: float
    source: str                   # 'user' | 'auto'
    outgoing_account_iban: Optional[str] = None
    incoming_account_iban: Optional[str] = None
    outgoing_amount_cents: Optional[int] = None
    outgoing_booking_date: Optional[str] = None
    incoming_booking_date: Optional[str] = None
    outgoing_counterparty: Optional[str] = None
    incoming_counterparty: Optional[str] = None


@dataclass(frozen=True)
class Goal:
    """Sparziel / Sinking Fund (Phase 2, 2026-09-12).

    Persistenter Zielwert mit optionaler Zeitkomponente (YNAB/PocketSmith-
    Semantik): ``target_cents`` ist das Sparziel, ``target_date`` das
    optionale Faelligkeitsdatum (ISO YYYY-MM-DD), ``monthly_rate_cents``
    die geplante monatliche Rate. Fortschritt = Summe der in
    ``goal_contributions`` zugeordneten Buchungen (max. 1 Ziel pro
    Buchung, UNIQUE-Constraint -- Double-Counting ist konstruktiv
    ausgeschlossen).
    """

    id: int
    name: str
    iban: str                          # finanzierendes Konto (SSOT: IBAN)
    currency: str
    target_cents: int                  # > 0
    target_date: Optional[str] = None  # ISO YYYY-MM-DD (optional)
    monthly_rate_cents: Optional[int] = None  # >= 0
    status: str = "active"             # VALID_GOAL_STATUSES
    notes: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


@dataclass(frozen=True)
class GoalContribution:
    """Zuordnung einer Buchung zu exakt einem Ziel (Fortschritts-Beitrag).

    ``amount_cents`` ist ein Snapshot des Buchungsbetrags zum Zeitpunkt
    der Zuordnung (vorzeichenbehaftet) -- der Fortschritt ist damit
    stabil, auch wenn die Buchung spaeter bearbeitet wird.
    """

    id: int
    goal_id: int
    transaction_id: int
    amount_cents: int
    source: str                        # 'user' | 'rule'
    created_at: Optional[str] = None


class PlanRevisionConflict(ValueError):
    """Optimistic-Concurrency-Konflikt bei Plan-Item-Updates.

    ``expected_revision`` entspricht nicht der aktuellen ``revision``:
    ein paralleler Write hat das Item zwi-schenzeitlich geaendert.
    Die DAO ueberschreibt NICHT (Gate: keine stille Ueberschreibung).
    """

    def __init__(
        self,
        plan_item_id: int,
        expected_revision: Optional[int],
        actual_revision: int,
    ) -> None:
        super().__init__(
            f"Plan-Item {plan_item_id}: Revision-Konflikt "
            f"(expected={expected_revision}, actual={actual_revision})"
        )
        self.plan_item_id = plan_item_id
        self.expected_revision = expected_revision
        self.actual_revision = actual_revision


@dataclass(frozen=True)
class PlanItem:
    """Einmalige geplante Einnahme/Ausgabe (Forecast UX AP1, 2026-09-16).

    Bewusst KEINE Buchung/Transaktion: ein Plan-Item ist eine datierte,
    rueckgaengige Prognose-Annahme (z. B. "Miete Januar 2027: 1350",
    "Steuererstattung: 1500 am 2026-11-15"). Es taucht in der
    ``cash_flow_forecast``-Prognose auf, erzeugt aber weder
    ``transactions`` noch ``goal_contributions`` (getrennte Semantik,
    Bank-/Kontodaten bleiben unveraendert).

    * ``amount_cents`` > 0 (Vorzeichen kommt aus ``kind``: income +, expense -)
    * ``iban`` ist Single Source of Truth (wie Goals)
    * ``revision``: optimistic lock, Start 1, bei jedem Write +1
    * ``client_token``: UI-Idempotenz (Doppel-Click desselben Create)
    * ``source_type``: 'manual' (AP1) | 'detected' (AP2, Vorschlags-Pfad)
    * ``status``: 'active' | 'paused' | 'done' | 'rejected'
      (nur 'active' fließt in die Prognose)
    """

    id: int
    iban: str
    currency: str
    kind: str                              # 'income' | 'expense'
    amount_cents: int                      # > 0
    category_id: Optional[int] = None
    title: Optional[str] = None
    due_date: str = ""                     # ISO YYYY-MM-DD
    source_type: str = "manual"            # 'manual' | 'detected'
    status: str = "active"                 # VALID_PLAN_ITEM_STATUSES
    revision: int = 1
    client_token: Optional[str] = None
    notes: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


@dataclass(frozen=True)
class PlanJournalEntry:
    """Aenderungs-Journal eines Plan-Items (Undo via Gegenrevision).

    ``before_json``/``after_json`` sind komplette Item-Snapshots
    (JSON, ohne ``client_token``) oder None. Das Journal referenziert
    das Item OHNE Foreign Key: Eintraege ueberleben die Loeschung des
    Items und machen damit ``deleted`` wieder rueckgaengig (Reinsert
    mit derselben ID).
    """

    id: int
    plan_item_id: int
    action: str                            # 'created' | 'updated' | 'deleted' | 'restored'
    before_json: Optional[str] = None
    after_json: Optional[str] = None
    revision: int = 1
    created_at: Optional[str] = None


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


_SCHEMA_STATEMENTS: Tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS banks (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        name            TEXT NOT NULL,
        bic             TEXT,
        country_code    TEXT,
        created_at      TEXT NOT NULL DEFAULT (datetime('now'))
    )
    """,
    # SQLite verbietet Ausdrcke in UNIQUE-Constraints; via UNIQUE INDEX auf
    # COALESCE() lsst sich Bank-Identitt trotzdem deterministisch erzwingen.
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_banks_unique ON banks(name, COALESCE(bic, ''))",
    f"""
    CREATE TABLE IF NOT EXISTS accounts (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        bank_id         INTEGER NOT NULL REFERENCES banks(id) ON DELETE RESTRICT,
        iban            TEXT NOT NULL UNIQUE,
        account_holder  TEXT,
        currency        TEXT NOT NULL DEFAULT '{DEFAULT_CURRENCY}',
        account_type    TEXT,
        created_at      TEXT NOT NULL DEFAULT (datetime('now'))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS statements (
        id                      INTEGER PRIMARY KEY AUTOINCREMENT,
        account_id              INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
        period_start            TEXT,
        period_end              TEXT,
        opening_balance_cents   INTEGER,
        closing_balance_cents   INTEGER,
        total_credits_cents     INTEGER,
        total_debits_cents      INTEGER,
        consistency_passed      INTEGER,
        consistency_errors      TEXT,
        needs_review            INTEGER NOT NULL DEFAULT 0,
        source_pdf_hash         TEXT NOT NULL UNIQUE,
        source_filename         TEXT,
        imported_at             TEXT NOT NULL DEFAULT (datetime('now'))
    )
    """,
    f"""
    CREATE TABLE IF NOT EXISTS transactions (
        id                  INTEGER PRIMARY KEY AUTOINCREMENT,
        statement_id        INTEGER NOT NULL REFERENCES statements(id) ON DELETE CASCADE,
        account_id          INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
        booking_date        TEXT NOT NULL,
        value_date          TEXT,
        amount_cents        INTEGER NOT NULL,
        currency            TEXT NOT NULL DEFAULT '{DEFAULT_CURRENCY}',
        counterparty        TEXT,
        counterparty_iban   TEXT,
        purpose             TEXT,
        booking_type        TEXT,
        transaction_nature  TEXT NOT NULL DEFAULT 'ordinary',
        raw_text            TEXT,
        dedup_hash          TEXT NOT NULL UNIQUE,
        created_at          TEXT NOT NULL DEFAULT (datetime('now'))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS categories (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        name        TEXT NOT NULL UNIQUE,
        parent_id   INTEGER REFERENCES categories(id) ON DELETE SET NULL,
        kind        TEXT NOT NULL DEFAULT 'expense',  -- 'expense' | 'income' | 'transfer'
        color       TEXT,                              -- optionaler Hex-Code für UI
        created_at  TEXT NOT NULL DEFAULT (datetime('now'))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS transaction_category (
        transaction_id  INTEGER NOT NULL REFERENCES transactions(id) ON DELETE CASCADE,
        category_id     INTEGER NOT NULL REFERENCES categories(id) ON DELETE CASCADE,
        confidence      REAL NOT NULL DEFAULT 1.0,
        source          TEXT NOT NULL DEFAULT 'user',  -- 'user' | 'llm' | 'rule'
        created_at      TEXT NOT NULL DEFAULT (datetime('now')),
        PRIMARY KEY (transaction_id, category_id)
    )
    """,
    # Nachhaltige Auto-Kategorisierung: deterministische Regel pro Counterparty.
    # Match-Strategie zweistufig: (1) IBAN exakt -- bei Überweisungen unzweideutig;
    # (2) Fallback auf normalisierten Counterparty-String (lowercased+trimmed)
    # für Karten-/Lastschriftbuchungen ohne Counterparty-IBAN.
    """
    CREATE TABLE IF NOT EXISTS counterparty_rules (
        id                   INTEGER PRIMARY KEY AUTOINCREMENT,
        match_iban           TEXT,
        match_counterparty   TEXT,
        category_id          INTEGER NOT NULL REFERENCES categories(id) ON DELETE CASCADE,
        created_at           TEXT NOT NULL DEFAULT (datetime('now')),
        CHECK ((match_iban IS NOT NULL) OR (match_counterparty IS NOT NULL))
    )
    """,
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_rules_iban ON counterparty_rules(match_iban) WHERE match_iban IS NOT NULL",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_rules_cp   ON counterparty_rules(match_counterparty) WHERE match_counterparty IS NOT NULL",
    """
    CREATE TABLE IF NOT EXISTS budgets (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        category_id     INTEGER NOT NULL REFERENCES categories(id) ON DELETE CASCADE,
        month           TEXT NOT NULL,         -- ISO-Monat YYYY-MM
        budget_cents    INTEGER NOT NULL,      -- signed; bei expense-Kategorien negativ
        created_at      TEXT NOT NULL DEFAULT (datetime('now')),
        UNIQUE(category_id, month)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_tx_account_date ON transactions(account_id, booking_date)",
    "CREATE INDEX IF NOT EXISTS idx_tx_statement   ON transactions(statement_id)",
    "CREATE INDEX IF NOT EXISTS idx_tx_counterparty ON transactions(counterparty)",
    "CREATE INDEX IF NOT EXISTS idx_stmt_account   ON statements(account_id)",
    "CREATE INDEX IF NOT EXISTS idx_budgets_month  ON budgets(month)",
    # ------------------------------------------------------------------
    # Goals / Sinking Funds (Phase 2, 2026-09-12): Sparziele mit
    # optionalem Faelligkeitsdatum + expliziten Ziel-Beitragen.
    # Invariante: max. 1 Ziel pro Buchung (UNIQUE transaction_id) --
    # Double-Counting ist damit konstruktiv ausgeschlossen.
    # ------------------------------------------------------------------
    f"""
    CREATE TABLE IF NOT EXISTS goals (
        id                  INTEGER PRIMARY KEY AUTOINCREMENT,
        name                TEXT NOT NULL,
        iban                TEXT NOT NULL,   -- finanzierendes Konto (SSOT: IBAN)
        currency            TEXT NOT NULL DEFAULT '{DEFAULT_CURRENCY}',
        target_cents        INTEGER NOT NULL,
        target_date         TEXT,            -- ISO YYYY-MM-DD (optional)
        monthly_rate_cents  INTEGER,         -- >= 0
        status              TEXT NOT NULL DEFAULT 'active',
        notes               TEXT,
        created_at          TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at          TEXT NOT NULL DEFAULT (datetime('now'))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS goal_contributions (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        goal_id         INTEGER NOT NULL REFERENCES goals(id) ON DELETE CASCADE,
        transaction_id  INTEGER NOT NULL UNIQUE REFERENCES transactions(id) ON DELETE CASCADE,
        amount_cents    INTEGER NOT NULL,    -- Snapshot des Buchungsbetrags
        source          TEXT NOT NULL DEFAULT 'user',
        created_at      TEXT NOT NULL DEFAULT (datetime('now'))
    )
    """,
    # Index-Namen sind Teil des oeffentlichen Schemas (goals_active / gc_*):
    "CREATE INDEX IF NOT EXISTS goals_active           ON goals(status)",
    "CREATE INDEX IF NOT EXISTS idx_goals_target       ON goals(target_date)",
    "CREATE INDEX IF NOT EXISTS gc_goal                ON goal_contributions(goal_id)",
    "CREATE INDEX IF NOT EXISTS gc_tx                  ON goal_contributions(transaction_id)",
    # Transfer-Verknpfungen: zwei Tx (negativ auf Konto A, positiv auf Konto B)
    # werden als eine Geldbewegung erkannt -- z.B. Kreditkartenabrechnung
    # gegen Sammelbelastung auf dem Girokonto. Beide Buchungen werden in
    # Cashflow-Aggregationen ausgeschlossen.
    """
    CREATE TABLE IF NOT EXISTS transfer_links (
        id                  INTEGER PRIMARY KEY AUTOINCREMENT,
        outgoing_tx_id      INTEGER NOT NULL UNIQUE REFERENCES transactions(id) ON DELETE CASCADE,
        incoming_tx_id      INTEGER NOT NULL UNIQUE REFERENCES transactions(id) ON DELETE CASCADE,
        confidence          REAL NOT NULL DEFAULT 1.0,
        source              TEXT NOT NULL DEFAULT 'user',
        created_at          TEXT NOT NULL DEFAULT (datetime('now')),
        CHECK (outgoing_tx_id != incoming_tx_id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_transfer_out ON transfer_links(outgoing_tx_id)",
    "CREATE INDEX IF NOT EXISTS idx_transfer_in  ON transfer_links(incoming_tx_id)",
    # Statement-basierte Ausgleichs-Verknuepfung fuer Kreditkarten: eine
    # Sammelbelastung auf einem Nicht-CC-Konto wird dem passenden
    # Kreditkarten-Statement zugeordnet (Betragsgleichheit zu
    # |closing_balance| + zeitliche Naehe zu period_end).
    """
    CREATE TABLE IF NOT EXISTS statement_settlements (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        transaction_id  INTEGER NOT NULL UNIQUE REFERENCES transactions(id) ON DELETE CASCADE,
        statement_id    INTEGER NOT NULL UNIQUE REFERENCES statements(id) ON DELETE CASCADE,
        confidence      REAL NOT NULL DEFAULT 1.0,
        source          TEXT NOT NULL DEFAULT 'auto',
        created_at      TEXT NOT NULL DEFAULT (datetime('now'))
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_stmt_settle_tx ON statement_settlements(transaction_id)",
    "CREATE INDEX IF NOT EXISTS idx_stmt_settle_stmt ON statement_settlements(statement_id)",
    """
    CREATE TABLE IF NOT EXISTS finance_schema_catalog (
        key             TEXT PRIMARY KEY,
        payload_json    TEXT NOT NULL,
        schema_hash     TEXT NOT NULL,
        updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
    )
    """,
    # ------------------------------------------------------------------
    # Generischer Transaktions-Suchindex (FTS5 + Vektor-Embeddings).
    # Hier (Freshe-DB-DLL) und NICHT nur in der Migration, damit eine
    # frische Datenbank komplett initialisiert ist (Tabellen, Indexe,
    # FTS-Trigger, Constraints). Die Backfill-Logik in
    # _migrate_transaction_search bleibt fuer Altdatenbanken erhalten.
    # ------------------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS transaction_search_docs (
        transaction_id    INTEGER PRIMARY KEY REFERENCES transactions(id) ON DELETE CASCADE,
        search_text       TEXT NOT NULL,
        search_text_norm  TEXT NOT NULL,
        updated_at        TEXT NOT NULL DEFAULT (datetime('now'))
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_tx_search_docs_norm ON transaction_search_docs(search_text_norm)",
    """
    CREATE VIRTUAL TABLE IF NOT EXISTS transaction_search_fts USING fts5(
        transaction_id UNINDEXED,
        search_text,
        tokenize='unicode61 remove_diacritics 2'
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS transaction_search_embeddings (
        transaction_id    INTEGER PRIMARY KEY REFERENCES transactions(id) ON DELETE CASCADE,
        model_name        TEXT NOT NULL,
        embedding_dim     INTEGER NOT NULL,
        embedding_blob    BLOB NOT NULL,
        updated_at        TEXT NOT NULL DEFAULT (datetime('now'))
    )
    """,
    # ------------------------------------------------------------------
    # Forecast plan items (Forecast UX AP1, 2026-09-16): datierte,
    # rueckgaengige Prognose-Annahmen (KEINE Buchungen, kein
    # goal_contributions-Mixing; Bank-/Kontodaten bleiben unveraendert).
    # CREATE ... IF NOT EXISTS macht die Tabellen fuer Altdatenbanken
    # automatisch additiv (analog transaction_search_docs, 2026-08-04).
    # ------------------------------------------------------------------
    f"""
    CREATE TABLE IF NOT EXISTS forecast_plan_items (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        iban            TEXT NOT NULL,
        currency        TEXT NOT NULL DEFAULT '{DEFAULT_CURRENCY}',
        kind            TEXT NOT NULL CHECK (kind IN ('income', 'expense')),
        amount_cents    INTEGER NOT NULL CHECK (amount_cents > 0),
        category_id     INTEGER REFERENCES categories(id) ON DELETE SET NULL,
        title           TEXT,
        due_date        TEXT NOT NULL,
        source_type     TEXT NOT NULL DEFAULT 'manual'
                        CHECK (source_type IN ('manual', 'detected')),
        status          TEXT NOT NULL DEFAULT 'active'
                        CHECK (status IN ('active', 'paused', 'done', 'rejected')),
        revision        INTEGER NOT NULL DEFAULT 1,
        client_token    TEXT UNIQUE,
        notes           TEXT,
        created_at      TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
    )
    """,
    "CREATE INDEX IF NOT EXISTS fpi_iban_due ON forecast_plan_items (iban, due_date)",
    "CREATE INDEX IF NOT EXISTS fpi_status ON forecast_plan_items (status)",
    # Aenderungs-Journal (Undo via Gegenrevision). Bewusst OHNE FOREIGN
    # KEY auf forecast_plan_items: Eintraege ueberleben die Loeschung
    # des Items und machen damit 'deleted' wieder rueckgaengig (Reinsert
    # mit derselben ID).
    """
    CREATE TABLE IF NOT EXISTS forecast_plan_journal (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        plan_item_id    INTEGER NOT NULL,
        action          TEXT NOT NULL
                        CHECK (action IN ('created', 'updated', 'deleted', 'restored')),
        before_json     TEXT,
        after_json      TEXT,
        revision        INTEGER NOT NULL,
        created_at      TEXT NOT NULL DEFAULT (datetime('now'))
    )
    """,
    "CREATE INDEX IF NOT EXISTS fpj_item ON forecast_plan_journal (plan_item_id, id)",
)

_FINANCE_SCHEMA_CATALOG_KEY = "schema_context_v1"

_FINANCE_SEMANTIC_HINTS: Dict[str, Dict[str, Any]] = {
    "banks": {
        "role": "master_data",
        "grain": "one row per bank",
        "primary_metrics": [],
    },
    "accounts": {
        "role": "master_data",
        "grain": "one row per account",
        "primary_metrics": [],
    },
    "statements": {
        "role": "periodic_snapshot",
        "grain": "one row per imported account statement",
        "primary_metrics": ["opening_balance_cents", "closing_balance_cents"],
    },
    "transactions": {
        "role": "event_fact",
        "grain": "one row per booking",
        "primary_metrics": ["amount_cents"],
        "time_column": "booking_date",
    },
    "transaction_category": {
        "role": "mapping",
        "grain": "many-to-many transaction/category assignment",
        "primary_metrics": ["confidence"],
    },
    "categories": {
        "role": "dimension",
        "grain": "one row per category",
        "primary_metrics": [],
    },
    "counterparty_rules": {
        "role": "rule_base",
        "grain": "one row per categorization rule",
        "primary_metrics": [],
    },
    "budgets": {
        "role": "plan_fact",
        "grain": "one row per category/month budget",
        "primary_metrics": ["budget_cents"],
    },
    "transfer_links": {
        "role": "reconciliation_link",
        "grain": "one row per internal transfer pair",
        "primary_metrics": ["confidence"],
    },
    "statement_settlements": {
        "role": "reconciliation_link",
        "grain": "one row per statement-to-transaction settlement",
        "primary_metrics": ["confidence"],
    },
    "transaction_search_docs": {
        "role": "search_index",
        "grain": "one row per transaction searchable text",
        "primary_metrics": [],
    },
    "transaction_search_embeddings": {
        "role": "search_index",
        "grain": "one row per transaction embedding",
        "primary_metrics": ["embedding_dim"],
    },
}


# ---------------------------------------------------------------------------
# DAO
# ---------------------------------------------------------------------------


def _normalize_iban(iban: str) -> str:
    """Normalisiert IBAN: Whitespace entfernen, Uppercase."""
    return "".join(iban.split()).upper()


def _normalize_counterparty(name: Optional[str]) -> Optional[str]:
    """Deterministische, verlustarme Normalisierung für Regel-Matching.

    Lowercased + Whitespace-Kollaps. Bewusst KEINE Pattern/Substring-Logik
    -- das wäre nicht-deterministisch und gegen die Projekt-Direktive.
    """
    if not name:
        return None
    return " ".join(name.lower().split()) or None


_LIKE_WILDCARD_RE = re.compile(r"%+")


def _normalize_search_text(value: Any) -> str:
    """Normalisiert Freitext fuer den Finance-Suchindex deterministisch."""
    if value is None:
        return ""
    text = str(value).replace('<|"|>', ' ').strip().lower()
    text = re.sub(r"\s+", " ", text)
    return text


def _tokenize_search_query(value: str) -> List[str]:
    """Tokenisiert Suchanfragen fuer FTS5 ohne domanenspezifische Heuristik."""
    normalized = _normalize_search_text(value)
    return [token for token in re.findall(r"[\w]{2,}", normalized, flags=re.UNICODE) if token]


def _normalize_like_needle(value: Any) -> Optional[str]:
    """Kanonisiert einen User-/LLM-LIKE-Suchbegriff für die Finance-DAO.

    Single Source of Truth für alle ``*_like``-Filter:

    * akzeptiert ``None``/leer und liefert ``None`` zurück;
    * entfernt umschließende Quotes und Tool-Token-Artefakte (``<|"|>``);
    * konvertiert Glob-Wildcards (``*``/``?``) in SQL-Wildcards (``%``/``_``);
    * kollabiert mehrfache ``%`` und entfernt führende/abschließende ``%``,
      weil die DAO-Klauseln selbst ``%…%`` ergänzen.

    Es findet bewusst keinerlei Keyword-/Pattern-Heuristik statt – nur eine
    deterministische Eingabe-Säuberung an der DAO-Grenze.
    """
    if value is None:
        return None
    text = str(value).replace('<|"|>', "").strip()
    if text and text[0] == text[-1] and text[0] in {'"', "'"}:
        text = text[1:-1].strip()
    if not text:
        return None
    text = text.replace("*", "%").replace("?", "_")
    text = _LIKE_WILDCARD_RE.sub("%", text).strip("%").strip()
    return text or None


def _to_cents(amount: float) -> int:
    """Geldbetrag (Float) in vorzeichenbehaftete Cents (int)."""
    decimal_amount = Decimal(str(amount))
    return int((decimal_amount * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _from_cents(cents: int) -> float:
    """Cents (int)  Geldbetrag (Float). Nur für Anzeige."""
    return cents / 100.0


def _hash_file(file_path: str) -> str:
    """SHA-256 über Dateiinhalt für ``statements.source_pdf_hash``."""
    h = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _tx_dedup_hash(
    account_id: int,
    booking_date: str,
    amount_cents: int,
    counterparty: Optional[str],
    purpose: Optional[str],
) -> str:
    """Stabile, deterministische Dedup-Signatur einer Buchung.

    Bewusst NICHT inklusive ``statement_id`` oder ``raw_text`` --
    derselbe Buchungssatz aus zwei sich ueberlappenden Auszuegen muss als
    Duplikat erkannt werden.
    """
    booking_date_str = str(booking_date).strip() if booking_date is not None else ""
    if not booking_date_str:
        raise ValueError("booking_date is required for transaction dedup hash")

    counterparty_str = (counterparty or "")
    if not isinstance(counterparty_str, str):
        counterparty_str = str(counterparty_str)

    purpose_str = (purpose or "")
    if not isinstance(purpose_str, str):
        purpose_str = str(purpose_str)

    payload = "\u241f".join(
        [
            str(account_id),
            booking_date_str,
            str(amount_cents),
            counterparty_str.strip().lower(),
            purpose_str.strip().lower(),
        ]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class FinanceDB:
    """Thread-safe DAO für die Finanz-DB.

    Singleton-Verhalten über den Default-Pfad; alternative Pfade für Tests
    legen unabhngige Instanzen an.
    """

    _instances: Dict[str, "FinanceDB"] = {}
    _instances_lock: threading.RLock = threading.RLock()

    def __init__(self, db_path: Optional[str] = None) -> None:
        resolved = db_path or os.environ.get("FINANCE_DB_PATH") or str(_DEFAULT_DB_PATH)
        self.db_path = str(Path(resolved).resolve())
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._init_schema()
        logger.info(f" FinanceDB initialized at {self.db_path}")

    @classmethod
    def get_instance(cls, db_path: Optional[str] = None) -> "FinanceDB":
        key = str(Path(db_path or os.environ.get("FINANCE_DB_PATH") or _DEFAULT_DB_PATH).resolve())
        with cls._instances_lock:
            if key not in cls._instances:
                cls._instances[key] = cls(key)
            return cls._instances[key]

    # -- low-level connection ----------------------------------------

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path, check_same_thread=False, timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self._lock, self._connect() as conn:
            for stmt in _SCHEMA_STATEMENTS:
                conn.execute(stmt)
            self._migrate_categories(conn)
            self._migrate_transactions(conn)
            self._migrate_account_types(conn)
            self._migrate_statements_consistency(conn)
            self._migrate_transaction_search(conn)
            self._migrate_goal_index_names(conn)
            self._refresh_schema_catalog(conn)

    def _migrate_goal_index_names(self, conn: sqlite3.Connection) -> None:
        """Entfernt Legacy-Index-Namen der Goal-Tabellen (2026-09-13).

        Die Index-Namen ``goals_active`` / ``gc_goal`` / ``gc_tx`` sind Teil
        des oeffentlichen Schemas. Bestehende DBs tragen noch die alten
        ``idx_*``-Namen (dieselben Spalten) und werden hier entfernt,
        damit keine redundanten Duplikat-Indizes bleiben.
        """
        for legacy in ("idx_goals_status", "idx_goal_contrib_goal"):
            conn.execute(f"DROP INDEX IF EXISTS {legacy}")

    def _refresh_schema_catalog(self, conn: sqlite3.Connection) -> None:
        schema_hash = self._compute_schema_hash(conn)
        row = conn.execute(
            "SELECT schema_hash FROM finance_schema_catalog WHERE key = ?",
            (_FINANCE_SCHEMA_CATALOG_KEY,),
        ).fetchone()
        if row and str(row["schema_hash"] or "") == schema_hash:
            return

        payload = self._build_schema_context_payload(conn, schema_hash=schema_hash)
        conn.execute(
            """
            INSERT INTO finance_schema_catalog (key, payload_json, schema_hash, updated_at)
            VALUES (?, ?, ?, datetime('now'))
            ON CONFLICT(key) DO UPDATE SET
                payload_json = excluded.payload_json,
                schema_hash = excluded.schema_hash,
                updated_at = datetime('now')
            """,
            (
                _FINANCE_SCHEMA_CATALOG_KEY,
                json.dumps(payload, ensure_ascii=False),
                schema_hash,
            ),
        )

    def _compute_schema_hash(self, conn: sqlite3.Connection) -> str:
        rows = conn.execute(
            """
            SELECT type, name, COALESCE(sql, '') AS sql
            FROM sqlite_master
            WHERE type IN ('table', 'index', 'view')
              AND name NOT LIKE 'sqlite_%'
            ORDER BY type, name
            """
        ).fetchall()
        payload = "\n".join(
            f"{row['type']}|{row['name']}|{row['sql']}" for row in rows
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _build_schema_context_payload(
        self,
        conn: sqlite3.Connection,
        *,
        schema_hash: str,
    ) -> Dict[str, Any]:
        table_rows = conn.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type = 'table'
              AND name NOT LIKE 'sqlite_%'
            ORDER BY name
            """
        ).fetchall()

        tables: Dict[str, Dict[str, Any]] = {}
        relationships: List[Dict[str, Any]] = []
        for row in table_rows:
            table_name = str(row["name"])
            safe_name = table_name.replace('"', '""')
            columns = conn.execute(f'PRAGMA table_info("{safe_name}")').fetchall()
            fks = conn.execute(f'PRAGMA foreign_key_list("{safe_name}")').fetchall()

            table_columns = []
            for col in columns:
                table_columns.append(
                    {
                        "name": col["name"],
                        "type": col["type"],
                        "notnull": bool(col["notnull"]),
                        "default": col["dflt_value"],
                        "pk": bool(col["pk"]),
                    }
                )

            fk_entries = []
            for fk in fks:
                entry = {
                    "from": fk["from"],
                    "to_table": fk["table"],
                    "to": fk["to"],
                    "on_update": fk["on_update"],
                    "on_delete": fk["on_delete"],
                }
                fk_entries.append(entry)
                relationships.append(
                    {
                        "from_table": table_name,
                        **entry,
                    }
                )

            tables[table_name] = {
                "columns": table_columns,
                "foreign_keys": fk_entries,
                "semantic_hint": _FINANCE_SEMANTIC_HINTS.get(table_name, {}),
            }

        return {
            "version": _FINANCE_SCHEMA_CATALOG_KEY,
            "db_path": self.db_path,
            "schema_hash": schema_hash,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "tables": tables,
            "relationships": relationships,
        }

    def get_schema_context(self) -> Dict[str, Any]:
        with self._lock, self._connect() as conn:
            self._refresh_schema_catalog(conn)
            row = conn.execute(
                "SELECT payload_json FROM finance_schema_catalog WHERE key = ?",
                (_FINANCE_SCHEMA_CATALOG_KEY,),
            ).fetchone()
            if not row:
                return {
                    "version": _FINANCE_SCHEMA_CATALOG_KEY,
                    "db_path": self.db_path,
                    "schema_hash": "",
                    "generated_at": datetime.utcnow().isoformat() + "Z",
                    "tables": {},
                    "relationships": [],
                }
            payload = str(row["payload_json"] or "{}")
            try:
                parsed = json.loads(payload)
            except json.JSONDecodeError:
                logger.warning("finance schema catalog payload was invalid JSON; rebuilding")
                self._refresh_schema_catalog(conn)
                row2 = conn.execute(
                    "SELECT payload_json FROM finance_schema_catalog WHERE key = ?",
                    (_FINANCE_SCHEMA_CATALOG_KEY,),
                ).fetchone()
                payload2 = str(row2["payload_json"] or "{}") if row2 else "{}"
                parsed = json.loads(payload2)
            return parsed if isinstance(parsed, dict) else {}

    @staticmethod
    def _migrate_statements_consistency(conn: sqlite3.Connection) -> None:
        """Idempotente Migration der Konsistenz-/Review-Spalten auf ``statements``.

        Frische DBs erhalten die Spalten direkt aus ``_SCHEMA_STATEMENTS``.
        Bestehende DBs bekommen sie hier per ``ALTER TABLE ADD COLUMN``
        (nur, wenn sie noch fehlen). ``needs_review`` wird mit
        ``NOT NULL DEFAULT 0`` angelegt, damit vorhandene Zeilen automatisch
        als "keine Review noetig" gelten, bis sie geprueft werden.
        """
        cols = {row["name"] for row in conn.execute("PRAGMA table_info(statements)").fetchall()}
        if "total_credits_cents" not in cols:
            conn.execute("ALTER TABLE statements ADD COLUMN total_credits_cents INTEGER")
        if "total_debits_cents" not in cols:
            conn.execute("ALTER TABLE statements ADD COLUMN total_debits_cents INTEGER")
        if "consistency_passed" not in cols:
            conn.execute("ALTER TABLE statements ADD COLUMN consistency_passed INTEGER")
        if "consistency_errors" not in cols:
            conn.execute("ALTER TABLE statements ADD COLUMN consistency_errors TEXT")
        if "needs_review" not in cols:
            conn.execute("ALTER TABLE statements ADD COLUMN needs_review INTEGER NOT NULL DEFAULT 0")

        # Index fuer die Review-Abfrage (idempotent).
        conn.execute("CREATE INDEX IF NOT EXISTS idx_stmt_review ON statements(needs_review)")

    @staticmethod
    def _migrate_transaction_search(conn: sqlite3.Connection) -> None:
        """Backfilled den generischen Transaktions-Suchindex (Altdatenbanken).

        Die DDL (Tabellen ``transaction_search_docs`` / ``transaction_search_fts``
        / ``transaction_search_embeddings`` samt Index) liegt nun in
        ``_SCHEMA_STATEMENTS`` und wird bei JEDEM Init (frisch und alt)
        idempotent angelegt. Diese Migration macht nur noch das Backfill:
        wenn die Such-Tabellen weniger Zeilen als ``transactions`` haben,
        wird der Index neu aufgebaut.
        """
        tx_count_row = conn.execute("SELECT COUNT(*) AS cnt FROM transactions").fetchone()
        doc_count_row = conn.execute("SELECT COUNT(*) AS cnt FROM transaction_search_docs").fetchone()
        fts_count_row = conn.execute("SELECT COUNT(*) AS cnt FROM transaction_search_fts").fetchone()
        emb_count_row = conn.execute("SELECT COUNT(*) AS cnt FROM transaction_search_embeddings").fetchone()
        tx_count = int(tx_count_row["cnt"] if tx_count_row else 0)
        doc_count = int(doc_count_row["cnt"] if doc_count_row else 0)
        fts_count = int(fts_count_row["cnt"] if fts_count_row else 0)
        emb_count = int(emb_count_row["cnt"] if emb_count_row else 0)

        if tx_count != doc_count or tx_count != fts_count or tx_count != emb_count:
            FinanceDB._rebuild_transaction_search_index_within_conn(conn)

    @staticmethod
    def _build_transaction_search_text(row: sqlite3.Row) -> str:
        parts = [
            row["counterparty"],
            row["purpose"],
            row["booking_type"],
            row["raw_text"],
            row["categories"],
            row["account_iban"],
            row["account_type"],
            row["currency"],
            row["booking_date"],
        ]
        return "\n".join(part for part in parts if part and str(part).strip())

    @staticmethod
    def _delete_transaction_search_docs_within_conn(
        conn: sqlite3.Connection,
        transaction_ids: Sequence[int],
    ) -> None:
        unique_ids = sorted({int(tx_id) for tx_id in transaction_ids if int(tx_id) > 0})
        if not unique_ids:
            return
        for tx_id in unique_ids:
            conn.execute(
                "DELETE FROM transaction_search_fts WHERE transaction_id = ?",
                (tx_id,),
            )
            conn.execute(
                "DELETE FROM transaction_search_docs WHERE transaction_id = ?",
                (tx_id,),
            )
            conn.execute(
                "DELETE FROM transaction_search_embeddings WHERE transaction_id = ?",
                (tx_id,),
            )

    @staticmethod
    def _embed_search_texts(texts: Sequence[str]) -> tuple[str, int, list[bytes]]:
        from utils.embedding_singleton import EmbeddingSingleton

        if not texts:
            return ("", 0, [])

        model = EmbeddingSingleton()
        if not model.is_loaded() and not model.load_model():
            raise RuntimeError("Embedding model could not be loaded for finance search index")

        embeddings = model.encode(list(texts), batch_size=min(64, max(8, len(texts))), defer_cache_cleanup=True)
        model_name = str(model.model_name or "unknown")
        # embedding_dim kann None sein, wenn der Singleton nicht vollstaendig
        # initialisiert wurde -- dann die echte Array-Dimension verwenden.
        embedding_dim = int(getattr(model, "embedding_dim", None) or embeddings.shape[1])
        blobs = [row.astype("float32", copy=False).tobytes() for row in embeddings]
        model.flush_cuda_cache()
        return model_name, embedding_dim, blobs

    @staticmethod
    def _refresh_transaction_search_docs_within_conn(
        conn: sqlite3.Connection,
        transaction_ids: Sequence[int],
    ) -> None:
        unique_ids = sorted({int(tx_id) for tx_id in transaction_ids if int(tx_id) > 0})
        if not unique_ids:
            return

        FinanceDB._delete_transaction_search_docs_within_conn(conn, unique_ids)

        for offset in range(0, len(unique_ids), 500):
            chunk = unique_ids[offset : offset + 500]
            placeholders = ",".join("?" * len(chunk))
            rows = conn.execute(
                f"""
                SELECT
                    t.id,
                    t.booking_date,
                    t.currency,
                    t.counterparty,
                    t.purpose,
                    t.booking_type,
                    t.raw_text,
                    a.iban AS account_iban,
                    a.account_type AS account_type,
                    (
                        SELECT GROUP_CONCAT(c.name, ' ')
                        FROM transaction_category tc
                        JOIN categories c ON c.id = tc.category_id
                        WHERE tc.transaction_id = t.id
                    ) AS categories
                FROM transactions t
                JOIN accounts a ON a.id = t.account_id
                WHERE t.id IN ({placeholders})
                """,
                tuple(chunk),
            ).fetchall()

            texts_to_embed: List[str] = []
            row_ids: List[int] = []
            normalized_texts: List[str] = []
            for row in rows:
                search_text = FinanceDB._build_transaction_search_text(row)
                search_text_norm = _normalize_search_text(search_text)
                row_id = int(row["id"])
                conn.execute(
                    """
                    INSERT INTO transaction_search_docs (transaction_id, search_text, search_text_norm, updated_at)
                    VALUES (?, ?, ?, datetime('now'))
                    """,
                    (row_id, search_text, search_text_norm),
                )
                conn.execute(
                    "INSERT INTO transaction_search_fts (transaction_id, search_text) VALUES (?, ?)",
                    (row_id, search_text),
                )
                row_ids.append(row_id)
                texts_to_embed.append(search_text)
                normalized_texts.append(search_text_norm)

            if row_ids:
                model_name, embedding_dim, blobs = FinanceDB._embed_search_texts(texts_to_embed)
                for tx_id, blob in zip(row_ids, blobs):
                    conn.execute(
                        """
                        INSERT INTO transaction_search_embeddings (
                            transaction_id, model_name, embedding_dim, embedding_blob, updated_at
                        ) VALUES (?, ?, ?, ?, datetime('now'))
                        """,
                        (tx_id, model_name, embedding_dim, sqlite3.Binary(blob)),
                    )

    @staticmethod
    def _rebuild_transaction_search_index_within_conn(conn: sqlite3.Connection) -> None:
        conn.execute("DELETE FROM transaction_search_fts")
        conn.execute("DELETE FROM transaction_search_docs")
        conn.execute("DELETE FROM transaction_search_embeddings")
        rows = conn.execute("SELECT id FROM transactions ORDER BY id").fetchall()
        FinanceDB._refresh_transaction_search_docs_within_conn(
            conn,
            [int(row["id"]) for row in rows],
        )

    @staticmethod
    def _build_fts5_query(text: str) -> str:
        tokens = _tokenize_search_query(text)
        if not tokens:
            raise ValueError("search query must contain searchable terms")
        if len(tokens) == 1:
            return f'{tokens[0]}*'
        phrase = ' '.join(tokens)
        prefixes = " OR ".join(f"{token}*" for token in tokens)
        return f'"{phrase}" OR ({prefixes})'

    @staticmethod
    def _migrate_transactions(conn: sqlite3.Connection) -> None:
        """Idempotente Spalten-/Datenmigration fuer Transaktions-Natur.

        ``transaction_nature`` macht interne Transfers zu einer first-class
        Domaenenaussage statt eines rein impliziten Effekts der Link-Tabelle.
        Bestehende DBs bekommen die Spalte per ``ALTER TABLE``; anschliessend
        werden alle vorhandenen Zeilen deterministisch aus Link-/Kategorie-
        Zustand auf ihren aktuellen Naturwert gebracht.
        """
        cols = {row["name"] for row in conn.execute("PRAGMA table_info(transactions)").fetchall()}
        if "transaction_nature" not in cols:
            conn.execute(
                "ALTER TABLE transactions ADD COLUMN transaction_nature TEXT NOT NULL DEFAULT 'ordinary'"
            )
        FinanceDB._refresh_all_transaction_natures_within_conn(conn)

    @staticmethod
    def _compute_transaction_nature_within_conn(
        conn: sqlite3.Connection,
        transaction_id: int,
    ) -> str:
        """Berechnet die Domaenen-Natur exakt aus persistiertem Zustand."""
        linked = conn.execute(
            """
            SELECT 1
            FROM transfer_links
            WHERE outgoing_tx_id = ? OR incoming_tx_id = ?
            LIMIT 1
            """,
            (transaction_id, transaction_id),
        ).fetchone()
        if linked:
            return "internal_transfer"

        stmt_linked = conn.execute(
            """
            SELECT 1
            FROM statement_settlements
            WHERE transaction_id = ?
            LIMIT 1
            """,
            (transaction_id,),
        ).fetchone()
        if stmt_linked:
            return "internal_transfer"

        category_row = conn.execute(
            """
            SELECT c.kind AS kind
            FROM transaction_category tc
            JOIN categories c ON c.id = tc.category_id
            WHERE tc.transaction_id = ?
            ORDER BY tc.confidence DESC, c.id ASC
            LIMIT 1
            """,
            (transaction_id,),
        ).fetchone()
        if category_row and category_row["kind"] == "transfer":
            return "internal_transfer"
        return "ordinary"

    @staticmethod
    def _refresh_transaction_natures_within_conn(
        conn: sqlite3.Connection,
        transaction_ids: Sequence[int],
    ) -> None:
        unique_ids = sorted({int(tx_id) for tx_id in transaction_ids if int(tx_id) > 0})
        for tx_id in unique_ids:
            nature = FinanceDB._compute_transaction_nature_within_conn(conn, tx_id)
            if nature not in VALID_TRANSACTION_NATURES:
                raise ValueError(f"Unsupported transaction_nature computed: {nature!r}")
            conn.execute(
                "UPDATE transactions SET transaction_nature = ? WHERE id = ?",
                (nature, tx_id),
            )

    @staticmethod
    def _refresh_all_transaction_natures_within_conn(conn: sqlite3.Connection) -> None:
        rows = conn.execute("SELECT id FROM transactions").fetchall()
        FinanceDB._refresh_transaction_natures_within_conn(
            conn,
            [int(row["id"]) for row in rows],
        )

    @staticmethod
    def _non_transfer_clause(table_alias: str = "t") -> str:
        return (
            f"COALESCE({table_alias}.transaction_nature, 'ordinary') != 'internal_transfer' "
            f"AND {table_alias}.id NOT IN (SELECT outgoing_tx_id FROM transfer_links "
            f"UNION SELECT incoming_tx_id FROM transfer_links)"
        )

    @staticmethod
    def _transfer_leg_clause(table_alias: str = "t") -> str:
        """Logisches Komplement zu ``_non_transfer_clause``: exakt die internen
        Transfer-Beine derselben Person (verlinkte Ausgangs-/Eingangsbeine oder
        Nature 'internal_transfer', z. B. Kreditkarten-Settlements)."""
        return (
            f"(COALESCE({table_alias}.transaction_nature, 'ordinary') = 'internal_transfer' "
            f"OR {table_alias}.id IN (SELECT outgoing_tx_id FROM transfer_links "
            f"UNION SELECT incoming_tx_id FROM transfer_links))"
        )

    @staticmethod
    def _migrate_categories(conn: sqlite3.Connection) -> None:
        """Idempotente Spalten-Erweiterung für Bestands-DBs.

        ``CREATE TABLE IF NOT EXISTS`` wirkt nur auf neue DBs; existierende
        Tabellen brauchen ALTER TABLE ADD COLUMN. Wir lesen ``PRAGMA
        table_info`` und fgen nur fehlende Spalten an -- deterministisch
        und ohne Try/Except-Workaround.
        """
        cols = {row["name"] for row in conn.execute("PRAGMA table_info(categories)").fetchall()}
        if "kind" not in cols:
            conn.execute(
                "ALTER TABLE categories ADD COLUMN kind TEXT NOT NULL DEFAULT 'expense'"
            )
        if "color" not in cols:
            conn.execute("ALTER TABLE categories ADD COLUMN color TEXT")

        # Historische Datenkorrektur: In Altstaenden wurde die technische
        # Kategorie "Kreditkarte" teils als expense angelegt. Semantisch ist
        # sie Transfer (Abrechnungsausgleich), daher auf transfer umstellen.
        # Die Operation ist idempotent und betrifft exakt diese Kategorie.
        conn.execute(
            """
            UPDATE categories
            SET kind = 'transfer'
            WHERE lower(trim(name)) = 'kreditkarte'
              AND kind <> 'transfer'
            """
        )

        tx_rows = conn.execute(
            """
            SELECT DISTINCT tc.transaction_id AS id
            FROM transaction_category tc
            JOIN categories c ON c.id = tc.category_id
            WHERE lower(trim(c.name)) = 'kreditkarte'
            """
        ).fetchall()
        if tx_rows:
            FinanceDB._refresh_transaction_natures_within_conn(
                conn,
                [int(r["id"]) for r in tx_rows],
            )

    @staticmethod
    def _migrate_account_types(conn: sqlite3.Connection) -> None:
        """Idempotente Umbenennung veralteter (deutscher) account_type-Werte.

        Wenn ein Auszug mit dem alten Extraktions-Schema importiert wurde,
        steht in ``accounts.account_type`` ein deutscher Wert.  Diese
        Methode konvertiert genau die Zeilen, die noch einen Altwert tragen;
        Zeilen mit bereits gueltigen englischen Werten bleiben unveraendert.
        Da nur bekannte Altwerte umgeschrieben werden (exakter Vergleich,
        kein Pattern-Matching), ist die Operation idempotent und sicher.
        """
        for old, new in ACCOUNT_TYPE_MIGRATIONS.items():
            conn.execute(
                "UPDATE accounts SET account_type = ? WHERE account_type = ?",
                (new, old),
            )

        # Structural repair: valid IBAN-based accounts cannot be card-number
        # based credit-card identifiers. This fixes historical misclassifications
        # and is idempotent.
        rows = conn.execute(
            "SELECT id, iban, account_type FROM accounts WHERE account_type = 'credit_card'"
        ).fetchall()
        for row in rows:
            if _is_valid_iban(row["iban"]):
                conn.execute(
                    "UPDATE accounts SET account_type = 'checking' WHERE id = ?",
                    (int(row["id"]),),
                )

    # -- bank / account upsert ---------------------------------------

    def upsert_bank(
        self,
        name: str,
        bic: Optional[str] = None,
        country_code: Optional[str] = None,
    ) -> int:
        """Insert or fetch a bank by ``(name, bic)``. Returns ``bank_id``."""
        name_clean = (name or "").strip()
        bic_clean = (bic or "").strip().upper() or None
        country_clean = (country_code or "").strip().upper() or None
        if not name_clean:
            raise ValueError("Bank name is required")
        with self._lock, self._connect() as conn:
            return self._upsert_bank_within_conn(
                conn,
                name=name_clean,
                bic=bic_clean,
                country_code=country_clean,
            )

    def upsert_account(
        self,
        bank_id: int,
        iban: str,
        account_holder: Optional[str] = None,
        currency: str = DEFAULT_CURRENCY,
        account_type: Optional[str] = None,
    ) -> int:
        """Insert account or return existing by IBAN. Updates holder/type when missing."""
        iban_norm = _normalize_iban(iban)
        if not iban_norm:
            raise ValueError("IBAN is required")
        with self._lock, self._connect() as conn:
            return self._upsert_account_within_conn(
                conn,
                bank_id=bank_id,
                iban=iban_norm,
                account_holder=account_holder,
                currency=currency,
                account_type=account_type,
            )

    def set_account_type(self, account_id: int, account_type: Optional[str]) -> bool:
        """Setzt/aktualisiert den Konto-Typ. ``None`` lscht das Feld."""
        if account_type is not None and account_type not in VALID_ACCOUNT_TYPES:
            raise ValueError(
                f"account_type must be one of {sorted(VALID_ACCOUNT_TYPES)}, got {account_type!r}"
            )
        with self._lock, self._connect() as conn:
            cur = conn.execute(
                "UPDATE accounts SET account_type = ? WHERE id = ?",
                (account_type, account_id),
            )
            return cur.rowcount > 0

    # -- statement & transactions ------------------------------------

    def find_statement_by_pdf_hash(self, pdf_hash: str) -> Optional[Statement]:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM statements WHERE source_pdf_hash = ?", (pdf_hash,)
            ).fetchone()
            return self._row_to_statement(row) if row else None

    def find_account_id_by_iban(self, iban: str) -> Optional[int]:
        """Gibt die ``account_id`` zur normalisierten IBAN zurueck (oder None).

        Read-only Lookup (kein Upsert) -- sicher fuer den Import-Pfad, wo der
        Account spaeter ohnehin atomar angelegt wird.
        """
        iban_norm = _normalize_iban(iban or "")
        if not iban_norm:
            return None
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT id FROM accounts WHERE iban = ?", (iban_norm,)
            ).fetchone()
        return int(row["id"]) if row else None

    def get_prior_closing_balance(
        self,
        account_id: int,
        before_period: Optional[str],
        exclude_statement_id: Optional[int] = None,
    ) -> Optional[float]:
        """Schlussaldo (Hauptwaehrung) des naechsten vorhergehenden Statements
        desselben Kontos mit ``period_end < before_period`` -- oder None.

        Dient als ``prior_closing_balance`` fuer die Konsistenzpruefung
        (Saldo-Kette ueber Statements hinweg). Deterministisch: naechster nach
        ``period_end`` (absteigend), bei Gleichstand hoeheres ``id``.
        ``exclude_statement_id`` wird vom Suchraum ausgeschlossen, damit ein
        Statement (z.B. beim Repair) sich nicht selbst als Vorstatement
        betrachtet. Ohne Vorstatement/Periode: None -> die Pruefung wird
        konservativ als warning skipped (kein stiller Pass).
        """
        before = (before_period or "").strip() or None
        if before is None:
            return None
        with self._lock, self._connect() as conn:
            if exclude_statement_id is not None:
                row = conn.execute(
                    """
                    SELECT closing_balance_cents
                    FROM statements
                    WHERE account_id = ?
                      AND id != ?
                      AND period_end IS NOT NULL
                      AND period_end < ?
                      AND closing_balance_cents IS NOT NULL
                    ORDER BY period_end DESC, id DESC
                    LIMIT 1
                    """,
                    (account_id, exclude_statement_id, before),
                ).fetchone()
            else:
                row = conn.execute(
                    """
                    SELECT closing_balance_cents
                    FROM statements
                    WHERE account_id = ?
                      AND period_end IS NOT NULL
                      AND period_end < ?
                      AND closing_balance_cents IS NOT NULL
                    ORDER BY period_end DESC, id DESC
                    LIMIT 1
                    """,
                    (account_id, before),
                ).fetchone()
        if row is None:
            return None
        cents = row["closing_balance_cents"]
        return None if cents is None else cents / 100.0

    def get_statement(self, statement_id: int) -> Optional[Statement]:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM statements WHERE id = ?",
                (statement_id,),
            ).fetchone()
            return self._row_to_statement(row) if row else None

    def get_statement_transaction_count(self, statement_id: int) -> int:
        with self._lock, self._connect() as conn:
            return self._get_statement_transaction_count_within_conn(conn, statement_id)

    def delete_statement(self, statement_id: int) -> None:
        with self._lock, self._connect() as conn:
            self._delete_statement_within_conn(conn, statement_id)

    def delete_transaction(self, transaction_id: int) -> bool:
        """Delete a single transaction by id.

        Cascading FKs remove dependent rows in ``transaction_category`` and
        ``transfer_links`` automatically.
        """
        with self._lock, self._connect() as conn:
            self._delete_transaction_search_docs_within_conn(conn, [transaction_id])
            cur = conn.execute("DELETE FROM transactions WHERE id = ?", (transaction_id,))
            return cur.rowcount > 0

    def insert_statement(
        self,
        *,
        account_id: int,
        source_pdf_hash: str,
        source_filename: Optional[str],
        period_start: Optional[str],
        period_end: Optional[str],
        opening_balance: Optional[float],
        closing_balance: Optional[float],
        total_credits: Optional[float] = None,
        total_debits: Optional[float] = None,
        consistency_passed: Optional[bool] = None,
        consistency_errors: Optional[str] = None,
        needs_review: bool = False,
    ) -> int:
        with self._lock, self._connect() as conn:
            return self._insert_statement_within_conn(
                conn,
                account_id=account_id,
                source_pdf_hash=source_pdf_hash,
                source_filename=source_filename,
                period_start=period_start,
                period_end=period_end,
                opening_balance=opening_balance,
                closing_balance=closing_balance,
                total_credits=total_credits,
                total_debits=total_debits,
                consistency_passed=consistency_passed,
                consistency_errors=consistency_errors,
                needs_review=needs_review,
            )

    def insert_transactions(
        self,
        *,
        statement_id: int,
        account_id: int,
        transactions: Sequence[Dict[str, Any]],
    ) -> Tuple[int, int]:
        """Insert transactions with dedup. Returns ``(inserted, duplicates)``.

        Wendet nach dem Insert ``counterparty_rules`` an: jede neu
        eingefgte Buchung erhlt automatisch die Kategorie der zutreffenden
        Regel (Source ``'rule'``, Confidence 1.0). Das ist der nachhaltige
        Kern -- einmal kategorisiert, immer kategorisiert.
        """
        inserted = 0
        duplicates = 0
        new_tx_ids: List[int] = []
        with self._lock, self._connect() as conn:
            return self._insert_transactions_within_conn(
                conn,
                statement_id=statement_id,
                account_id=account_id,
                transactions=transactions,
            )

    def persist_statement_import(
        self,
        *,
        bank_name: str,
        bank_bic: Optional[str],
        bank_country_code: Optional[str],
        iban: str,
        account_holder: Optional[str],
        currency: str,
        account_type: Optional[str],
        source_pdf_hash: str,
        source_filename: Optional[str],
        period_start: Optional[str],
        period_end: Optional[str],
        opening_balance: Optional[float],
        closing_balance: Optional[float],
        total_credits: Optional[float] = None,
        total_debits: Optional[float] = None,
        consistency_passed: Optional[bool] = None,
        consistency_errors: Optional[str] = None,
        needs_review: bool = False,
        transactions: Sequence[Dict[str, Any]],
    ) -> Tuple[int, int, int, int, int]:
        """Persistiert einen vollstaendigen PDF-Import atomar.

        Returns ``(bank_id, account_id, statement_id, inserted, duplicates)``.
        """
        with self._lock, self._connect() as conn:
            bank_id = self._upsert_bank_within_conn(
                conn,
                name=(bank_name or "").strip(),
                bic=(bank_bic or "").strip().upper() or None,
                country_code=(bank_country_code or "").strip().upper() or None,
            )
            account_id = self._upsert_account_within_conn(
                conn,
                bank_id=bank_id,
                iban=_normalize_iban(iban),
                account_holder=account_holder,
                currency=currency,
                account_type=account_type,
            )
            statement_id = self._insert_statement_within_conn(
                conn,
                account_id=account_id,
                source_pdf_hash=source_pdf_hash,
                source_filename=source_filename,
                period_start=period_start,
                period_end=period_end,
                opening_balance=opening_balance,
                closing_balance=closing_balance,
                total_credits=total_credits,
                total_debits=total_debits,
                consistency_passed=consistency_passed,
                consistency_errors=consistency_errors,
                needs_review=needs_review,
            )
            inserted, duplicates = self._insert_transactions_within_conn(
                conn,
                statement_id=statement_id,
                account_id=account_id,
                transactions=transactions,
            )
            return bank_id, account_id, statement_id, inserted, duplicates

    @staticmethod
    def _upsert_bank_within_conn(
        conn: sqlite3.Connection,
        *,
        name: str,
        bic: Optional[str],
        country_code: Optional[str],
    ) -> int:
        row = conn.execute(
            "SELECT id FROM banks WHERE name = ? AND COALESCE(bic, '') = COALESCE(?, '')",
            (name, bic),
        ).fetchone()
        if row:
            return int(row["id"])
        cur = conn.execute(
            "INSERT INTO banks (name, bic, country_code) VALUES (?, ?, ?)",
            (name, bic, country_code),
        )
        return int(cur.lastrowid or 0)

    @staticmethod
    def _upsert_account_within_conn(
        conn: sqlite3.Connection,
        *,
        bank_id: int,
        iban: str,
        account_holder: Optional[str],
        currency: str,
        account_type: Optional[str],
    ) -> int:
        row = conn.execute(
            "SELECT id, account_holder, account_type FROM accounts WHERE iban = ?",
            (iban,),
        ).fetchone()
        if row:
            account_id = int(row["id"])
            updates: List[str] = []
            params: List[Any] = []
            if account_holder and not row["account_holder"]:
                updates.append("account_holder = ?")
                params.append(account_holder.strip())

            incoming_type = (account_type or "").strip() or None
            existing_type = (row["account_type"] or "").strip() or None
            reconciled_type = incoming_type
            if existing_type == "credit_card" and _is_valid_iban(iban):
                reconciled_type = "checking"

            if reconciled_type and not existing_type:
                updates.append("account_type = ?")
                params.append(reconciled_type)
            elif (
                reconciled_type
                and existing_type
                and reconciled_type != existing_type
                and existing_type == "credit_card"
                and reconciled_type == "checking"
            ):
                updates.append("account_type = ?")
                params.append(reconciled_type)

            if updates:
                params.append(account_id)
                conn.execute(
                    f"UPDATE accounts SET {', '.join(updates)} WHERE id = ?",
                    params,
                )
            return account_id
        cur = conn.execute(
            """
            INSERT INTO accounts (bank_id, iban, account_holder, currency, account_type)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                bank_id,
                iban,
                (account_holder or "").strip() or None,
                currency.upper(),
                (account_type or "").strip() or None,
            ),
        )
        return int(cur.lastrowid or 0)

    @staticmethod
    def _insert_statement_within_conn(
        conn: sqlite3.Connection,
        *,
        account_id: int,
        source_pdf_hash: str,
        source_filename: Optional[str],
        period_start: Optional[str],
        period_end: Optional[str],
        opening_balance: Optional[float],
        closing_balance: Optional[float],
        total_credits: Optional[float] = None,
        total_debits: Optional[float] = None,
        consistency_passed: Optional[bool] = None,
        consistency_errors: Optional[str] = None,
        needs_review: bool = False,
    ) -> int:
        cur = conn.execute(
            """
            INSERT INTO statements (
                account_id, period_start, period_end,
                opening_balance_cents, closing_balance_cents,
                total_credits_cents, total_debits_cents,
                consistency_passed, consistency_errors, needs_review,
                source_pdf_hash, source_filename
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                account_id,
                period_start,
                period_end,
                _to_cents(opening_balance) if opening_balance is not None else None,
                _to_cents(closing_balance) if closing_balance is not None else None,
                _to_cents(total_credits) if total_credits is not None else None,
                _to_cents(total_debits) if total_debits is not None else None,
                None if consistency_passed is None else int(bool(consistency_passed)),
                consistency_errors,
                int(bool(needs_review)),
                source_pdf_hash,
                source_filename,
            ),
        )
        return int(cur.lastrowid or 0)

    def _insert_transactions_within_conn(
        self,
        conn: sqlite3.Connection,
        *,
        statement_id: int,
        account_id: int,
        transactions: Sequence[Dict[str, Any]],
    ) -> Tuple[int, int]:
        inserted = 0
        duplicates = 0
        new_tx_ids: List[int] = []
        for tx in transactions:
            amount_cents = _to_cents(float(tx["amount"]))
            booking_date = tx["booking_date"]
            if isinstance(booking_date, (date, datetime)):
                booking_date = booking_date.strftime("%Y-%m-%d")
            booking_date = str(booking_date).strip() if booking_date is not None else ""
            if not booking_date:
                raise ValueError(
                    "Invalid transaction payload: booking_date is missing or empty"
                )
            value_date = tx.get("value_date")
            if isinstance(value_date, (date, datetime)):
                value_date = value_date.strftime("%Y-%m-%d")
            value_date = str(value_date).strip() if value_date is not None else None
            dedup_hash = _tx_dedup_hash(
                account_id,
                booking_date,
                amount_cents,
                tx.get("counterparty"),
                tx.get("purpose"),
            )
            exists = conn.execute(
                "SELECT 1 FROM transactions WHERE dedup_hash = ?", (dedup_hash,)
            ).fetchone()
            if exists:
                duplicates += 1
                continue
            cur = conn.execute(
                """
                INSERT INTO transactions (
                    statement_id, account_id, booking_date, value_date,
                    amount_cents, currency, counterparty, counterparty_iban,
                    purpose, booking_type, transaction_nature, raw_text, dedup_hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    statement_id,
                    account_id,
                    booking_date,
                    value_date,
                    amount_cents,
                    (tx.get("currency") or DEFAULT_CURRENCY).upper(),
                    (tx.get("counterparty") or "").strip() or None,
                    _normalize_iban(tx["counterparty_iban"]) if tx.get("counterparty_iban") else None,
                    (tx.get("purpose") or "").strip() or None,
                    (tx.get("booking_type") or "").strip() or None,
                    "ordinary",
                    tx.get("raw_text"),
                    dedup_hash,
                ),
            )
            new_id = int(cur.lastrowid or 0)
            if new_id:
                new_tx_ids.append(new_id)
            inserted += 1
        if new_tx_ids:
            self._apply_rules_within_conn(conn, new_tx_ids)
            self._auto_detect_transfers_within_conn(conn, new_tx_ids)
            self._auto_detect_card_settlements_within_conn(conn, transaction_ids=new_tx_ids)

            account_row = conn.execute(
                "SELECT account_type FROM accounts WHERE id = ?",
                (account_id,),
            ).fetchone()
            # Wenn ein Kreditkarten-Statement neu importiert wurde, kann die
            # passende Sammelbelastung bereits auf dem Girokonto existieren.
            # Dann einmal global nach offenen Ausgleichs-Tx suchen.
            if account_row and (account_row["account_type"] or "") == "credit_card":
                self._auto_detect_card_settlements_within_conn(conn)
            self._refresh_transaction_natures_within_conn(conn, new_tx_ids)
            self._refresh_transaction_search_docs_within_conn(conn, new_tx_ids)
        return inserted, duplicates

    @staticmethod
    def _get_statement_transaction_count_within_conn(
        conn: sqlite3.Connection, statement_id: int
    ) -> int:
        row = conn.execute(
            "SELECT COUNT(*) AS cnt FROM transactions WHERE statement_id = ?",
            (statement_id,),
        ).fetchone()
        return int(row["cnt"] if row else 0)

    @staticmethod
    def _delete_statement_within_conn(conn: sqlite3.Connection, statement_id: int) -> None:
        tx_rows = conn.execute(
            "SELECT id FROM transactions WHERE statement_id = ?",
            (statement_id,),
        ).fetchall()
        if tx_rows:
            FinanceDB._delete_transaction_search_docs_within_conn(
                conn,
                [int(row["id"]) for row in tx_rows],
            )
        conn.execute("DELETE FROM statements WHERE id = ?", (statement_id,))

    # -- queries -----------------------------------------------------

    def list_banks(self) -> List[Bank]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT id, name, bic, country_code FROM banks ORDER BY name"
            ).fetchall()
            return [
                Bank(id=int(r["id"]), name=r["name"], bic=r["bic"], country_code=r["country_code"])
                for r in rows
            ]

    def list_accounts(self) -> List[Account]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """
                SELECT a.id, a.bank_id, a.iban, a.account_holder, a.currency,
                       a.account_type, b.name AS bank_name
                FROM accounts a
                LEFT JOIN banks b ON b.id = a.bank_id
                ORDER BY b.name, a.iban
                """
            ).fetchall()
            return [
                Account(
                    id=int(r["id"]),
                    bank_id=int(r["bank_id"]),
                    iban=r["iban"],
                    account_holder=r["account_holder"],
                    currency=r["currency"],
                    account_type=r["account_type"],
                    bank_name=r["bank_name"],
                )
                for r in rows
            ]

    def query_transactions(
        self,
        *,
        account_id: Optional[int] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        counterparty_like: Optional[str] = None,
        category: Optional[str] = None,
        limit: int = 500,
    ) -> List[Transaction]:
        clauses: List[str] = []
        params: List[Any] = []
        if account_id is not None:
            clauses.append("t.account_id = ?")
            params.append(account_id)
        if start_date:
            clauses.append("t.booking_date >= ?")
            params.append(start_date)
        if end_date:
            clauses.append("t.booking_date <= ?")
            params.append(end_date)
        needle = _normalize_like_needle(counterparty_like)
        if needle:
            clauses.append(
                "(" 
                "LOWER(COALESCE(t.counterparty, '')) LIKE ? "
                "OR LOWER(COALESCE(t.purpose, '')) LIKE ? "
                "OR EXISTS ("
                "SELECT 1 FROM transaction_category tc "
                "JOIN categories c ON c.id = tc.category_id "
                "WHERE tc.transaction_id = t.id "
                "AND LOWER(COALESCE(c.name, '')) LIKE ?"
                ")"
                ")"
            )
            like = f"%{needle.lower()}%"
            params.extend([like, like, like])
        if category:
            clauses.append(
                "EXISTS (SELECT 1 FROM transaction_category tc "
                "JOIN categories c ON c.id = tc.category_id "
                "WHERE tc.transaction_id = t.id AND c.name = ?)"
            )
            params.append(category)
        where_clause = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        sql = f"""
            SELECT t.*, (
                SELECT c.name FROM transaction_category tc
                JOIN categories c ON c.id = tc.category_id
                WHERE tc.transaction_id = t.id
                ORDER BY tc.confidence DESC LIMIT 1
            ) AS category
            FROM transactions t
            {where_clause}
            ORDER BY t.booking_date DESC, t.id DESC
            LIMIT ?
        """
        params.append(limit)
        with self._lock, self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
            return [self._row_to_transaction(r) for r in rows]

    def search_transactions_text(
        self,
        *,
        query_text: str,
        account_id: Optional[int] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        limit: int = 500,
        include_transfers: bool = False,
    ) -> List[Dict[str, Any]]:
        normalized_query = _normalize_search_text(query_text)
        if not normalized_query:
            raise ValueError("query_text must be non-empty")

        import numpy as np
        from utils.embedding_singleton import EmbeddingSingleton

        safe_limit = max(1, min(int(limit or 500), 2000))
        candidate_limit = max(200, min(8000, safe_limit * 8))
        clauses: List[str] = []
        params: List[Any] = []
        if account_id is not None:
            clauses.append("t.account_id = ?")
            params.append(account_id)
        if start_date:
            clauses.append("t.booking_date >= ?")
            params.append(start_date)
        if end_date:
            clauses.append("t.booking_date <= ?")
            params.append(end_date)
        if not include_transfers:
            clauses.append(self._non_transfer_clause("t"))
        where_clause = ("WHERE " + " AND ".join(clauses)) if clauses else ""

        category_sql = (
            "(SELECT c.name FROM transaction_category tc "
            "JOIN categories c ON c.id = tc.category_id "
            "WHERE tc.transaction_id = t.id "
            "ORDER BY tc.confidence DESC LIMIT 1)"
        )

        fts_query = self._build_fts5_query(normalized_query)
        like_query = f"%{normalized_query}%"
        lexical_sql = f"""
            SELECT
                t.*,
                {category_sql} AS category,
                docs.search_text,
                docs.search_text_norm,
                bm25(transaction_search_fts) AS bm25_score,
                CASE WHEN docs.search_text_norm LIKE ? THEN 1 ELSE 0 END AS phrase_match
            FROM transaction_search_fts
            JOIN transaction_search_docs docs ON docs.transaction_id = transaction_search_fts.transaction_id
            JOIN transactions t ON t.id = docs.transaction_id
            {where_clause}
              AND transaction_search_fts MATCH ?
            ORDER BY phrase_match DESC, bm25_score ASC, ABS(t.amount_cents) DESC, t.booking_date DESC
            LIMIT ?
        """
        fallback_sql = f"""
            SELECT
                t.*,
                {category_sql} AS category,
                docs.search_text,
                docs.search_text_norm,
                999999.0 AS bm25_score,
                CASE WHEN docs.search_text_norm LIKE ? THEN 1 ELSE 0 END AS phrase_match
            FROM transaction_search_docs docs
            JOIN transactions t ON t.id = docs.transaction_id
            {where_clause}
              AND docs.search_text_norm LIKE ?
            ORDER BY phrase_match DESC, ABS(t.amount_cents) DESC, t.booking_date DESC
            LIMIT ?
        """
        semantic_sql = f"""
            SELECT
                t.*,
                {category_sql} AS category,
                docs.search_text,
                docs.search_text_norm,
                emb.embedding_blob,
                emb.embedding_dim,
                emb.model_name,
                CASE WHEN docs.search_text_norm LIKE ? THEN 1 ELSE 0 END AS phrase_match
            FROM transaction_search_embeddings emb
            JOIN transaction_search_docs docs ON docs.transaction_id = emb.transaction_id
            JOIN transactions t ON t.id = emb.transaction_id
            {where_clause}
            ORDER BY ABS(t.amount_cents) DESC, t.booking_date DESC
            LIMIT ?
        """

        lexical_by_id: Dict[int, Dict[str, Any]] = {}
        semantic_rows: List[sqlite3.Row] = []
        with self._lock, self._connect() as conn:
            lexical_rows = conn.execute(
                lexical_sql,
                [like_query, *params, fts_query, candidate_limit],
            ).fetchall()
            if not lexical_rows:
                lexical_rows = conn.execute(
                    fallback_sql,
                    [like_query, *params, like_query, candidate_limit],
                ).fetchall()
            for rank, row in enumerate(lexical_rows, start=1):
                lexical_by_id[int(row["id"])] = {"row": row, "lexical_rank": rank}

            semantic_rows = conn.execute(
                semantic_sql,
                [like_query, *params, candidate_limit],
            ).fetchall()

        semantic_scored: List[Dict[str, Any]] = []
        try:
            model = EmbeddingSingleton()
            if model.is_loaded() or model.load_model():
                query_embedding = model.encode([normalized_query], batch_size=1, defer_cache_cleanup=True)[0].astype("float32", copy=False)

                for row in semantic_rows:
                    tx_id = int(row["id"])
                    emb = np.frombuffer(row["embedding_blob"], dtype=np.float32)
                    if emb.size != int(row["embedding_dim"]):
                        logger.warning(
                            "Skipping corrupt finance search embedding for transaction_id=%s",
                            tx_id,
                        )
                        continue
                    similarity = float(np.dot(query_embedding, emb))
                    semantic_scored.append({"row": row, "semantic_score": similarity})

                model.flush_cuda_cache()
            else:
                logger.warning(
                    "Embedding model unavailable for finance search query; falling back to lexical ranking only"
                )
        except Exception as exc:
            logger.warning(
                "Semantic finance search failed (%s); falling back to lexical ranking only",
                exc,
            )
            semantic_scored = []

        semantic_scored.sort(
            key=lambda item: (
                item["semantic_score"],
                bool(item["row"]["phrase_match"]),
                abs(int(item["row"]["amount_cents"])),
                str(item["row"]["booking_date"]),
            ),
            reverse=True,
        )

        fused: Dict[int, Dict[str, Any]] = {}
        for rank, item in enumerate(semantic_scored, start=1):
            row = item["row"]
            tx_id = int(row["id"])
            fused[tx_id] = {
                "row": row,
                "semantic_rank": rank,
                "semantic_score": float(item["semantic_score"]),
                "lexical_rank": None,
            }

        for tx_id, info in lexical_by_id.items():
            entry = fused.setdefault(
                tx_id,
                {
                    "row": info["row"],
                    "semantic_rank": None,
                    "semantic_score": 0.0,
                    "lexical_rank": None,
                },
            )
            entry["lexical_rank"] = int(info["lexical_rank"])

        ranked: List[Dict[str, Any]] = []
        for tx_id, item in fused.items():
            lexical_rank = item["lexical_rank"]
            semantic_rank = item["semantic_rank"]
            row = item["row"]
            phrase_match = bool(row["phrase_match"])
            rrf = 0.0
            if lexical_rank is not None:
                rrf += 1.0 / (60.0 + lexical_rank)
            if semantic_rank is not None:
                rrf += 1.0 / (60.0 + semantic_rank)
            fused_score = rrf + (0.20 if phrase_match else 0.0) + max(0.0, item["semantic_score"]) * 0.35
            ranked.append(
                {
                    "transaction_id": tx_id,
                    "row": row,
                    "semantic_score": float(item["semantic_score"]),
                    "lexical_rank": lexical_rank,
                    "semantic_rank": semantic_rank,
                    "phrase_match": phrase_match,
                    "fused_score": fused_score,
                }
            )

        ranked.sort(
            key=lambda item: (
                item["fused_score"],
                item["phrase_match"],
                abs(int(item["row"]["amount_cents"])),
                str(item["row"]["booking_date"]),
            ),
            reverse=True,
        )

        return [
            {
                "transaction": self._row_to_transaction(item["row"]),
                "semantic_score": round(float(item["semantic_score"]), 6),
                "fused_score": round(float(item["fused_score"]), 6),
                "phrase_match": bool(item["phrase_match"]),
                "lexical_rank": item["lexical_rank"],
                "semantic_rank": item["semantic_rank"],
                "match_source": (
                    "hybrid"
                    if item["lexical_rank"] is not None and item["semantic_rank"] is not None
                    else "semantic"
                    if item["semantic_rank"] is not None
                    else "lexical"
                ),
                "search_text": item["row"]["search_text"],
            }
            for item in ranked[:safe_limit]
        ]

    def list_transaction_currencies(self, account_id: Optional[int] = None) -> List[str]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT DISTINCT currency FROM transactions "
                "WHERE (? IS NULL OR account_id = ?) ORDER BY currency",
                (account_id, account_id),
            ).fetchall()
        return [str(row["currency"]) for row in rows]

    def aggregate(
        self,
        *,
        group_by: str,  # 'month' | 'category' | 'counterparty' | 'account'
        account_id: Optional[int] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        include_transfers: bool = False,
        currency: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Return aggregated sums grouped by the requested dimension.

        Result row format: ``{"key": str, "currency": str, "income_cents": int,
        "expense_cents": int, "net_cents": int, "count": int}``.

        ``include_transfers=False`` (default) blendet beide Seiten von
        ``transfer_links`` aus -- ntig, damit Kreditkarten-Sammelbelastungen
        nicht doppelt gegen die Einzelumstze gezhlt werden.
        """
        group_clauses = {
            "month": "substr(t.booking_date, 1, 7)",
            "account": "a.iban",
            "counterparty": "COALESCE(t.counterparty, '(unknown)')",
            "category": (
                "COALESCE((SELECT c.name FROM transaction_category tc "
                "JOIN categories c ON c.id = tc.category_id "
                "WHERE tc.transaction_id = t.id "
                "ORDER BY tc.confidence DESC LIMIT 1), '(uncategorized)')"
            ),
        }
        if group_by not in group_clauses:
            raise ValueError(
                f"group_by must be one of {list(group_clauses)}, got {group_by!r}"
            )
        group_expr = group_clauses[group_by]

        clauses: List[str] = []
        params: List[Any] = []
        if account_id is not None:
            clauses.append("t.account_id = ?")
            params.append(account_id)
        if start_date:
            clauses.append("t.booking_date >= ?")
            params.append(start_date)
        if end_date:
            clauses.append("t.booking_date <= ?")
            params.append(end_date)
        if currency:
            clauses.append("t.currency = ?")
            params.append(currency.upper())
        if not include_transfers:
            clauses.append(self._non_transfer_clause("t"))
        where_clause = ("WHERE " + " AND ".join(clauses)) if clauses else ""

        sql = f"""
            SELECT
                {group_expr} AS key,
                t.currency AS currency,
                SUM(CASE WHEN t.amount_cents > 0 THEN t.amount_cents ELSE 0 END) AS income_cents,
                SUM(CASE WHEN t.amount_cents < 0 THEN t.amount_cents ELSE 0 END) AS expense_cents,
                SUM(t.amount_cents) AS net_cents,
                COUNT(*) AS cnt
            FROM transactions t
            JOIN accounts a ON a.id = t.account_id
            {where_clause}
            GROUP BY {group_expr}, t.currency
            ORDER BY key, t.currency
        """
        with self._lock, self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
            return [
                {
                    "key": r["key"],
                    "currency": r["currency"],
                    "income_cents": int(r["income_cents"] or 0),
                    "expense_cents": int(r["expense_cents"] or 0),
                    "net_cents": int(r["net_cents"] or 0),
                    "count": int(r["cnt"]),
                }
                for r in rows
            ]

    def list_analysis_facts(
        self,
        *,
        account_id: Optional[int] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        include_transfers: bool = False,
    ) -> List[Dict[str, Any]]:
        """Return transaction facts used by deterministic Finance analytics."""
        clauses: List[str] = []
        params: List[Any] = []
        if account_id is not None:
            clauses.append("t.account_id = ?")
            params.append(account_id)
        if start_date:
            clauses.append("t.booking_date >= ?")
            params.append(start_date)
        if end_date:
            clauses.append("t.booking_date <= ?")
            params.append(end_date)
        if not include_transfers:
            clauses.append(self._non_transfer_clause("t"))
        where_clause = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        sql = f"""
            SELECT
                t.id AS transaction_id,
                t.booking_date,
                substr(t.booking_date, 1, 7) AS month,
                t.amount_cents,
                t.currency,
                COALESCE(t.counterparty, '(unknown)') AS counterparty,
                COALESCE((
                    SELECT c.name
                    FROM transaction_category tc
                    JOIN categories c ON c.id = tc.category_id
                    WHERE tc.transaction_id = t.id
                    ORDER BY tc.confidence DESC
                    LIMIT 1
                ), '(uncategorized)') AS category,
                -- 2026-09-13: Ziel-Zuordnung (SSOT goal_contributions; 0 = keine).
                COALESCE((
                    SELECT gc.goal_id FROM goal_contributions gc
                    WHERE gc.transaction_id = t.id
                    ORDER BY gc.id
                    LIMIT 1
                ), 0) AS goal_id
            FROM transactions t
            {where_clause}
            ORDER BY t.booking_date, t.id
        """
        with self._lock, self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
            return [
                {
                    "transaction_id": int(row["transaction_id"]),
                    "booking_date": str(row["booking_date"]),
                    "month": str(row["month"]),
                    "amount_cents": int(row["amount_cents"]),
                    "currency": str(row["currency"]),
                    "counterparty": str(row["counterparty"]),
                    "category": str(row["category"]),
                    "goal_id": int(row["goal_id"] or 0),
                }
                for row in rows
            ]

    def summarize_counterparty_costs(
        self,
        *,
        counterparty_like: str,
        account_id: Optional[int] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        include_transfers: bool = False,
    ) -> Dict[str, Any]:
        """Summiert Ausgaben/Refunds fuer eine Gegenseite im Zeitraum.

        Filtert auf Gegenseite ODER Verwendungszweck per LIKE-Substring,
        damit auch Buchungen ohne saubere Counterparty-Zeile erfasst werden.
        """
        needle = _normalize_like_needle(counterparty_like)
        if not needle:
            raise ValueError("counterparty_like must be non-empty")

        clauses: List[str] = [
            "(" 
            "LOWER(COALESCE(t.counterparty, '')) LIKE ? "
            "OR LOWER(COALESCE(t.purpose, '')) LIKE ? "
            "OR EXISTS ("
            "SELECT 1 FROM transaction_category tc "
            "JOIN categories c ON c.id = tc.category_id "
            "WHERE tc.transaction_id = t.id "
            "AND LOWER(COALESCE(c.name, '')) LIKE ?"
            ")"
            ")",
        ]
        like = f"%{needle.lower()}%"
        params: List[Any] = [like, like, like]

        if account_id is not None:
            clauses.append("t.account_id = ?")
            params.append(account_id)
        if start_date:
            clauses.append("t.booking_date >= ?")
            params.append(start_date)
        if end_date:
            clauses.append("t.booking_date <= ?")
            params.append(end_date)
        if not include_transfers:
            clauses.append(self._non_transfer_clause("t"))

        where_clause = "WHERE " + " AND ".join(clauses)

        sql_totals = f"""
            SELECT
                COALESCE(SUM(CASE WHEN t.amount_cents < 0 THEN -t.amount_cents ELSE 0 END), 0) AS expense_abs_cents,
                COALESCE(SUM(CASE WHEN t.amount_cents > 0 THEN t.amount_cents ELSE 0 END), 0) AS refund_cents,
                COALESCE(SUM(t.amount_cents), 0) AS net_cents,
                COUNT(*) AS cnt
            FROM transactions t
            {where_clause}
        """

        sql_currency = f"""
            SELECT
                COALESCE(t.currency, '{DEFAULT_CURRENCY}') AS currency,
                COALESCE(SUM(CASE WHEN t.amount_cents < 0 THEN -t.amount_cents ELSE 0 END), 0) AS expense_abs_cents,
                COALESCE(SUM(CASE WHEN t.amount_cents > 0 THEN t.amount_cents ELSE 0 END), 0) AS refund_cents,
                COALESCE(SUM(t.amount_cents), 0) AS net_cents,
                COUNT(*) AS cnt
            FROM transactions t
            {where_clause}
            GROUP BY COALESCE(t.currency, '{DEFAULT_CURRENCY}')
            ORDER BY cnt DESC
        """

        with self._lock, self._connect() as conn:
            totals = conn.execute(sql_totals, params).fetchone()
            by_currency = conn.execute(sql_currency, params).fetchall()

        return {
            "counterparty_like": needle,
            "expense_abs_cents": int(totals["expense_abs_cents"] or 0),
            "refund_cents": int(totals["refund_cents"] or 0),
            "net_cents": int(totals["net_cents"] or 0),
            "tx_count": int(totals["cnt"] or 0),
            "by_currency": [
                {
                    "currency": r["currency"],
                    "expense_abs_cents": int(r["expense_abs_cents"] or 0),
                    "refund_cents": int(r["refund_cents"] or 0),
                    "net_cents": int(r["net_cents"] or 0),
                    "tx_count": int(r["cnt"] or 0),
                }
                for r in by_currency
            ],
        }

    def top_counterparty_expenses(
        self,
        *,
        account_id: Optional[int] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        limit: int = 5,
        include_transfers: bool = False,
    ) -> List[Dict[str, Any]]:
        """Liefert Top-Gegenseiten nach absoluten Ausgaben (nur negative Buchungen)."""
        safe_limit = max(1, min(int(limit or 5), 100))

        clauses: List[str] = ["t.amount_cents < 0"]
        params: List[Any] = []

        if account_id is not None:
            clauses.append("t.account_id = ?")
            params.append(account_id)
        if start_date:
            clauses.append("t.booking_date >= ?")
            params.append(start_date)
        if end_date:
            clauses.append("t.booking_date <= ?")
            params.append(end_date)
        if not include_transfers:
            clauses.append(self._non_transfer_clause("t"))

        where_clause = "WHERE " + " AND ".join(clauses)
        sql = f"""
            SELECT
                COALESCE(t.counterparty, '(unknown)') AS counterparty,
                COALESCE(SUM(-t.amount_cents), 0) AS expense_abs_cents,
                COUNT(*) AS cnt,
                COALESCE(MAX(t.currency), '{DEFAULT_CURRENCY}') AS currency
            FROM transactions t
            {where_clause}
            GROUP BY counterparty
            HAVING expense_abs_cents > 0
            ORDER BY expense_abs_cents DESC, cnt DESC, counterparty ASC
            LIMIT ?
        """
        params.append(safe_limit)

        with self._lock, self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()

        return [
            {
                "counterparty": r["counterparty"],
                "expense_abs_cents": int(r["expense_abs_cents"] or 0),
                "tx_count": int(r["cnt"] or 0),
                "currency": r["currency"],
            }
            for r in rows
        ]

    def balance_at(self, account_id: int, as_of_date: str) -> Dict[str, Any]:
        """Return cumulative balance up to ``as_of_date`` (inclusive).

        Combines the most recent statement's opening balance with the sum of
        all subsequent transactions. Falls back to pure transaction sum when
        no statement-derived opening balance is available.

        Semantik (konsistent mit ``consistency.py`` und
        ``test_finance_reconciliation``): ``opening_balance`` ist der Saldo
        VOR ``period_start`` (Anfangssaldo), alle Buchungen der Periode --
        einschliesslich ``booking_date == period_start`` -- schlagen darauf
        auf (``opening + Summe(Periode) = closing``). Deshalb ``>=`` am
        Anker-Tag: eine Buchung am ersten Auszugstag ist Teil des Auszugs
        und darf nicht vom Anker "verschluckt" werden.
        """
        with self._lock, self._connect() as conn:
            stmt_row = conn.execute(
                """
                SELECT period_start, opening_balance_cents
                FROM statements
                WHERE account_id = ?
                  AND opening_balance_cents IS NOT NULL
                  AND COALESCE(period_start, '') <= ?
                ORDER BY period_start DESC LIMIT 1
                """,
                (account_id, as_of_date),
            ).fetchone()
            if stmt_row and stmt_row["opening_balance_cents"] is not None:
                anchor_date = stmt_row["period_start"] or "0001-01-01"
                anchor_cents = int(stmt_row["opening_balance_cents"])
                tx_row = conn.execute(
                    """
                    SELECT COALESCE(SUM(amount_cents), 0) AS s
                    FROM transactions
                    WHERE account_id = ? AND booking_date >= ? AND booking_date <= ?
                    """,
                    (account_id, anchor_date, as_of_date),
                ).fetchone()
                total_cents = anchor_cents + int(tx_row["s"])
            else:
                row = conn.execute(
                    """
                    SELECT COALESCE(SUM(amount_cents), 0) AS s
                    FROM transactions
                    WHERE account_id = ? AND booking_date <= ?
                    """,
                    (account_id, as_of_date),
                ).fetchone()
                total_cents = int(row["s"])
            return {
                "account_id": account_id,
                "as_of_date": as_of_date,
                "balance_cents": total_cents,
                "balance": _from_cents(total_cents),
            }

    def effective_balance_at(self, account_id: int, as_of_date: str) -> Dict[str, Any]:
        """Legacy-Analysewert: ``balance_at`` minus interne Transfer-Beine.

        Kein realer Konto- oder Haushaltsgesamtstand. Kontostandsanzeige,
        Guthaben-Prognose und Reconcile verwenden ``balance_at``. Die
        Ausschlussmenge ist das Komplement von ``_non_transfer_clause``.

        Das Ergebnis enthaelt ``internal_transfer_net_cents`` (Summe der
        herausgerechneten Beine im Kontokontext), damit Konsumenten die
        Anpassung sichtbar melden koennen.
        """
        base = self.balance_at(account_id, as_of_date)
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT COALESCE(SUM(t.amount_cents), 0) AS s "
                "FROM transactions t "
                f"WHERE t.account_id = ? AND t.booking_date <= ? AND "
                + self._transfer_leg_clause("t"),
                (account_id, as_of_date),
            ).fetchone()
        transfer_net_cents = int(row["s"]) if row and row["s"] is not None else 0
        total_cents = int(base["balance_cents"]) - transfer_net_cents
        return {
            "account_id": account_id,
            "as_of_date": as_of_date,
            "balance_cents": total_cents,
            "balance": _from_cents(total_cents),
            "internal_transfer_net_cents": transfer_net_cents,
        }

    # -- categories --------------------------------------------------

    def upsert_category(
        self,
        name: str,
        *,
        parent_id: Optional[int] = None,
        kind: str = "expense",
        color: Optional[str] = None,
        overwrite_kind: bool = True,
    ) -> int:
        name_clean = (name or "").strip()
        if not name_clean:
            raise ValueError("Category name is required")
        if kind not in ("expense", "income", "transfer"):
            raise ValueError(f"kind must be expense|income|transfer, got {kind!r}")
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT id FROM categories WHERE name = ?", (name_clean,)).fetchone()
            if row:
                cid = int(row["id"])
                conn.execute(
                    "UPDATE categories SET parent_id = COALESCE(?, parent_id), "
                    "kind = CASE WHEN ? THEN ? ELSE kind END, "
                    "color = COALESCE(?, color) WHERE id = ?",
                    (parent_id, overwrite_kind, kind, color, cid),
                )
                return cid
            cur = conn.execute(
                "INSERT INTO categories (name, parent_id, kind, color) VALUES (?, ?, ?, ?)",
                (name_clean, parent_id, kind, color),
            )
            return int(cur.lastrowid or 0)

    def list_categories(self) -> List[Category]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT id, name, parent_id, kind, color FROM categories ORDER BY name"
            ).fetchall()
            return [
                Category(
                    id=int(r["id"]),
                    name=r["name"],
                    parent_id=int(r["parent_id"]) if r["parent_id"] is not None else None,
                    kind=r["kind"],
                    color=r["color"],
                )
                for r in rows
            ]

    def delete_category(self, category_id: int) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("DELETE FROM categories WHERE id = ?", (category_id,))

    def assign_category(
        self,
        transaction_id: int,
        category_id: int,
        *,
        confidence: float = 1.0,
        source: str = "user",
    ) -> None:
        if source not in ("user", "llm", "rule"):
            raise ValueError(f"source must be user|llm|rule, got {source!r}")
        with self._lock, self._connect() as conn:
            # Ein einziges Kategorie-Assignment pro Transaktion -- alte ersetzen
            conn.execute(
                "DELETE FROM transaction_category WHERE transaction_id = ?",
                (transaction_id,),
            )
            conn.execute(
                """
                INSERT INTO transaction_category (transaction_id, category_id, confidence, source)
                VALUES (?, ?, ?, ?)
                """,
                (transaction_id, category_id, confidence, source),
            )
            self._refresh_transaction_natures_within_conn(conn, [transaction_id])
            self._refresh_transaction_search_docs_within_conn(conn, [transaction_id])

    def unassign_category(self, transaction_id: int) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                "DELETE FROM transaction_category WHERE transaction_id = ?",
                (transaction_id,),
            )
            self._refresh_transaction_natures_within_conn(conn, [transaction_id])
            self._refresh_transaction_search_docs_within_conn(conn, [transaction_id])

    # -- counterparty rules ------------------------------------------

    def upsert_rule(
        self,
        *,
        category_id: int,
        match_iban: Optional[str] = None,
        match_counterparty: Optional[str] = None,
    ) -> int:
        """Speichert eine deterministische Auto-Kategorisierungs-Regel.

        Genau einer der beiden Match-Schlssel muss gesetzt sein. UPSERT-
        Verhalten: identische Match-Bedingung berschreibt die category_id
        (User darf seine Regel ndern).
        """
        iban_norm = _normalize_iban(match_iban) if match_iban else None
        cp_norm = _normalize_counterparty(match_counterparty)
        if not iban_norm and not cp_norm:
            raise ValueError("Either match_iban or match_counterparty must be set")
        if iban_norm and cp_norm:
            raise ValueError("Provide exactly one of match_iban / match_counterparty")
        with self._lock, self._connect() as conn:
            if iban_norm:
                row = conn.execute(
                    "SELECT id FROM counterparty_rules WHERE match_iban = ?", (iban_norm,)
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT id FROM counterparty_rules WHERE match_counterparty = ?", (cp_norm,)
                ).fetchone()
            if row:
                rid = int(row["id"])
                conn.execute(
                    "UPDATE counterparty_rules SET category_id = ? WHERE id = ?",
                    (category_id, rid),
                )
                return rid
            cur = conn.execute(
                "INSERT INTO counterparty_rules (match_iban, match_counterparty, category_id) "
                "VALUES (?, ?, ?)",
                (iban_norm, cp_norm, category_id),
            )
            return int(cur.lastrowid or 0)

    def list_rules(self) -> List[CounterpartyRule]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """
                SELECT r.id, r.match_iban, r.match_counterparty, r.category_id,
                       c.name AS category_name
                FROM counterparty_rules r
                LEFT JOIN categories c ON c.id = r.category_id
                ORDER BY c.name, r.match_counterparty, r.match_iban
                """
            ).fetchall()
            return [
                CounterpartyRule(
                    id=int(r["id"]),
                    match_iban=r["match_iban"],
                    match_counterparty=r["match_counterparty"],
                    category_id=int(r["category_id"]),
                    category_name=r["category_name"],
                )
                for r in rows
            ]

    def delete_rule(self, rule_id: int) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("DELETE FROM counterparty_rules WHERE id = ?", (rule_id,))

    def apply_rules(
        self,
        *,
        only_uncategorized: bool = True,
        account_id: Optional[int] = None,
    ) -> int:
        """Wendet alle Regeln auf existierende Buchungen an.

        Per Default nur auf noch unkategorisierte Buchungen, damit User-
        Zuweisungen nicht berschrieben werden. Mit ``only_uncategorized=
        False`` werden auch bestehende ``source IN ('llm','rule')``-
        Zuweisungen ersetzt; explizite ``source='user'``-Zuweisungen
        bleiben dennoch unangetastet.
        """
        with self._lock, self._connect() as conn:
            tx_ids = self._collect_target_tx_ids(
                conn,
                only_uncategorized=only_uncategorized,
                account_id=account_id,
            )
            if not tx_ids:
                return 0
            applied = self._apply_rules_within_conn(conn, tx_ids)
            if applied:
                self._refresh_transaction_search_docs_within_conn(conn, tx_ids)
            return applied

    @staticmethod
    def _collect_target_tx_ids(
        conn: sqlite3.Connection,
        *,
        only_uncategorized: bool,
        account_id: Optional[int],
    ) -> List[int]:
        clauses: List[str] = []
        params: List[Any] = []
        if account_id is not None:
            clauses.append("t.account_id = ?")
            params.append(account_id)
        clauses.append("COALESCE(t.transaction_nature, 'ordinary') != 'internal_transfer'")
        if only_uncategorized:
            clauses.append(
                "NOT EXISTS (SELECT 1 FROM transaction_category tc "
                "WHERE tc.transaction_id = t.id)"
            )
        else:
            clauses.append(
                "NOT EXISTS (SELECT 1 FROM transaction_category tc "
                "WHERE tc.transaction_id = t.id AND tc.source = 'user')"
            )
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        rows = conn.execute(f"SELECT t.id FROM transactions t {where}", params).fetchall()
        return [int(r["id"]) for r in rows]

    @staticmethod
    def _apply_rules_within_conn(
        conn: sqlite3.Connection, tx_ids: Sequence[int]
    ) -> int:
        """Setzt Kategorien für ``tx_ids`` gemäß Regeln. Erfordert offenen
        Transaktions-Kontext (ldt Regeln einmalig in den Speicher).
        """
        rule_rows = conn.execute(
            "SELECT match_iban, match_counterparty, category_id FROM counterparty_rules"
        ).fetchall()
        if not rule_rows:
            return 0
        iban_to_cat: Dict[str, int] = {}
        cp_to_cat: Dict[str, int] = {}
        for r in rule_rows:
            if r["match_iban"]:
                iban_to_cat[r["match_iban"]] = int(r["category_id"])
            elif r["match_counterparty"]:
                cp_to_cat[r["match_counterparty"]] = int(r["category_id"])
        if not iban_to_cat and not cp_to_cat:
            return 0
        applied = 0
        # Chunked IN-Query, um SQLite-Parameter-Limit zu respektieren
        for offset in range(0, len(tx_ids), 500):
            chunk = tx_ids[offset : offset + 500]
            placeholders = ",".join("?" * len(chunk))
            rows = conn.execute(
                f"SELECT id, counterparty, counterparty_iban FROM transactions "
                f"WHERE id IN ({placeholders})",
                tuple(chunk),
            ).fetchall()
            for tx in rows:
                cat_id: Optional[int] = None
                if tx["counterparty_iban"] and tx["counterparty_iban"] in iban_to_cat:
                    cat_id = iban_to_cat[tx["counterparty_iban"]]
                else:
                    norm = _normalize_counterparty(tx["counterparty"])
                    if norm and norm in cp_to_cat:
                        cat_id = cp_to_cat[norm]
                if cat_id is None:
                    continue
                # Vorhandenes nicht-User-Assignment ersetzen, User-Assignment respektieren
                user_owned = conn.execute(
                    "SELECT 1 FROM transaction_category WHERE transaction_id = ? AND source = 'user'",
                    (int(tx["id"]),),
                ).fetchone()
                if user_owned:
                    continue
                conn.execute(
                    "DELETE FROM transaction_category WHERE transaction_id = ?",
                    (int(tx["id"]),),
                )
                conn.execute(
                    "INSERT INTO transaction_category (transaction_id, category_id, confidence, source) "
                    "VALUES (?, ?, 1.0, 'rule')",
                    (int(tx["id"]), cat_id),
                )
                FinanceDB._refresh_transaction_natures_within_conn(conn, [int(tx["id"])])
                applied += 1
        return applied

    # -- uncategorized -----------------------------------------------

    def get_transaction(self, transaction_id: int) -> Optional[Transaction]:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                """
                SELECT t.*, (
                    SELECT c.name FROM transaction_category tc
                    JOIN categories c ON c.id = tc.category_id
                    WHERE tc.transaction_id = t.id
                    ORDER BY tc.confidence DESC LIMIT 1
                ) AS category
                FROM transactions t WHERE t.id = ?
                """,
                (transaction_id,),
            ).fetchone()
            return self._row_to_transaction(row) if row else None

    def list_uncategorized(
        self,
        *,
        account_id: Optional[int] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        limit: int = 200,
    ) -> List[Transaction]:
        clauses: List[str] = [
            "NOT EXISTS (SELECT 1 FROM transaction_category tc WHERE tc.transaction_id = t.id)",
            "COALESCE(t.transaction_nature, 'ordinary') != 'internal_transfer'",
        ]
        params: List[Any] = []
        if account_id is not None:
            clauses.append("t.account_id = ?")
            params.append(account_id)
        if start_date:
            clauses.append("t.booking_date >= ?")
            params.append(start_date)
        if end_date:
            clauses.append("t.booking_date <= ?")
            params.append(end_date)
        where = "WHERE " + " AND ".join(clauses)
        sql = f"""
            SELECT t.*, NULL AS category
            FROM transactions t
            {where}
            ORDER BY t.booking_date DESC, t.id DESC
            LIMIT ?
        """
        params.append(limit)
        with self._lock, self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
            return [self._row_to_transaction(r) for r in rows]

    # -- budgets -----------------------------------------------------

    def upsert_budget(
        self, *, category_id: int, month: str, budget_cents: int
    ) -> int:
        """Speichert ein positives Haushaltslimit in DEFAULT_CURRENCY."""
        if not (len(month) == 7 and month[4] == "-" and month[:4].isdigit() and month[5:].isdigit()):
            raise ValueError(f"month must be YYYY-MM, got {month!r}")
        self._validate_iso_date(f"{month}-01", field="month")
        budget_cents = abs(budget_cents)
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT id FROM budgets WHERE category_id = ? AND month = ?",
                (category_id, month),
            ).fetchone()
            if row:
                bid = int(row["id"])
                conn.execute(
                    "UPDATE budgets SET budget_cents = ? WHERE id = ?",
                    (budget_cents, bid),
                )
                return bid
            cur = conn.execute(
                "INSERT INTO budgets (category_id, month, budget_cents) VALUES (?, ?, ?)",
                (category_id, month, budget_cents),
            )
            return int(cur.lastrowid or 0)

    def delete_budget(self, budget_id: int) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("DELETE FROM budgets WHERE id = ?", (budget_id,))

    def list_budgets(self, month: Optional[str] = None) -> List[Budget]:
        sql = """
            SELECT b.id, b.category_id, b.month, b.budget_cents, c.name AS category_name
            FROM budgets b
            LEFT JOIN categories c ON c.id = b.category_id
        """
        params: List[Any] = []
        if month:
            sql += " WHERE b.month = ?"
            params.append(month)
        sql += " ORDER BY b.month DESC, c.name"
        with self._lock, self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
            return [
                Budget(
                    id=int(r["id"]),
                    category_id=int(r["category_id"]),
                    category_name=r["category_name"],
                    month=r["month"],
                    budget_cents=abs(int(r["budget_cents"])),
                )
                for r in rows
            ]

    def budget_status(
        self, month: str, *, account_id: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """Soll/Ist in DEFAULT_CURRENCY fuer einen Monat (YYYY-MM).

        Haushaltslimits bleiben global; Istwerte sind optional kontogefiltert.
        Transfers und Buchungen in anderen Waehrungen werden ausgeschlossen.
        """
        if not (len(month) == 7 and month[4] == "-"):
            raise ValueError(f"month must be YYYY-MM, got {month!r}")
        self._validate_iso_date(f"{month}-01", field="month")
        sql = """
            SELECT
                c.id   AS category_id,
                c.name AS category,
                c.kind AS kind,
                ABS(COALESCE(b.budget_cents, 0)) AS budget_cents,
                COALESCE(SUM(
                    CASE
                        WHEN c.kind = 'expense' AND t.amount_cents < 0 THEN -t.amount_cents
                        WHEN c.kind = 'income' AND t.amount_cents > 0 THEN t.amount_cents
                        ELSE 0
                    END
                ), 0) AS actual_cents,
                COUNT(t.id) AS tx_count
            FROM categories c
            LEFT JOIN budgets b
                ON b.category_id = c.id AND b.month = ?
            LEFT JOIN transaction_category tc ON tc.category_id = c.id
            LEFT JOIN transactions t
                ON t.id = tc.transaction_id
                AND substr(t.booking_date, 1, 7) = ?
                AND t.currency = ?
                AND (? IS NULL OR t.account_id = ?)
                AND COALESCE(t.transaction_nature, 'ordinary') != 'internal_transfer'
                AND t.id NOT IN (SELECT outgoing_tx_id FROM transfer_links
                                 UNION SELECT incoming_tx_id FROM transfer_links)
            GROUP BY c.id, c.name, c.kind, b.budget_cents
            HAVING (b.budget_cents IS NOT NULL) OR (COUNT(t.id) > 0)
            ORDER BY c.kind, c.name
        """
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                sql, (month, month, DEFAULT_CURRENCY, account_id, account_id)
            ).fetchall()
            return [
                {
                    "category_id": int(r["category_id"]),
                    "category": r["category"],
                    "kind": r["kind"],
                    "currency": DEFAULT_CURRENCY,
                    "budget_cents": int(r["budget_cents"] or 0),
                    "actual_cents": int(r["actual_cents"] or 0),
                    "remaining_cents": int(r["budget_cents"] or 0) - int(r["actual_cents"] or 0),
                    "tx_count": int(r["tx_count"] or 0),
                }
                for r in rows
            ]

    # -- goals / sinking funds (Phase 2, 2026-09-12) --------------------

    @staticmethod
    def _validate_iso_date(value: str, *, field: str) -> str:
        """Validiert ISO-Datum YYYY-MM-DD (Format + Kalendergueltigkeit)."""
        if not (len(value) == 10 and value[4] == "-" and value[7] == "-"
                and value[:4].isdigit() and value[5:7].isdigit() and value[8:].isdigit()):
            raise ValueError(f"{field} must be YYYY-MM-DD, got {value!r}")
        try:
            datetime.strptime(value, "%Y-%m-%d")
        except ValueError:
            raise ValueError(f"{field} is not a valid calendar date: {value!r}") from None
        return value

    @staticmethod
    def _currency_for_iban(iban: str) -> Optional[str]:
        """Ableitet die ISO-4217-Waehrung aus dem IBAN-Landescode (ISO 13616).

        Die ersten zwei Zeichen der IBAN sind der ISO-3166-Landescode:
        DE/AT -> EUR (Euroland), CH -> CHF (CH-IBANs sind standardmaessig CHF).
        Fuer alle anderen Laender gibt es keine eindeutige Ableitung,
        daher None -> der Aufrufer faellt auf DEFAULT_CURRENCY zurueck.
        """
        normalized = "".join(str(iban or "").split()).upper()
        if len(normalized) < 2 or not normalized[:2].isalpha():
            return None
        country = normalized[:2]
        if country in ("DE", "AT"):
            return "EUR"
        if country == "CH":
            return "CHF"
        return None

    def upsert_goal(
        self,
        *,
        name: str,
        iban: str,
        target_cents: int,
        currency: Optional[str] = None,
        target_date: Optional[str] = None,
        monthly_rate_cents: Optional[int] = None,
        status: str = "active",
        notes: Optional[str] = None,
    ) -> int:
        """Erstellt oder aktualisiert ein Ziel (Schluessel: name + iban).

        Validierung (Fail-Fast): name/iban nicht-leer, target_cents > 0,
        monthly_rate_cents >= 0, status in VALID_GOAL_STATUSES,
        target_date ISO YYYY-MM-DD.

        Currency-Default: IBAN-Landescode (DE/AT -> EUR, CH -> CHF),
        sonst DEFAULT_CURRENCY (siehe :meth:`_currency_for_iban`).
        """
        if not name or not str(name).strip():
            raise ValueError("name must be non-empty")
        if not iban or not str(iban).strip():
            raise ValueError("iban must be non-empty")
        if not isinstance(target_cents, int) or isinstance(target_cents, bool) or target_cents <= 0:
            raise ValueError(f"target_cents must be a positive int, got {target_cents!r}")
        if monthly_rate_cents is not None and (
            not isinstance(monthly_rate_cents, int)
            or isinstance(monthly_rate_cents, bool)
            or monthly_rate_cents < 0
        ):
            raise ValueError(f"monthly_rate_cents must be >= 0, got {monthly_rate_cents!r}")
        if status not in VALID_GOAL_STATUSES:
            raise ValueError(f"status must be one of {sorted(VALID_GOAL_STATUSES)}, got {status!r}")
        if target_date is not None:
            target_date = self._validate_iso_date(str(target_date), field="target_date")
        # 2026-09-13: Currency-Fallback ueber den IBAN-Landescode (SSOT: das
        # Ziel wird vom Kontokonto finanziert) vor DEFAULT_CURRENCY:
        # DE/AT -> EUR, CH -> CHF, sonst DEFAULT_CURRENCY.
        currency = (currency or self._currency_for_iban(iban) or DEFAULT_CURRENCY).upper()
        if len(currency) != 3 or not currency.isalpha():
            raise ValueError(f"currency must be a 3-letter ISO code, got {currency!r}")
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT id FROM goals WHERE name = ? AND iban = ?",
                (name.strip(), iban.strip()),
            ).fetchone()
            if row:
                gid = int(row["id"])
                conn.execute(
                    """UPDATE goals SET target_cents = ?, currency = ?, target_date = ?,
                       monthly_rate_cents = ?, status = ?, notes = ?,
                       updated_at = datetime('now') WHERE id = ?""",
                    (target_cents, currency, target_date, monthly_rate_cents,
                     status, notes, gid),
                )
                return gid
            cur = conn.execute(
                """INSERT INTO goals (name, iban, currency, target_cents, target_date,
                                      monthly_rate_cents, status, notes)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (name.strip(), iban.strip(), currency, target_cents, target_date,
                 monthly_rate_cents, status, notes),
            )
            return int(cur.lastrowid or 0)

    def get_goal(self, goal_id: int) -> Optional[Goal]:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT * FROM goals WHERE id = ?", (goal_id,)).fetchone()
            if row is None:
                return None
            return Goal(
                id=int(row["id"]),
                name=row["name"],
                iban=row["iban"],
                currency=row["currency"],
                target_cents=int(row["target_cents"]),
                target_date=row["target_date"],
                monthly_rate_cents=(
                    int(row["monthly_rate_cents"]) if row["monthly_rate_cents"] is not None else None
                ),
                status=row["status"],
                notes=row["notes"],
                created_at=row["created_at"],
                updated_at=row["updated_at"],
            )

    def list_goals(
        self, *, status: Optional[str] = None, iban: Optional[str] = None
    ) -> List[Goal]:
        sql = "SELECT * FROM goals WHERE 1=1"
        params: List[Any] = []
        if status is not None:
            if status not in VALID_GOAL_STATUSES:
                raise ValueError(f"status must be one of {sorted(VALID_GOAL_STATUSES)}, got {status!r}")
            sql += " AND status = ?"
            params.append(status)
        if iban is not None:
            sql += " AND iban = ?"
            params.append(iban)
        sql += " ORDER BY created_at DESC, name"
        with self._lock, self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [
            Goal(
                id=int(r["id"]), name=r["name"], iban=r["iban"], currency=r["currency"],
                target_cents=int(r["target_cents"]), target_date=r["target_date"],
                monthly_rate_cents=int(r["monthly_rate_cents"]) if r["monthly_rate_cents"] is not None else None,
                status=r["status"], notes=r["notes"],
                created_at=r["created_at"], updated_at=r["updated_at"],
            )
            for r in rows
        ]

    def goal_progress(self, goal_id: int) -> Dict[str, Any]:
        """Fortschritt eines Ziels: Summe der zugeordneten Buchungen.

        Liefert ``progress_cents`` (vorzeichenbehaftet), ``remaining_cents``,
        ``progress_pct`` (bezogen auf target_cents, kann >100 sein) und
        ``contribution_count``. ``ValueError`` bei unbekannter goal_id.
        """
        with self._lock, self._connect() as conn:
            goal = conn.execute("SELECT * FROM goals WHERE id = ?", (goal_id,)).fetchone()
            if goal is None:
                raise ValueError(f"goal {goal_id} not found")
            agg = conn.execute(
                "SELECT COUNT(*) AS n, COALESCE(SUM(amount_cents), 0) AS s "
                "FROM goal_contributions WHERE goal_id = ?",
                (goal_id,),
            ).fetchone()
        target = int(goal["target_cents"])
        progress = int(agg["s"])
        pct = round(progress * 100.0 / target, 2) if target else 0.0
        return {
            "goal_id": int(goal["id"]),
            "status": goal["status"],
            "target_cents": target,
            "progress_cents": progress,
            "remaining_cents": target - progress,
            "progress_pct": pct,
            "contribution_count": int(agg["n"]),
        }

    def goal_progress_asof(
        self, goal_id: int, reference_date: date
    ) -> Dict[str, Any]:
        """Fortschritt eines Ziels bis (inklusive) eines Referenzdatums.

        Nur Buchungen mit ``booking_date <= reference_date`` zählen --
        für Projektionen ohne Future-Leak (Phase 2). ``reference_date``
        ist ein ``datetime.date`` (oder 'YYYY-MM-DD'). ``ValueError``
        bei unbekannter goal_id.
        """
        ref_iso = (
            reference_date.isoformat()
            if isinstance(reference_date, date)
            else str(reference_date)
        )
        with self._lock, self._connect() as conn:
            goal = conn.execute("SELECT * FROM goals WHERE id = ?", (goal_id,)).fetchone()
            if goal is None:
                raise ValueError(f"goal {goal_id} not found")
            agg = conn.execute(
                "SELECT COUNT(*) AS n, COALESCE(SUM(gc.amount_cents), 0) AS s "
                "FROM goal_contributions gc "
                "JOIN transactions t ON t.id = gc.transaction_id "
                "WHERE gc.goal_id = ? AND t.booking_date <= ?",
                (goal_id, ref_iso),
            ).fetchone()
        target = int(goal["target_cents"])
        progress = int(agg["s"])
        pct = min(100.0, round(progress * 100.0 / target, 2)) if target else 0.0
        return {
            "goal_id": goal_id,
            "reference_date": ref_iso,
            "target_cents": target,
            "saved_cents": progress,
            "progress_pct": pct,
            "contribution_count": int(agg["n"]),
        }

    def assigned_transaction_ids(self) -> List[int]:
        """Alle ``transactions.id``, die bereits einem Ziel zugeordnet sind.

        Für die Kandidaten-Erkennung (Phase 2 DoD #5: bereits zugeordnete
        Buchungen werden ausgeschlossen).
        """
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT DISTINCT transaction_id FROM goal_contributions"
            ).fetchall()
        return [int(row["transaction_id"]) for row in rows]

    def assign_contribution(
        self, goal_id: int, transaction_id: int, *, source: str = "user"
    ) -> int:
        """Ordnet eine Buchung exakt einem Ziel zu (1 Buchung = 1 Ziel).

        Invariante aus UNIQUE(transaction_id): ist die Buchung bereits
        einem Ziel zugeordnet, ``ValueError`` (klar + actionable,
        ``transfer_links``-Muster). Der Betrag wird als Snapshot
        gespeichert; Vorzeichen egal (Spar-Beitrag = positiv,
        Ziel-Auszahlung = negativ -- beides ist Fortschritt).
        """
        if source not in ("user", "rule"):
            raise ValueError(f"source must be user|rule, got {source!r}")
        with self._lock, self._connect() as conn:
            if conn.execute("SELECT id FROM goals WHERE id = ?", (goal_id,)).fetchone() is None:
                raise ValueError(f"goal {goal_id} not found")
            tx = conn.execute(
                "SELECT id, amount_cents FROM transactions WHERE id = ?",
                (transaction_id,),
            ).fetchone()
            if tx is None:
                raise ValueError(f"transaction {transaction_id} not found")
            existing = conn.execute(
                "SELECT id, goal_id FROM goal_contributions WHERE transaction_id = ?",
                (transaction_id,),
            ).fetchone()
            if existing is not None:
                raise ValueError(
                    f"transaction {transaction_id} is already assigned to goal "
                    f"{int(existing['goal_id'])} (contribution id {int(existing['id'])}); "
                    "unassign it first or choose a different goal"
                )
            try:
                cur = conn.execute(
                    "INSERT INTO goal_contributions "
                    "(goal_id, transaction_id, amount_cents, source) "
                    "VALUES (?, ?, ?, ?)",
                    (goal_id, transaction_id, int(tx["amount_cents"]), source),
                )
            except sqlite3.IntegrityError as exc:
                # Safety-Net (paralleler Writer zwischen Check und Insert):
                # lauter, klarer Fehler -- nie silent.
                raise ValueError(
                    f"goal contribution conflict for goal={goal_id} / "
                    f"tx={transaction_id}: {exc}"
                ) from exc
            return int(cur.lastrowid or 0)

    def unassign_contribution(self, contribution_id: int) -> None:
        with self._lock, self._connect() as conn:
            cur = conn.execute(
                "DELETE FROM goal_contributions WHERE id = ?", (contribution_id,)
            )
            if cur.rowcount == 0:
                raise ValueError(f"contribution {contribution_id} not found")

    def list_contributions(self, goal_id: int) -> List[GoalContribution]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM goal_contributions WHERE goal_id = ? "
                "ORDER BY created_at, id",
                (goal_id,),
            ).fetchall()
        return [
            GoalContribution(
                id=int(r["id"]),
                goal_id=int(r["goal_id"]),
                transaction_id=int(r["transaction_id"]),
                amount_cents=int(r["amount_cents"]),
                source=r["source"],
                created_at=r["created_at"],
            )
            for r in rows
        ]

    def find_contribution_id(
        self, goal_id: int, transaction_id: int
    ) -> Optional[int]:
        """Liefert die ``goal_contributions.id`` fuer ein (Ziel, Buchung)-Paar.

        ``None`` wenn die Buchung dem Ziel nicht zugeordnet ist (z. B.
        bereits entfernt oder einem anderen Ziel zugeordnet -- max. 1 Ziel
        pro Buchung gilt konstruktiv).
        """
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT id FROM goal_contributions WHERE goal_id = ? AND transaction_id = ?",
                (goal_id, transaction_id),
            ).fetchone()
        return int(row["id"]) if row is not None else None

    def set_goal_status(self, goal_id: int, status: str) -> None:
        if status not in VALID_GOAL_STATUSES:
            raise ValueError(f"status must be one of {sorted(VALID_GOAL_STATUSES)}, got {status!r}")
        with self._lock, self._connect() as conn:
            cur = conn.execute(
                "UPDATE goals SET status = ?, updated_at = datetime('now') WHERE id = ?",
                (status, goal_id),
            )
            if cur.rowcount == 0:
                raise ValueError(f"goal {goal_id} not found")

    def delete_goal(self, goal_id: int) -> None:
        """Loescht ein Ziel; Ziel-Beitraege fallen per ON DELETE CASCADE ab."""
        with self._lock, self._connect() as conn:
            cur = conn.execute("DELETE FROM goals WHERE id = ?", (goal_id,))
            if cur.rowcount == 0:
                raise ValueError(f"goal {goal_id} not found")

    # -- forecast plan items (Forecast UX AP1, 2026-09-16) -------------
    #
    # Plan-Items sind datierte, rueckgaengige Prognose-Annahmen. Sie
    # sind bewusst KEINE Buchungen/Transaktionen und auch keine
    # goal_contributions (siehe Dataclass ``PlanItem``); Bank- und
    # Kontodaten bleiben unveraendert.
    #
    # Concurrency: jeder Write erhoeht ``revision``; Updates verlangen
    # ``expected_revision`` (``PlanRevisionConflict`` bei Abweichung,
    # keine stille Ueberschreibung).
    # Undo: ``forecast_plan_journal`` speichert komplette
    # before/after-Snapshots; ``undo_plan_change`` wendet die
    # Gegenrevision an (letzte Aktion pro Item).

    @staticmethod
    def _plan_snapshot(row: sqlite3.Row) -> Dict[str, Any]:
        """Kompletter Snapshot eines Plan-Items (Journal/Undo)."""
        return {
            "id": int(row["id"]),
            "iban": row["iban"],
            "currency": row["currency"],
            "kind": row["kind"],
            "amount_cents": int(row["amount_cents"]),
            "category_id": row["category_id"],
            "title": row["title"],
            "due_date": row["due_date"],
            "source_type": row["source_type"],
            "status": row["status"],
            "revision": int(row["revision"]),
            "client_token": row["client_token"],
            "notes": row["notes"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    @classmethod
    def _plan_item_from_row(cls, row: sqlite3.Row) -> PlanItem:
        return PlanItem(
            id=int(row["id"]),
            iban=row["iban"],
            currency=row["currency"],
            kind=row["kind"],
            amount_cents=int(row["amount_cents"]),
            category_id=row["category_id"],
            title=row["title"],
            due_date=row["due_date"],
            source_type=row["source_type"],
            status=row["status"],
            revision=int(row["revision"]),
            client_token=row["client_token"],
            notes=row["notes"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _clean_text(value: Optional[str]) -> Optional[str]:
        """Whitespace-Kollaps; leer/None bleibt None."""
        if value is None:
            return None
        cleaned = str(value).strip()
        return cleaned or None

    @staticmethod
    def _validate_plan_fields(
        *,
        iban: Optional[str],
        currency: Optional[str],
        kind: Optional[str],
        amount_cents: Optional[int],
        due_date: Optional[str],
        title: Optional[str],
        notes: Optional[str],
        category_id: Optional[int],
        status: Optional[str],
        source_type: Optional[str],
    ) -> None:
        """Gemeinsame Feld-Validierung an der DAO-Grenze (lauter Fehler)."""
        if iban is not None:
            if not isinstance(iban, str) or not iban.strip():
                raise ValueError("iban must be a non-empty string")
        if currency is not None:
            if (
                not isinstance(currency, str)
                or len(currency) != 3
                or not currency.isalpha()
            ):
                raise ValueError(
                    f"currency must be a 3-letter code, got {currency!r}"
                )
        if kind is not None and kind not in VALID_PLAN_ITEM_KINDS:
            raise ValueError(
                f"kind must be one of {sorted(VALID_PLAN_ITEM_KINDS)}, got {kind!r}"
            )
        if amount_cents is not None:
            if (
                isinstance(amount_cents, bool)
                or not isinstance(amount_cents, int)
                or amount_cents <= 0
            ):
                raise ValueError(
                    f"amount_cents must be an int > 0, got {amount_cents!r}"
                )
        if due_date is not None:
            try:
                date.fromisoformat(str(due_date).strip())
            except ValueError:
                raise ValueError(
                    f"due_date must be ISO YYYY-MM-DD, got {due_date!r}"
                ) from None
        for label, value in (("title", title), ("notes", notes)):
            if value is not None and not isinstance(value, str):
                raise ValueError(f"{label} must be a string or None")
        if category_id is not None:
            if (
                isinstance(category_id, bool)
                or not isinstance(category_id, int)
                or category_id <= 0
            ):
                raise ValueError(
                    f"category_id must be a positive int or None, got {category_id!r}"
                )
        if status is not None and status not in VALID_PLAN_ITEM_STATUSES:
            raise ValueError(
                f"status must be one of {sorted(VALID_PLAN_ITEM_STATUSES)}, "
                f"got {status!r}"
            )
        if source_type is not None and source_type not in VALID_PLAN_ITEM_SOURCES:
            raise ValueError(
                f"source_type must be one of {sorted(VALID_PLAN_ITEM_SOURCES)}, "
                f"got {source_type!r}"
            )

    def create_plan_item(
        self,
        *,
        iban: str,
        kind: str,
        amount_cents: int,
        due_date: str,
        currency: Optional[str] = None,
        title: Optional[str] = None,
        notes: Optional[str] = None,
        category_id: Optional[int] = None,
        source_type: str = "manual",
        status: str = "active",
        client_token: Optional[str] = None,
    ) -> PlanItem:
        """Erstellt ein Plan-Item (Journal-Aktion 'created').

        ``client_token`` macht den Aufruf idempotent: wird derselbe
        Token erneut verwendet, wird das BESTEHENDE Item zurueckgegeben
        (kein Duplikat, keine implizite Update).

        ``ValueError`` bei ungueltigen Feldern oder unbekannter IBAN --
        ein Plan-Item muss ein bekanntes Konto referenzieren.
        """
        self._validate_plan_fields(
            iban=iban,
            currency=currency,
            kind=kind,
            amount_cents=amount_cents,
            due_date=due_date,
            title=title,
            notes=notes,
            category_id=category_id,
            status=status,
            source_type=source_type,
        )
        iban_norm = _normalize_iban(iban)
        currency_norm = (currency or DEFAULT_CURRENCY).upper()
        token = (
            (str(client_token).strip() or None)
            if client_token is not None
            else None
        )
        title_norm = self._clean_text(title)
        notes_norm = self._clean_text(notes)
        due_norm = str(due_date).strip()
        with self._lock, self._connect() as conn:
            if token is not None:
                existing = conn.execute(
                    "SELECT * FROM forecast_plan_items WHERE client_token = ?",
                    (token,),
                ).fetchone()
                if existing is not None:
                    return self._plan_item_from_row(existing)
            if (
                conn.execute(
                    "SELECT 1 FROM accounts WHERE iban = ?", (iban_norm,)
                ).fetchone()
                is None
            ):
                raise ValueError(f"unknown account: {iban_norm}")
            if category_id is not None and (
                conn.execute(
                    "SELECT 1 FROM categories WHERE id = ?", (category_id,)
                ).fetchone()
                is None
            ):
                raise ValueError(f"unknown category: {category_id}")
            cur = conn.execute(
                """INSERT INTO forecast_plan_items (
                       iban, currency, kind, amount_cents, category_id, title,
                       due_date, source_type, status, revision, client_token,
                       notes
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)""",
                (
                    iban_norm,
                    currency_norm,
                    kind,
                    int(amount_cents),
                    category_id,
                    title_norm,
                    due_norm,
                    source_type,
                    status,
                    token,
                    notes_norm,
                ),
            )
            row = conn.execute(
                "SELECT * FROM forecast_plan_items WHERE id = ?",
                (int(cur.lastrowid or 0),),
            ).fetchone()
            snapshot = self._plan_snapshot(row)
            conn.execute(
                "INSERT INTO forecast_plan_journal "
                "(plan_item_id, action, before_json, after_json, revision) "
                "VALUES (?, 'created', NULL, ?, 1)",
                (
                    snapshot["id"],
                    json.dumps(snapshot, sort_keys=True, ensure_ascii=False),
                ),
            )
        return self._plan_item_from_row(row)

    def get_plan_item(self, plan_item_id: int) -> PlanItem:
        """Liest ein Plan-Item; ``ValueError`` bei unbekannter ID."""
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM forecast_plan_items WHERE id = ?",
                (plan_item_id,),
            ).fetchone()
        if row is None:
            raise ValueError(f"plan item {plan_item_id} not found")
        return self._plan_item_from_row(row)

    def list_plan_items(
        self,
        *,
        status: Optional[str] = None,
        iban: Optional[str] = None,
        kind: Optional[str] = None,
        source_type: Optional[str] = None,
        due_from: Optional[str] = None,
        due_to: Optional[str] = None,
    ) -> List[PlanItem]:
        """Listet Plan-Items (Filter optional; Sortierung due_date, iban, id)."""
        sql = "SELECT * FROM forecast_plan_items WHERE 1=1"
        params: List[Any] = []
        if status is not None:
            if status not in VALID_PLAN_ITEM_STATUSES:
                raise ValueError(
                    f"status must be one of {sorted(VALID_PLAN_ITEM_STATUSES)}, "
                    f"got {status!r}"
                )
            sql += " AND status = ?"
            params.append(status)
        if iban is not None:
            sql += " AND iban = ?"
            params.append(_normalize_iban(iban))
        if kind is not None:
            if kind not in VALID_PLAN_ITEM_KINDS:
                raise ValueError(
                    f"kind must be one of {sorted(VALID_PLAN_ITEM_KINDS)}, "
                    f"got {kind!r}"
                )
            sql += " AND kind = ?"
            params.append(kind)
        if source_type is not None:
            if source_type not in VALID_PLAN_ITEM_SOURCES:
                raise ValueError(
                    f"source_type must be one of {sorted(VALID_PLAN_ITEM_SOURCES)}, "
                    f"got {source_type!r}"
                )
            sql += " AND source_type = ?"
            params.append(source_type)
        if due_from is not None:
            sql += " AND due_date >= ?"
            params.append(str(due_from).strip())
        if due_to is not None:
            sql += " AND due_date <= ?"
            params.append(str(due_to).strip())
        sql += " ORDER BY due_date, iban, id"
        with self._lock, self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [self._plan_item_from_row(r) for r in rows]

    def update_plan_item(
        self,
        plan_item_id: int,
        expected_revision: int,
        *,
        iban: Optional[str] = None,
        currency: Optional[str] = None,
        kind: Optional[str] = None,
        amount_cents: Optional[int] = None,
        due_date: Optional[str] = None,
        title: Optional[str] = None,
        notes: Optional[str] = None,
        category_id: Optional[int] = None,
        status: Optional[str] = None,
    ) -> PlanItem:
        """Aktualisiert eine oder mehrere Felder (Journal-Aktion 'updated').

        Optimistic Locking: ``expected_revision`` muss der aktuellen
        ``revision`` entsprechen, sonst ``PlanRevisionConflict`` --
        keine stille Ueberschreibung paralleler Aenderungen.
        ``category_id=None`` und ``None``-Texte sind 'nicht aendern',
        keine Loeschung (AP1-Bereich).
        """
        if (
            isinstance(expected_revision, bool)
            or not isinstance(expected_revision, int)
            or expected_revision <= 0
        ):
            raise ValueError(
                "expected_revision must be a positive int, "
                f"got {expected_revision!r}"
            )
        self._validate_plan_fields(
            iban=iban,
            currency=currency,
            kind=kind,
            amount_cents=amount_cents,
            due_date=due_date,
            title=title,
            notes=notes,
            category_id=category_id,
            status=status,
            source_type=None,
        )
        sets: List[str] = []
        params: List[Any] = []
        if iban is not None:
            sets.append("iban = ?")
            params.append(_normalize_iban(iban))
        if currency is not None:
            sets.append("currency = ?")
            params.append(str(currency).upper())
        if kind is not None:
            sets.append("kind = ?")
            params.append(kind)
        if amount_cents is not None:
            sets.append("amount_cents = ?")
            params.append(int(amount_cents))
        if due_date is not None:
            sets.append("due_date = ?")
            params.append(str(due_date).strip())
        if title is not None:
            sets.append("title = ?")
            params.append(self._clean_text(title))
        if notes is not None:
            sets.append("notes = ?")
            params.append(self._clean_text(notes))
        if category_id is not None:
            sets.append("category_id = ?")
            params.append(category_id)
        if status is not None:
            sets.append("status = ?")
            params.append(status)
        if not sets:
            raise ValueError("no fields to update")
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM forecast_plan_items WHERE id = ?",
                (plan_item_id,),
            ).fetchone()
            if row is None:
                raise ValueError(f"plan item {plan_item_id} not found")
            actual = int(row["revision"])
            if actual != int(expected_revision):
                raise PlanRevisionConflict(
                    plan_item_id, int(expected_revision), actual
                )
            if iban is not None and (
                conn.execute(
                    "SELECT 1 FROM accounts WHERE iban = ?",
                    (_normalize_iban(iban),),
                ).fetchone()
                is None
            ):
                raise ValueError(
                    f"unknown account: {_normalize_iban(iban)}"
                )
            if category_id is not None and (
                conn.execute(
                    "SELECT 1 FROM categories WHERE id = ?", (category_id,)
                ).fetchone()
                is None
            ):
                raise ValueError(f"unknown category: {category_id}")
            before = self._plan_snapshot(row)
            conn.execute(
                "UPDATE forecast_plan_items SET "
                + ", ".join(sets)
                + ", revision = revision + 1, updated_at = datetime('now') "
                "WHERE id = ?",
                [*params, plan_item_id],
            )
            new_row = conn.execute(
                "SELECT * FROM forecast_plan_items WHERE id = ?",
                (plan_item_id,),
            ).fetchone()
            after = self._plan_snapshot(new_row)
            conn.execute(
                "INSERT INTO forecast_plan_journal "
                "(plan_item_id, action, before_json, after_json, revision) "
                "VALUES (?, 'updated', ?, ?, ?)",
                (
                    plan_item_id,
                    json.dumps(before, sort_keys=True, ensure_ascii=False),
                    json.dumps(after, sort_keys=True, ensure_ascii=False),
                    after["revision"],
                ),
            )
        return self._plan_item_from_row(new_row)

    def set_plan_item_status(
        self, plan_item_id: int, status: str, expected_revision: int
    ) -> PlanItem:
        """Setzt nur den Status (active/paused/done/rejected)."""
        if status not in VALID_PLAN_ITEM_STATUSES:
            raise ValueError(
                f"status must be one of {sorted(VALID_PLAN_ITEM_STATUSES)}, "
                f"got {status!r}"
            )
        return self.update_plan_item(
            plan_item_id, expected_revision, status=status
        )

    def delete_plan_item(self, plan_item_id: int) -> None:
        """Loescht ein Plan-Item (Journal-Aktion 'deleted').

        Rueckgaengig via ``undo_plan_change`` -- das Journal haelt den
        kompletten before-Snapshot und ueberlebt die Loeschung (kein
        Foreign Key auf forecast_plan_items).
        """
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM forecast_plan_items WHERE id = ?",
                (plan_item_id,),
            ).fetchone()
            if row is None:
                raise ValueError(f"plan item {plan_item_id} not found")
            before = self._plan_snapshot(row)
            conn.execute(
                "DELETE FROM forecast_plan_items WHERE id = ?",
                (plan_item_id,),
            )
            conn.execute(
                "INSERT INTO forecast_plan_journal "
                "(plan_item_id, action, before_json, after_json, revision) "
                "VALUES (?, 'deleted', ?, NULL, ?)",
                (
                    plan_item_id,
                    json.dumps(before, sort_keys=True, ensure_ascii=False),
                    before["revision"],
                ),
            )

    @staticmethod
    def _plan_journal_from_row(row: sqlite3.Row) -> PlanJournalEntry:
        """Journal-Zeile -> Dataclass (``before_json``/``after_json`` roh)."""
        return PlanJournalEntry(
            id=int(row["id"]),
            plan_item_id=int(row["plan_item_id"]),
            action=row["action"],
            before_json=row["before_json"],
            after_json=row["after_json"],
            revision=int(row["revision"]),
            created_at=row["created_at"],
        )

    @staticmethod
    def _load_plan_snapshot(raw: Optional[str]) -> Optional[Dict[str, Any]]:
        """JSON-Snapshot parsen (kaputter/None -> None; Undo meldet dann)."""
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except (TypeError, ValueError):
            return None

    def get_plan_journal(
        self, plan_item_id: int, *, limit: int = 50
    ) -> List[PlanJournalEntry]:
        """Journal-Eintraege eines Items (neueste zuerst)."""
        if limit <= 0:
            raise ValueError("limit must be > 0")
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM forecast_plan_journal WHERE plan_item_id = ? "
                "ORDER BY id DESC LIMIT ?",
                (plan_item_id, limit),
            ).fetchall()
        return [self._plan_journal_from_row(r) for r in rows]

    def latest_plan_journal(
        self, plan_item_id: int
    ) -> Optional[PlanJournalEntry]:
        """Neuester Journal-Eintrag (None, falls keiner existiert)."""
        entries = self.get_plan_journal(plan_item_id, limit=1)
        return entries[0] if entries else None

    def undo_plan_change(self, plan_item_id: int) -> PlanItem:
        """Undo der letzten Aenderung (Gegenrevision, D5/D7).

        * 'created'  -> Item wird geloescht
        * 'updated'  -> before-Zustand wird wiederhergestellt
        * 'deleted'  -> before-Zustand wird zurueckgesetzt (gleiche ID)
        * 'restored' -> before-Zustand wird wiederhergestellt

        Jede Undo-Aktion schreibt selbst einen Journal-Eintrag
        ('deleted' bzw. 'restored'); das urspruengliche Journal bleibt
        erhalten. Liefert den Zustand NACH dem Undo (bei 'created':
        der geloeschte Zustand).
        """
        with self._lock, self._connect() as conn:
            entry = conn.execute(
                "SELECT * FROM forecast_plan_journal "
                "WHERE plan_item_id = ? ORDER BY id DESC LIMIT 1",
                (plan_item_id,),
            ).fetchone()
            if entry is None:
                raise ValueError(
                    f"plan item {plan_item_id} has no journal entry"
                )
            action = entry["action"]
            before = self._load_plan_snapshot(entry["before_json"])
            row = conn.execute(
                "SELECT * FROM forecast_plan_items WHERE id = ?",
                (plan_item_id,),
            ).fetchone()
            if action == "created":
                if row is None:
                    raise ValueError(f"plan item {plan_item_id} not found")
                cur_before = self._plan_snapshot(row)
                conn.execute(
                    "DELETE FROM forecast_plan_items WHERE id = ?",
                    (plan_item_id,),
                )
                conn.execute(
                    "INSERT INTO forecast_plan_journal "
                    "(plan_item_id, action, before_json, after_json, revision) "
                    "VALUES (?, 'deleted', ?, NULL, ?)",
                    (
                        plan_item_id,
                        json.dumps(
                            cur_before, sort_keys=True, ensure_ascii=False
                        ),
                        cur_before["revision"],
                    ),
                )
                return self._plan_item_from_row(row)
            if before is None:
                raise ValueError(
                    "journal entry has no before-state to restore"
                )
            if row is not None:
                cur_before = self._plan_snapshot(row)
                conn.execute(
                    """UPDATE forecast_plan_items SET
                       iban = ?, currency = ?, kind = ?, amount_cents = ?,
                       category_id = ?, title = ?, due_date = ?,
                       source_type = ?, status = ?, client_token = ?,
                       notes = ?, created_at = ?,
                       updated_at = datetime('now'),
                       revision = revision + 1
                       WHERE id = ?""",
                    (
                        before["iban"],
                        before["currency"],
                        before["kind"],
                        before["amount_cents"],
                        before["category_id"],
                        before["title"],
                        before["due_date"],
                        before["source_type"],
                        before["status"],
                        before["client_token"],
                        before["notes"],
                        before["created_at"],
                        plan_item_id,
                    ),
                )
            else:
                conn.execute(
                    """INSERT INTO forecast_plan_items (
                       id, iban, currency, kind, amount_cents, category_id,
                       title, due_date, source_type, status, revision,
                       client_token, notes, created_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)""",
                    (
                        before["id"],
                        before["iban"],
                        before["currency"],
                        before["kind"],
                        before["amount_cents"],
                        before["category_id"],
                        before["title"],
                        before["due_date"],
                        before["source_type"],
                        before["status"],
                        before["client_token"],
                        before["notes"],
                        before["created_at"],
                    ),
                )
            new_row = conn.execute(
                "SELECT * FROM forecast_plan_items WHERE id = ?",
                (plan_item_id,),
            ).fetchone()
            after = self._plan_snapshot(new_row)
            undo_before_json = (
                json.dumps(cur_before, sort_keys=True, ensure_ascii=False)
                if row is not None
                else None
            )
            conn.execute(
                "INSERT INTO forecast_plan_journal "
                "(plan_item_id, action, before_json, after_json, revision) "
                "VALUES (?, 'restored', ?, ?, ?)",
                (
                    plan_item_id,
                    undo_before_json,
                    json.dumps(after, sort_keys=True, ensure_ascii=False),
                    after["revision"],
                ),
            )
        return self._plan_item_from_row(new_row)

    # -- monthly report ----------------------------------------------

    def monthly_report(
        self, month: str, *, account_id: Optional[int] = None,
        include_transfers: bool = False,
        currency: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Aggregierter Monatsbericht: Cashflow, Kategorien, Top-Empfänger, Budget-Status.

        ``include_transfers=False`` (default) blendet beide Seiten von
        ``transfer_links`` aus, damit Kreditkarten-Sammelbelastungen nicht
        gegen die Einzelumstze doppelt zhlen.
        """
        if not (len(month) == 7 and month[4] == "-"):
            raise ValueError(f"month must be YYYY-MM, got {month!r}")
        self._validate_iso_date(f"{month}-01", field="month")
        clauses = ["substr(t.booking_date, 1, 7) = ?"]
        params: List[Any] = [month]
        if account_id is not None:
            clauses.append("t.account_id = ?")
            params.append(account_id)
        if not include_transfers:
            clauses.append(self._non_transfer_clause("t"))
        where = "WHERE " + " AND ".join(clauses)
        with self._lock, self._connect() as conn:
            if currency is None:
                currencies = conn.execute(
                    f"SELECT DISTINCT t.currency FROM transactions t {where}", params
                ).fetchall()
                if len(currencies) > 1:
                    raise ValueError("Multiple currencies: select a report currency")
                currency = str(currencies[0]["currency"]) if currencies else DEFAULT_CURRENCY
            currency = currency.upper()
            where += " AND t.currency = ?"
            params.append(currency)
            totals = conn.execute(
                f"""
                SELECT
                    COALESCE(SUM(CASE WHEN amount_cents > 0 THEN amount_cents ELSE 0 END), 0) AS income,
                    COALESCE(SUM(CASE WHEN amount_cents < 0 THEN amount_cents ELSE 0 END), 0) AS expense,
                    COALESCE(SUM(amount_cents), 0) AS net,
                    COUNT(*) AS cnt
                FROM transactions t
                {where}
                """,
                params,
            ).fetchone()
            categories = conn.execute(
                f"""
                SELECT
                    COALESCE(c.name, '(uncategorized)') AS category,
                    COALESCE(c.kind, 'expense') AS kind,
                    SUM(t.amount_cents) AS sum_cents,
                    COUNT(t.id) AS cnt
                FROM transactions t
                LEFT JOIN transaction_category tc ON tc.transaction_id = t.id
                LEFT JOIN categories c ON c.id = tc.category_id
                {where}
                GROUP BY category, kind
                ORDER BY sum_cents
                """,
                params,
            ).fetchall()
            counterparties = conn.execute(
                f"""
                SELECT
                    COALESCE(t.counterparty, '(unknown)') AS counterparty,
                    SUM(t.amount_cents) AS sum_cents,
                    COUNT(*) AS cnt
                FROM transactions t
                {where}
                GROUP BY counterparty
                ORDER BY sum_cents ASC
                LIMIT 10
                """,
                params,
            ).fetchall()
        return {
            "month": month,
            "account_id": account_id,
            "currency": currency,
            "budget_currency": DEFAULT_CURRENCY,
            "income_cents": int(totals["income"] or 0),
            "expense_cents": int(totals["expense"] or 0),
            "net_cents": int(totals["net"] or 0),
            "tx_count": int(totals["cnt"] or 0),
            "by_category": [
                {
                    "category": r["category"],
                    "kind": r["kind"],
                    "sum_cents": int(r["sum_cents"] or 0),
                    "count": int(r["cnt"]),
                }
                for r in categories
            ],
            "top_counterparties": [
                {
                    "counterparty": r["counterparty"],
                    "sum_cents": int(r["sum_cents"] or 0),
                    "count": int(r["cnt"]),
                }
                for r in counterparties
            ],
            "budget_status": (
                self.budget_status(month, account_id=account_id)
                if currency == DEFAULT_CURRENCY else []
            ),
        }

    # -- transfer linking --------------------------------------------

    @staticmethod
    def _auto_detect_transfers_within_conn(
        conn: sqlite3.Connection,
        new_tx_ids: Sequence[int],
        *,
        max_days: int = 5,
    ) -> int:
        """Auto-Pair fuer interne Transfers zwischen eigenen Konten.

        Strukturelle Kriterien (rein deterministisch, keine Keywords):

        * |Betrag| identisch, Vorzeichen entgegengesetzt, verschiedene Accounts,
          Datum-Distanz <= ``max_days``, beide Seiten noch nicht verlinkt.
        * **IBAN-Verknuepfung verpflichtend**: mindestens eine Seite muss per
          ``counterparty_iban`` auf das Konto der anderen Seite zeigen
          (``counterparty_iban`` einer Tx == ``account.iban`` der Partner-Tx).
          Damit werden zufaellige P2P-Rueckzahlungen mit gleichem Betrag
          ausgeschlossen, ohne auf Counterparty-Texte zu raten.
        * Eindeutigkeit: genau ein passender Partner.

        Kreditkarten-Sammelbelastungen (typischerweise ohne
        ``counterparty_iban``) werden separat ueber
        ``_auto_detect_card_settlements_within_conn`` verlinkt.

        Confidence: 1.0 selber Tag, 0.85 1 Tag, 0.7 2-3 Tage, 0.55 4-5 Tage.
        Source: ``'auto'``.
        """
        if not new_tx_ids:
            return 0
        linked = 0
        existing_links_sql = (
            "SELECT outgoing_tx_id AS id FROM transfer_links "
            "UNION SELECT incoming_tx_id FROM transfer_links"
        )
        for tx_id in new_tx_ids:
            row = conn.execute(
                "SELECT t.id, t.account_id, t.amount_cents, t.booking_date, "
                "t.counterparty_iban, a.iban AS own_iban "
                "FROM transactions t JOIN accounts a ON a.id = t.account_id "
                "WHERE t.id = ?",
                (tx_id,),
            ).fetchone()
            if row is None:
                continue
            if conn.execute(
                f"SELECT 1 FROM ({existing_links_sql}) x WHERE x.id = ?", (tx_id,)
            ).fetchone():
                continue
            own_iban_self = (row["own_iban"] or "").strip().upper() or None
            cpty_iban_self = (row["counterparty_iban"] or "").strip().upper() or None
            candidates = conn.execute(
                f"""
                SELECT t.id, t.booking_date, t.counterparty_iban,
                       a.iban AS own_iban,
                       ABS(julianday(t.booking_date) - julianday(?)) AS day_diff
                FROM transactions t
                JOIN accounts a ON a.id = t.account_id
                WHERE t.amount_cents = ?
                  AND t.account_id != ?
                  AND ABS(julianday(t.booking_date) - julianday(?)) <= ?
                  AND t.id NOT IN ({existing_links_sql})
                ORDER BY day_diff ASC, t.id ASC
                """,
                (
                    row["booking_date"],
                    -int(row["amount_cents"]),
                    int(row["account_id"]),
                    row["booking_date"],
                    max_days,
                ),
            ).fetchall()
            # IBAN-Verknuepfung pruefen: mindestens eine Seite zeigt per
            # counterparty_iban auf das eigene IBAN der jeweils anderen Tx.
            qualified: List[sqlite3.Row] = []
            for cand in candidates:
                cand_own = (cand["own_iban"] or "").strip().upper() or None
                cand_cpty = (cand["counterparty_iban"] or "").strip().upper() or None
                self_points_to_partner = (
                    cpty_iban_self is not None and cand_own is not None
                    and cpty_iban_self == cand_own
                )
                partner_points_to_self = (
                    cand_cpty is not None and own_iban_self is not None
                    and cand_cpty == own_iban_self
                )
                if self_points_to_partner or partner_points_to_self:
                    qualified.append(cand)
            if len(qualified) != 1:
                continue
            partner = qualified[0]
            day_diff = float(partner["day_diff"] or 0.0)
            if day_diff < 0.5:
                conf = 1.0
            elif day_diff < 1.5:
                conf = 0.85
            elif day_diff < 3.5:
                conf = 0.70
            else:
                conf = 0.55
            if int(row["amount_cents"]) < 0:
                out_id, in_id = int(row["id"]), int(partner["id"])
            else:
                out_id, in_id = int(partner["id"]), int(row["id"])
            try:
                conn.execute(
                    "INSERT INTO transfer_links "
                    "(outgoing_tx_id, incoming_tx_id, confidence, source) "
                    "VALUES (?, ?, ?, 'auto')",
                    (out_id, in_id, conf),
                )
                FinanceDB._refresh_transaction_natures_within_conn(conn, [out_id, in_id])
                linked += 1
            except sqlite3.IntegrityError:
                continue
        return linked

    @staticmethod
    def _auto_detect_card_settlements_within_conn(
        conn: sqlite3.Connection,
        transaction_ids: Optional[Sequence[int]] = None,
        *,
        max_days_after_statement: int = 45,
    ) -> int:
        """Auto-Link fuer Kreditkarten-Ausgleich als Statement-Settlement.

        Strukturelle Kriterien (ohne Keyword/Pattern):
        1) Kandidat ist negative Buchung auf Nicht-CC-Konto.
        2) Betrag entspricht exakt ``ABS(closing_balance_cents)`` eines
           Kreditkarten-Statements.
        3) Buchungsdatum liegt in [period_end, period_end + max_days].
        4) Eindeutigkeit: genau ein passendes Statement und Statement noch
           nicht zugeordnet.
        """
        if transaction_ids is None:
            tx_filter_sql = ""
            params: List[Any] = []
        else:
            unique_ids = sorted({int(tx_id) for tx_id in transaction_ids if int(tx_id) > 0})
            if not unique_ids:
                return 0
            placeholders = ",".join("?" for _ in unique_ids)
            tx_filter_sql = f"AND t.id IN ({placeholders})"
            params = list(unique_ids)

        rows = conn.execute(
            f"""
            SELECT t.id, t.amount_cents, t.booking_date
            FROM transactions t
            JOIN accounts a ON a.id = t.account_id
            WHERE t.amount_cents < 0
              AND COALESCE(a.account_type, '') != 'credit_card'
              AND t.id NOT IN (
                    SELECT outgoing_tx_id FROM transfer_links
                    UNION SELECT incoming_tx_id FROM transfer_links
              )
              AND t.id NOT IN (SELECT transaction_id FROM statement_settlements)
              {tx_filter_sql}
            ORDER BY t.id ASC
            """,
            params,
        ).fetchall()

        linked = 0
        for row in rows:
            tx_id = int(row["id"])
            amount_cents = int(row["amount_cents"])
            booking_date = row["booking_date"]

            candidates = conn.execute(
                """
                SELECT
                    s.id,
                    ABS(julianday(?) - julianday(s.period_end)) AS day_diff
                FROM statements s
                JOIN accounts a ON a.id = s.account_id
                LEFT JOIN statement_settlements ss ON ss.statement_id = s.id
                WHERE COALESCE(a.account_type, '') = 'credit_card'
                  AND s.period_end IS NOT NULL
                  AND s.closing_balance_cents IS NOT NULL
                  AND ABS(s.closing_balance_cents) = ABS(?)
                  AND julianday(?) >= julianday(s.period_end)
                  AND julianday(?) <= julianday(s.period_end) + ?
                  AND ss.id IS NULL
                ORDER BY day_diff ASC, s.id ASC
                """,
                (
                    booking_date,
                    amount_cents,
                    booking_date,
                    booking_date,
                    int(max_days_after_statement),
                ),
            ).fetchall()
            if len(candidates) != 1:
                continue

            day_diff = float(candidates[0]["day_diff"] or 0.0)
            if day_diff < 0.5:
                conf = 1.0
            elif day_diff < 2.5:
                conf = 0.9
            elif day_diff < 7.5:
                conf = 0.8
            else:
                conf = 0.65

            try:
                cur = conn.execute(
                    """
                    INSERT INTO statement_settlements
                    (transaction_id, statement_id, confidence, source)
                    VALUES (?, ?, ?, 'auto')
                    """,
                    (tx_id, int(candidates[0]["id"]), conf),
                )
                if int(cur.lastrowid or 0) > 0:
                    FinanceDB._refresh_transaction_natures_within_conn(conn, [tx_id])
                    linked += 1
            except sqlite3.IntegrityError:
                continue
        return linked

    def detect_statement_settlement_gaps(
        self,
        *,
        max_days_after_statement: int = 45,
        extended_search_days: int = 180,
    ) -> List[Dict[str, Any]]:
        """Liefert offene Kreditkarten-Statement-Ausgleichsfaelle als Diagnose.

        Ziel: Restfaelle nachvollziehbar klassifizieren statt manuell zu loeschen.
        Die Methode ist read-only und erzeugt keine Verknuepfungen.

        Rueckgabe pro offenem Statement:
        - status:
          - "no_candidate": kein passender Ausgleich gefunden
          - "candidate_out_of_window": Betrag passt, aber nur ausserhalb
            des produktiven Zuordnungsfensters
          - "ambiguous_in_window": mehrere passende Kandidaten im Fenster
          - "single_candidate_in_window": genau ein Kandidat im Fenster
        - Kandidatenlisten (max. 10) mit Buchung, Konto und Tagdifferenz
        """
        if max_days_after_statement <= 0:
            raise ValueError("max_days_after_statement must be > 0")
        if extended_search_days < max_days_after_statement:
            raise ValueError(
                "extended_search_days must be >= max_days_after_statement"
            )

        with self._connect() as conn:
            statements = conn.execute(
                """
                SELECT
                    s.id,
                    s.account_id,
                    s.period_end,
                    s.closing_balance_cents,
                    a.iban AS card_iban
                FROM statements s
                JOIN accounts a ON a.id = s.account_id
                LEFT JOIN statement_settlements ss ON ss.statement_id = s.id
                WHERE COALESCE(a.account_type, '') = 'credit_card'
                  AND s.period_end IS NOT NULL
                  AND s.closing_balance_cents IS NOT NULL
                  AND ss.id IS NULL
                ORDER BY s.period_end ASC, s.id ASC
                """
            ).fetchall()

            gaps: List[Dict[str, Any]] = []
            for stmt in statements:
                stmt_id = int(stmt["id"])
                period_end = str(stmt["period_end"])
                amount_cents = abs(int(stmt["closing_balance_cents"] or 0))

                candidates = conn.execute(
                    """
                    SELECT
                        t.id,
                        t.booking_date,
                        t.amount_cents,
                        a.id AS account_id,
                        a.iban,
                        ABS(julianday(t.booking_date) - julianday(?)) AS day_diff
                    FROM transactions t
                    JOIN accounts a ON a.id = t.account_id
                    WHERE t.amount_cents < 0
                      AND COALESCE(a.account_type, '') != 'credit_card'
                      AND ABS(t.amount_cents) = ?
                      AND julianday(t.booking_date) >= julianday(?)
                      AND julianday(t.booking_date) <= julianday(?) + ?
                      AND t.id NOT IN (
                            SELECT outgoing_tx_id FROM transfer_links
                            UNION SELECT incoming_tx_id FROM transfer_links
                      )
                      AND t.id NOT IN (SELECT transaction_id FROM statement_settlements)
                    ORDER BY day_diff ASC, t.booking_date ASC, t.id ASC
                    LIMIT 100
                    """,
                    (
                        period_end,
                        amount_cents,
                        period_end,
                        period_end,
                        int(extended_search_days),
                    ),
                ).fetchall()

                in_window = [
                    c for c in candidates if float(c["day_diff"] or 0.0) <= max_days_after_statement
                ]

                if not candidates:
                    status = "no_candidate"
                elif not in_window:
                    status = "candidate_out_of_window"
                elif len(in_window) == 1:
                    status = "single_candidate_in_window"
                else:
                    status = "ambiguous_in_window"

                def _serialize(rows: Sequence[sqlite3.Row]) -> List[Dict[str, Any]]:
                    out: List[Dict[str, Any]] = []
                    for row in rows[:10]:
                        out.append(
                            {
                                "transaction_id": int(row["id"]),
                                "account_id": int(row["account_id"]),
                                "account_iban": row["iban"],
                                "booking_date": row["booking_date"],
                                "amount_cents": int(row["amount_cents"]),
                                "day_diff": round(float(row["day_diff"] or 0.0), 2),
                            }
                        )
                    return out

                gaps.append(
                    {
                        "statement_id": stmt_id,
                        "statement_account_id": int(stmt["account_id"]),
                        "statement_account_iban": stmt["card_iban"],
                        "period_end": period_end,
                        "abs_closing_balance_cents": amount_cents,
                        "status": status,
                        "in_window_candidates": _serialize(in_window),
                        "extended_candidates": _serialize(candidates),
                    }
                )

            return gaps

    def detect_transfer_candidates(
        self, *, max_days: int = 5
    ) -> List[Dict[str, Any]]:
        """Liefert ALLE noch unverlinkten Transfer-Kandidaten-Paare.

        Reine Vorschlge -- es wird nichts gespeichert. UI / LLM kann sie
        anschauen, besttigen oder ablehnen. Mehrdeutige Tx (mehrere mgliche
        Partner) erscheinen entsprechend mehrfach mit unterschiedlichen
        Partnern; der User entscheidet.
        """
        sql = """
            SELECT
                t1.id AS out_id,
                t2.id AS in_id,
                t1.amount_cents AS out_amount,
                t1.booking_date AS out_date,
                t2.booking_date AS in_date,
                t1.counterparty AS out_cp,
                t2.counterparty AS in_cp,
                a1.iban AS out_iban,
                a2.iban AS in_iban,
                ABS(julianday(t2.booking_date) - julianday(t1.booking_date)) AS day_diff
            FROM transactions t1
            JOIN transactions t2
              ON t1.amount_cents = -t2.amount_cents
             AND t1.account_id != t2.account_id
             AND t1.amount_cents < 0
             AND ABS(julianday(t2.booking_date) - julianday(t1.booking_date)) <= ?
            JOIN accounts a1 ON a1.id = t1.account_id
            JOIN accounts a2 ON a2.id = t2.account_id
            WHERE t1.id NOT IN (SELECT outgoing_tx_id FROM transfer_links
                                UNION SELECT incoming_tx_id FROM transfer_links)
              AND t2.id NOT IN (SELECT outgoing_tx_id FROM transfer_links
                                UNION SELECT incoming_tx_id FROM transfer_links)
            ORDER BY day_diff ASC, t1.booking_date DESC, t1.id ASC
        """
        with self._lock, self._connect() as conn:
            rows = conn.execute(sql, (max_days,)).fetchall()
            candidates = [
                {
                    "outgoing_tx_id": int(r["out_id"]),
                    "incoming_tx_id": int(r["in_id"]),
                    "amount_cents": int(r["out_amount"]),
                    "outgoing_date": r["out_date"],
                    "incoming_date": r["in_date"],
                    "outgoing_iban": r["out_iban"],
                    "incoming_iban": r["in_iban"],
                    "outgoing_counterparty": r["out_cp"],
                    "incoming_counterparty": r["in_cp"],
                    "day_diff": float(r["day_diff"] or 0.0),
                }
                for r in rows
            ]
            # Mehrdeutigkeit sichtbar machen (Root-Cause-Kontext 2026-09-11):
            # wie viele NOCH offene Kandidaten teilen sich dieselbe
            # incoming/outgoing Tx? >0 bedeutet: pro Seite kann nur EIN
            # Kandidat verlinkt werden (UNIQUE-Constraints auf
            # transfer_links); der zuerst verknuepfte blockiert die anderen.
            in_counts: Dict[int, int] = {}
            out_counts: Dict[int, int] = {}
            for cand in candidates:
                in_counts[cand["incoming_tx_id"]] = (
                    in_counts.get(cand["incoming_tx_id"], 0) + 1
                )
                out_counts[cand["outgoing_tx_id"]] = (
                    out_counts.get(cand["outgoing_tx_id"], 0) + 1
                )
            for cand in candidates:
                cand["incoming_alternatives"] = (
                    in_counts[cand["incoming_tx_id"]] - 1
                )
                cand["outgoing_alternatives"] = (
                    out_counts[cand["outgoing_tx_id"]] - 1
                )
            return candidates

    def link_transfer(
        self,
        *,
        outgoing_tx_id: int,
        incoming_tx_id: int,
        confidence: float = 1.0,
        source: str = "user",
    ) -> int:
        """Verknpft zwei Tx als einen Transfer. Validiert Vorzeichen/Konten."""
        if outgoing_tx_id == incoming_tx_id:
            raise ValueError("outgoing and incoming must differ")
        if source not in ("user", "auto"):
            raise ValueError(f"source must be user|auto, got {source!r}")
        with self._lock, self._connect() as conn:
            out_row = conn.execute(
                "SELECT id, account_id, amount_cents FROM transactions WHERE id = ?",
                (outgoing_tx_id,),
            ).fetchone()
            in_row = conn.execute(
                "SELECT id, account_id, amount_cents FROM transactions WHERE id = ?",
                (incoming_tx_id,),
            ).fetchone()
            if out_row is None or in_row is None:
                raise ValueError("transaction not found")
            if int(out_row["amount_cents"]) >= 0:
                raise ValueError("outgoing transaction must have negative amount")
            if int(in_row["amount_cents"]) <= 0:
                raise ValueError("incoming transaction must have positive amount")
            if int(out_row["amount_cents"]) != -int(in_row["amount_cents"]):
                raise ValueError("amounts must be exact negatives of each other")
            if int(out_row["account_id"]) == int(in_row["account_id"]):
                raise ValueError("transfer must span two different accounts")
            # Root-Cause-Fix 2026-09-11: UNIQUE(outgoing_tx_id) /
            # UNIQUE(incoming_tx_id) erlauben je Seite genau eine
            # Verknuepfung. detect_transfer_candidates listet mehrdeutige
            # Kandidaten (gleiche in_id, mehrere out_ids) bewusst mehrfach --
            # verlinkt der User zwei davon, traf das alte rohe
            # sqlite3.IntegrityError die UI/Agent-Aufrufe (crash, nur
            # ValueError wurde gefangen). Jetzt: klares, actionable
            # ValueError mit der ID der bestehenden Verknuepfung.
            for side, tx_id in (
                ("outgoing", outgoing_tx_id),
                ("incoming", incoming_tx_id),
            ):
                existing = conn.execute(
                    "SELECT id, outgoing_tx_id, incoming_tx_id FROM transfer_links "
                    "WHERE outgoing_tx_id = ? OR incoming_tx_id = ?",
                    (tx_id, tx_id),
                ).fetchone()
                if existing is not None:
                    raise ValueError(
                        f"{side} transaction {tx_id} is already linked "
                        f"(link id {int(existing['id'])}: "
                        f"out={int(existing['outgoing_tx_id'])} -> "
                        f"in={int(existing['incoming_tx_id'])}); "
                        "unlink it first or choose a different pair"
                    )
            try:
                cur = conn.execute(
                    "INSERT INTO transfer_links "
                    "(outgoing_tx_id, incoming_tx_id, confidence, source) "
                    "VALUES (?, ?, ?, ?)",
                    (outgoing_tx_id, incoming_tx_id, float(confidence), source),
                )
            except sqlite3.IntegrityError as exc:
                # Safety-Net (z.B. paralleler Writer zwischen Check und
                # Insert): weiterhin lauter, klarer Fehler -- nie silent.
                raise ValueError(
                    f"transfer link conflict for out={outgoing_tx_id} / "
                    f"in={incoming_tx_id}: {exc}"
                ) from exc
            self._refresh_transaction_natures_within_conn(
                conn,
                [outgoing_tx_id, incoming_tx_id],
            )
            return int(cur.lastrowid or 0)

    def unlink_transfer(self, link_id: int) -> bool:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT outgoing_tx_id, incoming_tx_id FROM transfer_links WHERE id = ?",
                (link_id,),
            ).fetchone()
            cur = conn.execute("DELETE FROM transfer_links WHERE id = ?", (link_id,))
            if row is not None and cur.rowcount > 0:
                self._refresh_transaction_natures_within_conn(
                    conn,
                    [int(row["outgoing_tx_id"]), int(row["incoming_tx_id"])],
                )
            return cur.rowcount > 0

    def list_transfer_links(self) -> List[TransferLink]:
        sql = """
            SELECT
                tl.id, tl.outgoing_tx_id, tl.incoming_tx_id,
                tl.confidence, tl.source,
                a1.iban AS out_iban, a2.iban AS in_iban,
                t1.amount_cents AS out_amount,
                t1.booking_date AS out_date, t2.booking_date AS in_date,
                t1.counterparty AS out_cp, t2.counterparty AS in_cp
            FROM transfer_links tl
            JOIN transactions t1 ON t1.id = tl.outgoing_tx_id
            JOIN transactions t2 ON t2.id = tl.incoming_tx_id
            JOIN accounts a1 ON a1.id = t1.account_id
            JOIN accounts a2 ON a2.id = t2.account_id
            ORDER BY t1.booking_date DESC, tl.id DESC
        """
        with self._lock, self._connect() as conn:
            rows = conn.execute(sql).fetchall()
            return [
                TransferLink(
                    id=int(r["id"]),
                    outgoing_tx_id=int(r["outgoing_tx_id"]),
                    incoming_tx_id=int(r["incoming_tx_id"]),
                    confidence=float(r["confidence"]),
                    source=r["source"],
                    outgoing_account_iban=r["out_iban"],
                    incoming_account_iban=r["in_iban"],
                    outgoing_amount_cents=int(r["out_amount"]),
                    outgoing_booking_date=r["out_date"],
                    incoming_booking_date=r["in_date"],
                    outgoing_counterparty=r["out_cp"],
                    incoming_counterparty=r["in_cp"],
                )
                for r in rows
            ]

    def find_statements_with_incomplete_balances(self) -> List["Statement"]:
        """Gibt Statements zurueck, bei denen Eroeffnungs- oder Schlusssaldo
        NULL oder 0 ist (Indiz fuer fehlgeschlagene Header-Extraktion).
        """
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM statements
                WHERE opening_balance_cents IS NULL
                   OR closing_balance_cents IS NULL
                   OR opening_balance_cents = 0
                   OR closing_balance_cents = 0
                ORDER BY id
                """
            ).fetchall()
            return [self._row_to_statement(r) for r in rows]

    def update_statement_balances(
        self,
        statement_id: int,
        *,
        opening_balance: Optional[float] = None,
        closing_balance: Optional[float] = None,
        period_start: Optional[str] = None,
        period_end: Optional[str] = None,
    ) -> bool:
        """Aktualisiert Saldo- und Perioden-Felder eines bestehenden Statements.

        Nur explizit uebergebene Nicht-None-Felder werden geschrieben.
        Gibt True zurueck, wenn mindestens eine Zeile veraendert wurde.
        """
        sets: List[str] = []
        params: List[Any] = []
        if opening_balance is not None:
            sets.append("opening_balance_cents = ?")
            params.append(_to_cents(opening_balance))
        if closing_balance is not None:
            sets.append("closing_balance_cents = ?")
            params.append(_to_cents(closing_balance))
        if period_start is not None:
            sets.append("period_start = ?")
            params.append(period_start)
        if period_end is not None:
            sets.append("period_end = ?")
            params.append(period_end)
        if not sets:
            return False
        params.append(statement_id)
        with self._lock, self._connect() as conn:
            cur = conn.execute(
                f"UPDATE statements SET {', '.join(sets)} WHERE id = ?",
                params,
            )
            return cur.rowcount > 0

    def update_statement_consistency(
        self,
        statement_id: int,
        *,
        total_credits: Optional[float] = None,
        total_debits: Optional[float] = None,
        consistency_passed: Optional[bool] = None,
        consistency_errors: Optional[str] = None,
        needs_review: Optional[bool] = None,
    ) -> bool:
        """Aktualisiert die Konsistenz-/Review-Zustaende eines Statements.

        Wird nach Import oder Reparatur (z. B. korrigierte Saetze) aufgerufen,
        um das Ergebnis von :func:`finance.consistency` zu persistieren.
        Nur explizit uebergebene Felder werden geschrieben; ``None`` bei
        ``consistency_passed`` laesst die Spalte bewusst unangetastet.
        Gibt True zurueck, wenn mindestens eine Zeile veraendert wurde.
        """
        sets: List[str] = []
        params: List[Any] = []
        if total_credits is not None:
            sets.append("total_credits_cents = ?")
            params.append(_to_cents(total_credits))
        if total_debits is not None:
            sets.append("total_debits_cents = ?")
            params.append(_to_cents(total_debits))
        if consistency_passed is not None:
            sets.append("consistency_passed = ?")
            params.append(int(bool(consistency_passed)))
        if consistency_errors is not None:
            sets.append("consistency_errors = ?")
            params.append(consistency_errors)
        if needs_review is not None:
            sets.append("needs_review = ?")
            params.append(int(bool(needs_review)))
        if not sets:
            return False
        params.append(statement_id)
        with self._lock, self._connect() as conn:
            cur = conn.execute(
                f"UPDATE statements SET {', '.join(sets)} WHERE id = ?",
                params,
            )
            return cur.rowcount > 0

    def find_review_needed_statements(self) -> List["Statement"]:
        """Gibt alle Statements zurueck, die einer manuellen Pruefung beduerfen.

        Das sind Zeilen mit ``needs_review = 1`` (fehlgeschlagene oder
        unvollstaendige Konsistenzpruefung). Nach ``imported_at`` absteigend,
        damit die neusten zuerst angezeigt werden.
        """
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM statements
                WHERE needs_review = 1
                ORDER BY imported_at DESC, id DESC
                """
            ).fetchall()
            return [self._row_to_statement(r) for r in rows]

    def get_statement_transactions(self, statement_id: int) -> List[Transaction]:
        """Gibt alle Transaktionen eines Statements in Buchungsreihenfolge zurueck.

        Wird im Review-Workflow verwendet, um die Einzelbuchungen eines
        zur Pruefung markierten Statements anzuzeigen.
        """
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM transactions
                WHERE statement_id = ?
                ORDER BY booking_date ASC, id ASC
                """,
                (statement_id,),
            ).fetchall()
            return [self._row_to_transaction(r) for r in rows]

    def evaluate_statement_import_completeness(
        self,
        statement_id: int,
        *,
        settlement_window_days: int = 45,
        statement_lookback_days: int = 15,
    ) -> Dict[str, Any]:
        """Bewertet Datenvollstaendigkeit fuer Ausgleichs-/Transferlogik.

        Fokus liegt auf Kreditkarten-Statements: ohne Gegenkonto-Daten
        (Nicht-CC) kann kein struktureller Ausgleich gefunden werden.
        Die Bewertung ist deterministisch und read-only.
        """
        if settlement_window_days <= 0:
            raise ValueError("settlement_window_days must be > 0")
        if statement_lookback_days < 0:
            raise ValueError("statement_lookback_days must be >= 0")

        with self._lock, self._connect() as conn:
            row = conn.execute(
                """
                SELECT
                    s.id,
                    s.account_id,
                    s.period_start,
                    s.period_end,
                    s.closing_balance_cents,
                    a.account_type,
                    a.iban
                FROM statements s
                JOIN accounts a ON a.id = s.account_id
                WHERE s.id = ?
                """,
                (statement_id,),
            ).fetchone()
            if row is None:
                raise ValueError(f"statement not found: {statement_id}")

            account_type = (row["account_type"] or "").strip()
            period_end = row["period_end"]
            closing_balance_cents = row["closing_balance_cents"]

            base: Dict[str, Any] = {
                "statement_id": int(row["id"]),
                "account_id": int(row["account_id"]),
                "account_iban": row["iban"],
                "account_type": account_type or None,
                "period_start": row["period_start"],
                "period_end": period_end,
                "settlement_window_days": int(settlement_window_days),
            }

            if account_type != "credit_card":
                base.update(
                    {
                        "status": "not_applicable",
                        "severity": "info",
                        "message": "Completeness check is focused on credit-card settlement coverage.",
                    }
                )
                return base

            if not period_end:
                base.update(
                    {
                        "status": "missing_statement_period_end",
                        "severity": "error",
                        "message": "Statement has no period_end; settlement coverage cannot be evaluated.",
                    }
                )
                return base

            tx_row = conn.execute(
                """
                SELECT COUNT(*) AS cnt
                FROM transactions t
                JOIN accounts a ON a.id = t.account_id
                WHERE COALESCE(a.account_type, '') != 'credit_card'
                  AND julianday(t.booking_date) >= julianday(?)
                  AND julianday(t.booking_date) <= julianday(?) + ?
                """,
                (period_end, period_end, int(settlement_window_days)),
            ).fetchone()
            counterpart_tx_count = int(tx_row["cnt"] if tx_row else 0)

            stmt_row = conn.execute(
                """
                SELECT COUNT(*) AS cnt
                FROM statements s
                JOIN accounts a ON a.id = s.account_id
                WHERE COALESCE(a.account_type, '') != 'credit_card'
                  AND s.period_end IS NOT NULL
                  AND julianday(s.period_end) >= julianday(?) - ?
                  AND julianday(s.period_end) <= julianday(?) + ?
                """,
                (
                    period_end,
                    int(statement_lookback_days),
                    period_end,
                    int(settlement_window_days),
                ),
            ).fetchone()
            counterpart_statement_count = int(stmt_row["cnt"] if stmt_row else 0)

            same_amount_tx = 0
            if closing_balance_cents is not None:
                amt_row = conn.execute(
                    """
                    SELECT COUNT(*) AS cnt
                    FROM transactions t
                    JOIN accounts a ON a.id = t.account_id
                    WHERE COALESCE(a.account_type, '') != 'credit_card'
                      AND t.amount_cents < 0
                      AND ABS(t.amount_cents) = ABS(?)
                      AND julianday(t.booking_date) >= julianday(?)
                      AND julianday(t.booking_date) <= julianday(?) + ?
                    """,
                    (
                        int(closing_balance_cents),
                        period_end,
                        period_end,
                        int(settlement_window_days),
                    ),
                ).fetchone()
                same_amount_tx = int(amt_row["cnt"] if amt_row else 0)

            settled_row = conn.execute(
                "SELECT 1 FROM statement_settlements WHERE statement_id = ? LIMIT 1",
                (statement_id,),
            ).fetchone()
            already_settled = settled_row is not None

            base.update(
                {
                    "counterpart_transaction_count": counterpart_tx_count,
                    "counterpart_statement_count": counterpart_statement_count,
                    "same_amount_transaction_count": same_amount_tx,
                    "already_settled": already_settled,
                }
            )

            if already_settled:
                base.update(
                    {
                        "status": "complete_settled",
                        "severity": "info",
                        "message": "Statement already has a settlement link.",
                    }
                )
            elif counterpart_statement_count == 0 and counterpart_tx_count == 0:
                base.update(
                    {
                        "status": "missing_counterpart_data",
                        "severity": "error",
                        "message": "No non-credit-card statements/transactions found in settlement window.",
                    }
                )
            elif counterpart_tx_count == 0:
                base.update(
                    {
                        "status": "missing_counterpart_transactions",
                        "severity": "warning",
                        "message": "Counterpart statements exist, but no non-credit-card transactions in settlement window.",
                    }
                )
            elif same_amount_tx == 0:
                base.update(
                    {
                        "status": "no_same_amount_candidate",
                        "severity": "warning",
                        "message": "Counterpart data exists, but no same-amount settlement candidate found in window.",
                    }
                )
            else:
                base.update(
                    {
                        "status": "counterpart_data_present",
                        "severity": "info",
                        "message": "Counterpart data and same-amount candidates are present.",
                    }
                )

            return base

    def relink_all_transfers(self, *, max_days: int = 5) -> int:
        """Fuehrt Auto-Erkennung fuer alle noch unverlinkten internen
        Geldbewegungen erneut durch.

        Erforderlich, wenn ein weiteres Konto nachtraeglich importiert wird
        (z.B. Kreditkarten-Statements), damit bestehende Gegenbuchungen auf
        dem Girokonto rueckwirkend verknuepft werden. Deckt beide Formen ab:
        klassisches Tx-Paar (-X/+X) und statement-basierte Kreditkarten-
        Ausgleichsbuchungen ohne explizites +X Gegenstueck.

        Gibt die Anzahl neu angelegter Verknuepfungen zurueck.
        """
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id FROM transactions
                WHERE id NOT IN (
                    SELECT outgoing_tx_id FROM transfer_links
                    UNION
                    SELECT incoming_tx_id FROM transfer_links
                )
                ORDER BY id
                """
            ).fetchall()
            all_unlinked = [int(r["id"]) for r in rows]
            linked_pairs = self._auto_detect_transfers_within_conn(
                conn, all_unlinked, max_days=max_days
            )
            linked_settlements = self._auto_detect_card_settlements_within_conn(conn)
            return linked_pairs + linked_settlements

    # -- helpers -----------------------------------------------------

    @staticmethod
    def _row_to_statement(row: sqlite3.Row) -> Statement:
        consistency_passed = row["consistency_passed"]
        return Statement(
            id=int(row["id"]),
            account_id=int(row["account_id"]),
            period_start=row["period_start"],
            period_end=row["period_end"],
            opening_balance_cents=row["opening_balance_cents"],
            closing_balance_cents=row["closing_balance_cents"],
            total_credits_cents=row["total_credits_cents"],
            total_debits_cents=row["total_debits_cents"],
            consistency_passed=None if consistency_passed is None else bool(consistency_passed),
            consistency_errors=row["consistency_errors"],
            needs_review=bool(row["needs_review"]),
            source_pdf_hash=row["source_pdf_hash"],
            source_filename=row["source_filename"],
            imported_at=row["imported_at"],
        )

    @staticmethod
    def _row_to_transaction(row: sqlite3.Row) -> Transaction:
        return Transaction(
            id=int(row["id"]),
            statement_id=int(row["statement_id"]),
            account_id=int(row["account_id"]),
            booking_date=row["booking_date"],
            value_date=row["value_date"],
            amount_cents=int(row["amount_cents"]),
            currency=row["currency"],
            counterparty=row["counterparty"],
            counterparty_iban=row["counterparty_iban"],
            purpose=row["purpose"],
            booking_type=row["booking_type"],
            transaction_nature=(
                row["transaction_nature"]
                if "transaction_nature" in row.keys() and row["transaction_nature"]
                else "ordinary"
            ),
            category=row["category"] if "category" in row.keys() else None,
            dedup_hash=row["dedup_hash"],
        )


__all__ = [
    "FinanceDB",
    "Bank",
    "Account",
    "Statement",
    "Transaction",
    "Category",
    "CounterpartyRule",
    "Budget",
    "TransferLink",
    "_hash_file",
    "_to_cents",
    "_from_cents",
    "_normalize_iban",
    "_normalize_counterparty",
]
