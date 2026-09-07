#!/usr/bin/env python3
"""Evaluate execution-mode routing with the configured local GGUF model."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent_chatbot_logic import AgentChatbotLogic
from scripts.model_loader import DEFAULT_MODEL, ModelLoader


VALID_MODES = {"SIMPLE", "PLAN_EXECUTE", "REACT"}


def _load_cases(path: Path) -> List[Dict[str, str]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list) or not raw:
        raise ValueError("Routing case file must contain a non-empty JSON array")

    cases: List[Dict[str, str]] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ValueError(f"Routing case at index {index} is not an object")
        case_id = str(item.get("case_id", "")).strip()
        expected_mode = str(item.get("expected_mode", "")).strip().upper()
        prompt = str(item.get("prompt", "")).strip()
        if not case_id or not prompt or expected_mode not in VALID_MODES:
            raise ValueError(f"Invalid routing case at index {index}")
        cases.append({
            "case_id": case_id,
            "expected_mode": expected_mode,
            "prompt": prompt,
        })
    return cases


def _build_router(model_loader: Any) -> AgentChatbotLogic:
    router = AgentChatbotLogic.__new__(AgentChatbotLogic)
    router.agent_mode_enabled = True
    router.chat_routing_config = {"use_llm_routing": True}
    router.settings = {"use_react_agent": True}
    router.model_loader = model_loader
    return router


def main() -> int:
    parser = argparse.ArgumentParser(description="Run local execution-mode routing gates")
    parser.add_argument("--model-id", default=DEFAULT_MODEL)
    parser.add_argument("--cases", default="tests/fixtures/agent_routing_cases.json")
    parser.add_argument("--out", default="")
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()

    cases = _load_cases(Path(args.cases))
    model_loader = ModelLoader()
    if not model_loader.load_model_by_config(args.model_id):
        raise RuntimeError(f"Model could not be loaded: {args.model_id}")
    if model_loader.get_current_model_id() != args.model_id:
        raise RuntimeError(
            f"Model load drift: requested={args.model_id}, "
            f"loaded={model_loader.get_current_model_id()}"
        )

    router = _build_router(model_loader)
    results = []
    for case in cases:
        actual_mode = router._select_agent_execution_mode(case["prompt"])
        results.append({
            "case_id": case["case_id"],
            "expected_mode": case["expected_mode"],
            "actual_mode": actual_mode,
            "passed": actual_mode == case["expected_mode"],
        })

    passed = sum(1 for result in results if result["passed"])
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "model_id": model_loader.get_current_model_id(),
        "total_cases": len(results),
        "passed_cases": passed,
        "accuracy": passed / len(results),
        "gate_passed": passed == len(results),
        "results": results,
    }

    output_path = Path(args.out) if args.out else (
        Path("monitoring")
        / "agent_routing"
        / f"routing_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({**report, "output": str(output_path)}, ensure_ascii=False, indent=2))

    if args.strict and not report["gate_passed"]:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())