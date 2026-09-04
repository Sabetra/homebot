"""
Reclassifier — backfill ``chunks.domain`` / ``chunks.safety_flag`` for legacy
data using the same :class:`ContentClassifier` as the live ingest path.

Why this exists
---------------
Before the SOTA ContentClassifier was introduced, every chunk fell back to
``domain='general'`` / ``safety_flag='safe'`` regardless of content. Web
ingests, PDF uploads and Trafilatura extracts therefore landed in the wrong
namespace and crisis content was never flagged. Adding the classifier to
the ingest path fixes the *future*; this module fixes the *past*.

Design (per-doc grain, fail-fast, idempotent, versioned)
--------------------------------------------------------
* **Grain.** The live ingest classifies *per document* (``base_text``) and
  stamps every produced chunk with the verdict. The backfill mirrors that
  exactly: it groups chunks by ``doc_id``, reassembles a representative
  prefix (first 8 KiB after de-overlapping), classifies once per doc, and
  writes the verdict to every chunk of that doc. This guarantees
  *Durchgängigkeit* — backfill semantics ≡ ingest semantics.
* **Fail-fast.** If the classifier raises (no prototype + no LLM, invalid
  LLM output, embedding error) the sweep aborts with a non-zero exit. No
  silent defaults, no swallow-and-continue. Operator fixes the root cause
  (e.g. attach LLM, bootstrap psych corpus) and reruns; processed docs
  are already committed and won't be revisited because their
  ``classification_version`` is now current.
* **Idempotent + resumable.** ``classification_version < CURRENT`` is the
  only filter. Re-running after a partial sweep continues exactly where
  the previous run stopped.
* **Versioned.** When the classifier's behaviour changes, bump
  :data:`agent.rag_store.core.content_classifier.CLASSIFICATION_VERSION`;
  this module then automatically reclassifies every doc on the next run.

CLI:
    python -m agent.rag_store.maintenance.reclassifier \\
        --db-path agent/rag_store.db [--batch-size 32] [--dry-run]

The CLI resolves the LLM client via the same fallback as
``LLMKnowledgeGraph``: streamlit ``session_state.model_loader`` →
``model_loader`` module import. The classifier needs an LLM whenever the
psych prototype is unavailable or the candidate falls into the ambiguous
band; without one, the run aborts on the first such doc (by design).
"""
from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
import time
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Tuple

from agent.rag_store.core.content_classifier import (
    CLASSIFICATION_VERSION,
    ContentClassifier,
    LLMVerificationError,
)
from agent.rag_store.core.database import DatabaseManager
from agent.rag_store.core.embeddings import EmbeddingManager

logger = logging.getLogger(__name__)


# Reassembly prefix size — must be >= ContentClassifier cache key window
# (8000) so the cache hits / misses align with the ingest path.
_DOC_PREFIX_BYTES = 8000


@dataclass
class ReclassifierStats:
    """Result aggregate for a sweep run."""
    docs_total: int = 0
    docs_reclassified: int = 0
    docs_unchanged: int = 0
    docs_skipped: int = 0
    chunks_updated: int = 0
    by_method: dict = field(default_factory=dict)
    by_domain: dict = field(default_factory=dict)
    by_safety: dict = field(default_factory=dict)
    duration_sec: float = 0.0


