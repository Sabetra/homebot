"""
Differential equivalence tests for the optimized emergency_trim_messages
(2026-08-28, P2: additive token accounting).

The legacy algorithm (repeated full-list tokenization per removal step) is
reproduced verbatim below as a frozen reference implementation. Every scenario
must produce identical results (or identical fail-closed errors) for the
optimized version and the legacy version.
"""
import copy
import logging

import pytest

from wellbeing_session.context.token_budget_manager import (
    TokenBudgetExceededError,
    TokenBudgetManager,
)

logging.disable(logging.CRITICAL)  # keep the reference implementation's logs quiet


class _CharacterTokenizer:
    def count_tokens(self, text):
        return len(text)


def _legacy_emergency_trim(manager, messages):
    """FROZEN COPY of the pre-2026-08-28 implementation — do not modify.

    Used exclusively as the differential reference in this test module.
    """
    if not messages:
        return messages

    trimmed = copy.deepcopy(messages)
    system_index = next(
        (index for index, message in enumerate(trimmed) if message.get("role") == "system"),
        None,
    )
    user_index = next(
        (index for index in range(len(trimmed) - 1, -1, -1)
         if trimmed[index].get("role") == "user"),
        None,
    )
    if system_index is None or user_index is None:
        raise TokenBudgetExceededError(
            "Cannot preserve prompt: system or current user message is missing"
        )

    trimmed[system_index]["content"] = manager._system_prompt
    trimmed[user_index]["content"] = manager._user_query
    immutable_messages = [trimmed[system_index], trimmed[user_index]]
    immutable_valid, immutable_tokens = manager.validate_messages(immutable_messages)
    if not immutable_valid:
        raise TokenBudgetExceededError(
            "Immutable system prompt and current user query exceed prompt budget "
            f"({immutable_tokens} > {manager._prompt_budget})"
        )

    protected = {system_index, user_index}
    optional_indices = [
        index for index in range(len(trimmed)) if index not in protected
    ]
    while optional_indices:
        is_valid, _ = manager.validate_messages(trimmed)
        if is_valid:
            return trimmed
        remove_index = optional_indices.pop(0)
        trimmed.pop(remove_index)
        optional_indices = [
            index - 1 if index > remove_index else index
            for index in optional_indices
        ]

    is_valid, total = manager.validate_messages(trimmed)
    if not is_valid:
        raise TokenBudgetExceededError(
            f"Emergency trim failed to satisfy prompt budget ({total} > {manager._prompt_budget})"
        )
    return trimmed


def _make_manager(model_loader, n_ctx, reserve):
    return TokenBudgetManager(
        model_loader=model_loader, n_ctx=n_ctx, generation_reserve=reserve
    )


def _compare(name, manager, messages):
    """Run both implementations and assert identical results or identical errors."""
    legacy = _legacy_emergency_trim(manager, copy.deepcopy(messages))
    optimized = manager.emergency_trim_messages(copy.deepcopy(messages))
    assert optimized == legacy, f"{name}: optimized != legacy"


@pytest.mark.parametrize("n_ctx, reserve", [(120, 20), (90, 20), (60, 10), (500, 32)])
def test_trim_equivalence_partial_and_full_drops(n_ctx, reserve):
    manager = _make_manager(_CharacterTokenizer(), n_ctx, reserve)
    system_prompt = "safety base " + ("opt ctx " * 6)
    user_query = "current user query"
    manager.set_system_prompt(system_prompt, immutable_text="safety base")
    manager.set_user_query(user_query)

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": "old question " * 5},
        {"role": "assistant", "content": "old answer " * 7},
        {"role": "user", "content": "rag evidence " * 9 + user_query},
    ]
    _compare(f"n_ctx={n_ctx},reserve={reserve}", manager, messages)


def test_trim_equivalence_no_removal_needed():
    manager = _make_manager(_CharacterTokenizer(), 1000, 20)
    manager.set_system_prompt("sys")
    manager.set_user_query("query")
    messages = [
        {"role": "system", "content": "something else entirely"},
        {"role": "user", "content": "old"},
        {"role": "assistant", "content": "reply"},
        {"role": "user", "content": "unrelated"},
    ]
    _compare("no-removal", manager, messages)
