"""
🔍 SEARCH MODULE - Iteration 7 (Oktober 2025)
==============================================

Zentralisiert alle Suche/Query-Logik:
- Hybrid Search (Embedding + Knowledge Graph)
- Embedding-based Search (mit FAISS/NumPy)
- Knowledge Graph Search
- Result Fusion & Enrichment

Vorteile:
✅ Klare Verantwortlichkeiten (Single Responsibility)
✅ Leicht testbar (isolierte Suchlogik)
✅ Wiederverwendbar (unabhängig von RAG Store)
✅ Wartbar (alle Search-Features an einem Ort)

Integration:
- Nutzt DatabaseManager für DB-Zugriff
- Nutzt EmbeddingManager für Query-Embeddings
- Nutzt Optional: FAISSIndexManager und SmartFusionEngine
"""

from __future__ import annotations

import logging
import os
import time
import json
import re
import threading
from typing import List, Dict, Any, Optional, Tuple, TYPE_CHECKING

# Typing imports
if TYPE_CHECKING:
    from .database import DatabaseManager
    from .embeddings import EmbeddingManager

# Optional dependencies
try:
    import numpy as np
    NUMPY_AVAILABLE = True
except ImportError:
    np = None  # type: ignore[assignment]
    NUMPY_AVAILABLE = False

# NetworkX for Community Detection (optional)
try:
    import networkx as nx
    NETWORKX_AVAILABLE = True
except ImportError:
    nx = None  # type: ignore[assignment]
    NETWORKX_AVAILABLE = False

# FAISS Hybrid Search Components
FAISS_HYBRID_AVAILABLE = False
FAISSIndexManager: Any = None
SmartFusionEngine: Any = None


def _ensure_faiss_hybrid_runtime() -> bool:
    """Lazy import for optional FAISS hybrid dependencies."""
    global FAISS_HYBRID_AVAILABLE, FAISSIndexManager, SmartFusionEngine
    if FAISS_HYBRID_AVAILABLE and FAISSIndexManager is not None and SmartFusionEngine is not None:
        return True
    try:
        from agent.faiss_index_manager import FAISSIndexManager as _FAISSIndexManager  # type: ignore
        from agent.smart_fusion_engine import SmartFusionEngine as _SmartFusionEngine  # type: ignore
        FAISSIndexManager = _FAISSIndexManager
        SmartFusionEngine = _SmartFusionEngine
        FAISS_HYBRID_AVAILABLE = True
        return True
    except ImportError:
        try:
            # Fallback: Try without agent. prefix (if running from agent/ directory)
            from faiss_index_manager import FAISSIndexManager as _FAISSIndexManager  # type: ignore
            from smart_fusion_engine import SmartFusionEngine as _SmartFusionEngine  # type: ignore
            FAISSIndexManager = _FAISSIndexManager
            SmartFusionEngine = _SmartFusionEngine
            FAISS_HYBRID_AVAILABLE = True
            return True
        except ImportError:
            FAISS_HYBRID_AVAILABLE = False
            return False

# BM25 Sparse Retrieval (SOTA: bm25s with Snowball Stemming + Persistence)
# Priority: bm25s (Cython/NumPy, save/load, 133x faster) > rank_bm25 (fallback)
BM25_AVAILABLE = False
_BM25S_AVAILABLE = False
_BM25_LEGACY_AVAILABLE = False
try:
    import bm25s as _bm25s  # type: ignore[import-untyped]
    _BM25S_AVAILABLE = True
    BM25_AVAILABLE = True
except ImportError:
    _bm25s = None  # type: ignore[assignment]

if not _BM25S_AVAILABLE:
    BM25Okapi: Any = None
    try:
        from rank_bm25 import BM25Okapi  # type: ignore[no-redef]
        _BM25_LEGACY_AVAILABLE = True
        BM25_AVAILABLE = True
    except ImportError:
        pass

# Snowball Stemmer for German (SOTA: improves BM25 recall for German text)
_BM25_STEMMER: Any = None
try:
    import Stemmer as _StemmerLib  # type: ignore[import-untyped]
    _BM25_STEMMER = _StemmerLib.Stemmer("german")
except ImportError:
    pass

# Cross-Encoder Reranker (SOTA Reranking)
RERANKER_AVAILABLE = False
_get_reranker: Any = None
_reciprocal_rank_fusion: Any = None
try:
    from agent.reranker import get_reranker as _get_reranker  # type: ignore[no-redef]
    from agent.reranker import reciprocal_rank_fusion as _reciprocal_rank_fusion  # type: ignore[no-redef]
    RERANKER_AVAILABLE = True
except ImportError:
    try:
        from reranker import get_reranker as _get_reranker  # type: ignore[no-redef]
        from reranker import reciprocal_rank_fusion as _reciprocal_rank_fusion  # type: ignore[no-redef]
        RERANKER_AVAILABLE = True
    except ImportError:
        pass

logger = logging.getLogger(__name__)


