from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Dict
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from wellbeing.profile_synthesizer import ProfileSynthesizer


class _DummyDB:
    @contextmanager
    def get_connection(self):
        raise RuntimeError("DB access not expected in this unit test")
        yield


class _GrammarAwareLoader:
    def __init__(self) -> None:
        self.kwargs: Dict[str, Any] = {}

    def generate_response(
        self,
        messages: list,
        temperature: float,
        max_tokens: int,
        stop: list | None = None,
        grammar: Any = None,
    ) -> str:
        self.kwargs = {
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stop": stop,
            "grammar": grammar,
        }
        return '{"core_personality": {"traits": [], "communication_style": "", "decision_making": "", "confidence": 0.8}, "current_state": {"emotional_tone": "", "stress_level": "low", "life_phase": "", "primary_concerns": [], "confidence": 0.7}, "relationships": {"family_dynamics": {}, "social_style": "", "attachment_patterns": "", "confidence": 0.7}, "goals_and_growth": {"current_goals": [], "growth_areas": [], "progress_indicators": [], "confidence": 0.7}, "coping_and_resources": {"strategies": [], "strengths": [], "support_systems": [], "confidence": 0.7}, "therapeutic_focus": {"priority_areas": [], "intervention_suggestions": [], "progress_markers": [], "confidence": 0.7}, "overall_confidence": 0.74}'


class _GrammarBlindLoader:
    def __init__(self) -> None:
        self.kwargs: Dict[str, Any] = {}

    def generate_response(self, messages: list, temperature: float, max_tokens: int, stop: list | None = None) -> str:
        self.kwargs = {
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stop": stop,
        }
        return '{}'


def test_call_llm_for_synthesis_uses_grammar_when_supported(monkeypatch):
    loader = _GrammarAwareLoader()
    synthesizer = ProfileSynthesizer(psychological_db=_DummyDB(), model_loader=loader)

    sentinel_grammar = object()
    monkeypatch.setattr(synthesizer, "_compile_profile_json_grammar", lambda: sentinel_grammar)

    _ = synthesizer._call_llm_for_synthesis("prompt", 512)

    assert loader.kwargs["grammar"] is sentinel_grammar
    assert loader.kwargs["max_tokens"] == 512
    assert loader.kwargs["temperature"] == 0.2


def test_call_llm_for_synthesis_without_grammar_support():
    loader = _GrammarBlindLoader()
    synthesizer = ProfileSynthesizer(psychological_db=_DummyDB(), model_loader=loader)

    _ = synthesizer._call_llm_for_synthesis("prompt", 256)

    assert "max_tokens" in loader.kwargs
    assert "stop" in loader.kwargs


def test_validate_profile_schema_requires_section_confidences():
    loader = _GrammarBlindLoader()
    synthesizer = ProfileSynthesizer(psychological_db=_DummyDB(), model_loader=loader)

    valid = {
        "core_personality": {"traits": [], "communication_style": "", "decision_making": "", "confidence": 0.8},
        "current_state": {"emotional_tone": "", "stress_level": "low", "life_phase": "", "primary_concerns": [], "confidence": 0.7},
        "relationships": {"family_dynamics": {}, "social_style": "", "attachment_patterns": "", "confidence": 0.7},
        "goals_and_growth": {"current_goals": [], "growth_areas": [], "progress_indicators": [], "confidence": 0.7},
        "coping_and_resources": {"strategies": [], "strengths": [], "support_systems": [], "confidence": 0.7},
        "therapeutic_focus": {"priority_areas": [], "intervention_suggestions": [], "progress_markers": [], "confidence": 0.7},
        "overall_confidence": 0.74,
    }
    assert synthesizer._validate_profile_schema(valid)

    invalid = dict(valid)
    invalid["therapeutic_focus"] = {"priority_areas": []}
    assert not synthesizer._validate_profile_schema(invalid)


def test_parse_llm_response_rejects_invalid_stress_level():
    loader = _GrammarBlindLoader()
    synthesizer = ProfileSynthesizer(psychological_db=_DummyDB(), model_loader=loader)

    response = (
        '{"core_personality": {"traits": [], "communication_style": "", "decision_making": "", "confidence": 0.8}, '
        '"current_state": {"emotional_tone": "", "stress_level": "extreme", "life_phase": "", "primary_concerns": [], "confidence": 0.7}, '
        '"relationships": {"family_dynamics": {}, "social_style": "", "attachment_patterns": "", "confidence": 0.7}, '
        '"goals_and_growth": {"current_goals": [], "growth_areas": [], "progress_indicators": [], "confidence": 0.7}, '
        '"coping_and_resources": {"strategies": [], "strengths": [], "support_systems": [], "confidence": 0.7}, '
        '"therapeutic_focus": {"priority_areas": [], "intervention_suggestions": [], "progress_markers": [], "confidence": 0.7}, '
        '"overall_confidence": 0.74}'
    )

    assert synthesizer._parse_llm_response(response) is None


def test_parse_llm_response_rejects_empty_high_confidence_profile():
    loader = _GrammarBlindLoader()
    synthesizer = ProfileSynthesizer(psychological_db=_DummyDB(), model_loader=loader)

    response = (
        '{"core_personality": {"traits": [], "communication_style": "", "decision_making": "", "confidence": 0.95}, '
        '"current_state": {"emotional_tone": "", "stress_level": "low", "life_phase": "", "primary_concerns": [], "confidence": 0.95}, '
        '"relationships": {"family_dynamics": {}, "social_style": "", "attachment_patterns": "", "confidence": 0.95}, '
        '"goals_and_growth": {"current_goals": [], "growth_areas": [], "progress_indicators": [], "confidence": 0.95}, '
        '"coping_and_resources": {"strategies": [], "strengths": [], "support_systems": [], "confidence": 0.95}, '
        '"therapeutic_focus": {"priority_areas": [], "intervention_suggestions": [], "progress_markers": [], "confidence": 0.95}, '
        '"overall_confidence": 0.95}'
    )

    assert synthesizer._parse_llm_response(response) is None
