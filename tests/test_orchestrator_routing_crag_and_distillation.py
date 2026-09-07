from types import SimpleNamespace

from agent.agent_types import ToolCall
from agent.orchestrator import AgentOrchestrator, RetrievalRoute, RetrievalRoutingDecision
import agent.evidence_manager as evidence_module
from agent.evidence_manager import EvidenceManager


def _mk_orchestrator_stub() -> AgentOrchestrator:
    # Build a lightweight instance without running full __init__.
    obj = AgentOrchestrator.__new__(AgentOrchestrator)
    obj.retrieval_router_enabled = True
    obj.local_only_mode = False
    obj.semantic_live_routing_enabled = True
    obj.rag_enabled = True
    obj.adaptive_strategy = False
    obj.rag_k = 4
    obj.web_search_k = 3
    obj._last_retrieval_route = RetrievalRoute.RAG_REQUIRED
    obj.crag_self_correction_enabled = True
    obj.crag_max_retries = 2
    obj.crag_grounding_threshold = 0.35
    return obj


def test_apply_retrieval_route_internal_only_filters_retrieval_calls() -> None:
    orch = _mk_orchestrator_stub()
    planned = [
        ToolCall(tool="web_search", parameters={"query": "x"}),
        ToolCall(tool="rag_search", parameters={"query": "x", "k": 3}),
        ToolCall(tool="calculator", parameters={"expr": "1+1"}),
    ]
    decision = RetrievalRoutingDecision(
        route=RetrievalRoute.INTERNAL_ONLY,
        reason="test",
        confidence=1.0,
        focused_query="x",
    )

    out = orch._apply_retrieval_route(planned, decision)

    assert [c.tool for c in out] == ["calculator"]
    assert orch._last_retrieval_route == RetrievalRoute.INTERNAL_ONLY


def test_apply_retrieval_route_rag_required_injects_rag_and_blocks_web() -> None:
    orch = _mk_orchestrator_stub()
    planned = [ToolCall(tool="web_search", parameters={"query": "x"})]
    decision = RetrievalRoutingDecision(
        route=RetrievalRoute.RAG_REQUIRED,
        reason="test",
        confidence=1.0,
        focused_query="x",
    )

    out = orch._apply_retrieval_route(planned, decision)

    tools = [c.tool for c in out]
    assert "web_search" not in tools
    assert tools.count("rag_search") == 1
    rag_call = next(c for c in out if c.tool == "rag_search")
    assert rag_call.parameters["query"] == "x"
    assert rag_call.parameters["k"] == orch.rag_k


def test_run_crag_self_correction_skips_for_internal_only_route() -> None:
    orch = _mk_orchestrator_stub()
    orch._decide_retrieval_route = lambda query: RetrievalRoutingDecision(  # type: ignore[assignment]
        route=RetrievalRoute.INTERNAL_ONLY,
        reason="smalltalk",
        confidence=0.9,
        focused_query=query,
    )

    current_verification = SimpleNamespace(grounding_score=0.05)
    result = orch._run_crag_self_correction(
        query="hi",
        history=[],
        extras=[],
        current_sources=[],
        current_results=[],
        current_verification_result=current_verification,
        fallback=False,
    )

    assert result is None


def test_run_crag_self_correction_retries_and_returns_improved_result() -> None:
    orch = _mk_orchestrator_stub()
    calls_seen = []

    orch._decide_retrieval_route = lambda query: RetrievalRoutingDecision(  # type: ignore[assignment]
        route=RetrievalRoute.WEB_REQUIRED,
        reason="needs_live",
        confidence=0.8,
        focused_query=query,
    )
    orch._generate_retry_query_from_verification = lambda query, verification_result, attempt: f"retry-{attempt}"  # type: ignore[assignment]

    class _Tools:
        def run(self, calls):
            calls_seen.append([c.tool for c in calls])
            return []

    orch.tools = _Tools()
    orch.evidence_max_candidates = 8
    orch.evidence_shortlist_m = 5
    orch.evidence_diversity_lambda = 0.7
    orch.news_min_k = 3
    orch.news_max_k = 4
    orch.use_llm_evidence_selection = False
    orch._is_news_query = lambda query: False  # type: ignore[assignment]
    orch.evidence_manager = SimpleNamespace(
        select_evidence_from_tool_results=lambda **kwargs: SimpleNamespace(
            sources=[SimpleNamespace(url="https://example.com", snippet="fact")]
        )
    )
    orch.summarize = lambda query, history, sources, extras, fallback: ("draft answer", None)  # type: ignore[assignment]
    orch.verify_step = lambda query, draft, evidence, fallback: (  # type: ignore[assignment]
        "improved answer",
        SimpleNamespace(grounding_score=0.8),
    )

    current_verification = SimpleNamespace(grounding_score=0.1)
    result = orch._run_crag_self_correction(
        query="latest release",
        history=[],
        extras=[],
        current_sources=[],
        current_results=[],
        current_verification_result=current_verification,
        fallback=False,
    )

    assert result is not None
    final_text, verification_result, updated_sources, updated_results = result
    assert final_text == "improved answer"
    assert verification_result.grounding_score >= 0.35
    assert len(updated_sources) == 1
    assert updated_results == []
    assert calls_seen, "CRAG retry should execute retrieval calls"
    assert "rag_search" in calls_seen[0]
    assert "web_search" in calls_seen[0]


def test_distill_web_evidence_schema_validation_retry_and_url_mapping(monkeypatch) -> None:
    manager = EvidenceManager(evidence_processor=None, source_manager=None, tools_manager=None)

    class _Validated:
        def __init__(self):
            self.facts = [
                SimpleNamespace(source_id="S1", fact="Alpha fact", confidence=0.9),
                SimpleNamespace(source_id="S2", fact="Beta fact", confidence=0.8),
            ]

    class _SchemaModel:
        calls = 0

        @classmethod
        def model_validate_json(cls, payload: str):
            cls.calls += 1
            if cls.calls == 1:
                raise ValueError("schema mismatch")
            return _Validated()

    monkeypatch.setattr(evidence_module, "DistilledFactBatchModel", _SchemaModel)

    responses = [
        '{"facts": [{"bad": true}]}',
        '{"facts": [{"fact": "Alpha fact", "source_id": "S1", "confidence": 0.9}, {"fact": "Beta fact", "source_id": "S2", "confidence": 0.8}]}'
    ]

    class _ModelLoader:
        def __init__(self):
            self.calls = 0

        def generate_response(self, **kwargs):
            out = responses[self.calls]
            self.calls += 1
            return out

    ml = _ModelLoader()
    sources = [
        SimpleNamespace(url="https://a.example", title="A", snippet="a snippet", score=0.9),
        SimpleNamespace(url="https://b.example", title="B", snippet="b snippet", score=0.8),
    ]

    distilled = manager.distill_web_evidence(
        sources=sources,
        query="test query",
        model_loader=ml,
        batch_size=2,
        top_k_web_sources=2,
        max_regen_attempts=1,
    )

    assert ml.calls == 2, "invalid schema should trigger one regeneration retry"
    assert distilled["https://a.example"] == "Alpha fact"
    assert distilled["https://b.example"] == "Beta fact"
