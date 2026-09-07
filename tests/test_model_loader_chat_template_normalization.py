import pytest

from scripts.model_loader import ModelLoader
from utils import token_scaling as _ts
from utils.token_scaling import TokenScalingProposal


@pytest.fixture(autouse=True)
def _reset_token_scaling_registry():
    """Isoliert die globale Token-Scaling-Registry (Test-Isolation).

    ``_render_chat_template`` faellt zurueck auf
    ``token_scaling.current_proposal()`` (SOTA 2026-09-04), wenn der
    Caller keine expliziten Werte ubergibt. Ein von einem fruheren Test
    zurueckgebliebener Vorschlag wuerde in spaetere Render-Tests
    durchschlagen -- daher vor und nach jedem Test resetten.
    """
    _ts.set_current_proposal(None)
    yield
    _ts.set_current_proposal(None)


def test_chat_template_normalizer_folds_tool_roles_into_alternating_turns():
    messages = [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "user-1"},
        {"role": "assistant", "content": "assistant-1"},
        {"role": "tool", "content": "tool-result"},
        {"role": "assistant_tool_call", "content": "tool-call"},
        {"role": "user", "content": "user-2"},
        {"role": "tool_response", "content": "tool-response"},
    ]

    normalized = ModelLoader._normalize_messages_for_chat_template(messages)

    assert [msg["role"] for msg in normalized] == ["system", "user", "assistant", "user", "assistant"]
    assert "tool-result" in normalized[2]["content"]
    assert "tool-call" in normalized[2]["content"]
    assert normalized[4]["role"] == "assistant"
    assert "tool-response" in normalized[4]["content"]


# Minimale Nachbildung der Qwen3.x-Reasoning-Logik (enable_thinking / Prefill).
_QWEN_LIKE_TEMPLATE = (
    "{% for message in messages %}"
    "<|im_start|>{{ message.role }}\n{{ message.content }}<|im_end|>\n"
    "{% endfor %}"
    "{% if add_generation_prompt %}<|im_start|>assistant\n"
    "{% if enable_thinking is defined and enable_thinking is false %}"
    "<think>\n\n</think>\n\n"
    "{% else %}<think>\n{% endif %}{% endif %}"
)


def _make_template_loader(template: str = _QWEN_LIKE_TEMPLATE) -> ModelLoader:
    import jinja2

    loader = ModelLoader.__new__(ModelLoader)
    loader._jinja_template = jinja2.Environment().from_string(template)
    loader._supports_system_role = True
    loader._bos_token = "<|endoftext|>"
    loader._eos_token = "<|im_end|>"
    loader._ensure_template_capabilities = lambda: None  # type: ignore[method-assign]
    return loader


def test_render_chat_template_disables_thinking_with_prefilled_block():
    loader = _make_template_loader()

    rendered = loader._render_chat_template(
        [{"role": "user", "content": "Hi"}], tools=None, enable_thinking=False
    )

    assert rendered.endswith("<think>\n\n</think>\n\n")


def test_render_chat_template_keeps_thinking_by_default():
    loader = _make_template_loader()

    rendered = loader._render_chat_template(
        [{"role": "user", "content": "Hi"}], tools=None
    )

    assert rendered.endswith("<think>\n")


# Minimales Template, das die reasoning_effort/thinking_budget-Variablen
# liest (wie Qwen3.x) -- erlaubt das echte Forwarding zu pruefen, ohne
# das komplette Produktions-Template zu benoetigen.
_REASONING_TEMPLATE = (
    "{% for message in messages %}{{ message.content }}{% endfor %}"
    "[effort={{ reasoning_effort if reasoning_effort is defined else 'none' }}]"
    "[budget={{ thinking_budget if thinking_budget is defined else 'none' }}]"
)

# Geschlossener Think-Tag als Escape (\x3c/think\x3e): die Datei enthaelt
# bewusst keine Literal-Tags (Transport-Sicherheit); zur Laufzeit identisch.
_CLOSE_THINK = "\x3c/think\x3e"


