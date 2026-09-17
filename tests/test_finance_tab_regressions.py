from __future__ import annotations

import pytest

from finance import tab as finance_tab
from finance.db_schema import _to_cents


@pytest.mark.parametrize(
    ("amount", "expected_cents"),
    [
        (1.005, 101),
        (-1.005, -101),
        (2.675, 268),
    ],
)
def test_to_cents_uses_commercial_rounding(amount: float, expected_cents: int) -> None:
    assert _to_cents(amount) == expected_cents


def test_format_eur_has_no_trailing_whitespace() -> None:
    formatted = finance_tab._format_eur(1234.5)

    assert formatted == "1.234,50"
    assert formatted == formatted.strip()


def test_relink_transfers_uses_productive_settlement_window() -> None:
    class _RecordingDB:
        received_max_days: int | None = None

        def relink_all_transfers(self, *, max_days: int) -> int:
            self.received_max_days = max_days
            return 3

    db = _RecordingDB()
    relink = getattr(finance_tab, "_relink_transfers", None)

    assert callable(relink)
    assert relink(db, productive_window_days=45) == 3
    assert db.received_max_days == 45


# ---------------------------------------------------------------------------
# Sparziele (Goals / Sinking Funds, Finance SOTA Phase 2) — pure helpers
# ---------------------------------------------------------------------------


def test_goal_status_label_maps_known_statuses() -> None:
    assert finance_tab._goal_status_label("active") == "aktiv"
    assert finance_tab._goal_status_label("paused") == "pausiert"
    assert finance_tab._goal_status_label("achieved") == "erreicht"
    assert finance_tab._goal_status_label("archived") == "archiviert"


@pytest.mark.parametrize("status", [None, "", "unknown_status"])
def test_goal_status_label_unknown_returns_raw(status: object) -> None:
    assert finance_tab._goal_status_label(status) == str(status or "")


@pytest.mark.parametrize("name", ["", "   ", None])
def test_validate_goal_form_requires_name(name: object) -> None:
    assert finance_tab._validate_goal_form(name, "100") == "finance_ui.goals.name_required"


@pytest.mark.parametrize("target", [None, "", "abc", 0, 0.0, -5])
def test_validate_goal_form_requires_positive_target(target: object) -> None:
    assert finance_tab._validate_goal_form("Urlaub", target) == "finance_ui.goals.target_invalid"


def test_validate_goal_form_accepts_valid_input() -> None:
    assert finance_tab._validate_goal_form("Urlaub", "1000.50") is None
    assert finance_tab._validate_goal_form("Urlaub", 2500) is None


def test_goal_form_error_text_maps_error_keys() -> None:
    name_error = finance_tab._goal_form_error_text("finance_ui.goals.name_required")
    target_error = finance_tab._goal_form_error_text("finance_ui.goals.target_invalid")

    assert name_error == "Name und Konto sind erforderlich."
    assert target_error == "Zielbetrag muss positiv sein."
    assert name_error != target_error


@pytest.mark.parametrize(
    ("progress", "expected"),
    [
        ({"progress_cents": 1550}, 15.5),
        ({"progress_cents": 0}, 0.0),
        ({"other": 1}, None),
        ({}, None),
        (None, None),
        ("nope", None),
    ],
)
def test_progress_amount_converts_cents(progress: object, expected: float | None) -> None:
    assert finance_tab._progress_amount(progress, "progress_cents") == expected


@pytest.mark.parametrize(
    ("cents", "expected"),
    [
        (True, None),  # Bool ist KEIN numerisches Cents-Feld
        ("1500", None),
        (1234.5, 12.35),
    ],
)
def test_progress_amount_rejects_non_numeric(cents: object, expected: float | None) -> None:
    assert finance_tab._progress_amount({"progress_cents": cents}, "progress_cents") == expected


def test_fmt_money_formats_and_handles_invalid() -> None:
    assert finance_tab._fmt_money(1234.5) == "1.234,50"
    assert finance_tab._fmt_money(0) == "0,00"
    assert finance_tab._fmt_money(None) == "–"
    assert finance_tab._fmt_money("abc") == "–"


def test_goal_table_rows_skips_invalid_items() -> None:
    assert finance_tab._goal_table_rows(None) == []
    assert finance_tab._goal_table_rows(["nope", 42, ["nested"]]) == []


