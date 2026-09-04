from __future__ import annotations

import pytest

from utils.tab_runtime_health import (
    TabHealthError,
    collect_tab_health_snapshot,
)


class _FeedbackLoggerOk:
    def log_feedback(self, *args, **kwargs):
        return None

    def get_statistics(self, *args, **kwargs):
        return {}

    def get_advanced_analytics(self, *args, **kwargs):
        return {}

    def get_optimization_insights(self, *args, **kwargs):
        return {}


class _ModelLoader:
    pass


class _ChatLogic:
    pass


def _callable_cls(cls):
    return cls


def test_collect_snapshot_success_with_optional_features_disabled():
    snap = collect_tab_health_snapshot(
        ai_available=True,
        model_loader_cls=_callable_cls(_ModelLoader),
        chat_logic_cls=_callable_cls(_ChatLogic),
        feedback_enabled=False,
        feedback_logger=None,
        quality_enabled=False,
        quality_renderer=None,
    )

    data = snap.to_dict()
    assert data["ai_components_available"] is True
    assert data["feedback_logger_enabled"] is False
    assert data["feedback_logger_valid"] is False
    assert data["quality_dashboard_enabled"] is False
    assert data["quality_dashboard_valid"] is False


def test_collect_snapshot_success_with_all_features_enabled():
    snap = collect_tab_health_snapshot(
        ai_available=True,
        model_loader_cls=_callable_cls(_ModelLoader),
        chat_logic_cls=_callable_cls(_ChatLogic),
        feedback_enabled=True,
        feedback_logger=_FeedbackLoggerOk(),
        quality_enabled=True,
        quality_renderer=lambda: None,
    )

    data = snap.to_dict()
    assert data["feedback_logger_valid"] is True
    assert data["quality_dashboard_valid"] is True


def test_collect_snapshot_fails_without_ai_components():
    with pytest.raises(TabHealthError):
        collect_tab_health_snapshot(
            ai_available=False,
            model_loader_cls=None,
            chat_logic_cls=None,
            feedback_enabled=False,
            feedback_logger=None,
            quality_enabled=False,
            quality_renderer=None,
        )


def test_collect_snapshot_fails_for_incomplete_feedback_logger_contract():
    class BadFeedback:
        def log_feedback(self, *args, **kwargs):
            return None

    with pytest.raises(TabHealthError):
        collect_tab_health_snapshot(
            ai_available=True,
            model_loader_cls=_callable_cls(_ModelLoader),
            chat_logic_cls=_callable_cls(_ChatLogic),
            feedback_enabled=True,
            feedback_logger=BadFeedback(),
            quality_enabled=False,
            quality_renderer=None,
        )


def test_collect_snapshot_fails_for_non_callable_quality_renderer():
    with pytest.raises(TabHealthError):
        collect_tab_health_snapshot(
            ai_available=True,
            model_loader_cls=_callable_cls(_ModelLoader),
            chat_logic_cls=_callable_cls(_ChatLogic),
            feedback_enabled=False,
            feedback_logger=None,
            quality_enabled=True,
            quality_renderer="not-callable",
        )
