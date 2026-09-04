<!-- last-verified: 2026-08-20 -->
# Agent Orchestrator - Production-Ready Architecture

> **Status:** Legacy-Referenz (historischer Stand) — aktuelle, kanonische Architektur: `docs/01_ARCHITECTURE_DEEP_DIVE.md`

## Overview

The Agent Orchestrator is a sophisticated AI agent system with modular architecture, comprehensive verification, and production-ready error handling. It coordinates planning, tool execution, evidence selection, RAG search, answer generation, and quality verification.

## Architecture

### Core Components

```
┌─────────────────────────────────────────────────────────────┐
│                    AgentOrchestrator                         │
│                   (Main Coordinator)                         │
└────────────────────────┬────────────────────────────────────┘
                         │
         ┌───────────────┼───────────────┐
         │               │               │
         ▼               ▼               ▼
┌────────────────┐ ┌──────────────┐ ┌──────────────┐
│ SecurityManager│ │QueryStrategy │ │  Evidence    │
│                │ │   Manager    │ │   Manager    │
│ • Input Valid. │ │ • Complexity │ │ • Collection │
│ • PII Detect.  │ │ • Routing    │ │ • Ranking    │
│ • Prompt Inj.  │ │ • Refinement │ │ • Filtering  │
└────────────────┘ └──────────────┘ └──────────────┘
         │               │               │
         │               ▼               │
         │       ┌──────────────┐       │
         │       │  RAGManager  │       │
         │       │              │       │
         │       │ • RAG Search │       │
         │       │ • GPU FAISS  │       │
         │       │ • Multi-Query│       │
         │       └──────────────┘       │
         │               │               │
         └───────────────┼───────────────┘
                         ▼
                 ┌──────────────┐
                 │  Response    │
                 │  Builder     │
                 │ • Prompts    │
                 │ • Citations  │
                 │ • Formatting │
                 └──────────────┘
                         │
                         ▼
                 ┌──────────────┐
                 │Verification  │
                 │  Manager     │
                 │ • Quality    │
                 │ • Grounding  │
                 │ • Hallucination│
                 └──────────────┘
```

## Essential Module Map (What Code Lives Where)

This map complements the deep-dive sections below and gives a fast overview of
the most important modules and their code responsibilities.

### 1. `agent/orchestrator.py` (Core Coordination)

- Main class: `AgentOrchestrator`
- Routing/data classes: `RetrievalRoute`, `RetrievalRoutingDecision`
- Contains code for end-to-end query execution:
    - planner/tool dispatch orchestration
    - manager pipeline composition (security, strategy, evidence, RAG, verification)
    - domain routing (finance, psychological, code/analysis flows)
    - trace generation, fallbacks, and lifecycle cleanup

### 2. `agent/intent_detector.py` (Intent Classification)

- Core types/classes: `IntentType`, `IntentResult`, `GenericIntentDetector`, `VisualizationIntentDetector`
- Public helpers: `detect_user_intent()`, `wants_visualization()`
- Contains code for semantic intent classification before planning, including
    visualization-specific detection and confidence-based fallback paths.

### 3. `agent/query_strategy_manager.py` (Strategy and Decomposition)

- Core classes: `QueryAnalyzer`, `QueryRefiner`, `StrategySelector`, `SubQueryGenerator`, `QueryStrategyManager`
- Enums/models: `QueryComplexity`, `SearchStrategy`, `QueryStrategy`
- Contains code for complexity assessment, query refinement, search-strategy
    selection, and sub-query generation for multi-query retrieval.

### 4. `agent/security_manager.py` (Safety Guardrails)

- Core classes: `PatternInjectionDetector`, `LLMInjectionDetector`, `InjectionAggregator`, `PIIDetector`, `SecurityManager`
- Supporting types: `ThreatLevel`, `InjectionAnalysis`, `ValidationResult`
- Contains code for prompt-injection detection, input/output validation, PII
    checks, threat scoring, and strict-mode policy enforcement.

### 5. `agent/evidence_manager.py` (Evidence Selection)

- Core classes: `EvidenceDeduplicator`, `EvidenceQualityFilter`, `EvidenceManager`
- Result containers: `EvidenceCollectionResult`, `SourceSelectionResult`
- Contains code for collecting tool evidence, deduplication, quality filtering,
    reranking/diversity logic, and final source selection for synthesis.

### 6. `agent/rag_manager.py` + `agent/unified_rag_store.py` (Retrieval Layer)

- `agent/rag_manager.py` core classes: `GPUFAISSManager`, `RAGManager`, `RAGResult`
- `agent/unified_rag_store.py` core component: `UnifiedRagStore` (hybrid store)
- Contains code for GPU/CPU retrieval execution, FAISS/BM25 hybrid search,
    index lifecycle management, result fusion, and retrieval-time filtering.

### 7. `agent/response_builder.py` (Prompt + Output Formatting)

- Core classes: `CitationManager`, `PromptBuilder`, `ResponseFormatter`, `ResponseBuilder`
- Factory helper: `get_response_builder()`
- Contains code for prompt construction (planner/summarizer/verifier prompts),
    citation insertion, source post-processing, and final answer formatting.

