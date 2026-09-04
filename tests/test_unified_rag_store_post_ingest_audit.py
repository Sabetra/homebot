import sqlite3
from datetime import datetime, timezone

from agent.unified_rag_store import UnifiedRagStore


def test_post_ingest_audit_handles_default_sqlite_rows(tmp_path):
    db_path = tmp_path / "audit.db"
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute(
        "CREATE TABLE chunk_quality (doc_id TEXT, chunk_id INTEGER, structural_score REAL, content_type TEXT, defect_flags TEXT, last_checked TEXT)"
    )
    cur.execute("CREATE TABLE triples (triple_id INTEGER PRIMARY KEY, doc_id TEXT)")
    cur.execute(
        "CREATE TABLE triple_quality (triple_id INTEGER PRIMARY KEY, grounding_score REAL, last_verified TEXT)"
    )
    cur.execute(
        "CREATE TABLE quality_audit_log (action TEXT, details TEXT, timestamp TEXT)"
    )
    cur.execute(
        "INSERT INTO chunk_quality VALUES (?, ?, ?, ?, ?, ?)",
        ("doc-1", 7, 0.2, "text/plain", "", datetime.now(timezone.utc).isoformat()),
    )
    cur.execute("INSERT INTO triples VALUES (?, ?)", (101, "doc-1"))
    conn.commit()

    store = UnifiedRagStore.__new__(UnifiedRagStore)
    store.debug = False
    store.get_connection = lambda: conn
    store.return_connection = lambda _conn: None

    stats = store._run_post_ingest_audit(
        [("doc-1", 7, "hello", "{}", b"emb", "general", "safe", "v1")],
        ["doc-1"],
        {"triples": 1},
    )

    assert stats["chunks_audited"] == 1
    assert stats["quality_warnings"] == 1
    assert stats["triples_queued_for_grounding"] == 1
    assert conn.execute("SELECT grounding_score FROM triple_quality").fetchone()[0] == -1.0
    assert conn.execute("SELECT COUNT(*) FROM quality_audit_log").fetchone()[0] == 1
