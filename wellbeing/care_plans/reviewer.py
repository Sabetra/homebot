"""
Self-supervision reviewer.

After a draft response has been generated, the reviewer checks the draft
against the active treatment plan and against ethical/safety constraints.
It runs **conditionally** to keep latency tolerable:

- always when the latest risk assessment is ELEVATED or ACUTE
- always when the stage-of-change has changed since last session
- otherwise on a budget (e.g. every Nth turn)

When risk is ELEVATED or ACUTE *and* a RAG search function is wired in,
the reviewer additionally pulls evidence-based crisis-response elements
(safety plan, de-escalation steps, suicide-prevention guideline content)
and injects them into the prompt as **internal adherence criteria**. The
LLM is instructed to verify the draft contains the required safety
elements; the retrieved text is never quoted to the user.

Output is a structured verdict; the caller decides whether to ship the
draft, edit it, or re-generate.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable, List, Optional, Sequence

from .llm_json import call_llm_json, clamp01, safe_str_list
from .models import (
    PlanGoal,
    RiskAssessment,
    RiskLevel,
    SessionFocus,
    StageAssessment,
)

logger = logging.getLogger(__name__)


@dataclass
class ReviewVerdict:
    accept: bool                  # True if the draft is acceptable as-is
    safety_ok: bool
    plan_alignment: float         # 0..1
    issues: List[str]             # human-readable issues
    suggested_revision: str       # optional re-write guidance / replacement text
    confidence: float


_REVIEWER_PROMPT_BASE = """Du bist ein klinischer Supervisor. Prüfe den ENTWURF einer Therapeut-Antwort.

NUTZERANFRAGE:
\"\"\"{user_message}\"\"\"

ENTWURF:
\"\"\"{draft}\"\"\"

AKTUELLER FOKUS:
- Primärziel: {primary_goal}
- Geplante Interventionen: {interventions}

AKTUELLES TTM-STADIUM: {stage}
RISIKO-LEVEL: {risk_level} ({risk_drivers})
{evidence_block}
PRÜFAUFGABEN:
1. Sicherheit: Keine Diagnosen, keine Medikamenten- oder Dosierungs-Empfehlungen,
   keine bagatellisierenden oder beschämenden Aussagen, angemessene Krisenpfade
   bei erhöhtem Risiko.
2. Plan-Adhärenz: Bewegt der Entwurf den Nutzer Richtung Primärziel?
   Passt der Stil zum TTM-Stadium (z.B. motivierend bei Contemplation,
   konkret bei Action)?
3. Empathie: Spürt der Nutzer Gehört-werden?

Antworte ausschließlich mit JSON:
{{
  "accept": true | false,
  "safety_ok": true | false,
  "plan_alignment": 0.0,        // 0..1
  "issues": ["<problem 1>", "..."],
  "suggested_revision": "<entweder ein verbesserter Antworttext, oder leer>",
  "confidence": 0.0
}}

JSON:"""


_EVIDENCE_BLOCK_TEMPLATE = """
EVIDENZ-BASIERTE KRISEN-LEITLINIEN (interne Prüfkriterien — NICHT zitieren):
{snippets}
ZUSATZAUFGABE bei erhöhtem Risiko:
4. Adhärenz an Krisen-Leitlinien: Enthält der Entwurf die in den Leitlinien
   genannten Schutzfaktoren / Sicherheitselemente (z.B. Validierung,
   Notfall-Ressourcen, konkrete nächste Schritte)? Wenn nicht, liste in
   "issues" exakt, welches Element fehlt, und schreibe in
   "suggested_revision" eine angepasste Version, die diese Elemente enthält
   — ohne die Leitlinien wörtlich zu zitieren.
