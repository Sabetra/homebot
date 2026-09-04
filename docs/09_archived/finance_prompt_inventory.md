# Finance Prompt Inventory

**Generated:** 2026-06-22  
**Status:** Verified against source code  
**Coverage:** query_planner.py, query_reflector.py, chat.py, tools.py, grammar_compiler.py

---

## 1. Query Planner (`finance/query_planner.py`)

### 1.1 Pydantic Schema: `FinanceQueryPlan`
```python
class FinanceQueryPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tool: Literal[
        "finance_search_bookings",
        "finance_sql_query",
        "finance_counterparty_costs",
        "finance_category_costs",
        "finance_cost_structure",
        "finance_recurring_expense",
        "finance_expense_forecast",
        "finance_expense_anomaly",
        "finance_budget_status",
        "finance_budget_vs_actual",
        "finance_savings_potential",
        "finance_expense_trend_break",
    ]
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str = Field(min_length=1)
    arguments: Dict[str, Any] = Field(default_factory=dict)
```

### 1.2 Prompt Template (`_build_planner_prompt`)
Built from `_PLANNER_SYSTEM` + `_PLANNER_CONTEXT_TAIL` + dynamic slots:
- **`{tool_definitions}`**: rendered from `finance/tools.py` docstrings via `_render_tool_definitions`
- **`{schema_context}`**: rendered from `finance/db_schema.py` via `_render_schema_context`
- **`{question}`**: user question
- **`{conversation_context}`**: last 6 turns from history

### 1.3 Argument Defaults Enforcement
Each tool has a corresponding `_default_*` function that fills missing arguments:
| Function | Default Tool | Key Defaults |
|----------|-------------|--------------|
| `_default_search_args` | `finance_search_bookings` | `limit=500`, `include_transfers=False` |
| `_default_sql_args` | `finance_sql_query` | `limit=200`, `time_limit_sec=15` |
| `_default_counterparty_costs_args` | `finance_counterparty_costs` | `start_date`=-90d, `end_date`=now |
| `_default_category_costs_args` | `finance_category_costs` | `start_date`=-90d, `end_date`=now |
| `_default_cost_structure_args` | `finance_cost_structure` | `start_date`=-90d, `end_date`=now |
| `_default_recurring_expense_args` | `finance_recurring_expense` | `start_date`=-180d, `end_date`=now |
| `_default_expense_forecast_args` | `finance_expense_forecast` | `history_months=12`, `forecast_months=3` |
| `_default_expense_anomaly_args` | `finance_expense_anomaly` | `start_date`=-180d, `end_date`=now, `max_items=15` |
| `_default_budget_status_args` | `finance_budget_status` | `month`=current |
| `_default_budget_vs_actual_args` | `finance_budget_vs_actual` | `start_month`=-6mo, `end_month`=current |
| `_default_savings_potential_args` | `finance_savings_potential` | `start_date`=-90d, `end_date`=now |
| `_default_expense_trend_break_args` | `finance_expense_trend_break` | `start_date`=-180d, `end_date`=now, `min_history_months=6` |

### 1.4 Fallback Logic
When structured output fails (exception or empty tool):
1. Try `_infer_tool_from_question` (regex-based intent matching)
2. Try `_infer_tool_from_previous_trace` (repeat last tool with adjusted args)
3. Default to `finance_search_bookings` with user message as query

### 1.5 Regex-Based Intent Matching (`_infer_tool_from_question`)
Patterns detected: budget, forecast, anomaly, savings, trend break, cost structure, recurring, counterparty, category.

### 1.6 Grammar-Constrained Decoding (IMPLEMENTED)
- Calls `compile_finance_plan_grammar()` from `grammar_compiler.py`
- Passes compiled GBNF grammar to `LLMStructuredWrapper`
- Falls back to standard Pydantic validation if grammar unsupported by provider

---

## 2. Query Reflector (`finance/query_reflector.py`)

### 2.1 Pydantic Schema: `FinanceContinuationDecision`
```python
class FinanceContinuationDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal[
        "done",
        "retry_search",
        "retry_sql",
        "retry_counterparty_costs",
        "retry_category_costs",
        "retry_cost_structure",
        "retry_recurring_expense",
        "retry_expense_forecast",
        "retry_expense_anomaly",
        "retry_budget_status",
        "retry_budget_vs_actual",
        "retry_savings_potential",
        "retry_expense_trend_break",
    ]
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str = Field(min_length=1)
    continuation_args: Dict[str, Any] = Field(default_factory=dict)
```

