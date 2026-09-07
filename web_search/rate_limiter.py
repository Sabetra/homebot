"""
Rate Limiter
=============

Token-bucket rate limiter for DDG API calls.
Prevents 429 errors and API throttling.

Author: SOTA Web Search Upgrade
Date: 2026-03-08
"""

import logging
import threading
import time
from typing import Optional

logger = logging.getLogger(__name__)


class RateLimiter:
    """
    Token-bucket rate limiter.
    
    Allows a configurable number of requests per time window.
    Blocks (sleeps) when rate limit is exceeded.
    
    Default: 5 requests per 10 seconds (conservative for DDG).
    """
    
    def __init__(
        self,
        max_requests: int = 5,
        window_seconds: float = 10.0,
    ) -> None:
        """
        Args:
            max_requests: Maximum requests per window
            window_seconds: Time window in seconds
        """
        self._max_requests = max_requests
        self._window = window_seconds
        self._timestamps: list = []
        self._lock = threading.Lock()
        self._total_waits = 0
        self._total_wait_time = 0.0
        
        logger.debug(
            f"RateLimiter: {max_requests} req / {window_seconds}s"
        )
    
    def acquire(self, timeout: Optional[float] = 30.0) -> bool:
        """
        Acquire permission to make a request.
        
        Blocks until a slot is available or timeout is reached.
        
        Args:
            timeout: Maximum time to wait (seconds). None = wait forever.
            
        Returns:
            True if permission granted, False if timeout
        """
        start = time.time()
        
        while True:
            with self._lock:
                now = time.time()
                
                # Remove timestamps outside window
                self._timestamps = [
                    t for t in self._timestamps
                    if now - t < self._window
                ]
                
                # Check if we can proceed
                if len(self._timestamps) < self._max_requests:
                    self._timestamps.append(now)
                    return True
                
                # Calculate wait time
                oldest = self._timestamps[0]
                wait = self._window - (now - oldest) + 0.05  # small buffer
            
            # Check timeout
            elapsed = time.time() - start
            if timeout is not None and elapsed + wait > timeout:
                logger.warning(f"Rate limiter timeout after {elapsed:.1f}s")
                return False
            
            # Wait
            logger.debug(f"Rate limited, waiting {wait:.1f}s...")
            self._total_waits += 1
            self._total_wait_time += min(wait, 1.0)
            time.sleep(min(wait, 1.0))  # Sleep in chunks for responsiveness
    
    def get_stats(self) -> dict:
        """Get rate limiter statistics."""
        return {
            "total_waits": self._total_waits,
            "total_wait_time_s": self._total_wait_time,
            "current_window_usage": len(self._timestamps),
            "max_per_window": self._max_requests,
            "window_seconds": self._window,
        }


# Singleton
_limiter: Optional[RateLimiter] = None
_limiter_lock = threading.Lock()


def get_rate_limiter() -> RateLimiter:
    """Get or create the singleton RateLimiter."""
    global _limiter
    if _limiter is None:
        with _limiter_lock:
            if _limiter is None:
                _limiter = RateLimiter()
    return _limiter


__all__ = ["RateLimiter", "get_rate_limiter"]