def test_goal_table_rows_maps_goal_and_progress() -> None:
    items = [
        {
            "goal": {
                "goal_id": 1,
                "name": "Urlaub",
                "iban": "DE02120300000000202051",
                "target_amount": 3000.0,
                "target_date": "2026-08-01",
                "monthly_rate": 250.0,
                "status": "active",
            },
            "progress": {
                "progress_cents": 150000,
                "remaining_cents": 150000,
                "progress_pct": 50.0,
            },
        }
    ]

    rows = finance_tab._goal_table_rows(items)

    assert len(rows) == 1
    row = rows[0]
    assert row["Name"] == "Urlaub"
    assert row["Konto"] == "DE02120300000000202051"
    assert row["Ziel"] == 3000.0
    assert row["Gespart"] == 1500.0
    assert row["Offen"] == 1500.0
    assert row["%"] == 50.0
    assert row["Monatsrate"] == 250.0
    assert row["Zieldatum"] == "2026-08-01"
    assert row["Status"] == "aktiv"


def test_goal_table_rows_tolerates_missing_parts() -> None:
    row = finance_tab._goal_table_rows([{"goal": {"name": "X"}, "progress": None}])[0]

    assert row["Name"] == "X"
    assert row["Konto"] == ""
    assert row["Gespart"] is None
    assert row["Offen"] is None
    assert row["Monatsrate"] is None
    assert row["Zieldatum"] == ""
    assert row["Status"] == ""


def test_candidate_table_rows_skips_invalid_items() -> None:
    assert finance_tab._candidate_table_rows(None) == []
    assert finance_tab._candidate_table_rows(["nope", 7]) == []


def test_candidate_table_rows_maps_fields() -> None:
    rows = finance_tab._candidate_table_rows(
        [
            {
                "counterparty": "Handwerker",
                "occurrences": 3,
                "average_amount": 1200.0,
                "monthly_equivalent": 100.0,
            }
        ]
    )

    assert len(rows) == 1
    assert rows[0]["Gegenseite"] == "Handwerker"
    assert rows[0]["Vorkommen"] == 3
    assert rows[0]["Ø Betrag"] == 1200.0
    assert rows[0]["Monats-Äquivalent"] == 100.0


class _FakeTx:
    """Minimales Transaction-Doppel für die Goals-Selectbox-Helfer."""

    def __init__(
        self,
        tx_id: int | None,
        amount_cents: object,
        booking_date: str | None = "2026-01-05",
        counterparty: str = "Gegenseite",
    ) -> None:
        self.id = tx_id
        self.amount_cents = amount_cents
        self.booking_date = booking_date
        self.counterparty = counterparty


def test_assignable_transactions_filters_assigned_ids() -> None:
    txs = [_FakeTx(1, -10000), _FakeTx(2, -20000), _FakeTx(3, -3000)]

    assert finance_tab._assignable_transactions(txs, {2}) == [txs[0], txs[2]]
    assert finance_tab._assignable_transactions(txs, None) == txs
    assert finance_tab._assignable_transactions(txs, [1, 2, 3]) == []
    assert finance_tab._assignable_transactions(txs, {True}) == txs  # Bool ist keine tx-ID


def test_assignable_transactions_accepts_objects_with_id() -> None:
    class _Ref:
        def __init__(self, tx_id: int) -> None:
            self.id = tx_id

    txs = [_FakeTx(1, -10000), _FakeTx(2, -20000)]

    assert finance_tab._assignable_transactions(txs, [_Ref(1)]) == [txs[1]]


def test_assignable_transactions_drops_entries_without_id() -> None:
    txs = [_FakeTx(None, -10000), _FakeTx(2, -20000)]

    assert finance_tab._assignable_transactions(txs, set()) == [txs[1]]


def test_transaction_option_label_signs_and_fallbacks() -> None:
    positive = finance_tab._transaction_option_label(_FakeTx(1, 12345, "2026-01-05", "Sparrück"))
    negative = finance_tab._transaction_option_label(_FakeTx(2, -9900, "2026-02-01", "Bank"))
    invalid = finance_tab._transaction_option_label(_FakeTx(3, "n/a", None, ""))

    assert positive == "2026-01-05 · Sparrück (+123,45)"
    assert negative == "2026-02-01 · Bank (-99,00)"
    assert invalid == "? ·  (0.00)"


