"""
RAG Manager mit GPU FAISS Integration
======================================

Zentrale RAG-Verwaltung mit GPU-Optimierung.

Features:
- RAG Configuration Management
- Single & Multi-Query RAG Execution
- GPU FAISS Integration (RTX 4090 optimiert)
- Index Management
- Performance Optimization

Author: Implementation 2025-10-09
"""

from __future__ import annotations
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass
import logging
import time

logger = logging.getLogger(__name__)

# GPU/FAISS Imports
_torch_mod = None  # type: ignore[assignment]
_faiss_mod = None  # type: ignore[assignment]
FAISS_AVAILABLE = False
GPU_AVAILABLE = False


def _ensure_gpu_runtime() -> None:
    """Lazy-load optional torch/faiss dependencies at runtime."""
    global _torch_mod, _faiss_mod, FAISS_AVAILABLE, GPU_AVAILABLE
    if _torch_mod is not None and _faiss_mod is not None:
        return
    try:
        import torch as _torch  # type: ignore
        import faiss as _faiss  # type: ignore
        _torch_mod = _torch
        _faiss_mod = _faiss
        FAISS_AVAILABLE = True
        GPU_AVAILABLE = bool(_torch_mod.cuda.is_available()) if _torch_mod else False
    except ImportError as e:
        FAISS_AVAILABLE = False
        GPU_AVAILABLE = False
        logger.warning(f"FAISS/PyTorch nicht verfügbar: {e}")


@dataclass
class RAGResult:
    """Ergebnis einer RAG-Suche"""
    chunks: List[Any]
    total_results: int
    search_time_ms: float
    used_gpu: bool
    metadata: Dict[str, Any]


class GPUFAISSManager:
    """
    GPU FAISS Integration für ultra-schnelle Vector Search
    
    Optimiert für RTX 4090:
    - Automatischer GPU Transfer
    - Fallback zu CPU
    - VRAM Management
    """
    
    def __init__(self):
        """Initialisiert GPU FAISS wenn verfügbar"""
        _ensure_gpu_runtime()
        self.gpu_enabled = False
        self.gpu_resources = None
        self.gpu_id = 0  # RTX 4090 auf cuda:0
        
        if not FAISS_AVAILABLE or not GPU_AVAILABLE:
            logger.info("GPU FAISS nicht verfügbar, nutze CPU-FAISS")
            return
        
        try:
            # Check GPU availability
            if _faiss_mod is None:
                raise RuntimeError("FAISS module not loaded")

            ngpus = _faiss_mod.get_num_gpus()  # type: ignore
            if ngpus > 0:
                logger.info(f"🎮 FAISS: {ngpus} GPU(s) verfügbar")
                
                # Initialisiere GPU Resources
                self.gpu_resources = _faiss_mod.StandardGpuResources()  # type: ignore
                self.gpu_enabled = True
                
                logger.info(f"✅ GPU FAISS aktiviert (Device: cuda:{self.gpu_id})")
            else:
                logger.info("Keine GPUs für FAISS verfügbar")
        except Exception as e:
            logger.warning(f"GPU FAISS Initialisierung fehlgeschlagen: {e}")
            self.gpu_enabled = False
    
    def transfer_index_to_gpu(self, cpu_index: Any) -> Any:
        """
        Transferiert FAISS Index von CPU zu GPU
        
        Args:
            cpu_index: CPU FAISS Index
            
        Returns:
            GPU FAISS Index (oder Original bei Fehler)
        """
        if not self.gpu_enabled or not self.gpu_resources:
            return cpu_index
        
        try:
            logger.info("🎮 Transferiere FAISS Index zu GPU...")
            start = time.time()
            
            # Transfer to GPU
            if _faiss_mod is None:
                raise RuntimeError("FAISS module not loaded")

            gpu_index = _faiss_mod.index_cpu_to_gpu(  # type: ignore
                self.gpu_resources,
                self.gpu_id,
                cpu_index
            )
            
            elapsed = time.time() - start
            logger.info(f"✅ Index auf GPU in {elapsed:.2f}s (VRAM: ~{self._estimate_vram_mb(cpu_index)}MB)")
            
            return gpu_index
            
        except Exception as e:
            logger.error(f"GPU Transfer fehlgeschlagen: {e}, nutze CPU Index")
            return cpu_index
    
    def _estimate_vram_mb(self, index: Any) -> int:
        """Schätzt VRAM-Nutzung eines Index"""
        try:
            # Rough estimate: n_vectors * d_dimensions * 4 bytes (float32)
            n = index.ntotal
            d = index.d
            vram_bytes = n * d * 4
            vram_mb = vram_bytes / (1024 * 1024)
            return int(vram_mb)
        except Exception:
            return 0
    
    def cleanup(self):
        """Cleanup GPU Resources"""
        if self.gpu_resources:
            del self.gpu_resources
            self.gpu_resources = None
        
        if GPU_AVAILABLE and _torch_mod is not None:
            try:
                _torch_mod.cuda.empty_cache()
                logger.info("🗑️ GPU FAISS Resources bereinigt")
            except Exception as e:
                logger.warning(f"GPU cleanup fehlgeschlagen: {e}")


