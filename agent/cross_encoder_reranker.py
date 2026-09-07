"""Compat-Shim: legacy ``RerankResult``-API auf ``agent.reranker`` umgebogen.

Historisch gab es zwei parallele Cross-Encoder-Implementierungen
(``agent/reranker.py`` und dieses Modul) -- mit eigenen Singletons und
identischem Modell (BGE-reranker-v2-m3). Das verdoppelte den VRAM-Footprint
und sorgte fuer divergierende Verhalten.

Dieses Modul ist jetzt ein duenner Adapter:

* ``CrossEncoderReranker`` und ``get_cross_encoder_reranker()`` delegieren
  auf den kanonischen Singleton aus ``agent.reranker.get_reranker()``.
* ``rerank()`` liefert weiterhin das alte ``RerankResult``-Dataclass, damit
  bestehende Aufrufer (``agent/evidence_processor.py``,
  ``wellbeing/wellbeing_db.py``,
  ``agent/hybrid_reasoning.py``) ohne Aenderung funktionieren.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from agent.reranker import (
    CrossEncoderReranker as _CanonicalReranker,
)
from agent.reranker import (
    get_reranker as _get_canonical_reranker,
)

logger = logging.getLogger(__name__)

try:  # pragma: no cover - importseitig
    import sentence_transformers  # noqa: F401

    CROSS_ENCODER_AVAILABLE: bool = True
except ImportError:
    CROSS_ENCODER_AVAILABLE = False


@dataclass
class RerankResult:
    """Legacy-Rueckgabeformat der alten ``CrossEncoderReranker.rerank``-API."""

    original_indices: List[int]
    reranked_indices: List[int]
    scores: List[float]
    processing_time_ms: float
    model_used: str


_CONTENT_KEYS = ("content", "text", "passage", "chunk")


def _extract_content(candidate: Dict[str, Any]) -> str:
    for key in _CONTENT_KEYS:
        value = candidate.get(key)
        if isinstance(value, str) and value:
            return value
    return str(candidate)


class CrossEncoderReranker:
    """Adapter mit der historischen ``RerankResult``-API.

    Intern wird ausschliesslich der kanonische Singleton aus
    ``agent.reranker`` verwendet -- es entsteht kein zweites Modell.
    """

    def __init__(
        self,
        model_name: Optional[str] = None,
        device: Optional[str] = None,
        max_length: int = 512,
        batch_size: int = 32,
    ) -> None:
        self._upstream: _CanonicalReranker = _get_canonical_reranker(
            model_name=model_name,
            device=device,
            max_length=max_length,
        )
        self.batch_size = batch_size

    def _lazy_init(self) -> None:
        """Legacy compatibility shim for callers that expect the old API."""
        self._upstream._ensure_loaded()

    def _ensure_loaded(self) -> None:
        """Expose the canonical lazy-init hook under the legacy wrapper."""
        self._upstream._ensure_loaded()

    @property
    def model_name(self) -> str:
        return self._upstream.model_name

    @property
    def device(self) -> str:
        return getattr(self._upstream, "_device", None) or "auto"

    @property
    def max_length(self) -> int:
        return self._upstream.max_length

    @property
    def model(self) -> Any:
        return getattr(self._upstream, "_model", None)

    @property
    def is_available(self) -> bool:
        return bool(self._upstream.is_available)

    def rerank(
        self,
        query: str,
        candidates: List[Dict[str, Any]],
        top_k: Optional[int] = None,
        score_threshold: float = 0.0,
    ) -> RerankResult:
        original_indices = list(range(len(candidates)))
        if not candidates:
            return RerankResult(
                original_indices=[],
                reranked_indices=[],
                scores=[],
                processing_time_ms=0.0,
                model_used=self.model_name,
            )

        # Normalisiere Eingabe: agent.reranker.rerank erwartet ein 'text'-Feld.
        # Ein Index-Mapping erlaubt es, die ursprungs-Indizes zu rekonstruieren.
        prepared: List[Dict[str, Any]] = []
        for idx, cand in enumerate(candidates):
            prepared.append(
                {
                    "_orig_idx": idx,
                    "text": _extract_content(cand),
                }
            )

        start = time.time()
        ranked = self._upstream.rerank(
            query=query,
            passages=prepared,
            top_k=None,  # Filterung uebernehmen wir hier (inkl. threshold).
            text_key="text",
            score_key="rerank_score",
            preserve_original_score=False,
        )
        elapsed_ms = (time.time() - start) * 1000.0

        scored: List[tuple[int, float]] = []
        for entry in ranked:
            orig_idx = int(entry.get("_orig_idx", -1))
            if orig_idx < 0:
                continue
            score = float(entry.get("rerank_score", entry.get("score", 0.0)))
            scored.append((orig_idx, score))

        if score_threshold > 0:
            filtered = [(i, s) for i, s in scored if s >= score_threshold]
            if not filtered and scored:
                # Reranker eliminiert nicht -- mindestens das beste Ergebnis
                # bleibt, sonst kollabieren nachgelagerte Grounding-Stufen.
                filtered = [scored[0]]
                logger.warning(
                    "All %d candidates below threshold %s; keeping best (%.4f)",
                    len(scored),
                    score_threshold,
                    scored[0][1],
                )
            scored = filtered

        if top_k is not None and top_k > 0:
            scored = scored[:top_k]

        reranked_indices = [i for i, _ in scored]
        scores = [s for _, s in scored]

        logger.debug(
            "Reranking: %d -> %d candidates in %.1fms",
            len(candidates),
            len(reranked_indices),
            elapsed_ms,
        )

        return RerankResult(
            original_indices=original_indices,
            reranked_indices=reranked_indices,
            scores=scores,
            processing_time_ms=elapsed_ms,
            model_used=self.model_name,
        )

    def unload(self) -> None:
        unload = getattr(self._upstream, "unload", None)
        if callable(unload):
            unload()


_reranker_instance: Optional[CrossEncoderReranker] = None


def get_cross_encoder_reranker(
    model_name: Optional[str] = None,
    device: Optional[str] = None,
    max_length: int = 512,
    batch_size: int = 32,
    **_ignored: Any,
) -> CrossEncoderReranker:
    """Liefert den (Adapter-)Singleton; der echte Modell-Singleton lebt in
    ``agent.reranker``. Doppel-Footprints sind damit ausgeschlossen."""
    global _reranker_instance
    if _reranker_instance is None:
        _reranker_instance = CrossEncoderReranker(
            model_name=model_name,
            device=device,
            max_length=max_length,
            batch_size=batch_size,
        )
    return _reranker_instance


__all__ = [
    "CROSS_ENCODER_AVAILABLE",
    "CrossEncoderReranker",
    "RerankResult",
    "get_cross_encoder_reranker",
]
