<!-- last-verified: 2026-08-20 -->
# 02 – SOTA Roadmap & Implementierungsstatus

**Letzte Aktualisierung**: 2026-08-20 (Doku-Audit)
**Status**: Phase 1 vollstaendig umgesetzt; Phase 2 teilweise offen (Verdrahtung, keine Implementierungsluecken); Neue SOTA-Komponenten seit 07-15 dokumentiert
**Hardware-Profil**: Windows 11, 64GB RAM, RTX 4090, Gemma4 12B

---

## 1. Überblick

Dieses Dokument konsolidiert die SOTA-Landschaft, den aktuellen Implementierungsstand und die weiteren Schritte. Es ersetzt die früheren Dokumente:
- `SOTA_ROADMAP.md`
- `SOTA_RAG_QUALITY_PIPELINE.md`
- `SOTA_IMPLEMENTATION_TRACKER.md`
- `SOTA_IMPLEMENTATION_PROGRESS.md` (root)

### User-Prompt (Original)
> bitte setze die offenen Schritte aus SOTA_IMPLEMENTATION_PROGRESS.md um.
> **Beachte:** Windows PC, 64GB RAM, RTX4090, Gemma4 12B, venv: `venv_mistral_gguf/Scripts/Activate.ps1`
> Behebe Ursachen von Fehlern (SOTA root cause), wende CoT + ToT an, bewerte Varianten 1-7 Sterne.

---

## 2. Current SOTA Landscape (2025/2026)

### RAG Patterns

| Pattern | Source | Status | Priorität |
|---------|--------|--------|-----------|
| **Multi-Query RAG** | Stanford SEBA | ✅ Implementiert | – |
| **Hybrid Scoring** (semantic + BM25 + KG) | Custom | ✅ Implementiert | – |
| **CRAG (Corrective RAG)** | UW 2024 | ✅ Implementiert (Orchestrator Self-Correction Loop) | – |
| **Self-RAG** | Meta AI 2023 | ✅ QueryStrategyManager + adaptive routing | – |
| **Adversarial Filter** | CRaF 2024 | ✅ EvidenceManager (quality gates) | – |
| **RAP** | Google DeepMind 2024 | ❌ Kein direkter Implementierungsbeleg | 🟢 LOW |
| **FLARE** | MIT 2023 | ✅ CRAG Self-Correction Loop | – |
| **Adaptive RAG** | Self-RAG Meta AI 2023 | ✅ `agent/adaptive_rag.py` + getestet | – |
| **Multi-Hop Retrieval** | IRCOT UW 2023 | ✅ `agent/adaptive_rag.py` | – |
| **Contradiction Detection** | Fact-Checking SOTA | ✅ `agent/contradiction_detector.py` | – |
| **Cross-Encoder Reranking** | Cross-Encoder SOTA 2024 | ✅ `agent/cross_encoder_reranker.py` (GPU) | – |
| **Community Detection** | Leiden Traag 2019 | ✅ `agent/community_detector.py` (656 Zeilen) | – |

### Reasoning Patterns

| Pattern | Source | Status | Priorität |
|---------|--------|--------|-----------|
| **Hybrid Reasoning** (Toulmin + Reflection + Critic + Debate) | Custom | ✅ Implementiert | – |
| **Tree of Thoughts (ToT)** | Google 2023 | ⚠️ `meta_orchestrate()` in `agent/hybrid_reasoning.py` ist ein Strategie-Selector (semantic/hybrid/keyword), **kein** Baum-Exploration mit Backtracking | – |
| **Graph of Thoughts (GoT)** | MIT 2023 / LangGraph 2024 | ⚠️ `wellbeing_session/workflow/langgraph_real.py` nutzt LangGraph StateGraph (konditionale Kanten, Checkpointing, Human-in-the-Loop) — ist ein **linearer DAG**, **kein** GoT (keine Zyklen, kein Thought-Merging, keine Distillation) | – |
| **RAP** | Google DeepMind 2024 | ❌ Nicht separat implementiert | 🟢 LOW |

