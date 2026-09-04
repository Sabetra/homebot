# SOTA Roadmap Verification Workdoc
# Created: 2026-08-01
# Status: ANALYSIS COMPLETE - Ready for Roadmap Update

## Task
Verifiziere Analyse eines anderen LLM zu offenen Punkten in `docs/02_SOTA_ROADMAP.md` und korrigiere entsprechend.

---

## Verification Results

### Phase 2: Deep Orchestrator Integration

| Item | Other LLM Claim | Verified Status | Correction |
|------|----------------|----------------|------------|
| **2.1** `_rag_enhanced()` | ✅ DONE (UnifiedRAGStore indirekt aktiv) | **✅ CONFIRMED DONE** | AdaptiveRAGRouter/Pipeline initialisiert (L541/552), `_rag_enhanced()` on L1588, called L2409. Voll integriert. |
| **2.2** `_live_search()` | ❌ OFFEN - QueryPlanner/Reflector nicht verwendet | **❌ INCORRECT CLAIM** | `_live_search()` (L1408-1475) verwendet WebSearchPlanner + WebSearchReflector (L1449-1458). Fallback auf simple search bei Fehler. **VOLL INTEGRATED.** |
| **2.3** `_verify_and_fix_tools()` | ⚠️ TEILWEISE - kein Secondary-Model | **✅ CORRECT** | `verify_step()` (L3458) verwendet VerificationManager v2.0 (4-fach Ensemble), aber kein separater Secondary Model. **Entscheidung: Experimental, nicht implementieren.** |
| **2.4** `_generate_answer_from_sources()` | ❌ OFFEN - GrammarCompiler nicht integriert | **⚠️ PARTIALLY CORRECT** | `summarize()` (L1121-1168) ruft `model_loader.generate_response()` OHNE grammar param auf. **ABER**: GrammarCompiler ist für JSON-structured outputs designed, nicht für free-text Antworten. `generate_response()` accepts `grammar` param (scripts/model_loader.py L~700). Free-text-Pfad braucht kein Grammar. |

### Phase 3: UI + Lifecycle

| Item | Status |
|------|--------|
| **3.1** GUI Config-Panel fuer SOTA-Pipeline-Toggles | ❌ OFFEN - confirmiert |
| **3.2** Lifecycle-Integration (Health-Metriken) | ❌ OFFEN - confirmiert |
| **3.3** Startup-Sequence (AsyncStartupService) | ❌ OFFEN - confirmiert |

### Runtime-Integration

| Komponente | Other LLM Claim | Verified Status |
|-----------|----------------|----------------|
| **CommunityDetector** | Standalone; produktive Integration offen | **✅ CONFIRMED** | Nur in `agent/community_detector.py`, tests, docs. Nicht in Orchestrator importiert. |
| **AdaptiveRAG** | Implementiert + getestet; Orchestrator-Integration offen | **❌ INCORRECT** | Voll integriert: L541 AdaptiveRAGRouter, L552 AdaptiveRAGPipeline, L1588 `_rag_enhanced()`, L2409 call site, L4919 CRAG-Loop. |
| **SOTAPipeline** | Initialisiert; Lifecycle/Continuous Mode nicht default-gesteuert | **✅ CONFIRMED** | L503-505 initialisiert, L605-606 property, L4613-4630 Metriken. Kein Lifecycle-Health-Metriken-Integration. |
| **DoclingParallel** | Initialisiert; im Chat-Antwortpfad nicht aktiv | **✅ CONFIRMED** | L480-485 initialisiert, aber kein Call im `_generate_answer_from_sources()` Pfad. |

### SOTA-Landschaft

| Pattern | Status | Anmerkung |
|---------|--------|-----------|
| **RAP** | ❌ Nicht implementiert | Roadmap: LOW priority, nicht investieren |
| **ToT** | ⚠️ Strategie-Selector existiert | Roadmap: nicht investieren |
| **GoT** | ⚠️ Linearer DAG | Roadmap: nicht investieren |
| **DSPy** | ⚠️ Keine produktive Nutzung | Roadmap: experimentell |

---

## Summary of Corrections

| Claim | Was | Ist | Impact |
|-------|-----|-----|--------|
| Phase 2.2 `_live_search()` | ❌ OFFEN | **✅ DONE** | WebSearchPlanner + Reflector seit L1449 integriert |
| Runtime AdaptiveRAG | ❌ Integration offen | **✅ INTEGRATED** | 5+ Integration points im Orchestrator |
| Phase 2.4 GrammarCompiler | ❌ OFFEN | **⚠️ INTENTIONAL** | Grammar nur fuer JSON, nicht fuer free-text |
| Phase 2.3 Secondary Model | ⚠️ TEILWEISE | **✅ DESIGN DECISION** | Experimental, bewusst nicht implementiert |

---

## Next Steps
1. [ ] Update `docs/02_SOTA_ROADMAP.md` with corrected status
2. [ ] Update `docs/00_CONTEXT_MASTER.md` if needed
3. [ ] Run tests to verify no regressions
4. [ ] Archive Workdoc