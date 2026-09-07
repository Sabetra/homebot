"""
PII Protection Pipeline — anonymization layer for stored psychological session data.

Provides:
- ``PIIDetector``  — detect PII entities using Microsoft Presidio
- ``PIIAnonymizer`` — anonymize / pseudonymize detected PII
- ``PIIProtectionPipeline`` — end-to-end detect → anonymize → log pipeline
- ``@pii_protected`` decorator — auto-anonymize function return values

Supports:
- German and English PII detection (names, emails, phones, locations, dates)
- Reversible pseudonymization (for authorized de-anonymization)
- Configurable detection thresholds
- Integration with OpenTelemetry for audit tracing

Usage::

    from pii_protection import PIIProtectionPipeline

    pipeline = PIIProtectionPipeline(languages=["de", "en"])
    clean_text = pipeline.protect("Mein Name ist Hans Müller, Tel: 0176-1234567")
    # → "Mein Name ist <PERSON>, Tel: <PHONE_NUMBER>"
"""

from pii_protection.detector import PIIDetector, PIIEntity
from pii_protection.anonymizer import PIIAnonymizer, AnonymizationResult
from pii_protection.pipeline import PIIProtectionPipeline
from pii_protection.decorators import pii_protected

__all__ = [
    "PIIDetector",
    "PIIEntity",
    "PIIAnonymizer",
    "AnonymizationResult",
    "PIIProtectionPipeline",
    "pii_protected",
]
