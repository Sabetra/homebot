"""Compliance tests: visible legal disclaimers for Finance & Psychology.

These disclaimers are a release requirement (see
docs/WORKDOC_PUBLIC_LAUNCH_20260831.md, Stage 2):

- ``finance_ui.main.disclaimer`` — "not tax/legal/investment advice"
- ``wellbeing.disclaimer`` — "not a medical service; crisis resources"

Guarantees:
- Both keys exist in EVERY locale with a real, non-placeholder translation.
- The wellbeing disclaimer always points to crisis resources (e.g. "112").
- The UI code still references the keys (regression guard against
  accidental removal of the visible banner).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
LOCALES_DIR = ROOT / "i18n" / "locales"

DISCLAIMER_KEYS = (
    "finance_ui.main.disclaimer",
    "wellbeing.disclaimer",
)


def _flatten(obj: dict, prefix: str = "") -> dict:
    flat: dict = {}
    for k, v in obj.items():
        full = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            flat.update(_flatten(v, full))
        else:
            flat[full] = v
    return flat


@pytest.mark.parametrize("locale", ["de", "en", "bg"])
def test_disclaimer_keys_present_in_all_locales(locale: str) -> None:
    path = LOCALES_DIR / f"{locale}.json"
    assert path.exists(), f"missing locale file: {path}"
    with open(path, encoding="utf-8") as fh:
        flat = _flatten(json.load(fh))
    for key in DISCLAIMER_KEYS:
        assert key in flat, f"missing disclaimer key '{key}' in {locale}.json"
        value = flat[key]
        assert isinstance(value, str) and len(value) >= 40, (
            f"disclaimer '{key}' in {locale}.json looks like a placeholder: {value!r}"
        )
        assert key not in value, f"disclaimer '{key}' in {locale}.json contains the key itself"


@pytest.mark.parametrize("locale", ["de", "en", "bg"])
def test_psych_disclaimer_mentions_crisis_resources(locale: str) -> None:
    with open(LOCALES_DIR / f"{locale}.json", encoding="utf-8") as fh:
        flat = _flatten(json.load(fh))
    value = flat["wellbeing.disclaimer"].lower()
    assert "112" in value, (
        "psychological disclaimer must reference emergency services (112)"
    )


def test_finance_ui_still_renders_disclaimer() -> None:
    src = (ROOT / "finance" / "tab.py").read_text(encoding="utf-8")
    assert '"finance_ui.main.disclaimer"' in src, (
        "finance/tab.py no longer renders the finance disclaimer"
    )


def test_wellbeing_ui_still_renders_disclaimer() -> None:
    src = (ROOT / "wellbeing_session_interface.py").read_text(encoding="utf-8")
    assert '"wellbeing.disclaimer"' in src, (
        "wellbeing_session_interface.py no longer renders the wellbeing disclaimer"
    )


# ── EU AI Act Art. 50(1): AI-system disclosure ─────────────────────────
AI_DISCLOSURE_KEY = "wellbeing_ui.welcome.ai_note"


def test_ai_disclosure_key_present_in_all_locales() -> None:
    """The AI-system disclosure must exist in every locale with real content."""
    for locale in ("de", "en", "bg"):
        path = LOCALES_DIR / f"{locale}.json"
        assert path.exists(), f"missing locale file: {path}"
        with open(path, encoding="utf-8") as fh:
            flat = _flatten(json.load(fh))
        assert AI_DISCLOSURE_KEY in flat, (
            f"missing AI-disclosure key '{AI_DISCLOSURE_KEY}' in {locale}.json"
        )
        value = flat[AI_DISCLOSURE_KEY]
        assert isinstance(value, str) and len(value) >= 20, (
            f"AI-disclosure '{AI_DISCLOSURE_KEY}' in {locale}.json looks like a "
            f"placeholder: {value!r}"
        )
        assert AI_DISCLOSURE_KEY not in value


def test_ui_renders_ai_disclosure() -> None:
    """Regression guard: the visible banner must still reference the key.

    The disclosure is rendered in ``render_complete_interface`` so it is
    visible in BOTH the welcome view and the active session view.
    """
    src = (ROOT / "wellbeing_session_interface.py").read_text(encoding="utf-8")
    assert '"wellbeing_ui.welcome.ai_note"' in src, (
        "wellbeing_session_interface.py no longer renders the AI disclosure"
    )


# ── Non-clinical positioning of user-facing strings ────────────────────
# User-facing psych-UI strings must NOT self-identify as clinical/medical.
# (The ``wellbeing.disclaimer`` intentionally *negates* medical service —
# "not a substitute for ... diagnosis, or therapy" — and is excluded on purpose.)
NON_CLINICAL_UI_KEYS = (
    "wellbeing_ui.welcome.header",
    "wellbeing_ui.welcome.intro",
    "wellbeing_ui.welcome.start_button",
    "wellbeing_ui.active.subheader",
    "wellbeing_ui.active.chat_header",
    "wellbeing_ui.session.header",
    "wellbeing_ui.lifecycle.new_success",
)
CLINICAL_TERMS = (
    "psychologische", "psychologischer", "psychologischen",
    "therapie", "therapeutisch", "diagnose", "behandlung",
    "therapy", "therapeutic", "diagnosis", "treatment", "psychological",
    "терапия", "диагноза", "психологическ",
)


@pytest.mark.parametrize("locale", ["de", "en", "bg"])
def test_user_facing_strings_not_clinically_positioned(locale: str) -> None:
    """Guard against re-introducing clinical self-positioning in the UI."""
    with open(LOCALES_DIR / f"{locale}.json", encoding="utf-8") as fh:
        flat = _flatten(json.load(fh))
    for key in NON_CLINICAL_UI_KEYS:
        assert key in flat, f"missing key '{key}' in {locale}.json"
        value = flat[key].lower()
        for term in CLINICAL_TERMS:
            assert term not in value, (
                f"'{key}' in {locale}.json is clinically positioned "
                f"(found '{term}'): {flat[key]!r}"
            )


# ── Non-clinical positioning of Python-side strings (UI status + LLM voice) ──
# Phase A.5: user-visible status/profile strings in agent_chatbot_logic.py and
# the LLM system prompt must NOT self-identify as clinical. This closes the
# remaining "psychologisch" voice-leak channels that Phase A (i18n/README) left.
def test_chatbot_ui_strings_not_clinical() -> None:
    """User-visible status/profile strings in agent_chatbot_logic.py are non-clinical."""
    low = (ROOT / "agent_chatbot_logic.py").read_text(encoding="utf-8").lower()
    for term in (
        "psychologischer chat",
        "therapeutischer modus",
        "psychologisches profil von",
        "fehler beim psychologischen chat",
        "psycho-chat-fehler",
    ):
        assert term not in low, (
            f"agent_chatbot_logic.py still contains a clinical user-facing string: '{term}'"
        )


def test_llm_system_prompt_not_clinically_self_identified() -> None:
    """The LLM system prompt must not self-identify as 'psychologisch' (bot voice).

    The GRENZEN negation "ohne eine Psychotherapie ... vorzutäuschen" is allowed
    on purpose (it is a boundary, not a self-identification) and is NOT matched,
    because it contains "psychotherapie", not the adjective "psychologisch".
    """
    src = (
        ROOT / "wellbeing_session" / "handlers" / "response_generator.py"
    ).read_text(encoding="utf-8")
    block = src[src.index("CARE_SYSTEM_PROMPT_BASE"):][:4000].lower()
    assert "psychologisch" not in block, (
        "CARE_SYSTEM_PROMPT_BASE still self-identifies as 'psychologisch'"
    )


def test_llm_facing_context_labels_not_clinical() -> None:
    """LLM-facing section labels in context_formatter.py must be non-clinical.

    These labels are part of the formatted context sent to the model, so they
    shape the bot's voice. (Internal identifiers such as the ``care_goals``
    data key and the ``_format_care_goals`` method are intentionally left
    for the later internal-rename session and are NOT checked here.)
    """
    low = (
        ROOT / "wellbeing_session" / "context" / "context_formatter.py"
    ).read_text(encoding="utf-8").lower()
    for term in (
        "therapeutische schwerpunkte",
        "🎯 therapeutische ziele",
    ):
        assert term not in low, (
            f"context_formatter.py still has a clinical LLM-facing label: '{term}'"
        )




