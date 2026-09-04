<!-- last-verified: 2026-08-28 -->
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

### 3.1 `evaluate()` (~415 Zeilen, Zeile ~133-547)
**SOTA Evaluation-Pipeline.**

| Aspekt | Detail |
|--------|--------|
| **Zweck** | Führt umfassende Evaluationen des Agent-Systems durch |
| **Metriken** | Accuracy, Relevance, Completeness, Faithfulness, Answer-Relevance |
| **RAGAS Integration** | Nutzt RAGAS-Framework für RAG-spezifische Metriken |
| **Ausgabe** | Detailierte Scores + Reports + Visualisierungen |

---

## 4. `agent/sota_pipeline.py` – SOTA Pipeline

### 4.1 `_run_pipeline_step()` (~398 Zeilen, Zeile ~171-568)
**Pipeline-Step Execution.**

| Aspekt | Detail |
|--------|--------|
| **Zweck** | Führt einen einzelnen Pipeline-Schritt aus (z.B. Retrieval, Generation, Verification) |
| **Pipeline-Stages** | Retrieval → Generation → Verification → Post-Processing |
| **Parallel** | Schritte können parallel via ThreadPool ausgeführt werden |
| **Monitoring** | Jeder Schritt wird getimed + geloggt + gemessen |

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
| **Zweck** | Fuehrt lesende SQLite-Abfragen sowie 34 exponierte Finance-Tools aus |
| **Analysepfade** | Kategorie-/Gegenparteikosten, Kostenstruktur, wiederkehrende Ausgaben, Forecast, Anomalien, Budget-vs-Ist, Sparpotenzial, Trendbruch |
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
| **Existenzbeleg** | Direkte Abfrage von `psychological_sessions`; Manager-Cache ist nie autoritativ |
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
| **Integrität** | FK auf `psychological_sessions(id)` und Post-Insert-Invarianten; identische `(session, role, content_hash)`-Writes werden nur innerhalb von 30 Sekunden wiederverwendet |
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
| **Identität** | Response- und Session-Context-Builder akzeptieren ausschließlich die persistierte `psychological_sessions.user_id`; fehlende Identity ist ein expliziter Fehler |

### 11.8 Insight-Auswahl und Korrektur-Lifecycle

| Aspekt | Detail |
|--------|--------|
| **Schema-Owner** | `WellbeingDatabase` erstellt und migriert `psychological_insights`; Provider hängen nicht von einer vorherigen Extractor-Initialisierung ab |
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

**Datei:** `agent/change_detector.py` (662 Zeilen)
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

### SOTA Features
- Thread-pool parallele Verarbeitung mit memory-aware Batch-Sizing
- 2-stufiger PDF-Fallback: Docling -> pdfplumber (Root-Cause-Fix 2026-07-14: AdvancedPDFProcessor-Zweig entfernt — rief nie-existierendes `extract_text` und war zirkulär, da der Adapter selbst an Docling delegierte)
- DOCX-Verarbeitung mit Style-Erkennung (Heading vs Text)
- Progress-Callbacks und Cancellation-Support
- SHA256-Hashing für Change-Detection
- Async-Kompatibilität via run_in_executor

---

## Modul 21: agent/multimodal_rag.py

**Zweck:** Multi-modale Chunking- und Indexierungsbibliothek für RAG. Unterstützt Text, Tabellen, Figuren und Formeln als separate, abfragbare Einheiten mit Cross-Referenzen.

### Data Models

| Funktion / Klasse | Parameter | Rückgabe | Zusammenfassung |
|---|---|---|---|
| `ContentType` (Enum) | — | `text`, `table`, `figure`, `formula`, `mixed` | Enum zur Typisierung von Chunks. Jeder Typ wird im Index separat geführt. |
| `MultiModalChunk` (Pydantic) | `chunk_id`, `source_file`, `primary_content`, `content_type`, `page_number?`, `metadata?`, `sub_chunks?`, `cross_references?`, `hash_sha256?` | — | Zentrales Chunk-Objekt. `to_vector_payload()` exportiert ein Dict für Embedding-Datenbanken. `sub_chunks` enthalten z.B. einzelne Tabellenzeilen. |

### Chunker

| Funktion | Parameter | Rückgabe | Zusammenfassung |
|---|---|---|---|
| `MultiModalChunker.__init__` | `chunk_size: int = 1000`, `chunk_overlap: int = 200`, `language: str = "de"` | — | Konfiguriert Chunk-Größe, Overlap und Sprache. |
| `.chunk_text(text, source, page?)` | text, source, page? | `List[MultiModalChunk]` | Splittet Text an Satzgrenzen (`(?<=[.!?])\s+(?=[A-Z])`). Kleine Texte werden ganz zurückgegeben. |
| `._split_large_text(text, source, page?)` | text, source, page? | `List[MultiModalChunk]` | Akkumuliert Sätze bis `chunk_size` erreicht; dann neuer Chunk mit 25% Overlap der letzten Sätze. |
| `._split_sentences(text)` | text | `List[str]` | Regex-basierte Satzsegmentierung, die Abkürzungen teilweise respektiert. |
| `.chunk_table(table: TableStructure)` | TableStructure | `List[MultiModalChunk]` | Erzeugt einen Haupt-Chunk (Natural-Language-Beschreibung) + Sub-Chunks pro Tabellenzeile + Markdown-Chunk. |
| `._row_to_sentence(row, columns)` | row, columns | `str` | Konvertiert eine Tabellenzeile in "Spalte1: Wert1; Spalte2: Wert2". |
| `.chunk_figure(figure: FigureDescription)` | FigureDescription | `MultiModalChunk` | Erzeugt einen Chunk aus `figure.index_content` mit Metadaten (caption, description). |
| `.chunk_formula(formula: FormulaBlock)` | FormulaBlock | `MultiModalChunk` | Erzeugt einen Chunk aus `formula.index_content` mit LaTeX und Beschreibung. |
| `.chunk_mixed_content(sections, source)` | `List[Dict]`, source | `List[MultiModalChunk]` | Iteriert über gemischte Sektionen (text/table/figure/formula) und ruft die passenden Chunker auf. Verlinkt Cross-Referenzen pro Seite. |
| `._link_cross_references(chunks)` | chunks | — | Gruppiert Chunks nach Seite; jeder Chunk bekommt die IDs der anderen Chunks derselben Seite als `cross_references`. |

### Index

| Funktion | Parameter | Rückgabe | Zusammenfassung |
|---|---|---|---|
| `MultiModalRAGIndex.__init__` | — | — | Leere Indexe: `_index` ( Haupt-Dict), `_type_index`, `_source_index`, `_page_index`, `_hash_index`. |
| `.add_chunk(chunk)` | MultiModalChunk | `bool` | Fügt Chunk hinzu; `False` bei Duplikat (gleiche chunk_id). Aktualisiert alle 5 Indexe. |
| `.add_chunks(chunks)` | `List[MultiModalChunk]` | `int` | Fügt mehrere Chunks hinzu; gibt Anzahl neuer Chunks zurück. |
| `.remove_by_source(source)` | source | `int` | Entfernt alle Chunks einer Quelle aus allen Indexen. |
| `.get_chunk(chunk_id)` | chunk_id | `Optional[MultiModalChunk]` | Lookup by ID. |
| `.get_by_source(source)` | source | `List[MultiModalChunk]` | Alle Chunks einer Datei. |
| `.get_by_type(content_type)` | ContentType | `List[MultiModalChunk]` | Alle Chunks eines Typs. |
| `.get_by_page(page)` | page | `List[MultiModalChunk]` | Alle Chunks einer Seite. |
| `.get_with_sub_chunks(chunk_id)` | chunk_id | `Optional[Dict]` | Chunk mit expandierten Sub-Chunks als Vector-Payload. |
| `.expand_query(query, include_types?)` | query, include_types? | `str` | Fügt kontextbezogene Suchbegriffe hinzu (z.B. "tabellarische Daten" bei Tabellentermen). |
| `.stats()` | — | `Dict` | Statistik: total_chunks, by_type, sources, pages_indexed. |
| `.clear()` | — | — | Löscht den gesamten Index. |