**SOTA-Korrektur (2026-08-01):** Unabhängige Internet-Recherche widerlegt Teile der früheren Ablehnungs-Argumente:
- **ToT**: Cross-ToT-Paper (arXiv:2311.08097) testete mit Llama-2-13B + Bloomz-7B — ToT **funktioniert** mit kleinen Modellen, liefert aber **keinen klaren Vorteil** vs CoT. Behauptung "540B nötig" war falsch.
- **GoT**: Offizielle Implementierung (`spcl/graph-of-thoughts`) unterstützt jegliches LLM als Engine. Kein Benchmark <13B existiert, aber Framework schließt lokale Modelle nicht aus.
- **RAP**: DeepMind-spezifisches Benchmark-Framework — Ablehnung bleibt korrekt.

### Optimization & Verification

| Pattern | Source | Status | Priorität |
|---------|--------|--------|-----------|
| **DSPy** | Stanford 2023 | ⚠️ Keine produktive Nutzung belegt; **SOTA-Korrektur**: DSPy unterstützt lokale Modelle via Model Routers (LiteLLM), MIPROv2-Optimizer (2024) funktioniert mit Open-Weight-Modellen. Behauptung "braucht 70B+" war falsch. | 🟡 MEDIUM |
| **LangGraph** | LangChain 2024 | ✅ `wellbeing_session/workflow/langgraph_real.py` — StateGraph mit TypedDict-State, konditionalen Kanten (Crisis-Routing), SQLite/Memory-Checkpointing, Streaming (astream_events), Human-in-the-Loop (interrupt_before), Tenacity-Retry | – |
| **Multi-Verifier Ensemble** | Stanford 2024 | ✅ VerificationManager v2.0 (4-fach Ensemble) | – |
| **Factuality Grader** | RAGAS 2024 | ✅ VerificationManager (FACTUAL_CONSISTENCY) | – |
| **VerificationManager** v2.0 | Custom | ✅ Voll integriert | – |

---

## 3. Bereits Implementierte SOTA-Komponenten

| Komponente | Datei | Integriert in Orchestrator? |
|---|---|---|
| **UnifiedRAGStore** | `agent/unified_rag_store.py` | ✅ Indirekt aktiv (über `agent/tools.py` → `ToolManager.rag_search`) |
| **MultimodalRAG** | `agent/multimodal_rag.py` | ✅ Initialisiert; Query-Expansion im Retrieval-Pfad verdrahtet |
| **DoclingParallel** | `agent/docling_parallel.py` | ⚠️ Initialisiert; im Chat-Antwortpfad noch nicht aktiv genutzt |
| **StrixKATEval** | `agent/strixkat_eval.py` | ✅ Initialisiert; Post-Answer-Evaluation im Hauptpfad verdrahtet |
| **ChangeDetector** | `agent/change_detector.py` | ✅ Initialisiert; Novelty-Telemetrie im Hauptpfad verdrahtet |
| **SOTAPipeline** (Facade) | `agent/sota_pipeline.py` | ⚠️ Initialisiert; Lifecycle/Continuous Mode noch nicht default-gesteuert |
| **QueryPlanner** | `finance/query_planner.py` | ✅ In Finance |
| **GrammarCompiler** | `finance/grammar_compiler.py` | ✅ In Finance |
| **QueryReflector** | `finance/query_reflector.py` | ✅ In Finance |
| **CommunityDetector** | `agent/community_detector.py` | ✅ Standalone; produktive Integration offen |
| **AdaptiveRAG** | `agent/adaptive_rag.py` | ✅ Voll integriert (über Orchestrator `_adaptive_rag` Property, `decide_retrieval_strategy()`) |
| **ContradictionDetector** | `agent/contradiction_detector.py` | ✅ Implementiert |
| **CrossEncoderReranker** | `agent/cross_encoder_reranker.py` | ✅ GPU-optimiert, lazy-loaded |
| **AutoTuner** | `agent/auto_tuner.py` | ✅ Implementiert |
| **DecompositionEngine** | `agent/decomposition_engine.py` | ✅ Implementiert |
| **EvidenceManager** | `agent/evidence_manager.py` | ✅ Implementiert |
| **QueryStrategyManager** | `agent/query_strategy_manager.py` | ✅ Implementiert |
| **FeedbackOptimizer** | `agent/feedback_optimizer.py` | ✅ Implementiert |
| **AdaptivePlanner** | `agent/adaptive_planner.py` | ✅ Implementiert |
| **OptimizedResearchEngine** | `agent/optimized_research_engine.py` | ✅ Implementiert |
| **HybridSearch** | `agent/hybrid_search.py` | ✅ Implementiert |
| **IntelligentRouter** | `agent/intelligent_routing.py` | ✅ Implementiert |
| **PathSandbox** | `agent/path_sandbox.py` | ✅ Implementiert |
| **ExtractionMetrics** | `agent/extraction_metrics.py` | ✅ Implementiert |
| **ExtractionCache** | `agent/extraction_cache.py` | ✅ Implementiert |
| **GrammarConstrainedLLM** | `agent/grammars.py` | ✅ Implementiert |

