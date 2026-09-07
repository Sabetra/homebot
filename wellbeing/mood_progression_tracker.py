"""
MOOD PROGRESSION TRACKER
========================
Intelligentes Mood-Tracking mit zeitgewichteter Analyse

Features:
- Exponential Weighting (neuere Moods wichtiger)
- Statistische Signifikanz-Prüfung
- Zeitbasierte Regression für Trend-Analyse
- Throttling (max 1 Update / 5 Min)
- Human-readable Insights
"""

import json
import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Any, Callable
import numpy as np

logger = logging.getLogger(__name__)


class MoodProgressionTracker:
    """
    Intelligentes Mood-Tracking mit zeitgewichteter Analyse und statistischer Validierung
    """
    
    def __init__(self, db: Any) -> None:
        """
        Args:
            db: WellbeingDatabase Instanz
        """
        self.db = db
        self._last_update: Dict[str, datetime] = {}  # Session-ID → Timestamp (für Throttling)
        self._update_threshold = timedelta(minutes=5)  # Max. alle 5 Min
        
        # Mood-Kategorien mit Valenz-Scores (0.0 = sehr negativ, 1.0 = sehr positiv)
        self.mood_valence = {
            # Sehr Positiv (0.8-1.0)
            'happy': 1.0,
            'joyful': 1.0,
            'ecstatic': 1.0,
            'grateful': 0.9,
            'hopeful': 0.85,
            'optimistic': 0.85,
            'excited': 0.85,
            
            # Positiv (0.6-0.8)
            'content': 0.75,
            'calm': 0.7,
            'relaxed': 0.7,
            'peaceful': 0.7,
            'satisfied': 0.75,
            'pleased': 0.7,
            
            # Leicht Positiv (0.5-0.6)
            'okay': 0.55,
            'fine': 0.55,
            'comfortable': 0.6,
            
            # Neutral (0.45-0.55)
            'neutral': 0.5,
            'indifferent': 0.5,
            'meh': 0.5,
            
            # Leicht Negativ (0.3-0.45)
            'tired': 0.4,
            'bored': 0.4,
            'restless': 0.35,
            'uneasy': 0.35,
            
            # Negativ (0.15-0.3)
            'sad': 0.3,
            'worried': 0.25,
            'anxious': 0.2,
            'stressed': 0.2,
            'frustrated': 0.25,
            'angry': 0.2,
            'lonely': 0.25,
            
            # Sehr Negativ (0.0-0.15)
            'depressed': 0.05,
            'hopeless': 0.0,
            'desperate': 0.05,
            'fearful': 0.1,
            'overwhelmed': 0.1,
            'panicked': 0.05,
        }
        
        # Fallback für unbekannte Moods
        self.DEFAULT_VALENCE = 0.5
        
        # Minimum-Thresholds
        self.MIN_MOODS_FOR_ANALYSIS = 3  # Mindestens 3 Moods für Trend
        self.MIN_MOODS_FOR_HIGH_CONFIDENCE = 10  # 10+ für hohe Confidence
    
    def should_update(self, session_id: str) -> bool:
        """
        Throttling: Update nur alle X Minuten (außer force=True)
        
        Returns:
            True wenn Update erlaubt, False wenn zu früh
        """
        last_update: Optional[datetime] = self._last_update.get(session_id)
        
        if not last_update:
            return True  # Noch nie updated
        
        time_since_update = datetime.now(timezone.utc) - last_update
        return bool(time_since_update >= self._update_threshold)
    
    def update_mood_progression(
        self, 
        session_id: str,
        force: bool = False
    ) -> Optional[Dict[str, Any]]:
        """
        Aktualisiert Mood Progression mit intelligenter zeitgewichteter Analyse
        
        Args:
            session_id: Session ID
            force: Ignoriere Throttling (für manuelle Updates)
        
        Returns:
            {
                'overall_trend': 'improving'/'stable'/'declining',
                'trend_strength': 0.65,    # 0.0-1.0: Wie stark ist der Trend
                'confidence': 0.82,         # 0.0-1.0: Statistische Signifikanz
                'current_valence': 0.7,     # Aktueller Mood-Score
                'average_valence': 0.55,    # Gewichteter Durchschnitt
                'mood_count': 15,           # Anzahl Moods
                'mood_history': [...],      # Letzte 10 Moods mit Timestamps
                'insights': 'Ihre Stimmung verbessert sich...',
                'updated_at': '2025-10-28T...'
            }
            oder None wenn zu wenig Daten oder Update throttled
        """
        # Throttling (außer force=True)
        if not force and not self.should_update(session_id):
            logger.debug(f"⏳ Mood Update throttled für Session {session_id}")
            return None
        
        # Hole alle Moods mit Timestamps aus DB
        mood_series = self._load_mood_series(session_id)
        
        if len(mood_series) < self.MIN_MOODS_FOR_ANALYSIS:
            logger.debug(f"Zu wenig Mood-Daten ({len(mood_series)}/{self.MIN_MOODS_FOR_ANALYSIS}) für Analyse")
            return None
        
        # Zeitgewichtete Trend-Analyse
        trend_result = self._calculate_weighted_trend(mood_series)
        
        # Generiere human-readable Insights
        insights = self._generate_mood_insights(trend_result, mood_series)
        
        # Erstelle Progression-Objekt
        progression = {
            'overall_trend': trend_result['trend'],
            'trend_strength': trend_result['strength'],
            'confidence': trend_result['confidence'],
            'current_valence': mood_series[-1]['valence'],
            'average_valence': trend_result['average'],
            'mood_count': len(mood_series),
            'mood_history': [
                {
                    'mood': m['mood'],
                    'valence': m['valence'],
                    'time': m['timestamp'].isoformat()
                }
                for m in mood_series[-10:]  # Letzte 10 für History
            ],
            'insights': insights,
            'updated_at': datetime.now(timezone.utc).isoformat()
        }
        
        # Speichere in DB
        self._save_progression_to_db(session_id, progression)
        
        # Update Throttling-Timestamp
        self._last_update[session_id] = datetime.now(timezone.utc)
        
        logger.info(
            f"✅ Mood Progression: {trend_result['trend']} "
            f"(Strength: {trend_result['strength']:.2f}, Conf: {trend_result['confidence']:.2f})"
        )
        
        return progression
    
    def _load_mood_series(self, session_id: str) -> List[Dict[str, Any]]:
        """
        Lädt Mood-Zeitreihe aus DB mit Timestamps
        
        Returns:
            [
                {'mood': 'happy', 'valence': 1.0, 'timestamp': datetime(...)},
                ...
            ]
        """
        try:
            with self.db.get_connection() as conn:
                cursor = conn.execute(
                    """
                                        SELECT mood_indicators, created_at
                    FROM session_interactions
                    WHERE session_id = ?
                                            AND role = 'user'
                      AND mood_indicators IS NOT NULL
                      AND mood_indicators != ''
                                            AND created_at IS NOT NULL
                                        ORDER BY created_at ASC
                    """,
                    (session_id,),
                )
                interactions = cursor.fetchall()
            
            mood_series: List[Dict[str, Any]] = []
            
            for interaction in interactions:
                try:
                    mood_indicators_raw = interaction["mood_indicators"]
                    if not mood_indicators_raw:
                        continue
                    
                    # ✅ FIX: Robustere Parsing-Logik
                    mood_data = None
                    try:
                        mood_data = json.loads(mood_indicators_raw)
                    except (json.JSONDecodeError, TypeError):
                        try:
                            decrypted = self.db._decrypt_data(mood_indicators_raw)
                            if decrypted:
                                mood_data = json.loads(decrypted)
                        except Exception:
                            pass  # Ignoriere Parsing-Fehler
                    
                    # ✅ FIX: Überspringe wenn mood_data None ist oder kein Dict
                    if mood_data is None or not isinstance(mood_data, dict):
                        continue
                    
                    # ✅ FIX: Mood extrahieren mit EXTRA-SICHEREN Checks
                    # Doppelte None-Checks um Race-Conditions zu vermeiden
                    mood = None
                    
                    # 1. Versuche 'overall_mood' (mit doppeltem None-Check)
                    if mood_data is not None and isinstance(mood_data, dict):
                        if 'overall_mood' in mood_data and mood_data.get('overall_mood'):
                            mood = mood_data['overall_mood']
                    
                    # 2. Versuche 'emotional_markers' (Liste)
                    if mood is None and isinstance(mood_data, dict):
                        markers = mood_data.get('emotional_markers')
                        if isinstance(markers, list) and len(markers) > 0:
                            mood = markers[0]
                    
                    # 3. Versuche 'mood'
                    if mood is None and isinstance(mood_data, dict):
                        mood_val = mood_data.get('mood')
                        if mood_val:
                            mood = mood_val
                    
                    # ✅ FIX: Expliziter None-Check ZUERST (verhindert "NoneType is not iterable")
                    if mood is None:
                        continue
                    
                    # ✅ FIX: Dann String-Checks (nur wenn mood nicht None ist)
                    if isinstance(mood, str) and mood.strip() == '':
                        continue
                    
                    # ✅ FIX: Konvertiere mood zu String falls es ein anderer Typ ist
                    mood = str(mood) if not isinstance(mood, str) else mood
                    # Timestamp aus created_at normalisieren
                    timestamp_str = interaction["created_at"]
                    if isinstance(timestamp_str, bytes):
                        timestamp_str = timestamp_str.decode('utf-8', errors='ignore')
                    if 'T' not in timestamp_str and ' ' in timestamp_str:
                        timestamp_str = timestamp_str.replace(' ', 'T')
                    if not timestamp_str.endswith('Z') and '+' not in timestamp_str:
                        timestamp_str += '+00:00'
                    timestamp = datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))
                    # ✅ FIX: Valence-Lookup mit explizitem None-Check und str() Konvertierung
                    mood_key = str(mood).lower() if mood is not None else 'neutral'
                    valence = self.mood_valence.get(mood_key, self.DEFAULT_VALENCE)
                    mood_series.append({
                        'mood': mood,
                        'valence': valence,
                        'timestamp': timestamp,
                    })
                except Exception as e:
                    logger.warning(f"Mood-Parsing fehlgeschlagen für Interaction: {e}")
                    continue
            
            # Fallback: Falls keine Daten in session_interactions, versuche mood_tracking
            if not mood_series:
                try:
                    with self.db.get_connection() as conn:
                        cursor = conn.execute(
                            """
                            SELECT mood, valence, timestamp
                            FROM mood_tracking
                            WHERE session_id = ?
                            ORDER BY timestamp ASC
                            """,
                            (session_id,),
                        )
                        rows = cursor.fetchall()
                    for row in rows:
                        mood = row["mood"]
                        valence = float(row["valence"]) if row["valence"] is not None else self.DEFAULT_VALENCE
                        ts = row["timestamp"]
                        if 'T' not in ts and ' ' in ts:
                            ts = ts.replace(' ', 'T')
                        if not ts.endswith('Z') and '+' not in ts:
                            ts += '+00:00'
                        timestamp = datetime.fromisoformat(ts.replace('Z', '+00:00'))
                        mood_series.append({
                            'mood': mood,
                            'valence': valence,
                            'timestamp': timestamp,
                        })
                except Exception:
                    pass
            
            return mood_series
        except Exception as e:
            logger.error(f"❌ Fehler beim Laden der Mood-Serie: {e}", exc_info=True)
            return []
    
    def _calculate_weighted_trend(self, mood_series: List[Dict]) -> Dict[str, Any]:
        """
        Berechnet zeitgewichteten Trend mit statistischer Validierung
        
        Returns:
            {
                'trend': 'improving'/'stable'/'declining',
                'strength': 0.65,      # 0.0-1.0
                'confidence': 0.82,    # 0.0-1.0
                'average': 0.55,       # Gewichteter Durchschnitt
                'slope': 1.2e-6        # Raw slope (für Debugging)
            }
        """
        if len(mood_series) < self.MIN_MOODS_FOR_ANALYSIS:
            return {
                'trend': 'insufficient_data',
                'strength': 0.0,
                'confidence': 0.0,
                'average': self.DEFAULT_VALENCE,
                'slope': 0.0
            }
        
        # 1. EXPONENTIAL WEIGHTING (neuere Moods wichtiger)
        weights = []
        decay_factor = 0.9  # Ältere Moods werden mit 0.9^n gewichtet
        
        for i in range(len(mood_series)):
            age = len(mood_series) - i - 1  # 0 für neueste, N für älteste
            weight = decay_factor ** age
            weights.append(weight)
        
        # Normalisiere Weights (Summe = 1.0)
        total_weight = sum(weights)
        normalized_weights = [w / total_weight for w in weights]
        
        # 2. GEWICHTETER DURCHSCHNITT
        weighted_avg = sum(
            m['valence'] * w
            for m, w in zip(mood_series, normalized_weights)
        )
        
        # 3. LINEAR REGRESSION FÜR TREND
        # Konvertiere Timestamps zu numerischen Werten (Sekunden seit erstem Mood)
        timestamps_numeric = [
            (m['timestamp'] - mood_series[0]['timestamp']).total_seconds()
            for m in mood_series
        ]
        valences = [m['valence'] for m in mood_series]
        
        # Gewichtete lineare Regression (Polyfit mit Weights)
        try:
            coefficients = np.polyfit(timestamps_numeric, valences, 1, w=normalized_weights)
            slope = coefficients[0]
        except Exception as e:
            logger.warning(f"Regression fehlgeschlagen: {e}")
            slope = 0.0
        
        # 4. TREND-KLASSIFIKATION basierend auf Slope
        # Slope ist in "Valenz-Änderung pro Sekunde"
        # Typische Werte: ~1e-7 bis 1e-5 (sehr klein wegen Sekunden!)
        
        threshold_stable = 5e-7  # Quasi-konstant wenn |slope| < threshold
        
        if abs(slope) < threshold_stable:
            trend = 'stable'
            strength = 0.0
        elif slope > 0:
            trend = 'improving'
            # Normalisiere slope zu 0-1 Range (heuristisch)
            strength = min(abs(slope) * 1e6, 1.0)
        else:
            trend = 'declining'
            strength = min(abs(slope) * 1e6, 1.0)
        
        # 5. CONFIDENCE-BERECHNUNG
        # Faktoren: Datenmenge + Konsistenz (niedrige Varianz = hohe Confidence)
        
        # Daten-Confidence (mehr Daten = höher)
        data_confidence = min(len(mood_series) / self.MIN_MOODS_FOR_HIGH_CONFIDENCE, 1.0)
        
        # Konsistenz-Confidence (niedrige Varianz = hohe Confidence)
        variance = np.var(valences)
        # Varianz von 0.0 (perfekt konsistent) bis ~0.25 (sehr inkonsistent)
        consistency_confidence = max(1.0 - (variance * 4), 0.0)
        
        # Gesamt-Confidence (Durchschnitt)
        overall_confidence = (data_confidence + consistency_confidence) / 2
        
        return {
            'trend': trend,
            'strength': round(strength, 2),
            'confidence': round(overall_confidence, 2),
            'average': round(weighted_avg, 2),
            'slope': slope  # Raw für Debugging
        }
    
    def _generate_mood_insights(
        self,
        trend_result: Dict,
        mood_series: List[Dict]
    ) -> str:
        """
        Generiert human-readable Insights aus Trend-Analyse
        
        Returns:
            "Ihre Stimmung verbessert sich deutlich (robuste Datenlage). Aktuell: happy."
        """
        trend = trend_result['trend']
        strength = trend_result['strength']
        confidence = trend_result['confidence']
        
        # 1. BASIS-INSIGHT (Trend-Beschreibung)
        if trend == 'improving':
            if strength > 0.7:
                base = "Ihre Stimmung verbessert sich deutlich"
            elif strength > 0.4:
                base = "Ihre Stimmung zeigt eine positive Entwicklung"
            else:
                base = "Ihre Stimmung verbessert sich leicht"
        elif trend == 'declining':
            if strength > 0.7:
                base = "Ihre Stimmung verschlechtert sich merklich"
            elif strength > 0.4:
                base = "Ihre Stimmung zeigt einen negativen Trend"
            else:
                base = "Ihre Stimmung ist leicht rückläufig"
        else:  # stable
            base = "Ihre Stimmung ist weitgehend stabil"
        
        # 2. CONFIDENCE-QUALIFIER
        if confidence < 0.5:
            qualifier = " (noch unsicher, wenig Daten)"
        elif confidence < 0.7:
            qualifier = " (moderate Datenlage)"
        else:
            qualifier = " (robuste Datenlage)"
        
        # 3. AKTUELLER ZUSTAND
        current_mood = mood_series[-1]['mood']
        current_text = f" Aktuell: {current_mood}."
        
        return base + qualifier + "." + current_text
    
    def _save_progression_to_db(self, session_id: str, progression: Dict[str, Any]) -> None:
        """Speichert Mood Progression in DB"""
        try:
            progression_json = json.dumps(progression)
            
            with self.db.get_connection() as conn:
                conn.execute("""
                    UPDATE wellbeing_sessions
                    SET mood_progression = ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                """, (progression_json, session_id))
                
                conn.commit()
                logger.debug(f"💾 Mood Progression gespeichert für Session {session_id}")
                
        except Exception as e:
            logger.error(f"❌ Fehler beim Speichern der Mood Progression: {e}", exc_info=True)
    
    def get_progression_for_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        """
        Holt gespeicherte Mood Progression aus DB
        
        Returns:
            Progression-Dict oder None
        """
        try:
            with self.db.get_connection() as conn:
                cursor = conn.execute("""
                    SELECT mood_progression
                    FROM wellbeing_sessions
                    WHERE id = ?
                """, (session_id,))
                
                row = cursor.fetchone()
                
                if not row or not row['mood_progression']:
                    return None
                
                progression_data: Any = json.loads(row['mood_progression'])
                # Ensure we return the correct type
                return progression_data if isinstance(progression_data, dict) else None
                
        except Exception as e:
            logger.error(f"Fehler beim Laden der Mood Progression: {e}")
            return None
    
    def analyze_and_store_mood(
        self,
        user_message: str,
        session_id: str,
        llm_function: Optional[Callable[..., str]] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Analysiert User-Nachricht für emotionale Indikatoren und speichert Mood
        
        Nutzt LLM für intelligente Mood-Erkennung oder Fallback zu Keyword-basiert.
        
        Args:
            user_message: Die User-Nachricht
            session_id: Session-ID
            llm_function: Optional LLM-Funktion für Mood-Extraktion
        
        Returns:
            {
                'detected_mood': 'anxious',
                'confidence': 0.8,
                'valence': 0.2,
                'reasoning': 'Nutzer drückt Sorgen aus...'
            }
            oder None wenn kein Mood erkannt
        """
        try:
            # === MOOD-ERKENNUNG ===
            detected_mood = None
            confidence = 0.5
            reasoning = ""
            
            if llm_function:
                # LLM-basierte Mood-Extraktion (bevorzugt)
                try:
                    llm_result = self._extract_mood_via_llm(user_message, llm_function)
                    if llm_result:
                        detected_mood = llm_result.get('mood')
                        confidence = llm_result.get('confidence', 0.8)
                        reasoning = llm_result.get('reasoning', '')
                except Exception as e:
                    logger.warning(f"LLM-Mood-Extraktion fehlgeschlagen: {e}, nutze Fallback")
            
            if not detected_mood:
                # Fallback: Keyword-basierte Erkennung
                keyword_result = self._extract_mood_via_keywords(user_message)
                if keyword_result:
                    detected_mood = keyword_result.get('mood')
                    confidence = keyword_result.get('confidence', 0.5)
                    reasoning = keyword_result.get('reasoning', 'Keyword-basiert erkannt')
            
            if not detected_mood:
                logger.debug(f"Kein Mood in Nachricht erkannt: '{user_message[:50]}...'")
                return None
            
            # === MOOD SPEICHERN ===
            # Hole Valence-Score für erkannten Mood
            valence = self.mood_valence.get(detected_mood.lower(), self.DEFAULT_VALENCE)
            
            # Speichere in session_interactions (falls Tabelle mood_tracking existiert)
            # Oder: Update wellbeing_sessions.mood_progression
            self._store_mood_in_db(
                session_id=session_id,
                mood=detected_mood,
                valence=valence,
                confidence=confidence,
                reasoning=reasoning
            )
            
            # Trigger Progression-Update (mit Throttling)
            self.update_mood_progression(session_id, force=False)
            
            result = {
                'detected_mood': detected_mood,
                'confidence': confidence,
                'valence': valence,
                'reasoning': reasoning
            }
            
            logger.info(f"✅ Mood gespeichert: {detected_mood} (valence={valence:.2f}, conf={confidence:.2f})")
            return result
            
        except Exception as e:
            logger.error(f"❌ analyze_and_store_mood fehlgeschlagen: {e}")
            return None
    
    def _extract_mood_via_llm(
        self,
        user_message: str,
        llm_function: Callable[..., str]
    ) -> Optional[Dict[str, Any]]:
        """
        Extrahiert Mood via LLM (intelligente semantische Analyse)
        
        Args:
            user_message: User-Nachricht
            llm_function: LLM-Funktion (z.B. aus agent_chatbot_logic)
        
        Returns:
            {
                'mood': 'anxious',
                'confidence': 0.85,
                'reasoning': 'User expresses worry about...'
            }
        """
        try:
            # LLM-Prompt für Mood-Extraktion
            prompt = f"""Analysiere die folgende Nachricht und erkenne die emotionale Stimmung (Mood) des Nutzers.

Nachricht: "{user_message}"

Gib NUR ein JSON-Objekt zurück (keine Erklärung):
{{
    "mood": "<einer von: {', '.join(list(self.mood_valence.keys())[:10])}... oder ähnlich>",
    "confidence": <0.0-1.0>,
    "reasoning": "<kurze Begründung>"
}}

Falls KEIN klarer Mood erkennbar ist, gib zurück:
{{"mood": null, "confidence": 0.0, "reasoning": "Keine emotionalen Indikatoren"}}"""

            # Rufe LLM auf
            llm_response = llm_function(prompt)
            
            # Parse JSON aus Response
            import re
            json_match = re.search(r'\{.*\}', llm_response, re.DOTALL)
            if json_match:
                parsed_data: Any = json.loads(json_match.group())
                
                # Validierung: Ensure it's a dict with required fields
                if isinstance(parsed_data, dict) and parsed_data.get('mood') and parsed_data.get('confidence', 0) > 0.3:
                    return parsed_data
            
            return None
            
        except Exception as e:
            logger.error(f"LLM-Mood-Extraktion fehlgeschlagen: {e}")
            return None
    
    def _extract_mood_via_keywords(
        self,
        user_message: str
    ) -> Optional[Dict[str, Any]]:
        """
        Fallback: Keyword-basierte Mood-Erkennung
        
        Args:
            user_message: User-Nachricht
        
        Returns:
            {
                'mood': 'sad',
                'confidence': 0.6,
                'reasoning': 'Keywords: traurig, niedergeschlagen'
            }
        """
        message_lower = user_message.lower()
        
        # Mood-Keyword-Mappings (Deutsch)
        keyword_map = {
            'anxious': ['angst', 'ängstlich', 'sorge', 'nervös', 'unruhig', 'besorgt'],
            'sad': ['traurig', 'niedergeschlagen', 'deprimiert', 'weinen', 'tränen'],
            'stressed': ['stress', 'gestresst', 'überfordert', 'druck'],
            'angry': ['wütend', 'ärger', 'wut', 'frustriert', 'sauer'],
            'worried': ['sorgen', 'beunruhigt', 'grübel'],
            'happy': ['glücklich', 'freude', 'froh', 'fröhlich'],
            'hopeful': ['hoffnung', 'hoffnungsvoll', 'optimistisch'],
            'calm': ['ruhig', 'gelassen', 'entspannt'],
            'tired': ['müde', 'erschöpft', 'kraftlos', 'schlaflos'],
            'lonely': ['einsam', 'allein', 'isoliert'],
        }
        
        # Zähle Keyword-Matches
        best_mood = None
        best_score = 0
        matched_keywords = []
        
        for mood, keywords in keyword_map.items():
            score = sum(1 for kw in keywords if kw in message_lower)
            if score > best_score:
                best_score = score
                best_mood = mood
                matched_keywords = [kw for kw in keywords if kw in message_lower]
        
        if best_mood and best_score > 0:
            # Confidence basiert auf Anzahl Matches
            confidence = min(0.8, 0.4 + (best_score * 0.2))
            
            return {
                'mood': best_mood,
                'confidence': confidence,
                'reasoning': f"Keywords: {', '.join(matched_keywords)}"
            }
        
        return None
    
    def _store_mood_in_db(
        self,
        session_id: str,
        mood: str,
        valence: float,
        confidence: float,
        reasoning: str
    ) -> None:
        """
        Speichert Mood-Eintrag in DB
        
        Strategie:
        1. Prüfe ob mood_tracking Tabelle existiert
        2. Falls ja: INSERT in mood_tracking
        3. Falls nein: Append zu session_interactions als Metadaten
        """
        try:
            timestamp = datetime.now(timezone.utc).isoformat()
            
            # Versuche mood_tracking Tabelle
            with self.db.get_connection() as conn:
                try:
                    conn.execute("""
                        INSERT INTO mood_tracking (
                            session_id, mood, valence, confidence, 
                            reasoning, timestamp
                        ) VALUES (?, ?, ?, ?, ?, ?)
                    """, (session_id, mood, valence, confidence, reasoning, timestamp))
                    conn.commit()
                    logger.debug(f"✅ Mood in mood_tracking gespeichert")
                    
                except Exception as e:
                    # Tabelle existiert nicht → erstelle sie
                    logger.debug(f"mood_tracking Tabelle existiert nicht, erstelle...")
                    conn.execute("""
                        CREATE TABLE IF NOT EXISTS mood_tracking (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            session_id TEXT NOT NULL,
                            mood TEXT NOT NULL,
                            valence REAL NOT NULL,
                            confidence REAL NOT NULL,
                            reasoning TEXT,
                            timestamp TEXT NOT NULL,
                            FOREIGN KEY (session_id) REFERENCES wellbeing_sessions(id)
                        )
                    """)
                    conn.commit()
                    
                    # Nochmal versuchen
                    conn.execute("""
                        INSERT INTO mood_tracking (
                            session_id, mood, valence, confidence, 
                            reasoning, timestamp
                        ) VALUES (?, ?, ?, ?, ?, ?)
                    """, (session_id, mood, valence, confidence, reasoning, timestamp))
                    conn.commit()
                    logger.info(f"✅ mood_tracking Tabelle erstellt und Mood gespeichert")
                    
        except Exception as e:
            logger.error(f"❌ Mood-Speicherung fehlgeschlagen: {e}")

    def get_mood_trend(self, user_id: str, days: int = 7) -> Optional[Dict[str, Any]]:
        """
        Holt User-übergreifenden Mood-Trend über mehrere Tage/Sessions.
        
        Diese Methode aggregiert Mood-Daten aus allen Sessions eines Users
        über den angegebenen Zeitraum.
        
        Args:
            user_id: User-ID
            days: Zeitraum in Tagen (default: 7)
        
        Returns:
            {
                'overall_trend': 'improving'/'stable'/'declining',
                'trend_strength': 0.65,
                'confidence': 0.82,
                'current_valence': 0.7,
                'average_valence': 0.55,
                'mood_count': 15,
                'sessions_analyzed': 3,
                'mood_history': [...],
                'insights': 'Ihre Stimmung verbessert sich...',
                'period_days': 7
            }
            oder None wenn zu wenig Daten
        """
        try:
            cutoff_date = datetime.now(timezone.utc) - timedelta(days=days)
            cutoff_str = cutoff_date.isoformat()
            
            # 1. Hole alle relevanten Sessions des Users
            with self.db.get_connection() as conn:
                cursor = conn.execute("""
                    SELECT id, mood_progression, start_time
                    FROM wellbeing_sessions
                    WHERE user_id = ?
                      AND start_time >= ?
                    ORDER BY start_time DESC
                """, (user_id, cutoff_str))
                sessions = cursor.fetchall()
            
            if not sessions:
                logger.debug(f"Keine Sessions für User {user_id} in den letzten {days} Tagen")
                return None
            
            # 2. Sammle alle Mood-Daten aus Sessions
            all_moods = []
            sessions_with_moods = 0
            
            for session in sessions:
                session_id = session['id']
                mood_progression_raw = session['mood_progression']
                
                # Versuche gespeicherte Progression zu parsen
                if mood_progression_raw:
                    try:
                        progression = json.loads(mood_progression_raw)
                        if progression.get('mood_history'):
                            for mood_entry in progression['mood_history']:
                                all_moods.append({
                                    'mood': mood_entry.get('mood'),
                                    'valence': mood_entry.get('valence', self.DEFAULT_VALENCE),
                                    'timestamp': datetime.fromisoformat(mood_entry.get('time', cutoff_str).replace('Z', '+00:00'))
                                })
                            sessions_with_moods += 1
                            continue
                    except (json.JSONDecodeError, TypeError):
                        pass
                
                # Fallback: Lade Moods direkt aus Session
                session_moods = self._load_mood_series(session_id)
                if session_moods:
                    all_moods.extend(session_moods)
                    sessions_with_moods += 1
            
            if len(all_moods) < self.MIN_MOODS_FOR_ANALYSIS:
                logger.debug(f"Zu wenig Mood-Daten ({len(all_moods)}/{self.MIN_MOODS_FOR_ANALYSIS}) für User-Trend")
                return None
            
            # 3. Sortiere nach Timestamp
            all_moods.sort(key=lambda x: x['timestamp'])
            
            # 4. Berechne User-übergreifenden Trend
            trend_result = self._calculate_weighted_trend(all_moods)
            
            # 5. Generiere Insights
            insights = self._generate_mood_insights(trend_result, all_moods)
            
            # 6. Erstelle Ergebnis
            result = {
                'overall_trend': trend_result['trend'],
                'trend_strength': trend_result['strength'],
                'confidence': trend_result['confidence'],
                'current_valence': all_moods[-1]['valence'] if all_moods else 0.5,
                'average_valence': trend_result['average'],
                'mood_count': len(all_moods),
                'sessions_analyzed': sessions_with_moods,
                'mood_history': [
                    {
                        'mood': m['mood'],
                        'valence': m['valence'],
                        'time': m['timestamp'].isoformat() if isinstance(m['timestamp'], datetime) else m['timestamp']
                    }
                    for m in all_moods[-15:]  # Letzte 15 für History
                ],
                'insights': insights,
                'period_days': days,
                'updated_at': datetime.now(timezone.utc).isoformat()
            }
            
            logger.info(
                f"✅ User Mood Trend: {trend_result['trend']} "
                f"(Sessions: {sessions_with_moods}, Moods: {len(all_moods)}, Days: {days})"
            )
            
            return result
            
        except Exception as e:
            logger.error(f"❌ Fehler beim Berechnen des User Mood Trends: {e}", exc_info=True)
            return None
