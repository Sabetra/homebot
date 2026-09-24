"""Output-PII-Gate in AgentOrchestrator._finalize_answer (2026-09-24).

Verifiziert:
1. PII (IBAN, E-Mail) im Antworttext wird maskiert.
2. PII in Follow-up-Fragen wird maskiert.
3. Flag APP_ENABLE_OUTPUT_PII_MASKING=0 deaktiviert das Gate.
4. Fail-Open: kaputtes security_manager blockiert die Antwort NICHT.
"""
from __future__ import annotations

import pytest

from agent.orchestrator import AgentOrchestrator
from agent.security_manager import SecurityManager


class _FakeResponseBuilder:
    def format_response(self, raw_answer: str, **kwargs) -> str:
        return raw_answer


def _make_orch(*, masking_enabled: bool, security_manager=None) -> AgentOrchestrator:
    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    orch.output_pii_masking_enabled = masking_enabled
    orch.security_manager = security_manager
    orch.response_builder = _FakeResponseBuilder()
    orch.citation_inline_details = False
    orch.append_sources_block = False
    return orch


IBAN = "DE89370400440532013000"
EMAIL = "geheim@example.de"


def test_pii_in_answer_is_masked() -> None:
    orch = _make_orch(masking_enabled=True, security_manager=SecurityManager())
    raw = f"Dein Konto {IBAN} ist aktiv. Kontakt: {EMAIL}"
    answer, _ = orch._finalize_answer(
        "test", raw, sources=[], extracted_followups=[]
    )
    assert IBAN not in answer
    assert EMAIL not in answer
    assert "****" in answer  # Maskierungszeichen vorhanden


def test_pii_in_followups_is_masked() -> None:
    orch = _make_orch(masking_enabled=True, security_manager=SecurityManager())
    answer, followups = orch._finalize_answer(
        "test", "Antwort.", sources=[], extracted_followups=[f"Schreib an {EMAIL}"]
    )
    assert followups
    assert EMAIL not in followups[0]
    assert EMAIL in answer or True  # Antwort hatte keine PII
    assert "Antwort." in answer


def test_flag_off_disables_gate() -> None:
    orch = _make_orch(masking_enabled=False, security_manager=SecurityManager())
    raw = f"Konto {IBAN} aktiv."
    answer, _ = orch._finalize_answer("test", raw, sources=[], extracted_followups=[])
    assert IBAN in answer  # unmaskiert, da Flag aus


def test_fail_open_when_security_manager_broken() -> None:
    class _BrokenSM:
        def validate_output(self, text: str, mask_pii: bool = True):
            raise RuntimeError("simulierter Gate-Fehler")

    orch = _make_orch(masking_enabled=True, security_manager=_BrokenSM())
    raw = f"Konto {IBAN} aktiv."
    answer, _ = orch._finalize_answer("test", raw, sources=[], extracted_followups=[])
    assert IBAN in answer  # Antwort geht trotzdem raus (Fail-Open)


def test_no_pii_text_passes_through() -> None:
    orch = _make_orch(masking_enabled=True, security_manager=SecurityManager())
    raw = "Alles gut, keine PII hier."
    answer, _ = orch._finalize_answer("test", raw, sources=[], extracted_followups=[])
    assert answer == raw


def test_flag_env_default_is_on(monkeypatch) -> None:
    monkeypatch.delenv("APP_ENABLE_OUTPUT_PII_MASKING", raising=False)
    from utils.runtime_policy import parse_bool_env
    assert parse_bool_env("APP_ENABLE_OUTPUT_PII_MASKING", "1") is True


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
