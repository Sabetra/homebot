<!-- last-verified: 2026-09-16 -->
# Funktionen.md – Große & Komplexe Funktionen des Projekts

> **Zweck:** Diese Datei fasst alle besonders großen/komplexen Funktionen zusammen, damit sie bei späteren Aufgaben schnell verstanden und bearbeitet werden können.
> **Stand:** 2026-08-28 (Selektiver AUX-GPU-Modell-Lifecycle) | **LLM:** Gemma4 12B | **System:** Windows 11, Dual-GPU (RTX 4090 LLM + RTX 3060 Ti AUX), 64GB RAM

---

## 1. `agent/orchestrator.py` – Central Orchestrator

### 1.1 `run_tools_and_summarize()`
**Zentraler Tool-, Evidence- und Antwortpfad; die grossen Teilablaeufe sind in Helper extrahiert.**

| Aspekt | Detail |
|--------|--------|
| **Zweck** | Executiert den gesamten Tool-Execution + Summarization + Verification Pipeline-Schritt |
| **Input** | `query`, `planned_calls`, `history`, optional `reasoning`, `critique`, `planner_ms`, `planner_raw` |
| **Output** | `FinalAnswer(text, sources, trace, followup_questions, graphics)` |
| **Phasen** | 1) Finance-Query-Erkennung → 2) Tool-Ergebnisse sammeln → 3) RAG-First Gating → 4) Hybrid Source Fusion → 5) Date-Validation → 6) Summarizer → 7) Verifier → 8) Fallback |
| **Kritische Sub-Komponenten** | `QueryStrategyManager`, `EvidenceDeduplicator`, `UnifiedRAGStore`, `VerificationManager`, `FallbackManager` |
| **Sonderfall** | Finance-Tools liefern einen Grounding-Block; Planung und Fortsetzungsentscheidung verwenden die typisierten Finance-Vertraege |
| **Hybrid Fusion** | Wenn RAG-First sagt INSUFFICIENT + Web-Suche läuft → beides wird kombiniert (K3-Szenario), wenn RAG-Score >= 0.60 |

**Wichtige interne Logik:**
- `_collect_all_tool_results()` sammelt alle Ergebnisse und filtert duplicates
- `_execute_summarizer_phase()` ruft LLM für Text-Zusammenfassung auf
- `_execute_verifier_phase()` validiert die Antwort (optional, wenn Verifier aktiv)
- `_execute_fallback_phase()` wenn summarizer+verifier nicht helfen
- `_finalize_answer()` baut den FinalAnswer mit Citations + Sources + Follow-ups
- Fallback-Kette: `fallback_summarize()` → `fallback_finance_summarize()` → `deterministic_finance_text()`
- SOTA-Root-Cause-Fix 2026-07-13: `MultimodalRAG` expandiert Queries jetzt vor Retrieval (`_expand_query_for_multimodal()`, ~1277), `StrixKAT` bewertet Antworten nach Synthese (`_evaluate_sota_answer_quality()`, ~1293), und die doppelte späte `_run_sota_enhancement()`-Ausführung wurde entfernt

### 1.1a `_execute_tools_with_rag_postprocessing()` (neu extrahiert, 2026-07-13)

| Aspekt | Detail |
|--------|--------|
| **Zweck** | Entkoppelt Tool-Ausführung + RAG-Nachverarbeitung aus `run_tools_and_summarize()` in eine klar benannte Teilfunktion |
| **Input** | `query`, `planned_calls`, `trace`, `skip_web_search`, `rag_first_results`, `rag_result_count`, `rag_max_score` |
| **Output** | `(planned_calls, results, finance_grounding_block, early_answer)` |
| **Kern-Logik** | 1) Web-Tool-Plan-Anpassung bei RAG-first 2) Tool-Execution-Logging 3) deterministische Finance- und Grafik-only-Short-Circuits 4) Hybrid Source Fusion 5) Date-Validation 6) async Web→RAG Persist 7) RAG-Follow-up-Execution 8) Tool-Trace-Update |
| **Root-Cause-Nutzen** | Reduziert Monolith-Komplexität und verhindert, dass bereits erzeugte lokale Diagramme durch sachfremde RAG-/Web- und Quellenvalidierungsschleifen blockiert werden |

### 1.1f `_collect_graphics()` (2026-07-25)

| Aspekt | Detail |
|--------|--------|
| **Zweck** | Normalisiert Grafikresultate aus `create_diagram`, `canvas` und `code_executor` in einen stabilen Antwortvertrag |
| **Input** | `List[ToolResult]` mit `raw_payload`, Dateipfad oder `plot_base64` |
| **Output** | Liste aus Datei- oder Base64-Artefakten mit MIME-Typ, Caption, Diagrammtyp und Backend |
| **Invariante** | Jedes `GraphicArtifact` besitzt genau einen Payload (`path` XOR `data_base64`) |
| **UI-Pfad** | `FinalAnswer.graphics` → `AgentChatbotLogic.last_graphics` → `ChatRunResult.graphics` → `chat_tab._render_graphics()` → History/SQLite |

### 1.1g Nutzerprogramm-Auslieferung (2026-07-25)

| Aspekt | Detail |
|--------|--------|
| **Zweck** | Trennt ephemeren internen Python-Code von einem durch den User angeforderten, wiederverwendbaren Programm |
| **Aktivierung** | `code_executor` mit `deliver_to_user=true`, `artifact_name`; bei GUI/Spiel zusaetzlich `detached=true` |
| **Invariante** | Nur die letzte erfolgreich ausgefuehrte Codeversion wird gespeichert; Fehler und Security-Blocks erzeugen keinen Download |
| **UI-Pfad** | `ExecutionResult.files` → `FinalAnswer.files`/REACT-Artefakt → `ChatRunResult.files` → `chat_tab._render_files()` → Download und SQLite-History |
| **Sicherheit** | UI akzeptiert nur aufgeloeste Pfade unter `code_sandbox`, begrenzt Downloads auf 20 MiB und zeigt Python-Quellcode optional an |

### 1.1b `_build_tool_summaries_and_trace_artifacts()` (neu extrahiert, 2026-07-13)

| Aspekt | Detail |
|--------|--------|
| **Zweck** | Kapselt den kompletten Extras-/Tool-Summary-/Trace-Artefakt-Aufbau aus dem Hauptpfad |
| **Input** | `query`, `results`, `reasoning`, `finance_grounding_block`, `trace` |
| **Output** | `extras: List[str]` |
| **Kern-Logik** | 1) Tool-Summaries normalisieren 2) Planner-Reasoning/Finance-Grounding als Extras anhängen 3) Multiquery-Summary ergänzen 4) Detaillierte `trace.tool_results`-Struktur aufbauen 5) `trace.extras_count` setzen |
| **Root-Cause-Nutzen** | Reduziert die Entscheidungsdichte in `run_tools_and_summarize()` und macht Debug-/Trace-Logik separat testbar |

### 1.1c `_select_and_enrich_evidence()` (neu extrahiert, 2026-07-13)

| Aspekt | Detail |
|--------|--------|
| **Zweck** | Bündelt Evidence-Selection, Enhanced-Retrieval-Fallback und Source-Validation-Trace in einem klaren Schritt |
| **Input** | `query`, `results`, `history`, `trace` |
| **Output** | `(sources, evidence_result)` |
| **Kern-Logik** | 1) `EvidenceManager.select_evidence_from_tool_results()` 2) bei zu wenig Quellen optional `_rag_enhanced()` 3) `trace.source_validation` befüllen 4) Evidence-Summary-Logging |
| **Root-Cause-Nutzen** | Trennt Retrieval-Evidenz-Orchestrierung vom Hauptpfad und reduziert Komplexitäts-/Fehleroberfläche bei weiteren SOTA-Änderungen |

### 1.1d `_apply_post_evidence_refinement()` (neu extrahiert, 2026-07-13)

| Aspekt | Detail |
|--------|--------|
| **Zweck** | Kapselt den kompletten Post-Evidence-Entscheidungsblock vor der Finalgenerierung |
| **Input** | `query`, `sources`, `results`, `trace` |
| **Output** | `(sources, use_fallback)` |
| **Kern-Logik** | 1) Fallback-Entscheid + Heuristik-Trace 2) optionaler `IRCoT`-Loop 3) best-effort `_run_sota_enhancement()` inkl. Source-Optimierung/Metric-Trace |
| **Root-Cause-Nutzen** | Entflechtet einen besonders risikoreichen Kontrollfluss (Fallback/IRCoT/SOTA) aus `run_tools_and_summarize()` und macht ihn isoliert wartbar/testbar |

### 1.1e `_populate_source_observability()` (neu extrahiert, 2026-07-13)

| Aspekt | Detail |
|--------|--------|
| **Zweck** | Zentralisiert Observability-Metadaten aus finalen Sources vor der Antwortgenerierung |
| **Input** | `sources`, `trace` |
| **Output** | `None` (mutiert `trace`) |
| **Kern-Logik** | 1) `trace.evidence_domains` aus Source-URLs ableiten 2) optionale `trace.rag_stats` aus RAG-Store lesen |
| **Root-Cause-Nutzen** | Entfernt wiederholte Trace-Befüllung aus dem Hauptpfad und reduziert Copy/Paste-Risiko bei weiteren Orchestrator-Cuts |

### 1.1f `_run_rag_first_gating()` (neu extrahiert, 2026-07-13)

| Aspekt | Detail |
|--------|--------|
| **Zweck** | Kapselt den kompletten RAG-first-Gating-Entscheid vor Tool-Ausführung |
| **Input** | `query`, `planned_calls`, `initial_skip_web_search` |
| **Output** | `(skip_web_search, rag_first_results, rag_result_count, rag_max_score)` |
| **Kern-Logik** | 1) Eligibility (`web_search` geplant, kein explizites RAG, nicht zeitkritisch) 2) optionales RAG-first Retrieval (mit/ohne Gap-Detection) 3) Qualitätsmetriken 4) LLM- oder Heuristik-Entscheid für Skip-Web |
| **Root-Cause-Nutzen** | Entlastet `run_tools_and_summarize()` vom größten Kontrollfluss-Block und macht Gating-Logik isoliert überprüfbar |

### 1.1g `_apply_retrieval_route_with_trace()` (neu extrahiert, 2026-07-13)

| Aspekt | Detail |
|--------|--------|
| **Zweck** | Bündelt Retrieval-Routing und konsistente Trace-Anreicherung in einem Schritt |
| **Input** | `query`, `planned_calls`, `trace` |
| **Output** | `(route_decision, planned_calls)` |
| **Kern-Logik** | 1) `_decide_retrieval_route()` 2) `_apply_retrieval_route()` 3) `trace.source_validation` um Routing-Metadaten erweitern 4) effektive Tool-Route loggen |
| **Root-Cause-Nutzen** | Entfernt wiederholte Routing-Detailverdrahtung aus dem Hauptpfad und reduziert Drift-Risiko zwischen Routing und Trace |

### 1.1h `_apply_security_guard()` (neu extrahiert, 2026-07-13)

| Aspekt | Detail |
|--------|--------|
| **Zweck** | Zentralisiert Security-Validierung/Sanitization und optionales Early-Return bei blockierten Queries |
| **Input** | `query`, `trace` |
| **Output** | `(query, Optional[FinalAnswer])` |
| **Kern-Logik** | 1) `security_manager.validate_input()` 2) Block-Response bei Invalid-Input 3) Sanitization übernehmen 4) Security-Warnings loggen |
| **Root-Cause-Nutzen** | Entkoppelt sicherheitskritische Guard-Logik vom Hauptfluss und macht die Early-Exit-Semantik explizit testbar |

### 1.1i `_post_generation_housekeeping()` (neu extrahiert, 2026-07-13)

| Aspekt | Detail |
|--------|--------|
| **Zweck** | Bündelt Post-Generation-Querschnittsaufgaben (Persist, Trace-Snapshots, Observability-Log) |
| **Input** | `sources`, `trace`, `evidence_result` |
| **Output** | `None` |
| **Kern-Logik** | 1) best-effort Persistierung selektierter Quellen nach RAG 2) Snapshot zentraler Generation-Parameter im Trace 3) kompaktes `orchestrate_done`-Logging |
| **Root-Cause-Nutzen** | Reduziert „Tail-Complexity" im Hauptpfad und hält nicht-funktionale Nacharbeiten konsistent an einer Stelle |

### 1.1j `_apply_hybrid_reasoning_validation()` (neu extrahiert, 2026-07-13)

| Aspekt | Detail |
|--------|--------|
| **Zweck** | Kapselt Hybrid-Reasoning-Validierung inkl. optionaler Re-Synthese als separaten Pipeline-Schritt |
| **Input** | `query`, `history`, `sources`, `extras`, `final_text` |
| **Output** | `final_text` (ggf. ersetzt/angereichert nach Validierung) |
| **Kern-Logik** | 1) Source→Evidence-Konvertierung 2) Cross-Encoder-Reranking 3) Grounding-Validierung 4) optionale Re-Synthese + Re-Validierung 5) Qualitätsmetriken + fail-fast bei Pipeline-Fehler |
| **Root-Cause-Nutzen** | Entfernt den größten semantischen Qualitäts-Block aus `run_tools_and_summarize()` und macht Grounding-Verhalten isoliert test-/reviewbar |

### 1.1k `_apply_no_tools_hybrid_reranking()` (neu extrahiert, 2026-07-13)

| Aspekt | Detail |
|--------|--------|
| **Zweck** | Kapselt den Hybrid-Reranking-Schritt im RAG-only/no-tools-Pfad |
| **Input** | `query`, `sources` |
| **Output** | `sources` (ggf. rerankt) |
| **Kern-Logik** | 1) Source→Evidence-Konvertierung 2) Cross-Encoder-Reranking 3) Rückkonvertierung zu `Source` inkl. Score-Update 4) fail-soft Rückgabe der Original-Sources bei Fehler |
| **Root-Cause-Nutzen** | Entfernt den letzten großen Hybrid-Duplikatblock aus `run_no_tools_and_summarize()` und harmonisiert die Pipeline-Struktur zwischen beiden Orchestrator-Pfaden |

### 1.1l `_finalize_and_build_answer()` (neu extrahiert, 2026-07-13)

| Aspekt | Detail |
|--------|--------|
| **Zweck** | Vereinheitlicht den Abschluss der Antwortpipeline (Finalisierung + Return-Objekt) für Haupt- und no-tools-Pfad |
| **Input** | `query`, `final_text`, `sources`, `trace`, `extracted_followups`, optional `finance_grounding_block` |
| **Output** | `FinalAnswer` |
| **Kern-Logik** | 1) optionales Finance-Grounding-Merge 2) `_finalize_answer()` aufrufen 3) konsistentes `FinalAnswer`-Objekt erstellen |
| **Root-Cause-Nutzen** | Entfernt verbleibende End-of-Pipeline-Duplikation und reduziert Risiko divergierender Abschlusslogik zwischen den beiden Hauptpfaden |

### 1.2 `planner_step()`
**Plant typisierte Tool-Aufrufe und gibt Planner-Metadaten fuer den Ausfuehrungspfad zurueck.**

| Aspekt | Detail |
|--------|--------|
| **Input** | `query`, `history`, optional `time_context` |
| **Output** | `(planned_calls, reasoning, critique, planner_ms, planner_raw, normalized_query)` |
| **Folgepfad** | Toolplaene gehen an `run_tools_and_summarize()`; ohne Toolplan wird `run_no_tools_and_summarize()` verwendet |

### 1.3 `run_no_tools_and_summarize()`
**Antwortpfad ohne explizite Tools, weiterhin mit optionaler RAG-Evidenz.**

| Aspekt | Detail |
|--------|--------|
| **Zweck** | Fuehrt RAG, Evidence-Auswahl, optionales Reranking, Synthese, Verifikation und Finalisierung aus |
| **Input** | `query`, `history` |
| **Output** | `FinalAnswer` |
| **Gemeinsamer Abschluss** | Nutzt wie der Toolpfad `_finalize_and_build_answer()` |

---

## 2. Psychotab und UI-/Session-Management

### 2.1 `AgentChatbotLogic.psychological_chat()`
**Separater Psychologie-Antwortpfad ohne normalen Agent-/Streaming-Orchestrator.**

| Aspekt | Detail |
|--------|--------|
| **Zweck** | Erzeugt therapeutische Antworten aus separater Session-History, persönlichem Kontext sowie optional lokaler Psychologie-RAG-/faktischer Web-Evidence |
| **Phasen** | 1) Intent-Klassifikation → 2) Session-/KG-Kontext → 3) konditionales RAG/Web → 4) Prompt-Budgetierung → 5) LLM-Generation → 6) Provenance-Gate → 7) optionales Postprocessing |
| **Routing** | PERSONAL: kein RAG/Web; MIXED: kuratierte lokale Psych-RAG; FACTUAL: lokale RAG und optionaler Web-Fallback außerhalb `APP_LOCAL_ONLY` |
| **Provenienz** | `wellbeing_session/response_provenance.py` bildet eine request-lokale Allowlist aus echten Webresultaten. Rechercheclaims ohne Evidence und nicht exakt gelieferte URLs verwerfen den Draft. Nach genau einer kontrollierten Regeneration gilt Fail-Closed. |
| **UI-/Persistenzpfad** | `WellbeingSessionInterface` → `ResponseGenerator` → `psychological_chat()` → `ChatInputHandler`; erst der final geprüfte String wird als Assistant-Nachricht gespeichert |
| **Safety-Abgrenzung** | Akute/probende Safety-Antworten werden vor freier LLM-Generation deterministisch im Handler erzeugt und durchlaufen diesen Quellen-Guard nicht |

### 2.2 Chat-Streaming-Pipeline (2026-07-25)

| Funktion/Klasse | Verantwortung |
|-----------------|----------------|
| `AgentChatbotLogic.stream_chat_events()` | Startet einen request-lokalen Producer-Thread, ueberfuehrt Callbacks in geordnete `ChatEvent`s und stellt genau ein terminales Event sicher |
| `AgentChatbotLogic.cancel_stream()` | Bricht den aktiven Run einer Session kooperativ ueber `ActiveRunRegistry` ab |
| `StreamingContext.emit()` | Vergibt monotone Sequenzen, erfasst First-Text-Timing und sperrt Emissionen nach dem terminalen Event |
| `ChatEventConsumer.observe()` | Demultiplext Events reload-sicher ueber `event.type`, normalisiert fremde Completion-Resultate und erzwingt genau ein terminales Event |
| `ModelLoader.generate_response_stream()` | Iteriert native llama.cpp-Deltas unter dem CUDA-Lock, prueft Cancellation und schliesst den Iterator garantiert |
| `StreamingTextFilter.feed()` / `finish()` | Entfernt private Denkmarker, Rollenfortsetzungen und vollstaendige/abgeschnittene `[FOLLOW_UP]`-Bloecke zustandsbehaftet ueber Chunk-Grenzen; Follow-up-Inhalte bleiben separat abrufbar |
| `get_ai_response_events()` | Loest die Session-Sprache auf und verbindet Streamlit mit dem typisierten Agent-Stream |
| `chat_tab._stream_ai_response()` | Rendert nur `TextDelta` via `st.write_stream()`, leitet Route und sichere Prozessschritte an die Live-Timeline weiter, verarbeitet Stop/Fehler und gibt nur ein erfolgreiches `ChatRunResult` zur Persistenz frei |
| `chat_tab.progress_callback()` | Bewahrt die letzten acht Prozessschritte mit abgeschlossen/aktiv-Status; zeigt keine privaten Reasoning-Inhalte |
| `_standard_agent_chat_impl()` | Emittiert sichere PLAN_EXECUTE-Checkpoints fuer Analyse, Reasoning-Phase, Planung, Toolauswahl, Werkzeuge/Quellen, Synthese und Qualitaetssicherung |
| `_optimized_research_chat()` | Emittiert entsprechende Checkpoints fuer Quellensuche, Quellenbewertung, Synthese und Ausgabe-Finalisierung und reicht Progress beim Fallback weiter |

**Kritische Invarianten:** Schichtuebergreifende Events werden ueber ihren serialisierbaren Discriminator und nicht ueber Python-Klassenidentitaet erkannt; die Timeline zeigt nur sichere Prozessmetadaten und nie Raw-CoT; sichtbarer Text entspricht `ChatRunResult.text`; klickbare Folgefragen sind separate naechste Nutzeranfragen und nie Assistant-Fragen im Antworttext; REACT emittiert erst nach Citation-/Verification-/PII-Gates; Cancellation/Fehler rollen interne History zurueck und erzeugen keinen Assistant-DB-Eintrag. Architekturdetails: `docs/15_STREAMING_ARCHITECTURE.md`.

