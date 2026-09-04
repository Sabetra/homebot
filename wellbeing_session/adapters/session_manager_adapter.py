"""
Session Manager Adapter

Provides backward compatibility layer between the old interface and the new
WellbeingSessionManager API.

This adapter maps old method names to new method names and handles data format
conversions to ensure seamless integration with legacy code.

✅ Phase 1+2: AddMessageResult API-Vertrag + Recovery-Strategie (keine synthetischen Users)
✅ Phase 3: Rebind-Observability (Callbacks + Persistenz)
✅ Phase 4: Concurrency-Härtung (thread-safe via Lock)
"""
import logging
import warnings
import sqlite3
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Phase 1: AddMessageResult — expliziter API-Vertrag (keine impliziten Fallbacks)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class AddMessageResult:
    """
    Explizites Ergebnis einer add_message/add_message_with_result Operation.

    SOTA-Prinzip: "API contracts should be explicit, not implicit" (Google SRE)
    Statt silent-fallbacks: strukturierter Fehler oder klarer Erfolg.

    Attributes:
        success: True wenn Nachricht erfolgreich gespeichert
        session_id: Die tatsächlich verwendete Session-ID (kann bei Rebind abweichen)
        error: Fehlerbeschreibung bei success=False
        rebinding_occurred: True wenn ein Session-Rebind passierte
        old_session_id: Vorherige Session-ID (falls Rebind)
    """
    success: bool
    session_id: Optional[str] = None
    error: Optional[str] = None
    rebinding_occurred: bool = False
    old_session_id: Optional[str] = None
    risk_level: Optional[str] = None
    is_crisis: bool = False
    safety_action: str = "normal"

    @classmethod
    def failure(cls, error: str, old_session_id: Optional[str] = None) -> "AddMessageResult":
        return cls(success=False, error=error, old_session_id=old_session_id)

    @classmethod
    def ok(
        cls,
        session_id: str,
        rebinding_occurred: bool = False,
        old_session_id: Optional[str] = None,
        risk_level: Optional[str] = None,
        safety_action: str = "normal",
    ) -> "AddMessageResult":
        """Factory for a successful result."""
        effective_action = "acute" if risk_level == "acute" else safety_action
        return cls(
            success=True,
            session_id=session_id,
            rebinding_occurred=rebinding_occurred,
            old_session_id=old_session_id,
            risk_level=risk_level,
            is_crisis=effective_action == "acute",
            safety_action=effective_action,
        )


# ─── Phase 3: Rebind-Observability ──────────────────────────────────────────
# Callbacks werden aufgerufen, wenn sich die aktive Session-ID ändert (Rebind).
# Signatur: callback(old_id: str | None, new_id: str, reason: str)
_session_change_callbacks: List[Callable[[Optional[str], str, str], None]] = []
_callbacks_lock = threading.Lock()


def on_session_change(callback: Callable[[Optional[str], str, str], None]) -> None:
    """Register a callback for session-rebind events."""
    with _callbacks_lock:
        if callback not in _session_change_callbacks:
            _session_change_callbacks.append(callback)


def _emit_session_change(old_id: Optional[str], new_id: str, reason: str) -> None:
    """Fire all registered session-change callbacks (non-blocking per callback)."""
    with _callbacks_lock:
        callbacks = list(_session_change_callbacks)
    for cb in callbacks:
        try:
            cb(old_id, new_id, reason)
        except Exception as e:
            logger.debug(f"Session-change callback {cb.__name__} threw: {e}")


# Import connection pool if available
try:
    from database.connection_pool import ConnectionPool as ConnectionPoolType
    POOL_AVAILABLE = True
except ImportError:
    POOL_AVAILABLE = False
    ConnectionPoolType = None  # type: ignore[misc,assignment]


