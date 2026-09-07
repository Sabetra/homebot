"""Pydantic-Schemata für LLM-strukturierte Kontoauszug-Extraktion.

Diese Modelle dienen ausschliesslich als ``output_schema`` für
``LLMStructuredWrapper.generate_structured`` -- der GBNF-Grammatik-Pfad
garantiert, dass das LLM token-weise nur schema-konformes JSON emittiert.
DB-Persistenz luft ber ``finance.db_schema`` mit eigenen Datenklassen;
diese Trennung ist beabsichtigt: das LLM-Format ist menschenfreundlich
(Floats, ISO-Datum-Strings), das DB-Format speichereffizient (Cents als
INTEGER, normalisierte IBANs).
"""

from __future__ import annotations

import os
import re
from datetime import datetime
from typing import Any, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


# Standard-Waehrung -- konfigurierbar via Umgebungsvariable
# ``FINANCE_DEFAULT_CURRENCY``. Default ist CHF, weil der Datenbestand
# (PostFinance-Auszuege, beide Konten CHF) und der Nutzungskontext
# Schweiz sind. Greift nur als Sicherheitsnetz, wenn weder Buchungs-
# noch Statement-Header eine Waehrung liefern -- der echte Pfad zieht
# die Waehrung aus dem Statement-Header.
DEFAULT_CURRENCY: str = (os.environ.get("FINANCE_DEFAULT_CURRENCY") or "CHF").upper()
if len(DEFAULT_CURRENCY) != 3 or not DEFAULT_CURRENCY.isalpha():
    # Strikte ISO-4217-Form -- Fehlkonfigurationen werden hart gemeldet,
    # nicht stillschweigend mit "EUR" maskiert.
    raise ValueError(
        f"FINANCE_DEFAULT_CURRENCY must be a 3-letter ISO-4217 code, got {DEFAULT_CURRENCY!r}"
    )


# ISO 13616 IBAN: 2 letters country, 2 digits checksum, 11..30 alphanum BBAN.
_IBAN_RE = re.compile(r"^[A-Z]{2}\d{2}[A-Z0-9]{11,30}$")
_BIC_RE = re.compile(r"^[A-Z]{6}[A-Z0-9]{2}([A-Z0-9]{3})?$")


def _norm_iban(value: str) -> str:
    return "".join(value.split()).upper()


# ---------------------------------------------------------------------------
# Datumsnormalisierung
# ---------------------------------------------------------------------------
# Bank-Auszuege liefern Buchungsdaten in unterschiedlichen Schreibweisen:
#   * ISO 8601:           2024-07-15
#   * Deutsche Notation:  15.07.2024 / 15.07.24
#   * Tagesabschluss:     15-07-2024 / 15-07-24
#   * Englisch (selten):  15/07/2024 / 15/07/24
# Das LLM gibt das woertlich wieder (richtig so -- niemals halluzinieren).
# Normalisierung gehoert in die Validator-Schicht, nicht in den Prompt:
# Dort ist sie deterministisch, testbar und unabhaengig vom Modell.
#
# Y2K-Pivot fuer 2-stellige Jahre folgt dem Python-strptime-Standard:
# 00..68 -> 2000..2068, 69..99 -> 1969..1999. Fuer Bank-Auszuege
# irrelevant (kein realer Auszug aus 1969 laeuft durch diesen Pfad);
# wir verwenden den Built-in-Pivot statt eigener Logik, um Konsistenz
# mit dateutil/pandas zu wahren.
_DATE_INPUT_FORMATS: List[str] = [
    "%Y-%m-%d",   # 2024-07-15
    "%d.%m.%Y",   # 15.07.2024
    "%d.%m.%y",   # 15.07.24
    "%d-%m-%Y",   # 15-07-2024
    "%d-%m-%y",   # 15-07-24
    "%d/%m/%Y",   # 15/07/2024
    "%d/%m/%y",   # 15/07/24
]


