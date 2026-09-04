#!/usr/bin/env python3
"""
ENHANCED INTENT DETECTOR WITH ROBUST LLM HANDLING
==================================================

Refactored mit:
- GuaranteedLLMCaller (niemals leere Responses)
- RobustResponseHandler (multi-method JSON parsing)
- Detailed Logging & Transparency

Author: AI System Evolution  
Date: 2025-10-05 (Refactored)
"""

import json
import re
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass
from enum import Enum
import logging

# Import neue Utilities
from llm_utils.guaranteed_caller import GuaranteedLLMCaller
from llm_utils.robust_response_handler import RobustResponseHandler, ParsingResult
from utils.token_manager import estimate_prompt_tokens, estimate_structured_output_tokens

try:
    from agent.grammars import JSON_OBJECT_GRAMMAR
except ImportError:
    JSON_OBJECT_GRAMMAR = None

logger = logging.getLogger(__name__)


class IntentType(Enum):
    """Erkannte Intent-Typen"""
    VISUALIZATION = "visualization"
    SEARCH = "search"
    ANALYSIS = "analysis"
    CREATION = "creation"
    MODIFICATION = "modification"
    QUESTION = "question"
    COMMAND = "command"
    UNKNOWN = "unknown"


@dataclass
class IntentResult:
    """Ergebnis der Intent-Erkennung"""
    intent_type: IntentType
    confidence: float  # 0.0 - 1.0
    reasoning: str
    suggested_tools: List[Dict[str, Any]]
    metadata: Dict[str, Any]
    
    # NEU: Metadaten über LLM-Call
    llm_call_success: bool = True
    llm_attempts: int = 1
    parsing_method: str = "direct_json"
    
    def is_confident(self, threshold: float = 0.7) -> bool:
        """Prüft ob Confidence über Schwellwert"""
        return self.confidence >= threshold