**Kritische Lücke**: Nicht mehr "fehlende SOTA-Komponenten", sondern **Runtime-Interface-Drift und unvollständige Lifecycle-Nutzung**. Der produktive Orchestrator initialisiert die Pipeline-Komponenten bereits, aber historische API-Annahmen und verspätete Hook-Platzierung haben Teile der Integration faktisch entkoppelt. Die Root-Cause-Korrektur liegt daher in korrekter Verdrahtung, nicht im bloßen Hinzufügen weiterer Module.

**SOTA-Update (2026-08-01):** Seit dem letzten Doku-Update (2026-07-14) wurden **17 neue SOTA-Komponenten** implementiert (siehe Tabelle oben). Die Implementierungs-Lücke ist geschlossen; die offenen Punkte sind Verdrahtungsfragen im Orchestrator.

---

## 4. Priority Implementation Roadmap

### Phase 1: HIGH PRIORITY (bereits abgeschlossen)

- [x] UnifiedRAGStore mit Hybrid-Scoring
- [x] MultimodalRAG (Text + Bild + Diagramm)
- [x] DoclingParallel (6x Beschleunigung)
- [x] StrixKATEval (Qualitätsmetriken)
- [x] ChangeDetector (Kontext-Adaption)
- [x] QueryPlanner / GrammarCompiler / QueryReflector (Finance)

### Phase 2: Deep Orchestrator Integration (TEILWEISE OFFEN, PRIORITÄT: HOCH)

- [x] **2.1** `_rag_enhanced()` – UnifiedRAGStore im produktiven Pfad vorhanden (indirekt über ToolManager)
- [x] **2.2** `_live_search()` – Adaptive Depth (QueryPlanner + Reflector) in Web-Suche ✅ VERIFIZIERT 2026-08-01
  - `WebSearchPlanner.plan()` + `WebSearchReflector.reflect()` werden in `_live_search()` aufgerufen (Zeilen ~1400-1440)
  - Initialisiert im `__init__` mit `WEB_SEARCH_PLANNER_AVAILABLE` Guard
  - **Korrektur**: Externes LLM hatte fälschlich "OFFEN" bewertet — ist voll integriert
- [ ] **2.3** `_verify_and_fix_tools()` – Secondary-Model-Grounding
  - `verify_step()` verwendet VerificationManager v2.0 (4-fach Ensemble), aber **kein Secondary-Model**
  - Verwendet `self.model_loader` (SAME model) für Verification — kein separates Verifikationsmodell