def _normalize_date(value: Optional[str], *, allow_empty: bool = True) -> Optional[str]:
    """Parsed ein Datum aus den im Bank-Kontext ueblichen Formaten und
    liefert ISO-8601 (YYYY-MM-DD) zurueck.

    Liefert ``None`` wenn ``value`` leer/None ist und ``allow_empty=True``.
    Bei ``allow_empty=False`` wird fuer leer/None ein ``ValueError`` geworfen.
    Wirft ``ValueError`` wenn keiner der unterstuetzten Formate matcht --
    so wird ein echter LLM-Halluzinationsfall (z.B. "Juli 2024") nicht
    stillschweigend in einen plausiblen Wert umgemuenzt.
    """
    if value is None:
        if allow_empty:
            return None
        raise ValueError("Date is required")
    v = value.strip()
    if not v:
        if allow_empty:
            return None
        raise ValueError("Date is required")
    for fmt in _DATE_INPUT_FORMATS:
        try:
            return datetime.strptime(v, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    raise ValueError(
        f"Date must be YYYY-MM-DD or DD.MM.YYYY/DD-MM-YY etc., got {value!r}"
    )


class BankInfo(BaseModel):
    """Bank-Header eines Kontoauszugs."""

    name: str = Field(
        description="Offizieller Bank-Name, z.B. 'Deutsche Bank AG'",
        min_length=1,
        max_length=140,
    )
    bic: Optional[str] = Field(
        default=None,
        description="BIC/SWIFT-Code wenn auf dem Auszug genannt, sonst null",
        max_length=11,
    )
    country_code: Optional[str] = Field(
        default=None, description="ISO-3166 Alpha-2, z.B. 'DE'", min_length=2, max_length=2
    )

    @field_validator("bic")
    @classmethod
    def _check_bic(cls, v: Optional[str]) -> Optional[str]:
        if v is None or not v.strip():
            return None
        v = v.strip().upper()
        if not _BIC_RE.match(v):
            raise ValueError(f"Invalid BIC format: {v!r}")
        return v

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {"name": "Deutsche Bank AG", "bic": "DEUTDEFF", "country_code": "DE"}
            ]
        }
    )


# Strukturelle Regex fuer Kontonummern/Kartennummern (nicht IBAN):
# rein alphanumerisch, 6-34 Zeichen -- entspricht den gueltigen BBAN-Zeichensaetzen
# ohne Laendercode-Prefix. Deckt alle gaengigen Formate ab (16-stellige
# Kartennummern, 10-18 stellige Kontonummern, CH/AT/DE BBAN etc.).
_ACCOUNT_NR_RE = re.compile(r"^[A-Z0-9]{6,34}$")

# ---------------------------------------------------------------------------
# Single source of truth for account-type vocabulary.
# The Literal below and this frozenset must always list the same values.
# ``db_schema.py`` imports VALID_ACCOUNT_TYPES to avoid a second definition.
# ---------------------------------------------------------------------------
VALID_ACCOUNT_TYPES: frozenset = frozenset(
    {"checking", "credit_card", "savings", "cash", "investment", "other"}
)

VALID_TRANSACTION_NATURES: frozenset = frozenset(
    {
        "ordinary",
        "internal_transfer",
        "fee",
        "interest",
        "refund",
        "cash_advance",
        "adjustment",
    }
)

# Mapping used once during DB initialisation to rename legacy German values
# that were stored by the previous extraction schema.
ACCOUNT_TYPE_MIGRATIONS: dict = {
    "girokonto": "checking",
    "sparkonto": "savings",
    "kreditkarte": "credit_card",
    "tagesgeld": "savings",
    "depot": "investment",
    "sonstiges": "other",
}


