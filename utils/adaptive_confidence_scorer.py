#!/usr/bin/env python3
"""
ADAPTIVE CONFIDENCE SCORING für Knowledge Graph Triples
========================================================

Optimiert das Confidence-Scoring basierend auf:
- Triple-Kontext und Qualität
- Extraktions-Methode (LLM vs. Rule-based)
- Source-Text-Klarheit
- Semantische Kohärenz
- Temporal-Faktoren (neuere Triples = höheres Gewicht)
- User-Feedback-Integration (optional)

Author: AI System Evolution
Date: 2025-11-04
"""

import re
import logging
from typing import Dict, List, Optional, Tuple, TypedDict
from dataclasses import dataclass
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


class ConfidenceWeights(TypedDict):
    """Type-safe weights for confidence scoring"""
    extraction_method: float
    source_clarity: float
    semantic_coherence: float
    temporal: float
    specificity: float
    length: float


class ConfidenceThresholds(TypedDict):
    """Type-safe thresholds for confidence scoring"""
    min_subject_length: int
    min_predicate_length: int
    min_object_length: int
    max_subject_length: int
    max_predicate_length: int
    max_object_length: int
    optimal_subject_length: Tuple[int, int]
    optimal_predicate_length: Tuple[int, int]
    optimal_object_length: Tuple[int, int]


@dataclass
class ConfidenceFactors:
    """Faktoren für Confidence-Berechnung"""
    base_confidence: float = 0.5
    extraction_method_bonus: float = 0.0
    source_clarity_bonus: float = 0.0
    semantic_coherence_bonus: float = 0.0
    temporal_bonus: float = 0.0
    length_penalty: float = 0.0
    specificity_bonus: float = 0.0
    
    def calculate_final_confidence(self) -> float:
        """Berechnet finale Confidence (0.0-1.0)"""
        confidence = (
            self.base_confidence +
            self.extraction_method_bonus +
            self.source_clarity_bonus +
            self.semantic_coherence_bonus +
            self.temporal_bonus +
            self.specificity_bonus -
            self.length_penalty
        )
        
        # Clamp auf 0.0-1.0
        return max(0.0, min(1.0, confidence))


