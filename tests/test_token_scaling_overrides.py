"""Tests für ``utils/token_scaling.py`` — UI-Overrides, Persistenz, Präzedenz.

Abdeckung:
  - ``compute_sweet_spot`` (PURE-Kern: Determinismus, KV-Fallback, Invariante)
  - ``resolve_proposal`` (Präzedenz UI > ENV > Auto; ungültige Werte; effort=off)
  - ``TokenScalingOverrides`` (Serialisierung, from_dict-Resilienz)
  - ``overrides_from_values`` (UI → Override-Objekt, "Wert = Auto → kein Override")
  - Persistenz (save/load/clear, Korruption, leere Overrides)
  - ``main_generation_max_tokens`` (Budget-Floor)
  - ``propose`` (End-to-End mit Fake-Modell + Registrierung)
"""

from __future__ import annotations

import json

import pytest

from utils import token_scaling as ts
from utils.token_scaling import (
    TokenScalingOverrides,
    TokenScalingProposal,
    allowed_reasoning_efforts,
    compute_sweet_spot,
    main_generation_max_tokens,
    overrides_from_values,
    resolve_proposal,
)

# Echte ENV-Namen aus ``utils.token_scaling`` (Single Source of Truth) plus
# die Persistenz-Umgebungsvariable (für Tests).
ENV_KEYS = (
    ts.ENV_N_CTX,
    ts.ENV_KV_QUANT,
    ts.ENV_MAX_OUTPUT_TOKENS,
    ts.ENV_THINKING_BUDGET,
    ts.ENV_REASONING_EFFORT,
    "BOT6_TOKEN_SCALING_OVERRIDES",
)


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch):
    """ENV-Overrides + globaler Proposal-Registrierungs-Zustand isolieren."""
    for key in ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    ts.set_current_proposal(None)
    yield
    ts.set_current_proposal(None)


def _base(**overrides) -> TokenScalingProposal:
    """Deterministischer Basis-Vorschlag: 24 GB VRAM, 16 GB Modell, 100 kB/Token.

    Erwartet: n_ctx=32768 (f16 passt), thinking=8192, output=16384,
    effort=medium (Default-Closed-Set enthält "medium").
    """
    args = dict(
        vram_ceiling_gb=24.0,
        weights_gb=16.0,
        kv_bytes_per_token=100_000,
        requested_n_ctx=32_768,
        is_reasoning=True,
    )
    args.update(overrides)
    return compute_sweet_spot(**args)


# ── compute_sweet_spot (PURE-Kern) ──────────────────────────────────────────

def test_base_proposal_deterministic_and_auto_sourced() -> None:
    p = _base()
    assert p.n_ctx == 32768
    assert p.kv_quant == "f16"
    assert p.thinking_budget == 8192
    assert p.output_budget == 16384
    assert p.reasoning_effort == "medium"
    assert all(src == "auto" for src in p.source.values())


def test_non_reasoning_model_gets_no_thinking_budget() -> None:
    p = _base(is_reasoning=False)
    assert p.thinking_budget == 0
    assert p.output_budget == 8192  # min(40 % · 32768, 8192, available)
    assert p.reasoning_effort == "off"


def test_kv_falls_back_to_q8_0_with_note() -> None:
    # 300 kB/Token: f16(32768) und q8_0(32768) passen nicht, q8_0(16384) ja.
    p = _base(kv_bytes_per_token=300_000)
    assert p.n_ctx == 16384
    assert p.kv_quant == "q8_0"
    assert any("q8_0" in note for note in p.notes)


def test_invariant_thinking_plus_output_within_window() -> None:
    for kv_bpt, requested in ((100_000, 32768), (300_000, 32768), (1_000_000, 65536), (40_000, 16384)):
        p = compute_sweet_spot(
            vram_ceiling_gb=24.0,
            weights_gb=16.0,
            kv_bytes_per_token=kv_bpt,
            requested_n_ctx=requested,
            is_reasoning=True,
        )
        assert p.thinking_budget + p.output_budget <= p.n_ctx - 2048
        assert p.n_ctx <= requested


