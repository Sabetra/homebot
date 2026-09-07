# SOTA RAG Quality Pipeline - Implementierungs-Dokumentation

> **Verifizierter Stand (24.06.2026):**
> Die vier Kernmodule sind im Code vorhanden und die wichtigsten Integrationsfehler wurden bereinigt.
> Die tiefere Orchestrator-Integration ist jedoch nur teilweise umgesetzt; einige Namen werden über Kompatibilitäts-Wrapper/Aliase bereitgestellt.

## User-Prompt (Original)

```
kannst Du diese Punkte Sota umsetzen?
## Ja - Alle vier zusammen bilden ein **komplettes, self-healing RAG-Ökosystem**

## Das Gesamtsystem: RAG-Qualität von der Quelle bis zur Antwort

[Pipeline-Diagramm: ChangeDetector -> Docling-Parallel -> Multi-Modal RAG -> StrixKAT Eval]

## Synergie-Matrix, Konkreter Nutzen, Performance-Gesamtbilanz, Empfehlung

Beachte:
Ich nutze einen PC mit Windows, 64 GB RAM und RTX4090. Als LLM für den Bot nutze ich i.d.R. Gemma4 12B. 
nutze die virtuelle Umgebung: <PROJEKT_ROOT>\venv_mistral_gguf\Scripts\Activate.ps1
```

## Hardware-Konfiguration
- **OS:** Windows 11
- **RAM:** 64 GB
- **GPU:** RTX 4090 (24GB VRAM)
- **LLM:** Gemma4 12B (primär)
- **Venv:** <PROJEKT_ROOT>\venv_mistral_gguf\Scripts\Activate.ps1

## Projektstatus

| Phase | Komponente | Status | Datei |
|---|---|---|---|
| P1 | ChangeDetector (P3-2) | ✅ Implementiert | agent/change_detector.py |
| P2 | Docling-Parallel (P3-3) | ✅ Implementiert mit Kompatibilitäts-Wrapper | agent/docling_parallel.py |
| P3 | Multi-Modal RAG (P4-2) | ✅ Implementiert mit Kompatibilitäts-Alias | agent/multimodal_rag.py |
| P4 | StrixKAT Eval (P2-3) | ✅ Implementiert mit Kompatibilitäts-Alias | agent/strixkat_eval.py |
| P5 | Pipeline Integration | ✅ Als Kompatibilitätsschicht vorhanden | agent/quality_pipeline.py |

## Bestehende Architektur (Relevant)

### UnifiedRagStore (agent/unified_rag_store.py)
- FAISS-basiert mit Hybrid Search (BM25 + Semantic)
- Content Classifier (5 Kategorien: financial, medical, legal, technical, general)
- Auto-Plumbing (Embedding/LLM Auto-Detection)
- GPU-Unterstützung (cuda_index_state)
- Deduplication, TTL, Bulk-Import
- ~2500+ Zeilen

### AgentOrchestrator (agent/orchestrator.py)
- Planning, Tool Execution, Evidence Selection
- IRCoT (Iterative Retrieval Chain-of-Thought)
- CRAG Self-Correction
- Multi-Query RAG
- Dynamic Context Window
- ~4200+ Zeilen

### EmbeddingManager
- BGEMultiLanguage mit GPU-Optimierung
- Batch-Processing unterstützt

### Existing PDF Processor
- advanced_pdf_processor.py (existiert bereits)
- content_extractor/ Modul

### Verifizierte SOTA-Module
- `agent/change_detector.py`: `ChangeDetector`, `ChangeEvent`, `WatchConfig`, plus `scan()`, `start()`, `stop()`
- `agent/docling_parallel.py`: `DoclingParallelProcessor` plus `process_single()` und `process_batch()`
- `agent/multimodal_rag.py`: `MultiModalRAGIndex`, `MultiModalRAG`, sowie Alias `MultimodalRAG`
- `agent/strixkat_eval.py`: `StrixKATEvalEngine`, `StrixKATEval`, `StrixKATEvaluator`
- `agent/sota_pipeline.py`: `SOTAPipeline` als tatsächliche Integrationsklasse
- `agent/quality_pipeline.py`: Kompatibilitäts-Wrapper auf `SOTAPipeline`

## Design-Prinzipien
1. **Self-Healing:** Qualitätseinbruch automatisch erkennen, diagnostizieren, beheben
2. **Zero-Config:** Auto-Detection wie UnifiedRagStore
3. **GPU-First:** RTX 4090充分利用
4. **Async-First:** Kein Blockieren des Live-Betriebs
5. **Observability:** Vollständige Metriken und Logging

## Verifizierter Implementierungsstatus

Die folgenden Punkte sind im aktuellen Code bestätigt:
- `ChangeDetector` kann Verzeichnisse überwachen, Hash-basierte Änderungen erkennen und Änderungen als dict-kompatible Ereignisse liefern.
- `Docling-Parallel` verarbeitet einzelne Dateien und bietet `process_single()` als kompatiblen Einstiegspunkt für die Pipeline.
- `Multi-Modal RAG` bietet eine indexierende Klasse und einen Pipeline-kompatiblen `chunk_document()`-Pfad über `MultiModalRAG`.
- `StrixKAT Eval` stellt Evaluierungs- und Rollback-kompatible Methoden bereit, darunter `evaluate_full_pipeline()` und `rollback_to_last_good_state()` im Wrapper.
- `agent/sota_pipeline.py` ist die echte Orchestrierungsschicht; `agent/quality_pipeline.py` dient nur als Alias/Kompatibilität.

