"""Regression tests for deterministic direct local-file query fast path."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent_chatbot_logic import AgentChatbotLogic


class _ToolkitStub:
    def __init__(self, result: dict):
        self.result = result
        self.calls: list[tuple[str, dict]] = []

    def execute_tool(self, tool_name: str, params: dict):
        self.calls.append((tool_name, params))
        return self.result


def _logic_with_tool_result(result: dict) -> AgentChatbotLogic:
    logic = AgentChatbotLogic.__new__(AgentChatbotLogic)
    logic.agent_toolkit = _ToolkitStub(result)
    return logic


def test_direct_file_query_prompts_permission_on_sandbox_denial():
    logic = _logic_with_tool_result(
        {
            "success": False,
            "needs_user_permission": True,
            "suggested_user_prompt": "Soll ich Freigabe anfragen?",
        }
    )

    reply = logic._try_direct_local_file_query(
        r"Lies die Datei C:\Dokumente\qa_test_document.pdf"
    )

    assert reply is not None
    assert "Freigabe" in reply
    assert "C:\\Dokumente\\qa_test_document.pdf" in reply


def test_direct_file_query_extracts_project_title():
    logic = _logic_with_tool_result(
        {
            "success": True,
            "text": "Projekttitel: ALPHA-OMEGA-2026\nKundennummer: 77821",
            "ingest_policy": "stream_only_no_rag",
            "rag_ingest": False,
        }
    )

    reply = logic._try_direct_local_file_query(
        r"Lies bitte C:\Dokumente\qa_test_document.pdf und nenne den Projekttitel"
    )

    assert reply == "ALPHA-OMEGA-2026"


def test_extract_windows_file_path():
    logic = _logic_with_tool_result({"success": True, "text": "x"})

    path = logic._extract_windows_file_path(
        r"Bitte lies C:\Dokumente\qa_test_document.pdf und antworte kurz"
    )

    assert path == r"C:\Dokumente\qa_test_document.pdf"