class AdaptiveConfidenceScorer:
    """
    Adaptives Confidence-Scoring für KG-Triples
    
    Berücksichtigt multiple Faktoren für präzise Confidence-Bewertung:
    - Extraktions-Qualität
    - Kontext-Klarheit
    - Semantische Kohärenz
    - Zeitliche Relevanz
    - Triple-Spezifität
    """
    
    def __init__(self) -> None:
        """Initialisiert Confidence-Scorer"""
        # Gewichtungen für verschiedene Faktoren (type-safe)
        self.weights: ConfidenceWeights = {
            'extraction_method': 0.15,
            'source_clarity': 0.20,
            'semantic_coherence': 0.25,
            'temporal': 0.10,
            'specificity': 0.15,
            'length': 0.05
        }
        
        # Schwellwerte (type-safe)
        self.thresholds: ConfidenceThresholds = {
            'min_subject_length': 2,
            'min_predicate_length': 3,
            'min_object_length': 2,
            'max_subject_length': 50,
            'max_predicate_length': 30,
            'max_object_length': 100,
            'optimal_subject_length': (3, 20),
            'optimal_predicate_length': (4, 15),
            'optimal_object_length': (3, 30)
        }
        
        logger.info("✅ AdaptiveConfidenceScorer initialisiert")
    
    def score_triple(
        self,
        subject: str,
        predicate: str,
        obj: str,
        source_text: str = "",
        extraction_method: str = "unknown",
        created_at: Optional[datetime] = None,
        metadata: Optional[Dict] = None
    ) -> Tuple[float, ConfidenceFactors]:
        """
        Berechnet Confidence-Score für ein Triple
        
        Args:
            subject: Triple-Subject
            predicate: Triple-Predicate
            obj: Triple-Object
            source_text: Ursprungstext (für Kontext)
            extraction_method: Extraktions-Methode ('llm', 'rule', 'manual')
            created_at: Erstellungszeitpunkt
            metadata: Zusätzliche Metadaten
            
        Returns:
            (confidence_score, confidence_factors)
        """
        factors = ConfidenceFactors()
        
        # 1. Base Confidence (abhängig von Extraktions-Methode)
        factors.base_confidence = self._get_base_confidence(extraction_method)
        
        # 2. Extraktions-Methoden-Bonus
        factors.extraction_method_bonus = self._score_extraction_method(
            extraction_method, metadata
        )
        
        # 3. Source-Clarity-Bonus
        factors.source_clarity_bonus = self._score_source_clarity(
            subject, predicate, obj, source_text
        )
        
        # 4. Semantic-Coherence-Bonus
        factors.semantic_coherence_bonus = self._score_semantic_coherence(
            subject, predicate, obj
        )
        
        # 5. Temporal-Bonus (neuere Triples höher gewichtet)
        if created_at:
            factors.temporal_bonus = self._score_temporal_relevance(created_at)
        
        # 6. Specificity-Bonus
        factors.specificity_bonus = self._score_specificity(
            subject, predicate, obj
        )
        
        # 7. Length-Penalty (zu lange Triples weniger vertrauenswürdig)
        factors.length_penalty = self._score_length_penalty(
            subject, predicate, obj
        )
        
        # Finale Confidence berechnen
        final_confidence = factors.calculate_final_confidence()
        
        logger.debug(f"Confidence-Scoring: {subject} | {predicate} | {obj}")
        logger.debug(f"  Base: {factors.base_confidence:.2f}")
        logger.debug(f"  Method: +{factors.extraction_method_bonus:.2f}")
        logger.debug(f"  Clarity: +{factors.source_clarity_bonus:.2f}")
        logger.debug(f"  Coherence: +{factors.semantic_coherence_bonus:.2f}")
        logger.debug(f"  Temporal: +{factors.temporal_bonus:.2f}")
        logger.debug(f"  Specificity: +{factors.specificity_bonus:.2f}")
        logger.debug(f"  Length Penalty: -{factors.length_penalty:.2f}")
        logger.debug(f"  ➡️ FINAL: {final_confidence:.2f}")
        
        return final_confidence, factors
    
    def _get_base_confidence(self, extraction_method: str) -> float:
        """Basis-Confidence abhängig von Extraktions-Methode"""
        method_confidences = {
            'llm': 0.60,         # LLM = mittlere Base
            'llm_enhanced': 0.65,  # Enhanced LLM = höhere Base
            'rule': 0.50,        # Rule-based = niedrigere Base
            'manual': 0.85,      # Manuell = hohe Base
            'hybrid': 0.70,      # Hybrid = gute Base
            'unknown': 0.40      # Unknown = niedrig
        }
        
        return method_confidences.get(extraction_method.lower(), 0.40)
    
    def _score_extraction_method(
        self, 
        extraction_method: str, 
        metadata: Optional[Dict] = None
    ) -> float:
        """Score basierend auf Extraktions-Methode und Qualitätsindikatoren"""
        bonus = 0.0
        
        # LLM-spezifische Bonus-Faktoren
        if extraction_method.lower() in ['llm', 'llm_enhanced']:
            if metadata:
                # LLM-Temperature (niedrig = präziser)
                temperature = metadata.get('temperature', 0.5)
                if temperature <= 0.2:
                    bonus += 0.10
                elif temperature <= 0.5:
                    bonus += 0.05
                
                # LLM-Retries (weniger = besser)
                retries = metadata.get('retries', 0)
                if retries == 0:
                    bonus += 0.05
                elif retries <= 1:
                    bonus += 0.02
        
        # Hybrid-Methode Bonus
        elif extraction_method.lower() == 'hybrid':
            bonus += 0.08
        
        return bonus * self.weights['extraction_method']
    
    def _score_source_clarity(
        self,
        subject: str,
        predicate: str,
        obj: str,
        source_text: str
    ) -> float:
        """Score basierend auf Klarheit des Ursprungstextes"""
        if not source_text:
            return 0.0
        
        bonus = 0.0
        
        # 1. Triple-Komponenten im Source-Text vorhanden?
        subject_in_source = subject.lower() in source_text.lower()
        predicate_in_source = predicate.lower() in source_text.lower()
        object_in_source = obj.lower() in source_text.lower()
        
        components_found = sum([subject_in_source, predicate_in_source, object_in_source])
        
        if components_found == 3:
            bonus += 0.15  # Alle Komponenten gefunden
        elif components_found == 2:
            bonus += 0.10  # 2 Komponenten gefunden
        elif components_found == 1:
            bonus += 0.05  # 1 Komponente gefunden
        
        # 2. Source-Text-Länge (zu kurz = wenig Kontext)
        if len(source_text) >= 100:
            bonus += 0.05
        elif len(source_text) >= 50:
            bonus += 0.03
        
        # 3. Source-Text-Qualität (vollständige Sätze?)
        complete_sentences = len(re.findall(r'[.!?]', source_text))
        if complete_sentences >= 2:
            bonus += 0.05
        elif complete_sentences >= 1:
            bonus += 0.02
        
        return bonus * self.weights['source_clarity']
    
    def _score_semantic_coherence(
        self,
        subject: str,
        predicate: str,
        obj: str
    ) -> float:
        """Score basierend auf semantischer Kohärenz des Triples"""
        bonus = 0.0
        
        # 1. Predicate-Qualität
        # Psychologisch relevante Prädikate höher bewerten
        therapeutic_predicates = [
            'hat', 'fühlt', 'erlebt', 'leidet', 'spricht über',
            'träumt von', 'denkt an', 'sorgt sich um', 'arbeitet an',
            'entwickelt', 'zeigt', 'äußert', 'beschreibt'
        ]
        
        if any(pred in predicate.lower() for pred in therapeutic_predicates):
            bonus += 0.12
        
        # 2. Subject-Object-Kohärenz
        # Subject und Object sollten unterschiedlich sein
        if subject.lower() != obj.lower():
            bonus += 0.08
        
        # 3. Keine generischen Werte
        generic_terms = ['etwas', 'ding', 'sache', 'etwas anderes', 'vieles']
        
        subject_generic = any(term in subject.lower() for term in generic_terms)
        object_generic = any(term in obj.lower() for term in generic_terms)
        
        if not subject_generic and not object_generic:
            bonus += 0.10
        elif subject_generic or object_generic:
            bonus -= 0.05  # Penalty für generische Terme
        
        return bonus * self.weights['semantic_coherence']
    
    def _score_temporal_relevance(self, created_at: datetime) -> float:
        """Score basierend auf Aktualität (neuere Triples höher gewichtet)"""
        now = datetime.now(created_at.tzinfo) if created_at.tzinfo else datetime.now()
        
        age = now - created_at
        
        # Exponentieller Decay über Zeit
        if age <= timedelta(days=1):
            bonus = 0.10  # Sehr aktuell
        elif age <= timedelta(days=7):
            bonus = 0.08  # Letzte Woche
        elif age <= timedelta(days=30):
            bonus = 0.05  # Letzter Monat
        elif age <= timedelta(days=90):
            bonus = 0.02  # Letzte 3 Monate
        else:
            bonus = 0.00  # Älter
        
        return bonus * self.weights['temporal']
    
    def _score_specificity(
        self,
        subject: str,
        predicate: str,
        obj: str
    ) -> float:
        """Score basierend auf Spezifität des Triples"""
        bonus = 0.0
        
        # 1. Länge = Proxy für Spezifität
        optimal_s_min, optimal_s_max = self.thresholds['optimal_subject_length']
        optimal_p_min, optimal_p_max = self.thresholds['optimal_predicate_length']
        optimal_o_min, optimal_o_max = self.thresholds['optimal_object_length']
        
        # Subject-Spezifität
        if optimal_s_min <= len(subject) <= optimal_s_max:
            bonus += 0.05
        
        # Predicate-Spezifität
        if optimal_p_min <= len(predicate) <= optimal_p_max:
            bonus += 0.05
        
        # Object-Spezifität
        if optimal_o_min <= len(obj) <= optimal_o_max:
            bonus += 0.05
        
        # 2. Konkrete Namen/Begriffe (Großbuchstaben am Anfang)
        if subject[0].isupper() and len(subject) > 2:
            bonus += 0.03  # Eigenname
        
        if obj[0].isupper() and len(obj) > 2:
            bonus += 0.03  # Eigenname
        
        return bonus * self.weights['specificity']
    
    def _score_length_penalty(
        self,
        subject: str,
        predicate: str,
        obj: str
    ) -> float:
        """Penalty für zu lange Triple-Komponenten"""
        penalty = 0.0
        
        # Subject zu lang?
        if len(subject) > self.thresholds['max_subject_length']:
            overflow = len(subject) - self.thresholds['max_subject_length']
            penalty += min(0.10, overflow * 0.002)
        
        # Predicate zu lang?
        if len(predicate) > self.thresholds['max_predicate_length']:
            overflow = len(predicate) - self.thresholds['max_predicate_length']
            penalty += min(0.10, overflow * 0.003)
        
        # Object zu lang?
        if len(obj) > self.thresholds['max_object_length']:
            overflow = len(obj) - self.thresholds['max_object_length']
            penalty += min(0.10, overflow * 0.001)
        
        return penalty * self.weights['length']
    
    def batch_score_triples(
        self,
        triples: List[Dict]
    ) -> List[Tuple[Dict, float, ConfidenceFactors]]:
        """
        Batch-Scoring für Multiple Triples
        
        Args:
            triples: Liste von Triple-Dicts mit subject, predicate, object, ...
            
        Returns:
            Liste von (triple, confidence, factors)
        """
        results = []
        
        for triple in triples:
            confidence, factors = self.score_triple(
                subject=triple.get('subject', ''),
                predicate=triple.get('predicate', ''),
                obj=triple.get('object', ''),
                source_text=triple.get('source_text', ''),
                extraction_method=triple.get('extraction_method', 'unknown'),
                created_at=triple.get('created_at'),
                metadata=triple.get('metadata', {})
            )
            
            results.append((triple, confidence, factors))
        
        logger.info(f"✅ Batch-Scoring: {len(triples)} Triples bewertet")
        
        return results