- [x] **2.4** `_generate_answer_from_sources()` – Grammar-constrained Output ✅ VERIFIZIERT 2026-08-01
  - `summarize()` (Zeile ~1121) ruft `self.model_loader.generate_response()` auf, **ohne** GrammarCompiler
  - **ABER**: GrammarCompiler (`agent/grammars.py`) ist fuer **JSON-structured outputs** designed (via `GrammarConstrainedLLM`)
  - `generate_response()` in `scripts/model_loader.py` akzeptiert `grammar` param — wird bei JSON-Requests verwendet
  - Free-text-Antworten brauchen **kein** Grammar-Constraining — das waere kontraproduktiv (erschwert fluessigen Text)
  - **Entscheidung**: Kein Offener Punkt — Design ist korrekt

**Stand 2026-08-01 (VERIFIZIERT, TIEFENANALYSE):**
- [x] Phase 2.2 war bereits DONE — externes LLM hatte falsch bewertet
- [x] AdaptiveRAG ist bereits voll integriert — externes LLM hatte falsch bewertet
- [x] Phase 2.4 GrammarCompiler ist INTENTIONAL nicht fuer free-text — kein offener Punkt
- [ ] Phase 2.3 Secondary-Model-Grounding bleibt offen — **Entscheidung: Experimental, nicht implementieren**
  - Secondary Model erfordert zweites GGUF-Modell im RAM (~6GB extra VRAM)
  - VerificationManager v2.0 (4-fach Ensemble) liefert bereits robuste Verification
  - Kosten-Nutzen-Verhaeltnis unguenstig fuer Local-First-Architektur
- [x] SOTA-Komponenten im Orchestrator korrekt benannt und erreichbar gemacht
- [x] `MultimodalRAG` von einer Phantom-`enrich_query()`-Annahme auf echte Query-Expansion im Retrieval-Pfad umgestellt
- [x] `StrixKATEval` semantisch korrekt nach der Antwortgenerierung verdrahtet
- [x] Doppelte, verspätete `_run_sota_enhancement()`-Ausführung entfernt

**Hinweis:** Die Phase-2-Restarbeiten betreffen primär die Verdrahtung der SOTA-Pipeline-Komponenten im Hauptpfad, nicht die Grundintegration von UnifiedRAGStore/CRAG.

### Phase 3: UI + Lifecycle (PRIORITÄT: MITTEL)

- [ ] **3.1** GUI Config-Panel für SOTA-Pipeline-Toggles
- [ ] **3.2** Lifecycle-Integration (Health-Metriken)
- [ ] **3.3** Startup-Sequence (AsyncStartupService initialisiert SOTA-Komponenten)

### Phase 4: Validation + Cleanup (PRIORITÄT: NIEDRIG)

- [ ] **4.1** RAGAS-Suite erweitern
- [ ] **4.2** Dead-Code entfernen
- [ ] **4.3** Dokumentationen finalisieren

### Phase 5: Next-Gen Patterns (8+ Wochen)

| Pattern | Impact | Effort | ETA |
|---------|--------|--------|-----|
| CRAG | ✅ Bereits vorhanden | – | – |
| Self-RAG-Evaluation | ✅ AdaptiveRAG implementiert | – | – |
| DSPy-Evaluation | 🟢 Low | 🔴 High | Nur bei messbarem Vorteil |
| Adversarial Filter | ✅ EvidenceManager implementiert | – | – |
| Multi-Verifier Ensemble | ✅ VerificationManager v2.0 | – | – |
| Tree of Thoughts | ⚠️ `meta_orchestrate()` ist ein Strategie-Selector, kein ToT | – | – |
| Graph of Thoughts | ⚠️ LangGraph StateGraph ist ein linearer DAG, kein GoT | – | – |
| Community Detection | ✅ CommunityDetector implementiert | – | – |
| Contradiction Detection | ✅ ContradictionDetector implementiert | – | – |
| Cross-Encoder Reranking | ✅ CrossEncoderReranker implementiert | – | – |
| Weitere LangGraph Workflows | 🟡 Medium | 🔴 High | Nur bei passendem State-Problem |

