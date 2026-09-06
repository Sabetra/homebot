from __future__ import annotations

import pytest

from wellbeing_session.context.context_builder import PsychologicalContextBuilder
from wellbeing_session.context.session_context_builder import SessionContextBuilder


class _DummySessionManager:
    manager = None


class _ProfileCacheRuntimeError:
    def get_cached_profile(self, user_id: str):
        raise RuntimeError(f"DB load failed for user {user_id}")


class _ProfileCacheOk:
    def get_cached_profile(self, user_id: str):
        class _Profile:
            def to_context_dict(self):
                return {
                    "core_personality": {},
                    "current_state": {},
                    "relationships": {},
                    "goals_and_growth": {},
                    "coping_and_resources": {},
                    "therapeutic_focus": {},
                    "overall_confidence": 0.7,
                    "version": 1,
                    "updated_at": "2026-05-26T00:00:00+00:00",
                }

        return _Profile()


def test_session_context_builder_propagates_runtime_profile_errors():
    builder = SessionContextBuilder(
        session_manager=_DummySessionManager(),
        profile_cache=_ProfileCacheRuntimeError(),
    )

    with pytest.raises(RuntimeError, match="DB load failed"):
        builder._load_persistent_profile("user-1")


def test_psychological_context_builder_propagates_runtime_profile_errors():
    builder = PsychologicalContextBuilder(
        session_manager=_DummySessionManager(),
        profile_cache=_ProfileCacheRuntimeError(),
        enable_monitoring=False,
    )

    with pytest.raises(RuntimeError, match="DB load failed"):
        builder._gather_persistent_profile("user-1")


def test_extract_sources_supports_previous_sessions_alias():
    builder = PsychologicalContextBuilder(
        session_manager=_DummySessionManager(),
        profile_cache=_ProfileCacheOk(),
        enable_monitoring=False,
    )

    sources = builder._extract_sources(
        {
            "knowledge_graph": [{"subject": "u"}],
            "previous_sessions": [{"session_id": "s1"}],
            "persistent_profile": {"overall_confidence": 0.8},
        }
    )

    assert "knowledge_graph" in sources
    assert "session_summaries" in sources
    assert "persistent_profile" in sources
