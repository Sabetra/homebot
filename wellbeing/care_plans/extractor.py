"""
Goal extraction & progress engine.

Asks the LLM to:

1. Propose new candidate goals from recent interactions (extraction).
2. Score current progress on each *active* goal (progress engine).

Both steps emit structured JSON; we never look at the message text with
keywords or regex.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from .llm_json import call_llm_json, clamp01, safe_str_list
from .models import GoalStatus, GoalUpdate, PlanGoal

logger = logging.getLogger(__name__)


_EXTRACT_PROMPT = """Du extrahierst therapeutische Ziele aus der Sitzung.

INTERAKTIONEN (jüngste zuerst, gekürzt):
{interactions}

EXISTIERENDE AKTIVE ZIELE (zur Vermeidung von Doppelungen):
{existing_goals}

Schlage maximal 3 NEUE oder VERFEINERTE Ziele vor. Jedes Ziel muss:
- konkret und beobachtbar sein,
- eine messbare Komponente haben (target_metric),
- vom Nutzer (nicht vom Therapeuten) ableitbar sein.

Antworte ausschließlich mit JSON:
{{
  "goals": [
    {{
      "title": "<kurz, max 90 Zeichen>",
      "rationale": "<warum dieses Ziel>",
      "target_metric": "<wie wird Fortschritt erkannt?>",
      "priority": 1 | 2 | 3 | 4 | 5,
      "confidence": 0.0
    }}
  ]
}}

Wenn keine neuen Ziele angemessen sind, gib "goals": [] zurück.

JSON:"""


_PROGRESS_PROMPT = """Du bewertest den AKTUELLEN Fortschritt auf jedem Ziel.

AKTIVE ZIELE:
{goals}

LETZTE INTERAKTIONEN:
{interactions}

Antworte ausschließlich mit JSON:
{{
  "updates": [
    {{
      "goal_id": <int>,
      "progress_score": 0.0,    // 0 = kein Fortschritt, 1 = Ziel vollständig erreicht
      "confidence": 0.0,
      "evidence": ["<kurze Beobachtung 1>", "..."]
    }}
  ]
}}

Bewerte nur Ziele, zu denen es im Gespräch klare Anhaltspunkte gibt.
Lass andere Ziele aus.

JSON:"""


def _format_interactions(items: List[Dict[str, Any]], limit: int = 12) -> str:
    if not items:
        return "—"
    lines: List[str] = []
    for it in items[-limit:]:
        role = it.get("role", "?")
        msg = (it.get("user_message") or it.get("assistant_message")
               or it.get("content") or "").strip()
        if msg:
            lines.append(f"[{role}] {msg[:300]}")
    return "\n".join(lines) if lines else "—"


def _format_existing(goals: List[PlanGoal]) -> str:
    if not goals:
        return "—"
    return "\n".join(f"  • id={g.id}: \"{g.title}\"" for g in goals[:8])


def _format_goals_for_progress(goals: List[PlanGoal]) -> str:
    if not goals:
        return "—"
    lines: List[str] = []
    for g in goals[:8]:
        last = (f"{g.last_progress_score:.2f}"
                if isinstance(g.last_progress_score, (int, float)) else "—")
        lines.append(f"  id={g.id} | last={last} | metric={g.target_metric or '—'} | \"{g.title}\"")
    return "\n".join(lines)


class GoalExtractor:
    """Proposes new candidate goals from session interactions."""

    def __init__(self, llm_function: Optional[Callable[..., str]]) -> None:
        self.llm_function = llm_function

    def extract(
        self,
        *,
        interactions: List[Dict[str, Any]],
        existing_goals: List[PlanGoal],
    ) -> List[Dict[str, Any]]:
        if len(interactions) < 3:
            return []

        prompt = _EXTRACT_PROMPT.format(
            interactions=_format_interactions(interactions),
            existing_goals=_format_existing(existing_goals),
        )
        parsed = call_llm_json(self.llm_function, prompt, debug_label="goal_extractor")
        if parsed is None:
            return []

        out: List[Dict[str, Any]] = []
        for raw in (parsed.get("goals") or [])[:3]:
            if not isinstance(raw, dict):
                continue
            title = str(raw.get("title", "")).strip()
            if len(title) < 8:
                continue
            try:
                priority = int(raw.get("priority", 3))
            except (TypeError, ValueError):
                priority = 3
            priority = max(1, min(5, priority))
            out.append({
                "title": title[:120],
                "rationale": str(raw.get("rationale", ""))[:500],
                "target_metric": str(raw.get("target_metric", ""))[:240],
                "priority": priority,
                "confidence": clamp01(raw.get("confidence"), default=0.5),
            })
        return out


class ProgressEngine:
    """Generates per-goal progress observations from recent interactions."""

    def __init__(self, llm_function: Optional[Callable[..., str]]) -> None:
        self.llm_function = llm_function

    def assess(
        self,
        *,
        session_id: str,
        turn_idx: Optional[int],
        active_goals: List[PlanGoal],
        interactions: List[Dict[str, Any]],
    ) -> List[GoalUpdate]:
        if not active_goals:
            return []

        prompt = _PROGRESS_PROMPT.format(
            goals=_format_goals_for_progress(active_goals),
            interactions=_format_interactions(interactions),
        )
        parsed = call_llm_json(self.llm_function, prompt, debug_label="progress_engine")
        if parsed is None:
            return []

        active_by_id = {g.id: g for g in active_goals if g.id is not None}
        out: List[GoalUpdate] = []
        for raw in (parsed.get("updates") or [])[:10]:
            if not isinstance(raw, dict):
                continue
            try:
                gid = int(raw.get("goal_id"))
            except (TypeError, ValueError):
                continue
            goal = active_by_id.get(gid)
            if goal is None:
                continue
            score = clamp01(raw.get("progress_score"), default=0.0)
            previous = goal.last_progress_score
            delta = (score - previous) if isinstance(previous, (int, float)) else None
            out.append(GoalUpdate(
                id=None, goal_id=gid, session_id=session_id,
                turn_idx=turn_idx,
                progress_score=score,
                confidence=clamp01(raw.get("confidence"), default=0.5),
                evidence=safe_str_list(raw.get("evidence"), max_items=3, max_len=240),
                delta=delta,
                created_at=datetime.now(timezone.utc).isoformat(),
            ))
        return out