---

## 5. Hardware-Specific Optimizations (RTX 4090 + Gemma4 12B)

| Optimization | Current | Target | Benefit |
|-------------|---------|--------|---------|
| Verifiziertes llama.cpp-Profil | `n_batch=3072`, `n_ubatch=2048`, 12 Threads | Beibehalten | Stabiler RTX-4090-Betrieb |
| Batch-/Ubatch-Tuning | Verifizierte Grenzen vorhanden | Nur benchmarkbasiert | Keine optimistische Erhoehung |
| Quantisierung | GGUF-Modellabhaengig | Pro Modell benchmarken | Qualitaet/VRAM abwaegen |
| Speculative Decoding | Nicht produktiv belegt | **SOTA-Korrektur (2026-08-01)**: llama.cpp voll unterstützt (`--model-draft`), Benchmarks zeigen 20-50% Speedup auf Consumer-GPUs. Draft-Modell (0.6B Q4 ≈ 600MB VRAM) passt auf RTX 4090. **Empfehlung: Produktiv testen.** | 20-50% Latenz-Reduktion |

vLLM, TensorRT und PagedAttention sind keine pauschalen Ziele fuer den produktiven GGUF-/`llama-cpp-python`-Stack. Ein Runtime-Wechsel waere eine separate Architekturentscheidung mit Qualitaets-, Template-, VRAM- und Betriebsbenchmarks.

---

## 6. Verifikation externer Analyse (2026-08-01) — Detailierte Korrektur

Am 2026-08-01 wurde eine externe LLM-Analyse der SOTA-Roadmap unabhängig verifiziert. Die externe Analyse identifizierte folgende offene Punkte:

### Externe Analyse — Behauptungen vs. Fakten

| Externe Behauptung | Verifikationsergebnis | Detail |
|-------------------|----------------------|--------|
| **Phase 2.2** `_live_search()` OFFEN | **FALSCH** — ist DONE | `WebSearchPlanner` + `WebSearchReflector` werden in `_live_search()` (Zeilen ~1400-1440) aufgerufen. Initialisiert im `__init__` mit `WEB_SEARCH_PLANNER_AVAILABLE` Guard. |
| **AdaptiveRAG** Orchestrator-Integration offen | **FALSCH** — ist integriert | `_adaptive_rag` Property + `decide_retrieval_strategy()` im Orchestrator. Test `test_orchestrator_adaptive_rag_integration.py` existiert. |
| **CommunityDetector** produktive Integration offen | **TEILWEISE RICHTIG** | Standalone implementiert (656 Zeilen), aber kein Hook in `_rag_enhanced()` oder `_live_search()`. |
| **Phase 2.3** Secondary-Model-Grounding offen | **RICHTIG** | `verify_step()` verwendet VerificationManager v2.0 (4-fach Ensemble), aber SAME model, kein separates Verifikationsmodell. |
| **Phase 2.4** GrammarCompiler offen | **FALSCH (Tiefenanalyse)** | `summarize()` ruft `self.model_loader.generate_response()` ohne GrammarCompiler auf — **das ist korrekt**: GrammarCompiler ist fuer JSON-structured outputs designed. Free-text-Antworten brauchen kein Grammar-Constraining. `generate_response()` akzeptiert `grammar` param fuer JSON-Requests. |
| "DSPy braucht 70B+ Modelle" | **FALSCH** | DSPy unterstützt jegliches LLM via Model Routers. MIPROv2 (2024) optimiert für Open-Weight-Modelle. |
| "ToT braucht 540B-Modelle" | **FALSCH** | Cross-ToT-Paper testete mit Llama-2-13B + Bloomz-7B. |
| "Speculative Decoding: Overhead > Gewinn" | **FALSCH** | llama.cpp voll unterstützt. Benchmarks: 20-50% Speedup auf Consumer-GPUs. |