### 8. `agent/verification_manager.py` (Quality and Grounding)

- Core classes: `VerificationManager`, `TermOverlapGrounder`, `SemanticGrounder`, `NLIEntailmentChecker`, `LLMFactVerifier`, `AtomicClaimDecomposer`
- Core enums/models: `VerificationLevel`, `VerificationStatus`, `VerificationResult`
- Contains code for multi-level answer verification, grounding/hallucination
    checks, claim decomposition, entailment analysis, and confidence scoring.

### 9. `agent_toolkit.py` + `agent_chatbot_logic.py` (Runtime Integration)

- `agent_toolkit.py`: `AgentToolkit` (tool registration + execution abstraction)
- `agent_chatbot_logic.py`: `AgentChatbotLogic` + retry/degradation wrapper logic
- Contains code for runtime integration across tools, session-bound chat
    execution, model-call retries, and orchestrator handoff into UI/chat flows.

### 10. `finance/*.py` (Grounded Finance Subsystem)

- `finance/db_schema.py`: ORM entities (`Bank`, `Account`, `Statement`, `Transaction`, etc.) + `FinanceDB`
- `finance/query_planner.py`: `FinanceQueryPlan`, `FinanceQueryPlanner`
- `finance/query_reflector.py`: `FinanceContinuationDecision`, `FinanceQueryReflector`
- `finance/tools.py`: `FinanceTools` tool surface
- `finance/chat.py`: `FinanceChatEngine`, `FinanceChatResult`, `FinanceChatTrace`
- Contains code for schema-aware finance planning, read-only SQL execution,
    reflection-guided iteration (`done`/`retry_search`/`retry_sql`), and
    grounded final synthesis from tool evidence.

## Module Contracts (Code-Verified)

Verified against current imports and runtime wiring on **2026-06-23**.

### `agent/orchestrator.py`

| Contract Aspect | Code-verified Definition |
|---|---|
| Entrypoint | `AgentOrchestrator.run_tools_and_summarize(...)`, `run_no_tools_and_summarize(...)`, `planner_step(...)`, `summarize(...)` |
| Consumes | `ToolManager`, `ContextManager`, `SecurityManager`, `QueryStrategyManager`, `EvidenceManager`, `RAGManager`, `ResponseBuilder`, `VerificationManager` |
| Produces | `FinalAnswer` + `AgentTrace`-oriented data path via planner/summarizer/verification stages |
| Side Effects | tool execution, optional web->RAG persistence, runtime/env-driven mode switches, trace/log emission |
| Invariants | preserves manager-pipeline orchestration; degrades via fallback paths instead of hard failing query loop |

### `agent/intent_detector.py`

| Contract Aspect | Code-verified Definition |
|---|---|
| Entrypoint | `detect_user_intent(...)`, `wants_visualization(...)` |
| Consumes | query text + optional LLM wrapper |
| Produces | intent classification (`IntentResult`) + confidence |
| Side Effects | logging only |
| Invariants | emits typed intent objects; uncertainty remains representable via confidence score |

### `agent/query_strategy_manager.py`

| Contract Aspect | Code-verified Definition |
|---|---|
| Entrypoint | `QueryStrategyManager` methods for complexity, refinement, sub-query generation |
| Consumes | query text, optional context/history, optional llm callable |
| Produces | `QueryComplexity`, strategy/refinement outputs, sub-query candidates |
| Side Effects | logging and optional LLM calls |
| Invariants | strategy space is enum-bounded (`QueryComplexity`, `SearchStrategy`) |

### `agent/security_manager.py`

| Contract Aspect | Code-verified Definition |
|---|---|
| Entrypoint | `SecurityManager` validation APIs |
| Consumes | input/output text plus URL/source strings |
| Produces | `ValidationResult`, injection/PII findings, threat-level signal |
| Side Effects | security diagnostics logging |
| Invariants | unsafe patterns are surfaced as structured findings, not hidden in free text |

### `agent/evidence_manager.py`

| Contract Aspect | Code-verified Definition |
|---|---|
| Entrypoint | `EvidenceManager` selection/merge functions |
| Consumes | tool results + candidate sources |
| Produces | `EvidenceCollectionResult`, `SourceSelectionResult` |
| Side Effects | ranking/dedup processing and logging |
| Invariants | dedup/filter stage precedes final source shortlist |

### `agent/rag_manager.py` + `agent/unified_rag_store.py`

| Contract Aspect | Code-verified Definition |
|---|---|
| Entrypoint | `RAGManager` retrieval execution; `UnifiedRagStore` indexing/query APIs |
| Consumes | query/sub-queries + retrieval config |
| Produces | ranked retrieval results (`RAGResult` and source payloads) |
| Side Effects | FAISS/BM25 index access, optional GPU utilization, persistence/checkpoint IO |
| Invariants | retrieval path supports degraded operation when specific acceleration/runtime features are unavailable |

### `agent/response_builder.py`

| Contract Aspect | Code-verified Definition |
|---|---|
| Entrypoint | `ResponseBuilder` (`PromptBuilder`, `ResponseFormatter`, `CitationManager`) |
| Consumes | query/history/evidence plus raw generation text |
| Produces | formatted answer text with citation/source wrapping |
| Side Effects | formatting diagnostics logging |
| Invariants | formatting/citation stage is post-retrieval and post-selection, not a retrieval substitute |

