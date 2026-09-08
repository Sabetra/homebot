"""Deterministische Cents-Konsistenzpruefung: Einzeittests der Engine.

Deckt ``finance.consistency`` ab - ohne DB, ohne LLM, ohne Float-Summen:
  * ``to_cents``: exakte Cents-Konvertierung (ROUND_HALF_UP, Float-Drift-sicher)
  * ``evaluate_statement_consistency``: alle vier Pruefungen
  * ``evaluate_extracted_statement``: Objekt- UND Mapping-Form
  * ``ConsistencyReport``: ``passed`` / ``errors`` / ``warnings`` / ``to_dict``

Semantik (Single Source of Truth: finance.consistency):
  * FEHLGESCHLAGENER Check  -> STATUS_FAILED              (needs_review=True)
  * uebersprungener Check   -> STATUS_PASSED_WITH_WARNINGS (needs_review=True)
  * ALLE vier Checks bestanden -> STATUS_PASSED (needs_review=False).
    Ein fehlender Vorperiode-Endsaldo laesst den prior-balance-Check
    ueberspringen -> bleibt also (konservativ) review-pflichtig.
"""

from __future__ import annotations

import json

import pytest

from finance.consistency import (
    CHECK_BALANCE_CHAIN,
    CHECK_CREDITS_TOTAL,
    CHECK_DEBITS_TOTAL,
    CHECK_PRIOR_BALANCE_LINK,
    STATUS_FAILED,
    STATUS_PASSED,
    STATUS_PASSED_WITH_WARNINGS,
    evaluate_extracted_statement,
    evaluate_statement_consistency,
    to_cents,
)


# ===========================================================================
# to_cents: exakte Cents-Konvertierung
# ===========================================================================


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (0.0, 0),
        (1.00, 100),
        (-1.00, -100),
        (1234.56, 123456),
        (-1234.56, -123456),
        (1.005, 101),      # ROUND_HALF_UP, keine Banker''s Rounding
        (2.675, 268),
        (-2.675, -268),
        (0.1 + 0.2, 30),   # Float-Drift -> exakt 30 Cents
        ("10.005", 1001),  # String-Eingang (z.B. aus LLM-JSON)
        ("999.99", 99999),
    ],
)
def test_to_cents_precision(value, expected) -> None:
    assert to_cents(value) == expected


def test_to_cents_none_raises() -> None:
    with pytest.raises(TypeError):
        to_cents(None)


def test_to_cents_bitwise_equivalent_to_db_schema() -> None:
    """Engine- und DB-Cents muessen bit-genau vergleichbar sein."""
    from finance.db_schema import _to_cents

    for value in (0.0, 1.005, 2.675, -2.675, 0.1 + 0.2, 1234.56, 999.99):
        assert to_cents(value) == _to_cents(value)


# ===========================================================================
# Referenz-Auszug + Helfer
# ===========================================================================


def _consistent_inputs() -> dict:
    """Voll konsistenter Auszug (alle vier Checks bestanden).

    Gutschriften=1000.00, Belastungen=400.00; 500.00 + 1000.00 - 400.00 = 1100.00.
    Vorperiode-Endsaldo=500.00 (stammt mit dem Anfangssaldo ueberein).
    """
    return dict(
        opening_balance=500.00,
        closing_balance=1100.00,
        total_credits=1000.00,
        total_debits=400.00,
        transactions=[{"amount": 1000.00}, {"amount": -400.00}],
        prior_closing_balance=500.00,
    )


def _check_by_name(report, name: str):
    for c in report.checks:
        if c.name == name:
            return c
    raise AssertionError(f"Check {name!r} not found in report: {[c.name for c in report.checks]}")

# ===========================================================================
# STATUS_PASSED: alle vier Checks laufen und bestehen
# ===========================================================================


def test_fully_consistent_statement_passes() -> None:
    report = evaluate_statement_consistency(**_consistent_inputs())
    assert report.status == STATUS_PASSED
    assert report.needs_review is False
    assert report.passed is True
    assert report.errors == []
    assert report.warnings == []


