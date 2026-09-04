#!/usr/bin/env python3
"""
Zentrale Topic-Extraktion für psychologische Module
==================================================
Einheitliche, LLM-basierte Topic-Erkennung für alle psychologischen Komponenten
"""

import logging
from typing import List, Dict, Any, Optional, Callable

logger = logging.getLogger(__name__)

class WellbeingTopicExtractor:
    """
    Zentrale Topic-Extraktion für psychologische Inhalte
    
    Verwendet LLM-basierte Analyse für adaptive, präzise Topic-Erkennung
    Ersetzt harte Keyword-Listen durch intelligente Sprachanalyse
    """
    
    def __init__(self, chat_function: Optional[Callable] = None):
        """
        Initialisiert den Topic-Extractor
        
        Args:
            chat_function: LLM-Chat-Funktion für Topic-Analyse
        """
        self.chat_function = chat_function
        logger.info("✓ WellbeingTopicExtractor initialisiert")
    
    def extract_topics_from_conversation(self, session_context: List[str], 
                                       current_message: str = "") -> List[str]:
        """
        Extrahiert Hauptthemen aus Gesprächskontext (für psychological_interface)
        
        Args:
            session_context: Liste bisheriger Nachrichten
            current_message: Aktuelle Nachricht
            
        Returns:
            Liste der erkannten psychologischen Themen
        """
        try:
            all_text = " ".join(session_context + [current_message] if current_message else session_context)
            
            # LLM-basierte Topic-Extraktion für dynamische, adaptive Erkennung
            if self.chat_function and callable(self.chat_function):
                topic_prompt = f"""
Analysiere die folgenden Gesprächsinhalte und identifiziere die 2-3 wichtigsten psychologischen Themen oder Anliegen.

GESPRÄCHSINHALTE:
{all_text[-2000:]}  # Letzte 2000 Zeichen

RICHTLINIEN:
- Gib nur die Themen aus, eines pro Zeile
- Verwende deutsche Begriffe
- Fokussiere auf psychologische/therapeutische Kategorien
- Beginne jede Zeile mit einem Bindestrich (-)
- Sei spezifisch aber nicht zu eng gefasst

Beispiele für gute Themen:
- Familienkonflikte
- Angstbewältigung
- Beziehungsprobleme
- Selbstwertthemen
- Stressmanagement
- Trauerbewältigung
- Strukturierte Herangehensweise
"""
                
                try:
                    response = self.chat_function(topic_prompt)
                    if response and isinstance(response, str):
                        topics = []
                        for line in response.split('\n'):
                            clean_line = line.strip()
                            if clean_line.startswith('-'):
                                topic = clean_line[1:].strip()
                                if topic and len(topic) > 3:  # Mindestlänge
                                    topics.append(topic)
                        
                        if topics:
                            logger.info(f"✨ LLM extrahierte Topics: {topics}")
                            return topics[:3]  # Max 3 Topics
                            
                except Exception as e:
                    logger.warning(f"⚠️ LLM-Topic-Extraktion fehlgeschlagen: {e}")
            
            # Backup: Einfache Keyword-basierte Erkennung nur für wichtigste Kategorien
            return self._fallback_topic_extraction(all_text)
            
        except Exception as e:
            logger.error(f"❌ Topic-Extraktion fehlgeschlagen: {e}")
            return ["Allgemeine Unterstützung"]
    
    def extract_topics_from_session_history(self, history: List[Dict[str, Any]]) -> List[str]:
        """
        Extrahiert Themen aus Session-Historie (für session_loader)
        
        Args:
            history: Liste von Interaktionen mit role/content
            
        Returns:
            Liste der erkannten Themen
        """
        try:
            # Sammle nur User-Nachrichten für Topic-Analyse
            user_messages = []
            for interaction in history:
                if interaction.get('role') == 'user':
                    user_messages.append(interaction.get('content', ''))
            
            if not user_messages:
                return ['Allgemeine Unterstützung']
            
            # Verwende dieselbe Logik wie für Conversation-Topics
            combined_text = " ".join(user_messages)
            
            # LLM-basierte Extraktion
            if self.chat_function and callable(self.chat_function):
                topic_prompt = f"""
Analysiere die folgenden Benutzer-Nachrichten aus einer psychologischen Session und identifiziere die Hauptthemen.

BENUTZER-NACHRICHTEN:
{combined_text[-2500:]}  # Letzte 2500 Zeichen

RICHTLINIEN:
- Extrahiere 2-5 psychologische Kernthemen
- Gib nur die Themen aus, eines pro Zeile
- Beginne jede Zeile mit einem Bindestrich (-)
- Verwende präzise deutsche Begriffe
- Fokussiere auf therapeutisch relevante Kategorien

Beispiele:
- Angstbewältigung
- Depression/Stimmungstiefs  
- Familienkonflikte
- Selbstwertprobleme
- Stressmanagement
- Beziehungsprobleme
- Traumaverarbeitung
"""
                
                try:
                    response = self.chat_function(topic_prompt)
                    if response and isinstance(response, str):
                        topics = []
                        for line in response.split('\n'):
                            clean_line = line.strip()
                            if clean_line.startswith('-'):
                                topic = clean_line[1:].strip()
                                if topic and len(topic) > 3:
                                    topics.append(topic)
                        
                        if topics:
                            logger.info(f"✨ Session-Topics extrahiert: {topics}")
                            return topics
                            
                except Exception as e:
                    logger.warning(f"⚠️ Session-Topic-Extraktion fehlgeschlagen: {e}")
            
            # Fallback
            return self._fallback_topic_extraction(combined_text)
            
        except Exception as e:
            logger.error(f"❌ Session-Topic-Extraktion fehlgeschlagen: {e}")
            return ['Allgemeine Unterstützung']
    
    def evaluate_topic_importance(self, topics: List[str]) -> Dict[str, int]:
        """
        Bewertet die psychologische Wichtigkeit von Topics (für session_loader)
        
        Args:
            topics: Liste der zu bewertenden Topics
            
        Returns:
            Dictionary mit Topic → Wichtigkeits-Score (1-10)
        """
        try:
            if not topics:
                return {}
            
            # LLM-basierte Wichtigkeitsbewertung
            if self.chat_function and callable(self.chat_function):
                importance_prompt = f"""
Bewerte die psychologische Wichtigkeit/Kritikalität der folgenden Themen auf einer Skala von 1-10.

THEMEN:
{chr(10).join(f'- {topic}' for topic in topics)}

BEWERTUNGSKRITERIEN:
- 9-10: Kritische/akute Themen (Krise, Suizidalität, schwere Depression, Trauma)
- 7-8: Wichtige therapeutische Themen (Angst, mittlere Depression, Beziehungskrisen)
- 5-6: Relevante Alltagsthemen (Stress, Selbstwert, Kommunikation)
- 3-4: Unterstützende Themen (Struktur, Planung, leichte Belastung)
- 1-2: Allgemeine/niedrigschwellige Themen

AUSGABEFORMAT:
Thema: Score
(Nur das Format "Thema: X" verwenden, ein Thema pro Zeile)
"""
                
                try:
                    response = self.chat_function(importance_prompt)
                    if response and isinstance(response, str):
                        scores = {}
                        for line in response.split('\n'):
                            if ':' in line:
                                try:
                                    topic_part, score_part = line.split(':', 1)
                                    topic = topic_part.strip()
                                    score = int(score_part.strip())
                                    if 1 <= score <= 10 and topic in topics:
                                        scores[topic] = score
                                except ValueError:
                                    continue
                        
                        if scores:
                            logger.info(f"✨ Topic-Wichtigkeit bewertet: {scores}")
                            return scores
                            
                except Exception as e:
                    logger.warning(f"⚠️ Topic-Wichtigkeitsbewertung fehlgeschlagen: {e}")
            
            # Fallback: Semantische Keyword-basierte Bewertung
            return self._fallback_importance_evaluation(topics)
            
        except Exception as e:
            logger.error(f"❌ Topic-Wichtigkeitsbewertung fehlgeschlagen: {e}")
            return {}
    
    def _fallback_topic_extraction(self, text: str) -> List[str]:
        """Backup: Keyword-basierte Topic-Extraktion"""
        topics = []
        text_lower = text.lower()
        
        # Nur essentielle Kategorien mit erweiterten Keywords
        if any(word in text_lower for word in ["familie", "eltern", "mutter", "vater", "geschwister", "partner", "beziehung"]):
            topics.append("Familiäre/Partnerschaftliche Themen")
        
        if any(word in text_lower for word in ["angst", "ängstlich", "befürcht", "sorge", "panik"]):
            topics.append("Angstbewältigung")
            
        if any(word in text_lower for word in ["traurig", "deprimiert", "niedergeschlagen", "hoffnungslos"]):
            topics.append("Stimmungstiefs")
            
        if any(word in text_lower for word in ["stress", "überfordert", "druck", "belastet"]):
            topics.append("Stressmanagement")
            
        if any(word in text_lower for word in ["strukturiert", "methodisch", "plan", "schritte", "vorgehen"]):
            topics.append("Strukturierte Herangehensweise")
        
        return topics if topics else ["Allgemeine Unterstützung"]
    
    def _fallback_importance_evaluation(self, topics: List[str]) -> Dict[str, int]:
        """Backup: Keyword-basierte Wichtigkeitsbewertung"""
        scores = {}
        
        for topic in topics:
            topic_lower = topic.lower()
            
            # Kritische Themen (8-9)
            if any(word in topic_lower for word in 
                  ['krise', 'suizid', 'trauma', 'missbrauch', 'gewalt', 'panik']):
                scores[topic] = 9
            # Wichtige therapeutische Themen (6-7)
            elif any(word in topic_lower for word in
                    ['depression', 'angst', 'beziehung', 'familie', 'konflikt']):
                scores[topic] = 7
            # Relevante Alltagsthemen (4-5)
            elif any(word in topic_lower for word in
                    ['stress', 'selbstwert', 'schlaf', 'arbeit', 'kommunikation']):
                scores[topic] = 5
            # Strukturelle/unterstützende Themen (3-4)
            elif any(word in topic_lower for word in
                    ['struktur', 'plan', 'methode', 'vorgehen', 'organisation']):
                scores[topic] = 3
            # Allgemeine Themen (2)
            else:
                scores[topic] = 2
        
        return scores
    
    def set_chat_function(self, chat_function: Callable) -> None:
        """Setzt/ändert die Chat-Funktion"""
        self.chat_function = chat_function
        logger.info("✓ Chat-Funktion für TopicExtractor aktualisiert")
