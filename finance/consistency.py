"""Deterministische Cents-Präzise-Konsistenzprüfungen für Kontoauszüge.

Kernidee:
    Das LLM extrahiert Header (Salden, Gutschrifts-/Belastungssummen) und
    Buchungen aus einem Kontoauszug. Dabei können sich Fehler einschleichen
    (falsch zugeordnete Beträge, halluzinierte Summen, verwechselte Salden).
    Dieses Modul prüft den extrahierten Zustand NACH der Extraktion
    deterministisch und in Cents-Präzision -- ohne LLM, ohne Float-Arithmetik.

Prüfungen (alle optional, je nach verfügbarer Datenlage):
    1. ``credits_total``      Summe aller Gutschriften (Buchungen > 0)
                              == ausgewiesene ``total_credits``.
    2. ``debits_total``       Summe aller Belastungen (|Buchungen < 0|)
                              == ausgewiesene ``total_debits``.
    3. ``balance_chain``      ``opening + credits - debits == closing``.
    4. ``prior_balance_link`` Vorheriger Endsaldo == aktueller Anfangssaldo
                              (nur wenn ein vorheriger Auszug existiert).

Isolierung & Testbarkeit:
    * Keine Abhängigkeit von ``finance.db_schema`` / ``finance.models`` --
      nur ``decimal`` und ``dataclasses``. Damit sind die Prüfungen ohne
      Live-LLM, ohne DB und ohne Streamlit ausführbar.
    * Cents-Präzision: Alle Geldbeträge werden EXAKT EINMAL über
      ``to_cents`` (ROUND_HALF_UP auf ``Decimal(str(value))``) in Cents
      konvertiert; danach erfolgt ausschließlich ganzzahlige Arithmetik.
      Die Konvertierung ist identisch zu ``finance.db_schema._to_cents``.

Ergebnis-Semantik (deterministisch):
    * ``passed``                Alle ausgeführten Prüfungen bestanden,
                                und mindestens eine Prüfung wurde ausgeführt.
    * ``passed_with_warnings``  Keine Prüfung fehlgeschlagen, aber eine oder
                                mehrere Prüfungen waren nicht ausführbar
                                (fehlende Salden/Summen) -- "unvollständig".
    * ``failed``                Mindestens eine Prüfung ist fehlgeschlagen.

    ``needs_review`` ist True für jeden Zustand außer ``passed`` -- also
    sowohl für ``failed`` als auch für ``passed_with_warnings`` (incomplete).
    Damit werden weder Fehlimporte noch nicht prüfbare Importe stillschweigend
    durchgewunken.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, List, Mapping, Optional, Sequence, Tuple

# ---------------------------------------------------------------------------
# Status-Konstanten (Single Source of Truth für die Berichts-Semantik)
# ---------------------------------------------------------------------------

STATUS_PASSED = "passed"
STATUS_PASSED_WITH_WARNINGS = "passed_with_warnings"
STATUS_FAILED = "failed"

CHECK_CREDITS_TOTAL = "credits_total"
CHECK_DEBITS_TOTAL = "debits_total"
CHECK_BALANCE_CHAIN = "balance_chain"
CHECK_PRIOR_BALANCE_LINK = "prior_balance_link"

# Einzelne Prüfung
CHECK_STATUS_PASSED = "passed"
CHECK_STATUS_FAILED = "failed"
CHECK_STATUS_SKIPPED = "skipped"


# ---------------------------------------------------------------------------
# Cents-Konvertierung (identisch zu finance.db_schema._to_cents)
# ---------------------------------------------------------------------------


def to_cents(value: Any) -> int:
    """Geldbetrag (Float/Int/Decimal/str) in vorzeichenbehaftete Cents (int).

    Verwendet ``Decimal(str(value))`` + ``ROUND_HALF_UP`` -- exakt dieselbe
    Semantik wie ``finance.db_schema._to_cents``. Dadurch sind die hier
    berechneten Prüf-Cents bit-genau mit den in der DB gespeicherten Cents
    vergleichbar (keine Float-Summen, keine Doppelrundung).
    """
    if value is None:
        raise TypeError("to_cents requires a non-None amount")
    decimal_amount = Decimal(str(value))
    return int((decimal_amount * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _fmt_cents(cents: int) -> str:
    """Cents als menschenlesbarer Betrag, z.B. ``1234.56`` / ``-12.30``.

    Ausschließlich für Fehler-/Info-Meldungen gedacht -- nie für Vergleich.
    """
    sign = "-" if cents < 0 else ""
    abs_cents = abs(cents)
    major = abs_cents // 100
    minor = abs_cents % 100
    return f"{sign}{major}.{minor:02d}"


# ---------------------------------------------------------------------------
# Ergebnis-Strukturen
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ConsistencyCheckResult:
    """Ergebnis einer einzelnen Konsistenzprüfung."""

    name: str
    status: str  # passed | failed | skipped
    expected_cents: Optional[int] = None
    actual_cents: Optional[int] = None
    reason: str = ""


@dataclass(frozen=True)
class ConsistencyReport:
    """Gesamtergebnis der Konsistenzprüfung eines einzelnen Auszugs."""

    status: str  # passed | passed_with_warnings | failed
    needs_review: bool
    checks: Tuple[ConsistencyCheckResult, ...] = field(default_factory=tuple)
    prior_closing_balance_cents: Optional[int] = None

    @property
    def passed(self) -> bool:
        """True genau dann, wenn keine Prüfung fehlgeschlagen ist."""
        return self.status != STATUS_FAILED

    @property
    def errors(self) -> List[str]:
        """Menschenlesbare Gründe aller FEHLGESCHLAGENEN Prüfungen."""
        return [c.reason for c in self.checks if c.status == CHECK_STATUS_FAILED and c.reason]

    @property
    def warnings(self) -> List[str]:
        """Menschenlesbare Gründe aller übersprungenen (nicht prüfbaren) Prüfungen."""
        return [c.reason for c in self.checks if c.status == CHECK_STATUS_SKIPPED and c.reason]

    def to_dict(self) -> Mapping[str, Any]:
        """JSON-/UI-taugliche Darstellung (für ``st.json`` und DB-Storage)."""
        return {
            "status": self.status,
            "needs_review": self.needs_review,
            "prior_closing_balance_cents": self.prior_closing_balance_cents,
            "checks": [
                {
                    "name": c.name,
                    "status": c.status,
                    "expected_cents": c.expected_cents,
                    "actual_cents": c.actual_cents,
                    "reason": c.reason,
                }
                for c in self.checks
            ],
            "errors": self.errors,
            "warnings": self.warnings,
        }


# ---------------------------------------------------------------------------
# interne Helfer
# ---------------------------------------------------------------------------


def _tx_amount(tx: Any) -> float:
    """Liefert den signed Betrag einer Buchung aus Object- oder Dict-Form.

    Unterstützt sowohl Pydantic-Modelle / Dataclasses (``.amount``) als auch
    einfache Dicts (``{"amount": ...}``). Wirft ``ValueError`` wenn kein
    Betrag vorhanden ist -- so wird ein echter Defekt sichtbar, statt still
    als 0 gezählt (kein silent-fallback).
    """
    if isinstance(tx, Mapping):
        raw = tx.get("amount")
    else:
        raw = getattr(tx, "amount", None)
    if raw is None:
        raise ValueError("transaction without amount is not consistent-checkable")
    return float(raw)


def _split_credits_debits(transactions: Optional[Sequence[Any]]) -> Tuple[int, int]:
    """Summiert Buchungen cent-genau in (credits, debits) Cents auf.

    credits = Summe aller positiven Beträge (Eingänge).
    debits  = Summe der Beträge aller negativen Buchungen (Ausgänge, Magnitude).
    """
    credits = 0
    debits = 0
    for tx in transactions or []:
        cents = to_cents(_tx_amount(tx))
        if cents > 0:
            credits += cents
        elif cents < 0:
            debits += -cents
    return credits, debits


def _magnitude_cents(value: Any) -> int:
    """Betrag als Magnitude in Cents (Vorzeichen wird ignoriert)."""
    return abs(to_cents(value))


# ---------------------------------------------------------------------------
# Haupt-API
# ---------------------------------------------------------------------------


def evaluate_statement_consistency(
    *,
    opening_balance: Optional[float] = None,
    closing_balance: Optional[float] = None,
    total_credits: Optional[float] = None,
    total_debits: Optional[float] = None,
    transactions: Optional[Sequence[Any]] = None,
    prior_closing_balance: Optional[float] = None,
) -> ConsistencyReport:
    """Führt die deterministischen Konsistenzprüfungen für einen Auszug aus.

    Parameter (alle optional, ``None`` = im Auszug nicht ausgewiesen):
        opening_balance       Anfangssaldo (signed).
        closing_balance       Endsaldo (signed).
        total_credits         Ausgewiesene Summe der Gutschriften (Magnitude).
        total_debits          Ausgewiesene Summe der Belastungen (Magnitude).
        transactions          Buchungen (Object mit ``.amount`` oder Dict).
        prior_closing_balance Endsaldo des vorherigen Auszugs (signed),
                              falls vorhanden.

    Returns:
        ConsistencyReport -- deterministisch, Cents-genau, ohne Seiteneffekte.
    """
    credits, debits = _split_credits_debits(transactions)
    checks: List[ConsistencyCheckResult] = []

    # -- 1) Gutschriftssumme ------------------------------------------------
    if total_credits is not None:
        expected = _magnitude_cents(total_credits)
        if expected == credits:
            checks.append(
                ConsistencyCheckResult(
                    name=CHECK_CREDITS_TOTAL,
                    status=CHECK_STATUS_PASSED,
                    expected_cents=credits,
                    actual_cents=expected,
                    reason="Gutschriftssumme stimmt ({a}).".format(a=_fmt_cents(credits)),
                )
            )
        else:
            checks.append(
                ConsistencyCheckResult(
                    name=CHECK_CREDITS_TOTAL,
                    status=CHECK_STATUS_FAILED,
                    expected_cents=credits,
                    actual_cents=expected,
                    reason=(
                        "Gutschriftssumme weicht ab: Buchungen summieren auf "
                        f"{_fmt_cents(credits)}, ausgewiesen {_fmt_cents(expected)}."
                    ),
                )
            )
    else:
        checks.append(
            ConsistencyCheckResult(
                name=CHECK_CREDITS_TOTAL,
                status=CHECK_STATUS_SKIPPED,
                reason="Keine Gutschriftssumme ausgewiesen -- Prüfung nicht möglich.",
            )
        )

    # -- 2) Belastungssumme -------------------------------------------------
    if total_debits is not None:
        expected = _magnitude_cents(total_debits)
        if expected == debits:
            checks.append(
                ConsistencyCheckResult(
                    name=CHECK_DEBITS_TOTAL,
                    status=CHECK_STATUS_PASSED,
                    expected_cents=debits,
                    actual_cents=expected,
                    reason="Belastungssumme stimmt ({a}).".format(a=_fmt_cents(debits)),
                )
            )
        else:
            checks.append(
                ConsistencyCheckResult(
                    name=CHECK_DEBITS_TOTAL,
                    status=CHECK_STATUS_FAILED,
                    expected_cents=debits,
                    actual_cents=expected,
                    reason=(
                        "Belastungssumme weicht ab: Buchungen summieren auf "
                        f"{_fmt_cents(debits)}, ausgewiesen {_fmt_cents(expected)}."
                    ),
                )
            )
    else:
        checks.append(
            ConsistencyCheckResult(
                name=CHECK_DEBITS_TOTAL,
                status=CHECK_STATUS_SKIPPED,
                reason="Keine Belastungssumme ausgewiesen -- Prüfung nicht möglich.",
            )
        )

    # -- 3) Saldo-Kette: opening + credits - debits == closing --------------
    if opening_balance is not None and closing_balance is not None:
        computed_closing = to_cents(opening_balance) + credits - debits
        stated_closing = to_cents(closing_balance)
        if computed_closing == stated_closing:
            checks.append(
                ConsistencyCheckResult(
                    name=CHECK_BALANCE_CHAIN,
                    status=CHECK_STATUS_PASSED,
                    expected_cents=computed_closing,
                    actual_cents=stated_closing,
                    reason=(
                        "Saldo-Kette stimmt: {o} + {c} - {d} = {r}."
                    ).format(
                        o=_fmt_cents(to_cents(opening_balance)),
                        c=_fmt_cents(credits),
                        d=_fmt_cents(debits),
                        r=_fmt_cents(computed_closing),
                    ),
                )
            )
        else:
            checks.append(
                ConsistencyCheckResult(
                    name=CHECK_BALANCE_CHAIN,
                    status=CHECK_STATUS_FAILED,
                    expected_cents=computed_closing,
                    actual_cents=stated_closing,
                    reason=(
                        "Saldo-Kette weicht ab: {o} + {c} - {d} = {r}, "
                        "ausgewiesener Endsaldo ist {stated}."
                    ).format(
                        o=_fmt_cents(to_cents(opening_balance)),
                        c=_fmt_cents(credits),
                        d=_fmt_cents(debits),
                        r=_fmt_cents(computed_closing),
                        stated=_fmt_cents(stated_closing),
                    ),
                )
            )
    else:
        checks.append(
            ConsistencyCheckResult(
                name=CHECK_BALANCE_CHAIN,
                status=CHECK_STATUS_SKIPPED,
                reason="Anfangs- oder Endsaldo fehlt -- Saldo-Kette nicht prüfbar.",
            )
        )

    # -- 4) Verknüpfung zum vorherigen Auszug ------------------------------
    prior_cents: Optional[int] = None
    if prior_closing_balance is not None and opening_balance is not None:
        prior_cents = to_cents(prior_closing_balance)
        current_opening = to_cents(opening_balance)
        if prior_cents == current_opening:
            checks.append(
                ConsistencyCheckResult(
                    name=CHECK_PRIOR_BALANCE_LINK,
                    status=CHECK_STATUS_PASSED,
                    expected_cents=current_opening,
                    actual_cents=prior_cents,
                    reason=(
                        "Anfangssaldo stimmt mit Endsaldo des Vorzeitraums "
                        "überein ({a}).".format(a=_fmt_cents(current_opening))
                    ),
                )
            )
        else:
            checks.append(
                ConsistencyCheckResult(
                    name=CHECK_PRIOR_BALANCE_LINK,
                    status=CHECK_STATUS_FAILED,
                    expected_cents=current_opening,
                    actual_cents=prior_cents,
                    reason=(
                        "Anfangssaldo ({o}) stimmt nicht mit Endsaldo des "
                        "Vorzeitraums ({p}) überein."
                    ).format(o=_fmt_cents(current_opening), p=_fmt_cents(prior_cents)),
                )
            )
    else:
        checks.append(
            ConsistencyCheckResult(
                name=CHECK_PRIOR_BALANCE_LINK,
                status=CHECK_STATUS_SKIPPED,
                reason=(
                    "Kein vorheriger Endsaldo / kein Anfangssaldo verfügbar -- "
                    "Verknüpfung zum Vorzeitraum nicht prüfbar."
                ),
            )
        )

    # -- Gesamtauswertung ---------------------------------------------------
    any_failed = any(c.status == CHECK_STATUS_FAILED for c in checks)
    any_skipped = any(c.status == CHECK_STATUS_SKIPPED for c in checks)
    any_evaluated = any(c.status != CHECK_STATUS_SKIPPED for c in checks)

    if any_failed:
        status = STATUS_FAILED
    elif any_skipped or not any_evaluated:
        status = STATUS_PASSED_WITH_WARNINGS
    else:
        status = STATUS_PASSED

    return ConsistencyReport(
        status=status,
        needs_review=status != STATUS_PASSED,
        checks=tuple(checks),
        prior_closing_balance_cents=prior_cents,
    )


def evaluate_extracted_statement(
    statement: Any,
    *,
    prior_closing_balance: Optional[float] = None,
) -> ConsistencyReport:
    """Duck-typing-Adapter für extrahierte Statement-Objekte.

    Akzeptiert ``finance.models.ExtractedStatement`` / ``StatementHeader``
    (Attribute) oder ein einfaches Mapping mit denselben Schlüsseln. Damit
    bleibt dieses Modul isoliert und ohne Import der Pydantic-Modelle
    testbar.
    """
    if isinstance(statement, Mapping):
        get = statement.get
    else:

        def get(key: str, default: Any = None) -> Any:
            return getattr(statement, key, default)

    transactions = get("transactions", None)
    return evaluate_statement_consistency(
        opening_balance=get("opening_balance", None),
        closing_balance=get("closing_balance", None),
        total_credits=get("total_credits", None),
        total_debits=get("total_debits", None),
        transactions=transactions,
        prior_closing_balance=prior_closing_balance,
    )


__all__ = [
    "ConsistencyCheckResult",
    "ConsistencyReport",
    "evaluate_statement_consistency",
    "evaluate_extracted_statement",
    "to_cents",
    "STATUS_PASSED",
    "STATUS_PASSED_WITH_WARNINGS",
    "STATUS_FAILED",
]