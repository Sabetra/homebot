import sqlite3

import pytest

from wellbeing.wellbeing_db import WellbeingDatabase
from wellbeing_user_insight_extractor import (
    PersonalityInsight,
    WellbeingUserInsightExtractor,
)


def _insert_session_and_insight(db: WellbeingDatabase, suffix: str, user_id: str) -> int:
    session_id = f"session-{suffix}"
    with db.get_connection() as conn:
        conn.execute(
            "INSERT INTO wellbeing_sessions(id, user_id) VALUES (?, ?)",
            (session_id, user_id),
        )
        cursor = conn.execute(
            """
            INSERT INTO wellbeing_insights (
                user_id, session_id, insight_type, category, value,
                encrypted_data, confidence, temporal_context, insight_hash,
                created_at, first_session_id, first_seen_at, last_seen_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                session_id,
                "emotional_state",
                "current_state",
                f"Insight {suffix}",
                "",
                0.8,
                "current",
                f"hash-{suffix}",
                "2026-08-01T00:00:00+00:00",
                session_id,
                "2026-08-01T00:00:00+00:00",
                "2026-08-01T00:00:00+00:00",
            ),
        )
        conn.commit()
        return int(cursor.lastrowid)


def test_fresh_database_owns_complete_insight_schema(tmp_path):
    db = WellbeingDatabase(db_path=str(tmp_path / "psych.db"))

    with db.get_connection() as conn:
        columns = {
            row[1] for row in conn.execute("PRAGMA table_info(wellbeing_insights)")
        }
        audit_table = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?",
            ("wellbeing_insight_corrections",),
        ).fetchone()

    assert {
        "mention_count",
        "first_session_id",
        "first_seen_at",
        "last_seen_at",
        "correction_status",
        "corrected_at",
        "corrected_by",
        "correction_reason",
    } <= columns
    assert audit_table is not None


def test_legacy_insight_schema_migrates_idempotently_and_encrypts_reasons(tmp_path):
    db_path = tmp_path / "legacy.db"
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE wellbeing_insights (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                insight_type TEXT NOT NULL,
                category TEXT NOT NULL,
                value TEXT NOT NULL,
                encrypted_data TEXT NOT NULL,
                confidence REAL NOT NULL,
                temporal_context TEXT NOT NULL,
                insight_hash TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL,
                validated_at TEXT NULL
            );
            INSERT INTO wellbeing_insights (
                user_id, session_id, insight_type, category, value,
                encrypted_data, confidence, temporal_context, insight_hash,
                created_at
            ) VALUES (
                'user-a', 'session-a', 'emotional_state', 'current_state',
                'Legacy insight', '', 0.8, 'current', 'legacy-hash',
                '2026-08-01T00:00:00+00:00'
            );
            """
        )

    db = WellbeingDatabase(db_path=str(db_path))
    with db.get_connection() as conn:
        conn.execute(
            """
            UPDATE wellbeing_insights
            SET correction_reason = 'Alter Klartextgrund'
            WHERE insight_hash = 'legacy-hash'
            """
        )
        conn.execute(
            """
            INSERT INTO wellbeing_insight_corrections (
                insight_id, user_id, previous_status, new_status,
                corrected_by, reason, created_at
            ) VALUES (1, 'user-a', 'active', 'rejected', 'user',
                      'Alter Auditgrund', '2026-08-02T00:00:00+00:00')
            """
        )
        conn.commit()

    db._migrate_insight_schema()
    db._migrate_insight_schema()

    with db.get_connection() as conn:
        columns = {
            row[1] for row in conn.execute("PRAGMA table_info(wellbeing_insights)")
        }
        indexes = {
            row[1] for row in conn.execute("PRAGMA index_list(wellbeing_insights)")
        }
        insight = conn.execute(
            """
            SELECT first_session_id, first_seen_at, last_seen_at,
                   correction_status, correction_reason
            FROM wellbeing_insights
            WHERE insight_hash = 'legacy-hash'
            """
        ).fetchone()
        audit_reason = conn.execute(
            "SELECT reason FROM wellbeing_insight_corrections WHERE insight_id = 1"
        ).fetchone()[0]

    assert {
        "mention_count",
        "first_session_id",
        "first_seen_at",
        "last_seen_at",
        "correction_status",
        "corrected_at",
        "corrected_by",
        "correction_reason",
    } <= columns
    assert "idx_insights_active_user" in indexes
    assert tuple(insight[:4]) == (
        "session-a",
        "2026-08-01T00:00:00+00:00",
        "2026-08-01T00:00:00+00:00",
        "active",
    )
    assert insight[4] != "Alter Klartextgrund"
    assert audit_reason != "Alter Auditgrund"
    assert db._decrypt_data(insight[4]) == "Alter Klartextgrund"
    assert db._decrypt_data(audit_reason) == "Alter Auditgrund"