class AccountInfo(BaseModel):
    """Konto-Header eines Kontoauszugs."""

    iban: str = Field(
        description=(
            "IBAN des Kontos (ISO 13616) oder -- bei Kreditkarten, die keine "
            "eigene IBAN haben -- die Kartennummer bzw. Kontonummer. "
            "Ohne Leerzeichen, Grossbuchstaben/Ziffern. "
            "Beispiel IBAN: 'CH9300762011623852957'. "
            "Beispiel Kartennummer: '0000800472744336'."
        ),
        min_length=6,
        max_length=34,
    )
    account_holder: Optional[str] = Field(
        default=None, description="Name des Kontoinhabers", max_length=140
    )
    currency: str = Field(default=DEFAULT_CURRENCY, min_length=3, max_length=3, description="ISO-4217")
    account_type: Optional[
        Literal["checking", "credit_card", "savings", "cash", "investment", "other"]
    ] = Field(default=None, description="Kontotyp")

    @field_validator("iban")
    @classmethod
    def _check_iban(cls, v: str) -> str:
        v = _norm_iban(v)
        if _IBAN_RE.match(v):
            # Vollstaendige IBAN -- bereits normalisiert.
            return v
        # Kein Standard-IBAN (z.B. Kartennummer oder Kontonummer ohne
        # Laendercode). Strukturelle Mindestvalidierung: nur alphanumerische
        # Zeichen, 6-34 Stellen -- keine Keywords, kein Raten.
        if _ACCOUNT_NR_RE.match(v):
            return v
        raise ValueError(
            f"Invalid account identifier (neither IBAN nor valid account/card number): {v!r}"
        )

    @field_validator("currency", mode="before")
    @classmethod
    def _coerce_currency(cls, v: Any) -> Any:
        # Leeres LLM-Feld -> Default. Pydantic nimmt den Field-Default sonst
        # nur, wenn der Key fehlt; ein explizit gesetzter Empty-String ueber-
        # schreibt ihn und triggert min_length=3.
        if v is None:
            return DEFAULT_CURRENCY
        if isinstance(v, str) and not v.strip():
            return DEFAULT_CURRENCY
        return v

    @field_validator("currency")
    @classmethod
    def _upper_ccy(cls, v: str) -> str:
        return v.upper()

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "iban": "CH9300762011623852957",
                    "account_holder": "Max Mustermann",
                    "currency": "CHF",
                    "account_type": "checking",
                },
                {
                    "iban": "0000800472744336",
                    "account_holder": "Max Mustermann",
                    "currency": "CHF",
                    "account_type": "credit_card",
                },
            ]
        }
    )


class ExtractedTransaction(BaseModel):
    """Eine einzelne Buchung aus dem Auszug.

    ``amount`` ist signed: positiv = Eingang, negativ = Ausgang.
    """

    booking_date: str = Field(
        description=(
            "Buchungsdatum -- uebernimm die Schreibweise aus dem Auszug "
            "woertlich (z.B. '15.07.2024', '15-07-24' oder '2024-07-15'); "
            "die Normalisierung uebernimmt die Verarbeitung"
        )
    )
    value_date: Optional[str] = Field(
        default=None,
        description=(
            "Wertstellung -- gleiche Schreibweise wie booking_date, "
            "sonst null"
        ),
    )
    amount: float = Field(description="Betrag, signed (positiv=Eingang, negativ=Ausgang)")
    currency: str = Field(default=DEFAULT_CURRENCY, min_length=3, max_length=3)
    counterparty: Optional[str] = Field(
        default=None,
        description="Name des Empfngers/Auftraggebers",
        max_length=140,
    )
    counterparty_iban: Optional[str] = Field(
        default=None,
        description="IBAN der Gegenseite, sofern angegeben",
        max_length=34,
    )
    purpose: Optional[str] = Field(
        default=None,
        description="Verwendungszweck/Buchungstext (woertlich aus dem Auszug)",
        max_length=280,
    )
    booking_type: Optional[str] = Field(
        default=None,
        description=(
            "Buchungsart als kurzes Label, z.B. 'SEPA-berweisung', 'Lastschrift', "
            "'Gehalt', 'Kartenzahlung'. NUR der Typ-Begriff, KEINE Klammer-Kommentare "
            "oder Erluterungen."
        ),
    )

    @field_validator("booking_date")
    @classmethod
    def _check_booking_date(cls, v: str) -> str:
        normalized = _normalize_date(v, allow_empty=False)
        # allow_empty=False guarantees non-None; assert narrows type for mypy.
        assert normalized is not None
        return normalized

    @field_validator("value_date")
    @classmethod
    def _check_value_date(cls, v: Optional[str]) -> Optional[str]:
        return _normalize_date(v, allow_empty=True)

    @field_validator("counterparty_iban")
    @classmethod
    def _check_cp_iban(cls, v: Optional[str]) -> Optional[str]:
        if v is None or not v.strip():
            return None
        v = _norm_iban(v)
        if not _IBAN_RE.match(v):
            # Lenient: counterparty IBAN might be partially redacted on the
            # statement; do not crash the whole extraction. Drop it.
            return None
        return v

    @field_validator("currency", mode="before")
    @classmethod
    def _coerce_currency(cls, v: Any) -> Any:
        if v is None:
            return DEFAULT_CURRENCY
        if isinstance(v, str) and not v.strip():
            return DEFAULT_CURRENCY
        return v

    @field_validator("currency")
    @classmethod
    def _upper_ccy(cls, v: str) -> str:
        return v.upper()

    @field_validator("booking_type")
    @classmethod
    def _normalize_booking_type(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        normalized = re.sub(r"\s+", " ", v).strip()
        if not normalized:
            return None
        # booking_type ist fachlich ein optionales Kurzlabel. Wenn das LLM hier
        # erkennbar Rohtext oder ganze Buchungsbeschreibungen ablegt, ist die
        # korrekte Normalform nicht "crashen", sondern "kein valider Typ" -> null.
        if len(normalized) > 60:
            return None
        return normalized

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "booking_date": "2026-01-15",
                    "value_date": "2026-01-15",
                    "amount": -42.99,
                    "currency": "EUR",
                    "counterparty": "Telekom Deutschland GmbH",
                    "counterparty_iban": "DE12500700240012345600",
                    "purpose": "Mobilfunk-Rechnung 2026-01",
                    "booking_type": "SEPA-Lastschrift",
                }
            ]
        }
    )


