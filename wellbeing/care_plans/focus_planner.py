"""
Session Focus Planner.

Selects the primary goal (and a few secondary ones) for the upcoming /
current session, plans concrete interventions appropriate to the user's
TTM stage, and explicitly carries forward any open items from the
previous session's focus.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from .llm_json import call_llm_json, clamp01, safe_str_list
from .models import (
    GoalStatus,
    PlanGoal,
    SessionFocus,
    StageAssessment,
    StageOfChange,
)

logger = logging.getLogger(__name__)


_PLANNER_PROMPT = """Du planst den therapeutischen Fokus der nächsten/aktuellen Session.

AKTIVE ZIELE (Priorität, letzter Fortschritt):
{goals}

AKTUELLES TTM-STADIUM: {stage}

OFFENE PUNKTE AUS LETZTER SESSION:
{carry_forward}

LETZTE NUTZER-NACHRICHTEN (jüngste zuerst, gekürzt):
{recent_messages}

AUFGABE:
- Wähle EIN Primärziel (id) für diese Session.
- Wähle 0–2 Sekundärziele (ids).
- Schlage 1–3 konkrete, stadiums-passende Interventionen vor
  (Precontemplation: Bewusstsein wecken; Contemplation: Ambivalenz erkunden;
   Preparation: konkreten Plan formulieren; Action: Handlung anleiten;
   Maintenance: Rückfall-Prävention).
- Übernimm relevante offene Punkte als Notiz.

Antworte ausschließlich mit JSON:
{{
  "primary_goal_id": <int oder null>,
  "secondary_goal_ids": [<int>, ...],
  "planned_steps": ["<konkrete Intervention>", "..."],
  "carry_forward_notes": "<knappe Notiz zu offenen Punkten>",
  "confidence": 0.0
}}

JSON:"""


def _format_goals(goals: List[PlanGoal]) -> str:
    if not goals:
        return "—"
    lines: List[str] = []
    for g in goals:
        progress = g.last_progress_score
        progress_s = f"{progress:.2f}" if isinstance(progress, (int, float)) else "—"
        lines.append(
            f"  • id={g.id} | P{g.priority} | progress={progress_s} | "
            f"\"{g.title}\""
        )
    return "\n".join(lines)


def _format_messages(messages: List[Dict[str, Any]], limit: int = 8) -> str:
    if not messages:
        return "—"
    lines: List[str] = []
    for m in messages[-limit:]:
        role = m.get("role", "?")
        content = (m.get("user_message") or m.get("assistant_message")
                   or m.get("content") or "").strip()
        if content:
            lines.append(f"  [{role}] {content[:240]}")
    return "\n".join(lines)


class FocusPlanner:
    """Chooses the active focus for a session."""

    def __init__(self, llm_function: Optional[Callable[..., str]]) -> None:
        self.llm_function = llm_function

    def plan(
        self,
        *,
        plan_id: int,
        session_id: str,
        active_goals: List[PlanGoal],
        stage: Optional[StageAssessment],
        previous_focus: Optional[SessionFocus],
        recent_messages: List[Dict[str, Any]],
    ) -> Optional[SessionFocus]:
        if not active_goals:
            # No goals → no focus is sensible; manager will skip persisting.
            return None

        active_only = [g for g in active_goals if g.status == GoalStatus.ACTIVE]
        if not active_only:
            return None

        carry = previous_focus.carry_forward_notes if previous_focus else ""
        if previous_focus and previous_focus.planned_steps:
            carry = (carry + "\nLetzte Interventionen: "
                     + "; ".join(previous_focus.planned_steps[:3])).strip()

        prompt = _PLANNER_PROMPT.format(
            goals=_format_goals(active_only),
            stage=stage.stage.value if stage else StageOfChange.UNKNOWN.value,
            carry_forward=carry or "—",
            recent_messages=_format_messages(recent_messages),
        )
        parsed = call_llm_json(self.llm_function, prompt, debug_label="focus_planner")
        if parsed is None:
            # Fall back: pick highest-priority active goal, no LLM interventions.
            primary = active_only[0]
            return SessionFocus(
                id=None, session_id=session_id, plan_id=plan_id,
                primary_goal_id=primary.id, secondary_goal_ids=[],
                planned_steps=[],
                carry_forward_from_session_id=(previous_focus.session_id if previous_focus else None),
                carry_forward_notes=carry,
                created_at=datetime.now(timezone.utc).isoformat(),
            )

        active_ids = {g.id for g in active_only}
        primary_id = parsed.get("primary_goal_id")
        if not isinstance(primary_id, int) or primary_id not in active_ids:
            primary_id = active_only[0].id

        secondary_ids_raw = parsed.get("secondary_goal_ids") or []
        secondary_ids = [
            int(x) for x in secondary_ids_raw
            if isinstance(x, int) and x in active_ids and x != primary_id
        ][:2]

        return SessionFocus(
            id=None, session_id=session_id, plan_id=plan_id,
            primary_goal_id=primary_id,
            secondary_goal_ids=secondary_ids,
            planned_steps=safe_str_list(parsed.get("planned_steps"),
                                                max_items=3, max_len=240),
            carry_forward_from_session_id=(previous_focus.session_id if previous_focus else None),
            carry_forward_notes=str(parsed.get("carry_forward_notes", carry))[:1000],
            created_at=datetime.now(timezone.utc).isoformat(),
        )
