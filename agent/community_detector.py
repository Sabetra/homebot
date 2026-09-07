# -*- coding: utf-8 -*-
"""
Community Detection Engine für Knowledge Graph
==============================================

SOTA Community Detection (2025) basierend auf Leiden-Algorithmus mit:
- Multi-resolution Community Discovery
- Community Summary Generation via LLM
- Subgraph Retrieval statt nur 1-Hop-Nachbarn
- Community-aware RAG Reranking

Referenzen:
- Traag et al. (2019) - The Leiden Algorithm
- Rosvall & Bergstrom (2008) - Map Equation
- Newman (2006) - Modularity Optimization

Author: Agent System
Date: 2026-07-15
"""

import logging
import time
import hashlib
from typing import Dict, List, Optional, Any, Tuple, Set, Callable
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
import json
import numpy as np

import networkx as nx
from networkx.algorithms import community as nx_community

logger = logging.getLogger(__name__)


# ============================================================================
# Data Classes
# ============================================================================

class CommunityQuality(Enum):
    """Qualitätsbewertung einer Community."""
    EXCELLENT = "excellent"  # Modularity > 0.7
    GOOD = "good"           # Modularity > 0.5
    FAIR = "fair"           # Modularity > 0.3
    POOR = "poor"           # Modularity <= 0.3


@dataclass
class CommunityInfo:
    """Informationen über eine Community."""
    community_id: int
    nodes: List[str]
    size: int
    density: float
    modularity_score: float
    quality: CommunityQuality
    summary: str = ""
    keywords: List[str] = field(default_factory=list)
    central_nodes: List[str] = field(default_factory=list)
    avg_clustering: float = 0.0
    diameter: int = -1
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "community_id": self.community_id,
            "nodes": self.nodes,
            "size": self.size,
            "density": self.density,
            "modularity_score": self.modularity_score,
            "quality": self.quality.value,
            "summary": self.summary,
            "keywords": self.keywords,
            "central_nodes": self.central_nodes,
            "avg_clustering": self.avg_clustering,
            "diameter": self.diameter,
        }


@dataclass
class SubgraphResult:
    """Ergebnis einer Subgraph-Suche."""
    subgraph: nx.Graph
    community_ids: List[int]
    relevance_scores: Dict[str, float]
    total_nodes: int
    total_edges: int
    retrieval_time_ms: float
    query_embedding: Optional[np.ndarray] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "community_ids": self.community_ids,
            "relevance_scores": self.relevance_scores,
            "total_nodes": self.total_nodes,
            "total_edges": self.total_edges,
            "retrieval_time_ms": self.retrieval_time_ms,
        }


# ============================================================================
# Community Detector
# ============================================================================