class ExtractedStatement(BaseModel):
    """Vollstndiger Kontoauszug nach LLM-Extraktion."""

    bank: BankInfo
    account: AccountInfo
    period_start: Optional[str] = Field(
        default=None,
        description=(
            "Auszugs-Beginn (Schreibweise aus dem Auszug woertlich, "
            "z.B. '01.07.2024' oder '2024-07-01')"
        ),
    )
    period_end: Optional[str] = Field(
        default=None,
        description="Auszugs-Ende (Schreibweise wie period_start)",
    )
    opening_balance: Optional[float] = Field(
        default=None, description="Anfangssaldo (signed). null wenn nicht ausgewiesen."
    )
    closing_balance: Optional[float] = Field(
        default=None, description="Endsaldo (signed). null wenn nicht ausgewiesen."
    )
    transactions: List[ExtractedTransaction] = Field(
        default_factory=list, description="Alle Buchungen des Auszugs"
    )

    @field_validator("period_start", "period_end")
    @classmethod
    def _check_period(cls, v: Optional[str]) -> Optional[str]:
        return _normalize_date(v)

    @model_validator(mode="after")
    def _check_period_order(self) -> "ExtractedStatement":
        if self.period_start and self.period_end:
            if self.period_start > self.period_end:
                raise ValueError(
                    f"period_start ({self.period_start}) must be <= period_end ({self.period_end})"
                )
        return self

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "bank": {"name": "Deutsche Bank AG", "bic": "DEUTDEFF", "country_code": "DE"},
                    "account": {
                        "iban": "DE89370400440532013000",
                        "account_holder": "Max Mustermann",
                        "currency": "EUR",
                        "account_type": "checking",
                    },
                    "period_start": "2026-01-01",
                    "period_end": "2026-01-31",
                    "opening_balance": 1234.56,
                    "closing_balance": 987.65,
                    "transactions": [
                        {
                            "booking_date": "2026-01-15",
                            "value_date": "2026-01-15",
                            "amount": -42.99,
                            "currency": "EUR",
                            "counterparty": "Telekom Deutschland GmbH",
                            "counterparty_iban": None,
                            "purpose": "Mobilfunk-Rechnung",
                            "booking_type": "SEPA-Lastschrift",
                        }
                    ],
                }
            ]
        }
    )


