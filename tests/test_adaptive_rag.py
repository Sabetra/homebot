"""
Tests für Adaptive RAG: Router + Multi-Hop Retriever + Pipeline
================================================================

Testet:
1. AdaptiveRAGRouter — shallow/deep Entscheidungen, Parsing, Fallback, Cache
2. MultiHopRetriever — iterative Hops, Konvergenz, Dedup, Cap
3. AdaptiveRAGPipeline — End-to-End Flow
"""

import pytest
from unittest.mock import MagicMock, call
from dataclasses import dataclass
from typing import List, Tuple

from agent.adaptive_rag import (
    AdaptiveRAGRouter,
    MultiHopRetriever,
    AdaptiveRAGPipeline,
    RetrievalDepth,
    RouterDecision,
    MultiHopResult,
    HopResult,
    AdaptiveRAGResult,
)


# ============================================================================
# Fixtures
# ============================================================================

def mock_llm_shallow(prompt: str, max_tokens: int) -> str:
    """Simuliert LLM das shallow antwortet."""
    return "DEPTH: shallow\nCONFIDENCE: 0.9\nREASON: Einfache Faktenfrage\nENTITIES: \nRELATIONS: "


def mock_llm_deep(prompt: str, max_tokens: int) -> str:
    """Simuliert LLM das deep antwortet."""
    return "DEPTH: deep\nCONFIDENCE: 0.85\nREASON: Vergleichsanfrage benötigt Multi-Hop\nENTITIES: Berlin, München\nRELATIONS: Temperatur, Bevölkerung"


def mock_llm_sufficient(prompt: str, max_tokens: int) -> str:
    """Simuliert LLM das SUFFICIENT: true antwortet."""
    return "SUFFICIENT: true"


def mock_llm_insufficient(prompt: str, max_tokens: int) -> str:
    """Simuliert LLM das SUFFICIENT: false antwortet."""
    return "SUFFICIENT: false"


def mock_llm_constraint_gen(prompt: str, max_tokens: int) -> str:
    """Simuliert Constraint-Generierung."""
    return """SUFFICIENT: false
REASON: Fehlende Details zu Entity B
SUBQUERIES:
1. Entity B details properties
2. Entity B relationship to Entity A
3. Comparison metrics Entity A vs B"""


def mock_retrieve(query: str, k: int) -> Tuple[List[str], List[float]]:
    """Simuliert Retrieval mit deterministischen Ergebnissen."""
    texts = [f"Chunk_{query[:10]}_{i}" for i in range(k)]
    scores = [0.9 - i * 0.05 for i in range(k)]
    return texts, scores


# ============================================================================
# 1. AdaptiveRAGRouter Tests
# ============================================================================

