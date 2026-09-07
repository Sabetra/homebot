"""
SQLite repository for the SOTA treatment plan domain.

Owns:
- Schema creation (idempotent, additive — never alters existing tables)
- All CRUD against ``care_plans``, ``plan_goals``, ``goal_updates``,
  ``context_formulations_v2``, ``session_focuses``, ``risk_assessments``,
  ``checkin_observations``, ``progress_stages``.

Never silently swallows errors: callers see exceptions, the manager layer
above translates them into structured non-result returns.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import struct
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from .models import (
    CaseFormulation,
    GoalStatus,
    GoalUpdate,
    MBCObservation,
    PlanGoal,
    PlanStatus,
    RiskAssessment,
    RiskLevel,
    SessionFocus,
    StageAssessment,
    StageOfChange,
    CarePlan,
    json_to_list,
    list_to_json,
)

logger = logging.getLogger(__name__)


# ────────────────────────── Embedding (de)serialisation ─────────────────


def serialise_embedding(vec: Optional[np.ndarray]) -> Optional[bytes]:
    """Pack a 1-D float embedding into bytes (little-endian float32)."""
    if vec is None:
        return None
    arr = np.asarray(vec, dtype=np.float32).ravel()
    return struct.pack(f"<{arr.size}f", *arr.tolist())


def deserialise_embedding(blob: Optional[bytes]) -> Optional[np.ndarray]:
    if blob is None or len(blob) == 0:
        return None
    n = len(blob) // 4
    return np.array(struct.unpack(f"<{n}f", blob), dtype=np.float32)


# ────────────────────────── Schema DDL ──────────────────────────────────


_SCHEMA_DDL = """
-- Top-level treatment plan; one active per user.
CREATE TABLE IF NOT EXISTS care_plans (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         TEXT NOT NULL,
    status          TEXT NOT NULL,
    version         INTEGER NOT NULL DEFAULT 1,
    formulation_id  INTEGER,
    notes           TEXT DEFAULT '',
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_care_plans_user_status
    ON care_plans(user_id, status);

-- Hierarchical goals; parent_goal_id NULL ⇒ Oberziel.
CREATE TABLE IF NOT EXISTS plan_goals (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_id         INTEGER NOT NULL,
    parent_goal_id  INTEGER,
    title           TEXT NOT NULL,
    rationale       TEXT DEFAULT '',
    target_metric   TEXT DEFAULT '',
    target_value    REAL,
    status          TEXT NOT NULL,
    priority        INTEGER NOT NULL DEFAULT 3,
    confidence      REAL NOT NULL DEFAULT 0.0,
    embedding       BLOB,
    last_progress_score REAL,
    created_at      TEXT NOT NULL,
    closed_at       TEXT,
    FOREIGN KEY (plan_id) REFERENCES care_plans(id),
    FOREIGN KEY (parent_goal_id) REFERENCES plan_goals(id)
);
CREATE INDEX IF NOT EXISTS idx_plan_goals_plan ON plan_goals(plan_id);
CREATE INDEX IF NOT EXISTS idx_plan_goals_parent ON plan_goals(parent_goal_id);
CREATE INDEX IF NOT EXISTS idx_plan_goals_status ON plan_goals(status);

-- One numerical progress observation per (goal, session, occurrence).
CREATE TABLE IF NOT EXISTS goal_updates (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    goal_id         INTEGER NOT NULL,
    session_id      TEXT NOT NULL,
    turn_idx        INTEGER,
    progress_score  REAL NOT NULL,
    confidence      REAL NOT NULL,
    evidence        TEXT NOT NULL DEFAULT '[]',
    delta           REAL,
    created_at      TEXT NOT NULL,
    FOREIGN KEY (goal_id) REFERENCES plan_goals(id)
);
CREATE INDEX IF NOT EXISTS idx_goal_updates_goal ON goal_updates(goal_id);
CREATE INDEX IF NOT EXISTS idx_goal_updates_session ON goal_updates(session_id);

-- 5-P case formulation, fully versioned.
CREATE TABLE IF NOT EXISTS context_formulations_v2 (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         TEXT NOT NULL,
    presenting      TEXT NOT NULL DEFAULT '[]',
    predisposing    TEXT NOT NULL DEFAULT '[]',
    precipitating   TEXT NOT NULL DEFAULT '[]',
    perpetuating    TEXT NOT NULL DEFAULT '[]',
    protective      TEXT NOT NULL DEFAULT '[]',
    confidence      REAL NOT NULL DEFAULT 0.0,
    version         INTEGER NOT NULL DEFAULT 1,
    created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_context_form_v2_user ON context_formulations_v2(user_id);

-- Session focus / planned interventions / carry-forward.
CREATE TABLE IF NOT EXISTS session_focuses (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      TEXT NOT NULL UNIQUE,
    plan_id         INTEGER NOT NULL,
    primary_goal_id INTEGER,
    secondary_goal_ids      TEXT NOT NULL DEFAULT '[]',
    planned_steps   TEXT NOT NULL DEFAULT '[]',
    carry_forward_from_session_id TEXT,
    carry_forward_notes     TEXT DEFAULT '',
    focus_mode             TEXT NOT NULL DEFAULT 'suggested',
    created_at      TEXT NOT NULL,
    FOREIGN KEY (plan_id) REFERENCES care_plans(id),
    FOREIGN KEY (primary_goal_id) REFERENCES plan_goals(id)
);
CREATE INDEX IF NOT EXISTS idx_session_focuses_session ON session_focuses(session_id);

-- Per-turn risk assessment from LLM classifier.
CREATE TABLE IF NOT EXISTS risk_assessments (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      TEXT NOT NULL,
    turn_idx        INTEGER NOT NULL,
    level           TEXT NOT NULL,
    confidence      REAL NOT NULL,
    drivers         TEXT NOT NULL DEFAULT '[]',
    protective_factors TEXT NOT NULL DEFAULT '[]',
    action_taken    TEXT NOT NULL DEFAULT 'none',
    raw_classifier_output TEXT DEFAULT '',
    created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_risk_assessments_session ON risk_assessments(session_id);
CREATE INDEX IF NOT EXISTS idx_risk_assessments_level ON risk_assessments(level);

-- Session-local safety episode state. No message text or clinical content is stored.
CREATE TABLE IF NOT EXISTS safety_episodes (
    session_id          TEXT PRIMARY KEY,
    state               TEXT NOT NULL,
    last_risk_level     TEXT NOT NULL,
    probe_sent_at_turn  INTEGER,
    resolved_at_turn    INTEGER,
    updated_at          TEXT NOT NULL
);

-- Measurement-based care: conversational item observations.
CREATE TABLE IF NOT EXISTS checkin_observations (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         TEXT NOT NULL,
    session_id      TEXT NOT NULL,
    instrument      TEXT NOT NULL,
    item_key        TEXT NOT NULL,
    response_text   TEXT NOT NULL,
    derived_score   REAL,
    confidence      REAL NOT NULL DEFAULT 0.0,
    created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_checkin_user_instrument
    ON checkin_observations(user_id, instrument);

-- Stage-of-Change snapshots (TTM).
CREATE TABLE IF NOT EXISTS progress_stages (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         TEXT NOT NULL,
    session_id      TEXT,
    stage           TEXT NOT NULL,
    confidence      REAL NOT NULL,
    rationale       TEXT NOT NULL DEFAULT '',
    created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_progress_stages_user
    ON progress_stages(user_id, created_at);
"""


# ────────────────────────── Repository ──────────────────────────────────


class CarePlanRepository:
    """Thin SQLite-bound repository — never makes business decisions."""

    def __init__(self, db: Any) -> None:
        """``db`` is a ``WellbeingDatabase`` instance (uses ``get_connection``)."""
        self.db = db
        self._init_schema()

    # ---- schema -----------------------------------------------------------
    def _init_schema(self) -> None:
        with self.db.get_connection() as conn:
            conn.executescript(_SCHEMA_DDL)
            # Additive migration for existing installations.
            cols = conn.execute("PRAGMA table_info(goal_updates)").fetchall()
            col_names = {row[1] for row in cols}
            if "turn_idx" not in col_names:
                conn.execute("ALTER TABLE goal_updates ADD COLUMN turn_idx INTEGER")
            focus_cols = {
                row[1]
                for row in conn.execute("PRAGMA table_info(session_focuses)").fetchall()
            }
            if "focus_mode" not in focus_cols:
                conn.execute(
                    "ALTER TABLE session_focuses "
                    "ADD COLUMN focus_mode TEXT NOT NULL DEFAULT 'suggested'"
                )
            conn.execute(
                """CREATE INDEX IF NOT EXISTS idx_goal_updates_goal_session_turn
                   ON goal_updates(goal_id, session_id, turn_idx)"""
            )
            # Cooldown tracking: reduces false-positive risk alerts after safe turns.
            safety_cols = {
                row[1]
                for row in conn.execute("PRAGMA table_info(safety_episodes)").fetchall()
            }
            if "cooldown_counter" not in safety_cols:
                conn.execute(
                    "ALTER TABLE safety_episodes "
                    "ADD COLUMN cooldown_counter INTEGER NOT NULL DEFAULT 0"
                )
            if "risk_paused_until_turn" not in safety_cols:
                conn.execute(
                    "ALTER TABLE safety_episodes "
                    "ADD COLUMN risk_paused_until_turn INTEGER"
                )
            conn.commit()

    # ---- care_plans --------------------------------------------------
    def create_plan(self, plan: CarePlan) -> int:
        with self.db.get_connection() as conn:
            cur = conn.execute(
                """INSERT INTO care_plans
                   (user_id, status, version, formulation_id, notes, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (plan.user_id, plan.status.value, plan.version, plan.formulation_id,
                 plan.notes, plan.created_at, plan.updated_at),
            )
            conn.commit()
            return int(cur.lastrowid or 0)

    def get_active_plan(self, user_id: str) -> Optional[CarePlan]:
        with self.db.get_connection() as conn:
            row = conn.execute(
                """SELECT * FROM care_plans
                   WHERE user_id = ? AND status = ?
                   ORDER BY version DESC LIMIT 1""",
                (user_id, PlanStatus.ACTIVE.value),
            ).fetchone()
        return self._row_to_plan(row) if row else None

    def list_plans(
        self,
        user_id: str,
        statuses: Optional[List[PlanStatus]] = None,
    ) -> List[CarePlan]:
        """List treatment plans for one user ordered by recency (newest first)."""
        with self.db.get_connection() as conn:
            if statuses:
                placeholders = ",".join("?" for _ in statuses)
                rows = conn.execute(
                    f"""SELECT * FROM care_plans
                        WHERE user_id = ? AND status IN ({placeholders})
                        ORDER BY updated_at DESC, created_at DESC""",
                    (user_id, *[s.value for s in statuses]),
                ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT * FROM care_plans
                       WHERE user_id = ?
                       ORDER BY updated_at DESC, created_at DESC""",
                    (user_id,),
                ).fetchall()
        return [self._row_to_plan(r) for r in rows]

    def update_plan_status(self, plan_id: int, status: PlanStatus) -> None:
        with self.db.get_connection() as conn:
            conn.execute(
                "UPDATE care_plans SET status = ?, updated_at = datetime('now') WHERE id = ?",
                (status.value, plan_id),
            )
            conn.commit()

    def attach_formulation(self, plan_id: int, formulation_id: int) -> None:
        with self.db.get_connection() as conn:
            conn.execute(
                "UPDATE care_plans SET formulation_id = ?, updated_at = datetime('now') WHERE id = ?",
                (formulation_id, plan_id),
            )
            conn.commit()

    @staticmethod
    def _row_to_plan(row: sqlite3.Row) -> CarePlan:
        return CarePlan(
            id=row["id"], user_id=row["user_id"],
            status=PlanStatus(row["status"]), version=row["version"],
            formulation_id=row["formulation_id"], notes=row["notes"] or "",
            created_at=row["created_at"], updated_at=row["updated_at"],
        )

    # ---- plan_goals -------------------------------------------------------
    def insert_goal(self, goal: PlanGoal) -> int:
        with self.db.get_connection() as conn:
            cur = conn.execute(
                """INSERT INTO plan_goals
                   (plan_id, parent_goal_id, title, rationale, target_metric,
                    target_value, status, priority, confidence, embedding,
                    last_progress_score, created_at, closed_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (goal.plan_id, goal.parent_goal_id, goal.title, goal.rationale,
                 goal.target_metric, goal.target_value, goal.status.value,
                 goal.priority, goal.confidence, goal.embedding,
                 goal.last_progress_score, goal.created_at, goal.closed_at),
            )
            conn.commit()
            return int(cur.lastrowid or 0)

    def update_goal_status(self, goal_id: int, status: GoalStatus,
                           closed_at: Optional[str] = None) -> None:
        with self.db.get_connection() as conn:
            conn.execute(
                "UPDATE plan_goals SET status = ?, closed_at = ? WHERE id = ?",
                (status.value, closed_at, goal_id),
            )
            conn.commit()

    def update_goal_progress(self, goal_id: int, progress_score: float) -> None:
        with self.db.get_connection() as conn:
            conn.execute(
                "UPDATE plan_goals SET last_progress_score = ? WHERE id = ?",
                (progress_score, goal_id),
            )
            conn.commit()

    def list_goals(self, plan_id: int,
                   statuses: Optional[List[GoalStatus]] = None) -> List[PlanGoal]:
        with self.db.get_connection() as conn:
            if statuses:
                placeholders = ",".join("?" for _ in statuses)
                rows = conn.execute(
                    f"""SELECT * FROM plan_goals
                        WHERE plan_id = ? AND status IN ({placeholders})
                        ORDER BY priority ASC, created_at ASC""",
                    (plan_id, *[s.value for s in statuses]),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM plan_goals WHERE plan_id = ? ORDER BY priority ASC, created_at ASC",
                    (plan_id,),
                ).fetchall()
        return [self._row_to_goal(r) for r in rows]

    def get_goal(self, goal_id: int) -> Optional[PlanGoal]:
        with self.db.get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM plan_goals WHERE id = ?", (goal_id,),
            ).fetchone()
        return self._row_to_goal(row) if row else None

    @staticmethod
    def _row_to_goal(row: sqlite3.Row) -> PlanGoal:
        return PlanGoal(
            id=row["id"], plan_id=row["plan_id"],
            parent_goal_id=row["parent_goal_id"], title=row["title"],
            rationale=row["rationale"] or "", target_metric=row["target_metric"] or "",
            target_value=row["target_value"], status=GoalStatus(row["status"]),
            priority=row["priority"], confidence=row["confidence"],
            embedding=row["embedding"],
            last_progress_score=row["last_progress_score"],
            created_at=row["created_at"], closed_at=row["closed_at"],
        )

    # ---- goal_updates -----------------------------------------------------
    def insert_goal_update(self, upd: GoalUpdate) -> int:
        with self.db.get_connection() as conn:
            cur = conn.execute(
                """INSERT INTO goal_updates
                   (goal_id, session_id, turn_idx, progress_score, confidence, evidence, delta, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (upd.goal_id, upd.session_id, upd.turn_idx, upd.progress_score,
                 upd.confidence, list_to_json(upd.evidence), upd.delta,
                 upd.created_at),
            )
            conn.commit()
        # cache last score for fast prompt-time access
        self.update_goal_progress(upd.goal_id, upd.progress_score)
        return int(cur.lastrowid or 0)

    def goal_trajectory(self, goal_id: int, limit: int = 20) -> List[GoalUpdate]:
        with self.db.get_connection() as conn:
            rows = conn.execute(
                """SELECT * FROM goal_updates
                   WHERE goal_id = ? ORDER BY created_at DESC LIMIT ?""",
                (goal_id, limit),
            ).fetchall()
        out: List[GoalUpdate] = []
        for r in rows:
            out.append(GoalUpdate(
                id=r["id"], goal_id=r["goal_id"], session_id=r["session_id"],
                turn_idx=r["turn_idx"],
                progress_score=r["progress_score"], confidence=r["confidence"],
                evidence=json_to_list(r["evidence"]),
                delta=r["delta"], created_at=r["created_at"],
            ))
        return out

    def has_goal_update(
        self,
        goal_id: int,
        session_id: str,
        turn_idx: Optional[int],
    ) -> bool:
        with self.db.get_connection() as conn:
            if turn_idx is None:
                row = conn.execute(
                    """SELECT 1 FROM goal_updates
                       WHERE goal_id = ? AND session_id = ?
                       ORDER BY id DESC LIMIT 1""",
                    (goal_id, session_id),
                ).fetchone()
            else:
                row = conn.execute(
                    """SELECT 1 FROM goal_updates
                       WHERE goal_id = ? AND session_id = ? AND turn_idx = ?
                       ORDER BY id DESC LIMIT 1""",
                    (goal_id, session_id, turn_idx),
                ).fetchone()
        return row is not None

    def count_goals(self, plan_id: int) -> int:
        with self.db.get_connection() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS c FROM plan_goals WHERE plan_id = ?",
                (plan_id,),
            ).fetchone()
        return int(row["c"]) if row else 0

    def count_goal_updates(self, plan_id: int) -> int:
        with self.db.get_connection() as conn:
            row = conn.execute(
                """SELECT COUNT(*) AS c
                   FROM goal_updates gu
                   JOIN plan_goals pg ON gu.goal_id = pg.id
                   WHERE pg.plan_id = ?""",
                (plan_id,),
            ).fetchone()
        return int(row["c"]) if row else 0

    # ---- context_formulations_v2 --------------------------------------------
    def insert_formulation(self, f: CaseFormulation) -> int:
        with self.db.get_connection() as conn:
            cur = conn.execute(
                """INSERT INTO context_formulations_v2
                   (user_id, presenting, predisposing, precipitating, perpetuating,
                    protective, confidence, version, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (f.user_id, list_to_json(f.presenting), list_to_json(f.predisposing),
                 list_to_json(f.precipitating), list_to_json(f.perpetuating),
                 list_to_json(f.protective), f.confidence, f.version, f.created_at),
            )
            conn.commit()
            return int(cur.lastrowid or 0)

    def latest_formulation(self, user_id: str) -> Optional[CaseFormulation]:
        with self.db.get_connection() as conn:
            row = conn.execute(
                """SELECT * FROM context_formulations_v2
                   WHERE user_id = ? ORDER BY version DESC LIMIT 1""",
                (user_id,),
            ).fetchone()
        if not row:
            return None
        return CaseFormulation(
            id=row["id"], user_id=row["user_id"],
            presenting=json_to_list(row["presenting"]),
            predisposing=json_to_list(row["predisposing"]),
            precipitating=json_to_list(row["precipitating"]),
            perpetuating=json_to_list(row["perpetuating"]),
            protective=json_to_list(row["protective"]),
            confidence=row["confidence"], version=row["version"],
            created_at=row["created_at"],
        )

    # ---- session_focuses --------------------------------------------------
    def upsert_focus(self, focus: SessionFocus) -> int:
        with self.db.get_connection() as conn:
            cur = conn.execute(
                """INSERT INTO session_focuses
                   (session_id, plan_id, primary_goal_id, secondary_goal_ids,
                    planned_steps, carry_forward_from_session_id,
                          carry_forward_notes, focus_mode, created_at)
                         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(session_id) DO UPDATE SET
                     plan_id=excluded.plan_id,
                     primary_goal_id=excluded.primary_goal_id,
                     secondary_goal_ids=excluded.secondary_goal_ids,
                     planned_steps=excluded.planned_steps,
                     carry_forward_from_session_id=excluded.carry_forward_from_session_id,
                                         carry_forward_notes=excluded.carry_forward_notes,
                                         focus_mode=CASE
                                             WHEN session_focuses.focus_mode = 'confirmed' THEN 'confirmed'
                                             ELSE excluded.focus_mode
                                         END""",
                (focus.session_id, focus.plan_id, focus.primary_goal_id,
                 list_to_json(focus.secondary_goal_ids),
                 list_to_json(focus.planned_steps),
                 focus.carry_forward_from_session_id,
                 focus.carry_forward_notes, focus.focus_mode, focus.created_at),
            )
            conn.commit()
            return int(cur.lastrowid or 0)

    def get_focus(self, session_id: str) -> Optional[SessionFocus]:
        with self.db.get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM session_focuses WHERE session_id = ?", (session_id,),
            ).fetchone()
        if not row:
            return None
        return SessionFocus(
            id=row["id"], session_id=row["session_id"], plan_id=row["plan_id"],
            primary_goal_id=row["primary_goal_id"],
            secondary_goal_ids=json_to_list(row["secondary_goal_ids"]),
            planned_steps=json_to_list(row["planned_steps"]),
            carry_forward_from_session_id=row["carry_forward_from_session_id"],
            carry_forward_notes=row["carry_forward_notes"] or "",
            focus_mode=row["focus_mode"] or "suggested",
            created_at=row["created_at"],
        )

    def set_focus_mode(self, session_id: str, mode: str) -> bool:
        if mode not in {"suggested", "confirmed", "paused", "dismissed"}:
            raise ValueError(f"Unsupported focus mode: {mode}")
        with self.db.get_connection() as conn:
            cursor = conn.execute(
                "UPDATE session_focuses SET focus_mode = ? WHERE session_id = ?",
                (mode, session_id),
            )
            conn.commit()
            return cursor.rowcount == 1

    def select_session_focus(self, session_id: str, goal_id: int) -> bool:
        """Select one active goal as the user-confirmed focus for this session."""
        with self.db.get_connection() as conn:
            goal = conn.execute(
                """
                SELECT id, plan_id FROM plan_goals
                WHERE id = ? AND status = ?
                """,
                (goal_id, GoalStatus.ACTIVE.value),
            ).fetchone()
            if goal is None:
                return False
            conn.execute(
                """
                INSERT INTO session_focuses (
                    session_id, plan_id, primary_goal_id, secondary_goal_ids,
                    planned_steps, carry_forward_notes, focus_mode,
                    created_at
                ) VALUES (?, ?, ?, '[]', '[]', '', 'confirmed', datetime('now'))
                ON CONFLICT(session_id) DO UPDATE SET
                    plan_id=excluded.plan_id,
                    primary_goal_id=excluded.primary_goal_id,
                    secondary_goal_ids='[]',
                    planned_steps='[]',
                    carry_forward_notes='',
                    focus_mode='confirmed'
                """,
                (session_id, goal["plan_id"], goal_id),
            )
            conn.commit()
        return True

    def latest_focus_for_user(self, user_id: str) -> Optional[SessionFocus]:
        """Latest focus across all active sessions of this user."""
        with self.db.get_connection() as conn:
            row = conn.execute(
                """SELECT f.* FROM session_focuses f
                   JOIN care_plans p ON f.plan_id = p.id
                   WHERE p.user_id = ?
                   ORDER BY f.created_at DESC LIMIT 1""",
                (user_id,),
            ).fetchone()
        if not row:
            return None
        return SessionFocus(
            id=row["id"], session_id=row["session_id"], plan_id=row["plan_id"],
            primary_goal_id=row["primary_goal_id"],
            secondary_goal_ids=json_to_list(row["secondary_goal_ids"]),
            planned_steps=json_to_list(row["planned_steps"]),
            carry_forward_from_session_id=row["carry_forward_from_session_id"],
            carry_forward_notes=row["carry_forward_notes"] or "",
            focus_mode=row["focus_mode"] or "suggested",
            created_at=row["created_at"],
        )

    # ---- risk_assessments -------------------------------------------------
    def insert_risk_assessment(self, r: RiskAssessment) -> int:
        with self.db.get_connection() as conn:
            cur = conn.execute(
                """INSERT INTO risk_assessments
                   (session_id, turn_idx, level, confidence, drivers,
                    protective_factors, action_taken, raw_classifier_output, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (r.session_id, r.turn_idx, r.level.value, r.confidence,
                 list_to_json(r.drivers), list_to_json(r.protective_factors),
                 r.action_taken, r.raw_classifier_output, r.created_at),
            )
            conn.commit()
            return int(cur.lastrowid or 0)

    def latest_risk(self, session_id: str) -> Optional[RiskAssessment]:
        with self.db.get_connection() as conn:
            row = conn.execute(
                """SELECT * FROM risk_assessments WHERE session_id = ?
                   ORDER BY created_at DESC LIMIT 1""",
                (session_id,),
            ).fetchone()
        if not row:
            return None
        return RiskAssessment(
            id=row["id"], session_id=row["session_id"], turn_idx=row["turn_idx"],
            level=RiskLevel(row["level"]), confidence=row["confidence"],
            drivers=json_to_list(row["drivers"]),
            protective_factors=json_to_list(row["protective_factors"]),
            action_taken=row["action_taken"],
            raw_classifier_output=row["raw_classifier_output"] or "",
            created_at=row["created_at"],
        )

    def transition_safety_episode(
        self,
        session_id: str,
        turn_idx: int,
        level: RiskLevel,
        *,
        reprobe_after_turns: int = 12,
        cooldown_threshold: int = 4,       # safe turns before cooldown kicks in
        cooldown_suppress_probe: bool = True,  # suppress probe during cooldown
    ) -> str:
        """Return ``normal``, ``probe`` or ``acute`` and persist episode state.

        Cooldown logic (false-positive reduction):
        - After *cooldown_threshold* consecutive safe (NONE/LOW) turns the
          ``cooldown_counter`` reaches the threshold.  While elevated,
          ``probe`` actions are suppressed — the system trusts the recent
          safe trajectory.
        - ACUTE always breaks through (no cooldown for acute risk).
        - ``risk_paused_until_turn`` can be set externally (user feedback) to
          skip risk processing for a window of turns.
        """
        # ── Read previous episode state ──────────────────────────────────
        with self.db.get_connection() as conn:
            previous = conn.execute(
                "SELECT * FROM safety_episodes WHERE session_id = ?",
                (session_id,),
            ).fetchone()

        prev_cooldown = (
            int(previous["cooldown_counter"])
            if previous is not None and previous["cooldown_counter"] is not None
            else 0
        )
        prev_paused_until = (
            int(previous["risk_paused_until_turn"])
            if previous is not None and previous["risk_paused_until_turn"] is not None
            else None
        )
        prev_probe = (
            int(previous["probe_sent_at_turn"])
            if previous is not None and previous["probe_sent_at_turn"] is not None
            else None
        )

        # ── User-pause override ──────────────────────────────────────────
        if prev_paused_until is not None and turn_idx < prev_paused_until:
            # Risk assessment paused by user — always return normal
            return "normal"

        # ── ACUTE always breaks through ──────────────────────────────────
        if level == RiskLevel.ACUTE:
            action = "acute"
            state = "acute_active"
            new_cooldown = 0
        else:
            # ── Cooldown logic for ELEVATED ──────────────────────────────
            if level in (RiskLevel.NONE, RiskLevel.LOW):
                # Safe turn → increment cooldown counter
                new_cooldown = prev_cooldown + 1
                in_cooldown = False
            else:
                # ELEVATED → check if we're in cooldown period
                in_cooldown = prev_cooldown >= cooldown_threshold
                new_cooldown = 0  # reset on elevated

            can_probe = (
                prev_probe is None
                or turn_idx - prev_probe >= reprobe_after_turns
            )

            if level == RiskLevel.ELEVATED and can_probe and not (
                cooldown_suppress_probe and in_cooldown
            ):
                action = "probe"
                state = "check_required"
                prev_probe = turn_idx
            elif level == RiskLevel.ELEVATED:
                action = "normal"
                state = "cooldown" if in_cooldown else "elevated_monitoring"
            else:
                action = "normal"
                state = "resolved" if previous is not None else "none"

        # ── Persist updated state ────────────────────────────────────────
        with self.db.get_connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            probe_turn = (
                turn_idx if action == "probe"
                else prev_probe
            )
            resolved_turn = turn_idx if state == "resolved" else None
            # Clear pause if it has expired
            paused_until = (
                prev_paused_until
                if prev_paused_until is not None and turn_idx < prev_paused_until
                else None
            )
            conn.execute(
                """
                INSERT INTO safety_episodes (
                    session_id, state, last_risk_level, probe_sent_at_turn,
                    resolved_at_turn, cooldown_counter, risk_paused_until_turn,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))
                ON CONFLICT(session_id) DO UPDATE SET
                    state=excluded.state,
                    last_risk_level=excluded.last_risk_level,
                    probe_sent_at_turn=excluded.probe_sent_at_turn,
                    resolved_at_turn=excluded.resolved_at_turn,
                    cooldown_counter=excluded.cooldown_counter,
                    risk_paused_until_turn=excluded.risk_paused_until_turn,
                    updated_at=excluded.updated_at
                """,
                (
                    session_id,
                    state,
                    level.value,
                    probe_turn,
                    resolved_turn,
                    new_cooldown,
                    paused_until,
                ),
            )
            conn.commit()
        return action

    def get_safety_episode(self, session_id: str) -> Optional[Dict[str, Any]]:
        with self.db.get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM safety_episodes WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        return dict(row) if row is not None else None

    def pause_risk_assessment(
        self,
        session_id: str,
        pause_until_turn: int,
    ) -> None:
        """Pause risk assessment until *pause_until_turn* (user feedback).

        This sets ``risk_paused_until_turn`` in the safety_episodes table.
        If no episode exists yet, one is created with state ``none``.
        ACUTE risk always breaks through regardless of pause state.
        """
        with self.db.get_connection() as conn:
            conn.execute(
                """
                INSERT INTO safety_episodes (
                    session_id, state, last_risk_level, probe_sent_at_turn,
                    resolved_at_turn, cooldown_counter, risk_paused_until_turn,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))
                ON CONFLICT(session_id) DO UPDATE SET
                    risk_paused_until_turn=excluded.risk_paused_until_turn,
                    updated_at=excluded.updated_at
                """,
                (
                    session_id,
                    "paused",
                    "none",
                    None,
                    None,
                    0,
                    pause_until_turn,
                ),
            )
            conn.commit()

    # ---- checkin_observations -------------------------------------------------
    def insert_mbc(self, obs: MBCObservation) -> int:
        with self.db.get_connection() as conn:
            cur = conn.execute(
                """INSERT INTO checkin_observations
                   (user_id, session_id, instrument, item_key, response_text,
                    derived_score, confidence, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (obs.user_id, obs.session_id, obs.instrument, obs.item_key,
                 obs.response_text, obs.derived_score, obs.confidence, obs.created_at),
            )
            conn.commit()
            return int(cur.lastrowid or 0)

    def latest_mbc_per_item(self, user_id: str, instrument: str) -> List[MBCObservation]:
        """Most recent observation per item_key for the given instrument."""
        with self.db.get_connection() as conn:
            rows = conn.execute(
                """SELECT m.* FROM checkin_observations m
                   JOIN (
                     SELECT item_key, MAX(created_at) AS max_ts
                     FROM checkin_observations
                     WHERE user_id = ? AND instrument = ?
                     GROUP BY item_key
                   ) latest
                   ON m.item_key = latest.item_key AND m.created_at = latest.max_ts
                   WHERE m.user_id = ? AND m.instrument = ?""",
                (user_id, instrument, user_id, instrument),
            ).fetchall()
        out: List[MBCObservation] = []
        for r in rows:
            out.append(MBCObservation(
                id=r["id"], user_id=r["user_id"], session_id=r["session_id"],
                instrument=r["instrument"], item_key=r["item_key"],
                response_text=r["response_text"], derived_score=r["derived_score"],
                confidence=r["confidence"], created_at=r["created_at"],
            ))
        return out

    # ---- progress_stages ------------------------------------------------
    def insert_stage(self, s: StageAssessment) -> int:
        with self.db.get_connection() as conn:
            cur = conn.execute(
                """INSERT INTO progress_stages
                   (user_id, session_id, stage, confidence, rationale, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (s.user_id, s.session_id, s.stage.value, s.confidence,
                 s.rationale, s.created_at),
            )
            conn.commit()
            return int(cur.lastrowid or 0)

    def latest_stage(self, user_id: str) -> Optional[StageAssessment]:
        with self.db.get_connection() as conn:
            row = conn.execute(
                """SELECT * FROM progress_stages
                   WHERE user_id = ? ORDER BY created_at DESC LIMIT 1""",
                (user_id,),
            ).fetchone()
        if not row:
            return None
        return StageAssessment(
            id=row["id"], user_id=row["user_id"], session_id=row["session_id"],
            stage=StageOfChange(row["stage"]), confidence=row["confidence"],
            rationale=row["rationale"] or "", created_at=row["created_at"],
        )