def test_fully_consistent_statement_all_checks_passed() -> None:
    report = evaluate_statement_consistency(**_consistent_inputs())
    for name in (
        CHECK_CREDITS_TOTAL,
        CHECK_DEBITS_TOTAL,
        CHECK_BALANCE_CHAIN,
        CHECK_PRIOR_BALANCE_LINK,
    ):
        assert _check_by_name(report, name).status == "passed"


def test_fully_consistent_statement_prior_cents_recorded() -> None:
    report = evaluate_statement_consistency(**_consistent_inputs())
    assert report.prior_closing_balance_cents == 50000


# ===========================================================================
# STATUS_FAILED: ein fehlgeschlagener Check
# ===========================================================================


def test_credits_total_mismatch_fails() -> None:
    inputs = _consistent_inputs()
    inputs["total_credits"] = 1.00  # Gutschriften weichen ab
    report = evaluate_statement_consistency(**inputs)
    assert report.status == STATUS_FAILED
    assert report.needs_review is True
    assert report.passed is False
    assert _check_by_name(report, CHECK_CREDITS_TOTAL).status == "failed"
    assert report.errors


def test_debits_total_mismatch_fails() -> None:
    inputs = _consistent_inputs()
    inputs["total_debits"] = 1.00
    report = evaluate_statement_consistency(**inputs)
    assert report.status == STATUS_FAILED
    assert report.needs_review is True
    assert _check_by_name(report, CHECK_DEBITS_TOTAL).status == "failed"


def test_balance_chain_mismatch_fails() -> None:
    """Anfang + Gut - Last != Endsaldo -> Saldo-Kette faellt durch."""
    inputs = _consistent_inputs()
    inputs["closing_balance"] = 1200.00  # 500 + 1000 - 400 = 1100 != 1200
    report = evaluate_statement_consistency(**inputs)
    assert report.status == STATUS_FAILED
    assert report.needs_review is True
    assert _check_by_name(report, CHECK_BALANCE_CHAIN).status == "failed"


def test_prior_balance_link_mismatch_fails() -> None:
    """Anfangssaldo != Endsaldo der Vorperiode -> Verknuepfung faellt durch."""
    inputs = _consistent_inputs()
    inputs["prior_closing_balance"] = 499.99
    report = evaluate_statement_consistency(**inputs)
    assert report.status == STATUS_FAILED
    assert report.needs_review is True
    assert _check_by_name(report, CHECK_PRIOR_BALANCE_LINK).status == "failed"


# ===========================================================================
# STATUS_PASSED_WITH_WARNINGS: uebersprungene Checks
# ===========================================================================


def test_missing_prior_balance_is_warning() -> None:
    """Kein Vorperiode-Saldo -> prior-balance-Check wird uebersprungen.

    Konservativ: bleibt review-pflichtig, obwohl nichts fehlgeschlagen ist.
    """
    inputs = _consistent_inputs()
    inputs["prior_closing_balance"] = None
    report = evaluate_statement_consistency(**inputs)
    assert report.status == STATUS_PASSED_WITH_WARNINGS
    assert report.needs_review is True
    assert _check_by_name(report, CHECK_PRIOR_BALANCE_LINK).status == "skipped"
    assert report.warnings


def test_missing_totals_is_warning() -> None:
    inputs = _consistent_inputs()
    inputs["total_credits"] = None
    inputs["total_debits"] = None
    report = evaluate_statement_consistency(**inputs)
    assert report.status == STATUS_PASSED_WITH_WARNINGS
    assert report.needs_review is True
    assert _check_by_name(report, CHECK_CREDITS_TOTAL).status == "skipped"
    assert _check_by_name(report, CHECK_DEBITS_TOTAL).status == "skipped"


def test_zero_amount_transactions_are_consistent() -> None:
    """Null-Betraege zaehlen weder Gutschriften noch Belastungen (keine Drift)."""
    report = evaluate_statement_consistency(
        opening_balance=100.00,
        closing_balance=100.00,
        total_credits=0.00,
        total_debits=0.00,
        transactions=[{"amount": 0.00}, {"amount": 0.00}],
        prior_closing_balance=100.00,
    )
    assert report.status == STATUS_PASSED
    assert report.needs_review is False

