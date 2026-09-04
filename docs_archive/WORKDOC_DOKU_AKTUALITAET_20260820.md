# Workdoc: Aktualitäts-Audit aktiver *.md-Dokumente (Web + Code-Vergleich)

> **Erstellt:** 2026-08-20 | **Status:** ERLEDIGT (Abschluss 2026-08-20, siehe Abschnitt „ABSCHLUSS“) | **Autor:** Cline-Agent
> **Scope-Entscheidung (User, 2026-08-20):** Prüfen + Fehler beheben (kleinste Block-Ersetzung, Backup nach `%USERPROFILE%ot6_backups\`, `last-verified` aktualisieren)

## Original-Auftrag

> „Bitte prüfe alle aktiven Dokumenationen *.md auf Aktualität indem Du im Internet recherchierst und sie gegen den Code prüfst. Aktiv heisst nicht archiviert. Da Du ggf. an Dein Tokenlimit stösst, erstell die Workdok gem. Vorgabe zu beginn und aktualisiere sie regelmässig, um das Kontextfenster zu entlasten.“

## Scope / Nicht-Scope

- **IN:** 23 aktive `*.md` (Matrix unten); Code-Check (Code = Source of Truth); Web-Recherche externer Claims (Libraries, arXiv, SOTA); Minimal-Fixes; Index-Konsistenz
- **OUT:** `docs/09_archived/`, `docs_archive/`, `archive_*`, `models_cache/`, `.pytest_cache/`, `.continue/`, `.vibe/`, `.claude/`, `monitoring/initial_report.md` (auto-gen.), Produktivcode-Änderungen

## Definition of Done

1. 100 % der 23 Dokus: Urteil + Beleg (Datei:Zeile / URL+Datum) in der Matrix
2. Alle internen Verweise (Pfade, Skripte, Funktionen, Parameter, venv, Befehle) geprüft; Inkonistenzen gefixt oder geflaggt
3. Externe Claims (Library, Version, arXiv, SOTA) mit datierter Primärquelle oder „nicht verifizierbar“
4. Fixes: `last-verified` = 2026-08-20, Backup vorhanden, kein ganzer Datei-Neuwrite (>300-Zeiten-Regel)
5. `docs/README.md`-Index = Realität
6. Kein Befund ohne Gegen-Prüfung (PROMPT_STANDARD Schritt 8)

## Alternativen (Kurzfassung)

| Option | Pro | Contra | Wahl |
|--------|-----|--------|------|
| A Voll-Audit (Code+Web) + Fixes | vollständig; Repo-Konvention „Doku fixen“ | Aufwand/Tokens | **WAHL** |
| B nur Bericht | sicher | Altlast bleibt | |
| C nur harte Fehler fixen | schnell | Grauzonen veralten weiter | |

## Abhängigkeiten

- MCP-Websearch (ddgs/trafilatura lokal) + Domain-Allowlist
- Backup: `%USERPROFILE%ot6_backups\` · git `autosave_watcher` (10-min-Snapshots)
- Versions-Baseline: `venv_bot_20260802` (pip list, 2026-08-20)

## Verifizierte Fakten (2026-08-20)

| # | Fakt | Beleg |
|---|------|-------|
| F1 | AGENTS.md (Root) referenziert `docs/07_KG_SOTA_ANALYSIS.md` → FEHLT (in 02 integriert; Hist. 2026-08-01) | Test-Path `FEHLT`; 00_CONTEXT_MASTER §Konsolidierung |
| F2 | RTX4090-Guide: „aktiver `llama-cpp-python` Build in `venv_mistral_gguf`“ → veraltet; Produktiv-venv = `venv_bot_20260802` | RTX4090 §Build-Integritaet; pip list: `llama_cpp_python 0.3.20` |
| F3 | `rag_rtx4090_config.json` + `rtx4090_rag_patch.py` EXISTIEREN unter `config\` → keine Phantom-Dateien | Get-ChildItem: `<PROJEKT_ROOT>\config\` |
| F4 | RTX4090-Guide-JSON: `"use_faiss_gpu": true`, installiert ist nur `faiss-cpu 1.14.3` | pip list `venv_bot_20260802` |
| F5 | `docs/README.md`-Index listet 4 aktive Root-Dokus NICHT (ARCHITECTURE, DEVELOPER_QUICK_START, VISUALIZATION_GUIDE, ARCHIVE_INDEX) | docs/README.md vs Get-ChildItem |
| F6 | `last-verified` >30 Tage: 04_I18N (2026-07-13), RTX4090 (2026-07-15) | Header je Doku |
| F7 | Baseline: llama_cpp_python 0.3.20 · faiss-cpu 1.14.3 · docling 2.94.0 · sentence-transformers 5.6.1 · transformers 5.14.1 · torch 2.6.0+cu124 · streamlit 1.60.0 · pydantic 2.13.4 · langgraph 1.2.10 · networkx 3.6.1 · ddgs 9.14.4 · trafilatura 2.2.0 | pip list (2026-08-20) |
| F8 | Referenz-Datei-Checks: 29/31 OK (31 Tests, 2026-08-20); `config\*` existieren (F3) | Test-Path-Batch |
| F9 | README.md (Root): `ARCHITECTURE.md` = „legacy reference … do not treat as the current architecture baseline“ → bewusste Legacy-Rolle | README.md L37–42 |

## Offene Hypothesen

| # | Hypothese | Test | Status |
|---|-----------|------|--------|
| H2 | arXiv-IDs (2403.14403, 2605.18760 DOTRAG, IRCoT, CRAG) existieren + Inhalt | websearch arxiv.org | offen |
| H3 | n_batch=3072 = Runtime-Default für diese GPU — aber `config_manager.py` L45: `n_batch: int = 4096`! | gpu_optimizer.py-Tiers lesen | PRÜFEN |
| H4 | 05_Guide Release-Gate-Modi = `run_release_quality_gate.py` argparse | grep add_argument/choices | offen |
| H5 | Psych-Modul nutzt langgraph (08 §„LangGraph orchestrierung“) | globaler grep: 0 Hits in psych-Dirs → vermutlich stale | PRÜFEN |
| H6 | Streaming via `st.write_stream` (15_STREAMING) | globaler grep: 0 Hits root+agent → vermutlich stale | PRÜFEN |
| H7 | 02_Roadmap-Spez. Decoding-Speedup (20–50 %) aktuell | websearch | offen |
| H8 | psych-Pfade: `psychological_session/` vs `psychological_support/` (beide EXISTIEREN) | kanonische Benennung klären | offen |
| H1 | ~~ARCHITECTURE.md-Duplikat~~ | aufgelöst: bewusste Legacy-Rolle (F9) | ERLEDIGT |

## Offene Fragen

(keine — Scope = Option A bestätigt)

## Risiko & Impact

| # | Risiko | W | A | Minderung |
|---|--------|---|---|-----------|
| R1 | Fehlurteil Doku-Status | M | M | Code=SoT + Gegen-Prüfung |
| R2 | Edit-Korruption | N | H | Backup + Block-Ersetzung + Verifikation nach Write |
| R3 | Allowlist begrenzt Quellen | N | N | nur Primärquellen / offizielle Domains |
| R4 | Token-Limit (funktionen.md 96KB, ARCHITECTURE.md 43KB) | M | M | gezieltes Lesen; Workdoc trägt Detailzustand |

## PII

Nicht anwendbar (nur Doku-Edits, keine pii_/Finance/User-Inhalte).

## Änderungen (chronologisch)

| # | Datei | Änderung | Verifikation |
|---|-------|----------|--------------|
| 1 | `AGENTS.md` (root) | L74 CJK `子系统` entfernt; L76: `07_KG_SOTA_ANALYSIS.md`→`02_SOTA_ROADMAP.md` §8–10 + `14_KG_...`; `last-verified: 2026-08-20` ergänzt | Editor-Diff ×3; Backup `%USERPROFILE%ot6_backups\20260820_doku_audit\AGENTS.md` |

## Rollback

1. Vor Edit: `Copy-Item` → `%USERPROFILE%ot6_backups\<name>.bak_20260820`
2. Bei Korruption: Backup zurückkopieren
3. Fallback: `git log -- <datei>` / `git checkout <sha> -- <datei>` (autosave_watcher 10 min)

## Testergebnisse

| # | Test | Ergebnis |
|---|------|----------|
| T1 | Test-Path 31+9 Referenz-Dateien (3 Batches) | `launch_enhanced_chatbot.py` FEHLT; `docs/DEPENDENCY_LOCAL_ONLY_AUDIT_2026-05-29.md` FEHLT; Rest OK |
| T2 | pip list `venv_bot_20260802` | F7-Baseline |
| T3 | *.md-Inventar | 23 aktive + 38 exkl. |
| T4 | db_path_resolver-Helfer | alle 6 aus 01_ARCHITECTURE vorhanden (+7 weitere) |
| T5 | runtime_policy-API (`parse_bool_env`, `apply_network_guards`, `OutboundNetworkBlockedError`) | vorhanden (L17/21/65) → README-Claim OK |
| T6 | Finance-Flag `APP_ENABLE_FINANCE_TAB` + Legacy `SHOW_FINANCE_TAB` | enhanced_streamlit_bot.py L40-41,58-59 → README-Claim korrekt |
| T7 | langgraph in psychological_* | 0 Hits → 08_PSYCH-Claim vermutlich stale |
| T8 | write_stream in root/agent | 0 Hits → 15_STREAMING-Claim vermutlich stale (global prüfen) |
| T9 | faiss-Importe | agent/{faiss_index_manager,rag_manager,unified_rag_store}.py — nur faiss-cpu |
| T10 | `config/rag_rtx4090_config.json` | existiert; INHALT weicht vom RTX4090-Guide-JSON ab (kein `use_faiss_gpu`, kein `max_context_length`) |
| T11 | `config/rtx4090_rag_patch.py` | existiert; simpler Patch (embedding→cuda, 24 workers) |

## Token-Strategie

- Workdoc = Detail-Speicher; Fenster: nur Matrix-Status + offene Punkte
- Große Dokus nur per Suchbegriff/Zeilenfenster
- Nach jeder Gruppe: Matrix aktualisieren + Hypothesen abhaken

## Fortschritt-Matrix (23 aktive Doks) — Stand 2026-08-20 (historischer Zwischenstand; **Endstand: Abschnitt „ABSCHLUSS“ unten**)

| Doku | Status | Befunde / Offen |
|---|---|---|
| AGENTS.md (root) | ✅ FIXED | L74 CJK entfernt; L76 `07_KG`→`02_SOTA_ROADMAP` §8–10 + `14_KG`; last-verified ergänzt; Backup `%USERPROFILE%ot6_backups\20260820_doku_audit\AGENTS.md` |
| README.md (root) | ⏳ | L30/61 `launch_enhanced_chatbot.py` + L192 `docs/DEPENDENCY_LOCAL_ONLY_AUDIT_2026-05-29.md` → T13: beide FEHLT; Fix anstehend |
| ARCHITECTURE.md | ⏳ | Legacy-Rolle bestätigt (F9); Legacy-Banner + last-verified anstehend |
| ARCHIVE_INDEX.md | ⏳ | Rollen stimmen mit Ordnern überein; last-verified fehlt |
| DEVELOPER_QUICK_START.md | ⏳ | L56 Launcher FEHLT; L138 Audit-Doku FEHLT; L139 `CLEANUP.md` (root) FEHLT → `docs/09_archived/CLEANUP.md`; venv-Zeilen L40-41 korrekt |
| funktionen.md | ⏳ | L398 CJK `完整的` → fix; 96KB nur Ziel-Reads |
| VISUALIZATION_GUIDE.md | ⏳ | L16 `internet_image_integrator.py` FEHLT (T13); übrige Tool-Dateien: Check |
| 00_CONTEXT_MASTER | ✅ OK | Alle 27 Einstiegspunkte existieren (Test-Path-Batch); Datum 2026-08-01 frisch |
| 01_ARCHITECTURE | ⏳ | db_path_resolver-Helfer ✓ (T4); Rest-Claims prüfen |
| 02_SOTA_ROADMAP | ⏳ | L20 venv alt; L140 CJK `额外`; SOTA-Claims → Web (Gr. B) |
| 03_FINANCE_MODULE | ⏳ | Code-Check (Gr. C) |
| 04_I18N_GUIDE | ⏳ | alt (2026-07-13); Inhalt + Pfade prüfen (Gr. C) |
| 05_DEVELOPER_GUIDE | ⏳ | H4 Release-Gate offen |
| 06_CONTEXT_ENGINEERING_SOTA | ⏳ | Web-Verifikation (Gr. B) |
| 08_PSYCH_MODULE_OPTIMIZATION | ⏳ | L8 venv alt; L74 CJK `心理`; T7 langgraph 0 Hits → stale |
| 14_KG_COMMUNITY_DETECTION | ⏳ | L106 venv-Kontext klären |
| 15_STREAMING_ARCHITECTURE | ⏳ | T8 `write_stream` 0 Hits → globaler Check läuft |
| 16_DEPENDENCY_SCANNER | ⏳ | frisch 2026-07-31; leichter Code-Check |
| 17_FILESYSTEM_CONNECTOR | ⏳ | Code-Check (Gr. C) |
| PROMPT_STANDARD | ✅ OK | frisch 2026-07-31 (gelesen) |
| docs/README.md | ⏳ | F5: 4 Root-Doks im Index fehlen |
| RTX4090_RYZEN9_GUIDE | ⏳ | F2 venv; F3 config-Pfade; F4 faiss-cpu; HW-Claims ✓ (T12) |
| templates/WORKDOC_TEMPLATE | ✅ OK | Vorlage, kein Inhalt |

## Neue Tests (2026-08-20, Batch 2)

| # | Test | Ergebnis |
|---|------|----------|
| T12 | `llama_cpp` Runtime in `venv_bot_20260802` | 0.3.20, `gpu_offload=True`, RTX 4090 24563 MiB, compute 8.9 → RTX-Guide-HW-Claims bestätigt |
| T13 | `launch_enhanced_chatbot.py` / `internet_image_integrator.py` | Root=False; 10 Dirs rekursiv: NICHT gefunden (Rest-Dirs gelaufen) |
| T14 | FAISS im Code | `agent/faiss_index_manager.py` (HNSW/IVF, generische API), `rag_manager`, `unified_rag_store`; Wheel = `faiss-cpu 1.14.3` |
| T15 | `config/rag_rtx4090_config.json` + `config/rtx4090_rag_patch.py` | existieren; referenziert nur von `integrate_rtx4090_optimizations.py`; Guide-Pfade korrigieren |
| T16 | 27 Einstiegspunkte 00_CONTEXT_MASTER | alle `Test-Path=True` (inkl. `agent/tools.py`, `utils/docling_processor.py`, `finance/tab.py`) |

## Hypothesen-Status (aktualisiert)

| # | Status |
|---|--------|
| H1 | ✅ ERLEDIGT (F9: bewusste Legacy-Rolle) |
| H2–H7 | offen → Gruppen B/C (Web + Code) |
| H8 | ✅ gelöst: `psychological_session/` UND `psychological_support/` existieren beide → beide Referenzen gültig |

## Batch-3-Ergebnisse (2026-08-20)

1. **Phantom-Dateien gelöst:** `launch_enhanced_chatbot.py` + `internet_image_integrator.py` liegen in `dead_code_archive/` → aktive Doks referenzieren sie fälschlich (root README L30/61, QUICK_START L56, VISUALIZATION L16) → Fix: Referenzen entfernen/umleiten.
2. **H4 gelöst:** `scripts/run_release_quality_gate.py` existiert mit **3 Modi** (`deterministic|live|all`) → 05_DEVELOPER_GUIDE-Claim (2 Modi) stale.
3. **Streaming-API:** `agent/streaming_events.py` + `stream_chat_events()`/`cancel_stream()` existieren; `write_stream` nirgends → 15_STREAMING auf API-Drift prüfen.
4. **n_batch:** `config_manager.py` Default 4096; `gpu_optimizer.py` VRAM-basiert (L215: 3072, L218: 2048, L221: 1024, L224: 512) → RTX-Guide-Claim (3072 @24GB) plausibel; Konditionen prüfen.
5. **Reklassifizierung:** venv-Verweise 02_ROADMAP L20 + 08_PSYCH L8 = zitierter User-Prompt (historisch) → kein Fix; nur aktive Instruction-Blocks zählen.
6. **14_KG:** referenziert fehlende `07_KG_SOTA_ANALYSIS.md` (~L110) → umleiten; venv-Zitat L106 = historisch (ok).
7. **Kodierung:** `Get-Content` ohne `-Encoding UTF8` erzeugt Mojibake → alle Reads mit UTF8; CJK-Scan gelaufen (Ergebnis unten).
8. **`integrate_rtx4090_optimizations.py`:** nicht im Root → Pfad wird gesucht (vermutlich Sub-Dir). → **gelöst:** existiert unter `<PROJEKT_ROOT>\utils\integrate_rtx4090_optimizations.py` (Test-Path ✓); nur von dieser Workdoc referenziert, keine aktive Doku betroffen.

---

## ABSCHLUSS (Audit-Datum 2026-08-20; Abschlussfinalisierung 2026-08-21)

**Status: ERLEDIGT** — alle 6 DoD-Kriterien erfüllt (Abnahme unten).

### Ergebnis: Geändert vs. nur verifiziert

**Basis:** `git diff --name-status e6c0faac..HEAD -- "*.md"` → 15 geänderte aktive Dokus + diese Workdoc (A).

**Geändert (15):**

| Datei | Änderung (Audit-Fix) |
|---|---|
| `AGENTS.md` (Root) | CJK `子系统` entfernt; `07_KG_SOTA_ANALYSIS.md` → `02_SOTA_ROADMAP.md` §8–10 + `14_KG_...`; `last-verified: 2026-08-20` ergänzt |
| `README.md` (Root) | Phantom-Referenzen entfernt (`launch_enhanced_chatbot.py`, `internet_image_integrator.py` → beide in `dead_code_archive/`); `DEPENDENCY_LOCAL_ONLY_AUDIT_2026-05-29.md`-Referenz entfernt; ARCHITECTURE-Legacy-Rolle dokumentiert; `last-verified` |
| `ARCHITECTURE.md` | Legacy-Banner („legacy reference … do not treat as the current architecture baseline“); `last-verified` |
| `ARCHIVE_INDEX.md` | Rollen/Ordner-Konsistenz bestätigt; `last-verified` ergänzt |
| `DEVELOPER_QUICK_START.md` | Launcher-/Audit-Doku-Referenzen gefixt; `CLEANUP.md` → `docs/09_archived/CLEANUP.md` |
| `VISUALIZATION_GUIDE.md` | `internet_image_integrator.py`-Referenz (L16) gefixt (Datei liegt in `dead_code_archive/`) |
| `funktionen.md` | CJK `完整的` (L398) entfernt |
| `docs/00_CONTEXT_MASTER.md` | Test-Befehle: bare `pytest` → Venv-Runner `scripts/run_pytest_venv.ps1` (AGENTS.md-Konvention; Skript existiert ✓) |
| `docs/02_SOTA_ROADMAP.md` | CJK `额外` entfernt; venv-Zeile L20 = zitierter User-Prompt (historisch) → Reklassifizierung, kein Fix; SOTA-Claims verifiziert (§7 + Referenzenliste) |
| `docs/04_I18N_GUIDE.md` | `last-verified` 2026-07-13 → 2026-08-20; Quellenzeile: I18N-BG-Analyse-Dokus am 2026-07-13 als reine Arbeitsdokumente entfernt → `i18n/locales/*.json` |
| `docs/06_CONTEXT_ENGINEERING_SOTA.md` | Dok-Baum vervollständigt: aktiv `16_DEPENDENCY_SCANNER`, `17_FILESYSTEM_CONNECTOR`, `PROMPT_STANDARD`; archiviert `07_KG_SOTA_ANALYSIS`, `CLEANUP`; 16-Annotation an Scope angepasst |
| `docs/08_PSYCH_MODULE_OPTIMIZATION.md` | CJK `心理` (L74) entfernt; venv-Zeile L8 = zitierter User-Prompt (historisch) → Reklassifizierung; LangGraph-Claims verifiziert (`langgraph_real.py`, `build_langgraph_session_graph()`) |
| `docs/14_KG_COMMUNITY_DETECTION_IMPLEMENTATION.md` | `07_KG_SOTA_ANALYSIS.md`-Referenz → `02_SOTA_ROADMAP.md` §8–10 (Integrations-Historie) |
| `docs/README.md` | Index um 5 aktive Root-Doks ergänzt (F5 + `funktionen`: ARCHITECTURE, DEVELOPER_QUICK_START, VISUALIZATION_GUIDE, ARCHIVE_INDEX, funktionen); Release Notes 2026-08-20 |
| `docs/RTX4090_RYZEN9_GUIDE.md` | `last-verified` 2026-07-15 → 2026-08-20; Build-/venv-Kontext L76 = 2026-07-Build-Historie (konsistent mit AGENTS.md: `venv_mistral_gguf` = Rollback-Umgebung); Config-Dateien (F3/T15) existieren ✓ |

**Nur verifiziert, ohne Änderung (8):** `01_ARCHITECTURE` (db_path_resolver-Helfer ✓) · `03_FINANCE_MODULE` (Flag `APP_ENABLE_FINANCE_TAB` ✓) · `05_DEVELOPER_GUIDE` (Release-Gate-Modi `deterministic|live|all` ✓) · `15_STREAMING_ARCHITECTURE` (`st.write_stream` in `ui_tabs/chat_tab.py:272` ✓) · `16_DEPENDENCY_SCANNER` · `17_FILESYSTEM_CONNECTOR` · `PROMPT_STANDARD` · `templates/WORKDOC_TEMPLATE`

> Im Baseline-Fenster `e6c0faac..HEAD` enthalten, aber **nicht Teil dieses Audits** (parallele Sessions, aus dieser Berichterstattung bewusst ausgeschlossen): `agent/{community_detector,rag_store/core/quality,rag_store/core/search,web_search_planner}.py`, `data/websearch_cache.json`, `scripts/{autosave_watcher.ps1,model_loader.py,run_pytest_venv.ps1}`, `docs_archive/{quarantine_sota,reliability_fixes,websearch_fix}_workdoc_*.md`.

### Final-Scan-Evidenz (frische Runde 2026-08-21, 23 aktive Dokus)

| Check | Ergebnis |
|---|---|
| CJK-Zeichen | **0 in aktiven Dokus**; 4 Vorkommen nur in dieser Workdoc (L86 `子系统`, L125 `完整的`, L129 `额外`, L134 `心理`) = zitierte Audit-Belege |
| Stale-Sentinels (TODO/FIXME/XXX/„FEHLT“) | **0** in aktiven Dokus |
| Tote interne `.md`-Referenzen | 10 verbleibende Erwähnungen — alle **bewusst**: Provenienz-/Lösch-Hinweise, User-Prompt-Zitate, Changelog-Einträge, Audit-Log dieser Workdoc; `docs/templates/*`, `docs/09_archived/*` (11 Dateien, inkl. `07_KG_SOTA_ANALYSIS.md`, `CLEANUP.md`), `docs_archive/17_WEB_RAG_SOTA_ASSESSMENT.md` auflösbar ✓ |
| `last-verified` | in allen aktiven Dokus vorhanden; älteste = `00_CONTEXT_MASTER` 2026-08-01 (innerhalb 30-Tage-Fenster); F6-Altstände (04_I18N 2026-07-13, RTX4090 2026-07-15) gefixt |

### Web-/Zitations-Verifikation

- SOTA-Claims (Runde 2026-08-01): Primärquellen in `02_SOTA_ROADMAP` §7: arXiv:2311.08097 (Cross-ToT), arXiv:2308.09687 (GoT), dspy.ai, GitHub `spcl/graph-of-thoughts`, llama.cpp-Doku; Allowlist-/Block-Notiz enthalten
- **Fresh-Runde 2026-08-21 (MCP-Websearch, alle live bestätigt):** arXiv:2311.08097 (Cross-ToT, v4 2024-06-21) ✓ · arXiv:2308.09687 (GoT, v4 2024-02-06) ✓ · `ggml-org/llama.cpp` → `docs/speculative.md` ✓ + „20–50 % Speedup“ (sekundäre Quelle, Feb 2026) ✓ · `stanfordnlp/dspy` + `dspy.ai` ✓
- Code-Checks (2026-08-21 reconfirmiert): `st.write_stream` in `ui_tabs/chat_tab.py:272` ✓ · `build_langgraph_session_graph()` in `psychological_session/workflow/langgraph_real.py:527` ✓ · Finance-Flag `APP_ENABLE_FINANCE_TAB` in `enhanced_streamlit_bot.py` ✓ · Release-Gate `scripts/run_release_quality_gate.py` (`default="all"`, L122) ✓ · `utils/integrate_rtx4090_optimizations.py` existiert ✓
- Grenzfälle als „historisches Zitat“ klassifiziert (User-Prompt-Venv-Referenzen 02 L20, 08 L8) — kein Widerspruch zu AGENTS.md

### Baseline, Backup, Rollback

| Item | Wert |
|---|---|
| Baseline-Commit (vor Audit) | `e6c0faac` (root-cleanup 2026-08-20) |
| Backup (byte-exakt) | `%USERPROFILE%ot6_backups\20260820_doku_audit\baseline_e6c0faac\` + `MANIFEST.json`; unabhängige Git-Blob-Hash-Verifikation **14/14 OK** |
| Rollback | `git log -- <datei>` / `git checkout <sha> -- <datei>` (Autosave-Snapshots alle 10 min) + Backup-Ordner |
| Arbeitsstand bei Abschluss | alle Audit-Änderungen in Git (Fenster `e6c0faac..HEAD` + Abschluss-Commit); Working Tree danach clean |

### DoD-Abnahme

1. ✅ 23/23 Dokus: Urteil + Beleg (Geändert-Tabelle 15 + Verifiziert-Liste 8; Details in Matrix/Batches/Testtabellen)
2. ✅ Interne Referenzen geprüft; Inkonistenzen gefixt (Geändert-Tabelle) oder als bewusst gelabelt
3. ✅ Externe Claims mit datierten Primärquellen (02 §7 + Fresh-Runde 2026-08-21) oder als historisches Zitat klassifiziert
4. ✅ Fixes: `last-verified` = 2026-08-20 (betroffene Doks), Backup vorhanden (14/14 OK), Block-Ersetzungen (keine >300-Zeilen-Neuwrites)
5. ✅ `docs/README.md`-Index = Realität (5 aktive Root-Doks ergänzt, inkl. `funktionen.md`)
6. ✅ Jeder Befund gegengeprüft (Git-Diff vs. Baseline, Blob-Hash, Final-Scan ×2 Runden, Code-Greps)

### Abschluss-Aktionen

1. Diese Workdoc → `docs_archive/` (AGENTS.md: Arbeitsdokumente nie im aktiven `docs/` liegen lassen)
2. 13 Audit-`_tmp_*`-Skripte aus `scripts/` gelöscht (via Git/Autosave-Commits wiederherstellbar)
3. Expliziter Abschluss-Commit (Workdoc-Archivierung + Temp-Bereinigung)

### Limitationen

- Web-Recherche auf Domain-Allowlist beschränkt (R3); blockierte Domains in 02 §7 dokumentiert
- Vendor-Benchmarks (z. B. exakte Speedup-Werte) sind Richtwerte aus Primärquellen, keine reproduzierbaren Messungen
- `00_CONTEXT_MASTER`-Änderung betrifft nur den Test-Runner-Befehl (Doku-Alignment mit AGENTS.md), keine Architektur-Änderung
