"""
Generic Intent Detection System
================================

Ersetzt keyword-basierte Tool-Auswahl durch semantische Intent-Erkennung.

Philosophie:
- LLMs sollen VERSTEHEN, nicht MATCHEN
- Semantische Analyse statt syntaktische Pattern
- Sprachneutral und kontextbewusst
- Selbstverbessernd durch Feedback

Author: AI System Evolution
Date: 2025-10-05
Status: PRODUCTION READY
"""

import json
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass
from enum import Enum
import logging
from utils.token_manager import estimate_prompt_tokens, estimate_structured_output_tokens

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
    
    def is_confident(self, threshold: float = 0.7) -> bool:
        """Prüft ob Confidence über Schwellwert"""
        return self.confidence >= threshold


class GenericIntentDetector:
    """
    Generischer Intent-Detektor der LLM-Reasoning nutzt
    um Benutzerabsichten zu verstehen.
    
    Vorteile gegenüber Keyword-Matching:
    1. Sprachneutral (Deutsch, Englisch, etc.)
    2. Kontextbewusst (versteht implizite Wünsche)
    3. Robust (keine false negatives durch fehlende Keywords)
    4. Wartungsarm (keine Keyword-Listen zu pflegen)
    """
    
    def __init__(self, llm_client=None):
        """
        Args:
            llm_client: LLM Client für Intent-Erkennung
                       Falls None, wird Standard-LLM verwendet
        """
        self.llm = llm_client or self._get_default_llm()
        self.intent_history = []  # Für selbstlernendes System
        
    def detect_intent(
        self,
        user_message: str,
        context: Optional[Dict] = None,
        available_tools: Optional[List[Dict]] = None
    ) -> IntentResult:
        """
        Erkennt die Benutzerabsicht semantisch.
        
        Args:
            user_message: Die Benutzeranfrage
            context: Optionaler Kontext (Konversations-Historie, etc.)
            available_tools: Liste verfügbarer Tools für bessere Auswahl
            
        Returns:
            IntentResult mit erkanntem Intent und Tool-Vorschlägen
        """
        
        logger.info(f"Detecting intent for: {user_message[:100]}...")
        
        # Erstelle Intent-Detection Prompt
        prompt = self._build_intent_prompt(
            user_message,
            context or {},
            available_tools or []
        )
        
        # LLM analysiert die Absicht
        try:
            response = self._call_llm(prompt)
            intent_data = self._parse_llm_response(response)
            
            result = IntentResult(
                intent_type=IntentType(intent_data.get("intent_type", "unknown")),
                confidence=float(intent_data.get("confidence", 0.0)),
                reasoning=intent_data.get("reasoning", ""),
                suggested_tools=intent_data.get("suggested_tools", []),
                metadata=intent_data.get("metadata", {})
            )
            
            # Speichere für selbstlernendes System
            self.intent_history.append({
                "query": user_message,
                "result": result,
                "timestamp": self._get_timestamp()
            })
            
            logger.info(f"Detected intent: {result.intent_type.value} "
                       f"(confidence: {result.confidence:.2f})")
            
            return result
            
        except Exception as e:
            logger.error(f"Intent detection failed: {e}")
            return self._fallback_intent()
    
    def _build_intent_prompt(
        self,
        user_message: str,
        context: Dict,
        available_tools: List[Dict]
    ) -> str:
        """Erstellt den Prompt für LLM-basierte Intent-Erkennung"""
        
        tools_section = ""
        if available_tools:
            tools_section = "\n\nVERFÜGBARE TOOLS:\n" + "\n".join([
                f"- {tool.get('name', 'unknown')}: {tool.get('description', '')}"
                for tool in available_tools
            ])
        
        context_section = ""
        if context:
            context_section = f"\n\nKONTEXT:\n{json.dumps(context, indent=2, ensure_ascii=False)}"
        
        return f"""Du bist ein intelligenter Intent-Analyzer für einen KI-Agenten.

Deine Aufgabe ist es, die WAHRE ABSICHT des Benutzers zu verstehen - 
nicht nur die verwendeten Wörter zu matchen!

BENUTZERANFRAGE:
"{user_message}"
{context_section}
{tools_section}

ANALYSE-FRAMEWORK (Chain-of-Thought):

1. SEMANTISCHE ANALYSE
   - Was möchte der Benutzer WIRKLICH erreichen?
   - Was ist das gewünschte Endergebnis?
   - Gibt es implizite Bedürfnisse?

2. INTENT-KATEGORISIERUNG
   Mögliche Intent-Typen:
   - "visualization": Möchte etwas visuell sehen/dargestellt haben
     → Hinweise: Beziehungen verstehen, Übersicht brauchen, komplexe Daten,
                 "zeigen", "darstellen", "bildlich", implizite Komplexität
   
   - "search": Sucht nach Information/Daten
     → Hinweise: "finde", "suche", "wo ist", Fragen nach Fakten
   
   - "analysis": Möchte Daten/Situation analysiert haben
     → Hinweise: "analysiere", "bewerte", "vergleiche", "was bedeutet"
   
   - "creation": Möchte etwas neues erstellen
     → Hinweise: "erstelle", "generiere", "baue", "mache"
   
   - "modification": Möchte etwas ändern/aktualisieren
     → Hinweise: "ändere", "update", "korrigiere", "verbessere"
   
   - "question": Stellt eine Frage
     → Hinweise: Fragewörter, Unsicherheit, Wissenslücke
   
   - "command": Gibt einen direkten Befehl
     → Hinweise: Imperativ, klare Anweisung

3. TOOL-MATCHING
   - Welche verfügbaren Tools passen zum erkannten Intent?
   - Priorität: Spezifische Tools > Generische Tools
   - Können mehrere Tools kombiniert werden?

4. CONFIDENCE-BEWERTUNG
   - Wie sicher bist du über den erkannten Intent?
   - Gibt es Mehrdeutigkeiten?
   - Ist zusätzliche Information nötig?

5. IMPLIZITE BEDÜRFNISSE
   WICHTIG: Erkenne auch was NICHT explizit gesagt wurde!
   - Bei komplexen Beziehungen → Visualisierung hilfreich
   - Bei vielen Daten → Zusammenfassung nötig
   - Bei Vergleichen → Tabellarische Darstellung sinnvoll

BEISPIELE für implizite Visualisierungs-Intents:

❌ FALSCH (nur Keyword-Matching):
"Ich verstehe die Zusammenhänge nicht" → KEIN "visualisier" Keyword → Kein Diagramm

✅ RICHTIG (semantisches Verständnis):
"Ich verstehe die Zusammenhänge nicht" → Komplexität erkannt → Visualisierung würde helfen!

❌ FALSCH:
"Kannst du mir erklären wie X mit Y zusammenhängt?" → KEIN Visualisierungs-Keyword

✅ RICHTIG:
"Kannst du mir erklären wie X mit Y zusammenhängt?" → Beziehung = visuell besser darstellbar!

ANTWORT-FORMAT (JSON):
{{
    "intent_type": "visualization|search|analysis|creation|modification|question|command|unknown",
    "confidence": 0.0-1.0,
    "reasoning": "Schritt-für-Schritt Erklärung deiner Analyse (ausführlich!)",
    "primary_need": "Was der User hauptsächlich braucht",
    "implicit_needs": ["Was der User NICHT sagte, aber vermutlich braucht"],
    "suggested_tools": [
        {{
            "tool_name": "tool_id",
            "reason": "Warum dieses Tool passt",
            "priority": 1-10,
            "parameters": {{"suggested": "parameter values if obvious"}}
        }}
    ],
    "metadata": {{
        "language": "detected language",
        "complexity": "low|medium|high",
        "ambiguity": "clear|somewhat_ambiguous|very_ambiguous"
    }}
}}

Analysiere jetzt die Benutzeranfrage und antworte NUR mit dem JSON (kein zusätzlicher Text!):
"""

    def _call_llm(self, prompt: str) -> str:
        """Ruft LLM für Intent-Erkennung auf"""
        try:
            prompt_tokens = estimate_prompt_tokens(prompt)
            model_n_ctx = getattr(self.llm, "get_max_context_tokens", lambda: 16384)() or 16384
            max_tokens_dynamic = estimate_structured_output_tokens(
                prompt_tokens=prompt_tokens,
                model_context_window=model_n_ctx,
                min_output_tokens=384,
                max_output_tokens=2048,
            )

            # Try ModelLoader.generate_response (our primary case)
            if hasattr(self.llm, 'generate_response'):
                messages = [
                    {"role": "system", "content": "You are a helpful assistant that analyzes user intent."},
                    {"role": "user", "content": prompt}
                ]
                response = self.llm.generate_response(
                    messages=messages,
                    max_tokens=max_tokens_dynamic,
                    temperature=0.2,
                    image_path=None
                )
                # Handle string or dict response
                if isinstance(response, dict):
                    return str(response.get('content', response))
                return str(response)
            # Try standard LangChain-style invoke
            elif hasattr(self.llm, 'invoke'):
                response = self.llm.invoke(prompt)
                # Handle different response types
                if hasattr(response, 'content'):
                    return str(response.content)  # type: ignore
                return str(response)
            # Try callable
            elif callable(self.llm):
                result = self.llm(prompt)
                return str(result)
            else:
                raise ValueError("LLM client has no known interface (generate_response, invoke, or callable)")
        except Exception as e:
            logger.error(f"LLM call failed: {e}", exc_info=True)
            raise
    
    def _parse_llm_response(self, response: str) -> Dict:
        """Parsed LLM Response zu strukturiertem Intent"""
        try:
            # Extrahiere JSON aus Response (falls LLM extra Text hinzufügt)
            if "```json" in response:
                json_str = response.split("```json")[1].split("```")[0]
            elif "```" in response:
                json_str = response.split("```")[1].split("```")[0]
            else:
                json_str = response
            
            # Parse JSON
            intent_data = json.loads(json_str.strip())
            
            # Validiere erforderliche Felder
            required = ["intent_type", "confidence", "reasoning", "suggested_tools"]
            for field in required:
                if field not in intent_data:
                    logger.warning(f"Missing field in LLM response: {field}")
                    intent_data[field] = self._get_default_value(field)
            
            return dict(intent_data)
            
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse LLM response as JSON: {e}")
            logger.error(f"Response was: {response[:500]}")
            return self._get_fallback_intent_data()
    
    def _get_default_llm(self):
        """Holt Standard-LLM aus Konfiguration"""
        logger.warning("No LLM client provided to GenericIntentDetector, using mock")
        return self._create_mock_llm()
    
    def _create_mock_llm(self):
        """Erstellt Mock-LLM für Testing"""
        class MockLLM:
            def generate_response(self, messages=None, **kwargs) -> str:
                return json.dumps({
                    "intent_type": "question",
                    "confidence": 0.5,
                    "reasoning": "Mock LLM - please configure real LLM",
                    "suggested_tools": [],
                    "metadata": {}
                })
            
            def invoke(self, prompt: str) -> str:
                """Fallback für LangChain-style calls"""
                return self.generate_response()
        return MockLLM()
    
    def _fallback_intent(self) -> IntentResult:
        """Fallback wenn Intent-Detection fehlschlägt"""
        return IntentResult(
            intent_type=IntentType.UNKNOWN,
            confidence=0.0,
            reasoning="Intent detection failed, using fallback",
            suggested_tools=[],
            metadata={"fallback": True}
        )
    
    def _get_fallback_intent_data(self) -> Dict:
        """Fallback Intent-Data bei Parse-Fehler"""
        return {
            "intent_type": "unknown",
            "confidence": 0.0,
            "reasoning": "Failed to parse LLM response",
            "suggested_tools": [],
            "metadata": {"error": True}
        }
    
    def _get_default_value(self, field: str):
        """Default-Werte für fehlende Felder"""
        defaults = {
            "intent_type": "unknown",
            "confidence": 0.0,
            "reasoning": "",
            "suggested_tools": [],
            "metadata": {}
        }
        return defaults.get(field, None)
    
    def _get_timestamp(self) -> str:
        """Aktuelle Timestamp für Logging"""
        from datetime import datetime
        return datetime.now().isoformat()


