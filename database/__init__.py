"""
Database package — connection pooling (sync + async) and DB operations.

Exports:
    ConnectionPool          — sync thread-safe connection pool
    AsyncConnectionPool     — async aiosqlite-based pool
    get_pool                — sync singleton pool factory
    get_async_pool          — async singleton pool factory
"""

from database.connection_pool import ConnectionPool, get_pool, close_all_pools

__all__ = [
    "ConnectionPool",
    "get_pool",
    "close_all_pools",
]

# Async pool is optional (requires aiosqlite)
try:
    from database.async_connection_pool import (
        AsyncConnectionPool,
        get_async_pool,
        close_all_async_pools,
    )
    __all__ += [
        "AsyncConnectionPool",
        "get_async_pool",
        "close_all_async_pools",
    ]
except ImportError:
    pass
