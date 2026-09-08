"""Deterministische Cents-Konsistenzpruefung: Einzeittests der Engine.

Deckt ``finance.consistency`` ab — ohne DB, ohne LLM, ohne Float-Summen:
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
        (1.005, 101),      # ROUND_HALF_UP, keine Banker's Rounding
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
    STATUS_PASSED,
    STATUS_PASSED_WITH_WARNINGS,
    evaluate_extracted_statement,
    evaluate_statement_consistency,
    to_cents,
)