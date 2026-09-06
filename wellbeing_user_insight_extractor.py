#!/usr/bin/env python3
"""
PSYCHOLOGISCHER USER-INSIGHT-EXTRACTOR
======================================

Integrierte User-Persönlichkeitsanalyse als Teil des psychologischen Systems.
Extrahiert tiefe Persönlichkeitseinsichten während natürlicher Care-Gespräche.

Features:
- LLM-basierte psychologische Analyse
- Historische Persönlichkeitsentwicklung
- Integration in psychologische Sessions
- Verschlüsselte Speicherung in WellbeingDatabase
- Automatische Validierung und Updates
"""

import logging
import json
from datetime import datetime, timezone
from typing import Dict, List, Any, Optional
from dataclasses import dataclass, asdict

logger = logging.getLogger(__name__)

@dataclass
class PersonalityInsight:
    """Psychologische Persönlichkeitseinsicht"""
    user_id: str
    session_id: str
    insight_type: str  # personality_trait, life_event, behavioral_pattern, emotional_state
    category: str      # core_personality, current_state, relationships, goals, fears
    value: str
    description: str
    confidence: float
    evidence: List[str]  # Zitate/Belege aus dem Gespräch
    temporal_context: str  # current, past, future, developing
    created_at: str
    validated_at: Optional[str] = None
    
    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.now(timezone.utc).isoformat()

@dataclass
class PersonalityProfile:
    """Vollständiges psychologisches Persönlichkeitsprofil"""
    user_id: str
    
    # Kern-Persönlichkeit (stabil)
    core_traits: Optional[List[str]] = None
    personality_type: Optional[str] = None
    communication_style: Optional[str] = None
    decision_making_style: Optional[str] = None
    
    # Aktuelle Zustände (veränderlich)
    current_mood: Optional[str] = None
    stress_level: Optional[str] = None
    life_phase: Optional[str] = None
    primary_concerns: Optional[List[str]] = None
    
    # Beziehungen und Soziales
    relationship_patterns: Optional[List[str]] = None
    social_style: Optional[str] = None
    attachment_style: Optional[str] = None
    
    # Ziele und Entwicklung
    current_goals: Optional[List[str]] = None
    personal_growth_areas: Optional[List[str]] = None
    coping_strategies: Optional[List[str]] = None
    
    # Lebensgeschichte
    significant_events: Optional[List[Dict[str, Any]]] = None
    life_transitions: Optional[List[Dict[str, Any]]] = None
    
    # Metadaten
    confidence_score: float = 0.0
    last_updated: Optional[str] = None
    session_count: int = 0
    
    def __post_init__(self):
        # Initialisiere Listen
        for field in ['core_traits', 'primary_concerns', 'relationship_patterns', 
                     'current_goals', 'personal_growth_areas', 'coping_strategies',
                     'significant_events', 'life_transitions']:
            if getattr(self, field) is None:
                setattr(self, field, [])
        
        if not self.last_updated:
            self.last_updated = datetime.now(timezone.utc).isoformat()

