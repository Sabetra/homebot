"""Tests for Phase E (2026-09-01) Wellbeing schema de-clinicalisation.

Covers:
- legacy DB migration (tables + indexes + column renamed, data preserved);
- FK references carried over to renamed parent tables;
- idempotency (second run is a no-op, sentinel present);
- fresh (empty) DB migration is a no-op and sets the sentinel;
- conflict (both old and new object present) fails loudly with RuntimeError.
"""
from __future__ import annotations

import sqlite3

import pytest

from wellbeing.schema_migration import (
    COLUMN_RENAMES,
    INDEX_RENAMES,
    TABLE_RENAMES,
    SENTINEL_KEY,
    migrate_wellbeing_schema,
)

CLINICAL_TABLES = {old for old, _ in TABLE_RENAMES}
CLINICAL_INDEXES = {old for old, _ in INDEX_RENAMES}
CLINICAL_COLUMNS = {old_col for _, old_col, _ in COLUMN_RENAMES}


def FernetKey() -> bytes:
    """Fresh Fernet key so tests never touch the production key file."""
    from cryptography.fernet import Fernet
    return Fernet.generate_key()


def _create_legacy_db(db_path) -> None:
    """Build a minimal legacy (clinical) schema exercising every rename mechanic.

    - Table renames: psychological_sessions, alliance_scores, treatment_plans.
    - Index renames: idx_alliance_session, idx_treatment_plans_user_status.
    - Column rename: session_focuses.planned_interventions.
    - FKs: alliance_scores + session_focuses reference psychological_sessions.
    One row per renamed table so row-count preservation is checkable.
    """
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute(
        """CREATE TABLE psychological_sessions (
               id TEXT PRIMARY KEY,
               user_id TEXT NOT NULL,
               created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
           )"""
    )
    conn.execute(
        """CREATE TABLE alliance_scores (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               session_id TEXT NOT NULL,
               score REAL DEFAULT 0,
               FOREIGN KEY (session_id)
                   REFERENCES psychological_sessions(id) ON DELETE CASCADE
           )"""
    )
    conn.execute("CREATE INDEX idx_alliance_session ON alliance_scores(session_id)")
    conn.execute(
        """CREATE TABLE treatment_plans (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               user_id TEXT NOT NULL,
               status TEXT DEFAULT 'active'
           )"""
    )
    conn.execute(
        "CREATE INDEX idx_treatment_plans_user_status ON treatment_plans(user_id, status)"
    )
    conn.execute(
        """CREATE TABLE session_focuses (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               session_id TEXT NOT NULL,
               planned_interventions TEXT DEFAULT NULL,
               FOREIGN KEY (session_id)
                   REFERENCES psychological_sessions(id) ON DELETE CASCADE
           )"""
    )
    conn.execute("INSERT INTO psychological_sessions (id, user_id) VALUES ('s1', 'u1')")
    conn.execute("INSERT INTO alliance_scores (session_id, score) VALUES ('s1', 4.5)")
    conn.execute("INSERT INTO treatment_plans (user_id, status) VALUES ('u1', 'active')")
    conn.execute(
        "INSERT INTO session_focuses (session_id, planned_interventions) VALUES ('s1', 'breathe')"
    )
    conn.commit()
    conn.close()


def _tables(conn):
    return {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}


def _indexes(conn):
    return {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND name NOT LIKE 'sqlite_%'")}


def _columns(conn, table):
    return {r[1] for r in conn.execute(f'PRAGMA table_info("{table}")').fetchall()}


def test_migrate_legacy_db_renames_objects_and_preserves_data(tmp_path):
    db = tmp_path / "legacy.db"
    _create_legacy_db(db)

    conn = sqlite3.connect(db)
    conn.execute("PRAGMA foreign_keys=ON")
    stats = migrate_wellbeing_schema(conn)

    assert stats["tables"] == 3
    assert stats["indexes"] == 2
    assert stats["columns"] == 1

    tables = _tables(conn)
    for new in ("wellbeing_sessions", "engagement_scores", "care_plans"):
        assert new in tables
    for old in ("psychological_sessions", "alliance_scores", "treatment_plans"):
        assert old not in tables

    indexes = _indexes(conn)
    for new in ("idx_engagement_session", "idx_care_plans_user_status"):
        assert new in indexes
    for old in ("idx_alliance_session", "idx_treatment_plans_user_status"):
        assert old not in indexes

    # Data preserved (row counts + content).
    assert conn.execute("SELECT COUNT(*) FROM wellbeing_sessions").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM engagement_scores").fetchone()[0] == 1
    assert conn.execute("SELECT score FROM engagement_scores").fetchone()[0] == 4.5
    assert conn.execute("SELECT COUNT(*) FROM care_plans").fetchone()[0] == 1

    # Column renamed, value intact.
    sf_cols = _columns(conn, "session_focuses")
    assert "planned_steps" in sf_cols
    assert "planned_interventions" not in sf_cols
    assert conn.execute("SELECT planned_steps FROM session_focuses").fetchone()[0] == "breathe"
    conn.close()


def test_migrate_carries_over_foreign_keys(tmp_path):
    db = tmp_path / "legacy.db"
    _create_legacy_db(db)

    conn = sqlite3.connect(db)
    conn.execute("PRAGMA foreign_keys=ON")
    migrate_wellbeing_schema(conn)

    # engagement_scores (was alliance_scores) must now reference wellbeing_sessions.
    fk = conn.execute("PRAGMA foreign_key_list(engagement_scores)").fetchall()
    assert any(r[2] == "wellbeing_sessions" for r in fk)
    assert not any(r[2] == "psychological_sessions" for r in fk)

    # No schema text still points at the old parent name.
    leftover = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND sql LIKE '%psychological_sessions%'"
    ).fetchall()
    assert leftover == []
    conn.close()


