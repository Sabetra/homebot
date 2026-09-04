#!/usr/bin/env python3
"""
Date Validator für zeitkritische Queries
Prüft ob gefundene Daten plausibel sind (nicht in der Vergangenheit)

NEUE VERSION: LLM-basierte intelligente Datumsvalidierung mit:
- Query-Intent-Erkennung (historisch vs. aktuell)
- Domain-Velocity-Bewertung (News vs. Lexikon)
- Source-Authority-Berücksichtigung (Wikipedia vs. Blog)
- Sliding-Scale statt binärer Warnung (0-1 Score)
- Batch-Processing für Performance
"""

from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List, Union
import re
import logging
import json
import time

logger = logging.getLogger(__name__)

# GBNF Grammar für garantiert valides JSON (SOTA: keine Parsing-Fehler mehr)
try:
    from agent.grammars import DATE_VALIDATOR_ARRAY_GRAMMAR
except ImportError:
    DATE_VALIDATOR_ARRAY_GRAMMAR = None
    logger.warning("DATE_VALIDATOR_ARRAY_GRAMMAR nicht verfügbar -- Fallback auf unstrukturierte Generation")


# ============================================================================
# NEUE LLM-BASIERTE DATE VALIDATION
# ============================================================================

class LLMDateValidator:
    """
    Intelligente LLM-basierte Datumsvalidierung
    
    Vorteile gegenüber Regex-Validator:
    - Versteht Query-Context ("Einstein" vs. "Wetter")
    - Berücksichtigt Domain-Velocity (News vs. Geschichte)
    - Bewertet Source-Authority (Wikipedia vs. Blog)
    - Sliding-Scale (0-1) statt binär (gut/schlecht)
    """
    
    def __init__(self, model_loader=None):
        """
        Args:
            model_loader: ModelLoader-Instanz für LLM-Calls
                         Falls None, Fallback auf DateValidator (Regex)
        """
        self.model_loader = model_loader
        self.today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        self.fallback_validator = DateValidator()  # Regex-Fallback
        
        # Performance-Stats
        self.stats = {
            'total_calls': 0,
            'llm_successes': 0,
            'llm_failures': 0,
            'fallback_uses': 0,
            'avg_response_time_ms': 0.0
        }
    
    def validate_sources_batch(
        self, 
        query: str,
        sources: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        Validiert mehrere Quellen in EINEM LLM-Call (Batch-Processing)
        
        Args:
            query: User-Query für Context-Awareness
            sources: Web-Search-Results mit Keys:
                     - 'content' oder 'snippet': Text-Inhalt
                     - 'url': Quellen-URL
                     - 'date' (optional): Veröffentlichungsdatum
        
        Returns:
            {
                'has_warnings': bool,
                'warnings': List[str],
                'scores': List[Dict],  # Pro Quelle: {score, reasoning, ...}
                'warning_message': Optional[str],  # User-freundliche Warnung
                'validations': List[Dict]  # Backward-kompatibel
            }
        """
        start_time = time.time()
        self.stats['total_calls'] += 1
        
        # Fallback wenn kein LLM verfügbar
        if not self.model_loader:
            logger.warning("LLM nicht verfügbar, nutze Regex-Fallback")
            self.stats['fallback_uses'] += 1
            return self._fallback_validation(sources)
        
        # Keine Quellen → Keine Validation
        if not sources:
            return {
                'has_warnings': False,
                'warnings': [],
                'scores': [],
                'warning_message': None,
                'validations': []
            }
        
        try:
            # LLM-basierte Batch-Validation
            scores = self._llm_validate_batch(query, sources)
            self.stats['llm_successes'] += 1
            
            # Generiere Warnungen basierend auf Scores
            result = self._generate_warnings_from_scores(scores, sources)
            
            # Performance-Tracking
            elapsed_ms = (time.time() - start_time) * 1000
            self.stats['avg_response_time_ms'] = (
                (self.stats['avg_response_time_ms'] * (self.stats['total_calls'] - 1) + elapsed_ms)
                / self.stats['total_calls']
            )
            
            logger.info(
                f"✅ LLM-Date-Validation: Query='{query[:50]}', "
                f"Sources={len(sources)}, Warnings={len(result['warnings'])}, "
                f"Time={elapsed_ms:.0f}ms"
            )
            
            return result
        
        except Exception as e:
            logger.warning(f"⚠️ LLM-Validation fehlgeschlagen: {e}, nutze Fallback")
            self.stats['llm_failures'] += 1
            self.stats['fallback_uses'] += 1
            return self._fallback_validation(sources)
    
    def _llm_validate_batch(self, query: str, sources: List[Dict]) -> List[Dict]:
        """
        LLM-Call für Batch-Validation aller Quellen.
        
        SOTA: GBNF Grammar-Enforcement garantiert valides JSON-Array.
        System/User-Split für optimale Magistral-Template-Nutzung.
        
        Returns:
            List[Dict] mit Scores pro Quelle:
            {
                'source_index': int,
                'final_score': float,
                'reasoning': str,
                'warning_level': str  # "none", "low", "medium", "high"
            }
        """
        if not self.model_loader:
            raise ValueError("model_loader ist None, kann nicht validieren")
        
        # System/User Messages aufbauen (statt monolithischem Prompt)
        system_msg, user_msg = self._build_split_messages(query, sources)
        messages = [
            {"role": "system", "content": system_msg},
            {"role": "user", "content": user_msg},
        ]
        
        # SOTA: GBNF Grammar-Enforcement (Primary Path)
        grammar_available = (
            DATE_VALIDATOR_ARRAY_GRAMMAR is not None
            and hasattr(self.model_loader, 'generate_with_grammar')
        )
        
        response = ""
        if grammar_available:
            try:
                response = self.model_loader.generate_with_grammar(
                    messages=messages,
                    grammar_str=DATE_VALIDATOR_ARRAY_GRAMMAR,
                    max_tokens=2048,
                    temperature=0.1,
                )
                logger.debug("✅ Date-Validation via GBNF grammar erfolgreich")
            except Exception as e:
                logger.warning(f"⚠️ Grammar-basierte Date-Validation fehlgeschlagen: {e}")
                response = ""
        
        # Fallback: Unstrukturierte Generation (wenn Grammar nicht verfügbar)
        if not response or not response.strip():
            logger.info("🔄 Date-Validator: Fallback auf generate_response (ohne Grammar)")
            response = self.model_loader.generate_response(
                messages=messages,
                max_tokens=2048,
                temperature=0.1,
            )
        
        # Parse JSON-Response
        scores = self._parse_llm_response(response)
        
        logger.debug(f"📊 LLM-Scores: {scores}")
        
        return scores
    
    def _build_split_messages(self, query: str, sources: List[Dict]) -> tuple:
        """
        Erstellt System- und User-Message getrennt (SOTA Magistral-Template).
        
        Returns:
            (system_prompt: str, user_prompt: str)
        """
        # ── System Prompt: Bewertungskriterien (stabil, wiederverwendbar) ──
        system_prompt = """Du bist ein Experte für Informations-Aktualität und -Relevanz.

BEWERTUNGS-REGELN:
1. Query-Intent dominiert: Historische Frage → alte Daten OK. Aktuelle Frage → nur neue Daten.
2. Domain-Velocity: Wetter/News=Stunden-Tage, Tech=Wochen-Monate, Wissenschaft/Geschichte=Jahre.
3. Source-Authority: .gov/.edu/Wikipedia=HIGH, Etablierte Medien=MEDIUM, Blogs/Foren=LOW.
4. Kein Datum → neutral (final_score ~0.5).

SCORING:
- final_score > 0.7 → warning_level "none"
- final_score 0.4-0.7 → warning_level "low" oder "medium"
- final_score < 0.4 → warning_level "high"

OUTPUT: JSON-Array mit einem Objekt pro Quelle.
Jedes Objekt hat: source_index (int), final_score (0.0-1.0), warning_level ("none"|"low"|"medium"|"high"), reasoning (kurz, max 100 Zeichen)."""

        # ── User Prompt: Query + Quellen-Daten (dynamisch) ──
        sources_data = []
        for i, source in enumerate(sources):
            content = source.get('content') or source.get('snippet', '')
            content_short = content[:300] + "..." if len(content) > 300 else content
            
            date_str = "unbekannt"
            days_old: Any = "unbekannt"
            if 'date' in source and source['date']:
                if isinstance(source['date'], datetime):
                    date_str = source['date'].strftime('%Y-%m-%d')
                    days_old = (self.today - source['date']).days
                elif isinstance(source['date'], str):
                    date_str = source['date']
                    days_old = "unbekannt"
            
            sources_data.append({
                'index': i,
                'url': source.get('url', 'unknown'),
                'date': date_str,
                'days_old': days_old,
                'content': content_short,
            })
        
        sources_json = json.dumps(sources_data, ensure_ascii=False, indent=2)
        
        user_prompt = f"""QUERY: "{query}"
HEUTE: {self.today.strftime('%Y-%m-%d')}

QUELLEN:
{sources_json}

Bewerte jede Quelle."""

        return system_prompt, user_prompt
    
    def _build_batch_prompt(self, query: str, sources: List[Dict]) -> str:
        """Legacy: Erstellt Batch-Prompt (nur noch für Fallback ohne Grammar)."""
        system_msg, user_msg = self._build_split_messages(query, sources)
        return f"{system_msg}\n\n{user_msg}"
    
    def _parse_llm_response(self, response: str) -> List[Dict]:
        """
        Parst LLM-Response.
        
        Mit GBNF Grammar ist das JSON garantiert valide.
        Fallback-Pfad: Entfernt Markdown/Reasoning-Prefix falls nötig.
        
        Returns:
            List[Dict] mit Scores
        
        Raises:
            ValueError: Wenn Parsing komplett fehlschlägt
        """
        if not response or not response.strip():
            raise ValueError("LLM gab leere Response zurück")
        
        response_clean = response.strip()
        
        # Entferne Markdown-Formatting (```json ... ```) -- Fallback-Pfad
        if response_clean.startswith("```json"):
            response_clean = response_clean.split("```json")[1].split("```")[0].strip()
        elif response_clean.startswith("```"):
            response_clean = response_clean.split("```")[1].split("```")[0].strip()
        
        # Entferne Reasoning-Prefix vor JSON (Fallback-Pfad: LLM schreibt Text vor Array)
        bracket_pos = response_clean.find("[")
        if bracket_pos > 0:
            logger.debug(f"Reasoning-Prefix entfernt ({bracket_pos} Zeichen vor JSON-Array)")
            response_clean = response_clean[bracket_pos:]
        
        # Parse JSON
        try:
            scores = json.loads(response_clean)
            
            if not isinstance(scores, list):
                raise ValueError(f"Response ist keine Liste: {type(scores)}")
            
            # Validierung: required keys (Grammar erzwingt dies, Safety für Fallback)
            for score in scores:
                score.setdefault('source_index', 0)
                score.setdefault('final_score', 0.5)
                score.setdefault('warning_level', 'medium')
                score.setdefault('reasoning', '')
            
            return scores
        
        except json.JSONDecodeError as e:
            logger.error(f"JSON-Parsing fehlgeschlagen: {e}")
            logger.debug(f"Response war: {response_clean[:500]}...")
            raise ValueError(f"LLM gab kein valides JSON zurück: {e}")
    
    def _generate_warnings_from_scores(
        self, 
        scores: List[Dict],
        sources: List[Dict]
    ) -> Dict[str, Any]:
        """
        Generiert User-freundliche Warnungen basierend auf LLM-Scores
        
        Returns:
            {
                'has_warnings': bool,
                'warnings': List[str],
                'scores': List[Dict],
                'warning_message': Optional[str],
                'validations': List[Dict]  # Backward-kompatibel
            }
        """
        warnings = []
        high_warnings = []
        medium_warnings = []
        low_warnings = []
        
        # Kategorisiere Warnungen nach Severity
        for score in scores:
            warning_level = score.get('warning_level', 'none')
            final_score = score.get('final_score', 0.5)
            reasoning = score.get('reasoning', '')
            source_index = score.get('source_index', 0)
            
            if source_index < len(sources):
                source_url = sources[source_index].get('url', 'Unknown')
            else:
                source_url = 'Unknown'
            
            if warning_level == 'high':
                warning_text = f"⚠️ Kritisch: {source_url[:50]} (Score: {final_score:.2f}) - {reasoning}"
                warnings.append(warning_text)
                high_warnings.append(score)
            elif warning_level == 'medium':
                warning_text = f"⚠️ Veraltet: {source_url[:50]} (Score: {final_score:.2f}) - {reasoning}"
                warnings.append(warning_text)
                medium_warnings.append(score)
            elif warning_level == 'low':
                low_warnings.append(score)
        
        # Berechne Durchschnittsscore
        avg_score = sum(s.get('final_score', 0.5) for s in scores) / len(scores) if scores else 1.0
        
        # Entscheide ob Warnung nötig
        has_warnings = len(high_warnings) > 0 or len(medium_warnings) > 1 or avg_score < 0.5
        
        # Generiere User-Message
        warning_message = None
        if high_warnings:
            warning_message = f"""
⚠️ **WARNUNG: Möglicherweise veraltete oder irrelevante Daten**

Einige Quellen scheinen für Ihre Anfrage nicht optimal zu sein:

{chr(10).join(f"- {w['reasoning']}" for w in high_warnings[:3])}

💡 **Empfehlung:** Bitte prüfen Sie aktuelle offizielle Quellen für zeitkritische Informationen.
"""
        elif medium_warnings and avg_score < 0.6:
            warning_message = f"""
ℹ️ **HINWEIS:** Einige Quellen könnten veraltet sein.

Durchschnittliche Relevanz: {avg_score:.0%}

💡 Für zeitkritische Informationen empfehlen wir zusätzliche aktuelle Quellen.
"""
        
        # Backward-Kompatibilität: Erstelle 'validations' im alten Format
        validations = []
        for i, score in enumerate(scores):
            validations.append({
                'is_valid': score.get('final_score', 0.5) > 0.7,
                'warnings': [score.get('reasoning', '')] if score.get('warning_level') != 'none' else [],
                'dates_found': [],  # LLM analysiert Content, keine explizite Datum-Extraktion
                'context': sources[i].get('url', '') if i < len(sources) else ''
            })
        
        return {
            'has_warnings': has_warnings,
            'warnings': warnings,
            'scores': scores,
            'warning_message': warning_message,
            'validations': validations  # Backward-kompatibel
        }
    
    def _fallback_validation(self, sources: List[Dict]) -> Dict[str, Any]:
        """Fallback auf Regex-basierte Validation (alte DateValidator)"""
        logger.info("🔄 Nutze Regex-Fallback für Date-Validation")
        
        all_warnings = []
        all_validations = []
        
        for i, source in enumerate(sources):
            content = source.get('content') or source.get('snippet', '')
            url = source.get('url', f'Source {i+1}')
            
            if content:
                validation = self.fallback_validator.validate_future_dates(content, context=url)
                all_validations.append(validation)
                
                if validation.get('warnings'):
                    all_warnings.extend(validation['warnings'])
        
        has_warnings = any(not v.get('is_valid', True) for v in all_validations)
        
        return {
            'has_warnings': has_warnings,
            'warnings': all_warnings,
            'scores': [],  # Regex hat keine Scores
            'warning_message': self.fallback_validator.create_warning_message({
                'is_valid': not has_warnings,
                'past_dates': [d for v in all_validations for d in v.get('past_dates', [])]
            }) if has_warnings else None,
            'validations': all_validations
        }


# ============================================================================
# ALTE REGEX-BASIERTE DATE VALIDATION (Fallback)
# ============================================================================

class DateValidator:
    """Validiert Datumsangaben in Web-Search-Ergebnissen"""
    
    def __init__(self):
        # Wichtig: Nur Datum ohne Uhrzeit für korrekte Vergleiche
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
    
    def extract_dates_from_text(self, text: str) -> List[Dict[str, Any]]:
        """
        Extrahiert Datumsangaben aus Text
        
        Unterstützte Formate:
        - 24. August 2025
        - 24.08.2025
        - 2025-08-24
        - August 24, 2025
        """
        dates = []
        
        # Pattern 1: 24. August 2025
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
        
        # Pattern 2: 24.08.2025
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
        
        # Pattern 3: 2025-08-24
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
        
        # Pattern 4: August 24, 2025
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
    
    def validate_future_dates(self, text: str, context: str = "") -> Dict[str, Any]:
        """
        Prüft ob die im Text gefundenen Daten in der Zukunft liegen
        
        Returns:
            Dict mit Validierungs-Ergebnis:
            - is_valid: bool (alle Daten in Zukunft)
            - warnings: List[str] (Warnungen für vergangene Daten)
            - dates_found: List[Dict] (alle gefundenen Daten)
            - oldest_date: Optional[datetime] (ältestes gefundenes Datum)
            - newest_date: Optional[datetime] (neuestes gefundenes Datum)
        """
        dates = self.extract_dates_from_text(text)
        
        warnings = []
        past_dates = []
        future_dates = []
        
        for date_info in dates:
            date = date_info['date']
            date_text = date_info['text']
            
            days_diff = (date - self.today).days
            
            if days_diff < 0:
                # Datum liegt in der Vergangenheit
                past_dates.append(date_info)
                warning = f"⚠️ Datum in Vergangenheit: '{date_text}' (vor {abs(days_diff)} Tagen)"
                warnings.append(warning)
                logger.warning(warning + f" | Context: {context[:100]}")
            elif days_diff == 0:
                # Heute
                future_dates.append(date_info)
            else:
                # Zukunft
                future_dates.append(date_info)
        
        result = {
            'is_valid': len(past_dates) == 0 and len(dates) > 0,
            'warnings': warnings,
            'dates_found': dates,
            'past_dates': past_dates,
            'future_dates': future_dates,
            'oldest_date': min([d['date'] for d in dates]) if dates else None,
            'newest_date': max([d['date'] for d in dates]) if dates else None,
            'context': context[:200] if context else ""
        }
        
        return result
    
    def create_warning_message(self, validation_result: Dict[str, Any]) -> Optional[str]:
        """
        Erstellt eine Warn-Nachricht für den User basierend auf Validierung
        
        Returns:
            Warn-Text oder None wenn keine Warnung nötig
        """
        if validation_result['is_valid']:
            return None
        
        past_dates = validation_result.get('past_dates', [])
        
        if not past_dates:
            return None
        
        # Erstelle Warn-Nachricht
        warning_lines = [
            "\n⚠️ **WARNUNG: Möglicherweise veraltete Daten**\n"
        ]
        
        for date_info in past_dates[:3]:  # Max 3 Beispiele
            date = date_info['date']
            days_ago = (self.today - date).days
            warning_lines.append(
                f"- Das Datum **{date_info['text']}** liegt **{days_ago} Tage** in der Vergangenheit!"
            )
        
        warning_lines.append(
            "\n💡 **Empfehlung:** Die Quelle könnte veraltet sein. "
            "Bitte prüfen Sie aktuelle offizielle Quellen."
        )
        
        return "\n".join(warning_lines)


def validate_web_search_results(
    results: List[Dict[str, Any]],
    query: Optional[str] = None,
    model_loader=None
) -> Dict[str, Any]:
    """
    Validiert Web-Search-Ergebnisse auf Datum-Plausibilität
    
    NEUE VERSION: Nutzt LLM-basierte intelligente Validation wenn verfügbar
    
    Args:
        results: Liste von Web-Search-Ergebnissen mit 'content' oder 'snippet'
        query: User-Query für Context-Awareness (optional, empfohlen)
        model_loader: ModelLoader für LLM-Validation (optional, Fallback auf Regex)
    
    Returns:
        Dict mit Gesamt-Validierung und Warnungen:
        {
            'has_warnings': bool,
            'warnings': List[str],
            'scores': List[Dict],  # Nur bei LLM
            'warning_message': Optional[str],
            'validations': List[Dict]  # Backward-kompatibel
        }
    """
    # Nutze LLM-Validator wenn model_loader vorhanden
    if model_loader and query:
        llm_validator = LLMDateValidator(model_loader)
        return llm_validator.validate_sources_batch(query, results)
    
    # Fallback auf Regex-basierte Validation
    logger.info("🔄 LLM nicht verfügbar oder keine Query → Nutze Regex-Fallback")
    regex_validator = DateValidator()
    
    all_warnings = []
    all_validations = []
    
    for i, result in enumerate(results):
        content = result.get('content', result.get('snippet', ''))
        url = result.get('url', f'Result {i+1}')
        
        if content:
            validation = regex_validator.validate_future_dates(content, context=url)
            all_validations.append(validation)
            
            if validation['warnings']:
                all_warnings.extend(validation['warnings'])
    
    # Gesamt-Bewertung
    has_past_dates = any(not v['is_valid'] for v in all_validations)
    
    return {
        'has_warnings': has_past_dates,
        'warnings': all_warnings,
        'scores': [],  # Regex hat keine Scores
        'validations': all_validations,
        'warning_message': regex_validator.create_warning_message({
            'is_valid': not has_past_dates,
            'past_dates': [d for v in all_validations for d in v.get('past_dates', [])]
        }) if has_past_dates else None
    }


# Globale Instanz für einfache Nutzung
_global_validator: Optional[Union[LLMDateValidator, DateValidator]] = None

def get_date_validator(model_loader: Any = None) -> Union[LLMDateValidator, DateValidator]:
    """
    Gibt globale DateValidator-Instanz zurück (Singleton)
    
    Args:
        model_loader: ModelLoader für LLM-Validation
    
    Returns:
        LLMDateValidator wenn model_loader vorhanden, sonst DateValidator
    """
    global _global_validator
    if _global_validator is None:
        if model_loader:
            _global_validator = LLMDateValidator(model_loader)
        else:
            _global_validator = DateValidator()  # Fallback
    return _global_validator