### Compatibility Wrapper

| Funktion | Parameter | Rückgabe | Zusammenfassung |
|---|---|---|---|
| `MultiModalRAG.__init__` | `chunk_size=1000`, `chunk_overlap=200`, `include_tables=True`, `include_diagrams=True`, `include_formulas=True`, `language="de"` | — | Erbt von `MultiModalRAGIndex` + hält `MultiModalChunker`. Wird von SOTA-Pipeline verwendet. |
| `.chunk_document(content, metadata?)` | content, metadata? | `List[Dict]` | Chunkt Dokument in pipeline-kompatible Dicts via `chunker.chunk_text()` -> `to_vector_payload()`. |

### Factory Functions

| Funktion | Parameter | Rückgabe | Zusammenfassung |
|---|---|---|---|
| `create_chunker(chunk_size, chunk_overlap)` | chunk_size, chunk_overlap | `MultiModalChunker` | Factory für Standard-Chunker. |
| `create_index()` | — | `MultiModalRAGIndex` | Factory für leeren Index. |

### SOTA Features
- 5 Indexe (Haupt, Typ, Quelle, Seite, Hash) für O(1)-Lookups
- Hash-basierte Deduplizierung (SHA256)
- Cross-Referenzen zwischen Chunks derselben Seite
- Query-Expansion mit kontextbezogenen Suchbegriffen
- Sub-Chunks für granulare Tabellenzeilen-Suche
- Backwards-kompatible Aliase (`MultimodalRAG = MultiModalRAG`)

### Modul: `agent/multimodal_rag.py` - Multi-Modal RAG Chunking & Indexing

| # | Funktion/Klasse | Zeile | Beschreibung |
|---|----------------|-------|-------------|
| 1 | `ContentType` (Enum) | ~18 | Enum für Inhaltstypen: TEXT, TABLE, FIGURE, FORMULA |
| 2 | `TableStructure` (dataclass) | ~27 | Strukturierte Tabelle mit ID, Spalten, Zeilen, Caption, Source-Tracking |
| 3 | `TableStructure.index_content` (property) | ~52 | Generiert indexierbaren Text aus Tabellendaten |
| 4 | `TableStructure.markdown_export` (property) | ~60 | Exportiert Tabelle als Markdown-String |
| 5 | `TableStructure.natural_language` (property) | ~75 | Konvertiert Tabelle zu natürlichsprachiger Beschreibung |
| 6 | `FigureDescription` (dataclass) | ~92 | Beschreibung einer Figur/Diagramms mit Caption, Description, Alt-Text |
| 7 | `FigureDescription.index_content` (property) | ~103 | Generiert indexierbaren Text aus Figur-Beschreibung |
| 8 | `FormulaBlock` (dataclass) | ~110 | Mathematische Formel mit LaTeX, Plain-Text, Description |
| 9 | `FormulaBlock.index_content` (property) | ~121 | Generiert indexierbaren Text aus Formel |
| 10 | `MultiModalChunk` (dataclass) | ~131 | Chunk mit multi-modalem Inhalt (Text, Tabelle, Figur, Formel) |
| 11 | `MultiModalChunk.to_vector_payload()` | ~143 | Erstellt Payload-Dictionary für Vektor-Indexierung |
| 12 | `MultiModalChunker.__init__()` | ~172 | Initialisiert Chunker mit chunk_size, overlap, language |
| 13 | `MultiModalChunker.chunk_text()` | ~187 | Chunkt reinen Text mit Satzgrenzen-Erkennung |
| 14 | `MultiModalChunker._split_large_text()` | ~220 | Splitet großen Text mit Overlap an Satzgrenzen |
| 15 | `MultiModalChunker._split_sentences()` | ~254 | Splitet Text in Sätze (respektiert Abkürzungen) |
| 16 | `MultiModalChunker.chunk_table()` | ~266 | Erstellt Chunks aus strukturierter Tabelle (NL + Markdown + Row-SubChunks) |
| 17 | `MultiModalChunker.chunk_figure()` | ~329 | Erstellt Chunk für Figur/Diagramm |
| 18 | `MultiModalChunker.chunk_formula()` | ~350 | Erstellt Chunk für mathematische Formel |
| 19 | `MultiModalChunker.chunk_mixed_content()` | ~371 | Verarbeitet gemischten Inhalt (Text + Tabellen + Figuren + Formeln) |
| 20 | `MultiModalChunker._link_cross_references()` | ~419 | Erstellt Cross-References zwischen Chunks derselben Seite |
| 21 | `MultiModalRAGIndex.__init__()` | ~448 | Initialisiert Index mit Type/Source/Page/Hash-Indizes |
| 22 | `MultiModalRAGIndex.add_chunk()` | ~459 | Fügt Chunk hinzu (False bei Duplikaten) |
| 23 | `MultiModalRAGIndex.add_chunks()` | ~484 | Fügt multiple Chunks hinzu, gibt Anzahl neuer Chunks zurück |
| 24 | `MultiModalRAGIndex.remove_by_source()` | ~492 | Entfernt alle Chunks einer Quelle |
| 25 | `MultiModalRAGIndex.get_chunk()` | ~522 | Gibt Chunk nach ID zurück |
| 26 | `MultiModalRAGIndex.get_by_source()` | ~526 | Gibt alle Chunks einer Quelle zurück |
| 27 | `MultiModalRAGIndex.get_by_type()` | ~531 | Gibt alle Chunks eines bestimmten Typs zurück |
| 28 | `MultiModalRAGIndex.get_by_page()` | ~536 | Gibt alle Chunks einer bestimmten Seite zurück |
| 29 | `MultiModalRAGIndex.get_with_sub_chunks()` | ~541 | Gibt Chunk mit expandierten Sub-Chunks zurück |
| 30 | `MultiModalRAGIndex.expand_query()` | ~555 | Erweitert Query um cross-modal Kontext (Table/Figure/Formula-Hints) |
| 31 | `MultiModalRAGIndex.stats()` | ~588 | Gibt Index-Statistiken zurück |
| 32 | `MultiModalRAGIndex.clear()` | ~604 | Leert den gesamten Index |
| 33 | `MultiModalRAG.__init__()` | ~616 | Kompatibilitäts-Wrapper für SOTA-Pipeline |
| 34 | `MultiModalRAG.chunk_document()` | ~629 | Chunkt Dokument in pipeline-freundliche Dictionaries |
| 35 | `create_chunker()` | ~649 | Factory-Funktion: erstellt MultiModalChunker mit Defaults |
| 36 | `create_index()` | ~654 | Factory-Funktion: erstellt leeren MultiModalRAGIndex |

### SOTA Features
- Content-type-aware Chunking (Text, Table, Figure, Formula)
- Sentence-boundary splitting mit Overlap für kohärente Chunks
- Cross-modal Query Expansion (Query-Hints basierend auf Inhaltstyp)
- Hash-basierte Deduplizierung (SHA256)
- Multi-Index-System (Type, Source, Page, Hash)
- Sub-Chunk-Support für Tabellenzeilen
- Cross-Reference-Linking zwischen Chunks derselben Seite

<!-- Nächstes Modul: agent/strixkat_eval.py -->

## 21. `agent/multimodal_rag.py` – Multi-Modal RAG Chunking & Indexing (~706 Zeilen)

### Zweck
Spezifisches Chunking und Indexieren für multi-modale Inhalte (Text, Tabellen, Figuren, Formeln) aus PDF-Dokumenten. Arbeit eng mit `docling_parallel.py` zusammen, um die von Docling extrahierten strukturierten Sektionen in vektor-datenbank-fähige Chunks zu verwandeln.

### Datenmodelle

