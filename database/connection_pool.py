"""
Database Connection Pool Manager
=================================
Thread-safe connection pooling for SQLite databases.

Features:
- Configurable pool size
- Connection reuse
- Automatic connection cleanup
- Thread-safe operations
- Connection health checks

Author: Refactoring Team
Date: 2026-02-14
Phase: Database Connection Pooling (SOFORT Priority)
"""

import sqlite3
import threading
import time
import logging
from typing import Optional, Dict, Any
from contextlib import contextmanager
from queue import Queue, Empty, Full
from pathlib import Path

logger = logging.getLogger(__name__)


class ConnectionPool:
    """
    Thread-safe SQLite connection pool.
    
    Features:
    - Connection reuse (reduces overhead)
    - Configurable pool size
    - Connection validation
    - Automatic cleanup
    - Thread-safe operations
    
    Usage:
        pool = ConnectionPool("db.sqlite", pool_size=5)
        
        with pool.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM table")
            results = cursor.fetchall()
    """
    
    def __init__(
        self,
        db_path: str,
        pool_size: int = 5,
        max_overflow: int = 10,
        timeout: float = 30.0,
        check_same_thread: bool = False
    ):
        """
        Initialize connection pool.
        
        Args:
            db_path: Path to SQLite database
            pool_size: Number of persistent connections
            max_overflow: Additional connections allowed temporarily
            timeout: Timeout for acquiring connection (seconds)
            check_same_thread: SQLite same_thread check
        """
        self.db_path = Path(db_path)
        self.pool_size = pool_size
        self.max_overflow = max_overflow
        self.timeout = timeout
        self.check_same_thread = check_same_thread
        
        # Connection pool (Queue is thread-safe)
        self._pool: Queue = Queue(maxsize=pool_size)
        
        # Overflow connections tracking
        self._overflow_connections = 0
        self._overflow_lock = threading.Lock()
        
        # Statistics
        self._stats: Dict[str, int | float] = {
            'connections_created': 0,
            'connections_reused': 0,
            'connections_closed': 0,
            'pool_hits': 0,
            'pool_misses': 0,
            'overflow_used': 0
        }
        self._stats_lock = threading.Lock()
        
        # Initialize pool
        self._initialize_pool()
        
        logger.info(
            f"✅ Connection pool initialized: {self.db_path.name}, "
            f"pool_size={pool_size}, max_overflow={max_overflow}"
        )
    
    def _initialize_pool(self):
        """Initialize connection pool with connections."""
        for _ in range(self.pool_size):
            try:
                conn = self._create_connection()
                self._pool.put(conn, block=False)
            except Exception as e:
                logger.error(f"❌ Failed to initialize pool connection: {e}")
    
    def _create_connection(self) -> sqlite3.Connection:
        """
        Create new database connection.
        
        Returns:
            Fresh SQLite connection
        """
        try:
            conn = sqlite3.connect(
                str(self.db_path),
                check_same_thread=self.check_same_thread,
                timeout=self.timeout
            )

            # Optimize connection settings
            conn.row_factory = sqlite3.Row  # Dict-like rows
            # DELETE journal mode: no -shm/-wal/-journal disk files — critical when parent dir is readonly
            conn.execute("PRAGMA journal_mode=DELETE")
            conn.execute("PRAGMA synchronous=NORMAL")  # Good balance
            conn.execute("PRAGMA cache_size=-64000")  # 64MB cache
            conn.execute("PRAGMA temp_store=MEMORY")  # Fast temp operations
            conn.execute("PRAGMA foreign_keys=ON")
            
            with self._stats_lock:
                self._stats['connections_created'] += 1
            
            logger.debug(f"✅ Created new connection to {self.db_path.name}")
            return conn
            
        except Exception as e:
            logger.error(f"❌ Failed to create connection: {e}")
            raise
    
    def _validate_connection(self, conn: sqlite3.Connection) -> bool:
        """
        Check if connection is still valid.
        
        Args:
            conn: Connection to validate
            
        Returns:
            True if valid, False otherwise
        """
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT 1")
            cursor.fetchone()
            return True
        except Exception as e:
            logger.warning(f"⚠️ Connection validation failed: {e}")
            return False
    
    @contextmanager
    def get_connection(self):
        """
        Get connection from pool (context manager).
        
        Yields:
            Database connection
            
        Example:
            with pool.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM table")
        """
        conn = None
        from_overflow = False
        
        try:
            # Try to get from pool (non-blocking)
            try:
                conn = self._pool.get(block=False)
                with self._stats_lock:
                    self._stats['pool_hits'] += 1
                    self._stats['connections_reused'] += 1
                
                # Validate connection
                if not self._validate_connection(conn):
                    logger.warning("⚠️ Connection invalid, creating new one")
                    conn.close()
                    conn = self._create_connection()
                
                logger.debug(f"♻️ Reused connection from pool")
                
            except Empty:
                # Pool empty, try overflow
                with self._overflow_lock:
                    if self._overflow_connections < self.max_overflow:
                        self._overflow_connections += 1
                        from_overflow = True
                        with self._stats_lock:
                            self._stats['pool_misses'] += 1
                            self._stats['overflow_used'] += 1
                        
                        conn = self._create_connection()
                        logger.debug(f"🔄 Created overflow connection ({self._overflow_connections}/{self.max_overflow})")
                    else:
                        # Wait for available connection
                        with self._stats_lock:
                            self._stats['pool_misses'] += 1
                        
                        logger.debug(f"⏳ Waiting for connection (timeout={self.timeout}s)")
                        conn = self._pool.get(block=True, timeout=self.timeout)
                        
                        # Validate after waiting
                        if not self._validate_connection(conn):
                            conn.close()
                            conn = self._create_connection()
            
            # Yield connection to caller
            yield conn
            
        except Empty:
            logger.error(f"❌ Timeout waiting for connection ({self.timeout}s)")
            raise TimeoutError(f"Could not acquire connection within {self.timeout}s")
            
        except Exception as e:
            logger.error(f"❌ Error in get_connection: {e}")
            if conn:
                try:
                    conn.rollback()  # Rollback on error
                except sqlite3.Error:
                    pass
            raise
            
        finally:
            # Return connection to pool
            if conn:
                try:
                    # Ensure clean state
                    conn.rollback()  # Clear any uncommitted transactions
                    
                    if from_overflow:
                        # Close overflow connections
                        conn.close()
                        with self._overflow_lock:
                            self._overflow_connections -= 1
                        with self._stats_lock:
                            self._stats['connections_closed'] += 1
                        logger.debug("🔚 Closed overflow connection")
                    else:
                        # Return to pool
                        try:
                            self._pool.put(conn, block=False)
                            logger.debug("♻️ Returned connection to pool")
                        except Full:
                            # Pool full (shouldn't happen but handle gracefully)
                            conn.close()
                            with self._stats_lock:
                                self._stats['connections_closed'] += 1
                            logger.warning("⚠️ Pool full, closed connection")
                            
                except Exception as e:
                    logger.error(f"❌ Error returning connection: {e}")
    
    def get_stats(self) -> Dict[str, Any]:
        """
        Get pool statistics.
        
        Returns:
            Dictionary with pool stats
        """
        with self._stats_lock:
            stats = self._stats.copy()
        
        stats['pool_size'] = self.pool_size
        stats['pool_available'] = self._pool.qsize()
        stats['overflow_active'] = self._overflow_connections
        stats['hit_rate'] = (
            stats['pool_hits'] / max(stats['pool_hits'] + stats['pool_misses'], 1)
        )
        
        return stats
    
    def close(self):
        """Close all connections in pool."""
        logger.info(f"🔚 Closing connection pool: {self.db_path.name}")
        
        closed = 0
        while not self._pool.empty():
            try:
                conn = self._pool.get(block=False)
                conn.close()
                closed += 1
                with self._stats_lock:
                    self._stats['connections_closed'] += 1
            except Empty:
                break
            except Exception as e:
                logger.error(f"❌ Error closing connection: {e}")
        
        logger.info(f"✅ Closed {closed} connections")
        
        # Log final stats
        stats = self.get_stats()
        logger.info(
            f"📊 Final stats: "
            f"created={stats['connections_created']}, "
            f"reused={stats['connections_reused']}, "
            f"closed={stats['connections_closed']}, "
            f"hit_rate={stats['hit_rate']:.1%}"
        )
    
    def __enter__(self):
        """Context manager entry."""
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.close()
        return False


# Singleton pool manager
_pools: Dict[str, ConnectionPool] = {}
_pools_lock = threading.Lock()


def get_pool(
    db_path: str,
    pool_size: int = 5,
    max_overflow: int = 10
) -> ConnectionPool:
    """
    Get or create connection pool for database (singleton pattern).
    
    Args:
        db_path: Path to database
        pool_size: Number of persistent connections
        max_overflow: Additional overflow connections
        
    Returns:
        Connection pool instance
        
    Example:
        pool = get_pool("wellbeing_store.db")
        with pool.get_connection() as conn:
            # Use connection
            pass
    """
    db_path = str(Path(db_path).resolve())
    
    with _pools_lock:
        if db_path not in _pools:
            _pools[db_path] = ConnectionPool(
                db_path=db_path,
                pool_size=pool_size,
                max_overflow=max_overflow
            )
            logger.info(f"✅ Created new pool for: {Path(db_path).name}")
        
        return _pools[db_path]


def close_all_pools():
    """Close all connection pools."""
    with _pools_lock:
        for db_path, pool in _pools.items():
            logger.info(f"🔚 Closing pool: {Path(db_path).name}")
            pool.close()
        _pools.clear()
    
    logger.info("✅ All pools closed")
