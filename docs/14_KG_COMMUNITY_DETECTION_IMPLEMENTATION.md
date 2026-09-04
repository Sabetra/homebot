<!-- last-verified: 2026-08-20 -->
# KG Community Detection - Implementationsdokument

## User-Aufgabenstellung (Original)

```
setze folgendes um:
- Community Detection auf dem bestehenden Graphen
- Subgraph-Retrieval statt nur 1-Hop-Nachbarn
- Community-Summaries für Antwortsynthese
- KG in den RAG-Reranker einspeisen
```

## Status: VOLLSTÄNDIG INTEGRIERT ✅

### Erfolgreich abgeschlossen
- [x] Bestehende Dokumentation analysiert (00_CONTEXT_MASTER.md, 07_KG_SOTA_ANALYSIS.md, multimodal_rag.py)
- [x] SOTA Community Detection Methoden recherchiert (Leiden, LFR-Benchmark)
- [x] `community_detector.py` erstellt (Leiden-Algorithmus, CommunitySummaries, SubgraphRetriever)
- [x] Community Detection, Subgraph-Retrieval, Community-Summaries und Rerank-Scores im Modul implementiert
- [x] Fokussierte Tests in `tests/test_community_detection.py` vorhanden
- [x] **CommunityDetector in SearchManager.lazy-init integriert (2026-08-12)**
- [x] **Community-Aware Triple-Expansion in `_knowledge_graph_search` (Step 2b)**
- [x] **Community-Rerank-Score in Scoring-Formel (entity + reranker + lifecycle + community)**
- [x] **Disk-Persistenz für Community-State (`.community_state.json`)**
- [x] **Alle 631 Tests bestanden (keine Regression)**
- [x] **`get_community_info()` und `generate_community_summaries()` implementiert (2026-08-13)** — behebt AttributeError bei Initialisierung

### Fehlerbehebung (2026-08-13)
**Problem:** `search.py` rief `detector.generate_community_summaries()` (Zeile 1909) und `detector.get_community_info(comm_id)` (Zeilen 2022, 2033, 2080) auf, aber diese Methoden existierten nicht in `CommunityDetector`.

**Root Cause:** API-Lücke zwischen `search.py` (Consumer) und `community_detector.py` (Provider). Die Klasse hatte `get_communities()` (alle) und `get_community_summary()` (nur String), aber keine Methode für einzelnes CommunityInfo-Objekt.

**Fix:** Zwei Methoden hinzugefügt:
1. `get_community_info(community_id)` → O(1) CommunityInfo-Lookup mit lazy Summary/Keyword-Generierung
2. `generate_community_summaries()` → Batch-Generierung keyword-basierter Summaries für alle Communities

**Verifikation:** 9/9 Tests bestanden, Integrationstest erfolgreich.

## Integrationsarchitektur (SOTA v5)

### Pipeline-Integration in `_knowledge_graph_search`

```
Query → Semantic Entity Match → 1-Hop Triples
    → [★ Community Detection] → Relevant Communities
    → [★ Community Expansion] → Additional Triples
    → 2-Hop Expansion → Scoring (entity + reranker + lifecycle + community)
    → Top-k Results
```

### SearchManager Erweiterungen

| Methode | Zweck |
|---------|-------|
| `_ensure_community_detector()` | Lazy-Init mit Disk-Persistenz |
| `_get_relevant_communities()` | Finde Communities passend zum Query |
| `_expand_triples_via_communities()` | Triple-Expansion via Community-Mitglieder |
| `_get_community_rerank_score()` | Community-Qualität als Rerank-Signal |
| `invalidate_community_detector()` | Cache-Invalidation nach KG-Mutationen |

### CommunityDetector API (vollständig)

| Methode | Rückgabe | Zweck |
|---------|----------|-------|
| `detect_communities()` | `Dict[str, Any]` | Leiden-Algorithmus ausführen |
| `get_communities()` | `List[CommunityInfo]` | Alle Communities als Info-Objekte |
| `get_community_info(id)` | `Optional[CommunityInfo]` | Einzelne Community als Info-Objekt **(2026-08-13)** |
| `get_community_summary(id)` | `str` | Summary-String einer Community |
| `generate_community_summaries()` | `Dict[int, str]` | Batch-Generierung aller Summaries **(2026-08-13)** |
| `get_node_community(node)` | `Optional[int]` | Community-ID eines Nodes |
| `get_community_nodes(id)` | `Optional[Set[str]]` | Nodes einer Community |
| `retrieve_subgraph(query, ...)` | `SubgraphResult` | Subgraph-Retrieval |
| `compute_rerank_scores(...)` | `List[float]` | Community-aware Reranking |
| `save_state(path)` | `bool` | Persistenz |
| `load_state(path)` | `bool` | Wiederherstellung |

### Scoring-Formel (angepasst)

**Mit Reranker:**
```
score = 0.15 × entity_score
      + 0.40 × reranker_score
      + 0.25 × lifecycle_score
      + 0.20 × community_score  ← NEU
```

**Ohne Reranker:**
```
score = 0.40 × entity_score
      + 0.35 × lifecycle_score
      + 0.25 × community_score  ← NEU
```

### Persistenz

- Community-State wird unter `.community_state.json` im DB-Verzeichnis gespeichert
- Validierung bei Laden: Graph-Struktur muss innerhalb von ±20% Node-Anzahl liegen
- Automatische Neugenerierung bei Staleness

## Wichtige Infos aus existing Docs

### Aus 00_CONTEXT_MASTER.md
- Local-First Multimodal AI Chatbot, Windows 11, 64GB RAM, RTX 4090
- Primär-LLM: Gemma4 12B (GGUF via llama-cpp-python)
- venv: `<PROJEKT_ROOT>\venv_mistral_gguf\Scripts\Activate.ps1`
- Streamlit-UI, RAG (FAISS + Docling), KG (NetworkX), Finance-Engine

### Aus 02_SOTA_ROADMAP.md (KG-SOTA-Sektion; ehem. 07_KG_SOTA_ANALYSIS.md)
- NetworkX ist aktuelle KG-Implementierung
- KG-Reranker wird bereits diskutiert aber nicht implementiert
- 1-Hop-Nachbarn sind aktuelle limitation

### Bestehende KG-Architektur (multimodal_rag.py)
- `KnowledgeStore`: NetworkX DiGraph mit node/edge attributes
- FAISS für vector similarity search
- Docling für document processing
- Query pipeline: embed -> retrieve -> rerank -> synthesize

## SOTA Referenzen

| Methode | Quelle | Sterne |
|---------|--------|--------|
| Leiden Algorithmus | Traag et al. 2019 | ★★★★★★★ |
| LFR-Benchmark | Lancichinetti et al. 2008 | ★★★★★★★ |
| Modularity Optimization | Newman 2006 | ★★★★★★★ |
| Label Propagation | Raghavan et al. 2007 | ★★★★★** |
| Infomap | Rosvall & Bergstrom 2008 | ★★★★★** |

## Nächste Schritte

1. Kontrollierenden KG-Retrievalpfad festlegen und Community Detection dort injizieren
2. Subgraph-Ergebnisse als typisierten Retrievalvertrag durchreichen
3. Community-Summaries und Rerank-Scores in Synthese/Reranking integrieren
4. Integrations- und Regressionsabdeckung ergaenzen