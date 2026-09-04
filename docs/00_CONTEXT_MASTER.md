<!-- last-verified: 2026-08-28 -->
# CONTEXT MASTER - AI Chatbot Project

> **Zweck:** Master-Context-Dokument fuer LLM-basierte Development-Workflows.
> Dieses Dokument provides die essentielle Projektuebersicht, damit ein LLM-Agent
> schnell den Projektzustand versteht, ohne alle Einzeldokumente lesen zu muessen.
>
> **Stand:** 2026-07-27 | **Consolidation Release 1.2**
> **System:** Windows 11, 64 GB RAM, Dual-GPU (RTX 4090 24 GB [LLM] + RTX 3060 Ti 8 GB [AUX]) | **LLM:** Gemma4 12B | **Env:** venv_bot_20260802

---

## 1. PROJECT ESSENTIALS

### 1.1 What is this project?
A **Local-First Multimodal AI Chatbot Workspace** with:
- RAG-based document retrieval (PDFs, images, text)
- Finance query engine with natural language SQL
- Psychological session support with multi-phase therapy workflows
- Knowledge Graph (KG) entity resolution and semantic search
- Internationalization (i18n) support (DE, EN, BG)
- GPU-optimized inference (RTX 4090, CUDA-thread-safe)

### 1.2 Tech Stack
| Component | Technology |
|-----------|-----------|
| LLM Inference | GGUF via llama-cpp-python, GGUF loader |
| RAG | FAISS indices, Docling PDF processor, parallel embeddings |
| Knowledge Graph | NetworkX, semantic entity matching, cosine similarity |
| Finance Engine | Custom query planner, grammar compiler, query reflector |
| Framework | Pydantic v2, Streamlit UI, Pytest |
| i18n | Custom i18n_manager with JSON locale files |
| GPU | CUDA locks, VRAM monitoring, adaptive batch sizing |

### 1.3 Key Entry Points
```
enhanced_streamlit_bot.py    # Main UI application
agent/orchestrator.py        # Core RAG orchestration (SOTA pipeline)
agent/sota_pipeline.py       # SOTA RAG pipeline with StrixKAT eval
agent_chatbot_logic.py       # Chat logic with ReAct agent routing
finance/tab.py               # Finance UI tab
wellbeing_session/       # Therapy session module
i18n/i18n_manager.py         # Internationalization manager
```

### 1.4 Virtual Environment
```powershell
<PROJEKT_ROOT>\venv_bot_20260802\Scripts\Activate.ps1
```

`venv_bot_20260802` ist die validierte Produktivumgebung (siehe `05_DEVELOPER_GUIDE.md`).
`venv_mistral_gguf` bleibt ausschliesslich als Rollback-Umgebung bestehen.

---

## 2. ARCHITECTURE OVERVIEW

### 2.1 Core Architecture Pattern
```
User Input -> Semantic Router -> [SIMPLE | PLAN_EXECUTE | REACT]
                                    |
                                    v
                           Retrieval Router
                          /        |        \
                        RAG      KG       Web Search
                          \        |        /
                            v      v      v
                       EvidenceReRank -> IRCoT -> Response
```

### 2.2 SOTA RAG Pipeline Stages
1. **Multi-Query Fallback** - 3 query variants + cross-validator
2. **EvidenceReRank** - 3-tier evidence scoring
3. **IRCoT** - Information-Seeking ReAct Chain of Thought
4. **CRAG Self-Correction** - Verify->Retry cycles (max 2)
5. **StrixKAT Evaluation** - Quality gates with SQLite snapshots

### 2.3 GPU Architecture
- **Dual-GPU (2026-08-25):** LLM → RTX 4090 (cuda:0), AUX (Reranker/Embeddings/NLI/OCR/Docling) → RTX 3060 Ti (cuda:1); Single Source of Truth `utils/gpu_devices.py`
- CUDA- und NVML-Index vertauscht (UUID-gemappt in `gpu_devices.py`) — Monitoring nutzt `llm_nvml`/`aux_nvml`
- CUDA locks in 6+ modules prevent race conditions
- Adaptive batch sizing based on VRAM class
- Default: n_batch=3072, n_ubatch=2048, n_threads=12; LLM-Load erzwingt split_mode=NONE (kein Layer-Split auf die AUX-GPU)
- VRAM monitoring via NVML/PyTorch + nvidia-smi-CLI-Fallback (`get_all_gpu_snapshots()`, beide GPUs)
- **Selektiver AUX-Modell-Lifecycle (2026-08-28):** Kalte AUX-Modelle (Docling-Pipeline, EasyOCR-Reader) werden nach Import-/OCR-Peaks zentral entladen (`release_cold_aux_models()`, `utils/aux_model_release.py`) und geben den CUDA-Cache an das OS zurück; heiße Query-Path-Modelle (Reranker/NLI/Embeddings) bleiben resident; Lazy-Reload bei Bedarf (funktionen.md §V)