"""


def _fmt_goal(goal: Optional[PlanGoal]) -> str:
    if goal is None:
        return "—"
    return f"{goal.title} (Status={goal.status.value}, P{goal.priority})"


def _format_evidence_block(snippets: Sequence[str]) -> str:
    if not snippets:
        return ""
    bullets = "\n".join(f"- {s.strip()}" for s in snippets if s and s.strip())
    if not bullets:
        return ""
    return _EVIDENCE_BLOCK_TEMPLATE.format(snippets=bullets)


def _build_evidence_query(
    user_message: str, risk: RiskAssessment, stage: Optional[StageAssessment],
) -> str:
    """Build a *content-derived* RAG query.

    The query mixes the user's own words (so the FAISS embedding is
    grounded in the actual situation) with the LLM-classified risk
    drivers and the stage label. There is no keyword pattern matching
    here — drivers come from the upstream LLM RiskClassifier.
    """
    parts: List[str] = [user_message[:600]]
    if risk.drivers:
        parts.append("Risikofaktoren: " + ", ".join(risk.drivers[:5]))
    if stage is not None:
        parts.append(f"Stadium: {stage.stage.value}")
    parts.append(
        "Evidenzbasierte Krisen-Antwort: Validierung, Sicherheits-Plan, "
        "akute Schutzfaktoren, Notfall-Ressourcen."
    )
    return " | ".join(parts)


class Reviewer:
    """Conditional single-pass review of a draft response."""

    # Risk levels that trigger an evidence-based RAG lookup during review.
    _RAG_TRIGGER_LEVELS = (RiskLevel.ELEVATED, RiskLevel.ACUTE)

    def __init__(
        self,
        llm_function: Optional[Callable[..., str]],
        rag_search_fn: Optional[Callable[[str], List[str]]] = None,
    ) -> None:
        self.llm_function = llm_function
        # rag_search_fn(query: str) -> list[str] of evidence snippets.
        # Optional: the Reviewer remains fully functional without it,
        # only the crisis-adherence check is degraded gracefully.
        self.rag_search_fn = rag_search_fn

    def set_rag_function(
        self, rag_search_fn: Optional[Callable[[str], List[str]]],
    ) -> None:
        """Inject (or replace) the RAG search callable."""
        self.rag_search_fn = rag_search_fn

    def should_review(
        self, risk: Optional[RiskAssessment], stage_changed: bool,
        turn_idx: int, every_n_turns: int = 6,
    ) -> bool:
        # Only ACUTE triggers immediate review — ELEVATED is too common and
        # causes false-positive interruptions.  Periodic review (every_n_turns)
        # still catches drift without being intrusive.
        if risk and risk.level == RiskLevel.ACUTE:
            return True
        if stage_changed:
            return True
        if every_n_turns > 0 and turn_idx > 0 and (turn_idx % every_n_turns == 0):
            return True
        return False

    def _maybe_fetch_evidence(
        self,
        *,
        user_message: str,
        risk: Optional[RiskAssessment],
        stage: Optional[StageAssessment],
    ) -> List[str]:
        """Return evidence snippets if conditions are met, else []."""
        if self.rag_search_fn is None:
            return []
        if risk is None or risk.level not in self._RAG_TRIGGER_LEVELS:
            return []
        try:
            query = _build_evidence_query(user_message, risk, stage)
            snippets = self.rag_search_fn(query)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[treatment.reviewer] RAG evidence lookup failed (%s) — "
                "review proceeds without crisis-adherence check", exc,
            )
            return []
        if not isinstance(snippets, list):
            logger.warning(
                "[treatment.reviewer] rag_search_fn returned %s, expected list — ignoring",
                type(snippets).__name__,
            )
            return []
        clean: List[str] = []
        for s in snippets[:5]:
            if isinstance(s, str) and s.strip():
                clean.append(s.strip()[:600])
        return clean

    def review(
        self,
        *,
        user_message: str,
        draft: str,
        primary_goal: Optional[PlanGoal],
        focus: Optional[SessionFocus],
        stage: Optional[StageAssessment],
        risk: Optional[RiskAssessment],
    ) -> Optional[ReviewVerdict]:
        if not draft or not draft.strip():
            return None

        evidence_snippets = self._maybe_fetch_evidence(
            user_message=user_message, risk=risk, stage=stage,
        )
        evidence_block = _format_evidence_block(evidence_snippets)
        if evidence_snippets:
            logger.info(
                "[treatment.reviewer] crisis-adherence check active "
                "(risk=%s, %d evidence snippets)",
                risk.level.value if risk else "?", len(evidence_snippets),
            )

        prompt = _REVIEWER_PROMPT_BASE.format(
            user_message=user_message[:2000],
            draft=draft[:3000],
            primary_goal=_fmt_goal(primary_goal),
            interventions=", ".join((focus.planned_steps if focus else [])[:5]) or "—",
            stage=stage.stage.value if stage else "unknown",
            risk_level=risk.level.value if risk else "none",
            risk_drivers=", ".join((risk.drivers if risk else [])[:3]) or "—",
            evidence_block=evidence_block,
        )
        parsed = call_llm_json(self.llm_function, prompt, debug_label="reviewer")
        if parsed is None:
            return None

        return ReviewVerdict(
            accept=bool(parsed.get("accept", False)),
            safety_ok=bool(parsed.get("safety_ok", False)),
            plan_alignment=clamp01(parsed.get("plan_alignment"), default=0.5),
            issues=safe_str_list(parsed.get("issues"), max_items=6),
            suggested_revision=str(parsed.get("suggested_revision", ""))[:4000],
            confidence=clamp01(parsed.get("confidence"), default=0.5),
        )
