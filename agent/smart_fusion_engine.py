"""
🧠 Smart Fusion Engine - Intelligent Result Fusion
==================================================

Kombiniert FAISS + KG Results intelligent:
- Score-Normalisierung
- Recency Boost
- Deduplication
- Source-Diversität
- 🆕 Feedback-basierte Gewichts-Anpassung

Author: AI Assistant
Date: 2025-10-06
Updated: 2025-10-22 (Feedback Integration)
"""

import numpy as np
from typing import Any, Dict, List, Optional, Tuple
import logging

logger = logging.getLogger(__name__)


class SmartFusionEngine:
    """
    Intelligente Fusion von FAISS + KG Results.
    
    Features:
    - Score-Normalisierung über verschiedene Quellen
    - Recency Boost (neuere Chunks leicht bevorzugen)
    - Deduplication
    - Source-Diversität (Mix aus FAISS + KG)
    - 🆕 Adaptive Gewichte basierend auf User-Feedback
    - ★ SOTA v2: Wilson-Score retrieval boost from feedback history
    """
    
    def __init__(
        self, 
        recency_boost_factor: float = 0.05,
        kg_boost_factor: float = 0.1,
        target_kg_ratio: float = 0.18,
        use_adaptive_weights: bool = True,
        debug: bool = False
    ):
        """
        Initialisiert Fusion Engine.
        
        Args:
            recency_boost_factor: Max. Boost für neueste Chunks (z.B. 0.05 = 5%)
            kg_boost_factor: Boost für KG-basierte Results
            target_kg_ratio: Ziel-Anteil KG-Results (z.B. 0.18 = 18%)
            use_adaptive_weights: Wenn True, nutze Feedback-basierte Gewichte
            debug: Enable debug logging
        """
        self.recency_boost = recency_boost_factor
        self.kg_boost = kg_boost_factor
        self.target_kg_ratio = target_kg_ratio
        self.use_adaptive_weights = use_adaptive_weights
        self.debug = debug
        
        # Für Recency Calculation
        self.chunk_id_range: Optional[tuple] = None
        
        # 🆕 Adaptive Gewichte (von FeedbackOptimizer)
        self.adaptive_faiss_weight: Optional[float] = None
        self.adaptive_kg_weight: Optional[float] = None
        
        # ★ SOTA v2: Wilson-Score utility map from feedback
        # {chunk_id_str: wilson_score} — refreshed periodically
        self._utility_scores: Dict[str, float] = {}
        self._utility_max_boost: float = 0.12  # Max +12% boost for top-rated chunks
        
        logger.info(
            f"✅ SmartFusionEngine initialized "
            f"(recency={recency_boost_factor}, kg_ratio={target_kg_ratio}, "
            f"adaptive={use_adaptive_weights})"
        )
    
    def set_adaptive_weights(self, faiss_weight: float, kg_weight: float):
        """
        🆕 Setzt adaptive Gewichte von FeedbackOptimizer
        
        Args:
            faiss_weight: Gewicht für FAISS Results (0-1)
            kg_weight: Gewicht für KG Results (0-1)
        """
        self.adaptive_faiss_weight = faiss_weight
        self.adaptive_kg_weight = kg_weight
        logger.info(
            f"🔄 Adaptive weights updated: "
            f"FAISS={faiss_weight:.2f}, KG={kg_weight:.2f}"
        )
    
    def set_utility_scores(self, utility_scores: Dict[str, float]) -> None:
        """
        ★ SOTA v2: Set Wilson-Score utility map from feedback analysis.
        
        Call this periodically (e.g., every 5 min or on feedback update)
        with the output of RAGQualityManager.compute_chunk_utility_scores().
        
        Args:
            utility_scores: {chunk_id_str: wilson_lower_bound}  (0.0 - 1.0)
        """
        self._utility_scores = utility_scores or {}
        if self.debug:
            n = len(self._utility_scores)
            if n > 0:
                avg = sum(self._utility_scores.values()) / n
                logger.debug(f"★ Wilson utility scores loaded: {n} chunks, avg={avg:.3f}")

    def _apply_wilson_boost(self, result: Dict, chunk_id) -> float:
        """
        ★ SOTA v2: Compute Wilson-Score retrieval boost for a chunk.
        
        Chunks with high user-feedback Wilson Score get a score multiplier.
        - Wilson score 0.0 → no boost
        - Wilson score 0.5 → +6% boost  
        - Wilson score 1.0 → +12% boost (max)
        
        Returns the boost factor (0.0 to _utility_max_boost).
        """
        if not self._utility_scores:
            return 0.0
        
        cid_str = str(chunk_id) if chunk_id is not None else ""
        wilson = self._utility_scores.get(cid_str, 0.0)
        
        if wilson <= 0.0:
            return 0.0
        
        # Linear mapping: wilson [0, 1] → boost [0, max_boost]
        boost = wilson * self._utility_max_boost
        
        if self.debug and boost > 0:
            logger.debug(f"★ Wilson boost chunk {cid_str}: wilson={wilson:.3f} → +{boost:.4f}")
        
        return boost
    
    def get_current_weights(self) -> Tuple[float, float]:
        """
        🆕 Gibt aktuelle Gewichte zurück (adaptiv oder default)
        
        Returns:
            (faiss_weight, kg_weight)
        """
        if (self.use_adaptive_weights and 
            self.adaptive_faiss_weight is not None and 
            self.adaptive_kg_weight is not None):
            return self.adaptive_faiss_weight, self.adaptive_kg_weight
        else:
            # Default: 70/30 (alte Logik verwendet Boost-Faktoren anders)
            return 0.7, 0.3
    
    def set_chunk_id_range(self, min_id: int, max_id: int) -> None:
        """
        Setzt Min/Max Chunk-IDs für Recency-Berechnung.
        
        Args:
            min_id: Kleinste Chunk-ID in DB
            max_id: Größte Chunk-ID in DB
        """
        self.chunk_id_range = (min_id, max_id)
        logger.debug(f"Chunk ID range: {min_id} - {max_id}")
    
    def fuse(
        self,
        faiss_results: List[Dict],
        kg_results: List[Dict],
        query_embedding: Optional[np.ndarray] = None,
        k: int = 5
    ) -> List[Dict]:
        """
        Fusioniert FAISS + KG Results intelligent.
        
        Strategy:
        1. Normalisiere Scores (0-1 Range)
        2. Recency Boost anwenden
        3. KG-Boost für Diversität
        4. Re-Ranking mit Query-Relevanz (optional)
        5. Deduplizierung
        6. Final Top-K mit Target KG-Ratio
        
        Args:
            faiss_results: Results von FAISS Search
            kg_results: Results von KG Search
            query_embedding: Optional Query-Vektor für Re-Ranking
            k: Anzahl finaler Results
        
        Returns:
            Top-K fusionierte Results
        """
        all_results = []
        seen_chunks = set()
        
        # 1. FAISS RESULTS VERARBEITEN
        for result in faiss_results:
            chunk_id = result.get('chunk_id')
            if chunk_id in seen_chunks:
                continue
            
            # Recency Boost berechnen
            recency_score = self._calculate_recency_boost(chunk_id)
            
            # ★ SOTA v2: Wilson-Score feedback boost
            wilson_boost = self._apply_wilson_boost(result, chunk_id)
            
            # Kombinierter Score
            base_score = float(result.get('score', 0.0))
            combined_score = base_score * (1.0 + recency_score + wilson_boost)
            
            all_results.append({
                **result,
                'original_score': base_score,
                'recency_boost': recency_score,
                'wilson_boost': wilson_boost,
                'combined_score': combined_score,
                'source': 'faiss'
            })
            seen_chunks.add(chunk_id)
        
        # 2. KG RESULTS VERARBEITEN
        for result in kg_results:
            chunk_id = result.get('chunk_id')
            
            # KG-Results haben eigenes Scoring
            # Normalisieren auf FAISS-Range
            base_score = float(result.get('score', 0.0))
            normalized_score = self._normalize_kg_score(base_score)
            
            # Recency + KG Boost + ★ Wilson Boost
            recency_score = self._calculate_recency_boost(chunk_id)
            wilson_boost = self._apply_wilson_boost(result, chunk_id)
            combined_score = normalized_score * (1.0 + recency_score + self.kg_boost + wilson_boost)
            
            # 🆕 HYBRID-MATCH HANDLING: Wenn Chunk bereits von FAISS gefunden wurde
            if chunk_id in seen_chunks:
                # Kombiniere Scores für besseres Ranking (Hybrid-Match = höhere Qualität!)
                for existing in all_results:
                    if existing.get('chunk_id') == chunk_id:
                        # Bonus für Hybrid-Match (beide Quellen fanden denselben Chunk)
                        hybrid_bonus = combined_score * 0.3  # 30% vom KG-Score als Bonus
                        existing['combined_score'] += hybrid_bonus
                        existing['source'] = 'hybrid'  # Markiere als Hybrid-Match
                        existing['kg_boost'] = self.kg_boost
                        existing['hybrid_bonus'] = hybrid_bonus
                        
                        # Optional: Füge KG-Metadaten hinzu
                        if 'metadata' not in existing:
                            existing['metadata'] = {}
                        if 'kg_triple' in result.get('metadata', {}):
                            existing['metadata']['kg_triple'] = result['metadata']['kg_triple']
                        
                        logger.debug(
                            f"🎯 Hybrid-Match detected for chunk {chunk_id}: "
                            f"FAISS + KG bonus = +{hybrid_bonus:.3f}"
                        )
                        break
                continue  # Weiter zum nächsten KG-Result
            
            # Neuer KG-only Match
            all_results.append({
                **result,
                'original_score': base_score,
                'normalized_score': normalized_score,
                'recency_boost': recency_score,
                'kg_boost': self.kg_boost,
                'combined_score': combined_score,
                'source': 'kg'
            })
            seen_chunks.add(chunk_id)
        
        # 3. RE-RANKING MIT QUERY-SIMILARITY (optional)
        if query_embedding is not None:
            for result in all_results:
                if 'embedding' in result and result['embedding'] is not None:
                    # Re-calculate similarity mit Query
                    query_sim = self._cosine_similarity(
                        query_embedding,
                        result['embedding']
                    )
                    # Kombiniere mit bestehendem Score (70/30 Split)
                    result['query_similarity'] = query_sim
                    result['combined_score'] = (
                        result['combined_score'] * 0.7 + query_sim * 0.3
                    )
        
        # 4. SORTIEREN NACH COMBINED SCORE
        all_results.sort(key=lambda x: x['combined_score'], reverse=True)
        
        # 5. INTELLIGENT TOP-K MIT KG-RATIO
        final_results = self._select_with_diversity(all_results, k)
        
        # 🆕 ENHANCED LOGGING mit Statistiken
        kg_count = sum(1 for r in final_results if r['source'] == 'kg')
        hybrid_count = sum(1 for r in final_results if r['source'] == 'hybrid')
        faiss_count = len(final_results) - kg_count - hybrid_count
        
        logger.debug(
            f"🧠 Fusion Results: {len(final_results)} total "
            f"(FAISS={faiss_count}, KG={kg_count}, Hybrid={hybrid_count}, "
            f"KG-ratio={kg_count/len(final_results) if final_results else 0:.1%})"
        )
        
        # 🆕 Score-Statistiken (wenn Debug aktiviert)
        if self.debug and final_results:
            scores = [r['combined_score'] for r in final_results]
            faiss_scores = [r['original_score'] for r in final_results
                            if r.get('source') in ('faiss', 'hybrid')] or [0]
            kg_scores = [r['original_score'] for r in final_results
                         if r.get('source') == 'kg'] or [0]
            
            logger.debug(
                f"📊 Score Stats:\n"
                f"  Combined: min={min(scores):.3f}, max={max(scores):.3f}, avg={sum(scores)/len(scores):.3f}\n"
                f"  FAISS Original: min={min(faiss_scores):.3f}, max={max(faiss_scores):.3f}, avg={sum(faiss_scores)/len(faiss_scores):.3f}\n"
                f"  KG Original: min={min(kg_scores):.3f}, max={max(kg_scores):.3f}, avg={sum(kg_scores)/len(kg_scores):.3f}"
            )
        
        return final_results
    
    def _calculate_recency_boost(self, chunk_id) -> float:
        """
        Berechnet Recency Boost basierend auf Chunk-ID.
        
        Neuere Chunks bekommen kleinen Bonus (max 5%).
        
        Args:
            chunk_id: Chunk-ID (kann String, Int oder None sein)
        
        Returns:
            Boost-Faktor (0.0 - recency_boost)
        """
        if self.chunk_id_range is None or chunk_id is None:
            # Fallback: kein Boost
            return 0.0
        
        try:
            # Konvertiere zu Int (falls String)
            chunk_id_int = int(chunk_id) if isinstance(chunk_id, str) else chunk_id
            
            min_id, max_id = self.chunk_id_range
            
            if max_id == min_id:
                return 0.0
            
            # Normalisiere auf 0-1 Range
            normalized = float(chunk_id_int - min_id) / float(max_id - min_id)
            
            # Skaliere auf Boost-Range
            return float(normalized * self.recency_boost)
            
        except (ValueError, TypeError):
            # Fallback bei Konvertierungsfehlern
            return 0.0
    
    def _normalize_kg_score(self, kg_score: float) -> float:
        """
        Normalisiert KG-Score auf FAISS-Range (0-1).
        
        KG-Scores sind meist bereits 0-1 (triple confidence),
        aber wir clippen sicherheitshalber.
        
        Args:
            kg_score: KG-Score
        
        Returns:
            Normalisierter Score
        """
        normalized = float(np.clip(kg_score, 0.0, 1.0))
        
        # 🆕 TRANSPARENTES LOGGING für Score-Normalisierung
        if kg_score > 1.0:
            logger.warning(
                f"⚠️ KG Score > 1.0 clipped: {kg_score:.3f} → {normalized:.3f} "
                f"(Check KG scoring logic!)"
            )
        elif kg_score < 0.0:
            logger.warning(
                f"⚠️ KG Score < 0.0 clipped: {kg_score:.3f} → {normalized:.3f}"
            )
        
        if self.debug and logger.isEnabledFor(logging.DEBUG):
            logger.debug(
                f"🔢 KG Score Normalization: {kg_score:.3f} → {normalized:.3f} "
                f"(clipped: {kg_score != normalized})"
            )
        
        return normalized
    
    def _cosine_similarity(self, vec1: np.ndarray, vec2: np.ndarray) -> float:
        """
        Berechnet Cosine Similarity zwischen zwei Vektoren.
        
        Args:
            vec1: Vektor 1
            vec2: Vektor 2
        
        Returns:
            Cosine Similarity (0-1)
        """
        try:
            # Ensure numpy arrays
            v1 = np.array(vec1, dtype=np.float32).flatten()
            v2 = np.array(vec2, dtype=np.float32).flatten()
            
            # Compute cosine similarity
            dot_product = np.dot(v1, v2)
            norm1 = np.linalg.norm(v1)
            norm2 = np.linalg.norm(v2)
            
            if norm1 == 0 or norm2 == 0:
                return 0.0
            
            similarity = dot_product / (norm1 * norm2)
            
            # Clip to [0, 1] range
            return float(np.clip(similarity, 0.0, 1.0))
            
        except Exception as e:
            logger.warning(f"Cosine similarity calculation failed: {e}")
            return 0.0
    
    def _select_with_diversity(
        self,
        sorted_results: List[Dict],
        k: int
    ) -> List[Dict]:
        """
        Wählt Top-K Results mit Diversität.
        
        ★ SOTA: Quality-aware KG diversity selection.
        KG results are only included if they meet a minimum quality score.
        This prevents low-quality KG results from displacing better FAISS results
        just to hit the 18% KG target ratio.
        
        Strategie:
        - Nimm beste Results
        - Achte auf Target KG-Ratio, aber NUR für qualitativ gute KG-Results
        - Maximal 50% KG um Qualität zu garantieren
        - ★ NEW: min_kg_score threshold (0.25) — KG below this never get diversity boost
        
        Args:
            sorted_results: Nach Score sortierte Results
            k: Anzahl gewünschter Results
        
        Returns:
            Top-K Results mit Diversität
        """
        if len(sorted_results) <= k:
            return sorted_results
        
        # ★ SOTA: Minimum score for KG to qualify for diversity boost
        min_kg_quality_score = 0.25
        
        # Berechne Target KG-Count
        target_kg_count = int(k * self.target_kg_ratio)
        max_kg_count = max(1, int(k * 0.5))  # Maximal 50% KG
        target_kg_count = min(target_kg_count, max_kg_count)
        
        selected: List[Dict[str, Any]] = []
        kg_count = 0
        faiss_count = 0
        
        # Iteriere durch sortierte Results
        for result in sorted_results:
            if len(selected) >= k:
                break
            
            source = result.get('source', 'unknown')
            result_score = result.get('combined_score', result.get('score', 0.0))
            
            if source == 'kg':
                # ★ NEW: Only give KG diversity boost if score is above minimum
                if result_score >= min_kg_quality_score and kg_count < target_kg_count:
                    selected.append(result)
                    kg_count += 1
                elif faiss_count >= k - target_kg_count and result_score >= min_kg_quality_score:
                    # Falls FAISS-Quota erfüllt, nehme gutes KG
                    selected.append(result)
                    kg_count += 1
                elif result_score < min_kg_quality_score:
                    # Low-quality KG: only include if no FAISS alternatives
                    pass  # Will be picked up in fill-up phase if needed
                else:
                    pass  # KG quota filled
            else:  # FAISS
                if faiss_count < k - target_kg_count or kg_count >= target_kg_count:
                    selected.append(result)
                    faiss_count += 1
        
        # Fill up wenn noch nicht voll
        remaining = k - len(selected)
        if remaining > 0:
            for result in sorted_results:
                if result not in selected:
                    selected.append(result)
                    if len(selected) >= k:
                        break
        
        return selected[:k]