| Nr. | Klasse / Enum | ~Zeile | Beschreibung |
|-----|-------------|--------|-------------|
| 1 | `ContentType` (enum) | ~30 | Enum für Inhaltstypen: `text`, `table`, `figure`, `formula` |
| 2 | `TableStructure` | ~40 | Pydantic-Modell für Tabellendaten: `table_id`, `columns`, `rows`, `caption`, `source_file`, `page` |
| 3 | `FigureDescription` | ~55 | Pydantic-Modell für Figuren: `figure_id`, `caption`, `description`, `source_file`, `page` |
| 4 | `FormulaBlock` | ~68 | Pydantic-Modell für Formeln: `formula_id`, `latex`, `description`, `source_file`, `page` |
| 5 | `MultiModalChunk` | ~80 | Zentrales Chunk-Modell: `chunk_id`, `content_type`, `primary_content`, `metadata`, `page_number`, `source_file`, `hash_sha256`, `cross_references`, `sub_chunks` |

### `MultiModalChunk` Methoden

| Nr. | Methode | ~Zeile | Beschreibung |
|-----|---------|--------|-------------|
| 6 | `MultiModalChunk.to_vector_payload()` | ~120 | Wandelt Chunk in Dictionary für Vektor-Datenbank um (content, metadata, embedding-ready) |
| 7 | `MultiModalChunk.compute_hash()` | ~135 | Berechnet SHA256-Hash des primären Inhalts |
| 8 | `MultiModalChunk.generate_id()` | ~142 | Generiert eindeutige Chunk-ID basierend auf Typ+Seite+Index |

### `MultiModalChunker` Klasse (~200 Zeilen)

| Nr. | Methode | ~Zeile | Beschreibung |
|-----|---------|--------|-------------|
| 9 | `MultiModalChunker.__init__()` | ~170 | Konfiguration: `chunk_size`, `chunk_overlap`, `language` |
| 10 | `MultiModalChunker.chunk_text()` | ~185 | Chunkt reinen Text mit Overlap-Support (split by sentences/words) |
| 11 | `MultiModalChunker.chunk_table()` | ~220 | Wandelt `TableStructure` in Chunks um: jede Zeile wird zu einem eigenen Chunk mit Spalten-Namen als Kontext |
| 12 | `MultiModalChunker.chunk_figure()` | ~250 | Erzeugt Chunk aus `FigureDescription` (Caption + Description als Inhalt) |
| 13 | `MultiModalChunker.chunk_formula()` | ~260 | Erzeugt Chunk aus `FormulaBlock` (LaTeX + Description als Inhalt) |
| 14 | `MultiModalChunker.chunk_mixed_content()` | ~275 | Verarbeitet Liste von gemischten Sektionen (Text/Tabellen/Figuren/Formeln) und erstellt cross-references |
| 15 | `MultiModalChunker._link_cross_references()` | ~419 | Erstellt Cross-References zwischen Chunks derselben Seite |

### `MultiModalRAGIndex` Klasse (~170 Zeilen)

| Nr. | Methode | ~Zeile | Beschreibung |
|-----|---------|--------|-------------|
| 16 | `MultiModalRAGIndex.__init__()` | ~448 | Initialisiert 5 Indizes: `_index` (Haupt), `_type_index`, `_source_index`, `_page_index`, `_hash_index` |
| 17 | `MultiModalRAGIndex.add_chunk()` | ~459 | Fügt Chunk hinzu; gibt `False` bei Duplikaten (hash-basiert) |
| 18 | `MultiModalRAGIndex.add_chunks()` | ~484 | Fügt mehrere Chunks hinzu; gibt Anzahl neuer Chunks zurück |
| 19 | `MultiModalRAGIndex.remove_by_source()` | ~492 | Entfernt alle Chunks einer Quelle (säubert alle 5 Indizes) |
| 20 | `MultiModalRAGIndex.get_chunk()` | ~522 | Gibt Chunk per ID zurück |
| 21 | `MultiModalRAGIndex.get_by_source()` | ~526 | Gibt alle Chunks einer Quelle zurück |
| 22 | `MultiModalRAGIndex.get_by_type()` | ~531 | Gibt alle Chunks eines Content-Typs zurück |
| 23 | `MultiModalRAGIndex.get_by_page()` | ~536 | Gibt alle Chunks einer Seite zurück |
| 24 | `MultiModalRAGIndex.get_with_sub_chunks()` | ~541 | Gibt Chunk mit expandierten Sub-Chunks zurück |
| 25 | `MultiModalRAGIndex.expand_query()` | ~555 | Erweitert Query um cross-modal Kontext (Tabellen-/Figuren-/Formel-Terme erkennen) |
| 26 | `MultiModalRAGIndex.stats()` | ~588 | Gibt Index-Statistiken zurück (total, by_type, sources, pages_indexed) |
| 27 | `MultiModalRAGIndex.clear()` | ~604 | Leert den gesamten Index |

### `MultiModalRAG` Klasse (~15 Zeilen)

| Nr. | Methode | ~Zeile | Beschreibung |
|-----|---------|--------|-------------|
| 28 | `MultiModalRAG.__init__()` | ~616 | Erbt von `MultiModalRAGIndex`; erstellt `MultiModalChunker`; Flags für tables/diagrams/formulas |
| 29 | `MultiModalRAG.chunk_document()` | ~629 | Pipeline-Wrapper: chunkt Dokument und gibt Liste von Dicts zurück |

### Factory-Funktionen

| Nr. | Funktion | ~Zeile | Beschreibung |
|-----|----------|--------|-------------|
| 30 | `create_chunker()` | ~649 | Erstellt `MultiModalChunker` mit Default-Settings (chunk_size=1000, overlap=200) |
| 31 | `create_index()` | ~654 | Erstellt leeren `MultiModalRAGIndex` |

### Kompatibilitäts-Aliase
- `MultimodalRAG = MultiModalRAG` (Zeile ~643) – für case-insensitive Imports im Codebase

### SOTA Features
- 5-facher Index (Haupt + Typ + Quelle + Seite + Hash) für O(1)-Lookup
- Hash-basierte Deduplizierung verhindert doppelte Chunks
- Cross-Reference-Verknüpfung zwischen Chunks derselben Seite
- Query-Expansion erkennt kontextuelle Hinweise (Tabellen/Figuren/Formeln)
- Content-Type-Filterung für gezielte Retrieval-Szenarien
- Vollständige Pydantic-V2-Modelle für Typ-Sicherheit

### agent/multimodal_rag.py