def test_hybrid_fixed_overhead_reduces_context() -> None:
    ohne = _base(fixed_overhead_gb=0.0)
    mit = _base(fixed_overhead_gb=6.0)  # z. B. SSM-Zustand
    assert mit.n_ctx <= ohne.n_ctx


def test_requested_n_ctx_is_upper_bound() -> None:
    p = _base(requested_n_ctx=8192)
    assert p.n_ctx <= 8192


# ── allowed_reasoning_efforts ───────────────────────────────────────────────

def test_allowed_efforts_qwen35_closed_set() -> None:
    assert allowed_reasoning_efforts("qwen35") == ("xhigh", "medium", "low")


def test_allowed_efforts_unknown_arch_never_empty() -> None:
    assert "off" in allowed_reasoning_efforts(None)
    assert "medium" in allowed_reasoning_efforts("gemma4")


# ── resolve_proposal: Präzedenz UI > ENV > Auto ─────────────────────────────

def test_resolve_without_overrides_keeps_auto() -> None:
    base = _base()
    result = resolve_proposal(base, env={k: None for k in ENV_KEYS})
    assert (result.n_ctx, result.kv_quant) == (base.n_ctx, base.kv_quant)
    assert result.thinking_budget == base.thinking_budget
    assert result.output_budget == base.output_budget
    assert all(src == "auto" for src in result.source.values())


def test_resolve_env_overrides_beat_auto() -> None:
    base = _base()
    env = {
        ts.ENV_N_CTX: "8192",
        ts.ENV_KV_QUANT: "Q8_0",  # Uppercase → normalisiert
        ts.ENV_MAX_OUTPUT_TOKENS: "4096",
        ts.ENV_THINKING_BUDGET: "2048",
        ts.ENV_REASONING_EFFORT: "high",
    }
    result = resolve_proposal(base, env=env)
    assert result.n_ctx == 8192
    assert result.kv_quant == "q8_0"
    assert result.output_budget == 4096
    assert result.thinking_budget == 2048
    assert result.reasoning_effort == "high"
    assert all(src == "env" for src in result.source.values())


def test_resolve_ui_overrides_beat_env() -> None:
    base = _base()
    env = {
        ts.ENV_N_CTX: "8192",
        ts.ENV_KV_QUANT: "q8_0",
        ts.ENV_MAX_OUTPUT_TOKENS: "4096",
        ts.ENV_THINKING_BUDGET: "2048",
        ts.ENV_REASONING_EFFORT: "high",
    }
    ui = TokenScalingOverrides(n_ctx=16384, output_budget=1024)
    result = resolve_proposal(base, env=env, explicit=ui)
    assert result.n_ctx == 16384 and result.source["n_ctx"] == "user"
    assert result.output_budget == 1024 and result.source["output_budget"] == "user"
    # Nicht-UI-Felder fallen auf ENV zurück.
    assert result.kv_quant == "q8_0" and result.source["kv_quant"] == "env"
    assert result.thinking_budget == 2048 and result.source["thinking_budget"] == "env"
    assert result.reasoning_effort == "high" and result.source["reasoning_effort"] == "env"


def test_resolve_partial_ui_leaves_rest_auto() -> None:
    base = _base()
    ui = TokenScalingOverrides(kv_quant="q8_0")
    result = resolve_proposal(base, env={k: None for k in ENV_KEYS}, explicit=ui)
    assert result.kv_quant == "q8_0" and result.source["kv_quant"] == "user"
    assert result.n_ctx == base.n_ctx and result.source["n_ctx"] == "auto"


