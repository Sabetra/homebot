"""
Session-Manager für psychologische Unterstützung
===============================================
Verwaltet Benutzer-Sessions mit intelligenter Kontext-Führung,
automatischer Zusammenfassung und nahtloser Integration.
"""

import logging
import time
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Any, Tuple, Callable
from dataclasses import dataclass
import json
import hashlib

from .wellbeing_db import WellbeingDatabase
from .privacy_handler import PrivacyHandler

# NEU: Import der Manager für Goals und Mood Progression
from .mood_progression_tracker import MoodProgressionTracker

# SOTA: Treatment Plan Domain (Single Source of Truth)
from .care_plans import CarePlanManager

# NEU: User-ID-Konfiguration importieren
try:
    import sys
    import os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from config.user_id_config import get_current_user_id
    AUTO_USER_ID = True
except ImportError:
    AUTO_USER_ID = False
    def get_current_user_id() -> str:
        """Fallback wenn config nicht verfügbar"""
        return "default_user"

# Logger für Session-Management
logger = logging.getLogger(__name__)


def normalize_datetime(dt: Any) -> datetime:
    """
    Normalisiert ein Datetime-Objekt zu timezone-aware UTC.
    
    Args:
        dt: Datetime-Objekt oder String
        
    Returns:
        Timezone-aware datetime in UTC
    """
    if dt is None:
        return datetime.now(timezone.utc)
    
    # String zu datetime konvertieren
    result: datetime
    if isinstance(dt, str):
        # Handle ISO format with 'Z' or timezone offset
        dt_str = dt.replace('Z', '+00:00')
        try:
            result = datetime.fromisoformat(dt_str)
        except ValueError:
            # Fallback: try basic parsing
            try:
                result = datetime.strptime(dt_str, '%Y-%m-%d %H:%M:%S.%f')
            except ValueError:
                result = datetime.strptime(dt_str, '%Y-%m-%d %H:%M:%S')
    else:
        # At this point, dt must be datetime (not str)
        assert isinstance(dt, datetime), f"Expected datetime, got {type(dt)}"
        result = dt
    
    # Wenn offset-naive, dann als UTC interpretieren
    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)
    # Wenn andere Timezone, zu UTC konvertieren
    elif result.tzinfo != timezone.utc:
        result = result.astimezone(timezone.utc)
    
    return result


@dataclass
class SessionContext:
    """Container für Session-Kontext-Informationen"""
    session_id: str
    user_id: str
    start_time: datetime
    last_interaction: datetime
    interaction_count: int
    user_turn_count: int = 0
    summary: Optional[str] = None
    mood_trend: Optional[str] = None
    care_notes: Optional[List[str]] = None
    
    def __post_init__(self) -> None:
        if self.care_notes is None:
            self.care_notes = []

