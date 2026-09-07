"""
Async Service Container — dual-mode (sync + async) DI container.

Extends the sync ``ServiceContainer`` with async-first service construction
and lifecycle management.  All async services (pool, startup, session-end)
are created lazily and run via ``await``.

For Streamlit (sync) callers the ``run_sync()`` bridge is provided.

Usage (async)::

    container = AsyncServiceContainer(ServiceConfig())
    await container.build_async(session_manager)
    await container.startup_cleanup_async()
    ...
    await container.shutdown_async()

Usage (sync bridge)::

    container = AsyncServiceContainer(ServiceConfig())
    container.build_sync(session_manager)
    container.startup_cleanup_sync()
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional

from wellbeing_session.services.service_container import ServiceContainer, ServiceConfig

logger = logging.getLogger(__name__)

# Optional imports — graceful degradation
_ASYNC_POOL_AVAILABLE = False
try:
    from database.async_connection_pool import AsyncConnectionPool, get_async_pool, close_all_async_pools
    from database.async_db_operations import (
        delete_empty_orphaned_sessions,
        close_orphaned_sessions_with_messages,
        find_sessions_needing_summary,
        get_kg_triples,
        get_session_interactions,
        get_user_sessions_async,
        set_session_end_time,
    )
    _ASYNC_POOL_AVAILABLE = True
except ImportError:
    pass

_OTEL_AVAILABLE = False
try:
    from observability.decorators import traced_async, metered_async
    _OTEL_AVAILABLE = True
except ImportError:
    def traced_async(*a: Any, **kw: Any) -> Any:  # type: ignore[misc]
        def d(fn: Any) -> Any: return fn
        if a and callable(a[0]): return a[0]
        return d
    def metered_async(*a: Any, **kw: Any) -> Any:  # type: ignore[misc]
        def d(fn: Any) -> Any: return fn
        if a and callable(a[0]): return a[0]
        return d


class AsyncServiceContainer:
    """Async-capable DI container wrapping the sync ``ServiceContainer``.

    Owns async lifecycle for:
    - ``AsyncConnectionPool``
    - Async startup cleanup
    - Async session-end workflow
    - Async context building (future)
    """

    DB_PATH: str = "wellbeing_store.db"

    def __init__(self, config: Optional[ServiceConfig] = None) -> None:
        self._sync_container = ServiceContainer(config)
        self._pool: Optional[Any] = None  # AsyncConnectionPool
        self._initialized: bool = False

    # ------------------------------------------------------------------
    # Sync container passthrough
    # ------------------------------------------------------------------

    @property
    def sync(self) -> ServiceContainer:
        """Access the underlying sync container."""
        return self._sync_container

    @property
    def pool(self) -> Optional[Any]:
        """The async connection pool (``None`` until ``build_async`` is called)."""
        return self._pool

    # ------------------------------------------------------------------
    # Async build
    # ------------------------------------------------------------------

    @traced_async("container.build_async")
    async def build_async(self, session_manager: Optional[Any] = None) -> "AsyncServiceContainer":
        """Build all sync services + initialize async pool.

        Args:
            session_manager: Existing ``SessionManagerAdapter``.

        Returns:
            ``self`` for fluent chaining.
        """
        # Build sync services first
        self._sync_container.build(session_manager)

        # Initialize async pool
        if _ASYNC_POOL_AVAILABLE:
            self._pool = await get_async_pool(self.DB_PATH)
            logger.info("✅ AsyncServiceContainer: async pool initialized")
        else:
            logger.warning("⚠️ AsyncServiceContainer: aiosqlite not available")

        self._initialized = True
        logger.info("✅ AsyncServiceContainer: build complete (async=%s)", _ASYNC_POOL_AVAILABLE)
        return self

    def build_sync(self, session_manager: Optional[Any] = None) -> "AsyncServiceContainer":
        """Sync bridge for ``build_async`` — for Streamlit callers."""
        self._sync_container.build(session_manager)
        if _ASYNC_POOL_AVAILABLE:
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(self._init_pool_async())
            except RuntimeError:
                asyncio.run(self._init_pool_async())
        self._initialized = True
        return self

    async def _init_pool_async(self) -> None:
        self._pool = await get_async_pool(self.DB_PATH)

    # ------------------------------------------------------------------
    # Async startup cleanup
    # ------------------------------------------------------------------

    @traced_async("container.startup_cleanup")
    @metered_async("container.startup_cleanup")
    async def startup_cleanup_async(self) -> Dict[str, int]:
        """Run full startup cleanup using async DB operations.

        Returns:
            Stats dict with ``deleted``, ``closed``, ``fixed``, ``summaries_needed``.
        """
        if not _ASYNC_POOL_AVAILABLE or self._pool is None:
            logger.warning("⚠️ Async pool unavailable — falling back to sync cleanup")
            self._sync_container.startup_service.run_startup_cleanup()
            return {"fallback": 1}

        from datetime import datetime, timedelta

        stats: Dict[str, int] = {"deleted": 0, "closed": 0, "fixed": 0, "summaries_needed": 0}

        async with self._pool.get_connection() as conn:
            cutoff = (datetime.now() - timedelta(hours=1)).isoformat()

            stats["deleted"] = await delete_empty_orphaned_sessions(conn, cutoff)
            stats["closed"] = await close_orphaned_sessions_with_messages(conn, cutoff)
            await conn.commit()

            sessions = await find_sessions_needing_summary(conn)
            stats["summaries_needed"] = len([s for s in sessions if s[2] > 0])

        logger.info(
            "🧹 Async startup cleanup: deleted=%d  closed=%d  summaries=%d",
            stats["deleted"], stats["closed"], stats["summaries_needed"],
        )
        return stats

    def startup_cleanup_sync(self) -> None:
        """Sync bridge for startup cleanup."""
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self.startup_cleanup_async())
        except RuntimeError:
            asyncio.run(self.startup_cleanup_async())

    # ------------------------------------------------------------------
    # Async context building
    # ------------------------------------------------------------------

    @traced_async("container.get_kg_triples")
    async def get_kg_triples_async(
        self, user_id: str, limit: int = 100
    ) -> List[Dict[str, Any]]:
        """Fetch knowledge-graph triples asynchronously."""
        if not _ASYNC_POOL_AVAILABLE or self._pool is None:
            return []
        async with self._pool.get_connection() as conn:
            return await get_kg_triples(conn, user_id, limit)

    @traced_async("container.get_session_interactions")
    async def get_session_interactions_async(
        self, session_id: str, limit: int = 50
    ) -> List[Dict[str, Any]]:
        """Fetch session interactions asynchronously."""
        if not _ASYNC_POOL_AVAILABLE or self._pool is None:
            return []
        async with self._pool.get_connection() as conn:
            return await get_session_interactions(conn, session_id, limit)

    @traced_async("container.get_user_sessions")
    async def get_user_sessions_async(
        self, user_id: str, status: str = "active", limit: int = 10
    ) -> List[Dict[str, Any]]:
        """Fetch user sessions asynchronously."""
        if not _ASYNC_POOL_AVAILABLE or self._pool is None:
            return []
        async with self._pool.get_connection() as conn:
            return await get_user_sessions_async(conn, user_id, status, limit)

    # ------------------------------------------------------------------
    # Async session end
    # ------------------------------------------------------------------

    @traced_async("container.end_session")
    async def end_session_async(self, session_id: str) -> bool:
        """End a session asynchronously (set end_time)."""
        if not _ASYNC_POOL_AVAILABLE or self._pool is None:
            # Fall back to sync
            result: bool = self._sync_container.session_manager.end_session(session_id)
            return result
        async with self._pool.get_connection() as conn:
            return await set_session_end_time(conn, session_id)

    # ------------------------------------------------------------------
    # Shutdown
    # ------------------------------------------------------------------

    async def shutdown_async(self) -> None:
        """Shut down async resources (pool, OTel, etc.)."""
        if _ASYNC_POOL_AVAILABLE:
            await close_all_async_pools()
        self._initialized = False
        logger.info("🔚 AsyncServiceContainer shut down")

    # ------------------------------------------------------------------
    # Delegated sync container attributes
    # ------------------------------------------------------------------

    def __getattr__(self, name: str) -> Any:
        """Delegate attribute lookups to the sync container."""
        return getattr(self._sync_container, name)
