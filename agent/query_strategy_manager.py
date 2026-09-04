"""
Query Strategy Manager für Adaptive RAG
========================================

Intelligente Query-Analyse und Strategie-Auswahl.

Features:
- Query Complexity Analysis
- Adaptive Strategy Selection (Keyword/Semantic/Hybrid/Multi-Query)
- Intelligent Query Refinement
- Dynamic k-Value Optimization

Note (2025): Sub-query generation moved to ``agent.decomposition_engine``
(SOTA Variant 4). The ``sub_queries`` field on ``QueryStrategy`` is kept
for backward compatibility but is no longer populated here.

Author: Implementation 2025-10-09
"""

from __future__ import annotations
from typing import List, Dict, Any, Optional, Tuple, Callable
from dataclasses import dataclass
from enum import Enum
import logging
import re
import json

logger = logging.getLogger(__name__)


class QueryComplexity(Enum):
    """Query Komplexitäts-Level"""
    SIMPLE = "simple"              # Einfache Fakten-Frage (Wer? Was? Wann?)
    MODERATE = "moderate"          # Standard-Frage
    COMPLEX = "complex"            # Multi-faceted Question
    VERY_COMPLEX = "very_complex"  # Requires decomposition


class SearchStrategy(Enum):
    """Such-Strategien"""
    KEYWORD = "keyword"            # Keyword-basierte Suche
    SEMANTIC = "semantic"          # Embedding-basierte Suche
    HYBRID = "hybrid"              # Kombination aus beiden
    MULTI_QUERY = "multi_query"    # Multiple Sub-Queries
    ITERATIVE = "iterative"        # Iterative Refinement


@dataclass
class QueryStrategy:
    """Vollständige Query-Strategie"""
    original_query: str
    refined_query: str
    complexity: QueryComplexity
    strategy: SearchStrategy
    confidence: float
    reasoning: str
    recommended_k: int
    sub_queries: Optional[List[str]] = None
    metadata: Optional[Dict[str, Any]] = None


class QueryAnalyzer:
    """Analysiert Query-Komplexität (rule-based)"""
    
    def analyze_complexity(self, query: str) -> Tuple[QueryComplexity, Dict[str, Any]]:
        """
        Analysiert Query-Komplexität
        
        Returns:
            (complexity, analysis_metadata)
        """
        metadata = {}
        
        # 1. Basis-Metriken
        word_count = len(query.split())
        char_count = len(query)
        question_marks = query.count('?')
        
        metadata['word_count'] = word_count
        metadata['char_count'] = char_count
        metadata['question_marks'] = question_marks
        
        # 2. Pattern Detection
        has_and_or = bool(re.search(r'\b(and|or|sowie|oder|außerdem|additionally)\b', query, re.IGNORECASE))
        has_why_how = bool(re.search(r'\b(why|how|warum|wie|wieso|weshalb)\b', query, re.IGNORECASE))
        has_compare = bool(re.search(r'\b(compare|unterschied|vergleich|vs|versus|gegenüber)\b', query, re.IGNORECASE))
        has_multiple_topics = query.count(',') > 2 or query.count(';') > 1
        has_conditional = bool(re.search(r'\b(if|when|falls|wenn|sofern)\b', query, re.IGNORECASE))
        has_negation = bool(re.search(r'\b(not|keine?|nicht)\b', query, re.IGNORECASE))
        
        metadata['has_and_or'] = has_and_or
        metadata['has_why_how'] = has_why_how
        metadata['has_compare'] = has_compare
        metadata['has_multiple_topics'] = has_multiple_topics
        metadata['has_conditional'] = has_conditional
        metadata['has_negation'] = has_negation
        
        # 3. Complexity Scoring
        complexity_score = 0
        
        # Word count contribution
        if word_count > 30:
            complexity_score += 3
        elif word_count > 15:
            complexity_score += 2
        elif word_count > 8:
            complexity_score += 1
        
        # Pattern contributions
        if question_marks > 1:
            complexity_score += 2
        if has_and_or:
            complexity_score += 1
        if has_why_how:
            complexity_score += 2  # Why/How questions are inherently complex
        if has_compare:
            complexity_score += 2
        if has_multiple_topics:
            complexity_score += 2
        if has_conditional:
            complexity_score += 1
        if has_negation:
            complexity_score += 1
        
        metadata['complexity_score'] = complexity_score
        
        # 4. Map to Complexity Level
        if complexity_score >= 8:
            complexity = QueryComplexity.VERY_COMPLEX
        elif complexity_score >= 5:
            complexity = QueryComplexity.COMPLEX
        elif complexity_score >= 2:
            complexity = QueryComplexity.MODERATE
        else:
            complexity = QueryComplexity.SIMPLE
        
        logger.debug(f"Query Complexity: {complexity.value} (Score: {complexity_score})")
        
        return complexity, metadata


