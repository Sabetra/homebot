<!-- last-verified: 2026-09-03 -->

# 20 — Token/Context-Skalierung (hardware-bewusst)

**Modul:** `utils/token_scaling.py` · **Tests:** `tests/test_token_scaling_overrides.py` (38), `tests/test_model_loader_streaming.py` (3) · **UI:** Sidebar-Panel „Token Scaling" in `enhanced_streamlit_bot.py`

## 1. Zweck & Prinzip

Die drei bewusst getrennten Größen werden PRO HARDWARE und PRO MODELL abgeleitet —
keine einzelne feste Zahl:

| Größe | Begrenzt durch | Regler |
|-------|----------------|--------|
| Kontextfenster (`n_ctx`) | VRAM (KV-Cache + Gewichte) | Auto-Vorschlag ≤ requested |
| Output-Budget (`max output`) | `n_ctx` | ≤ 50 % / ≤ 16384 (Reasoning) bzw. ≤ 40 % / ≤ 8192 |
| Thinking-Budget (reasoning) | `n_ctx − prompt − output` | ≤ 30 % / ≤ 8192 / ≤ Fenster/2 |

Zusätzlich als eigene Regler: **KV-Quantisierung** (`f16` | `q8_0`) und
**Reasoning-Effort** (Closed-Set pro Architektur, `off` = aus).

Prinzipien (SOTA 2026, Reasoning-Modelle):

1. **Auto-Check zuerst:** VRAM-Pre-Check + GGUF-Metadaten → Sweet-Spot-Vorschlag.
2. **Jeder Wert bleibt ein Regler:** Auto-Default + ENV-Override + UI-Override.
3. **User-Request bleibt Deckel:** Der Vorschlag ist immer `≤ requested n_ctx`;
   eine User-Eingabe wirkt als Minimum-Floor für `max_tokens`
   (`main_generation_max_tokens` = `max(User-Einstellung, thinking+output)`).
4. **Thinking bleibt aktiv:** Es wird nur gedeckelt (Budget/Effort), nie
   deaktiviert — außer `reasoning_effort=off` oder Nicht-Reasoning-Modell.
5. **Never-failing:** Bei fehlender GPU / fehlenden Metadaten werden konservative
   Defaults gewählt und gewarnt — die App-Initialisierung bricht nie.

## 2. Architektur

Bewusst **entkoppelt** von `scripts/model_loader.py` (schwerer Import, `llama_cpp`):

| Schicht | Funktion | Testbar ohne GPU/Dateien |
|---------|----------|--------------------------|
| `compute_sweet_spot()` | **PURE-Kern:** VRAM-Budget → n_ctx → KV-Quant → Budgets | ✅ 100 % |
| `auto_proposal()` | Dünne Auto-Check-Schicht: `utils.vram_monitor`, Dateigröße, GGUF-Meta | teilweise |
| `propose()` | **Öffentliche API:** Auto → ENV → UI (Präzedenz) + Registry | ✅ |
| Registry (`set/current_proposal`) | Thread-sicher (Lock), Generierungs-Pfade lesen ohne Weiterreichen | ✅ |

Aufrufkette im Loader (`scripts/model_loader.py`):

```
load_model(..., token_scaling_overrides)
  → token_scaling.propose(model_path, requested_n_ctx, mmproj_path, explicit=overrides)
      → auto_proposal(...)            # VRAM + GGUF-Meta + Gewichtsgröße
      → resolve_proposal(...)         # ENV, dann UI (Präzedenz), Invariante
      → set_current_proposal(...)     # Registry für Generierung
  → n_ctx wird auf Vorschlag gekappt (loggt Vorher/Nachher)
  → type_k/type_v aus kv_type_pair(proposal.kv_quant) an Llama-Constructor
```

## 3. Sweet-Spot-Algorithmus (`compute_sweet_spot`)

1. **VRAM-Budget** = `Gesamt-VRAM der LLM-GPU × 0.88` (safety_ratio).
   `free_gb` dient nur der Co-Tenant-Warnung; Deckelung ist die Gesamtkapazität
   (LM Studio ist laut Projekt-Konvention vor App-Runs geschlossen).
