"""Hybrid entity boost for KG retrieval.

This module supplements semantic KG retrieval with an additional entity-guided
search stage. It prefers data-driven entity grounding from the KG entity index
and uses lexical role aliases only as a bounded fallback signal.

Used by:
- WellbeingContextBuilder._gather_knowledge_graph()
- SessionContextBuilder._load_kg_triples()

SOTA direction:
- Hybrid retrieval (semantic + lexical) for better out-of-domain recall.
- Reciprocal Rank Fusion (RRF) for stable score combination without brittle
    manual weighting.
"""

import logging
import re
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

# ── Family/person role aliases (lexical fallback only) ─────────────────────
_FAMILY_ALIASES: Dict[str, List[str]] = {
    'papa': ['Papa', 'Vater'],
    'vater': ['Vater', 'Papa'],
    'mama': ['Mama', 'Mutter'],
    'mutter': ['Mutter', 'Mama'],
    'bruder': ['Bruder'],
    'schwester': ['Schwester'],
    'oma': ['Oma', 'Großmutter'],
    'großmutter': ['Großmutter', 'Oma'],
    'opa': ['Opa', 'Großvater'],
    'großvater': ['Großvater', 'Opa'],
    'onkel': ['Onkel'],
    'tante': ['Tante'],
    'sohn': ['Sohn'],
    'tochter': ['Tochter'],
    'partner': ['Partner', 'Partnerin'],
    'partnerin': ['Partnerin', 'Partner'],
    'frau': ['Frau', 'Ehefrau'],
    'mann': ['Mann', 'Ehemann'],
    'freund': ['Freund'],
    'freundin': ['Freundin'],
    'ex': ['Ex', 'Ex-Partner', 'Ex-Partnerin', 'Ex-Freund', 'Ex-Freundin'],
    'schwiegervater': ['Schwiegervater'],
    'schwiegermutter': ['Schwiegermutter'],
    'cousine': ['Cousine', 'Cousin'],
    'cousin': ['Cousin', 'Cousine'],
    'neffe': ['Neffe'],
    'nichte': ['Nichte'],
    'enkel': ['Enkel', 'Enkelkind'],
}

# ── Entity Detection Patterns (reuse from llm_intent_classifier) ───────────
_POSSESSIVE_PATTERN = re.compile(
    r'\b(?:mein(?:e[nrmst]?|s)?|unser(?:e[nrmst]?|s)?)\s+'
    r'(vater|papa|mutter|mama|bruder|schwester|oma|opa|großmutter|großvater|'
    r'onkel|tante|cousine?|neffe|nichte|sohn|tochter|enkel(?:kind)?|'
    r'freund(?:in)?|partner(?:in)?|frau|mann|ex|chef(?:in)?|kolleg(?:e|in)|'
    r'schwiegervater|schwiegermutter|schwager|schwägerin)',
    re.IGNORECASE
)

_FAMILY_ROLE_PATTERN = re.compile(
    r'\b(?:de[rnm]|die|das|vom|zum|über(?:\s+(?:de[rnm]|meinen?m?))?|mit\s+(?:de[rnm]|meinem?))\s*'
    r'(vater|papa|mutter|mama|bruder|schwester|oma|opa|großmutter|großvater|'
    r'onkel|tante|sohn|tochter|partner(?:in)?|frau|mann|freund(?:in)?)',
    re.IGNORECASE
)

# Standalone family terms in knowledge-query context
_KNOWLEDGE_FAMILY_PATTERN = re.compile(
    r'\b(?:was\s+weißt\s+du\s+über|kennst\s+du|erinnerst\s+du\s+dich\s+an?)\b.*?'
    r'\b(vater|papa|mutter|mama|bruder|schwester|oma|opa|großmutter|großvater|'
    r'onkel|tante|sohn|tochter|partner(?:in)?|frau|mann|freund(?:in)?|familie)\b',
    re.IGNORECASE | re.DOTALL
)


