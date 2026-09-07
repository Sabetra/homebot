"""Test: _maybe_decrypt does NOT emit WARNING for plaintext values.

Regression test for:
  WARNING:wellbeing.wellbeing_db:Entschlüsselung fehlgeschlagen:
  
Root cause: _maybe_decrypt called _decrypt_data on every value regardless of
whether it was actually encrypted.  Plaintext session_summary / care_goals
contain spaces / umlauts / punctuation — they cannot be valid base64url tokens,
so _decrypt_data always failed and logged a WARNING on every read.

Fix: _maybe_decrypt now applies a structural base64url heuristic BEFORE calling
_decrypt_data.  Plaintext values short-circuit and return unchanged without
any warning.
"""

import logging
import pathlib
import pytest
from wellbeing.wellbeing_db import WellbeingDatabase


class _WarningCatcher(logging.Handler):
    """Captures WARNING-level emissions during test scope."""
    def __init__(self):
        super().__init__()
        self.warnings: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        msg = self.format(record)
        self.warnings.append(msg)


@pytest.fixture
def wellbeing_db(tmp_path: pathlib.Path):
    """Minimal WellbeingDatabase backed by a temp SQLite file."""
    db_path = str(tmp_path / "test_psych.db")
    db = WellbeingDatabase(db_path=db_path)
    return db


class TestMaybeDecryptNoWarning:
    """_maybe_decrypt must not log WARNING for plaintext inputs."""

    # -- German plaintext (typical session_summary) --
    @pytest.mark.parametrize(
        "plaintext",
        [
            "Der Patient berichtet von Schlafstörungen und Angstgefühlen.",
            "Therapieziel: Stressbewältigung und Emotionsregulation",
            "Alptraum, Familie, Chef, Angst",
            "Gute Besserung der Stimmung in den letzten 3 Wochen.",
            "1. Schlafhygiene  2. Achtsamkeit  3. Bewegung",
        ],
    )
    def test_plaintext_no_warning(self, wellbeing_db, plaintext: str):
        handler = _WarningCatcher()
        logger = logging.getLogger("wellbeing.wellbeing_db")
        logger.addHandler(handler)
        try:
            result = wellbeing_db._maybe_decrypt(plaintext)
            # Plaintext should pass through unchanged
            assert result == plaintext
            # No WARNING about decryption failure
            decrypt_warnings = [
                w for w in handler.warnings if "Entschlüsselung fehlgeschlagen" in w
            ]
            assert (
                len(decrypt_warnings) == 0
            ), f"Unexpected WARNING for plaintext {plaintext!r}: {decrypt_warnings}"
        finally:
            logger.removeHandler(handler)

    # -- Encrypted data round-trips correctly --
    def test_encrypted_roundtrip_no_warning(self, wellbeing_db):
        original = "Geheimnis: Behandlungsinformation"
        encrypted = wellbeing_db._encrypt_data(original)
        
        handler = _WarningCatcher()
        logger = logging.getLogger("wellbeing.wellbeing_db")
        logger.addHandler(handler)
        try:
            result = wellbeing_db._maybe_decrypt(encrypted)
            assert result == original
            decrypt_warnings = [
                w for w in handler.warnings if "Entschlüsselung fehlgeschlagen" in w
            ]
            assert len(decrypt_warnings) == 0, decrypt_warnings
        finally:
            logger.removeHandler(handler)

    # -- Edge cases --
    @pytest.mark.parametrize(
        "input_val",
        [None, "", 0],
    )
    def test_edge_cases_no_warning(self, wellbeing_db, input_val):
        handler = _WarningCatcher()
        logger = logging.getLogger("wellbeing.wellbeing_db")
        logger.addHandler(handler)
        try:
            result = wellbeing_db._maybe_decrypt(input_val)
            # Should return as-is
            assert result == input_val
            decrypt_warnings = [
                w for w in handler.warnings if "Entschlüsselung fehlgeschlagen" in w
            ]
            assert len(decrypt_warnings) == 0
        finally:
            logger.removeHandler(handler)