2. **KV-Budget** = Budget − Gewichte (Modell **+ mmproj**) − 1,0 GB
   Aktivierungs-Reserve − fester Overhead (n_ctx-unabhängig, z. B. SSM-Zustand).
3. **n_ctx-Kandidaten** (absteigend, ≤ requested):
   `65536 → 32768 → 16384 → 8192 → 4096 → 2048`.
4. **KV-Quant:** `f16` bevorzugt; passt `f16` nicht → `q8_0`
   (halbiert KV-Speicher, <0,1 % Qualitätsverlust). Passt selbst das kleinste
   Kandidatenfenster nicht → konservativster Wert + `q8_0` + Hinweis
   (OOM-Fallback im Loader bleibt Safety-Net).
5. **Budgets** (`_derive_budgets`):
   - Reasoning: `thinking ≤ min(30 % n_ctx, 8192, available/2)`,
     `output ≤ min(50 % n_ctx, 16384, available − thinking)`
   - Nicht-Reasoning: `thinking = 0`, `output ≤ min(40 % n_ctx, 8192, available)`
   - **Invariante (hart):** `thinking + output ≤ n_ctx − prompt_reserve (2048)`;
     Floor = 0 (ehrliches 0/0 + Hinweis statt stiller Overflow).
6. **Reasoning-Effort:** Reasoning → `medium` (auf Closed-Set gekürzt);
   sonst `off`.

### KV-Bytes/Token aus GGUF-Metadaten (Single Source of Truth)

`kv_bytes_per_token = 2 (K+V) × n_kv_layers × n_head_kv × head_dim × 2 (f16)`

- Hybrid-SSM-Modelle (z. B. Qwen3-Next/`qwen35`): nur die
  Voll-Attention-Layer (`full_attention_interval`) skalieren mit `n_ctx`;
  SSM-Layer halten einen **festen** Zustand (`ssm_fixed_bytes`, n_ctx-unabhängig,
  Formeln = llama.cpp `qwen3next.cpp`, Sicherheitsfaktor 2).
- Fehlende/unvollständige Meta → konservativer Default
  (`2 × 40 × 8 × 128 × 2` Byte/Token) + Hinweis; unvollständige Hybrid-Keys →
  „alle Layer als KV" (nie der umgekehrte Kredit).

### Reasoning-Erkennung & Closed-Set

- **Erkennung:** Dateiname-Heuristik (`qwen3`, `magistral`, `deepseek-r1`, `qwq`,
  `thinking`) — konservativ (Default: nicht-Reasoning).
- **Effort-Closed-Set pro Architektur** (das Chat-Template parst den Wert!):
  `qwen35` → `xhigh`/`medium`/`low` (verifiziert); unbekannt → großzügiges
  Default-Set `off|minimal|low|medium|high|xhigh|max|default`.

## 4. Präzedenz: UI > ENV > Auto

`resolve_proposal()` wendet in dieser Reihenfolge an (jeder Wert trägt seine
Quelle `auto`/`env`/`user` im `source`-Dict — für Log & UI-Badges):

| Feld | ENV-Konstante (real) | Erlaubte Werte | Ungültig |
|------|----------------------|----------------|----------|
| `n_ctx` | `LLM_N_CTX` | Integer ≥ 512 | → Auto |
| `kv_quant` | `BOT_KV_QUANT` | `f16`, `q8_0` (case-insensitiv) | → Auto (f16) |
| `output_budget` | `BOT_MAX_OUTPUT_TOKENS` | Integer ≥ 0 | → Auto |
| `thinking_budget` | `BOT_THINKING_BUDGET` | Integer ≥ 0 | → Auto |
| `reasoning_effort` | `BOT_REASONING_EFFORT` | Closed-Set + `off` | → Auto |

- **Ungültige Werte werden nie wirksam** — sie fallen still (mit Hinweis) auf
  Auto zurück; ein harter Fehler im UI-/Load-Pfad ist ausgeschlossen.
- Nach der Override-Anwendung wird die Invariante **erneut** erzwungen
  (User-Override darf das Fenster nicht sprengen).