### 2.4 Finance Module
- `FinanceQueryPlanner.plan()` erzeugt einen validierten, typisierten Finance-Toolplan
- Toolargumente werden kompakt aus den kanonischen OpenAI-Toolschemas in den Planner-Prompt abgeleitet
- `GrammarCompiler.compile_for_schema()` kompiliert Pydantic-v2-Schemas zu Decoding-Grammatiken
- `FinanceQueryReflector.decide()` entscheidet anhand des Tool-Traces ueber Fortsetzung oder Abschluss
- 34 exponierte Finance-Tools sind implementiert; acht Analysepfade decken Kostenstruktur, Recurrence, Forecast, Anomalien, Budget, Sparpotenzial und Trendbrueche ab
- Produktiver Finance-Chat erlaubt nur lokale `finance_*`-Tools und keinen Python-Executor
- Strikter synthetischer Gemma4-Canary: `scripts/run_finance_canary.py`

### 2.5 Chat Streaming
- Typed local-only `ChatEvent` protocol across model, routing, bridge and Streamlit UI
- Native llama.cpp text deltas for SIMPLE; structured progress with post-gate final text for agent routes
- Request-scoped cancellation, iterator cleanup and internal-history rollback
- Assistant history/SQLite commit only after canonical `RunCompleted`
- No raw chain of thought; REACT output follows citation, verification and PII gates
- Details: `15_STREAMING_ARCHITECTURE.md`

---

## 3. DOCUMENTATION MAP

### 3.1 Active Documentation (Read These)
| File | Purpose | When to Read |
|------|---------|--------------|
| `AGENTS.md` (root) | Agent entry point: commands, conventions, GPU params | Agents: always (auto-loaded by most tools) |
| `00_CONTEXT_MASTER.md` | This file - quick orientation | Always first |
| `01_ARCHITECTURE_DEEP_DIVE.md` | Detailed architecture | When modifying core components |
| `02_SOTA_ROADMAP.md` | SOTA implementation status | When planning improvements |
| `03_FINANCE_MODULE.md` | Finance engine details | When working on finance features |
| `04_I18N_GUIDE.md` | Internationalization | When adding translations |
| `05_DEVELOPER_GUIDE.md` | Setup, testing, debugging | When starting development |
| `06_CONTEXT_ENGINEERING_SOTA.md` | Context engineering guide for LLM workflows | When optimizing LLM context usage |
| `08_PSYCH_MODULE_OPTIMIZATION.md` | Psycho-Modul: Safety, Identity, Datenlebenszyklus, Persistenz und SOTA-Fixes | Bei Änderungen am Psycho-Modul |
| `14_KG_COMMUNITY_DETECTION_IMPLEMENTATION.md` | Standalone Community Detection/Subgraph Retrieval; produktive Verdrahtung offen | Bei KG-Community-Arbeit |
| `15_STREAMING_ARCHITECTURE.md` | Typed chat events, route behavior, cancellation and persistence | When modifying normal chat streaming |
| `16_DEPENDENCY_SCANNER.md` | Dependency Vulnerability Scanner: lokaler, privacy-preserving Security-Scan | Bei Security-Audits |
| `17_FILESYSTEM_CONNECTOR.md` | SOTA Filesystem Connector: Path-Sandbox, Declarative Tool Profiles, Security-Layer | Bei FS-Tool-Änderungen |
| `18_LEGAL_WEB_PERSIST.md` | Legal/Ethical Compliance für web-sourced RAG-Persistierung (robots.txt/Header/Meta-Gates, Retention 30 d, Pruning) | Bei Web-RAG-Persistierung |
| `19_LICENSES_AND_COMPLIANCE.md` | Lizenzen & Compliance: AGPL-3.0 (Michaël Artebas), LICENSES.md-Generator/Checker, Modell-Gewichte | Bei Lizenz-/Dependency-/Compliance-Änderungen |
| `20_TOKEN_SCALING.md` | Token/Context-Skalierung: hardware-bewusster Auto-Vorschlag, Präzedenz UI > ENV > Auto, KV-Quantisierung, Persistenz, Fallbacks | Bei Token-/Context-/KV-/Reasoning-Effort-Änderungen |
| `RTX4090_RYZEN9_GUIDE.md` | Hardware tuning (verified LLM profile) | When touching GPU/LLM params |
| `funktionen.md` (root) | Compendium of large/complex functions | Before editing orchestrator/pipeline code |
| `ARCHITECTURE.md` (root) | Legacy architecture reference | Historical context |
| `README.md` (root) | Project overview | User-facing info |

