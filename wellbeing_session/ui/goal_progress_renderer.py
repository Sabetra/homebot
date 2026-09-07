"""
Goal progress visualization for the active psychological session.

This module keeps data assembly and UI rendering separated:
- GoalProgressDataBuilder: resolves user/session scoped treatment data
- GoalProgressRenderer: renders progress cards with confidence and evidence
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional

import streamlit as st
from i18n import t as i18n_t

from wellbeing_session.ui.goal_renderer import GoalUIRenderer


def _tr(key: str, default: str, **kwargs: Any) -> str:
    translated = i18n_t(key, **kwargs)
    if translated == key:
        if kwargs:
            try:
                return default.format(**kwargs)
            except Exception:
                return default
        return default
    return translated


@dataclass(frozen=True)
class GoalProgressViewModel:
    """UI-ready goal progress payload for one goal."""

    goal_id: int
    title: str
    status: str
    priority: int
    target_metric: str
    target_value: Optional[float]
    progress_score: Optional[float]
    confidence: Optional[float]
    delta: Optional[float]
    evidence: List[str]
    updated_at: Optional[str]
    source: str
    plan_status: Optional[str]


class GoalProgressDataBuilder:
    """Builds goal progress view models for the active user/session."""

    def __init__(self, session_manager: Any) -> None:
        self.session_manager = session_manager

    def build_for_session(self, session_id: str, current_user_name: str) -> List[GoalProgressViewModel]:
        """
        Build progress data for the currently active session owner only.

        Enforces privacy by verifying that session owner and current active user
        resolve to the same canonical user id.
        """
        if not session_id:
            return []

        session_summary = self.session_manager.get_session_summary(session_id)
        summary_user_id = str((session_summary or {}).get("user_id") or "").strip()
        state_user_id = str(st.session_state.get("wellbeing_current_user_id") or "").strip()
        resolved_user_id = state_user_id
        if not resolved_user_id:
            resolved_user_id = str(self.session_manager.resolve_user_id(current_user_name)).strip()

        # Hard privacy guard: never render goals for a different session owner.
        if summary_user_id and resolved_user_id and summary_user_id != resolved_user_id:
            return []

        active_user_id = resolved_user_id or summary_user_id
        if not active_user_id:
            return []

        manager = getattr(self.session_manager, "manager", None)
        treatment_manager = getattr(manager, "treatment_manager", None)
        if treatment_manager is None:
            return []

        repo = treatment_manager.repo
        plans = repo.list_plans(active_user_id)
        output: List[GoalProgressViewModel] = []

        for plan in plans:
            if plan.id is None:
                continue

            goals = repo.list_goals(plan.id)
            for goal in goals:
                if goal.id is None:
                    continue

                updates = repo.goal_trajectory(goal.id, limit=25)
                display_update = self._pick_display_update(updates, session_id)
                progress_score = display_update.progress_score if display_update else goal.last_progress_score
                confidence = display_update.confidence if display_update else None
                delta = self._resolve_delta(updates, display_update)
                evidence = display_update.evidence if display_update else []
                updated_at = display_update.created_at if display_update else goal.created_at

                status_value = goal.status.value if hasattr(goal.status, "value") else str(goal.status)
                plan_status_value = plan.status.value if hasattr(plan.status, "value") else str(plan.status)

                output.append(
                    GoalProgressViewModel(
                        goal_id=int(goal.id),
                        title=str(goal.title),
                        status=status_value,
                        priority=int(goal.priority),
                        target_metric=str(goal.target_metric or ""),
                        target_value=goal.target_value,
                        progress_score=progress_score,
                        confidence=confidence,
                        delta=delta,
                        evidence=[str(item) for item in evidence],
                        updated_at=updated_at,
                        source="treatment",
                        plan_status=plan_status_value,
                    )
                )

        if output:
            output.sort(key=lambda item: self._sort_key(item.updated_at), reverse=True)
            return output

        return self._build_from_insights(active_user_id, manager)

    def _build_from_insights(self, user_id: str, manager: Any) -> List[GoalProgressViewModel]:
        """Fallback read model: derive goal timeline from structured psychological insights."""
        db = getattr(manager, "db", None)
        if db is None or not hasattr(db, "get_user_insights"):
            return []

        insights = db.get_user_insights(user_id=user_id, session_id=None, limit=250)
        if not insights:
            return []

        output: List[GoalProgressViewModel] = []
        temporal_to_status: Dict[str, str] = {
            "past": "achieved",
            "historical": "achieved",
            "resolved": "achieved",
            "current": "active",
            "present": "active",
            "ongoing": "active",
            "developing": "active",
            "future": "proposed",
            "planned": "proposed",
        }

        for insight in insights:
            category = str(insight.get("category") or "").strip().lower()
            if category != "goals":
                continue

            insight_id = int(insight.get("insight_id") or 0)
            if insight_id <= 0:
                continue

            temporal_context = str(insight.get("temporal_context") or "").strip().lower()
            status_value = temporal_to_status.get(temporal_context, "proposed")

            description = str(
                insight.get("description")
                or insight.get("value")
                or _tr("wellbeing_ui.goal_progress.default_goal_title", "Ziel #{id}", id=insight_id)
            )
            evidence = insight.get("evidence") or []
            if not isinstance(evidence, list):
                evidence = []

            raw_confidence = insight.get("confidence")
            confidence_value = float(raw_confidence) if raw_confidence is not None else None

            output.append(
                GoalProgressViewModel(
                    goal_id=-insight_id,
                    title=description,
                    status=status_value,
                    priority=3,
                    target_metric=str(insight.get("insight_type") or _tr("wellbeing_ui.goal_progress.default_metric", "Insight")),
                    target_value=None,
                    # Use confidence as the progress proxy for insight-derived goals.
                    progress_score=confidence_value,
                    confidence=confidence_value,
                    delta=None,
                    evidence=[str(item) for item in evidence],
                    updated_at=str(insight.get("created_at") or ""),
                    source="insight",
                    plan_status=None,
                )
            )

        output.sort(key=lambda item: self._sort_key(item.updated_at), reverse=True)
        return output

    @staticmethod
    def _sort_key(raw_value: Optional[str]) -> datetime:
        if not raw_value:
            return datetime.min
        parsed = GoalProgressDataBuilder._try_parse_iso(str(raw_value))
        return parsed if parsed is not None else datetime.min

    @staticmethod
    def _try_parse_iso(value: str) -> Optional[datetime]:
        if not value:
            return None
        normalized = value.strip().replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(normalized)
        except ValueError:
            return None

    @staticmethod
    def _pick_display_update(updates: List[Any], session_id: str) -> Optional[Any]:
        if not updates:
            return None
        for update in updates:
            if str(getattr(update, "session_id", "")) == session_id:
                return update
        return updates[0]

    @staticmethod
    def _resolve_delta(updates: List[Any], display_update: Optional[Any]) -> Optional[float]:
        if display_update is None:
            return None

        explicit_delta = getattr(display_update, "delta", None)
        if explicit_delta is not None:
            return float(explicit_delta)

        try:
            idx = updates.index(display_update)
        except ValueError:
            return None

        if idx + 1 >= len(updates):
            return None

        current = float(getattr(display_update, "progress_score", 0.0) or 0.0)
        previous = float(getattr(updates[idx + 1], "progress_score", 0.0) or 0.0)
        return current - previous


class GoalProgressRenderer:
    """Renders goal progress cards in the active session interface."""

    RUNTIME_VERSION = 2

    def __init__(self, session_manager: Any) -> None:
        self._session_manager = session_manager
        self._builder = GoalProgressDataBuilder(session_manager)

    def render(self, session_id: str, current_user_name: str) -> None:
        goals = self._builder.build_for_session(session_id=session_id, current_user_name=current_user_name)

        if not goals:
            st.caption(_tr("wellbeing_ui.goal_progress.empty", "Keine Ziele im Treatment- oder Insight-Verlauf fur diese Session verfuegbar."))
            return

        st.markdown(_tr("wellbeing_ui.goal_progress.header", "### 🎯 Ziel-Verlauf & Fortschritt"))
        st.caption(_tr("wellbeing_ui.goal_progress.source_caption", "Nur fuer den aktuell aktiven Session-Nutzer sichtbar. Quelle: Treatment-Plan + Insights."))

        self._render_focus_controls(session_id)

        for goal in goals:
            self._render_goal_card(goal, session_id)

    def _treatment_manager(self) -> Optional[Any]:
        manager = getattr(self._session_manager, "manager", None) or self._session_manager
        return getattr(manager, "treatment_manager", None)

    def _render_focus_controls(self, session_id: str) -> None:
        treatment_manager = self._treatment_manager()
        if treatment_manager is None:
            return
        focus = treatment_manager.repo.get_focus(session_id)
        if focus is None or focus.primary_goal_id is None or focus.focus_mode == "dismissed":
            return
        goal = treatment_manager.repo.get_goal(focus.primary_goal_id)
        if goal is None:
            return

        st.caption(
            _tr(
                "wellbeing_ui.goal_progress.focus_value",
                "Vorgeschlagener Sitzungsfokus: {value}",
                value=goal.title,
            )
        )
        if focus.focus_mode == "suggested":
            confirm, dismiss = st.columns(2)
            if confirm.button(
                _tr("wellbeing_ui.goal_progress.focus_confirm", "✓ Als Fokus verwenden"),
                key=f"focus-confirm-{session_id}",
                use_container_width=True,
            ):
                treatment_manager.set_focus_mode(session_id, "confirmed")
                st.rerun()
            if dismiss.button(
                _tr("wellbeing_ui.goal_progress.focus_later", "× Später"),
                key=f"focus-dismiss-{session_id}",
                use_container_width=True,
            ):
                treatment_manager.set_focus_mode(session_id, "dismissed")
                st.rerun()
        elif focus.focus_mode == "confirmed":
            if st.button(
                _tr("wellbeing_ui.goal_progress.focus_pause", "Ⅱ Fokus pausieren"),
                key=f"focus-pause-{session_id}",
            ):
                treatment_manager.set_focus_mode(session_id, "paused")
                st.rerun()
        elif focus.focus_mode == "paused":
            if st.button(
                _tr("wellbeing_ui.goal_progress.focus_resume", "▶ Fokus fortsetzen"),
                key=f"focus-resume-{session_id}",
            ):
                treatment_manager.set_focus_mode(session_id, "confirmed")
                st.rerun()

    def _render_goal_card(self, goal: GoalProgressViewModel, session_id: str) -> None:
        render_info = GoalUIRenderer.get_render_info(goal.status, goal.progress_score)

        with st.container(border=True):
            left, mid, right = st.columns([3, 1, 1])

            with left:
                st.markdown(f"{render_info.emoji} **{goal.title}**")
                status_line = _tr("wellbeing_ui.goal_progress.status", "Status: {value}", value=render_info.label)
                if goal.target_metric:
                    status_line += _tr("wellbeing_ui.goal_progress.target_metric", " | Zielmetrik: {value}", value=goal.target_metric)
                if goal.plan_status:
                    status_line += _tr("wellbeing_ui.goal_progress.plan", " | Plan: {value}", value=goal.plan_status)
                status_line += _tr("wellbeing_ui.goal_progress.source", " | Quelle: {value}", value=goal.source)
                st.caption(status_line)

            with mid:
                if goal.progress_score is None:
                    st.metric(_tr("wellbeing_ui.goal_progress.metric_progress", "Fortschritt"), "-", help=_tr("wellbeing_ui.goal_progress.no_score_help", "Noch keine Bewertung fuer dieses Ziel vorhanden"))
                else:
                    help_text = (
                        _tr("wellbeing_ui.goal_progress.insight_help", "Erkennungssicherheit des Ziels aus Insight-Analyse")
                        if goal.source == "insight"
                        else None
                    )
                    st.metric(
                        _tr("wellbeing_ui.goal_progress.metric_progress", "Fortschritt"),
                        f"{goal.progress_score * 100:.0f}%",
                        delta=self._format_delta(goal.delta),
                        help=help_text,
                    )

            with right:
                if goal.confidence is None:
                    st.metric(_tr("wellbeing_ui.goal_progress.metric_confidence", "Sicherheit"), "-")
                else:
                    st.metric(_tr("wellbeing_ui.goal_progress.metric_confidence", "Sicherheit"), f"{goal.confidence * 100:.0f}%")

            if goal.progress_score is not None:
                bounded = min(max(float(goal.progress_score), 0.0), 1.0)
                st.progress(bounded)

            if goal.updated_at:
                st.caption(_tr("wellbeing_ui.goal_progress.updated_at", "Letzte Aktualisierung: {value}", value=self._format_timestamp(goal.updated_at)))

            if goal.evidence:
                with st.expander(_tr("wellbeing_ui.goal_progress.evidence_expander", "Evidenz aus der Sitzung anzeigen")):
                    for item in goal.evidence:
                        st.markdown(f"- {item}")

            treatment_manager = self._treatment_manager()
            if treatment_manager is not None and goal.source == "treatment" and goal.goal_id > 0:
                focus = treatment_manager.repo.get_focus(session_id)
                is_current = bool(
                    focus
                    and focus.primary_goal_id == goal.goal_id
                    and focus.focus_mode == "confirmed"
                )
                if not is_current and st.button(
                    _tr("wellbeing_ui.goal_progress.focus_choose", "◎ Als Fokus wählen"),
                    key=f"focus-choose-{session_id}-{goal.goal_id}",
                ):
                    treatment_manager.select_session_focus(session_id, goal.goal_id)
                    st.rerun()

    @staticmethod
    def _format_delta(delta: Optional[float]) -> Optional[str]:
        if delta is None:
            return None
        if abs(delta) < 0.005:
            return "0 pp"
        return f"{delta * 100:+.1f} pp"

    @staticmethod
    def _format_timestamp(raw_value: str) -> str:
        parsed = GoalProgressRenderer._try_parse_iso(raw_value)
        if parsed is None:
            return str(raw_value)
        return parsed.strftime("%d.%m.%Y %H:%M")

    @staticmethod
    def _try_parse_iso(value: str) -> Optional[datetime]:
        if not value:
            return None
        normalized = value.strip().replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(normalized)
        except ValueError:
            return None

