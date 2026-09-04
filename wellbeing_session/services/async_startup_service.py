"""
Async Startup Service — non-blocking version of StartupService.

Mirrors ``wellbeing_session.services.startup_service.StartupService``
but uses ``aiosqlite`` via ``AsyncConnectionPool`` for all DB operations.

The sync ``StartupService`` remains available for contexts that cannot
use ``await`` (e.g. Streamlit top-level).  The bridge helper
``run_startup_cleanup_sync()`` runs the async version inside
``asyncio.run()`` for convenience.

Usage::

    service = AsyncStartupService(session_manager)

    # Pure async
    await service.run_startup_cleanup()

    # Or from sync Streamlit code
    from wellbeing_session.services.async_startup_service import run_startup_cleanup_sync
    run_startup_cleanup_sync(session_manager)
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any, List, Tuple

logger = logging.getLogger(__name__)

# Late-import to avoid hard dependency at module level
_ASYNC_POOL_AVAILABLE = False
try:
    from database.async_connection_pool import AsyncConnectionPool, get_async_pool
    from database.async_db_operations import (
        delete_empty_orphaned_sessions,
        close_orphaned_sessions_with_messages,
        find_sessions_needing_summary,
    )
    _ASYNC_POOL_AVAILABLE = True
except ImportError:
    pass

# Optional observability — no-op fallback when unavailable
try:
    from observability.decorators import traced_async, metered_async
except ImportError:
    def traced_async(*a: Any, **kw: Any) -> Any:  # type: ignore[misc]
        def _d(fn: Any) -> Any: return fn
        return _d if not (a and callable(a[0])) else a[0]
    def metered_async(*a: Any, **kw: Any) -> Any:  # type: ignore[misc]
        def _d(fn: Any) -> Any: return fn
        return _d if not (a and callable(a[0])) else a[0]


class AsyncStartupService:
    """Non-blocking startup cleanup for the psychological session system."""

    DB_PATH = str(__import__("utils.db_path_resolver", fromlist=["get_wellbeing_path"]).get_wellbeing_path())

    def __init__(
        self,
        session_manager: Any,
        orphan_cutoff_hours: int = 1,
    ) -> None:
        self._session_manager = session_manager
        self._orphan_cutoff_hours = orphan_cutoff_hours

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @traced_async("async_startup.run_cleanup")
    @metered_async("async_startup.run_cleanup")
    async def run_startup_cleanup(self) -> Tuple[int, int, int]:
        """Full startup-cleanup pipeline (async)."""
        if not _ASYNC_POOL_AVAILABLE:
            logger.warning(
                "⚠️ AsyncStartupService: aiosqlite/async pool not available, skipping"
            )
            return 0, 0, 0

        try:
            pool = await get_async_pool(self.DB_PATH)
            async with pool.get_connection() as conn:
                cutoff = (
                    datetime.now() - timedelta(hours=self._orphan_cutoff_hours)
                ).isoformat()

                deleted = await delete_empty_orphaned_sessions(conn, cutoff)
                closed = await close_orphaned_sessions_with_messages(conn, cutoff)
                await conn.commit()

                sessions = await find_sessions_needing_summary(conn)

            if deleted > 0 or closed > 0:
                logger.info(
                    "🧹 AsyncStartupService: %d deleted, %d closed",
                    deleted, closed,
                )

            sessions_with_data = [
                (sid, uid, cnt) for sid, uid, cnt in sessions if cnt > 0
            ]
            if sessions_with_data:
                logger.info(
                    "📝 %d ended sessions need summary generation",
                    len(sessions_with_data),
                )
                await self.generate_missing_summaries(sessions_with_data)
            else:
                logger.debug(
                    "🧹 AsyncStartupService: all ended sessions have summaries"
                )

            return deleted, closed, 0

        except Exception as exc:
            logger.error("❌ AsyncStartupService cleanup failed: %s", exc)
            raise

    async def generate_missing_summaries(
        self, sessions: List[Tuple[str, str, int]]
    ) -> None:
        """Generate summaries for ended sessions (async, delegates to sync manager)."""
        generated = 0
        failed = 0

        for session_id, user_id, interaction_count in sessions:
            if interaction_count == 0:
                continue
            try:
                # Summary generation itself is sync (LLM call) — run in executor
                loop = asyncio.get_running_loop()
                success = await loop.run_in_executor(
                    None, self._generate_one_summary, session_id
                )
                if success:
                    generated += 1
                else:
                    failed += 1
            except Exception as exc:
                failed += 1
                logger.warning(
                    "⚠️ Summary generation error for %s…: %s",
                    session_id[:16], exc,
                )

        if generated > 0 or failed > 0:
            logger.info(
                "📝 Async summary generation: %d succeeded, %d failed",
                generated, failed,
            )

    # ------------------------------------------------------------------
    # Private
    # ------------------------------------------------------------------

    def _generate_one_summary(self, session_id: str) -> bool:
        """Sync helper — runs in executor thread."""
        internal = getattr(self._session_manager, "manager", None)
        if internal is not None and hasattr(internal, "close_session"):
            summarizer = getattr(internal, "context_summarizer", None)
            if summarizer is None:
                return False
            return bool(internal.close_session(session_id, generate_summary=True))
        # Fallback
        return bool(self._session_manager.end_session(session_id))


# ---------------------------------------------------------------------------
# Sync bridge for Streamlit (or any sync caller)
# ---------------------------------------------------------------------------


def run_startup_cleanup_sync(session_manager: Any, orphan_cutoff_hours: int = 1) -> None:
    """Run the async startup cleanup from synchronous code.

    Uses ``asyncio.run()`` — safe to call from Streamlit top-level.
    """
    service = AsyncStartupService(
        session_manager=session_manager,
        orphan_cutoff_hours=orphan_cutoff_hours,
    )
    try:
        loop = asyncio.get_running_loop()
        # Already in an event loop — schedule as a task
        loop.create_task(service.run_startup_cleanup())
    except RuntimeError:
        # No running loop — safe to use asyncio.run()
        asyncio.run(service.run_startup_cleanup())