| # | Funktion / Klasse | Zeile | Beschreibung |
|---|---|---|---|
| 1 | `ContentType` (Enum) | ~52 | Inhaltstypen: TEXT, TABLE, FIGURE, FORMULA, HEADER, CODE, MIXED |
| 2 | `TableStructure` (dataclass) | ~61 | Strukturierte Tabellendaten mit ID, Spalten, Zeilen, Caption, Markdown-Export und Natural-Language-Darstellung |
| 3 | `TableStructure.markdown_export` (property) | ~78 | Generiert Markdown-Tabelle aus columns + rows |
| 4 | `TableStructure.natural_language` (property) | ~85 | Erstellt Natural-Language-Beschreibung der Tabellendaten (pro Zeile einen Satz) |
| 5 | `TableStructure.index_content` (property) | ~93 | Kombiniert caption + description + natural_language für Vector-Indexierung |
| 6 | `FigureDescription` (dataclass) | ~99 | Beschreibung von Abbildungen/Diagrammen mit ID, Caption, Description, Alt-Text, Source |
| 7 | `FigureDescription.index_content` (property) | ~103 | Kombiniert caption + description + alt_text für Indexierung |
| 8 | `FormulaBlock` (dataclass) | ~110 | Mathematische Formeln mit LaTeX, Plain-Text-Alternative und Beschreibung |
| 9 | `FormulaBlock.index_content` (property) | ~121 | Kombiniert description + plain_text (oder latex) für Indexierung |
| 10 | `MultiModalChunk` (dataclass) | ~131 | Chunk mit mehreren Inhaltstypen: chunk_id, source, primary_content, content_type, page, metadata, hash, sub_chunks, cross_references |
| 11 | `MultiModalChunk.to_vector_payload()` | ~143 | Erstellt Dictionary-Payload für Vector-Indexierung (chunk_id, content, content_type, source, page, metadata, hash, cross_refs) |
| 12 | `MultiModalChunker.__init__()` | ~172 | Initialisiert Chunker mit chunk_size, chunk_overlap, language und internem Zähler |
| 13 | `MultiModalChunker._next_chunk_id()` | ~179 | Generiert eindeutige Chunk-ID basierend auf Source-Datei und Zähler |
| 14 | `MultiModalChunker.chunk_text()` | ~187 | Chunkt Plain-Text mit Sentence-Boundary-Splitting; bei kleinem Text direkter Return, sonst _split_large_text |
| 15 | `MultiModalChunker._create_text_chunk()` | ~209 | Erstellt MultiModalChunk aus Text mit SHA256-Hash |
| 16 | `MultiModalChunker._split_large_text()` | ~220 | Splitet großen Text an Satzgrenzen mit Overlap (25% der aktuellen Sätze als Overlap) |
| 17 | `MultiModalChunker._split_sentences()` | ~254 | Splitet Text an Satzgrenzen (Regex: .!? + Space + Uppercase), respektiert Unicode-Umlaute |
| 18 | `MultiModalChunker.chunk_table()` | ~266 | Erstellt Chunks aus TableStructure: Haupt-Chunk (NL-Beschreibung) + Sub-Chunks (pro Zeile) + Markdown-Chunk |
| 19 | `MultiModalChunker._row_to_sentence()` | ~317 | Konvertiert Tabellenzeile in Natural-Language-Satz ("Spalte: Wert; Spalte: Wert") |
| 20 | `MultiModalChunker.chunk_figure()` | ~329 | Erstellt Chunk für Abbildung/Diagramm mit Caption, Description als Metadaten |
| 21 | `MultiModalChunker.chunk_formula()` | ~350 | Erstellt Chunk für mathematische Formel mit LaTeX und Beschreibung als Metadaten |
| 22 | `MultiModalChunker.chunk_mixed_content()` | ~371 | Verarbeitet gemischte Inhalte (Text + Tabellen + Figuren + Formeln), ruft passende chunk_*-Methode pro Section auf |
| 23 | `MultiModalChunker._link_cross_references()` | ~419 | Verknüpft Chunks derselben Seite als Cross-References (jeder Chunk kennt die anderen Chunk-IDs der Seite) |
| 24 | `MultiModalRAGIndex.__init__()` | ~448 | Initialisiert leeren Index mit Hauptindex, Type-Index, Source-Index, Page-Index und Hash-Index |
| 25 | `MultiModalRAGIndex.add_chunk()` | ~459 | Fügt Chunk hinzu; gibt False bei Duplikat (chunk_id existiert bereits); pflegt alle Indexe |
| 26 | `MultiModalRAGIndex.add_chunks()` | ~484 | Fügt mehrere Chunks hinzu; gibt Anzahl neuer Chunks zurück |
| 27 | `MultiModalRAGIndex.remove_by_source()` | ~492 | Entfernt alle Chunks einer Quelle aus allen Indexen; gibt Anzahl entfernter Chunks zurück |
| 28 | `MultiModalRAGIndex.get_chunk()` | ~522 | Gibt Chunk nach chunk_id zurück |
| 29 | `MultiModalRAGIndex.get_by_source()` | ~526 | Gibt alle Chunks einer Quelle zurück |
| 30 | `MultiModalRAGIndex.get_by_type()` | ~531 | Gibt alle Chunks eines Content-Typs zurück |
| 31 | `MultiModalRAGIndex.get_by_page()` | ~536 | Gibt alle Chunks einer bestimmten Seite zurück |
| 32 | `MultiModalRAGIndex.get_with_sub_chunks()` | ~541 | Gibt Chunk mit expandierten Sub-Chunks als Dictionary zurück |
| 33 | `MultiModalRAGIndex.expand_query()` | ~555 | Erweitert Query um Cross-Modal-Kontext: erkennt table/figure/formula-Terme und fügt deutsche Suchbegriffe hinzu |
| 34 | `MultiModalRAGIndex.stats()` | ~588 | Liefert Index-Statistiken: total_chunks, by_type, sources, pages_indexed |
| 35 | `MultiModalRAGIndex.clear()` | ~604 | Löscht den gesamten Index |
| 36 | `MultiModalRAG.__init__()` | ~613 | Kompatibilitäts-Wrapper: erbt von MultiModalRAGIndex, enthält MultiModalChunker-Instanz |
| 37 | `MultiModalRAG.chunk_document()` | ~629 | Chunkt Dokument-Inhalt in pipeline-freundliche Dictionaries (ruft chunker.chunk_text + to_vector_payload auf) |
| 38 | `MultimodalRAG` (alias) | ~643 | Backwards-kompatibler Alias für MultiModalRAG |
| 39 | `create_chunker()` | ~649 | Factory-Funktion: erstellt MultiModalChunker mit Default-Settings (chunk_size=1000, overlap=200) |
| 40 | `create_index()` | ~654 | Factory-Funktion: erstellt leeren MultiModalRAGIndex |

### SOTA Features
- Content-Type-aware Chunking: Text, Tabellen, Figuren, Formeln werden typ-spezifisch verarbeitet
- Sentence-Boundary-Splitting mit Overlap für natürliche Textgrenzen
- Tabellen werden als Natural-Language + Markdown + Row-Sub-Chunks indexiert
- Cross-Reference-Verknüpfung zwischen Chunks derselben Seite
- Query-Expansion: erkennt Inhaltstyp-Terme und erweitert um deutsche Suchbegriffe
- SHA256-basierte Deduplizierung
- Multi-Index-System: Hauptindex + Type-Index + Source-Index + Page-Index + Hash-Index

<!-- Nächstes Modul: agent/strixkat_eval.py -->

## Modul 21: `wellbeing_session/lifecycle/session_lifecycle_manager.py`

### Überblick
Session-Lifecycle-Manager für psychologische Support-Sessions: erstellt Sessions in SQLite, verfolgt Status (active/paused/ended), extrahiert User-Insights und stellt UI-Dialoge für Session-Abschluss bereit.

### Klassen & Funktionen

| # | Funktion/Klasse | Zeile | Beschreibung |
|---|----------------|-------|-------------|
| 1 | `_tr(key, default, **kwargs)` | ~30 | Helper: i18n-Translation mit Fallback; nutzt `i18n.translate()` oder default-String mit `.format(**kwargs)` |
| 2 | `_insight_type_label(insight_type)` | ~46 | Helper: gibt deutschen Label fuer Insight-Typ zurueck (life_event→"Lebensereignis", coping_mechanism→"Bewaeltigungsstrategie", etc.) |
| 3 | `_resolve_db_path()` | ~68 | Löst Datenbank-Pfad via `DbPathResolver` (fallback: `data/psychological_sessions.db`) |
| 4 | `SessionLifecycleManager.__init__()` | ~80 | Initialisiert mit `db_path`, `session_manager`-Referenz und Logger |
| 5 | `SessionLifecycleManager._get_db_connection()` | ~90 | Context-Manager: öffnet SQLite-Connection mit WAL-Mode + Foreign-Keys; committed/rollback in try/finally |
| 6 | `SessionLifecycleManager.cleanup_orphaned_sessions_on_startup()` | ~110 | Markiert alle `active` Sessions als `ended` deren `end_time` NULL ist und >24h alt; verhindert Waisen-Sessions nach Crash |
| 7 | `SessionLifecycleManager.create_and_start_new_session()` | ~145 | Erstellt neue Session in DB: generiert UUID, speichert `start_time`, `status='active'`; zeigt UI-Info-Box; gibt session_id zurueck (None bei Fehler) |
| 8 | `SessionLifecycleManager.end_current_session()` | ~228 | Beendet Session mit vollem UI-Dialog: Checkbox fuer Insight-Extraktion, Info-Button, Abbrechen/Bestaetigen-Buttons; ruft `extract_insights_func` auf; zeigt erkannte Insights mit Icon + Konfidenz; resettet Session-State; triggert `_end_session_fallback` + `st.rerun()` |
| 9 | `SessionLifecycleManager._end_session_fallback()` | ~351 | Fallback: stellt sicher dass Session in DB als `ended` markiert ist (UPDATE mit COALESCE für end_time) |

