"""
LLM-based risk classifier.

Replaces the keyword fallback in ``async_message_handler.detect_crisis``.
Returns a structured ``RiskAssessment``; callers must inspect the level
and act accordingly. Never raises.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from .llm_json import call_llm_json, clamp01, safe_str_list
from .models import RiskAssessment, RiskLevel

logger = logging.getLogger(__name__)


_RISK_PROMPT = """Du bist ein klinischer Risiko-Reviewer. Analysiere die folgende Nutzer-Nachricht
auf Hinweise für akute oder erhöhte psychische Krise (Suizidalität, Selbstverletzung,
Fremdgefährdung, dissoziative Notfälle, akute Hoffnungslosigkeit).

NACHRICHT (Originaltext, nicht zitieren in der Antwort):
\"\"\"{message}\"\"\"

KONTEXT:
- Letzter bekannter Risiko-Level: {previous_level}
- Letzte Stimmung: {mood_hint}

Antworte ausschließlich mit JSON nach diesem Schema:
{{
  "level": "none" | "low" | "elevated" | "acute",
  "confidence": 0.0,
  "drivers": ["<kurzer, anonymer Faktor>", "..."],
  "protective_factors": ["<Schutzfaktor>", "..."],
  "rationale": "<1 Satz, ohne Zitate aus der Nachricht>"
}}

Regeln:
- Bewerte zuerst und entscheidend die AKTUELLE NACHRICHT. Der letzte Risiko-Level
    ist nur Verlaufskontext und darf allein weder "elevated" noch "acute" begründen.
- Ein Themenwechsel oder das Ausbleiben weiterer Krisenaussagen ist kein Krisensignal.
- "acute": klar geäußerte aktuelle Suizidabsicht mit Plan oder Mitteln,
  akute Selbst- oder Fremdgefährdung.
- "elevated": passive Suizidgedanken ("wünschte ich schlief ewig"),
  wiederkehrende oder anhaltende Hoffnungslosigkeit über mehrere Sessions,
  oder dokumentierte jüngere Krisen-Episode.
  Vorübergehende emotionale Belastung, normale Hilflosigkeit, Traurigkeit
  oder allgemeine Verzweiflung als Therapiethema gehören hier NICHT hin.
- "low": Belastung ohne Krisensignale (Stress, Traurigkeit,
  vorübergehende Hilflosigkeit, emotionale Schwankungen,
  allgemeine Sorgen oder Beziehungskonflikte).
- "none": kein Risiko erkennbar.
- "drivers" und "protective_factors": kurze, allgemeine Begriffe (KEINE wörtlichen Zitate).
- WICHTIG: Der Nutzer nutzt diesen Bot als persönlichen Begleiter. Auch bei
  erhöhter Belastung soll der Dialog fortgeführt werden. Klassifiziere nur dann
  "elevated" oder "acute", wenn die Nachricht EINDRUCKSVOLL und EINSCHRÄNKUNGSLOS
  auf unmittelbare Gefahr hindeutet. Bei Unsicherheit: stuft nach "low" ab.
- Normale Therapiethemen wie Ängste, Sorgen, Beziehungskonflikte, Arbeitsstress,
  Trauer oder Frustration sind "low" — selbst wenn sie intensiv ausgedrückt werden.

JSON:"""


def _validate_risk_payload(payload: dict) -> bool:
    if not isinstance(payload, dict):
        return False
    return "level" in payload


class RiskClassifier:
    """Classifies a single user message for psychological risk."""

    def __init__(self, llm_function: Optional[Callable[..., str]]) -> None:
        self.llm_function = llm_function

    def assess(
        self,
        *,
        session_id: str,
        turn_idx: int,
        user_message: str,
        previous_level: RiskLevel = RiskLevel.NONE,
        mood_hint: str = "—",
    ) -> RiskAssessment:
        """Return a ``RiskAssessment`` for current-message evidence.

        Classifier unavailability returns LOW so failure cannot fabricate an
        acute alert or repeatedly pull the user away from their current topic.
        """
        if not user_message or not user_message.strip():
            return RiskAssessment(
                id=None, session_id=session_id, turn_idx=turn_idx,
                level=RiskLevel.NONE, confidence=1.0,
                drivers=[], protective_factors=[],
                action_taken="none", raw_classifier_output="",
                created_at=datetime.now(timezone.utc).isoformat(),
            )

        prompt = _RISK_PROMPT.format(
            message=user_message[:4000],
            previous_level=previous_level.value,
            mood_hint=mood_hint or "—",
        )
        parsed = None
        if self.llm_function is not None:
            parsed = call_llm_json(
                self.llm_function,
                prompt,
                schema_validator=_validate_risk_payload,
                debug_label="risk_classifier",
            )

        if parsed is None:
            # Fail-safe: when the classifier is unavailable, assume LOW risk.
            # Rationale: session_manager_adapter only triggers crisis on `acute`,
            # so a parser failure must not produce false-positive crisis alerts.
            # `elevated` is still handled via the probing path (Step B).
            logger.warning(
                "[risk_classifier] classifier unavailable — failing safe to LOW"
            )
            return RiskAssessment(
                id=None, session_id=session_id, turn_idx=turn_idx,
                level=RiskLevel.LOW, confidence=0.0,
                drivers=["classifier-unavailable"], protective_factors=[],
                action_taken="none",
                raw_classifier_output="",
                created_at=datetime.now(timezone.utc).isoformat(),
            )

        raw_level = str(parsed.get("level", "none")).strip().lower()
        try:
            level = RiskLevel(raw_level)
        except ValueError:
            logger.debug("[risk_classifier] unknown level '%s' — coercing to LOW",
                         raw_level)
            level = RiskLevel.LOW

        confidence = clamp01(parsed.get("confidence"), default=0.5)
        if level == RiskLevel.ACUTE and confidence < 0.8:
            level = RiskLevel.ELEVATED if confidence >= 0.6 else RiskLevel.LOW
        elif level == RiskLevel.ELEVATED and confidence < 0.70:
            level = RiskLevel.LOW

        return RiskAssessment(
            id=None, session_id=session_id, turn_idx=turn_idx,
            level=level,
            confidence=confidence,
            drivers=safe_str_list(parsed.get("drivers")),
            protective_factors=safe_str_list(parsed.get("protective_factors")),
            action_taken=_action_for(level),
            raw_classifier_output=str(parsed.get("rationale", ""))[:500],
            created_at=datetime.now(timezone.utc).isoformat(),
        )


def _action_for(level: RiskLevel) -> str:
    return {
        RiskLevel.NONE: "none",
        RiskLevel.LOW: "monitor",
        RiskLevel.ELEVATED: "reviewer_required",
        RiskLevel.ACUTE: "safety_protocol",
    }[level]