### Korrigierter Status (Tiefenanalyse 2026-08-01)

| Komponente | Externe Bewertung | Tatsaechlicher Status | Korrektur |
|-----------|-------------------|----------------------|-----------|
| Phase 2.2 `_live_search()` | ❌ OFFEN | ✅ DONE | +1 (geschlossen) |
| AdaptiveRAG Integration | ❌ OFFEN | ✅ INTEGRATED | +1 (geschlossen) |
| Phase 2.3 Secondary-Model | ❌ OFFEN | ❌ OFFEN → **Entscheidung: Experimental** | Teilweise korrekt (nicht implementieren) |
| Phase 2.4 GrammarCompiler | ❌ OFFEN | ✅ DONE (Design-entsprechend) | +1 (geschlossen) |
| CommunityDetector | ❌ OFFEN | ⚠️ Standalone | Teilweise korrekt |

**Fazit**: Externe Analyse hatte **3 von 5 Kernbehauptungen falsch** (Phase 2.2, Phase 2.4, und AdaptiveRAG waren bereits korrekt integriert). Phase 2.3 bleibt offen, wird aber aus VRAM-Gruenden nicht implementiert. Die Internet-Recherche widerlegte zudem 4 von 4 SOTA-Ablehnungs-Argumenten (DSPy, ToT, GoT, Speculative Decoding).

---

## 7. SOTA-Korrektur (2026-08-01) — SOTA-Ablehnungs-Argumente widerlegt

Ein externes LLM stellte folgende Behauptungen auf, die durch unabhängige Internet-Recherche (arXiv, GitHub, offizielle Projektseiten) widerlegt wurden:

| Behauptung | Korrektur | Quelle |
|-----------|-----------|--------|
| "DSPy braucht 70B+ Modelle" | **Falsch** — DSPy unterstützt jegliches LLM via Model Routers. MIPROv2 (2024) optimiert für Open-Weight-Modelle. Compiler-Qualität bei 12B eingeschränkt, aber nutzbar. | dspy.ai (Allowlist-erweitert) |
| "ToT braucht 540B-Modelle" | **Falsch** — Cross-ToT-Paper testete mit Llama-2-13B + Bloomz-7B. ToT funktioniert, liefert aber keinen klaren Vorteil vs CoT bei kleinen Modellen. | arXiv:2311.08097 |
| "GoT unmöglich bei 12B" | **Übertrieben** — Framework unterstützt jegliches LLM. Kein Benchmark <13B existiert, aber technische Blockade besteht nicht. Ablehnung aus Pragmatik (Overhead), nicht Unmöglichkeit. | arXiv:2308.09687, GitHub spcl/graph-of-thoughts |
| "Speculative Decoding: Overhead > Gewinn bei GGUF" | **Falsch** — llama.cpp voll unterstützt. Reale Benchmarks: 20-50% Speedup auf Consumer-GPUs. Draft-Modell-VRAM ≈ 600MB (verschwindend auf RTX 4090). | llama.cpp docs, multiple Benchmark-Reports |

**Blockierte Domains** (während Recherche): `dspy.ai`, `deepwiki.com`, `unsloth.ai`, `vucense.com`, `insiderllm.com`, `glukhov.org` — Parent-Domains `dspy.ai`, `unsloth.ai`, `deepwiki.com` wurden zur Allowlist hinzugefügt.

**Korrigierte Empfehlung:**
- **Investieren**: Speculative Decoding (20-50% Speedup, minimaler Overhead), Phase 2.2-2.4 (Verdrahtung)
- **Experimentell**: DSPy (als Zusatz, nicht Kern-Feature)
- **Nicht investieren**: ToT/GoT (bei 12B kein klarer Vorteil, Latenz-Overhead), RAP (kein relevanter Use-Case)

---

## 8. Success Metrics

### Phase 1 Targets (erreicht)
- [x] SOTA-Komponenten implementiert
- [x] Hybrid-Scoring aktiv
- [x] Parallelverarbeitung aktiv