def _reasoning_proposal(
    thinking_budget: int, reasoning_effort: str
) -> TokenScalingProposal:
    return TokenScalingProposal(
        n_ctx=16384,
        kv_quant="q8_0",
        output_budget=4096,
        thinking_budget=thinking_budget,
        reasoning_effort=reasoning_effort,
    )


def test_render_chat_template_forwards_reasoning_effort_and_budget():
    """Regression (2026-09-04): Der echte Render-Pfad darf keinen
    NameError fuer ``reasoning_effort`` ausloesen.

    Die alten Streaming-Tests ersetzten ``_render_chat_template`` durch
    ein Lambda und haben den Bug verdeckt. Hier laeuft die echte Methode
    mit echtem Jinja-Template.
    """
    loader = _make_template_loader(_REASONING_TEMPLATE)

    rendered = loader._render_chat_template(
        [{"role": "user", "content": "Hi"}],
        tools=None,
        reasoning_effort="medium",
        thinking_budget=1024,
    )

    assert "[effort=medium]" in rendered
    assert "[budget=1024]" in rendered


def test_render_chat_template_falls_back_to_active_proposal():
    """Ohne Caller-Werte liefert der aktive Token-Scaling-Vorschlag
    Budget + Effort."""
    loader = _make_template_loader(_REASONING_TEMPLATE)
    _ts.set_current_proposal(_reasoning_proposal(2048, "medium"))

    rendered = loader._render_chat_template(
        [{"role": "user", "content": "Hi"}], tools=None
    )

    assert "[effort=medium]" in rendered
    assert "[budget=2048]" in rendered


def test_render_chat_template_explicit_values_win_over_proposal():
    """Explizite Caller-Werte gewinnen IMMER gegen den aktiven Vorschlag."""
    loader = _make_template_loader(_REASONING_TEMPLATE)
    _ts.set_current_proposal(_reasoning_proposal(2048, "high"))

    rendered = loader._render_chat_template(
        [{"role": "user", "content": "Hi"}],
        tools=None,
        reasoning_effort="low",
        thinking_budget=512,
    )

    assert "[effort=low]" in rendered
    assert "[budget=512]" in rendered


def test_render_chat_template_proposal_off_disables_thinking():
    """Vorschlag mit effort='off' + Budget=0 schaltet Thinking AUS
    (enable_thinking=False) -- auch bei Thinking-faehigem Template."""
    loader = _make_template_loader()  # Qwen-like: Thinking Default
    _ts.set_current_proposal(_reasoning_proposal(0, "off"))

    rendered = loader._render_chat_template(
        [{"role": "user", "content": "Hi"}], tools=None
    )

    assert rendered.endswith(_CLOSE_THINK + "\n\n")


def test_render_chat_template_explicit_off_wins_over_reasoning_proposal():
    """Explizites ``enable_thinking=False`` (z.B. Grammar-Pfad) gewinnt
    gegen einen Vorschlag mit positivem Budget."""
    loader = _make_template_loader()  # Qwen-like: Thinking Default
    _ts.set_current_proposal(_reasoning_proposal(2048, "medium"))

    rendered = loader._render_chat_template(
        [{"role": "user", "content": "Hi"}], tools=None, enable_thinking=False
    )

    assert rendered.endswith(_CLOSE_THINK + "\n\n")


def test_strip_reasoning_markup_removes_orphan_closing_think():
    # Qwen3.x: <think> steht im Prompt-Prefill, Output enthält nur </think>
    raw = "Compute 17*23 = 391. Need final only number.\n</think>\n\n391"

    assert ModelLoader._strip_reasoning_markup(raw) == "391"


def test_strip_reasoning_markup_removes_paired_think_block():
    raw = "<think>reasoning here</think>\nAntwort"

    assert ModelLoader._strip_reasoning_markup(raw) == "Antwort"


def test_strip_reasoning_markup_keeps_content_for_planner():
    raw = "<think>plan steps</think> result"

    assert ModelLoader._strip_reasoning_markup(raw, strip_think_blocks=False) == "plan steps result"