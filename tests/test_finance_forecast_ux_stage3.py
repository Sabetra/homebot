"""Forecast-UX AP2 Stage 3: Wiederkehrende Serien & Erkennungs-Kandidaten.

Deterministische UI-Tests (kein LLM, keine echten DB-Schreibzugriffe):

* Rendering: Kandidaten-Sektion (Erkennungs-Button, pending-Kandidaten mit
  Evidenz, Bestätigen/Ablehnen) und Serien-Liste (Tabelle + Statusaktionen
  Pausieren/Fortsetzen/Beenden; beendete Serien ohne Aktionen).
* Verhalten: Alle Aktionen laufen über die kanonischen FinanceTools
  (``detect_series_candidates``, ``list_series_candidates``,
  ``confirm_candidate``, ``reject_candidate``, ``list_series``,
  ``pause_series``, ``resume_series``, ``end_series``) — Parameter sind
  ``fingerprint`` bzw. ``series_id`` (+ IBAN-Filter), keine DAO-Schreibzugriffe.
* i18n: Alle im Serien-Block genutzten ``finance_ui.forecast.*``-Keys
  existieren in de/en/bg.
* Kein LLM: Der Serien-Block referenziert keinen LLM-Client
  (``chat_logic``/``model_loader``/``_get_llm_client``); das Verhalten wird
  vollständig von einem deterministischen Tools-Stub gesteuert.
"""

from __future__ import annotations

import inspect
import json
import re
from pathlib import Path

import pytest

from finance import tab as finance_tab

LOCALES_DIR = Path(__file__).resolve().parents[1] / "i18n" / "locales"


def _series_block_source() -> str:
    """Quelltext des Stage-3-Serien-Blocks (Marker-basiert, stabil)."""
    source = inspect.getsource(finance_tab)
    start = source.index("# Forecast Serien & Kandidaten")
    end = source.index("def _render_forecast_bills")
    return source[start:end]


# ---------------------------------------------------------------------------
# Reine Helper-Funktionen (keine Streamlit-Interaktion)
# ---------------------------------------------------------------------------


def test_series_status_label_maps_known_statuses() -> None:
    assert finance_tab._series_status_label("active") == "aktiv"
    assert finance_tab._series_status_label("paused") == "pausiert"
    assert finance_tab._series_status_label("ended") == "beendet"


@pytest.mark.parametrize("status", [None, "", "unknown_status"])
def test_series_status_label_unknown_returns_raw(status: object) -> None:
    assert finance_tab._series_status_label(status) == str(status or "")


def test_series_source_label_maps_known_sources() -> None:
    assert finance_tab._series_source_label("manual") == "manuell"
    assert finance_tab._series_source_label("detected") == "erkannt"


@pytest.mark.parametrize("source", [None, "", "import"])
def test_series_source_label_unknown_returns_raw(source: object) -> None:
    assert finance_tab._series_source_label(source) == str(source or "")


@pytest.mark.parametrize(
    ("cadence", "period_n", "expected"),
    [
        ("monthly", 1, "Monatlich"),
        ("weekly", 1, "Wöchentlich"),
        ("yearly", 1, "Jährlich"),
        ("n_months", 1, "Monatlich"),
        ("n_months", 3, "Alle 3 Monate"),
        ("n_weeks", 1, "Wöchentlich"),
        ("n_weeks", 2, "Alle 2 Wochen"),
    ],
)
def test_series_cadence_label_maps(cadence: str, period_n: int, expected: str) -> None:
    assert finance_tab._series_cadence_label(cadence, period_n) == expected


def test_series_cadence_label_invalid_period_falls_back_to_one() -> None:
    assert finance_tab._series_cadence_label("n_months", "abc") == "Monatlich"
    assert finance_tab._series_cadence_label("n_weeks", None) == "Wöchentlich"


def test_series_cadence_label_unknown_or_empty() -> None:
    assert finance_tab._series_cadence_label("daily", 1) == "daily"
    assert finance_tab._series_cadence_label(None, 1) == ""


@pytest.mark.parametrize(
    ("item", "expected"),
    [
        ({"amount": 13.99, "currency": "EUR"}, "13,99"),
        ({"amount": 850.0}, "850,00"),
        ({"amount_cents": 100}, "–"),  # Cents-Format ist kein Serien-Format
        ({}, "–"),
        (None, "–"),
    ],
)
def test_series_amount_label(item: object, expected: str) -> None:
    assert finance_tab._series_amount_label(item) == expected


# ---------------------------------------------------------------------------
# i18n-Konsistenz + LLM-freier Serien-Block
# ---------------------------------------------------------------------------