### 2.2 Action Catalog Validation
`_validate_reflector_actions_coverage()` runs at module load:
1. Ensures `_REFLECTOR_ACTIONS` tuple == `Literal` type args
2. Ensures every non-`done` action is mentioned in prompt with `action=<name>`
3. Raises `RuntimeError` on any drift

### 2.3 Prompt Template (`_build_prompt`)
German-language prompt instructing the LLM to decide:
- `action=done` if evidence suffices
- Various `retry_*` actions if evidence is insufficient
- `continuation_args` must be populated with next-tool arguments
- Rules for truncation detection (truncated_possible=true, row_count==applied_limit)

### 2.4 Fallback
`FinanceContinuationDecision(action="done", confidence=0.51, rationale="Fallback: ...")`

### 2.5 Grammar-Constrained Decoding (IMPLEMENTED)
- Calls `compile_reflector_grammar()` from `grammar_compiler.py`
- Passes compiled GBNF grammar to `LLMStructuredWrapper`
- Falls back to standard Pydantic validation if grammar unsupported by provider

---

## 3. Chat Engine (`finance/chat.py`)

### 3.1 `FinanceChatEngine` Configuration
- `MAX_TOOL_RESULT_CHARS`: 12000
- `_max_tokens`: 4096 (planner), 2048 (reflector), 4096 (final answer)
- `_temperature`: 0.1
- `_max_tool_rounds`: 8

### 3.2 Tool Loop (`_run_tool_loop`)
1. Query planner generates `FinanceQueryPlan`
2. Tool executed via `self._toolkit.execute_tool(name, args)`
3. Result appended to trace + messages
4. Query reflector decides continuation
5. If `action != "done"`, retry with `continuation_args`
6. After loop, generate final answer

### 3.3 Response Modes
- `finance_planner`: returns tool name + args only
- `full_execution`: runs tool loop, returns final answer

### 3.4 Recovery Mechanisms
- `_recover_tool_calls_from_text`: tries LLM's `recover_tool_calls` then `_parse_tool_calls`
- `_strip_pseudo_tool_text`: removes `<|tool_call|>` and `call:` prefixes
- `_ensure_search_args`: fills missing `query_text`, `limit`, `include_transfers`
- `_normalize_sql_args`: maps `sql_query` -> `sql`

---

## 4. Tools (`finance/tools.py`)

### 4.1 Tool Summary
| Tool Name | Purpose | Key Parameters | Returns |
|-----------|---------|---------------|---------|
| `finance_search_bookings` | Semantic + fulltext search | `query_text`, `limit`, `include_transfers` | booking list |
| `finance_sql_query` | Read-only SQL execution | `sql`, `query_params`, `limit`, `time_limit_sec` | row list + count |
| `finance_counterparty_costs` | Costs by counterparty | `counterparty`, `start_date`, `end_date` | cost breakdown |
| `finance_category_costs` | Costs by category | `categories`, `start_date`, `end_date` | cost breakdown |
| `finance_cost_structure` | Fixed vs variable costs | `start_date`, `end_date` | structure analysis |
| `finance_recurring_expense` | Recurring expenses/abos | `start_date`, `end_date` | recurring list |
| `finance_expense_forecast` | Future expense projection | `history_months`, `forecast_months` | forecast data |
| `finance_expense_anomaly` | Unusual expense detection | `start_date`, `end_date`, `max_items` | anomaly list |
| `finance_budget_status` | Monthly budget status | `month` | budget summary |
| `finance_budget_vs_actual` | Multi-month budget comparison | `start_month`, `end_month` | comparison table |
| `finance_savings_potential` | Savings opportunity analysis | `start_date`, `end_date` | savings list |
| `finance_expense_trend_break` | Trend break detection | `start_date`, `end_date`, `min_history_months` | trend analysis |

---

## 5. Cache (`finance/cache.py`)

### 5.1 `FinanceQueryCache`
- LRU eviction policy (max 512 entries)
- TTL-based stale eviction (default 600s)
- Async-safe with asyncio locks
- Cache warmup for common queries
- Hit/miss/eviction metrics

---

## 6. Grammar Compiler (`finance/grammar_compiler.py`)

