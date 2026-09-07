#!/usr/bin/env python3
"""
LLM-BASED PSYCHOLOGICAL QUERY INTENT CLASSIFIER
=================================================

Classifies user queries semantically into PERSONAL / MIXED / FACTUAL using
the local GGUF model. The classification is the single gate that decides
whether generic psychology RAG content is mixed into a personal care
conversation — therefore it must be content-aware, not keyword-based.

Design:
- Single focused LLM call with structured JSON output
- ~50-100 tokens max → fast inference (~0.5-1.5s)
- Understands negation, context, implicit meaning, sarcasm
- In-memory cache (same query → same result)

Failure mode (SOTA-correct):
- If the LLM is unavailable or returns malformed output, the classifier
  returns ``QueryIntent.PERSONAL`` with low confidence. PERSONAL is the
  *safe default*: it skips RAG entirely, so a misclassified factual
  question loses some breadth, but a misclassified personal one never
  leaks generic textbook content into a sensitive conversation. This
  asymmetry is intentional. There is **no** keyword/regex fallback —
  any pattern-based fallback would silently re-enable the very
  contamination the LLM classifier exists to prevent.

Categories:
    PERSONAL  → About user's life/feelings/relationships → KG + Profile only, skip RAG
    MIXED     → Personal context + seeking factual info → KG + Profile + RAG
    FACTUAL   → General psychology knowledge/techniques → RAG primary, KG background
"""

import json
import logging
import re
from typing import Any, Dict, Optional

from wellbeing_session.context.query_intent_classifier import (
    QueryClassification,
    QueryIntent,
    _POSSESSIVE_PATTERNS,
    _FAMILY_ROLES,
)

logger = logging.getLogger(__name__)

# ── Telemetry ───────────────────────────────────────────────────────────────
# Counts how often the classifier collapses to its safe-default branch.
# This is the *only* place where classification silently degrades, so a
# rising counter is a leading indicator of LLM problems (model swap,
# OOM, prompt regression). Read at session boundaries via
# ``get_classifier_telemetry()``.

_SAFE_DEFAULT_COUNTERS: Dict[str, int] = {
    "empty_query": 0,
    "no_model_loader": 0,
    "llm_failure": 0,
    "total_calls": 0,
}


def get_classifier_telemetry() -> Dict[str, int]:
    """Return a snapshot of classifier safe-default fallback counters.

    Useful for end-of-session logging or dashboards. Returned dict is
    a copy; mutating it does not affect internal state.
    """
    return dict(_SAFE_DEFAULT_COUNTERS)


def reset_classifier_telemetry() -> None:
    """Reset all classifier fallback counters to zero."""
    for k in _SAFE_DEFAULT_COUNTERS:
        _SAFE_DEFAULT_COUNTERS[k] = 0

# ── Classification Prompt Template ──────────────────────────────────────────
# Optimized for small GGUF models (Ministral/Gemma):
# - German-first instructions
# - Minimal token budget
# - Strict JSON format
# - Examples inline for few-shot

_CLASSIFY_SYSTEM_PROMPT = (
    "Du bist ein psychologischer Query-Klassifikator. "
    "Klassifiziere die Benutzeranfrage in genau EINE Kategorie.\n\n"
    "Kategorien:\n"
    "- PERSONAL: Über das persönliche Leben, Gefühle, Beziehungen, Familie, Erfahrungen der Person. "
    "Auch Erinnerungsfragen ('Was weißt du über mich?', 'Habe ich dir von X erzählt?').\n"
    "- MIXED: Die Person beschreibt etwas Persönliches UND fragt nach Fachwissen/Ratschlag/Techniken. "
    "Z.B. 'Ich fühle mich schlecht — ist das normal?' oder 'Hilft Meditation bei meiner Angst?'\n"
    "- FACTUAL: Rein sachliche Frage über Psychologie, Therapiemethoden, Definitionen, Forschung. "
    "Kein persönlicher Bezug. Z.B. 'Was ist kognitive Verhaltenstherapie?'\n\n"
    "Erkenne auch Familienmitglieder: Vater/Papa, Mutter/Mama, Bruder, Schwester, Partner, etc.\n\n"
    "Antworte NUR mit exaktem JSON:\n"
    '{"intent":"PERSONAL|MIXED|FACTUAL","family":["rolle1"],"reason":"kurz"}'
)


