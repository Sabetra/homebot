"""Lifecycle maintenance for RAG triples and quarantine state.

This module exposes the explicit maintenance actions that complement the
online RAG quality path:

* reopen aged quarantine rows for regeneration
* reverify triples according to their validity policy
* resolve structural contradictions between competing triples

The goal is not to invent a new policy layer. Instead, the script wires the
existing quality-manager primitives into a small one-shot operator entrypoint
similar to the existing reclassifier and grounding backlog drain jobs.
"""
from __future__ import annotations

import argparse
import logging
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional

from agent.rag_store.core.quality import RAGQualityManager
from agent.rag_store.maintenance.drain_quarantine_regeneration import (
    RegenerationDrainStats,
    drain as drain_quarantine_regeneration,
)

logger = logging.getLogger(__name__)


@dataclass
class LifecycleMaintenanceResult:
    reopened_quarantine: int = 0
    temporal_reverification: Dict[str, object] = field(default_factory=dict)
    contradiction_resolution: Dict[str, object] = field(default_factory=dict)
    regeneration_drain: Dict[str, object] = field(default_factory=dict)


def _resolve_reranker() -> Optional[object]:
    from agent.reranker import get_reranker

    reranker = get_reranker()
    if hasattr(reranker, "_ensure_loaded"):
        reranker._ensure_loaded()
    if hasattr(reranker, "is_available") and not reranker.is_available:
        return None
    return reranker


def run_lifecycle_maintenance(
    db_path: str,
    reopen_quarantine: bool,
    run_regeneration: bool,
    reverify_temporal: bool,
    resolve_contradictions: bool,
    stale_after_days: int,
    quarantine_age_days: int,
    regeneration_batch_size: int,
    regeneration_max_batches: int,
    regeneration_min_grounding_score: float,
    regeneration_max_retry_attempts: int,
    regeneration_dry_run: bool,
    limit: int,
    require_reranker: bool,
    require_llm: bool,
) -> LifecycleMaintenanceResult:
    result = LifecycleMaintenanceResult()
    reranker = _resolve_reranker()
    if reranker is None and (reverify_temporal or resolve_contradictions or require_reranker):
        raise RuntimeError("lifecycle-maintenance: no reranker available")

    manager = RAGQualityManager(db_path=db_path, reranker=reranker)
    manager.ensure_quality_schema()

    if reopen_quarantine:
        conn = manager._get_connection()
        try:
            result.reopened_quarantine = manager.reopen_aged_quarantine(
                conn,
                min_age_days=quarantine_age_days,
                limit=limit,
            )
        finally:
            conn.close()

    if run_regeneration:
        drain_stats: RegenerationDrainStats = drain_quarantine_regeneration(
            db_path=db_path,
            batch_size=regeneration_batch_size,
            max_batches=regeneration_max_batches,
            min_grounding_score=regeneration_min_grounding_score,
            max_retry_attempts=regeneration_max_retry_attempts,
            dry_run=regeneration_dry_run,
            require_reranker=require_reranker,
            require_llm=require_llm,
        )
        result.regeneration_drain = asdict(drain_stats)

    if reverify_temporal:
        result.temporal_reverification = manager.run_temporal_reverification(
            stale_after_days=stale_after_days,
            sample_limit=limit,
            reranker=reranker,
        )

    if resolve_contradictions:
        result.contradiction_resolution = manager.resolve_triple_contradictions(
            limit=limit,
        )

    return result


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run explicit lifecycle maintenance for RAG triples.",
    )
    parser.add_argument("--db-path", default="rag_store.db")
    parser.add_argument("--limit", type=int, default=5000)
    parser.add_argument("--stale-after-days", type=int, default=30)
    parser.add_argument("--quarantine-age-days", type=int, default=90)
    parser.add_argument("--reopen-quarantine", action="store_true")
    parser.add_argument("--run-regeneration", action="store_true")
    parser.add_argument("--reverify-temporal", action="store_true")
    parser.add_argument("--resolve-contradictions", action="store_true")
    parser.add_argument("--regeneration-batch-size", type=int, default=100)
    parser.add_argument("--regeneration-max-batches", type=int, default=10)
    parser.add_argument("--regeneration-min-grounding-score", type=float, default=0.3)
    parser.add_argument("--regeneration-max-retry-attempts", type=int, default=3)
    parser.add_argument("--regeneration-dry-run", action="store_true")
    parser.add_argument("--require-reranker", action="store_true")
    parser.add_argument("--require-llm", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    if not (args.reopen_quarantine or args.run_regeneration or args.reverify_temporal or args.resolve_contradictions):
        args.reopen_quarantine = True
        args.run_regeneration = True
        args.reverify_temporal = True
        args.resolve_contradictions = True

    try:
        result = run_lifecycle_maintenance(
            db_path=args.db_path,
            reopen_quarantine=args.reopen_quarantine,
            run_regeneration=args.run_regeneration,
            reverify_temporal=args.reverify_temporal,
            resolve_contradictions=args.resolve_contradictions,
            stale_after_days=args.stale_after_days,
            quarantine_age_days=args.quarantine_age_days,
            regeneration_batch_size=args.regeneration_batch_size,
            regeneration_max_batches=args.regeneration_max_batches,
            regeneration_min_grounding_score=args.regeneration_min_grounding_score,
            regeneration_max_retry_attempts=args.regeneration_max_retry_attempts,
            regeneration_dry_run=args.regeneration_dry_run,
            limit=args.limit,
            require_reranker=args.require_reranker,
            require_llm=args.require_llm,
        )
    except RuntimeError as exc:
        logger.error("lifecycle-maintenance: %s", exc)
        return 2

    logger.info("lifecycle-maintenance: result=%s", asdict(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())