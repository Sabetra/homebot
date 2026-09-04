"""
SOTA RAG Quality Pipeline - Integration Module
=============================================
Verknüpft ChangeDetector, Docling-Parallel, Multi-Modal RAG und StrixKAT Eval
zu einer durchgängigen, self-healing Pipeline.

Pipeline-Fluss:
  Quelle geaendert (ChangeDetector)
    -> PDF schnell extrahieren (Docling-Parallel)
      -> Multi-Modal indexieren (Multi-Modal RAG)
        -> Qualität messen (StrixKAT Eval)
          -> Live oder Auto-Rollback

Hardware: RTX 4090 + 64GB RAM, Windows 11
LLM: Gemma4 12B (typisch)
"""

import asyncio
import logging
import time
import hashlib
from pathlib import Path
from typing import Any, Dict, List, Optional, Callable, Awaitable
from dataclasses import dataclass, field
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# ─── Data Classes ──────────────────────────────────────────────────────────

@dataclass
class PipelineDocument:
    """Repraesentiert ein Dokument in der Pipeline."""
    doc_id: str
    source_path: Optional[str] = None
    content: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)
    chunks: List[Dict[str, Any]] = field(default_factory=list)
    embedding_vectors: Optional[List[float]] = None
    quality_score: Optional[float] = None
    pipeline_status: str = "pending"  # pending|processing|indexed|evaluated|failed
    processed_at: Optional[str] = None
    error_message: Optional[str] = None


@dataclass
class PipelineResult:
    """Ergebnis eines Pipeline-Durchlaufes."""
    success: bool
    documents_processed: int = 0
    documents_failed: int = 0
    avg_quality_score: Optional[float] = None
    processing_time_sec: float = 0.0
    rollback_triggered: bool = False
    details: List[Dict[str, Any]] = field(default_factory=list)
    error: Optional[str] = None


@dataclass
class PipelineConfig:
    """Konfiguration der SOTA Pipeline."""
    # ChangeDetector
    watch_directories: List[str] = field(default_factory=list)
    file_extensions: List[str] = field(default_factory=lambda: ['.pdf', '.docx', '.txt', '.md'])
    hash_algorithm: str = "sha256"
    polling_interval_sec: float = 30.0
    enable_change_detection: bool = True

    # Docling-Parallel
    max_workers: int = 8  # RTX 4090 kann viel Parallelitaet
    enable_docling: bool = True
    docling_timeout_sec: int = 300

    # Multi-Modal RAG
    enable_multimodal: bool = True
    chunk_size: int = 1500
    chunk_overlap: int = 200
    include_tables: bool = True
    include_diagrams: bool = True
    include_formulas: bool = True

    # StrixKAT Eval
    enable_evaluation: bool = True
    quality_threshold: float = 0.75
    eval_interval_sec: float = 300.0  # 5 Minuten
    auto_rollback: bool = True
    min_eval_samples: int = 10

    # Allgemein
    debug: bool = False
    backup_before_changes: bool = True


# ─── Pipeline Engine ───────────────────────────────────────────────────────