---

## 3. `agent/strixkat_eval.py` – Evaluation Pipeline

### 3.1 `StrixKATEval.evaluate()` (Zeile 853, ~63 Zeilen)
**SOTA Evaluation-Pipeline.**

| Aspekt | Detail |
|--------|--------|
| **Zweck** | Führt umfassende Evaluationen des Agent-Systems durch |
| **Metriken** | Accuracy, Relevance, Completeness, Faithfulness, Answer-Relevance |
| **RAGAS Integration** | Nutzt RAGAS-Framework für RAG-spezifische Metriken |
| **Ausgabe** | Detailierte Scores + Reports + Visualisierungen |

---

## 4. `agent/sota_pipeline.py` – SOTA Pipeline

**Datei:** `agent/sota_pipeline.py` (580 Zeilen) — dataclasses, kein Pydantic.
**Zweck:** Verknüpft ChangeDetector, Docling-Parallel, Multi-Modal RAG und StrixKAT Eval zu einer durchgängigen, self-healing Pipeline.

Flow: Quelle geändert (ChangeDetector) → PDF-Extraktion (Docling-Parallel) → Multi-Modal-Chunking (Multi-Modal RAG) → Indexierung (UnifiedRAGStore) → Qualitätsmessung (StrixKAT Eval) → Live oder Auto-Rollback.

### 4.1 `SOTAPipeline.process_document()` (Zeile 229, ~82 Zeilen)
**Dokumenten-Pipeline: Extraktion → Chunking → Indexierung → Evaluation.**

| Aspekt | Detail |
|--------|--------|
| **Zweck** | Verarbeitet ein einzelnes `PipelineDocument` durch die gesamte Pipeline |
| **Flow** | Validate → Extract (Docling-Parallel via `run_in_executor`) → Chunk (Multi-Modal RAG, Fallback `_simple_chunk()`) → Index (`unified_rag_store.add_document()`) → Evaluate (StrixKAT, gedrosselt via `eval_interval_sec`) |
| **Parallel** | `process_batch()` (Zeile 312) verarbeitet Dokumente parallel via `asyncio.gather` |
| **Self-Healing** | `_run_evaluation()` (Zeile 358) ruft `StrixKAT.evaluate_full_pipeline()` auf; `overall_quality` < `quality_threshold` (Default 0.75) + `auto_rollback` → `_trigger_rollback()` (Zeile 397) → `rollback_to_last_good_state()` |
| **Datenmodelle** | `PipelineDocument` (Zeile 33), `PipelineResult` (Zeile 48), `PipelineConfig` (Zeile 61) |
| **Weitere API** | `eval_scheduler` (Zeile 208, lazy `EvalScheduler` + `EvalResultPersistence`), `scan_for_changes()` (421), `full_pipeline_run()` (455), `start_continuous_mode()` (519), `get_pipeline()`-Factory (567) |

---

## 5. `agent/unified_rag_store.py` – Unified RAG Store

### 5.1 `retrieve()` / `search()` (~250+ Zeilen)
**Unified RAG Retrieval.**

| Aspekt | Detail |
|--------|--------|
| **Zweck** | Zentrale RAG-Suchfunktion mit Multi-Source-Support |
| **Quellen** | FAISS-Vektor-Index, Knowledge Graph, Web-Cache, File-Cache |
| **Scoring** | Hybrid-Score: Embedding-Similarity + Recency + Relevance |
| **Gating** | RAG-First-Gate entscheidet, ob lokale Daten ausreichend sind |
| **Persistenz** | Web-Ergebnisse können in RAG persistiert werden (_submit_persist_web_to_rag) |

### 5.2 `upsert_pdf()` / `_upsert_pdf_sequential()` – PDF-Ingest-Kette (aktualisiert 2026-07-14)

| Aspekt | Detail |
|--------|--------|
| **Zweck** | Kompletter PDF-Ingest: Readability-Check → Extraktion → Chunking → KG-Build |
| **Primärpfad** | `utils/docling_processor.DoclingProcessor` (AI-Layout, TableFormer, OCR, HybridChunker) |
| **Fallback-Kette** | pymupdf4llm → pdfminer → PyMuPDF direkt → EasyOCR (`_extract_pdf_with_ocr`) |
| **Readability-Gate** | `pdf_readability_checker.check_pdf_readable()`: pymupdf → pypdf2 → pdfminer; bei False wird Ingest übersprungen |
| **Root-Cause-Fix 2026-07-14** | `AdvancedPDFProcessor`-Adapter komplett entfernt: war zirkulär (delegierte an Docling) und durch `force_ocr`-TypeError funktionsunfähig; `_extract_pdf_text_advanced()` gelöscht |
| **Weitere Fixes** | `db_path`-Bug in `__init__` (abspath(None)-Crash) und Executor-Shutdown-Race in `_submit_entity_resolution_locked()` behoben |

---

## 6. `agent/verification_manager.py` – Verification

### 6.1 `verify()` (~200+ Zeilen)
**Antwort-Verifikation.**

| Aspekt | Detail |
|--------|--------|
| **Zweck** | Verifiziert LLM-Antworten auf Korrektheit, Vollständigkeit, Konsistenz |
| **Methoden** | Fakten-Check, Konsistenz-Check, Citation-Validation |
| **Output** | VerificationResult mit Score + Issues + Suggestions |

---

## 7. `finance/` Module – Finance Query Pipeline

### 7.1 `finance/query_planner.py` – `FinanceQueryPlanner.plan()`
**Finance Query Planning.**

| Aspekt | Detail |
|--------|--------|
| **Zweck** | Liefert einen validierten initialen `FinanceQueryPlan` fuer Finance-Tools |
| **Input** | `question`, `schema_context`, `available_tools`, optional `reference_date` |
| **Logik** | Strukturierte LLM-Planung; kompakte Toolargument-Vertraege werden aus `available_tools[].function.parameters` abgeleitet; deterministischer Finance-Tool-Fallback mit `used_fallback`/`last_error` |

### 7.2 `finance/grammar_compiler.py` – `GrammarCompiler.compile_for_schema()`
**Grammar Compilation für Finance-Queries.**

| Aspekt | Detail |
|--------|--------|
| **Zweck** | Kompiliert ein Pydantic-v2-Modell zu einer BNF-Grammatik fuer constrained decoding |
| **Input/Output** | `BaseModel`-Subklasse plus optionale `GrammarConfig` → BNF-String |

### 7.3 `finance/query_reflector.py` – `FinanceQueryReflector.decide()`
**Query Reflection & Self-Correction.**

| Aspekt | Detail |
|--------|--------|
| **Zweck** | Entscheidet typisiert zwischen Abschluss und einem weiteren Finance-Toolschritt |
| **Input** | Frage, Schema-Kontext, Tool-Trace, letzte Tool-Ausgaben, verfuegbare Tools, optional Konversationskontext |
| **Output** | `FinanceContinuationDecision` mit `action="done"` oder `action="continue"` |

### 7.4 `finance/extractor.py` – `extract_statement()` (~1545 Zeilen)
**PDF-Kontoauszug-Extraktor mit Docling + LLM Pipeline.**

| Aspekt | Detail |
|--------|--------|
| **Zweck** | Extrahiert Strukturdaten (Header + Transaktionen) aus PDF-Kontoauszügen |
| **Phasen** | 1) PDF → Docling (Markdown + Tabellen + KG-Chunks) 2) SHA-256 Hash (Idempotenz) 3) Header-Pass 4) Transaktions-Pass (chunk-basiert) 5) Dedup + Sort 6) FinanceDB.upsert_* |
| **Token-Budget** | Adaptive Chunk-Größe (28K Chars, 800 Overlap, 800-Token Safety-Margin, 3.8 chars/token) |
| **Qualität** | Strukturelle Fehlervermeidung, keine heuristischen Workarounds |

### 7.5 `finance/categorizer.py` – `suggest()` / `apply()` (~309 Zeilen)
**LLM-Batch-Kategorisierung unkategorisierter Buchungen.**

| Aspekt | Detail |
|--------|--------|
| **Zweck** | Vorschläge für Kategorien generieren und anwenden |
| **Workflow** | `suggest()`: Holt unkategorisierte TX, baut Prompt, GBNF-erzwungenes JSON → `apply()`: Schreibt in DB, erstellt `counterparty_rules` |
| **Qualität** | Adaptive Batch-Größe, kein Keyword/Regex-Fallback |

### 7.6 `finance/models.py` – Pydantic v2 Schemata (~572 Zeilen)
**Strukturierte Datenmodelle für Finance-Extraktion.**

| Aspekt | Detail |
|--------|--------|
| **Zweck** | Pydantic v2 Schemata für LLM-strukturierte Extraktion |
| **Features** | IBAN/BIC Validierung (ISO 13616), Datumsnormalisierung (7 Formate), Account-Type/Transaction-Nature Vokabulare |
| **Qualität** | Pydantic v2 konform, keine v1-API |

### 7.7 `finance/tools.py` - SQLite- und Analyse-Tools
**Deterministische Finance-Query- und Analyse-Ausfuehrung.**

| Aspekt | Detail |
|--------|--------|
| **Zweck** | Fuehrt lesende SQLite-Abfragen sowie 47 exponierte Finance-Tools aus (inkl. 10 Goals-Tools seit Phase 2, 2026-09-15) |
| **Analysepfade** | Kategorie-/Gegenparteikosten, Kostenstruktur, wiederkehrende Ausgaben, Forecast, Anomalien, Budget-vs-Ist, Sparpotenzial, Trendbruch, Sparziele (Goals: Fortschritt, Projektion, Kandidaten) |
| **Invarianten** | Signed integer cents intern; positive Ausgabenpraesentation; Transfers standardmaessig aus; Waehrungen getrennt |

### 7.8 `finance/chat.py` – Finance-Chat-Engine (~879 Zeilen)
**Natürlichsprachlicher Finance-Chat mit Follow-ups.**

| Aspekt | Detail |
|--------|--------|
| **Zweck** | Planner -> lokales Finance-Tool -> Reflector -> toolfreie Endsynthese |
| **Sicherheit** | Produktiv nur `finance_*`; kein `code_executor` |
| **Abschluss** | Erfolgreiche direkte Aggregationen werden ohne erneute Tool-Autonomie synthetisiert |

### 7.9 `finance/cache.py` – LRU-Cache (~200 Zeilen)
**Query-Result-Cache mit TTL und Warmup.**

| Aspekt | Detail |
|--------|--------|
| **Zweck** | Cacht Query-Results mit LRU-Eviction, TTL, und Warmup |
| **Qualität** | Deterministisches Eviction, Thread-safe |

### 7.10 `finance/token_budget.py` – n_ctx Resolution (~40 Zeilen)
**Single Source of Truth für LLM-Kontextgröße.**

| Aspekt | Detail |
|--------|--------|
| **Zweck** | Löst `n_ctx` aus GGUF-Metadaten oder Fallback |
| **Resolution** | `get_max_context_tokens()` → `_cached_n_ctx` → Default 16384 |

### 7.11 `scripts/run_release_quality_gate.py` - Release-Quality-Orchestrator
**Einheitlicher lokaler Quality-Gate-Runner.**

| Aspekt | Detail |
|--------|--------|
| **Modi** | `deterministic`: Gesamttests + Profile-Fixture; `live`: Finance- und Profile-Gemma4-Canaries; `all`: beide Gruppen |
| **Vertrag** | Aktiver Python-Interpreter, `APP_LOCAL_ONLY=1`, harte Child-Exit-Codes, Fail-Fast oder optional `--keep-going` |
| **Output** | Aggregierter JSON-Bericht und eingebettete Teilreports unter `monitoring/release_quality/` |

### 7.11 `finance/db_schema.py` – FinanceDB (~2000 Zeilen)
**SQLite-Datenbank mit FinanceDB-Klasse.**

| Aspekt | Detail |
|--------|--------|
| **Zweck** | SQLite-basierte Finanzdatenbank mit vollständigem CRUD |
| **Tabellen** | Banks, Accounts, Statements, Transactions, Categories, Counterparty Rules, Reconciliations |
| **Fixes (2026-07-26)** | `_from_cents(0)` → `"0.0"`, `_to_cents("0.0")` → `0`, `_hash_file` → binary mode, COALESCE Null-Coercion in `list_uncategorized`/`list_counterparties` |

### 7.12 `finance/consistency.py` — Prior-Balance & Statement-Konsistenz (2026-09-09)
**Deterministische Konsistenzprüfung von Auszügen (kein LLM, keine Seiteneffekte).**

| Aspekt | Details |
|--------|---------|
| **Zweck** | Saldo-Kette, Gutschrifts-/Belastungssummen und Vorzeitraum-Link (`prior_balance_link`) prüfen |
| **API** | `evaluate_statement_consistency()`, `evaluate_extracted_statement()` (Duck-Typing-Adapter), `ConsistencyReport` mit `errors`/`warnings`/`to_dict()` |
| **Prior-Lookup** | `FinanceDB.get_prior_closing_balance()`: nur gleiches Konto, strikt `period_end < before`, neuester nicht-leerer Endsaldo, `exclude_statement_id` = Selbstreferenz-Guard (Repair) |
| **Konservativ** | Fehlender Vorzeitraum → `skipped` → `passed_with_warnings` + `needs_review=True` (kein stiller Pass) |
| **Review** | `needs_review = (status != passed)`; Repair (`FinanceExtractor.repair_statement_header`) klärt nur bei Vollpass, sonst bleibt Review + `consistency_errors` |
| **Settlement** | Cross-Account Kreditkarten-Settlement **unterstützt**: `relink_all_transfers(max_days=5)` (`statement_settlements` + nature `settlement`), `detect_statement_settlement_gaps()` (read-only: `no_candidate`/`candidate_out_of_window`/`ambiguous_in_window`/`single_candidate_in_window`) |
| **Grenzen** | Fenster (Default 5 Tage), exakte Cents-Übereinstimmung, Belastungen auf dem Kartenkonto selbst werden nicht verlinkt |
| **Tests** | `tests/test_finance_prior_balance.py` (14), `tests/test_finance_prior_balance_real_data.py` (18) — alle CPU-only, kein LLM/Embedding im VRAM |

---

## 8. `llm_utils/language_detector.py` – Language Detection

### 8.1 `detect_language()` (~150+ Zeilen)
**Spracherkennung.**

| Aspekt | Detail |
|--------|--------|
| **Zweck** | Erkennt die Sprache des User-Inputs |
| **Methoden** | LLM-basiert + Pattern-Matching Fallback |
| **Unterstützt** | DE, EN, FR, ES, BG, IT, PT, RU, UA, PL, CS, SK, HU, RO, HR, SL, SR, BG |

### 8.2 `llm_utils/guaranteed_caller.py` – `call_with_guarantee()`

| Aspekt | Detail |
|--------|--------|
| **Default-Vertrag** | Freie Textantworten werden weiterhin gegen `min_response_length` validiert und bei Ablehnung mit progressiven Temperaturen wiederholt. |
| **Strukturierte Antworten** | Ein optionaler `response_validator` ersetzt ausschließlich für diesen Call die pauschale Längenprüfung durch einen Domänenvertrag. |
| **Diagnostik** | Retry-Warnungen nennen `mode=domain` oder `mode=min_length:N`; `LLMCallResult.success` bleibt der autoritative Fallback-Indikator. |

---

## 9. `i18n/i18n_manager.py` – Internationalization

### 9.1 `translate()` (~100+ Zeilen)
**Translation Management.**

| Aspekt | Detail |
|--------|--------|
| **Zweck** | Zentrale Übersetzungs-Funktion für alle UI-Texte |
| **Sprachen** | DE, EN, BG (in JSON-Dateien unter `i18n/locales/`) |
| **Fallback** | English als Fallback-Sprache |
| **t()-Key-Default (seit 2026-09-01)** | `t(key, default)` gibt bei fehlendem Key den Fallback-Text zurück (vorher: TypeError bei zweitem Positional-Arg — u. a. Compliance-Banner in `wellbeing_session_interface.py`); `t(key)` bleibt unverändert (gibt Key zurück) |

---

## 10. `utils/` – Utility Functions

### 10.1 `utils/db_path_resolver.py` – `resolve_db_path()`
**Database Path Resolution.**

| Aspekt | Detail |
|--------|--------|
| **Zweck** | Löst den korrekten Datenbank-Pfad basierend auf Umgebungsvariablen und Konfiguration |
| **Fallback-Kette** | Env-Var → Config-File → Default-Path |

---

## 11. `wellbeing_session/` – Session Lifecycle

### 11.1 `session_lifecycle_manager.py` – `manage_session()`
**Session Lifecycle Management.**

| Aspekt | Detail |
|--------|--------|
| **Zweck** | Managt den kompletten Lifecycle psychologischer Sessions |
| **Phasen** | Init → Active → Paused → Completed → Archived |
| **Persistenz** | Session-State wird in Datenbank gespeichert |

### 11.2 `services/startup_service.py` / `async_startup_service.py`
**Startup Services.**

| Aspekt | Detail |
|--------|--------|
| **Zweck** | Initialisiert alle Services beim Application-Start |
| **Async-Variante** | `async_startup_service.py` für nicht-blockierenden Start |
| **Cleanup-Invariante** | `StartupService.cleanup_orphaned_sessions()` ist der kanonische synchrone Owner; Lifecycle und Async-Pfad delegieren bzw. verwenden dieselbe reale Schema-Semantik (`session_summary`, `end_time`, `session_interactions`) |
| **Verbindungen** | Jede Sync-/Async-Pool-Verbindung aktiviert `PRAGMA foreign_keys=ON` |

### 11.3 `adapters/session_manager_adapter.py` – `add_message_with_result()`

| Aspekt | Detail |
|--------|--------|
| **Zweck** | Erzwingt einen expliziten Persistenzvertrag (`AddMessageResult`) und liefert die tatsächlich verwendete Session-ID zurück |
| **Existenzbeleg** | Direkte Abfrage von `wellbeing_sessions`; Manager-Cache ist nie autoritativ |
| **Recovery** | Rebind nur mit User-ID aus persistierter Zeile oder exakt gebundenem `SessionContext`; eine Session-ID wird nie als User-ID interpretiert |
| **Caller-Invariante** | Handler müssen `success` prüfen und `session_id` vor jeder Folgeoperation in ihren Zustand übernehmen |
| **Safety-Vertrag** | Nach einem User-Write enthält `AddMessageResult` `risk_level` und `safety_action` (`normal`, `probe`, `acute`). `probe` erscheint pro persistierter Safety-Episode höchstens einmal; frisches `acute` wird nie unterdrückt. Handler entscheiden nicht anhand von UI-State. |

### 11.4 `handlers/chat_input_handler.py` – `handle_psychological_chat_input()`

| Aspekt | Detail |
|--------|--------|
| **Zweck** | Orchestriert User-Persistenz, LLM-Antwort, Assistant-Persistenz und Streamlit-Rerun |
| **Gates** | Fehlgeschlagener User-Write stoppt die Generierung; nur erfolgreicher Assistant-Write erlaubt Erfolgslog und DB-basierten Rerun |
| **Rebind** | `st.session_state.psych_current_session` wird nach jedem erfolgreichen Write auf die effektive Session-ID gesetzt |
| **Fehlerausgabe** | Bei fehlgeschlagenem Assistant-Write bleibt die bereits generierte Antwort im aktuellen UI-Lauf sichtbar und wird nicht fälschlich als gespeichert gemeldet |
| **Krisenpfad** | Fail-Open-Begleitung (2026-08-20, Entscheidung 1b=B): `elevated`/`acute` werden als Warning geloggt; `generate_response_func` läuft exakt einmal mit dem Original-Input, die Antwort wird als normaler Turn persistiert (Chat-, Sync- und Async-Handler einheitlich). Der frühere deterministische Krisenblock (Fail-Closed) ist aus den Produktions-Handlern entfernt; `build_crisis_response()` bleibt nur noch als lokalisierte i18n-Vorlage (`tests/test_psychological_crisis_i18n.py`). Safety-Episoden-Automat und `AddMessageResult`-Vertrag (§11.3/§11.4a) bleiben unverändert. |
| **Profil-Cache** | Sync- und Async-Handler invalidieren nach einer Interaktion direkt den injizierten `ProfileCacheManager` über `invalidate_profile()`. Es gibt keinen separaten Capability-Import; Cachefehler bleiben für den Chat nicht fatal, werden aber als Warning protokolliert. |

