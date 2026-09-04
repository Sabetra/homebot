import asyncio
import sqlite3
from datetime import datetime, timedelta

import wellbeing_session.services.startup_service as startup_module
from wellbeing_session.lifecycle.session_lifecycle_manager import SessionLifecycleManager
from wellbeing_session.services.startup_service import StartupService


def _create_schema(db_path: str) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE wellbeing_sessions (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                start_time TEXT NOT NULL,
                end_time TEXT,
                session_summary TEXT,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE session_interactions (
                id INTEGER PRIMARY KEY,
                session_id TEXT NOT NULL REFERENCES wellbeing_sessions(id) ON DELETE CASCADE,
                role TEXT NOT NULL,
                content TEXT NOT NULL
            );
            """
        )


def test_startup_cleanup_uses_canonical_schema(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "psych.db"
    _create_schema(str(db_path))
    old = (datetime.now() - timedelta(hours=2)).isoformat()

    with sqlite3.connect(db_path) as conn:
        conn.executemany(
            "INSERT INTO wellbeing_sessions VALUES (?, ?, ?, NULL, NULL, ?)",
            [("empty", "user", old, old), ("with-turn", "user", old, old)],
        )
        conn.execute(
            "INSERT INTO session_interactions(session_id, role, content) VALUES (?, ?, ?)",
            ("with-turn", "user", "hello"),
        )

    monkeypatch.setattr(startup_module, "_POOL_AVAILABLE", False)
    service = StartupService(object(), db_path=str(db_path))

    assert service.run_startup_cleanup(generate_summaries=False) == (1, 1, 0)

    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM wellbeing_sessions WHERE id = 'empty'"
        ).fetchone()[0] == 0
        assert conn.execute(
            "SELECT end_time FROM wellbeing_sessions WHERE id = 'with-turn'"
        ).fetchone()[0] is not None


def test_lifecycle_manager_delegates_to_canonical_cleanup(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "psych.db"
    _create_schema(str(db_path))
    old = (datetime.now() - timedelta(hours=2)).isoformat()
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO wellbeing_sessions VALUES (?, ?, ?, NULL, NULL, ?)",
            ("empty", "user", old, old),
        )

    monkeypatch.setattr(startup_module, "_POOL_AVAILABLE", False)
    monkeypatch.setattr(SessionLifecycleManager, "_DB_PATH", str(db_path))

    assert SessionLifecycleManager(object()).cleanup_orphaned_sessions_on_startup() == (1, 0, 0)


def test_async_cleanup_helpers_use_canonical_schema(tmp_path) -> None:
    aiosqlite = __import__("aiosqlite")
    from database.async_db_operations import (
        close_orphaned_sessions_with_messages,
        delete_empty_orphaned_sessions,
        find_sessions_needing_summary,
    )

    db_path = tmp_path / "psych.db"
    _create_schema(str(db_path))
    old = (datetime.now() - timedelta(hours=2)).isoformat()
    with sqlite3.connect(db_path) as conn:
        conn.executemany(
            "INSERT INTO wellbeing_sessions VALUES (?, ?, ?, NULL, NULL, ?)",
            [("empty", "user", old, old), ("with-turn", "user", old, old)],
        )
        conn.execute(
            "INSERT INTO session_interactions(session_id, role, content) VALUES (?, ?, ?)",
            ("with-turn", "user", "hello"),
        )

    async def run_cleanup() -> tuple[int, int, list[tuple[str, str, int]]]:
        async with aiosqlite.connect(db_path) as conn:
            deleted = await delete_empty_orphaned_sessions(conn, datetime.now().isoformat())
            closed = await close_orphaned_sessions_with_messages(conn, datetime.now().isoformat())
            await conn.commit()
            summaries = await find_sessions_needing_summary(conn)
            return deleted, closed, summaries

    deleted, closed, summaries = asyncio.run(run_cleanup())
    assert (deleted, closed) == (1, 1)
    assert summaries == [("with-turn", "user", 1)]