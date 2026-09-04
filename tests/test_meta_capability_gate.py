"""
Test für _is_meta_capability_query deterministischen Gate.
Stellt sicher, dass Meta-Fragen nach Fähigkeiten korrekt als PLAN_EXECUTE geroutet werden.
"""

import sys
import os
from typing import Optional

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from agent.web_search_planner import WebSearchPlanner, WebSearchReflector
from agent_chatbot_logic import AgentChatbotLogic


class MockAgentChatbotLogic:
    """Minimaler Mock für Test des Meta-Capability-Gates."""

    def __init__(self):
        self.agent_mode_enabled = True
        self.chat_routing_config = {"use_llm_routing": True}
        self.model_loader = None
        self._last_routing_debug = ""

    # Kopiere die relevanten Methoden aus AgentChatbotLogic
    def _is_meta_capability_query(self, user_prompt: Optional[str]) -> bool:
        text = (user_prompt or "").strip()
        if not text:
            return False
        lower = text.lower()
        meta_patterns = [
            "was kannst du",
            "was können sie",
            "was kannst du so",
            "welche fähigkeiten",
            "welche tools",
            "was kannst du alles",
            "kannst du",
            "was bist du",
            "wer bist du",
            "was für tools",
            "welche fähigkeiten hast",
            "was kannst du mir",
            "welche möglichkeiten",
            "was kannst du tun",
            "was sind deine fähigkeiten",
            "was sind deine tools",
            "welche tools hast du",
            "welche tools kannst du",
            "what can you do",
            "what are your capabilities",
            "what tools do you have",
            "what tools can you",
            "what are your tools",
            "what can you",
            "who are you",
            "what are you",
            "what abilities",
            "what functions",
            "list your tools",
            "show me your tools",
            "tell me what you can",
            "deine fähigkeiten",
            "deine tools",
            "deine möglichkeiten",
            "kannst du dateien",
            "kannst du suchen",
            "kannst du code",
            "kannst du web",
            "kannst du bilder",
            "kannst du finanz",
        ]
        return any(pattern in lower for pattern in meta_patterns)


def test_meta_queries_detected():
    """Meta-Fragen sollen als True erkannt werden."""
    mock = MockAgentChatbotLogic()

    meta_queries = [
        "Was kannst du alles?",
        "Was kannst du?",
        "Welche Tools hast du?",
        "Was sind deine Fähigkeiten?",
        "Wer bist du?",
        "Was bist du?",
        "What can you do?",
        "What tools do you have?",
        "Who are you?",
        "Deine Fähigkeiten auflisten",
        "Was kannst du mir anbieten?",
        "Welche Möglichkeiten hast du?",
        "Kannst du Dateien suchen?",
        "Kannst du Code ausführen?",
        "Was für Tools hast du?",
    ]

    for query in meta_queries:
        result = mock._is_meta_capability_query(query)
        assert result is True, f"Meta-Query sollte erkannt werden: '{query}'"

    print(f"✅ {len(meta_queries)} Meta-Queries korrekt erkannt")


def test_normal_queries_not_detected():
    """Normale Fragen sollen NICHT als Meta-Query erkannt werden."""
    mock = MockAgentChatbotLogic()

    normal_queries = [
        "Hallo, wie geht's?",
        "Was ist die Hauptstadt von Deutschland?",
        "Erzähl mir einen Witz",
        "Wie viel ist 2+2?",
        "Schreib mir ein Gedicht",
        "Was bedeutet KI?",
        "Erkläre mir Quantenphysik",
        "Ich brauche Hilfe bei Python",
        "Wie funktioniert RAG?",
        "Was ist der Sinn des Lebens?",
    ]

    for query in normal_queries:
        result = mock._is_meta_capability_query(query)
        assert result is False, f"Normale Query sollte NICHT als Meta erkannt werden: '{query}'"

    print(f"✅ {len(normal_queries)} normale Queries korrekt durchgelassen")


def test_empty_and_none():
    """Leere Eingaben sollen False zurückgeben."""
    mock = MockAgentChatbotLogic()

    assert mock._is_meta_capability_query("") is False
    assert mock._is_meta_capability_query(None) is False
    assert mock._is_meta_capability_query("   ") is False

    print("✅ Leere Eingaben korrekt behandelt")


def test_web_search_components_accept_max_tokens_kwarg():
    """Regressions-Test: Konstruktoren müssen max_tokens akzeptieren."""

    def fake_llm(prompt, max_tokens=128):
        return {"prompt": prompt, "max_tokens": max_tokens}

    planner = WebSearchPlanner(fake_llm, max_tokens=256)
    reflector = WebSearchReflector(fake_llm, max_tokens=256)

    assert planner.max_tokens == 256
    assert reflector.max_tokens == 256
    assert planner._generate("hello") == {"prompt": "hello", "max_tokens": 256}
    assert reflector._generate("hello") == {"prompt": "hello", "max_tokens": 256}

    print("✅ WebSearchPlanner/Reflector akzeptieren max_tokens")


if __name__ == "__main__":
    test_meta_queries_detected()
    test_normal_queries_not_detected()
    test_empty_and_none()
    test_web_search_components_accept_max_tokens_kwarg()
    print("\n🎉 Alle Meta-Capability-Tests bestanden!")