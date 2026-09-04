"""Frische- & Policy-Gates fuer LICENSES.md (s. docs/19_LICENSES_AND_COMPLIANCE.md).

Laueft den lokalen, stdlib-basierten Checker als Subprozess (venv-Python).
Deterministisch: keine Netz-/Zeitabhaengigkeit.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CHECKER = REPO_ROOT / "scripts" / "check_licenses.py"


def _run_checker(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(CHECKER), *args],
        capture_output=True,
        text=True,
        timeout=180,
    )


def _diagnostics(proc: subprocess.CompletedProcess) -> str:
    return (
        "Aktion: python scripts/generate_licenses.py  "
        "(und MANUAL_OVERRIDES pruefen)\n"
        f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )


def test_licenses_md_up_to_date() -> None:
    """LICENSES.md muss mit der installierten Venv uebereinstimmen."""
    proc = _run_checker()
    assert proc.returncode == 0, _diagnostics(proc)


def test_no_unknown_runtime_licenses_strict() -> None:
    """Keine UNKNOWN/needs-review-Lizenzen in Runtime-Abhaengigkeiten."""
    proc = _run_checker("--strict")
    assert proc.returncode == 0, _diagnostics(proc)