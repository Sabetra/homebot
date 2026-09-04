"""Filesystem Tools Integration Tests (2026)
============================================

End-to-End Tests für die Integration von list_directory und search_files
Tools mit dem Agent Toolkit und Path Sandbox.
"""

import sys
from pathlib import Path
import tempfile
import shutil
from typing import Any, cast

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from agent.path_sandbox import PathSandboxError
from agent_toolkit import AgentToolkit


class TestFilesystemToolsIntegration:
    """End-to-End Tests für FS-Tool-Integration."""

    def setup_method(self):
        self.test_dir = tempfile.mkdtemp(prefix="fs_integration_")

    def teardown_method(self):
        if Path(self.test_dir).exists():
            shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_list_directory_tool_exists(self):
        """list_directory Tool existiert im Toolkit."""
        from agent.tool_schemas import get_tool_schemas
        schemas = get_tool_schemas()
        tool_names = [s["function"]["name"] for s in schemas]
        assert "list_directory" in tool_names

    def test_search_files_tool_exists(self):
        """search_files Tool existiert im Toolkit."""
        from agent.tool_schemas import get_tool_schemas
        schemas = get_tool_schemas()
        tool_names = [s["function"]["name"] for s in schemas]
        assert "search_files" in tool_names

    def test_list_directory_schema_structure(self):
        """list_directory Schema hat korrekte Struktur."""
        from agent.tool_schemas import get_tool_schemas
        schemas = get_tool_schemas()
        list_dir_schema = next(
            (s for s in schemas if s["function"]["name"] == "list_directory"),
            None
        )
        assert list_dir_schema is not None
        assert "path" in list_dir_schema["function"]["parameters"]["properties"]

    def test_search_files_schema_structure(self):
        """search_files Schema hat korrekte Struktur."""
        from agent.tool_schemas import get_tool_schemas
        schemas = get_tool_schemas()
        search_schema = next(
            (s for s in schemas if s["function"]["name"] == "search_files"),
            None
        )
        assert search_schema is not None
        props = search_schema["function"]["parameters"]["properties"]
        assert "root_path" in props
        assert "pattern" in props

    def test_toolkit_dispatch_list_directory(self):
        """TOOL_DISPATCH enthält list_directory."""
        # dispatch ist in execute_tool() als lokales Dict definiert
        # Wir prüfen, dass die Handler-Methoden existieren
        from agent_toolkit import AgentToolkit
        assert hasattr(AgentToolkit, "_list_directory")

    def test_toolkit_dispatch_search_files(self):
        """TOOL_DISPATCH enthält search_files."""
        from agent_toolkit import AgentToolkit
        assert hasattr(AgentToolkit, "_search_files")

    def test_sandbox_integration_basic(self):
        """PathSandbox blockiert Zugriffe außerhalb des Workspace."""
        from agent.path_sandbox import PathSandbox, PathSandboxError
        sandbox = PathSandbox(base_dirs=[self.test_dir])

        # Innerhalb des Workspace sollte klappen
        safe_file = Path(self.test_dir) / "safe.txt"
        safe_file.write_text("test")
        result = sandbox.resolve(str(safe_file), must_exist=True)
        assert result is not None

        # Außerhalb des Workspace sollte scheitern
        with pytest.raises(PathSandboxError):
            sandbox.resolve("/etc/passwd", must_exist=False)


def test_execute_tool_sandbox_error_requests_allowlist_extension():
    """Sandbox-Fehler sollen eine strukturierte Freigabe-Anfrage enthalten."""
    toolkit = AgentToolkit.__new__(AgentToolkit)
    toolkit._refresh_runtime_mode = lambda: None

    def _raise_sandbox(_params):
        raise PathSandboxError("Pfad ausserhalb der Sandbox")

    toolkit._file_reader = cast(Any, _raise_sandbox)

    result = toolkit.execute_tool("file_reader", {"file_path": "C:/Dokumente/geladen_chat.pdf"})

    assert result["success"] is False
    assert result["error_class"] == "sandbox_violation"
    assert result["needs_user_permission"] is True
    assert result["permission_action"] == "allowlist_extend_or_temp_grant"
    assert result["requested_path"] == "C:/Dokumente/geladen_chat.pdf"
    assert result["ingest_policy"] == "stream_only_no_rag"


def test_grant_pending_path_access_temporary_extends_sandbox(tmp_path: Path):
    """Temporäre Freigabe erweitert die Sandbox-Basisverzeichnisse tatsächlich."""
    target_dir = tmp_path / "external"
    target_dir.mkdir(parents=True, exist_ok=True)

    toolkit = AgentToolkit.__new__(AgentToolkit)
    toolkit._default_base_dirs = [str(tmp_path)]
    toolkit._temporary_allowlist_dirs = []
    toolkit._persistent_allowlist_dirs = []
    toolkit._allowlist_state_file = tmp_path / "config" / "path_allowlist.json"
    toolkit._pending_permission_request = {
        "requested_path": str(target_dir / "doc.pdf"),
        "tool": "pdf_extract",
    }

    from agent.path_sandbox import PathSandbox
    toolkit.path_sandbox = PathSandbox(base_dirs=[str(tmp_path)])

    result = toolkit.grant_pending_path_access(mode="temporary")

    assert result["success"] is True
    assert result["grant_mode"] == "temporary"
    assert str(target_dir.resolve()) in result["base_dirs"]


def test_file_reader_marks_stream_only_and_no_rag(tmp_path: Path):
    """Datei-Reads über Tooling sind explizit stream-only und nicht RAG-persistent."""
    sample = tmp_path / "note.txt"
    sample.write_text("hello", encoding="utf-8")

    toolkit = AgentToolkit.__new__(AgentToolkit)
    from agent.path_sandbox import PathSandbox
    toolkit.path_sandbox = PathSandbox(base_dirs=[str(tmp_path)])

    result = toolkit._file_reader({"file_path": str(sample)})

    assert result["success"] is True
    assert result["ingest_policy"] == "stream_only_no_rag"
    assert result["rag_ingest"] is False


def test_execute_tool_list_directory_returns_structured_entries(tmp_path: Path):
    """list_directory über execute_tool liefert strukturierte Einträge ohne Attributfehler."""
    d = tmp_path / "demo"
    d.mkdir(parents=True, exist_ok=True)
    (d / "a.txt").write_text("hi", encoding="utf-8")

    toolkit = AgentToolkit.__new__(AgentToolkit)
    toolkit._refresh_runtime_mode = lambda: None

    from agent.path_sandbox import PathSandbox
    toolkit.path_sandbox = PathSandbox(base_dirs=[str(tmp_path)])

    result = toolkit.execute_tool("list_directory", {"path": str(d), "max_depth": 1})

    assert result["success"] is True
    assert result["count"] >= 1
    assert isinstance(result["entries"], list)
    assert "name" in result["entries"][0]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])