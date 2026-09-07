"""
Quarantine-Regeneration-Drain — single-shot batched execution of
`RAGQualityManager.regenerate_quarantined_triples(...)`.

Why this exists
---------------
The dashboard action is intentionally manual and batch-bound, which is good for
interactive operations but inefficient for larger quarantine backlogs. This
module provides an explicit one-shot operator entrypoint with bounded work,
resume semantics, and fail-fast fault handling.

Design
------
- Idempotent and resumable: each batch only processes actionable rows
  (`pending` or retry-eligible `failed`).
- Bounded work: `--batch-size` entries per batch, `--max-batches` per run.
- Fail-fast: runtime faults (DB/LLM/reranker/scoring) abort the run.
- Honest dry-run: reports actionable backlog and recoverability split without
  DB mutations.

CLI:
    python -m agent.rag_store.maintenance.drain_quarantine_regeneration \
        --db-path rag_store.db \
        [--batch-size 100] [--max-batches 10] [--min-grounding-score 0.3] \
        [--max-retry-attempts 3] [--dry-run]

Exit codes:
    0 = clean run
    1 = runtime fault
    2 = configuration fault (`--require-reranker` / `--require-llm`)
"""
from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
import time
from dataclasses import asdict, dataclass
from typing import List, Optional

from agent.rag_store.core.quality import RAGQualityManager

logger = logging.getLogger(__name__)


@dataclass
class RegenerationDrainStats:
    backlog_before: int
    backlog_after: int
    batches_run: int
    quarantine_completed: int
    quarantine_failed: int
    recoverable_seen: int
    unrecoverable_seen: int
    triples_inserted: int
    triples_grounded: int
    triples_extracted: int
    duration_ms: float
    dry_run: bool


