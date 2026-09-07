"""
Domain models for the SOTA treatment plan layer.

All models are dataclasses with explicit JSON (de)serialisation helpers.
They map 1:1 onto the SQLite tables created by ``CarePlanRepository``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional


# ───────────────────────────── Enums ─────────────────────────────────────


class PlanStatus(str, Enum):
    DRAFT = "draft"
    ACTIVE = "active"
    REVISED = "revised"
    CLOSED = "closed"


class GoalStatus(str, Enum):
    PROPOSED = "proposed"     # extracted but not yet validated by user/system
    ACTIVE = "active"
    ACHIEVED = "achieved"
    DROPPED = "dropped"        # explicitly dropped (no longer relevant)
    SUPERSEDED = "superseded"  # rolled into another goal via matcher


class StageOfChange(str, Enum):
    """Transtheoretical Model (Prochaska & DiClemente)."""
    PRECONTEMPLATION = "precontemplation"
    CONTEMPLATION = "contemplation"
    PREPARATION = "preparation"
    ACTION = "action"
    MAINTENANCE = "maintenance"
    UNKNOWN = "unknown"


class RiskLevel(str, Enum):
    NONE = "none"
    LOW = "low"
    ELEVATED = "elevated"
    ACUTE = "acute"


# ───────────────────────────── Dataclasses ──────────────────────────────


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class PlanGoal:
    """A single goal within a treatment plan.

    Goals form a hierarchy via ``parent_goal_id`` — top-level Oberziele have
    ``parent_goal_id is None``; sub-goals reference their parent.
    """
    id: Optional[int]
    plan_id: int
    parent_goal_id: Optional[int]
    title: str
    rationale: str
    target_metric: str          # human-readable description of what is measured
    target_value: Optional[float]  # 0..1 normalised target (e.g. 1.0 = full achievement)
    status: GoalStatus
    priority: int               # 1 (highest) .. 5
    confidence: float           # extraction confidence at creation time, 0..1
    embedding: Optional[bytes] = None  # serialised float32[d] for matching
    created_at: str = field(default_factory=_utcnow_iso)
    closed_at: Optional[str] = None
    last_progress_score: Optional[float] = None  # cached from goal_updates, 0..1

    def to_prompt_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "status": self.status.value,
            "priority": self.priority,
            "metric": self.target_metric,
            "progress": self.last_progress_score,
            "parent_goal_id": self.parent_goal_id,
        }

    def to_context_dict(self) -> Dict[str, Any]:
        """Canonical context projection used across context builders/providers.

        Keep this as single source of truth to prevent schema drift between
        treatment domain models and consumer-side dict mappings.
        """
        progress = self.last_progress_score
        return {
            "goal": self.title,
            "id": self.id,
            "status": self.status.value,
            "priority": self.priority,
            "progress": progress,
            "has_progress": bool((progress or 0.0) > 0.0),
            "metric": self.target_metric,
            "parent_goal_id": self.parent_goal_id,
            "created": self.created_at,
            "closed": self.closed_at,
            "progress_triples": [],
        }


@dataclass
class GoalUpdate:
    """One observation of progress on a specific goal in a specific session."""
    id: Optional[int]
    goal_id: int
    session_id: str
    progress_score: float       # 0..1 — how close the user is to ``target_value``
    confidence: float           # 0..1 — how confident the assessment is
    evidence: List[str]         # short verbatim quotes / observations
    turn_idx: Optional[int] = None
    delta: Optional[float] = None  # progress_score - previous_score
    created_at: str = field(default_factory=_utcnow_iso)


@dataclass
class CarePlan:
    """Top-level container; one *active* plan per user at any time."""
    id: Optional[int]
    user_id: str
    status: PlanStatus
    version: int
    formulation_id: Optional[int]
    notes: str = ""
    created_at: str = field(default_factory=_utcnow_iso)
    updated_at: str = field(default_factory=_utcnow_iso)


@dataclass
class CaseFormulation:
    """5-P case formulation (Macneil et al., 2012).

    The legacy 4-P table in ``wellbeing_db.py`` is unused and superseded
    by this version, which adds the ``presenting`` problem axis.
    """
    id: Optional[int]
    user_id: str
    presenting: List[str]       # current problems / Anliegen
    predisposing: List[str]     # vulnerabilities (history, biology, attachment)
    precipitating: List[str]    # triggering events
    perpetuating: List[str]     # maintaining factors (avoidance, beliefs)
    protective: List[str]       # strengths, resources
    confidence: float
    version: int
    created_at: str = field(default_factory=_utcnow_iso)


@dataclass
class SessionFocus:
    """Plan for the next/current session — explicit carry-forward."""
    id: Optional[int]
    session_id: str
    plan_id: int
    primary_goal_id: Optional[int]
    secondary_goal_ids: List[int]
    planned_steps: List[str]
    carry_forward_from_session_id: Optional[str]
    carry_forward_notes: str = ""
    focus_mode: str = "suggested"
    created_at: str = field(default_factory=_utcnow_iso)


@dataclass
class RiskAssessment:
    """Per-turn structured risk assessment (replaces keyword detection)."""
    id: Optional[int]
    session_id: str
    turn_idx: int
    level: RiskLevel
    confidence: float
    drivers: List[str]            # reason fragments from classifier (no PII echoed back)
    protective_factors: List[str]
    action_taken: str             # 'none' | 'safety_protocol' | 'reviewer_required' | ...
    raw_classifier_output: str = ""
    created_at: str = field(default_factory=_utcnow_iso)


@dataclass
class MBCObservation:
    """One observation from a conversational MBC instrument item."""
    id: Optional[int]
    user_id: str
    session_id: str
    instrument: str               # 'WHO5-like' | 'MoodCheck' | 'CalmCheck' | ...
    item_key: str                 # canonical item identifier within the instrument
    response_text: str            # the user's free-text response
    derived_score: Optional[float]  # 0..1 normalised score derived by LLM
    confidence: float
    created_at: str = field(default_factory=_utcnow_iso)


@dataclass
class StageAssessment:
    """Stage-of-Change snapshot for a user at a point in time."""
    id: Optional[int]
    user_id: str
    session_id: Optional[str]
    stage: StageOfChange
    confidence: float
    rationale: str
    created_at: str = field(default_factory=_utcnow_iso)


# ───────────────────────────── Serialisation helpers ─────────────────────


def list_to_json(value: Optional[List[Any]]) -> str:
    return json.dumps(value or [], ensure_ascii=False)


def json_to_list(value: Optional[str]) -> List[Any]:
    if not value:
        return []
    try:
        loaded = json.loads(value)
    except (TypeError, ValueError):
        return []
    return loaded if isinstance(loaded, list) else []
