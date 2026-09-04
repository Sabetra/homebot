import json
import sqlite3
import sys
import types
from datetime import datetime, timezone
from types import SimpleNamespace

from agent.rag_store.core.quality import RAGQualityManager
from agent.rag_store.utils.memory import calculate_triple_hash


class _FakeReranker:
    is_available = True

    def _ensure_loaded(self):
        return None

    def rerank(self, query, passages, top_k=1, text_key="text"):
        return [{"score": 0.91}]


class _SemanticReranker:
    is_available = True

    def _ensure_loaded(self):
        return None

    def rerank(self, query, passages, top_k=1, text_key="text"):
        text = str(passages[0].get(text_key, ""))
        if "semantic anchor" in text.lower():
            return [{"score": 0.99}]
        return [{"score": 0.05}]


def _insert_quarantine_row(conn, *, source_id, backup, reason="ungrounded"):
    conn.execute(
        "INSERT INTO quarantine(source_table, source_id, reason, quarantined_at, data_backup) "
        "VALUES ('triples', ?, ?, ?, ?)",
        (source_id, reason, datetime.now(timezone.utc).isoformat(), json.dumps(backup)),
    )


def _install_fake_kg_extractor(monkeypatch, triples=None):
    fake_module = types.ModuleType("agent.llm_knowledge_graph")

    class _FakeExtractor:
        def __init__(self):
            self.llm_client = SimpleNamespace(llm=object())

        def extract_from_chunks(self, chunk_data, doc_context):
            return triples or []

    fake_module.LLMKnowledgeGraphExtractor = _FakeExtractor
    fake_module.normalize_entity_for_matching = lambda value: str(value).strip().lower()
    monkeypatch.setitem(sys.modules, "agent.llm_knowledge_graph", fake_module)


def test_regenerate_quarantined_triples_dry_run_classifies_recoverability(tmp_path):
    db_path = tmp_path / "quality.db"
    manager = RAGQualityManager(db_path=str(db_path), reranker=_FakeReranker())
    manager.ensure_quality_schema()

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("INSERT INTO documents(doc_id) VALUES (?)", ("doc-1",))
    conn.execute(
        "INSERT INTO chunks(doc_id, chunk_id, text, metadata, embedding) VALUES (?, ?, ?, ?, ?)",
        (
            "doc-1",
            7,
            "This source chunk contains enough grounded factual content to regenerate a valid triple.",
            json.dumps({"source": "doc-1"}),
            b"emb",
        ),
    )
    _insert_quarantine_row(
        conn,
        source_id="101",
        backup={
            "doc_id": "doc-1",
            "subject": "Alice",
            "predicate": "works_at",
            "object": "Acme",
            "metadata": {"source_chunk_id": 7},
        },
    )
    _insert_quarantine_row(
        conn,
        source_id="102",
        backup={
            "doc_id": "missing-doc",
            "subject": "Bob",
            "predicate": "lives_in",
            "object": "Berlin",
            "metadata": {"source_chunk_id": 99},
        },
    )
    conn.commit()
    conn.close()

    stats = manager.regenerate_quarantined_triples(dry_run=True, batch_size=10)

    assert stats["recoverable_quarantine_entries"] == 1
    assert stats["unrecoverable_quarantine_entries"] == 1
    assert stats["source_chunk_groups_total"] == 1
    assert stats["source_chunks_found"] == 1
    assert stats["source_chunks_missing"] == 1

    conn = sqlite3.connect(db_path)
    statuses = conn.execute(
        "SELECT source_id, regeneration_status FROM quarantine ORDER BY source_id"
    ).fetchall()
    conn.close()
    assert statuses == [("101", "pending"), ("102", "pending")]