def detect_family_entities(query: str) -> List[str]:
    """
    Detect family/person entity mentions in a query.

    Uses NER-style regex patterns (standard practice for closed-set entities).
    Returns normalized role names (lowercase).

    Args:
        query: User query text.

    Returns:
        List of detected family roles, deduplicated, order preserved.
    """
    entities: List[str] = []

    # Possessive references: "mein Vater", "meine Mutter"
    for match in _POSSESSIVE_PATTERN.findall(query):
        # findall returns tuples of (possessive, role) for multi-group patterns
        if isinstance(match, tuple):
            role = match[-1]  # last group is the role
        else:
            role = match
        entities.append(role.lower())

    # Article references: "den Vater", "über die Mutter"
    for role in _FAMILY_ROLE_PATTERN.findall(query):
        r = role.lower()
        if r not in entities:
            entities.append(r)

    # Knowledge-query context: "was weißt du über ... Vater"
    for role in _KNOWLEDGE_FAMILY_PATTERN.findall(query):
        r = role.lower()
        if r not in entities:
            entities.append(r)

    # Deduplicate preserving order
    seen: Set[str] = set()
    unique: List[str] = []
    for e in entities:
        if e not in seen:
            seen.add(e)
            unique.append(e)
    return unique


def _primary_score(triple: Dict[str, Any]) -> float:
    """Best-available score signal in a triple dict."""
    for key in (
        "combined_score",
        "relevance_score",
        "rerank_score",
        "similarity",
        "confidence",
    ):
        value = triple.get(key)
        if isinstance(value, (int, float)):
            return float(value)
    return 0.0


def _collect_entity_hints(db: Any, query: str) -> List[Tuple[str, float, str]]:
    """Collect entity hints from semantic and lexical sources.

    Returns tuples: (entity_text, score, source)
    """
    hints: List[Tuple[str, float, str]] = []
    seen: Set[str] = set()

    semantic_matcher = getattr(db, "_semantic_entity_match", None)
    if callable(semantic_matcher):
        try:
            raw_matches = semantic_matcher(query, top_k=10)
            if not isinstance(raw_matches, list):
                raw_matches = []
            for entity_text, sim in raw_matches:
                if not entity_text or sim < 0.45:
                    continue
                norm = str(entity_text).strip().lower()
                if norm and norm not in seen:
                    seen.add(norm)
                    hints.append((str(entity_text).strip(), float(sim), "semantic_entity_match"))
        except RuntimeError:
            raise
        except Exception as exc:
            logger.debug(f"[ENTITY-BOOST] semantic entity hints unavailable: {exc}")

    # Lexical fallback only when semantic grounding had no result.
    if not hints:
        for role in detect_family_entities(query):
            for alias in _FAMILY_ALIASES.get(role, [role.capitalize()]):
                norm = alias.strip().lower()
                if norm and norm not in seen:
                    seen.add(norm)
                    hints.append((alias, 0.55, "lexical_family_fallback"))

    return hints


def _rrf_merge(
    existing_triples: List[Dict[str, Any]],
    boosted_triples: List[Dict[str, Any]],
    rank_constant: int = 20,
) -> List[Dict[str, Any]]:
    """Merge result lists with Reciprocal Rank Fusion (RRF)."""
    key_to_item: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
    rrf_scores: Dict[Tuple[str, str, str], float] = {}

    # Start from existing items.
    for triple in existing_triples:
        key = _triple_key(triple)
        key_to_item[key] = triple.copy()

    # Merge/append boosted items.
    for triple in boosted_triples:
        key = _triple_key(triple)
        if key in key_to_item:
            current = key_to_item[key]
            # Preserve best available score signals and source metadata.
            current["confidence"] = max(float(current.get("confidence", 0.0)), float(triple.get("confidence", 0.0)))
            current["combined_score"] = max(float(current.get("combined_score", 0.0)), float(triple.get("combined_score", 0.0)))
            current["relevance_score"] = max(float(current.get("relevance_score", 0.0)), float(triple.get("relevance_score", 0.0)))
            if triple.get("_boost_source"):
                current["_boost_source"] = triple.get("_boost_source")
        else:
            key_to_item[key] = triple.copy()

    ranked_existing = sorted(existing_triples, key=_primary_score, reverse=True)
    ranked_boosted = sorted(boosted_triples, key=_primary_score, reverse=True)

    # Kanonische RRF-Formel: utils/rank_fusion.py (Single Source of Truth, Phase 2).
    # Dieser Konsument behaelt nur seine eigene Merge-/Enrich-/Sortier-Semantik;
    # nur die Score-Berechnung wird ueber die kanonische Implementierung gelegt.
    from utils.rank_fusion import reciprocal_rank_fusion as _canonical_rrf
    rrf_scores = {
        entry.key: entry.score
        for entry in _canonical_rrf(
            [ranked_existing, ranked_boosted],
            k=rank_constant,
            key_fn=_triple_key,
        )
    }

    merged = list(key_to_item.values())
    for item in merged:
        key = _triple_key(item)
        rrf_score = float(rrf_scores.get(key, 0.0))
        item["rrf_score"] = rrf_score
        # Keep backward compatibility: downstream code already consumes combined_score.
        item["combined_score"] = max(float(item.get("combined_score", 0.0)), rrf_score)
        item["relevance_score"] = max(float(item.get("relevance_score", 0.0)), rrf_score)
        item["similarity"] = max(float(item.get("similarity", 0.0)), float(item.get("combined_score", 0.0)))

    merged.sort(key=lambda t: (float(t.get("rrf_score", 0.0)), _primary_score(t)), reverse=True)
    return merged


