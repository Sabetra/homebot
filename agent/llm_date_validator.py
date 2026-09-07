#!/usr/bin/env python3
"""
LLM-based Date Validator - Intelligente, context-aware Datumsvalidierung
"""

from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List, TypedDict
import re
import json
import logging

logger = logging.getLogger(__name__)


class DateInfo(TypedDict):
    """Einzelnes extrahiertes Datum"""
    date: datetime
    text: str
    format: str


class DateScore(TypedDict):
    """LLM-Bewertung eines Datums"""
    source_index: int
    relevance_score: float      # 0-1: Query-Relevanz
    freshness_score: float      # 0-1: Domain-spezifische Aktualität
    authority_score: float      # 0-1: Quelle vertrauenswürdig?
    final_score: float          # 0-1: Gewichtete Kombination
    reasoning: str              # LLM-Begründung
    warning_level: str          # "none"|"low"|"medium"|"high"


class ValidationResult(TypedDict):
    """Gesamt-Validierung"""
    has_warnings: bool
    average_score: float
    scores: List[DateScore]
    warning_message: Optional[str]
    debug_info: Dict[str, Any]


class LLMDateValidator:
    """
    Intelligenter Date-Validator mit LLM-basiertem Context-Awareness
    
    Features:
    - Query-Intent-Analyse (historisch vs. aktuell vs. zeitlos)
    - Domain-Velocity (News vs. Lexikon)
    - Source-Authority (Wikipedia vs. Blog)
    - Sliding-Scale Scoring (0-1, nicht binär)
    - Batch-Processing (1 LLM-Call für alle Quellen)
    """
    
    def __init__(self, model_loader):
        """
        Args:
            model_loader: ModelLoader-Instanz für LLM-Calls
        """
        self.model_loader = model_loader
        self.today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        
        # Deutsche Monats-Namen
        self.german_months = {
            'januar': 1, 'februar': 2, 'märz': 3, 'april': 4,
            'mai': 5, 'juni': 6, 'juli': 7, 'august': 8,
            'september': 9, 'oktober': 10, 'november': 11, 'dezember': 12
        }
        
        # Englische Monats-Namen
        self.english_months = {
            'january': 1, 'february': 2, 'march': 3, 'april': 4,
            'may': 5, 'june': 6, 'july': 7, 'august': 8,
            'september': 9, 'october': 10, 'november': 11, 'december': 12
        }
    
    def extract_dates_from_text(self, text: str) -> List[DateInfo]:
        """
        Extrahiert Datumsangaben aus Text (Regex-basiert)
        
        Unterstützte Formate:
        - 24. August 2025
        - 24.08.2025
        - 2025-08-24
        - August 24, 2025
        """
        dates: List[DateInfo] = []
        
        # Pattern 1: 24. August 2025 (Deutsch)
        pattern1 = r'(\d{1,2})\.\s*([A-Za-zä]+)\s*(\d{4})'
        for match in re.finditer(pattern1, text, re.IGNORECASE):
            day = int(match.group(1))
            month_name = match.group(2).lower()
            year = int(match.group(3))
            
            month = self.german_months.get(month_name) or self.english_months.get(month_name)
            if month:
                try:
                    date = datetime(year, month, day)
                    dates.append({
                        'date': date,
                        'text': match.group(0),
                        'format': 'german_text'
                    })
                except ValueError:
                    pass
        
        # Pattern 2: 24.08.2025 (Numerisch)
        pattern2 = r'(\d{1,2})\.(\d{1,2})\.(\d{4})'
        for match in re.finditer(pattern2, text):
            day = int(match.group(1))
            month = int(match.group(2))
            year = int(match.group(3))
            
            try:
                date = datetime(year, month, day)
                dates.append({
                    'date': date,
                    'text': match.group(0),
                    'format': 'german_numeric'
                })
            except ValueError:
                pass
        
        # Pattern 3: 2025-08-24 (ISO)
        pattern3 = r'(\d{4})-(\d{1,2})-(\d{1,2})'
        for match in re.finditer(pattern3, text):
            year = int(match.group(1))
            month = int(match.group(2))
            day = int(match.group(3))
            
            try:
                date = datetime(year, month, day)
                dates.append({
                    'date': date,
                    'text': match.group(0),
                    'format': 'iso'
                })
            except ValueError:
                pass
        
        # Pattern 4: August 24, 2025 (Englisch)
        pattern4 = r'([A-Za-z]+)\s+(\d{1,2}),?\s+(\d{4})'
        for match in re.finditer(pattern4, text, re.IGNORECASE):
            month_name = match.group(1).lower()
            day = int(match.group(2))
            year = int(match.group(3))
            
            month = self.english_months.get(month_name) or self.german_months.get(month_name)
            if month:
                try:
                    date = datetime(year, month, day)
                    dates.append({
                        'date': date,
                        'text': match.group(0),
                        'format': 'english_text'
                    })
                except ValueError:
                    pass
        
        return dates
    
    def _build_batch_prompt(
        self, 
        query: str,
        sources_with_dates: List[Dict[str, Any]]
    ) -> str:
        """
        Erstelle optimalen Batch-Prompt für LLM-Scoring
        
        Args:
            query: User-Query
            sources_with_dates: Liste von Quellen mit extrahierten Daten
        
        Returns:
            Prompt-String für LLM
        """
        prompt = f"""Du bist ein Experte für Informations-Aktualität und -Relevanz.

USER QUERY:
"{query}"

HEUTE: {self.today.strftime('%d.%m.%Y')}

QUELLEN MIT DATEN:
"""
        
        for src in sources_with_dates:
            oldest_date = min(d['date'] for d in src['dates'])
            days_old = (self.today - oldest_date).days
            
            prompt += f"""
[QUELLE {src['index']}]
URL: {src['url']}
Ältestes Datum: {oldest_date.strftime('%d.%m.%Y')} (vor {days_old} Tagen)
Snippet: "{src['snippet'][:300]}..."
"""
        
        prompt += """
AUFGABE:
Bewerte für JEDE Quelle die Relevanz und Aktualität des Datums im Kontext der User-Query.

FAKTOREN:

1. **Query-Intent:** Was will der User?
   - Historisch: "Wer war Einstein?" → Alte Daten sind PERFEKT
   - Aktuell: "Wetter morgen?" → Nur neue Daten
   - Zeitlos: "Was ist Python?" → Alter moderat wichtig
   - Trend: "KI-Trends 2026" → Sehr neue Daten nötig

2. **Domain-Velocity:** Wie schnell ändert sich das Thema?
   - News/Wetter: EXTREM schnell (Stunden-Tage)
   - Tech/Software: Schnell (Wochen-Monate)
   - Wissenschaft: Mittel (Monate-Jahre)
   - Geschichte/Lexikon: SEHR langsam (Jahre-Dekaden)

3. **Source-Authority:** Ist die Quelle vertrauenswürdig?
   - Wikipedia/Gov (.gov/.edu): HIGH Authority
   - Etablierte News: MEDIUM Authority
   - Blogs/Foren: LOW Authority
   - Je höher Authority, desto mehr verzeihen wir ältere Daten

SCORING (0.0 - 1.0):
- relevance_score: Passt die Info zur Query? (0=irrelevant, 1=perfekt)
- freshness_score: Ist die Info aktuell genug? (0=zu alt, 1=perfekt aktuell)
- authority_score: Ist die Quelle vertrauenswürdig? (0=unsicher, 1=sehr vertrauenswürdig)
- final_score: Gewichtete Kombination (0.4*relevance + 0.4*freshness + 0.2*authority)

WARNING LEVEL:
- "none": final_score >= 0.7 (alles gut)
- "low": 0.5 <= final_score < 0.7 (leichte Bedenken)
- "medium": 0.3 <= final_score < 0.5 (moderate Warnung)
- "high": final_score < 0.3 (starke Warnung)

OUTPUT FORMAT (NUR JSON, KEIN ANDERER TEXT):
[
  {
    "source_index": 0,
    "relevance_score": 0.95,
    "freshness_score": 1.0,
    "authority_score": 0.9,
    "final_score": 0.95,
    "reasoning": "Historische Query, Wikipedia ist perfekt",
    "warning_level": "none"
  }
]

BEISPIELE:

Query: "Wer war Albert Einstein?"
Quelle 0: wikipedia.org, Datum: 1955 (vor 25915 Tagen)
→ {"source_index": 0, "relevance_score": 1.0, "freshness_score": 1.0, "authority_score": 1.0, "final_score": 1.0, "reasoning": "Historische Frage. Wikipedia ist Referenz. 1955 ist Todesjahr - perfekt relevant.", "warning_level": "none"}

Query: "Wetter Berlin morgen"
Quelle 0: wetter.com, Datum: 2024-01-15 (vor 730 Tagen)
→ {"source_index": 0, "relevance_score": 0.0, "freshness_score": 0.0, "authority_score": 0.7, "final_score": 0.14, "reasoning": "Wetter braucht SEHR aktuelle Daten. 2 Jahre alt ist irrelevant.", "warning_level": "high"}

Query: "Was ist Machine Learning?"
Quelle 0: towardsdatascience.com, Datum: 2020-05-10 (vor 2074 Tagen)
→ {"source_index": 0, "relevance_score": 0.8, "freshness_score": 0.6, "authority_score": 0.8, "final_score": 0.72, "reasoning": "Grundkonzept ist zeitlos. 2020 ist OK für Basics. Respektierte Quelle.", "warning_level": "none"}

Query: "Python 3.12 Features"
Quelle 0: random-blog.com, Datum: 2018-06-20 (vor 2763 Tagen)
→ {"source_index": 0, "relevance_score": 0.2, "freshness_score": 0.1, "authority_score": 0.3, "final_score": 0.16, "reasoning": "Query fragt nach 3.12 (2023), aber Quelle ist 2018. Python ändert sich schnell.", "warning_level": "high"}

Query: "Geschichte des Zweiten Weltkriegs"
Quelle 0: britannica.com, Datum: 2010-03-15 (vor 5782 Tagen)
→ {"source_index": 0, "relevance_score": 1.0, "freshness_score": 0.9, "authority_score": 1.0, "final_score": 0.98, "reasoning": "Historisch. Britannica ist hochautoritativ. 2010 ist perfekt.", "warning_level": "none"}

Query: "Aktuelle Coronavirus-Zahlen"
Quelle 0: rki.de, Datum: 2023-12-01 (vor 408 Tagen)
→ {"source_index": 0, "relevance_score": 0.3, "freshness_score": 0.1, "authority_score": 1.0, "final_score": 0.28, "reasoning": "Query fragt nach AKTUELLEN Zahlen. 1 Jahr alt ist zu alt.", "warning_level": "high"}

Query: "Wie funktioniert Photosynthese?"
Quelle 0: bio-lex.de, Datum: 2015-09-10 (vor 3777 Tagen)
→ {"source_index": 0, "relevance_score": 1.0, "freshness_score": 0.95, "authority_score": 0.7, "final_score": 0.91, "reasoning": "Zeitloses Konzept. Photosynthese ändert sich nicht. 2015 ist OK.", "warning_level": "none"}

WICHTIG:
- Analysiere JEDEN Kontext individuell
- Sei streng bei "aktuell", "heute", "morgen", "2026"
- Sei großzügig bei "Geschichte", "Konzept", "verstorben"
- Domain-Velocity ist KEY: News ≠ Lexikon
- Authority kann ältere Daten retten (Wikipedia!)

Jetzt bewerte die obigen Quellen!
"""
        return prompt
    
    def _parse_llm_response(self, response: str) -> List[DateScore]:
        """
        Parse LLM-Response robust mit mehreren Fallbacks
        
        Args:
            response: Raw LLM-Response
        
        Returns:
            Liste von DateScores (kann leer sein bei Fehler)
        """
        try:
            # 1. Entferne Markdown-Formatting
            response_clean = response.strip()
            if response_clean.startswith("```json"):
                response_clean = response_clean.split("```json")[1].split("```")[0].strip()
            elif response_clean.startswith("```"):
                response_clean = response_clean.split("```")[1].split("```")[0].strip()
            
            # 2. Parse JSON
            scores = json.loads(response_clean)
            
            # 3. Validierung & Sanitization
            validated_scores = []
            for score in scores:
                # Sicherstelle alle Felder existieren
                validated_score: DateScore = {
                    'source_index': int(score.get('source_index', -1)),
                    'relevance_score': float(score.get('relevance_score', 0.5)),
                    'freshness_score': float(score.get('freshness_score', 0.5)),
                    'authority_score': float(score.get('authority_score', 0.5)),
                    'final_score': float(score.get('final_score', 0.5)),
                    'reasoning': str(score.get('reasoning', 'No reasoning provided')),
                    'warning_level': score.get('warning_level', 'medium')
                }
                
                # Clamp scores to 0-1
                validated_score['relevance_score'] = max(0.0, min(1.0, validated_score['relevance_score']))
                validated_score['freshness_score'] = max(0.0, min(1.0, validated_score['freshness_score']))
                validated_score['authority_score'] = max(0.0, min(1.0, validated_score['authority_score']))
                validated_score['final_score'] = max(0.0, min(1.0, validated_score['final_score']))
                
                validated_scores.append(validated_score)
            
            return validated_scores
        
        except json.JSONDecodeError as e:
            logger.error(f"JSON-Parsing fehlgeschlagen: {e}")
            logger.debug(f"Response war: {response[:500]}...")
            return []
        
        except Exception as e:
            logger.error(f"Unerwarteter Fehler beim Parsing: {e}")
            import traceback
            traceback.print_exc()
            return []
    
    def generate_smart_warning(self, scores: List[DateScore]) -> Optional[str]:
        """
        Erstelle kontext-sensitive Warnung basierend auf Scores
        
        Args:
            scores: Liste von DateScores
        
        Returns:
            Warning-String oder None wenn alles OK
        """
        if not scores:
            return None
        
        # Berechne Durchschnitt
        avg_final_score = sum(s['final_score'] for s in scores) / len(scores)
        
        # Finde kritische Quellen
        high_warnings = [s for s in scores if s['warning_level'] == 'high']
        medium_warnings = [s for s in scores if s['warning_level'] == 'medium']
        
        # FALL 1: Alles OK (Score > 0.7, keine high warnings)
        if avg_final_score >= 0.7 and not high_warnings:
            return None
        
        # FALL 2: Kritische Warnings
        if high_warnings or avg_final_score < 0.4:
            warning_lines = [
                "\n⚠️ **WARNUNG: Möglicherweise veraltete oder irrelevante Daten**\n"
            ]
            
            # Zeige Gründe (max 3)
            for score in high_warnings[:3]:
                warning_lines.append(f"- {score['reasoning']}")
            
            warning_lines.append(
                f"\n💡 **Durchschnittliche Relevanz:** {avg_final_score:.0%}"
            )
            warning_lines.append(
                "**Empfehlung:** Bitte prüfen Sie aktuelle offizielle Quellen."
            )
            
            return "\n".join(warning_lines)
        
        # FALL 3: Moderate Warnings (0.4-0.7)
        if medium_warnings or 0.4 <= avg_final_score < 0.7:
            return f"""
ℹ️ **HINWEIS:** Einige Quellen könnten veraltet sein.

**Durchschnittliche Relevanz:** {avg_final_score:.0%}

💡 Für zeitkritische Informationen empfehlen wir zusätzliche aktuelle Quellen.
"""
        
        return None
    
    def validate_sources_with_llm(
        self, 
        query: str,
        sources: List[Dict[str, Any]]
    ) -> ValidationResult:
        """
        Validiere Web-Search-Quellen mit LLM (Haupt-Methode)
        
        Args:
            query: User-Query
            sources: Liste von Web-Search-Results mit 'content'/'snippet' und 'url'
        
        Returns:
            ValidationResult mit Scores und Warnings
        """
        # 1. Extrahiere Daten aus allen Quellen
        sources_with_dates = []
        for i, source in enumerate(sources):
            content = source.get('content', source.get('snippet', ''))
            dates = self.extract_dates_from_text(content)
            
            if dates:
                sources_with_dates.append({
                    'index': i,
                    'url': source.get('url', ''),
                    'dates': dates,
                    'snippet': content[:300]  # Nur Snippet für Prompt
                })
        
        # Keine Daten gefunden → Keine Validierung nötig
        if not sources_with_dates:
            logger.info("Keine Daten in Quellen gefunden → Keine Date-Validation")
            return {
                'has_warnings': False,
                'average_score': 1.0,
                'scores': [],
                'warning_message': None,
                'debug_info': {'reason': 'no_dates_found'}
            }
        
        # 2. Baue Batch-Prompt
        prompt = self._build_batch_prompt(query, sources_with_dates)
        
        # 3. LLM-Call
        try:
            logger.info(f"🤖 LLM-Date-Validation: {len(sources_with_dates)} Quellen mit Daten")
            
            response = self.model_loader.generate_response(
                messages=[{"role": "user", "content": prompt}],
                max_tokens=1024,  # Genug für alle Scores
                temperature=0.1   # Niedrig für konsistente Bewertung
            )
            
            # 4. Parse Response
            scores = self._parse_llm_response(response)
            
            if not scores:
                logger.warning("LLM lieferte keine validen Scores")
                return {
                    'has_warnings': False,
                    'average_score': 0.5,  # Neutral
                    'scores': [],
                    'warning_message': None,
                    'debug_info': {
                        'reason': 'parsing_failed', 
                        'response_preview': response[:200]
                    }
                }
            
            # 5. Berechne Gesamt-Score
            avg_score = sum(s['final_score'] for s in scores) / len(scores)
            
            # 6. Generiere Warning
            warning_msg = self.generate_smart_warning(scores)
            
            logger.info(f"✅ Date-Validation: Avg={avg_score:.2f}, Warnings={'Ja' if warning_msg else 'Nein'}")
            
            return {
                'has_warnings': warning_msg is not None,
                'average_score': avg_score,
                'scores': scores,
                'warning_message': warning_msg,
                'debug_info': {
                    'sources_validated': len(scores),
                    'sources_with_dates': len(sources_with_dates),
                    'total_sources': len(sources)
                }
            }
        
        except Exception as e:
            logger.error(f"LLM-Date-Validation fehlgeschlagen: {e}")
            import traceback
            traceback.print_exc()
            
            # Fallback: Keine Validierung (fail-safe)
            return {
                'has_warnings': False,
                'average_score': 0.5,
                'scores': [],
                'warning_message': None,
                'debug_info': {'reason': 'exception', 'error': str(e)}
            }


# Convenience-Funktion für Abwärtskompatibilität
def validate_web_search_results(
    results: List[Dict[str, Any]], 
    query: str,
    model_loader
) -> ValidationResult:
    """
    Convenience-Funktion: Validiert Web-Search-Ergebnisse mit LLM
    
    Args:
        results: Liste von Web-Search-Ergebnissen
        query: User-Query
        model_loader: ModelLoader-Instanz
    
    Returns:
        ValidationResult
    """
    validator = LLMDateValidator(model_loader)
    return validator.validate_sources_with_llm(query, results)


# Globale Instanz (Singleton)
_global_validator: Optional[LLMDateValidator] = None

def get_llm_date_validator(model_loader) -> LLMDateValidator:
    """Gibt globale LLMDateValidator-Instanz zurück"""
    global _global_validator
    if _global_validator is None:
        _global_validator = LLMDateValidator(model_loader)
    return _global_validator
