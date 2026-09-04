from pathlib import Path
from types import SimpleNamespace

import wellbeing.kg_faiss_manager as kg_manager
import scripts.run_profile_synthesis_eval as eval_runner

from wellbeing.profile_synthesis_evaluator import (
    EvalCase,
    ProfileSynthesisKpiThresholds,
    evaluate_cases,
    load_cases_from_json,
)


def test_fixture_cases_pass_expected_match_rate():
    fixture_path = Path("tests/fixtures/profile_synthesis_gold_cases.json")
    cases = load_cases_from_json(fixture_path)

    report = evaluate_cases(cases, thresholds=ProfileSynthesisKpiThresholds())

    assert report["kpis"]["total_cases"] == 6
    assert report["kpis"]["expected_valid_cases"] == 2
    assert report["kpis"]["expected_match_rate"] == 1.0
    assert report["kpis"]["parse_pass_rate"] == 1.0
    assert report["kpis"]["schema_pass_rate"] == 1.0
    assert report["kpis"]["semantic_pass_rate"] == 1.0
    assert report["gate_passed"]


def test_gate_fails_with_impossible_semantic_threshold():
    fixture_path = Path("tests/fixtures/profile_synthesis_gold_cases.json")
    cases = load_cases_from_json(fixture_path)

    strict_thresholds = ProfileSynthesisKpiThresholds(min_semantic_pass_rate=1.1)
    report = evaluate_cases(cases, thresholds=strict_thresholds)

    assert not report["gate_passed"]
    assert any("semantic_pass_rate" in failure for failure in report["gate_failures"])


def test_gate_fails_when_successful_cases_below_threshold():
    cases = [
        EvalCase(
            case_id="invalid-1",
            payload=None,
            expected_valid=True,
            source="unit",
        )
    ]
    thresholds = ProfileSynthesisKpiThresholds(min_successful_cases=1)

    report = evaluate_cases(cases, thresholds=thresholds)

    assert report["kpis"]["successful_cases"] == 0
    assert not report["gate_passed"]
    assert any("successful_cases<1" in failure for failure in report["gate_failures"])


class _LazyLoader:
    def __init__(self) -> None:
        self.current_model_id = None
        self.loaded_model_id = None

    def get_current_model_id(self):
        return self.current_model_id

    def is_model_loaded(self):
        return self.loaded_model_id is not None

    def load_model_by_config(self, model_id: str):
        self.loaded_model_id = model_id
        self.current_model_id = model_id
        return True


def test_ensure_model_loaded_uses_gemma4_12b_when_loader_starts_empty():
    loader = _LazyLoader()

    eval_runner._ensure_model_loaded(loader, "gemma-4-12b-it")

    assert loader.loaded_model_id == "gemma-4-12b-it"
    assert loader.get_current_model_id() == "gemma-4-12b-it"
    assert loader.is_model_loaded()


def test_ensure_model_loaded_reloads_when_wrong_model_is_already_loaded():
    loader = _LazyLoader()
    loader.current_model_id = "other-model"
    loader.loaded_model_id = "other-model"

    eval_runner._ensure_model_loaded(loader, "gemma-4-12b-it")

    assert loader.loaded_model_id == "gemma-4-12b-it"
    assert loader.get_current_model_id() == "gemma-4-12b-it"


def test_build_thresholds_keeps_defaults_for_gemma4_12b_canary():
    args = SimpleNamespace(
        min_parse_pass_rate=0.98,
        min_schema_pass_rate=1.0,
        min_semantic_pass_rate=0.98,
        min_successful_cases=1,
        min_overall_confidence=0.35,
        max_overall_confidence=0.95,
    )

    thresholds = eval_runner._build_thresholds(args)

    assert thresholds.min_parse_pass_rate == 0.98
    assert thresholds.min_schema_pass_rate == 1.0
    assert thresholds.min_semantic_pass_rate == 0.98
    assert thresholds.min_successful_cases == 1
    assert thresholds.min_overall_confidence == 0.35
    assert thresholds.max_overall_confidence == 0.95


def test_kg_faiss_manager_factory_injects_late_bound_db_instance(monkeypatch):
    singleton = kg_manager.PsychoKGFAISSManager.__new__(kg_manager.PsychoKGFAISSManager)
    singleton._db_instance = None
    monkeypatch.setattr(kg_manager, "_psycho_kg_faiss_manager_instance", singleton)

    sentinel_db = object()
    factory_result = kg_manager.get_psycho_kg_faiss_manager(db_instance=sentinel_db)

    assert factory_result is singleton
    assert factory_result._db_instance is sentinel_db
