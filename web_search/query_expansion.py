"""
Query Expansion
================

SOTA multi-query generation for web search.
Generates alternative query formulations to improve recall.

SOTA References:
    - RAG-Fusion (Raudaschl, 2023): Multi-query generation + RRF
    - Reciprocal Rank Fusion for result merging

Author: SOTA Web Search Upgrade
Date: 2026-03-08
"""

import logging
import re
from typing import List, Optional, Any

logger = logging.getLogger(__name__)


class QueryExpander:
    """
    Expands a single search query into multiple alternative formulations.
    
    Methods:
    1. LLM-based multi-query (wenn model_loader verfügbar)
    2. Rule-based keyword expansion (Fallback, kein LLM nötig)
    
    The expanded queries capture different aspects and phrasings
    to improve search recall (more relevant results found).
    """
    
    def __init__(self, model_loader: Optional[Any] = None) -> None:
        """
        Args:
            model_loader: LLM model loader for LLM-based expansion.
                         If None, only rule-based expansion is used.
        """
        self._model_loader = model_loader
        logger.debug(f"QueryExpander initialized (LLM: {'Yes' if model_loader else 'No'})")
    
    def expand(self, query: str, max_expansions: int = 2) -> List[str]:
        """
        Generate expanded queries.
        
        Args:
            query: Original search query
            max_expansions: Maximum number of additional queries
            
        Returns:
            List of expanded queries (NOT including original).
            Empty list if expansion fails or is not useful.
        """
        if not query or len(query) < 5:
            return []
        
        # Try LLM-based expansion first
        if self._model_loader and hasattr(self._model_loader, 'generate_response'):
            try:
                expanded = self._llm_expand(query, max_expansions)
                if expanded:
                    return expanded
            except Exception as e:
                logger.debug(f"LLM expansion failed, using rules: {e}")
        
        # Fallback: rule-based expansion
        return self._rule_based_expand(query, max_expansions)
    
    # CoT skip patterns for magistral reasoning model
    _COT_PREFIXES = (
        "okay", "beispiel", "fokus", "original", "alternative", "hier",
        "suchbegriff", "die ", "eine ", "jetzt", "erster", "zweiter",
        "dritter", "dritte ", "aspekt", "vielleicht", "also", "ich ",
        "nun ", "das ", "der ", "diese", "zunächst", "schauen",
        "betrachten", "man ", "oder ", "und ", "wir ",
    )
    
    def _llm_expand(self, query: str, max_expansions: int) -> List[str]:
        """
        Generate alternative queries using LLM.
        
        Handles magistral reasoning model's CoT output by:
        1. Extracting clean numbered lines
        2. Falling back to quoted strings within reasoning
        """
        prompt = f"""Erstelle {max_expansions} alternative Suchanfragen für eine Suchmaschine.

Originalfrage: {query}

Antworte NUR mit {max_expansions} Suchanfragen, eine pro Zeile, nummeriert:
1."""
        
        response = self._model_loader.generate_response(
            prompt,
            max_tokens=150,
            temperature=0.4,
        )
        
        if not response:
            return []
        
        return self._parse_query_response(response, query, max_expansions)
    
    def _parse_query_response(self, response: str, original_query: str, max_results: int) -> List[str]:
        """
        Robustly parse expanded queries from LLM response.
        
        Handles CoT reasoning by extracting actual search terms via:
        1. Clean numbered lines
        2. Quoted strings within reasoning
        """
        def is_search_query(text: str) -> bool:
            if not text or len(text) < 5 or len(text) > 80:
                return False
            if text.lower().startswith(self._COT_PREFIXES):
                return False
            if ":" in text[:15] or "**" in text:
                return False
            if len(text.split()) > 10:
                return False
            if any(w in text.lower() for w in ["vielleicht", "könnte", "brauche", "prüfe"]):
                return False
            if text.lower().strip() == original_query.lower().strip():
                return False
            return True
        
        queries = []
        lines = [l.strip() for l in response.strip().split("\n") if l.strip()]
        
        # Method 1: Try to extract clean numbered lines
        for line in lines:
            cleaned = re.sub(r'^[\d\.\-\)\]\*\#\>\s]+', '', line).strip()
            cleaned = cleaned.strip('"\'`*?')
            if is_search_query(cleaned):
                queries.append(cleaned)
        
        # Method 2: If not enough, extract quoted strings from CoT
        if len(queries) < max_results:
            quoted = re.findall(r'"([^"]{5,80})"', response)
            for q in quoted:
                q = q.strip()
                if is_search_query(q) and q not in queries:
                    queries.append(q)
        
        return queries[:max_results]
    
    def _rule_based_expand(self, query: str, max_expansions: int) -> List[str]:
        """
        Simple rule-based query expansion.
        
        Strategies:
        1. Language flip (DE ↔ EN for technical terms)
        2. Add/remove question words
        3. Synonym substitution for common terms
        """
        expansions = []
        q_lower = query.lower()
        
        # Strategy 1: Add specificity keywords
        if "vergleich" in q_lower or "unterschied" in q_lower:
            expansions.append(f"{query} Vor- und Nachteile")
        
        if "wirksamkeit" in q_lower or "wirkung" in q_lower:
            expansions.append(f"{query} Studie Evidenz")
        
        if "therapie" in q_lower or "behandlung" in q_lower:
            expansions.append(f"{query} Leitlinie Empfehlung")
        
        # Strategy 2: English variant for technical/scientific queries
        en_terms = {
            "kognitive verhaltenstherapie": "cognitive behavioral therapy CBT",
            "achtsamkeit": "mindfulness",
            "neuroplastizität": "neuroplasticity",
            "angststörung": "anxiety disorder",
            "depression": "depression treatment",
            "burnout": "burnout syndrome",
            "ptbs": "PTSD",
            "emdr": "EMDR therapy effectiveness",
            "psychotherapie": "psychotherapy",
            "stressbewältigung": "stress management",
        }
        for de, en in en_terms.items():
            if de in q_lower:
                expansions.append(en)
                break
        
        # Strategy 3: Reformulate question
        question_words = ["was ist", "wie funktioniert", "welche", "warum"]
        for qw in question_words:
            if qw in q_lower:
                # Remove question word for keyword-style query
                keyword_query = q_lower.replace(qw, "").strip()
                if len(keyword_query) > 5:
                    expansions.append(keyword_query)
                break
        
        # Deduplicate and limit
        seen = {query.lower()}
        unique = []
        for exp in expansions:
            if exp.lower() not in seen:
                seen.add(exp.lower())
                unique.append(exp)
        
        return unique[:max_expansions]


