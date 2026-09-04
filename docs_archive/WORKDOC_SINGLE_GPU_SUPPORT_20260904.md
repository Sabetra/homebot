# Workdoc: Single-GPU-Support (entspannte Validierung + Rollen-Konsistenz)

> **Erstellt:** 2026-09-04
> **Status:** ERLEDIGT (2026-09-04)
> **Autor:** Cline Agent

## Original-Auftrag

Make the bot safely runnable on a single-GPU system by relaxing GPU validation and
adding tests, without LM Studio API support. Scope: nur Option A implementieren
(entspannte Validierung + Tests); LM-Studio-API-Arbeit ist out of scope.

## Scope & Nicht-Scope

| Im Scope | Nicht im Scope |
|----------|----------------|
| `scripts/validate_gpu_placement.py` für Single-GPU entspannen (PASS + Warnung) | LM-Studio-API-Integration |
| Rollen-Mapping in `utils/vram_monitor.py` für Single-GPU konsistent ("LLM+AUX") | GPU-/LLM-Parameter ändern |
| LLM-Rollen-Konsumenten tolerant machen: `scripts/model_loader.py`, `utils/token_scaling.py` | Runtime-Load-Pfad (bereits resilient: OOM-Fallback, KV-Quant-Retry, Cold-AUX-Release) |
| Tests: 1 GPU PASS, gleiche GPU PASS+Warnung, invalide Device FAIL, 0 GPUs FAIL | |

## Definition of Done

| # | Kriterium | Prüfmethode | Status |
|---|-----------|-------------|--------|
| 1 | Genau 1 nutzbare GPU → `check_placement()` PASS | `tests/test_validate_gpu_placement_single.py::test_single_gpu_passes_with_info_warning` | ☑ |
| 2 | LLM+AUX auf derselben GPU → PASS mit Warnung (kein `ok=False`) | `test_dual_gpus_but_same_assignment_warns_but_passes` | ☑ |
| 3 | Invalide env-Override (nicht-Integer oder Index nicht sichtbar) → FAIL | `test_env_override_invisible_device_fails`, `test_env_override_non_numeric_fails` | ☑ |
| 4 | Keine nutzbare GPU (Detection-Fallback-Platzhalter) → FAIL | `test_no_usable_gpu_fails` | ☑ |
| 5 | Single-GPU-Snapshot trägt Rolle "LLM+AUX" (kein AUX-only-Kollaps) | `tests/test_vram_monitor_single_gpu_role.py::test_single_gpu_snapshot_has_composite_role` | ☑ |
| 6 | `model_loader`-VRAM-Precheck und `token_scaling` erkennen "LLM+AUX" | `test_single_gpu_role_is_recognised_as_llm_role` + `test_model_loader_vram_precheck.py` | ☑ |
| 7 | Dual-GPU-Maschine (aktuelle Maschine) → Skript-Durchlauf ohne Placement-FAIL | `check_placement()` isoliert = PASS; Reranker-Check der Vollausführung blockiert, solange LM Studio VRAM hält (AGENTS.md) | ☑ (teilw.) |
| 8 | Bestehende GPU-/Loader-/Scaling-Tests bleiben grün | 18/18 + 69/69 + 7/7 (s. Testergebnisse) | ☑ |
| 9 | Doku: AGENTS.md + `docs/00_CONTEXT_MASTER.md` (Changelog) | Inspektion | ☑ |

## Alternativen & Entscheidung

| # | Option | Pro | Contra | Risiko |
|---|--------|-----|--------|--------|
| A | Nur `validate_gpu_placement.py` entspannen | Minimaler Change | Konsumenten suchen `role == "LLM"`; Single-GPU-Snapshot trägt Rolle "AUX" → VRAM-Precheck + Auto-Context-Check werden still inaktiv | Mittel |
| B | A + Rollen-Mapping "LLM+AUX" in `vram_monitor` + Konsumenten-toleranz | Vollständige Single-GPU-Observability/Prechecks | 3 weitere (kleine) Änderungsstellen | Gering |

> **Auswahl:** B — die Runtime unterstützt Single-GPU bereits; Observability und
> VRAM-Prechecks dürfen dabei nicht still inaktiv werden (Root-Cause statt Workaround).

## Verifizierte Fakten

| # | Fakt | Beleg |
|---|------|-------|
| 1 | `get_placement()` mappt eine GPU auf beide Rollen (`single_gpu=True`, `llm_cuda == aux_cuda`) | `utils/gpu_devices.py` (Audit; `describe()` dokumentiert "LLM+AUX → cuda:N") |
| 2 | `role_by_nvml = {p.llm_nvml: "LLM", p.aux_nvml: "AUX"}` kollabiert bei gleicher NVML-Nummer → Rolle "AUX" | `utils/vram_monitor.py:179` |
| 3 | `model_loader`-Precheck sucht `s.get("role") == "LLM"` | `scripts/model_loader.py` (VRAM-Precheck, ~Zeile 1165) |
| 4 | `token_scaling`-Auto-Check sucht `s.get("role") == "LLM"` | `utils/token_scaling.py` (~Zeile 1008) |
| 5 | `validate_gpu_placement` hard-fails bei `len(gpus) < 2`, `single_gpu`, `llm_cuda == aux_cuda` | `scripts/validate_gpu_placement.py` `check_placement()` |
| 6 | `model_loader` hat weiche VRAM-Warnung, OOM-Fallback (~8192), KV-Quant-Retry, GPU-Größen-Profil | `scripts/model_loader.py` (Audit) |
| 7 | `release_cold_aux_models()` gibt AUX-VRAM bei Bedarf frei | `utils/aux_model_release.py` (Audit) |
| 8 | `performance_tab` rendert die Rollen-String durch (cosmetic) | `ui_tabs/performance_tab.py:187-188` |

