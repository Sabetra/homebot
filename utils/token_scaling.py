"""
Token/Context-Skalierung — Auto-Check → Vorschlagswert → User-Override.
=========================================================================

Single Source of Truth für die ABLEITUNG der drei bewusst getrennten Größen:

    1. Kontextfenster  (n_ctx)         — hardware-limitiert (VRAM)
    2. Output-Budget   (max output)    — durch n_ctx begrenzt
    3. Thinking-Budget (reasoning)     — durch n_ctx − prompt − output begrenzt

Prinzip (SOTA 2026, Reasoning-Modelle wie Qwen3.x):
  - Die drei Größen werden GETRENNT gesteuert (keine einzelne feste Zahl).
  - Ein Auto-Check (VRAM-Pre-Check + GGUF-Metadaten) berechnet PRO HARDWARE
    und PRO MODELL einen Vorschlagswert (Sweet Spot).
  - Jeder Wert bleibt ein Regler: Auto-Default + ENV-Override (User-Override).
    Der User kann jeden Wert nach oben/unten setzen.
  - Invariante:  thinking_budget + output_budget ≤ n_ctx − prompt_reserve.
  - Thinking bleibt AKTIV — es wird nur gedeckelt (Budget/Effort), nie
    deaktiviert. (Nur expliziter User-Override ``BOT_REASONING_EFFORT=off``
    oder ein nicht-Reasoning-Modell schaltet es aus.)

NIE-FAILING (wie ``utils/gpu_devices.py``): Bei fehlender GPU / fehlenden
Metadaten werden konservative Defaults gewählt und gewarnt — statt die
App-Initialisierung zu brechen.

Bewusst ENT-KOPPELT von ``scripts/model_loader.py`` (schwerer Import,
``llama_cpp``): Der Kern ``compute_sweet_spot`` ist eine PURE Funktion —
100 % testbar ohne GPU und ohne Dateien. Nur die dünne ``auto_proposal``-
Schicht fragt ``utils.vram_monitor`` / Dateigröße / GGUF-Metadaten ab.

CLI:
    python -m utils.token_scaling --model <pfad.gguf> [--requested-n-ctx 16384]
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import struct
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

# Einheit für alle "gb"-Werte: GiB (1024**3) — konsistent mit
# ``tests/test_model_loader_vram_precheck.py`` (``int(6.5 * 1024**3)``).
GB = 1024 ** 3

# ── ENV-Overrides (User-Override; User gewinnt immer) ──────────────────────
# n_ctx nutzt das BESTEHende ``LLM_N_CTX`` (kein neues Erfinden). Die übrigen
# drei Regler sind neu. ``off`` wird pro Feld interpretiert.
ENV_N_CTX = "LLM_N_CTX"
ENV_KV_QUANT = "BOT_KV_QUANT"
ENV_MAX_OUTPUT_TOKENS = "BOT_MAX_OUTPUT_TOKENS"
ENV_THINKING_BUDGET = "BOT_THINKING_BUDGET"
ENV_REASONING_EFFORT = "BOT_REASONING_EFFORT"

# Erlaubte KV-Quantisierungen (llama.cpp ``type_k``/``type_v``). q8_0 halbiert
# den KV-Speicher mit <0,1 % Qualitätsverlust (SOTA 2026); q4_0-V-Cache vermeide.
_KV_QUANTS = ("f16", "q8_0")

# ── KV-Quant → ggml_type (``type_k``/``type_v`` für den Llama-Constructor) ──
# WICHTIG: ``type_k``/``type_v`` sind ``ggml_type``-Werte (GGML_TYPE_F16=1,
# GGML_TYPE_Q8_0=8) — NICHT die Nummerierung von ``llama_ftype``! Die
# Annotation ``Optional[llama_ftype]`` im installierten llama-cpp-python
# 0.3.35 (Llama-Constructor) ist irreführend: das Int wird unverändert an
# ``llama_model_params`` durchgereicht. Werte gegen das GGML_TYPE_*-Enum der
# installierten ``llama_cpp/llama_cpp.py`` abgeglichen (2026-09-01).
_KV_GGML_TYPES: Dict[str, int] = {
    "f16": 1,   # GGML_TYPE_F16 — sicherer Default (= llama.cpp-Standard-KV-Typ)
    "q8_0": 8,  # GGML_TYPE_Q8_0 — ~halber KV-Speicher, minimaler Verlust
}


def kv_type_pair(kv_quant: str) -> Optional[Tuple[int, int]]:
    """Mappe eine erlaubte KV-Quantisierung auf ``(type_k, type_v)``.

    Gibt ``None`` für ungestützte Quantisierung zurück — der Aufrufer
    übergibt dann keine ``type_k``/``type_v`` (llama.cpp-Default = f16).
    """
    t = _KV_GGML_TYPES.get(kv_quant)
    if t is None:
        return None
    return (t, t)


# ── Aktueller Vorschlag (Registry) ──────────────────────────────────────────
# Nach ``propose()`` (gerufen aus ``ModelLoader.load_model``) wird das
# aktuelle Ergebnis hier abgelegt. Die Generierungs-Pfade (Orchestrator,
# Chatbot-Logik, UI) lesen es, ohne das Objekt weiterreichen zu müssen.
# Nie-feilend und thread-sicher (Lock nur für die Zeiger-Zugriffe).
_CURRENT_PROPOSAL: Optional[TokenScalingProposal] = None
_CURRENT_PROPOSAL_LOCK = threading.Lock()


def set_current_proposal(proposal: Optional[TokenScalingProposal]) -> None:
    """Aktuellen Token-Skalierungs-Vorschlag setzen (oder ``None`` löschen)."""
    global _CURRENT_PROPOSAL
    with _CURRENT_PROPOSAL_LOCK:
        _CURRENT_PROPOSAL = proposal


def current_proposal() -> Optional[TokenScalingProposal]:
    """Letzte ``propose()``-Ergebnis (``None``, falls noch kein Modell)."""
    with _CURRENT_PROPOSAL_LOCK:
        return _CURRENT_PROPOSAL


def main_generation_max_tokens(
    fallback: int = 4096, current: Optional[int] = None
) -> int:
    """Obergrenze für ``max_tokens`` der HAUPT-ANTWORT.

    Bei Reasoning-Modellen gehören die Thinking-Tokens zur Completion (und
    werden erst nachher gestrippt) → die Obergrenze muss Thinking + Output
    abdecken. Die ``compute_sweet_spot``-Invariante garantiert bereits,
    dass das in ``n_ctx − prompt_reserve`` passt. Nicht-Reasoning-Modelle:
    Thinking=0 → reines Output-Budget.

    ``current`` (optional): bereits gesetzter Wert (z. B.
    ``settings["max_tokens"]`` / ``summarizer_max_tokens``). User-Einstellungen
    gewinnen: das Ergebnis ist ``max(current/fallback, Vorschlags-Budget)``.
    Ohne aktuellen Vorschlag (Modell noch nicht geladen): ``fallback``.
    """
    base = int(current) if current is not None else int(fallback)
    p = current_proposal()
    if p is not None and (p.output_budget > 0 or p.thinking_budget > 0):
        return max(base, int(p.thinking_budget) + int(p.output_budget))
    return max(128, base)


def model_architecture(model_path: str) -> Optional[str]:
    """``general.architecture`` aus den GGUF-Metadaten (``None`` bei Fehlern).

    Öffentliche Hülle über ``_arch_of`` (z. B. für die Einstellungen-Tab).
    """
    return _arch_of(model_path)

# Standard-Kandidaten für das Kontextfenster (absteigend, Potenzen von 2).
_N_CTX_CANDIDATES: Tuple[int, ...] = (65536, 32768, 16384, 8192, 4096, 2048)

# Konservativer Default für KV-Bytes/Token (f16, K+V), falls GGUF-Meta fehlt:
# entspricht ~40 Layer × 8 KV-Köpfe × 128 head_dim (typisch 8-14B-Klasse).
_DEFAULT_KV_BYTES_PER_TOKEN = 2 * 40 * 8 * 128 * 2

# ── reasoning_effort: Closed-Set PRO ARCHITEKTUR ─────────────────────────────
# reasoning_effort wird vom Chat-Template (Jinja) des MODELLS geparst — nicht
# von llama.cpp. Jede Modell-Familie hat ihre eigene Menge gültiger Werte;
# ein ungültiger Wert im Template = Fehler/undefiniertes Verhalten. Die Engine
# muss daher die Werte auf das Template-Set des konkreten Modells kürzen.
#
# Qwen3.8 (Architektur ``qwen35``): Template akzeptiert NUR
# ``xhigh``/``medium``/``low`` (verifiziert 2026-09). „Thinking aus" drückt man
# über ``enable_thinking=False`` / ``thinking_budget=0`` aus — NICHT über
# reasoning_effort. Andere Architekturen: großzügiges Default-Set (darf unten
# weiter eingegrenzt werden, sobald ein Template verifiziert ist).
_REASONING_EFFORT_DEFAULT = (
    "off", "minimal", "low", "medium", "high", "xhigh", "max", "default",
)
_REASONING_EFFORT_ALLOWED = {
    "qwen35": ("xhigh", "medium", "low"),  # Qwen3.8-Template (verifiziert)
    # Weitere restriktive Templates hier ergänzen (Key = general.architecture).
}


def allowed_reasoning_efforts(arch: Optional[str]) -> Tuple[str, ...]:
    """Closed-Set an reasoning_effort-Werten, die das Chat-Template akzeptiert.

    Unbekannte Architektur → großzügiges Default-Set (nie leer).
    """
    if arch:
        return _REASONING_EFFORT_ALLOWED.get(arch, _REASONING_EFFORT_DEFAULT)
    return _REASONING_EFFORT_DEFAULT


def _pick_effort(allowed: Tuple[str, ...], preferred: str) -> str:
    """Liefert ``preferred``, wenn er erlaubt ist; sonst einen erlaubten Wert.

    Deterministisch (erster Eintrag), damit Auto-Vorschläge reproduzierbar sind.
    """
    if preferred in allowed:
        return preferred
    return allowed[0] if allowed else preferred


# ── Ergebnis-Typ ────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class TokenScalingProposal:
    """Ein konkreter Vorschlag (Auto) oder das nach Override aufgelöste Ergebnis.

    ``source`` dokumentiert je Wert, ob er aus dem Auto-Check (``auto``),
    einem ENV-Override (``env``) oder einem expliziten UI-Override
    (``explicit``) stammt — wichtig für Log & UI-Badges.
    """

    n_ctx: int
    kv_quant: str
    output_budget: int
    thinking_budget: int
    reasoning_effort: str  # "medium" | "low" | "high" | "xhigh" | "off"
    source: Dict[str, str] = field(default_factory=dict)
    notes: Tuple[str, ...] = ()

    def describe(self) -> str:
        """Menschenlesbare Zusammenfassung (Log/CLI)."""
        src = lambda k: self.source.get(k, "?")  # noqa: E731
        lines = [
            f"  Kontextfenster  : {self.n_ctx:>7}  [{src('n_ctx')}]  ({self.kv_quant:>4} KV)",
            f"  Output-Budget   : {self.output_budget:>7}  [{src('output_budget')}] tokens",
            f"  Thinking-Budget : {self.thinking_budget:>7}  [{src('thinking_budget')}] tokens",
            f"  Reasoning-Effort: {self.reasoning_effort:>7}  [{src('reasoning_effort')}]",
        ]
        if self.notes:
            lines.append("  Hinweise:")
            lines.extend(f"    - {n}" for n in self.notes)
        return "\n".join(lines)


# ── UI-Overrides (explizit, z. B. aus der Sidebar; User gewinnt immer) ─────
@dataclass(frozen=True)
class TokenScalingOverrides:
    """Explizite User-Overrides (UI-Panel). ``None`` = „Auto" (kein Override).

    Präzedenz in ``resolve_proposal``: **UI-Override > ENV > Auto**.
    Felder, die ``None`` sind, behalten den Auto-/ENV-Wert.
    ``reasoning_effort="off"`` schaltet Thinking aus (thinking_budget=0).
    """

    n_ctx: Optional[int] = None
    kv_quant: Optional[str] = None
    output_budget: Optional[int] = None
    thinking_budget: Optional[int] = None
    reasoning_effort: Optional[str] = None


    def to_raw(self) -> Dict[str, str]:
        """Nicht-leere Felder als normalisierte Raw-Strings (für ``_apply_source``).

        Nie-failing: nicht-integerbare Zahlen (z. B. aus handgeschriebenen
        Persistenz-JSONs) werden übersprungen — ``_apply_source`` würfe sonst.
        """
        raw: Dict[str, str] = {}
        for key, value in (
            ("n_ctx", self.n_ctx),
            ("output_budget", self.output_budget),
            ("thinking_budget", self.thinking_budget),
        ):
            if value is None:
                continue
            try:
                raw[key] = str(int(value))
            except (TypeError, ValueError):
                continue
        if self.kv_quant:
            raw["kv_quant"] = str(self.kv_quant).strip().lower()
        if self.reasoning_effort:
            raw["reasoning_effort"] = str(self.reasoning_effort).strip().lower()
        return raw

    @property
    def is_empty(self) -> bool:
        """True, wenn kein Feld gesetzt ist (kein Override)."""
        return not self.to_raw()

    def to_dict(self) -> Dict[str, Optional[Any]]:
        """JSON-serialisierbar (Persistenz, ``None`` = Auto)."""
        return {
            "n_ctx": self.n_ctx,
            "kv_quant": self.kv_quant,
            "output_budget": self.output_budget,
            "thinking_budget": self.thinking_budget,
            "reasoning_effort": self.reasoning_effort,
        }


    @classmethod
    def from_dict(cls, data: Dict[str, Optional[Any]]) -> "TokenScalingOverrides":
        """Rekonstruiert aus Persistenz-JSON (toleriert fehlende/ungültige Felder)."""
        d = data if isinstance(data, dict) else {}

        def _int(key: str) -> Optional[int]:
            v = d.get(key)
            if v is None:
                return None
            try:
                return int(v)
            except (TypeError, ValueError):
                return None

        def _str(key: str) -> Optional[str]:
            v = d.get(key)
            if v is None:
                return None
            s = str(v).strip()
            return s or None

        kv = _str("kv_quant")
        if kv is not None:
            kv = kv.lower()
            if kv not in _KV_QUANTS:
                kv = None  # fremde/ungültige Quantisierung → Auto bleibt

        return cls(
            n_ctx=_int("n_ctx"),
            kv_quant=kv,
            output_budget=_int("output_budget"),
            thinking_budget=_int("thinking_budget"),
            reasoning_effort=_str("reasoning_effort"),
        )


def overrides_from_values(
    auto: "TokenScalingProposal",
    *,
    n_ctx: Optional[int] = None,
    kv_quant: Optional[str] = None,
    output_budget: Optional[int] = None,
    thinking_budget: Optional[int] = None,
    reasoning_effort: Optional[str] = None,
) -> Optional["TokenScalingOverrides"]:
    """Vergleicht UI-Werte mit dem Auto-Vorschlag → Override-Objekt.

    Werte, die mit dem Auto-Vorschlag identisch sind, werden NICHT als
    Override gespeichert (bleiben „Auto"). ``None`` = „Auto" (Widget-Default).
    Ungültige Werte (nicht-integerbare Zahlen, fremde KV-Quanten) werden
    ignoriert → Auto. Gibt ``None`` zurück, wenn alles Auto ist.
    """
    def _differs_int(value: Any, auto_value: int) -> bool:
        try:
            return value is not None and int(value) != int(auto_value)
        except (TypeError, ValueError):
            return False  # ungültig → kein Override (Auto bleibt)

    kv = str(kv_quant).strip().lower() if kv_quant else ""
    eff = str(reasoning_effort).strip().lower() if reasoning_effort else ""
    ov = TokenScalingOverrides(
        n_ctx=n_ctx if _differs_int(n_ctx, auto.n_ctx) else None,
        kv_quant=(kv if (kv in _KV_QUANTS and kv != auto.kv_quant) else None),
        output_budget=(
            output_budget if _differs_int(output_budget, auto.output_budget) else None
        ),
        thinking_budget=(
            thinking_budget
            if _differs_int(thinking_budget, auto.thinking_budget)
            else None
        ),
        reasoning_effort=(eff if (eff and eff != auto.reasoning_effort) else None),
    )
    return ov if not ov.is_empty else None


def _derive_budgets(
    n_ctx: int, is_reasoning: bool, prompt_reserve: int = 2048
) -> Tuple[int, int, Optional[str]]:
    """Leitet Output-/Thinking-Budget aus dem Kontextfenster ab (PURE).

    Gleiche Regeln wie der Sweet-Spot: Reasoning → Thinking ≤30 %/≤8192 und
    ≤Fenster/2, Output ≤50 %/≤16384; Non-Reasoning → Output ≤40 %/≤8192.
    Invariante ``thinking + output ≤ n_ctx − prompt_reserve`` wird hart
    erzwungen (Floor 0: ehrliches 0/0 statt stiller Overflow). Rückgabe
    ``(thinking, output, note)`` — ``note`` nur, wenn Skalierung greift.
    """
    available = max(0, int(n_ctx) - int(prompt_reserve))
    if is_reasoning:
        thinking_budget = int(min(n_ctx * 0.30, 8192, available // 2))
        output_budget = int(min(n_ctx * 0.50, 16384, available - thinking_budget))
    else:
        thinking_budget = 0
        output_budget = int(min(n_ctx * 0.40, 8192, available))
    note: Optional[str] = None
    if thinking_budget + output_budget > available:
        scale = available / max(1, (thinking_budget + output_budget))
        thinking_budget = int(thinking_budget * scale)
        output_budget = min(output_budget, max(0, available - thinking_budget))
        note = "Invariante thinking+output≤Fenster−Reserve durch Skalierung erzwungen."
    return max(0, thinking_budget), max(0, output_budget), note


def compute_sweet_spot(
    *,
    vram_ceiling_gb: float,
    weights_gb: float,
    kv_bytes_per_token: int,
    requested_n_ctx: int,
    is_reasoning: bool,
    allowed_efforts: Tuple[str, ...] = _REASONING_EFFORT_DEFAULT,
    prompt_reserve: int = 2048,
    activation_reserve_gb: float = 1.0,
    fixed_overhead_gb: float = 0.0,
    safety_ratio: float = 0.88,
    n_ctx_candidates: Tuple[int, ...] = _N_CTX_CANDIDATES,
) -> TokenScalingProposal:
    """Berechnet den Sweet Spot für eine gegebene Hardware + ein gegebenes Modell.

    Findet das größte ``n_ctx ≤ requested_n_ctx``, das
    ``weights + KV(n_ctx) + Aktivierung + Fix-Overhead`` in
    ``vram_ceiling * safety_ratio`` hineinpasst. ``fixed_overhead_gb`` deckt
    n_ctx-UNabhängige Modell-Komponenten ab (z.B. der feste SSM-Zustand bei
    Hybrid-Modellen wie Qwen3-Next/Qwen3.8). Bevorzugt f16-KV; fällt auf
    q8_0 zurück, wenn f16 nicht passt. Leitet danach Output- und
    Thinking-Budget ab (Reasoning-Ratio)
    und garantiert die Invariante ``thinking + output ≤ n_ctx − prompt_reserve``.

    Alle Parameter sind explizit (PURE) → deterministisch und unit-testbar.
    """
    if vram_ceiling_gb < 0:
        vram_ceiling_gb = 0.0
    if weights_gb < 0:
        weights_gb = 0.0
    kv_bytes_per_token = max(0, int(kv_bytes_per_token))
    requested_n_ctx = max(512, int(requested_n_ctx))
    fixed_overhead_gb = max(0.0, float(fixed_overhead_gb))

    budget_vram_gb = vram_ceiling_gb * safety_ratio
    # Fix-Overhead (n_ctx-unabhängig, z.B. SSM-Zustand) wird wie die
    # Aktivierungs-Reserve vorab vom KV-Budget abgezogen.
    kv_budget_gb = max(
        0.0, budget_vram_gb - weights_gb - activation_reserve_gb - fixed_overhead_gb
    )

    notes: list = []
    chosen_n_ctx: Optional[int] = None
    chosen_kv: Optional[str] = None

    cands = sorted((c for c in n_ctx_candidates if c <= requested_n_ctx), reverse=True)
    if not cands:
        cands = [requested_n_ctx]

    def _kv_gb(n_ctx: int, quant: str) -> float:
        bpt = kv_bytes_per_token if quant == "f16" else kv_bytes_per_token * 0.5
        return (n_ctx * bpt) / GB

    for n_ctx in cands:
        if kv_budget_gb >= _kv_gb(n_ctx, "f16"):
            chosen_n_ctx, chosen_kv = n_ctx, "f16"
            break
        if kv_budget_gb >= _kv_gb(n_ctx, "q8_0"):
            chosen_n_ctx, chosen_kv = n_ctx, "q8_0"
            notes.append(
                f"KV f16 für {n_ctx} passt nicht ({_kv_gb(n_ctx, 'f16'):.2f} GB > "
                f"{kv_budget_gb:.2f} GB) → q8_0 gewählt"
            )
            break
        notes.append(
            f"n_ctx={n_ctx} passt nicht (KV f16 {_kv_gb(n_ctx, 'f16'):.2f} GB, "
            f"q8_0 {_kv_gb(n_ctx, 'q8_0'):.2f} GB > Budget {kv_budget_gb:.2f} GB)"
        )

    if chosen_n_ctx is None:
        chosen_n_ctx = min(cands)
        chosen_kv = "q8_0"
        notes.append(
            "Selbst das kleinste n_ctx überschreitet das VRAM-Budget — "
            "konservativster Wert gewählt (OOM-Fallback im Loader bleibt Safety-Net)."
        )

    # ── Budget-Ableitung (Output + Thinking) ────────────────────────────────
    # Invariante (hart): thinking + output ≤ n_ctx − prompt_reserve.
    # Floor = 0 (nicht 512!): Ein zu kleines Fenster darf den Kontext NICHT
    # übercommiten — dann ist die ehrliche Antwort 0/0 + Hinweis, nicht ein
    # stiller Overflow. OOM-Fallback im Loader bleibt das echte Safety-Net.
    available = max(0, chosen_n_ctx - prompt_reserve)
    if available == 0:
        notes.append(
            f"Kontextfenster {chosen_n_ctx} ≤ prompt_reserve ({prompt_reserve}): "
            "Kein Spielraum für Thinking/Output — VRAM/n_ctx erhöhen empfohlen."
        )
    if is_reasoning:
        # SOTA: xhigh→medium dämpft Overthinking, Thinking bleibt an. Der Wert
        # muss zum Closed-Set des Chat-Templates passen (z.B. Qwen3.8=qwen35
        # akzeptiert nur xhigh/medium/low) → per Architektur kürzen.
        reasoning_effort = _pick_effort(allowed_efforts, "medium")
        notes.append(
            "Reasoning-Modell: Thinking (≤30 %/≤8192) + Output (≤50 %/≤16384) "
            f"teilen sich das Fenster; Effort={reasoning_effort} dämpft Overthinking."
        )
    else:
        reasoning_effort = "off"
    thinking_budget, output_budget, inv_note = _derive_budgets(
        chosen_n_ctx, is_reasoning, prompt_reserve
    )
    if inv_note:
        notes.append(inv_note)

    source = {
        "n_ctx": "auto", "kv_quant": "auto", "output_budget": "auto",
        "thinking_budget": "auto", "reasoning_effort": "auto",
    }
    return TokenScalingProposal(
        n_ctx=chosen_n_ctx,
        kv_quant=chosen_kv,
        output_budget=max(0, output_budget),
        thinking_budget=max(0, thinking_budget),
        reasoning_effort=reasoning_effort,
        source=source,
        notes=tuple(notes),
    )


# ── User-Override (User gewinnt immer) ─────────────────────────────────────
def _default_env() -> Dict[str, Optional[str]]:
    return {
        ENV_N_CTX: _env(ENV_N_CTX),
        ENV_KV_QUANT: _env(ENV_KV_QUANT),
        ENV_MAX_OUTPUT_TOKENS: _env(ENV_MAX_OUTPUT_TOKENS),
        ENV_THINKING_BUDGET: _env(ENV_THINKING_BUDGET),
        ENV_REASONING_EFFORT: _env(ENV_REASONING_EFFORT),
    }


def _env_to_raw(env: Dict[str, Optional[str]]) -> Dict[str, str]:
    """ENV-Dict → normalisierte Override-Werte (leer, wenn nichts gesetzt)."""
    raw: Dict[str, str] = {}
    for key, field in (
        (ENV_N_CTX, "n_ctx"),
        (ENV_KV_QUANT, "kv_quant"),
        (ENV_MAX_OUTPUT_TOKENS, "output_budget"),
        (ENV_THINKING_BUDGET, "thinking_budget"),
        (ENV_REASONING_EFFORT, "reasoning_effort"),
    ):
        v = env.get(key)
        if v:
            raw[field] = str(v).strip()
    return raw


def _apply_source(
    new: Dict[str, Any],
    src: Dict[str, str],
    notes: list,
    values: Dict[str, str],
    label: str,
    allowed_efforts: Tuple[str, ...],
) -> None:
    """Wendet EINE Override-Quelle (``env``/``user``) in-place auf ``new`` an.

    ``values``: normalisiertes Feld→Wert (s. ``TokenScalingOverrides.to_raw``);
    spätere Aufrufe gewinnen (Wert UND Quell-Label). Ungültige Werte werden
    übersprungen und als Hinweis protokolliert (Auto-Wert bleibt).
    """
    if "n_ctx" in values:
        try:
            new["n_ctx"] = max(512, int(values["n_ctx"]))
            src["n_ctx"] = label
        except (TypeError, ValueError):
            notes.append(f"{label} n_ctx={values['n_ctx']!r} ungültig → Auto-Wert bleibt.")
    if "kv_quant" in values:
        kv = str(values["kv_quant"]).strip().lower()
        if kv in _KV_QUANTS:
            new["kv_quant"] = kv
            src["kv_quant"] = label
        else:
            notes.append(f"{label} kv_quant={kv!r} ungültig (erlaubt: {_KV_QUANTS}) → Auto bleibt.")
    if "output_budget" in values:
        try:
            new["output_budget"] = max(0, int(values["output_budget"]))
            src["output_budget"] = label
        except (TypeError, ValueError):
            notes.append(f"{label} output_budget={values['output_budget']!r} ungültig → Auto bleibt.")
    if "thinking_budget" in values:
        try:
            new["thinking_budget"] = max(0, int(values["thinking_budget"]))
            src["thinking_budget"] = label
        except (TypeError, ValueError):
            notes.append(f"{label} thinking_budget={values['thinking_budget']!r} ungültig → Auto bleibt.")
    if "reasoning_effort" in values:
        eff = str(values["reasoning_effort"]).strip().lower()
        if eff == "off":
            new["reasoning_effort"] = "off"
            new["thinking_budget"] = 0
            src["reasoning_effort"] = label
        elif eff in allowed_efforts:
            new["reasoning_effort"] = eff
            src["reasoning_effort"] = label
        else:
            notes.append(
                f"{label} reasoning_effort={eff!r} nicht vom Template erlaubt "
                f"(erlaubt: {'|'.join(allowed_efforts)} | off) → Auto bleibt."
            )


def resolve_proposal(
    proposal: TokenScalingProposal,
    env: Optional[Dict[str, Optional[str]]] = None,
    allowed_efforts: Tuple[str, ...] = _REASONING_EFFORT_DEFAULT,
    explicit: Optional[TokenScalingOverrides] = None,
) -> TokenScalingProposal:
    """Wendet Overrides auf einen Vorschlag an. Präzedenz: UI (``explicit``) > ENV > Auto.

    ``env`` ist injizierbar (Testbarkeit); Default = ``os.environ``.
    ``explicit`` ist das UI-Override (``TokenScalingOverrides``); schlägt ENV.
    ``allowed_efforts`` ist das Closed-Set des Chat-Templates — reasoning_effort
    wird darauf validiert (kein ungültiger Wert kommt ins Template).
    Die Invariante ``thinking + output ≤ n_ctx − prompt_reserve`` wird nach
    dem Override erneut erzwungen (User kann nicht das Fenster sprengen).
    """
    e = env if env is not None else _default_env()

    new = {
        "n_ctx": proposal.n_ctx,
        "kv_quant": proposal.kv_quant,
        "output_budget": proposal.output_budget,
        "thinking_budget": proposal.thinking_budget,
        "reasoning_effort": proposal.reasoning_effort,
    }
    src = dict(proposal.source)
    notes = list(proposal.notes)

    # ENV-Overrides (Quelle „env")
    _apply_source(new, src, notes, _env_to_raw(e), "env", allowed_efforts)

    # UI-Overrides (Quelle „user") — Präzedenz über ENV.
    if explicit is not None:
        _apply_source(new, src, notes, explicit.to_raw(), "user", allowed_efforts)

    # Invariante erneut erzwingen (User-Override darf Fenster nicht sprengen).
    available = max(0, new["n_ctx"] - 2048)
    if available > 0 and new["thinking_budget"] + new["output_budget"] > available:
        scale = available / max(1, (new["thinking_budget"] + new["output_budget"]))
        new["thinking_budget"] = int(new["thinking_budget"] * scale)
        new["output_budget"] = min(new["output_budget"], max(0, available - new["thinking_budget"]))
        notes.append("Invariante nach User-Override erneut erzwungen.")

    return TokenScalingProposal(
        n_ctx=new["n_ctx"],
        kv_quant=new["kv_quant"],
        output_budget=new["output_budget"],
        thinking_budget=new["thinking_budget"],
        reasoning_effort=new["reasoning_effort"],
        source=src,
        notes=tuple(notes),
    )


# ── Persistenz der UI-Overrides (pro Modell, außerhalb des Repos) ────────────
def overrides_path() -> Path:
    """Persistenzpfad der UI-Overrides (stabil, außerhalb des Repos).

    Für Tests per ``HOMEBOT_TOKEN_SCALING_OVERRIDES`` umleiten.
    """
    env_path = os.environ.get("HOMEBOT_TOKEN_SCALING_OVERRIDES")
    if env_path:
        return Path(env_path).expanduser()
    return Path.home() / ".cache" / "homebot" / "token_scaling_overrides.json"


def load_overrides(model_name: str) -> TokenScalingOverrides:
    """Gespeicherte UI-Overrides eines Modells lesen (``None``-Felder = Auto).

    Wirft nie (fehlende/korrupte Datei = leere Overrides); ungültige
    Felder werden toleriert (``TokenScalingOverrides.from_dict``).
    """
    try:
        data = json.loads(overrides_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return TokenScalingOverrides()
    entry = data.get(model_name) if isinstance(data, dict) else None
    return TokenScalingOverrides.from_dict(entry if isinstance(entry, dict) else {})


def save_overrides(model_name: str, overrides: TokenScalingOverrides) -> None:
    """UI-Overrides eines Modells atomar persistieren (Temp-Datei + ``replace``).

    Dateiformat: flaches JSON ``{<Modell-Key>: {<Feld>: "<Wert>"}}`` — nur
    gesetzte Felder als Strings (``TokenScalingOverrides.to_raw``).
    Leere Overrides (alles Auto) entfernen den Modell-Eintrag aus der Datei.
    """
    path = overrides_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                data = {}
        else:
            data = {}
        if overrides.is_empty:
            data.pop(model_name, None)
        else:
            data[model_name] = overrides.to_raw()
        tmp = path.with_name(path.name + f".tmp-{os.getpid()}")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)
    except (OSError, ValueError) as e:
        # Kein harter Fehler im UI-Pfad: Overrides gelten in dieser Sitzung.
        logging.getLogger(__name__).warning("Token-Scale-Overrides nicht gespeichert: %s", e)


def clear_overrides(model_name: str) -> None:
    """Gespeicherte Overrides eines Modells löschen (``"__all__"`` = ganze Datei)."""
    path = overrides_path()
    try:
        if model_name == "__all__":
            if path.exists():
                path.unlink()
            return
        if not path.exists():
            return
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and data.pop(model_name, None) is not None:
            tmp = path.with_name(path.name + f".tmp-{os.getpid()}")
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp, path)
    except (OSError, ValueError) as e:
        logging.getLogger(__name__).warning("Token-Scale-Overrides nicht gelöscht: %s", e)


# GGUF-Scalar-Format-Tabelle (gguf.h, Typen 0–12). Für _read_kv_meta.
_GGUF_SCALAR_FORMATS = {
    0: "<B", 1: "<b", 2: "<H", 3: "<h", 4: "<I", 5: "<i", 6: "<f",
    10: "<Q", 11: "<q", 12: "<d",
}


def _read_meta(model_path: str) -> Optional[Dict[str, Any]]:
    """Liest die (numerischen) GGUF-Metadaten aus dem Header — FOKUSSIERTE
    Variante, die nur die Architektur-Keys für die KV-/SSM-Schätzung holt.

    Spiegelt ``scripts/model_loader._read_gguf_metadata`` (Single Source of
    Truth bleibt die GGUF-Datei), ist aber bewusst klein und entkoppelt,
    damit ``token_scaling`` nicht den schweren ``model_loader`` importiert.
    Arrays/Tokenizer-Werte werden nur zum Byte-Sync übersprungen (nicht
    gespeichert). Gibt None bei Fehler → Aufrufer nutzt
    ``_DEFAULT_KV_BYTES_PER_TOKEN``.
    """
    meta: Dict[str, Any] = {}
    try:
        with open(model_path, "rb") as fh:
            head = fh.read(24)
            if len(head) < 24 or head[:4] != b"GGUF":
                return None
            (version,) = struct.unpack_from("<I", head, 4)
            if version != 3:
                return None
            (kv_count,) = struct.unpack_from("<Q", head, 16)
            if kv_count > 100_000:
                return None

            def _read_value(vt: int) -> Any:
                if vt == 7:  # bool
                    b = fh.read(1)
                    return (b[0] != 0) if len(b) == 1 else False
                if vt == 8:  # string
                    n_b = fh.read(8)
                    if len(n_b) < 8:
                        raise EOFError
                    (n,) = struct.unpack("<Q", n_b)
                    if n > 100_000_000:
                        raise ValueError
                    return fh.read(n)
                if vt in _GGUF_SCALAR_FORMATS:
                    fmt = _GGUF_SCALAR_FORMATS[vt]
                    raw = fh.read(struct.calcsize(fmt))
                    if len(raw) < struct.calcsize(fmt):
                        raise EOFError
                    return struct.unpack(fmt, raw)[0]
                if vt == 9:  # array
                    et_b = fh.read(4)
                    if len(et_b) < 4:
                        raise EOFError
                    (et,) = struct.unpack("<I", et_b)
                    n_b = fh.read(8)
                    if len(n_b) < 8:
                        raise EOFError
                    (c,) = struct.unpack("<Q", n_b)
                    if c > 10_000_000:
                        raise ValueError
                    if et in _GGUF_SCALAR_FORMATS:
                        # Skalar-Array (feste Elementgröße): ein einziger seek reicht.
                        fh.seek(c * struct.calcsize(_GGUF_SCALAR_FORMATS[et]), 1)
                    else:
                        for _i in range(c):
                            _read_value(et)
                    return None
                raise ValueError(f"unbekannter GGUF-Typ {vt}")

            arch: Optional[str] = None
            for _ in range(int(kv_count)):
                k_b = fh.read(8)
                if len(k_b) < 8:
                    break
                (klen,) = struct.unpack("<Q", k_b)
                if klen > 1_000_000:
                    raise ValueError
                key = fh.read(klen).decode("utf-8", errors="replace")
                # WICHTIG: Value-Type ist u32 (4 Bytes), NICHT u8 — identisch zu
                # scripts/model_loader._read_gguf_metadata (fh.read(4)/<I). Ein
                # u8-Read verschiebt den Stream um 3 Bytes und bricht das Parsing.
                vt_b = fh.read(4)
                if len(vt_b) < 4:
                    break
                (vt,) = struct.unpack("<I", vt_b)
                val = _read_value(vt)
                if key == "general.architecture" and isinstance(val, bytes):
                    # Architektur ist ein String-Value — explizit dekodieren und
                    # behalten (braucht _extract_shape_from_meta als Namespace).
                    arch = val.decode("utf-8", errors="replace")
                    meta["general.architecture"] = arch
                    continue
                # FOKUS: nur numerische Architektur-Keys sammeln (Shape-Info).
                # Strings/Arrays (z.B. 250k Tokenizer-Strings) bleiben aussen vor.
                if arch is None or not isinstance(val, (int, float)) or isinstance(val, bool):
                    continue
                if key.startswith(arch + ".") and val > 0:
                    meta[key] = int(val)
                # Abbruch bei Tokenizer-Sektion: alle relevanten Keys
                # (general.*, <arch>.* — inkl. Hybrid-/SSM-Keys) liegen
                # konventionsgemäß davor; die riesigen Tokenizer-Arrays
                # (250k+ Strings) werden NICHT gelesen.
                # Kein früherer Shape-Abbruch: Hybrid-Keys wie
                # full_attention_interval / ssm.* können erst NACH den
                # Shape-Keys kommen — die komplette Arch-Sektion muss rein.
                if key.startswith("tokenizer."):
                    break
            if arch is None:
                return None
    except (OSError, EOFError, ValueError, struct.error):
        return None

    return meta


def _read_kv_meta(model_path: str) -> Optional[Tuple[int, int, int]]:
    """Kompatibler Zugriff: (n_layer, n_head_kv, head_dim) aus den Metadaten.

    None bei Lese-Fehler → Aufrufer nutzt ``_DEFAULT_KV_BYTES_PER_TOKEN``.
    """
    meta = _read_meta(model_path)
    return _extract_shape_from_meta(meta) if meta else None


# Architektur-relative Key-Konventionen (Suffixe hinter `<arch>.`) für die
# KV-Shape-Extraktion. Moderne Namen (qwen35/gemma4) zuerst, dann Legacy-Formen
# (llama2). Spiegelt scripts/model_loader._extract_gguf_shape + Qwen3-Next-
# Ergänzungen (key_length/value_length).
_KV_LAYER_KEYS = ("block_count", "n_layer", "n_layers")
_KV_HEAD_KV_KEYS = ("attention.head_count_kv", "n_head_kv", "attention.head_count_k")
_KV_HEAD_KEYS = ("attention.head_count", "n_head")
# key_length/value_length: Qwen3-Next-/Qwen3.8-Konvention — head_size fehlt dort,
# die wahre KV-Head-Dimension steht in attention.key_length (z.B. 256, NICHT
# n_embd//n_head=213).
_KV_HEAD_DIM_KEYS = (
    "attention.head_size", "head_size", "attention.key_length", "attention.value_length",
)


def _kv_num(meta: Dict[str, Any], arch: str, suffixes: Tuple[str, ...]) -> Optional[int]:
    for suffix in suffixes:
        value = meta.get(arch + "." + suffix)
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)) and value > 0:
            return int(value)
    return None


def _extract_shape_from_meta(meta: Dict[str, Any]) -> Optional[Tuple[int, int, int]]:
    """Extrahiert (n_layer, n_head_kv, head_dim) aus GGUF-Metadaten.

    None, wenn ein Wert fehlt → Aufrufer nutzt die Fallback-Schätzung.
    """
    arch = meta.get("general.architecture")
    if not isinstance(arch, str) or not arch:
        return None

    n_layer = _kv_num(meta, arch, _KV_LAYER_KEYS)
    n_head = _kv_num(meta, arch, _KV_HEAD_KEYS)
    n_head_kv = _kv_num(meta, arch, _KV_HEAD_KV_KEYS) or n_head
    head_dim = _kv_num(meta, arch, _KV_HEAD_DIM_KEYS)
    if head_dim is None:
        n_embd = _kv_num(meta, arch, ("embedding_length", "n_embd"))
        if n_embd is not None and n_head:
            head_dim = n_embd // n_head
    if not (n_layer and n_head_kv and head_dim):
        return None
    return (n_layer, n_head_kv, head_dim)


# ── Hybrid-SSM-Erkennung (Qwen3-Next/qwen35 & co.) ─────────────────────────
# Manche Architekturen sind Hybride aus linearer Attention (SSM/Delta-Net)
# und klassischer Voll-Attention: Nur jedes k-te Layer (full_attention_interval)
# führt einen n_ctx-skalierten KV-Cache; die übrigen Layer halten einen FESTEN,
# n_ctx-unabhängigen SSM-Zustand. Qwen3.8-27B (qwen35): 65 Layer,
# interval 4 → 16 KV-Layer + 49 SSM-Layer.
#
# Formeln = llama.cpp src/models/qwen3next.cpp (2026-09 verifiziert):
#   is_recr[i] = (i + 1) % full_attn_interval != 0   →  n_full = n_layer // k
#   key_dim    = ssm.state_size * ssm.group_count
#   value_dim  = ssm.state_size * ssm.time_step_rank
#   pro SSM-Layer: (key_dim + value_dim) * state  (rekurrenter Zustand)
#                 + (conv_kernel - 1) * (2*key_dim + value_dim)  (Conv-State)
#
# Sicherheitsfaktor: Ring-Slots/Rollback-Zeilen (llama-memory-recurrent:
# n_rows = mem_size * (1 + n_rs_seq)) und f16-vs-f32-State-Dtype — beide
# wirken in dieselbe Richtung (mehr Speicher); 2× deckt den realistischen
# Worst-Case ab. Fehlt ein Hybrid-Key → kein Hybrid-Kredit (konservativ).
_SSM_SAFETY_FACTOR = 2
_KV_FULL_ATTN_INTERVAL_KEYS = ("full_attention_interval",)
_KV_NEXTN_KEYS = ("nextn_predict_layers", "n_nextn")
_SSM_STATE_KEYS = ("ssm.state_size",)
_SSM_GROUP_KEYS = ("ssm.group_count",)
_SSM_DT_RANK_KEYS = ("ssm.time_step_rank",)
_SSM_CONV_KEYS = ("ssm.conv_kernel",)


def _kv_geometry_from_meta(meta: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """KV-Cache-Geometrie inkl. Hybrid-SSM-Trennung aus GGUF-Metadaten.

    Liefert (None, wenn die Basis-Shape fehlt):
      n_layer / n_kv_layers / n_ssm_layers / n_head_kv / head_dim,
      kv_bytes_per_token  — n_ctx-skaliert (K+V, f16)
      ssm_fixed_bytes     — n_ctx-UNabhängig (SSM-Zustand, 0 bei Voll-Attention)

    Bewusst konservative Richtung: Unvollständige Hybrid-Metadaten bedeuten
    „alle Layer als KV, kein SSM-Kredit" (Alt-Verhalten), nie das Gegenteil.
    """
    arch = meta.get("general.architecture")
    if not isinstance(arch, str) or not arch:
        return None
    shape = _extract_shape_from_meta(meta)
    if shape is None:
        return None
    n_layer, n_head_kv, head_dim = shape

    interval = _kv_num(meta, arch, _KV_FULL_ATTN_INTERVAL_KEYS)
    nextn = _kv_num(meta, arch, _KV_NEXTN_KEYS) or 0
    state = _kv_num(meta, arch, _SSM_STATE_KEYS)
    n_groups = _kv_num(meta, arch, _SSM_GROUP_KEYS)
    dt_rank = _kv_num(meta, arch, _SSM_DT_RANK_KEYS)

    if interval and interval > 1 and state and n_groups and dt_rank:
        # Verifizierte Hybrid-Geometrie (Qwen3-Next-Konvention).
        n_full = n_layer // interval
        n_ssm = n_layer - n_full
        conv_k = _kv_num(meta, arch, _SSM_CONV_KEYS) or 1
        key_dim = state * n_groups
        val_dim = state * dt_rank
        per_layer = (key_dim + val_dim) * state + max(0, conv_k - 1) * (2 * key_dim + val_dim)
        ssm_fixed_bytes = n_ssm * per_layer * 4 * _SSM_SAFETY_FACTOR
    else:
        # Kein/nicht-verifiziertes Hybrid: alle Layer als KV (konservativ).
        n_full = n_layer
        n_ssm = 0
        ssm_fixed_bytes = 0

    n_kv_layers = n_full + nextn
    # K+V (2) × f16 (2) pro KV-Head-Dimension.
    kv_bytes_per_token = 2 * n_kv_layers * n_head_kv * head_dim * 2
    return {
        "n_layer": n_layer,
        "n_kv_layers": n_kv_layers,
        "n_ssm_layers": n_ssm,
        "n_head_kv": n_head_kv,
        "head_dim": head_dim,
        "kv_bytes_per_token": kv_bytes_per_token,
        "ssm_fixed_bytes": ssm_fixed_bytes,
    }


# ── ENV-Helfer ──────────────────────────────────────────────────────────────
def _env(name: str) -> Optional[str]:
    """Rückgabe des ENV-Werts (stripped) oder None, wenn leer/ungesetzt."""
    raw = os.environ.get(name)
    if raw is None:
        return None
    raw = raw.strip()
    return raw or None


def detect_is_reasoning(model_path: str) -> bool:
    """Heuristik: Ist das Modell ein Reasoning-Modell? (Name basiert.)

    Bewusst konservativ (Default False) — sonst droht ein Thinking-Budget bei
    Modellen, die gar kein Thinking haben. Neue Familien hier ergänzen.
    """
    name = os.path.basename(str(model_path)).lower()
    markers = ("qwen3", "magistral", "deepseek-r1", "qwq", "thinking")
    return any(m in name for m in markers)


def _arch_of(model_path: str) -> Optional[str]:
    """GGUF-Architektur (``general.architecture``) oder None bei Lese-Fehler.

    Basis für das Closed-Set ``allowed_reasoning_efforts`` — das Template, das
    reasoning_effort parst, hängt von der Architektur ab (z.B. ``qwen35``).
    """
    meta = _read_meta(model_path)
    if not meta:
        return None
    arch = meta.get("general.architecture")
    return arch if isinstance(arch, str) and arch else None



def _llm_gpu_vram() -> Optional[Tuple[float, float]]:
    """VRAM der LLM-GPU als ``(total_gb, free_gb)`` via ``utils.vram_monitor``.

    None bei Fehler. ``total_gb`` = Kapazität (Obergrenze), ``free_gb`` =
    momentaner freier Anteil (für Co-Tenant-Warnung). Dieselbe Quelle wie der
    VRAM-Pre-Check in ``scripts/model_loader.py``. Single-GPU-Systeme tragen
    die zusammengesetzte Rolle "LLM+AUX" und werden ebenfalls erkannt.
    """
    try:
        import utils.vram_monitor as vm
        snaps = vm.get_all_gpu_snapshots() or []
        snap = next((s for s in snaps if s.get("role") in ("LLM", "LLM+AUX")), None)
        if snap is None and snaps:
            snap = snaps[0]
        if snap is not None:
            return (
                float(snap.get("total_gb", 0.0)),
                float(snap.get("free_gb", 0.0)),
            )
    except Exception as exc:  # nie-failing: Auto-Detect darf nicht brechen
        logger.warning("token_scaling: VRAM-Query fehlgeschlagen: %s", exc)
    return None


# ── Auto-Detection (dünne Schicht: Hardware/Dateien → PURE-Kern) ────────────
def auto_proposal(
    model_path: str,
    requested_n_ctx: int = 16384,
    is_reasoning: Optional[bool] = None,
    mmproj_path: Optional[str] = None,
    allowed_efforts: Optional[Tuple[str, ...]] = None,
) -> TokenScalingProposal:
    """AUTO-CHECK: berechnet einen Vorschlag für diese Hardware + dieses Modell.

    Inputs (alles nie-failing):
      - VRAM frei (LLM-GPU) via ``utils.vram_monitor``
      - Gewichtsgröße = Modelldatei (+ optional mmproj)
      - KV-Geometrie aus GGUF-Metadaten (Default bei Lese-Fehler)
    Ergebnis ist ein *Vorschlag* — ``resolve_proposal``/``propose`` wendet
    danach die User-Overrides an (User gewinnt).
    """
    notes: list = []

    # Gewichtsgröße (GB)
    weights_gb = 0.0
    try:
        weights_gb = os.path.getsize(model_path) / GB
    except OSError as exc:
        logger.warning("token_scaling: Modellgröße nicht lesbar: %s", exc)
    if mmproj_path:
        try:
            weights_gb += os.path.getsize(mmproj_path) / GB
        except OSError:
            pass

    # KV-Geometrie (Single Source of Truth: GGUF-Metadaten), inkl.
    # Hybrid-SSM-Trennung (Voll-Attention-KV vs. fester SSM-Zustand).
    # Meta wird einmal gelesen und für Geometrie UND Architektur-Set wiederverwendet.
    meta = _read_meta(model_path) or {}
    geom = _kv_geometry_from_meta(meta)
    if geom is not None:
        kv_bytes_per_token = geom["kv_bytes_per_token"]
        fixed_overhead_gb = geom["ssm_fixed_bytes"] / GB
        if geom["n_ssm_layers"] > 0:
            notes.append(
                f"Hybrid-SSM: {geom['n_kv_layers']} KV-Layer + {geom['n_ssm_layers']} "
                f"SSM-Layer (fester Zustand ≈ {fixed_overhead_gb:.2f} GB, "
                f"n_ctx-unabhängig) → {kv_bytes_per_token} KV-Byte/Token (f16)"
            )
        else:
            notes.append(
                f"GGUF-Meta: n_layer={geom['n_layer']}, n_head_kv={geom['n_head_kv']}, "
                f"head_dim={geom['head_dim']} → {kv_bytes_per_token} KV-Byte/Token (f16)"
            )
    else:
        kv_bytes_per_token = _DEFAULT_KV_BYTES_PER_TOKEN
        fixed_overhead_gb = 0.0
        notes.append("GGUF-Meta nicht lesbar → konservativer KV-Default.")

    # VRAM: Obergrenze = GESAMTKAPAZITÄT der LLM-GPU. Das Modell ist das
    # Working Set (LM Studio ist laut Projekt-Konvention vor App-Runs
    # geschlossen) → total_gb ist die ehrliche Deckelung. free_gb dient nur
    # der Co-Tenant-Warnung (stark belegte GPU könnte das Loading behindern).
    vram = _llm_gpu_vram()
    if vram is None:
        total_gb, free_gb = 8.0, 8.0
        notes.append("VRAM-Query nicht möglich → konservativ 8 GB.")
    else:
        total_gb, free_gb = vram
        if free_gb > 0 and total_gb > 0 and free_gb < 0.6 * total_gb:
            notes.append(
                f"Co-Tenant: nur {free_gb:.1f}/{total_gb:.1f} GB frei — der "
                "Vorschlag basiert auf der Gesamtkapazität; eine stark "
                "belegte GPU könnte das Modell-Loading dennoch behindern "
                "(OOM-Fallback im Loader bleibt Safety-Net)."
            )

    reasoning = (
        detect_is_reasoning(model_path) if is_reasoning is None else bool(is_reasoning)
    )

    # Closed-Set für reasoning_effort (pro Architektur). Reused aus dem Meta-
    # Read oben (kein zweiter Dateizugriff). Default: großzügig.
    if allowed_efforts is None:
        arch = meta.get("general.architecture")
        allowed_efforts = allowed_reasoning_efforts(
            arch if isinstance(arch, str) and arch else None
        )

    proposal = compute_sweet_spot(
        vram_ceiling_gb=total_gb,
        weights_gb=weights_gb,
        kv_bytes_per_token=kv_bytes_per_token,
        fixed_overhead_gb=fixed_overhead_gb,
        requested_n_ctx=requested_n_ctx,
        is_reasoning=reasoning,
        allowed_efforts=allowed_efforts,
    )
    return TokenScalingProposal(
        n_ctx=proposal.n_ctx,
        kv_quant=proposal.kv_quant,
        output_budget=proposal.output_budget,
        thinking_budget=proposal.thinking_budget,
        reasoning_effort=proposal.reasoning_effort,
        source=proposal.source,
        notes=tuple(notes) + tuple(proposal.notes),
    )


# ── Öffentliche API ─────────────────────────────────────────────────────────
def propose(
    model_path: str,
    requested_n_ctx: int = 16384,
    is_reasoning: Optional[bool] = None,
    mmproj_path: Optional[str] = None,
    apply_overrides: bool = True,
    explicit: Optional[TokenScalingOverrides] = None,
) -> TokenScalingProposal:
    """AUTO-CHECK → Vorschlag → (Overrides). **Präzedenz: UI > ENV > Auto.**

    Das ist die einzige Funktion, die App/Loader/UI/CLI aufrufen sollen.
    ``explicit`` sind die UI-Overrides (gewinnen über ENV). Loggt die
    Entscheidung (inkl. Quellen auto/env/user) und liefert das fertige
    ``TokenScalingProposal``.
    """
    # Closed-Set für reasoning_effort pro Architektur (z.B. Qwen3.8=qwen35
    # → nur xhigh/medium/low). Einmal bestimmt, für Auto UND Override genutzt.
    allowed_efforts = allowed_reasoning_efforts(_arch_of(model_path))
    p = auto_proposal(
        model_path,
        requested_n_ctx=requested_n_ctx,
        is_reasoning=is_reasoning,
        mmproj_path=mmproj_path,
        allowed_efforts=allowed_efforts,
    )
    if apply_overrides:
        p = resolve_proposal(p, allowed_efforts=allowed_efforts, explicit=explicit)
    logger.info(
        "🎛 Token-Skalierung für %s:\n%s",
        os.path.basename(str(model_path)),
        p.describe(),
    )
    # Registry: Generierungs-Pfade (Orchestrator, Chatbot-Logik, UI) können
    # den aktuellen Vorschlag lesen, ohne ihn weiterreichen zu müssen.
    set_current_proposal(p)
    return p


def main() -> None:
    """CLI: ``python -m utils.token_scaling --model <pfad.gguf>``."""
    # Windows-Konsolen nutzen oft cp1252/ascii → die Unicode-Arrowen in
    # Notes/Describe würden UnicodeEncodeError werfen. Robust: stdout/stderr
    # auf UTF-8 (errors=replace als letztes Netz) umstellen, nie crashen.
    import sys
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    ap = argparse.ArgumentParser(
        description="Hardware-bewusste Token/Context-Skalierung (Auto-Check → Vorschlag → Override)"
    )
    ap.add_argument("--model", required=True, help="Pfad zur GGUF-Datei")
    ap.add_argument("--mmproj", default=None, help="Optionale mmproj-Datei (Vision)")
    ap.add_argument("--requested-n-ctx", type=int, default=16384,
                    help="Max. gewünschtes Kontextfenster (Default 16384)")
    ap.add_argument("--no-override", action="store_true",
                    help="ENV-Overrides ignorieren (reinen Auto-Vorschlag zeigen)")
    args = ap.parse_args()

    logging.basicConfig(level=logging.WARNING)
    p = propose(
        args.model,
        requested_n_ctx=args.requested_n_ctx,
        mmproj_path=args.mmproj,
        apply_overrides=not args.no_override,
    )
    print("\n=== Token-Skalierung ===")
    print(p.describe())


if __name__ == "__main__":
    main()