def _build_classify_messages(query: str) -> list:
    """Build the LLM messages for classification."""
    return [
        {"role": "system", "content": _CLASSIFY_SYSTEM_PROMPT},
        {"role": "user", "content": query},
    ]


# ── Simple LRU-style in-memory cache ────────────────────────────────────────
_CLASSIFY_CACHE: Dict[str, QueryClassification] = {}
_CACHE_MAX_SIZE = 200


def _cache_get(key: str) -> Optional[QueryClassification]:
    return _CLASSIFY_CACHE.get(key)


def _cache_put(key: str, value: QueryClassification) -> None:
    if len(_CLASSIFY_CACHE) >= _CACHE_MAX_SIZE:
        # Evict oldest 20%
        evict_count = _CACHE_MAX_SIZE // 5
        for k in list(_CLASSIFY_CACHE.keys())[:evict_count]:
            del _CLASSIFY_CACHE[k]
    _CLASSIFY_CACHE[key] = value


# ── Family entity extraction (lightweight, reuses regex patterns) ───────────
# These regex patterns are well-tested and appropriate here:
# They're not "intent classification by keyword" — they're NER for specific entities.

def _extract_family_entities(query: str) -> list:
    """
    Extract family role entities from query using NER-style patterns.
    
    NOTE: This is entity extraction (NER), not intent classification.
    Pattern-based NER for closed-set entity types (family roles) is
    appropriate and standard practice even in SOTA NLP systems.
    """
    entities = []
    
    possessive_matches = _POSSESSIVE_PATTERNS.findall(query)
    for _, role in possessive_matches:
        entities.append(role.lower())
    
    family_matches = _FAMILY_ROLES.findall(query)
    for role in family_matches:
        role_lower = role.lower()
        if role_lower not in entities:
            entities.append(role_lower)
    
    return list(dict.fromkeys(entities))  # deduplicate preserving order


# ── JSON Parsing (robust against LLM quirks) ───────────────────────────────

def _parse_classification_json(raw: str) -> Optional[Dict[str, Any]]:
    """
    Parse the LLM's JSON output, tolerant of common quirks:
    - Markdown code blocks
    - [THINK] blocks
    - Trailing text after JSON
    """
    text = raw.strip()
    
    # Strip [THINK]...[/THINK] blocks
    text = re.sub(r'\[THINK\].*?\[/THINK\]', '', text, flags=re.DOTALL).strip()
    
    # Strip markdown code fences
    text = re.sub(r'^```(?:json)?\s*', '', text, flags=re.MULTILINE)
    text = re.sub(r'```\s*$', '', text, flags=re.MULTILINE)
    text = text.strip()
    
    # Find first JSON object
    brace_start = text.find('{')
    if brace_start == -1:
        return None
    
    # Find matching closing brace
    depth = 0
    for i in range(brace_start, len(text)):
        if text[i] == '{':
            depth += 1
        elif text[i] == '}':
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[brace_start:i + 1])
                except json.JSONDecodeError:
                    return None
    
    return None


# ── Main Classification Function ────────────────────────────────────────────

