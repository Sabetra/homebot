#!/usr/bin/env python3
"""
QUERY INTENT — Types & Family-Role NER patterns
================================================

This module is the **types-and-NER** half of the query-intent layer:

* ``QueryIntent`` enum and ``QueryClassification`` dataclass — shared across
  the active LLM-based classifier (``llm_intent_classifier``) and any
  downstream consumer.
* ``_POSSESSIVE_PATTERNS`` / ``_FAMILY_ROLES`` — closed-set German family-role
  named-entity recognition. Pattern-based NER over a finite, closed
  ontology (kinship terms) is a legitimate use of regex: it does not make
  routing decisions, it only enriches the classification result with
  detected family entities for KG search-boost.

The actual *intent classification* (PERSONAL / MIXED / FACTUAL) is performed
by ``llm_intent_classifier.classify_query_llm`` — see that module's docstring
for the rationale why no regex/keyword fallback exists.
"""

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import List


class QueryIntent(Enum):
    PERSONAL = "personal"   # About the user's life, feelings, relationships
    MIXED = "mixed"         # Blends personal context with factual information
    FACTUAL = "factual"     # General psychology knowledge, techniques, definitions


@dataclass
class QueryClassification:
    """Result of query intent classification."""
    intent: QueryIntent
    confidence: float                                              # 0.0–1.0
    family_entities: List[str] = field(default_factory=list)       # Detected family roles
    person_references: List[str] = field(default_factory=list)     # Any person names/roles
    reasoning: str = ""                                            # Human-readable explanation


# ── Family-role NER patterns (closed set — legitimate regex use) ────────

# Possessive personal references (German). Covers all German declensions:
# mein/meine/meinen/meinem/meiner/meines etc.
_POSSESSIVE_PATTERNS = re.compile(
    r'\b(mein(?:e[nrmst]?|s)?|unser(?:e[nrmst]?|s)?)\s+'
    r'(vater|papa|mutter|mama|bruder|schwester|oma|opa|großmutter|großvater|'
    r'onkel|tante|cousine?|neffe|nichte|sohn|tochter|enkel(?:kind)?|'
    r'freund(?:in)?|partner(?:in)?|frau|mann|ex|chef(?:in)?|kolleg(?:e|in)|'
    r'therapeut(?:in)?|arzt|ärztin|nachbar(?:in)?|mitbewohner(?:in)?|'
    r'schwiegervater|schwiegermutter|schwager|schwägerin)',
    re.IGNORECASE
)

# Family roles (standalone or with article)
_FAMILY_ROLES = re.compile(
    r'\b(?:de[rnm]|die|das|vom|zum|mit\s+(?:de[rnm]|meinem?))\s*'
    r'(vater|papa|mutter|mama|bruder|schwester|oma|opa|großmutter|großvater|'
    r'onkel|tante|sohn|tochter|partner(?:in)?|frau|mann|freund(?:in)?)',
    re.IGNORECASE
)
