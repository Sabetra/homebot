"""
Smart Feedback Hints System
============================

Generiert hilfreiche Tipps basierend auf User-Feedback,
um User zu helfen den Bot besser zu nutzen.

Unterstützt zwei Modi:
1. Pattern-basiert (schnell, deterministisch)
2. LLM-basiert (intelligent, adaptiv) - EMPFOHLEN
"""

from typing import Optional, Dict, List
import logging

logger = logging.getLogger(__name__)

# Try to import LLM hint generator
try:
    from utils.llm_hints import generate_llm_hint as _generate_llm_hint, LLMHintGenerator
    LLM_HINTS_AVAILABLE = True
    llm_hint_fn = _generate_llm_hint
except ImportError:
    LLM_HINTS_AVAILABLE = False
    llm_hint_fn = None
    logger.warning("LLM hints not available - falling back to pattern-based hints")


class SmartHintGenerator:
    """Generiert kontextuelle Tipps basierend auf Feedback-Text."""
    
    # Hint-Datenbank: Pattern → Hilfreicher Tipp
    HINT_DATABASE = {
        "zu_lang": {
            "keywords": ["lang", "ausführlich", "zu viel", "kürzer", "kompakt", "knapp"],
            "hint": """💡 **Tipp für präzisere Antworten:**
- Frage konkreter: *"Fasse in 3 Punkten zusammen"*
- Oder: *"Erkläre kurz in 2-3 Sätzen"*
- Nutze: *"Nur das Wichtigste bitte!"*"""
        },
        
        "zu_kurz": {
            "keywords": ["kurz", "zu wenig", "mehr", "detail", "ausführlicher", "genauer"],
            "hint": """💡 **Tipp für ausführlichere Antworten:**
- Frage nach Details: *"Erkläre das ausführlicher"*
- Oder: *"Gib mir mehr Beispiele"*
- Nutze: *"Mit konkreten Details bitte!"*"""
        },
        
        "kontext_fehlt": {
            "keywords": ["kontext", "pdf", "quelle", "dokument", "datei", "upload", "hochladen"],
            "hint": """💡 **Tipp für besseren Kontext:**
- Lade die relevante PDF im Seitenmenü hoch
- Frage dann spezifisch: *"Was steht im PDF zu X?"*
- Erwähne Seitenzahlen wenn bekannt: *"Laut Seite 5..."*
- Nutze den **Wellbeing-Tab** für psychologische Fachkonzepte"""
        },
        
        "unklar": {
            "keywords": ["unklar", "verwirrend", "verstehe nicht", "chaotisch", "wirr", "durcheinander"],
            "hint": """💡 **Tipp für klarere Antworten:**
- Stelle Rückfragen! Ich erkläre gerne genauer.
- Bitte um Beispiele: *"Erkläre mit einem Beispiel"*
- Oder: *"Erkläre es einfacher"* bzw. *"Wie für einen Laien"*"""
        },
        
        "falsch": {
            "keywords": ["falsch", "fehler", "stimmt nicht", "inkorrekt", "unrichtig", "wrong"],
            "hint": """💡 **Tipp bei Fehlern:**
- Gib mir mehr Kontext (PDF, Link, etc.)
- Weise mich explizit auf den Fehler hin
- Frage nochmal mit mehr Details
- Falls Fachkonzept: Nutze den **Wellbeing-Tab** mit aktivierter Knowledge-Graph-Suche"""
        },
        
        "keine_quelle": {
            "keywords": ["quelle", "beleg", "nachweis", "referenz", "zitat", "woher"],
            "hint": """💡 **Tipp für Quellenangaben:**
- Frage explizit: *"Mit Quellenangaben bitte!"*
- Oder: *"Auf welcher Seite steht das?"*
- Bei hochgeladener PDF: *"Zitiere aus dem PDF"*"""
        },
        
        "zu_allgemein": {
            "keywords": ["allgemein", "vage", "unspezifisch", "oberflächlich", "pauschal"],
            "hint": """💡 **Tipp für spezifischere Antworten:**
- Stelle konkretere Fragen
- Gib mehr Kontext zu deiner Situation
- Frage nach konkreten Beispielen oder Fallstudien"""
        },
        
        "zu_technisch": {
            "keywords": ["technisch", "fachbegriff", "fachchinesisch", "kompliziert", "jargon"],
            "hint": """💡 **Tipp für verständlichere Sprache:**
- Frage nach einfacher Erklärung: *"Erkläre es wie für Laien"*
- Oder: *"Ohne Fachbegriffe bitte"*
- Nutze: *"Mit einfachen Beispielen"*"""
        }
    }
    
    def __init__(self, use_llm: bool = True, llm_model: str = "mistral"):
        """
        Initialize hint generator.
        
        Args:
            use_llm: Use LLM-based hints (recommended) or fall back to patterns
            llm_model: Ollama model to use for LLM hints
        """
        self.use_llm = use_llm and LLM_HINTS_AVAILABLE
        self.llm_model = llm_model
        
        if use_llm and not LLM_HINTS_AVAILABLE:
            logger.warning("LLM hints requested but not available - using pattern fallback")
    
    def generate_hint(
        self, 
        feedback_text: Optional[str],
        quick_category: Optional[str] = None,
        context: Optional[Dict] = None
    ) -> Optional[str]:
        """
        Generiert passenden Hint basierend auf Feedback.
        
        Args:
            feedback_text: Freitext-Feedback vom User
            quick_category: Kategorie aus Quick-Select (z.B. "Zu lang")
            context: Zusätzlicher Kontext (z.B. ob PDF hochgeladen)
        
        Returns:
            Hilfreicher Tipp oder None falls kein passender Hint
        """
        # OPTION 1: LLM-basierte Hints (intelligent, adaptiv)
        if self.use_llm and LLM_HINTS_AVAILABLE and llm_hint_fn:
            try:
                hint = llm_hint_fn(
                    feedback_text=feedback_text,
                    quick_category=quick_category,
                    context=context or {},
                    model=self.llm_model
                )
                if hint:
                    logger.info("Generated LLM-based hint")
                    return hint
            except Exception as e:
                logger.warning(f"LLM hint generation failed, falling back to patterns: {e}")
        
        # OPTION 2: Pattern-basierte Hints (Fallback)
        # Quick-Category hat Priorität
        if quick_category:
            hint = self._get_hint_for_category(quick_category)
            if hint:
                return hint
        
        # Fallback: Analyse Freitext
        if feedback_text:
            hint = self._analyze_feedback_text(feedback_text)
            if hint:
                return hint
        
        # Kein spezifischer Hint gefunden
        return self._get_default_hint()
    
    def _get_hint_for_category(self, category: str) -> Optional[str]:
        """Mappe Quick-Select-Kategorie auf Hint."""
        category_mapping = {
            "Zu lang": "zu_lang",
            "Zu kurz": "zu_kurz",
            "Kontext fehlt": "kontext_fehlt",
            "Unklar": "unklar",
            "Fehler": "falsch",
            "Keine Quelle": "keine_quelle",
            "Zu allgemein": "zu_allgemein",
            "Zu technisch": "zu_technisch"
        }
        
        hint_key = category_mapping.get(category)
        if hint_key and hint_key in self.HINT_DATABASE:
            return self.HINT_DATABASE[hint_key]["hint"]
        
        return None
    
    def _analyze_feedback_text(self, text: str) -> Optional[str]:
        """Analysiere Freitext und finde passenden Hint."""
        text_lower = text.lower()
        
        # Versuche jeden Pattern zu matchen
        matches: List[tuple] = []  # (hint_key, match_count)
        
        for hint_key, hint_data in self.HINT_DATABASE.items():
            keywords = hint_data["keywords"]
            match_count = sum(1 for kw in keywords if kw in text_lower)
            
            if match_count > 0:
                matches.append((hint_key, match_count))
        
        # Sortiere nach Anzahl Matches (beste zuerst)
        if matches:
            matches.sort(key=lambda x: -x[1])
            best_match_key = matches[0][0]
            return self.HINT_DATABASE[best_match_key]["hint"]
        
        return None
    
    def _get_default_hint(self) -> str:
        """Fallback-Hint wenn kein spezifischer Match."""
        return """💡 **Allgemeiner Tipp:**
Je spezifischer deine Frage, desto besser die Antwort!
Nutze Kontext (PDFs, vorherige Nachrichten) für mehr Präzision."""
    
    def get_all_categories(self) -> List[str]:
        """Gibt alle verfügbaren Quick-Select-Kategorien zurück."""
        return [
            "Zu lang",
            "Zu kurz", 
            "Kontext fehlt",
            "Unklar",
            "Fehler",
            "Keine Quelle",
            "Zu allgemein",
            "Zu technisch",
            "Anderes"
        ]


