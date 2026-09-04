"""
Async Connection Pool for SQLite — aiosqlite-based.

Provides an async counterpart to ``database/connection_pool.py`` that:
- Uses ``aiosqlite`` for non-blocking DB access.
- Maintains a bounded connection pool via ``asyncio.Queue``.
- Supports overflow connections when the pool is exhausted.
- Exposes an ``async with pool.get_connection()`` context-manager.
- Tracks the same statistics as the sync pool (hits, misses, overflow).

Usage::

    pool = AsyncConnectionPool("wellbeing_store.db", pool_size=5)
    await pool.initialize()

    async with pool.get_connection() as conn:
        cursor = await conn.execute("SELECT * FROM table")
        rows = await cursor.fetchall()

    await pool.close()

Design notes
~~~~~~~~~~~~
* ``aiosqlite.Connection`` wraps a real ``sqlite3.Connection`` on a
  background thread, so write-contention is still single-writer.
  This is fine for our workload (read-heavy, low-frequency writes).
* The pool pre-warms *pool_size* connections on ``initialize()`` —
  callers that forget to call it get lazy-init on first ``get_connection()``.
* Stats are protected by ``asyncio.Lock`` (not threading.Lock).
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator, Dict

import aiosqlite

logger = logging.getLogger(__name__)


class AsyncConnectionPool:
    """Async connection pool for SQLite via *aiosqlite*."""

    def __init__(
        self,
        db_path: str,
        pool_size: int = 5,
        max_overflow: int = 10,
        timeout: float = 30.0,
    ) -> None:
        self.db_path = Path(db_path)
        self.pool_size = pool_size
        self.max_overflow = max_overflow
        self.timeout = timeout

        self._pool: asyncio.Queue[aiosqlite.Connection] = asyncio.Queue(maxsize=pool_size)
        self._overflow_count: int = 0
        self._overflow_lock = asyncio.Lock()
        self._initialized: bool = False

        self._stats_lock = asyncio.Lock()
        self._stats: Dict[str, int | float] = {
            "connections_created": 0,
            "connections_reused": 0,
            "connections_closed": 0,
            "pool_hits": 0,
            "pool_misses": 0,
            "overflow_used": 0,
        }

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def initialize(self) -> None:
        """Pre-warm the pool with *pool_size* connections."""
        if self._initialized:
            return
        for _ in range(self.pool_size):
            conn = await self._create_connection()
            await self._pool.put(conn)
        self._initialized = True
        logger.info(
            "✅ AsyncConnectionPool initialised: %s  pool_size=%d  max_overflow=%d",
            self.db_path.name,
            self.pool_size,
            self.max_overflow,
        )

    async def close(self) -> None:
        """Close every connection in the pool."""
        closed = 0
        while not self._pool.empty():
            try:
                conn = self._pool.get_nowait()
                await conn.close()
                closed += 1
            except asyncio.QueueEmpty:
                break
        async with self._stats_lock:
            self._stats["connections_closed"] += closed
        self._initialized = False
        logger.info("✅ AsyncConnectionPool closed (%d connections)", closed)

    # ------------------------------------------------------------------
    # Connection acquisition
    # ------------------------------------------------------------------

    @asynccontextmanager
    async def get_connection(self) -> AsyncIterator[aiosqlite.Connection]:
        """Acquire a pooled connection (async context-manager).

        Falls back to overflow if the pool is empty, then blocks with
        timeout as a last resort.
        """
        if not self._initialized:
            await self.initialize()

        conn: aiosqlite.Connection | None = None
        from_overflow = False

        try:
            # 1. Try non-blocking pool fetch
            try:
                conn = self._pool.get_nowait()
                async with self._stats_lock:
                    self._stats["pool_hits"] += 1
                    self._stats["connections_reused"] += 1

                # Validate
                if not await self._validate(conn):
                    await conn.close()
                    conn = await self._create_connection()

            except asyncio.QueueEmpty:
                # 2. Try overflow
                async with self._overflow_lock:
                    if self._overflow_count < self.max_overflow:
                        self._overflow_count += 1
                        from_overflow = True

                if from_overflow:
                    async with self._stats_lock:
                        self._stats["pool_misses"] += 1
                        self._stats["overflow_used"] += 1
                    conn = await self._create_connection()
                else:
                    # 3. Block until available
                    async with self._stats_lock:
                        self._stats["pool_misses"] += 1
                    conn = await asyncio.wait_for(
                        self._pool.get(), timeout=self.timeout
                    )
                    if not await self._validate(conn):
                        await conn.close()
                        conn = await self._create_connection()

            yield conn

        except asyncio.TimeoutError:
            raise TimeoutError(
                f"AsyncConnectionPool: could not acquire connection within {self.timeout}s"
            )
        except Exception:
            if conn is not None:
                try:
                    await conn.rollback()
                except Exception:
                    pass
            raise
        finally:
            if conn is not None:
                try:
                    await conn.rollback()  # clear uncommitted state
                except Exception:
                    pass

                if from_overflow:
                    await conn.close()
                    async with self._overflow_lock:
                        self._overflow_count -= 1
                    async with self._stats_lock:
                        self._stats["connections_closed"] += 1
                else:
                    try:
                        self._pool.put_nowait(conn)
                    except asyncio.QueueFull:
                        await conn.close()
                        async with self._stats_lock:
                            self._stats["connections_closed"] += 1

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _create_connection(self) -> aiosqlite.Connection:
        conn = await aiosqlite.connect(
            str(self.db_path),
            timeout=self.timeout,
        )
        conn.row_factory = aiosqlite.Row
        await conn.execute("PRAGMA journal_mode=WAL")
        await conn.execute("PRAGMA synchronous=NORMAL")
        await conn.execute("PRAGMA cache_size=-64000")
        await conn.execute("PRAGMA temp_store=MEMORY")
        await conn.execute("PRAGMA foreign_keys=ON")

        async with self._stats_lock:
            self._stats["connections_created"] += 1
        return conn

    @staticmethod
    async def _validate(conn: aiosqlite.Connection) -> bool:
        try:
            cursor = await conn.execute("SELECT 1")
            await cursor.fetchone()
            return True
        except Exception:
            return False

    async def get_stats(self) -> Dict[str, Any]:
        async with self._stats_lock:
            stats = self._stats.copy()
        stats["pool_size"] = self.pool_size
        stats["pool_available"] = self._pool.qsize()
        stats["overflow_active"] = self._overflow_count
        total = stats["pool_hits"] + stats["pool_misses"]
        stats["hit_rate"] = stats["pool_hits"] / max(total, 1)
        return stats

    # ------------------------------------------------------------------
    # Context-manager for the pool itself
    # ------------------------------------------------------------------

    async def __aenter__(self) -> "AsyncConnectionPool":
        await self.initialize()
        return self

    async def __aexit__(self, *exc: Any) -> bool:
        await self.close()
        return False


# ---------------------------------------------------------------------------
# Singleton registry (mirrors sync ``get_pool`` / ``close_all_pools``)
# ---------------------------------------------------------------------------

_pools: Dict[str, AsyncConnectionPool] = {}
_pools_lock = asyncio.Lock()


async def get_async_pool(
    db_path: str,
    pool_size: int = 5,
    max_overflow: int = 10,
) -> AsyncConnectionPool:
    """Get or create an async connection pool (singleton per resolved path)."""
    resolved = str(Path(db_path).resolve())
    async with _pools_lock:
        if resolved not in _pools:
            pool = AsyncConnectionPool(
                db_path=resolved,
                pool_size=pool_size,
                max_overflow=max_overflow,
            )
            await pool.initialize()
            _pools[resolved] = pool
            logger.info("✅ Created async pool for: %s", Path(resolved).name)
        return _pools[resolved]


async def close_all_async_pools() -> None:
    """Close every registered async pool."""
    async with _pools_lock:
        for path, pool in _pools.items():
            logger.info("🔚 Closing async pool: %s", Path(path).name)
            await pool.close()
        _pools.clear()
    logger.info("✅ All async pools closed")