### Wichtige Details

**Session-DB-Schema:**
- Tabelle: `psychological_sessions`
- Spalten: `id` (UUID), `start_time`, `end_time`, `status` (active/paused/ended), `updated_at`
- WAL-Mode fuer concurrent reads

**Insight-Typen und Icons:**
| Typ | Icon | Label |
|-----|------|-------|
| `life_event` | 🎯 | Lebensereignis |
| `coping_mechanism` | 🛠️ | Bewaeltigungsstrategie |
| `personality` / `personality_trait` | 🧩 | Persoenlichkeit |
| `behavioral_pattern` | 🔄 | Verhaltensmuster |
| `emotional_state` | 💭 | Emotionaler Zustand |
| `relationship_dynamic` | 👥 | Beziehungsdynamik |
| `cognitive_pattern` | 💡 | Kognitives Muster |

**UI-Flow (end_current_session):**
1. Trennlinie + Header "Session-Abschluss"
2. Spalten-Layout: Checkbox (Insights) | Info-Button
3. Expandable Info-Text wenn Info-Button gedrueckt
4. Zwei Buttons: "Zurueck zur Session" (Abbrechen) | "Session jetzt beenden" (Bestaetigen)
5. Bei Bestaetigung: optional Insights extrahieren → Ergebnisse anzeigen → Session in DB beenden → Session-State resetten → `st.rerun()`

**Error-Handling:**
- Jeder Schritt in try/except mit Logger + `st.error()` / `st.info()` Feedback
- Fallback-Methode stellt sicher dass Session auch bei UI-Fehlern in DB beendet wird

<!-- Nächstes Modul: agent/multimodal_rag.py -->

---

## Modul 21: `agent/multimodal_rag.py`

### Überblick
Multimodales RAG-Chunking- und Indexing-System mit Unterstützung für Text, Tabellen, Abbildungen und Formeln. Bietet strukturierte Chunk-Erzeugung aus gemischtem Dokumenteninhalt sowie einen mehrdimensionalen Index mit Deduplizierung via SHA256-Hashing.

### Klassen & Funktionen

| # | Funktion/Klasse | Zeile | Beschreibung |
|---|----------------|-------|-------------|
| 1 | `ContentType` (enum) | ~15 | Enum für Inhaltstypen: TEXT, TABLE, FIGURE, FORMULA, CODE |
| 2 | `MultiModalChunk` (dataclass) | ~28 | Repräsentiert einen multimodalen Chunk mit chunk_id, content_type, primary_content, source_file, page_number, hash_sha256, sub_chunks, metadata, timestamp |
| 3 | `MultiModalChunk.to_vector_payload()` | ~60 | Serialisiert Chunk zu Dictionary für Vector-Store (embeddings-fähig) |
| 4 | `MultiModalChunk.from_dict()` | ~75 | Deserialisiert Chunk aus Dictionary (Classmethod) |
| 5 | `MultiModalChunker.__init__()` | ~95 | Initialisiert Chunker mit chunk_size, chunk_overlap, language |
| 6 | `MultiModalChunker.chunk_text()` | ~108 | Chunkt reinen Text mit Overlap-Support (sentence-splitting) |
| 7 | `MultiModalChunker._split_sentences()` | ~130 | Split Text in Sätze (sprachsensitiv: `.`, `!`, `?`, `。`, `？`) |
| 8 | `MultiModalChunker.chunk_mixed_content()` | ~155 | Chunkt gemischten Inhalt (Liste von Sektionen mit type, content, table, figure, formula) |
| 9 | `MultiModalChunker._chunk_table()` | ~195 | Chunkt Tabellen-Daten: erzeugt Textrepräsentation + Metadaten |
| 10 | `MultiModalChunker._chunk_figure()` | ~218 | Chunkt Abbildungen: extrahiert Caption + Description |
| 11 | `MultiModalChunker._chunk_formula()` | ~235 | Chunkt Formeln: LaTeX-Content + Beschreibung |
| 12 | `MultiModalChunker._generate_chunk_id()` | ~250 | Generiert eindeutige chunk_id via SHA256(source + page + type + content_hash) |
| 13 | `MultiModalChunker._compute_hash()` | ~260 | Berechnet SHA256-Hash von Content für Deduplizierung |
| 14 | `MultiModalRAGIndex.__init__()` | ~280 | Initialisiert leeren Index mit 5 internen Dictionaries |
| 15 | `MultiModalRAGIndex.add_chunk()` | ~298 | Fügt einen Chunk hinzu; gibt False bei Duplikat |
| 16 | `MultiModalRAGIndex.add_chunks()` | ~318 | Fügt mehrere Chunks hinzu; gibt Anzahl neuer Chunks zurück |
| 17 | `MultiModalRAGIndex.remove_by_source()` | ~328 | Entfernt alle Chunks einer Quelle; pflegt alle Indices konsistent |
| 18 | `MultiModalRAGIndex.get_chunk()` | ~365 | Gibt Chunk by ID zurück |
| 19 | `MultiModalRAGIndex.get_by_source()` | ~370 | Gibt alle Chunks einer Quelle zurück |
| 20 | `MultiModalRAGIndex.get_by_type()` | ~378 | Gibt alle Chunks eines Typs zurück |
| 21 | `MultiModalRAGIndex.get_by_page()` | ~386 | Gibt alle Chunks einer Seite zurück |
| 22 | `MultiModalRAGIndex.get_with_sub_chunks()` | ~394 | Gibt Chunk mit expandierten Sub-Chunks zurück |
| 23 | `MultiModalRAGIndex.expand_query()` | ~410 | Erweitert Query um cross-modale Kontext-Hinweise (Tabelle, Abbildung, Formel) |
| 24 | `MultiModalRAGIndex.stats()` | ~445 | Gibt Index-Statistiken zurück (total, by_type, sources, pages_indexed) |
| 25 | `MultiModalRAGIndex.clear()` | ~460 | Löscht den gesamten Index |
| 26 | `MultiModalRAG.__init__()` | ~472 | Kompatibilitäts-Wrapper für SOTA-Pipeline; initialisiert Chunker + Index |
| 27 | `MultiModalRAG.chunk_document()` | ~490 | Chunkt Dokument in pipeline-fähige Dictionaries |
| 28 | `create_chunker()` | ~649 | Factory-Funktion: erstellt MultiModalChunker mit Default-Settings |
| 29 | `create_index()` | ~654 | Factory-Funktion: erstellt leeren MultiModalRAGIndex |
| 30 | `MultimodalRAG` (alias) | ~643 | Kompatibilitäts-Alias für alte Import-Namen |

### Index-Architektur
Der `MultiModalRAGIndex` verwaltet 5 parallele Indizes:
- `_index`: Haupt-Dictionary (chunk_id -> MultiModalChunk)
- `_type_index`: content_type -> [chunk_ids]
- `_source_index`: source_file -> [chunk_ids]
- `_page_index`: page_number -> [chunk_ids]
- `_hash_index`: sha256_hash -> chunk_id (für Deduplizierung)

### Query-Expansion-Logik
Die `expand_query()`-Methode erkennt kontextuelle Begriffe und fügt automatisch Suchhinweise hinzu:
- **Tabellen-Begriffe**: "tabelle", "table", "daten", "data", "zahlen", "numbers", "werte", "values" → + "tabellarische Daten"
- **Abbildungs-Begriffe**: "abbildung", "figure", "diagramm", "diagram", "grafik", "chart", "bild", "image" → + "Bildbeschreibung"
- **Formel-Begriffe**: "formel", "formula", "gleichung", "equation", "berechnung", "calculation" → + "mathematische Formel"

### SOTA Features
- Multimodales Chunking: Text, Tabellen, Abbildungen, Formeln
- 5-dimensionales Indexing (ID, Typ, Quelle, Seite, Hash)
- Hash-basierte Deduplizierung (SHA256)
- Konsistente Index-Pflege bei Löschoperationen
- Query-Expansion für cross-modale Suche
- Pipeline-kompatible Serialisierung (to_vector_payload / from_dict)

