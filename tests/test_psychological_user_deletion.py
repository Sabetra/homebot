from contextlib import contextmanager
import sqlite3

from wellbeing.wellbeing_db import WellbeingDatabase
from wellbeing.session_manager import WellbeingSessionManager


class _DeletionDatabase:
    def __init__(self, db_path) -> None:
        self.db_path = str(db_path)
        self.kg_faiss_manager = None
        self._profile_cache = None

    @contextmanager
    def get_connection(self):
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute("PRAGMA foreign_keys=ON")
            yield conn
        finally:
            conn.close()

    def invalidate_entity_index(self) -> None:
        pass


def _create_deletion_fixture(db_path: str) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE wellbeing_sessions (
                id TEXT PRIMARY KEY, user_id TEXT NOT NULL
            );
            CREATE TABLE session_interactions (
                id INTEGER PRIMARY KEY, session_id TEXT NOT NULL
            );
            CREATE TABLE triples (
                id INTEGER PRIMARY KEY, session_id TEXT, interaction_id INTEGER
            );
            CREATE TABLE context_formulations (
                id INTEGER PRIMARY KEY, user_id TEXT NOT NULL
            );
            CREATE TABLE wellbeing_profiles (
                user_id TEXT PRIMARY KEY, profile_data TEXT
            );
            CREATE TABLE care_plans (
                id INTEGER PRIMARY KEY, user_id TEXT NOT NULL
            );
            CREATE TABLE plan_goals (
                id INTEGER PRIMARY KEY, plan_id INTEGER NOT NULL
            );
            CREATE TABLE goal_updates (
                id INTEGER PRIMARY KEY, goal_id INTEGER NOT NULL, session_id TEXT NOT NULL
            );
            CREATE TABLE session_focuses (
                id INTEGER PRIMARY KEY, session_id TEXT NOT NULL, plan_id INTEGER NOT NULL
            );
            CREATE TABLE kg_entities (
                entity_id INTEGER PRIMARY KEY,
                entity_text TEXT UNIQUE,
                normalized_text TEXT,
                entity_type TEXT,
                frequency INTEGER,
                first_seen_doc_id TEXT,
                embedding BLOB
            );

            INSERT INTO wellbeing_sessions VALUES ('session-a', 'user-a');
            INSERT INTO wellbeing_sessions VALUES ('session-b', 'user-b');
            INSERT INTO session_interactions VALUES (1, 'session-a');
            INSERT INTO session_interactions VALUES (2, 'session-b');
            ALTER TABLE triples ADD COLUMN subject TEXT;
            ALTER TABLE triples ADD COLUMN object TEXT;
            INSERT INTO triples VALUES (1, 'session-a', 1, 'private-a', 'shared');
            INSERT INTO triples VALUES (2, 'session-b', 2, 'private-b', 'shared');
            INSERT INTO context_formulations VALUES (1, 'user-a');
            INSERT INTO context_formulations VALUES (2, 'user-b');
            INSERT INTO wellbeing_profiles VALUES ('user-a', 'a');
            INSERT INTO wellbeing_profiles VALUES ('user-b', 'b');
            INSERT INTO care_plans VALUES (1, 'user-a');
            INSERT INTO care_plans VALUES (2, 'user-b');
            INSERT INTO plan_goals VALUES (1, 1);
            INSERT INTO plan_goals VALUES (2, 2);
            INSERT INTO goal_updates VALUES (1, 1, 'session-a');
            INSERT INTO goal_updates VALUES (2, 2, 'session-b');
            INSERT INTO session_focuses VALUES (1, 'session-a', 1);
            INSERT INTO session_focuses VALUES (2, 'session-b', 2);
            INSERT INTO kg_entities VALUES (1, 'private-a', 'private-a', 'entity', 1, NULL, NULL);
            INSERT INTO kg_entities VALUES (2, 'private-b', 'private-b', 'entity', 1, NULL, X'01');
            INSERT INTO kg_entities VALUES (3, 'shared', 'shared', 'entity', 2, NULL, X'02');
            """
        )


def test_delete_user_data_removes_raw_derived_and_treatment_data(tmp_path) -> None:
    db_path = tmp_path / "psych.db"
    _create_deletion_fixture(str(db_path))
    db = _DeletionDatabase(db_path)

    result = WellbeingDatabase.delete_user_data(db, "user-a")

    assert result["session_ids"] == ["session-a"]
    with sqlite3.connect(db_path) as conn:
        for table in (
            "wellbeing_sessions",
            "session_interactions",
            "triples",
            "context_formulations",
            "wellbeing_profiles",
            "care_plans",
            "plan_goals",
            "goal_updates",
            "session_focuses",
        ):
            assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 1, table
        entities = conn.execute(
            "SELECT entity_text, frequency, embedding FROM kg_entities ORDER BY entity_text"
        ).fetchall()
        assert entities == [("private-b", 1, b"\x01"), ("shared", 1, b"\x02")]
        assert conn.execute(
            "SELECT COUNT(*) FROM wellbeing_sessions WHERE user_id = 'user-a'"
        ).fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM wellbeing_sessions WHERE user_id = 'user-b'"
        ).fetchone()[0] == 1


def test_session_manager_reports_failure_and_preserves_cache_on_db_error() -> None:
    class FailingDatabase:
        def delete_user_data(self, user_id):
            raise sqlite3.OperationalError("write failed")

    manager = WellbeingSessionManager.__new__(WellbeingSessionManager)
    manager.db = FailingDatabase()
    manager.resolve_user_id = lambda user_id: user_id
    manager._active_sessions = {"session-a": object()}
    manager._session_locks = {"session-a": True}
    manager._last_auto_summarization = {"session-a": object()}
    manager._last_backfill = {"user-a": object()}

    assert manager.delete_user_data("user-a") is False
    assert "session-a" in manager._active_sessions