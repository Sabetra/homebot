#!/usr/bin/env python3
"""
LLM Language Detector
=====================

Robuste, sprachunabhängige Spracherkennung mit LLM + Fallback.

Features:
- LLM-basierte Primär-Erkennung (kontextbewusst)
- langdetect Fallback (statistisch)
- Confidence-basierte Auswahl
- Unterstützung für Deutsch, Englisch, Türkisch, u.a.

Author: AI System Evolution
Date: 2025-10-05
"""

import logging
from typing import Optional, List, Dict, Any
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)

# Optional: langdetect als Fallback
try:
    from langdetect import detect_langs, DetectorFactory
    # Setze Seed für reproduzierbare Ergebnisse
    DetectorFactory.seed = 0
    LANGDETECT_AVAILABLE = True
except ImportError:
    logger.info("ℹ️ langdetect nicht verfügbar - nur LLM-basierte Erkennung")
    LANGDETECT_AVAILABLE = False


class Language(str, Enum):
    """Unterstützte Sprachen"""
    GERMAN = "de"
    ENGLISH = "en"
    TURKISH = "tr"
    BULGARIAN = "bg"
    FRENCH = "fr"
    SPANISH = "es"
    ITALIAN = "it"
    DUTCH = "nl"
    POLISH = "pl"
    RUSSIAN = "ru"
    UNKNOWN = "unknown"


@dataclass
class LanguageResult:
    """Ergebnis der Spracherkennung"""
    language: Language
    confidence: float  # 0.0 - 1.0
    method_used: str  # "llm", "langdetect", "fallback"
    reasoning: Optional[str] = None
    alternative_languages: Optional[List[Dict[str, float]]] = None


