"""UI-Regresstest für das Token-Scaling-Panel (Streamlit-Rendering).

Hintergrund (2026-09-04): ``st.selectbox(..., disabled_options=...)`` ist
kein Streamlit-API — das Panel crashte beim App-Start mit ``TypeError``.
Die Token-Scaling-Unit-Tests (``test_token_scaling_overrides.py``) testen
nur die Logik in ``utils/token_scaling.py`` und rendern nie die
Streamlit-Widgets; deshalb konnte der Bug durchlaufen. Dieser Test rendert
``_render_token_scaling_panel()`` im Streamlit-Test-Runtime (``AppTest``)
und fängt invalid-Widget-Kwargs sowie Optionslisten-Regressionen ab.

Hermetisch: ``auto_proposal`` und ``load_overrides`` werden gepatcht
(kein GPU-Zugriff, keine Persistenz-Datei); ``overrides_from_values``
läuft als echte Funktion mit.
"""

from __future__ import annotations

import dataclasses
from typing import Callable, Optional

from streamlit.testing.v1 import AppTest

from utils.token_scaling import TokenScalingOverrides, TokenScalingProposal

FAKE_MODEL_PATH = r"C:\fake_models\gemma-4-12b-it.Q4_0.gguf"


def _auto_proposal_fake(
    model_path: str,
    requested_n_ctx: Optional[int] = None,
    mmproj_path: Optional[str] = None,
) -> TokenScalingProposal:
    """Deterministischer Auto-Vorschlag.

    Bewusst mit ``kv_quant="q8_0"``: das ist der erwartete Auto-Wert auf
    dieser Hardware — das Panel bietet "auto"/"f16"/"q8_0" an (q8_0 seit
    2026-09-04 Runtime-validiert).
    """
    return TokenScalingProposal(
        n_ctx=16384,
        kv_quant="q8_0",
        output_budget=4096,
        thinking_budget=2048,
        reasoning_effort="medium",
        source={"n_ctx": "auto", "kv_quant": "auto"},
        notes=(),
    )


def _app_script() -> None:
    """Streamlit-Skript: rendert das Token-Scaling-Panel.

    Achtung: ``AppTest.from_function`` serialisiert diese Funktion in eine
    Temp-Datei — Modul-Globals des Testmoduls sind dort NICHT sichtbar.
    Der Modell-Pfad kommt daher über ``session_state``.
    """
    import streamlit as st

    import enhanced_streamlit_bot as bot

    model_path = st.session_state["ts_test_model_path"]
    st.session_state["selected_model_info"] = {
        "model_path": model_path,
        "mmproj_path": None,
    }
    bot._render_token_scaling_panel()


def _run_panel(monkeypatch) -> AppTest:
    """Panel im AppTest-Runtime rendern (mit gepatchten Hermetik-Abhängigkeiten)."""
    import utils.token_scaling as ts

    monkeypatch.setattr(ts, "auto_proposal", _auto_proposal_fake)
    monkeypatch.setattr(ts, "load_overrides", lambda model_path: TokenScalingOverrides())
    at = AppTest.from_function(_app_script, default_timeout=300)
    at.session_state["ts_test_model_path"] = FAKE_MODEL_PATH
    at.run()
    return at


def test_panel_renders_without_exceptions(monkeypatch) -> None:
    """Der eigentliche Bug (2026-09-04): invalid-Widget-KWarg → TypeError.

    Ohne diesen Test wäre ``disabled_options=[...]`` nie aufgefallen.
    """
    at = _run_panel(monkeypatch)
    assert not at.exception, f"Panel-Render fehlgeschlagen: {at.exception}"