def test_regeneration_skips_failed_rows_at_retry_limit(tmp_path):
    db_path = tmp_path / "quality_retry_limit.db"
    manager = RAGQualityManager(db_path=str(db_path), reranker=_FakeReranker())
    manager.ensure_quality_schema()

    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO quarantine(source_table, source_id, reason, quarantined_at, data_backup, regeneration_status, regeneration_attempts) "
        "VALUES ('triples', 'retry-max', 'ungrounded', ?, ?, 'failed', 3)",
        (
            datetime.now(timezone.utc).isoformat(),
            json.dumps({"doc_id": "missing-doc", "subject": "A", "predicate": "B", "object": "C"}),
        ),
    )
    conn.commit()
    conn.close()

    stats = manager.regenerate_quarantined_triples(
        dry_run=True,
        batch_size=10,
        max_retry_attempts=3,
    )

    assert stats["quarantine_entries_processed"] == 0
    assert stats["recoverable_quarantine_entries"] == 0
    assert stats["unrecoverable_quarantine_entries"] == 0


def test_resolve_regeneration_source_prefers_semantic_reranker(tmp_path):
    db_path = tmp_path / "quality_semantic.db"
    manager = RAGQualityManager(db_path=str(db_path), reranker=_SemanticReranker())
    manager.ensure_quality_schema()

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("INSERT INTO documents(doc_id) VALUES (?)", ("doc-1",))
    conn.execute(
        "INSERT INTO chunks(doc_id, chunk_id, text, metadata, embedding) VALUES (?, ?, ?, ?, ?)",
        (
            "doc-1",
            1,
            "Alice and Acme are both mentioned here but the relation itself is not clearly evidenced.",
            "{}",
            b"emb",
        ),
    )
    conn.execute(
        "INSERT INTO chunks(doc_id, chunk_id, text, metadata, embedding) VALUES (?, ?, ?, ?, ?)",
        (
            "doc-1",
            2,
            "This chunk provides the semantic anchor that confirms Alice works at Acme Corp with explicit evidence.",
            "{}",
            b"emb",
        ),
    )
    conn.commit()

    cur = conn.cursor()
    resolved_doc_id, resolved_chunk_id, chunk_text, resolution_kind = manager._resolve_quarantine_regeneration_source(
        cur,
        {
            "doc_id": "doc-1",
            "subject": "Alice",
            "predicate": "works_at",
            "object": "Acme Corp",
        },
        reranker=_SemanticReranker(),
    )
    conn.close()

    assert resolved_doc_id == "doc-1"
    assert resolved_chunk_id == 2
    assert "semantic anchor" in chunk_text.lower()
    assert resolution_kind == "doc_semantic_reranker"


def test_regenerate_quarantined_triples_marks_failed_and_completed(tmp_path, monkeypatch):
    fake_module = types.ModuleType("agent.llm_knowledge_graph")

    class _FakeExtractor:
        def __init__(self):
            self.llm_client = SimpleNamespace(llm=object())

        def extract_from_chunks(self, chunk_data, doc_context):
            return [
                SimpleNamespace(
                    subject="Alice",
                    predicate="works_at",
                    object="Acme Corp",
                )
            ]

    fake_module.LLMKnowledgeGraphExtractor = _FakeExtractor
    fake_module.normalize_entity_for_matching = lambda value: str(value).strip().lower()
    monkeypatch.setitem(sys.modules, "agent.llm_knowledge_graph", fake_module)

    db_path = tmp_path / "quality_exec.db"
    manager = RAGQualityManager(db_path=str(db_path), reranker=_FakeReranker())
    manager.ensure_quality_schema()

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("INSERT INTO documents(doc_id) VALUES (?)", ("doc-1",))
    conn.execute(
        "INSERT INTO chunks(doc_id, chunk_id, text, metadata, embedding) VALUES (?, ?, ?, ?, ?)",
        (
            "doc-1",
            3,
            "Alice works at Acme Corp and the statement is clearly supported by this chunk text.",
            json.dumps({"source": "doc-1"}),
            b"emb",
        ),
    )
    _insert_quarantine_row(
        conn,
        source_id="201",
        backup={
            "doc_id": "doc-1",
            "subject": "Alice",
            "predicate": "works_at",
            "object": "Acme Corp",
            "metadata": {"source_chunk_id": 3},
        },
    )
    _insert_quarantine_row(
        conn,
        source_id="202",
        backup={
            "doc_id": "missing-doc",
            "subject": "Mallory",
            "predicate": "owns",
            "object": "Ghost Inc",
            "metadata": {"source_chunk_id": 404},
        },
    )
    triple_hash = calculate_triple_hash("Alice", "works_at", "Acme Corp")
    _insert_quarantine_row(
        conn,
        source_id="299",
        backup={
            "doc_id": "legacy",
            "note": f'string contains "triple_hash": "{triple_hash}" but not as JSON field',
        },
    )
    conn.execute(
        "UPDATE quarantine SET regeneration_status = 'completed' WHERE source_id = '299'"
    )
    conn.commit()
    conn.close()

    stats = manager.regenerate_quarantined_triples(
        dry_run=False,
        batch_size=10,
        min_grounding_score=0.3,
    )

    assert stats["triples_inserted"] == 1
    assert stats["quarantine_marked_completed"] == 1
    assert stats["quarantine_marked_failed"] == 1

    conn = sqlite3.connect(db_path)
    quarantine_rows = conn.execute(
        "SELECT source_id, regeneration_status, last_regeneration_error "
        "FROM quarantine ORDER BY source_id"
    ).fetchall()
    triple_count = conn.execute("SELECT COUNT(*) FROM triples").fetchone()[0]
    quality_count = conn.execute("SELECT COUNT(*) FROM triple_quality").fetchone()[0]
    conn.close()

    assert quarantine_rows[0][0] == "201"
    assert quarantine_rows[0][1] == "completed"
    assert quarantine_rows[1][0] == "202"
    assert quarantine_rows[1][1] == "failed"
    assert "Source chunk unavailable" in quarantine_rows[1][2]
    assert triple_count == 1
    assert quality_count == 1


