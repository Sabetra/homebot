#!/usr/bin/env python3
"""Run local profile synthesis evaluation gates.

Modes:
- fixture: evaluate fixture payloads against local KPI gates.
- canary: run live local synthesis for recent users and evaluate outputs.
"""

from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

if sys.platform == "win32":
    # Force child-process interpreter to the active runtime (venv-safe on Windows).
    try:
        mp.set_executable(sys.executable)
    except Exception:
        pass
    # Some spawners resolve via _base_executable / PYTHONEXECUTABLE.
    try:
        sys._base_executable = sys.executable  # type: ignore[attr-defined]
    except Exception:
        pass
    os.environ["PYTHONEXECUTABLE"] = sys.executable

from wellbeing.profile_synthesis_evaluator import (
    EvalCase,
    ProfileSynthesisKpiThresholds,
    evaluate_cases,
    load_cases_from_json,
    write_report,
)

DEFAULT_MODEL_ID = "gemma-4-12b-it"


def _build_thresholds(args: argparse.Namespace) -> ProfileSynthesisKpiThresholds:
    return ProfileSynthesisKpiThresholds(
        min_parse_pass_rate=args.min_parse_pass_rate,
        min_schema_pass_rate=args.min_schema_pass_rate,
        min_semantic_pass_rate=args.min_semantic_pass_rate,
        min_successful_cases=args.min_successful_cases,
        min_overall_confidence=args.min_overall_confidence,
        max_overall_confidence=args.max_overall_confidence,
    )


def _run_fixture_mode(args: argparse.Namespace) -> Dict[str, Any]:
    case_path = Path(args.cases)
    cases = load_cases_from_json(case_path)
    thresholds = _build_thresholds(args)
    report = evaluate_cases(cases, thresholds=thresholds)
    report["mode"] = "fixture"
    report["case_file"] = str(case_path)
    return report


def _load_recent_user_ids(db: Any, max_users: int) -> List[str]:
    with db.get_connection() as conn:
        rows = conn.execute(
            """
            SELECT user_id
            FROM wellbeing_sessions
            GROUP BY user_id
            ORDER BY MAX(start_time) DESC
            LIMIT ?
            """,
            (max_users,),
        ).fetchall()
    return [str(row["user_id"]) for row in rows if row and row["user_id"]]


def _ensure_model_loaded(model_loader: Any, model_id: str) -> None:
    current_model = model_loader.get_current_model_id()
    is_loaded = bool(getattr(model_loader, "is_model_loaded", lambda: False)())
    if current_model == model_id and is_loaded:
        return

    # Hard quality contract for canary gates: run with the explicit target model,
    # not with whichever model happens to be resident in memory.
    ok = model_loader.load_model_by_config(model_id)
    if not ok or not getattr(model_loader, "is_model_loaded", lambda: False)():
        raise RuntimeError(f"Model could not be loaded: {model_id}")
    loaded_model = model_loader.get_current_model_id()
    if loaded_model != model_id:
        raise RuntimeError(
            "Model load drift detected: "
            f"requested={model_id}, loaded={loaded_model}"
        )


def _run_canary_mode(args: argparse.Namespace) -> Dict[str, Any]:
    try:
        from wellbeing.profile_synthesizer import create_profile_synthesizer
        from wellbeing.wellbeing_db import WellbeingDatabase
        from scripts.model_loader import ModelLoader
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "Canary mode dependencies are missing in the local environment: "
            f"{exc}. Install project requirements before running live canary gates."
        ) from exc

    thresholds = _build_thresholds(args)

    model_loader = ModelLoader()
    _ensure_model_loaded(model_loader, args.model_id)
    db = WellbeingDatabase(db_path=args.db_path, model_loader=model_loader)
    synthesizer = create_profile_synthesizer(db, model_loader)

    user_ids = _load_recent_user_ids(db, args.max_users)
    cases: List[EvalCase] = []

    for idx, user_id in enumerate(user_ids, start=1):
        profile = synthesizer.synthesize_profile(
            user_id=user_id,
            max_kg_triples=args.max_kg_triples,
            max_sessions=args.max_sessions,
            force_regenerate=args.force_regenerate,
            synthesis_type="full",
        )
        payload = profile.to_context_dict() if profile else None
        cases.append(
            EvalCase(
                case_id=f"canary-{idx}",
                payload=payload,
                expected_valid=True,
                source=f"user:{user_id[:12]}",
            )
        )

    report = evaluate_cases(cases, thresholds=thresholds)
    report["mode"] = "canary"
    report["db_path"] = args.db_path
    report["model_id"] = args.model_id
    report["evaluated_users"] = len(user_ids)
    return report


def _default_report_path(mode: str) -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return Path("monitoring") / "profile_synthesis" / f"{mode}_report_{ts}.json"


def main() -> int:
    parser = argparse.ArgumentParser(description="Run local profile synthesis quality gates")
    parser.add_argument("--mode", choices=["fixture", "canary"], default="fixture")
    parser.add_argument("--cases", default="tests/fixtures/profile_synthesis_gold_cases.json")
    parser.add_argument("--out", default="")
    parser.add_argument("--strict", action="store_true")

    parser.add_argument("--min-parse-pass-rate", type=float, default=0.98)
    parser.add_argument("--min-schema-pass-rate", type=float, default=1.0)
    parser.add_argument("--min-semantic-pass-rate", type=float, default=0.98)
    parser.add_argument("--min-successful-cases", type=int, default=1)
    parser.add_argument("--min-overall-confidence", type=float, default=0.35)
    parser.add_argument("--max-overall-confidence", type=float, default=0.95)

    parser.add_argument("--db-path", default="wellbeing_store.db")
    parser.add_argument("--model-id", default=DEFAULT_MODEL_ID)
    parser.add_argument("--max-users", type=int, default=10)
    parser.add_argument("--max-kg-triples", type=int, default=200)
    parser.add_argument("--max-sessions", type=int, default=20)
    parser.add_argument("--force-regenerate", action="store_true")

    args = parser.parse_args()

    if args.mode == "fixture":
        report = _run_fixture_mode(args)
    else:
        report = _run_canary_mode(args)

    output_path = Path(args.out) if args.out else _default_report_path(args.mode)
    write_report(report, output_path)

    summary = {
        "mode": report.get("mode"),
        "gate_passed": report.get("gate_passed"),
        "kpis": report.get("kpis"),
        "gate_failures": report.get("gate_failures"),
        "output": str(output_path),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))

    if args.strict and not report.get("gate_passed", False):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