### 11.4a Treatment-Fokus und Safety-Episode

| Aspekt | Detail |
|--------|--------|
| **Safety Episode** | `CarePlanRepository.transition_safety_episode()` besitzt den sessionlokalen Zustandsautomaten. Sechs-Turn-Fenster, genau eine Elevated-Probe, Auflösung bei LOW/NONE, unconditional ACUTE. |
| **Focus Ownership** | `SessionFocus.focus_mode` startet als `suggested`. Nur `confirmed` plus aktuelle Turn-Relevanz darf Ziele oder Interventionen in die Generation geben. |
| **User-Steuerung** | `GoalProgressRenderer` bestätigt, pausiert, verwirft, reaktiviert oder wechselt Fokus über `CarePlanManager`; Repository bleibt Source of Truth. |
| **Prompt-Hygiene** | Vollständiger Treatment-Plan unterdrückt die separate Zielliste; historischer Risk-Kontext erscheint nur in einer aktiven Safety-Episode. |

### 11.5 `wellbeing/wellbeing_db.py` – `save_interaction()`

| Aspekt | Detail |
|--------|--------|
| **Zweck** | Speichert verschlüsselte User-/Assistant-Interaktionen mit zeitgebundener Request-Deduplizierung sowie optionaler KG-Extraktion |
| **Transaktion** | `BEGIN IMMEDIATE` serialisiert Parent-Check, Deduplizierung, Insert und Session-Timestamp-Update bis zum Commit |
| **Integrität** | FK auf `wellbeing_sessions(id)` und Post-Insert-Invarianten; identische `(session, role, content_hash)`-Writes werden nur innerhalb von 30 Sekunden wiederverwendet |
| **Nachlauf** | KG-Extraktion erfolgt erst nach Commit und nur für geeignete User-Nachrichten. Der Enhanced-Extractor akzeptiert kurze valide JSON-Objekte anhand einer `triples`-Liste statt einer Mindestlänge; `success=False`-Envelopes werden nicht geparst, sondern pro Chunk explizit in den lokalen Fallback geroutet. |

### 11.6 `wellbeing/wellbeing_db.py` – `delete_user_data()`

| Aspekt | Detail |
|--------|--------|
| **Zweck** | Löscht Rohdaten, Profile, Formulierungen, Treatment-Pläne, Ziele, Fortschritt, Fokus und sessiongebundene Ableitungen eines kanonischen Users |
| **Atomarität** | Ein `BEGIN IMMEDIATE` umfasst Ownership-Snapshots, abhängige Deletes, Residualprüfung und Commit; bei Fehlern vollständiger Rollback |
| **Globaler KG-Cache** | `kg_entities` wird aus verbleibenden Triples neu aufgebaut; passende Embeddings anderer Nutzer bleiben erhalten, gelöschte User-Entities nicht |
| **Nachlauf** | In-Memory-/FAISS-/Profil-Caches werden erst nach erfolgreichem Commit invalidiert |

### 11.7 Mood- und Treatment-Pipeline

| Aspekt | Detail |
|--------|--------|
| **Mood** | Nur User-Turns schreiben Mood-Metadaten, aktualisieren `SessionContext.mood_trend` und triggern `MoodProgressionTracker`; dessen DB-Abfrage filtert zusätzlich `role='user'` |
| **Treatment** | `_run_treatment_pipeline()` gibt das strukturierte `TurnResult` zurück; der Manager hält das letzte User-Turn-Ergebnis sessiongebunden für den Adapter bereit |
| **Identität** | Response- und Session-Context-Builder akzeptieren ausschließlich die persistierte `wellbeing_sessions.user_id`; fehlende Identity ist ein expliziter Fehler |

### 11.8 Insight-Auswahl und Korrektur-Lifecycle

| Aspekt | Detail |
|--------|--------|
| **Schema-Owner** | `WellbeingDatabase` erstellt und migriert `wellbeing_insights`; Provider hängen nicht von einer vorherigen Extractor-Initialisierung ab |
| **Auswahl** | `UserContextBuilder._select_hybrid_top_n()` kombiniert Provider-Evidenz, Confidence, Query-Relevanz, Wiederholungen und Recency deterministisch mit Typabdeckung |
| **Korrekturen** | `correct_user_insight()` prüft `insight_id + user_id`, validiert Status und Replacement und schreibt Statusmutation sowie Auditzeile mit strikt verschlüsseltem Grund in einer Transaktion |
| **Schutz** | Rejected/superseded Insights bleiben von Retrieval und Noisy-OR-Reextraktion ausgeschlossen; nur Menschen dürfen `rejected` reaktivieren, `superseded` bleibt terminal |
| **Dedup-Semantik** | Unicode-normalisierte konservative Paraphrasen werden zusammengeführt; unterschiedliche Negationssignale verhindern die Deduplizierung widersprüchlicher Aussagen |

### 11.9 Care Goals und Prompt-Budget

| Aspekt | Detail |
|--------|--------|
| **Goals** | `CareGoalsProvider` liest ACTIVE/ACHIEVED aus dem Care-Plan-Repository; aktive Ziele werden vor erreichten priorisiert |
| **Prompt-Semantik** | Aktive Ziele sind nutzergetragene Orientierungsanker; erreichte Ziele werden explizit nur als Fortschrittskontext gerendert |
| **Immutable Prompt** | `TokenBudgetManager.emergency_trim_messages()` kopiert Eingaben tief und bewahrt Sicherheits-Systemprompt sowie rohe aktuelle Query unverändert |
| **Fail-Closed** | Optionaler Kontext und alte Historie werden entfernt, bis der Prompt passt; wenn immutable Inhalte allein zu groß sind, verhindert `TokenBudgetExceededError` den Modellaufruf |

---

## 12. `models_pydantic_v2.py` – Pydantic Models

### 12.1 Data Models (~500+ Zeilen)
**Pydantic v2 Models für alle Datenstrukturen.**

| Aspekt | Detail |
|--------|--------|
| **Zweck** | Definiert alle Datenmodelle des Projekts mit Pydantic v2 |
| **Wichtige Models** | `AgentResponse`, `ToolResult`, `FinalAnswer`, `VerificationResult`, `TraceInfo`, `QueryPlan` |
| **Migration** | Von Pydantic v1 auf v2 migriert (adapter in `pydantic_migration_adapter.py`) |

---

## 13. `agent/config_manager.py` – Config Management

### 13.1 `load_config()` / `get_setting()`
**Configuration Management.**

| Aspekt | Detail |
|--------|--------|
| **Zweck** | Lädt und managt alle Konfigurations-Einstellungen |
| **Quellen** | `settings.json`, Environment Variables, Command-Line Args |
| **Caching** | Konfiguration wird gecached für Performance |

---

## 14. `kg_dashboard.py` – Knowledge Graph Dashboard

### 14.1 Dashboard Functions (~300+ Zeilen)
**KG Visualization Dashboard.**

| Aspekt | Detail |
|--------|--------|
| **Zweck** | Visualisiert den Knowledge Graph mit Streamlit |
| **Features** | Node-Graph, Relationship-Explorer, Search, Filter |

---

## 15. `database/chat_history_db.py` – Chat History DB

### 15.1 `save_message()` / `load_history()`
**Chat History Persistence.**

| Aspekt | Detail |
|--------|--------|
| **Zweck** | Speichert und lädt Chat-History |
| **DB** | SQLite mit strukturierter Schema |
| **Features** | Session-basiert, Timestamps, User-IDs |

---

## 16. `finance/tab.py` – Finance UI Tab

### 16.1 Finance Streamlit Tab (~400+ Zeilen)
**Finance Module UI.**

| Aspekt | Detail |
|--------|--------|
| **Zweck** | Streamlit-Tab für Finance-Queries und -Visualisierungen |
| **Integration** | `QueryPlanner`, `GrammarCompiler`, `QueryReflector` |

---

## 17. `wellbeing_session/handlers/response_generator.py` – `ResponseGenerator`

**Psychologisch aktive Antwortgenerierung mit DB-autoritativem Kontext.**

| Aspekt | Detail |
|--------|--------|
| **Einstieg** | `generate_psychological_response()` validiert zuerst Session und persistierte User-ID, baut danach Profil-, KG-, Summary-, Mood- und Zielkontext |
| **System-Prompt** | `THERAPEUTIC_SYSTEM_PROMPT_BASE` fordert warmes Validieren, genau eine vorsichtige Hypothese, kollaborative Prüfung, eine kleine Intervention und höchstens eine fokussierte Frage |
| **Grenzen** | Keine Diagnosegewissheit und keine Empfehlung, Änderung oder Bewertung von Medikamenten/Dosierungen |
| **Kontextbudget** | `_calculate_adaptive_message_limit()` reduziert History abhängig von der Größe des umfassenden Kontexts; `_reduce_prompt_size()` behält Systemkontext plus letzte acht Turns |
| **Konvertierung** | `_convert_to_session_context()` überführt KG, frühere Sessions, Mood, Ziele, Insights und persistentes Profil in den `psychological_chat()`-Vertrag |
| **Fallback** | Auch ohne umfassenden Kontext bleibt die therapeutische Identität aktiv; Sicherheitsantworten werden nicht hier generiert, sondern deterministisch vor diesem Pfad geroutet |

---

## 18. `agent/change_detector.py` – Change Detection

**Datei:** `agent/change_detector.py` (709 Zeilen)
**Zweck:** P3-2: CHANGE DETECTOR – RAG Quality Pipeline Component. Erkennt Änderungen an Quelldokumenten (PDFs, Docs, Finance DB, Web) mittels SHA-256-Hash-basierter Erkennung und watchdog-basiertem File Watching.

### Klassen

| # | Klasse | Zeile | Zweck |
|---|--------|-------|-------|
| 1 | `DocumentFingerprint` | 49 | Immutable Fingerabdruck für Dokumente (SHA-256, Größe, Zeiten) |
| 2 | `ChangeEvent` | 81 | Repräsentiert eine erkannte Änderung (added/modified/deleted) |
| 3 | `WatchConfig` | 106 | Konfiguration für beobachtete Verzeichnisse |
| 4 | `HashCache` | 121 | Persistenter Cache für Datei-Hashes (überlebt Restarts) |
| 5 | `ChangeDetector` | 193 | Hauptklasse: SOTA Change Detector für RAG Quelldokumente |

### Funktionen von `DocumentFingerprint`

| # | Funktion | Zeile | Art | Parameter | Rückgabe | Zweck |
|---|----------|-------|-----|-----------|----------|-------|
| 1 | `to_dict()` | 58 | Method | - | `Dict[str, Any]` | Serialisiert Fingerabdruck zu Dictionary |
| 2 | `from_dict()` | 69 | Class Method | `data: Dict` | `DocumentFingerprint` | Deserialisiert Fingerabdruck aus Dictionary |

### Funktionen von `ChangeEvent`

| # | Funktion | Zeile | Art | Parameter | Rückgabe | Zweck |
|---|----------|-------|-----|-----------|----------|-------|
| 1 | `to_dict()` | 92 | Method | - | `Dict[str, Any]` | Serialisiert Change-Event zu Dictionary |

### Funktionen von `HashCache`

| # | Funktion | Zeile | Art | Parameter | Rückgabe | Zweck |
|---|----------|-------|-----|-----------|----------|-------|
| 1 | `__init__()` | 124 | Constructor | `cache_file: str` | - | Initialisiert Cache, lädt vom Datenträger |
| 2 | `_load()` | 130 | Private | - | - | Lädt Fingerprints aus JSON-Datei |
| 3 | `_save()` | 145 | Private | - | - | Speichert Fingerprints in JSON-Datei |
| 4 | `get()` | 154 | Method | `file_path: str` | `Optional[DocumentFingerprint]` | Gibt Fingerabdruck zurück (thread-safe) |
| 5 | `set()` | 158 | Method | `file_path, fingerprint` | - | Speichert Fingerabdruck + persistiert |
| 6 | `remove()` | 163 | Method | `file_path: str` | - | Entfernt Fingerabdruck aus Cache |
| 7 | `exists()` | 168 | Method | `file_path: str` | `bool` | Prüft Cache-Existenz |
| 8 | `has_changed()` | 172 | Method | `file_path, current_hash` | `bool` | Prüft ob Datei sich geändert hat |
| 9 | `clear()` | 180 | Method | - | - | Leert den gesamten Cache |
| 10 | `__len__()` | 185 | Method | - | `int` | Anzahl gecachter Fingerprints |

### Funktionen von `ChangeDetector`

| # | Funktion | Zeile | Art | Parameter | Rückgabe | Zweck |
|---|----------|-------|-----|-----------|----------|-------|
| 1 | `__init__()` | 205 | Constructor | `watch_configs, cache_file` | - | Initialisiert Detector |
| 2 | `add_watch_directory()` | 223 | Method | `directory, extensions, recursive, debounce_seconds` | - | Fügt Verzeichnis hinzu |
| 3 | `remove_watch_directory()` | 235 | Method | `directory: str` | `bool` | Entfernt beobachtetes Verzeichnis |
| 4 | `on_change()` | 248 | Method | `callback: Callable` | - | Registriert sync Callback |
| 5 | `on_change_async()` | 252 | Method | `callback: Callable` | - | Registriert async Callback |
| 6 | `compute_hash()` | 260 | Static | `file_path, chunk_size` | `str` | SHA-256 Hash berechnen |
| 7 | `get_file_fingerprint()` | 273 | Static | `file_path: str` | `Optional[DocumentFingerprint]` | Fingerabdruck erstellen |
| 8 | `_generate_event_id()` | 296 | Private | - | `str` | Eindeutige Event-ID |
| 9 | `_create_change_event()` | 301 | Private | `change_type, file_path, old_hash, new_hash` | `ChangeEvent` | Event erstellen |
| 10 | `_debounce()` | 322 | Private | `file_path, delay, callback` | - | Debouncet schnelle Änderungen |
| 11 | `_should_process()` | 337 | Private | `file_path, config` | `bool` | Prüft ob Datei verarbeitet werden soll |
| 12 | `_process_file_change()` | 353 | Private | `file_path, event_type, config` | - | Verarbeitet Datei-Änderung mit Hash-Verifikation |
| 13 | `_dispatch_event()` | 394 | Private | `event: ChangeEvent` | - | Verteilt Event an Callbacks |
| 14 | `_create_handler()` | 419 | Private | `config: WatchConfig` | `RAGChangeHandler` | Erstellt FileSystemEventHandler |
| 15 | `start()` | 466 | Method | - | - | Startet Beobachtung |
| 16 | `stop()` | 500 | Method | - | - | Stoppt Beobachtung |
| 17 | `is_running` | 524 | Property | - | `bool` | Running-Status |
| 18 | `scan_directory()` | 532 | Method | `directory, extensions` | `List[ChangeEvent]` | Manuelles Scannen |
| 19 | `scan()` | 581 | Method | - | `List[Dict]` | Kompatibilitäts-Wrapper |
| 20 | `get_status()` | 595 | Method | - | `Dict[str, Any]` | aktuellen Status |
| 21 | `get_change_log()` | 605 | Method | `limit: int` | `List[Dict]` | Recenten Change-Log |
| 22 | `get_novelty_score()` | 610 | Method | `query=None, limit=50, half_life_seconds=86400.0` | `float` | Liefert novelty/freshness-Score aus rezenter Quellaktivität |
| 23 | `reset_cache()` | ~646 | Method | - | - | Setzt Hash-Cache zurück |

### Modulebene

| # | Funktion | Zeile | Zweck |
|---|----------|-------|-------|
| 1 | `create_default_detector()` | 621 | Factory: ChangeDetector mit Standard-Konfiguration |

### SOTA Features
- Async-kompatibel mit asyncio Event Loop
- Batch Change Detection (Debouncing)
- Konfigurierbare Watch-Verzeichnisse
- Hash-Cache Persistenz (überlebt Restarts)
- Integration mit async_startup_service.py
- Thread-safe Operationen

---

## 19. `agent/multimodal_rag.py` – Multi-Modal RAG Chunking & Indexing

**Zweck:** Content-typen-aware Chunking und Indexierung für multi-modale Inhalte (Text, Tabellen, Figuren, Formeln, Header, Code, Mixed) aus PDF-Dokumenten. Arbeitet eng mit `agent/docling_parallel.py` (Sektion 21) zusammen; die SOTA-Pipeline nutzt die Kompatibilitätsklasse `MultiModalRAG` (§19.4).

**Datei:** `agent/multimodal_rag.py` (706 Zeilen) — dataclasses, kein Pydantic.

### 19.1 Datenmodelle

| # | Klasse / Enum | Zeile | Beschreibung |
|---|---------------|-------|--------------|
| 1 | `ContentType` (Enum) | 34 | 7 Mitglieder: `TEXT`, `TABLE`, `FIGURE`, `FORMULA`, `HEADER`, `CODE`, `MIXED` |
| 2 | `TableStructure` (dataclass) | 48 | Strukturierte Tabelle: `table_id`, `columns`, `rows`, `caption`, Source; Properties `markdown_export` (Zeile 59) und `natural_language` (Zeile 75) |
| 3 | `FigureDescription` (dataclass) | 92 | Figur/Diagramm: `figure_id`, `caption`, `description`, `alt_text`, Source; Property `index_content` (Zeile 103) |
| 4 | `FormulaBlock` (dataclass) | 110 | Mathematische Formel: `latex`, Plain-Text-Alternative, `description`; Property `index_content` (Zeile 121) |
| 5 | `MultiModalChunk` (dataclass) | 131 | Zentrales Chunk: `chunk_id`, `content_type`, `primary_content`, `source`, `page`, `metadata`, `hash`, `sub_chunks`, `cross_references`; Methode `to_vector_payload()` (Zeile 143) |

### 19.2 `MultiModalChunker` (Zeile 160) – Chunking-Engine

| # | Methode | Zeile | Zweck |
|---|---------|-------|-------|
| 1 | `__init__()` | 172 | Konfiguration: `chunk_size` (Default 1000), `chunk_overlap` (Default 200) |
| 2 | `_next_chunk_id()` | 179 | Generiert eindeutige, source-basierte Chunk-IDs |
| 3 | `chunk_text()` | 187 | Zerlegt Text in Chunks bei Satzgrenzen; nutzt `_split_large_text()` bei Überschreitung von `chunk_size` |
| 4 | `_create_text_chunk()` | 209 | Erzeugt einen `MultiModalChunk` aus Text mit Metadaten (source, page, hash, timestamp) |
| 5 | `_split_large_text()` | 220 | Split bei Satzgrenzen mit Overlap `max(1, n // 4)` Sätze (≈25 %) |
| 6 | `_split_sentences()` | 254 | Satz-Splitting via Regex `(?<=[.!?])\s+(?=[A-Z\xC0-\xD6\xD8-\xDE])` (großes Initial oder Umlaut) |
| 7 | `chunk_table()` | 266 | Haupt-Chunk (NL-Beschreibung) + Sub-Chunks (pro Zeile) + Markdown-Chunk; Cross-Referenzen |
| 8 | `_row_to_sentence()` | 317 | Wandelt Tabellenzeile in natürlichsprachlichen Satz um |
| 9 | `chunk_figure()` | 329 | Erzeugt `MultiModalChunk` für Figur mit `FigureDescription` |
| 10 | `chunk_formula()` | 350 | Erzeugt `MultiModalChunk` für Formel mit `FormulaBlock` |
| 11 | `chunk_mixed_content()` | 371 | Chunkt gemischte Sektionen (Text + Tabelle + Figur + Formel) aus Docling-Output |
| 12 | `_link_cross_references()` | 419 | Verknüpft Chunks derselben Seite via `cross_references` |

### 19.3 `MultiModalRAGIndex` (Zeile 437) – Index & Retrieval

Index-Architektur: 5 parallele Indizes (Zeilen 448–453): `_index` (Haupt: `chunk_id` → Chunk), `_type_index` (`content_type` → Chunks), `_source_index` (Quelle → Chunks), `_page_index` (Seite → Chunks), `_hash_index` (SHA256-Hash → Chunk, Deduplizierung).

