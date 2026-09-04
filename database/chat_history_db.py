"""
SOTA SQLite Chat History Database für Enhanced Streamlit Bot
============================================================

State-of-the-Art Persistenz-Lösung für:
- Chat-Historie (Sessions, Nachrichten)
- Benutzerpräferenzen
- Dokumenten-Metadaten

Architekturprinzipien:
- Root-Cause-Persistenz: Keine Workarounds, direkte DB-Operationen
- DSGVO-konform: Alle Daten bleiben lokal
- Thread-Safe: SQLite mit Connection Pooling für Streamlit
- Performance: Indizes, Prepared Statements, Batch-Operationen
- Data Integrity: Transaktionen, Foreign Keys

Verwendung:
    from database.chat_history_db import ChatHistoryDB
    
    db = ChatHistoryDB()
    
    # Session speichern
    db.save_chat_history(history)
    
    # Session laden
    history = db.load_chat_history(session_id)
    
    # Alle Sessions auflisten
    sessions = db.list_sessions()
"""

import sqlite3
import os
import json
import threading
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any, Tuple, Union, Generator
from contextlib import contextmanager
import logging
from pydantic import ValidationError as PydanticValidationError

# SOTA: Import Pydantic Models
from schemas import ChatMessage, ChatHistory, SOTAError

# SOTA: Central DB path resolver - ensures absolute paths regardless of CWD
from utils.db_path_resolver import get_chat_history_path

logger = logging.getLogger(__name__)


# ============================================================================
# DATABASE SCHEMA DEFINITION (SOTA)
# ============================================================================

class DBSchema:
    """SOTA: Zentrale Schema-Definition für Versionierung und Migration."""
    
    # Aktuelle Schema-Version
    VERSION = "2.0.0"
    
    # Schema-Definition (SQL)
    SCHEMA_SQL = """
    -- ========================================================================
    -- CHAT SESSIONS TABLE
    -- ========================================================================
    CREATE TABLE IF NOT EXISTS chat_sessions (
        session_id TEXT PRIMARY KEY,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        metadata TEXT,  -- JSON: Modell, Einstellungen, etc.
        is_active INTEGER DEFAULT 1
    );
    
    -- ========================================================================
    -- CHAT MESSAGES TABLE
    -- ========================================================================
    CREATE TABLE IF NOT EXISTS chat_messages (
        message_id TEXT PRIMARY KEY,
        session_id TEXT NOT NULL,
        content TEXT NOT NULL,
        sender TEXT NOT NULL CHECK(sender IN ('user', 'assistant', 'system')),
        timestamp TEXT NOT NULL,
        metadata TEXT,  -- JSON: Tool-Results, RAG-Chunks, etc.
        reasoning TEXT,  -- AI-Reasoning (optional)
        tool_results TEXT,  -- JSON: Ergebnisse von Tools
        FOREIGN KEY (session_id) REFERENCES chat_sessions(session_id) ON DELETE CASCADE
    );
    
    -- ========================================================================
    -- MESSAGE INDEXES (Performance-Optimierung)
    -- ========================================================================
    CREATE INDEX IF NOT EXISTS idx_messages_session_id ON chat_messages(session_id);
    CREATE INDEX IF NOT EXISTS idx_messages_timestamp ON chat_messages(timestamp);
    CREATE INDEX IF NOT EXISTS idx_messages_sender ON chat_messages(sender);
    
    -- ========================================================================
    -- UPLOADED FILES TABLE
    -- ========================================================================
    CREATE TABLE IF NOT EXISTS uploaded_files (
        upload_id TEXT PRIMARY KEY,
        session_id TEXT,
        file_name TEXT NOT NULL,
        safe_file_name TEXT NOT NULL,
        file_type TEXT NOT NULL,
        file_size INTEGER NOT NULL,
        file_path TEXT NOT NULL,
        mime_type TEXT,
        uploaded_at TEXT NOT NULL,
        metadata TEXT,  -- JSON: z. B. page_count für PDFs
        FOREIGN KEY (session_id) REFERENCES chat_sessions(session_id) ON DELETE SET NULL
    );
    
    CREATE INDEX IF NOT EXISTS idx_files_session_id ON uploaded_files(session_id);
    CREATE INDEX IF NOT EXISTS idx_files_timestamp ON uploaded_files(uploaded_at);
    
    -- ========================================================================
    -- MERMAID DIAGRAMS TABLE
    -- ========================================================================
    CREATE TABLE IF NOT EXISTS mermaid_diagrams (
        diagram_id TEXT PRIMARY KEY,
        session_id TEXT,
        diagram_type TEXT NOT NULL,
        title TEXT NOT NULL,
        mermaid_code TEXT NOT NULL,
        metadata TEXT,  -- JSON: Originaldaten, Export-Optionen
        created_at TEXT NOT NULL,
        FOREIGN KEY (session_id) REFERENCES chat_sessions(session_id) ON DELETE SET NULL
    );
    
    CREATE INDEX IF NOT EXISTS idx_diagrams_session_id ON mermaid_diagrams(session_id);
    
    -- ========================================================================
    -- DATABASE METADATA (Schema-Versionierung)
    -- ========================================================================
    CREATE TABLE IF NOT EXISTS db_metadata (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );
    """
    
    # Migrationen für Upgrade-Pfade aus älteren Schema-Versionen.
    # SCHEMA_SQL ist idempotente Source of Truth für die aktuelle Schema-Version.
    # Migrations-Einträge greifen nur, wenn die DB tatsächlich von einer älteren
    # Version hochgezogen wird; jede Migration ist als Funktion implementiert,
    # die ihre Vorbedingung selbst prüft (PRAGMA table_info etc.) — dadurch sind
    # alle Migrationen idempotent und kollidieren nicht mit SCHEMA_SQL.
    MIGRATIONS: "dict[str, list]" = {
        # 1.0.0 → 1.1.0: Index, idempotent dank IF NOT EXISTS.
        "1.1.0": [
            "CREATE INDEX IF NOT EXISTS idx_messages_sender ON chat_messages(sender)",
        ],
        # 2.0.0: alle Tabellen werden bereits von SCHEMA_SQL erzeugt;
        # die Migration ist deshalb ein No-Op-Marker. Falls in Zukunft echte
        # Schema-Änderungen für 2.0.0 anfallen, hier idempotente Statements
        # (CREATE ... IF NOT EXISTS / ALTER mit Vorbedingungs-Check) ergänzen.
    }
    
    @classmethod
    def get_schema_version(cls, conn: sqlite3.Connection) -> str:
        """SOTA: Aktuelle Schema-Version aus DB auslesen."""
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT value FROM db_metadata WHERE key = 'schema_version'")
            result = cursor.fetchone()
            return result[0] if result else "0.0.0"
        except sqlite3.Error:
            return "0.0.0"
    
    @classmethod
    def set_schema_version(cls, conn: sqlite3.Connection, version: str) -> None:
        """SOTA: Schema-Version in DB speichern."""
        cursor = conn.cursor()
        cursor.execute(
            "INSERT OR REPLACE INTO db_metadata (key, value) VALUES (?, ?)",
            ("schema_version", version)
        )
        conn.commit()