class WellbeingSessionManager:
    """
    Verwaltet psychologische Sessions mit:
    - Automatischer Session-Erstellung und -Wiederherstellung
    - Kontext-Tracking und Stimmungsanalyse
    - Integration mit Privacy-Handler
    - Performance-optimierte Datenbankoperationen
    """
    
    def __init__(self, db: Optional[WellbeingDatabase] = None, 
                 privacy_handler: Optional[PrivacyHandler] = None,
                 auto_summarize_threshold: int = 20,
                 model_loader: Any = None) -> None:
        """
        Initialisiert Session-Manager
        
        Args:
            db: Datenbank-Instanz (wird erstellt wenn None)
            privacy_handler: Privacy-Handler (wird erstellt wenn None)
            auto_summarize_threshold: Anzahl Interaktionen für automatische Zusammenfassung
            model_loader: ModelLoader-Instanz für LLM-basierte Summaries (REQUIRED für ContextSummarizer!)
        """
        # ✅ WICHTIG: Speichere ModelLoader als Instanzvariable!
        self.model_loader = model_loader
        
        self.db = db or WellbeingDatabase(model_loader=model_loader)
        self.privacy_handler = privacy_handler or PrivacyHandler()
        self.auto_summarize_threshold = auto_summarize_threshold
        
        # Active Sessions Cache für Performance
        self._active_sessions: Dict[str, SessionContext] = {}
        self._session_locks: Dict[str, bool] = {}
        self._last_treatment_results: Dict[str, Any] = {}
        
        # NEU: Auto-Summarization Cooldown (verhindert Endlosschleifen)
        self._last_auto_summarization: Dict[str, datetime] = {}
        self._auto_summarization_cooldown = timedelta(minutes=5)  # 5 Minuten Cooldown
        
        # NEU: Backfill Cooldown (verhindert mehrfache Ausführung pro User)
        self._last_backfill: Dict[str, datetime] = {}
        self._backfill_cooldown = timedelta(hours=1)  # 1 Stunde Cooldown pro User
        
        # NEU: ContextSummarizer (100% LLM-basiert, ModelLoader REQUIRED!)
        self.context_summarizer = None
        try:
            from wellbeing.context_summarizer import ContextSummarizer
            
            # ✅ KRITISCH: ModelLoader muss vorhanden sein!
            if not hasattr(self, 'model_loader') or self.model_loader is None:
                logger.warning("⚠️ ModelLoader nicht verfügbar - ContextSummarizer wird NICHT initialisiert!")
                logger.warning("⚠️ Session-Summaries werden NICHT generiert bis ModelLoader verfügbar ist!")
            else:
                self.context_summarizer = ContextSummarizer(self.db, model_loader=self.model_loader)
                logger.info("✅ ContextSummarizer erfolgreich geladen (100% LLM-basiert)")
        except ValueError as e:
            logger.error(f"❌ ContextSummarizer benötigt ModelLoader: {e}")
            logger.error("❌ Session-Summaries NICHT verfügbar!")
        except Exception as e:
            logger.error(f"❌ ContextSummarizer Fehler: {e}")
        
        # Care goals are now exclusively managed by ``CarePlanManager``
        # below — the legacy ``CareGoalManager`` has been removed.

        # NEU: Mood Progression Tracker
        self.mood_tracker = MoodProgressionTracker(self.db)
        logger.info("✅ MoodProgressionTracker initialisiert")

        # SOTA: Treatment Plan Manager — canonical Source of Truth across sessions.
        # Wires goals (with embedding+LLM matching), case formulation (5P),
        # stage-of-change (TTM), per-turn risk assessment, MBC, session focus,
        # and self-supervision review.
        self.treatment_manager = CarePlanManager(self.db, llm_function=None)
        logger.info("✅ CarePlanManager initialisiert (SOTA Treatment Plan Domain)")
        
        logger.info("✓ WellbeingSessionManager mit Auto-Summarization-Cooldown initialisiert")

    def resolve_user_id(self, user_id: str) -> str:
        """Resolve an external user identifier to the canonical stored user id."""
        if not user_id or not str(user_id).strip():
            raise ValueError("user_id must not be empty")
        return self.privacy_handler.anonymize_user_id(str(user_id).strip())
    
    def create_or_restore_session(self, user_id: str, restore_if_recent: bool = True) -> str:
        """
        Erstellt neue Session oder stellt aktuelle wieder her
        
        Args:
            user_id: Eindeutige Benutzer-ID
            restore_if_recent: Stelle letzte Session wieder her wenn < 24h alt
            
        Returns:
            Session-ID
        """
        try:
            # Anonymisiere User-ID falls nötig
            user_id = self.resolve_user_id(user_id)
            
            # Versuche aktuelle Session zu finden
            if restore_if_recent:
                recent_session = self._find_recent_session(user_id)
                if recent_session:
                    logger.info(f"✓ Session wiederhergestellt: {recent_session}")
                    
                    # 🔥 NEU: Backfill auch beim Wiederherstellen (mit Cooldown!)
                    # Stellt sicher, dass previous_sessions vollständig sind
                    self._run_backfill_with_cooldown(
                        user_id,
                        context="SESSION-RESTORE",
                        run_summary=True,
                        run_treatment=False,
                    )
                    
                    return recent_session
            
            # Erstelle neue Session
            session_id = self.db.create_session(user_id)
            
            # Initialisiere Session-Kontext
            context = SessionContext(
                session_id=session_id,
                user_id=user_id,
                start_time=datetime.now(timezone.utc),
                last_interaction=datetime.now(timezone.utc),
                interaction_count=0,
                user_turn_count=0,
            )
            
            self._active_sessions[session_id] = context
            logger.info(f"✓ Neue Session erstellt: {session_id} für User: {user_id}")
            
            # 🔥 NEU: Backfill fehlende Summaries für alte Sessions (mit Cooldown!)
            # Dies stellt sicher, dass previous_sessions immer vollständig sind
            self._run_backfill_with_cooldown(
                user_id,
                context="SESSION-START",
                run_summary=True,
                run_treatment=False,
            )
            
            return session_id
            
        except Exception as e:
            logger.error(f"❌ Session-Erstellung fehlgeschlagen: {e}")
            raise
    
    def _find_recent_session(self, user_id: str, hours_threshold: int = 24) -> Optional[str]:
        """
        Findet aktuelle Session wenn sie kürzlich aktiv war
        
        Args:
            user_id: Benutzer-ID
            hours_threshold: Stunden-Schwellwert für "kürzlich"
            
        Returns:
            Session-ID oder None
        """
        try:
            sessions = self.db.get_user_sessions(user_id, status='active')
            
            if not sessions:
                return None
            
            # Finde neueste Session
            latest_session: Dict[str, Any] = max(sessions, key=lambda s: s['updated_at'])
            
            # Prüfe ob kürzlich aktiv - FIXED: Verwende normalize_datetime
            updated_time = normalize_datetime(latest_session['updated_at'])
            current_time = datetime.now(timezone.utc)
            time_diff = current_time - updated_time
            
            if time_diff.total_seconds() / 3600 <= hours_threshold:
                # Lade Session-Kontext
                self._load_session_context(latest_session['id'])
                session_id_raw = latest_session.get('id')
                # Ensure we return str | None as declared
                return str(session_id_raw) if session_id_raw is not None else None
            
            return None
            
        except Exception as e:
            logger.error(f"❌ Suche nach aktueller Session fehlgeschlagen: {e}")
            return None
    
    def load_specific_session(self, session_id: str) -> bool:
        """
        Lädt eine spezifische Session in den aktiven Kontext
        
        Args:
            session_id: Session-ID die geladen werden soll
            
        Returns:
            True wenn erfolgreich geladen
        """
        try:
            # Prüfe ob Session existiert
            target_session = self.db.get_session_record(session_id)
            
            if not target_session:
                logger.warning(f"⚠️ Session nicht gefunden: {session_id}")
                return False
            
            # Lade Session-Kontext
            self._load_session_context(session_id)
            
            # Setze als aktuelle Session
            self._current_session_id = session_id
            
            logger.info(f"✅ Session erfolgreich geladen: {session_id}")
            return True
            
        except Exception as e:
            logger.error(f"❌ Session-Laden fehlgeschlagen: {e}")
            return False

    def get_session_list(self, user_id: str = "default_user", include_closed: bool = True) -> List[Dict[str, Any]]:
        """
        Holt Liste aller Sessions eines Benutzers
        
        Args:
            user_id: Benutzer-ID
            include_closed: Auch geschlossene Sessions einschließen
            
        Returns:
            Liste von Sessions mit Metadaten
        """
        try:
            user_id = self.resolve_user_id(user_id)

            # Lade Sessions aus Datenbank
            if include_closed:
                sessions = self.db.get_user_sessions(user_id)
            else:
                sessions = self.db.get_user_sessions(user_id, status='active')
            
            # Ergänze um Interaktions-Anzahl
            for session in sessions:
                try:
                    # FIXED: Use get_session_interactions instead of get_session_history
                    interactions = self.db.get_session_interactions(session['id'])
                    session['interaction_count'] = len(interactions)
                    session['user_message_count'] = len([i for i in interactions if i.get('role') == 'user'])
                except Exception:
                    session['interaction_count'] = 0
                    session['user_message_count'] = 0
            
            logger.info(f"📋 {len(sessions)} Sessions gefunden für User: {user_id}")
            return sessions
            
        except Exception as e:
            logger.error(f"❌ Session-Liste laden fehlgeschlagen: {e}")
            return []

    def get_current_session_id(self) -> Optional[str]:
        """
        Gibt die ID der aktuell aktiven Session zurück
        
        Returns:
            Session-ID oder None
        """
        return getattr(self, '_current_session_id', None)

    def switch_to_session(self, session_id: str) -> bool:
        """
        Wechselt zur angegebenen Session
        
        Args:
            session_id: Ziel-Session-ID
            
        Returns:
            True wenn erfolgreich gewechselt
        """
        try:
            # Speichere aktuelle Session wenn vorhanden
            current_session_id = self.get_current_session_id()
            if current_session_id and current_session_id in self._active_sessions:
                self._save_session_state(current_session_id)
            
            # Lade neue Session
            success = self.load_specific_session(session_id)
            
            if success:
                logger.info(f"🔄 Zu Session gewechselt: {session_id}")
                return True
            else:
                logger.warning(f"⚠️ Session-Wechsel fehlgeschlagen: {session_id}")
                return False
                
        except Exception as e:
            logger.error(f"❌ Session-Wechsel-Fehler: {e}")
            return False

    def _save_session_state(self, session_id: str) -> None:
        """
        Speichert den aktuellen Zustand einer Session
        
        Args:
            session_id: Session-ID
        """
        try:
            if session_id in self._active_sessions:
                context = self._active_sessions[session_id]
                
                # Aktualisiere Datenbank mit letzter Interaktion
                # (Implementierung kann erweitert werden)
                logger.debug(f"💾 Session-Zustand gespeichert: {session_id}")
                
        except Exception as e:
            logger.warning(f"⚠️ Session-Zustand speichern fehlgeschlagen: {e}")

    def _load_session_context(self, session_id: str) -> None:
        """
        Lädt den Kontext einer Session in den Cache
        
        Args:
            session_id: Session-ID
        """
        try:
            # DIREKT aus der Datenbank laden - NICHT über get_session_details (verhindert Rekursion!)
            # Verwende direkte SQL-Abfrage um Rekursion zu vermeiden
            with self.db.get_connection() as conn:
                cursor = conn.execute("""
                    SELECT id, user_id, start_time, end_time, session_summary, 
                           mood_progression, care_goals, created_at, updated_at
                    FROM wellbeing_sessions 
                    WHERE id = ?
                """, (session_id,))
                
                row = cursor.fetchone()
                if not row:
                    logger.debug(f"⚠️ Session nicht gefunden: {session_id}")
                    return
                
                session_data = dict(row)
                
                # Hole Interaction Count
                count_cursor = conn.execute(
                    "SELECT COUNT(*) FROM session_interactions WHERE session_id = ?",
                    (session_id,)
                )
                interaction_count = count_cursor.fetchone()[0]

                user_count_cursor = conn.execute(
                    "SELECT COUNT(*) FROM session_interactions WHERE session_id = ? AND role = 'user'",
                    (session_id,),
                )
                user_turn_count = user_count_cursor.fetchone()[0]
            
            # Erstelle Session-Kontext - FIXED: Verwende normalize_datetime
            context = SessionContext(
                session_id=session_id,
                user_id=session_data['user_id'],
                start_time=normalize_datetime(session_data.get('created_at')),
                last_interaction=normalize_datetime(session_data.get('updated_at')),
                interaction_count=interaction_count,
                user_turn_count=user_turn_count,
                summary=session_data.get('session_summary'),
                mood_trend=session_data.get('mood_progression'),
                care_notes=[]  # Notes werden separat geladen wenn nötig
            )
            
            # 🔄 AUTO-REGENERIERUNG: Prüfe ob Summary regelbasiert ist und regeneriere mit LLM
            current_summary = session_data.get('session_summary')
            if current_summary and self._is_rule_based_summary(current_summary):
                logger.info(f"🔄 Regelbasierte Summary erkannt - starte Auto-Regenerierung...")
                new_summary = self._auto_regenerate_summary_if_needed(session_id, current_summary)
                if new_summary:
                    context.summary = new_summary  # Update Cache mit neuer Summary
            
            # In Cache laden
            self._active_sessions[session_id] = context
            logger.debug(f"📥 Session-Kontext geladen: {session_id}")
                
        except Exception as e:
            logger.warning(f"⚠️ Session-Kontext laden fehlgeschlagen: {e}")

    def add_interaction(self, session_id: str, role: str, content: str, 
                       mood_analysis: Optional[Dict[str, Any]] = None) -> bool:
        """
        Fügt Interaktion zur Session hinzu
        
        Args:
            session_id: Session-ID
            role: 'user' oder 'assistant'
            content: Nachrichteninhalt
            mood_analysis: Optionale Stimmungsanalyse
            
        Returns:
            True wenn erfolgreich
        """
        try:
            # DEBUG-LOG: Einstieg
            logger.info(f"📝 SESSION-MGR: add_interaction aufgerufen")
            logger.info(f"📝 SESSION-MGR: Session {session_id[:8]}..., Role: {role}, Content: {len(content)} Zeichen")
            
            # Verhindere doppelte Bearbeitung
            if self._session_locks.get(session_id, False):
                logger.warning(f"⚠ Session {session_id} bereits in Bearbeitung")
                return False
                
            self._session_locks[session_id] = True
            
            # Anonymisiere/bereinige Content
            cleaned_content = self.privacy_handler.clean_content(content)
            logger.debug(f"📝 SESSION-MGR: Content bereinigt: {len(cleaned_content)} Zeichen")
            
            # Konvertiere Mood-Analysis zu JSON
            mood_json = json.dumps(mood_analysis) if mood_analysis else None
            logger.debug(f"📝 SESSION-MGR: Mood-JSON: {bool(mood_json)}")
            
            # Speichere in Datenbank
            logger.info(f"📝 SESSION-MGR: Rufe db.save_interaction auf...")
            interaction_id = self.db.save_interaction(
                session_id=session_id,
                role=role,
                content=cleaned_content,
                mood_indicators=mood_json
            )
            
            if interaction_id is None:
                logger.error(f"❌❌ SESSION-MGR: DB-Speicherung fehlgeschlagen (save_interaction gab None zurück)!")
                return False
            else:
                logger.info(f"✅ SESSION-MGR: DB-Speicherung erfolgreich (Interaktion-ID: {interaction_id})")
            
            # ✅ Periodisches Cleanup: Alle 10 Interaktionen
            if interaction_id and interaction_id % 10 == 0:
                self._cleanup_old_cooldowns()
            
            # Update Session-Kontext
            if session_id in self._active_sessions:
                context = self._active_sessions[session_id]
                context.last_interaction = datetime.now(timezone.utc)
                context.interaction_count += 1
                if role == 'user':
                    context.user_turn_count += 1
                logger.debug(f"📝 SESSION-MGR: Session-Context aktualisiert, Count: {context.interaction_count}")
                
                # Füge Mood-Trend hinzu
                if role == 'user' and mood_analysis:
                    context.mood_trend = mood_analysis.get('overall_mood', 'neutral')
            else:
                logger.warning(f"⚠ SESSION-MGR: Session {session_id[:8]}... nicht im aktiven Cache!")
            
            # SOTA: Run the canonical treatment pipeline (risk + goals + progress
            # + formulation + stage + focus). Only on user turns. All components
            # fail soft — exceptions are caught inside the pipeline.
            if role == 'user':
                self._last_treatment_results.pop(session_id, None)
                treatment_result = self._run_treatment_pipeline(
                    session_id=session_id,
                    user_message=cleaned_content,
                    mood_analysis=mood_analysis,
                )
                if treatment_result is not None:
                    self._last_treatment_results[session_id] = treatment_result
            
            # NEU: Update Mood Progression (intelligent, throttled)
            if role == 'user':
                self._update_mood_progression_if_needed(session_id)
            
            # Prüfe ob automatische Zusammenfassung nötig
            if self._should_auto_summarize(session_id):
                logger.info(f"📝 SESSION-MGR: Auto-Summarization triggered")
                self._trigger_auto_summarization(session_id)
            
            logger.info(f"✅✅ SESSION-MGR ERFOLG: Interaktion {interaction_id} komplett verarbeitet")
            return True
            
        except Exception as e:
            logger.error(f"❌❌ SESSION-MGR FEHLER: Interaktion-Hinzufügung fehlgeschlagen: {e}")
            import traceback
            logger.error(f"Traceback: {traceback.format_exc()}")
            return False
            
        finally:
            self._session_locks[session_id] = False
    
    # Alias für Kompatibilität mit wellbeing_session_interface.py
    def add_message(self, session_id: str, role: str, content: str, 
                   emotional_markers: Optional[List[str]] = None) -> bool:
        """
        Alias für add_interaction - Kompatibilität mit Interface
        
        Args:
            session_id: Session-ID
            role: 'user' oder 'assistant'
            content: Nachrichteninhalt
            emotional_markers: Liste emotionaler Marker (kann leer sein, nie None)
            
        Returns:
            True wenn erfolgreich
        """
        if role != 'user':
            mood_analysis = None
        elif emotional_markers and len(emotional_markers) > 0:
            # Normale emotional_markers vorhanden
            mood_analysis = {
                'emotional_markers': emotional_markers,
                'overall_mood': emotional_markers[0]
            }
        else:
            # ✅ Fallback: Strukturiertes neutrales Mood statt None
            mood_analysis = {
                'emotional_markers': [],
                'overall_mood': 'neutral'
            }
            logger.debug("📝 Keine User-Emotionsmarker → neutraler User-Mood")
        
        return self.add_interaction(session_id, role, content, mood_analysis)
    
    def _should_auto_summarize(self, session_id: str) -> bool:
        """
        Prüft ob automatische Zusammenfassung ausgelöst werden soll
        (mit Cooldown-Protection gegen Endlosschleifen)
        
        Args:
            session_id: Session-ID
            
        Returns:
            True wenn Zusammenfassung nötig
        """
        try:
            context = self._active_sessions.get(session_id)
            if not context:
                return False
            
            # COOLDOWN-CHECK: Prüfe ob letzte Auto-Summarization zu kürzlich war
            last_summary = self._last_auto_summarization.get(session_id)
            if last_summary:
                time_since_last = datetime.now(timezone.utc) - last_summary
                if time_since_last < self._auto_summarization_cooldown:
                    logger.debug(f"🔄 Auto-Summarization Cooldown aktiv für {session_id} (noch {self._auto_summarization_cooldown - time_since_last})")
                    return False
            
            # Schwellwert erreicht?
            if context.interaction_count >= self.auto_summarize_threshold:
                return True
            
            # Zeit-basierte Zusammenfassung (jede Stunde bei aktiver Session)
            time_since_start = datetime.now(timezone.utc) - context.start_time
            if time_since_start.total_seconds() > 3600 and context.interaction_count >= 5:
                return True
            
            return False
            
        except Exception as e:
            logger.error(f"❌ Auto-Summarize-Prüfung fehlgeschlagen: {e}")
            return False
    
    def _trigger_auto_summarization(self, session_id: str) -> None:
        """
        Löst automatische Zusammenfassung aus (mit Cooldown-Tracking)
        
        Args:
            session_id: Session-ID
        """
        try:
            # Setze Cooldown-Timestamp SOFORT (verhindert mehrfache Ausführung)
            self._last_auto_summarization[session_id] = datetime.now(timezone.utc)
            
            logger.info(f"🔄 Auto-Zusammenfassung für Session: {session_id}")
            
            # ✅ FIXED: Rufe update_session_summary() auf statt Placeholder!
            summary = self.update_session_summary(session_id, force=False)
            
            if summary:
                logger.info(f"✅ Auto-Summary generiert: {len(summary)} Zeichen")
            else:
                logger.debug("📊 Auto-Summary übersprungen (Throttling)")
            
        except Exception as e:
            logger.error(f"❌ Auto-Zusammenfassung fehlgeschlagen: {e}")
            # Bei Fehler, Cooldown zurücksetzen
            if session_id in self._last_auto_summarization:
                del self._last_auto_summarization[session_id]
    
    def _run_treatment_pipeline(
        self,
        session_id: str,
        user_message: str,
        mood_analysis: Optional[Dict[str, Any]] = None,
    ) -> Optional[Any]:
        """Run the SOTA CarePlanManager pipeline for the current user turn.

        Always fails soft: any exception is logged but never raised — the
        legacy code paths must keep working even if the new pipeline fails.
        """
        try:
            ctx = self._active_sessions.get(session_id)
            if ctx is None:
                return None
            user_id = getattr(ctx, 'user_id', None)
            if not user_id:
                return None

            # Recent interactions for the LLM components.
            # ``get_session_interactions`` returns rows with columns
            # (role, content, ...). Each row is already one message — we
            # forward role+content directly. The DB query is session-scoped,
            # so cross-session/user contamination is impossible.
            recent = self.db.get_session_interactions(session_id) or []
            recent = recent[-20:]
            recent_payload: List[Dict[str, Any]] = []
            for it in recent:
                role = it.get('role')
                content = it.get('content')
                if role and content:
                    recent_payload.append(
                        {'role': str(role), 'content': str(content)}
                    )

            mood_summary = '—'
            if mood_analysis:
                tone = mood_analysis.get('overall_mood') or mood_analysis.get('emotional_tone')
                if tone:
                    mood_summary = str(tone)

            return self.treatment_manager.process_turn(
                user_id=user_id,
                session_id=session_id,
                turn_idx=ctx.user_turn_count,
                user_message=user_message,
                recent_interactions=recent_payload,
                mood_summary=mood_summary,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"⚠️ Treatment pipeline failed (non-fatal): {exc}")
            return None

    def get_last_treatment_result(self, session_id: str) -> Optional[Any]:
        """Return the structured result of the latest persisted user turn."""
        return self._last_treatment_results.get(session_id)

    def _update_mood_progression_if_needed(self, session_id: str) -> None:
        """
        Updated Mood Progression (intelligent, mit Throttling)
        
        Args:
            session_id: Session-ID
        """
        try:
            result = self.mood_tracker.update_mood_progression(session_id, force=False)
            
            if result:
                logger.info(f"📊 Mood Update: {result['overall_trend']} (Conf: {result['confidence']:.2f})")
            else:
                logger.debug("Mood Update übersprungen (throttled oder zu wenig Daten)")
                
        except Exception as e:
            logger.error(f"❌ Mood Progression Update fehlgeschlagen: {e}", exc_info=True)
    
    def set_llm_function(self, llm_function: Callable[..., Any]) -> None:
        """Route the LLM function into all treatment-domain components."""
        # SOTA: route the same LLM into all treatment-domain components.
        self.treatment_manager.llm_function = llm_function
        self.treatment_manager.matcher.llm_function = llm_function
        self.treatment_manager.extractor.llm_function = llm_function
        self.treatment_manager.progress.llm_function = llm_function
        self.treatment_manager.risk.llm_function = llm_function
        self.treatment_manager.formulator.llm_function = llm_function
        self.treatment_manager.stage.llm_function = llm_function
        self.treatment_manager.focus_planner.llm_function = llm_function
        self.treatment_manager.mbc.llm_function = llm_function
        self.treatment_manager.reviewer.llm_function = llm_function
        logger.info("🧠 LLM-Funktion für CarePlanManager gesetzt")
    
    def set_model_loader(self, model_loader: Any) -> None:
        """
        Setzt ModelLoader nach Initialisierung (z.B. wenn Chat-System fertig geladen ist)
        
        Args:
            model_loader: ModelLoader-Instanz aus dem Chat-System
        """
        try:
            self.model_loader = model_loader
            
            # Update DB mit ModelLoader (DB re-initialisiert KG-Extractor)
            if hasattr(self.db, 'set_model_loader'):
                self.db.set_model_loader(model_loader)
                logger.info("✅ ModelLoader über DB.set_model_loader() propagiert")
            
            # Build a wrapper around model_loader and route into CarePlanManager.
            if model_loader:
                def llm_wrapper(prompt: str, image_path: Optional[str] = None) -> str:
                    """Wrapper für LLM-Aufrufe aus den Domain-Komponenten."""
                    try:
                        response: Any = model_loader.generate_response(
                            prompt=prompt,
                            image_path=image_path,
                            max_tokens=1024,
                            temperature=0.3,  # Niedrig für strukturierte JSON-Ausgabe
                        )
                        return str(response) if response else ""
                    except Exception as e:
                        logger.error(f"❌ LLM-Wrapper Fehler: {e}")
                        return ""

                self.set_llm_function(llm_wrapper)
                logger.info("✅ LLM-Wrapper im CarePlanManager aktiviert")

                # Trigger a deterministic backfill pass once LLM capabilities
                # are available. Include cached sessions and the current/recent
                # DB users so backfill also runs when no session is in memory.
                for uid in self._collect_backfill_user_ids(max_recent_users=1):
                    self._run_backfill_with_cooldown(
                        uid,
                        context="MODEL-LOADER",
                        run_summary=False,
                        run_treatment=True,
                    )
            
            # ✅ NEU: Initialisiere ContextSummarizer wenn noch nicht vorhanden
            if not self.context_summarizer and model_loader:
                try:
                    from wellbeing.context_summarizer import ContextSummarizer
                    self.context_summarizer = ContextSummarizer(self.db, model_loader=model_loader)
                    logger.info("✅ ContextSummarizer nachträglich initialisiert (100% LLM-basiert)")
                except Exception as e:
                    logger.error(f"❌ ContextSummarizer konnte nicht initialisiert werden: {e}")
            
            logger.info("✅ ModelLoader erfolgreich gesetzt")
        except Exception as e:
            logger.error(f"❌ Fehler beim Setzen des ModelLoaders: {e}")

    def _collect_backfill_user_ids(self, max_recent_users: int = 5) -> List[str]:
        """Collect canonical user IDs for treatment backfill triggering."""
        candidates: List[str] = []
        seen: set[str] = set()

        def _add(raw_user_id: Optional[str]) -> None:
            if not raw_user_id:
                return
            try:
                canonical = self.resolve_user_id(str(raw_user_id))
            except Exception:
                return
            if canonical and canonical not in seen:
                seen.add(canonical)
                candidates.append(canonical)

        # 1) Active in-memory sessions.
        for ctx in self._active_sessions.values():
            _add(getattr(ctx, 'user_id', None))

        # 2) Configured current user (if available).
        try:
            _add(get_current_user_id())
        except Exception:
            pass

        # 3) Most recently active DB users.
        try:
            with self.db.get_connection() as conn:
                rows = conn.execute(
                    """SELECT user_id, MAX(updated_at) AS last_seen
                       FROM wellbeing_sessions
                       GROUP BY user_id
                       ORDER BY last_seen DESC
                       LIMIT ?""",
                    (max_recent_users,),
                ).fetchall()
            for row in rows:
                _add(row['user_id'])
        except Exception as exc:
            logger.debug("[BACKFILL] Could not collect recent DB users: %s", exc)

        return candidates
    
    def get_session_context(self, session_id: str) -> Optional[SessionContext]:
        """
        Liefert aktuellen Session-Kontext
        
        Args:
            session_id: Session-ID
            
        Returns:
            SessionContext oder None
        """
        try:
            # Aus Cache laden
            if session_id in self._active_sessions:
                return self._active_sessions[session_id]
            
            # Aus Datenbank laden
            self._load_session_context(session_id)
            return self._active_sessions.get(session_id)
            
        except Exception as e:
            logger.error(f"❌ Kontext-Abruf fehlgeschlagen: {e}")
            return None
    
    def get_session_history(self, session_id: str, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """
        Liefert Session-Historie
        
        Args:
            session_id: Session-ID
            limit: Maximale Anzahl Nachrichten
            
        Returns:
            Liste von Interaktionen
        """
        try:
            # FIXED: Use get_session_interactions (limit wird ignoriert, aber funktioniert)
            interactions = self.db.get_session_interactions(session_id)
            # Limit manuell anwenden
            return interactions[:limit] if limit else interactions
        except Exception as e:
            logger.error(f"❌ Historie-Abruf fehlgeschlagen: {e}")
            return []
    
    def close_session(self, session_id: str, generate_summary: bool = True) -> bool:
        """
        Schließt Session ab.

        Die Session wird IMMER geschlossen (end_time gesetzt), unabhängig davon
        ob die Summary-Generierung gelingt.  Ein Therapeut beendet die Sitzung
        auch wenn er die Notizen noch nicht geschrieben hat.

        Args:
            session_id: Session-ID
            generate_summary: Versuche LLM-basierte Zusammenfassung zu erstellen

        Returns:
            True wenn erfolgreich (Session geschlossen), False nur bei DB-Fehler
        """
        summary = None
        summary_error = None

        # ── 1. Summary-Generierung (darf Session-Schließung nicht blockieren) ──
        if generate_summary:
            if not self.context_summarizer:
                summary_error = "ContextSummarizer nicht verfügbar — ModelLoader fehlt"
                logger.warning(f"⚠️ {summary_error}")
            else:
                try:
                    logger.info(f"📝 Generiere LLM-basierte Session-Zusammenfassung für: {session_id}")
                    summary = self.context_summarizer.create_session_summary(session_id)

                    if not summary or len(summary.strip()) < 50:
                        summary_error = f"LLM-Summary zu kurz oder leer: {len(summary) if summary else 0} chars"
                        logger.warning(f"⚠️ {summary_error}")
                        summary = None
                    else:
                        logger.info(f"✅ LLM-Summary erstellt: {len(summary)} Zeichen")
                except Exception as e:
                    summary_error = str(e)
                    logger.error(f"❌ LLM-Summary fehlgeschlagen: {e}", exc_info=True)

        # ── 2. Session IMMER schließen (end_time + ggf. summary) ──
        try:
            with self.db.get_connection() as conn:
                conn.execute("""
                    UPDATE wellbeing_sessions
                    SET end_time = ?, session_summary = ?, updated_at = ?
                    WHERE id = ?
                """, (
                    datetime.now(timezone.utc).isoformat(),
                    summary,
                    datetime.now(timezone.utc).isoformat(),
                    session_id
                ))
                conn.commit()
        except Exception as e:
            logger.error(f"❌ Session-Update in DB fehlgeschlagen: {e}", exc_info=True)
            return False

        # ── 3. Cleanup In-Memory-State ──
        self._active_sessions.pop(session_id, None)
        self._session_locks.pop(session_id, None)
        self._last_auto_summarization.pop(session_id, None)

        if summary_error:
            logger.info(f"✓ Session geschlossen OHNE Summary: {session_id} (Grund: {summary_error})")
        else:
            logger.info(f"✓ Session geschlossen MIT Summary: {session_id}")
        return True
    
    def _generate_session_summary(self, session_id: str) -> str:
        """
        **DEPRECATED** - Regelbasierte Session-Zusammenfassung (NICHT MEHR VERWENDEN!)
        
        ⚠️ Diese Methode ist veraltet und sollte nicht mehr verwendet werden!
        ✅ Nutze stattdessen: ContextSummarizer.create_session_summary() (100% LLM-basiert)
        
        Args:
            session_id: Session-ID
            
        Returns:
            Zusammenfassung als Text
            
        Deprecated:
            Seit 2025-11-04: Ersetzt durch LLM-basierte Summaries
        """
        try:
            # FIXED: Lade Session-Interaktionen
            interactions = self.db.get_session_interactions(session_id)
            context = self.get_session_context(session_id)
            
            if not interactions:
                return "Keine Interaktionen in dieser Session."
            
            # Basis-Statistiken
            user_messages = len([i for i in interactions if i['role'] == 'user'])
            assistant_messages = len([i for i in interactions if i['role'] == 'assistant'])
            total_words = sum(i.get('word_count', 0) for i in interactions)
            
            summary_parts = [
                f"Session-Zusammenfassung (ID: {session_id})",
                f"Dauer: {self._format_session_duration(context)}",
                f"Nachrichten: {user_messages} Benutzer, {assistant_messages} Assistent",
                f"Gesamte Wörter: {total_words}",
            ]
            
            # Mood-Trend wenn verfügbar
            if context and context.mood_trend:
                summary_parts.append(f"Letzte Stimmung: {context.mood_trend}")
            
            # Letzte Interaktionen
            recent_topics = self._extract_recent_topics(interactions[-5:])
            if recent_topics:
                summary_parts.append(f"Aktuelle Themen: {', '.join(recent_topics)}")
            
            return "\n".join(summary_parts)
            
        except Exception as e:
            logger.error(f"❌ Summary-Generierung fehlgeschlagen: {e}")
            return f"Session beendet am {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')}"
    
    def _format_session_duration(self, context: Optional[SessionContext]) -> str:
        """
        **DEPRECATED** - Nur für legacy _generate_session_summary
        
        Deprecated:
            Seit 2025-11-04: Teil der veralteten regelbasierten Summary-Methode
        """
        if not context:
            return "Unbekannt"
        
        duration = context.last_interaction - context.start_time
        hours = int(duration.total_seconds() // 3600)
        minutes = int((duration.total_seconds() % 3600) // 60)
        
        if hours > 0:
            return f"{hours}h {minutes}m"
        else:
            return f"{minutes}m"
    
    def _extract_recent_topics(self, interactions: List[Dict[str, Any]]) -> List[str]:
        """
        **DEPRECATED** - Regelbasierte Themenextraktion (NICHT MEHR VERWENDEN!)
        
        ⚠️ Diese Methode nutzt einfache Keyword-Extraktion ohne semantisches Verständnis
        ✅ LLM-basierte Summaries extrahieren Themen automatisch mit echtem Verständnis
        
        Deprecated:
            Seit 2025-11-04: Ersetzt durch LLM-basierte Topic-Extraction
        """
        try:
            # Einfache Keyword-Extraktion
            topics = []
            user_messages = [i['content'] for i in interactions if i['role'] == 'user']
            
            # Häufige therapeutische Begriffe
            topic_keywords = {
                'gefühle': ['gefühl', 'emotion', 'traurig', 'glücklich', 'ängstlich', 'wütend'],
                'beziehungen': ['familie', 'freund', 'partner', 'beziehung', 'eltern'],
                'arbeit': ['arbeit', 'job', 'beruf', 'kollege', 'chef', 'stress'],
                'gesundheit': ['schlaf', 'müde', 'krank', 'gesundheit', 'schmerz'],
                'ziele': ['ziel', 'plan', 'zukunft', 'wunsch', 'traum']
            }
            
            for message in user_messages:
                message_lower = message.lower()
                for topic, keywords in topic_keywords.items():
                    if any(keyword in message_lower for keyword in keywords):
                        if topic not in topics:
                            topics.append(topic)
            
            return topics[:3]  # Maximal 3 Themen
            
        except Exception as e:
            logger.error(f"❌ Topic-Extraktion fehlgeschlagen: {e}")
            return []
    
    def list_user_sessions(self, user_id: str, include_closed: bool = False) -> List[Dict[str, Any]]:
        """
        Listet alle Sessions eines Benutzers
        
        Args:
            user_id: Benutzer-ID
            include_closed: Inklusive geschlossene Sessions
            
        Returns:
            Liste von Session-Informationen
        """
        try:
            user_id = self.resolve_user_id(user_id)
            
            if include_closed:
                sessions = self.db.get_user_sessions(user_id)
            else:
                sessions = self.db.get_user_sessions(user_id, status='active')
            
            # Ergänze Session-Kontext
            enriched_sessions = []
            for session in sessions:
                context = self.get_session_context(session['id'])
                session_info = {
                    **session,
                    'interaction_count': context.interaction_count if context else 0,
                    'last_interaction': context.last_interaction.isoformat() if context else session['updated_at']
                }
                enriched_sessions.append(session_info)
            
            return enriched_sessions
            
        except Exception as e:
            logger.error(f"❌ Session-Liste fehlgeschlagen: {e}")
            return []
    
    def delete_user_data(self, user_id: str) -> bool:
        """
        Löscht alle Daten eines Benutzers (DSGVO-Konform)
        
        Args:
            user_id: Benutzer-ID
            
        Returns:
            True wenn erfolgreich
        """
        try:
            user_id = self.resolve_user_id(user_id)
            result = self.db.delete_user_data(user_id)
            for session_id in result["session_ids"]:
                self._active_sessions.pop(session_id, None)
                self._session_locks.pop(session_id, None)
                self._last_auto_summarization.pop(session_id, None)
            self._last_backfill.pop(user_id, None)

            logger.info(
                "✓ Alle Roh- und Ableitungsdaten gelöscht für User %s (%d Sessions)",
                user_id,
                len(result["session_ids"]),
            )
            return True
            
        except Exception as e:
            logger.error(f"❌ Datenlöschung fehlgeschlagen: {e}")
            return False
    
    def get_statistics(self) -> Dict[str, Any]:
        """
        Liefert Session-Manager-Statistiken
        
        Returns:
            Dictionary mit Statistiken
        """
        try:
            # FIXED: get_database_stats existiert nicht - sammle Stats manuell
            with self.db.get_connection() as conn:
                # Zähle Sessions
                total_sessions = conn.execute("SELECT COUNT(*) FROM wellbeing_sessions").fetchone()[0]
                active_sessions = conn.execute("SELECT COUNT(*) FROM wellbeing_sessions WHERE end_time IS NULL").fetchone()[0]
                # Zähle Interaktionen
                total_interactions = conn.execute("SELECT COUNT(*) FROM session_interactions").fetchone()[0]
                
                db_stats = {
                    'total_sessions': total_sessions,
                    'active_sessions': active_sessions,
                    'total_interactions': total_interactions
                }
            
            # Cache-Statistiken
            active_cache_sessions = len(self._active_sessions)
            locked_sessions = sum(1 for locked in self._session_locks.values() if locked)
            
            return {
                **db_stats,
                'cache_stats': {
                    'active_sessions_cached': active_cache_sessions,
                    'locked_sessions': locked_sessions
                },
                'auto_summarize_threshold': self.auto_summarize_threshold
            }
            
        except Exception as e:
            logger.error(f"❌ Statistik-Abruf fehlgeschlagen: {e}")
            return {}

    # === INTERFACE-METHODEN FÜR PSYCHOLOGISCHES INTERFACE ===
    
    def create_session(self, user_id: Optional[str] = None) -> str:
        """
        Erstelle neue Session - Interface-kompatible Methode
        
        Args:
            user_id: Benutzer-ID (automatisch ermittelt wenn None)
            
        Returns:
            Session-ID
        """
        # Ermittle User-ID automatisch wenn nicht angegeben
        if user_id is None:
            try:
                if AUTO_USER_ID:
                    user_id = get_current_user_id()
                else:
                    user_id = "default_user"
            except:
                user_id = "default_user"
        
        # Sicherstellen, dass user_id immer ein String ist
        if not isinstance(user_id, str):
            user_id = str(user_id)  # type: ignore[unreachable]
        
        return self.create_or_restore_session(user_id, restore_if_recent=False)
    
    def get_current_session(self) -> Optional[Dict[str, Any]]:
        """
        Hole aktuelle Session - Interface-kompatible Methode
        
        Returns:
            Session-Dictionary oder None
        """
        try:
            # Suche nach aktiver Session im Cache
            for session_id, context in self._active_sessions.items():
                # Prüfe ob Session kürzlich aktiv war (letzten 24 Stunden)
                time_diff = datetime.now(timezone.utc) - context.last_interaction
                if time_diff.total_seconds() / 3600 <= 24:
                    return {
                        'id': session_id,
                        'user_id': context.user_id,
                        'created_at': context.start_time.isoformat(),
                        'updated_at': context.last_interaction.isoformat(),
                        'message_count': context.interaction_count,
                        'summary': context.summary,
                        'mood_trend': context.mood_trend,
                        'crisis_detected': getattr(context, 'crisis_detected', False)
                    }
            return None
        except Exception as e:
            logger.error(f"❌ Fehler beim Abrufen der aktuellen Session: {e}")
            return None
    
    def add_to_context(self, session_id: str, content: str) -> bool:
        """
        Füge Inhalt zum Session-Kontext hinzu - Interface-kompatible Methode
        
        Args:
            session_id: Session-ID
            content: Inhalt der hinzugefügt werden soll
            
        Returns:
            True wenn erfolgreich
        """
        try:
            # DEBUG-LOG: Einstieg
            logger.info(f"📝 SESSION-MGR: add_to_context aufgerufen")
            logger.info(f"📝 SESSION-MGR: Content: '{content[:80]}...'")
            
            # Extrahiere Rolle aus Content
            if content.startswith("Benutzer: "):
                role = "user"
                actual_content = content[10:]  # Entferne "Benutzer: "
                logger.debug(f"📝 SESSION-MGR: Erkannt als USER-Nachricht")
            elif content.startswith("Assistent: "):
                role = "assistant"
                actual_content = content[12:]  # Entferne "Assistent: "
                logger.debug(f"📝 SESSION-MGR: Erkannt als ASSISTANT-Nachricht")
            else:
                role = "system"
                actual_content = content
                logger.debug(f"📝 SESSION-MGR: Erkannt als SYSTEM-Nachricht")
            
            logger.info(f"📝 SESSION-MGR: Rufe add_interaction auf (Role: {role})...")
            result = self.add_interaction(session_id, role, actual_content)
            
            if result:
                logger.info(f"✅ SESSION-MGR: add_to_context ERFOLG")
            else:
                logger.error(f"❌ SESSION-MGR: add_to_context FEHLGESCHLAGEN")
            
            return result
        except Exception as e:
            logger.error(f"❌❌ SESSION-MGR: Fehler beim Hinzufügen zum Kontext: {e}")
            import traceback
            logger.error(f"Traceback: {traceback.format_exc()}")
            return False
    
    def end_current_session(self) -> bool:
        """
        Beende aktuelle Session - Interface-kompatible Methode
        
        Returns:
            True wenn erfolgreich
        """
        try:
            current_session = self.get_current_session()
            if current_session:
                return self.close_session(current_session['id'], generate_summary=True)
            return True  # Keine aktive Session zum Beenden
        except Exception as e:
            logger.error(f"❌ Fehler beim Beenden der aktuellen Session: {e}")
            return False
    
    def get_session_details(self, session_id: str) -> Dict[str, Any]:
        """
        Hole detaillierte Session-Informationen - Interface-kompatible Methode
        
        Args:
            session_id: Session-ID
            
        Returns:
            Detaillierte Session-Informationen
        """
        try:
            context = self.get_session_context(session_id)
            if not context:
                return {"status": "not_found"}
            
            history = self.get_session_history(session_id, limit=5)
            
            return {
                "status": "found",
                "session_id": session_id,
                "user_id": context.user_id,
                "created_at": context.start_time.isoformat(),
                "last_interaction": context.last_interaction.isoformat(),
                "interaction_count": context.interaction_count,
                "summary": context.summary,
                "mood_trend": context.mood_trend,
                "care_notes": context.care_notes,
                "recent_history": history,
                "crisis_detected": getattr(context, 'crisis_detected', False)
            }
        except Exception as e:
            logger.error(f"❌ Fehler beim Abrufen der Session-Details: {e}")
            return {"status": "error", "error": str(e)}

    def get_user_profile(self, user_id: str) -> Optional[Dict[str, Any]]:
        """
        Hole vollständiges User-Profil mit Session-Historie
        
        Diese Methode wird vom PsychologicalInterface verwendet, um
        historischen Kontext für den RAG-enhanced Prompt zu laden.
        
        Args:
            user_id: Benutzer-ID
            
        Returns:
            User-Profil mit allen Sessions und Interaktionen oder None
        """
        try:
            user_id = self.resolve_user_id(user_id)

            # Hole alle Sessions des Users
            all_sessions = self.db.get_user_sessions(user_id)
            
            if not all_sessions:
                logger.info(f"ℹ️ Keine Sessions für user_id={user_id} gefunden")
                return None
            
            # Statistiken sammeln
            total_sessions = len(all_sessions)
            total_interactions = 0
            topics = set()
            mood_history = []
            recent_sessions = []
            care_goals = set()
            session_summaries = []
            
            # Sortiere Sessions nach Datum (neueste zuerst)
            sorted_sessions = sorted(
                all_sessions, 
                key=lambda s: s.get('updated_at', s.get('created_at', '')), 
                reverse=True
            )
            
            # Verarbeite Sessions (letzte 10 für Kontext)
            for session in sorted_sessions[:10]:
                session_id = session['id']
                
                # Hole Session-Details
                interactions = self.db.get_session_interactions(session_id)
                total_interactions += len(interactions)
                
                # Sammle Topics aus User-Messages (echte Daten!)
                for interaction in interactions:
                    user_msg = interaction.get('user_message', '')
                    if user_msg:
                        # Entferne kurze Wörter und Füllwörter
                        words = user_msg.lower().split()
                        meaningful_words = [
                            w.strip('.,!?;:') for w in words 
                            if len(w) > 5 and w not in ['kannst', 'möchte', 'sollte', 'könnte', 'würde']
                        ]
                        topics.update(meaningful_words[:5])  # Top 5 pro Nachricht
                
                # Sammle Mood Progression (falls vorhanden)
                if session.get('mood_progression'):
                    mood_history.append(session['mood_progression'])
                
                # Care goals — pulled from the canonical CarePlanManager
                # (single source of truth). The legacy `;`-string column on the
                # session row is no longer authoritative.
                # Fallback: if the manager is unavailable for any reason we read
                # the legacy column to preserve continuity (read-only).
                # We accumulate per-goal-id to deduplicate across sessions.
                # Done once per profile build (outside the loop) below.
                pass
                
                # Sammle Session Summaries (sehr wertvoll!)
                if session.get('session_summary'):
                    current_summary = session['session_summary']
                    
                    # 🔄 AUTO-REGENERIERUNG: Prüfe ob Summary regelbasierte ist
                    if self._is_rule_based_summary(current_summary):
                        logger.debug(f"🔄 Regelbasierte Summary in previous session erkannt - regeneriere...")
                        new_summary = self._auto_regenerate_summary_if_needed(session_id, current_summary)
                        if new_summary:
                            current_summary = new_summary  # Verwende neue Summary
                    
                    session_summaries.append({
                        'date': session.get('created_at'),
                        'summary': current_summary
                    })
                
                # Recent Sessions für Kontext
                recent_sessions.append({
                    'session_id': session_id,
                    'created_at': session.get('created_at'),
                    'interaction_count': len(interactions),
                    'summary': session.get('session_summary', ''),
                    'goals': session.get('care_goals', ''),
                    'mood': session.get('mood_progression', '')
                })
            
            # Erstelle Profil mit ECHTEN Session-Daten
            # SOTA: pull active goals from CarePlanManager — single source of
            # truth. The legacy `;`-joined column is no longer read; downstream
            # consumers receive only structured goals from the treatment domain.
            plan = self.treatment_manager.repo.get_active_plan(user_id)
            if plan and plan.id:
                from .care_plans.models import GoalStatus as _GS
                active_goals = self.treatment_manager.repo.list_goals(
                    plan.id, statuses=[_GS.ACTIVE, _GS.ACHIEVED],
                )
                for g in active_goals:
                    if g.title:
                        care_goals.add(g.title)

            profile: Dict[str, Any] = {
                'user_id': user_id,
                'total_sessions': total_sessions,
                'total_interactions': total_interactions,
                
                # Session-Historie (für Kontext)
                'recent_sessions': recent_sessions,
                
                # Aggregierte Insights
                'topics': list(topics)[:20],  # Top 20 Topics aus echten Messages
                'care_goals': list(care_goals),  # Echte Care-Ziele
                'session_summaries': session_summaries[:5],  # Letzte 5 Summaries
                'mood_history': mood_history[-10:],  # Letzte 10 Mood-Progressions
                
                # Metadaten
                'created_at': all_sessions[0].get('created_at') if all_sessions else None,
                'last_activity': sorted_sessions[0].get('updated_at') if sorted_sessions else None,
                'updated_at': sorted_sessions[0].get('updated_at') if sorted_sessions else None,
                
                # Für PersonalityProfile-Kompatibilität (wird von user_insight_extractor erwartet)
                'personality_traits': {
                    'core_traits': [],  # Wird später aus Insights extrahiert
                    'type': None
                },
                'communication_style': None,  # Wird später aus Insights extrahiert
                'preferences': {
                    'coping_strategies': []  # Wird später extrahiert
                }
            }
            
            logger.info(f"✅ User-Profil geladen: {user_id} ({total_sessions} Sessions, {total_interactions} Interactions, {len(topics)} Topics)")
            if care_goals:
                logger.info(f"   Care-Ziele: {list(care_goals)[:3]}")
            if session_summaries:
                logger.info(f"   Session-Summaries: {len(session_summaries)}")
            
            return profile
            
        except Exception as e:
            logger.error(f"❌ Fehler beim Laden des User-Profils: {e}")
            import traceback
            traceback.print_exc()
            return None
        
    def update_session_summary(self, session_id: str, force: bool = False) -> Optional[str]:
        """
        Aktualisiert Session-Summary OHNE die Session zu schließen
        
        Intelligentes Update mit Throttling:
        - Nur alle N Interactions (z.B. alle 5)
        - Nur wenn genug neue Daten seit letztem Update
        - Oder wenn force=True
        
        Args:
            session_id: Session-ID
            force: Erzwinge Update auch ohne neue Daten
            
        Returns:
            Generierte Summary oder None
        """
        try:
            # ✅ FIX: Prüfe zuerst im Cache, dann lade aus DB
            context = self._active_sessions.get(session_id)
            
            if not context:
                # Session nicht im Cache - lade aus DB (für Backfill von alten Sessions)
                logger.debug(f"📋 Session {session_id[:12]}... nicht im Cache - lade aus DB")
                try:
                    # 🔥 FIX: Hole Session direkt aus DB via SQL
                    with self.db.get_connection() as conn:
                        cursor = conn.execute("""
                            SELECT id, user_id, start_time, end_time, session_summary,
                                   mood_progression, care_goals, created_at, updated_at
                            FROM wellbeing_sessions
                            WHERE id = ?
                        """, (session_id,))
                        
                        row = cursor.fetchone()
                        if not row:
                            logger.warning(f"⚠️ Session {session_id[:12]}... nicht in DB gefunden")
                            return None
                        
                        session_data = dict(row)
                    
                    # Baue temporären Context aus DB-Daten
                    interactions = self.db.get_session_interactions(session_id)
                    context = SessionContext(
                        session_id=session_id,
                        user_id=session_data.get('user_id', 'default_user'),
                        start_time=normalize_datetime(session_data.get('created_at')),
                        last_interaction=normalize_datetime(session_data.get('updated_at')),
                        interaction_count=len(interactions)
                    )
                    logger.debug(f"   → Temporärer Context erstellt: {context.interaction_count} Interactions")
                    
                except Exception as e:
                    logger.error(f"❌ Fehler beim Laden der Session aus DB: {e}")
                    return None
            
            # Throttling-Check (außer bei force=True)
            if not force:
                # Nur alle 5+ Interactions updaten
                if context.interaction_count < 5:
                    logger.debug(f"📊 Summary-Update übersprungen - zu wenig Interactions ({context.interaction_count})")
                    return None
                
                # Nur alle 5 Interactions updaten (5, 10, 15, ...)
                if context.interaction_count % 5 != 0:
                    logger.debug(f"📊 Summary-Update übersprungen - warte auf nächsten Meilenstein")
                    return None
            
            logger.info(f"📝 Generiere Session-Summary für {session_id[:12]}... ({context.interaction_count} Interactions)")
            
            # Generiere Summary
            summary = None
            if self.context_summarizer:
                try:
                    summary = self.context_summarizer.create_session_summary(session_id)
                    if summary:
                        logger.info(f"✅ KI-Summary erstellt: {len(summary)} Zeichen")
                except Exception as e:
                    logger.warning(f"⚠️ KI-Summary fehlgeschlagen: {e}, nutze Fallback")
                    summary = self._generate_session_summary(session_id)
            else:
                summary = self._generate_session_summary(session_id)
            
            # Speichere Summary in DB (OHNE Session zu schließen!)
            if summary:
                try:
                    with self.db.get_connection() as conn:
                        conn.execute("""
                            UPDATE wellbeing_sessions
                            SET session_summary = ?, updated_at = ?
                            WHERE id = ?
                        """, (
                            summary,
                            datetime.now(timezone.utc).isoformat(),
                            session_id
                        ))
                        conn.commit()
                    
                    # Update Context-Cache (falls im Cache)
                    if session_id in self._active_sessions:
                        self._active_sessions[session_id].summary = summary
                    
                    logger.info(f"✅ Session-Summary gespeichert ({len(summary)} chars)")
                    return summary
                    
                except Exception as e:
                    logger.error(f"❌ Summary-Speicherung fehlgeschlagen: {e}")
            
            return None
            
        except Exception as e:
            logger.error(f"❌ Session-Summary-Update fehlgeschlagen: {e}")
            return None

    def _backfill_missing_summaries(self, user_id: str, max_sessions: int = 5) -> int:
        """
        Generiert fehlende Session-Summaries für alte Sessions eines Users
        
        Diese Funktion wird beim Session-Start aufgerufen und prüft ob frühere
        Sessions des Users keine Summary haben. Falls ja, werden diese nachträglich
        generiert um vollständigen Care-Kontext zu gewährleisten.
        
        Args:
            user_id: User-ID dessen alte Sessions geprüft werden
            max_sessions: Maximale Anzahl Sessions die bearbeitet werden (Standard: 5)
            
        Returns:
            Anzahl der generierten Summaries
        """
        try:
            logger.info(f"🔍 [BACKFILL] Prüfe fehlende Summaries für user_id={user_id[:12]}...")
            
            # Hole GESCHLOSSENE Sessions des Users ohne Summary
            # (status='active' war falsch — beendete Sessions haben end_time IS NOT NULL,
            #  also status='ended'.  Gerade DIESE brauchen Backfill, nicht aktive.)
            sessions = self.db.get_user_sessions(user_id, status='closed')
            
            if not sessions:
                logger.info(f"   → Keine Sessions gefunden")
                return 0
            
            # Sortiere nach Datum (neueste zuerst)
            sorted_sessions = sorted(
                sessions,
                key=lambda s: s.get('updated_at', s.get('created_at', '')),
                reverse=True
            )
            
            # Finde Sessions ohne Summary (mind. 20 chars)
            sessions_without_summary = []
            for session in sorted_sessions[:max_sessions]:
                summary = session.get('session_summary', '')
                if not summary or len(summary.strip()) < 20:
                    # Prüfe ob Session genug Interaktionen hat (mind. 3)
                    interactions = self.db.get_session_interactions(session['id'])
                    if len(interactions) >= 3:
                        sessions_without_summary.append(session)
            
            if not sessions_without_summary:
                logger.info(f"✅ [BACKFILL] Alle Sessions haben bereits Summaries")
                return 0
            
            logger.info(f"📝 [BACKFILL] {len(sessions_without_summary)} Sessions ohne Summary gefunden")
            
            # Generiere Summaries (mit Rate-Limiting)
            generated_count = 0
            for session in sessions_without_summary[:3]:  # Max 3 auf einmal
                session_id = session['id']
                logger.info(f"   → Generiere Summary für Session {session_id[:12]}...")
                
                try:
                    # Verwende update_session_summary() zur Generierung
                    summary = self.update_session_summary(session_id, force=True)
                    
                    if summary:
                        generated_count += 1
                        logger.info(f"      ✅ Summary generiert: {len(summary)} Zeichen")
                    else:
                        logger.warning(f"      ⚠️ Summary-Generierung fehlgeschlagen")
                    
                    # Rate-Limiting: Pause zwischen Generierungen
                    import time
                    time.sleep(1)
                    
                except Exception as e:
                    logger.error(f"      ❌ Fehler bei Summary-Generierung: {e}")
                    continue
            
            logger.info(f"✅ [BACKFILL] {generated_count} Summaries generiert")
            return generated_count
            
        except Exception as e:
            logger.error(f"❌ [BACKFILL] Fehler: {e}")
            import traceback
            traceback.print_exc()
            return 0
    
    def _run_backfill_with_cooldown(
        self,
        user_id: str,
        context: str = "UNKNOWN",
        run_summary: bool = True,
        run_treatment: bool = True,
    ) -> int:
        """
        Führt Backfill mit Cooldown-Protection aus
        
        Verhindert dass Backfill bei jeder Nachricht ausgeführt wird.
        Cooldown: 1 Stunde pro User
        
        Args:
            user_id: User-ID für Backfill
            context: Kontext-String für Logging (z.B. "SESSION-START", "SESSION-RESTORE")
            
        Returns:
            Anzahl generierter Summaries (0 wenn Cooldown aktiv)
        """
        try:
            # Prüfe Cooldown
            last_backfill = self._last_backfill.get(user_id)
            if last_backfill:
                time_since_last = datetime.now(timezone.utc) - last_backfill
                if time_since_last < self._backfill_cooldown:
                    remaining = self._backfill_cooldown - time_since_last
                    logger.debug(f"🔄 [{context}] Backfill Cooldown aktiv (noch {remaining})")
                    return 0
            
            generated = 0
            treatment_backfilled = 0

            if run_summary:
                generated = self._backfill_missing_summaries(user_id, max_sessions=5)

            if run_treatment:
                treatment_backfilled = self._backfill_treatment_history(user_id)
            
            # Setze Cooldown-Timestamp
            self._last_backfill[user_id] = datetime.now(timezone.utc)
            
            if generated > 0 or treatment_backfilled > 0:
                logger.info(
                    "✅ [%s] Backfill abgeschlossen: summaries=%s, treatment_sessions=%s",
                    context,
                    generated,
                    treatment_backfilled,
                )
            
            return generated
            
        except Exception as e:
            logger.warning(f"⚠️ [{context}] Backfill fehlgeschlagen: {e}")
            return 0

    def _backfill_treatment_history(self, user_id: str, max_sessions: int = 12) -> int:
        """Backfill treatment goals/progress by replaying historic user turns."""
        try:
            if not callable(getattr(self.treatment_manager, 'llm_function', None)):
                logger.debug("[TREATMENT-BACKFILL] Skip (LLM nicht verfügbar)")
                return 0

            sessions = self.db.get_user_sessions(user_id) or []
            if not sessions:
                return 0

            sessions_sorted = sorted(
                sessions,
                key=lambda s: s.get('created_at') or s.get('updated_at') or '',
            )

            # Preflight: build payloads first. If no replayable content exists,
            # do not create a treatment plan as a side effect.
            replay_candidates: List[Tuple[str, List[Dict[str, Any]]]] = []
            for s in sessions_sorted[-max_sessions:]:
                session_id = s.get('id')
                if not session_id:
                    continue
                interactions = self.db.get_session_interactions(session_id) or []
                payload: List[Dict[str, Any]] = []
                for it in interactions:
                    role = it.get('role')
                    content = it.get('content')
                    if role and content:
                        payload.append({
                            'role': str(role),
                            'content': str(content),
                        })
                if len(payload) >= 3:
                    replay_candidates.append((str(session_id), payload))

            if not replay_candidates:
                return 0

            plan = self.treatment_manager.repo.get_active_plan(user_id)
            if plan is not None and plan.id is not None:
                existing_goals = self.treatment_manager.repo.count_goals(plan.id)
                existing_updates = self.treatment_manager.repo.count_goal_updates(plan.id)
                if existing_goals > 0 and existing_updates > 0:
                    logger.debug("[TREATMENT-BACKFILL] Bereits befüllt, kein Replay nötig")
                    return 0

            replayed = 0
            for session_id, payload in replay_candidates:
                stats = self.treatment_manager.backfill_session(
                    user_id=user_id,
                    session_id=session_id,
                    interactions=payload,
                )
                if stats.get('new_goals', 0) > 0 or stats.get('new_updates', 0) > 0:
                    replayed += 1

            return replayed
        except Exception as e:
            logger.warning(f"⚠️ [TREATMENT-BACKFILL] Fehler: {e}")
            return 0
    
    def _is_rule_based_summary(self, summary: str) -> bool:
        """
        Erkennt ob eine Summary regelbasiert (alt) oder LLM-basiert (neu) ist
        
        Heuristik:
        - Regelbasierte Summaries sind kurz (<300 chars)
        - Enthalten typische Patterns wie "Session-Zusammenfassung", "Dauer:", "Nachrichten:"
        - Haben wenig semantischen Inhalt
        
        Args:
            summary: Zu prüfende Summary
            
        Returns:
            True wenn regelbasiert (sollte regeneriert werden)
        """
        if not summary or len(summary.strip()) == 0:
            return False  # Leere Summary ist kein regelbasierter Fall
        
        # Heuristik 1: Länge
        if len(summary) < 300:
            # Heuristik 2: Typische regelbasierte Patterns
            rule_based_patterns = [
                "Session-Zusammenfassung (ID:",
                "Session-Zusammenfassung:",
                "Dauer:",
                "Nachrichten:",
                "Zeitraum: --:-- - --:--",
                "Hauptthemen:",
            ]
            
            for pattern in rule_based_patterns:
                if pattern in summary:
                    return True
        
        return False
    
    def _auto_regenerate_summary_if_needed(self, session_id: str, current_summary: Optional[str]) -> Optional[str]:
        """
        🔄 AUTO-REGENERIERUNG: Erkennt regelbasierte Summaries und regeneriert sie mit LLM
        
        Diese Methode wird automatisch aufgerufen wenn eine Session geladen wird.
        Wenn eine alte regelbasierte Summary erkannt wird, wird sie im Hintergrund
        mit dem LLM neu generiert.
        
        Args:
            session_id: Session-ID
            current_summary: Aktuelle Summary aus der DB
            
        Returns:
            Neue LLM-basierte Summary oder None wenn Regenerierung nicht nötig/möglich
        """
        try:
            # Prüfe ob Summary regelbasiert ist
            if not current_summary or not self._is_rule_based_summary(current_summary):
                return None  # Bereits LLM-basiert oder leer
            
            # Prüfe ob ContextSummarizer verfügbar ist
            if not self.context_summarizer:
                logger.debug(f"📋 Regelbasierte Summary erkannt, aber ContextSummarizer nicht verfügbar")
                return None
            
            logger.info(f"🔄 AUTO-REGENERIERUNG: Regelbasierte Summary erkannt für Session {session_id[:12]}...")
            logger.info(f"   → Alte Summary: {len(current_summary)} chars (regelbasiert)")
            
            # Regeneriere mit LLM
            try:
                new_summary = self.context_summarizer.create_session_summary(session_id)
                
                if new_summary and len(new_summary) > len(current_summary):
                    logger.info(f"   ✅ LLM-Summary generiert: {len(new_summary)} chars")
                    
                    # Speichere sofort in DB
                    with self.db.get_connection() as conn:
                        conn.execute("""
                            UPDATE wellbeing_sessions
                            SET session_summary = ?, updated_at = ?
                            WHERE id = ?
                        """, (
                            new_summary,
                            datetime.now(timezone.utc).isoformat(),
                            session_id
                        ))
                        conn.commit()
                    
                    logger.info(f"   💾 Neue Summary gespeichert: {len(current_summary)} → {len(new_summary)} chars")
                    return new_summary
                else:
                    logger.warning(f"   ⚠️ LLM-Summary nicht besser als alte: {len(new_summary) if new_summary else 0} chars")
                    return None
                    
            except Exception as e:
                logger.warning(f"   ⚠️ Auto-Regenerierung fehlgeschlagen: {e}")
                return None
                
        except Exception as e:
            logger.error(f"❌ Auto-Regenerierung Fehler: {e}")
            return None

    def _update_session_summary(self, session_id: str, force: bool = False) -> Optional[str]:
        """
        Aktualisiert die Session-Zusammenfassung
        
        Diese Methode kombiniert:
        - Intelligentes Throttling: Nur alle N Interaktionen (z.B. alle 5)
        - Automatische Regenerierung von regelbasierten Summaries
        
        Args:
            session_id: Session-ID
            force: Erzwinge Update auch ohne neue Daten
            
        Returns:
            Generierte Summary oder None
        """
        try:
            # Prüfe zuerst die automatische Regenerierung
            new_summary = self._auto_regenerate_summary_if_needed(session_id, None)
            if new_summary:
                return new_summary
            
            # Ansonsten normales Update
            return self.update_session_summary(session_id, force=force)
        
        except Exception as e:
            logger.error(f"❌ Fehler bei der Aktualisierung der Session-Zusammenfassung: {e}")
            return None

    def _cleanup_old_cooldowns(self) -> None:
        """
        Cleanup expired cooldown entries (verhindert Memory Leaks)
        
        Entfernt Einträge, die älter als 2x die Cooldown-Period sind.
        Wird automatisch bei bestimmten Operationen aufgerufen.
        """
        try:
            now = datetime.now(timezone.utc)
            
            # Cleanup auto-summarization cooldowns (älter als 2x Cooldown-Period)
            expired_summarizations = [
                sid for sid, ts in self._last_auto_summarization.items()
                if now - ts > self._auto_summarization_cooldown * 2
            ]
            for sid in expired_summarizations:
                del self._last_auto_summarization[sid]
            
            if expired_summarizations:
                logger.info(f"🗑️ Cleaned up {len(expired_summarizations)} expired auto-summarization entries")
            
            # Cleanup backfill cooldowns (älter als 2x Cooldown-Period)
            expired_backfills = [
                uid for uid, ts in self._last_backfill.items()
                if now - ts > self._backfill_cooldown * 2
            ]
            for uid in expired_backfills:
                del self._last_backfill[uid]
            
            if expired_backfills:
                logger.info(f"🗑️ Cleaned up {len(expired_backfills)} expired backfill entries")
                
        except Exception as e:
            logger.warning(f"⚠️ Cooldown cleanup failed: {e}")
    
    def cleanup_old_sessions(self, days: int = 90) -> int:
        """
        Bereinigt alte, abgeschlossene Sessions (DB + Memory Cleanup)
        
        Args:
            days: Sessions älter als X Tage werden gelöscht
            
        Returns:
            Anzahl gelöschter Sessions
        """
        try:
            cutoff_date = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
            deleted_count = 0
            
            # Finde alte Sessions in DB
            with self.db.get_connection() as conn:
                cursor = conn.execute("""
                    SELECT id FROM wellbeing_sessions
                    WHERE end_time IS NOT NULL
                    AND end_time < ?
                """, (cutoff_date,))
                old_sessions = [row[0] for row in cursor.fetchall()]
            
            # Lösche jede Session
            for session_id in old_sessions:
                try:
                    with self.db.get_connection() as conn:
                        # Lösche Interaktionen
                        conn.execute("DELETE FROM session_interactions WHERE session_id = ?", (session_id,))
                        # Lösche Session
                        conn.execute("DELETE FROM wellbeing_sessions WHERE id = ?", (session_id,))
                        conn.commit()
                        
                        # Entferne aus Memory-Cache
                        self._active_sessions.pop(session_id, None)
                        self._session_locks.pop(session_id, None)
                        self._last_auto_summarization.pop(session_id, None)
                        
                        deleted_count += 1
                except Exception as e:
                    logger.warning(f"⚠️ Konnte Session {session_id} nicht löschen: {e}")
            
            # Cleanup alte Cooldowns (Memory-Leak-Prevention)
            self._cleanup_old_cooldowns()
            
            logger.info(f"✅ Cleanup abgeschlossen: {deleted_count} alte Sessions gelöscht (>{days} Tage)")
            return deleted_count
            
        except Exception as e:
            logger.error(f"❌ Cleanup fehlgeschlagen: {e}")
            return 0

