"""
SOTA RAG Pipeline -- HyDE + CRAG + Contextual Compression
==========================================================

Implementiert State-of-the-Art Retrieval-Augmented Generation Techniken
für den ReAct Agent.

Komponenten:
  1. **HyDE** (Hypothetical Document Embeddings) [Gao et al. 2023]
     → Generiert hypothetische Antwort, embeddet diese für symmetrischen Recall
     
  2. **CRAG** (Corrective RAG) [Yan et al. 2024]
     → Bewertet abgerufene Chunks auf Relevanz, filtert irrelevante
     
  3. **Contextual Compression** [Xu et al. 2024]
     → Komprimiert Chunks auf relevante Passagen vor LLM-Injection
     
  4. **RAG Persist** (Write-Through Cache)
     → Persistiert Web-Ergebnisse asynchron mit Full-Content-Extraction

Alle Komponenten standardmäßig aktiviert, konfigurierbar über Flags.
"""

from __future__ import annotations

import logging
import re as _re
import threading
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Set, Tuple

import utils.web_compliance as web_compliance

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════════════
# Sentence Splitter for Extractive Compression
# ═══════════════════════════════════════════════════════════════════════════════

# Pre-compiled regex: split on sentence-ending punctuation followed by
# whitespace + uppercase letter (handles German Ä/Ö/Ü + English).
# Preserves abbreviations like "z.B.", "Dr.", "etc." by requiring uppercase
# after the split boundary.
_SENTENCE_SPLIT_RE = _re.compile(r'(?<=[.!?])\s+(?=[A-ZÄÖÜ])')


def _split_sentences(text: str) -> List[str]:
    """Split text into sentences (DE + EN safe, no external deps)."""
    parts = _SENTENCE_SPLIT_RE.split(text)
    return [s.strip() for s in parts if s.strip() and len(s.strip()) > 10]


# ═══════════════════════════════════════════════════════════════════════════════
# CRAG Relevanz-Kategorien
# ═══════════════════════════════════════════════════════════════════════════════

HYDE_PROMPT = """Beantworte die folgende Frage hypothetisch in 2-3 informativen Sätzen.
Schreibe so, als wäre es ein Abschnitt aus einem Fachtext oder Wikipedia-Artikel.
Verwende spezifische Fakten und Begriffe, auch wenn du dir nicht sicher bist.

Frage: {query}

Hypothetische Antwort:"""


