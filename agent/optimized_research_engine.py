#!/usr/bin/env python3
"""
OPTIMIZED RESEARCH ENGINE - 2025 Version
Progressive Enhancement mit paralleler Strategieausführung und adaptiver Schwellenwerte

Features:
- Query-Profiling für intelligente Strategieauswahl
- Parallele Ausführung mehrerer Suchstrategien
- Progressive Enhancement (von RAG zu Web zu Spezialized)
- Smart Caching für bessere Performance  
- Adaptive Schwellenwerte basierend auf Query-Komplexität
- Konfidenz-basierte Ergebnis-Selektion
"""

import time
import asyncio
import logging
import hashlib
from typing import Dict, List, Any, Optional, Tuple, Union
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from enum import Enum

from agent.intelligent_routing import (
    IntelligentRouter, QueryType, SearchStrategy, 
    get_global_router, get_search_strategy
)
from agent.agent_types import ToolCall, ToolResult

logger = logging.getLogger(__name__)

class ResearchComplexity(Enum):
    """Komplexitätsstufen für Query-Profiling"""
    SIMPLE = "simple"
    MODERATE = "moderate"
    COMPLEX = "complex"
    EXPERT = "expert"

class ResearchConfidence(Enum):
    """Konfidenz-Level für Ergebnisse"""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    EXCELLENT = "excellent"

@dataclass
class QueryProfile:
    """Profil einer Query für optimale Strategieauswahl"""
    complexity: ResearchComplexity
    domains: List[str]
    time_sensitivity: float  # 0.0-1.0
    factual_vs_analytical: float  # 0.0=factual, 1.0=analytical
    structure_preference: float  # 0.0=unstructured, 1.0=structured
    estimated_quality_threshold: float  # 0.0-1.0
    
@dataclass
class ResearchResult:
    """Ergebnis einer Research-Operation"""
    results: List[Dict[str, Any]]
    confidence: ResearchConfidence
    strategy_used: str
    search_time: float
    sources_count: int
    quality_score: float
    metadata: Dict[str, Any]