class LLMLanguageDetector:
    """
    LLM-basierter Language Detector mit statistischem Fallback.
    
    Strategie:
    1. LLM-basierte Erkennung (Primär - kontextbewusst)
       → Versteht Kontext, Grammatik, Semantik
    
    2. langdetect Fallback (Sekundär - statistisch)
       → Schnell, aber nur pattern-basiert
    
    3. Heuristic Fallback (Tertiär - character-basiert)
       → Grundlegende Character-Set-Analyse
    
    Auswahl:
    - LLM > 0.8 confidence → Use LLM
    - langdetect > 0.9 confidence → Use langdetect
    - Beide unsicher → Majority vote oder Heuristic
    """
    
    def __init__(self, llm_client=None):
        """
        Args:
            llm_client: LLM-Client für intelligente Erkennung
        """
        self.llm = llm_client
        self.detection_stats = {
            "llm": 0,
            "langdetect": 0,
            "heuristic": 0,
            "fallback": 0
        }
    
    def detect_language(self, text: str, min_confidence: float = 0.7) -> LanguageResult:
        """
        Erkennt Sprache mit hybrider Strategie.
        
        Args:
            text: Text zur Analyse
            min_confidence: Minimale Confidence für zuverlässiges Ergebnis
            
        Returns:
            LanguageResult
        """
        
        if not text or len(text.strip()) < 3:
            logger.warning("⚠️ Text zu kurz für Spracherkennung")
            return LanguageResult(
                language=Language.UNKNOWN,
                confidence=0.0,
                method_used="fallback",
                reasoning="Text too short"
            )
        
        # Versuch 1: LLM-basierte Erkennung
        if self.llm:
            llm_result = self._detect_with_llm(text)
            if llm_result and llm_result.confidence >= 0.8:
                self.detection_stats["llm"] += 1
                return llm_result
        
        # Versuch 2: langdetect Fallback
        if LANGDETECT_AVAILABLE:
            langdetect_result = self._detect_with_langdetect(text)
            if langdetect_result and langdetect_result.confidence >= 0.9:
                self.detection_stats["langdetect"] += 1
                return langdetect_result
        
        # Versuch 3: Wenn beide unsicher, nutze beste verfügbare
        if self.llm:
            llm_result = self._detect_with_llm(text)
            if llm_result and llm_result.confidence >= min_confidence:
                self.detection_stats["llm"] += 1
                return llm_result
        
        if LANGDETECT_AVAILABLE:
            langdetect_result = self._detect_with_langdetect(text)
            if langdetect_result and langdetect_result.confidence >= min_confidence:
                self.detection_stats["langdetect"] += 1
                return langdetect_result
        
        # Versuch 4: Heuristic Fallback
        heuristic_result = self._detect_with_heuristics(text)
        self.detection_stats["heuristic"] += 1
        return heuristic_result
    
    def _detect_with_llm(self, text: str) -> Optional[LanguageResult]:
        """LLM-basierte Spracherkennung (kontextbewusst)"""
        try:
            prompt = self._create_language_detection_prompt(text)
            
            # LLM-Call
            if self.llm is None:
                logger.warning("⚠️ LLM ist None")
                return None
            
            if hasattr(self.llm, 'generate_response'):
                response = self.llm.generate_response(
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=150,
                    temperature=0.1,
                    image_path=None
                )
                
                if isinstance(response, dict):
                    response = response.get('content', str(response))
            else:
                logger.warning("⚠️ LLM hat keine generate_response Methode")
                return None
            
            # Parse Response
            result = self._parse_language_response(response, "llm")
            logger.debug(f"🧠 LLM Language Detection: {result.language.value} (confidence={result.confidence})")
            return result
            
        except Exception as e:
            logger.error(f"❌ LLM Language Detection failed: {e}")
            return None
    
    def _detect_with_langdetect(self, text: str) -> Optional[LanguageResult]:
        """langdetect-basierte Spracherkennung (statistisch)"""
        if not LANGDETECT_AVAILABLE:
            return None
            
        try:
            # langdetect gibt Liste von (lang, prob) zurück
            from langdetect import detect_langs
            langs = detect_langs(text)
            
            if langs:
                best = langs[0]
                lang_code = best.lang
                confidence = best.prob
                
                # Mappe zu unserem Language Enum
                try:
                    language = Language(lang_code)
                except ValueError:
                    language = Language.UNKNOWN
                
                # Sammle Alternativen
                alternatives = [
                    {"language": l.lang, "confidence": l.prob}
                    for l in langs[1:3]  # Top 2 Alternativen
                ]
                
                logger.debug(f"📊 langdetect: {language.value} (confidence={confidence})")
                
                return LanguageResult(
                    language=language,
                    confidence=confidence,
                    method_used="langdetect",
                    alternative_languages=alternatives if alternatives else None
                )
            
            return None
            
        except Exception as e:
            logger.error(f"❌ langdetect failed: {e}")
            return None
    
    def _detect_with_heuristics(self, text: str) -> LanguageResult:
        """Heuristic-basierte Spracherkennung (character-basiert)"""
        
        text_lower = text.lower()
        
        # Deutsche Indikatoren
        german_chars = sum(1 for c in text if c in 'äöüßÄÖÜ')
        german_words = sum(1 for w in ['der', 'die', 'das', 'und', 'ist', 'nicht', 'ich', 'sie', 'er']
                          if w in text_lower.split())
        
        # Türkische Indikatoren
        turkish_chars = sum(1 for c in text if c in 'ğĞıİşŞçÇöÖüÜ')
        turkish_words = sum(1 for w in ['ve', 'bir', 'bu', 'için', 'var', 'mi', 'ne']
                           if w in text_lower.split())
        
        # Englische Indikatoren
        english_words = sum(1 for w in ['the', 'is', 'are', 'and', 'or', 'not', 'have', 'has']
                           if w in text_lower.split())
        
        # Bulgarische Indikatoren (Kyrillisch)
        bulgarian_chars = sum(1 for c in text if c in 'аябвгдеежзийклмнопрстуфхцчшщъыьэюяёю')
        bulgarian_words = sum(1 for w in ['на', 'в', 'е', 'и', 'от', 'за', 'се', 'не', 'да', 'че', 'то', 'му']
                             if w in text_lower.split())
        
        # Score-basierte Entscheidung
        scores = {
            Language.GERMAN: german_chars * 3 + german_words * 2,
            Language.TURKISH: turkish_chars * 3 + turkish_words * 2,
            Language.ENGLISH: english_words * 2,
            Language.BULGARIAN: bulgarian_chars * 3 + bulgarian_words * 2,
        }
        
        best_lang = max(scores, key=lambda k: scores[k])
        best_score = scores[best_lang]
        total_score = sum(scores.values()) or 1
        confidence = min(best_score / total_score, 0.9)  # Max 0.9 für Heuristics
        
        logger.debug(f"🔍 Heuristic: {best_lang.value} (confidence={confidence}, scores={scores})")
        
        return LanguageResult(
            language=best_lang if confidence > 0.3 else Language.UNKNOWN,
            confidence=confidence,
            method_used="heuristic",
            reasoning=f"Scores: {scores}"
        )
    
    def _create_language_detection_prompt(self, text: str) -> str:
        """Erstellt Prompt für LLM-basierte Spracherkennung"""
        
        # Kürze Text falls zu lang
        text_sample = text[:500] if len(text) > 500 else text
        
        return f"""Analyze the language of this text.

TEXT:
"{text_sample}"

ANALYSIS CRITERIA:
1. Vocabulary and grammar patterns
2. Special characters (ä, ö, ü for German; ğ, ı, ş for Turkish; а, я, ю for Bulgarian Cyrillic)
3. Common words and phrases
4. Sentence structure

IMPORTANT:
- German: Capitalized nouns, umlauts (ä, ö, ü), common words like "der", "die", "das"
- Turkish: Dotless i (ı), special chars (ğ, ş, ç), common words like "ve", "bir", "bu"
- English: No special chars, common words like "the", "is", "are"
- Bulgarian: Cyrillic script (а, я, ю, э, щ), common words like "на", "в", "е", "и", "от"

RESPONSE FORMAT (JSON only):
{{
    "language": "de|en|tr|bg|fr|es|it|nl|pl|ru|unknown",
    "confidence": 0.0-1.0,
    "reasoning": "Brief explanation of decision"
}}

Respond ONLY with the JSON (no extra text):"""
    
    def _parse_language_response(self, response: str, method: str) -> LanguageResult:
        """Parsed LLM-Response zur Spracherkennung"""
        try:
            # Importiere RobustResponseHandler für consistent parsing
            from .robust_response_handler import parse_json_safe
            
            data = parse_json_safe(
                response,
                expected_keys=["language", "confidence"],
                required_keys=["language"],
                default_values={"language": "unknown", "confidence": 0.5}
            )
            
            # Parse language
            lang_str = data.get("language", "unknown")
            try:
                language = Language(lang_str)
            except ValueError:
                language = Language.UNKNOWN
            
            # Parse confidence
            confidence = float(data.get("confidence", 0.5))
            
            # Parse reasoning
            reasoning = data.get("reasoning", "")
            
            return LanguageResult(
                language=language,
                confidence=confidence,
                method_used=method,
                reasoning=reasoning
            )
            
        except Exception as e:
            logger.error(f"❌ Language response parsing failed: {e}")
            return LanguageResult(
                language=Language.UNKNOWN,
                confidence=0.0,
                method_used=method,
                reasoning=f"Parsing error: {e}"
            )
    
    def get_detection_stats(self) -> Dict[str, int]:
        """Gibt Statistiken über verwendete Detection-Methoden zurück"""
        stats_copy = self.detection_stats.copy()
        # Ensure type safety - dict.copy() preserves type but MyPy needs assurance
        return dict(stats_copy)  # Explicit dict construction for type safety


# Convenience function
def detect_language_safe(text: str, llm_client=None) -> Language:
    """
    Convenience function für schnelle Spracherkennung.
    
    Returns:
        Language enum
    """
    detector = LLMLanguageDetector(llm_client)
    result = detector.detect_language(text)
    return result.language