class PsychologicalUserInsightExtractor:
    """
    Psychologischer User-Insight-Extractor
    
    Funktionen:
    - Tiefe Persönlichkeitsanalyse während Care-Sessions
    - Historische Entwicklungsverfolgung 
    - Automatische Validierung neuer Erkenntnisse
    - Integration in WellbeingSessionManager (unified DB)
    """
    
    def __init__(self, session_manager=None, psychological_db=None, chat_function=None):
        """
        Initialisiert den Extractor
        
        Args:
            session_manager: WellbeingSessionManager Instanz (NEU - preferred)
            psychological_db: WellbeingDatabase Instanz (DEPRECATED - backward compatibility)
            chat_function: Funktion für LLM-Aufrufe
        """
        # REFACTORED: Nutze session_manager wenn verfügbar, sonst fallback zu psychological_db
        if session_manager:
            self.session_manager = session_manager
            self.db = self._resolve_db_from_session_manager(session_manager)
            logger.info("🔄 Using unified session_manager")
        elif psychological_db:
            self.db = psychological_db
            self.session_manager = None
            logger.warning("⚠️ Using deprecated psychological_db (consider migration)")
        else:
            raise ValueError("Either session_manager or psychological_db must be provided")
        
        self.chat_function = chat_function
        self.llm_available = chat_function is not None
        
        # Erstelle Tabelle für Insights falls DB verfügbar.
        if self.db:
            self._ensure_insights_table()
        
        logger.info("🧠💡 Psychologischer User-Insight-Extractor initialisiert")

    def _resolve_db_from_session_manager(self, session_manager):
        """Resolve WellbeingDatabase from adapter or direct session manager."""
        db = getattr(session_manager, 'db_manager', None)
        if db is not None:
            return db

        manager = getattr(session_manager, 'manager', None)
        if manager is not None:
            return getattr(manager, 'db', None)

        return getattr(session_manager, 'db', None)
    
    def _ensure_insights_table(self):
        """Ensure the canonical DB schema and extractor-specific hash migration."""
        if not self.db:
            logger.debug("⏭️ Überspringe Tabellenerstellung - verwende session_manager")
            return

        self.db._migrate_insight_schema()
        # Hash-Schema v2: temporal_context wird Teil des Hashs, damit
        # "Angst (current)" und "Angst (past)" nicht kollidieren.
        self._migrate_recompute_insight_hashes_v2()

    def _migrate_recompute_insight_hashes_v2(self) -> None:
        """Stellt sicher, dass alle Hashes der v2-Form (mit temporal_context)
        entsprechen. Idempotent über ``_schema_meta``-Sentinel.

        Bei Hash-Kollisionen (zwei Zeilen, die nach v2 denselben Hash hätten,
        z.B. weil sie sich nur in einer alten Schreibweise unterschieden)
        wird zur ältesten Zeile konsolidiert: confidence via Noisy-OR,
        mention_count summiert, Spät-Felder (last_*) bleiben aktuell.
        """
        if not self.db:
            return
        with self.db.get_connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS _schema_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT
                )
            """)
            already = conn.execute(
                "SELECT value FROM _schema_meta WHERE key = 'insight_hash_v2'"
            ).fetchone()
            if already:
                return

            rows = conn.execute("""
                SELECT id, user_id, insight_type, category, value, temporal_context,
                       insight_hash, confidence, mention_count, created_at,
                       first_session_id, last_seen_at
                FROM wellbeing_insights
                ORDER BY created_at ASC, id ASC
            """).fetchall()

            # Gruppiere nach neuem Hash; bei Kollision: konsolidiere.
            keepers: Dict[str, int] = {}  # new_hash → keeper id
            collapsed = 0
            rewritten = 0

            for row in rows:
                row_id = row[0]
                new_hash = self._compute_hash_v2(
                    user_id=row[1],
                    insight_type=row[2],
                    category=row[3],
                    value=row[4],
                    temporal_context=row[5],
                )
                if new_hash == row[6]:
                    keepers.setdefault(new_hash, row_id)
                    continue

                if new_hash in keepers:
                    keeper_id = keepers[new_hash]
                    # Noisy-OR der Confidence + Aufsummierung mention_count.
                    keeper = conn.execute(
                        "SELECT confidence, mention_count, last_seen_at "
                        "FROM wellbeing_insights WHERE id = ?",
                        (keeper_id,),
                    ).fetchone()
                    p_old = float(keeper[0] or 0.0)
                    p_new = float(row[7] or 0.0)
                    merged_conf = min(0.99, 1.0 - (1.0 - p_old) * (1.0 - p_new))
                    merged_count = int(keeper[1] or 1) + int(row[8] or 1)
                    last_seen = max(
                        keeper[2] or "",
                        row[11] or row[9] or "",
                    ) or None
                    conn.execute(
                        """
                        UPDATE wellbeing_insights
                        SET confidence = ?, mention_count = ?, last_seen_at = ?
                        WHERE id = ?
                        """,
                        (merged_conf, merged_count, last_seen, keeper_id),
                    )
                    conn.execute(
                        "DELETE FROM wellbeing_insights WHERE id = ?",
                        (row_id,),
                    )
                    collapsed += 1
                else:
                    conn.execute(
                        "UPDATE wellbeing_insights SET insight_hash = ? WHERE id = ?",
                        (new_hash, row_id),
                    )
                    keepers[new_hash] = row_id
                    rewritten += 1

            conn.execute(
                "INSERT OR REPLACE INTO _schema_meta VALUES ('insight_hash_v2', 'done')"
            )
            conn.commit()
            if rewritten or collapsed:
                logger.info(
                    "✅ insight_hash_v2 migration: rewritten=%d collapsed=%d",
                    rewritten, collapsed,
                )
    
    def extract_insights_from_session(self, user_id: str, session_id: str, 
                                    conversation_history: List[Dict[str, Any]]) -> List[PersonalityInsight]:
        """
        Extrahiert psychologische Einsichten aus einer Session
        
        Args:
            user_id: User-ID
            session_id: Session-ID
            conversation_history: Gesprächsverlauf [{"role": "user/assistant", "content": "..."}]
            
        Returns:
            Liste von PersonalityInsight Objekten
        """
        if not (self.chat_function and callable(self.chat_function)):
            logger.warning("⚠️ LLM nicht verfügbar - kann keine Einsichten extrahieren")
            return []
        
        logger.info(f"🧠🔍 Extrahiere psychologische Einsichten für {user_id} aus Session {session_id}")
        
        # Lade bisheriges Profil
        existing_profile = self.get_personality_profile(user_id)
        
        # Extrahiere User-Nachrichten
        user_messages = [msg['content'] for msg in conversation_history if msg.get('role') == 'user']
        if not user_messages:
            logger.warning("⚠️ Keine User-Nachrichten zum Analysieren")
            return []
        
        # LLM-basierte Analyse
        insights = self._analyze_with_psychological_llm(
            user_id, session_id, user_messages, existing_profile
        )
        
        # Speichere Einsichten in Datenbank
        if insights:
            self._store_insights(insights)
            logger.info(f"✅ {len(insights)} psychologische Einsichten extrahiert und gespeichert")
        
        return insights
    
    def _analyze_with_psychological_llm(self, user_id: str, session_id: str, 
                                      user_messages: List[str], 
                                      existing_profile: Optional[PersonalityProfile]) -> List[PersonalityInsight]:
        """LLM-basierte psychologische Analyse"""
        
        combined_text = '\n'.join(user_messages)
        
        # Erstelle Kontext aus bisherigem Profil
        profile_context = ""
        if existing_profile:
            profile_context = f"""