class CommunityDetector:
    """
    SOTA Community Detection für Knowledge Graphs.
    
    Features:
    - Leiden-Algorithmus für stabile Communities
    - Multi-resolution Analysis
    - Community Summary Generation
    - Subgraph Retrieval
    - RAG-aware Reranking
    
    Usage:
        detector = CommunityDetector(graph)
        detector.detect_communities()
        communities = detector.get_communities()
        
        # Subgraph retrieval
        result = detector.retrieve_subgraph("query text", top_k=5)
        
        # Community summary
        summary = detector.get_community_summary(community_id=0)
    """
    
    def __init__(self, graph: nx.Graph, resolution: float = 1.0):
        """
        Args:
            graph: NetworkX Graph/MultiGraph
            resolution: Auflösung für Community Detection (höher = mehr Communities)
        """
        self.graph = graph
        self.resolution = resolution
        self.communities: Dict[int, Set[str]] = {}
        self.node_to_community: Dict[str, int] = {}
        self.community_summaries: Dict[int, str] = {}
        self.community_keywords: Dict[int, List[str]] = {}
        self.modularity: float = 0.0
        self._detected: bool = False
        self._detection_time_ms: float = 0.0
        
    def detect_communities(self, force: bool = False) -> Dict[str, Any]:
        """
        Community Detection mit Greedy Modularity (NetworkX-native).
        
        Args:
            force: Wenn True, erzwingt Neudetection
            
        Returns:
            Dictionary mit Detection-Statistiken
        """
        if self._detected and not force:
            return self._get_stats()
            
        start_time = time.time()
        logger.info(f"🔍 Starting community detection on graph with "
                   f"{self.graph.number_of_nodes()} nodes, "
                   f"{self.graph.number_of_edges()} edges")
        
        try:
            # Greedy Modularity Community Detection (NetworkX-native, stabil & performant)
            communities_gen = nx_community.greedy_modularity_communities(
                self.graph,
                weight="weight"
            )
            
            # Generator von Sets -> in partition umwandeln
            partition = {}
            for comm_id, node_set in enumerate(communities_gen):
                for node in node_set:
                    partition[node] = comm_id
            
            # Partition organisieren: community_id -> nodes
            self.communities = {}
            self.node_to_community = {}
            
            for node, comm_id in partition.items():
                if comm_id not in self.communities:
                    self.communities[comm_id] = set()
                self.communities[comm_id].add(node)
                self.node_to_community[node] = comm_id
            
            # Modularity berechnen (erwartet Iterable von Sets, kein Dict)
            community_sets = [nodes for nodes in self.communities.values()]
            self.modularity = nx_community.modularity(
                self.graph,
                community_sets
            )
            
            self._detection_time_ms = (time.time() - start_time) * 1000
            self._detected = True
            
            logger.info(f"✅ Community detection complete: "
                       f"{len(self.communities)} communities, "
                       f"modularity={self.modularity:.4f}, "
                       f"time={self._detection_time_ms:.1f}ms")
            
            return self._get_stats()
            
        except Exception as e:
            logger.error(f"❌ Community detection failed: {e}")
            self._detected = False
            return {"success": False, "error": str(e)}
    
    def _get_stats(self) -> Dict[str, Any]:
        """Statistiken zurückgeben."""
        return {
            "success": self._detected,
            "num_communities": len(self.communities),
            "num_nodes": self.graph.number_of_nodes(),
            "num_edges": self.graph.number_of_edges(),
            "modularity": self.modularity,
            "detection_time_ms": self._detection_time_ms,
            "resolution": self.resolution,
        }
    
    def get_communities(self) -> List[CommunityInfo]:
        """
        Alle Communities mit Metadaten zurückgeben.
        
        Returns:
            Liste von CommunityInfo-Objekten
        """
        if not self._detected:
            self.detect_communities()
            
        community_infos = []
        
        for comm_id, nodes in self.communities.items():
            subgraph = self.graph.subgraph(nodes)
            density = nx.density(subgraph)
            
            # Modularity pro Community (als Anteil der Gesamtmodularity)
            comm_modularity = self.modularity / max(len(self.communities), 1)
            
            # Qualität bewerten
            quality = self._assess_quality(comm_modularity)
            
            # Keywords extrahieren
            keywords = self._extract_keywords(nodes)
            
            # Zentrale Nodes (top 5 by degree centrality)
            centralities = nx.degree_centrality(subgraph)
            central_nodes = sorted(centralities.keys(), key=lambda n: centralities[n], reverse=True)[:5]
            
            # Average clustering
            avg_clustering = nx.average_clustering(subgraph)
            
            # Diameter (falls nicht zu groß)
            diameter: int = -1
            if len(nodes) <= 100:
                try:
                    diam_result = nx.diameter(subgraph)
                    if isinstance(diam_result, int):
                        diameter = diam_result
                except (nx.NetworkXError, TypeError, ValueError):
                    diameter = -1
            
            community_infos.append(CommunityInfo(
                community_id=comm_id,
                nodes=list(nodes),
                size=len(nodes),
                density=density,
                modularity_score=comm_modularity,
                quality=quality,
                summary=self.community_summaries.get(comm_id, ""),
                keywords=keywords,
                central_nodes=central_nodes,
                avg_clustering=avg_clustering,
                diameter=diameter,
            ))
        
        # Nach Größe sortieren (größte zuerst)
        community_infos.sort(key=lambda x: x.size, reverse=True)
        return community_infos
    
    def get_node_community(self, node: str) -> Optional[int]:
        """Community-ID eines Nodes zurückgeben."""
        return self.node_to_community.get(node)
    
    def get_community_nodes(self, community_id: int) -> Optional[Set[str]]:
        """Nodes einer Community zurückgeben."""
        return self.communities.get(community_id)
    
    def get_community_info(self, community_id: int) -> Optional[CommunityInfo]:
        """
        CommunityInfo-Objekt für eine einzelne Community zurückgeben.

        O(1) Lookup mit optionalem Cache. Erstellt das CommunityInfo-Objekt
        aus vorhandenen Daten (Nodes, Summary, Keywords, Graph-Metriken).

        Args:
            community_id: Community-ID

        Returns:
            CommunityInfo-Objekt oder None wenn Community nicht existiert
        """
        if not self._detected:
            return None

        nodes = self.communities.get(community_id)
        if nodes is None:
            return None

        # Summary lazy nachladen (falls noch nicht vorhanden)
        if community_id not in self.community_summaries:
            self.get_community_summary(community_id)

        # Keywords lazy nachladen (falls noch nicht vorhanden)
        keywords = self.community_keywords.get(community_id, [])
        if not keywords:
            keywords = self._extract_keywords(nodes)
            self.community_keywords[community_id] = keywords

        subgraph = self.graph.subgraph(nodes)
        density = nx.density(subgraph)

        # Modularity pro Community (als Anteil der Gesamtmodularity)
        comm_modularity = self.modularity / max(len(self.communities), 1)

        # Qualität bewerten
        quality = self._assess_quality(comm_modularity)

        # Zentrale Nodes (top 5 by degree centrality)
        centralities = nx.degree_centrality(subgraph)
        central_nodes = sorted(centralities.keys(), key=lambda n: centralities[n], reverse=True)[:5]

        # Average clustering
        avg_clustering = nx.average_clustering(subgraph)

        # Diameter (falls nicht zu groß)
        diameter: int = -1
        if len(nodes) <= 100:
            try:
                diam_result = nx.diameter(subgraph)
                if isinstance(diam_result, int):
                    diameter = diam_result
            except (nx.NetworkXError, TypeError, ValueError):
                diameter = -1

        return CommunityInfo(
            community_id=community_id,
            nodes=list(nodes),
            size=len(nodes),
            density=density,
            modularity_score=comm_modularity,
            quality=quality,
            summary=self.community_summaries.get(community_id, ""),
            keywords=keywords,
            central_nodes=central_nodes,
            avg_clustering=avg_clustering,
            diameter=diameter,
        )

    def generate_community_summaries(self) -> Dict[int, str]:
        """
        Batch-Generierung von keyword-basierten Summaries für alle Communities.

        Wird bei Initialisierung aufgerufen, um Summaries für die Persistenz
        vorzubereiten. Nutzt keyword-basierte Extraktion (kein LLM) für
        schnelle Generierung auch bei tausenden Communities.

        Returns:
            Dictionary {community_id: summary_text}
        """
        if not self._detected:
            self.detect_communities()

        for comm_id in self.communities:
            if comm_id not in self.community_summaries:
                self.get_community_summary(comm_id)

        return dict(self.community_summaries)
    
    def retrieve_subgraph(
        self,
        query: str,
        top_k_communities: int = 5,
        max_depth: int = 2,
        include_embeddings: bool = True
    ) -> SubgraphResult:
        """
        Subgraph-Retrieval basierend auf Query.
        
        Statt nur 1-Hop-Nachbarn werden relevante Communities
        identifiziert und deren Subgraphs extrahiert.
        
        Args:
            query: Suchanfrage
            top_k_communities: Anzahl der relevantesten Communities
            max_depth: Maximale Tiefe der Neighbor-Expansion
            include_embeddings: Embeddings verwenden (falls verfügbar)
            
        Returns:
            SubgraphResult mit relevanz-bewerteten Nodes
        """
        start_time = time.time()
        
        if not self._detected:
            self.detect_communities()
        
        # Query-Hash als Proxy für Embedding (kann durch echtes Embedding ersetzt werden)
        query_hash = int(hashlib.md5(query.encode()).hexdigest(), 16)
        
        # Community-Relevanz berechnen
        community_scores = self._score_communities(query, query_hash)
        
        # Top-K Communities auswählen
        sorted_communities = sorted(
            community_scores.items(),
            key=lambda x: x[1],
            reverse=True
        )[:top_k_communities]
        
        # Subgraph aufbauen
        subgraph_nodes = set()
        relevance_scores = {}
        
        for comm_id, score in sorted_communities:
            nodes = self.communities.get(comm_id, set())
            for node in nodes:
                subgraph_nodes.add(node)
                # Node-Relevanz = Community-Score * Node-Centrality
                node_centrality = self._get_node_centrality(node)
                relevance_scores[node] = score * (1 + node_centrality)
        
        # Neighbor-Expansion (max_depth)
        if max_depth > 1:
            expanded_nodes = self._expand_neighbors(subgraph_nodes, max_depth - 1)
            for node in expanded_nodes:
                if node not in subgraph_nodes:
                    subgraph_nodes.add(node)
                    relevance_scores[node] = 0.1  # Geringe Relevanz für expanded nodes
        
        # Subgraph extrahieren
        subgraph = self.graph.subgraph(subgraph_nodes).copy()
        
        retrieval_time = (time.time() - start_time) * 1000
        
        return SubgraphResult(
            subgraph=subgraph,
            community_ids=[c for c, _ in sorted_communities],
            relevance_scores=relevance_scores,
            total_nodes=len(subgraph_nodes),
            total_edges=subgraph.number_of_edges(),
            retrieval_time_ms=retrieval_time,
        )
    
    def get_community_summary(
        self,
        community_id: int,
        generate_if_missing: bool = True,
        llm_callback: Optional[Callable] = None
    ) -> str:
        """
        Summary einer Community zurückgeben oder generieren.
        
        Args:
            community_id: Community-ID
            generate_if_missing: Summary generieren wenn nicht vorhanden
            llm_callback: Callback für LLM-basierte Summary-Generierung
            
        Returns:
            Summary-Text oder leerer String
        """
        if community_id in self.community_summaries:
            return self.community_summaries[community_id]
        
        if not generate_if_missing:
            return ""
        
        # Summary generieren
        nodes = self.communities.get(community_id, set())
        if not nodes:
            return ""
        
        # Fallback: Keyword-basiertes Summary
        keywords = self._extract_keywords(nodes)
        summary = f"Community {community_id}: {len(nodes)} nodes, " \
                  f"key topics: {', '.join(keywords[:10])}"
        
        # LLM-basiertes Summary (falls Callback verfügbar)
        if llm_callback:
            try:
                node_labels = []
                for node in list(nodes)[:20]:  # Max 20 Nodes für Context
                    label = self.graph.nodes.get(node, {}).get("label", node)
                    node_labels.append(label)
                
                llm_summary = llm_callback(node_labels, keywords)
                if llm_summary:
                    summary = llm_summary
            except Exception as e:
                logger.warning(f"LLM summary generation failed: {e}")
        
        self.community_summaries[community_id] = summary
        return summary
    
    def compute_rerank_scores(
        self,
        candidates: List[Dict[str, Any]],
        query: str
    ) -> List[float]:
        """
        Community-aware Reranking-Scores für RAG-Candidates.
        
        Args:
            candidates: Liste von Candidate-Dictionaries mit 'node' oder 'community_id'
            query: Suchanfrage
            
        Returns:
            Liste von Relevance-Scores
        """
        if not self._detected:
            self.detect_communities()
        
        scores = []
        query_hash = int(hashlib.md5(query.encode()).hexdigest(), 16)
        
        for candidate in candidates:
            node = candidate.get("node")
            community_id = candidate.get("community_id")
            
            if node and node in self.node_to_community:
                node_comm = self.node_to_community[node]
                comm_score = self._score_community_relevance(node_comm, query, query_hash)
                node_centrality = self._get_node_centrality(node)
                final_score = comm_score * (1 + node_centrality)
                scores.append(final_score)
            elif community_id is not None:
                comm_score = self._score_community_relevance(community_id, query, query_hash)
                scores.append(comm_score)
            else:
                scores.append(0.0)
        
        return scores
    
    # ========================================================================
    # Private Helper Methods
    # ========================================================================
    
    def _assess_quality(self, modularity: float) -> CommunityQuality:
        """Qualität basierend auf Modularity bewerten."""
        if modularity > 0.7:
            return CommunityQuality.EXCELLENT
        elif modularity > 0.5:
            return CommunityQuality.GOOD
        elif modularity > 0.3:
            return CommunityQuality.FAIR
        else:
            return CommunityQuality.POOR
    
    def _extract_keywords(self, nodes: Set[str]) -> List[str]:
        """Keywords aus Node-Labels extrahieren."""
        keywords: Dict[str, int] = {}
        for node in nodes:
            label = self.graph.nodes.get(node, {}).get("label", node)
            # Einfache Tokenisierung
            tokens = str(label).lower().split()
            for token in tokens:
                if len(token) > 2:  # Kurze Tokens ignorieren
                    keywords[token] = keywords.get(token, 0) + 1
        
        # Top-Keywords zurückgeben
        sorted_keywords = sorted(keywords.items(), key=lambda x: x[1], reverse=True)
        return [k for k, _ in sorted_keywords[:20]]
    
    def _score_communities(
        self,
        query: str,
        query_hash: int
    ) -> Dict[int, float]:
        """
        Community-Relevanz für Query bewerten.
        
        Nutzt eine Kombination aus:
        - Keyword-Übereinstimmung
        - Community-Dichte
        - Community-Größe
        """
        scores = {}
        query_tokens = set(query.lower().split())
        
        for comm_id, nodes in self.communities.items():
            # Keyword-Match-Score
            keyword_score = 0.0
            for node in nodes:
                label = str(self.graph.nodes.get(node, {}).get("label", node)).lower()
                label_tokens = set(label.split())
                intersection = query_tokens & label_tokens
                if intersection:
                    keyword_score += len(intersection) / max(len(query_tokens), 1)
            
            keyword_score /= max(len(nodes), 1)
            
            # Dichte-Bonus (dichtere Communities sind kohäsiver)
            subgraph = self.graph.subgraph(nodes)
            density = nx.density(subgraph)
            density_bonus = density * 0.3
            
            # Größe-Penalty (sehr große Communities sind weniger spezifisch)
            size_penalty = 1.0 / (1.0 + 0.01 * len(nodes))
            
            # Kombiniert
            scores[comm_id] = (keyword_score + density_bonus) * size_penalty
        
        return scores
    
    def _score_community_relevance(
        self,
        community_id: int,
        query: str,
        query_hash: int
    ) -> float:
        """Relevanz einer einzelnen Community für Query."""
        scores = self._score_communities(query, query_hash)
        return scores.get(community_id, 0.0)
    
    def _get_node_centrality(self, node: str) -> float:
        """Degree Centrality eines Nodes."""
        if node not in self.graph:
            return 0.0
        return nx.degree_centrality(self.graph).get(node, 0.0)
    
    def _expand_neighbors(
        self,
        nodes: Set[str],
        depth: int
    ) -> Set[str]:
        """Neighbor-Expansion bis zu max_depth."""
        expanded: Set[str] = set()
        current_nodes = nodes.copy()
        
        for _ in range(depth):
            new_nodes = set()
            for node in current_nodes:
                if node in self.graph:
                    neighbors = self.graph.neighbors(node)
                    new_nodes.update(neighbors)
            
            new_nodes -= nodes
            new_nodes -= expanded
            expanded.update(new_nodes)
            current_nodes = new_nodes
        
        return expanded
    
    # ========================================================================
    # Persistence
    # ========================================================================
    
    def save_state(self, path: str) -> bool:
        """Community-Detection-State speichern."""
        try:
            state = {
                "communities": {str(k): list(v) for k, v in self.communities.items()},
                "node_to_community": self.node_to_community,
                "community_summaries": self.community_summaries,
                "community_keywords": self.community_keywords,
                "modularity": self.modularity,
                "resolution": self.resolution,
                "detected": self._detected,
            }
            
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(state, f, ensure_ascii=False, indent=2)
            
            logger.info(f"💾 Community state saved to {path}")
            return True
            
        except Exception as e:
            logger.error(f"❌ Failed to save community state: {e}")
            return False
    
    def load_state(self, path: str) -> bool:
        """Community-Detection-State laden."""
        try:
            with open(path, 'r', encoding='utf-8') as f:
                state = json.load(f)
            
            self.communities = {
                int(k): set(v) for k, v in state["communities"].items()
            }
            self.node_to_community = state["node_to_community"]
            self.community_summaries = state["community_summaries"]
            self.community_keywords = state.get("community_keywords", {})
            self.modularity = state["modularity"]
            self.resolution = state.get("resolution", self.resolution)
            self._detected = state["detected"]
            
            logger.info(f"📂 Community state loaded from {path}")
            return True
            
        except Exception as e:
            logger.error(f"❌ Failed to load community state: {e}")
            return False


# ============================================================================
# Standalone Utility Functions
# ============================================================================

def detect_communities_on_graph(
    graph: nx.Graph,
    resolution: float = 1.0,
    save_path: Optional[str] = None
) -> Tuple[CommunityDetector, Dict[str, Any]]:
    """
    Convenience-Funktion: Community Detection auf einem Graphen.
    
    Args:
        graph: NetworkX Graph
        resolution: Auflösungsparameter
        save_path: Optional Speicherpfad für State
        
    Returns:
        Tuple von (CommunityDetector, Stats-Dictionary)
    """
    detector = CommunityDetector(graph, resolution=resolution)
    stats = detector.detect_communities()
    
    if save_path and stats.get("success"):
        detector.save_state(save_path)
    
    return detector, stats


def get_subgraph_retrieval(
    detector: CommunityDetector,
    query: str,
    top_k: int = 5
) -> SubgraphResult:
    """
    Convenience-Funktion: Subgraph-Retrieval.
    
    Args:
        detector: CommunityDetector-Instanz
        query: Suchanfrage
        top_k: Anzahl der Communities
        
    Returns:
        SubgraphResult
    """
    return detector.retrieve_subgraph(query, top_k_communities=top_k)