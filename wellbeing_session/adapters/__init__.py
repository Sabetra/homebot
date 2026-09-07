"""
Adapter modules for psychological session interface.

This package contains adapter classes that provide backward compatibility
between old and new APIs.
"""
from .session_manager_adapter import SessionManagerAdapter

__all__ = ['SessionManagerAdapter']