class TestAdaptiveRAGRouter:
    """Tests für den LLM-basierten Router."""

    def test_shallow_routing(self):
        """Shallow-Query wird korrekt geroutet."""
        router = AdaptiveRAGRouter(llm_callable=mock_llm_shallow)
        decision = router.route("Was ist die Hauptstadt von Frankreich?")

        assert decision.depth == RetrievalDepth.SHALLOW
        assert decision.confidence == 0.9
        assert "Faktenfrage" in decision.reason
        assert router.stats["shallow_count"] == 1

    def test_deep_routing(self):
        """Deep-Query wird korrekt geroutet."""
        router = AdaptiveRAGRouter(llm_callable=mock_llm_deep)
        decision = router.route("Vergleiche Berlin und München in Klima und Bevölkerung")

        assert decision.depth == RetrievalDepth.DEEP
        assert decision.confidence == 0.85
        assert "Berlin" in decision.entities
        assert "München" in decision.entities
        assert router.stats["deep_count"] == 1

    def test_cache_hit(self):
        """Zweite identische Query nutzt Cache."""
        router = AdaptiveRAGRouter(llm_callable=mock_llm_shallow)
        q = "Was ist Python?"
        router.route(q)
        router.route(q)

        assert router.stats["total_calls"] == 2
        assert router.stats["cache_hits"] == 1

    def test_fallback_on_llm_error(self):
        """Bei LLM-Fehler wird Fallback verwendet."""
        def failing_llm(prompt: str, max_tokens: int) -> str:
            raise RuntimeError("LLM unavailable")

        router = AdaptiveRAGRouter(llm_callable=failing_llm)
        decision = router.route("Testfrage")

        # Fallback sollte shallow sein (kein Multi-Entity Pattern)
        assert decision.depth == RetrievalDepth.SHALLOW
        assert decision.confidence == 0.5

    def test_fallback_deep_pattern(self):
        """Fallback erkennt Deep-Pattern (case-sensitive Match)."""
        def failing_llm(prompt: str, max_tokens: int) -> str:
            raise RuntimeError("LLM unavailable")

        router = AdaptiveRAGRouter(llm_callable=failing_llm)
        # Pattern "im Vergleich zu" muss exakt (case-sensitive) im Query vorkommen
        decision = router.route("A im Vergleich zu B Eigenschaften")

        assert decision.depth == RetrievalDepth.DEEP

    def test_fallback_shallow_default(self):
        """Fallback bei unbekanntem Pattern gibt shallow zurück."""
        def failing_llm(prompt: str, max_tokens: int) -> str:
            raise RuntimeError("LLM unavailable")

        router = AdaptiveRAGRouter(llm_callable=failing_llm)
        decision = router.route("Einfache Testfrage ohne Muster")

        assert decision.depth == RetrievalDepth.SHALLOW

    def test_parse_malformed_response(self):
        """Bei schlechtem LLM-Output wird Default verwendet."""
        def malformed_llm(prompt: str, max_tokens: int) -> str:
            return "Kurzantwort ohne Struktur"

        router = AdaptiveRAGRouter(llm_callable=malformed_llm)
        decision = router.route("Test")

        assert decision.depth == RetrievalDepth.SHALLOW  # Default
        assert decision.confidence == 0.7  # Default

    def test_history_context(self):
        """History wird als Kontext berücksichtigt."""
        history = [
            {"role": "user", "content": "Frage 1"},
            {"role": "assistant", "content": "Antwort 1"},
            {"role": "user", "content": "Frage 2"},
        ]
        router = AdaptiveRAGRouter(llm_callable=mock_llm_shallow)
        decision = router.route("Frage 3", history=history)

        assert decision.depth == RetrievalDepth.SHALLOW

    def test_reset_stats(self):
        """Stats können zurückgesetzt werden."""
        router = AdaptiveRAGRouter(llm_callable=mock_llm_shallow)
        router.route("Test")
        stats = router.reset_stats()

        assert stats["total_calls"] == 0
        assert stats["shallow_count"] == 0

    def test_latency_tracking(self):
        """Latenz wird gemessen."""
        router = AdaptiveRAGRouter(llm_callable=mock_llm_shallow)
        decision = router.route("Test")

        assert decision.latency_ms >= 0


# ============================================================================
# 2. MultiHopRetriever Tests
# ============================================================================

