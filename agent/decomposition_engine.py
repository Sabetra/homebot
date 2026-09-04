"""
Central Decomposition Engine — SOTA Variant 4 (Hybrid Plan-Critic-Refine-Synthesize)
====================================================================================

Single source of truth for query decomposition across:

* main agent path  (intent=None      → all sub-queries treated as factual)
* psycho path      (intent=PERSONAL  → passthrough, never decomposed)
* psycho path      (intent=MIXED     → tagged sub-queries, source-separated)
* psycho path      (intent=FACTUAL   → factual sub-queries, full RAG)

Decision matrix
---------------

    complexity       | intent=None        | PERSONAL    | MIXED                | FACTUAL
    -----------------|--------------------|-------------|----------------------|---------------------
    SIMPLE/MODERATE  | passthrough        | passthrough | passthrough          | passthrough
    COMPLEX          | MQ (factual×n)     | passthrough | MQ (mixed)           | MQ (factual×n)
    VERY_COMPLEX     | MQ + critic*       | passthrough | MQ (mixed) + critic* | MQ + critic*

* Critic stage is wired in commit 3; this commit ships the routing skeleton
  so callers can already adopt the new API.

Why this lives in ``agent/`` and not in ``wellbeing_session/``
-----------------------------------------------------------------

The engine is *intent-aware* but not *intent-bound*: the main agent path
uses it without any psychological context. ``QueryIntent`` is imported
from ``wellbeing_session.context.query_intent_classifier`` because
that module is types-only (no heavy deps) and is the one place the enum
already lives — duplicating it would create exactly the kind of code
drift we want to eliminate.

Failure semantics
-----------------

Every failure mode collapses to *passthrough* (the original query, tagged
according to intent). Passthrough is the safe default: it preserves the
caller's previous behaviour, never invents sub-queries, and never silently
re-enables RAG for a PERSONAL intent.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, List, Optional

from agent.query_strategy_manager import QueryAnalyzer, QueryComplexity
from wellbeing_session.context.query_intent_classifier import QueryIntent

logger = logging.getLogger(__name__)


# ── Public types ────────────────────────────────────────────────────────────

class SubQuerySource(Enum):
    """Where a sub-query is meant to be answered.

    FACTUAL  → general RAG / web (textbook knowledge)
    PERSONAL → user knowledge graph / persistent profile only
    """
    FACTUAL = "factual"
    PERSONAL = "personal"


@dataclass
class TaggedSubQuery:
    text: str
    source: SubQuerySource


@dataclass
class DecompositionResult:
    sub_queries: List[TaggedSubQuery]
    complexity: QueryComplexity
    decomposed: bool                  # False ⇒ single passthrough
    critic_applied: bool = False      # set by critic stage (commit 3)
    reasoning: str = ""

    @property
    def factual_queries(self) -> List[str]:
        return [sq.text for sq in self.sub_queries if sq.source == SubQuerySource.FACTUAL]

    @property
    def personal_queries(self) -> List[str]:
        return [sq.text for sq in self.sub_queries if sq.source == SubQuerySource.PERSONAL]


# ── Prompts ─────────────────────────────────────────────────────────────────
#
# Prompts are German-first, minimal-token, and deliberately rigid in their
# output contract. We do *not* ask the LLM to "explain" or "justify" — those
# tokens get stripped and waste budget. The contract is verified downstream
# in the parsers; on contract violation we return [] which the engine
# converts into a safe passthrough.

_FACTUAL_DECOMP_PROMPT = (
    "Zerlege die folgende Frage in {n} fokussierte Sub-Fragen für Recherche-Zwecke.\n\n"
    'Original-Frage: "{query}"\n\n'
    "Anforderungen:\n"
    "- Genau {n} Sub-Fragen, die zusammen die Original-Frage abdecken\n"
    "- Jede Sub-Frage adressiert genau einen Aspekt\n"
    "- Keine redundanten Sub-Fragen\n"
    "- In derselben Sprache wie das Original\n\n"
    "Antworte mit GENAU {n} Zeilen, eine Sub-Frage pro Zeile, "
    "OHNE Nummerierung, OHNE Bullets, OHNE Erklärung."
)

_MIXED_DECOMP_PROMPT = (
    "Die folgende Anfrage einer therapeutischen Begleitung enthält BEIDES:\n"
    "- persönliche Aspekte (zur Person, ihrem Leben, Gefühlen, Beziehungen)\n"
    "- sachliche Aspekte (allgemeines Fachwissen, Techniken, Methoden)\n\n"
    'Original: "{query}"\n\n'
    "Erzeuge bis zu {n_personal} persönliche Sub-Fragen UND "
    "bis zu {n_factual} sachliche Sub-Fragen.\n\n"
    "Antworte AUSSCHLIESSLICH im JSON-Format:\n"
    '{{"personal": ["...", "..."], "factual": ["...", "..."]}}\n\n'
    "Halte dich exakt an dieses Format. Keine Erklärung, kein Markdown, kein Vorwort."
)

_CRITIC_PROMPT = (
    "Du bekommst eine Original-Frage und eine Liste daraus abgeleiteter Sub-Fragen.\n"
    "Aufgabe: Identifiziere redundante Sub-Fragen (semantisch gleichbedeutend oder "
    "vollständig in anderen enthalten). Behalte die präzisere/spezifischere Version.\n\n"
    'Original: "{query}"\n\n'
    "Sub-Fragen (0-indiziert):\n{numbered}\n\n"
    'Antworte AUSSCHLIESSLICH im JSON-Format: {{"keep": [Indizes der zu behaltenden Sub-Fragen]}}\n'
    "Behalte mindestens {min_keep} Sub-Fragen. Keine Erklärung, kein Markdown."
)


# ── Engine ──────────────────────────────────────────────────────────────────

class DecompositionEngine:
    """Central, intent-aware, complexity-driven decomposition engine.

    Construct once per process (it is cheap and stateless). Pass the same
    ``llm_callable`` you use elsewhere — the engine never assumes anything
    about the model identity, only that ``llm(prompt, max_tokens=int)``
    returns a string.
    """

    # Number of sub-queries by complexity. Tuned for diminishing returns:
    # empirical work (Khot 2023, Press 2023) shows recall saturates ~5;
    # going higher costs latency without lifting quality on a 7B-class LLM.
    _N_BY_COMPLEXITY = {
        QueryComplexity.COMPLEX: 4,
        QueryComplexity.VERY_COMPLEX: 6,
    }

    def __init__(
        self,
        llm_callable: Optional[Callable[..., str]] = None,
        analyzer: Optional[QueryAnalyzer] = None,
    ) -> None:
        self.llm = llm_callable
        self.analyzer = analyzer or QueryAnalyzer()

    # -- public API ---------------------------------------------------------

    def decompose(
        self,
        query: str,
        intent: Optional[QueryIntent] = None,
        force_complexity: Optional[QueryComplexity] = None,
    ) -> DecompositionResult:
        """Decompose ``query`` according to intent and complexity.

        Args:
            query: Raw user query.
            intent: Optional ``QueryIntent``. When ``None`` (default), the
                engine behaves as for the main agent path: every produced
                sub-query is tagged ``FACTUAL`` and the routing matrix
                collapses to the third column.
            force_complexity: Override the analyzer's complexity decision.
                Useful for callers that already computed complexity and
                want to avoid a redundant pass.

        Returns:
            ``DecompositionResult`` whose ``decomposed`` flag tells the
            caller whether to fan out to per-sub-query retrieval. When
            ``decomposed=False`` the result still contains exactly one
            tagged passthrough entry, so the calling code never has to
            special-case "no sub-queries".
        """
        clean = (query or "").strip()
        if not clean:
            return DecompositionResult(
                sub_queries=[],
                complexity=QueryComplexity.SIMPLE,
                decomposed=False,
                reasoning="empty query",
            )

        complexity = force_complexity or self.analyzer.analyze_complexity(clean)[0]

        # Rule 1 — PERSONAL intent never decomposes. Sub-queries against
        # textbook RAG would re-introduce exactly the contamination the
        # intent classifier exists to prevent.
        if intent == QueryIntent.PERSONAL:
            return self._passthrough(
                clean,
                complexity,
                SubQuerySource.PERSONAL,
                "intent=PERSONAL → passthrough (no RAG)",
            )

        # Rule 2 — Below COMPLEX, decomposition adds latency without
        # adding recall. Pass through.
        if complexity in (QueryComplexity.SIMPLE, QueryComplexity.MODERATE):
            source = SubQuerySource.FACTUAL  # PERSONAL was handled above
            return self._passthrough(
                clean,
                complexity,
                source,
                f"complexity={complexity.value} → passthrough",
            )

        # Rule 3 — At/above COMPLEX we want decomposition, but only if we
        # have an LLM. No keyword fallback exists by design.
        if self.llm is None:
            logger.warning(
                "DecompositionEngine: LLM unavailable → passthrough "
                "(complexity=%s, intent=%s)",
                complexity.value,
                intent.value if intent else "none",
            )
            return self._passthrough(
                clean,
                complexity,
                SubQuerySource.FACTUAL,
                "LLM unavailable → passthrough",
            )

        n = self._N_BY_COMPLEXITY[complexity]

        if intent == QueryIntent.MIXED:
            tagged = self._decompose_mixed(clean, n_total=n)
        else:
            # intent == FACTUAL or intent is None (main bot path)
            tagged = self._decompose_factual(clean, n=n)

        if not tagged:
            # The LLM returned nothing parseable. We refuse to invent
            # queries; passthrough preserves correctness at the cost of
            # one missed recall opportunity.
            return self._passthrough(
                clean,
                complexity,
                SubQuerySource.FACTUAL,
                "decomposition produced no usable queries → passthrough",
            )

        # Rule 4 — VERY_COMPLEX queries get a critic pass that prunes
        # redundant sub-queries. Single iteration only (no recursion);
        # parse failures keep all sub-queries (safe default).
        critic_applied = False
        if complexity == QueryComplexity.VERY_COMPLEX and len(tagged) >= 3:
            tagged = self._critic_pass(clean, tagged)
            critic_applied = True

        intent_label = intent.value if intent else "none"
        return DecompositionResult(
            sub_queries=tagged,
            complexity=complexity,
            decomposed=True,
            critic_applied=critic_applied,
            reasoning=(
                f"decomposed into {len(tagged)} sub-queries "
                f"(intent={intent_label}, complexity={complexity.value}"
                f"{', critic=on' if critic_applied else ''})"
            ),
        )

    # -- internals ----------------------------------------------------------

    @staticmethod
    def _passthrough(
        query: str,
        complexity: QueryComplexity,
        source: SubQuerySource,
        reasoning: str,
    ) -> DecompositionResult:
        return DecompositionResult(
            sub_queries=[TaggedSubQuery(text=query, source=source)],
            complexity=complexity,
            decomposed=False,
            reasoning=reasoning,
        )

    def _decompose_factual(self, query: str, n: int) -> List[TaggedSubQuery]:
        assert self.llm is not None  # checked by caller
        prompt = _FACTUAL_DECOMP_PROMPT.format(query=query, n=n)
        try:
            raw = self.llm(prompt, max_tokens=400)
        except Exception as exc:
            logger.warning("DecompositionEngine factual LLM call failed: %s", exc)
            return []

        lines = self._parse_lines(raw, expected=n)
        return [TaggedSubQuery(text=line, source=SubQuerySource.FACTUAL) for line in lines]

    def _decompose_mixed(self, query: str, n_total: int) -> List[TaggedSubQuery]:
        assert self.llm is not None  # checked by caller
        n_personal = max(1, n_total // 2)
        n_factual = max(1, n_total - n_personal)
        prompt = _MIXED_DECOMP_PROMPT.format(
            query=query, n_personal=n_personal, n_factual=n_factual
        )
        try:
            raw = self.llm(prompt, max_tokens=500)
        except Exception as exc:
            logger.warning("DecompositionEngine mixed LLM call failed: %s", exc)
            return []

        data = self._parse_mixed_json(raw)
        if data is None:
            return []

        tagged: List[TaggedSubQuery] = []
        for q in data.get("personal", [])[:n_personal]:
            if isinstance(q, str) and len(q.strip()) > 5:
                tagged.append(TaggedSubQuery(text=q.strip(), source=SubQuerySource.PERSONAL))
        for q in data.get("factual", [])[:n_factual]:
            if isinstance(q, str) and len(q.strip()) > 5:
                tagged.append(TaggedSubQuery(text=q.strip(), source=SubQuerySource.FACTUAL))
        return tagged

    def _critic_pass(
        self, query: str, tagged: List[TaggedSubQuery]
    ) -> List[TaggedSubQuery]:
        """Single-iteration redundancy critic for VERY_COMPLEX queries.

        Asks the LLM which sub-queries to keep. On any failure (LLM error,
        parse error, malformed indices, fewer than ``min_keep`` survivors)
        the original list is returned unchanged — silently dropping
        sub-queries on noise would be worse than running redundant RAG.
        """
        assert self.llm is not None
        if not tagged:
            return tagged

        min_keep = max(3, len(tagged) // 2)
        numbered = "\n".join(f"{i}: {sq.text}" for i, sq in enumerate(tagged))
        prompt = _CRITIC_PROMPT.format(
            query=query, numbered=numbered, min_keep=min_keep
        )

        try:
            raw = self.llm(prompt, max_tokens=200)
        except Exception as exc:
            logger.warning("DecompositionEngine critic LLM call failed: %s", exc)
            return tagged

        data = self._parse_mixed_json(raw)
        if not isinstance(data, dict) or "keep" not in data:
            logger.debug("DecompositionEngine critic: unparseable response, keeping all")
            return tagged

        try:
            indices = [int(i) for i in data["keep"] if 0 <= int(i) < len(tagged)]
        except (TypeError, ValueError):
            logger.debug("DecompositionEngine critic: malformed indices, keeping all")
            return tagged

        # Deduplicate while preserving order.
        seen: set = set()
        kept: List[TaggedSubQuery] = []
        for i in indices:
            if i in seen:
                continue
            seen.add(i)
            kept.append(tagged[i])

        if len(kept) < min_keep:
            logger.debug(
                "DecompositionEngine critic: would keep %d < min_keep=%d, reverting",
                len(kept), min_keep,
            )
            return tagged

        if len(kept) < len(tagged):
            logger.info(
                "DecompositionEngine critic pruned %d → %d sub-queries",
                len(tagged), len(kept),
            )
        return kept

    # -- parsing helpers ----------------------------------------------------

    @staticmethod
    def _parse_lines(raw: str, expected: int) -> List[str]:
        """Parse newline-separated sub-queries.

        Tolerant of: numbering ("1. "), bullets ("- ", "• "), [THINK] blocks,
        leading/trailing whitespace. Rejects lines shorter than 6 chars
        (those are almost always model artefacts like "Sub:" or "Frage:").
        """
        text = (raw or "")
        text = re.sub(r'\[THINK\].*?\[/THINK\]', '', text, flags=re.DOTALL)

        out: List[str] = []
        for line in text.splitlines():
            stripped = line.strip()
            if len(stripped) < 6:
                continue
            cleaned = re.sub(r'^\s*\d+[\.\)]\s*', '', stripped)
            cleaned = re.sub(r'^\s*[-\*•]\s*', '', cleaned)
            cleaned = cleaned.strip()
            if len(cleaned) > 5:
                out.append(cleaned)
        return out[:expected]

    @staticmethod
    def _parse_mixed_json(raw: str) -> Optional[Any]:
        """Extract the first balanced JSON object from a model response.

        Robust against: markdown code fences, [THINK] blocks, trailing prose
        after the JSON. Returns ``None`` on any failure — the caller turns
        that into a passthrough.
        """
        text = (raw or "").strip()
        text = re.sub(r'\[THINK\].*?\[/THINK\]', '', text, flags=re.DOTALL).strip()
        text = re.sub(r'^```(?:json)?\s*', '', text, flags=re.MULTILINE)
        text = re.sub(r'```\s*$', '', text, flags=re.MULTILINE).strip()

        start = text.find('{')
        if start == -1:
            return None
        depth = 0
        for i in range(start, len(text)):
            if text[i] == '{':
                depth += 1
            elif text[i] == '}':
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start:i + 1])
                    except json.JSONDecodeError:
                        return None
        return None