BISHERIGES PSYCHOLOGISCHES PROFIL:

Kern-Persönlichkeit:
- Traits: {', '.join(existing_profile.core_traits) if existing_profile.core_traits else 'noch unbekannt'}
- Persönlichkeitstyp: {existing_profile.personality_type or 'noch unbekannt'}
- Kommunikationsstil: {existing_profile.communication_style or 'noch unbekannt'}

Aktuelle Zustände:
- Stimmung: {existing_profile.current_mood or 'noch unbekannt'}
- Stress-Level: {existing_profile.stress_level or 'noch unbekannt'}
- Lebensphase: {existing_profile.life_phase or 'noch unbekannt'}
- Hauptsorgen: {', '.join(existing_profile.primary_concerns) if existing_profile.primary_concerns else 'noch unbekannt'}

Beziehungsmuster:
- Sozialstil: {existing_profile.social_style or 'noch unbekannt'}
- Bindungsstil: {existing_profile.attachment_style or 'noch unbekannt'}

Ziele & Entwicklung:
- Aktuelle Ziele: {', '.join(existing_profile.current_goals) if existing_profile.current_goals else 'noch unbekannt'}
- Wachstumsbereiche: {', '.join(existing_profile.personal_growth_areas) if existing_profile.personal_growth_areas else 'noch unbekannt'}

Sessions: {existing_profile.session_count}, Confidence: {existing_profile.confidence_score:.2f}

AUFGABE: Analysiere die neuen Äußerungen und validiere/erweitere das Profil.
"""
        
        psychological_prompt = f"""Du bist ein erfahrener Psychologe und Persönlichkeitsanalyst. Analysiere die folgenden Äußerungen einer Person tiefgreifend und extrahiere psychologische Einsichten.

{profile_context}

NEUE ÄUSSERUNGEN ZUR ANALYSE:
{combined_text}

ANWEISUNGEN:
1. Führe eine tiefe psychologische Analyse durch
2. Identifiziere Persönlichkeitstraits, emotionale Muster, Beziehungsdynamiken
3. Erkenne Lebensveränderungen, Entwicklungsphasen, Coping-Strategien
4. Validiere bisherige Erkenntnisse oder erkenne Widersprüche
5. Achte auf subtile psychologische Hinweise in der Sprache

