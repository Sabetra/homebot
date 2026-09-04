"""
Stage-of-Change classifier (TTM).

Derives the user's current stage from structural signals — recent goal
progress, mood trajectory, MBC observations, formulation status — by
asking the LLM to classify, NOT by keyword matching.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from .llm_json import call_llm_json, clamp01
from .models import (
    CaseFormulation,
    GoalUpdate,
    PlanGoal,
    StageAssessment,
    StageOfChange,
)

logger = logging.getLogger(__name__)


_STAGE_PROMPT = """Du bist ein klinischer Reviewer und schätzt das Veränderungs-Stadium nach
dem Transtheoretischen Modell (TTM) ein.

AKTIVE ZIELE (mit letzten Fortschritts-Scores):
{goals}

LETZTE FORTSCHRITTS-BEOBACHTUNGEN (chronologisch):
{trajectory}

STIMMUNGS-VERLAUF: {mood_summary}
FALLKONZEPTION: {formulation}

Antworte ausschließlich mit JSON:
{{
  "stage": "precontemplation" | "contemplation" | "preparation" | "action" | "maintenance",
  "confidence": 0.0,
  "rationale": "<1 Satz>"
}}

Definitionen:
- precontemplation: kein Veränderungs-Bewusstsein, keine Absicht.
- contemplation: erkennt Problem, ambivalent, keine Handlung in nächsten 30 Tagen.
- preparation: konkrete Schritte geplant, bald (innerhalb 30 Tagen).
- action: aktive Veränderung in den letzten 6 Monaten.
- maintenance: Veränderung > 6 Monate stabilisiert.

JSON:"""


def _format_goals(goals: List[PlanGoal]) -> str:
    if not goals:
        return "—"
    lines: List[str] = []
    for g in goals[:6]:
        progress = g.last_progress_score
        progress_s = f"{progress:.2f}" if isinstance(progress, (int, float)) else "—"
        lines.append(f"  • {g.title} [Status={g.status.value}, Progress={progress_s}, P{g.priority}]")
    return "\n".join(lines)


def _format_trajectory(updates: List[GoalUpdate]) -> str:
    if not updates:
        return "—"
    lines: List[str] = []
    for u in updates[:8]:
        delta = f"{u.delta:+.2f}" if isinstance(u.delta, (int, float)) else "—"
        lines.append(f"  • {u.created_at}: progress={u.progress_score:.2f}, Δ={delta}")
    return "\n".join(lines)


def _format_formulation(f: Optional[CaseFormulation]) -> str:
    if not f:
        return "—"
    return (
        f"presenting={f.presenting[:3]}, perpetuating={f.perpetuating[:3]}, "
        f"protective={f.protective[:3]} (conf={f.confidence:.2f})"
    )


class StageClassifier:
    """Classifies the user's TTM stage from structural data."""

    def __init__(self, llm_function: Optional[Callable[..., str]]) -> None:
        self.llm_function = llm_function

    def classify(
        self,
        *,
        user_id: str,
        session_id: Optional[str],
        active_goals: List[PlanGoal],
        recent_updates: List[GoalUpdate],
        formulation: Optional[CaseFormulation] = None,
        mood_summary: str = "—",
    ) -> Optional[StageAssessment]:
        # Need *some* signal; otherwise we return UNKNOWN as a real, honest answer.
        if not active_goals and not recent_updates:
            return StageAssessment(
                id=None, user_id=user_id, session_id=session_id,
                stage=StageOfChange.UNKNOWN, confidence=0.0,
                rationale="no plan signals yet",
                created_at=datetime.now(timezone.utc).isoformat(),
            )

        prompt = _STAGE_PROMPT.format(
            goals=_format_goals(active_goals),
            trajectory=_format_trajectory(recent_updates),
            mood_summary=mood_summary or "—",
            formulation=_format_formulation(formulation),
        )
        parsed = call_llm_json(self.llm_function, prompt, debug_label="stage_classifier")
        if parsed is None:
            return None

        raw = str(parsed.get("stage", "unknown")).strip().lower()
        try:
            stage = StageOfChange(raw)
        except ValueError:
            stage = StageOfChange.UNKNOWN

        return StageAssessment(
            id=None, user_id=user_id, session_id=session_id,
            stage=stage,
            confidence=clamp01(parsed.get("confidence"), default=0.5),
            rationale=str(parsed.get("rationale", ""))[:500],
            created_at=datetime.now(timezone.utc).isoformat(),
        )