- `reasoning_effort=off` → `thinking_budget=0`.
- UI-Panel: Werte, die dem Auto-Vorschlag **identisch** sind, werden bewusst
  nicht als Override gespeichert (`overrides_from_values` → `None` = alles Auto).

## 5. KV-Quantisierung (ggml-Typen)

- `kv_type_pair(kv_quant)` → `(type_k, type_v)` für den Llama-Constructor:
  `f16` → `GGML_TYPE_F16 = 1`, `q8_0` → `GGML_TYPE_Q8_0 = 8`.
- WICHTIG: Das sind **`ggml_type`-Werte**, NICHT `llama_ftype`-Nummern
  (die Annotation im installierten llama-cpp-python ist irreführend).
- `None` (Auto/ungültig) → keine `type_k`/`type_v` → llama.cpp-Default (f16).
- `q4_0`-V-Cache wird bewusst gemieden (Qualität).
- **Status `q8_0` (2026-09-04): ✅ VOLL VALIDIERT.**
  - ✅ Python-Bindings: llama-cpp-python **0.3.35** akzeptiert
    `type_k`/`type_v` im `Llama`-Constructor (Signatur gecheckt).
  - ✅ GGML-Enum-Werte gegen `llama_cpp/llama_cpp.py` abgeglichen
    (`GGML_TYPE_Q8_0 = 8`).
  - ✅ **Full-Runtime (2026-09-04):** echtes Modell-Load mit
    `type_k = type_v = 8` (q8_0-KV-Cache) + erfolgreiche Generation:
    Nemotron-3-Nano-4B Q4_K_M (2.64 GB), `n_ctx=4096`, `n_gpu_layers=-1`,
    `flash_attn=True`, `offload_kqv=True`, RTX 4090 — **neben laufendem
    LM Studio** (nur ~5 GB freie 4090-VRAM; Isolation via
    `CUDA_VISIBLE_DEVICES=0` auf die 4090, kleine Batch/Thread-Werte).
    VRAM nach Prozessende vollständig zurückgegeben (keine Leaks).
  - **UI (2026-09-04):** `q8_0` ist in `kv_options`
    (`enhanced_streamlit_bot.py`, KV-Block) und wählbar; die
    `gui.token_scaling.q8_note`-Caption ist jetzt informativ
    („≈ halbiert den KV-Cache-VRAM“). Regression gesichert durch
    `tests/test_streamlit_token_scaling_panel.py`
    (`test_kv_quant_options_include_validated_q8_0`).
  - Historie: Streamlit hat kein `disabled_options` — dieses KWarg crashte
    den App-Start mit `TypeError` (2026-09-04, behoben); die
    Options-Liste ist das Gate.
- Loader-Fallback: akzeptiert die Engine `type_k`/`type_v` nicht, werden sie
  sicher entfernt (keine app-crashende Exception).

## 6. UI-Flow (Sidebar „Token Scaling")

```
Panel (vor dem Load, reine Berechnung — lädt KEIN Modell)
  auto = auto_proposal(model_path, LLM_CONTEXT_SIZE, mmproj)
  stored = load_overrides(model_path)          # pro Modell, Startwerte
  Widgets: n_ctx / KV / Output / Thinking / Effort  (+ Reset-Button)
  st.session_state.ts_overrides = overrides_from_values(auto, ...)
        │
initialize_ai()
  load_model(..., token_scaling_overrides=ts_overrides)
        │
  token_scaling.propose(..., explicit=ts_overrides)   # Registry + n_ctx-Kappung
        │
  nach erfolgreichem Load:
    ts_overrides ≠ None → save_overrides(model_key, ts_overrides)
    ts_overrides = None → clear_overrides(model_key)
```

- Modell-Key = `model_path` (dynamische Registry) bzw. Config-Key (statisch);
  Panel und `initialize_ai` verwenden denselben Key → Roundtrip konsistent.
- Statische Fallback-Konfig (keine Registry): Panel nicht verfügbar,
  Caption-Hinweis; Laden läuft mit Auto im Loader.


## 7. Persistenz (außerhalb des Repos)

