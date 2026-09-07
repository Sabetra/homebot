# Finance Module Optimization Roadmap

**Generated:** 2026-06-22
**Last Updated:** 2026-06-22 17:09 CET
**Status:** P0 Complete — Grammar-Constrained Decoding implemented and verified
**Based On:** Comprehensive analysis of query_planner.py, query_reflector.py, chat.py, tools.py, cache.py, db_schema.py

---

## 1. Priority Matrix

| Priority | Optimization | User Impact | Dev Effort | Risk | Status |
|----------|-------------|-------------|------------|------|--------|
| **P0** | Grammar-Constrained Decoding | ++ High | + Medium | Low | **COMPLETE** |
| **P0** | Unit Tests for Query Planner | High robustness | + Medium | None | Planned |
| **P1** | Caching Layer for Aggregations | Med (DB load) | - Low | Low | Planned |
| **P1** | Export Function (CSV/Excel) | + User value | - Low | None | Planned |
| **P2** | Multi-Account Filter Routing | Med (context) | ++ Medium | Med | Backlog |
| **P2** | Diagnostic Dead Code Cleanup | Internal | - Low | Low | Backlog |
| **P3** | Streaming Final Response | + UX speed | ++ Medium | Med | Backlog |

---

## 2. P0: Grammar-Constrained Decoding

### 2.1 Problem Statement
Current structured output in `LLMStructuredWrapper.generate_structured_safe()` uses a **generate-then-validate** pattern:
1. LLM generates free-text JSON
2. Pydantic validates against schema
3. On failure, retry with error feedback (up to `max_retries=3`)

**Issues:**
- Wasted tokens on invalid outputs
- Latency from retry cycles
- No guarantee of convergence (can still fail after 3 retries)
- Prompt drift risk: LLM can ignore schema instructions

### 2.2 Solution: Grammar-Constrained Decoding
Constrain LLM output at the token level using a BNF grammar derived from the Pydantic schema. This eliminates invalid outputs entirely.

### 2.3 Approach Options

| Library | Pros | Cons | Recommendation |
|---------|------|------|----------------|
| **Outlines** | Mature, Pydantic-native, supports multiple backends | Requires compatible LLM backend | **Primary choice** |
| **Guidance** | LLM.dev backed, hologram profiles | Steeper learning curve | Alternative |
| **LMQL** | Query-language constraints, very flexible | Research-oriented, less production-ready | Future option |
| **XGrammar** | Cross-framework compatible | Younger ecosystem | Monitor |

### 2.4 Implementation Plan

**Phase 1: Schema-to-Grammar Compilation**
```python
# New module: finance/grammar_compiler.py
from outlines import grammars
from pydantic import BaseModel

def compile_finance_plan_grammar() -> str:
    """Compile FinanceQueryPlan schema to EBNF grammar."""
    # Map Literal[tool_names] -> grammar alternatives
    # Map float fields -> numeric grammar
    # Map string fields -> string grammar with length constraints
    pass

def compile_reflector_grammar() -> str:
    """Compile FinanceContinuationDecision schema to EBNF grammar."""
    pass
```

**Phase 2: Integration with LLMStructuredWrapper**
```python
# Modify: llm_structured_wrapper.py
class LLMStructuredWrapper:
    def generate_structured_grammar_constrained(
        self,
        schema: Type[BaseModel],
        grammar: str,
        messages: List[Dict],
        **kwargs
    ) -> BaseModel:
        """Generate with grammar constraints, zero retries needed."""
        pass
```

**Phase 3: Query Planner Integration**
```python
# Modify: finance/query_planner.py
def build_finance_plan_with_grammar(
    question: str,
    llm: LLMStructuredWrapper,
    grammar: str,
    context: Dict,
) -> FinanceQueryPlan:
    pass
```

### 2.5 Expected Benefits
- **Zero validation failures**: Grammar guarantees valid output
- **Reduced latency**: No retry cycles
- **Reduced token waste**: No invalid JSON attempts
- **Higher reliability**: Deterministic schema compliance

