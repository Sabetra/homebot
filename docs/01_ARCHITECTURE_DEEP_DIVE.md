<!-- last-verified: 2026-07-26 -->
# ARCHITECTURE DEEP DIVE

> **Stand:** 2026-07-26 | **Consolidation Release 1.2**
> **Quellen:** aktiver Code, `ARCHITECTURE.md`, `agent/orchestrator.py`, `agent/sota_pipeline.py`

---

## 1. CORE ARCHITECTURE PATTERN

### 1.1 High-Level Flow
```
User Input
    |
    v
[Semantic Router] --> [SIMPLE | PLAN_EXECUTE | REACT]
    |                    |
    v                    v
[Retrieval Router]   [ReAct Agent Loop]
    |                    |
    v                    v
[RAG / KG / Web]    [Tool Execution]
    |                    |
    v                    v
[EvidenceReRank]    [Response Generation]
    |
    v
[IRCoT Chain] --> [CRAG Self-Correction] --> [Final Response]
```

### 1.2 Semantic Router (`agent/orchestrator.py`)
Routes incoming queries to appropriate handling strategy:
- **SIMPLE**: Direct answer, single-hop RAG
- **PLAN_EXECUTE**: Complex queries requiring multi-step reasoning
- **REACT**: Queries needing tool execution (finance, web search, code)

### 1.3 Retrieval Router
Determines best knowledge source:
- **RAG**: Document-based retrieval (FAISS indices)
- **KG**: Knowledge Graph entity resolution
- **Web Search**: Real-time information lookup

---

## 2. SOTA RAG PIPELINE

### 2.1 Pipeline Stages (Detailed)

#### Stage 1: Multi-Query Fallback
- Generates 3 query variants (original + 2 rephrasings)
- Cross-validator ensures semantic equivalence
- Prevents single-query failure modes

#### Stage 2: EvidenceReRank
- 3-tier scoring: Relevance + Recency + Authority
- Combines vector similarity with metadata weights
- Filters out low-confidence evidence

#### Stage 3: IRCoT (Information-Seeking ReAct Chain of Thought)
- Iterative reasoning: Think -> Search -> Evaluate -> Repeat
- Maximum 3 iterations to prevent infinite loops
- Each iteration builds on previous findings

#### Stage 4: CRAG Self-Correction
- Verify -> Retry cycle (max 2 attempts)
- Factuality checker validates claims against sources
- Confidence threshold determines acceptance

#### Stage 5: StrixKAT Evaluation
- Offline batch evaluation of RAG quality
- 15+ metrics including faithfulness, answer relevance
- SQLite snapshots for quality tracking

### 2.2 SOTA Pipeline Components

| Component | File | Lines | Status |
|-----------|------|-------|--------|
| ChangeDetector | `agent/change_detector.py` | ~800 | Production |
| Docling-Parallel | `agent/docling_parallel.py` | ~750 | Production |
| Multi-Modal RAG | `agent/multimodal_rag.py` | ~900 | Production |
| StrixKAT Eval | `agent/strixkat_eval.py` | ~1100 | Production |
| SOTA Pipeline | `agent/sota_pipeline.py` | ~950 | Production |

---

## 3. GPU ARCHITECTURE

### 3.1 CUDA Lock Mechanism
6+ modules use CUDA locks to prevent race conditions:
- Model inference
- Embedding generation
- PDF processing
- Image generation
- KG operations
- Finance queries

### 3.2 VRAM Management
- Adaptive batch sizing based on VRAM class
- Default config: n_batch=3072, n_ubatch=2048
- Thread count: 12 (optimized for RTX 4090)
- VRAM monitoring via NVML/PyTorch

### 3.3 Performance Verification

Latenz-, VRAM- und Durchsatzwerte sind hardware-, Modell- und Datensatz-abhaengig und werden hier nicht als statische Architekturwerte gepflegt. Fuer LLM-Parameter und reproduzierbare Messungen gelten `RTX4090_RYZEN9_GUIDE.md` und `scripts/benchmark_llm_gpu_tuning.py`.

---

## 4. KNOWLEDGE GRAPH

### 4.1 KG Architecture
- NetworkX graph for entity relationships
- Semantic entity matching with cosine similarity
- FAISS indices for fast similarity search
- PII protection layer

### 4.2 KG Dashboard
- Visual graph exploration
- Entity resolution interface
- Relationship analysis
- Semantic search

### 4.3 KG Integration
- Embedded in RAG pipeline as knowledge source
- Cross-references with document evidence
- Supports multi-hop entity resolution

---

