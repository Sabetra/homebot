from __future__ import annotations

import logging
from typing import Any

import pytest

from wellbeing_session.handlers.async_message_handler import AsyncMessageHandler
from wellbeing_session.handlers.message_handler import MessageHandler


class _SessionManagerStub:
    def __init__(self, user_id: str | None) -> None:
        self.user_id = user_id

    def get_session_summary(self, session_id: str) -> dict[str, Any]:
        return {"session_id": session_id, "user_id": self.user_id}


class _ProfileCacheSpy:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def invalidate_profile(self, **kwargs: Any) -> None:
        self.calls.append(kwargs)


class _FailingProfileCache:
    def invalidate_profile(self, **kwargs: Any) -> None:
        raise RuntimeError("cache unavailable")


@pytest.mark.parametrize("handler_type", [MessageHandler, AsyncMessageHandler])
def test_handler_invalidates_injected_profile_cache(handler_type):
    profile_cache = _ProfileCacheSpy()
    handler = handler_type(
        session_manager=_SessionManagerStub("user-a"),
        emotional_analyzer=None,
        profile_cache=profile_cache,
    )

    handler._invalidate_profile_cache("session-a")

    assert profile_cache.calls == [
        {
            "user_id": "user-a",
            "trigger_type": "new_interaction",
            "trigger_source_id": "session-a",
        }
    ]


@pytest.mark.parametrize("handler_type", [MessageHandler, AsyncMessageHandler])
def test_handler_skips_profile_invalidation_without_user_id(handler_type):
    profile_cache = _ProfileCacheSpy()
    handler = handler_type(
        session_manager=_SessionManagerStub(None),
        emotional_analyzer=None,
        profile_cache=profile_cache,
    )

    handler._invalidate_profile_cache("session-a")

    assert profile_cache.calls == []


@pytest.mark.parametrize("handler_type", [MessageHandler, AsyncMessageHandler])
def test_handler_logs_profile_cache_failure_without_raising(handler_type, caplog):
    handler = handler_type(
        session_manager=_SessionManagerStub("user-a"),
        emotional_analyzer=None,
        profile_cache=_FailingProfileCache(),
    )

    with caplog.at_level(logging.WARNING):
        handler._invalidate_profile_cache("session-a")

    assert "cache unavailable" in caplog.text
    assert "session-a" in caplog.text
