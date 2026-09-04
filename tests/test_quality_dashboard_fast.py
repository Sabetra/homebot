"""
Fast unit tests for RAG Quality Dashboard (SOTA v2) — no LLM/Reranker needed.

Verifies:
1. QualityManager.run_structural_audit() returns valid AuditReport
2. QualityManager.run_remediation(dry_run=True) returns valid stats
3. QualityManager.get_db_health_stats() returns valid dict
4. _score_hypothesis fallback works when reranker has no predict method
5. Schema migration includes inferred_source_chunk_id column
"""
import json
import sqlite3
import sys
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

# Ensure project root is on path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.rag_store.core.quality import RAGQualityManager as QualityManager, AuditReport


def _test_db_path() -> Path:
    """Return a temporary test database path — unique per test class to avoid file locks."""
    import hashlib
    import inspect
    # Use a hash of the calling frame's class name to create unique DB paths
    try:
        # Walk up the stack to find the test class
        for frame_info in inspect.stack()[1:]:
            frame_locals = frame_info.frame.f_locals
            if 'self' in frame_locals:
                cls_name = type(frame_locals['self']).__name__
                break
        else:
            cls_name = "default"
    except Exception:
        cls_name = "default"
    suffix = hashlib.md5(cls_name.encode()).hexdigest()[:8]
    return Path(__file__).resolve().parent.parent / "data" / f"chatbot_rag_test_quality_{suffix}.db"


def _create_empty_test_db() -> sqlite3.Connection:
    """Create an empty test DB with the full RAG schema."""
    db_path = _test_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    # Run the full schema initialization
    from agent.rag_store.core.database import DatabaseManager
    from agent.rag_store.core.quality import RAGQualityManager
    dbm = DatabaseManager(db_path=str(db_path))
    dbm.ensure_schema()
    # Also ensure quality-specific tables
    qm = RAGQualityManager(db_path=str(db_path))
    qm.ensure_quality_schema()
    return conn


class TestQualityManagerStructuralAudit:
    """Test that structural audit runs without errors on a fresh DB."""

    def setup_method(self):
        self.conn = _create_empty_test_db()
        self.qm = QualityManager(db_path=str(_test_db_path()))

    def teardown_method(self):
        self.conn.close()
        # Clean up test DB
        db_path = _test_db_path()
        if db_path.exists():
            db_path.unlink()

    def test_structural_audit_returns_report(self):
        """run_structural_audit must return an AuditReport with valid fields."""
        report = self.qm.run_structural_audit()
        assert isinstance(report, AuditReport)
        assert report.total_chunks == 0
        assert report.total_triples == 0
        assert report.duration_ms >= 0

    def test_structural_audit_with_data(self):
        """Audit on a DB with real chunks/triples returns correct counts."""
        conn = self.conn
        # Insert a test document (schema: documents(doc_id TEXT PRIMARY KEY))
        conn.execute("INSERT OR IGNORE INTO documents(doc_id) VALUES (?)", ("test_doc_1",))
        # Insert test chunks (embedding BLOB NOT NULL — provide dummy bytes)
        dummy_embedding = b'\x00' * 384
        conn.execute(
            "INSERT INTO chunks(doc_id, chunk_id, text, metadata, embedding) VALUES (?, ?, ?, ?, ?)",
            ("test_doc_1", 1, "This is a meaningful test chunk about artificial intelligence.", "{}", dummy_embedding),
        )
        conn.execute(
            "INSERT INTO chunks(doc_id, chunk_id, text, metadata, embedding) VALUES (?, ?, ?, ?, ?)",
            ("test_doc_1", 2, "Another chunk about machine learning and neural networks.", "{}", dummy_embedding),
        )
        # Insert a test triple
        conn.execute(
            "INSERT INTO triples(doc_id, subject, predicate, object, metadata, triple_hash) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("test_doc_1", "AI", "is_a", "Technology", "{}", "hash123"),
        )
        conn.commit()

        report = self.qm.run_structural_audit()
        assert report.total_chunks == 2
        assert report.total_triples == 1
        assert report.orphan_chunks == 0
        assert report.orphan_triples == 0

    def test_health_stats_returns_dict(self):
        """get_db_health_stats must return a dict with expected keys."""
        stats = self.qm.get_db_health_stats()
        assert isinstance(stats, dict)
        assert "total_chunks" in stats
        assert "total_triples" in stats
        assert "total_documents" in stats
        assert "quarantine" in stats