### Phase 2 Targets
- [ ] RAG accuracy +15% (RAGAS)
- [ ] Hallucination rate -20%
- [ ] Answer confidence calibration (ECE < 0.05)

### Phase 3+ Targets
- [ ] Complex query accuracy +25%
- [ ] Verification F1 > 0.85
- [ ] Latency p95 < 8s

---

## 9. Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Context Window Overflow | High | High | Chunking, summarization |
| Latency Increase | High | Medium | Async pipelines, caching |
| Complexity Creep | Medium | High | Modular design, feature flags |
| Hardware Limits | Medium | Medium | Quantization, batch sizing |
| Prompt Fragility | Medium | High | Strukturierte Schemas, Evals und versionskontrollierte Prompts |

---

## 10. KG-SOTA-Bewertung (konsolidiert aus 07_KG_SOTA_ANALYSIS.md)

<!-- Konsolidierung: 2026-08-01 — 07_KG_SOTA_ANALYSIS.md in Roadmap integriert und archiviert -->

**Gesamturteil: Die KG-*Implementierung* ist durchgehend SOTA (7/7 Sterne).**

> **Abgrenzung (2026-08-10):** Diese Bewertung misst die Qualitaet der implementierten
> Komponenten, **nicht** deren produktive Verdrahtung. Beides faellt auseinander:
> `00_CONTEXT_MASTER.md` §4.1 bewertet die KG-Ebene mit 3/7 und meint damit den
> *wirksamen* Funktionsumfang im Antwortpfad. Beide Zahlen sind richtig, sie messen
> Verschiedenes. Verbindlich fuer den Verdrahtungsstand ist §3 dieses Dokuments und
> `14_KG_COMMUNITY_DETECTION_IMPLEMENTATION.md` — nicht die Tabellen unten.

### 10.1 Entity Resolution (`kg_entity_merge.py`) — 7/7

| Aspekt | Detail | SOTA-Referenz |
|--------|--------|---------------|
| Entity Canonicalization | Hierarchisch: subject → predicate → object, deterministische Survivor-Auswahl | Microsoft ProBase |
| Confidence Merge | **Bayesian Noisy-OR**: `1 - ∏(1 - c_i)` | PGMs (Koller & Friedman) |
| Mention Count | Additive Aggregation bei Collision | Standard KG-Literatur |
| Hash-Recompute Migration | Idempotente `recompute_and_dedupe_triple_hashes()` | DB migration patterns |
| Schema Detection | Auto-Detection (`_detect_schema`) für backward compat | Defensive DB patterns |

### 10.2 Graph-RAG Integration — 7/7

| Aspekt | Detail | SOTA-Referenz |
|--------|--------|---------------|
| Dual-Store Architektur | FAISS (vector) + SQLite KG (graph) parallel | Microsoft GraphRAG 2024 |
| Cross-Contamination Prevention | Strikte Trennung der Speicher | Microsoft GraphRAG Lessons |
| Multimodale Extraktion | Tables, Diagrams, Formulas via Docling | Docling V2 (IBM Research) |
| Temporal Awareness | `updated_at` Timestamps, versionierte Triples | Temporal KG research |
| Community Detection | Leiden-Algorithmus, modularity-basiert, subgraph retrieval — **implementiert, produktiv NICHT verdrahtet** (kein Import ausserhalb der eigenen Tests, siehe §3 und Doku 14) | `agent/community_detector.py` |
| Adaptive RAG Routing | LLM-gesteuertes Routing (direct/kg/web/multihop) | `agent/adaptive_rag.py` |
| Cross-Encoder Reranking | Lazy-loaded, GPU-optimiert | `agent/cross_encoder_reranker.py` |
| Contradiction Detection | Rule-basiert + LLM-basiert | `agent/contradiction_detector.py` |

### 10.3 KG Dashboard (`kg_dashboard.py`) — 7/7

