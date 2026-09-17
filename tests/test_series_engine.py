"""AP2 Forecast-UX: deterministische Serien-Engine (Stage 1, pure).

Validiert ``finance/series_engine.py`` (rein stdlib, kein DB/UI/Modell):

* Expansion: monthly, n_months, weekly, n_weeks, yearly — inkl.
  Anchor-Tag-Clamping (29..31) auf echte Kalendermonate und Schaltjahre
* Ausnahmen: skip/move/amount pro Serien-Key + ORIGINAL-Termin;
  erneute Expansion erzeugt weder Dubletten noch Verlust (T11)
* Kandidatenerkennung: 2+ Beobachtungen => pruefbarer Vorschlag (nie
  Auto-Bestaetigung), Einnahme/Ausgabe getrennt (F04), Konto-/
  Waehrungs-Scope isoliert (F03), stabiler Fingerprint (T06)
* Ist-Abgleich: konservativ 1:1 (Mehrdeutigkeit nur melden, nie
  auto-verknuepfen, T12)
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from finance.series_engine import (  # noqa: E402
    MatchResult,
    Occurrence,
    SeriesException,
    SeriesSpec,
    candidate_fingerprint,
    clamp_day,
    detect_candidates,
    expand_series,
    match_occurrence,
    match_occurrences,
    next_occurrences,
)

IBAN_A = "DE89 3704 0044 0532 0130 00"
IBAN_B = "DE42 5001 0517 5487 3600 33"


def spec(**overrides: Any) -> SeriesSpec:
    base: Dict[str, Any] = dict(
        key="k",
        direction="expense",
        cadence="monthly",
        period_n=1,
        anchor_day=15,
        anchor_date="2026-01-15",
        amount_cents=2990,
        counterparty="Netflix",
        currency="EUR",
    )
    base.update(overrides)
    return SeriesSpec(**base)


def dates_of(occs: Any) -> List[str]:
    return [o.date for o in occs]


# ============================================================================
# clamp_day
# ============================================================================


class TestClampDay:
    def test_short_month_clamps(self) -> None:
        assert clamp_day(2026, 4, 31) == 30
        assert clamp_day(2026, 2, 31) == 28
        assert clamp_day(2026, 6, 29) == 30
        assert clamp_day(2026, 1, 15) == 15

    def test_leap_year_february(self) -> None:
        assert clamp_day(2024, 2, 29) == 29
        assert clamp_day(2024, 2, 31) == 29
        assert clamp_day(2026, 2, 29) == 28

    def test_invalid_input(self) -> None:
        with pytest.raises(ValueError):
            clamp_day(2026, 0, 15)
        with pytest.raises(ValueError):
            clamp_day(2026, 13, 15)
        with pytest.raises(ValueError):
            clamp_day(2026, 1, 0)
        with pytest.raises(ValueError):
            clamp_day(2026, 1, 32)


# ============================================================================
# SeriesSpec-Validierung
# ============================================================================


class TestSeriesSpecValidation:
    @pytest.mark.parametrize("cadence", ["monthly", "weekly", "yearly"])
    def test_period_n_must_be_one_for_plain_cadences(self, cadence: str) -> None:
        with pytest.raises(ValueError):
            spec(cadence=cadence, period_n=2)

    def test_n_months_range(self) -> None:
        spec(cadence="n_months", period_n=2)
        spec(cadence="n_months", period_n=11)
        with pytest.raises(ValueError):
            spec(cadence="n_months", period_n=1)
        with pytest.raises(ValueError):
            spec(cadence="n_months", period_n=12)

    def test_n_weeks_range(self) -> None:
        spec(cadence="n_weeks", period_n=2)
        spec(cadence="n_weeks", period_n=52)
        with pytest.raises(ValueError):
            spec(cadence="n_weeks", period_n=1)
        with pytest.raises(ValueError):
            spec(cadence="n_weeks", period_n=53)

    @pytest.mark.parametrize(
        "field, value",
        [
            ("direction", "transfer"),
            ("cadence", "fortnightly"),
            ("anchor_day", 0),
            ("anchor_day", 32),
            ("amount_cents", 0),
            ("amount_cents", -1),
            ("amount_cents", True),
            ("anchor_date", "15.01.2026"),
            ("period_n", "2"),
        ],
    )
    def test_invalid_fields(self, field: str, value: Any) -> None:
        with pytest.raises(ValueError):
            spec(**{field: value})

    def test_effective_window_order(self) -> None:
        spec(effective_from="2026-01-01", effective_to="2026-12-31")
        with pytest.raises(ValueError):
            spec(effective_from="2026-06-01", effective_to="2026-01-01")



# ============================================================================
# Expansion (Prompt S5.1: echte Kalendermonate, Zweimonatlich != 2x/monatlich)
# ============================================================================


class TestExpansion:
    def test_monthly_clamps_short_months(self) -> None:
        s = spec(anchor_day=31, anchor_date="2026-01-31")
        occs = expand_series(s, window_start="2026-01-01", window_end="2026-07-31")
        assert dates_of(occs) == [
            "2026-01-31",
            "2026-02-28",
            "2026-03-31",
            "2026-04-30",
            "2026-05-31",
            "2026-06-30",
            "2026-07-31",
        ]

    def test_monthly_leap_february(self) -> None:
        s = spec(anchor_day=31, anchor_date="2024-01-31")
        occs = expand_series(s, window_start="2024-02-01", window_end="2024-02-29")
        assert dates_of(occs) == ["2024-02-29"]

    def test_n_months_two_is_bimonthly_not_twice_monthly(self) -> None:
        s = spec(cadence="n_months", period_n=2, anchor_day=1, anchor_date="2026-01-01")
        occs = expand_series(s, window_start="2026-01-01", window_end="2026-09-30")
        assert dates_of(occs) == [
            "2026-01-01",
            "2026-03-01",
            "2026-05-01",
            "2026-07-01",
            "2026-09-01",
        ]

    def test_weekly_exact_seven_day_steps(self) -> None:
        s = spec(cadence="weekly", anchor_day=5, anchor_date="2026-01-05")
        occs = expand_series(s, window_start="2026-01-01", window_end="2026-02-08")
        # Exakte 7-Tage-Schritte vom Erstanker (kein Monats-Clamping).
        assert dates_of(occs) == [
            "2026-01-05",
            "2026-01-12",
            "2026-01-19",
            "2026-01-26",
            "2026-02-02",
        ]
        # Fenster-Ausschnitt: 2026-02-02 liegt draussen.
        occs2 = expand_series(s, window_start="2026-01-20", window_end="2026-02-01")
        assert dates_of(occs2) == ["2026-01-26"]

    def test_n_weeks_fortnightly(self) -> None:
        s = spec(cadence="n_weeks", period_n=2, anchor_day=10, anchor_date="2026-01-10")
        occs = expand_series(s, window_start="2026-01-01", window_end="2026-02-15")
        assert dates_of(occs) == ["2026-01-10", "2026-01-24", "2026-02-07"]

    def test_yearly_once_per_year(self) -> None:
        s = spec(cadence="yearly", anchor_day=2, anchor_date="2024-03-02")
        occs = expand_series(s, window_start="2024-01-01", window_end="2027-12-31")
        assert dates_of(occs) == [
            "2024-03-02",
            "2025-03-02",
            "2026-03-02",
            "2027-03-02",
        ]
        # Anker 31 bleibt stabil (Januar hat immer 31 Tage).
        s31 = spec(cadence="yearly", anchor_day=31, anchor_date="2024-01-31")
        occs31 = expand_series(s31, window_start="2024-01-01", window_end="2026-12-31")
        assert dates_of(occs31) == [
            "2024-01-31",
            "2025-01-31",
            "2026-01-31",
        ]

    def test_window_trims_result(self) -> None:
        s = spec()
        occs = expand_series(s, window_start="2026-03-01", window_end="2026-05-31")
        assert dates_of(occs) == ["2026-03-15", "2026-04-15", "2026-05-15"]

    def test_effective_from_and_to_limit_series(self) -> None:
        s = spec(effective_from="2026-02-01", effective_to="2026-04-30")
        occs = expand_series(s, window_start="2026-01-01", window_end="2026-12-31")
        assert dates_of(occs) == ["2026-02-15", "2026-03-15", "2026-04-15"]

    def test_effective_to_in_past_returns_empty(self) -> None:
        s = spec(effective_to="2025-12-31")
        assert expand_series(s, window_start="2026-01-01", window_end="2026-12-31") == []

    def test_without_window_returns_anchor_only(self) -> None:
        s = spec()
        occs = expand_series(s)
        assert dates_of(occs) == ["2026-01-15"]

    def test_no_duplicates_and_sorted(self) -> None:
        s = spec(anchor_day=29, anchor_date="2024-01-29")
        occs = expand_series(s, window_start="2024-01-01", window_end="2026-12-31")
        ds = dates_of(occs)
        assert ds == sorted(set(ds))
        # Februar 2024 (29) vs. Februar 2025/2026 (28) — jeweils genau ein Slot
        assert ds.count("2024-02-29") == 1
        assert ds.count("2025-02-28") == 1
        assert ds.count("2026-02-28") == 1



# ============================================================================
# Ausnahmen (Prompt S5.1: verschoben / Betrag ersetzt / ausgelassen; T11)
# ============================================================================


class TestExceptions:
    def test_skip_removes_slot_without_surrogate(self) -> None:
        s = spec()
        exc = {
            "2026-01-15": SeriesException(
                key="k", original_due_date="2026-01-15", exception_type="skip"
            )
        }
        occs = expand_series(s, exceptions=exc, window_start="2026-01-01", window_end="2026-03-31")
        assert dates_of(occs) == ["2026-02-15", "2026-03-15"]
        assert all(o.exception is None for o in occs)

    def test_move_replaces_date_but_keeps_identity(self) -> None:
        s = spec()
        exc = {
            "2026-01-15": SeriesException(
                key="k",
                original_due_date="2026-01-15",
                exception_type="move",
                new_due_date="2026-01-20",
            )
        }
        occs = expand_series(s, exceptions=exc, window_start="2026-01-01", window_end="2026-01-31")
        assert len(occs) == 1
        assert occs[0].date == "2026-01-20"
        assert occs[0].original_date == "2026-01-15"  # Identitaet erhalten
        assert occs[0].exception == "move"

    def test_amount_replaces_value_only(self) -> None:
        s = spec()
        exc = {
            "2026-01-15": SeriesException(
                key="k",
                original_due_date="2026-01-15",
                exception_type="amount",
                amount_cents=4990,
            )
        }
        occs = expand_series(s, exceptions=exc, window_start="2026-01-01", window_end="2026-01-31")
        assert len(occs) == 1
        assert occs[0].amount_cents == 4990
        assert occs[0].date == "2026-01-15"
        assert occs[0].exception == "amount"

    def test_reexpansion_is_stable_no_dups_no_loss(self) -> None:
        """T11: erneute Expansion erzeugt weder Dubletten noch Verlust."""
        s = spec()
        exc = {
            "2026-01-15": SeriesException(
                key="k",
                original_due_date="2026-01-15",
                exception_type="move",
                new_due_date="2026-01-20",
            ),
            "2026-02-15": SeriesException(
                key="k", original_due_date="2026-02-15", exception_type="skip"
            ),
            "2026-03-15": SeriesException(
                key="k",
                original_due_date="2026-03-15",
                exception_type="amount",
                amount_cents=1,
            ),
        }
        first = expand_series(s, exceptions=exc, window_start="2026-01-01", window_end="2026-06-30")
        second = expand_series(s, exceptions=exc, window_start="2026-01-01", window_end="2026-06-30")
        assert first == second  # deterministisch
        ds = dates_of(first)
        assert ds == sorted(set(ds))  # keine Dubletten
        assert "2026-01-20" in ds  # Move erhalten
        assert "2026-01-15" not in ds
        assert "2026-02-15" not in ds  # Skip erhalten
        assert "2026-03-15" in ds  # kein Verlust durch Skip/Move
        moved = [o for o in first if o.date == "2026-01-20"][0]
        assert moved.original_date == "2026-01-15"
        assert [o.amount_cents for o in first if o.original_date == "2026-03-15"] == [1]

    def test_exceptions_outside_window_are_ignored(self) -> None:
        s = spec()
        exc = {
            "2030-01-15": SeriesException(
                key="k", original_due_date="2030-01-15", exception_type="skip"
            )
        }
        occs = expand_series(s, exceptions=exc, window_start="2026-01-01", window_end="2026-03-31")
        assert dates_of(occs) == ["2026-01-15", "2026-02-15", "2026-03-15"]

    def test_exception_validations(self) -> None:
        with pytest.raises(ValueError):
            SeriesException(key="k", original_due_date="2026-01-15", exception_type="freeze")
        with pytest.raises(ValueError):
            SeriesException(key="k", original_due_date="2026-01-15", exception_type="move")
        with pytest.raises(ValueError):
            SeriesException(key="k", original_due_date="2026-01-15", exception_type="amount")
        with pytest.raises(ValueError):
            SeriesException(
                key="k",
                original_due_date="2026-01-15",
                exception_type="skip",
                new_due_date="2026-01-20",
            )
        with pytest.raises(ValueError):
            SeriesException(key="k", original_due_date="15.01.2026", exception_type="skip")
        with pytest.raises(ValueError):
            SeriesException(
                key="k",
                original_due_date="2026-01-15",
                exception_type="move",
                new_due_date="not-a-date",
            )



# ============================================================================
# next_occurrences (naechste Vorkommen ab Datum, mit Ausnahmen)
# ============================================================================


class TestNextOccurrences:
    def test_next_three_from_mid_month(self) -> None:
        s = spec()
        occs = next_occurrences(s, count=3, on_or_after="2026-07-01")
        assert dates_of(occs) == ["2026-07-15", "2026-08-15", "2026-09-15"]

    def test_on_anchor_day_includes_it(self) -> None:
        s = spec()
        occs = next_occurrences(s, count=2, on_or_after="2026-01-15")
        assert dates_of(occs) == ["2026-01-15", "2026-02-15"]

    def test_exceptions_respected(self) -> None:
        s = spec()
        exc = {
            "2026-01-15": SeriesException(
                key="k",
                original_due_date="2026-01-15",
                exception_type="move",
                new_due_date="2026-01-20",
            ),
            "2026-02-15": SeriesException(
                key="k", original_due_date="2026-02-15", exception_type="skip"
            ),
        }
        occs = next_occurrences(s, count=3, on_or_after="2026-01-01", exceptions=exc)
        assert dates_of(occs) == ["2026-01-20", "2026-03-15", "2026-04-15"]

    def test_count_zero_and_past_effective_to(self) -> None:
        s = spec()
        assert next_occurrences(s, count=0, on_or_after="2026-01-01") == []
        ended = spec(effective_to="2025-12-31")
        assert next_occurrences(ended, count=3, on_or_after="2026-01-01") == []

    def test_yearly_fills_across_year_boundary(self) -> None:
        s = spec(cadence="yearly", anchor_day=5, anchor_date="2026-03-05")
        occs = next_occurrences(s, count=2, on_or_after="2026-04-01")
        assert dates_of(occs) == ["2027-03-05", "2028-03-05"]


# ============================================================================
# Kandidatenerkennung (Prompt S5.2: 2+ Belege => pruefbarer Vorschlag)
# ============================================================================


def fact(
    txn: str,
    when: str,
    amount: int,
    iban: str = IBAN_A,
    counterparty: str = "Netflix",
    currency: str = "EUR",
) -> Dict[str, Any]:
    return {
        "transaction_id": txn,
        "date": when,
        "amount_cents": amount,
        "iban": iban,
        "counterparty": counterparty,
        "currency": currency,
    }


class TestCandidateFingerprint:
    def test_stable_under_normalization(self) -> None:
        a = candidate_fingerprint(IBAN_A, "EUR", "expense", "Netflix", "monthly", 1, 15)
        b = candidate_fingerprint(
            "DE89370400440532013000", "EUR", "expense", " Netflix ", "monthly", 1, 15
        )
        assert a == b

    def test_sensitive_to_every_dimension(self) -> None:
        base = candidate_fingerprint(IBAN_A, "EUR", "expense", "Netflix", "monthly", 1, 15)
        assert base != candidate_fingerprint(IBAN_B, "EUR", "expense", "Netflix", "monthly", 1, 15)
        assert base != candidate_fingerprint(IBAN_A, "USD", "expense", "Netflix", "monthly", 1, 15)
        assert base != candidate_fingerprint(IBAN_A, "EUR", "income", "Netflix", "monthly", 1, 15)
        assert base != candidate_fingerprint(IBAN_A, "EUR", "expense", "Netflix", "n_months", 2, 15)
        assert base != candidate_fingerprint(IBAN_A, "EUR", "expense", "Netflix", "monthly", 1, 20)
        assert base != candidate_fingerprint(
            IBAN_A, "EUR", "expense", "Netflix", "monthly", 1, 15, "2026-01-15"
        )

    def test_invalid_inputs(self) -> None:
        with pytest.raises(ValueError):
            candidate_fingerprint("", "EUR", "expense", "Netflix", "monthly", 1, 15)
        with pytest.raises(ValueError):
            candidate_fingerprint(IBAN_A, "EUR", "both", "Netflix", "monthly", 1, 15)
        with pytest.raises(ValueError):
            candidate_fingerprint(IBAN_A, "EUR", "expense", "Netflix", "biweekly", 1, 15)
        with pytest.raises(ValueError):
            candidate_fingerprint(IBAN_A, "EUR", "expense", "Netflix", "monthly", 0, 15)


class TestCandidateDetection:
    def test_two_or_more_observations_yield_reviewable_candidate(self) -> None:
        facts = [
            fact("t1", "2026-01-15", -2990),
            fact("t2", "2026-02-15", -2990),
        ]
        cands = detect_candidates(facts)
        assert len(cands) == 1
        c = cands[0]
        assert c.cadence == "monthly"
        assert c.period_n == 1
        assert c.anchor_day == 15
        assert c.anchor_date == "2026-01-15"
        assert c.amount_cents == 2990  # Betrag positiv; direction trennt Vorzeichen
        assert c.direction == "expense"
        assert c.iban == IBAN_A
        assert c.counterparty == "Netflix"
        assert c.evidence == (("t1", "2026-01-15", 2990), ("t2", "2026-02-15", 2990))
        assert c.confidence == "low"

    def test_single_observation_is_not_a_candidate(self) -> None:
        assert detect_candidates([fact("t1", "2026-01-15", -2990)]) == []

    def test_irregular_gaps_are_not_a_candidate(self) -> None:
        facts = [
            fact("t1", "2026-01-05", -1990),
            fact("t2", "2026-03-18", -1990),
            fact("t3", "2026-11-02", -1990),
        ]
        assert detect_candidates(facts) == []

    def test_income_and_expense_are_separate_series(self) -> None:
        """F04: Erstattung (+) und Ausgabe (-) derselben Gegenpartei getrennt."""
        facts = [
            fact("e1", "2026-01-15", -2990),
            fact("e2", "2026-02-15", -2990),
            fact("r1", "2026-01-20", +2990),
            fact("r2", "2026-02-20", +2990),
        ]
        cands = detect_candidates(facts)
        assert len(cands) == 2
        by_dir = {c.direction: c for c in cands}
        assert by_dir["expense"].amount_cents == 2990
        assert by_dir["income"].amount_cents == 2990
        assert by_dir["expense"].fingerprint != by_dir["income"].fingerprint

    def test_same_counterparty_on_different_accounts_isolated(self) -> None:
        """F03: Konto-/Waehrungsscope bleibt isoliert (keine Quervermischung)."""
        facts = [
            fact("a1", "2026-01-15", -2990, iban=IBAN_A, currency="EUR"),
            fact("a2", "2026-02-15", -2990, iban=IBAN_A, currency="EUR"),
            fact("b1", "2026-01-15", -2990, iban=IBAN_B, currency="EUR"),
            fact("b2", "2026-02-15", -2990, iban=IBAN_B, currency="EUR"),
            fact("u1", "2026-01-15", -2990, iban=IBAN_A, currency="USD"),
            fact("u2", "2026-02-15", -2990, iban=IBAN_A, currency="USD"),
        ]
        cands = detect_candidates(facts)
        assert len(cands) == 3
        scopes = {(c.iban, c.currency) for c in cands}
        assert scopes == {(IBAN_A, "EUR"), (IBAN_B, "EUR"), (IBAN_A, "USD")}
        assert len({c.fingerprint for c in cands}) == 3

    def test_amount_is_latest_observation(self) -> None:
        """F02: Preisaenderung bleibt als aktuellster Betrag erhalten."""
        facts = [
            fact("t1", "2026-01-15", -2990),
            fact("t2", "2026-02-15", -2990),
            fact("t3", "2026-03-15", -3490),
        ]
        cands = detect_candidates(facts)
        assert len(cands) == 1
        assert cands[0].amount_cents == 3490
        assert cands[0].evidence[-1] == ("t3", "2026-03-15", 3490)

    def test_confidence_scales_with_evidence(self) -> None:
        two = [fact("t1", "2026-01-15", -2990), fact("t2", "2026-02-15", -2990)]
        assert detect_candidates(two)[0].confidence == "low"
        eight = [fact(f"t{i}", f"2025-{m:02d}-15", -2990) for m, i in enumerate(range(1, 7), 1)]
        eight += [fact(f"t{i}", f"2026-{m:02d}-15", -2990) for m, i in ((7, 7), (8, 8))]
        assert detect_candidates(eight)[0].confidence in ("medium", "high")

    def test_incomplete_facts_are_ignored(self) -> None:
        good = [
            fact("g1", "2026-01-15", -2990),
            fact("g2", "2026-02-15", -2990),
        ]
        broken = [
            fact("x1", "2026-01-15", -2990, iban=""),
            fact("x2", "2026-02-15", 0),
            fact("x3", "15.01.2026", -2990),
        ]
        assert len(detect_candidates(good + broken)) == 1
        c = detect_candidates(good + broken)[0]
        assert {t for t, _, _ in c.evidence} == {"g1", "g2"}


# ============================================================================
# Matchen (Prompt S3.2/T8: konservativ, 1:1, Ambiguitaet => ungematcht)
# ============================================================================


def act(
    txn: str,
    when: str,
    amount: int,
    iban: str = IBAN_A,
    counterparty: str = "Netflix",
    currency: str = "EUR",
    tolerance_cents: Optional[int] = 500,
) -> Dict[str, Any]:
    d = {
        "transaction_id": txn,
        "date": when,
        "amount_cents": amount,
        "iban": iban,
        "counterparty": counterparty,
        "currency": currency,
    }
    if tolerance_cents is not None:
        d["tolerance_cents"] = tolerance_cents
    return d


def occ(
    odate: str,
    amount: int,
    key: str = "ser1",
    direction: str = "expense",
    counterparty: str = "Netflix",
    iban: str = IBAN_A,
    currency: str = "EUR",
) -> Occurrence:
    return Occurrence(
        key=key,
        source_id=key,
        origin="series",
        direction=direction,
        amount_cents=amount,
        counterparty=counterparty,
        iban=iban,
        currency=currency,
        date=odate,
        original_date=odate,
    )


class TestMatching:
    def test_exact_pair_matches(self) -> None:
        o = occ("2026-01-15", 2990)
        a = act("t1", "2026-01-15", -2990)
        res = match_occurrences([o], [a])
        assert res == {"ser1": "t1"}
        assert set(res) == {"ser1"} and set(res.values()) == {"t1"}

    def test_tolerance_bounded_by_cents(self) -> None:
        o = occ("2026-01-15", 2990)
        within = act("t1", "2026-01-15", -2990, tolerance_cents=500)
        assert match_occurrences([o], [within]) == {"ser1": "t1"}
        beyond = act("t1", "2026-01-15", -3400, tolerance_cents=500)  # 410 > 500? nein: 410 < 500
        assert match_occurrences([o], [act("t1", "2026-01-15", -3600, tolerance_cents=500)]) == {}
        _ = beyond

    def test_date_window_bounded(self) -> None:
        o = occ("2026-01-15", 2990)
        day_before = act("t1", "2026-01-14", -2990)
        assert match_occurrences([o], [day_before]) == {"ser1": "t1"}
        day_after = act("t1", "2026-01-16", -2990)
        assert match_occurrences([o], [day_after]) == {"ser1": "t1"}
        too_far = act("t1", "2026-01-17", -2990)
        assert match_occurrences([o], [too_far]) == {}

    def test_scope_mismatch_never_matches(self) -> None:
        o = occ("2026-01-15", 2990)
        wrong_iban = act("t1", "2026-01-15", -2990, iban=IBAN_B)
        wrong_cp = act("t2", "2026-01-15", -2990, counterparty="Spotify")
        wrong_cur = act("t3", "2026-01-15", -2990, currency="USD")
        assert match_occurrences([o], [wrong_iban, wrong_cp, wrong_cur]) == {}

    def test_direction_mismatch_never_matches(self) -> None:
        """F04: Erstattungs-Eingang darf nie eine Ausgabe-Serie matchen."""
        o_exp = occ("2026-01-15", 2990, direction="expense")
        o_inc = occ("2026-01-15", 2990, key="ser2", direction="income")
        refund = act("r1", "2026-01-15", +2990)
        res = match_occurrences([o_exp, o_inc], [refund])
        assert res == {"ser2": "r1"}
        assert "ser1" not in res

    def test_one_to_one_only(self) -> None:
        o = occ("2026-01-15", 2990)
        a1 = act("t1", "2026-01-15", -2990)
        a2 = act("t2", "2026-01-15", -2990)
        res = match_occurrences([o], [a1, a2])
        assert len(res) <= 1
        if res:
            assert len(set(res.values())) == 1

    def test_ambiguity_two_series_one_actual_stays_unmatched(self) -> None:
        """T8: zwei Serien, ein Beleg => Ambiguitaet, konservativ ungematcht."""
        o1 = occ("2026-01-15", 2990, key="ser1", counterparty="Netflix")
        o2 = occ("2026-01-15", 2990, key="ser2", counterparty="Netflix")
        a1 = act("t1", "2026-01-15", -2990, counterparty="Netflix")
        res = match_occurrences([o1, o2], [a1])
        assert res == {}

    def test_no_cross_pairs(self) -> None:
        """Verschobene Serie (15→20) darf den 15. nicht fressen; neue Serien-
        Vorkommen matchen nie alte (origin-Kontrolle ist aufrufseitig)."""
        o_moved = occ("2026-01-20", 2990, key="ser1")  # Move: original 15.
        o_other = occ("2026-01-15", 1990, key="ser2", counterparty="Spotify")
        a15 = act("t15", "2026-01-15", -1990, counterparty="Spotify")
        a20 = act("t20", "2026-01-20", -2990)
        res = match_occurrences([o_moved, o_other], [a15, a20])
        assert res == {"ser1": "t20", "ser2": "t15"}

    def test_empty_inputs(self) -> None:
        o = occ("2026-01-15", 2990)
        assert match_occurrences([], [act("t1", "2026-01-15", -2990)]) == {}
        assert match_occurrences([o], []) == {}

