from __future__ import annotations

import pytest

from wellbeing_session.context.context_formatter import ContextFormatter
from wellbeing_session.context.insight_extractor import UserInsightExtractor
from wellbeing.wellbeing_db import WellbeingDatabase
from wellbeing.care_plans.models import (
    GoalStatus,
    PlanGoal,
    PlanStatus,
    SessionFocus,
    CarePlan,
)
from wellbeing.care_plans.repository import CarePlanRepository
from user_context_builder.models import UserContextRequest
from user_context_builder.providers.care_goals import CareGoalsProvider


class _DummyChatLogic:
    class _Loader:
        def generate_response(self, *args, **kwargs):
            return '{"insights": [{"insight": "Test insight", "type": "theme", "confidence": 0.8}]}'

    model_loader = _Loader()


def test_formatter_supports_previous_sessions_alias():
    formatter = ContextFormatter()

    rendered = formatter.format_context_for_llm(
        {
            "knowledge_graph": [],
            "previous_sessions": [
                {
                    "date": "2026-05-26T10:00:00",
                    "summary": "Vorherige Sitzung mit Fokus auf Selbstregulation.",
                }
            ],
            "mood_progression": None,
            "care_goals": [],
            "user_insights": [],
            "persistent_profile": None,
        }
    )

    assert "VORHERIGE SESSIONS" in rendered
    assert "Selbstregulation" in rendered


def test_care_goals_distinguish_active_anchor_from_achieved_progress():
    formatter = ContextFormatter()

    rendered = formatter.format_context_for_llm(
        {
            "care_goals": [
                {"goal": "Grenzen kommunizieren", "status": "active", "progress": 0.4},
                {"goal": "Morgenroutine etablieren", "status": "achieved", "progress": 1.0},
            ]
        }
    )

    assert "[Aktiv] Grenzen kommunizieren" in rendered
    assert "[Erreicht] Morgenroutine etablieren" in rendered
    assert "erreichte Ziele dienen nur als Fortschrittskontext" in rendered


def test_unconfirmed_or_irrelevant_focus_does_not_steer_current_topic():
    formatter = ContextFormatter()
    treatment_plan = {
        "plan": {"id": 1},
        "primary_goal": {"id": 7, "title": "Arbeitsstress reduzieren", "status": "active"},
        "secondary_goals": [],
        "active_goals": [{"id": 7, "title": "Arbeitsstress reduzieren", "status": "active"}],
        "focus": {
            "mode": "confirmed",
            "planned_steps": ["Arbeitsbelastung priorisieren"],
        },
    }

    unrelated = formatter.format_context_for_llm(
        {
            "current_user_input": "Heute beschäftigt mich der Streit mit meiner Schwester",
            "treatment_plan": treatment_plan,
            "care_goals": [
                {"goal": "Arbeitsstress reduzieren", "status": "active"}
            ],
        }
    )
    related = formatter.format_context_for_llm(
        {
            "current_user_input": "Mein Arbeitsstress ist heute wieder sehr hoch",
            "treatment_plan": treatment_plan,
            "care_goals": [
                {"goal": "Arbeitsstress reduzieren", "status": "active"}
            ],
        }
    )

    assert "Arbeitsstress reduzieren" not in unrelated
    assert "Arbeitsbelastung priorisieren" not in unrelated
    assert unrelated.count("CARE ZIELE") == 0
    assert "Arbeitsstress reduzieren" in related
    assert "Arbeitsbelastung priorisieren" in related


def test_suggested_focus_never_enters_prompt():
    formatter = ContextFormatter()
    rendered = formatter.format_context_for_llm(
        {
            "current_user_input": "Arbeitsstress",
            "treatment_plan": {
                "plan": {"id": 1},
                "primary_goal": {"id": 7, "title": "Arbeitsstress reduzieren", "status": "active"},
                "active_goals": [],
                "focus": {"mode": "suggested", "planned_steps": ["Priorisieren"]},
            },
        }
    )

    assert "Arbeitsstress reduzieren" not in rendered
    assert "Priorisieren" not in rendered


