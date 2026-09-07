from concurrent.futures import ThreadPoolExecutor

import pytest

from i18n.i18n_manager import I18nSession, reset_session, set_language
from wellbeing_session.handlers.chat_input_handler import (
    build_crisis_response,
    build_safety_check_response,
)


REQUIRED_CRISIS_KEYS = {
    "header",
    "intro",
    "line1_name",
    "line1_number",
    "line1_desc",
    "line2_name",
    "line2_number",
    "line3_name",
    "line3_url",
    "immediate",
    "safety_question",
    "check_intro",
    "closing",
}


@pytest.fixture(autouse=True)
def reset_i18n_context():
    reset_session()
    yield
    reset_session()


@pytest.mark.parametrize(
    ("language", "expected_header", "expected_immediate", "expected_contact"),
    [
        ("de", "Krisenhinweis", "unmittelbare Gefahr", "0800 111 0 111"),
        ("en", "Crisis Notice", "immediate danger", "988"),
        ("bg", "Спешно известие", "непосредствена опасност", "02 800 700"),
    ],
)
def test_acute_crisis_response_is_localized_and_actionable(
    language,
    expected_header,
    expected_immediate,
    expected_contact,
):
    set_language(language)

    response = build_crisis_response("acute")

    assert expected_header in response
    assert expected_immediate in response
    assert expected_contact in response
    assert "findahelpline.com" in response or "online.telefonseelsorge.de" in response


@pytest.mark.parametrize("language", ["de", "en", "bg"])
def test_non_acute_crisis_response_omits_immediate_danger_instruction(language):
    session = I18nSession()
    session.set_language(language)
    immediate_instruction = session.t("wellbeing.crisis.immediate")
    set_language(language)

    response = build_crisis_response("elevated")

    assert immediate_instruction not in response
    assert session.t("wellbeing.crisis.safety_question") in response


@pytest.mark.parametrize("language", ["de", "en", "bg"])
def test_safety_check_is_concise_and_omits_crisis_resources(language):
    session = I18nSession()
    session.set_language(language)
    set_language(language)

    response = build_safety_check_response()

    assert session.t("wellbeing.crisis.check_intro") in response
    assert session.t("wellbeing.crisis.safety_question") in response
    assert session.t("wellbeing.crisis.line1_number") not in response
    assert session.t("wellbeing.crisis.header") not in response


def test_all_supported_locales_define_the_complete_crisis_contract():
    session = I18nSession()

    for language in session.get_supported_languages():
        crisis_data = session._locales[language]["wellbeing"]["crisis"]
        assert REQUIRED_CRISIS_KEYS <= crisis_data.keys(), language
        assert all(str(crisis_data[key]).strip() for key in REQUIRED_CRISIS_KEYS), language


def test_parallel_crisis_responses_keep_language_context_isolated():
    def render(language):
        reset_session()
        set_language(language)
        return build_crisis_response("acute")

    with ThreadPoolExecutor(max_workers=3) as executor:
        responses = dict(zip(
            ("de", "en", "bg"),
            executor.map(render, ("de", "en", "bg")),
        ))

    assert "Krisenhinweis" in responses["de"]
    assert "Crisis Notice" in responses["en"]
    assert "Спешно известие" in responses["bg"]
    assert "Crisis Notice" not in responses["de"]
    assert "Krisenhinweis" not in responses["bg"]