def _triple_key(triple: Dict[str, Any]) -> Tuple[str, str, str]:
    """Create a deduplication key from a triple dict."""
    return (
        str(triple.get('subject', '')).strip().lower(),
        str(triple.get('predicate', '')).strip().lower(),
        str(triple.get('object', '')).strip().lower(),
    )


def family_entity_kg_boost(
    db: Any,
    query: str,
    existing_triples: List[Dict[str, Any]],
    user_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Supplement KG results with entity-guided retrieval and RRF merging."""
    entity_hints = _collect_entity_hints(db, query)
    if not entity_hints:
        return existing_triples

    if not hasattr(db, 'search_knowledge_graph'):
        logger.warning("⚠️ [FAMILY-BOOST] DB hat keine search_knowledge_graph Methode")
        return existing_triples

    # Build set of existing triple keys for dedup
    existing_keys: Set[Tuple[str, str, str]] = set()
    for t in existing_triples:
        existing_keys.add(_triple_key(t))

    boost_count = 0
    boosted_triples: List[Dict[str, Any]] = []

    for entity_text, entity_score, hint_source in entity_hints:
        try:
            results = db.search_knowledge_graph(
                query=entity_text,
                user_id=user_id,
                limit=15,
                min_confidence=0.3,
            )
            for triple in results:
                key = _triple_key(triple)
                if key in existing_keys:
                    continue

                existing_keys.add(key)
                triple_copy = triple.copy()
                base = max(_primary_score(triple_copy), float(triple_copy.get("confidence", 0.0)))
                boost_score = max(base, entity_score * 0.95)
                triple_copy["relevance_score"] = boost_score
                triple_copy["combined_score"] = max(float(triple_copy.get("combined_score", 0.0)), boost_score)
                triple_copy["similarity"] = max(float(triple_copy.get("similarity", 0.0)), boost_score)
                triple_copy["_family_boost"] = True
                triple_copy["_boost_source"] = hint_source
                triple_copy.setdefault("rerank_score", 0.0)
                triple_copy.setdefault("entity_score", float(entity_score))
                triple_copy.setdefault(
                    "source_date",
                    triple_copy.get("interaction_date") or triple_copy.get("created_at") or "N/A",
                )
                boosted_triples.append(triple_copy)
                boost_count += 1
        except RuntimeError:
            raise
        except Exception as exc:
            logger.warning(
                f"⚠️ [FAMILY-BOOST] Entity lookup failed for '{entity_text}': {exc}"
            )

    if boost_count > 0:
        logger.info(
            f"👨‍👩‍👧 [FAMILY-BOOST] {boost_count} zusätzliche Triples via entity boost "
            f"(hints={len(entity_hints)})"
        )
        return _rrf_merge(existing_triples, boosted_triples)
    else:
        logger.info(
            "👨‍👩‍👧 [FAMILY-BOOST] Keine zusätzlichen Triples gefunden "
            "(Boost-Hinweise bereits abgedeckt)"
        )
        return existing_triples
