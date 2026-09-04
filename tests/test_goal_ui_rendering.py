"""
Unit tests for GoalUIRenderer state machine.

Tests the deterministic mapping from domain goal state (status, progress) 
to UI rendering state (emoji, label, color).

This is the core logic for displaying therapeutic goals correctly.
"""

import sys
import pytest
from pathlib import Path

# Direct import to avoid circular dependency with streamlit in __init__.py
import importlib.util
spec = importlib.util.spec_from_file_location(
    "goal_renderer", 
    str(Path(__file__).resolve().parent.parent / "wellbeing_session" / "ui" / "goal_renderer.py")
)
goal_renderer_module = importlib.util.module_from_spec(spec)
sys.modules['goal_renderer'] = goal_renderer_module
spec.loader.exec_module(goal_renderer_module)

GoalUIRenderer = goal_renderer_module.GoalUIRenderer
GoalUIState = goal_renderer_module.GoalUIState
_STATE_METADATA = goal_renderer_module._STATE_METADATA


class TestGoalUIRendererStateResolution:
    """Test the core state machine logic."""

    # Terminal states - should override any progress value
    
    def test_achieved_status_returns_achieved_ui_state(self):
        """Achieved goals should always show ✅ regardless of progress"""
        assert GoalUIRenderer.resolve_ui_state("achieved", progress_score=None) == GoalUIState.ACHIEVED
        assert GoalUIRenderer.resolve_ui_state("achieved", progress_score=0.0) == GoalUIState.ACHIEVED
        assert GoalUIRenderer.resolve_ui_state("achieved", progress_score=0.5) == GoalUIState.ACHIEVED
        assert GoalUIRenderer.resolve_ui_state("achieved", progress_score=1.0) == GoalUIState.ACHIEVED
    
    def test_dropped_status_returns_dropped_ui_state(self):
        """Dropped goals should always show ❌"""
        assert GoalUIRenderer.resolve_ui_state("dropped", progress_score=None) == GoalUIState.DROPPED
        assert GoalUIRenderer.resolve_ui_state("dropped", progress_score=0.5) == GoalUIState.DROPPED
    
    def test_superseded_status_returns_superseded_ui_state(self):
        """Superseded goals should always show 🔀"""
        assert GoalUIRenderer.resolve_ui_state("superseded", progress_score=None) == GoalUIState.SUPERSEDED
        assert GoalUIRenderer.resolve_ui_state("superseded", progress_score=0.8) == GoalUIState.SUPERSEDED
    
    # Proposed state
    
    def test_proposed_status_returns_proposed_ui_state(self):
        """Proposed goals should show 💡"""
        assert GoalUIRenderer.resolve_ui_state("proposed", progress_score=None) == GoalUIState.PROPOSED
    
    # Active state with progress gradient
    
    def test_active_no_progress_returns_not_started(self):
        """Active goal with no progress should show 🔄"""
        assert GoalUIRenderer.resolve_ui_state("active", progress_score=None) == GoalUIState.ACTIVE_NOT_STARTED
    
    def test_active_minimal_progress_returns_in_progress(self):
        """Active goal with 0-30% progress should show ⏳"""
        assert GoalUIRenderer.resolve_ui_state("active", progress_score=0.0) == GoalUIState.ACTIVE_IN_PROGRESS
        assert GoalUIRenderer.resolve_ui_state("active", progress_score=0.15) == GoalUIState.ACTIVE_IN_PROGRESS
        assert GoalUIRenderer.resolve_ui_state("active", progress_score=0.29) == GoalUIState.ACTIVE_IN_PROGRESS
    
    def test_active_moderate_progress_returns_making_progress(self):
        """Active goal with 30-70% progress should show 📊"""
        assert GoalUIRenderer.resolve_ui_state("active", progress_score=0.30) == GoalUIState.ACTIVE_MAKING_PROGRESS
        assert GoalUIRenderer.resolve_ui_state("active", progress_score=0.50) == GoalUIState.ACTIVE_MAKING_PROGRESS
        assert GoalUIRenderer.resolve_ui_state("active", progress_score=0.69) == GoalUIState.ACTIVE_MAKING_PROGRESS
    
    def test_active_high_progress_returns_nearly_achieved(self):
        """Active goal with 70%+ progress should show ⚡"""
        assert GoalUIRenderer.resolve_ui_state("active", progress_score=0.70) == GoalUIState.ACTIVE_NEARLY_ACHIEVED
        assert GoalUIRenderer.resolve_ui_state("active", progress_score=0.85) == GoalUIState.ACTIVE_NEARLY_ACHIEVED
        assert GoalUIRenderer.resolve_ui_state("active", progress_score=1.0) == GoalUIState.ACTIVE_NEARLY_ACHIEVED
    
    # Unknown statuses
    
    def test_unknown_status_defaults_to_active_not_started(self):
        """Unknown status should default to ACTIVE_NOT_STARTED"""
        assert GoalUIRenderer.resolve_ui_state("unknown_status") == GoalUIState.ACTIVE_NOT_STARTED
        assert GoalUIRenderer.resolve_ui_state("") == GoalUIState.ACTIVE_NOT_STARTED
    
    # Boundary conditions
    
    def test_boundary_at_30_percent(self):
        """30% is boundary between IN_PROGRESS and MAKING_PROGRESS"""
        assert GoalUIRenderer.resolve_ui_state("active", progress_score=0.299) == GoalUIState.ACTIVE_IN_PROGRESS
        assert GoalUIRenderer.resolve_ui_state("active", progress_score=0.30) == GoalUIState.ACTIVE_MAKING_PROGRESS
    
    def test_boundary_at_70_percent(self):
        """70% is boundary between MAKING_PROGRESS and NEARLY_ACHIEVED"""
        assert GoalUIRenderer.resolve_ui_state("active", progress_score=0.699) == GoalUIState.ACTIVE_MAKING_PROGRESS
        assert GoalUIRenderer.resolve_ui_state("active", progress_score=0.70) == GoalUIState.ACTIVE_NEARLY_ACHIEVED