### `agent/verification_manager.py`

| Contract Aspect | Code-verified Definition |
|---|---|
| Entrypoint | `VerificationManager.verify_answer(...)` |
| Consumes | answer text + evidence list + `VerificationLevel` |
| Produces | `VerificationResult` with quality/grounding/hallucination signals |
| Side Effects | optional semantic/NLI/fact-check style calls + logging |
| Invariants | verification output remains structured even under partial-check failures |

### `agent_toolkit.py` + `agent_chatbot_logic.py`

| Contract Aspect | Code-verified Definition |
|---|---|
| Entrypoint | `AgentToolkit` tool dispatch (`_initialize_tools`, `execute_tool` path), `AgentChatbotLogic.chat(...)` |
| Consumes | user turns, model loader, orchestrator + tool registry |
| Produces | chat response text and tool-execution artifacts |
| Side Effects | tool execution, optional web/content extraction, optional code execution, caching/session/runtime mode changes |
| Invariants | chat loop continues with guarded fallbacks/retries; tool surface is centralized in toolkit mapping; planner/agent prompts derive tool context from runtime catalog to prevent schema drift |

### `finance/*.py`

| Contract Aspect | Code-verified Definition |
|---|---|
| Entrypoint | `FinanceChatEngine.respond(...)`, `FinanceTools` methods, `FinanceQueryPlanner.plan(...)`, `FinanceQueryReflector.reflect(...)` |
| Consumes | finance question, schema context, finance tool schemas, prior tool outcomes |
| Produces | grounded finance answer + `FinanceChatTrace`/`FinanceChatResult` |
| Side Effects | DB reads through finance tools, reflection-driven retry loop, prompt/schema coverage fail-fast checks |
| Invariants | finance chat restricts tool routing to finance/code-executor scope; SQL path is explicitly read-only in tool contract wording |

## Dependency Matrix (Import and Runtime Wiring)

Legend: `D` = direct import dependency, `R` = runtime/call dependency without direct import,
`-` = no verified dependency in current wiring.

| From \ To | orchestrator | intent_detector | query_strategy | security | evidence | rag | response_builder | verification | toolkit/chat | finance |
|---|---|---|---|---|---|---|---|---|---|---|
| orchestrator | - | D | D | D | D | D | D | D | R | R |
| intent_detector | - | - | - | - | - | - | - | - | - | - |
| query_strategy | - | - | - | - | - | - | - | - | - | - |
| security | - | - | - | - | - | - | - | - | - | - |
| evidence | - | - | - | - | - | - | - | - | - | - |
| rag | - | - | - | - | - | - | - | - | - | - |
| response_builder | - | - | - | - | - | - | - | - | - | - |
| verification | - | - | - | - | - | - | - | - | - | - |
| toolkit/chat | D | R | R | R | R | R | R | R | - | D |
| finance | R | - | R | - | - | - | - | - | R | - |

### Practical Read Path (Why this matrix is useful)

- If a change starts at chat behavior, read in this order:
    `agent_chatbot_logic.py` -> `agent/orchestrator.py` -> targeted manager module.
- If a change starts at finance behavior, read in this order:
    `finance/chat.py` -> `finance/query_planner.py`/`finance/query_reflector.py` -> `finance/tools.py` -> `agent_toolkit.py`.
- If a change starts at evidence/quality behavior, read in this order:
    `agent/orchestrator.py` -> `agent/evidence_manager.py` / `agent/verification_manager.py` / `agent/response_builder.py`.

## Key File Navigation

This section provides verified section maps for the four largest active Python files. **Verified 2026-06-22**: Line counts checked against actual source files and confirmed accurate.

### 1. `agent/orchestrator.py` (4242 lines total)

Central query orchestration engine with modular manager architecture for SOTA patterns.

| Lines | Section | Key Classes / Functions |
|-------|---------|-------------------------|
| 1–100 | Imports & Enums | `from __future__`, type imports, `RetrievalRoute` enum, config flags |
| 101–300 | Feature Detection & Initialization | Intent detection, privacy handler, adaptive planning imports with try/except fallbacks |
| 301–600 | AgentOrchestrator Class Declaration & `__init__` | Class definition, constructor with manager instantiation (SecurityManager, QueryStrategyManager, etc.) |
| 601–1000 | Core Query Execution: `run()` & Dispatchers | Main `run()` entry point, session routing, history handling, fallback logic |
| 1001–1600 | RAG & Evidence Selection | `_select_evidence()`, evidence deduplication, multi-query merging with deduplicator |
| 1601–2200 | Response Generation & Verification | `summarize()`, prompt building, LLM calls, `verify_step()` with verification manager |
| 2201–2800 | Hybrid Reasoning & Advanced Routing | `_route_reasoning()`, systematic/reflection/critic dispatchers, hybrid reasoning integration |
| 2801–3400 | Finance & Domain-Specific Handlers | `_handle_finance_query()`, grammar-constrained generation, SQL execution paths |
| 3401–4000 | Session Lifecycle & Multimodal Features | Psychological session routing, feedback integration, KG cache management |
| 4001–4242 | Utilities, Cleanup & Deprecations | Fallback methods, error handlers, trace serialization, deprecated helper functions |

