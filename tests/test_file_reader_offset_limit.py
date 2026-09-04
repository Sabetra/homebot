"""P1: file_reader offset/limit — Zeilenfenster-Reads (2026-08-24)
===================================================================

Regression-Tests für die P1-Kontext-Navigation (Claude Code Read-Modell):

* `PathSandbox.read_file_safe(..., offset=1, limit=2000)` liest ein
  1-basiertes Zeilenfenster statt die ersten N Zeichen blind abzuschneiden.
* Das Ergebnis-5-tuple enthält ehrliche Line-Metadaten
  (total_lines, start_line, end_line, has_more_lines, next_offset).
* `AgentToolkit._file_reader` gibt `offset`/`limit` (LLM-Tool-Parameter)
  durch und liefert Navigations-Hinweise im Tool-Result.
* Das P0-Char-Backstop (50.000 Zeichen) bleibt für sehr lange Zeilen aktiv.

Referenz: docs/WORKDOC_FILESYSTEM_CONTEXT_SAFETY_20260824.md (§ P1)
"""

import shutil
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.path_sandbox import (
    DEFAULT_READ_LINE_LIMIT,
    PathSandbox,
    PathSandboxError,
)
from agent.tool_schemas import get_tool_schema_by_name
from agent_toolkit import AgentToolkit


def _make_toolkit(sandbox_root: str) -> AgentToolkit:
    """Leichtgewichtiges Toolkit mit Test-Sandbox (ohne __init__-Overhead)."""
    toolkit = AgentToolkit.__new__(AgentToolkit)
    toolkit.path_sandbox = PathSandbox(base_dirs=[sandbox_root])
    return toolkit


