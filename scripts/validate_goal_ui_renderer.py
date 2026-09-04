#!/usr/bin/env python3
"""
Validation script for Goal UI Renderer.

Direct tests without pytest to avoid circular import issues.
"""

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from wellbeing_session.ui.goal_renderer import GoalUIRenderer, GoalUIState


def test_achieved_status():
    """Achieved goals must show ✅"""
    result = GoalUIRenderer.emoji("achieved")
    assert result == "✅", f"Expected ✅, got {result}"
    print("✅ test_achieved_status: PASS")


def test_dropped_status():
    """Dropped goals must show ❌"""
    result = GoalUIRenderer.emoji("dropped")
    assert result == "❌", f"Expected ❌, got {result}"
    print("✅ test_dropped_status: PASS")


def test_active_progress_gradient():
    """Active goals show progress gradient"""
    tests = [
        (None, "🔄"),      # No progress
        (0.15, "⏳"),       # 0-30%
        (0.50, "📊"),       # 30-70%
        (0.85, "⚡"),       # 70%+
    ]
    
    for progress, expected_emoji in tests:
        result = GoalUIRenderer.emoji("active", progress)
        assert result == expected_emoji, f"Active with {progress}: expected {expected_emoji}, got {result}"
    
    print("✅ test_active_progress_gradient: PASS")


def test_state_resolution():
    """Test state machine resolution"""
    tests = [
        ("achieved", None, GoalUIState.ACHIEVED),
        ("dropped", None, GoalUIState.DROPPED),
        ("superseded", 0.5, GoalUIState.SUPERSEDED),
        ("proposed", None, GoalUIState.PROPOSED),
        ("active", None, GoalUIState.ACTIVE_NOT_STARTED),
        ("active", 0.15, GoalUIState.ACTIVE_IN_PROGRESS),
        ("active", 0.50, GoalUIState.ACTIVE_MAKING_PROGRESS),
        ("active", 0.80, GoalUIState.ACTIVE_NEARLY_ACHIEVED),
    ]
    
    for status, progress, expected_state in tests:
        result = GoalUIRenderer.resolve_ui_state(status, progress)
        assert result == expected_state, f"{status} with {progress}: expected {expected_state}, got {result}"
    
    print("✅ test_state_resolution: PASS")


def test_render_info():
    """Test complete render information"""
    info = GoalUIRenderer.get_render_info("achieved")
    assert info.emoji == "✅"
    assert info.state == GoalUIState.ACHIEVED
    assert info.label == "Erreicht"
    
    info2 = GoalUIRenderer.get_render_info("active", progress_score=0.75)
    assert info2.progress_percentage == 75.0
    
    print("✅ test_render_info: PASS")


def test_backwards_compatibility():
    """Test that old get_status_emoji still works"""
    # Import the updated function
    from wellbeing_session.utils.text_utils import get_status_emoji
    
    emoji = get_status_emoji("achieved")
    assert emoji == "✅", f"Legacy function failed: expected ✅, got {emoji}"
    
    emoji = get_status_emoji("active", has_progress=True)
    assert emoji == "📊", f"Legacy function with progress failed"
    
    print("✅ test_backwards_compatibility: PASS")


def main():
    """Run all tests"""
    print("\n🧪 Running Goal UI Renderer Validation Tests\n")
    print("=" * 60)
    
    tests = [
        test_achieved_status,
        test_dropped_status,
        test_active_progress_gradient,
        test_state_resolution,
        test_render_info,
        test_backwards_compatibility,
    ]
    
    passed = 0
    failed = 0
    
    for test in tests:
        try:
            test()
            passed += 1
        except Exception as e:
            print(f"❌ {test.__name__}: FAIL - {e}")
            failed += 1
    
    print("=" * 60)
    print(f"\n📊 Results: {passed} passed, {failed} failed\n")
    
    if failed == 0:
        print("🎉 All tests passed!")
        return 0
    else:
        print("⚠️ Some tests failed!")
        return 1


if __name__ == "__main__":
    sys.exit(main())
