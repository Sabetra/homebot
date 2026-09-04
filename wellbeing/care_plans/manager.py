"""
CarePlanManager — Orchestrator for the SOTA treatment plan domain.

This is the only public entry point that the rest of the application
should depend on. It coordinates:

- Plan lifecycle (auto-create on first use)
- Goal extraction → matching → persistence (no string-set dedup)
- Per-session progress assessment
- Risk classification (per turn)
- Case formulation (periodic refresh)
- Stage-of-Change classification (periodic refresh)
- Session focus / carry-forward
- Self-supervision review of draft responses
- Context bundling for prompt construction

All sub-components fail soft: if an LLM call fails the manager logs and
proceeds without that piece — it never raises into the caller.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from .case_formulator import CaseFormulator
from .extractor import GoalExtractor, ProgressEngine
from .focus_planner import FocusPlanner
from .goal_matcher import GoalMatcher, MatchDecision, embed_goal_text
from .mbc import MBCEngine, MBCItem, WHO5_LIKE
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
    _utcnow_iso,
)
from .repository import CarePlanRepository
from .reviewer import ReviewVerdict, Reviewer
from .risk_classifier import RiskClassifier
from .stage_classifier import StageClassifier

logger = logging.getLogger(__name__)


# ─────────────────────────── Bundles ────────────────────────────────────


@dataclass
class TreatmentContext:
    """Bundle of state delivered to the prompt builder."""
    plan: Optional[CarePlan]
    formulation: Optional[CaseFormulation]
    active_goals: List[PlanGoal] = field(default_factory=list)
    primary_goal: Optional[PlanGoal] = None
    secondary_goals: List[PlanGoal] = field(default_factory=list)
    focus: Optional[SessionFocus] = None
    previous_focus: Optional[SessionFocus] = None
    stage: Optional[StageAssessment] = None
    latest_risk: Optional[RiskAssessment] = None
    latest_mbc: List[MBCObservation] = field(default_factory=list)


@dataclass
class TurnResult:
    """Result of processing one user turn."""
    risk: Optional[RiskAssessment] = None
    safety_action: str = "normal"
    new_goals: List[PlanGoal] = field(default_factory=list)
    progress_updates: List[GoalUpdate] = field(default_factory=list)
    formulation_refreshed: Optional[CaseFormulation] = None
    stage_refreshed: Optional[StageAssessment] = None
    focus_refreshed: Optional[SessionFocus] = None


# ─────────────────────────── Manager ────────────────────────────────────


class CarePlanManager:
    """High-level orchestrator. Constructed once per process.

    Architectural invariant — RAG isolation
    ----------------------------------------
    Inference components inside this domain (``GoalExtractor``,
    ``ProgressEngine``, ``RiskClassifier``, ``CaseFormulator``,
    ``StageClassifier``, ``FocusPlanner``, ``MBCEngine``) operate
    **strictly on user-scoped data**: ``recent_interactions``, the
    user's ``treatment_plan``, ``context_formulations_v2``, and the
    user-scoped knowledge graph. They MUST NOT pull general
    psychology RAG content. Mixing generic textbook knowledge into
    these classifiers would systematically distort outputs:

    * The ``RiskClassifier`` would over-pathologise (DSM symptom
      lists in the prompt make the model see risk markers
      everywhere).
    * The ``GoalExtractor`` would surface goals from the literature
      instead of the user's own statements.
    * The ``CaseFormulator`` would replace idiographic 5P content
      with nomothetic stereotypes.

    The single legitimate RAG consumer in this domain is the
    ``Reviewer`` — but only as an *adherence checker*, not as a
    content source: it asks RAG for evidence-based crisis-response
    elements (e.g. safety-plan steps) when risk is ELEVATED/ACUTE
    and verifies that the draft contains them. The retrieved text
    is never quoted to the user.
    """

    # How often the slow components run (in turns within a session).
    EXTRACTION_EVERY = 6
    PROGRESS_EVERY = 4
    FORMULATION_EVERY = 18
    STAGE_EVERY = 8
    FOCUS_EVERY = 12
    REVIEW_EVERY = 6

    def __init__(self, db: Any, llm_function: Optional[Callable[..., str]]) -> None:
        self.repo = CarePlanRepository(db)
        self.llm_function = llm_function
        self.matcher = GoalMatcher(llm_function)
        self.extractor = GoalExtractor(llm_function)
        self.progress = ProgressEngine(llm_function)
        self.risk = RiskClassifier(llm_function)
        self.formulator = CaseFormulator(llm_function)
        self.stage = StageClassifier(llm_function)
        self.focus_planner = FocusPlanner(llm_function)
        self.mbc = MBCEngine(llm_function)
        self.reviewer = Reviewer(llm_function)
        # Track current turn for pause_risk_assessment window calculations
        self._current_turn: int = 0

    # ─────────────────── runtime wiring ─────────────────────────────────
    def set_rag_function(
        self, rag_search_fn: Optional[Callable[[str], List[str]]],
    ) -> None:
        """Inject the RAG search callable used by the Reviewer.

        This is the **only** RAG handle ever passed into the treatment
        domain — the architectural invariant in the class docstring forbids
        any inference component from consuming it. The Reviewer uses it
        purely as an adherence checker for crisis turns (ELEVATED / ACUTE).
        """
        self.reviewer.set_rag_function(rag_search_fn)
        logger.info(
            "[treatment] RAG search function %s for crisis-adherence review",
            "wired" if rag_search_fn else "cleared",
        )

    # ─────────────────── plan lifecycle ─────────────────────────────────
    def get_or_create_plan(self, user_id: str) -> CarePlan:
        plan = self.repo.get_active_plan(user_id)
        if plan is not None:
            return plan
        new_plan = CarePlan(
            id=None, user_id=user_id, status=PlanStatus.ACTIVE,
            version=1, formulation_id=None, notes="auto-created",
        )
        new_plan.id = self.repo.create_plan(new_plan)
        logger.info("[treatment] created new plan %s for user %s",
                    new_plan.id, user_id)
        return new_plan

    # ─────────────────── per-turn entry point ───────────────────────────
    def process_turn(
        self,
        *,
        user_id: str,
        session_id: str,
        turn_idx: int,
        user_message: str,
        recent_interactions: List[Dict[str, Any]],
        kg_triples: Optional[List[Dict[str, Any]]] = None,
        profile: Optional[Dict[str, Any]] = None,
        mood_summary: str = "—",
        run_risk: bool = True,
        run_formulation: bool = True,
        run_stage: bool = True,
        run_focus: bool = True,
    ) -> TurnResult:
        """Process one user turn end-to-end.

        Always: assess risk.
        Periodically: extract goals, score progress, refresh formulation,
        classify stage, plan focus.

        Returns a ``TurnResult`` for diagnostics; persistence is already done.
        """
        result = TurnResult()
        plan = self.get_or_create_plan(user_id)
        # plan.id is guaranteed non-None by get_or_create_plan (it assigns on create).
        assert plan.id is not None
        plan_id: int = plan.id

        # Track current turn for pause window calculations
        self._current_turn = turn_idx

        # 1) Risk — always per turn in live processing.
        if run_risk:
            # Check if risk assessment is paused (user feedback)
            episode = self.repo.get_safety_episode(session_id)
            is_paused = (
                episode is not None
                and episode.get("risk_paused_until_turn") is not None
                and turn_idx < episode.get("risk_paused_until_turn")
            )
            if is_paused:
                logger.debug(
                    "[treatment] risk assessment paused until turn %d (current %d)",
                    episode.get("risk_paused_until_turn"), turn_idx,
                )
                result.safety_action = "normal"
            else:
                # Get cooldown counter to adjust thresholds
                cooldown = episode.get("cooldown_counter", 0) if episode else 0
                effective_elevated_threshold = 0.75 if cooldown >= 8 else 0.65
                effective_acute_threshold = 0.90 if cooldown >= 8 else 0.80

                try:
                    previous = self.repo.latest_risk(session_id)
                    risk = self.risk.assess(
                        session_id=session_id, turn_idx=turn_idx,
                        user_message=user_message,
                        previous_level=(previous.level if previous else RiskLevel.NONE),
                        mood_hint=mood_summary,
                    )
                    # Apply adaptive thresholds post-classification
                    if risk.level == RiskLevel.ACUTE and risk.confidence < effective_acute_threshold:
                        risk.level = RiskLevel.ELEVATED if risk.confidence >= effective_elevated_threshold else RiskLevel.LOW
                        risk.action_taken = "reviewer_required" if risk.level == RiskLevel.ELEVATED else "monitor"
                        logger.debug(
                            "[treatment] ACUTE downgraded to %s (conf %.2f < %.2f, cooldown=%d)",
                            risk.level.value, risk.confidence, effective_acute_threshold, cooldown,
                        )
                    elif risk.level == RiskLevel.ELEVATED and risk.confidence < effective_elevated_threshold:
                        risk.level = RiskLevel.LOW
                        risk.action_taken = "monitor"
                        logger.debug(
                            "[treatment] ELEVATED downgraded to LOW (conf %.2f < %.2f, cooldown=%d)",
                            risk.confidence, effective_elevated_threshold, cooldown,
                        )

                    self.repo.insert_risk_assessment(risk)
                    result.risk = risk
                    result.safety_action = self.repo.transition_safety_episode(
                        session_id,
                        turn_idx,
                        risk.level,
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning("[treatment] risk assessment failed: %s", exc)

        # 2) Goal extraction (periodic) → matcher → persist
        if turn_idx > 0 and turn_idx % self.EXTRACTION_EVERY == 0:
            try:
                existing = self.repo.list_goals(plan_id, statuses=[
                    GoalStatus.PROPOSED, GoalStatus.ACTIVE,
                ])
                proposals = self.extractor.extract(
                    interactions=recent_interactions, existing_goals=existing,
                )
                for prop in proposals:
                    decision = self.matcher.match(
                        proposal_title=prop["title"],
                        proposal_rationale=prop["rationale"],
                        existing_goals=existing,
                    )
                    persisted = self._apply_match_decision(
                        plan_id=plan_id, proposal=prop, decision=decision,
                    )
                    if persisted is not None:
                        result.new_goals.append(persisted)
                        existing.append(persisted)
            except Exception as exc:  # noqa: BLE001
                logger.warning("[treatment] goal extraction failed: %s", exc)

        # 3) Progress (periodic)
        if turn_idx > 0 and turn_idx % self.PROGRESS_EVERY == 0:
            try:
                actives = self.repo.list_goals(plan_id, statuses=[GoalStatus.ACTIVE])
                updates = self.progress.assess(
                    session_id=session_id,
                    turn_idx=turn_idx,
                    active_goals=actives, interactions=recent_interactions,
                )
                for upd in updates:
                    if self.repo.has_goal_update(
                        upd.goal_id, upd.session_id, upd.turn_idx,
                    ):
                        continue
                    self.repo.insert_goal_update(upd)
                    if upd.progress_score >= 0.95:
                        self.repo.update_goal_status(
                            upd.goal_id, GoalStatus.ACHIEVED,
                            closed_at=_utcnow_iso(),
                        )
                result.progress_updates = updates
            except Exception as exc:  # noqa: BLE001
                logger.warning("[treatment] progress engine failed: %s", exc)

        # 4) Case formulation (periodic / first-time)
        if run_formulation and (
            (turn_idx > 0 and turn_idx % self.FORMULATION_EVERY == 0)
            or (plan.formulation_id is None and turn_idx >= 5)
        ):
            try:
                previous_form = self.repo.latest_formulation(user_id)
                f = self.formulator.formulate(
                    user_id=user_id,
                    interactions=recent_interactions,
                    kg_triples=kg_triples or [],
                    profile=profile,
                    previous=previous_form,
                )
                if f is not None:
                    f.id = self.repo.insert_formulation(f)
                    self.repo.attach_formulation(plan_id, f.id)
                    result.formulation_refreshed = f
            except Exception as exc:  # noqa: BLE001
                logger.warning("[treatment] formulation failed: %s", exc)

        # 5) Stage-of-Change (periodic)
        if run_stage and turn_idx > 0 and turn_idx % self.STAGE_EVERY == 0:
            try:
                actives = self.repo.list_goals(plan_id, statuses=[GoalStatus.ACTIVE])
                trajectory: List[GoalUpdate] = []
                for g in actives[:6]:
                    assert g.id is not None
                    trajectory.extend(self.repo.goal_trajectory(g.id, limit=3))
                trajectory.sort(key=lambda u: u.created_at, reverse=True)
                stage = self.stage.classify(
                    user_id=user_id, session_id=session_id,
                    active_goals=actives, recent_updates=trajectory,
                    formulation=self.repo.latest_formulation(user_id),
                    mood_summary=mood_summary,
                )
                if stage is not None:
                    self.repo.insert_stage(stage)
                    result.stage_refreshed = stage
            except Exception as exc:  # noqa: BLE001
                logger.warning("[treatment] stage classification failed: %s", exc)

        # 6) Session focus (periodic)
        focus_existing = self.repo.get_focus(session_id)
        needs_focus = (focus_existing is None and turn_idx >= 2) or (
            turn_idx > 0 and turn_idx % self.FOCUS_EVERY == 0
        )
        if run_focus and needs_focus:
            try:
                actives = self.repo.list_goals(plan_id, statuses=[GoalStatus.ACTIVE])
                previous_focus = self.repo.latest_focus_for_user(user_id)
                # if previous focus is for *this* session, treat it as 'previous'
                # only if explicitly older
                if previous_focus and previous_focus.session_id == session_id:
                    previous_focus = None
                stage_for_focus = result.stage_refreshed or self.repo.latest_stage(user_id)
                focus = self.focus_planner.plan(
                    plan_id=plan_id, session_id=session_id,
                    active_goals=actives, stage=stage_for_focus,
                    previous_focus=previous_focus,
                    recent_messages=recent_interactions,
                )
                if focus is not None:
                    self.repo.upsert_focus(focus)
                    result.focus_refreshed = focus
            except Exception as exc:  # noqa: BLE001
                logger.warning("[treatment] focus planning failed: %s", exc)

        return result

    def backfill_session(
        self,
        *,
        user_id: str,
        session_id: str,
        interactions: List[Dict[str, Any]],
    ) -> Dict[str, int]:
        """Replay only user turns to backfill missing goals/progress.

        Risk, stage, formulation and focus are intentionally skipped to avoid
        duplicating existing live-time telemetry.
        """
        counters = {"turns": 0, "new_goals": 0, "new_updates": 0}
        user_turn_idx = 0
        if not interactions:
            return counters

        for idx, item in enumerate(interactions):
            role = str(item.get("role") or "").strip().lower()
            if role != "user":
                continue

            message = str(item.get("content") or "").strip()
            if not message:
                continue

            user_turn_idx += 1
            counters["turns"] += 1
            recent_payload = interactions[max(0, idx - 19): idx + 1]

            result = self.process_turn(
                user_id=user_id,
                session_id=session_id,
                turn_idx=user_turn_idx,
                user_message=message,
                recent_interactions=recent_payload,
                mood_summary="—",
                run_risk=False,
                run_formulation=False,
                run_stage=False,
                run_focus=False,
            )
            counters["new_goals"] += len(result.new_goals)
            counters["new_updates"] += len(result.progress_updates)

        return counters

    # ─────────────────── public risk shortcut (used by handler) ─────────
    def assess_risk(
        self,
        *,
        session_id: str,
        turn_idx: int,
        user_message: str,
        mood_hint: str = "—",
    ) -> RiskAssessment:
        # Check if risk assessment is paused (user feedback)
        episode = self.repo.get_safety_episode(session_id)
        if episode is not None:
            paused_until = episode.get("risk_paused_until_turn")
            if paused_until is not None and turn_idx < paused_until:
                logger.debug(
                    "[treatment] risk assessment paused until turn %d (current %d)",
                    paused_until, turn_idx,
                )
                # Return safe assessment without calling LLM
                previous = self.repo.latest_risk(session_id)
                return RiskAssessment(
                    id=None, session_id=session_id, turn_idx=turn_idx,
                    level=RiskLevel.NONE, confidence=1.0,
                    drivers=[], protective_factors=[],
                    action_taken="none", raw_classifier_output="",
                )

        # Get cooldown counter to adjust thresholds
        cooldown = episode.get("cooldown_counter", 0) if episode else 0
        effective_elevated_threshold = 0.75 if cooldown >= 8 else 0.65
        effective_acute_threshold = 0.90 if cooldown >= 8 else 0.80

        previous = self.repo.latest_risk(session_id)
        risk = self.risk.assess(
            session_id=session_id, turn_idx=turn_idx,
            user_message=user_message,
            previous_level=(previous.level if previous else RiskLevel.NONE),
            mood_hint=mood_hint,
        )
        # Apply adaptive thresholds post-classification
        if risk.level == RiskLevel.ACUTE and risk.confidence < effective_acute_threshold:
            risk.level = RiskLevel.ELEVATED if risk.confidence >= effective_elevated_threshold else RiskLevel.LOW
            risk.action_taken = "reviewer_required" if risk.level == RiskLevel.ELEVATED else "monitor"
            logger.debug(
                "[treatment] ACUTE downgraded to %s (conf %.2f < %.2f, cooldown=%d)",
                risk.level.value, risk.confidence, effective_acute_threshold, cooldown,
            )
        elif risk.level == RiskLevel.ELEVATED and risk.confidence < effective_elevated_threshold:
            risk.level = RiskLevel.LOW
            risk.action_taken = "monitor"
            logger.debug(
                "[treatment] ELEVATED downgraded to LOW (conf %.2f < %.2f, cooldown=%d)",
                risk.confidence, effective_elevated_threshold, cooldown,
            )

        self.repo.insert_risk_assessment(risk)
        return risk

    # ─────────────────── MBC ────────────────────────────────────────────
    def score_mbc_response(
        self, *, user_id: str, session_id: str, item: MBCItem,
        response_text: str,
    ) -> Optional[MBCObservation]:
        obs = self.mbc.score(
            user_id=user_id, session_id=session_id,
            item=item, response_text=response_text,
        )
        if obs is not None:
            self.repo.insert_mbc(obs)
        return obs

    def latest_well_being(self, user_id: str) -> List[MBCObservation]:
        return self.repo.latest_mbc_per_item(user_id, "WHO5-like")

    # ─────────────────── user feedback ────────────────────────────────────
    def pause_risk_assessment(
        self,
        session_id: str,
        turns: int = 10,
    ) -> None:
        """Pause risk assessment for *turns* turns (user feedback: \"lass das\").

        This sets ``risk_paused_until_turn`` in the safety_episodes table.
        ACUTE risk always breaks through regardless of pause state.
        """
        current_turn = self._current_turn or 0
        pause_until = current_turn + turns
        self.repo.pause_risk_assessment(session_id, pause_until)
        logger.info(
            "[treatment] risk assessment paused for %d turns (until %d) "
            "for session %s",
            turns, pause_until, session_id[:12],
        )

    # ─────────────────── reviewer ───────────────────────────────────────
    def maybe_review(
        self,
        *,
        user_id: str,
        session_id: str,
        turn_idx: int,
        user_message: str,
        draft: str,
    ) -> Optional[ReviewVerdict]:
        risk = self.repo.latest_risk(session_id)
        focus = self.repo.get_focus(session_id)
        primary_goal = self.repo.get_goal(focus.primary_goal_id) if (
            focus and focus.primary_goal_id) else None
        stage = self.repo.latest_stage(user_id)
        # For now we don't track stage transitions across turns explicitly;
        # use a simple cadence + risk gate.
        if not self.reviewer.should_review(
            risk, stage_changed=False, turn_idx=turn_idx,
            every_n_turns=self.REVIEW_EVERY,
        ):
            return None
        return self.reviewer.review(
            user_message=user_message, draft=draft,
            primary_goal=primary_goal, focus=focus,
            stage=stage, risk=risk,
        )

    # ─────────────────── prompt context bundle ──────────────────────────
    def build_context(
        self, *, user_id: str, session_id: str,
    ) -> TreatmentContext:
        plan = self.repo.get_active_plan(user_id)
        if plan is None:
            return TreatmentContext(plan=None, formulation=None)

        formulation = self.repo.latest_formulation(user_id)
        assert plan.id is not None
        actives = self.repo.list_goals(plan.id, statuses=[GoalStatus.ACTIVE])

        focus = self.repo.get_focus(session_id)
        previous_focus = self.repo.latest_focus_for_user(user_id)
        if previous_focus and focus and previous_focus.session_id == session_id:
            # don't show 'previous' that is actually the current session
            # — fall back to whatever is older
            previous_focus = None

        primary_goal = None
        secondary_goals: List[PlanGoal] = []
        if focus:
            if focus.primary_goal_id:
                primary_goal = self.repo.get_goal(focus.primary_goal_id)
            for sgid in focus.secondary_goal_ids[:2]:
                g = self.repo.get_goal(sgid)
                if g:
                    secondary_goals.append(g)

        return TreatmentContext(
            plan=plan,
            formulation=formulation,
            active_goals=actives,
            primary_goal=primary_goal,
            secondary_goals=secondary_goals,
            focus=focus,
            previous_focus=previous_focus,
            stage=self.repo.latest_stage(user_id),
            latest_risk=self.repo.latest_risk(session_id),
            latest_mbc=self.repo.latest_mbc_per_item(user_id, "WHO5-like"),
        )

    def set_focus_mode(self, session_id: str, mode: str) -> bool:
        """Apply an explicit user decision to the current session focus."""
        return self.repo.set_focus_mode(session_id, mode)

    def select_session_focus(self, session_id: str, goal_id: int) -> bool:
        """Make one active goal the explicit user-confirmed session focus."""
        return self.repo.select_session_focus(session_id, goal_id)

    # ─────────────────── internal helpers ───────────────────────────────
    def _apply_match_decision(
        self, *, plan_id: int, proposal: Dict[str, Any],
        decision: MatchDecision,
    ) -> Optional[PlanGoal]:
        """Translate a matcher decision into the right write."""
        if decision.relation == "same" and decision.matched_goal_id:
            # Existing goal already covers this — nothing to insert.
            logger.debug("[treatment] proposal merged into goal %s",
                         decision.matched_goal_id)
            return None

        parent_id: Optional[int] = None
        if decision.relation == "subgoal_of" and decision.matched_goal_id:
            parent_id = decision.matched_goal_id
        # supergoal_of: we do not auto-rewrite the existing hierarchy here;
        # a future revision pass can do that. We insert as a sibling.

        embedding = embed_goal_text(proposal["title"])
        goal = PlanGoal(
            id=None, plan_id=plan_id, parent_goal_id=parent_id,
            title=proposal["title"],
            rationale=proposal.get("rationale", ""),
            target_metric=proposal.get("target_metric", ""),
            target_value=1.0,
            status=GoalStatus.ACTIVE,
            priority=int(proposal.get("priority", 3)),
            confidence=float(proposal.get("confidence", 0.5)),
            embedding=embedding,
        )
        goal.id = self.repo.insert_goal(goal)
        return goal


__all__ = [
    "CarePlanManager",
    "TreatmentContext",
    "TurnResult",
    "WHO5_LIKE",
]
