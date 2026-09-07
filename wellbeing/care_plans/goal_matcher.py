"""
Embedding+LLM-verifier goal matcher.

Replaces the legacy ``set(...)`` string-deduplication of goals with a
two-stage match:

1. **Semantic candidate retrieval** — cosine similarity against the
   stored goal embeddings. Below ``MIN_CANDIDATE_SIM`` we declare
   "no candidate" and the new goal is created.

2. **LLM verifier** — for the top candidate we ask the LLM whether the
   candidate and the new proposal denote the same therapeutic concern,
   a sub-goal relation, or are independent. The LLM answers in JSON,
   never via keywords on our side.

If embeddings are unavailable, we fall back to LLM-only verification on
the top-N most-recent goals (still no string matching).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable, List, Optional, Tuple

import numpy as np

from .llm_json import call_llm_json, clamp01
from .models import GoalStatus, PlanGoal
from .repository import deserialise_embedding, serialise_embedding

logger = logging.getLogger(__name__)


MIN_CANDIDATE_SIM = 0.55      # below this we don't even ask the LLM
HIGH_CANDIDATE_SIM = 0.90      # above this we trust the embedding alone for "same"


@dataclass
class MatchDecision:
    """Outcome of matching a proposed goal against existing ones."""
    relation: str                  # "same" | "subgoal_of" | "supergoal_of" | "independent"
    matched_goal_id: Optional[int] # the existing goal it matched (or parent for subgoal)
    confidence: float
    rationale: str


def _embed_text(text: str) -> Optional[np.ndarray]:
    try:
        from utils.embedding_singleton import get_embedding_model
    except ImportError:
        return None
    try:
        model = get_embedding_model()
    except Exception as exc:  # noqa: BLE001
        logger.debug("Embedding model unavailable for goal matcher: %s", exc)
        return None
    if model is None:
        return None
    try:
        vec = model.encode([text], normalize_embeddings=True, show_progress_bar=False)
        return np.asarray(vec[0], dtype=np.float32)
    except Exception as exc:  # noqa: BLE001
        logger.debug("Embedding call failed: %s", exc)
        return None


def embed_goal_text(text: str) -> Optional[bytes]:
    """Public helper used by the manager when persisting a new goal."""
    vec = _embed_text(text)
    return serialise_embedding(vec) if vec is not None else None


def _cos_sim(a: np.ndarray, b: np.ndarray) -> float:
    # vectors are already normalised by the embedding model
    return float(np.dot(a, b))


_VERIFIER_PROMPT = """Du bist ein klinischer Reviewer. Vergleiche zwei therapeutische Ziele.

EXISTIERENDES ZIEL:
"{candidate}"
Begründung: {candidate_rationale}

NEU VORGESCHLAGENES ZIEL:
"{proposal}"
Begründung: {proposal_rationale}

Antworte ausschließlich mit JSON nach diesem Schema:
{{
  "relation": "same" | "subgoal_of" | "supergoal_of" | "independent",
  "confidence": 0.0,
  "rationale": "<1 Satz Begründung>"
}}

Definitionen:
- "same": Beide bezeichnen dieselbe therapeutische Veränderung (auch bei Umformulierung).
- "subgoal_of": Das neue Ziel ist ein konkreter Teilschritt des existierenden.
- "supergoal_of": Das existierende Ziel ist ein Teilschritt des neuen.
- "independent": Keines der obigen.

JSON:"""


class GoalMatcher:
    """Matches new goal proposals against existing plan goals."""

    def __init__(self, llm_function: Optional[Callable[..., str]]) -> None:
        self.llm_function = llm_function

    # -------------------------------------------------------------------
    def match(
        self,
        proposal_title: str,
        proposal_rationale: str,
        existing_goals: List[PlanGoal],
    ) -> MatchDecision:
        """Return a structured matching decision.

        Never throws — on any failure returns ``relation='independent'`` with
        zero confidence so the manager creates a new goal.
        """
        if not existing_goals:
            return MatchDecision("independent", None, 1.0, "no existing goals")

        # Stage 1 — semantic candidate retrieval
        proposal_vec = _embed_text(proposal_title)
        ranked: List[Tuple[float, PlanGoal]] = []
        if proposal_vec is not None:
            for g in existing_goals:
                gv = deserialise_embedding(g.embedding)
                if gv is None:
                    continue
                ranked.append((_cos_sim(proposal_vec, gv), g))
            ranked.sort(key=lambda t: t[0], reverse=True)
        else:
            # No embeddings — pass top-N most recent active goals to the LLM directly.
            ranked = [(0.0, g) for g in existing_goals[:5]]

        if not ranked:
            return MatchDecision("independent", None, 1.0, "no embedded candidates")

        top_sim, top_goal = ranked[0]

        # If embeddings are very high we accept "same" without LLM.
        if proposal_vec is not None and top_sim >= HIGH_CANDIDATE_SIM:
            return MatchDecision(
                "same", top_goal.id, top_sim,
                f"cosine={top_sim:.3f} ≥ {HIGH_CANDIDATE_SIM:.2f}",
            )

        # If embeddings are very low we skip the LLM call.
        if proposal_vec is not None and top_sim < MIN_CANDIDATE_SIM:
            return MatchDecision(
                "independent", None, 1.0 - top_sim,
                f"cosine={top_sim:.3f} < {MIN_CANDIDATE_SIM:.2f}",
            )

        # Stage 2 — LLM verifier
        prompt = _VERIFIER_PROMPT.format(
            candidate=top_goal.title,
            candidate_rationale=top_goal.rationale or "—",
            proposal=proposal_title,
            proposal_rationale=proposal_rationale or "—",
        )
        parsed = call_llm_json(self.llm_function, prompt, debug_label="goal_matcher")
        if not parsed:
            # Failure to verify: fall back to "independent" — never a string match.
            return MatchDecision("independent", None, 0.0, "verifier-unavailable")

        relation = str(parsed.get("relation", "independent")).strip().lower()
        if relation not in {"same", "subgoal_of", "supergoal_of", "independent"}:
            relation = "independent"

        confidence = clamp01(parsed.get("confidence"), default=0.5)
        rationale = str(parsed.get("rationale", "")).strip()[:240]

        if relation == "independent":
            return MatchDecision("independent", None, confidence, rationale)
        return MatchDecision(relation, top_goal.id, confidence, rationale)