def test_candidate_goal_params_prefers_annual_and_monthly() -> None:
    candidate = {
        "counterparty": "  Kfz-Versicherung  ",
        "average_amount": 500.0,
        "monthly_equivalent": 42.0,
        "annual_equivalent": 510.0,
    }

    params = finance_tab._candidate_goal_params(candidate, "DE02120300000000202051")

    assert params == {
        "name": "Kfz-Versicherung",
        "iban": "DE02120300000000202051",
        "target_amount": 510.0,
        "monthly_rate": 42.0,
    }


def test_candidate_goal_params_falls_back_to_average() -> None:
    params = finance_tab._candidate_goal_params({"counterparty": "X", "average_amount": 300.5}, "IBAN")

    assert params["name"] == "X"
    assert params["target_amount"] == 300.5
    assert params["monthly_rate"] == 300.5


def test_candidate_goal_params_non_dict_is_safe() -> None:
    params = finance_tab._candidate_goal_params(None, "IBAN")

    assert params == {"name": "", "iban": "IBAN", "target_amount": 0.0, "monthly_rate": 0.0}


def test_projection_headline_key_priority() -> None:
    headline = finance_tab._projection_headline_key

    # Kein Dict / ungültig
    assert headline(None) == "finance_ui.goals.projection_none"
    assert headline("nope") == "finance_ui.goals.projection_none"
    # Erreicht schlägt Overdue (Priorität)
    assert headline({"achieved": True, "overdue": True}) == "finance_ui.goals.projection_achieved_now"
    # Overdue
    assert headline(
        {"overdue": True, "target_date": "2026-01-01", "remaining": 500.0}
    ) == "finance_ui.goals.projection_overdue"
    # On-/Off-Track (explizit)
    assert headline({"on_track": True}) == "finance_ui.goals.projection_on_track"
    assert headline({"on_track": False}) == "finance_ui.goals.projection_off_track"
    # Keine Rate -> keine Projektion (on_track=None bleibt unentschieden)
    assert headline({"rate": None, "achieved_month": "2026-10"}) == "finance_ui.goals.projection_none"
    # Erreichbarkeits-Monat
    assert headline({"rate": 100.0, "achieved_month": "2026-10"}) == "finance_ui.goals.projection_achieved"
    # Verbleibende Monate
    assert headline({"rate": 100.0, "months_left_at_rate": 3}) == "finance_ui.goals.projection_months_left"
    # Rate vorhanden, aber keine Ableitung möglich
    assert headline({"rate": 100.0}) == "finance_ui.goals.projection_none"


@pytest.mark.parametrize(
    "projection",
    [
        None,
        {"achieved": True},
        {"overdue": True, "target_date": "2026-01-01", "remaining": 500.0},
        {"on_track": True},
        {"on_track": False},
        {"rate": None},
        {"rate": 100.0, "achieved_month": "2026-10"},
        {"rate": 100.0, "months_left_at_rate": 3},
    ],
)
def test_projection_headline_text_is_nonempty(projection: object) -> None:
    text = finance_tab._projection_headline_text(projection)

    assert isinstance(text, str)
    assert text.strip()


def test_projection_headline_text_overdue_includes_date() -> None:
    text = finance_tab._projection_headline_text(
        {"overdue": True, "target_date": "2026-01-01", "remaining": 500.0}
    )

    assert "2026-01-01" in text


@pytest.mark.parametrize("month, valid", [("2026-09", True), ("2026-13", False), ("2026-00", False), ("2026-9", False), ("", False)])
def test_month_input_validation(month, valid):
    assert finance_tab._valid_month(month) is valid


def _candidate_test_app():
    from types import SimpleNamespace
    import streamlit as st
    from finance.tab import _render_goals_candidates

    class Tools:
        def suggest_goal_candidates(self, params):
            st.session_state["search_count"] = st.session_state.get("search_count", 0) + 1
            return {"success": True, "candidates": [{"counterparty": "Synthetic Saving", "occurrences": 3,
                    "average_amount": 100, "monthly_equivalent": 100, "annual_equivalent": 1200}]}

        def upsert_goal(self, params):
            st.session_state["created_goal"] = params
            return {"success": True}

    _render_goals_candidates(Tools(), [SimpleNamespace(iban="SYNTHETIC_CHF", currency="CHF"),
                                     SimpleNamespace(iban="SYNTHETIC_EUR", currency="EUR")])