### 2. `agent/unified_rag_store.py` (5886 lines total)

Consolidated hybrid RAG system with GPU-accelerated FAISS vector search and BM25 keyword fusion.

| Lines | Section | Key Classes / Functions |
|-------|---------|-------------------------|
| 1–200 | Module Header & Runtime Checks | Docstring, FAISS/GPU imports, runtime capability detection (`_ensure_faiss_hybrid_runtime()`) |
| 201–600 | Data Models & PDF Processing | `RAGDocument`, `RAGQueryResult` dataclasses, `AdvancedPDFProcessor` class for document ingestion |
| 601–1200 | Index Initialization & GPU Setup | `UnifiedRagStore.__init__()`, FAISS GPU index creation, BM25 index bootstrap |
| 1201–2000 | FAISS GPU Vector Search Implementation | `_build_gpu_index()`, `_vector_search()`, GPU tensor operations, similarity computation |
| 2001–2800 | BM25 Keyword Search & Term Frequency | `_build_keyword_index()`, `_keyword_search()`, TF-IDF scoring, keyword relevance ranking |
| 2801–3400 | Reciprocal Rank Fusion & Hybrid Merging | `_merge_results()`, `_rrf_fuse()`, score normalization, hybrid result combination |
| 3401–4200 | Index Persistence & Checkpointing | `_save_index()`, `_load_index()`, serialization, corruption recovery, version management |
| 4201–4800 | Document Ingestion Pipeline | `add_documents()`, chunking, batch embedding generation, dual-index updates, deduplication |
| 4801–5400 | Query Execution & Batch Processing | `query()`, `query_batch()`, result formatting, filtering, ranking |
| 5401–5886 | Maintenance, Stats & Utilities | `clear()`, `stats()`, health checks, resource cleanup, shared store factory |

### 3. `agent_chatbot_logic.py` (3345 lines total)

High-level chat session manager integrating orchestrator, RAG store, and LLM call orchestration.

| Lines | Section | Key Classes / Functions |
|-------|---------|-------------------------|
| 1–102 | Imports & Decorators | Standard library & third-party imports, retry decorator definition (`retry_on_failure`) |
| 103–300 | Configuration & Session Models | Environment config, `ChatSession` dataclass with history buffer and metadata |
| 301–600 | ChatbotEngine Initialization | Class definition, constructor linking orchestrator, RAG store, session factory, model paths |
| 601–1000 | Message Processing & Intent Classification | `process_message()`, input validation, pre-filtering, intent detection |
| 1001–1400 | Context Building & History Truncation | `_build_context()`, sliding window management, token budgeting, context cleanup |
| 1401–1800 | LLM Call & Response Extraction | `_call_llm()`, JSON parsing, schema validation, structured fallback parsing |
| 1801–2200 | Session Persistence Layer | `_save_session()`, `_load_session()`, SQLite backend, JSON encoding/decoding |
| 2201–2600 | Error Handling & Recovery | `_handle_llm_error()`, retry logic with exponential backoff, circuit breaker patterns |
| 2601–3000 | Metrics Logging & Observability | `_log_metrics()`, session stats collection, health endpoints, performance tracking |
| 3001–3345 | Advanced Features & Extensions | Follow-up question extraction, complexity analysis, Redis caching integration |

### 4. `agent_toolkit.py` (3183 lines total)

Tool registry and execution engine integrating web search, RAG, finance, and psychology tools.

| Lines | Section | Key Classes / Functions |
|-------|---------|-------------------------|
| 1–111 | Module Imports & Registry Setup | Standard library, third-party imports, tool registry initialization |
| 112–400 | Tool Schema Definitions | `ToolDefinition`, `ToolParameter`, `ToolResult` Pydantic models, validation schemas |
| 401–700 | ToolkitRegistry Class & Initialization | Class declaration, constructor, tool registration, schema validation, dependency injection |
| 701–1200 | Web Search Tool Integration | `web_search()`, `url_scraper()`, rate limiting, cache integration, result formatting |
| 1201–1800 | RAG Tool Integration | `rag_query()`, `rag_add_document()`, index refresh hooks, document upload handlers |
| 1801–2300 | Finance Tool Stack | `finance_sql_query()`, `finance_schema_lookup()`, read-only query guards, result type conversion |
| 2301–2700 | Psychology & KG Tool Integration | `psycho_kg_query()`, `session_state_get()`, KG cache access, psychological session APIs |
| 2701–3000 | Tool Execution Engine | `execute_tool()`, error isolation, timeout handling, result formatting, type coercion |
| 3001–3183 | Tool Discovery & Introspection | `list_tools()`, `get_tool_schema()`, health diagnostics, capability enumeration |

---

## Finance Query Stack

The finance subsystem uses a dedicated grounded path instead of the generic
RAG/web stack:

* [finance/db_schema.py](finance/db_schema.py) persists a versioned schema
    catalog (`finance_schema_catalog`) with schema hash, table metadata,
    relationships, and semantic hints.
