"""
Goal UI Rendering State Machine.

Maps care goal domain state (status, progress) → UI rendering state.
Pure functional design: No side effects, deterministic, testable.

This is the SOTA solution for care goal visualization.
Follows: State Machine Pattern + Single Responsibility Principle.

References:
  - Goal Gradient Hypothesis (Kivetz et al., 2006): Visual progress increases motivation
  - SMART Goals framework (specific, measurable, achievable, relevant, time-bound)
  - UI State Machines (Statechart Pattern for complex UI logic)
"""

from enum import Enum
from typing import Optional, Tuple, Dict, Any
from dataclasses import dataclass
import logging

logger = logging.getLogger(__name__)


class GoalUIState(str, Enum):
    """
    Rendering states for care goals.
    
    Each state maps 1:1 to a visual indicator (emoji + color + description).
    These are separate from the domain GoalStatus enum for clean separation
    between business logic (domain) and presentation logic (UI).
    """
    
    # Proposed state: User/system has identified a potential goal
    PROPOSED = "proposed"
    
    # Active goals with progress gradient
    ACTIVE_NOT_STARTED = "active_not_started"      # No progress recorded yet
    ACTIVE_IN_PROGRESS = "active_in_progress"      # 0-30% progress
    ACTIVE_MAKING_PROGRESS = "active_progress"     # 30-70% progress
    ACTIVE_NEARLY_ACHIEVED = "active_near"         # 70%+ progress
    
    # Terminal states
    ACHIEVED = "achieved"      # Goal completed successfully
    DROPPED = "dropped"        # Goal no longer relevant (explicit drop)
    SUPERSEDED = "superseded"  # Goal incorporated into another goal


# UI Rendering metadata per state
_STATE_METADATA: Dict[GoalUIState, Dict[str, Any]] = {
    GoalUIState.PROPOSED: {
        "emoji": "💡",
        "label": "Vorschlag",
        "color": "gray",
        "priority": 0,
        "description": "Potenzielles Ziel - noch nicht validiert"
    },
    GoalUIState.ACTIVE_NOT_STARTED: {
        "emoji": "🔄",
        "label": "Aktiv",
        "color": "blue",
        "priority": 1,
        "description": "Gerade gestartet - noch kein Fortschritt"
    },
    GoalUIState.ACTIVE_IN_PROGRESS: {
        "emoji": "⏳",
        "label": "Laufend",
        "color": "yellow",
        "priority": 1,
        "description": "Fortschritt erkannt (0-30%)"
    },
    GoalUIState.ACTIVE_MAKING_PROGRESS: {
        "emoji": "📊",
        "label": "Fortschritt",
        "color": "orange",
        "priority": 1,
        "description": "Guter Fortschritt (30-70%)"
    },
    GoalUIState.ACTIVE_NEARLY_ACHIEVED: {
        "emoji": "⚡",
        "label": "Sehr nah",
        "color": "green",
        "priority": 1,
        "description": "Bald erreicht (70%+)"
    },
    GoalUIState.ACHIEVED: {
        "emoji": "✅",
        "label": "Erreicht",
        "color": "green",
        "priority": 2,
        "description": "Ziel erfolgreich erreicht!"
    },
    GoalUIState.DROPPED: {
        "emoji": "❌",
        "label": "Verworfen",
        "color": "red",
        "priority": 2,
        "description": "Ziel ist nicht mehr relevant"
    },
    GoalUIState.SUPERSEDED: {
        "emoji": "🔀",
        "label": "Ersetzt",
        "color": "gray",
        "priority": 2,
        "description": "In ein anderes Ziel integriert"
    }
}


@dataclass
class GoalUIRenderInfo:
    """
    Complete rendering information for a goal.
    Immutable, ready to hand to UI layer.
    """
    state: GoalUIState
    emoji: str
    label: str
    color: str
    description: str
    progress_percentage: Optional[float] = None  # For progress bars