### 3.2 Archive (Reference Only)
| Location | Contents |
|----------|----------|
| `docs/09_archived/` | Archived docs-level material (old audits, trackers) |
| `docs_archive/` | Historical reports; `analysis_artifacts/` for generated outputs |
| `ARCHIVE_INDEX.md` (root) | Archive entry point & retention policy |

---

## 4. SOTA STATUS (July 2026)

### 4.1 Current Ratings (1-7 Scale)
| Category | Rating | Notes |
|----------|--------|-------|
| RAG Quality | ⭐⭐⭐⭐⭐ (5/7) | IRCoT + EvidenceReRank + Multi-Query. Missing: Multi-Hop 3+ sources |
| KG Depth | ⭐⭐⭐ (3/7) | Bewertet den **wirksamen** Funktionsumfang im Antwortpfad. `02_SOTA_ROADMAP.md` §10 vergibt fuer dieselbe Ebene 7/7 — das misst die *Implementierungsqualitaet* der Einzelkomponenten. Die Luecke zwischen beiden Zahlen ist die fehlende Verdrahtung (z. B. CommunityDetector). |
| Performance | ⭐⭐⭐⭐⭐⭐ (6/7) | RTX4090 fully utilized, 400MB/s embedding |
| Reliability | ⭐⭐⭐⭐⭐⭐ (6/7) | 4-tier fail-safe, no silent errors |
| Maintainability | ⭐⭐⭐⭐ (4/7) | Good modularity, test coverage ~40% |
| Security | ⭐⭐⭐⭐⭐ (5/7) | PII filter, RBAC. Missing: KG encryption-at-rest |
| **Overall** | **4.8/7** | SOTA-adjacent (KG ist Haupt-Hebel) |

### 4.2 Remaining Items
- **P2-1:** Config Manager hot-reload + feature flags
- **P2-2:** Finance adversarial test suite (DONE per audit addendum)
- **P2-3:** StrixKAT integration (DONE per audit addendum)
- **P2-X:** Secondary-Model-Grounding in Verification → **Entscheidung: Experimental, nicht implementieren** (VRAM-Kosten > Nutzen)
- **P3-1:** Test coverage increase (currently ~40%)
- **P3-2:** Adaptive retrieval depth in `_live_search()` ✅ DONE (WebSearchPlanner + WebSearchReflector integriert)
- **P3-3:** Lifecycle/default continuous-mode usage for `SOTAPipeline` and related health metrics

### 4.3 Audit A-E (2026-07-14, verifiziert)
- Alle Audit-Foki A-E abgeschlossen; Belege siehe `docs_archive/AUDIT_SOTA_WORKDOC_2026-07-14.md` (korrigierte Fassung, archiviert).
- PDF-Pipeline: `advanced_pdf_processor` (deprecated Adapter) vollstaendig entfernt/archiviert.
  Alle Call-Sites nutzen jetzt direkt `utils/docling_processor.DoclingProcessor`;
  Nicht-Docling-Fallbacks laufen ueber pymupdf4llm -> pdfminer -> PyMuPDF -> OCR.
- Dead-Code-Archivierung: `scripts/archive_dead_code.ps1` ist safe-by-default
  (Dry-Run + ripgrep-Referenzcheck + JSON-Report; verschieben nur mit `-Execute`).
- `schemas/__init__.py`: Pydantic-V2-Protokoll (`__get_pydantic_core_schema__`) statt deprecated `__get_validators__`.

---

## 5. QUICK COMMANDS

### 5.1 Start Commands
```powershell
# Start with finance module
.\start_private_with_finance.ps1

# Start without finance (public mode)
.\start_public_no_finance.ps1

# Start with Mistral vibe
.\start_mistral_vibe.ps1
```

