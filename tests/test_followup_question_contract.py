from __future__ import annotations

from typing import Any

from agent.orchestrator import AgentOrchestrator
from agent.prompts import SUMMARIZER_FALLBACK_USER_TEMPLATE, SUMMARIZER_USER_TEMPLATE
from chatbot_logic import DEFAULT_SYSTEM_PROMPT
from utils.followup_question_extractor import (
    FOLLOWUP_PERSPECTIVE_INSTRUCTION,
    format_followup_for_prompt,
)


class _CapturingLoader:
    def __init__(self) -> None:
        self.messages: list[dict[str, str]] = []

    def generate_response(self, **kwargs: Any) -> str:
        self.messages = kwargs["messages"]
        return "Wie funktioniert das technisch?|Welche Grenzen gibt es?|Was folgt daraus?"


def test_all_shared_followup_prompts_require_user_perspective() -> None:
    assert FOLLOWUP_PERSPECTIVE_INSTRUCTION in DEFAULT_SYSTEM_PROMPT
    assert FOLLOWUP_PERSPECTIVE_INSTRUCTION in SUMMARIZER_USER_TEMPLATE
    assert FOLLOWUP_PERSPECTIVE_INSTRUCTION in SUMMARIZER_FALLBACK_USER_TEMPLATE
    assert FOLLOWUP_PERSPECTIVE_INSTRUCTION in format_followup_for_prompt()


def test_dedicated_followup_call_requests_next_user_messages() -> None:
    loader = _CapturingLoader()
    orchestrator = AgentOrchestrator.__new__(AgentOrchestrator)
    orchestrator.model_loader = loader

    questions = orchestrator._generate_followup_questions(
        "Ich teste Streaming.",
        "Das ist eine ausreichend lange Antwort, damit Folgefragen erzeugt werden koennen.",
    )

    system_prompt = loader.messages[0]["content"]
    assert FOLLOWUP_PERSPECTIVE_INSTRUCTION in system_prompt
    assert "derselben Sprache wie die Nutzerfrage" in system_prompt
    assert questions == [
        "Wie funktioniert das technisch?",
        "Welche Grenzen gibt es?",
        "Was folgt daraus?",
    ]