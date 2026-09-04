"""
TOOL RETRIEVER (Phase 2, Progressive Disclosure — 2026-08-24)
==============================================================
Lokales Hybrid-Retrieval für die Auswahl der ReAct-Tool-Schemata:

    BM25 (rank_bm25)  +  Cosine-Similarität (bestehendes Embedding-Singleton)
        →  Reciprocal Rank Fusion (RRF, k=60)

Prinzipien:
- **Local-only**: keine Cloud-/API-Calls. Das Embedding-Modell ist das
  bestehende Singleton (utils/embedding_singleton.py); wenn es nicht
  verfügbar ist (Import/Laden fehlschlägt), degradiert der Retriever
  EXPLIZIT geloggt auf BM25-only.
- **Core-Tools kommen nie raus**: ``core`` wird in Pool-Reihenfolge
  VORANGESTELLT; Retrieval rankt nur die Nicht-Core-Kandidaten.
- **Deterministisch**: gleiche Query + gleicher Pool → gleiche Ausgabe
  (Tiebreak = Position im Pool).
- **Niemals leer**: Bei vollständigem Ranking-Fehlschlag wird der
  unveränderte Pool zurückgegeben (explizit geloggt, keine Stille).

Nutzung (ReActAgent._apply_tool_retrieval):
    retriever = get_tool_retriever(tool_schemas)
    ranked    = retriever.rank(query, candidates=pool, top_k=12, core=[...])
    # ``retrieve(...)`` ist ein Alias für ``rank(...)``.
"""

from __future__ import annotations

import logging
import re
import threading
from typing import Any, Dict, List, Optional, Sequence

from utils.rank_fusion import reciprocal_rank_fusion

logger = logging.getLogger(__name__)

# RRF-Constant (Cormack et al. 2009): k=60 ist der Standardwert.
_RRF_K = 60

# Tokenizer: Unicode-Wörter (DE-Beschreibungen enthalten Umlaute).
# Kein Stopword-Filter: Bei <50 Dokumenten gibt BM25 seltenen Tokens
# ohnehin das höhere Gewicht; der Filter würde das Signal reduzieren.
_TOKEN_RE = re.compile(r"[a-z0-9äöüß]+")


def _tokenize(text: str) -> List[str]:
    """Deterministischer, UTF-8-sicherer Tokenizer (kleingeschrieben)."""
    return _TOKEN_RE.findall(text.lower())


def rrf_fuse(
    rankings: Sequence[Sequence[str]],
    tiebreak_order: Optional[Sequence[str]] = None,
) -> List[str]:
    """Reciprocal Rank Fusion über mehrere Ranglisten (rein, unit-testbar).

    Args:
        rankings: Liste geordneter Name-Listen (beste zuerst). Leere
            Listen werden ignoriert.
        tiebreak_order: Optionaler deterministischer Tiebreak
            (z. B. Pool-Reihenfolge).

    Returns:
        Fusionierte, geordnete Liste aller Namen aus mindestens einer
        Rangliste.
    """
    # Kanonische RRF-Mathematik: utils/rank_fusion.py
    # (Workdoc: docs/WORKDOC_CODEBASE_AUDIT_20260828.md, Phase 2).
    # Tiebreak-Semantik unverändert: ohne tiebreak_order = erste Begegnung,
    # mit tiebreak_order = dessen Position (Unbekannte zuletzt, stabil).
    entries = reciprocal_rank_fusion(rankings, k=_RRF_K)
    if not entries:
        return []
    if tiebreak_order is None:
        return [entry.item for entry in entries]
    order_index = {name: i for i, name in enumerate(tiebreak_order)}
    ordered = sorted(
        entries,
        key=lambda e: (-e.score, order_index.get(e.item, len(entries))),
    )
    return [entry.item for entry in ordered]


