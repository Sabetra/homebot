"""
LLM-based Smart Hint Generator
================================

Generiert intelligente, kontextuelle Tipps für User-Feedback
unter Verwendung eines LLMs (Ollama).

Vorteile gegenüber Pattern-Matching:
- Versteht Nuancen und Kontext
- Adaptiv für alle Feedback-Typen
- Keine manuelle Pattern-Wartung nötig
"""

import logging
from typing import Optional, Dict, Any
import json

logger = logging.getLogger(__name__)

# Try to import ollama (with fallback)
try:
    import ollama
    OLLAMA_AVAILABLE = True
except ImportError:
    OLLAMA_AVAILABLE = False
    logger.warning("Ollama not available - LLM hints will fall back to pattern-based")


class LLMHintGenerator:
    """Generiert intelligente Hints via LLM."""
    
    def __init__(
        self, 
        model: str = "mistral",
        fallback_to_pattern: bool = True
    ):
        """
        Initialize LLM hint generator.
        
        Args:
            model: Ollama model to use (default: mistral)
            fallback_to_pattern: Fall back to pattern-based hints on error
        """
        self.model = model
        self.fallback_to_pattern = fallback_to_pattern
        
        if not OLLAMA_AVAILABLE:
            logger.warning("Ollama not available - hints will use pattern fallback")
    
    def generate_hint(
        self,
        feedback_text: Optional[str],
        quick_category: Optional[str] = None,
        context: Optional[Dict] = None
    ) -> Optional[str]:
        """
        Generiert intelligenten Hint basierend auf Feedback.
        
        Args:
            feedback_text: Freitext-Feedback vom User
            quick_category: Kategorie aus Quick-Select
            context: Zusätzlicher Kontext (PDF, Query, etc.)
        
        Returns:
            Hilfreicher Tipp oder None
        """
        # Validierung
        if not feedback_text or len(feedback_text.strip()) < 3:
            return self._get_minimal_feedback_hint()
        
        # Versuche LLM-Hint
        if OLLAMA_AVAILABLE:
            try:
                hint = self._generate_llm_hint(
                    feedback_text, 
                    quick_category, 
                    context or {}
                )
                if hint:
                    return hint
            except Exception as e:
                logger.warning(f"LLM hint generation failed: {e}")
        
        # Fallback auf Pattern-basiert
        if self.fallback_to_pattern:
            return self._generate_pattern_hint(feedback_text, quick_category)
        
        return self._get_generic_hint()
    
    def _generate_llm_hint(
        self,
        feedback_text: str,
        quick_category: Optional[str],
        context: Dict[str, Any]
    ) -> Optional[str]:
        """Generiert Hint via Ollama LLM."""
        
        # Baue Kontext-Information
        context_info = []
        if context.get('has_pdf'):
            context_info.append("- User hat eine PDF hochgeladen")
        if context.get('original_query'):
            query_preview = context['original_query'][:100]
            context_info.append(f"- Original-Frage: \"{query_preview}\"")
        if quick_category and quick_category != "Anderes":
            context_info.append(f"- Kategorie: {quick_category}")
        
        context_str = "\n".join(context_info) if context_info else "- Kein zusätzlicher Kontext"
        
        # Prompt für LLM
        prompt = f"""Du bist ein hilfreicher Assistent für einen Psychology-Chatbot.

Der User hat negatives Feedback zu einer Antwort gegeben:
"{feedback_text}"

Kontext:
{context_str}

Deine Aufgabe: Erstelle einen KONKRETEN, ACTIONABLE Tipp (max 50 Wörter), 
wie der User beim nächsten Mal bessere Antworten bekommen kann.

WICHTIG:
- Sei SPEZIFISCH, nicht generisch
- Gib konkrete Beispiel-Fragen
- Nutze den Kontext (z.B. erwähne PDF wenn hochgeladen)
- Format: "💡 **Tipp:** [Dein Tipp]"

GUTE Beispiele:
- "💡 **Tipp:** Frage konkreter, z.B. 'Fasse in 3 Punkten zusammen' für kürzere Antworten"
- "💡 **Tipp:** Ich sehe du hast eine PDF - frage spezifischer: 'Was steht auf Seite X zu Konzept Y?'"
- "💡 **Tipp:** Bitte um einfachere Erklärung: 'Erkläre es ohne Fachbegriffe' oder 'Wie für einen Laien'"

SCHLECHTE Beispiele (zu generisch):
- "Stelle bessere Fragen"
- "Sei präziser"
- "Gib mehr Kontext"

Jetzt dein konkreter Tipp:"""

        try:
            response = ollama.generate(
                model=self.model,
                prompt=prompt,
                options={
                    "temperature": 0.3,  # Deterministischer
                    "max_tokens": 100,
                    "top_p": 0.9,
                    "num_predict": 100
                }
            )
            
            hint: str = str(response['response']).strip()
            
            # Bereinigung
            if hint.startswith('"') and hint.endswith('"'):
                hint = hint[1:-1]
            
            if not hint.startswith("💡"):
                hint = f"💡 **Tipp:** {hint}"
            
            # Validierung: Hint sollte nicht zu kurz sein
            if len(hint) < 20:
                logger.warning(f"LLM hint too short: {hint}")
                return None
            
            logger.info(f"Generated LLM hint: {hint[:50]}...")
            return hint
            
        except Exception as e:
            logger.error(f"Ollama generation failed: {e}")
            return None
    
    def _generate_pattern_hint(
        self,
        feedback_text: str,
        quick_category: Optional[str]
    ) -> str:
        """Fallback: Einfache Pattern-basierte Hints."""
        
        text_lower = feedback_text.lower()
        
        # Quick-Category hat Priorität
        if quick_category:
            category_hints = {
                "Zu lang": "💡 **Tipp:** Frage konkreter, z.B. 'Fasse in 3 Punkten zusammen'",
                "Zu kurz": "💡 **Tipp:** Frage nach mehr Details: 'Erkläre das ausführlicher'",
                "Kontext fehlt": "💡 **Tipp:** Lade die relevante PDF im Seitenmenü hoch für besseren Kontext",
                "Unklar": "💡 **Tipp:** Stelle Rückfragen, ich erkläre gerne genauer!",
                "Fehler": "💡 **Tipp:** Gib mir mehr Kontext (PDF, Details) für präzisere Antworten",
                "Keine Quelle": "💡 **Tipp:** Frage explizit: 'Mit Quellenangaben bitte!'",
                "Zu allgemein": "💡 **Tipp:** Stelle spezifischere Fragen mit mehr Kontext",
                "Zu technisch": "💡 **Tipp:** Frage nach einfacher Erklärung: 'Ohne Fachbegriffe bitte'"
            }
            if quick_category in category_hints:
                return category_hints[quick_category]
        
        # Keyword-basiertes Pattern-Matching
        if any(kw in text_lower for kw in ["lang", "ausführlich", "zu viel"]):
            return "💡 **Tipp:** Frage konkreter, z.B. 'Fasse in 3 Punkten zusammen'"
        
        if any(kw in text_lower for kw in ["kontext", "pdf", "quelle", "dokument"]):
            return "💡 **Tipp:** Lade die relevante PDF hoch für besseren Kontext!"
        
        if any(kw in text_lower for kw in ["unklar", "verwirrend", "verstehe nicht"]):
            return "💡 **Tipp:** Stelle Rückfragen, ich erkläre gerne genauer!"
        
        if any(kw in text_lower for kw in ["kurz", "mehr", "detail"]):
            return "💡 **Tipp:** Frage nach Details: 'Erkläre das ausführlicher bitte'"
        
        if any(kw in text_lower for kw in ["technisch", "kompliziert", "fachbegriff"]):
            return "💡 **Tipp:** Bitte um einfachere Sprache: 'Erkläre es ohne Fachbegriffe'"
        
        return self._get_generic_hint()
    
    def _get_minimal_feedback_hint(self) -> str:
        """Hint wenn Feedback zu kurz/leer."""
        return "💡 **Tipp:** Beschreibe genauer was nicht passte, damit ich besser lernen kann!"
    
    def _get_generic_hint(self) -> str:
        """Generic Fallback-Hint."""
        return "💡 **Tipp:** Je spezifischer deine Frage, desto besser die Antwort!"