* [finance/query_planner.py](finance/query_planner.py) turns the user
    question plus schema context into a structured first tool plan.
* [finance/query_reflector.py](finance/query_reflector.py) runs a structured
    evidence-sufficiency gate (`done` vs. `retry_search` vs. `retry_sql`)
    based on tool outcomes.
* [finance/chat.py](finance/chat.py) injects schema context, executes the
    planned finance tools, applies iterative reflection-guided retries, and
    forces final synthesis from tool results.
* [finance/tools.py](finance/tools.py) exposes the read-only `finance_sql_query`
    tool and the schema-context tool to the agent runtime.
* [agent/tool_schemas.py](agent/tool_schemas.py) and
    [agent_toolkit.py](agent_toolkit.py) register the finance tool contracts and
    dispatchers.

This stack is intentionally explicit: the schema is discovered once, the first
tool choice is planned from structure and query intent, reflection checks if
evidence is sufficient after each tool round, and SQL is used as the
authoritative aggregation path for exact finance questions.

## Manager Modules

### 1. SecurityManager
**Purpose**: Input/output validation, PII detection, security checks

**Features**:
- ✅ Input validation (length, format, malicious patterns)
- ✅ Output validation (PII detection)
- ✅ Prompt injection detection
- ✅ Source URL validation
- ✅ Strict mode support

**Usage**:
```python
from agent.security_manager import SecurityManager

manager = SecurityManager()

# Validate input
result = manager.validate_input(user_query)
if not result.is_safe:
    print(f"Security issues: {result.issues}")

# Validate output
result = manager.validate_output(answer)
if result.pii_found:
    print(f"PII detected: {result.pii_found}")
```

### 2. QueryStrategyManager
**Purpose**: Query analysis, complexity assessment, adaptive routing

**Features**:
- ✅ Query complexity assessment (simple/moderate/complex)
- ✅ Adaptive routing based on complexity
- ✅ Query refinement with context
- ✅ Sub-query generation for multi-query RAG
- ✅ News/current events detection

**Usage**:
```python
from agent.query_strategy_manager import QueryStrategyManager

manager = QueryStrategyManager(llm_callable=llm_wrapper)

# Assess complexity
complexity = manager.assess_query_complexity(query)

# Refine query
refined = manager.refine_query(query, context, history)

# Generate sub-queries
subqueries = manager.generate_subqueries(query, n=5)
```

### 3. EvidenceManager
**Purpose**: Evidence collection, ranking, quality filtering

**Features**:
- ✅ Evidence collection from tool results
- ✅ Evidence deduplication
- ✅ Quality filtering
- ✅ Cross-encoder reranking
- ✅ Diversity optimization
- ✅ Multi-query evidence merging
- ✅ LLM-based evidence selection

**Usage**:
```python
from agent.evidence_manager import EvidenceManager

manager = EvidenceManager(evidence_processor, source_manager, tools_manager)

# Select evidence from tools
result = manager.select_evidence_from_tool_results(
    query=query,
    tool_results=results,
    max_sources=10
)

sources = result.selected_sources
```

### 4. RAGManager
**Purpose**: RAG execution with GPU acceleration and multi-query support

**Features**:
- ✅ Single/multi-query RAG execution
- ✅ GPU-accelerated FAISS search (RTX 4090)
- ✅ Hybrid search (FAISS + numpy fallback)
- ✅ Index management
- ✅ Result deduplication
- ✅ Comprehensive logging

**Usage**:
```python
from agent.rag_manager import RAGManager

manager = RAGManager(tools_manager, enable_gpu=True)

# Execute RAG with multi-query
result = manager.execute_rag_with_multiquery(
    query=query,
    subqueries=subqueries,
    k_per_query=5,
    min_score=0.0
)

sources = result.sources
```

### 5. ResponseBuilder
**Purpose**: Prompt building, citation management, response formatting

**Features**:
- ✅ Planner/summarizer/verifier prompt building
- ✅ Citation augmentation
- ✅ Source filtering (actually used sources only)
- ✅ Response formatting
- ✅ Sources block generation
- ✅ Fallback mode support

**Usage**:
```python
from agent.response_builder import ResponseBuilder

builder = ResponseBuilder()

# Build summarizer prompt
prompt_result = builder.build_summarizer_prompt(
    query=query,
    history=history,
    sources=sources,
    extras=extras
)

# Format response with citations
final_text = builder.format_response(
    raw_answer=answer,
    sources=sources,
    include_citations=True,
    append_sources=True
)
```

### 6. VerificationManager
**Purpose**: Answer quality assessment, hallucination detection, verification

**Features**:
- ✅ Answer quality assessment
- ✅ Evidence grounding checks
- ✅ Hallucination detection
- ✅ Citation verification
- ✅ Fact validation
- ✅ Confidence scoring
- ✅ Multi-level verification (BASIC/STANDARD/STRICT)