if __name__ == "__main__":
    # Test Setup
    logging.basicConfig(level=logging.DEBUG)
    
    print("=" * 60)
    print("🧠 Smart Fusion Engine - Test")
    print("=" * 60)
    
    # 1. Initialize
    fusion = SmartFusionEngine(
        recency_boost_factor=0.05,
        target_kg_ratio=0.18
    )
    
    # Set chunk ID range for recency calculation
    fusion.set_chunk_id_range(min_id=1000, max_id=72000)
    
    # 2. Mock Data
    faiss_results = [
        {'chunk_id': '70000', 'score': 0.92, 'text': 'Recent FAISS result'},
        {'chunk_id': '50000', 'score': 0.88, 'text': 'Mid FAISS result'},
        {'chunk_id': '20000', 'score': 0.85, 'text': 'Old FAISS result'},
    ]
    
    kg_results = [
        {'chunk_id': '65000', 'score': 0.78, 'text': 'Recent KG result'},
        {'chunk_id': '30000', 'score': 0.75, 'text': 'Mid KG result'},
    ]
    
    # 3. Test Fusion
    fused = fusion.fuse(faiss_results, kg_results, k=5)
    
    print("\n✅ Fusion Results:")
    for i, result in enumerate(fused, 1):
        print(f"{i}. [{result['source'].upper()}] "
              f"chunk={result['chunk_id']}, "
              f"score={result['combined_score']:.4f} "
              f"(orig={result['original_score']:.3f}, "
              f"recency={result['recency_boost']:.4f})")
    
    # 4. Test Statistics
    kg_count = sum(1 for r in fused if r['source'] == 'kg')
    print(f"\n📊 Statistics:")
    print(f"   Total: {len(fused)}")
    print(f"   FAISS: {len(fused) - kg_count}")
    print(f"   KG: {kg_count} ({kg_count/len(fused)*100:.1f}%)")
    
    print("\n✅ Test completed!")