### 2.6 Risks & Mitigations
| Risk | Mitigation |
|------|-----------|
| Backend doesn't support grammar constraints | Fallback to generate-then-validate |
| Grammar compilation bugs | Unit tests for grammar compiler |
| Increased memory usage | Profile and benchmark |

---

## 3. P0: Unit Tests for Query Planner

### 3.1 Current State
**No unit tests exist** for `finance/query_planner.py`. The module is tested implicitly through the full chat flow, but not in isolation.

### 3.2 Test Coverage Targets

```
tests/test_finance_query_planner.py
├── test_planner_with_mock_llm[12 tool selections]
│   ├── test_budget_question -> finance_budget_status
│   ├── test_forecast_question -> finance_expense_forecast
│   ├── test_anomaly_question -> finance_expense_anomaly
│   ├── test_savings_question -> finance_savings_potential
│   ├── test_trend_question -> finance_expense_trend_break
│   ├── test_cost_structure_question -> finance_cost_structure
│   ├── test_recurring_question -> finance_recurring_expense
│   ├── test_counterparty_question -> finance_counterparty_costs
│   ├── test_category_question -> finance_category_costs
│   ├── test_search_question -> finance_search_bookings
│   ├── test_sql_question -> finance_sql_query
│   └── test_budget_vs_actual_question -> finance_budget_vs_actual
├── test_fallback_logic
│   ├── test_regex_inference_on_json_parse_failure
│   ├── test_previous_trace_inference
│   └── test_default_to_search_bookings
├── test_argument_defaults
│   ├── test_search_defaults_applied
│   ├── test_sql_defaults_applied
│   ├── test_date_defaults_applied[9 aggregation tools]
├── test_prompt_building
│   ├── test_tool_definitions_rendered
│   ├── test_schema_context_rendered
│   └── test_conversation_context_included
└── test_edge_cases
    ├── test_empty_question
    ├── test_very_long_question
    └── test_non_finance_question
```

### 3.3 Mock Strategy
```python
from unittest.mock import MagicMock, patch
import pytest

class MockLLMStructuredWrapper:
    """Returns predetermined FinanceQueryPlan instances."""
    def generate_structured_safe(self, schema, messages, **kwargs):
        return self._return_value

class MockToolkit:
    """No-op tool execution for isolation testing."""
    def execute_tool(self, name, args):
        return {"success": True, "tool": name, "args": args}
```

### 3.4 Expected Benefits
- **Regression safety**: Catch breaking changes before deployment
- **Confidence in refactoring**: Safe to restructure with test coverage
- **Documentation**: Tests serve as executable specification

---

## 4. P1: Caching Layer for Aggregations

### 4.1 Current State
`FinanceQueryCache` exists in `finance/cache.py` with:
- LRU eviction (max 512 entries)
- TTL-based stale eviction (default 600s)
- Async-safe with asyncio locks
- Hit/miss/eviction metrics

**Problem:** Currently only caches raw query results, not aggregation results. Budget/forecast queries hit the database on every call.

### 4.2 Extension Plan

```python
# Modify: finance/cache.py
class FinanceAggregationCache:
    """Specialized cache for aggregation tool results."""
    
    def cache_budget_status(self, month: str, result: dict):
        """Cache monthly budget status (changes infrequently)."""
        pass
    
    def cache_expense_forecast(self, history_months: int, result: dict):
        """Cache forecast results (computationally expensive)."""
        pass
    
    def cache_cost_structure(self, start_date: str, end_date: str, result: dict):
        """Cache cost structure analysis."""
        pass
```

### 4.3 Cache Key Strategy
- **Budget queries**: Key by `(month, account_id)` → TTL 1 hour
- **Forecast queries**: Key by `(history_months, account_id)` → TTL 30 min
- **Cost structure**: Key by `(start_date, end_date, account_id)` → TTL 1 hour
- **Search queries**: Existing cache handles these

### 4.4 Expected Benefits
- **Reduced DB load**: Aggregation queries are expensive (multiple JOINs)
- **Faster responses**: Cache hits return in <1ms
- **Scalability**: Supports more concurrent users

