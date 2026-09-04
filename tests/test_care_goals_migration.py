"""C6 (Scope-B): Migrationstest therapeutic_goals -> care_goals.

Sichert:
- Rename der Legacy-Spalte ``therapeutic_goals`` auf ``care_goals``
- Daten bleiben bei der Migration erhalten
- Idempotenz (zweite Initialisierung darf nicht fehlschlagen)
"""

import sqlite3

from cryptography.fernet import Fernet

from wellbeing.wellbeing_db import WellbeingDatabase

_VALID_KEY = Fernet.generate_key().decode()


def _legacy_schema(conn: sqlite3.Connection) -> None:
    """DB-Status VOR dem Rename: Spalte ``therapeutic_goals`` + Beispiel-Daten."""
    conn.execute(
        "CREATE TABLE wellbeing_sessions ("
        "id TEXT PRIMARY KEY, "
        "user_id TEXT NOT NULL, "
        "start_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP, "
        "end_time TIMESTAMP NULL, "
        "session_summary TEXT NULL, "
        "mood_progression TEXT NULL, "
        "therapeutic_goals TEXT NULL, "
        "privacy_level INTEGER DEFAULT 1, "
        "anonymized INTEGER DEFAULT 1, "
        "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, "
        "updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP"
        ")"
    )
    conn.execute(
        "INSERT INTO wellbeing_sessions (id, user_id, therapeutic_goals) VALUES (?, ?, ?)",
        ("session-legacy", "user-1", "Ziel A; Ziel B"),
    )
    conn.commit()


def _columns(db_path) -> set:
    with sqlite3.connect(db_path) as conn:
        return {row[1] for row in conn.execute("PRAGMA table_info(wellbeing_sessions)")}


def test_migration_renames_column_and_preserves_data(tmp_path):
    db_path = tmp_path / "wellbeing_store.db"
    with sqlite3.connect(db_path) as conn:
        _legacy_schema(conn)

    # Initialisierung der echten DB (fÃ¼hrt die Migration aus)
    WellbeingDatabase(db_path=str(db_path), encryption_key=_VALID_KEY)

    cols = _columns(db_path)
    assert "care_goals" in cols
    assert "therapeutic_goals" not in cols

    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT care_goals FROM wellbeing_sessions WHERE id = ?",
            ("session-legacy",),
        ).fetchone()
    assert row is not None
    assert row[0] == "Ziel A; Ziel B"


def test_migration_is_idempotent(tmp_path):
    db_path = tmp_path / "wellbeing_store.db"
    with sqlite3.connect(db_path) as conn:
        _legacy_schema(conn)

    # Erste Initialisierung: Migration lÃ¤uft
    WellbeingDatabase(db_path=str(db_path), encryption_key=_VALID_KEY)
    # Zweite Initialisierung: Spalte existiert nicht mehr â€” darf nicht fehlschlagen
    WellbeingDatabase(db_path=str(db_path), encryption_key=_VALID_KEY)

    cols = _columns(db_path)
    assert "care_goals" in cols
    assert "therapeutic_goals" not in cols