class QueryRefiner:
    """Verfeinert Queries für bessere RAG-Ergebnisse"""
    
    def __init__(self, llm_callable: Optional[Callable] = None):
        """
        Args:
            llm_callable: Optional LLM für intelligente Refinement
        """
        self.llm = llm_callable
    
    def refine(
        self,
        query: str,
        complexity: QueryComplexity,
        use_llm: bool = True
    ) -> Tuple[str, str]:
        """
        Verfeinert Query
        
        Returns:
            (refined_query, refinement_reasoning)
        """
        # 1. Basic Refinement (rule-based)
        refined = self._basic_refinement(query)
        
        # 2. LLM-based Refinement (für komplexe Queries)
        if use_llm and self.llm and complexity in [QueryComplexity.COMPLEX, QueryComplexity.VERY_COMPLEX]:
            try:
                refined, reasoning = self._llm_refinement(refined, complexity)
                return refined, reasoning
            except Exception as e:
                logger.warning(f"LLM Refinement fehlgeschlagen: {e}, nutze Basic Refinement")
        
        return refined, "Basic rule-based refinement"
    
    def _basic_refinement(self, query: str) -> str:
        """Basic Query Refinement (rule-based)"""
        refined = query.strip()
        
        # 1. Entferne übermäßige Whitespaces
        refined = ' '.join(refined.split())
        
        # 2. Normalisiere Fragezeichen
        refined = re.sub(r'\?+', '?', refined)
        
        # 3. Füge Fragezeichen hinzu wenn fehlt (bei Fragewörtern)
        if re.match(r'^(wh(o|at|en|ere|y|ich)|how|was|wer|wo|wann|warum|wie)\b', refined, re.IGNORECASE):
            if not refined.endswith('?'):
                refined += '?'
        
        return refined
    
    def _llm_refinement(
        self,
        query: str,
        complexity: QueryComplexity
    ) -> Tuple[str, str]:
        """LLM-based Query Refinement"""
        prompt = f"""Du bist ein Query-Optimierungs-Experte. Verbessere die folgende Frage für bessere Suchergebnisse.

Original-Frage: "{query}"
Komplexität: {complexity.value}

Aufgaben:
1. Mache die Frage präziser und spezifischer
2. Erweitere mit relevanten Keywords
3. Normalisiere Fachbegriffe
4. Halte die ursprüngliche Intention

Antworte NUR mit JSON:
{{
    "refined_query": "Verbesserte Frage hier",
    "reasoning": "Kurze Begründung der Änderungen"
}}"""
        
        try:
            if self.llm is None:
                raise RuntimeError("LLM not available")
            response = self.llm(prompt, max_tokens=200)
            
            # Parse JSON
            response_clean = response.strip()
            if "```json" in response_clean:
                response_clean = response_clean.split("```json")[1].split("```")[0]
            elif "```" in response_clean:
                response_clean = response_clean.split("```")[1].split("```")[0]
            
            data = json.loads(response_clean.strip())
            
            refined = data.get("refined_query", query)
            reasoning = data.get("reasoning", "LLM refinement")
            
            return refined, reasoning
            
        except Exception as e:
            logger.warning(f"JSON Parsing fehlgeschlagen: {e}")
            return query, "LLM refinement failed"


