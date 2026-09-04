"""
LLM-based 5-P case formulator.

Builds (or revises) a structured case formulation from session interactions,
KG triples, and the persistent psychological profile. The formulation is the
*explanatory model* under which the treatment plan is then designed.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from .llm_json import call_llm_json, clamp01, safe_str_list
from .models import CaseFormulation

logger = logging.getLogger(__name__)


_FORMULATOR_PROMPT = """Du erstellst eine therapeutische Fallkonzeption nach dem 5-P-Modell.

INTERAKTIONEN (jüngste zuerst, gekürzt):
{interactions}

PROFIL-EXTRAKT:
{profile_excerpt}

WISSENSGRAPH-TRIPLES (Top {kg_top_n}):
{kg_triples}

VORHERIGE FORMULIERUNG (falls vorhanden):
{previous_formulation}

Erstelle eine knappe, hypothetische 5-P-Fallkonzeption. Keine Diagnosen.
Antworte ausschließlich mit JSON:
{{
  "presenting": ["<aktuelles Anliegen 1>", "..."],
  "predisposing": ["<Vulnerabilität / Hintergrund>", "..."],
  "precipitating": ["<auslösendes Ereignis>", "..."],
  "perpetuating": ["<aufrechterhaltender Faktor>", "..."],
  "protective": ["<Ressource / Schutzfaktor>", "..."],
  "confidence": 0.0
}}

Maximal 4 Einträge pro Liste. Knappe Stichpunkte, keine vollen Sätze. Keine Zitate.

JSON:"""


def _format_interactions(interactions: List[Dict[str, Any]], limit: int = 15) -> str:
    lines: List[str] = []
    for it in interactions[-limit:]:
        role = it.get("role", "?")
        msg = (it.get("user_message") or it.get("assistant_message")
               or it.get("content") or "").strip()
        if not msg:
            continue
        lines.append(f"[{role}] {msg[:300]}")
    return "\n".join(lines) if lines else "—"


def _format_kg(triples: List[Dict[str, Any]], top_n: int = 12) -> str:
    if not triples:
        return "—"
    lines: List[str] = []
    for t in triples[:top_n]:
        s = t.get("subject", "?"); p = t.get("predicate", "?"); o = t.get("object", "?")
        lines.append(f"  - {s} → {p} → {o}")
    return "\n".join(lines)


def _format_profile_excerpt(profile: Optional[Dict[str, Any]]) -> str:
    if not profile:
        return "—"
    bits: List[str] = []
    cs = profile.get("current_state") or {}
    if cs:
        bits.append(f"State: {cs.get('emotional_tone', '')} / stress={cs.get('stress_level', '')}")
    tf = profile.get("therapeutic_focus") or {}
    if tf and tf.get("priority_areas"):
        bits.append(f"Foci: {', '.join(tf['priority_areas'][:3])}")
    cr = profile.get("coping_and_resources") or {}
    if cr and cr.get("strengths"):
        bits.append(f"Strengths: {', '.join(cr['strengths'][:3])}")
    return " | ".join(bits) if bits else "—"


def _format_previous_formulation(prev: Optional[CaseFormulation]) -> str:
    if not prev:
        return "—"
    return (
        f"presenting={prev.presenting} | predisposing={prev.predisposing} "
        f"| precipitating={prev.precipitating} | perpetuating={prev.perpetuating} "
        f"| protective={prev.protective} (v{prev.version}, conf={prev.confidence:.2f})"
    )


def _validate(payload: dict) -> bool:
    return isinstance(payload, dict) and any(
        k in payload for k in ("presenting", "predisposing", "perpetuating")
    )


class CaseFormulator:
    """Produces or refines a 5-P case formulation."""

    def __init__(self, llm_function: Optional[Callable[..., str]]) -> None:
        self.llm_function = llm_function

    def formulate(
        self,
        *,
        user_id: str,
        interactions: List[Dict[str, Any]],
        kg_triples: Optional[List[Dict[str, Any]]] = None,
        profile: Optional[Dict[str, Any]] = None,
        previous: Optional[CaseFormulation] = None,
    ) -> Optional[CaseFormulation]:
        if not interactions or len(interactions) < 3:
            return None  # not enough data

        prompt = _FORMULATOR_PROMPT.format(
            interactions=_format_interactions(interactions),
            profile_excerpt=_format_profile_excerpt(profile),
            kg_top_n=12,
            kg_triples=_format_kg(kg_triples or []),
            previous_formulation=_format_previous_formulation(previous),
        )
        parsed = call_llm_json(
            self.llm_function, prompt,
            schema_validator=_validate, debug_label="case_formulator",
        )
        if parsed is None:
            return None

        version = (previous.version + 1) if previous else 1
        return CaseFormulation(
            id=None,
            user_id=user_id,
            presenting=safe_str_list(parsed.get("presenting"), max_items=4),
            predisposing=safe_str_list(parsed.get("predisposing"), max_items=4),
            precipitating=safe_str_list(parsed.get("precipitating"), max_items=4),
            perpetuating=safe_str_list(parsed.get("perpetuating"), max_items=4),
            protective=safe_str_list(parsed.get("protective"), max_items=4),
            confidence=clamp01(parsed.get("confidence"), default=0.5),
            version=version,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
