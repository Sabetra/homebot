"""
Care Goals Provider for user context building.

Reads from the SOTA treatment domain (`wellbeing.care_plans`)
which is the single source of truth for care goals across sessions.
"""

from typing import Optional, List, Dict, Any
import logging
from user_context_builder.base import BaseContextProvider
from user_context_builder.models import CareGoalsData, UserContextRequest

logger = logging.getLogger(__name__)


class CareGoalsProvider(BaseContextProvider):
    """Provider for fetching care goals and progress."""

    def __init__(
        self,
        max_goals: int = 10,
        priority: int = 40,
    ):
        super().__init__(name="care_goals", priority=priority)
        self.max_goals = max_goals

    def provide(
        self,
        request: UserContextRequest,
        session_manager: Any
    ) -> Optional[CareGoalsData]:
        tm = self._resolve_treatment_manager(session_manager)
        if tm is None:
            logger.debug("CarePlanManager not available — returning empty goals")
            return CareGoalsData()

        try:
            goals = self._fetch_goals(tm, request.user_id)
        except Exception as exc:
            logger.error(
                f"Error fetching care goals: {exc}", exc_info=True,
            )
            return CareGoalsData()

        if not goals:
            return CareGoalsData()

        with_progress = sum(
            1 for g in goals
            if g.get("progress") is not None and g.get("progress") > 0
        )
        return CareGoalsData(
            goals=goals,
            goals_with_progress=with_progress,
        )

    def _resolve_treatment_manager(self, session_manager: Any) -> Optional[Any]:
        tm = getattr(session_manager, 'treatment_manager', None)
        if tm is not None:
            return tm
        inner = getattr(session_manager, 'manager', None)
        if inner is not None:
            return getattr(inner, 'treatment_manager', None)
        return None

    def _fetch_goals(
        self, tm: Any, user_id: str,
    ) -> List[Dict[str, Any]]:
        from wellbeing.care_plans.models import GoalStatus

        plan = tm.repo.get_active_plan(user_id)
        if plan is None or not plan.id:
            return []

        active = tm.repo.list_goals(
            plan.id,
            statuses=[GoalStatus.ACTIVE, GoalStatus.ACHIEVED],
        )
        if not active:
            return []

        active.sort(
            key=lambda g: (
                g.status == GoalStatus.ACHIEVED,
                g.priority,
                -(g.last_progress_score or 0.0),
            )
        )

        out: List[Dict[str, Any]] = []
        for g in active[: self.max_goals]:
            out.append(g.to_context_dict())
        return out
