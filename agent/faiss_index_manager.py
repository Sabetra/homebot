"""
🚀 FAISS Index Manager - Adaptive Hybrid Search Engine
======================================================

Features:
- Dual-Index Strategy: Recent (20k) + Full (all)
- Adaptive Search: Fast Path + Fallback
- Intelligent ID Mapping
- Performance Statistics
- Persistence Support (Auto Save/Load)
- Staleness Detection (Auto Rebuild bei DB-Änderungen)

Author: AI Assistant
Date: 2025-10-06
Version: 2.0 (with Persistence)
"""

import os
import multiprocessing

# 🚀 CRITICAL: Setze OMP_NUM_THREADS VOR dem FAISS-Import!
# Dies ist die einzige Methode, die garantiert funktioniert
_n_cores = multiprocessing.cpu_count()
os.environ['OMP_NUM_THREADS'] = str(_n_cores)
os.environ['OPENBLAS_NUM_THREADS'] = str(_n_cores)
os.environ['MKL_NUM_THREADS'] = str(_n_cores)

import sqlite3
import numpy as np
import time
import pickle
import logging
import hashlib
import threading
from pathlib import Path
from typing import List, Tuple, Dict, Optional

logger = logging.getLogger(__name__)

faiss = None


def _ensure_faiss_runtime() -> bool:
    """Lazy-load FAISS and apply OpenMP settings once at runtime."""
    global faiss
    if faiss is not None:
        return True  # type: ignore[unreachable]
    try:
        import faiss as _faiss  # type: ignore
        faiss = _faiss
        try:
            faiss.omp_set_num_threads(_n_cores)
            logger.info(
                f"🚀 FAISS OpenMP: {_n_cores} Threads aktiviert "
                f"(OMP_NUM_THREADS={os.environ.get('OMP_NUM_THREADS')})"
            )
        except Exception as e:
            logger.warning(f"⚠️ FAISS OpenMP konnte nicht konfiguriert werden: {e}")
        return True  # type: ignore[unreachable]
    except ImportError as e:
        logger.error(f"❌ FAISS Import fehlgeschlagen: {e}")
        return False


# Global Singleton Instance
_faiss_manager_instance = None
_faiss_manager_lock = threading.Lock()

# Staleness TTL cache -- avoid checking on every search
_STALENESS_TTL_SECONDS = 30.0