class SOTAPipeline:
    """
    SOTA RAG Quality Pipeline Engine.

    Koordiniert ChangeDetector, Docling-Parallel, Multi-Modal RAG und
    StrixKAT Eval zu einer durchgaengigen Verarbeitungskette.
    """

    def __init__(self, config: Optional[PipelineConfig] = None, unified_rag_store=None):
        self.config = config or PipelineConfig()
        self.unified_rag_store = unified_rag_store
        
        # Lazy-Import der Komponenten (verfuegbar wenn importierbar)
        self._change_detector = None
        self._docling_processor = None
        self._multimodal_rag = None
        self._strixkat_eval = None
        
        # Pipeline-State
        self._document_registry: Dict[str, PipelineDocument] = {}
        self._processing_queue: asyncio.Queue = asyncio.Queue()
        self._pipeline_running = False
        self._executor = ThreadPoolExecutor(max_workers=self.config.max_workers)
        self._last_eval_time = 0.0
        self._last_quality_score: Optional[float] = None
        
        # EvalScheduler (Massnahme 1: Scheduled Eval-Job)
        self._eval_scheduler: Optional[Any] = None
        
        # Metriken
        self._metrics: Dict[str, Any] = {
            "documents_processed": 0,
            "documents_failed": 0,
            "pipeline_runs": 0,
            "rollbacks": 0,
            "avg_processing_time": 0.0,
            "start_time": datetime.now(timezone.utc).isoformat(),
        }
        
        if self.config.debug:
            logger.info("SOTA Pipeline initialized with config: %s", self.config)

    # ─── Component Accessors ───────────────────────────────────────────

    @property
    def change_detector(self):
        if self._change_detector is None:
            try:
                from .change_detector import ChangeDetector, WatchConfig
                watch_configs = []
                extensions = set(self.config.file_extensions or [])
                for directory in self.config.watch_directories:
                    watch_configs.append(
                        WatchConfig(
                            directory=directory,
                            extensions=extensions or {".pdf", ".docx", ".txt", ".md"},
                            recursive=True,
                            debounce_seconds=max(0.1, float(self.config.polling_interval_sec)),
                        )
                    )
                self._change_detector = ChangeDetector(watch_configs=watch_configs)
                logger.info("ChangeDetector loaded")
            except Exception as e:
                logger.warning("ChangeDetector not available: %s", e)
        return self._change_detector

    @property
    def docling_processor(self):
        if self._docling_processor is None:
            try:
                from .docling_parallel import DoclingParallelProcessor
                self._docling_processor = DoclingParallelProcessor(
                    max_workers=self.config.max_workers
                )
                logger.info("DoclingParallelProcessor loaded")
            except Exception as e:
                logger.warning("DoclingParallelProcessor not available: %s", e)
        return self._docling_processor

    @property
    def multimodal_rag(self):
        if self._multimodal_rag is None:
            try:
                from .multimodal_rag import MultiModalRAG
                self._multimodal_rag = MultiModalRAG(
                    chunk_size=self.config.chunk_size,
                    chunk_overlap=self.config.chunk_overlap,
                    include_tables=self.config.include_tables,
                    include_diagrams=self.config.include_diagrams,
                    include_formulas=self.config.include_formulas,
                )
                logger.info("MultiModalRAG loaded")
            except Exception as e:
                logger.warning("MultiModalRAG not available: %s", e)
        return self._multimodal_rag

    @property
    def strixkat_eval(self):
        if self._strixkat_eval is None:
            try:
                from .strixkat_eval import StrixKATEvaluator
                self._strixkat_eval = StrixKATEvaluator(
                    quality_threshold=self.config.quality_threshold,
                    auto_rollback=self.config.auto_rollback,
                )
                logger.info("StrixKATEvaluator loaded")
            except Exception as e:
                logger.warning("StrixKATEvaluator not available: %s", e)
        return self._strixkat_eval

    @property
    def eval_scheduler(self):
        """Lazy-Init des EvalSchedulers (Massnahme 1: Scheduled Eval-Job)."""
        if self._eval_scheduler is None:
            try:
                from .strixkat_eval import EvalScheduler, EvalResultPersistence
                engine = self.strixkat_eval
                if engine is None:
                    return None
                persistence = EvalResultPersistence()
                self._eval_scheduler = EvalScheduler(
                    engine=engine,
                    persistence=persistence,
                    interval_seconds=int(self.config.eval_interval_sec),
                )
                logger.info("EvalScheduler loaded (interval=%ds)", self.config.eval_interval_sec)
            except Exception as e:
                logger.warning("EvalScheduler not available: %s", e)
        return self._eval_scheduler

    # ─── Core Pipeline Methods ─────────────────────────────────────────

    async def process_document(self, doc: PipelineDocument) -> PipelineDocument:
        """
        Verarbeitet ein einzelnes Dokument durch die gesamte Pipeline.
        
        Flow: Validate -> Extract (Docling) -> Chunk (Multi-Modal) -> 
              Index -> Evaluate (StrixKAT)
        """
        start_time = time.monotonic()
        doc.pipeline_status = "processing"
        docling_processor = self.docling_processor if self.config.enable_docling else None
        multimodal_rag = self.multimodal_rag if self.config.enable_multimodal else None
        strixkat_eval = self.strixkat_eval if self.config.enable_evaluation else None
        
        try:
            # Step 1: PDF Extraction (Docling-Parallel)
            if self.config.enable_docling and docling_processor:
                if doc.source_path and doc.source_path.endswith('.pdf'):
                    if self.config.debug:
                        logger.info("Docling extraction for %s", doc.doc_id)
                    extraction_result = await asyncio.get_event_loop().run_in_executor(
                        self._executor,
                        docling_processor.process_single,
                        doc.source_path
                    )
                    if extraction_result and extraction_result.get("content"):
                        doc.content = extraction_result["content"]
                        doc.metadata.update(extraction_result.get("metadata", {}))
            
            # Step 2: Multi-Modal Chunking
            if self.config.enable_multimodal and multimodal_rag:
                if self.config.debug:
                    logger.info("Multi-Modal chunking for %s", doc.doc_id)
                chunks = multimodal_rag.chunk_document(
                    doc.content, doc.metadata
                )
                doc.chunks = chunks
            else:
                # Fallback: Standard chunking
                doc.chunks = self._simple_chunk(doc.content)
            
            # Step 3: Index in UnifiedRAGStore
            if self.unified_rag_store and doc.content:
                if self.config.debug:
                    logger.info("Indexing document %s in UnifiedRAGStore", doc.doc_id)
                index_result = self.unified_rag_store.add_document(
                    content=doc.content,
                    doc_id=doc.doc_id,
                    metadata=doc.metadata,
                )
                doc.metadata["index_result"] = index_result
            
            # Step 4: Mark as indexed
            doc.pipeline_status = "indexed"
            doc.processed_at = datetime.now(timezone.utc).isoformat()
            
            # Step 5: Schedule Evaluation (if not recently run)
            now = time.monotonic()
            if (self.config.enable_evaluation and 
                strixkat_eval and 
                now - self._last_eval_time >= self.config.eval_interval_sec):
                if self.config.debug:
                    logger.info("Scheduling StrixKAT evaluation")
                await self._run_evaluation()
                self._last_eval_time = now
            
            # Success
            doc.pipeline_status = "evaluated"
            self._metrics["documents_processed"] += 1
            
            elapsed = time.monotonic() - start_time
            if self.config.debug:
                logger.info(
                    "Document %s processed in %.2fs", doc.doc_id, elapsed
                )
            
        except Exception as e:
            doc.pipeline_status = "failed"
            doc.error_message = str(e)
            self._metrics["documents_failed"] += 1
            logger.error("Pipeline failed for %s: %s", doc.doc_id, e)
        
        return doc

    async def process_batch(self, documents: List[PipelineDocument]) -> PipelineResult:
        """
        Verarbeitet einen Batch von Dokumenten parallel.
        """
        start_time = time.monotonic()
        self._metrics["pipeline_runs"] += 1
        
        tasks = [self.process_document(doc) for doc in documents]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        success_count = 0
        failed_count = 0
        details = []

        for i, result in enumerate(results):
            if isinstance(result, Exception):
                failed_count += 1
                details.append({
                    "doc_id": documents[i].doc_id,
                    "status": "failed",
                    "error": str(result),
                })
            elif isinstance(result, PipelineDocument):
                success_count += 1
                self._document_registry[result.doc_id] = result
                details.append({
                    "doc_id": result.doc_id,
                    "status": result.pipeline_status,
                    "chunks": len(result.chunks),
                    "processed_at": result.processed_at,
                })
        
        elapsed = time.monotonic() - start_time
        self._metrics["avg_processing_time"] = (
            (self._metrics["avg_processing_time"] * (self._metrics["pipeline_runs"] - 1) + elapsed)
            / self._metrics["pipeline_runs"]
        )
        
        return PipelineResult(
            success=failed_count == 0,
            documents_processed=success_count,
            documents_failed=failed_count,
            processing_time_sec=round(elapsed, 3),
            details=details,
        )

    async def _run_evaluation(self) -> Dict[str, Any]:
        """Fuehrt eine StrixKAT Evaluation durch."""
        evaluator = self.strixkat_eval
        if not evaluator or not self.unified_rag_store:
            return {"status": "skipped", "reason": "evaluator or store unavailable"}
        
        try:
            if self.config.debug:
                logger.info("Running StrixKAT evaluation")
            
            eval_result = await asyncio.get_event_loop().run_in_executor(
                self._executor,
                evaluator.evaluate_full_pipeline,
                self.unified_rag_store,
            )
            
            self._last_quality_score = eval_result.get("overall_quality")
            
            if (self.config.auto_rollback and 
                self._last_quality_score and 
                self._last_quality_score < self.config.quality_threshold):
                logger.warning(
                    "Quality %0.3f below threshold %0.3f - triggering rollback",
                    self._last_quality_score,
                    self.config.quality_threshold,
                )
                await self._trigger_rollback()
                self._metrics["rollbacks"] += 1
            
            return {
                "status": "completed",
                "quality_score": self._last_quality_score,
                "details": eval_result,
            }
        
        except Exception as e:
            logger.error("Evaluation failed: %s", e)
            return {"status": "failed", "error": str(e)}

    async def _trigger_rollback(self) -> Dict[str, Any]:
        """Loest ein automatisches Rollback aus."""
        if not self.unified_rag_store:
            return {"status": "skipped", "reason": "no store"}
        
        try:
            if self.config.debug:
                logger.info("Triggering auto-rollback")
            
            evaluator = self.strixkat_eval
            if evaluator:
                rollback_result = evaluator.rollback_to_last_good_state(
                    self.unified_rag_store
                )
                return {"status": "rolled_back", "details": rollback_result}
            
            return {"status": "skipped", "reason": "no rollback handler"}
        
        except Exception as e:
            logger.error("Rollback failed: %s", e)
            return {"status": "rollback_failed", "error": str(e)}

    # ─── Change Detection Integration ────────────────────────────────

    async def scan_for_changes(self) -> List[PipelineDocument]:
        """
        Scannt auf Aenderungen und gibt neue/geaenderte Dokumente zurueck.
        """
        if not self.config.enable_change_detection or not self.change_detector:
            return []
        
        try:
            changes = self.change_detector.scan()
            documents = []
            
            for change in changes:
                if change.get("status") in ["new", "modified"]:
                    doc_id = self._compute_doc_id(change.get("path", ""))
                    doc = PipelineDocument(
                        doc_id=doc_id,
                        source_path=change.get("path"),
                        metadata={
                            "source": "change_detector",
                            "change_type": change.get("status"),
                            "detected_at": datetime.now(timezone.utc).isoformat(),
                        },
                    )
                    documents.append(doc)
            
            if documents and self.config.debug:
                logger.info("Detected %d changed documents", len(documents))
            
            return documents
        
        except Exception as e:
            logger.error("Change detection failed: %s", e)
            return []

    async def full_pipeline_run(self) -> PipelineResult:
        """
        Fuehrt einen kompletten Pipeline-Durchlauf durch:
        Change Detection -> Processing -> Evaluation
        """
        start_time = time.monotonic()
        
        # Step 1: Scan for changes
        documents = await self.scan_for_changes()
        
        if not documents:
            return PipelineResult(
                success=True,
                documents_processed=0,
                processing_time_sec=round(time.monotonic() - start_time, 3),
                details=[{"message": "No changes detected"}],
            )
        
        # Step 2: Process batch
        result = await self.process_batch(documents)
        result.processing_time_sec = round(time.monotonic() - start_time, 3)
        
        # Step 3: Evaluate if needed
        if self.config.enable_evaluation:
            eval_result = await self._run_evaluation()
            result.details.append({"evaluation": eval_result})
            result.avg_quality_score = self._last_quality_score
        
        return result

    # ─── Utilities ───────────────────────────────────────────────────

    @staticmethod
    def _compute_doc_id(path: str) -> str:
        """Berechnet eine eindeutige Doc-ID aus einem Pfad."""
        return hashlib.sha256(path.encode("utf-8")).hexdigest()[:16]

    @staticmethod
    def _simple_chunk(content: str, chunk_size: int = 1500, overlap: int = 200) -> List[Dict[str, Any]]:
        """Einfaches Text-Chunking als Fallback."""
        chunks: List[Dict[str, Any]] = []
        start = 0
        while start < len(content):
            end = min(start + chunk_size, len(content))
            chunk_text = content[start:end]
            chunks.append({
                "text": chunk_text,
                "chunk_idx": len(chunks),
                "content_type": "text",
                "start_pos": start,
                "end_pos": end,
            })
            start += chunk_size - overlap
        return chunks

    def get_metrics(self) -> Dict[str, Any]:
        """Gibt die aktuellen Pipeline-Metriken zurueck."""
        return {
            **self._metrics,
            "last_quality_score": self._last_quality_score,
            "documents_in_registry": len(self._document_registry),
            "pipeline_running": self._pipeline_running,
        }

    async def start_continuous_mode(self, callback: Optional[Callable] = None):
        """
        Startet den kontinuierlichen Pipeline-Betrieb.
        Scannt in regelmoeßigen Abstaenden auf Aenderungen.
        Startet optional den EvalScheduler (Massnahme 1).
        """
        self._pipeline_running = True
        logger.info("SOTA Pipeline continuous mode started")

        # EvalScheduler starten (Massnahme 1: Scheduled Eval-Job)
        if self.config.enable_evaluation:
            scheduler = self.eval_scheduler
            if scheduler is not None:
                scheduler.start()
                logger.info("EvalScheduler started via continuous mode")
        
        while self._pipeline_running:
            try:
                result = await self.full_pipeline_run()
                if callback:
                    await callback(result)
            except Exception as e:
                logger.error("Pipeline continuous loop error: %s", e)
            
            await asyncio.sleep(self.config.polling_interval_sec)

    def stop_continuous_mode(self):
        """Stoppt den kontinuierlichen Betrieb inkl. EvalScheduler."""
        self._pipeline_running = False
        # EvalScheduler stoppen
        if self._eval_scheduler is not None:
            self._eval_scheduler.stop()
            logger.info("EvalScheduler stopped")
        self._executor.shutdown(wait=False, cancel_futures=True)
        logger.info("SOTA Pipeline continuous mode stopped")

    def __del__(self):
        """Cleanup bei Destruktion."""
        try:
            self._executor.shutdown(wait=False, cancel_futures=True)
        except Exception:
            pass


# ─── Module-Level Convenience ──────────────────────────────────────────────

_pipeline_instance: Optional[SOTAPipeline] = None

def get_pipeline(config: Optional[PipelineConfig] = None, 
                 unified_rag_store=None) -> SOTAPipeline:
    """Singleton-Access zur Pipeline."""
    global _pipeline_instance
    if _pipeline_instance is None:
        _pipeline_instance = SOTAPipeline(config=config, unified_rag_store=unified_rag_store)
    return _pipeline_instance

def reset_pipeline():
    """Reset des Singletons ( fuer Tests )."""
    global _pipeline_instance
    if _pipeline_instance:
        _pipeline_instance.stop_continuous_mode()
    _pipeline_instance = None