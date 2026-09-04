"""
Tests für Community Detection Engine.
"""
import pytest
import networkx as nx
from agent.community_detector import (
    CommunityDetector,
    CommunityInfo,
    CommunityQuality,
    SubgraphResult,
    detect_communities_on_graph,
    get_subgraph_retrieval,
)


class TestCommunityDetector:
    """Tests für den CommunityDetector."""

    def setup_method(self):
        """Test-Graph erstellen."""
        self.graph = nx.Graph()
        # Community 1: Cluster A
        self.graph.add_edges_from([
            ("A1", "A2"), ("A2", "A3"), ("A3", "A1"),
            ("A1", "A4"), ("A2", "A4"),
        ])
        self.graph.nodes["A1"]["label"] = "Maschinelles Lernen KI"
        self.graph.nodes["A2"]["label"] = "Neuronales Netzwerk Deep Learning"
        self.graph.nodes["A3"]["label"] = "Künstliche Intelligenz ML"
        self.graph.nodes["A4"]["label"] = "Training Daten Modell"

        # Community 2: Cluster B
        self.graph.add_edges_from([
            ("B1", "B2"), ("B2", "B3"), ("B3", "B1"),
        ])
        self.graph.nodes["B1"]["label"] = "Finanz Markt Aktien"
        self.graph.nodes["B2"]["label"] = "Wirtschaft Börse Handel"
        self.graph.nodes["B3"]["label"] = "Investment Portfolio Rendite"

        # Verbindung zwischen Communities
        self.graph.add_edge("A1", "B1")

    def test_detect_communities(self):
        """Community Detection funktioniert."""
        detector = CommunityDetector(self.graph)
        stats = detector.detect_communities()

        assert stats["success"] is True
        assert stats["num_communities"] >= 1
        assert stats["num_nodes"] == 7
        assert stats["modularity"] >= 0

    def test_get_communities(self):
        """Community-Infos sind korrekt."""
        detector = CommunityDetector(self.graph)
        detector.detect_communities()
        communities = detector.get_communities()

        assert len(communities) >= 1
        for comm in communities:
            assert isinstance(comm, CommunityInfo)
            assert comm.size > 0
            assert comm.density >= 0
            assert comm.quality in CommunityQuality

    def test_retrieve_subgraph(self):
        """Subgraph-Retrieval funktioniert."""
        detector = CommunityDetector(self.graph)
        detector.detect_communities()
        result = detector.retrieve_subgraph("KI Lernen", top_k_communities=2)

        assert isinstance(result, SubgraphResult)
        assert result.total_nodes > 0
        assert result.retrieval_time_ms >= 0
        assert len(result.community_ids) <= 2

    def test_rerank_scores(self):
        """Reranking-Scores sind korrekt."""
        detector = CommunityDetector(self.graph)
        detector.detect_communities()
        candidates = [
            {"node": "A1"},
            {"node": "B1"},
            {"community_id": 0},
        ]
        scores = detector.compute_rerank_scores(candidates, "KI")
        assert len(scores) == 3
        assert all(isinstance(s, float) for s in scores)

    def test_save_load_state(self, tmp_path):
        """State-Persistenz funktioniert."""
        detector = CommunityDetector(self.graph)
        detector.detect_communities()
        path = str(tmp_path / "communities.json")
        assert detector.save_state(path) is True
        assert detector.load_state(path) is True
        assert detector._detected is True

    def test_get_community_info(self):
        """Einzel-Community-Info ist korrekt."""
        detector = CommunityDetector(self.graph)
        detector.detect_communities()

        # Hole Info für Community 0 (existiert immer)
        info = detector.get_community_info(0)
        assert info is not None
        assert isinstance(info, CommunityInfo)
        assert info.community_id == 0
        assert info.size > 0
        assert info.density >= 0
        assert info.quality in CommunityQuality
        assert isinstance(info.keywords, list)
        assert isinstance(info.central_nodes, list)

        # Nicht-existierende Community gibt None zurück
        assert detector.get_community_info(9999) is None

        # Vor Detection gibt None zurück
        detector2 = CommunityDetector(self.graph)
        assert detector2.get_community_info(0) is None

    def test_generate_community_summaries(self):
        """Batch-Generierung von Summaries funktioniert."""
        detector = CommunityDetector(self.graph)
        detector.detect_communities()

        summaries = detector.generate_community_summaries()
        assert isinstance(summaries, dict)
        assert len(summaries) == len(detector.communities)

        # Alle Communities haben ein Summary
        for comm_id in detector.communities:
            assert comm_id in summaries
            assert isinstance(summaries[comm_id], str)
            assert len(summaries[comm_id]) > 0

        # Zweiter Aufruf ist idempotent (keine Duplikate)
        summaries2 = detector.generate_community_summaries()
        assert summaries == summaries2


class TestStandaloneFunctions:
    """Tests für Standalone-Funktionen."""

    def test_detect_communities_on_graph(self):
        """Convenience-Funktion funktioniert."""
        graph = nx.Graph()
        graph.add_edges_from([(1, 2), (2, 3), (3, 1)])
        detector, stats = detect_communities_on_graph(graph)
        assert stats["success"] is True
        assert isinstance(detector, CommunityDetector)

    def test_get_subgraph_retrieval(self):
        """Convenience-Funktion für Retrieval."""
        graph = nx.Graph()
        graph.add_edges_from([(1, 2), (2, 3), (3, 1)])
        detector, _ = detect_communities_on_graph(graph)
        result = get_subgraph_retrieval(detector, "test", top_k=1)
        assert isinstance(result, SubgraphResult)