class ToolRetriever:
    """Hybrides Retrieval über Tool-Schemata (BM25 + Cosine → RRF).

    Thread-sicher: Embedding-Matrix wird lazy einmalig gebaut (RLock).
    """

    def __init__(self, schemas: Sequence[Dict[str, Any]]):
        self._schemas = list(schemas)
        self._names: List[str] = [
            str(s.get("function", {}).get("name", "")) for s in self._schemas
        ]
        # BM25/Embedding-Dokument = Name + Beschreibung (Name gibt Signal).
        self._docs: Dict[str, str] = {
            str(s.get("function", {}).get("name", "")): (
                str(s.get("function", {}).get("name", "")) + ". "
                + str(s.get("function", {}).get("description", ""))
            )
            for s in self._schemas
        }
        self._lock = threading.RLock()
        self._matrix: Optional[Any] = None  # normalisierte (n, d) Array über _matrix_names
        self._matrix_names: List[str] = []
        self._matrix_failed = False  # kein erneuter Embedding-Versuch

    # ── Ranking-Teile ────────────────────────────────────────────────────

    def _bm25_ranking(self, query: str, candidates: Sequence[str]) -> List[str]:
        """BM25-Rangliste (beste zuerst); leere Liste bei Fehlschlag."""
        if not candidates:
            return []
        try:
            from rank_bm25 import BM25Okapi
        except Exception:
            logger.warning(
                "[tool_retriever] rank_bm25 nicht verfügbar — BM25-Ranking deaktiviert",
                exc_info=True,
            )
            return []
        try:
            # WICHTIG: Corpus UND Query in Wort-Token zerlegen (Single Source of
            # Truth: _tokenize). py-rank-bm25 behandelt ein nacktes ``str`` als
            # Zeichenfolge — ohne Tokenisierung würde BM25 über
            # Buchstabenhäufigkeit scoren (Regression 2026-08-24: "calculator"
            # schlug "pdf_extract" bei Query "pdf document" über c/t/o-Häufigkeit).
            corpus = [_tokenize(self._docs[n]) for n in candidates]
            tokens = _tokenize(query)
            if not tokens or any(not doc for doc in corpus):
                return []
            bm25 = BM25Okapi(corpus)
            scores = bm25.get_scores(tokens)
            order = sorted(range(len(candidates)), key=lambda i: (-float(scores[i]), i))
            return [candidates[i] for i in order]
        except Exception:
            logger.warning(
                "[tool_retriever] BM25-Ranking fehlgeschlagen — aus Ranking entfernt",
                exc_info=True,
            )
            return []

    def _build_matrix(self) -> Optional[Any]:
        """Baut (einmalig) die normalisierte Embedding-Matrix über _matrix_names."""
        try:
            import numpy as np
            from utils.embedding_singleton import get_embedding_model

            model = get_embedding_model()
            if not model.is_loaded():
                if not model.load_model():
                    logger.warning(
                        "[tool_retriever] Embedding-Modell nicht ladbar — "
                        "Cosine-Ranking deaktiviert (BM25-only)"
                    )
                    self._matrix_failed = True
                    return None
            names = [n for n in self._names if n]
            if not names:
                self._matrix_failed = True
                return None
            matrix = np.asarray(
                model.encode([self._docs[n] for n in names], batch_size=32),
                dtype=np.float64,
            )
            matrix = matrix.reshape(len(names), -1)
            norms = np.linalg.norm(matrix, axis=1, keepdims=True)
            norms[norms == 0] = 1.0
            self._matrix = matrix / norms
            self._matrix_names = names
            return self._matrix
        except Exception:
            logger.warning(
                "[tool_retriever] Embedding-Modell nicht verfügbar — "
                "Cosine-Ranking deaktiviert (BM25-only)",
                exc_info=True,
            )
            self._matrix_failed = True
            return None

    def _cosine_ranking(self, query: str, candidates: Sequence[str]) -> List[str]:
        """Cosine-Rangliste (beste zuerst); leere Liste, wenn nicht verfügbar."""
        if not candidates:
            return []
        with self._lock:
            if self._matrix is None and not self._matrix_failed:
                self._build_matrix()
        if self._matrix is None:
            return []
        try:
            import numpy as np
            from utils.embedding_singleton import get_embedding_model

            name_to_row = {n: i for i, n in enumerate(self._matrix_names)}
            rows = [self._matrix[name_to_row[n]] for n in candidates]
            mat = np.asarray(rows, dtype=np.float64)

            model = get_embedding_model()
            q_vec = np.asarray(model.encode([query], batch_size=4), dtype=np.float64)
            q_vec = q_vec.reshape(-1)
            norm = float(np.linalg.norm(q_vec))
            if norm == 0:
                return []
            q_vec = q_vec / norm

            scores = mat @ q_vec
            order = sorted(range(len(candidates)), key=lambda i: (-float(scores[i]), i))
            return [candidates[i] for i in order]
        except Exception:
            logger.warning(
                "[tool_retriever] Cosine-Ranking fehlgeschlagen — aus Ranking entfernt",
                exc_info=True,
            )
            return []


    # ── Public API ───────────────────────────────────────────────────────

    def rank(
        self,
        query: str,
        candidates: Sequence[str],
        top_k: int = 12,
        core: Optional[Sequence[str]] = None,
    ) -> List[str]:
        """Rangt den Kandidaten-Pool für die Query; Core-Tools kommen NIE raus.

        Args:
            query: User-Query (leere Query → keine Re-Ranking-Änderung).
            candidates: Kandidaten-Namen (Profil-Pool).
            top_k: Anzahl Nicht-Core-Tools, die bei aktivem Ranking oben
                bleiben (der Rest des Pools folgt deterministisch).
            core: Core-Tool-Namen (bleiben immer, Pool-Reihenfolge).

        Returns:
            Geordnete Name-Liste: [core...] + [top-k gerankt] + [Rest].
            Bei vollständigem Ranking-Fehlschlag: [core...] + [voller Rest].
        """
        pool = list(dict.fromkeys(candidates))
        core_set = set(core or [])
        core_ordered = [n for n in pool if n in core_set]
        non_core = [n for n in pool if n not in core_set]

        if not query or not str(query).strip() or not non_core:
            return core_ordered + non_core

        bm25 = self._bm25_ranking(str(query), non_core)
        cosine = self._cosine_ranking(str(query), non_core)
        if not bm25 and not cosine:
            # EXPLIZITE (nicht stille!) Degradation: unveränderter Pool.
            logger.warning(
                "[tool_retriever] Kein Ranking verfügbar (BM25+Cosine ausgefallen) — "
                "Pool wird unverändert verwendet (%d Tools)",
                len(pool),
            )
            return core_ordered + non_core

        fused = rrf_fuse([r for r in (bm25, cosine) if r], tiebreak_order=non_core)
        selected = fused[: max(0, int(top_k))]
        rest = [n for n in non_core if n not in set(selected)]
        result = core_ordered + selected + rest
        logger.info(
            "[tool_retriever] Pool %d → %d Tools (top_k=%d, core=%d, bm25=%s, cosine=%s)",
            len(pool), len(result), top_k, len(core_ordered), bool(bm25), bool(cosine),
        )
        return result

    def retrieve(
        self,
        query: str,
        candidates: Sequence[str],
        top_k: int = 12,
        core: Optional[Sequence[str]] = None,
    ) -> List[str]:
        """Alias für :meth:`rank` (Bezeichnungs-Kompatibilität)."""
        return self.rank(query=query, candidates=candidates, top_k=top_k, core=core)