class RAGPipeline:
    """SOTA RAG Pipeline mit HyDE, CRAG und Contextual Compression.
    
    Orchestriert den kompletten RAG-Prefetch-Zyklus:
        Query → [HyDE] → Retrieve → [CRAG Filter] → [Compress] → Context
    
    Sowie den Write-Through-Pfad:
        Web Results → [Background Thread] → Full-Content-Extraction → RAG Store
    
    Args:
        tool_manager: ToolManager mit RAG-Store-Zugriff
        model_loader: ModelLoader für LLM-Calls (HyDE, CRAG, Compression)
        hyde_enabled: HyDE aktivieren (Default: True)
        crag_enabled: CRAG Chunk-Bewertung aktivieren (Default: True)
        compression_enabled: Contextual Compression aktivieren (Default: True)
        persist_enabled: Web-zu-RAG Persistierung aktivieren (Default: True)
        prefetch_k: Anzahl RAG-Ergebnisse beim Prefetch (Default: 8)
        prefetch_min_score: Minimaler Score für Prefetch-Ergebnisse (Default: 0.3)
        crag_relevance_threshold: Min. Anteil relevanter Chunks (Default: 0.3)
    """
    
    def __init__(
        self,
        tool_manager,
        model_loader,
        hyde_enabled: bool = True,
        crag_enabled: bool = True,
        compression_enabled: bool = True,
        persist_enabled: bool = True,
        reranking_enabled: bool = True,
        prefetch_k: int = 8,
        prefetch_min_score: float = 0.3,
        crag_relevance_threshold: float = 0.3,
    ):
        self.tool_manager = tool_manager
        self.model_loader = model_loader
        
        # Feature Flags (alle standardmäßig AN)
        self.hyde_enabled = hyde_enabled
        self.crag_enabled = crag_enabled
        self.compression_enabled = compression_enabled
        self.persist_enabled = persist_enabled
        self.reranking_enabled = reranking_enabled
        
        # Prefetch Config
        self.prefetch_k = prefetch_k
        self.prefetch_min_score = prefetch_min_score
        self.crag_relevance_threshold = crag_relevance_threshold
        
        # Cross-Encoder Reranker (Lazy-Init Singleton)
        # SOTA (2026-08-28): Kein init-time is_available-Check mehr. Der Reranker
        # ist LAZY (lädt erst beim ersten rerank()-Aufruf via _ensure_loaded()).
        # Ein is_available-Check hier würde False sehen (Modell noch nicht geladen)
        # und Reranking dauerhaft deaktivieren — das Lazy-Loading würde zunichte
        # gemacht. Stattdessen halten wir das Singleton und verlassen uns auf die
        # graceful-Degradation in reranker.rerank(): lädt bei Bedarf, gibt die
        # Original-Reihenfolge zurück, wenn das Modell nicht verfügbar ist.
        self._reranker = None
        if reranking_enabled:
            try:
                from agent.reranker import get_reranker
                self._reranker = get_reranker()
            except Exception as e:
                logger.warning(f"[RAG] Reranker-Init fehlgeschlagen: {e} -- Reranking deaktiviert")
                self.reranking_enabled = False
        
        # Persist State (URL-Deduplication, Background Threading)
        self._processed_urls: Set[str] = set()
        self._persist_lock = threading.Lock()
        self._persist_threads: List[threading.Thread] = []
        
        # Stats (thread-safe via Lock)
        self._stats_lock = threading.Lock()
        self.stats = {
            "prefetch_calls": 0,
            "prefetch_hits": 0,
            "hyde_calls": 0,
            "crag_filtered": 0,
            "rerank_calls": 0,
            "compression_calls": 0,
            "persist_calls": 0,
            "persist_urls": 0,
        }
        
        # Web-Content Retention (2026-08-30): Einmaliger Prune abgelaufener
        # Web-sourced Records im Hintergrund (daemon, non-blocking, fail-soft).
        # Siehe docs/18_LEGAL_WEB_PERSIST.md. WEB_RETENTION_DAYS=0 → kein Prune.
        if persist_enabled:
            threading.Thread(
                target=self._startup_web_retention_prune,
                daemon=True,
                name="rag-web-retention",
            ).start()
        
        flags = []
        if hyde_enabled: flags.append("HyDE")
        if crag_enabled: flags.append("CRAG")
        if self.reranking_enabled: flags.append("Rerank")
        if compression_enabled: flags.append("Compression")
        if persist_enabled: flags.append("Persist")
        logger.info(f"✅ RAGPipeline initialisiert: [{', '.join(flags)}], k={prefetch_k}")

    def _startup_web_retention_prune(self) -> None:
        """Einmaliger Web-Retention-Prune beim Pipeline-Start (daemon-thread).

        Non-blocking und fail-soft: Fehlschläge werden geloggt und haben
        keinerlei Auswirkung auf die Pipeline. Nur web-derived Records
        (``source_type`` startet mit ``web``) werden berücksichtigt — lokale
        Dokumente, KI-Generiertes und Records ohne verwertbaren Timestamp
        bleiben unberührt. FAISS/BM25-Staleness wird über den bestehenden
        ``auto_rebuild_on_stale``-Mechanismus aufgelöst.
        """
        try:
            if not web_compliance.is_compliance_enabled():
                return
            rag_store = self._get_rag_store()
            if rag_store is None or not hasattr(rag_store, "prune_web_content"):
                return
            pruned = int(rag_store.prune_web_content() or 0)
            if pruned:
                logger.info(f"[RAG-RETENTION] Startup-Prune: {pruned} Web-Dokumente bereinigt")
        except Exception as exc:
            logger.warning(f"[RAG-RETENTION] Startup-Prune fehlgeschlagen (non-blocking): {exc}")

    # ═══════════════════════════════════════════════════════════════════
    # PUBLIC API: Prefetch (Read Path)
    # ═══════════════════════════════════════════════════════════════════
    
    def prefetch(self, query: str) -> Tuple[str, List[Dict[str, Any]], bool]:
        """Führt den kompletten RAG-Prefetch-Zyklus aus.

        Restructured SOTA Pipeline (Feb 2026):
            Query → [HyDE(GPU) ∥ Search(CPU)] → Search(HyDE) → BM25 → RRF
                  → Rerank(CE) → CRAG(CE-scores) → Compress(CE-extractive)

        Key architectural change:
          - CRAG and Compression now use the Cross-Encoder (CPU, ~5-20ms)
            instead of the 24B LLM (GPU, ~70s each).
          - Cross-encoders are *specifically trained* for relevance scoring
            and outperform generalist LLMs on this task (Nogueira & Cho 2019).
          - HyDE generation runs in parallel with original-query FAISS search
            (GPU generates while CPU searches).
          - Pipeline ordering: Rerank BEFORE CRAG, so CRAG can use CE scores.

        Performance impact:
          - Old: 3×LLM calls (~210s total) → New: 1×LLM call + CE scoring (~70s)
          - CRAG: 70s → <1ms (CE-score thresholding)
          - Compression: 70s → ~20ms (CE sentence scoring)

        Returns:
            (context_text, sources, needs_web)
        """
        self._inc_stat("prefetch_calls")
        start = time.perf_counter()
        needs_web = False

        rag_store = self._get_rag_store()
        if not rag_store:
            logger.debug("[RAG-PREFETCH] Kein RAG Store verfügbar → Skip")
            return "", [], False

        # ══════════════════════════════════════════════════════════════
        # Step 1: HyDE + Dual-Retrieval  (GPU ∥ CPU parallelisiert)
        # ══════════════════════════════════════════════════════════════
        raw_hits: List[Dict[str, Any]] = []
        hyde_query: Optional[str] = None
        
        # Conditional HyDE: Only activate for queries that benefit from it
        # (conceptual, multi-hop, analytical). Skip for simple factual lookups
        # where the original query already contains the exact search terms.
        use_hyde = self.hyde_enabled and self._should_use_hyde(query)

        if use_hyde:
            # Launch original-query FAISS search on CPU in background thread
            # while HyDE generates on GPU -- independent resources, zero contention.
            original_hits_box: List[Optional[List[Dict[str, Any]]]] = [None]

            def _search_original() -> None:
                try:
                    original_hits_box[0] = rag_store.search(
                        query,
                        k=self.prefetch_k,
                        min_score=self.prefetch_min_score,
                        adaptive_confidence=True,
                    )
                except Exception as exc:
                    logger.debug(f"[RAG-PREFETCH] Parallel original search failed: {exc}")

            search_thread = threading.Thread(
                target=_search_original, daemon=True, name="rag-orig-search"
            )
            search_thread.start()

            # HyDE generation on GPU (the slow part -- ~70s for 24B model)
            hyde_query = self._hyde_generate(query)

            # Wait for original search (should have finished long ago)
            search_thread.join(timeout=10.0)
            original_hits = original_hits_box[0] or []

            # Search with HyDE query (fast -- CPU/FAISS only)
            if hyde_query:
                hyde_hits = rag_store.search(
                    hyde_query,
                    k=self.prefetch_k,
                    min_score=self.prefetch_min_score,
                    adaptive_confidence=True,
                )
                if hyde_hits and original_hits:
                    raw_hits = self._rrf_merge(original_hits, hyde_hits)
                    logger.info(
                        f"[RAG-PREFETCH] HyDE dual-retrieval: "
                        f"{len(raw_hits)} merged hits (RRF)"
                    )
                else:
                    raw_hits = hyde_hits or original_hits
            else:
                raw_hits = original_hits
        else:
            # No HyDE -- simple single search
            raw_hits = rag_store.search(
                query,
                k=self.prefetch_k,
                min_score=self.prefetch_min_score,
                adaptive_confidence=True,
            ) or []

        # ══════════════════════════════════════════════════════════════
        # Step 2: BM25 Keyword Search + Hybrid Merge (Chen et al. 2024)
        # ══════════════════════════════════════════════════════════════
        bm25_hits = self._bm25_search(rag_store, query)
        if bm25_hits and raw_hits:
            raw_hits = self._rrf_merge(raw_hits, bm25_hits)
            logger.info(
                f"[RAG-PREFETCH] Hybrid BM25+Dense: "
                f"{len(raw_hits)} merged hits (RRF)"
            )
        elif bm25_hits and not raw_hits:
            raw_hits = bm25_hits

        if not raw_hits:
            elapsed = (time.perf_counter() - start) * 1000
            logger.info(f"[RAG-PREFETCH] Keine Ergebnisse ({elapsed:.0f}ms) → Skip")
            return "", [], True

        # ══════════════════════════════════════════════════════════════
        # Step 3: Cross-Encoder Reranking FIRST
        #         (provides scores for CRAG + Compression)
        # ══════════════════════════════════════════════════════════════
        if self.reranking_enabled and self._reranker and len(raw_hits) > 1:
            raw_hits = self._rerank_chunks(query, raw_hits)

        # ══════════════════════════════════════════════════════════════
        # Step 3.5: CRAG via Cross-Encoder Scores  (NO LLM CALL)
        # ══════════════════════════════════════════════════════════════
        if self.crag_enabled and len(raw_hits) > 1:
            raw_hits, needs_web = self._crag_evaluate(query, raw_hits)
            if not raw_hits:
                elapsed = (time.perf_counter() - start) * 1000
                logger.info(
                    f"[RAG-PREFETCH] CRAG: alle irrelevant ({elapsed:.0f}ms) → Skip"
                )
                return "", [], True

        # ══════════════════════════════════════════════════════════════
        # Step 4: Extractive Compression via Cross-Encoder (NO LLM CALL)
        # ══════════════════════════════════════════════════════════════
        if self.compression_enabled:
            raw_hits = self._compress_chunks(query, raw_hits)

        # ══════════════════════════════════════════════════════════════
        # Step 5: Format Context + Sources
        # ══════════════════════════════════════════════════════════════
        context_text, sources = self._format_prefetch_results(query, raw_hits)

        elapsed = (time.perf_counter() - start) * 1000
        self._inc_stat("prefetch_hits")
        logger.info(
            f"[RAG-PREFETCH] {len(raw_hits)} relevante Chunks "
            f"({'HyDE→' if self.hyde_enabled else ''}"
            f"{'Rerank→' if self.reranking_enabled else ''}"
            f"{'CRAG→' if self.crag_enabled else ''}"
            f"{'Compress' if self.compression_enabled else 'Raw'}) "
            f"in {elapsed:.0f}ms"
            f"{' [needs_web=True]' if needs_web else ''}"
        )

        return context_text, sources, needs_web

    # ═══════════════════════════════════════════════════════════════════
    # HyDE Dual-Retrieval: Reciprocal Rank Fusion (Cormack et al. 2009)
    # ═══════════════════════════════════════════════════════════════════

    def _rrf_merge(
        self,
        list_a: List[Dict[str, Any]],
        list_b: List[Dict[str, Any]],
        k: int = 60,
    ) -> List[Dict[str, Any]]:
        """Merge two ranked lists via Reciprocal Rank Fusion.

        RRF-Score = sum(1 / (k + rank_i)) über alle Listen.
        Dedupliziert nach text-Inhalt (identische Chunks aus beiden Listen).

        Die Mathematik lebt kanonisch in utils/rank_fusion.py
        (Workdoc: docs/WORKDOC_CODEBASE_AUDIT_20260828.md, Phase 2).
        Key-Logik (Text-Präfix) und First-Wins-Item bleiben unverändert;
        chunks mit leerem Text-Key wurden früher übersprungen — bleiben es.

        Args:
            list_a: Erste Rangliste (z.B. HyDE-Ergebnisse)
            list_b: Zweite Rangliste (z.B. Original-Query-Ergebnisse)
            k: RRF-Konstante (Standard: 60, wie im Original-Paper)

        Returns:
            Gemergte, nach RRF-Score sortierte Liste (max. prefetch_k)
        """
        from utils.rank_fusion import fuse_dicts

        # Chunk-ID: Text-basierter Hash (da Chunks kein stabiles ID-Feld haben)
        def _chunk_key(chunk: Dict[str, Any]) -> Optional[str]:
            text = (chunk.get("text") or chunk.get("snippet") or "")[:200]
            stripped = text.strip()
            # None → überspringen (Altverhalten); Ränge der Folgenden bleiben
            # unverändert (enumerate läuft auf der Original-Liste).
            return stripped or None

        merged = fuse_dicts([list_a, list_b], k=k, key_fn=_chunk_key)
        return merged[: self.prefetch_k]

    # ═══════════════════════════════════════════════════════════════════
    # BM25 Keyword Search (SOTA: Okapi BM25, Robertson et al. 1995)
    # ═══════════════════════════════════════════════════════════════════
    
    # Class-level BM25 index cache -- rebuilt only when corpus changes
    _bm25_index: Any = None
    _bm25_corpus_docs: Optional[List[Dict[str, Any]]] = None
    _bm25_corpus_hash: int = 0
    _bm25_lock = threading.Lock()

    # Bilingual stopwords for tokenizer (German + English)
    _BM25_STOPWORDS: Set[str] = {
        # German
        "und", "oder", "der", "die", "das", "ein", "eine", "ist",
        "sind", "war", "hat", "haben", "wird", "werden", "mit",
        "von", "für", "auf", "bei", "nach", "über", "aus",
        "wie", "was", "wer", "als", "auch", "noch", "schon",
        "den", "dem", "des", "im", "am", "zum", "zur", "vom",
        "nicht", "aber", "wenn", "dann", "nur", "ich", "du",
        "er", "sie", "es", "wir", "ihr", "man", "sich",
        "sein", "kann", "wird", "so", "da", "hier", "dort",
        # English  
        "the", "and", "for", "this", "that", "with", "from",
        "was", "were", "are", "not", "but", "can", "will",
        "has", "have", "had", "been", "its", "their", "there",
        "which", "what", "when", "where", "who", "how", "all",
        "each", "every", "both", "few", "more", "most", "other",
        "some", "such", "than", "too", "very", "just", "about",
    }

    @staticmethod
    def _bm25_tokenize(text: str) -> List[str]:
        """Tokenize text for BM25: lowercase, remove stopwords, min length 2."""
        import re
        tokens = re.findall(r'\w{2,}', text.lower())
        return [t for t in tokens if t not in RAGPipeline._BM25_STOPWORDS]

    def _build_bm25_index(self, rag_store) -> bool:
        """Build/rebuild BM25Okapi index from all chunks in the store.
        
        Uses rank_bm25.BM25Okapi with proper TF-IDF scoring and
        document length normalization (Okapi BM25, k1=1.5, b=0.75).
        
        Returns True if index was (re)built successfully.
        """
        try:
            conn = rag_store.get_connection() if hasattr(rag_store, 'get_connection') else None
            if not conn:
                return False
            
            cur = conn.cursor()
            cur.execute("SELECT text, metadata FROM chunks")
            rows = cur.fetchall()
            
            if not rows:
                return False
            
            # Check if corpus changed (hash of row count + first/last text)
            corpus_hash = hash((len(rows), rows[0][0][:50] if rows else "", rows[-1][0][:50] if rows else ""))
            if corpus_hash == RAGPipeline._bm25_corpus_hash and RAGPipeline._bm25_index is not None:
                return True  # Index is current
            
            import json as json_module
            from rank_bm25 import BM25Okapi
            
            docs: List[Dict[str, Any]] = []
            tokenized_corpus: List[List[str]] = []
            
            for text, meta_json in rows:
                if not text or not text.strip():
                    continue
                meta = {}
                if meta_json:
                    try:
                        meta = json_module.loads(meta_json) if isinstance(meta_json, str) else meta_json
                    except (json_module.JSONDecodeError, TypeError):
                        pass
                
                tokens = self._bm25_tokenize(text)
                if tokens:
                    docs.append({"text": text, "metadata": meta})
                    tokenized_corpus.append(tokens)
            
            if not tokenized_corpus:
                return False
            
            # Build BM25Okapi index (k1=1.5, b=0.75 -- standard Okapi parameters)
            RAGPipeline._bm25_index = BM25Okapi(tokenized_corpus, k1=1.5, b=0.75)
            RAGPipeline._bm25_corpus_docs = docs
            RAGPipeline._bm25_corpus_hash = corpus_hash
            
            logger.info(f"[BM25] Index built: {len(docs)} documents, Okapi BM25 (k1=1.5, b=0.75)")
            return True
            
        except ImportError:
            logger.warning("[BM25] rank-bm25 not installed -- pip install rank-bm25")
            return False
        except Exception as e:
            logger.warning(f"[BM25] Index build failed: {e}")
            return False

    def _bm25_search(
        self, rag_store, query: str, k: int = 0
    ) -> List[Dict[str, Any]]:
        """Real BM25 sparse keyword search using Okapi BM25 (Robertson et al. 1995).
        
        Proper BM25 with:
          - Term Frequency (TF) saturation via k1 parameter
          - Inverse Document Frequency (IDF) from corpus statistics
          - Document length normalization via b parameter
        
        Complements dense retrieval (FAISS) which excels at semantic matching
        but misses exact keyword/entity matches.
        
        Args:
            rag_store: UnifiedRagStore with SQLite backend
            query: Search query
            k: Max results (0 = use self.prefetch_k)
            
        Returns:
            List of matching chunks with text, metadata, BM25 score
        """
        if k == 0:
            k = self.prefetch_k
        
        try:
            with RAGPipeline._bm25_lock:
                if not self._build_bm25_index(rag_store):
                    return []
            
            bm25 = RAGPipeline._bm25_index
            corpus_docs = RAGPipeline._bm25_corpus_docs
            
            if bm25 is None or not corpus_docs:
                return []
            
            # Tokenize query with same tokenizer as corpus
            query_tokens = self._bm25_tokenize(query)
            if not query_tokens:
                return []
            
            # Get BM25 scores for all documents
            import numpy as np
            scores = bm25.get_scores(query_tokens)
            
            # Get top-k indices by score (descending)
            if len(scores) <= k:
                top_indices = np.argsort(scores)[::-1]
            else:
                # Partial sort for efficiency with large corpus
                top_indices = np.argpartition(scores, -k)[-k:]
                top_indices = top_indices[np.argsort(scores[top_indices])[::-1]]
            
            # Filter out zero-score documents and build results
            hits: List[Dict[str, Any]] = []
            for idx in top_indices:
                score = float(scores[idx])
                if score <= 0.0:
                    break  # All remaining have zero score
                
                doc = corpus_docs[idx]
                hits.append({
                    "text": doc["text"],
                    "metadata": doc.get("metadata", {}),
                    "score": score,
                    "retrieval_method": "bm25_okapi",
                })
            
            if hits:
                logger.info(
                    f"[BM25] Okapi BM25: {len(hits)} hits for "
                    f"{len(query_tokens)} query terms "
                    f"(top={hits[0]['score']:.3f})"
                )
            return hits
            
        except Exception as e:
            logger.debug(f"[BM25] Search failed: {e}")
            return []

    # ═══════════════════════════════════════════════════════════════════
    # PUBLIC API: Persist (Write Path)
    # ═══════════════════════════════════════════════════════════════════
    
    def persist_web_results(self, web_results: List[Dict[str, Any]], query: str) -> None:
        """Persistiert Web-Suchergebnisse ins RAG (Background Thread).
        
        Full-Content-Extraction mit Trafilatura via upsert_url().
        Non-blocking: Startet Background-Thread und kehrt sofort zurück.
        URL-Deduplication pro Session verhindert doppelte Verarbeitung.
        
        Args:
            web_results: Web-Search-Ergebnisse (dicts mit url, title, snippet)
            query: Original-Suchanfrage für Metadaten
        """
        if not self.persist_enabled or not web_results:
            return
        
        rag_store = self._get_rag_store()
        if not rag_store:
            return
        
        # URL-Deduplication (schnell, non-blocking)
        new_results = []
        with self._persist_lock:
            for result in web_results:
                url = result.get("url", result.get("href", ""))
                if url and url.startswith(("http://", "https://")) and url not in self._processed_urls:
                    self._processed_urls.add(url)
                    new_results.append(result)
        
        if not new_results:
            logger.debug("[RAG-PERSIST] Alle URLs bereits verarbeitet → Skip")
            return
        
        self._inc_stat("persist_calls")
        
        # Background Thread für non-blocking Full-Content-Extraction
        thread = threading.Thread(
            target=self._persist_worker,
            args=(rag_store, new_results, query),
            daemon=True,
            name=f"rag-persist-{len(self._persist_threads)}",
        )
        thread.start()
        self._persist_threads.append(thread)
        
        logger.info(f"[RAG-PERSIST] Background-Thread gestartet für {len(new_results)} URLs")

    # ═══════════════════════════════════════════════════════════════════
    # HyDE: Hypothetical Document Embeddings
    # ═══════════════════════════════════════════════════════════════════
    
    # ----------------------------------------------------------------
    # HyDE Routing: Embedding-based QueryType classification.
    #
    # Earlier versions used a regex-based keyword router (anti-pattern per
    # repo policy). We now delegate to IntelligentRouter, which performs
    # zero-shot embedding classification against category prototypes. The
    # QueryType is mapped to a HyDE-benefit decision below.
    #
    # Rationale:
    # - STRUCTURED_DATA: exact tabular / data lookups — HyDE adds no recall.
    # - CURRENT_EVENTS: news / recency-sensitive — HyDE drift > benefit.
    # - FACTUAL: definitions / explanations — HyDE improves embedding match.
    # - TECHNICAL: code / documentation — HyDE improves embedding match.
    # - GENERAL: ambiguous — HyDE helps recall on open queries.
    _HYDE_BENEFICIAL_QUERY_TYPES = None  # populated lazily to avoid import cycles

    def _should_use_hyde(self, query: str) -> bool:
        """Classify whether HyDE will benefit this query.

        Decision is based on:
        1. Hard physical guard: very short queries (<= 3 tokens) skip HyDE.
           Such queries are always exact lookups and HyDE wastes ~70s GPU.
        2. Embedding-based zero-shot classification via IntelligentRouter,
           mapped to HyDE-benefit. No keyword/pattern matching.

        Returns:
            True if HyDE should be activated for this query.
        """
        q = query.strip()
        if len(q.split()) <= 3:
            logger.debug("[HyDE] Short query (<=3 tokens) → skip")
            return False

        # Lazy import to avoid cycles
        if RAGPipeline._HYDE_BENEFICIAL_QUERY_TYPES is None:
            from agent.intelligent_routing import QueryType
            RAGPipeline._HYDE_BENEFICIAL_QUERY_TYPES = frozenset({
                QueryType.FACTUAL,
                QueryType.TECHNICAL,
                QueryType.GENERAL,
            })

        from agent.intelligent_routing import get_global_router
        query_type, confidence = get_global_router().classify_query(query)
        use_hyde = query_type in RAGPipeline._HYDE_BENEFICIAL_QUERY_TYPES
        logger.debug(
            "[HyDE] QueryType=%s (conf=%.2f) → %s",
            query_type.value, confidence, "use HyDE" if use_hyde else "skip",
        )
        return use_hyde
    
    def _hyde_generate(self, query: str) -> Optional[str]:
        """Generiert hypothetische Antwort für besseren Embedding-Recall.
        
        Statt die kurze Query zu embedden (asymmetrisch), generiert das LLM
        eine hypothetische Antwort. Diese wird dann als Suchquery verwendet,
        weil sie semantisch näher an den gespeicherten Chunks liegt.
        
        Returns:
            Hypothetische Antwort oder None bei Fehler
        """
        self._inc_stat("hyde_calls")
        start = time.perf_counter()
        
        try:
            response = self.model_loader.generate_response(
                messages=[
                    {"role": "system", "content": "Du bist ein Experte. Antworte kurz und faktisch."},
                    {"role": "user", "content": HYDE_PROMPT.format(query=query)},
                ],
                max_tokens=200,
                temperature=0.3,
            )
            
            if response and len(response.strip()) > 20:
                elapsed = (time.perf_counter() - start) * 1000
                logger.info(f"[HyDE] Hypothetische Antwort generiert ({len(response)} chars, {elapsed:.0f}ms)")
                return response.strip()
            
            return None
            
        except Exception as e:
            logger.warning(f"[HyDE] Generierung fehlgeschlagen: {e}")
            return None

    # ═══════════════════════════════════════════════════════════════════
    # CRAG: Corrective Retrieval-Augmented Generation
    # ═══════════════════════════════════════════════════════════════════
    
    def _crag_evaluate(
        self, query: str, chunks: List[Dict[str, Any]]
    ) -> Tuple[List[Dict[str, Any]], bool]:
        """CRAG Chunk-Filter via Cross-Encoder Scores.

        SOTA-Änderung (Feb 2026):
          Statt eines 24B-LLM-Calls (~70s) werden die bereits vom
          Cross-Encoder berechneten Relevanz-Scores verwendet (<1ms).

        Warum das besser ist als LLM-basiertes CRAG:
          1. Cross-Encoder sind *speziell für Relevanz-Scoring trainiert*
             (Nogueira & Cho 2019, Thakur et al. 2021 BEIR).
             Ein generalistisches LLM für binäre Klassifikation ist das
             falsche Werkzeug.
          2. Deterministisch: Keine LLM-Varianz bei Wiederholungen.
          3. 100.000× schneller: <1ms vs ~70.000ms.

        Score-Schwellen (BGE-reranker-v2-m3. sigmoid-normiert [0,1]):
          - relevant: score ≥ 0.3
          - ambiguous: 0.05 ≤ score < 0.3 (behalten, aber als unsicher)
          - irrelevant: score < 0.05 (entfernt)

        Fallback: Wenn kein ``rerank_score`` vorhanden (Reranker deaktiviert),
        werden FAISS-Cosine-Scores mit angepassten Schwellen verwendet.

        Returns:
            (filtered_chunks, needs_web_supplement)
        """
        if not chunks:
            return chunks, False

        self._inc_stat("crag_filtered")
        start = time.perf_counter()

        # Adaptive Thresholds je nach Score-Typ
        has_rerank = any("rerank_score" in c for c in chunks)
        if has_rerank:
            # BGE-reranker-v2-m3: sigmoid-activated scores ∈ [0, 1]
            relevant_thr = 0.3
            ambiguous_thr = 0.05
        else:
            # FAISS cosine-similarity scores ∈ [0, 1]
            relevant_thr = 0.50
            ambiguous_thr = 0.30

        filtered: List[Dict[str, Any]] = []
        relevant_count = 0

        for chunk in chunks:
            score = chunk.get("rerank_score", chunk.get("score", 0.0))
            if score >= relevant_thr:
                filtered.append(chunk)
                relevant_count += 1
            elif score >= ambiguous_thr:
                filtered.append(chunk)
            # else: irrelevant → removed

        # Mindestens den besten Chunk behalten (Chunks sind Rerank-sortiert)
        if not filtered and chunks:
            filtered = [chunks[0]]

        total = len(chunks)
        relevance_ratio = relevant_count / total if total > 0 else 0
        needs_web = relevance_ratio < self.crag_relevance_threshold

        elapsed = (time.perf_counter() - start) * 1000
        logger.info(
            f"[CRAG] {relevant_count}/{total} relevant, "
            f"{len(filtered)} behalten, needs_web={needs_web} ({elapsed:.0f}ms)"
        )

        return filtered, needs_web

    # ═══════════════════════════════════════════════════════════════════
    # Cross-Encoder Reranking (SOTA -- Nogueira & Cho 2019)
    # ═══════════════════════════════════════════════════════════════════

    def _rerank_chunks(
        self, query: str, chunks: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Rerankt Chunks via Cross-Encoder für präziseres Relevanz-Ranking.

        Bi-Encoder (FAISS) optimiert auf Recall, Cross-Encoder auf Precision.
        Pipeline: FAISS (broad recall) → Cross-Encoder (precise rank) → CRAG (score filter).
        
        Nutzt ``agent.reranker.CrossEncoderReranker`` (ms-marco-MiniLM-L-6-v2, ~22M params).
        GPU-beschleunigt auf RTX 4090, Latenz ~5-20ms für 8 Chunks.
        """
        if not chunks or not self._reranker:
            return chunks

        self._inc_stat("rerank_calls")
        start = time.perf_counter()

        try:
            # Prepare passages for reranker -- need 'text' key
            passages = []
            for chunk in chunks:
                text = (
                    chunk.get("text")
                    or chunk.get("content")
                    or chunk.get("snippet")
                    or str(chunk.get("metadata", {}).get("text", ""))
                )
                passages.append({**chunk, "text": text})

            reranked = self._reranker.rerank(
                query=query,
                passages=passages,
                top_k=len(chunks),  # Keep all, just reorder
                text_key="text",
            )

            elapsed_ms = (time.perf_counter() - start) * 1000
            logger.info(
                f"[RAG-RERANK] {len(chunks)} Chunks rerankt in {elapsed_ms:.0f}ms "
                f"(top-score={reranked[0].get('rerank_score', 0):.3f})"
            )
            return reranked

        except Exception as e:
            logger.warning(f"[RAG-RERANK] Reranking fehlgeschlagen: {e} -- verwende Original-Reihenfolge")
            return chunks

    # ═══════════════════════════════════════════════════════════════════
    # Contextual Compression
    # ═══════════════════════════════════════════════════════════════════
    
    def _compress_chunks(
        self, query: str, chunks: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Extractive Compression via Cross-Encoder Sentence Scoring.

        SOTA-Änderung (Feb 2026):
          Statt eines 24B-LLM-Calls (~70s) werden einzelne Sätze per
          Cross-Encoder gegen die Query gescort (~20ms für alle Chunks).
          Nur Sätze mit überdurchschnittlicher Relevanz werden behalten.

        Warum das besser ist als LLM-basierte Kompression:
          1. Kein Informationsverlust: Sätze werden *exakt* behalten, nicht
             vom LLM umformuliert (Halluzinationsrisiko = 0).
          2. Keine destruktive Kompression: Das alte LLM hat 1489→30 chars
             (2%) erzeugt -- praktisch alles zerstört.
          3. ~3500× schneller: ~20ms vs ~70.000ms.
          4. Deterministisch: Identisches Ergebnis bei Wiederholung.

        Algorithmus (SOTA: Zhong et al. 2020 -- Extractive Summarization
        as Text Matching):
          1. Splitte jeden Chunk in Sätze
          2. Score jedes (query, sentence) Paar per Cross-Encoder
          3. Behalte Sätze mit Score ≥ adaptivem Schwellenwert
             (Median der Chunk-Scores, min. 0.01)

        Fallback: Wenn kein Reranker verfügbar → Chunks unverändert.
        """
        if not chunks:
            return chunks

        self._inc_stat("compression_calls")
        start = time.perf_counter()

        # Ohne Reranker keine extractive Kompression möglich
        if not self._reranker or not self._reranker.is_available:
            logger.debug("[COMPRESS] Kein Reranker → Skip Compression")
            return chunks

        # ── Phase 1: Sammle alle (query, sentence) Paare gebatched ──
        all_pairs: List[tuple] = []
        # Track: (chunk_idx, chunk, sentences, pair_start, pair_end)
        compressible: List[tuple] = []
        passthrough_indices: set = set()  # Chunks zu kurz für Kompression

        for i, chunk in enumerate(chunks):
            text = chunk.get("text") or chunk.get("snippet") or ""
            if not text.strip() or len(text) < 150:
                passthrough_indices.add(i)
                continue

            sentences = _split_sentences(text)
            if len(sentences) <= 2:
                passthrough_indices.add(i)
                continue

            pair_start = len(all_pairs)
            for s in sentences:
                all_pairs.append((query, s))
            compressible.append((i, chunk, sentences, pair_start, len(all_pairs)))

        if not all_pairs:
            return chunks

        # ── Phase 2: Ein gebatchter Cross-Encoder Call für ALLE Sätze ──
        try:
            scores = self._reranker._predict_optimized(all_pairs)
        except Exception as e:
            logger.warning(f"[COMPRESS] CE sentence scoring failed: {e} → Original-Chunks")
            return chunks

        # ── Phase 3: Satz-Selektion pro Chunk (adaptiver Schwellenwert) ──
        import numpy as np

        result_chunks: List[Dict[str, Any]] = [None] * len(chunks)  # type: ignore[list-item]

        # Passthrough-Chunks unverändert setzen
        for i in passthrough_indices:
            result_chunks[i] = chunks[i]

        # Komprimierte Chunks verarbeiten
        for (i, chunk, sentences, pair_start, pair_end) in compressible:
            chunk_scores = np.array(scores[pair_start:pair_end])

            # Adaptiver Schwellenwert: Median der Satz-Scores
            # Behält ~50% der relevantesten Sätze pro Chunk
            threshold = max(float(np.median(chunk_scores)), 0.01)

            kept = [
                s for s, sc in zip(sentences, chunk_scores)
                if sc >= threshold
            ]

            original_text = chunk.get("text", "")
            if kept and len(" ".join(kept)) < len(original_text) * 0.95:
                compressed = dict(chunk)
                compressed["original_text"] = original_text
                compressed["text"] = " ".join(kept)
                compressed["compressed"] = True
                result_chunks[i] = compressed
            else:
                result_chunks[i] = chunk

        # Sicherheits-Fallback für unverarbeitete Slots
        final_chunks: List[Dict[str, Any]] = [
            result_chunks[i] if result_chunks[i] is not None else chunks[i]
            for i in range(len(chunks))
        ]

        elapsed = (time.perf_counter() - start) * 1000
        original_len = sum(len(c.get("text", "")) for c in chunks)
        compressed_len = sum(len(c.get("text", "")) for c in final_chunks)
        ratio = compressed_len / original_len if original_len > 0 else 1.0
        logger.info(
            f"[COMPRESS] {original_len}→{compressed_len} chars "
            f"({ratio:.0%} des Originals, {elapsed:.0f}ms)"
        )

        return final_chunks

    # ═══════════════════════════════════════════════════════════════════
    # Persist Worker (Background Thread)
    # ═══════════════════════════════════════════════════════════════════
    
    def _persist_worker(
        self, rag_store, web_results: List[Dict[str, Any]], query: str
    ) -> None:
        """Background-Worker für Full-Content-Extraction + RAG-Speicherung.
        
        🆕 DOCLING SOTA (2025-07-18):
            - Erkennt PDF/DOCX/PPTX/XLSX URLs automatisch
            - Docling AI-Verarbeitung mit angepasstem Timeout
            - HTML → schneller Parser, Documents → Docling AI
        
        Nutzt die erweiterte upsert_url()-Methode des UnifiedRagStore
        mit Docling für Dokumente und Trafilatura für HTML.
        Fallback auf Snippet-Speicherung bei Timeout/Fehler.
        """
        start = time.perf_counter()
        stored_count = 0
        
        # Pre-Check: Welche URLs sind Dokumente (für angepasstes Timeout)?
        _DOCUMENT_EXTENSIONS = {
            '.pdf', '.docx', '.doc', '.pptx', '.ppt', '.xlsx', '.xls'
        }
        
        for result in web_results:
            url = result.get("url", result.get("href", ""))
            if not url:
                continue
            
            title = result.get("title", "")
            snippet = result.get("body", result.get("snippet", ""))
            
            # Timeout anpassen: Documente brauchen mehr Zeit (Download + AI)
            url_lower = url.lower()
            is_document = any(url_lower.endswith(ext) for ext in _DOCUMENT_EXTENSIONS)
            request_timeout = 60 if is_document else 8  # 60s für Docs, 8s für HTML
            
            # Versuche Full-Content-Extraction via upsert_url
            try:
                if hasattr(rag_store, "upsert_url"):
                    success = rag_store.upsert_url(
                        url,
                        metadata={
                            "title": title,
                            "source_type": "web_full",
                            "original_snippet": (snippet or "")[:500],
                            "search_query": query,
                            "search_timestamp": datetime.now().isoformat(),
                            "canonical_url": url,
                        },
                        include_tables=True,
                        include_links=False,
                        timeout=request_timeout,
                        chunk_size=1500,
                        chunk_overlap=200,
                    )
                    
                    if success:
                        stored_count += 1
                        logger.debug(f"[RAG-PERSIST] Full-Content: {url}")
                        continue
                        
            except Exception as e:
                logger.debug(f"[RAG-PERSIST] Full-Content fehlgeschlagen für {url}: {e}")
            
            # Fallback: Snippet speichern (2026-08-30: nur wenn Compliance-Gate
            # die URL erlaubt — robots.txt / X-Robots-Tag / no-store, fail-open)
            if snippet and len(snippet) >= 50:
                if not web_compliance.gate_persistence("rag_pipeline.snippet_fallback", url):
                    logger.debug(f"[RAG-PERSIST] Snippet-Fallback blockiert (Compliance): {url}")
                    continue
                try:
                    rag_store.upsert_documents([{
                        "text": f"# {title}\n\n{snippet}\n\nQuelle: {url}",
                        "metadata": {
                            "title": title,
                            "url": url,
                            "canonical_url": url,
                            "source_type": "web_snippet",
                            "content_type": "text/html",
                            "search_query": query,
                            "search_timestamp": datetime.now().isoformat(),
                        },
                    }])
                    stored_count += 1
                    logger.debug(f"[RAG-PERSIST] Snippet-Fallback: {url}")
                except Exception as e:
                    logger.warning(f"[RAG-PERSIST] Snippet-Speicherung fehlgeschlagen: {url}: {e}")
        
        elapsed = time.perf_counter() - start
        self._inc_stat("persist_urls", stored_count)
        logger.info(
            f"[RAG-PERSIST] {stored_count}/{len(web_results)} URLs gespeichert "
            f"in {elapsed:.1f}s (Background)"
        )

    # ═══════════════════════════════════════════════════════════════════
    # Helpers
    # ═══════════════════════════════════════════════════════════════════
    
    def _get_rag_store(self):
        """Gibt den RAG Store vom ToolManager zurück."""
        if self.tool_manager and hasattr(self.tool_manager, 'rag'):
            return self.tool_manager.rag
        return None

    def _format_prefetch_results(
        self, query: str, hits: List[Dict[str, Any]]
    ) -> Tuple[str, List[Dict[str, Any]]]:
        """Formatiert RAG-Hits als injizierbaren Context-Text + Sources-Liste."""
        if not hits:
            return "", []
        
        sources: List[Dict[str, Any]] = []
        text_parts = []
        
        for i, hit in enumerate(hits[:8], 1):
            text = hit.get("text") or hit.get("snippet") or ""
            meta = hit.get("metadata") or {}
            score = hit.get("score", 0.0)
            title = meta.get("title", "")
            url = meta.get("url", "")
            compressed = hit.get("compressed", False)
            
            if not text.strip():
                continue
            
            # Text für LLM-Context
            prefix = f"[Lokales Wissen {i}]"
            if title:
                prefix += f" {title}"
            if compressed:
                prefix += " (komprimiert)"
            text_parts.append(f"{prefix}\n{text[:500]}")
            
            # Source für GUI
            sources.append({
                "title": title or f"RAG-Ergebnis {i}",
                "url": url or f"rag://chunk_{i}",
                "snippet": text[:200],
                "score": score,
                "type": "rag",
            })
        
        if not text_parts:
            return "", []
        
        context = (
            "[LOKALES WISSEN AUS RAG-DATENBANK]\n"
            "Die folgenden Informationen stammen aus der lokalen Wissensdatenbank "
            "und könnten für die Beantwortung relevant sein:\n\n"
            + "\n\n".join(text_parts)
            + "\n\n[ENDE LOKALES WISSEN]"
        )
        
        return context, sources

    def get_stats(self) -> Dict[str, Any]:
        """Gibt Pipeline-Statistiken zurück (thread-safe snapshot)."""
        with self._stats_lock:
            return dict(self.stats)

    def _inc_stat(self, key: str, amount: int = 1) -> None:
        """Thread-safe stat increment."""
        with self._stats_lock:
            self.stats[key] = self.stats.get(key, 0) + amount

    def cleanup(self) -> None:
        """Wartet auf laufende Persist-Threads (mit Timeout) und bereinigt."""
        alive = [t for t in self._persist_threads if t.is_alive()]
        if alive:
            logger.info(f"[RAG] Cleanup: Warte auf {len(alive)} Persist-Threads...")
        for t in alive:
            t.join(timeout=10)
            if t.is_alive():
                logger.warning(f"[RAG] Persist-Thread {t.name} hat Timeout überschritten")
        # Bereinige abgeschlossene Threads
        self._persist_threads = [t for t in self._persist_threads if t.is_alive()]