class RAGManager:
    """
    Zentrale RAG-Verwaltung
    
    Koordiniert:
    - RAG Execution (Single & Multi-Query)
    - GPU FAISS Integration
    - Index Management
    - Performance Optimization
    """
    
    def __init__(
        self,
        tools_manager: Optional[Any] = None,
        enable_gpu: bool = True
    ):
        """
        Args:
            tools_manager: ToolManager mit RAG-Funktionen
            enable_gpu: GPU FAISS aktivieren
        """
        self.tools = tools_manager
        
        # GPU FAISS Manager
        self.gpu_manager = GPUFAISSManager() if enable_gpu else None
        self.gpu_enabled = self.gpu_manager.gpu_enabled if self.gpu_manager else False
        
        # Stats
        self.total_searches = 0
        self.total_gpu_searches = 0
        self.total_cpu_searches = 0
        
        logger.info(f"✅ RAGManager initialisiert (GPU: {self.gpu_enabled})")
    
    def init_gpu_faiss(self) -> bool:
        """
        Initialisiert GPU FAISS (transferiert Indices)
        
        Returns:
            True wenn erfolgreich
        """
        if not self.gpu_enabled or not self.gpu_manager:
            logger.info("GPU FAISS nicht verfügbar")
            return False
        
        try:
            # Zugriff auf FAISS Index Manager
            if self.tools and hasattr(self.tools, 'faiss_manager'):
                faiss_manager = self.tools.faiss_manager
                
                # Transfer Recent Index
                if hasattr(faiss_manager, 'recent_index') and faiss_manager.recent_index:
                    logger.info("🎮 Transferiere Recent Index zu GPU...")
                    faiss_manager.recent_index = self.gpu_manager.transfer_index_to_gpu(
                        faiss_manager.recent_index
                    )
                
                # Transfer Full Index
                if hasattr(faiss_manager, 'full_index') and faiss_manager.full_index:
                    logger.info("🎮 Transferiere Full Index zu GPU...")
                    faiss_manager.full_index = self.gpu_manager.transfer_index_to_gpu(
                        faiss_manager.full_index
                    )
                
                logger.info("✅ GPU FAISS Initialisierung komplett")
                return True
            else:
                logger.warning("FAISS Manager nicht gefunden in Tools")
                return False
                
        except Exception as e:
            logger.error(f"GPU FAISS Initialisierung fehlgeschlagen: {e}")
            return False
    
    def execute_rag(
        self,
        query: str,
        k: int = 6,
        multi_query: bool = False,
        sub_queries: Optional[List[str]] = None
    ) -> RAGResult:
        """
        Führt RAG-Suche aus
        
        Args:
            query: Haupt-Query
            k: Anzahl Results
            multi_query: Multi-Query Mode
            sub_queries: Optional Sub-Queries
            
        Returns:
            RAGResult
        """
        start_time = time.time()
        self.total_searches += 1
        
        if multi_query and sub_queries:
            # Multi-Query RAG
            chunks = self._execute_multi_query_rag(sub_queries, k)
        else:
            # Single Query RAG
            chunks = self._execute_single_rag(query, k)
        
        search_time_ms = (time.time() - start_time) * 1000
        
        # Track GPU usage
        used_gpu = self.gpu_enabled
        if used_gpu:
            self.total_gpu_searches += 1
        else:
            self.total_cpu_searches += 1
        
        result = RAGResult(
            chunks=chunks,
            total_results=len(chunks),
            search_time_ms=search_time_ms,
            used_gpu=used_gpu,
            metadata={
                'multi_query': multi_query,
                'num_sub_queries': len(sub_queries) if sub_queries else 0
            }
        )
        
        logger.info(
            f"{'🎮' if used_gpu else '🖥️'} RAG Search: "
            f"{len(chunks)} results in {search_time_ms:.1f}ms "
            f"({'GPU' if used_gpu else 'CPU'})"
        )
        
        return result
    
    def _execute_single_rag(self, query: str, k: int) -> List[Any]:
        """Führt Single-Query RAG aus"""
        if not self.tools:
            logger.warning("Tools Manager nicht verfügbar")
            return []
        
        try:
            result = self.tools.rag_search(query, k=k)  # type: ignore
            
            # Extract chunks
            if hasattr(result, 'chunks'):
                return list(result.chunks)
            elif hasattr(result, 'results'):
                return list(result.results)
            elif isinstance(result, list):
                return result
            else:
                return [result]
                
        except Exception as e:
            logger.error(f"RAG Search fehlgeschlagen: {e}")
            return []
    
    def _execute_multi_query_rag(
        self,
        sub_queries: List[str],
        k: int
    ) -> List[Any]:
        """Führt Multi-Query RAG aus"""
        all_chunks = []
        
        # k pro Sub-Query
        k_per_query = max(2, k // len(sub_queries))
        
        for i, sub_q in enumerate(sub_queries, 1):
            logger.debug(f"Sub-Query {i}/{len(sub_queries)}: {sub_q[:50]}...")
            
            chunks = self._execute_single_rag(sub_q, k_per_query)
            all_chunks.extend(chunks)
        
        # Deduplicate & limit to k
        unique_chunks = self._deduplicate_chunks(all_chunks)
        return unique_chunks[:k]
    
    def _deduplicate_chunks(self, chunks: List[Any]) -> List[Any]:
        """Dedupliziert Chunks"""
        seen = set()
        unique = []
        
        for chunk in chunks:
            # Hash basierend auf Content
            content = getattr(chunk, 'content', getattr(chunk, 'text', str(chunk)))
            chunk_hash = hash(content[:200])  # First 200 chars
            
            if chunk_hash not in seen:
                seen.add(chunk_hash)
                unique.append(chunk)
        
        return unique
    
    def get_stats(self) -> Dict[str, Any]:
        """Gibt RAG-Statistiken zurück"""
        return {
            'gpu_enabled': self.gpu_enabled,
            'total_searches': self.total_searches,
            'gpu_searches': self.total_gpu_searches,
            'cpu_searches': self.total_cpu_searches,
            'gpu_usage_percent': (
                (self.total_gpu_searches / self.total_searches * 100)
                if self.total_searches > 0 else 0
            )
        }
    
    def cleanup(self):
        """Cleanup Resources"""
        if self.gpu_manager:
            self.gpu_manager.cleanup()
    
    def execute_rag_with_multiquery(
        self,
        query: str,
        k: int = 6,
        min_score: float = 0.0,
        multiquery_enabled: bool = False,
        mq_n: int = 5,
        mq_k: int = 5,
        sub_queries: Optional[List[str]] = None,
        is_time_critical: bool = False,
        web_results_available: bool = False
    ) -> List[Any]:
        """
        Orchestrator-kompatible RAG-Execution mit Multi-Query Support
        
        Führt RAG-Suche aus mit vollständiger Orchestrator-Logik:
        - Hybrid RAG Strategy (Zeit-kritische Queries)
        - Multi-Query RAG mit Sub-Queries
        - Detailed Logging
        - ToolResult Generation
        
        Args:
            query: Haupt-Query
            k: Anzahl Results für Haupt-Query
            min_score: Minimaler Score-Threshold
            multiquery_enabled: Multi-Query aktivieren
            mq_n: Anzahl Sub-Queries
            mq_k: Results pro Sub-Query
            sub_queries: Optional vorgenerierte Sub-Queries
            is_time_critical: Zeitkritische Query (für Hybrid-Modus)
            web_results_available: Web-Ergebnisse verfügbar (für Hybrid-Modus)
            
        Returns:
            List of ToolResult objects
        """
        results: List[Any] = []
        
        # Hybrid RAG Strategy Check
        should_execute_rag = True
        rag_reason = "Standard RAG-Modus"
        
        if is_time_critical and web_results_available:
            should_execute_rag = True  # IMMER ausführen für Fusion!
            rag_reason = "Zeitkritische Frage: Web (aktuell) + RAG (historisch/lokal) Fusion"
            logger.info(f"🔄 Hybrid-Modus: Web-Ergebnisse vorhanden, ergänze mit RAG-Wissen")
        elif is_time_critical and not web_results_available:
            should_execute_rag = True
            rag_reason = "Zeitkritische Frage ohne Web-Ergebnisse - RAG als Hauptquelle"
        
        logger.info(f"RAG-Entscheidung: {rag_reason}")
        
        if not should_execute_rag:
            logger.info("RAG-Execution übersprungen basierend auf Hybrid-Strategie")
            return results
        
        logger.info(f"RAG aktiviert - Multiquery: {multiquery_enabled}")
        
        if not self.tools:
            logger.warning("Tools Manager nicht verfügbar - keine RAG-Execution möglich")
            return results
        
        try:
            if multiquery_enabled:
                logger.info("🔍" + "=" * 68)
                logger.info(f"🔍 STARTE MULTIQUERY-RAG")
                logger.info(f"🔍 Query: '{query[:70]}...'")
                logger.info(f"🔍 Parameter: k={k}, min_score={min_score}, mq_n={mq_n}, mq_k={mq_k}")
                logger.info("🔍" + "=" * 68)
                
                # 🚀 BATCH-SEARCH OPTIMIZATION: Nutze Batch-API für alle Queries
                # Sammle alle Queries (Main + Sub-Queries)
                all_queries = [query]
                k_values = [k]
                
                if sub_queries:
                    limited_subqs = sub_queries[: max(1, int(mq_n))]
                    all_queries.extend(limited_subqs)
                    k_values.extend([mq_k] * len(limited_subqs))
                    logger.info(f"🚀 BATCH-SEARCH: {len(all_queries)} Queries (1 Main + {len(limited_subqs)} Sub)")
                else:
                    logger.info(f"🚀 BATCH-SEARCH: 1 Query (nur Main)")
                
                # Führe Batch-Search aus (150x schneller als sequenziell!)
                try:
                    import time
                    batch_start = time.time()
                    
                    batch_results = self.tools.rag_search_batch(
                        queries=all_queries,
                        k_list=k_values,
                        min_score=min_score
                    )
                    
                    batch_time = (time.time() - batch_start) * 1000
                    logger.info(f"✅ BATCH-SEARCH COMPLETE: {batch_time:.1f}ms für {len(all_queries)} Queries")
                    logger.info(f"   └─ Durchschnitt: {batch_time/len(all_queries):.1f}ms pro Query")
                    
                    # Verarbeite Batch-Results
                    for i, (q, rag_result) in enumerate(zip(all_queries, batch_results)):
                        results.append(rag_result)
                        rag_count = len(rag_result.results or []) if hasattr(rag_result, 'results') else 0
                        
                        if i == 0:
                            # Main Query
                            logger.info(f"✅ MAIN QUERY RAG: {rag_count} Ergebnisse")
                            if rag_count > 0 and hasattr(rag_result, 'results') and rag_result.results:
                                first_result = rag_result.results[0]
                                title = first_result.get('title', 'N/A') if isinstance(first_result, dict) else 'N/A'
                                logger.info(f"   └─ Top-Ergebnis: {title[:60]}...")
                        else:
                            # Sub-Query
                            logger.info(f"✅ SUBQUERY {i}/{len(all_queries)-1} RAG: {rag_count} Ergebnisse")
                            logger.info(f"   ├─ Query: '{q[:60]}...'")
                            if rag_count > 0 and hasattr(rag_result, 'results') and rag_result.results:
                                first_result = rag_result.results[0]
                                title = first_result.get('title', 'N/A') if isinstance(first_result, dict) else 'N/A'
                                logger.info(f"   └─ Top-Ergebnis: {title[:50]}...")
                    
                except AttributeError as e:
                    logger.error(f"❌ Batch-RAG-Tool nicht verfügbar: {e}")
                    logger.warning("⚠️ Fallback zu sequenzieller Suche...")
                    
                    # FALLBACK: Sequenzielle Suche (alt)
                    try:
                        rag_result = self.tools.rag_search(query, k=k, min_score=min_score)  # type: ignore
                        results.append(rag_result)
                    except Exception as e2:
                        logger.error(f"Auch Fallback fehlgeschlagen: {e2}")
                    
                except Exception as e:
                    logger.error(f"❌ Batch-RAG fehlgeschlagen: {type(e).__name__}: {e}")
                    import traceback
                    logger.debug(f"Traceback:\n{traceback.format_exc()}")
                    logger.warning("⚠️ Fallback zu sequenzieller Suche...")
                    
                    # FALLBACK: Sequenzielle Suche (alt)
                    try:
                        rag_result = self.tools.rag_search(query, k=k, min_score=min_score)  # type: ignore
                        results.append(rag_result)
                    except Exception as e2:
                        logger.error(f"Auch Fallback fehlgeschlagen: {e2}")
                    
            else:
                # Single Query RAG (kein Multi-Query)
                logger.info("Normale RAG-Suche (ohne Multiquery)")
                try:
                    rag_res = self.tools.rag_search(query, k=k, min_score=min_score)  # type: ignore
                    results.append(rag_res)
                    rag_count = len(rag_res.results or []) if hasattr(rag_res, 'results') else 0
                    logger.info(f"✅ Single RAG Query: {rag_count} Ergebnisse")
                except Exception as e:
                    logger.error(f"Single RAG Query fehlgeschlagen: {type(e).__name__}: {e}")
                    
        except AttributeError as e:
            logger.error(f"RAG-Tool oder RAG-Methode nicht verfügbar: {e}")
            logger.info("Fahre ohne RAG-Ergebnisse fort")
        except Exception as e:
            logger.warning(f"Fehler bei RAG-Ausführung: {type(e).__name__}: {e}")
            import traceback
            logger.debug(f"RAG-Execution-Fehler Traceback:\n{traceback.format_exc()}")
            logger.info("Fahre ohne RAG-Ergebnisse fort")
        
        # Log Summary
        rag_result_count = len(results)
        total_results = sum(
            len(r.results or []) if hasattr(r, 'results') else 0
            for r in results
        )
        logger.info(f"📊 RAG-Execution Summary: {rag_result_count} Searches, {total_results} Total Results")
        
        return results
    
    def execute_rag_with_gap_detection(
        self,
        query: str,
        k: int = 6,
        min_score: float = 0.0,
        multiquery_enabled: bool = False,
        mq_n: int = 5,
        mq_k: int = 5,
        sub_queries: Optional[List[str]] = None,
        is_time_critical: bool = False,
        web_results_available: bool = False,
        min_results_threshold: int = 2,
        enable_web_fallback: bool = True,
        persist_to_rag: bool = True,
        allowed_domains: Optional[List[str]] = None,
        exclude_safety_flags: Optional[List[str]] = None,
    ) -> List[Any]:
        """
        RAG-Execution mit intelligenter Gap-Detection für Sub-Queries
        
        Erweitert execute_rag_with_multiquery um:
        - Automatische Gap-Detection für Sub-Queries
        - Web-Fallback wenn RAG unzureichende Ergebnisse liefert
        - Persistierung neuer Web-Ergebnisse in RAG
        - Detaillierte Gap-Analyse und Logging
        
        Args:
            query: Haupt-Query
            k: Anzahl Results für Haupt-Query
            min_score: Minimaler Score-Threshold
            multiquery_enabled: Multi-Query aktivieren
            mq_n: Anzahl Sub-Queries
            mq_k: Results pro Sub-Query
            sub_queries: Optional vorgenerierte Sub-Queries
            is_time_critical: Zeitkritische Query
            web_results_available: Web-Ergebnisse verfügbar
            min_results_threshold: Minimale Anzahl Results um Gap zu vermeiden (default: 2)
            enable_web_fallback: Web-Fallback für Gaps aktivieren (default: True)
            persist_to_rag: Web-Ergebnisse in RAG persistieren (default: True)
            
        Returns:
            List of ToolResult objects (RAG + optional Web-Fallback)
        """
        logger.info("=" * 70)
        logger.info("🔍 RAG MIT GAP-DETECTION GESTARTET")
        logger.info(f"   ├─ Query: '{query[:60]}...'")
        logger.info(f"   ├─ Multi-Query: {multiquery_enabled}")
        logger.info(f"   ├─ Sub-Queries: {len(sub_queries) if sub_queries else 0}")
        logger.info(f"   ├─ Web-Fallback: {enable_web_fallback}")
        logger.info(f"   └─ RAG-Persistierung: {persist_to_rag}")
        logger.info("=" * 70)
        
        results: List[Any] = []
        gap_stats: Dict[str, Any] = {
            'total_queries': 0,
            'queries_with_gaps': 0,
            'web_fallbacks_triggered': 0,
            'new_results_persisted': 0,
            'gaps_filled': []
        }
        
        # Hybrid RAG Strategy Check
        should_execute_rag = True
        rag_reason = "Standard RAG-Modus mit Gap-Detection"
        
        if is_time_critical and web_results_available:
            should_execute_rag = True
            rag_reason = "Zeitkritisch: Web + RAG Fusion mit Gap-Detection"
        elif is_time_critical and not web_results_available:
            should_execute_rag = True
            rag_reason = "Zeitkritisch ohne Web: RAG als Hauptquelle mit Gap-Detection"
        
        logger.info(f"📋 RAG-Strategie: {rag_reason}")
        
        if not should_execute_rag:
            logger.info("⏭️ RAG-Execution übersprungen")
            return results
        
        if not self.tools:
            logger.warning("⚠️ Tools Manager nicht verfügbar")
            return results
        
        try:
            # 1. ORIGINAL QUERY RAG
            gap_stats['total_queries'] += 1
            logger.info(f"🔎 ORIGINAL QUERY: '{query[:70]}...'")
            
            try:
                rag_result = self.tools.rag_search(
                    query, k=k, min_score=min_score,
                    allowed_domains=allowed_domains,
                    exclude_safety_flags=exclude_safety_flags,
                )
                results.append(rag_result)
                rag_count = len(rag_result.results or []) if hasattr(rag_result, 'results') else 0
                
                # Extract top score for quality check
                # TRY MULTIPLE SOURCES: result['score'], result['similarity'], result['distance']
                top_score = None
                first_result = None
                if rag_result.results and len(rag_result.results) > 0:
                    first_result = rag_result.results[0]
                    if isinstance(first_result, dict):
                        # Try different score fields
                        top_score = (
                            first_result.get('score') or
                            first_result.get('similarity') or
                            first_result.get('distance') or
                            first_result.get('_score')
                        )
                    elif hasattr(first_result, 'score'):
                        top_score = first_result.score
                    elif hasattr(first_result, 'similarity'):
                        top_score = first_result.similarity
                
                # DEBUG: Log score extraction
                if top_score is None and rag_count > 0 and first_result is not None:
                    logger.warning(f"   ⚠️ Konnte Score nicht extrahieren! Result-Type: {type(first_result)}")
                    if isinstance(first_result, dict):
                        logger.debug(f"   Verfügbare Keys: {list(first_result.keys())}")
                
                logger.info(f"   └─ RAG: {rag_count} Ergebnisse" + 
                           (f" (Top-Score: {top_score:.3f})" if top_score is not None else " (Score: N/A)"))
                
                # Gap-Detection: Check BOTH count AND quality
                has_count_gap = rag_count < min_results_threshold
                has_quality_gap = False
                
                # Quality check nur wenn Score verfügbar und min_score gesetzt
                if top_score is not None and min_score > 0:
                    has_quality_gap = top_score < min_score
                elif top_score is None and min_score > 0 and rag_count > 0:
                    # FALLBACK: Wenn Score nicht extrahierbar, gehe von Gap aus (sicher ist sicher)
                    has_quality_gap = True
                    logger.warning(f"   ⚠️ Score nicht verfügbar - gehe von Quality-Gap aus (Safety-Fallback)")
                
                if has_count_gap or has_quality_gap:
                    gap_stats['queries_with_gaps'] += 1
                    
                    if has_count_gap:
                        logger.info(f"   ℹ️ Gap (Count): {rag_count} < {min_results_threshold} — Web-Fallback wird geprüft")
                    if has_quality_gap:
                        logger.info(f"   ℹ️ Gap (Quality): Top-Score {top_score:.3f} < {min_score:.3f} — Web-Fallback wird geprüft")
                    
                    # Initialize web_results_added before conditional block
                    web_results_added = 0
                    
                    if enable_web_fallback:
                        gap_reason = []
                        if has_count_gap:
                            gap_reason.append(f"count:{rag_count}/{min_results_threshold}")
                        if has_quality_gap:
                            gap_reason.append(f"score:{top_score:.3f}/{min_score:.3f}")
                        
                        logger.info(f"   🌐 Triggere Web-Fallback ({', '.join(gap_reason)})...")
                        web_results_added = self._execute_web_fallback_for_gap(
                            query=query,
                            gap_description=f"Gap: {', '.join(gap_reason)}",
                            results_list=results,
                            persist_to_rag=persist_to_rag,
                            gap_stats=gap_stats
                        )
                    
                    if web_results_added:
                            gap_stats['gaps_filled'].append({
                                'query': query,
                                'type': 'original',
                                'rag_count': rag_count,
                                'top_score': top_score,
                                'has_count_gap': has_count_gap,
                                'has_quality_gap': has_quality_gap,
                                'web_results': web_results_added
                            })
                
            except Exception as e:
                logger.error(f"❌ Original Query RAG fehlgeschlagen: {type(e).__name__}: {e}")
            
            # 2. SUB-QUERY RAG MIT GAP-DETECTION (BATCH-OPTIMIZED)
            if multiquery_enabled and sub_queries:
                limited_subqs = sub_queries[:max(1, int(mq_n))]
                logger.info(f"\n🔍 SUB-QUERY EXECUTION ({len(limited_subqs)} Queries)")
                logger.info("=" * 70)
                
                # 🚀 BATCH-SEARCH für alle Sub-Queries
                if len(limited_subqs) > 0:
                    try:
                        import time
                        batch_start = time.time()
                        
                        # Batch-Search für alle Sub-Queries
                        k_list_subq = [mq_k] * len(limited_subqs)
                        batch_subq_results = self.tools.rag_search_batch(
                            queries=limited_subqs,
                            k_list=k_list_subq,
                            min_score=min_score,
                            allowed_domains=allowed_domains,
                            exclude_safety_flags=exclude_safety_flags,
                        )
                        
                        batch_time = (time.time() - batch_start) * 1000
                        logger.info(f"✅ SUB-QUERY BATCH-SEARCH: {batch_time:.1f}ms für {len(limited_subqs)} Queries")
                        
                        # Gap-Detection für jedes Sub-Query-Ergebnis
                        for i, (sq, subq_result) in enumerate(zip(limited_subqs, batch_subq_results), 1):
                            gap_stats['total_queries'] += 1
                            logger.info(f"\n📌 Sub-Query {i}/{len(limited_subqs)}: '{sq[:60]}...'")
                            
                            results.append(subq_result)
                            subq_count = len(subq_result.results or []) if hasattr(subq_result, 'results') else 0
                            
                            # Extract top score for quality check
                            top_score = None
                            if subq_result.results and len(subq_result.results) > 0:
                                first_result = subq_result.results[0]
                                if isinstance(first_result, dict):
                                    top_score = first_result.get('score')
                                elif hasattr(first_result, 'score'):
                                    top_score = first_result.score
                            
                            logger.info(f"   └─ RAG: {subq_count} Ergebnisse" +
                                       (f" (Top-Score: {top_score:.3f})" if top_score is not None else ""))
                            
                            # Gap-Detection: Check BOTH count AND quality
                            has_count_gap = subq_count < min_results_threshold
                            has_quality_gap = False
                            
                            if top_score is not None and min_score > 0:
                                has_quality_gap = top_score < min_score
                            
                            if has_count_gap or has_quality_gap:
                                gap_stats['queries_with_gaps'] += 1
                                
                                if has_count_gap:
                                    logger.info(f"   ℹ️ Gap (Count): {subq_count} < {min_results_threshold} — Web-Fallback wird geprüft")
                                if has_quality_gap:
                                    logger.info(f"   ℹ️ Gap (Quality): Top-Score {top_score:.3f} < {min_score:.3f} — Web-Fallback wird geprüft")
                                
                                # Initialize web_results_added before conditional block
                                web_results_added = 0
                                
                                if enable_web_fallback:
                                    gap_reason = []
                                    if has_count_gap:
                                        gap_reason.append(f"count:{subq_count}/{min_results_threshold}")
                                    if has_quality_gap:
                                        gap_reason.append(f"score:{top_score:.3f}/{min_score:.3f}")
                                    
                                    logger.info(f"   🌐 Triggere Web-Fallback ({', '.join(gap_reason)})...")
                                    web_results_added = self._execute_web_fallback_for_gap(
                                        query=sq,
                                        gap_description=f"Sub-Query Gap: {', '.join(gap_reason)}",
                                        results_list=results,
                                        persist_to_rag=persist_to_rag,
                                        gap_stats=gap_stats
                                )
                                
                                if web_results_added:
                                    gap_stats['gaps_filled'].append({
                                        'query': sq,
                                        'type': f'sub_query_{i}',
                                        'rag_count': subq_count,
                                        'top_score': top_score,
                                        'has_count_gap': has_count_gap,
                                        'has_quality_gap': has_quality_gap,
                                        'web_results': web_results_added
                                    })
                            else:
                                logger.info(f"   ✅ Ausreichende Ergebnisse (Count: {subq_count} ≥ {min_results_threshold}" +
                                           (f", Score: {top_score:.3f} ≥ {min_score:.3f}" if top_score and min_score > 0 else "") +
                                           ")")
                        
                    except AttributeError as e:
                        logger.error(f"❌ Batch-RAG für Sub-Queries nicht verfügbar: {e}")
                        logger.warning("⚠️ Fallback zu sequenzieller Sub-Query-Suche...")
                        
                        # FALLBACK: Sequenzielle Suche für Sub-Queries
                        for i, sq in enumerate(limited_subqs, 1):
                            try:
                                subq_result = self.tools.rag_search(
                                    sq, k=mq_k, min_score=min_score,
                                    allowed_domains=allowed_domains,
                                    exclude_safety_flags=exclude_safety_flags,
                                )
                                results.append(subq_result)
                                logger.info(f"✅ Sub-Query {i} (Fallback): OK")
                            except Exception as e2:
                                logger.error(f"❌ Sub-Query {i} Fallback fehlgeschlagen: {e2}")
                                continue
                        
                    except Exception as e:
                        logger.error(f"❌ Sub-Query Batch-Search fehlgeschlagen: {type(e).__name__}: {e}")
                        import traceback
                        logger.debug(f"Traceback:\n{traceback.format_exc()}")
            
            elif not multiquery_enabled:
                logger.info("ℹ️ Multi-Query deaktiviert - nur Original Query")
            else:
                # Designed path: DecompositionEngine intentionally returns []
                # for SIMPLE/MODERATE queries (complexity gating). Not a fault.
                logger.info(
                    "ℹ️ Multi-Query aktiviert, aber Decomposition lieferte keine Sub-Queries "
                    "(Query-Komplexität SIMPLE/MODERATE → Single-Query-Pfad)."
                )
        
        except Exception as e:
            logger.error(f"❌ Kritischer Fehler bei RAG-Execution: {type(e).__name__}: {e}")
            import traceback
            logger.debug(f"Traceback:\n{traceback.format_exc()}")
        
        # FINAL SUMMARY
        logger.info("\n" + "=" * 70)
        logger.info("📊 GAP-DETECTION SUMMARY")
        logger.info(f"   ├─ Queries insgesamt: {gap_stats['total_queries']}")
        logger.info(f"   ├─ Gaps erkannt: {gap_stats['queries_with_gaps']}")
        logger.info(f"   ├─ Web-Fallbacks: {gap_stats['web_fallbacks_triggered']}")
        logger.info(f"   ├─ In RAG persistiert: {gap_stats['new_results_persisted']}")
        logger.info(f"   └─ Gaps gefüllt: {len(gap_stats['gaps_filled'])}")
        
        if gap_stats['gaps_filled']:
            logger.info("\n📋 GEFÜLLTE GAPS:")
            for gap_info in gap_stats['gaps_filled']:
                gap_reasons = []
                if gap_info.get('has_count_gap'):
                    gap_reasons.append(f"Count: {gap_info['rag_count']}")
                if gap_info.get('has_quality_gap') and gap_info.get('top_score') is not None:
                    gap_reasons.append(f"Score: {gap_info['top_score']:.3f}")
                
                reason_str = " & ".join(gap_reasons) if gap_reasons else "Unknown"
                logger.info(f"   • {gap_info['type']}: {gap_info['query'][:50]}...")
                logger.info(f"     Gap-Grund: {reason_str}")
                logger.info(f"     Lösung: +{gap_info['web_results']} Web-Ergebnisse → RAG")
        
        total_results = sum(
            len(r.results or []) if hasattr(r, 'results') else 0
            for r in results
        )
        logger.info(f"\n✅ TOTAL: {len(results)} Searches, {total_results} Results")
        logger.info("=" * 70)
        
        return results
    
    def _execute_web_fallback_for_gap(
        self,
        query: str,
        gap_description: str,
        results_list: List[Any],
        persist_to_rag: bool,
        gap_stats: Dict[str, Any],
        web_num_results: int = 3
    ) -> int:
        """
        Führt Web-Fallback für erkannten Gap aus
        
        Args:
            query: Query mit Gap
            gap_description: Beschreibung des Gaps
            results_list: List zum Hinzufügen der Web-Results
            persist_to_rag: Ob Ergebnisse in RAG persistiert werden sollen
            gap_stats: Stats-Dictionary zum Update
            web_num_results: Anzahl Web-Ergebnisse
            
        Returns:
            Anzahl hinzugefügter Web-Ergebnisse
        """
        # Routing path: ToolManager.run([ToolCall('web_search', ...)]) →
        # AgentToolkit.execute_tool. ToolManager exposes web_search via run(),
        # not as a direct attribute, so a hasattr check would always fail.
        if not self.tools or not (
            getattr(self.tools, "has_web_search", lambda: False)()
        ):
            logger.info("   ℹ️ Web-Search nicht verfügbar (lokaler Modus oder Toolkit fehlt)")
            return 0
        
        try:
            from agent.agent_types import ToolCall
            
            # Web-Suche
            web_call = ToolCall(
                tool="web_search",
                parameters={"query": query, "num_results": web_num_results}
            )
            logger.info(f"   🌐 Web-Suche: '{query[:50]}...' (max: {web_num_results})")
            
            web_results = self.tools.run([web_call])
            
            if web_results and web_results[0].success and web_results[0].results:
                web_count = len(web_results[0].results)
                logger.info(f"   ✅ Web-Fallback: {web_count} Ergebnisse gefunden")
                
                # Zu Results hinzufügen
                results_list.extend(web_results)
                gap_stats['web_fallbacks_triggered'] += 1
                
                # In RAG persistieren
                if persist_to_rag:
                    try:
                        persisted_count = self._persist_web_to_rag_safe(web_results[0].results)
                        gap_stats['new_results_persisted'] += persisted_count
                        logger.info(f"   💾 In RAG persistiert: {persisted_count} Dokumente")
                    except Exception as e:
                        logger.warning(f"   ⚠️ RAG-Persistierung fehlgeschlagen: {e}")
                
                return web_count
            else:
                logger.warning("   ⚠️ Web-Fallback lieferte keine Ergebnisse")
                return 0
                
        except Exception as e:
            logger.error(f"   ❌ Web-Fallback fehlgeschlagen: {type(e).__name__}: {e}")
            return 0
    
    def _persist_web_to_rag_safe(self, web_results: List[Dict[str, Any]]) -> int:
        """
        Sichere Persistierung von Web-Ergebnissen in RAG
        
        Args:
            web_results: Liste von Web-Ergebnissen
            
        Returns:
            Anzahl erfolgreich persistierter Dokumente
        """
        if not self.tools or not hasattr(self.tools, 'rag_upsert_url'):
            logger.debug("   ⚠️ RAG-Upsert nicht verfügbar")
            return 0
        
        persisted = 0
        for result in web_results[:3]:  # Limit to top 3
            url = None
            try:
                url = result.get('url') or result.get('link')
                if not url:
                    continue
                
                # Upsert URL in RAG
                self.tools.rag_upsert_url(url)
                persisted += 1
                
            except Exception as e:
                logger.debug(f"   ⚠️ URL-Persistierung fehlgeschlagen für {url or 'unknown'}: {e}")
                continue
        
        return persisted