class TestQualityManagerRemediationDryRun:
    """Test remediation dry_run returns a valid plan without modifying DB."""

    def setup_method(self):
        self.conn = _create_empty_test_db()
        self.qm = QualityManager(db_path=str(_test_db_path()))
        # Insert an orphan chunk: temporarily disable FK so chunk has no parent doc.
        # This is intentional — we want to test orphan detection by remediation engine.
        self.conn.execute("PRAGMA foreign_keys=OFF")
        dummy_embedding = b'\x00' * 384
        self.conn.execute(
            "INSERT INTO chunks(doc_id, chunk_id, text, metadata, embedding) VALUES (?, ?, ?, ?, ?)",
            ("orphan_doc", 1, "This chunk has no parent document.", "{}", dummy_embedding),
        )
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.commit()

    def teardown_method(self):
        # Close QualityManager's internal connection first to release file lock
        if hasattr(self.qm, "conn"):
            self.qm.conn.close()
        if hasattr(self.qm, "_conn"):
            self.qm._conn.close()
        self.conn.close()
        db_path = _test_db_path()
        if db_path.exists():
            db_path.unlink()

    def test_dry_run_returns_plan(self):
        """dry_run=True must return a plan without modifying the DB."""
        stats = self.qm.run_remediation(
            dry_run=True,
            quarantine_orphans=True,
        )
        assert stats["dry_run"] is True
        assert "plan" in stats
        assert isinstance(stats["plan"], list)
        # Orphan chunk should be in the plan
        found_orphan = any(
            p.get("reason") == "orphan_no_parent_document"
            for p in stats["plan"]
        )
        assert found_orphan, f"Expected orphan quarantine in plan: {stats['plan']}"

    def test_dry_run_does_not_modify_db(self):
        """After dry_run, chunk count must be unchanged."""
        _ = self.qm.run_remediation(dry_run=True, quarantine_orphans=True)
        cur = self.conn.cursor()
        cur.execute("SELECT COUNT(*) FROM chunks")
        count = cur.fetchone()[0]
        assert count == 1, "dry_run must not delete chunks"


class TestHypothesisScoreFallback:
    """Test that _score_hypothesis_against_chunk works even when reranker lacks predict."""

    def test_fallback_returns_zero(self):
        """When reranker has no predict method, fallback returns 0.0."""
        qm = QualityManager(db_path=str(_test_db_path()))
        # Create a mock reranker without predict
        class DummyReranker:
            pass
        dummy = DummyReranker()
        result = qm._score_hypothesis_against_chunk(dummy, "test hypothesis", "test passage")
        assert result == 0.0
        assert isinstance(result, float)


class TestSchemaMigration:
    """Test that the DB schema includes inferred_source_chunk_id."""

    def setup_method(self):
        self.conn = _create_empty_test_db()

    def teardown_method(self):
        self.conn.close()
        db_path = _test_db_path()
        if db_path.exists():
            db_path.unlink()

    def test_triple_quality_has_inferred_source_chunk_id(self):
        """The triple_quality table must have the inferred_source_chunk_id column."""
        cur = self.conn.cursor()
        cur.execute("PRAGMA table_info(triple_quality)")
        columns = [row[1] for row in cur.fetchall()]
        assert "inferred_source_chunk_id" in columns, (
            f"Missing column 'inferred_source_chunk_id' in triple_quality. "
            f"Columns: {columns}"
        )

    def test_triple_quality_has_all_sota_v3_columns(self):
        """Verify all SOTA v3 columns exist in triple_quality."""
        cur = self.conn.cursor()
        cur.execute("PRAGMA table_info(triple_quality)")
        columns = [row[1] for row in cur.fetchall()]
        required = [
            "triple_id", "grounding_score", "predicate_info_value",
            "inferred_source_chunk_id", "is_contradicted", "contradicts_triple_id",
            "canonical_subject", "canonical_object", "last_verified",
            "action_taken",
        ]
        missing = [c for c in required if c not in columns]
        assert not missing, f"Missing columns in triple_quality: {missing}"


class TestChunkQualityScoring:
    """Test chunk structural scoring edge cases."""

    def setup_method(self):
        self.qm = QualityManager(db_path=str(_test_db_path()))

    def test_empty_text_scores_low(self):
        """Empty text should get a low structural score."""
        score = self.qm.score_chunk_structural("", "{}")
        assert score < 0.5

    def test_meaningful_text_scores_higher(self):
        """Meaningful text should score higher than empty."""
        empty_score = self.qm.score_chunk_structural("", "{}")
        good_score = self.qm.score_chunk_structural(
            "Artificial intelligence is a branch of computer science that focuses on "
            "building intelligent machines capable of learning and reasoning.",
            "{}"
        )
        assert good_score > empty_score

    def test_defect_detection_cookie_banner(self):
        """Cookie banners should be detected as defects.
        
        The detector requires >= 3 cookie-related keywords. The test text must
        contain enough matches (cookie, datenschutz, privacy policy, etc.).
        """
        defects = self.qm.detect_defects(
            "Wir verwenden Cookies, um Ihre Erfahrung zu verbessern. "
            "Durch die Nutzung unserer Website stimmen Sie der Verwendung von Cookies zu. "
            "Cookie-Einstellungen können Sie jederzeit ändern. "
            "Privacy Policy und Datenschutz finden Sie unten. "
            "Klicken Sie auf 'Accept All' oder 'Ablehnen'.",
            "{}"
        )
        assert "cookie_banner" in defects, f"Expected 'cookie_banner' in defects: {defects}"

    def test_content_type_detection(self):
        """Prose text should be detected as prose."""
        ct = self.qm.detect_content_type(
            "This is a normal paragraph of text with meaningful content.",
            "{}"
        )
        assert ct == "prose"


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v", "--no-header", "-p", "no:cacheprovider"])