"""Tool Profiles Tests (2026)
============================

Testet die deklarative Tool-Verfügbarkeit pro Tab-Mode:
- Profile existieren und sind korrekt konfiguriert
- Tool-Filterung funktioniert pro Mode
- FS-Read/Write-Einschränkungen werden eingehalten
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from agent.tool_profiles import (
    ToolProfile,
    TOOL_PROFILES,
    get_profile,
    is_tool_allowed,
    has_fs_read,
    has_fs_write,
    filter_tool_schemas,
)


class TestToolProfiles:
    """Test-Suite für deklarative Tool-Profile."""

    def test_main_chat_profile_exists(self):
        assert "main_chat" in TOOL_PROFILES
        assert isinstance(TOOL_PROFILES["main_chat"], ToolProfile)

    def test_finance_tab_profile_exists(self):
        assert "finance_tab" in TOOL_PROFILES
        assert isinstance(TOOL_PROFILES["finance_tab"], ToolProfile)

    def test_wellbeing_tab_profile_exists(self):
        assert "wellbeing_tab" in TOOL_PROFILES
        assert isinstance(TOOL_PROFILES["wellbeing_tab"], ToolProfile)

    def test_main_chat_has_full_tools(self):
        profile = get_profile("main_chat")
        assert "rag_search" in profile.allowed_tools
        assert "file_reader" in profile.allowed_tools
        assert "pdf_extract" in profile.allowed_tools
        assert "list_directory" in profile.allowed_tools

    def test_main_chat_fs_read_write(self):
        assert has_fs_read("main_chat") is True
        assert has_fs_write("main_chat") is True

    def test_finance_tab_read_only(self):
        assert has_fs_read("finance_tab") is True
        assert has_fs_write("finance_tab") is False

    def test_finance_tab_no_file_writer(self):
        profile = get_profile("finance_tab")
        assert "file_writer" not in profile.allowed_tools

    def test_wellbeing_tab_no_fs_access(self):
        assert has_fs_read("wellbeing_tab") is False
        assert has_fs_write("wellbeing_tab") is False

    def test_wellbeing_tab_only_rag(self):
        profile = get_profile("wellbeing_tab")
        assert "rag_search" in profile.allowed_tools
        assert len(profile.allowed_tools) == 1

    # ------------------------------------------------------------------
    # is_tool_allowed Tests
    # ------------------------------------------------------------------

    def test_is_tool_allowed_main_chat(self):
        assert is_tool_allowed("rag_search", "main_chat") is True
        assert is_tool_allowed("file_reader", "main_chat") is True

    def test_is_tool_allowed_finance(self):
        assert is_tool_allowed("rag_search", "finance_tab") is True
        assert is_tool_allowed("file_writer", "finance_tab") is False

    def test_is_tool_allowed_wellbeing(self):
        assert is_tool_allowed("rag_search", "wellbeing_tab") is True
        assert is_tool_allowed("file_reader", "wellbeing_tab") is False
        assert is_tool_allowed("web_search", "wellbeing_tab") is False

    # ------------------------------------------------------------------
    # filter_tool_schemas Tests
    # ------------------------------------------------------------------

    def test_filter_tool_schemas_wellbeing(self):
        schemas = [
            {"function": {"name": "rag_search"}},
            {"function": {"name": "file_reader"}},
            {"function": {"name": "file_writer"}},
            {"function": {"name": "web_search"}},
        ]
        filtered = filter_tool_schemas(schemas, "wellbeing_tab")
        assert len(filtered) == 1
        assert filtered[0]["function"]["name"] == "rag_search"

    def test_filter_tool_schemas_finance(self):
        schemas = [
            {"function": {"name": "rag_search"}},
            {"function": {"name": "file_reader"}},
            {"function": {"name": "file_writer"}},
            {"function": {"name": "calculator"}},
        ]
        filtered = filter_tool_schemas(schemas, "finance_tab")
        names = [s["function"]["name"] for s in filtered]
        assert "rag_search" in names
        assert "file_writer" not in names


if __name__ == "__main__":
    pytest.main([__file__, "-v"])