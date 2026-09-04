"""File Reader Safety Tests (P0 Context-Safety, 2026-08-24)
=============================================================

Regression-Tests für den P0-Context-Overflow-Fix:
`file_reader` nutzt jetzt `PathSandbox.read_file_safe` mit Hard-Char-Limit
(50.000 Zeichen Default), Binary-Check und Trunkierung-Metadaten.

Früher (Produktions-Bug): `read_text()` hatte KEIN Char-Limit — große
Dateien liefen ungekürzt in den LLM-Kontext (32K-token-Fenster) und
erzeugten Context-Bloat / Context-Rot.

SOTA-Bezug: Anthropic Tool-Context-Management — begrenzte Tool-Results,
ehrliche Trunkierung-Signale, kein stiller Kontext-Overflow.
"""

import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.path_sandbox import PathSandbox
from agent_toolkit import AgentToolkit


def _make_toolkit(sandbox_root: str) -> AgentToolkit:
    """Leichtgewichtiges Toolkit mit Test-Sandbox (ohne __init__-Overhead)."""
    toolkit = AgentToolkit.__new__(AgentToolkit)
    toolkit.path_sandbox = PathSandbox(base_dirs=[sandbox_root])
    return toolkit


class TestFileReaderSafety:
    """P0: file_reader darf nie ungetrunktet in den Kontext liefern."""

    def setup_method(self):
        self.test_dir = tempfile.mkdtemp(prefix="fs_reader_safety_")
        self.toolkit = _make_toolkit(self.test_dir)

    def teardown_method(self):
        if Path(self.test_dir).exists():
            shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_large_file_truncated_and_flagged(self):
        """60k-Zeichen-Datei wird auf 50k Zeichen gekürzt und flaggt."""
        big = Path(self.test_dir) / "big.txt"
        big.write_text("a" * 60_000, encoding="utf-8")

        result = self.toolkit._file_reader({"file_path": str(big)})

        assert result["success"] is True
        assert len(result["content"]) == 50_000
        assert result["size"] == 50_000
        assert result["was_truncated"] is True
        assert result["total_chars"] == 60_000
        assert result["truncated_at"] == 50_000
        assert result["suggested_action"]  # nicht leer

    def test_small_file_not_truncated(self):
        """Kleine Datei: kompletter Inhalt, was_truncated=False."""
        small = Path(self.test_dir) / "small.txt"
        small.write_text("hallo welt", encoding="utf-8")

        result = self.toolkit._file_reader({"file_path": str(small)})

        assert result["success"] is True
        assert result["content"] == "hallo welt"
        assert result["was_truncated"] is False
        assert result["total_chars"] == 10

    def test_file_exactly_at_limit_not_truncated(self):
        """Genau 50.000 Zeichen → KEINE Trunkierung (striktes >-Semantik)."""
        edge = Path(self.test_dir) / "edge.txt"
        edge.write_text("b" * 50_000, encoding="utf-8")

        result = self.toolkit._file_reader({"file_path": str(edge)})

        assert result["success"] is True
        assert len(result["content"]) == 50_000
        assert result["was_truncated"] is False
        assert result["total_chars"] == 50_000
        assert "truncated_at" not in result
        assert "suggested_action" not in result

    def test_binary_rejected(self):
        """Binärdatei wird abgelehnt (Binary-Check bleibt erhalten)."""
        exe = Path(self.test_dir) / "prog.exe"
        exe.write_bytes(b"MZ\x90\x00\x03\x00\x00\x00\x04\x00" + b"\x00" * 32)

        result = self.toolkit._file_reader({"file_path": str(exe)})

        assert result["success"] is False
        assert result["error_class"] == "sandbox_error"

    def test_sandbox_escape_blocked(self):
        """Pfad außerhalb der Sandbox → sandbox_error + Freigabe-Anfrage."""
        outside = tempfile.NamedTemporaryFile(
            prefix="fs_reader_escape_", suffix=".txt", delete=False
        )
        try:
            outside.write(b"secret")
            outside.close()
            result = self.toolkit._file_reader({"file_path": outside.name})
            assert result["success"] is False
            assert result["error_class"] == "sandbox_error"
            assert result.get("needs_user_permission") is True
        finally:
            Path(outside.name).unlink(missing_ok=True)

    def test_invalid_utf8_decoded_with_replacement(self):
        """Ungültiges UTF-8 wird ersetzt (errors='replace'), statt encoding_error."""
        weird = Path(self.test_dir) / "weird.txt"
        # Ungültiges UTF-8 OHNE NUL-Bytes (sonst greift die Binary-Erkennung,
        # wie in test_binary_rejected erwartet):
        weird.write_bytes(b"\xff\xfe Latin1-Term \xc3\x28")

        result = self.toolkit._file_reader({"file_path": str(weird)})

        assert result["success"] is True
        assert "\ufffd" in result["content"]  # U+FFFD Replacement-Char
        assert result["was_truncated"] is False

    def test_contract_keys_preserved(self):
        """Bestehender Ergebnis-Vertrag bleibt stabil (agent_chatbot_logic liest 'content')."""
        small = Path(self.test_dir) / "contract.txt"
        small.write_text("ok", encoding="utf-8")

        result = self.toolkit._file_reader({"file_path": str(small)})

        for key in (
            "success", "file_path", "content", "size",
            "ingest_policy", "rag_ingest", "message",
        ):
            assert key in result, f"Vertrags-Key fehlt: {key}"
        assert result["ingest_policy"] == "stream_only_no_rag"
        assert result["rag_ingest"] is False