Offen bleibt die tiefe Ausgestaltung der Orchestrator-Features wie `_rag_enhanced`, `_live_search`, `_verify_and_fix_tools` und `_generate_answer_from_sources`.

## Abhängigkeiten
- watchdog (File System Watching)
- docling (PDF Processing) - falls verfügbar
- hashlib (built-in)
- concurrent.futures (built-in)
- sqlite3 (built-in)

## CHANGELOG

### 2026-06-24 - Projektstart
- Implementierungs-Doku erstellt
- Plan mit User abgestimmt
- ACT MODE gestartet

### 2026-06-24 - Discovery Phase (Vollstaendig)
- README.md, ARCHITECTURE.md, SOTA_ROADMAP.md gelesen
- Existing code analysiert: UnifiedRagStore (~2500 Zeilen), AgentOrchestrator (~4200 Zeilen)
- RAGAS SOTA Evaluation gefunden (ragas_sota_evaluation.py)
- GPU Optimizer gefunden (gpu_optimizer.py)
- Advanced PDF Processor gefunden (advanced_pdf_processor.py)
- Smart Fusion Engine gefunden (smart_fusion_engine.py)
- Tests analysiert: test_unified_rag_store.py, test_corrective_rag.py, test_rag_quality_metrics.py
- File watcher gefunden: document_file_watcher.py
- Config manager analysiert: config_manager.py (~1500 Zeilen)
- Network visualizer, PII protection, Internet search gefunden

### 2026-06-24 - Verifikation und Kompatibilität
- Kernmodule in `agent/` verifiziert und Pylance-Fehler bereinigt
- Interface-Drift zwischen Orchestrator und Modulen über Aliase/Wrapper entschärft
- `agent/quality_pipeline.py` als Kompatibilitätsdatei ergänzt
- `py_compile` für die betroffenen Module erfolgreich ausgeführt

## SOTA-Recherche Ergebnisse (Mitte 2026)

### Change Detection in RAG Pipelines
- **SOTA Ansatz**: Content-based hashing + file system events + change manifests
- **Key Paper**: "Continuous RAG: Incremental Index Updates for Dynamic Knowledge" (2025)
- **Best Practice**: Hash-basierte Deduplication + event-driven Reindexing

### Document Processing
- **SOTA Tool**: Docling (IBM Research 2024) - PDF-to-structured mit Layout-Understanding
- **Alternative**: Marker, PDFPlumber + Tesseract (falls Docling nicht verfuegbar)
- **Key Feature**: Table extraction, figure detection, formula parsing

### Multi-Modal RAG
- **SOTA Ansatz**: Unified embedding space für Text, Tabellen, Bilder, Formeln
- **Key Paper**: "Multi-Modal RAG: Retrieving across Text, Tables and Figures" (2025)
- **Implementation**: Separate chunk types mit typ-spezifischen Embeddings

### RAG Evaluation
- **SOTA Framework**: RAGAS (rasa GmbH) + DeepEval
- **Key Metrics**: Faithfulness, Answer Relevancy, Context Precision, Context Recall
- **Advanced**: LLM-as-Judge mit Rubrics, Adversarial Testing

## Next Steps
- [x] Discovery & Documentation
- [x] Kernmodule verifiziert
- [x] Integrations-Namensdrift bereinigt
- [ ] Tiefe Orchestrator-Features ausbauen (`_rag_enhanced`, `_live_search`, `_verify_and_fix_tools`, `_generate_answer_from_sources`)
- [ ] End-to-End-Lauf mit Test-PDF validieren
- [ ] Doku bei weiteren API-Änderungen synchron halten

---

## Delta-Update (2026-07-11)

Seit der Erstversion dieser Doku wurden folgende Root-Cause-Fixes im Code umgesetzt:

1. `agent/sota_pipeline.py`
	- ChangeDetector-Integration auf korrekte API umgestellt (`WatchConfig`-basierte Instanziierung).
	- Prozesspfade auf Lazy-Properties umgestellt (`docling_processor`, `multimodal_rag`, `strixkat_eval`) statt direkter Nutzung interner `_...`-Felder.

2. `agent/strixkat_eval.py`
	- `rollback_to_last_good_state()` ist kein Stub mehr.
	- Snapshot/Restore via SQLite Online-Backup-API implementiert.
	- "Last good state" wird bei hinreichender Qualitätsbewertung persistiert.

3. `agent/config_manager.py`
	- Explizites Reload eingeführt (`reload(force=...)`, `reload_if_override_changed()`).
	- File-change-basierte Reload-Erkennung über `.agent_env`-mtime ergänzt.

4. Finance Query Reflection (P2-2) adversarial abgesichert
	- Neue adversarial Test-Suite ergänzt: `tests/test_finance_reflector_adversarial.py`.
	- Abgedeckte Angriffs-/Fehlerfälle:
		- `retry_search` mit leerem/ungültigem `query_text` -> Default-Injektion (`query_text`, `limit`, `include_transfers`).
		- `retry_sql` mit `sql_query` statt `sql` -> Normalisierung auf `sql`.
		- `retry_counterparty_costs` ohne valide `counterparty` -> Fallback auf Nutzerfrage.
		- Unerwartete Action (`retry_unknown_action`) -> sicher ignoriert (kein Dispatch).
	- Validierungslauf (venv):
		- `pytest -q tests/test_finance_reflector_adversarial.py` -> `4 passed`
		- `pytest -q tests/test_finance_chat.py tests/test_finance_reflector_adversarial.py` -> `6 passed`

Damit ist der in dieser Doku beschriebene Integrationsstand gegenüber 24.06.2026 verbessert; verbleibend ist primär die formale E2E-Test-Absicherung als Qualitäts-Gate.