def test_rejected_insight_is_audited_and_excluded_from_retrieval(tmp_path):
    db = WellbeingDatabase(db_path=str(tmp_path / "psych.db"))
    insight_id = _insert_session_and_insight(db, "a", "user-a")

    assert db.correct_user_insight(
        insight_id,
        "user-a",
        "rejected",
        corrected_by="user",
        reason="Das trifft auf mich nicht zu",
    )
    assert db.get_user_insights("user-a") == []

    with db.get_connection() as conn:
        event = conn.execute(
            """
            SELECT previous_status, new_status, corrected_by, reason
            FROM wellbeing_insight_corrections
            WHERE insight_id = ?
            """,
            (insight_id,),
        ).fetchone()

    assert tuple(event[:3]) == ("active", "rejected", "user")
    assert event[3] != "Das trifft auf mich nicht zu"
    assert db._decrypt_data(event[3]) == "Das trifft auf mich nicht zu"


def test_correction_enforces_owner_and_valid_replacement(tmp_path):
    db = WellbeingDatabase(db_path=str(tmp_path / "psych.db"))
    original_id = _insert_session_and_insight(db, "original", "user-a")
    replacement_id = _insert_session_and_insight(db, "replacement", "user-a")
    foreign_id = _insert_session_and_insight(db, "foreign", "user-b")

    assert not db.correct_user_insight(original_id, "user-b", "rejected")
    assert not db.correct_user_insight(
        original_id,
        "user-a",
        "superseded",
        replacement_insight_id=foreign_id,
    )
    assert db.correct_user_insight(
        original_id,
        "user-a",
        "superseded",
        replacement_insight_id=replacement_id,
        reason="Präzisere Formulierung",
    )

    assert [item["insight_id"] for item in db.get_user_insights("user-a")] == [replacement_id]


def test_system_cannot_reactivate_user_corrected_insight(tmp_path):
    db = WellbeingDatabase(db_path=str(tmp_path / "psych.db"))
    insight_id = _insert_session_and_insight(db, "a", "user-a")
    assert db.correct_user_insight(insight_id, "user-a", "rejected")

    with pytest.raises(ValueError, match="cannot reactivate"):
        db.correct_user_insight(
            insight_id,
            "user-a",
            "active",
            corrected_by="system",
        )


def test_human_reactivation_is_audited_and_supersession_is_terminal(tmp_path):
    db = WellbeingDatabase(db_path=str(tmp_path / "psych.db"))
    rejected_id = _insert_session_and_insight(db, "rejected", "user-a")
    replacement_id = _insert_session_and_insight(db, "replacement", "user-a")

    assert db.correct_user_insight(rejected_id, "user-a", "rejected")
    assert db.correct_user_insight(
        rejected_id,
        "user-a",
        "active",
        corrected_by="therapist",
        reason="Gemeinsam neu bewertet",
    )
    assert rejected_id in [item["insight_id"] for item in db.get_user_insights("user-a")]

    assert db.correct_user_insight(
        rejected_id,
        "user-a",
        "superseded",
        replacement_insight_id=replacement_id,
    )
    with pytest.raises(ValueError, match="cannot be reactivated"):
        db.correct_user_insight(
            rejected_id,
            "user-a",
            "active",
            corrected_by="user",
        )

    with db.get_connection() as conn:
        events = conn.execute(
            """
            SELECT previous_status, new_status, reason
            FROM wellbeing_insight_corrections
            WHERE insight_id = ?
            ORDER BY id
            """,
            (rejected_id,),
        ).fetchall()

    assert [(event[0], event[1]) for event in events] == [
        ("active", "rejected"),
        ("rejected", "active"),
        ("active", "superseded"),
    ]
    assert db._decrypt_data(events[1][2]) == "Gemeinsam neu bewertet"


def test_reextraction_does_not_reactivate_rejected_insight(tmp_path):
    db = WellbeingDatabase(db_path=str(tmp_path / "psych.db"))
    with db.get_connection() as conn:
        conn.execute(
            "INSERT INTO wellbeing_sessions(id, user_id) VALUES (?, ?)",
            ("session-a", "user-a"),
        )
        conn.commit()

    extractor = WellbeingUserInsightExtractor(wellbeing_db=db)
    insight = PersonalityInsight(
        user_id="user-a",
        session_id="session-a",
        insight_type="emotional_state",
        category="current_state",
        value="Wiederkehrender Stress",
        description="Stress wird wiederholt beschrieben",
        confidence=0.8,
        evidence=["Ich bin gestresst"],
        temporal_context="current",
        created_at="2026-08-01T00:00:00+00:00",
    )
    extractor._store_insights([insight])

    with db.get_connection() as conn:
        row = conn.execute(
            "SELECT id, mention_count FROM wellbeing_insights"
        ).fetchone()
    assert extractor.reject_insight(row[0], "user-a", reason="Unzutreffend")

    extractor._store_insights([insight])

    with db.get_connection() as conn:
        protected = conn.execute(
            """
            SELECT correction_status, mention_count
            FROM wellbeing_insights
            WHERE id = ?
            """,
            (row[0],),
        ).fetchone()

    assert tuple(protected) == ("rejected", row[1])
    assert db.get_user_insights("user-a") == []
