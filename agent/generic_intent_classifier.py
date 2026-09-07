"""
Generic Intent Classifier using LLM
Ersetzt hart codierte Keywords durch intelligente LLM-basierte Klassifikation
Mit universeller Entitäts-Erkennung und Konsistenz-Validierung für alle Domänen
"""

import json
import logging
from typing import Optional, Dict, List, Any, Tuple
from dataclasses import dataclass
from .universal_entity_validator import UniversalEntityValidator

@dataclass
class IntentClassification:
    """Ergebnis der Intent-Klassifikation"""
    needs_web_search: bool
    content_preference: Optional[List[str]]  # ["table", "kg"] oder None
    reasoning: str
    confidence: float = 1.0
    enhanced_query: Optional[str] = None  # Erweiterte Query für bessere RAG-Suche
    entity_context: Optional[str] = None  # Erkannte primäre Entität (universell, nicht nur Firmen)

class GenericIntentClassifier:
    """
    LLM-basierter Intent-Klassifikator
    Ersetzt alle hart codierten Keyword-Listen durch intelligente Klassifikation
    Integriert universelle Entitäts-Erkennung für alle Domänen (nicht nur Firmen)
    """
    
    def __init__(self, model_loader: Any = None) -> None:
        self.model_loader = model_loader
        self.logger = logging.getLogger(__name__)
        
        # Universelle Entitäts-Validierung (für alle Domänen, nicht nur Firmen)
        self.entity_validator = UniversalEntityValidator(model_loader)
        
        # Cache für Performance (gleiche Query = gleiche Klassifikation)
        self._classification_cache: Dict[str, IntentClassification] = {}
        
        # Prompt Template für Klassifikation
        self.classification_prompt = """Du bist ein Intent-Klassifikator für ein RAG-Chatbot-System mit einem kleinen lokalen LLM. Analysiere die Benutzeranfrage und entscheide:

**1. WEBSUCHE ERFORDERLICH?** (true/false)
Websuche ist nötig bei:
- Aktuellen/zeitkritischen Informationen (News, Wetter, aktuelle Ereignisse)
- Daten die sich häufig ändern (Börsenkurse, aktuelle Statistiken)
- Zeitreihen-Fragen zu aktuellen Entwicklungen
- Fragen nach "neuesten", "aktuellen", "heute", "2024/2025/2026" etc.
- **Spezifische Fakten über Tiere, Pflanzen, Arten, Spezies** (z.B. Aussehen, Lebensraum, Taxonomie)
- **Spezifische Fakten über Personen, Orte, Produkte, Unternehmen**
- **Lateinische/wissenschaftliche Namen, Klassifikation, biologische Details**
- **Jede Frage, deren korrekte Antwort spezifisches Faktenwissen erfordert, das ein kleines lokales LLM wahrscheinlich NICHT zuverlässig kennt**

**2. CONTENT-PRÄFERENZ?** (null oder Array)
- "table": Bei Statistiken, Zahlen, Vergleichen, Zeitreihen, tabellarischen Daten
- "kg": Bei Beziehungen, Strukturen, Zusammenhängen, Netzwerken
- Beide ["table", "kg"]: Bei komplexen Datenanalysen über Zeit

**3. KURZE BEGRÜNDUNG** (max. 50 Wörter)

**WICHTIG:** 
- Zeitreihen-Fragen (Entwicklung über Jahre) brauchen meist BEIDE: Websuche UND ["table", "kg"]
- NUR simple Grüße, einfache Mathe und breit bekannte Konzepte brauchen KEINE Websuche
- Nische Fakten (seltene Tierarten, spezifische Personen, konkrete Produkte) brauchen IMMER Websuche
- Bei Unsicherheit: lieber Websuche aktivieren

Antworte NUR im exakten JSON-Format:
```json
{
  "needs_web_search": true,
  "content_preference": ["table", "kg"],
  "reasoning": "Zeitreihen-Analyse über mehrere Jahre benötigt aktuelle Daten und strukturierte Darstellung"
}
```

Benutzeranfrage: "{query}"
"""

    def classify_intent(self, query: str) -> IntentClassification:
        """
        Klassifiziert eine Benutzeranfrage mit dem LLM
        
        Args:
            query: Die Benutzeranfrage
            
        Returns:
            IntentClassification mit allen Entscheidungen
        """
        if not query or not query.strip():
            return IntentClassification(
                needs_web_search=False,
                content_preference=None,
                reasoning="Leere Anfrage"
            )
        
        # Cache-Check für Performance
        cache_key = query.strip().lower()
        if cache_key in self._classification_cache:
            self.logger.debug(f"Cache hit für Intent-Klassifikation: {query[:50]}...")
            return self._classification_cache[cache_key]
        
        try:
            # LLM-Aufruf für Klassifikation
            classification = self._call_llm_for_classification(query)
            
            # ✅ SAFETY NET: Pattern-basierte Korrektur VOR Entity-Analyse
            # Ein kleines lokales LLM erkennt oft nicht, dass spezifische Fakten
            # Web-Suche benötigen. Diese Heuristik korrigiert offensichtliche Fehlklassifikationen.
            if not classification.needs_web_search:
                classification = self._apply_web_search_safety_net(query, classification)
            
            # Erweitere Query für entitätsspezifische Suche mit universeller LLM-Analyse
            if self.entity_validator:
                entity_analysis = self.entity_validator.analyze_entities(query)
                if entity_analysis.primary_entities:
                    classification.enhanced_query = entity_analysis.enhanced_query
                    # Nehme erste primäre Entität als Kontext
                    primary_entity = entity_analysis.primary_entities[0] if entity_analysis.primary_entities else None
                    classification.entity_context = primary_entity.get('name') if primary_entity else None
                    self.logger.info(f"Entitäts-Kontext erkannt: {classification.entity_context}, Domäne: {entity_analysis.domain_context}")
            
            # Cache speichern
            self._classification_cache[cache_key] = classification
            
            self.logger.info(f"Intent klassifiziert: {query[:50]}... → Web: {classification.needs_web_search}, Pref: {classification.content_preference}")
            
            return classification
            
        except Exception as e:
            self.logger.error(f"Fehler bei Intent-Klassifikation: {e}")
            # Fallback zu konservativen Defaults
            return IntentClassification(
                needs_web_search=True,  # Lieber zu viel als zu wenig
                content_preference=["table", "kg"],  # Beide Typen für beste Abdeckung
                reasoning=f"Fallback wegen Fehler: {str(e)[:30]}...",
                confidence=0.5
            )

    def _apply_web_search_safety_net(
        self, query: str, classification: IntentClassification
    ) -> IntentClassification:
        """
        Pattern-basiertes Safety Net: Korrigiert LLM-Fehlklassifikationen.
        
        Ein kleines lokales LLM erkennt oft nicht, dass Fragen über spezifische
        Entitäten (Personen, Tierarten, Produkte etc.) Web-Suche brauchen.
        Diese Methode erkennt solche Fälle und setzt needs_web_search=True.
        
        Wird NUR aufgerufen wenn das LLM needs_web_search=False entschieden hat.
        """
        q = query.lower()
        
        # 1. Fragen über spezifische Personen (Eigennamen erkennen)
        # Heuristik: Wörter mit Großbuchstaben die keine deutschen Satzanfänge sind
        words = query.split()
        # Ignoriere erstes Wort (Satzanfang) und typische deutsche Fragewörter
        skip_words = {
            'was', 'wer', 'wie', 'wo', 'wann', 'warum', 'welche', 'welcher',
            'welches', 'welchem', 'welchen', 'kannst', 'könntest', 'erzähl',
            'erzähle', 'beschreibe', 'erkläre', 'sage', 'sag', 'zeige',
            'nenne', 'gibt', 'ist', 'sind', 'hat', 'haben', 'weisst',
            'weißt', 'kennst', 'kennt', 'the', 'what', 'who', 'how',
            'where', 'when', 'why', 'tell', 'describe', 'explain',
            'du', 'mir', 'über', 'alles', 'etwas', 'ich', 'bitte',
        }
        
        capitalized_words = []
        for i, word in enumerate(words):
            clean = word.strip('?!.,;:()[]{}"""\'')
            if not clean:
                continue
            if clean[0].isupper() and clean.lower() not in skip_words and i > 0:
                capitalized_words.append(clean)
        
        # Wenn 2+ zusammenhängende Großbuchstaben-Wörter → wahrscheinlich Eigenname
        if len(capitalized_words) >= 2:
            self.logger.info(
                f"🔍 Safety Net: Eigenname erkannt ({' '.join(capitalized_words)}) → Web-Suche aktiviert"
            )
            classification.needs_web_search = True
            classification.reasoning += " [Safety Net: Eigenname erkannt → Web-Suche]"
            return classification
        
        # 2. Fragen nach spezifischen Arten/Spezies
        species_indicators = [
            'art ', 'arten ', 'spezies', 'rasse', 'gattung', 'taxonom',
            'bärbling', 'salmler', 'wels', 'barsch', 'cichlid',
            'species', 'breed', 'genus',
        ]
        look_indicators = [
            'wie sieht', 'wie sehen', 'wie aussehen', 'aussehen',
            'what does', 'what do', 'how does', 'look like',
            'beschreibe das aussehen', 'beschreibung',
        ]
        
        has_species = any(ind in q for ind in species_indicators)
        has_look = any(ind in q for ind in look_indicators)
        
        if has_species or (has_look and len(capitalized_words) >= 1):
            self.logger.info(
                f"🔍 Safety Net: Spezies/Aussehen-Frage erkannt → Web-Suche aktiviert"
            )
            classification.needs_web_search = True
            classification.reasoning += " [Safety Net: Spezies/Aussehen-Frage → Web-Suche]"
            return classification
        
        # 3. "Was weißt du über X?" / "Wer ist X?" Muster
        about_patterns = [
            'was weisst du über', 'was weißt du über',
            'was weisst du alles über', 'was weißt du alles über',
            'was kannst du mir über', 'was kannst du über',
            'erzähl mir über', 'erzähle mir über', 'erzähl mir von',
            'wer ist', 'wer war', 'who is', 'who was',
            'tell me about', 'what do you know about',
        ]
        
        if any(pattern in q for pattern in about_patterns) and len(capitalized_words) >= 1:
            self.logger.info(
                f"🔍 Safety Net: 'Über X'-Frage mit Eigenname → Web-Suche aktiviert"
            )
            classification.needs_web_search = True
            classification.reasoning += " [Safety Net: Entity-Frage → Web-Suche]"
            return classification
        
        return classification

    def _call_llm_for_classification(self, query: str) -> IntentClassification:
        """
        Ruft das LLM für die Intent-Klassifikation auf
        """
        if not self.model_loader or not hasattr(self.model_loader, 'model') or not self.model_loader.model:
            # Fallback ohne LLM - konservative Heuristik
            return self._fallback_classification(query)
        
        # Prompt mit Query füllen
        prompt = self.classification_prompt.format(query=query)
        
        try:
            # LLM-Generierung
            response = self.model_loader.model(
                prompt,
                max_tokens=150,
                temperature=0.1,  # Wenig Kreativität, konsistente Klassifikation
                stop=["```", "\n\n"]
            )
            
            # JSON-Response parsen
            response_text = response['choices'][0]['text'].strip()
            
            # Extrahiere JSON aus der Antwort
            json_start = response_text.find('{')
            json_end = response_text.rfind('}') + 1
            
            if json_start >= 0 and json_end > json_start:
                json_text = response_text[json_start:json_end]
                classification_data = json.loads(json_text)
                
                return IntentClassification(
                    needs_web_search=bool(classification_data.get('needs_web_search', False)),
                    content_preference=classification_data.get('content_preference'),
                    reasoning=str(classification_data.get('reasoning', 'Keine Begründung')),
                    confidence=0.9
                )
            else:
                raise ValueError("Keine gültige JSON-Antwort gefunden")
                
        except Exception as e:
            self.logger.warning(f"LLM-Klassifikation fehlgeschlagen: {e}")
            return self._fallback_classification(query)

    def _fallback_classification(self, query: str) -> IntentClassification:
        """
        Fallback-Klassifikation ohne LLM.
        
        Wird NUR aufgerufen, wenn das LLM nicht erreichbar ist.
        Gibt eine konservative Klassifikation zurück:
        - needs_web_search=True (lieber zu viel suchen als zu wenig)
        - content_preference=None (keine Keyword-Heuristiken — bleibt generisch)
        """
        return IntentClassification(
            needs_web_search=True,
            content_preference=None,
            reasoning="Fallback: LLM nicht verfügbar — konservativ web_search=True, keine Format-Heuristik",
            confidence=0.3
        )

    def clear_cache(self) -> None:
        """Leert den Klassifikation-Cache"""
        self._classification_cache.clear()
        self.logger.info("Intent-Klassifikation Cache geleert")

    def get_cache_stats(self) -> Dict[str, Any]:
        """Gibt Cache-Statistiken zurück"""
        return {
            "cache_size": len(self._classification_cache),
            "cached_queries": list(self._classification_cache.keys())[:5]  # Nur erste 5 für Debug
        }