def reciprocal_rank_fusion(
    result_lists: List[List[dict]],
    k: int = 60,
    url_key: str = "url",
) -> List[dict]:
    """
    Reciprocal Rank Fusion (RRF) to merge multiple ranked result lists.

    RRF Score = Σ 1/(k + rank_i) for each list where the result appears.

    Reference: Cormack et al. (2009) -- "Reciprocal Rank Fusion outperforms
    Condorcet and individual Rank Learning Methods"

    Die Mathematik lebt kanonisch in utils/rank_fusion.py
    (Workdoc: docs/WORKDOC_CODEBASE_AUDIT_20260828.md, Phase 2).
    URL-Normalisierung und First-Wins-Item bleiben unverändert;
    leere URLs werden weiterhin übersprungen.

    Args:
        result_lists: List of ranked result lists (each list is a ranking)
        k: RRF constant (default: 60, standard value)
        url_key: Key to use for result identity

    Returns:
        Fused results sorted by RRF score (highest first)
    """
    from utils.rank_fusion import reciprocal_rank_fusion as _rrf_core

    def _url_key(result: dict):
        url = (result.get(url_key, "") or "").strip().rstrip("/").lower()
        if not url:
            return None  # überspringen (Altverhalten: continue)
        return url.replace("www.", "").replace("http://", "https://")

    return [entry.item for entry in _rrf_core(result_lists, k=k, key_fn=_url_key)]


__all__ = ["QueryExpander", "reciprocal_rank_fusion"]