# ===========================================================================
# evaluate_extracted_statement: Objekt- UND Mapping-Form
# ===========================================================================


class _Tx:
    def __init__(self, amount):
        self.amount = amount


class _Extracted:
    """Entspricht den relevanten Attributen von ExtractedStatement."""

    def __init__(self):
        self.opening_balance = 500.0
        self.closing_balance = 1100.0
        self.total_credits = 1000.0
        self.total_debits = 400.0
        self.transactions = [_Tx(1000.0), _Tx(-400.0)]


def test_extracted_statement_object_form() -> None:
    report = evaluate_extracted_statement(_Extracted(), prior_closing_balance=500.0)
    assert report.status == STATUS_PASSED
    assert report.needs_review is False


def test_extracted_statement_object_form_without_prior() -> None:
    """Import-Pfad liefert keinen Vorperiode-Saldo -> Warning, review-pflichtig."""
    report = evaluate_extracted_statement(_Extracted())
    assert report.status == STATUS_PASSED_WITH_WARNINGS
    assert report.needs_review is True


def test_extracted_statement_dict_form_repair_path() -> None:
    """Repair-Pfad uebergeben die DB-Buchungen als Mapping mit 'amount'."""
    report = evaluate_extracted_statement(
        {
            "opening_balance": 500.0,
            "closing_balance": 1100.0,
            "total_credits": 1000.0,
            "total_debits": 400.0,
            "transactions": [{"amount": 1000.0}, {"amount": -400.0}],
        },
        prior_closing_balance=500.0,
    )
    assert report.status == STATUS_PASSED
    assert report.needs_review is False


def test_extracted_statement_dict_with_decimal_amounts() -> None:
    """Repair-Pfad nutzt Decimal(cents)/100 - muss exakt funktionieren."""
    from decimal import Decimal

    report = evaluate_extracted_statement(
        {
            "opening_balance": 500.0,
            "closing_balance": 1100.0,
            "total_credits": 1000.0,
            "total_debits": 400.0,
            "transactions": [
                {"amount": Decimal(100000) / 100},
                {"amount": Decimal(-40000) / 100},
            ],
        },
        prior_closing_balance=500.0,
    )
    assert report.status == STATUS_PASSED
    assert report.needs_review is False


def test_extracted_statement_missing_keys_treated_as_none() -> None:
    report = evaluate_extracted_statement({"opening_balance": 1.0})
    assert report.status == STATUS_PASSED_WITH_WARNINGS
    assert report.needs_review is True


# ===========================================================================
# ConsistencyReport: to_dict / JSON-Serialisierbarkeit / passed
# ===========================================================================


def test_to_dict_is_json_serializable() -> None:
    report = evaluate_statement_consistency(**_consistent_inputs())
    d = report.to_dict()
    assert d["status"] == STATUS_PASSED
    assert d["needs_review"] is False
    assert isinstance(d["checks"], list)
    json.dumps(d)


def test_to_dict_failed_includes_errors() -> None:
    report = evaluate_statement_consistency(**{**_consistent_inputs(), "total_credits": 1.0})
    d = report.to_dict()
    assert d["status"] == STATUS_FAILED
    assert d["needs_review"] is True
    assert d["errors"]
    json.dumps(d)


def test_passed_property_semantics() -> None:
    """passed == 'kein Check fehlgeschlagen' (Warnings koennen 'passed' sein)."""
    passed = evaluate_statement_consistency(**_consistent_inputs())
    warning = evaluate_statement_consistency(**{**_consistent_inputs(), "prior_closing_balance": None})
    failed = evaluate_statement_consistency(**{**_consistent_inputs(), "total_debits": 1.0})
    assert passed.passed is True
    assert warning.passed is True  # Warning ist kein Fehler
    assert failed.passed is False