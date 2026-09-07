"""
Locale negotiation for deterministic i18n language resolution.

Resolution order:
1) explicit user override
2) session language
3) auto-detected language from user input
4) fallback language
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from i18n.i18n_manager import DEFAULT_LANGUAGE, SUPPORTED_LANGUAGES

try:
    from llm_utils.language_detector import LLMLanguageDetector
except Exception:  # pragma: no cover - optional dependency
    LLMLanguageDetector = None  # type: ignore[assignment]


@dataclass(frozen=True)
class LocaleNegotiationResult:
    language: str
    source: str
    confidence: float = 0.0


class LocaleNegotiator:
    """Deterministic locale resolution with optional language auto-detection."""

    def __init__(self, fallback_language: str = DEFAULT_LANGUAGE, min_confidence: float = 0.7):
        self.fallback_language = self._normalize(fallback_language) or DEFAULT_LANGUAGE
        self.min_confidence = min_confidence

    @staticmethod
    def _normalize(language: Optional[str]) -> Optional[str]:
        if not language:
            return None
        normalized = language.strip().lower()
        return normalized if normalized in SUPPORTED_LANGUAGES else None

    def resolve_language(
        self,
        *,
        explicit_language: Optional[str] = None,
        session_language: Optional[str] = None,
        user_message: Optional[str] = None,
        llm_client=None,
        allow_auto_detect: bool = True,
    ) -> LocaleNegotiationResult:
        """Resolve language by precedence with optional detection from user message."""
        explicit = self._normalize(explicit_language)
        if explicit:
            return LocaleNegotiationResult(language=explicit, source="explicit", confidence=1.0)

        session = self._normalize(session_language)
        if session:
            return LocaleNegotiationResult(language=session, source="session", confidence=0.95)

        if allow_auto_detect and user_message and user_message.strip() and LLMLanguageDetector is not None:
            detector = LLMLanguageDetector(llm_client=llm_client)
            detection = detector.detect_language(user_message, min_confidence=self.min_confidence)
            detected_lang = self._normalize(getattr(detection.language, "value", str(detection.language)))
            if detected_lang and detection.confidence >= self.min_confidence:
                return LocaleNegotiationResult(
                    language=detected_lang,
                    source="detected",
                    confidence=float(detection.confidence),
                )

        return LocaleNegotiationResult(language=self.fallback_language, source="fallback", confidence=0.5)