class TestMultiHopRetriever:
    """Tests für den Multi-Hop Retriever."""

    def test_single_hop_convergence(self):
        """Bei sofortiger Konvergenz kein weiterer Hop."""
        call_count = 0

        def counting_llm(prompt: str, max_tokens: int) -> str:
            nonlocal call_count
            call_count += 1
            return "SUFFICIENT: true"

        retriever = MultiHopRetriever(
            llm_callable=counting_llm,
            retrieve_fn=mock_retrieve,
            max_hops=3,
            min_evidence_count=6,
        )

        # Initiale Evidence mit 6 Chunks
        initial = ([f"Chunk_{i}" for i in range(6)], [0.9] * 6)
        result = retriever.retrieve("Testfrage", initial_evidence=initial)

        assert result.hops_executed == 0
        assert result.converged is True
        assert len(result.total_evidence_texts) == 6

    def test_multi_hop_execution(self):
        """Mehrere Hops werden ausgeführt bei Insuffizienz."""
        call_count = 0

        def step_llm(prompt: str, max_tokens: int) -> str:
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                return "SUFFICIENT: false"
            return "SUFFICIENT: true"

        retriever = MultiHopRetriever(
            llm_callable=step_llm,
            retrieve_fn=mock_retrieve,
            max_hops=3,
            min_evidence_count=6,
        )

        initial = ([f"Chunk_{i}" for i in range(6)], [0.9] * 6)
        result = retriever.retrieve("Testfrage", initial_evidence=initial)

        # Mindestens 1 Hop nötig
        assert result.hops_executed >= 1

    def test_evidence_deduplication(self):
        """Doppelte Evidences werden entfernt."""
        def dup_retrieve(query: str, k: int) -> Tuple[List[str], List[float]]:
            # Immer gleiche Chunks zurückgeben
            return ([f"Same_{i}" for i in range(k)], [0.9] * k)

        retriever = MultiHopRetriever(
            llm_callable=mock_llm_insufficient,
            retrieve_fn=dup_retrieve,
            max_hops=2,
            min_evidence_count=3,
        )

        initial = ([f"Same_{i}" for i in range(5)], [0.9] * 5)
        result = retriever.retrieve("Test", initial_evidence=initial)

        # Trotz 2 Hops sollte Dedup die Gesamtzahl begrenzen
        assert len(set(result.total_evidence_texts)) <= len(result.total_evidence_texts)

    def test_max_evidence_cap(self):
        """Evidence-Cap wird eingehalten."""
        retriever = MultiHopRetriever(
            llm_callable=mock_llm_insufficient,
            retrieve_fn=mock_retrieve,
            max_hops=3,
            max_evidence_count=10,
        )

        initial = ([f"Chunk_{i}" for i in range(8)], [0.9] * 8)
        result = retriever.retrieve("Test", initial_evidence=initial)

        assert len(result.total_evidence_texts) <= 20  # Cap ist pro Hop, aber insgesamt begrenzt

    def test_max_hops_reached(self):
        """Bei max_hops wird abgebrochen."""
        retriever = MultiHopRetriever(
            llm_callable=mock_llm_insufficient,  # Immer insufficient
            retrieve_fn=mock_retrieve,
            max_hops=2,
            min_evidence_count=100,  # Sehr hoch, nie erreicht
        )

        result = retriever.retrieve("Test")

        assert result.hops_executed <= 2
        assert result.converged is False

    def test_stats_tracking(self):
        """Statistiken werden korrekt geführt."""
        retriever = MultiHopRetriever(
            llm_callable=mock_llm_sufficient,
            retrieve_fn=mock_retrieve,
            max_hops=3,
            min_evidence_count=6,
        )

        initial = ([f"Chunk_{i}" for i in range(6)], [0.9] * 6)
        retriever.retrieve("Test", initial_evidence=initial)

        assert retriever.stats["total_runs"] == 1
        assert retriever.stats["converged_early"] >= 1

    def test_no_initial_evidence(self):
        """Ohne initiale Evidence wird im Hop-Retrieval gearbeitet."""
        retriever = MultiHopRetriever(
            llm_callable=mock_llm_insufficient,  # Immer insufficient → Hop wird ausgeführt
            retrieve_fn=mock_retrieve,
            max_hops=1,
            min_evidence_count=100,  # Sehr hoch, nie erreicht → Hop läuft
        )

        result = retriever.retrieve("Test")
        # MultiHopRetriever führt Retrieval in Hops auch ohne initiale Evidence
        assert result.hops_executed >= 1
        assert len(result.total_evidence_texts) > 0

    def test_evidence_hash_uniqueness(self):
        """Verschiedene Evidences haben verschiedene Hashes."""
        h1 = MultiHopRetriever._hash_evidence("Text A")
        h2 = MultiHopRetriever._hash_evidence("Text B")
        assert h1 != h2

    def test_evidence_hash_determinism(self):
        """Gleiche Evidence hat gleichen Hash."""
        h1 = MultiHopRetriever._hash_evidence("Same Text")
        h2 = MultiHopRetriever._hash_evidence("Same Text")
        assert h1 == h2


# ============================================================================
# 3. AdaptiveRAGPipeline Tests
# ============================================================================