def test_resolve_effort_off_forces_zero_thinking() -> None:
    base = _base()
    env = {ts.ENV_THINKING_BUDGET: "2048", ts.ENV_N_CTX: None, ts.ENV_KV_QUANT: None,
           ts.ENV_MAX_OUTPUT_TOKENS: None, ts.ENV_REASONING_EFFORT: None}
    result = resolve_proposal(
        base, env=env, explicit=TokenScalingOverrides(reasoning_effort="off")
    )
    assert result.reasoning_effort == "off"
    assert result.source["reasoning_effort"] == "user"
    assert result.thinking_budget == 0  # Hard-Invariante, gewinnt über ENV


def test_resolve_invalid_ui_values_kept_auto_with_note() -> None:
    base = _base()
    ui = TokenScalingOverrides(
        n_ctx="garbage", kv_quant="q4_0", reasoning_effort="bogus"
    )
    result = resolve_proposal(base, env={k: None for k in ENV_KEYS}, explicit=ui)
    assert result.n_ctx == base.n_ctx and result.source["n_ctx"] == "auto"
    assert result.kv_quant == base.kv_quant and result.source["kv_quant"] == "auto"
    assert result.reasoning_effort == base.reasoning_effort
    assert any("ungültig" in note or "nicht vom Template erlaubt" in note for note in result.notes)


def test_resolve_invalid_env_values_kept_auto() -> None:
    base = _base()
    env = {
        ts.ENV_N_CTX: "abc",
        ts.ENV_KV_QUANT: "q2_k",
        ts.ENV_MAX_OUTPUT_TOKENS: "",
        ts.ENV_THINKING_BUDGET: "  ",
        ts.ENV_REASONING_EFFORT: "extreme",
    }
    result = resolve_proposal(base, env=env)
    assert result.n_ctx == base.n_ctx and result.source["n_ctx"] == "auto"
    assert result.kv_quant == base.kv_quant and result.source["kv_quant"] == "auto"
    assert result.reasoning_effort == base.reasoning_effort


def test_resolve_enforces_invariant_after_override() -> None:
    base = _base()  # 32768 / think 8192 / out 16384
    ui = TokenScalingOverrides(n_ctx=4096)  # Fenster viel kleiner
    result = resolve_proposal(base, env={k: None for k in ENV_KEYS}, explicit=ui)
    assert result.n_ctx == 4096
    assert result.thinking_budget + result.output_budget <= 4096 - 2048
    assert "Invariante" in " ".join(result.notes)


def test_resolve_n_ctx_floor_512() -> None:
    base = _base()
    result = resolve_proposal(
        base, env={k: None for k in ENV_KEYS},
        explicit=TokenScalingOverrides(n_ctx=64),
    )
    assert result.n_ctx == 512


# ── TokenScalingOverrides: Serialisierung & Resilienz ───────────────────────

def test_overrides_to_raw_normalizes_and_skips_none() -> None:
    ov = TokenScalingOverrides(n_ctx=4096, kv_quant="Q8_0", reasoning_effort="HIGH")
    raw = ov.to_raw()
    assert raw == {"n_ctx": "4096", "kv_quant": "q8_0", "reasoning_effort": "high"}
    assert ov.is_empty is False


def test_overrides_empty_is_empty() -> None:
    ov = TokenScalingOverrides()
    assert ov.is_empty is True
    assert ov.to_raw() == {}


def test_overrides_dict_roundtrip() -> None:
    ov = TokenScalingOverrides(n_ctx=8192, output_budget=2048, thinking_budget=1024)
    restored = TokenScalingOverrides.from_dict(ov.to_dict())
    assert restored == ov


def test_overrides_from_dict_invalid_values_become_none() -> None:
    ov = TokenScalingOverrides.from_dict(
        {"n_ctx": "abc", "kv_quant": "q4_0", "output_budget": 4096}
    )
    assert ov.n_ctx is None
    assert ov.kv_quant is None
    assert ov.output_budget == 4096
    assert not ov.is_empty


# ── overrides_from_values (UI: Wert = Auto → kein Override) ─────────────────

