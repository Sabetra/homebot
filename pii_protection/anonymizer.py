"""
PII Anonymizer — replaces detected PII entities with placeholders or pseudonyms.

Supports multiple strategies:
- ``"replace"``   → ``<ENTITY_TYPE>``  (e.g. ``<PERSON>``)
- ``"hash"``      → deterministic hash (reversible with key)
- ``"redact"``    → full removal (replaced with ``[REDACTED]``)
- ``"mask"``      → partial masking (e.g. ``Ha** M****``)
- ``"encrypt"``   → Fernet-encrypted (reversible with key)

Thread-safe.  Stateless except for the optional encryption key.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from pii_protection.detector import PIIEntity

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class AnonymizationResult:
    """Result of anonymizing a text."""

    original_text: str
    anonymized_text: str
    entities_found: int
    entities_anonymized: int
    entity_map: Dict[str, str] = field(default_factory=dict)
    """Mapping from placeholder → original value (only for reversible modes)."""
    strategy: str = "replace"


# ---------------------------------------------------------------------------
# Anonymizer
# ---------------------------------------------------------------------------


class PIIAnonymizer:
    """Anonymize PII entities in text.

    Args:
        strategy: Anonymization strategy (see module docstring).
        encryption_key: Fernet key for ``"encrypt"`` strategy.
            If *None* and strategy is ``"encrypt"``, a random key is generated.
        hash_salt: Salt for ``"hash"`` strategy (defaults to random).
    """

    VALID_STRATEGIES = {"replace", "hash", "redact", "mask", "encrypt"}

    def __init__(
        self,
        strategy: str = "replace",
        encryption_key: Optional[bytes] = None,
        hash_salt: Optional[str] = None,
    ) -> None:
        if strategy not in self.VALID_STRATEGIES:
            raise ValueError(
                f"Invalid strategy '{strategy}'. Choose from {self.VALID_STRATEGIES}"
            )
        self.strategy = strategy
        self._hash_salt = hash_salt or secrets.token_hex(16)

        # Lazy Fernet init
        self._fernet: Optional[Any] = None
        if strategy == "encrypt":
            self._init_fernet(encryption_key)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def anonymize(
        self,
        text: str,
        entities: List[PIIEntity],
        *,
        strategy_override: Optional[str] = None,
    ) -> AnonymizationResult:
        """Anonymize *entities* in *text*.

        Args:
            text: The original text.
            entities: PII entities detected by ``PIIDetector``.
            strategy_override: Use a different strategy for this call only.

        Returns:
            ``AnonymizationResult`` with the cleaned text and metadata.
        """
        strat = strategy_override or self.strategy
        if not entities:
            return AnonymizationResult(
                original_text=text,
                anonymized_text=text,
                entities_found=0,
                entities_anonymized=0,
                strategy=strat,
            )

        # Sort entities by start offset descending so replacements don't shift indices
        sorted_entities = sorted(entities, key=lambda e: e.start, reverse=True)

        anonymized = text
        entity_map: Dict[str, str] = {}
        count = 0

        for ent in sorted_entities:
            replacement = self._get_replacement(ent, strat)
            entity_map[replacement] = ent.text
            anonymized = anonymized[: ent.start] + replacement + anonymized[ent.end :]
            count += 1

        return AnonymizationResult(
            original_text=text,
            anonymized_text=anonymized,
            entities_found=len(entities),
            entities_anonymized=count,
            entity_map=entity_map,
            strategy=strat,
        )

    def deanonymize(self, result: AnonymizationResult) -> str:
        """Reverse anonymization (only works for reversible strategies).

        Returns the original text if ``entity_map`` is available, else the
        anonymized text unchanged.
        """
        if not result.entity_map:
            return result.anonymized_text

        text = result.anonymized_text
        for placeholder, original in result.entity_map.items():
            text = text.replace(placeholder, original)
        return text

    # ------------------------------------------------------------------
    # Strategy implementations
    # ------------------------------------------------------------------

    def _get_replacement(self, entity: PIIEntity, strategy: str) -> str:
        if strategy == "replace":
            return f"<{entity.entity_type}>"

        if strategy == "redact":
            return "[REDACTED]"

        if strategy == "mask":
            return self._mask(entity.text)

        if strategy == "hash":
            return self._hash(entity.text, entity.entity_type)

        if strategy == "encrypt":
            return self._encrypt(entity.text)

        return f"<{entity.entity_type}>"

    @staticmethod
    def _mask(text: str) -> str:
        """Mask all but the first character of each word."""
        words = text.split()
        masked = []
        for word in words:
            if len(word) <= 1:
                masked.append("*")
            else:
                masked.append(word[0] + "*" * (len(word) - 1))
        return " ".join(masked)

    def _hash(self, text: str, entity_type: str) -> str:
        """Deterministic hash — same input always produces the same output."""
        digest = hashlib.sha256(
            f"{self._hash_salt}:{text}".encode()
        ).hexdigest()[:12]
        return f"<{entity_type}:{digest}>"

    def _encrypt(self, text: str) -> str:
        """Fernet-encrypt the text (reversible with the key)."""
        if self._fernet is None:
            return f"<ENCRYPTED>"
        token = self._fernet.encrypt(text.encode()).decode()
        return f"<ENC:{token}>"

    def _decrypt(self, token: str) -> str:
        """Fernet-decrypt a token."""
        if self._fernet is None:
            return token
        # Strip the <ENC:...> wrapper
        if token.startswith("<ENC:") and token.endswith(">"):
            raw = token[5:-1]
        else:
            raw = token
        return self._fernet.decrypt(raw.encode()).decode()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _init_fernet(self, key: Optional[bytes]) -> None:
        try:
            from cryptography.fernet import Fernet

            if key is None:
                key = Fernet.generate_key()
                logger.info("🔐 PIIAnonymizer: generated new Fernet key")
            self._fernet = Fernet(key)
        except ImportError:
            logger.warning(
                "⚠️ PIIAnonymizer: cryptography not installed — encrypt strategy unavailable"
            )
