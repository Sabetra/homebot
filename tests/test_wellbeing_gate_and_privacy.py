"""
Regression-Tests (2026-08-28) — KG-Trennung & Privacy:

1. PRIVACY GUARD: Der normale Chat-Pfad (`AgentChatbotLogic._agent_chat`)
   erhält NIEMALS psychologische Profil-/Familien-/Session-Daten (Psych-KG).
   Der Thread-Local-Schalter wird hier explizit auf False gesetzt — auch dann,
   wenn `ENABLE_AGENT_PSYCH_INTEGRATION` gesetzt ist oder ein Orchestrator-Patch
   den Schalter aktiviert hat.
2. Psych-Tab-Pfad: Der Schalter kann dort weiterhin aktiviert werden
   (`psychological_chat` nutzt ihn) — das Guard betrifft nur den normalen Chat.
3. PII-Regression: Die hartkodierten Namen (Christine/Kiano) dürfen in den
   betroffenen Modulen nicht wieder auftauchen.
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest

from agent_chatbot_logic import AgentChatbotLogic
from utils.psychological_orchestrator_integration import (
    get_psychological_context_enabled,
    set_psychological_context_enabled,
)


def _make_bare_bot():
    """Instanz ohne __init__ — `_agent_chat` braucht nur die zwei
    Routing-Attribute; der eigentliche Chat-Flow wird gestubt."""
    bot = AgentChatbotLogic.__new__(AgentChatbotLogic)
    bot.search_method = "intelligent_routing"
    bot.optimized_research_engine = None
    return bot


def test_agent_chat_forces_psych_context_off_even_with_env_opt_in(monkeypatch):
    bot = _make_bare_bot()
    observed = {}

    def fake_standard_agent_chat(*args, **kwargs):
        observed["enabled_during"] = get_psychological_context_enabled()
        return "antwort"

    monkeypatch.setattr(AgentChatbotLogic, "_standard_agent_chat",
                        fake_standard_agent_chat)
    monkeypatch.setenv("ENABLE_AGENT_PSYCH_INTEGRATION", "1")

    # Simuliere einen aktiven Orchestrator-Patch (Flag vor dem Call ON)
    set_psychological_context_enabled(True)
    try:
        result = bot._agent_chat("hallo welt")
    finally:
        set_psychological_context_enabled(False)

    assert result == "antwort"
    assert observed["enabled_during"] is False, \
        "normaler Chat muss den Psych-Kontext zwangsweise deaktivieren"
    assert get_psychological_context_enabled() is False


def test_agent_chat_resets_flag_even_on_exception(monkeypatch):
    bot = _make_bare_bot()

    def failing_standard_agent_chat(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(AgentChatbotLogic, "_standard_agent_chat",
                        failing_standard_agent_chat)

    set_psychological_context_enabled(True)
    try:
        with pytest.raises(RuntimeError, match="boom"):
            bot._agent_chat("hallo")
    finally:
        set_psychological_context_enabled(False)
    assert get_psychological_context_enabled() is False, \
        "Flag darf nach einem Fehler nicht auf True stehen (Leak-Guard)"


def test_agent_chat_does_not_use_deprecated_psych_gate(monkeypatch):
    """Der deaktivierte Intent-Gate darf im normalen Pfad keine Rolle mehr
    spielen: `_agent_chat` ruft `_ensure_psychological_integration` nicht auf."""
    bot = _make_bare_bot()
    calls = []
    monkeypatch.setattr(
        AgentChatbotLogic, "_ensure_psychological_integration",
        lambda self: calls.append(self),
    )

    def fake_standard_agent_chat(*args, **kwargs):
        return "ok"

    monkeypatch.setattr(AgentChatbotLogic, "_standard_agent_chat",
                        fake_standard_agent_chat)

    set_psychological_context_enabled(True)
    try:
        bot._agent_chat("test")
    finally:
        set_psychological_context_enabled(False)

    assert calls == []


def test_deprecated_helpers_retained_for_rollback():
    assert callable(getattr(AgentChatbotLogic, "_ensure_psychological_integration", None))
    assert callable(getattr(AgentChatbotLogic, "_should_enable_psychological_integration", None))


def test_psych_tab_flag_can_still_be_enabled():
    """Der Psych-Tab-Pfad (psychological_chat) aktiviert den Schalter selbst —
    das Guard darf diese Fähigkeit nicht einschränken."""
    set_psychological_context_enabled(True)
    try:
        assert get_psychological_context_enabled() is True
    finally:
        set_psychological_context_enabled(False)
    assert get_psychological_context_enabled() is False


def test_no_hardcoded_pii_in_edited_modules():
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    for rel in (
        "utils/psychological_orchestrator_integration.py",
        "agent/privacy_handler_enhanced.py",
    ):
        with open(os.path.join(repo_root, rel), "r", encoding="utf-8") as fh:
            source = fh.read()
        for name in ("Christine", "Kiano"):
            assert name not in source, \
                f"{rel} enthält wieder das PII-Literal '{name}'"