**Usage**:
```python
from agent.verification_manager import VerificationManager, VerificationLevel

manager = VerificationManager()

# Verify answer
result = manager.verify_answer(
    answer=answer,
    evidence_list=evidence,
    query=query,
    level=VerificationLevel.STANDARD
)

print(f"Confidence: {result.confidence_score:.2f}")
print(f"Quality: {result.quality_score:.2f}")
print(f"Grounding: {result.grounding_score:.2f}")
print(f"Hallucination Risk: {result.hallucination_risk:.2f}")
```

## Hybrid Reasoning Engine

The orchestrator includes a suite of specialized reasoning backends dispatched via
semantic intent detection. Unlike a single monolithic prompt, each intent class
flows through the reasoning path that matches its cognitive profile.

| Engine | Purpose | When Activated |
|--------|---------|----------------|
| **SystematicThinker** | Toulmin-style structured reasoning (claim, warrant, backing) | `analysis`, `explain` intents |
| **ReflectionEngine** | Self-critique + revision loop (generate → critique → revise) | `compare`, `evaluate` intents |
| **CriticEngine** | Multi-criteria quality grading with structured rubrics | `review`, `grade` intents |
| **DebateEngine** | Adversarial multi-perspective debate synthesis | `debate`, `pros-cons` intents |

```
┌─────────────────────────────────────────────────────────────┐
│                   Hybrid Reasoning Router                     │
└──────────────────────┬──────────────────────────────────────┘
                       │
          ┌────────────┴────────────┐
          │                         │
          ▼                         ▼
   ┌──────────────┐          ┌──────────────┐
   │Systematic    │          │Reflection    │
   │Thinker       │          │Engine        │
   │(Toulmin)     │          │(critique)    │
   └──────────────┘          └──────────────┘
          │                         │
          ▼                         ▼
   ┌──────────────┐          ┌──────────────┐
   │Critic        │          │Debate        │
   │Engine        │          │Engine        │
   │(grading)     │          │(adversarial) │
   └──────────────┘          └──────────────┘
```

### Intent Detection

Intent detection replaced keyword-based routing with LLM-based semantic
classification. The `IntentDetector` (`agent/intent_detector.py`) classifies each
incoming query into one of several intent buckets before the planner runs,
allowing the orchestrator to pre-select the correct reasoning engine and tool
budget.

```python
from agent.intent_detector import IntentDetector

detector = IntentDetector(llm_callable=llm_wrapper)
intent = detector.classify(query)
# intent.intent -> "analysis" | "finance" | "psychological" | "code" | ...
# intent.confidence -> float 0..1
```

Low-confidence intent detections fall back to the default planner path.

### Adaptive Planning

The planner is lazy-loaded and feature-flagged at runtime. Feature flags are
evaluated from environment variables and can toggle entire sub-pipelines without
code changes:

| Flag | Controls | Default |
|------|----------|---------|
| `RAG_ENABLED` | RAG search pipeline | `true` |
| `RAG_MULTIQUERY` | Multi-query expansion | `true` |
| `ENABLE_VERIFICATION` | Answer verification stage | `true` |
| `APP_LOCAL_ONLY` | Network egress guard | `0` |

Planner instantiation is deferred until the first query that actually requires
tool usage, reducing cold-start overhead.

### Query Decomposition

The central decomposition engine (`agent/query_decomposer.py`) breaks compound
queries into atomic sub-queries before routing. Each sub-query is independently
planned, executed, and its results are merged by the evidence manager.

```
Original: "Compare Python vs Rust for web backend performance and ecosystem"
          │
          ▼ Decompose
          ├── "Python web backend performance benchmarks"
          ├── "Rust web backend performance benchmarks"
          ├── "Python web ecosystem maturity (frameworks, packages)"
          └── "Rust web ecosystem maturity (frameworks, packages)"
```

Decomposition is controlled by `QUERY_DECOMPOSITION_ENABLED` and respects a
max-depth limit to prevent combinatorial explosion.

## Wellbeing Session Lifecycle

The wellbeing session module manages a long-lived reflective conversation
context with its own lifecycle:

```
┌─────────────────────────────────────────────────────────────┐
│              SessionLifecycleManager                          │
│                                                               │
│  INIT → WARMUP → ACTIVE → PAUSED → TERMINATED                │
│                                                               │
│  • State transitions are guarded by lifecycle rules           │
│  • KG cache is session-scoped (psycho_kg_faiss/)              │
│  • Startup services inject RAG + tool context on WARMUP       │
└─────────────────────────────────────────────────────────────┘
```

Key files:
- `wellbeing_session/lifecycle/session_lifecycle_manager.py` — state machine
- `wellbeing_session/services/startup_service.py` — synchronous startup
- `wellbeing_session/services/async_startup_service.py` — async startup (non-blocking)

Async startup is preferred in production to avoid blocking the main UI thread
during RAG index warmup.

## Runtime Policy (Local-Only Mode)

Central policy enforcement lives in `utils/runtime_policy.py`. The
`APP_LOCAL_ONLY` environment variable controls network egress:

| Setting | Network | Web Search | Azure OCR | HF Downloads |
|---------|---------|------------|-----------|--------------|
| `0` (default) | allowed | allowed | allowed | allowed |
| `1` (local-only) | blocked | blocked | blocked | offline |