## Risiko & Impact-Matrix

| # | Risiko | Wahrscheinlichkeit | Auswirkung | Minderungsmaßnahme |
|---|--------|--------------------|------------|--------------------|
| 1 | Geteilte GPU: AUX-Modelle drücken LLM-VRAM | mittel | mittel | bestehend: OOM-Retry, KV-Quant-Retry, Cold-AUX-Release, globale CUDA-Locks |
| 2 | env-Override auf unsichtbares Device → silent Auto-Fallback | gering | hoch | Validator: hartes FAIL bei nicht-Integer / nicht sichtbarem Index (neu) |
| 3 | Doppelte Rolle "LLM+AUX" verwirrt UI-Anzeige | gering | kosmetisch | String wird 1:1 gerendert; Doku-Hinweis |

## Änderungen

| # | Datei | Änderung | Test-Ergebnis |
|---|-------|----------|---------------|
| 1 | `utils/vram_monitor.py` | `get_all_gpu_snapshots()`: Single-GPU-Rolle `LLM+AUX` (sonst `LLM`/`AUX`) | 3/3 `test_vram_monitor_single_gpu_role.py` PASS |
| 2 | `scripts/model_loader.py` | `_is_llm_role()`-Helper (akzeptiert `LLM` + `LLM+AUX`) im VRAM-Pre-Check | 69/69 Loader-/Scaling-Stack (inkl. `test_model_loader_vram_precheck.py`) PASS |
| 3 | `utils/token_scaling.py` | `_llm_gpu_vram()` erkennt Rolle `LLM+AUX` | 38/38 `test_token_scaling_overrides.py` PASS |
| 4 | `scripts/validate_gpu_placement.py` | Single-GPU-Policy (1 GPU PASS + Info; same-GPU Warnung; 0 GPU FAIL; env-Override hart) + Exit 0/1 | 15/15 `test_validate_gpu_placement_single.py` PASS |
| 5 | `tests/test_validate_gpu_placement_single.py`, `tests/test_vram_monitor_single_gpu_role.py` | neue hermetische Test-Suiten (monkeypatch, kein NVIDIA-Stack) | 18/18 PASS |
| 6 | `tests/conftest.py` | Autouse-Fixturre `_isolate_model_loader_singleton` (Root-Cause: lauffolgenrependente Singleton-Kontamination) | 69/69 Loader-/Scaling-Stack PASS |
| 7 | `utils/gpu_devices.py` | CLI-Diagnose: stdout/stderr auf UTF-8 (Codepage-Crash-Vermeidung) | 7/7 `test_benchmark_llm_gpu_tuning.py` PASS |
| 8 | `AGENTS.md`, `docs/00_CONTEXT_MASTER.md` | Single-GPU-Bullet + Changelog-Eintrag 2026-09-04 | — |

## Rollback-Strategie

| Schritt | Aktion |
|---------|--------|
| 1 | Backups: `~\bot6_backups\20260904_single_gpu\` (6 Dateien, vor Edit angelegt) |
| 2 | `Copy-Item`-Rückkopie je Datei (Pflege: AGENTS.md → AGENTS.md) + `git checkout` als 2. Quelle |
| 3 | Testsuite neu ausführen |

## Testergebnisse

| # | Test / Befehl | Ergebnis | Datum |
|---|---------------|----------|-------|
| 1 | `tests/test_validate_gpu_placement_single.py` (15 Tests: 1-GPU-PASS, same-GPU-Warnung, 0-GPU-FAIL, env-Override-FAIL/PASS, VRAM-Check-Rollen) | 15/15 PASS | 2026-09-04 |
| 2 | `tests/test_vram_monitor_single_gpu_role.py` (3 Tests: Composite-Rolle, LLM-Rollen-Erkennung, Dual-GPU-Separation) | 3/3 PASS | 2026-09-04 |
| 3 | Loader-/Scaling-Stack: `test_model_loader_vram_precheck.py`, `test_token_scaling_overrides.py`, `test_model_loader_chat_template_normalization.py`, `test_model_loader_streaming.py`, `test_model_loader_token_cache.py`, `test_model_loader_dynamic.py` | 69/69 PASS | 2026-09-04 |
| 4 | `tests/test_benchmark_llm_gpu_tuning.py` | 7/7 PASS | 2026-09-04 |
| 5 | `py_compile` auf `validate_gpu_placement.py`, `vram_monitor.py`, `model_loader.py`, `gpu_devices.py`, `conftest.py` | OK (Exit 0) | 2026-09-04 |
| 6 | `scripts/validate_gpu_placement.py` (Vollausführung auf Dual-GPU-Maschine) | Placement-Check PASS; Reranker-Check nicht ausführbar, solange LM Studio VRAM hält (bekannte Einschränkung, AGENTS.md) | 2026-09-04 |