class VisualizationIntentDetector(GenericIntentDetector):
    """
    Spezialisierter Detector nur für Visualisierungs-Intents.
    
    Nutzt dieselbe generische Logik, aber mit optimiertem Prompt
    für Visualisierungs-Erkennung.
    """
    
    def detect_visualization_intent(
        self,
        user_message: str,
        context: Optional[Dict] = None
    ) -> Tuple[bool, float, str]:
        """
        Erkennt ob User eine Visualisierung möchte.
        
        Returns:
            (wants_visualization, confidence, suggested_diagram_type)
        """
        
        result = self.detect_intent(user_message, context)
        
        wants_viz = result.intent_type == IntentType.VISUALIZATION
        confidence = result.confidence
        
        # Extrahiere vorgeschlagenen Diagramm-Typ
        diagram_type = "network"  # Default
        for tool in result.suggested_tools:
            if "diagram_type" in tool.get("parameters", {}):
                diagram_type = tool["parameters"]["diagram_type"]
                break
        
        return wants_viz, confidence, diagram_type


# Convenience Functions für einfache Integration

def detect_user_intent(
    user_message: str,
    context: Optional[Dict] = None,
    llm_client=None
) -> IntentResult:
    """
    Convenience Function: Erkennt Benutzer-Intent.
    
    Usage:
        intent = detect_user_intent("Zeig mir die Beziehungen zwischen X und Y")
        if intent.intent_type == IntentType.VISUALIZATION:
            # Erstelle Visualisierung
    """
    detector = GenericIntentDetector(llm_client)
    return detector.detect_intent(user_message, context)