def test_kv_quant_options_include_validated_q8_0(monkeypatch) -> None:
    """q8_0 ist Runtime-validiert (2026-09-04) → muss wählbar sein.

    Gegenregression: ``q8_0`` darf aus der Options-Liste nicht
    verschwinden (z. B. durch falsche „sicherheitsbedingte“ Ausschlüsse) —
    die Options-Liste ist das einzige Gate, das Streamlit kennt.
    """
    at = _run_panel(monkeypatch)
    assert not at.exception, f"Panel-Render fehlgeschlagen: {at.exception}"

    kv_widgets = [w for w in at.sidebar.selectbox if "f16" in list(w.options)]
    assert len(kv_widgets) == 1, "KV-Quant-Selectbox fehlt in der Sidebar"
    kv = kv_widgets[0]
    opts = list(kv.options)  # format_func-Labels (z. B. "auto" → "Auto")
    assert "q8_0" in opts, "q8_0 muss wählbar sein (Runtime-validiert)"
    assert "f16" in opts, f"unerwartete Optionsliste: {opts}"
    assert len(opts) == 3, f"unerwartete Optionsliste: {opts}"
    # Default folgt dem Auto-Vorschlag (Fake: kv_quant="q8_0") → q8_0-Slot
    # (Index 2) -- unabhängig von der Label-Formatierung (z.B. "Auto").
    assert kv.index == 2
    # Panel-Ende lief durch: ts_overrides gesetzt (q8_0 == auto → kein Override).
    assert at.session_state["ts_overrides"] is None


def test_number_inputs_present(monkeypatch) -> None:
    """Sanity: n_ctx-/Output-/Thinking-Number-Inputs sind gerendert."""
    at = _run_panel(monkeypatch)
    assert not at.exception, f"Panel-Render fehlgeschlagen: {at.exception}"
    assert len(at.sidebar.number_input) >= 3
    n_ctx_inputs = [w for w in at.sidebar.number_input if w.value == 16384]
    assert len(n_ctx_inputs) == 1
    assert n_ctx_inputs[0].value == 16384


def test_kv_default_follows_f16_proposal(monkeypatch) -> None:
    """KV-Default folgt dem Auto-Vorschlag (kv=f16 → f16-Slot).

    2026-09-04: Das Panel-Default folgt dem aktiven Vorschlag statt eines
    fiktiven "auto" -- Verhalten bleibt identisch (Wert == auto erzeugt
    kein Fake-Override).
    """
    import utils.token_scaling as ts

    def _f16_fake(model_path, requested_n_ctx=None, mmproj_path=None):
        return dataclasses.replace(
            _auto_proposal_fake(model_path, requested_n_ctx, mmproj_path),
            kv_quant="f16",
        )

    monkeypatch.setattr(ts, "auto_proposal", _f16_fake)
    monkeypatch.setattr(ts, "load_overrides", lambda model_path: TokenScalingOverrides())
    at = AppTest.from_function(_app_script, default_timeout=300)
    at.session_state["ts_test_model_path"] = FAKE_MODEL_PATH
    at.run()
    assert not at.exception, f"Panel-Render fehlgeschlagen: {at.exception}"

    kv = [w for w in at.sidebar.selectbox if "f16" in list(w.options)][0]
    assert kv.index == 1, "Default muss der f16-Slot sein (Vorschlag kv=f16)"
    # f16 == auto → wird NICHT als Fake-Override gespeichert.
    assert at.session_state["ts_overrides"] is None


def test_kv_default_falls_back_to_auto_for_unknown_proposal(monkeypatch) -> None:
    """Vorschlag-KV, das nicht im Drop-down steht (q4_0) → Default "auto"."""
    import utils.token_scaling as ts

    def _q4_fake(model_path, requested_n_ctx=None, mmproj_path=None):
        return dataclasses.replace(
            _auto_proposal_fake(model_path, requested_n_ctx, mmproj_path),
            kv_quant="q4_0",
        )

    monkeypatch.setattr(ts, "auto_proposal", _q4_fake)
    monkeypatch.setattr(ts, "load_overrides", lambda model_path: TokenScalingOverrides())
    at = AppTest.from_function(_app_script, default_timeout=300)
    at.session_state["ts_test_model_path"] = FAKE_MODEL_PATH
    at.run()
    assert not at.exception, f"Panel-Render fehlgeschlagen: {at.exception}"

    kv = [w for w in at.sidebar.selectbox if "f16" in list(w.options)][0]
    assert kv.index == 0, "Fallback-Default muss der 'auto'-Slot sein"
    assert at.session_state["ts_overrides"] is None
