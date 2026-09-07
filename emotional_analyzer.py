#!/usr/bin/env python3
"""
GENERISCHE LLM-BASIERTE EMOTIONSANALYSE
=====================================

Ein modernes System für präzise Emotionserkennung ohne Keywords,
basierend auf LLM-Sentimentanalyse mit strukturierten Prompts.

Features:
- Generische, LLM-basierte Analyse
- Mehrere Emotionen parallel erkannt
- Intensitäts- und Konfidenz-Bewertung
- Krisenrisikoerkennung
- JSON-strukturierte Ergebnisse
- Session-Kontext-Integration
"""

import logging
from typing import Dict, List, Any, Optional, Union
from dataclasses import dataclass, asdict
from datetime import datetime

logger = logging.getLogger(__name__)

@dataclass
class EmotionalAnalysis:
    """Strukturierte Emotionsanalyse-Ergebnisse"""
    emotions: Dict[str, float]  # Emotion -> Intensität (0.0-1.0)
    dominant_emotion: str
    overall_valence: str  # "positiv", "neutral", "negativ"
    intensity_level: str  # "niedrig", "mittel", "hoch"
    crisis_indicators: bool
    emotional_complexity: str  # "einfach", "gemischt", "komplex"
    confidence: float  # 0.0-1.0
    reasoning: str
    timestamp: datetime
    
    def to_dict(self) -> Dict[str, Any]:
        """Konvertiert zu Dictionary für Storage"""
        data = asdict(self)
        data["timestamp"] = self.timestamp.isoformat()
        return data
    
    def get_primary_emotions(self, threshold: float = 0.3) -> List[str]:
        """Gibt alle Emotionen über Schwellenwert zurück"""
        return [emotion for emotion, intensity in self.emotions.items() 
                if intensity >= threshold]