class StrategySelector:
    """Wählt optimale Such-Strategie basierend auf Query"""
    
    def __init__(self, llm_callable: Optional[Callable] = None):
        """
        Args:
            llm_callable: Optional LLM für intelligente Strategie-Auswahl
        """
        self.llm = llm_callable
    
    def select_strategy(
        self,
        query: str,
        complexity: QueryComplexity,
        use_llm: bool = True
    ) -> Tuple[SearchStrategy, float, str]:
        """
        Wählt optimale Such-Strategie
        
        Returns:
            (strategy, confidence, reasoning)
        """
        # 1. Rule-based Fallback
        fallback_strategy = self._rule_based_selection(complexity)
        
        # 2. LLM-based Selection (wenn verfügbar)
        if use_llm and self.llm:
            try:
                return self._llm_based_selection(query, complexity)
            except Exception as e:
                logger.warning(f"LLM Strategy Selection fehlgeschlagen: {e}, nutze Rule-based")
        
        return fallback_strategy
    
    def _rule_based_selection(
        self,
        complexity: QueryComplexity
    ) -> Tuple[SearchStrategy, float, str]:
        """Rule-based Strategy Selection (Fallback)"""
        mapping = {
            QueryComplexity.SIMPLE: (SearchStrategy.KEYWORD, 0.7, "Simple query → Keyword search"),
            QueryComplexity.MODERATE: (SearchStrategy.HYBRID, 0.6, "Moderate query → Hybrid search"),
            QueryComplexity.COMPLEX: (SearchStrategy.HYBRID, 0.6, "Complex query → Hybrid search"),
            QueryComplexity.VERY_COMPLEX: (SearchStrategy.MULTI_QUERY, 0.5, "Very complex → Multi-query"),
        }
        return mapping.get(complexity, (SearchStrategy.HYBRID, 0.5, "Default: Hybrid"))
    
    def _llm_based_selection(
        self,
        query: str,
        complexity: QueryComplexity
    ) -> Tuple[SearchStrategy, float, str]:
        """LLM-based Strategy Selection"""
        prompt = f"""Du bist ein Such-Strategie-Experte. Wähle die beste Strategie für diese Query.

Query: "{query}"
Komplexität: {complexity.value}

Strategien:
1. keyword: Keyword-basierte Suche (für einfache Fakten: Wer? Was? Wann?)
2. semantic: Embedding-basierte Suche (für konzeptuelle Fragen: Warum? Wie funktioniert?)
3. hybrid: Kombination aus Keyword + Semantic (für Standard-Fragen)
4. multi_query: Zerlege in mehrere Sub-Queries (für komplexe Multi-Aspekt-Fragen)
5. iterative: Iterative Verfeinerung (für sehr komplexe Analysen)

Antworte NUR mit JSON:
{{
    "strategy": "keyword|semantic|hybrid|multi_query|iterative",
    "confidence": 0.0-1.0,
    "reasoning": "Kurze Begründung"
}}"""
        
        try:
            if self.llm is None:
                raise RuntimeError("LLM not available")
            response = self.llm(prompt, max_tokens=150)
            
            # Parse JSON
            response_clean = response.strip()
            if "```json" in response_clean:
                response_clean = response_clean.split("```json")[1].split("```")[0]
            elif "```" in response_clean:
                response_clean = response_clean.split("```")[1].split("```")[0]
            
            data = json.loads(response_clean.strip())
            
            # Map to Enum
            strategy_map = {
                "keyword": SearchStrategy.KEYWORD,
                "semantic": SearchStrategy.SEMANTIC,
                "hybrid": SearchStrategy.HYBRID,
                "multi_query": SearchStrategy.MULTI_QUERY,
                "iterative": SearchStrategy.ITERATIVE
            }
            
            strategy = strategy_map.get(data.get("strategy", "hybrid"), SearchStrategy.HYBRID)
            confidence = float(data.get("confidence", 0.5))
            reasoning = str(data.get("reasoning", "LLM decision"))
            
            return strategy, confidence, reasoning
            
        except Exception as e:
            logger.warning(f"LLM Strategy Selection Parsing fehlgeschlagen: {e}")
            return self._rule_based_selection(complexity)


