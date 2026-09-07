"""Native-Thinking-Capability des Reasoning-Optimizers (Qwen3.x u.a.).

Root Cause (2026-08-27): Der Optimizer injizierte das Magistral-[THINK]-Format
auch bei Modellen, deren GGUF-Template natives Thinking rendert (Qwen3.x:
<think>-Prefill, enable_thinking). Folge: kollidierende Formate, Reasoning-
Leaks in Antworten und fehlgeschlagene Klassifikationen (max_tokens=10 wurde
komplett von Think-Tokens verbraucht).
"""
from __future__ import annotations

from typing import Any, Dict

import pytest

import scripts.model_loader as model_loader_module
from ministral_reasoning_optimizer import MinistralReasoningOptimizer

_QWEN_TEMPLATE = "{% for m in messages %}{{ m.content }}{% endfor %}<think>\n"
_MAGISTRAL_TEMPLATE = "{{ bos_token }}[INST]{% for m in messages %}{{ m.content }}{% endfor %}[/INST]"
# Gemma 4: 'enable_thinking' steht im Template, aber der DEFAULT-Render prefillt
# einen GESCHLOSSENEN Thought-Block — das Modell denkt per Default NICHT nativ.
_GEMMA4_TEMPLATE = (
    "{% for m in messages %}{{ m.content }}{% endfor %}"
    "{% if not enable_thinking | default(false) %}"
    "<|channel>thought\n<channel|>"
    "{% endif %}"
)


class _FakeLlama:
    def __init__(self, template: str) -> None:
        self.metadata = {"tokenizer.chat_template": template}


def _make_optimizer(template: str) -> MinistralReasoningOptimizer:
    return MinistralReasoningOptimizer(llama_model=_FakeLlama(template))


def test_native_thinking_detected_for_qwen_template():
    assert _make_optimizer(_QWEN_TEMPLATE)._native_thinking_template() is True


def test_native_thinking_not_detected_for_magistral_template():
    assert _make_optimizer(_MAGISTRAL_TEMPLATE)._native_thinking_template() is False


def test_native_thinking_not_detected_for_gemma4_closed_thought_prefill():
    # Regression: String-Matching auf 'enable_thinking' klassifizierte Gemma 4
    # fälschlich als nativ denkend — der Default-Render ist aber geschlossen.
    assert _make_optimizer(_GEMMA4_TEMPLATE)._native_thinking_template() is False


def test_gemma4_keeps_think_prompt_injection():
    prompt = _make_optimizer(_GEMMA4_TEMPLATE).get_system_prompt(enable_reasoning=True)

    assert "[THINK]" in prompt


def test_system_prompt_omits_think_tags_for_native_thinking_model():
    prompt = _make_optimizer(_QWEN_TEMPLATE).get_system_prompt(enable_reasoning=True)

    assert "[THINK]" not in prompt
    assert "Core Guidelines" in prompt


def test_system_prompt_keeps_think_tags_for_magistral():
    prompt = _make_optimizer(_MAGISTRAL_TEMPLATE).get_system_prompt(enable_reasoning=True)

    assert "[THINK]" in prompt


def test_parse_response_native_orphan_closing_think():
    optimizer = _make_optimizer(_QWEN_TEMPLATE)
    response = {
        "choices": [
            {"message": {"content": "Wir rechnen 17*23=391.\n</think>\n\n391"}}
        ]
    }

    parsed = optimizer.parse_ministral_response(response)

    assert parsed["answer"] == "391"
    assert "391" in parsed["reasoning"] or "rechnen" in parsed["reasoning"]


def test_parse_response_native_paired_think():
    optimizer = _make_optimizer(_QWEN_TEMPLATE)
    response = {
        "choices": [
            {"message": {"content": "<think>Schritt 1</think>\nAntwort hier"}}
        ]
    }

    parsed = optimizer.parse_ministral_response(response)

    assert parsed["answer"] == "Antwort hier"
    assert parsed["reasoning"] == "Schritt 1"


def test_classification_prefers_loader_path(monkeypatch: pytest.MonkeyPatch):
    fake_llm = _FakeLlama(_QWEN_TEMPLATE)
    optimizer = MinistralReasoningOptimizer(llama_model=fake_llm)

    calls: Dict[str, Any] = {}

    class _FakeLoader:
        llm = fake_llm

        def generate_response(self, **kwargs: Any) -> str:
            calls.update(kwargs)
            return "Complex."

    monkeypatch.setattr(
        model_loader_module.ModelLoader, "_instance", _FakeLoader(), raising=False
    )

    result = optimizer.estimate_complexity_with_llm("Warum ist der Himmel blau?")

    assert result == "complex"
    assert calls["max_tokens"] == 16, "Klassifikation muss den Loader-Pfad nutzen"


def test_classification_regex_fallback_on_verbose_answer(monkeypatch: pytest.MonkeyPatch):
    fake_llm = _FakeLlama(_QWEN_TEMPLATE)
    optimizer = MinistralReasoningOptimizer(llama_model=fake_llm)

    class _FakeLoader:
        llm = fake_llm

        def generate_response(self, **kwargs: Any) -> str:
            return "The query complexity is medium overall."

    monkeypatch.setattr(
        model_loader_module.ModelLoader, "_instance", _FakeLoader(), raising=False
    )

    assert optimizer.estimate_complexity_with_llm("Wie installiere ich Python?") == "medium"
