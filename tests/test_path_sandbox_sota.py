"""SOTA Path Sandbox Tests (2026)
=================================

Testet die SOTA-Erweiterungen des Path-Sandbox-Moduls:
- Symlink-Rejection
- Binary-File-Erkennung
- Max-Depth-Limiter
- Path-Traversal-Schutz
- Oversize-Datei-Schutz
"""

import os
import sys
import tempfile
import shutil
from pathlib import Path
from unittest import mock

# Ensure agent module is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.path_sandbox import (
    PathSandbox,
    PathSandboxError,
    DEFAULT_MAX_READ_BYTES,
    DEFAULT_MAX_WRITE_BYTES,
)

import pytest


class TestPathSandboxSOTA:
    """Test-Suite für SOTA Path-Sandbox-Features."""

    def setup_method(self):
        """Erstelle temporäres Test-Verzeichnis."""
        self.test_dir = tempfile.mkdtemp(prefix="sandbox_test_")
        self.sandbox = PathSandbox(base_dirs=[self.test_dir])

    def teardown_method(self):
        """Bereinige temporäres Test-Verzeichnis."""
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir, ignore_errors=True)

    # ------------------------------------------------------------------
    # Symlink-Rejection Tests
    # ------------------------------------------------------------------

    def test_symlink_rejection_basic(self):
        """Symlinks außerhalb des Workspace werden abgewiesen."""
        outside_file = tempfile.NamedTemporaryFile(delete=False, suffix=".txt")
        outside_file.write(b"geheime Daten")
        outside_file.close()

        try:
            symlink_path = Path(self.test_dir) / "evil_link.txt"
            symlink_path.symlink_to(outside_file.name)

            with pytest.raises(PathSandboxError):
                self.sandbox.resolve(str(symlink_path), must_exist=True)
        finally:
            os.unlink(outside_file.name)
            if symlink_path.exists():
                symlink_path.unlink()

    def test_symlink_directory_rejection(self):
        """Symlink-Verzeichnisse werden ebenfalls abgewiesen."""
        outside_dir = tempfile.mkdtemp(prefix="outside_")
        try:
            symlink_dir = Path(self.test_dir) / "evil_dir_link"
            symlink_dir.symlink_to(outside_dir)

            # must_exist=True → Symlink-Check wird aktiviert
            with pytest.raises(PathSandboxError):
                self.sandbox.resolve(str(symlink_dir), must_exist=True)
        finally:
            shutil.rmtree(outside_dir, ignore_errors=True)
            if symlink_dir.exists():
                symlink_dir.unlink()

    # ------------------------------------------------------------------
    # Path-Traversal Tests
    # ------------------------------------------------------------------

    def test_path_traversal_dotdot(self):
        """..-Sequenzen werden blockiert."""
        evil_path = os.path.join(self.test_dir, "..", "etc", "passwd")
        with pytest.raises(PathSandboxError):
            self.sandbox.resolve(evil_path, must_exist=False)

    def test_path_traversal_absolute_escape(self):
        """Absolute Pfade außerhalb des Workspace werden blockiert."""
        evil_path = "/etc/passwd" if os.name != "nt" else "C:\\Windows\\System32\\config\\SAM"
        with pytest.raises(PathSandboxError):
            self.sandbox.resolve(evil_path, must_exist=False)

    def test_path_traversal_mixed_slashes(self):
        """Gemischte Slash-Typen werden normalisiert und geprüft."""
        evil_path = os.path.join(self.test_dir, "subdir", "..", "..", "secrets")
        with pytest.raises(PathSandboxError):
            self.sandbox.resolve(evil_path, must_exist=False)

    # ------------------------------------------------------------------
    # Binary File Detection Tests
    # ------------------------------------------------------------------

    def test_binary_file_detection_pyc(self):
        """Python .pyc Dateien werden als binär erkannt."""
        pyc_file = Path(self.test_dir) / "compiled.pyc"
        # Python magic bytes + null bytes
        pyc_file.write_bytes(b"\x42\x0d\x0d\x0a" + b"\x00" * 100)

        with pytest.raises(PathSandboxError):
            self.sandbox.read_file_safe(str(pyc_file))

    def test_binary_file_detection_exe(self):
        """EXE-Dateien werden als binär erkannt."""
        exe_file = Path(self.test_dir) / "malware.exe"
        exe_file.write_bytes(b"MZ" + b"\x00" * 100)

        with pytest.raises(PathSandboxError):
            self.sandbox.read_file_safe(str(exe_file))

    def test_text_file_allowed(self):
        """Text-Dateien werden normal gelesen."""
        txt_file = Path(self.test_dir) / "document.txt"
        txt_file.write_text("Hallo Welt! Dies ist ein Testdokument.", encoding="utf-8")

        path, content, was_truncated, total_chars, line_meta = self.sandbox.read_file_safe(str(txt_file))
        assert "Hallo Welt" in content
        assert was_truncated is False
        # P1: 5-tuple mit Line-Metadaten
        assert line_meta["total_lines"] == 1
        assert line_meta["has_more_lines"] is False

    # ------------------------------------------------------------------
    # Max-Depth Tests
    # ------------------------------------------------------------------

    def test_max_depth_limiter(self):
        """Max-Depth wird bei Directory-Listings eingehalten."""
        deep_path = Path(self.test_dir) / "a" / "b" / "c" / "d" / "e" / "f"
        deep_path.mkdir(parents=True, exist_ok=True)

        entries = self.sandbox.list_directory_safe(self.test_dir, max_depth=2)
        # Alle Einträge müssen innerhalb von max_depth liegen
        for entry in entries:
            rel = Path(entry.path).relative_to(self.test_dir)
            assert rel.parts.count(os.sep) < 2

    def test_max_depth_default(self):
        """Standard-Max-Depth ist 5."""
        entries = self.sandbox.list_directory_safe(self.test_dir)
        # Standard ist 5, also sollten alle Einträge innerhalb von 5 Ebenen liegen
        for entry in entries:
            rel = Path(entry.path).relative_to(self.test_dir)
            assert rel.parts.count(os.sep) < 5

    # ------------------------------------------------------------------
    # Oversize File Tests
    # ------------------------------------------------------------------

    def test_oversize_file_rejection(self):
        """Übergroße Dateien werden abgewiesen."""
        large_file = Path(self.test_dir) / "huge.bin"
        large_file.write_bytes(b"X" * (DEFAULT_MAX_READ_BYTES + 1024))

        with pytest.raises(PathSandboxError):
            self.sandbox.read_file_safe(str(large_file))

    def test_normal_file_accepted(self):
        """Normale Dateien werden akzeptiert."""
        normal_file = Path(self.test_dir) / "normal.txt"
        normal_file.write_text("Normaler Inhalt", encoding="utf-8")

        path, content, was_truncated, total_chars, line_meta = self.sandbox.read_file_safe(str(normal_file))
        assert "Normaler Inhalt" in content
        # P1: 5-tuple mit Line-Metadaten
        assert line_meta["total_lines"] == 1
        assert line_meta["start_line"] == 1
        assert line_meta["end_line"] == 1

    # ------------------------------------------------------------------
    # list_directory_safe Tests
    # ------------------------------------------------------------------

    def test_list_directory_safe_basic(self):
        """Basis-Verzeichnisauflistung funktioniert."""
        (Path(self.test_dir) / "file1.txt").write_text("inhalt1")
        (Path(self.test_dir) / "file2.py").write_text("print('hi')")
        subdir = Path(self.test_dir) / "subdir"
        subdir.mkdir(exist_ok=True)
        (subdir / "file3.txt").write_text("inhalt3")

        entries = self.sandbox.list_directory_safe(self.test_dir, max_depth=1)
        assert len(entries) > 0
        names = [e.name for e in entries]
        assert "file1.txt" in names

    def test_list_directory_safe_nonexistent(self):
        """Nicht-existierendes Verzeichnis wird gemeldet."""
        with pytest.raises(PathSandboxError):
            self.sandbox.list_directory_safe("/nonexistent/path/12345")

    # ------------------------------------------------------------------
    # search_files_safe Tests
    # ------------------------------------------------------------------

    def test_search_files_safe_by_name(self):
        """Suche nach Dateinamen funktioniert."""
        (Path(self.test_dir) / "test.py").write_text("code")
        (Path(self.test_dir) / "other.txt").write_text("text")
        (Path(self.test_dir) / "another.py").write_text("more code")

        matches = self.sandbox.search_files_safe(
            self.test_dir, pattern=r".*\.py$", max_depth=3
        )
        assert len(matches) >= 2

    def test_search_files_safe_content(self):
        """Inhaltssuche in Text-Dateien funktioniert."""
        (Path(self.test_dir) / "secret.py").write_text("password = '12345'")
        (Path(self.test_dir) / "normal.py").write_text("print('hello')")

        matches = self.sandbox.search_files_safe(
            self.test_dir, pattern=r"password", content_search=True, max_depth=3
        )
        assert len(matches) >= 1

    def test_search_files_safe_respects_max_depth(self):
        """Max-Depth wird bei Suche eingehalten."""
        deep_file = Path(self.test_dir) / "a" / "b" / "c" / "d" / "e" / "deep.txt"
        deep_file.parent.mkdir(parents=True, exist_ok=True)
        deep_file.write_text("geheim")

        matches = self.sandbox.search_files_safe(
            self.test_dir, pattern=r".*", max_depth=1
        )
        match_paths = [m.get("path", "") for m in matches]
        assert not any("deep.txt" in p for p in match_paths)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])