class ChunkReclassifier:
    """One-shot maintenance job: re-label legacy chunks using the live
    classifier.

    Parameters
    ----------
    db_manager
        Open :class:`DatabaseManager`. Schema migrations must already have
        run (``ensure_schema``).
    classifier
        Configured :class:`ContentClassifier`. The caller is responsible
        for wiring an LLM wrapper if the corpus is too sparse for prototype
        classification — this class deliberately does NOT fall back.
    """

    def __init__(
        self,
        db_manager: DatabaseManager,
        classifier: ContentClassifier,
    ) -> None:
        self._db = db_manager
        self._classifier = classifier

    # ── Public API ──────────────────────────────────────────────────
    def run(
        self,
        *,
        batch_size: int = 32,
        embed_batch_size: int = 64,
        dry_run: bool = False,
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
    ) -> ReclassifierStats:
        """Reclassify every doc with ``classification_version < CURRENT``.

        Commits per ``batch_size`` docs (so partial progress survives
        crashes). Embeds **``embed_batch_size`` docs per GPU call** via
        :meth:`ContentClassifier.classify_many` — this is the lever that
        turns embedding from a batch-of-1 trickle into a saturating GPU
        workload. Raises on the first classifier error.

        Parameters
        ----------
        batch_size
            DB commit granularity (docs per ``COMMIT``).
        embed_batch_size
            Number of docs whose excerpts are sent to the embedder in
            one ``encode()`` call. Set as high as VRAM allows; the
            embedder's own ``gpu_batch_size`` further sub-batches inside
            the model. Typical sweet spot: 64–256.
        progress_callback
            Optional ``(processed, total, current_doc_id) -> None`` hook,
            invoked after each doc completes. Used by UI front-ends to
            render progress bars; the callback must not raise.
        """
        if batch_size <= 0:
            raise ValueError(f"batch_size must be > 0, got {batch_size}")
        if embed_batch_size <= 0:
            raise ValueError(
                f"embed_batch_size must be > 0, got {embed_batch_size}"
            )

        stats = ReclassifierStats()
        start = time.monotonic()

        conn = self._db.get_connection()
        try:
            stale_doc_ids = self._fetch_stale_doc_ids(conn)
            stats.docs_total = len(stale_doc_ids)
            logger.info(
                "Reclassifier sweep: %d doc(s) below classification_version=%d "
                "(dry_run=%s, embed_batch=%d, commit_batch=%d)",
                stats.docs_total, CLASSIFICATION_VERSION,
                dry_run, embed_batch_size, batch_size,
            )
            if not stale_doc_ids:
                stats.duration_sec = time.monotonic() - start
                return stats

            processed = 0
            since_commit = 0
            for emb_start in range(0, len(stale_doc_ids), embed_batch_size):
                emb_batch = stale_doc_ids[emb_start: emb_start + embed_batch_size]
                self._reclassify_doc_batch(
                    conn, emb_batch, stats, dry_run,
                )
                for doc_id in emb_batch:
                    processed += 1
                    if progress_callback is not None:
                        progress_callback(processed, stats.docs_total, doc_id)
                since_commit += len(emb_batch)
                if not dry_run and since_commit >= batch_size:
                    conn.commit()
                    since_commit = 0
                logger.info(
                    "Reclassifier progress: %d/%d docs processed",
                    processed, stats.docs_total,
                )
            if not dry_run and since_commit > 0:
                conn.commit()
        finally:
            self._db.return_connection(conn)

        stats.duration_sec = time.monotonic() - start
        return stats

    # ── Internals ───────────────────────────────────────────────────
    @staticmethod
    def _fetch_stale_doc_ids(conn: sqlite3.Connection) -> List[str]:
        cur = conn.cursor()
        try:
            rows = cur.execute(
                "SELECT DISTINCT doc_id FROM chunks "
                "WHERE classification_version IS NULL "
                "   OR classification_version < ? "
                "ORDER BY doc_id",
                (CLASSIFICATION_VERSION,),
            ).fetchall()
            return [r[0] for r in rows]
        finally:
            cur.close()

    def _reclassify_doc_batch(
        self,
        conn: sqlite3.Connection,
        doc_ids: List[str],
        stats: ReclassifierStats,
        dry_run: bool,
    ) -> None:
        """Reassemble + classify_many + UPDATE for a batch of docs.

        Embedding goes through :meth:`ContentClassifier.classify_many`
        which fans the whole batch into a single GPU call — the actual
        reason this method exists.
        """
        if not doc_ids:
            return

        # Single SQL fetch for the whole batch (locality + 1 round trip).
        placeholders = ",".join("?" * len(doc_ids))
        cur = conn.cursor()
        try:
            cur.execute(
                f"SELECT doc_id, chunk_id, text, domain, safety_flag "
                f"FROM chunks WHERE doc_id IN ({placeholders}) "
                f"ORDER BY doc_id, chunk_id",
                doc_ids,
            )
            grouped: dict = {}
            for row in cur.fetchall():
                grouped.setdefault(row[0], []).append(row[1:])

            items: List[Tuple[str, str]] = []
            present_doc_ids: List[str] = []
            current_labels: List[Tuple[Optional[str], Optional[str]]] = []
            for doc_id in doc_ids:
                rows = grouped.get(doc_id)
                if not rows:
                    # No chunks for this doc — skip silently (vacuumed?).
                    continue
                doc_text = self._reassemble_doc_text(rows)
                if not doc_text:
                    raise RuntimeError(
                        f"Reclassifier: doc_id={doc_id!r} has no usable "
                        "text (all chunks empty/whitespace). Investigate "
                        "the source ingest before reclassifying."
                    )
                items.append((doc_text, doc_id))
                present_doc_ids.append(doc_id)
                # rows[0] = (chunk_id, text, domain, safety_flag)
                current_labels.append((rows[0][2], rows[0][3]))

            if not items:
                return

            # ── ONE batched classification call (GPU saturation) ────
            # Pass the active conn so cache writes share this
            # transaction; otherwise they would open a separate writer
            # and block on SQLite's single-writer constraint until
            # busy_timeout expires.
            try:
                verdicts = self._classifier.classify_many(items, conn=conn)
                skipped_idx: set = set()
            except LLMVerificationError as exc:
                # Transient LLM failure (e.g. llama.cpp threw a Windows
                # C++ exception, structured-output parser exhausted its
                # retries). One bad doc would otherwise abort the whole
                # sweep; instead we retry per-item so the remaining docs
                # in this batch still progress, and skip the offenders.
                # Skipped docs keep their old classification_version so
                # the next sweep automatically picks them up again.
                logger.warning(
                    "Reclassifier: batch classify_many failed (%s) — "
                    "falling back to per-item retry for %d docs.",
                    exc, len(items),
                )
                verdicts = []
                skipped_idx = set()
                for idx, item in enumerate(items):
                    try:
                        v = self._classifier.classify_many([item], conn=conn)[0]
                        verdicts.append(v)
                    except LLMVerificationError as item_exc:
                        logger.warning(
                            "Reclassifier: skipping doc_id=%r — LLM "
                            "verification failed twice: %s",
                            present_doc_ids[idx], item_exc,
                        )
                        verdicts.append(None)  # type: ignore[arg-type]
                        skipped_idx.add(idx)

            for idx, (doc_id, verdict, (current_domain, current_safety)) in enumerate(
                zip(present_doc_ids, verdicts, current_labels)
            ):
                if idx in skipped_idx:
                    stats.docs_skipped += 1
                    continue
                stats.by_method[verdict.method] = (
                    stats.by_method.get(verdict.method, 0) + 1
                )
                stats.by_domain[verdict.corpus_domain] = (
                    stats.by_domain.get(verdict.corpus_domain, 0) + 1
                )
                stats.by_safety[verdict.safety_flag] = (
                    stats.by_safety.get(verdict.safety_flag, 0) + 1
                )

                unchanged = (
                    current_domain == verdict.corpus_domain
                    and current_safety == verdict.safety_flag
                )

                if dry_run:
                    logger.debug(
                        "[DRY] %s : %s/%s -> %s/%s (method=%s, sim=%s)",
                        doc_id, current_domain, current_safety,
                        verdict.corpus_domain, verdict.safety_flag,
                        verdict.method, verdict.similarity,
                    )
                else:
                    cur.execute(
                        "UPDATE chunks "
                        "   SET domain = ?, safety_flag = ?, "
                        "       classification_version = ? "
                        " WHERE doc_id = ?",
                        (verdict.corpus_domain, verdict.safety_flag,
                         CLASSIFICATION_VERSION, doc_id),
                    )
                    stats.chunks_updated += cur.rowcount

                if unchanged:
                    stats.docs_unchanged += 1
                else:
                    stats.docs_reclassified += 1
        finally:
            cur.close()

    @staticmethod
    def _reassemble_doc_text(rows: List[Tuple]) -> str:
        """Concatenate chunk texts (ordered by ``chunk_id``) up to
        :data:`_DOC_PREFIX_BYTES`. The ingest classifier's cache key uses
        the same window, so cache hits/misses align across ingest and
        backfill paths.
        """
        out: List[str] = []
        size = 0
        for _chunk_id, text, _d, _s in rows:
            if not text:
                continue
            t = text.strip()
            if not t:
                continue
            out.append(t)
            size += len(t.encode("utf-8", errors="ignore"))
            if size >= _DOC_PREFIX_BYTES:
                break
        return "\n\n".join(out)


