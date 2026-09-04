"""Runtime contracts and health checks for Streamlit main tabs.

Fail-fast design: critical contract violations raise ``TabHealthError``
instead of silently degrading UI behavior.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict


class TabHealthError(RuntimeError):
    """Raised when a tab dependency contract is violated."""


@dataclass(frozen=True)
class TabHealthSnapshot:
    ai_components_available: bool
    feedback_logger_enabled: bool
    feedback_logger_valid: bool
    quality_dashboard_enabled: bool
    quality_dashboard_valid: bool

    def to_dict(self) -> Dict[str, bool]:
        return {
            "ai_components_available": self.ai_components_available,
            "feedback_logger_enabled": self.feedback_logger_enabled,
            "feedback_logger_valid": self.feedback_logger_valid,
            "quality_dashboard_enabled": self.quality_dashboard_enabled,
            "quality_dashboard_valid": self.quality_dashboard_valid,
        }


def _assert_callable(name: str, value: Any) -> None:
    if not callable(value):
        raise TabHealthError(f"Contract violation: '{name}' must be callable")


def assert_ai_component_contract(
    *,
    ai_available: bool,
    model_loader_cls: Any,
    chat_logic_cls: Any,
) -> None:
    """Validate that core AI components are importable and callable."""
    if not ai_available:
        raise TabHealthError("AI components are not available")
    _assert_callable("ModelLoader", model_loader_cls)
    _assert_callable("AgentChatbotLogic", chat_logic_cls)


def assert_feedback_logger_contract(
    *,
    feedback_enabled: bool,
    feedback_logger: Any,
) -> bool:
    """Validate feedback logger API shape when feature is enabled."""
    if not feedback_enabled:
        return False
    if feedback_logger is None:
        raise TabHealthError("feedback_logger is enabled but instance is None")

    required = (
        "log_feedback",
        "get_statistics",
        "get_advanced_analytics",
        "get_optimization_insights",
    )
    for attr in required:
        _assert_callable(f"feedback_logger.{attr}", getattr(feedback_logger, attr, None))
    return True


def assert_quality_dashboard_contract(
    *,
    quality_enabled: bool,
    quality_renderer: Any,
) -> bool:
    """Validate quality dashboard renderer contract when enabled."""
    if not quality_enabled:
        return False
    _assert_callable("render_quality_dashboard", quality_renderer)
    return True


def collect_tab_health_snapshot(
    *,
    ai_available: bool,
    model_loader_cls: Any,
    chat_logic_cls: Any,
    feedback_enabled: bool,
    feedback_logger: Any,
    quality_enabled: bool,
    quality_renderer: Any,
) -> TabHealthSnapshot:
    """Collect strict runtime health information for tab dependencies."""
    assert_ai_component_contract(
        ai_available=ai_available,
        model_loader_cls=model_loader_cls,
        chat_logic_cls=chat_logic_cls,
    )
    feedback_valid = assert_feedback_logger_contract(
        feedback_enabled=feedback_enabled,
        feedback_logger=feedback_logger,
    )
    quality_valid = assert_quality_dashboard_contract(
        quality_enabled=quality_enabled,
        quality_renderer=quality_renderer,
    )
    return TabHealthSnapshot(
        ai_components_available=ai_available,
        feedback_logger_enabled=feedback_enabled,
        feedback_logger_valid=feedback_valid,
        quality_dashboard_enabled=quality_enabled,
        quality_dashboard_valid=quality_valid,
    )
