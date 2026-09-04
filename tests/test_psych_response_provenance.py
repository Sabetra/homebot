from wellbeing_session.response_provenance import (
    build_psych_web_provenance_instruction,
    finalize_psych_response_provenance,
    validate_psych_response_provenance,
)


def test_rejects_research_claim_and_urls_without_verified_web_sources() -> None:
    response = (
        "Nach meiner Online-Recherche sind diese Verfahren gut belegt. "
        "Siehe [EMDR](https://www.deutsche-emdr-gesellschaft.de/was-ist-emdr/) "
        "und https://morgenroth.at/wise-mind/."
    )

    result = validate_psych_response_provenance(response, verified_web_urls=())

    assert result.is_valid is False
    assert result.has_unsupported_research_claim is True
    assert result.unsupported_urls == (
        "https://www.deutsche-emdr-gesellschaft.de/was-ist-emdr/",
        "https://morgenroth.at/wise-mind/",
    )


def test_allows_only_exact_urls_from_request_local_web_results() -> None:
    verified_url = "https://example.org/verified-article"
    response = f"Die ergänzende Websuche ergab: [Quelle]({verified_url})."

    result = validate_psych_response_provenance(
        response,
        verified_web_urls=(verified_url,),
    )

    assert result.is_valid is True
    assert result.has_unsupported_research_claim is False
    assert result.unsupported_urls == ()


def test_rejects_unverified_url_even_when_other_web_source_was_verified() -> None:
    response = (
        "Die Websuche ergab [eine Quelle](https://example.org/verified), "
        "außerdem https://invented.example/advice."
    )

    result = validate_psych_response_provenance(
        response,
        verified_web_urls=("https://example.org/verified",),
    )

    assert result.is_valid is False
    assert result.unsupported_urls == ("https://invented.example/advice",)


def test_regular_therapeutic_response_needs_no_web_provenance() -> None:
    response = "Es klingt, als ob beide Bedürfnisse gleichzeitig Raum brauchen."

    result = validate_psych_response_provenance(response, verified_web_urls=())

    assert result.is_valid is True
    assert result.has_unsupported_research_claim is False
    assert result.unsupported_urls == ()


def test_no_source_prompt_explicitly_forbids_research_claims_and_urls() -> None:
    instruction = build_psych_web_provenance_instruction(())

    assert "keine verifizierte Online-Recherche" in instruction
    assert "keine externen URLs" in instruction


def test_invalid_draft_is_regenerated_once_and_valid_retry_survives() -> None:
    attempts: list[str] = []

    def regenerate(correction_instruction: str) -> str:
        attempts.append(correction_instruction)
        return "Ich stütze diese Einordnung auf allgemeines Fachwissen, nicht auf eine aktuelle Websuche."

    response, was_replaced = finalize_psych_response_provenance(
        "Meine Online-Recherche: https://invented.example/advice",
        verified_web_urls=(),
        regenerate=regenerate,
        language="de",
    )

    assert len(attempts) == 1
    assert "nicht ausgeben" in attempts[0]
    assert was_replaced is True
    assert "https://" not in response


def test_invalid_retry_fails_closed_without_leaking_draft() -> None:
    attempts = 0

    def regenerate(_: str) -> str:
        nonlocal attempts
        attempts += 1
        return "Laut meiner Websuche: https://still-invented.example/source"

    response, was_replaced = finalize_psych_response_provenance(
        "Online-Recherche: https://invented.example/advice",
        verified_web_urls=(),
        regenerate=regenerate,
        language="de",
    )

    assert attempts == 1
    assert was_replaced is True
    assert "https://" not in response
    assert "verifizierte Quellen" in response


def test_regeneration_error_fails_closed() -> None:
    def regenerate(_: str) -> str:
        raise RuntimeError("model unavailable")

    response, was_replaced = finalize_psych_response_provenance(
        "Meine Online-Recherche: https://invented.example/advice",
        verified_web_urls=(),
        regenerate=regenerate,
        language="en",
    )

    assert was_replaced is True
    assert "https://" not in response
    assert "unsupported source claims" in response


def test_english_research_claim_without_url_is_rejected() -> None:
    result = validate_psych_response_provenance(
        "According to my online research, this method is effective.",
        verified_web_urls=(),
    )

    assert result.is_valid is False
    assert result.has_unsupported_research_claim is True


def test_positive_research_heading_without_url_is_rejected() -> None:
    result = validate_psych_response_provenance(
        "Online-Recherche ergab mehrere wirksame Ansätze.",
        verified_web_urls=(),
    )

    assert result.is_valid is False
    assert result.has_unsupported_research_claim is True


def test_transparent_no_research_statement_is_allowed() -> None:
    result = validate_psych_response_provenance(
        "Ich habe keine Online-Recherche durchgeführt und antworte aus allgemeinem Wissen.",
        verified_web_urls=(),
    )

    assert result.is_valid is True
    assert result.has_unsupported_research_claim is False