| # | Methode | Zeile | Zweck |
|---|---------|-------|-------|
| 1 | `__init__()` | 448 | Initialisiert die 5 Indizes + `chunk_counter` |
| 2 | `add_chunk()` | 459 | Fügt Chunk hinzu (Deduplizierung via SHA256-Hash); True/False bei Erfolg/Duplikat |
| 3 | `add_chunks()` | 484 | Batch-Hinzufügen; liefert Anzahl neu hinzugefügter Chunks |
| 4 | `remove_by_source()` | 492 | Löscht alle Chunks einer Quelle |
| 5 | `get_chunk()` | 522 | O(1)-Abruf via `chunk_id` |
| 6 | `get_by_source()` | 526 | Alle Chunks einer Quelle |
| 7 | `get_by_type()` | 531 | Alle Chunks eines `ContentType` |
| 8 | `get_by_page()` | 536 | Alle Chunks einer Seite |
| 9 | `get_with_sub_chunks()` | 541 | Chunk + zugehörige Sub-Chunks (Tabellenzeilen) |
| 10 | `expand_query()` | 555 | Cross-modale Query-Expansion: Text-Query → + "Tabelle", "Figur", "Formel"-Begriffe; optional `include_types`-Filter |
| 11 | `stats()` | 588 | Statistik: total, pro Typ, pro Quelle, mit Sub-Chunks, Cross-Referenzen |
| 12 | `clear()` | 604 | Leert alle 5 Indizes |

### 19.4 `MultiModalRAG` (Zeile 613) – Pipeline-Kompatibilität

| # | Element | Zeile | Beschreibung |
|---|---------|-------|--------------|
| 1 | `MultiModalRAG.__init__()` | 616 | Erbt von `MultiModalRAGIndex`; hält `MultiModalChunker` (Defaults: chunk_size 1000, overlap 200) |
| 2 | `chunk_document()` | 629 | Chunkt Dokument in pipeline-kompatible Dicts via `chunker.chunk_text()` → `to_vector_payload()` |

**Nutzung in der SOTA-Pipeline** (`agent/sota_pipeline.py`, 580 Zeilen): Lazy-Property `multimodal_rag` (Zeile 177) mit lazy Import `from .multimodal_rag import MultiModalRAG` (Zeile 180); `chunk_document()`-Aufruf an Zeile 261; Toggle `config.enable_multimodal` (Default `True`).

### 19.5 Factory-Funktionen & Alias

| # | Funktion / Alias | Zeile | Beschreibung |
|---|------------------|-------|--------------|
| 1 | `create_chunker()` | 649 | Factory: `MultiModalChunker` mit Defaults (chunk_size=1000, overlap=200) |
| 2 | `create_index()` | 654 | Factory: leeres `MultiModalRAGIndex` |
| 3 | `MultimodalRAG` (Alias) | 643 | Backwards-kompatibler Alias: `MultimodalRAG = MultiModalRAG` |

### SOTA Features
- Content-type-aware Chunking (Text, Tabelle, Figur, Formel, Header, Code, Mixed)
- Satzgrenzen-Splitting mit Overlap (`max(1, n // 4)` Sätze, ≈25 %)
- Tabellen: NL-Beschreibung + Markdown-Export + Row-Sub-Chunks
- 5-facher Index (Haupt, Typ, Quelle, Seite, Hash) für O(1)-Lookups
- SHA256-basierte Deduplizierung
- Cross-Reference-Verknüpfung zwischen Chunks derselben Seite
- Cross-modale Query-Expansion (`expand_query`)

---

## 20. `wellbeing_session/lifecycle/session_lifecycle_manager.py` – Session Lifecycle

**Zweck:** Session-Lifecycle-Manager für die Wellbeing-Support-Sessions: erstellt Sessions in SQLite, verfolgt den Status (active/paused/ended), extrahiert User-Insights und stellt UI-Dialoge für den Session-Abschluss bereit.

### 20.1 Funktionen & Methoden

| # | Funktion / Methode | Zeile | Beschreibung |
|---|--------------------|-------|--------------|
| 1 | `_tr(key, default, **kwargs)` | 26 | Helper: i18n-Übersetzung mit Fallback; nutzt `i18n.translate()` oder den Default-String mit `.format(**kwargs)` |
| 2 | `_insight_type_label(insight_type)` | 38 | Helper: Label für Insight-Typ (z. B. `life_event` → "Lebensereignis", `coping_mechanism` → "Bewältigungsstrategie") |
| 3 | `_resolve_db_path()` | 43 | Helper: Datenbank-Pfad zentral via `utils/db_path_resolver` (produktive DBs liegen unter dem `.db_root`-Ziel, nicht im Repo) |
| 4 | `__init__()` | 60 | Initialisiert den `SessionLifecycleManager` |
| 5 | `cleanup_orphaned_sessions_on_startup()` | 75 | Setzt überlebende "active"/"paused" Sessions aus vorherigen Läufen auf "ended"; liefert Tuple mit 3 Zählwerten |
| 6 | `create_and_start_new_session(user_name)` | 96 | Erstellt neue Session (INSERT in `wellbeing_sessions`, Status "active"); setzt `current_session_id` im Memory |
| 7 | `end_current_session(user_name, user_input, ai_response)` | 121 | Schließt Session (Status → "ended"), extrahiert Insights (INSERT in `wellbeing_insights`), speichert Interaktion (INSERT in `session_interactions`), zeigt UI-Dialog |
| 8 | `_get_db_connection()` | 245 | Context-Manager für SQLite-Connection; WAL/Journal-Mode wird zentral in `database/connection_pool.py` konfiguriert |
| 9 | `_end_session_fallback(session_id)` | 268 | Setzt Session auf "ended" bei Fehlerschritt (try/except-Log) |

### SOTA Features
- WAL/Journal-Mode via `database/connection_pool.py` (concurrent reads)
- 3-Tabelle-Schema: `wellbeing_sessions`, `session_interactions`, `wellbeing_insights`
- DB-Pfade ausschließlich via `utils/db_path_resolver` (produktive DBs unter `.db_root`, nicht im Repo)
- i18n-Labels für Insight-Typen (DE/EN/BG)
- Cleanup von orphaned Sessions bei Startup

---

## 21. `agent/docling_parallel.py` – Docling Parallel Processing

**Zweck:** SOTA-Parallel-Dokumentenprozessor mit thread-pool-basierter Verarbeitung, memory-aware Batch-Sizing und 2-stufigem PDF-Fallback (Docling → pdfplumber).

### 21.1 Klassen & Funktionen (`agent/docling_parallel.py`, 685 Zeilen)

| # | Funktion / Klasse | Zeile | Beschreibung |
|---|-------------------|-------|--------------|
| 1 | `DocumentType` (Enum) | 51 | Typen: PDF, DOCX, TXT, MD, UNKNOWN |
| 2 | `ProcessingStatus` (Enum) | 58 | PENDING, IN_PROGRESS, COMPLETED, FAILED, CANCELLED |
| 3 | `DocumentChunk` (dataclass) | 66 | `chunk_id`, `content`, `chunk_type` (text/table/figure/formula/header), `page_number`, `metadata`, `embedding_ready`; `to_dict()` (Zeile 75) |
| 4 | `ProcessingResult` (dataclass) | 86 | `file_path`, `status`, `chunks`, `error_message`, `processing_time_ms`, `document_type`, `page_count`, `hash_sha256`; Properties `success` (Zeile 98), `chunk_count` (Zeile 102); `to_dict()` (Zeile 105) |
| 5 | `SystemConfig` (Klasse) | 121 | Dynamische Erkennung: `max_workers` (Zeile 124, `cpu_count`), `max_memory_mb` (psutil), `batch_size` (memory-aware, ~200 MB/PDF), `gpu_available` |
| 6 | `DoclingParallelProcessor` (Klasse) | 172 | Zentrale Klasse |
| 7 | `.__init__()` | 184 | `max_workers`, `batch_size` Parameter |
| 8 | `.on_progress(callback)` | 201 | Registriert Progress-Callback |
| 9 | `._fire_progress()` | 205 | Triggert Callback mit Status |
| 10 | `.cancel_file()` | 218 | Setzt File auf CANCELLED |
| 11 | `.cancel_all()` | 223 | Setzt alle Files auf CANCELLED |
| 12 | `._get_executor()` | 232 | Lazy-Initialisierung ThreadPoolExecutor |
| 13 | `.shutdown()` | 237 | Beendet Executor (wait=False) |
| 14 | `._process_single_document()` | 247 | Haupt-Logik: Hash-Check, Typ-Erkennung, Prozessauswahl, Ergebnis-Speicherung |
| 15 | `.process_single()` | 318 | Öffentliche API für einzelnes Dokument |
| 16 | `._process_pdf()` | 339 | Dispatcher: Docling zuerst, Fallback auf pdfplumber |
| 17 | `._process_pdf_docling()` | 346 | Docling-Verarbeitung (primärer Pfad) |
| 18 | `._process_pdf_fallback()` | 402 | pdfplumber-Verarbeitung (Fallback) |
| 19 | `._process_docx()` | 434 | python-docx mit Style-Erkennung |
| 20 | `._process_text()` | 476 | Plaintext-Verarbeitung |
| 21 | `._get_document_type()` | 501 | Erweiterungs-basierte Typ-Erkennung |
| 22 | `._compute_hash()` | 513 | SHA256-Hash für Change-Detection |
| 23 | `.process_batch()` | 524 | Synchron: verarbeitet Batch sequenziell |
| 24 | `.process_batch_async()` | 568 | Asynchron: verarbeitet Batch parallel via ThreadPool |
| 25 | `.scan_directory()` | 581 | Scannt Verzeichnis nach unterstützten Dateien |
| 26 | `.get_status()` | 611 | Status aller Files |
| 27 | `.get_results()` | 626 | Alle Ergebnisse |
| 28 | `.get_successful_results()` | 631 | Nur erfolgreiche Ergebnisse |
| 29 | `.reset_stats()` | 636 | Reset Counter |
| 30 | `create_processor()` | 653 | Factory-Funktion |
| 31 | `DoclingParallel` (Alias) | 685 | Backwards-kompatibler Alias: `DoclingParallel = DoclingParallelProcessor` |

### SOTA Features
- Thread-pool-parallele Verarbeitung mit memory-aware Batch-Sizing
- 2-stufiger PDF-Fallback: Docling → pdfplumber (Root-Cause-Fix 2026-07-14: AdvancedPDFProcessor-Zweig entfernt — rief nie-existierendes `extract_text` und war zirkulär, da der Adapter selbst an Docling delegierte)
- DOCX-Verarbeitung mit Style-Erkennung (Heading vs Text)
- Progress-Callbacks und Cancellation-Support
- SHA256-Hashing für Change-Detection
- Async-Kompatibilität via `process_batch_async()`

---

## 22. `wellbeing_session/workflow/langgraph_real.py` – LangGraph Session Pipeline

**Zweck:** SOTA LangGraph-basierte StateGraph-Pipeline für die Wellbeing-Sessions mit 7 Nodes, conditional crisis-routing, Dependency-Injection und persistenter Checkpointer-Unterstützung (MemorySaver/SqliteSaver).

### 22.1 Klassen & Funktionen (`wellbeing_session/workflow/langgraph_real.py`)

| # | Funktion / Klasse | Zeile | Beschreibung |
|---|-------------------|-------|--------------|
| 1 | `WellbeingSessionState` (TypedDict) | 156 | State-Definition für die LangGraph-Pipeline |
| 2 | `_DependencyRegistry` (Klasse) | 104 | Thread-sichere Dependency-Injection-Registry (register/get/clear pro `thread_id`) |
| 3 | `get_dependency_registry()` | 147 | Singleton-Accessor für die `_DependencyRegistry` |
| 4 | `_get_dep(state, key)` | 204 | Helper: liest Dependency aus State oder Registry |
| 5 | `validate_input()` | 210 | Node: validiert User-Input |
| 6 | `analyze_emotion()` | 259 | Node: Emotionsanalyse via `emotional_analyzer` (Zeile 271) |
| 7 | `crisis_router()` | 302 | Conditional-Edge-Router: `crisis_response` oder `build_context` |
| 8 | `crisis_response()` | 309 | Node: Krisen-Response (Fail-Open-Begleitung) |
| 9 | `build_context()` | 354 | Node: baut Kontext via `context_builder` (Zeile 361) und `context_formatter` (Zeile 362) |
| 10 | `generate_response()` | 396 | Node: LLM-Response via `langchain_model` (Zeile 407) / `chat_logic` (Zeile 427) |
| 11 | `enhance_response()` | 448 | Node: Response-Enhancement |
| 12 | `record_messages()` | 484 | Node: persistiert User- und Assistant-Nachricht via `session_manager` (Zeile 488) |
| 13 | `build_langgraph_session_graph()` | 527 | Baut StateGraph: validate → analyze → crisis_router → (crisis_response) → build_context → generate → enhance → record |

### SOTA Features
- LangGraph StateGraph (kein handgefertigter State-Machine-Code)
- Conditional-Edge-Router für Krisen-Pfad (Fail-Open-Begleitung, 2026-08-20)
- Dependency-Injection via `_DependencyRegistry` (keine Globals, thread-sicher)
- TypedDict-State (`WellbeingSessionState`) für saubere Typisierung
- Persistente Checkpointer-Unterstützung (MemorySaver/SqliteSaver)

---

## N. `scripts/dependency_vulnerability_scanner.py` — Dependency Vulnerability Scanner

> **Stand:** 2026-09-06 | **Doku:** [docs/16_DEPENDENCY_SCANNER.md](docs/16_DEPENDENCY_SCANNER.md)

| Aspekt | Detail |
|--------|--------|
| **Zweck** | Privacy-preserving Security-Scan für Python-Dependencies + SOTA-Risiko-Priorisierung (P0–P3) |
| **Engine** | OSV-API (Primary) + pip-audit (Zweitquelle) + Heuristik-Fallback |
| **Enrichment** | CISA KEV + FIRST EPSS via `scripts/vuln_enrich.py` + Code-Level-Reachability via `scripts/reachability.py` (best-effort, `--no-enrich` deaktiviert KEV/EPSS/Reachability) |
| **Input** | `requirements.txt` (oder custom via `-r`) |
| **Output** | Console-Report (inkl. Risk-Tier + Reachability-Tag) + JSON (`-o report.json`, inkl. `enrichment_stats`) |
| **Security** | `--strict` Exit-1 bei ANY Vulnerability (CI/CD-fähig) |
| **Cache** | `data/vuln_cache/` (osv/, kev/, epss/), 24h TTL, `--refresh` zum manuellen Aktualisieren |
| **Tests** | 123 Tests / 18 Klassen (`tests/test_dependency_vulnerability_scanner.py`) |
| **RAG** | **Nicht** in RAG aufnehmen (Tool, kein Wissensdokument, Ergebnisse zeitabhängig) |

### Kern-Komponenten

| # | Funktion/Klasse | Zeilen | Beschreibung |
|---|----------------|--------|-------------|
| 1 | `parse_requirements()` | ~297 | Parst `requirements.txt` in `List[Tuple[str, str]]` (Name+Version) |
| 2 | `Vulnerability` | ~103 | CVE-ID, Severity, Package + Enrichment-Felder (`kev`, `epss`, `risk_tier`, `risk_score`, …) |
| 3 | `ScanResult` | ~178 | Gesamtergebnis: vulnerabilities, summary, scan_time, packages_scanned, `enrichment_stats` |
| 4 | `VulnerabilityScanner.scan()` | ~404 | Haupt-Scan: OSV → pip-audit → Heuristik, danach best-effort-Enrichment |
| 5 | `VulnerabilityScanner._scan_with_osv()` | ~625 | Primary-Engine: `api.osv.dev/v1/query` mit per-Package-Cache (24h TTL) |
| 6 | `VulnerabilityScanner._enrich_vulns()` | ~478 | Best-effort-Enrichment: KEV + EPSS + `prioritize()`; Fehler fallen nie auf den Scan zurück |
| 7 | `VulnerabilityScanner._scan_pip_audit()` | ~515 | Zweitquelle: Subprocess mit Timeout, `--skip-db-update`, `--format=json` |
| 8 | `VulnerabilityScanner._heuristic_scan()` | ~842 | Fallback: 7 bekannte kritische Patterns (IMMER als Scan-Fehler markiert) |
| 9 | `vuln_enrich.compute_risk()` | ~117 | Pure/deterministisches Tiering: P0=KEV, P1=critical/(high+EPSS≥0.3), P2=high/medium, P3=rest; Score 0–17 |
| 10 | `vuln_enrich.KEVCatalog` | ~198 | CISA-KEV-Feed: Cache (`kev/kev_cache.json`), Offline/Refresh, case-insensitiver Lookup |
| 11 | `vuln_enrich.EPSSClient` | ~373 | FIRST-EPSS: Batch (Chunk ≤100 CVEs), Cache (`epss/epss_cache.json`), `get_scores()` → `Dict[str, float]` |
| 12 | `vuln_enrich.prioritize()` | ~550 | Orchestriert KEV+EPSS+`compute_risk` pro Vuln; **Reachability-Regel:** `reachable=False` → Tier eine Stufe herab (P0→P1, …, P3→P3); robust gegen `__slots__`/Attribute-Fehler |
| 13 | `reachability.extract_imports()` | ~60 | AST-Import-Sammlung einer `.py`-Datei (BOM-tolerant via `utf-8-sig`; Parse-Fehler → `set()`) |
| 14 | `reachability._dist_requires()` | ~110 | Deklarierte Abhängigkeiten einer Distribution via `importlib.metadata.requires()` (Parsen von `Requires-Dist`) |
| 15 | `reachability.CodeReachability` | ~200 | Repo-Scan: Import-Set + Distribution-Map (`packages_distributions()`) + **Abhängigkeits-Closure** (BFS); `is_reachable()` → `True`/`False`/`None` (unbestimmbar) |
| 16 | `ReportFormatter` | ~981 | Console-Report (inkl. P0–P3 + `unreachable`-Tag in der Tag-Liste `[Tier | KEV! | unreachable | EPSS=…]`) + JSON-Report mit `enrichment_stats` |
| 17 | `main()` | ~1097 | CLI-Entry-Point: `--strict`, `--offline`, `--refresh`, `--no-enrich`, `-r`, `-o` |

### SOTA Features
- OSV als Primary-Engine (direkter API-Call statt pip-audit-Subprocess, pypa/advisory-database als Zweitquelle)
- **CISA KEV + FIRST EPSS-Enrichment** (2026-Standard-Signale: "aktiv ausgenutzt" + Exploit-Wahrscheinlichkeit)
- **Risiko-Tiering P0–P3** (KEV → P0, critical/high+EPSS → P1, high/medium → P2, low/unknown → P3) + Score 0–17
- **Code-Level Reachability** (2026-09-06): statische AST-Import-Analyse + `importlib.metadata`-Abhängigkeits-Closure (BFS); installiert aber ungenutzte CVEs → Tier eine Stufe herab (P0→P1, …); `None` = unbestimmbar (Tier unverändert); BOM-tolerant (`utf-8-sig`); voll lokal, best-effort, bricht den Scan nie
- Per-Package-OSV-Cache + KEV/EPSS-Cache (24h TTL) → `--offline` für ALLE Quellen
- Best-effort-Enrichment: KEV/EPSS-Fehler brechen den Scan nie (`kev`/`epss` bleiben `null`)
- EPSS-Batch mit Chunking (≤100 CVEs/Request) + Rate-Limit-Delay
- `--strict` Mode für CI/CD-Pipelines (Exit-1 bei ANY Vulnerability)
- Zero Telemetry, nur CVE-IDs an FIRST, KEV reiner GET; Zero PII-Leak

## O. `agent/adaptive_rag.py` — Adaptive-RAG Pipeline (Multi-Hop + LLM-Router)

> **Stand:** 2026-07-31 | **Doku:** [17_WEB_RAG_SOTA_ASSESSMENT.md](docs_archive/17_WEB_RAG_SOTA_ASSESSMENT.md) (archiviert 2026-08-01)

| Aspekt | Detail |
|--------|--------|
| **Zweck** | Query-adaptive Retrieval-Pipeline: einfache Queries erhalten schnellen shallow-Path, komplexe Queries erhalten Multi-Hop BFS mit bis zu 3 Hops |
| **Input** | `query: str`, optional `max_hops: int` (default 3), `max_depth: int` (default 3) |
| **Output** | `List[RAGDocument]` mit Score, Source, Hop-Info |
| **SOTA-Lücke geschlossen** | Multi-Hop Retrieval 4.0 → 8.5/10, Query-adaptive Retrieval 5.0 → 9.0/10 |
| **Tests** | 26 Tests (`tests/test_adaptive_rag.py`) — Router, MultiHop, Pipeline, Integration |

### Kern-Komponenten