class LLMEmotionalAnalyzer:
    """
    LLM-basierter Emotions-Analytiker ohne Keywords
    """
    
    def __init__(self, chat_logic: Any = None, model_loader: Any = None) -> None:
        """
        Initialisiert den Emotional Analyzer
        
        Args:
            chat_logic: Chat-Logic-Instanz für LLM-Aufrufe
        """
        self.chat_logic = chat_logic
        self.model_loader = model_loader or getattr(chat_logic, "model_loader", None)
        self.emotion_categories = [
            "freude", "trauer", "angst", "wut", "stress", 
            "hoffnung", "einsamkeit", "verwirrung", "zufriedenheit",
            "frustration", "überraschung", "scham"
        ]
        
        # Krisenrisikoindikator-Cache
        self.recent_analyses: List[EmotionalAnalysis] = []
        self.max_cache_size = 10
        
    def analyze_emotional_state(self, message: str, session_context: Optional[List[str]] = None) -> EmotionalAnalysis:
        """
        Führt umfassende Emotionsanalyse durch
        
        Args:
            message: Zu analysierende Nachricht
            session_context: Optionaler Session-Kontext für bessere Analyse
            
        Returns:
            EmotionalAnalysis-Objekt mit detaillierten Ergebnissen
        """
        llm_transport = self._get_llm_transport()
        if llm_transport is None:
            logger.warning(
                "⚠️ Keine LLM-Transportbindung verfügbar "
                "(weder model_loader noch chat_logic) - verwende Fallback-Analyse"
            )
            return self._fallback_analysis(message)
        
        try:
            # Erstelle strukturierten Analyse-Prompt
            analysis_prompt = self._create_analysis_prompt(message, session_context)
            
            # LLM-Aufruf für Emotionsanalyse
            logger.debug(f"🧠 Führe LLM-Emotionsanalyse durch für: '{message[:50]}...'")
            llm_response = llm_transport(analysis_prompt)
            
            # Parse JSON-Response
            emotional_data = self._parse_llm_response(llm_response)
            
            # Erstelle EmotionalAnalysis-Objekt
            analysis = EmotionalAnalysis(
                emotions=emotional_data.get('emotions', {}),
                dominant_emotion=emotional_data.get('dominant_emotion', 'neutral'),
                overall_valence=emotional_data.get('overall_valence', 'neutral'),
                intensity_level=emotional_data.get('intensity_level', 'niedrig'),
                crisis_indicators=emotional_data.get('crisis_indicators', False),
                emotional_complexity=emotional_data.get('emotional_complexity', 'einfach'),
                confidence=emotional_data.get('confidence', 0.5),
                reasoning=emotional_data.get('reasoning', 'LLM-Analyse durchgeführt'),
                timestamp=datetime.now()
            )
            
            # Cache-Management
            self._update_analysis_cache(analysis)
            
            logger.info(f"✅ Emotionsanalyse abgeschlossen: {analysis.dominant_emotion} "
                       f"({analysis.intensity_level}, {analysis.confidence:.2f} Konfidenz)")
            
            return analysis
            
        except Exception as e:
            logger.error(f"❌ Fehler bei LLM-Emotionsanalyse: {e}")
            return self._fallback_analysis(message)

    def _get_llm_transport(self) -> Optional[Any]:
        """Return a stateless LLM callable for emotional analysis.

        Root cause fix: emotional analysis must not run through the general
        chat pipeline because that pipeline carries mutable conversation state
        and can violate chat-template role contracts. The preferred transport is
        therefore ``model_loader.generate_response(messages=[...])`` with an
        isolated prompt. ``chat_logic.chat`` is kept only as a compatibility
        fallback for environments that have not yet been migrated.
        """
        model_loader = self.model_loader or getattr(self.chat_logic, "model_loader", None)
        if model_loader is not None and hasattr(model_loader, "generate_response"):
            self.model_loader = model_loader

            def _call_with_model_loader(prompt: str) -> str:
                return str(model_loader.generate_response(
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=768,
                    temperature=0.2,
                ) or "")

            return _call_with_model_loader

        if self.chat_logic is not None and hasattr(self.chat_logic, "chat"):
            return self.chat_logic.chat

        return None
    
    def _create_analysis_prompt(self, message: str, session_context: Optional[List[str]] = None) -> str:
        """Erstellt strukturierten Prompt für LLM-Emotionsanalyse"""
        
        context_section = ""
        if session_context:
            recent_context = session_context[-3:]  # Letzte 3 Nachrichten
            context_section = f"""
GESPRÄCHSKONTEXT (letzte Nachrichten):
{chr(10).join(f"- {ctx}" for ctx in recent_context)}
"""
        
        prompt = f"""Du bist ein spezialisierter Emotions-Analytiker für psychologische Unterstützung.

AUFGABE: Analysiere die emotionalen Inhalte der folgenden Nachricht präzise und empathisch.

{context_section}
AKTUELLE NACHRICHT: "{message}"

ANWEISUNG: Bewerte jede Emotion von 0.0 (nicht vorhanden) bis 1.0 (sehr stark ausgeprägt).

ANTWORT-FORMAT (NUR gültiges JSON):
{{
    "emotions": {{
        "freude": 0.0,
        "trauer": 0.0,
        "angst": 0.0,
        "wut": 0.0,
        "stress": 0.0,
        "hoffnung": 0.0,
        "einsamkeit": 0.0,
        "verwirrung": 0.0,
        "zufriedenheit": 0.0,
        "frustration": 0.0,
        "überraschung": 0.0,
        "scham": 0.0
    }},
    "dominant_emotion": "name_der_stärksten_emotion",
    "overall_valence": "positiv/neutral/negativ",
    "intensity_level": "niedrig/mittel/hoch",
    "crisis_indicators": false,
    "emotional_complexity": "einfach/gemischt/komplex",
    "confidence": 0.8,
    "reasoning": "Kurze Begründung der Analyse"
}}

WICHTIG:
- Berücksichtige Negationen ("nicht traurig" ≠ traurig)
- Achte auf Intensitätswörter ("sehr", "etwas", "extrem")
- crisis_indicators=NUR bei expliziter, unmittelbarer Gefahr:
  aktive Suizidabsicht mit Plan/Mittel, akute Selbstverletzung im Moment,
  direkte Fremdgefährdung oder schwere dissoziative Notfälle.
  Allgemeine Hoffnungslosigkeit, Hilflosigkeit, Traurigkeit oder
  emotionale Belastung sind KEINE Krisenindikatoren — sie sind
  normale Therapiethemen und gehören über die RiskClassifier-Ebene
  (low/elevated) bewertet, nicht über diesen Binär-Flag.
- Antwort muss gültiges JSON sein"""
        
        return prompt
    
    def _parse_llm_response(self, response: str) -> Dict[str, Any]:
        """Parsed LLM-Response zu strukturierten Daten mit robustem Multi-Methoden-Fallback"""
        try:
            # Importiere robusten Parser
            from utils.llm_json_parser import parse_llm_json, validate_emotion_schema
            
            # Default-Werte wenn Parsing komplett fehlschlägt
            default_emotions = {
                "freude": 0.0, "trauer": 0.3, "angst": 0.0, "wut": 0.0,
                "stress": 0.2, "hoffnung": 0.0, "einsamkeit": 0.0, "verwirrung": 0.4,
                "zufriedenheit": 0.0, "frustration": 0.0, "überraschung": 0.0, "scham": 0.0
            }
            default_data = {
                "emotions": default_emotions,
                "dominant_emotion": "verwirrung",
                "overall_valence": "neutral",
                "intensity_level": "niedrig",
                "crisis_indicators": False,
                "emotional_complexity": "einfach",
                "confidence": 0.3,
                "reasoning": "Fallback wegen Parse-Fehler"
            }
            
            # Robustes Parsing mit Multi-Methoden-Fallback
            data = parse_llm_json(
                response,
                schema_validator=validate_emotion_schema,
                default_on_error=default_data,
                debug=True  # Verbose logging für Debugging
            )
            
            return data
            
        except Exception as e:
            logger.error(f"❌ Kritischer Response-Parse-Fehler: {e}")
            logger.debug(f"Response war: {response[:500]}...")
            # Fallback zu Default-Werten
            return {
                "emotions": {emotion: 0.0 for emotion in self.emotion_categories},
                "dominant_emotion": "neutral",
                "overall_valence": "neutral",
                "intensity_level": "niedrig",
                "crisis_indicators": False,
                "emotional_complexity": "einfach",
                "confidence": 0.0,
                "reasoning": "Parse fehlgeschlagen - Fallback"
            }
    
    def _fallback_analysis(self, message: str) -> EmotionalAnalysis:
        """
        Fallback wenn LLM nicht verfügbar.

        SOTA-Entscheidung: Wir verzichten bewusst auf eine keyword-/lexikon-
        basierte Heuristik. Solche Heuristiken liefern systematisch falsch-
        positive Krisen-Flags (Negation, Ironie, Zitat) und falsch-negative
        Emotions-Profile (Idiome, Mehrsprachigkeit) und sind kein nachhaltig
        verlässliches Signal. Stattdessen geben wir ein explizit neutrales
        Profil mit Konfidenz 0 zurück. Sicherheitskritische Risiko-
        Klassifikation läuft unabhängig über
        ``wellbeing.care_plans.risk_classifier`` und braucht
        diesen Pfad nicht.
        """
        logger.warning(
            "🔄 Emotionsanalyse-Fallback aktiv (LLM unavailable). "
            "Returning neutral, low-confidence result."
        )
        emotions = {emotion: 0.0 for emotion in self.emotion_categories}
        return EmotionalAnalysis(
            emotions=emotions,
            dominant_emotion='neutral',
            overall_valence='neutral',
            intensity_level='niedrig',
            crisis_indicators=False,
            emotional_complexity='einfach',
            confidence=0.0,
            reasoning='LLM nicht verfügbar — neutrale Default-Analyse '
                     '(keine Keyword-Heuristik).',
            timestamp=datetime.now()
        )
    
    
    def _update_analysis_cache(self, analysis: EmotionalAnalysis) -> None:
        """Aktualisiert den Analyse-Cache für Trend-Erkennung"""
        self.recent_analyses.append(analysis)
        
        # Cache-Größe begrenzen
        if len(self.recent_analyses) > self.max_cache_size:
            self.recent_analyses.pop(0)
    
    def get_emotional_trend(self, lookback: int = 5) -> Dict[str, Any]:
        """
        Analysiert emotionale Trends über mehrere Nachrichten
        
        Args:
            lookback: Anzahl der letzten Analysen für Trend
            
        Returns:
            Dictionary mit Trend-Informationen
        """
        if len(self.recent_analyses) < 2:
            return {"trend": "insufficient_data", "direction": "stable"}
        
        recent = self.recent_analyses[-min(lookback, len(self.recent_analyses)):]
        
        # Durchschnittliche Valenz-Entwicklung
        valence_scores = []
        for analysis in recent:
            if analysis.overall_valence == 'positiv':
                valence_scores.append(1)
            elif analysis.overall_valence == 'negativ':
                valence_scores.append(-1)
            else:
                valence_scores.append(0)
        
        # Trend-Richtung
        if len(valence_scores) >= 2:
            trend_direction = "improving" if valence_scores[-1] > valence_scores[0] else \
                            "declining" if valence_scores[-1] < valence_scores[0] else "stable"
        else:
            trend_direction = "stable"
        
        # Krisenrisiko-Trend
        crisis_count = sum(1 for a in recent if a.crisis_indicators)
        crisis_risk = "high" if crisis_count >= 2 else "low"
        
        return {
            "trend": trend_direction,
            "direction": trend_direction,
            "average_valence": sum(valence_scores) / len(valence_scores),
            "crisis_risk": crisis_risk,
            "dominant_emotions": [a.dominant_emotion for a in recent],
            "analysis_count": len(recent)
        }
    
    def set_chat_logic(self, chat_logic: Any) -> None:
        """Setzt die Chat-Logic für LLM-Aufrufe"""
        self.chat_logic = chat_logic
        self.model_loader = getattr(chat_logic, "model_loader", None)
        logger.info("✅ Chat-Logic für Emotional Analyzer gesetzt")

    def set_model_loader(self, model_loader: Any) -> None:
        """Setzt den stateless ModelLoader für isolierte LLM-Aufrufe."""
        self.model_loader = model_loader
        logger.info("✅ ModelLoader für Emotional Analyzer gesetzt")


# Globale Instanz
emotional_analyzer = LLMEmotionalAnalyzer()

if __name__ == "__main__":
    # Test der Emotionsanalyse
    print("🧪 Teste LLM-Emotionsanalyse...")
    
    analyzer = LLMEmotionalAnalyzer()
    
    test_messages = [
        "Ich bin heute sehr glücklich und zufrieden!",
        "Mir geht es nicht so gut, ich fühle mich einsam.",
        "Ich weiß nicht mehr weiter, alles ist hoffnungslos.",
        "Die Arbeit stresst mich total, ich kann nicht mehr.",
        "Das Wetter ist okay, nichts Besonderes heute."
    ]
    
    for msg in test_messages:
        analysis = analyzer.analyze_emotional_state(msg)
        print(f"📝 '{msg}' → {analysis.dominant_emotion} ({analysis.intensity_level})")
    
    print("🎉 Emotionsanalyse-Test abgeschlossen!")