- **Pfad:** `~/.cache/bot6/token_scaling_overrides.json`
  (Tests: per `BOT6_TOKEN_SCALING_OVERRIDES` umleiten).
- **Format:** flaches JSON `{ "<Modell-Key>": { "<Feld>": "<Wert>" } }` —
  nur gesetzte Felder, normalisierte Raw-Strings (`to_raw()`; KV/Effort
  lowercase). Beispiel:
  ```json
  {
    "C:\\models\\gemma-4-12B.gguf": {
      "n_ctx": "16384",
      "output_budget": "4096"
    }
  }
  ```
- **Leere Overrides** (alles Auto) → Modell-Eintrag wird **entfernt**.
- `clear_overrides(model)` entfernt den Eintrag; `clear_overrides("__all__")`
  löscht die gesamte Datei.
- **Atomar:** Temp-Datei (pid-suffixed) + `os.replace`.
- **Never-failing:** fehlende/korrupte Datei = leere Overrides (kein Exception
  im UI-Pfad); Schreibfehler = Warning, Override gilt in der Sitzung.

## 8. Fallback-Regeln (Never-failing-Matrix)

| Situation | Verhalten |
|-----------|-----------|
| VRAM-Query schlägt fehl | konservativ 8 GB + Hinweis |
| GGUF-Meta nicht lesbar | KV-Bytes-Default (typisch 8–14B-Klasse) + Hinweis |
| `propose()` fehlt im Loader | unverändertes `n_ctx`, Registry geleert, Warning |
| OOM beim Load | existierender OOM-Fallback in `model_loader` (Safety-Net) |
| `type_k`/`type_v` nicht akzeptiert | kwargs entfernt → llama.cpp-Default (f16) |
| Persistenz-Datei korrupt/fehlend | leere Overrides (Auto), Warning |
| Co-Tenant belegt VRAM (< 60 % frei) | Hinweis; Vorschlag basiert auf Gesamtkapazität |

## 9. Öffentliche API

```python
from utils import token_scaling

p = token_scaling.propose(                      # einzige API für App/Loader/CLI
    model_path, requested_n_ctx=16384,
    mmproj_path=..., explicit=overrides,        # UI-Overrides (UI > ENV > Auto)
)
token_scaling.current_proposal()                # Registry (None vor erstem Load)
token_scaling.main_generation_max_tokens(fallback=4096, current=...)
token_scaling.allowed_reasoning_efforts(arch)   # Closed-Set pro Architektur
token_scaling.model_architecture(model_path)    # general.architecture (UI)
```

**CLI:**

```powershell
python -m utils.token_scaling --model <pfad.gguf> [--requested-n-ctx 16384]
```

## 10. Tests

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run_pytest_venv.ps1 tests/test_token_scaling_overrides.py -v
powershell -ExecutionPolicy Bypass -File .\scripts\run_pytest_venv.ps1 tests/test_model_loader_streaming.py -v
```

Abdeckung (38 + 3 Tests): Sweet-Spot-Mathematik (f16/q8_0-Fallback, Invariante,
Hybrid-SSM), Präzedenz UI > ENV > Auto, `from_dict`/`to_raw`-Normalisierung
(KV-lowercase, ungestützte KV → Auto), Persistenz-Roundtrip (atomar, leere
Overrides entfernen Eintrag, `__all__`), `is_empty`-Property,
`overrides_from_values` (Auto-Identität → kein Override), Registry-Thread-Sicherheit.

## 11. Referenzen

- `utils/token_scaling.py` — Implementierung (Single Source of Truth)
- `scripts/model_loader.py` — `propose`-Aufruf, `type_k`/`type_v`, OOM-Fallback
- `enhanced_streamlit_bot.py` — Sidebar-Panel + `initialize_ai`-Flow
- `utils/vram_monitor.py` — VRAM-Queries (LLM-GPU)
- `docs/RTX4090_RYZEN9_GUIDE.md` — verifizierte GPU-/LLM-Parameter
- `i18n/locales/*.json` — `gui.token_scaling.*`-Strings (DE/EN/BG)