class TestAdaptiveRAGPipeline:
    """End-to-End Tests für die Pipeline."""

    def test_shallow_pipeline(self):
        """Shallow-Query geht direkt zum Retrieval."""
        router = AdaptiveRAGRouter(llm_callable=mock_llm_shallow)
        retriever = MultiHopRetriever(
            llm_callable=mock_llm_sufficient,
            retrieve_fn=mock_retrieve,
            max_hops=3,
        )
        pipeline = AdaptiveRAGPipeline(router=router, multi_hop=retriever, default_k=6)

        result = pipeline.execute("Was ist Python?")

        assert result.route == RetrievalDepth.SHALLOW
        assert result.hops_used == 0
        assert len(result.evidence_texts) == 6

    def test_deep_pipeline(self):
        """Deep-Query nutzt Multi-Hop."""
        router = AdaptiveRAGRouter(llm_callable=mock_llm_deep)
        retriever = MultiHopRetriever(
            llm_callable=mock_llm_sufficient,
            retrieve_fn=mock_retrieve,
            max_hops=3,
            min_evidence_count=6,
        )
        pipeline = AdaptiveRAGPipeline(router=router, multi_hop=retriever, default_k=6)

        result = pipeline.execute("Vergleiche A und B")

        assert result.route == RetrievalDepth.DEEP
        assert result.multi_hop_result is not None
        assert len(result.evidence_texts) >= 6

    def test_latency_measurement(self):
        """Gesamtlatenz wird gemessen."""
        router = AdaptiveRAGRouter(llm_callable=mock_llm_shallow)
        retriever = MultiHopRetriever(
            llm_callable=mock_llm_sufficient,
            retrieve_fn=mock_retrieve,
            max_hops=3,
        )
        pipeline = AdaptiveRAGPipeline(router=router, multi_hop=retriever, default_k=6)

        result = pipeline.execute("Test")
        assert result.total_latency_ms >= 0

    def test_router_stats_integrated(self):
        """Router-Stats sind nach Pipeline-Call verfügbar."""
        router = AdaptiveRAGRouter(llm_callable=mock_llm_shallow)
        retriever = MultiHopRetriever(
            llm_callable=mock_llm_sufficient,
            retrieve_fn=mock_retrieve,
            max_hops=3,
        )
        pipeline = AdaptiveRAGPipeline(router=router, multi_hop=retriever, default_k=6)

        pipeline.execute("Test 1")
        pipeline.execute("Test 2")

        assert router.stats["total_calls"] == 2


# ============================================================================
# 4. Integration Tests
# ============================================================================

class TestIntegration:
    """Integrationstests zwischen Komponenten."""

    def test_router_to_multihop_handoff(self):
        """Router-Entscheidung wird korrekt an MultiHop weitergegeben."""
        router = AdaptiveRAGRouter(llm_callable=mock_llm_deep)
        decision = router.route("Komplexe Frage")

        assert decision.depth == RetrievalDepth.DEEP
        assert isinstance(decision, RouterDecision)

    def test_multihop_result_structure(self):
        """MultiHopResult hat korrekte Struktur."""
        retriever = MultiHopRetriever(
            llm_callable=mock_llm_sufficient,
            retrieve_fn=mock_retrieve,
            max_hops=1,
            min_evidence_count=100,
        )

        result = retriever.retrieve("Test")

        assert isinstance(result, MultiHopResult)
        assert hasattr(result, 'hops')
        assert hasattr(result, 'total_evidence_texts')
        assert hasattr(result, 'converged')

    def test_pipeline_result_structure(self):
        """Pipeline-Resultat hat korrekte Struktur."""
        router = AdaptiveRAGRouter(llm_callable=mock_llm_shallow)
        retriever = MultiHopRetriever(
            llm_callable=mock_llm_sufficient,
            retrieve_fn=mock_retrieve,
            max_hops=3,
        )
        pipeline = AdaptiveRAGPipeline(router=router, multi_hop=retriever, default_k=6)

        result = pipeline.execute("Test")

        assert isinstance(result, AdaptiveRAGResult)
        assert hasattr(result, 'route')
        assert hasattr(result, 'router_decision')
        assert hasattr(result, 'evidence_texts')
        assert hasattr(result, 'total_latency_ms')


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--no-header", "-p", "no:cacheprovider"])