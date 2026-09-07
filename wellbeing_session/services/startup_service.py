"""
Startup Service — Application startup tasks for the psychological session system.

Extracted from ``WellbeingSessionInterface._cleanup_orphaned_sessions_on_startup``
and ``_generate_missing_summaries`` to keep the main interface thin and testable.

Responsibilities:
1. Clean up orphaned sessions left by crashes (no end_time, no summary).
2. Delete empty orphaned sessions (0 messages).
3. Set end_time for orphaned sessions *with* messages.
4. Generate missing summaries for ended sessions.

All DB operations use the connection pool when available, falling back to
raw ``sqlite3.connect()``.

✅ Phase 8C: OpenTelemetry instrumented (traced + metered).
"""

from __future__ import annotations

import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Any, Iterator, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Optional pool
try:
    from database.connection_pool import get_pool
    _POOL_AVAILABLE = True
except ImportError:
    _POOL_AVAILABLE = False
    get_pool = None  # type: ignore[assignment]

# Optional observability — no-op fallback decorators when unavailable
try:
    from observability.decorators import traced, metered
except ImportError:
    from typing import Callable, TypeVar as _TV
    _F = _TV("_F", bound=Callable[..., Any])
    def traced(*a: Any, **kw: Any) -> Any:
        def _d(fn: _F) -> _F: return fn
        return _d if not (a and callable(a[0])) else a[0]
    def metered(*a: Any, **kw: Any) -> Any:
        def _d(fn: _F) -> _F: return fn
        return _d if not (a and callable(a[0])) else a[0]


class StartupService:
    """Performs one-time startup housekeeping for the psychological session system."""

    DB_PATH = str(__import__("utils.db_path_resolver", fromlist=["get_wellbeing_path"]).get_wellbeing_path())

    def __init__(
        self,
        session_manager: Any,
        orphan_cutoff_hours: int = 1,
        db_path: Optional[str] = None,
    ) -> None:
        """
        Args:
            session_manager: ``SessionManagerAdapter`` (or anything with ``.end_session``).
            orphan_cutoff_hours: Sessions older than this without end_time are considered orphaned.
            db_path: Optional database path override, primarily for delegated callers and tests.
        """
        self._session_manager = session_manager
        self._orphan_cutoff_hours = orphan_cutoff_hours
        self._db_path = db_path or self.DB_PATH

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @traced("startup.run_cleanup")
    @metered("startup.run_cleanup")
    def run_startup_cleanup(self, generate_summaries: bool = True) -> Tuple[int, int, int]:
        """Execute the full startup-cleanup pipeline.

        Safe to call multiple times — subsequent calls are effectively no-ops
        if nothing needs cleaning.

        Args:
            generate_summaries: If False, skip LLM-based summary generation
                (useful at startup before the model is loaded — summaries
                are deferred to the first ``set_model_loader`` call).

        ✅ Phase 8C: OpenTelemetry instrumented.
        """
        with self._connection() as conn:
            cursor = conn.cursor()
            cutoff = (datetime.now() - timedelta(hours=self._orphan_cutoff_hours)).isoformat()

            deleted_empty = self._delete_empty_orphaned_sessions(cursor, cutoff)
            closed_with_msgs = self._close_orphaned_sessions_with_messages(cursor, cutoff)
            conn.commit()

            if deleted_empty > 0 or closed_with_msgs > 0:
                logger.info(
                    f"🧹 StartupService: {deleted_empty} empty sessions deleted, "
                    f"{closed_with_msgs} sessions closed"
                )

            sessions_needing_summary = (
                self._find_sessions_needing_summary(cursor) if generate_summaries else []
            )

        sessions_with_interactions = [
            (sid, uid, cnt) for sid, uid, cnt in sessions_needing_summary if cnt > 0
        ]
        if sessions_with_interactions:
            logger.info(
                f"📝 {len(sessions_with_interactions)} ended sessions need summary generation"
            )
            self.generate_missing_summaries(sessions_with_interactions)
        elif generate_summaries:
            logger.debug("🧹 StartupService: all ended sessions already have summaries")

        return deleted_empty, closed_with_msgs, 0

    def generate_missing_summaries(
        self,
        sessions: List[Tuple[str, str, int]],
    ) -> None:
        """Generate summaries for ended sessions that lack one.

        Args:
            sessions: List of ``(session_id, user_id, interaction_count)`` tuples.
        """
        generated = 0
        failed = 0

        for session_id, user_id, interaction_count in sessions:
            if interaction_count == 0:
                continue

            try:
                logger.info(
                    f"📝 Generating summary for session {session_id[:16]}… "
                    f"({interaction_count} interactions)"
                )

                internal_manager = getattr(self._session_manager, "manager", None)
                if internal_manager is not None and hasattr(internal_manager, "close_session"):
                    # Check if summarizer is ready
                    if (
                        not hasattr(internal_manager, "context_summarizer")
                        or internal_manager.context_summarizer is None
                    ):
                        logger.warning(
                            f"⚠️ ContextSummarizer not ready (no ModelLoader) — "
                            f"skipping {session_id[:16]}…"
                        )
                        failed += 1
                        continue

                    success: bool = internal_manager.close_session(session_id, generate_summary=True)
                    if success:
                        generated += 1
                        logger.info(f"✅ Summary generated for {session_id[:16]}…")
                    else:
                        failed += 1
                else:
                    # Fallback to adapter's end_session
                    if self._session_manager.end_session(session_id):
                        generated += 1
                    else:
                        failed += 1

            except Exception as exc:
                failed += 1
                logger.warning(f"⚠️ Summary generation error for {session_id[:16]}…: {exc}")

        if generated > 0 or failed > 0:
            logger.info(
                f"📝 Summary generation complete: {generated} succeeded, {failed} failed"
            )

    # ------------------------------------------------------------------
    # Private DB helpers
    # ------------------------------------------------------------------

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        """Yield a correctly scoped connection with foreign keys enabled."""
        if _POOL_AVAILABLE and get_pool is not None:
            with get_pool(self._db_path).get_connection() as conn:
                conn.execute("PRAGMA foreign_keys=ON")
                yield conn
            return

        conn = sqlite3.connect(self._db_path)
        try:
            conn.execute("PRAGMA foreign_keys=ON")
            yield conn
        finally:
            conn.close()

    @staticmethod
    def _delete_empty_orphaned_sessions(cursor: Any, cutoff: str) -> int:
        """Delete orphaned sessions with 0 messages."""
        cursor.execute(
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
        return int(cursor.rowcount)

    @staticmethod
    def _close_orphaned_sessions_with_messages(cursor: Any, cutoff: str) -> int:
        """Set end_time for orphaned sessions that DO have messages."""
        cursor.execute(
            """
            UPDATE wellbeing_sessions
            SET end_time       = CURRENT_TIMESTAMP,
                updated_at     = CURRENT_TIMESTAMP
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
        return int(cursor.rowcount)

    @staticmethod
    def _find_sessions_needing_summary(cursor: Any) -> List[Tuple[str, str, int]]:
        """Return ended sessions without a summary but with interactions."""
        cursor.execute(
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
        return [(row[0], row[1], row[2]) for row in cursor.fetchall()]