<!-- Nächstes Modul: agent/strixkat_eval.py -->

## 21. `agent/multimodal_rag.py` – Multi-Modal Chunking & RAG Index (~706 Zeilen)

**Zweck:** Content-typen-aware Chunking und Indexierung für multi-modale Dokumente (Text, Tabellen, Figuren, Formeln).

### Data Models

| # | Klasse / Enum | Zeile | Zweck |
|---|--------------|-------|-------|
| 1 | `ContentType` (Enum) | ~36 | Text / Table / Figure / Formula – Typ-Klassifikation |
| 2 | `ChunkSubType` (Enum) | ~48 | Paragraph / Sentence / Header / Cell / Caption / Label |
| 3 | `MultiModalChunk` (dataclass) | ~57 | Chunk mit ID, Source, Content, Type, Page, SubChunks, Cross-References, Hash |
| 4 | `TableStructure` (dataclass) | ~105 | Tabelle mit ID, Spalten, Zeilen, Caption, Markdown-Export, LaTeX-Export |
| 5 | `FigureDescription` (dataclass) | ~128 | Figur mit ID, Caption, Description, Index-Content |
| 6 | `FormulaBlock` (dataclass) | ~141 | Formel mit ID, LaTeX, Description, Index-Content |

### `MultiModalChunk` – Wichtige Methoden

| # | Methode | Zeile | Zweck |
|---|---------|-------|-------|
| 7 | `MultiModalChunk.to_vector_payload()` | ~82 | Wandelt Chunk in dictionary für Vector-DB um |
| 8 | `MultiModalChunk.add_sub_chunk()` | ~95 | Fügt Sub-Chunk hinzu (z.B. Zellen einer Tabellenzeile) |

### `TableStructure` – Wichtige Methoden

| # | Methode | Zeile | Zweck |
|---|---------|-------|-------|
| 9 | `TableStructure.to_markdown()` | ~115 | Generiert Markdown-Tabelle |
| 10 | `TableStructure.to_latex()` | ~123 | Generiert LaTeX-Tabellen-Code |

### `MultiModalChunker` – Chunking-Engine

| # | Methode | Zeile | Zweck |
|---|---------|-------|-------|
| 11 | `MultiModalChunker.__init__()` | ~172 | Konfiguration: chunk_size, overlap, language |
| 12 | `MultiModalChunker.chunk_text()` | ~183 | Splitzt Text in Chunks mit Overlap; erkennt Headers (all-caps) und erstellt SubChunks |
| 13 | `MultiModalChunker._split_with_overlap()` | ~214 | Splitzt Text in Segmente mit konfigurierbarem Overlap |
| 14 | `MultiModalChunker._is_header()` | ~226 | Statisch: erkennt Header (all-caps, <=60 chars) |
| 15 | `MultiModalChunker._next_chunk_id()` | ~230 | Generiert UUID-basierte Chunk-ID |
| 16 | `MultiModalChunker.chunk_table()` | ~237 | Chunkt Tabelle: 1 Overview-Chunk + pro Zeile Row-Chunk + SubChunks für Zellen |
| 17 | `MultiModalChunker._row_to_sentence()` | ~317 | Wandelt Tabellenzeile in natürlichsprachigen Satz um |
| 18 | `MultiModalChunker.chunk_figure()` | ~329 | Erstellt Chunk für Figur/Diagramm |
| 19 | `MultiModalChunker.chunk_formula()` | ~350 | Erstellt Chunk für mathematische Formel |
| 20 | `MultiModalChunker.chunk_mixed_content()` | ~371 | Verarbeitet gemischten Content (Text+Tabellen+Figuren+Formeln) + Cross-References |
| 21 | `MultiModalChunker._link_cross_references()` | ~419 | Verknüpft Chunks derselben Seite gegenseitig |

### `MultiModalRAGIndex` – Index & Retrieval

| # | Methode | Zeile | Zweck |
|---|---------|-------|-------|
| 22 | `MultiModalRAGIndex.__init__()` | ~448 | Initialisiert Haupt-Index + Type/Source/Page/Hash-Sub-Indizes |
| 23 | `MultiModalRAGIndex.add_chunk()` | ~459 | Fügt Chunk hinzu; False bei Duplikat |
| 24 | `MultiModalRAGIndex.add_chunks()` | ~484 | Fügt mehrere Chunks hinzu; gibt Anzahl neuer Chunks |
| 25 | `MultiModalRAGIndex.remove_by_source()` | ~492 | Entfernt alle Chunks einer Quelle |
| 26 | `MultiModalRAGIndex.get_chunk()` | ~522 | Chunk nach ID abrufen |
| 27 | `MultiModalRAGIndex.get_by_source()` | ~526 | Alle Chunks einer Quelle |
| 28 | `MultiModalRAGIndex.get_by_type()` | ~531 | Alle Chunks eines Content-Typs |
| 29 | `MultiModalRAGIndex.get_by_page()` | ~536 | Alle Chunks einer Seite |
| 30 | `MultiModalRAGIndex.get_with_sub_chunks()` | ~541 | Chunk mit expandierten SubChunks |
| 31 | `MultiModalRAGIndex.expand_query()` | ~555 | Cross-modal Query-Expansion (Tabellen-/Figur-/Formel-Begriffe erkennen) |
| 32 | `MultiModalRAGIndex.stats()` | ~588 | Index-Statistiken (Total, by_type, Sources, Pages) |
| 33 | `MultiModalRAGIndex.clear()` | ~604 | Leert den gesamten Index |

### `MultiModalRAG` – Pipeline-Kompatibilität

| # | Methode | Zeile | Zweck |
|---|---------|-------|-------|
| 34 | `MultiModalRAG.__init__()` | ~616 | Erbt von MultiModalRAGIndex + hält MultiModalChunker |
| 35 | `MultiModalRAG.chunk_document()` | ~629 | Chunkt Dokument und gibt pipeline-freundliche Dicts zurück |

### Factory-Funktionen

| # | Funktion | Zeile | Zweck |
|---|----------|-------|-------|
| 36 | `create_chunker()` | ~649 | Erstellt MultiModalChunker mit Default-Settings |
| 37 | `create_index()` | ~654 | Erstellt leeren MultiModalRAGIndex |

### SOTA Features
- Content-typen-aware Chunking (Text, Table, Figure, Formula)
- SubChunk-Hierarchie (Tabelle -> Zeilen -> Zellen)
- Cross-Reference-Verknüpfung zwischen Chunks derselben Seite
- Multi-Index (Type, Source, Page, Hash) für effizientes Retrieval
- Cross-modal Query-Expansion
- Hash-basierte Deduplizierung
- Kompatibilitäts-Alias `MultimodalRAG` für bestehende Imports

<!-- Nächstes Modul: agent/strixkat_eval.py -->

## 19. `agent/multimodal_rag.py` – Multimodal RAG

**Datei:** `agent/multimodal_rag.py` (706 Zeilen)
**Zweck:** Multimodaler RAG-Store mit semantischem Chunking, Content-Fusion und Hybrid-Suche über Text-, Bild- und Code-Inhalte.

### Datenstrukturen

| # | Struktur | Zeile | Art | Zweck |
|---|----------|-------|-----|-------|
| 1 | `Chunk` | ~30 | dataclass | Repräsentiert einen Chunk mit ID, Content, Metadaten, Embedding |
| 2 | `ContentCategory` | ~45 | Enum | Kategorien: TEXT, IMAGE, CODE, TABLE, MIXED |
| 3 | `ContentFusionResult` | ~55 | dataclass | Ergebnis der Content-Fusion mit fused_text, sources, confidence |

### Funktionen von `SemanticChunker`

| # | Funktion | Zeile | Art | Parameter | Rückgabe | Zweck |
|---|----------|-------|-----|-----------|----------|-------|
| 1 | `__init__()` | ~70 | Constructor | `chunk_size`, `chunk_overlap`, `min_sentence_length` | - | Initialisiert Chunker mit Token-Limits |
| 2 | `chunk_text()` | ~90 | Method | `text: str`, `source: str` | `List[Chunk]` | Zerlegt Text in semantische Chunks |
| 3 | `chunk_mixed_content()` | ~130 | Method | `sections: List`, `source: str` | `List[Chunk]` | Chunking mixed Content (Text+Bild+Code) |
| 4 | `stats` | ~180 | Property | - | `Dict` | Statistik über Chunk-Größen |