def test_series_i18n_keys_present_in_all_locales() -> None:
    block = _series_block_source()
    keys = set(re.findall(r"finance_ui\.forecast\.([a-z_]+)", block))
    assert keys, "Serien-Block liefert keine i18n-Keys (Regex defekt?)"
    for locale in ("de", "en", "bg"):
        data = json.loads((LOCALES_DIR / f"{locale}.json").read_text(encoding="utf-8"))
        forecast = data["finance_ui"]["forecast"]
        missing = sorted(keys - set(forecast))
        assert not missing, f"{locale}: fehlende Serien-Keys {missing}"


def test_series_block_has_no_llm_references() -> None:
    """Der Serien-Workflow ist deterministisch: kein LLM-Client im Pfad."""
    block = _series_block_source()
    for identifier in ("chat_logic", "model_loader", "_get_llm_client", "llm_client"):
        assert identifier not in block, (
            f"Serien-Block darf keinen LLM-Client berühren (gefunden: {identifier})"
        )


# ---------------------------------------------------------------------------
# AppTest: Rendering + Aktionen (deterministischer Tools-Stub)
# ---------------------------------------------------------------------------


def _series_test_app() -> None:
    from typing import Any, cast

    import streamlit as st

    from finance.tab import _render_forecast_series_section

    class Tools:
        """Deterministischer Stub der kanonischen FinanceTools (AP2 S3-Subset)."""

        def detect_series_candidates(self, params):
            st.session_state["detect_params"] = params
            return {"success": True, "count": 2, "new_candidates": 1}

        def list_series_candidates(self, params):
            st.session_state["list_candidates_params"] = params
            return {
                "success": True,
                "count": 2,
                "candidates": [
                    {
                        "fingerprint": "fp-netflix",
                        "counterparty": "Netflix",
                        "cadence": "monthly",
                        "period_n": 1,
                        "amount": 13.99,
                        "currency": "EUR",
                        "direction": "expense",
                        "status": "pending",
                        "confidence": 0.95,
                        "evidence": [
                            {"transaction_id": 1, "date": "2026-08-08", "amount": 13.99},
                            {"transaction_id": 2, "date": "2026-07-08", "amount": 13.99},
                        ],
                    },
                    {
                        "fingerprint": "fp-va",
                        "counterparty": "Hausverwaltung",
                        "cadence": "n_months",
                        "period_n": 3,
                        "amount": 1200.0,
                        "currency": "CHF",
                        "direction": "expense",
                        "status": "pending",
                        "confidence": None,
                        "evidence": [],
                    },
                    {
                        "fingerprint": "fp-already-decided",
                        "counterparty": "Bereits entschieden",
                        "status": "confirmed",
                    },
                ],
            }

        def confirm_candidate(self, params):
            st.session_state["confirmed"] = params
            return {"success": True, "candidate": {"fingerprint": params.get("fingerprint")}}

        def reject_candidate(self, params):
            st.session_state["rejected"] = params
            return {"success": True, "candidate": {"fingerprint": params.get("fingerprint")}}

        def list_series(self, params):
            st.session_state["list_series_params"] = params
            return {
                "success": True,
                "count": 3,
                "series": [
                    {
                        "id": 1,
                        "counterparty": "Miete",
                        "cadence": "monthly",
                        "period_n": 1,
                        "amount": 850.0,
                        "currency": "CHF",
                        "direction": "expense",
                        "status": "active",
                        "source": "manual",
                        "effective_from": "2026-01-01",
                        "effective_to": None,
                    },
                    {
                        "id": 2,
                        "counterparty": "Versicherung",
                        "cadence": "n_weeks",
                        "period_n": 2,
                        "amount": 50.0,
                        "currency": "EUR",
                        "direction": "expense",
                        "status": "paused",
                        "source": "detected",
                        "effective_from": None,
                        "effective_to": None,
                    },
                    {
                        "id": 3,
                        "counterparty": "Alt-Abo",
                        "cadence": "monthly",
                        "period_n": 1,
                        "amount": 10.0,
                        "currency": "EUR",
                        "direction": "income",
                        "status": "ended",
                        "source": "manual",
                        "effective_from": "2025-01-01",
                        "effective_to": "2025-12-31",
                    },
                ],
            }

        def pause_series(self, params):
            st.session_state["paused"] = params
            return {"success": True, "series": {"id": params.get("series_id"), "status": "paused"}}

        def resume_series(self, params):
            st.session_state["resumed"] = params
            return {"success": True, "series": {"id": params.get("series_id"), "status": "active"}}

        def end_series(self, params):
            st.session_state["ended"] = params
            return {"success": True, "series": {"id": params.get("series_id"), "status": "ended"}}

    _render_forecast_series_section(cast(Any, Tools()), {"iban": "SYNTHETIC-IBAN"})