def test_overrides_from_values_all_auto_returns_none() -> None:
    base = _base()
    result = overrides_from_values(
        base,
        n_ctx=base.n_ctx,
        kv_quant=base.kv_quant,
        output_budget=base.output_budget,
        thinking_budget=base.thinking_budget,
        reasoning_effort=base.reasoning_effort,
    )
    assert result is None


def test_overrides_from_values_partial() -> None:
    base = _base()
    result = overrides_from_values(
        base,
        n_ctx=8192,
        kv_quant=base.kv_quant,
        output_budget=1024,
        thinking_budget=base.thinking_budget,
        reasoning_effort="off",
    )
    assert result is not None
    assert result.n_ctx == 8192
    assert result.kv_quant is None  # gleich wie Auto → kein Override
    assert result.output_budget == 1024
    assert result.thinking_budget is None
    assert result.reasoning_effort == "off"


def test_overrides_from_values_invalid_returns_none() -> None:
    base = _base()
    result = overrides_from_values(
        base,
        n_ctx="not-an-int",  # type: ignore[arg-type]
        kv_quant="q4_0",
        output_budget="x",  # type: ignore[arg-type]
        thinking_budget=None,
        reasoning_effort="",
    )
    assert result is None


# ── Persistenz (save / load / clear) ────────────────────────────────────────

def test_persistence_roundtrip(monkeypatch, tmp_path) -> None:
    target = tmp_path / "overrides.json"
    monkeypatch.setenv("BOT6_TOKEN_SCALING_OVERRIDES", str(target))
    model_a = "C:\\models\\modelA.gguf"
    model_b = "C:\\models\\modelB.gguf"

    assert ts.load_overrides(model_a).n_ctx is None  # leer vor save

    ov = TokenScalingOverrides(n_ctx=8192, kv_quant="q8_0")
    ts.save_overrides(model_a, ov)
    assert ts.load_overrides(model_a) == ov
    assert ts.load_overrides(model_b).is_empty  # andere Modelle unbeeinflusst


def test_persistence_save_empty_removes_entry(monkeypatch, tmp_path) -> None:
    target = tmp_path / "overrides.json"
    monkeypatch.setenv("BOT6_TOKEN_SCALING_OVERRIDES", str(target))
    model = "C:\\models\\modelA.gguf"

    ts.save_overrides(model, TokenScalingOverrides(n_ctx=4096))
    assert ts.load_overrides(model).n_ctx == 4096

    ts.save_overrides(model, TokenScalingOverrides())  # Reset → Eintrag weg
    assert ts.load_overrides(model).is_empty


def test_persistence_clear_single(monkeypatch, tmp_path) -> None:
    target = tmp_path / "overrides.json"
    monkeypatch.setenv("BOT6_TOKEN_SCALING_OVERRIDES", str(target))
    a, b = "modelA.gguf", "modelB.gguf"
    ts.save_overrides(a, TokenScalingOverrides(n_ctx=4096))
    ts.save_overrides(b, TokenScalingOverrides(kv_quant="q8_0"))

    ts.clear_overrides(a)
    assert ts.load_overrides(a).is_empty
    assert ts.load_overrides(b).kv_quant == "q8_0"


def test_persistence_clear_all_removes_file(monkeypatch, tmp_path) -> None:
    target = tmp_path / "overrides.json"
    monkeypatch.setenv("BOT6_TOKEN_SCALING_OVERRIDES", str(target))
    ts.save_overrides("modelA.gguf", TokenScalingOverrides(n_ctx=4096))
    assert target.exists()

    ts.clear_overrides("__all__")
    assert not target.exists()


