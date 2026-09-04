"""Tests für die ripgrep-basierte Content-Suche (P2, 2026-08-25).

Abgedeckt (Workdoc-DoD #5: Cap 50 / Timeout 10s / partial / timed_out):
- PathSandbox.search_content_rg:
    Basic-Match, Case-Sensitivity, fixed_string, hidden, glob,
    max_results-Truncation (inkl. Default-Cap 50), Context-Zeilen,
    Invalid-Regex, rg-fehlt (RipgrepNotFoundError), Sandbox-Denial,
    Timeout-Partial (deterministisch via subprocess.run-Mock)
- AgentToolkit._search_files:
    Content-Default (rg), Name-Modus, Missing-Parameter, Invalid-Regex,
    Sandbox-Denial + Permission-Shape (execute_tool-Dispatch),
    Python-Fallback (rg fehlt), Timeout-Partial
"""

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

# Ensure agent module is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import agent.path_sandbox as path_sandbox_mod
from agent.path_sandbox import (
    PathSandbox,
    PathSandboxError,
    RipgrepNotFoundError,
    rg_bin,
)
from agent_toolkit import AgentToolkit

RG_AVAILABLE = rg_bin() is not None

skipif_no_rg = pytest.mark.skipif(
    not RG_AVAILABLE, reason="ripgrep (rg) nicht installiert"
)


def _write(base: str, name: str, content: str) -> str:
    """Textdatei unter base/name schreiben (Unterordner erlaubt)."""
    p = Path(base) / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return str(p)


def _make_toolkit(base_dirs: list) -> AgentToolkit:
    """Toolkit ohne __init__ (Isolation, Muster wie test_filesystem_tools_integration)."""
    toolkit = AgentToolkit.__new__(AgentToolkit)
    toolkit.path_sandbox = PathSandbox(base_dirs=base_dirs)
    toolkit._refresh_runtime_mode = lambda: None
    return toolkit


class _SandboxFixture:
    """Gemeinsames Setup für Sandbox- und Toolkit-Tests."""

    def setup_method(self):
        self.test_dir = tempfile.mkdtemp(prefix="search_rg_test_")
        self.sandbox = PathSandbox(base_dirs=[self.test_dir])
        self.toolkit = _make_toolkit([self.test_dir])

    def teardown_method(self):
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir, ignore_errors=True)


# ======================================================================
# PathSandbox.search_content_rg (rg-Backend)
# ======================================================================