def test_series_section_renders_candidates_and_series() -> None:
    from streamlit.testing.v1 import AppTest

    app = AppTest.from_function(_series_test_app).run()
    assert not app.exception
    keys = {button.key for button in app.button}
    # Kandidaten: Erkennen + Bestätigen/Ablehnen nur für die 2 pending-Kandidaten
    assert "finance_forecast_series_detect" in keys
    assert "finance_forecast_series_cand_confirm_0" in keys
    assert "finance_forecast_series_cand_reject_0" in keys
    assert "finance_forecast_series_cand_confirm_1" in keys
    assert "finance_forecast_series_cand_reject_1" in keys
    assert "finance_forecast_series_cand_confirm_2" not in keys  # confirmed → ausgeblendet
    # Serien: active → Pausieren/Beenden; paused → Fortsetzen/Beenden; ended → keine Aktion
    assert "finance_forecast_series_pause_1" in keys
    assert "finance_forecast_series_end_1" in keys
    assert "finance_forecast_series_resume_2" in keys
    assert "finance_forecast_series_end_2" in keys
    assert "finance_forecast_series_pause_2" not in keys
    for key in (
        "finance_forecast_series_pause_3",
        "finance_forecast_series_resume_3",
        "finance_forecast_series_end_3",
    ):
        assert key not in keys
    # Beide Kandidaten + alle drei Serien werden gerendert (Expander)
    labels = " | ".join(expander.label for expander in app.expander)
    for name in ("Netflix", "Hausverwaltung", "Miete", "Versicherung", "Alt-Abo"):
        assert name in labels


def test_detect_button_uses_iban_filter_and_reports_result() -> None:
    from streamlit.testing.v1 import AppTest

    app = AppTest.from_function(_series_test_app).run()
    app.button(key="finance_forecast_series_detect").click().run()
    assert not app.exception
    assert app.session_state["detect_params"] == {"iban": "SYNTHETIC-IBAN"}
    # Flash über st.rerun() hinweg: Erfolgsmeldung mit Zählern (count=2, neu=1)
    success_texts = " | ".join(element.value for element in app.success)
    assert "2" in success_texts and "1" in success_texts


def test_confirm_candidate_sends_fingerprint() -> None:
    from streamlit.testing.v1 import AppTest

    app = AppTest.from_function(_series_test_app).run()
    app.button(key="finance_forecast_series_cand_confirm_0").click().run()
    assert not app.exception
    assert app.session_state["confirmed"] == {"fingerprint": "fp-netflix"}
    assert "rejected" not in app.session_state.filtered_state
    assert app.success


def test_reject_candidate_sends_fingerprint() -> None:
    from streamlit.testing.v1 import AppTest

    app = AppTest.from_function(_series_test_app).run()
    app.button(key="finance_forecast_series_cand_reject_1").click().run()
    assert not app.exception
    assert app.session_state["rejected"] == {"fingerprint": "fp-va"}
    assert "confirmed" not in app.session_state.filtered_state
    assert app.success



def test_pause_resume_end_use_canonical_tools_with_series_id() -> None:
    from streamlit.testing.v1 import AppTest

    app = AppTest.from_function(_series_test_app).run()
    app.button(key="finance_forecast_series_pause_1").click().run()
    assert not app.exception
    assert app.session_state["paused"] == {"series_id": 1}
    assert app.success

    app = AppTest.from_function(_series_test_app).run()
    app.button(key="finance_forecast_series_resume_2").click().run()
    assert not app.exception
    assert app.session_state["resumed"] == {"series_id": 2}
    assert app.success

    app = AppTest.from_function(_series_test_app).run()
    app.button(key="finance_forecast_series_end_2").click().run()
    assert not app.exception
    assert app.session_state["ended"] == {"series_id": 2}
    assert app.success


def _series_failure_app() -> None:
    from typing import Any, cast

    import streamlit as st

    from finance.tab import _render_forecast_series_section

    class Tools:
        def list_series_candidates(self, params):
            return {
                "success": True,
                "count": 1,
                "candidates": [
                    {
                        "fingerprint": "fp-x",
                        "counterparty": "X",
                        "cadence": "monthly",
                        "period_n": 1,
                        "amount": 5.0,
                        "currency": "EUR",
                        "direction": "expense",
                        "status": "pending",
                        "confidence": None,
                        "evidence": [],
                    }
                ],
            }

        def confirm_candidate(self, params):
            st.session_state["confirmed"] = params
            return {"success": False, "error": "fingerprint unbekannt", "error_class": "not_found"}

        def list_series(self, params):
            return {"success": False, "error": "db offline", "error_class": "invalid_param"}

    _render_forecast_series_section(cast(Any, Tools()), {})


def test_failed_tools_show_errors_without_crash() -> None:
    from streamlit.testing.v1 import AppTest

    app = AppTest.from_function(_series_failure_app).run()
    assert not app.exception
    errors = " | ".join(element.value for element in app.error)
    assert "db offline" in errors

    app.button(key="finance_forecast_series_cand_confirm_0").click().run()
    assert not app.exception
    assert app.session_state["confirmed"] == {"fingerprint": "fp-x"}
    errors = " | ".join(element.value for element in app.error)
    assert "fingerprint unbekannt" in errors