## 5. FINANCE MODULE ARCHITECTURE

### 5.1 Query Pipeline
```
Natural Language Query
    |
    v
[FinanceQueryPlanner.plan()] --> typisierter Toolplan
    |
    v
[Finance-Tools] --> schema-validierte DB-Abfragen
    |
    v
[FinanceQueryReflector.decide()] --> done | continue
    |
    v
[Database Execution] --> Results
```

### 5.2 Components
| Component | File | Lines | Purpose |
|-----------|------|-------|---------|
| Query Planner | `finance/query_planner.py` | `plan()` | Validierter `FinanceQueryPlan` |
| Grammar Compiler | `finance/grammar_compiler.py` | `compile_for_schema()` | Pydantic-Schema zu BNF |
| Query Reflector | `finance/query_reflector.py` | `decide()` | Fortsetzungsentscheidung |
| Tab UI | `finance/tab.py` | Streamlit UI | Finance-Workflow |
| Cache | `finance/cache.py` | In-Process Cache | Query-Ergebnisse |
| DB Schema | `finance/db_schema.py` | Schema/Migrationen | SQLite-Vertrag |

---

## 6. WELLBEING SESSION MODULE

### 6.1 Session Lifecycle
- Multi-phase therapy workflow support
- Session state management
- Insight extraction
- Emotional analysis

### 6.2 Key Files
- `wellbeing_session/lifecycle/session_lifecycle_manager.py`
- `wellbeing_session/services/startup_service.py`
- `wellbeing_session/services/async_startup_service.py`

---

## 7. INTERNATIONALIZATION (i18n)

### 7.1 Architecture
- JSON-based locale files
- Custom i18n_manager
- Supported languages: DE, EN, BG

### 7.2 Key Files
- `i18n/i18n_manager.py` - Core manager
- `i18n/locales/de.json` - German translations
- `i18n/locales/en.json` - English translations
- `i18n/locales/bg.json` - Bulgarian translations

---

## 8. ERROR HANDLING & FAILOVERS

### 8.1 4-Tier Fail-Safe
1. **Primary**: Full RAG pipeline with GPU acceleration
2. **Secondary**: CPU-only fallback with reduced features
3. **Tertiary**: Cached response retrieval
4. **Emergency**: Pre-defined safe responses

### 8.2 No Silent Errors
- All errors logged with context
- Graceful degradation on failure
- User-facing error messages localized

---

## 8a. MODUL-LANDSCHAFT: WELCHE EBENE WOFUER

<!-- Ergaenzt 2026-08-10, belegt durch Import-Analyse des aktiven Codes. -->

Mehrere Bereiche sehen auf den ersten Blick nach doppelten, konkurrierenden Stacks
aus. Sie sind es ueberwiegend **nicht** — es sind Schichten mit verschiedenen
Konsumenten. Diese Sektion haelt fest, welcher Pfad kanonisch ist, damit die Frage
nicht bei jeder Aenderung neu beantwortet werden muss.

### 8a.1 Wellbeing-Modul: geschichtet, nicht doppelt

`wellbeing_session/` und `wellbeing/` sind **kein** Alt/Neu-Paar.
`wellbeing_session_interface.py` (Root) ist die Orchestrierungsschicht und
importiert aus **beiden**:

| Ebene | Verzeichnis | Rolle |
|-------|-------------|-------|
| Orchestrierung | `wellbeing_session_interface.py` | Einstieg, bindet beide Ebenen zusammen |
| Modulare Ebene | `wellbeing_session/` | Services, Handlers, Lifecycle, UI, Workflow (LangGraph), Context |
| Engine/Daten | `wellbeing/` | `session_manager.py`, `conversation_core.py`, `wellbeing_db.py` |
| Bruecke | `wellbeing_session/adapters/SessionManagerAdapter` | verbindet modulare Ebene mit der Engine |

Belege: `wellbeing_session_interface.py:40-62` (modulare Ebene),
`:91` und `:97` (Engine), `:122` (Adapter).

**Konsequenz:** `wellbeing/` ist nicht abzuloesen, sondern die
Datenschicht. Aenderungen an Sessions laufen ueber die modulare Ebene, Aenderungen
an Persistenz und therapeutischer Kernlogik ueber die Engine.

### 8a.2 RAG: gestufte Konsumenten

