"""AgentOrchestrator._decide_retrieval_route – direkte Routing-Tests.

Ziel: Die 5 Routing-Pfade von _decide_retrieval_route deterministisch abdecken
ohne echte LLM-Abhängigkeit. Jeder Test isoliert genau einen Pfad.

Pfade:
  1. router_disabled → default (RAG_REQUIRED oder INTERNAL_ONLY)
  2. smalltalk/internal_only → INTERNAL_ONLY
  3. local_only_mode → RAG_REQUIRED (oder INTERNAL_ONLY wenn rag deaktiviert)
  4. requires_web + confidence >= 0.35 → WEB_REQUIRED
  5. default fallback → RAG_REQUIRED

Hinweis: Keine LLM-Abhängigkeit, rein lokal.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Optional

import pytest

from agent.orchestrator import (
    AgentOrchestrator,
    RetrievalRoute,
    RetrievalRoutingDecision,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mk_orchestrator(
    *,
    retrieval_router_enabled: bool = True,
    local_only_mode: bool = False,
    rag_enabled: bool = True,
    semantic_live_routing_enabled: bool = True,
) -> AgentOrchestrator:
    """Leichtgewichtiger Orchestrator-Stub ohne __init__."""
    obj = AgentOrchestrator.__new__(AgentOrchestrator)
    obj.retrieval_router_enabled = retrieval_router_enabled
    obj.local_only_mode = local_only_mode
    obj.rag_enabled = rag_enabled
    obj.semantic_live_routing_enabled = semantic_live_routing_enabled
    obj.adaptive_strategy = False
    obj._last_retrieval_route = RetrievalRoute.RAG_REQUIRED
    obj._live_data_assessment_cache = {}
    return obj


# ---------------------------------------------------------------------------
# Pfad 1: Router deaktiviert
# ---------------------------------------------------------------------------

class TestRouterDisabled:
    """Wenn retrieval_router_enabled=False, wird default-Routing verwendet."""

    def test_router_disabled_rag_enabled_returns_rag_required(self):
        orch = _mk_orchestrator(retrieval_router_enabled=False, rag_enabled=True)
        decision = orch._decide_retrieval_route("Wie funktioniert Photosynthese?")
        assert decision.route == RetrievalRoute.RAG_REQUIRED
        assert decision.reason == "router_disabled"
        assert decision.confidence == 0.0

    def test_router_disabled_rag_disabled_returns_internal_only(self):
        orch = _mk_orchestrator(retrieval_router_enabled=False, rag_enabled=False)
        decision = orch._decide_retrieval_route("Wie funktioniert Photosynthese?")
        assert decision.route == RetrievalRoute.INTERNAL_ONLY
        assert decision.reason == "router_disabled"


# ---------------------------------------------------------------------------
# Pfad 2: Smalltalk / internal-only Query
# ---------------------------------------------------------------------------

class TestInternalOnlyQuery:
    """Smalltalk-Queries werden als INTERNAL_ONLY geroutet."""

    def test_smalltalk_hello(self):
        orch = _mk_orchestrator()
        decision = orch._decide_retrieval_route("Hallo")
        assert decision.route == RetrievalRoute.INTERNAL_ONLY
        assert decision.reason == "smalltalk_or_local_reasoning"

    def test_smalltalk_thanks(self):
        orch = _mk_orchestrator()
        decision = orch._decide_retrieval_route("Danke!")
        assert decision.route == RetrievalRoute.INTERNAL_ONLY

    def test_local_reasoning_math(self):
        """'Was ist 2+2?' ist kein Smalltalk — braucht _assess_live_data_need.
        Wir mocken die Assessment-Methode, damit der Test deterministisch ist."""
        orch = _mk_orchestrator()
        orch._assess_live_data_need = lambda q, **kw: {  # type: ignore[assignment]
            "requires_web": False,
            "confidence": 0.0,
        }
        decision = orch._decide_retrieval_route("Was ist 2+2?")
        # Kein smalltalk, kein web → default RAG_REQUIRED
        assert decision.route == RetrievalRoute.RAG_REQUIRED


# ---------------------------------------------------------------------------
# Pfad 3: local_only_mode aktiv
# ---------------------------------------------------------------------------

class TestLocalOnlyMode:
    """Wenn APP_LOCAL_ONLY aktiv, wird RAG_REQUIRED (oder INTERNAL_ONLY) gezwungen."""

    def test_local_only_rag_enabled_returns_rag_required(self):
        orch = _mk_orchestrator(local_only_mode=True, rag_enabled=True)
        decision = orch._decide_retrieval_route("Aktuelle Wetterdaten Berlin")
        assert decision.route == RetrievalRoute.RAG_REQUIRED
        assert decision.reason == "app_local_only"
        assert decision.confidence == 1.0

    def test_local_only_rag_disabled_returns_internal_only(self):
        orch = _mk_orchestrator(local_only_mode=True, rag_enabled=False)
        decision = orch._decide_retrieval_route("Aktuelle Wetterdaten Berlin")
        assert decision.route == RetrievalRoute.INTERNAL_ONLY
        assert decision.reason == "app_local_only"


# ---------------------------------------------------------------------------
# Pfad 4: Web-Required (live data needed)
# ---------------------------------------------------------------------------

class TestWebRequired:
    """Queries mit live-data-Bedarf werden WEB_REQUIRED geroutet."""

    def test_live_data_query_routes_web(self):
        orch = _mk_orchestrator()
        # _assess_live_data_need ist heuristisch – wir testen den Pfad
        # indem wir die Methode mocken
        orch._assess_live_data_need = lambda q, **kw: {  # type: ignore[assignment]
            "requires_web": True,
            "confidence": 0.8,
            "reason": "current_weather_query",
            "focused_query": q,
        }
        decision = orch._decide_retrieval_route("Aktuelles Wetter in Berlin")
        assert decision.route == RetrievalRoute.WEB_REQUIRED
        assert decision.confidence >= 0.35

    def test_low_confidence_live_data_falls_through(self):
        """Bei confidence < 0.35 wird nicht WEB_REQUIRED geroutet."""
        orch = _mk_orchestrator()
        orch._assess_live_data_need = lambda q, **kw: {  # type: ignore[assignment]
            "requires_web": True,
            "confidence": 0.2,
            "reason": "uncertain",
            "focused_query": q,
        }
        decision = orch._decide_retrieval_route("Irgendwas aktuelles")
        # Fällt durch auf default RAG_REQUIRED
        assert decision.route == RetrievalRoute.RAG_REQUIRED


# ---------------------------------------------------------------------------
# Pfad 5: Default Fallback
# ---------------------------------------------------------------------------

class TestDefaultFallback:
    """Ohne Sonderfall wird RAG_REQUIRED (oder INTERNAL_ONLY) als Default verwendet."""

    def test_default_rag_required(self):
        orch = _mk_orchestrator()
        # Kein smalltalk, kein live-data → default
        orch._assess_live_data_need = lambda q, **kw: {  # type: ignore[assignment]
            "requires_web": False,
            "confidence": 0.0,
        }
        decision = orch._decide_retrieval_route("Erkläre Quantencomputing")
        assert decision.route == RetrievalRoute.RAG_REQUIRED
        assert decision.reason == "default_rag_first"
        assert decision.confidence == 0.6

    def test_default_internal_only_when_rag_disabled(self):
        orch = _mk_orchestrator(rag_enabled=False)
        orch._assess_live_data_need = lambda q, **kw: {  # type: ignore[assignment]
            "requires_web": False,
            "confidence": 0.0,
        }
        decision = orch._decide_retrieval_route("Erkläre Quantencomputing")
        assert decision.route == RetrievalRoute.INTERNAL_ONLY


# ---------------------------------------------------------------------------
# focused_query wird korrekt durchgereicht
# ---------------------------------------------------------------------------

class TestFocusedQuery:
    """focused_query ist der bereinigte Input."""

    def test_focused_query_preserved(self):
        orch = _mk_orchestrator(retrieval_router_enabled=False)
        decision = orch._decide_retrieval_route("  Test Query  ")
        assert decision.focused_query == "Test Query"

    def test_focused_query_from_assessment(self):
        orch = _mk_orchestrator()
        orch._assess_live_data_need = lambda q, **kw: {  # type: ignore[assignment]
            "requires_web": True,
            "confidence": 0.9,
            "reason": "live",
            "focused_query": "refined query",
        }
        decision = orch._decide_retrieval_route("Breite Frage")
        assert decision.focused_query == "refined query"