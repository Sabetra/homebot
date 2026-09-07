"""
UI package for psychological session interface.

This package contains UI renderer classes for displaying psychological
session interfaces.

Modules:
    - session_management_renderer: Session management UI
    - active_session_renderer: Active session UI
    - welcome_renderer: Welcome interface UI

Extracted from wellbeing_session_interface.py as part of Phase 5 refactoring.
"""

from typing import Any

__all__ = [
    "SessionManagementRenderer",
    "ActiveSessionRenderer",
    "WelcomeRenderer",
    "GoalProgressRenderer",
]


def __getattr__(name: str) -> Any:
    """Lazy exports to avoid importing Streamlit-heavy modules at package import time."""
    if name == "SessionManagementRenderer":
        from .session_management_renderer import SessionManagementRenderer

        return SessionManagementRenderer
    if name == "ActiveSessionRenderer":
        from .active_session_renderer import ActiveSessionRenderer

        return ActiveSessionRenderer
    if name == "WelcomeRenderer":
        from .welcome_renderer import WelcomeRenderer

        return WelcomeRenderer
    if name == "GoalProgressRenderer":
        from .goal_progress_renderer import GoalProgressRenderer

        return GoalProgressRenderer
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