class TestReadFileSafeLineWindow:
    """Sandbox-Ebene: read_file_safe offset/limit + Line-Metadaten."""

    def setup_method(self):
        self.test_dir = Path(tempfile.mkdtemp(prefix="fs_p1_window_"))
        self.sandbox = PathSandbox(base_dirs=[str(self.test_dir)])

    def teardown_method(self):
        if self.test_dir.exists():
            shutil.rmtree(self.test_dir, ignore_errors=True)

    def _write_lines(self, name: str, n: int) -> Path:
        """n Zeilen 'linie-<i>' schreiben und Pfad zurückgeben."""
        p = self.test_dir / name
        p.write_text("\n".join(f"linie-{i}" for i in range(1, n + 1)), encoding="utf-8")
        return p

    def test_full_read_small_file(self):
        """Kleine Datei: komplettes Lesen, 5-tuple + saubere Metadaten."""
        p = self._write_lines("small.txt", 5)

        path, content, was_truncated, total_chars, line_meta = self.sandbox.read_file_safe(str(p))

        assert Path(path) == p
        assert was_truncated is False
        assert content == "\n".join(f"linie-{i}" for i in range(1, 6))
        assert line_meta["total_lines"] == 5
        assert line_meta["start_line"] == 1
        assert line_meta["end_line"] == 5
        assert line_meta["has_more_lines"] is False
        assert line_meta["next_offset"] == 6

    def test_window_offset_limit(self):
        """Fenster offset=3 limit=4 → genau Zeilen 3–6, has_more=True."""
        p = self._write_lines("ten.txt", 10)

        _, content, _, _, meta = self.sandbox.read_file_safe(str(p), offset=3, limit=4)

        assert content.splitlines() == ["linie-3", "linie-4", "linie-5", "linie-6"]
        assert meta["total_lines"] == 10
        assert meta["start_line"] == 3
        assert meta["end_line"] == 6
        assert meta["has_more_lines"] is True
        assert meta["next_offset"] == 7

    def test_continuation_with_next_offset(self):
        """Weiterlesen mit offset=next_offset liefert den Rest exakt."""
        p = self._write_lines("ten.txt", 10)

        first = self.sandbox.read_file_safe(str(p), offset=3, limit=4)
        second = self.sandbox.read_file_safe(str(p), offset=first[4]["next_offset"], limit=4)

        assert first[1].splitlines() == [f"linie-{i}" for i in range(3, 7)]
        assert second[1].splitlines() == [f"linie-{i}" for i in range(7, 11)]
        assert second[4]["has_more_lines"] is False

    def test_limit_one(self):
        """limit=1 liefert exakt eine Zeile."""
        p = self._write_lines("many.txt", 50)

        _, content, _, _, meta = self.sandbox.read_file_safe(str(p), offset=10, limit=1)

        assert content == "linie-10"
        assert meta["start_line"] == 10
        assert meta["end_line"] == 10
        assert meta["has_more_lines"] is True
        assert meta["next_offset"] == 11

    def test_offset_beyond_eof_honest_empty(self):
        """Offset über Dateiende: leerer Inhalt + ehrliche Metadaten (kein Crash)."""
        p = self._write_lines("five.txt", 5)

        path, content, was_truncated, total_chars, meta = self.sandbox.read_file_safe(str(p), offset=99)

        assert content == ""
        assert was_truncated is False
        # Gesamtgröße = 5 Zeilen à 7 Zeichen + 4 Newlines
        assert total_chars == 5 * len("linie-1") + 4
        assert meta["total_lines"] == 5
        assert meta["start_line"] == 99
        assert meta["end_line"] == 0
        assert meta["has_more_lines"] is False
        assert meta["next_offset"] == 99

    def test_offset_zero_clamped_to_one(self):
        """offset=0 → 1-basiert geclamped (kein silent Fehlverhalten)."""
        p = self._write_lines("five.txt", 5)

        _, _, _, _, meta = self.sandbox.read_file_safe(str(p), offset=0, limit=2)

        assert meta["start_line"] == 1
        assert meta["end_line"] == 2

    def test_limit_zero_treated_as_one(self):
        """limit=0 → 1 Zeile (Minimum 1)."""
        p = self._write_lines("five.txt", 5)

        _, content, _, _, meta = self.sandbox.read_file_safe(str(p), limit=0)

        assert content == "linie-1"
        assert meta["end_line"] == 1

    def test_default_limit_constant(self):
        """Standard-Limit ist die Dokument-Konstante (2.000 Zeilen)."""
        assert DEFAULT_READ_LINE_LIMIT == 2000

    def test_empty_file(self):
        """Leere Datei: success, leerer Inhalt, total_lines=0."""
        p = self.test_dir / "empty.txt"
        p.write_text("", encoding="utf-8")

        _, content, was_truncated, total_chars, meta = self.sandbox.read_file_safe(str(p))

        assert content == ""
        assert was_truncated is False
        assert total_chars == 0
        assert meta["total_lines"] == 0
        assert meta["has_more_lines"] is False

    def test_crlf_line_cleaning(self):
        """CRLF-Dateien: Zeilen ohne Rest-\\r, korrekte Zeilenzahl."""
        p = self.test_dir / "crlf.txt"
        p.write_bytes(b"alpha\r\nbeta\r\ngamma\r\n")

        _, content, _, _, meta = self.sandbox.read_file_safe(str(p))

        assert "\r" not in content
        assert content.splitlines() == ["alpha", "beta", "gamma"]
        assert meta["total_lines"] == 3
        assert meta["end_line"] == 3

    def test_char_backstop_long_single_line(self):
        """P0-Backstop: 80k-Zeichen-Einzelzeile wird auf max_read_chars gekürzt."""
        p = self.test_dir / "long.txt"
        p.write_text("x" * 80_000, encoding="utf-8")

        _, content, was_truncated, total_chars, meta = self.sandbox.read_file_safe(str(p))

        assert was_truncated is True
        assert len(content) == 50_000
        assert total_chars == 80_000
        assert meta["total_lines"] == 1
        assert meta["has_more_lines"] is False

    def test_window_at_file_end(self):
        """Fenster am Dateiende: end_line = total_lines, has_more=False."""
        p = self._write_lines("twenty.txt", 20)

        _, content, _, _, meta = self.sandbox.read_file_safe(str(p), offset=19, limit=5)

        assert content.splitlines() == ["linie-19", "linie-20"]
        assert meta["end_line"] == 20
        assert meta["has_more_lines"] is False
        assert meta["next_offset"] == 21

    def test_binary_detection_preserved(self):
        """Binary-Check bleibt auch mit offset/limit aktiv."""
        p = self.test_dir / "bin.dat"
        p.write_bytes(b"\x00\x01\x02\x03" * 10)

        with pytest.raises(PathSandboxError):
            self.sandbox.read_file_safe(str(p), offset=1, limit=10)

    def test_oversize_byte_guard_preserved(self):
        """P0-Byte-Guard (>20MB) bleibt auch mit Zeilenfenster aktiv."""
        # 100 Zeilen à 300 KB → 30 MB, aber nur 100 Zeilen.
        p = self.test_dir / "huge.txt"
        with p.open("wb") as fh:
            for i in range(100):
                fh.write(f"zeile-{i}:".encode() + b"a" * 300_000 + b"\n")

        with pytest.raises(PathSandboxError, match="größer als"):
            self.sandbox.read_file_safe(str(p), offset=1, limit=1)

    def test_sandbox_escape_preserved(self):
        """Sandbox-Escape bleibt mit offset/limit blockiert."""
        outside = Path(tempfile.mkdtemp(prefix="fs_p1_outside_"))
        (outside / "secret.txt").write_text("secret", encoding="utf-8")
        try:
            with pytest.raises(PathSandboxError):
                self.sandbox.read_file_safe(str(outside / "secret.txt"), offset=1)
        finally:
            shutil.rmtree(outside, ignore_errors=True)


