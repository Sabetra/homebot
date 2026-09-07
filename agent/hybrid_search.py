#!/usr/bin/env python3
"""
HYBRID SEARCH ENGINE mit Intelligent Routing
Kombiniert RAG, Web-Suche und intelligente Strategie-Auswahl
"""

import time
import asyncio
import logging
from typing import Dict, List, Any, Optional, Tuple, Union
from concurrent.futures import ThreadPoolExecutor, as_completed

from agent.intelligent_routing import (
    IntelligentRouter, QueryType, SearchStrategy, 
    get_global_router, get_search_strategy
)

logger = logging.getLogger(__name__)

class HybridSearchEngine:
    """
    Intelligente Hybrid-Suchmaschine
    
    Features:
    - Automatisches Query-Routing
    - RAG + Web-Suche Kombination
    - Result-Merging und Reranking
    - Performance-Optimierung
    """
    
    def __init__(self, rag_store, web_search_enabled: bool = True):
        self.rag_store = rag_store
        self.web_search_enabled = web_search_enabled
        self.router = get_global_router()
        self.performance_stats: Dict[str, Any] = {}
        
        # Web-Suche Verfügbarkeit prüfen
        self._check_web_search_availability()
        
    def _check_web_search_availability(self):
        """Prüft ob Web-Suche verfügbar ist"""
        try:
            # In VS Code ist vscode-websearchforcopilot_webSearch verfügbar
            # Hier simulieren wir die Verfügbarkeit
            self.web_search_available = self.web_search_enabled
            if self.web_search_available:
                logger.info("🌐 Web-Suche Integration aktiviert")
            else:
                logger.warning("🌐 Web-Suche nicht verfügbar, reine RAG-Suche")
        except Exception as e:
            self.web_search_available = False
            logger.warning(f"Web-Suche Fehler: {e}")
    
    def search(self, query: str, k: int = 10, force_strategy: Optional[str] = None, 
               force_query_type: Optional[QueryType] = None) -> Dict[str, Any]:
        """
        Intelligente Hybrid-Suche mit automatischem Routing
        
        Args:
            query: Suchanfrage
            k: Anzahl gewünschter Ergebnisse
            force_strategy: Erzwinge bestimmte Strategie ("rag_only", "web_only", "hybrid")
            force_query_type: Erzwinge bestimmten Query-Typ (für Tests)
            
        Returns:
            Dict mit Suchergebnissen und Metadaten
        """
        start_time = time.time()
        
        # Strategie ermitteln
        if force_strategy:
            strategy, routing_info = self._get_forced_strategy(force_strategy, query)
        else:
            strategy, routing_info = get_search_strategy(query, force_query_type)
        
        logger.info(f"🎯 Query: '{query[:50]}...' → {strategy.name} (Konfidenz: {routing_info['confidence']:.2f})")
        
        try:
            # Suche durchführen
            if not self.web_search_available or not strategy.web_enabled:
                # Reine RAG-Suche
                results = self._search_rag_only(query, k, strategy)
                search_method = "RAG Only"
                
            elif strategy.rag_ratio <= 0.1:
                # Reine Web-Suche
                results = self._search_web_only(query, k)
                search_method = "Web Only"
                
            else:
                # Hybrid-Suche
                results = self._search_hybrid(query, k, strategy)
                search_method = "Hybrid"
            
            # Ergebnis-Metadaten hinzufügen
            total_time = time.time() - start_time
            
            search_result = {
                "results": results,
                "metadata": {
                    "query": query,
                    "total_results": len(results),
                    "search_time": total_time,
                    "search_method": search_method,
                    "routing_info": routing_info,
                    "strategy": {
                        "name": strategy.name,
                        "rag_ratio": strategy.rag_ratio,
                        "boost_structured": strategy.boost_structured,
                        "web_enabled": strategy.web_enabled
                    }
                }
            }
            
            # Performance-Statistiken aktualisieren
            self._update_performance_stats(routing_info["query_type"], total_time, len(results))
            
            return search_result
            
        except Exception as e:
            logger.error(f"Hybrid-Suche Fehler: {e}")
            # Fallback auf einfache RAG-Suche
            results = self.rag_store.search(query, k=k)
            return {
                "results": results,
                "metadata": {
                    "query": query,
                    "total_results": len(results),
                    "search_time": time.time() - start_time,
                    "search_method": "RAG Fallback",
                    "error": str(e)
                }
            }
    
    def _search_rag_only(self, query: str, k: int, strategy: SearchStrategy) -> List[Dict[str, Any]]:
        """Reine RAG-Suche mit optimiertem Boosting"""
        
        # Enhanced RAG mit strukturiertem Boosting
        if strategy.boost_structured:
            # Strukturierte Typen bevorzugen
            prefer_types = {"table", "kg"}
            results = self.rag_store.search(
                query, 
                k=k, 
                prefer_types=prefer_types,
                boost_factor=strategy.structured_boost
            )
        else:
            # Standard RAG-Suche
            results = self.rag_store.search(query, k=k)
        
        # Zusätzliches Query-spezifisches Boosting
        if strategy.boost_structured:
            results = self._apply_query_specific_boosting(query, results)
        
        search_results: List[Dict[str, Any]] = results
        return search_results
    
    def _search_web_only(self, query: str, k: int) -> List[Dict[str, Any]]:
        """Reine Web-Suche (simuliert)"""
        
        # In echter Implementierung würde hier vscode-websearchforcopilot_webSearch aufgerufen
        # Für jetzt simulieren wir Web-Ergebnisse
        web_results = []
        
        for i in range(min(k, 5)):  # Max 5 Web-Ergebnisse
            web_results.append({
                "content": f"Web-Ergebnis {i+1} für '{query}': Simulierte Web-Suche Antwort...",
                "metadata": {
                    "type": "web",
                    "source": f"web_result_{i+1}",
                    "url": f"https://example.com/search?q={query}&result={i+1}",
                    "title": f"Web-Ergebnis {i+1}"
                },
                "score": 0.8 - (i * 0.1)  # Absteigende Web-Scores
            })
        
        logger.info(f"🌐 Simulierte {len(web_results)} Web-Ergebnisse")
        return web_results
    
    def _search_hybrid(self, query: str, k: int, strategy: SearchStrategy) -> List[Dict[str, Any]]:
        """Hybrid-Suche: RAG + Web kombiniert"""
        
        # Ergebnisse aufteilen
        rag_count = max(1, int(k * strategy.rag_ratio))
        web_count = max(1, k - rag_count)
        
        # Parallel-Suche für bessere Performance
        with ThreadPoolExecutor(max_workers=4) as executor:  # Erhöht für Ryzen 9 5950X
            # RAG-Suche starten
            rag_future = executor.submit(self._search_rag_only, query, rag_count, strategy)
            
            # Web-Suche starten (falls aktiviert)
            if self.web_search_available and strategy.web_enabled:
                web_future = executor.submit(self._search_web_only, query, web_count)
            else:
                web_future = None
            
            # Ergebnisse sammeln
            rag_results = rag_future.result()
            web_results = web_future.result() if web_future else []
        
        # Ergebnisse intelligent mergen
        merged_results = self._merge_results(rag_results, web_results, k, strategy)
        
        logger.info(f"🔄 Hybrid: {len(rag_results)} RAG + {len(web_results)} Web → {len(merged_results)} final")
        return merged_results
    
    def _merge_results(self, rag_results: List[Dict], web_results: List[Dict], 
                      k: int, strategy: SearchStrategy) -> List[Dict]:
        """Intelligent Result-Merging"""
        
        # Alle Ergebnisse sammeln
        all_results = []
        
        # RAG-Ergebnisse hinzufügen (mit Boosting falls konfiguriert)
        for result in rag_results:
            result_copy = result.copy()
            if strategy.boost_structured:
                result_type = result.get("metadata", {}).get("type", "")
                if result_type in ["table", "kg"]:
                    result_copy["score"] = result_copy.get("score", 0.0) + 0.1
            all_results.append(result_copy)
        
        # Web-Ergebnisse hinzufügen
        for result in web_results:
            all_results.append(result)
        
        # Nach Score sortieren und deduplizieren
        all_results.sort(key=lambda x: x.get("score", 0.0), reverse=True)
        
        # Duplikate entfernen (einfache Content-basierte Deduplizierung)
        unique_results = []
        seen_content = set()
        
        for result in all_results:
            content_hash = hash(result.get("content", "")[:100])  # Erste 100 Zeichen
            if content_hash not in seen_content:
                seen_content.add(content_hash)
                unique_results.append(result)
                
                if len(unique_results) >= k:
                    break
        
        return unique_results
    
    def _apply_query_specific_boosting(self, query: str, results: List[Dict]) -> List[Dict]:
        """Wendet Query-spezifisches Boosting an"""
        
        query_lower = query.lower()
        structure_terms = ['tabelle', 'daten', 'statistik', 'preis', 'kosten', 'zahlen', 'übersicht']
        
        if any(term in query_lower for term in structure_terms):
            # Boost für strukturierte Inhalte
            for result in results:
                result_type = result.get("metadata", {}).get("type", "")
                if result_type == "table":
                    result["score"] = result.get("score", 0.0) + 0.1
                elif result_type == "kg":
                    result["score"] = result.get("score", 0.0) + 0.05
            
            # Nach neuen Scores sortieren
            results.sort(key=lambda x: x.get("score", 0.0), reverse=True)
        
        return results
    
    def _get_forced_strategy(self, force_strategy: str, query: str) -> Tuple[SearchStrategy, Dict]:
        """Erstellt forcierte Strategie"""
        
        if force_strategy == "rag_only":
            strategy = SearchStrategy("Forced RAG Only", 1.0, True, False, 0.15)
        elif force_strategy == "web_only":
            strategy = SearchStrategy("Forced Web Only", 0.0, False, True, 0.08)
        elif force_strategy == "hybrid":
            strategy = SearchStrategy("Forced Hybrid", 0.6, False, True, 0.1)
        else:
            # Fallback auf automatisches Routing
            return get_search_strategy(query)
        
        routing_info = {
            "query_type": "forced",
            "confidence": 1.0,
            "strategy_name": strategy.name,
            "rag_ratio": strategy.rag_ratio,
            "boost_structured": strategy.boost_structured,
            "web_enabled": strategy.web_enabled,
            "routing_time": 0.0,
            "fallback_used": False
        }
        
        return strategy, routing_info
    
    def _update_performance_stats(self, query_type: str, search_time: float, result_count: int):
        """Aktualisiert Performance-Statistiken"""
        
        if query_type not in self.performance_stats:
            self.performance_stats[query_type] = {
                "total_queries": 0,
                "avg_time": 0.0,
                "avg_results": 0.0
            }
        
        stats = self.performance_stats[query_type]
        old_count = stats["total_queries"]
        
        stats["total_queries"] += 1
        stats["avg_time"] = (stats["avg_time"] * old_count + search_time) / stats["total_queries"]
        stats["avg_results"] = (stats["avg_results"] * old_count + result_count) / stats["total_queries"]
    
    def get_performance_stats(self) -> Dict[str, Any]:
        """Gibt Performance-Statistiken zurück"""
        return {
            "performance_stats": self.performance_stats,
            "router_stats": self.router.get_stats(),
            "web_search_available": self.web_search_available
        }
    
    def optimize_performance(self):
        """Optimiert Performance basierend auf gesammelten Statistiken"""
        
        for query_type, stats in self.performance_stats.items():
            # Feedback für Router generieren
            avg_time = stats["avg_time"]
            avg_results = stats["avg_results"]
            
            # Qualität = Ergebnisse/Zeit Ratio (vereinfacht)
            quality_score = min(1.0, avg_results / max(0.1, avg_time))
            speed_score = min(1.0, 1.0 / max(0.1, avg_time))
            
            performance_feedback = {
                "quality": quality_score,
                "speed": speed_score
            }
            
            # Router optimieren
            try:
                qt = QueryType(query_type)
                self.router.optimize_strategy(qt, performance_feedback)
            except (ValueError, AttributeError):
                pass  # Unbekannter Query-Typ


def create_hybrid_search_engine(rag_store, web_search_enabled: bool = True) -> HybridSearchEngine:
    """Factory-Funktion für HybridSearchEngine"""
    return HybridSearchEngine(rag_store, web_search_enabled)

# Convenience-Funktion für einfache Integration
def smart_search(rag_store, query: str, k: int = 10, **kwargs) -> Dict[str, Any]:
    """
    Führt intelligente Hybrid-Suche durch
    
    Args:
        rag_store: RAG Store Instanz
        query: Suchanfrage
        k: Anzahl Ergebnisse
        **kwargs: Weitere Parameter für HybridSearchEngine.search()
    
    Returns:
        Dict mit Suchergebnissen und Metadaten
    """
    engine = create_hybrid_search_engine(rag_store)
    return engine.search(query, k, **kwargs)
