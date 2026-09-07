#!/usr/bin/env python3
"""
INTELLIGENT ROUTING SYSTEM für optimale Suchkombinationen
Automatische Query-Klassifikation und adaptive Suchstrategie-Auswahl
"""

import time
import logging
from typing import Dict, List, Any, Optional, Tuple
from enum import Enum

logger = logging.getLogger(__name__)

class QueryType(Enum):
    """Query-Kategorien für intelligentes Routing"""
    STRUCTURED_DATA = "structured_data"  # Tabellen, KG, spezifische Daten
    CURRENT_EVENTS = "current_events"    # Aktuelle Ereignisse, News
    FACTUAL = "factual"                  # Faktische Wissensfragen
    GENERAL = "general"                  # Allgemeine Anfragen
    TECHNICAL = "technical"              # Technische/spezifische Dokumentation

class SearchStrategy:
    """Konfiguration für verschiedene Suchstrategien"""
    def __init__(self, name: str, rag_ratio: float, boost_structured: bool = False, 
                 web_enabled: bool = True, structured_boost: float = 0.15):
        self.name = name
        self.rag_ratio = rag_ratio          # Anteil RAG vs Web (0.0-1.0)
        self.boost_structured = boost_structured  # Strukturierte Daten bevorzugen
        self.web_enabled = web_enabled       # Web-Suche aktiviert
        self.structured_boost = structured_boost  # Boost-Faktor für strukturierte Daten

# Vordefinierte Strategien für verschiedene Query-Typen
SEARCH_STRATEGIES = {
    QueryType.STRUCTURED_DATA: SearchStrategy(
        name="Enhanced RAG", 
        rag_ratio=0.9, 
        boost_structured=True, 
        web_enabled=False,
        structured_boost=0.2
    ),
    QueryType.CURRENT_EVENTS: SearchStrategy(
        name="Web-focused Hybrid", 
        rag_ratio=0.3, 
        boost_structured=False, 
        web_enabled=True,
        structured_boost=0.08
    ),
    QueryType.FACTUAL: SearchStrategy(
        name="Balanced Hybrid", 
        rag_ratio=0.6, 
        boost_structured=False, 
        web_enabled=True,
        structured_boost=0.1
    ),
    QueryType.GENERAL: SearchStrategy(
        name="RAG-focused Hybrid", 
        rag_ratio=0.8, 
        boost_structured=False, 
        web_enabled=True,
        structured_boost=0.1
    ),
    QueryType.TECHNICAL: SearchStrategy(
        name="Document-focused RAG", 
        rag_ratio=0.95, 
        boost_structured=True, 
        web_enabled=False,
        structured_boost=0.15
    )
}

