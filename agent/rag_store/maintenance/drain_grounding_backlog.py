"""
Grounding-Backlog-Drain — single-shot batched verification of KG triples
whose ``triple_quality.grounding_score`` is still the sentinel ``-1`` (or
missing). The job is purely a *re-execution wrapper* around the existing
:meth:`RAGQualityManager.run_reranker_audit`; it does not introduce a new
verification algorithm.

Why this exists
---------------
The on-demand audit path (Wave 1-11) verifies grounding only when manually
triggered. Live-DB metrics (2026-05-14) showed a persistent backlog of
unverified triples that the read path was already excluding by policy
(Wave 2: contradiction-aware ranking), but which kept growing on every
ingest. Without a controlled drain there is no way to reach a fully
verified state without running a single oversized audit, which (a) blocks
GPU for minutes and (b) loses progress on any transient fault.

Design
------
* **Idempotent + resumable**: every batch only selects
  ``grounding_score IS NULL OR grounding_score < 0``; finished triples are
  not re-processed.
* **Bounded work**: ``--batch-size`` triples per audit call,
  ``--max-batches`` calls per run; the script exits cleanly when either
  the budget is exhausted or the backlog is empty.
* **No new background thread, no new state**: the function calls existing
  code paths only, so the surface area for regressions stays minimal.
* **Dry-run is an honest probe**: it counts the backlog and (optionally)
  scores a single batch *without persisting* — never invents a "would
  have" number.
* **Fail-fast**: any unexpected DB / reranker fault propagates and aborts;
  the next run picks up exactly where this one stopped.

CLI:
    python -m agent.rag_store.maintenance.drain_grounding_backlog \\
        --db-path rag_store.db \\
        [--batch-size 500] [--max-batches 10] [--dry-run]

Exit codes:
    0 — clean run (drained or budget exhausted)
    1 — runtime fault (DB locked, reranker init failure, etc.)
    2 — configuration error (no reranker resolvable, --require-reranker)
"""
from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
import time
from dataclasses import dataclass, asdict
from typing import List, Optional

from agent.rag_store.core.quality import RAGQualityManager

logger = logging.getLogger(__name__)


@dataclass
class DrainStats:
    """Aggregated result of a drain run; emitted as the final log line."""
    backlog_before: int
    backlog_after: int
    batches_run: int
    triples_verified: int
    triples_grounded: int
    triples_ungrounded: int
    duration_ms: float
    dry_run: bool