def classify_query_llm(
    query: str,
    model_loader: Any = None,
) -> QueryClassification:
    """
    LLM-based query intent classification.
    
    Uses the local GGUF model for semantic understanding of the query.
    Falls back to regex-based classification if LLM is unavailable or fails.
    
    Args:
        query: The user's query text
        model_loader: ModelLoader instance with generate_response()
        
    Returns:
        QueryClassification with intent, confidence, family entities
    """
    query_stripped = query.strip()
    _SAFE_DEFAULT_COUNTERS["total_calls"] += 1
    if not query_stripped:
        _SAFE_DEFAULT_COUNTERS["empty_query"] += 1
        return QueryClassification(
            intent=QueryIntent.PERSONAL,
            confidence=0.5,
            reasoning="Empty query → safe default PERSONAL"
        )
    
    # Cache check
    cache_key = query_stripped.lower()
    cached = _cache_get(cache_key)
    if cached is not None:
        logger.debug(f"🎯 [LLM-INTENT] Cache hit: {cached.intent.value}")
        return cached
    
    # Extract family entities (NER — always, independent of classification)
    family_entities = _extract_family_entities(query_stripped)
    
    # SOTA failure mode: if no model_loader, route to safe default (PERSONAL).
    # Keyword/regex fallback was removed deliberately — see module docstring.
    if model_loader is None:
        _SAFE_DEFAULT_COUNTERS["no_model_loader"] += 1
        logger.warning(
            "⚠️ [LLM-INTENT] No model_loader available → safe default PERSONAL "
            "(no RAG to avoid contamination)"
        )
        result = QueryClassification(
            intent=QueryIntent.PERSONAL,
            confidence=0.3,
            family_entities=family_entities,
            reasoning="No LLM available → safe default PERSONAL",
        )
        _cache_put(cache_key, result)
        return result

    try:
        messages = _build_classify_messages(query_stripped)

        raw_response = model_loader.generate_response(
            max_tokens=120,        # Minimal — JSON is ~50-80 tokens
            temperature=0.1,       # Near-deterministic for classification
            messages=messages,
            top_p=0.9,
            repeat_penalty=1.0,    # No repeat penalty for JSON
            min_p=0.05,
            strip_think_blocks=False,  # We handle [THINK] stripping ourselves
        )

        if not raw_response:
            raise RuntimeError("empty LLM response")

        parsed = _parse_classification_json(raw_response)
        if not parsed or 'intent' not in parsed:
            raise RuntimeError(f"unparseable JSON: {raw_response[:200]!r}")

        intent_str = str(parsed['intent']).upper().strip()
        intent_map = {
            'PERSONAL': QueryIntent.PERSONAL,
            'MIXED': QueryIntent.MIXED,
            'FACTUAL': QueryIntent.FACTUAL,
        }
        intent = intent_map.get(intent_str)
        if intent is None:
            raise RuntimeError(f"unknown intent label: {intent_str!r}")

        # Merge family entities from LLM and NER
        llm_family = parsed.get('family', [])
        if isinstance(llm_family, list):
            for entity in llm_family:
                if isinstance(entity, str):
                    # The LLM sometimes returns "role1/synonym" as a single
                    # entity. Split on / and , to normalize.
                    sub_entities = re.split(r'[/,]', entity)
                    for sub in sub_entities:
                        e_lower = sub.strip().lower()
                        if e_lower and e_lower not in family_entities:
                            family_entities.append(e_lower)

        reasoning = parsed.get('reason', parsed.get('reasoning', 'LLM classification'))

        result = QueryClassification(
            intent=intent,
            confidence=0.85,
            family_entities=family_entities,
            reasoning=f"LLM: {reasoning}",
        )
        _cache_put(cache_key, result)
        logger.info(
            f"🎯 [LLM-INTENT] {intent.value.upper()} "
            f"(family={family_entities}) ← {reasoning}"
        )
        return result

    except Exception as exc:
        # SOTA failure mode: safe default PERSONAL. Logged at WARNING because
        # this masks a real problem (LLM down or producing garbage), but
        # never silently re-enables keyword routing.
        _SAFE_DEFAULT_COUNTERS["llm_failure"] += 1
        logger.warning(
            f"⚠️ [LLM-INTENT] LLM classification failed ({exc}) "
            f"→ safe default PERSONAL"
        )
        result = QueryClassification(
            intent=QueryIntent.PERSONAL,
            confidence=0.3,
            family_entities=family_entities,
            reasoning=f"LLM-failure-safe-default: {exc}",
        )
        _cache_put(cache_key, result)
        return result
