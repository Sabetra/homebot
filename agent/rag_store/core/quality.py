"""
RAG Quality Management — SOTA Data Quality Module
===================================================

Provides:
    1. Schema extensions for quality tracking (chunk_quality, triple_quality,
       quarantine, quality_audit_log, retrieval_feedback)
    2. Structural chunk quality scoring (no LLM needed)
    3. Near-duplicate detection via embedding cosine similarity
    4. Cross-document boilerplate detection via MinHash / Jaccard
    5. KG triple verification via cross-encoder reranker (non-circular)
    6. Predicate information-value scoring
    7. Staleness detection
    8. Orphan detection
    9. Full DB audit orchestrator (manual trigger)
   10. Remediation engine (quarantine, merge, delete with backup)
   11. Retrieval feedback tracking & chunk utility scoring
   12. Evaluation metrics (Precision@k stub, KG grounding rate, etc.)

Design:
    - All scans are *on-demand* (no scheduler, no background threads)
    - Structural scans never touch the LLM
    - Reranker-based verification uses the already-loaded cross-encoder
    - Every destructive action goes through quarantine first (reversible)

Author: SOTA RAG Quality System
Date: 2026-03-23
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import re
import sqlite3
import time
import base64
from collections import Counter, defaultdict
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

import numpy as np

logger = logging.getLogger(__name__)


def _canonical_reranker(reranker: Any) -> Any:
    """Return the canonical cross-encoder instance for scoring calls.

    Some call sites may pass lightweight wrappers. If a dedicated reranker
    object is embedded under a `reranker` attribute, prefer that; otherwise
    use the object directly.
    """
    if reranker is None:
        return None
    return getattr(reranker, "reranker", reranker)


_URL_PATTERN = re.compile(r"(?:https?://|www\.)\S+", re.IGNORECASE)
_BOILERPLATE_PATTERNS = re.compile(
    r"(?:"
    r"cookie|privacy\s*policy|datenschutz|terms\s+of\s+service|impressum|"
    r"all\s+rights\s+reserved|copyright|sign\s+in|log\s+in|menu|navigation|"
    r"kontakt|contact|about\s+us|über\s+uns"
    r")",
    re.IGNORECASE,
)
_NAV_SEPARATOR_PATTERN = re.compile(
    r"(?:\s[|/›>»•·]\s){2,}|(?:home|start|startseite)\s*(?:[|/›>»•·])",
    re.IGNORECASE,
)
_GENERIC_PREDICATES: Dict[str, float] = {
    "is": 0.01,
    "ist": 0.01,
    "was": 0.01,
    "war": 0.01,
    "are": 0.01,
    "sind": 0.01,
    "has": 0.02,
    "hat": 0.02,
    "have": 0.02,
    "contains": 0.04,
    "enthält": 0.04,
    "related to": 0.04,
    "bezieht sich auf": 0.04,
    "mentions": 0.05,
    "erwähnt": 0.05,
    "about": 0.05,
}


def _parse_iso_datetime(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    text = str(value).strip()
    if not text:
        return None

    text = text.replace("Z", "+00:00")
    parsed: Optional[datetime] = None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                parsed = datetime.strptime(text, fmt)
                break
            except ValueError:
                parsed = None
        if parsed is None:
            return None

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


@dataclass
class AuditEntry:
    action: str
    target_table: str = ""
    target_id: str = ""
    details: str = ""
    score_before: Optional[float] = None
    score_after: Optional[float] = None


@dataclass
class AuditReport:
    timestamp: str
    total_chunks: int = 0
    total_triples: int = 0
    orphan_chunks: int = 0
    orphan_triples: int = 0
    embedding_dim_mismatch: int = 0
    short_chunks: int = 0
    url_dump_chunks: int = 0
    boilerplate_chunks: int = 0
    near_duplicates: int = 0
    low_quality_chunks: int = 0
    defect_chunks: int = 0
    generic_predicate_triples: int = 0
    regex_fallback_triples: int = 0
    ungrounded_triples: int = 0
    age_distribution: Dict[str, int] = field(default_factory=dict)
    content_type_distribution: Dict[str, int] = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)
    duration_ms: float = 0.0

    def summary(self) -> str:
        return (
            f"audit chunks={self.total_chunks}, triples={self.total_triples}, "
            f"orphans={self.orphan_chunks + self.orphan_triples}, "
            f"duplicates={self.near_duplicates}, low_quality={self.low_quality_chunks}, "
            f"errors={len(self.errors)}, duration_ms={self.duration_ms:.1f}"
        )


class RAGQualityManager:
    def __init__(
        self,
        db_path: str = "rag_store.db",
        reranker: Optional[Any] = None,
        expected_embedding_dim: int = 1024,
    ) -> None:
        self.db_path = db_path
        self._reranker = reranker
        self.expected_embedding_dim = expected_embedding_dim

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def ensure_quality_schema(self, conn: Optional[sqlite3.Connection] = None) -> None:
        own_conn = conn is None
        if own_conn:
            conn = self._get_connection()

        assert conn is not None
        try:
            self._ensure_core_tables(conn)
            cur = conn.cursor()

            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS chunk_quality (
                    doc_id TEXT NOT NULL,
                    chunk_id INTEGER NOT NULL,
                    structural_score REAL DEFAULT 0.0,
                    content_type TEXT DEFAULT 'prose',
                    defect_flags TEXT DEFAULT '',
                    last_checked TEXT,
                    action_taken TEXT DEFAULT 'none',
                    PRIMARY KEY (doc_id, chunk_id)
                )
                """
            )

            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS triple_quality (
                    triple_id INTEGER PRIMARY KEY,
                    grounding_score REAL DEFAULT -1,
                    predicate_info_value REAL DEFAULT 0.0,
                    inferred_source_chunk_id INTEGER,
                    is_contradicted INTEGER DEFAULT 0,
                    contradicts_triple_id INTEGER,
                    canonical_subject TEXT,
                    canonical_object TEXT,
                    last_verified TEXT,
                    action_taken TEXT DEFAULT 'none'
                )
                """
            )

            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS quarantine (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_table TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    quarantined_at TEXT NOT NULL,
                    data_backup TEXT NOT NULL,
                    auto_delete_after TEXT,
                    regeneration_status TEXT DEFAULT 'pending',
                    regeneration_attempts INTEGER DEFAULT 0,
                    last_regeneration_at TEXT,
                    last_regeneration_error TEXT
                )
                """
            )

            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS quality_audit_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    action TEXT NOT NULL,
                    target_table TEXT DEFAULT '',
                    target_id TEXT DEFAULT '',
                    details TEXT DEFAULT '',
                    score_before REAL,
                    score_after REAL
                )
                """
            )

            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS retrieval_feedback (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    query TEXT NOT NULL,
                    chunk_ids TEXT NOT NULL,
                    chunk_scores TEXT NOT NULL,
                    answer_excerpt TEXT DEFAULT '',
                    user_feedback INTEGER DEFAULT 0
                )
                """
            )

            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS quality_audit_trend (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    audit_timestamp TEXT NOT NULL,
                    metric_name TEXT NOT NULL,
                    metric_value REAL NOT NULL,
                    delta_from_previous REAL DEFAULT 0
                )
                """
            )

            try:
                cur.execute("ALTER TABLE triples ADD COLUMN contradiction_state TEXT DEFAULT 'none'")
            except sqlite3.OperationalError:
                pass
            try:
                cur.execute("ALTER TABLE triples ADD COLUMN evidence_strength REAL DEFAULT 0.5")
            except sqlite3.OperationalError:
                pass
            try:
                cur.execute("ALTER TABLE triples ADD COLUMN validity_type TEXT DEFAULT 'atemporal'")
            except sqlite3.OperationalError:
                pass
            try:
                cur.execute("ALTER TABLE triples ADD COLUMN valid_from TEXT")
            except sqlite3.OperationalError:
                pass
            try:
                cur.execute("ALTER TABLE triples ADD COLUMN valid_to TEXT")
            except sqlite3.OperationalError:
                pass
            try:
                cur.execute("ALTER TABLE triples ADD COLUMN observed_at TEXT")
            except sqlite3.OperationalError:
                pass

            cur.execute("CREATE INDEX IF NOT EXISTS idx_chunk_quality_score ON chunk_quality(structural_score)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_chunk_quality_action ON chunk_quality(action_taken)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_triple_quality_grounding ON triple_quality(grounding_score)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_triple_quality_predicate ON triple_quality(predicate_info_value)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_quarantine_status ON quarantine(source_table, regeneration_status, quarantined_at)")
            # Expression index: O(log n) duplicate checks on backed-up triple hashes.
            # json_valid guard keeps legacy non-JSON backups insertable.
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_quarantine_triple_hash "
                "ON quarantine(json_extract(data_backup, '$.triple_hash')) "
                "WHERE source_table = 'triples' AND json_valid(data_backup)"
            )
            cur.execute("CREATE INDEX IF NOT EXISTS idx_quality_audit_log_time ON quality_audit_log(timestamp)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_quality_audit_trend_metric ON quality_audit_trend(metric_name, audit_timestamp)")
            conn.commit()
        finally:
            if own_conn:
                conn.close()

    def _ensure_core_tables(self, conn: sqlite3.Connection) -> None:
        """Ensure core RAG tables exist (documents, chunks, triples).
        
        These are normally created by DatabaseManager.ensure_schema(),
        but get_db_health_stats() may run before DatabaseManager is
        instantiated. Creating them here avoids 'no such table' errors.
        """
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS documents (
                doc_id TEXT PRIMARY KEY
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS chunks (
                doc_id TEXT NOT NULL,
                chunk_id INTEGER NOT NULL,
                text TEXT NOT NULL,
                metadata TEXT NOT NULL,
                embedding BLOB NOT NULL,
                PRIMARY KEY (doc_id, chunk_id),
                FOREIGN KEY (doc_id) REFERENCES documents(doc_id) ON DELETE CASCADE
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS triples (
                triple_id INTEGER PRIMARY KEY AUTOINCREMENT,
                doc_id TEXT NOT NULL,
                page INTEGER,
                table_id INTEGER,
                subject TEXT,
                predicate TEXT,
                object TEXT,
                metadata TEXT,
                triple_hash TEXT,
                source_chunk_id INTEGER
            )
        """)
        try:
            cur.execute("ALTER TABLE triples ADD COLUMN source_chunk_id INTEGER")
        except sqlite3.OperationalError:
            pass
        cur.execute("""
            CREATE TABLE IF NOT EXISTS kg_entities (
                entity_id INTEGER PRIMARY KEY AUTOINCREMENT,
                entity_text TEXT NOT NULL UNIQUE,
                normalized_text TEXT NOT NULL,
                entity_type TEXT DEFAULT 'entity',
                frequency INTEGER DEFAULT 1,
                first_seen_doc_id TEXT,
                embedding BLOB
            )
        """)
        conn.commit()

    # ─── Audit Log ────────────────────────────────────────────────

    def _log_audit(
        self, conn: sqlite3.Connection, entry: AuditEntry
    ) -> None:
        conn.execute(
            "INSERT INTO quality_audit_log(timestamp, action, target_table, target_id, details, score_before, score_after) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                datetime.now(timezone.utc).isoformat(),
                entry.action,
                entry.target_table,
                entry.target_id,
                entry.details,
                entry.score_before,
                entry.score_after,
            ),
        )

    def _resolve_quarantine_regeneration_source(
        self,
        cur: sqlite3.Cursor,
        backup: Dict[str, Any],
        reranker: Optional[Any] = None,
    ) -> Tuple[str, Optional[int], Optional[str], str]:
        """Resolve the source chunk for a quarantined triple.

        Returns:
            (resolved_doc_id, resolved_chunk_id, chunk_text, resolution_kind)
            where resolution_kind describes which provenance path succeeded
            (or ``missing`` when no usable chunk text could be recovered).
        """
        doc_id = str(backup.get("doc_id", "") or "").strip()
        hypothesis = (
            f"{str(backup.get('subject', '') or '').strip()} "
            f"{str(backup.get('predicate', '') or '').strip()} "
            f"{str(backup.get('object', '') or '').strip()}"
        ).strip()

        def _pick_best_with_reranker(candidates: List[sqlite3.Row]) -> Optional[sqlite3.Row]:
            if reranker is None or not hypothesis:
                return None
            best_row: Optional[sqlite3.Row] = None
            best_score = float("-inf")
            for row in candidates:
                text = str(row["text"] or "")
                if len(text.strip()) < 50:
                    continue
                score = self._score_hypothesis_against_chunk(reranker, hypothesis, text)
                if score > best_score:
                    best_score = score
                    best_row = row
            return best_row
        source_chunk_id = (
            backup.get("source_chunk_id")
            or backup.get("inferred_source_chunk_id")
            or backup.get("resolved_source_chunk_id")
        )

        if source_chunk_id is not None:
            try:
                source_chunk_id = int(source_chunk_id)
            except (TypeError, ValueError):
                source_chunk_id = None

        if doc_id and source_chunk_id is not None:
            cur.execute(
                "SELECT text FROM chunks WHERE doc_id = ? AND chunk_id = ?",
                (doc_id, source_chunk_id),
            )
            chunk_row = cur.fetchone()
            if chunk_row and chunk_row["text"]:
                return doc_id, source_chunk_id, chunk_row["text"], "direct_source_chunk"

        if doc_id and reranker is not None and hypothesis:
            cur.execute(
                "SELECT chunk_id, text FROM chunks WHERE doc_id = ? ORDER BY chunk_id",
                (doc_id,),
            )
            candidate_chunks = cur.fetchall()
            best_chunk = _pick_best_with_reranker(candidate_chunks)
            if best_chunk and best_chunk["text"]:
                return (
                    doc_id,
                    int(best_chunk["chunk_id"]),
                    best_chunk["text"],
                    "doc_semantic_reranker",
                )

        source_hint = backup.get("source_hint")
        if source_hint:
            cur.execute(
                "SELECT doc_id, chunk_id, text FROM chunks "
                "WHERE json_extract(metadata, '$.source') = ? "
                "   OR json_extract(metadata, '$.filename') = ? "
                "ORDER BY doc_id, chunk_id",
                (source_hint, source_hint),
            )
            provenance_rows = cur.fetchall()
            if provenance_rows:
                preferred = None
                if source_chunk_id is not None:
                    for prow in provenance_rows:
                        if int(prow["chunk_id"]) == int(source_chunk_id):
                            preferred = prow
                            break
                if preferred is None:
                    preferred = _pick_best_with_reranker(provenance_rows)
                if preferred is None:
                    preferred = provenance_rows[0]
                if preferred and preferred["text"]:
                    return (
                        str(preferred["doc_id"]),
                        int(preferred["chunk_id"]),
                        preferred["text"],
                        "source_hint",
                    )

        if doc_id:
            basename = os.path.basename(str(doc_id)).strip()
            if basename:
                cur.execute(
                    "SELECT DISTINCT doc_id FROM chunks "
                    "WHERE doc_id LIKE ? OR doc_id LIKE ? "
                    "ORDER BY LENGTH(doc_id) ASC "
                    "LIMIT 20",
                    (f"%{basename}", f"%{basename}%"),
                )
                alt_doc_ids = [r[0] for r in cur.fetchall() if r and r[0]]
                if alt_doc_ids:
                    best_chunk = None
                    best_doc_id = None
                    semantic_candidates: List[sqlite3.Row] = []
                    for alt_doc_id in alt_doc_ids:
                        cur.execute(
                            "SELECT doc_id, chunk_id, text FROM chunks WHERE doc_id = ? ORDER BY chunk_id",
                            (alt_doc_id,),
                        )
                        candidate_chunks = cur.fetchall()
                        if source_chunk_id is not None:
                            for cc in candidate_chunks:
                                if int(cc["chunk_id"]) == int(source_chunk_id):
                                    best_chunk = cc
                                    best_doc_id = alt_doc_id
                                    break
                        if best_chunk is not None:
                            break
                        for cc in candidate_chunks:
                            cc_text = str(cc["text"] or "")
                            if len(cc_text.strip()) >= 50:
                                semantic_candidates.append(cc)

                    if best_chunk is None and semantic_candidates and reranker is not None and hypothesis:
                        best_chunk = _pick_best_with_reranker(semantic_candidates)
                        if best_chunk is not None:
                            best_doc_id = str(best_chunk["doc_id"])

                    if best_chunk and best_chunk["text"] and best_doc_id is not None:
                        return (
                            best_doc_id,
                            int(best_chunk["chunk_id"]),
                            best_chunk["text"],
                            "doc_basename_semantic",
                        )

        return doc_id, source_chunk_id if isinstance(source_chunk_id, int) else None, None, "missing"

    def _score_hypothesis_against_chunk(
        self,
        reranker: Any,
        hypothesis: str,
        chunk_text: str,
    ) -> float:
        """Score a hypothesis against a chunk using the reranker.

        Falls back to 0.0 when the reranker lacks a callable ``rerank`` method
        (e-orthe user runs without a reranker installed).  This avoids
        ``AttributeError`` at runtime.
        """
        if not hasattr(reranker, "rerank") or not callable(reranker.rerank):
            return 0.0

        try:
            rerank_result: Any = reranker.rerank(
                query=hypothesis,
                passages=[{"text": chunk_text}],
                top_k=1,
                text_key="text",
            )
        except Exception:
            return 0.0

        if not rerank_result:
            return 0.0

        best = rerank_result[0]
        raw_score = best.get("rerank_score", best.get("score"))
        if raw_score is None:
            return 0.0
        return float(raw_score)

    @staticmethod
    def _lexical_grounding_ok(subject: str, obj: str, chunk_text: str) -> bool:
        """Cheap second grounding signal: entity tokens must appear in the chunk.

        Guards against reranker over-scoring topically similar but factually
        unsupported triples. Requires at least one content token of BOTH the
        subject and the object to occur in the chunk (case-insensitive).
        """
        text_lower = (chunk_text or "").lower()

        def _any_token_present(entity: str) -> bool:
            tokens = [t for t in re.findall(r"\w+", (entity or "").lower()) if len(t) >= 3]
            if not tokens:
                return True  # nothing checkable — don't block
            return any(t in text_lower for t in tokens)

        return _any_token_present(subject) and _any_token_present(obj)

    # =================================================================
    # ★ SOTA v2: CONTENT-TYPE DETECTION
    # =================================================================

    # Patterns for content-type classification
    _FORMULA_PATTERN = re.compile(
        r'(?:'
        r'[=≈≠≤≥∑∏∫∂√∞±×÷∈∉⊂⊃∪∩∧∨¬∀∃]'
        r'|\b(?:sin|cos|tan|log|ln|exp|lim|inf|sup|max|min|arg)\s*\('
        r'|\b\d+\s*[+\-*/^]\s*\d+'
        r'|\b[a-z]\s*=\s*[a-z0-9+\-*/^()\s]+'
        r'|\$[^$]+\$'
        r'|\\(?:frac|sqrt|sum|prod|int|partial|alpha|beta|gamma|delta|epsilon|theta|lambda|mu|sigma|omega)\b'
        r')',
        re.IGNORECASE
    )
    _TABLE_PATTERN = re.compile(r'(?:\|[^|]+){2,}\||\t[^\t]+\t')
    _CODE_PATTERN = re.compile(
        r'(?:'
        r'(?:def|class|function|return|import|from|if|else|for|while|try|except)\s'
        r'|(?:public|private|static|void|int|string|bool)\s'
        r'|\{[^}]*\}'
        r'|(?:=>|->|::|\.\.|\.\.\.)'
        r')',
        re.IGNORECASE
    )
    _DEFINITION_PATTERN = re.compile(
        r'(?:'
        r'\b(?:Definition|Def\.|Satz|Theorem|Lemma|Korollar|Axiom|Postulat)\s*[:\d.]'
        r'|\b(?:bezeichnet|definiert als|heißt|ist definiert|wird definiert|means|is defined as|refers to)\b'
        r')',
        re.IGNORECASE
    )
    _LEGAL_PATTERN = re.compile(
        r'(?:'
        r'§\s*\d+'
        r'|\b(?:Abs\.|Absatz|Artikel|Art\.)\s*\d+'
        r'|\b(?:gemäß|laut|nach Maßgabe|vorbehaltlich|unbeschadet)\b'
        r'|\b(?:pursuant to|in accordance with|notwithstanding|hereinafter)\b'
        r')',
        re.IGNORECASE
    )

    @staticmethod
    def detect_content_type(text: str, metadata_json: str = "{}") -> str:
        """
        Classify chunk content type. This affects how structural scoring
        and noise filtering treat the chunk.
        
        Returns one of: 'prose', 'formula', 'table', 'code', 'definition', 'legal'
        """
        if not text or len(text.strip()) < 5:
            return "prose"
        
        text_stripped = text.strip()
        
        # Check metadata hints first
        try:
            meta = json.loads(metadata_json) if isinstance(metadata_json, str) else metadata_json
            source_type = meta.get("source_type", "")
            if source_type in ("code", "programming"):
                return "code"
        except (json.JSONDecodeError, TypeError, AttributeError):
            pass
        
        # Count pattern matches
        formula_hits = len(RAGQualityManager._FORMULA_PATTERN.findall(text_stripped))
        table_hits = len(RAGQualityManager._TABLE_PATTERN.findall(text_stripped))
        code_hits = len(RAGQualityManager._CODE_PATTERN.findall(text_stripped))
        definition_hits = len(RAGQualityManager._DEFINITION_PATTERN.findall(text_stripped))
        legal_hits = len(RAGQualityManager._LEGAL_PATTERN.findall(text_stripped))
        
        words = re.findall(r'\b\w+\b', text_stripped)
        word_count = max(len(words), 1)
        
        # Normalise hits by text length
        formula_density = formula_hits / word_count
        
        # Decision: highest signal wins, with minimum thresholds
        if formula_hits >= 3 or formula_density > 0.05:
            return "formula"
        if table_hits >= 2:
            return "table"
        if definition_hits >= 1 and len(text_stripped) < 500:
            return "definition"
        if legal_hits >= 2:
            return "legal"
        if code_hits >= 3:
            return "code"
        
        return "prose"

    # =================================================================
    # ★ SOTA v2: DEFECT FLAG DETECTION (binary flags, not scores)
    # =================================================================

    _ENCODING_GARBAGE_PATTERN = re.compile(
        r'[\x00-\x08\x0b\x0c\x0e-\x1f]|'
        r'[ïïÂ]{2,}|â€[™œ"|Ã¤Ã¶Ã¼Ãœ|Â§Â°|'
        r'\\u[0-9a-f]{4}(?:\\u[0-9a-f]{4}){3,}',
        re.IGNORECASE
    )

    @staticmethod
    def detect_defects(text: str, metadata_json: str = "{}") -> Set[str]:
        """
        Detect binary defect flags for a chunk. These are HARD problems,
        not soft quality signals. A chunk can have zero or more defects.
        
        Defect flags:
          - encoding_garbage: Mojibake, control chars, broken Unicode
          - cookie_banner: Cookie consent / privacy policy text
          - pure_navigation: Only nav links / breadcrumbs / sidebar
          - url_dump: Predominantly URLs with no prose
          - trivial: Empty or near-empty content (<15 chars after strip)
        
        Returns a set of defect flag strings.
        """
        flags: Set[str] = set()
        
        if not text:
            flags.add("trivial")
            return flags
        
        text_stripped = text.strip()
        text_len = len(text_stripped)
        
        if text_len < 15:
            flags.add("trivial")
            return flags
        
        # Encoding garbage
        garbage_hits = len(RAGQualityManager._ENCODING_GARBAGE_PATTERN.findall(text_stripped))
        if garbage_hits >= 3 or (garbage_hits >= 1 and text_len < 100):
            flags.add("encoding_garbage")
        
        # Cookie banner / privacy policy
        cookie_keywords = len(re.findall(
            r'(?i)\b(?:cookie|datenschutz|privacy\s*policy|wir\s*verwenden\s*cookies|'
            r'we\s*use\s*cookies|akzeptieren|accept\s*all|ablehnen|reject|'
            r'einstellungen\s*speichern|save\s*preferences|cookie\s*(?:einstellungen|settings|preferences))\b',
            text_stripped
        ))
        if cookie_keywords >= 3:
            flags.add("cookie_banner")
        
        # Pure navigation
        nav_indicators = 0
        nav_seps = len(re.findall(r'[\|•·/]{2,}|(?:\s[\|•·/]\s){3,}', text_stripped))
        if nav_seps >= 2:
            nav_indicators += 2
        breadcrumb = len(re.findall(r'(?:home|start|kontakt|contact|about|über\s*uns)\s*[\|•·/>]', text_stripped, re.IGNORECASE))
        if breadcrumb >= 2:
            nav_indicators += 2
        link_list = len(re.findall(r'(?:^|\n)\s*(?:[\•\-\*]|\d+\.)\s*\[?https?://', text_stripped, re.IGNORECASE))
        if link_list >= 3:
            nav_indicators += 1
        # Check for sentence content — navigation lacks sentences
        sentence_endings = len(re.findall(r'[.!?]\s+[A-ZÄÖÜ]', text_stripped))
        if sentence_endings == 0 and nav_indicators >= 2:
            flags.add("pure_navigation")
        
        # URL dump
        urls = _URL_PATTERN.findall(text_stripped)
        url_char_len = sum(len(u) for u in urls)
        url_ratio = url_char_len / max(text_len, 1)
        if url_ratio > 0.6:
            flags.add("url_dump")
        
        return flags

    # =================================================================
    # ★ SOTA v2: AGE DISTRIBUTION (replaces staleness penalty)
    # =================================================================

    def get_age_distribution(self, conn: sqlite3.Connection) -> Dict[str, int]:
        """
        Get age distribution of chunks (informational only, NOT a quality signal).
        Historical data is valuable regardless of age.
        
        Returns buckets: {'<7d': N, '7-30d': N, '30-90d': N, '90d-1y': N, '>1y': N, 'unknown': N}
        """
        cur = conn.cursor()
        cur.execute("SELECT metadata FROM chunks")
        now = datetime.now(timezone.utc)
        
        buckets = {"<7d": 0, "7-30d": 0, "30-90d": 0, "90d-1y": 0, ">1y": 0, "unknown": 0}
        
        for row in cur.fetchall():
            try:
                meta = json.loads(row["metadata"] or "{}")
                extracted_at = meta.get("extracted_at", "")
                if not extracted_at:
                    buckets["unknown"] += 1
                    continue
                dt = datetime.fromisoformat(extracted_at.replace("Z", "+00:00"))
                age = (now - dt).days
                if age < 7:
                    buckets["<7d"] += 1
                elif age < 30:
                    buckets["7-30d"] += 1
                elif age < 90:
                    buckets["30-90d"] += 1
                elif age < 365:
                    buckets["90d-1y"] += 1
                else:
                    buckets[">1y"] += 1
            except (json.JSONDecodeError, ValueError, TypeError):
                buckets["unknown"] += 1
        
        return buckets

    # =================================================================
    # STRUCTURAL SCORING (No LLM, no reranker)
    # =================================================================

    def score_chunk_structural(self, text: str, metadata_json: str = "{}") -> float:
        """
        ★ SOTA v2: Content-type-aware structural quality scoring (0.0-1.0).
        
        Critical improvement: Different content types have different structural
        expectations. A formula chunk legitimately has low alpha ratio and short
        length. A legal chunk legitimately has no sentence-ending capitalization.
        
        Scoring is penalty-based from 1.0 (perfect).
        Content type modulates which penalties apply and their magnitude.
        """
        if not text or len(text.strip()) < 10:
            return 0.0

        text_stripped = text.strip()
        text_len = len(text_stripped)
        words = re.findall(r'\b\w+\b', text_stripped)
        word_count = max(len(words), 1)
        
        # Detect content type for adaptive scoring
        content_type = self.detect_content_type(text, metadata_json)
        
        # Detect hard defects — these override scoring
        defects = self.detect_defects(text, metadata_json)
        if "trivial" in defects:
            return 0.0
        if "encoding_garbage" in defects:
            return 0.05
        if "cookie_banner" in defects:
            return 0.1
        if "pure_navigation" in defects:
            return 0.1

        score = 1.0  # Start at perfect, subtract penalties

        # ── 1. Length penalty (relaxed for formulas, definitions, tables) ──
        if content_type in ("formula", "definition", "table"):
            # Short is expected for these types
            if text_len < 15:
                score -= 0.2
        elif content_type == "code":
            if text_len < 20:
                score -= 0.3
        else:  # prose, legal
            if text_len < 30:
                score -= 0.4
            elif text_len < 80:
                score -= 0.2
            elif text_len < 150:
                score -= 0.05

        # ── 2. Alpha ratio (disabled for formula, table, code) ──
        alpha_chars = sum(1 for c in text_stripped if c.isalpha())
        alpha_ratio = alpha_chars / max(text_len, 1)
        if content_type in ("formula", "table", "code"):
            # These types legitimately have low alpha ratio
            if alpha_ratio < 0.10:
                score -= 0.15  # Still penalize if almost no letters at all
        else:
            if alpha_ratio < 0.40:
                score -= 0.4
            elif alpha_ratio < 0.55:
                score -= 0.15

        # ── 3. Sentence detection (disabled for formula, table, code, definition) ──
        if content_type == "prose":
            sentence_endings = len(re.findall(r'[.!?]\s+[A-ZÄÖÜ]', text_stripped))
            ends_with_sentence = text_stripped.rstrip().endswith(('.', '!', '?'))
            if sentence_endings == 0 and not ends_with_sentence and text_len > 100:
                score -= 0.15

        # ── 4. Boilerplate patterns ──
        boilerplate_matches = len(_BOILERPLATE_PATTERNS.findall(text_stripped))
        if boilerplate_matches >= 3:
            score -= 0.5
        elif boilerplate_matches >= 2:
            score -= 0.3
        elif boilerplate_matches >= 1:
            score -= 0.15

        # ── 5. URL density ──
        urls = _URL_PATTERN.findall(text_stripped)
        url_char_len = sum(len(u) for u in urls)
        url_ratio = url_char_len / max(text_len, 1)
        if url_ratio > 0.5:
            score -= 0.45
        elif url_ratio > 0.3:
            score -= 0.25

        # ── 6. Navigation separators ──
        nav_matches = len(_NAV_SEPARATOR_PATTERN.findall(text_stripped))
        if nav_matches >= 2:
            score -= 0.35

        # ── 7. Unique word ratio (disabled for formula, code) ──
        if content_type not in ("formula", "code"):
            unique_words = set(w.lower() for w in words)
            unique_ratio = len(unique_words) / word_count
            if unique_ratio < 0.3 and word_count > 20:
                score -= 0.2

        # ── 8. Information density ──
        if content_type == "prose":
            cap_words = sum(1 for w in words if w[0].isupper() and len(w) > 1)
            numbers = sum(1 for w in words if any(c.isdigit() for c in w))
            info_density = (cap_words + numbers) / word_count
            if info_density < 0.02 and word_count > 30:
                score -= 0.1

        return max(0.0, min(1.0, score))

    # =================================================================
    # NEAR-DUPLICATE DETECTION
    # =================================================================

    def find_near_duplicates(
        self,
        conn: sqlite3.Connection,
        cosine_threshold: float = 0.97,
        sample_limit: int = 0,
    ) -> List[Tuple[str, int, str, int, float]]:
        """
        Find near-duplicate chunk pairs via embedding cosine similarity.
        
        Returns list of (doc_id_a, chunk_id_a, doc_id_b, chunk_id_b, similarity).
        Only cross-document duplicates are flagged.
        """
        logger.info(f"🔍 Scanning for near-duplicates (threshold={cosine_threshold})...")
        cur = conn.cursor()

        query = "SELECT doc_id, chunk_id, embedding FROM chunks ORDER BY doc_id, chunk_id"
        if sample_limit > 0:
            query += f" LIMIT {sample_limit}"
        cur.execute(query)

        rows = cur.fetchall()
        if not rows:
            return []

        # Parse embeddings
        items: List[Tuple[str, int, np.ndarray]] = []
        for row in rows:
            doc_id, chunk_id = row["doc_id"], row["chunk_id"]
            blob = row["embedding"]
            if blob:
                vec = np.frombuffer(blob, dtype=np.float32)
                if len(vec) == self.expected_embedding_dim:
                    # Normalise for fast cosine via dot product
                    norm = np.linalg.norm(vec)
                    if norm > 0:
                        items.append((doc_id, chunk_id, vec / norm))

        logger.info(f"  Loaded {len(items)} embeddings for dedup scan")

        if len(items) < 2:
            return []

        duplicates: List[Tuple[str, int, str, int, float]] = []

        # ★ SOTA v4: GPU-accelerated near-duplicate detection
        # Matrix multiplication on CUDA (RTX 4090: 128 SMs, 16384 CUDA cores)
        # is 5-20x faster than numpy CPU for 15k×768 → 15k×15k matmul.
        # np.where/tril on the result stays on CPU (fast, no kernel needed).
        # Fallback: numpy CPU if CUDA is unavailable.
        def _vectorised_dedup(work_items):
            vecs = np.stack([v for _, _, v in work_items])
            doc_ids_arr = [d for d, _, _ in work_items]
            n = len(work_items)

            # Compute similarity matrix — GPU if VRAM allows, else CPU
            def _compute_sim_matrix(vecs: np.ndarray, n: int) -> np.ndarray:
                """Compute upper-triangle cosine sim matrix (GPU or CPU)."""
                try:
                    import torch
                    if torch.cuda.is_available():
                        # Dual-GPU: Sim-Matrix auf der AUX-GPU (RTX 3060 Ti)
                        try:
                            from utils.gpu_devices import get_placement
                            _aux_idx = get_placement().aux_cuda
                        except Exception:
                            _aux_idx = 0
                        _dev = "cuda" if _aux_idx == 0 else f"cuda:{_aux_idx}"
                        vram_needed_bytes = (n * 768 * 4) + (n * n * 4) * 2
                        free_vram = torch.cuda.mem_get_info(_aux_idx)[0]
                        if free_vram > vram_needed_bytes * 1.5:
                            vecs_gpu = torch.tensor(vecs, device=_dev, dtype=torch.float32)
                            sim_gpu = torch.mm(vecs_gpu, vecs_gpu.T)
                            sim_gpu[torch.tril_indices(n, n, device=_dev).unbind()] = 0.0
                            result = sim_gpu.cpu().numpy()
                            del vecs_gpu, sim_gpu
                            torch.cuda.empty_cache()
                            logger.debug(f"  Near-duplicate sim matrix computed on GPU ({n}×{n}, free VRAM: {free_vram/1024**3:.1f}GB)")
                            return result
                        else:
                            logger.debug(
                                f"  GPU VRAM insufficient for {n}×{n} sim matrix "
                                f"(need {vram_needed_bytes*1.5/1024**3:.1f}GB, free {free_vram/1024**3:.1f}GB) → CPU fallback"
                            )
                except ImportError:
                    pass
                # CPU path: L2-normalised embeddings → cosine = dot product
                mat = vecs @ vecs.T
                mat[np.tril_indices(n)] = 0.0
                return np.asarray(mat, dtype=np.float32)

            sim_matrix = _compute_sim_matrix(vecs, n)

            # Find all pairs above threshold in one vectorised call
            rows_idx, cols_idx = np.where(sim_matrix >= cosine_threshold)
            results = []
            for r, c in zip(rows_idx, cols_idx):
                if doc_ids_arr[r] == doc_ids_arr[c]:
                    continue  # Same document — skip
                results.append((
                    work_items[r][0], work_items[r][1],
                    work_items[c][0], work_items[c][1],
                    float(sim_matrix[r, c]),
                ))
            return results

        if len(items) <= 20000:
            duplicates = _vectorised_dedup(items)
        else:
            # For large DBs: sampled comparison
            import random
            sample_size = min(15000, len(items))
            sampled = random.sample(items, sample_size)
            duplicates = _vectorised_dedup(sampled)

        logger.info(f"  Found {len(duplicates)} near-duplicate pairs")
        return duplicates

    # =================================================================
    # CROSS-DOCUMENT BOILERPLATE DETECTION
    # =================================================================

    def find_boilerplate_chunks(
        self,
        conn: sqlite3.Connection,
        doc_frequency_threshold: float = 0.3,
        min_docs: int = 5,
    ) -> List[Tuple[str, int, str, int]]:
        """
        ★ SOTA v2: Find chunks that appear (near-)identically across many documents.
        
        Fixes:
        - Fingerprint: 500 chars (was 200 — too aggressive, caught legitimate intros)
        - min_docs: max(5, total_docs * 0.01) — scales with corpus size
        - Only flags if fingerprint > 50 chars (was 30)
        
        Returns list of (doc_id, chunk_id, fingerprint, doc_count).
        """
        logger.info("🔍 Scanning for cross-document boilerplate (SOTA v2)...")
        cur = conn.cursor()
        cur.execute("SELECT doc_id, chunk_id, text FROM chunks")

        # Fingerprint: normalised first 500 chars (was 200)
        fingerprints: Dict[str, List[Tuple[str, int]]] = defaultdict(list)
        for row in cur.fetchall():
            text = row["text"] or ""
            fp = re.sub(r'\s+', ' ', text[:500].lower().strip())
            if len(fp) > 50:  # Only meaningful length (was 30)
                fingerprints[fp].append((row["doc_id"], row["chunk_id"]))

        # Count distinct doc_ids per fingerprint
        cur.execute("SELECT COUNT(DISTINCT doc_id) FROM documents")
        total_docs_row = cur.fetchone()
        total_docs = total_docs_row[0] if total_docs_row else 1
        total_docs = max(total_docs, 1)
        
        # ★ SOTA v2: min_docs scales with corpus size
        effective_min_docs = max(min_docs, int(total_docs * 0.01))

        boilerplate: List[Tuple[str, int, str, int]] = []
        for fp, locations in fingerprints.items():
            distinct_docs = len(set(doc_id for doc_id, _ in locations))
            if distinct_docs >= effective_min_docs and distinct_docs / total_docs >= doc_frequency_threshold:
                for doc_id, chunk_id in locations:
                    boilerplate.append((doc_id, chunk_id, fp[:60], distinct_docs))

        logger.info(
            f"  Found {len(boilerplate)} boilerplate chunks across "
            f"{len(set(fp for _, _, fp, _ in boilerplate))} unique patterns "
            f"(min_docs={effective_min_docs})"
        )
        return boilerplate

    # =================================================================
    # ORPHAN DETECTION
    # =================================================================

    def find_orphans(self, conn: sqlite3.Connection) -> Tuple[List[Tuple[str, int]], List[Tuple[int, str]]]:
        """
        Find orphan chunks (no parent doc) and orphan triples (no parent doc).
        Returns (orphan_chunks, orphan_triples).
        """
        cur = conn.cursor()

        cur.execute(
            "SELECT c.doc_id, c.chunk_id FROM chunks c "
            "LEFT JOIN documents d ON c.doc_id = d.doc_id "
            "WHERE d.doc_id IS NULL"
        )
        orphan_chunks = [(r["doc_id"], r["chunk_id"]) for r in cur.fetchall()]

        cur.execute(
            "SELECT t.triple_id, t.doc_id FROM triples t "
            "LEFT JOIN documents d ON t.doc_id = d.doc_id "
            "WHERE d.doc_id IS NULL"
        )
        orphan_triples = [(r["triple_id"], r["doc_id"]) for r in cur.fetchall()]

        logger.info(f"  Orphans: {len(orphan_chunks)} chunks, {len(orphan_triples)} triples")
        return orphan_chunks, orphan_triples

    # =================================================================
    # EMBEDDING DIMENSION CHECK
    # =================================================================

    def check_embedding_dimensions(self, conn: sqlite3.Connection) -> List[Tuple[str, int, int]]:
        """
        Find chunks with wrong embedding dimensions.
        Returns list of (doc_id, chunk_id, actual_dim).
        """
        cur = conn.cursor()
        cur.execute("SELECT doc_id, chunk_id, embedding FROM chunks")
        mismatches = []
        for row in cur.fetchall():
            blob = row["embedding"]
            if blob:
                actual_dim = len(blob) // 4  # float32 = 4 bytes
                if actual_dim != self.expected_embedding_dim:
                    mismatches.append((row["doc_id"], row["chunk_id"], actual_dim))
        if mismatches:
            logger.warning(f"  ⚠️ {len(mismatches)} chunks with wrong embedding dim (expected {self.expected_embedding_dim})")
        return mismatches

    # =================================================================
    # ★ SOTA v2: STALENESS REMOVED AS QUALITY SIGNAL
    # =================================================================
    # Historical knowledge is valuable regardless of age.
    # "Wer war 2008 CEO von Siemens?" requires old data.
    # Age is NOT a defect — it's metadata for temporal query routing.

    def find_stale_chunks(
        self,
        conn: sqlite3.Connection,
        max_age_days: int = 90,
    ) -> List[Tuple[str, int, str, int]]:
        """
        DEPRECATED: Returns empty list. Staleness is NOT a quality signal.
        
        Use get_age_distribution() for informational age overview.
        Use temporal query classifier in SearchManager for time-aware retrieval.
        """
        logger.debug("find_stale_chunks() called but staleness is no longer a quality signal")
        return []

    # =================================================================
    # SHORT / URL-DUMP CHUNK DETECTION
    # =================================================================

    def find_short_chunks(self, conn: sqlite3.Connection, min_length: int = 50) -> List[Tuple[str, int, int]]:
        cur = conn.cursor()
        cur.execute("SELECT doc_id, chunk_id, LENGTH(text) as tlen FROM chunks WHERE LENGTH(text) < ?", (min_length,))
        return [(r["doc_id"], r["chunk_id"], r["tlen"]) for r in cur.fetchall()]

    def find_url_dump_chunks(self, conn: sqlite3.Connection, url_ratio_threshold: float = 0.5) -> List[Tuple[str, int, float]]:
        cur = conn.cursor()
        cur.execute("SELECT doc_id, chunk_id, text FROM chunks")
        results = []
        for row in cur.fetchall():
            text = row["text"] or ""
            if len(text) < 20:
                continue
            urls = _URL_PATTERN.findall(text)
            url_char_len = sum(len(u) for u in urls)
            ratio = url_char_len / max(len(text), 1)
            if ratio >= url_ratio_threshold:
                results.append((row["doc_id"], row["chunk_id"], ratio))
        return results

    # =================================================================
    # ★ SOTA v2: IDF-BASED PREDICATE SCORING
    # =================================================================

    @staticmethod
    def score_predicate(predicate: str) -> float:
        """
        Legacy static predicate scoring (fallback when no IDF map available).
        Used only during ingest when DB connection is not available.
        """
        pred_lower = predicate.strip().lower()
        if not pred_lower:
            return 0.0
        if pred_lower in _GENERIC_PREDICATES:
            return _GENERIC_PREDICATES[pred_lower]
        # Fallback: anything not in the generic list gets a moderate score
        return 0.5

    def compute_predicate_idf(self, conn: sqlite3.Connection) -> Dict[str, float]:
        """
        ★ SOTA v2: Compute IDF (Inverse Document Frequency) scores for ALL predicates
        in the corpus. Rare predicates are more informative.
        
        IDF(p) = log(N / df(p)) where N = total triples, df(p) = frequency of predicate p.
        Normalised to 0.0-1.0 range.
        
        This replaces the static word-count heuristic (which was INVERTED:
        "founded"=0.3 while "is related to"=0.8).
        
        Returns {predicate_lower: idf_score}
        """
        cur = conn.cursor()
        cur.execute("SELECT predicate, COUNT(*) as freq FROM triples GROUP BY predicate")
        rows = cur.fetchall()
        
        if not rows:
            return {}
        
        # Build frequency map
        pred_freq: Dict[str, int] = {}
        total = 0
        for row in rows:
            pred = (row["predicate"] or "").strip().lower()
            if pred:
                pred_freq[pred] = row["freq"]
                total += row["freq"]
        
        if total == 0:
            return {}
        
        max_freq = max(pred_freq.values())
        
        # Compute normalised IDF
        idf_scores: Dict[str, float] = {}
        for pred, freq in pred_freq.items():
            # Hard floor for known-garbage predicates (articles, conjunctions)
            if pred in _GENERIC_PREDICATES and _GENERIC_PREDICATES[pred] <= 0.05:
                idf_scores[pred] = 0.0
                continue
            
            # IDF: rare predicates → high score
            raw_idf = math.log((total + 1) / (freq + 1))
            max_idf = math.log((total + 1) / 2)  # max possible IDF (freq=1)
            
            if max_idf > 0:
                normalised = raw_idf / max_idf  # 0.0 to 1.0
            else:
                normalised = 0.5
            
            idf_scores[pred] = round(max(0.0, min(1.0, normalised)), 4)
        
        logger.info(f"  Computed IDF scores for {len(idf_scores)} unique predicates "
                     f"(total={total}, max_freq={max_freq})")
        return idf_scores

    def score_predicate_idf(self, predicate: str, idf_map: Dict[str, float]) -> float:
        """Score a predicate using precomputed IDF map."""
        pred_lower = predicate.strip().lower()
        if not pred_lower:
            return 0.0
        # Known garbage override
        if pred_lower in _GENERIC_PREDICATES and _GENERIC_PREDICATES[pred_lower] <= 0.05:
            return 0.0
        return idf_map.get(pred_lower, 0.5)  # Unknown predicates get moderate score

    def score_all_triples_predicates(self, conn: sqlite3.Connection) -> Dict[int, float]:
        """
        ★ SOTA v2: Score all triples by IDF-based predicate quality.
        Returns {triple_id: score}.
        """
        # Compute IDF map from corpus
        idf_map = self.compute_predicate_idf(conn)
        
        cur = conn.cursor()
        cur.execute("SELECT triple_id, predicate FROM triples")
        scores = {}
        for row in cur.fetchall():
            pred = row["predicate"] or ""
            scores[row["triple_id"]] = self.score_predicate_idf(pred, idf_map)
        return scores

    # =================================================================
    # ★ SOTA v2: KG GROUNDING VIA CROSS-ENCODER + SOURCE CHUNK
    # =================================================================

    def verify_triples_grounding(
        self,
        conn: sqlite3.Connection,
        reranker: Any = None,
        batch_size: int = 64,
        min_grounding_score: float = 0.0,
        sample_limit: int = 5000,
        triple_ids: Optional[List[int]] = None,
    ) -> Dict[int, float]:
        """
        ★ SOTA v2: Verify KG triples against their SOURCE CHUNK (not GROUP_CONCAT).
        
        Critical fix: Old version used GROUP_CONCAT(all chunks of doc) truncated
        to 2000 chars — this is WRONG because for large documents, the relevant
        chunk was often not in those 2000 chars.
        
        New approach:
        1. If triple has source_chunk_id → use that chunk directly
        2. If no source_chunk_id (legacy triples) → infer best chunk via
           embedding similarity between triple text and doc chunks
        3. Verify triple against best-matching chunk(s) using reranker
        4. Progressive sampling: verify up to sample_limit triples per run
        5. Verify ALL triples regardless of extraction method (LLM or regex)
           — extraction method says nothing about semantic correctness
        
        Returns {triple_id: grounding_score}.
        """
        rr = _canonical_reranker(reranker or self._reranker)
        if rr is None:
            logger.warning("⚠️ No reranker available for triple grounding verification")
            return {}

        # ★ SOTA FIX: CrossEncoderReranker uses lazy-init — model is NOT loaded
        # until first rerank() call. is_available returns False before loading.
        # We must trigger loading before checking availability.
        if hasattr(rr, '_ensure_loaded'):
            try:
                rr._ensure_loaded()
            except Exception as e:
                logger.error(f"❌ Reranker loading failed: {e}")
                return {}

        if hasattr(rr, 'is_available') and not rr.is_available:
            logger.warning("⚠️ Reranker not available/loaded for grounding check")
            return {}

        logger.info(f"🔍 Verifying KG triple grounding (SOTA v2, sample_limit={sample_limit})...")
        cur = conn.cursor()

        # ★ SOTA v3: Verify ALL triples — regex and LLM alike.
        # Extraction method (regex vs LLM) says nothing about semantic correctness.
        # A regex triple "X is_a Y" can be perfectly grounded; an LLM triple can hallucinate.
        where_clauses: List[str] = []
        params: List[Any] = []
        if triple_ids:
            placeholders = ",".join("?" for _ in triple_ids)
            where_clauses.append(f"t.triple_id IN ({placeholders})")
            params.extend(triple_ids)
        else:
            where_clauses.append("(tq.grounding_score IS NULL OR tq.grounding_score < 0)")

        where_sql = " AND ".join(where_clauses)
        if where_sql:
            where_sql = "WHERE " + where_sql

        cur.execute(
            f"""
            SELECT t.triple_id, t.subject, t.predicate, t.object, t.doc_id,
                   t.source_chunk_id, t.metadata
            FROM triples t
            LEFT JOIN triple_quality tq ON t.triple_id = tq.triple_id
            {where_sql}
            ORDER BY t.triple_id
            LIMIT ?
        """,
            (*params, sample_limit),
        )
        rows = cur.fetchall()
        
        if not rows:
            logger.info("  All triples already verified or no triples found")
            return {}

        logger.info(f"  Verifying {len(rows)} triples (all extraction types)...")

        # Pre-load chunk texts per doc_id for efficiency
        doc_ids = list(set(row["doc_id"] for row in rows))
        doc_chunks: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        
        for doc_id in doc_ids:
            cur.execute(
                "SELECT chunk_id, text FROM chunks WHERE doc_id = ? ORDER BY chunk_id",
                (doc_id,)
            )
            for chunk_row in cur.fetchall():
                doc_chunks[doc_id].append({
                    "chunk_id": chunk_row["chunk_id"],
                    "text": chunk_row["text"] or "",
                })

        # ★ SOTA v3: Batch grounding verification using reranker's batch API.
        # Instead of calling rr.rerank() per triple (N separate calls),
        # collect ALL (hypothesis, passage) pairs and score them in one batch.
        # This exploits ONNX/GPU batching: ~3-10x faster than sequential calls.
        
        scores: Dict[int, float] = {}
        inferred_source_chunks: Dict[int, int] = {}  # triple_id -> chunk_id
        
        # Phase 1: Collect all (hypothesis, passage) pairs with metadata
        all_pairs: List[Tuple[str, str]] = []  # (hypothesis, passage_text)
        pair_meta: List[Dict[str, Any]] = []   # {triple_id, chunk_id, pair_type}
        
        for row in rows:
            triple_id = row["triple_id"]
            doc_id = row["doc_id"]
            hypothesis = f"{row['subject']} {row['predicate']} {row['object']}"
            source_chunk_id = row["source_chunk_id"]
            
            chunks = doc_chunks.get(doc_id, [])
            if not chunks:
                scores[triple_id] = 0.0
                continue
            
            if source_chunk_id is not None:
                # Direct verification: 1 pair for this triple
                source_text = ""
                for c in chunks:
                    if c["chunk_id"] == source_chunk_id:
                        source_text = c["text"]
                        break
                if source_text:
                    all_pairs.append((hypothesis, source_text))
                    pair_meta.append({
                        "triple_id": triple_id,
                        "chunk_id": source_chunk_id,
                        "pair_type": "direct",
                    })
                else:
                    scores[triple_id] = 0.0
            else:
                # No source_chunk_id: score against top-20 candidate chunks
                candidate_chunks = chunks[:20]
                if not candidate_chunks:
                    scores[triple_id] = 0.0
                    continue
                for c in candidate_chunks:
                    all_pairs.append((hypothesis, c["text"]))
                    pair_meta.append({
                        "triple_id": triple_id,
                        "chunk_id": c["chunk_id"],
                        "pair_type": "candidate",
                    })
        
        # Phase 2: Batch predict all pairs at once
        if all_pairs:
            try:
                # Use the batch API (ONNX path: ~3.3x faster than PyTorch)
                if hasattr(rr, '_predict_optimized'):
                    all_scores_list = rr._predict_optimized(
                        all_pairs, batch_size=batch_size
                    )
                else:
                    # Fallback: use rerank() per pair (shouldn't happen)
                    all_scores_list = []
                    for hyp, pas in all_pairs:
                        try:
                            result = rr.rerank(
                                query=hyp,
                                passages=[{"text": pas}],
                                top_k=1,
                                text_key="text",
                            )
                            s = result[0].get("rerank_score", 0.0) if result else 0.0
                            all_scores_list.append(float(s))
                        except Exception:
                            all_scores_list.append(-1.0)
                
                # Phase 3: Map scores back to triples
                # For "direct" pairs: 1:1 mapping
                # For "candidate" pairs: take the max score per triple
                triple_candidate_scores: Dict[int, List[Tuple[float, int]]] = defaultdict(list)
                
                for idx, (score_val, meta) in enumerate(zip(all_scores_list, pair_meta)):
                    tid = meta["triple_id"]
                    cid = meta["chunk_id"]
                    if meta["pair_type"] == "direct":
                        scores[tid] = float(score_val)
                    else:
                        triple_candidate_scores[tid].append((float(score_val), cid))
                
                # Resolve candidates: pick best score per triple
                for tid, candidates in triple_candidate_scores.items():
                    if candidates:
                        best_score, best_chunk_id = max(candidates, key=lambda x: x[0])
                        scores[tid] = best_score
                        inferred_source_chunks[tid] = best_chunk_id
                
            except Exception as e:
                logger.error(f"Batch grounding verification failed: {e}")
                # Mark all unscored triples as -1 (retry later)
                for meta in pair_meta:
                    if meta["triple_id"] not in scores:
                        scores[meta["triple_id"]] = -1.0

        # Persist inferred source chunk IDs (root-cause: a missing
        # triple_quality row is the only legitimate "nothing to update" case
        # and is handled by UPSERT semantics below; SQL errors are real bugs
        # and must propagate).
        for triple_id, chunk_id in inferred_source_chunks.items():
            conn.execute(
                "INSERT INTO triple_quality(triple_id, inferred_source_chunk_id) "
                "VALUES (?, ?) "
                "ON CONFLICT(triple_id) DO UPDATE SET inferred_source_chunk_id=excluded.inferred_source_chunk_id",
                (triple_id, chunk_id),
            )

        grounded = sum(1 for s in scores.values() if s > 0.3)
        logger.info(f"  Grounding results: {grounded}/{len(scores)} triples grounded (score > 0.3)")
        return scores

    # =================================================================
    # REGEX FALLBACK TRIPLE IDENTIFICATION
    # =================================================================

    def find_regex_fallback_triples(self, conn: sqlite3.Connection) -> List[Tuple[int, str]]:
        """Find triples generated by regex fallback (not LLM)."""
        cur = conn.cursor()
        cur.execute("SELECT triple_id, metadata FROM triples")
        regex_triples = []
        for row in cur.fetchall():
            try:
                meta = json.loads(row["metadata"] or "{}")
                kg_source = meta.get("kg_source", "")
                if kg_source != "llm":
                    regex_triples.append((row["triple_id"], kg_source or "unknown"))
            except (json.JSONDecodeError, TypeError):
                regex_triples.append((row["triple_id"], "parse_error"))
        return regex_triples

    # =================================================================
    # QUARANTINE SYSTEM
    # =================================================================

    def quarantine_chunks(
        self,
        conn: sqlite3.Connection,
        chunk_ids: List[Tuple[str, int]],
        reason: str,
        auto_delete_days: int = 30,
        cascade_to_triples: bool = True,
    ) -> int:
        """Move chunks to quarantine (soft-delete with backup).
        
        ★ SOTA v3: cascade_to_triples — when True, automatically quarantines
        all triples associated with the quarantined chunks. The association
        is resolved via inferred_source_chunk_id (specific) and doc_id (fallback
        when all chunks of a doc are quarantined).
        """
        now = datetime.now(timezone.utc).isoformat()
        auto_delete = (datetime.now(timezone.utc) + timedelta(days=auto_delete_days)).isoformat()
        quarantined = 0
        cascade_triple_ids: List[int] = []
        cur = conn.cursor()

        for doc_id, chunk_id in chunk_ids:
            try:
                # Backup the chunk — include *all* schema columns so restore is
                # lossless. Previously this dropped embedding, domain,
                # safety_flag and classification_version, silently corrupting
                # restored chunks (empty embedding => unfindable, default
                # namespace => wrong retrieval scope).
                cur.execute(
                    "SELECT doc_id, chunk_id, text, metadata, embedding, "
                    "domain, safety_flag, classification_version "
                    "FROM chunks WHERE doc_id=? AND chunk_id=?",
                    (doc_id, chunk_id),
                )
                row = cur.fetchone()
                if not row:
                    continue

                emb_blob = row["embedding"] if "embedding" in row.keys() else b""
                emb_b64 = base64.b64encode(emb_blob or b"").decode("ascii")
                backup = json.dumps({
                    "doc_id": row["doc_id"],
                    "chunk_id": row["chunk_id"],
                    "text": row["text"],
                    "metadata": row["metadata"],
                    "embedding_b64": emb_b64,
                    "domain": row["domain"],
                    "safety_flag": row["safety_flag"],
                    "classification_version": row["classification_version"],
                }, ensure_ascii=False)

                # Insert into quarantine
                cur.execute(
                    "INSERT INTO quarantine(source_table, source_id, reason, quarantined_at, data_backup, auto_delete_after) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    ("chunks", f"{doc_id}:{chunk_id}", reason, now, backup, auto_delete),
                )

                # Delete from main table
                cur.execute(
                    "DELETE FROM chunks WHERE doc_id=? AND chunk_id=?",
                    (doc_id, chunk_id),
                )

                # ★ Update chunk_quality to prevent re-processing
                cur.execute(
                    "UPDATE chunk_quality SET action_taken='quarantined' "
                    "WHERE doc_id=? AND chunk_id=?",
                    (doc_id, chunk_id),
                )

                # ★ SOTA v3: Collect triples to cascade-quarantine
                if cascade_to_triples:
                    # Primary: triples with inferred_source_chunk_id
                    cur.execute(
                        "SELECT t.triple_id FROM triples t "
                        "JOIN triple_quality tq ON t.triple_id = tq.triple_id "
                        "WHERE tq.inferred_source_chunk_id = ? AND t.doc_id = ?",
                        (chunk_id, doc_id),
                    )
                    for trow in cur.fetchall():
                        cascade_triple_ids.append(trow[0])

                    # Fallback: if no specific chunk link, check if ALL chunks of
                    # this doc are being quarantined → quarantine doc's triples
                    if not cascade_triple_ids:
                        all_doc_chunks = set(
                            (did, cid) for did, cid in chunk_ids if did == doc_id
                        )
                        cur.execute(
                            "SELECT COUNT(*) FROM chunks WHERE doc_id = ?", (doc_id,)
                        )
                        remaining = cur.fetchone()[0]
                        if remaining == 0:
                            # All chunks of this doc are gone → orphan its triples
                            cur.execute(
                                "SELECT triple_id FROM triples WHERE doc_id = ?",
                                (doc_id,),
                            )
                            for trow in cur.fetchall():
                                cascade_triple_ids.append(trow[0])

                # Log audit
                self._log_audit(conn, AuditEntry(
                    action="quarantine",
                    target_table="chunks",
                    target_id=f"{doc_id}:{chunk_id}",
                    details=reason,
                ))

                quarantined += 1
            except Exception as e:
                logger.error(f"Failed to quarantine chunk {doc_id}:{chunk_id}: {e}")

        # ★ SOTA v3: Execute cascade quarantine for associated triples
        cascade_count = 0
        if cascade_to_triples and cascade_triple_ids:
            unique_triple_ids = list(set(cascade_triple_ids))
            cascade_reason = f"cascade_chunk_quarantine:{reason}"
            cascade_count = self.quarantine_triples(
                conn, unique_triple_ids, cascade_reason, auto_delete_days
            )
            logger.info(
                f"🔗 Cascade: quarantined {cascade_count} triples "
                f"associated with {quarantined} quarantined chunks"
            )

        conn.commit()
        logger.info(f"🗑️ Quarantined {quarantined} chunks (reason: {reason})")
        return quarantined

    def quarantine_triples(
        self,
        conn: sqlite3.Connection,
        triple_ids: List[int],
        reason: str,
        auto_delete_days: int = 30,
    ) -> int:
        """Move triples to quarantine."""
        now = datetime.now(timezone.utc).isoformat()
        auto_delete = (datetime.now(timezone.utc) + timedelta(days=auto_delete_days)).isoformat()
        quarantined = 0
        cur = conn.cursor()

        for triple_id in triple_ids:
            try:
                cur.execute(
                    "SELECT t.triple_id, t.doc_id, t.subject, t.predicate, t.object, "
                    "       t.metadata, t.triple_hash, t.source_chunk_id, "
                    "       tq.inferred_source_chunk_id "
                    "FROM triples t "
                    "LEFT JOIN triple_quality tq ON tq.triple_id = t.triple_id "
                    "WHERE t.triple_id=?",
                    (triple_id,),
                )
                row = cur.fetchone()
                if not row:
                    continue

                resolved_source_chunk_id = row["source_chunk_id"]
                if resolved_source_chunk_id is None:
                    resolved_source_chunk_id = row["inferred_source_chunk_id"]

                source_hint = None
                if resolved_source_chunk_id is not None:
                    cur.execute(
                        "SELECT metadata FROM chunks WHERE doc_id = ? AND chunk_id = ?",
                        (row["doc_id"], resolved_source_chunk_id),
                    )
                    chunk_row = cur.fetchone()
                    if chunk_row and chunk_row["metadata"]:
                        try:
                            chunk_meta = json.loads(chunk_row["metadata"])
                            source_hint = chunk_meta.get("source") or chunk_meta.get("filename")
                        except (json.JSONDecodeError, TypeError):
                            source_hint = None

                backup = json.dumps({
                    "triple_id": row["triple_id"],
                    "doc_id": row["doc_id"],
                    "subject": row["subject"],
                    "predicate": row["predicate"],
                    "object": row["object"],
                    "metadata": row["metadata"],
                    "triple_hash": row["triple_hash"],
                    "source_chunk_id": row["source_chunk_id"],
                    "inferred_source_chunk_id": row["inferred_source_chunk_id"],
                    "resolved_source_chunk_id": resolved_source_chunk_id,
                    "source_hint": source_hint,
                }, ensure_ascii=False)

                cur.execute(
                    "INSERT INTO quarantine(source_table, source_id, reason, quarantined_at, data_backup, auto_delete_after) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    ("triples", str(triple_id), reason, now, backup, auto_delete),
                )

                # ★ SOTA: Capture subject/object BEFORE delete so we can
                # recompute kg_entities.frequency afterwards (else stale freq
                # ranks already-removed entities too high in KG retrieval).
                affected_entities = {row["subject"], row["object"]}

                cur.execute("DELETE FROM triples WHERE triple_id=?", (triple_id,))

                # ★ Update triple_quality to prevent re-processing
                cur.execute(
                    "UPDATE triple_quality SET action_taken='quarantined' "
                    "WHERE triple_id=?",
                    (triple_id,),
                )

                # ★ SOTA: Recompute frequency for affected entities (mirrors
                # unified_rag_store._delete_triples_by_doc_id pattern).
                # If entity is no longer referenced anywhere, drop its row.
                for entity in affected_entities:
                    if not entity:
                        continue
                    remaining = cur.execute(
                        "SELECT COUNT(*) FROM triples "
                        "WHERE subject = ? OR object = ?",
                        (entity, entity),
                    ).fetchone()[0]
                    if remaining == 0:
                        cur.execute(
                            "DELETE FROM kg_entities WHERE entity_text = ?",
                            (entity,),
                        )
                    else:
                        cur.execute(
                            "UPDATE kg_entities SET frequency = ? "
                            "WHERE entity_text = ?",
                            (remaining, entity),
                        )

                self._log_audit(conn, AuditEntry(
                    action="quarantine",
                    target_table="triples",
                    target_id=str(triple_id),
                    details=reason,
                ))

                quarantined += 1
            except Exception as e:
                logger.error(f"Failed to quarantine triple {triple_id}: {e}")

        conn.commit()
        logger.info(f"🗑️ Quarantined {quarantined} triples (reason: {reason})")
        return quarantined

    def restore_from_quarantine(
        self,
        conn: sqlite3.Connection,
        quarantine_ids: List[int],
        cascade_restore: bool = True,
    ) -> int:
        """Restore records from quarantine back to their original tables.
        
        ★ SOTA v3: cascade_restore — when restoring a chunk, automatically also
        restores triples that were cascade-quarantined because of this chunk
        (identified by reason prefix 'cascade_chunk_quarantine:').
        """
        restored = 0
        cascade_qids: List[int] = []
        cur = conn.cursor()

        for qid in quarantine_ids:
            try:
                cur.execute("SELECT source_table, source_id, data_backup, reason FROM quarantine WHERE id=?", (qid,))
                row = cur.fetchone()
                if not row:
                    continue

                data = json.loads(row["data_backup"])
                source_table = row["source_table"]

                if source_table == "chunks":
                    # Lossless restore — honour all columns the backup carries.
                    # Older backups (pre-fix) lack embedding_b64/domain/etc.;
                    # for those we MUST raise rather than re-insert garbage,
                    # since a chunk with empty embedding is invisible to
                    # retrieval and a chunk in the wrong domain is worse than
                    # no chunk at all.
                    required = (
                        "embedding_b64", "domain", "safety_flag",
                        "classification_version",
                    )
                    missing = [k for k in required if k not in data]
                    if missing:
                        raise RuntimeError(
                            f"Quarantine entry {qid} (chunk "
                            f"{data.get('doc_id')}:{data.get('chunk_id')}) is in"
                            f" the legacy backup format and lacks {missing}. "
                            "Refusing to restore lossy data; quarantine this "
                            "row for manual review or re-ingest the source."
                        )
                    emb_blob = base64.b64decode(data["embedding_b64"])
                    cur.execute(
                        "INSERT OR REPLACE INTO chunks("
                        "doc_id, chunk_id, text, metadata, embedding, "
                        "domain, safety_flag, classification_version"
                        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            data["doc_id"], data["chunk_id"],
                            data["text"], data["metadata"],
                            emb_blob,
                            data["domain"], data["safety_flag"],
                            int(data["classification_version"]),
                        ),
                    )
                    # ★ SOTA v3: Find cascade-quarantined triples for this chunk
                    if cascade_restore:
                        source_id = row["source_id"]  # "doc_id:chunk_id"
                        cur.execute(
                            "SELECT id FROM quarantine WHERE source_table='triples' "
                            "AND reason LIKE 'cascade_chunk_quarantine:%'",
                        )
                        for trow in cur.fetchall():
                            cascade_qids.append(trow[0])

                elif source_table == "triples":
                    cur.execute(
                        "INSERT OR REPLACE INTO triples(doc_id, subject, predicate, object, metadata, triple_hash, source_chunk_id) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (data["doc_id"], data["subject"], data["predicate"],
                         data["object"], data["metadata"], data.get("triple_hash", ""),
                         data.get("source_chunk_id")),
                    )

                cur.execute("DELETE FROM quarantine WHERE id=?", (qid,))

                self._log_audit(conn, AuditEntry(
                    action="restore",
                    target_table=source_table,
                    target_id=row["source_id"],
                    details="Restored from quarantine",
                ))

                restored += 1
            except Exception as e:
                logger.error(f"Failed to restore quarantine record {qid}: {e}")

        # ★ SOTA v3: Cascade-restore triples that were auto-quarantined
        if cascade_restore and cascade_qids:
            # Filter out IDs already processed in this call
            already_processed = set(quarantine_ids)
            new_cascade = [qid for qid in set(cascade_qids) if qid not in already_processed]
            if new_cascade:
                cascade_restored = self.restore_from_quarantine(
                    conn, new_cascade, cascade_restore=False
                )
                restored += cascade_restored
                logger.info(f"🔗 Cascade-restored {cascade_restored} triples")

        conn.commit()
        logger.info(f"✅ Restored {restored} records from quarantine")
        return restored

    def detect_chunks_with_bad_triples(
        self,
        conn: sqlite3.Connection,
        grounding_threshold: float = 0.3,
        failure_ratio: float = 0.5,
    ) -> List[Tuple[str, int, float]]:
        """
        ★ SOTA v3: Reverse signal — identify chunks where >failure_ratio of their
        triples have grounding_score below grounding_threshold.
        
        This catches chunks that LOOK structurally fine but contain factually
        dubious content (hallucinated facts, wrong attributions).
        
        Uses inferred_source_chunk_id from triple_quality to link triples to chunks.
        
        Returns: List of (doc_id, chunk_id, ratio_of_failed_triples)
        """
        cur = conn.cursor()
        # Get all chunk-triple links with grounding scores
        cur.execute(
            "SELECT tq.inferred_source_chunk_id AS chunk_id, t.doc_id, tq.grounding_score "
            "FROM triple_quality tq "
            "JOIN triples t ON t.triple_id = tq.triple_id "
            "WHERE tq.inferred_source_chunk_id IS NOT NULL "
            "AND tq.grounding_score >= 0"  # -1 = not yet verified
        )
        
        # Aggregate per chunk: count total and failed
        chunk_stats: Dict[Tuple[str, int], Dict[str, int]] = {}
        for row in cur.fetchall():
            key = (row["doc_id"], row["chunk_id"])
            if key not in chunk_stats:
                chunk_stats[key] = {"total": 0, "failed": 0}
            chunk_stats[key]["total"] += 1
            if row["grounding_score"] < grounding_threshold:
                chunk_stats[key]["failed"] += 1
        
        # Filter: only chunks with enough triples and high failure ratio
        bad_chunks = []
        for (doc_id, chunk_id), stats in chunk_stats.items():
            if stats["total"] >= 2:  # Need at least 2 triples to judge
                ratio = stats["failed"] / stats["total"]
                if ratio >= failure_ratio:
                    bad_chunks.append((doc_id, chunk_id, round(ratio, 3)))
        
        if bad_chunks:
            logger.info(
                f"🔗 Reverse signal: {len(bad_chunks)} chunks have "
                f">{failure_ratio*100:.0f}% failed triples (grounding < {grounding_threshold})"
            )
        return bad_chunks

    def get_quarantine_stats(self, conn: sqlite3.Connection) -> Dict[str, Any]:
        """Get quarantine statistics."""
        cur = conn.cursor()
        cur.execute("SELECT source_table, reason, COUNT(*) as cnt FROM quarantine GROUP BY source_table, reason")
        stats: Dict[str, Any] = {"total": 0, "by_table": {}, "by_reason": {}}
        for row in cur.fetchall():
            table, reason, cnt = row["source_table"], row["reason"], row["cnt"]
            stats["total"] += cnt
            stats["by_table"][table] = stats["by_table"].get(table, 0) + cnt
            stats["by_reason"][reason] = stats["by_reason"].get(reason, 0) + cnt
        return stats

    def purge_expired_quarantine(self, conn: sqlite3.Connection) -> int:
        """Delete expired quarantine records in terminal states only.

        State-aware retention (DLQ pattern): rows that are still ``pending``
        or retryable ``failed`` are evidence awaiting processing and must not
        be purged by the timer alone.
        """
        now = datetime.now(timezone.utc).isoformat()
        cur = conn.cursor()
        terminal_filter = (
            "auto_delete_after < ? "
            "AND COALESCE(regeneration_status, 'pending') IN ('completed', 'permanent_failed')"
        )
        cur.execute(f"SELECT COUNT(*) FROM quarantine WHERE {terminal_filter}", (now,))
        count = int(cur.fetchone()[0])
        if count > 0:
            cur.execute(f"DELETE FROM quarantine WHERE {terminal_filter}", (now,))
            conn.commit()
            logger.info(f"🗑️ Purged {count} expired quarantine records (terminal states)")
        return count

    def reopen_aged_quarantine(
        self,
        conn: sqlite3.Connection,
        min_age_days: int = 90,
        limit: int = 1000,
    ) -> int:
        """Reopen aged quarantine entries so regeneration can reevaluate them.

        This is a deliberate maintenance action, not a workaround: entries
        that reached a terminal state (``completed`` or ``permanent_failed``)
        long ago are explicitly pushed back to ``pending`` so the root-cause
        regeneration loop can run again — e.g. after a document re-import made
        a previously missing source chunk available. Attempts are reset so
        reopened rows get a full retry budget.
        """
        if min_age_days <= 0:
            raise ValueError(f"min_age_days must be > 0, got {min_age_days}")
        if limit <= 0:
            raise ValueError(f"limit must be > 0, got {limit}")

        threshold = (datetime.now(timezone.utc) - timedelta(days=min_age_days)).isoformat()
        now_iso = datetime.now(timezone.utc).isoformat()
        cur = conn.cursor()
        cur.execute(
            "SELECT id FROM quarantine "
            "WHERE source_table='triples' "
            "AND COALESCE(regeneration_status, 'pending') IN ('completed', 'permanent_failed') "
            "AND quarantined_at < ? "
            "ORDER BY quarantined_at ASC "
            "LIMIT ?",
            (threshold, limit),
        )
        qids = [int(row[0]) for row in cur.fetchall()]
        if not qids:
            return 0

        placeholders = ",".join("?" for _ in qids)
        cur.execute(
            f"UPDATE quarantine "
            f"SET regeneration_status='pending', "
            f"    regeneration_attempts=0, "
            f"    last_regeneration_at=?, "
            f"    last_regeneration_error=NULL "
            f"WHERE id IN ({placeholders})",
            (now_iso, *qids),
        )
        conn.commit()

        for qid in qids:
            self._log_audit(conn, AuditEntry(
                action="quarantine_reopened",
                target_table="quarantine",
                target_id=str(qid),
                details=f"Aged quarantine reopened after {min_age_days} days",
            ))

        conn.commit()
        logger.info(f"♻️ Reopened {len(qids)} aged quarantine rows for reevaluation")
        return len(qids)

    def run_temporal_reverification(
        self,
        validity_types: Optional[List[str]] = None,
        stale_after_days: int = 30,
        sample_limit: int = 5000,
        reranker: Any = None,
        progress_callback: Optional[Callable[..., Any]] = None,
    ) -> Dict[str, Any]:
        """Reverify triples whose validity policy says they should be checked again.

        This is the explicit maintenance path for periodic/event/ephemeral facts.
        It reuses the same grounding verifier as the main audit path, but the
        candidate set is selected by temporal lifecycle policy rather than by
        ``grounding_score < 0``.
        """
        if stale_after_days <= 0:
            raise ValueError(f"stale_after_days must be > 0, got {stale_after_days}")
        if sample_limit <= 0:
            raise ValueError(f"sample_limit must be > 0, got {sample_limit}")

        validity_types = validity_types or ["event", "ephemeral", "periodic"]
        normalized_validity_types = [str(v).lower() for v in validity_types if str(v).strip()]
        if not normalized_validity_types:
            return {
                "candidate_triples": 0,
                "verified_triples": 0,
                "grounded_count": 0,
                "ungrounded_count": 0,
                "duration_ms": 0.0,
            }

        conn = self._get_connection()
        start = time.time()
        try:
            cur = conn.cursor()
            cutoff = (datetime.now(timezone.utc) - timedelta(days=stale_after_days)).isoformat()
            placeholders = ",".join("?" for _ in normalized_validity_types)
            cur.execute(
                f"""
                SELECT t.triple_id
                FROM triples t
                LEFT JOIN triple_quality tq ON t.triple_id = tq.triple_id
                WHERE COALESCE(t.validity_type, 'atemporal') IN ({placeholders})
                  AND (
                        tq.last_verified IS NULL
                        OR tq.last_verified < ?
                      )
                ORDER BY COALESCE(tq.last_verified, ''), t.triple_id
                LIMIT ?
                """,
                (*normalized_validity_types, cutoff, sample_limit),
            )
            candidate_ids = [int(row[0]) for row in cur.fetchall()]

            if not candidate_ids:
                return {
                    "candidate_triples": 0,
                    "verified_triples": 0,
                    "grounded_count": 0,
                    "ungrounded_count": 0,
                    "duration_ms": round((time.time() - start) * 1000, 1),
                    "validity_types": normalized_validity_types,
                }

            if progress_callback:
                progress_callback(
                    f"Reverifying {len(candidate_ids)} temporal triples ({', '.join(normalized_validity_types)})..."
                )

            result = self.run_reranker_audit(
                reranker=reranker,
                sample_limit=sample_limit,
                triple_ids=candidate_ids,
            )
            result.update({
                "candidate_triples": len(candidate_ids),
                "validity_types": normalized_validity_types,
                "stale_after_days": stale_after_days,
                "duration_ms": round((time.time() - start) * 1000, 1),
            })
            return result
        finally:
            conn.close()

    def resolve_triple_contradictions(
        self,
        limit: int = 5000,
        min_confidence_gap: float = 0.15,
        progress_callback: Optional[Callable[..., Any]] = None,
    ) -> Dict[str, Any]:
        """Detect and mark structurally contradictory triples.

        The policy is structural, not keyword-based:
        - same normalized subject + predicate
        - different normalized object
        - overlapping temporal span -> contradiction / dispute
        - non-overlapping temporal span -> superseded
        """
        if limit <= 0:
            raise ValueError(f"limit must be > 0, got {limit}")
        if min_confidence_gap < 0:
            raise ValueError(f"min_confidence_gap must be >= 0, got {min_confidence_gap}")

        from agent.llm_knowledge_graph import normalize_entity_for_matching

        conn = self._get_connection()
        start = time.time()
        stats: Dict[str, Any] = {
            "candidate_triples": 0,
            "groups_processed": 0,
            "groups_with_conflicts": 0,
            "triples_marked_contradicted": 0,
            "triples_marked_superseded": 0,
            "triples_marked_disputed": 0,
            "triples_left_untouched": 0,
            "duration_ms": 0.0,
        }

        def _temporal_span(row: sqlite3.Row) -> Tuple[Optional[datetime], Optional[datetime]]:
            start_dt = _parse_iso_datetime(row["valid_from"]) or _parse_iso_datetime(row["observed_at"]) or _parse_iso_datetime(row["last_verified"]) 
            end_dt = _parse_iso_datetime(row["valid_to"]) or start_dt
            return start_dt, end_dt

        def _quality_score(row: sqlite3.Row) -> float:
            parts: List[float] = []
            for key, fallback in (("confidence", 0.5), ("evidence_strength", 0.5), ("grounding_score", 0.0)):
                value = row[key]
                try:
                    score = float(value) if value is not None else fallback
                except (TypeError, ValueError):
                    score = fallback
                if key == "grounding_score" and score < 0:
                    score = fallback * 0.5
                parts.append(max(0.0, min(1.0, score)))
            # Bias toward evidence + grounding; confidence is a weaker prior.
            return round((parts[0] * 0.25) + (parts[1] * 0.35) + (parts[2] * 0.40), 4)

        try:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT t.triple_id, t.doc_id, t.subject, t.predicate, t.object,
                       t.confidence, t.evidence_strength, t.validity_type,
                       t.valid_from, t.valid_to, t.observed_at, t.contradiction_state,
                       tq.grounding_score, tq.is_contradicted, tq.contradicts_triple_id,
                       tq.canonical_subject, tq.canonical_object, tq.last_verified
                FROM triples t
                LEFT JOIN triple_quality tq ON t.triple_id = tq.triple_id
                ORDER BY t.subject, t.predicate, t.triple_id
                LIMIT ?
                """,
                (limit,),
            )
            rows = cur.fetchall()
            stats["candidate_triples"] = len(rows)
            if not rows:
                return stats

            groups: Dict[Tuple[str, str], List[sqlite3.Row]] = defaultdict(list)
            for row in rows:
                subj = normalize_entity_for_matching((row["subject"] or "").strip())
                pred = normalize_entity_for_matching((row["predicate"] or "").strip())
                if not subj or not pred:
                    stats["triples_left_untouched"] += 1
                    continue
                groups[(subj, pred)].append(row)

            if progress_callback:
                progress_callback(f"Resolving contradictions in {len(groups)} subject/predicate groups...")

            now_iso = datetime.now(timezone.utc).isoformat()
            for (subj_norm, pred_norm), group_rows in groups.items():
                if len(group_rows) < 2:
                    stats["triples_left_untouched"] += len(group_rows)
                    continue

                # Build object buckets.
                object_buckets: Dict[str, List[sqlite3.Row]] = defaultdict(list)
                for row in group_rows:
                    obj_norm = normalize_entity_for_matching((row["object"] or "").strip())
                    if obj_norm:
                        object_buckets[obj_norm].append(row)

                distinct_objects = [obj for obj in object_buckets if object_buckets[obj]]
                if len(distinct_objects) < 2:
                    stats["triples_left_untouched"] += len(group_rows)
                    continue

                stats["groups_processed"] += 1

                # Find the best-supported triple in the group.
                scored_rows = sorted(
                    group_rows,
                    key=lambda row: (
                        _quality_score(row),
                        _parse_iso_datetime(row["last_verified"]) or datetime.min.replace(tzinfo=timezone.utc),
                        int(row["triple_id"]),
                    ),
                    reverse=True,
                )
                winner = scored_rows[0]
                winner_obj = normalize_entity_for_matching((winner["object"] or "").strip())
                winner_span = _temporal_span(winner)
                winner_score = _quality_score(winner)

                conflict_found = False
                updates: List[Tuple[int, str, int, Optional[int], str, str]] = []
                # tuple: (triple_id, contradiction_state, is_contradicted, contradicts_triple_id, canonical_subject, canonical_object)
                for row in group_rows:
                    if int(row["triple_id"]) == int(winner["triple_id"]):
                        updates.append((
                            int(row["triple_id"]),
                            "none",
                            0,
                            None,
                            winner["subject"],
                            winner["object"],
                        ))
                        continue

                    row_obj = normalize_entity_for_matching((row["object"] or "").strip())
                    if row_obj == winner_obj:
                        stats["triples_left_untouched"] += 1
                        continue

                    row_span = _temporal_span(row)
                    overlap = True
                    if winner_span[0] and winner_span[1] and row_span[0] and row_span[1]:
                        overlap = not (winner_span[1] < row_span[0] or row_span[1] < winner_span[0])

                    row_score = _quality_score(row)
                    score_gap = winner_score - row_score
                    if overlap:
                        state = "contradicted" if score_gap >= min_confidence_gap else "disputed"
                    else:
                        state = "superseded"

                    conflict_found = True
                    if state == "contradicted":
                        stats["triples_marked_contradicted"] += 1
                    elif state == "superseded":
                        stats["triples_marked_superseded"] += 1
                    else:
                        stats["triples_marked_disputed"] += 1

                    updates.append((
                        int(row["triple_id"]),
                        state,
                        1 if state == "contradicted" else 0,
                        int(winner["triple_id"]),
                        winner["subject"],
                        winner["object"],
                    ))

                if not conflict_found:
                    stats["triples_left_untouched"] += len(group_rows)
                    continue

                stats["groups_with_conflicts"] += 1

                for triple_id, state, is_contradicted, contradicts_id, canonical_subject, canonical_object in updates:
                    cur.execute(
                        "UPDATE triples SET contradiction_state=? WHERE triple_id=?",
                        (state, triple_id),
                    )
                    cur.execute(
                        """
                        INSERT INTO triple_quality(
                            triple_id, is_contradicted, contradicts_triple_id,
                            canonical_subject, canonical_object, last_verified, action_taken
                        ) VALUES (?, ?, ?, ?, ?, ?, 'contradiction_resolution')
                        ON CONFLICT(triple_id) DO UPDATE SET
                            is_contradicted=excluded.is_contradicted,
                            contradicts_triple_id=excluded.contradicts_triple_id,
                            canonical_subject=excluded.canonical_subject,
                            canonical_object=excluded.canonical_object,
                            last_verified=excluded.last_verified,
                            action_taken=excluded.action_taken
                        """,
                        (
                            triple_id,
                            is_contradicted,
                            contradicts_id,
                            canonical_subject,
                            canonical_object,
                            now_iso,
                        ),
                    )

                    self._log_audit(conn, AuditEntry(
                        action="contradiction_resolution",
                        target_table="triples",
                        target_id=str(triple_id),
                        details=f"state={state}; canonical={canonical_subject!r} -> {canonical_object!r}; winner={winner['triple_id']}",
                    ))

            conn.commit()
            stats["duration_ms"] = round((time.time() - start) * 1000, 1)
            logger.info(
                "🧭 Contradiction resolution: groups=%d, contradicted=%d, superseded=%d, disputed=%d",
                stats["groups_with_conflicts"],
                stats["triples_marked_contradicted"],
                stats["triples_marked_superseded"],
                stats["triples_marked_disputed"],
            )
            return stats
        finally:
            conn.close()

    # =================================================================
    # RETRIEVAL FEEDBACK
    # =================================================================

    def record_retrieval_feedback(
        self,
        conn: sqlite3.Connection,
        query: str,
        chunk_ids: List[str],
        chunk_scores: List[float],
        answer_excerpt: str = "",
        user_feedback: int = 0,
    ) -> None:
        """Record retrieval feedback for adaptive scoring."""
        conn.execute(
            "INSERT INTO retrieval_feedback(timestamp, query, chunk_ids, chunk_scores, answer_excerpt, user_feedback) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                datetime.now(timezone.utc).isoformat(),
                query,
                json.dumps(chunk_ids),
                json.dumps(chunk_scores),
                answer_excerpt[:500],
                user_feedback,
            ),
        )
        conn.commit()

    def compute_chunk_utility_scores(self, conn: sqlite3.Connection) -> Dict[str, float]:
        """
        Compute chunk utility scores from retrieval feedback.
        
        Chunks that are frequently retrieved WITH positive feedback get higher utility.
        Returns {chunk_id_str: utility_score}.
        """
        cur = conn.cursor()
        cur.execute("SELECT chunk_ids, user_feedback FROM retrieval_feedback WHERE user_feedback != 0")

        chunk_positive: Counter[str] = Counter()
        chunk_negative: Counter[str] = Counter()
        chunk_total: Counter[str] = Counter()

        for row in cur.fetchall():
            try:
                ids = json.loads(row["chunk_ids"] or "[]")
                feedback = row["user_feedback"]
                for cid in ids:
                    chunk_total[str(cid)] += 1
                    if feedback > 0:
                        chunk_positive[str(cid)] += 1
                    elif feedback < 0:
                        chunk_negative[str(cid)] += 1
            except (json.JSONDecodeError, TypeError):
                pass

        utility = {}
        for cid in chunk_total:
            pos = chunk_positive.get(cid, 0)
            neg = chunk_negative.get(cid, 0)
            total = chunk_total[cid]
            # Wilson score interval lower bound (simplified)
            if total > 0:
                p = pos / total
                z = 1.96  # 95% confidence
                denominator = 1 + z * z / total
                center = (p + z * z / (2 * total)) / denominator
                spread = z * math.sqrt((p * (1 - p) + z * z / (4 * total)) / total) / denominator
                utility[cid] = max(0.0, center - spread)
            else:
                utility[cid] = 0.5  # neutral

        return utility

    # =================================================================
    # FULL STRUCTURAL AUDIT (manual trigger)
    # =================================================================

    def run_structural_audit(
        self,
        progress_callback: Optional[Callable[..., Any]] = None,
    ) -> AuditReport:
        """
        Run a full structural audit of the RAG database.
        
        This does NOT use the LLM or reranker — purely structural checks.
        Safe to run while the bot is active.
        """
        report = AuditReport(timestamp=datetime.now(timezone.utc).isoformat())
        start = time.time()
        conn = self._get_connection()

        try:
            cur = conn.cursor()

            # Basic counts
            cur.execute("SELECT COUNT(*) FROM chunks")
            report.total_chunks = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM triples")
            report.total_triples = cur.fetchone()[0]

            if progress_callback:
                progress_callback("Checking orphans...")

            # 1. Orphans
            orphan_chunks, orphan_triples = self.find_orphans(conn)
            report.orphan_chunks = len(orphan_chunks)
            report.orphan_triples = len(orphan_triples)

            if progress_callback:
                progress_callback("Checking embedding dimensions...")

            # 2. Embedding dimensions
            dim_mismatches = self.check_embedding_dimensions(conn)
            report.embedding_dim_mismatch = len(dim_mismatches)

            if progress_callback:
                progress_callback("Checking short/URL chunks...")

            # 3. Short chunks
            short = self.find_short_chunks(conn)
            report.short_chunks = len(short)

            # 4. URL dumps
            url_dumps = self.find_url_dump_chunks(conn)
            report.url_dump_chunks = len(url_dumps)

            if progress_callback:
                progress_callback("Scanning for boilerplate...")

            # 5. Boilerplate
            boilerplate = self.find_boilerplate_chunks(conn)
            report.boilerplate_chunks = len(boilerplate)

            if progress_callback:
                progress_callback("Scanning for near-duplicates...")

            # 6. Near-duplicates
            duplicates = self.find_near_duplicates(conn)
            report.near_duplicates = len(duplicates)

            if progress_callback:
                progress_callback("Computing age distribution...")

            # 7. ★ SOTA v2: Age distribution (informational, NOT a penalty)
            report.age_distribution = self.get_age_distribution(conn)

            if progress_callback:
                progress_callback("Scoring chunk quality + detecting defects...")

            # 8. ★ SOTA v3: Incremental chunk quality scoring with batch DB writes
            # KEY OPTIMISATION: Only score chunks that have NO existing quality record
            # (new since last audit or ingest). Chunks scored at ingest time are reused.
            # This turns a 237s full-scan into a fast incremental check.
            now_iso = datetime.now(timezone.utc).isoformat()
            
            # Find chunks that need scoring (LEFT JOIN: no existing quality record)
            cur.execute(
                "SELECT c.doc_id, c.chunk_id, c.text, c.metadata "
                "FROM chunks c "
                "LEFT JOIN chunk_quality cq ON c.doc_id = cq.doc_id AND c.chunk_id = cq.chunk_id "
                "WHERE cq.doc_id IS NULL"
            )
            unscored_rows = cur.fetchall()
            
            # Score only the unscored chunks
            quality_batch: List[Tuple] = []
            scored_new = 0
            for idx, row in enumerate(unscored_rows):
                text = row["text"] or ""
                metadata = row["metadata"] or "{}"
                score = self.score_chunk_structural(text, metadata)
                content_type = self.detect_content_type(text, metadata)
                defects = self.detect_defects(text, metadata)
                defect_str = ",".join(sorted(defects)) if defects else ""
                quality_batch.append((
                    row["doc_id"], row["chunk_id"], score, content_type, defect_str, now_iso
                ))
                scored_new += 1
                if progress_callback and idx > 0 and idx % 10000 == 0:
                    progress_callback(f"Scoring new chunks... {idx}/{len(unscored_rows)}")
            
            # Batch write new quality scores
            if quality_batch:
                conn.executemany(
                    "INSERT OR REPLACE INTO chunk_quality"
                    "(doc_id, chunk_id, structural_score, content_type, defect_flags, last_checked) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    quality_batch,
                )
            
            # Now read aggregated stats from the FULL chunk_quality table
            cur.execute(
                "SELECT COUNT(*) FROM chunk_quality WHERE structural_score < 0.3"
            )
            low_quality = cur.fetchone()[0]
            
            cur.execute(
                "SELECT COUNT(*) FROM chunk_quality "
                "WHERE defect_flags != '' AND defect_flags IS NOT NULL"
            )
            defect_count = cur.fetchone()[0]
            
            cur.execute(
                "SELECT content_type, COUNT(*) as cnt FROM chunk_quality GROUP BY content_type"
            )
            content_types: Dict[str, int] = {}
            for r in cur.fetchall():
                content_types[r["content_type"] or "prose"] = r["cnt"]
            
            report.low_quality_chunks = low_quality
            report.defect_chunks = defect_count
            report.content_type_distribution = content_types
            
            if scored_new > 0:
                logger.info(f"  Scored {scored_new} new chunks (incremental), reused {report.total_chunks - scored_new} existing")

            if progress_callback:
                progress_callback("Scoring predicate quality...")

            # 9. ★ SOTA v3: Predicate quality with batch DB writes
            pred_scores = self.score_all_triples_predicates(conn)
            generic = sum(1 for s in pred_scores.values() if s < 0.3)
            report.generic_predicate_triples = generic
            # Batch-persist all predicate scores at once (was: 357k individual INSERTs)
            pred_batch = [
                (tid, score, now_iso) for tid, score in pred_scores.items()
            ]
            if pred_batch:
                conn.executemany(
                    "INSERT OR REPLACE INTO triple_quality(triple_id, predicate_info_value, last_verified) "
                    "VALUES (?, ?, ?)",
                    pred_batch,
                )

            # 10. Regex fallback triples
            regex_triples = self.find_regex_fallback_triples(conn)
            report.regex_fallback_triples = len(regex_triples)

            conn.commit()

            # Log audit
            self._log_audit(conn, AuditEntry(
                action="structural_audit",
                details=json.dumps(asdict(report), ensure_ascii=False),
            ))
            conn.commit()

        except Exception as e:
            report.errors.append(str(e))
            logger.error(f"❌ Structural audit error: {e}")
        finally:
            conn.close()

        report.duration_ms = (time.time() - start) * 1000
        logger.info(f"📊 {report.summary()}")
        
        # ★ SOTA v2: Record trend data for comparison with previous audits
        try:
            self.record_audit_trend(report)
        except Exception as e:
            logger.warning(f"⚠️ Failed to record trend: {e}")
        
        return report

    # =================================================================
    # RERANKER-BASED AUDIT (uses cross-encoder, no LLM)
    # =================================================================

    def run_reranker_audit(
        self,
        reranker: Any = None,
        sample_limit: int = 5000,
        triple_ids: Optional[List[int]] = None,
        progress_callback: Optional[Callable[..., Any]] = None,
    ) -> Dict[str, Any]:
        """
        Run KG grounding verification using the cross-encoder reranker.
        
        This does NOT use the LLM. The cross-encoder is a separate model.
        Duration scales with sample_limit (~1-3 min per 1000 triples on GPU).
        """
        rr = reranker or self._reranker
        conn = self._get_connection()
        start = time.time()

        try:
            if progress_callback:
                progress_callback(f"Verifying triple grounding via cross-encoder ({sample_limit} samples)...")

            grounding_scores = self.verify_triples_grounding(
                conn,
                rr,
                sample_limit=sample_limit,
                triple_ids=triple_ids,
            )

            # Persist scores
            now_iso = datetime.now(timezone.utc).isoformat()
            for triple_id, score in grounding_scores.items():
                conn.execute(
                    "UPDATE triple_quality SET grounding_score=?, last_verified=? WHERE triple_id=?",
                    (score, now_iso, triple_id),
                )
                # Also insert if not exists
                conn.execute(
                    "INSERT OR IGNORE INTO triple_quality(triple_id, grounding_score, last_verified) "
                    "VALUES (?, ?, ?)",
                    (triple_id, score, now_iso),
                )
            conn.commit()

            ungrounded = sum(1 for s in grounding_scores.values() if 0 <= s < 0.3)
            self._log_audit(conn, AuditEntry(
                action="reranker_audit",
                details=json.dumps({
                    "total_triples": len(grounding_scores),
                    "ungrounded": ungrounded,
                    "duration_ms": (time.time() - start) * 1000,
                }),
            ))
            conn.commit()

            result = {
                "total_verified": len(grounding_scores),
                "ungrounded_count": ungrounded,
                "grounded_count": sum(1 for s in grounding_scores.values() if s >= 0.3),
                "duration_ms": (time.time() - start) * 1000,
            }
            logger.info(f"📊 Reranker audit: {result}")
            return result
        finally:
            conn.close()

    # =================================================================
    # ★ SOTA v2: REMEDIATION WITH SEVERITY TIERS + DRY-RUN + FEEDBACK PROTECTION
    # =================================================================

    def _compute_feedback_protection_set(self, conn: sqlite3.Connection) -> Set[str]:
        """
        ★ SOTA v2: Compute set of chunk IDs that should NEVER be auto-quarantined
        because they have significant positive retrieval feedback.
        
        Uses Wilson Score lower bound > 0.3 with at least 3 retrievals.
        """
        utility = self.compute_chunk_utility_scores(conn)
        # Also need retrieval counts
        cur = conn.cursor()
        cur.execute("SELECT chunk_ids FROM retrieval_feedback")
        chunk_counts: Counter[str] = Counter()
        for row in cur.fetchall():
            try:
                ids = json.loads(row["chunk_ids"] or "[]")
                for cid in ids:
                    chunk_counts[str(cid)] += 1
            except (json.JSONDecodeError, TypeError):
                pass
        
        protected = set()
        for cid, score in utility.items():
            if score > 0.3 and chunk_counts.get(cid, 0) >= 3:
                protected.add(cid)
        
        return protected

    def run_remediation(
        self,
        quarantine_orphans: bool = True,
        quarantine_duplicates: bool = True,
        quarantine_boilerplate: bool = True,
        quarantine_defects: bool = True,
        quarantine_ungrounded: bool = False,
        quarantine_generic_predicates: bool = False,
        min_structural_score: float = 0.2,
        min_grounding_score: float = 0.15,
        max_predicate_score: float = 0.1,
        dry_run: bool = False,
        progress_callback: Optional[Callable[..., Any]] = None,
    ) -> Dict[str, Any]:
        """
        ★ SOTA v2: Execute remediation with severity tiers, dry-run, and feedback protection.
        
        Severity Tiers:
          Tier 0 (AUTO): Orphans, encoding garbage, trivial chunks — always safe to remove
          Tier 1 (RECOMMENDED): Duplicates, boilerplate, cookie banners — quarantine recommended
          Tier 2 (OBSERVE): Low quality, generic predicates — log but don't auto-quarantine
        
        Feedback Protection:
          Chunks with Wilson Score > 0.3 and ≥3 retrievals are NEVER auto-quarantined.
          The user found these chunks useful — quality signals may be wrong.
        
        Dry Run:
          When dry_run=True, returns what WOULD be quarantined without taking action.
        
        Returns stats dict with counts and dry_run plan if applicable.
        """
        conn = self._get_connection()
        stats: Dict[str, Any] = {
            "dry_run": dry_run,
            "tier_0_quarantined": 0,
            "tier_1_quarantined": 0,
            "tier_2_logged": 0,
            "feedback_protected": 0,
            "orphans_quarantined": 0,
            "duplicates_quarantined": 0,
            "boilerplate_quarantined": 0,
            "defect_quarantined": 0,
            "low_quality_quarantined": 0,
            "ungrounded_quarantined": 0,
            "generic_pred_quarantined": 0,
            "reverse_signal_chunks": 0,
            "plan": [],  # For dry-run: list of planned actions
        }

        try:
            # Compute feedback protection set
            if progress_callback:
                progress_callback("Computing feedback protection...")
            protected = self._compute_feedback_protection_set(conn)
            if protected:
                logger.info(f"🛡️ {len(protected)} chunks protected by positive feedback")

            def _safe_quarantine_chunks(chunk_ids, reason, tier):
                """Quarantine with feedback protection."""
                safe_ids = []
                for doc_id, chunk_id in chunk_ids:
                    cid_str = f"{doc_id}:{chunk_id}"
                    if cid_str in protected:
                        stats["feedback_protected"] += 1
                        logger.debug(f"🛡️ Protected from quarantine: {cid_str} (positive feedback)")
                        continue
                    safe_ids.append((doc_id, chunk_id))
                
                if dry_run:
                    stats["plan"].append({
                        "action": "quarantine_chunks",
                        "reason": reason,
                        "tier": tier,
                        "count": len(safe_ids),
                    })
                    return len(safe_ids)
                elif safe_ids:
                    return self.quarantine_chunks(conn, safe_ids, reason)
                return 0

            def _safe_quarantine_triples(triple_ids, reason, tier):
                """Quarantine triples."""
                if dry_run:
                    stats["plan"].append({
                        "action": "quarantine_triples",
                        "reason": reason,
                        "tier": tier,
                        "count": len(triple_ids),
                    })
                    return len(triple_ids)
                elif triple_ids:
                    return self.quarantine_triples(conn, triple_ids, reason)
                return 0

            # ══════════════════════════════════════════════════════════
            # TIER 0 (AUTO): Always safe to remove
            # ══════════════════════════════════════════════════════════
            
            if quarantine_orphans:
                if progress_callback:
                    progress_callback("[Tier 0] Quarantining orphans...")
                orphan_chunks, orphan_triples = self.find_orphans(conn)
                if orphan_chunks:
                    cnt = _safe_quarantine_chunks(orphan_chunks, "orphan_no_parent_document", 0)
                    stats["orphans_quarantined"] += cnt
                    stats["tier_0_quarantined"] += cnt
                if orphan_triples:
                    cnt = _safe_quarantine_triples(
                        [t[0] for t in orphan_triples], "orphan_no_parent_document", 0
                    )
                    stats["orphans_quarantined"] += cnt
                    stats["tier_0_quarantined"] += cnt

            if quarantine_defects:
                if progress_callback:
                    progress_callback("[Tier 0] Quarantining defect chunks...")
                cur = conn.cursor()
                cur.execute(
                    "SELECT doc_id, chunk_id, defect_flags FROM chunk_quality "
                    "WHERE defect_flags != '' AND defect_flags IS NOT NULL AND action_taken='none'"
                )
                defect_ids = []
                for r in cur.fetchall():
                    flags = (r["defect_flags"] or "").split(",")
                    # Tier 0: encoding_garbage, trivial
                    if any(f in ("encoding_garbage", "trivial") for f in flags):
                        defect_ids.append((r["doc_id"], r["chunk_id"]))
                if defect_ids:
                    cnt = _safe_quarantine_chunks(defect_ids, "hard_defect_tier_0", 0)
                    stats["defect_quarantined"] += cnt
                    stats["tier_0_quarantined"] += cnt

            # ══════════════════════════════════════════════════════════
            # TIER 1 (RECOMMENDED): Quarantine recommended
            # ══════════════════════════════════════════════════════════
            
            if quarantine_duplicates:
                if progress_callback:
                    progress_callback("[Tier 1] Quarantining duplicates...")
                duplicates = self.find_near_duplicates(conn)
                to_quarantine = set()
                for doc_a, cid_a, doc_b, cid_b, sim in duplicates:
                    cur = conn.cursor()
                    cur.execute("SELECT structural_score FROM chunk_quality WHERE doc_id=? AND chunk_id=?", (doc_a, cid_a))
                    row_a = cur.fetchone()
                    cur.execute("SELECT structural_score FROM chunk_quality WHERE doc_id=? AND chunk_id=?", (doc_b, cid_b))
                    row_b = cur.fetchone()
                    score_a = row_a["structural_score"] if row_a else 0.5
                    score_b = row_b["structural_score"] if row_b else 0.5
                    if score_a >= score_b:
                        to_quarantine.add((doc_b, cid_b))
                    else:
                        to_quarantine.add((doc_a, cid_a))
                if to_quarantine:
                    cnt = _safe_quarantine_chunks(list(to_quarantine), "near_duplicate", 1)
                    stats["duplicates_quarantined"] = cnt
                    stats["tier_1_quarantined"] += cnt

            if quarantine_boilerplate:
                if progress_callback:
                    progress_callback("[Tier 1] Quarantining boilerplate...")
                boilerplate = self.find_boilerplate_chunks(conn)
                bp_ids = list(set((doc_id, cid) for doc_id, cid, _, _ in boilerplate))
                if bp_ids:
                    cnt = _safe_quarantine_chunks(bp_ids, "cross_document_boilerplate", 1)
                    stats["boilerplate_quarantined"] = cnt
                    stats["tier_1_quarantined"] += cnt

            if quarantine_defects:
                # Tier 1 defects: cookie_banner, pure_navigation, url_dump
                cur = conn.cursor()
                cur.execute(
                    "SELECT doc_id, chunk_id, defect_flags FROM chunk_quality "
                    "WHERE defect_flags != '' AND defect_flags IS NOT NULL AND action_taken='none'"
                )
                tier1_defect_ids = []
                for r in cur.fetchall():
                    flags = (r["defect_flags"] or "").split(",")
                    if any(f in ("cookie_banner", "pure_navigation", "url_dump") for f in flags):
                        # Not already quarantined in tier 0
                        if not any(f in ("encoding_garbage", "trivial") for f in flags):
                            tier1_defect_ids.append((r["doc_id"], r["chunk_id"]))
                if tier1_defect_ids:
                    cnt = _safe_quarantine_chunks(tier1_defect_ids, "defect_tier_1", 1)
                    stats["defect_quarantined"] += cnt
                    stats["tier_1_quarantined"] += cnt

            # ══════════════════════════════════════════════════════════
            # TIER 2 (OBSERVE): Log but don't auto-quarantine
            # ══════════════════════════════════════════════════════════
            
            if progress_callback:
                progress_callback("[Tier 2] Logging low quality for observation...")
            cur = conn.cursor()
            cur.execute(
                "SELECT doc_id, chunk_id FROM chunk_quality WHERE structural_score < ? AND action_taken='none'",
                (min_structural_score,),
            )
            low_q = [(r["doc_id"], r["chunk_id"]) for r in cur.fetchall()]
            if low_q:
                stats["low_quality_quarantined"] = len(low_q)
                stats["tier_2_logged"] += len(low_q)
                # Log but don't quarantine in tier 2 (observe mode)
                self._log_audit(conn, AuditEntry(
                    action="tier_2_observe",
                    details=f"{len(low_q)} low-quality chunks observed (score < {min_structural_score})",
                ))

            if quarantine_ungrounded:
                if progress_callback:
                    progress_callback("[Tier 1] Quarantining ungrounded triples...")
                cur.execute(
                    "SELECT triple_id FROM triple_quality WHERE grounding_score >= 0 AND grounding_score < ?",
                    (min_grounding_score,),
                )
                ungrounded = [r["triple_id"] for r in cur.fetchall()]
                if ungrounded:
                    cnt = _safe_quarantine_triples(
                        ungrounded, f"ungrounded_score_below_{min_grounding_score}", 1
                    )
                    stats["ungrounded_quarantined"] = cnt
                    stats["tier_1_quarantined"] += cnt

            if quarantine_generic_predicates:
                if progress_callback:
                    progress_callback("[Tier 2] Logging generic predicate triples...")
                cur.execute(
                    "SELECT triple_id FROM triple_quality WHERE predicate_info_value < ?",
                    (max_predicate_score,),
                )
                generic = [r["triple_id"] for r in cur.fetchall()]
                if generic:
                    stats["generic_pred_quarantined"] = len(generic)
                    stats["tier_2_logged"] += len(generic)
                    self._log_audit(conn, AuditEntry(
                        action="tier_2_observe",
                        details=f"{len(generic)} generic-predicate triples observed (IDF < {max_predicate_score})",
                    ))

            # ══════════════════════════════════════════════════════════
            # ★ SOTA v3: REVERSE SIGNAL — Triple failures → chunk flagging
            # ══════════════════════════════════════════════════════════
            if progress_callback:
                progress_callback("[Tier 2] Checking reverse signal: bad triples → chunks...")
            bad_triple_chunks = self.detect_chunks_with_bad_triples(
                conn,
                grounding_threshold=min_grounding_score,
                failure_ratio=0.5,
            )
            if bad_triple_chunks:
                stats["reverse_signal_chunks"] = len(bad_triple_chunks)
                stats["tier_2_logged"] += len(bad_triple_chunks)
                # Mark these chunks with lowered structural score in chunk_quality
                for doc_id, chunk_id, ratio in bad_triple_chunks:
                    try:
                        cur = conn.cursor()
                        cur.execute(
                            "SELECT structural_score FROM chunk_quality WHERE doc_id=? AND chunk_id=?",
                            (doc_id, chunk_id),
                        )
                        row = cur.fetchone()
                        if row:
                            # Penalise: multiply current score by (1 - failed_ratio * 0.5)
                            old_score = row["structural_score"] or 0.5
                            penalty = 1.0 - (ratio * 0.5)
                            new_score = max(0.1, old_score * penalty)
                            cur.execute(
                                "UPDATE chunk_quality SET structural_score=?, last_checked=? "
                                "WHERE doc_id=? AND chunk_id=?",
                                (round(new_score, 4), datetime.now(timezone.utc).isoformat(),
                                 doc_id, chunk_id),
                            )
                    except Exception as e:
                        logger.debug(f"Reverse signal update failed for {doc_id}:{chunk_id}: {e}")
                self._log_audit(conn, AuditEntry(
                    action="reverse_signal",
                    details=f"{len(bad_triple_chunks)} chunks penalised due to failed triple grounding",
                ))

            conn.commit()
        except Exception as e:
            logger.error(f"❌ Remediation error: {e}")
        finally:
            conn.close()

        total = stats["tier_0_quarantined"] + stats["tier_1_quarantined"]
        mode = "DRY-RUN" if dry_run else "EXECUTED"
        logger.info(
            f"🧹 Remediation [{mode}]: {total} items quarantined, "
            f"{stats['tier_2_logged']} observed, "
            f"{stats['feedback_protected']} protected by feedback"
        )
        return stats

    # =================================================================
    # ★ SOTA v3: TRIPLE REGENERATION FROM QUARANTINE VIA LLM
    # =================================================================

    def regenerate_quarantined_triples(
        self,
        dry_run: bool = False,
        batch_size: int = 50,
        min_grounding_score: float = 0.3,
        max_retry_attempts: int = 3,
        max_regeneration_generations: int = 2,
        progress_callback: Optional[Callable[..., Any]] = None,
    ) -> Dict[str, Any]:
        """
        ★ SOTA v3: Re-extract triples from source chunks for quarantined triples.

        Closes the quality loop:
            Quarantine (bad triples removed) → Identify source chunks →
            LLM re-extraction → Cross-encoder grounding verification →
            Only grounded triples inserted → Audit logged

        Root-cause approach (NOT a workaround):
        - Quarantined triples were bad because they were regex-extracted or
          hallucinated. The source chunks still contain valid information.
        - Re-extraction via LLM produces semantically correct triples.
        - Immediate grounding verification prevents re-inserting bad triples.
        - Failure taxonomy (DLQ pattern): permanent causes (invalid backup)
          go straight to ``permanent_failed``; transient causes (source chunk
          missing, extraction/grounding failure) stay ``failed`` and retryable
          until ``max_retry_attempts``, then transition to ``permanent_failed``.
        - Anti-loop via generation counter: regenerated triples may be
          regenerated again up to ``max_regeneration_generations`` lineage
          steps, so bad regenerations remain fixable.

        Args:
            dry_run: If True, only report what WOULD be regenerated.
            batch_size: Max quarantine entries to process per call.
            min_grounding_score: Minimum cross-encoder score for new triples.
            max_retry_attempts: Retry budget before permanent_failed.
            max_regeneration_generations: Max regeneration lineage depth.
            progress_callback: Optional callable for progress updates.

        Returns:
            Stats dict with counts.
        """
        stats: Dict[str, Any] = {
            "dry_run": dry_run,
            "quarantine_entries_processed": 0,
            "source_chunks_found": 0,
            "source_chunks_missing": 0,
            "source_chunk_entries_missing": 0,
            "source_chunk_groups_total": 0,
            "recoverable_quarantine_entries": 0,
            "unrecoverable_quarantine_entries": 0,
            "doc_id_remapped": 0,
            "triples_extracted": 0,
            "triples_grounded": 0,
            "triples_inserted": 0,
            "triples_duplicate_skipped": 0,
            "triples_ungrounded_skipped": 0,
            "already_regenerated_skipped": 0,
            "quarantine_marked_completed": 0,
            "quarantine_marked_failed": 0,
            "quarantine_marked_permanent_failed": 0,
            "missing_source_doc_ids_sample": [],
            "errors": [],
        }
        grounding_scores: List[float] = []

        def _mark_quarantine_state(
            cur: sqlite3.Cursor,
            qid: int,
            *,
            state: str,
            error_text: Optional[str] = None,
        ) -> None:
            now_iso = datetime.now(timezone.utc).isoformat()
            # Transient failures escalate to permanent_failed at the retry limit
            if state == "failed":
                cur.execute(
                    "SELECT COALESCE(regeneration_attempts, 0) FROM quarantine WHERE id = ?",
                    (qid,),
                )
                row = cur.fetchone()
                attempts_after = (int(row[0]) if row else 0) + 1
                if attempts_after >= max_retry_attempts:
                    state = "permanent_failed"
            cur.execute(
                "UPDATE quarantine "
                "SET regeneration_status = ?, "
                "regeneration_attempts = COALESCE(regeneration_attempts, 0) + 1, "
                "last_regeneration_at = ?, "
                "last_regeneration_error = ? "
                "WHERE id = ?",
                (state, now_iso, error_text, qid),
            )
            if state == "permanent_failed":
                stats["quarantine_marked_permanent_failed"] += 1

        def _upsert_kg_entities_for_triple(
            cur: sqlite3.Cursor,
            *,
            doc_id: str,
            subject: str,
            obj: str,
        ) -> None:
            from agent.llm_knowledge_graph import normalize_entity_for_matching

            for entity_text in (subject, obj):
                normalized = normalize_entity_for_matching((entity_text or "").strip())
                if not normalized:
                    continue
                cur.execute(
                    "SELECT entity_id, frequency FROM kg_entities WHERE normalized_text = ?",
                    (normalized,),
                )
                existing = cur.fetchone()
                if existing:
                    cur.execute(
                        "UPDATE kg_entities SET frequency = ? WHERE entity_id = ?",
                        (int(existing["frequency"] or 0) + 1, existing["entity_id"]),
                    )
                else:
                    cur.execute(
                        "INSERT INTO kg_entities(entity_text, normalized_text, entity_type, frequency, first_seen_doc_id) "
                        "VALUES (?, ?, 'entity', 1, ?)",
                        (entity_text, normalized, doc_id),
                    )

        # ── 1. Initialize LLM KG Extractor ─────────────────────────
        kg_extractor = None  # type: ignore[assignment]
        if not dry_run:
            try:
                from agent.llm_knowledge_graph import LLMKnowledgeGraphExtractor
                kg_extractor = LLMKnowledgeGraphExtractor()
                # Verify LLM is actually loaded
                if (kg_extractor.llm_client is None
                        or not hasattr(kg_extractor.llm_client, 'llm')
                        or kg_extractor.llm_client.llm is None):
                    msg = "LLM nicht geladen — Triple-Regeneration benötigt ein geladenes LLM-Modell"
                    logger.error(f"❌ {msg}")
                    stats["errors"].append(msg)
                    return stats
            except ImportError as e:
                msg = f"LLMKnowledgeGraphExtractor nicht importierbar: {e}"
                logger.error(f"❌ {msg}")
                stats["errors"].append(msg)
                return stats

        # ── 2. Get reranker for grounding verification ──────────────
        rr = _canonical_reranker(self._reranker)
        if rr is None and not dry_run:
            from agent.reranker import get_reranker

            rr = get_reranker()
            self._reranker = rr

        if rr is not None and hasattr(rr, '_ensure_loaded'):
            rr._ensure_loaded()
        if rr is not None and hasattr(rr, 'is_available') and not rr.is_available:
            rr = None

        if rr is None and not dry_run:
            msg = "Kein Reranker verfügbar — neue Triples können nicht verifiziert werden"
            logger.error(f"❌ {msg}")
            stats["errors"].append(msg)
            return stats

        conn = self._get_connection()
        start_time = time.time()

        try:
            cur = conn.cursor()

            # ── 3. Find quarantined triples eligible for regeneration ──
            if progress_callback:
                progress_callback("Suche quarantinierte Triples für Regeneration...")

            cur.execute("""
                                SELECT q.id, q.source_id, q.data_backup, q.reason
                FROM quarantine q
                WHERE q.source_table = 'triples'
                                    AND (
                                        COALESCE(q.regeneration_status, 'pending') = 'pending'
                                        OR (
                                            COALESCE(q.regeneration_status, 'pending') = 'failed'
                                            AND COALESCE(q.regeneration_attempts, 0) < ?
                                        )
                                    )
                ORDER BY CASE COALESCE(q.regeneration_status, 'pending')
                             WHEN 'pending' THEN 0 ELSE 1 END,
                         q.quarantined_at ASC
                LIMIT ?
            """, (max_retry_attempts, batch_size,))
            quarantine_rows = cur.fetchall()

            if not quarantine_rows:
                logger.info("✅ Keine quarantinierten Triples zur Regeneration gefunden")
                return stats

            logger.info(f"🔄 {len(quarantine_rows)} quarantinierte Triples zur Regeneration gefunden")

            # ── 4. Classify quarantine rows by actual recoverability ─────
            already_regenerated = 0
            recoverable_groups: Dict[Tuple[str, Optional[int]], Dict[str, Any]] = {}
            irrecoverable_rows: List[Dict[str, Any]] = []

            for qrow in quarantine_rows:
                try:
                    backup = json.loads(qrow["data_backup"])
                except (json.JSONDecodeError, TypeError):
                    err = f"Quarantine #{qrow['id']}: Invalid backup data"
                    stats["errors"].append(err)
                    irrecoverable_rows.append({
                        "quarantine_id": qrow["id"],
                        "error": err,
                        "permanent": True,
                    })
                    continue

                # Anti-loop via lineage depth instead of a binary flag:
                # regenerated triples stay fixable up to N generations.
                metadata_str = backup.get("metadata", "{}")
                try:
                    meta = json.loads(metadata_str) if isinstance(metadata_str, str) else (metadata_str or {})
                except (json.JSONDecodeError, TypeError):
                    meta = {}

                generation = int(meta.get("regeneration_generation", 0) or 0)
                if generation == 0 and meta.get("regenerated_from_quarantine"):
                    generation = 1  # legacy rows without generation counter
                if generation >= max_regeneration_generations:
                    already_regenerated += 1
                    if not dry_run:
                        _mark_quarantine_state(
                            cur,
                            qrow["id"],
                            state="permanent_failed",
                            error_text=(
                                f"Regeneration lineage exhausted "
                                f"(generation {generation} >= {max_regeneration_generations})"
                            ),
                        )
                    continue

                resolved_doc_id, resolved_chunk_id, chunk_text, resolution_kind = self._resolve_quarantine_regeneration_source(
                    cur,
                    backup,
                    reranker=rr,
                )

                if not chunk_text or len(chunk_text.strip()) < 50:
                    stats["source_chunks_missing"] += 1
                    stats["source_chunk_entries_missing"] += 1
                    doc_id = str(backup.get("doc_id", "") or "").strip()
                    if doc_id and len(stats["missing_source_doc_ids_sample"]) < 20:
                        if doc_id not in stats["missing_source_doc_ids_sample"]:
                            stats["missing_source_doc_ids_sample"].append(doc_id)
                    irrecoverable_rows.append({
                        "quarantine_id": qrow["id"],
                        "doc_id": doc_id,
                        "error": (
                            f"Source chunk unavailable for regeneration ({resolution_kind}): "
                            f"{doc_id}:{resolved_chunk_id}"
                        ),
                    })
                    continue

                stats["source_chunks_found"] += 1
                doc_id = resolved_doc_id
                if doc_id != str(backup.get("doc_id", "") or "").strip():
                    stats["doc_id_remapped"] += 1

                group_key = (doc_id, resolved_chunk_id)
                group = recoverable_groups.setdefault(group_key, {
                    "chunk_text": chunk_text,
                    "entries": [],
                })
                group["entries"].append({
                    "quarantine_id": qrow["id"],
                    "backup": backup,
                    "reason": qrow["reason"],
                    "generation": generation,
                })

            stats["already_regenerated_skipped"] = already_regenerated
            stats["quarantine_entries_processed"] = len(quarantine_rows) - already_regenerated
            stats["source_chunk_groups_total"] = len(recoverable_groups)
            stats["recoverable_quarantine_entries"] = sum(
                len(group["entries"]) for group in recoverable_groups.values()
            )
            stats["unrecoverable_quarantine_entries"] = len(irrecoverable_rows)

            if progress_callback:
                progress_callback(
                    f"Klassifiziert: {len(recoverable_groups)} regenerierbare Source-Chunks, "
                    f"{len(irrecoverable_rows)} nicht mehr rekonstruierbare Einträge"
                )

            if dry_run:
                stats["triples_extracted"] += stats["recoverable_quarantine_entries"]
                return stats

            for row in irrecoverable_rows:
                _mark_quarantine_state(
                    cur,
                    row["quarantine_id"],
                    state="permanent_failed" if row.get("permanent") else "failed",
                    error_text=row["error"],
                )
                stats["quarantine_marked_failed"] += 1
                self._log_audit(conn, AuditEntry(
                    action="triple_regeneration_failed",
                    target_table="quarantine",
                    target_id=str(row["quarantine_id"]),
                    details=row["error"],
                ))

            # ── 5. For each source chunk: extract + verify + insert ─────
            from agent.rag_store.utils.memory import calculate_triple_hash

            processed_groups = 0
            total_groups = len(recoverable_groups)
            processed_recoverable_entries = 0

            for (doc_id, source_chunk_id), group in recoverable_groups.items():
                entries = group["entries"]
                chunk_text = group["chunk_text"]
                if batch_size > 0 and processed_recoverable_entries >= batch_size:
                    break

                processed_groups += 1
                if progress_callback and processed_groups % 5 == 0:
                    progress_callback(
                        f"Verarbeite Chunk-Gruppe {processed_groups}/{total_groups} "
                        f"(doc={doc_id[:20]}...)"
                    )
                assert chunk_text and len(chunk_text.strip()) >= 50, (
                    "Recoverable group must have a usable source chunk"
                )

                # 5b. LLM re-extraction
                assert kg_extractor is not None, "KG extractor must be available in non-dry-run mode"
                try:
                    chunk_data = [{"text": chunk_text, "chunk_id": source_chunk_id}]
                    doc_context = {"doc_id": doc_id, "source_type": "generic"}
                    new_triples = kg_extractor.extract_from_chunks(chunk_data, doc_context)
                except Exception as e:
                    extraction_err = f"Extraction failed for {doc_id}:{source_chunk_id}: {e}"
                    logger.error(f"❌ {extraction_err}")
                    stats["errors"].append(extraction_err)
                    for entry in entries:
                        _mark_quarantine_state(
                            cur,
                            entry["quarantine_id"],
                            state="failed",
                            error_text=extraction_err,
                        )
                        stats["quarantine_marked_failed"] += 1
                    continue

                stats["triples_extracted"] += len(new_triples)
                processed_recoverable_entries += len(entries)

                if not new_triples:
                    for entry in entries:
                        _mark_quarantine_state(
                            cur,
                            entry["quarantine_id"],
                            state="failed",
                            error_text=f"No triples extracted from {doc_id}:{source_chunk_id}",
                        )
                        stats["quarantine_marked_failed"] += 1
                        self._log_audit(conn, AuditEntry(
                            action="triple_regeneration_failed",
                            target_table="quarantine",
                            target_id=str(entry["quarantine_id"]),
                            details=f"No triples extracted from {doc_id}:{source_chunk_id}",
                        ))
                    continue

                # 5c. Dual-gate grounding: cross-encoder score plus a cheap
                # lexical check; high scores (>=0.7) pass on their own.
                grounded_triples = []
                assert rr is not None, "Reranker must be available in non-dry-run mode"
                for triple in new_triples:
                    hypothesis = f"{triple.subject} {triple.predicate} {triple.object}"
                    score = self._score_hypothesis_against_chunk(rr, hypothesis, chunk_text)
                    grounding_scores.append(round(score, 4))

                    lexical_ok = self._lexical_grounding_ok(
                        triple.subject, triple.object, chunk_text
                    )
                    if score >= min_grounding_score and (lexical_ok or score >= 0.7):
                        grounded_triples.append((triple, score))
                        stats["triples_grounded"] += 1
                    else:
                        stats["triples_ungrounded_skipped"] += 1

                # 5d. Insert grounded triples with dedup
                parent_generation = max(
                    (int(entry.get("generation", 0) or 0) for entry in entries),
                    default=0,
                )
                for triple, grounding_score in grounded_triples:
                    triple_hash = calculate_triple_hash(
                        triple.subject, triple.predicate, triple.object
                    )

                    # Duplicate check
                    cur.execute(
                        "SELECT COUNT(*) FROM triples WHERE triple_hash = ?",
                        (triple_hash,),
                    )
                    if cur.fetchone()[0] > 0:
                        stats["triples_duplicate_skipped"] += 1
                        continue

                    # Also check quarantine for same hash (avoid reinserting exact same triple)
                    cur.execute(
                        "SELECT COUNT(*) FROM quarantine WHERE source_table = 'triples' "
                        "AND json_valid(data_backup) = 1 "
                        "AND json_extract(data_backup, '$.triple_hash') = ?",
                        (triple_hash,),
                    )
                    if cur.fetchone()[0] > 0:
                        stats["triples_duplicate_skipped"] += 1
                        continue

                    # Insert new triple
                    now_iso = datetime.now(timezone.utc).isoformat()
                    triple_metadata = json.dumps({
                        "kg_source": "llm_per_chunk",
                        "extraction_method": "llm_regenerated",
                        "regenerated_from_quarantine": True,
                        "regeneration_generation": parent_generation + 1,
                        "regenerated_at": now_iso,
                        "grounding_score": round(grounding_score, 4),
                        "created_at": now_iso,
                    }, ensure_ascii=False)

                    cur.execute("""
                        INSERT INTO triples(doc_id, page, table_id, subject, predicate,
                                           object, metadata, triple_hash, source_chunk_id)
                        VALUES (?, NULL, NULL, ?, ?, ?, ?, ?, ?)
                    """, (
                        doc_id, triple.subject, triple.predicate, triple.object,
                        triple_metadata, triple_hash, source_chunk_id,
                    ))

                    new_triple_id = cur.lastrowid

                    # Store grounding score in triple_quality immediately
                    cur.execute("""
                        INSERT OR REPLACE INTO triple_quality(
                            triple_id, grounding_score, inferred_source_chunk_id, last_verified, action_taken
                        )
                        VALUES (?, ?, ?, ?, 'none')
                    """, (new_triple_id, round(grounding_score, 4), source_chunk_id, now_iso))

                    _upsert_kg_entities_for_triple(
                        cur,
                        doc_id=doc_id,
                        subject=triple.subject,
                        obj=triple.object,
                    )

                    stats["triples_inserted"] += 1

                # 5e. Mark processed quarantine entries
                for entry in entries:
                    qid = entry["quarantine_id"]
                    if not dry_run:
                        _mark_quarantine_state(
                            cur,
                            qid,
                            state="completed",
                            error_text=None,
                        )
                        stats["quarantine_marked_completed"] += 1

                    self._log_audit(conn, AuditEntry(
                        action="triple_regeneration",
                        target_table="quarantine",
                        target_id=str(qid),
                        details=f"Regenerated from {doc_id}:{source_chunk_id}, "
                                f"{len(grounded_triples)} grounded of {len(new_triples)} extracted",
                    ))

            conn.commit()

        except Exception as e:
            logger.error(f"❌ Triple regeneration error: {e}")
            import traceback
            logger.error(traceback.format_exc())
            stats["errors"].append(str(e))
        finally:
            conn.close()

        duration = time.time() - start_time
        stats["duration_seconds"] = round(duration, 1)
        stats["grounding_score_summary"] = {
            "count": len(grounding_scores),
            "min": min(grounding_scores) if grounding_scores else None,
            "mean": round(sum(grounding_scores) / len(grounding_scores), 4) if grounding_scores else None,
            "max": max(grounding_scores) if grounding_scores else None,
        }
        # In-memory KG / FAISS caches only see new triples after a reload
        stats["kg_reload_recommended"] = stats["triples_inserted"] > 0
        mode = "DRY-RUN" if dry_run else "EXECUTED"
        logger.info(
            f"🔄 Triple Regeneration [{mode}]: "
            f"{stats['quarantine_entries_processed']} verarbeitet, "
            f"{stats['source_chunks_found']} Chunks gefunden, "
            f"{stats['triples_extracted']} extrahiert, "
            f"{stats['triples_grounded']} gegrounded, "
            f"{stats['triples_inserted']} eingefügt, "
            f"{stats['triples_duplicate_skipped']} Duplikate übersprungen, "
            f"{stats['triples_ungrounded_skipped']} ungrounded übersprungen "
            f"({duration:.1f}s)"
        )
        return stats

    # =================================================================
    # QUICK STATS
    # =================================================================

    def get_db_health_stats(self) -> Dict[str, Any]:
        """Get a quick overview of DB health."""
        conn = self._get_connection()
        try:
            cur = conn.cursor()
            stats = {}

            cur.execute("SELECT COUNT(*) FROM chunks")
            stats["total_chunks"] = cur.fetchone()[0]

            cur.execute("SELECT COUNT(*) FROM triples")
            stats["total_triples"] = cur.fetchone()[0]

            cur.execute("SELECT COUNT(*) FROM documents")
            stats["total_documents"] = cur.fetchone()[0]

            # Quality scores
            cur.execute("SELECT AVG(structural_score), MIN(structural_score), MAX(structural_score) FROM chunk_quality")
            row = cur.fetchone()
            stats["chunk_quality_avg"] = round(row[0], 3) if row[0] else None
            stats["chunk_quality_min"] = round(row[1], 3) if row[1] else None
            stats["chunk_quality_max"] = round(row[2], 3) if row[2] else None

            # Quarantine
            stats["quarantine"] = self.get_quarantine_stats(conn)

            # Last audit
            cur.execute("SELECT timestamp, details FROM quality_audit_log WHERE action='structural_audit' ORDER BY timestamp DESC LIMIT 1")
            row = cur.fetchone()
            stats["last_audit"] = row["timestamp"] if row else None

            # KG grounding
            cur.execute("SELECT AVG(grounding_score) FROM triple_quality WHERE grounding_score >= 0")
            row = cur.fetchone()
            stats["avg_grounding_score"] = round(row[0], 3) if row and row[0] is not None else None

            # Predicate quality
            cur.execute("SELECT AVG(predicate_info_value) FROM triple_quality WHERE predicate_info_value > 0")
            row = cur.fetchone()
            stats["avg_predicate_quality"] = round(row[0], 3) if row and row[0] is not None else None

            # Feedback stats
            cur.execute("SELECT COUNT(*) FROM retrieval_feedback")
            stats["total_feedback"] = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM retrieval_feedback WHERE user_feedback > 0")
            stats["positive_feedback"] = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM retrieval_feedback WHERE user_feedback < 0")
            stats["negative_feedback"] = cur.fetchone()[0]

            return stats
        except Exception as e:
            logger.error(f"Failed to get health stats: {e}")
            return {"error": str(e)}
        finally:
            conn.close()

    def get_quarantine_items(self, conn: Optional[sqlite3.Connection] = None) -> List[Dict[str, Any]]:
        """Get all quarantine items for review."""
        own_conn = conn is None
        if own_conn:
            conn = self._get_connection()
        assert conn is not None
        try:
            cur = conn.cursor()
            cur.execute("SELECT id, source_table, source_id, reason, quarantined_at, auto_delete_after FROM quarantine ORDER BY quarantined_at DESC")
            return [dict(r) for r in cur.fetchall()]
        finally:
            if own_conn:
                conn.close()

    def get_audit_log(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Get recent audit log entries."""
        conn = self._get_connection()
        try:
            cur = conn.cursor()
            cur.execute("SELECT * FROM quality_audit_log ORDER BY timestamp DESC LIMIT ?", (limit,))
            return [dict(r) for r in cur.fetchall()]
        finally:
            conn.close()

    # =================================================================
    # ★ SOTA v2: TREND TRACKING
    # =================================================================

    def record_audit_trend(self, report: AuditReport) -> None:
        """
        Store audit metrics for trend comparison. Called after each structural audit.
        Computes delta from previous audit automatically.
        """
        conn = self._get_connection()
        try:
            cur = conn.cursor()
            timestamp = report.timestamp
            
            metrics = {
                "total_chunks": report.total_chunks,
                "total_triples": report.total_triples,
                "orphan_chunks": report.orphan_chunks,
                "orphan_triples": report.orphan_triples,
                "near_duplicates": report.near_duplicates,
                "boilerplate_chunks": report.boilerplate_chunks,
                "short_chunks": report.short_chunks,
                "url_dump_chunks": report.url_dump_chunks,
                "low_quality_chunks": report.low_quality_chunks,
                "defect_chunks": report.defect_chunks,
                "generic_predicate_triples": report.generic_predicate_triples,
                "regex_fallback_triples": report.regex_fallback_triples,
            }
            
            for metric_name, metric_value in metrics.items():
                # Get previous value for delta computation
                cur.execute(
                    "SELECT metric_value FROM quality_audit_trend "
                    "WHERE metric_name = ? ORDER BY audit_timestamp DESC LIMIT 1",
                    (metric_name,)
                )
                prev_row = cur.fetchone()
                delta = metric_value - (prev_row[0] if prev_row else metric_value)
                
                conn.execute(
                    "INSERT INTO quality_audit_trend(audit_timestamp, metric_name, metric_value, delta_from_previous) "
                    "VALUES (?, ?, ?, ?)",
                    (timestamp, metric_name, metric_value, delta)
                )
            
            conn.commit()
            logger.info(f"📈 Audit trend recorded ({len(metrics)} metrics)")
        except Exception as e:
            logger.warning(f"⚠️ Failed to record audit trend: {e}")
        finally:
            conn.close()

    def get_trend_data(self, metric_name: str, limit: int = 20) -> List[Dict[str, Any]]:
        """Get historical trend data for a specific metric."""
        conn = self._get_connection()
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT audit_timestamp, metric_value, delta_from_previous "
                "FROM quality_audit_trend "
                "WHERE metric_name = ? ORDER BY audit_timestamp DESC LIMIT ?",
                (metric_name, limit)
            )
            return [dict(r) for r in cur.fetchall()]
        except Exception:
            return []
        finally:
            conn.close()
