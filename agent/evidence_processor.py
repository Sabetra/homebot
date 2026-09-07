"""
Evidence Processing & Ranking für AgentOrchestrator
===================================================

Dieses Modul übernimmt:
- Evidence-Ranking (TF-IDF, Semantic Similarity)
- Scoring & Relevanz-Bewertung
- Adaptive Top-K Selektion
- Domain-Authority Scoring
- Contradiction Detection & Source Validation (NEU)

Autor: Refactored from orchestrator.py (2025-10-08)
Updated: 2025-10-09 (Contradiction Detection)
"""

from __future__ import annotations
from typing import Callable, List, Dict, Any, Tuple, Optional
from math import sqrt
import logging

# NEU: Import ContradictionDetector
from agent.contradiction_detector import ContradictionDetector

logger = logging.getLogger(__name__)

# NEU: Import Cross-Encoder Reranker
get_cross_encoder_reranker: Optional[Callable[..., Any]] = None
try:
    from agent.cross_encoder_reranker import get_cross_encoder_reranker  # type: ignore[assignment,no-redef]
    CROSS_ENCODER_AVAILABLE = True
except ImportError:
    CROSS_ENCODER_AVAILABLE = False
    logger.warning("⚠️ Cross-Encoder Reranker nicht verfügbar")


