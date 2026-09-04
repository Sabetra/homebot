from datetime import datetime, timezone

from user_context_builder.builder import (
    _normalized_tokens,
    _selection_score,
    _select_hybrid_top_n,
)


def _insight(
    value: str,
    *,
    insight_type: str = "emotional_state",
    category: str = "current_state",
    confidence: float = 0.8,
    mention_count: int = 1,
    last_seen_at: str = "2026-08-01T00:00:00+00:00",
):
    return {
        "type": insight_type,
        "category": category,
        "value": value,
        "confidence": confidence,
        "mention_count": mention_count,
        "last_seen_at": last_seen_at,
    }


def test_selection_is_query_conditioned_after_coverage():
    insights = [
        _insight("Konflikte mit der Schwester", category="relationships"),
        _insight("Stress durch die Arbeit", category="goals"),
        _insight("Unsicherheit bei Entscheidungen", category="fears"),
    ]

    selected = _select_hybrid_top_n(insights, "Der Stress bei der Arbeit belastet mich", n=2)

    assert selected[0]["category"] == "relationships"
    assert selected[1]["value"] == "Stress durch die Arbeit"


def test_selection_preserves_mention_strength():
    insights = [
        _insight("Einmalige Beobachtung", category="goals", confidence=0.9),
        _insight("Wiederkehrendes Muster", category="goals", confidence=0.72, mention_count=9),
    ]

    selected = _select_hybrid_top_n(insights, None, n=1)

    assert selected[0]["value"] == "Wiederkehrendes Muster"


def test_selection_deduplicates_conservative_paraphrases():
    insights = [
        _insight("Ich bin häufig gestresst", confidence=0.9, mention_count=4),
        _insight("Ich fühle mich oft gestresst", confidence=0.8, mention_count=2),
        _insight("Ich schlafe schlecht", insight_type="behavioral_pattern", category="goals"),
    ]

    selected = _select_hybrid_top_n(insights, "Stress und Schlaf", n=8)
    values = {item["value"] for item in selected}

    assert len(values & {"Ich bin häufig gestresst", "Ich fühle mich oft gestresst"}) == 1
    assert "Ich schlafe schlecht" in values


def test_selection_preserves_negated_contradiction():
    insights = [
        _insight("Ich bin häufig gestresst", confidence=0.9),
        _insight("Ich bin nicht häufig gestresst", confidence=0.8),
    ]

    selected = _select_hybrid_top_n(insights, "Stress", n=8)

    assert [item["value"] for item in selected] == [
        "Ich bin häufig gestresst",
        "Ich bin nicht häufig gestresst",
    ]


def test_selection_applies_type_dependent_recency_at_fixed_time():
    insights = [
        _insight(
            "Alter aktueller Zustand",
            insight_type="emotional_state",
            category="goals",
            confidence=0.9,
            last_seen_at="2025-08-01T00:00:00+00:00",
        ),
        _insight(
            "Altes stabiles Merkmal",
            insight_type="personality_trait",
            category="goals",
            confidence=0.6,
            last_seen_at="2025-08-01T00:00:00+00:00",
        ),
    ]

    now = datetime(2026, 8, 1, tzinfo=timezone.utc)
    scores = [
        _selection_score(insight, _normalized_tokens(None), now)
        for insight in insights
    ]

    assert scores[1] > scores[0]


def test_selection_enforces_hard_limit():
    insights = [
        _insight(f"Insight number{index:02d}", category=f"category-{index}")
        for index in range(12)
    ]

    selected = _select_hybrid_top_n(insights, None)

    assert len(selected) == 8


def test_selection_covers_available_insight_types():
    insights = [
        _insight("Hoher Stress", insight_type="emotional_state", category="current_state"),
        _insight("Vermeidet Konflikte", insight_type="behavioral_pattern", category="goals"),
        _insight("Atmung hilft", insight_type="coping_mechanism", category="goals"),
    ]

    selected = _select_hybrid_top_n(insights, None, n=3)

    assert {item["type"] for item in selected} == {
        "emotional_state",
        "behavioral_pattern",
        "coping_mechanism",
    }


def test_selection_is_stable_for_equal_scores():
    insights = [
        _insight("Kernmerkmal", insight_type="personality_trait", category="core_personality"),
        _insight("Aktueller Zustand", category="current_state"),
        _insight("Beziehungsmuster", insight_type="relationship_dynamic", category="relationships"),
    ]

    first = _select_hybrid_top_n(insights, None, n=3)
    second = _select_hybrid_top_n(insights, None, n=3)

    assert [item["value"] for item in first] == [
        "Kernmerkmal",
        "Aktueller Zustand",
        "Beziehungsmuster",
    ]
    assert first == second
