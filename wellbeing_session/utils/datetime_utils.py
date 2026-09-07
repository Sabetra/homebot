"""
Datetime Utility Functions for Psychological Session Management
================================================================

Helper functions for normalizing and handling datetime objects
across the psychological session system.

Author: Refactoring Team
Date: 2026-02-14
Phase: Code-Splitting (Phase 1: Utilities)
"""

from datetime import datetime, timezone
from typing import Any


def normalize_datetime(dt: Any) -> datetime:
    """
    Normalisiert ein Datetime-Objekt zu timezone-aware UTC.
    
    Handles various datetime formats and ensures all datetime objects
    are timezone-aware and in UTC timezone.
    
    Args:
        dt: Datetime-Objekt, String oder None
        
    Returns:
        Timezone-aware datetime in UTC
        
    Examples:
        >>> normalize_datetime(None)
        datetime.datetime(2026, 2, 14, ..., tzinfo=timezone.utc)
        
        >>> normalize_datetime("2026-02-14 10:30:00")
        datetime.datetime(2026, 2, 14, 10, 30, tzinfo=timezone.utc)
        
        >>> normalize_datetime(datetime(2026, 2, 14, 10, 30))
        datetime.datetime(2026, 2, 14, 10, 30, tzinfo=timezone.utc)
    """
    if dt is None:
        return datetime.now(timezone.utc)
    
    # String zu datetime konvertieren
    result: datetime
    if isinstance(dt, str):
        # Handle ISO format with 'Z' or timezone offset
        dt_str = dt.replace('Z', '+00:00')
        try:
            result = datetime.fromisoformat(dt_str)
        except ValueError:
            # Fallback: try basic parsing
            try:
                result = datetime.strptime(dt_str, '%Y-%m-%d %H:%M:%S.%f')
            except ValueError:
                result = datetime.strptime(dt_str, '%Y-%m-%d %H:%M:%S')
    else:
        # At this point, dt must be datetime (not str)
        assert isinstance(dt, datetime), f"Expected datetime, got {type(dt)}"
        result = dt
    
    # Wenn offset-naive, dann als UTC interpretieren
    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)
    # Wenn andere Timezone, zu UTC konvertieren
    elif result.tzinfo != timezone.utc:
        result = result.astimezone(timezone.utc)
    
    return result


def get_sort_time(session: dict) -> datetime:
    """
    Extrahiert und normalisiert Sortier-Zeit aus Session-Dictionary.
    
    Used for sorting sessions by their last update time.
    
    Args:
        session: Session dictionary with 'updated_at' or 'created_at' field
        
    Returns:
        Timezone-aware datetime for sorting
        
    Examples:
        >>> session = {'updated_at': '2026-02-14 10:30:00'}
        >>> get_sort_time(session)
        datetime.datetime(2026, 2, 14, 10, 30, tzinfo=timezone.utc)
    """
    updated_at = session.get('updated_at') or session.get('created_at')
    result = normalize_datetime(updated_at)
    # Ensure we return a datetime (normalize_datetime already handles this)
    return result if isinstance(result, datetime) else datetime.now(timezone.utc)