# Singleton-Instanz
_llm_hint_generator = None

def get_llm_hint_generator(model: str = "mistral") -> LLMHintGenerator:
    """Singleton-Access zum LLM Hint Generator."""
    global _llm_hint_generator
    if _llm_hint_generator is None:
        _llm_hint_generator = LLMHintGenerator(model=model)
    return _llm_hint_generator


def generate_llm_hint(
    feedback_text: Optional[str],
    quick_category: Optional[str] = None,
    context: Optional[Dict] = None,
    model: str = "mistral"
) -> Optional[str]:
    """
    Convenience-Funktion für LLM-basierte Hints.
    
    Args:
        feedback_text: User-Feedback-Text
        quick_category: Kategorie aus Quick-Select
        context: Dict mit Kontext-Info (has_pdf, original_query, etc.)
        model: Ollama model (default: mistral)
    
    Returns:
        Intelligenter Hint oder None
    
    Example:
        >>> hint = generate_llm_hint(
        ...     "Antwort war zu technisch",
        ...     context={'has_pdf': True, 'original_query': 'Was ist Resilienz?'}
        ... )
        >>> print(hint)
        💡 **Tipp:** Bitte um einfachere Erklärung: 'Erkläre Resilienz 
                     aus dem PDF ohne Fachbegriffe'
    """
    generator = get_llm_hint_generator(model=model)
    return generator.generate_hint(feedback_text, quick_category, context)


if __name__ == "__main__":
    # Test
    print("=" * 70)
    print("LLM-BASED HINT GENERATOR - TEST")
    print("=" * 70)
    
    test_cases: list[Dict[str, Any]] = [
        {
            "feedback": "Die Antwort war viel zu lang und ausführlich",
            "category": None,
            "context": {"has_pdf": False}
        },
        {
            "feedback": "Kontext aus dem PDF wurde nicht berücksichtigt",
            "category": "Kontext fehlt",
            "context": {"has_pdf": True, "original_query": "Was ist kognitive Dissonanz?"}
        },
        {
            "feedback": "Zu viele Fachbegriffe, verstehe ich nicht",
            "category": "Zu technisch",
            "context": {"has_pdf": False}
        },
    ]
    
    for i, test in enumerate(test_cases, 1):
        print(f"\n{'='*70}")
        print(f"TEST CASE {i}")
        print(f"{'='*70}")
        print(f"Feedback: \"{test['feedback']}\"")
        print(f"Category: {test['category']}")
        print(f"Context: {test['context']}")
        print(f"\nGenerating hint...")
        
        hint = generate_llm_hint(
            test['feedback'],
            test['category'],
            test['context']
        )
        
        print(f"\n💡 Generated Hint:\n{hint}")
        print()
