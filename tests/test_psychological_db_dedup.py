from contextlib import contextmanager
from pathlib import Path
import hashlib
import sqlite3

from wellbeing.wellbeing_db import WellbeingDatabase


class _FakeWellbeingDatabase:
    def __init__(self, db_path: Path) -> None:
        self.db_path = str(db_path)
        self.llm_kg_extractor = None

    @contextmanager
    def get_connection(self):
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute("PRAGMA foreign_keys=ON")
            yield conn
        finally:
            conn.close()

    def _compute_content_hmac(self, content: str) -> str:
        return hashlib.sha256(content.encode("utf-8")).hexdigest()

    def _encrypt_data(self, data: str) -> str:
        return data

    def _compute_simhash(self, content: str) -> int:
        return 0

    def _simhash_buckets(self, simhash_val: int):
        return (0, 0, 0, 0)

    def _maybe_decrypt(self, content: str):
        return content

    def _is_system_message(self, content: str) -> bool:
        return False

    def _verify_interaction_invariants(self, conn, interaction_id: int) -> bool:
        return True


def test_save_interaction_deduplicates_only_recent_exact_retries(tmp_path):
    db_path = tmp_path / "psych.db"
    fake_db = _FakeWellbeingDatabase(db_path)

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "CREATE TABLE wellbeing_sessions ("
            "id TEXT PRIMARY KEY, "
            "user_id TEXT NOT NULL, "
            "start_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP, "
            "end_time TIMESTAMP NULL, "
            "session_summary TEXT NULL, "
            "mood_progression TEXT NULL, "
            "care_goals TEXT NULL, "
            "privacy_level INTEGER DEFAULT 1, "
            "anonymized INTEGER DEFAULT 1, "
            "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, "
            "updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP"
            ")"
        )
        conn.execute(
            "CREATE TABLE session_interactions ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "session_id TEXT NOT NULL, "
            "role TEXT NOT NULL, "
            "content TEXT NOT NULL, "
            "content_encrypted INTEGER DEFAULT 0, "
            "content_hash TEXT DEFAULT NULL, "
            "simhash INTEGER DEFAULT NULL, "
            "simhash_b0 INTEGER DEFAULT NULL, "
            "simhash_b1 INTEGER DEFAULT NULL, "
            "simhash_b2 INTEGER DEFAULT NULL, "
            "simhash_b3 INTEGER DEFAULT NULL, "
            "mood_indicators TEXT NULL, "
            "care_notes TEXT NULL, "
            "word_count INTEGER DEFAULT 0, "
            "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP"
            ")"
        )
        conn.execute(
            "INSERT INTO wellbeing_sessions (id, user_id) VALUES (?, ?)",
            ("session-1", "user-1"),
        )
        conn.commit()

    first_id = WellbeingDatabase.save_interaction(
        fake_db,
        session_id="session-1",
        role="user",
        content="Hallo Welt",
    )
    second_id = WellbeingDatabase.save_interaction(
        fake_db,
        session_id="session-1",
        role="user",
        content="Hallo Welt",
    )

    assert first_id == second_id

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE session_interactions "
            "SET created_at = datetime('now', '-31 seconds') WHERE id = ?",
            (first_id,),
        )
        conn.commit()

    third_id = WellbeingDatabase.save_interaction(
        fake_db,
        session_id="session-1",
        role="user",
        content="Hallo Welt",
    )

    assert third_id != first_id

    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT id, session_id, role, content_hash FROM session_interactions"
        ).fetchall()

    assert len(rows) == 2
    assert rows[0][1:] == ("session-1", "user", hashlib.sha256(b"Hallo Welt").hexdigest())


def test_dedup_migration_replaces_permanent_unique_index(tmp_path):
    db_path = tmp_path / "psych.db"
    fake_db = _FakeWellbeingDatabase(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "CREATE TABLE session_interactions ("
            "id INTEGER PRIMARY KEY, session_id TEXT, role TEXT, "
            "content_hash TEXT, created_at TEXT)"
        )
        conn.execute(
            "CREATE UNIQUE INDEX ux_interactions_session_role_hash "
            "ON session_interactions(session_id, role, content_hash)"
        )

    WellbeingDatabase._migrate_interaction_dedup_window_index(fake_db)

    with sqlite3.connect(db_path) as conn:
        indexes = {row[1]: row[2] for row in conn.execute("PRAGMA index_list(session_interactions)")}
    assert "ux_interactions_session_role_hash" not in indexes
    assert indexes["idx_interactions_dedup_window"] == 0