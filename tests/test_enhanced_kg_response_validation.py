from __future__ import annotations

from typing import Any

from agent.llm_knowledge_graph_enhanced import EnhancedLLMKnowledgeGraphExtractor
from llm_utils.guaranteed_caller import GuaranteedLLMCaller, LLMCallResult


SHORT_VALID_KG_JSON = '{"triples": [], "metadata": {}}'


class _StaticLLM:
    def __init__(self, response: str) -> None:
        self.response = response
        self.calls = 0

    def __call__(self, _prompt: str) -> str:
        self.calls += 1
        return self.response


class _SequenceLLM:
    def __init__(self, responses: list[str]) -> None:
        self.responses = iter(responses)
        self.calls = 0

    def __call__(self, _prompt: str) -> str:
        self.calls += 1
        return next(self.responses)


class _FailedCaller:
    def call_with_guarantee(self, **_kwargs: Any) -> LLMCallResult:
        return LLMCallResult(
            response='{"error": "LLM call failed", "fallback": true}',
            success=False,
            attempts=3,
            total_time=0.1,
            temperature_used=0.5,
            error_message="All 3 attempts failed",
        )


class _ParserSpy:
    def __init__(self) -> None:
        self.called = False

    def parse_llm_response(self, *_args: Any, **_kwargs: Any) -> Any:
        self.called = True
        raise AssertionError("failed LLM envelopes must not reach the KG parser")


def test_short_valid_empty_kg_json_is_accepted_without_retry():
    assert len(SHORT_VALID_KG_JSON) == 31
    llm = _StaticLLM(SHORT_VALID_KG_JSON)
    extractor = EnhancedLLMKnowledgeGraphExtractor(llm_client=llm)

    triples = extractor.extract_knowledge_graph(
        "Was weißt Du über meinen Vater?",
        doc_context={"source_type": "psychology", "user_name": "Alex"},
    )

    assert triples == []
    assert llm.calls == 1


def test_structurally_invalid_kg_json_is_retried():
    llm = _SequenceLLM([
        "{}",
        '{"triples": "not-a-list"}',
        SHORT_VALID_KG_JSON,
    ])
    extractor = EnhancedLLMKnowledgeGraphExtractor(llm_client=llm)

    triples = extractor.extract_knowledge_graph("Ein ausreichend langer therapeutischer Text.")

    assert triples == []
    assert llm.calls == 3


def test_short_fenced_kg_json_is_accepted_without_retry():
    llm = _StaticLLM(f"```json\n{SHORT_VALID_KG_JSON}\n```")
    extractor = EnhancedLLMKnowledgeGraphExtractor(llm_client=llm)

    triples = extractor.extract_knowledge_graph("Ein ausreichend langer therapeutischer Text.")

    assert triples == []
    assert llm.calls == 1


def test_generic_caller_keeps_default_minimum_length_validation():
    llm = _StaticLLM("short")
    caller = GuaranteedLLMCaller(llm, max_retries=2, min_response_length=10)

    result = caller.call_with_guarantee(prompt="test", fallback_response="fallback")

    assert not result.success
    assert result.response == "fallback"
    assert llm.calls == 2


def test_failed_llm_envelope_is_not_sent_to_kg_parser(monkeypatch):
    extractor = EnhancedLLMKnowledgeGraphExtractor(llm_client=lambda _prompt: "unused")
    extractor.llm_caller = _FailedCaller()
    parser = _ParserSpy()
    extractor.response_handler = parser
    fallback_calls: list[str] = []
    monkeypatch.setattr(
        extractor,
        "_fallback_extraction",
        lambda text: fallback_calls.append(text) or [],
    )

    triples = extractor.extract_knowledge_graph("Ein ausreichend langer therapeutischer Text.")

    assert triples == []
    assert not parser.called
    assert fallback_calls == ["Ein ausreichend langer therapeutischer Text."]
