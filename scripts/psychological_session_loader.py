#!/usr/bin/env python3
"""
Session-Management für psychologische Unterstützung
===================================================
Ermöglicht gezieltes Laden und Verwalten von psychologischen Sessions,
damit Benutzer nicht alles neu erzählen müssen.
"""

import logging
import os
import sys
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any

# Für den Import der psychologischen Module
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

logger = logging.getLogger(__name__)

class PsychologicalSessionLoader:
    """
    Session-Loader für psychologische Unterstützung
    
    Funktionen:
    - Auflisten aller Sessions eines Benutzers
    - Gezieltes Laden einer bestimmten Session
    - Session-Zusammenfassungen anzeigen
    - Intelligente Session-Empfehlungen
    """
    
    def __init__(self):
        self.session_manager = None
        self.db = None
        self._topic_extractor = None
        self._initialize_components()
    
    def _initialize_components(self):
        """Initialisiert Session-Manager und Datenbank"""
        try:
            from wellbeing.session_manager import WellbeingSessionManager
            from wellbeing.wellbeing_db import WellbeingDatabase
            from wellbeing.topic_extractor import WellbeingTopicExtractor
            
            self.db = WellbeingDatabase()
            self.session_manager = WellbeingSessionManager(db=self.db)
            
            # Initialisiere Topic-Extractor (Chat-Funktion wird später gesetzt)
            self._topic_extractor = WellbeingTopicExtractor()
            
            logger.info("✓ Session-Loader initialisiert")
            return True
            
        except Exception as e:
            logger.error(f"❌ Session-Loader Initialisierung fehlgeschlagen: {e}")
            return False
    
    def list_user_sessions(self, user_id: str = "default_user", include_closed: bool = True) -> List[Dict[str, Any]]:
        """
        Listet alle Sessions eines Benutzers auf
        
        Args:
            user_id: Benutzer-ID (default: "default_user")
            include_closed: Auch geschlossene Sessions anzeigen
            
        Returns:
            Liste von Session-Informationen mit Metadaten
        """
        if not self.db:
            print("❌ Session-Loader nicht initialisiert")
            return []
        
        try:
            # Lade Sessions aus der Datenbank
            sessions = self.db.get_user_sessions(user_id)
            
            if not include_closed:
                sessions = [s for s in sessions if s['status'] == 'active']
            
            # Ergänze Sessions um zusätzliche Metadaten
            enriched_sessions = []
            
            for session in sessions:
                # Lade Session-Historie für Metadaten
                history = self.db.get_session_history(session['id'], limit=None)
                
                # Berechne Session-Statistiken
                user_messages = [h for h in history if h['role'] == 'user']
                assistant_messages = [h for h in history if h['role'] == 'assistant']
                
                # Extrahiere Hauptthemen
                main_topics = self._extract_session_topics(history)
                
                # Erstelle Session-Vorschau
                preview = self._create_session_preview(history)
                
                # Berechne Session-Dauer
                duration = self._calculate_session_duration(session, history)
                
                enriched_session = {
                    'id': session['id'],
                    'created_at': session['created_at'],
                    'updated_at': session['updated_at'],
                    'status': session['status'],
                    'summary': session['summary'],
                    'privacy_level': session['privacy_level'],
                    # Neue Metadaten
                    'interaction_count': len(history),
                    'user_message_count': len(user_messages),
                    'assistant_message_count': len(assistant_messages),
                    'main_topics': main_topics,
                    'preview': preview,
                    'duration_minutes': duration,
                    'last_interaction_days_ago': self._days_since_last_interaction(session['updated_at'])
                }
                
                enriched_sessions.append(enriched_session)
            
            # Sortiere nach Aktualisierungsdatum (neueste zuerst)
            enriched_sessions.sort(key=lambda x: x['updated_at'], reverse=True)
            
            logger.info(f"📋 {len(enriched_sessions)} Sessions gefunden für User: {user_id}")
            return enriched_sessions
            
        except Exception as e:
            logger.error(f"❌ Session-Auflistung fehlgeschlagen: {e}")
            return []
    
    def _extract_session_topics(self, history: List[Dict[str, Any]]) -> List[str]:
        """Extrahiert Hauptthemen aus der Session-Historie mittels zentraler Topic-Extraktion"""
        try:
            # Verwende zentrale Topic-Extraktion
            if self._topic_extractor:
                topics = self._topic_extractor.extract_topics_from_session_history(history)
                return topics
            else:
                logger.warning("⚠️ Topic-Extractor nicht verfügbar, verwende Fallback")
                return self._fallback_extract_session_topics(history)
                
        except Exception as e:
            logger.error(f"❌ Session-Topic-Extraktion fehlgeschlagen: {e}")
            return self._fallback_extract_session_topics(history)
    
    def _fallback_extract_session_topics(self, history: List[Dict[str, Any]]) -> List[str]:
        """Fallback: Einfache Keyword-basierte Topic-Extraktion"""
        topics = set()
        
        # Analysiere Benutzer-Nachrichten nach Schlüsselwörtern
        for interaction in history:
            if interaction['role'] == 'user':
                content = interaction['content'].lower()
                
                # Nur wichtigste Kategorien
                if any(word in content for word in ['angst', 'ängstlich', 'furcht', 'panik']):
                    topics.add('Angstbewältigung')
                if any(word in content for word in ['traurig', 'deprimiert', 'niedergeschlagen']):
                    topics.add('Stimmungstiefs')
                if any(word in content for word in ['stress', 'gestresst', 'überforderung']):
                    topics.add('Stressmanagement')
                if any(word in content for word in ['beziehung', 'partner', 'ehe', 'familie']):
                    topics.add('Beziehungsthemen')
                if any(word in content for word in ['trauma', 'missbrauch', 'gewalt']):
                    topics.add('Trauma')
        
        return list(topics) if topics else ['Allgemeine Unterstützung']
    
    def _create_session_preview(self, history: List[Dict[str, Any]], max_length: int = 150) -> str:
        """Erstellt eine Vorschau der Session"""
        if not history:
            return "Keine Nachrichten vorhanden"
        
        # Finde erste Benutzer-Nachricht
        first_user_message = next((h for h in history if h['role'] == 'user'), None)
        
        if first_user_message:
            content = first_user_message['content']
            if len(content) > max_length:
                return content[:max_length] + "..."
            return content
        
        return "Session ohne Benutzer-Nachrichten"
    
    def _calculate_session_duration(self, session: Dict[str, Any], history: List[Dict[str, Any]]) -> int:
        """Berechnet Session-Dauer in Minuten"""
        try:
            if not history:
                return 0
            
            start_time = datetime.fromisoformat(session['created_at'].replace('Z', '+00:00'))
            
            # Verwende letzte Interaktion oder Session-Update
            if session['updated_at']:
                end_time = datetime.fromisoformat(session['updated_at'].replace('Z', '+00:00'))
            else:
                last_interaction = max(history, key=lambda x: x['timestamp'])
                end_time = datetime.fromisoformat(last_interaction['timestamp'].replace('Z', '+00:00'))
            
            duration = end_time - start_time
            return int(duration.total_seconds() / 60)
            
        except Exception:
            return 0
    
    def _days_since_last_interaction(self, updated_at: str) -> int:
        """Berechnet Tage seit letzter Interaktion"""
        try:
            last_time = datetime.fromisoformat(updated_at.replace('Z', '+00:00'))
            now = datetime.now(last_time.tzinfo)
            delta = now - last_time
            return delta.days
        except Exception:
            return 0
    
    def get_session_details(self, session_id: str) -> Optional[Dict[str, Any]]:
        """
        Holt detaillierte Informationen zu einer Session
        
        Args:
            session_id: Session-ID
            
        Returns:
            Detaillierte Session-Informationen oder None
        """
        if not self.db:
            return None
        
        try:
            # Lade vollständige Session-Historie
            history = self.db.get_session_history(session_id)
            
            if not history:
                logger.warning(f"⚠️ Keine Historie für Session {session_id} gefunden")
                return None
            
            # Organisiere Historie
            user_messages = [h for h in history if h['role'] == 'user']
            assistant_messages = [h for h in history if h['role'] == 'assistant']
            
            # Erstelle detaillierte Analyse
            topics = self._extract_session_topics(history)
            conversation_flow = self._analyze_conversation_flow(history)
            mood_progression = self._analyze_mood_progression(history)
            
            session_details = {
                'session_id': session_id,
                'total_interactions': len(history),
                'user_messages': len(user_messages),
                'assistant_messages': len(assistant_messages),
                'main_topics': topics,
                'conversation_flow': conversation_flow,
                'mood_progression': mood_progression,
                'full_history': history,
                'session_summary': self._create_detailed_summary(history, topics)
            }
            
            return session_details
            
        except Exception as e:
            logger.error(f"❌ Session-Details laden fehlgeschlagen: {e}")
            return None
    
    def _analyze_conversation_flow(self, history: List[Dict[str, Any]]) -> List[str]:
        """Analysiert den Gesprächsverlauf"""
        flow = []
        
        # Analysiere in 3er-Gruppen (Anfang, Mitte, Ende)
        total = len(history)
        
        if total >= 6:
            # Anfang
            start_user_msgs = [h for h in history[:total//3] if h['role'] == 'user']
            if start_user_msgs:
                start_topics = self._extract_session_topics(start_user_msgs)
                flow.append(f"Anfang: {', '.join(start_topics)}")
            
            # Mitte
            mid_user_msgs = [h for h in history[total//3:2*total//3] if h['role'] == 'user']
            if mid_user_msgs:
                mid_topics = self._extract_session_topics(mid_user_msgs)
                flow.append(f"Verlauf: {', '.join(mid_topics)}")
            
            # Ende
            end_user_msgs = [h for h in history[2*total//3:] if h['role'] == 'user']
            if end_user_msgs:
                end_topics = self._extract_session_topics(end_user_msgs)
                flow.append(f"Aktuell: {', '.join(end_topics)}")
        
        return flow if flow else ["Kurze Session"]
    
    def _analyze_mood_progression(self, history: List[Dict[str, Any]]) -> List[str]:
        """Analysiert die Stimmungsentwicklung"""
        moods = []
        
        # Einfache Stimmungsanalyse basierend auf Indikatoren
        for interaction in history:
            if interaction['role'] == 'user':
                content = interaction['content'].lower()
                
                if any(word in content for word in ['besser', 'gut', 'positiv', 'hoffnung']):
                    moods.append('positiv')
                elif any(word in content for word in ['schlecht', 'traurig', 'verzweifelt', 'hoffnungslos']):
                    moods.append('negativ')
                elif any(word in content for word in ['unsicher', 'verwirrt', 'ratlos']):
                    moods.append('unsicher')
                else:
                    moods.append('neutral')
        
        # Vereinfache zu Trend
        if not moods:
            return ["Keine Stimmungsdaten"]
        
        recent_moods = moods[-3:]  # Letzte 3 Stimmungen
        
        if 'positiv' in recent_moods:
            return ["Aufwärtstrend erkennbar"]
        elif recent_moods.count('negativ') >= 2:
            return ["Bedrückte Stimmung"]
        else:
            return ["Stabile Stimmung"]
    
    def _create_detailed_summary(self, history: List[Dict[str, Any]], topics: List[str]) -> str:
        """Erstellt eine detaillierte Session-Zusammenfassung"""
        if not history:
            return "Keine Session-Inhalte vorhanden"
        
        user_messages = [h for h in history if h['role'] == 'user']
        
        # Erstelle Zusammenfassung
        summary_parts = []
        
        summary_parts.append(f"Session mit {len(history)} Nachrichten")
        summary_parts.append(f"Hauptthemen: {', '.join(topics)}")
        
        if user_messages:
            first_msg = user_messages[0]['content'][:100] + "..." if len(user_messages[0]['content']) > 100 else user_messages[0]['content']
            summary_parts.append(f"Begann mit: \"{first_msg}\"")
            
            if len(user_messages) > 1:
                last_msg = user_messages[-1]['content'][:100] + "..." if len(user_messages[-1]['content']) > 100 else user_messages[-1]['content']
                summary_parts.append(f"Zuletzt: \"{last_msg}\"")
        
        return " | ".join(summary_parts)
    
    def load_session_context(self, session_id: str) -> bool:
        """
        Lädt eine bestimmte Session in den aktiven Kontext
        
        Args:
            session_id: Session-ID
            
        Returns:
            True wenn erfolgreich geladen
        """
        if not self.session_manager:
            print("❌ Session-Manager nicht verfügbar")
            return False
        
        try:
            # Versuche Session zu laden
            success = self.session_manager.load_specific_session(session_id)
            
            if success:
                logger.info(f"✅ Session erfolgreich geladen: {session_id}")
                return True
            else:
                logger.warning(f"⚠️ Session konnte nicht geladen werden: {session_id}")
                return False
                
        except Exception as e:
            logger.error(f"❌ Session-Laden fehlgeschlagen: {e}")
            return False
    
    def recommend_sessions(self, user_id: str = "default_user", max_recommendations: int = 3) -> List[Dict[str, Any]]:
        """
        Empfiehlt relevante Sessions zum Fortsetzen
        
        Args:
            user_id: Benutzer-ID
            max_recommendations: Maximale Anzahl Empfehlungen
            
        Returns:
            Liste empfohlener Sessions mit Begründung
        """
        sessions = self.list_user_sessions(user_id, include_closed=False)
        
        if not sessions:
            return []
        
        recommendations = []
        
        for session in sessions[:max_recommendations]:
            # Bewerte Session für Empfehlung
            score = 0
            reasons = []
            
            # Aktuelle Sessions bevorzugen
            if session['status'] == 'active':
                score += 10
                reasons.append("Aktive Session")
            
            # Kürzlich aktive Sessions
            if session['last_interaction_days_ago'] <= 7:
                score += 8
                reasons.append(f"Vor {session['last_interaction_days_ago']} Tagen aktiv")
            elif session['last_interaction_days_ago'] <= 30:
                score += 5
                reasons.append("Kürzlich aktiv")
            
            # Sessions mit vielen Interaktionen
            if session['interaction_count'] >= 10:
                score += 5
                reasons.append("Ausführliche Session")
            elif session['interaction_count'] >= 5:
                score += 3
                reasons.append("Etablierte Session")
            
            # Sessions mit wichtigen Themen - semantische LLM-basierte Bewertung
            session_topics = session.get('main_topics', [])
            if session_topics and self._topic_extractor:
                try:
                    # Nutze zentrale Topic-Wichtigkeitsbewertung
                    topic_scores = self._topic_extractor.evaluate_topic_importance(session_topics)
                    
                    if topic_scores:
                        # Berechne Gesamt-Wichtigkeitsscore
                        max_score = max(topic_scores.values()) if topic_scores else 0
                        avg_score = sum(topic_scores.values()) / len(topic_scores) if topic_scores else 0
                        
                        # Bewerte basierend auf höchstem und durchschnittlichem Score
                        if max_score >= 8:  # Kritische Themen
                            score += 8
                            reasons.append(f"Kritische Themen (Score: {max_score})")
                        elif max_score >= 6:  # Wichtige Themen
                            score += 6
                            reasons.append(f"Wichtige Themen (Score: {max_score})")
                        elif avg_score >= 5:  # Durchschnittlich wichtig
                            score += 4
                            reasons.append(f"Relevante Themen (Ø {avg_score:.1f})")
                        else:
                            score += 2
                            reasons.append("Allgemeine Themen")
                        
                        logger.info(f"📊 Topic-Bewertung für Session: {topic_scores}")
                    else:
                        # Fallback auf einfache Kategorien-Bewertung
                        score += self._fallback_topic_scoring(session_topics)
                        reasons.append(f"Themen: {len(session_topics)} erkannt")
                        
                except Exception as e:
                    logger.warning(f"⚠️ Semantische Topic-Bewertung fehlgeschlagen: {e}")
                    # Fallback
                    score += self._fallback_topic_scoring(session_topics)
                    reasons.append(f"Themen: {len(session_topics)} erkannt")
            elif session_topics:
                # Fallback wenn kein Topic-Extractor verfügbar
                score += self._fallback_topic_scoring(session_topics)
                reasons.append(f"Themen: {len(session_topics)} erkannt")
            
            recommendation = {
                'session': session,
                'score': score,
                'reasons': reasons,
                'recommendation_text': self._create_recommendation_text(session, reasons)
            }
            
            recommendations.append(recommendation)
        
        # Sortiere nach Score
        recommendations.sort(key=lambda x: x['score'], reverse=True)
        
        return recommendations[:max_recommendations]
    
    def _create_recommendation_text(self, session: Dict[str, Any], reasons: List[str]) -> str:
        """Erstellt einen empfehlenden Text für eine Session"""
        topics_text = ", ".join(session['main_topics'])
        reasons_text = ", ".join(reasons)
        
        return f"Session über {topics_text} ({reasons_text}) - {session['preview']}"

    def _fallback_topic_scoring(self, topics: List[str]) -> int:
        """Fallback: Einfache Topic-Bewertung basierend auf Keywords"""
        score = 0
        for topic in topics:
            topic_lower = topic.lower()
            # Kritische Themen
            if any(word in topic_lower for word in 
                  ['krise', 'suizid', 'trauma', 'panik', 'depression']):
                score += 3
            # Wichtige Themen
            elif any(word in topic_lower for word in
                    ['angst', 'beziehung', 'familie', 'konflikt', 'stress']):
                score += 2
            # Allgemeine Themen
            else:
                score += 1
        return min(7, score)  # Max 7 Punkte
    
    def set_chat_function(self, chat_function):
        """Setzt Chat-Funktion für LLM-basierte Topic-Analyse"""
        if self._topic_extractor:
            self._topic_extractor.set_chat_function(chat_function)
            logger.info("✓ Chat-Funktion für Session-Loader Topic-Extraktion gesetzt")

def demonstrate_session_management():
    """Demonstriert das Session-Management"""
    
    print("🧠📋 Session-Management für psychologische Unterstützung")
    print("="*60)
    
    loader = PsychologicalSessionLoader()
    
    if not loader.db:
        print("❌ Session-Loader konnte nicht initialisiert werden")
        return
    
    # Teste Session-Auflistung
    print("📋 Lade verfügbare Sessions...")
    sessions = loader.list_user_sessions("default_user")
    
    if sessions:
        print(f"\n✅ {len(sessions)} Sessions gefunden:")
        
        for i, session in enumerate(sessions[:5], 1):  # Zeige erste 5
            print(f"\n{i}. Session ID: {session['id'][:12]}...")
            print(f"   📅 Erstellt: {session['created_at'][:19]}")
            print(f"   💬 Nachrichten: {session['interaction_count']}")
            print(f"   🏷️  Themen: {', '.join(session['main_topics'])}")
            print(f"   📝 Vorschau: {session['preview'][:80]}...")
            print(f"   ⏰ Letzte Aktivität: vor {session['last_interaction_days_ago']} Tagen")
        
        # Teste Session-Empfehlungen
        print(f"\n🎯 Session-Empfehlungen:")
        recommendations = loader.recommend_sessions("default_user")
        
        for i, rec in enumerate(recommendations, 1):
            print(f"\n{i}. {rec['recommendation_text']}")
            print(f"   Score: {rec['score']} | Gründe: {', '.join(rec['reasons'])}")
        
        # Teste detaillierte Session-Informationen
        if sessions:
            print(f"\n🔍 Detailansicht der ersten Session:")
            details = loader.get_session_details(sessions[0]['id'])
            
            if details:
                print(f"   📊 Gesamtinteraktionen: {details['total_interactions']}")
                print(f"   💭 Gesprächsverlauf: {' → '.join(details['conversation_flow'])}")
                print(f"   😊 Stimmung: {', '.join(details['mood_progression'])}")
                print(f"   📋 Zusammenfassung: {details['session_summary']}")
    
    else:
        print("📋 Keine Sessions gefunden")
        print("💡 Starte eine neue psychologische Session im Bot")
    
    print(f"\n{'='*60}")
    print("✅ Session-Management Demonstration abgeschlossen!")


if __name__ == "__main__":
    demonstrate_session_management()