def test_persistence_load_missing_file_is_empty(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("BOT6_TOKEN_SCALING_OVERRIDES", str(tmp_path / "missing.json"))
    assert ts.load_overrides("any.gguf").is_empty


def test_persistence_load_corrupt_file_is_empty(monkeypatch, tmp_path) -> None:
    target = tmp_path / "overrides.json"
    target.write_text("{definitely not json", encoding="utf-8")
    monkeypatch.setenv("BOT6_TOKEN_SCALING_OVERRIDES", str(target))
    assert ts.load_overrides("any.gguf").is_empty  # nie-failing


def test_persistence_file_is_valid_json_and_atomic(monkeypatch, tmp_path) -> None:
    target = tmp_path / "overrides.json"
    monkeypatch.setenv("BOT6_TOKEN_SCALING_OVERRIDES", str(target))
    ts.save_overrides("m1.gguf", TokenScalingOverrides(n_ctx=4096))
    data = json.loads(target.read_text(encoding="utf-8"))
    assert data["m1.gguf"] == {"n_ctx": "4096"}
    assert not target.with_suffix(".json.tmp").exists()  # kein Atomar-Rückstand


# ── main_generation_max_tokens ───────────────────────────────────────────────

def test_main_max_tokens_without_proposal_uses_fallback() -> None:
    assert main_generation_max_tokens(fallback=4096, current=None) == 4096
    assert main_generation_max_tokens(fallback=4096, current=8192) == 8192
    assert main_generation_max_tokens(fallback=128, current=None) == 128  # Floor


def test_main_max_tokens_uses_proposal_budget_as_floor() -> None:
    base = _base()  # think 8192 + out 16384 = 24576
    ts.set_current_proposal(base)
    assert main_generation_max_tokens(fallback=4096, current=2048) == 24576
    # User-Wert bleibt als Minimum wirksam
    assert main_generation_max_tokens(fallback=4096, current=30000) == 30000


def test_main_max_tokens_zero_budget_proposal_falls_back() -> None:
    p = TokenScalingProposal(
        n_ctx=1024, kv_quant="f16", output_budget=0, thinking_budget=0,
        reasoning_effort="off",
        source={"n_ctx": "auto", "kv_quant": "auto", "output_budget": "auto",
                "thinking_budget": "auto", "reasoning_effort": "auto"},
        notes=(),
    )
    ts.set_current_proposal(p)
    assert main_generation_max_tokens(fallback=4096, current=None) == 4096


def test_current_proposal_registry_thread_safe() -> None:
    import threading

    errors: list[BaseException] = []

    def worker() -> None:
        try:
            for _ in range(200):
                ts.set_current_proposal(_base())
                assert ts.current_proposal() is not None
        except BaseException as exc:  # pragma: no cover - Test-Wächter
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    assert ts.current_proposal() is not None


# ── propose() End-to-End (Fake-Modell, keine GPU-Last) ──────────────────────

def test_propose_applies_explicit_and_registers(tmp_path) -> None:
    model = tmp_path / "gemma4-12b-test.gguf"  # Name ohne Reasoning-Marker
    model.write_bytes(b"GGUF-fake")

    ui = TokenScalingOverrides(n_ctx=2048, kv_quant="f16")
    result = ts.propose(model_path=str(model), requested_n_ctx=8192, explicit=ui)

    assert isinstance(result, TokenScalingProposal)
    assert result.n_ctx == 2048 and result.source["n_ctx"] == "user"
    assert result.kv_quant == "f16" and result.source["kv_quant"] == "user"
    assert ts.current_proposal() is result  # für Generierungs-Pfade registriert


def test_propose_never_failing_on_bogus_path(tmp_path) -> None:
    result = ts.propose(model_path=str(tmp_path / "missing.gguf"), requested_n_ctx=16384)
    assert isinstance(result, TokenScalingProposal)
    assert result.n_ctx >= 512
    assert result.kv_quant in ("f16", "q8_0")


def test_propose_is_reasoning_by_name(tmp_path) -> None:
    model = tmp_path / "Qwen3.8-27B-Q4_K_M.gguf"
    model.write_bytes(b"GGUF-fake")
    assert ts.detect_is_reasoning(str(model)) is True
    result = ts.propose(model_path=str(model), requested_n_ctx=16384)
    # Reasoning-Modell: Thinking-Budget > 0 (außer Fenster zu klein)
    if result.n_ctx > 2048:
        assert result.thinking_budget > 0