class StatementHeader(BaseModel):
    """Kontoauszug-Kopfdaten ohne Buchungsliste.

    Dient der zweistufigen Extraktion: erster LLM-Call bestimmt nur Bank /
    Konto / Periode / Salden aus der ersten Auszugsseite. Damit ist der
    Prompt klein, der Grammar-Output fast immer < 200 Tokens, und das
    n_ctx-Budget bleibt für die transaktions-bezogenen Folge-Calls intakt.
    """

    bank: BankInfo
    account: AccountInfo
    period_start: Optional[str] = Field(
        default=None,
        description=(
            "Auszugs-Beginn (Schreibweise aus dem Auszug woertlich)"
        ),
    )
    period_end: Optional[str] = Field(
        default=None,
        description="Auszugs-Ende (Schreibweise wie period_start)",
    )
    opening_balance: Optional[float] = Field(default=None, description="Anfangssaldo (signed)")
    closing_balance: Optional[float] = Field(default=None, description="Endsaldo (signed)")

    @field_validator("period_start", "period_end")
    @classmethod
    def _check_period(cls, v: Optional[str]) -> Optional[str]:
        return _normalize_date(v)


class TransactionBatch(BaseModel):
    """Liste von Buchungen aus einem Markdown-Ausschnitt.

    Wird pro Chunk extrahiert; das LLM darf ausschliesslich Buchungen
    melden, die im Ausschnitt stehen (nicht im umgebenden Kontext).
    """

    transactions: List[ExtractedTransaction] = Field(default_factory=list)


__all__ = [
    "BankInfo",
    "AccountInfo",
    "ExtractedTransaction",
    "ExtractedStatement",
    "StatementHeader",
    "TransactionBatch",
    "CategorySuggestion",
    "CategorySuggestions",
    "VALID_ACCOUNT_TYPES",
    "ACCOUNT_TYPE_MIGRATIONS",
    "VALID_TRANSACTION_NATURES",
]


# ---------------------------------------------------------------------------
# LLM-Categorizer
# ---------------------------------------------------------------------------


class CategorySuggestion(BaseModel):
    """Vorschlag des LLM für eine einzelne Buchung.

    ``transaction_id`` referenziert den der Anfrage beigegebenen Index;
    ``category`` MUSS exakt einer der im Prompt aufgelisteten Kategorien
    entsprechen oder eine vom LLM neu vorgeschlagene -- in beiden Fllen
    bernimmt der Code die Auflsung gegen die DB.
    ``create_rule`` signalisiert, ob der Vorschlag stabil genug ist, um
    ihn als Counterparty-Regel persistieren zu drfen (LLM-Heuristik;
    finale Entscheidung trifft der User im UI).
    """

    transaction_id: int = Field(description="ID der zu kategorisierenden Buchung")
    category: str = Field(description="Kategoriename, exakt aus der Vorgabeliste oder neu")
    confidence: float = Field(
        default=0.7, ge=0.0, le=1.0, description="Selbsteingeschtzte Sicherheit"
    )
    reasoning: Optional[str] = Field(
        default=None, max_length=240, description="Sehr knappe Begrndung (max ~30 Worte)"
    )
    create_rule: bool = Field(
        default=False,
        description="True wenn Counterparty stabil ist und der Vorschlag als Auto-Regel taugt",
    )

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "transaction_id": 42,
                    "category": "Lebensmittel",
                    "confidence": 0.95,
                    "reasoning": "REWE ist ein Lebensmittel-Einzelhndler",
                    "create_rule": True,
                }
            ]
        }
    )


class CategorySuggestions(BaseModel):
    """Container für Batch-Vorschläge -- Output-Schema für GBNF."""

    suggestions: List[CategorySuggestion] = Field(default_factory=list)

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "suggestions": [
                        {
                            "transaction_id": 1,
                            "category": "Miete",
                            "confidence": 0.99,
                            "reasoning": "Wiederkehrende monatliche Mietzahlung",
                            "create_rule": True,
                        },
                        {
                            "transaction_id": 2,
                            "category": "Restaurants",
                            "confidence": 0.8,
                            "reasoning": "Kartenzahlung an Gastronomie-Betrieb",
                            "create_rule": False,
                        },
                    ]
                }
            ]
        }
    )