| # | Funktion/Klasse | Zeilen | Beschreibung |
|---|----------------|--------|-------------|
| 1 | `AdaptiveRAGRouter` | ~50 | LLM-basierter Classifier: analysiert Query-Komplexität (Schlüsselwörter + LLM-Fallback), entscheidet shallow vs deep |
| 2 | `MultiHopRetriever` | ~80 | BFS-basiertes Multi-Hop Retrieval: Hop 1 → Query, Hop 2..N → Entity-Expansion, Confidence-Akkumulation, Cycle-Detection |
| 3 | `AdaptiveRAGPipeline` | ~60 | End-to-End Pipeline: Router → shallow (1-Step) oder deep (Multi-Hop) → Score-Aggregation → Deduplizierung |
| 4 | `_classify_complexity()` | ~30 | LLM-Prompt-basierte Komplexitätsanalyse mit Keyword-Fallback bei LLM-Fehler |
| 5 | `_expand_query()` | ~20 | Entity-Extraktion aus vorherigen Hop-Ergebnissen für nächsten Hop |
| 6 | `_aggregate_scores()` | ~15 | Multi-Hop Score-Aggregation mit Hop-Discount-Faktor (0.9^hop) |

### SOTA Features

- LLM-gesteuerter Komplexitäts-Router (shallow/deep Entscheidung pro Query)
- BFS-basiertes Multi-Hop Retrieval mit Cycle-Detection
- Confidence-Akkumulation über Hops mit Discount-Faktor
- Graceful Degradation: bei LLM-Fehler → Keyword-basierter Fallback
- Deduplizierung über alle Hops (title-basiert)
- Max-Depth-Limiting verhindert unendliche Loops

---

## P. SOTA Filesystem Connector (2026)

> **Stand:** 2026-08-25 | **Doku:** [docs/17_FILESYSTEM_CONNECTOR.md](docs/17_FILESYSTEM_CONNECTOR.md)

| Aspekt | Detail |
|--------|--------|
| **Zweck** | Sicherer, deklarativ konfigurierter Dateisystem-Zugriff für den Agenten (inspiriert vom Masters of AI Harness) |
| **Dateien** | `agent/path_sandbox.py`, `agent/tool_profiles.py`, `agent/tool_schemas.py`, `agent_toolkit.py`, `agent/orchestrator.py` |
| **Tests** | 100 Tests (`test_path_sandbox_sota.py`, `test_tool_profiles.py`, `test_filesystem_tools_integration.py`, `test_file_reader_safety.py`, `test_file_reader_offset_limit.py`, `test_search_files_rg.py`) — alle PASS (2026-08-25) |

### Kern-Komponenten

| # | Datei | Funktion/Klasse | Zeilen | Beschreibung |
|---|-------|----------------|--------|-------------|
| 1 | `agent/path_sandbox.py` | `PathSandbox.resolve()` | ~50 | `os.path.realpath()` + Symlink-Check + Workspace-Boundary |
| 2 | `agent/path_sandbox.py` | `PathSandbox.read_text()` | ~30 | Text-Lesen mit Char-Limiter (50K) |
| 3 | `agent/path_sandbox.py` | `PathSandbox.write_text()` | ~25 | Text-Schreiben (nur wenn FS-Write erlaubt) |
| 4 | `agent/path_sandbox.py` | `PathSandbox.list_directory_safe()` | ~40 | Directory-Listing + Depth-Limiter (Standard: 5) |
| 5 | `agent/path_sandbox.py` | `PathSandbox.search_files_safe()` | ~50 | Name-Suche (Python-Fallback für P2) + Depth-Limiter |
| 6 | `agent/tool_profiles.py` | `ToolProfile` (dataclass) | ~30 | Declaratives Profil: allowed_tools, fs_root, fs_read/write, Limits |
| 7 | `agent/tool_profiles.py` | `TOOL_PROFILES` (Dict) | ~60 | 4 Profile: main_chat, finance_tab, psych_tab, settings_tab |
| 8 | `agent/tool_profiles.py` | `is_tool_allowed()` | ~5 | Prüft ob Tool im Mode erlaubt ist |
| 9 | `agent/tool_profiles.py` | `has_fs_read()` / `has_fs_write()` | ~3 | FS-Permission-Check pro Mode |
| 10 | `agent/tool_schemas.py` | `list_directory` Schema | ~35 | OpenAI-Tool-Format für Directory-Listing |
| 11 | `agent/tool_schemas.py` | `search_files` Schema | ~75 | OpenAI-Tool-Format, P2-Vollvertrag: 8 Parameter, Default Content-Suche |
| 12 | `agent_toolkit.py` | `_list_directory()` | ~20 | Dispatch-Handler → path_sandbox.list_directory_safe() |
| 13 | `agent_toolkit.py` | `_search_files()` | ~130 | P2: rg-first-Dispatch + Fallback-Kette + stabiler Fehler-Vertrag (`success`/`error_code`/`error`) |
| 14 | `orchestrator.py` | `_is_tool_allowed_for_mode()` | ~5 | Filtert Tools pro Tab-Mode via Tool-Profile |
| 15 | `orchestrator.py` | `_build_runtime_planner_tool_block()` | ~30 | Baut Tool-Liste für Planner-Prompt (profilbasiert) |
| 16 | `agent/prompts.py` | `PLANNER_SYSTEM` (Regeln 8–9) | ~5 | list_directory/search_files Routing-Regeln |
| 17 | `agent/prompts.py` | `PLANNER_USER_TEMPLATE` | ~5 | Verfügbare Tools-Liste (inkl. FS-Tools) |
| 18 | `agent/path_sandbox.py` | `PathSandbox.search_content_rg()` + rg-Konstanten | ~160 | P2 (2026-08-25): `rg --json`-Content-Suche — Caps (`DEFAULT_RG_MAX_RESULTS` 50 / `MAX_RG_MAX_RESULTS` 200), Timeout (`DEFAULT_RG_TIMEOUT` 10 s / `MAX_RG_TIMEOUT` 60 s, SIGKILL + Partial-Hits), hidden/`.gitignore`, context (0–100), `fixed_string` (`-F`), `--glob`, `MAX_RG_FILE_SIZE_BYTES` 20 MB |

### Tool-Profile

| Profil | Tools | FS-Read | FS-Write | Zweck |
|--------|-------|---------|----------|-------|
| `main_chat` | Alle (9) | ✅ | ✅ | Vollständiger Zugriff |
| `finance_tab` | RAG, Reader, Calc | ✅ | ❌ | Read-only für CSV/Excel |
| `psych_tab` | Nur RAG | ❌ | ❌ | Privacy-First |
| `settings_tab` | Nur Reader | ✅ | ❌ | Konfig-Dateien lesen |

### SOTA-Security-Layer

- **Path Traversal:** `os.path.realpath()` vor jedem Zugriff (Race-Condition-Schutz)
- **Symlink-Escape:** `os.path.islink()` → abweisen
- **Workspace-Boundary:** Resolved path muss unter `workspace_root` liegen
- **Binärdatei-Erkennung:** Null-byte Heuristik (erste 8192 Bytes)
- **Depth-Limiter:** Standard 5 Ebenen (konfigurierbar)
- **Char-Limiter:** Standard 50.000 Zeichen (Token-Budget-Schutz)
- **Size-Limiter:** Standard 5 MB (10 MB für Finance-Tab)

### P2: ripgrep-Content-Suche (2026-08-25)

> `search_files` ist jetzt **Default-Content-Suche** im Dateiinhalt
> (ripgrep-Backend, `rg --json`); reine Name-Suche via `content_search=false`.
> Inspiriert: Claude Code Grep-Verhalten (2026). Keine neue pip-Abhängigkeit
> (rg-Binary lokal; Python-Name-Suche als Fallback ohne rg).

| Aspekt | Detail |
|--------|--------|
| **Backend-Priorität** | (1) `search_content_rg()` → `rg --json`; (2) Fallback: `search_files_safe()` (Python-Name-Suche) bei fehlendem rg, rg-Ausführbarkeitsfehler (`FileNotFoundError`/`OSError`) oder Timeout |
| **Parameter** | `root_path`, `pattern`, `content_search` (Default `true`), `case_insensitive` (`rg -i`), `fixed_string` (`rg -F`, kein Regex-Parser), `glob` (`rg --glob`), `hidden` (Default `false`, `.gitignore` wird beachtet), `context` (0–100, `rg -C`), `max_results` (Default 50, Hard-Max 200), `timeout` (Default 10 s, Hard-Max 60 s) |
| **Sandbox** | `root_path` läuft erst durch `PathSandbox.resolve()`; rg-Pfade bleiben im Workspace; die rg-Argumente sind kein zweiter Pfad-Kanal |
| **Timeout-Verhalten** | rg-Prozess wird hart beendet (SIGKILL); bereits geparste Hits bleiben erhalten → `partial=true`, `timed_out=true` |
| **Fehler-Vertrag** | `success`/`error_code`/`error` stabil über alle Pfade: `sandbox_error` (→ `needs_user_permission=true` + `allowed_tools: ["execute_tool"]`), `invalid_regex`, `invalid_parameter`, `not_found`; Fallback-Hits tragen `match_type: name` |
| **Tests** | `tests/test_search_files_rg.py` — 26 Tests (Caps, Timeout/Partial, fixed_string, hidden, context, invalid_regex, Name-Fallback, rg-missing-Fallback, Sandbox-Abweisung, Permission-Shape, Vertrag) — 26/26 PASS; Full-Suite 808/808 PASS |
| **Doku/Prompt** | `docs/17_FILESYSTEM_CONNECTOR.md` (search_files-Vollvertrag + rg-Security-Layer), `agent/tool_schemas.py` (Vollvertrag), `agent/prompts.py` (Regel 9 + WICHTIG-Liste + User-Template), Workdoc in `docs_archive/WORKDOC_FILESYSTEM_CONTEXT_SAFETY_20260824.md` |

---

## Q. Progressive Tool Disclosure (2026-08-24)

> **Stand:** 2026-08-24 | **Modul:** `agent/react_agent.py` + `agent/tool_retriever.py` + `agent/tool_profiles.py`
> **Zweck:** Progressive Tool Disclosure im ReAct-Agent — Tool-Pool pro Tab (Profile-Gating),
> deterministischer Finance-Intent-Override, Capability-Gap-Retry (max 1x) und
> Hybrid-Tool-Retrieval für große Pools. Reduziert Token-Overhead & Tool-Halluzinationen.

| Aspekt | Detail |
|--------|--------|
| **Dateien** | `agent/react_agent.py` (Filter + Intent + Retry), `agent/tool_retriever.py` (BM25+Cosine+RRF), `agent/tool_profiles.py` (Finance-Partition) |
| **Tests** | `test_tool_profile_gating.py` + `test_tool_retriever.py` — 73 PASS; Regression 121 PASS (8 Suites) |
| **Sicherheits-Invariante** | Leerer Tool-Pool wird NIE still akzeptiert — explizit geloggt + Fallback auf volles Tool-Set (kein silent fallback) |
| **Retry-Garantie** | Capability-Gap-Retry genau 1x (idempotent, via `capability_gap_retry`-Flag + `tool_pool`-Override) |

### Kern-Komponenten

| # | Komponente | Ort | Funktion |
|---|-----------|-----|----------|
| 1 | `_tool_schemas_for_state()` | `react_agent.py` | Zentrale Tool-Pool-Filterung pro Run (Profil → Route-Overlay → Retrieval → Safety-Netz) |
| 2 | `_resolve_tool_pool_names()` | `react_agent.py` | Tool-Namen des Pools (Override > Profil) + Finance-Intent-Erweiterung (nur Erweiterung, nie Reduktion) |
| 3 | `_apply_tool_retrieval()` | `react_agent.py` | Verengung großer Pools (>12) via Retriever auf Core + top-8; Core-Tools kommen nie raus |
| 4 | `_maybe_capability_gap_retry()` | `react_agent.py` | Detectiert "kein Zugriff"-Antworten ohne Finance-Tools → einziger Retry mit erweitertem Pool |
| 5 | `HybridToolRetriever` | `tool_retriever.py` | BM25 + Cosine + RRF-Ranking der Tool-Schemata gegen die Query |
| 6 | `FINANCE_CORE` / `FINANCE_ANALYTICS` / `FINANCE_WRITE_TOOLS` | `tool_profiles.py` | Single Source of Truth der Finance-Tool-Partition |
| 7 | `_FINANCE_INTENT_RE` | `react_agent.py` | Deterministische DE+EN Intent-Erkennung (Regex, kein LLM-Call), Recall-optimiert |

### Finance-Tool-Partition (Single Source of Truth)

| Gruppe | Inhalt | Zugang im ReAct-Chat |
|--------|--------|----------------------|
| `FINANCE_CORE` | Read-only (13 Tools, inkl. `finance_sql_query` als Escape-Hat) | via Finance-Intent oder Finance-Tab-Pool |
| `FINANCE_ANALYTICS` | Spezialisierte Analyse (13 Tools) | via Finance-Tab-Pool |
| `FINANCE_READ_TOOLS` | CORE + ANALYTICS (26) | gehören zum `finance_tab`-Profil |
| `FINANCE_WRITE_TOOLS` | Schreib-/Verwaltung (8 Tools) | **nie** im ReAct-Pool — nur dedizierte Finance-Pipeline |

### Design-Prinzipien

- **Recall > Precision** beim Intent: Ein False-Positive erweitert nur harmlos den Pool;
  ein False-Negative wäre eine Capability-Lücke → dagegen schützt der Gap-Retry.
- **Progressive Disclosure**: Default-Pool klein (main_chat), Domain-Tools werden erst
  bei Intent/Tab/Ranking sichtbar.
- **Deterministisch & testbar**: Intent-Erkennung + Pool-Aufbau ohne LLM; alle Pfade
  via 73 dedizierte Tests abgesichert.
- **Kein silent fallback**: Jeder Fallback- und Fehlerpfad ist explizit geloggt.

---

## R. Tool-Result Eviction (2026-08-24)

> **Stand:** 2026-08-24 | **Modul:** `agent/tool_result_eviction.py` (neu, ~220 Zeilen) + Hook in `agent/react_agent.py`
> **Zweck:** Kompaktierung alter idempotenter Dateisystem-Tool-Ergebnisse (`file_reader`,
> `search_files`, `list_directory`) direkt vor dem LLM-Call — verhindert Context-Rot-Blutungen
> in langen ReAct-Läufen (mehrere hundert KB an Read-Ergebnissen, die das Modell nie wieder
> braucht). Inspiriert: Claude Code Read/Grep-Verhalten, Anthropic Context-Engineering (2026).

| Aspekt | Detail |
|--------|--------|
| **Dateien** | `agent/tool_result_eviction.py` (neues Modul), `agent/react_agent.py` (+28 Zeilen Hook in `_node_agent_step`), `utils/token_manager.py` (Wiederverwendung `estimate_prompt_tokens`) |
| **Tests** | `tests/test_tool_result_eviction.py` (17 Tests) + `tests/test_react_agent_eviction_integration.py` (3 Tests im echten Node) — 20/20 PASS; Full-Suite 757/757 PASS |
| **Nur idempotente Tools** | `file_reader`, `search_files`, `list_directory` — ein erneuter Aufruf ist immer sicher. Nicht-idempotente Tools (z. B. `code_executor`, Writes) bleiben NICHT evictiert |
| **Letzte K=2 pro Tool intakt** | Die zwei jeweils letzten Tool-Results eines evictierbaren Tools bleiben vollständig erhalten; alle davor werden zu kompakten Platzholdern |
| **Trigger-Budget** | Eviction greift nur, wenn `estimate_prompt_tokens(messages) ≥ 3000` (`DEFAULT_TRIGGER_TOKENS`) — kurze Läufe bleiben unangetastet |
| **Struktur-Invariante** | `role`, `tool_call_id`, `tool_calls`-Verdrahtung bleiben unverändert; nur der `content`-Text der Tool-Results wird ersetzt (neue Liste, State nie mutiert) |
| **Platzhalter-Vertrag** | Beginnt mit `[EVICTED]`, enthält Tool-Name + Original-Größe (Zeichen) + explizite Aufforderung zur Re-Execution (z. B. `file_reader` erneut mit Offset/Limit) |
| **Non-fatal** | Fehler im Hook werden geloggt (`logger.warning`, exc_info) und stoppen den Chat NICHT — Eviction ist eine Optimierung |
| **Observability** | Bei aktiver Eviction: `logger.info` mit Anzahl evictierter Results + Tokens vor/nachher; der LLM-Call-Log (SOTA 2026-08-21 P0) zeigt weiterhin das aktive Tool-Set pro Iteration |

### Kern-Komponenten

| # | Komponente | Ort | Funktion |
|---|-----------|-----|----------|
| 1 | `evict_stale_tool_results()` | `tool_result_eviction.py` | Öffentliche API: `(messages, keep_last=2, trigger_tokens=3000) → (messages, stats)`; reine Funktion (Input nie mutiert), deterministisch |
| 2 | `EVICTABLE_TOOLS` | `tool_result_eviction.py` | Allowlist idempotenter FS-Tools (`file_reader`, `search_files`, `list_directory`) |
| 3 | `_placeholder()` + `_format_params()` | `tool_result_eviction.py` | Baut den `[EVICTED]`-Platzhalter (Tool, Params, Original-Größe, Re-Execution-Hinweis) |
| 4 | `_tool_call_index()` | `tool_result_eviction.py` | Mapping `tool_call_id → (tool_name, args)` über assistant-`tool_calls` (robust gegen String-JSON) |
| 5 | `_estimate_tokens()` | `tool_result_eviction.py` | Wrapper um `utils/token_manager.estimate_prompt_tokens()` (deterministisch, `use_tiktoken=False`) |
| 6 | Eviction-Hook | `react_agent.py` `_node_agent_step` (~Zeile 1361) | Aufruf direkt vor `model_loader.generate_with_tools()`; try/except + Warning-Log (non-fatal) |

### Design-Prinzipien

- **Letzte-K-Regel:** Das Modell arbeitet typischerweise auf den jüngsten Ergebnissen;
  ältere sind bei idempotenten Reads redundant → Platzhalter mit Re-Execution-Hinweis
  ist sicherer als stilles Löschen.
- **Budget-Trigger statt immer-evictieren:** Kleine Kontexte (unter 3000 Tokens) bleiben
  byte-identisch — kein Overhead, keine veränderte Prompt-Struktur bei kurzen Läufe.
- **Kein State-Mutation:** Die Funktion liefert eine NEUE Liste; `state["messages"]`
  bleibt unverändert (Regressionstest `test_state_messages_not_mutated`).
- **Recoverability:** Jeder Platzhalter dokumentiert Tool + Original-Größe + wie man das
  Ergebnis erneut holt → das Modell kann nachvollziehen, was verloren ging.
- **Observability first:** Der frühere Fehlerfall (Context-Blutungen) war in Logs unsichtbar;
  jetzt sind Eviction-Statistik und aktives Tool-Set pro Iteration protokolliert.

---

## S. Filesystem-Context-Navigation (P1, 2026-08-25)

**Stand:** 2026-08-25 | **Modul:** `agent/path_sandbox.py` + `agent_toolkit.py` (`_file_reader`) + `agent/tool_schemas.py`

**Zweck:** Zeilenbasierte Navigation in großen Dateien (`offset`/`limit`, Claude-Code-Read-Modell) —
das Modell liest ein Fenster, erhält Navigation-Metadaten und setzt über `next_offset` exakt dort
fort, wo der vorherige Read aufgehört hat. Ergänzt P0 (Hard-Char-Limit) und P0.5 (Eviction):
Evictierte `file_reader`-Ergebnisse werden jetzt gezielt per `offset`/`limit` nachgeladen.

| Aspekt | Detail |
|--------|--------|
| **Dateien** | `agent/path_sandbox.py` (`read_file_safe(offset, limit)` + `line_meta`), `agent_toolkit.py` (`_file_reader`-Parameter + Navigation-Hinweise), `agent/tool_schemas.py` (Schema-Parameter) |
| **Tests** | `tests/test_file_reader_offset_limit.py` (25 Tests, neu) + `test_path_sandbox_sota.py` (12) + `test_file_reader_safety.py` (8) — 45/45 PASS; Full-Suite 782/782 PASS |
| **`line_meta`-Vertrag** | 5. Rückgabe-Element von `read_file_safe`: `{total_lines, start_line, end_line, has_more_lines, next_offset}`; `next_offset` ist **immer int** = `max(start_line, end_line + 1)` (Voll-Read → `total_lines + 1`, Offset über EOF → Offset selbst) |
| **Default-Limits** | `limit` = 2000 Zeilen (`DEFAULT_READ_LINE_LIMIT`), Byte-Guard 20 MB (`DEFAULT_MAX_READ_BYTES`, P1: 50 → 20 MB, Token-Budget-Schutz), Char-Limit 50.000 (P0, bleibt als Backstop) |
| **Navigation im Result** | `_file_reader` liefert `total_lines`/`start_line`/`end_line`/`has_more_lines`/`next_offset`; `suggested_action` mit konkretem `next_offset` (Teil-Read), Char-Backstop-Warnung (Byte-Read), EOF-Überlauf-Hinweis ("Nutze `offset` ≤ N") |
| **Rollback-Pre-State** | Commit `e708b177` (2026-08-24 23:47) — letzter Snapshot vor der P1-Implementierung (`6f06a064`) |

