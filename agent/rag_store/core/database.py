"""
Database Manager Module for RAG Store
=====================================

Extrahiert aus unified_rag_store.py (Iteration 5, Oktober 2025)

Verwaltet alle SQLite-Datenbank-Operationen:
- Connection-Pooling für Multi-Threading
- Schema-Management (tables, indices)
- Robuste Cleanup-Strategien
- WAL-Mode-Handling
"""

from __future__ import annotations

import os
import sqlite3
import threading
import queue
import time
import logging
import sys
from contextlib import contextmanager
from typing import Optional

# Import ProcessingConfig from utils
try:
    from ..utils import ProcessingConfig
except ImportError:
    from agent.rag_store.utils import ProcessingConfig

logger = logging.getLogger(__name__)


class DatabaseManager:
    """
    Verwaltet SQLite-Datenbankverbindungen und Schema für RAG Store
    
    Features:
    - Thread-sichere Connection-Pools
    - Automatisches Schema-Management
    - Robuste Cleanup-Strategien (Windows-kompatibel)
    - Optimierte SQLite-PRAGMA-Einstellungen
    
    Usage:
        >>> db = DatabaseManager(db_path="rag.db", config=ProcessingConfig())
        >>> conn = db.get_connection()
        >>> # ... use connection ...
        >>> db.return_connection(conn)
        >>> db.close()
    
    Or with context manager:
        >>> with db.connection() as conn:
        ...     cur = conn.cursor()
        ...     cur.execute("SELECT * FROM chunks")
    """
    
    def __init__(
        self,
        db_path: str,
        config: Optional[ProcessingConfig] = None,
        debug: bool = False
    ):
        """
        Initialisiert DatabaseManager
        
        Args:
            db_path: Pfad zur SQLite-Datenbank
            config: Performance-Konfiguration
            debug: Debug-Modus aktivieren
        """
        # KRITISCHER FIX: :memory: zu shared-memory URI konvertieren
        # Sonst hat jede Connection ihre eigene isolierte In-Memory-DB!
        if db_path == ":memory:":
            self.db_path = "file::memory:?cache=shared"
            self._use_uri = True
            if debug:
                logger.info("🔄 :memory: → shared memory URI (multi-connection support)")
        else:
            self.db_path = db_path
            self._use_uri = False
        
        self.config = config or ProcessingConfig()
        self.debug = debug
        
        # Connection Pool für Multi-Threading
        self._connection_pool: queue.Queue = queue.Queue(
            maxsize=self.config.connection_pool_size
        )
        self._lock = threading.RLock()
        
        # Initialisierung
        self._setup_database()
        self._setup_connection_pool()
        self.ensure_schema()
        
        if self.debug:
            logger.info(f"✅ DatabaseManager initialized: {os.path.basename(db_path)}")
    
    def _setup_database(self) -> None:
        """Konfiguriert SQLite für optimale Performance"""
        
        if self.config.sqlite_wal_mode:
            try:
                conn = sqlite3.connect(self.db_path, check_same_thread=False, uri=self._use_uri)
                # WAL-Modus: erlaubt parallele Leser (kg_dashboard) neben Schreiber
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA synchronous=NORMAL")
                conn.execute("PRAGMA busy_timeout=15000")  # 15s warten statt sofort "locked"
                conn.execute("PRAGMA cache_size=100000") 
                conn.execute("PRAGMA temp_store=MEMORY")
                conn.execute("PRAGMA mmap_size=268435456")  # 256MB
                conn.execute("PRAGMA wal_autocheckpoint=1000")
                conn.close()
                
                if self.debug:
                    logger.info("🗃️ SQLite WAL-Mode aktiviert (concurrent reads möglich)")
                    
            except Exception as e:
                logger.warning(f"SQLite WAL-Setup fehlgeschlagen: {e}")
    
    def _setup_connection_pool(self) -> None:
        """Erstellt Connection-Pool für Multi-Threading"""
        
        for _ in range(self.config.connection_pool_size):
            try:
                conn = sqlite3.connect(self.db_path, check_same_thread=False, uri=self._use_uri)
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA synchronous=NORMAL")
                conn.execute("PRAGMA busy_timeout=15000")
                conn.execute("PRAGMA foreign_keys = ON")
                self._connection_pool.put(conn)
            except Exception as e:
                logger.error(f"Connection-Pool Setup fehlgeschlagen: {e}")
                break
    
    def get_connection(self) -> sqlite3.Connection:
        """
        Thread-sichere Connection aus dem Pool
        
        Returns:
            sqlite3.Connection: Datenbankverbindung
            
        Note:
            Bei Pool-Erschöpfung wird eine neue Connection erstellt
        """
        try:
            conn: sqlite3.Connection = self._connection_pool.get(timeout=5.0)
            return conn
        except queue.Empty:
            conn = sqlite3.connect(self.db_path, check_same_thread=False, uri=self._use_uri)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA busy_timeout=15000")
            conn.execute("PRAGMA foreign_keys = ON")
            return conn
    
    def return_connection(self, conn: sqlite3.Connection) -> None:
        """
        Connection zurück in den Pool
        
        Args:
            conn: Zurückzugebende Connection
            
        Note:
            Connection wird geschlossen wenn Pool voll ist
        """
        try:
            self._connection_pool.put_nowait(conn)
        except queue.Full:
            conn.close()
    
    @contextmanager
    def connection(self):
        """
        Context Manager für Connections
        
        Usage:
            >>> with db.connection() as conn:
            ...     cur = conn.cursor()
            ...     cur.execute("SELECT * FROM chunks")
        """
        conn = self.get_connection()
        try:
            yield conn
        finally:
            self.return_connection(conn)
    
    def ensure_schema(self) -> None:
        """
        Erstellt Datenbank-Schema
        
        Tables:
            - documents: Dokument-Metadaten
            - chunks: Text-Chunks mit Embeddings
            - tables: Strukturierte Daten aus PDFs
            - triples: Knowledge Graph Triples
        """
        conn = self.get_connection()
        cur = conn.cursor()
        try:
            # Documents table
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS documents (
                    doc_id TEXT PRIMARY KEY
                )
                """
            )
            
            # Chunks table
            # ── SOTA Domain/Safety-Namespacing (chunks_domain_v1) ────────────────
            # `domain`       — corpus namespace ('general', 'psych', …) used to
            #                  partition retrieval per LLM intent classifier.
            # `safety_flag`  — content-safety classification ('safe', 'crisis',
            #                  'sensitive') for pre-retrieval filtering. Defaults
            #                  ensure existing chunks are retrievable; ingest paths
            #                  must set explicit values for new content.
            # ─────────────────────────────────────────────────────────────────────
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS chunks (
                    doc_id TEXT NOT NULL,
                    chunk_id INTEGER NOT NULL,
                    text TEXT NOT NULL,
                    metadata TEXT NOT NULL,
                    embedding BLOB NOT NULL,
                    domain TEXT NOT NULL DEFAULT 'general',
                    safety_flag TEXT NOT NULL DEFAULT 'safe',
                    classification_version INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (doc_id, chunk_id),
                    FOREIGN KEY (doc_id) REFERENCES documents(doc_id) ON DELETE CASCADE
                )
                """
            )
            
            # Tables for structured data
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS tables (
                    table_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    doc_id TEXT NOT NULL,
                    page INTEGER,
                    headers_json TEXT NOT NULL,
                    rows_json TEXT NOT NULL,
                    markdown TEXT,
                    metadata TEXT,
                    FOREIGN KEY (doc_id) REFERENCES documents(doc_id) ON DELETE CASCADE
                )
                """
            )
            
            # Knowledge graph triples
            cur.execute(
                """
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
                    FOREIGN KEY (table_id) REFERENCES tables(table_id) ON DELETE CASCADE
                )
                """
            )
            
            # Image metadata table (multimodal support)
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS image_metadata (
                    image_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    doc_id TEXT NOT NULL,
                    page_number INTEGER,
                    image_index INTEGER,
                    image_path TEXT NOT NULL,
                    image_format TEXT,
                    width INTEGER,
                    height INTEGER,
                    size_bytes INTEGER,
                    image_hash TEXT,
                    alt_text TEXT,
                    ocr_text TEXT,
                    ocr_confidence REAL,
                    source TEXT,
                    created_at TEXT,
                    FOREIGN KEY (doc_id) REFERENCES documents(doc_id) ON DELETE CASCADE
                )
                """
            )
            
            # Füge triple_hash Spalte hinzu falls sie nicht existiert (für bestehende Datenbanken)
            try:
                cur.execute("ALTER TABLE triples ADD COLUMN triple_hash TEXT")
                logger.info("✅ triple_hash Spalte zur triples Tabelle hinzugefügt")
            except sqlite3.OperationalError:
                # Spalte existiert bereits
                pass
            
            # ★ SOTA: source_chunk_id — tracks which chunk a triple was extracted from
            try:
                cur.execute("ALTER TABLE triples ADD COLUMN source_chunk_id INTEGER")
                logger.info("✅ source_chunk_id Spalte zur triples Tabelle hinzugefügt")
            except sqlite3.OperationalError:
                pass  # Spalte existiert bereits

            # ★ SOTA Update-Layer: confidence + Bayesian repeat-mention tracking
            #   confidence    — float in [0,1], primary signal from LLM extraction;
            #                   updated via Noisy-OR on repeated mentions
            #   mention_count — number of times this triple was re-extracted
            #   created_at    — first time seen
            #   updated_at    — last Bayesian update / first insert
            for ddl in (
                "ALTER TABLE triples ADD COLUMN confidence REAL DEFAULT 0.5",
                "ALTER TABLE triples ADD COLUMN mention_count INTEGER DEFAULT 1",
                "ALTER TABLE triples ADD COLUMN created_at TEXT",
                "ALTER TABLE triples ADD COLUMN updated_at TEXT",
                "ALTER TABLE triples ADD COLUMN validity_type TEXT DEFAULT 'atemporal'",
                "ALTER TABLE triples ADD COLUMN valid_from TEXT",
                "ALTER TABLE triples ADD COLUMN valid_to TEXT",
                "ALTER TABLE triples ADD COLUMN observed_at TEXT",
                "ALTER TABLE triples ADD COLUMN last_verified_at TEXT",
                "ALTER TABLE triples ADD COLUMN contradiction_state TEXT DEFAULT 'none'",
                "ALTER TABLE triples ADD COLUMN evidence_strength REAL DEFAULT 0.5",
            ):
                try:
                    cur.execute(ddl)
                except sqlite3.OperationalError:
                    pass  # column already exists
            
            # ★ SOTA v3: Entity Embedding Table for Semantic KG Search
            # Replaces string-based LIKE search with embedding similarity.
            # Each unique entity (subject/object) gets an embedding vector.
            # At query time: embed query → cosine similarity → find matching entities → retrieve triples.
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS kg_entities (
                    entity_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    entity_text TEXT NOT NULL UNIQUE,
                    normalized_text TEXT NOT NULL,
                    entity_type TEXT DEFAULT 'entity',
                    frequency INTEGER DEFAULT 1,
                    first_seen_doc_id TEXT,
                    embedding BLOB
                )
                """
            )
            
            # Indices für Performance
            cur.execute("CREATE INDEX IF NOT EXISTS idx_tables_doc ON tables(doc_id)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_triples_doc ON triples(doc_id)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_triples_sp ON triples(subject, predicate)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_triples_hash ON triples(triple_hash)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_kg_entities_normalized ON kg_entities(normalized_text)")
            
            # Entity-to-Triple join index: schnelle Suche nach Triples einer Entity
            cur.execute("CREATE INDEX IF NOT EXISTS idx_triples_subject ON triples(subject)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_triples_object ON triples(object)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_triples_confidence ON triples(confidence)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_triples_updated_at ON triples(updated_at)")
            
            # ★ SOTA v4: Migrate existing entity normalized_text to deep normalization
            # On first run after upgrade, re-normalizes all entities with
            # normalize_entity_for_matching() (title-strip, case-fold, etc.)
            self._migrate_entity_normalization(cur)

            # ★ SOTA: Heal stale triple_hash values + SPO duplicates that earlier
            # merge code paths left behind (rewrote subject/object without hash
            # recompute). Idempotent — runs once, then never again.
            self._migrate_triple_hash_v2(cur)

            # ★ SOTA: Add domain/safety_flag columns to chunks of existing DBs
            # so that pre-retrieval namespace filtering works on legacy data.
            # Backfills psycho-corpus chunks to domain='psych' via the metadata
            # JSON marker `source: psycho_corpus_sota`.
            self._migrate_add_chunk_domain_v1(cur)

            # ★ SOTA: Persistent cache for ContentClassifier (prototype + LLM
            # hybrid). Keyed by SHA256(content) so re-ingesting the same web
            # page or PDF never re-pays the classification cost.
            self._migrate_content_classification_v1(cur)

            # ── Versioning column for chunk-level reclassification ────────
            self._migrate_chunk_classification_version_v1(cur)

            # Indices für image_metadata
            cur.execute("CREATE INDEX IF NOT EXISTS idx_images_doc ON image_metadata(doc_id)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_images_hash ON image_metadata(image_hash)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_images_page ON image_metadata(doc_id, page_number)")

            # Composite index for namespace pre-filter (domain + safety_flag).
            # Used by SearchManager's chunk hydration to filter by allowed_domains
            # and to exclude unsafe content in O(log n).
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_chunks_domain_safety "
                "ON chunks(domain, safety_flag)"
            )
            
            # Versuche UNIQUE Index auf triple_hash zu erstellen (falls keine Duplikate vorhanden)
            try:
                cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_triples_hash_unique ON triples(triple_hash)")
                logger.info("✅ UNIQUE Index auf triple_hash erstellt")
            except sqlite3.IntegrityError:
                logger.warning("⚠️ UNIQUE Index auf triple_hash konnte nicht erstellt werden (Duplikate vorhanden)")
                logger.info("💡 Führen Sie 'fix_kg_duplicates.py' aus, um bestehende Duplikate zu bereinigen")
            
            # ── Quality-Management-Schema ──────────────────────────────
            from .quality import RAGQualityManager
            qm = RAGQualityManager(db_path=self.db_path)
            qm.ensure_quality_schema(conn)
            
            conn.commit()
        finally:
            cur.close()
            self.return_connection(conn)
    
    # ──────────────────────────────────────────────────────────────────
    #  SOTA v4 — one-shot migration: deep entity normalization
    # ──────────────────────────────────────────────────────────────────
    def _migrate_entity_normalization(self, cur: sqlite3.Cursor) -> None:
        """Re-normalize all kg_entities.normalized_text with the SOTA
        normalize_entity_for_matching() function (title-strip, case-fold,
        parenthetical removal, whitespace cleanup).

        The migration is idempotent: a sentinel row in a tiny
        ``_schema_meta`` table records whether the migration already ran.
        After re-normalization, duplicate normalized_text values are
        merged (higher-frequency entity keeps its row, the other is
        deleted and all triples are rewritten to point to the surviving
        entity text).
        """
        try:
            # ── sentinel table ──────────────────────────────────────
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS _schema_meta (
                    key   TEXT PRIMARY KEY,
                    value TEXT
                )
                """
            )
            already = cur.execute(
                "SELECT value FROM _schema_meta WHERE key = 'entity_norm_v4'"
            ).fetchone()
            if already:
                return  # migration already applied

            # ── import SOTA normalizer ──────────────────────────────
            try:
                from agent.llm_knowledge_graph import normalize_entity_for_matching
            except ImportError:
                try:
                    import sys, pathlib
                    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3]))
                    from agent.llm_knowledge_graph import normalize_entity_for_matching
                except ImportError:
                    logger.warning(
                        "⚠️ normalize_entity_for_matching not available — "
                        "entity normalization migration skipped"
                    )
                    return

            # ── fetch all entities ──────────────────────────────────
            rows = cur.execute(
                "SELECT entity_id, entity_text, normalized_text, frequency "
                "FROM kg_entities"
            ).fetchall()
            if not rows:
                cur.execute(
                    "INSERT OR REPLACE INTO _schema_meta VALUES "
                    "('entity_norm_v4', 'done')"
                )
                return

            logger.info(
                f"🔄 SOTA v4 entity normalization migration: "
                f"re-normalizing {len(rows)} entities …"
            )

            # entity_id → (entity_text, new_norm, frequency)
            id_info: dict[int, tuple[str, str, int]] = {}
            # new_norm → list of (entity_id, entity_text, frequency)
            norm_groups: dict[str, list[tuple[int, str, int]]] = {}

            for eid, etxt, _old_norm, freq in rows:
                new_norm = normalize_entity_for_matching(etxt)
                id_info[eid] = (etxt, new_norm, freq or 1)
                norm_groups.setdefault(new_norm, []).append(
                    (eid, etxt, freq or 1)
                )

            merged = 0
            updated = 0

            for norm, group in norm_groups.items():
                if len(group) == 1:
                    # unique — just update normalized_text
                    eid, _etxt, _freq = group[0]
                    cur.execute(
                        "UPDATE kg_entities SET normalized_text = ? "
                        "WHERE entity_id = ?",
                        (norm, eid),
                    )
                    updated += 1
                else:
                    # collision → keep highest-frequency entity, merge rest
                    group.sort(key=lambda g: g[2], reverse=True)
                    winner_id, winner_text, _wf = group[0]
                    cur.execute(
                        "UPDATE kg_entities SET normalized_text = ? "
                        "WHERE entity_id = ?",
                        (norm, winner_id),
                    )
                    updated += 1

                    for loser_id, loser_text, _lf in group[1:]:
                        # rewrite triples that reference the loser (helper recomputes
                        # triple_hash + collapses on collision via Bayesian Noisy-OR)
                        from agent.kg_entity_merge import merge_entity_in_triples
                        from agent.rag_store.utils.memory import calculate_triple_hash
                        merge_entity_in_triples(
                            cur.connection, calculate_triple_hash, winner_text, loser_text
                        )
                        # delete the loser entity row
                        cur.execute(
                            "DELETE FROM kg_entities WHERE entity_id = ?",
                            (loser_id,),
                        )
                        merged += 1

            # mark done
            cur.execute(
                "INSERT OR REPLACE INTO _schema_meta VALUES "
                "('entity_norm_v4', 'done')"
            )
            logger.info(
                f"✅ Entity normalization migration complete: "
                f"{updated} updated, {merged} duplicates merged"
            )

        except Exception as exc:
            raise RuntimeError(
                f"entity normalization migration failed: {exc}"
            ) from exc

    # ──────────────────────────────────────────────────────────────────
    #  SOTA — one-shot migration: triple_hash resync + dedupe
    # ──────────────────────────────────────────────────────────────────
    def _migrate_triple_hash_v2(self, cur: sqlite3.Cursor) -> None:
        """Heal stale `triple_hash` values and SPO duplicates left behind
        by earlier merge code paths that rewrote subject/object without
        recomputing the hash. Idempotent via ``_schema_meta``.

        After this migration, the UNIQUE constraint on triple_hash holds
        and every future merge is funnelled through the shared helper
        `agent.kg_entity_merge.merge_entity_in_triples`, which keeps the
        invariant going forward.
        """
        try:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS _schema_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT
                )
                """
            )
            already = cur.execute(
                "SELECT value FROM _schema_meta WHERE key = 'triple_hash_v2'"
            ).fetchone()
            if already:
                return

            from agent.kg_entity_merge import recompute_and_dedupe_triple_hashes
            from agent.rag_store.utils.memory import calculate_triple_hash

            stats = recompute_and_dedupe_triple_hashes(
                cur.connection, calculate_triple_hash
            )
            cur.execute(
                "INSERT OR REPLACE INTO _schema_meta VALUES "
                "('triple_hash_v2', 'done')"
            )
            logger.info(
                "✅ triple_hash v2 migration complete: scanned=%d rewritten=%d collapsed=%d",
                stats["rows_scanned"], stats["hashes_rewritten"], stats["collapsed"],
            )
        except Exception as exc:
            raise RuntimeError(
                f"triple_hash v2 migration failed: {exc}"
            ) from exc

    def close(self) -> None:
        """
        ROBUSTE Schließung aller Verbindungen und Aufräumen
        
        3-Phasen-Cleanup:
        1. Pre-close WAL checkpoint
        2. Close all pooled connections
        3. Aggressive database cleanup
        """
        with self._lock:
            try:
                if self.debug:
                    logger.info("🔒 Starting ROBUST database close...")
                
                # Phase 1: Pre-close WAL checkpoint
                temp_conn = None
                try:
                    temp_conn = sqlite3.connect(self.db_path, check_same_thread=False, timeout=10.0, uri=self._use_uri)
                    temp_conn.execute("PRAGMA busy_timeout = 5000")
                    temp_conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                    temp_conn.commit()
                    if self.debug:
                        logger.info("🔄 Pre-close WAL checkpoint completed")
                except Exception as e:
                    if self.debug:
                        logger.warning(f"⚠️ Pre-close checkpoint failed: {e}")
                finally:
                    if temp_conn:
                        try:
                            temp_conn.close()
                        except Exception:
                            pass
                
                # Phase 2: Close all pooled connections
                closed_count = 0
                while not self._connection_pool.empty():
                    try:
                        conn = self._connection_pool.get_nowait()
                        conn.close()
                        closed_count += 1
                    except (queue.Empty, Exception):
                        break
                
                if self.debug:
                    logger.info(f"🔒 Closed {closed_count} pooled connections")
                
                # Phase 3: Final WAL checkpoint
                try:
                    final_conn = sqlite3.connect(self.db_path, timeout=10.0, uri=self._use_uri)
                    final_conn.execute("PRAGMA busy_timeout = 5000")
                    result = final_conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
                    final_conn.close()
                    if self.debug:
                        logger.info(f"🗃️ Final WAL Checkpoint: {result}")
                except Exception as e:
                    if self.debug:
                        logger.warning(f"⚠️ Final checkpoint failed: {e}")
                
                # Phase 4: Aggressive cleanup (nur wenn Interpreter nicht im Shutdown ist)
                if not self._is_interpreter_shutting_down():
                    self._aggressive_database_cleanup()
                
                if self.debug:
                    logger.info("✅ ROBUST database close completed")
                    
            except Exception as e:
                logger.error(f"Database close error: {e}")
    
    def _aggressive_database_cleanup(self) -> None:
        """
        ROBUSTE database cleanup to prevent WAL/SHM file issues
        
        3-Runden-Strategie mit steigender Aggressivität:
        1. Standard TRUNCATE checkpoint
        2. Journal-Mode-Switching (DELETE → WAL)
        3. Nuclear option (MEMORY → DELETE)
        
        Danach: Multiple file removal strategies (Windows-kompatibel)
        """
        if self._is_interpreter_shutting_down():
            return

        try:
            if self.debug:
                logger.info("🔥 ROBUSTE Aggressive database cleanup...")
            
            # Step 1: Multiple attempts with increasing aggression
            cleanup_success = False
            
            for round_num in range(3):  # 3 rounds of increasing aggression
                if self.debug:
                    logger.info(f"🔄 Cleanup Round {round_num + 1}/3")
                
                temp_conn = None
                try:
                    # Create isolated connection for cleanup
                    temp_conn = sqlite3.connect(self.db_path, timeout=15.0, uri=self._use_uri)
                    temp_conn.execute("PRAGMA busy_timeout = 10000")  # 10 second timeout
                    
                    # Round 1: Standard cleanup
                    if round_num == 0:
                        temp_conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                        temp_conn.commit()
                        
                    # Round 2: Aggressive mode switching
                    elif round_num == 1:
                        # Force journal mode to DELETE (removes WAL files)
                        temp_conn.execute("PRAGMA journal_mode=DELETE")
                        temp_conn.commit()
                        time.sleep(1.0)
                        
                        # Switch back to WAL (clean state)
                        temp_conn.execute("PRAGMA journal_mode=WAL")
                        temp_conn.commit()
                        
                    # Round 3: Nuclear option - disable WAL temporarily
                    else:
                        temp_conn.execute("PRAGMA journal_mode=MEMORY")
                        temp_conn.commit()
                        time.sleep(0.5)
                        temp_conn.execute("PRAGMA journal_mode=DELETE")
                        temp_conn.commit()
                        time.sleep(1.0)
                        
                    if self.debug:
                        logger.info(f"✅ Cleanup round {round_num + 1} completed")
                    cleanup_success = True
                    break
                    
                except Exception as e:
                    if self.debug:
                        logger.warning(f"⚠️ Cleanup round {round_num + 1} failed: {e}")
                finally:
                    if temp_conn:
                        try:
                            temp_conn.close()
                        except Exception:
                            pass
                
                # Wait between rounds
                time.sleep(0.5)
            
            # Step 2: Force garbage collection
            import gc
            gc.collect()
            time.sleep(1.0)
            
            # Step 3: Manual file cleanup with multiple strategies
            wal_file = f"{self.db_path}-wal"
            shm_file = f"{self.db_path}-shm"
            
            for file_path in [wal_file, shm_file]:
                if os.path.exists(file_path):
                    file_removed = False
                    
                    # Strategy 1: Direct removal
                    try:
                        os.remove(file_path)
                        file_removed = True
                        if self.debug:
                            logger.info(f"🗑️ Removed {os.path.basename(file_path)}")
                    except Exception as e:
                        if self.debug:
                            logger.warning(f"⚠️ Direct removal failed for {os.path.basename(file_path)}: {e}")
                    
                    # Strategy 2: Truncate if removal failed
                    if not file_removed:
                        try:
                            with open(file_path, 'w') as f:
                                f.truncate(0)
                            if self.debug:
                                logger.info(f"🔧 Truncated {os.path.basename(file_path)}")
                        except Exception as e:
                            if self.debug:
                                logger.warning(f"⚠️ Truncation failed for {os.path.basename(file_path)}: {e}")
                    
                    # Strategy 3: Rename and delete (Windows workaround)
                    if not file_removed and os.path.exists(file_path):
                        try:
                            temp_name = f"{file_path}.temp_{int(time.time())}"
                            os.rename(file_path, temp_name)
                            os.remove(temp_name)
                            if self.debug:
                                logger.info(f"🔄 Renamed and removed {os.path.basename(file_path)}")
                        except Exception as e:
                            if self.debug:
                                logger.warning(f"⚠️ Rename strategy failed for {os.path.basename(file_path)}: {e}")
            
            # Step 4: Final verification
            wal_exists = os.path.exists(wal_file)
            shm_exists = os.path.exists(shm_file)
            
            if self.debug:
                if not wal_exists and not shm_exists:
                    logger.info("✅ ALL WAL/SHM files successfully cleaned up!")
                else:
                    logger.warning(f"⚠️ Cleanup incomplete - WAL: {'exists' if wal_exists else 'removed'}, SHM: {'exists' if shm_exists else 'removed'}")
            
        except Exception as e:
            if self._is_shutdown_exception(e):
                return
            logger.warning(f"Aggressive database cleanup error: {e}")

    @staticmethod
    def _is_interpreter_shutting_down() -> bool:
        try:
            return bool(getattr(sys, "is_finalizing", lambda: False)())
        except Exception as exc:
            logger.debug(f"DatabaseManager finalization check failed: {exc}")
            return False

    @staticmethod
    def _is_shutdown_exception(exc: Exception) -> bool:
        text = str(exc)
        return (
            "sys.meta_path is None" in text
            or "Python is likely shutting down" in text
            or "interpreter shutdown" in text.lower()
        )
    
    def __del__(self):
        """Destructor: Ensure cleanup on object destruction"""
        try:
            self.close()
        except Exception as exc:
            if not self._is_shutdown_exception(exc):
                logger.debug(f"DatabaseManager destructor cleanup failed: {exc}")

    # ──────────────────────────────────────────────────────────────────
    #  SOTA — one-shot migration: chunks.domain + chunks.safety_flag
    # ──────────────────────────────────────────────────────────────────
    def _migrate_add_chunk_domain_v1(self, cur: sqlite3.Cursor) -> None:
        """Add `domain` and `safety_flag` columns to ``chunks`` for legacy DBs
        and backfill the psycho-corpus to ``domain='psych'``.

        The schema-level ``CREATE TABLE`` already declares both columns with
        defaults, so for *new* DBs this migration is a no-op. For DBs created
        before the schema bump, ``ALTER TABLE`` is the only way to add the
        columns since SQLite cannot re-issue CREATE TABLE on an existing
        relation.

        Idempotent via ``_schema_meta`` sentinel ``chunks_domain_v1``.
        """
        try:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS _schema_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT
                )
                """
            )
            already = cur.execute(
                "SELECT value FROM _schema_meta WHERE key = 'chunks_domain_v1'"
            ).fetchone()
            if already:
                return

            existing_cols = {
                row[1] for row in cur.execute("PRAGMA table_info(chunks)").fetchall()
            }

            if 'domain' not in existing_cols:
                cur.execute(
                    "ALTER TABLE chunks ADD COLUMN domain TEXT NOT NULL DEFAULT 'general'"
                )
                logger.info("✅ chunks.domain column added (default='general')")

            if 'safety_flag' not in existing_cols:
                cur.execute(
                    "ALTER TABLE chunks ADD COLUMN safety_flag TEXT NOT NULL DEFAULT 'safe'"
                )
                logger.info("✅ chunks.safety_flag column added (default='safe')")

            # Backfill: psycho-corpus chunks identified via JSON metadata marker.
            # ``json_extract`` is a built-in SQLite function; if a chunk was
            # ingested without metadata the LIKE-clause guards against
            # extraction errors on malformed JSON.
            backfilled = cur.execute(
                """
                UPDATE chunks
                SET domain = 'psych'
                WHERE domain = 'general'
                  AND metadata LIKE '%psycho_corpus_sota%'
                  AND json_extract(metadata, '$.source') = 'psycho_corpus_sota'
                """
            ).rowcount
            if backfilled:
                logger.info(
                    f"✅ Backfilled {backfilled} psycho-corpus chunks to domain='psych'"
                )

            cur.execute(
                "INSERT OR REPLACE INTO _schema_meta(key, value) VALUES (?, ?)",
                ('chunks_domain_v1', '1'),
            )
        except Exception as exc:
            # Surface the error — silent migration failures previously masked
            # the bootstrapper bug that caused the psycho-corpus to be missing.
            raise RuntimeError(
                f"chunks_domain_v1 migration failed: {exc}"
            ) from exc

    # ──────────────────────────────────────────────────────────────────
    #  SOTA — content classification cache (prototype + LLM hybrid)
    # ──────────────────────────────────────────────────────────────────
    def _migrate_content_classification_v1(self, cur: sqlite3.Cursor) -> None:
        """Create ``content_classification_cache`` for the ContentClassifier.

        Keyed by SHA256(canonical text representative) to avoid recomputing
        prototype-similarity / LLM verdicts on re-ingestion of the same
        document or web page. Idempotent via ``_schema_meta`` sentinel
        ``content_classification_v1``.

        Schema:
            content_hash  — SHA256 of the classification input (title + first
                            chunk of body, see ContentClassifier._cache_key)
            corpus_domain — 'general' | 'psych' (mirror of chunks.domain)
            safety_flag   — 'safe' | 'sensitive' | 'crisis'
            confidence    — float in [0,1] from the classifier
            method        — 'prototype' | 'llm' | 'hybrid' | 'explicit'
            created_at    — ISO-8601 timestamp
        """
        try:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS _schema_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT
                )
                """
            )
            already = cur.execute(
                "SELECT value FROM _schema_meta WHERE key = 'content_classification_v1'"
            ).fetchone()
            if already:
                return

            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS content_classification_cache (
                    content_hash  TEXT PRIMARY KEY,
                    corpus_domain TEXT NOT NULL,
                    safety_flag   TEXT NOT NULL,
                    confidence    REAL NOT NULL,
                    method        TEXT NOT NULL,
                    created_at    TEXT NOT NULL
                )
                """
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_classification_method "
                "ON content_classification_cache(method)"
            )
            cur.execute(
                "INSERT OR REPLACE INTO _schema_meta(key, value) VALUES (?, ?)",
                ('content_classification_v1', '1'),
            )
            logger.info("✅ content_classification_cache table ready")
        except Exception as exc:
            raise RuntimeError(
                f"content_classification_v1 migration failed: {exc}"
            ) from exc

    # ──────────────────────────────────────────────────────────────────
    #  SOTA — chunk classification_version (per-doc reclassifier sweeps)
    # ──────────────────────────────────────────────────────────────────
    def _migrate_chunk_classification_version_v1(self, cur: sqlite3.Cursor) -> None:
        """Add ``classification_version`` column to ``chunks``.

        Tracks which version of the :class:`ContentClassifier` last labeled
        each chunk's ``domain`` / ``safety_flag``. The maintenance job in
        ``agent.rag_store.maintenance.reclassifier`` re-classifies every doc
        whose chunks carry a version lower than ``CLASSIFICATION_VERSION``,
        so threshold or model upgrades can flow deterministically through
        the legacy corpus.

        For new DBs the schema-level CREATE TABLE already declares the
        column; for legacy DBs ``ALTER TABLE`` adds it with default ``0`` so
        existing rows are automatically flagged for the next sweep.

        Idempotent via ``_schema_meta`` sentinel
        ``chunks_classification_version_v1``.
        """
        try:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS _schema_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT
                )
                """
            )
            already = cur.execute(
                "SELECT value FROM _schema_meta "
                "WHERE key = 'chunks_classification_version_v1'"
            ).fetchone()
            if already:
                return

            existing_cols = {
                row[1] for row in cur.execute("PRAGMA table_info(chunks)").fetchall()
            }
            if 'classification_version' not in existing_cols:
                cur.execute(
                    "ALTER TABLE chunks ADD COLUMN "
                    "classification_version INTEGER NOT NULL DEFAULT 0"
                )
                logger.info(
                    "✅ chunks.classification_version column added "
                    "(default=0; legacy rows flagged for reclassifier sweep)"
                )

            cur.execute(
                "INSERT OR REPLACE INTO _schema_meta(key, value) VALUES (?, ?)",
                ('chunks_classification_version_v1', '1'),
            )
        except Exception as exc:
            raise RuntimeError(
                f"chunks_classification_version_v1 migration failed: {exc}"
            ) from exc


# Public API
__all__ = [
    'DatabaseManager',
]