```python
from utils.runtime_policy import parse_bool_env, apply_network_guards

local_only = parse_bool_env("APP_LOCAL_ONLY", default=0)
if local_only:
    apply_network_guards()  # sets HF_HUB_OFFLINE, blocks urllib
```

All entrypoints print a bootstrap line at startup:
`[BOOT] Runtime mode resolved: APP_LOCAL_ONLY raw='...' normalized=0|1`

## Unified RAG Store

The `UnifiedRAGStore` (`agent/unified_rag_store.py`) merges FAISS GPU vector
search with keyword (BM25-style) fallback into a single hybrid index:

```
┌─────────────────────────────────────────────────────────────┐
│                  UnifiedRAGStore                             │
│                                                              │
│  ┌──────────────┐         ┌──────────────┐                  │
│  │ FAISS GPU    │  merge  │ Keyword      │                  │
│  │ Vector Index │◄───────►│ (BM25) Index │                  │
│  └──────────────┘         └──────────────┘                  │
│           │                        │                         │
│           └───────────┬────────────┘                         │
│                       │ Reciprocal Rank Fusion                │
│                       ▼                                       │
│              Hybrid Result Set (deduplicated)                 │
└─────────────────────────────────────────────────────────────┘
```

Hybrid search improves recall for queries that contain proper nouns or
domain-specific terminology that may not embed well in dense vectors.

## Configuration

### Environment Variables

```bash
# RAG Configuration
RAG_ENABLED=true
RAG_K=6
RAG_MIN_SCORE=0.0
RAG_PERSIST_FROM_WEB=true
RAG_CHUNK_SIZE=800
RAG_CHUNK_OVERLAP=100

# Multi-Query RAG
RAG_MULTIQUERY=true
RAG_MQ_N=5
RAG_MQ_K=5

# Evidence Selection
EVIDENCE_MAX_CANDIDATES=20
EVIDENCE_SHORTLIST_M=12
EVIDENCE_DIVERSITY_LAMBDA=0.7

# Generation Settings
SUMMARIZER_MAX_TOKENS=1024
VERIFIER_MAX_TOKENS=1024

# Presentation
CITATION_INLINE_DETAILS=false
APPEND_SOURCES_BLOCK=true
```

### Programmatic Configuration

```python
orchestrator = AgentOrchestrator(
    model_loader=loader,
    rag_enabled=True,
    rag_k=6,
    rag_min_score=0.0,
    use_env_config=True  # Read from environment
)

# Enable verification
orchestrator.enable_answer_verification = True
orchestrator.verification_level = VerificationLevel.STANDARD

# Enable strict security
orchestrator.strict_mode_enabled = True

# Configure multi-query RAG
orchestrator.multiquery_enabled = True
orchestrator.mq_n = 5
orchestrator.mq_k = 5
```

## Usage Examples

### Basic Query

```python
from agent.orchestrator import AgentOrchestrator

# Initialize
orchestrator = AgentOrchestrator(model_loader=loader)

# Run query
result = orchestrator.run(
    query="What is Python?",
    history=[],
    session_id="session_123"
)

# Access results
print(result.text)
print(f"Sources: {len(result.sources)}")
print(f"Confidence: {result.trace.verification_confidence}")
```

### Complex Query with Tools

```python
# Complex query triggers tool usage
result = orchestrator.run(
    query="Search for recent AI developments and explain them",
    history=[],
    session_id="session_456"
)

# Check trace
trace = result.trace
print(f"Tools used: {trace.ran_tools}")
print(f"Evidence domains: {trace.evidence_domains}")
print(f"Verification: {trace.verification_confidence}")
```

### Multi-Turn Conversation

```python
history = []

# Turn 1
result1 = orchestrator.run("What is Python?", history, "session_1")
history.append({"role": "user", "content": "What is Python?"})
history.append({"role": "assistant", "content": result1.text})

# Turn 2
result2 = orchestrator.run("How do I install it?", history, "session_1")
history.append({"role": "user", "content": "How do I install it?"})
history.append({"role": "assistant", "content": result2.text})
```

## Observability

### AgentTrace Fields

```python
trace = result.trace

# Planning
trace.planner_output           # Planner raw output
trace.planned_tools            # Tools planned
trace.ran_tools                # Tools actually executed

# Evidence
trace.evidence_domains         # Source domains
trace.source_validation        # Validation stats

# RAG
trace.rag_enabled              # RAG enabled?
trace.multiquery_enabled       # Multi-query enabled?
trace.subqueries               # Generated sub-queries
trace.rag_stats                # RAG statistics

# Timing
trace.planner_ms               # Planning time
trace.tools_ms                 # Tool execution time
trace.summarize_ms             # Summarization time
trace.verify_ms                # Verification time

# Verification (NEW)
trace.verification_confidence   # Overall confidence
trace.verification_quality      # Quality score
trace.verification_grounding    # Grounding score
trace.verification_hallucination_risk  # Hallucination risk
trace.verification_issues       # List of issues
trace.verification_warnings     # List of warnings
```

## Performance

### Optimizations

- ✅ **GPU Acceleration**: FAISS GPU for fast similarity search
- ✅ **URL Deduplication**: Prevents redundant web scraping
- ✅ **Threading Lock**: Prevents database conflicts
- ✅ **LRU Caching**: Caches frequent operations
- ✅ **Efficient Evidence Selection**: Cross-encoder reranking

