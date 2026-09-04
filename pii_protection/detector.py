"""
PII Detector — identifies personally identifiable information in text.

Uses Microsoft Presidio AnalyzerEngine with German + English support.
Falls back to regex-based detection if Presidio's NLP model is not available.

Thread-safe: the ``AnalyzerEngine`` is created once and reused.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PIIEntity:
    """A detected PII entity in text."""

    entity_type: str        # e.g. "PERSON", "EMAIL_ADDRESS", "PHONE_NUMBER"
    start: int              # char offset — inclusive
    end: int                # char offset — exclusive
    score: float            # confidence 0.0 – 1.0
    text: str               # the matched substring
    recognizer: str = ""    # which recognizer found it


# ---------------------------------------------------------------------------
# German regex patterns (fallback when spaCy model is missing)
# ---------------------------------------------------------------------------

_GERMAN_PHONE_RE = re.compile(
    r"""
    (?<!\d)
    (?:\+49|0049|0)       # country code or leading zero
    [\s\-./]?
    \d{2,5}               # area code
    [\s\-./]?
    \d{3,10}              # subscriber number
    (?!\d)
    """,
    re.VERBOSE,
)

_EMAIL_RE = re.compile(
    r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}",
)

_GERMAN_DATE_RE = re.compile(
    r"\b\d{1,2}\.\d{1,2}\.\d{2,4}\b"
)

_IBAN_RE = re.compile(
    r"\b[A-Z]{2}\d{2}\s?\d{4}\s?\d{4}\s?\d{4}\s?\d{4}\s?\d{0,2}\b"
)


# ---------------------------------------------------------------------------
# Detector
# ---------------------------------------------------------------------------


class PIIDetector:
    """Detect PII entities using Presidio (preferred) or regex fallback.

    Args:
        languages: List of ISO-639-1 language codes.
        score_threshold: Minimum confidence to keep an entity.
        entities: Which entity types to detect.  ``None`` = all supported.
    """

    # Default entity types we care about in psychological sessions
    DEFAULT_ENTITIES: List[str] = [
        "PERSON",
        "EMAIL_ADDRESS",
        "PHONE_NUMBER",
        "LOCATION",
        "DATE_TIME",
        "IBAN_CODE",
        "CREDIT_CARD",
        "IP_ADDRESS",
        "URL",
    ]

    def __init__(
        self,
        languages: Optional[List[str]] = None,
        score_threshold: float = 0.4,
        entities: Optional[List[str]] = None,
    ) -> None:
        self.languages = languages or ["de", "en"]
        self.score_threshold = score_threshold
        self.entities = entities or self.DEFAULT_ENTITIES

        self._analyzer: Optional[Any] = None
        self._presidio_available: bool = False
        self._init_analyzer()

    # ------------------------------------------------------------------
    # Initialization
    # ------------------------------------------------------------------

    def _init_analyzer(self) -> None:
        """Try to initialize Presidio; fall back to regex if unavailable."""
        try:
            from presidio_analyzer import AnalyzerEngine, RecognizerRegistry
            from presidio_analyzer.nlp_engine import NlpEngineProvider

            # Try to build with spaCy — may fail if model not downloaded
            try:
                nlp_config = {
                    "nlp_engine_name": "spacy",
                    "models": [
                        {"lang_code": lang, "model_name": self._get_model_name(lang)}
                        for lang in self.languages
                    ],
                }
                nlp_engine = NlpEngineProvider(nlp_configuration=nlp_config).create_engine()
                self._analyzer = AnalyzerEngine(nlp_engine=nlp_engine)
                self._presidio_available = True
                logger.info(
                    "✅ PIIDetector: Presidio + spaCy initialized (languages=%s)",
                    self.languages,
                )
            except Exception as model_exc:
                # spaCy model not available — use Presidio with default engine
                logger.warning(
                    "⚠️ PIIDetector: spaCy model not available (%s), using default engine",
                    model_exc,
                )
                self._analyzer = AnalyzerEngine()
                self._presidio_available = True
                # Override to English-only when no German model
                self.languages = ["en"]

        except ImportError:
            self._presidio_available = False
            logger.warning("⚠️ PIIDetector: Presidio not installed, using regex fallback")

    @staticmethod
    def _get_model_name(lang: str) -> str:
        """Return the spaCy model name for a language."""
        models = {
            "de": "de_core_news_sm",
            "en": "en_core_web_sm",
        }
        return models.get(lang, f"{lang}_core_news_sm")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def detect(self, text: str) -> List[PIIEntity]:
        """Detect PII entities in *text*.

        Returns a list of ``PIIEntity`` sorted by start offset, with
        scores >= ``self.score_threshold``.
        """
        if not text or not text.strip():
            return []

        if self._presidio_available and self._analyzer is not None:
            return self._detect_presidio(text)
        return self._detect_regex(text)

    def has_pii(self, text: str) -> bool:
        """Quick check — does the text contain any PII?"""
        return len(self.detect(text)) > 0

    def detect_summary(self, text: str) -> Dict[str, int]:
        """Return a {entity_type: count} summary of detected PII."""
        entities = self.detect(text)
        summary: Dict[str, int] = {}
        for e in entities:
            summary[e.entity_type] = summary.get(e.entity_type, 0) + 1
        return summary

    # ------------------------------------------------------------------
    # Presidio backend
    # ------------------------------------------------------------------

    def _detect_presidio(self, text: str) -> List[PIIEntity]:
        results: List[PIIEntity] = []
        for lang in self.languages:
            try:
                raw = self._analyzer.analyze(  # type: ignore[union-attr]
                    text=text,
                    language=lang,
                    entities=self.entities,
                    score_threshold=self.score_threshold,
                )
                for r in raw:
                    results.append(
                        PIIEntity(
                            entity_type=r.entity_type,
                            start=r.start,
                            end=r.end,
                            score=r.score,
                            text=text[r.start : r.end],
                            recognizer=r.recognition_metadata.get(
                                "recognizer_name", ""
                            )
                            if r.recognition_metadata
                            else "",
                        )
                    )
            except Exception as exc:
                logger.debug("Presidio analyze(%s) failed: %s", lang, exc)

        # De-duplicate overlapping spans (keep highest score)
        results = self._deduplicate(results)
        results.sort(key=lambda e: e.start)
        return results

    # ------------------------------------------------------------------
    # Regex fallback
    # ------------------------------------------------------------------

    def _detect_regex(self, text: str) -> List[PIIEntity]:
        results: List[PIIEntity] = []

        for m in _EMAIL_RE.finditer(text):
            results.append(
                PIIEntity("EMAIL_ADDRESS", m.start(), m.end(), 0.95, m.group(), "regex")
            )
        for m in _GERMAN_PHONE_RE.finditer(text):
            results.append(
                PIIEntity("PHONE_NUMBER", m.start(), m.end(), 0.80, m.group(), "regex")
            )
        for m in _GERMAN_DATE_RE.finditer(text):
            results.append(
                PIIEntity("DATE_TIME", m.start(), m.end(), 0.70, m.group(), "regex")
            )
        for m in _IBAN_RE.finditer(text):
            results.append(
                PIIEntity("IBAN_CODE", m.start(), m.end(), 0.95, m.group(), "regex")
            )

        results = self._deduplicate(results)
        results.sort(key=lambda e: e.start)
        return results

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _deduplicate(entities: List[PIIEntity]) -> List[PIIEntity]:
        """Remove overlapping entities, keeping the one with the highest score."""
        if not entities:
            return entities

        # Sort by start, then by score descending
        sorted_ents = sorted(entities, key=lambda e: (e.start, -e.score))
        result: List[PIIEntity] = [sorted_ents[0]]

        for ent in sorted_ents[1:]:
            prev = result[-1]
            if ent.start >= prev.end:
                # No overlap
                result.append(ent)
            elif ent.score > prev.score:
                # Overlapping but higher score — replace
                result[-1] = ent

        return result