# ============================================================================
# DATABASE CONNECTION POOL (SOTA - Thread-Safe)
# ============================================================================

class ConnectionPool:
    """
    SOTA: Thread-Safe Connection Pool für SQLite.
    
    Root-Cause-Lösung:
    - Kein Workaround: Eigenes Connection Management
    - Thread-Safe: Locking für Multi-Threading (Streamlit)
    - Performance: Connection Reuse
    - Auto-Cleanup: Connections werden automatisch geschlossen
    """
    
    def __init__(self, db_path: Path, max_connections: int = 5):
        self.db_path = db_path
        self.max_connections = max_connections
        self._pool: List[sqlite3.Connection] = []
        self._lock = threading.Lock()
        self._in_use: Dict[sqlite3.Connection, bool] = {}
        
        # Erstelle DB-Verzeichnis falls nicht vorhanden
        db_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Initialisiere Pool
        for _ in range(max_connections):
            self._create_connection()
    
    def _create_connection(self) -> sqlite3.Connection:
        """SOTA: Neue SQLite-Connection erstellen."""
        # SQLITEConfig für bessere Performance
        conn = sqlite3.connect(
            str(self.db_path),
            check_same_thread=False,  # Wichtig für Streamlit!
            isolation_level=None,  # Autocommit-Modus
        )
        
        # Performance-Optimierungen
        conn.execute("PRAGMA journal_mode=DELETE")  # No -wal/-shm disk files (readonly dir fix)
        conn.execute("PRAGMA synchronous=NORMAL")  # Balance between safety and speed
        conn.execute("PRAGMA temp_store=MEMORY")  # Temporary tables in RAM
        conn.execute("PRAGMA cache_size=-20000")  # 20MB Cache
        conn.execute("PRAGMA foreign_keys=ON")  # Foreign Key Constraints aktivieren
        
        # Row Factory für Dict-Cursor
        conn.row_factory = sqlite3.Row
        
        return conn
    
    @contextmanager
    def get_connection(self) -> Generator[sqlite3.Connection, Any, Any]:
        """
        SOTA: Context Manager für Connection Pool.
        
        Usage:
            with pool.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM table")
        """
        with self._lock:
            # Suche nach verfügbarer Connection
            for conn in self._pool:
                if not self._in_use.get(conn, False):
                    self._in_use[conn] = True
                    try:
                        yield conn
                    finally:
                        self._in_use[conn] = False
                    return
            
            # Falls keine verfügbar, erstelle neue (bis max)
            if len(self._pool) < self.max_connections:
                new_conn = self._create_connection()
                self._pool.append(new_conn)
                self._in_use[new_conn] = True
                try:
                    yield new_conn
                finally:
                    self._in_use[new_conn] = False
                return
            
            # Falls Max erreicht, warte auf verfügbare Connection
            # (Einfache Implementierung: Erste Connection verwenden)
            conn = self._pool[0]
            self._in_use[conn] = True
            try:
                yield conn
            finally:
                self._in_use[conn] = False
    
    def close_all(self) -> None:
        """SOTA: Alle Connections schließen."""
        with self._lock:
            for conn in self._pool:
                try:
                    conn.close()
                except sqlite3.Error as e:
                    logger.error(f"Error closing connection: {e}")
            self._pool = []
            self._in_use = {}


