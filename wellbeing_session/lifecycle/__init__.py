"""
Session lifecycle management package.

Handles the complete lifecycle of psychological sessions:
- Session creation and startup
- Session cleanup and maintenance
- Session ending and finalization
- Insight extraction

Extracted from wellbeing_session_interface.py as part of Phase 6 refactoring.
✅ Phase 9: Added async lifecycle manager.
"""

from .session_lifecycle_manager import SessionLifecycleManager
from .async_session_lifecycle import AsyncSessionLifecycleManager

__all__ = [
    'SessionLifecycleManager',
    'AsyncSessionLifecycleManager',
]