def test_failed_row_escalates_to_permanent_failed_at_retry_limit(tmp_path, monkeypatch):
    _install_fake_kg_extractor(monkeypatch)
    db_path = tmp_path / "quality_escalation.db"
    manager = RAGQualityManager(db_path=str(db_path), reranker=_FakeReranker())
    manager.ensure_quality_schema()

    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO quarantine(source_table, source_id, reason, quarantined_at, data_backup, regeneration_status, regeneration_attempts) "
        "VALUES ('triples', 'esc-1', 'ungrounded', ?, ?, 'failed', 2)",
        (
            datetime.now(timezone.utc).isoformat(),
            json.dumps({"doc_id": "missing-doc", "subject": "A", "predicate": "B", "object": "C"}),
        ),
    )
    conn.commit()
    conn.close()

    stats = manager.regenerate_quarantined_triples(
        dry_run=False, batch_size=10, max_retry_attempts=3
    )

    conn = sqlite3.connect(db_path)
    status, attempts = conn.execute(
        "SELECT regeneration_status, regeneration_attempts FROM quarantine WHERE source_id='esc-1'"
    ).fetchone()
    conn.close()
    assert status == "permanent_failed"
    assert attempts == 3
    assert stats["quarantine_marked_permanent_failed"] == 1


def test_invalid_backup_goes_straight_to_permanent_failed(tmp_path, monkeypatch):
    _install_fake_kg_extractor(monkeypatch)
    db_path = tmp_path / "quality_invalid_backup.db"
    manager = RAGQualityManager(db_path=str(db_path), reranker=_FakeReranker())
    manager.ensure_quality_schema()

    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO quarantine(source_table, source_id, reason, quarantined_at, data_backup) "
        "VALUES ('triples', 'bad-json', 'ungrounded', ?, 'NOT-JSON{')",
        (datetime.now(timezone.utc).isoformat(),),
    )
    conn.commit()
    conn.close()

    manager.regenerate_quarantined_triples(dry_run=False, batch_size=10)

    conn = sqlite3.connect(db_path)
    status = conn.execute(
        "SELECT regeneration_status FROM quarantine WHERE source_id='bad-json'"
    ).fetchone()[0]
    conn.close()
    assert status == "permanent_failed"


