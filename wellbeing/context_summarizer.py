"""
LLM-BASIERTE Kontext-Zusammenfassung für psychologische Sessions
=================================================================
Vollständig LLM-gestützte Zusammenfassung mit:
- Semantisches Verständnis therapeutisch relevanter Informationen
- Intelligente Komprimierung für Kontext-Management
- Stimmungsanalyse und Trend-Erkennung
- KEINE regelbasierten Fallbacks - nur LLM!
"""

import logging
from typing import Dict, List, Optional, Any
from datetime import datetime
import json

from .wellbeing_db import WellbeingDatabase

# Logger für Kontext-Zusammenfassung
logger = logging.getLogger(__name__)

class ContextSummarizer:
    """
    100% LLM-basierte Kontext-Zusammenfassung für psychologische Sessions
    
    Features:
    - LLM versteht semantisch therapeutische Relevanz
    - Keine regelbasierten Stopword-Filter mehr
    - Intelligente Komprimierung mit echtem Verständnis
    - KEIN Fallback - ModelLoader ist REQUIRED
    """
    
    def __init__(self, db: Optional[WellbeingDatabase] = None,
                 model_loader: Any = None, max_context_length: int = 8000) -> None:
        """
        Initialisiert Kontext-Zusammenfasser
        
        Args:
            db: Datenbank-Instanz
            model_loader: ModelLoader für LLM-basierte Zusammenfassung (REQUIRED!)
            max_context_length: Maximale Kontext-Länge in Tokens
        """
        self.db = db or WellbeingDatabase()
        
        # ✅ KRITISCH: ModelLoader ist REQUIRED, kein Fallback!
        if not model_loader:
            raise ValueError("❌ ContextSummarizer benötigt einen ModelLoader! Kein Fallback verfügbar.")
        
        self.model_loader = model_loader
        self.max_context_length = max_context_length
        
        # Template für verschiedene Zusammenfassungstypen
        self._init_summary_templates()
        
        logger.info("✅ ContextSummarizer initialisiert (100% LLM-basiert, kein Fallback)")
    
    def _init_summary_templates(self) -> None:
        """Initialisiert Templates für Zusammenfassungen"""
        self.summary_templates = {
            'periodic': {
                'system_prompt': """Du bist ein therapeutischer Dokumentations-Assistent. Erstelle eine ZWISCHEN-ZUSAMMENFASSUNG dieser laufenden Session.

WICHTIG: Dies ist KEINE Antwort an den Patienten! Schreibe in der 3. Person über den Klienten/Benutzer.

Erstelle eine prägnante Zusammenfassung mit:

1. **Hauptthemen**: Zentrale Gesprächsthemen und Problembereiche (Bullet-Points)
2. **Emotionale Lage**: Aktuelle Stimmung und emotionale Muster des Klienten
3. **Fortschritte**: Erkenntnisse oder Verbesserungen die der Klient zeigt
4. **Aktuelle Herausforderungen**: Womit kämpft der Klient gerade?
5. **Besprochene Ansätze**: Welche Techniken oder Strategien wurden erwähnt?

Schreibe in der 3. Person (z.B. "Der Klient äußerte...", "Es wurde besprochen...")
Länge: 200-400 Wörter.""",
                'max_length': 1500
            },
            
            'session_end': {
                'system_prompt': """Du bist ein therapeutischer Dokumentations-Assistent. Deine Aufgabe ist es, eine ZUSAMMENFASSUNG dieser Session für die Patientenakte zu erstellen.

⚠️ WICHTIG - LIES DAS SORGFÄLTIG:
- Dies ist KEINE Antwort an den Patienten!
- Antworte NICHT mit "Ich verstehe..." oder "Möchten Sie..."!
- Schreibe in der 3. Person über den Klienten/Benutzer!
- Erstelle eine DOKUMENTATION, keine Chat-Nachricht!

Struktur der Zusammenfassung:
1. **Session-Überblick**: Ungefähre Dauer, Anzahl Nachrichten, allgemeiner Verlauf
2. **Hauptthemen**: Die zentralen besprochenen Themen (2-4 Bullet-Points)
3. **Emotionale Entwicklung**: Wie war die Stimmung zu Beginn vs. Ende?
4. **Wichtige Erkenntnisse**: Was hat der Klient erkannt oder gelernt?
5. **Besprochene Strategien**: Welche Techniken/Ansätze wurden besprochen?
6. **Für nächste Session**: Worauf sollte in Zukunft geachtet werden?

Beispiel-Anfang (RICHTIG):
"**Session-Überblick**: Die Session dauerte ca. 20 Minuten mit 12 Nachrichten. Der Klient berichtete über..."

FALSCH wäre:
"Ich verstehe, dass Sie..." oder "Möchten Sie darüber sprechen..."

Schreibe professionell, strukturiert und in der 3. Person. Länge: 300-600 Wörter.""",
                'max_length': 2000
            },
            
            'mood_analysis': {
                'system_prompt': """Analysiere die emotionalen Muster in dieser Unterhaltung.

Erstelle eine Stimmungsanalyse mit:
- **Emotionale Bandbreite**: Erkannte Gefühle und deren Intensität
- **Stimmungsverlauf**: Wie sich die Emotionen entwickelt haben
- **Trigger-Punkte**: Themen die starke emotionale Reaktionen auslösen
- **Bewältigungsstrategien**: Wie der Klient mit Emotionen umgeht
- **Resilienz-Faktoren**: Stärken und Ressourcen
- **Risiko-Einschätzung**: Indikatoren für kritische Zustände

Fokus auf therapeutisch verwertbare Erkenntnisse.""",
                'max_length': 1200
            }
        }
    
    def create_periodic_summary(self, session_id: str, 
                              interaction_limit: Optional[int] = None) -> Optional[str]:
        """
        Erstellt periodische Zusammenfassung einer Session
        
        Args:
            session_id: Session-ID
            interaction_limit: Anzahl der zu berücksichtigenden Interaktionen
            
        Returns:
            Zusammenfassung oder None bei Fehler
        """
        try:
            # FIXED: Use get_session_interactions instead of get_session_history
            interactions = self.db.get_session_interactions(session_id)
            
            # Apply interaction limit if specified
            if interaction_limit and len(interactions) > interaction_limit:
                interactions = interactions[-interaction_limit:]
            
            if not interactions or len(interactions) < 3:
                logger.info(f"Zu wenige Interaktionen für Zusammenfassung: {len(interactions)}")
                return None
            
            # Generiere Zusammenfassung
            summary = self._generate_summary(interactions, 'periodic')
            
            if summary:
                # Speichere in Datenbank
                summary_id = self.db.save_context_summary(
                    session_id=session_id,
                    summary_type='periodic',
                    content=summary,
                    interaction_count=len(interactions)
                )
                
                logger.info(f"✓ Periodische Zusammenfassung erstellt: {summary_id}")
                return summary
            
            return None
            
        except Exception as e:
            logger.error(f"❌ Periodische Zusammenfassung fehlgeschlagen: {e}")
            return None
    
    def create_session_summary(self, session_id: str) -> Optional[str]:
        """
        Erstellt Abschluss-Zusammenfassung einer kompletten Session
        
        Args:
            session_id: Session-ID
            
        Returns:
            Abschluss-Zusammenfassung oder None
        """
        try:
            # FIXED: Use get_session_interactions instead of get_session_history
            interactions = self.db.get_session_interactions(session_id)
            
            if not interactions:
                logger.warning(f"Keine Interaktionen für Session: {session_id}")
                return None
            
            # Generiere Session-Zusammenfassung
            summary = self._generate_summary(interactions, 'session_end')
            
            if summary:
                # Speichere als Session-Ende-Zusammenfassung
                summary_id = self.db.save_context_summary(
                    session_id=session_id,
                    summary_type='session_end',
                    content=summary,
                    interaction_count=len(interactions)
                )
                
                logger.info(f"✓ Session-Zusammenfassung erstellt: {summary_id}")
                return summary
            
            return None
            
        except Exception as e:
            logger.error(f"❌ Session-Zusammenfassung fehlgeschlagen: {e}")
            return None
    
    def create_mood_analysis(self, session_id: str) -> Optional[Dict[str, Any]]:
        """
        Erstellt detaillierte Stimmungsanalyse einer Session
        
        Args:
            session_id: Session-ID
            
        Returns:
            Stimmungsanalyse als Dictionary oder None
        """
        try:
            # FIXED: Use get_session_interactions instead of get_session_history
            interactions = self.db.get_session_interactions(session_id)
            
            if not interactions:
                return None
            
            # Generiere Stimmungsanalyse
            mood_summary = self._generate_summary(interactions, 'mood_analysis')
            
            if not mood_summary:
                return None
            
            # Extrahiere strukturierte Daten
            mood_data = self._parse_mood_analysis(mood_summary, interactions)
            
            # Speichere Analyse
            summary_id = self.db.save_context_summary(
                session_id=session_id,
                summary_type='mood_analysis',
                content=json.dumps(mood_data, ensure_ascii=False, indent=2),
                interaction_count=len(interactions)
            )
            
            logger.info(f"✓ Stimmungsanalyse erstellt: {summary_id}")
            return mood_data
            
        except Exception as e:
            logger.error(f"❌ Stimmungsanalyse fehlgeschlagen: {e}")
            return None
    
    def _generate_summary(self, interactions: List[Dict[str, Any]], 
                         summary_type: str) -> Optional[str]:
        """
        Generiert 100% LLM-basierte Zusammenfassung (KEIN Fallback!)
        
        Args:
            interactions: Liste von Interaktionen
            summary_type: Typ der Zusammenfassung
            
        Returns:
            LLM-generierte Zusammenfassung
            
        Raises:
            ValueError: Wenn keine Interactions vorhanden
            RuntimeError: Wenn LLM-Generierung fehlschlägt
        """
        if not interactions:
            raise ValueError("Keine Interaktionen für Zusammenfassung vorhanden!")
        
        try:
            # Bereite Kontext vor
            context = self._prepare_context(interactions)
            
            if not context:
                raise ValueError("Kontext-Vorbereitung fehlgeschlagen!")
            
            # Hole Template
            template = self.summary_templates.get(summary_type, self.summary_templates['periodic'])
            
            # Messages-Format für korrekte Nutzung des Mistral Chat-Templates
            # (kein manuelles [INST]/[/INST] mehr nötig — create_chat_completion
            # wendet das im GGUF eingebettete Template automatisch an)
            summary_messages = [
                {"role": "system", "content": template['system_prompt']},
                {"role": "user", "content": f"""===== TRANSKRIPT DER ABGESCHLOSSENEN SESSION (ZUR DOKUMENTATION) =====
{context}
===== ENDE DES TRANSKRIPTS =====

Erstelle nun die Zusammenfassung für die Patientenakte (in der 3. Person, KEINE Chat-Antwort):"""}
            ]
            
            logger.info(f"🧠 Generiere LLM-Summary (Typ: {summary_type}, Context: {len(context)} chars)")
            
            # ✅ Generiere mit LLM (REQUIRED, kein Fallback!)
            # Nutzt messages= für korrektes Chat-Template-Format
            response = self.model_loader.generate_response(
                messages=summary_messages,
                max_tokens=template['max_length'],
                temperature=0.3  # Niedrige Temperatur für konsistente Zusammenfassungen
            )
            
            # Ensure string return
            response_str = str(response) if response is not None else ""
            
            if not response_str or len(response_str.strip()) < 50:
                raise RuntimeError(f"LLM-Response zu kurz oder leer: {len(response_str)} chars")
            
            logger.info(f"✅ LLM-Zusammenfassung generiert ({len(response_str)} Zeichen)")
            return response_str.strip()
            
        except Exception as e:
            logger.error(f"❌ LLM-Zusammenfassung KRITISCHER FEHLER: {e}")
            # ❌ KEIN FALLBACK! Exception propagieren
            raise RuntimeError(f"LLM-Zusammenfassung fehlgeschlagen: {e}") from e
    
    def _prepare_context(self, interactions: List[Dict[str, Any]]) -> str:
        """
        Bereitet Interaktionen für KI-Prompt vor
        
        Args:
            interactions: Liste von Interaktionen
            
        Returns:
            Formatierter Kontext-String
        """
        try:
            context_lines = []
            
            for interaction in interactions:
                timestamp = interaction.get('timestamp', '')
                role = interaction.get('role', 'unknown')
                content = interaction.get('content', '')
                
                # Formatiere Zeitstempel
                time_str = self._format_timestamp(timestamp)
                
                # Rolle übersetzen
                role_german = 'Benutzer' if role == 'user' else 'Assistent'
                
                # Füge Interaktion hinzu
                context_lines.append(f"[{time_str}] {role_german}: {content}")
            
            context = '\n'.join(context_lines)
            
            # Kürze wenn zu lang
            if len(context) > self.max_context_length:
                context = self._truncate_context(context)
            
            return context
            
        except Exception as e:
            logger.error(f"❌ Kontext-Vorbereitung fehlgeschlagen: {e}")
            return ""
    
    def _format_timestamp(self, timestamp: str) -> str:
        """Formatiert Zeitstempel für Lesbarkeit"""
        try:
            if not timestamp:
                return "--:--"
            
            # Parse ISO-Format
            dt = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
            return dt.strftime('%H:%M')
            
        except Exception:
            return "--:--"
    
    def _truncate_context(self, context: str) -> str:
        """
        Kürzt Kontext auf maximale Länge
        
        Args:
            context: Vollständiger Kontext
            
        Returns:
            Gekürzter Kontext
        """
        try:
            if len(context) <= self.max_context_length:
                return context
            
            # Teile in Zeilen
            lines = context.split('\n')
            
            # Beginne vom Ende und kürze
            truncated_lines: List[str] = []
            current_length = 0
            
            for line in reversed(lines):
                if current_length + len(line) + 1 > self.max_context_length:
                    break
                truncated_lines.insert(0, line)
                current_length += len(line) + 1
            
            truncated = '\n'.join(truncated_lines)
            
            # Füge Hinweis hinzu
            if len(truncated_lines) < len(lines):
                truncated = f"[...{len(lines) - len(truncated_lines)} ältere Nachrichten...]\n{truncated}"
            
            return truncated
            
        except Exception as e:
            logger.error(f"❌ Kontext-Kürzung fehlgeschlagen: {e}")
            return context[:self.max_context_length]
    
    
    def _parse_mood_analysis(self, mood_summary: str, 
                           interactions: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Parst LLM-generierte Stimmungsanalyse in strukturierte Daten
        
        Args:
            mood_summary: LLM-generierte Zusammenfassung
            interactions: Original-Interaktionen
            
        Returns:
            Strukturierte Stimmungsdaten
        """
        try:
            # Basis-Daten aus Interaktionen
            mood_data = {
                'summary': mood_summary,
                'interaction_count': len(interactions),
                'time_span': self._calculate_time_span(interactions),
                'conversation_intensity': self._calculate_intensity(interactions),
                'generated_at': datetime.now().isoformat()
            }
            
            return mood_data
            
        except Exception as e:
            logger.error(f"❌ Mood-Analysis-Parsing fehlgeschlagen: {e}")
            return {'summary': mood_summary, 'error': str(e)}
    
    def _calculate_time_span(self, interactions: List[Dict[str, Any]]) -> str:
        """Berechnet Zeitspanne der Session"""
        try:
            if len(interactions) < 2:
                return "Einzelnachricht"
            
            first = datetime.fromisoformat(interactions[0]['timestamp'].replace('Z', '+00:00'))
            last = datetime.fromisoformat(interactions[-1]['timestamp'].replace('Z', '+00:00'))
            
            diff = last - first
            hours = int(diff.total_seconds() // 3600)
            minutes = int((diff.total_seconds() % 3600) // 60)
            
            if hours > 0:
                return f"{hours}h {minutes}m"
            else:
                return f"{minutes}m"
                
        except Exception:
            return "Unbekannt"
    
    def _calculate_intensity(self, interactions: List[Dict[str, Any]]) -> str:
        """Berechnet Gesprächsintensität"""
        try:
            if not interactions:
                return "niedrig"
            
            avg_length = sum(len(i.get('content', '')) for i in interactions) / len(interactions)
            
            if avg_length > 200:
                return "hoch"
            elif avg_length > 100:
                return "mittel"
            else:
                return "niedrig"
                
        except Exception:
            return "unbekannt"
    

# Alias für Interface-Kompatibilität
PsychologicalContextSummarizer = ContextSummarizer
