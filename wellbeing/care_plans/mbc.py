"""
Measurement-Based Care (MBC) — conversational, opt-in.

We do NOT use the clinical PHQ-9 / GAD-7 verbatim (those are diagnostic
instruments that require informed consent and clinician oversight). Instead
we use **adapted conversational items** that probe the same constructs
(mood, anhedonia, anxiety, well-being) in ordinary language.

The LLM scores the user's response to each item on a normalised 0..1 scale
where 0 = "very poor / strong symptom" and 1 = "very good / no symptom".
This is a probe, not a diagnosis.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, List, Optional

from .llm_json import call_llm_json, clamp01
from .models import MBCObservation

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MBCItem:
    instrument: str
    key: str
    question: str           # gentle, conversational; user-facing
    construct: str          # what is being measured (for the scoring prompt)


# Two short, conservative instruments. Items are intentionally short and open.
WHO5_LIKE: List[MBCItem] = [
    MBCItem("WHO5-like", "vitality",
            "Wie energiegeladen hast du dich in den letzten Tagen gefühlt?",
            "Vitalität / Energie"),
    MBCItem("WHO5-like", "calm",
            "Wie ruhig und entspannt warst du in den letzten Tagen?",
            "Ruhe / Entspannung"),
    MBCItem("WHO5-like", "interest",
            "Wie interessiert warst du an Dingen, die dir sonst Freude machen?",
            "Anhedonie (invers)"),
    MBCItem("WHO5-like", "rest",
            "Wie erholt bist du morgens aufgewacht?",
            "Schlaf-Erholung"),
    MBCItem("WHO5-like", "meaning",
            "Wie sehr hatte dein Alltag in den letzten Tagen einen Sinn für dich?",
            "Sinn / Lebensbezug"),
]


_SCORE_PROMPT = """Du bewertest eine Selbst-Beobachtung des Nutzers.

KONSTRUKT: {construct}
FRAGE: {question}
ANTWORT DES NUTZERS: \"\"\"{response}\"\"\"

Antworte ausschließlich mit JSON:
{{
  "derived_score": 0.0,   // 0.0 = sehr schlecht / starkes Symptom, 1.0 = sehr gut / kein Symptom
  "confidence": 0.0,
  "rationale": "<1 Satz>"
}}

Wenn die Antwort die Frage nicht klar beantwortet, setze confidence niedrig.

JSON:"""


def _validate(payload: dict) -> bool:
    return isinstance(payload, dict) and "derived_score" in payload


class MBCEngine:
    """Scores user responses to MBC items via the LLM."""

    def __init__(self, llm_function: Optional[Callable[..., str]]) -> None:
        self.llm_function = llm_function

    def score(
        self,
        *,
        user_id: str,
        session_id: str,
        item: MBCItem,
        response_text: str,
    ) -> Optional[MBCObservation]:
        if not response_text or not response_text.strip():
            return None

        parsed = call_llm_json(
            self.llm_function,
            _SCORE_PROMPT.format(
                construct=item.construct, question=item.question,
                response=response_text[:1500],
            ),
            schema_validator=_validate,
            debug_label="mbc_engine",
        )
        if parsed is None:
            return None

        return MBCObservation(
            id=None, user_id=user_id, session_id=session_id,
            instrument=item.instrument, item_key=item.key,
            response_text=response_text[:2000],
            derived_score=clamp01(parsed.get("derived_score"), default=0.5),
            confidence=clamp01(parsed.get("confidence"), default=0.4),
            created_at=datetime.now(timezone.utc).isoformat(),
        )