def test_regeneration_generation_allows_second_pass_but_blocks_third(tmp_path, monkeypatch):
    _install_fake_kg_extractor(monkeypatch)
    db_path = tmp_path / "quality_lineage.db"
    manager = RAGQualityManager(db_path=str(db_path), reranker=_FakeReranker())
    manager.ensure_quality_schema()

    conn = sqlite3.connect(db_path)
    # Generation 1 (below limit 2): stays in the recoverable path
    _insert_quarantine_row(
        conn,
        source_id="gen-1",
        backup={
            "doc_id": "missing-doc",
            "subject": "A", "predicate": "B", "object": "C",
            "metadata": json.dumps({"regenerated_from_quarantine": True, "regeneration_generation": 1}),
        },
    )
    # Generation 2 (at limit): lineage exhausted → permanent_failed
    _insert_quarantine_row(
        conn,
        source_id="gen-2",
        backup={
            "doc_id": "missing-doc",
            "subject": "D", "predicate": "E", "object": "F",
            "metadata": json.dumps({"regenerated_from_quarantine": True, "regeneration_generation": 2}),
        },
    )
    conn.commit()
    conn.close()

    stats = manager.regenerate_quarantined_triples(
        dry_run=False, batch_size=10, max_regeneration_generations=2
    )

    conn = sqlite3.connect(db_path)
    rows = dict(conn.execute(
        "SELECT source_id, regeneration_status FROM quarantine"
    ).fetchall())
    conn.close()
    # gen-1 was processed (chunk missing → failed), gen-2 skipped as exhausted
    assert rows["gen-1"] == "failed"
    assert rows["gen-2"] == "permanent_failed"
    assert stats["already_regenerated_skipped"] == 1


def test_purge_keeps_pending_and_failed_rows(tmp_path):
    db_path = tmp_path / "quality_purge.db"
    manager = RAGQualityManager(db_path=str(db_path), reranker=_FakeReranker())
    manager.ensure_quality_schema()

    conn = sqlite3.connect(db_path)
    expired = "2020-01-01T00:00:00+00:00"
    for source_id, status in [
        ("p-pending", "pending"), ("p-failed", "failed"),
        ("p-completed", "completed"), ("p-permanent", "permanent_failed"),
    ]:
        conn.execute(
            "INSERT INTO quarantine(source_table, source_id, reason, quarantined_at, data_backup, auto_delete_after, regeneration_status) "
            "VALUES ('triples', ?, 'r', ?, '{}', ?, ?)",
            (source_id, datetime.now(timezone.utc).isoformat(), expired, status),
        )
    conn.commit()

    purged = manager.purge_expired_quarantine(conn)
    remaining = {r[0] for r in conn.execute("SELECT source_id FROM quarantine").fetchall()}
    conn.close()

    assert purged == 2
    assert remaining == {"p-pending", "p-failed"}


def test_reopen_includes_permanent_failed_and_resets_attempts(tmp_path):
    db_path = tmp_path / "quality_reopen.db"
    manager = RAGQualityManager(db_path=str(db_path), reranker=_FakeReranker())
    manager.ensure_quality_schema()

    conn = sqlite3.connect(db_path)
    old_ts = "2020-01-01T00:00:00+00:00"
    conn.execute(
        "INSERT INTO quarantine(source_table, source_id, reason, quarantined_at, data_backup, regeneration_status, regeneration_attempts) "
        "VALUES ('triples', 'ro-1', 'r', ?, '{}', 'permanent_failed', 3)",
        (old_ts,),
    )
    conn.execute(
        "INSERT INTO quarantine(source_table, source_id, reason, quarantined_at, data_backup, regeneration_status, regeneration_attempts) "
        "VALUES ('triples', 'ro-2', 'r', ?, '{}', 'completed', 1)",
        (old_ts,),
    )
    conn.commit()

    reopened = manager.reopen_aged_quarantine(conn, min_age_days=30, limit=10)
    rows = conn.execute(
        "SELECT source_id, regeneration_status, regeneration_attempts FROM quarantine ORDER BY source_id"
    ).fetchall()
    conn.close()

    assert reopened == 2
    for _, status, attempts in rows:
        assert status == "pending"
        assert attempts == 0