Gib das Ergebnis als JSON zurück:
{{
    "analysis_summary": "Kurze Zusammenfassung der psychologischen Analyse",
    "insights": [
        {{
            "insight_type": "personality_trait|life_event|behavioral_pattern|emotional_state",
            "category": "core_personality|current_state|relationships|goals|fears|coping",
            "value": "Kurzer Wert/Name der Einsicht",
            "description": "Detaillierte psychologische Beschreibung",
            "confidence": 0.0-1.0,
            "evidence": ["Zitat/Beleg aus dem Text", "Weiterer Beleg"],
            "temporal_context": "current|past|future|developing"
        }}
    ],
    "profile_updates": {{
        "new_discoveries": ["Liste neuer Erkenntnisse"],
        "validated_aspects": ["Liste bestätigter bisheriger Einsichten"],
        "contradictions": ["Liste von Widersprüchen zu bisherigem Profil"],
        "developmental_changes": ["Liste erkannter Persönlichkeitsentwicklungen"]
    }},
    "care_notes": {{
        "emotional_state": "Beschreibung des aktuellen emotionalen Zustands",
        "stress_indicators": ["Erkannte Stress-Signale"],
        "growth_opportunities": ["Erkannte Wachstumschancen"],
        "recommended_focus": ["Empfohlene Care-Schwerpunkte"]
    }}
}}

