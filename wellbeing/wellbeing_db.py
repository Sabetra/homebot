"""
Psychologische Datenbank - Sichere SQLite-Implementierung mit KG-Extraktion
===========================================================================
Separate Datenbank für psychologische Session-Daten mit:
- Verschlüsselung sensitiver Inhalte
- DSGVO-konforme Datenstrukturen
- Connection Pooling für Performance
- Automatische Backup-Mechanismen
- **NEU: Automatische Knowledge Graph-Extraktion**
"""

import sqlite3
import json
import hashlib
import logging
import os
import hmac
import unicodedata
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any, Tuple, Union, Type, Generator
from contextlib import contextmanager
import threading
from cryptography.fernet import Fernet
import base64
import re

# Logger für Datenbank-Operationen (ZUERST definieren!)
logger = logging.getLogger(__name__)

# ★ SOTA: canonical entity normalisation. Hard import — no fallback.
# A shallow fallback (replace('_',' ').lower()) silently corrupts the
# normalized_text column (e.g. "Prof. Dr. Schmidt" stays distinct from
# "schmidt" after the deep normaliser ran on different rows). This caused
# duplicate-entity bugs that surfaced in the Phase-22/23 audit, so we now
# fail fast if the canonical helper is unavailable.
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from agent.llm_knowledge_graph import normalize_entity_for_matching  # noqa: E402

# NEU: Enhanced KG-Extraktion Imports (robuste LLM-Calls, multi-method parsing)
try:
    # Versuche zuerst enhanced Version
    try:
        from agent.llm_knowledge_graph_enhanced import EnhancedLLMKnowledgeGraphExtractor
        from agent.llm_knowledge_graph_enhanced import KGTriple as KGTripleEnhanced
        from agent.llm_knowledge_graph_enhanced import normalize_text
        # Type aliases for compatibility (using Union to allow both types)
        LLMKnowledgeGraphExtractor: Optional[Type[Any]] = EnhancedLLMKnowledgeGraphExtractor
        KGTriple: Optional[Type[Any]] = KGTripleEnhanced
        logger.info("✅ Enhanced KG Extractor geladen")
    except ImportError:
        # Fallback auf alte Version
        from agent.llm_knowledge_graph import LLMKnowledgeGraphExtractor as LLMKGExtractorFallback
        from agent.llm_knowledge_graph import KGTriple as KGTripleFallback
        from agent.llm_knowledge_graph import normalize_text
        LLMKnowledgeGraphExtractor = LLMKGExtractorFallback
        KGTriple = KGTripleFallback
        logger.warning("⚠️ Using fallback KG Extractor (nicht enhanced)")

    KG_EXTRACTION_AVAILABLE = True

    # Triple-Hash-Berechnung (triple_utils.py existiert nicht mehr - verwende lokale Implementierung)
    def calculate_triple_hash(subject: str, predicate: str, obj: str) -> str:
        """Berechnet Hash für ein Knowledge Graph Triple"""
        return hashlib.md5(f"{subject}_{predicate}_{obj}".encode()).hexdigest()

except ImportError as e:
    KG_EXTRACTION_AVAILABLE = False
    LLMKnowledgeGraphExtractor = None
    KGTriple = None
    logger.warning(f"⚠️ KG-Extraktion nicht verfügbar: {e}")
    def calculate_triple_hash(subject: str, predicate: str, obj: str) -> str:
        return hashlib.md5(f"{subject}_{predicate}_{obj}".encode()).hexdigest()

    # Fallback normalize_text wenn KG-Import fehlschlägt
    def normalize_text(text: str) -> str:
        """Fallback-Normalisierung für KG-Triples"""
        if not text:
            return text
        if text.startswith('psych_') or text.startswith('session_'):
            return text
        normalized = text.replace('_', ' ')
        # O(n) statt while-Loop (O(n^2)); verhaltensäquivalent: kollabiert nur
        # ASCII-Leerzeichen-Läufe >=2 zu einem Leerzeichen (Tabs/Newlines bleiben).
        normalized = re.sub(r' {2,}', ' ', normalized)
        return normalized.strip()

def normalize_search_query(query: str) -> str:
    """
    Normalisiert eine Suchanfrage für flexible Triple-Suche.
    Ersetzt Unterstriche durch Leerzeichen und entfernt doppelte Leerzeichen.
    
    Args:
        query: Originale Suchanfrage
        
    Returns:
        Normalisierte Suchanfrage
    """
    if not query:
        return query
    
    # Ersetze Unterstriche durch Leerzeichen
    normalized = query.replace('_', ' ')
    
    # Entferne doppelte Leerzeichen
    # O(n) statt while-Loop (O(n^2)); verhaltensäquivalent: kollabiert nur
    # ASCII-Leerzeichen-Läufe >=2 zu einem Leerzeichen (Tabs/Newlines bleiben).
    normalized = re.sub(r' {2,}', ' ', normalized)
    
    return normalized.strip()

def create_flexible_search_pattern(query: str) -> str:
    """
    Erstellt ein flexibles Suchmuster für SQL LIKE.
    Fügt % zwischen Wörtern ein für Treffer unabhängig von Trennzeichen.
    
    Args:
        query: Normalisierte Suchanfrage
        
    Returns:
        SQL LIKE Pattern (z.B. "hat%Angst%vor")
    """
    if not query:
        return "%"
    
    # Normalisiere zuerst
    normalized = normalize_search_query(query)
    
    # Teile in Wörter und verbinde mit %
    words = normalized.split()
    if len(words) <= 1:
        return f"%{normalized}%"
    
    # Mehrere Wörter: Flexibles Pattern
    return "%" + "%".join(words) + "%"