class SubQueryGenerator:
    """DEPRECATED — sub-query generation now lives in
    ``agent.decomposition_engine.DecompositionEngine``.

    Kept as an empty stub purely to surface a clear ImportError if some
    external caller still references it after the SOTA Variant 4 refactor.
    Will be deleted in a follow-up commit once the ecosystem is verified.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:  # pragma: no cover
        raise RuntimeError(
            "SubQueryGenerator was removed. Use "
            "agent.decomposition_engine.DecompositionEngine instead."
        )


class QueryStrategyManager:
    """
    Zentrale Query-Strategie-Verwaltung
    
    Koordiniert:
    - Complexity Analysis
    - Query Refinement
    - Strategy Selection
    - Sub-Query Generation
    - k-Value Optimization
    """
    
    def __init__(self, llm_callable: Optional[Callable] = None):
        """
        Args:
            llm_callable: LLM für intelligente Komponenten
        """
        self.llm = llm_callable
        
        # Komponenten
        self.analyzer = QueryAnalyzer()
        self.refiner = QueryRefiner(llm_callable)
        self.selector = StrategySelector(llm_callable)
        # NOTE: sub_query_gen attribute is intentionally absent.
        # Sub-query generation moved to agent.decomposition_engine.

        logger.info("✅ QueryStrategyManager initialisiert")
    
    def analyze_and_route(
        self,
        query: str,
        context: Optional[Dict[str, Any]] = None,
        use_llm: bool = True
    ) -> QueryStrategy:
        """
        Vollständige Query-Analyse und Routing
        
        Args:
            query: User Query
            context: Optional context (history, preferences)
            use_llm: Ob LLM verwendet werden soll
            
        Returns:
            QueryStrategy mit allen Entscheidungen
        """
        logger.info(f"🎯 Query Strategy Analysis: '{query[:60]}...'")
        
        # 1. Complexity Analysis
        complexity, metadata = self.analyzer.analyze_complexity(query)
        logger.info(f"   Complexity: {complexity.value} (Score: {metadata.get('complexity_score', 0)})")
        
        # 2. Query Refinement
        refined_query, refinement_reasoning = self.refiner.refine(query, complexity, use_llm)
        if refined_query != query:
            logger.info(f"   Refined: '{refined_query}'")
        
        # 3. Strategy Selection
        strategy, confidence, reasoning = self.selector.select_strategy(refined_query, complexity, use_llm)
        logger.info(f"   Strategy: {strategy.value} (Confidence: {confidence:.2f})")
        logger.debug(f"   Reasoning: {reasoning}")
        
        # 4. Determine optimal k
        recommended_k = self._determine_k(complexity, strategy)
        logger.info(f"   Recommended k: {recommended_k}")

        # 5. Sub-query generation removed — see agent.decomposition_engine.
        sub_queries = None

        return QueryStrategy(
            original_query=query,
            refined_query=refined_query,
            complexity=complexity,
            strategy=strategy,
            confidence=confidence,
            reasoning=reasoning,
            recommended_k=recommended_k,
            sub_queries=sub_queries,
            metadata=metadata
        )
    
    def _determine_k(self, complexity: QueryComplexity, strategy: SearchStrategy) -> int:
        """Bestimmt optimales k basierend auf Komplexität und Strategie"""
        # Basis k-Werte pro Komplexität
        base_k = {
            QueryComplexity.SIMPLE: 3,
            QueryComplexity.MODERATE: 6,
            QueryComplexity.COMPLEX: 10,
            QueryComplexity.VERY_COMPLEX: 15
        }
        
        k = base_k.get(complexity, 6)
        
        # Adjust für Strategie
        if strategy == SearchStrategy.MULTI_QUERY:
            # Weniger pro Sub-Query
            k = max(3, k // 2)
        elif strategy == SearchStrategy.ITERATIVE:
            # Mehr für iterative Refinement
            k = max(5, int(k * 1.2))
        elif strategy == SearchStrategy.KEYWORD:
            # Weniger für Keyword (präziser)
            k = max(3, int(k * 0.8))
        
        return min(k, 20)  # Cap bei 20


# Singleton (optional)
_strategy_manager_instance: Optional[QueryStrategyManager] = None


def get_query_strategy_manager(llm_callable: Optional[Callable] = None) -> QueryStrategyManager:
    """
    Gibt QueryStrategyManager Singleton zurück
    
    Args:
        llm_callable: LLM für intelligente Komponenten
        
    Returns:
        QueryStrategyManager Instance
    """
    global _strategy_manager_instance
    if _strategy_manager_instance is None:
        _strategy_manager_instance = QueryStrategyManager(llm_callable)
    return _strategy_manager_instance
