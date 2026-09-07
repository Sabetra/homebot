"""
Async Database Operations — high-level async helpers for psychological session DB.

Provides reusable async functions that mirror the sync SQL operations found in
``StartupService``, ``SessionContextBuilder`` and ``SessionManagerAdapter``.

All functions accept an ``aiosqlite.Connection`` so they are independent of
the pool implementation (easy to test with in-memory DBs).

Usage::

    from database.async_connection_pool import get_async_pool
    from database.async_db_operations import (
        delete_empty_orphaned_sessions,
        close_orphaned_sessions_with_messages,
        find_sessions_needing_summary,
    )

    pool = await get_async_pool("wellbeing_store.db")
    async with pool.get_connection() as conn:
        deleted = await delete_empty_orphaned_sessions(conn, cutoff_iso)
        closed  = await close_orphaned_sessions_with_messages(conn, cutoff_iso)
        await conn.commit()
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

import aiosqlite

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Startup cleanup queries (async mirrors of StartupService static methods)
# ---------------------------------------------------------------------------


async def delete_empty_orphaned_sessions(
    conn: aiosqlite.Connection, cutoff: str
) -> int:
    """Delete orphaned sessions with 0 messages (async)."""
    cursor = await conn.execute(
        """
        DELETE FROM wellbeing_sessions
        WHERE end_time IS NULL
          AND (session_summary IS NULL OR session_summary = '')
          AND start_time < ?
          AND NOT EXISTS (
              SELECT 1 FROM session_interactions i
              WHERE i.session_id = wellbeing_sessions.id
          )
        """,
        (cutoff,),
    )
    return cursor.rowcount


async def close_orphaned_sessions_with_messages(
    conn: aiosqlite.Connection, cutoff: str
) -> int:
    """Set end_time for orphaned sessions that DO have messages (async)."""
    cursor = await conn.execute(
        """
        UPDATE wellbeing_sessions
        SET end_time   = CURRENT_TIMESTAMP,
            updated_at = CURRENT_TIMESTAMP
        WHERE end_time IS NULL
          AND (session_summary IS NULL OR session_summary = '')
          AND start_time < ?
          AND EXISTS (
              SELECT 1 FROM session_interactions i
              WHERE i.session_id = wellbeing_sessions.id
          )
        """,
        (cutoff,),
    )
    return cursor.rowcount


async def find_sessions_needing_summary(
    conn: aiosqlite.Connection,
) -> List[Tuple[str, str, int]]:
    """Return ended sessions without a summary but with interactions (async)."""
    cursor = await conn.execute(
        """
        SELECT s.id,
               s.user_id,
               (SELECT COUNT(*)
                FROM session_interactions i
                WHERE i.session_id = s.id) AS interaction_count
        FROM wellbeing_sessions s
        WHERE s.end_time IS NOT NULL
          AND (s.session_summary IS NULL OR s.session_summary = '')
        """
    )
    rows = await cursor.fetchall()
    return [(row[0], row[1], row[2]) for row in rows]


# ---------------------------------------------------------------------------
# Session end queries
# ---------------------------------------------------------------------------


async def set_session_end_time(conn: aiosqlite.Connection, session_id: str) -> bool:
    """Set end_time for a session (async fallback when close_session is unavailable)."""
    cursor = await conn.execute(
        """
        UPDATE wellbeing_sessions
        SET end_time   = CURRENT_TIMESTAMP,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (session_id,),
    )
    await conn.commit()
    return cursor.rowcount == 1


# ---------------------------------------------------------------------------
# Context-builder queries
# ---------------------------------------------------------------------------


async def get_kg_triples(
    conn: aiosqlite.Connection,
    user_id: str,
    limit: int = 100,
) -> List[Dict[str, Any]]:
    """Fetch knowledge-graph triples for a user (async)."""
    cursor = await conn.execute(
        """
        SELECT subject, predicate, object, confidence, source_date, source
        FROM knowledge_graph_triples
        WHERE user_id = ?
        ORDER BY confidence DESC
        LIMIT ?
        """,
        (user_id, limit),
    )
    rows = await cursor.fetchall()
    return [
        {
            "subject": row[0],
            "predicate": row[1],
            "object": row[2],
            "confidence": row[3],
            "source_date": row[4],
            "source": row[5],
        }
        for row in rows
    ]


async def get_session_interactions(
    conn: aiosqlite.Connection,
    session_id: str,
    limit: int = 50,
) -> List[Dict[str, Any]]:
    """Fetch interactions for a session (async)."""
    cursor = await conn.execute(
        """
        SELECT role, content, created_at, mood_indicators
        FROM session_interactions
        WHERE session_id = ?
        ORDER BY created_at ASC
        LIMIT ?
        """,
        (session_id, limit),
    )
    rows = await cursor.fetchall()
    return [
        {
            "role": row[0],
            "content": row[1],
            "timestamp": row[2],
            "mood_indicators": row[3],
        }
        for row in rows
    ]


async def get_user_sessions_async(
    conn: aiosqlite.Connection,
    user_id: str,
    status: str = "active",
    limit: int = 10,
) -> List[Dict[str, Any]]:
    """Fetch sessions for a user (async)."""
    normalized_status = status.strip().lower()
    if normalized_status not in {"active", "closed", "all"}:
        raise ValueError("status must be 'active', 'closed', or 'all'")

    cursor = await conn.execute(
        """
        SELECT id, user_id, start_time, end_time,
               CASE WHEN end_time IS NULL THEN 'active' ELSE 'closed' END AS status,
               session_summary, updated_at
        FROM wellbeing_sessions
        WHERE user_id = ?
          AND (
              ? = 'all'
              OR (? = 'active' AND end_time IS NULL)
              OR (? = 'closed' AND end_time IS NOT NULL)
          )
        ORDER BY start_time DESC
        LIMIT ?
        """,
        (user_id, normalized_status, normalized_status, normalized_status, limit),
    )
    rows = await cursor.fetchall()
    return [
        {
            "id": row[0],
            "user_id": row[1],
            "start_time": row[2],
            "end_time": row[3],
            "status": row[4],
            "session_summary": row[5],
            "updated_at": row[6],
        }
        for row in rows
    ]