def test_candidate_can_be_created_on_subsequent_rerun():
    from streamlit.testing.v1 import AppTest

    app = AppTest.from_function(_candidate_test_app).run()
    assert not app.exception
    app.button(key="finance_goals_cand_run").click().run()
    assert not app.exception
    app.button(key="finance_goals_cand_use_0").click().run()
    assert not app.exception
    assert app.session_state.filtered_state["created_goal"] == {
        "name": "Synthetic Saving", "iban": "SYNTHETIC_CHF", "currency": "CHF",
        "target_amount": 1200, "monthly_rate": 100,
    }
    assert app.session_state.filtered_state["search_count"] == 1


def test_candidate_cache_is_scoped_to_account():
    from streamlit.testing.v1 import AppTest

    app = AppTest.from_function(_candidate_test_app).run()
    app.button(key="finance_goals_cand_run").click().run()
    app.selectbox(key="finance_goals_cand_iban").select("SYNTHETIC_EUR").run()
    assert not app.exception
    assert "finance_goals_cand_use_0" not in [button.key for button in app.button]
    assert "created_goal" not in app.session_state.filtered_state


def _assignment_test_app():
    from types import SimpleNamespace
    from typing import Any, cast
    import streamlit as st
    from finance.tab import _render_goal_assign

    class DB:
        def list_accounts(self):
            return [SimpleNamespace(iban="SYNTHETIC", id=1)]

        def assigned_transaction_ids(self):
            return [33]

        def query_transactions(self, **kwargs):
            return [SimpleNamespace(id=identifier, amount_cents=10000,
                    booking_date="2026-09-01", counterparty="Synthetic") for identifier in (11, 22, 33)]

    class Tools:
        def list_goal_contributions(self, params):
            return {"success": True, "contributions": []}

        def assign_goal_contribution(self, params):
            st.session_state["assigned_tx"] = params["transaction_id"]
            return {"success": True}

    _render_goal_assign(cast(Any, DB()), Tools(), {"goal_id": 1, "iban": "SYNTHETIC"})


def test_goal_assignment_uses_id_and_excludes_other_goals():
    from streamlit.testing.v1 import AppTest

    app = AppTest.from_function(_assignment_test_app).run()
    assert not app.exception
    options = app.selectbox(key="finance_goals_assign_select_1").options
    assert len(options) == 2
    assert options[0] != options[1]
    app.selectbox(key="finance_goals_assign_select_1").select(22).run()
    app.button(key="finance_goals_assign_btn_1").click().run()
    assert not app.exception
    assert app.session_state.filtered_state["assigned_tx"] == 22


def _analytics_test_app():
    from types import SimpleNamespace
    from typing import Any, cast
    import streamlit as st
    from finance.tab import _render_analytics_tab

    class DB:
        def list_accounts(self):
            return [SimpleNamespace(id=1, iban="SYNTHETIC", bank_name="Synthetic")]

        def list_transaction_currencies(self, account_id=None):
            return ["CHF", "EUR"]

        def aggregate(self, **kwargs):
            st.session_state["aggregate_scope"] = kwargs
            return []

        def monthly_report(self, month, **kwargs):
            st.session_state["report_scope"] = kwargs
            return {"income_cents": 10000, "expense_cents": -5000, "net_cents": 5000,
                    "tx_count": 2, "budget_status": []}

    _render_analytics_tab(cast(Any, DB()))
    st.button("Synthetic rerun", key="synthetic_rerun")


def test_analytics_report_keeps_currency_and_survives_rerun():
    from streamlit.testing.v1 import AppTest

    app = AppTest.from_function(_analytics_test_app).run()
    app.selectbox(key="finance_analytics_currency").select("EUR").run()
    app.button(key="finance_report_btn").click().run()
    assert not app.exception
    assert app.session_state.filtered_state["aggregate_scope"]["currency"] == "EUR"
    assert app.session_state.filtered_state["report_scope"]["currency"] == "EUR"
    assert len(app.metric) == 4
    app.button(key="synthetic_rerun").click().run()
    assert not app.exception
    assert len(app.metric) == 4


def test_invalid_month_is_reported_without_streamlit_exception():
    from streamlit.testing.v1 import AppTest

    app = AppTest.from_function(_analytics_test_app).run()
    app.text_input(key="finance_report_month").input("2026-13").run()
    app.button(key="finance_report_btn").click().run()
    assert not app.exception
    assert len(app.error) == 1
    assert "report_scope" not in app.session_state.filtered_state