# Utility-Funktion für einfachen Export
def score_triple_confidence(
    subject: str,
    predicate: str,
    obj: str,
    **kwargs
) -> float:
    """
    Convenience-Funktion für schnelles Confidence-Scoring
    
    Args:
        subject: Triple-Subject
        predicate: Triple-Predicate
        obj: Triple-Object
        **kwargs: Optionale Parameter (source_text, extraction_method, etc.)
        
    Returns:
        Confidence-Score (0.0-1.0)
    """
    scorer = AdaptiveConfidenceScorer()
    confidence, _ = scorer.score_triple(subject, predicate, obj, **kwargs)
    return confidence


if __name__ == "__main__":
    # Test-Beispiele
    logging.basicConfig(level=logging.DEBUG)
    
    scorer = AdaptiveConfidenceScorer()
    
    # Test 1: Hochwertiges Triple
    print("\n" + "="*80)
    print("TEST 1: Hochwertiges Triple")
    print("="*80)
    conf1, factors1 = scorer.score_triple(
        subject="Vater",
        predicate="hat",
        obj="Alkoholproblem",
        source_text="Mein Vater hat ein Alkoholproblem, das die Familie sehr belastet.",
        extraction_method="llm_enhanced",
        created_at=datetime.now(),
        metadata={'temperature': 0.1, 'retries': 0}
    )
    print(f"Confidence: {conf1:.3f}")
    
    # Test 2: Niedrigwertiges Triple
    print("\n" + "="*80)
    print("TEST 2: Niedrigwertiges Triple")
    print("="*80)
    conf2, factors2 = scorer.score_triple(
        subject="Etwas",
        predicate="ist",
        obj="Sache",
        source_text="",
        extraction_method="unknown",
        created_at=datetime.now() - timedelta(days=365)
    )
    print(f"Confidence: {conf2:.3f}")
    
    # Test 3: Mittleres Triple
    print("\n" + "="*80)
    print("TEST 3: Mittleres Triple")
    print("="*80)
    conf3, factors3 = scorer.score_triple(
        subject="Benutzer",
        predicate="erlebt",
        obj="Stress bei der Arbeit",
        source_text="Ich erlebe viel Stress bei der Arbeit in letzter Zeit.",
        extraction_method="llm",
        created_at=datetime.now() - timedelta(days=7),
        metadata={'temperature': 0.3, 'retries': 1}
    )
    print(f"Confidence: {conf3:.3f}")
