"""Tests for chat-side consent handling of pending sandbox permission requests."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent_chatbot_logic import AgentChatbotLogic


class _ToolkitStub:
    def __init__(self):
        self.pending = {"requested_path": "C:/Dokumente/geladen_chat.pdf"}
        self.grants: list[str] = []

    def get_pending_permission_request(self):
        return dict(self.pending) if self.pending else None

    def clear_pending_permission_request(self):
        self.pending = None

    def grant_pending_path_access(self, mode: str = "temporary"):
        self.grants.append(mode)
        self.pending = None
        return {
            "success": True,
            "grant_mode": mode,
            "granted_base_dir": "C:/Dokumente",
        }


def _build_logic_with_stub() -> AgentChatbotLogic:
    logic = AgentChatbotLogic.__new__(AgentChatbotLogic)
    logic.agent_toolkit = _ToolkitStub()
    return logic


def test_permission_consent_temporary():
    logic = _build_logic_with_stub()

    reply = logic._try_handle_pending_permission_consent("ja, bitte temporär freigeben")

    assert reply is not None
    assert "temporär" in reply.lower()
    assert logic.agent_toolkit.grants == ["temporary"]


def test_permission_consent_persistent():
    logic = _build_logic_with_stub()

    reply = logic._try_handle_pending_permission_consent("ja, dauerhaft in die allowlist")

    assert reply is not None
    assert "dauerhaft" in reply.lower()
    assert logic.agent_toolkit.grants == ["persistent"]


def test_permission_consent_denied():
    logic = _build_logic_with_stub()

    reply = logic._try_handle_pending_permission_consent("nein")

    assert reply is not None
    assert "gesperrt" in reply.lower()
    assert logic.agent_toolkit.pending is None