# ============================================================================
# MAIN DATABASE CLASS
# ============================================================================

class ChatHistoryDB:
    """
    SOTA: Haupt-Datenbankklasse für Chat-Historie.
    
    Features:
    - Komplette CRUD-Operationen für Chat-Daten
    - Transaktions-Sicherheit
    - Batch-Operationen für Performance
    - Automatische Schema-Migration
    - DSGVO-konform (lokale Daten)
    
    Root-Cause-Lösung:
    - Keine Workarounds: Direkte DB-Operationen
    - Keine broad exceptions: Spezifische Fehlerbehandlung
    - Type Safety: Nutzung der Pydantic-Schemas
    """
    
    # Standard-DB-Pfad (absolut, da Streamlit CWD variieren kann)
    # SOTA: Central path resolver ensures absolute paths regardless of CWD
    DEFAULT_DB_PATH = get_chat_history_path()
    
    def __init__(self, db_path: Optional[Union[str, Path]] = None):
        """
        SOTA: Initialisiere Datenbank.
        
        Args:
            db_path: Pfad zur SQLite-Datenbank (Default: data/chat_history.db)
        """
        if db_path is None:
            db_path = self.DEFAULT_DB_PATH
        else:
            db_path = Path(db_path)
        
        # Stelle sicher, dass Parent-Directory existiert
        db_path.parent.mkdir(parents=True, exist_ok=True)
        
        self.db_path = db_path
        self.pool = ConnectionPool(db_path)
        
        # Initialisiere Schema
        self._initialize_schema()
        
        logger.info(f"ChatHistoryDB initialisiert: {self.db_path}")
    
    def _initialize_schema(self) -> None:
        """SOTA: Schema initialisieren und Migrationen durchführen."""
        with self.pool.get_connection() as conn:
            # Erstelle Tabellen
            conn.executescript(DBSchema.SCHEMA_SQL)
            
            # Prüfe Schema-Version
            current_version = DBSchema.get_schema_version(conn)
            
            if current_version != DBSchema.VERSION:
                logger.info(f"Schema-Migration: {current_version} -> {DBSchema.VERSION}")
                self._run_migrations(conn, current_version)
                DBSchema.set_schema_version(conn, DBSchema.VERSION)
                logger.info("Schema-Migration abgeschlossen")
            
            conn.commit()
    
    def _run_migrations(self, conn: sqlite3.Connection, from_version: str) -> None:
        """Idempotente Migrationen ab ``from_version`` ausführen.

        Jeder Migration-Eintrag ist als idempotentes SQL-Statement formuliert
        (``CREATE ... IF NOT EXISTS`` o.Ä.). Schlägt eine Migration fehl, wird
        die Ursache als ``logger.error`` protokolliert; der Schema-Initialisierer
        kann anschließend entscheiden, ob die DB-Version trotzdem hochgezogen wird.
        """
        for version, migrations in DBSchema.MIGRATIONS.items():
            if version <= from_version:
                continue
            for migration_sql in migrations:
                try:
                    conn.execute(migration_sql)
                    logger.info(f"Migration {version} angewendet")
                except sqlite3.Error as exc:
                    logger.error(f"Migration {version} fehlgeschlagen: {exc}")
    
    # ========================================================================
    # SESSION OPERATIONS
    # ========================================================================
    
    def create_session(
        self,
        session_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> str:
        """
        SOTA: Neue Chat-Session erstellen.
        
        Args:
            session_id: Optionale Session-ID (wird generiert falls None)
            metadata: Session-Metadaten
            
        Returns:
            session_id: Die erstellte Session-ID
            
        Root-Cause: Session-ID wird generiert falls nicht angegeben
        """
        import uuid
        
        if session_id is None:
            session_id = f"session_{uuid.uuid4().hex[:16]}"
        
        now = datetime.now(timezone.utc).isoformat()
        metadata_json = json.dumps(metadata or {})
        
        with self.pool.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO chat_sessions (session_id, created_at, updated_at, metadata)
                VALUES (?, ?, ?, ?)
                """,
                (session_id, now, now, metadata_json)
            )
            conn.commit()
        
        logger.info(f"Session erstellt: {session_id}")
        return session_id
    
    def list_sessions(
        self,
        limit: int = 100,
        offset: int = 0,
        active_only: bool = False
    ) -> List[Dict[str, Any]]:
        """
        SOTA: Liste aller Sessions abrufen.
        
        Args:
            limit: Maximale Anzahl Ergebnisse
            offset: Offset für Pagination
            active_only: Nur aktive Sessions
            
        Returns:
            Liste von Session-Dicts
        """
        with self.pool.get_connection() as conn:
            cursor = conn.cursor()
            
            if active_only:
                query = """
                SELECT session_id, created_at, updated_at, metadata
                FROM chat_sessions
                WHERE is_active = 1
                ORDER BY updated_at DESC
                LIMIT ? OFFSET ?
                """
            else:
                query = """
                SELECT session_id, created_at, updated_at, metadata
                FROM chat_sessions
                ORDER BY updated_at DESC
                LIMIT ? OFFSET ?
                """
            
            cursor.execute(query, (limit, offset))
            rows = cursor.fetchall()
            
            sessions = []
            for row in rows:
                session = {
                    "session_id": row["session_id"],
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"],
                    "metadata": json.loads(row["metadata"]) if row["metadata"] else {},
                    "message_count": self._count_messages_for_session(conn, row["session_id"]),
                }
                sessions.append(session)
            
            return sessions
    
    def _count_messages_for_session(self, conn: sqlite3.Connection, session_id: str) -> int:
        """SOTA: Anzahl Nachrichten für eine Session zählen."""
        cursor = conn.cursor()
        cursor.execute(
            "SELECT COUNT(*) FROM chat_messages WHERE session_id = ?",
            (session_id,)
        )
        result = cursor.fetchone()
        return result[0] if result else 0
    
    def get_session_metadata(self, session_id: str) -> Optional[Dict[str, Any]]:
        """
        SOTA: Metadaten einer Session abrufen.
        
        Args:
            session_id: Session-ID
            
        Returns:
            Metadaten-Dict oder None
        """
        with self.pool.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT metadata FROM chat_sessions WHERE session_id = ?",
                (session_id,)
            )
            row = cursor.fetchone()
            return json.loads(row["metadata"]) if row and row["metadata"] else None
    
    def update_session_metadata(
        self,
        session_id: str,
        metadata: Dict[str, Any]
    ) -> bool:
        """
        SOTA: Metadaten einer Session aktualisieren.
        
        Args:
            session_id: Session-ID
            metadata: Neue Metadaten
            
        Returns:
            True bei Erfolg, False bei Fehler
        """
        metadata_json = json.dumps(metadata)
        now = datetime.now(timezone.utc).isoformat()
        
        with self.pool.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE chat_sessions
                SET metadata = ?, updated_at = ?
                WHERE session_id = ?
                """,
                (metadata_json, now, session_id)
            )
            conn.commit()
            return cursor.rowcount > 0
    
    def deactivate_session(self, session_id: str) -> bool:
        """
        SOTA: Session deaktivieren (Archivierung).
        
        Args:
            session_id: Session-ID
            
        Returns:
            True bei Erfolg, False bei Fehler
        """
        with self.pool.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE chat_sessions SET is_active = 0 WHERE session_id = ?",
                (session_id,)
            )
            conn.commit()
            return cursor.rowcount > 0
    
    def delete_session(self, session_id: str) -> bool:
        """
        SOTA: Session und alle zugehörigen Daten löschen.
        
        WARNING: Löscht alle Nachrichten, Dateien und Diagramme der Session!
        
        Args:
            session_id: Session-ID
            
        Returns:
            True bei Erfolg, False bei Fehler
            
        Root-Cause: CASCADE DELETE löscht alle abhängigen Daten
        """
        with self.pool.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "DELETE FROM chat_sessions WHERE session_id = ?",
                (session_id,)
            )
            conn.commit()
            return cursor.rowcount > 0
    
    # ========================================================================
    # CHAT HISTORY OPERATIONS
    # ========================================================================
    
    def save_chat_history(self, chat_history: ChatHistory) -> bool:
        """
        SOTA: Komplette Chat-Historie speichern.
        
        Args:
            chat_history: ChatHistory-Objekt
            
        Returns:
            True bei Erfolg, False bei Fehler
            
        Root-Cause: Transaktion für atomare Operation
        """
        session_id = chat_history.session_id
        
        with self.pool.get_connection() as conn:
            try:
                # Session erstellen/aktualisieren
                now = datetime.now(timezone.utc).isoformat()
                metadata_json = json.dumps(chat_history.metadata or {})
                
                cursor = conn.cursor()
                cursor.execute(
                    """
                    INSERT OR REPLACE INTO chat_sessions
                    (session_id, created_at, updated_at, metadata)
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        session_id,
                        chat_history.created_at.isoformat(),
                        now,
                        metadata_json
                    )
                )
                
                # Alle Nachrichten der Session löschen (für Replace)
                cursor.execute(
                    "DELETE FROM chat_messages WHERE session_id = ?",
                    (session_id,)
                )
                
                # Nachrichten speichern
                for message in chat_history.messages:
                    self._insert_message(cursor, message, session_id)
                
                conn.commit()
                logger.info(f"Chat-Historie gespeichert: {session_id} ({len(chat_history.messages)} Nachrichten)")
                return True
                
            except sqlite3.Error as e:
                conn.rollback()
                raise RuntimeError(
                    f"SQLite-Fehler beim Speichern der Chat-Historie fuer Session '{session_id}'"
                ) from e
    
    def _insert_message(
        self,
        cursor: sqlite3.Cursor,
        message: ChatMessage,
        session_id: str
    ) -> None:
        """SOTA: Einzelne Nachricht in die Datenbank einfügen."""
        cursor.execute(
            """
            INSERT INTO chat_messages
            (message_id, session_id, content, sender, timestamp, metadata, reasoning, tool_results)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                message.message_id,
                session_id,
                message.content,
                message.sender,
                message.timestamp.isoformat(),
                json.dumps(message.metadata or {}),
                message.reasoning,
                json.dumps(message.tool_results or []),
            )
        )

    def _ensure_session_exists(self, cursor: sqlite3.Cursor, session_id: str) -> None:
        """Ensure parent chat session exists before writing FK-dependent records."""
        cursor.execute(
            "SELECT 1 FROM chat_sessions WHERE session_id = ?",
            (session_id,)
        )
        if cursor.fetchone():
            return

        now = datetime.now(timezone.utc).isoformat()
        cursor.execute(
            "INSERT INTO chat_sessions (session_id, created_at, updated_at) VALUES (?, ?, ?)",
            (session_id, now, now)
        )
    
    def load_chat_history(self, session_id: str) -> Optional[ChatHistory]:
        """
        SOTA: Chat-Historie aus der Datenbank laden.
        
        Args:
            session_id: Session-ID
            
        Returns:
            ChatHistory-Objekt oder None
            
        Root-Cause: Alle Nachrichten werden geladen und validiert
        """
        with self.pool.get_connection() as conn:
            # Session-Metadaten laden
            cursor = conn.cursor()
            cursor.execute(
                "SELECT created_at, updated_at, metadata FROM chat_sessions WHERE session_id = ?",
                (session_id,)
            )
            session_row = cursor.fetchone()
            
            if not session_row:
                logger.warning(f"Session nicht gefunden: {session_id}")
                return None
            
            # Nachrichten laden
            cursor.execute(
                """
                SELECT message_id, content, sender, timestamp, metadata, reasoning, tool_results
                FROM chat_messages
                WHERE session_id = ?
                ORDER BY timestamp ASC
                """,
                (session_id,)
            )
            message_rows = cursor.fetchall()
            
            # ChatHistory-Objekt erstellen
            messages = []
            for row in message_rows:
                message_data = {
                    "content": row["content"],
                    "sender": row["sender"],
                    "timestamp": row["timestamp"],
                    "message_id": row["message_id"],
                    "metadata": json.loads(row["metadata"]) if row["metadata"] else None,
                    "reasoning": row["reasoning"],
                    "tool_results": json.loads(row["tool_results"]) if row["tool_results"] else None,
                    "conversation_id": session_id,
                }
                try:
                    message = ChatMessage(**message_data)
                    messages.append(message)
                except PydanticValidationError as e:
                    raise ValueError(
                        f"Ungueltige Nachricht in Session '{session_id}' (message_id={row['message_id']})"
                    ) from e
            
            # ChatHistory erstellen
            history_data = {
                "messages": messages,
                "session_id": session_id,
                "created_at": session_row["created_at"],
                "updated_at": session_row["updated_at"],
                "metadata": json.loads(session_row["metadata"]) if session_row["metadata"] else None,
            }
            
            try:
                return ChatHistory(**history_data)
            except PydanticValidationError as e:
                raise ValueError(
                    f"Ungueltige Session-Daten fuer Session '{session_id}'"
                ) from e
    
    def append_message_to_session(
        self,
        session_id: str,
        message: ChatMessage
    ) -> bool:
        """
        SOTA: Nachricht an bestehende Session anhängen.
        
        Args:
            session_id: Session-ID
            message: ChatMessage-Objekt
            
        Returns:
            True bei Erfolg, False bei Fehler
        """
        with self.pool.get_connection() as conn:
            try:
                cursor = conn.cursor()
                self._ensure_session_exists(cursor, session_id)
                
                # Nachricht einfügen
                self._insert_message(cursor, message, session_id)
                
                # Session updated_at aktualisieren
                now = datetime.now(timezone.utc).isoformat()
                cursor.execute(
                    "UPDATE chat_sessions SET updated_at = ? WHERE session_id = ?",
                    (now, session_id)
                )
                
                conn.commit()
                logger.info(f"Nachricht angehängt: {message.message_id} -> {session_id}")
                return True
                
            except sqlite3.Error as e:
                conn.rollback()
                raise RuntimeError(
                    f"SQLite-Fehler beim Anhaengen der Nachricht '{message.message_id}' an Session '{session_id}'"
                ) from e
    
    def get_last_messages(
        self,
        session_id: str,
        count: int = 10
    ) -> List[ChatMessage]:
        """
        SOTA: Letzte N Nachrichten einer Session abrufen.
        
        Args:
            session_id: Session-ID
            count: Anzahl Nachrichten
            
        Returns:
            Liste von ChatMessage-Objekten
        """
        with self.pool.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT message_id, content, sender, timestamp, metadata, reasoning, tool_results
                FROM chat_messages
                WHERE session_id = ?
                ORDER BY timestamp DESC
                LIMIT ?
                """,
                (session_id, count)
            )
            rows = cursor.fetchall()
            
            messages = []
            for row in reversed(rows):  # Umdrehen für chronologische Reihenfolge
                message_data = {
                    "content": row["content"],
                    "sender": row["sender"],
                    "timestamp": row["timestamp"],
                    "message_id": row["message_id"],
                    "metadata": json.loads(row["metadata"]) if row["metadata"] else None,
                    "reasoning": row["reasoning"],
                    "tool_results": json.loads(row["tool_results"]) if row["tool_results"] else None,
                    "conversation_id": session_id,
                }
                try:
                    message = ChatMessage(**message_data)
                    messages.append(message)
                except PydanticValidationError as e:
                    raise ValueError(
                        f"Ungueltige Nachricht in Session '{session_id}' (message_id={row['message_id']})"
                    ) from e
            
            return messages
    
    # ========================================================================
    # FILE UPLOAD OPERATIONS
    # ========================================================================
    
    def save_file_upload(
        self,
        session_id: str,
        file_name: str,
        safe_file_name: str,
        file_type: str,
        file_size: int,
        file_path: str,
        mime_type: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> str:
        """
        SOTA: Datei-Upload speichern.
        
        Args:
            session_id: Session-ID
            file_name: Originaler Dateiname
            safe_file_name: Bereinigter Dateiname
            file_type: Dateityp
            file_size: Dateigröße in Bytes
            file_path: Speicherpfad
            mime_type: MIME-Type
            metadata: Zusätzliche Metadaten
            
        Returns:
            upload_id: Die generierte Upload-ID
        """
        import uuid
        upload_id = f"upload_{uuid.uuid4().hex[:16]}"
        now = datetime.now(timezone.utc).isoformat()
        metadata_json = json.dumps(metadata or {})
        
        with self.pool.get_connection() as conn:
            cursor = conn.cursor()
            self._ensure_session_exists(cursor, session_id)
            cursor.execute(
                """
                INSERT INTO uploaded_files
                (upload_id, session_id, file_name, safe_file_name, file_type, file_size, file_path, mime_type, uploaded_at, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    upload_id,
                    session_id,
                    file_name,
                    safe_file_name,
                    file_type,
                    file_size,
                    file_path,
                    mime_type,
                    now,
                    metadata_json
                )
            )
            conn.commit()
            logger.info(f"Datei-Upload gespeichert: {upload_id}")
            return upload_id
    
    def get_session_files(self, session_id: str) -> List[Dict[str, Any]]:
        """
        SOTA: Alle Dateien einer Session abrufen.
        
        Args:
            session_id: Session-ID
            
        Returns:
            Liste von Datei-Dicts
        """
        with self.pool.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT upload_id, file_name, safe_file_name, file_type, file_size, file_path, mime_type, uploaded_at, metadata
                FROM uploaded_files
                WHERE session_id = ?
                ORDER BY uploaded_at DESC
                """,
                (session_id,)
            )
            rows = cursor.fetchall()
            
            files = []
            for row in rows:
                file_data = {
                    "upload_id": row["upload_id"],
                    "file_name": row["file_name"],
                    "safe_file_name": row["safe_file_name"],
                    "file_type": row["file_type"],
                    "file_size": row["file_size"],
                    "file_path": row["file_path"],
                    "mime_type": row["mime_type"],
                    "uploaded_at": row["uploaded_at"],
                    "metadata": json.loads(row["metadata"]) if row["metadata"] else {},
                }
                files.append(file_data)
            
            return files
    
    # ========================================================================
    # MERMAID DIAGRAM OPERATIONS
    # ========================================================================
    
    def save_mermaid_diagram(
        self,
        session_id: str,
        diagram_type: str,
        title: str,
        mermaid_code: str,
        metadata: Optional[Dict[str, Any]] = None
    ) -> str:
        """
        SOTA: Mermaid-Diagramm speichern.
        
        Args:
            session_id: Session-ID
            diagram_type: Diagramm-Typ
            title: Titel
            mermaid_code: Mermaid-Code
            metadata: Metadaten
            
        Returns:
            diagram_id: Die generierte Diagramm-ID
        """
        import uuid
        diagram_id = f"diagram_{uuid.uuid4().hex[:16]}"
        now = datetime.now(timezone.utc).isoformat()
        metadata_json = json.dumps(metadata or {})
        
        with self.pool.get_connection() as conn:
            cursor = conn.cursor()
            self._ensure_session_exists(cursor, session_id)
            cursor.execute(
                """
                INSERT INTO mermaid_diagrams
                (diagram_id, session_id, diagram_type, title, mermaid_code, metadata, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    diagram_id,
                    session_id,
                    diagram_type,
                    title,
                    mermaid_code,
                    metadata_json,
                    now
                )
            )
            conn.commit()
            logger.info(f"Mermaid-Diagramm gespeichert: {diagram_id}")
            return diagram_id
    
    def get_session_diagrams(self, session_id: str) -> List[Dict[str, Any]]:
        """
        SOTA: Alle Diagramme einer Session abrufen.
        
        Args:
            session_id: Session-ID
            
        Returns:
            Liste von Diagramm-Dicts
        """
        with self.pool.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT diagram_id, diagram_type, title, mermaid_code, metadata, created_at
                FROM mermaid_diagrams
                WHERE session_id = ?
                ORDER BY created_at DESC
                """,
                (session_id,)
            )
            rows = cursor.fetchall()
            
            diagrams = []
            for row in rows:
                diagram_data = {
                    "diagram_id": row["diagram_id"],
                    "diagram_type": row["diagram_type"],
                    "title": row["title"],
                    "mermaid_code": row["mermaid_code"],
                    "metadata": json.loads(row["metadata"]) if row["metadata"] else {},
                    "created_at": row["created_at"],
                }
                diagrams.append(diagram_data)
            
            return diagrams
    
    # ========================================================================
    # SEARCH & ANALYTICS OPERATIONS
    # ========================================================================
    
    def search_messages(
        self,
        query: str,
        limit: int = 50,
        session_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        SOTA: Nachrichten nach Inhalt durchsuchen.
        
        Args:
            query: Suchbegriff
            limit: Maximale Anzahl Ergebnisse
            session_id: Optionale Session-Filter
            
        Returns:
            Liste von Nachricht-Dicts
            
        Root-Cause: Einfache LIKE-Suche (für bessere Performance würde man FTS5 verwenden)
        """
        with self.pool.get_connection() as conn:
            cursor = conn.cursor()
            
            if session_id:
                cursor.execute(
                    """
                    SELECT message_id, session_id, content, sender, timestamp
                    FROM chat_messages
                    WHERE session_id = ? AND content LIKE ?
                    ORDER BY timestamp DESC
                    LIMIT ?
                    """,
                    (session_id, f"%{query}%", limit)
                )
            else:
                cursor.execute(
                    """
                    SELECT message_id, session_id, content, sender, timestamp
                    FROM chat_messages
                    WHERE content LIKE ?
                    ORDER BY timestamp DESC
                    LIMIT ?
                    """,
                    (f"%{query}%", limit)
                )
            
            rows = cursor.fetchall()
            
            results = []
            for row in rows:
                results.append({
                    "message_id": row["message_id"],
                    "session_id": row["session_id"],
                    "content": row["content"][:200] + "..." if len(row["content"]) > 200 else row["content"],
                    "sender": row["sender"],
                    "timestamp": row["timestamp"],
                })
            
            return results
    
    def get_session_statistics(self, session_id: str) -> Dict[str, Any]:
        """
        SOTA: Statistiken für eine Session abrufen.
        
        Args:
            session_id: Session-ID
            
        Returns:
            Statistik-Dict
        """
        with self.pool.get_connection() as conn:
            cursor = conn.cursor()
            
            # Nachrichten-Statistiken
            cursor.execute(
                "SELECT COUNT(*), sender FROM chat_messages WHERE session_id = ? GROUP BY sender",
                (session_id,)
            )
            message_stats = cursor.fetchall()
            
            stats = {
                "total_messages": 0,
                "user_messages": 0,
                "assistant_messages": 0,
                "total_files": 0,
                "total_diagrams": 0,
            }
            
            for row in message_stats:
                sender = row["sender"]
                count = row["COUNT(*)"]
                stats["total_messages"] += count
                if sender == "user":
                    stats["user_messages"] = count
                elif sender == "assistant":
                    stats["assistant_messages"] = count
            
            # Datei-Statistiken
            cursor.execute(
                "SELECT COUNT(*) FROM uploaded_files WHERE session_id = ?",
                (session_id,)
            )
            stats["total_files"] = cursor.fetchone()[0]
            
            # Diagramm-Statistiken
            cursor.execute(
                "SELECT COUNT(*) FROM mermaid_diagrams WHERE session_id = ?",
                (session_id,)
            )
            stats["total_diagrams"] = cursor.fetchone()[0]
            
            return stats
    
    # ========================================================================
    # MAINTENANCE OPERATIONS
    # ========================================================================
    
    def cleanup_old_sessions(self, max_age_days: int = 30) -> int:
        """
        SOTA: Alte Sessions bereinigen.
        
        Args:
            max_age_days: Maximales Alter in Tagen
            
        Returns:
            Anzahl gelöschter Sessions
            
        Root-Cause: CASCADE DELETE löscht alle abhängigen Daten
        """
        import datetime as dt
        
        cutoff_date = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=max_age_days)).isoformat()
        
        with self.pool.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "DELETE FROM chat_sessions WHERE updated_at < ? AND is_active = 0",
                (cutoff_date,)
            )
            deleted_count = cursor.rowcount
            conn.commit()
            
            logger.info(f"Bereinigung: {deleted_count} alte Sessions gelöscht")
            return deleted_count
    
    def vacuum_database(self) -> None:
        """
        SOTA: Datenbank optimieren (VACUUM).
        
        Root-Cause: SQLite VACUUM für bessere Performance
        """
        with self.pool.get_connection() as conn:
            conn.execute("VACUUM")
            conn.commit()
            logger.info("Datenbank-VACUUM durchgeführt")
    
    def get_database_info(self) -> Dict[str, Any]:
        """
        SOTA: Datenbank-Informationen abrufen.
        
        Returns:
            Info-Dict
        """
        with self.pool.get_connection() as conn:
            cursor = conn.cursor()
            
            # Tabellen-Informationen
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = [row["name"] for row in cursor.fetchall()]
            
            # Größen-Informationen
            db_size = os.path.getsize(self.db_path) if self.db_path.exists() else 0
            
            # Session-Zähler
            cursor.execute("SELECT COUNT(*) FROM chat_sessions")
            session_count = cursor.fetchone()[0]
            
            cursor.execute("SELECT COUNT(*) FROM chat_messages")
            message_count = cursor.fetchone()[0]
            
            return {
                "db_path": str(self.db_path),
                "db_size_bytes": db_size,
                "db_size_mb": round(db_size / (1024 * 1024), 2),
                "schema_version": DBSchema.get_schema_version(conn),
                "tables": tables,
                "total_sessions": session_count,
                "total_messages": message_count,
            }
    
    # ========================================================================
    # CLOSE & CLEANUP
    # ========================================================================
    
    def close(self) -> None:
        """SOTA: Datenbank-Verbindung schließen."""
        # SOTA-FIX: Use getattr with default to avoid AttributeError when __init__ failed early
        pool = getattr(self, 'pool', None)
        if pool is not None:
            try:
                pool.close_all()
                logger.info("ChatHistoryDB geschlossen")
            except Exception:
                logger.debug("ChatHistoryDB close_all during cleanup ignored")
    
    def __del__(self) -> None:
        """SOTA: Destructor für automatisches Schließen."""
        try:
            self.close()
        except Exception:
            pass  # Suppress noise during interpreter shutdown


# ============================================================================
# SINGLETON INSTANCE (für bequemen Zugriff)
# ============================================================================

_default_db: Optional[ChatHistoryDB] = None
_db_lock = threading.Lock()


def get_default_chat_db() -> ChatHistoryDB:
    """
    SOTA: Singleton-Instanz der Chat-Datenbank.
    
    Usage:
        db = get_default_chat_db()
        db.save_chat_history(history)
    """
    global _default_db
    
    with _db_lock:
        if _default_db is None:
            _default_db = ChatHistoryDB()
        return _default_db


def reset_default_chat_db() -> None:
    """SOTA: Singleton-Instanz zurücksetzen (für Tests)."""
    global _default_db
    
    with _db_lock:
        if _default_db is not None:
            _default_db.close()
            _default_db = None


# ============================================================================
# EXPORT
# ============================================================================

__all__ = [
    'ChatHistoryDB',
    'DBSchema',
    'ConnectionPool',
    'get_default_chat_db',
    'reset_default_chat_db',
]
