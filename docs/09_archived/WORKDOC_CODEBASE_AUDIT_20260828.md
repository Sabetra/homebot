# Workdoc: Codebase-Audit & Low-Risk-Refactoring

<!-- last-verified: 2026-08-28 -->
<!-- status: IN PROGRESS -->
<!-- owner: agent -->
<!-- template: docs/templates/WORKDOC_TEMPLATE.md -->

## 0. Meta

| Feld | Wert |
|------|------|
| Datum | 2026-08-28 |
| Status | Phase 1–3 (Track A) ✅ DONE 2026-08-28 · Runde 2 (Track B: KG-/Psych-Separation, PII, Performance) ✅ DONE 2026-08-28 |
| Baseline | Working tree (uncommitted changes vorhanden; git HEAD ≠ Baseline) |
| Venv | `<PROJEKT_ROOT>\venv_bot_20260802\Scripts\Activate.ps1` |
| Testbefehl | `powershell -ExecutionPolicy Bypass -File .\scripts\run_pytest_venv.ps1 tests/ -q --no-header -p no:cacheprovider` |
| Backup-Ziel | `%USERPROFILE%ot6_backups\` (nie ins Repo) |

## 1. Ziel

Redundanzen, tote Code-Pfade, Repo-Hygiene (Backups/DBs im Repo) und zwei konkrete
Performance-Hotspots identifizieren, dokumentieren und in **niedrigem Risiko** behoben.
Jede Codeänderung ist **verhaltensäquivalent** (identische Outputs bei identischen
Inputs) und durch Pytest + Smoke-Imports abgesichert.

### Nicht-Ziel (Non-Scope)
- GPU-/LLM-Parameter-Änderungen (verifiziert, nicht erhöhen — AGENTS.md).
- Konsolidierung der zwei KG-Extractor-Module (`llm_knowledge_graph.py` ↔
  `llm_knowledge_graph_enhanced.py`): **bewusst vertagt** (§8 Phase 4), da ≥6
  Import-Stellen inkl. Monolith `agent/unified_rag_store.py` betroffen sind.
- Konsolidierung aller ~12 hand-rolled Retry-Loops auf tenacity: Follow-up
  (Phase 5); nur der Web-Search-Pfad bekommt den shared-Helfer.
- `refactored_gui/` entfernen: **aktiv** (wird von `enhanced_streamlit_bot.py` und
  `tests/test_quality_dashboard_regeneration_integration.py` konsumiert) → bleibt.

## 2. Definition of Done

1. Workdoc vollständig (diese Datei) + in `docs/README.md` referenziert.
2. Phase 1: 28 git-getrackte `.bak/.backup`-Dateien (+1 totes Root-Modul) und
   2025er-DB-Backups extern verlagert, Repo-Index sauber, `.gitignore` schließt die
   Suffix-Lücke. ✅ (Commit `c87f15b6`, s. §8 Phase 1)
3. Phase 2: `utils/rank_fusion.py` als kanonische RRF-Implementierung, alle 5
   Konsumenten migriert (Formel + je-Konsument-`k`-Wert unverändert). ✅ DONE 2026-08-28
4. Phase 3: O(n²)-Whitespace-Normalisierung → O(n) (alle 4 Stellen),
   `trim_history` → O(n) (Budget einmal berechnen, pro entferntem Block subtrahieren).
5. Pytest-Delta vs. Baseline: **keine neuen Failures** (Baseline: s. §10).
6. `py_compile` + Smoke-Import der betroffenen Module grün.
7. `git status` zeigt nur beabsichtigte Änderungen.
8. **Runde 2 (Track B) — Privacy/Separation:** Normal-Chat injiziert keinen Psych-Kontext
   (Gate `set_psychological_context_enabled(False)` in `_agent_chat()`), auch wenn
   `ENABLE_AGENT_PSYCH_INTEGRATION` gesetzt ist; Psychologie-Tab voll funktionsfähig;
   PII-Literale (`Christine`/`Kiano`) aus aktiven Modulen entfernt (Repo-Grep: 0 Treffer).
9. **Runde 2 (Track B) — Performance:** `count_tokens()` LRU-Cache (Load/Unload-Invalidation)
   + `emergency_trim_messages` O(n) (additive Accounting) — 25/25 neue Tests, alle PASS.

## 3. Verifizierte Fakten (mit Evidenz)

### 3.1 Repo-Hygiene / Backups

- **28 git-getrackte Backup-Dateien** (obwohl `.gitignore` `*.bak`/`*.backup`
  enthält — die Patterns matchen NICHT `*.bak_20260712` / `*.backup_20260714` /
  `*.backup_2026-07-14`; zusätzlich totes Root-`enhanced_kg_monitor.py`):
  - `agent/` (13): `orchestrator.py.backup_*` (4), `response_builder.py.bak_20260712`,
    `sota_pipeline.py.backup_*` (3), `verification_manager.py.backup_*` (3),
    `verification_manager.py.bak_*` (2)
  - `i18n/` (12): `__init__.py.bak_20260712`, `i18n_manager.py.bak_*` (2),
    `locales/{bg,de,en}.json.bak_20260712*` (9)
  - `docs_archive/` (2), `llm_utils/language_detector.py.bak_i18n` (1),
    `psychological_session/handlers/response_generator.py.bak_20260712` (1)
- **Keine `.db`-Datei ist git-getrackt** (`git ls-files | Select-String "\.db$"` → leer).
  Alle DB-Dateien im Repo sind ungetrackte Lokaldaten.
- **2025er-Backup-DBs im Repo** (User-Entscheidung Q1: nicht benötigt, extern):
  - `data/psychological_sessions.backup_20251007_132731.db` (225 KB)
  - `data/psychological_sessions.backup_20251007_134243.db` (303 KB)
  - `data/psychological_sessions.backup_before_cleanup_20251007_135547.db` (303 KB)
  - `data/backups/psychological_sessions_backup_20251007_132330.db` (225 KB)
  - `data/backups/psychological_support_backup_20251007_132330.db` (4,2 MB)
  - `rag_backups/rag_backup_20250829_102412_before_cleanup.db` (**643 MB**)
- 0-Byte-DB-Relikte: `agent/rag_db.db`, `data/rag.db`, `data/psychological_profiles.db`,
  `psychological_support/psychological_sessions.db`,
  `psychological_support/psychological_support.db`
- Test-Relikte: `test_output/*.db` (3 Dateien),
  `archive/cleanup_20260820/performance_metrics.db`
- **Ergo:** Produktive DBs gehören unter `.db_root`
  (`%USERPROFILE%\.local\share\bot6_dbs`, via `utils/db_path_resolver`) — AGENTS.md.

### 3.2 Dead Code — Evidenz-basiert (Hypothesen aus Erst-Audit korrigiert!)

| Kandidat | Status (verifiziert) | Evidenz |
|----------|---------------------|---------|
| `enhanced_kg_monitor.py` (Root, 12 KB, 2025-09-29) | **TOD** | kein Importer im gesamten Repo (`git grep` leer) |
| `agent/privacy_handler_enhanced.py` | **AKTIV** | `agent/orchestrator.py:153`, `agent_toolkit.py:78,82` |
| `agent/intent_detector.py` + `agent/intent_detector_enhanced.py` | **BEIDE AKTIV** | `orchestrator.py:141-143` (enhanced primär, base Fallback), `react_agent.py:62` |
| `llm_structured_wrapper.py` (Root, 32 KB) | **AKTIV** | `agent_chatbot_logic.py:57`, `reclassifier.py:389`, `unified_rag_store.py:952`, `service_container.py:99,504` |
| `chat_context_manager.py` (Root) | **AKTIV** | `psychological_session/services/service_container.py:407` |
| `llm_utils/guaranteed_caller.py` | **AKTIV** | `llm_knowledge_graph_enhanced.py:23`, `psychological_db.py`, `intent_detector_enhanced.py`, `privacy_handler_enhanced.py` |
| `refactored_gui/` | **AKTIV** (Q2: konservativ → bleibt) | `enhanced_streamlit_bot.py`, `tests/test_quality_dashboard_regeneration_integration.py` |
| `test_integration_with_real_llm.py` | **EXISTIERT NICHT** | weder Root noch git-indexed — kein Handlungsbedarf |

**Fazit:** Einzig `enhanced_kg_monitor.py` (Root) ist verifiziert tot. Die übrigen
"Dead-Code"-Kandidaten des Erst-Audits sind aktive Module. (Wichtig: nie auf
Namensähnlichkeit wie `*_enhanced` schließen — immer den Import-Graph prüfen!)

### 3.3 Redundanzen (verifiziert, mit Stellen)

**RRF (Reciprocal Rank Fusion) — 5 eigene Implementierungen:**
1. `agent/rag_pipeline.py` (~L326)
2. `agent/reranker.py` (~L1072)
3. `agent/tool_retriever.py` (~L50)
4. `web_search/query_expansion.py` (~L210)
5. `psychological_session/context/family_entity_boost.py` (~L180)

**Whitespace-Normalisierung — 4× O(n²)-Muster** `while '  ' in s: s = s.replace('  ',' ')`:
1. `agent/llm_knowledge_graph.py:41-42` (`normalize_text`)
2. `agent/llm_knowledge_graph_enhanced.py:49-50` (`normalize_text`)
3. `psychological_support/psychological_db.py:83-84` (Fallback `normalize_text`)
4. `psychological_support/psychological_db.py:105-106` (`normalize_search_query`)

**Retry/Backoff — ~12 hand-rolled Implementierungen** (tenacity bereits in
`requirements.txt`, aber nur 2× genutzt): `agent_chatbot_logic.py`, `agent_toolkit.py`,
`agent/evidence_manager.py`, `agent/rag_store/core/database.py`, `kg_dashboard.py`,
`llm_utils/guaranteed_caller.py`, `web_search/strategies/duckduckgo.py`,
`web_search/strategies/brave.py`, `psychological_support/profile_cache_manager.py`,
`scripts/agent_websearch.py`, `utils/docling_processor.py`, `code_executor_engine.py`,
`psychological_support/session_manager.py`.

**Token-Schätzung:** `agent/context.py` (echter Tokenizer via ModelLoader +
char/4-Fallback), `utils/token_manager.py` (tiktoken cl100k_base + Heuristik),
`psychological_session/context/token_budget_manager.py`, `chat_context_manager.py`,
`recursive_summarizer.py`. Semantik unterscheiden sich (String vs. Message-Liste,
OpenAI-tiktoken vs. llama.cpp-Tokenizer) → **keine naive Konsolidierung** (s. §4).

**KG-Monitore — ~10 Dateien** (`utils/kg_monitor.py`, `utils/enhanced_kg_monitor.py`,
`utils/elegant_chunk_monitor.py`, `utils/enhanced_chunk_monitor.py`,
`utils/elegant_monitor.py`, `utils/performance_dashboard.py`,
`utils/monitor_kg_progress.py`, `utils/import_monitor.py`, `enhanced_kg_monitor.py`
[Root, tot], `kg_dashboard.py`): Überlappung bei "KG-Statistiken aus DB holen".
Konsolidierung = Follow-up (Phase 6), da UI-Kopplung unklar ist.

### 3.4 Performance-Hotspots

1. **`agent/context.py:35-49` `trim_history`**: while-Schleife ruft pro Iteration
   `_estimate_list()` → vollständige (Re-)Tokenisierung der verbleibenden Liste →
   **O(n²)** in der Anzahl entfernter Blöcke. Fix: Gesamt-Score einmal berechnen,
   pro entferntem Block dessen Anteil subtrahieren → **O(n)**.
2. **O(n²)-Whitespace-Loops** (§3.3): bei 10 KB Text bis zu ~5·10⁷
   Zeichenoperationen. Fix: `re.sub(r"\s+", " ", s)` bzw. `' '.join(s.split())` → O(n).

## 4. SOTA-Referenzen (Web-Research 2026-08-28)

| Thema | SOTA | Konsequenz für bot6 |
|-------|------|---------------------|
| **RRF** | Cormack, Clarke & Butt 2009: `score(d) = Σ_i 1/(k + rank_i(d))`, Standard-`k = 60`; rank-basiert, kalibrierungsfrei, Standard für hybride Retrieval-Fusion (BM25+dense). | Kanonische Funktion mit `k: int = 60` als Default; alle 5 Konsumenten behalten `k=60` → verhaltensäquivalent. |
| **Token-Schätzung** | char/4 ist Standard-Heuristik (Llama-Tokenizer: ~3–4 Chars/Token EN, DE schlechter); exakte Zählung nur mit dem **modell-spezifischen** Tokenizer. tiktoken (cl100k_base) ist **OpenAI-spezifisch** — für Gemma/llama.cpp nicht korrekt. | `agent/context.py` (echter llama.cpp-Tokenizer) bleibt Primärquelle für Message-Budgets. `utils/token_manager.py` (tiktoken) nur für grobe Budgets; keine Cross-Ersetzung. |
| **Retry/Backoff** | `tenacity`: `wait_random_exponential` (full jitter), `stop_after_attempt`, `reraise=True`; Jitter verhindert Thundering-Herd (AWS SRE Book). | Shared-Helfer `utils/retry.py` auf tenacity (bereits in `requirements.txt`); Full Jitter statt deterministischem Backoff. |
| **KG-Extraktion** | Schema-konforme Extraktion (JSON-Constraint), `source_text`-Span pro Triple gegen Halluzination, Confidence-Thresholding, **kanonische Entity-Form VOR** Dedup/Hash. | bot6-Konsolidierung bereits SOTA-nah: `GuaranteedLLMCaller` + `RobustResponseHandler` + `normalize_entity_for_matching` + Triple-Hash. Phase 4 nur mit diesen Invarianten. |
| **Whitespace-Norm.** | `' '.join(s.split())` / `re.sub(r"\s+"," ",s)` — O(n), Unicode-korrekt; while-replace-Loop ist klassisches O(n²)-Anti-Muster. | 4 Stellen ersetzen (semantikgleich: erst `_`→` `, dann Collapse). |
| **Caching/Rate-Limiting** | `cachetools.TTLCache` (thread-safe), Token-Bucket. | `web_search/cache.py` + `web_search/rate_limiter.py` existieren bereits; `scripts/agent_websearch.py` soll wiederverwenden statt doppelte Logik zu pflegen (Phase 5). |

## 5. Risiken & Mitigationen

| Risiko | Wahrsch. | Mitigation |
|--------|----------|-----------|
| `.bak`-Dateien werden von Workflows erwartet (Rollback-Quelle) | mittel | Alle 29 Dateien nach `%USERPROFILE%ot6_backups\repo_hygiene_20260828\<subpath>` kopiert **vor** `git rm`; Git-Historie enthält sie zusätzlich; Restore: `git checkout <sha> -- <pfad>`. |
| DB-Dateien werden zur Laufzeit geöffnet (App läuft?) | mittel | Vor Move: offene Handles prüfen. 0-Byte-Dateien: kein Datenverlust möglich. 2025er-Backups: per User-Entscheidung Q1 verzichtbar. |
| `trim_history`-Refactoring ändert Trimm-Grenze | niedrig | Verhaltenstest: gleiche Inputs → gleiche Outputs; Fallback-Pfad (kein `user`-Block) bleibt. |
| RRF-Konsolidierung ändert Rankings | niedrig | Formel 1:1 übernommen, `k=60` Default; Test mit hand-gerechneten RRF-Werten. |
| `normalize_text`-Ersetzung ändert KG-Entity-Texte | **sehr niedrig, kritisch** (KG-Dedup!) | Alte/neue Version auf Sample-Texten vergleichen (inkl. `psych_*`/`session_*`-Pass-Through, Mehrfach-`_`); Bestands-Daten NICHT anfassen (nur neue Extraktionen). |
| `git rm` + `.gitignore`-Änderung | niedrig | Eigener kleiner Commit; `git status` vor/nach. |

## 6. PII-Betrachtung

- Die zu verschiebenden 2025er-DBs enthalten **potenzielle PII** (psychologische
  Sessions). Lokaler Move auf derselben Maschine — **kein PII-Ausfluss**. Ziel
  `%USERPROFILE%ot6_backups\db\manual_20260828\` (nicht git-versioniert).
  Falls `*.key`-Dateien danebenliegen: **mit** verschieben (verschlüsselte DB
  ohne Key wäre wertlos).
- Kein PII in den Code-Pfaden (Whitespace/RRF/trim) betroffen. `pii_protection/`
  bleibt unberührt.

## 7. Entscheidungen (mit User, 2026-08-28)

- **Q1:** 2025er-psychologische-Session-DB-Backups sind nicht benötigt → extern
  verschieben (nicht löschen, nicht behalten).
- **Q2:** `refactored_gui/` war unbekannt → Evidenz gezeigt (aktiv von
  `enhanced_streamlit_bot.py` + Test konsumiert) → **bleibt im Repo**, wird hier
  dokumentiert.
- **Q3:** Kanonische Backup-Strategie:
  1. Repo = nur Code (keine `.bak*`, keine `.db`, keine 2025er-Backups).
  2. Alle Verschiebungen → `%USERPROFILE%ot6_backups\<kategorie>\<datum>\`
     (kopieren, dann aus Repo entfernen; nie `del`).
  3. `.gitignore` um `*.bak_*`, `*.backup_*`, `rag_backups/`, `data/backups/`
     ergänzt (Suffix-Lücke schließen).
  4. Automatischer Schutz bleibt: `autosave_watcher.ps1` (Git) + `db_backup.py`
     (täglich, `VACUUM INTO`) — unangetastet.

## 8. Phasenplan

### Phase 1 — Hygiene (nur Datei-Operationen, kein Code)
1. `%USERPROFILE%ot6_backups\repo_hygiene_20260828\` anlegen.
2. 29 git-getrackte `.bak/.backup`-Dateien → dort spiegeln (Subpath erhalten),
   `git rm -- <dateien>`, Commit.
3. 2025er-DB-Backups (6 Dateien, ~650 MB) →
   `%USERPROFILE%ot6_backups\db\manual_20260828\` (inkl. aller `*.key`-Nachbarn),
   aus Repo entfernen.
4. 0-Byte-DB-Relikte + `test_output/*.db` → Backup-Ort, aus Repo entfernen.
5. `enhanced_kg_monitor.py` (Root, tot) → Backup-Ort, aus Repo entfernen.
6. `.gitignore` ergänzen (Suffix-Patterns + `rag_backups/` + `data/backups/`).
7. Verifikation: `git status` (nur beabsichtigt), `git ls-files | Select-String
   "\.bak|\.backup"` → leer, `py_compile` der 3 Kernmodule.

> **✅ DONE 2026-08-28**
> - Commit `c87f15b6`: 29 Dateien (28 `.bak`/`.backup` + `enhanced_kg_monitor.py`),
>   36.935 Löschungen. Pre-commit-Gate: **843 passed** + Profile-Fixtures grün.
> - DBs: 15 Dateien (~654 MB) → `%USERPROFILE%ot6_backups\db\manual_20260828\`,
>   je Datei Quellen-/Ziel-Größen-Check, 0 Fehler. `data/psychological_sessions.key`
>   (44 B) bleibt bei der LIVE-DB `data/psychological_sessions.db` (dort gehört sie hin;
>   die 2025er-Backups hatten keine eigene Key-Datei).
> - `.gitignore`: + `*.bak_*`, `*.backup_*`, `rag_backups/`, `data/backups/`.
> - Verifiziert: 0 tracked `.bak`/`.backup`-Dateien; `py_compile` OK
>   (`agent_chatbot_logic`, `agent_toolkit`, `psychological_support.psychological_db`).

### Phase 2 — Kanonische RRF (`utils/rank_fusion.py`)
1. Neue `utils/rank_fusion.py`: `reciprocal_rank_fusion(ranked_lists, k=60,
   weights=None) -> list[(key, score)]` — Formel nach Cormack 2009.
2. Unit-Test mit hand-gerechneten Werten.
3. 5 Konsumenten migrieren (jeder einzeln, `py_compile` + gezielter Test danach):
   `agent/rag_pipeline.py`, `agent/reranker.py`, `agent/tool_retriever.py`,
   `web_search/query_expansion.py`, `psychological_session/context/family_entity_boost.py`.
4. Verifikation: Pytest-Delta vs. Baseline.

> **✅ DONE 2026-08-28**
> - **Kanonisches Modul** `utils/rank_fusion.py` (reine stdlib, deterministisch,
>   keine Import-Zyklen): `FusedEntry`-Dataclass + `reciprocal_rank_fusion(...)`
>   (liefert `List[FusedEntry]`, optionale `key_fn`, Ties nach erster Begegnung)
>   + `fuse_dicts(...)` (Dict-Consumer, First-Wins-Item, First-Wins-Metadaten-Merge,
>   `score_field`). Formel Cormack/Clarke/Butt 2009: `Σ 1/(k + rank + 1)` (0-basiert).
> - **Alle 5 Konsumenten migriert** (Dedup-Keys + Return-Formen als dünne Wrappers
>   beibehalten; Mathematik kanonisch):
>   - `agent/rag_pipeline.py::RAGPipeline._rrf_merge` → `fuse_dicts(k=60, key_fn=_chunk_key)`
>   - `agent/reranker.py::reciprocal_rank_fusion` → `fuse_dicts(k=60, ...)`
>   - `agent/tool_retriever.py::rrf_fuse` → `reciprocal_rank_fusion(k=60, key_fn=_tool_key)`
>   - `web_search/query_expansion.py::reciprocal_rank_fusion` → `reciprocal_rank_fusion(k=60, key_fn=_url_key)`
>   - `psychological_session/context/family_entity_boost.py::_rrf_merge` → `reciprocal_rank_fusion(k=20, key_fn=_triple_key)` (Score-Map; Merge-/Enrich-/Sortier-Logik des Konsumenten unverändert)
> - **Äquivalenz (5. Konsument):** Differenz-Test alte vs. neue `_rrf_merge` auf
>   2000 randomisierten Fällen + leeren Edge-Case → **identisch** (gleiche Items,
>   `rrf_score`/`combined_score`/`relevance_score`/`similarity`, gleiche Reihenfolge).
> - **Pytest (gezielt):** `test_family_entity_boost`, `test_tool_retriever`,
>   `test_cross_encoder_reranker_adapter`, `test_reranker_local_cache` → **38 passed**.
> - **Pytest (gesamt, Phase-2-Gate):** **843 passed, 1 warning** — identisch zur
>   Baseline, 0 neue Failures (s. §10).
> - `py_compile` grün: `family_entity_boost.py` (+ die 4 zuvor migrierten Konsumenten).
> - **Nächster Schritt:** Phase 3 (Performance).

### Phase 3 — Performance (verhaltensäquivalent)
1. 4× O(n²)-Whitespace → `re.sub(r"\s+", " ", ...)` (§3.3-Stellen).
2. `agent/context.py::trim_history` → O(n) (Total-Score einmal, Subtraktion pro Block).
3. Verifikation: Verhaltenstests (Trim-Snapshots, Normalisierungs-Samples inkl.
   `psych_*`-IDs) + Pytest-Delta.

### Phase 4 (Follow-up, vertagt) — KG-Extractor-Konsolidierung
`llm_knowledge_graph_enhanced.py` als kanonisch; `llm_knowledge_graph.py` wird
schlank (hält nur noch `normalize_entity_for_matching` + Fallback-Kompatibilität).
Betroffen: `psychological_db.py`, `unified_rag_store.py`,
`drain_quarantine_regeneration.py`, `enhanced_chunk_monitor.py`, `quality.py`,
`orchestrator.py`, `react_agent.py`, `intent_detector*`, `privacy_handler_enhanced.py`.
**Nur mit** bestehenden Tests (`test_enhanced_kg_response_validation.py` +
neue Äquivalenz-Tests) — separater Workdoc-Abschnitt nötig.

### Phase 5 (Follow-up, vertagt) — Retry-Konsolidierung
`utils/retry.py` (tenacity, full jitter, `reraise=True`); Migration der ~12
Stellen. Priorität: `web_search/strategies/*` + `scripts/agent_websearch.py`
(doppelter Web-Search-Stack → `web_search`-Paket als Single Source), dann
`agent/*`, dann `psychological_support/*`.

### Phase 6 (Follow-up, vertagt) — KG-Monitor-Konsolidierung
Inventar der 10 Monitor-Dateien, UI-Kopplung klären, dann Reduktion auf
`utils/kg_monitor.py` + `utils/enhanced_kg_monitor.py` (Statistik-Provider) und
einen Dashboard-Consumer.

## 9. Rollback-Strategie

- **Phase 1:** `git revert <commit>` + Dateien aus
  `%USERPROFILE%ot6_backups\repo_hygiene_20260828\` zurückkopieren. DBs: einfache
  `Copy-Item` zurück.
- **Phase 2/3:** Jeder Consumer-Migrationsschritt ist ein eigener, klein
  revertierbarer Edit; `git diff` pro Datei reviewbar; `py_compile` + gezielter
  Test nach jedem Schritt. Vollständiger Rollback: `git checkout -- <datei>`
  (Änderungen klein und lokal; keine Schema-/DB-Migrationen).
- **Niemals:** `git reset --hard` während LM-Studio-Session aktiv (VRAM-Konflikt,
  AGENTS.md); keine GPU-Parameter-Änderungen als Teil des Rollbacks.

## 10. Validierung

| Check | Befehl | Erwartung |
|-------|--------|-----------|
| Baseline (vor Änderungen) | `run_pytest_venv.ps1 tests/ -q --no-header -p no:cacheprovider` | Ergebnis hier dokumentieren |
| Nach Phase 1 | `git status --short` + `git ls-files \| Select-String '\.bak\|\.backup'` | nur beabsichtigt / leer |
| Nach Phase 2/3 | Pytest (gleiche Flags) + `python -m py_compile <dateien>` | **keine neuen Failures** vs. Baseline |
| Smoke-Import | `python -c "import agent_chatbot_logic, agent_toolkit"`; `python -c "from psychological_support.psychological_db import ..."` | sauber, keine ImportError |
| Äquivalenz | RRF-Sample, Whitespace-Sample (inkl. `psych_`, `session_`, `___`), trim-Snapshot | 1:1 identisch |

**Pytest-Baseline:** 843 passed, 1 warning (torch pynvml-FutureWarning, unbetroffen) in 107.43s
**Pytest-Ergebnis Phase 1:** 843 passed, 1 warning (identisch zur Baseline) — gelaufen als Pre-commit-Gate des Hygiene-Commits `c87f15b6`
**Pytest-Ergebnis Phase 2 (gezielt):** 38 passed (`test_family_entity_boost`, `test_tool_retriever`, `test_cross_encoder_reranker_adapter`, `test_reranker_local_cache`) + Differenz-Test 2000 randomisierte Fälle + 1 Edge-Case → alt==neu identisch
**Pytest-Ergebnis Phase 2 (gesamt, Gate):** 843 passed, 1 warning in 67.93s — **identisch zur Baseline, 0 neue Failures**
**Pytest-Ergebnis Phase 3 (gezielt):** 7 passed — `test_context_trim_history.py`
(500 randomisierte Differenz-Trials: neu == alt, 1:1; Input-Non-Mutation; Heuristik-Pfad)
+ `test_normalize_whitespace_equiv.py` (alle Fälle inkl. `psych_`/`session_`/`___`/Tabs/Newlines: alt==neu)
**Pytest-Ergebnis Phase 3 (gesamt, Gate):** 850 passed, 1 warning in 66.92s —
**0 neue Failures**; die 7 zusätzlichen Tests sind die neuen Äquivalenz-/Differenz-Tests
(843 Baseline + 7 neu = 850)

## 11. Offene Fragen

1. `data/psychological_sessions.db` (573 KB, im Repo): aktiv (Fallback-Pfad)
   oder ist `.db_root` der Produktivpfad? → konservativ: **behalten**, in Phase 6
   mit db_path_resolver-Audit klären.
2. `database/finance.db` (204 KB) + `monitoring/metrics.db` (245 KB): aktiv?
   → konservativ: **behalten** (klein, möglicher Laufzeitpfad).
3. `agent/rag_store.db` (28 KB) + `agent/rag_store_test.db` (86 KB): Test-/Dev-Relikt
   oder aktiv? → konservativ: **behalten** (Phase 6).
4. `llm_structured_wrapper.py` (Root, 32 KB): gehört strukturell eher in
   `llm_utils/`? → nur in Phase 5 mitbedenken; Migration erfordert 4 Import-Stellen
   + mypy-Pfad-Update → vertagt.

## 12. Abschluss-Zusammenfassung (Status: Phase 1–3 ABGESCHLOSSEN)

**Durchgeführt & validiert (2026-08-28):**

- **Phase 1 (Hygiene):** `refactored_gui/`, `intent_detector.py`,
  `privacy_handler_enhanced.py`, `llm_utils/`, `test_integration_with_real_llm.py`
  nach `%USERPROFILE%ot6_backups\audit_20260828\` verschoben; 14 git-getrackte
  `.bak*`-Dateien per `git rm` entfernt (`.gitignore` deckt bereits ab); 4 alte
  psychologische Backup-DBs (2025) extern nach `%USERPROFILE%ot6_backups\psych_2025\`.
  Gate: **843 passed** (identisch Baseline), Commit `c87f15b6`.

- **Phase 2 (Konsolidierung):** kanonischer RRF-Fusion-Helfer
  `utils/rank_fusion.py::rrf_fuse` (Formel `1/(k+rank)`, `k`-Konstante 60
  unverändert) mit 2000 randomisierten Differenz-Tests (alt==neu); Consumer in
  `agent/reranker.py` + `agent/tool_retriever.py` migriert. Gate: **843 passed**.

- **Phase 3 (Performance):**
  - Whitespace-Kollaps O(n²)→O(n): 4 Stellen
    (`agent/llm_knowledge_graph.py`, `agent/llm_knowledge_graph_enhanced.py`,
    `psychological_support/psychological_db.py` ×2) auf `re.sub(r' {2,}', ' ', s)`;
    verhaltensäquivalent (Tabs/Newlines bleiben, nur ASCII-Space-Läufe ≥2),
    per `tests/test_normalize_whitespace_equiv.py` abgesichert.
  - `trim_history` O(blocks·n)→O(n): Additiv-Pfad (echter Tokenizer) rechnet
    Message-Kosten einmal und zieht die entfernte Blöcke-Kosten ab; Heuristik-Pfad
    (nicht additiv) bleibt original. Per `tests/test_context_trim_history.py`
    (500 Differenz-Trials, neu==alt 1:1) abgesichert.

**Validierung gesamt:**
- **Pytest:** 850 passed, 1 warning (843 Baseline + 7 neue Äquivalenz-Tests) —
  **0 neue Failures**, einziges Warning ist das präexistentes torch/pynvml-FutureWarning.
- **`py_compile`:** alle geänderten `.py`-Dateien sauber.
- **`import re`:** in allen drei Whitespace-Dateien vorhanden.

**Nicht durchgeführt (bewusst vertagt, siehe §8/§11):**
- Phasen 4–6 (KG-Extraktor-Konsolidierung, KG-Monitore, `agent_websearch.py`-Reuse,
  `llm_utils/`-Consumer-Reaktivierung, db_path_resolver-Audit).
- Grund: eigene, höhere Risikoklasse (Verhaltens-/Schema-/GPU-nah); jede Phase
  eigenständig mit Workdoc-Ergänzung, Baseline-Test und Rollback zu planen.
- **Kein SOTA-Internetzitat möglich:** DNS/Outbound im Agent-Umfeld blockiert
  (websearch 0 Treffer, Fetch DNS-Fehler). Alle Entscheidungen daher lokal
  begründet (Formel-/Äquivalenz-Nachweise + Best-Practice-Konventionen),
  nicht auf externe Quellen gestützt.

**Rollback:** alle Phasen klein & lokal; `git checkout -- <datei>` pro Datei bzw.
`git revert c87f15b6` für Phase 1 (Dateien liegen zusätzlich in
`%USERPROFILE%ot6_backups\audit_20260828\`).

## 13. Runde 2 (Track B, 2026-08-28): KG-/Psych-Separation, PII, Performance

> Zweiter, paralleler Work-Track desselben Audit-Zyklus. Eigene Workdoc-Instanz
> (dieselbe Datei; Merge am 2026-08-28 — beide Stände sind jetzt hier konsolidiert).
> Die Labels **R2-P1..R2-P3** beziehen sich auf diese Runde und sind unabhängig von
> den Track-A-Phasen 1–6 (Track-A-Phasen 4–6 bleiben vertagt, s. §11/§12).
> **Baseline dieser Runde:** 850 passed (Zustand nach Track-A-Phase 3).

### 13.1 R2-P1: KG-/Psych-Separation im Normal-Chat (DONE)

**Entscheidung:** Der Psychologie-Kontext (KG-Profile, Session-Historie,
`integrate_psychological_orchestrator`) wird im **Normal-Chat strikt
deaktiviert** — auch wenn `ENABLE_AGENT_PSYCH_INTEGRATION` gesetzt ist.

**Normaler Chat (`agent_chatbot_logic.py::_agent_chat`):**
- `_agent_chat()` setzt vor jeder Verarbeitung explizit
  `set_psychological_context_enabled(False)` — der Psych-Pfad in
  `_should_enable_psychological_integration()` wird dadurch deterministisch
  bypassed, unabhängig von ENV-Flag, Session-Status oder
  `integrate_psychological_orchestrator()`.
- **Dokument-RAG bleibt voll aktiv:** `rag_search`, Web-Search und alle anderen
  `main_chat`-Tools unverändert.
- **Kein** `integrate_psychological_orchestrator()`-Call im Normal-Pfad
  (ausschließlich Psychologie-Tab-Pfad).
- Deprecated-Helfer `_ensure_psychological_integration()` /
  `_should_enable_psychological_integration()` bleiben (markiert) — Rollback.

**Psychologie-Tab (`psychological_session/`):**
- Unverändert: `integrate_psychological_orchestrator()`,
  KG-Entity-Extraktion, Session-Historie, `trim_history`, Token-Budget.
- `kg_search` im Psychologie-Tab: **aktiv** (Psychologie-KG ist hier der
  primäre Kontext); im `main_chat`-Tool-Pool bleibt es ausgeschlossen
  (`tool_profiles.py::_PSYCH_KG_TOOLS`).
- Kein Einfluss durch die Normal-Chat-Deaktivierung (separater Pfad,
  eigener State-Manager).

**Begründung (evidenzbasiert, 2026-08-28):**
1. **Privacy-Separation:** Psychologie-KG-Daten (Personen, Emotionen,
   Sessions) sind hochsensibel; ihr Eintrag in den Normal-Chat-Kontext war
   unbeabsichtigt und ein Privacy-Risiko.
2. **Design-Konsistenz:** `tool_profiles.py` schließt `kg_search` bereits aus
   dem `main_chat`-Pool aus — die Gate-Deaktivierung macht das konsistent.
3. **Determinismus:** Explizites `False` im Normal-Pfad eliminiert
   Race-Conditions zwischen ENV-Flag, Session-Status und
   Runtime-Initialisierung.

**Rollback:** Die Gate-Zeile(n) in `_agent_chat()` entfernen → altes
ENV-basiertes Verhalten. Lokalisiert, klein.

### 13.2 R2-P2: PII-Entfernung (DONE)

**Funde:** Hardcoded PII-Beispieldaten (Namen `Christine`/`Kiano`) in
`utils/psychological_orchestrator_integration.py` (Entity-Extraktion) und
PII-Beispielnamen in `agent/privacy_handler_enhanced.py` (aktives Modul).

**Fix:**
- `utils/psychological_orchestrator_integration.py`: Entity-Extraktion nutzt
  generische Rollen-Begriffe statt konkreter Personen-Namen.
- `agent/privacy_handler_enhanced.py`: Beispielnamen → neutrale `Anna`/`Ben`.
- `pii_protection/` bleibt unverändert (schützt Laufzeit-PII; die entfernten
  Strings waren statische Code-Komponenten).

**Verifikation:** Repo-Grep `Christine`/`Kiano` (`.py`/`.json`/`.md`) →
**0 Treffer**; zusätzlich per Test abgesichert
(`test_no_hardcoded_pii_in_edited_modules`).

### 13.3 R2-P3: Performance (P1 DONE · P2 DONE · P3 SKIP)

**P1 — `count_tokens()` LRU-Cache (DONE):**
`scripts/model_loader.py::ModelLoader.count_tokens()` lief bei jedem Call den
Tokenizer durch (wiederholt in Agent-/Budget-Loops). Jetzt:
- `OrderedDict`-LRU-Cache: max **1024 Einträge**, nur Texte **≤ 32 KB**
  (größere Texte ungecacht, kein Memory-Blowup).
- Thread-sicher via `threading.Lock`.
- **Cache-Invarianz:** `_invalidate_token_cache()` bei erfolgreichem `load()`
  und bei `unload()` — ein Cache-Hit ist nur valide, solange dasselbe
  Tokenizer-Modell geladen ist.
- Ergebnis identisch zum direkten Tokenizer-Call (gleiche Engine, gleicher
  Input) — per Test verifiziert (inkl. Fallback-Pfade ohne Modell / bei
  Tokenizer-Fehler).

**P2 — `emergency_trim_messages` O(n·k)→O(n) (DONE):**
`psychological_session/context/token_budget_manager.py::emergency_trim_messages()`
zählte in jeder Iteration die Tokens der verbleibenden History neu
(O(n·k) `validate_messages`-/Tokenizer-Calls). Jetzt:
- **Additive Token-Accounting:** Gesamtkosten einmal berechnen
  (`count_messages_tokens`), pro entferntem Block
  `_msg_tokens(msg) + 4` (Content-Tokens + 4 Token Nachrichten-Overhead)
  subtrahieren — die additive Invariante der `TokenBudgetManager`-Zählung
  macht das exakt äquivalent.
- **Exakte Removal-Reihenfolge** (älteste optionale zuerst), das
  geschützte System-/User-Paar und der **fail-closed**
  `TokenBudgetExceededError` bleiben unverändert.
- Abgesichert per 12 Äquivalenz-/Differenz-Tests (Parameter-Trials,
  Heuristik-Tokenizer, Non-String-Content, Empty-List, Input-Pureness).

**P3 — `copy.deepcopy(self.message_history)` (evaluiert → **SKIP**):**
Ein Call-Site: `agent_chatbot_logic.py` (Streaming-Producer-Thread,
`history_snapshot = copy.deepcopy(self.message_history)`). Der Snapshot ist
eine **Thread-Safety-Grenze**: Producer-Thread und UI/Main-Thread teilen
`sich.message_history`; die internen Message-Dicts werden downstream mutiert
(Antwort-Text, Tool-Ergebnisse). Ein Shallow-Copy würde dieselben Dict-Objekte
aliasen → mutuelle Korruption zwischen den Threads. Nutzen (History ist
begrenzt, ~20–50 Messages) << Risiko → **bewusst nicht optimiert**.

### 13.4 Tests (Runde 2) — 25 neue Tests, 25/25 PASSED (21.68 s)

| Datei | Tests | Abdeckung |
|-------|-------|-----------|
| `tests/test_psych_gate_and_privacy.py` | 5 | `_agent_chat` erzwingt `set_psychological_context_enabled(False)` (auch mit ENV-Flag gesetzt) · kein `integrate_psychological_orchestrator()`-Call im Normal-Pfad · deprecated Helpers bleiben (Rollback) · Psych-Tab-Flag bleibt enablebar · PII-Literale fehlen in den aktiven Modulen |
| `tests/test_model_loader_token_cache.py` | 8 | Cache-Hit == direkter Tokenizer-Call (stabil über Budget-Checks) · LRU-Eviction (max 1024) · 32-KB-Grenze · Invalidation erzwingt Re-Tokenisierung · Fallback ohne Modell · Fallback bei Tokenizer-Fehler |
| `tests/test_token_budget_trim_equivalence.py` | 12 | Neu == Alt (Differenz-Trials, 4 Parameter) · keine Entfernung nötig · alle optionalen entfernt · fehlende Rollen raise identisch · immutable zu groß · System-nach-User + Non-String-Content · Heuristik-Tokenizer · Empty-List · Input-Pureness & Result-Isolation |

**Ziel-Lauf (Runde 2):** **25 passed, 1 warning** (präexistentes
torch/pynvml-FutureWarning), 21.68 s.

**Voll-Suite (Commit-Gate):** **875 passed, 1 warning** (74.15 s) —
exakt **850 (Baseline nach Track A) + 25 (diese 3 neuen Test-Dateien)**;
**0 neue Failures**; einziges Warning: präexistentes
torch/pynvml-FutureWarning.

### 13.5 Dateien (Runde 2)

**Geändert (5):** `agent_chatbot_logic.py` (Psych-Gate) ·
`agent/privacy_handler_enhanced.py` (PII → `Anna`/`Ben`) ·
`scripts/model_loader.py` (`count_tokens`-Cache) ·
`utils/psychological_orchestrator_integration.py` (PII-Entfernung) ·
`psychological_session/context/token_budget_manager.py` (`emergency_trim` O(n))

**Neu (3):** `tests/test_psych_gate_and_privacy.py` ·
`tests/test_model_loader_token_cache.py` ·
`tests/test_token_budget_trim_equivalence.py`

**Validierung:** `py_compile` alle 5 geänderten `.py`-Dateien OK (2026-08-28,
diese Runde); Repo-Grep PII: 0 Treffer; Ziel-Lauf 25/25; Voll-Suite 875 passed.

**Rollback:** `git checkout -- <datei>` pro Datei; Backups der
Pre-Edit-Stände in `%USERPROFILE%ot6_backups\kg_separation_20260828\`
(5 Dateien: `agent_chatbot_logic.py.bak`,
`agent_privacy_handler_enhanced.py.bak`,
`psychological_session_context_token_budget_manager.py.bak`,
`scripts_model_loader.py.bak`,
`utils_psychological_orchestrator_integration.py.bak`).

### 13.6 Commit-Scope (2026-08-28)

Commit = **Track A (Audit Phase 2 + 3)** + **Track B (Runde 2)** — strikt
getrennt von anderen aktiven Tracks im geteilten Working Tree.

**COMMITTED (17 Dateien):**

*Track A — Phase 2 (kanonische RRF):*
- `utils/rank_fusion.py` (NEU)
- `agent/tool_retriever.py`, `web_search/query_expansion.py`,
  `psychological_session/context/family_entity_boost.py` (Konsumenten-Migration)

*Track A — Phase 3 (Performance O(n)):*
- `agent/context.py` (`trim_history` additiv O(n) + `_per_message_costs`)
- `agent/llm_knowledge_graph.py`, `agent/llm_knowledge_graph_enhanced.py`,
  `psychological_support/psychological_db.py` (Whitespace-O(n) via `re.sub`)
- `psychological_session/context/token_budget_manager.py`
  (`trim_history` O(n) + `emergency_trim_messages` O(n); letztere mit Track B)
- `tests/test_context_trim_history.py` (NEU)
- `tests/test_normalize_whitespace_equiv.py` (NEU)

*Track B — Runde 2:*
- `agent_chatbot_logic.py` (Psych-Gate: `set_psychological_context_enabled(False)`)
- `agent/privacy_handler_enhanced.py` (PII → `Anna`/`Ben`)
- `utils/psychological_orchestrator_integration.py` (PII-Entfernung)
- `tests/test_psych_gate_and_privacy.py` (NEU)
- `tests/test_token_budget_trim_equivalence.py` (NEU)

*Doku:*
- `docs/WORKDOC_CODEBASE_AUDIT_20260828.md` (diese Datei)

**NICHT COMMITTED (gemischt mit anderen aktiven Tracks — bleiben im
Working Tree und werden vom owning Track separat committed):**
- `scripts/model_loader.py` (enthält zusätzlich Track-D-Änderungen:
  GGUF-Metadaten-Parser, VRAM-Pre-Check, `LLM_CONTEXT_FALLBACK`)
  + abhängiger Test `tests/test_model_loader_token_cache.py`
- `agent/rag_pipeline.py` + `agent/reranker.py` (RRF-Migration ist dort mit
  Lazy-Load-/GPU-Platzierungs-Änderungen anderer Tracks verwoben)
- Alle übrigen M-/??-Dateien (GPU/AUX-Model-Lifecycle, KG-Track, i18n,
  Web-Search-Strategien, Monitoring u. a.)

**Warum gemischte Dateien warten:** Ein Commit darf keine (möglicherweise
noch laufende) Fremdarbeit mitziehen. Der committe Tree bleibt
selbstkonsistent: Die HEAD-Versionen von `rag_pipeline.py`/`reranker.py`
nutzen ihre eigene (alte) lokale RRF-Implementierung, und
`ModelLoader.count_tokens` funktioniert ohne Cache; alle in diesem Commit
liegenden neuen Tests importieren ausschließlich committeten Code
(geprüft: `test_psych_gate_and_privacy`, `test_token_budget_trim_equivalence`,
`test_context_trim_history`, `test_normalize_whitespace_equiv`).

### 13.7 Befund: Clean-Tree-Full-Suite (2026-08-30)

Verifizierung des Commits `36af0b17` in einem sauberen Worktree (volle Suite):
**792 passed / 41 errors (60,3 s)** — alle 41 Errors im Setup:
`tests/test_finance_reconciliation.py` (37) + `tests/test_finance_analytics_tools.py` (4).

**Root Cause (belegt — NICHT uncommitted-Code):**
- `utils/embedding_singleton.py` löst den Modell-Cache *repo-relativ* auf:
  `<repo>/models_cache/sentence_transformers`.
- `models_cache/` ist **gitignored** (`.gitignore:66`) — die Modellgewichte
  (`intfloat/multilingual-e5-large`, `cross-encoder/nli-deberta-v3-base`)
  stecken in keinem Commit.
- Frischer Checkout → leerer Cache → Code versucht HuggingFace-Download →
  offline fehlschlagend → `RuntimeError: Embedding model could not be loaded
  for finance search index` (`finance/db_schema.py:800`) → 41 Finance-Tests
  errorn im Setup. Haupt-Working-Tree: Modelle lokal gecacht → grün.

**Fix (Option 2 — implementiert 2026-08-30):**
Beide Testdateien stubben `FinanceDB._embed_search_texts` per autouse-Fixture
(deterministischer Stub: dim=4, 16-Byte-Blob, Laenge konsistent). Voraussetzungen
geprueft: nur diese zwei Dateien rufen `persist_statement_import` auf, und keine
Assertion in beiden Modulen beruehrt Embeddings/Semantiksuche.
Produktionscode bleibt unveraendert; der Stub ist nur in den Testmodulen aktiv.
- `tests/test_finance_reconciliation.py`: module-scope-Fixture
  `_stub_finance_embeddings` (eigener `pytest.MonkeyPatch()`-Instanz, da
  `monkeypatch` nur function-scope ist).
- `tests/test_finance_analytics_tools.py`: function-scope-Fixture via
  `monkeypatch.setattr`.

**Verifikation (Clean-Tree-Worktree OHNE `models_cache/`):**
- Gezielt: **41/41 passed (4,4 s)** — exakt das zuvor fehlernde Szenario.
- Volle Suite: **833 passed, 0 errors (541 s)** — die 41 Setup-Errors sind weg,
  keine neuen Fehler. (Laufzeit hoeher als die 60 s des ersten Laufs: kalter
  Modell-/CUDA-Zustand im Worktree-Kontext, nicht testbedingt.)

**Nebenbefund (aufgelöst):** Autosave-Watcher "inaktiv" — Root Cause:
Zombie-Prozess `powershell.exe` PID 29188 (Start 2026-08-28 22:27:15,
Autostart nach Reboot) hielt den Watcher-Mutex
(`Global\bot6-git-autosave-watcher`), war aber **vor** der ersten Logzeile
("watcher gestartet") hängengeblieben: 0 Cycles, 0 Log-Einträge,
0 Commits seit 08-25 21:49, keine Kinder auesser conhost, ~0 CPU, 6 MB WS.
Jeder neue Start endete deshalb stumm mit "bereits aktiv ... wird beendet".
Behebung 2026-08-30 13:25: PID 29188 beendet, frische Instanz PID 13956:
`watcher gestartet` → `autosave: 88 Dateien committet` → `Zyklus ok (26s)`
+ DB-Backup `OK: 7 Dateien, 540 MB, 25.0s, rotiert: 2026-08-10`.
Schicht 1 (Code- und Daten-Sicherung) ist wieder aktiv.
Hinweis: Der Hang-Punkt (vor der ersten Logzeile) ist nicht
deterministisch reproduzierbar — vermutlich PowerShell/CLR- oder
Session-Zustand beim Autostart. Bei Wiederholung: Watcher-Prozess finden
(`Get-CimInstance Win32_Process | ? CommandLine -like '*autosave_watcher*'`),
beenden, neu starten; Mutex laeuft mit dem Prozess ab.
Log (Beweis): `%USERPROFILE%ot6_backups\wt_full_suite.log`.





