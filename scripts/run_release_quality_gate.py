#!/usr/bin/env python3
"""Run deterministic and live local release-quality gates."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MODEL_ID = "gemma-4-12b-it"


@dataclass(frozen=True)
class GateStep:
    name: str
    command: List[str]
    report_path: Optional[Path] = None


def _build_steps(args: argparse.Namespace, run_id: str) -> List[GateStep]:
    python = sys.executable
    report_root = Path("monitoring") / "release_quality" / run_id
    steps: List[GateStep] = []

    if args.mode in {"deterministic", "all"}:
        steps.extend(
            [
                GateStep(
                    name="pytest",
                    command=[
                        python,
                        "-m",
                        "pytest",
                        "tests/",
                        "-q",
                        "--no-header",
                        "-p",
                        "no:cacheprovider",
                    ],
                ),
                GateStep(
                    name="profile-fixture",
                    command=[
                        python,
                        "scripts/run_profile_synthesis_eval.py",
                        "--mode",
                        "fixture",
                        "--strict",
                        "--out",
                        str(report_root / "profile_fixture.json"),
                    ],
                    report_path=report_root / "profile_fixture.json",
                ),
            ]
        )

    if args.mode in {"live", "all"}:
        finance_report = report_root / "finance_canary.json"
        profile_report = report_root / "profile_canary.json"
        steps.append(
            GateStep(
                name="finance-canary",
                command=[
                    python,
                    "scripts/run_finance_canary.py",
                    "--model-id",
                    args.model_id,
                    "--strict",
                    "--out",
                    str(finance_report),
                ],
                report_path=finance_report,
            )
        )
        profile_command = [
            python,
            "scripts/run_profile_synthesis_eval.py",
            "--mode",
            "canary",
            "--model-id",
            args.model_id,
            "--max-users",
            str(args.max_users),
            "--strict",
            "--out",
            str(profile_report),
        ]
        if args.force_regenerate:
            profile_command.append("--force-regenerate")
        steps.append(
            GateStep(
                name="profile-canary",
                command=profile_command,
                report_path=profile_report,
            )
        )

    return steps


def _load_child_report(path: Optional[Path]) -> Optional[Dict[str, object]]:
    if path is None or not path.exists():
        return None
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return loaded if isinstance(loaded, dict) else None


def main() -> int:
    parser = argparse.ArgumentParser(description="Run strict local release-quality gates")
    parser.add_argument(
        "--mode",
        choices=["deterministic", "live", "all"],
        default="all",
    )
    parser.add_argument("--model-id", default=DEFAULT_MODEL_ID)
    parser.add_argument("--max-users", type=int, default=10)
    parser.add_argument("--force-regenerate", action="store_true")
    parser.add_argument("--out", default="")
    parser.add_argument(
        "--keep-going",
        action="store_true",
        help="Run remaining gates after a failed step.",
    )
    args = parser.parse_args()

    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    steps = _build_steps(args, run_id)
    environment = os.environ.copy()
    environment["APP_LOCAL_ONLY"] = "1"
    results: List[Dict[str, object]] = []

    for step in steps:
        print(f"\n=== {step.name} ===", flush=True)
        completed = subprocess.run(
            step.command,
            cwd=ROOT,
            env=environment,
            check=False,
        )
        results.append(
            {
                "name": step.name,
                "command": step.command,
                "return_code": completed.returncode,
                "passed": completed.returncode == 0,
                "report_path": str(step.report_path) if step.report_path else None,
                "report": _load_child_report(ROOT / step.report_path) if step.report_path else None,
            }
        )
        if completed.returncode != 0 and not args.keep_going:
            break

    executed_names = {str(result["name"]) for result in results}
    skipped = [step.name for step in steps if step.name not in executed_names]
    gate_passed = len(results) == len(steps) and all(
        bool(result["passed"]) for result in results
    )
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": args.mode,
        "model_id": args.model_id if args.mode in {"live", "all"} else None,
        "local_only": True,
        "gate_passed": gate_passed,
        "failed_steps": [
            str(result["name"]) for result in results if not result["passed"]
        ],
        "skipped_steps": skipped,
        "steps": results,
    }
    output_path = Path(args.out) if args.out else (
        Path("monitoring") / "release_quality" / f"release_gate_{run_id}.json"
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "gate_passed": gate_passed,
                "failed_steps": report["failed_steps"],
                "skipped_steps": skipped,
                "output": str(output_path),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if gate_passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