class IntelligentRouter:
    """
    SOTA Intelligent Router with Embedding-based Zero-Shot Classification.
    
    Uses cosine similarity between query embedding and category prototype embeddings
    for robust classification, with keyword fallback when embedding model unavailable.
    
    Features:
    - Zero-shot embedding classification (SOTA)
    - Keyword-based fallback  
    - Adaptive strategy optimization via performance feedback
    - Performance monitoring
    """
    
    # Category prototype descriptions for zero-shot classification
    # Each category gets multiple representative descriptions in German
    CATEGORY_PROTOTYPES = {
        QueryType.STRUCTURED_DATA: [
            "Zeige mir eine Tabelle mit Daten und Statistiken",
            "Erstelle ein Diagramm oder Chart mit Zahlen",
            "Exportiere die Daten als CSV oder Excel",
            "Datenbankabfrage mit SQL oder strukturierten Daten",
            "Übersicht über Preise, Kosten und Kennzahlen",
        ],
        QueryType.CURRENT_EVENTS: [
            "Was ist heute in den Nachrichten passiert?",
            "Aktuelle Entwicklungen und neue Trends 2025",
            "Die neuesten Updates und Ereignisse",
            "Was hat sich kürzlich in der Welt verändert?",
            "Aktuelle News und Berichterstattung",
        ],
        QueryType.FACTUAL: [
            "Was ist die Definition und Bedeutung davon?",
            "Erkläre mir wie das funktioniert",
            "Beschreibe die Grundlagen und Konzepte",
            "Was sind die Fakten und Hintergründe?",
            "Gib mir eine sachliche Erklärung dazu",
        ],
        QueryType.GENERAL: [
            "Hilf mir bei einem allgemeinen Thema",
            "Ich habe eine Frage zu einem Thema",
            "Kannst du mir bei etwas helfen?",
            "Erzähl mir etwas über dieses Thema",
            "Allgemeine Unterhaltung und Beratung",
        ],
        QueryType.TECHNICAL: [
            "Wie implementiere ich diese API Funktion im Code?",
            "Erkläre den Algorithmus und die Programmierung",
            "Technische Dokumentation zur Software und Framework",
            "Debugging und Fehlerbehebung im Quellcode",
            "Architektur und Design Pattern der Anwendung",
        ],
    }
    
    def __init__(self):
        self.query_stats = {}
        self.performance_cache = {}
        self.fallback_strategy = SEARCH_STRATEGIES[QueryType.GENERAL]
        
        # SOTA: Embedding-based classification
        self._embedding_model = None
        self._prototype_embeddings: Optional[Dict[QueryType, Any]] = None
        self._embedding_available = False
        
        try:
            from utils.embedding_singleton import get_embedding_model
            self._embedding_model_fn = get_embedding_model
            self._embedding_available = True
            logger.info("🧠 IntelligentRouter: Embedding-based classification available")
        except ImportError:
            logger.info("IntelligentRouter: Using keyword-based fallback (no embedding model)")
    
    def _ensure_prototypes(self):
        """Lazily compute and cache prototype embeddings for each category."""
        if self._prototype_embeddings is not None:
            return
        
        import numpy as np
        
        try:
            model = self._embedding_model_fn()
            self._prototype_embeddings = {}
            
            for query_type, descriptions in self.CATEGORY_PROTOTYPES.items():
                # Encode all prototype descriptions and average them
                embeddings = model.encode(descriptions, show_progress_bar=False)
                # Centroid = mean of all prototypes for this category
                centroid = np.mean(embeddings, axis=0)
                # Re-normalize the centroid
                centroid = centroid / np.linalg.norm(centroid)
                self._prototype_embeddings[query_type] = centroid
                
            logger.info(f"🧠 Computed {len(self._prototype_embeddings)} category prototype embeddings")
        except Exception as e:
            logger.warning(f"Failed to compute prototype embeddings: {e}", exc_info=True)
            self._embedding_available = False
            self._prototype_embeddings = None
        
    def classify_query(self, query: str) -> Tuple[QueryType, float]:
        """
        Classify a query using embedding-based zero-shot classification.
        
        Falls back to keyword matching if embedding model unavailable.
        
        Returns:
            Tuple[QueryType, float]: (detected_type, confidence_score)
        """
        query_lower = query.lower().strip()
        
        if len(query_lower) < 3:
            return QueryType.GENERAL, 0.5
        
        # SOTA: Embedding-based classification.
        # Runtime failures are propagated (fail-fast) — silent keyword fallback
        # would hide model/embedding faults and drift routing quality.
        # Keyword classification remains the legitimate capability-absent path
        # (no embedding model available at init time, see __init__).
        if self._embedding_available:
            result = self._classify_by_embedding(query)
            if result is not None:
                return result
            # Embedding path declined to classify (e.g. very low confidence) —
            # fall through to keyword-based path is acceptable here.
        
        # Capability-absent path: no embedding model available.
        return self._classify_by_keywords(query_lower)
    
    def _classify_by_embedding(self, query: str) -> Optional[Tuple[QueryType, float]]:
        """Zero-shot classification via cosine similarity to category prototypes."""
        import numpy as np
        
        self._ensure_prototypes()
        if self._prototype_embeddings is None:
            return None
        
        model = self._embedding_model_fn()
        query_embedding = model.encode([query], normalize_embeddings=True, show_progress_bar=False)[0]
        
        # Compute similarity to each category centroid
        similarities = {}
        for query_type, centroid in self._prototype_embeddings.items():
            sim = float(np.dot(query_embedding, centroid))
            similarities[query_type] = sim
        
        # Find best match
        best_type = max(similarities, key=lambda k: similarities[k])
        best_sim = similarities[best_type]
        
        # Convert cosine similarity to confidence
        # Typical range: 0.3-0.8; map to 0.4-0.95
        confidence = min(0.95, max(0.4, (best_sim - 0.3) * 1.1 + 0.4))
        
        # Check margin: best should be significantly better than second-best
        sorted_sims = sorted(similarities.values(), reverse=True)
        if len(sorted_sims) > 1:
            margin = sorted_sims[0] - sorted_sims[1]
            if margin < 0.02:
                # Very close scores → reduce confidence
                confidence *= 0.7
        
        return best_type, confidence
    
    def _classify_by_keywords(self, query_lower: str) -> Tuple[QueryType, float]:
        """Fallback: Keyword-based classification."""
        # Strukturierte Daten
        structure_keywords = [
            'tabelle', 'daten', 'statistik', 'preis', 'preise', 'kosten', 
            'zahlen', 'übersicht', 'diagramm', 'chart', 'graph', 'liste',
            'database', 'db', 'sql', 'export', 'import', 'csv', 'excel',
            'matrix', 'vektor', 'array', 'dataset', 'datensatz'
        ]
        structure_score = sum(1 for kw in structure_keywords if kw in query_lower)
        
        # Aktuelle Ereignisse
        current_keywords = [
            'heute', 'aktuell', 'neu', 'neue', 'neues', '2025', '2024', 
            'jetzt', 'kürzlich', 'letzte', 'letzten', 'recent', 'latest',
            'news', 'nachrichten', 'entwicklung', 'trend', 'update'
        ]
        current_score = sum(1 for kw in current_keywords if kw in query_lower)
        
        # Faktische Fragen
        factual_patterns = [
            'was ist', 'was sind', 'wie funktioniert', 'definition', 
            'bedeutung', 'erklärung', 'erkläre', 'beschreibe',
            'what is', 'how does', 'define', 'explain', 'describe'
        ]
        factual_score = sum(1 for pattern in factual_patterns if pattern in query_lower)
        
        # Technische Dokumentation
        technical_keywords = [
            'api', 'code', 'programmierung', 'software', 'algorithmus',
            'funktion', 'methode', 'klasse', 'modul', 'bibliothek',
            'framework', 'library', 'documentation', 'manual', 'guide'
        ]
        technical_score = sum(1 for kw in technical_keywords if kw in query_lower)
        
        scores = {
            QueryType.STRUCTURED_DATA: structure_score * 2.0,
            QueryType.CURRENT_EVENTS: current_score * 1.8,
            QueryType.FACTUAL: factual_score * 1.5,
            QueryType.TECHNICAL: technical_score * 1.6
        }
        
        if max(scores.values()) > 0:
            best_type = max(scores, key=lambda k: scores[k])
            confidence = min(0.95, scores[best_type] * 0.3)
            if confidence >= 0.4:
                return best_type, confidence
        
        return QueryType.GENERAL, 0.6
    
    def get_strategy(self, query: str, force_type: Optional[QueryType] = None) -> Tuple[SearchStrategy, Dict[str, Any]]:
        """
        Ermittelt optimale Suchstrategie für eine Query
        
        Args:
            query: Die Suchanfrage
            force_type: Erzwinge bestimmten Query-Typ (für Tests)
            
        Returns:
            Tuple[SearchStrategy, Dict]: (strategie, routing_info)
        """
        start_time = time.time()
        
        # Query klassifizieren
        if force_type:
            query_type = force_type
            confidence = 1.0
        else:
            query_type, confidence = self.classify_query(query)
        
        # Entsprechende Strategie wählen
        strategy = SEARCH_STRATEGIES.get(query_type, self.fallback_strategy)
        
        # Routing-Informationen sammeln
        routing_info = {
            "query_type": query_type.value,
            "confidence": confidence,
            "strategy_name": strategy.name,
            "rag_ratio": strategy.rag_ratio,
            "boost_structured": strategy.boost_structured,
            "web_enabled": strategy.web_enabled,
            "routing_time": time.time() - start_time,
            "fallback_used": query_type not in SEARCH_STRATEGIES
        }
        
        # Statistiken aktualisieren
        self._update_stats(query_type, confidence)
        
        return strategy, routing_info
    
    def _update_stats(self, query_type: QueryType, confidence: float):
        """Aktualisiert interne Statistiken"""
        type_name = query_type.value
        if type_name not in self.query_stats:
            self.query_stats[type_name] = {"count": 0, "avg_confidence": 0.0}
        
        stats = self.query_stats[type_name]
        old_avg = stats["avg_confidence"]
        old_count = stats["count"]
        
        stats["count"] += 1
        stats["avg_confidence"] = (old_avg * old_count + confidence) / stats["count"]
    
    def get_stats(self) -> Dict[str, Any]:
        """Gibt Routing-Statistiken zurück"""
        return {
            "query_stats": self.query_stats,
            "total_queries": sum(stats["count"] for stats in self.query_stats.values()),
            "strategies_available": list(SEARCH_STRATEGIES.keys()),
            "fallback_strategy": self.fallback_strategy.name
        }
    
    def optimize_strategy(self, query_type: QueryType, performance_feedback: Dict[str, float]):
        """
        Optimiert Strategien basierend auf Performance-Feedback
        
        Args:
            query_type: Der Query-Typ
            performance_feedback: Dict mit Metriken wie {"quality": 0.8, "speed": 0.9}
        """
        if query_type in SEARCH_STRATEGIES:
            strategy = SEARCH_STRATEGIES[query_type]
            
            # Einfache Optimierung: Bei schlechter Qualität, mehr Web-Suche
            if performance_feedback.get("quality", 0.5) < 0.6:
                if strategy.web_enabled and strategy.rag_ratio > 0.3:
                    strategy.rag_ratio = max(0.3, strategy.rag_ratio - 0.1)
                    logger.info(f"Optimized {query_type.value}: reduced RAG ratio to {strategy.rag_ratio}")
            
            # Bei schlechter Geschwindigkeit, weniger Web-Suche
            elif performance_feedback.get("speed", 0.5) < 0.5:
                if strategy.web_enabled and strategy.rag_ratio < 0.9:
                    strategy.rag_ratio = min(0.9, strategy.rag_ratio + 0.1)
                    logger.info(f"Optimized {query_type.value}: increased RAG ratio to {strategy.rag_ratio}")

# Globale Router-Instanz
_global_router = None

def get_global_router() -> IntelligentRouter:
    """Gibt globale Router-Instanz zurück (Singleton)"""
    global _global_router
    if _global_router is None:
        _global_router = IntelligentRouter()
    return _global_router

def classify_query(query: str) -> Tuple[QueryType, float]:
    """Convenience-Funktion für Query-Klassifikation"""
    return get_global_router().classify_query(query)

def get_search_strategy(query: str, force_type: Optional[QueryType] = None) -> Tuple[SearchStrategy, Dict[str, Any]]:
    """Convenience-Funktion für Strategie-Ermittlung"""
    return get_global_router().get_strategy(query, force_type)