def test_session_focus_requires_explicit_user_confirmation(tmp_path):
    db = WellbeingDatabase(db_path=str(tmp_path / "psych.db"))
    repo = CarePlanRepository(db)
    plan = CarePlan(
        id=None,
        user_id="user-a",
        status=PlanStatus.ACTIVE,
        version=1,
        formulation_id=None,
    )
    plan.id = repo.create_plan(plan)
    goal = PlanGoal(
        id=None,
        plan_id=plan.id,
        parent_goal_id=None,
        title="Arbeitsstress reduzieren",
        rationale="Vom User genannt",
        target_metric="Belastung",
        target_value=1.0,
        status=GoalStatus.ACTIVE,
        priority=1,
        confidence=0.9,
    )
    goal.id = repo.insert_goal(goal)
    repo.upsert_focus(
        SessionFocus(
            id=None,
            session_id="session-a",
            plan_id=plan.id,
            primary_goal_id=goal.id,
            secondary_goal_ids=[],
            planned_steps=["Priorisieren"],
            carry_forward_from_session_id=None,
        )
    )

    assert repo.get_focus("session-a").focus_mode == "suggested"
    assert repo.set_focus_mode("session-a", "confirmed")
    assert repo.get_focus("session-a").focus_mode == "confirmed"
    assert repo.set_focus_mode("session-a", "paused")
    assert repo.get_focus("session-a").focus_mode == "paused"
    assert repo.select_session_focus("session-a", goal.id)
    selected = repo.get_focus("session-a")
    assert selected.focus_mode == "confirmed"
    assert selected.primary_goal_id == goal.id


def test_care_goal_provider_prioritizes_active_over_achieved():
    from wellbeing.care_plans.models import GoalStatus, PlanGoal

    class _Repo:
        def get_active_plan(self, user_id):
            return type("Plan", (), {"id": 7})()

        def list_goals(self, plan_id, statuses):
            assert statuses == [GoalStatus.ACTIVE, GoalStatus.ACHIEVED]
            return [
                PlanGoal(
                    id=1,
                    plan_id=plan_id,
                    parent_goal_id=None,
                    title="Erreichtes Ziel",
                    rationale="",
                    target_metric="",
                    target_value=1.0,
                    status=GoalStatus.ACHIEVED,
                    priority=1,
                    confidence=1.0,
                    last_progress_score=1.0,
                ),
                PlanGoal(
                    id=2,
                    plan_id=plan_id,
                    parent_goal_id=None,
                    title="Aktives Ziel",
                    rationale="",
                    target_metric="",
                    target_value=1.0,
                    status=GoalStatus.ACTIVE,
                    priority=5,
                    confidence=1.0,
                    last_progress_score=0.2,
                ),
            ]

    manager = type("Manager", (), {"repo": _Repo()})()
    session_manager = type("SessionManager", (), {"treatment_manager": manager})()
    request = UserContextRequest(
        user_id="user-a",
        current_session_id="session-a",
        user_input="Heute",
    )

    result = CareGoalsProvider(max_goals=1).provide(request, session_manager)

    assert result is not None
    assert [goal["goal"] for goal in result.goals] == ["Aktives Ziel"]


def test_goal_progress_fallback_uses_score_based_ranking(monkeypatch):
    formatter = ContextFormatter(chat_logic=None)
    monkeypatch.setattr("utils.embedding_singleton.get_embedding_model", lambda: None)

    triples = [
        {
            "subject": "u",
            "predicate": "arbeitet_an",
            "object": "Ziel A",
            "confidence": 0.9,
            "similarity": 0.9,
            "combined_score": 0.8,
        },
        {
            "subject": "u",
            "predicate": "spricht_ueber",
            "object": "Ziel B",
            "confidence": 0.2,
            "similarity": 0.2,
            "combined_score": 0.2,
        },
    ]

    ranked = formatter.get_goal_progress_triples("beliebiges ziel", triples)

    assert ranked
    assert ranked[0]["object"] == "Ziel A"
    assert "goal_match_score" in ranked[0]


def test_insight_extractor_fallback_returns_structural_insights():
    extractor = UserInsightExtractor(chat_logic=None)

    triples = [
        {"subject": "arbeit", "predicate": "beeinflusst", "object": "stress", "confidence": 0.8},
        {"subject": "arbeit", "predicate": "beeinflusst", "object": "schlaf", "confidence": 0.7},
        {"subject": "familie", "predicate": "unterstuetzt", "object": "stabilitaet", "confidence": 0.9},
    ]

    insights = extractor.extract_user_insights_fallback(triples)

    assert insights
    assert any(i.get("type") == "meta" for i in insights)


def test_insight_extractor_parses_object_payload_without_regex():
    extractor = UserInsightExtractor(chat_logic=None)

    response = '{"insights": [{"insight": "Muster", "type": "theme", "confidence": 0.75}]}'
    parsed = extractor._try_legacy_parse(response, triples=[])

    assert parsed
    assert parsed[0]["insight"] == "Muster"


def test_insight_extractor_propagates_runtime_error():
    extractor = UserInsightExtractor(chat_logic=_DummyChatLogic())

    def _raise_runtime(*args, **kwargs):
        raise RuntimeError("critical-model-failure")

    extractor._extract_via_llm = _raise_runtime  # type: ignore[assignment]

    with pytest.raises(RuntimeError, match="critical-model-failure"):
        extractor.extract_user_insights(
            triples=[{"subject": "a"}, {"subject": "b"}, {"subject": "c"}],
            user_input="x",
        )