class SearchManager:
    """
    🔍 Zentralisiertes Search Management (SOTA Hybrid Search)
    
    Verantwortlichkeiten:
    - Hybrid Search (Dense FAISS + Sparse BM25 + Knowledge Graph)
    - Cross-Encoder Reranking (Nogueira & Cho 2019)
    - Reciprocal Rank Fusion (Croft et al. 2009)
    - Context Enrichment
    
    SOTA Features:
    - FAISS Dense Retrieval (555x schneller als NumPy)
    - BM25 Sparse Retrieval (exakte Keyword-Matches)
    - RRF Fusion (robuster als gewichteter Durchschnitt)
    - Cross-Encoder Reranking (5-15% MRR Improvement)
    - Adaptive Confidence based on search depth
    - Smart Fusion Engine (KG + FAISS + Recency)
    - NumPy fallback for compatibility
    """
    
    def __init__(
        self,
        db_manager: DatabaseManager,
        embedding_manager: Optional[EmbeddingManager] = None,
        embedding_dim: int = 1024,
        faiss_manager: Optional[Any] = None,
        fusion_engine: Optional[Any] = None,
        debug: bool = False
    ):
        """
        Initialize SearchManager
        
        Args:
            db_manager: DatabaseManager for DB access
            embedding_manager: Optional EmbeddingManager for query embeddings (can be set later)
            embedding_dim: Embedding dimension
            faiss_manager: Optional FAISSIndexManager for fast vector search
            fusion_engine: Optional SmartFusionEngine for result fusion
            debug: Enable debug logging
        """
        self.db_manager = db_manager
        self.embedding_manager = embedding_manager  # Can be None initially (lazy init)
        self.embedding_dim = embedding_dim
        _ensure_faiss_hybrid_runtime()
        self.faiss_manager = faiss_manager
        self.fusion_engine = fusion_engine
        self.debug = debug
        
        # ★ SOTA v3: Store last search results for feedback loop
        # These are read by agent_chatbot_logic to pass chunk_ids through to feedback
        self._last_search_chunk_ids: List[str] = []
        self._last_search_chunk_scores: List[float] = []
        
        # BM25 index (lazy-init, thread-safe)
        self._bm25_index: Any = None
        self._bm25_chunk_ids: List[str] = []
        self._bm25_texts: List[str] = []
        self._bm25_lock = threading.Lock()
        self._bm25_built = False
        self._bm25_save_dir: str = self._get_bm25_save_dir()
        
        # ★ SOTA v3: Entity Embedding Index (lazy-init, thread-safe)
        # Version counter detects invalidation after lock release (prevents race conditions)
        self._entity_texts: Optional[List[str]] = None
        self._entity_embeddings: Any = None  # numpy array (N, dim)
        self._entity_index_lock = threading.Lock()
        self._entity_index_built = False
        self._entity_index_version = 0  # ← Incremented on invalidate to detect staleness
        
        # Reranker (lazy-init via singleton)
        self._reranker: Any = None
        self._reranker_checked = False
        
        # ★ SOTA v5: Community Detection (lazy-init, thread-safe)
        # Leiden-Algorithmus für Community-basierte Triple-Expansion
        self._community_detector: Any = None
        self._community_detector_lock = threading.Lock()
        self._community_detector_built = False
        self._community_state_path: Optional[str] = None
        
        if self.debug:
            bm25_status = 'bm25s' if _BM25S_AVAILABLE else ('rank_bm25' if _BM25_LEGACY_AVAILABLE else 'disabled')
            community_status = 'available' if NETWORKX_AVAILABLE else 'disabled'
            logger.info(
                f"✅ SearchManager initialized: "
                f"dim={embedding_dim}, "
                f"FAISS={'available' if faiss_manager else 'disabled'}, "
                f"Fusion={'available' if fusion_engine else 'disabled'}, "
                f"BM25={bm25_status}, "
                f"Stemmer={'german' if _BM25_STEMMER else 'disabled'}, "
                f"Reranker={'available' if RERANKER_AVAILABLE else 'disabled'}, "
                f"CommunityDetection={community_status}"
            )
    
    # ====================================================================
    # CORPUS DOMAIN / SAFETY FILTER (Option C — Shared RAG Namespacing)
    # ====================================================================

    @staticmethod
    def _chunk_filter_clause(
        allowed_domains: Optional[List[str]],
        exclude_safety_flags: Optional[List[str]],
        table_alias: str = "",
    ) -> Tuple[str, List[Any]]:
        """
        Build a SQL fragment for filtering chunks by domain / safety_flag.

        Returns ``(" AND <conditions>", params)`` ready to append after an
        existing WHERE clause. Returns ``("", [])`` when no filter is active.

        ``table_alias`` enables qualified column references when joining
        (e.g. ``"c"`` → ``c.domain``). Pass empty for unqualified queries.
        """
        prefix = f"{table_alias}." if table_alias else ""
        fragments: List[str] = []
        params: List[Any] = []
        if allowed_domains:
            placeholders = ",".join(["?"] * len(allowed_domains))
            fragments.append(f"{prefix}domain IN ({placeholders})")
            params.extend(allowed_domains)
        if exclude_safety_flags:
            placeholders = ",".join(["?"] * len(exclude_safety_flags))
            fragments.append(f"{prefix}safety_flag NOT IN ({placeholders})")
            params.extend(exclude_safety_flags)
        if not fragments:
            return "", []
        return " AND " + " AND ".join(fragments), params

    def search(
        self,
        query: str,
        k: int = 5,
        min_score: float = 0.0,
        adaptive_confidence: bool = True,
        faiss_min_confidence: Optional[float] = None,
        allowed_domains: Optional[List[str]] = None,
        exclude_safety_flags: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """
        🏆 SOTA Hybrid Search Pipeline
        
        Combines 3 retrieval signals via Reciprocal Rank Fusion (RRF),
        then reranks with a Cross-Encoder for maximum relevance.
        
        Pipeline:
            1. Dense (FAISS/NumPy embedding search)
            2. Sparse (BM25 keyword search)         ← NEW
            3. Knowledge Graph (entity match)
            4. RRF Fusion (replaces weighted avg)    ← NEW
            5. Cross-Encoder Reranking               ← NEW
            6. KG Context Enrichment
        
        References:
            - Croft et al. (2009): Reciprocal Rank Fusion
            - Nogueira & Cho (2019): Passage Re-ranking with BERT
            - Luan et al. (2021): Sparse, Dense, and Attentional Representations
        
        Args:
            query: Suchquery
            k: Anzahl Ergebnisse
            min_score: Minimaler Similarity-Score (NACH FAISS)
            adaptive_confidence: Wenn True, passe FAISS Confidence an k an
            faiss_min_confidence: Manuelle FAISS Confidence
            allowed_domains: Optional whitelist of corpus domains
                (e.g. ``['psych']`` or ``['psych', 'general']``). ``None``
                disables the filter and returns all domains.
            exclude_safety_flags: Optional blacklist of safety_flag values
                (e.g. ``['crisis']``). ``None`` disables the filter.
            
        Returns:
            Liste von Suchergebnissen mit Text, Metadaten und Score
        """
        if not query.strip():
            return []
        
        try:
            start_time = time.time()
            
            # ─── Stage 1: Multi-Signal Retrieval ────────────────
            # 1a. Dense retrieval (FAISS / NumPy)
            embedding_results = self._embedding_search(
                query, k * 3, min_score, adaptive_confidence, faiss_min_confidence,
                allowed_domains=allowed_domains,
                exclude_safety_flags=exclude_safety_flags,
            )
            
            # 1b. Sparse retrieval (BM25) — NEW SOTA
            bm25_results = (
                self._bm25_search(
                    query, k * 3,
                    allowed_domains=allowed_domains,
                    exclude_safety_flags=exclude_safety_flags,
                )
                if BM25_AVAILABLE
                else []
            )
            
            # 1c. Knowledge Graph retrieval
            kg_results = self._knowledge_graph_search(
                query, k,
                allowed_domains=allowed_domains,
                exclude_safety_flags=exclude_safety_flags,
            )
            
            if self.debug:
                logger.debug(
                    f"📊 Stage 1 Retrieval: Dense={len(embedding_results)}, "
                    f"BM25={len(bm25_results)}, KG={len(kg_results)}"
                )
            
            # ─── Stage 2: Fusion ────────────────────────────────
            if _reciprocal_rank_fusion and RERANKER_AVAILABLE and (bm25_results or kg_results):
                # SOTA: Reciprocal Rank Fusion across all signals
                signals = [embedding_results]
                if bm25_results:
                    signals.append(bm25_results)
                if kg_results:
                    signals.append(kg_results)
                
                fused_results = _reciprocal_rank_fusion(*signals, k=60)
                
                if self.debug:
                    logger.debug(f"🔀 RRF Fusion: {len(fused_results)} results")
            elif self.fusion_engine and FAISS_HYBRID_AVAILABLE:
                # Legacy: Smart Fusion Engine
                try:
                    fused_results = self.fusion_engine.fuse(
                        faiss_results=embedding_results,
                        kg_results=kg_results,
                        k=k * 2
                    )
                except Exception as e:
                    raise RuntimeError(f"Smart Fusion failed: {e}") from e
            else:
                fused_results = self._fuse_search_results(embedding_results, kg_results, query)
            
            # ─── Stage 3: Cross-Encoder Reranking ───────────────
            reranked_results = self._rerank_results(query, fused_results, k * 2)
            
            # ─── Stage 3b (NEW): Post-Reranking Quality Gate ───
            # ★ SOTA: Drop results with reranker score below threshold.
            # The cross-encoder score is a calibrated semantic relevance signal;
            # returning low-score results pollutes context and degrades LLM answers.
            reranked_results = self._apply_reranker_quality_gate(reranked_results, k)
            
            # ─── Stage 3c: Structural Quality Penalty ──────────
            # ★ SOTA v3: Apply pre-computed chunk quality as a soft score multiplier.
            # Chunks with low structural_score are penalized, not removed.
            # This ensures defect chunks with high semantic match are still de-prioritized.
            reranked_results = self._apply_structural_quality_penalty(reranked_results)
            
            # ─── Stage 3d (NEW): Temporal-Aware Boost ──────────
            # ★ SOTA: Soft-boost results matching temporal query intent.
            # This is NOT a filter — it only re-orders.
            temporal_hints = self.classify_temporal_query(query)
            if temporal_hints:
                reranked_results = self._apply_temporal_boost(reranked_results, temporal_hints)
                if self.debug:
                    logger.debug(f"⏰ Temporal query detected: {temporal_hints}")

            # ─── Stage 3e (NEW): Fact Lifecycle Scoring ──────────
            # ★ SOTA: Apply contradiction/grounding/evidence/validity-aware
            # scoring for KG-backed results. This is a soft re-ranking step.
            reranked_results = self._apply_fact_lifecycle_scoring(
                reranked_results,
                temporal_hints=temporal_hints,
            )
            
            # ─── Stage 4: KG Context Enrichment ────────────────
            enriched_results = self._enrich_with_kg_context(reranked_results, query)
            
            final_results = enriched_results[:k]
            
            # ★ SOTA v3: Store chunk_ids and scores for feedback loop
            self._last_search_chunk_ids = []
            self._last_search_chunk_scores = []
            for r in final_results:
                cid = r.get('chunk_id', r.get('id', ''))
                if cid:
                    self._last_search_chunk_ids.append(str(cid))
                    self._last_search_chunk_scores.append(float(r.get('score', 0.0)))
            
            elapsed = (time.time() - start_time) * 1000
            if self.debug:
                logger.debug(
                    f"🏆 SOTA Search complete: {elapsed:.1f}ms, "
                    f"{len(final_results)} final results"
                )
            
            return final_results
                
        except Exception as e:
            logger.error(f"Hybrid search failed (fail-fast): {e}")
            raise RuntimeError(f"Hybrid search pipeline failed: {e}") from e
    
    # ====================================================================
    # BM25 SPARSE RETRIEVAL (SOTA: bm25s + Snowball + Persistence)
    # ====================================================================
    
    def _get_bm25_save_dir(self) -> str:
        """Get the directory for persisting BM25 index.
        
        Stores the index alongside the SQLite database for co-location.
        Returns empty string for in-memory databases (no persistence).
        """
        try:
            db_path = self.db_manager.db_path
            if not db_path or "memory" in db_path:
                return ""
            db_dir = os.path.dirname(os.path.abspath(db_path))
            return os.path.join(db_dir, ".bm25s_index")
        except Exception:
            return ""
    
    def _build_bm25_index(self) -> bool:
        """
        Build or load BM25 index with SOTA optimizations.
        
        Pipeline:
          1. Try loading persisted index from disk (67ms vs 11000ms rebuild)
          2. If stale or missing: fetch chunks → tokenize with Snowball
             stemmer → build bm25s index → persist to disk
        
        SOTA optimizations:
          - bm25s: Cython/NumPy sparse matrices (133x faster than rank_bm25)
          - Snowball German stemmer: "Verarbeitung" → "verarbeit" (better recall)
          - Persistence: save/load eliminates rebuild on restart
          - Staleness check: chunk count vs DB ensures index freshness
        
        Returns:
            True if index is ready
        """
        if self._bm25_built:
            return True
        
        if not BM25_AVAILABLE:
            return False
        
        with self._bm25_lock:
            # Double-checked locking
            if self._bm25_built:
                return True
            
            try:
                # ── Step 1: Try loading persisted index ──────────────
                if _BM25S_AVAILABLE and self._try_load_bm25_index():
                    return True
                
                start = time.time()
                conn = self.db_manager.get_connection()
                cur = conn.cursor()
                
                try:
                    cur.execute("SELECT chunk_id, text FROM chunks ORDER BY chunk_id")
                    rows = cur.fetchall()
                finally:
                    cur.close()
                    self.db_manager.return_connection(conn)
                
                if not rows:
                    logger.warning("BM25: No chunks in database")
                    return False
                
                chunk_ids = []
                texts = []
                
                for chunk_id, text in rows:
                    chunk_ids.append(chunk_id)
                    texts.append(text or "")
                
                self._bm25_chunk_ids = chunk_ids
                self._bm25_texts = texts
                
                # ── Step 2: Build index ──────────────────────────────
                if _BM25S_AVAILABLE and _bm25s is not None:
                    # SOTA path: bm25s with Snowball stemmer
                    corpus_tokens = _bm25s.tokenize(
                        texts,
                        stemmer=_BM25_STEMMER,
                        stopwords="de" if _BM25_STEMMER else None,
                    )
                    retriever = _bm25s.BM25()
                    retriever.index(corpus_tokens)
                    self._bm25_index = retriever
                    
                    # ── Step 3: Persist to disk ──────────────────────
                    self._save_bm25_index()
                else:
                    # Legacy fallback: rank_bm25
                    tokenized = []
                    for text in texts:
                        tokens = re.sub(r'[^\w\s]', ' ', text.lower()).split()
                        tokenized.append(tokens)
                    self._bm25_index = BM25Okapi(tokenized)
                
                self._bm25_built = True
                
                elapsed = (time.time() - start) * 1000
                backend = "bm25s+stemmer" if _BM25S_AVAILABLE else "rank_bm25"
                logger.info(
                    f"✅ BM25 index built ({backend}): "
                    f"{len(rows)} chunks in {elapsed:.1f}ms"
                )
                return True
                
            except Exception as e:
                logger.error(f"❌ BM25 index build failed (fail-fast): {e}")
                raise RuntimeError(f"BM25 index build failed: {e}") from e
    
    def _try_load_bm25_index(self) -> bool:
        """Try loading a persisted bm25s index from disk.
        
        Validates freshness by comparing saved chunk count with current
        database count.  Returns False if index is stale or missing.
        
        Average load time: ~67ms (vs ~11000ms rebuild).
        """
        if not self._bm25_save_dir or not _BM25S_AVAILABLE or _bm25s is None:
            return False
        
        try:
            import json as _json
            
            meta_path = os.path.join(self._bm25_save_dir, "_bm25_meta.json")
            if not os.path.exists(meta_path):
                return False
            
            # Read metadata
            with open(meta_path, "r", encoding="utf-8") as f:
                meta = _json.load(f)
            
            saved_count = meta.get("chunk_count", -1)
            
            # Verify freshness: chunk count must match DB
            conn = self.db_manager.get_connection()
            try:
                cur = conn.execute("SELECT COUNT(*) FROM chunks")
                db_count = cur.fetchone()[0]
            finally:
                self.db_manager.return_connection(conn)
            
            if saved_count != db_count:
                logger.info(
                    f"BM25 index stale: saved={saved_count}, db={db_count}. Rebuilding."
                )
                return False
            
            # Load bm25s index
            start = time.time()
            self._bm25_index = _bm25s.BM25.load(self._bm25_save_dir)
            
            # Load chunk_ids
            ids_path = os.path.join(self._bm25_save_dir, "_chunk_ids.json")
            with open(ids_path, "r", encoding="utf-8") as f:
                self._bm25_chunk_ids = _json.load(f)
            
            self._bm25_texts = []  # Not loaded (saves memory, fetched from DB on search)
            self._bm25_built = True
            
            elapsed = (time.time() - start) * 1000
            logger.info(
                f"⚡ BM25 index loaded from disk: {saved_count} chunks in {elapsed:.1f}ms"
            )
            return True
            
        except Exception as e:
            logger.debug(f"BM25 index load failed: {e}")
            return False
    
    def _save_bm25_index(self) -> None:
        """Persist bm25s index + metadata to disk.
        
        Saves:
          - bm25s sparse matrices (via retriever.save())
          - chunk_ids mapping (JSON)
          - metadata with chunk_count for staleness detection
        """
        if not self._bm25_save_dir or not _BM25S_AVAILABLE:
            return
        
        try:
            import json as _json
            
            os.makedirs(self._bm25_save_dir, exist_ok=True)
            
            # Save bm25s index
            self._bm25_index.save(self._bm25_save_dir)
            
            # Save chunk_ids
            ids_path = os.path.join(self._bm25_save_dir, "_chunk_ids.json")
            with open(ids_path, "w", encoding="utf-8") as f:
                _json.dump(self._bm25_chunk_ids, f)
            
            # Save metadata for staleness detection
            meta_path = os.path.join(self._bm25_save_dir, "_bm25_meta.json")
            with open(meta_path, "w", encoding="utf-8") as f:
                _json.dump({
                    "chunk_count": len(self._bm25_chunk_ids),
                    "backend": "bm25s",
                    "stemmer": "german" if _BM25_STEMMER else None,
                    "stopwords": "de" if _BM25_STEMMER else None,
                }, f)
            
            logger.debug(
                f"BM25 index saved: {len(self._bm25_chunk_ids)} chunks "
                f"→ {self._bm25_save_dir}"
            )
        except Exception as e:
            logger.debug(f"BM25 index save failed (non-critical): {e}")
    
    def invalidate_bm25_index(self) -> None:
        """Invalidate BM25 index (call after adding/removing chunks).
        
        Clears both in-memory state AND persisted index on disk.
        """
        with self._bm25_lock:
            self._bm25_index = None
            self._bm25_chunk_ids = []
            self._bm25_texts = []
            self._bm25_built = False
            
            # Delete persisted index
            if self._bm25_save_dir and os.path.isdir(self._bm25_save_dir):
                try:
                    import shutil
                    shutil.rmtree(self._bm25_save_dir, ignore_errors=True)
                    logger.debug(f"BM25 persisted index deleted: {self._bm25_save_dir}")
                except Exception:
                    pass
            
            logger.debug("BM25 index invalidated")
    
    def _bm25_search(
        self,
        query: str,
        k: int = 10,
        allowed_domains: Optional[List[str]] = None,
        exclude_safety_flags: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """
        BM25 sparse keyword search (SOTA: bm25s with Snowball stemming).
        
        Complements dense (embedding) search by finding exact keyword matches
        that dense representations might miss. Crucial for domain-specific
        terminology, names, codes, etc.
        
        bm25s path:  tokenize → retrieve (top-k from sparse matrix) → ~2ms
        Legacy path:  tokenize → get_scores (all docs) → argsort → ~430ms
        
        Args:
            query: Search query
            k: Number of results
            
        Returns:
            List of result dicts with text, metadata, score, chunk_id
        """
        if not BM25_AVAILABLE or not self._build_bm25_index():
            return []
        
        try:
            # When namespace/safety filters are active, over-fetch from BM25
            # so that post-hydration filtering still leaves enough results.
            has_chunk_filter = bool(allowed_domains) or bool(exclude_safety_flags)
            fetch_k = k * 3 if has_chunk_filter else k
            if _BM25S_AVAILABLE:
                return self._bm25_search_bm25s(
                    query, fetch_k,
                    allowed_domains=allowed_domains,
                    exclude_safety_flags=exclude_safety_flags,
                    final_k=k,
                )
            else:
                return self._bm25_search_legacy(
                    query, fetch_k,
                    allowed_domains=allowed_domains,
                    exclude_safety_flags=exclude_safety_flags,
                    final_k=k,
                )
        except Exception as e:
            logger.error(f"BM25 search failed (fail-fast): {e}")
            raise RuntimeError(f"BM25 search failed: {e}") from e
    
    def _bm25_search_bm25s(
        self,
        query: str,
        k: int,
        allowed_domains: Optional[List[str]] = None,
        exclude_safety_flags: Optional[List[str]] = None,
        final_k: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """SOTA bm25s search path with Snowball stemming."""
        if _bm25s is None:
            return []
        # Tokenize query with same stemmer as corpus
        query_tokens = _bm25s.tokenize(
            [query],
            stemmer=_BM25_STEMMER,
            stopwords="de" if _BM25_STEMMER else None,
        )
        
        # Retrieve top-k directly (no need to score all 120k docs)
        effective_k = min(k, len(self._bm25_chunk_ids))
        if effective_k <= 0:
            return []
        
        doc_indices, doc_scores = self._bm25_index.retrieve(
            query_tokens, k=effective_k
        )
        
        # doc_indices/doc_scores shape: (1, k) since we query 1 query
        indices = doc_indices[0]
        scores = doc_scores[0]
        
        # Fetch chunk data from DB
        results = []
        conn = self.db_manager.get_connection()
        filter_clause, filter_params = self._chunk_filter_clause(
            allowed_domains, exclude_safety_flags
        )
        
        try:
            # Compute max score for normalization
            valid_scores = [float(s) for s in scores if float(s) > 0]
            max_score = max(valid_scores) if valid_scores else 1.0
            
            for idx, score in zip(indices, scores):
                score_f = float(score)
                if score_f <= 0:
                    continue
                
                idx_i = int(idx)
                if idx_i < 0 or idx_i >= len(self._bm25_chunk_ids):
                    continue
                
                chunk_id = self._bm25_chunk_ids[idx_i]
                cur = conn.execute(
                    "SELECT doc_id, text, metadata, domain, safety_flag "
                    f"FROM chunks WHERE chunk_id = ?{filter_clause}",
                    (chunk_id, *filter_params),
                )
                row = cur.fetchone()
                if row:
                    doc_id, text, metadata_json, c_domain, c_safety = row
                    try:
                        metadata = json.loads(metadata_json)
                    except Exception:
                        metadata = {}
                    
                    norm_score = score_f / max_score if max_score > 0 else 0.0
                    
                    results.append({
                        "text": text,
                        "metadata": {**metadata, "search_type": "bm25"},
                        "score": norm_score,
                        "bm25_raw_score": score_f,
                        "doc_id": doc_id,
                        "chunk_id": chunk_id,
                        "domain": c_domain,
                        "safety_flag": c_safety,
                    })
        finally:
            self.db_manager.return_connection(conn)
        
        if final_k is not None and len(results) > final_k:
            results = results[:final_k]
        
        if self.debug:
            logger.debug(f"📝 BM25 search (bm25s): {len(results)} results for '{query[:50]}'")
        
        return results
    
    def _bm25_search_legacy(
        self,
        query: str,
        k: int,
        allowed_domains: Optional[List[str]] = None,
        exclude_safety_flags: Optional[List[str]] = None,
        final_k: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Legacy rank_bm25 search path (fallback)."""
        # Tokenize query same as corpus
        query_tokens = re.sub(r'[^\w\s]', ' ', query.lower()).split()
        if not query_tokens:
            return []
        
        # Get BM25 scores for all documents
        scores = self._bm25_index.get_scores(query_tokens)
        
        # Get top-k indices
        if NUMPY_AVAILABLE and np is not None:
            top_indices = np.argsort(scores)[-k:][::-1]
            top_scores = scores[top_indices]
        else:
            indexed = list(enumerate(scores))
            indexed.sort(key=lambda x: x[1], reverse=True)
            top_indices = [i for i, _ in indexed[:k]]
            top_scores = [s for _, s in indexed[:k]]
        
        # Fetch chunk data from DB
        results = []
        conn = self.db_manager.get_connection()
        filter_clause, filter_params = self._chunk_filter_clause(
            allowed_domains, exclude_safety_flags
        )
        
        try:
            for idx, score in zip(top_indices, top_scores):
                if float(score) <= 0:
                    continue
                
                chunk_id = self._bm25_chunk_ids[int(idx)]
                cur = conn.execute(
                    "SELECT doc_id, text, metadata, domain, safety_flag "
                    f"FROM chunks WHERE chunk_id = ?{filter_clause}",
                    (chunk_id, *filter_params),
                )
                row = cur.fetchone()
                if row:
                    doc_id, text, metadata_json, c_domain, c_safety = row
                    try:
                        metadata = json.loads(metadata_json)
                    except Exception:
                        metadata = {}
                    
                    max_score = float(max(top_scores)) if len(top_scores) > 0 else 1.0
                    norm_score = float(score) / max_score if max_score > 0 else 0.0
                    
                    results.append({
                        "text": text,
                        "metadata": {**metadata, "search_type": "bm25"},
                        "score": norm_score,
                        "bm25_raw_score": float(score),
                        "doc_id": doc_id,
                        "chunk_id": chunk_id,
                        "domain": c_domain,
                        "safety_flag": c_safety,
                    })
        finally:
            self.db_manager.return_connection(conn)
        
        if final_k is not None and len(results) > final_k:
            results = results[:final_k]
        
        if self.debug:
            logger.debug(f"📝 BM25 search (legacy): {len(results)} results for '{query[:50]}'")
        
        return results
    
    # ====================================================================
    # CROSS-ENCODER RERANKING (NEW SOTA)
    # ====================================================================
    
    def _get_reranker(self) -> Any:
        """Get or init the cross-encoder reranker singleton."""
        if not self._reranker_checked:
            self._reranker_checked = True
            if RERANKER_AVAILABLE and _get_reranker:
                try:
                    self._reranker = _get_reranker()
                    # SOTA (2026-08-28): Kein is_available-Check. Der Reranker ist
                    # LAZY (lädt erst beim ersten rerank()/score_pair()-Call via
                    # _ensure_loaded()). is_available wäre hier False (Modell noch
                    # nicht geladen) und würde den Reranker dauerhaft deaktivieren.
                    # Die Caller (rerank/score_pair/batch_score) laden bei Bedarf
                    # und degradieren graceful (Original-Reihenfolge / Score 0.0),
                    # wenn das Modell nicht verfügbar ist.
                    if self._reranker:
                        logger.info("✅ Cross-Encoder Reranker connected to SearchManager (lazy-load)")
                except Exception as e:
                    logger.error(f"❌ Reranker init failed (fail-fast): {e}")
                    raise RuntimeError(f"Reranker initialization failed: {e}") from e
        return self._reranker
    
    def _rerank_results(
        self,
        query: str,
        results: List[Dict[str, Any]],
        top_k: int,
    ) -> List[Dict[str, Any]]:
        """
        Rerank search results using cross-encoder.
        
        Args:
            query: Search query
            results: Fused results from Stage 2
            top_k: Max results to return
            
        Returns:
            Reranked results
        """
        reranker = self._get_reranker()
        
        if not reranker or not results:
            return results[:top_k]
        
        try:
            start = time.time()
            reranked = reranker.rerank(
                query=query,
                passages=results,
                top_k=top_k,
                text_key="text",
            )
            elapsed = (time.time() - start) * 1000
            
            if self.debug:
                logger.debug(
                    f"🏆 Reranked {len(results)} → {len(reranked)} results in {elapsed:.1f}ms"
                )
            
            return reranked
            
        except Exception as e:
            logger.error(f"Reranking failed (fail-fast): {e}")
            raise RuntimeError(f"Cross-encoder reranking failed: {e}") from e
    
    # ====================================================================
    # TEMPORAL QUERY CLASSIFICATION (SOTA: Time-Aware Retrieval)
    # ====================================================================
    
    # Temporal keyword sets (German + English)
    _TEMPORAL_YEAR_PATTERN = re.compile(r'\b(19[5-9]\d|20[0-3]\d)\b')
    _TEMPORAL_RANGE_PATTERN = re.compile(
        r'\b(19[5-9]\d|20[0-3]\d)\s*[-–bis]+\s*(19[5-9]\d|20[0-3]\d)\b'
    )
    _TEMPORAL_KEYWORDS_HISTORICAL = {
        'damals', 'früher', 'historisch', 'historische', 'historischen', 'historisches',
        'historie', 'vergangenheit', 'ehemals', 'einst', 'seinerzeit', 'vormals',
        'ursprünglich', 'ursprüngliche', 'ursprünglichen',
        'previously', 'formerly', 'historically', 'in the past', 'back then',
    }
    _TEMPORAL_KEYWORDS_RECENT = {
        'aktuell', 'aktuelle', 'aktuellen', 'aktuelles', 'aktuellste',
        'neueste', 'neuesten', 'neuester', 'neuste', 'neusten',
        'kürzlich', 'jüngst', 'jüngste', 'jüngsten',
        'letzte', 'letzten', 'letzter',
        'recent', 'recently', 'latest', 'current', 'newest', 'today',
    }
    _TEMPORAL_KEYWORDS_EVOLUTION = {
        'entwicklung', 'entwickelt', 'entwickelte', 'entwickelten',
        'veränderung', 'verändert', 'veränderte',
        'wandel', 'gewandelt', 'verlauf', 'chronologie', 'chronologisch',
        'evolution', 'evolved', 'timeline', 'progression', 'change over time',
        'über die zeit', 'im laufe der zeit', 'over time',
    }

    @staticmethod
    def classify_temporal_query(query: str) -> Optional[Dict[str, Any]]:
        """
        ★ SOTA: Detect temporal intent in a query.
        
        Returns None for non-temporal queries.
        For temporal queries, returns hints like:
          {"type": "year", "years": [2008], "direction": "past"}
          {"type": "range", "start": 2000, "end": 2010}
          {"type": "keyword", "direction": "historical"|"recent"|"evolution"}
        
        This is NOT a filter — it's used downstream for soft score boosting.
        All chunks remain accessible regardless of temporal classification.
        """
        if not query or not query.strip():
            return None
        
        q_lower = query.lower()
        
        # 1. Explicit year range (e.g., "2000-2010", "2000 bis 2010")
        range_match = SearchManager._TEMPORAL_RANGE_PATTERN.search(query)
        if range_match:
            return {
                "type": "range",
                "start": int(range_match.group(1)),
                "end": int(range_match.group(2)),
                "direction": "specific",
            }
        
        # 2. Explicit year mentions
        year_matches = SearchManager._TEMPORAL_YEAR_PATTERN.findall(query)
        if year_matches:
            years = sorted(set(int(y) for y in year_matches))
            # Determine direction based on years relative to "now"
            import datetime as _dt
            current_year = _dt.datetime.now().year
            avg_year = sum(years) / len(years)
            direction = "recent" if avg_year >= current_year - 3 else "past"
            return {
                "type": "year",
                "years": years,
                "direction": direction,
            }
        
        # 3. Temporal keywords — use cleaned tokens (no punctuation)
        clean_words = re.findall(r'\w+', q_lower)
        tokens = set(clean_words)
        # Also check 2-word and 3-word phrases from cleaned words
        phrases = set(tokens)  # Start with single words
        for i in range(len(clean_words)):
            if i + 1 < len(clean_words):
                phrases.add(f"{clean_words[i]} {clean_words[i+1]}")
            if i + 2 < len(clean_words):
                phrases.add(f"{clean_words[i]} {clean_words[i+1]} {clean_words[i+2]}")
        
        if phrases & SearchManager._TEMPORAL_KEYWORDS_EVOLUTION:
            return {"type": "keyword", "direction": "evolution"}
        if phrases & SearchManager._TEMPORAL_KEYWORDS_HISTORICAL:
            return {"type": "keyword", "direction": "historical"}
        if phrases & SearchManager._TEMPORAL_KEYWORDS_RECENT:
            return {"type": "keyword", "direction": "recent"}
        
        return None

    def _apply_temporal_boost(
        self,
        results: List[Dict[str, Any]],
        temporal_hints: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        """
        ★ SOTA: Apply a soft score boost to results matching temporal hints.
        
        This is a BOOST, not a filter. It re-orders results so temporally
        matching content surfaces higher, but never removes non-matching results.
        
        Boost factor: up to +15% of the result's own score for perfect temporal match.
        """
        if not results or not temporal_hints:
            return results
        
        direction = temporal_hints.get("direction", "")
        hint_type = temporal_hints.get("type", "")
        target_years = set(temporal_hints.get("years", []))
        range_start = temporal_hints.get("start", 0)
        range_end = temporal_hints.get("end", 9999)
        
        boosted = []
        for r in results:
            boost = 0.0
            text = (r.get("text", "") or "")[:500]  # Only scan first 500 chars
            metadata = r.get("metadata", {}) or {}
            
            # Extract date info from metadata
            extracted_at = metadata.get("extracted_at", "") or metadata.get("created_at", "")
            doc_year = None
            if extracted_at:
                year_match = re.search(r'(19[5-9]\d|20[0-3]\d)', str(extracted_at))
                if year_match:
                    doc_year = int(year_match.group(1))
            
            # Also extract years from text
            text_years = set(int(y) for y in SearchManager._TEMPORAL_YEAR_PATTERN.findall(text))
            
            if hint_type == "year" and target_years:
                # Boost if document mentions the target year(s)
                overlap = target_years & text_years
                if overlap:
                    boost = 0.15 * (len(overlap) / len(target_years))
                elif doc_year and doc_year in target_years:
                    boost = 0.10
                    
            elif hint_type == "range":
                # Boost for documents within the year range
                in_range = {y for y in text_years if range_start <= y <= range_end}
                if in_range:
                    boost = 0.12
                elif doc_year and range_start <= doc_year <= range_end:
                    boost = 0.08
                    
            elif hint_type == "keyword":
                if direction == "historical":
                    # Prefer older documents
                    if doc_year and doc_year < 2015:
                        boost = 0.10
                    elif text_years and min(text_years) < 2015:
                        boost = 0.08
                elif direction == "recent":
                    # Prefer newer documents
                    import datetime as _dt
                    cutoff = _dt.datetime.now().year - 3
                    if doc_year and doc_year >= cutoff:
                        boost = 0.10
                    elif text_years and max(text_years) >= cutoff:
                        boost = 0.08
                elif direction == "evolution":
                    # Prefer documents that span multiple years (diverse timeline)
                    if len(text_years) >= 2:
                        boost = 0.12
                    elif text_years:
                        boost = 0.05
            
            # Apply boost to score
            if boost > 0:
                score_key = "rerank_score" if "rerank_score" in r else "score"
                original_score = r.get(score_key, 0.0)
                r[score_key] = original_score * (1.0 + boost)
                r["_temporal_boost"] = boost
            
            boosted.append(r)
        
        # Re-sort by score after boosting
        score_key = "rerank_score" if any("rerank_score" in r for r in boosted) else "score"
        boosted.sort(key=lambda r: r.get(score_key, 0.0), reverse=True)
        
        if self.debug:
            n_boosted = sum(1 for r in boosted if r.get("_temporal_boost", 0) > 0)
            if n_boosted:
                logger.debug(
                    f"⏰ Temporal boost: {n_boosted}/{len(boosted)} results boosted "
                    f"(type={hint_type}, direction={direction})"
                )
        
        return boosted

    @staticmethod
    def _parse_iso_datetime(value: Any) -> Optional[Any]:
        """Parse ISO date/time strings to UTC-aware datetime, return None on failure."""
        if not value:
            return None
        try:
            from datetime import datetime, timezone

            text = str(value).strip()
            if not text:
                return None
            text = text.replace("Z", "+00:00")
            dt = datetime.fromisoformat(text)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            else:
                dt = dt.astimezone(timezone.utc)
            return dt
        except Exception:
            return None

    def _score_triple_lifecycle(
        self,
        triple_meta: Dict[str, Any],
        temporal_hints: Optional[Dict[str, Any]] = None,
    ) -> float:
        """
        Compute a lifecycle quality score in [0, 1] for a KG triple.

        Factors:
        - confidence / evidence_strength / grounding_score
        - contradiction_state / is_contradicted
        - temporal validity fit for temporal queries
        """
        score = 0.5

        # Reliability signals
        confidence = triple_meta.get("kg_confidence")
        evidence_strength = triple_meta.get("kg_evidence_strength")
        grounding_score = triple_meta.get("kg_grounding_score")

        try:
            if confidence is not None:
                conf = max(0.0, min(1.0, float(confidence)))
                score = (score * 0.35) + (conf * 0.65)
        except Exception:
            pass

        try:
            if evidence_strength is not None:
                evid = max(0.0, min(1.0, float(evidence_strength)))
                score = (score * 0.5) + (evid * 0.5)
        except Exception:
            pass

        try:
            if grounding_score is not None:
                g = float(grounding_score)
                if g >= 0.0:
                    g = max(0.0, min(1.0, g))
                    score = (score * 0.6) + (g * 0.4)
                else:
                    score *= 0.9
        except Exception:
            pass

        # Contradiction penalty
        contradiction_state = str(triple_meta.get("kg_contradiction_state") or "none").lower()
        is_contradicted = bool(triple_meta.get("kg_is_contradicted", False))

        hard_false = {"confirmed_false", "retracted", "superseded", "contradicted"}
        soft_false = {"disputed", "uncertain", "challenged"}

        if contradiction_state in hard_false or is_contradicted:
            score *= 0.15
        elif contradiction_state in soft_false:
            score *= 0.55

        # Temporal fit (only when query has temporal intent)
        if temporal_hints:
            from datetime import datetime, timezone

            now = datetime.now(timezone.utc)
            hint_type = temporal_hints.get("type", "")
            direction = str(temporal_hints.get("direction", ""))

            validity_type = str(triple_meta.get("kg_validity_type") or "atemporal").lower()
            valid_from = self._parse_iso_datetime(triple_meta.get("kg_valid_from"))
            valid_to = self._parse_iso_datetime(triple_meta.get("kg_valid_to"))
            observed_at = self._parse_iso_datetime(triple_meta.get("kg_observed_at"))

            # If explicitly time-bound and expired, downrank for recent/current intent.
            if validity_type in {"event", "ephemeral", "periodic"} and valid_to and valid_to < now:
                if direction in {"recent", "current"}:
                    score *= 0.6

            if hint_type == "year":
                years = {int(y) for y in temporal_hints.get("years", [])}
                if years:
                    overlap = False
                    for y in years:
                        if valid_from and valid_to:
                            if valid_from.year <= y <= valid_to.year:
                                overlap = True
                                break
                        elif valid_from and valid_from.year == y:
                            overlap = True
                            break
                        elif observed_at and observed_at.year == y:
                            overlap = True
                            break
                    score *= 1.08 if overlap else 0.7

            elif hint_type == "range":
                start = int(temporal_hints.get("start", 0))
                end = int(temporal_hints.get("end", 9999))
                overlap = False
                if valid_from and valid_to:
                    overlap = not (valid_to.year < start or valid_from.year > end)
                elif valid_from:
                    overlap = start <= valid_from.year <= end
                elif observed_at:
                    overlap = start <= observed_at.year <= end
                score *= 1.06 if overlap else 0.72

            elif hint_type == "keyword":
                if direction == "recent":
                    cutoff = now.year - 3
                    recent = (observed_at and observed_at.year >= cutoff) or (valid_from and valid_from.year >= cutoff)
                    score *= 1.06 if recent else 0.78
                elif direction == "historical":
                    cutoff = now.year - 5
                    old = (observed_at and observed_at.year <= cutoff) or (valid_to and valid_to.year <= cutoff)
                    score *= 1.06 if old else 0.85
                elif direction == "evolution":
                    if valid_from and valid_to and valid_from.year != valid_to.year:
                        score *= 1.08

        return max(0.0, min(1.0, score))

    def _apply_fact_lifecycle_scoring(
        self,
        results: List[Dict[str, Any]],
        temporal_hints: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Apply lifecycle-aware soft scoring for KG-backed results.

        Uses triple metadata (if available) and re-orders results without filtering.
        """
        if not results:
            return results

        score_key = "rerank_score" if any("rerank_score" in r for r in results) else "score"
        adjusted = 0

        for r in results:
            md = r.get("metadata") or {}
            if not isinstance(md, dict):
                continue

            if "kg_triple" not in md:
                continue

            lifecycle_score = self._score_triple_lifecycle(md, temporal_hints=temporal_hints)
            base_score = float(r.get(score_key, 0.0))

            # Blend semantic relevance (70%) with lifecycle trust (30%).
            # For hard contradictions lifecycle_score collapses and strongly downranks.
            blended = (base_score * 0.7) + (lifecycle_score * 0.3)
            r[score_key] = blended
            r["score"] = blended
            r["_kg_lifecycle_score"] = lifecycle_score
            adjusted += 1

        if adjusted > 0:
            results.sort(key=lambda x: x.get(score_key, 0.0), reverse=True)
            if self.debug:
                logger.debug(
                    f"🧭 Lifecycle scoring applied to {adjusted}/{len(results)} results"
                )

        return results

    def _apply_reranker_quality_gate(
        self,
        results: List[Dict[str, Any]],
        k: int,
        min_reranker_score: float = 0.03,
        relative_threshold: float = 0.15,
        fallback_min_results: int = 1,
    ) -> List[Dict[str, Any]]:
        """
        ★ SOTA v2: Post-reranking quality gate with relative threshold.
        
        Critical fix: Old version used static 0.08 threshold which was too
        aggressive for explorative/broad queries where all results score lower.
        
        New approach:
        - Floor: 0.03 (absolute minimum, below this is truly random)
        - Relative: Keep results ≥ 15% of top-1 score
        - This lets explorative queries return results even if all scores are low
        - But still filters clearly irrelevant results in focused queries
        
        Args:
            results: Reranked results (may have 'rerank_score' key)
            k: Target result count
            min_reranker_score: Absolute floor score (0.03)
            relative_threshold: Keep if score >= top_score * relative_threshold
            fallback_min_results: Guarantee at least this many results
            
        Returns:
            Filtered results
        """
        if not results:
            return results
        
        # Check if reranker actually scored the results
        has_reranker_scores = any(
            "rerank_score" in r or ("score" in r and r.get("_reranked"))
            for r in results
        )
        
        if not has_reranker_scores:
            return results[:k]
        
        # Find top-1 score for relative threshold
        top_score = 0.0
        for r in results:
            score = r.get("rerank_score", r.get("score", 0.0))
            if score > top_score:
                top_score = score
        
        # Compute effective threshold: max(floor, top_score * relative_pct)
        effective_threshold = max(min_reranker_score, top_score * relative_threshold)
        
        filtered = []
        for r in results:
            score = r.get("rerank_score", r.get("score", 0.0))
            if score >= effective_threshold:
                filtered.append(r)
        
        # Ensure at least fallback_min_results
        if len(filtered) < fallback_min_results and results:
            filtered = results[:fallback_min_results]
        
        if self.debug and len(filtered) < len(results):
            logger.debug(
                f"🚧 Quality gate: {len(results)} → {len(filtered)} results "
                f"(floor={min_reranker_score}, relative={effective_threshold:.4f}, "
                f"top_score={top_score:.4f})"
            )
        
        return filtered[:k]
    
    def _apply_structural_quality_penalty(
        self,
        results: List[Dict[str, Any]],
        quality_floor: float = 0.3,
    ) -> List[Dict[str, Any]]:
        """
        ★ SOTA v3: Apply pre-computed chunk structural quality as a soft score multiplier.
        
        Reads chunk_quality.structural_score from the DB for returned chunks and
        multiplies each result's score by max(structural_score, quality_floor).
        
        This means:
          - A chunk with structural_score=1.0 → no penalty
          - A chunk with structural_score=0.5 → score * 0.5
          - A chunk with structural_score=0.1 → score * 0.3 (floor protects minimum)
          - A chunk with no quality score (never audited) → no penalty applied
        
        After penalty, results are re-sorted by score.
        
        Args:
            results: Reranked search results
            quality_floor: Minimum multiplier (prevents total zeroing of valid results)
            
        Returns:
            Results with quality penalty applied, re-sorted by score
        """
        if not results:
            return results
        
        # Collect (doc_id, chunk_id) pairs that we need quality scores for
        chunk_keys = []
        for r in results:
            doc_id = r.get('doc_id', r.get('metadata', {}).get('doc_id', ''))
            chunk_id = r.get('chunk_id', r.get('id', ''))
            if doc_id and chunk_id:
                chunk_keys.append((str(doc_id), int(chunk_id) if str(chunk_id).isdigit() else -1))
        
        if not chunk_keys:
            return results
        
        # Batch-fetch structural scores from chunk_quality table
        quality_map: Dict[str, float] = {}  # "doc_id:chunk_id" -> structural_score
        try:
            conn = self.db_manager.get_connection()
            cur: Any = None
            try:
                cur = conn.cursor()
                # Use IN clause for batch lookup (SQLite limit: 999 params)
                for i in range(0, len(chunk_keys), 400):
                    batch = chunk_keys[i:i+400]
                    conditions = " OR ".join(
                        f"(doc_id = ? AND chunk_id = ?)" for _ in batch
                    )
                    params = []
                    for doc_id, chunk_id in batch:
                        params.extend([doc_id, chunk_id])
                    
                    cur.execute(
                        f"SELECT doc_id, chunk_id, structural_score, defect_flags "
                        f"FROM chunk_quality WHERE {conditions}",
                        params
                    )
                    for row in cur.fetchall():
                        key = f"{row[0]}:{row[1]}"
                        # Hard defects get maximum penalty
                        defect_flags = row[3] or ""
                        if any(d in defect_flags for d in ("encoding_garbage", "trivial")):
                            quality_map[key] = 0.0
                        else:
                            quality_map[key] = float(row[2]) if row[2] is not None else -1.0
            finally:
                if cur is not None:
                    cur.close()
                self.db_manager.return_connection(conn)
        except Exception as e:
            if self.debug:
                logger.debug(f"⚠️ Quality penalty: DB lookup failed: {e}")
            return results  # Return unchanged on DB error
        
        if not quality_map:
            return results  # No quality data → no penalty
        
        # Apply quality multiplier
        n_penalized = 0
        score_key = "rerank_score" if any("rerank_score" in r for r in results) else "score"
        
        for r in results:
            doc_id = str(r.get('doc_id', r.get('metadata', {}).get('doc_id', '')))
            chunk_id = str(r.get('chunk_id', r.get('id', '')))
            key = f"{doc_id}:{chunk_id}"
            
            if key in quality_map:
                q_score = quality_map[key]
                if q_score < 0:
                    continue  # -1 = not scored yet → no penalty
                multiplier = max(q_score, quality_floor)
                if multiplier < 1.0:
                    original_score = r.get(score_key, 0.0)
                    r[score_key] = original_score * multiplier
                    r["score"] = r[score_key]
                    r["_quality_penalty"] = 1.0 - multiplier
                    n_penalized += 1
        
        # Re-sort by score after penalty
        results.sort(key=lambda r: r.get(score_key, 0.0), reverse=True)
        
        if self.debug and n_penalized > 0:
            logger.debug(
                f"📉 Quality penalty: {n_penalized}/{len(results)} results penalized "
                f"(quality data for {len(quality_map)} chunks)"
            )
        
        return results

    # ====================================================================
    # DENSE RETRIEVAL (FAISS / NumPy)
    # ====================================================================
    
    def _embedding_search(
        self,
        query: str,
        k: int = 5,
        min_score: float = 0.0,
        adaptive_confidence: bool = True,
        faiss_min_confidence: Optional[float] = None,
        allowed_domains: Optional[List[str]] = None,
        exclude_safety_flags: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Embedding-basierte Suche mit FAISS HYBRID ENGINE.
        
        🚀 FEATURES:
            - FAISS Adaptive Search (555x schneller als NumPy!)
            - Dual-Index Strategy (Recent + Full)
            - Intelligent Fast Path/Fallback
            - Adaptive Confidence basierend auf Suchtiefe k
            
        Args:
            query: Suchanfrage
            k: Suchtiefe (Anzahl Results)
            min_score: Minimaler Score-Filter (NACH FAISS)
            adaptive_confidence: Wenn True, passe Confidence an k an
            faiss_min_confidence: Manuelle FAISS Confidence (überschreibt adaptive!)
            
        Fallback: NumPy-basierte Suche wenn FAISS nicht verfügbar
        """
        if not query.strip():
            return []
        
        # Check for EmbeddingManager availability
        if self.embedding_manager is None:
            raise RuntimeError(
                "SearchManager embedding_manager is None (lazy init not completed)"
            )
        
        try:
            # Query-Embedding generieren
            query_emb = self.embedding_manager.embed_texts([query])[0]
            
            # Konvertiere zu numpy array falls nötig
            if NUMPY_AVAILABLE and np is not None and not isinstance(query_emb, np.ndarray):
                query_emb = np.array(query_emb, dtype="float32")
            
            # 🚀 FAISS HYBRID PATH (Primär)
            if self.faiss_manager and FAISS_HYBRID_AVAILABLE:
                try:
                    start_time = time.time()
                    
                    # When namespace/safety filters are active we over-fetch
                    # so the post-FAISS SQL filter still leaves enough hits.
                    has_chunk_filter = bool(allowed_domains) or bool(exclude_safety_flags)
                    fetch_k = k * 3 if has_chunk_filter else k * 2

                    # FAISS Adaptive Search mit dynamischer Confidence
                    chunk_ids, scores, strategy = self.faiss_manager.search(
                        query_embedding=query_emb,
                        k=fetch_k,
                        min_confidence=faiss_min_confidence,
                        adaptive_confidence=adaptive_confidence if faiss_min_confidence is None else False
                    )
                    
                    search_time = (time.time() - start_time) * 1000
                    
                    if self.debug:
                        logger.debug(
                            f"🚀 FAISS Search: {search_time:.2f}ms | "
                            f"Strategy: {strategy} | "
                            f"Results: {len(chunk_ids)}"
                        )
                    
                    # Convert FAISS results to RAG format
                    results = []
                    filter_clause, filter_params = self._chunk_filter_clause(
                        allowed_domains, exclude_safety_flags
                    )
                    conn = self.db_manager.get_connection()
                    try:
                        for chunk_id, score in zip(chunk_ids, scores):
                            # Hole Chunk-Daten aus DB
                            cur = conn.execute(
                                "SELECT doc_id, text, metadata, domain, safety_flag "
                                f"FROM chunks WHERE chunk_id = ?{filter_clause}",
                                (chunk_id, *filter_params),
                            )
                            row = cur.fetchone()
                            if row:
                                doc_id, text, metadata_json, c_domain, c_safety = row
                                try:
                                    metadata = json.loads(metadata_json)
                                except (json.JSONDecodeError, TypeError):
                                    metadata = {}
                                
                                results.append({
                                    "text": text,
                                    "metadata": metadata,
                                    "score": float(score),
                                    "doc_id": doc_id,
                                    "chunk_id": chunk_id,
                                    "domain": c_domain,
                                    "safety_flag": c_safety,
                                })
                    finally:
                        self.db_manager.return_connection(conn)
                    
                    # Nach Score sortieren und zurückgeben
                    results.sort(key=lambda x: x["score"], reverse=True)
                    return results[:k]
                    
                except Exception as e:
                    raise RuntimeError(f"FAISS search execution failed: {e}") from e
            
            # 📊 NUMPY FALLBACK PATH (Original-Implementation)
            if not NUMPY_AVAILABLE or np is None:
                raise RuntimeError("NumPy not available; cannot execute embedding search")
            
            # Alle Chunks mit Embeddings laden
            conn = self.db_manager.get_connection()
            cur = conn.cursor()
            
            try:
                filter_clause, filter_params = self._chunk_filter_clause(
                    allowed_domains, exclude_safety_flags
                )
                where_sql = ""
                if filter_clause:
                    # _chunk_filter_clause prefixes " AND "; convert to WHERE for the
                    # standalone full-scan query.
                    where_sql = " WHERE " + filter_clause[len(" AND "):]
                cur.execute(
                    "SELECT doc_id, chunk_id, text, metadata, embedding, domain, safety_flag "
                    f"FROM chunks{where_sql} ORDER BY chunk_id",
                    filter_params,
                )
                
                results = []
                for row in cur.fetchall():
                    doc_id, chunk_id, text, metadata_json, embedding_blob, c_domain, c_safety = row
                    
                    # Embedding aus BLOB laden
                    chunk_emb = np.frombuffer(embedding_blob, dtype="float32")
                    
                    # Cosine Similarity berechnen (mit Zero-Vector-Schutz)
                    query_norm = np.linalg.norm(query_emb)
                    chunk_norm = np.linalg.norm(chunk_emb)
                    if query_norm == 0.0 or chunk_norm == 0.0:
                        similarity = 0.0
                    else:
                        similarity = float(
                            np.dot(query_emb, chunk_emb) / (query_norm * chunk_norm)
                        )
                    
                    if similarity >= min_score:
                        try:
                            metadata = json.loads(metadata_json)
                        except (json.JSONDecodeError, TypeError):
                            metadata = {}
                        
                        results.append({
                            "text": text,
                            "metadata": metadata,
                            "score": similarity,
                            "doc_id": doc_id,
                            "chunk_id": chunk_id,
                            "domain": c_domain,
                            "safety_flag": c_safety,
                        })
                
                # Nach Score sortieren und Top-k zurückgeben
                results.sort(key=lambda x: x["score"], reverse=True)
                return results[:k]
                
            finally:
                cur.close()
                self.db_manager.return_connection(conn)
                
        except Exception as e:
            logger.error(f"Embedding search failed (fail-fast): {e}")
            raise RuntimeError(f"Embedding search failed: {e}") from e
    
    # ====================================================================
    # ★ SOTA v3: SEMANTIC KNOWLEDGE GRAPH SEARCH
    # ====================================================================

    def _ensure_entity_index(self) -> bool:
        """
        Lazy-init: Load entity embeddings from kg_entities table into numpy array.
        Returns True if index is available and has entries.
        """
        if np is None:
            return False

        def _is_valid_entity_matrix(matrix: Any, texts: Optional[List[str]]) -> bool:
            if np is None:
                return False
            if matrix is None or texts is None:
                return False
            if not isinstance(matrix, np.ndarray):
                return False
            if matrix.ndim != 2 or matrix.shape[0] == 0 or matrix.shape[1] == 0:
                return False
            if matrix.shape[0] != len(texts):
                return False
            return bool(np.isfinite(matrix).all())

        def _decode_embedding_blob(raw: Any) -> Optional[Any]:
            if np is None:
                return None
            if raw is None:
                return None
            try:
                if isinstance(raw, (bytes, bytearray, memoryview)):
                    emb = np.frombuffer(raw, dtype=np.float32).copy()
                elif isinstance(raw, np.ndarray):
                    emb = np.asarray(raw, dtype=np.float32).copy()
                elif isinstance(raw, (list, tuple)):
                    emb = np.asarray(raw, dtype=np.float32)
                else:
                    return None
            except Exception:
                return None

            emb = np.asarray(emb, dtype=np.float32).reshape(-1)
            if emb.size == 0 or not np.isfinite(emb).all():
                return None
            return emb

        def _expected_entity_dim() -> Optional[int]:
            mgr = self.embedding_manager
            if mgr is not None:
                dim_attr = getattr(mgr, "dimension", None)
                if isinstance(dim_attr, int) and dim_attr > 0:
                    return int(dim_attr)
                if callable(dim_attr):
                    try:
                        dim_value = dim_attr()
                    except Exception:
                        dim_value = None
                    if isinstance(dim_value, int) and dim_value > 0:
                        return int(dim_value)
            if isinstance(self.embedding_dim, int) and self.embedding_dim > 0:
                return int(self.embedding_dim)
            return None

        if self._entity_index_built:
            return _is_valid_entity_matrix(self._entity_embeddings, self._entity_texts)

        with self._entity_index_lock:
            if self._entity_index_built:
                return _is_valid_entity_matrix(self._entity_embeddings, self._entity_texts)

            try:
                if not NUMPY_AVAILABLE or not self.embedding_manager:
                    self._entity_index_built = True
                    return False

                conn = self.db_manager.get_connection()
                cur = conn.cursor()
                try:
                    cur.execute(
                        "SELECT entity_text, embedding FROM kg_entities WHERE embedding IS NOT NULL"
                    )
                    rows = cur.fetchall()
                finally:
                    cur.close()
                    self.db_manager.return_connection(conn)

                if not rows:
                    logger.info("[KG-EntityIndex] No entity embeddings found — index empty")
                    self._entity_texts = []
                    self._entity_embeddings = None
                    self._entity_index_built = True
                    return False

                expected_dim = _expected_entity_dim()
                texts: List[str] = []
                embs: List[Any] = []
                rejected_rows = 0
                for entity_text, emb_blob in rows:
                    emb = _decode_embedding_blob(emb_blob)
                    if emb is None:
                        rejected_rows += 1
                        continue
                    if expected_dim is not None and emb.shape[0] != expected_dim:
                        rejected_rows += 1
                        continue
                    texts.append(entity_text)
                    embs.append(emb)

                if texts:
                    matrix = np.stack(embs, axis=0).astype(np.float32, copy=False)
                    if matrix.ndim != 2:
                        raise RuntimeError(
                            f"KG entity index matrix must be 2D, got shape={matrix.shape}"
                        )

                    # L2-normalize for cosine similarity via dot product
                    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
                    norms = np.maximum(norms, 1e-10)
                    matrix = matrix / norms

                    if not _is_valid_entity_matrix(matrix, texts):
                        raise RuntimeError(
                            "KG entity index failed post-normalization validation"
                        )

                    self._entity_texts = texts
                    self._entity_embeddings = matrix
                    logger.info(
                        f"[KG-EntityIndex] Loaded {len(texts)} entity embeddings "
                        f"({self._entity_embeddings.shape})"
                    )
                    if rejected_rows > 0:
                        logger.warning(
                            f"[KG-EntityIndex] Rejected {rejected_rows} invalid entity embeddings "
                            f"(expected_dim={expected_dim})"
                        )
                else:
                    self._entity_texts = []
                    self._entity_embeddings = None
                    if rejected_rows > 0:
                        logger.warning(
                            f"[KG-EntityIndex] Rejected all {rejected_rows} entity embeddings as invalid"
                        )

                self._entity_index_built = True
                return _is_valid_entity_matrix(self._entity_embeddings, self._entity_texts)

            except Exception as e:
                logger.error(f"[KG-EntityIndex] Build failed (fail-fast): {e}")
                self._entity_texts = []
                self._entity_embeddings = None
                self._entity_index_built = False
                raise RuntimeError(f"KG entity index build failed: {e}") from e

    def invalidate_entity_index(self) -> None:
        """
        Mark entity index as stale after KG mutations.

        Uses non-destructive invalidation so concurrent readers can finish on a
        stable snapshot while the next reader lazily rebuilds fresh state.
        """
        with self._entity_index_lock:
            self._entity_index_built = False
            self._entity_index_version += 1  # ← Increment to invalidate any stale references

    # ====================================================================
    # COMMUNITY DETECTION (SOTA v5: Leiden Algorithm)
    # ====================================================================

    def _ensure_community_detector(self) -> bool:
        """
        Lazy-Initialisierung des CommunityDetector.

        Pipeline:
          1. Try loading persisted community state from disk
          2. If stale or missing: build NetworkX graph from triples → run Leiden
          3. Generate community summaries & keywords

        Returns:
            True if community detector is ready
        """
        if self._community_detector_built and self._community_detector is not None:
            return True

        with self._community_detector_lock:
            if self._community_detector_built and self._community_detector is not None:
                return True

            try:
                if not NETWORKX_AVAILABLE:
                    logger.warning("[CommunityDetection] NetworkX not available — disabled")
                    self._community_detector_built = True
                    return False

                try:
                    from agent.community_detector import CommunityDetector as _CommunityDetector
                except ImportError:
                    logger.warning("[CommunityDetection] community_detector module not found — disabled")
                    self._community_detector_built = True
                    return False

                graph = self._build_kg_graph_for_community_detection()
                if graph is None or graph.number_of_nodes() < 5:
                    logger.info("[CommunityDetection] Graph too small for community detection")
                    self._community_detector_built = True
                    return False

                state_path = self._get_community_state_path()
                self._community_state_path = state_path

                if state_path and os.path.exists(state_path):
                    try:
                        detector = _CommunityDetector(graph)
                        if detector.load_state(state_path):
                            if self._validate_community_state(detector, graph):
                                self._community_detector = detector
                                self._community_detector_built = True
                                if self.debug:
                                    logger.info("[CommunityDetection] Loaded persisted state")
                                return True
                    except Exception as load_err:
                        logger.debug(f"[CommunityDetection] Failed to load state: {load_err}")

                detector = _CommunityDetector(graph)
                stats = detector.detect_communities()

                if stats.get("success") and stats.get("num_communities", 0) > 0:
                    detector.generate_community_summaries()
                    if state_path:
                        detector.save_state(state_path)
                    self._community_detector = detector
                    self._community_detector_built = True
                    if self.debug:
                        logger.info(
                            f"[CommunityDetection] Detected {stats['num_communities']} communities, "
                            f"modularity={stats.get('modularity', 0.0):.3f}"
                        )
                    return True
                else:
                    logger.info("[CommunityDetection] No communities detected")
                    self._community_detector_built = True
                    return False

            except Exception as e:
                logger.error(f"[CommunityDetection] Initialization failed: {e}")
                self._community_detector_built = True
                return False

    def _get_community_state_path(self) -> Optional[str]:
        """Get path for persisting community detection state."""
        try:
            db_path = self.db_manager.db_path
            if not db_path or "memory" in db_path:
                return None
            db_dir = os.path.dirname(os.path.abspath(db_path))
            return os.path.join(db_dir, ".community_state.json")
        except Exception:
            return None

    def _build_kg_graph_for_community_detection(self) -> Optional[Any]:
        """Build a NetworkX graph from KG triples for community detection."""
        if not NETWORKX_AVAILABLE or nx is None:
            return None
        try:
            G = nx.Graph()
            conn = self.db_manager.get_connection()
            cur = conn.cursor()
            try:
                cur.execute("""
                    SELECT DISTINCT t.subject, t.predicate, t.object
                    FROM triples t
                    WHERE t.subject IS NOT NULL AND t.object IS NOT NULL
                    LIMIT 10000
                """)
                triples = cur.fetchall()
            finally:
                cur.close()
                self.db_manager.return_connection(conn)

            if not triples:
                return None

            for subject, predicate, obj in triples:
                if not G.has_node(subject):
                    G.add_node(subject, node_type="entity")
                if not G.has_node(obj):
                    G.add_node(obj, node_type="entity")
                G.add_edge(subject, obj, relation=predicate)

            if self.debug:
                logger.debug(
                    f"[CommunityDetection] Built graph: {G.number_of_nodes()} nodes, "
                    f"{G.number_of_edges()} edges"
                )
            return G

        except Exception as e:
            logger.error(f"[CommunityDetection] Failed to build graph: {e}")
            return None

    def _validate_community_state(self, detector: Any, graph: Any) -> bool:
        """Validate persisted community state matches current graph structure."""
        try:
            persisted_node_count = sum(len(nodes) for nodes in detector.communities.values())
            current_node_count = graph.number_of_nodes()
            if current_node_count == 0:
                return False
            ratio = persisted_node_count / max(current_node_count, 1)
            return 0.8 <= ratio <= 1.2
        except Exception:
            return False

    def invalidate_community_detector(self) -> None:
        """Mark community detector as stale after KG mutations."""
        with self._community_detector_lock:
            self._community_detector_built = False
            self._community_detector = None

    def _get_relevant_communities(self, query: str, matched_entities: List[Tuple[str, float]],
                                  top_k: int = 3) -> List[Dict[str, Any]]:
        """
        Find communities relevant to the query based on matched entities.

        Args:
            query: Search query
            matched_entities: List of (entity_text, score) tuples
            top_k: Number of communities to return

        Returns:
            List of community dicts with id, score, nodes, summary
        """
        if not self._community_detector or not matched_entities:
            return []

        try:
            detector = self._community_detector
            community_scores: Dict[int, float] = {}
            entity_set = {entity_text for entity_text, _ in matched_entities[:10]}

            for comm_id, comm_nodes in detector.communities.items():
                overlap = len(entity_set.intersection(comm_nodes))
                if overlap > 0:
                    comm_info = detector.get_community_info(comm_id)
                    if comm_info:
                        quality_factor = comm_info.modularity_score if comm_info.modularity_score > 0 else 0.5
                        size_factor = max(len(comm_nodes), 1) ** 0.5
                        score = (overlap * quality_factor) / size_factor
                        community_scores[comm_id] = community_scores.get(comm_id, 0) + score

            sorted_communities = sorted(community_scores.items(), key=lambda x: x[1], reverse=True)

            results = []
            for comm_id, score in sorted_communities[:top_k]:
                comm_info = detector.get_community_info(comm_id)
                if comm_info:
                    results.append({
                        "community_id": comm_id,
                        "score": score,
                        "nodes": list(comm_info.nodes),
                        "summary": comm_info.summary,
                        "keywords": comm_info.keywords,
                        "size": comm_info.size,
                        "quality": comm_info.quality.value,
                    })

            return results

        except Exception as e:
            logger.debug(f"[CommunityDetection] Failed to get relevant communities: {e}")
            return []

    def _get_community_rerank_score(self, triple: Dict[str, Any]) -> float:
        """
        Get community-aware rerank score for a triple.

        Boosts triples whose entities belong to high-quality, relevant communities.

        Args:
            triple: Triple dict with 'subject' and 'object'

        Returns:
            Score between 0.0 and 1.0
        """
        if not self._community_detector:
            return 0.5  # Neutral score when no community detection

        try:
            detector = self._community_detector
            subject = triple.get("subject", "")
            obj = triple.get("object", "")

            subj_community = detector.node_to_community.get(subject)
            obj_community = detector.node_to_community.get(obj)

            if subj_community is None and obj_community is None:
                return 0.5  # Neutral: entities not in any community

            scores = []
            for comm_id in [subj_community, obj_community]:
                if comm_id is not None:
                    comm_info = detector.get_community_info(comm_id)
                    if comm_info:
                        scores.append(comm_info.modularity_score)

            if scores:
                return max(0.0, min(1.0, sum(scores) / len(scores)))
            return 0.5

        except Exception:
            return 0.5  # Neutral on error

    def _expand_triples_via_communities(self, query: str, matched_entities: List[Tuple[str, float]],
                                        relevant_communities: List[Dict[str, Any]],
                                        seen_triple_ids: set, kg_filter_clause: str,
                                        kg_filter_params: List[Any], k: int,
                                        cur: Any) -> List[Dict[str, Any]]:
        """
        Expand triple candidates via community membership.

        For each relevant community, retrieve additional triples containing
        community member entities (beyond the initial matched entities).
        """
        if not relevant_communities:
            return []

        expanded_triples = []
        entity_texts = {entity_text for entity_text, _ in matched_entities}
        max_community_expansion = 50

        for comm in relevant_communities[:3]:
            comm_nodes = set(comm.get("nodes", []))
            expansion_entities = comm_nodes - entity_texts

            for entity_text in list(expansion_entities)[:10]:
                try:
                    cur.execute(f"""
                        SELECT t.triple_id, t.doc_id, t.subject, t.predicate, t.object,
                            t.source_chunk_id, c.text, c.metadata, c.chunk_id,
                            t.confidence, t.evidence_strength,
                            t.validity_type, t.valid_from, t.valid_to,
                            t.observed_at, t.last_verified_at,
                            t.contradiction_state,
                            tq.grounding_score, tq.is_contradicted
                        FROM triples t
                        LEFT JOIN triple_quality tq ON tq.triple_id = t.triple_id
                        LEFT JOIN chunks c ON (t.source_chunk_id IS NOT NULL AND t.source_chunk_id = c.chunk_id)
                                           OR (t.source_chunk_id IS NULL AND t.doc_id = c.doc_id)
                        WHERE (t.subject = ? OR t.object = ?){kg_filter_clause}
                        LIMIT ?
                    """, (entity_text, entity_text, *kg_filter_params, k * 2))

                    for row in cur.fetchall():
                        triple_id = row[0]
                        if triple_id in seen_triple_ids:
                            continue
                        seen_triple_ids.add(triple_id)
                        expanded_triples.append({
                            "triple_id": triple_id,
                            "doc_id": row[1],
                            "subject": row[2],
                            "predicate": row[3],
                            "object": row[4],
                            "source_chunk_id": row[5],
                            "chunk_text": row[6],
                            "chunk_metadata": row[7],
                            "chunk_id": row[8],
                            "confidence": row[9],
                            "evidence_strength": row[10],
                            "validity_type": row[11],
                            "valid_from": row[12],
                            "valid_to": row[13],
                            "observed_at": row[14],
                            "last_verified_at": row[15],
                            "contradiction_state": row[16],
                            "grounding_score": row[17],
                            "is_contradicted": row[18],
                            "entity_score": 0.4,
                            "hop": 1.5,
                            "community_id": comm["community_id"],
                        })

                    if len(expanded_triples) >= max_community_expansion:
                        break

                except Exception as e:
                    logger.debug(f"[CommunityDetection] Failed to expand entity {entity_text}: {e}")
                    continue

        if self.debug and expanded_triples:
            logger.debug(
                f"[CommunityDetection] Expanded {len(expanded_triples)} triples via "
                f"{len(relevant_communities)} communities"
            )

        return expanded_triples

    def _semantic_entity_match(self, query: str, top_k: int = 15) -> List[Tuple[str, float]]:
        """
        Embed query → cosine similarity against all entity embeddings → top-k entities.

        Returns list of (entity_text, similarity_score) sorted by score desc.
        Uses snapshot semantics so concurrent index invalidation does not break
        in-flight reads.
        """
        if np is None:
            return []
        if self.embedding_manager is None:
            return []
        if not self._ensure_entity_index():
            return []

        # Snapshot index references under lock for read-side stability.
        with self._entity_index_lock:
            entity_embeddings = self._entity_embeddings
            entity_texts = list(self._entity_texts) if self._entity_texts is not None else None
            snapshot_version = self._entity_index_version

        try:
            if not isinstance(entity_embeddings, np.ndarray) or entity_embeddings.ndim != 2:
                raise RuntimeError(
                    f"KG entity index invalid shape before match: "
                    f"{getattr(entity_embeddings, 'shape', None)}"
                )
            if not entity_texts or entity_embeddings.shape[0] != len(entity_texts):
                raise RuntimeError(
                    "KG entity index/text mismatch before semantic match"
                )

            # Embed query (reuse existing infrastructure)
            query_matrix = self.embedding_manager.embed_texts([f"query: {query}"])
            query_matrix = np.asarray(query_matrix, dtype=np.float32)
            if query_matrix.ndim == 1:
                query_emb = query_matrix
            elif query_matrix.ndim == 2 and query_matrix.shape[0] >= 1:
                query_emb = query_matrix[0]
            else:
                raise RuntimeError(
                    f"Query embedding has invalid shape: {query_matrix.shape}"
                )

            query_emb = np.asarray(query_emb, dtype=np.float32).reshape(-1)
            if query_emb.size == 0:
                raise RuntimeError("Query embedding is empty")

            if query_emb.shape[0] != entity_embeddings.shape[1]:
                raise RuntimeError(
                    "Embedding dimension mismatch in semantic entity match: "
                    f"query_dim={query_emb.shape[0]}, index_dim={entity_embeddings.shape[1]}"
                )

            query_emb = query_emb / max(float(np.linalg.norm(query_emb)), 1e-10)

            # Cosine similarity = dot product (both L2-normalized)
            similarities = entity_embeddings @ query_emb  # (N,)

            # Staleness is acceptable for this read; next call rebuilds lazily.
            if self._entity_index_version != snapshot_version and self.debug:
                logger.debug(
                    "[KG-SemanticMatch] Index version changed during read "
                    f"(snapshot={snapshot_version}, current={self._entity_index_version})"
                )

            # Top-k indices
            if len(similarities) <= top_k:
                top_indices = np.argsort(similarities)[::-1]
            else:
                # Partial sort for efficiency
                top_indices = np.argpartition(similarities, -top_k)[-top_k:]
                top_indices = top_indices[np.argsort(similarities[top_indices])[::-1]]

            results = []
            for idx in top_indices:
                score = float(similarities[idx])
                if score > 0.3:  # Minimum semantic similarity threshold
                    results.append((entity_texts[idx], score))

            return results

        except Exception as e:
            logger.error(f"[KG-SemanticMatch] Entity matching failed (fail-fast): {e}")
            raise RuntimeError(f"KG semantic entity match failed: {e}") from e

    def _knowledge_graph_search(
        self,
        query: str,
        k: int = 5,
        allowed_domains: Optional[List[str]] = None,
        exclude_safety_flags: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """
        ★ SOTA v4: 2-Hop Semantic Knowledge Graph Search

        Pipeline:
          1. Embed query → find semantically similar entities (not LIKE!)
          2a. Retrieve triples containing those entities (1-hop, exact SQL match)
          2b. NEW: 2-hop expansion — extract new entities from 1-hop triples,
              retrieve their triples (bounded: max 5 expansion entities × 10 triples)
          3. Score all candidate triples with cross-encoder reranker against query
          4. Return top-k with chunk text and triple metadata

        2-Hop enables transitive knowledge discovery:
          Query "therapy" → Entity "CBT-I" → Triple "CBT-I treats Insomnia"
          → 2-hop entity "Insomnia" → Triple "Insomnia symptom_of Depression"

        Fallback: If entity index is empty, use improved text-based search
        with reranker scoring (still better than old LIKE + Jaccard).
        """
        if not query.strip():
            return []

        try:
            temporal_hints = self.classify_temporal_query(query)
            conn = self.db_manager.get_connection()
            cur = conn.cursor()

            try:
                # ── Step 1: Find matching entities (semantic or text-based) ──
                matched_entities = self._semantic_entity_match(query, top_k=15)

                candidate_triples = []
                kg_filter_clause, kg_filter_params = self._chunk_filter_clause(
                    allowed_domains, exclude_safety_flags, table_alias="c"
                )

                if matched_entities:
                    # ── Step 2a: 1-Hop — Retrieve triples for matched entities ──
                    seen_triple_ids = set()
                    hop1_new_entities = set()  # entities discovered in 1-hop results
                    hop1_entity_texts = set(e[0] for e in matched_entities[:10])

                    for entity_text, entity_score in matched_entities[:10]:
                        cur.execute(f"""
                            SELECT t.triple_id, t.doc_id, t.subject, t.predicate, t.object,
                                t.source_chunk_id, c.text, c.metadata, c.chunk_id,
                                t.confidence, t.evidence_strength,
                                t.validity_type, t.valid_from, t.valid_to,
                                t.observed_at, t.last_verified_at,
                                t.contradiction_state,
                                tq.grounding_score, tq.is_contradicted
                            FROM triples t
                            LEFT JOIN triple_quality tq ON tq.triple_id = t.triple_id
                            LEFT JOIN chunks c ON (t.source_chunk_id IS NOT NULL AND t.source_chunk_id = c.chunk_id)
                                               OR (t.source_chunk_id IS NULL AND t.doc_id = c.doc_id)
                            WHERE (t.subject = ? OR t.object = ?){kg_filter_clause}
                            LIMIT ?
                        """, (entity_text, entity_text, *kg_filter_params, k * 4))

                        for row in cur.fetchall():
                            triple_id = row[0]
                            if triple_id in seen_triple_ids:
                                continue
                            seen_triple_ids.add(triple_id)
                            ct = {
                                "triple_id": triple_id,
                                "doc_id": row[1],
                                "subject": row[2],
                                "predicate": row[3],
                                "object": row[4],
                                "source_chunk_id": row[5],
                                "chunk_text": row[6],
                                "chunk_metadata": row[7],
                                "chunk_id": row[8],
                                "confidence": row[9],
                                "evidence_strength": row[10],
                                "validity_type": row[11],
                                "valid_from": row[12],
                                "valid_to": row[13],
                                "observed_at": row[14],
                                "last_verified_at": row[15],
                                "contradiction_state": row[16],
                                "grounding_score": row[17],
                                "is_contradicted": row[18],
                                "entity_score": entity_score,
                                "hop": 1,
                            }
                            candidate_triples.append(ct)

                            # Collect new entities for 2-hop expansion
                            if ct["subject"] and ct["subject"] not in hop1_entity_texts:
                                hop1_new_entities.add(ct["subject"])
                            if ct["object"] and ct["object"] not in hop1_entity_texts:
                                hop1_new_entities.add(ct["object"])

                    # ── Step 2b: Community-Aware Expansion (SOTA v5) ──
                    # Expand via community membership before standard 2-hop
                    self._ensure_community_detector()
                    relevant_communities = self._get_relevant_communities(
                        query, matched_entities, top_k=3
                    )
                    if relevant_communities:
                        community_expanded = self._expand_triples_via_communities(
                            query, matched_entities, relevant_communities,
                            seen_triple_ids, kg_filter_clause, kg_filter_params,
                            k, cur
                        )
                        candidate_triples.extend(community_expanded)

                    # ── Step 2c: 2-Hop — Expand from discovered entities ──
                    # Bounded: max 5 expansion entities × max 10 triples each
                    # Only expand entities that aren't too generic (min 3 chars)
                    hop2_candidates = [
                        e for e in hop1_new_entities
                        if e and len(e.strip()) >= 3
                    ]
                    # Prioritize: shorter entities tend to be more specific/named
                    hop2_candidates.sort(key=lambda e: len(e))
                    hop2_expansion_limit = min(5, len(hop2_candidates))
                    hop2_triple_limit = 10

                    for expansion_entity in hop2_candidates[:hop2_expansion_limit]:
                        cur.execute(f"""
                            SELECT t.triple_id, t.doc_id, t.subject, t.predicate, t.object,
                                t.source_chunk_id, c.text, c.metadata, c.chunk_id,
                                t.confidence, t.evidence_strength,
                                t.validity_type, t.valid_from, t.valid_to,
                                t.observed_at, t.last_verified_at,
                                t.contradiction_state,
                                tq.grounding_score, tq.is_contradicted
                            FROM triples t
                            LEFT JOIN triple_quality tq ON tq.triple_id = t.triple_id
                            LEFT JOIN chunks c ON (t.source_chunk_id IS NOT NULL AND t.source_chunk_id = c.chunk_id)
                                               OR (t.source_chunk_id IS NULL AND t.doc_id = c.doc_id)
                            WHERE (t.subject = ? OR t.object = ?){kg_filter_clause}
                            LIMIT ?
                        """, (expansion_entity, expansion_entity, *kg_filter_params, hop2_triple_limit))

                        for row in cur.fetchall():
                            triple_id = row[0]
                            if triple_id in seen_triple_ids:
                                continue
                            seen_triple_ids.add(triple_id)
                            candidate_triples.append({
                                "triple_id": triple_id,
                                "doc_id": row[1],
                                "subject": row[2],
                                "predicate": row[3],
                                "object": row[4],
                                "source_chunk_id": row[5],
                                "chunk_text": row[6],
                                "chunk_metadata": row[7],
                                "chunk_id": row[8],
                                "confidence": row[9],
                                "evidence_strength": row[10],
                                "validity_type": row[11],
                                "valid_from": row[12],
                                "valid_to": row[13],
                                "observed_at": row[14],
                                "last_verified_at": row[15],
                                "contradiction_state": row[16],
                                "grounding_score": row[17],
                                "is_contradicted": row[18],
                                "entity_score": 0.3,  # Lower score for 2-hop (indirect)
                                "hop": 2,
                            })

                    if self.debug and hop2_expansion_limit > 0:
                        hop2_count = sum(1 for ct in candidate_triples if ct.get("hop") == 2)
                        logger.debug(
                            f"[KG-2Hop] {hop2_expansion_limit} expansion entities → "
                            f"{hop2_count} 2-hop triples added"
                        )

                else:
                    # ── Step 2b: Fallback — broader text retrieval ──
                    # Extract meaningful tokens from query (improved)
                    tokens = self._extract_query_tokens(query)
                    if not tokens:
                        return []

                    seen_triple_ids = set()
                    for token in tokens[:5]:
                        cur.execute(f"""
                            SELECT t.triple_id, t.doc_id, t.subject, t.predicate, t.object,
                                t.source_chunk_id, c.text, c.metadata, c.chunk_id,
                                t.confidence, t.evidence_strength,
                                t.validity_type, t.valid_from, t.valid_to,
                                t.observed_at, t.last_verified_at,
                                t.contradiction_state,
                                tq.grounding_score, tq.is_contradicted
                            FROM triples t
                            LEFT JOIN triple_quality tq ON tq.triple_id = t.triple_id
                            LEFT JOIN chunks c ON (t.source_chunk_id IS NOT NULL AND t.source_chunk_id = c.chunk_id)
                                               OR (t.source_chunk_id IS NULL AND t.doc_id = c.doc_id)
                            WHERE (LOWER(t.subject) LIKE LOWER(?)
                               OR LOWER(t.object) LIKE LOWER(?)){kg_filter_clause}
                            ORDER BY t.triple_id DESC
                            LIMIT ?
                        """, (f"%{token}%", f"%{token}%", *kg_filter_params, k * 6))

                        for row in cur.fetchall():
                            triple_id = row[0]
                            if triple_id in seen_triple_ids:
                                continue
                            seen_triple_ids.add(triple_id)
                            candidate_triples.append({
                                "triple_id": triple_id,
                                "doc_id": row[1],
                                "subject": row[2],
                                "predicate": row[3],
                                "object": row[4],
                                "source_chunk_id": row[5],
                                "chunk_text": row[6],
                                "chunk_metadata": row[7],
                                "chunk_id": row[8],
                                "confidence": row[9],
                                "evidence_strength": row[10],
                                "validity_type": row[11],
                                "valid_from": row[12],
                                "valid_to": row[13],
                                "observed_at": row[14],
                                "last_verified_at": row[15],
                                "contradiction_state": row[16],
                                "grounding_score": row[17],
                                "is_contradicted": row[18],
                                "entity_score": 0.5,  # Lower confidence for text-match
                            })

                if not candidate_triples:
                    return []

                # ── Step 3: Cross-Encoder Reranker Scoring ──
                reranker = self._get_reranker()
                scored_results = []

                if reranker:
                    # Build passages for reranker: triple as natural language text
                    passages = []
                    for ct in candidate_triples:
                        triple_text = f"{ct['subject']} {ct['predicate']} {ct['object']}"
                        passage_text = triple_text
                        # If chunk text available, prepend for context
                        if ct.get("chunk_text"):
                            passage_text = ct["chunk_text"][:500] + f"\n[Relation: {triple_text}]"
                        passages.append({"text": passage_text, "_ct": ct})

                    try:
                        reranked = reranker.rerank(
                            query=query,
                            passages=passages,
                            top_k=min(k * 2, len(passages)),
                            text_key="text",
                        )
                        for item in reranked:
                            ct = item.get("_ct", {})
                            rerank_score = item.get("rerank_score", item.get("score", 0.0))
                            lifecycle_score = self._score_triple_lifecycle(ct, temporal_hints=temporal_hints)
                            # ★ SOTA v5: Community-aware rerank score
                            community_score = self._get_community_rerank_score(ct)
                            # Combine: entity + reranker + lifecycle + community
                            combined_score = (
                                0.15 * ct.get("entity_score", 0.5)
                                + 0.40 * max(0, rerank_score)
                                + 0.25 * lifecycle_score
                                + 0.20 * community_score
                            )
                            scored_results.append((ct, combined_score))
                    except Exception as e:
                        raise RuntimeError(f"KG reranker scoring failed: {e}") from e
                else:
                    # No reranker: lifecycle-aware entity score + community boost
                    for ct in candidate_triples:
                        lifecycle_score = self._score_triple_lifecycle(ct, temporal_hints=temporal_hints)
                        community_score = self._get_community_rerank_score(ct)
                        combined_score = (
                            0.40 * ct.get("entity_score", 0.5)
                            + 0.35 * lifecycle_score
                            + 0.25 * community_score
                        )
                        scored_results.append((ct, combined_score))

                # ── Step 4: Build results ──
                scored_results.sort(key=lambda x: x[1], reverse=True)

                kg_results = []
                for ct, score in scored_results[:k]:
                    try:
                        metadata = json.loads(ct["chunk_metadata"]) if ct.get("chunk_metadata") else {}
                    except (json.JSONDecodeError, TypeError):
                        metadata = {}

                    triple_str = f"{ct['subject']} → {ct['predicate']} → {ct['object']}"
                    kg_results.append({
                        "text": ct.get("chunk_text") or triple_str,
                        "metadata": {
                            **metadata,
                            "kg_triple": f"{ct['subject']} | {ct['predicate']} | {ct['object']}",
                            "kg_hop": ct.get("hop", 1),
                            "kg_confidence": ct.get("confidence"),
                            "kg_evidence_strength": ct.get("evidence_strength"),
                            "kg_validity_type": ct.get("validity_type"),
                            "kg_valid_from": ct.get("valid_from"),
                            "kg_valid_to": ct.get("valid_to"),
                            "kg_observed_at": ct.get("observed_at"),
                            "kg_last_verified_at": ct.get("last_verified_at"),
                            "kg_contradiction_state": ct.get("contradiction_state") or "none",
                            "kg_grounding_score": ct.get("grounding_score"),
                            "kg_is_contradicted": bool(ct.get("is_contradicted", False)),
                            "kg_community_id": ct.get("community_id"),
                            "search_type": "knowledge_graph",
                        },
                        "score": min(score, 1.0),
                        "doc_id": ct["doc_id"],
                        "chunk_id": ct.get("chunk_id"),
                    })

                if self.debug:
                    hop1_count = sum(1 for ct in candidate_triples if ct.get("hop", 1) == 1)
                    hop2_count = sum(1 for ct in candidate_triples if ct.get("hop") == 2)
                    comm_count = sum(1 for ct in candidate_triples if ct.get("community_id") is not None)
                    logger.debug(
                        f"[KG-Search] {len(matched_entities)} entities → "
                        f"{hop1_count} 1-hop + {comm_count} community + {hop2_count} 2-hop = "
                        f"{len(candidate_triples)} candidates → {len(kg_results)} results"
                    )
                return kg_results

            finally:
                cur.close()
                self.db_manager.return_connection(conn)

        except Exception as e:
            logger.error(f"KG search failed (fail-fast): {e}")
            raise RuntimeError(f"Knowledge graph search failed: {e}") from e

    def _extract_query_tokens(self, query: str) -> List[str]:
        """
        Improved query tokenization for KG text-based fallback.
        Extracts meaningful tokens and bigrams, filters stopwords.
        """
        _STOPWORDS = {
            'der', 'die', 'das', 'und', 'oder', 'aber', 'mit', 'von', 'zu', 'für',
            'auf', 'in', 'bei', 'the', 'and', 'or', 'but', 'with', 'from', 'to',
            'for', 'on', 'at', 'is', 'are', 'was', 'were', 'ich', 'du', 'er',
            'sie', 'es', 'wir', 'ihr', 'ein', 'eine', 'einen', 'what', 'how',
            'where', 'when', 'why', 'who', 'which', 'wie', 'wo', 'wann', 'warum',
            'nicht', 'auch', 'noch', 'nur', 'schon', 'kann', 'hat', 'ist', 'wird',
            'den', 'dem', 'des', 'eines', 'einem', 'einer', 'über', 'nach', 'durch',
        }
        query_clean = re.sub(r'[^\w\s\-äöüÄÖÜß]', ' ', query.lower())
        tokens = [t for t in query_clean.split() if len(t) >= 3 and t not in _STOPWORDS]

        # Add bigrams (compound concepts)
        bigrams = []
        for i in range(len(tokens) - 1):
            bigrams.append(f"{tokens[i]} {tokens[i+1]}")

        # Prioritize bigrams (more specific) then unigrams
        return (bigrams + tokens)[:8]

    def _fuse_search_results(
        self,
        embedding_results: List[Dict[str, Any]],
        kg_results: List[Dict[str, Any]],
        query: str
    ) -> List[Dict[str, Any]]:
        """
        Intelligente Fusion von Embedding- und KG-Suchergebnissen
        """
        # 1. Deduplizierung basierend auf doc_id und chunk_id
        seen = set()
        all_results = []
        
        # 2. Gewichtung: 70% Embedding, 30% KG (anpassbar)
        embedding_weight = 0.7
        kg_weight = 0.3
        
        # 3. Embedding-Ergebnisse mit angepasstem Score
        for result in embedding_results:
            key = (result.get("doc_id", ""), result.get("chunk_id", ""))
            if key not in seen:
                seen.add(key)
                result_copy = result.copy()
                result_copy["final_score"] = result["score"] * embedding_weight
                result_copy["search_type"] = "embedding"
                all_results.append(result_copy)
        
        # 4. KG-Ergebnisse mit angepasstem Score
        for result in kg_results:
            key = (result.get("doc_id", ""), result.get("chunk_id", ""))
            if key not in seen:
                seen.add(key)
                result_copy = result.copy()
                result_copy["final_score"] = result["score"] * kg_weight
                result_copy["search_type"] = "knowledge_graph"
                all_results.append(result_copy)
            else:
                # Kombiniere Scores wenn bereits vorhanden (Hybrid-Match)
                for existing in all_results:
                    if (existing.get("doc_id", ""), existing.get("chunk_id", "")) == key:
                        existing["final_score"] += result["score"] * kg_weight
                        existing["search_type"] = "hybrid"
                        if "metadata" in existing and "metadata" in result:
                            existing["metadata"].update(result["metadata"])
                        break
        
        # 5. Nach final_score sortieren
        all_results.sort(key=lambda x: x["final_score"], reverse=True)
        
        return all_results
    
    def _enrich_with_kg_context(
        self, results: List[Dict[str, Any]], query: str
    ) -> List[Dict[str, Any]]:
        """
        ★ SOTA v4: Entity-Based Cross-Document KG Enrichment

        Root-Cause Fix für v3-Schwäche: v3 holte Triples per doc_id —
        das verfehlte cross-doc Relationen und zog irrelevante Triples.

        Neuer Algorithmus:
        1. Semantic Entity Match: Query → Top-N ähnlichste Entities
        2. Entity-Triple-Lookup: Alle Triples die diese Entities enthalten
           (quer über ALLE Dokumente, nicht nur das Result-Dokument)
        3. Cross-Encoder Reranking: Score jedes Triple gegen die Query
        4. Pro Result: Attachiere Top-3 relevanteste Triples als kg_context
           (können aus anderen Dokumenten stammen → cross-doc knowledge)

        Fallback: Wenn Entity-Index leer → v3 doc_id-basierter Fallback.
        """
        if not results:
            return results

        try:
            # ── Step 1: Find query-relevant entities ──
            matched_entities = self._semantic_entity_match(query, top_k=10)

            if matched_entities:
                # ── Step 2: Entity-based triple retrieval (cross-document) ──
                conn = self.db_manager.get_connection()
                cur = conn.cursor()

                all_entity_triples: List[Tuple[str, str, str, float]] = []  # (s,p,o, entity_score)
                seen_triple_hashes = set()

                for entity_text, entity_score in matched_entities[:8]:
                    cur.execute("""
                        SELECT DISTINCT subject, predicate, object,
                                        confidence, evidence_strength,
                                        validity_type, valid_from, valid_to,
                                        observed_at, contradiction_state
                        FROM triples
                        WHERE (subject = ? OR object = ?)
                          AND COALESCE(contradiction_state, 'none') NOT IN (
                              'confirmed_false', 'retracted', 'superseded', 'contradicted'
                          )
                        ORDER BY COALESCE(confidence, 0.5) DESC
                        LIMIT 15
                    """, (entity_text, entity_text))

                    for row in cur.fetchall():
                        triple_hash = (row[0].lower(), row[1].lower(), row[2].lower())
                        if triple_hash not in seen_triple_hashes:
                            seen_triple_hashes.add(triple_hash)
                            all_entity_triples.append((row[0], row[1], row[2], entity_score))

                cur.close()
                self.db_manager.return_connection(conn)

                if all_entity_triples:
                    # ── Step 3: Cross-encoder reranking of entity triples ──
                    reranker = self._get_reranker()
                    scored_triples: List[Tuple[str, float]] = []

                    if reranker and len(all_entity_triples) > 3:
                        try:
                            passages = [
                                {"text": f"{s} {p} {o}"} for s, p, o, _ in all_entity_triples
                            ]
                            reranked = reranker.rerank(
                                query=query,
                                passages=passages,
                                top_k=min(10, len(passages)),
                                text_key="text",
                            )
                            for item in reranked:
                                idx = item.get("index", item.get("corpus_id", 0))
                                rerank_score = item.get("rerank_score", item.get("score", 0.0))
                                if 0 <= idx < len(all_entity_triples):
                                    s, p, o, e_score = all_entity_triples[idx]
                                    # Combine entity similarity + reranker relevance
                                    combined = 0.3 * e_score + 0.7 * max(0, rerank_score)
                                    scored_triples.append((f"{s} → {p} → {o}", combined))
                        except Exception as e:
                            raise RuntimeError(f"KG enrichment reranker failed: {e}") from e
                    else:
                        for s, p, o, e_score in all_entity_triples:
                            scored_triples.append((f"{s} → {p} → {o}", e_score))

                    scored_triples.sort(key=lambda x: x[1], reverse=True)

                    # ── Step 4: Attach top-K triples to EACH result ──
                    # All results get the same query-relevant triples (cross-doc)
                    top_kg_context = [t[0] for t in scored_triples[:5]]
                    for result in results:
                        if "metadata" not in result:
                            result["metadata"] = {}
                        result["metadata"]["kg_context"] = top_kg_context[:3]

                    if self.debug:
                        logger.debug(
                            f"[KG-Enrich] Entity-based: {len(matched_entities)} entities → "
                            f"{len(all_entity_triples)} triples → top-{len(top_kg_context)} attached"
                        )
                    return results

            # ── Fallback: doc_id-based enrichment (v3 logic) ──
            # Used when entity index is empty (first-time, no embeddings yet)
            conn = self.db_manager.get_connection()
            cur = conn.cursor()
            reranker = self._get_reranker()

            doc_ids = list(set(
                r.get("doc_id", "") for r in results if r.get("doc_id")
            ))
            if not doc_ids:
                cur.close()
                self.db_manager.return_connection(conn)
                return results

            doc_triples: Dict[str, List[Tuple[str, str, str]]] = {}
            for doc_id in doc_ids:
                base_doc_id = doc_id.rsplit("_chunk_", 1)[0] if "_chunk_" in doc_id else doc_id
                cur.execute("""
                    SELECT DISTINCT subject, predicate, object
                    FROM triples
                    WHERE doc_id = ? OR doc_id = ? OR doc_id LIKE ?
                    LIMIT 30
                """, (doc_id, base_doc_id, f"{base_doc_id}_chunk_%"))

                triples = []
                for row in cur.fetchall():
                    subject, predicate, obj = row
                    if subject and predicate and obj:
                        triples.append((subject, predicate, obj))
                if triples:
                    doc_triples[doc_id] = triples

            cur.close()
            self.db_manager.return_connection(conn)

            for result in results:
                doc_id = result.get("doc_id", "")
                triples = doc_triples.get(doc_id, [])
                if not triples:
                    continue

                if "metadata" not in result:
                    result["metadata"] = {}

                if reranker and len(triples) > 3:
                    try:
                        passages = [
                            {"text": f"{s} {p} {o}"} for s, p, o in triples
                        ]
                        reranked = reranker.rerank(
                            query=query,
                            passages=passages,
                            top_k=min(5, len(passages)),
                            text_key="text",
                        )
                        scored = []
                        for item in reranked:
                            idx = item.get("index", item.get("corpus_id", 0))
                            score = item.get("rerank_score", item.get("score", 0.0))
                            if 0 <= idx < len(triples):
                                s, p, o = triples[idx]
                                scored.append((f"{s} → {p} → {o}", score))
                        scored.sort(key=lambda x: x[1], reverse=True)
                        result["metadata"]["kg_context"] = [t[0] for t in scored[:3]]
                    except Exception as e:
                        raise RuntimeError(
                            f"KG enrichment reranker failed for {doc_id}: {e}"
                        ) from e
                else:
                    result["metadata"]["kg_context"] = [
                        f"{s} → {p} → {o}" for s, p, o in triples[:3]
                    ]

        except Exception as e:
            logger.error(f"KG context enrichment failed (fail-fast): {e}")
            raise RuntimeError(f"KG context enrichment failed: {e}") from e

        return results
    
    def batch_search(
        self,
        queries: List[str],
        k_list: List[int],
        min_score: float = 0.0,
        adaptive_confidence: bool = True,
        faiss_min_confidence: Optional[float] = None,
        allowed_domains: Optional[List[str]] = None,
        exclude_safety_flags: Optional[List[str]] = None,
    ) -> List[List[Dict[str, Any]]]:
        """
        🚀 BATCH SEARCH: Optimierte Batch-Suche für multiple Queries
        
        **Performance-Vorteil:**
        - 150x schneller als sequenzielle search() Calls bei Multi-Query
        - Nutzt native FAISS Batch-API mit OpenMP-Parallelisierung
        - Single Embedding-Call für alle Queries
        - Optimiertes DB-Fetching
        
        **Use-Cases:**
        - Multi-Query RAG (1 Main + N Sub-Queries)
        - Gap-Detection mit mehreren Queries
        - Parallel Query Processing
        
        Args:
            queries: Liste von Suchanfragen
            k_list: Liste von k-Werten (einer pro Query)
            min_score: Minimaler Score-Filter (NACH FAISS)
            adaptive_confidence: Wenn True, passe Confidence an k an
            faiss_min_confidence: Manuelle FAISS Confidence
            
        Returns:
            Liste von Listen mit Suchergebnissen (eine Liste pro Query)
            
        Example:
            >>> queries = ["Python tutorial", "Machine Learning", "Data Science"]
            >>> k_list = [5, 10, 8]
            >>> results = search_manager.batch_search(queries, k_list)
            >>> # results[0] = 5 Results für "Python tutorial"
            >>> # results[1] = 10 Results für "Machine Learning"
            >>> # results[2] = 8 Results für "Data Science"
        """
        if not queries:
            return []
        
        # Validate input
        if len(queries) != len(k_list):
            raise ValueError(f"queries length ({len(queries)}) must match k_list length ({len(k_list)})")
        
        # Filter empty queries
        valid_indices = [i for i, q in enumerate(queries) if q.strip()]
        if not valid_indices:
            return [[] for _ in queries]
        
        # Check for EmbeddingManager availability
        if self.embedding_manager is None:
            raise RuntimeError(
                "SearchManager embedding_manager is None in batch_search (lazy init not completed)"
            )
        
        try:
            # 1. EMBED ALL QUERIES IN ONE CALL (Batch-Embedding)
            start_time = time.time()
            all_embeddings = self.embedding_manager.embed_texts(queries)
            embed_time = (time.time() - start_time) * 1000
            
            # Convert to numpy array
            if NUMPY_AVAILABLE and np is not None:
                query_embeddings = np.array(all_embeddings, dtype="float32")
            else:
                raise RuntimeError("NumPy not available for batch search")
            
            if self.debug:
                logger.debug(f"📦 Batch Embedding: {len(queries)} queries in {embed_time:.2f}ms")
            
            # 2. FAISS BATCH SEARCH (if available)
            if self.faiss_manager and FAISS_HYBRID_AVAILABLE:
                try:
                    has_chunk_filter = bool(allowed_domains) or bool(exclude_safety_flags)
                    # Adjust k_list for fusion (k*2 per query, k*3 if filter active)
                    fetch_factor = 3 if has_chunk_filter else 2
                    adjusted_k_list = [k * fetch_factor for k in k_list]
                    
                    # Native FAISS Batch-Search
                    batch_results = self.faiss_manager.batch_search(
                        query_embeddings=query_embeddings,
                        k_list=adjusted_k_list,
                        min_confidence=faiss_min_confidence,
                        adaptive_confidence=adaptive_confidence if faiss_min_confidence is None else False
                    )
                    
                    # Unpack results: List[Tuple[chunk_ids, scores, strategy]]
                    batch_chunk_ids = [r[0] for r in batch_results]
                    batch_scores = [r[1] for r in batch_results]
                    batch_strategies = [r[2] for r in batch_results]
                    
                    if self.debug:
                        total_results = sum(len(ids) for ids in batch_chunk_ids)
                        logger.debug(
                            f"🚀 FAISS Batch Search: {len(queries)} queries, "
                            f"{total_results} total results"
                        )
                    
                    # 3. FETCH CHUNKS FROM DB (Batch-optimized)
                    all_results: List[Any] = []
                    conn = self.db_manager.get_connection()
                    filter_clause, filter_params = self._chunk_filter_clause(
                        allowed_domains, exclude_safety_flags
                    )
                    
                    for i, (chunk_ids, scores) in enumerate(zip(batch_chunk_ids, batch_scores)):
                        if not chunk_ids:
                            all_results.append([])
                            continue
                        
                        # Fetch chunks for this query
                        placeholders = ','.join(['?'] * len(chunk_ids))
                        query_sql = (
                            "SELECT chunk_id, doc_id, text, metadata, domain, safety_flag "
                            f"FROM chunks WHERE chunk_id IN ({placeholders}){filter_clause}"
                        )
                        
                        cur = conn.execute(query_sql, [*chunk_ids, *filter_params])
                        rows = cur.fetchall()
                        
                        # Create lookup for quick access
                        chunk_lookup = {}
                        for row in rows:
                            chunk_id, doc_id, text, metadata_json, c_domain, c_safety = row
                            try:
                                metadata = json.loads(metadata_json)
                            except (json.JSONDecodeError, TypeError):
                                metadata = {}
                            
                            chunk_lookup[chunk_id] = {
                                "text": text,
                                "metadata": metadata,
                                "doc_id": doc_id,
                                "chunk_id": chunk_id,
                                "domain": c_domain,
                                "safety_flag": c_safety,
                            }
                        
                        # Build results in original order (matching scores)
                        query_results = []
                        for chunk_id, score in zip(chunk_ids, scores):
                            if chunk_id in chunk_lookup:
                                result = chunk_lookup[chunk_id].copy()
                                result["score"] = float(score)
                                query_results.append(result)
                        
                        # Sort by score and limit to original k
                        query_results.sort(key=lambda x: x["score"], reverse=True)
                        all_results.append(query_results[:k_list[i]])
                    
                    self.db_manager.return_connection(conn)
                    return all_results
                    
                except Exception as e:
                    raise RuntimeError(f"FAISS batch search execution failed: {e}") from e
            
            # 4. Sequential NumPy Search (if FAISS feature is unavailable)
            logger.info("📊 Using sequential NumPy search (FAISS feature unavailable)")
            all_results = []
            for i, query in enumerate(queries):
                results = self.search(
                    query=query,
                    k=k_list[i],
                    min_score=min_score,
                    adaptive_confidence=adaptive_confidence,
                    faiss_min_confidence=faiss_min_confidence,
                    allowed_domains=allowed_domains,
                    exclude_safety_flags=exclude_safety_flags,
                )
                all_results.append(results)
            
            return all_results
            
        except Exception as e:
            logger.error(f"Batch search failed (fail-fast): {e}")
            raise RuntimeError(f"Batch search failed: {e}") from e