class EnhancedIntentDetector:
    """
    Enhanced Generic Intent Detector mit robusten LLM-Calls.
    
    VERBESSERUNGEN:
    ✅ Garantiert niemals leere LLM-Responses (GuaranteedLLMCaller)
    ✅ Multi-Methoden JSON-Parsing (RobustResponseHandler)
    ✅ Detailed Logging & Diagnostics
    ✅ Graceful Fallbacks mit informativen Defaults
    """
    
    def __init__(self, llm_client=None):
        """
        Args:
            llm_client: LLM Client für Intent-Erkennung
                       Falls None, wird Standard-LLM verwendet
        """
        self.llm_raw = llm_client or self._get_default_llm()
        
        # GBNF Grammar-Support prüfen (SOTA: garantiert valides JSON)
        self._grammar_available = (
            JSON_OBJECT_GRAMMAR is not None
            and hasattr(self.llm_raw, 'generate_with_grammar')
        )
        
        # Wrape LLM mit Guaranteed Caller (Fallback wenn Grammar nicht verfügbar)
        self.llm_caller = GuaranteedLLMCaller(
            self.llm_raw,
            max_retries=3,
            min_response_length=20,
            default_temperature=0.2
        )
        
        # Response Handler für JSON-Parsing (Fallback-Pfad)
        self.response_handler = RobustResponseHandler()
        
        # Intent History für selbstlernendes System
        self.intent_history = []
        
        logger.info(f"✅ Enhanced Intent Detector initialisiert (grammar={self._grammar_available})")
    
    @staticmethod
    def _parse_confidence(raw_value) -> float:
        """
        Extrahiert numerischen Confidence-Wert aus LLM-Output.
        
        LLMs geben manchmal 'hoch (0.9)' oder 'high' statt 0.9 zurück.
        Dies ist inhärente LLM-Nondeterminsmus-Behandlung, kein Workaround.
        """
        # Fall 1: Bereits numerisch
        if isinstance(raw_value, (int, float)):
            return max(0.0, min(1.0, float(raw_value)))
        
        # Fall 2: String -- extrahiere Zahl
        if isinstance(raw_value, str):
            # Suche nach Float/Int in Klammern oder allein: "hoch (0.9)", "0.85", etc.
            match = re.search(r'(\d+\.?\d*)', raw_value)
            if match:
                val = float(match.group(1))
                # Wenn Wert > 1, könnte es Prozent sein (85 → 0.85)
                if val > 1.0:
                    val = val / 100.0
                return max(0.0, min(1.0, val))
            
            # Fall 3: Nur Text ohne Zahl → semantisch mappen
            text = raw_value.lower().strip()
            semantic_map = {
                'sehr hoch': 0.95, 'very high': 0.95,
                'hoch': 0.85, 'high': 0.85,
                'mittel': 0.5, 'medium': 0.5, 'moderate': 0.5,
                'niedrig': 0.25, 'low': 0.25,
                'sehr niedrig': 0.1, 'very low': 0.1,
            }
            for key, val in semantic_map.items():
                if key in text:
                    return val
        
        # Fallback
        return 0.5
    
    def detect_intent(
        self,
        user_message: str,
        context: Optional[Dict] = None,
        available_tools: Optional[List[Dict]] = None
    ) -> IntentResult:
        """
        Erkennt die Benutzerabsicht semantisch mit robusten Fallbacks.
        
        Args:
            user_message: Die Benutzeranfrage
            context: Optionaler Kontext (Konversations-Historie, etc.)
            available_tools: Liste verfügbarer Tools für bessere Auswahl
            
        Returns:
            IntentResult mit erkanntem Intent und Tool-Vorschlägen
        """
        
        logger.info(f"🔍 Detecting intent for: {user_message[:100]}...")
        
        # Erstelle Intent-Detection Messages (System/User getrennt)
        system_prompt, user_prompt = self._build_intent_messages(
            user_message,
            context or {},
            available_tools or []
        )
        
        default_values = {
            "intent_type": "unknown",
            "confidence": 0.3,
            "reasoning": "Intent detection failed - using fallback",
            "suggested_tools": [],
            "metadata": {}
        }

        prompt_text = f"{system_prompt}\n\n{user_prompt}"
        prompt_tokens = estimate_prompt_tokens(prompt_text)
        model_n_ctx = getattr(self.llm_raw, "get_max_context_tokens", lambda: 16384)() or 16384
        max_tokens_dynamic = estimate_structured_output_tokens(
            prompt_tokens=prompt_tokens,
            model_context_window=model_n_ctx,
            min_output_tokens=384,
            max_output_tokens=2048,
        )
        
        # SOTA: GBNF Grammar-Enforcement (garantiert valides JSON)
        parsing_result = None
        if self._grammar_available:
            try:
                messages = [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ]
                raw_response = self.llm_raw.generate_with_grammar(
                    messages=messages,
                    grammar_str=JSON_OBJECT_GRAMMAR,
                    max_tokens=max_tokens_dynamic,
                    temperature=0.2,
                )
                if raw_response and raw_response.strip():
                    try:
                        data = json.loads(raw_response)
                        parsing_result = ParsingResult(
                            data=data,
                            success=True,
                            method_used="gbnf_grammar",
                        )
                        logger.debug("✅ Intent detection via GBNF grammar erfolgreich")
                    except json.JSONDecodeError:
                        logger.warning("⚠️ GBNF response kein valides JSON, Fallback...")
            except Exception as e:
                logger.warning(f"⚠️ Grammar-basierte Intent-Detection fehlgeschlagen: {e}")
        
        # Fallback: GuaranteedLLMCaller + RobustResponseHandler
        if parsing_result is None or not parsing_result.success:
            full_prompt = f"{system_prompt}\n\n{user_prompt}"
            llm_result = self.llm_caller.call_with_guarantee(
                prompt=full_prompt,
                max_tokens=max_tokens_dynamic,
                temperature=0.2
            )
            logger.debug(f"📊 LLM Call Stats: success={llm_result.success}, "
                        f"attempts={llm_result.attempts}, time={llm_result.total_time:.2f}s")
            
            parsing_result = self.response_handler.parse_llm_response(
                llm_result.response,
                expected_keys=["intent_type", "confidence", "reasoning", "suggested_tools"],
                required_keys=["intent_type"],
                default_values=default_values
            )
        
        if not parsing_result.success:
            logger.warning(f"⚠️ Intent parsing used fallback method: {parsing_result.method_used}")
        
        # Baue IntentResult
        intent_data = parsing_result.data or {}
        
        try:
            intent_type = IntentType(intent_data.get("intent_type", "unknown"))
        except ValueError:
            logger.warning(f"⚠️ Unknown intent type: {intent_data.get('intent_type')}")
            intent_type = IntentType.UNKNOWN
        
        result = IntentResult(
            intent_type=intent_type,
            confidence=self._parse_confidence(intent_data.get("confidence", 0.0)),
            reasoning=intent_data.get("reasoning", ""),
            suggested_tools=intent_data.get("suggested_tools", []),
            metadata=intent_data.get("metadata", {}),
            # LLM-Call Metadaten
            llm_call_success=parsing_result.success,
            llm_attempts=1,
            parsing_method=parsing_result.method_used
        )
        
        # Speichere für selbstlernendes System
        self.intent_history.append({
            "query": user_message,
            "result": result,
            "timestamp": self._get_timestamp()
        })
        
        logger.info(f"✅ Detected intent: {result.intent_type.value} "
                   f"(confidence: {result.confidence:.2f}, "
                   f"llm_success: {result.llm_call_success})")
        
        return result
    
    def _build_intent_messages(
        self,
        user_message: str,
        context: Dict,
        available_tools: List[Dict]
    ) -> tuple:
        """Erstellt System- und User-Prompt für LLM-basierte Intent-Erkennung.
        
        SOTA: Separater System/User-Split + kompakter Klassifikations-Prompt
        statt monolithischem CoT-Framework (das den LLM zum Freitext-Reasoning
        verleitet statt JSON auszugeben).
        
        Returns:
            Tuple[str, str]: (system_prompt, user_prompt)
        """
        
        tools_section = ""
        if available_tools:
            tools_section = "VERFÜGBARE TOOLS:\n" + "\n".join([
                f"- {tool.get('name', 'unknown')}: {tool.get('description', '')}"
                for tool in available_tools
            ]) + "\n\n"
        
        system_prompt = f"""Du bist ein Intent-Klassifikator. Klassifiziere Benutzeranfragen in JSON.

INTENT-TYPEN:
- "visualization": Visuell darstellen (Diagramm, Graph, Chart, Übersicht)
- "search": Information suchen (Fakten, Daten, Nachrichten)
- "analysis": Analysieren, bewerten, vergleichen
- "creation": Etwas Neues erstellen oder generieren
- "modification": Etwas Ändern, aktualisieren, korrigieren
- "question": Wissensfrage beantworten
- "command": Direkter Befehl ausführen
- "unknown": Unklar

{tools_section}Antworte AUSSCHLIESSLICH mit JSON (kein Text davor oder danach):
{{"intent_type": "...", "confidence": 0.0-1.0, "reasoning": "Kurze Begründung", "suggested_tools": [{{"tool_name": "...", "reason": "...", "priority": 1}}], "metadata": {{"language": "de|en", "complexity": "low|medium|high"}}}}"""
        
        # User-Prompt: NUR die zu analysierende Nachricht
        context_hint = ""
        if context:
            history = context.get("history", [])
            if history:
                context_hint = f"\n(Konversations-Kontext: {len(history)} vorherige Nachrichten)"
        
        user_prompt = f"Klassifiziere diese Anfrage:{context_hint}\n\n\"{user_message}\""
        
        return system_prompt, user_prompt
    
    def _get_default_llm(self):
        """Lädt Standard-LLM falls keiner übergeben wurde"""
        # Fallback-Implementierung
        logger.warning("⚠️ Kein LLM-Client übergeben - verwende Default")
        try:
            from scripts.model_loader import ModelLoader
            return ModelLoader()
        except Exception:
            logger.error("❌ Konnte Standard-LLM nicht laden!")
            return None
    
    def _get_timestamp(self) -> str:
        """Gibt aktuellen Timestamp zurück"""
        from datetime import datetime
        return datetime.now().isoformat()
    
    def get_stats(self) -> Dict[str, Any]:
        """Gibt Statistiken über Intent-Detection zurück"""
        return {
            "total_intents_detected": len(self.intent_history),
            "llm_caller_stats": {
                "name": "GuaranteedLLMCaller",
                "max_retries": self.llm_caller.max_retries,
                "min_response_length": self.llm_caller.min_response_length
            },
            "parsing_stats": self.response_handler.get_parsing_stats(),
            "recent_intents": [
                {
                    "type": item["result"].intent_type.value,
                    "confidence": item["result"].confidence,
                    "success": item["result"].llm_call_success
                }
                for item in self.intent_history[-10:]  # Last 10
            ]
        }


# Backward compatibility alias
GenericIntentDetector = EnhancedIntentDetector