class OptimizedResearchEngine:
    """
    Optimierte Research-Engine mit paralleler Ausführung und progressivem Enhancement
    """
    
    def __init__(self, rag_store, tool_manager=None, max_parallel_strategies: int = 3):
        self.rag_store = rag_store
        self.tool_manager = tool_manager
        self.max_parallel_strategies = max_parallel_strategies
        self.router = get_global_router()
        
        # Cache für bereits durchgeführte Suchen
        self.search_cache: Dict[str, Any] = {}
        self.cache_ttl = 3600  # 1 Stunde
        
        # Performance-Statistiken
        self.performance_stats = {
            "queries_processed": 0,
            "cache_hits": 0,
            "parallel_strategies_used": 0,
            "average_response_time": 0.0
        }
        
    def research(self, query: str, k: int = 10, max_search_time: float = 30.0,
                 force_strategies: Optional[List[str]] = None,
                 quality_threshold: Optional[float] = None) -> ResearchResult:
        """
        Hauptfunktion für optimierte Research
        
        Args:
            query: Suchanfrage
            k: Anzahl gewünschter Ergebnisse
            max_search_time: Maximale Suchzeit in Sekunden
            force_strategies: Erzwinge bestimmte Strategien
            quality_threshold: Minimale Qualitätsschwelle
            
        Returns:
            ResearchResult mit besten Ergebnissen
        """
        start_time = time.time()
        
        # 1. Query-Profiling
        profile = self._profile_query(query)
        logger.info(f"🔍 Query Profile: {profile.complexity.value}, Domains: {profile.domains}, "
                   f"Time-sensitive: {profile.time_sensitivity:.2f}")
        
        # 2. Cache-Check
        cache_key = self._get_cache_key(query, k)
        cached_result = self._get_from_cache(cache_key)
        if cached_result:
            logger.info("⚡ Cache Hit - returning cached result")
            self.performance_stats["cache_hits"] += 1
            return cached_result
        
        # 3. Adaptive Schwellenwerte basierend auf Query-Profil
        if quality_threshold is None:
            quality_threshold = profile.estimated_quality_threshold
        
        # 4. Strategieauswahl basierend auf Profil
        strategies = self._select_strategies(profile, force_strategies)
        logger.info(f"🎯 Selected strategies: {[s['name'] for s in strategies]}")
        
        # 5. Progressive Enhancement mit paralleler Ausführung
        best_result = self._progressive_enhancement_search(
            query, k, strategies, quality_threshold, max_search_time, profile
        )
        
        # 6. Cache-Update
        self._update_cache(cache_key, best_result)
        
        # 7. Performance-Update
        self.performance_stats["queries_processed"] += 1
        total_time = time.time() - start_time
        self._update_performance_stats(total_time)
        
        logger.info(f"✅ Research completed in {total_time:.2f}s, "
                   f"confidence: {best_result.confidence.value}, "
                   f"quality: {best_result.quality_score:.2f}")
        
        return best_result
    
    def _profile_query(self, query: str) -> QueryProfile:
        """Analysiert Query und erstellt Profil für optimale Strategieauswahl"""
        
        # Komplexitätsanalyse basierend auf Query-Länge und Struktur
        complexity = self._analyze_complexity(query)
        
        # Domain-Erkennung
        domains = self._detect_domains(query)
        
        # Zeitkritikalität
        time_sensitivity = self._analyze_time_sensitivity(query)
        
        # Faktisch vs. analytisch
        factual_vs_analytical = self._analyze_query_type(query)
        
        # Strukturpräferenz
        structure_preference = self._analyze_structure_preference(query)
        
        # Qualitätsschwelle basierend auf Komplexität
        quality_threshold = self._estimate_quality_threshold(complexity, factual_vs_analytical)
        
        return QueryProfile(
            complexity=complexity,
            domains=domains,
            time_sensitivity=time_sensitivity,
            factual_vs_analytical=factual_vs_analytical,
            structure_preference=structure_preference,
            estimated_quality_threshold=quality_threshold
        )
    
    def _analyze_complexity(self, query: str) -> ResearchComplexity:
        """Analysiert Query-Komplexität"""
        query_lower = query.lower()
        
        # Einfache Indikatoren
        simple_indicators = ["was ist", "wie heißt", "wann wurde", "wo liegt", "definition"]
        complex_indicators = ["vergleiche", "analysiere", "erkläre warum", "strategie", "konzept"]
        expert_indicators = ["implementierung", "architektur", "optimierung", "algorithmus"]
        
        word_count = len(query.split())
        
        if any(indicator in query_lower for indicator in expert_indicators) or word_count > 20:
            return ResearchComplexity.EXPERT
        elif any(indicator in query_lower for indicator in complex_indicators) or word_count > 10:
            return ResearchComplexity.COMPLEX
        elif word_count > 5:
            return ResearchComplexity.MODERATE
        else:
            return ResearchComplexity.SIMPLE
    
    def _detect_domains(self, query: str) -> List[str]:
        """Erkennt relevante Domains für die Query"""
        query_lower = query.lower()
        
        domain_keywords = {
            "technology": ["software", "hardware", "computer", "tech", "digital", "AI", "ML"],
            "business": ["unternehmen", "business", "marketing", "verkauf", "strategie"],
            "science": ["wissenschaft", "forschung", "studie", "experiment", "analyse"],
            "health": ["gesundheit", "medizin", "therapie", "behandlung", "symptom"],
            "education": ["lernen", "bildung", "schule", "universität", "kurs"],
            "current_events": ["heute", "aktuell", "neu", "2025", "news", "ereignis"]
        }
        
        detected_domains = []
        for domain, keywords in domain_keywords.items():
            if any(keyword in query_lower for keyword in keywords):
                detected_domains.append(domain)
        
        return detected_domains if detected_domains else ["general"]
    
    def _analyze_time_sensitivity(self, query: str) -> float:
        """Analysiert Zeitkritikalität der Query (0.0-1.0)"""
        query_lower = query.lower()
        
        high_time_sensitivity = ["aktuell", "heute", "jetzt", "neu", "2025", "recent", "latest"]
        medium_time_sensitivity = ["trend", "entwicklung", "status", "current"]
        
        if any(keyword in query_lower for keyword in high_time_sensitivity):
            return 0.9
        elif any(keyword in query_lower for keyword in medium_time_sensitivity):
            return 0.6
        else:
            return 0.2
    
    def _analyze_query_type(self, query: str) -> float:
        """Analysiert ob Query faktisch (0.0) oder analytisch (1.0) ist"""
        query_lower = query.lower()
        
        factual_indicators = ["was ist", "wer ist", "wann", "wo", "definition", "facts"]
        analytical_indicators = ["warum", "wie", "vergleiche", "analysiere", "bewerte", "erkläre"]
        
        if any(indicator in query_lower for indicator in analytical_indicators):
            return 0.8
        elif any(indicator in query_lower for indicator in factual_indicators):
            return 0.2
        else:
            return 0.5
    
    def _analyze_structure_preference(self, query: str) -> float:
        """Analysiert Präferenz für strukturierte Daten (0.0-1.0)"""
        query_lower = query.lower()
        
        structured_indicators = ["tabelle", "liste", "übersicht", "vergleich", "zahlen", "statistik"]
        
        if any(indicator in query_lower for indicator in structured_indicators):
            return 0.9
        else:
            return 0.3
    
    def _estimate_quality_threshold(self, complexity: ResearchComplexity, 
                                  factual_vs_analytical: float) -> float:
        """Schätzt optimale Qualitätsschwelle basierend auf Query-Eigenschaften"""
        
        base_threshold = {
            ResearchComplexity.SIMPLE: 0.6,
            ResearchComplexity.MODERATE: 0.7,
            ResearchComplexity.COMPLEX: 0.75,
            ResearchComplexity.EXPERT: 0.8
        }[complexity]
        
        # Analytische Queries brauchen höhere Qualität
        analytical_boost = factual_vs_analytical * 0.1
        
        return min(0.9, base_threshold + analytical_boost)
    
    def _select_strategies(self, profile: QueryProfile, 
                          force_strategies: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        """Wählt optimale Suchstrategien basierend auf Query-Profil"""
        
        if force_strategies:
            return [{"name": strategy, "priority": 1.0} for strategy in force_strategies]
        
        strategies: List[Dict[str, Any]] = []
        
        # Basis-Strategie basierend auf Domains und Zeitkritikalität
        if "current_events" in profile.domains or profile.time_sensitivity > 0.7:
            strategies.append({"name": "web_focused_hybrid", "priority": 0.9})
            strategies.append({"name": "rag_only", "priority": 0.3})
        elif profile.structure_preference > 0.7:
            strategies.append({"name": "enhanced_rag", "priority": 0.9})
            strategies.append({"name": "balanced_hybrid", "priority": 0.6})
        else:
            strategies.append({"name": "balanced_hybrid", "priority": 0.8})
            strategies.append({"name": "rag_only", "priority": 0.7})
        
        # Für komplexe Queries: zusätzliche Strategien
        if profile.complexity in [ResearchComplexity.COMPLEX, ResearchComplexity.EXPERT]:
            strategies.append({"name": "web_only", "priority": 0.5})
        
        # Sortieren nach Priorität und limitieren
        strategies.sort(key=lambda x: x["priority"], reverse=True)
        return strategies[:self.max_parallel_strategies]
    
    def _progressive_enhancement_search(self, query: str, k: int, strategies: List[Dict],
                                      quality_threshold: float, max_search_time: float,
                                      profile: QueryProfile) -> ResearchResult:
        """
        Progressive Enhancement: Startet mit schnellster Strategie und erweitert bei Bedarf
        """
        
        start_time = time.time()
        best_result = None
        
        # Phase 1: Schnelle Basis-Suche (RAG)
        logger.info("🚀 Phase 1: Fast RAG search")
        rag_result = self._execute_single_strategy(query, k, "rag_only", profile)
        
        if rag_result and rag_result.quality_score >= quality_threshold:
            logger.info(f"✅ Phase 1 sufficient: Quality {rag_result.quality_score:.2f} >= {quality_threshold:.2f}")
            return rag_result
        
        best_result = rag_result
        
        # Phase 2: Parallele Hybrid-Suche
        remaining_time = max_search_time - (time.time() - start_time)
        if remaining_time > 5.0 and len(strategies) > 1:
            logger.info("🔄 Phase 2: Parallel hybrid search")
            parallel_results = self._execute_parallel_strategies(
                query, k, strategies[1:], profile, remaining_time
            )
            
            # Beste Ergebnisse aus paralleler Suche wählen
            for result in parallel_results:
                if result and (not best_result or result.quality_score > best_result.quality_score):
                    best_result = result
                    
            if best_result and best_result.quality_score >= quality_threshold:
                logger.info(f"✅ Phase 2 sufficient: Quality {best_result.quality_score:.2f}")
                return best_result
        
        # Phase 3: Spezialized Search (falls immer noch unzureichend)
        remaining_time = max_search_time - (time.time() - start_time)
        if remaining_time > 3.0 and (not best_result or best_result.quality_score < quality_threshold):
            logger.info("🎯 Phase 3: Specialized search")
            specialized_result = self._execute_specialized_search(query, k, profile, remaining_time)
            
            if specialized_result and (not best_result or specialized_result.quality_score > best_result.quality_score):
                best_result = specialized_result
        
        return best_result or self._create_fallback_result(query, k)
    
    def _execute_single_strategy(self, query: str, k: int, strategy_name: str,
                                profile: QueryProfile) -> Optional[ResearchResult]:
        """Führt eine einzelne Suchstrategie aus"""
        
        try:
            start_time = time.time()
            
            if strategy_name == "rag_only":
                results = self._search_rag_only(query, k, profile)
            elif strategy_name == "web_only":
                results = self._search_web_only(query, k)
            elif strategy_name == "enhanced_rag":
                results = self._search_enhanced_rag(query, k, profile)
            elif strategy_name == "balanced_hybrid":
                results = self._search_balanced_hybrid(query, k, profile)
            elif strategy_name == "web_focused_hybrid":
                results = self._search_web_focused_hybrid(query, k, profile)
            else:
                results = []
            
            search_time = time.time() - start_time
            quality_score = self._calculate_quality_score(results, query, profile)
            confidence = self._determine_confidence(quality_score, len(results), search_time)
            
            return ResearchResult(
                results=results,
                confidence=confidence,
                strategy_used=strategy_name,
                search_time=search_time,
                sources_count=len(results),
                quality_score=quality_score,
                metadata={"profile": profile, "timestamp": time.time()}
            )
            
        except Exception as e:
            logger.error(f"Strategy {strategy_name} failed: {e}")
            return None
    
    def _execute_parallel_strategies(self, query: str, k: int, strategies: List[Dict],
                                   profile: QueryProfile, max_time: float) -> List[Optional[ResearchResult]]:
        """Führt mehrere Strategien parallel aus"""
        
        results = []
        
        with ThreadPoolExecutor(max_workers=min(len(strategies), 3)) as executor:
            future_to_strategy = {
                executor.submit(self._execute_single_strategy, query, k, s["name"], profile): s["name"]
                for s in strategies
            }
            
            for future in as_completed(future_to_strategy, timeout=max_time):
                try:
                    result = future.result(timeout=2.0)
                    results.append(result)
                except Exception as e:
                    strategy_name = future_to_strategy[future]
                    logger.warning(f"Parallel strategy {strategy_name} failed: {e}")
                    results.append(None)
        
        self.performance_stats["parallel_strategies_used"] += len([r for r in results if r])
        return results
    
    def _execute_specialized_search(self, query: str, k: int, profile: QueryProfile,
                                   max_time: float) -> Optional[ResearchResult]:
        """Führt spezialisierte Suche für schwierige Queries durch"""
        
        try:
            # Multi-Query Expansion für bessere Coverage
            expanded_queries = self._expand_query(query, profile)
            
            all_results = []
            
            for expanded_query in expanded_queries[:3]:  # Max 3 erweiterte Queries
                try:
                    # Combination of RAG and Web with expanded query
                    rag_results = self._search_rag_only(expanded_query, k//2, profile)
                    web_results = self._search_web_only(expanded_query, k//2)
                    
                    all_results.extend(rag_results)
                    all_results.extend(web_results)
                    
                except Exception as e:
                    logger.warning(f"Expanded query search failed: {e}")
                    continue
            
            # Deduplizierung und Ranking
            deduplicated_results = self._deduplicate_and_rank(all_results, query, k)
            
            search_time = max_time * 0.8  # Estimate
            quality_score = self._calculate_quality_score(deduplicated_results, query, profile)
            confidence = self._determine_confidence(quality_score, len(deduplicated_results), search_time)
            
            return ResearchResult(
                results=deduplicated_results,
                confidence=confidence,
                strategy_used="specialized_multi_query",
                search_time=search_time,
                sources_count=len(deduplicated_results),
                quality_score=quality_score,
                metadata={"expanded_queries": expanded_queries, "profile": profile}
            )
            
        except Exception as e:
            logger.error(f"Specialized search failed: {e}")
            return None
    
    def _search_rag_only(self, query: str, k: int, profile: QueryProfile) -> List[Dict[str, Any]]:
        """RAG-only Suche mit Profil-Optimierung"""
        try:
            # Strukturierte Daten bevorzugen falls gewünscht
            if profile.structure_preference > 0.7:
                prefer_types = {"table", "kg"}
                boost_factor = 0.2
            else:
                prefer_types = None
                boost_factor = 0.0
            
            if hasattr(self.rag_store, 'search') and callable(self.rag_store.search):
                if prefer_types:
                    results = self.rag_store.search(
                        query, k=k, prefer_types=prefer_types, boost_factor=boost_factor
                    )
                else:
                    results = self.rag_store.search(query, k=k)
                
                return results if isinstance(results, list) else []
            
        except Exception as e:
            logger.warning(f"RAG search failed: {e}")
        
        return []
    
    def _search_web_only(self, query: str, k: int) -> List[Dict[str, Any]]:
        """Web-only Suche (simuliert)"""
        # Hier würde die echte Web-Suche implementiert
        # Für jetzt simulieren wir Ergebnisse
        
        web_results = []
        for i in range(min(k, 5)):
            web_results.append({
                "content": f"Web-Ergebnis {i+1} für '{query}': Simulierte Web-Suche...",
                "metadata": {
                    "type": "web",
                    "source": f"web_{i+1}",
                    "url": f"https://example.com/search?q={query}&result={i+1}",
                    "title": f"Web-Ergebnis {i+1}"
                },
                "score": 0.8 - (i * 0.1)
            })
        
        return web_results
    
    def _search_enhanced_rag(self, query: str, k: int, profile: QueryProfile) -> List[Dict[str, Any]]:
        """Enhanced RAG mit strukturiertem Boosting"""
        results = self._search_rag_only(query, k, profile)
        
        # Zusätzliches Boosting für strukturierte Inhalte
        for result in results:
            if result.get("metadata", {}).get("type") in ["table", "kg"]:
                result["score"] = result.get("score", 0.0) + 0.15
        
        # Re-sortieren nach Score
        results.sort(key=lambda x: x.get("score", 0.0), reverse=True)
        return results[:k]
    
    def _search_balanced_hybrid(self, query: str, k: int, profile: QueryProfile) -> List[Dict[str, Any]]:
        """Balanced Hybrid: 60% RAG, 40% Web"""
        rag_count = max(1, int(k * 0.6))
        web_count = max(1, k - rag_count)
        
        rag_results = self._search_rag_only(query, rag_count, profile)
        web_results = self._search_web_only(query, web_count)
        
        # Kombinieren und nach Score sortieren
        all_results = rag_results + web_results
        all_results.sort(key=lambda x: x.get("score", 0.0), reverse=True)
        
        return all_results[:k]
    
    def _search_web_focused_hybrid(self, query: str, k: int, profile: QueryProfile) -> List[Dict[str, Any]]:
        """Web-focused Hybrid: 30% RAG, 70% Web"""
        rag_count = max(1, int(k * 0.3))
        web_count = max(1, k - rag_count)
        
        rag_results = self._search_rag_only(query, rag_count, profile)
        web_results = self._search_web_only(query, web_count)
        
        # Web-Ergebnisse bevorzugen für zeitkritische Queries
        if profile.time_sensitivity > 0.7:
            for result in web_results:
                result["score"] = result.get("score", 0.0) + 0.1
        
        all_results = rag_results + web_results
        all_results.sort(key=lambda x: x.get("score", 0.0), reverse=True)
        
        return all_results[:k]
    
    def _expand_query(self, query: str, profile: QueryProfile) -> List[str]:
        """Erweitert Query für bessere Coverage"""
        expanded = [query]  # Original immer dabei
        
        # Basierend auf Komplexität und Typ erweitern
        if profile.complexity in [ResearchComplexity.COMPLEX, ResearchComplexity.EXPERT]:
            expanded.append(f"{query} explanation")
            expanded.append(f"{query} details")
        
        if profile.factual_vs_analytical > 0.6:
            expanded.append(f"{query} analysis")
            expanded.append(f"why {query}")
        
        if "technology" in profile.domains:
            expanded.append(f"{query} implementation")
        
        return expanded
    
    def _deduplicate_and_rank(self, results: List[Dict], query: str, k: int) -> List[Dict[str, Any]]:
        """Dedupliziert und rankt Ergebnisse"""
        
        # Einfache Deduplizierung basierend auf Content-Ähnlichkeit
        deduplicated = []
        seen_content = set()
        
        for result in results:
            content = result.get("content", "")
            content_hash = hashlib.md5(content[:200].encode()).hexdigest()
            
            if content_hash not in seen_content:
                seen_content.add(content_hash)
                deduplicated.append(result)
        
        # Nach Score sortieren
        deduplicated.sort(key=lambda x: x.get("score", 0.0), reverse=True)
        
        return deduplicated[:k]
    
    def _calculate_quality_score(self, results: List[Dict], query: str, 
                                profile: QueryProfile) -> float:
        """Berechnet Qualitätsscore für Ergebnisse"""
        
        if not results:
            return 0.0
        
        # Basis-Score: Durchschnitt der Einzelscores
        scores = [r.get("score", 0.0) for r in results]
        avg_score = sum(scores) / len(scores) if scores else 0.0
        
        # Anzahl-Bonus (mehr Ergebnisse = besser, aber mit abnehmender Rendite)
        count_bonus = min(0.2, len(results) * 0.02)
        
        # Diversitäts-Bonus (verschiedene Quellen-Typen)
        source_types = set(r.get("metadata", {}).get("type", "unknown") for r in results)
        diversity_bonus = min(0.15, len(source_types) * 0.05)
        
        # Struktur-Bonus falls gewünscht
        structure_bonus = 0.0
        if profile.structure_preference > 0.7:
            structured_count = sum(1 for r in results 
                                 if r.get("metadata", {}).get("type") in ["table", "kg"])
            structure_bonus = min(0.1, structured_count * 0.03)
        
        total_score = avg_score + count_bonus + diversity_bonus + structure_bonus
        return min(1.0, total_score)
    
    def _determine_confidence(self, quality_score: float, result_count: int, 
                            search_time: float) -> ResearchConfidence:
        """Bestimmt Konfidenz-Level basierend auf Ergebnis-Qualität"""
        
        if quality_score >= 0.8 and result_count >= 5:
            return ResearchConfidence.EXCELLENT
        elif quality_score >= 0.7 and result_count >= 3:
            return ResearchConfidence.HIGH
        elif quality_score >= 0.5 and result_count >= 2:
            return ResearchConfidence.MEDIUM
        else:
            return ResearchConfidence.LOW
    
    def _get_cache_key(self, query: str, k: int) -> str:
        """Erstellt Cache-Key für Query"""
        query_hash = hashlib.md5(f"{query}_{k}".encode()).hexdigest()
        return f"research_{query_hash}"
    
    def _get_from_cache(self, cache_key: str) -> Optional[ResearchResult]:
        """Holt Ergebnis aus Cache"""
        if cache_key in self.search_cache:
            cached_data = self.search_cache[cache_key]
            if time.time() - cached_data["timestamp"] < self.cache_ttl:
                result: ResearchResult = cached_data["result"]
                return result
            else:
                del self.search_cache[cache_key]
        return None
    
    def _update_cache(self, cache_key: str, result: ResearchResult) -> None:
        """Aktualisiert Cache mit neuem Ergebnis"""
        self.search_cache[cache_key] = {
            "result": result,
            "timestamp": time.time()
        }
        
        # Cache-Größe begrenzen
        if len(self.search_cache) > 100:
            oldest_key = min(self.search_cache.keys(), 
                           key=lambda k: self.search_cache[k]["timestamp"])
            del self.search_cache[oldest_key]
    
    def _update_performance_stats(self, search_time: float) -> None:
        """Aktualisiert Performance-Statistiken"""
        old_avg = self.performance_stats["average_response_time"]
        count = self.performance_stats["queries_processed"]
        
        # Laufender Durchschnitt
        new_avg = (old_avg * (count - 1) + search_time) / count if count > 0 else search_time
        self.performance_stats["average_response_time"] = new_avg
    
    def _create_fallback_result(self, query: str, k: int) -> ResearchResult:
        """Erstellt Fallback-Ergebnis wenn alle Strategien fehlschlagen"""
        return ResearchResult(
            results=[],
            confidence=ResearchConfidence.LOW,
            strategy_used="fallback",
            search_time=0.0,
            sources_count=0,
            quality_score=0.0,
            metadata={"fallback": True, "query": query}
        )
    
    def get_performance_stats(self) -> Dict[str, Any]:
        """Gibt Performance-Statistiken zurück"""
        return self.performance_stats.copy()
    
    def clear_cache(self) -> None:
        """Leert den Search-Cache"""
        self.search_cache.clear()
        logger.info("🗑️ Search cache cleared")

# Factory-Funktion
def create_optimized_research_engine(rag_store, tool_manager=None, **kwargs) -> OptimizedResearchEngine:
    """Erstellt optimierte Research-Engine"""
    return OptimizedResearchEngine(rag_store, tool_manager, **kwargs)

# Convenience-Funktion
def optimized_research(rag_store, query: str, k: int = 10, **kwargs) -> ResearchResult:
    """
    Führt optimierte Research durch
    
    Args:
        rag_store: RAG Store Instanz
        query: Suchanfrage
        k: Anzahl Ergebnisse
        **kwargs: Weitere Parameter
    
    Returns:
        ResearchResult mit besten Ergebnissen
    """
    engine = create_optimized_research_engine(rag_store)
    return engine.research(query, k, **kwargs)