# ── Modul-Singleton ────────────────────────────────────────────────────────

# Gekoppelt an den Tool-Set (Namen in Registry-Reihenfolge), sodass:
#   - dieselbe Registry immer dieselbe Instanz erhält (Matrix-Cache),
#   - verschiedene Registries parallel koexistieren (eigene Retriever),
#   - der Wechsel zurück zu einer Registry die URSPRÜNGLICHE Instanz
#     wiederherstellt (tests/test_tool_retriever.py::TestSingleton).
_retriever: Optional[Dict[tuple, ToolRetriever]] = None
_retriever_lock = threading.Lock()
_MAX_CACHED_RETRIEVERS = 8


def get_tool_retriever(schemas: Sequence[Dict[str, Any]]) -> ToolRetriever:
    """Liefert den (Schema-Set-gebundenen) Retriever-Singleton.

    Der Cache ist pro Tool-Set (Namen in Registry-Reihenfolge) getrennt —
    verschiedene Registries erhalten separate Retriever, und der Wechsel
    zurück zu einer Registry stellt die ursprüngliche Instanz wieder her
    (statt eine neue anzulegen).
    """
    global _retriever
    key = tuple(str(s.get("function", {}).get("name", "")) for s in schemas)
    with _retriever_lock:
        if _retriever is None:
            _retriever = {}
        cached = _retriever.get(key)
        if cached is None:
            cached = ToolRetriever(schemas)
            _retriever[key] = cached
            while len(_retriever) > _MAX_CACHED_RETRIEVERS:
                _retriever.pop(next(iter(_retriever)))
        return cached
