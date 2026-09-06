"""
Dedizierter FAISS Manager für Psycho-Tab KG-Triples
====================================================

Separate Vector Search für psychologische Knowledge Graph Triples.
Komplett getrennt vom Normal-Chat RAG System!

Features:
- HNSW Index für schnelle ANN (Approximate Nearest Neighbor)
- 100% Coverage aller Triples
- Auto-Caching & Persistence
- Sub-Second Latenz auch bei 100k+ Triples
- User-Filtering
- Staleness Detection (Auto-Rebuild bei DB-Änderungen)

Author: AI Assistant
Date: 2025-11-16
Version: 1.0
"""

import os
import multiprocessing

# 🚀 CRITICAL: Setze OMP_NUM_THREADS VOR dem FAISS-Import!
_n_cores = multiprocessing.cpu_count()
os.environ['OMP_NUM_THREADS'] = str(_n_cores)
os.environ['OPENBLAS_NUM_THREADS'] = str(_n_cores)
os.environ['MKL_NUM_THREADS'] = str(_n_cores)

import sqlite3
import threading
import numpy as np
import pickle
import logging
import hashlib
import time
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple, TypedDict
from datetime import datetime

# ✅ Import embedding singleton EARLY to avoid circular imports
from utils.embedding_singleton import get_embedding_model

logger = logging.getLogger(__name__)

faiss: Any = None


class KGFAISSStats(TypedDict):
    """Type-safe statistics for KG FAISS manager."""
    total_triples: int
    avg_search_ms: float
    total_searches: int
    cache_hits: int
    last_rebuild: Optional[str]

def _ensure_faiss_runtime() -> bool:
    """Lazy-load FAISS and apply OpenMP settings once at runtime."""
    global faiss
    if faiss is not None:
        return True
    try:
        import faiss as _faiss
        faiss = _faiss
        try:
            faiss.omp_set_num_threads(_n_cores)
            logger.info(
                f"🚀 KG-FAISS OpenMP: {_n_cores} Threads aktiviert "
                f"(OMP_NUM_THREADS={os.environ.get('OMP_NUM_THREADS')})"
            )
        except Exception as e:
            logger.warning(f"⚠️ KG-FAISS OpenMP konnte nicht konfiguriert werden: {e}")
        return True
    except ImportError as e:
        logger.error(f"❌ KG-FAISS Import fehlgeschlagen: {e}")
        return False


def _get_faiss() -> Any:
    """Return the faiss module, raising RuntimeError if not available."""
    if faiss is None:
        if not _ensure_faiss_runtime():
            raise RuntimeError("FAISS ist nicht verfügbar")
    return faiss