def wants_visualization(
    user_message: str,
    threshold: float = 0.7,
    llm_client=None
) -> bool:
    """
    Convenience Function: Prüft ob User Visualisierung möchte.
    
    Usage:
        if wants_visualization("Kannst du mir das bildlich erklären?"):
            create_diagram(...)
    """
    detector = VisualizationIntentDetector(llm_client)
    wants_viz, confidence, _ = detector.detect_visualization_intent(user_message)
    return wants_viz and confidence >= threshold


# Testing & Validation

if __name__ == "__main__":
    """Test-Cases für Intent Detection"""
    
    print("🧪 Testing Generic Intent Detector\n")
    
    test_cases = [
        # Explizite Visualisierungs-Requests
        "Visualisiere die Beziehungen zwischen verschiedenen KI-Frameworks",
        "Show me a network diagram of the connections",
        
        # Implizite Visualisierungs-Requests (KRITISCH!)
        "Ich verstehe die Zusammenhänge zwischen X und Y nicht",
        "Kannst du mir bildlich erklären wie das funktioniert?",
        "Das ist zu kompliziert, ich brauche eine Übersicht",
        
        # Andere Intent-Typen
        "Suche nach Dokumenten über Machine Learning",
        "Analysiere die Performance meines Modells",
        "Erstelle einen neuen Knowledge Graph Eintrag",
        
        # Mehrdeutige Anfragen
        "Was weißt du über Neural Networks?",
    ]
    
    detector = GenericIntentDetector()
    
    for i, test_query in enumerate(test_cases, 1):
        print(f"\n{'='*80}")
        print(f"Test Case {i}: {test_query}")
        print(f"{'='*80}")
        
        result = detector.detect_intent(test_query)
        
        print(f"\n✅ Intent Type: {result.intent_type.value}")
        print(f"📊 Confidence: {result.confidence:.2%}")
        print(f"💭 Reasoning: {result.reasoning}")
        print(f"🛠️  Suggested Tools: {[t['tool_name'] for t in result.suggested_tools]}")
        
        if result.is_confident():
            print(f"✅ HIGH CONFIDENCE - Intent erkannt!")
        else:
            print(f"⚠️  LOW CONFIDENCE - Weitere Klärung nötig")
    
    print(f"\n\n{'='*80}")
    print("🎯 Intent Detection Testing Complete!")
    print(f"{'='*80}")