def test_trim_equivalence_all_optional_removed():
    manager = _make_manager(_CharacterTokenizer(), 80, 10)
    manager.set_system_prompt("s" * 10)
    manager.set_user_query("q" * 10)
    messages = [
        {"role": "system", "content": "s" * 10},
        {"role": "user", "content": "u" * 20},
        {"role": "assistant", "content": "a" * 20},
        {"role": "user", "content": "q" * 10},
    ]
    _compare("all-optional-removed", manager, messages)


def test_trim_equivalence_missing_roles_raise_identically():
    manager = _make_manager(_CharacterTokenizer(), 100, 10)
    manager.set_system_prompt("sys")
    manager.set_user_query("query")

    for label, messages in [
        ("no-system", [{"role": "user", "content": "x"}]),
        ("no-user", [{"role": "system", "content": "x"},
                     {"role": "assistant", "content": "y"}]),
    ]:
        legacy_exc = optimized_exc = None
        try:
            _legacy_emergency_trim(manager, copy.deepcopy(messages))
        except TokenBudgetExceededError as exc:
            legacy_exc = exc
        try:
            manager.emergency_trim_messages(copy.deepcopy(messages))
        except TokenBudgetExceededError as exc:
            optimized_exc = exc
        assert legacy_exc is not None and optimized_exc is not None, label
        assert str(optimized_exc) == str(legacy_exc), label


def test_trim_equivalence_immutable_too_large():
    manager = _make_manager(_CharacterTokenizer(), 50, 10)
    manager.set_system_prompt("s" * 30)
    manager.set_user_query("q" * 20)
    messages = [
        {"role": "system", "content": "s" * 30},
        {"role": "user", "content": "filler"},
        {"role": "assistant", "content": "filler"},
        {"role": "user", "content": "q" * 20},
    ]
    legacy_exc = optimized_exc = None
    try:
        _legacy_emergency_trim(manager, copy.deepcopy(messages))
    except TokenBudgetExceededError as exc:
        legacy_exc = exc
    try:
        manager.emergency_trim_messages(copy.deepcopy(messages))
    except TokenBudgetExceededError as exc:
        optimized_exc = exc
    assert legacy_exc is not None and optimized_exc is not None
    assert str(optimized_exc) == str(legacy_exc)


def test_trim_equivalence_system_after_user_and_non_string_content():
    manager = _make_manager(_CharacterTokenizer(), 100, 10)
    manager.set_system_prompt("sys " * 3)
    manager.set_user_query("query")
    messages = [
        {"role": "user", "content": "first user"},
        {"role": "system", "content": "sys " * 3},
        {"role": "assistant", "content": [{"type": "text", "text": "rich"}]},
        {"role": "user", "content": ""},
        {"role": "assistant", "content": "tail"},
    ]
    _compare("system-after-user", manager, messages)


def test_trim_equivalence_heuristic_tokenizer():
    # No model loader → len(text)//4 Fallback-Pfad
    manager = _make_manager(None, 100, 10)
    manager.set_system_prompt("s" * 20)
    manager.set_user_query("q" * 20)
    messages = [
        {"role": "system", "content": "s" * 20},
        {"role": "user", "content": "u" * 30},
        {"role": "assistant", "content": "a" * 40},
        {"role": "user", "content": "q" * 20},
    ]
    _compare("heuristic-tokenizer", manager, messages)


def test_trim_equivalence_empty_list_passthrough():
    manager = _make_manager(_CharacterTokenizer(), 100, 10)
    manager.set_system_prompt("sys")
    manager.set_user_query("query")
    assert _legacy_emergency_trim(manager, []) == []
    assert manager.emergency_trim_messages([]) == []


def test_optimized_trim_is_input_pure_and_result_isolated():
    manager = _make_manager(_CharacterTokenizer(), 120, 20)
    manager.set_system_prompt("safety")
    manager.set_user_query("query")
    messages = [
        {"role": "system", "content": "safety"},
        {"role": "user", "content": "old " * 15},
        {"role": "assistant", "content": "reply " * 15},
        {"role": "user", "content": "ctx " * 15 + "query"},
    ]
    original = copy.deepcopy(messages)
    result = manager.emergency_trim_messages(messages)
    assert messages == original
    assert result is not messages
    # Ergebnis-Dicts dürfen keine Input-Dicts sein (Rollback-Sicherheit:
    # spätere Mutationen der History dürfen das getrimmte Ergebnis nicht treffen)
    input_ids = {id(msg) for msg in messages}
    assert all(id(msg) not in input_ids for msg in result)