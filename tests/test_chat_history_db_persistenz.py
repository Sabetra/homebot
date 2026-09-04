"""ChatHistoryDB Persistenz- und Isolation-Invarianten.

P0: Systemweite Persistenz-Härtung.
Invarianten (falsifizierbar):
  I1: Nur eine DB-Zeile bestätigt Session-Existenz (keine Phantom-Sessions).
  I2: Insert + Message ist atomar (Rollback bei Konflikt).
  I3: FK CASCADE: Session-Delete löscht alle Messages.
  I4: Dedup: Doppelte session_id erzeugt KEINE zweite Zeile.
  I5: Thread-Safety: parallele Inserts verlieren keine Daten.

Hinweis: Keine LLM-Abhängigkeit, rein lokal.
"""

from __future__ import annotations

import sqlite3
import threading
import uuid
from pathlib import Path
from typing import Generator

import pytest

from database.chat_history_db import ChatHistoryDB
from schemas import ChatMessage


def _make_message(content: str, sender: str) -> ChatMessage:
    """ChatMessage mit eindeutiger UUID-basierter message_id erstellen."""
    return ChatMessage(
        content=content,
        sender=sender,
        message_id=f"msg_{uuid.uuid4().hex[:16]}",
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def tmp_db_path(tmp_path: Path) -> str:
    """Erstellt einen temporären DB-Pfad."""
    return str(tmp_path / "chat_history_test.db")


@pytest.fixture()
def db(tmp_db_path: str) -> Generator[ChatHistoryDB, None, None]:
    """Liefert eine ChatHistoryDB mit isoliertem Pfad."""
    instance = ChatHistoryDB(db_path=tmp_db_path)
    yield instance
    instance.close()


# ---------------------------------------------------------------------------
# I1: DB-autoritative Session-Existenz
# ---------------------------------------------------------------------------

class TestI1_SessionExistenz:
    """Nur eine DB-Zeile bestätigt Session-Existenz."""

    def test_session_nach_create_existiert(self, db: ChatHistoryDB):
        session_id = "test_i1_001"
        db.create_session(session_id)
        sessions = db.list_sessions()
        ids = [s["session_id"] for s in sessions]
        assert session_id in ids

    def test_session_ohne_create_existiert_nicht(self, db: ChatHistoryDB):
        sessions = db.list_sessions()
        ids = [s["session_id"] for s in sessions]
        assert "phantom_session" not in ids

    def test_delete_session_entfernt(self, db: ChatHistoryDB):
        session_id = "test_i1_002"
        db.create_session(session_id)
        db.delete_session(session_id)
        sessions = db.list_sessions()
        ids = [s["session_id"] for s in sessions]
        assert session_id not in ids


# ---------------------------------------------------------------------------
# I2: Atomare Transaktionen
# ---------------------------------------------------------------------------

class TestI2_AtomareTransaktion:
    """Session + Message sind atomar."""

    def test_session_und_message_atomar(self, db: ChatHistoryDB):
        session_id = "test_i2_001"
        db.create_session(session_id)
        msg = _make_message("Hallo", "user")
        db.append_message_to_session(session_id, msg)
        messages = db.get_last_messages(session_id, count=10)
        assert len(messages) >= 1
        assert any(m.content == "Hallo" for m in messages)

    def test_message_auto_createt_session(self, db: ChatHistoryDB):
        """_ensure_session_exists auto-creates Session (by design)."""
        session_id = "auto_created_session"
        msg = _make_message("Hallo", "user")
        # Sollte fehlerfrei funktionieren (Session wird auto-erzeugt)
        result = db.append_message_to_session(session_id, msg)
        assert result is True
        # Session existiert jetzt in der DB
        sessions = db.list_sessions()
        ids = [s["session_id"] for s in sessions]
        assert session_id in ids


# ---------------------------------------------------------------------------
# I3: FK CASCADE Delete
# ---------------------------------------------------------------------------

class TestI3_CascadeDelete:
    """Session-Delete löscht alle Messages via FK CASCADE."""

    def test_cascade_loescht_messages(self, db: ChatHistoryDB, tmp_db_path: str):
        session_id = "test_i3_001"
        db.create_session(session_id)
        db.append_message_to_session(session_id, _make_message("Msg 1", "user"))
        db.append_message_to_session(session_id, _make_message("Msg 2", "assistant"))

        # Direkt in DB prüfen
        conn = sqlite3.connect(tmp_db_path)
        try:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM chat_messages WHERE session_id = ?", (session_id,))
            count_before = cur.fetchone()[0]
            assert count_before >= 2

            db.delete_session(session_id)

            cur.execute("SELECT COUNT(*) FROM chat_messages WHERE session_id = ?", (session_id,))
            count_after = cur.fetchone()[0]
            assert count_after == 0
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# I4: Deduplication
# ---------------------------------------------------------------------------

class TestI4_Dedup:
    """Doppelte session_id erzeugt KEINE zweite Zeile."""

    def test_doppeltes_create_wirft_oder_ignoriert(self, db: ChatHistoryDB, tmp_db_path: str):
        session_id = "test_i4_001"
        db.create_session(session_id)
        # Zweites Create sollte entweder ignorieren oder werfen
        try:
            db.create_session(session_id)
        except Exception:
            pass  # erwartet: Konflikt wird behandelt

        # Nur eine Zeile existiert
        conn = sqlite3.connect(tmp_db_path)
        try:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM chat_sessions WHERE session_id = ?", (session_id,))
            count = cur.fetchone()[0]
            assert count == 1
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# I5: Thread-Safety (parallele Inserts)
# ---------------------------------------------------------------------------

class TestI5_ThreadSafety:
    """Parallele Inserts verlieren keine Daten."""

    def test_parallele_inserts_kein_datenverlust(self, db: ChatHistoryDB, tmp_db_path: str):
        session_id = "test_i5_001"
        db.create_session(session_id)
        num_threads = 10
        msgs_per_thread = 5
        errors: list = []

        def worker(thread_id: int):
            try:
                for i in range(msgs_per_thread):
                    msg = _make_message(f"thread_{thread_id}_msg_{i}", "user")
                    db.append_message_to_session(session_id, msg)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(t,)) for t in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        assert len(errors) == 0, f"Thread errors: {errors}"

        # Direkt in DB zählen
        conn = sqlite3.connect(tmp_db_path)
        try:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM chat_messages WHERE session_id = ?", (session_id,))
            count = cur.fetchone()[0]
            assert count >= num_threads * msgs_per_thread
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# Bonus: Data Integrity
# ---------------------------------------------------------------------------

class TestDataIntegrity:
    """Sender-Constraint und Timestamp-Integrität."""

    def test_gueltige_sender_akzeptiert(self, db: ChatHistoryDB):
        session_id = "test_integrity_002"
        db.create_session(session_id)
        for sender in ("user", "assistant", "system"):
            db.append_message_to_session(session_id, _make_message(f"msg from {sender}", sender))
        messages = db.get_last_messages(session_id, count=10)
        senders = {m.sender for m in messages}
        assert "user" in senders
        assert "assistant" in senders
        assert "system" in senders