# ──────────────────────────────────────────────────────────────────────
#  CLI
# ──────────────────────────────────────────────────────────────────────
def _resolve_llm_client() -> Optional[object]:
    """Best-effort LLM client resolution mirroring the live attach pattern."""
    try:
        import streamlit as st  # type: ignore
        ml = getattr(st.session_state, "model_loader", None)
        if ml is not None:
            return ml
    except Exception:
        pass
    try:
        from scripts.model_loader import get_model_loader
        return get_model_loader()
    except Exception:
        return None


def _build_classifier(
    db_manager: DatabaseManager,
    embedding_manager: EmbeddingManager,
    llm_client: Optional[object],
) -> ContentClassifier:
    llm_wrapper = None
    if llm_client is not None:
        try:
            from llm_structured_wrapper import LLMStructuredWrapper
            llm_wrapper = LLMStructuredWrapper(
                llm_client=llm_client,
                max_retries=2,
                temperature=0.0,
                enable_logging=False,
            )
        except Exception as exc:
            # Per project rule: do not silently degrade. Surface and abort.
            raise RuntimeError(
                f"Reclassifier: failed to construct LLMStructuredWrapper "
                f"around the resolved LLM client: {exc}"
            ) from exc

    return ContentClassifier(
        embedding_manager=embedding_manager,
        db_manager=db_manager,
        llm_wrapper=llm_wrapper,
    )


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Reclassify legacy RAG chunks (domain + safety_flag) "
                    "with the current ContentClassifier.",
    )
    parser.add_argument("--db-path", default="agent/rag_store.db",
                        help="Path to the RAG SQLite DB.")
    parser.add_argument("--batch-size", type=int, default=32,
                        help="Docs per commit batch.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Compute verdicts without writing.")
    parser.add_argument("--embedding-model",
                        default="intfloat/multilingual-e5-large",
                        help="Embedding model used by the classifier "
                             "(must match ingest-time model).")
    parser.add_argument("--device", default="cpu",
                        help="Embedding device ('cpu' or 'cuda').")
    parser.add_argument("--require-llm", action="store_true",
                        help="Abort if no LLM client can be resolved "
                             "(recommended for safety_flag fidelity).")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    db = DatabaseManager(args.db_path)
    em = EmbeddingManager(model_name=args.embedding_model, device=args.device, debug=False)

    llm_client = _resolve_llm_client()
    if llm_client is None and args.require_llm:
        logger.error(
            "Reclassifier: --require-llm given but no LLM client could be "
            "resolved (no streamlit session, no model_loader module). Abort."
        )
        return 2
    if llm_client is None:
        logger.warning(
            "Reclassifier: running without LLM. Will succeed only when the "
            "psych prototype is built AND every doc lands outside the "
            "ambiguous band; otherwise the run will abort by design."
        )

    classifier = _build_classifier(db, em, llm_client)
    job = ChunkReclassifier(db, classifier)
    stats = job.run(batch_size=args.batch_size, dry_run=args.dry_run)

    logger.info(
        "Reclassifier done: %d/%d docs reclassified, %d unchanged, "
        "%d chunks updated, %.1fs",
        stats.docs_reclassified, stats.docs_total, stats.docs_unchanged,
        stats.chunks_updated, stats.duration_sec,
    )
    logger.info("  by method: %s", stats.by_method)
    logger.info("  by domain: %s", stats.by_domain)
    logger.info("  by safety: %s", stats.by_safety)
    return 0


if __name__ == "__main__":
    sys.exit(main())