### Kern-Komponenten

| # | Komponente | Ort | Funktion |
|---|-----------|-----|----------|
| 1 | `read_file_safe()` | `path_sandbox.py` | Zeilenfenster-Read: `offset` (1-basiert, Default 1), `limit` (Default 2000); Binary-/Byte-Checks bleiben; liefert 5-tuple mit `line_meta` |
| 2 | `DEFAULT_READ_LINE_LIMIT` / `DEFAULT_MAX_READ_BYTES` | `path_sandbox.py` | 2000 Zeilen / 20 MB — Single Source of Truth der Read-Limits |
| 3 | `_file_reader()` | `agent_toolkit.py` | Tool-Handler: `offset`/`limit`-Parameter, `line_meta` 1:1-Flattening, `suggested_action`-Navigation (next_offset / Char-Backstop / EOF-Hinweis) |
| 4 | `file_reader`-Schema | `tool_schemas.py` | `offset`/`limit`-Parameter mit Continuation-Hinweis für das LLM |

### Design-Prinzipien

- **Claude-Code-Read-Modell:** Fenster-Read statt Voll-Read; das Modell steuert die Navigation
  über `next_offset` — deterministisch, ohne Zeilen-Zählfehler des Modells.
- **`next_offset` immer int:** Eindeutiger Fortsetzungs-Punkt (keine `None`-Ämbiguität);
  Offset über EOF → der Offset selbst (leeres Fenster, `has_more_lines=false`) →
  `suggested_action` korrigiert das Limit ("Nutze `offset` ≤ N").
- **Schicht-Stack (P0 → P1):** 20-MB-Byte-Guard (hart, `sandbox_error`) → 2000-Zeilenfenster →
  50K-Char-Backstop (weich, `was_truncated` + `truncated_at`) — jede Schicht unabhängig testbar.
- **Kooperation mit P0.5:** Eviction-Platzhalter enthalten den Re-Execution-Hinweis mit
  `offset`/`limit` — das Modell lädt evictierte Results gezielt nach, statt zu vollzulesen.

---

## T. Dual-GPU-Platzierung LLM/AUX (2026-08-25)

> **Stand:** 2026-08-25 | **Modul:** `utils/gpu_devices.py` (neu, Single Source of Truth) + 9 Konsumenten-Module
> **Zweck:** LLM strikt auf RTX 4090 (24 GB), alle AUX-Modelle auf RTX 3060 Ti (8 GB) — VRAM-Isolation,
> CUDA↔NVML-Indexauflösung, einheitliche Device-Strings, bewahrte CPU-Fallbacks.

| Aspekt | Detail |
|--------|--------|
| **Dateien** | `utils/gpu_devices.py` (Placement, UUID-Mapping, env-Overrides, `python -m`-CLI), `utils/vram_monitor.py` (beide GPUs, NVML-Index-Auflösung, nvidia-smi-CLI-Fallback), `ui_tabs/performance_tab.py` (Rollen-Display), `scripts/validate_gpu_placement.py` (Runtime-Validierung) |
| **Konsumenten** | `agent/reranker.py` (ONNX `CUDAExecutionProvider(device_id=aux_cuda)`), `agent/rag_store/core/embeddings.py` (SentenceTransformer `cuda:1`), `agent/verification_manager.py` (NLI-onnx), `agent/ocr_processor.py` + `agent/vision_ocr_processor.py` (EasyOCR `cuda:1`), `utils/docling_processor.py` (Torch `cuda:1`), `cache/semantic_cache.py` + `ragas_sota_evaluation.py` (Embedding-Device) |
| **Tests** | 33/33 Reranker/Verification/Embedding/Reranker-Cache + 55/55 Docling (2026-08-25) |
| **Validierung** | `python -m utils.gpu_devices` (Diagnose) · `python scripts/validate_gpu_placement.py [--bench]` (Placement + VRAM + Reranker-Provider) · `nvidia-smi` (NVML-Reihenfolge!) |

### GPU-Rollen & Index-Systeme

| Rolle | GPU | CUDA-Runtime | NVML (nvidia-smi) |
|-------|-----|--------------|-------------------|
| LLM (Gemma4 12B, llama.cpp) | RTX 4090 (24 GB) | `cuda:0` | **NVML 1** |
| AUX (Reranker/Embeddings/NLI/OCR/Docling) | RTX 3060 Ti (8 GB) | `cuda:1` | **NVML 0** |

### Design-Prinzipien

- **Single Source of Truth:** `get_placement()` liefert `llm_cuda`/`aux_cuda` (Runtime-Indizes),
  `llm_nvml`/`aux_nvml` (Monitoring-Indizes) und `aux_device_string` — Konsumenten hardcoden
  nie `cuda:N`.
- **CUDA-Index ≠ NVML-Index** (auf diesem System vertauscht); Auflösung per
  `torch.cuda.get_device_properties(i).uuid ↔ nvmlDeviceGetUUID()` — nie per Positionsnummer.
- **Device-Formen pro Runtime:** ONNX → `device_id=aux_cuda` (Integer); Torch/SentenceTransformer/
  EasyOCR/Docling → `aux_device_string`; llama.cpp → `get_llm_cuda_index()`.
- **Overrides:** `BOT_LLM_CUDA_DEVICE` / `BOT_AUX_CUDA_DEVICE` (Integer = CUDA-Runtime-Index);
  unsichtbare Indizes werden geloggt und ignoriert (Auto bleibt).
- **CPU-Fallbacks bleiben aktiv** bei fehlendem GPU-Backend (ONNX EP, Torch-CUDA, pynvml) —
  nie-failing: App startet, Platzierung warnt, keine harten Fehler.
- **ONNX-Voraussetzung:** `onnxruntime-gpu` im venv; sonst CPU-Reranking (funktioniert, langsamer).
- **Monitoring:** `VRAMMonitor` auflöst `device_id` → NVML-Index via Placement;
  `get_all_gpu_snapshots()` liefert beide GPUs mit `role` (LLM/AUX) + `cuda_index`;
  Performance-Tab zeigt Rollenbeschriftung + Placement-Zeile; pynvml primär,
  `nvidia-smi --query-gpu`-CLI als Fallback.
- **Validierungs-Voraussetzung:** LM Studio schließen (hält VRAM auf beiden GPUs) —
  sonst verfälschte Platzierungs-/VRAM-Messungen.


## U. Dynamische LM-Studio-Modell-Registry (2026-08-26)

> **Stand:** 2026-08-26 | **Module:** `utils/model_registry.py` (neu) + `scripts/model_loader.py` (erweitert) + `enhanced_streamlit_bot.py` (Sidebar)
> **Zweck:** Sidebar listet live alle Modelle im LM-Studio-Community-Ordner (ohne Neustart),
> erkennt Vision-Unterstützung über `mmproj` und lädt per Pfad mit Unload/Reload-Logik.

| Aspekt | Detail |
|--------|--------|
| **Dateien** | `utils/model_registry.py` (rekursiver Scan, `ModelInfo`, `scan_models()`, `find_model_by_path()`, `python -m`-CLI), `scripts/model_loader.py` (`load_model_by_path()`, `custom:<Datei>`-IDs), `enhanced_streamlit_bot.py` (Sidebar-Selectbox, `initialize_ai()`-Branch), `i18n/locales/{de,en,bg}.json` (4 neue Schlüssel) |
| **Modell-Ordner** | `~\.cache\lm-studio\models\lmstudio-community` (Override: `BOT_MODELS_DIR`) |
| **Tests** | `tests/test_model_registry.py` (11) + `tests/test_model_loader_dynamic.py` (5) = 16/16 PASS (2026-08-26);
  Test-Fixtures nutzen **Sparse-Dateien** (1 Byte, logische Größe via `seek()`) — die Registry liest nur
  `stat().st_size`, daher keine GB-großen Writes nötig (verursachten sonst transiente `OSError`s unter Disk-Druck) |
| **Validierung** | `python -m utils.model_registry` (Live-CLI: 11 Modelle, Vision-Flags korrekt) + Bare-Mode-Smoke
  (Import von `enhanced_streamlit_bot`, 11 Labels via `info_by_path` + `_dynamic_model_label()`,
  Default-Auswahl + Stale-Guard) |

### Funktionsweise

- **Live-Scan:** `scan_models()` scannt den Modell-Ordner bei jedem Sidebar-Render — neue
  Modelle/Ordner erscheinen ohne App-Neustart.
- **Erkennungs-Regeln:** Alle `.gguf` außer `mmproj*` und Shards (`*-NNNNN-of-NNNNN.gguf`).
  Pro Ordner + Quantisierung eine Eintrag (mehrere Quantisierungen → mehrere Einträge).
- **Vision:** `mmproj*.gguf` im selben Ordner wie die Haupt-GGUF → `is_vision=True` +
  `mmproj_path` gesetzt (größte mmproj gewinnt).
- **Sidebar:** Selectbox über Pfade (stabile Widget-Identität), Label `Anzeigename (GB) · 👁 Vision/📝 Text`;
  Caption mit Modell-Ordner + Anzahl. Fehlt der Ordner: Warnung + Fallback auf die
  statischen `MODEL_CONFIGS`.
- **Streamlit-`format_func`-Kontrakt:** `st.selectbox(format_func=...)` übergibt den rohen
  Optionswert (Pfad-String!) an die Label-Funktion — daher löst die Closure `_label_by_path()`
  die `ModelInfo` über das `info_by_path`-Dict (Pfad → `ModelInfo`) auf, bevor
  `_dynamic_model_label()` aufgerufen wird. Direkt `_dynamic_model_label` als `format_func`
  zu übergeben würde `AttributeError: 'str' object has no attribute 'is_vision'` werfen.
- **Stale-Session-Reset:** Ist der gewählte Pfad nicht mehr in der Live-Registry
  (Modell-Ordner in LM Studio gelöscht), werden `selected_model_path` und
  `selected_model_info` vor dem Render zurückgesetzt — die Selectbox fällt auf die
  Default-Auswahl zurück statt zu crashen.
- **Laden:** `initialize_ai()` liest `st.session_state["selected_model_info"]` →
  `ModelLoader.load_model_by_path(model_path, mmproj_path)`. Ein anderes, bereits geladenes
  Modell wird vorher entladen; gleiches Modell erneut wählen → kein unnötiger Reload.
- **Modell-ID:** Dynamische Modelle erhalten `custom:<Dateiname>`
  (z. B. `custom:gemma-4-12B-it-QAT-Q4_0.gguf`) — `get_current_model_name()` entfernt das Präfix.
- **Override:** `BOT_MODELS_DIR` (alternativer Modell-Ordner, z. B. für Tests oder eine
  zweite LM-Studio-Installation).
- **Default-Auswahl:** Produktionsmodell (Gemma 4 12B), falls vorhanden, sonst erster
  Eintrag der Sortierung (Ordner A–Z, Größe absteigend).

### Grenzen

- Registry ist framework-agnostisch (kein Streamlit/llama-Import) → direkt unit-testbar.
- Der Load läuft weiterhin über das `ModelLoader`-Singleton (CUDA-Locks, VRAM-Pre-Check,
  Special-Tokens aus GGUF-Metadaten als Single Source of Truth).
- Vision-Modell ohne mmproj bleibt reines Text-Modell (keine Auto-Vision).

## V. Selektiver AUX-GPU-Modell-Lifecycle (2026-08-28)

> **Stand:** 2026-08-28 | **Module:** `utils/aux_model_release.py` (neu) + `agent/ocr_processor.py` + `agent/vision_ocr_processor.py` + `utils/docling_processor.py` + `ui_tabs/chat_tab.py`
> **Zweck:** Kalte AUX-Modelle (Docling-Pipeline, EasyOCR-Reader) nach Import-/OCR-Peaks entladen und den CUDA-Cache an das OS zurückgeben — VRAM-Headroom auf der 3060 Ti für den LLM-Query-Path, ohne die heißen Query-Path-Modelle zu verdrängen und ohne Feature-Verlust (Lazy-Reload).

| Aspekt | Detail |
|--------|--------|
| **Dateien** | `utils/aux_model_release.py` (`release_cold_aux_models(reason)` — zentrale Freigabe), `agent/ocr_processor.py` (WeakSet-Registry `_active_ocr_processors` + idempotentes `cleanup()`), `agent/vision_ocr_processor.py` (WeakSet-Registry `_active_vision_ocr_processors` + `cleanup()`), `utils/docling_processor.py` (idempotentes `cleanup()`, `get_instance()` ohne Eager-Load), `ui_tabs/chat_tab.py` (Post-Import-Hook im `finally`), `agent/reranker.py` (`_load_failed`-Cache), `agent/rag_pipeline.py` + `agent/rag_store/core/search.py` (verfrühte `is_available`-Gates entfernt) |
| **Kalt (entladbar)** | Docling-Converter (inkl. interner OCR/Modelle), EasyOCR-Reader (`OCRProcessor.reader`, `VisionOCRProcessor.easyocr_reader`) |
| **Heiß (resident)** | Reranker (ONNX), NLI, Embeddings — werden NICHT berührt |
| **Nicht entladen (bewusst)** | Vision-Modell (= aktuell geladenes multimodales LLM; Produktion: Gemma 4 12B — Fallback-Modell nur, wenn das aktuelle LLM keine Vision hat) — lebt im geteilten `ModelLoader`-Singleton (dieselbe Slot wie das Produktiv-LLM); Entladung würde das Haupt-LLM verdrängen |
| **Verifiziert** | `py_compile` (9 .py-Dateien OK) · Smoke-Test `monitoring/_smoke_aux_release.py` (Registry, Cleanup-Idempotenz, Lazy-Reload, Docling-Cleanup) PASS · breiterer RAG/Reranker/Docling/OCR-Testlauf + End-to-End-VRAM-Check = offene Next-Steps |

### Funktionsweise

- **Lazy-Loading bleibt Basis:** Konstruktoren laden nichts — `OCRProcessor.reader` startet als `None`, `DoclingProcessor.get_instance()` ist ein reiner Singleton-Factory (Wrapper, `_initialized=False`). Converter/Reader laden erst via `_ensure_initialized()` / `_ensure_reader()` / `_ensure_easyocr_reader()` bei Verwendung.
- **Registries (WeakSet):** `OCRProcessor` und `VisionOCRProcessor` registrieren sich in einem modulinternen `weakref.WeakSet`; `get_active_ocr_processors()` / `get_active_vision_ocr_processors()` liefern die Live-Instanzen. WeakRef → kein Leak, normale GC bleibt erhalten.
- **Zentrale Freigabe:** `release_cold_aux_models(reason)` entlädt (1) Docling via `get_instance().cleanup()`, (2) alle registrierten OCR-Instanzen via `cleanup()`; danach `gc.collect()` + `torch.cuda.empty_cache()`. Defensive: ein Fehler bei einem Modell blockiert die anderen nicht (try/except, `logger.debug`). Rückgabe z. B. `["docling", "easyocr x2"]`.
- **Idempotenz:** Alle `cleanup()`-Methoden guarden auf `is not None` (`reader` / `easyocr_reader` / `_converter`) und sind mehrfach aufrufbar; zweiter Aufruf = no-op.
- **Lazy-Reload bleibt erreichbar:** Nach dem Release ist der Reader/Converter `None` → nächste Verwendung lädt transparent neu. Deshalb werden Instanzen **bewusst NICHT** aus der Registry entfernt (sonst würde ein nachfolgendes Release den neu geladenen Reader nicht finden).
- **Hook:** `ui_tabs/chat_tab.py` ruft im `finally`-Block der PDF-Verarbeitung `release_cold_aux_models(reason="pdf_import")` — Release-Fehler stören keinen erfolgreichen Import.

### Design-Prinzipien

- **Selektivität:** Nur KALTE Modelle (Docling, EasyOCR) werden entladen; HEISSE Query-Path-Modelle (Reranker/NLI/Embeddings) bleiben resident, um wiederholte Reload-Latenzen im Query-Path zu vermeiden.
- **Keine Silent-Fallbacks:** Defensive `try/except` um die Release-Aufrufe (Produktiv-Konvention), aber keine Schema-/Tabellen-Umgehungen; Fehler werden geloggt.
- **GPU-agnostisch:** `torch.cuda.empty_cache()` (kein hardcodiertes `cuda:N`); Platzierung bleibt über `utils/gpu_devices.py` (Single Source of Truth).
- **Kein Eager-Load:** `get_instance()` und OCR-Konstruktoren haben keine Modell-Load-Seiteneffekte.
- **VRAM-Isolation:** Die Freigabe gibt den CUDA-Cache an das OS zurück, damit die 3060-Ti-VRAM für den LLM-Query-Path frei ist.

### Grenzen / Next-Steps

- Release-Frequenz: nach jedem PDF-Import; bei sehr häufigen Importen dominiert die Lazy-Reload-Latenz (EasyOCR ~1–3 s, Docling ~5–15 s).
- Offene Next-Steps: breiterer Testlauf (RAG-Pipeline/-Search, Reranker/Cross-Encoder, Docling, OCR/Vision-OCR, GUI-Importpfad) + End-to-End-PDF-Import mit VRAM-Vorher/Nachher (nvidia-smi, LM Studio vorher schließen, sonst verfälscht).
- `monitoring/_smoke_aux_release.py` ist ein temporärer Smoke-Test — Entscheidung offen: behalten, in `tests/` migrieren oder entfernen.

## W. Legal/Ethical Compliance für web-sourced RAG-Persistierung (2026-08-30)

> **Stand:** 2026-08-30 | **Module:** `utils/web_compliance.py` (neu) + `agent/unified_rag_store.py` + `agent/rag_pipeline.py` + `agent/tools.py` + `agent/orchestrator.py`
> **Zweck:** Web-sourced Content (Snippets, `upsert_url`, Vision-URLs) wird im RAG-Store dauerhaft gehalten — das Compliance-Modell respektiert explizite Opt-Out-Signale der Quelle (robots-Disallow, noindex/nofollow, no-store) und das Retention-Modell löscht abgelaufenen Web-Content wieder (Default 30 Tage). Reine Python-Stdlib, keine neuen Abhängigkeiten, keine DB-Schema-Änderungen.

| Aspekt | Detail |
|--------|--------|
| **Dateien** | `utils/web_compliance.py` (Compliance + Retention-Helfer), `agent/unified_rag_store.py` (`retention_until`-Injektion, `prune_web_content()`, Gates in `upsert_url`/`upsert_url_with_vision`), `agent/tools.py` (Gate in `persist_to_rag` + Retention), `agent/rag_pipeline.py` (Gate Snippet-Fallback + Prune-Thread bei Start), `agent/orchestrator.py` (Gate Snippet-Fallback + Retention) |
| **Schichten** | robots.txt (`RobotsChecker`) → Response-Header (`check_response_headers`) → HTML-Meta (`check_html_meta`) → `decide()` → `gate_persistence()` |
| **Blockierend** | `Disallow`-Treff (längste Regel), `X-Robots-Tag`/`Googlebot`: noindex/nofollow/noarchive/nosnippet/noimageindex/nocache, `Cache-Control`/`Pragma: no-store`; `index`/`follow`/fehlende Signale = erlaubt |
| **Fail-Open** | robots-Fetch-/Parse-Fehler → erlaubt + WARNING (unerreichbare robots.txt bricht keine Persistierung); explizit erhaltene Opt-Out-Signale blocken immer hart |
| **Retention** | `retention_until` (ISO-8601) bei Persistierung injiziert; `WEB_RETENTION_DAYS` Default 30, `0`/negativ = unbegrenzt, ungültig → 30 + WARNING |
| **Pruning** | `prune_web_content(max_age_days=None, dry_run=False)`: nur `source_type LIKE 'web%'`, Ablauf via `retention_until` (Fallback `search_timestamp`/`date_stored`), Records ohne Zeitstempel werden übersprungen; Kind-Tabellen vor Dokument; Pipeline-Start = daemon-Thread (fail-soft) |
| **ENV** | `WEB_COMPLIANCE_ENABLED` (Master, Default aktiv), `WEB_RETENTION_DAYS` (Default 30) |
| **Tests** | `tests/test_web_compliance.py` — 47/47, hermetisch (injizierbarer `fetcher`, keine echten robots.txt-Downloads) |

