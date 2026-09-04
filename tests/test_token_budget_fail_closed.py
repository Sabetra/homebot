from copy import deepcopy

import pytest

from wellbeing_session.context.token_budget_manager import (
    TokenBudgetExceededError,
    TokenBudgetManager,
)


class _CharacterTokenizer:
    def count_tokens(self, text):
        return len(text)


def test_emergency_trim_preserves_immutable_messages_and_input():
    manager = TokenBudgetManager(
        model_loader=_CharacterTokenizer(),
        n_ctx=120,
        generation_reserve=20,
    )
    system_prompt = "safety instructions"
    user_query = "my exact current query"
    manager.set_system_prompt(system_prompt)
    manager.set_user_query(user_query)
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": "old " * 20},
        {"role": "assistant", "content": "reply " * 20},
        {
            "role": "user",
            "content": "optional retrieved context " * 20 + user_query,
        },
    ]
    original = deepcopy(messages)

    trimmed = manager.emergency_trim_messages(messages)

    assert messages == original
    assert trimmed[0] == {"role": "system", "content": system_prompt}
    assert trimmed[-1] == {"role": "user", "content": user_query}
    assert manager.validate_messages(trimmed)[0]


def test_emergency_trim_drops_enriched_system_context_but_keeps_safety_base():
    manager = TokenBudgetManager(
        model_loader=_CharacterTokenizer(),
        n_ctx=90,
        generation_reserve=20,
    )
    safety_base = "immutable safety"
    full_system_prompt = safety_base + (" optional profile context" * 10)
    user_query = "current query"
    manager.set_system_prompt(full_system_prompt, immutable_text=safety_base)
    manager.set_user_query(user_query)

    trimmed = manager.emergency_trim_messages(
        [
            {"role": "system", "content": full_system_prompt},
            {"role": "user", "content": user_query},
        ]
    )

    assert trimmed == [
        {"role": "system", "content": safety_base},
        {"role": "user", "content": user_query},
    ]
    assert manager.validate_messages(trimmed)[0]


def test_emergency_trim_fails_when_immutable_content_cannot_fit():
    manager = TokenBudgetManager(
        model_loader=_CharacterTokenizer(),
        n_ctx=50,
        generation_reserve=10,
    )
    system_prompt = "s" * 30
    user_query = "q" * 20
    manager.set_system_prompt(system_prompt)
    manager.set_user_query(user_query)

    with pytest.raises(TokenBudgetExceededError, match="Immutable"):
        manager.emergency_trim_messages(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_query},
            ]
        )