### Funktionen von `ContentFusionEngine`

| # | Funktion | Zeile | Art | Parameter | Rückgabe | Zweck |
|---|----------|-------|-----|-----------|----------|-------|
| 1 | `__init__()` | ~200 | Constructor | `similarity_threshold: float` | - | Initialisiert Fusion-Engine |
| 2 | `fuse()` | ~215 | Method | `chunks: List[Chunk]` | `ContentFusionResult` | Fusioniert mehrere Chunks zu unified Text |
| 3 | `_compute_weights()` | ~260 | Private | `chunks` | `List[float]` | Berechnet Gewichte basierend auf Relevanz |
| 4 | `_merge_similar()` | ~290 | Private | `chunks`, `threshold` | `List[Chunk]` | Merge ähnliche Chunks |

### Funktionen von `MultiModalRAG`

| # | Funktion | Zeile | Art | Parameter | Rückgabe | Zweck |
|---|----------|-------|-----|-----------|----------|-------|
| 1 | `__init__()` | ~320 | Constructor | `index_path`, `embedding_dim`, `max_chunks` | - | Initialisiert Multimodal-RAG-Index |
| 2 | `search()` | ~350 | Method | `query: str`, `top_k: int`, `category_filter` | `List[Chunk]` | Sucht im Index nach relevanten Chunks |
| 3 | `retrieve()` | ~390 | Method | `query: str`, `top_k: int` | `ContentFusionResult` | Retrieve + Fusion in einem Schritt |
| 4 | `add_chunks()` | ~430 | Method | `chunks: List[Chunk]` | `int` | Fügt Chunks hinzu, gibt Anzahl zurück |
| 5 | `add_document()` | ~460 | Method | `file_path: str`, `metadata: Dict` | `int` | Dokument verarbeiten + indizieren |
| 6 | `stats` | ~510 | Property | - | `Dict` | Index-Statistiken |
| 7 | `clear()` | ~525 | Method | - | - | Leert den Index |

### Modulebene

| # | Funktion | Zeile | Zweck |
|---|----------|-------|-------|
| 1 | `compute_similarity()` | ~560 | Berechnet Kosinus-Ähnlichkeit zwischen zwei Vektoren |
| 2 | `truncate_text()` | ~580 | Trunkiert Text auf maximale Token-Anzahl |

### SOTA Features
- Semantisches Chunking (nicht nur feste Größen)
- Multimodale Unterstützung (Text, Bilder, Code, Tabellen)
- Content-Fusion mit gewichteter Merge-Strategie
- Persistenter Index (überlebt Restarts)

<!-- Nächstes Modul: agent/docling_parallel.py -->

---

## Modul 20: `agent/docling_parallel.py`

### Überblick
SOTA-Parallel-Dokumentenprozessor mit thread-pool basierter Verarbeitung, memory-aware Batch-Sizing, Progress-Callbacks, Cancellation-Support und Fallback auf Basis-Prozessor.

### Klassen & Funktionen

| # | Funktion/Klasse | Zeile | Beschreibung |
|---|----------------|-------|-------------|
| 1 | `DocumentType` (enum) | ~15 | Enum für Dokumenttypen: PDF, DOCX, TXT, MD, UNKNOWN |
| 2 | `ProcessingStatus` (enum) | ~25 | Enum für Status: PENDING, IN_PROGRESS, COMPLETED, FAILED, CANCELLED |
| 3 | `DocumentChunk` (dataclass) | ~35 | Repräsentiert einen Dokumenten-Abschnitt mit chunk_id, content, chunk_type, page_number, metadata |
| 4 | `DocumentChunk.to_dict()` | ~75 | Serialisiert DocumentChunk zu Dictionary |
| 5 | `ProcessingResult` (dataclass) | ~86 | Ergebnis der Verarbeitung eines Dokuments inkl. Status, Chunks, Fehler, Zeit, Hash |
| 6 | `ProcessingResult.success` (property) | ~98 | Gibt True zurück wenn Status COMPLETED ist |
| 7 | `ProcessingResult.chunk_count` (property) | ~102 | Gibt Anzahl der Chunks zurück |
| 8 | `ProcessingResult.to_dict()` | ~105 | Serialisiert ProcessingResult zu Dictionary |
| 9 | `SystemConfig` (class) | ~121 | Erkennt System-Kapazitäten und konfiguriert entsprechend |
| 10 | `SystemConfig._detect_max_workers()` | ~130 | Erkennt CPU-Anzahl für Max-Worker |
| 11 | `SystemConfig._detect_max_memory()` | ~136 | Erkennt verfügbaren RAM in MB (via psutil oder Default 50GB) |
| 12 | `SystemConfig._calculate_batch_size()` | ~145 | Berechnet optimale Batch-Größe basierend auf verfügbarer Memory (~200MB/PDF) |
| 13 | `SystemConfig._detect_gpu()` | ~152 | Prüft GPU-Verfügbarkeit via torch.cuda |
| 14 | `SystemConfig.to_dict()` | ~160 | Serialisiert SystemConfig zu Dictionary |
| 15 | `DoclingParallelProcessor.__init__()` | ~184 | Initialisiert Prozessor mit max_workers, batch_size, Locks, Callbacks, Stats |
| 16 | `DoclingParallelProcessor.on_progress()` | ~201 | Registriert einen Progress-Callback: callback(file_path, status, total_processed) |
| 17 | `DoclingParallelProcessor._fire_progress()` | ~205 | Feuert alle registrierten Progress-Callbacks |
| 18 | `DoclingParallelProcessor.cancel_file()` | ~218 | Storniert Verarbeitung für eine spezifische Datei |
| 19 | `DoclingParallelProcessor.cancel_all()` | ~223 | Storniert alle ausstehenden Verarbeitungen |
| 20 | `DoclingParallelProcessor._get_executor()` | ~232 | Gibt ThreadPoolExecutor zurück (lazy initialization) |
| 21 | `DoclingParallelProcessor.shutdown()` | ~237 | Schaltet den Executor sicher |
| 22 | `DoclingParallelProcessor._process_single_document()` | ~247 | Verarbeitet ein einzelnes Dokument im Thread-Pool (Typ-Erkennung, Hash, Chunks) |
| 23 | `DoclingParallelProcessor.process_single()` | ~318 | Kompatibilitäts-Wrapper für SOTA-Pipeline (gibt Dict mit content, metadata, chunks) |
| 24 | `DoclingParallelProcessor._process_pdf()` | ~339 | Routet PDF-Verarbeitung zu Docling oder Fallback |
| 25 | `DoclingParallelProcessor._process_pdf_docling()` | ~346 | Verarbeitet PDF mit Docling (SOTA): extrahiert Text, Tabellen, Figuren pro Seite |
| 26 | `DoclingParallelProcessor._process_pdf_fallback()` | ~402 | Fallback: AdvancedPDFProcessor -> pdfplumber -> Error |
| 27 | `DoclingParallelProcessor._process_docx()` | ~451 | Verarbeitet DOCX via python-docx: Paragraphen (mit Style-Erkennung) + Tabellen |
| 28 | `DoclingParallelProcessor._process_text()` | ~493 | Verarbeitet Text/Markdown-Dateien (split by \n\n) |
| 29 | `DoclingParallelProcessor._get_document_type()` | ~517 | Statische Methode: erkennt Dokumenttyp aus Dateierweiterung |
| 30 | `DoclingParallelProcessor._compute_hash()` | ~529 | Statische Methode: berechnet SHA256-Hash einer Datei |
| 31 | `DoclingParallelProcessor.process_batch()` | ~541 | Verarbeitet mehrere Dokumente parallel via ThreadPoolExecutor + as_completed |
| 32 | `DoclingParallelProcessor.process_batch_async()` | ~585 | Async-Wrapper für Batch-Verarbeitung (run_in_executor) |
| 33 | `DoclingParallelProcessor.scan_directory()` | ~598 | Scannt Verzeichnis nach verarbeitbaren Dateien (rekursiv oder flach) |
| 34 | `DoclingParallelProcessor.get_status()` | ~628 | Gibt aktuellen Verarbeitungsstatus als Dictionary zurück |
| 35 | `DoclingParallelProcessor.get_results()` | ~643 | Gibt alle ProcessingResults zurück |
| 36 | `DoclingParallelProcessor.get_successful_results()` | ~648 | Gibt nur erfolgreiche Results zurück |
| 37 | `DoclingParallelProcessor.reset_stats()` | ~653 | Setzt Statistiken zurück |
| 38 | `DoclingParallelProcessor.__del__()` | ~661 | Cleanup: shutdown() aufrufen |
| 39 | `create_processor()` | ~670 | Factory-Funktion: erstellt DoclingParallelProcessor mit auto-erkannter Konfiguration |
| 40 | `DoclingParallel` (alias) | ~702 | Kompatibilitäts-Alias für orchestrator.py Import |