### Funktionsweise

- **RobotsChecker:** Pro-Domain-Cache (~1 h TTL; Negativ-Cache 60 s nach Fetch-Fehler → kein Hammerschlag). Fetch via injizierbarem `fetcher` (Default: `urllib.request`, Timeout 5 s, max. 1 MB, UA `homebot-local-rag/1.0`). Parse via `urllib.robotparser` — aber **eigene Regelwahl** (s. u.). Thread-sicher (`threading.Lock`).
- **Eigene Regelwahl (CPython-Quirks):** (1) `_normalize_robots_text()` stellt `User-agent: *` vor, wenn Direktiven vor dem ersten UA-Block stehen (CPython würde sie still ignorieren). (2) `_select_entry()` + `_most_specific_rule()`: Entry-Auswahl wie CPython (Agent-spezifisch → `*`-Default), Regel-Auswahl nach **RFC 9309 §3 longest-match** (CPython `Entry.allowance()` nimmt die *erste* passende — `Disallow: /` + `Allow: /public` würde `/public` falsch blocken).
- **Reason-Strings:** `skipped` (keine http(s)-URL), `fail_open`, `robots_allow`, `robots_disallow`, `headers: …`, `meta: …`, `disabled` — deterministisch, von Tests abgedeckt.
- **Gate-Vertrag:** `gate_persistence(context, url, headers=None, html=None) -> bool`; bei Blockade WARNING-Log mit Kontext + Reasons und `False` (Caller persistiert nicht, kein Hard-Error). Leere URL → `True` (keine Web-URL).
- **Retention-Pfad:** `retention_until_iso()` → ISO-8601 UTC (`now + WEB_RETENTION_DAYS`) oder `None` (unbegrenzt/deaktiviert). `prune_web_content()` liest `MIN(retention_until)` via `json_extract(metadata, '$.retention_until')`, filtert `source_type LIKE 'web%'`, löscht Kind-Tabellen (Chunks/Evidence) vor dem Dokument.

### Design-Prinzipien

- **Konservativ, aber betriebsfest:** Explizite Opt-Outs blocken hart; unentscheidbare Fälle (Fetch-Fehler) fail-open mit Log — keine stummen Verwerfungen, kein Break der Persistierung.
- **Keine Silent-Fallbacks:** Jeder Fail-Open-Pfad loggt eine WARNING; ungültige ENV-Werte warnen und fallen auf Defaults zurück.
- **Root-Cause statt Workaround:** Die CPython-Robots-Quirks werden nicht umgangen, sondern durch korrekte RFC-9309-Regelwahl ersetzt (Regressions-Tests vorhanden).
- **Keine DB-Schema-Änderung:** Retention lebt im bestehenden `metadata`-JSON (`retention_until`) — Pruning ist reines SELECT/DELETE, ohne Migration.
- **Hermetische Tests:** Netzwerk vollständig simuliert (injizierbarer `fetcher`), keine echten robots.txt-Downloads in der CI.

### Grenzen / Next-Steps

- `prune_web_content()` läuft nur bei Pipeline-Start; bei dauerhaft laufender Instanz ist die Prune-Frequenz = App-Starts. Bei Bedarf: Intervall-Timer.
- Robots-Check erfolgt pro Persistierung (1 h Cache) — bei massenhafter URL-Persistierung pro Domain ist ein Fetch pro Stunde möglich (Negativ-Cache begrenzt Fehler-Frequenz auf 1/60 s).
- `WEB_RETENTION_DAYS` ist global; pro-Source-Overrides (z. B. Lizenzen) sind offen.
- Vollständige Doku: `docs/18_LEGAL_WEB_PERSIST.md`.

## X. Wellbeing De-Klinifizierung (Phase E, 2026-09-01)

> **Stand:** 2026-09-01 | **Module:** `wellbeing/schema_migration.py` (neu), `wellbeing/file_migration.py` (neu), `wellbeing/wellbeing_db.py`, `wellbeing/kg_faiss_manager.py`, `utils/db_path_resolver.py`, `wellbeing_session/lifecycle/*`, `wellbeing_session/services/async_service_container.py`, `scripts/db_backup.py`, `utils/intelligent_workspace_cleanup.py`
> **Zweck:** Umbenennung des klinischen Schemas (Tabellen/Indizes/Spalten), der DB-/Key-/Cache-Dateinamen und der genehmigten Code-Identifikatoren zu neutralen Namen (`wellbeing_*`). Reine Rename-Migration: Zeilen, FKs, verschlüsselte Daten und Key-Material bleiben vollständig erhalten. **Kein Security-Fix.**

| Aspekt | Detail |
|--------|--------|
| **Schema (E1)** | `wellbeing/schema_migration.py` → `migrate_wellbeing_schema(conn)`: Tabellen (z. B. `psychological_sessions`→`wellbeing_sessions`, `alliance_scores`→`engagement_scores`, `treatment_plans`→`care_plans`), Indizes (Drop/Recreate aus `sqlite_master.sql` — SQLite kennt kein `ALTER INDEX ... RENAME`), Spalte (`planned_interventions`→`planned_steps`); idempotent via `_schema_meta`-Sentinel; Namenskonflikt → `RuntimeError` (fail loud) |
| **Dateien (E2)** | `wellbeing/file_migration.py` → `migrate_wellbeing_files(new_db_path)`: `psychological_support.db[.key]`→`wellbeing_store.db[.key]`, `psychological_support_kg_cache/`→`wellbeing_kg_cache/`; WAL-Checkpoint (TRUNCATE) vor dem Move; DB+Key als Paar via `os.replace` (Key-Fehler → DB-Rollback); `-wal`/`-shm`-Sidecars mitgenommen; fehlender/ungültiger Key oder alte+neue Datei gleichzeitig → `RuntimeError`; Cache-Konflikt → beide Verzeichnisse bleiben |
| **Reihenfolge (kritisch)** | E2 läuft in `WellbeingDatabase.__init__` **vor** `_init_encryption()` — sonst würde neben der leeren neuen DB ein FRISCHER Key generiert und die verschlüsselten Legacy-Daten wären verwaist (stummer, unwiederbringlicher Datenverlust) |
| **Auflösung** | `utils/db_path_resolver.py` (`get_wellbeing_path()`/`get_kg_path()` → `wellbeing_store.db`), `wellbeing/kg_faiss_manager.py` (Cache-Dir `wellbeing_kg_cache/`), `wellbeing_session/lifecycle/*` + `services/async_service_container.py` (`DB_PATH`-Konstanten), `scripts/db_backup.py` (Zielname), `utils/intelligent_workspace_cleanup.py` (Ausnahmelisten) |
| **Tests** | `tests/test_wellbeing_schema_migration.py` (7) + `tests/test_wellbeing_file_migration.py` (10) = 17/17 grün; Produktionskopie-Check: 212 Sessions / 2715 Interaktionen erhalten, echte Daten mit dem verschobenen Key entschlüsselbar, Key byte-identisch |
| **Out of Scope** | Plaintext-Klinikinhalte in Zeilen, 5P-Spaltennamen, `CaseFormulation`/`case_formulator`-Identifikatoren außerhalb der Matrix, KG-`source_type`-Werte, RAG-Taxonomie, ENV `PSYCHO_DB_KEY` (User-Vertrag, Umbenennung würde Umgebungen brechen) |

### Funktionsweise

- **E1 (Schema)** läuft in `_init_schema()` **vor** der DDL (`CREATE TABLE IF NOT EXISTS`) — sonst entstünden neben den umbenannten Legacy-Tabellen leere neue Tabellen, in denen die Daten stranden. FK-Referenzen trägt SQLite beim `ALTER TABLE RENAME` automatisch über; Indizes werden aus der gespeicherten Definition neu angelegt.
- **E2 (Dateien)** läuft am Anfang von `__init__` (nach Pfad-Resolution, vor Key-Auflösung): WAL-Checkpoint → Key validieren (muss gültiger Fernet-Token sein) → `os.replace` DB → `os.replace` Key (Fehler → DB wieder zurück) → Sidecars mitnehmen (Ziel **in parent** verankert — relative Pfade würden ins CWD wandern!) → Cache-Dir.
- Beide Migrationen sind One-Shot und idempotent; der zweite Lauf ist ein No-Op (`moved=False` bzw. `already_migrated=True`).

### Design-Prinzipien

- **Fail loud, never silent:** Jeder unsichere Zustand (fehlender Key, ungültiger Key, alte UND neue Datei gleichzeitig) → `RuntimeError` mit Handlungsaufforderung — kein Überschreiben, kein Raten.
- **Paarintegrität:** DB und Fernet-Key werden niemals getrennt; ein abgebrochener Key-Move stellt den DB-Zustand wieder her.
- **Reihenfolge strukturell erzwungen:** Der E2-Aufruf in `__init__` vor `_init_encryption()` ist im Code fixiert, nicht Aufrufer-Vorgabe.
- **Renaming, kein Rewrite:** Null Änderung an Zeilendaten und Key-Material — ausschließlich Namen wechseln.

### Grenzen / Next-Steps

- Die Produktions-DB liegt noch unter dem Legacy-Namen und wird beim **nächsten App-Start** automatisch migriert (DB-Backup ist aktuell; `db_backup.py` benennt das Backup-Ziel bereits `wellbeing_store.db`).
- E1-Produktionsverifikation erfolgte auf einer Kopie (FK-Map-Vergleich statt `PRAGMA foreign_key_check`, da `personality_profiles` → `sessions` außerhalb der Rename-Matrix liegt).
- ENV-Name `PSYCHO_DB_KEY` bleibt (bewusst out of scope); bei Bedarf später mit Backward-Compatible-Fallback umbenennen.

## Y. Hardware-bewusste Token/Context-Skalierung (2026-09-03)

> **Stand:** 2026-09-03 | **Module:** `utils/token_scaling.py` (neu) + `scripts/model_loader.py` (Anbindung) + `enhanced_streamlit_bot.py` (Sidebar-Panel) + `i18n/locales/*.json` (`gui.token_scaling.*`)
> **Zweck:** Kontextfenster, Output-Budget und Thinking-Budget PRO HARDWARE und PRO MODELL aus VRAM + GGUF-Metadaten ableiten — mit jedem Wert als Regler (Auto → ENV → UI) und nie-feilender Persistenz pro Modell.