class SessionManagerAdapter:
    """
    Mini-Adapter: Macht neue WellbeingSessionManager API kompatibel mit altem Interface

    Mappt alte Methoden-Namen → neue Methoden-Namen:
    - get_or_create_session → create_or_restore_session
    - get_session_summary → get_session_context
    - add_message → record_interaction

    Attributes:
        manager: Instance of WellbeingSessionManager
    """

    def __init__(self, psych_manager: Any) -> None:
        """
        Initialize the adapter with a WellbeingSessionManager instance.

        Args:
            psych_manager: WellbeingSessionManager instance
        """
        self.manager = psych_manager
        self._current_session_id: Optional[str] = None
        self._session_lock = threading.Lock()  # Phase 4: Concurrency für Adapter
        logger.info("✅ SessionManagerAdapter initialisiert - Nutzt wellbeing_sessions Tabelle (51 Sessions!)")

    @property
    def db_manager(self) -> Any:
        """Compatibility surface for V2 providers expecting a DB manager."""
        return getattr(self.manager, 'db', None)

    @property
    def mood_tracker(self) -> Any:
        """Compatibility surface for V2 providers expecting a mood tracker."""
        return getattr(self.manager, 'mood_tracker', None)

    def resolve_user_id(self, user_name: str) -> str:
        """Resolve a display/user name to the canonical stored user id."""
        if hasattr(self.manager, 'resolve_user_id'):
            return str(self.manager.resolve_user_id(user_name))
        return str(user_name)

    # ──────────────────────────────────────────────────────────────────────
    # Phase 2: Recovery-Strategie — echte User-ID aus orphanger Session
    # ──────────────────────────────────────────────────────────────────────

    def _recover_original_user_id(self, orphaned_session_id: str) -> Optional[str]:
        """
        Extrahiert die echte user_id aus einer orphaned Session in der DB.

                2-Strategie-Kette:
          1. wellbeing_sessions.user_id direkt abfragen
                    2. Bereits geladene SessionContext-Metadaten verwenden

        Args:
            orphaned_session_id: Session-ID die nicht mehr existiert/ungültig ist

        Returns:
            user_id wenn gefunden, None sonst
        """
        # Strategie 1: Direkt aus wellbeing_sessions
        try:
            db = getattr(self.manager, 'db', None)
            if db is not None:
                with db.get_connection() as conn:
                    row = conn.execute(
                        "SELECT user_id FROM wellbeing_sessions WHERE id = ? LIMIT 1",
                        (orphaned_session_id,)
                    ).fetchone()
                    if row and row[0]:
                        logger.info(f"✅ Recovery: user_id '{row[0]}' aus Session {orphaned_session_id[:12]}... extrahiert")
                        return str(row[0])
        except Exception as e:
            logger.debug(f"Recovery-Strategie 1 (sessions.user_id) fehlgeschlagen: {e}")

        cached_sessions = getattr(self.manager, '_active_sessions', {})
        cached_context = cached_sessions.get(orphaned_session_id)
        cached_user_id = getattr(cached_context, 'user_id', None)
        if cached_user_id:
            logger.info(
                "Recovery: user_id '%s' aus gebundenem SessionContext fuer %s... extrahiert",
                cached_user_id,
                orphaned_session_id[:12],
            )
            return str(cached_user_id)

        logger.error(f"❌ Recovery: Konnte user_id für Session {orphaned_session_id[:12]}... nicht ermitteln")
        return None

    # ──────────────────────────────────────────────────────────────────────
    # Phase 1+2: add_message_with_result — neue Haupt-API
    # ──────────────────────────────────────────────────────────────────────

    def add_message_with_result(
        self,
        session_id: str,
        role: str,
        content: str,
        emotional_markers: Optional[List[str]] = None,
        is_crisis: bool = False
    ) -> AddMessageResult:
        """
        Add message to session mit explizitem Ergebnis (neue Haupt-API).

        ✅ ROOT-CAUSE FIX: Kein synthetischer User mehr.
           Bei orphaned Session: echte user_id aus DB extrahieren → neue Session
           mit echter user_id erstellen → Nachricht speichern.
        ✅ Wenn user_id nicht extrahierbar: strukturierter Fehler (AddMessageResult.failure).
        ✅ Phase 3: Rebind-Observability — feuert Callbacks bei Session-Rebind.
        ✅ Phase 4: Concurrency — thread-sicher via Lock.

        Args:
            session_id: Session ID
            role: Message role ('user', 'assistant', 'system')
            content: Message content
            emotional_markers: Optional list of emotional markers
            is_crisis: Whether this is a crisis message

        Returns:
            AddMessageResult mit success/error/rebind-Status
        """
        # Phase 4: Thread-sicherer Zugriff
        with self._session_lock:
            old_session_id = self._current_session_id

            try:
                # Validierung: Leere session_id
                if not session_id:
                    logger.error("❌ ADDMESSAGE: Leere session_id — Nachricht wird NICHT gespeichert!")
                    return AddMessageResult.failure("Leere session_id", old_session_id)

                # Prüfe ob Session existiert
                session_exists = self._validate_session_exists(session_id)

                if not session_exists:
                    logger.info(
                        f"ℹ️  ADDMESSAGE: Session {session_id[:12]}... nicht in DB — "
                        f"Recovery mit echter user_id..."
                    )

                    # ✅ ROOT-CAUSE FIX: Echte user_id extrahieren statt synthetischen Users
                    recovered_user_id = self._recover_original_user_id(session_id)

                    if recovered_user_id is None:
                        # Strukturierter Fehler — kein synthetischer User
                        logger.error(
                            f"❌ ADDMESSAGE: Konnte user_id für Session {session_id[:12]}... "
                            f"nicht ermitteln. Nachricht wird NICHT gespeichert."
                        )
                        return AddMessageResult.failure(
                            f"Session {session_id[:12]}... orphaniert, user_id nicht extrahierbar",
                            old_session_id
                        )

                    # Neue Session mit ECHTER user_id erstellen
                    try:
                        new_session_id = self.manager.create_or_restore_session(
                            user_id=recovered_user_id,
                            restore_if_recent=False
                        )
                        logger.warning(
                            f"✅ SESSION-RECOVERY: Neue Session {new_session_id[:12]}... "
                            f"mit echter user_id='{recovered_user_id}' erstellt. "
                            f"Ersetzt orphanierte {session_id[:12]}..."
                        )
                        # Phase 3: Rebind-Observability
                        _emit_session_change(session_id, new_session_id, "orphaned_session_recovery")
                        session_id = new_session_id
                    except Exception as recovery_error:
                        logger.error(
                            f"❌ SESSION-RECOVERY FAILED: Konnte Session nicht erstellen: {recovery_error}. "
                            f"Nachricht wird NICHT gespeichert."
                        )
                        return AddMessageResult.failure(
                            f"Recovery fehlgeschlagen: {recovery_error}",
                            old_session_id
                        )

                # Update current session (thread-safe)
                rebinding_occurred = False
                if self._current_session_id != session_id:
                    rebinding_occurred = True
                    _emit_session_change(old_session_id, session_id, "session_switch")
                    self._current_session_id = session_id

                # ✅ Jetzt ist session_id validiert - add_interaction kann sicher aufgerufen werden
                mood_analysis = None
                if emotional_markers:
                    mood_analysis = {'emotional_markers': emotional_markers}

                success = self.manager.add_interaction(
                    session_id=session_id,
                    role=role,
                    content=content,
                    mood_analysis=mood_analysis
                )

                if success:
                    risk_level = None
                    safety_action = "normal"
                    if role == "user" and hasattr(self.manager, "get_last_treatment_result"):
                        turn_result = self.manager.get_last_treatment_result(session_id)
                        risk = getattr(turn_result, "risk", None)
                        level = getattr(risk, "level", None)
                        risk_level = getattr(level, "value", level)
                        if risk_level is not None:
                            risk_level = str(risk_level)
                        safety_action = str(
                            getattr(turn_result, "safety_action", "normal") or "normal"
                        )
                    logger.info(
                        f"✅ Nachricht für Session {session_id[:12]}... gespeichert "
                        f"(role: {role}, {len(content)} Zeichen)"
                    )
                    return AddMessageResult.ok(
                        session_id,
                        rebinding_occurred,
                        old_session_id,
                        risk_level,
                        safety_action,
                    )
                else:
                    logger.error(f"❌ Nachricht konnte nicht gespeichert werden!")
                    return AddMessageResult.failure("add_interaction() hat False zurückgegeben", old_session_id)

            except Exception as e:
                logger.error(f"Fehler beim Hinzufügen der Nachricht: {e}")
                import traceback
                traceback.print_exc()
                return AddMessageResult.failure(str(e), old_session_id)

    # ──────────────────────────────────────────────────────────────────────
    # add_message — deprecierter Kompat-Wrapper (ruft add_message_with_result)
    # ──────────────────────────────────────────────────────────────────────

    def add_message(
        self,
        session_id: str,
        role: str,
        content: str,
        emotional_markers: Optional[List[str]] = None,
        is_crisis: bool = False
    ) -> None:
        """
        Add message to session (alte API — deprecated, nutzt add_message_with_result intern).

        ⚠️  DEPRECATED: Verwende add_message_with_result() für explizite Ergebnisse.
            Dieser Wrapper bleibt aus Kompatibilitätsgründen erhalten.

        Args:
            session_id: Session ID
            role: Message role ('user', 'assistant', 'system')
            content: Message content
            emotional_markers: Optional list of emotional markers
            is_crisis: Whether this is a crisis message
        """
        warnings.warn(
            "add_message() ist deprecated. Verwende add_message_with_result() für explizite Ergebnisse.",
            DeprecationWarning,
            stacklevel=2
        )
        result = self.add_message_with_result(
            session_id=session_id,
            role=role,
            content=content,
            emotional_markers=emotional_markers,
            is_crisis=is_crisis
        )
        if not result.success:
            logger.error(f"add_message() FEHLER: {result.error}")

    # ──────────────────────────────────────────────────────────────────────
    # Session-Validierung
    # ──────────────────────────────────────────────────────────────────────

    def _validate_session_exists(self, session_id: str) -> bool:
        """
        Validiere ob Session in DB existiert.

        ✅ ROOT-CAUSE FIX: Zentrale Validierung verhindert stale/orphaned Sessions

        Args:
            session_id: Session ID to validate

        Returns:
            True wenn Session existiert, False sonst
        """
        try:
            db = getattr(self.manager, 'db', None)
            if db is None:
                logger.error("Session-Validierung ohne Datenbank ist nicht möglich")
                return False

            with db.get_connection() as conn:
                result = conn.execute(
                    "SELECT 1 FROM wellbeing_sessions WHERE id = ? LIMIT 1",
                    (session_id,),
                ).fetchone()
                if result:
                    logger.debug(f"✓ Session {session_id[:12]}... existiert in DB")
                    return True

            logger.info(f"Session {session_id[:12]}... nicht in DB (Recovery folgt)")
            return False

        except Exception as e:
            logger.error(f"❌ Session-Validierung fehlgeschlagen: {e}")
            return False

    # ──────────────────────────────────────────────────────────────────────
    # Legacy-Methoden (unchanged)
    # ──────────────────────────────────────────────────────────────────────

    def get_or_create_session(self, user_name: str, force_new: bool = False) -> str:
        """
        Get existing session or create new one (old API → new API).

        Args:
            user_name: Username
            force_new: If True, always create a new session (no restore)

        Returns:
            Session ID string
        """
        if force_new:
            result = self.manager.create_or_restore_session(user_name, restore_if_recent=False)
            return str(result)
        else:
            result = self.manager.create_or_restore_session(user_name, restore_if_recent=True)
            return str(result)

    def create_session(self, user_name: str, force_new: bool = False) -> str:
        """Create a session."""
        return self.get_or_create_session(user_name, force_new=force_new)

    def get_session_summary(self, session_id: str) -> Optional[Dict[str, Any]]:
        """
        Get session summary (old API → new API with data conversion).

        Args:
            session_id: Session ID

        Returns:
            Dictionary with session summary or None if not found
        """
        try:
            context = self.manager.get_session_context(session_id)
            if not context:
                return None

            # Convert SessionContext → Dictionary (not Dataclass!)
            return {
                'session_id': context.session_id,
                'user_id': context.user_id,  # ✅ CRITICAL: Also return user_id!
                'user_name': context.user_id,  # user_id is also used as user_name
                'start_time': context.start_time,
                'last_activity': context.last_interaction,
                'message_count': context.interaction_count,
                'key_topics': context.care_notes or [],
                'emotional_state': context.mood_trend or "neutral",
                'session_summary': context.summary or "",
                'is_active': True
            }
        except Exception as e:
            logger.error(f"Fehler beim Abrufen der Session-Summary: {e}")
            return None

    def get_session_context(self, session_id: str, max_messages: int = 20) -> List[Dict[str, Any]]:
        """
        Get session context (for chat history).

        Args:
            session_id: Session ID
            max_messages: Maximum number of messages to return

        Returns:
            List of message dictionaries
        """
        try:
            interactions = self.manager.db.get_session_interactions(session_id, decrypt=True)
            interactions = interactions[-max_messages:] if interactions else []

            messages = []
            for interaction in interactions:
                messages.append({
                    'role': interaction.get('role', 'user'),
                    'content': interaction.get('content', ''),
                    'timestamp': interaction.get('created_at') or interaction.get('timestamp'),
                    'emotional_markers': interaction.get('mood_indicators', [])
                })

            return messages
        except Exception as e:
            logger.error(f"Fehler beim Abrufen des Session-Kontexts: {e}")
            import traceback
            traceback.print_exc()
            return []

    def end_session(self, session_id: str) -> bool:
        """
        End session with LLM-based summary generation.

        Args:
            session_id: Session ID

        Returns:
            True if successful, False otherwise
        """
        try:
            # ✅ FIX: Use close_session() from SessionManager for LLM summary!
            if hasattr(self.manager, 'close_session'):
                success = self.manager.close_session(session_id, generate_summary=True)
                if success:
                    logger.info(f"✅ Session {session_id[:12]}... beendet mit LLM-Summary")
                    return True
                else:
                    logger.warning(f"⚠️ close_session() fehlgeschlagen, Fallback auf end_time setzen")

            # Fallback: Only set end_time (if close_session not available)
            if POOL_AVAILABLE and ConnectionPoolType is not None:
                pool = ConnectionPoolType(self.manager.db.db_path)
                with pool.get_connection() as conn:
                    conn.execute(
                        "UPDATE wellbeing_sessions SET end_time = CURRENT_TIMESTAMP, "
                        "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                        (session_id,)
                    )
                    conn.commit()
            else:
                with sqlite3.connect(self.manager.db.db_path) as conn:
                    conn.execute(
                        "UPDATE wellbeing_sessions SET end_time = CURRENT_TIMESTAMP, "
                        "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                        (session_id,)
                    )
                    conn.commit()

            logger.info(f"✅ Session {session_id[:12]}... beendet (end_time gesetzt, keine Summary)")
            return True

        except Exception as e:
            logger.error(f"❌ Fehler beim Beenden der Session: {e}")
            import traceback
            traceback.print_exc()
            return False

    def get_user_sessions(self, user_name: str, limit: int = 10,
                         status: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Get all sessions for a user.

        Args:
            user_name: Username
            limit: Maximum number of sessions to return
            status: Optional status filter ('active', 'ended', etc.).
                    None = ALL sessions (inkl. ended/previous sessions).

        Returns:
            List of session dictionaries
        """
        try:
            resolved_user_id = self.resolve_user_id(user_name)
            sessions = self.manager.db.get_user_sessions(resolved_user_id, status=status)
            return sessions[:limit] if sessions else []
        except Exception as e:
            logger.error(f"Fehler beim Abrufen der User-Sessions: {e}")
            return []

    def get_user_profile(self, user_name: str) -> Optional[Dict[str, Any]]:
        """Return the canonical profile for the selected user."""
        try:
            resolved_user_id = self.resolve_user_id(user_name)
            if hasattr(self.manager, 'get_user_profile'):
                return self.manager.get_user_profile(resolved_user_id)
            return None
        except Exception as e:
            logger.error(f"Fehler beim Abrufen des User-Profils: {e}")
            return None

    def get_user_insights(
        self,
        user_name: str,
        session_id: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Return normalized insights scoped to exactly one user."""
        try:
            resolved_user_id = self.resolve_user_id(user_name)
            return self.manager.db.get_user_insights(
                user_id=resolved_user_id,
                session_id=session_id,
                limit=limit,
            )
        except Exception as e:
            logger.error(f"Fehler beim Abrufen der User-Insights: {e}")
            return []

    def cleanup_old_sessions(self, days: int = 90) -> int:
        """
        Cleanup old sessions (DB + Memory).

        Args:
            days: Delete sessions older than this many days

        Returns:
            Number of sessions cleaned up
        """
        try:
            if hasattr(self.manager, 'cleanup_old_sessions'):
                result = self.manager.cleanup_old_sessions(days)
                return int(result) if result is not None else 0

            logger.warning(f"⚠️ cleanup_old_sessions() nicht verfügbar im SessionManager")
            return 0
        except Exception as e:
            logger.error(f"Fehler beim Cleanup: {e}")
            return 0

    def set_model_loader(self, model_loader: Any) -> None:
        """
        Set ModelLoader for KG extraction and LLM features.

        Args:
            model_loader: ModelLoader instance
        """
        try:
            if hasattr(self.manager, 'set_model_loader'):
                self.manager.set_model_loader(model_loader)
                logger.info("✅ ModelLoader im SessionManager gesetzt")
        except Exception as e:
            logger.error(f"❌ Fehler beim Setzen des ModelLoaders: {e}")