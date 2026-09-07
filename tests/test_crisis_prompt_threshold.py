"""Test: crisis_indicators-Schwelle im Emotional-Analyzer-Prompt.

Stellt sicher, dass der Analyse-Prompt nur bei expliziter, unmittelbarer
Gefahr Krisen-Flags erlaubt und normale emotionale Belastung ausschließt.

Hypothese H1: Der Prompt enthält klare Kriterien, die "Hoffnungslosigkeit"
und "Hilflosigkeit" explizit als KEINE Krisenindikatoren benennt.
"""

import pytest
from emotional_analyzer import LLMEmotionalAnalyzer
from wellbeing.wellbeing_db import WellbeingDatabase
from wellbeing.care_plans.models import RiskLevel
from wellbeing.care_plans.repository import CarePlanRepository
from wellbeing.care_plans.risk_classifier import RiskClassifier, _RISK_PROMPT


class TestCrisisPromptThreshold:
    """Prompt-Inhalt testen — keine LLM nötig."""

    @pytest.fixture
    def analyzer(self):
        return LLMEmotionalAnalyzer()

    def test_prompt_excludes_hopelessness_as_crisis(self, analyzer):
        """Allgemeine Hoffnungslosigkeit darf kein Krisen-Flag sein."""
        prompt = analyzer._create_analysis_prompt("Test-Nachricht")
        assert "Hoffnungslosigkeit" in prompt
        assert "KEINE Krisenindikatoren" in prompt

    def test_prompt_excludes_helplessness_as_crisis(self, analyzer):
        """Hilflosigkeit darf kein Krisen-Flag sein."""
        prompt = analyzer._create_analysis_prompt("Test-Nachricht")
        assert "Hilflosigkeit" in prompt

    def test_prompt_requires_explicit_immediate_danger(self, analyzer):
        """Prompt muss explizite, unmittelbare Gefahr verlangen."""
        prompt = analyzer._create_analysis_prompt("Test-Nachricht")
        assert "expliziter" in prompt
        assert "unmittelbarer Gefahr" in prompt

    def test_prompt_mentions_suicide_intent(self, analyzer):
        """Prompt muss Suizidabsicht als echtes Krisenkriterium nennen."""
        prompt = analyzer._create_analysis_prompt("Test-Nachricht")
        assert "Suizidabsicht" in prompt

    def test_prompt_mentions_self_harm(self, analyzer):
        """Prompt muss Selbstverletzung als echtes Krisenkriterium nennen."""
        prompt = analyzer._create_analysis_prompt("Test-Nachricht")
        assert "Selbstverletzung" in prompt

    def test_prompt_refs_risk_classifier_for_moderate_cases(self, analyzer):
        """Prompt muss verweisen, dass moderate Fälle über RiskClassifier laufen."""
        prompt = analyzer._create_analysis_prompt("Test-Nachricht")
        assert "RiskClassifier" in prompt

    def test_fallback_analysis_returns_no_crisis(self, analyzer):
        """Fallback-Analyse darf niemals Krisen-Flag setzen."""
        result = analyzer._fallback_analysis("Ich fühle mich hilflos.")
        assert result.crisis_indicators is False

    def test_parse_default_returns_no_crisis(self, analyzer):
        """Default-Parse-Ergebnis darf kein Krisen-Flag sein."""
        result = analyzer._parse_llm_response("INVALID JSON {{{")
        assert result.get("crisis_indicators") is False


class TestCrisisPromptWithSessionContext:
    """Prompt mit Session-Kontext testen."""

    @pytest.fixture
    def analyzer(self):
        return LLMEmotionalAnalyzer()

    def test_prompt_with_context_preserves_crisis_criteria(self, analyzer):
        """Kontext darf Krisen-Kriterien nicht verwässern."""
        ctx = [
            "Ich hatte einen schlechten Tag.",
            "Die Arbeit war stressig.",
            "Ich brauche jemanden zum Reden."
        ]
        prompt = analyzer._create_analysis_prompt(
            "Ich fühle mich etwas hoffnungslos.",
            session_context=ctx
        )
        assert "KEINE Krisenindikatoren" in prompt
        assert "Hoffnungslosigkeit" in prompt


def test_safety_episode_probes_once_but_never_suppresses_acute(tmp_path):
    db = WellbeingDatabase(db_path=str(tmp_path / "psych.db"))
    repo = CarePlanRepository(db)

    actions = [
        repo.transition_safety_episode("session-a", 1, RiskLevel.ELEVATED),
        repo.transition_safety_episode("session-a", 2, RiskLevel.ELEVATED),
        repo.transition_safety_episode("session-a", 3, RiskLevel.LOW),
        repo.transition_safety_episode("session-a", 4, RiskLevel.ELEVATED),
        repo.transition_safety_episode("session-a", 5, RiskLevel.ACUTE),
    ]

    # Probe nur beim ersten ELEVATED, danach normal (Cooldown + längeres Reprobe-Intervall)
    assert actions == ["probe", "normal", "normal", "normal", "acute"]

    with db.get_connection() as conn:
        episode = conn.execute(
            """
            SELECT state, last_risk_level, probe_sent_at_turn
            FROM safety_episodes WHERE session_id = ?
            """,
            ("session-a",),
        ).fetchone()

    assert tuple(episode) == ("acute_active", "acute", 1)


def test_previous_risk_cannot_independently_escalate_current_turn():
    assert "AKTUELLE NACHRICHT" in _RISK_PROMPT
    assert "darf allein weder" in _RISK_PROMPT
    assert "Themenwechsel" in _RISK_PROMPT


@pytest.mark.parametrize(
    ("raw_level", "confidence", "expected"),
    [
        ("acute", 0.79, RiskLevel.ELEVATED),
        ("acute", 0.59, RiskLevel.LOW),
        ("elevated", 0.64, RiskLevel.LOW),
        ("acute", 0.9, RiskLevel.ACUTE),
        ("elevated", 0.8, RiskLevel.ELEVATED),
    ],
)
def test_uncertain_risk_classification_is_downgraded(
    monkeypatch,
    raw_level,
    confidence,
    expected,
):
    monkeypatch.setattr(
        "wellbeing.care_plans.risk_classifier.call_llm_json",
        lambda *args, **kwargs: {
            "level": raw_level,
            "confidence": confidence,
            "drivers": [],
            "protective_factors": [],
            "rationale": "test",
        },
    )

    result = RiskClassifier(lambda *args, **kwargs: "{}").assess(
        session_id="session-a",
        turn_idx=2,
        user_message="Aktuelle Nachricht",
        previous_level=RiskLevel.ACUTE,
    )

    assert result.level == expected


if __name__ == "__main__":
    pytest.main([__file__, "-v"])