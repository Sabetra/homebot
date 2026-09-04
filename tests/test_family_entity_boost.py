from __future__ import annotations

import pytest

from wellbeing_session.context.family_entity_boost import family_entity_kg_boost


class _SemanticBoostDb:
    def __init__(self) -> None:
        self.search_queries = []

    def _semantic_entity_match(self, query: str, top_k: int = 10):
        return [("Vater", 0.86)]

    def search_knowledge_graph(self, query: str, user_id: str | None, limit: int, min_confidence: float):
        self.search_queries.append(query)
        if query == "Vater":
            return [
                {
                    "subject": "Vater",
                    "predicate": "hat_thema",
                    "object": "Arbeitsstress",
                    "confidence": 0.91,
                    "similarity": 0.74,
                }
            ]
        return []


class _RuntimeErrorDb:
    def _semantic_entity_match(self, query: str, top_k: int = 10):
        return [("Vater", 0.8)]

    def search_knowledge_graph(self, query: str, user_id: str | None, limit: int, min_confidence: float):
        raise RuntimeError("db-corruption")


def test_family_entity_boost_uses_semantic_hints_without_regex_match():
    db = _SemanticBoostDb()
    existing = [
        {
            "subject": "Arbeit",
            "predicate": "beeinflusst",
            "object": "Stimmung",
            "confidence": 0.8,
            "combined_score": 0.72,
        }
    ]

    merged = family_entity_kg_boost(
        db=db,
        query="Wie steht es aktuell mit Beziehungen zuhause?",
        existing_triples=existing,
        user_id="user-1",
    )

    assert merged
    assert any(t.get("subject") == "Vater" for t in merged)
    boosted = next(t for t in merged if t.get("subject") == "Vater")
    assert boosted.get("_family_boost") is True
    assert boosted.get("_boost_source") == "semantic_entity_match"
    assert "Vater" in db.search_queries


def test_family_entity_boost_propagates_runtime_errors():
    db = _RuntimeErrorDb()

    with pytest.raises(RuntimeError, match="db-corruption"):
        family_entity_kg_boost(
            db=db,
            query="Mein Vater",
            existing_triples=[],
            user_id="user-1",
        )


def test_family_entity_boost_deduplicates_existing_triples():
    db = _SemanticBoostDb()
    existing = [
        {
            "subject": "Vater",
            "predicate": "hat_thema",
            "object": "Arbeitsstress",
            "confidence": 0.88,
            "combined_score": 0.71,
        }
    ]

    merged = family_entity_kg_boost(
        db=db,
        query="Erzaehl was ueber Familie",
        existing_triples=existing,
        user_id="user-1",
    )

    assert len([t for t in merged if t.get("subject") == "Vater"]) == 1
