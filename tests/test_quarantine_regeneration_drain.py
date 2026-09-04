import json
import sqlite3
from datetime import datetime, timezone

from agent.rag_store.core.quality import RAGQualityManager
from agent.rag_store.maintenance.drain_quarantine_regeneration import (
    _count_actionable_backlog,
    drain,
)


class _FakeReranker:
    is_available = True

    def _ensure_loaded(self):
        return None

    def rerank(self, query, passages, top_k=1, text_key="text"):
        return [{"score": 0.95}]


def _insert_quarantine(conn, source_id, status, attempts):
    conn.execute(
        "INSERT INTO quarantine(source_table, source_id, reason, quarantined_at, data_backup, regeneration_status, regeneration_attempts) "
        "VALUES ('triples', ?, 'ungrounded', ?, ?, ?, ?)",
        (
            source_id,
            datetime.now(timezone.utc).isoformat(),
            json.dumps({"doc_id": "doc-1"}),
            status,
            attempts,
        ),
    )


def test_actionable_backlog_respects_retry_budget(tmp_path):
    db_path = tmp_path / "regen_backlog.db"
    manager = RAGQualityManager(db_path=str(db_path), reranker=_FakeReranker())
    manager.ensure_quality_schema()

    conn = sqlite3.connect(db_path)
    _insert_quarantine(conn, "1", "pending", 0)
    _insert_quarantine(conn, "2", "failed", 0)
    _insert_quarantine(conn, "3", "failed", 3)
    _insert_quarantine(conn, "4", "completed", 1)
    conn.commit()
    conn.close()

    assert _count_actionable_backlog(str(db_path), max_retry_attempts=3) == 2


def test_regeneration_drain_dry_run_reports_recoverability(monkeypatch, tmp_path):
    db_path = tmp_path / "regen_dry.db"
    manager = RAGQualityManager(db_path=str(db_path), reranker=_FakeReranker())
    manager.ensure_quality_schema()

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("INSERT INTO documents(doc_id) VALUES (?)", ("doc-1",))
    conn.execute(
        "INSERT INTO chunks(doc_id, chunk_id, text, metadata, embedding) VALUES (?, ?, ?, ?, ?)",
        (
            "doc-1",
            9,
            "This chunk contains explicit evidence that can regenerate grounded triples for the sample relation.",
            json.dumps({"source": "doc-1"}),
            b"emb",
        ),
    )
    conn.execute(
        "INSERT INTO quarantine(source_table, source_id, reason, quarantined_at, data_backup, regeneration_status, regeneration_attempts) "
        "VALUES ('triples', ?, 'ungrounded', ?, ?, 'pending', 0)",
        (
            "100",
            datetime.now(timezone.utc).isoformat(),
            json.dumps(
                {
                    "doc_id": "doc-1",
                    "subject": "Alice",
                    "predicate": "works_at",
                    "object": "Acme",
                    "source_chunk_id": 9,
                }
            ),
        ),
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(
        "agent.rag_store.maintenance.drain_quarantine_regeneration._resolve_reranker",
        lambda: _FakeReranker(),
    )

    stats = drain(
        db_path=str(db_path),
        batch_size=10,
        max_batches=5,
        min_grounding_score=0.3,
        max_retry_attempts=3,
        dry_run=True,
        require_reranker=True,
        require_llm=True,
    )

    assert stats.dry_run is True
    assert stats.backlog_before == 1
    assert stats.recoverable_seen == 1
    assert stats.unrecoverable_seen == 0
    assert stats.backlog_after == 1