Wichtig:
- Nur wissenschaftlich fundierte psychologische Einschätzungen
- Keine Diagnosen stellen
- Respektvoller und professioneller Umgang
- Bei Unsicherheit niedrigere Confidence angeben"""

        try:
            if not self.chat_function:
                logger.warning("⚠️ Chat-Funktion nicht verfügbar")
                return []
                
            response = self.chat_function(psychological_prompt)
            if not response:
                logger.warning("⚠️ LLM gab keine Antwort zurück")
                return []
            
            # Parse JSON Response
            json_start = response.find('{')
            json_end = response.rfind('}') + 1
            if json_start >= 0 and json_end > json_start:
                json_str = response[json_start:json_end]
                analysis_data = json.loads(json_str)
                
                # Log Analysis Summary
                summary = analysis_data.get('analysis_summary', '')
                if summary:
                    logger.info(f"🧠 Psychologische Analyse: {summary}")
                
                # Log Profile Updates
                updates = analysis_data.get('profile_updates', {})
                for key, items in updates.items():
                    if items:
                        logger.info(f"🔍 {key}: {', '.join(items)}")
                
                # Erstelle PersonalityInsight Objekte
                insights = []
                for insight_data in analysis_data.get('insights', []):
                    insight = PersonalityInsight(
                        user_id=user_id,
                        session_id=session_id,
                        insight_type=insight_data.get('insight_type', 'personality_trait'),
                        category=insight_data.get('category', 'core_personality'),
                        value=insight_data.get('value', ''),
                        description=insight_data.get('description', ''),
                        confidence=float(insight_data.get('confidence', 0.5)),
                        evidence=insight_data.get('evidence', []),
                        temporal_context=insight_data.get('temporal_context', 'current'),
                        created_at=datetime.now(timezone.utc).isoformat()
                    )
                    insights.append(insight)
                
                return insights
                
        except json.JSONDecodeError as e:
            logger.warning(f"⚠️ LLM Response ist kein valides JSON: {e}")
            return []
        except Exception as e:
            logger.error(f"❌ Psychologische Analyse fehlgeschlagen: {e}")
            return []
        
        return []
    
    def _store_insights(self, insights: List[PersonalityInsight]):
        """
        Speichert Einsichten in der Datenbank (unified session_manager oder alte DB).

        SOTA v2:
        - UPSERT auf ``insight_hash`` (UNIQUE).
        - Bei Wiederholung wird ``confidence`` via Noisy-OR aktualisiert
          (analog zu KG-Triples), ``mention_count`` inkrementiert,
          ``session_id``/``last_seen_at`` auf die *aktuelle* Session aktualisiert.
        - ``first_session_id`` / ``first_seen_at`` werden NIE überschrieben –
          die Session-Provenance bleibt damit über alle 17 Sessions erhalten.
        """
        if not self.db:
            logger.error("❌ Keine DB verfügbar zum Speichern der Einsichten")
            return

        if not insights:
            return

        with self.db.get_connection() as conn:
            for insight in insights:
                insight_hash = self._create_insight_hash(insight)
                insight_json = json.dumps(asdict(insight), ensure_ascii=False)
                encrypted_insight = self.db._encrypt_data(insight_json)
                now_iso = insight.created_at or datetime.now(timezone.utc).isoformat()

                conn.execute(
                    """
                    INSERT INTO wellbeing_insights (
                        user_id, session_id, insight_type, category, value,
                        encrypted_data, confidence, temporal_context, insight_hash,
                        created_at, mention_count, first_session_id,
                        first_seen_at, last_seen_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
                    ON CONFLICT(insight_hash) DO UPDATE SET
                        -- Noisy-OR der Confidence (monoton, gedeckelt bei 0.99)
                        -- Nur wenn Insight nicht vom User korrigiert/widerlegt wurde
                        confidence = MIN(
                            0.99,
                            1.0 - (1.0 - wellbeing_insights.confidence)
                                * (1.0 - excluded.confidence)
                        ),
                        mention_count = wellbeing_insights.mention_count + 1,
                        session_id     = excluded.session_id,
                        last_seen_at   = excluded.last_seen_at,
                        encrypted_data = excluded.encrypted_data
                    WHERE wellbeing_insights.correction_status != 'rejected'
                      AND wellbeing_insights.correction_status != 'superseded'
                    """,
                    (
                        insight.user_id,
                        insight.session_id,
                        insight.insight_type,
                        insight.category,
                        insight.value,
                        encrypted_insight,
                        insight.confidence,
                        insight.temporal_context,
                        insight_hash,
                        now_iso,
                        insight.session_id,  # first_session_id (nur bei INSERT)
                        now_iso,             # first_seen_at   (nur bei INSERT)
                        now_iso,             # last_seen_at
                    ),
                )
            conn.commit()
        logger.info(f"✅ {len(insights)} Insights upserted (Noisy-OR + mention_count)")

    @staticmethod
    def _compute_hash_v2(
        user_id: str,
        insight_type: str,
        category: str,
        value: str,
        temporal_context: str,
    ) -> str:
        """Hash v2: bezieht ``temporal_context`` ein, damit z.B.
        'Angst (current)' und 'Angst (past)' nicht kollidieren.
        Session-ID ist absichtlich NICHT enthalten – konzeptuelle Identität
        soll session-übergreifend dedupliziert werden.
        """
        import hashlib
        normalized_value = (value or "").strip().casefold()
        normalized_temp = (temporal_context or "current").strip().casefold()
        hash_string = (
            f"{user_id}:{insight_type}:{category}:{normalized_value}:{normalized_temp}"
        )
        return hashlib.sha256(hash_string.encode("utf-8")).hexdigest()

    def _create_insight_hash(self, insight: PersonalityInsight) -> str:
        """Erstellt v2-Hash für Insight (siehe ``_compute_hash_v2``)."""
        return self._compute_hash_v2(
            user_id=insight.user_id,
            insight_type=insight.insight_type,
            category=insight.category,
            value=insight.value,
            temporal_context=insight.temporal_context,
        )
    
    def get_personality_profile(self, user_id: str) -> Optional[PersonalityProfile]:
        """
        Lädt das vollständige Persönlichkeitsprofil eines Users
        REFACTORED: Unterstützt session_manager und alte DB
        """
        try:
            # REFACTORED: Nutze session_manager wenn verfügbar
            if self.session_manager:
                # Hole User Profile aus session_manager
                profile_data = self.session_manager.get_user_profile(user_id)
                
                if not profile_data:
                    logger.info(f"ℹ️ Kein User Profile für {user_id} gefunden")
                    return None
                
                # Konvertiere zu PersonalityProfile
                profile = PersonalityProfile(
                    user_id=user_id,
                    core_traits=profile_data.get('personality_traits', {}).get('core_traits', []),
                    personality_type=profile_data.get('personality_traits', {}).get('type'),
                    communication_style=profile_data.get('communication_style'),
                    current_goals=profile_data.get('care_goals', []),
                    coping_strategies=profile_data.get('preferences', {}).get('coping_strategies', []),
                    last_updated=profile_data.get('updated_at'),
                    confidence_score=0.8  # Default
                )
                
                logger.info(f"✅ Profile geladen für {user_id} aus unified DB")
                return profile
            
            # FALLBACK: Alte psychological_db
            if self.db:
                with self.db.get_connection() as conn:
                    # Lade alle Einsichten des Users
                    cursor = conn.execute("""
                        SELECT insight_type, category, value, encrypted_data, confidence, 
                               temporal_context, created_at
                        FROM wellbeing_insights 
                        WHERE user_id = ?
                        ORDER BY created_at DESC
                    """, (user_id,))
                    
                    insights_data = cursor.fetchall()
                    if not insights_data:
                        return None
                    
                    # Baue Profil aus Einsichten zusammen
                    profile = PersonalityProfile(user_id=user_id)
                    
                    for row in insights_data:
                        try:
                            # Entschlüssele Insight-Daten
                            decrypted_data = self.db._decrypt_data(row[3])
                            insight_dict = json.loads(decrypted_data)
                            
                            # Kategorisiere und füge zu Profil hinzu
                            self._integrate_insight_into_profile(profile, insight_dict)
                            
                        except Exception as e:
                            logger.warning(f"⚠️ Fehler beim Verarbeiten einer Einsicht: {e}")
                            continue
                    
                    # Berechne Metadaten
                    session_ids = set()
                    for row in insights_data:
                        try:
                            decrypted_data = self.db._decrypt_data(row[3])
                            insight_dict = json.loads(decrypted_data)
                            session_ids.add(insight_dict.get('session_id', ''))
                        except Exception as exc:
                            logger.debug(f"Skipping malformed insight during profile metadata pass: {exc}")
                            continue
                    
                    profile.session_count = len(session_ids)
                    profile.confidence_score = self._calculate_profile_confidence(insights_data)
                    profile.last_updated = max(row[6] for row in insights_data)
                    
                    return profile
            
            # If neither session_manager nor db is available
            logger.warning(f"⚠️ Keine Datenquelle verfügbar für get_personality_profile")
            return None
                
        except Exception as e:
            logger.error(f"❌ Fehler beim Laden des Persönlichkeitsprofils: {e}")
            return None
    
    def _integrate_insight_into_profile(self, profile: PersonalityProfile, insight_dict: Dict[str, Any]):
        """Integriert eine Einsicht in das Persönlichkeitsprofil"""
        category = insight_dict.get('category', '')
        value = insight_dict.get('value', '')
        confidence = insight_dict.get('confidence', 0.5)
        
        # Nur hochqualitative Einsichten integrieren
        if confidence < 0.6:
            return
        
        if category == 'core_personality':
            if insight_dict.get('insight_type') == 'personality_trait':
                if profile.core_traits is None:
                    profile.core_traits = []
                if value not in profile.core_traits:
                    profile.core_traits.append(value)
        
        elif category == 'current_state':
            if 'mood' in value.lower():
                profile.current_mood = value
            elif 'stress' in value.lower():
                profile.stress_level = value
            elif 'phase' in value.lower() or 'transition' in value.lower():
                profile.life_phase = value
        
        elif category == 'relationships':
            if 'social' in value.lower():
                profile.social_style = value
            elif 'attachment' in value.lower() or 'binding' in value.lower():
                profile.attachment_style = value
            else:
                if profile.relationship_patterns is None:
                    profile.relationship_patterns = []
                if value not in profile.relationship_patterns:
                    profile.relationship_patterns.append(value)
        
        elif category == 'goals':
            if profile.current_goals is None:
                profile.current_goals = []
            if value not in profile.current_goals:
                profile.current_goals.append(value)
        
        elif category == 'coping':
            if profile.coping_strategies is None:
                profile.coping_strategies = []
            if value not in profile.coping_strategies:
                profile.coping_strategies.append(value)
    
    def _calculate_profile_confidence(self, insights_data: List) -> float:
        """Berechnet Gesamt-Confidence des Profils"""
        if not insights_data:
            return 0.0
        
        confidences = [row[4] for row in insights_data if len(row) > 4]
        return round(sum(confidences) / len(confidences), 2) if confidences else 0.0
    
    def update_personality_profile(self, user_id: str, session_id: str) -> bool:
        """Aktualisiert das Persönlichkeitsprofil nach einer Session"""
        try:
            if self.db is None:
                logger.error("❌ Keine DB-Verbindung verfügbar")
                return False
            
            # Lade aktuelle Session-Historie
            with self.db.get_connection() as conn:
                cursor = conn.execute("""
                    SELECT role, content FROM session_interactions 
                    WHERE session_id = ?
                    ORDER BY timestamp ASC
                """, (session_id,))
                
                interactions = cursor.fetchall()
                conversation_history = [
                    {"role": row[0], "content": self.db._decrypt_data(row[1])}
                    for row in interactions
                ]
            
            # Extrahiere neue Einsichten
            insights = self.extract_insights_from_session(user_id, session_id, conversation_history)
            
            logger.info(f"🧠✅ Persönlichkeitsprofil aktualisiert für {user_id}")
            return len(insights) > 0
            
        except Exception as e:
            logger.error(f"❌ Fehler beim Aktualisieren des Profils: {e}")
            return False
    
    # ------------------------------------------------------------------
    # Korrektur-Workflow (P1: Persistente Nutzerkorrektur)
    # ------------------------------------------------------------------

    def reject_insight(
        self,
        insight_id: int,
        user_id: str,
        reason: Optional[str] = None,
        corrected_by: str = "user",
    ) -> bool:
        """Markiert einen Insight als vom Nutzer abgelehnt.

        Args:
            insight_id: ID des Insight
            user_id: Canonical User-ID (Ownership-Prüfung)
            reason: Optionaler Grund
            corrected_by: 'user' | 'system' | 'therapist'

        Returns:
            True bei Erfolg
        """
        if self.db is None:
            return False
        return self.db.correct_user_insight(
            insight_id,
            user_id,
            "rejected",
            corrected_by=corrected_by,
            reason=reason,
        )

    def supersede_insight(
        self,
        insight_id: int,
        user_id: str,
        new_insight_id: Optional[int] = None,
        reason: Optional[str] = None,
        corrected_by: str = "system",
    ) -> bool:
        """Markiert einen Insight als durch neuen Insight ersetzt.

        Args:
            insight_id: ID des alten Insight
            user_id: Canonical User-ID
            new_insight_id: Optional ID des ersetzenden Insight
            reason: Optionaler Grund
            corrected_by: 'user' | 'system' | 'therapist'

        Returns:
            True bei Erfolg
        """
        if self.db is None:
            return False
        return self.db.correct_user_insight(
            insight_id,
            user_id,
            "superseded",
            corrected_by=corrected_by,
            reason=reason,
            replacement_insight_id=new_insight_id,
        )

    def reactivate_insight(
        self,
        insight_id: int,
        user_id: str,
        reason: Optional[str] = None,
        corrected_by: str = "user",
    ) -> bool:
        """Reactivate a rejected insight after an explicit human correction."""
        if self.db is None:
            return False
        return self.db.correct_user_insight(
            insight_id,
            user_id,
            "active",
            corrected_by=corrected_by,
            reason=reason,
        )

    def get_user_insights_summary(self, user_id: str, limit: int = 10) -> Dict[str, Any]:
        """Gibt eine Zusammenfassung der User-Einsichten zurück"""
        try:
            profile = self.get_personality_profile(user_id)
            if not profile:
                return {"status": "no_profile", "insights": []}
            
            if self.db is None:
                logger.error("❌ Keine DB-Verbindung verfügbar")
                return {"status": "no_db", "insights": []}

            lookup_user_id = user_id
            if self.session_manager and hasattr(self.session_manager, 'resolve_user_id'):
                lookup_user_id = self.session_manager.resolve_user_id(user_id)

            recent_insights = [
                {
                    "type": insight.get("insight_type"),
                    "category": insight.get("category"),
                    "value": insight.get("value"),
                    "confidence": insight.get("confidence"),
                    "date": str(insight.get("created_at") or "")[:10],
                }
                for insight in self.db.get_user_insights(user_id=lookup_user_id, limit=limit)
            ]
            
            return {
                "status": "success",
                "profile_summary": {
                    "core_traits": (profile.core_traits or [])[:5],  # Top 5
                    "current_mood": profile.current_mood,
                    "primary_concerns": (profile.primary_concerns or [])[:3],  # Top 3
                    "confidence_score": profile.confidence_score,
                    "session_count": profile.session_count
                },
                "recent_insights": recent_insights
            }
            
        except Exception as e:
            logger.error(f"❌ Fehler beim Erstellen der Einsichten-Zusammenfassung: {e}")
            return {"status": "error", "error": str(e)}


# Factory-Funktion für Integration
def create_psychological_user_extractor(psychological_db=None, session_manager=None, chat_function=None):
    """
    Erstellt PsychologicalUserInsightExtractor mit gegebenen Abhängigkeiten
    
    Args:
        psychological_db: DEPRECATED - alte psychologische Datenbank
        session_manager: EMPFOHLEN - neue unified session manager
        chat_function: Optional LLM-Chat-Funktion
    """
    return PsychologicalUserInsightExtractor(
        psychological_db=psychological_db,
        session_manager=session_manager,
        chat_function=chat_function
    )