class TestFileReaderToolkitOffsetLimit:
    """Toolkit-Ebene: _file_reader offset/limit-Durchreichung + Ergebnis-Vertrag."""

    def setup_method(self):
        self.test_dir = Path(tempfile.mkdtemp(prefix="fs_p1_toolkit_"))
        self.toolkit = _make_toolkit(str(self.test_dir))

    def teardown_method(self):
        if self.test_dir.exists():
            shutil.rmtree(self.test_dir, ignore_errors=True)

    def _write_lines(self, name: str, n: int) -> Path:
        p = self.test_dir / name
        p.write_text("\n".join(f"zeile-{i}" for i in range(1, n + 1)), encoding="utf-8")
        return p

    def test_result_contains_line_metadata(self):
        """Tool-Result enthält alle P1-Metadaten-Keys."""
        p = self._write_lines("meta.txt", 8)

        result = self.toolkit._file_reader({"file_path": str(p)})

        assert result["success"] is True
        for key in ("total_lines", "start_line", "end_line", "has_more_lines", "next_offset"):
            assert key in result, f"Metadaten-Key fehlt: {key}"
        assert result["total_lines"] == 8
        assert result["start_line"] == 1
        assert result["end_line"] == 8
        assert result["has_more_lines"] is False
        assert result["next_offset"] == 9
        assert "Zeilen 1–8 von 8" in result["message"]

    def test_window_read_navigation_hint(self):
        """Teilweises Lesen → suggested_action mit konkretem next_offset."""
        p = self._write_lines("nav.txt", 100)

        result = self.toolkit._file_reader({"file_path": str(p), "offset": 1, "limit": 10})

        assert result["success"] is True
        assert result["start_line"] == 1
        assert result["end_line"] == 10
        assert result["has_more_lines"] is True
        assert result["next_offset"] == 11
        assert "offset=11" in result["suggested_action"]

    def test_full_read_no_suggested_action(self):
        """P0-Vertrag: sauber komplett gelesen → kein suggested_action, kein truncated_at."""
        p = self._write_lines("clean.txt", 20)

        result = self.toolkit._file_reader({"file_path": str(p)})

        assert result["success"] is True
        assert result["was_truncated"] is False
        assert "suggested_action" not in result
        assert "truncated_at" not in result

    def test_numeric_string_params_accepted(self):
        """LLM-typische numeric Strings ("5") werden zu int coerziert."""
        p = self._write_lines("str.txt", 20)

        result = self.toolkit._file_reader({"file_path": str(p), "offset": "5", "limit": "3"})

        assert result["success"] is True
        assert result["start_line"] == 5
        assert result["end_line"] == 7
        assert "zeile-5" in result["content"]
        assert "zeile-7" in result["content"]
        assert "zeile-8" not in result["content"]

    def test_non_numeric_offset_explicit_error(self):
        """Nicht-konvertierbarer offset → expliziter invalid_params-Fehler."""
        p = self._write_lines("bad.txt", 5)

        result = self.toolkit._file_reader({"file_path": str(p), "offset": "abc"})

        assert result["success"] is False
        assert result["error_class"] == "invalid_params"
        assert "Ganzzahlen" in result["error"]

    def test_non_numeric_limit_explicit_error(self):
        """Nicht-konvertierbarer limit → expliziter invalid_params-Fehler."""
        p = self._write_lines("bad2.txt", 5)

        result = self.toolkit._file_reader({"file_path": str(p), "limit": "viel"})

        assert result["success"] is False
        assert result["error_class"] == "invalid_params"

    def test_missing_file_path_rejected(self):
        """Fehlende file_path → invalid_params (klare Fehlermeldung)."""
        result = self.toolkit._file_reader({"offset": 1})

        assert result["success"] is False
        assert result["error_class"] == "invalid_params"
        assert "file_path" in result["error"]

    def test_offset_beyond_eof_guided(self):
        """Offset über Dateiende: leerer Inhalt + Navigationshinweis."""
        p = self._write_lines("eof.txt", 10)

        result = self.toolkit._file_reader({"file_path": str(p), "offset": 500})

        assert result["success"] is True
        assert result["content"] == ""
        assert result["total_lines"] == 10
        assert result["has_more_lines"] is False
        assert "offset ≤ 10" in result["suggested_action"]
        assert "über Dateiende" in result["message"]

    def test_p0_truncation_contract_preserved(self):
        """P0-Regression: 60k-Zeichen-Datei → 50k + truncated_at=50000."""
        big = self.test_dir / "big.txt"
        big.write_text("a" * 60_000, encoding="utf-8")

        result = self.toolkit._file_reader({"file_path": str(big)})

        assert result["success"] is True
        assert len(result["content"]) == 50_000
        assert result["was_truncated"] is True
        assert result["total_chars"] == 60_000
        assert result["truncated_at"] == 50_000
        assert "Char-Backstop" in result["suggested_action"]


class TestFileReaderSchema:
    """Tool-Schema: LLM kennt offset/limit."""

    def test_schema_has_offset_and_limit(self):
        schema = get_tool_schema_by_name("file_reader")
        assert schema is not None
        props = schema["function"]["parameters"]["properties"]

        assert "offset" in props
        assert "limit" in props
        assert props["offset"]["type"] == "integer"
        assert props["limit"]["type"] == "integer"
        assert props["offset"].get("default") == 1
        assert props["limit"].get("default") == 2000
        assert props["offset"].get("minimum") == 1
        assert props["limit"].get("minimum") == 1
        # file_path bleibt der einzige Pflichtparameter
        assert schema["function"]["parameters"]["required"] == ["file_path"]