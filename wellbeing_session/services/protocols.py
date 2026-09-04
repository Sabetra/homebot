"""
Protocol definitions (interfaces) for all psychological session services.

These protocols define the contracts that services must fulfill,
enabling dependency inversion and testability. Any concrete class
that satisfies these protocols can be injected into the container.

Usage:
    class MockChatLogic:
        def chat(self, prompt: str, ...) -> str:
            return "mocked response"
    
    # MockChatLogic satisfies ChatLogicProtocol without inheriting it
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Protocol, runtime_checkable

logger = logging.getLogger(__name__)


@runtime_checkable
class ChatLogicProtocol(Protocol):
    """Protocol for the chat logic service (LLM interaction)."""

    model_loader: Any

    def chat(
        self,
        prompt: str,
        use_web_search: bool = False,
        use_agent_toolkit: bool = False,
    ) -> str:
        """Send a prompt to the LLM and return the response."""
        ...


@runtime_checkable
class ModelLoaderProtocol(Protocol):
    """Protocol for the model loader service."""

    def get_model(self) -> Any:
        """Return the loaded LLM model instance."""
        ...


@runtime_checkable
class SessionManagerProtocol(Protocol):
    """Protocol for session management operations."""

    def end_session(self, session_id: str) -> bool:
        """End a session and return success status."""
        ...

    def get_session_messages(self, session_id: str) -> List[Dict[str, Any]]:
        """Retrieve messages for a given session."""
        ...

    def get_user_sessions(self, user_id: str) -> List[Dict[str, Any]]:
        """Retrieve all sessions for a user."""
        ...


@runtime_checkable
class EmotionalAnalyzerProtocol(Protocol):
    """Protocol for emotional analysis."""

    chat_logic: Any

    def analyze(self, text: str) -> Any:
        """Analyze emotional content of text."""
        ...


@runtime_checkable
class ContextManagerProtocol(Protocol):
    """Protocol for chat context management (token budgeting)."""

    def get_context_window(self) -> int:
        """Return the available context window in tokens."""
        ...


@runtime_checkable
class ProfileCacheProtocol(Protocol):
    """Protocol for the profile cache manager."""

    synthesizer: Any

    def get_profile(self, user_id: str) -> Optional[Dict[str, Any]]:
        """Get cached user profile."""
        ...

    def invalidate(self, user_id: str) -> None:
        """Invalidate cached profile for user."""
        ...


@runtime_checkable
class InsightExtractorProtocol(Protocol):
    """Protocol for extracting user insights from sessions."""

    chat_function: Any

    def extract_insights(self, session_id: str) -> Dict[str, Any]:
        """Extract insights from a session."""
        ...


# ---------------------------------------------------------------------------
# Async Protocol Variants (SOTA: dual-mode sync/async architecture)
# ---------------------------------------------------------------------------


@runtime_checkable
class AsyncSessionManagerProtocol(Protocol):
    """Async protocol for session management operations."""

    async def end_session_async(self, session_id: str) -> bool:
        """End a session asynchronously."""
        ...

    async def get_session_messages_async(self, session_id: str) -> List[Dict[str, Any]]:
        """Retrieve messages for a session asynchronously."""
        ...

    async def get_user_sessions_async(self, user_id: str) -> List[Dict[str, Any]]:
        """Retrieve all sessions for a user asynchronously."""
        ...


@runtime_checkable
class AsyncStartupServiceProtocol(Protocol):
    """Async protocol for startup cleanup."""

    async def run_startup_cleanup(self) -> tuple[int, int, int]:
        """Execute the full startup-cleanup pipeline asynchronously."""
        ...

    async def generate_missing_summaries(
        self, sessions: List[Any]
    ) -> None:
        """Generate summaries for ended sessions asynchronously."""
        ...


@runtime_checkable
class AsyncDBPoolProtocol(Protocol):
    """Async protocol for the database connection pool."""

    async def initialize(self) -> None:
        """Pre-warm the pool."""
        ...

    async def close(self) -> None:
        """Close all connections."""
        ...


# ---------------------------------------------------------------------------
# Phase 9: Async Handler + Lifecycle + Workflow Protocols
# ---------------------------------------------------------------------------


@runtime_checkable
class AsyncResponseGeneratorProtocol(Protocol):
    """Async protocol for response generation."""

    async def generate_psychological_response(
        self,
        user_input: str,
        session_id: str,
        user_id: str,
        build_context_func: Any,
        format_context_func: Any,
        load_history_func: Any = None,
    ) -> str:
        """Generate an AI response asynchronously."""
        ...


@runtime_checkable
class AsyncMessageHandlerProtocol(Protocol):
    """Async protocol for message handling."""

    async def handle_psychological_message(
        self,
        user_message: str,
        ai_response: str,
        session_id: str,
        psych_enabled: bool,
        build_context_func: Any,
        format_context_func: Any,
    ) -> str:
        """Process a psychological message asynchronously."""
        ...


@runtime_checkable
class AsyncLifecycleManagerProtocol(Protocol):
    """Async protocol for session lifecycle management."""

    async def cleanup_orphaned_sessions_async(self) -> Any:
        """Clean up orphaned sessions asynchronously."""
        ...

    async def end_session_async(self, session_id: str) -> bool:
        """End a session asynchronously."""
        ...


@runtime_checkable
class WorkflowGraphProtocol(Protocol):
    """Protocol for the session workflow graph (LangGraph-style)."""

    def run(self, state: Any) -> Any:
        """Execute the graph synchronously."""
        ...

    async def run_async(self, state: Any) -> Any:
        """Execute the graph asynchronously."""
        ...

    @property
    def node_names(self) -> List[str]:
        """Return all registered node names."""
        ...


__all__ = [
    "ChatLogicProtocol",
    "ModelLoaderProtocol",
    "SessionManagerProtocol",
    "EmotionalAnalyzerProtocol",
    "ContextManagerProtocol",
    "ProfileCacheProtocol",
    "InsightExtractorProtocol",
    "AsyncSessionManagerProtocol",
    "AsyncStartupServiceProtocol",
    "AsyncDBPoolProtocol",
    "AsyncResponseGeneratorProtocol",
    "AsyncMessageHandlerProtocol",
    "AsyncLifecycleManagerProtocol",
    "WorkflowGraphProtocol",
]
