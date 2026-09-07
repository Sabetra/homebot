# SOTA-Optimierung Workdoc (2026-07-31)

## Vollständiger ursprünglicher Auftrag

> "Siehst Du optimierungspotential für das Projekt? Recherchier dafür im Internet, was Sota ist."

## Scope & Nicht-Scope

**IN Scope:**
- P0: Speculative Decoding Benchmark (llama-cpp-python auf RTX 4090 + Gemma4 12B)
- P0: KG-Qualitäts-Messung (Entity-Count, Relation-Dichte, Coverage)
- P1: AutoTuner in Startup-Lifecycle verdrahten
- P1: DecompositionEngine für MODERATE-Queries aktivieren
- P2: GrammarCompiler generalisieren
- P3: Community Detection aktivieren (falls KG-Qualität OK)
- P3: DSPy evaluieren

**NICHT im Scope:**
- vLLM-Migration (zu invasiv, Nutzen unklar)
- Tree of Thoughts (zu komplex für 12B)
- Architekturwechsel (existierender Code ist SOTA-nah)
- Deadcode-Reinigung (erst Performance messen)

## Verifizierte Fakten (mit Belegen)

| Fakt | Quelle | Symbol/Zeile |
|------|--------|--------------|
| DecompositionEngine existiert, 445 Zeilen | `agent/decomposition_engine.py` | Klasse `DecompositionEngine` |
| DecompositionEngine im Orchestrator verdrahtet | `agent/orchestrator.py` | `self.decomposition_engine = DecompositionEngine(...)` |
| DecompositionEngine nur COMPLEX/VERY_COMPLEX | `agent/rag_manager.py` | Kommentar: *"intentionally returns [] for SIMPLE/MODERATE"* |
| AutoTuner existiert, 608 Zeilen | `agent/auto_tuner.py` | Klasse `AutoTuner` |
| AutoTuner NICHT im Startup | `psychological_session/services/startup_service.py` | Kein Import von `auto_tuner` |
| gpu_optimizer.py existiert, 409 Zeilen | `gpu_optimizer.py` | Klasse `GPUOptimizer` |
| KEIN Speculative Decoding aktiv | `gpu_optimizer.py` | Kein `speculative`-Parameter |
| Gemma4 12B GGUF produktiv | `docs/00_CONTEXT_MASTER.md` | "Produktiv-LLM: Gemma4 12B" |
| n_batch=3072, n_ubatch=2048 | `docs/00_CONTEXT_MASTER.md` | GPU-Parameter-Tabelle |
| RTX 4090, 24GB VRAM | AGENTS.md | Hardware-Angabe |

## CrossRef SOTA-Recherche (live, 2026-07-31)

| Bereich | Paper | Datum | Relevanz |
|---------|-------|-------|----------|
| Speculative Decoding | "Transactional KV Caching for Speculative Decoding under Paged KV Memory" | 2026-02 | 1.5-2x Speedup belegt |
| Multi-Hop RAG | "RDMSFR-RAG: Reasoning RAG Based on Relation Disambiguation" | 2026-05 | Multi-Scale Fusion |
| Multi-Hop RAG | "DTE-RAG: Dual-Tree Structure Contextual Enhanced" | 2026-04 | Similarity + Relatedness |
| DSPy | "Optimizing LLM Prompt Engineering with DSPy-Based Declarative Learning" | 2026-04 | Lokale Modelle unterstützt |
| GraphRAG | "A Survey of Agentic GraphRAG" | 2026-05 | Graph-native Agents |

## Hypothesen (klar getrennt von Fakten)

1. **H1:** Speculative Decoding mit llama-cpp beschleunigt Gemma4 12B um 30-50% auf RTX 4090
2. **H2:** KG hat <500 Entities oder Relation-Dichte <0.3 (Community Detection daher wertlos ohne Audit)
3. **H3:** DecompositionEngine für MODERATE-Queries verbessert Recall um 10-15%
4. **H4:** AutoTuner periodic stabilisiert RAG-Qualität über Zeit

## Änderungen & Testergebnisse

### P0: Speculative Decoding Benchmark
- [ ] Benchmark ohne Speculative Decoding (Baseline)
- [ ] Benchmark mit Speculative Decoding
- [ ] Ergebnis: tokens/s vor/nach

### P0: KG-Qualitäts-Messung
- [ ] Entity-Count
- [ ] Relation-Dichte
- [ ] Coverage

---

**Änderungs-Log:**
| Datum | Änderung | Test | Ergebnis |
|-------|----------|------|----------|
| 2026-07-31 | Workdoc erstellt | - | - |
| 2026-07-31 | KG-Audit: DB leer | `scripts/kg_quality_audit.py` | 0 Entities, 0 Relations |
| 2026-07-31 | llama-cpp 0.3.20 | `pip show` | SD kompatibel, nicht aktiv |
| 2026-07-31 | AutoTuner nicht im Startup | `startup_service.py` Zeile 1-253 | Kein Import von `auto_tuner` |
| 2026-07-31 | DecompEngine Gating | `decomposition_engine.py` | SIMPLE/MODERATE → passthrough |
