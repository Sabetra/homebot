"""
Kanonische Reciprocal Rank Fusion (RRF) — Single Source of Truth
=================================================================

Formel nach Cormack, Clarke & Butt (2009), SIGIR:
    "Reciprocal Rank Fusion outperforms Condorcet and individual
     Rank Learning Methods"

    RRF(item) = Σ über alle Rankings i:  1 / (k + rank_i(item) + 1)

    mit 0-basierten Rängen (Top-Position = rank 0) und Standard-k = 60.
    Die historischen Konsumenten sind 0-basiert mit k=60 bzw. 1-basiert
    mit k=20 formuliert — die Wrappers übertragen ihren k-Wert 1:1
    (s. docs/WORKDOC_CODEBASE_AUDIT_20260828.md, Phase 2).

Seit 2026-08-28 ersetzt dieses Modul die fünf lokalen RRF-Implementierungen:
    - agent/reranker.py::reciprocal_rank_fusion
    - agent/tool_retriever.py::rrf_fuse
    - agent/rag_pipeline.py::RAGPipeline._rrf_merge
    - web_search/query_expansion.py::reciprocal_rank_fusion
    - wellbeing_session/context/family_entity_boost.py::_rrf_merge

Die Konsumenten behalten ihre jeweiligen Dedup-Keys und Return-Formen als
dünne Wrappers; die Mathematik und die Determinismus-Garantie (Ties werden
nach erster Begegnung geordnet) leben hier.

Eigenschaften:
    - Rein (nur stdlib), keine Import-Zyklen, thread-sicher (keine Mutation).
    - Deterministisch: gleiche Eingabe → gleiche Ausgabe (Tie-Break stabil).
    - Items, deren key_fn None liefert, werden übersprungen (Consumer-Logik).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, Hashable, List, Optional, Sequence

__all__ = ["FusedEntry", "reciprocal_rank_fusion", "fuse_dicts"]


@dataclass(frozen=True)
class FusedEntry:
    """Ein gefusionertes Ergebnis: Dedup-Key, erstes Item, RRF-Score."""

    key: Hashable
    item: Any
    score: float
    first_seen_index: int  # globale Begegnungs-Reihenfolge (stabilen Tie-Break)


def _identity_key(item: Any) -> Hashable:
    return item


def reciprocal_rank_fusion(
    ranked_lists: Sequence[Sequence[Any]],
    *,
    k: int = 60,
    key_fn: Optional[Callable[[Any], Hashable]] = None,
) -> List[FusedEntry]:
    """Fusioniert mehrere best-first-rangierte Listen über RRF.

    Args:
        ranked_lists: Liste von Rankings (jedes Ranking: Liste von Items,
            Position 0 = beste). Leere Rankings sind erlaubt.
        k: RRF-Konstante (Standard 60, Cormack 2009). Muss >= 0 sein.
        key_fn: Mappt ein Item auf seinen Dedup-Key. Liefert None für dieses
            Item → es wird übersprungen. Default: Identität.

    Returns:
        FusedEntry-Listen sortiert nach absteigendem Score; bei gleichen
        Scores nach erster Begegnung (stabil, deterministisch). Das Item ist
        das ERSTE vorkommende Objekt für den Key (erste Liste hat Vorrang).
    """
    if k < 0:
        raise ValueError(f"k muss >= 0 sein, bekommen: {k!r}")
    if key_fn is None:
        key_fn = _identity_key

    scores: Dict[Hashable, float] = {}
    first_seen: Dict[Hashable, Any] = {}
    encounter: Dict[Hashable, int] = {}
    next_encounter = 0

    for ranking in ranked_lists:
        for rank, item in enumerate(ranking):
            key = key_fn(item)
            if key is None:
                continue
            if key not in scores:
                scores[key] = 0.0
                first_seen[key] = item
                encounter[key] = next_encounter
                next_encounter += 1
            scores[key] += 1.0 / (k + rank + 1)

    entries = [
        FusedEntry(key=key, item=first_seen[key], score=scores[key],
                   first_seen_index=encounter[key])
        for key in scores
    ]
    entries.sort(key=lambda e: (-e.score, e.first_seen_index))
    return entries


def fuse_dicts(
    ranked_lists: Sequence[Sequence[Dict[str, Any]]],
    *,
    k: int = 60,
    key_fn: Optional[Callable[[Dict[str, Any]], Hashable]] = None,
    score_field: str = "rrf_score",
) -> List[Dict[str, Any]]:
    """RRF über Dict-Listen; liefert Kopien des ersten Items + Score-Feld.

    Semantik (übernimmt die historischen agent/reranker.py-Verhalten):
        - Erster gefundener Item gewinnt (frühere Listen haben Vorrang).
        - Trägt der erste Item ein ``metadata``-Dict, werden fehlende
          Schlüssel aus den Metadata-Dicts späterer Vorkommnisse gefüllt
          (First-Wins, keine Überschreibung).
        - Der RRF-Score wird unter ``score_field`` gesetzt.
        - Sortierung wie ``reciprocal_rank_fusion`` (Score absteigend,
          dann erste Begegnung).
    """
    entries = reciprocal_rank_fusion(ranked_lists, k=k, key_fn=key_fn)
    if key_fn is None:
        key_fn = _identity_key

    # Metadaten je Key zusammenführen — streng nach historischer Semantik:
    # Nur wenn der ERSTE Item des Keys ein ``metadata``-Dict trägt, werden
    # die Metadata-Dicts späterer Vorkommnisse (First-Wins) dazu gemergt.
    first_seen_keys: set = set()
    first_meta: Dict[Hashable, Optional[Dict[str, Any]]] = {}
    later_meta: Dict[Hashable, List[Dict[str, Any]]] = {}
    for ranking in ranked_lists:
        for item in ranking:
            if not isinstance(item, dict):
                continue
            key = key_fn(item)
            if key is None:
                continue
            meta = item.get("metadata")
            is_meta = isinstance(meta, dict)
            if key not in first_seen_keys:
                first_seen_keys.add(key)
                first_meta[key] = dict(meta) if is_meta else None
            elif first_meta.get(key) is not None and is_meta:
                later_meta.setdefault(key, []).append(meta)

    results: List[Dict[str, Any]] = []
    for entry in entries:
        out = dict(entry.item) if isinstance(entry.item, dict) else {"item": entry.item}
        out[score_field] = entry.score
        base_meta = first_meta.get(entry.key)
        if base_meta is not None:
            for meta in later_meta.get(entry.key, ()):
                for mk, mv in meta.items():
                    base_meta.setdefault(mk, mv)
            out["metadata"] = base_meta
        results.append(out)
    return results