def test_migrate_is_idempotent_second_run_noop(tmp_path):
    db = tmp_path / "legacy.db"
    _create_legacy_db(db)

    conn = sqlite3.connect(db)
    conn.execute("PRAGMA foreign_keys=ON")
    first = migrate_wellbeing_schema(conn)
    assert first["tables"] == 3 and first["indexes"] == 2 and first["columns"] == 1

    second = migrate_wellbeing_schema(conn)
    assert second["already_migrated"] is True
    assert second["tables"] == 0 and second["indexes"] == 0 and second["columns"] == 0
    conn.close()


def test_migrate_fresh_db_is_noop_and_sets_sentinel(tmp_path):
    db = tmp_path / "fresh.db"
    conn = sqlite3.connect(db)

    stats = migrate_wellbeing_schema(conn)
    assert stats["tables"] == 0 and stats["indexes"] == 0 and stats["columns"] == 0
    assert stats["already_migrated"] is False

    # Sentinel written so a re-run is a clean no-op.
    row = conn.execute(
        "SELECT value FROM _schema_meta WHERE key=?", (SENTINEL_KEY,)
    ).fetchone()
    assert row is not None and row[0] == "done"

    # No clinical names exist on a fresh (empty) DB.
    assert not (CLINICAL_TABLES & _tables(conn))
    assert not (CLINICAL_INDEXES & _indexes(conn))
    conn.close()


def test_migrate_conflict_fails_loudly(tmp_path):
    db = tmp_path / "conflict.db"
    conn = sqlite3.connect(db)
    # Both old and new table present -> must raise, never silently drop/rename.
    conn.execute("CREATE TABLE alliance_scores (id INTEGER PRIMARY KEY)")
    conn.execute("CREATE TABLE engagement_scores (id INTEGER PRIMARY KEY)")
    conn.commit()

    with pytest.raises(RuntimeError, match="conflict"):
        migrate_wellbeing_schema(conn)
    conn.close()


def test_wellbeing_database_fresh_schema_has_no_clinical_names(tmp_path):
    """Integration: a real WellbeingDatabase on a fresh path must expose only
    neutral (wellbeing) table/index/column names — i.e. migration (no-op) + DDL
    together produce a schema free of approved clinical object names."""
    from wellbeing.wellbeing_db import WellbeingDatabase

    db_path = tmp_path / "fresh.db"
    WellbeingDatabase(db_path=str(db_path), encryption_key=FernetKey())

    conn = sqlite3.connect(db_path)
    tables = _tables(conn)
    indexes = _indexes(conn)

    # The core renamed tables that live in the MAIN _init_schema() DDL must
    # exist under their NEW names. (care_plans is created by a separate
    # repository module, so it is intentionally NOT asserted here.)
    for new in ("wellbeing_sessions", "engagement_scores", "self_check_results",
                "practice_tasks", "context_formulations", "progress_assessments",
                "wellbeing_insights", "wellbeing_insight_corrections"):
        assert new in tables, f"expected table {new} missing; have {sorted(tables)}"

    # No approved clinical table/index names remain anywhere.
    assert not (CLINICAL_TABLES & tables), sorted(CLINICAL_TABLES & tables)
    assert not (CLINICAL_INDEXES & indexes), sorted(CLINICAL_INDEXES & indexes)

    # The renamed column must be under its new name (session_focuses exists).
    if "session_focuses" in tables:
        assert "planned_steps" in _columns(conn, "session_focuses")
        assert "planned_interventions" not in _columns(conn, "session_focuses")
    conn.close()


def test_wellbeing_database_migrates_legacy_db_in_place(tmp_path):
    """Integration: pointing WellbeingDatabase at a LEGACY DB renames its tables
    in place (data preserved) instead of creating empty new tables beside them."""
    from wellbeing.wellbeing_db import WellbeingDatabase

    db_path = tmp_path / "legacy.db"
    _create_legacy_db(db_path)

    WellbeingDatabase(db_path=str(db_path), encryption_key=FernetKey())

    conn = sqlite3.connect(db_path)
    tables = _tables(conn)
    assert "wellbeing_sessions" in tables
    assert "engagement_scores" in tables
    assert "care_plans" in tables
    # Old clinical tables must be gone (renamed, not duplicated).
    assert "psychological_sessions" not in tables
    assert "alliance_scores" not in tables
    assert "treatment_plans" not in tables
    # Data survived the in-place rename.
    assert conn.execute("SELECT COUNT(*) FROM engagement_scores").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM care_plans").fetchone()[0] == 1
    conn.close()