class TestSearchContentRg(_SandboxFixture):
    """Sandbox-Ebene: rg-Kommando, Parsing, Caps, Fehler-Pfade."""

    def test_basic_match_shape(self):
        """Treffer liefern das dokumentierte Match-Schema."""
        _write(self.test_dir, "hello.txt", "hello world\nsecond line\n")
        res = self.sandbox.search_content_rg(self.test_dir, "hello")

        assert res["backend"] == "ripgrep"
        assert res["count"] == 1
        assert res["truncated"] is False
        assert res["timed_out"] is False
        assert res["error"] is None
        assert isinstance(res["elapsed_ms"], int)

        m = res["matches"][0]
        assert set(m) == {"path", "line", "text", "context_before", "context_after"}
        assert m["line"] == 1
        assert m["text"].startswith("hello world")
        assert str(self.test_dir) in m["path"]
        assert Path(m["path"]).is_file()

    def test_case_insensitive_default_and_explicit(self):
        """Default: case-insensitive; case_insensitive=False ist exakt."""
        _write(self.test_dir, "case.txt", "Hello there\n")

        assert self.sandbox.search_content_rg(
            self.test_dir, "hello"
        )["count"] == 1
        assert self.sandbox.search_content_rg(
            self.test_dir, "HELLO"
        )["count"] == 1  # Default: -i
        assert self.sandbox.search_content_rg(
            self.test_dir, "HELLO", case_sensitive=True
        )["count"] == 0
        assert self.sandbox.search_content_rg(
            self.test_dir, "Hello", case_sensitive=True
        )["count"] == 1

    def test_fixed_string_literal(self):
        """fixed_string=True: Metazeichen werden wörtlich behandelt (-F)."""
        _write(self.test_dir, "fix.txt", "aXb\na.b\n")

        # Regex-Modus: "a.b" trifft aXb UND a.b
        regex_res = self.sandbox.search_content_rg(self.test_dir, "a.b")
        assert regex_res["count"] == 2

        # Literal-Modus: nur die exakte Zeichenfolge "a.b"
        literal_res = self.sandbox.search_content_rg(
            self.test_dir, "a.b", fixed_string=True
        )
        assert literal_res["count"] == 1
        assert "a.b" in literal_res["matches"][0]["text"]

    def test_hidden_excluded_by_default_included_with_flag(self):
        """Dotfiles standardmäßig aus (z.B. .env), hidden=True einbeziehen."""
        _write(self.test_dir, ".hidden_secret.txt", "hidden-needle\n")

        default_res = self.sandbox.search_content_rg(self.test_dir, "hidden-needle")
        assert default_res["count"] == 0

        hidden_res = self.sandbox.search_content_rg(
            self.test_dir, "hidden-needle", hidden=True
        )
        assert hidden_res["count"] == 1

    def test_glob_filter(self):
        """glob=*.py schränkt die Suchmenge ein."""
        _write(self.test_dir, "a.txt", "globtest\n")
        _write(self.test_dir, "b.py", "globtest\n")

        all_res = self.sandbox.search_content_rg(self.test_dir, "globtest")
        assert all_res["count"] == 2

        py_res = self.sandbox.search_content_rg(
            self.test_dir, "globtest", glob="*.py"
        )
        assert py_res["count"] == 1
        assert py_res["matches"][0]["path"].endswith("b.py")

    def test_max_results_truncation(self):
        """max_results-Cap wird durchgesetzt und als truncated markiert."""
        lines = "\n".join(f"needle {i}" for i in range(100))
        _write(self.test_dir, "many.txt", lines + "\n")

        res = self.sandbox.search_content_rg(
            self.test_dir, "needle", max_results=5
        )
        assert res["count"] == 5
        assert res["truncated"] is True

    def test_default_cap_50(self):
        """DoD-Default: DEFAULT_RG_MAX_RESULTS=50 ohne expliziten Parameter."""
        lines = "\n".join(f"needle {i}" for i in range(60))
        _write(self.test_dir, "many.txt", lines + "\n")

        res = self.sandbox.search_content_rg(self.test_dir, "needle")
        assert res["count"] == 50
        assert res["truncated"] is True

    def test_context_lines(self):
        """context=2 liefert before/after-Kontextzeilen pro Treffer."""
        _write(self.test_dir, "ctx.txt", "aaa\nbbb\nneedle\nddd\neee\n")

        res = self.sandbox.search_content_rg(
            self.test_dir, "needle", context=2
        )
        assert res["count"] == 1
        m = res["matches"][0]
        assert m["context_before"] == ["aaa", "bbb"]
        assert m["context_after"] == ["ddd", "eee"]

    def test_invalid_regex_reports_error_class(self):
        """Ungültige Regex (rg exit 2) → strukturierte invalid_regex-Fehler."""
        _write(self.test_dir, "x.txt", "anything\n")
        res = self.sandbox.search_content_rg(self.test_dir, "(?:")

        assert res["count"] == 0
        assert res["error"]
        assert res["error_class"] == "invalid_regex"

    def test_empty_pattern_raises(self):
        """Leeres Pattern ist ein Validierungsfehler (ValueError)."""
        with pytest.raises(ValueError):
            self.sandbox.search_content_rg(self.test_dir, "")

    def test_sandbox_denied_outside_root(self):
        """Root außerhalb der Sandbox → PathSandboxError (vor dem Spawn)."""
        outside = tempfile.mkdtemp(prefix="search_rg_outside_")
        try:
            _write(outside, "secret.txt", "geheim\n")
            with pytest.raises(PathSandboxError):
                self.sandbox.search_content_rg(outside, "geheim")
        finally:
            shutil.rmtree(outside, ignore_errors=True)

    def test_rg_missing_raises_explicit_error(self, monkeypatch):
        """rg fehlt → RipgrepNotFoundError (Trigger für Toolkit-Fallback)."""
        _write(self.test_dir, "x.txt", "anything\n")
        monkeypatch.setattr(path_sandbox_mod, "rg_bin", lambda: None)
        with pytest.raises(RipgrepNotFoundError):
            self.sandbox.search_content_rg(self.test_dir, "anything")

    def test_timeout_partial_results(self, monkeypatch):
        """Timeout: Partial-Output bleibt erhalten, timed_out/truncated=True.

        Deterministisch: subprocess.run wirft TimeoutExpired mit einem
        abgeschnittenen --json-Stream (1 gültige + 1 unvollständige Zeile).
        """
        _write(self.test_dir, "a.txt", "needle one\nneedle two\n")
        partial = (
            b'{"type":"begin","data":{"path":{"text":"a.txt"}}}\n'
            b'{"type":"match","data":{"path":{"text":"a.txt"},'
            b'"lines":{"text":"needle one\\n"},"line_number":1}}\n'
            b'{"type":"match","data":{"path":{"text":"a.txt"},"lines":{"text":"nee'
        )

        def _timeout(*args, **kwargs):
            raise subprocess.TimeoutExpired("rg", 0.01, output=partial, stderr=b"")

        monkeypatch.setattr(subprocess, "run", _timeout)
        res = self.sandbox.search_content_rg(self.test_dir, "needle")

        assert res["timed_out"] is True
        assert res["truncated"] is True
        assert res["error"] is None
        assert res["count"] == 1
        assert "needle one" in res["matches"][0]["text"]


