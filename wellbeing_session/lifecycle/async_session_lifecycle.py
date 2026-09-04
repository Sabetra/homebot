"""
Async Session Lifecycle Manager — async variant of SessionLifecycleManager.

Non-blocking session lifecycle operations (cleanup, create, end) using
the async DB pool.

✅ Phase 9: Full async migration of lifecycle layer.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

# Optional async pool
_ASYNC_AVAILABLE = False
try:
    from database.async_connection_pool import AsyncConnectionPool, get_async_pool
    from database.async_db_operations import (
        delete_empty_orphaned_sessions,
        close_orphaned_sessions_with_messages,
        find_sessions_needing_summary,
        set_session_end_time,
    )
    _ASYNC_AVAILABLE = True
except ImportError:
    pass

# Optional OTel
try:
    from observability.decorators import traced_async, metered_async
except ImportError:
    def traced_async(*a: Any, **kw: Any) -> Any:  # type: ignore[misc]
        def d(fn: Any) -> Any: return fn
        if a and callable(a[0]): return a[0]
        return d
    def metered_async(*a: Any, **kw: Any) -> Any:  # type: ignore[misc]
        def d(fn: Any) -> Any: return fn
        if a and callable(a[0]): return a[0]
        return d


class AsyncSessionLifecycleManager:
    """Manages the complete lifecycle of psychological sessions asynchronously.

    Provides the same operations as ``SessionLifecycleManager`` but uses
    the async connection pool for all DB I/O.
    """

    DB_PATH: str = "wellbeing_store.db"

    def __init__(
        self,
        session_manager: Any,
        insight_extractor: Optional[Any] = None,
        async_pool: Optional[Any] = None,
    ) -> None:
        self.session_manager = session_manager
        self.insight_extractor = insight_extractor
        self._pool = async_pool

    # ------------------------------------------------------------------
    # Async cleanup
    # ------------------------------------------------------------------

    @traced_async("lifecycle.cleanup_orphaned")
    @metered_async("lifecycle.cleanup_orphaned")
    async def cleanup_orphaned_sessions_async(self) -> Tuple[int, int, int]:
        """Clean up orphaned sessions asynchronously.

        Returns:
            Tuple of (deleted_empty, closed_with_msgs, status_fixed).
        """
        if not _ASYNC_AVAILABLE or self._pool is None:
            # Fallback to sync
            from wellbeing_session.lifecycle import SessionLifecycleManager
            sync_mgr = SessionLifecycleManager(self.session_manager, self.insight_extractor)
            return sync_mgr.cleanup_orphaned_sessions_on_startup()

        try:
            cutoff = (datetime.now() - timedelta(hours=1)).isoformat()

            async with self._pool.get_connection() as conn:
                deleted = await delete_empty_orphaned_sessions(conn, cutoff)
                closed = await close_orphaned_sessions_with_messages(conn, cutoff)
                await conn.commit()

            logger.info(
                "✅ Async lifecycle cleanup: deleted=%d closed=%d",
                deleted, closed,
            )
            return deleted, closed, 0

        except Exception as exc:
            logger.error("❌ Async cleanup error: %s", exc)
            raise

    # ------------------------------------------------------------------
    # Async session end
    # ------------------------------------------------------------------

    @traced_async("lifecycle.end_session")
    async def end_session_async(self, session_id: str) -> bool:
        """End a session asynchronously.

        Args:
            session_id: Session to end.

        Returns:
            True on success.
        """
        if not _ASYNC_AVAILABLE or self._pool is None:
            return self.session_manager.end_session(session_id)

        try:
            async with self._pool.get_connection() as conn:
                result = await set_session_end_time(conn, session_id)
            logger.info("✅ Async session ended: %s", session_id[:12])
            return result
        except Exception as exc:
            logger.error("❌ Async end_session error: %s", exc)
            return False

    # ------------------------------------------------------------------
    # Async summary generation check
    # ------------------------------------------------------------------

    @traced_async("lifecycle.find_needing_summary")
    async def find_sessions_needing_summary_async(self) -> int:
        """Count sessions needing summary generation.

        Returns:
            Number of sessions needing summaries.
        """
        if not _ASYNC_AVAILABLE or self._pool is None:
            return 0

        try:
            async with self._pool.get_connection() as conn:
                sessions = await find_sessions_needing_summary(conn)
            count = len([s for s in sessions if s[2] > 0])
            logger.info("📊 Async: %d sessions need summaries", count)
            return count
        except Exception as exc:
            logger.error("❌ Async find_sessions_needing_summary error: %s", exc)
            return 0

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    @property
    def is_async_available(self) -> bool:
        """Whether async DB operations are available."""
        return _ASYNC_AVAILABLE and self._pool is not None
