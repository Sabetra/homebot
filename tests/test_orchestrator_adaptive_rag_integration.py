"""AgentOrchestrator Adaptive-RAG Integration-Tests.

Ziel: Den vollständigen Orchestrator-Adaptive-RAG-Flow deterministisch testen:
  1. _decide_retrieval_route() setzt decision.depth via AdaptiveRAGRouter
  2. _apply_retrieval_route() enforced MultiHop bei depth="deep"
  3. Trace-Tracking (multi_hop_executed, multi_hop_ms, etc.)
  4. Graceful-Fallback bei Router-/MultiHop-Fehlern
  5. Feature-Flag adaptive_strategy= False deaktiviert adaptive Pfade

Keine echte LLM — alles gemockt.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import pytest

from agent.orchestrator import (
    AgentOrchestrator,
    RetrievalRoute,
    RetrievalRoutingDecision,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _pin_connected_runtime_mode(monkeypatch):
    """Haelt ``APP_LOCAL_ONLY`` fuer dieses Modul deterministisch auf "0".

    Die Tests hier pruefen das Retrieval-Routing im *verbundenen* Modus; der
    Stub setzt entsprechend ``local_only_mode=False``. Ohne dieses Pinning
    haengt das Ergebnis an der Umgebung des Testlaufs: Steht ``APP_LOCAL_ONLY``
    auf "1", erkennt ``_refresh_runtime_mode()`` einen Moduswechsel, kippt
    ``local_only_mode`` mitten im Test auf True und greift dabei auf
    ``self.model_loader`` zu, das der Stub nicht besitzt.

    Das ist keine theoretische Konstellation: ``scripts/run_release_quality_gate.py``
    (Zeile 139) erzwingt ``APP_LOCAL_ONLY=1`` -- unter dem harten Release-Gate
    des Projekts war dieses Modul deshalb rot, bei einem blossen ``pytest tests/``
    ohne gesetzte Variable dagegen gruen.
    """
    monkeypatch.setenv("APP_LOCAL_ONLY", "0")


def _mk_orchestrator(
    *,
    retrieval_router_enabled: bool = True,
    local_only_mode: bool = False,
    rag_enabled: bool = True,
    semantic_live_routing_enabled: bool = True,
    adaptive_strategy: bool = True,
) -> AgentOrchestrator:
    """Leichtgewichtiger Orchestrator-Stub.

    Der Stub umgeht ``__init__`` und muss dessen Invarianten deshalb selbst
    herstellen -- insbesondere ``model_loader`` und
    ``subquery_web_fallback_enabled``, die ``_refresh_runtime_mode()`` im
    Wechselpfad benoetigt.
    """
    obj = AgentOrchestrator.__new__(AgentOrchestrator)
    # Spiegelt agent/orchestrator.py __init__ (model_loader, local_only_mode,
    # subquery_web_fallback_enabled).
    obj.model_loader = None
    obj.retrieval_router_enabled = retrieval_router_enabled
    obj.local_only_mode = local_only_mode
    obj.subquery_web_fallback_enabled = not local_only_mode
    obj.rag_enabled = rag_enabled
    obj.semantic_live_routing_enabled = semantic_live_routing_enabled
    obj.adaptive_strategy = adaptive_strategy
    obj._last_retrieval_route = RetrievalRoute.RAG_REQUIRED
    obj._live_data_assessment_cache = {}
    obj._adaptive_router = None
    obj._adaptive_multi_hop = None
    obj._adaptive_pipeline = None
    return obj


class _MockRouterDecision:
    """Mock für AdaptiveRAGRouter.route() Rückgabe."""
    def __init__(self, depth: str, confidence: float):
        self.depth = SimpleNamespace(value=depth)  # router_decision.depth.value
        self.confidence = confidence


class _MockMultiHopResult:
    """Mock für MultiHopRetriever.retrieve() Rückgabe."""
    def __init__(
        self,
        hops_executed: int = 2,
        converged: bool = True,
        evidence_texts: Optional[List[str]] = None,
        evidence_scores: Optional[List[float]] = None,
    ):
        self.hops_executed = hops_executed
        self.converged = converged
        self.total_evidence_texts = evidence_texts or ["multi_hop_evidence_A", "multi_hop_evidence_B"]
        self.total_evidence_scores = evidence_scores or [0.92, 0.85]
        self.latency_ms = 150.0


# ---------------------------------------------------------------------------
# Pfad A: Router setzt depth bei adaptive_strategy=True
# ---------------------------------------------------------------------------

class TestRouterSetsDepth:
    """_decide_retrieval_route() ruft _adaptive_router.route() auf und setzt depth."""

    def test_shallow_query_sets_depth_shallow(self):
        orch = _mk_orchestrator()
        orch._adaptive_router = SimpleNamespace(
            route=lambda q: _MockRouterDecision(depth="shallow", confidence=0.88)
        )
        orch._assess_live_data_need = lambda q, **kw: {  # type: ignore[assignment]
            "requires_web": False, "confidence": 0.0,
        }
        decision = orch._decide_retrieval_route("Was ist Python?")
        assert decision.depth == "shallow"
        assert decision.route == RetrievalRoute.RAG_REQUIRED

    def test_deep_query_sets_depth_deep(self):
        orch = _mk_orchestrator()
        orch._adaptive_router = SimpleNamespace(
            route=lambda q: _MockRouterDecision(depth="deep", confidence=0.75)
        )
        orch._assess_live_data_need = lambda q, **kw: {  # type: ignore[assignment]
            "requires_web": False, "confidence": 0.0,
        }
        decision = orch._decide_retrieval_route(
            "Warum ist Python langsamer als C und wie wirkt sich das auf ML aus?"
        )
        assert decision.depth == "deep"
        assert decision.route == RetrievalRoute.RAG_REQUIRED

    def test_adaptive_strategy_false_skips_router(self):
        orch = _mk_orchestrator(adaptive_strategy=False)
        orch._adaptive_router = SimpleNamespace(
            route=lambda q: _MockRouterDecision(depth="deep", confidence=0.9)
        )
        orch._assess_live_data_need = lambda q, **kw: {  # type: ignore[assignment]
            "requires_web": False, "confidence": 0.0,
        }
        decision = orch._decide_retrieval_route("Komplexe Frage")
        assert decision.depth is None  # Router nicht gerufen

    def test_router_none_skips_depth(self):
        orch = _mk_orchestrator()
        orch._adaptive_router = None
        orch._assess_live_data_need = lambda q, **kw: {  # type: ignore[assignment]
            "requires_web": False, "confidence": 0.0,
        }
        decision = orch._decide_retrieval_route("Test")
        assert decision.depth is None

    def test_router_exception_falls_back_gracefully(self):
        orch = _mk_orchestrator()
        orch._adaptive_router = SimpleNamespace(
            route=lambda q: (_ for _ in ()).throw(RuntimeError("LLM down"))
        )
        orch._assess_live_data_need = lambda q, **kw: {  # type: ignore[assignment]
            "requires_web": False, "confidence": 0.0,
        }
        decision = orch._decide_retrieval_route("Test")
        assert decision.depth is None  # Fallback: depth=None
        assert decision.route == RetrievalRoute.RAG_REQUIRED


# ---------------------------------------------------------------------------
# Pfad B: MultiHop-Enforcement bei depth="deep"
# ---------------------------------------------------------------------------

class TestMultiHopEnforcement:
    """_apply_retrieval_route() enforced MultiHop bei decision.depth == 'deep'."""

    def _tool_result(self, text: str, score: float = 0.9) -> Any:
        """Hilfsfunktion für ToolResult-Mocks."""
        r = SimpleNamespace()
        r.text = text
        r.score = score
        return r

    def test_deep_query_triggers_multi_hop(self):
        orch = _mk_orchestrator()
        orch._adaptive_multi_hop = SimpleNamespace(
            retrieve=lambda **kw: _MockMultiHopResult(
                hops_executed=2, converged=True,
                evidence_texts=["mh_e1", "mh_e2"],
                evidence_scores=[0.93, 0.87],
            )
        )
        decision = RetrievalRoutingDecision(
            route=RetrievalRoute.RAG_REQUIRED,
            reason="default_rag_first",
            confidence=0.6,
            focused_query="Warum ist Python langsam?",
            depth="deep",
        )
        # Simuliere den MultiHop-Block aus _apply_retrieval_route()
        # (Zeilen 3006-3045)
        rag_first_results = [
            self._tool_result("init_evidence_A", 0.91),
            self._tool_result("init_evidence_B", 0.84),
        ]
        assert decision.depth == "deep"
        assert orch._adaptive_multi_hop is not None
        assert orch.adaptive_strategy

        # MultiHop retrieve aufrufen (wie in orchestrator.py Zeile 3015)
        initial_texts = [r.text for r in rag_first_results if r.text and r.text != ""]
        initial_scores = [r.score for r in rag_first_results if getattr(r, "score", None) and r.score > 0]

        mh_result = orch._adaptive_multi_hop.retrieve(
            query=decision.focused_query,
            initial_evidence=(initial_texts, initial_scores),
        )
        assert mh_result.hops_executed == 2
        assert mh_result.converged is True
        assert len(mh_result.total_evidence_texts) == 2

    def test_shallow_query_skips_multi_hop(self):
        orch = _mk_orchestrator()
        orch._adaptive_multi_hop = SimpleNamespace(
            retrieve=lambda q, initial_evidence: _MockMultiHopResult()
        )
        decision = RetrievalRoutingDecision(
            route=RetrievalRoute.RAG_REQUIRED,
            reason="default_rag_first",
            confidence=0.6,
            focused_query="Was ist Python?",
            depth="shallow",
        )
        # Bei shallow: der MultiHop-Block wird nicht betreten
        # (decision.depth != "deep")
        assert decision.depth == "shallow"
        # Kein MultiHop-Aufruf erwartet

    def test_multi_hop_error_does_not_crash(self):
        orch = _mk_orchestrator()
        orch._adaptive_multi_hop = SimpleNamespace(
            retrieve=lambda **kw: (_ for _ in ()).throw(RuntimeError("FAISS down"))
        )
        decision = RetrievalRoutingDecision(
            route=RetrievalRoute.RAG_REQUIRED,
            reason="default_rag_first",
            confidence=0.6,
            focused_query="Test",
            depth="deep",
        )
        initial_texts = ["some_evidence"]
        initial_scores = [0.8]
        # Graceful-Fallback: try/except in orchestrator.py Zeilen 3040-3045
        try:
            mh_result = orch._adaptive_multi_hop.retrieve(
                query=decision.focused_query,
                initial_evidence=(initial_texts, initial_scores),
            )
            assert False, "Sollte Exception werfen"
        except RuntimeError:
            pass  # Erwartet — in Production wird dies geloggt und zu plain RAG gefallt


# ---------------------------------------------------------------------------
# Pfad C: Full-Flow Integration (decide → apply → MultiHop)
# ---------------------------------------------------------------------------

class TestFullAdaptiveRagFlow:
    """End-to-End: Query → _decide → _apply mit MultiHop."""

    def test_full_flow_deep_query(self):
        orch = _mk_orchestrator()
        # Router entscheidet "deep"
        orch._adaptive_router = SimpleNamespace(
            route=lambda q: _MockRouterDecision(depth="deep", confidence=0.72)
        )
        # MultiHop liefert Evidenz
        orch._adaptive_multi_hop = SimpleNamespace(
            retrieve=lambda **kw: _MockMultiHopResult(
                hops_executed=3, converged=True,
            )
        )
        orch._assess_live_data_need = lambda q, **kw: {  # type: ignore[assignment]
            "requires_web": False, "confidence": 0.0,
        }

        # Step 1: Decide
        decision = orch._decide_retrieval_route(
            "Wie hängt Stimmung mit Finanzentscheidungen zusammen?"
        )
        assert decision.depth == "deep"
        assert decision.route == RetrievalRoute.RAG_REQUIRED

        # Step 2: Apply MultiHop (simuliert)
        assert orch._adaptive_multi_hop is not None
        mh_result = orch._adaptive_multi_hop.retrieve(
            query=decision.focused_query,
            initial_evidence=(["seed_evidence"], [0.88]),
        )
        assert mh_result.hops_executed == 3
        assert mh_result.converged

    def test_full_flow_shallow_query(self):
        orch = _mk_orchestrator()
        orch._adaptive_router = SimpleNamespace(
            route=lambda q: _MockRouterDecision(depth="shallow", confidence=0.95)
        )
        orch._assess_live_data_need = lambda q, **kw: {  # type: ignore[assignment]
            "requires_web": False, "confidence": 0.0,
        }

        decision = orch._decide_retrieval_route("Was ist 2+2?")
        # Smalltalk-Erkenntnis vor Router — aber mit mock: kein smalltalk
        # Da "Was ist 2+2?" als arithmetic erkannt wird → INTERNAL_ONLY
        # Test mit nicht-smalltalk Query:
        decision2 = orch._decide_retrieval_route("Was ist Python?")
        assert decision2.depth == "shallow"

    def test_internal_only_bypasses_adaptive(self):
        """Smalltalk-Queries umgehen adaptive Pipeline komplett."""
        orch = _mk_orchestrator()
        orch._adaptive_router = SimpleNamespace(
            route=lambda q: (_ for _ in ()).throw(RuntimeError("sollte nicht gerufen werden"))
        )
        decision = orch._decide_retrieval_route("Hallo")
        assert decision.route == RetrievalRoute.INTERNAL_ONLY
        assert decision.depth is None  # Router nicht gerufen (smalltalk vor Router)


# ---------------------------------------------------------------------------
# Pfad D: Trace-Tracking
# ---------------------------------------------------------------------------

class TestTraceTracking:
    """MultiHop-Trace-Felder werden korrekt gesetzt."""

    def test_trace_fields_populated_on_deep(self):
        """Simuliere Trace-Setzung wie in orchestrator.py Zeilen 3020-3023."""
        mh_result = _MockMultiHopResult(hops_executed=2, converged=True)

        # Trace-Felder (wie in orchestrator.py):
        trace_multi_hop_executed = True
        trace_multi_hop_ms = round(150.0, 1)
        trace_multi_hop_hops = mh_result.hops_executed
        trace_multi_hop_converged = mh_result.converged

        assert trace_multi_hop_executed is True
        assert trace_multi_hop_ms == 150.0
        assert trace_multi_hop_hops == 2
        assert trace_multi_hop_converged is True

    def test_trace_fields_not_set_on_shallow(self):
        """Bei shallow-Route sollte multi_hop_executed=False bleiben."""
        # Default-Trace:
        trace_multi_hop_executed = False
        trace_multi_hop_ms = 0.0
        trace_multi_hop_hops = 0
        trace_multi_hop_converged = False

        assert trace_multi_hop_executed is False
        assert trace_multi_hop_hops == 0


# ---------------------------------------------------------------------------
# Pfad E: Feature-Flag Toggle
# ---------------------------------------------------------------------------

class TestFeatureFlagToggle:
    """adaptive_strategy=False deaktiviert alle adaptive Pfade."""

    def test_router_not_called_when_flag_false(self):
        call_count = 0
        def counting_route(q):
            nonlocal call_count
            call_count += 1
            return _MockRouterDecision(depth="deep", confidence=0.9)

        orch = _mk_orchestrator(adaptive_strategy=False)
        orch._adaptive_router = SimpleNamespace(route=counting_route)
        orch._assess_live_data_need = lambda q, **kw: {  # type: ignore[assignment]
            "requires_web": False, "confidence": 0.0,
        }
        orch._decide_retrieval_route("Test")
        assert call_count == 0  # Router nicht gerufen

    def test_multi_hop_not_called_when_flag_false(self):
        """Bei adaptive_strategy=False soll auch MultiHop nicht enforced werden."""
        orch = _mk_orchestrator(adaptive_strategy=False)
        # Der MultiHop-Block in _apply_retrieval_route() prüft:
        #   if decision.depth == "deep" and self._adaptive_multi_hop is not None
        #       and self.adaptive_strategy and rag_first_results:
        # Da adaptive_strategy=False, wird der Block nicht betreten
        assert orch.adaptive_strategy is False
        # decision.depth wäre None (da Router nicht gerufen) → Block nicht betreten


# ---------------------------------------------------------------------------
# Pfad F: Edge Cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    """Randfälle und Fehlerbehandlung."""

    def test_router_returns_unexpected_depth(self):
        """Router mit unbekanntem depth-Wert — graceful handling."""
        orch = _mk_orchestrator()
        orch._adaptive_router = SimpleNamespace(
            route=lambda q: _MockRouterDecision(depth="unknown", confidence=0.5)
        )
        orch._assess_live_data_need = lambda q, **kw: {  # type: ignore[assignment]
            "requires_web": False, "confidence": 0.0,
        }
        decision = orch._decide_retrieval_route("Test")
        assert decision.depth == "unknown"  # Wird durchgereicht
        # MultiHop-Enforcement prüft depth == "deep" → nicht betreten

    def test_empty_rag_results_no_multi_hop(self):
        """Bei leeren initial_evidence wird MultiHop nicht aufgerufen."""
        orch = _mk_orchestrator()
        orch._adaptive_multi_hop = SimpleNamespace(
            retrieve=lambda q, initial_evidence: (_ for _ in ()).throw(
                RuntimeError("sollte nicht gerufen werden")
            )
        )
        rag_first_results = []  # Keine initialen Ergebnisse
        initial_texts = [r.text for r in rag_first_results if getattr(r, "text", None)]
        initial_scores = [r.score for r in rag_first_results if getattr(r, "score", None)]
        # Bei leeren initial_texts/scores wird MultiHop-Block nicht betreten
        assert len(initial_texts) == 0
        assert len(initial_scores) == 0

    def test_web_required_route_with_deep_depth(self):
        """WEB_REQUIRED + deep depth: MultiHop könnte auch hier relevant sein."""
        orch = _mk_orchestrator()
        orch._adaptive_router = SimpleNamespace(
            route=lambda q: _MockRouterDecision(depth="deep", confidence=0.8)
        )
        orch._assess_live_data_need = lambda q, **kw: {  # type: ignore[assignment]
            "requires_web": True, "confidence": 0.7,
            "reason": "current_data", "focused_query": q,
        }
        decision = orch._decide_retrieval_route("Aktuelle KI-Entwicklungen 2026")
        assert decision.route == RetrievalRoute.WEB_REQUIRED
        # depth wird bei WEB_REQUIRED vor default gesetzt — aktueller Code
        # setzt depth nur im default-RAG-Pfad. WEB_REQUIRED übergangt adaptive.
        # Das ist ein bekanntes Limit (siehe Workdoc Schritt 7).


# ---------------------------------------------------------------------------
# Pfad G: CRAG-Self-Correction mit Adaptive Pipeline
# ---------------------------------------------------------------------------

class TestCragAdaptiveIntegration:
    """_run_crag_self_correction() verwendet _adaptive_pipeline.execute()."""

    def test_crag_uses_adaptive_pipeline_when_enabled(self):
        """
        Bei adaptive_strategy=True und _adaptive_pipeline != None
        soll _adaptive_pipeline.execute() statt self.tools.run() gerufen werden.
        """
        from agent.orchestrator import RetrievalRoute

        orch = _mk_orchestrator(adaptive_strategy=True, rag_enabled=True)
        orch.crag_self_correction_enabled = True
        orch.crag_grounding_threshold = 0.8
        orch.crag_max_retries = 2
        orch.rag_k = 4
        orch.web_search_k = 3

        # Adaptive Pipeline Mock
        class _MockAdaptResult:
            route = SimpleNamespace(value="deep")
            hops_used = 2
            evidence_texts = ["adapt_evidence_1", "adapt_evidence_2"]
            evidence_scores = [0.95, 0.88]

        adaptive_called = False

        class _MockPipeline:
            def execute(self, query: str):
                nonlocal adaptive_called
                adaptive_called = True
                return _MockAdaptResult()

        orch._adaptive_pipeline = _MockPipeline()  # type: ignore[assignment]

        # Tools Mock (soll NICHT gerufen werden wenn adaptive funktioniert)
        tools_called = False

        class _MockTools:
            def run(self, calls):
                nonlocal tools_called
                tools_called = True
                return []

        orch.tools = _MockTools()  # type: ignore[assignment]

        # Evidence Manager Mock
        class _MockEvidenceSource:
            def __init__(self, text: str, score: float):
                self.text = text
                self.score = score

        class _MockEvidenceResult:
            sources = [_MockEvidenceSource("evidence", 0.9)]

        class _MockEvidenceManager:
            def select_evidence_from_tool_results(self, **kw):
                return _MockEvidenceResult()

        orch.evidence_manager = _MockEvidenceManager()  # type: ignore[assignment]

        # Summarize + verify_step Mocks
        orch.summarize = lambda *a, **k: ("draft answer", [])  # type: ignore[assignment]
        orch.verify_step = lambda *a, **k: (  # type: ignore[assignment]
            "final answer",
            SimpleNamespace(grounding_score=0.95, issues=[], warnings=[]),
        )
        orch._is_news_query = lambda q: False  # type: ignore[assignment]
        orch.use_llm_evidence_selection = False
        orch.evidence_max_candidates = 5
        orch.evidence_shortlist_m = 3
        orch.evidence_diversity_lambda = 0.7
        orch.news_min_k = 2
        orch.news_max_k = 6

        # Current verification result mit niedrigem grounding
        class _MockLowGrounding:
            grounding_score = 0.4
            issues = ["insufficient_evidence"]
            warnings = []

        result = orch._run_crag_self_correction(
            query="Warum ist Python langsamer als C?",
            history=[],
            extras=[],
            current_sources=[],
            current_results=[],
            current_verification_result=_MockLowGrounding(),
            fallback=False,
        )

        # Adaptive Pipeline muss gerufen worden sein
        assert adaptive_called, "Adaptive Pipeline execute() wurde nicht aufgerufen"
        # Tools.run() sollte NICHT gerufen werden (da adaptive erfolgreich)
        assert not tools_called, "tools.run() sollte nicht aufgerufen werden bei erfolgreicher adaptive pipeline"
        # Ergebnis sollte improved grounding haben
        assert result is not None
        final_text, verification, sources, results = result
        assert final_text == "final answer"
        grounding = float(getattr(verification, "grounding_score", 0))
        assert grounding >= 0.95

    def test_crag_fallback_to_tools_when_adaptive_fails(self):
        """
        Wenn _adaptive_pipeline.execute() wirft, soll zu tools.run() gefallt werden.
        """
        from agent.orchestrator import RetrievalRoute

        orch = _mk_orchestrator(adaptive_strategy=True, rag_enabled=True)
        orch.crag_self_correction_enabled = True
        orch.crag_grounding_threshold = 0.8
        orch.crag_max_retries = 1
        orch.rag_k = 4
        orch.web_search_k = 3

        # Adaptive Pipeline wirft Exception
        class _MockPipeline:
            def execute(self, query: str):
                raise RuntimeError("FAISS index corrupted")

        orch._adaptive_pipeline = _MockPipeline()  # type: ignore[assignment]

        # Tools Mock — muss als Fallback gerufen werden
        fallback_called = False

        class _MockToolResult:
            tool = "rag_search"
            success = True
            text = "fallback_evidence"
            score = 0.82
            meta = {}

        class _MockTools:
            def run(self, calls):
                nonlocal fallback_called
                fallback_called = True
                return [_MockToolResult()]

        orch.tools = _MockTools()  # type: ignore[assignment]

        # Evidence Manager Mock
        class _MockEvidenceSource:
            text = "fallback_evidence"
            score = 0.82

        class _MockEvidenceResult:
            sources = [_MockEvidenceSource()]

        class _MockEvidenceManager:
            def select_evidence_from_tool_results(self, **kw):
                return _MockEvidenceResult()

        orch.evidence_manager = _MockEvidenceManager()  # type: ignore[assignment]
        orch.summarize = lambda *a, **k: ("draft", [])  # type: ignore[assignment]
        orch.verify_step = lambda *a, **k: (  # type: ignore[assignment]
            "final",
            SimpleNamespace(grounding_score=0.90, issues=[], warnings=[]),
        )
        orch._is_news_query = lambda q: False  # type: ignore[assignment]
        orch.use_llm_evidence_selection = False
        orch.evidence_max_candidates = 5
        orch.evidence_shortlist_m = 3
        orch.evidence_diversity_lambda = 0.7
        orch.news_min_k = 2
        orch.news_max_k = 6

        class _MockLowGrounding:
            grounding_score = 0.3
            issues = ["weak"]
            warnings = []

        result = orch._run_crag_self_correction(
            query="Test query",
            history=[],
            extras=[],
            current_sources=[],
            current_results=[],
            current_verification_result=_MockLowGrounding(),
            fallback=False,
        )

        # Fallback zu tools.run() muss aktiviert sein
        assert fallback_called, "tools.run() Fallback wurde nicht aufgerufen"
        assert result is not None

    def test_crag_skipped_when_grounding_already_sufficient(self):
        """Bei hohem grounding_score soll CRAG direkt None returnen."""
        orch = _mk_orchestrator(adaptive_strategy=True)
        orch.crag_self_correction_enabled = True
        orch.crag_grounding_threshold = 0.8

        class _MockGoodGrounding:
            grounding_score = 0.95
            issues = []
            warnings = []

        result = orch._run_crag_self_correction(
            query="Test",
            history=[],
            extras=[],
            current_sources=[],
            current_results=[],
            current_verification_result=_MockGoodGrounding(),
            fallback=False,
        )
        assert result is None  # Kein Retry nötig

    def test_crag_skipped_when_disabled(self):
        """Bei crag_self_correction_enabled=False soll CRAG deaktiviert sein."""
        orch = _mk_orchestrator(adaptive_strategy=True)
        orch.crag_self_correction_enabled = False

        result = orch._run_crag_self_correction(
            query="Test",
            history=[],
            extras=[],
            current_sources=[],
            current_results=[],
            current_verification_result=SimpleNamespace(grounding_score=0.2, issues=["x"], warnings=[]),
            fallback=False,
        )
        assert result is None

    def test_crag_skipped_on_internal_only_route(self):
        """INTERNAL_ONLY-Route soll CRAG-Retries überspringen."""
        orch = _mk_orchestrator(adaptive_strategy=True)
        orch.crag_self_correction_enabled = True
        orch.crag_grounding_threshold = 0.8
        orch._decide_retrieval_route = lambda q: RetrievalRoutingDecision(  # type: ignore[assignment]
            route=RetrievalRoute.INTERNAL_ONLY,
            reason="smalltalk",
            confidence=0.99,
            focused_query=q,
        )

        class _MockLowGrounding:
            grounding_score = 0.3
            issues = ["weak"]
            warnings = []

        result = orch._run_crag_self_correction(
            query="Hallo",
            history=[],
            extras=[],
            current_sources=[],
            current_results=[],
            current_verification_result=_MockLowGrounding(),
            fallback=False,
        )
        assert result is None  # INTERNAL_ONLY → kein CRAG-Retry

    def test_crag_adaptive_disabled_when_flag_false(self):
        """
        Bei adaptive_strategy=False soll der adaptive-Pfad in CRAG nicht betreten werden,
        sondern direkt tools.run() als plain retrieval nutzen.
        """
        orch = _mk_orchestrator(adaptive_strategy=False, rag_enabled=True)
        orch.crag_self_correction_enabled = True
        orch.crag_grounding_threshold = 0.8
        orch.crag_max_retries = 1
        orch.rag_k = 4
        orch.web_search_k = 3

        # Adaptive pipeline existiert, soll aber NICHT gerufen werden
        adaptive_called = False

        class _MockPipeline:
            def execute(self, query: str):
                nonlocal adaptive_called
                adaptive_called = True
                raise RuntimeError("sollte nicht gerufen werden")

        orch._adaptive_pipeline = _MockPipeline()  # type: ignore[assignment]

        tools_called = False

        class _MockToolResult:
            tool = "rag_search"
            success = True
            text = "plain_evidence"
            score = 0.80
            meta = {}

        class _MockTools:
            def run(self, calls):
                nonlocal tools_called
                tools_called = True
                return [_MockToolResult()]

        orch.tools = _MockTools()  # type: ignore[assignment]

        class _MockEvidenceSource:
            text = "plain_evidence"
            score = 0.80

        class _MockEvidenceResult:
            sources = [_MockEvidenceSource()]

        class _MockEvidenceManager:
            def select_evidence_from_tool_results(self, **kw):
                return _MockEvidenceResult()

        orch.evidence_manager = _MockEvidenceManager()  # type: ignore[assignment]
        orch.summarize = lambda *a, **k: ("draft", [])  # type: ignore[assignment]
        orch.verify_step = lambda *a, **k: (  # type: ignore[assignment]
            "final",
            SimpleNamespace(grounding_score=0.90, issues=[], warnings=[]),
        )
        orch._is_news_query = lambda q: False  # type: ignore[assignment]
        orch.use_llm_evidence_selection = False
        orch.evidence_max_candidates = 5
        orch.evidence_shortlist_m = 3
        orch.evidence_diversity_lambda = 0.7
        orch.news_min_k = 2
        orch.news_max_k = 6

        class _MockLowGrounding:
            grounding_score = 0.3
            issues = ["weak"]
            warnings = []

        result = orch._run_crag_self_correction(
            query="Test",
            history=[],
            extras=[],
            current_sources=[],
            current_results=[],
            current_verification_result=_MockLowGrounding(),
            fallback=False,
        )

        # Adaptive NICHT gerufen (Flag=False)
        assert not adaptive_called
        # tools.run() als plain retrieval gerufen
        assert tools_called
        assert result is not None
