"""
SOTA Treatment Plan Domain
==========================

Canonical, persistent care infrastructure for cross-session support:

- Treatment Plan with hierarchical goals (Oberziel → Teilziel)
- Case Formulation (5P model: Predisposing / Precipitating / Perpetuating / Protective / Presenting)
- Numerical Progress Trajectories per goal
- Stage-of-Change classification (TTM)
- LLM-based Risk Assessment (no keywords)
- Measurement-Based Care via conversational instruments
- Session-Focus carry-forward
- Self-supervision Reviewer pass

This is the SINGLE SOURCE OF TRUTH for care state across sessions.
The legacy `;`-separated `wellbeing_sessions.care_goals` column
is kept for read-only backwards-compatibility but no longer the source of truth.
"""

from .models import (
    PlanStatus,
    GoalStatus,
    StageOfChange,
    RiskLevel,
    PlanGoal,
    GoalUpdate,
    CarePlan,
    CaseFormulation,
    SessionFocus,
    RiskAssessment,
    MBCObservation,
    StageAssessment,
)
from .repository import CarePlanRepository
from .manager import CarePlanManager

__all__ = [
    "PlanStatus",
    "GoalStatus",
    "StageOfChange",
    "RiskLevel",
    "PlanGoal",
    "GoalUpdate",
    "CarePlan",
    "CaseFormulation",
    "SessionFocus",
    "RiskAssessment",
    "MBCObservation",
    "StageAssessment",
    "CarePlanRepository",
    "CarePlanManager",
]