### SOTA Features
- Thread-pool parallele Verarbeitung mit memory-aware Batch-Sizing
- 3-stufiger PDF-Fallback: Docling -> AdvancedPDFProcessor -> pdfplumber

---

## Modul 22: `wellbeing_session/workflow/langgraph_real.py` – LangGraph Session Pipeline

### Überblick
SOTA LangGraph-basierte StateGraph-Pipeline für psychologische Sessions mit 7 Nodes, conditional crisis-routing, dependency injection und persistenter Checkpointer-Unterstützung (MemorySaver/SqliteSaver).

### Klassen & Funktionen

| # | Funktion/Klasse | Zeile | Beschreibung |
|---|----------------|-------|-------------|
| 1 | `PsychSessionState` (TypedDict) | ~1 | State-Schema: user_input, session_id, is_valid, errors, dominant_emotion, emotional_markers, is_crisis, comprehensive_context, formatted_context, ai_response, enhanced_response, node_trace, node_timings |
| 2 | `_registry` (ThreadSafeRegistry) | ~12 | Thread-lokale Dependency-Injection (emotional_analyzer, langchain_model, chat_logic, session_manager, context_builder) |
| 3 | `_get_dep()` | ~178 | Resolve Dependency aus Registry via thread_id |
| 4 | `validate_input()` | ~184 | Node: Validiert user_input und session_id |
| 5 | `analyze_emotion()` | ~211 | Node: Emotion-Analyse via emotional_analyzer mit Fallback |
| 6 | `crisis_router()` | ~253 | Conditional Edge: crisis_response vs build_context |
| 7 | `crisis_response()` | ~260 | Node: Generiert Krisen-Text mit Helpline-Info (i18n-fähig) |
| 8 | `build_context()` | ~281 | Node: Baut psychologischen Kontext via context_builder |
| 9 | `generate_response()` | ~323 | Node: 3-Strategie-Fallback (LangChain → chat_logic → pre_generated) |
| 10 | `enhance_response()` | ~376 | Node: Emotionale Anreicherung mit Emoji-Prefix + Kontext-Notiz |
| 11 | `record_messages()` | ~404 | Node: Persistiert User/Assistant Messages in Session-DB |
| 12 | `build_langgraph_session_graph()` | ~447 | Graph-Builder: kompiliert StateGraph mit Checkpointer |

### SOTA Features
- LangGraph StateGraph mit typed state (Pydantic/TypedDict)
- Conditional routing (crisis detection → sofortige Krisen-Intervention)
- 3-stufige LLM-Strategie mit Graceful Degradation
- Dependency Injection via ThreadSafeRegistry (keine Globals)
- Persistent Checkpointer (MemorySaver default, SqliteSaver optional)
- Node-timing tracing für Performance-Monitoring
- i18n-Unterstützung für Crisis-Texte (DE/EN/BG)

## N. `scripts/dependency_vulnerability_scanner.py` — Dependency Vulnerability Scanner

> **Stand:** 2026-07-31 | **Doku:** [docs/16_DEPENDENCY_SCANNER.md](docs/16_DEPENDENCY_SCANNER.md)

| Aspekt | Detail |
|--------|--------|
| **Zweck** | Privacy-preserving Security-Scan für Python-Dependencies (lokal, keine Cloud-Calls) |
| **Engine** | pip-audit (Primary, subprocess-sandboxed) + Heuristik-Fallback |
| **Input** | `requirements.txt` (oder custom via `-r`) |
| **Output** | Console-Report + JSON (`-o report.json`) |
| **Security** | `--strict` Exit-1 bei ANY Vulnerability (CI/CD-fähig) |
| **Cache** | `data/vuln_cache/`, 24h TTL, `--update-cache` zum manuellen Aktualisieren |
| **Tests** | 43 Tests (`tests/test_dependency_vulnerability_scanner.py`) |
| **RAG** | **Nicht** in RAG aufnehmen (Tool, kein Wissensdokument, Ergebnisse zeitabhängig) |

### Kern-Komponenten

| # | Funktion/Klasse | Zeilen | Beschreibung |
|---|----------------|--------|-------------|
| 1 | `parse_requirements()` | ~40 | Parst `requirements.txt` in `List[Tuple[str, str]]` (Name+Version) |
| 2 | `Vulnerability` (dataclass) | ~50 | CVE-ID, Severity (CRITICAL/HIGH/MEDIUM/LOW), Package, Affected Versions, Description |
| 3 | `ScanResult` (dataclass) | ~60 | Gesamtergebnis: vulnerabilities, summary, scan_time, packages_scanned |
| 4 | `VulnerabilityScanner.scan()` | ~100 | Haupt-Scan: pip-audit-Call oder Heuristik-Fallback |
| 5 | `VulnerabilityScanner._scan_pip_audit()` | ~120 | Subprocess-Call mit `--skip-db-update` (Offline-Modus), `--format=json` |
| 6 | `VulnerabilityScanner._scan_heuristic()` | ~150 | Fallback: 7 bekannte kritische Patterns (z.B. `urllib3<1.26.5`, `cryptography<3.3.2`) |
| 7 | `VulnerabilityCache` | ~80 | Cache-Management: `data/vuln_cache/`, 24h TTL, JSON-Serialisierung |
| 8 | `ReportFormatter.format_console()` | ~60 | Human-readable Console-Output mit Severity-Farben |
| 9 | `ReportFormatter.format_json()` | ~40 | Maschinenlesbarer JSON-Output für CI/CD |
| 10 | `main()` | ~50 | CLI-Entry-Point mit Argument-Parsing |

### SOTA Features
- pip-audit als Primary-Engine (pypa/advisory-database, Python-Standard)
- Subprocess-Sandboxing (timeout, capture_output, text mode)
- Heuristik-Fallback wenn pip-audit nicht installiert
- Lokaler Cache mit TTL (kein Network-Call bei wiederholten Scans)
- `--strict` Mode für CI/CD-Pipelines (Exit-1 bei ANY Vulnerability)
- Zero Telemetry, Zero Cloud-Calls, Zero PII-Leak
- SOTA-Recherche via DuckDuckGo MCP-Server (`@fetch-mcp`)

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

- **RobotsChecker:** Pro-Domain-Cache (~1 h TTL; Negativ-Cache 60 s nach Fetch-Fehler → kein Hammerschlag). Fetch via injizierbarem `fetcher` (Default: `urllib.request`, Timeout 5 s, max. 1 MB, UA `bot6-local-rag/1.0`). Parse via `urllib.robotparser` — aber **eigene Regelwahl** (s. u.). Thread-sicher (`threading.Lock`).
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
| **Persistenz** | `~/.cache/bot6/token_scaling_overrides.json` (flach: `{modell: {feld: wert}}`, atomar via tmp + `os.replace`); leere Overrides entfernen den Eintrag; `__all__` löscht die Datei; korrupt/fehlend = Auto (Warning) |
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