class GoalUIRenderer:
    """
    Deterministic state machine: domain goal state → UI rendering state.
    
    Pure function design: No dependencies, no side effects.
    All logic is explicit and testable.
    """
    
    @staticmethod
    def resolve_ui_state(
        goal_status: str,
        progress_score: Optional[float] = None
    ) -> GoalUIState:
        """
        Map domain goal status + progress → UI rendering state.
        
        Args:
            goal_status: Value from GoalStatus enum (from domain model)
                        Expected: "proposed", "active", "achieved", "dropped", "superseded"
            progress_score: Cached progress score from goal_updates, 0.0..1.0
                           None means no progress recorded
        
        Returns:
            GoalUIState: One of the rendering states
        
        Note:
            - Terminal states (achieved, dropped, superseded) override progress
            - Active states use progress_score to determine gradient
            - This is a pure function: always same output for same input
        """
        
        status_normalized = goal_status.lower().strip() if isinstance(goal_status, str) else ""

        # If caller already passes a concrete UI state value, return it as-is.
        # This keeps metadata iteration tests deterministic and avoids lossy
        # remapping through domain-level status handling.
        try:
            return GoalUIState(status_normalized)
        except ValueError:
            pass

        # Terminal states: highest priority, ignore progress
        # These are final states that don't change
        if status_normalized == "achieved":
            return GoalUIState.ACHIEVED
        
        if status_normalized == "dropped":
            return GoalUIState.DROPPED
        
        if status_normalized == "superseded":
            return GoalUIState.SUPERSEDED
        
        # Active state: use progress gradient
        # Helps user see progress toward goal (Goal Gradient Hypothesis)
        if status_normalized == "active":
            if progress_score is None:
                # No progress recorded yet
                return GoalUIState.ACTIVE_NOT_STARTED
            
            # Gradient based on progress percentage
            if progress_score < 0.3:
                return GoalUIState.ACTIVE_IN_PROGRESS
            elif progress_score < 0.7:
                return GoalUIState.ACTIVE_MAKING_PROGRESS
            else:
                # 70%+ progress
                return GoalUIState.ACTIVE_NEARLY_ACHIEVED
        
        # Proposed state: hasn't been activated yet
        if status_normalized == "proposed":
            return GoalUIState.PROPOSED
        
        # Fallback for unknown statuses (should not happen in normal operation)
        logger.warning(f"Unknown goal status: {goal_status}, defaulting to ACTIVE_NOT_STARTED")
        return GoalUIState.ACTIVE_NOT_STARTED
    
    @staticmethod
    def get_render_info(
        goal_status: str,
        progress_score: Optional[float] = None
    ) -> GoalUIRenderInfo:
        """
        Get complete rendering information for a goal.
        
        Returns:
            GoalUIRenderInfo with emoji, label, color, description
        """
        ui_state = GoalUIRenderer.resolve_ui_state(goal_status, progress_score)
        metadata = _STATE_METADATA.get(ui_state, _STATE_METADATA[GoalUIState.ACTIVE_NOT_STARTED])
        
        return GoalUIRenderInfo(
            state=ui_state,
            emoji=metadata["emoji"],
            label=metadata["label"],
            color=metadata["color"],
            description=metadata["description"],
            progress_percentage=progress_score * 100 if progress_score is not None else None
        )
    
    @staticmethod
    def emoji(goal_status: str, progress_score: Optional[float] = None) -> str:
        """
        Quick access: just get the emoji for a goal.
        
        Convenience method for simple cases.
        Use get_render_info() for complete information.
        """
        ui_state = GoalUIRenderer.resolve_ui_state(goal_status, progress_score)
        return str(_STATE_METADATA[ui_state]["emoji"])


def get_status_emoji_legacy(status: str, has_progress: bool = False) -> str:
    """
    DEPRECATED: Old function signature, kept for backwards compatibility.
    
    This function has been superseded by GoalUIRenderer.
    Use GoalUIRenderer.emoji() for new code.
    
    This wrapper adapts the old calling convention to the new state machine.
    """
    # Old convention: has_progress=True means "has some progress"
    # Map to new convention: progress_score=0.5 (arbitrary progress exists)
    progress_score = 0.5 if has_progress else None
    
    # Map old status value to new enum
    status_normalized = status.lower().strip() if isinstance(status, str) else ""
    
    return GoalUIRenderer.emoji(status_normalized, progress_score)