class EvidenceProcessor:
    """Verarbeitet und rankt Evidence-Quellen"""
    
    def __init__(self, llm_callable=None):
        """
        Args:
            llm_callable: Optional LLM für semantische Contradiction-Detection
        """
        # NEU: ContradictionDetector initialisieren
        self.contradiction_detector = ContradictionDetector(llm_callable=llm_callable)
        
        # NEU: Cross-Encoder Reranker
        self.cross_encoder = None
        self.use_cross_encoder = False
        
        if CROSS_ENCODER_AVAILABLE and get_cross_encoder_reranker is not None:
            try:
                self.cross_encoder = get_cross_encoder_reranker()
                self.use_cross_encoder = True
                logger.info("✅ Cross-Encoder Reranking aktiviert")
            except Exception as e:
                logger.warning(f"⚠️ Cross-Encoder nicht verfügbar: {e}")
                self.cross_encoder = None
                self.use_cross_encoder = False
        
        logger.info(f"✅ EvidenceProcessor initialisiert (Contradiction Detection: ✓, Cross-Encoder: {self.use_cross_encoder})")
    
    def rank_with_scores(self, query: str, sources: List[Any]) -> List[Tuple[Any, float]]:
        """Bewertet Sources und gibt sie mit Scores zurück"""
        return [(src, self.score_of(query, src)) for src in sources]
    
    def score_sources(self, query: str, sources: List[Any]) -> List[Any]:
        """Sortiert Sources nach Relevanz-Score"""
        scored = self.rank_with_scores(query, sources)
        sorted_sources = sorted(scored, key=lambda x: x[1], reverse=True)
        return [src for src, _ in sorted_sources]
    
    def score_of(self, query: str, src: Any) -> float:
        """Berechnet Relevanz-Score für eine Source (TF-IDF-ähnlich)"""
        query_tokens = self._tokenize(query.lower())
        
        # Content-based scoring
        content = getattr(src, 'content', '') or getattr(src, 'text', '')
        content_tokens = self._tokenize(content.lower())
        
        # Token-Overlap
        overlap_score = self._overlap(query_tokens, content_tokens)
        
        # Domain-Authority Bonus
        url = getattr(src, 'url', '')
        domain_bonus = self.domain_authority_score(url) if url else 0.0
        
        # Combine scores
        final_score = (0.7 * overlap_score) + (0.3 * domain_bonus)
        
        return final_score
    
    def adaptive_top_k(self, n_ranked: int) -> int:
        """Bestimmt dynamisches k basierend auf Anzahl verfügbarer Sources"""
        if n_ranked < 3:
            return n_ranked
        elif n_ranked < 10:
            return min(5, n_ranked)
        else:
            return min(8, n_ranked)
    
    def domain_authority_score(self, url: str) -> float:
        """Bewertet Domain-Autorität (höher = vertrauenswürdiger)"""
        high_authority = [
            "wikipedia.org", "gov", "edu", ".org",
            "nature.com", "sciencedirect.com", "arxiv.org",
            "github.com", "stackoverflow.com"
        ]
        
        medium_authority = [
            "medium.com", "towardsdatascience.com",
            "dev.to", "hackernoon.com"
        ]
        
        url_lower = url.lower()
        
        for domain in high_authority:
            if domain in url_lower:
                return 1.0
        
        for domain in medium_authority:
            if domain in url_lower:
                return 0.6
        
        return 0.3  # Unknown domain
    
    def select_diverse_top_k(
        self,
        query: str,
        judged_sources: List[Tuple[Any, Any]],
        target_k: int,
        diversity_weight: float = 0.3
    ) -> List[Any]:
        """Wählt diverse Top-K Sources (vermeidet Domain-Clustering)"""
        if not judged_sources:
            return []
        
        # Sort by score
        sorted_sources = sorted(judged_sources, key=lambda x: x[1], reverse=True)
        
        selected: List[Any] = []
        domain_counts: Dict[str, int] = {}
        
        for src, score in sorted_sources:
            if len(selected) >= target_k:
                break
            
            # Extract domain
            url = getattr(src, 'url', '')
            domain = self._domain_of(url)
            
            # Check diversity constraint
            domain_count = domain_counts.get(domain, 0)
            max_per_domain = max(1, int(target_k * diversity_weight))
            
            if domain_count < max_per_domain:
                selected.append(src)
                domain_counts[domain] = domain_count + 1
        
        # Fill up if we didn't reach target_k
        if len(selected) < target_k:
            for src, _ in sorted_sources:
                if src not in selected:
                    selected.append(src)
                    if len(selected) >= target_k:
                        break
        
        logger.info(f"[DIVERSITY] Ausgewählt: {len(selected)} Sources aus {len(sorted_sources)}")
        return selected
    
    def _domain_of(self, url: str) -> str:
        """Extrahiert Domain aus URL"""
        if not url or "://" not in url:
            return "unknown"
        try:
            domain_part = url.split("://")[1].split("/")[0]
            parts = domain_part.split(".")
            if len(parts) >= 2:
                return ".".join(parts[-2:])
            return domain_part
        except Exception as e:
            logger.warning(f"Domain-Extraction fehlgeschlagen für {url}: {e}")
            return "unknown"
    
    def _tokenize(self, text: str) -> List[str]:
        """Simple Tokenization (Whitespace + Lowercase)"""
        return text.lower().split()
    
    def _overlap(self, tokens1: List[str], tokens2: List[str]) -> float:
        """Berechnet Token-Overlap (Jaccard-ähnlich)"""
        if not tokens1 or not tokens2:
            return 0.0
        
        set1 = set(tokens1)
        set2 = set(tokens2)
        
        intersection = len(set1 & set2)
        union = len(set1 | set2)
        
        return intersection / union if union > 0 else 0.0
    
    # ==================== NEU: CONTRADICTION DETECTION ====================
    
    def validate_and_filter_contradictions(
        self,
        sources: List[Any],
        query: str,
        use_llm: bool = False
    ) -> Tuple[List[Any], Dict[str, Any]]:
        """
        Validiert Sources auf Widersprüche und filtert unzuverlässige Quellen
        
        Args:
            sources: Liste von Source-Objekten
            query: Original-Query
            use_llm: Ob LLM für semantische Prüfung verwendet werden soll
        
        Returns:
            (filtered_sources, validation_report)
        """
        if len(sources) < 2:
            return sources, {"contradictions": 0, "removed": 0}
        
        logger.info(f"[VALIDATION] Prüfe {len(sources)} Sources auf Widersprüche")
        
        # Schritt 1: Widersprüche erkennen
        contradictions = self.contradiction_detector.detect_contradictions(
            evidences=sources,
            query=query,
            use_llm=use_llm
        )
        
        # Schritt 2: Widersprüche auflösen
        if contradictions:
            filtered_sources, resolution_report = self.contradiction_detector.resolve_contradictions(
                contradictions=contradictions,
                evidences=sources
            )
            
            # Schritt 3: Reliability-Tracking aktualisieren
            for source in sources:
                had_contradiction = source not in filtered_sources
                self.contradiction_detector.update_reliability(source, had_contradiction)
            
            validation_report = {
                "contradictions_found": len(contradictions),
                "sources_removed": len(sources) - len(filtered_sources),
                "sources_kept": len(filtered_sources),
                "resolution_details": resolution_report
            }
            
            logger.info(f"[VALIDATION] Widersprüche: {len(contradictions)}, Entfernt: {validation_report['sources_removed']}")
            return filtered_sources, validation_report
        
        else:
            # Keine Widersprüche - alle Sources validiert
            for source in sources:
                self.contradiction_detector.update_reliability(source, had_contradiction=False)
            
            return sources, {
                "contradictions_found": 0,
                "sources_removed": 0,
                "sources_kept": len(sources),
                "all_validated": True
            }
    
    # ==================== NEU: ADVANCED RANKING WITH RERANKING ====================
    
    def rank_and_optimize(
        self,
        evidences: List[Any],
        query: str,
        target_m: int = 12,
        diversity_lambda: float = 0.7,
        use_cross_encoder: bool = True
    ) -> List[Any]:
        """
        Rankt und optimiert Evidences mit optionalem Cross-Encoder Reranking
        
        Pipeline:
        1. Initial Filtering (remove low quality)
        2. Cross-Encoder Reranking (wenn verfügbar)
        3. Diversity Selection
        4. Contradiction Detection
        
        Args:
            evidences: Liste von Evidence-Objekten
            query: Suchquery
            target_m: Ziel-Anzahl von Evidences
            diversity_lambda: Gewicht für Diversität (0-1)
            use_cross_encoder: Ob Cross-Encoder verwendet werden soll
        
        Returns:
            Optimierte und gerankte Evidence-Liste
        """
        if not evidences:
            return []
        
        logger.info(f"[RANK] Start: {len(evidences)} Evidences")
        
        # Step 1: Initial Quality Filter (remove very low scores)
        # ★ SOTA: Increased threshold from 0.1 → 0.25
        # A score of 0.1 is barely above random; 0.25 ensures basic relevance.
        filtered_evidences = [
            ev for ev in evidences
            if getattr(ev, 'score', 0) > 0.25  # Mindest-Score (SOTA: erhöht von 0.1)
        ]
        
        # Fallback: if strict filter removes everything, keep top 3 by score
        if not filtered_evidences and evidences:
            sorted_ev = sorted(evidences, key=lambda e: getattr(e, 'score', 0), reverse=True)
            filtered_evidences = sorted_ev[:3]
        
        logger.info(f"[RANK] Nach Quality Filter: {len(filtered_evidences)} Evidences")
        
        if not filtered_evidences:
            return []
        
        # Step 2: Cross-Encoder Reranking (wenn aktiviert)
        if use_cross_encoder and self.use_cross_encoder and self.cross_encoder:
            try:
                logger.info("🎯 Starte Cross-Encoder Reranking...")
                
                # Prepare candidates
                candidates = [
                    {
                        'content': getattr(ev, 'content', '') or getattr(ev, 'text', ''),
                        'score': getattr(ev, 'score', 0.0),
                        'source': getattr(ev, 'source', 'unknown')
                    }
                    for ev in filtered_evidences
                ]
                
                # Rerank (mehr Kandidaten für Diversity Selection)
                # ★ SOTA: score_threshold raised from 0.0 → 0.05
                rerank_result = self.cross_encoder.rerank(
                    query=query,
                    candidates=candidates,
                    top_k=target_m * 2,  # Doppelt für Diversity
                    score_threshold=0.05
                )
                
                # Apply reranked order
                reranked_evidences = [
                    filtered_evidences[idx] for idx in rerank_result.reranked_indices
                ]
                
                # Update scores mit Reranker scores
                for i, ev in enumerate(reranked_evidences):
                    if i < len(rerank_result.scores):
                        # Combine: 70% Reranker + 30% Original
                        original_score = getattr(ev, 'score', 0.0)
                        reranker_score = rerank_result.scores[i]
                        ev.score = 0.7 * reranker_score + 0.3 * original_score
                
                logger.info(
                    f"✅ Reranking complete: {len(filtered_evidences)} → "
                    f"{len(reranked_evidences)} in {rerank_result.processing_time_ms:.1f}ms"
                )
                
                # ★ SOTA SAFETY NET: If reranking dropped ALL evidences
                # (e.g. all below score_threshold), keep the original best.
                # This prevents cascading grounding failures.
                if not reranked_evidences and filtered_evidences:
                    reranked_evidences = filtered_evidences[:1]
                    logger.warning(
                        f"⚠️ Reranking returned 0 evidences — keeping best "
                        f"pre-reranking candidate as fallback"
                    )
                
                filtered_evidences = reranked_evidences
                
            except Exception as e:
                logger.warning(f"⚠️ Reranking failed: {e}, using original order")
        
        # Step 3: Diversity Selection
        # Select top candidates with diversity
        if len(filtered_evidences) > target_m:
            # Use diversity lambda
            diverse_evidences = self._select_diverse_evidences(
                evidences=filtered_evidences,
                target_k=target_m,
                diversity_weight=diversity_lambda
            )
        else:
            diverse_evidences = filtered_evidences[:target_m]
        
        logger.info(f"[RANK] Nach Diversity Selection: {len(diverse_evidences)} Evidences")
        
        # Step 4: Contradiction Detection (optional)
        # Can be called separately by orchestrator
        
        logger.info(f"[RANK] Final: {len(diverse_evidences)} Evidences")
        return diverse_evidences
    
    def _select_diverse_evidences(
        self,
        evidences: List[Any],
        target_k: int,
        diversity_weight: float = 0.7
    ) -> List[Any]:
        """
        Selektiert diverse Evidences (vermeidet zu viele aus gleicher Source)
        
        Args:
            evidences: Bereits sortierte Evidences (nach Score)
            target_k: Anzahl zu selektierender Evidences
            diversity_weight: Gewicht für Diversität
        
        Returns:
            Diverse Evidences
        """
        if len(evidences) <= target_k:
            return evidences
        
        selected: List[Any] = []
        source_counts: Dict[str, int] = {}
        
        # First pass: Select with diversity constraint
        max_per_source = max(1, int(target_k * (1 - diversity_weight)))
        
        for ev in evidences:
            if len(selected) >= target_k:
                break
            
            source = getattr(ev, 'source', 'unknown')
            source_count = source_counts.get(source, 0)
            
            if source_count < max_per_source:
                selected.append(ev)
                source_counts[source] = source_count + 1
        
        # Fill up if needed
        if len(selected) < target_k:
            for ev in evidences:
                if ev not in selected:
                    selected.append(ev)
                    if len(selected) >= target_k:
                        break
        
        return selected[:target_k]