class WellbeingKGFAISSManager:
    """
    FAISS Index Manager für psychologische KG-Triples.
    
    Unterschiede zu Normal-Chat FAISS:
    - Speichert nur KG-Triples (nicht RAG-Chunks)
    - Separater Cache-Pfad (wellbeing_kg_cache/)
    - Einfachere Struktur (nur 1 Index, kein Dual-Index)
    - Optimiert für < 100k Triples
    - User-spezifische Filterung
    
    Performance:
    - Build-Zeit: ~2-5 Min für 4000 Triples
    - Search-Zeit: ~50-200ms (5-10x schneller als SQL)
    - Memory: ~20-30MB für 4000 Triples
    - Disk: ~10-20MB Cache
    """
    
    def __init__(
        self, 
        db_path: str, 
        embedding_dim: int = 1024,
        auto_load: bool = False,  # ⚠️ Changed from True to False to prevent circular init
        min_confidence: float = 0.5,
        db_instance: Any = None  # ⚠️ NEW: Accept existing DB instance to prevent circular init
    ) -> None:
        """
        Initialisiert Psycho KG FAISS Manager.
        
        Args:
            db_path: Pfad zur wellbeing_store.db
            embedding_dim: Dimensionalität der Embeddings (1024 für bge-large)
            auto_load: Automatisch Index laden/bauen
            min_confidence: Minimale Confidence für indexierte Triples
            db_instance: Optional existing WellbeingDatabase instance (prevents circular init)
        """
        if not _ensure_faiss_runtime():
            raise RuntimeError("FAISS ist nicht verfügbar")

        self.db_path = db_path
        self.embedding_dim = embedding_dim
        self.min_confidence = min_confidence
        self._db_instance = db_instance  # ⚠️ Store reference to avoid creating new instances
        
        # FAISS Index (HNSW für schnelle ANN-Suche)
        self.index: Any = None  # faiss.Index stubs are incomplete; use Any
        
        # ID Mapping: FAISS-Index-Position → Triple-ID in DB
        self.id_map: List[int] = []
        
        # Cache Directory (separat vom Normal-Chat!)
        self.cache_dir = Path(db_path).parent / "wellbeing_kg_cache"
        self.cache_dir.mkdir(exist_ok=True, parents=True)
        
        # Metadata für Staleness-Detection
        self.metadata_path = self.cache_dir / "metadata.pkl"
        
        # Performance Statistics
        self.stats: KGFAISSStats = {
            'total_triples': 0,
            'avg_search_ms': 0.0,
            'total_searches': 0,
            'cache_hits': 0,
            'last_rebuild': None
        }

        # Reentrant lock so build_index()/save_index() and incremental add
        # can be safely interleaved with search() under Streamlit threading.
        self._index_lock = threading.RLock()

        logger.info(f"✅ WellbeingKGFAISSManager initialized (dim={embedding_dim}, cache={self.cache_dir})")
        
        # Auto-Load/Build
        if auto_load:
            self._auto_load_or_build()
    
    def _auto_load_or_build(self) -> None:
        """Automatisches Laden oder Bauen des Index."""
        if self._index_cached() and not self._is_stale():
            # Cached und aktuell → Laden
            logger.info("📂 Loading cached Psycho KG FAISS index...")
            if self.load_index():
                logger.info(f"✅ Index loaded ({len(self.id_map):,} triples)")
                return
        
        # Nicht cached oder veraltet → Bauen
        logger.info("🔨 Building fresh Psycho KG FAISS index...")
        self.build_index()
    
    def _index_cached(self) -> bool:
        """Prüft ob Cached Index existiert."""
        index_path = self.cache_dir / "kg_index.faiss"
        mapping_path = self.cache_dir / "kg_id_map.pkl"
        return index_path.exists() and mapping_path.exists() and self.metadata_path.exists()
    
    def _is_stale(self) -> bool:
        """
        Prüft ob Cached Index veraltet ist.
        
        Staleness-Kriterien:
        1. DB-Hash hat sich geändert
        2. Anzahl Triples hat sich stark geändert (>10%)
        
        Returns:
            True wenn veraltet, False wenn aktuell
        """
        if not self.metadata_path.exists():
            return True
        
        try:
            # Lade gespeicherte Metadata
            with open(self.metadata_path, 'rb') as f:
                metadata = pickle.load(f)
            
            # Berechne aktuellen DB-Hash
            current_hash = self._get_db_hash()
            cached_hash = metadata.get('db_hash')
            
            if current_hash != cached_hash:
                logger.info(f"⚠️ DB changed: {cached_hash[:8] if cached_hash else 'None'}... → {current_hash[:8]}...")
                return True
            
            # Prüfe Anzahl Triples (direkt via SQL, KEINE DB-Instanz!)
            # ⚠️ AVOID WellbeingDatabase() to prevent circular init!
            conn = sqlite3.connect(self.db_path)
            cursor = conn.execute("SELECT COUNT(*) FROM triples WHERE confidence >= ?", (self.min_confidence,))
            current_count = cursor.fetchone()[0]
            conn.close()
            
            cached_count = metadata.get('triple_count', 0)
            
            # Wenn >10% Änderung, rebuild
            if abs(current_count - cached_count) / max(cached_count, 1) > 0.10:
                logger.info(f"⚠️ Triple count changed significantly: {cached_count} → {current_count}")
                return True
            
            return False
            
        except Exception as e:
            logger.warning(f"⚠️ Staleness check failed: {e}")
            return True
    
    def _get_db_hash(self) -> str:
        """Berechnet Hash der Triples-Tabelle für Staleness-Detection."""
        try:
            # ⚠️ Direkte DB-Verbindung, KEINE WellbeingDatabase() Instanz!
            conn = sqlite3.connect(self.db_path)
            cursor = conn.execute("""
                SELECT COUNT(*), MAX(created_at) 
                FROM triples 
                WHERE confidence >= ?
            """, (self.min_confidence,))
            row = cursor.fetchone()
            count, max_date = row[0], row[1]
            conn.close()
            
            hash_input = f"{count}:{max_date}"
            return hashlib.md5(hash_input.encode()).hexdigest()
            
        except Exception as e:
            logger.warning(f"⚠️ DB hash failed: {e}")
            return "unknown"
    
    def build_index(self, force_rebuild: bool = False) -> bool:
        """
        Baut FAISS Index aus allen KG-Triples.
        
        Args:
            force_rebuild: Rebuild auch wenn Cache existiert
            
        Returns:
            True wenn erfolgreich
        """
        start_time = time.time()
        
        try:
            # ⚠️ Use stored DB instance if available (prevents circular init)
            if self._db_instance is not None:
                db = self._db_instance
            else:
                # Fallback: create new instance (only when called standalone)
                from wellbeing.wellbeing_db import WellbeingDatabase
                db = WellbeingDatabase()
            
            embedding_model = get_embedding_model()  # Already imported at top
            
            # Stelle sicher dass Model geladen ist
            if not embedding_model.is_loaded():
                logger.info("📥 Loading embedding model...")
                if not embedding_model.load_model():
                    logger.error("❌ Failed to load embedding model")
                    return False
            
            # Lade alle Triples
            logger.info(f"📊 Loading triples (min_confidence={self.min_confidence})...")
            with db.get_connection() as conn:
                cursor = conn.execute("""
                    SELECT id, subject, predicate, object, confidence
                    FROM triples
                    WHERE confidence >= ?
                    ORDER BY confidence DESC, created_at DESC
                """, (self.min_confidence,))
                triples = cursor.fetchall()
            
            if not triples:
                logger.warning("⚠️ No triples found for indexing")
                return False
            
            logger.info(f"📊 Indexing {len(triples):,} triples...")
            
            # Erstelle Triple-Texte für Embedding
            triple_texts = [
                f"{row[1]} {row[2]} {row[3]}"  # subject predicate object
                for row in triples
            ]
            
            # Batch-Encoding (mit Progress Bar nur wenn viele Triples)
            logger.info("🔄 Encoding triples...")
            embeddings = embedding_model.encode(
                triple_texts, 
                batch_size=32, 
                show_progress_bar=len(triple_texts) > 1000  # Nur bei vielen Triples Progress anzeigen
            )
            embeddings = np.array(embeddings, dtype=np.float32)
            
            # Normalisiere für Cosine Similarity
            _get_faiss().normalize_L2(embeddings)

            # Erstelle HNSW Index (optimal für < 1M Vektoren)
            # M=48: Optimal für 10k-100k Vektoren (bessere Navigation)
            # efConstruction=500: Höhere Build-Quality für besseren Recall bei 112k Vektoren
            # efSearch=80: Bessere Search-Quality bei akzeptabler Performance
            index = _get_faiss().IndexHNSWFlat(self.embedding_dim, 48)
            index.hnsw.efConstruction = 500  # Erhöht von 200 → 500 für 112k Vektoren
            index.hnsw.efSearch = 80  # Erhöht von 64 → 80 für besseren Recall
            
            # Füge Embeddings hinzu
            logger.info("📥 Adding embeddings to FAISS index...")
            index.add(embeddings)
            
            # Speichere ID-Mapping (FAISS-Position → DB-ID)
            self.id_map = [row[0] for row in triples]  # Triple IDs
            self.index = index
            self.stats['total_triples'] = len(self.id_map)
            self.stats['last_rebuild'] = datetime.now().isoformat()
            
            # Speichere zu Disk
            self.save_index()
            
            elapsed = time.time() - start_time
            logger.info(f"✅ Index built: {len(self.id_map):,} triples in {elapsed:.1f}s")
            
            return True
            
        except Exception as e:
            logger.error(f"❌ Index build failed: {e}")
            import traceback
            traceback.print_exc()
            return False
    
    def save_index(self) -> bool:
        """Speichert Index und Mappings zu Disk."""
        try:
            index_path = self.cache_dir / "kg_index.faiss"
            mapping_path = self.cache_dir / "kg_id_map.pkl"
            
            # Speichere FAISS Index
            _get_faiss().write_index(self.index, str(index_path))
            
            # Speichere ID-Mapping
            with open(mapping_path, 'wb') as f:
                pickle.dump(self.id_map, f)
            
            # Speichere Metadata
            metadata = {
                'db_hash': self._get_db_hash(),
                'triple_count': len(self.id_map),
                'embedding_dim': self.embedding_dim,
                'min_confidence': self.min_confidence,
                'created_at': datetime.now().isoformat()
            }
            with open(self.metadata_path, 'wb') as f:
                pickle.dump(metadata, f)
            
            logger.info(f"💾 Index saved to {self.cache_dir}")
            return True
            
        except Exception as e:
            logger.error(f"❌ Index save failed: {e}")
            return False
    
    def load_index(self) -> bool:
        """Lädt Index und Mappings von Disk."""
        try:
            index_path = self.cache_dir / "kg_index.faiss"
            mapping_path = self.cache_dir / "kg_id_map.pkl"
            
            # Lade FAISS Index
            self.index = _get_faiss().read_index(str(index_path))
            
            # Lade ID-Mapping
            with open(mapping_path, 'rb') as f:
                self.id_map = pickle.load(f)
            
            self.stats['total_triples'] = len(self.id_map)
            self.stats['cache_hits'] += 1
            
            logger.info(f"✅ Index loaded: {len(self.id_map):,} triples")
            return True
            
        except Exception as e:
            logger.error(f"❌ Index load failed: {e}")
            return False
    
    def search(
        self, 
        query: str, 
        user_id: Optional[str] = None,
        session_id: Optional[str] = None,
        top_k: int = 20,
        min_similarity: float = 0.50
    ) -> List[Dict[str, Any]]:
        """
        Semantische Suche in KG-Triples mit FAISS.
        
        Args:
            query: Suchtext
            user_id: Optional User-Filter
            session_id: Optional Session-Filter
            top_k: Anzahl Top-Ergebnisse
            min_similarity: Minimale Cosine Similarity
            
        Returns:
            Liste von Triples mit Similarity-Scores, sortiert nach Relevanz
        """
        # 🔒 ISOLATION: user_id is REQUIRED to prevent cross-user data leakage.
        # The FAISS index is shared across all users; only the post-filter
        # restricts results. Falsy user_id would silently disable that filter.
        if not user_id:
            raise ValueError(
                "WellbeingKGFAISSManager.search requires a non-empty user_id "
                "to enforce per-user data isolation."
            )

        if self.index is None or len(self.id_map) == 0:
            logger.warning("⚠️ Index not available, attempting build...")
            if not self.build_index():
                return []

        try:
            from utils.embedding_singleton import get_embedding_model
            from wellbeing.wellbeing_db import WellbeingDatabase

            start_time = time.time()
            
            # Query Embedding
            embedding_model = get_embedding_model()
            query_emb = embedding_model.encode([query])[0]
            query_emb = np.array([query_emb], dtype=np.float32)
            _get_faiss().normalize_L2(query_emb)
            
            # FAISS Search (schnell!)
            # Suche 2x top_k für User/Session-Filter-Reserve
            # Lock to prevent races with add_triples_incremental() during index.add().
            with self._index_lock:
                k_search = min(top_k * 2, len(self.id_map))
                distances, indices = self.index.search(query_emb.reshape(1, -1), k_search)
                # Snapshot id_map slice we need so DB lookup is lock-free
                id_map_snapshot = list(self.id_map)
            
            # ✅ FIX: Konvertiere L2-Distanz zu Cosine Similarity
            # Für normalisierte Vektoren: L2² = 2 - 2*cos(θ)
            # Also: cos(θ) = 1 - L2²/2
            # HNSW gibt bereits L2² zurück (quadrierte Distanz)
            similarities = 1 - (distances / 2)
            
            # Konvertiere zu Triple-IDs und lade Details
            results = []
            # ⚠️ Use stored DB instance if available (prevents circular init)
            if self._db_instance is not None:
                db = self._db_instance
            else:
                # Fallback: create new instance (only when called standalone)
                db = WellbeingDatabase()
            
            for idx, sim in zip(indices[0], similarities[0]):
                if idx == -1 or sim < min_similarity:
                    continue
                
                triple_id = id_map_snapshot[idx]
                
                # Lade Triple-Details aus DB
                with db.get_connection() as conn:
                    cursor = conn.execute("""
                        SELECT 
                            t.subject, t.predicate, t.object, t.confidence,
                            t.session_id, t.created_at,
                            ps.user_id,
                            si.content as interaction_content,
                            si.created_at as interaction_date
                        FROM triples t
                        LEFT JOIN wellbeing_sessions ps ON t.session_id = ps.id
                        LEFT JOIN session_interactions si ON t.interaction_id = si.id
                        WHERE t.id = ?
                    """, (triple_id,))
                    row = cursor.fetchone()
                
                if not row:
                    continue
                
                # User-Filter
                if user_id and row['user_id'] != user_id:
                    continue
                
                # Session-Filter
                if session_id and row['session_id'] != session_id:
                    continue
                
                results.append({
                    'subject': row['subject'],
                    'predicate': row['predicate'],
                    'object': row['object'],
                    'confidence': row['confidence'],
                    'similarity': float(sim),
                    'session_id': row['session_id'],
                    'created_at': row['created_at'],
                    'interaction_content': row['interaction_content'],
                    'interaction_date': row['interaction_date']
                })
                
                if len(results) >= top_k:
                    break
            
            # Update Stats
            elapsed_ms = (time.time() - start_time) * 1000
            self.stats['total_searches'] += 1
            self.stats['avg_search_ms'] = (
                (self.stats['avg_search_ms'] * (self.stats['total_searches'] - 1) + elapsed_ms) 
                / self.stats['total_searches']
            )
            
            logger.info(f"🔍 FAISS Search: {len(results)} results in {elapsed_ms:.1f}ms (avg: {self.stats['avg_search_ms']:.1f}ms)")
            
            return results
            
        except Exception as e:
            logger.error(f"❌ FAISS search failed: {e}")
            import traceback
            traceback.print_exc()
            return []
    
    def get_stats(self) -> Dict[str, Any]:
        """Gibt Performance-Statistiken zurück."""
        return {
            **self.stats,
            'index_size_mb': self.index.ntotal * self.embedding_dim * 4 / (1024**2) if self.index else 0,
            'is_loaded': self.index is not None
        }
    
    def rebuild_if_stale(self) -> bool:
        """Prüft Staleness und rebuildet bei Bedarf."""
        with self._index_lock:
            if self._is_stale():
                logger.info("🔄 Index is stale, rebuilding...")
                return self.build_index(force_rebuild=True)
            return False

    def add_triples_incremental(
        self, triples: List[Tuple[int, str, str, str]]
    ) -> int:
        """Inkrementelles HNSW-Update für neu eingefügte Triples.

        Verhindert dass neue Fakten bis zum nächsten Full-Rebuild oder
        Prozess-Restart unsichtbar bleiben. ``IndexHNSWFlat`` unterstützt
        natives ``add()`` ohne vollständige Rekonstruktion; die Graph-Qualität
        bleibt nahe am Batch-Build.

        Args:
            triples: Liste von ``(triple_id, subject, predicate, object)``.

        Returns:
            Anzahl tatsächlich hinzugefügter Vektoren.
        """
        if not triples:
            return 0

        with self._index_lock:
            if self.index is None:
                # Kein Index geladen → voller Build holt die neuen Triples
                # ohnehin mit ab. Kein separater incremental Pfad nötig.
                logger.debug(
                    "⚠️ No FAISS index loaded — incremental add deferred to full build"
                )
                return 0

            try:
                texts = [f"{s} {p} {o}" for (_id, s, p, o) in triples]
                embedding_model = get_embedding_model()
                if not embedding_model.is_loaded():
                    embedding_model.load_model()
                embeddings = embedding_model.encode(
                    texts, batch_size=32, show_progress_bar=False
                )
                embeddings = np.asarray(embeddings, dtype=np.float32)
                if embeddings.ndim == 1:
                    embeddings = embeddings.reshape(1, -1)

                # Same normalization as full build (cosine via L2-normalized vectors)
                _get_faiss().normalize_L2(embeddings)

                self.index.add(embeddings)
                self.id_map.extend(int(t[0]) for t in triples)
                self.stats['total_triples'] = len(self.id_map)

                # Persist immediately so a process restart sees the new vectors.
                # save_index() refreshes metadata.db_hash so _is_stale() won't
                # trigger a redundant full rebuild on next startup.
                self.save_index()

                total = len(self.id_map)
                logger.info(
                    f"📥 FAISS incremental: +{len(triples)} triples (total {total:,})"
                )
                return len(triples)
            except Exception as e:
                logger.error(f"❌ FAISS incremental add failed: {e}")
                import traceback
                traceback.print_exc()
                return 0


