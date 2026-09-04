from __future__ import annotations

import sys
from types import SimpleNamespace

from scripts.run_release_quality_gate import _build_steps


def _args(**overrides):
    values = {
        "mode": "all",
        "model_id": "gemma-4-12b-it",
        "max_users": 10,
        "force_regenerate": False,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_deterministic_release_steps_use_active_interpreter_and_strict_fixture():
    steps = _build_steps(_args(mode="deterministic"), "test-run")

    assert [step.name for step in steps] == ["pytest", "profile-fixture"]
    assert all(step.command[0] == sys.executable for step in steps)
    assert "tests/" in steps[0].command
    assert "--strict" in steps[1].command
    assert steps[1].report_path.as_posix().endswith(
        "monitoring/release_quality/test-run/profile_fixture.json"
    )


def test_live_release_steps_pin_model_and_profile_canary_options():
    steps = _build_steps(
        _args(mode="live", max_users=3, force_regenerate=True),
        "test-run",
    )

    assert [step.name for step in steps] == ["finance-canary", "profile-canary"]
    assert all("--strict" in step.command for step in steps)
    assert all("gemma-4-12b-it" in step.command for step in steps)
    assert steps[1].command[steps[1].command.index("--max-users") + 1] == "3"
    assert "--force-regenerate" in steps[1].command