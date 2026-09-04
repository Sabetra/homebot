"""Kontoauszug-Extraktor: Docling + LLMStructuredWrapper.

Pipeline:
    1. PDF  Docling (Markdown + Tabellen + KG-optimierte Chunks)
    2. SHA-256 ber PDF-Bytes  Idempotenz-Schlssel (``source_pdf_hash``)
    3. Markdown wird in zwei Phasen extrahiert:
       a) Header-Pass: ``StatementHeader`` aus Kopf- UND End-Window des
          Auszugs (Bank / Konto / Periode / Salden). Dadurch sind Endsaldo
          und Periodenende robust auch bei langen Statements erfassbar.
       b) Transaktions-Pass: Markdown wird in token-bewusst dimensionierte
          Chunks aufgeteilt; pro Chunk wird ``TransactionBatch`` (NUR
          die Buchungen aus dem Ausschnitt) extrahiert. Resultate werden
          dedupliziert und in chronologischer Reihenfolge zusammengefuehrt.
    4. Beides wird zu ``ExtractedStatement`` zusammengesetzt -- die Pydantic-
       Validierung (Datumsformat, IBAN, Salden-Reihenfolge) bleibt unveraendert.
    5. ``FinanceDB.upsert_*`` schreibt Bank/Konto/Statement/Transactions.

Warum Chunking statt einer einzigen Extraktion?
    Bei Bank-Auszuegen mit 5+ Seiten erreicht das gerenderte Markdown leicht
    8-12k Tokens. Mit n_ctx=16384 bleibt nach Prompt-Overhead nur ein
    Output-Budget < 4k Tokens; die GBNF-Grammar produziert dann zwar
    einen syntaktisch validen JSON-Praefix, der aber mitten in der
    Transaktions-Liste abgeschnitten wird ("Expecting ',' delimiter").
    Divide-and-Conquer beseitigt diese Klasse von Bug strukturell, ohne
    Heuristik oder Output-Reparatur.

Strikte Trennung: kein Touch auf RAG/KG, kein Persistenz-Pfad ausser
``finance.db``.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field

from finance.db_schema import FinanceDB, _hash_file
from finance.models import (
    DEFAULT_CURRENCY,
    ExtractedStatement,
    ExtractedTransaction,
    StatementHeader,
)
from finance.token_budget import resolve_context_tokens

logger = logging.getLogger(__name__)


_HEADER_SYSTEM_PROMPT = (
    "Du extrahierst die Kopfdaten eines Bank-Kontoauszugs als JSON gemaess "
    "Schema. Dir werden zwei Ausschnitte gegeben: Anfang und Ende des "
    "Auszugs.\n\n"
    "Strikte Regeln:\n"
    "- Datum: uebernimm die Schreibweise aus dem Auszug woertlich "
    "(z.B. '01.07.2024' oder '2024-07-01'). Nicht selbst umrechnen.\n"
    "- Salden als signed Float (Soll = negativ).\n"
    "- Waehrung als ISO-4217 (EUR, USD, CHF...).\n"
    "- 'iban': IBAN des Kontos (z.B. 'CH9300762011623852957'). Bei Kreditkarten "
    "ohne eigene IBAN stattdessen die Kartennummer eintragen (z.B. '0000800472744336'). "
    "Ohne Leerzeichen, Grossbuchstaben/Ziffern.\n"
    "- 'account_type': eines der folgenden Schluesswoerter -- checking (Girokonto), "
    "credit_card (Kreditkarte), savings (Sparkonto/Tagesgeld), cash (Bargeld), "
    "investment (Depot/Wertpapiere), other (sonstiges). Nur diese Werte, nichts anderes.\n"
    "- Erfinde nichts. Felder, die im Auszug nicht erkennbar sind, auf null."
)


_TX_SYSTEM_PROMPT = (
    "Du extrahierst Buchungen aus einem AUSSCHNITT eines Bank-Kontoauszugs "
    "als JSON gemaess Schema.\n\n"
    "Strikte Regeln:\n"
    "- Liste NUR Buchungen, die WOERTLICH im Ausschnitt stehen.\n"
    "- Keine Buchungen aus Saldo-Zwischensummen, Zinsstaffeln oder Beispielen.\n"
    "- 'booking_date' MUSS ein vollstaendiges Kalenderdatum der Buchung sein. "
    "Keine Monats-/Periodenmarker, keine Monats-Jahres-Fragmente wie '09.24', "
    "keine Wechselkurs- oder Abrechnungslabels.\n"
    "- Datum: uebernimm die Schreibweise aus dem Auszug woertlich "
    "(z.B. '15.07.2024', '15-07-24' oder '2024-07-15') -- die "
    "Normalisierung uebernimmt die Verarbeitung danach. Wenn der Auszug "
    "nur Tag+Monat zeigt, leite das Jahr aus der Auszugsperiode ab und gib "
    "trotzdem ein vollstaendiges Datum aus. NICHT selbststaendig raten, wenn "
    "kein eindeutiges Buchungsdatum erkennbar ist -- dann die Zeile weglassen.\n"
    "- Betraege signed Float: Gutschrift positiv, Belastung negativ.\n"
    "- Wenn account_type='credit_card': Vorzeichen aus Cashflow-Sicht setzen: "
    "Kartenumsatz/Belastung/Gebuehr/Bargeldbezug = negativ; "
    "Rueckerstattung/Gutschrift/Zahlungseingang = positiv.\n"
    "- 'counterparty' und 'purpose' sind zentrale Felder fuer Auswertung und "
    "Kategorisierung: uebernimm den Zahlungs-/Haendlernamen und den "
    "Buchungstext woertlich aus der Zeile, sobald vorhanden.\n"
    "- Lasse NICHT gleichzeitig counterparty und purpose leer, wenn in der "
    "Buchungszeile ein Text steht.\n"
    "- 'booking_type' ist ein KURZES Label (max ~5 Worte), niemals ein Satz, "
    "keine Klammer-Kommentare oder Erlaeuterungen. Wenn kein kurzer Typ-Begriff "
    "klar erkennbar ist, setze booking_type auf null. Niemals den kompletten "
    "Buchungstext in booking_type kopieren.\n"
    "- 'purpose' wortwoertlich aus dem Auszug, ohne Zusaetze.\n"
    "- Wenn der Ausschnitt KEINE Buchung enthaelt, liefere eine leere Liste."
)

_FULL_DATE_FORMATS: Tuple[str, ...] = (
    "%Y-%m-%d",
    "%d.%m.%Y",
    "%d.%m.%y",
    "%d-%m-%Y",
    "%d-%m-%y",
    "%d/%m/%Y",
    "%d/%m/%y",
)

_PARTIAL_DAY_MONTH_RE = re.compile(r"^(\d{1,2})[./-](\d{1,2})$")
_MONTH_YEAR_RE = re.compile(r"^(\d{1,2})[./-](\d{2,4})$")
_IBAN_LIKE_RE = re.compile(r"^[A-Z]{2}\d{2}[A-Z0-9]{11,30}$")


class _RawExtractedTransaction(BaseModel):
    booking_date: str = Field(
        min_length=1,
        description=(
            "Buchungsdatum aus der Buchungszeile. Vollstaendiges Datum liefern, "
            "keine Monats-/Periodenmarker."
        ),
    )
    value_date: Optional[str] = Field(
        default=None,
        description="Wertstellungsdatum aus der Zeile, sonst null.",
    )
    amount: float = Field(description="Betrag signed: Eingang positiv, Ausgang negativ.")
    currency: str = Field(default=DEFAULT_CURRENCY, description="ISO-4217 Waehrungscode")
    counterparty: Optional[str] = Field(
        default=None,
        description="Empfaenger-/Haendlername aus der Buchungszeile (woertlich).",
        max_length=140,
    )
    counterparty_iban: Optional[str] = Field(
        default=None,
        description="Gegenkonto-IBAN wenn vorhanden, sonst null.",
        max_length=34,
    )
    purpose: Optional[str] = Field(
        default=None,
        description="Buchungstext/Verwendungszweck aus der Zeile (woertlich).",
        max_length=280,
    )
    booking_type: Optional[str] = Field(
        default=None,
        description="Kurzlabel der Buchungsart, nicht der volle Buchungstext.",
        max_length=280,
    )


class _RawTransactionBatch(BaseModel):
    transactions: List[_RawExtractedTransaction] = Field(default_factory=list)


class _ChunkTransactionNormalizationError(ValueError):
    """Raised when a chunk contains transaction rows that cannot be canonicalized."""


@dataclass
class StatementImportResult:
    """Ergebnis eines Auszug-Imports."""

    success: bool
    statement_id: Optional[int] = None
    account_id: Optional[int] = None
    bank_id: Optional[int] = None
    inserted_transactions: int = 0
    duplicate_transactions: int = 0
    skipped_existing_pdf: bool = False
    reconcile_new_links: int = 0
    settlement_gap_count: Optional[int] = None
    settlement_gap_status_counts: Optional[Dict[str, int]] = None
    completeness_check: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    extracted: Optional[ExtractedStatement] = None


# Token-Budget-Modell (deterministisch, keine Magic Numbers):
#
# Gemma-4-E4B / Mistral-Familie tokenisieren deutsches Markdown zu
# ~3.8 chars/token. Wir reservieren strukturell:
#   - n_ctx           = 16384 (LLM_CONTEXT_SIZE, vom Loader gesetzt)
#   - max_tokens_out  = adaptiv pro Chunk (siehe _adaptive_tx_max_tokens)
#   - safety_margin   = 800 Tokens (System-Prompt + Schema-Hints + Padding)
#
# Bei 28k Zeichen Chunk -> ~7400 Tokens Prompt + ~7000 Tokens verfuegbar
# fuer Output -> reicht fuer ~150 Buchungen pro Chunk (jede ~45 Tokens
# inkl. JSON-Overhead). Der adaptive Output-Berechner stellt sicher,
# dass max_tokens_out nie groesser ist als (n_ctx - prompt_tokens -
# safety_margin) -- damit wird Truncation strukturell ausgeschlossen,
# nicht via fixe Konstante.
_TX_CHARS_PER_TOKEN = 3.8
_TX_SAFETY_MARGIN_TOKENS = 800
_TX_CHUNK_CHARS = 28_000
_TX_CHUNK_OVERLAP = 800  # Ueberlappung haelt Tabellen-Zeilen, die ueber Chunk-
# Grenzen fallen, in mind. einem Chunk vollstaendig.
_TX_ADAPTIVE_MIN_CHARS = 2_400
_TX_ADAPTIVE_MAX_DEPTH = 4
_HEADER_WINDOW_CHARS = 6_000
_TX_MAX_TOKENS_FLOOR = 1024
_TX_MAX_TOKENS_CEIL = 8192
_HEADER_MAX_TOKENS = 1024


def _adaptive_tx_max_tokens(chunk_chars: int, n_ctx: int) -> int:
    """Bestimmt das Output-Budget pro Chunk strukturell aus n_ctx.

    Verhindert sowohl JSON-Truncation (zu kleines Budget) als auch
    Prefill-Overflow (Budget + Prompt > n_ctx). Floor/Ceil schuetzen
    gegen Degenerate-Faelle (winziger Chunk, der adaptive D&C ausgeloest
    hat, soll trotzdem Mindest-Budget bekommen).
    """
    prompt_tokens_estimate = int(chunk_chars / _TX_CHARS_PER_TOKEN)
    available = n_ctx - prompt_tokens_estimate - _TX_SAFETY_MARGIN_TOKENS
    return max(_TX_MAX_TOKENS_FLOOR, min(_TX_MAX_TOKENS_CEIL, available))


class FinanceExtractor:
    """Orchestriert Docling-Konvertierung + strukturierte LLM-Extraktion."""

    def __init__(
        self,
        llm_client: Any,
        *,
        db: Optional[FinanceDB] = None,
        max_retries: int = 2,
    ) -> None:
        if llm_client is None:
            raise ValueError("llm_client is required for FinanceExtractor")
        # Lazy imports halten den Modul-Load leichtgewichtig (Docling+llama
        # ziehen viele Abhngigkeiten).
        from llm_structured_wrapper import LLMStructuredWrapper

        self.db = db or FinanceDB.get_instance()
        self._wrapper = LLMStructuredWrapper(
            llm_client=llm_client,
            max_retries=max_retries,
            temperature=0.0,
            enable_logging=False,
        )

        # Einheitliche n_ctx-Aufloesung ueber finance.token_budget.
        self._n_ctx = resolve_context_tokens(llm_client)

    # -- public API --------------------------------------------------

    def import_pdf(
        self,
        pdf_path: str,
        *,
        source_filename: Optional[str] = None,
        prepared_markdown: Optional[str] = None,
    ) -> StatementImportResult:
        """Konvertiert ein Kontoauszug-PDF und persistiert das Ergebnis.

        ``prepared_markdown`` ueberspringt die Docling-Konvertierung -- wird
        vom Bulk-Import (import_pdfs_batch) benutzt, das Docling im
        Producer-Thread vorzieht.
        """
        pdf_path = str(Path(pdf_path).resolve())
        if not Path(pdf_path).is_file():
            return StatementImportResult(success=False, error=f"PDF not found: {pdf_path}")

        # 1) Idempotenz-Check ber PDF-Hash
        try:
            pdf_hash = _hash_file(pdf_path)
        except OSError as exc:
            return StatementImportResult(success=False, error=f"Cannot read PDF: {exc}")

        existing = self.db.find_statement_by_pdf_hash(pdf_hash)
        existing_tx_count: Optional[int] = None
        if existing is not None:
            existing_tx_count = self.db.get_statement_transaction_count(existing.id)
            if existing_tx_count > 0:
                logger.info(f"PDF already imported (statement_id={existing.id}); skipping")
                (
                    reconcile_new_links,
                    settlement_gap_count,
                    settlement_gap_status_counts,
                    completeness_check,
                ) = self._run_post_import_diagnostics(existing.id)
                return StatementImportResult(
                    success=True,
                    statement_id=existing.id,
                    account_id=existing.account_id,
                    skipped_existing_pdf=True,
                    reconcile_new_links=reconcile_new_links,
                    settlement_gap_count=settlement_gap_count,
                    settlement_gap_status_counts=settlement_gap_status_counts,
                    completeness_check=completeness_check,
                )
            logger.warning(
                "Found incomplete existing statement for pdf_hash=%s (statement_id=%s, tx_count=0); reimporting",
                pdf_hash,
                existing.id,
            )

        # 2) Docling-Konvertierung (oder vorbereiteter Markdown vom Bulk-Producer)
        if prepared_markdown is not None:
            markdown = prepared_markdown
        else:
            conv = self._docling_convert(pdf_path)
            if isinstance(conv, StatementImportResult):
                return conv  # Fehlerfall: bereits als Result verpackt
            markdown = conv

        # 3) Strukturierte LLM-Extraktion in zwei Phasen (Header + Tx-Chunks)
        try:
            extracted = self._extract_full(markdown)
        except Exception as exc:
            logger.exception("Structured extraction failed")
            return StatementImportResult(
                success=False, error=f"Structured extraction failed: {exc}"
            )

        # 4) Persistierung
        try:
            tx_dicts: List[Dict[str, Any]] = [tx.model_dump() for tx in extracted.transactions]
            if existing is not None and existing_tx_count == 0:
                self.db.delete_statement(existing.id)
            bank_id, account_id, statement_id, inserted, duplicates = self.db.persist_statement_import(
                bank_name=extracted.bank.name,
                bank_bic=extracted.bank.bic,
                bank_country_code=extracted.bank.country_code,
                iban=extracted.account.iban,
                account_holder=extracted.account.account_holder,
                currency=extracted.account.currency,
                account_type=extracted.account.account_type,
                source_pdf_hash=pdf_hash,
                source_filename=source_filename or Path(pdf_path).name,
                period_start=extracted.period_start,
                period_end=extracted.period_end,
                opening_balance=extracted.opening_balance,
                closing_balance=extracted.closing_balance,
                transactions=tx_dicts,
            )
        except Exception as exc:
            logger.exception("Persistence failed")
            return StatementImportResult(
                success=False, error=f"DB persistence failed: {exc}", extracted=extracted
            )

        # 5) Durchgaengige Post-Import-Reconcile-Pipeline
        (
            reconcile_new_links,
            settlement_gap_count,
            settlement_gap_status_counts,
            completeness_check,
        ) = self._run_post_import_diagnostics(statement_id)

        return StatementImportResult(
            success=True,
            statement_id=statement_id,
            account_id=account_id,
            bank_id=bank_id,
            inserted_transactions=inserted,
            duplicate_transactions=duplicates,
            reconcile_new_links=reconcile_new_links,
            settlement_gap_count=settlement_gap_count,
            settlement_gap_status_counts=settlement_gap_status_counts,
            completeness_check=completeness_check,
            extracted=extracted,
        )

    def _run_post_import_diagnostics(
        self,
        statement_id: int,
    ) -> tuple[int, Optional[int], Optional[Dict[str, int]], Optional[Dict[str, Any]]]:
        reconcile_new_links = 0
        settlement_gap_count: Optional[int] = None
        settlement_gap_status_counts: Optional[Dict[str, int]] = None
        completeness_check: Optional[Dict[str, Any]] = None
        try:
            # Erneut global relinken: deckt Altbestand + frisch importierte
            # Statements/Konten in einem Schritt deterministisch ab.
            reconcile_new_links = self.db.relink_all_transfers(max_days=5)

            # Offene Settlement-Luecken systematisch klassifizieren.
            gap_rows = self.db.detect_statement_settlement_gaps(
                max_days_after_statement=45,
                extended_search_days=180,
            )
            settlement_gap_count = len(gap_rows)
            gap_counts = {
                "no_candidate": 0,
                "candidate_out_of_window": 0,
                "ambiguous_in_window": 0,
                "single_candidate_in_window": 0,
            }
            for row in gap_rows:
                status = str(row.get("status") or "")
                if status in gap_counts:
                    gap_counts[status] += 1
            settlement_gap_status_counts = gap_counts

            # Harter Coverage-Check fuer den importierten Auszug.
            completeness_check = self.db.evaluate_statement_import_completeness(
                statement_id=statement_id,
                settlement_window_days=45,
                statement_lookback_days=15,
            )
            severity = str(completeness_check.get("severity") or "info")
            if severity in {"warning", "error"}:
                logger.warning(
                    "Import completeness check flagged %s: statement_id=%s status=%s message=%s",
                    severity,
                    statement_id,
                    completeness_check.get("status"),
                    completeness_check.get("message"),
                )
        except Exception as exc:
            # Import bleibt erfolgreich, Diagnose-Teilfehler wird transparent gemeldet.
            logger.exception("Post-import reconcile pipeline failed")
            completeness_check = {
                "status": "reconcile_pipeline_failed",
                "severity": "error",
                "message": str(exc),
                "statement_id": statement_id,
            }

        return (
            reconcile_new_links,
            settlement_gap_count,
            settlement_gap_status_counts,
            completeness_check,
        )

    # -- public helpers --------------------------------------------

    def _docling_convert(self, pdf_path: str) -> Any:
        """Konvertiert PDF -> Markdown via Docling. Thread-safe (DoclingProcessor
        haelt selbst den GPU-Zugriff via cuda_lock fern). Wird vom Producer-
        Thread im Bulk-Import aufgerufen, damit die CPU-bound Docling-Stage
        parallel zur LLM-Konsumstage laeufen kann.

        Returns markdown:str on success, StatementImportResult(error) on failure.
        """
        try:
            from utils.docling_processor import DoclingProcessor
        except ImportError as exc:
            return StatementImportResult(
                success=False, error=f"Docling not available: {exc}"
            )
        processor = DoclingProcessor.get_instance()
        result = processor.convert_file(pdf_path)
        if not result.success or not result.text:
            return StatementImportResult(
                success=False,
                error=f"Docling conversion failed: {result.error or 'empty output'}",
            )
        return result.text

    def import_pdfs_batch(
        self,
        pdf_paths: List[str],
        *,
        source_filenames: Optional[List[Optional[str]]] = None,
        progress_cb: Optional[Any] = None,
    ) -> List[StatementImportResult]:
        """Bulk-Import mehrerer PDFs mit Producer/Consumer-Pipeline.

        Architektur (Strict Ordering, kein cuda_lock-Refactor):
          - Producer-Thread konvertiert PDFs sequentiell via Docling (CPU).
          - Main-Thread konsumiert die Markdowns sequentiell, ruft den LLM
            seriell auf (haelt cuda_lock-Invariante intakt).
          - Queue-Buffer (maxsize=2) verhindert RAM-Overflow bei vielen PDFs
            und gibt dem Producer Backpressure.

        Speedup-Modell: bei N PDFs spart der Producer (N-1) * docling_time
        gegenueber der seriellen import_pdf-Schleife, ohne die LLM-
        Serialisierung zu beruehren.
        """
        import queue
        import threading

        if source_filenames is None:
            source_filenames = [None] * len(pdf_paths)
        if len(source_filenames) != len(pdf_paths):
            raise ValueError("source_filenames length must match pdf_paths length")

        # Queue-Element: (idx, pdf_path, source_filename, markdown_or_result, exception)
        work_queue: "queue.Queue[Tuple[int, str, Optional[str], Any, Optional[BaseException]]]" = queue.Queue(maxsize=2)
        SENTINEL_IDX = -1

        def producer() -> None:
            for idx, (path, src_name) in enumerate(zip(pdf_paths, source_filenames)):
                try:
                    conv = self._docling_convert(path)
                    work_queue.put((idx, path, src_name, conv, None))
                except BaseException as exc:  # noqa: BLE001 -- Producer reicht jede Exception weiter
                    work_queue.put((idx, path, src_name, None, exc))
            work_queue.put((SENTINEL_IDX, "", None, None, None))

        producer_thread = threading.Thread(target=producer, daemon=True, name="finance-docling-producer")
        producer_thread.start()

        results: List[Optional[StatementImportResult]] = [None] * len(pdf_paths)
        consumed = 0
        while True:
            idx, path, src_name, conv, exc = work_queue.get()
            if idx == SENTINEL_IDX:
                break
            if exc is not None:
                results[idx] = StatementImportResult(
                    success=False, error=f"Docling producer failed: {exc}"
                )
            elif isinstance(conv, StatementImportResult):
                results[idx] = conv  # Docling-Fehler durchreichen
            else:
                # conv ist Markdown -> Pipeline weiternutzen, Docling-Doppel-
                # Konvertierung explizit vermeiden via prepared_markdown.
                results[idx] = self.import_pdf(
                    path,
                    source_filename=src_name,
                    prepared_markdown=conv,
                )
            consumed += 1
            if progress_cb is not None:
                try:
                    progress_cb(consumed, len(pdf_paths))
                except Exception as exc:
                    logger.debug(f"Batch progress callback failed: {exc}")

        producer_thread.join(timeout=5.0)
        return [r if r is not None else StatementImportResult(success=False, error="missing batch result") for r in results]

    def repair_statement_header(
        self,
        statement_id: int,
        pdf_path: str,
    ) -> bool:
        """Re-extrahiert ausschliesslich die Kopfdaten (Bank, Konto, Periode,
        Salden) fuer ein bereits importiertes Statement und aktualisiert die
        DB-Felder.

        Buchungen werden nicht angetastet. Gedacht fuer Statements, die mit
        dem alten Single-Window-Extraktor importiert wurden und daher
        ``closing_balance = 0`` oder ``period_end = NULL`` haben.

        Gibt True zurueck wenn mindestens ein Datenbankfeld aktualisiert wurde.
        """
        pdf_path = str(Path(pdf_path).resolve())
        if not Path(pdf_path).is_file():
            raise FileNotFoundError(f"PDF not found: {pdf_path}")

        try:
            from utils.docling_processor import DoclingProcessor
        except ImportError as exc:
            raise RuntimeError(f"Docling not available: {exc}") from exc

        processor = DoclingProcessor.get_instance()
        result = processor.convert_file(pdf_path)
        if not result.success or not result.text:
            raise RuntimeError(
                f"Docling conversion failed: {result.error or 'empty output'}"
            )

        header = self._extract_header(result.text)
        return self.db.update_statement_balances(
            statement_id,
            opening_balance=header.opening_balance,
            closing_balance=header.closing_balance,
            period_start=header.period_start,
            period_end=header.period_end,
        )

    # -- private helpers -------------------------------------------

    def _extract_full(self, markdown: str) -> ExtractedStatement:
        """Zwei-Phasen-Extraktion: Header einmal, Transaktionen pro Chunk."""
        header = self._extract_header(markdown)
        transactions = self._extract_transactions(markdown, header=header)

        # Root-cause guard: entfernt typische "Saldo als Buchung"-Artefakte,
        # die bei Tabellen mit Betrag+Saldo in derselben Zeile entstehen koennen.
        transactions = self._prune_balance_artifacts(
            transactions,
            opening_balance=header.opening_balance,
            closing_balance=header.closing_balance,
        )

        # Kreditkarten-PDFs enthalten Betraege haeufig ohne explizites +/-.
        # Dadurch kann die LLM-Semantik je Chunk kippen (zeitlich lokalisierte
        # Sign-Inversion). Wir harmonisieren die Vorzeichen auf
        # Statement-Ebene datengetrieben ueber Tages-Cluster und entfernen
        # naheliegende Opposite-Sign-Duplikate aus Chunk-Overlap.
        transactions = self._reconcile_credit_card_signs(transactions, header=header)
        transactions = self._reconcile_credit_card_entity_signs(transactions, header=header)
        transactions = self._drop_near_duplicate_sign_conflicts(transactions, header=header)
        transactions = self._enforce_credit_card_cashflow_convention(transactions, header=header)
        transactions = self._normalize_credit_card_obvious_flow_signs(transactions, header=header)

        # Sortiere chronologisch (stable), wenn Buchungsdatum vergleichbar ist.
        transactions.sort(key=lambda tx: (tx.booking_date or "", tx.value_date or ""))

        return ExtractedStatement(
            bank=header.bank,
            account=header.account,
            period_start=header.period_start,
            period_end=header.period_end,
            opening_balance=header.opening_balance,
            closing_balance=header.closing_balance,
            transactions=transactions,
        )

    def _extract_header(self, markdown: str) -> StatementHeader:
        """Extrahiert Kopfdaten aus Kopf- und Endbereich des Auszugs.

        Mehrseitige Statements enthalten den Endsaldo oft nur im letzten
        Seitenblock. Der Header-Call bekommt deshalb zwei kleine Fenster
        (Start + Ende), bleibt also n_ctx-sicher, aber verliert keine
        relevanten Endinformationen.
        """
        head_window = markdown[:_HEADER_WINDOW_CHARS]
        tail_window = markdown[-_HEADER_WINDOW_CHARS:] if len(markdown) > _HEADER_WINDOW_CHARS else markdown

        prompt = (
            "Hier sind zwei Ausschnitte desselben Bank-Kontoauszugs als Markdown:\n\n"
            "<kopf>\n"
            f"{head_window}\n"
            "</kopf>\n\n"
            "<ende>\n"
            f"{tail_window}\n"
            "</ende>\n\n"
            "Extrahiere ausschliesslich die Kopfdaten (Bank, Konto, Periode, "
            "Anfangs-/Endsaldo). Prioritaet: Anfangssaldo eher aus <kopf>, "
            "Endsaldo/Periodenende eher aus <ende>. Wenn ein Feld in beiden "
            "Ausschnitten nicht klar erkennbar ist, setze es auf null."
        )

        merged = self._wrapper.generate_structured(
            prompt=prompt,
            output_schema=StatementHeader,
            max_tokens=_HEADER_MAX_TOKENS,
            system_prompt=_HEADER_SYSTEM_PROMPT,
        )

        account_type = self._coerce_account_type_from_context(
            merged.account.account_type,
            merged.account.iban,
        )

        # Gegen Halluzinationsfall "0.00 statt unbekannt": wenn ein Saldo exakt
        # null ist, aber der andere eine grosse Groessenordnung hat, den Nullwert
        # als unbekannt behandeln.
        opening = merged.opening_balance
        closing = merged.closing_balance
        if opening == 0.0 and closing is not None and abs(closing) >= 100.0:
            opening = None
        if closing == 0.0 and opening is not None and abs(opening) >= 100.0:
            closing = None

        if (
            opening is merged.opening_balance
            and closing is merged.closing_balance
            and account_type == merged.account.account_type
        ):
            return merged
        return StatementHeader(
            bank=merged.bank,
            account=merged.account.model_copy(update={"account_type": account_type}),
            period_start=merged.period_start,
            period_end=merged.period_end,
            opening_balance=opening,
            closing_balance=closing,
        )

    @classmethod
    def _coerce_account_type_from_context(
        cls,
        account_type: Optional[str],
        account_identifier: Optional[str],
    ) -> Optional[str]:
        """Harden account_type against structural misclassification.

        ``credit_card`` is only accepted when the account identifier is not a
        valid IBAN. This prevents false credit-card classification on classic
        giro accounts and keeps downstream sign logic consistent.
        """
        normalized_type = (account_type or "").strip().lower()
        if normalized_type != "credit_card":
            return account_type

        normalized_id = "".join((account_identifier or "").split()).upper()
        if not cls._looks_like_valid_iban(normalized_id):
            return account_type
        logger.warning(
            "Coerced account_type from credit_card to checking due to valid IBAN identifier"
        )
        return "checking"

    @staticmethod
    def _looks_like_valid_iban(identifier: str) -> bool:
        if not _IBAN_LIKE_RE.match(identifier):
            return False
        # ISO 13616 checksum: move country+checksum to end, map A=10..Z=35,
        # then mod 97 must equal 1.
        rearranged = f"{identifier[4:]}{identifier[:4]}"
        digits = []
        for ch in rearranged:
            if ch.isdigit():
                digits.append(ch)
            else:
                digits.append(str(ord(ch) - 55))
        number = "".join(digits)
        remainder = 0
        for c in number:
            remainder = (remainder * 10 + int(c)) % 97
        return remainder == 1

    def _extract_transactions(
        self,
        markdown: str,
        *,
        header: StatementHeader,
    ) -> List[ExtractedTransaction]:
        """Extrahiert Buchungen chunkweise und merged + dedupliziert."""
        chunks = self._chunk_markdown(markdown)
        all_tx: List[ExtractedTransaction] = []
        seen: set[Tuple[str, str, float, str]] = set()

        for idx, (chunk, position_hint) in enumerate(chunks):
            try:
                txs = self._extract_transactions_from_chunk(
                    chunk=chunk,
                    position_hint=position_hint,
                    chunk_index=idx,
                    total_chunks=len(chunks),
                    header=header,
                    depth=0,
                )
            except Exception:
                logger.exception(
                    "Transaction extraction failed for chunk %d/%d", idx + 1, len(chunks)
                )
                raise

            for tx in txs:
                key = (
                    tx.booking_date or "",
                    tx.value_date or "",
                    float(tx.amount),
                    (tx.purpose or "")[:80].strip(),
                )
                # Secondary key uses counterparty so chunk-overlap duplicates
                # (same tx parsed twice with different purpose wording) are caught.
                key2 = (
                    tx.booking_date or "",
                    tx.value_date or "",
                    float(tx.amount),
                    (tx.counterparty or "")[:60].strip().lower(),
                )
                if key in seen:
                    continue
                if key2 in seen and (tx.counterparty or "").strip():
                    continue
                seen.add(key)
                seen.add(key2)
                all_tx.append(tx)

        return all_tx

    @staticmethod
    def _normalize_text_for_match(value: Optional[str]) -> str:
        if not value:
            return ""
        return re.sub(r"\s+", " ", value.strip().lower())

    @classmethod
    def _text_overlap_score(cls, a: Optional[str], b: Optional[str]) -> float:
        na = cls._normalize_text_for_match(a)
        nb = cls._normalize_text_for_match(b)
        if not na or not nb:
            return 0.0
        sa = set(re.findall(r"[a-z0-9]+", na))
        sb = set(re.findall(r"[a-z0-9]+", nb))
        if not sa or not sb:
            return 0.0
        inter = len(sa & sb)
        return inter / float(min(len(sa), len(sb)))

    @classmethod
    def _prune_balance_artifacts(
        cls,
        txs: List[ExtractedTransaction],
        *,
        opening_balance: Optional[float],
        closing_balance: Optional[float],
    ) -> List[ExtractedTransaction]:
        """Entfernt falsche Buchungen, die tatsaechlich laufende Salden sind.

        Mathematische Kriterien (kein Keyword-Filter):
        1) gleicher Tag + gleiche Counterparty + gleicher Typ + aehnlicher Purpose,
           aber zwei stark unterschiedlich grosse Betraege (Ratio >= 20).
        2) der grosse Betrag liegt in der Groessenordnung der Header-Salden
           (typisch fuer running-balance-Spalte), der kleine nicht.
        """
        if len(txs) < 2:
            return txs
        if opening_balance is None and closing_balance is None:
            return txs

        balance_refs = [abs(v) for v in (opening_balance, closing_balance) if v is not None]
        if not balance_refs:
            return txs
        balance_scale = max(balance_refs)
        if balance_scale < 1000.0:
            return txs

        groups: Dict[Tuple[str, str], List[ExtractedTransaction]] = {}
        for tx in txs:
            key = (
                tx.booking_date or "",
                cls._normalize_text_for_match(tx.counterparty),
            )
            groups.setdefault(key, []).append(tx)

        to_drop_ids: set[int] = set()
        for bucket in groups.values():
            if len(bucket) < 2:
                continue
            ordered = sorted(bucket, key=lambda t: abs(float(t.amount)))
            small = ordered[0]
            large = ordered[-1]

            small_abs = abs(float(small.amount))
            large_abs = abs(float(large.amount))
            if small_abs <= 0:
                continue
            if (large_abs / small_abs) < 20.0:
                continue
            if large_abs < 2000.0:
                continue

            # Grosser Betrag muss im Bereich des Statement-Saldos liegen.
            if not (0.5 * balance_scale <= large_abs <= 2.5 * balance_scale):
                continue

            overlap = cls._text_overlap_score(small.purpose, large.purpose)
            if overlap < 0.5:
                continue

            if (small.amount > 0) != (large.amount > 0):
                continue

            to_drop_ids.add(id(large))

        if not to_drop_ids:
            return txs

        cleaned = [tx for tx in txs if id(tx) not in to_drop_ids]
        logger.warning(
            "Pruned %d running-balance artifacts from statement extraction",
            len(txs) - len(cleaned),
        )
        return cleaned

    @staticmethod
    def _parse_iso_date(value: Optional[str]) -> Optional[date]:
        if not value:
            return None
        try:
            return datetime.strptime(value, "%Y-%m-%d").date()
        except ValueError:
            return None

    @classmethod
    def _reconcile_credit_card_signs(
        cls,
        txs: List[ExtractedTransaction],
        *,
        header: StatementHeader,
    ) -> List[ExtractedTransaction]:
        if not txs:
            return txs
        if (header.account.account_type or "").lower() != "credit_card":
            return txs

        pos_total = sum(1 for tx in txs if float(tx.amount) > 0)
        neg_total = sum(1 for tx in txs if float(tx.amount) < 0)
        if pos_total == 0 or neg_total == 0:
            return txs
        global_sign = -1 if neg_total >= pos_total else 1

        day_rows: Dict[date, List[int]] = {}
        for idx, tx in enumerate(txs):
            d = cls._parse_iso_date(tx.booking_date)
            if d is None:
                continue
            day_rows.setdefault(d, []).append(idx)
        if not day_rows:
            return txs

        total_flips = 0
        max_passes = 3
        for _ in range(max_passes):
            day_marks: List[Tuple[date, int, int]] = []
            for d in sorted(day_rows):
                idxs = day_rows[d]
                pos = sum(1 for i in idxs if float(txs[i].amount) > 0)
                neg = sum(1 for i in idxs if float(txs[i].amount) < 0)
                total = pos + neg
                if total < 3:
                    continue
                dominant = 1 if pos > neg else -1
                purity = max(pos, neg) / float(total)
                if purity < 0.75:
                    continue
                day_marks.append((d, dominant, total))

            if not day_marks:
                break

            runs: List[List[Tuple[date, int, int]]] = []
            current: List[Tuple[date, int, int]] = []
            for mark in day_marks:
                d, dominant, _ = mark
                if dominant == global_sign:
                    if current:
                        runs.append(current)
                        current = []
                    continue
                if not current:
                    current = [mark]
                    continue
                prev_d = current[-1][0]
                if d <= (prev_d + timedelta(days=2)):
                    current.append(mark)
                else:
                    runs.append(current)
                    current = [mark]
            if current:
                runs.append(current)

            if not runs:
                break

            flip_indexes: set[int] = set()
            for run in runs:
                days = [d for d, _, _ in run]
                run_start = min(days)
                run_end = max(days)
                touched = [
                    i
                    for i, tx in enumerate(txs)
                    if (d := cls._parse_iso_date(tx.booking_date)) is not None
                    and run_start <= d <= run_end
                ]
                if len(touched) < 4:
                    continue
                opposite = [i for i in touched if global_sign * float(txs[i].amount) < 0]
                if len(opposite) < max(3, int(0.6 * len(touched))):
                    continue
                for i in opposite:
                    flip_indexes.add(i)

            if not flip_indexes:
                break

            for i in sorted(flip_indexes):
                txs[i].amount = -float(txs[i].amount)
            total_flips += len(flip_indexes)

        if total_flips <= 0:
            return txs

        logger.warning(
            "Reconciled %d credit-card transaction signs via iterative day-cluster consistency",
            total_flips,
        )
        return txs

    @classmethod
    def _enforce_credit_card_cashflow_convention(
        cls,
        txs: List[ExtractedTransaction],
        *,
        header: StatementHeader,
    ) -> List[ExtractedTransaction]:
        """Erzwingt finale Cashflow-Konvention fuer Kreditkarten-Statements.

        Root cause: Manche PDF-Chunks liefern fuer ganze Statements eine
        global invertierte Vorzeichenorientierung (Umsaetze positiv,
        Rueckzahlungen negativ). Die bestehenden lokalen Konsistenzregeln
        koennen diese Vollinversion nicht immer erkennen, wenn die Mehrzahl
        der Zeilen bereits gleichgerichtet falsch ist.

        Deterministische Regel:
        - Falls ein Kreditkarten-Statement stark positiv dominiert
          (pos >= 3 * neg und pos >= 5), wird das gesamte Statement
          global invertiert. Dadurch bleiben relative Vorzeichenbeziehungen
          (z.B. Kauf vs Rueckerstattung) erhalten, nur die Orientierung wird
          auf Cashflow-Sicht normalisiert.
        """
        if not txs:
            return txs
        if (header.account.account_type or "").lower() != "credit_card":
            return txs

        pos_total = sum(1 for tx in txs if float(tx.amount) > 0)
        neg_total = sum(1 for tx in txs if float(tx.amount) < 0)
        if pos_total < 5:
            return txs
        if pos_total < (3 * max(1, neg_total)):
            return txs

        for tx in txs:
            tx.amount = -float(tx.amount)

        logger.warning(
            "Inverted %d credit-card transaction signs due to strong positive dominance (pos=%d, neg=%d)",
            len(txs),
            pos_total,
            neg_total,
        )
        return txs

    @classmethod
    def _normalize_credit_card_obvious_flow_signs(
        cls,
        txs: List[ExtractedTransaction],
        *,
        header: StatementHeader,
    ) -> List[ExtractedTransaction]:
        """Konservativer Finalizer fuer offensichtliche Kreditkarten-Flow-Richtung.

        Regeln (nur klare Faelle):
        - Zahlung/Ladung/Gutschrift/Rueckerstattung -> positiv
        - Einkauf/Gebuehr/Bargeldbezug/Kauf oder beliebiger Merchant-Eintrag
          ohne explizites Inflow-Label -> negativ

        Damit bleiben echte Inflows erhalten, waehrend positive Merchant-
        Artefakte (die aus OCR/LLM-Sign-Schwankungen stammen) bereinigt werden.
        """
        if not txs:
            return txs
        if (header.account.account_type or "").lower() != "credit_card":
            return txs

        inflow_labels = {
            "zahlung",
            "ladung",
            "gutschrift",
            "rueckerstattung",
            "r\u00fcckerstattung",
        }
        outflow_labels = {
            "einkauf",
            "gebuehr",
            "geb\u00fchr",
            "bargeldbezug",
            "kauf",
        }

        flip_to_negative = 0
        flip_to_positive = 0
        for tx in txs:
            amount = float(tx.amount)
            if amount == 0.0:
                continue
            bt = cls._normalize_text_for_match(tx.booking_type)
            cp = cls._normalize_text_for_match(tx.counterparty)

            if bt in inflow_labels:
                if amount < 0:
                    tx.amount = abs(amount)
                    flip_to_positive += 1
                continue

            obvious_outflow = (bt in outflow_labels) or (bool(cp) and bt not in inflow_labels)
            if obvious_outflow and amount > 0:
                tx.amount = -abs(amount)
                flip_to_negative += 1

        if flip_to_negative or flip_to_positive:
            logger.warning(
                "Normalized %d credit-card signs to negative and %d to positive via obvious-flow semantics",
                flip_to_negative,
                flip_to_positive,
            )
        return txs

    @classmethod
    def _drop_near_duplicate_sign_conflicts(
        cls,
        txs: List[ExtractedTransaction],
        *,
        header: StatementHeader,
    ) -> List[ExtractedTransaction]:
        if len(txs) < 2:
            return txs
        if (header.account.account_type or "").lower() != "credit_card":
            return txs

        pos_total = sum(1 for tx in txs if float(tx.amount) > 0)
        neg_total = sum(1 for tx in txs if float(tx.amount) < 0)
        if pos_total == 0 or neg_total == 0:
            return txs
        global_sign = -1 if neg_total >= pos_total else 1

        rows = [
            (
                idx,
                cls._parse_iso_date(tx.booking_date),
                abs(float(tx.amount)),
                float(tx.amount),
                cls._normalize_text_for_match(tx.counterparty),
                cls._normalize_text_for_match(tx.purpose),
            )
            for idx, tx in enumerate(txs)
        ]

        to_drop: set[int] = set()
        for i in range(len(rows)):
            idx_i, day_i, abs_i, amt_i, cp_i, pu_i = rows[i]
            if idx_i in to_drop or day_i is None:
                continue
            for j in range(i + 1, len(rows)):
                idx_j, day_j, abs_j, amt_j, cp_j, pu_j = rows[j]
                if idx_j in to_drop or day_j is None:
                    continue
                if abs_i <= 0 or abs_j <= 0:
                    continue
                if abs(abs_i - abs_j) > 1e-6:
                    continue
                if (day_i - day_j).days not in (-1, 0, 1):
                    continue
                if amt_i * amt_j >= 0:
                    continue
                cp_score = cls._text_overlap_score(cp_i, cp_j)
                pu_score = cls._text_overlap_score(pu_i, pu_j)
                if max(cp_score, pu_score) < 0.9:
                    continue

                keep_i = (global_sign * amt_i) >= 0
                keep_j = (global_sign * amt_j) >= 0
                if keep_i and not keep_j:
                    to_drop.add(idx_j)
                elif keep_j and not keep_i:
                    to_drop.add(idx_i)

        if not to_drop:
            return txs

        cleaned = [tx for idx, tx in enumerate(txs) if idx not in to_drop]
        logger.warning(
            "Dropped %d near-duplicate opposite-sign transactions from credit-card overlap",
            len(to_drop),
        )
        return cleaned

    @classmethod
    def _reconcile_credit_card_entity_signs(
        cls,
        txs: List[ExtractedTransaction],
        *,
        header: StatementHeader,
    ) -> List[ExtractedTransaction]:
        if not txs:
            return txs
        if (header.account.account_type or "").lower() != "credit_card":
            return txs

        pos_total = sum(1 for tx in txs if float(tx.amount) > 0)
        neg_total = sum(1 for tx in txs if float(tx.amount) < 0)
        if pos_total == 0 or neg_total == 0:
            return txs
        global_sign = -1 if neg_total >= pos_total else 1

        cp_groups: Dict[str, List[int]] = {}
        bt_groups: Dict[str, List[int]] = {}
        for idx, tx in enumerate(txs):
            cp = cls._normalize_text_for_match(tx.counterparty)
            bt = cls._normalize_text_for_match(tx.booking_type)
            if cp:
                cp_groups.setdefault(cp, []).append(idx)
            if bt:
                bt_groups.setdefault(bt, []).append(idx)

        def _group_tendencies(
            groups: Dict[str, List[int]],
            *,
            min_count: int,
            min_ratio: float,
        ) -> Dict[str, int]:
            tendencies: Dict[str, int] = {}
            for key, idxs in groups.items():
                if len(idxs) < min_count:
                    continue
                pos = sum(1 for i in idxs if float(txs[i].amount) > 0)
                neg = sum(1 for i in idxs if float(txs[i].amount) < 0)
                total = pos + neg
                if total <= 0:
                    continue
                dominant = 1 if pos > neg else -1
                ratio = max(pos, neg) / float(total)
                if ratio < min_ratio:
                    continue
                tendencies[key] = dominant
            return tendencies

        cp_tendency = _group_tendencies(cp_groups, min_count=3, min_ratio=0.60)
        bt_tendency = _group_tendencies(bt_groups, min_count=6, min_ratio=0.80)

        flip_count = 0
        for tx in txs:
            amount = float(tx.amount)
            if global_sign * amount >= 0:
                continue
            cp = cls._normalize_text_for_match(tx.counterparty)
            bt = cls._normalize_text_for_match(tx.booking_type)
            votes = []
            if cp in cp_tendency:
                votes.append(cp_tendency[cp])
            if bt in bt_tendency:
                votes.append(bt_tendency[bt])
            if not votes:
                continue
            if all(v == global_sign for v in votes):
                tx.amount = -amount
                flip_count += 1

        if flip_count > 0:
            logger.warning(
                "Reconciled %d credit-card transaction signs via entity-level consistency",
                flip_count,
            )
        return txs

    def _extract_transactions_from_chunk(
        self,
        *,
        chunk: str,
        position_hint: str,
        chunk_index: int,
        total_chunks: int,
        header: StatementHeader,
        depth: int,
    ) -> List[ExtractedTransaction]:
        """Extrahiert Buchungen aus einem Chunk mit adaptivem Divide-and-Conquer.

        Hintergrund: Bei inhaltsdichten Ausschnitten (viele Buchungen mit langen
        Verwendungszwecken) kann das JSON-Output-Budget trotz GBNF aufgebraucht
        werden. Statt blind neu zu versuchen, halbieren wir den Ausschnitt
        strukturell und extrahieren beide Teilbereiche separat.
        """
        prompt = (
            f"Hier ist Ausschnitt {chunk_index + 1}/{total_chunks} ({position_hint}) "
            "eines Bank-Kontoauszugs:\n\n"
            f"Kontotyp: {header.account.account_type or '-'}\n"
            f"Auszugsperiode: {header.period_start or '-'} bis {header.period_end or '-'}\n\n"
            "<ausschnitt>\n"
            f"{chunk}\n"
            "</ausschnitt>\n\n"
            "Liste alle Buchungen, die in DIESEM Ausschnitt stehen, als JSON. "
            "Lasse Buchungen weg, die nur teilweise (am Rand abgeschnitten) "
            "erkennbar sind -- der naechste Ausschnitt enthaelt sie vollstaendig."
        )

        try:
            batch = self._wrapper.generate_structured(
                prompt=prompt,
                output_schema=_RawTransactionBatch,
                max_tokens=_adaptive_tx_max_tokens(len(chunk), self._n_ctx),
                system_prompt=_TX_SYSTEM_PROMPT,
            )
            return [
                self._canonicalize_raw_transaction(raw_tx, header=header)
                for raw_tx in batch.transactions
            ]
        except Exception as exc:
            # Adaptive D&C nur fuer strukturelle LLM-Output-Fehler:
            # wenn ein Chunk zu dicht ist, wird er rekursiv geteilt.
            err = str(exc)
            looks_structured_failure = (
                isinstance(exc, _ChunkTransactionNormalizationError)
                or
                "Structured extraction failed" in err
                or "Structured output" in err
                or "JSON Parsing Error" in err
                or "Unterminated string" in err
                or "Failed to generate valid structured output" in err
            )
            if not looks_structured_failure:
                raise
            if depth >= _TX_ADAPTIVE_MAX_DEPTH or len(chunk) <= _TX_ADAPTIVE_MIN_CHARS:
                raise

            split_target = max(_TX_ADAPTIVE_MIN_CHARS, len(chunk) // 2)
            split_overlap = min(_TX_CHUNK_OVERLAP, max(120, split_target // 8))
            subchunks = self._chunk_text(
                chunk,
                max_chars=split_target,
                overlap=split_overlap,
            )
            if len(subchunks) <= 1:
                raise

            logger.warning(
                "Adaptive tx split for chunk %d/%d (%s): depth=%d, %d chars -> %d subchunks",
                chunk_index + 1,
                total_chunks,
                position_hint,
                depth,
                len(chunk),
                len(subchunks),
            )

            merged: List[ExtractedTransaction] = []
            for sub_idx, (subchunk, sub_hint) in enumerate(subchunks):
                nested_hint = f"{position_hint}/{sub_hint}-{sub_idx + 1}"
                merged.extend(
                    self._extract_transactions_from_chunk(
                        chunk=subchunk,
                        position_hint=nested_hint,
                        chunk_index=chunk_index,
                        total_chunks=total_chunks,
                        header=header,
                        depth=depth + 1,
                    )
                )
            return merged

    def _canonicalize_raw_transaction(
        self,
        raw_tx: _RawExtractedTransaction,
        *,
        header: StatementHeader,
    ) -> ExtractedTransaction:
        try:
            booking_date = self._canonicalize_statement_date(
                raw_tx.booking_date,
                period_start=header.period_start,
                period_end=header.period_end,
                field_name="booking_date",
            )
            value_date = self._canonicalize_statement_date(
                raw_tx.value_date,
                period_start=header.period_start,
                period_end=header.period_end,
                field_name="value_date",
                allow_empty=True,
            )
        except ValueError as exc:
            raise _ChunkTransactionNormalizationError(str(exc)) from exc

        counterparty = self._normalize_optional_text(raw_tx.counterparty, max_len=140)
        purpose = self._normalize_optional_text(raw_tx.purpose, max_len=280)
        booking_type_raw = self._normalize_optional_text(raw_tx.booking_type, max_len=280)

        # Root-cause preservation: manche Modelle schreiben den gesamten
        # Buchungstext in booking_type statt purpose. Wir verlieren den Text
        # nicht, sondern uebernehmen ihn als purpose, wenn dieses leer ist.
        if not purpose and booking_type_raw:
            purpose = booking_type_raw

        booking_type = booking_type_raw if booking_type_raw and len(booking_type_raw) <= 60 else None

        # Currency-Fallback: wenn die Buchungszeile keine eigene Waehrung
        # liefert (LLMs lassen das Feld bei Auszuegen mit einheitlicher
        # Statement-Waehrung haeufig leer), gilt strukturell die Konto-/
        # Statement-Waehrung. Nur ein explizit gesetzter, nicht-leerer
        # ISO-Code ueberschreibt diesen Default.
        raw_currency = (raw_tx.currency or "").strip()
        currency = raw_currency if raw_currency else header.account.currency

        return ExtractedTransaction(
            booking_date=booking_date,
            value_date=value_date,
            amount=raw_tx.amount,
            currency=currency,
            counterparty=counterparty,
            counterparty_iban=raw_tx.counterparty_iban,
            purpose=purpose,
            booking_type=booking_type,
        )

    @staticmethod
    def _normalize_optional_text(value: Optional[str], *, max_len: int) -> Optional[str]:
        if value is None:
            return None
        normalized = re.sub(r"\s+", " ", value).strip()
        if not normalized:
            return None
        return normalized[:max_len]

    @staticmethod
    def _canonicalize_statement_date(
        raw_value: Optional[str],
        *,
        period_start: Optional[str],
        period_end: Optional[str],
        field_name: str,
        allow_empty: bool = False,
    ) -> Optional[str]:
        if raw_value is None:
            if allow_empty:
                return None
            raise ValueError(f"{field_name} is required")
        value = raw_value.strip()
        if not value:
            if allow_empty:
                return None
            raise ValueError(f"{field_name} is required")

        for fmt in _FULL_DATE_FORMATS:
            try:
                return datetime.strptime(value, fmt).strftime("%Y-%m-%d")
            except ValueError:
                continue

        month_year = _MONTH_YEAR_RE.match(value)
        if month_year:
            second = int(month_year.group(2))
            if second > 12:
                raise ValueError(
                    f"{field_name} must be a full transaction date, not a month/year fragment: {raw_value!r}"
                )

        partial = _PARTIAL_DAY_MONTH_RE.match(value)
        if partial:
            day = int(partial.group(1))
            month = int(partial.group(2))
            inferred = FinanceExtractor._infer_date_from_statement_period(
                day=day,
                month=month,
                period_start=period_start,
                period_end=period_end,
            )
            if inferred is not None:
                return inferred.strftime("%Y-%m-%d")

        raise ValueError(
            f"{field_name} is not a supported full statement date and could not be inferred from period context: {raw_value!r}"
        )

    @staticmethod
    def _infer_date_from_statement_period(
        *,
        day: int,
        month: int,
        period_start: Optional[str],
        period_end: Optional[str],
    ) -> Optional[date]:
        if not (1 <= day <= 31 and 1 <= month <= 12):
            return None

        start = date.fromisoformat(period_start) if period_start else None
        end = date.fromisoformat(period_end) if period_end else None

        candidate_years = {
            bound.year for bound in (start, end) if bound is not None
        }
        if not candidate_years:
            return None

        candidates: List[date] = []
        for year in sorted(candidate_years):
            try:
                candidates.append(date(year, month, day))
            except ValueError:
                continue

        if not candidates:
            return None

        in_range = [
            candidate
            for candidate in candidates
            if (start is None or candidate >= start) and (end is None or candidate <= end)
        ]
        if len(in_range) == 1:
            return in_range[0]
        if len(in_range) > 1:
            if end is not None:
                return min(in_range, key=lambda candidate: abs((candidate - end).days))
            return in_range[0]
        if end is not None:
            return min(candidates, key=lambda candidate: abs((candidate - end).days))
        return candidates[0]

    @staticmethod
    def _chunk_markdown(markdown: str) -> List[Tuple[str, str]]:
        """Teilt Markdown in ueberlappende Windows auf.

        Splittet bevorzugt an Absatz-Grenzen (``\\n\\n``); faellt auf
        Zeilenende zurueck, wenn der naechste Absatz das Char-Limit
        sprengen wuerde. Liefert ``(chunk, position_hint)`` Paare,
        damit der Prompt dem Modell Kontext gibt ("Anfang", "Mitte",
        "Ende") -- das verhindert, dass am Auszug-Ende stehende
        Saldo-Zwischensummen als Buchung interpretiert werden.
        """
        return FinanceExtractor._chunk_text(
            markdown,
            max_chars=_TX_CHUNK_CHARS,
            overlap=_TX_CHUNK_OVERLAP,
        )

    @staticmethod
    def _chunk_text(text: str, *, max_chars: int, overlap: int) -> List[Tuple[str, str]]:
        """Generischer, boundary-sensitiver Chunker mit Position-Hints."""
        if not text:
            return []
        if len(text) <= max_chars:
            return [(text, "vollstaendig")]

        chunks: List[Tuple[str, str]] = []
        start = 0
        n = len(text)
        while start < n:
            end = min(start + max_chars, n)
            if end < n:
                # Suche letzten Absatz-Boundary innerhalb der letzten ~8% des Fensters,
                # damit Zeilen moeglichst nicht zerschnitten werden.
                tail = max(600, max_chars // 12)
                window_start = max(start, end - tail)
                window = text[window_start:end]
                cut = window.rfind("\n\n")
                if cut == -1:
                    cut = window.rfind("\n")
                if cut > 0:
                    end = window_start + cut

            chunk = text[start:end]
            if start == 0:
                hint = "Anfang"
            elif end >= n:
                hint = "Ende"
            else:
                hint = "Mitte"
            chunks.append((chunk, hint))

            if end >= n:
                break
            start = max(end - overlap, start + 1)

        return chunks


__all__ = ["FinanceExtractor", "StatementImportResult"]