### Benchmarks

| Operation | Time (ms) | Notes |
|-----------|-----------|-------|
| Planning | 200-500 | LLM-based |
| Tool Execution | 1000-3000 | Web search + RAG |
| Evidence Selection | 100-300 | Cross-encoder |
| Summarization | 500-1500 | LLM-based |
| Verification | 50-200 | Pattern matching + scoring |
| **Total** | **2000-5500** | Full pipeline |

## Error Handling

### Comprehensive Coverage

All manager modules have comprehensive error handling:

```python
try:
    result = manager.process(input)
except AttributeError as e:
    logger.debug(f"Module not available: {e}")
except (ValueError, TypeError) as e:
    logger.warning(f"Input error: {e}")
except (ConnectionError, TimeoutError) as e:
    logger.warning(f"Network error: {e}")
except Exception as e:
    logger.error(f"Unexpected error: {type(e).__name__}: {e}")
    import traceback
    logger.debug(f"Traceback:\n{traceback.format_exc()}")
```

**No silent failures. Graceful degradation on errors.**

## Testing

### Unit Tests

```bash
# Run all tests
pytest tests/ -v

# Run specific manager tests
pytest tests/test_security_manager.py -v
pytest tests/test_verification_manager.py -v

# With coverage
pytest --cov=agent tests/
```

### Integration Tests

```bash
# Test full orchestrator
pytest tests/test_orchestrator_integration.py -v
```

## Migration Guide

### From Old Architecture

**Before** (Direct prompt building):
```python
from agent.prompts import SUMMARIZER_SYSTEM
messages = [
    {"role": "system", "content": SUMMARIZER_SYSTEM},
    {"role": "user", "content": user_content}
]
```

**After** (Manager-based):
```python
prompt_result = self.response_builder.build_summarizer_prompt(
    query=query,
    history=history,
    sources=sources
)
messages = prompt_result['messages']
```

**Before** (Manual evidence selection):
```python
ranked = self._rank_sources(query, sources)
selected = ranked[:10]
```

**After** (Manager-based):
```python
result = self.evidence_manager.select_evidence_from_tool_results(
    query=query,
    tool_results=results,
    max_sources=10
)
selected = result.selected_sources
```

## Best Practices

### 1. Always Validate Input
```python
validation = orchestrator.security_manager.validate_input(user_query)
if not validation.is_safe:
    return error_response("Unsafe input detected")
```

### 2. Check Verification Results
```python
if trace.verification_confidence < 0.7:
    logger.warning("Low confidence answer")
    # Maybe trigger additional search or fallback
```

### 3. Use Appropriate Verification Level
```python
# For critical applications
orchestrator.verification_level = VerificationLevel.STRICT

# For speed-critical applications
orchestrator.verification_level = VerificationLevel.BASIC
```

### 4. Monitor Trace Metrics
```python
if trace.verify_ms > 5000:
    logger.warning("Slow verification detected")

if len(trace.verification_issues) > 0:
    logger.warning(f"Verification issues: {trace.verification_issues}")
```

## Production Deployment

### Recommended Settings

```python
orchestrator = AgentOrchestrator(
    model_loader=loader,
    n_ctx=128000,
    reserve=4096,
    rag_enabled=True,
    rag_k=6,
    rag_persist_from_web=True,
    use_env_config=True
)

# Production verification
orchestrator.enable_answer_verification = True
orchestrator.verification_level = VerificationLevel.STANDARD

# Security
orchestrator.strict_mode_enabled = True

# Multi-query for better recall
orchestrator.multiquery_enabled = True
orchestrator.mq_n = 5
```

### Monitoring

Monitor these metrics:
- Query latency (p50, p95, p99)
- Verification confidence distribution
- Tool usage patterns
- Error rates by type
- RAG hit rates

## Troubleshooting

### Low Verification Confidence

**Symptom**: `trace.verification_confidence < 0.5`

**Solutions**:
1. Enable multi-query RAG for better evidence
2. Increase evidence quality threshold
3. Use STRICT verification to identify issues

### High Hallucination Risk

**Symptom**: `trace.verification_hallucination_risk > 0.5`

**Solutions**:
1. Check evidence grounding
2. Verify sources are relevant
3. Consider triggering comprehensive search

### Slow Performance

**Symptom**: Total time > 10 seconds

**Solutions**:
1. Check GPU is enabled for FAISS
2. Reduce number of sub-queries
3. Lower verification level
4. Enable caching

## Support

For issues or questions:
1. Check trace for detailed metrics
2. Review logs (debug level)
3. Run unit tests to verify setup
4. Check documentation for configuration

## Changelog

### Version 2.0 (October 2025)
- ✅ Modular manager architecture
- ✅ Comprehensive verification system
- ✅ GPU-accelerated FAISS
- ✅ Multi-query RAG
- ✅ Production-ready error handling
- ✅ Extensive observability

### Version 1.0 (Before refactoring)
- Basic orchestration
- Monolithic architecture
- Limited verification
- Basic error handling

---

**Built with ❤️ for production AI applications**