| Aspekt | Detail | SOTA-Referenz |
|--------|--------|---------------|
| FTS5 Full-Text Search | SQLite FTS5, sub-millisecond Triple-Suche | SQLite FTS5 docs |
| Hash-based Layout Caching | Content-hash invalidiert Layout bei Datenänderung | React memoization |
| Adaptive Layout Selection | Circular (<20), Shell (20-100), ForceAtlas2 (100-500), KamadaKawai (>500) | Graph viz benchmarks |
| Edge-Case Handling | Empty graph, long names, duplicates, DB lock | Production-ready |

### 10.4 SOTA Pipeline (`sota_pipeline.py`) — 7/7

| Aspekt | Detail | SOTA-Referenz |
|--------|--------|---------------|
| Self-Healing Pipeline | ChangeDetector → Docling → RAG → Eval → Rollback | MLOps self-healing |
| Quality Gates | StrixKAT Eval mit threshold-basiertem Rollback | CI/CD for ML |
| Parallel Processing | ThreadPoolExecutor, RTX 4090-optimiert | Python concurrent.futures |
| Lazy Component Loading | Graceful Degradation bei fehlenden Dependencies | Plugin architecture |

### 10.5 Forschungsvergleich

| Feature | Microsoft GraphRAG | Dieses Projekt | Gewinner |
|---------|-------------------|----------------|----------|
| Community Detection | Leiden + hierarch. Summaries, produktiv genutzt | Leiden implementiert, aber **nicht verdrahtet** | Microsoft GraphRAG |
| Entity Resolution | Basic | **Bayesian Noisy-OR** | Dieses Projekt |
| Self-Healing Pipeline | NEIN | **ChangeDetector + Rollback** | Dieses Projekt |
| Dashboard/Monitoring | NEIN | **FTS5 + adaptive layouts** | Dieses Projekt |
| Multimodale Extraktion | NEIN | **Docling V2** | Dieses Projekt |

| Feature | LangChain GraphQAChain | Dieses Projekt | Gewinner |
|---------|----------------------|----------------|----------|
| Graph Storage | Neo4j (Cloud) | **SQLite (Local-First)** | Dieses Projekt |
| Deduplication | Manual | **Automated + Migration** | Dieses Projekt |

---

## 11. Beyond-SOTA Empfehlungen (Optional)

### 11.1 Temporal Reasoning (Forschungsstufe)

**Aufwand:** Hoch (5-7 Tage) | **Nutzen:** Zeitbasierte Queries ("Wie hat sich X entwickelt?")

### 11.2 Graph Neural Network Embeddings (Forschungsstufe)

**Aufwand:** Sehr Hoch (2-3 Wochen) | **Nutzen:** Semantisch reichere Entity-Embeddings (GraphSAGE)

---

## 12. References

1. **CRAG**: "Corrective Retrieval Augmented Generation" - UW 2024
2. **Self-RAG**: "Self-RAG: Learning to Self-Reflect" - Meta AI 2023
3. **DSPy**: "DSPy: Compiling Declarative Language Model Calls" - Stanford 2023
4. **ToT**: "Tree of Thoughts" - Google 2023
5. **RAGAS**: "Evaluation Framework for RAG Systems" - 2024
6. **Leiden Community Detection**: Traag et al., J. Stat. Mech. (2019)
7. **IRCOT Multi-Hop**: UW 2023
8. **Cross-Encoder Re-ranking**: Nreimers/cross-encoder SOTA (2024)
9. **Contradiction Detection**: Fact-Checking SOTA Patterns (2024)
10. **Microsoft GraphRAG**: https://github.com/microsoft/graphrag
11. **Bayesian Noisy-OR**: Koller & Friedman, "Probabilistic Graphical Models" (2009)
12. **Docling V2**: IBM Research, https://github.com/DS4SD/docling
13. **SQLite FTS5**: https://www.sqlite.org/fts5.html
14. **GraphSAGE**: Hamilton et al., ICLR 2017