def _count_actionable_backlog(db_path: str, max_retry_attempts: int) -> int:
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT COUNT(*)
            FROM quarantine q
            WHERE q.source_table = 'triples'
              AND (
                    COALESCE(q.regeneration_status, 'pending') = 'pending'
                    OR (
                        COALESCE(q.regeneration_status, 'pending') = 'failed'
                        AND COALESCE(q.regeneration_attempts, 0) < ?
                    )
                  )
            """,
            (max_retry_attempts,),
        )
        row = cur.fetchone()
        return int(row[0]) if row else 0
    finally:
        conn.close()


def _resolve_reranker() -> Optional[object]:
    from agent.reranker import get_reranker

    reranker = get_reranker()
    if hasattr(reranker, "_ensure_loaded"):
        reranker._ensure_loaded()
    if hasattr(reranker, "is_available") and not reranker.is_available:
        return None
    return reranker


def _llm_ready() -> bool:
    from agent.llm_knowledge_graph import LLMKnowledgeGraphExtractor

    extractor = LLMKnowledgeGraphExtractor()
    return not (
        extractor.llm_client is None
        or not hasattr(extractor.llm_client, "llm")
        or extractor.llm_client.llm is None
    )


def drain(
    db_path: str,
    batch_size: int,
    max_batches: int,
    min_grounding_score: float,
    max_retry_attempts: int,
    dry_run: bool,
    require_reranker: bool,
    require_llm: bool,
) -> RegenerationDrainStats:
    start = time.time()
    backlog_before = _count_actionable_backlog(db_path, max_retry_attempts)
    logger.info("regen-drain: backlog_before=%d", backlog_before)

    if backlog_before == 0:
        return RegenerationDrainStats(
            backlog_before=0,
            backlog_after=0,
            batches_run=0,
            quarantine_completed=0,
            quarantine_failed=0,
            recoverable_seen=0,
            unrecoverable_seen=0,
            triples_inserted=0,
            triples_grounded=0,
            triples_extracted=0,
            duration_ms=(time.time() - start) * 1000.0,
            dry_run=dry_run,
        )

    reranker = _resolve_reranker()
    if reranker is None:
        msg = "regen-drain: no reranker available"
        if require_reranker:
            raise RuntimeError(msg + " (--require-reranker)")
        logger.warning("%s; nothing to do", msg)
        return RegenerationDrainStats(
            backlog_before=backlog_before,
            backlog_after=backlog_before,
            batches_run=0,
            quarantine_completed=0,
            quarantine_failed=0,
            recoverable_seen=0,
            unrecoverable_seen=0,
            triples_inserted=0,
            triples_grounded=0,
            triples_extracted=0,
            duration_ms=(time.time() - start) * 1000.0,
            dry_run=dry_run,
        )

    if not dry_run and not _llm_ready():
        msg = "regen-drain: LLM not loaded"
        if require_llm:
            raise RuntimeError(msg + " (--require-llm)")
        logger.warning("%s; nothing to do", msg)
        return RegenerationDrainStats(
            backlog_before=backlog_before,
            backlog_after=backlog_before,
            batches_run=0,
            quarantine_completed=0,
            quarantine_failed=0,
            recoverable_seen=0,
            unrecoverable_seen=0,
            triples_inserted=0,
            triples_grounded=0,
            triples_extracted=0,
            duration_ms=(time.time() - start) * 1000.0,
            dry_run=dry_run,
        )

    manager = RAGQualityManager(db_path=db_path, reranker=reranker)
    manager.ensure_quality_schema()

    if dry_run:
        probe = manager.regenerate_quarantined_triples(
            dry_run=True,
            batch_size=batch_size,
            min_grounding_score=min_grounding_score,
            max_retry_attempts=max_retry_attempts,
        )
        return RegenerationDrainStats(
            backlog_before=backlog_before,
            backlog_after=backlog_before,
            batches_run=0,
            quarantine_completed=0,
            quarantine_failed=0,
            recoverable_seen=int(probe.get("recoverable_quarantine_entries", 0)),
            unrecoverable_seen=int(probe.get("unrecoverable_quarantine_entries", 0)),
            triples_inserted=0,
            triples_grounded=0,
            triples_extracted=0,
            duration_ms=(time.time() - start) * 1000.0,
            dry_run=True,
        )

    batches_run = 0
    quarantine_completed = 0
    quarantine_failed = 0
    recoverable_seen = 0
    unrecoverable_seen = 0
    triples_inserted = 0
    triples_grounded = 0
    triples_extracted = 0

    for _ in range(max_batches):
        remaining = _count_actionable_backlog(db_path, max_retry_attempts)
        if remaining == 0:
            break
        logger.info("regen-drain: batch=%d remaining=%d", batches_run + 1, remaining)

        result = manager.regenerate_quarantined_triples(
            dry_run=False,
            batch_size=batch_size,
            min_grounding_score=min_grounding_score,
            max_retry_attempts=max_retry_attempts,
        )
        errors = result.get("errors", [])
        if errors:
            raise RuntimeError(f"regen-drain batch failed: {' | '.join(str(e) for e in errors)}")

        batches_run += 1
        quarantine_completed += int(result.get("quarantine_marked_completed", 0))
        quarantine_failed += int(result.get("quarantine_marked_failed", 0))
        recoverable_seen += int(result.get("recoverable_quarantine_entries", 0))
        unrecoverable_seen += int(result.get("unrecoverable_quarantine_entries", 0))
        triples_inserted += int(result.get("triples_inserted", 0))
        triples_grounded += int(result.get("triples_grounded", 0))
        triples_extracted += int(result.get("triples_extracted", 0))

        if (
            int(result.get("quarantine_marked_completed", 0)) == 0
            and int(result.get("quarantine_marked_failed", 0)) == 0
            and int(result.get("triples_inserted", 0)) == 0
        ):
            logger.warning(
                "regen-drain: batch produced no actionable progress; stopping to avoid a loop"
            )
            break

    backlog_after = _count_actionable_backlog(db_path, max_retry_attempts)
    return RegenerationDrainStats(
        backlog_before=backlog_before,
        backlog_after=backlog_after,
        batches_run=batches_run,
        quarantine_completed=quarantine_completed,
        quarantine_failed=quarantine_failed,
        recoverable_seen=recoverable_seen,
        unrecoverable_seen=unrecoverable_seen,
        triples_inserted=triples_inserted,
        triples_grounded=triples_grounded,
        triples_extracted=triples_extracted,
        duration_ms=(time.time() - start) * 1000.0,
        dry_run=False,
    )


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Drain quarantine triple regeneration backlog in bounded batches.",
    )
    parser.add_argument("--db-path", default="rag_store.db")
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument("--max-batches", type=int, default=10)
    parser.add_argument("--min-grounding-score", type=float, default=0.3)
    parser.add_argument("--max-retry-attempts", type=int, default=3)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--require-reranker", action="store_true")
    parser.add_argument("--require-llm", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    try:
        stats = drain(
            db_path=args.db_path,
            batch_size=args.batch_size,
            max_batches=args.max_batches,
            min_grounding_score=args.min_grounding_score,
            max_retry_attempts=args.max_retry_attempts,
            dry_run=args.dry_run,
            require_reranker=args.require_reranker,
            require_llm=args.require_llm,
        )
    except RuntimeError as exc:
        logger.error("regen-drain: %s", exc)
        return 2

    logger.info("regen-drain: result=%s", asdict(stats))
    return 0


if __name__ == "__main__":
    sys.exit(main())