class WellbeingDatabase:
    """
    Sichere SQLite-Datenbank für psychologische Session-Daten mit automatischer KG-Extraktion
    
    Features:
    - Verschlüsselung sensitiver Daten
    - Connection Pooling
    - Thread-sichere Operationen
    - DSGVO-konforme Datenstrukturen
    - Automatische Schema-Migration
    - **NEU: Automatische Knowledge Graph-Extraktion nach jeder Interaktion**
    """

    encryption_key: bytes

    def __init__(self, db_path: Optional[str] = None, encryption_key: Optional[str] = None,
                 model_loader: Any = None) -> None:
        """
        Initialisiert die psychologische Datenbank
        
        Args:
            db_path: Pfad zur SQLite-Datei (Default: App-Data-Verzeichnis via Resolver)
            encryption_key: Verschlüsselungsschlüssel (wird generiert wenn None)
            model_loader: Optionale ModelLoader-Instanz für KG-Extraktion (verwendet bestehende Instanz aus Chat)
        """
        # Resolve to user-writable app-data dir when not explicitly overridden
        from utils.db_path_resolver import get_wellbeing_path
        self.db_path = db_path or str(get_wellbeing_path())
        self.lock = threading.RLock()

        # Phase E (2026-09-01): one-shot rename of legacy file/key/cache artifacts
        # MUST run BEFORE _init_encryption, because the Fernet key path is derived
        # from db_path ("<db_path>.key"). Switching the resolver to
        # "wellbeing_store.db" while the on-disk files are still
        # "psychological_support.db*" would otherwise generate a FRESH key next to a
        # FRESH empty DB and orphan the real encrypted legacy data (silent,
        # unrecoverable loss). The migration is idempotent (no-op on fresh/
        # already-migrated stores) and fails loud on unsafe conditions.
        from wellbeing.file_migration import migrate_wellbeing_files
        _fm_stats = migrate_wellbeing_files(self.db_path)
        if _fm_stats.get("moved"):
            logger.info(
                "📦 Wellbeing-Datei-/Key-/Cache-Migration: db=%s key=%s cache=%s",
                _fm_stats.get("db"), _fm_stats.get("key"), _fm_stats.get("cache"),
            )

        # Verschlüsselung initialisieren
        self._init_encryption(encryption_key)
        
        # NEU: LLM-KG-Extractor initialisieren (falls verfügbar)
        self.llm_kg_extractor = None
        if KG_EXTRACTION_AVAILABLE and LLMKnowledgeGraphExtractor is not None:
            try:
                # Verwende übergebene ModelLoader-Instanz (z.B. aus Streamlit-Chat)
                self.llm_kg_extractor = LLMKnowledgeGraphExtractor(llm_client=model_loader)
                logger.info("✅ LLM-KG-Extraktor initialisiert" + (" (mit Chat-ModelLoader)" if model_loader else ""))
            except Exception as e:
                logger.warning(f"⚠️ LLM-KG-Extraktor nicht verfügbar: {e}")
        
        # Direkter Zugang zum Profil-Cache für Invalidierung bei KG-Updates
        self._profile_cache = None
        
        # ⚠️ Schema MUSS vor FAISS Manager Init erstellt werden,
        # damit die DB-Datei und die triples-Tabelle existieren!
        self._init_schema()
        
        # 🆕 FAISS Manager für KG-Triples (dediziert, getrennt vom Normal-Chat)
        # Lazy init to avoid optional FAISS side effects in code paths that do not
        # use semantic KG search.
        self.kg_faiss_manager = None
        self._kg_faiss_bootstrapped = False

        eager_faiss_init = str(os.getenv("PSYCHO_KG_FAISS_EAGER_INIT", "0")).strip().lower() in {
            "1", "true", "yes", "on"
        }
        if eager_faiss_init:
            self._ensure_kg_faiss_manager()
        
        # ★ SOTA v4: Entity embedding cache for semantic entity search
        # Version counter detects concurrent invalidation (race-condition protection)
        self._entity_texts: Optional[List[str]] = None
        self._entity_embeddings: Any = None  # numpy ndarray (N, dim) L2-normalized
        self._entity_index_built: bool = False
        self._entity_index_lock = threading.Lock()
        self._entity_index_version: int = 0  # ← Incremented on invalidate
        
        logger.info(f"✅ WellbeingDatabase initialisiert: {db_path}")

    def _ensure_kg_faiss_manager(self) -> None:
        """Initialize psycho KG FAISS manager once on first semantic use."""
        if self._kg_faiss_bootstrapped:
            return

        self._kg_faiss_bootstrapped = True
        try:
            from wellbeing.kg_faiss_manager import get_psycho_kg_faiss_manager
            self.kg_faiss_manager = get_psycho_kg_faiss_manager(
                db_path=self.db_path,
                embedding_dim=1024,
                db_instance=self,
            )
            logger.info("✅ Psycho KG FAISS Manager initialisiert (lazy)")

            try:
                if self.kg_faiss_manager._index_cached() and not self.kg_faiss_manager._is_stale():
                    logger.info("📂 Loading cached FAISS index...")
                    self.kg_faiss_manager.load_index()
                else:
                    logger.info("🔨 Building fresh FAISS index...")
                    self.kg_faiss_manager.build_index()
            except Exception as e:
                logger.warning(f"⚠️ FAISS Index load/build fehlgeschlagen: {e}")
        except Exception as e:
            logger.warning(f"⚠️ KG FAISS Manager Init fehlgeschlagen: {e}")
            self.kg_faiss_manager = None
    
    def set_profile_cache(self, profile_cache: Any) -> None:
        """Inject ProfileCacheManager for profile invalidation on KG updates."""
        self._profile_cache = profile_cache
        logger.info("✅ ProfileCacheManager injiziert (KG-Update → Profil-Invalidierung aktiv)")
    
    def _init_encryption(self, encryption_key: Optional[str] = None) -> None:
        """Initialisiert Verschlüsselung (mit ENV-Fallback und stabilem Schlüsselpfad)"""
        import os
        if encryption_key:
            # Accept either bytes or str for convenience in tests and callers
            if isinstance(encryption_key, (bytes, bytearray)):
                self.encryption_key = bytes(encryption_key)
            else:
                self.encryption_key = str(encryption_key).encode()
        else:
            # ENV-Override erlaubt, alten Schlüssel bereitzustellen
            env_key = os.environ.get("PSYCHO_DB_KEY")
            if env_key:
                # env_key is str (os.environ.get returns str, not bytes)
                self.encryption_key = env_key.encode()
            else:
                # Generiere oder lade Schlüssel aus stabilem, absolutem Pfad
                key_file = os.path.abspath(f"{self.db_path}.key")
                if os.path.exists(key_file):
                    with open(key_file, 'rb') as f:
                        self.encryption_key = f.read()
                else:
                    # Neuer Schlüssel (nur wenn keiner existiert)
                    self.encryption_key = Fernet.generate_key()
                    with open(key_file, 'wb') as f:
                        f.write(self.encryption_key)
                    os.chmod(key_file, 0o600)  # Nur Owner kann lesen
        self.cipher_suite = Fernet(self.encryption_key)

    def _compute_content_hmac(self, content: str) -> str:
        """Compute HMAC-SHA256 of content using the DB encryption key as secret.

        Returns hex digest string.
        """
        try:
            # Canonicalize content before computing HMAC to ensure deterministic
            # fingerprinting independent of trivial formatting differences.
            canonical = self._canonicalize_content(content)
            if not isinstance(self.encryption_key, (bytes, bytearray)):
                key = str(self.encryption_key).encode()
            else:
                key = self.encryption_key
            mac = hmac.new(key, canonical.encode('utf-8'), digestmod=hashlib.sha256)
            return mac.hexdigest()
        except Exception:
            # Fallback to SHA256 hex if HMAC fails for any reason
            return hashlib.sha256(content.encode('utf-8')).hexdigest()

    def _canonicalize_content(self, content: Optional[str]) -> str:
        """Canonicalize text for deterministic fingerprinting.

        Steps:
        - NFKC unicode normalization
        - lowercasing
        - collapse whitespace
        - strip leading/trailing punctuation and common boilerplate
        """
        if not content:
            return ""
        try:
            # Unicode normalize
            txt = unicodedata.normalize('NFKC', content)
            # Lowercase for language-agnostic matching
            txt = txt.lower()
            # Remove common control chars
            txt = txt.replace('\t', ' ').replace('\r', ' ').replace('\n', ' ')
            # Collapse multiple spaces
            while '  ' in txt:
                txt = txt.replace('  ', ' ')
            txt = txt.strip()
            return txt
        except Exception:
            return (content or '').strip()

    # ---------------- SimHash helpers for fuzzy dedup ----------------
    def _compute_simhash(self, text: str, hash_bits: int = 64) -> int:
        """Compute a 64-bit simhash for the given text."""
        try:
            from collections import Counter
            import struct

            # Tokenize on whitespace and punctuation
            tokens = re.findall(r"\w+", text.lower())
            if not tokens:
                return 0

            counts = Counter(tokens)
            v = [0] * hash_bits

            for token, weight in counts.items():
                h = hashlib.md5(token.encode('utf-8')).digest()
                # take first 8 bytes for 64-bit value
                hv = struct.unpack('>Q', h[:8])[0]
                for i in range(hash_bits):
                    bit = 1 if (hv >> i) & 1 else -1
                    v[i] += bit * weight

            # build final hash
            fingerprint = 0
            for i in range(hash_bits):
                if v[i] > 0:
                    fingerprint |= (1 << i)
            # ✅ CRITICAL FIX: Convert unsigned 64-bit to signed 64-bit for SQLite
            # SQLite INTEGER max = 2^63-1 (signed). Unsigned 64-bit values with
            # bit 63 set would exceed this limit → OverflowError.
            if fingerprint >= (1 << 63):
                fingerprint -= (1 << 64)
            return int(fingerprint)
        except Exception:
            return 0

    def _simhash_buckets(self, simhash_value: int) -> Tuple[int, int, int, int]:
        """Split 64-bit simhash into 4 16-bit buckets for LSH-like candidate retrieval."""
        # ✅ FIX: Mask to unsigned 64-bit first, since simhash is stored as signed
        unsigned_val = simhash_value & ((1 << 64) - 1)
        mask = (1 << 16) - 1
        b0 = unsigned_val & mask
        b1 = (unsigned_val >> 16) & mask
        b2 = (unsigned_val >> 32) & mask
        b3 = (unsigned_val >> 48) & mask
        return (b0, b1, b2, b3)

    def _hamming_distance(self, a: int, b: int) -> int:
        # ✅ FIX: Mask to 64 bits before counting, because simhash values are
        # stored as signed 64-bit integers. XOR of two signed ints can produce
        # a negative Python int whose bit_count() counts absolute-value bits.
        x = (a ^ b) & ((1 << 64) - 1)
        return bin(x).count('1')
    
    def _init_schema(self) -> None:
        """Initialisiert Datenbankschema"""
        # Phase E (2026-09-01): De-Klinifizierung des Legacy-Schemas MUSS VOR der
        # DDL laufen — sonst entstehen neben den alten (klinischen) Tabellen leere
        # neue Tabellen, die die vorhandenen Daten stranden lassen (App sähe "leere"
        # Tabellen). Die Migration ist idempotent (_schema_meta-Sentinel) und
        # committet intern; sie wirft bei Namenskonflikten laut (kein Silent-Fallback).
        from wellbeing.schema_migration import migrate_wellbeing_schema
        with self.get_connection() as conn:
            stats = migrate_wellbeing_schema(conn)
            if stats.get("tables") or stats.get("indexes") or stats.get("columns"):
                logger.info(
                    "🔄 Wellbeing-Schema de-klinifiziert: tables=%d indexes=%d columns=%d",
                    stats["tables"], stats["indexes"], stats["columns"],
                )
            else:
                logger.debug(
                    "Wellbeing-Schema: keine Umbenennung nötig "
                    "(neue DB oder bereits migriert, already_migrated=%s)",
                    stats.get("already_migrated"),
                )
            conn.executescript("""
                -- Benutzer-Sessions mit Anonymisierung
                CREATE TABLE IF NOT EXISTS wellbeing_sessions (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    start_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    end_time TIMESTAMP NULL,
                    session_summary TEXT NULL,
                    mood_progression TEXT NULL,
                    care_goals TEXT NULL,
                    privacy_level INTEGER DEFAULT 1,
                    anonymized INTEGER DEFAULT 1,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                
                -- Chat-Interaktionen verschlüsselt
                CREATE TABLE IF NOT EXISTS session_interactions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
                    content TEXT NOT NULL,
                    content_encrypted INTEGER DEFAULT 0,
                    content_hash TEXT DEFAULT NULL,
                    simhash INTEGER DEFAULT NULL,
                    simhash_b0 INTEGER DEFAULT NULL,
                    simhash_b1 INTEGER DEFAULT NULL,
                    simhash_b2 INTEGER DEFAULT NULL,
                    simhash_b3 INTEGER DEFAULT NULL,
                    mood_indicators TEXT NULL,
                    care_notes TEXT NULL,
                    word_count INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (session_id) REFERENCES wellbeing_sessions(id)
                );
                
                -- NEU: Knowledge Graph Tabellen
                CREATE TABLE IF NOT EXISTS knowledge_graph_entities (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT,
                    entity_type TEXT NOT NULL,
                    entity_value TEXT NOT NULL,
                    confidence REAL DEFAULT 0.0,
                    source_interaction_id INTEGER,
                    extraction_method TEXT DEFAULT 'llm',
                    metadata TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (session_id) REFERENCES wellbeing_sessions(id),
                    FOREIGN KEY (source_interaction_id) REFERENCES session_interactions(id)
                );
                
                CREATE TABLE IF NOT EXISTS triples (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT,
                    interaction_id INTEGER,
                    subject TEXT NOT NULL,
                    predicate TEXT NOT NULL,
                    object TEXT NOT NULL,
                    confidence REAL DEFAULT 0.0,
                    extraction_method TEXT DEFAULT 'llm',
                    metadata TEXT,
                    triple_hash TEXT UNIQUE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (session_id) REFERENCES wellbeing_sessions(id),
                    FOREIGN KEY (interaction_id) REFERENCES session_interactions(id)
                );
                
                -- ═══ Wellbeing-Selbstcheck-Ergebnisse (MoodCheck, CalmCheck) ═══
                CREATE TABLE IF NOT EXISTS self_check_results (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    instrument TEXT NOT NULL,
                    total_score INTEGER NOT NULL,
                    severity TEXT NOT NULL,
                    risk_level TEXT NOT NULL,
                    item_scores TEXT NOT NULL,
                    interpretation TEXT,
                    recommendation TEXT,
                    estimation_confidence REAL DEFAULT 0.0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (session_id) REFERENCES wellbeing_sessions(id)
                );

                -- ═══ Outcome-Assessments (Mikro-ORS/SRS) ═══
                CREATE TABLE IF NOT EXISTS progress_assessments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    distress_level INTEGER NOT NULL,
                    hope_level INTEGER NOT NULL,
                    functioning_level INTEGER NOT NULL,
                    session_helpfulness INTEGER NOT NULL,
                    overall_score REAL NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (session_id) REFERENCES wellbeing_sessions(id)
                );

                -- ═══ Homework-Tasks (Zwischen-Session-Aufgaben) ═══
                CREATE TABLE IF NOT EXISTS practice_tasks (
                    task_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    description TEXT NOT NULL,
                    technique_name TEXT,
                    assigned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    due_description TEXT DEFAULT 'bis zur nächsten Session',
                    completed INTEGER DEFAULT 0,
                    completion_notes TEXT,
                    completed_at TIMESTAMP,
                    FOREIGN KEY (session_id) REFERENCES wellbeing_sessions(id)
                );

                -- ═══ Case Formulations (4P-Modell) ═══
                CREATE TABLE IF NOT EXISTS context_formulations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    predisposing TEXT DEFAULT '[]',
                    precipitating TEXT DEFAULT '[]',
                    perpetuating TEXT DEFAULT '[]',
                    protective TEXT DEFAULT '[]',
                    hypotheses TEXT DEFAULT '[]',
                    confidence REAL DEFAULT 0.3,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                -- ═══ Allianz-Scores (pro Interaktion) ═══
                CREATE TABLE IF NOT EXISTS engagement_scores (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    interaction_id INTEGER,
                    score REAL NOT NULL,
                    engagement_level TEXT,
                    trend TEXT,
                    signals TEXT,
                    alert TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (session_id) REFERENCES wellbeing_sessions(id)
                );

                -- ═══ Kumulative Risiko-Bewertungen ═══
                CREATE TABLE IF NOT EXISTS cumulative_risk (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    cumulative_score REAL NOT NULL,
                    risk_level TEXT NOT NULL,
                    trend TEXT,
                    contributing_factors TEXT DEFAULT '[]',
                    recommended_action TEXT,
                    turn_scores TEXT DEFAULT '[]',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (session_id) REFERENCES wellbeing_sessions(id)
                );

                -- ═══ Kontext-Zusammenfassungen (periodisch, session_end, mood_analysis) ═══
                CREATE TABLE IF NOT EXISTS context_summaries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    summary_type TEXT NOT NULL,
                    content TEXT,
                    content_encrypted TEXT,
                    interaction_count INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (session_id) REFERENCES wellbeing_sessions(id)
                );

                -- Canonical psychological insight store. The database owns this
                -- schema because providers read it even when no extractor exists.
                CREATE TABLE IF NOT EXISTS wellbeing_insights (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    insight_type TEXT NOT NULL,
                    category TEXT NOT NULL,
                    value TEXT NOT NULL,
                    encrypted_data TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    temporal_context TEXT NOT NULL,
                    insight_hash TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL,
                    validated_at TEXT NULL,
                    mention_count INTEGER NOT NULL DEFAULT 1,
                    first_session_id TEXT,
                    first_seen_at TEXT,
                    last_seen_at TEXT,
                    correction_status TEXT NOT NULL DEFAULT 'active',
                    corrected_at TEXT NULL,
                    corrected_by TEXT NULL,
                    correction_reason TEXT NULL,
                    FOREIGN KEY (session_id) REFERENCES wellbeing_sessions(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS wellbeing_insight_corrections (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    insight_id INTEGER NOT NULL,
                    user_id TEXT NOT NULL,
                    previous_status TEXT NOT NULL,
                    new_status TEXT NOT NULL,
                    corrected_by TEXT NOT NULL,
                    reason TEXT NULL,
                    replacement_insight_id INTEGER NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (insight_id) REFERENCES wellbeing_insights(id) ON DELETE CASCADE,
                    FOREIGN KEY (replacement_insight_id) REFERENCES wellbeing_insights(id) ON DELETE SET NULL
                );

                -- ★ SOTA v3: Entity Embedding Index für semantische KG-Suche
                CREATE TABLE IF NOT EXISTS kg_entities (
                    entity_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    entity_text TEXT NOT NULL UNIQUE,
                    normalized_text TEXT NOT NULL,
                    entity_type TEXT DEFAULT 'entity',
                    frequency INTEGER DEFAULT 1,
                    first_seen_doc_id TEXT,
                    embedding BLOB
                );

                -- Indizes für Performance
                CREATE INDEX IF NOT EXISTS idx_sessions_user_id ON wellbeing_sessions(user_id);
                CREATE INDEX IF NOT EXISTS idx_interactions_session_id ON session_interactions(session_id);
                CREATE INDEX IF NOT EXISTS idx_entities_session_id ON knowledge_graph_entities(session_id);
                CREATE INDEX IF NOT EXISTS idx_triples_session_id ON triples(session_id);
                CREATE INDEX IF NOT EXISTS idx_triples_hash ON triples(triple_hash);
                -- ★ SOTA v3: Subject/Object indices for KG search performance
                CREATE INDEX IF NOT EXISTS idx_triples_subject ON triples(subject);
                CREATE INDEX IF NOT EXISTS idx_triples_object ON triples(object);
                CREATE INDEX IF NOT EXISTS idx_kg_entities_normalized ON kg_entities(normalized_text);
                CREATE INDEX IF NOT EXISTS idx_selfcheck_user ON self_check_results(user_id);
                CREATE INDEX IF NOT EXISTS idx_selfcheck_session ON self_check_results(session_id);
                CREATE INDEX IF NOT EXISTS idx_progress_session ON progress_assessments(session_id);
                CREATE INDEX IF NOT EXISTS idx_practice_user ON practice_tasks(user_id);
                CREATE INDEX IF NOT EXISTS idx_context_form_user ON context_formulations(user_id);
                CREATE INDEX IF NOT EXISTS idx_engagement_session ON engagement_scores(session_id);
                CREATE INDEX IF NOT EXISTS idx_cumrisk_session ON cumulative_risk(session_id);
                CREATE INDEX IF NOT EXISTS idx_context_summaries_session ON context_summaries(session_id);
                CREATE INDEX IF NOT EXISTS idx_context_summaries_type ON context_summaries(summary_type);
                CREATE INDEX IF NOT EXISTS idx_insights_user_id ON wellbeing_insights(user_id);
                CREATE INDEX IF NOT EXISTS idx_insights_session_id ON wellbeing_insights(session_id);
            """)
            conn.commit()

            self._migrate_insight_schema()
            # C6 (Scope-B, 2026-09-01): Rename der Legacy-Spalte
            # therapeutic_goals -> care_goals (De-Klinifizierung der Benennung).
            # Idempotent, Daten bleiben erhalten.
            self._migrate_rename_goals_column()

        # ── SOTA Upgrade: Schema Evolution VOR der Index-Erstellung ──
        # Bevor wir die Indizes für neuere Spalten (wie content_hash, simhash_*, created_at) 
        # erstellen können, müssen wir sicherstellen, dass ältere Datenbanken 
        # diese Spalten überhaupt besitzen (Schema-Evolution).
        
        # 1. Spalten dynamisch nachrüsten (falls fehlend)
        with self.get_connection() as conn:
            cols_interactions = {row[1] for row in conn.execute("PRAGMA table_info(session_interactions)").fetchall()}
            
            alter_statements = []
            if 'content_hash' not in cols_interactions:
                alter_statements.append("ALTER TABLE session_interactions ADD COLUMN content_hash TEXT DEFAULT NULL")
            if 'simhash' not in cols_interactions:
                alter_statements.append("ALTER TABLE session_interactions ADD COLUMN simhash INTEGER DEFAULT NULL")
            if 'simhash_b0' not in cols_interactions:
                alter_statements.append("ALTER TABLE session_interactions ADD COLUMN simhash_b0 INTEGER DEFAULT NULL")
            if 'simhash_b1' not in cols_interactions:
                alter_statements.append("ALTER TABLE session_interactions ADD COLUMN simhash_b1 INTEGER DEFAULT NULL")
            if 'simhash_b2' not in cols_interactions:
                alter_statements.append("ALTER TABLE session_interactions ADD COLUMN simhash_b2 INTEGER DEFAULT NULL")
            if 'simhash_b3' not in cols_interactions:
                alter_statements.append("ALTER TABLE session_interactions ADD COLUMN simhash_b3 INTEGER DEFAULT NULL")
            # created_at was added historically via alternative migrations, check just in case
            if 'created_at' not in cols_interactions:
                alter_statements.append("ALTER TABLE session_interactions ADD COLUMN created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
                
            for stmt in alter_statements:
                conn.execute(stmt)

            # ── KG triples schema evolution: Bayesian repeat-mention tracking ──
            # mention_count: number of times this triple was extracted (1 = once seen)
            # updated_at:     timestamp of last Bayesian confidence update
            cols_triples = {row[1] for row in conn.execute(
                "PRAGMA table_info(triples)"
            ).fetchall()}
            if 'mention_count' not in cols_triples:
                conn.execute(
                    "ALTER TABLE triples ADD COLUMN mention_count INTEGER DEFAULT 1"
                )
            if 'updated_at' not in cols_triples:
                conn.execute(
                    "ALTER TABLE triples ADD COLUMN updated_at TIMESTAMP"
                )
                # Backfill once: existing rows get their original created_at
                conn.execute(
                    "UPDATE triples SET updated_at = created_at WHERE updated_at IS NULL"
                )

            # 2. Indizes für alle (auch neue) Spalten erzeugen
            conn.executescript("""
                CREATE INDEX IF NOT EXISTS idx_interactions_content_hash ON session_interactions(content_hash);
                CREATE INDEX IF NOT EXISTS idx_interactions_simhash_b0 ON session_interactions(simhash_b0);
                CREATE INDEX IF NOT EXISTS idx_interactions_simhash_b1 ON session_interactions(simhash_b1);
                CREATE INDEX IF NOT EXISTS idx_interactions_simhash_b2 ON session_interactions(simhash_b2);
                CREATE INDEX IF NOT EXISTS idx_interactions_simhash_b3 ON session_interactions(simhash_b3);
                CREATE INDEX IF NOT EXISTS idx_interactions_created_at ON session_interactions(created_at);
                CREATE INDEX IF NOT EXISTS idx_triples_updated_at ON triples(updated_at);
            """)
            conn.commit()

        # ── Migration: Backfill NULL created_at aus der timestamp-Spalte ──
        # Die created_at-Spalte wurde via ALTER TABLE ADD COLUMN mit DEFAULT NULL
        # hinzugefügt. Die originale timestamp-Spalte hat DEFAULT CURRENT_TIMESTAMP
        # und enthält für ALLE Zeilen gültige Werte.
        self._migrate_backfill_created_at()
        # Migration: Backfill content_hash for existing interactions where possible
        self._migrate_backfill_content_hash()
        
        # ── ★ SOTA v4: Deep entity normalization migration ──
        # Re-normalizes all kg_entities.normalized_text with
        # normalize_entity_for_matching() and merges text-level duplicates.
        self._migrate_entity_normalization()
        # ── ★ SOTA: triple_hash resync + dedupe (heals stale hashes that
        # earlier merge code paths left behind). Idempotent.
        self._migrate_triple_hash_v2()
        # Request deduplication is time-bounded. A permanent UNIQUE constraint
        # would suppress legitimate repeated statements later in a session.
        self._migrate_interaction_dedup_window_index()

        # ── ISOLATION HARDENING: cleanup orphan rows + upgrade FKs to CASCADE ──
        # Pre-existing data was created when PRAGMA foreign_keys=OFF (SQLite default)
        # so deletions of wellbeing_sessions left orphaned rows in triples and
        # session_interactions. Those rows reference user_ids that no longer exist
        # → effectively "user-less" data that the per-user filter cannot reach.
        # We delete them here and upgrade the triples FK to ON DELETE CASCADE so
        # future deletions cascade properly even if FK enforcement is later disabled
        # in some external tool.
        self._migrate_cleanup_orphans_and_upgrade_fks()

    def _migrate_insight_schema(self) -> None:
        """Upgrade legacy insight tables before any provider reads them."""
        columns = [
            ("mention_count", "INTEGER NOT NULL DEFAULT 1"),
            ("first_session_id", "TEXT"),
            ("first_seen_at", "TEXT"),
            ("last_seen_at", "TEXT"),
            ("correction_status", "TEXT NOT NULL DEFAULT 'active'"),
            ("corrected_at", "TEXT NULL"),
            ("corrected_by", "TEXT NULL"),
            ("correction_reason", "TEXT NULL"),
        ]
        with self.get_connection() as conn:
            existing = {
                row[1]
                for row in conn.execute("PRAGMA table_info(wellbeing_insights)").fetchall()
            }
            for name, definition in columns:
                if name not in existing:
                    conn.execute(
                        f"ALTER TABLE wellbeing_insights ADD COLUMN {name} {definition}"
                    )
            conn.execute(
                """
                UPDATE wellbeing_insights
                SET first_session_id = COALESCE(first_session_id, session_id),
                    first_seen_at = COALESCE(first_seen_at, created_at),
                    last_seen_at = COALESCE(last_seen_at, created_at),
                    correction_status = COALESCE(correction_status, 'active')
                """
            )
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS wellbeing_insight_corrections (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    insight_id INTEGER NOT NULL,
                    user_id TEXT NOT NULL,
                    previous_status TEXT NOT NULL,
                    new_status TEXT NOT NULL,
                    corrected_by TEXT NOT NULL,
                    reason TEXT NULL,
                    replacement_insight_id INTEGER NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (insight_id) REFERENCES wellbeing_insights(id) ON DELETE CASCADE,
                    FOREIGN KEY (replacement_insight_id) REFERENCES wellbeing_insights(id) ON DELETE SET NULL
                );
                CREATE INDEX IF NOT EXISTS idx_insight_corrections_insight
                    ON wellbeing_insight_corrections(insight_id, created_at);
                CREATE INDEX IF NOT EXISTS idx_insights_active_user
                    ON wellbeing_insights(user_id, correction_status, last_seen_at);
                """
            )
            for table, column in (
                ("wellbeing_insights", "correction_reason"),
                ("wellbeing_insight_corrections", "reason"),
            ):
                rows = conn.execute(
                    f"SELECT id, {column} FROM {table} WHERE {column} IS NOT NULL"
                ).fetchall()
                for row_id, stored_reason in rows:
                    if self._maybe_decrypt(stored_reason) == stored_reason:
                        conn.execute(
                            f"UPDATE {table} SET {column} = ? WHERE id = ?",
                            (self._encrypt_sensitive_data(stored_reason), row_id),
                        )
            conn.commit()
    
    def _migrate_rename_goals_column(self) -> None:
        """Migration: Rename Spalte ``therapeutic_goals`` zu ``care_goals``.

        Scope-B (C6, 2026-09-01): De-Klinifizierung der Legacy-Benennung in der
        Tabelle ``wellbeing_sessions``.  ``ALTER TABLE ... RENAME COLUMN``
        (seit SQLite 3.25 verfügbar; hier 3.49.1) erhält die Spaltendaten
        vollständig.  Idempotent: wird übersprungen, wenn die alte Bezeichnung
        nicht mehr existiert oder die neue bereits gesetzt ist.
        """
        with self.get_connection() as conn:
            cols = {
                row[1]
                for row in conn.execute(
                    "PRAGMA table_info(wellbeing_sessions)"
                ).fetchall()
            }
            if "therapeutic_goals" in cols and "care_goals" not in cols:
                conn.execute(
                    "ALTER TABLE wellbeing_sessions "
                    "RENAME COLUMN therapeutic_goals TO care_goals"
                )
                conn.commit()
                logger.info(
                    "Migration: wellbeing_sessions.therapeutic_goals -> care_goals "
                    "(Daten erhalten)"
                )

    def _migrate_backfill_created_at(self) -> None:
        """Migration: Befüllt NULL created_at aus der timestamp-Spalte.

        Hintergrund:
        Die session_interactions-Tabelle wurde ursprünglich mit einer
        ``timestamp``-Spalte (DEFAULT CURRENT_TIMESTAMP) angelegt.  Später kam
        ``created_at`` via ALTER TABLE ADD COLUMN mit DEFAULT NULL dazu.  Weil
        CREATE TABLE IF NOT EXISTS bei existierender Tabelle ein No-Op ist,
        wurde das gewünschte DEFAULT CURRENT_TIMESTAMP nie wirksam.  Hier
        werden alle NULL-Werte einmalig aus der zuverlässig befüllten
        ``timestamp``-Spalte übertragen.
        """
        try:
            with self.get_connection() as conn:
                # Prüfen, ob beide Spalten existieren
                cols = {row[1] for row in conn.execute(
                    "PRAGMA table_info(session_interactions)"
                ).fetchall()}
                if 'created_at' not in cols or 'timestamp' not in cols:
                    return  # Nichts zu migrieren

                result = conn.execute(
                    "UPDATE session_interactions "
                    "SET created_at = timestamp "
                    "WHERE created_at IS NULL AND timestamp IS NOT NULL"
                )
                if result.rowcount > 0:
                    conn.commit()
                    logger.info(
                        f"✅ Migration: {result.rowcount} Zeilen – "
                        f"created_at aus timestamp-Spalte befüllt"
                    )
        except Exception as e:
            logger.warning(f"⚠️ Migration backfill created_at fehlgeschlagen: {e}")

    def _migrate_backfill_content_hash(self) -> None:
        """Migration: Berechne und befülle `content_hash` für vorhandene session_interactions.

        Versucht, Inhalte zu entschlüsseln; falls nicht möglich, bleibt die Spalte NULL.
        Diese Migration ist best-effort — neue Inserts speichern `content_hash` deterministisch.
        """
        try:
            with self.get_connection() as conn:
                # Column check ist nun redundant (passiert in _init_schema), aber zur Sicherheit:
                cols = {row[1] for row in conn.execute("PRAGMA table_info(session_interactions)").fetchall()}
                if 'content_hash' not in cols:
                    conn.execute("ALTER TABLE session_interactions ADD COLUMN content_hash TEXT DEFAULT NULL")
                    conn.commit()

                rows = conn.execute("SELECT id, content, content_encrypted FROM session_interactions WHERE content_hash IS NULL").fetchall()
                if not rows:
                    return

                updated = 0
                skipped = 0
                failed = 0
                for row in rows:
                    try:
                        rid = row[0]
                        content = row[1]
                        encrypted_flag = row[2]
                        plain = content
                        if encrypted_flag:
                            try:
                                plain = self._maybe_decrypt(content)
                            except Exception as decrypt_exc:
                                logger.debug(f"Migration content_hash: decrypt fehlgeschlagen fuer id={rid}: {decrypt_exc}")
                                plain = None

                        if not plain:
                            # unable to decrypt / compute — skip
                            skipped += 1
                            continue

                        ch = self._compute_content_hmac(plain)
                        conn.execute("UPDATE session_interactions SET content_hash = ? WHERE id = ?", (ch, rid))
                        updated += 1
                    except Exception as row_exc:
                        failed += 1
                        logger.warning(f"Migration content_hash: Verarbeitung fehlgeschlagen fuer id={row[0]}: {row_exc}")
                        continue

                if updated > 0:
                    conn.commit()
                    logger.info(f"✅ Migration: {updated} content_hash Werte befüllt")
                if skipped > 0:
                    logger.info(f"ℹ️ Migration content_hash: {skipped} Zeilen ohne verwertbaren Klartext uebersprungen")
                if failed > 0:
                    logger.warning(f"⚠️ Migration content_hash: {failed} Zeilen wegen Fehlern nicht aktualisiert")

        except Exception as e:
            logger.warning(f"⚠️ Migration backfill content_hash fehlgeschlagen (non-fatal): {e}")

        # Also backfill simhash for existing rows
        try:
            with self.get_connection() as conn:
                cols = {row[1] for row in conn.execute("PRAGMA table_info(session_interactions)").fetchall()}
                if 'simhash' not in cols:
                    conn.execute("ALTER TABLE session_interactions ADD COLUMN simhash INTEGER DEFAULT NULL")
                    conn.execute("ALTER TABLE session_interactions ADD COLUMN simhash_b0 INTEGER DEFAULT NULL")
                    conn.execute("ALTER TABLE session_interactions ADD COLUMN simhash_b1 INTEGER DEFAULT NULL")
                    conn.execute("ALTER TABLE session_interactions ADD COLUMN simhash_b2 INTEGER DEFAULT NULL")
                    conn.execute("ALTER TABLE session_interactions ADD COLUMN simhash_b3 INTEGER DEFAULT NULL")
                    conn.commit()

                rows = conn.execute("SELECT id, content, content_encrypted FROM session_interactions WHERE simhash IS NULL").fetchall()
                if not rows:
                    return

                updated = 0
                skipped = 0
                failed = 0
                for row in rows:
                    try:
                        rid = row[0]
                        content = row[1]
                        encrypted_flag = row[2]
                        plain = content
                        if encrypted_flag:
                            try:
                                plain = self._maybe_decrypt(content)
                            except Exception as decrypt_exc:
                                logger.debug(f"Migration simhash: decrypt fehlgeschlagen fuer id={rid}: {decrypt_exc}")
                                plain = None

                        if not plain:
                            skipped += 1
                            continue

                        sh = self._compute_simhash(plain)
                        b0, b1, b2, b3 = self._simhash_buckets(sh)
                        conn.execute("UPDATE session_interactions SET simhash = ?, simhash_b0 = ?, simhash_b1 = ?, simhash_b2 = ?, simhash_b3 = ? WHERE id = ?",
                                     (sh, b0, b1, b2, b3, rid))
                        updated += 1
                    except Exception as row_exc:
                        failed += 1
                        logger.warning(f"Migration simhash: Verarbeitung fehlgeschlagen fuer id={row[0]}: {row_exc}")
                        continue

                if updated > 0:
                    conn.commit()
                    logger.info(f"✅ Migration: {updated} simhash Werte befüllt")
                if skipped > 0:
                    logger.info(f"ℹ️ Migration simhash: {skipped} Zeilen ohne verwertbaren Klartext uebersprungen")
                if failed > 0:
                    logger.warning(f"⚠️ Migration simhash: {failed} Zeilen wegen Fehlern nicht aktualisiert")
        except Exception as e:
            logger.warning(f"⚠️ Migration backfill simhash failed (non-fatal): {e}")

    # ──────────────────────────────────────────────────────────────────
    #  ★ SOTA v4: Deep entity normalization migration (psycho DB)
    # ──────────────────────────────────────────────────────────────────
    def _migrate_entity_normalization(self) -> None:
        """Re-normalize all kg_entities.normalized_text with SOTA
        normalize_entity_for_matching() (title-strip, case-fold,
        parenthetical removal, whitespace cleanup).

        Idempotent: a sentinel row in ``_schema_meta`` prevents re-runs.
        After re-normalization, text-level duplicate entities are merged
        (highest-frequency entity wins, triples rewritten).
        """
        try:
            with self.get_connection() as conn:
                # ── sentinel table ──────────────────────────────────
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS _schema_meta (
                        key   TEXT PRIMARY KEY,
                        value TEXT
                    )
                    """
                )
                already = conn.execute(
                    "SELECT value FROM _schema_meta WHERE key = 'entity_norm_v4'"
                ).fetchone()
                if already:
                    return  # already applied

                # ── fetch all entities ──────────────────────────────
                rows = conn.execute(
                    "SELECT entity_id, entity_text, normalized_text, frequency "
                    "FROM kg_entities"
                ).fetchall()
                if not rows:
                    conn.execute(
                        "INSERT OR REPLACE INTO _schema_meta VALUES "
                        "('entity_norm_v4', 'done')"
                    )
                    conn.commit()
                    return

                logger.info(
                    f"🔄 SOTA v4 psycho entity normalization migration: "
                    f"re-normalizing {len(rows)} entities …"
                )

                # entity_id → (entity_text, new_norm, frequency)
                norm_groups: dict = {}  # new_norm → list of (entity_id, entity_text, frequency)

                for row in rows:
                    eid = row[0] if isinstance(row, (list, tuple)) else row['entity_id']
                    etxt = row[1] if isinstance(row, (list, tuple)) else row['entity_text']
                    freq = (row[3] if isinstance(row, (list, tuple)) else row['frequency']) or 1
                    new_norm = normalize_entity_for_matching(etxt)
                    norm_groups.setdefault(new_norm, []).append((eid, etxt, freq))

                merged = 0
                updated = 0

                for norm, group in norm_groups.items():
                    if len(group) == 1:
                        eid, _etxt, _freq = group[0]
                        conn.execute(
                            "UPDATE kg_entities SET normalized_text = ? "
                            "WHERE entity_id = ?",
                            (norm, eid),
                        )
                        updated += 1
                    else:
                        # collision → keep highest-frequency entity, merge rest
                        group.sort(key=lambda g: g[2], reverse=True)
                        winner_id, winner_text, _wf = group[0]
                        conn.execute(
                            "UPDATE kg_entities SET normalized_text = ? "
                            "WHERE entity_id = ?",
                            (norm, winner_id),
                        )
                        updated += 1

                        for loser_id, loser_text, _lf in group[1:]:
                            from agent.kg_entity_merge import merge_entity_in_triples
                            merge_entity_in_triples(
                                conn, calculate_triple_hash, winner_text, loser_text
                            )
                            conn.execute(
                                "DELETE FROM kg_entities WHERE entity_id = ?",
                                (loser_id,),
                            )
                            merged += 1

                conn.execute(
                    "INSERT OR REPLACE INTO _schema_meta VALUES "
                    "('entity_norm_v4', 'done')"
                )
                conn.commit()
                logger.info(
                    f"✅ Psycho entity normalization migration complete: "
                    f"{updated} updated, {merged} duplicates merged"
                )

        except Exception as exc:
            logger.warning(
                f"⚠️ Psycho entity normalization migration error (non-fatal): {exc}"
            )

    def _migrate_triple_hash_v2(self) -> None:
        """Heal stale ``triple_hash`` values + SPO duplicates left behind by
        earlier merge code paths that rewrote subject/object without
        recomputing the hash. Idempotent via ``_schema_meta``.
        """
        try:
            with self.get_connection() as conn:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS _schema_meta (
                        key TEXT PRIMARY KEY,
                        value TEXT
                    )
                    """
                )
                already = conn.execute(
                    "SELECT value FROM _schema_meta WHERE key = 'triple_hash_v2'"
                ).fetchone()
                if already:
                    return

                from agent.kg_entity_merge import recompute_and_dedupe_triple_hashes
                stats = recompute_and_dedupe_triple_hashes(
                    conn, calculate_triple_hash
                )
                conn.execute(
                    "INSERT OR REPLACE INTO _schema_meta VALUES "
                    "('triple_hash_v2', 'done')"
                )
                conn.commit()
                logger.info(
                    "✅ Psycho triple_hash v2 migration: scanned=%d rewritten=%d collapsed=%d",
                    stats["rows_scanned"], stats["hashes_rewritten"], stats["collapsed"],
                )
        except Exception as exc:
            logger.warning(
                f"⚠️ Psycho triple_hash v2 migration error (non-fatal): {exc}"
            )

    def _migrate_cleanup_orphans_and_upgrade_fks(self) -> None:
        """Remove orphan rows and upgrade triples FKs to ON DELETE CASCADE.

        Two-step idempotent migration:

        1. **Orphan cleanup**: rows in ``triples`` and ``session_interactions``
           that reference a ``wellbeing_sessions.id`` no longer present.
           Such rows are unreachable through the per-user filter (the
           ``session_id IN (SELECT id FROM wellbeing_sessions WHERE user_id=?)``
           subquery returns nothing for them) but still occupy storage and
           pollute the FAISS index. They were created when SQLite's
           ``PRAGMA foreign_keys=OFF`` (the default) was active, before this
           code base began enforcing it on every connection.

        2. **FK upgrade**: rebuild ``triples`` so that both foreign keys use
           ``ON DELETE CASCADE`` — currently they are ``NO ACTION`` which
           would either block deletes (when FK is on) or silently leave
           orphans (when FK is off). CASCADE matches the semantics of
           ``session_interactions.session_id`` and prevents the same class
           of orphans from re-appearing.

        Both steps are guarded so they execute at most once per upgrade.
        """
        try:
            with self.get_connection() as conn:
                # Step 1: orphan cleanup -------------------------------------
                orphan_triples = conn.execute(
                    "SELECT COUNT(*) FROM triples "
                    "WHERE session_id IS NOT NULL "
                    "  AND session_id NOT IN (SELECT id FROM wellbeing_sessions)"
                ).fetchone()[0]
                orphan_inter = conn.execute(
                    "SELECT COUNT(*) FROM session_interactions "
                    "WHERE session_id NOT IN (SELECT id FROM wellbeing_sessions)"
                ).fetchone()[0]
                if orphan_triples or orphan_inter:
                    # Disable FK enforcement only for this transaction so the
                    # cleanup can proceed even if older orphans violate
                    # NO-ACTION FKs.
                    conn.execute("PRAGMA foreign_keys=OFF")
                    conn.execute("BEGIN")
                    conn.execute(
                        "DELETE FROM triples "
                        "WHERE session_id IS NOT NULL "
                        "  AND session_id NOT IN (SELECT id FROM wellbeing_sessions)"
                    )
                    conn.execute(
                        "DELETE FROM session_interactions "
                        "WHERE session_id NOT IN (SELECT id FROM wellbeing_sessions)"
                    )
                    conn.commit()
                    conn.execute("PRAGMA foreign_keys=ON")
                    logger.warning(
                        f"🧹 Orphan cleanup: removed {orphan_triples} triples + "
                        f"{orphan_inter} session_interactions (sessions deleted "
                        f"while FK-enforcement was off)"
                    )

                # Step 2: FK upgrade -----------------------------------------
                fk_list = conn.execute(
                    "PRAGMA foreign_key_list(triples)"
                ).fetchall()
                # PRAGMA foreign_key_list returns: id, seq, table, from, to,
                #   on_update, on_delete, match
                needs_upgrade = any(
                    row[6] != 'CASCADE' for row in fk_list
                ) if fk_list else False
                if not needs_upgrade:
                    return

                # Capture full schema (columns + index DDL) before rebuild so
                # we recreate everything 1:1.
                col_rows = conn.execute(
                    "PRAGMA table_info(triples)"
                ).fetchall()
                # Collect non-autocreated indices + their DDL
                idx_ddls = [
                    row[0] for row in conn.execute(
                        "SELECT sql FROM sqlite_master "
                        "WHERE type='index' AND tbl_name='triples' "
                        "  AND sql IS NOT NULL"
                    ).fetchall()
                ]

                # Build the column DDL list verbatim so defaults / NOT NULL /
                # UNIQUE constraints are preserved.
                col_defs: list[str] = []
                col_names: list[str] = []
                for cid, name, ctype, notnull, dflt, pk in col_rows:
                    col_names.append(name)
                    parts = [f'"{name}"', ctype or '']
                    if pk:
                        parts.append("PRIMARY KEY AUTOINCREMENT")
                    if notnull and not pk:
                        parts.append("NOT NULL")
                    if dflt is not None:
                        parts.append(f"DEFAULT {dflt}")
                    col_defs.append(" ".join(p for p in parts if p))
                # Re-add UNIQUE constraint on triple_hash (was on original column)
                # and the new CASCADE FKs.
                col_defs.append('UNIQUE("triple_hash")')
                col_defs.append(
                    'FOREIGN KEY ("session_id") '
                    'REFERENCES wellbeing_sessions(id) ON DELETE CASCADE'
                )
                col_defs.append(
                    'FOREIGN KEY ("interaction_id") '
                    'REFERENCES session_interactions(id) ON DELETE CASCADE'
                )

                conn.execute("PRAGMA foreign_keys=OFF")
                conn.execute("BEGIN")
                try:
                    conn.execute(
                        f"CREATE TABLE triples_new ({', '.join(col_defs)})"
                    )
                    quoted_cols = ", ".join(f'"{c}"' for c in col_names)
                    conn.execute(
                        f"INSERT INTO triples_new ({quoted_cols}) "
                        f"SELECT {quoted_cols} FROM triples"
                    )
                    conn.execute("DROP TABLE triples")
                    conn.execute("ALTER TABLE triples_new RENAME TO triples")
                    # Recreate indices (the unique constraint on triple_hash is
                    # already inline; the rest are non-unique helpers).
                    for ddl in idx_ddls:
                        # Skip auto-created unique index for triple_hash since
                        # the inline UNIQUE handles it.
                        if 'sqlite_autoindex' in ddl:
                            continue
                        try:
                            conn.execute(ddl)
                        except sqlite3.OperationalError as idx_exc:
                            if "already exists" in str(idx_exc).lower():
                                logger.debug(f"Index existiert bereits waehrend triples-Rebuild: {idx_exc}")
                            else:
                                raise
                    # Validate FK integrity before commit.
                    bad = conn.execute("PRAGMA foreign_key_check(triples)").fetchall()
                    if bad:
                        raise RuntimeError(
                            f"FK integrity violation after rebuild: {bad[:5]}"
                        )
                    conn.commit()
                    logger.warning(
                        "🔧 triples FKs upgraded to ON DELETE CASCADE "
                        "(rebuilt table, data preserved)"
                    )
                except Exception:
                    conn.rollback()
                    raise
                finally:
                    conn.execute("PRAGMA foreign_keys=ON")
        except Exception as exc:
            logger.warning(
                f"⚠️ Orphan-cleanup / FK-upgrade migration failed (non-fatal): {exc}"
            )

    def _migrate_interaction_dedup_window_index(self) -> None:
        """Replace permanent exact uniqueness with an indexed 30-second lookup."""
        with self.get_connection() as conn:
            columns = {
                row[1]
                for row in conn.execute(
                    "PRAGMA table_info(session_interactions)"
                ).fetchall()
            }
            if "content_hash" not in columns:
                conn.execute(
                    "ALTER TABLE session_interactions "
                    "ADD COLUMN content_hash TEXT DEFAULT NULL"
                )
            conn.execute("DROP INDEX IF EXISTS ux_interactions_session_role_hash")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_interactions_dedup_window "
                "ON session_interactions(session_id, role, content_hash, created_at)"
            )
            conn.commit()

    # ──────────────────────────────────────────────────────────────────
    #  ★ SOTA v4: Entity Resolution via Embedding Similarity (psycho DB)
    # ──────────────────────────────────────────────────────────────────
    def _resolve_duplicate_psycho_entities(
        self, conn: Any, similarity_threshold: float = 0.90
    ) -> Dict[str, Any]:
        """Semantic entity dedup via cosine similarity over kg_entities embeddings.

        Algorithm (Union-Find):
          1. Load all entity embeddings from kg_entities
          2. Compute pairwise cosine similarity matrix (O(N²))
          3. For pairs >= threshold, merge via Union-Find
             (canonical = entity with highest frequency)
          4. Rewrite triples subject/object to canonical form
          5. Delete merged entity rows

        Returns:
            Statistics dict: entities_before, merged_groups, entities_after
        """
        import numpy as np
        stats = {"entities_before": 0, "merged_groups": 0, "entities_after": 0}

        rows = conn.execute(
            "SELECT entity_id, entity_text, normalized_text, frequency, embedding "
            "FROM kg_entities WHERE embedding IS NOT NULL"
        ).fetchall()
        stats["entities_before"] = len(rows)

        if len(rows) < 2:
            stats["entities_after"] = len(rows)
            return stats

        # Build embedding matrix
        entity_ids = []
        entity_texts = []
        entity_freqs = []
        embs = []

        for row in rows:
            if isinstance(row, (list, tuple)):
                eid, etext, _enorm, freq, emb_blob = row
            else:
                eid = row['entity_id']
                etext = row['entity_text']
                freq = row['frequency']
                emb_blob = row['embedding']
            if emb_blob:
                emb = np.frombuffer(emb_blob, dtype=np.float32).copy()
                entity_ids.append(eid)
                entity_texts.append(etext)
                entity_freqs.append(freq or 1)
                embs.append(emb)

        if len(embs) < 2:
            stats["entities_after"] = stats["entities_before"]
            return stats

        emb_matrix = np.stack(embs, axis=0)
        norms = np.linalg.norm(emb_matrix, axis=1, keepdims=True)
        norms = np.maximum(norms, 1e-10)
        emb_matrix = emb_matrix / norms

        # Pairwise cosine similarity
        sim_matrix = emb_matrix @ emb_matrix.T

        # Union-Find
        parent = list(range(len(entity_ids)))

        def find(x: int) -> int:
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(a: int, b: int) -> None:
            ra, rb = find(a), find(b)
            if ra != rb:
                if entity_freqs[ra] >= entity_freqs[rb]:
                    parent[rb] = ra
                else:
                    parent[ra] = rb

        for i in range(len(entity_ids)):
            for j in range(i + 1, len(entity_ids)):
                if sim_matrix[i, j] >= similarity_threshold:
                    union(i, j)

        # Collect merge groups
        groups: Dict[int, List[int]] = {}
        for i in range(len(entity_ids)):
            root = find(i)
            groups.setdefault(root, []).append(i)

        merged_count = 0
        for root, members in groups.items():
            if len(members) <= 1:
                continue

            canonical_idx = max(members, key=lambda idx: entity_freqs[idx])
            canonical_text = entity_texts[canonical_idx]
            canonical_id = entity_ids[canonical_idx]
            raw_non_canonical = [m for m in members if m != canonical_idx]
            raw_merge_texts = [entity_texts[m] for m in raw_non_canonical]
            
            # --- LLM VALIDATION SOTA v4 (CoT and ToT integration) ---
            approved_merge_texts = raw_merge_texts
            approved_indices = raw_non_canonical

            if hasattr(self, "llm_kg_extractor") and self.llm_kg_extractor:
                try:
                    import json, re
                    call_fn = None
                    # Bind to local variable — Pyright can't narrow Optional
                    # through mutable self attributes inside lambdas
                    extractor = self.llm_kg_extractor
                    if hasattr(extractor, "llm_caller") and getattr(extractor, "llm_caller", None):
                        # GuaranteedLLMCaller exposes call_with_guarantee(), not call_llm().
                        approx_prompt_tokens = max(1, len(raw_merge_texts) * 40 + len(canonical_text) // 4)
                        llm_max_tokens = min(2048, max(768, approx_prompt_tokens // 2))
                        call_fn = lambda p, sp: extractor.llm_caller.call_with_guarantee(
                            prompt=p,
                            system_prompt=sp,
                            temperature=0.1,
                            max_tokens=llm_max_tokens,
                        ).response
                    elif hasattr(extractor, "llm_raw") and getattr(extractor, "llm_raw", None):
                        call_fn = lambda p, sp: extractor.llm_raw.query(prompt=p, system_prompt=sp, temperature=0.1)

                    if call_fn:
                        sys_prompt = (
                            "Du bist ein SOTA System zur Graph Entity Resolution.\n"
                            "Wende CoT und ToT an:\n"
                            "1. Deconstruct (CoT): Analysiere das Root-Element und jedes Kandidaten-Element einzeln.\n"
                            "2. Evaluate & Explore (ToT Branching):\n"
                            "   - Path A: Was passiert, wenn wir sie mergen? Gehen wichtige psychologische Nuancen dauerhaft physisch verloren?\n"
                            "   - Path B: Was passiert, wenn wir sie separat behalten? Bleibt der Graph dadurch trennscharf?\n"
                            "3. Conclusion (CoT): Gehören die Kandidaten wirklich physikalisch in denselben Knoten?\n\n"
                            "Gib eine stricte JSON Antwort zurück:\n"
                            "{\n"
                            "  \"reasoning_tot\": \"Analyse...\",\n"
                            "  \"reasoning_cot\": \"Schritte...\",\n"
                            "  \"approved_candidates\": [\"Liste\", \"der\", \"auf jeden Fall identischen\", \"Strings\"]\n"
                            "}"
                        )
                        prompt = f"Root-Entität: \"{canonical_text}\"\nKandidaten: {json.dumps(raw_merge_texts)}\n\nFühre CoT/ToT durch und gib das JSON mit den strikt approvten Elementen zurück."
                        
                        logger.debug(f"[KG-ToT-Validator] Checking candidates against '{canonical_text}'")
                        res_str = call_fn(prompt, sys_prompt)
                        match_json = re.search(r'\{.*\}', res_str, re.DOTALL)
                        if match_json:
                            parsed = json.loads(match_json.group(0))
                            llm_approved = parsed.get("approved_candidates", [])
                            if isinstance(llm_approved, list):
                                approved_merge_texts = [c for c in llm_approved if c in raw_merge_texts]
                                approved_indices = [m for m in raw_non_canonical if entity_texts[m] in approved_merge_texts]
                                logger.info(f"[KG-ToT-Validator] Validation: {len(raw_merge_texts)} -> {len(approved_merge_texts)} approved")
                except Exception as e:
                    logger.warning(f"[KG-ToT-Validator] Graceful Degradation on validation: {e}")

            if not approved_indices:
                continue

            logger.info(f"[KG-Resolve-Psycho] Merging {approved_merge_texts} -> '{canonical_text}'")

            # Shared merge helper guarantees triple_hash recompute + Bayesian
            # Noisy-OR collapse on hash collision (no SPO duplicates survive).
            from agent.kg_entity_merge import merge_entity_in_triples

            for m in approved_indices:
                old_text = entity_texts[m]
                old_id = entity_ids[m]
                merge_entity_in_triples(
                    conn, calculate_triple_hash, canonical_text, old_text
                )
                conn.execute(
                    "UPDATE kg_entities SET frequency = frequency + ? "
                    "WHERE entity_id = ?",
                    (entity_freqs[m], canonical_id),
                )
                conn.execute(
                    "DELETE FROM kg_entities WHERE entity_id = ?",
                    (old_id,),
                )

            merged_count += 1

        conn.commit()
        stats["merged_groups"] = merged_count
        remaining = conn.execute("SELECT COUNT(*) FROM kg_entities").fetchone()
        stats["entities_after"] = remaining[0] if remaining else 0
        logger.info(
            f"[KG-Resolve-Psycho] Done: {stats['entities_before']} → "
            f"{stats['entities_after']} entities ({merged_count} groups merged)"
        )
        return stats

    @contextmanager
    def get_connection(self) -> Generator[sqlite3.Connection, None, None]:
        """Thread-sichere Datenbankverbindung - jeder Thread bekommt eigene Verbindung"""
        conn = None
        try:
            # Erstelle für jeden Thread eine neue Verbindung (thread-sicher)
            conn = sqlite3.connect(
                self.db_path, 
                timeout=30.0,
                check_same_thread=False  # Erlaube Thread-übergreifende Nutzung
            )
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA busy_timeout=15000")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA journal_mode=DELETE")
            # SOTA: synchronous=NORMAL gibt ACID-Compliance ohne
            # Performance-Strafe von FULL (jeder Write wartet auf Disk-Flush).
            # Bei WAL + NORMAL: Checkpoint-flush genügt, Crash-Safety bleibt
            # durch WAL-Log gewährleistet. (SQLite Docs, 2024)
            conn.execute("PRAGMA synchronous=NORMAL")
            # Cache: 64 MB pro Connection (PC hat 64 GB RAM — leicht zu tragen).
            # Negativer Wert = KB; -64000 = 64 MB page cache.
            conn.execute("PRAGMA cache_size=-64000")
            # Temporäre Tabellen/Indices im RAM statt auf Disk
            conn.execute("PRAGMA temp_store=MEMORY")
                    
            yield conn
            
        finally:
            if conn:
                conn.close()
    
    def _encrypt_data(self, data: str) -> str:
        """Verschlüsselt sensitive Daten"""
        if not data:
            return data
        
        try:
            encrypted = self.cipher_suite.encrypt(data.encode())
            return base64.urlsafe_b64encode(encrypted).decode()
        except Exception as e:
            logger.warning(f"Verschlüsselung fehlgeschlagen: {e}")
            return data

    def _encrypt_sensitive_data(self, data: str) -> str:
        """Encrypt sensitive text without permitting a plaintext fallback."""
        if not data:
            return data
        try:
            encrypted = self.cipher_suite.encrypt(data.encode())
            return base64.urlsafe_b64encode(encrypted).decode()
        except Exception as exc:
            raise RuntimeError("Sensitive data encryption failed") from exc
    
    def _decrypt_data(self, encrypted_data: str) -> str:
        """Entschlüsselt Daten (liefert Original bei Fehlschlag)"""
        if not encrypted_data:
            return encrypted_data
        try:
            decoded = base64.urlsafe_b64decode(encrypted_data.encode())
            decrypted = self.cipher_suite.decrypt(decoded)
            return decrypted.decode()
        except Exception as e:
            logger.debug(f"Entschlüsselung fehlgeschlagen (Fallback auf Original): {e}")
            return encrypted_data
    
    def _maybe_decrypt(self, value: Optional[str]) -> Optional[str]:
        """Versucht, einen evtl. verschlüsselten String zu entschlüsseln; sonst Original zurück.

        Heuristik (Zeile 1390):
        `_encrypt_data` kodiert doppelt (Fernet → base64url).  Daher ist ein
        verschlüsselter Wert immer ein gültiges base64url-Token.  Wir versuchen
        zuerst das base64-Decode; wenn es fehlschlägt handelt es sich mit
        hoher Wahrscheinlichkeit um Klartext und wir geben den Originalwert
        zurück, *ohne* `_decrypt_data` aufzurufen (und damit ohne WARNING).
        Nur wenn das Decode gelingt, wird `_decrypt_data` probiert.
        """
        if not value or not isinstance(value, str):
            return value
        # Schnell-Abbruch: Klartext mit typischen Nicht-base64-Zeichen
        # (Leerzeichen, Umlaute, Satzzeichen, etc.) kann nicht doppelt-kodiert sein.
        if any(not c.isalnum() and c not in '-_=' for c in value[:64]):
            return value
        # base64url-Decode als strukturierter Test: nur wenn es gelingt,
        # ist der Wert ein Kandidat für Entschlüsselung.
        try:
            candidate = base64.urlsafe_b64decode(value.encode())
        except Exception:
            return value
        # Kandidat gefunden – _decrypt_data übernimmt den eigentlichen
        # Fernet-Decrypt; bei Fehlschlag gibt es den Originalwert zurück.
        try:
            return self._decrypt_data(value)
        except Exception:
            return value
    
    def create_session(self, user_id: str, care_goals: Optional[str] = None) -> str:
        """
        Erstellt neue psychologische Session
        
        Args:
            user_id: Anonymisierte Benutzer-ID
            care_goals: Care-Ziele (optional)
            
        Returns:
            Session-ID
        """
        session_id = self._generate_session_id(user_id)
        # Speichere Ziele im Klartext (keine Verschlüsselung für bessere Kompatibilität)
        goals_value = care_goals if care_goals else None
        with self.get_connection() as conn:
            conn.execute(
                """
                INSERT INTO wellbeing_sessions 
                (id, user_id, care_goals) 
                VALUES (?, ?, ?)
                """,
                (session_id, user_id, goals_value),
            )
            conn.commit()
        logger.info(f"✅ Session erstellt: {session_id} für User: {user_id}")
        return session_id

    def delete_user_data(self, user_id: str) -> Dict[str, Any]:
        """Atomically remove raw and derived data owned by one canonical user id."""
        if not user_id or not str(user_id).strip():
            raise ValueError("user_id must not be empty")
        canonical_user_id = str(user_id).strip()
        deleted_counts: Dict[str, int] = {}

        with self.get_connection() as conn:
            try:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute("PRAGMA defer_foreign_keys=ON")
                table_names = {
                    str(row[0])
                    for row in conn.execute(
                        "SELECT name FROM sqlite_master "
                        "WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
                    ).fetchall()
                }

                conn.execute("DROP TABLE IF EXISTS temp._delete_sessions")
                conn.execute(
                    "CREATE TEMP TABLE _delete_sessions (id TEXT PRIMARY KEY)"
                )
                conn.execute(
                    "INSERT INTO _delete_sessions(id) "
                    "SELECT id FROM wellbeing_sessions WHERE user_id = ?",
                    (canonical_user_id,),
                )
                session_ids = [
                    str(row[0])
                    for row in conn.execute("SELECT id FROM _delete_sessions").fetchall()
                ]

                conn.execute("DROP TABLE IF EXISTS temp._delete_plans")
                conn.execute("CREATE TEMP TABLE _delete_plans (id INTEGER PRIMARY KEY)")
                if "care_plans" in table_names:
                    conn.execute(
                        "INSERT INTO _delete_plans(id) "
                        "SELECT id FROM care_plans WHERE user_id = ?",
                        (canonical_user_id,),
                    )

                conn.execute("DROP TABLE IF EXISTS temp._delete_goals")
                conn.execute("CREATE TEMP TABLE _delete_goals (id INTEGER PRIMARY KEY)")
                if "plan_goals" in table_names:
                    conn.execute(
                        "INSERT INTO _delete_goals(id) "
                        "SELECT id FROM plan_goals "
                        "WHERE plan_id IN (SELECT id FROM _delete_plans)"
                    )

                dependency_deletes = (
                    (
                        "goal_updates",
                        "goal_id IN (SELECT id FROM _delete_goals) "
                        "OR session_id IN (SELECT id FROM _delete_sessions)",
                    ),
                    (
                        "session_focuses",
                        "plan_id IN (SELECT id FROM _delete_plans) "
                        "OR session_id IN (SELECT id FROM _delete_sessions)",
                    ),
                    ("plan_goals", "plan_id IN (SELECT id FROM _delete_plans)"),
                    ("care_plans", "id IN (SELECT id FROM _delete_plans)"),
                )
                handled_tables = {
                    "goal_updates",
                    "session_focuses",
                    "plan_goals",
                    "care_plans",
                    "session_interactions",
                    "wellbeing_sessions",
                }
                for table_name, predicate in dependency_deletes:
                    if table_name not in table_names:
                        continue
                    cursor = conn.execute(f'DELETE FROM "{table_name}" WHERE {predicate}')
                    deleted_counts[table_name] = int(cursor.rowcount)

                for table_name in sorted(table_names - handled_tables):
                    columns = {
                        str(row[1])
                        for row in conn.execute(
                            f'PRAGMA table_info("{table_name}")'
                        ).fetchall()
                    }
                    predicates: List[str] = []
                    parameters: List[Any] = []
                    if "user_id" in columns:
                        predicates.append("user_id = ?")
                        parameters.append(canonical_user_id)
                    if "session_id" in columns:
                        predicates.append("session_id IN (SELECT id FROM _delete_sessions)")
                    if not predicates:
                        continue
                    cursor = conn.execute(
                        f'DELETE FROM "{table_name}" WHERE ' + " OR ".join(predicates),
                        parameters,
                    )
                    deleted_counts[table_name] = int(cursor.rowcount)

                if "session_interactions" in table_names:
                    cursor = conn.execute(
                        "DELETE FROM session_interactions "
                        "WHERE session_id IN (SELECT id FROM _delete_sessions)"
                    )
                    deleted_counts["session_interactions"] = int(cursor.rowcount)
                cursor = conn.execute(
                    "DELETE FROM wellbeing_sessions "
                    "WHERE id IN (SELECT id FROM _delete_sessions)"
                )
                deleted_counts["wellbeing_sessions"] = int(cursor.rowcount)

                if "kg_entities" in table_names and session_ids:
                    entity_columns = {
                        str(row[1])
                        for row in conn.execute(
                            'PRAGMA table_info("kg_entities")'
                        ).fetchall()
                    }
                    if {
                        "entity_text", "normalized_text", "frequency"
                    }.issubset(entity_columns) and "triples" in table_names:
                        old_entities = {
                            str(row[0]): row
                            for row in conn.execute(
                                "SELECT entity_text, entity_type, first_seen_doc_id, embedding "
                                "FROM kg_entities"
                            ).fetchall()
                        }
                        remaining_entities = conn.execute(
                            "SELECT entity_text, COUNT(*) FROM ("
                            "SELECT subject AS entity_text FROM triples "
                            "WHERE subject IS NOT NULL AND TRIM(subject) != '' "
                            "UNION ALL "
                            "SELECT object AS entity_text FROM triples "
                            "WHERE object IS NOT NULL AND TRIM(object) != ''"
                            ") GROUP BY entity_text"
                        ).fetchall()
                        cursor = conn.execute("DELETE FROM kg_entities")
                        deleted_counts["kg_entities"] = int(cursor.rowcount)
                        for entity_text, frequency in remaining_entities:
                            old = old_entities.get(str(entity_text))
                            conn.execute(
                                "INSERT INTO kg_entities "
                                "(entity_text, normalized_text, entity_type, frequency, "
                                "first_seen_doc_id, embedding) VALUES (?, ?, ?, ?, ?, ?)",
                                (
                                    str(entity_text),
                                    normalize_entity_for_matching(str(entity_text)),
                                    old[1] if old else "entity",
                                    int(frequency),
                                    old[2] if old else None,
                                    old[3] if old else None,
                                ),
                            )
                        deleted_counts["kg_entities_rebuilt"] = len(remaining_entities)
                    else:
                        cursor = conn.execute("DELETE FROM kg_entities")
                        deleted_counts["kg_entities"] = int(cursor.rowcount)

                residuals: Dict[str, int] = {}
                for table_name in sorted(table_names):
                    columns = {
                        str(row[1])
                        for row in conn.execute(
                            f'PRAGMA table_info("{table_name}")'
                        ).fetchall()
                    }
                    predicates = []
                    parameters = []
                    if "user_id" in columns:
                        predicates.append("user_id = ?")
                        parameters.append(canonical_user_id)
                    if "session_id" in columns:
                        predicates.append("session_id IN (SELECT id FROM _delete_sessions)")
                    if not predicates:
                        continue
                    remaining = int(
                        conn.execute(
                            f'SELECT COUNT(*) FROM "{table_name}" WHERE '
                            + " OR ".join(predicates),
                            parameters,
                        ).fetchone()[0]
                    )
                    if remaining:
                        residuals[table_name] = remaining

                for table_name, target_table, foreign_column in (
                    ("goal_updates", "_delete_goals", "goal_id"),
                    ("session_focuses", "_delete_plans", "plan_id"),
                    ("plan_goals", "_delete_plans", "plan_id"),
                    ("care_plans", "_delete_plans", "id"),
                ):
                    if table_name not in table_names:
                        continue
                    remaining = int(
                        conn.execute(
                            f'SELECT COUNT(*) FROM "{table_name}" '
                            f'WHERE "{foreign_column}" IN (SELECT id FROM {target_table})'
                        ).fetchone()[0]
                    )
                    if remaining:
                        residuals[table_name] = remaining

                if residuals:
                    raise RuntimeError(
                        f"User data deletion verification failed: {residuals}"
                    )
                conn.commit()
            except Exception:
                conn.rollback()
                raise

        self.invalidate_entity_index()
        if self.kg_faiss_manager is not None:
            with self.kg_faiss_manager._index_lock:
                self.kg_faiss_manager.index = None
                self.kg_faiss_manager.id_map = []
        if self._profile_cache is not None:
            self._profile_cache.purge_user_memory(canonical_user_id)

        return {
            "user_id": canonical_user_id,
            "session_ids": session_ids,
            "deleted_counts": deleted_counts,
        }
    
    def _generate_session_id(self, user_id: str) -> str:
        """Generiert eindeutige Session-ID"""
        timestamp = datetime.now(timezone.utc).isoformat()
        data = f"{user_id}_{timestamp}_{os.urandom(8).hex()}"
        return hashlib.sha256(data.encode()).hexdigest()[:16]
    
    def save_interaction(self, session_id: str, role: str, content: str, 
                        mood_indicators: Optional[str] = None,
                        care_notes: Optional[str] = None) -> Optional[int]:
        """
        Speichert Chat-Interaktion in Session mit automatischer KG-Extraktion
        Duplicate-Protection verhindert Endlosschleifen
        
        ✅ FORT: FK Validation - Prüft ob Session vor INSERT existiert
        
        Args:
            session_id: Session-ID
            role: 'user' oder 'assistant'
            content: Nachrichteninhalt
            mood_indicators: Erkannte Stimmungsindikatoren (JSON)
            care_notes: Care-Notizen
            
        Returns:
            Interaction-ID
        """
        try:
            # DEBUG-LOG: Einstieg
            logger.info(f"🔍 DB-SAVE: Speichere Interaktion für Session {session_id[:8]}..., Role: {role}")
            logger.info(f"🔍 DB-SAVE: Content-Länge: {len(content)} Zeichen, Mood: {bool(mood_indicators)}")
            
            # DUPLICATE-PROTECTION + INSERT: perform both inside a single DB transaction
            content_hash = self._compute_content_hmac(content)
            logger.debug(f"🔍 DB-SAVE: Content-HMAC: {content_hash}")

            # Verschlüssle sensible Inhalte (compute before acquiring transaction)
            encrypted_content = self._encrypt_data(content)
            content_encrypted = 1 if encrypted_content != content else 0
            word_count = len(content.split()) if content else 0
            logger.info(f"🔍 DB-SAVE: Verschlüsselung: {content_encrypted}, Word-Count: {word_count}")

            with self.get_connection() as conn:
                # Serialize parent validation, duplicate detection and child insert
                # in one write transaction so the session cannot disappear between
                # the existence check and the FK-protected insert.
                conn.execute("BEGIN IMMEDIATE")
                session_check = conn.execute(
                    "SELECT id FROM wellbeing_sessions WHERE id = ?",
                    (session_id,)
                ).fetchone()
                
                if not session_check:
                    # Session existiert nicht! Dies ist ein Fehler, der normalerweise
                    # von SessionManagerAdapter::_validate_session_exists() abgefangen werden sollte.
                    # Nur als Backup loggen und fehlschlagen (nicht automatisch erstellen!)
                    logger.error(
                        f"❌ BACKUP FK-CHECK: Session {session_id[:8]}... existiert nicht! "
                        f"SessionManagerAdapter sollte dies vor add_interaction() validiert haben. "
                        f"Dieser Fehler sollte NICHT vorkommen - überprüfe Handler-Logik!"
                    )
                    return None

                # Compute simhash and bucket values for fuzzy dedup checks
                simhash_val = self._compute_simhash(content)
                sb0, sb1, sb2, sb3 = self._simhash_buckets(simhash_val)

                cursor = conn.execute("""
                    SELECT id, created_at
                    FROM session_interactions
                    WHERE session_id = ? AND role = ?
                      AND content_hash = ?
                                            AND datetime(created_at) >= datetime('now', '-30 seconds')
                                        ORDER BY created_at DESC LIMIT 1
                """, (session_id, role, content_hash))

                duplicate = cursor.fetchone()
                if duplicate:
                    duplicate_id = duplicate[0]
                    try:
                        conn.rollback()
                    except Exception as exc:
                        logger.debug(f"Rollback after exact duplicate detection failed: {exc}")
                    logger.warning(f"🔄 DUPLICATE-PROTECTION: Identische Nachricht bereits gespeichert (ID: {duplicate_id})")
                    logger.warning(f"🔄 DUPLICATE-PROTECTION: KEIN neuer DB-Eintrag erstellt!")
                    return int(duplicate_id) if duplicate_id is not None else None

                # No duplicate: insert atomically
                logger.info(f"✅ DB-SAVE: KEIN DUPLICATE gefunden - führe INSERT innerhalb der Transaktion aus")
                # Before inserting, run fuzzy-simhash + embedding/reranker confirmation
                # Query candidate rows that share at least one simhash bucket
                # ✅ CRITICAL FIX: Apply 30-second time window (like exact-hash check).
                # Without this window, the fuzzy check compared against ALL past
                # interactions, incorrectly rejecting legitimately similar responses
                # from earlier conversation turns (e.g. same care topic)
                # as "duplicates".  The purpose of duplicate protection is to catch
                # Streamlit re-run double-saves (milliseconds apart), NOT to
                # suppress semantically similar but distinct responses.
                try:
                    candidate_rows = conn.execute(
                        "SELECT id, content, content_encrypted, simhash FROM session_interactions "
                        "WHERE session_id = ? AND role = ? "
                        "AND (simhash_b0 = ? OR simhash_b1 = ? OR simhash_b2 = ? OR simhash_b3 = ?) "
                        "AND datetime(created_at, '+30 seconds') > datetime('now') "
                        "ORDER BY created_at DESC LIMIT 50",
                        (session_id, role, sb0, sb1, sb2, sb3)
                    ).fetchall()
                except Exception:
                    candidate_rows = []

                # Evaluate candidates by hamming distance and reranker/embedding confirmation
                for crow in candidate_rows:
                    try:
                        cid = crow[0]
                        ccontent = crow[1]
                        cencrypted = crow[2]
                        csim = crow[3] or 0
                        # compute hamming
                        h = self._hamming_distance(simhash_val, int(csim or 0))
                        if h <= 3:
                            # potential near-duplicate, verify with cross-encoder or embedding
                            try:
                                # decrypt candidate if needed
                                if cencrypted:
                                    candidate_plain = self._maybe_decrypt(ccontent)
                                else:
                                    candidate_plain = ccontent

                                # Guard: candidate_plain must be a non-None string
                                # (_maybe_decrypt returns Optional[str])
                                if candidate_plain is None:
                                    continue

                                # Use cross-encoder reranker if available
                                try:
                                    from agent.cross_encoder_reranker import get_cross_encoder_reranker
                                    reranker = get_cross_encoder_reranker()
                                    rr = reranker.rerank(query=content, candidates=[{'content': candidate_plain}], top_k=1)
                                    score = rr.scores[0] if rr.scores else 0.0
                                    if score >= 0.90:
                                        conn.rollback()
                                        logger.warning(f"🔁 FUZZY DUPLICATE: cross-encoder confirmed duplicate (id={cid}, score={score})")
                                        return int(cid)
                                except Exception:
                                    # fallback to embedding cosine similarity
                                    try:
                                        from utils.embedding_singleton import get_embedding_model
                                        model = get_embedding_model()
                                        emb_q = model.encode([content])[0]
                                        emb_c = model.encode([candidate_plain])[0]
                                        import numpy as np
                                        vq = np.array(emb_q, dtype=np.float32)
                                        vc = np.array(emb_c, dtype=np.float32)
                                        # cosine
                                        denom = (np.linalg.norm(vq) * np.linalg.norm(vc))
                                        sim = float(np.dot(vq, vc) / (denom + 1e-10))
                                        if sim >= 0.92:
                                            conn.rollback()
                                            logger.warning(f"🔁 FUZZY DUPLICATE: embedding confirmed duplicate (id={cid}, sim={sim})")
                                            return int(cid)
                                    except Exception:
                                        logger.debug("Cross-encoder unavailable, falling back to embedding similarity", exc_info=True)
                            except Exception as exc:
                                logger.debug(f"Fuzzy duplicate candidate check failed for candidate {cid}: {exc}")
                    except Exception as bucket_exc:
                        logger.debug(f"Fuzzy duplicate bucket evaluation failed: {bucket_exc}")
                        continue

                cursor = conn.execute("""
                    INSERT INTO session_interactions
                    (session_id, role, content, content_encrypted, content_hash, mood_indicators,
                     care_notes, word_count, simhash, simhash_b0, simhash_b1, simhash_b2, simhash_b3, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """, (session_id, role, encrypted_content, content_encrypted, content_hash,
                      mood_indicators, care_notes, word_count, simhash_val, sb0, sb1, sb2, sb3))
                interaction_id_raw = cursor.lastrowid
                interaction_id: Optional[int] = int(interaction_id_raw) if interaction_id_raw is not None else None
                logger.info(f"✅ DB-SAVE: INSERT erfolgreich, Interaktion-ID: {interaction_id}")

                # Post-insert invariant check
                if interaction_id is not None and not self._verify_interaction_invariants(conn, interaction_id):
                    logger.error(f"❌ SOTA Invariant Violation: Interaction {interaction_id} failed checks")
                    conn.execute("ROLLBACK")
                    return None

                # Update session timestamp
                conn.execute("""
                    UPDATE wellbeing_sessions
                    SET updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                """, (session_id,))

                conn.commit()
                logger.info(f"✅ DB-SAVE: Transaction committed - Interaktion final gespeichert!")
            
            logger.info(f"✅✅ DB-SAVE KOMPLETT ERFOLG: Interaktion {interaction_id} in Session {session_id[:8]}...")
            
            # NEU: Automatische KG-Extraktion nach der Interaktion (mit zusätzlichem Schutz)
            # 🔥 KRITISCH: NUR User-Messages für KG-Extraktion verwenden!
            if (self.llm_kg_extractor and content and len(content.strip()) > 50 
                and interaction_id is not None and not self._is_system_message(content)
                and role == 'user'):  # ✅ FILTER: Nur User-Nachrichten!
                try:
                    logger.info(f"🧠 DB-SAVE: Starte KG-Extraktion für USER-Interaktion {interaction_id}")
                    
                    # Hole user_name aus der Session für konsistente KG-Triples
                    user_name = None
                    try:
                        with self.get_connection() as conn:
                            cursor = conn.execute(
                                "SELECT user_id FROM wellbeing_sessions WHERE id = ?", 
                                (session_id,)
                            )
                            row = cursor.fetchone()
                            if row:
                                user_name = row[0]  # user_id ist oft der angezeigte Name
                    except Exception:
                        logger.debug("Could not resolve user_name for KG extraction context", exc_info=True)
                    
                    self._create_automatic_knowledge_graph_for_interaction(
                        session_id, interaction_id, content, role, mood_indicators, user_name
                    )
                    logger.info(f"✅ DB-SAVE: KG-Extraktion erfolgreich")
                except Exception as kg_error:
                    # WICHTIG: KG-Fehler darf Speicherung nicht verhindern!
                    logger.error(f"❌ DB-SAVE: KG-Extraktion fehlgeschlagen (Interaktion aber gespeichert): {kg_error}")
            elif role != 'user':
                logger.debug(f"🔍 DB-SAVE: KG-Extraktion übersprungen für role={role} (nur 'user' erlaubt)")
            else:
                logger.debug(f"🔍 DB-SAVE: KG-Extraktion übersprungen (Extractor: {bool(self.llm_kg_extractor)}, Länge: {len(content.strip())})")

            
            return interaction_id

        except Exception as e:
            logger.error(f"❌❌ DB-SAVE KOMPLETTER FEHLER: Interaktion-Speicherung fehlgeschlagen: {e}")
            import traceback
            logger.error(f"Traceback: {traceback.format_exc()}")
            return None

    def _session_exists_with_details(self, conn: sqlite3.Connection, session_id: str) -> bool:
        """
        SOTA: Enhanced session existence check with detailed diagnostics.

        Args:
            conn: Database connection
            session_id: Session ID to check

        Returns:
            True if session exists, False otherwise
        """
        result = conn.execute(
            "SELECT id, user_id FROM wellbeing_sessions WHERE id = ? LIMIT 1",
            (session_id,)
        ).fetchone()

        if result:
            logger.debug(f"✅ Session {session_id[:12]}... exists (User: {result[1]})")
            return True
        else:
            logger.warning(f"⚠️  Session {session_id[:12]}... not found in wellbeing_sessions")
            return False

    def _verify_interaction_invariants(self, conn: sqlite3.Connection, interaction_id: int) -> bool:
        """
        SOTA: Verify critical invariants after interaction insert.

        Args:
            conn: Database connection
            interaction_id: ID of the inserted interaction

        Returns:
            True if all invariants are satisfied, False otherwise
        """
        # Check interaction exists
        interaction = conn.execute(
            "SELECT id, session_id FROM session_interactions WHERE id = ?",
            (interaction_id,)
        ).fetchone()

        if not interaction:
            logger.error(f"❌ Invariant: Interaction {interaction_id} not found after insert")
            return False

        # Check session still exists
        session_id = interaction[1]
        session_exists = conn.execute(
            "SELECT id FROM wellbeing_sessions WHERE id = ?",
            (session_id,)
        ).fetchone()

        if not session_exists:
            logger.error(f"❌ Invariant: Session {session_id[:12]}... disappeared during transaction")
            return False

        logger.debug(f"✅ All invariants verified for interaction {interaction_id}")
        return True

    def _create_automatic_knowledge_graph_for_interaction(self, session_id: str, interaction_id: int,
                                                        content: str, role: str, 
                                                        mood_indicators: Optional[str] = None,
                                                        user_name: Optional[str] = None) -> None:
        """
        Automatische KG-Extraktion für eine einzelne Interaktion (wie im RAG-Store)
        
        Args:
            session_id: Session-ID
            interaction_id: Interaktion-ID
            content: Nachrichteninhalt
            role: 'user' oder 'assistant'
            mood_indicators: Stimmungsindikatoren (JSON)
            user_name: Name des Benutzers für konsistente KG-Triples (optional)
        """
        if not self.llm_kg_extractor:
            return
        
        try:
            # Mindestlänge prüfen (KG nur für substanzielle Texte)
            if len(content.strip()) < 100:
                logger.debug(f"📋 Interaktion {interaction_id} zu kurz für KG ({len(content)} Zeichen)")
                return
            
            # LLM-KG-Extraktion mit psychologischem Kontext
            logger.info(f"🧠 Erstelle LLM-KG für Interaktion {interaction_id} ({len(content)} Zeichen)")
            
            doc_context = {
                "doc_id": f"interaction_{interaction_id}",
                "session_id": session_id,
                "role": role,
                "source_type": "psychology",
                "interaction_type": "wellbeing_conversation",
                "user_name": user_name or ""  # Name für konsistente KG-Triples
            }
            
            # LLM-KG-Extraktion
            kg_triples = self.llm_kg_extractor.extract_knowledge_graph(content, doc_context)
            
            if not kg_triples:
                logger.debug(f"⚠️ Keine KG-Triples für Interaktion {interaction_id} extrahiert")
                return
            
            # Konvertiere zu Tuple-Format
            result_triples = []
            for triple in kg_triples:
                if hasattr(triple, 'to_tuple'):
                    result_triples.append(triple.to_tuple())
                else:
                    result_triples.append((triple.subject, triple.predicate, triple.object))
            
            # Speichere Triples in der Datenbank
            with self.get_connection() as conn:
                interaction_triples_count = 0
                inserted_triples: List[Tuple[int, str, str, str]] = []  # (id, s, p, o) for FAISS incremental add
                bayesian_updates = 0

                for subject, predicate, obj in result_triples:
                    try:
                        # ✅ WICHTIG: Normalisiere alle Triple-Felder vor dem Insert
                        # (Entfernt Unterstriche, konsistente Formatierung)
                        subject = normalize_text(subject)
                        predicate = normalize_text(predicate)
                        obj = normalize_text(obj)
                        
                        # Berechne Hash für das Triple
                        triple_hash = calculate_triple_hash(subject, predicate, obj)
                        
                        # ✅ NEU: Adaptive Confidence-Berechnung (für DIESES Vorkommen)
                        try:
                            from utils.adaptive_confidence_scorer import score_triple_confidence
                            confidence = score_triple_confidence(
                                subject=subject,
                                predicate=predicate,
                                obj=obj,
                                source_text=content,
                                extraction_method='llm_enhanced',
                                created_at=datetime.now(),
                                metadata={
                                    'temperature': 0.1,
                                    'retries': 0,
                                    'content_length': len(content),
                                    'role': role
                                }
                            )
                            logger.debug(f"🎯 Adaptive Confidence: {confidence:.3f} für {subject}|{predicate}|{obj}")
                        except Exception as conf_err:
                            logger.warning(f"⚠️ Adaptive Confidence fehlgeschlagen, Fallback: {conf_err}")
                            confidence = 0.75  # Fallback auf konservative Confidence

                        # Prüfe, ob Triple mit diesem Hash bereits existiert.
                        # SOTA Repeat-Mention-Update (Noisy-OR Bayesian):
                        #   P_new = 1 - (1 - P_old) * (1 - P_evidence)
                        # Bei jedem erneuten Vorkommen wächst die Confidence
                        # monoton gegen 1.0; mention_count wird inkrementiert.
                        cursor = conn.execute(
                            "SELECT id, confidence FROM triples WHERE triple_hash = ?",
                            (triple_hash,)
                        )
                        existing = cursor.fetchone()

                        if existing is not None:
                            existing_id = existing[0]
                            old_conf = float(existing[1] or 0.0)
                            # Noisy-OR combination, hard cap at 0.99 (never absolute)
                            new_conf = 1.0 - (1.0 - old_conf) * (1.0 - confidence)
                            new_conf = min(0.99, max(old_conf, new_conf))
                            conn.execute("""
                                UPDATE triples
                                SET confidence = ?,
                                    mention_count = COALESCE(mention_count, 1) + 1,
                                    updated_at = CURRENT_TIMESTAMP
                                WHERE id = ?
                            """, (new_conf, existing_id))
                            bayesian_updates += 1
                            logger.debug(
                                f"🔁 Repeat-mention Bayesian update: id={existing_id} "
                                f"conf {old_conf:.3f} → {new_conf:.3f}"
                            )
                            continue

                        # Füge neues Triple hinzu (kein Duplikat)
                        cursor = conn.execute("""
                            INSERT INTO triples(session_id, interaction_id, subject, predicate, object,
                                              confidence, extraction_method, metadata, triple_hash,
                                              mention_count, updated_at)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, CURRENT_TIMESTAMP)
                        """, (session_id, interaction_id, subject, predicate, obj,
                              confidence, 'llm_enhanced', json.dumps({
                                  "kg_source": "llm_enhanced",
                                  "content_length": len(content),
                                  "role": role,
                                  "created_at": datetime.now().isoformat(),
                                  "confidence_method": "adaptive"
                              }), triple_hash))

                        new_id = cursor.lastrowid
                        if new_id is not None:
                            inserted_triples.append((int(new_id), subject, predicate, obj))
                        interaction_triples_count += 1
                        logger.debug(f"✅ Neues Triple eingefügt: {subject} → {predicate} → {obj}")
                        
                        # Invalidiere Profil-Cache nach KG-Update
                        if self._profile_cache is not None:
                            try:
                                user_id_cursor = conn.execute(
                                    "SELECT user_id FROM wellbeing_sessions WHERE id = ?",
                                    (session_id,)
                                )
                                user_row = user_id_cursor.fetchone()
                                if user_row:
                                    self._profile_cache.invalidate_profile_in_transaction(
                                        user_id=user_row['user_id'],
                                        trigger_type='kg_update',
                                        trigger_source_id=triple_hash,
                                        conn=conn,
                                    )
                            except Exception as inv_err:
                                logger.debug(f"⚠️ Profil-Invalidierung übersprungen: {inv_err}")
                        
                    except Exception as e:
                        logger.debug(f"❌ Triple-Insert fehlgeschlagen: {e}")
                
                conn.commit()
                logger.info(
                    f"✅ KG-Update Interaktion {interaction_id}: "
                    f"{interaction_triples_count} neu, {bayesian_updates} Bayesian-update"
                )

                # ★ SOTA: Inkrementelles FAISS-Update für neue Triples
                # → Ohne diesen Schritt bleibt der HNSW-Index bis zum nächsten
                #   Startup oder Full-Rebuild stale ⇒ neu extrahierte Fakten
                #   sind in der laufenden Session unsichtbar.
                self._ensure_kg_faiss_manager()
                if inserted_triples and self.kg_faiss_manager is not None:
                    try:
                        added = self.kg_faiss_manager.add_triples_incremental(inserted_triples)
                        logger.info(
                            f"📥 FAISS incremental add: {added}/{len(inserted_triples)} triples"
                        )
                    except Exception as e_inc:
                        logger.warning(
                            f"⚠️ FAISS incremental add failed (full rebuild on next startup): {e_inc}"
                        )

                # ★ SOTA v4: Store entity embeddings for new triples
                if interaction_triples_count > 0:
                    try:
                        stored = self._store_psycho_entity_embeddings(conn, kg_triples)
                        # ★ SOTA v4: Auto entity resolution after new entities added
                        if stored > 0:
                            self.invalidate_entity_index()  # ★ force re-load on next search
                            try:
                                self._resolve_duplicate_psycho_entities(
                                    conn, similarity_threshold=0.90
                                )
                            except Exception as e_res:
                                logger.debug(f"Entity resolution deferred: {e_res}")
                    except Exception as e_emb:
                        logger.debug(f"Entity embedding storage failed (non-fatal): {e_emb}")
                
        except Exception as e:
            logger.error(f"❌ KG-Erstellung für Interaktion {interaction_id} fehlgeschlagen: {e}")

    def _store_psycho_entity_embeddings(
        self, conn: Any, triples: List[Any]
    ) -> int:
        """
        ★ SOTA v3: Embed and store entities from psychological KG triples.

        Extracts unique subjects/objects, embeds them, stores in kg_entities table.
        Uses the same embedding model as the KG FAISS manager.
        """
        try:
            import numpy as np
            from utils.embedding_singleton import get_embedding_model
        except ImportError:
            return 0

        # Collect unique entities
        unique_entities: Dict[str, str] = {}  # normalized → original
        for triple in triples:
            if hasattr(triple, 'subject'):
                subject = str(triple.subject).strip()
                obj = str(triple.object).strip()
            elif isinstance(triple, (list, tuple)) and len(triple) >= 3:
                subject = str(triple[0]).strip()
                obj = str(triple[2]).strip()
            else:
                continue
            
            for entity in [subject, obj]:
                if len(entity) >= 2:
                    # ★ SOTA v4: Deep normalization (title-strip, case-fold,
                    #   parenthetical removal) — consistent with RAG store
                    normalized = normalize_entity_for_matching(entity)
                    if normalized and normalized not in unique_entities:
                        unique_entities[normalized] = entity

        if not unique_entities:
            return 0

        # Filter out already-embedded entities
        new_entities = []
        for normalized, original in unique_entities.items():
            try:
                cursor = conn.execute(
                    "SELECT entity_id FROM kg_entities WHERE normalized_text = ?",
                    (normalized,)
                )
                row = cursor.fetchone()
                if row:
                    conn.execute(
                        "UPDATE kg_entities SET frequency = frequency + 1 WHERE normalized_text = ?",
                        (normalized,)
                    )
                else:
                    new_entities.append((normalized, original))
            except Exception:
                new_entities.append((normalized, original))

        if not new_entities:
            return 0

        # Batch-embed
        try:
            embedding_model = get_embedding_model()
            entity_texts = [orig for _, orig in new_entities]
            embeddings = embedding_model.encode(entity_texts)

            stored = 0
            for i, (normalized, original) in enumerate(new_entities):
                try:
                    emb = np.array(embeddings[i], dtype=np.float32)
                    emb_blob = emb.tobytes()
                    conn.execute("""
                        INSERT OR IGNORE INTO kg_entities
                        (entity_text, normalized_text, entity_type, frequency, embedding)
                        VALUES (?, ?, 'entity', 1, ?)
                    """, (original, normalized, emb_blob))
                    stored += 1
                except Exception as e:
                    logger.debug(f"Entity insert failed for '{original}': {e}")

            conn.commit()
            logger.info(
                f"[KG-Entities] Psycho: Embedded {stored}/{len(new_entities)} new entities"
            )
            return stored
        except Exception as e:
            logger.warning(f"[KG-Entities] Psycho batch embedding failed: {e}")
            return 0

    def get_session_interactions(self, session_id: str, decrypt: bool = True) -> List[Dict[str, Any]]:
        """
        Ruft alle Interaktionen einer Session ab
        
        Args:
            session_id: Session-ID
            decrypt: Entschlüssele Inhalte automatisch
            
        Returns:
            Liste der Interaktionen
        """
        with self.get_connection() as conn:
            cursor = conn.execute("""
                SELECT id, role, content, content_encrypted, mood_indicators, 
                       care_notes, word_count, created_at
                FROM session_interactions 
                WHERE session_id = ?
                ORDER BY created_at ASC
            """, (session_id,))
            
            interactions = []
            for row in cursor.fetchall():
                interaction = dict(row)
                
                # Entschlüssele Content falls nötig
                if decrypt and interaction['content_encrypted']:
                    interaction['content'] = self._decrypt_data(interaction['content'])
                
                interactions.append(interaction)
        
        return interactions
    
    def search_enhanced_content(self, query: str, session_id: Optional[str] = None,
                              use_kg: bool = True, limit: int = 10) -> List[Dict[str, Any]]:
        """
        Erweiterte Suche mit Knowledge Graph-Unterstützung
        
        Args:
            query: Suchbegriff
            session_id: Spezifische Session (optional)
            use_kg: Nutze Knowledge Graph für bessere Ergebnisse
            limit: Maximale Anzahl Ergebnisse
            
        Returns:
            Liste relevanter Inhalte
        """
        results = []
        
        with self.get_connection() as conn:
            # 1. Direkte Textsuche in Interaktionen (mit normalisiertem Query)
            normalized_query = normalize_search_query(query)
            text_search_sql = """
                SELECT si.id, si.session_id, si.role, si.content, si.content_encrypted,
                       si.mood_indicators, si.created_at, ps.user_id
                FROM session_interactions si
                JOIN wellbeing_sessions ps ON si.session_id = ps.id
                WHERE si.content LIKE ?
            """
            params = [f"%{normalized_query}%"]
            
            if session_id:
                text_search_sql += " AND si.session_id = ?"
                params.append(session_id)
            
            text_search_sql += " ORDER BY si.created_at DESC LIMIT ?"
            params.append(str(limit))
            
            cursor = conn.execute(text_search_sql, params)
            
            for row in cursor.fetchall():
                result = dict(row)
                # Entschlüssele bei Bedarf
                if result['content_encrypted']:
                    result['content'] = self._decrypt_data(result['content'])
                result['search_type'] = 'text'
                results.append(result)
            
            # 2. Knowledge Graph-basierte Suche (wenn verfügbar) mit flexibler Suche
            if use_kg:
                simple_pattern = f"%{normalize_search_query(query)}%"
                flexible_pattern = create_flexible_search_pattern(query)
                kg_search_sql = """
                    SELECT DISTINCT si.id, si.session_id, si.role, si.content, si.content_encrypted,
                           si.mood_indicators, si.created_at, ps.user_id,
                           t.subject, t.predicate, t.object, t.confidence
                    FROM triples t
                    JOIN session_interactions si ON t.interaction_id = si.id
                    JOIN wellbeing_sessions ps ON si.session_id = ps.id
                    WHERE (t.subject LIKE ? OR t.predicate LIKE ? OR t.object LIKE ?
                           OR t.subject LIKE ? OR t.predicate LIKE ? OR t.object LIKE ?)
                """
                kg_params = [simple_pattern, simple_pattern, simple_pattern,
                            flexible_pattern, flexible_pattern, flexible_pattern]
                
                if session_id:
                    kg_search_sql += " AND si.session_id = ?"
                    kg_params.append(session_id)
                
                kg_search_sql += " ORDER BY t.confidence DESC, si.created_at DESC LIMIT ?"
                kg_params.append(str(limit))
                
                cursor = conn.execute(kg_search_sql, kg_params)
                
                for row in cursor.fetchall():
                    result = dict(row)
                    # Entschlüssele bei Bedarf
                    if result['content_encrypted']:
                        result['content'] = self._decrypt_data(result['content'])
                    result['search_type'] = 'knowledge_graph'
                    result['kg_triple'] = {
                        'subject': result['subject'],
                        'predicate': result['predicate'],
                        'object': result['object'],
                        'confidence': result['confidence']
                    }
                    results.append(result)
        
        # Entferne Duplikate und sortiere nach Relevanz
        unique_results = []
        seen_ids = set()
        
        for result in results:
            if result['id'] not in seen_ids:
                unique_results.append(result)
                seen_ids.add(result['id'])
        
        return unique_results[:limit]
    
    def search_knowledge_graph(
        self, 
        query: str, 
        user_id: Optional[str] = None,
        session_id: Optional[str] = None,
        limit: int = 10,
        min_confidence: float = 0.5
    ) -> List[Dict[str, Any]]:
        """
        ★ SOTA v3: Semantische Suche in Knowledge Graph Triples
        
        Pipeline (priorisiert):
        1. FAISS Semantic Search (wenn kg_faiss_manager verfügbar) — schnell + semantisch
        2. Fallback: SQL LIKE-basierte Suche (wenn FAISS nicht verfügbar)
        
        Args:
            query: Suchbegriff (z.B. "Alptraum", "Angst", "Familie")
            user_id: Optional - Filtere nur Triples dieses Users
            session_id: Optional - Filtere nur Triples dieser Session
            limit: Maximale Anzahl Ergebnisse
            min_confidence: Minimale Confidence (0.0-1.0)
            
        Returns:
            Liste von relevanten KG-Triples mit Kontext
        """
        # 🔒 ISOLATION: user_id is REQUIRED. Falsy user_id would let the SQL
        # fallback skip the user-filter clause and return triples of ALL users.
        if not user_id:
            raise ValueError(
                "search_knowledge_graph requires a non-empty user_id "
                "to enforce per-user data isolation."
            )

        self._ensure_kg_faiss_manager()

        # ── Step 1: Try FAISS Semantic Search first (fast + semantic) ──
        if self.kg_faiss_manager is not None:
            try:
                faiss_results = self.kg_faiss_manager.search(
                    query=query,
                    user_id=user_id,
                    session_id=session_id,
                    top_k=limit,
                    min_similarity=0.50,
                )
                if faiss_results:
                    # Filter by min_confidence
                    filtered = [
                        r for r in faiss_results
                        if r.get('confidence', 0) >= min_confidence
                    ]
                    if filtered:
                        logger.info(
                            f"🔍 KG-Suche (FAISS) für '{query}': {len(filtered)} Triples "
                            f"(von {len(faiss_results)} FAISS-Hits)"
                        )
                        return filtered[:limit]
            except Exception as e:
                logger.warning(f"⚠️ FAISS KG-Suche fehlgeschlagen, Fallback auf SQL: {e}")

        # ── Step 2: Fallback — SQL LIKE-basierte Suche ──
        with self.get_connection() as conn:
            sql = """
                SELECT 
                    t.subject, t.predicate, t.object, t.confidence,
                    t.extraction_method, t.metadata, t.created_at,
                    t.session_id, t.interaction_id,
                    si.content as interaction_content,
                    si.role as interaction_role,
                    si.created_at as interaction_date
                FROM triples t
                LEFT JOIN session_interactions si ON t.interaction_id = si.id
                WHERE (
                    t.subject LIKE ? OR 
                    t.predicate LIKE ? OR 
                    t.object LIKE ? OR
                    t.subject LIKE ? OR 
                    t.predicate LIKE ? OR 
                    t.object LIKE ?
                )
                AND t.confidence >= ?
            """
            
            simple_pattern = f"%{normalize_search_query(query)}%"
            flexible_pattern = create_flexible_search_pattern(query)
            params: list = [simple_pattern, simple_pattern, simple_pattern, 
                     flexible_pattern, flexible_pattern, flexible_pattern, min_confidence]
            
            if user_id:
                sql += """ 
                    AND t.session_id IN (
                        SELECT id FROM wellbeing_sessions WHERE user_id = ?
                    )
                """
                params.append(user_id)
            
            if session_id:
                sql += " AND t.session_id = ?"
                params.append(session_id)
            
            sql += " ORDER BY t.confidence DESC, t.created_at DESC LIMIT ?"
            params.append(limit)
            
            cursor = conn.execute(sql, params)
            
            results = []
            for row in cursor.fetchall():
                triple_dict = dict(row)
                
                try:
                    if triple_dict.get('metadata'):
                        triple_dict['metadata'] = json.loads(triple_dict['metadata'])
                except (json.JSONDecodeError, TypeError):
                    triple_dict['metadata'] = {}
                
                if triple_dict.get('interaction_content'):
                    try:
                        triple_dict['interaction_content'] = self._decrypt_data(
                            triple_dict['interaction_content']
                        )
                    except Exception as exc:
                        logger.debug(f"Could not decrypt interaction_content for triple {triple_dict.get('id')}: {exc}")
                
                results.append(triple_dict)
            
            logger.info(f"🔍 KG-Suche (SQL) für '{query}': {len(results)} Triples gefunden")
            return results

    def get_high_confidence_triples(
        self,
        user_id: Optional[str] = None,
        limit: int = 30,
        min_confidence: float = 0.5
    ) -> List[Dict[str, Any]]:
        """
        Retrieve top-N triples sorted by confidence, WITHOUT semantic similarity filtering.

        Used as a baseline context layer to ensure that important personal facts
        (family relationships, life events, persistent patterns) are always present
        in the LLM context, even when the user's current question is semantically
        unrelated to those facts (e.g., a meta-question like "do you have info about me?").

        Args:
            user_id: Optional — if set, restrict to sessions of this user.
                     None = single-user system, all triples.
            limit: Max number of triples to return (default 30).
            min_confidence: Minimum confidence threshold (default 0.5).

        Returns:
            List of triple dicts with: subject, predicate, object, confidence,
            similarity (fixed at 0.5 = neutral), combined_score, rerank_score,
            entity_score, source_date.
        """
        with self.get_connection() as conn:
            if user_id:
                sql = """
                    SELECT t.subject, t.predicate, t.object, t.confidence, t.created_at
                    FROM triples t
                    WHERE t.confidence >= ?
                      AND t.session_id IN (
                          SELECT id FROM wellbeing_sessions WHERE user_id = ?
                      )
                    ORDER BY t.confidence DESC, t.created_at DESC
                    LIMIT ?
                """
                rows = conn.execute(sql, (min_confidence, user_id, limit)).fetchall()
            else:
                sql = """
                    SELECT t.subject, t.predicate, t.object, t.confidence, t.created_at
                    FROM triples t
                    WHERE t.confidence >= ?
                    ORDER BY t.confidence DESC, t.created_at DESC
                    LIMIT ?
                """
                rows = conn.execute(sql, (min_confidence, limit)).fetchall()

        triples = []
        for row in rows:
            triples.append({
                'subject': row[0],
                'predicate': row[1],
                'object': row[2],
                'confidence': row[3] or 0.5,
                'similarity': 0.5,   # neutral — no query similarity computed
                'combined_score': 0.0,
                'rerank_score': 0.0,
                'entity_score': 0.0,
                'source_date': row[4] or 'N/A',
            })

        logger.info(
            f"📚 [BASELINE-KG] {len(triples)} high-confidence triples retrieved "
            f"(min_conf={min_confidence}, user_id={user_id or 'all'})"
        )
        return triples

    def get_session_knowledge_graph(self, session_id: str) -> Dict[str, Any]:
        """
        Ruft den Knowledge Graph für eine Session ab
        
        Args:
            session_id: Session-ID
            
        Returns:
            Knowledge Graph Daten
        """
        with self.get_connection() as conn:
            # Triples für die Session
            cursor = conn.execute("""
                SELECT subject, predicate, object, confidence, extraction_method, metadata, created_at
                FROM triples 
                WHERE session_id = ?
                ORDER BY confidence DESC, created_at DESC
            """, (session_id,))
            
            triples = []
            for row in cursor.fetchall():
                triple = dict(row)
                # Parse Metadata falls JSON
                try:
                    if triple['metadata']:
                        triple['metadata'] = json.loads(triple['metadata'])
                except (json.JSONDecodeError, TypeError):
                    triple['metadata'] = {}
                triples.append(triple)
            
            # Entitäten für die Session
            cursor = conn.execute("""
                SELECT entity_type, entity_value, confidence, extraction_method, metadata, created_at
                FROM knowledge_graph_entities
                WHERE session_id = ?
                ORDER BY confidence DESC, created_at DESC
            """, (session_id,))
            
            entities = []
            for row in cursor.fetchall():
                entity = dict(row)
                # Parse Metadata falls JSON
                try:
                    if entity['metadata']:
                        entity['metadata'] = json.loads(entity['metadata'])
                except (json.JSONDecodeError, TypeError):
                    entity['metadata'] = {}
                entities.append(entity)
        
        return {
            'session_id': session_id,
            'triples': triples,
            'entities': entities,
            'total_triples': len(triples),
            'total_entities': len(entities)
        }
    
    def save_context_summary(self, session_id: str, summary_type: str, 
                            content: str, interaction_count: int = 0) -> Optional[int]:
        """
        Speichert eine Kontext-Zusammenfassung für eine Session
        
        Args:
            session_id: Session-ID
            summary_type: Typ der Zusammenfassung (periodic, session_end, mood_analysis)
            content: Zusammenfassungstext
            interaction_count: Anzahl der zusammengefassten Interaktionen
            
        Returns:
            Summary-ID oder None bei Fehler
        """
        try:
            result_id: Optional[int] = None
            with self.get_connection() as conn:
                # 1. Für session_end: Update session_summary field in wellbeing_sessions
                if summary_type == 'session_end':
                    conn.execute("""
                        UPDATE wellbeing_sessions 
                        SET session_summary = ?,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE id = ?
                    """, (content, session_id))
                
                # 2. AUCH in context_summaries Tabelle speichern (für periodische und alle anderen)
                encrypted_content = self._encrypt_data(content) if hasattr(self, '_encrypt_data') else None
                
                cursor = conn.execute("""
                    INSERT INTO context_summaries 
                    (session_id, summary_type, content, content_encrypted, interaction_count, created_at)
                    VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """, (session_id, summary_type, content, encrypted_content, interaction_count))
                
                summary_id_raw = cursor.lastrowid
                conn.commit()
                
                # Ensure type safety
                result_id = int(summary_id_raw) if summary_id_raw is not None else None
                logger.info(f"✅ Context summary saved for session {session_id} (type: {summary_type}, id: {result_id})")
                return result_id
                
        except Exception as e:
            logger.error(f"❌ Failed to save context summary: {e}")
            return None

    def get_context_summaries(self, session_id: str, 
                              summary_type: Optional[str] = None,
                              limit: int = 10) -> List[Dict[str, Any]]:
        """
        Holt Kontext-Zusammenfassungen für eine Session
        
        Args:
            session_id: Session-ID
            summary_type: Optional - 'periodic', 'session_end', 'mood_analysis' oder None für alle
            limit: Maximale Anzahl der Ergebnisse
            
        Returns:
            Liste von Summary-Dictionaries
        """
        try:
            with self.get_connection() as conn:
                if summary_type:
                    cursor = conn.execute("""
                        SELECT id, session_id, summary_type, content, content_encrypted, 
                               created_at, interaction_count
                        FROM context_summaries
                        WHERE session_id = ? AND summary_type = ?
                        ORDER BY created_at DESC
                        LIMIT ?
                    """, (session_id, summary_type, limit))
                else:
                    cursor = conn.execute("""
                        SELECT id, session_id, summary_type, content, content_encrypted, 
                               created_at, interaction_count
                        FROM context_summaries
                        WHERE session_id = ?
                        ORDER BY created_at DESC
                        LIMIT ?
                    """, (session_id, limit))
                
                rows = cursor.fetchall()
                
                summaries = []
                for row in rows:
                    content = row['content']
                    # Versuche Entschlüsselung wenn nötig
                    if not content and row['content_encrypted']:
                        try:
                            content = self._decrypt_data(row['content_encrypted'])
                        except Exception as exc:
                            logger.debug(f"Could not decrypt context summary {row['id']}: {exc}")
                    
                    summaries.append({
                        'id': row['id'],
                        'session_id': row['session_id'],
                        'summary_type': row['summary_type'],
                        'content': content,
                        'created_at': row['created_at'],
                        'interaction_count': row['interaction_count']
                    })
                
                return summaries
                
        except Exception as e:
            logger.error(f"❌ Failed to get context summaries: {e}")
            return []

    def get_user_context_summaries(self, user_id: str, 
                                   summary_type: Optional[str] = None,
                                   days: int = 30,
                                   limit: int = 20) -> List[Dict[str, Any]]:
        """
        Holt Kontext-Zusammenfassungen für einen User über alle Sessions
        
        Args:
            user_id: User-ID
            summary_type: Optional - 'periodic', 'session_end' oder None für alle
            days: Zeitraum in Tagen
            limit: Maximale Anzahl der Ergebnisse
            
        Returns:
            Liste von Summary-Dictionaries mit Session-Infos
        """
        try:
            from datetime import datetime, timedelta, timezone
            cutoff_date = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
            
            with self.get_connection() as conn:
                query = """
                    SELECT cs.id, cs.session_id, cs.summary_type, cs.content, 
                           cs.content_encrypted, cs.created_at, cs.interaction_count,
                           ps.start_time as session_start
                    FROM context_summaries cs
                    JOIN wellbeing_sessions ps ON cs.session_id = ps.id
                    WHERE ps.user_id = ? AND cs.created_at >= ?
                """
                params: List[Any] = [user_id, cutoff_date]
                
                if summary_type:
                    query += " AND cs.summary_type = ?"
                    params.append(summary_type)
                
                query += " ORDER BY cs.created_at DESC LIMIT ?"
                params.append(limit)
                
                cursor = conn.execute(query, params)
                rows = cursor.fetchall()
                
                summaries = []
                for row in rows:
                    content = row['content']
                    if not content and row['content_encrypted']:
                        try:
                            content = self._decrypt_data(row['content_encrypted'])
                        except Exception as exc:
                            logger.debug(f"Could not decrypt user context summary {row['id']}: {exc}")
                    
                    summaries.append({
                        'id': row['id'],
                        'session_id': row['session_id'],
                        'summary_type': row['summary_type'],
                        'content': content,
                        'created_at': row['created_at'],
                        'interaction_count': row['interaction_count'],
                        'session_start': row['session_start']
                    })
                
                return summaries
                
        except Exception as e:
            logger.error(f"❌ Failed to get user context summaries: {e}")
            return []

    def close(self) -> None:
        """Schließt Datenbankverbindungen - mit neuem Thread-sicherem Modell"""
        # Schließe LLM-KG-Extractor falls verfügbar
        if hasattr(self, 'llm_kg_extractor') and self.llm_kg_extractor:
            try:
                # LLMKnowledgeGraphExtractor hat keine close() Methode - einfach auf None setzen
                self.llm_kg_extractor = None
            except Exception as exc:
                logger.debug(f"Failed to release llm_kg_extractor reference: {exc}")
        
        logger.info("🔒 WellbeingDatabase geschlossen")
    
    def _is_system_message(self, content: str) -> bool:
        """
        Prüft ob eine Nachricht eine System-/interne Nachricht ist
        (verhindert KG-Extraktion für System-Prompts)
        """
        if not content:
            return True
            
        content_lower = content.lower()
        system_indicators = [
            "du bist ein", "analysiere ob", "nachricht:", "aufgabe:",
            "system:", "assistant:", "user:", "kg-extraktion",
            "auto-summarization", "session-management"
        ]
        
        return any(indicator in content_lower for indicator in system_indicators)
    
    # ========================================
    # SESSION-MANAGEMENT DB-METHODEN
    # ========================================
    
    def get_user_sessions(self, user_id: str, status: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Holt alle Sessions eines Users
        
        Args:
            user_id: User-ID
            status: Optional - 'active', 'closed' oder None für alle
            
        Returns:
            Liste der Sessions
        """
        try:
            with self.get_connection() as conn:
                if status == 'active':
                    query = """
                        SELECT id, user_id, start_time, end_time, session_summary, 
                               mood_progression, care_goals, created_at, updated_at
                        FROM wellbeing_sessions 
                        WHERE user_id = ? AND end_time IS NULL
                        ORDER BY created_at DESC
                    """
                    params = (user_id,)
                elif status == 'closed':
                    query = """
                        SELECT id, user_id, start_time, end_time, session_summary, 
                               mood_progression, care_goals, created_at, updated_at
                        FROM wellbeing_sessions 
                        WHERE user_id = ? AND end_time IS NOT NULL
                        ORDER BY created_at DESC
                    """
                    params = (user_id,)
                else:
                    query = """
                        SELECT id, user_id, start_time, end_time, session_summary, 
                               mood_progression, care_goals, created_at, updated_at
                        FROM wellbeing_sessions 
                        WHERE user_id = ?
                        ORDER BY created_at DESC
                    """
                    params = (user_id,)
                cursor = conn.execute(query, params)
                sessions: List[Dict[str, Any]] = []
                for row in cursor.fetchall():
                    session = dict(row)
                    # Interaction Count hinzufügen
                    count_cursor = conn.execute(
                        "SELECT COUNT(*) FROM session_interactions WHERE session_id = ?",
                        (session['id'],),
                    )
                    session['interaction_count'] = count_cursor.fetchone()[0]
                    # Legacy-Entschlüsselung für Felder ohne Flag
                    if session.get('session_summary'):
                        session['session_summary'] = self._maybe_decrypt(session['session_summary'])
                    if session.get('care_goals'):
                        session['care_goals'] = self._maybe_decrypt(session['care_goals'])
                    sessions.append(session)
                return sessions
        except Exception as e:
            logger.error(f"❌ Fehler beim Laden der User-Sessions: {e}")
            return []

    def get_session_record(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Return one session record by id, including interaction count."""
        try:
            with self.get_connection() as conn:
                cursor = conn.execute(
                    """
                    SELECT id, user_id, start_time, end_time, session_summary,
                           mood_progression, care_goals, created_at, updated_at
                    FROM wellbeing_sessions
                    WHERE id = ?
                    LIMIT 1
                    """,
                    (session_id,),
                )
                row = cursor.fetchone()
                if row is None:
                    return None

                session = dict(row)
                count_cursor = conn.execute(
                    "SELECT COUNT(*) FROM session_interactions WHERE session_id = ?",
                    (session_id,),
                )
                session['interaction_count'] = count_cursor.fetchone()[0]
                if session.get('session_summary'):
                    session['session_summary'] = self._maybe_decrypt(session['session_summary'])
                if session.get('care_goals'):
                    session['care_goals'] = self._maybe_decrypt(session['care_goals'])
                return session
        except Exception as e:
            logger.error(f"❌ Fehler beim Laden der Session {session_id}: {e}")
            return None

    def get_user_insights(
        self,
        user_id: str,
        session_id: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Return normalized psychological insights for exactly one canonical user."""
        try:
            with self.get_connection() as conn:
                query = """
                    SELECT
                        pi.id AS insight_id,
                        pi.user_id,
                        pi.session_id,
                        pi.insight_type,
                        pi.category,
                        pi.value,
                        pi.confidence,
                        pi.temporal_context,
                        pi.created_at,
                        pi.validated_at,
                        pi.encrypted_data,
                        pi.correction_status,
                        pi.corrected_at,
                        pi.corrected_by,
                        pi.correction_reason,
                        ps.start_time AS session_start_time,
                        ps.end_time AS session_end_time,
                        ps.session_summary
                    FROM wellbeing_insights pi
                    INNER JOIN wellbeing_sessions ps
                        ON ps.id = pi.session_id
                    WHERE pi.user_id = ?
                      AND ps.user_id = ?
                      AND COALESCE(pi.correction_status, 'active') = 'active'
                """
                params: List[Any] = [user_id, user_id]

                if session_id:
                    query += " AND pi.session_id = ?"
                    params.append(session_id)

                query += " ORDER BY pi.created_at DESC, pi.confidence DESC"

                if limit is not None:
                    query += " LIMIT ?"
                    params.append(limit)

                cursor = conn.execute(query, tuple(params))
                insights: List[Dict[str, Any]] = []

                for row in cursor.fetchall():
                    payload: Dict[str, Any] = {}
                    encrypted_data = row['encrypted_data']
                    if encrypted_data:
                        decrypted_data = self._decrypt_data(encrypted_data)
                        if decrypted_data and isinstance(decrypted_data, str) and decrypted_data.strip().startswith('{'):
                            try:
                                payload = json.loads(decrypted_data)
                            except json.JSONDecodeError as exc:
                                logger.warning(
                                    "⚠️ Ungültige Insight-Payload für insight_id=%s: %s",
                                    row['insight_id'],
                                    exc,
                                )

                    description = (
                        payload.get('description')
                        or payload.get('content')
                        or row['value']
                        or ''
                    )
                    evidence = payload.get('evidence') or []
                    if not isinstance(evidence, list):
                        evidence = []

                    insights.append({
                        'insight_id': row['insight_id'],
                        'user_id': row['user_id'],
                        'session_id': row['session_id'],
                        'insight_type': row['insight_type'],
                        'category': row['category'],
                        'value': row['value'],
                        'description': description,
                        'confidence': row['confidence'],
                        'temporal_context': row['temporal_context'],
                        'created_at': row['created_at'],
                        'validated_at': row['validated_at'],
                        'evidence': evidence,
                        'session_start_time': row['session_start_time'],
                        'session_end_time': row['session_end_time'],
                        'session_summary': self._maybe_decrypt(row['session_summary']),
                    })

                return insights
        except Exception as e:
            logger.error(f"❌ Fehler beim Laden der User-Insights: {e}")
            raise

    def correct_user_insight(
        self,
        insight_id: int,
        user_id: str,
        new_status: str,
        *,
        corrected_by: str = "user",
        reason: Optional[str] = None,
        replacement_insight_id: Optional[int] = None,
    ) -> bool:
        """Apply an ownership-checked correction and append an immutable audit row."""
        valid_statuses = {"active", "rejected", "superseded"}
        valid_actors = {"user", "system", "therapist"}
        if new_status not in valid_statuses:
            raise ValueError(f"Unsupported correction status: {new_status}")
        if corrected_by not in valid_actors:
            raise ValueError(f"Unsupported correction actor: {corrected_by}")
        if new_status == "active" and corrected_by == "system":
            raise ValueError("System extraction cannot reactivate a corrected insight")
        if new_status == "superseded" and replacement_insight_id is None:
            raise ValueError("Superseded insights require a replacement_insight_id")
        if replacement_insight_id == insight_id:
            raise ValueError("An insight cannot supersede itself")

        now = datetime.now(timezone.utc).isoformat()
        with self.get_connection() as conn:
            row = conn.execute(
                "SELECT user_id, correction_status FROM wellbeing_insights WHERE id = ?",
                (insight_id,),
            ).fetchone()
            if row is None or str(row[0]) != user_id:
                return False

            previous_status = str(row[1] or "active")
            if new_status == "active" and previous_status == "superseded":
                raise ValueError("Superseded insights cannot be reactivated")
            if replacement_insight_id is not None:
                replacement = conn.execute(
                    """
                    SELECT user_id, correction_status
                    FROM wellbeing_insights
                    WHERE id = ?
                    """,
                    (replacement_insight_id,),
                ).fetchone()
                if (
                    replacement is None
                    or str(replacement[0]) != user_id
                    or str(replacement[1] or "active") != "active"
                ):
                    return False

            if previous_status == new_status:
                return True

            encrypted_reason = self._encrypt_sensitive_data(reason) if reason else None

            conn.execute(
                """
                UPDATE wellbeing_insights
                SET correction_status = ?, corrected_at = ?, corrected_by = ?,
                    correction_reason = ?
                WHERE id = ? AND user_id = ?
                """,
                (new_status, now, corrected_by, encrypted_reason, insight_id, user_id),
            )
            conn.execute(
                """
                INSERT INTO wellbeing_insight_corrections (
                    insight_id, user_id, previous_status, new_status,
                    corrected_by, reason, replacement_insight_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    insight_id,
                    user_id,
                    previous_status,
                    new_status,
                    corrected_by,
                    encrypted_reason,
                    replacement_insight_id,
                    now,
                ),
            )
            conn.commit()
        return True
    
    def set_model_loader(self, model_loader: Any) -> None:
        """
        Setzt ModelLoader nach Initialisierung
        
        Args:
            model_loader: ModelLoader-Instanz aus dem Chat-System
        """
        try:
            # Re-initialisiere LLM-KG-Extraktor mit neuem ModelLoader
            if KG_EXTRACTION_AVAILABLE and LLMKnowledgeGraphExtractor is not None and model_loader:
                self.llm_kg_extractor = LLMKnowledgeGraphExtractor(llm_client=model_loader)
                logger.info("✅ LLM-KG-Extraktor neu initialisiert mit Chat-ModelLoader")
            else:
                logger.warning("⚠️ Kann LLM-KG-Extraktor nicht initialisieren")
        except Exception as e:
            logger.error(f"❌ Fehler beim Setzen des ModelLoaders: {e}")

    # ──────────────────────────────────────────────────────────────────
    #  ★ SOTA v4: Entity Embedding Index (lazy-loaded, cached)
    # ──────────────────────────────────────────────────────────────────
    def _ensure_entity_index(self) -> bool:
        """Lazy-load entity embeddings from kg_entities into a numpy matrix.
        
        Returns True if index is available and non-empty.
        Thread-safe via _entity_index_lock.
        """
        if self._entity_index_built:
            return self._entity_embeddings is not None and len(self._entity_texts or []) > 0

        with self._entity_index_lock:
            if self._entity_index_built:
                return self._entity_embeddings is not None and len(self._entity_texts or []) > 0

            try:
                import numpy as np
                with self.get_connection() as conn:
                    rows = conn.execute(
                        "SELECT entity_text, embedding FROM kg_entities "
                        "WHERE embedding IS NOT NULL"
                    ).fetchall()

                if not rows:
                    logger.info("[KG-EntityIndex] No entity embeddings found — index empty")
                    self._entity_texts = []
                    self._entity_embeddings = None
                    self._entity_index_built = True
                    return False

                texts = []
                embs = []
                for row in rows:
                    entity_text = row[0] if isinstance(row, (list, tuple)) else row['entity_text']
                    emb_blob = row[1] if isinstance(row, (list, tuple)) else row['embedding']
                    if emb_blob:
                        emb = np.frombuffer(emb_blob, dtype=np.float32).copy()
                        texts.append(entity_text)
                        embs.append(emb)

                if texts:
                    self._entity_texts = texts
                    self._entity_embeddings = np.stack(embs, axis=0)
                    # L2-normalize for cosine similarity via dot product
                    norms = np.linalg.norm(self._entity_embeddings, axis=1, keepdims=True)
                    norms = np.maximum(norms, 1e-10)
                    self._entity_embeddings = self._entity_embeddings / norms
                    logger.info(
                        f"[KG-EntityIndex] Loaded {len(texts)} entity embeddings "
                        f"({self._entity_embeddings.shape})"
                    )
                else:
                    self._entity_texts = []
                    self._entity_embeddings = None

                self._entity_index_built = True
                return self._entity_embeddings is not None

            except Exception as e:
                logger.warning(f"[KG-EntityIndex] Build failed: {e}")
                self._entity_texts = []
                self._entity_embeddings = None
                self._entity_index_built = True
                return False

    def invalidate_entity_index(self) -> None:
        """Call after inserting new entity embeddings to force re-load on next search."""
        with self._entity_index_lock:
            self._entity_index_built = False
            self._entity_texts = None
            self._entity_embeddings = None
            self._entity_index_version += 1  # ← Increment to invalidate any stale references

    def _semantic_entity_match(self, query: str, top_k: int = 15) -> List[Tuple[str, float]]:
        """Embed query → cosine similarity against all entity embeddings → top-k entities.

        Returns list of (entity_text, similarity_score) sorted desc.
        Raises RuntimeError if index invalidated during operation (explicit diagnostics).
        """
        # Capture version BEFORE and AFTER ensure to detect race-condition invalidation
        version_before = self._entity_index_version
        if not self._ensure_entity_index():
            return []
        version_after = self._entity_index_version

        # Race-condition detection: if index was invalidated by another thread during ensure
        if version_after != version_before:
            raise RuntimeError(
                "[KG-SemanticMatch] Entity index was invalidated during operation — "
                "retry search (this is a transient race condition, not a persistent error)"
            )

        try:
            import numpy as np
            from utils.embedding_singleton import get_embedding_model

            embedding_model = get_embedding_model()
            if not embedding_model.is_loaded():
                if not embedding_model.load_model():
                    return []

            query_emb = embedding_model.encode([query])[0]
            query_emb = np.array(query_emb, dtype=np.float32)
            query_norm = np.linalg.norm(query_emb)
            if query_norm < 1e-10:
                return []
            query_emb = query_emb / query_norm

            # ★ SOTA: Pre-matmul version check detects concurrent invalidation (race-condition guard)
            if self._entity_index_version != version_after:
                raise RuntimeError(
                    "[KG-SemanticMatch] Entity index was invalidated during embedding — "
                    "retry search (race condition detected)"
                )

            # Cosine similarity = dot product (both L2-normalized)
            similarities = self._entity_embeddings @ query_emb  # (N,)

            # Top-k via partial sort
            if len(similarities) <= top_k:
                top_indices = np.argsort(similarities)[::-1]
            else:
                top_indices = np.argpartition(similarities, -top_k)[-top_k:]
                top_indices = top_indices[np.argsort(similarities[top_indices])[::-1]]

            # Bind to local — Pyright can't narrow Optional through mutable self attrs
            entity_texts = self._entity_texts
            if entity_texts is None:
                raise RuntimeError(
                    "[KG-SemanticMatch] Entity texts became None after version check — "
                    "retry search (unexpected invalidation)"
                )

            results = []
            for idx in top_indices:
                score = float(similarities[idx])
                if score > 0.3:  # Minimum semantic similarity
                    results.append((entity_texts[idx], score))

            return results

        except Exception as e:
            logger.error(f"[KG-SemanticMatch] Entity matching failed (fail-fast): {e}")
            raise RuntimeError(f"KG semantic entity match failed: {e}") from e

    def _entity_to_triples_2hop(
        self,
        matched_entities: List[Tuple[str, float]],
        user_id: Optional[str] = None,
        session_id: Optional[str] = None,
        min_confidence: float = 0.5,
        max_entities_hop1: int = 10,
        max_entities_hop2: int = 5,
        max_triples_per_entity: int = 20,
    ) -> List[Dict[str, Any]]:
        """★ SOTA v4: 2-hop entity → triple retrieval across ALL sessions.

        Pipeline:
          1-hop: matched entities → triples containing them (subject/object match)
          2-hop: NEW entities from 1-hop results → their triples (bounded)
          
        Returns candidate triples annotated with entity_score and hop number.
        """
        # 🔒 ISOLATION: user_id is REQUIRED — the SQL session_filter would
        # otherwise be empty and the JOIN would span every user's triples.
        if not user_id:
            raise ValueError(
                "_entity_to_triples_2hop requires a non-empty user_id "
                "to enforce per-user data isolation."
            )

        if not matched_entities:
            return []

        candidate_triples: List[Dict[str, Any]] = []
        seen_triple_hashes: set = set()

        try:
            with self.get_connection() as conn:
                # Build session filter SQL
                session_filter = ""
                filter_params: List[Any] = []
                if user_id:
                    session_filter += (
                        " AND t.session_id IN "
                        "(SELECT id FROM wellbeing_sessions WHERE user_id = ?)"
                    )
                    filter_params.append(user_id)
                if session_id:
                    session_filter += " AND t.session_id = ?"
                    filter_params.append(session_id)

                # ── 1-hop: entity → triples ──────────────────────────
                hop1_entity_texts = set()
                hop1_new_entities = set()

                for entity_text, entity_score in matched_entities[:max_entities_hop1]:
                    hop1_entity_texts.add(entity_text)
                    rows = conn.execute(
                        f"""
                        SELECT t.subject, t.predicate, t.object, t.confidence,
                               t.triple_hash, t.session_id, t.interaction_id,
                               t.extraction_method, t.metadata, t.created_at
                        FROM triples t
                        WHERE (t.subject = ? OR t.object = ?)
                          AND t.confidence >= ?
                          {session_filter}
                        ORDER BY t.confidence DESC
                        LIMIT ?
                        """,
                        [entity_text, entity_text, min_confidence]
                        + filter_params
                        + [max_triples_per_entity],
                    ).fetchall()

                    for row in rows:
                        r = dict(row) if hasattr(row, 'keys') else {
                            'subject': row[0], 'predicate': row[1], 'object': row[2],
                            'confidence': row[3], 'triple_hash': row[4],
                            'session_id': row[5], 'interaction_id': row[6],
                            'extraction_method': row[7], 'metadata': row[8],
                            'created_at': row[9],
                        }
                        th = r.get('triple_hash') or f"{r['subject']}_{r['predicate']}_{r['object']}"
                        if th in seen_triple_hashes:
                            continue
                        seen_triple_hashes.add(th)

                        r['entity_score'] = entity_score
                        r['hop'] = 1
                        candidate_triples.append(r)

                        # Collect new entities for 2-hop expansion
                        if r['subject'] and r['subject'] not in hop1_entity_texts:
                            hop1_new_entities.add(r['subject'])
                        if r['object'] and r['object'] not in hop1_entity_texts:
                            hop1_new_entities.add(r['object'])

                # ── 2-hop: new entities from 1-hop → their triples ──
                hop2_candidates = sorted(
                    [e for e in hop1_new_entities if e and len(e.strip()) >= 3],
                    key=lambda e: len(e),  # shorter = more likely named entity
                )

                for expansion_entity in hop2_candidates[:max_entities_hop2]:
                    rows = conn.execute(
                        f"""
                        SELECT t.subject, t.predicate, t.object, t.confidence,
                               t.triple_hash, t.session_id, t.interaction_id,
                               t.extraction_method, t.metadata, t.created_at
                        FROM triples t
                        WHERE (t.subject = ? OR t.object = ?)
                          AND t.confidence >= ?
                          {session_filter}
                        ORDER BY t.confidence DESC
                        LIMIT 10
                        """,
                        [expansion_entity, expansion_entity, min_confidence]
                        + filter_params,
                    ).fetchall()

                    for row in rows:
                        r = dict(row) if hasattr(row, 'keys') else {
                            'subject': row[0], 'predicate': row[1], 'object': row[2],
                            'confidence': row[3], 'triple_hash': row[4],
                            'session_id': row[5], 'interaction_id': row[6],
                            'extraction_method': row[7], 'metadata': row[8],
                            'created_at': row[9],
                        }
                        th = r.get('triple_hash') or f"{r['subject']}_{r['predicate']}_{r['object']}"
                        if th in seen_triple_hashes:
                            continue
                        seen_triple_hashes.add(th)

                        r['entity_score'] = 0.3  # lower score for indirect 2-hop
                        r['hop'] = 2
                        candidate_triples.append(r)

                hop1_count = sum(1 for c in candidate_triples if c.get('hop') == 1)
                hop2_count = sum(1 for c in candidate_triples if c.get('hop') == 2)
                logger.debug(
                    f"[KG-2Hop] {len(matched_entities)} entities → "
                    f"{hop1_count} 1-hop + {hop2_count} 2-hop = "
                    f"{len(candidate_triples)} candidates"
                )

        except Exception as e:
            logger.warning(f"[KG-2Hop] Triple retrieval failed: {e}")

        return candidate_triples

    def search_knowledge_graph_semantic(
        self,
        query: str,
        user_id: Optional[str] = None,
        session_id: Optional[str] = None,
        limit: int = 10,
        min_confidence: float = 0.5,
        similarity_threshold: float = 0.50,
    ) -> List[Dict[str, Any]]:
        """* SOTA v4: Hybrid KG search - entity-embeddings + FAISS + 2-hop + cross-encoder.

        Pipeline:
          1. Entity-embedding semantic match -> entity->triple lookup (1-hop + 2-hop)
          2. FAISS triple-text search (parallel signal, high recall)
          3. Merge & deduplicate candidates by triple_hash
          4. Cross-encoder reranking (BGE-reranker-v2-m3)
          5. Combined score = 0.4 x entity_score + 0.6 x rerank_score
          6. Return top-k sorted by combined_score descending

        Args:
            query: Semantische Suchanfrage (z.B. "Angst vor Chef")
            user_id: Optional - filtert auf Triples dieses Users
            session_id: Optional - filtert auf Triples dieser Session
            limit: Maximale Anzahl Ergebnisse
            min_confidence: Minimale KG-Confidence (0.0-1.0)
            similarity_threshold: Minimale Similarity für FAISS-Ergebnisse (0.0-1.0)

        Returns:
            Liste von KG-Triple-Dicts sortiert nach combined/similarity Score
        """
        import numpy as np

        # 🔒 ISOLATION: user_id is REQUIRED. Without it both the 2-hop SQL filter
        # and the FAISS post-filter degrade silently to "all users".
        if not user_id:
            raise ValueError(
                "search_knowledge_graph_semantic requires a non-empty user_id "
                "to enforce per-user data isolation."
            )

        all_candidates: Dict[str, Dict[str, Any]] = {}  # triple_hash → candidate

        # ──────────────────────────────────────────────────────────────
        #  SIGNAL 1: Entity-embedding match → 2-hop triple retrieval
        # ──────────────────────────────────────────────────────────────
        entity_candidates_count = 0
        try:
            matched_entities = self._semantic_entity_match(query, top_k=15)
            if matched_entities:
                entity_triples = self._entity_to_triples_2hop(
                    matched_entities,
                    user_id=user_id,
                    session_id=session_id,
                    min_confidence=min_confidence,
                )
                for triple in entity_triples:
                    th = triple.get('triple_hash') or (
                        f"{triple['subject']}_{triple['predicate']}_{triple['object']}"
                    )
                    if th not in all_candidates:
                        triple['_source'] = 'entity'
                        all_candidates[th] = triple
                        entity_candidates_count += 1
                    else:
                        # Keep higher entity_score if duplicate
                        existing_score = all_candidates[th].get('entity_score', 0)
                        if triple.get('entity_score', 0) > existing_score:
                            all_candidates[th]['entity_score'] = triple['entity_score']

                logger.debug(
                    f"[SOTA-Search] Signal 1 (Entity): {len(matched_entities)} entities → "
                    f"{entity_candidates_count} unique triples"
                )
        except Exception as e:
            logger.warning(f"[SOTA-Search] Entity signal failed: {e}")

        # ──────────────────────────────────────────────────────────────
        #  SIGNAL 2: FAISS triple-text search (existing manager)
        # ──────────────────────────────────────────────────────────────
        faiss_candidates_count = 0
        try:
            self._ensure_kg_faiss_manager()
            if hasattr(self, 'kg_faiss_manager') and self.kg_faiss_manager:
                faiss_results = self.kg_faiss_manager.search(
                    query=query,
                    user_id=user_id,
                    session_id=session_id,
                    top_k=max(limit * 3, 30),  # over-retrieve for reranking pool
                    min_similarity=max(similarity_threshold - 0.15, 0.20),
                )
                for r in (faiss_results or []):
                    th = r.get('triple_hash') or (
                        f"{r['subject']}_{r['predicate']}_{r['object']}"
                    )
                    if th not in all_candidates:
                        r['_source'] = 'faiss'
                        r['entity_score'] = 0.0
                        all_candidates[th] = r
                        faiss_candidates_count += 1
                    else:
                        # Merge FAISS similarity into existing candidate
                        existing = all_candidates[th]
                        if r.get('similarity', 0) > existing.get('similarity', 0):
                            existing['similarity'] = r['similarity']

                logger.debug(
                    f"[SOTA-Search] Signal 2 (FAISS): {faiss_candidates_count} new "
                    f"+ {len(faiss_results or []) - faiss_candidates_count} merged"
                )
        except Exception as e:
            logger.warning(f"[SOTA-Search] FAISS signal failed: {e}")

        # ──────────────────────────────────────────────────────────────
        #  No candidates from either signal → SQL LIKE fallback
        # ──────────────────────────────────────────────────────────────
        if not all_candidates:
            logger.info(
                f"[SOTA-Search] No candidates from entity/FAISS — "
                f"falling back to LIKE search"
            )
            return self.search_knowledge_graph(
                query, user_id, session_id, limit, min_confidence
            )

        candidates = list(all_candidates.values())
        logger.info(
            f"[SOTA-Search] {len(candidates)} candidates "
            f"(entity={entity_candidates_count}, faiss={faiss_candidates_count})"
        )

        # ──────────────────────────────────────────────────────────────
        #  STAGE 3: Cross-encoder reranking
        # ──────────────────────────────────────────────────────────────
        reranked = False
        try:
            from agent.reranker import get_reranker
            reranker = get_reranker()

            if reranker and reranker.is_available:
                passages = [
                    {
                        "text": f"{c['subject']} {c['predicate']} {c['object']}",
                        "_idx": i,
                    }
                    for i, c in enumerate(candidates)
                ]

                reranked_passages = reranker.rerank(
                    query=query,
                    passages=passages,
                    top_k=min(len(passages), max(limit * 2, 20)),
                    text_key="text",
                )

                # Map rerank_score back to candidates
                for rp in reranked_passages:
                    idx = rp.get("_idx")
                    if idx is not None and 0 <= idx < len(candidates):
                        candidates[idx]["rerank_score"] = rp.get("rerank_score", 0.0)

                reranked = True
                logger.debug(
                    f"[SOTA-Search] Cross-encoder reranked {len(reranked_passages)} passages"
                )
            else:
                logger.debug("[SOTA-Search] Reranker not available — using raw scores")

        except ImportError:
            logger.debug("[SOTA-Search] Reranker module not available")
        except Exception as e:
            logger.warning(f"[SOTA-Search] Reranking failed: {e}")

        # ──────────────────────────────────────────────────────────────
        #  STAGE 4: Combined scoring & final ranking
        # ──────────────────────────────────────────────────────────────
        for c in candidates:
            entity_score = c.get('entity_score', 0.0)
            faiss_sim = c.get('similarity', 0.0)

            if reranked:
                rerank_score = c.get('rerank_score', 0.0)
                # Combined: entity provenance + cross-encoder relevance
                # Weight reranker higher since it sees query-passage jointly
                combined = 0.4 * max(entity_score, faiss_sim) + 0.6 * rerank_score
            else:
                # Fallback: best raw score
                combined = max(entity_score, faiss_sim)

            c['combined_score'] = combined
            # Set 'similarity' to combined for backward compatibility
            c['similarity'] = combined

        # Sort by combined score descending
        candidates.sort(key=lambda c: c.get('combined_score', 0.0), reverse=True)

        # ──────────────────────────────────────────────────────────────
        #  STAGE 5: Return top-k with normalized output format
        # ──────────────────────────────────────────────────────────────
        results = []
        for c in candidates[:limit]:
            results.append({
                'subject': c.get('subject', ''),
                'predicate': c.get('predicate', ''),
                'object': c.get('object', ''),
                'confidence': c.get('confidence', 0.0),
                'similarity': c.get('similarity', 0.0),
                'session_id': c.get('session_id', ''),
                'interaction_id': c.get('interaction_id', ''),
                'extraction_method': c.get('extraction_method', ''),
                'metadata': c.get('metadata', ''),
                'created_at': c.get('created_at', ''),
                # Diagnostic fields (not used by callers, but useful for debugging)
                'entity_score': c.get('entity_score', 0.0),
                'rerank_score': c.get('rerank_score', 0.0),
                'combined_score': c.get('combined_score', 0.0),
                'hop': c.get('hop', 0),
                '_source': c.get('_source', ''),
            })

        sources: Dict[str, int] = {}
        for r in results:
            src = r.get('_source', 'unknown')
            sources[src] = sources.get(src, 0) + 1
        logger.info(
            f"[SOTA-Search] Returning {len(results)}/{len(candidates)} results "
            f"(sources: {sources}, reranked={reranked})"
        )

        return results