| Aspekt | Detail |
|--------|--------|
| **Dateien** | `utils/token_scaling.py` (PURE-Kern `compute_sweet_spot()`, `auto_proposal()`, `propose()`, Registry, Persistenz), `scripts/model_loader.py` (`propose`-Aufruf + `type_k`/`type_v` + OOM-Fallback), `enhanced_streamlit_bot.py` (Panel „Token Scaling" + `initialize_ai`-Flow) |
| **Kernlogik** | VRAM-Budget (LLM-GPU × 0.88) → KV-Budget (− Gewichte/mmproj − 1 GB − fester SSM-Overhead) → n_ctx-Kandidaten (65536…2048, ≤ requested) → KV-Quant (f16 → q8_0-Fallback) → Budgets (Reasoning: thinking ≤ 30 %/≤8192, output ≤ 50 %/≤16384; sonst output ≤ 40 %/≤8192); harte Invariante `thinking + output ≤ n_ctx − 2048` |
| **Präzedenz** | UI > ENV > Auto (`LLM_N_CTX`, `BOT_KV_QUANT`, `BOT_MAX_OUTPUT_TOKENS`, `BOT_THINKING_BUDGET`, `BOT_REASONING_EFFORT`); ungültige Werte fallen still auf Auto; Quelle pro Feld in `proposal.source` |
| **KV-Quant** | `kv_type_pair()` → `type_k`/`type_v` als **ggml_type** (f16 = 1, q8_0 = 8) an Llama-Constructor; llama-cpp-python 0.3.35 akzeptiert die Signaturen (verifiziert); `q8_0` in der UI **wählbar** (seit 2026-09-04 Runtime-validiert; Streamlit hat kein `disabled_options` — die Options-Liste ist das Gate, regressionsgesichert in `tests/test_streamlit_token_scaling_panel.py`) |
| **Reasoning** | Erkennung per Dateiname-Heuristik; Effort-Closed-Set pro Architektur (qwen35: xhigh/medium/low); `off` → thinking = 0; Thinking sonst nur gedeckelt, nie deaktiviert |
| **Persistenz** | `~/.cache/homebot/token_scaling_overrides.json` (flach: `{modell: {feld: wert}}`, atomar via tmp + `os.replace`); leere Overrides entfernen den Eintrag; `__all__` löscht die Datei; korrupt/fehlend = Auto (Warning) |
| **Verifiziert** | 44/44 Tests (`test_token_scaling_overrides.py` 38 + `test_model_loader_streaming.py` 3 + `test_streamlit_token_scaling_panel.py` 3, UI-Render-Regression) · Live-Smoke mit echtem Gemma4-12B-GGUF: Meta `48×16×512` → 1.572.864 KV-Byte/Token, f16→q8_0-Fallback getriggert, Persistenz-Roundtrip + Eintrag-Löschung OK · **q8_0-KV Runtime-Validierung (2026-09-04):** echtes Load + Generation mit `type_k=type_v=GGML_TYPE_Q8_0(8)` (Nemotron-3-Nano-4B Q4_K_M, n_ctx=4096, RTX 4090, neben laufendem LM Studio; VRAM-Leak-frei) |
| **Doku** | [docs/20_TOKEN_SCALING.md](docs/20_TOKEN_SCALING.md) (Algorithmus, ENV-Tabelle, Fallback-Matrix, API, CLI) |

### Funktionsweise

- **Vor dem Load (UI):** Das Sidebar-Panel berechnet `auto_proposal()` rein (VRAM-Query + GGUF-Meta + Dateigröße — kein Modell-Load) und zeigt Vorschlag + Quellen-Badges; gespeicherte Overrides (pro Modell-Key) sind die Widget-Startwerte.
- **Beim Load:** `initialize_ai()` übergibt `st.session_state.ts_overrides` an `load_model(..., token_scaling_overrides=…)`; der Loader ruft `token_scaling.propose(...)`, kapselt `n_ctx` auf den Vorschlag (Log: Vorher/Nachher) und registriert den Vorschlag thread-sicher (`set_current_proposal`) — Generierungs-Pfade lesen ihn ohne Weiterreichen.
- **Nach dem Load:** gesetzte Overrides → `save_overrides(model_key, …)`; alles Auto → `clear_overrides(model_key)` (Eintrag entfernt). Modell-Key = Modell-Pfad (dynamische Registry) bzw. Config-Key (statisch) — Panel und `initialize_ai` teilen sich denselben Key.
- **Budget-Ablösung:** `main_generation_max_tokens()` = `max(User-Einstellung, thinking + output)` — die User-Eingabe bleibt Minimum-Floor, nie wird unter ihr geliefert.

### Design-Prinzipien

- **Getrennte Regler statt einer Zahl:** n_ctx (VRAM-gekappt), Output-Budget, Thinking-Budget, KV-Quant, Reasoning-Effort sind unabhängig justierbar — SOTA-Prinzip für Reasoning-Modelle (Thinking aktiv, nur gedeckelt).
- **PURE-Kern:** `compute_sweet_spot()` ist deterministisch und 100 % ohne GPU/Dateien testbar; die Auto-Check-Schicht ist dünn und austauschbar.
- **Entkoppelt vom Loader:** `utils/token_scaling.py` importiert keinen `llama_cpp` — Tests und CLI (`python -m utils.token_scaling --model …`) laufen ohne Engine.
- **Never-failing:** fehlende VRAM-Query (→ 8 GB konservativ), fehlende Meta (→ Default), korrupte Persistenz (→ Auto), nicht akzeptierte `type_k`/`type_v` (→ kwargs entfernt) — die App-Initialisierung bricht nie ab.
- **Single Source of Truth:** KV-Bytes/Token aus GGUF-Metadaten (inkl. Hybrid-SSM-Trennung: nur Voll-Attention-Layer skalieren mit n_ctx); keine hardgecodeten LLM-Tokens.

### Grenzen / Next-Steps

- ~~**q8_0-Runtime-Validierung offen**~~ — **geschlossen (2026-09-04):** echtes Modell-Load + Generation mit `type_k=type_v=GGML_TYPE_Q8_0(8)` bestanden (Nemotron-3-Nano-4B Q4_K_M, n_ctx=4096, RTX 4090, neben laufendem LM Studio via `CUDA_VISIBLE_DEVICES`-Isolation, VRAM-Leak-frei). `q8_0` ist seither in `kv_options` wählbar; Regression in `tests/test_streamlit_token_scaling_panel.py` (`test_kv_quant_options_include_validated_q8_0`). Streamlit-historie: `disabled_options` ist kein Streamlit-KWarg (crashte den App-Start mit TypeError, 2026-09-04 behoben + regressionsgesichert).
- Reasoning-Erkennung ist Dateiname-Heuristik (konservativ); bei unbekannten Reasoning-Architekturen kann `thinking` zu niedrig ausfallen — UI-Override bleibt der Ausweg.
- Effort-Closed-Sets sind pro Architektur manuell gepflegt (aktuell nur `qwen35` verifiziert); neue Architekturen in `_EFFORT_SETS` ergänzen.


## Z. Video-Ingestion & Trusted-Channel-Gate (2026-09-07)

Lokale YouTube-Ingestion **nur für explizit erlaubte Channels**: UC-Channel-ID
ist der einzige Trust-Schlüssel (Name/Handle sind änderbar und spoofbar).
Fail-Closed: jede Fehlerquelle (Timeout, DNS, YouTube-Bruch, fehlende
Allowlist) = Ablehnung, **niemals** Freigabe.

### Kern-Komponenten

| Komponente | Datei | Funktion |
|------------|-------|----------|
| Allowlist | `config/trusted_channels.json` | 32 Channels (Wissenschaft, KI, Programmierung, offizielle API-/Tooling-Kanäle), `channel_id` (UC…) + Quellen-Nachweis; `status: approved` erforderlich |
| Gate-CLI | `scripts/verify_trusted_channels.py` | `resolve` (UC-Auflösung per `extract_flat`), `check` (Integrität), `verify <URL>` (Fail-Closed-Prüfung); `verify_video(url) -> (erlaubt, channel_id, name|Fehler)` |
| Pipeline | `scripts/ingest_video.py` | Gate → Metadaten → Subtitles (SRT/VTT-Parser, OpenCV-Frame-Extraktion) → `subtitles.json` + `metadata.json` + `frames/` + `manifest.json`; Exit 2 bei Gate-Verletzung |
| Tests | `tests/test_ingest_video_safety.py` | 18 offline-Tests: Injektions-/PII-Flagging, VTT-/SRT-Parsen, Frame-Limits, Gate-Fail-Closed (offline), 429-Retry (nur 429 → 1× 45s-Backoff, andere Fehler sofort) |

### Design-Prinzipien

- **UC-ID als Single Source of Truth:** `CHANNEL_ID_RE = ^UC[0-9A-Za-z_-]{22}$`;
  Name-Konsistenz ist nur eine Zusatz-Sicherung, nie ein Freigabe-Kriterium.
- **Fail-Closed:** `verify_video()` fängt alle Exceptions und liefert
  `(False, …)`; leere Allowlist = Abweisung; `ingest_video.py` stoppt
  vor Download/Extraktion (Exit 2).
- **Subtitles = untrusted data:** `subtitles.json` trägt eine
  Sicherheits-Envelope (`DATA_ONLY_NEVER_INSTRUCTIONS`), jede Zeile wird
  gegen Injektions-Muster (imperative Prompt-Phrasen DE/EN) und PII-Muster
  (E-Mail, Telefon, IBAN, Account-IDs) gecheckt — **flag-only**, nie
  Block, nie Ausführung.
- **Kein ffmpeg-Dependency:** Frame-Extraktion via OpenCV
  (`cv2.VideoCapture`, `cv2.imwrite`); Skala 480p, ≤12 Frames.
- **yt-dlp==2026.8.19** (pinned, Unlicense, 0 Runtime-Deps):
  `extract_info` nur (kein Video-Download), `extract_flat=True` bei
  Channel-URLs (sonst hängen Voll-Enumerations).
- **Keine Cloud-/LLM-Abhängigkeit im Gate-Pfad:** rein deterministisch.
- **Handle-Auflösung ist NICHT vertrauenswürdig (2026-09-07 belegt):**
  `@SamWitteveen` → Squatter-Kanal `samwitteveen` (22 Follower),
  `@CoreySchafer` → `Coreyschafer` (desc „what?"). Beide echten Channels
  wurden daher über **Original-Videos** verifiziert (Video-`channel_id` +
  Display-Name), nicht über die Handle-Tab-Extraktion. Regel: UC-ID immer
  gegen echten Kanalinhalt prüfen; ein reiner Handle-Namenmatch genügt nicht.
- **429-Rate-Limit-Härtung (2026-09-07):** yt-dlp-Optionen `sleep_interval_subtitles=5` +
  `sleep_requests=0.75`; Subtitle-Download (`_download_subtitles_with_retry`) mit genau
  **einem** 45s-Backoff-Retry, der **nur** auf HTTP-429/„Too Many Requests" (IP-Rate-Limit)
  reagiert — alle anderen Fehler brechen sofort ab (fail-closed). PO-Tokens bewusst NICHT
  im Einsatz (relevant v. a. für Player/GVS + übersetzte Untertitel, nicht Original-Untertitel).

### Grenzen / Next-Steps

- YouTube kann PO-Token/Prompt-Walls erzwingen (Release-Zyklen nötig —
  pinned Version + Update-Rhythmus dokumentiert).
- Audio-Transkription (Whisper) bewusst **nicht** im Scope; nur vorhandene
  Subtitles werden genutzt.
- Scanner: bestehende P1-Findinge (u. a. `pillow==12.0.0`) sind
  dokumentiert akzeptiert — `yt-dlp` ist scanner-sauber
  (Workdoc §Scanner-Policy).

## AA. Chat-Perf-Telemetrie (2026-09-11)

Low-Overhead-Flaschenhals-Diagnose pro Chat-Run: **exakt 1 SQLite-Zeile/Run**
(TTFT, Total, Route, Steps, Token, Engine-Metriken) — ohne UX-Kosten.
Hintergrund: Messwerte waren zuvor nur transiente UI-Events;
`performance_metrics.db` tot seit 2025-09-05.

### Kern-Komponenten

| Komponente | Datei | Funktion |
|------------|-------|----------|
| Recorder | `utils/chat_perf_recorder.py` (662 Zeilen) | `chat_perf_runs`-Schema + `ChatPerfRecord` + bounded Queue + Daemon-Writer-Thread (batched SQLite, WAL) + Observer (`make_recording_sink`) + Engine-Brücke + Kill-Switch; `py_compile`-sauber, wirft nie |
| Sink-Hook | `agent_chatbot_logic.py:1321` | `sink=chat_perf_recorder.make_recording_sink(event_queue.put)` — Base-Sink läuft **zuerst**, Telemetrie best-effort danach (Exception-Fang, kein Stream-Break) |
| Engine-Hooks | `scripts/model_loader.py:1635-1653, 3273-3298` | `begin_llm_call`/`end_llm_call` um `fn()` in `_resilient_llm_call` (fehlgeschlagene Retries **nicht** akkumuliert) + `generate_response_stream` (Cancel → Partial-Metriken). Alle übrigen `create_completion`-Call-Sites laufen durch `_resilient_llm_call` |
| Report | `scripts/perf_report.py` (251 Zeilen) | p50/p90/p99 für `ttft_ms`/`total_ms`/`tokens_per_second` je Route + Tag, Engine-Zeiten (Prefill/Generation), **Overhead-Zerlegung** (Pipeline vs. LLM), `recent_runs` mit `step_summary`/`trace_summary` (schema-adaptiv), `--route/--days/--json/--all` |
| Tests | `tests/test_chat_perf_telemetry.py` | 19 Tests: Kill-Switch, Observer (completed/failed/cancelled/foreign), tok/s-Fallback, bounded Queue, Writer-Persistenz, Engine-Metriken (Stub, Thread-Isolation, No-ctx, Negativ-Werte), Trace-Summary (Build, Persistenz, NULL-Trace, Migration) |
| Workdoc | `docs_archive/chat_perf_telemetry_workdoc_20260911.md` | DoD 8/8 ✅, Alternativen-Auswahl, Testnachweise |

### Design-Prinzipien

- **Best-Effort, nie blockierend:** Producer macht nur `put_nowait` (O(1),
  I/O-frei); I/O ausschließlich im Daemon-Writer; Queue begrenzt
  (älteste Zeile drop + `dropped`-Zähler) — UX-Pfad bleibt unangetastet.
- **1 Record/Run, nicht pro Token:** Token-Zahlen kommen aus C++
  (`llama_perf_context`: `n_p_eval`/`n_eval`/`t_p_eval_ms`/`t_eval_ms`),
  kein Python-Token-Counting im Hot-Path.
- **Engine-Metriken thread-lokal:** `consume_engine_metrics()` liest aus
  `_tls` — Background-Threads (KG-Extraktion, Vision) kontaminieren den
  Chat-Run nicht; akkumuliert über alle Calls eines Runs
  (Routing + Antwort), konsumiert beim Finalize.
- **Kein Hardcoden:** DB-Pfad via `utils/db_path_resolver.get_db_path("chat_perf.db")`;
  Engine-Bindings via `llama_cpp.llama_cpp` (0.3.35), Feature-Check
  (`llama_perf_context`/`llama_perf_context_reset` + `llm.ctx`) mit
  Silent-Noop-Fallback.
- **PII-frei:** nur IDs, Routen, Zeiten, Token-Zahlen, generische Step-Labels.
- **Kill-Switch:** `HOMEBOT_CHAT_PERF_DISABLED=1` → Recorder + Engine-Capture
  inaktiv (Hot-Pfad = no-op), ohne Code-Änderung.

### Real-Daten-Fixes & Erweiterungen (2026-09-12)

Nach den ersten 4 realen Runs (simple ×2, react, plan_execute) drei
Lücken gefixt:

- **`tokens_per_second` war immer NULL:** Die App emittiert
  `usage_updated` nur mit `ttft_ms` (`agent_chatbot_logic.py`); die
  Fallback-Logik in `_finalize` leitet tok/s jetzt aus den
  Engine-Metriken ab: `Σ generation_tokens / Σ generation_ms` (gewichtet
  über alle Calls des Runs, llama.cpp-Präzision). Die 4 Bestands-Rows
  wurden einmalig nachgebucht (identische Formel).
- **Step-Dauern fehlten:** `StepFinished` wurde ohne `duration_ms`
  emittiert, obwohl das Schema sie trägt → `stream_chat_events`
  (`agent_chatbot_logic.py`) timet jetzt Step-Start und setzt
  `duration_ms`; die `step_summary`-Spalte zeigt dadurch `Label=Xms`
  pro Step — damit ist die plan_execute-Overhead-Zeit (~81 % der
  Laufzeit) Step-weise zuordenbar.
- **Test-Kontamination der Produktions-DB:** Die Streaming-Suites
  (ohne Telemetrie-Fixtures) schrieben synthetische 0–1-ms-Runs in die
  produktive `chat_perf.db` (8 Noise-Rows, entfernt). Fix: autouse-Fixture
  `_disable_chat_perf_telemetry` in `tests/conftest.py` setzt
  `HOMEBOT_CHAT_PERF_DISABLED=1` suite-weit (dynamisch wirksam, keine
  DB-Datei); die Telemetrie-Tests entfernen die Variable in ihrem
  eigenen autouse-Fixture (läuft nach der Conftest-Instanziierung —
  empirisch verifiziert) und nutzen eine tmp-DB.
- **Trace ging vor der Persistenz verloren:** `AgentTrace` ist ein
  Dataclass (`agent/agent_types.py`), die Serialisierung in
  `agent_chatbot_logic.py` prüfte aber `hasattr(..., "model_dump")` →
  immer `trace=None` im `ChatRunResult` → Recorder verwurft die Zeile.
  Fix: `_serialize_trace_value()`/`_trace_to_dict()` (rekursiv;
  Pydantic `model_dump` + Dataclass `vars()`, nicht-serialisierbare
  Objekte als `repr`, wirft nie) → `ChatRunResult.trace` ist jetzt
  ein plain dict. Der Recorder fasst es in der neuen Spalte
  **`trace_summary`** (TEXT) kompakt zusammen:
  `planner_ms/tools_ms/summarize_ms/verify_ms`, multi-hop-Felder,
  `planned=[...]`/`ran=[...]` Tools, Subquery-Anzahl, `ragStats[...]`
  (max. 2000 Zeichen; schwere Debug-Felder wie `tool_results`
  bewusst ausgeschlossen). Bestands-DBs werden beim Recorder-Start
  migriert (`ALTER TABLE ... ADD COLUMN trace_summary TEXT`, idempotent;
  verifiziert an einer Kopie der Produktions-DB mit 9 Real-Rows).
  `perf_report.py` wählt optionale Spalten jetzt schema-adaptiv
  (PRAGMA table_info) und zeigt `recent_runs` mit `step_summary` +
  `trace_summary`. Damit ist Tool-/RAG-Zeit pro Run (z. B. der
  ~869 s-RAG-Step von Run 16) in der DB nachweisbar.
  Tests: 19/19 `tests/test_chat_perf_telemetry.py`
  (u. a. `_build_trace_summary`, Observer-Persistenz, Migration).
- **Stale-Trace-Leak zwischen Runs (2026-09-12, an echten Daten gefunden):**
  `last_trace` (und `last_sources`/`last_graphics`/`last_files`) wurden in
  `chat()` nie pro Run zurückgesetzt — ein SIMPLE-Run erbe damit die
  `AgentTrace` des letzten Agent-Runs (beobachtet: identische
  `trace_summary` mit Phase-Summe 677 s in SIMPLE-Runs von 12 s/68 s).
  Fix: der Reset-Block in `chat()` (`agent_chatbot_logic.py`,
  „Reset vor jedem Chat") setzt jetzt `last_trace`, `last_sources`,
  `last_graphics`, `last_files` auf Leerwerte — `chat()` ist der
  einzige Einstiegspunkt aller Routen (`_chat_core` wird nur dort
  aufgerufen). Die 2 nachgewiesenen kontaminierten Produktions-Rows
  wurden einmalig repariert (`trace_summary=NULL`).
  Regressionstests: `tests/test_agent_chat_streaming.py`
  (`test_chat_resets_stale_run_state_before_each_run`,
  `test_stream_completed_result_has_no_stale_trace`) — empirisch
  verifiziert, dass sie ohne Fix fehlschlagen.

### Grenzen

- Background-LLM-Calls (Vision/OCR, KG-Extraktion) bewusst nicht gehookt
  (nicht Teil des Chat-Runs).
- `llm_calls` zählt nur Calls im Producer-Thread (aktuell: alle im
  Streaming-Thread — korrekt).
- Hypothesen (LLM-Dominanz, RAG-Kosten, KV-Reuse) erst nach ≥ 5 realen
  Runs per `perf_report.py` falsifizierbar.

---

## AB. Finance SOTA Phase 1 – Monarch-Core Prognosen (2026-09-12)

**Deterministische Cashflow-/Guthaben-Prognose für den Finance-Tab.**
Schedule-first-Hybrid: wiederkehrende Zahlungen als deterministische Anker +
variable Residuals (OLS-Trend × Kalendermonats-Saisonalität) + Residual-
Bootstrap-Konfidenzintervall + Guthaben-Projektion. Kein ML, kein LLM, keine
neuen Dependencies, kein Future-Leak.

| Aspekt | Detail |
|--------|--------|
| **Zweck** | SOTA-Haushaltsanalyse: Guthaben-Projektion, Fälligkeits-Kalender, Abo-Audit (Erfolgskriterien Cash Predict / PocketSmith / Finanzguru — siehe Workdoc) |
| **Tools** | `finance_upcoming_bills` (Fenster 1–180 Tage, Anchortag = Median der letzten 5 Buchungstage, Abo-Kennzeichnung) · `finance_cash_flow_forecast` (Horizont 1–24 Monate, Rückblick 3–36, Konfidenz 0.5–0.99, `include_balance`) · `finance_subscription_audit` (Monats-/Jahreskosten, Trend, letzte Preisänderung, Abo-Heuristik) |
| **Methodik** | Deterministischer Anteil: Recurring-Gruppen (≥ 2 Buchungen, `_recurring_groups`) mit Historien-Monatswert + nächstem Fälligkeitstag (`_next_due_on_or_after`). Stochastischer Anteil: variable Einnahmen/Ausgaben mit OLS-Trend (`_fit_trend`) × Monats-Indizes (`_seasonal_index`; Degradation auf 1.0 bei < 12 Monaten). Unsicherheit: Residual-Bootstrap (`_bootstrap_interval`, B=1000, fester Seed → deterministisch, Perzentil-Methode). |
| **Guthaben-Kurve** | Start = reales `balance_at` am Referenztag, einschliesslich interner Transfers. Nur der Cashflow-Fit schliesst Transfers aus. Nur bei IBAN + Einzelwährung, sonst `balance: null`. Monatsmodell ab Folgemonat, keine tagesgenaue Restmonatsprognose. |
| **Invarianten** | CI-Reihenfolge (untere ≤ Punkt ≤ obere), kein Future-Leak (`_facts_up_to`), Transfer-Ausschluss (`_non_transfer_clause`), Währungen getrennt (keine Kursumrechnung — keine lokalen Kursdaten) |
| **Registrierung** | `agent/tool_schemas.py` +3 Schemas · `agent_toolkit.py` +3 Dispatch-Wrapper · `agent/tool_profiles.py` `FINANCE_ANALYTICS` +3 · `finance/chat.py` Planner-Prompt + Reflector + Retry-Dispatch +3 (Fail-Fast-Validatoren bei Import) · `finance/query_reflector.py` / `finance/grammar_compiler.py` `_REFLECTOR_ACTIONS` + `FINANCE_TOOL_NAMES` +3 |
| **UI** | Neuer Sub-Tab „📈 Prognosen" (`finance/tab.py::_render_forecast_tab`): Konto-Filter, Slider (Horizont/Rückblick/Konfidenz/Fälligkeitsfenster), Monats-Tabelle, Plotly-Chart (KI-Band + Guthaben), Fälligkeits-Tabelle, Audit-Tabelle |
| **i18n** | `finance_ui.forecast.*` (43 Keys) + `finance_ui.tabs.forecast` (DE/EN/BG) |
| **Tests** | `tests/test_finance_monarch_core.py` — 26 Tests (saisonales Residual-Verhalten, Balance-Ketten-Korrektheit, CI-Reihenfolge, kein Future-Leakage, Transfer-Ausschluss, Multi-Currency, Anker-/Fenster-Logik, Audit-Klassifikation/-Totale); breitere Finance-Suite (13 Finance-Dateien + i18n + tool-profiles) 2026-09-12: **220/220 PASS** |
| **Abgelehnte Varianten** | ML (Prophet/ARIMA/XGBoost: Overfit bei 12–36 Monatspunkten, schwere Dependencies, Non-Determinismus, AGPL-Lizenzcheck) · LLM als Prognose-Engine (Arithmetik unzuverlässig, nicht reproduzierbar) · externe APIs/Cloud. Bewertung: 5 Kategorien (Korrektheit/Robustheit/Wartbarkeit/Performance/Migrationsrisiko) × 1–7 im Workdoc |
| **Grenzen & Next** | KI bei < 12 Monaten Historie grob (per `notes` deklariert). **Phase 2 (fertig 2026-09-15):** DB-backed Goals/Sinking Funds inkl. UI-Tab „🎯 Sparziele" (siehe unten), verifizierte Vertraege siehe `docs/03_FINANCE_MODULE.md` §19. **Phase 3 (offen):** Szenario-What-If-Engine, ML-Experimente, Anomalie-Erkennung 2.0. Doku: `docs/03_FINANCE_MODULE.md` §18; Workdoc: `docs_archive/finance_sota_phase1_workdoc_20260912.md`. |

### Sparziel-Projektion: Reparaturstand 2026-09-15

`FinanceTools.project_goal` projiziert ab dem Folgemonat; Stand und historische
Rate sind taggenau auf `reference_date` begrenzt (`_history_rate_cents`).
Erreichte Ziele liefern Restlaufzeit 0; vergangene Zieltermine behalten ihre
vorzeichenbehaftete Monatsdifferenz. DAO-Waehrungsdefault und API-Testvertraege
sind in `docs/03_FINANCE_MODULE.md` §19 dokumentiert. Sparziel-Suite: 43 Tests,
gemeinsame Finance-/Profil-Regressionspruefung: 211 Tests bestanden.

### Sparziele-Tab (Goals / Sinking Funds UI, 2026-09-15)

Neuer Finance-Sub-Tab „🎯 Sparziele" (`finance/tab.py::_render_goals_tab`) für
Sinking-Fund-Ziele: pro Ziel ein Konto, ein Zielbetrag und optional Monatsrate,
Zieldatum, Notizen; Status `active`/`paused`/`achieved`/`archived`.

| Aspekt | Detail |
|--------|--------|
| **Zweck** | Wiederauffangbare Großausgaben (Urlaub, Kfz, Reparaturen) als Sparziele führen, Buchungen gezielt einem Ziel zuordnen und Fortschritt/Projektion sichtbar machen |
| **Datenmodell** | `goals` (Ziel + akkumulierter Stand, `progress_cents`/`remaining_cents`/`progress_pct`) + `goal_contributions` (Buchung ↔ Ziel, `db_schema.py`); Fortschritt wird aus zugeordneten Buchungen abgeleitet, nie doppelt gezählt |
| **API** | `FinanceTools`: `list_goals` · `upsert_goal` · `set_goal_status` · `delete_goal` · `assign_goal_contribution` · `unassign_goal_contribution` · `list_goal_contributions` · `project_goal` (Projektion, Restlaufzeit, On-/Off-Track) · `suggest_goal_candidates` (wiederkehrende Zahlungen > 45 Tage, stabile Beträge, unzugeordnet) |
| **UI** | Konto-Guard (`goals.need_accounts`) · Anlage-Formular (Name, Konto, Zielbetrag, Monatsrate, Zieldatum, Notizen) mit Validierung (`_validate_goal_form`) · Ziele-Tabelle (Ziel/Gespart/Offen/%/Rate/Status) · pro Ziel: Status-Select, Projektions-Zeile (`_projection_headline_key`-Priorität: erreicht > overdue > on/off-Track > Monate/Monat), zugeordnete Buchungen mit Unassign, Buchungen-Zuordnen (nur noch freie, `_assignable_transactions`), Löschen (Bestätigung) · Kandidaten-Vorschläge mit Pre-Fill-Anlage (`_candidate_goal_params`: annual → monthly → average) |
| **i18n** | `finance_ui.tabs.goals` + `finance_ui.goals.*` (77 Keys, DE/EN/BG); alle Strings über `_tr()` mit Fallback |
| **Tests** | `tests/test_finance_goals_schema.py` (43 Tests: Schema/API/Projektion) · `tests/test_finance_tab_regressions.py` (+47 reine-Helfer-Tests: Status-Labels, Formular-Validierung, Cents-Konvertierung, Tabellen-Mapping, Kandidaten-Parameter, Projektions-Priorität, Selectbox-Filter) · `tests/test_i18n_consistency.py` (Key-Parität DE/EN/BG) — 2026-09-15: 52/52 + 57/57 PASS |

### Finance-Tab: Korrekturstand 2026-09-16

`_render_analytics_tab` filtert explizit nach Waehrung; `aggregate` gruppiert
auch in der DB waehrungsgetrennt. `monthly_report` reicht den Kontofilter
bis zu Budget-Istwerten weiter. Budgets sind positive CHF-Haushaltslimits
(`DEFAULT_CURRENCY`), Altvorzeichen werden beim Lesen normalisiert.
UI-/Chat-Kategoriezuweisungen erhaelten mit `overwrite_kind=False` den Typ.

`_render_goals_candidates` speichert Ergebnisse kontogebunden im Session-State,
sodass der Uebernahme-Klick im Folgedurchlauf funktioniert. `_render_goal_assign`
verwendet echte IDs mit `format_func`, schliesst global belegte Buchungen aus
und kann gleich beschriftete Buchungen unterscheiden. Ziel-/Kandidatentabellen
zeigen ihre Waehrung; Reports und angeforderte Projektionen bleiben bei Reruns
sichtbar. Monatseingaben werden kalendergueltig validiert, Kontotypen korrekt
vorbelegt. Grenzen und Vertraege: `docs/03_FINANCE_MODULE.md` §20.

Verifiziert: 344 breite synthetische Regressionstests; anschliessend 110
UI-/Goal- und 14 Analytics-Tests. Kein produktiver DB-/LLM-/GPU-Zugriff.