### 6.1 `compile_finance_plan_grammar()`
Generates GBNF grammar for `FinanceQueryPlan` schema:
- Constrains `tool` field to exact 12 tool name literals
- Constrains `confidence` to valid float [0.0, 1.0]
- Constrains `rationale` to non-empty string
- Constrains `arguments` to JSON object

### 6.2 `compile_reflector_grammar()`
Generates GBNF grammar for `FinanceContinuationDecision` schema:
- Constrains `action` field to exact 14 action literals (done + 13 retry_*)
- Same float/string constraints as planner

### 6.3 `validate_grammar()`
Validates GBNF grammar syntax before use.

### 6.4 Graceful Fallback
Returns `None` if llama.cpp unavailable, triggering standard validation.

---

## 7. Structured Output Wrapper (`llm_structured_wrapper.py`)

### 7.1 `LLMStructuredWrapper`
- Wraps LLM client with Pydantic schema validation
- `generate_structured_safe`: retries up to `max_retries` on validation errors
- Falls back to JSON mode if Pydantic tools unavailable
- Temperature 0.0 for deterministic outputs

### 7.2 Grammar-Constrained Decoding Support
- Accepts `grammar_constraint` parameter
- Falls back gracefully to generate-then-validate when grammar unsupported

---

## 8. Optimization Status

### 8.1 P0: Grammar-Constrained Decoding ✅ IMPLEMENTED
**Impact:** ++ | **Effort:** + | **Status:** Complete  
Grammar compilation via `finance/grammar_compiler.py` with GBNF grammars for both `FinanceQueryPlan` and `FinanceContinuationDecision`. Integrated into `query_planner.py` and `query_reflector.py` with graceful fallback to standard validation.

### 8.2 P0: Unit Tests for Query Planner
**Impact:** High robustness | **Effort:** Medium  
Test with mock LLM for all 12 tool selections + fallback paths.

### 8.3 P1: Caching Layer for Aggregations
**Impact:** Reduce DB load | **Effort:** Low  
Extend `FinanceQueryCache` to cache aggregation results (budget, forecast, etc.).

### 8.4 P1: Export Function (CSV/Excel)
**Impact:** User value | **Effort:** Low  
Add `finance_export_results` tool for downloading tool output.

### 8.5 P2: Multi-Account Filter
**Impact:** Better routing | **Effort:** Medium  
Route queries based on account context from conversation.

### 8.6 P3: Streaming Response
**Impact:** Faster first response | **Effort:** Medium  
Stream final answer tokens instead of waiting for complete tool loop.

---

## 9. File Reference Map
| File | Lines | Primary Responsibility |
|------|-------|----------------------|
| `finance/query_planner.py` | ~400 | First-step tool selection + grammar integration |
| `finance/query_reflector.py` | 218 | Iterative continuation decisions + grammar integration |
| `finance/chat.py` | 879 | Main chat engine + tool loop |
| `finance/tools.py` | ~700 | 12 finance tool implementations |
| `finance/grammar_compiler.py` | ~200 | GBNF grammar compilation for planner + reflector |
| `finance/cache.py` | ~150 | LRU query cache with TTL eviction |
| `finance/db_schema.py` | ~100 | SQLite schema definitions |
| `finance/tab.py` | ~500 | Streamlit UI tab implementation |
| `llm_structured_wrapper.py` | ~200 | Grammar-aware structured output wrapper |

---

## 10. Quick Reference

### Tool Selection Flow
```
User Question
    -> Query Planner (grammar-constrained)
        -> FinanceQueryPlan {tool, confidence, rationale, arguments}
            -> Tool Execution
                -> Query Reflector (grammar-constrained)
                    -> FinanceContinuationDecision {action, confidence, rationale, continuation_args}
                        -> done: Generate Final Answer
                        -> retry_*: Continue Tool Loop
```

### Grammar Support Matrix
| Component | Grammar Enabled | Fallback |
|-----------|----------------|----------|
| Query Planner | GBNF via grammar_compiler.py | Pydantic validation |
| Query Reflector | GBNF via grammar_compiler.py | Pydantic validation |
| Final Answer | No (free text) | N/A |

### Language Notes
- Query planner prompt: English
- Query reflector prompt: German
- Tool docstrings: Mixed (English/German)
- Final answers: German (user-facing)