class FAISSIndexManager:
    """
    Verwaltet FAISS-Indizes für optimale Hybrid-Suche.
    
    Strategy:
    1. Recent Index: Neueste 20k Chunks (Fast Path)
    2. Full Index: Alle Chunks (Fallback)
    3. Adaptive Decision: Confidence-basiert
    """
    
    def __init__(self, db_path: str, embedding_dim: int = 1024, auto_load: bool = True, 
                 auto_rebuild_on_stale: bool = True, rebuild_threshold: int = 1000,
                 use_hnsw: bool = True, hnsw_m: int = 48, hnsw_ef_construction: int = 400):
        """
        Initialisiert FAISS Index Manager mit automatischer Persistence.
        
        Args:
            db_path: Pfad zur SQLite Datenbank
            embedding_dim: Dimensionalität der Embeddings
            auto_load: Automatisch Indizes von Disk laden (wenn verfügbar)
            auto_rebuild_on_stale: Automatisch rebuilden wenn Staleness bei search() erkannt
            rebuild_threshold: Nach wie vielen neuen Chunks ein Auto-Rebuild triggert
            use_hnsw: 🆕 Wenn True, nutze HNSW statt IVF für Full Index (schneller, besserer Recall)
            hnsw_m: 🆕 HNSW Parameter M (Anzahl Verbindungen pro Knoten, 16-64, default=48, optimal für 100k-500k Vektoren)
            hnsw_ef_construction: 🆕 HNSW Parameter efConstruction (Build-Qualität, 100-600, default=400, höhere Werte = bessere Qualität)
        """
        if not _ensure_faiss_runtime():
            raise RuntimeError("FAISS ist nicht verfügbar")

        self.db_path = db_path
        self.embedding_dim = embedding_dim
        self.auto_rebuild_on_stale = auto_rebuild_on_stale
        self.rebuild_threshold = rebuild_threshold
        self.use_hnsw = use_hnsw
        self.hnsw_m = hnsw_m
        self.hnsw_ef_construction = hnsw_ef_construction
        
        # Dual-Index Setup
        self.recent_index: Optional[faiss.Index] = None  # type: ignore[name-defined]
        self.full_index: Optional[faiss.Index] = None  # type: ignore[name-defined]
        
        # ID Mappings: FAISS-Index-ID → DB-Chunk-ID
        self.recent_id_map: List[str] = []
        self.full_id_map: List[str] = []
        
        # Thread safety: lock for index operations (search, rebuild)
        self._search_lock = threading.Lock()
        self._rebuild_lock = threading.Lock()
        self._rebuilding = False
        
        # Staleness TTL cache
        self._last_staleness_check: float = 0.0
        self._last_staleness_result: bool = True  # assume stale initially
        
        # Performance Statistics
        self.stats = {
            'fast_path_hits': 0,
            'fallback_hits': 0,
            'avg_fast_time_ms': 0.0,
            'avg_fallback_time_ms': 0.0,
            'total_searches': 0,
            'fast_path_percentage': 0.0,
            'auto_rebuilds': 0,  # Zähler für Auto-Rebuilds
            'chunks_since_rebuild': 0  # Neue Chunks seit letztem Rebuild
        }
        
        # Index Cache Path
        self.cache_dir = Path(db_path).parent / "faiss_cache"
        self.cache_dir.mkdir(exist_ok=True)
        
        # Metadata für Staleness-Detection
        self.metadata_path = self.cache_dir / "metadata.pkl"
        
        logger.info(
            f"✅ FAISSIndexManager initialized (dim={embedding_dim}, "
            f"index_type={'HNSW' if use_hnsw else 'IVF'}, "
            f"M={hnsw_m if use_hnsw else 'N/A'}, "
            f"efConstruction={hnsw_ef_construction if use_hnsw else 'N/A'})"
        )
        
        # Auto-Load: Versuche Indizes zu laden, rebuild wenn nötig
        if auto_load:
            self._auto_load_or_build()
    
    def _auto_load_or_build(self) -> None:
        """
        Automatisches Laden oder Bauen der Indizes.
        
        Logik:
        1. Prüfe ob Cached Indizes existieren
        2. Prüfe ob sie noch aktuell sind (Staleness-Check)
        3. Lade wenn aktuell, sonst rebuild
        """
        if self._indexes_cached() and not self._is_stale():
            # Cached und aktuell → Laden
            logger.info("📂 Loading cached FAISS indexes...")
            if self.load_indexes():
                logger.info(f"✅ Indexes loaded (Recent: {len(self.recent_id_map):,}, Full: {len(self.full_id_map):,})")
                return
        
        # Nicht cached oder veraltet → Bauen
        logger.info("🔨 Building fresh FAISS indexes...")
        self.build_indexes()
        self.save_indexes()
    
    def _indexes_cached(self) -> bool:
        """Prüft ob Cached Indizes existieren."""
        recent_path = self.cache_dir / "recent_index.faiss"
        full_path = self.cache_dir / "full_index.faiss"
        return recent_path.exists() and full_path.exists() and self.metadata_path.exists()
    
    def _is_stale(self) -> bool:
        """
        Prüft ob Cached Indizes veraltet sind.
        
        Staleness-Kriterien:
        1. DB-Hash hat sich geändert
        2. Anzahl Chunks hat sich geändert
        3. Letzte Änderung der DB nach Index-Build
        
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
                logger.info(f"⚠️ DB changed: {cached_hash[:8]}... → {current_hash[:8]}...")
                return True
            
            # Zusätzlicher Check: DB-Modifikationszeit
            db_mtime = os.path.getmtime(self.db_path)
            index_build_time = metadata.get('build_timestamp', 0)
            
            if db_mtime > index_build_time:
                logger.info(f"⚠️ DB modified after index build")
                return True
            
            logger.debug("✓ Indexes are up-to-date")
            return False
            
        except Exception as e:
            logger.warning(f"⚠️ Staleness check failed: {e}")
            return True  # Bei Fehler: Rebuild zur Sicherheit
    
    def _is_stale_cached(self) -> bool:
        """
        Staleness check with TTL caching.
        
        Avoids re-checking on every single search call.
        Cached result is valid for _STALENESS_TTL_SECONDS.
        """
        now = time.time()
        if (now - self._last_staleness_check) < _STALENESS_TTL_SECONDS:
            return self._last_staleness_result
        
        result = self._is_stale()
        self._last_staleness_check = now
        self._last_staleness_result = result
        return result
    
    def _get_db_hash(self) -> str:
        """
        Berechnet Hash der Datenbank für Staleness-Detection.
        
        Hash basiert auf:
        - Anzahl Chunks
        - Anzahl Chunks mit Embeddings
        - Letzter chunk_id
        
        Returns:
            MD5-Hash als String
        """
        conn = None
        try:
            conn = sqlite3.connect(self.db_path)
            
            # Schnelle Statistiken
            stats = conn.execute("""
                SELECT 
                    COUNT(*) as total,
                    COUNT(embedding) as with_emb,
                    MAX(chunk_id) as last_id
                FROM chunks
            """).fetchone()
            
            # Hash über Statistiken
            hash_input = f"{stats[0]}:{stats[1]}:{stats[2]}"
            return hashlib.md5(hash_input.encode()).hexdigest()
            
        except Exception as e:
            logger.error(f"❌ Failed to compute DB hash: {e}")
            # Fallback: Timestamp
            return str(int(time.time()))
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass
    
    def build_indexes(self, recent_limit: int = 20000, auto_save: bool = True) -> None:
        """
        Baut FAISS Single-Index (HNSW-optimiert).
        
        🚀 MIGRATION 2025-11-15: Dual-Index → Single-Index
        - Entfernt: Recent Index (Flat 20k) - war LANGSAMER als HNSW!
        - Behalten: Full Index (HNSW) - schnellste Option für <1M Vektoren
        
        Index-Strategie:
        - HNSW für alle Größen (optimal bis 1M Vektoren)
        - IVF nur als Fallback bei sehr großen DBs (>1M)
        - Flat für sehr kleine DBs (<10k)
        
        Args:
            recent_limit: DEPRECATED - wird ignoriert (Kompatibilität)
            auto_save: Automatisch speichern nach Build
        """
        # Prevent concurrent rebuilds
        if not self._rebuild_lock.acquire(blocking=False):
            logger.warning("⚠️ build_indexes() already in progress, skipping")
            return
        
        try:
            self._rebuilding = True
            self._build_indexes_internal(auto_save=auto_save)
        finally:
            self._rebuilding = False
            self._rebuild_lock.release()
            # Invalidate staleness cache after rebuild
            self._last_staleness_check = 0.0
    
    def _build_indexes_internal(self, auto_save: bool = True) -> None:
        """Internal index build (must be called under _rebuild_lock)."""
        assert faiss is not None, "FAISS must be loaded by _ensure_faiss_runtime()"
        logger.info("🏗️  Building FAISS index (Single-HNSW)...")
        start_time = time.time()
        
        # Reset Chunk-Counter
        self.stats['chunks_since_rebuild'] = 0
        
        conn = sqlite3.connect(self.db_path)
        try:
            # BUILD SINGLE INDEX (alle Vektoren)
            logger.info("   Loading all vectors from database...")
            all_data = conn.execute("""
                SELECT chunk_id, embedding 
                FROM chunks 
                WHERE embedding IS NOT NULL
                ORDER BY chunk_id
            """).fetchall()
        finally:
            conn.close()
        
        if not all_data:
            logger.error("❌ No chunks with embeddings found!")
            return
        
        # Konvertiere Embeddings zu NumPy Array
        all_vectors = np.array([
            np.frombuffer(row[1], dtype=np.float32) 
            for row in all_data
        ], dtype=np.float32)
        
        # Normalisieren für Cosine Similarity
        faiss.normalize_L2(all_vectors)
        
        # ID-Mapping für alle Vektoren
        self.full_id_map = [row[0] for row in all_data]
        n_vectors = len(all_vectors)
        
        logger.info(f"   Loaded {n_vectors:,} vectors from database")
        
        # Wähle Index-Typ basierend auf Größe und use_hnsw Flag
        if self.use_hnsw:
            # 🚀 HNSW: Best choice für <1M Vektoren
            logger.info(f"   Building HNSW Index (M={self.hnsw_m}, efConstruction={self.hnsw_ef_construction})...")
            # Klarstellung (Log): FAISS-HNSW ist per Design ein CPU-Index — FAISS bietet
            # keine GPU-Variante für HNSW (offizielles faiss-gpu-Paket ist eingestellt,
            # venv nutzt faiss-cpu). Die "GPU erkannt"-Zeile im RAG-Store bezieht sich
            # nur auf die Batch-Size-Erkennung; das Embedding-Modell läuft separat auf
            # der AUX-GPU. Der Build ist einmalig und wird via save_indexes() persistiert.
            logger.info(
                "   ℹ️  HNSW-Index-Builder: CPU (FAISS hat keine GPU-Variante für HNSW; "
                "Embedding-Modell läuft separat auf GPU). Build ist einmalig + wird gespeichert."
            )
            
            # CRITICAL: HNSW mit Inner Product Metric
            # FAISS Syntax: IndexHNSWFlat(dim, M, metric)
            self.full_index = faiss.IndexHNSWFlat(
                self.embedding_dim, 
                self.hnsw_m,
                faiss.METRIC_INNER_PRODUCT
            )
            self.full_index.hnsw.efConstruction = self.hnsw_ef_construction  # type: ignore
            self.full_index.add(all_vectors)  # type: ignore
            
            # Default efSearch für Query-Zeit (wird dynamisch angepasst)
            self.full_index.hnsw.efSearch = 64  # type: ignore
            
            logger.info(f"   ✅ HNSW Index built: {n_vectors:,} vectors (metric=IP, efSearch=64)")
            
        elif n_vectors > 1_000_000:
            # 🏗️ IVF: Nur für SEHR große Indizes (>1M)
            nlist = min(int(np.sqrt(n_vectors)), 4096)
            quantizer = faiss.IndexFlatIP(self.embedding_dim)
            self.full_index = faiss.IndexIVFFlat(
                quantizer, self.embedding_dim, nlist, faiss.METRIC_INNER_PRODUCT
            )
            
            # Training erforderlich für IVF
            logger.info(f"   Training IVF Index ({nlist} clusters)...")
            self.full_index.train(all_vectors)  # type: ignore
            self.full_index.add(all_vectors)  # type: ignore
            
            # Default nprobe (Anzahl Cluster zu durchsuchen)
            self.full_index.nprobe = 10  # type: ignore
            
            logger.info(f"   ✅ IVF Index built: {n_vectors:,} vectors (nprobe=10)")
        else:
            # Für kleine Indizes (<10k): Flat Index
            self.full_index = faiss.IndexFlatIP(self.embedding_dim)
            self.full_index.add(all_vectors)  # type: ignore
            
            logger.info(f"   ✅ Flat Index built: {n_vectors:,} vectors")
        
        # DEPRECATED: recent_index und recent_id_map werden nicht mehr verwendet
        # Setze auf None/[] für Klarheit und Abwärtskompatibilität
        self.recent_index = None
        self.recent_id_map = []
        
        elapsed = time.time() - start_time
        logger.info(f"✅ Single-Index build complete in {elapsed:.2f}s ({n_vectors:,} vectors)")
        logger.info(f"   Index Type: {'HNSW' if self.use_hnsw else 'IVF/Flat'}")
        logger.info(f"   Memory: ~{(n_vectors * self.embedding_dim * 4) / 1024 / 1024:.1f} MB")
        
        # Auto-Save nach erfolgreichem Build
        if auto_save:
            self.save_indexes()
    
    def search(
        self, 
        query_embedding: np.ndarray, 
        k: int = 5, 
        min_confidence: Optional[float] = None,
        adaptive_confidence: bool = True,
        adaptive_mode: str = 'stepped',
        auto_rebuild_if_stale: Optional[bool] = None
    ) -> Tuple[List[str], List[float], str]:
        """
        Single-Index HNSW Search (Direct, No Fallback).
        
        🚀 MIGRATION 2025-11-15: Simplified to Single-Index
        - Entfernt: Dual-Index Fast-Path/Fallback-Logik
        - Direkte Suche im HNSW Full-Index (schneller & einfacher!)
        
        Strategy:
        1. Prüfe ob Index noch aktuell (Staleness Check)
        2. Auto-Rebuild wenn veraltet (optional)
        3. Dynamisches efSearch basierend auf k
        4. Direkte HNSW-Suche (1-3ms, kein Overhead)
        
        Args:
            query_embedding: Query-Vektor
            k: Anzahl gewünschter Results
            min_confidence: DEPRECATED - wird ignoriert (Kompatibilität)
            adaptive_confidence: DEPRECATED - wird ignoriert (Kompatibilität)
            adaptive_mode: DEPRECATED - wird ignoriert (Kompatibilität)
            auto_rebuild_if_stale: Überschreibt self.auto_rebuild_on_stale
        
        Returns:
            (chunk_ids, scores, 'single_index')
        
        Examples:
            >>> search(query_emb, k=5)   # → Direkt HNSW, ~1.2ms
            >>> search(query_emb, k=100) # → Direkt HNSW, ~2.5ms
        """
        # Note: min_confidence, adaptive_confidence und adaptive_mode werden ignoriert
        # Behalten für API-Kompatibilität mit altem Code
        assert faiss is not None, "FAISS must be loaded by _ensure_faiss_runtime()"
        # Default: Nutze Instance-Setting
        if auto_rebuild_if_stale is None:
            auto_rebuild_if_stale = self.auto_rebuild_on_stale
        
        # 🔍 STALENESS CHECK (with TTL caching to avoid per-query overhead)
        if auto_rebuild_if_stale and self._is_stale_cached():
            logger.warning("⚠️ Index stale! Auto-rebuilding...")
            self.build_indexes(auto_save=True)  # build_indexes has its own concurrent guard
            self.stats['auto_rebuilds'] += 1
        
        # 🐛 CRITICAL: Check if index exists!
        if self.full_index is None:
            logger.error(
                f"❌ FAISS INDEX NOT BUILT!\n"
                f"   full_index: ❌ None\n"
                f"   → Bitte build_indexes() aufrufen!"
            )
            raise RuntimeError("Index not built! Call build_indexes() first.")
        
        # Validate embedding dimension
        if query_embedding.size != self.embedding_dim:
            raise ValueError(
                f"Query embedding dimension mismatch: "
                f"got {query_embedding.size}, expected {self.embedding_dim}"
            )
        
        # Normalisieren
        query_vec = query_embedding.reshape(1, -1).astype(np.float32)
        faiss.normalize_L2(query_vec)
        
        # 🚀 SINGLE-INDEX SEARCH (Direct HNSW) -- atomic with efSearch tuning
        with self._search_lock:
            start = time.perf_counter()
            
            # Tune Search-Parameter dynamisch basierend auf k
            if self.use_hnsw and hasattr(self.full_index, 'hnsw'):
                # 🎯 HNSW: Dynamic efSearch für optimalen Recall
                ef_search = self._calculate_ef_search(k)
                self.full_index.hnsw.efSearch = ef_search  # type: ignore
                logger.debug(f"🎯 Dynamic efSearch={ef_search} for k={k}")
            elif hasattr(self.full_index, 'nprobe'):
                # IVF: Scale nprobe mit k für besseren Recall
                nprobe = min(10 + (k // 10), 50)  # 10-50 basierend auf k
                self.full_index.nprobe = nprobe  # type: ignore
                logger.debug(f"🎯 Dynamic nprobe={nprobe} for k={k}")
            
            # Direkte Suche im Single-Index
            scores_arr, indices_arr = self.full_index.search(query_vec, k)  # type: ignore
            search_time = (time.perf_counter() - start) * 1000
        
        # Update Stats
        self.stats['total_searches'] += 1
        self._update_avg_time('single', search_time)
        
        # Mapping zu DB-Chunk-IDs
        valid_indices = [i for i in indices_arr[0] if i < len(self.full_id_map)]
        chunk_ids = [self.full_id_map[i] for i in valid_indices]
        scores = [float(scores_arr[0][j]) for j, i in enumerate(indices_arr[0]) if i < len(self.full_id_map)]
        
        # Debug logging
        top_score = scores[0] if scores else 0.0
        logger.debug(
            f"⚡ Single-Index Search: {search_time:.1f}ms | "
            f"k={k} | Results={len(chunk_ids)} | Top-Score={top_score:.3f}"
        )
        
        return chunk_ids, scores, 'single_index'
    
    def batch_search(
        self,
        query_embeddings: np.ndarray,
        k_list: List[int],
        min_confidence: Optional[float] = None,
        adaptive_confidence: bool = True,
        adaptive_mode: str = 'stepped'
    ) -> List[Tuple[List[str], List[float], str]]:
        """
        🚀 BATCH-SEARCH: Suche für mehrere Queries parallel (OpenMP-optimiert)
        
        🚀 MIGRATION 2025-11-15: Simplified to Single-Index
        - Direkte HNSW Batch-Search (kein Fast-Path/Fallback)
        - 150x schneller als sequenzielle Searches
        - Nutzt alle CPU-Cores via OpenMP (94.7% CPU-Auslastung)
        - Keine Python-GIL-Limitierung
        
        Args:
            query_embeddings: (N, dim) NumPy Array von Query-Vektoren
            k_list: Liste von k-Werten für jede Query (z.B. [6, 5, 5, 5, 5, 5])
            min_confidence: DEPRECATED - wird ignoriert (Kompatibilität)
            adaptive_confidence: DEPRECATED - wird ignoriert (Kompatibilität)
            adaptive_mode: DEPRECATED - wird ignoriert (Kompatibilität)
            
        Returns:
            Liste von (chunk_ids, scores, 'single_index') Tupeln für jede Query
            
        Example:
            >>> # 6 Queries (1 Main + 5 Sub)
            >>> queries = np.array([emb1, emb2, emb3, emb4, emb5, emb6])
            >>> k_list = [6, 5, 5, 5, 5, 5]
            >>> results = manager.batch_search(queries, k_list)
            >>> # Returns in ~3ms statt 4500ms! 🚀
        """
        # Validate inputs
        assert faiss is not None, "FAISS must be loaded by _ensure_faiss_runtime()"
        if query_embeddings.ndim != 2:
            raise ValueError(f"query_embeddings must be 2D array, got shape {query_embeddings.shape}")
        
        if query_embeddings.shape[1] != self.embedding_dim:
            raise ValueError(
                f"Embedding dimension mismatch: "
                f"got {query_embeddings.shape[1]}, expected {self.embedding_dim}"
            )
        
        if len(k_list) != len(query_embeddings):
            raise ValueError(f"k_list length ({len(k_list)}) must match number of queries ({len(query_embeddings)})")
        
        # Check if index exists
        if self.full_index is None:
            logger.error("❌ FAISS INDEX NOT BUILT! Call build_indexes() first.")
            raise RuntimeError("Index not built! Call build_indexes() first.")
        
        # Normalize all queries
        query_vecs = query_embeddings.astype(np.float32)
        faiss.normalize_L2(query_vecs)
        
        # Determine max k for batch search
        max_k = max(k_list)
        
        # Statistics
        batch_size = len(query_embeddings)
        
        # Thread-safe: lock around efSearch/nprobe mutation + search
        with self._search_lock:
            start_time = time.perf_counter()
            
            # Tune search parameters dynamically based on max k
            if self.use_hnsw and hasattr(self.full_index, 'hnsw'):
                # 🎯 Dynamic efSearch: Adapts to query requirements
                ef_search = self._calculate_ef_search(max_k)
                self.full_index.hnsw.efSearch = ef_search  # type: ignore
                logger.debug(f"🎯 Dynamic efSearch={ef_search} for max_k={max_k}")
            elif hasattr(self.full_index, 'nprobe'):
                # IVF: Scale nprobe with k for better recall
                nprobe = min(10 + (max_k // 10), 50)  # 10-50 based on k
                self.full_index.nprobe = nprobe  # type: ignore
                logger.debug(f"🎯 Dynamic nprobe={nprobe} for max_k={max_k}")
            
            # 🚀 SINGLE BATCH SEARCH: Direkt im Full-Index
            scores_batch, indices_batch = self.full_index.search(query_vecs, max_k)  # type: ignore
            search_time = (time.perf_counter() - start_time) * 1000
        
        # Process results for each query (trim to individual k)
        results = []
        for i, k in enumerate(k_list):
            valid_indices = [idx for idx in indices_batch[i][:k] if idx < len(self.full_id_map)]
            chunk_ids = [self.full_id_map[idx] for idx in valid_indices]
            scores = [float(scores_batch[i][j]) for j in range(len(valid_indices))]
            
            results.append((chunk_ids, scores, 'single_index'))
        
        # Update statistics
        self.stats['total_searches'] += batch_size
        self._update_avg_time('single', search_time)
        
        logger.info(
            f"⚡ Batch Search: {batch_size} queries in {search_time:.1f}ms "
            f"({search_time/batch_size:.1f}ms/query avg) | Strategy: single_index"
        )
        
        return results
    
    def _update_avg_time(self, mode: str, time_ms: float) -> None:
        """
        Aktualisiert durchschnittliche Suchzeit.
        
        🚀 MIGRATION 2025-11-15: Unified to single mode
        - Nur noch 'single' mode (kein fast/fallback mehr)
        """
        if mode == 'single':
            # Exponential Moving Average
            old_avg = self.stats.get('avg_search_time_ms', time_ms)
            self.stats['avg_search_time_ms'] = old_avg * 0.9 + time_ms * 0.1
        
        # Legacy-Kompatibilität (falls alter Code noch fast/fallback nutzt)
        elif mode in ('fast', 'fallback'):
            logger.warning(f"⚠️ Legacy mode '{mode}' used in _update_avg_time - mapping to 'single'")
    
    def save_indexes(self) -> None:
        """
        Speichert Index und Metadata auf Disk.
        
        🚀 MIGRATION 2025-11-15: Single-Index
        Gespeichert wird:
        - Full Index (FAISS)
        - ID Mappings (Pickle)
        - Metadata: DB-Hash, Timestamp, Statistiken
        """
        if self.full_index is None:
            logger.warning("⚠️ Cannot save: Index not built yet")
            return
        
        logger.info("💾 Saving FAISS index...")
        start_time = time.time()
        assert faiss is not None, "FAISS must be loaded by _ensure_faiss_runtime()"
        try:
            # 1. Save Full Index
            full_path = self.cache_dir / "full_index.faiss"
            faiss.write_index(self.full_index, str(full_path))
            
            # 2. Save ID Mappings
            mappings_path = self.cache_dir / "id_mappings.pkl"
            with open(mappings_path, 'wb') as f:
                pickle.dump({
                    'full_id_map': self.full_id_map,
                    # Deprecated (Kompatibilität):
                    'recent_id_map': []
                }, f)
            
            # 3. Save Metadata (für Staleness-Detection)
            metadata = {
                'db_hash': self._get_db_hash(),
                'build_timestamp': time.time(),
                'embedding_dim': self.embedding_dim,
                'full_count': len(self.full_id_map),
                'version': '3.0',  # Neue Version für Single-Index
                'use_hnsw': self.use_hnsw,
                'hnsw_m': self.hnsw_m if self.use_hnsw else None,
                'hnsw_ef_construction': self.hnsw_ef_construction if self.use_hnsw else None,
                # Deprecated (Kompatibilität):
                'recent_count': 0
            }
            
            with open(self.metadata_path, 'wb') as f:
                pickle.dump(metadata, f)
            
            elapsed = time.time() - start_time
            
            # Berechne Disk-Space
            total_size = sum([
                full_path.stat().st_size,
                mappings_path.stat().st_size,
                self.metadata_path.stat().st_size
            ])
            size_mb = total_size / (1024 * 1024)
            
            logger.info(f"✅ Index saved to {self.cache_dir}")
            logger.info(f"   Size: {size_mb:.1f} MB, Time: {elapsed:.2f}s")
            
        except Exception as e:
            logger.error(f"❌ Failed to save indexes: {e}")
            import traceback
            traceback.print_exc()
    
    def load_indexes(self) -> bool:
        """
        Lädt Index von Disk mit Validation.
        
        🚀 MIGRATION 2025-11-15: Single-Index
        
        Returns:
            True wenn erfolgreich, False sonst
        """
        try:
            full_path = self.cache_dir / "full_index.faiss"
            mappings_path = self.cache_dir / "id_mappings.pkl"
            
            # Prüfe ob alle Files existieren
            if not (full_path.exists() and mappings_path.exists() and self.metadata_path.exists()):
                logger.info("📂 No complete cached index found")
                return False
            
            logger.info("📂 Loading FAISS index from cache...")
            start_time = time.time()
            
            # 1. Load Metadata (für Validation)
            with open(self.metadata_path, 'rb') as f:
                metadata = pickle.load(f)
            
            # Validate Metadata
            if metadata.get('embedding_dim') != self.embedding_dim:
                logger.warning(f"⚠️ Dimension mismatch: cached={metadata.get('embedding_dim')}, current={self.embedding_dim}")
                return False
            
            # Validate Index-Typ (HNSW vs IVF)
            cached_use_hnsw = metadata.get('use_hnsw', False)
            if cached_use_hnsw != self.use_hnsw:
                logger.warning(
                    f"⚠️ Index type changed: cached={'HNSW' if cached_use_hnsw else 'IVF'}, "
                    f"current={'HNSW' if self.use_hnsw else 'IVF'} → Rebuild erforderlich"
                )
                return False
            
            # Validate HNSW-Parameter (wenn HNSW genutzt wird)
            if self.use_hnsw:
                cached_m = metadata.get('hnsw_m')
                cached_ef = metadata.get('hnsw_ef_construction')
                
                if cached_m != self.hnsw_m or cached_ef != self.hnsw_ef_construction:
                    logger.warning(
                        f"⚠️ HNSW parameters changed:\n"
                        f"   M: {cached_m} → {self.hnsw_m}\n"
                        f"   efConstruction: {cached_ef} → {self.hnsw_ef_construction}\n"
                        f"   → Rebuild erforderlich für optimale Performance"
                    )
                    return False
            
            # 2. Load Full Index
            assert faiss is not None, "FAISS must be loaded by _ensure_faiss_runtime()"
            self.full_index = faiss.read_index(str(full_path))
            
            # 3. Load ID Mappings
            with open(mappings_path, 'rb') as f:
                mappings = pickle.load(f)
                self.full_id_map = mappings.get('full_id_map', [])
                # Deprecated: recent_id_map wird ignoriert
                self.recent_id_map = []
                self.recent_index = None
            
            # Validate Counts
            if len(self.full_id_map) != metadata.get('full_count'):
                logger.warning("⚠️ ID mapping count mismatch")
                return False
            
            elapsed = time.time() - start_time
            
            # Berechne Alter des Cache
            cache_age = time.time() - metadata.get('build_timestamp', time.time())
            cache_age_hours = cache_age / 3600
            
            logger.info(f"✅ Loaded Single-Index: {len(self.full_id_map):,} vectors")
            logger.info(f"   Load Time: {elapsed:.2f}s, Cache Age: {cache_age_hours:.1f}h")
            logger.info(f"   Index Type: {'HNSW' if self.use_hnsw else 'IVF/Flat'}")
            
            return True
            
        except Exception as e:
            logger.error(f"❌ Failed to load index: {e}")
            import traceback
            traceback.print_exc()
            return False
    
    def invalidate_cache(self) -> None:
        """
        Löscht den Index-Cache.
        
        🚀 MIGRATION 2025-11-15: Single-Index
        
        Nützlich wenn:
        - Manueller Rebuild gewünscht
        - Cache beschädigt
        - Embedding-Model geändert
        """
        logger.info("🗑️  Invalidating FAISS cache...")
        
        try:
            # Lösche alle Cache-Files
            cache_files = [
                "full_index.faiss",
                "id_mappings.pkl",
                "metadata.pkl",
                # Legacy (für alte Dual-Index Caches):
                "recent_index.faiss"
            ]
            
            deleted = 0
            for filename in cache_files:
                file_path = self.cache_dir / filename
                if file_path.exists():
                    file_path.unlink()
                    deleted += 1
            
            logger.info(f"✅ Deleted {deleted} cache files")
            
            # Reset In-Memory Index
            self.recent_index = None
            self.full_index = None
            self.recent_id_map = []
            self.full_id_map = []
            
        except Exception as e:
            logger.error(f"❌ Failed to invalidate cache: {e}")
    
    def force_rebuild(self, recent_limit: int = 20000) -> None:
        """
        Erzwingt einen kompletten Rebuild der Indizes.
        
        Steps:
        1. Invalidiert den Cache
        2. Baut Indizes neu
        3. Speichert neu
        
        Args:
            recent_limit: Anzahl der Chunks für Recent Index
        """
        logger.info("🔨 Forcing index rebuild...")
        self.invalidate_cache()
        self.build_indexes(recent_limit=recent_limit, auto_save=True)
    
    def get_statistics(self) -> Dict:
        """
        Gibt Performance-Statistiken und Cache-Info zurück.
        
        🚀 MIGRATION 2025-11-15: Single-Index Stats
        
        Returns:
            Dict mit allen Statistiken
        """
        stats = {
            **self.stats,
            'index_size': len(self.full_id_map) if self.full_id_map else 0,
            'index_loaded': self.full_index is not None,
            'index_type': 'HNSW' if self.use_hnsw else 'IVF/Flat',
            'hnsw_m': self.hnsw_m if self.use_hnsw else None,
            'hnsw_ef_construction': self.hnsw_ef_construction if self.use_hnsw else None,
            # Deprecated (Kompatibilität):
            'recent_index_size': 0,
            'full_index_size': len(self.full_id_map) if self.full_id_map else 0,
        }
        
        # Cache-Info hinzufügen
        if self.metadata_path.exists():
            try:
                with open(self.metadata_path, 'rb') as f:
                    metadata = pickle.load(f)
                
                cache_age = time.time() - metadata.get('build_timestamp', time.time())
                
                stats.update({
                    'cache_exists': True,
                    'cache_age_hours': cache_age / 3600,
                    'cache_db_hash': metadata.get('db_hash', 'unknown')[:8],
                    'cache_version': metadata.get('version', 'unknown'),
                })
            except Exception:
                stats['cache_exists'] = False
        else:
            stats['cache_exists'] = False
        
        # Cache-Size berechnen
        cache_files = [
            "recent_index.faiss",
            "full_index.faiss",
            "id_mappings.pkl",
            "metadata.pkl"
        ]
        
        total_size = 0
        for filename in cache_files:
            file_path = self.cache_dir / filename
            if file_path.exists():
                total_size += file_path.stat().st_size
        
        stats['cache_size_mb'] = total_size / (1024 * 1024)
        
        return stats
    
    def reset_statistics(self) -> None:
        """Setzt Performance-Statistiken zurück."""
        self.stats = {
            'fast_path_hits': 0,
            'fallback_hits': 0,
            'avg_fast_time_ms': 0.0,
            'avg_fallback_time_ms': 0.0,
            'total_searches': 0,
            'fast_path_percentage': 0.0
        }
        logger.info("📊 Statistics reset")
    
    def notify_chunks_added(self, num_chunks: int = 1) -> bool:
        """
        Benachrichtigt den Manager über neu hinzugefügte Chunks.
        Triggert Auto-Rebuild wenn Threshold erreicht.
        
        Args:
            num_chunks: Anzahl der hinzugefügten Chunks
        
        Returns:
            True wenn Auto-Rebuild getriggert wurde, False sonst
        """
        self.stats['chunks_since_rebuild'] += num_chunks
        
        logger.debug(f"📝 {num_chunks} chunk(s) added. Total since rebuild: {self.stats['chunks_since_rebuild']}")
        
        # Prüfe Threshold
        if self.stats['chunks_since_rebuild'] >= self.rebuild_threshold:
            logger.info(f"🔨 Auto-Rebuild triggered: {self.stats['chunks_since_rebuild']} >= {self.rebuild_threshold}")
            self.build_indexes(auto_save=True)
            self.stats['auto_rebuilds'] += 1
            self.stats['chunks_since_rebuild'] = 0  # Reset Counter
            return True
        
        return False
    
    def check_and_rebuild_if_needed(self, force: bool = False) -> bool:
        """
        Prüft ob Rebuild nötig ist und führt ihn ggf. durch.
        
        Rebuild Kriterien:
        1. Index ist stale (DB geändert)
        2. Threshold erreicht (zu viele neue Chunks)
        3. Force-Flag gesetzt
        
        Args:
            force: Erzwingt Rebuild unabhängig von Kriterien
        
        Returns:
            True wenn Rebuild durchgeführt wurde, False sonst
        """
        should_rebuild = False
        reason = ""
        
        if force:
            should_rebuild = True
            reason = "forced"
        elif self._is_stale():
            should_rebuild = True
            reason = "stale"
        elif self.stats['chunks_since_rebuild'] >= self.rebuild_threshold:
            should_rebuild = True
            reason = f"threshold ({self.stats['chunks_since_rebuild']} chunks)"
        
        if should_rebuild:
            logger.info(f"🔨 Auto-Rebuild ({reason})...")
            self.build_indexes(auto_save=True)
            self.stats['auto_rebuilds'] += 1
            self.stats['chunks_since_rebuild'] = 0
            return True
        
        return False
    
    def _calculate_adaptive_confidence(self, k: int, mode: str = 'stepped') -> float:
        """
        Berechnet adaptive Confidence-Schwelle basierend auf Suchtiefe.
        
        Rationale: Bei höherer Suchtiefe (k) erwartet der User höhere Qualität,
                   da er bereit ist, mehr Ergebnisse zu durchsuchen.
        
        Args:
            k: Suchtiefe (Anzahl gewünschter Results)
            mode: 'stepped' (diskrete Stufen, empfohlen),
                  'linear' (gleichmäßiger Anstieg),
                  'exponential' (progressive Kurve)
        
        Returns:
            Adaptive confidence threshold (0.70 - 0.90)
        
        Examples:
            >>> _calculate_adaptive_confidence(3)   # Quick search
            0.70
            >>> _calculate_adaptive_confidence(5)   # Standard search
            0.75
            >>> _calculate_adaptive_confidence(15)  # Deep search
            0.85
            >>> _calculate_adaptive_confidence(20)  # Maximum depth
            0.90
        """
        if mode == 'stepped':
            # Variante 2: Diskrete Stufen (EMPFOHLEN)
            # Klare, vorhersagbare Qualitätsstufen
            if k <= 3:
                return 0.70  # ⚡ Quick Search
            elif k <= 7:
                return 0.75  # 📊 Standard Search
            elif k <= 12:
                return 0.80  # 🎯 Deep Search
            elif k <= 18:
                return 0.85  # 🔬 Very Deep Search
            else:  # k >= 19
                return 0.90  # 🏆 Maximum Depth Search
        
        elif mode == 'linear':
            # Variante 1: Linear Scaling
            # Gleichmäßiger Anstieg von k=3 (0.70) bis k=20 (0.90)
            k_clamped = max(3, min(k, 20))
            confidence = 0.70 + (0.90 - 0.70) * (k_clamped - 3) / 17
            return round(confidence, 2)
        
        elif mode == 'exponential':
            # Variante 3: Exponential Scaling
            # Progressive Kurve: langsam bis k=10, dann stärker
            k_clamped = max(3, min(k, 20))
            factor = ((k_clamped - 3) / 17) ** 1.5  # Power von 1.5 für progressive Kurve
            confidence = 0.70 + (0.90 - 0.70) * factor
            return float(round(confidence, 2))
        
        else:
            # Fallback zu Standard
            logger.warning(f"Unknown adaptive confidence mode: {mode}, using 0.75")
            return 0.75
        
    def _calculate_ef_search(self, k: int) -> int:
        """
        🎯 Berechnet optimales efSearch für gegebenes k.
        
        Rule of Thumb (aus HNSW Paper & Industry-Best-Practices):
        - efSearch sollte mindestens k sein
        - Für besseren Recall: efSearch = 2*k
        - Minimum: 64 (für sehr kleine k)
        - Maximum: 256 (diminishing returns darüber)
        
        Args:
            k: Gewünschte Anzahl Ergebnisse
            
        Returns:
            Optimales efSearch für dieses k
            
        Examples:
            k=5   → efSearch=64 (Minimum)
            k=10  → efSearch=64 (Minimum)
            k=50  → efSearch=100 (2*k)
            k=100 → efSearch=200 (2*k)
            k=200 → efSearch=256 (Maximum)
        """
        # Berechne ideales efSearch: 2*k für guten Recall
        ideal_ef = k * 2
        
        # Clamp zwischen 64 und 256
        ef_search = max(64, min(ideal_ef, 256))
        
        return ef_search
        

if __name__ == "__main__":
    # Test Setup
    logging.basicConfig(level=logging.INFO)
    
    # Pfad zur DB anpassen
    db_path = "rag_store.db"
    
    print("=" * 60)
    print("🚀 FAISS Index Manager - Test")
    print("=" * 60)
    
    # 1. Initialize
    manager = FAISSIndexManager(db_path=db_path, embedding_dim=1024)
    
    # 2. Try to load cached indexes
    if not manager.load_indexes():
        # 3. Build if not cached
        manager.build_indexes(recent_limit=20000)
        manager.save_indexes()
    
    # 4. Test Search
    print("\n📊 Testing Search...")
    conn = sqlite3.connect(db_path)
    
    # Get a random embedding for testing
    test_emb = conn.execute(
        "SELECT embedding FROM chunks WHERE embedding IS NOT NULL LIMIT 1"
    ).fetchone()
    
    if test_emb:
        query_vec = np.frombuffer(test_emb[0], dtype=np.float32)
        
        # Perform search
        chunk_ids, scores, strategy = manager.search(query_vec, k=5)
        
        print(f"\n✅ Search Results:")
        print(f"   Strategy: {strategy}")
        print(f"   Found {len(chunk_ids)} chunks")
        print(f"   Top score: {scores[0]:.4f}")
        
        # Show statistics
        print(f"\n📈 Statistics:")
        stats = manager.get_statistics()
        for key, value in stats.items():
            print(f"   {key}: {value}")
    
    conn.close()
    
    print("\n✅ Test completed!")


def get_faiss_manager(db_path: str, embedding_dim: int = 1024, **kwargs) -> FAISSIndexManager:
    """
    Singleton-Factory für FAISSIndexManager.
    
    Returns immer dieselbe Instanz für denselben db_path.
    Dies verhindert Memory Leaks durch multiple Instanzen.
    
    Args:
        db_path: Pfad zur SQLite Datenbank
        embedding_dim: Dimensionalität der Embeddings
        **kwargs: Weitere Parameter für FAISSIndexManager
    
    Returns:
        Singleton FAISSIndexManager Instanz
    """
    global _faiss_manager_instance
    
    with _faiss_manager_lock:
        if _faiss_manager_instance is None:
            logger.info("🏗️  Creating new singleton FAISSIndexManager instance...")
            _faiss_manager_instance = FAISSIndexManager(db_path, embedding_dim, **kwargs)
        elif _faiss_manager_instance.db_path != db_path:  # type: ignore[unreachable]
            logger.warning(f"⚠️ FAISSIndexManager db_path changed: {_faiss_manager_instance.db_path} → {db_path}")
            # Cleanup alte Instanz -- INLINE to avoid re-entrant lock deadlock
            _cleanup_internal(_faiss_manager_instance)
            _faiss_manager_instance = None
            # Neue Instanz erstellen
            _faiss_manager_instance = FAISSIndexManager(db_path, embedding_dim, **kwargs)
        
        return _faiss_manager_instance


def _cleanup_internal(instance: FAISSIndexManager) -> None:
    """
    Internal cleanup helper -- NO LOCK acquisition.
    
    Must be called from a context that already holds _faiss_manager_lock.
    This prevents the re-entrant deadlock that occurred when
    get_faiss_manager() called cleanup_faiss_manager() while holding the lock.
    """
    logger.info("🧹 Cleaning up FAISSIndexManager (internal)...")
    
    if instance.recent_index is not None:
        del instance.recent_index
    if instance.full_index is not None:
        del instance.full_index
    
    instance.recent_id_map.clear()
    instance.full_id_map.clear()
    
    logger.info("✅ FAISSIndexManager cleanup completed")


def cleanup_faiss_manager() -> None:
    """
    Bereinigt die globale FAISSIndexManager Instanz.
    
    Dies sollte aufgerufen werden wenn:
    - Die Anwendung beendet wird
    - Der db_path sich ändert
    - VRAM freigegeben werden muss
    """
    global _faiss_manager_instance
    
    with _faiss_manager_lock:
        if _faiss_manager_instance is not None:
            _cleanup_internal(_faiss_manager_instance)
            _faiss_manager_instance = None