| Komponente | Datei | Wird benutzt von |
|-----------|-------|------------------|
| Substrat (Quality, DB, Embeddings, Klassifizierung) | `agent/rag_store/` | UnifiedRagStore, Maintenance-Skripte, Quality-Dashboard |
| Store-Fassade | `agent/unified_rag_store.py` | `agent/tools.py` (mit Fallback auf `agent/rag_store`) |
| Manager | `agent/rag_manager.py` | `agent/orchestrator.py:53` |
| Pipeline | `agent/rag_pipeline.py` | `agent/react_agent.py:64` |
| Multimodal | `agent/multimodal_rag.py` | `agent/orchestrator.py:67` |

Auch hier keine Duplikate, sondern verschiedene Einstiegspunkte je Konsument.
Wer den Retrieval-Pfad des normalen Chats aendert, arbeitet an `rag_manager` +
`unified_rag_store`; wer den ReAct-Pfad aendert, an `rag_pipeline`.

### 8a.3 UI: hier gibt es echten Altbestand

`enhanced_streamlit_bot.py` + `ui_tabs/` ist der einzige produktive UI-Pfad
(`enhanced_streamlit_bot.py:29-34`).

`refactored_gui/` ist **bis auf eine Datei tot**: Nur
`refactored_gui/quality_dashboard.py` wird noch geladen, und zwar per
`importlib.spec_from_file_location` (`enhanced_streamlit_bot.py:117-121`).
`main_gui.py`, `streamlit_app.py`, `tabs/`, `widgets.py` und `workers.py` haben
ausserhalb von `refactored_gui/` selbst keine Konsumenten.

**Zielzustand:** `quality_dashboard.py` an einen regulaeren Ort verschieben
(z. B. `ui_tabs/`) und regulaer importieren, danach `refactored_gui/` entfernen.
Solange das nicht geschehen ist, gilt: keine neue Arbeit in `refactored_gui/`.

### 8a.4 Datenbank-Wurzel: ein Ort, ein Resolver

<!-- Ergaenzt 2026-08-10 nach Behebung des Zwei-Wurzeln-Splits. -->

Alle produktiven Datenbanken liegen unter dem `.db_root`-Ziel
(z. B. `~\.local\share\homebot_dbs`), aufgeloest ueber
`utils/db_path_resolver` (Prioritaet: `HOMEBOT_DB_ROOT`-Env → `.db_root`-Datei
→ Projekt-Root). Die logische Trennung der DBs bleibt unveraendert —
Psycho-DB (verschluesselt), RAG-Store, Finance-DB und Chat-History sind
getrennte Dateien mit getrennten Vertraegen; nur der Ablageort ist einheitlich.

| DB | Pfad unter DB-Root | Resolver-Helfer |
|----|--------------------|-----------------|
| RAG-Store | `rag_store.db` | `get_rag_store_path()` |
| Psycho (verschluesselt, Key daneben) | `psychological_support.db` | `get_wellbeing_path()` |
| Chat-History | `chat_history.db` | `get_chat_history_path()` |
| Finance | `database/finance.db` | `get_finance_path()` |
| Web-Policy | `web_policy.db` | `get_web_policy_path()` |
| StrixKAT-Feedback | `agent/rag_store.db` | `get_agent_rag_path()` |
| Psycho-KG-Cache | `psychological_support_kg_cache/` | folgt automatisch dem Psycho-DB-Pfad |

**Verbindliche Regel:** DB-Pfade nie als Literal (`"rag_store.db"`) oder ueber
eigene Workspace-Normalisierung aufloesen — immer ueber den Resolver.
Hintergrund: Zwischen 2026-07-29 (Einfuehrung `.db_root`) und 2026-08-10
umgingen `agent/tools.py` (Workspace-Root-Normalisierung von 2025-10-11),
`agent/web_policy.py` (CWD-relativ) und `agent/strixkat_eval.py`
(CWD-relative Literale) den Resolver. Folge: dieselbe DB existierte in zwei
Wurzelverzeichnissen mit divergierendem Inhalt. Die verwaisten Altstaende
liegen archiviert unter `~\homebot_backups\db\`.

---

## 9. CONFIGURATION

### 9.1 Environment Variables

Die frueher hier aufgefuehrten `RAG_*`-Schalter sind im aktiven Code nicht implementiert und daher kein Konfigurationsvertrag. Laufzeitkonfiguration erfolgt ueber vorhandene Orchestrator-Setter, `settings.json` und explizit im Code gelesene Environment-Variablen. Neue Schalter muessen zuerst implementiert und getestet werden, bevor sie hier dokumentiert werden.

### 9.2 Config Files
- `settings.json` - Application settings
- `requirements.txt` - Dependencies
- `requirements-dev.txt` - Dev dependencies

---

*Fuer Aenderungen an der Architektur, dieses Dokument aktualisieren.*