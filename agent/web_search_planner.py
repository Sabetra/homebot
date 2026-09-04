"""Generic web-search query planning and reflection module.

Decomposes a user query into focused sub-queries and reflects on whether
search results are sufficient.  Designed for integration into the main
orchestrator pipeline (Phase 2.2 of SOTA Roadmap).

SOTA Enhancement: Grammar-Constrained Decoding (GCD)
---------------------------------------------------
When ``grammar_constrained=True`` the LLM output is constrained to a
pre-compiled BNF grammar, guaranteeing structurally valid JSON.

See: docs/02_SOTA_ROADMAP.md
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)


# ── Pydantic schemas ─────────────────────────────────────────────────────────

class SubQuery(BaseModel):
    """A single focused sub-query."""
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, description="The search query string")
    purpose: str = Field(min_length=1, description="What this sub-query should find")
    priority: float = Field(ge=0.0, le=1.0, description="Importance (0-1)")


class WebSearchPlan(BaseModel):
    """Structured plan for a web search step."""
    model_config = ConfigDict(extra="forbid")

    sub_queries: List[SubQuery] = Field(
        min_length=1, max_length=5, description="Focused sub-queries"
    )
    strategy: Literal["parallel", "sequential", "adaptive"] = "parallel"
    rationale: str = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)


class SufficiencyVerdict(BaseModel):
    """Reflection: are search results sufficient?"""
    model_config = ConfigDict(extra="forbid")

    sufficient: bool
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str = Field(min_length=1)
    missing_aspects: List[str] = Field(default_factory=list)
    suggested_refinement: Optional[str] = None


# ── Grammar helpers ──────────────────────────────────────────────────────────

_JSON_SCHEMA_WEB_PLAN = (
    '{"type":"object","properties":{"sub_queries":{"type":"array",'
    '"items":{"type":"object","properties":{"query":{"type":"string"},'
    '"purpose":{"type":"string"},"priority":{"type":"number"}},'
    '"required":["query","purpose","priority"]}},{"strategy":{"type":"string",'
    '"enum":["parallel","sequential","adaptive"]},"rationale":{"type":"string"},'
    '"confidence":{"type":"number"}},"required":["sub_queries","strategy",'
    '"rationale","confidence"]}'
)

_JSON_SCHEMA_VERDICT = (
    '{"type":"object","properties":{"sufficient":{"type":"boolean"},'
    '"confidence":{"type":"number"},"rationale":{"type":"string"},'
    '"missing_aspects":{"type":"array","items":{"type":"string"}},'
    '"suggested_refinement":{"type":["string","null"]}},'
    '"required":["sufficient","confidence","rationale"]}'
)


def _compile_grammar(json_schema: str):
    """Return a pre-compiled LlamaGrammar or None."""
    try:
        from llama_cpp import LlamaGrammar
        return LlamaGrammar.from_json_schema(json_schema)
    except ImportError:
        logger.debug("[WebSearchPlanner] llama_cpp not available — grammar disabled")
        return None
    except Exception as exc:
        logger.warning("[WebSearchPlanner] grammar compile failed: %s", exc)
        return None


# ── Planner ──────────────────────────────────────────────────────────────────

class WebSearchPlanner:
    """Decomposes a user query into focused sub-queries.

    Parameters
    ----------
    llm_wrapper : Any
        Object with a ``generate_response(prompt=...) -> str`` method.
    grammar_constrained : bool
        When ``True`` output is constrained via GCD.
    """

    def __init__(
        self,
        llm_callable: Any,
        grammar_constrained: bool = False,
        max_tokens: int = 512,
    ) -> None:
        """Initialize planner.

        Parameters
        ----------
        llm_callable : callable
            Callable with signature ``llm_callable(prompt: str, max_tokens: int) -> str``.
            The orchestrator passes a lambda wrapping ``_llm_wrapper``.
        grammar_constrained : bool
            When ``True`` output is constrained via GCD.
        max_tokens : int
            Default output budget passed to the underlying LLM callable.
        """
        self._llm_callable = llm_callable
        self._grammar_constrained = grammar_constrained
        self.max_tokens = max_tokens
        self._grammar = (
            _compile_grammar(_JSON_SCHEMA_WEB_PLAN)
            if grammar_constrained else None
        )
        self.last_error: Optional[str] = None
        self.used_fallback = False

    # ── public API ─────────────────────────────────────────────────────────
    def plan(self, user_query: str, context: str = "") -> WebSearchPlan:
        """Produce a structured search plan."""
        prompt = self._build_plan_prompt(user_query, context)
        raw = self._generate(prompt)
        return self._parse_plan(raw, user_query)

    # ── internals ──────────────────────────────────────────────────────────
    def _build_plan_prompt(self, query: str, context: str) -> str:
        ctx_block = f"\nAdditional context: {context}" if context else ""
        return (
            "You are a search-query planner. Decompose the user question into "
            "focused web-search sub-queries.\n"
            f"User query: {query}{ctx_block}\n"
            "Return valid JSON matching the WebSearchPlan schema:\n"
            "  sub_queries: [{query, purpose, priority}]\n"
            "  strategy: 'parallel' | 'sequential' | 'adaptive'\n"
            "  rationale: string\n"
            "  confidence: float 0-1\n"
            "Keep sub-queries concise (<=12 words each)."
        )

    def _generate(self, prompt: str) -> str:
        try:
            # Use the callable directly (orchestrator passes a lambda)
            return self._llm_callable(prompt, max_tokens=self.max_tokens)
        except TypeError:
            # Fallback: callable might not accept max_tokens
            try:
                return self._llm_callable(prompt)
            except Exception as exc2:
                self.last_error = str(exc2)
                logger.warning("[WebSearchPlanner] LLM call failed: %s", exc2)
                return ""
        except Exception as exc:
            self.last_error = str(exc)
            logger.warning("[WebSearchPlanner] LLM call failed: %s", exc)
            return ""

    @staticmethod
    def _parse_plan(raw: str, fallback_query: str) -> WebSearchPlan:
        obj = _extract_json(raw)
        if obj:
            try:
                return WebSearchPlan.model_validate(obj)
            except Exception as exc:
                logger.debug("[WebSearchPlanner] validation fallback: %s", exc)
        # deterministic fallback
        return WebSearchPlan(
            sub_queries=[SubQuery(query=fallback_query, purpose="answer query", priority=1.0)],
            strategy="parallel",
            rationale="fallback (single query)",
            confidence=0.5,
        )


# ── Reflector ────────────────────────────────────────────────────────────────

class WebSearchReflector:
    """Decide whether web-search results are sufficient.

    Parameters
    ----------
    llm_wrapper : Any
        Object with a ``generate_response(prompt=...) -> str`` method.
    grammar_constrained : bool
        When ``True`` output is constrained via GCD.
    """

    def __init__(
        self,
        llm_callable: Any,
        grammar_constrained: bool = False,
        max_tokens: int = 512,
    ) -> None:
        """Initialize reflector.

        Parameters
        ----------
        llm_callable : callable
            Callable with signature ``llm_callable(prompt: str, max_tokens: int) -> str``.
        grammar_constrained : bool
            When ``True`` output is constrained via GCD.
        max_tokens : int
            Default output budget passed to the underlying LLM callable.
        """
        self._llm_callable = llm_callable
        self._grammar_constrained = grammar_constrained
        self.max_tokens = max_tokens
        self._grammar = (
            _compile_grammar(_JSON_SCHEMA_VERDICT)
            if grammar_constrained else None
        )
        self.last_error: Optional[str] = None

    # ── public API ─────────────────────────────────────────────────────────
    def reflect(
        self,
        original_query: str,
        results: List[Dict[str, Any]],
    ) -> SufficiencyVerdict:
        """Assess whether *results* sufficiently answer *original_query*."""
        prompt = self._build_reflect_prompt(original_query, results)
        raw = self._generate(prompt)
        return self._parse_verdict(raw, results)

    # ── internals ──────────────────────────────────────────────────────────
    def _build_reflect_prompt(self, query: str, results: List[Dict[str, Any]]) -> str:
        # Summarise results for the prompt (keep it short)
        snippets: List[str] = []
        for i, r in enumerate(results[:5]):
            title = r.get("title", r.get("name", ""))
            snippet = r.get("snippet", r.get("content", ""))[:300]
            url = r.get("url", "")
            snippets.append(f"[{i+1}] {title}\n{snippet}\nURL: {url}")
        results_block = "\n".join(snippets) if snippets else "(no results)"

        return (
            "You are a search-quality reflector. Decide whether the following "
            "web-search results are sufficient to answer the user query.\n"
            f"Query: {query}\n\nResults:\n{results_block}\n\n"
            "Return valid JSON:\n"
            "  sufficient: boolean\n"
            "  confidence: float 0-1\n"
            "  rationale: string\n"
            "  missing_aspects: [string]\n"
            "  suggested_refinement: string | null\n"
        )

    def _generate(self, prompt: str) -> str:
        try:
            # Use the callable directly (orchestrator passes a lambda)
            return self._llm_callable(prompt, max_tokens=self.max_tokens)
        except TypeError:
            # Fallback: callable might not accept max_tokens
            try:
                return self._llm_callable(prompt)
            except Exception as exc2:
                self.last_error = str(exc2)
                logger.warning("[WebSearchReflector] LLM call failed: %s", exc2)
                return ""
        except Exception as exc:
            self.last_error = str(exc)
            logger.warning("[WebSearchReflector] LLM call failed: %s", exc)
            return ""

    @staticmethod
    def _parse_verdict(raw: str, results: List[Dict[str, Any]]) -> SufficiencyVerdict:
        obj = _extract_json(raw)
        if obj:
            try:
                return SufficiencyVerdict.model_validate(obj)
            except Exception as exc:
                logger.debug("[WebSearchReflector] validation fallback: %s", exc)
        # If no results → insufficient; otherwise assume sufficient
        sufficient = len(results) > 0
        return SufficiencyVerdict(
            sufficient=sufficient,
            confidence=0.6,
            rationale="fallback verdict",
            missing_aspects=[] if sufficient else ["initial search needed"],
            suggested_refinement=None,
        )


# ── Shared helpers ───────────────────────────────────────────────────────────

def _extract_json(text: str) -> Optional[Dict[str, Any]]:
    """Best-effort JSON extraction from LLM output."""
    if not text:
        return None
    # Try full text first
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        pass
    # Try to find JSON block
    m = re.search(r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group())
        except (json.JSONDecodeError, ValueError):
            pass
    return None