---

## 5. P1: Export Function (CSV/Excel)

### 5.1 Problem Statement
Users cannot download tool results. All data stays in the chat interface.

### 5.2 Solution: `finance_export_results` Tool

```python
# New tool in finance/tools.py
def finance_export_results(
    query_text: str,
    format: Literal["csv", "xlsx"] = "csv",
    include_headers: bool = True,
    max_rows: int = 10000,
) -> dict:
    """Export tool results to CSV or Excel file."""
    pass
```

### 5.3 Implementation Details
- Use `pandas` for data manipulation
- Use `openpyxl` for Excel export
- Return file path + download link
- Integrate with Streamlit `st.download_button` in `tab.py`

### 5.4 Expected Benefits
- **User empowerment**: Download data for external analysis
- **Audit trail**: Exportable records for compliance
- **Integration**: CSV/Excel compatible with other tools

---

## 6. P2: Multi-Account Filter Routing

### 6.1 Problem Statement
Current query planner doesn't consider account context. All queries run against all accounts.

### 6.2 Solution
Add account detection in prompt:
1. Extract account mentions from user question (e.g., "for my checking account")
2. Map account names to account IDs via schema context
3. Inject account filter into tool arguments

### 6.3 Implementation
```python
# Modify: finance/query_planner.py
def _detect_account_context(question: str, schema_context: dict) -> Optional[str]:
    """Detect account filter from user question."""
    pass

def _inject_account_filter(args: dict, account_id: str) -> dict:
    """Add account_id filter to tool arguments."""
    pass
```

---

## 7. P2: Diagnostic Dead Code Cleanup

### 7.1 Current State
`diagnostics/` directory contains multiple test files:
- `test_finance_write.py`
- `test_finance_db_write.py`
- `test_db_access.py`
- `check_db_files.py`

Many of these are one-off debugging scripts, not reusable tests.

### 7.2 Cleanup Plan
1. Move valuable tests to `tests/test_finance_*.py`
2. Archive one-off scripts to `diagnostics/archive/`
3. Delete obsolete files
4. Update `diagnostics/README.md`

---

## 8. P3: Streaming Final Response

### 8.1 Problem Statement
Current implementation waits for complete tool loop before returning any response. For complex queries with multiple tool rounds, users wait 5-15 seconds.

### 8.2 Solution
Stream final answer tokens while tool loop is still executing:
1. Show tool execution progress in real-time
2. Stream final synthesis tokens as they generate
3. Provide intermediate results after each tool round

### 8.3 Implementation
```python
# Modify: finance/chat.py
async def _run_tool_loop_streaming(
    self,
    question: str,
    history: List[Dict],
) -> AsyncGenerator[str, None]:
    """Stream tool execution + final answer."""
    pass
```

---

## 9. Implementation Timeline

| Week | Priority | Deliverables |
|------|----------|-------------|
| **Week 1** | P0 | Grammar compiler module, unit tests for query planner |
| **Week 2** | P0 | Grammar-constrained decoding integration, full test suite |
| **Week 3** | P1 | Aggregation caching, export tool |
| **Week 4** | P2 | Multi-account routing, diagnostic cleanup |
| **Week 5** | P3 | Streaming responses, documentation updates |

---

## 10. Monitoring & Validation

### 10.1 Metrics to Track
- **Validation failure rate**: Should drop to 0% with grammar constraints
- **Average response latency**: Target <3s for simple queries
- **Cache hit rate**: Target >60% for aggregation queries
- **Unit test coverage**: Target >80% for query_planner, query_reflector

### 10.2 Regression Guards
- All new code must have unit tests
- Integration tests for full chat flow
- Performance benchmarks for tool loop latency
- Prompt drift detection via hash comparison

---

## 11. References

- **Outlines**: https://outlines-dev.github.io/outlines/
- **Guidance**: https://github.com/guidance-ai/guidance
- **LMQL**: https://lmql.ai/
- **XGrammar**: https://github.com/mlc-ai/xgrammar
- **Pydantic v2**: https://docs.pydantic.dev/latest/