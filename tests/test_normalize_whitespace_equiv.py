"""Behavior-equivalence tests for the O(n) whitespace normalization.

Codebase-Audit (2026-08-28), Phase 3: the O(n^2) collapse loop

    while '  ' in s:
        s = s.replace('  ', ' ')

was replaced with ``re.sub(r' {2,}', ' ', s)`` in four places:

- ``agent/llm_knowledge_graph.py::normalize_text``
- ``agent/llm_knowledge_graph_enhanced.py::normalize_text``
- ``wellbeing/wellbeing_db.py`` fallback ``normalize_text``
  (only defined when the KG import fails -- identical transformation)
- ``wellbeing/wellbeing_db.py::normalize_search_query``

The old loop collapses runs of >=2 ASCII spaces into a single space and leaves
tabs/newlines/other whitespace untouched (a final ``strip()`` removes edge
whitespace). ``re.sub(r' {2,}', ' ', s)`` is behaviorally identical. These tests
pin that down against an inlined verbatim copy of the legacy loop.
"""
from agent.llm_knowledge_graph import normalize_text as kg_normalize_text
from agent.llm_knowledge_graph_enhanced import normalize_text as kg_enhanced_normalize_text
from wellbeing.wellbeing_db import normalize_search_query


def _legacy_collapse(s: str) -> str:
    """Verbatim reference: the pre-optimization collapse loop."""
    while '  ' in s:
        s = s.replace('  ', ' ')
    return s


def _legacy_kg_normalize(text: str) -> str:
    """Verbatim reference of the legacy KG normalize_text."""
    if not text:
        return text
    if text.startswith('psych_') or text.startswith('session_'):
        return text
    normalized = text.replace('_', ' ')
    normalized = _legacy_collapse(normalized)
    return normalized.strip()


def _legacy_search_query(query: str) -> str:
    """Verbatim reference of the legacy normalize_search_query."""
    if not query:
        return query
    normalized = query.replace('_', ' ')
    normalized = _legacy_collapse(normalized)
    return normalized.strip()


_CASES = [
    "",
    "a",
    "a b",
    "a  b",
    "a   b",
    "a    b",
    "a\tb",
    "a\nb",
    "a \t b",
    "  leading and  trailing  ",
    "under_score  name",
    "multi\n\n\nline   with\ttabs  and  spaces",
    "a   \t  b",
    "My_Node  Name",
    "UPPER  CASE  WORDS",
    "psych_abc_123  def",      # technical ID guard (KG functions)
    "session_xyz  1",         # technical ID guard (KG functions)
]


def test_kg_normalize_text_matches_legacy():
    for s in _CASES:
        expected = _legacy_kg_normalize(s)
        assert kg_normalize_text(s) == expected, f"kg mismatch for {s!r}: {kg_normalize_text(s)!r} != {expected!r}"
        assert kg_enhanced_normalize_text(s) == expected, f"kg_enhanced mismatch for {s!r}: {kg_enhanced_normalize_text(s)!r} != {expected!r}"


def test_normalize_search_query_matches_legacy():
    for s in _CASES:
        expected = _legacy_search_query(s)
        assert normalize_search_query(s) == expected, f"search_query mismatch for {s!r}: {normalize_search_query(s)!r} != {expected!r}"


def test_normalize_search_query_empty_and_none_guard():
    # `if not query: return query` -> empty string returns empty string.
    assert normalize_search_query("") == ""