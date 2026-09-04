"""
PII Protection Pipeline — end-to-end detect → anonymize → audit-log.

Integrates:
- ``PIIDetector`` for entity detection
- ``PIIAnonymizer`` for text cleaning
- OpenTelemetry tracing for audit trail (optional)

Usage::

    pipeline = PIIProtectionPipeline(languages=["de", "en"])

    # Simple protection
    clean = pipeline.protect("Mein Name ist Hans Müller")
    # → "Mein Name ist <PERSON>"

    # Batch protection
    results = pipeline.protect_batch(["text1", "text2"])

    # Protection with audit info
    result = pipeline.protect_with_audit("Sensitive text here")
    print(result.entities_found, result.anonymized_text)
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from pii_protection.detector import PIIDetector, PIIEntity
from pii_protection.anonymizer import PIIAnonymizer, AnonymizationResult

logger = logging.getLogger(__name__)

# Try OTel integration — graceful degradation
try:
    from observability.decorators import traced, metered

    _OTEL_AVAILABLE = True
except ImportError:
    _OTEL_AVAILABLE = False

    # No-op decorators
    def traced(*args: Any, **kwargs: Any) -> Any:  # type: ignore[misc]
        def decorator(fn: Any) -> Any:
            return fn
        if args and callable(args[0]):
            return args[0]
        return decorator

    def metered(*args: Any, **kwargs: Any) -> Any:  # type: ignore[misc]
        def decorator(fn: Any) -> Any:
            return fn
        if args and callable(args[0]):
            return args[0]
        return decorator


class PIIProtectionPipeline:
    """End-to-end PII protection for psychological session data.

    Args:
        languages: Detection languages.
        strategy: Anonymization strategy.
        score_threshold: Minimum detection confidence.
        entity_types: Which PII types to detect (``None`` = all).
    """

    def __init__(
        self,
        languages: Optional[List[str]] = None,
        strategy: str = "replace",
        score_threshold: float = 0.4,
        entity_types: Optional[List[str]] = None,
        encryption_key: Optional[bytes] = None,
    ) -> None:
        self.detector = PIIDetector(
            languages=languages,
            score_threshold=score_threshold,
            entities=entity_types,
        )
        self.anonymizer = PIIAnonymizer(
            strategy=strategy,
            encryption_key=encryption_key,
        )
        self._stats: Dict[str, int] = {
            "texts_processed": 0,
            "entities_detected": 0,
            "entities_anonymized": 0,
        }

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @traced("pii.protect")  # type: ignore[misc]
    @metered("pii.protect")  # type: ignore[misc]
    def protect(self, text: str) -> str:
        """Detect and anonymize PII, returning clean text.

        This is the simplest entry point — returns only the anonymized string.
        For richer metadata use ``protect_with_audit()``.
        """
        result = self.protect_with_audit(text)
        return result.anonymized_text

    def protect_with_audit(self, text: str) -> AnonymizationResult:
        """Detect + anonymize + return full audit result."""
        entities = self.detector.detect(text)
        result = self.anonymizer.anonymize(text, entities)

        # Update stats
        self._stats["texts_processed"] += 1
        self._stats["entities_detected"] += result.entities_found
        self._stats["entities_anonymized"] += result.entities_anonymized

        if result.entities_found > 0:
            logger.info(
                "🔒 PII: %d entities detected, %d anonymized (strategy=%s)",
                result.entities_found,
                result.entities_anonymized,
                result.strategy,
            )

        return result

    def protect_batch(self, texts: List[str]) -> List[str]:
        """Anonymize a batch of texts, returning clean versions."""
        return [self.protect(t) for t in texts]

    def protect_dict(
        self,
        data: Dict[str, Any],
        fields: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Protect string fields in a dict.

        Args:
            data: Input dictionary.
            fields: Specific keys to protect.  ``None`` = all string values.

        Returns:
            New dict with protected values.
        """
        result = dict(data)
        keys = fields if fields else [k for k, v in data.items() if isinstance(v, str)]
        for key in keys:
            val = result.get(key)
            if isinstance(val, str):
                result[key] = self.protect(val)
            elif isinstance(val, list):
                result[key] = [
                    self.protect(item) if isinstance(item, str) else item
                    for item in val
                ]
        return result

    def protect_session_messages(
        self, messages: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Protect PII in a list of session messages.

        Anonymizes the ``content`` field of each message dict.
        """
        protected: List[Dict[str, Any]] = []
        for msg in messages:
            clean_msg = dict(msg)
            if "content" in clean_msg and isinstance(clean_msg["content"], str):
                clean_msg["content"] = self.protect(clean_msg["content"])
            protected.append(clean_msg)
        return protected

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    def scan(self, text: str) -> List[PIIEntity]:
        """Detect PII without anonymizing — useful for preview / audit."""
        return self.detector.detect(text)

    def has_pii(self, text: str) -> bool:
        """Quick check — does the text contain any PII?"""
        return self.detector.has_pii(text)

    def get_stats(self) -> Dict[str, int]:
        """Return cumulative pipeline statistics."""
        return dict(self._stats)

    def reset_stats(self) -> None:
        """Reset pipeline statistics."""
        for k in self._stats:
            self._stats[k] = 0