### 5.2 Development Commands
```powershell
# Run tests via the project venv; never rely on bare `python` in an arbitrary shell
powershell -ExecutionPolicy Bypass -File .\scripts\run_pytest_venv.ps1 tests/ -q --no-header -p no:cacheprovider

# Run specific test suite
powershell -ExecutionPolicy Bypass -File .\scripts\run_pytest_venv.ps1 tests/test_finance_reflector_adversarial.py -v

# Check test coverage
powershell -ExecutionPolicy Bypass -File .\scripts\run_pytest_venv.ps1 -- --cov=. --cov-report=html tests/

# Strict release gate (deterministic + local Gemma4 canaries)
python scripts/run_release_quality_gate.py --mode all --force-regenerate
```

---

## 6. CONTEXT ENGINEERING PRINCIPLES

This documentation follows **SOTA Context Engineering** principles:

1. **Hierarchical Structure**: Numbered documents (00-05) for progressive loading
2. **Progressive Disclosure**: Master context first, then deep dives as needed
3. **Semantic Grouping**: Related topics consolidated into single documents
4. **Machine-Readable**: Consistent Markdown formatting, tables, code blocks
5. **Cross-References**: Clear links between related documents
6. **Status Tracking**: Clear active vs archive distinctions
7. **LLM-Optimized**: Essential info in first 2000 tokens for quick context loading

### For LLM Agents:
- **Always read `00_CONTEXT_MASTER.md` first** for project orientation
- **Read specific numbered docs** only when working on that subsystem
- **Archive docs** are only for historical/root-cause reference
- **Code is source of truth** - docs provide context, code provides implementation

---

## 7. CONSOLIDATION HISTORY

