"""Request-local provenance validation for user-visible wellbeing responses."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Callable, Iterable


_URL_PATTERN = re.compile(r"https?://[^\s<>\]\[\"']+", re.IGNORECASE)
_TRAILING_URL_PUNCTUATION = ".,;:!?)]}"
_RESEARCH_CLAIM_PATTERNS = (
    re.compile(
        r"\b(?:meine[rn]?|einer|der|durch|laut)\s+"
        r"(?:online[- ]?(?:recherche|suche)|web[- ]?(?:recherche|suche))\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:ich\s+habe|wir\s+haben)\s+(?:online|im\s+(?:internet|web))\s+"
        r"(?:recherchiert|gesucht|nachgesehen)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:ich\s+habe|wir\s+haben)\s+(?:eine\s+)?"
        r"(?:online[- ]?(?:recherche|suche)|web[- ]?(?:recherche|suche))\s+"
        r"(?:durchgeführt|gemacht)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:online[- ]?(?:recherche|suche)|web[- ]?(?:recherche|suche))\s*"
        r"(?:ergab|ergibt|zeigt|belegt|bestätigt|zufolge)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:my|an|the|through|according\s+to\s+(?:my|the))\s+"
        r"(?:online|web)\s+(?:research|search)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:i|we)\s+(?:searched|researched|looked\s+up)\s+"
        r"(?:online|the\s+(?:internet|web))\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:моето|едно|това|според)\s+(?:онлайн|уеб)\s+"
        r"(?:проучване|търсене)\b",
        re.IGNORECASE,
    ),
)


@dataclass(frozen=True)
class WellbeingResponseProvenanceResult:
    """Validation result for one generated wellbeing response."""

    is_valid: bool
    unsupported_urls: tuple[str, ...]
    has_unsupported_research_claim: bool


def _normalize_url(url: str) -> str:
    return url.rstrip(_TRAILING_URL_PUNCTUATION)


def extract_external_urls(text: str) -> tuple[str, ...]:
    """Extract unique HTTP(S) URLs while removing surrounding prose punctuation."""
    return tuple(dict.fromkeys(_normalize_url(match.group(0)) for match in _URL_PATTERN.finditer(text)))


def validate_wellbeing_response_provenance(
    response: str,
    *,
    verified_web_urls: Iterable[str],
) -> WellbeingResponseProvenanceResult:
    """Reject URLs and web-research claims not backed by this request's tool results."""
    allowed_urls = {
        normalized
        for url in verified_web_urls
        if (normalized := _normalize_url(str(url).strip()))
    }
    response_urls = extract_external_urls(response)
    unsupported_urls = tuple(url for url in response_urls if url not in allowed_urls)
    claims_web_research = any(pattern.search(response) for pattern in _RESEARCH_CLAIM_PATTERNS)
    unsupported_claim = claims_web_research and not allowed_urls

    return WellbeingResponseProvenanceResult(
        is_valid=not unsupported_urls and not unsupported_claim,
        unsupported_urls=unsupported_urls,
        has_unsupported_research_claim=unsupported_claim,
    )


def build_wellbeing_web_provenance_instruction(verified_web_urls: Iterable[str]) -> str:
    """Build a request-local system instruction from actual web tool output."""
    allowed_urls = tuple(
        dict.fromkeys(
            normalized
            for url in verified_web_urls
            if (normalized := _normalize_url(str(url).strip()))
        )
    )
    if not allowed_urls:
        return (
            "\n\nQUELLEN-PROVENIENZ (VERBINDLICH):\n"
            "- Für diesen Request wurde keine verifizierte Online-Recherche ausgeführt.\n"
            "- Behaupte nicht, online, im Web oder im Internet recherchiert oder gesucht zu haben.\n"
            "- Gib keine externen URLs, Links oder vermeintlichen Online-Quellen aus.\n"
            "- Nutze allgemeines Fachwissen transparent als solches und benenne Unsicherheit."
        )

    rendered_urls = "\n".join(f"  - {url}" for url in allowed_urls)
    return (
        "\n\nQUELLEN-PROVENIENZ (VERBINDLICH):\n"
        "- Für diesen Request liegen verifizierte Web-Tool-Ergebnisse vor.\n"
        "- Als externe URLs sind ausschließlich die folgenden exakten URLs zulässig:\n"
        f"{rendered_urls}\n"
        "- Erfinde, vervollständige oder verändere keine URL und nenne keine andere Online-Quelle."
    )


def _failure_response(language: str) -> str:
    if language == "bg":
        return (
            "Не мога надеждно да предоставя този отговор без непотвърдени твърдения за източници. "
            "Моля, формулирайте въпроса отново; ще отговоря без да твърдя, че съм търсил онлайн."
        )
    if language == "en":
        return (
            "I cannot provide this answer reliably without unsupported source claims. "
            "Please rephrase the question; I will answer without claiming that I searched online."
        )
    return (
        "Ich kann diese Antwort nicht zuverlässig ohne unbelegte Behauptungen über verifizierte Quellen "
        "ausgeben. Bitte formuliere die Frage neu; ich antworte dann ohne vorzugeben, online recherchiert zu haben."
    )


def finalize_wellbeing_response_provenance(
    response: str,
    *,
    verified_web_urls: Iterable[str],
    regenerate: Callable[[str], str],
    language: str = "de",
) -> tuple[str, bool]:
    """Validate a draft, regenerate once on violation, then fail closed."""
    allowed_urls = tuple(verified_web_urls)
    initial_result = validate_wellbeing_response_provenance(
        response,
        verified_web_urls=allowed_urls,
    )
    if initial_result.is_valid:
        return response, False

    violations = []
    if initial_result.has_unsupported_research_claim:
        violations.append("unbelegte Behauptung einer Online-Recherche")
    if initial_result.unsupported_urls:
        violations.append("nicht durch Tool-Ergebnisse belegte URL")
    correction_instruction = (
        "\n\nKORREKTUR DES VERWORFENEN ENTWURFS (VERBINDLICH):\n"
        f"Der vorherige Entwurf enthielt: {', '.join(violations)}. "
        "Schreibe die Antwort vollständig neu. Diese Elemente nicht ausgeben."
        + build_wellbeing_web_provenance_instruction(allowed_urls)
    )
    try:
        retry_response = regenerate(correction_instruction)
    except Exception:
        return _failure_response(language), True
    retry_result = validate_wellbeing_response_provenance(
        retry_response,
        verified_web_urls=allowed_urls,
    )
    if retry_result.is_valid:
        return retry_response, True
    return _failure_response(language), True