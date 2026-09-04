#!/usr/bin/env python3
"""Benchmark llama.cpp GPU tuning combinations.

The script measures throughput and stability for different n_batch / n_ubatch
pairs against one or more prompt sizes. It is intentionally local-only and
writes a JSON report that can be used to pick the best production profile.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Sequence


DEFAULT_BATCH_VALUES = (1024, 1536, 2048, 3072)
DEFAULT_UBATCH_VALUES = (512, 1024, 2048)
DEFAULT_PROMPT_TOKEN_TARGETS = (2048, 8192, 16384, 18713)
DEFAULT_MAX_TOKENS = 256
DEFAULT_TEMPERATURE = 0.2
DEFAULT_THREAD_VALUES = ()
DEFAULT_THREAD_BATCH_VALUES = ()
DEFAULT_REPETITIONS = 1

# Ensure project root is importable regardless of invocation style
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


@dataclass(frozen=True)
class BenchmarkCase:
    batch: int
    ubatch: int
    threads: int
    threads_batch: int
    prompt_target_tokens: int


@dataclass
class BenchmarkResult:
    model_id: str
    batch: int
    ubatch: int
    threads: int
    threads_batch: int
    prompt_target_tokens: int
    prompt_tokens: int
    completion_tokens: int
    load_seconds: float
    inference_seconds: float
    prompt_tokens_per_second: float
    completion_tokens_per_second: float
    total_tokens_per_second: float
    ok: bool
    error: str | None = None


def parse_int_list(raw: str) -> list[int]:
    values: list[int] = []
    for chunk in raw.split(","):
        text = chunk.strip()
        if not text:
            continue
        values.append(int(text))
    return values


def build_case_grid(
    batch_values: Sequence[int],
    ubatch_values: Sequence[int],
    thread_values: Sequence[int],
    thread_batch_values: Sequence[int],
) -> list[BenchmarkCase]:
    effective_threads = [int(value) for value in thread_values if int(value) > 0]
    if not effective_threads:
        effective_threads = [0]

    effective_threads_batch = [int(value) for value in thread_batch_values if int(value) > 0]
    if not effective_threads_batch:
        effective_threads_batch = [0]

    cases: list[BenchmarkCase] = []
    for batch in batch_values:
        for ubatch in ubatch_values:
            if ubatch <= batch:
                for threads in effective_threads:
                    for threads_batch in effective_threads_batch:
                        cases.append(
                            BenchmarkCase(
                                batch=batch,
                                ubatch=ubatch,
                                threads=threads,
                                threads_batch=threads_batch,
                                prompt_target_tokens=0,
                            )
                        )
    return cases


def build_prompt_text(target_tokens: int, seed_text: str | None = None) -> str:
    """Build a synthetic long prompt for local throughput tests."""
    if seed_text is None:
        seed_text = (
            "Analysiere die folgenden Hinweise, fasse die wichtigen Punkte knapp zusammen, "
            "und erkläre die Konsequenzen fuer Durchsatz, Stabilitaet und GPU-Auslastung."
        )
    words = seed_text.split()
    if not words:
        return seed_text

    repetitions = max(1, math.ceil(target_tokens / max(1, len(words))))
    prompt = " ".join(words * repetitions)
    return prompt


def load_prompt_file(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _tokenize_text(loader: Any, text: str) -> int:
    tokens = loader.llm.tokenize(text.encode("utf-8"), add_bos=False, special=True)
    return len(tokens)


def _measure_case(
    *,
    model_id: str,
    batch: int,
    ubatch: int,
    threads: int,
    threads_batch: int,
    prompt_text: str,
    max_tokens: int,
    temperature: float,
) -> BenchmarkResult:
    previous_batch = os.environ.get("LLM_N_BATCH")
    previous_ubatch = os.environ.get("LLM_N_UBATCH")
    previous_threads = os.environ.get("LLM_N_THREADS")
    previous_threads_batch = os.environ.get("LLM_N_THREADS_BATCH")
    os.environ["LLM_N_BATCH"] = str(batch)
    os.environ["LLM_N_UBATCH"] = str(ubatch)
    if threads > 0:
        os.environ["LLM_N_THREADS"] = str(threads)
    else:
        os.environ.pop("LLM_N_THREADS", None)
    if threads_batch > 0:
        os.environ["LLM_N_THREADS_BATCH"] = str(threads_batch)
    else:
        os.environ.pop("LLM_N_THREADS_BATCH", None)

    loader = None
    try:
        try:
            from scripts.model_loader import ModelLoader
        except ModuleNotFoundError:
            # Running from scripts/ as sys.path[0] -> direct import fallback
            from model_loader import ModelLoader

        loader = ModelLoader()
        start_load = time.perf_counter()
        if not loader.load_model_by_config(model_id):
            raise RuntimeError(f"Model load failed: {model_id}")
        load_seconds = time.perf_counter() - start_load

        prompt_rendered = loader._render_chat_template(
            [{"role": "user", "content": prompt_text}],
            tools=None,
        )
        prompt_tokens = _tokenize_text(loader, prompt_rendered)

        start_infer = time.perf_counter()
        response_text = loader._process_text_only(
            prompt=prompt_text,
            messages=None,
            max_tokens=max_tokens,
            temperature=temperature,
            top_p=0.9,
            top_k=40,
            repeat_penalty=1.1,
            stop=None,
            min_p=0.05,
            strip_think_blocks=True,
            grammar=None,
        )
        inference_seconds = time.perf_counter() - start_infer
        completion_tokens = _tokenize_text(loader, response_text) if response_text else 0

        prompt_tps = prompt_tokens / max(inference_seconds, 1e-6)
        completion_tps = completion_tokens / max(inference_seconds, 1e-6)
        total_tps = (prompt_tokens + completion_tokens) / max(inference_seconds, 1e-6)

        return BenchmarkResult(
            model_id=model_id,
            batch=batch,
            ubatch=ubatch,
            threads=threads,
            threads_batch=threads_batch,
            prompt_target_tokens=0,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            load_seconds=load_seconds,
            inference_seconds=inference_seconds,
            prompt_tokens_per_second=prompt_tps,
            completion_tokens_per_second=completion_tps,
            total_tokens_per_second=total_tps,
            ok=True,
        )
    except Exception as exc:
        return BenchmarkResult(
            model_id=model_id,
            batch=batch,
            ubatch=ubatch,
            threads=threads,
            threads_batch=threads_batch,
            prompt_target_tokens=0,
            prompt_tokens=0,
            completion_tokens=0,
            load_seconds=0.0,
            inference_seconds=0.0,
            prompt_tokens_per_second=0.0,
            completion_tokens_per_second=0.0,
            total_tokens_per_second=0.0,
            ok=False,
            error=str(exc),
        )
    finally:
        if loader is not None:
            try:
                loader.unload_model()
            except Exception:
                pass
        if previous_batch is None:
            os.environ.pop("LLM_N_BATCH", None)
        else:
            os.environ["LLM_N_BATCH"] = previous_batch
        if previous_ubatch is None:
            os.environ.pop("LLM_N_UBATCH", None)
        else:
            os.environ["LLM_N_UBATCH"] = previous_ubatch
        if previous_threads is None:
            os.environ.pop("LLM_N_THREADS", None)
        else:
            os.environ["LLM_N_THREADS"] = previous_threads
        if previous_threads_batch is None:
            os.environ.pop("LLM_N_THREADS_BATCH", None)
        else:
            os.environ["LLM_N_THREADS_BATCH"] = previous_threads_batch


def _extract_json_line(output_text: str) -> dict[str, Any] | None:
    for line in reversed(output_text.splitlines()):
        line = line.strip()
        if not (line.startswith("{") and line.endswith("}")):
            continue
        try:
            payload = json.loads(line)
            if isinstance(payload, dict):
                return payload
        except json.JSONDecodeError:
            continue
    return None


def _run_case_isolated(
    *,
    model_id: str,
    batch: int,
    ubatch: int,
    threads: int,
    threads_batch: int,
    prompt_target_tokens: int,
    max_tokens: int,
    temperature: float,
    prompt_file: Path | None,
) -> dict[str, Any]:
    cmd = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--worker",
        "--model-id",
        model_id,
        "--worker-batch",
        str(batch),
        "--worker-ubatch",
        str(ubatch),
        "--worker-threads",
        str(threads),
        "--worker-threads-batch",
        str(threads_batch),
        "--worker-prompt-target",
        str(prompt_target_tokens),
        "--max-tokens",
        str(max_tokens),
        "--temperature",
        str(temperature),
    ]
    if prompt_file is not None:
        cmd.extend(["--prompt-file", str(prompt_file)])

    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        cwd=str(PROJECT_ROOT),
        check=False,
    )
    parsed = _extract_json_line(proc.stdout)
    if parsed is not None:
        return parsed

    error_tail = (proc.stderr or proc.stdout or "").strip().splitlines()
    tail = " | ".join(error_tail[-3:]) if error_tail else "worker failed without output"
    return asdict(
        BenchmarkResult(
            model_id=model_id,
            batch=batch,
            ubatch=ubatch,
            threads=threads,
            threads_batch=threads_batch,
            prompt_target_tokens=prompt_target_tokens,
            prompt_tokens=0,
            completion_tokens=0,
            load_seconds=0.0,
            inference_seconds=0.0,
            prompt_tokens_per_second=0.0,
            completion_tokens_per_second=0.0,
            total_tokens_per_second=0.0,
            ok=False,
            error=f"worker_exit={proc.returncode}: {tail}",
        )
    )


def _run_worker_case(args: argparse.Namespace) -> int:
    prompt_target = int(args.worker_prompt_target)
    prompt_text = load_prompt_file(Path(args.prompt_file)) if args.prompt_file else build_prompt_text(prompt_target)
    result = _measure_case(
        model_id=args.model_id,
        batch=int(args.worker_batch),
        ubatch=int(args.worker_ubatch),
        threads=int(args.worker_threads),
        threads_batch=int(args.worker_threads_batch),
        prompt_text=prompt_text,
        max_tokens=args.max_tokens,
        temperature=args.temperature,
    )
    result.prompt_target_tokens = prompt_target
    print(json.dumps(asdict(result), ensure_ascii=False))
    return 0


def run_benchmark(
    *,
    model_id: str,
    batch_values: Sequence[int],
    ubatch_values: Sequence[int],
    thread_values: Sequence[int],
    thread_batch_values: Sequence[int],
    prompt_targets: Sequence[int],
    max_tokens: int,
    temperature: float,
    prompt_file: Path | None,
    repetitions: int,
) -> list[dict[str, Any]]:
    cases = build_case_grid(batch_values, ubatch_values, thread_values, thread_batch_values)
    results: list[dict[str, Any]] = []

    for repetition in range(max(1, repetitions)):
        for prompt_target in prompt_targets:
            for case in cases:
                measured = _run_case_isolated(
                    model_id=model_id,
                    batch=case.batch,
                    ubatch=case.ubatch,
                    threads=case.threads,
                    threads_batch=case.threads_batch,
                    prompt_target_tokens=int(prompt_target),
                    max_tokens=max_tokens,
                    temperature=temperature,
                    prompt_file=prompt_file,
                )
                measured["repetition"] = repetition + 1
                results.append(measured)

    return results


def recommend_best_case(results: Sequence[dict[str, Any]]) -> dict[str, Any] | None:
    stable = [result for result in results if result.get("ok")]
    if not stable:
        return None

    longest_prompt = max(int(result.get("prompt_tokens", 0)) for result in stable)
    best_candidates = [
        result
        for result in stable
        if int(result.get("prompt_tokens", 0)) == longest_prompt
    ]
    best_candidates.sort(
        key=lambda result: (
            float(result.get("completion_tokens_per_second", 0.0)),
            float(result.get("total_tokens_per_second", 0.0)),
            -float(result.get("load_seconds", 0.0)),
        ),
        reverse=True,
    )
    return best_candidates[0] if best_candidates else stable[0]


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Benchmark llama.cpp GPU tuning profiles")
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--model-id", default="gemma-4-e4b")
    parser.add_argument("--batch-values", default=",".join(str(v) for v in DEFAULT_BATCH_VALUES))
    parser.add_argument("--ubatch-values", default=",".join(str(v) for v in DEFAULT_UBATCH_VALUES))
    parser.add_argument("--thread-values", default=",".join(str(v) for v in DEFAULT_THREAD_VALUES))
    parser.add_argument("--thread-batch-values", default=",".join(str(v) for v in DEFAULT_THREAD_BATCH_VALUES))
    parser.add_argument("--prompt-token-targets", default=",".join(str(v) for v in DEFAULT_PROMPT_TOKEN_TARGETS))
    parser.add_argument("--prompt-file", default="")
    parser.add_argument("--worker-batch", type=int, default=0)
    parser.add_argument("--worker-ubatch", type=int, default=0)
    parser.add_argument("--worker-threads", type=int, default=0)
    parser.add_argument("--worker-threads-batch", type=int, default=0)
    parser.add_argument("--worker-prompt-target", type=int, default=0)
    parser.add_argument("--max-tokens", type=int, default=DEFAULT_MAX_TOKENS)
    parser.add_argument("--temperature", type=float, default=DEFAULT_TEMPERATURE)
    parser.add_argument("--repetitions", type=int, default=DEFAULT_REPETITIONS)
    parser.add_argument("--out", default="monitoring/llm_gpu_tuning/latest_benchmark.json")
    return parser


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()

    if args.worker:
        return _run_worker_case(args)

    batch_values = parse_int_list(args.batch_values)
    ubatch_values = parse_int_list(args.ubatch_values)
    thread_values = parse_int_list(args.thread_values)
    thread_batch_values = parse_int_list(args.thread_batch_values)
    prompt_targets = parse_int_list(args.prompt_token_targets)
    prompt_file = Path(args.prompt_file) if args.prompt_file else None

    results = run_benchmark(
        model_id=args.model_id,
        batch_values=batch_values,
        ubatch_values=ubatch_values,
        thread_values=thread_values,
        thread_batch_values=thread_batch_values,
        prompt_targets=prompt_targets,
        max_tokens=args.max_tokens,
        temperature=args.temperature,
        prompt_file=prompt_file,
        repetitions=args.repetitions,
    )
    best = recommend_best_case(results)

    output = {
        "model_id": args.model_id,
        "batch_values": batch_values,
        "ubatch_values": ubatch_values,
        "thread_values": thread_values,
        "thread_batch_values": thread_batch_values,
        "prompt_targets": prompt_targets,
        "repetitions": args.repetitions,
        "results": results,
        "recommended": best,
    }

    output_path = Path(args.out)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps({
        "output": str(output_path),
        "recommended": best,
        "total_cases": len(results),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
