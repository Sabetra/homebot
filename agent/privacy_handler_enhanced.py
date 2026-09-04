#!/usr/bin/env python3
"""
ENHANCED PRIVACY HANDLER WITH GERMAN LANGUAGE AWARENESS
========================================================

Refactored mit:
- LLMLanguageDetector (robuste Spracherkennung)
- GuaranteedLLMCaller (niemals leere Responses)
- RobustResponseHandler (multi-method JSON parsing)
- Deutsche Grammatik-Regeln (Substantive ≠ Namen!)

Author: AI System Evolution
Date: 2025-10-05 (Refactored)
"""

import re
import logging
import json
from typing import Dict, Any, List, Optional, Tuple

# Import neue Utilities
from llm_utils.guaranteed_caller import GuaranteedLLMCaller
from llm_utils.robust_response_handler import RobustResponseHandler
from llm_utils.language_detector import LLMLanguageDetector, Language
from utils.token_manager import estimate_prompt_tokens, estimate_structured_output_tokens

logger = logging.getLogger(__name__)


class EnhancedPrivacyHandler:
    """
    Enhanced Privacy Handler mit deutscher Spracherkennung.
    
    VERBESSERUNGEN:
    ✅ Robuste LLM-basierte Spracherkennung (Deutsch/Türkisch/Englisch)
    ✅ Deutsche Grammatik-Regeln (Substantive = kapitalisiert, NICHT Namen!)
    ✅ Kontext-basierte Namens-Erkennung (nur mit Possessivpronomen etc.)
    ✅ Garantiert valide LLM-Responses
    ✅ Multi-Methoden JSON-Parsing
    ✅ Detailed Logging & Transparency
    """
    
    def __init__(self, llm_client=None):
        """
        Args:
            llm_client: LLM-Client für Privacy-Analyse
        """
        self.llm_raw = llm_client
        
        # Wrape LLM mit Guaranteed Caller (falls verfügbar)
        if self.llm_raw:
            self.llm_caller = GuaranteedLLMCaller(
                self.llm_raw,
                max_retries=3,
                min_response_length=20,
                default_temperature=0.1  # Niedrig für konsistente Ergebnisse
            )
        else:
            self.llm_caller = None
            logger.warning("⚠️ Privacy Handler ohne LLM initialisiert - nur Fallback verfügbar")
        
        # Response Handler für JSON-Parsing
        self.response_handler = RobustResponseHandler()
        
        # Language Detector
        self.language_detector = LLMLanguageDetector(self.llm_raw)
        
        # Web-Search Tool Indicators (für Tool-Type-Aware Distribution)
        self.web_search_indicators = [
            'web_search', 'websearch', 'search_web',
            'google_search', 'yahoo_search', 'bing_search',
            'duckduckgo_search', 'internet_search',
            'web_scraper', 'browser_search', 'online_search'
        ]
        
        # Deutsche Fachbegriff-Whitelist (nicht als Namen behandeln)
        self.german_technical_terms = {
            'depression', 'angst', 'trauma', 'stress', 'burnout',
            'prüfungsangst', 'panikattacke', 'zwangsstörung',
            'familie', 'schule', 'arbeit', 'therapie', 'beratung',
            'faiss', 'vector', 'embedding', 'rag', 'llm',
            'psychologie', 'psychiatrie', 'beziehung'
        }
        
        logger.info("✅ Enhanced Privacy Handler initialisiert")
    
    def analyze_query_privacy(
        self,
        query: str,
        detect_language: bool = True
    ) -> Tuple[bool, str, Optional[Language]]:
        """
        Analysiert Query auf private Daten mit Sprach-Awareness.
        
        Args:
            query: Die zu analysierende Query
            detect_language: Ob Sprache erkannt werden soll
            
        Returns:
            (has_private_data, safe_query, detected_language)
        """
        
        logger.debug(f"🔍 Analyzing query privacy: {query[:100]}...")
        
        # Sprache erkennen falls gewünscht
        detected_lang = None
        if detect_language and self.llm_raw:
            lang_result = self.language_detector.detect_language(query)
            detected_lang = lang_result.language
            logger.debug(f"🌍 Detected language: {detected_lang.value} "
                        f"(confidence={lang_result.confidence:.2f}, "
                        f"method={lang_result.method_used})")
        
        # LLM-basierte Privacy-Analyse (primär)
        if self.llm_caller:
            is_private, safe_query = self._analyze_with_llm(query, detected_lang)
            return is_private, safe_query, detected_lang
        
        # Fallback: Strukturelle Analyse
        logger.warning("⚠️ Kein LLM - verwende strukturelle Privacy-Analyse")
        is_private = self._structural_privacy_check(query)
        safe_query = query if not is_private else self._extract_topic(query)
        
        return is_private, safe_query, detected_lang
    
    def _analyze_with_llm(
        self,
        query: str,
        detected_language: Optional[Language]
    ) -> Tuple[bool, str]:
        """LLM-basierte Privacy-Analyse mit deutscher Spracherkennung"""
        
        # Fallback wenn kein LLM verfügbar
        if not self.llm_caller:
            is_private = self._structural_privacy_check(query)
            safe_query = query if not is_private else self._extract_topic(query)
            return is_private, safe_query
        
        # Erstelle Prompt basierend auf erkannter Sprache
        prompt = self._create_enhanced_privacy_prompt(query, detected_language)

        prompt_tokens = estimate_prompt_tokens(prompt)
        model_n_ctx = getattr(self.llm_raw, "get_max_context_tokens", lambda: 16384)() or 16384
        max_tokens_dynamic = estimate_structured_output_tokens(
            prompt_tokens=prompt_tokens,
            model_context_window=model_n_ctx,
            min_output_tokens=384,
            max_output_tokens=2048,
        )
        
        # LLM-Call mit Garantie
        llm_result = self.llm_caller.call_with_guarantee(
            prompt=prompt,
            max_tokens=max_tokens_dynamic,
            temperature=0.1
        )
        
        logger.debug(f"📊 LLM Privacy Call: success={llm_result.success}, "
                    f"attempts={llm_result.attempts}")
        
        # Parse Response
        parsing_result = self.response_handler.parse_llm_response(
            llm_result.response,
            expected_keys=["has_private_data", "safe_query"],
            required_keys=["has_private_data", "safe_query"],
            default_values={
                "has_private_data": False,
                "safe_query": query
            }
        )
        
        if not parsing_result.success:
            logger.warning(f"⚠️ Privacy parsing used fallback: {parsing_result.method_used}")
            # Sicherheits-Fallback: Wenn LLM-Parsing komplett fehlschlägt,
            # nutze strukturelle Prüfung statt unsicheren Default
            is_private = self._structural_privacy_check(query)
            safe_query = query if not is_private else self._extract_topic(query)
            logger.info(f"🔒 Privacy structural fallback: private={is_private}")
            return is_private, safe_query
        
        data = parsing_result.data or {}
        is_private = bool(data.get("has_private_data", False))
        safe_query = str(data.get("safe_query", query))
        
        logger.debug(f"🔒 Privacy Result: private={is_private}, query='{safe_query[:50]}...'")
        
        return is_private, safe_query
    
    def _create_enhanced_privacy_prompt(
        self,
        query: str,
        detected_language: Optional[Language]
    ) -> str:
        """Erstellt enhanced Privacy-Prompt mit deutscher Spracherkennung"""
        
        # Sprach-spezifische Regeln
        language_rules = ""
        if detected_language == Language.GERMAN:
            language_rules = """
WICHTIG - DEUTSCHE SPRACHE BESONDERHEITEN:
========================================
Im Deutschen werden ALLE Substantive großgeschrieben!
Das bedeutet: Kapitalisierung ≠ automatisch Name!

Beispiele:
✅ "Prüfungsangst" - Substantiv, KEIN Name
✅ "Depression" - Substantiv, KEIN Name  
✅ "Familie" - Substantiv, KEIN Name
✅ "Schule" - Substantiv, KEIN Name
❌ "Max Mustermann" - Name (Vorname + Nachname)
❌ "Anna" - Name (mit Kontext: "meine Frau Anna")
❌ "Ben" - Name (mit Kontext: "mein Sohn Ben")

ERKENNE NAMEN NUR WENN:
1. Vorname + Nachname (z.B. "Max Mustermann")
2. Einzelname mit persönlichem Kontext (z.B. "meine Frau Anna")
3. Possessivpronomen + Name (z.B. "mein Sohn Ben")

IGNORIERE:
- Fachbegriffe (Depression, Angst, Trauma, Stress, Burnout, etc.)
- Allgemeine Substantive (Familie, Schule, Arbeit, Beziehung, etc.)
- Technische Begriffe (FAISS, RAG, Vector, Embedding, etc.)
"""
        elif detected_language == Language.TURKISH:
            language_rules = """
TURKISH LANGUAGE SPECIFICS:
==========================
Turkish has different capitalization rules than German.
Only proper names and sentence beginnings are capitalized.

DETECT NAMES WHEN:
1. Clear personal names (first + last name)
2. Names with possessive suffix (e.g., "annem Ayşe")
"""
        else:  # English or Unknown
            language_rules = """
ENGLISH LANGUAGE SPECIFICS:
==========================
In English, only proper names and sentence beginnings are capitalized.

DETECT NAMES WHEN:
1. Capitalized words not at sentence start
2. Clear personal names (first + last name)
3. Names with possessive context ("my wife Jane")
"""
        
        return f"""Du bist ein Privacy-Analyzer für Text-Queries.

{language_rules}

=== ANALYSE-SCHRITTE ===

SCHRITT 1: Erkenne ECHTE private Daten:
- ✅ Namen von Personen (mit Kontext!)
- ✅ Alter ("8 Jahre alt", "23 Jahre")
- ✅ Spezifische Beziehungen ("meine Frau", "mein Sohn")
- ✅ Persönliche Identifikatoren ("ich bin", "ich habe")
- ❌ ABER: Fachbegriffe sind KEINE privaten Daten!
- ❌ ABER: Allgemeine Substantive sind KEINE Namen!

SCHRITT 2: Extrahiere den KERN-INTENT:
- Was will der User WIRKLICH wissen?
- Formuliere die Frage ALLGEMEIN und SACHLICH
- Entferne ALLE persönlichen Bezüge

SCHRITT 3: Erstelle die bereinigte Query:
- NUR die sachliche Frage
- KEINE privaten Daten
- KEINE technischen Marker

=== BEISPIELE ===

Query: "Mein Sohn Ben ist 8 Jahre alt und hat Schulschwierigkeiten"
→ {{"has_private_data": true, "safe_query": "Was hilft bei Schulschwierigkeiten von Kindern?"}}

Query: "Was sind die Symptome von Depression?"
→ {{"has_private_data": false, "safe_query": "Was sind die Symptome von Depression?"}}

Query: "Ich habe Prüfungsangst vor meiner Mathe-Klausur"
→ {{"has_private_data": true, "safe_query": "Was hilft bei Prüfungsangst?"}}

Query: "AKTUELLE FRAGE: Was hilft bei Burnout?"
→ {{"has_private_data": false, "safe_query": "Was hilft bei Burnout?"}}

=== QUERY ZU ANALYSIEREN ===
"{query}"

=== ANTWORT-FORMAT ===
Antworte NUR mit JSON (keine Markdown-Tags, keine Erklärungen):
{{
    "has_private_data": true/false,
    "safe_query": "Die bereinigte, sachliche Frage",
    "detected_private_items": ["Liste gefundener privater Daten (optional)"],
    "reasoning": "Kurze Begründung (optional)"
}}
"""
    
    def _structural_privacy_check(self, text: str) -> bool:
        """Strukturelle Privacy-Prüfung (Fallback ohne LLM)"""
        
        if not text or len(text.strip()) < 5:
            return False
        
        words = text.split()
        if len(words) == 0:
            return False
        
        # Kriterium 1: Kapitalisierte Wörter (wahrscheinlich Namen)
        # ABER: Filtere deutsche Substantive!
        capitalized = 0
        for i, word in enumerate(words):
            clean_word = re.sub(r'[^\w]', '', word).lower()
            
            # Ignoriere Fachbegriffe
            if clean_word in self.german_technical_terms:
                continue
            
            # Prüfe Kapitalisierung
            original_word = re.sub(r'[^\w]', '', word)
            if len(original_word) > 2 and original_word[0].isupper():
                # Ignoriere Satzanfang
                if i > 0:
                    capitalized += 1
        
        # >20% kapitalisiert = wahrscheinlich viele Namen
        capital_ratio = capitalized / len(words) if len(words) > 0 else 0
        if capital_ratio > 0.20:
            logger.debug(f"🔍 Hohe Namen-Dichte: {capital_ratio:.2f}")
            return True
        
        # >2 Namen absolut
        if capitalized >= 2:
            logger.debug(f"🔍 Mehrere Namen: {capitalized}")
            return True
        
        # Kriterium 2: Possessivpronomen + kapitalisiertes Wort
        possessive_patterns = [
            r'\b(mein|meine|meiner|mein)\s+([A-Z][a-zäöüß]+)',
            r'\b(my)\s+([A-Z][a-z]+)'
        ]
        for pattern in possessive_patterns:
            if re.search(pattern, text):
                logger.debug(f"🔍 Possessiv-Pattern gefunden")
                return True
        
        # Kriterium 3: Persönliche Pronomen + Verben
        personal_patterns = [
            r'\bich\s+(bin|habe|hatte|möchte|kann|will)',
            r'\bI\s+(am|have|had|want|can|will)'
        ]
        for pattern in personal_patterns:
            if re.search(pattern, text, re.IGNORECASE):
                # Nur als privat markieren wenn AUCH andere Indikatoren
                if capitalized >= 1 or len(text) > 100:
                    logger.debug(f"🔍 Persönliches Pronomen + Indikator")
                    return True
        
        logger.debug("✅ Keine Privacy-Indikatoren gefunden")
        return False
    
    def _extract_topic(self, text: str) -> str:
        """Extrahiert Haupt-Topic aus Text (Fallback)"""
        
        # Entferne persönliche Pronomen
        text = re.sub(r'\b(ich|mein|meine|meiner|my|I|me)\b', '', text, flags=re.IGNORECASE)
        
        # Entferne Altersangaben
        text = re.sub(r'\b\d+\s+Jahre?\s+(alt)?\b', '', text, flags=re.IGNORECASE)
        
        # Entferne technische Präfixe
        text = re.sub(r'^(AKTUELLE FRAGE:|USER:|Query:)\s*', '', text, flags=re.IGNORECASE)
        
        # Cleanup
        text = ' '.join(text.split())  # Normalize whitespace
        
        return text.strip() if text.strip() else "Wie kann ich helfen?"
    
    # ========================================================================
    # BACKWARD COMPATIBILITY METHODS (alte ChainOfThoughtPrivacyHandler API)
    # ========================================================================
    
    def detect_private_context(self, text: str) -> bool:
        """
        Backward compatible: Erkennt ob Text private Daten enthält.
        
        Args:
            text: Text zur Analyse
            
        Returns:
            True wenn private Daten gefunden
        """
        is_private, _, _ = self.analyze_query_privacy(text, detect_language=False)
        return is_private
    
    def extract_safe_query_for_web_search(self, query: str) -> str:
        """
        Backward compatible: Extrahiert sichere Query für Web-Search.
        
        Args:
            query: Original-Query
            
        Returns:
            Bereinigte Query ohne private Daten
        """
        _, safe_query, _ = self.analyze_query_privacy(query, detect_language=False)
        return safe_query
    
    def analyze_tool_privacy_requirements(self, planned_calls: List[Any]) -> Dict[str, List[Any]]:
        """
        Backward compatible: Analysiert Tool-Privacy-Requirements.
        
        Diese Methode ist in der enhanced Version in einem separaten Modul,
        für Backward Compatibility geben wir ein einfaches Ergebnis zurück.
        
        Args:
            planned_calls: Liste geplanter Tool-Calls
            
        Returns:
            Dict mit kategorisierten Tool-Calls
        """
        # Vereinfachte Kategorisierung (volle Implementierung ist optional)
        web_tools = []
        rag_tools = []
        
        for call in planned_calls:
            tool_name = getattr(call, 'tool', '').lower() if hasattr(call, 'tool') else str(call).lower()
            
            # Web-Search Tools
            if any(indicator in tool_name for indicator in self.web_search_indicators):
                web_tools.append(call)
            else:
                rag_tools.append(call)
        
        return {
            "web_search": web_tools,
            "rag_knowledge": rag_tools
        }
    
    def sanitize_web_search_calls(self, planned_calls: List[Any], original_query: str) -> List[Any]:
        """
        Backward compatible: Sanitisiert Web-Search-Calls.
        
        Args:
            planned_calls: Liste geplanter Tool-Calls
            original_query: Original-Query
            
        Returns:
            Sanitisierte Tool-Calls
        """
        # Extrahiere sichere Query
        _, safe_query, _ = self.analyze_query_privacy(original_query, detect_language=False)
        
        # Ersetze Query in Web-Search-Calls
        sanitized_calls = []
        for call in planned_calls:
            # Prüfe ob Web-Search-Call
            tool_name = getattr(call, 'tool', '').lower() if hasattr(call, 'tool') else ''
            
            if any(indicator in tool_name for indicator in self.web_search_indicators):
                # Erstelle sanitisierten Call
                sanitized_call = call
                if hasattr(call, 'arguments') and isinstance(call.arguments, dict):
                    # Clone und ersetze query
                    import copy
                    sanitized_call = copy.deepcopy(call)
                    if 'query' in sanitized_call.arguments:
                        sanitized_call.arguments['query'] = safe_query
                
                sanitized_calls.append(sanitized_call)
            else:
                # Nicht-Web-Calls unverändert
                sanitized_calls.append(call)
        
        return sanitized_calls
    
    # ========================================================================
    # END BACKWARD COMPATIBILITY
    # ========================================================================
    

# Backward compatibility alias
ChainOfThoughtPrivacyHandler = EnhancedPrivacyHandler