# ======================================================================
# AgentToolkit._search_files (Tool-Ebene)
# ======================================================================

class TestSearchFilesToolkit(_SandboxFixture):
    """Tool-Ebene: Parameter-Handling, Modi, Fallback, Permission-Shape."""

    @skipif_no_rg
    def test_content_search_is_default_rg_backend(self):
        """Content-Suche ist Default; Backend=ripgrep, Stream-only."""
        _write(self.test_dir, "hello.txt", "hello world\n")
        result = self.toolkit._search_files(
            {"root_path": self.test_dir, "pattern": "hello"}
        )

        assert result["success"] is True
        assert result["mode"] == "content"
        assert result["backend"] == "ripgrep"
        assert result["count"] == 1
        assert result["truncated"] is False
        assert result["timed_out"] is False
        assert result["results"][0]["text"].startswith("hello world")
        assert result["ingest_policy"] == "stream_only_no_rag"
        assert result["rag_ingest"] is False
        assert "hello" in result["message"]

    @skipif_no_rg
    def test_case_insensitive_param(self):
        _write(self.test_dir, "c.txt", "Hello there\n")
        insens = self.toolkit._search_files(
            {"root_path": self.test_dir, "pattern": "HELLO"}
        )
        assert insens["success"] is True
        assert insens["count"] == 1

        sens = self.toolkit._search_files(
            {
                "root_path": self.test_dir,
                "pattern": "HELLO",
                "case_insensitive": False,
            }
        )
        assert sens["success"] is True
        assert sens["count"] == 0

    @skipif_no_rg
    def test_fixed_string_param(self):
        _write(self.test_dir, "fix.txt", "aXb\na.b\n")
        literal = self.toolkit._search_files(
            {
                "root_path": self.test_dir,
                "pattern": "a.b",
                "fixed_string": True,
            }
        )
        assert literal["success"] is True
        assert literal["count"] == 1

    @skipif_no_rg
    def test_max_results_param(self):
        lines = "\n".join(f"needle {i}" for i in range(100))
        _write(self.test_dir, "many.txt", lines + "\n")
        result = self.toolkit._search_files(
            {
                "root_path": self.test_dir,
                "pattern": "needle",
                "max_results": 10,
            }
        )
        assert result["success"] is True
        assert result["count"] == 10
        assert result["truncated"] is True
        assert "Cap" in result["message"]

    @skipif_no_rg
    def test_invalid_regex_structured_error(self):
        result = self.toolkit._search_files(
            {"root_path": self.test_dir, "pattern": "(?:"}
        )
        assert result["success"] is False
        assert result["error_class"] == "invalid_regex"
        assert result["error"]

    def test_missing_root_parameter(self):
        result = self.toolkit._search_files({"pattern": "hello"})
        assert result["success"] is False
        assert result["error_class"] == "missing_parameter"

    def test_empty_pattern_parameter(self):
        result = self.toolkit._search_files(
            {"root_path": self.test_dir, "pattern": "   "}
        )
        assert result["success"] is False
        assert result["error_class"] == "missing_parameter"

    def test_name_mode_python_backend(self):
        """content_search=False bleibt der sichere Name-Modus (Python)."""
        _write(self.test_dir, "report_2026.txt", "inhalt egal\n")
        result = self.toolkit._search_files(
            {
                "root_path": self.test_dir,
                "pattern": r"report_\d+",
                "content_search": False,
            }
        )
        assert result["success"] is True
        assert result["mode"] == "name"
        assert result["backend"] == "python"
        assert result["count"] == 1
        assert result["results"][0]["name"] == "report_2026.txt"
        assert result["results"][0]["match_type"] == "name"

    def test_name_mode_invalid_regex(self):
        result = self.toolkit._search_files(
            {
                "root_path": self.test_dir,
                "pattern": "(?:",
                "content_search": False,
            }
        )
        assert result["success"] is False
        assert result["error_class"] == "invalid_pattern"

    def test_sandbox_denied_permission_shape(self):
        """Denial: success=False + needs_user_permission + Aktion + Pfad."""
        outside = tempfile.mkdtemp(prefix="search_rg_denied_")
        try:
            _write(outside, "secret.txt", "geheim\n")
            result = self.toolkit._search_files(
                {"root_path": outside, "pattern": "geheim"}
            )
            assert result["success"] is False
            assert result["error_class"] == "sandbox_error"
            assert result.get("needs_user_permission") is True
            assert result.get("permission_action") == "allowlist_extend_or_temp_grant"
            assert result.get("requested_path") == outside
        finally:
            shutil.rmtree(outside, ignore_errors=True)

    def test_execute_tool_search_files_sandbox_shape(self):
        """execute_tool-Dispatch liefert dieselbe Permission-Shape."""
        outside = tempfile.mkdtemp(prefix="search_rg_exec_")
        try:
            _write(outside, "secret.txt", "geheim\n")
            result = self.toolkit.execute_tool(
                "search_files", {"root_path": outside, "pattern": "geheim"}
            )
            assert result.get("error_class") == "sandbox_error"
            assert result.get("needs_user_permission") is True
            assert result.get("permission_action") == "allowlist_extend_or_temp_grant"
        finally:
            shutil.rmtree(outside, ignore_errors=True)

    def test_rg_missing_python_fallback(self, monkeypatch):
        """rg fehlt → expliziter, markierter Python-Fallback (kein Silent)."""
        _write(self.test_dir, "notes.txt", "fallback-needle hier\n")
        monkeypatch.setattr(path_sandbox_mod, "rg_bin", lambda: None)

        result = self.toolkit._search_files(
            {"root_path": self.test_dir, "pattern": "fallback-needle"}
        )
        assert result["success"] is True
        assert result["mode"] == "content"
        assert result["backend"] == "python-fallback"
        assert result["fallback_reason"]
        assert result["count"] >= 1
        # Treffer-Pfad muss die betroffene Datei enthalten (die Nadel ist im
        # DATEIINHALT, nicht im Pfad)
        assert any("notes.txt" in r["path"] for r in result["results"])

    def test_timeout_partial_toolkit(self, monkeypatch):
        """Timeout am Tool: success=True mit Partial-Treffern + Hinweis."""
        _write(self.test_dir, "a.txt", "needle one\nneedle two\n")
        partial = (
            b'{"type":"match","data":{"path":{"text":"a.txt"},'
            b'"lines":{"text":"needle one\\n"},"line_number":1}}\n'
        )

        def _timeout(*args, **kwargs):
            raise subprocess.TimeoutExpired("rg", 0.01, output=partial, stderr=b"")

        monkeypatch.setattr(subprocess, "run", _timeout)
        result = self.toolkit._search_files(
            {"root_path": self.test_dir, "pattern": "needle", "timeout": 0.01}
        )
        assert result["success"] is True
        assert result["timed_out"] is True
        assert result["truncated"] is True
        assert result["count"] == 1
        assert "Timeout" in result["message"]