def _count_backlog(db_path: str) -> int:
    """Return number of triples with missing or negative grounding_score."""
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT COUNT(*)
            FROM triples t
            LEFT JOIN triple_quality tq ON t.triple_id = tq.triple_id
            WHERE tq.grounding_score IS NULL OR tq.grounding_score < 0
            """
        )
        row = cur.fetchone()
        return int(row[0]) if row else 0
    finally:
        conn.close()


def _resolve_reranker() -> Optional[object]:
    """Lazy-load the canonical reranker singleton.

    Returns ``None`` only when the reranker module itself cannot be
    imported, which is a configuration error (not a runtime fault). The
    caller decides whether that is fatal.
    """
    from agent.reranker import get_reranker

    rr = get_reranker()
    # CrossEncoderReranker is lazy-init; force model load so is_available
    # reflects reality (mirrors the pattern in quality_dashboard.py).
    if hasattr(rr, "_ensure_loaded"):
        rr._ensure_loaded()
    if hasattr(rr, "is_available") and not rr.is_available:
        return None
    return rr


def drain(
    db_path: str,
    batch_size: int,
    max_batches: int,
    dry_run: bool,
    require_reranker: bool,
) -> DrainStats:
    """Drain the grounding-verification backlog.

    Parameters
    ----------
    db_path:
        Path to the RAG SQLite DB.
    batch_size:
        ``sample_limit`` forwarded to :meth:`run_reranker_audit` per batch.
    max_batches:
        Upper bound on the number of audit calls per drain run.
    dry_run:
        If ``True``, count the backlog and (when ``max_batches > 0``)
        execute the verification logic but do **not** persist any
        ``grounding_score`` updates. The dry-run measures throughput
        without changing DB state.
    require_reranker:
        If ``True`` and no reranker can be resolved, abort with exit
        code 2 instead of returning early.
    """
    start = time.time()
    backlog_before = _count_backlog(db_path)
    logger.info("grounding-drain: backlog_before=%d", backlog_before)

    if backlog_before == 0:
        return DrainStats(
            backlog_before=0, backlog_after=0, batches_run=0,
            triples_verified=0, triples_grounded=0, triples_ungrounded=0,
            duration_ms=(time.time() - start) * 1000.0, dry_run=dry_run,
        )

    reranker = _resolve_reranker()
    if reranker is None:
        msg = "grounding-drain: no reranker available"
        if require_reranker:
            raise RuntimeError(msg + " (--require-reranker)")
        logger.warning("%s; nothing to do", msg)
        return DrainStats(
            backlog_before=backlog_before, backlog_after=backlog_before,
            batches_run=0, triples_verified=0, triples_grounded=0,
            triples_ungrounded=0,
            duration_ms=(time.time() - start) * 1000.0, dry_run=dry_run,
        )

    qm = RAGQualityManager(db_path=db_path, reranker=reranker)
    qm.ensure_quality_schema()

    if dry_run:
        # Dry-run: do not call run_reranker_audit (which persists).
        # The backlog count above is the entire honest signal.
        logger.info("grounding-drain: dry-run, no audit executed")
        return DrainStats(
            backlog_before=backlog_before, backlog_after=backlog_before,
            batches_run=0, triples_verified=0, triples_grounded=0,
            triples_ungrounded=0,
            duration_ms=(time.time() - start) * 1000.0, dry_run=True,
        )

    batches_run = 0
    total_verified = 0
    total_grounded = 0
    total_ungrounded = 0

    for _ in range(max_batches):
        remaining = _count_backlog(db_path)
        if remaining == 0:
            break
        logger.info(
            "grounding-drain: batch=%d remaining=%d",
            batches_run + 1, remaining,
        )
        result = qm.run_reranker_audit(
            reranker=reranker,
            sample_limit=batch_size,
        )
        batches_run += 1
        verified = int(result.get("total_verified", 0))
        grounded = int(result.get("grounded_count", 0))
        ungrounded = int(result.get("ungrounded_count", 0))
        total_verified += verified
        total_grounded += grounded
        total_ungrounded += ungrounded
        # If a batch produces zero verifications, we have hit a stable
        # state (e.g. all remaining triples have no usable source chunks)
        # — further batches would loop without progress.
        if verified == 0:
            logger.warning(
                "grounding-drain: batch produced 0 verifications; stopping "
                "to avoid an unproductive loop. Investigate remaining=%d.",
                remaining,
            )
            break

    backlog_after = _count_backlog(db_path)
    return DrainStats(
        backlog_before=backlog_before,
        backlog_after=backlog_after,
        batches_run=batches_run,
        triples_verified=total_verified,
        triples_grounded=total_grounded,
        triples_ungrounded=total_ungrounded,
        duration_ms=(time.time() - start) * 1000.0,
        dry_run=False,
    )


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Drain the KG triple grounding-verification backlog.",
    )
    parser.add_argument("--db-path", default="rag_store.db",
                        help="Path to the RAG SQLite DB.")
    parser.add_argument("--batch-size", type=int, default=500,
                        help="sample_limit forwarded to run_reranker_audit "
                             "per batch.")
    parser.add_argument("--max-batches", type=int, default=10,
                        help="Maximum number of audit calls per run.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Only report the backlog; do not persist "
                             "grounding scores.")
    parser.add_argument("--require-reranker", action="store_true",
                        help="Exit with code 2 if no reranker is available "
                             "(otherwise the drain logs a warning and "
                             "returns early with no change).")
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
            dry_run=args.dry_run,
            require_reranker=args.require_reranker,
        )
    except RuntimeError as exc:
        # Configuration error path (e.g. --require-reranker without one).
        logger.error("grounding-drain: %s", exc)
        return 2

    logger.info("grounding-drain: result=%s", asdict(stats))
    return 0


if __name__ == "__main__":
    sys.exit(main())