| Date | Action |
|------|--------|
| 2026-07-13 | Initial consolidation: 25+ docs reduced to 7 core documents |
| 2026-07-13 | Context Master created as LLM-optimized entry point |
| 2026-07-13 | All originals backed up with `_bak_20260713` suffix |
| 2026-07-14 | Audit A-E abgeschlossen: Dead-Code-Restore, PDF-Pipeline Root-Cause-Fixes, Pydantic-V2-Migration in schemas/, archive_dead_code.ps1 safe-by-default |
| 2026-07-14 | Doku-SOTA-Audit: AGENTS.md (agents.md-Standard) eingeführt, Doc-Map vervollständigt (06, funktionen.md, RTX4090-Guide), Archiv-Referenzen korrigiert, GPU-Parameter-Widerspruch in 06 behoben (n_batch 8192→3072), Workdoc-Altlasten nach docs_archive/ verschoben |
| 2026-07-15 | KG-SOTA-Analyse: 07_KG_SOTA_ANALYSIS.md erstellt, Overall-Rating 5.2→4.8/7 (KG 3/7 identifiziert als Haupt-Hebel), SOTA-Roadmap mit 3 Phasen + 5 Varianten dokumentiert |
| 2026-07-23 | Psycho-Modul SOTA-Audit: 15 Probleme identifiziert und behoben (timezone-critical, KG missing method, RAG KeyErrors, dedup windows, lifecycle race condition). Tests: 5/5 passed. Audit-Doku archiviert nach docs_archive/PSYCHO_MODULE_SOTA_AUDIT.md |
| 2026-08-01 | SOTA-Doku-Konsolidierung: 07_KG_SOTA_ANALYSIS.md in 02_SOTA_ROADMAP.md integriert (§8-10: KG-SOTA-Bewertung, Beyond-SOTA, References), 07 nach 09_archived/ verschoben, 17_WEB_RAG_SOTA_ASSESSMENT.md-Phantom-Referenz entfernt |
| 2026-07-25 | Normaler Chat auf typisierte Streaming-Events umgestellt: native SIMPLE-Deltas, sichere Post-Gate-Agentausgabe, Cancellation, History-Rollback und completion-only Persistenz; Gemma-12B/RTX4090-Canary verifiziert |
| 2026-07-26 | Psycho-Session-Persistenz gehärtet: DB-autoritative Existenzprüfung, sichere Identity-Recovery, strukturierter Handler-Vertrag mit Session-Rebind, atomarer FK-Check/Insert und kanonische Mood-Timestamps; 69 breitere Psycho-Tests bestanden |
| 2026-07-27 | Finance-Analytics vervollstaendigt, Planner-Argumentvertraege kanonisch geerdet, Finance-Chat auf lokale Tools begrenzt und strikten Gemma4-Canary sowie einheitlichen Release-Quality-Runner eingefuehrt; 243 Gesamttests bestanden |
| 2026-08-01 | **SOTA-Verifikation externer Analyse**: Unabhaengige Code-Review + Internet-Recherche haben externe LLM-Analyse widerlegt (2/5 Kernbehauptungen falsch). Phase 2.2 (`_live_search()` QueryPlanner/Reflector) und AdaptiveRAG-Integration waren bereits vollstaendig integriert. SOTA-Ablehnungsargumente (DSPy, ToT, GoT, Speculative Decoding) durch arXiv/GitHub-Recherche widerlegt. Speculative Decoding jetzt als Investitions-Empfehlung (20-50% Speedup). Roadmap mit Verifikations-Sektion (§6-7) erweitert. |
| 2026-08-10 | Werkzeugkette und Doku-Konsistenz: Autosave-Sicherheitsnetz (`scripts/autosave_watcher.ps1`) und Pre-Commit-Gate (`.githooks/pre-commit`) aktiviert; `tests/test_orchestrator_adaptive_rag_integration.py` hermetisch gemacht (war unter `APP_LOCAL_ONLY=1` rot, d. h. unter dem Release-Gate); venv-Widerspruch in AGENTS.md/00/06/PROMPT_STANDARD auf `venv_bot_20260802` vereinheitlicht; KG-Bewertungswiderspruch zwischen 00 §4.1 (3/7) und 02 §10 (7/7) als Implementierung-vs-Verdrahtung aufgeloest; CommunityDetector-Status korrigiert; Modul-Landschaft in 01 §8a dokumentiert. Deterministisches Release-Gate gruen (624 Tests). |
| 2026-08-07 | SOTA Filesystem Connector implementiert: Path-Sandbox (Symlink/Race/Binär-Schutz), Declarative Tool Profiles (main_chat/finance/psych/settings), 2 neue Tools (list_directory, search_files), Orchestrator-Integration, 38 Tests bestanden. Doku: 17_FILESYSTEM_CONNECTOR.md. |
| 2026-08-24 | Progressive Tool Disclosure im ReAct-Pfad: Profil-Gating (main_chat = 10 Kern-Tools, finance_tab = Finance-Read-Tools), deterministische Finance-Intent-Erweiterung (nur erweiternd, Recall-optimiert, DE+EN Regex ohne LLM-Call), One-Shot Capability-Gap-Retry (State-Flag, geloggt), Hybrid-Retrieval (BM25 + Cosine + RRF k=60; Core-Tools geschuetzt, 4-stufige Degradation explizit geloggt). Finance-Tool-Partition als Single Source of Truth in tool_profiles.py (CORE=12, ANALYTICS=11, READ=26, WRITE=8; Write-Tools nie im ReAct-Pool, dedizierte Finance-Pipeline unveraendert). 97 neue + 121 Regressionstests bestanden; Produktionskallgraph verifiziert. Doku: funktionen.md §Q; Workdoc: docs_archive/WORKDOC_PROGRESSIVE_TOOL_DISCLOSURE_20260824.md. |
| 2026-08-25 | Dual-GPU-Platzierung: LLM → RTX 4090 (cuda:0), AUX (Reranker/Embeddings/NLI/OCR/Docling) → RTX 3060 Ti (cuda:1). Single Source of Truth `utils/gpu_devices.py` (CUDA↔NVML-UUID-Mapping, env-Overrides `BOT_LLM_CUDA_DEVICE`/`BOT_AUX_CUDA_DEVICE`); 9 Konsumenten-Module umgestellt; VRAM-Monitoring + Performance-Tab zeigen beide GPUs mit Rollen (pynvml + nvidia-smi-CLI-Fallback); ONNX-GPU-Reranking via `onnxruntime-gpu` (CPU-Fallback bleibt). Validierung: `python -m utils.gpu_devices` + `scripts/validate_gpu_placement.py` (LM Studio vorher schließen). Tests: 33/33 Reranker-Stack + 55/55 Docling PASS. Doku: AGENTS.md, RTX4090-RYZEN9-Guide, funktionen.md §T. |
| 2026-08-28 | Selektiver AUX-GPU-Modell-Lifecycle: Kalte AUX-Modelle (Docling-Pipeline, EasyOCR-Reader) werden nach Import-/OCR-Peaks zentral entladen (`utils/aux_model_release.py::release_cold_aux_models()`) — gibt den CUDA-Cache an das OS zurück, damit die 3060-Ti-VRAM für den LLM-Query-Path frei ist. Heiße Query-Path-Modelle (Reranker/NLI/Embeddings) bleiben resident. WeakSet-Registries (OCRProcessor, VisionOCRProcessor) + idempotentes cleanup() (gc.collect + torch.cuda.empty_cache); Lazy-Reload via _ensure_*() bleibt erreichbar (Instanzen bewusst NICHT aus Registry entfernt). Defensive Post-Import-Hook im finally-Block von ui_tabs/chat_tab.py (reason="pdf_import"). reranker.py: _load_failed-Cache (verhindert wiederholte fehlgeschlagene Lazy-Loads); rag_pipeline.py/rag_store/core/search.py: verfrühte is_available-Gates entfernt (Reranker-Laden nicht mehr dauerhaft blockiert). Vision-Modell (= aktuell geladenes multimodales LLM, in Produktion Gemma 4 12B) lebt im geteilten ModelLoader-Slot (Haupt-LLM-Schutz) und wird bewusst NICHT entladen. Verifiziert: py_compile (9 .py-Dateien) + Smoke-Test monitoring/_smoke_aux_release.py (Registry, Idempotenz, Lazy-Reload, Docling-Cleanup). Offene Next-Steps: breiterer RAG/Reranker/Docling/OCR-Testlauf + End-to-End-PDF-Import mit VRAM-Vorher/Nachher (LM Studio vorher schließen). Doku: funktionen.md §V; Workdoc: docs_archive/WORKDOC_AUX_GPU_MODEL_LIFECYCLE_20260828.md. |
| 2026-08-30 | Legal/Ethical Compliance für web-sourced RAG-Persistierung: `utils/web_compliance.py` (reine Stdlib) — robots.txt (per-Domain-Cache 1 h, Negativ-Cache 60 s, Fail-Open, RFC 9309 §3 longest-match via eigener Regelwahl — zwei CPython-3.12-Quirks in `urllib.robotparser` root-causiert und umgangen: bare-Direktiven-Ignore + first-match-instead-of-longest-match), X-Robots-Tag/Googlebot, Cache-Control/Pragma no-store, HTML meta robots; Gates an allen web-derived Persistierungs-Punkten (`unified_rag_store.upsert_url(_with_vision)`, `tools.persist_to_rag`, `rag_pipeline.snippet_fallback`, `orchestrator.snippet_fallback`); Retention: `retention_until`-Injektion (Default 30 d, `WEB_RETENTION_DAYS`, 0 = unbegrenzt) + `UnifiedRagStore.prune_web_content()` (web-only, Kind-Tabellen zuerst, keine blinden Deletes) bei Pipeline-Start im Hintergrund (daemon, fail-soft). Tests: `tests/test_web_compliance.py` 47/47 (hermetisch, injizierbarer fetcher, keine echten robots.txt-Downloads). Doku: 18_LEGAL_WEB_PERSIST.md, funktionen.md §W. |
| 2026-08-30 | Lizenzen & Compliance: AGPL-3.0 eingeführt (Copyright Michaël Artebas, `LICENSE`); Third-Party-Inventar `LICENSES.md` (52 direkt / 161 transitiv / 48 dev-only, 0 unknown/needs-review) via `scripts/generate_licenses.py` (Stdlib, PEP 639 + OSI-Klassifizierer, First-Wins-Metadaten-Parsing gegen korrupte METADATA-Artefakte, Extras-Auslassung, transitive Requires-Dist-Closure, Manual-Overrides); `scripts/check_licenses.py` (Frische + Policy, `--strict`); pytest-Gate `tests/test_licenses_md_up_to_date.py`; Pre-Commit-Hook mit fail-fast Lizenz-Gate; `pip-licenses` als unabhängiger Cross-Check (keine Abweichung); Root-Dokus `SUPPORT.md`, `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`, `SECURITY.md`; Doku: 19_LICENSES_AND_COMPLIANCE.md. |
| 2026-09-03 | Hardware-bewusste Token/Context-Skalierung vervollständigt: `utils/token_scaling.py` (Sweet-Spot-Kern PURE, Auto-Check via VRAM+GGUF-Meta inkl. Hybrid-SSM-Trennung, Präzedenz UI > ENV > Auto, KV-Quantisierung f16/q8_0, Reasoning-Effort-Closed-Sets pro Architektur, nie-feilende Persistenz `~/.cache/homebot/token_scaling_overrides.json` mit atomarem Write und Eintrag-Löschung bei leeren Overrides); `is_empty` als Property; `from_dict` normalisiert KV (lowercase, ungestützte Werte → Auto); flaches Persistenz-JSON `{modell: {feld: wert}}`; Streamlit-Sidebar-Panel (Auto-Vorschlag vor dem Load, q8_0 grau bis Runtime-Verifizierung) + `initialize_ai`-Flow; llama-cpp-python 0.3.35 akzeptiert `type_k`/`type_v` (Signatur verifiziert). Tests: 38/38 token_scaling_overrides + 3/3 model_loader_streaming PASS. Doku: 20_TOKEN_SCALING.md. |
| 2026-09-04 | Token-Skalierung: Streamlit-Start-Crash behoben (`st.selectbox(..., disabled_options=...)` ist kein Streamlit-API → TypeError; Options-Liste ist das Gate); **q8_0-KV Runtime-validiert** (echtes Load + Generation mit `type_k=type_v=GGML_TYPE_Q8_0(8)`, Nemotron-3-Nano-4B Q4_K_M, n_ctx=4096, RTX 4090, neben laufendem LM Studio via `CUDA_VISIBLE_DEVICES`-Isolation, VRAM-Leak-frei) → `q8_0` jetzt in der UI wählbar (informativ-`q8_note`-Caption); neuer UI-Regresstest `tests/test_streamlit_token_scaling_panel.py` (AppTest rendert das Panel, fängt invalid-Widget-Kwargs + Options-Regressionen ab). pynvml-FutureWarning root-causiert: deprecated `pynvml`-Redirector-Paket entfernt, `nvidia-ml-py>=13.610` in requirements.txt (import + Enum-Werte + `-W error::FutureWarning` sauber). `utils/gpu_devices.py`-CLI gegen CP1252-Console-Encodings gehärtet (stdout/stderr UTF-8-Reconfigure). Tests: 38/38 token_scaling_overrides + 3/3 model_loader_streaming + 3/3 streamlit_panel PASS. Doku: 20_TOKEN_SCALING.md §5, funktionen.md §Y. |
| 2026-09-04 | **Single-GPU-Support (Option A)**: EXAKT eine nutzbare GPU ist jetzt eine unterstützte Konfiguration statt ein Validierungs-Fehler. `scripts/validate_gpu_placement.py` neu (Policy: 1 GPU = PASS mit Info; LLM+AUX auf derselben GPU = PASS mit Warnung; 0 GPUs = FAIL; env-Override nicht-numerisch/unsichtbar = FAIL; Exit 0/1; `--bench` unverändert) — ersetzt den harten 2-GPU-Fail, war der einzige echte Single-GPU-Blocker (Runtime war schon single-GPU-fähig: `gpu_devices.py` mappt beide Rollen auf ein Device, `release_cold_aux_models()`, CPU-Fallback, CUDA-Lock-Serialisierung). `utils/vram_monitor.py`: `get_all_gpu_snapshots()` vergibt bei `llm_nvml == aux_nvml` die zusammengesetzte Rolle `LLM+AUX` (sonst `LLM`/`AUX`) — Konsumenten verfehlen die geteilte GPU nicht mehr. `scripts/model_loader.py`: neuer Helper `_is_llm_role()` (akzeptiert `LLM` + `LLM+AUX`) im VRAM-Pre-Check. `utils/gpu_devices.py`: CLI-Diagnose druckt UTF-8 (Codepage-Crash-Vermeidung). Test-Isolation: `tests/conftest.py` resetet das ModelLoader-Singleton je Test (fixt lauffolgenrependente Kontamination, 7→0 FAILED im Loader-Stack). Tests: 15/15 validate_gpu_placement_single (Policy: 1-GPU-PASS, same-GPU-Warnung, 0-GPU-FAIL, invalid-Device-FAIL) + 3/3 vram_monitor_single_gpu_role + Loader-Stack 31/31 + 38/38 token_scaling_overrides PASS. Doku: AGENTS.md (Single-GPU-Bullet), Workdoc: docs_archive/WORKDOC_SINGLE_GPU_SUPPORT_20260904.md. |

---

*Dieses Dokument wird bei jeder signifikanten Aenderung aktualisiert.*