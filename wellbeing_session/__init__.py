"""
Psychological Session Interface - Modular refactored version.

This package contains the refactored components of the WellbeingSessionInterface,
split into logical modules for better maintainability and testability.

Subpackages:
    - utils: Utility functions (datetime, text formatting, etc.)
    - adapters: SessionManager adapter
    - context: Context building logic (Phase 6a/6b)
    - handlers: Message and event handlers (Phase 4)
    - lifecycle: Session lifecycle management (Phase 6)
    - ui: UI rendering components (Phase 5)
    - services: Service Layer + DI container (Phase 7)
    - types: TypedDict definitions for type-safe data structures
"""
__version__ = "3.0.0"