class TestGoalUIRendererEmojis:
    """Test emoji generation."""
    
    def test_emoji_matches_state_metadata(self):
        """Each state should have a consistent emoji"""
        for state in GoalUIState:
            metadata = _STATE_METADATA[state]
            emoji = GoalUIRenderer.emoji(state.value, progress_score=None)
            assert emoji == metadata["emoji"], f"State {state} emoji mismatch"
    
    def test_achieved_shows_checkmark(self):
        """Achieved goals must show ✅"""
        assert GoalUIRenderer.emoji("achieved") == "✅"
    
    def test_dropped_shows_x(self):
        """Dropped goals must show ❌"""
        assert GoalUIRenderer.emoji("dropped") == "❌"
    
    def test_superseded_shows_arrow(self):
        """Superseded goals must show 🔀"""
        assert GoalUIRenderer.emoji("superseded") == "🔀"
    
    def test_proposed_shows_lightbulb(self):
        """Proposed goals must show 💡"""
        assert GoalUIRenderer.emoji("proposed") == "💡"
    
    def test_active_progress_gradient_emojis(self):
        """Active goals show progression of emojis based on progress"""
        assert GoalUIRenderer.emoji("active", progress_score=None) == "🔄"
        assert GoalUIRenderer.emoji("active", progress_score=0.15) == "⏳"
        assert GoalUIRenderer.emoji("active", progress_score=0.50) == "📊"
        assert GoalUIRenderer.emoji("active", progress_score=0.80) == "⚡"


class TestGoalUIRendererRenderInfo:
    """Test complete render information."""
    
    def test_render_info_has_all_required_fields(self):
        """RenderInfo should include emoji, label, color, description"""
        info = GoalUIRenderer.get_render_info("active", progress_score=0.5)
        assert info.emoji == "📊"
        assert info.label == "Fortschritt"
        assert info.color == "orange"
        assert info.description is not None
        assert info.state == GoalUIState.ACTIVE_MAKING_PROGRESS
    
    def test_render_info_progress_percentage(self):
        """RenderInfo should compute progress percentage"""
        info = GoalUIRenderer.get_render_info("active", progress_score=0.75)
        assert info.progress_percentage == 75.0
    
    def test_render_info_no_progress_percentage_when_none(self):
        """RenderInfo should have None progress when score is None"""
        info = GoalUIRenderer.get_render_info("active", progress_score=None)
        assert info.progress_percentage is None
    
    def test_render_info_achieved(self):
        """RenderInfo for achieved goal"""
        info = GoalUIRenderer.get_render_info("achieved")
        assert info.emoji == "✅"
        assert info.label == "Erreicht"
        assert info.color == "green"


class TestLegacyBackwardsCompatibility:
    """Test that old calling convention still works."""
    
    def test_legacy_get_status_emoji_with_progress(self):
        """Old function with has_progress=True should work"""
        from wellbeing_session.utils.text_utils import get_status_emoji
        
        # Old convention: status='achieved' with any progress should show ✅
        emoji = get_status_emoji('achieved', has_progress=False)
        assert emoji == "✅"
        
        emoji = get_status_emoji('achieved', has_progress=True)
        assert emoji == "✅"
    
    def test_legacy_get_status_emoji_active_states(self):
        """Old function with active status and has_progress=True shows 📊"""
        from wellbeing_session.utils.text_utils import get_status_emoji
        
        # Old convention: has_progress=True means "show progress emoji"
        emoji = get_status_emoji('active', has_progress=True)
        assert emoji == "📊"
    
    def test_legacy_get_status_emoji_active_no_progress(self):
        """Old function with active status and has_progress=False shows 🔄"""
        from wellbeing_session.utils.text_utils import get_status_emoji
        
        emoji = get_status_emoji('active', has_progress=False)
        assert emoji == "🔄"


class TestPureFunctionProperty:
    """Test that the state machine is deterministic (pure function)."""
    
    def test_same_input_always_same_output(self):
        """Multiple calls with same input must return same output"""
        for _ in range(100):
            result1 = GoalUIRenderer.resolve_ui_state("active", 0.5)
            result2 = GoalUIRenderer.resolve_ui_state("active", 0.5)
            assert result1 == result2
    
    def test_no_side_effects(self):
        """Calling the function should not modify any state"""
        initial_metadata = dict(_STATE_METADATA)
        
        for state in GoalUIState:
            GoalUIRenderer.resolve_ui_state(state.value)
            GoalUIRenderer.emoji(state.value)
            GoalUIRenderer.get_render_info(state.value)
        
        assert _STATE_METADATA == initial_metadata


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