# Globale Instanz für einfache Nutzung
_hint_generator = None

def get_hint_generator(use_llm: bool = True, llm_model: str = "mistral") -> SmartHintGenerator:
    """
    Singleton-Access zum Hint-Generator.
    
    Args:
        use_llm: Use LLM-based hints (recommended) or pattern-based
        llm_model: Ollama model for LLM hints
    """
    global _hint_generator
    if _hint_generator is None:
        _hint_generator = SmartHintGenerator(use_llm=use_llm, llm_model=llm_model)
    return _hint_generator


# Convenience-Funktion für direkten Zugriff
def generate_smart_hint(
    feedback_text: Optional[str] = None,
    quick_category: Optional[str] = None,
    context: Optional[Dict] = None,
    use_llm: bool = True,
    llm_model: str = "mistral"
) -> Optional[str]:
    """
    Convenience-Funktion zum Generieren von Hints.
    
    Args:
        feedback_text: User feedback text
        quick_category: Quick-select category
        context: Additional context (has_pdf, original_query, etc.)
        use_llm: Use LLM-based hints (recommended) or pattern-based
        llm_model: Ollama model for LLM hints
    
    Beispiel:
        >>> hint = generate_smart_hint(
        ...     feedback_text="Antwort ist zu lang",
        ...     context={'has_pdf': True}
        ... )
        >>> print(hint)
        💡 **Tipp:** Frage konkreter, z.B. 'Fasse in 3 Punkten zusammen'
    """
    generator = get_hint_generator(use_llm=use_llm, llm_model=llm_model)
    return generator.generate_hint(feedback_text, quick_category, context)


if __name__ == "__main__":
    # Quick Test
    print("=" * 60)
    print("SMART HINT GENERATOR - TEST")
    print("=" * 60)
    
    test_cases = [
        ("Antwort ist viel zu lang und ausführlich", None),
        ("Ich verstehe das nicht, sehr unklar", None),
        ("Kontext aus dem PDF fehlt komplett", None),
        (None, "Zu lang"),
        (None, "Kontext fehlt"),
        ("Wo ist die Quelle?", None),
    ]
    
    for text, category in test_cases:
        print(f"\n📝 Input: text='{text}', category='{category}'")
        hint = generate_smart_hint(text, category)
        print(f"💡 Hint:\n{hint}\n")
        print("-" * 60)
