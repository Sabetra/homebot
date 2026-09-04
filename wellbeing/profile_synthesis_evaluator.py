#!/usr/bin/env python3
"""Local profile synthesis evaluation and canary utilities.

This module implements local-only quality gates for synthesized psychological
profiles. It is intentionally independent from cloud services.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
import json

from wellbeing.profile_synthesizer import ProfileSynthesizer


@dataclass(frozen=True)
class ProfileSynthesisKpiThresholds:
    """Hard KPI thresholds for profile synthesis quality gates."""

    min_parse_pass_rate: float = 0.98
    min_schema_pass_rate: float = 1.0
    min_semantic_pass_rate: float = 0.98
    min_successful_cases: int = 1
    min_overall_confidence: float = 0.35
    max_overall_confidence: float = 0.95


@dataclass
class EvalCase:
    """Single evaluation case."""

    case_id: str
    payload: Optional[Dict[str, Any]]
    expected_valid: bool
    source: str


@dataclass
class EvalCaseResult:
    """Evaluation outcome for one case."""

    case_id: str
    source: str
    expected_valid: bool
    parse_ok: bool
    schema_ok: bool
    semantic_ok: bool
    confidence_ok: bool
    valid: bool
    error_reason: Optional[str]


def _make_validator_synthesizer() -> ProfileSynthesizer:
    """Create a lightweight synthesizer instance only for validation methods."""

    class _DummyDB:
        pass

    class _DummyLoader:
        pass

    return ProfileSynthesizer(psychological_db=_DummyDB(), model_loader=_DummyLoader())


def evaluate_payload(
    payload: Optional[Dict[str, Any]],
    expected_valid: bool,
    source: str,
    case_id: str,
    synthesizer: ProfileSynthesizer,
    thresholds: ProfileSynthesisKpiThresholds,
) -> EvalCaseResult:
    """Evaluate one payload against schema/semantic/confidence gates."""
    parse_ok = isinstance(payload, dict)
    schema_ok = False
    semantic_ok = False
    confidence_ok = False
    valid = False
    reason: Optional[str] = None

    if not parse_ok:
        reason = "payload_not_dict"
    else:
        schema_ok = synthesizer._validate_profile_schema(payload)
        if not schema_ok:
            reason = "schema_validation_failed"
        else:
            semantic_error = synthesizer._validate_semantic_consistency(payload)
            semantic_ok = semantic_error is None
            if not semantic_ok:
                reason = semantic_error or "semantic_validation_failed"
            else:
                overall = synthesizer._clamp_confidence(payload.get("overall_confidence"))
                confidence_ok = (
                    thresholds.min_overall_confidence
                    <= overall
                    <= thresholds.max_overall_confidence
                )
                if not confidence_ok:
                    reason = f"overall_confidence_out_of_bounds:{overall:.3f}"

    valid = parse_ok and schema_ok and semantic_ok and confidence_ok
    if valid:
        reason = None

    return EvalCaseResult(
        case_id=case_id,
        source=source,
        expected_valid=expected_valid,
        parse_ok=parse_ok,
        schema_ok=schema_ok,
        semantic_ok=semantic_ok,
        confidence_ok=confidence_ok,
        valid=valid,
        error_reason=reason,
    )


def evaluate_cases(
    cases: List[EvalCase],
    thresholds: Optional[ProfileSynthesisKpiThresholds] = None,
) -> Dict[str, Any]:
    """Evaluate multiple cases and produce KPI summary with pass/fail."""
    thresholds = thresholds or ProfileSynthesisKpiThresholds()
    synthesizer = _make_validator_synthesizer()

    results: List[EvalCaseResult] = [
        evaluate_payload(
            payload=case.payload,
            expected_valid=case.expected_valid,
            source=case.source,
            case_id=case.case_id,
            synthesizer=synthesizer,
            thresholds=thresholds,
        )
        for case in cases
    ]

    total = len(results)
    expected_valid_results = [r for r in results if r.expected_valid]
    expected_valid_total = len(expected_valid_results)

    parse_pass = sum(1 for r in expected_valid_results if r.parse_ok)
    schema_pass = sum(1 for r in expected_valid_results if r.schema_ok)
    semantic_pass = sum(1 for r in expected_valid_results if r.semantic_ok)
    successful_cases = sum(1 for r in expected_valid_results if r.valid)

    expected_matches = sum(
        1
        for r in results
        if (r.expected_valid and r.valid) or ((not r.expected_valid) and (not r.valid))
    )

    parse_rate = (parse_pass / expected_valid_total) if expected_valid_total else 0.0
    schema_rate = (schema_pass / expected_valid_total) if expected_valid_total else 0.0
    semantic_rate = (semantic_pass / expected_valid_total) if expected_valid_total else 0.0
    match_rate = (expected_matches / total) if total else 0.0

    kpis = {
        "total_cases": total,
        "expected_valid_cases": expected_valid_total,
        "successful_cases": successful_cases,
        "parse_pass_rate": round(parse_rate, 6),
        "schema_pass_rate": round(schema_rate, 6),
        "semantic_pass_rate": round(semantic_rate, 6),
        "expected_match_rate": round(match_rate, 6),
    }

    failed_cases = [asdict(r) for r in results if ((r.expected_valid and not r.valid) or ((not r.expected_valid) and r.valid))]

    gate_failures: List[str] = []
    if kpis["parse_pass_rate"] < thresholds.min_parse_pass_rate:
        gate_failures.append(
            f"parse_pass_rate<{thresholds.min_parse_pass_rate} ({kpis['parse_pass_rate']})"
        )
    if kpis["schema_pass_rate"] < thresholds.min_schema_pass_rate:
        gate_failures.append(
            f"schema_pass_rate<{thresholds.min_schema_pass_rate} ({kpis['schema_pass_rate']})"
        )
    if kpis["semantic_pass_rate"] < thresholds.min_semantic_pass_rate:
        gate_failures.append(
            f"semantic_pass_rate<{thresholds.min_semantic_pass_rate} ({kpis['semantic_pass_rate']})"
        )
    if successful_cases < thresholds.min_successful_cases:
        gate_failures.append(
            f"successful_cases<{thresholds.min_successful_cases} ({successful_cases})"
        )

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "thresholds": asdict(thresholds),
        "kpis": kpis,
        "gate_passed": len(gate_failures) == 0,
        "gate_failures": gate_failures,
        "failed_cases": failed_cases,
        "results": [asdict(r) for r in results],
    }


def load_cases_from_json(path: Path) -> List[EvalCase]:
    """Load evaluation cases from JSON file."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError("Case file must contain a JSON array")

    cases: List[EvalCase] = []
    for idx, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ValueError(f"Case at index {idx} is not an object")
        cases.append(
            EvalCase(
                case_id=str(item.get("case_id", f"case-{idx+1}")),
                payload=item.get("payload"),
                expected_valid=bool(item.get("expected_valid", True)),
                source=str(item.get("source", "fixture")),
            )
        )
    return cases


def write_report(report: Dict[str, Any], output_path: Path) -> None:
    """Write JSON report to disk."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
