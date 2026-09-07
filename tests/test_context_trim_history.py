"""Differential tests for ``agent/context.py::ContextManager.trim_history``.

Codebase-Audit (2026-08-28), Phase 3: ``trim_history`` was rewritten from
O(blocks * n) -- re-estimating the ENTIRE remaining list on every iteration,
which with the real tokenizer re-tokenizes every remaining message (GPU, under
``cuda_lock``) each iteration -- to O(n): compute each message's cost ONCE and
subtract the dropped block's cost from a running total.

The active estimator is additive (``count_messages_tokens == sum of per-message
costs``) when a real tokenizer is present, so the new path is EXACTLY
equivalent. The heuristic fallback (``"\\n".join(...) // CHARS_PER_TOKEN``) is
NOT additive, so that path keeps the original loop verbatim.

These tests prove the new implementation returns the SAME result as the
original algorithm for both estimator paths, plus invariance checks.
"""
import random

from agent.context import ContextManager


class FakeTokenizerLoader:
    """Deterministic, additive stand-in for ``ModelLoader``.

    ``count_tokens`` is non-trivial (not a bare ``len``) but deterministic;
    ``count_messages_tokens`` mirrors ``ModelLoader.count_messages_tokens``
    exactly: ``cost(msg) = count_tokens(content) + 4`` (content non-empty str)
    or ``4`` otherwise.
    """

    def count_tokens(self, text: str) -> int:
        return len(text.split()) + len(text) // 7

    def count_messages_tokens(self, messages) -> int:
        total = 0
        for msg in messages:
            content = msg.get("content", "")
            if isinstance(content, str) and content:
                total += self.count_tokens(content)
            total += 4
        return total


def _original_trim_history(estimate, history, max_tokens):
    """Verbatim reference copy of the pre-optimization ``trim_history``."""
    trimmed = list(history)
    while trimmed and estimate(trimmed) > max_tokens:
        idx = 0
        while idx < len(trimmed) and trimmed[idx].get("role") != "user":
            idx += 1
        if idx < len(trimmed):
            del trimmed[: idx + 1]
        else:
            trimmed.pop(0)
    return trimmed


_WORDS = ["alpha", "beta", "gamma", "delta", "epsilon", "zeta", "eta", "theta"]


def _random_history(rng: random.Random, n: int):
    history = []
    for _ in range(n):
        role = rng.choice(["user", "assistant", "system"])
        r = rng.random()
        if r < 0.08:
            content = ""                    # empty string
        elif r < 0.16:
            content = ["multi", "part"]     # non-string content
        else:
            content = " ".join(rng.choice(_WORDS) for _ in range(rng.randint(0, 10)))
        history.append({"role": role, "content": content})
    return history


def test_trim_history_tokenizer_path_matches_original():
    rng = random.Random(20260828)
    cm = ContextManager(n_ctx=8192, model_loader=FakeTokenizerLoader())
    for trial in range(500):
        n = rng.randint(0, 14)
        history = _random_history(rng, n)
        max_tokens = rng.randint(0, 220)
        expected = _original_trim_history(cm.estimate_tokens, history, max_tokens)
        actual = cm.trim_history(history, max_tokens)
        assert actual == expected, (
            f"Mismatch (trial={trial}, n={n}, max_tokens={max_tokens})\n"
            f"history  = {history}\n"
            f"expected = {expected}\n"
            f"actual   = {actual}"
        )


def test_trim_history_does_not_mutate_input():
    rng = random.Random(7)
    cm = ContextManager(n_ctx=8192, model_loader=FakeTokenizerLoader())
    history = _random_history(rng, 10)
    snapshot = [dict(m) for m in history]
    cm.trim_history(history, 50)
    assert history == snapshot


def test_trim_history_empty_and_budget_ok():
    cm = ContextManager(n_ctx=8192, model_loader=FakeTokenizerLoader())
    assert cm.trim_history([], 100) == []
    hist = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}]
    assert cm.trim_history(hist, 10_000) == hist


def test_trim_history_heuristic_path_still_works():
    # No model_loader -> non-additive heuristic path (original loop preserved).
    cm = ContextManager(n_ctx=8192, model_loader=None)
    history = [
        {"role": "user", "content": "a" * 80},
        {"role": "assistant", "content": "b" * 80},
        {"role": "user", "content": "c" * 80},
        {"role": "assistant", "content": "d" * 80},
    ]
    expected = _original_trim_history(cm.estimate_tokens, history, 30)
    actual = cm.trim_history(history, 30)
    assert actual == expected
    if actual:
        assert cm.estimate_tokens(actual) <= 30