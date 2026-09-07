from scripts.benchmark_llm_gpu_tuning import build_case_grid, build_prompt_text, parse_int_list, recommend_best_case
from scripts.model_loader import select_gpu_tuning_profile


def test_parse_int_list():
    assert parse_int_list("1024, 1536,2048") == [1024, 1536, 2048]


def test_build_case_grid_filters_invalid_pairs():
    cases = build_case_grid([1024, 2048], [512, 4096], [8, 12], [16])
    assert [(case.batch, case.ubatch, case.threads, case.threads_batch) for case in cases] == [
        (1024, 512, 8, 16),
        (1024, 512, 12, 16),
        (2048, 512, 8, 16),
        (2048, 512, 12, 16),
    ]


def test_build_case_grid_ignores_non_positive_threads():
    cases = build_case_grid([1024], [512], [0, -4, 12], [0, -2, 20])
    assert [(case.batch, case.ubatch, case.threads, case.threads_batch) for case in cases] == [
        (1024, 512, 12, 20)
    ]


def test_build_prompt_text_grows_with_target():
    short = build_prompt_text(64)
    long = build_prompt_text(512)
    assert len(long.split()) >= len(short.split())


def test_recommend_best_case_prefers_longest_stable_prompt():
    results = [
        {"ok": True, "prompt_tokens": 100, "completion_tokens_per_second": 10.0, "total_tokens_per_second": 20.0, "load_seconds": 1.0},
        {"ok": True, "prompt_tokens": 200, "completion_tokens_per_second": 8.0, "total_tokens_per_second": 18.0, "load_seconds": 0.5},
        {"ok": False, "prompt_tokens": 500, "completion_tokens_per_second": 99.0, "total_tokens_per_second": 99.0, "load_seconds": 0.1},
    ]
    best = recommend_best_case(results)
    assert best["prompt_tokens"] == 200


def test_select_gpu_tuning_profile_prefers_validated_single_user_default():
    profile = select_gpu_tuning_profile(
        model_path="gemma-4-e4b.gguf",
        n_ctx=32768,
        gpu_memory_gb=24.0,
        logical_cores=32,
    )

    assert profile["optimal_batch"] == 3072
    assert profile["optimal_ubatch"] == 2048
    assert profile["optimal_threads"] == 12
    assert profile["optimal_threads_batch"] == 12


def test_select_gpu_tuning_profile_keeps_explicit_batch_override():
    profile = select_gpu_tuning_profile(
        model_path="Qwen3.8-27B-Q4_K_M.gguf",
        n_ctx=16384,
        gpu_memory_gb=24.0,
        logical_cores=32,
        batch_override=3072,
    )

    assert profile["optimal_batch"] == 3072
    assert profile["batch_source"] == "override"