# Singleton Factory
_wellbeing_kg_faiss_manager_instance = None

def get_psycho_kg_faiss_manager(
    db_path: str = "wellbeing_store.db",
    embedding_dim: int = 1024,
    db_instance: Any = None  # ⚠️ NEW: Accept DB instance to prevent circular init
) -> "WellbeingKGFAISSManager":
    """
    Singleton Factory für WellbeingKGFAISSManager.
    
    Verhindert multiple Index-Instanzen (Memory Leak Prevention).
    
    ⚠️ NOTE: auto_load=False to prevent circular import with WellbeingDatabase!
    The index must be explicitly loaded/built by calling:
    - manager.load_index() or
    - manager.build_index()
    
    Args:
        db_path: Path to wellbeing_store.db
        embedding_dim: Embedding dimensionality (1024 for bge-large)
        db_instance: Optional existing WellbeingDatabase instance (prevents circular init)
    """
    global _wellbeing_kg_faiss_manager_instance
    
    if _wellbeing_kg_faiss_manager_instance is None:
        _wellbeing_kg_faiss_manager_instance = WellbeingKGFAISSManager(
            db_path=db_path,
            embedding_dim=embedding_dim,
            auto_load=False,  # ⚠️ Prevent circular init with WellbeingDatabase
            db_instance=db_instance  # ⚠️ Pass DB instance to prevent creating new ones
        )
    # Late-bound injection: a cached singleton may have been created earlier
    # without a DB handle. Reuse the existing manager, but attach the live
    # WellbeingDatabase instance so build/search paths do not fall back
    # to a fresh loader-less DB.
    if db_instance is not None and _wellbeing_kg_faiss_manager_instance._db_instance is None:
        _wellbeing_kg_faiss_manager_instance._db_instance = db_instance

    return _wellbeing_kg_faiss_manager_instance
