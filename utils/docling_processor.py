"""
SOTA Document Processor based on IBM Docling
=============================================

Universeller Dokumenten-Prozessor für PDF, DOCX, PPTX, XLSX, HTML, CSV, MD, Images.
Nutzt Docling's AI-Modelle (TableFormer, Layout RT-DETR, OCR) für 
State-of-the-Art Dokumentenverständnis mit Struktur-Erkennung.

Features:
- AI-basierte Layout-Analyse (RT-DETR)
- TableFormer für präzise Tabellenerkennung
- EasyOCR (de/en, GPU-dynamisch) für gescannte Dokumente
- HierarchicalChunker für semantisch sinnvolle Chunks
- GPU-Accelerator für Layout RT-DETR + TableFormer (CUDA wenn VRAM≥1GB frei, sonst CPU-Fallback)
- Thread-safe Singleton Pattern
- Lazy Model Loading
- Robuster Fallback bei Fehlern

Autor: SOTA Bot Pipeline
Version: 1.0.0 (2025-07-18)
"""

from __future__ import annotations

import gc
import logging
import os
import tempfile
import threading
import time
from pathlib import Path
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import requests
from utils.runtime_policy import OutboundNetworkBlockedError, parse_bool_env

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════
# MIME-Type / Extension Mapping
# ═══════════════════════════════════════════════════════════════════════

# Content-Type → Docling InputFormat Mapping
MIME_TO_FORMAT: Dict[str, str] = {
    "application/pdf": "PDF",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "DOCX",
    "application/msword": "DOCX",  # Legacy .doc (Docling tries best-effort)
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": "PPTX",
    "application/vnd.ms-powerpoint": "PPTX",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "XLSX",
    "application/vnd.ms-excel": "XLSX",
    "text/html": "HTML",
    "application/xhtml+xml": "HTML",
    "text/csv": "CSV",
    "text/markdown": "MD",
    "text/plain": "MD",  # Plain text → treat as markdown
    "image/png": "IMAGE",
    "image/jpeg": "IMAGE",
    "image/tiff": "IMAGE",
    "image/bmp": "IMAGE",
    "image/webp": "IMAGE",
}

# Extension → Docling InputFormat Mapping (v2.75: alle unterstützten Formate)
EXT_TO_FORMAT: Dict[str, str] = {
    ".pdf": "PDF",
    ".docx": "DOCX",
    ".doc": "DOCX",  # Legacy, best effort
    ".pptx": "PPTX",
    ".ppt": "PPTX",
    ".xlsx": "XLSX",
    ".xls": "XLSX",
    ".html": "HTML",
    ".htm": "HTML",
    ".csv": "CSV",
    ".md": "MD",
    ".markdown": "MD",
    ".txt": "MD",
    ".png": "IMAGE",
    ".jpg": "IMAGE",
    ".jpeg": "IMAGE",
    ".tiff": "IMAGE",
    ".tif": "IMAGE",
    ".bmp": "IMAGE",
    ".webp": "IMAGE",
    # v2.75 neue Formate
    ".asciidoc": "ASCIIDOC",
    ".adoc": "ASCIIDOC",
    ".asc": "ASCIIDOC",
    ".tex": "LATEX",
    ".latex": "LATEX",
    ".vtt": "VTT",
    ".xml": "HTML",  # Fallback
    ".json": "MD",   # Fallback
}

# Extensions die Docling nativ unterstützt (für URL-Download-Entscheidung)
DOCLING_SUPPORTED_EXTENSIONS = {
    ".pdf", ".docx", ".pptx", ".xlsx", ".html", ".htm",
    ".csv", ".md", ".png", ".jpg", ".jpeg", ".tiff", ".tif", ".bmp",
    ".asciidoc", ".adoc", ".asc", ".tex", ".latex", ".vtt",
}

# MIME-Types die einen Download für Docling-Verarbeitung auslösen
DOCLING_DOWNLOAD_MIME_TYPES = {
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "application/vnd.ms-powerpoint",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.ms-excel",
    "image/png", "image/jpeg", "image/tiff", "image/bmp", "image/webp",
}


# ═══════════════════════════════════════════════════════════════════════
# Result Data Classes
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class DoclingChunk:
    """Ein Chunk aus der Docling-Verarbeitung"""
    text: str
    metadata: Dict[str, Any] = field(default_factory=dict)
    # Optional: Heading path für hierarchische Navigation
    headings: List[str] = field(default_factory=list)
    # Chunk-Index
    index: int = 0


@dataclass
class DoclingResult:
    """Ergebnis einer Docling-Dokumentenkonvertierung"""
    success: bool
    text: str = ""          # Volltext als Markdown
    chunks: List[DoclingChunk] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None
    error_code: Optional[str] = None
    processing_time_s: float = 0.0
    format_detected: str = "unknown"
    num_pages: int = 0
    num_tables: int = 0
    num_figures: int = 0
    extraction_method: str = "docling"
    is_partial: bool = False  # True wenn Docling PARTIAL_SUCCESS (z.B. Timeout, Page-Fehler)
    # ★ SOTA Dual-Chunker: KG-optimierte Chunks (max_tokens=1200 ≈ 4800 Chars)
    # Separat von Retrieval-Chunks (max_tokens=384), weil optimale Chunk-Größe
    # für Retrieval (kurz, präzise) ≠ KG-Extraktion (lang, kontextreich).
    # Docling's HybridChunker erzeugt beide nativ (gleiche Dokumentstruktur).
    kg_chunks: List[DoclingChunk] = field(default_factory=list)


# ═══════════════════════════════════════════════════════════════════════
# Docling Processor Singleton
# ═══════════════════════════════════════════════════════════════════════

class DoclingProcessor:
    """
    SOTA Document Processor using IBM Docling
    
    Thread-safe Singleton mit lazy Model Loading.
    Alle AI-Modelle laufen auf CPU um GPU-VRAM für das LLM freizuhalten.
    
    Unterstützte Formate:
    - PDF (mit Layout-Analyse, Tabellenerkennung, OCR)
    - DOCX (Word-Dokumente)
    - PPTX (PowerPoint-Präsentationen)
    - XLSX (Excel-Tabellen)
    - HTML (Webseiten)
    - CSV, Markdown, Text
    - Images (PNG, JPG, TIFF → OCR)
    
    Usage:
        processor = DoclingProcessor.get_instance()
        result = processor.convert_file("document.pdf")
        if result.success:
            for chunk in result.chunks:
                print(chunk.text)
    """
    
    _instance: Optional[DoclingProcessor] = None
    _lock = threading.Lock()
    _init_lock = threading.Lock()
    
    def __init__(self) -> None:
        """Nicht direkt aufrufen - nutze get_instance()"""
        self._converter: Optional[Any] = None
        self._initialized = False
        self._model_load_time: float = 0.0
        self._conversions_count: int = 0
        self._total_processing_time: float = 0.0
        # Global APP_LOCAL_ONLY has precedence. DOCLING_LOCAL_ONLY can only tighten,
        # not loosen, local-only behavior for this processor.
        self._local_only = parse_bool_env("APP_LOCAL_ONLY", "0") or parse_bool_env("DOCLING_LOCAL_ONLY", "0")
        if self._local_only:
            os.environ.setdefault("HF_HUB_OFFLINE", "1")
            os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
            os.environ.setdefault("HF_DATASETS_OFFLINE", "1")
        
    @classmethod
    def get_instance(cls) -> DoclingProcessor:
        """Thread-safe Singleton Factory"""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = DoclingProcessor()
        return cls._instance
    
    def _ensure_initialized(self) -> None:
        """Lazy-Initialize Docling Converter.
        
        Docling v2.75 Defaults (SOTA, werden NICHT überschrieben):
          - Layout: docling-layout-heron (RT-DETR)
          - Tabellen: TableFormer ACCURATE + cell_matching
        
        Explizit gesetzt (abweichend von Defaults):
          - device=CUDA wenn ≥1 GB VRAM frei, sonst CPU-Fallback
          - num_threads=24 (Ryzen 9 5950X, CPU-Fallback/Prompt-Processing)
          - document_timeout=None (kein Timeout → verhindert Seiten-Abschneidung bei OCR-intensiven PDFs)
          - queue_max_size=20 (statt Default 100 → verhindert std::bad_alloc)
          - OCR=EasyOCR de/en (statt RapidOCR mit chinesischen Modellen)
          - Batch-Sizes: 4 (GPU) oder 2 (CPU) — GPU hat genug VRAM
          - allowed_formats=None (alle verfügbaren Formate)
          
        VRAM-Budget (RTX 4090 = 24 GB):
          Layout-Heron:      ~164 MB (safetensors)
          TableFormer ACC:   ~203 MB (safetensors)
          EasyOCR:            ~94 MB (CRAFT+Latin)
          Batch-Intermediates: ~200 MB
          ─────────────────────────────
          Total Docling GPU:  ~661 MB ≈ 0.65 GB
        """
        if self._initialized:
            return
            
        with self._init_lock:
            if self._initialized:
                return
                
            t0 = time.perf_counter()
            
            from docling.document_converter import DocumentConverter, PdfFormatOption
            from docling.datamodel.pipeline_options import (
                PdfPipelineOptions,
                EasyOcrOptions,
                LayoutOptions,
                TableStructureOptions,
            )
            from docling.datamodel.accelerator_options import (
                AcceleratorOptions,
                AcceleratorDevice,
            )
            from docling.datamodel.base_models import InputFormat
            
            # ═══════════════════════════════════════════════════════
            # GPU-Accelerator für Layout RT-DETR + TableFormer
            #
            # Bisheriger Zustand: device=CPU → 10-50x langsamer als
            # GPU für die PyTorch-basierten Modelle (RT-DETR, TableFormer).
            #
            # Docling-Modelle benötigen nur ~0.65 GB VRAM total:
            #   Layout-Heron:    164 MB
            #   TableFormer ACC: 203 MB
            #   EasyOCR:          94 MB
            #   Intermediates:  ~200 MB
            # → RTX 4090 hat auch mit LLM geladen noch ~8 GB frei.
            #
            # Fallback: CPU mit 24 Threads wenn VRAM < 1 GB frei.
            # ═══════════════════════════════════════════════════════
            _use_cuda = False
            _free_vram_gb = 0.0
            # Dual-GPU: Docling-Modelle auf der AUX-GPU (RTX 3060 Ti)
            _aux_idx = 0
            try:
                from utils.gpu_devices import get_placement
                _aux_idx = get_placement().aux_cuda
            except Exception:
                _aux_idx = 0
            import torch as _torch_probe
            if _torch_probe.cuda.is_available():
                _free_vram_gb = _torch_probe.cuda.mem_get_info(_aux_idx)[0] / (1024**3)
                # Docling GPU braucht ~0.65 GB, 1.0 GB Schwelle = sicherer Puffer
                _use_cuda = _free_vram_gb >= 1.0
                logger.info(
                    f"Docling Accelerator (cuda:{_aux_idx}): free VRAM={_free_vram_gb:.1f} GB "
                    f"→ {'CUDA' if _use_cuda else 'CPU (VRAM zu knapp)'}"
                )
            del _torch_probe
            
            if _use_cuda:
                _optimal_threads = max(4, int((os.cpu_count() or 8) * 0.75))
                # Dual-GPU: cuda:N wird von Docling validiert (validate_device)
                accelerator = AcceleratorOptions(
                    device=f"cuda:{_aux_idx}",
                    num_threads=_optimal_threads,  # Auto-detect: 75% of logical cores
                )
                # GPU kann größere Batches effizient verarbeiten
                _batch_size = 4
            else:
                _optimal_threads = max(4, int((os.cpu_count() or 8) * 0.75))
                accelerator = AcceleratorOptions(
                    device=AcceleratorDevice.CPU,
                    num_threads=_optimal_threads,
                )
                # CPU: Kleinere Batches für RAM-Stabilität
                _batch_size = 2
            
            # ═══════════════════════════════════════════════════════
            # M2: EasyOCR statt RapidOCR (Root Cause #2)
            # RapidOCR hat hardcoded chinesische Modelle (ch_PP-OCRv4_*),
            # `lang`-Parameter ist "reserved for future" → OCR-Qualität
            # für deutsche Texte ist mangelhaft.
            # EasyOCR unterstützt de/en nativ mit dedizierten Modellen.
            # GPU-Nutzung: Automatisch von accelerator_options.device
            # abgeleitet (use_gpu=None → docling prüft AcceleratorDevice).
            # ═══════════════════════════════════════════════════════
            ocr_options = EasyOcrOptions(
                lang=["de", "en"],
                confidence_threshold=0.5,
                # Local-only: kein Model-Download zur Laufzeit.
                download_enabled=not self._local_only,
            )
            
            # ═══════════════════════════════════════════════════════
            # M1: Pipeline Memory (Root Cause #1)
            # queue_max_size=100 (Default) erlaubt ~400 Seiten
            # gleichzeitig in den 5 Pipeline-Stages → std::bad_alloc.
            # Reduziert auf 20 als Safety-Netz.
            # Batch-Sizes: 4 (GPU) oder 2 (CPU).
            # TableFormer ACCURATE und Layout-Heron bleiben Default.
            # ═══════════════════════════════════════════════════════
            # ═══════════════════════════════════════════════════════
            # document_timeout=None: Kein artifzielles Timeout.
            #
            # BEGRÜNDUNG (Root-Cause-Fix):
            # document_timeout=300s hat bei OCR-intensiven Scan-PDFs
            # dazu geführt, dass die Threaded-Pipeline mitten in der
            # Verarbeitung abgebrochen wurde. Der OCR-Thread hing in
            # einem blockierenden EasyOCR-Call und konnte nicht sauber
            # terminieren → Thread-Leak + abgeschnittene Ergebnisse
            # (z.B. 3310 Zeichen statt 30.000+).
            #
            # Das Timeout ist die URSACHE des Seiten-Verlusts, nicht
            # ein Schutz davor. EasyOCR auf der RTX 4090 (~94MB VRAM)
            # terminiert immer — es gibt keinen Deadlock-Pfad.
            # Bei 20 Seiten × ~30s/Seite = ~600s ist 300s zu kurz.
            #
            # Schutz gegen pathologische Fälle erfolgt stattdessen durch:
            # 1. queue_max_size=20 (Memory-Backpressure)
            # 2. conv_result.status-Prüfung nach Konvertierung
            # ═══════════════════════════════════════════════════════
            pdf_options = PdfPipelineOptions(
                accelerator_options=accelerator,
                document_timeout=None,
                queue_max_size=20,
                ocr_batch_size=_batch_size,
                layout_batch_size=_batch_size,
                table_batch_size=_batch_size,
                ocr_options=ocr_options,
            )

            # Hart auf lokal: Keine externen OCR/LLM-Services im Pipeline-Pfad.
            if hasattr(pdf_options, "enable_remote_services"):
                setattr(pdf_options, "enable_remote_services", False)

            # Optionaler lokaler Modellpfad (vorab heruntergeladene Artefakte).
            local_artifacts_path = os.getenv("DOCLING_ARTIFACTS_PATH", "").strip()
            if self._local_only and local_artifacts_path:
                if os.path.isdir(local_artifacts_path):
                    setattr(pdf_options, "artifacts_path", str(Path(local_artifacts_path)))
                else:
                    logger.warning(
                        f"Docling local-only: DOCLING_ARTIFACTS_PATH existiert nicht: {local_artifacts_path}"
                    )
            
            # ═══════════════════════════════════════════════════════
            # Converter: allowed_formats=None → alle Formate
            # (PDF, DOCX, PPTX, XLSX, HTML, CSV, MD, IMAGE,
            #  ASCIIDOC, LATEX, AUDIO, VTT, XML_USPTO, ...)
            # format_options nur für PDF (Custom Accelerator)
            # ═══════════════════════════════════════════════════════
            self._converter = DocumentConverter(
                format_options={
                    InputFormat.PDF: PdfFormatOption(
                        pipeline_options=pdf_options,
                    ),
                },
            )
            
            self._initialized = True
            self._model_load_time = time.perf_counter() - t0
            self._device = f"cuda:{_aux_idx}" if _use_cuda else "cpu"
            
            _device_str = f"CUDA (free={_free_vram_gb:.1f}GB)" if _use_cuda else f"CPU/{accelerator.num_threads}T"
            # Type-narrow to the concrete subclasses for safe attribute access
            _layout: LayoutOptions = pdf_options.layout_options  # type: ignore[assignment]
            _table: TableStructureOptions = pdf_options.table_structure_options  # type: ignore[assignment]
            logger.info(
                f"✅ DoclingProcessor v2.75 initialisiert in {self._model_load_time:.1f}s "
                f"({_device_str}, "
                f"Layout={_layout.model_spec.name}, "
                f"Table={_table.mode}, "
                f"OCR=EasyOCR de/en GPU={'on' if _use_cuda else 'off'}, "
                f"queue={pdf_options.queue_max_size}, Batch={_batch_size}, "
                f"local_only={'on' if self._local_only else 'off'})"
            )
    
    # ═══════════════════════════════════════════════════════════════════
    # Public API: File Conversion
    # ═══════════════════════════════════════════════════════════════════
    
    def convert_file(
        self,
        file_path: str,
        *,
        chunk_size: int = 1500,
        chunk_overlap: int = 200,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> DoclingResult:
        """
        Konvertiere eine lokale Datei mit Docling.
        
        Args:
            file_path: Absoluter Pfad zur Datei
            chunk_size: Ziel-Chunk-Größe in Zeichen (für Fallback-Splitting)
            chunk_overlap: Überlappung zwischen Chunks
            metadata: Zusätzliche Metadaten für die Chunks
            
        Returns:
            DoclingResult mit Text, Chunks und Metadaten
        """
        t0 = time.perf_counter()
        
        if not os.path.exists(file_path):
            return DoclingResult(
                success=False,
                error=f"File not found: {file_path}",
                error_code="file_not_found",
            )
        
        file_ext = os.path.splitext(file_path)[1].lower()
        format_name = EXT_TO_FORMAT.get(file_ext, "unknown")
        
        if format_name == "unknown":
            return DoclingResult(
                success=False,
                error=f"Unsupported file extension: {file_ext}",
                error_code="unsupported_format",
                format_detected=format_name,
            )
        
        try:
            self._ensure_initialized()
            
            logger.info(f"📄 Docling: Konvertiere {os.path.basename(file_path)} ({format_name})")
            
            # Docling Conversion
            assert self._converter is not None, "Converter not initialized"
            conv_result = self._converter.convert(file_path)
            
            # ═══════════════════════════════════════════════════════
            # Status-Prüfung: ConversionStatus auswerten
            #
            # Docling setzt conv_result.status auf:
            #   SUCCESS        → alle Seiten verarbeitet
            #   PARTIAL_SUCCESS → Timeout/Seiten-Fehler (Daten unvollständig)
            #   FAILURE        → kompletter Fehlschlag
            #
            # PARTIAL_SUCCESS darf NICHT als success=True gemeldet werden,
            # da sonst abgeschnittene Ergebnisse permanent gecacht werden.
            # ═══════════════════════════════════════════════════════
            from docling.datamodel.base_models import ConversionStatus
            
            _is_partial = False
            _conv_status = getattr(conv_result, 'status', None)
            
            if _conv_status == ConversionStatus.FAILURE:
                _errors = []
                if hasattr(conv_result, 'errors'):
                    _errors = [str(e.error_message) for e in conv_result.errors[:5]]
                error_detail = "; ".join(_errors) if _errors else "Unknown failure"
                logger.error(
                    f"❌ Docling FAILURE für {os.path.basename(file_path)}: {error_detail}"
                )
                return DoclingResult(
                    success=False,
                    error=f"Docling conversion failed: {error_detail}",
                    error_code="conversion_failure",
                    format_detected=format_name,
                    processing_time_s=time.perf_counter() - t0,
                )
            
            if _conv_status == ConversionStatus.PARTIAL_SUCCESS:
                _is_partial = True
                _errors = []
                if hasattr(conv_result, 'errors'):
                    _errors = [str(e.error_message) for e in conv_result.errors[:5]]
                logger.warning(
                    f"⚠️ Docling PARTIAL_SUCCESS für {os.path.basename(file_path)}: "
                    f"{len(_errors)} Fehler: {'; '.join(_errors[:3])}"
                )
            
            # ═══════════════════════════════════════════════════════
            # Page-Loss Detection
            # Vergleich Input vs Output → Logging + Metadata.
            # ═══════════════════════════════════════════════════════
            _page_loss_ratio = 0.0
            _input_page_count = (
                getattr(conv_result.input, 'page_count', 0)
                if hasattr(conv_result, 'input') else 0
            )
            _output_page_count = (
                len(conv_result.document.pages)
                if hasattr(conv_result.document, 'pages') and conv_result.document.pages
                else 0
            )
            
            if _input_page_count > 0 and _output_page_count > 0:
                _page_loss_ratio = 1.0 - (_output_page_count / _input_page_count)
                if _page_loss_ratio > 0.3:
                    logger.error(
                        f"CRITICAL page loss: {_input_page_count} input → {_output_page_count} output "
                        f"({_page_loss_ratio:.0%} lost) for {os.path.basename(file_path)}"
                    )
                    _is_partial = True  # Page-Loss > 30% ist de facto partial
                elif _page_loss_ratio > 0.1:
                    logger.warning(
                        f"Page loss detected: {_input_page_count} → {_output_page_count} "
                        f"({_page_loss_ratio:.0%} lost) for {os.path.basename(file_path)}"
                    )
            
            # Export als Markdown (SOTA: behält Struktur bei)
            markdown_text = conv_result.document.export_to_markdown()
            
            if not markdown_text or not markdown_text.strip():
                return DoclingResult(
                    success=False,
                    error="Docling produced empty output",
                    error_code="empty_output",
                    format_detected=format_name,
                    processing_time_s=time.perf_counter() - t0,
                )
            
            # Statistiken sammeln
            num_pages = 0
            num_tables = 0
            num_figures = 0
            
            try:
                doc = conv_result.document
                # Page count
                if hasattr(doc, 'pages') and doc.pages:
                    num_pages = len(doc.pages)
                # Table/Figure count from document items
                if hasattr(doc, 'tables'):
                    num_tables = len(list(doc.tables))
                if hasattr(doc, 'pictures'):
                    num_figures = len(list(doc.pictures))
            except Exception:
                pass  # Statistiken sind optional
            
            # ═══════════════════════════════════════════════════════
            # SOTA Chunking: HybridChunker (Token-aware, merge_peers)
            # Nutzt Docling's Dokumentstruktur + Token-Limits für
            # semantisch sinnvolle Chunks mit konsistenter Größe
            # ═══════════════════════════════════════════════════════
            chunks = self._hybrid_chunk(
                conv_result,
                chunk_size=chunk_size,
                metadata=metadata,
                file_path=file_path,
                format_name=format_name,
            )
            
            # Fallback: Wenn HybridChunker keine Chunks liefert
            # (z.B. Dokument ohne Struktur → manuelles Markdown-Splitting)
            if not chunks:
                chunks = self._fallback_chunk(
                    markdown_text,
                    chunk_size=chunk_size,
                    chunk_overlap=chunk_overlap,
                    metadata=metadata,
                    file_path=file_path,
                    format_name=format_name,
                )
            
            # ═══════════════════════════════════════════════════════
            # ★ SOTA Dual-Chunker: KG-optimierte Chunks (max_tokens=1200)
            # Zweiter HybridChunker-Pass auf demselben DoclingDocument.
            # Kein Re-Parsing, nur Re-Chunking (~1-2s Extra).
            # ═══════════════════════════════════════════════════════
            kg_chunks: List[DoclingChunk] = []
            try:
                kg_chunks = self._hybrid_chunk_for_kg(
                    conv_result,
                    metadata=metadata,
                    file_path=file_path,
                    format_name=format_name,
                )
            except Exception as e:
                logger.warning(f"⚠️ KG-Chunker fehlgeschlagen: {e} — KG nutzt Retrieval-Chunks als Fallback")
            
            elapsed = time.perf_counter() - t0
            self._conversions_count += 1
            self._total_processing_time += elapsed
            
            result = DoclingResult(
                success=True,
                text=markdown_text,
                chunks=chunks,
                metadata={
                    "source": file_path,
                    "filename": os.path.basename(file_path),
                    "format": format_name,
                    "file_size_bytes": os.path.getsize(file_path),
                    "num_chunks": len(chunks),
                    "num_kg_chunks": len(kg_chunks),
                    "page_loss_ratio": _page_loss_ratio,
                    "input_page_count": _input_page_count,
                    "output_page_count": _output_page_count,
                    "conversion_status": str(_conv_status) if _conv_status else "unknown",
                    **(metadata or {}),
                },
                processing_time_s=elapsed,
                format_detected=format_name,
                num_pages=num_pages,
                num_tables=num_tables,
                num_figures=num_figures,
                extraction_method="docling",
                is_partial=_is_partial,
                kg_chunks=kg_chunks,
            )
            
            _status_icon = "⚠️" if _is_partial else "✅"
            _partial_suffix = f" [PARTIAL: {_page_loss_ratio:.0%} page loss]" if _is_partial else ""
            _kg_info = f", {len(kg_chunks)} kg-chunks" if kg_chunks else ""
            logger.info(
                f"{_status_icon} Docling: {os.path.basename(file_path)} → "
                f"{len(markdown_text)} chars, {len(chunks)} chunks{_kg_info}, "
                f"{num_tables} tables, {elapsed:.1f}s{_partial_suffix}"
            )
            
            return result
            
        except Exception as e:
            elapsed = time.perf_counter() - t0
            error_code = "conversion_exception"
            if isinstance(e, OutboundNetworkBlockedError):
                error_code = "local_only_network_blocked"
            elif self._local_only and isinstance(e, RuntimeError):
                error_code = "local_only_resource_missing"
            logger.error(f"❌ Docling conversion failed for {file_path}: {e}")
            return DoclingResult(
                success=False,
                error=str(e),
                error_code=error_code,
                format_detected=format_name,
                processing_time_s=elapsed,
            )
        finally:
            gc.collect()
    
    # ═══════════════════════════════════════════════════════════════════
    # Public API: URL Conversion
    # ═══════════════════════════════════════════════════════════════════
    
    def convert_url(
        self,
        url: str,
        *,
        chunk_size: int = 1500,
        chunk_overlap: int = 200,
        metadata: Optional[Dict[str, Any]] = None,
        timeout: int = 30,
    ) -> DoclingResult:
        """
        Lade ein Dokument von einer URL herunter und konvertiere es.
        
        SOTA Download-Validierung (Fix für HTTP-Error-Seiten-Misklassifikation):
        - Prädiktive Validierung: HEAD-Request mit Größen-Check
        - Post-Download-Validierung: Tatsächliche Größe vs Headers
        - Magic Byte Validierung: Datei-Format Verifizierung
        - Retry-Logik: Exponentieller Backoff für transiente Fehler
        - Adaptive Timeouts: Basierend auf Content-Length
        
        Behebt Root Cause des 0.2KB-Fehlers:
        - Server gab wahrscheinlich HTML-Fehlerseite zurück
        - Alte Logik: Blind geschrieben ohne Validierung
        - Neue Logik: 3-Ebenen Validierung + Retry
        
        Args:
            url: URL des Dokuments
            chunk_size: Ziel-Chunk-Größe
            chunk_overlap: Chunk-Überlappung
            metadata: Zusätzliche Metadaten
            timeout: HTTP Download-Timeout (wird adaptive ggf. erhöht)
            
        Returns:
            DoclingResult mit Text, Chunks und Metadaten
        """
        t0 = time.perf_counter()

        # Local-only Guard: keine externen Downloads in diesem Modus.
        if self._local_only:
            return DoclingResult(
                success=False,
                error=(
                    "Docling local-only mode aktiv: URL-Downloads sind deaktiviert. "
                    "Bitte lade die Datei lokal und nutze convert_file()."
                ),
                error_code="local_only_url_disabled",
                processing_time_s=time.perf_counter() - t0,
            )
        
        try:
            import requests
        except ImportError:
            return DoclingResult(
                success=False,
                error="requests library not available",
                error_code="requests_missing",
            )
        
        tmp_path: Optional[str] = None
        
        try:
            # ─── Dateityp bestimmen ───────────────────────────────
            format_name, file_ext = self._detect_url_format(url, timeout=timeout)
            
            if format_name == "unknown":
                return DoclingResult(
                    success=False,
                    error=f"Could not determine document format for URL: {url}",
                    error_code="format_detection_failed",
                    format_detected="unknown",
                    processing_time_s=time.perf_counter() - t0,
                )
            
            logger.info(f"📥 Docling: Downloading {format_name} from {url}")
            
            # ─── Prädiktive Validierung (HEAD-Request) ───────────
            predictive_check = self._validate_url_download(url, format_name, timeout)
            if not predictive_check["valid"]:
                logger.warning(f"⚠️ Pre-download validation failed: {predictive_check['reason']}")
                return DoclingResult(
                    success=False,
                    error=f"Pre-download validation failed: {predictive_check['reason']}",
                    error_code="pre_download_validation_failed",
                    format_detected=format_name,
                    processing_time_s=time.perf_counter() - t0,
                )
            
            # Adaptive Timeout basierend auf Content-Length
            predicted_size_mb = predictive_check.get("content_length_mb", 0)
            adaptive_timeout = self._calculate_adaptive_timeout(predicted_size_mb, base_timeout=timeout)
            logger.debug(f"Adaptive timeout: {adaptive_timeout}s (predicted size: {predicted_size_mb:.1f} MB)")
            
            # ─── Download mit Retry-Logik ───────────────────────
            tmp_path = self._download_with_retry(
                url, format_name, file_ext, adaptive_timeout
            )
            
            if not tmp_path:
                return DoclingResult(
                    success=False,
                    error=f"Download failed after retries for {url}",
                    error_code="download_failed",
                    format_detected=format_name,
                    processing_time_s=time.perf_counter() - t0,
                )
            
            # ─── Post-Download Validierung ──────────────────────
            validation_result = self._validate_downloaded_file(
                tmp_path, format_name, predictive_check.get("expected_size_bytes", 0)
            )
            
            if not validation_result["valid"]:
                logger.error(f"❌ Post-download validation failed: {validation_result['reason']}")
                return DoclingResult(
                    success=False,
                    error=f"Downloaded file validation failed: {validation_result['reason']}",
                    error_code="download_validation_failed",
                    format_detected=format_name,
                    processing_time_s=time.perf_counter() - t0,
                )
            
            actual_size = os.path.getsize(tmp_path)
            logger.info(f"📥 Downloaded {actual_size / 1024:.1f} KB → {tmp_path} ✓")
            
            # ─── Docling Conversion ──────────────────────────────
            merged_meta = {
                "source_url": url,
                "canonical_url": url,
                "source_type": f"web_{format_name.lower()}",
                "content_type": MIME_TO_FORMAT.get(format_name, format_name),
                "download_validated": True,
                "validation_checks": {
                    "pre_download": predictive_check,
                    "post_download": validation_result,
                },
                **(metadata or {}),
            }
            
            result = self.convert_file(
                tmp_path,
                chunk_size=chunk_size,
                chunk_overlap=chunk_overlap,
                metadata=merged_meta,
            )
            
            # URL-spezifische Metadaten ergänzen
            result.metadata["source_url"] = url
            result.metadata["canonical_url"] = url
            result.metadata["download_size_bytes"] = actual_size
            
            return result
            
        except requests.exceptions.Timeout:
            return DoclingResult(
                success=False,
                error=f"Download timeout after retries for {url}",
                error_code="download_timeout",
                processing_time_s=time.perf_counter() - t0,
            )
        except requests.exceptions.HTTPError as e:
            return DoclingResult(
                success=False,
                error=f"HTTP error downloading {url}: {e}",
                error_code="download_http_error",
                processing_time_s=time.perf_counter() - t0,
            )
        except Exception as e:
            return DoclingResult(
                success=False,
                error=f"URL conversion failed: {e}",
                error_code="url_conversion_exception",
                processing_time_s=time.perf_counter() - t0,
            )
        finally:
            # Cleanup Temp-Datei
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
            gc.collect()
    
    # ═══════════════════════════════════════════════════════════════════
    # Public API: Format Detection
    # ═══════════════════════════════════════════════════════════════════
    
    @staticmethod
    def detect_format_from_url(url: str, timeout: int = 5) -> Tuple[str, bool]:
        """
        Prüfe ob eine URL ein von Docling unterstütztes Dokument enthält.
        
        Returns:
            Tuple[format_name, should_use_docling]
            z.B. ("PDF", True) oder ("HTML", False) oder ("unknown", False)
        """
        # 1. Extension-Check
        from urllib.parse import urlparse, unquote
        parsed = urlparse(unquote(url))
        path = parsed.path.lower()
        
        for ext, fmt in EXT_TO_FORMAT.items():
            if path.endswith(ext):
                # HTML-Seiten brauchen kein Docling (existierender HTML-Parser reicht)
                use_docling = ext not in {".html", ".htm", ".txt", ".md", ".csv"}
                return fmt, use_docling
        
        # 2. HEAD-Request für Content-Type
        try:
            import requests
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            }
            head = requests.head(url, headers=headers, timeout=timeout, allow_redirects=True)
            content_type = head.headers.get('Content-Type', '').lower().split(';')[0].strip()
            
            if content_type in MIME_TO_FORMAT:
                fmt = MIME_TO_FORMAT[content_type]
                use_docling = content_type in DOCLING_DOWNLOAD_MIME_TYPES
                return fmt, use_docling
                
        except Exception:
            pass
        
        return "unknown", False
    
    @staticmethod
    def is_docling_supported(file_path: str) -> bool:
        """Prüfe ob eine lokale Datei von Docling unterstützt wird"""
        ext = os.path.splitext(file_path)[1].lower()
        return ext in EXT_TO_FORMAT
    
    # ═══════════════════════════════════════════════════════════════════
    # Internal: SOTA HybridChunker (Docling v2.65+)
    # ═══════════════════════════════════════════════════════════════════
    
    def _hybrid_chunk(
        self,
        conv_result: Any,
        *,
        chunk_size: int = 1500,
        metadata: Optional[Dict[str, Any]] = None,
        file_path: str = "",
        format_name: str = "",
    ) -> List[DoclingChunk]:
        """
        SOTA Chunking mit Docling HybridChunker.
        
        Vorteile gegenüber HierarchicalChunker:
          - Token-aware: Native Token-Zählung via HuggingFace Tokenizer
          - merge_peers: Semantisch zusammengehörige Abschnitte werden gruppiert
          - Keine manuelle Sub-Splitting-Logik nötig (max_tokens ist nativ)
          - Bessere Chunk-Größenkonsistenz
        
        Args:
            conv_result: Docling ConversionResult
            chunk_size: Maximale Chunk-Größe in Zeichen (wird zu Token konvertiert)
            metadata: Zusätzliche Chunk-Metadaten
            file_path: Quell-Dateipfad
            format_name: Erkanntes Format
        """
        from docling_core.transforms.chunker.hybrid_chunker import HybridChunker

        # ═══════════════════════════════════════════════════════
        # SOTA: E5-large max_seq_length = 514 tokens.
        # MTEB-Benchmarks zeigen optimale Retrieval-Qualität bei
        # 70-80% der max Sequenzlänge → 384 Tokens (74.7%).
        # Fester Wert statt chunk_size//3, da HybridChunker nativ
        # in Tokens arbeitet und E5-Tokenizer exakt zählt.
        # ═══════════════════════════════════════════════════════
        max_tokens = 384

        # Expliziter lokaler E5-Tokenizer — verhindert, dass Docling's
        # deprecated _patch model_validator MiniLM-L6-v2 (falscher
        # Tokenizer, offline nicht verfügbar) lädt.
        tokenizer = self._load_e5_tokenizer(max_tokens, local_only=self._local_only)

        chunker = HybridChunker(
            tokenizer=tokenizer,
            merge_peers=True,
        )
        
        chunks: List[DoclingChunk] = []
        doc = conv_result.document
        
        for i, chunk_iter in enumerate(chunker.chunk(doc)):
            chunk_text = chunk_iter.text
            
            if not chunk_text or not chunk_text.strip():
                continue
            
            chunks.append(DoclingChunk(
                text=chunk_text,
                metadata={
                    "source": file_path,
                    "format": format_name,
                    "chunk_method": "docling_hybrid",
                    "chunk_idx": i,
                    "max_tokens": max_tokens,
                    **(metadata or {}),
                },
                headings=self._extract_headings(chunk_iter),
                index=len(chunks),
            ))
        
        if chunks:
            avg_chars = sum(len(c.text) for c in chunks) // len(chunks)
            logger.debug(
                f"HybridChunker: {len(chunks)} chunks, "
                f"max_tokens={max_tokens}, avg {avg_chars} chars"
            )
        
        return chunks
    
    def _hybrid_chunk_for_kg(
        self,
        conv_result: Any,
        *,
        metadata: Optional[Dict[str, Any]] = None,
        file_path: str = "",
        format_name: str = "",
    ) -> List[DoclingChunk]:
        """
        ★ SOTA Dual-Chunker: KG-optimierte Chunks (max_tokens=1200).
        
        Nutzt denselben Docling HybridChunker wie Retrieval-Chunks, aber mit
        ~3× höherem Token-Limit. Docling erledigt alles nativ:
        - Token-Zählung via E5-large Tokenizer (exakt, nicht Char-Approximation)
        - merge_peers: Semantisch zusammengehörige Abschnitte werden gruppiert
        - Heading-Hierarchie wird automatisch erhalten
        - always_emit_headings=True: Jeder Chunk bekommt seinen Heading-Kontext
        
        GraphRAG nutzt ~1200 Tokens pro Chunk. SynthKG nutzt Absatz-Level
        (~2000-5000 Chars). 1200 Tokens ≈ 4800 Chars liegt genau im SOTA-Bereich.
        
        Args:
            conv_result: Docling ConversionResult (gleicher wie für Retrieval)
            metadata: Zusätzliche Chunk-Metadaten
            file_path: Quell-Dateipfad
            format_name: Erkanntes Format
            
        Returns:
            Liste von DoclingChunks mit max_tokens=1200 (KG-optimiert)
        """
        from docling_core.transforms.chunker.hybrid_chunker import HybridChunker

        # ═══════════════════════════════════════════════════════
        # KG-Extraktion: 1200 Tokens ≈ 4800 Chars
        # GraphRAG: ~1200 Tokens. Magistral Q4_K_M: max_tokens=2048 Output
        # bei ~1200 Input-Tokens → freier Headroom für JSON-Response.
        # ═══════════════════════════════════════════════════════
        KG_MAX_TOKENS = 1200

        tokenizer = self._load_e5_tokenizer(KG_MAX_TOKENS, local_only=self._local_only)

        chunker = HybridChunker(
            tokenizer=tokenizer,
            merge_peers=True,
            always_emit_headings=True,  # KG braucht Heading-Kontext in jedem Chunk
        )
        
        chunks: List[DoclingChunk] = []
        doc = conv_result.document
        
        for i, chunk_iter in enumerate(chunker.chunk(doc)):
            chunk_text = chunk_iter.text
            
            if not chunk_text or not chunk_text.strip():
                continue
            
            chunks.append(DoclingChunk(
                text=chunk_text,
                metadata={
                    "source": file_path,
                    "format": format_name,
                    "chunk_method": "docling_hybrid_kg",
                    "chunk_idx": i,
                    "max_tokens": KG_MAX_TOKENS,
                    **(metadata or {}),
                },
                headings=self._extract_headings(chunk_iter),
                index=len(chunks),
            ))
        
        if chunks:
            avg_chars = sum(len(c.text) for c in chunks) // len(chunks)
            logger.info(
                f"🔗 KG-Chunker: {len(chunks)} KG-Chunks, "
                f"max_tokens={KG_MAX_TOKENS}, avg {avg_chars} chars"
            )
        
        return chunks
    
    def _fallback_chunk(
        self,
        text: str,
        *,
        chunk_size: int = 1500,
        chunk_overlap: int = 200,
        metadata: Optional[Dict[str, Any]] = None,
        file_path: str = "",
        format_name: str = "",
    ) -> List[DoclingChunk]:
        """
        Fallback-Chunking wenn HierarchicalChunker fehlschlägt.
        
        Splittet den Markdown-Text an semantischen Grenzen:
        1. Markdown-Überschriften (##)
        2. Doppelte Newlines (Absätze)
        3. Einfache Newlines
        4. Zeichen-Limit
        """
        chunks: List[DoclingChunk] = []
        
        if not text or not text.strip():
            return chunks
        
        # Split an Markdown-Überschriften
        import re
        sections = re.split(r'\n(?=#{1,6}\s)', text)
        
        current_chunk = ""
        current_headings: List[str] = []
        
        for section in sections:
            # Heading extrahieren
            heading_match = re.match(r'^(#{1,6})\s+(.*?)$', section, re.MULTILINE)
            if heading_match:
                current_headings = [heading_match.group(2).strip()]
            
            if len(current_chunk) + len(section) <= chunk_size:
                current_chunk += ("\n\n" if current_chunk else "") + section
            else:
                # Aktuellen Chunk speichern
                if current_chunk.strip():
                    chunks.append(DoclingChunk(
                        text=current_chunk.strip(),
                        metadata={
                            "source": file_path,
                            "format": format_name,
                            "chunk_method": "docling_fallback",
                            "chunk_idx": len(chunks),
                            **(metadata or {}),
                        },
                        headings=current_headings.copy(),
                        index=len(chunks),
                    ))
                
                # Neuer Chunk mit Overlap
                if chunk_overlap > 0 and current_chunk:
                    overlap_text = current_chunk[-chunk_overlap:]
                    current_chunk = overlap_text + "\n\n" + section
                else:
                    current_chunk = section
        
        # Letzten Chunk speichern
        if current_chunk.strip():
            chunks.append(DoclingChunk(
                text=current_chunk.strip(),
                metadata={
                    "source": file_path,
                    "format": format_name,
                    "chunk_method": "docling_fallback",
                    "chunk_idx": len(chunks),
                    **(metadata or {}),
                },
                headings=current_headings.copy(),
                index=len(chunks),
            ))
        
        # Zu große Chunks nachträglich splitten
        final_chunks: List[DoclingChunk] = []
        for chunk in chunks:
            if len(chunk.text) > chunk_size * 2:
                sub_parts = self._split_oversized_chunk(
                    chunk.text, chunk_size, chunk_overlap
                )
                for j, sub_text in enumerate(sub_parts):
                    final_chunks.append(DoclingChunk(
                        text=sub_text,
                        metadata={
                            **chunk.metadata,
                            "chunk_method": "docling_fallback_sub",
                            "sub_chunk_idx": j,
                        },
                        headings=chunk.headings,
                        index=len(final_chunks),
                    ))
            else:
                chunk.index = len(final_chunks)
                final_chunks.append(chunk)
        
        logger.debug(f"Fallback chunker: {len(final_chunks)} chunks")
        return final_chunks
    
    # ═══════════════════════════════════════════════════════════════════
    # Internal Helpers
    # ═══════════════════════════════════════════════════════════════════
    
    def _detect_url_format(
        self, url: str, timeout: int = 10
    ) -> Tuple[str, str]:
        """
        Erkenne das Dokumentformat einer URL.
        
        Returns:
            Tuple[format_name, file_extension]
            z.B. ("PDF", ".pdf") oder ("DOCX", ".docx")
        """
        from urllib.parse import urlparse, unquote
        
        parsed = urlparse(unquote(url))
        path = parsed.path.lower()
        
        # 1. Extension-Check
        for ext, fmt in EXT_TO_FORMAT.items():
            if path.endswith(ext):
                return fmt, ext
        
        # 2. HEAD Request für Content-Type
        try:
            import requests
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            }
            head = requests.head(
                url, headers=headers, timeout=min(timeout, 5), allow_redirects=True
            )
            content_type = head.headers.get('Content-Type', '').lower().split(';')[0].strip()
            
            if content_type in MIME_TO_FORMAT:
                fmt = MIME_TO_FORMAT[content_type]
                # Reverse-lookup extension
                ext = next(
                    (e for e, f in EXT_TO_FORMAT.items() if f == fmt),
                    f".{fmt.lower()}"
                )
                return fmt, ext
                
        except Exception as e:
            logger.debug(f"HEAD request failed for format detection: {e}")
        
        return "unknown", ""
    
    @staticmethod
    def _load_e5_tokenizer(max_tokens: int, local_only: bool = False) -> Any:
        """
        Lädt den E5-large Tokenizer für Docling's HybridChunker.

        Sucht zuerst lokale HF-Snapshots in allen relevanten Cache-Roots
        (inkl. HF_HOME/HUGGINGFACE_HUB_CACHE). In local-only mode wird
        bei fehlenden Artefakten deterministisch mit RuntimeError abgebrochen.

        Wichtig: Setzt `model_max_length` des unterliegenden HF-Tokenizers
        auf einen Sentinel-Großwert (`int(1e30)` — HF-Idiom
        `VERY_LARGE_INTEGER`), um die HF-Warnung
        `"Token indices sequence length is longer than the specified
        maximum sequence length"` strukturell zu vermeiden.
        Begründung: Docling's `HybridChunker` steuert das Chunk-Splitting
        ausschließlich über den `max_tokens`-Parameter des
        `HuggingFaceTokenizer`-Wrappers. Der HF-eigene
        `model_max_length` (E5: 512) wird von Docling für die Chunk-Größe
        nicht konsultiert — nur die HF-Lib selbst triggert damit ihre
        Warnung. Entkopplung ist hier semantisch korrekt: Docling-Limit
        bleibt verbindlich, HF-Warnung verschwindet ohne Pattern-Filter.
        """
        import glob as _glob

        model_id = "intfloat/multilingual-e5-large"
        model_cache_key = "models--intfloat--multilingual-e5-large"
        project_root = Path(__file__).resolve().parent.parent
        home = Path.home()

        cache_candidates: List[Path] = []

        def _add_cache_dir(candidate: Optional[str]) -> None:
            if not candidate:
                return
            p = Path(candidate).expanduser()
            if p.exists() and p.is_dir():
                cache_candidates.append(p)

        _add_cache_dir(os.getenv("HUGGINGFACE_HUB_CACHE", ""))
        _add_cache_dir(os.getenv("TRANSFORMERS_CACHE", ""))
        _add_cache_dir(os.getenv("SENTENCE_TRANSFORMERS_HOME", ""))

        hf_home = os.getenv("HF_HOME", "").strip()
        if hf_home:
            hf_home_path = Path(hf_home).expanduser()
            _add_cache_dir(str(hf_home_path))
            _add_cache_dir(str(hf_home_path / "hub"))

        _add_cache_dir(str(project_root / "models_cache" / "huggingface" / "hub"))
        _add_cache_dir(str(project_root / "models_cache" / "sentence_transformers"))
        _add_cache_dir(str(home / ".cache" / "huggingface" / "hub"))
        _add_cache_dir(str(home / ".cache" / "sentence_transformers"))

        # Dedupe while preserving search order.
        seen: set[str] = set()
        ordered_cache_dirs: List[Path] = []
        for cache_dir in cache_candidates:
            resolved = str(cache_dir.resolve())
            if resolved in seen:
                continue
            seen.add(resolved)
            ordered_cache_dirs.append(cache_dir)

        e5_snapshots: List[str] = []
        for cache_dir in ordered_cache_dirs:
            e5_snapshots = _glob.glob(
                str(cache_dir / model_cache_key / "snapshots" / "*")
            )
            if e5_snapshots:
                break

        if not e5_snapshots and local_only:
            searched_dirs = ", ".join(str(p) for p in ordered_cache_dirs) or "<none>"
            raise RuntimeError(
                "Local-only tokenizer artifact missing for intfloat/multilingual-e5-large. "
                "Prefetch the model snapshot in a local HF cache first. "
                f"Searched cache roots: {searched_dirs}"
            )

        from docling_core.transforms.chunker.tokenizer.huggingface import HuggingFaceTokenizer

        e5_path = e5_snapshots[0] if e5_snapshots else model_id

        tokenizer = HuggingFaceTokenizer.from_pretrained(
            model_name=e5_path,
            max_tokens=max_tokens,
        )

        # HF-Warnung strukturell deaktivieren (siehe Docstring).
        # Der Attribut-Pfad ist Docling-API-stabil: HuggingFaceTokenizer
        # exponiert das HF-Tokenizer-Objekt unter `.tokenizer`.
        try:
            tokenizer.tokenizer.model_max_length = int(1e30)
        except AttributeError as e:
            logger.warning(
                f"E5-Tokenizer: model_max_length konnte nicht gesetzt werden "
                f"({e}); HF-Längen-Warnung bleibt sichtbar, Chunking ist "
                f"davon nicht betroffen."
            )

        return tokenizer

    @staticmethod
    def _extract_headings(chunk_iter: Any) -> List[str]:
        """Extrahiere Heading-Pfad aus einem Docling Chunk"""
        try:
            if hasattr(chunk_iter, 'meta') and hasattr(chunk_iter.meta, 'headings'):
                return list(chunk_iter.meta.headings) if chunk_iter.meta.headings else []
            if hasattr(chunk_iter, 'headings'):
                return list(chunk_iter.headings) if chunk_iter.headings else []
        except Exception:
            pass
        return []
    
    @staticmethod
    def _split_oversized_chunk(
        text: str, chunk_size: int, overlap: int
    ) -> List[str]:
        """Splitte einen zu großen Text-Chunk in kleinere Teile"""
        parts: List[str] = []
        start = 0
        
        while start < len(text):
            end = start + chunk_size
            
            if end < len(text):
                # Suche einen guten Split-Punkt (Absatz, Satz, Wort)
                for delimiter in ["\n\n", "\n", ". ", " "]:
                    split_pos = text.rfind(delimiter, start + chunk_size // 2, end)
                    if split_pos > start:
                        end = split_pos + len(delimiter)
                        break
            
            chunk_text = text[start:end].strip()
            if chunk_text:
                parts.append(chunk_text)
            
            start = end - overlap if overlap > 0 else end
            
            # Safety: Verhindere Endlosschleife
            if start >= len(text) or (len(parts) > 0 and start <= 0):
                break
        
        return parts if parts else [text]
    
    # ═══════════════════════════════════════════════════════════════════
    # Download Validation (SOTA Fix: 0.2KB Bug)
    # ═══════════════════════════════════════════════════════════════════
    
    def _validate_url_download(
        self, url: str, format_name: str, timeout: int = 10
    ) -> Dict[str, Any]:
        """
        Prädiktive Validierung vor dem Download (HEAD-Request).
        
        Behebt Root Cause: Server könnte HTML statt PDF zurückgeben.
        Diese Funktion erkennt das VOR dem Download.
        
        Returns:
            {
                "valid": bool,
                "reason": str,
                "content_length_mb": float,
                "expected_size_bytes": int,
                "content_type": str,
                "server_type": str,
            }
        """
        try:
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                'Referer': url.rsplit('/', 1)[0] + '/',  # Referrer für Forschungs-Server
            }
            
            # HEAD-Request für Metadaten (schneller als GET)
            response = requests.head(url, headers=headers, timeout=min(timeout, 5), allow_redirects=True)
            response.raise_for_status()
            
            content_length = int(response.headers.get('Content-Length', 0))
            content_type = response.headers.get('Content-Type', '').lower()
            server_header = response.headers.get('Server', 'unknown')

            content_length_mb = content_length / 1_000_000

            # 1. Content-Type validation first: explicit HTML mismatch is a
            # stronger signal than size heuristics and should be surfaced.
            if content_type and "text/html" in content_type:
                return {
                    "valid": False,
                    "reason": f"Server returned HTML (Content-Type: {content_type}), not document",
                    "content_length_mb": content_length_mb,
                    "content_type": content_type,
                    "server_type": server_header,
                }
            
            # 2. Größen-Validierung
            if content_length == 0:
                return {
                    "valid": False,
                    "reason": "Server did not provide Content-Length header",
                    "content_length_mb": 0.0,
                    "content_type": content_type,
                    "server_type": server_header,
                }
            
            # Zu kleine Dateien = wahrscheinlich Fehlerseiten (200 bytes ≈ HTML error)
            MIN_FILE_SIZES = {
                "PDF": 10 * 1024,      # Min 10 KB für PDFs
                "DOCX": 5 * 1024,      # Min 5 KB
                "XLSX": 5 * 1024,      # Min 5 KB
                "PPTX": 5 * 1024,      # Min 5 KB
                "HTML": 1 * 1024,      # Min 1 KB
                "IMAGE": 1 * 1024,     # Min 1 KB
            }
            
            min_size = MIN_FILE_SIZES.get(format_name, 1 * 1024)
            if content_length < min_size:
                return {
                    "valid": False,
                    "reason": f"File too small ({content_length} bytes < {min_size} bytes), likely error page",
                    "content_length_mb": content_length_mb,
                    "content_type": content_type,
                    "server_type": server_header,
                }
            
            # 3. Größen-Limit (200 MB)
            if content_length > 200 * 1024 * 1024:
                return {
                    "valid": False,
                    "reason": f"File too large ({content_length_mb:.0f} MB > 200 MB)",
                    "content_length_mb": content_length_mb,
                    "content_type": content_type,
                    "server_type": server_header,
                }
            
            # ✅ All checks passed
            return {
                "valid": True,
                "content_length_mb": content_length_mb,
                "expected_size_bytes": content_length,
                "content_type": content_type,
                "server_type": server_header,
            }
            
        except requests.exceptions.RequestException as e:
            return {
                "valid": False,
                "reason": f"HEAD request failed: {e}",
            }
        except Exception as e:
            logger.warning(f"Predictive validation failed: {e}")
            return {
                "valid": False,
                "content_length_mb": 0.0,
                "reason": f"Predictive validation error: {e}",
            }
    
    def _calculate_adaptive_timeout(self, predicted_size_mb: float, base_timeout: int = 30) -> int:
        """
        Berechne adaptiven Timeout basierend auf erwarteter Dateigröße.
        
        Heuristik:
        - <10 MB: 30s
        - 10-50 MB: 60s
        - 50-100 MB: 120s
        - >100 MB: 180s
        
        Grund: Manche Forschungs-Server haben langsame Netzwerke
        """
        if predicted_size_mb < 10:
            return base_timeout
        elif predicted_size_mb < 50:
            return 60
        elif predicted_size_mb < 100:
            return 120
        else:
            return 180
    
    def _download_with_retry(
        self, url: str, format_name: str, file_ext: str, timeout: int
    ) -> Optional[str]:
        """
        Lade Datei mit Retry-Logik (exponentieller Backoff).
        
        Behebt transiente Fehler (z.B. 503 Service Unavailable).
        Gibt None zurück wenn nach Retries noch Fehler auftreten.
        
        Returns:
            Pfad zur Temp-Datei oder None bei Fehler
        """
        import requests
        
        MAX_RETRIES = 3
        BASE_BACKOFF_SECONDS = 1.0
        
        for attempt in range(MAX_RETRIES):
            try:
                headers = {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                    'Referer': url.rsplit('/', 1)[0] + '/',
                    'Accept-Encoding': 'gzip, deflate',  # Standard HTTP compression
                }
                
                response = requests.get(
                    url, 
                    headers=headers, 
                    timeout=timeout, 
                    stream=True,
                    allow_redirects=True
                )
                response.raise_for_status()
                
                # In Temp-Datei schreiben (mit Progress-Logging)
                suffix = file_ext if file_ext else f".{format_name.lower()}"
                with tempfile.NamedTemporaryFile(
                    delete=False, suffix=suffix, prefix="docling_"
                ) as tmp:
                    bytes_downloaded = 0
                    for chunk_data in response.iter_content(chunk_size=65536):
                        if chunk_data:
                            tmp.write(chunk_data)
                            bytes_downloaded += len(chunk_data)
                    
                    tmp_path = tmp.name
                
                response.close()
                del response
                
                logger.info(f"✅ Download successful on attempt {attempt + 1}/{MAX_RETRIES}")
                return tmp_path
                
            except (requests.exceptions.RequestException, OSError) as e:
                error_name = type(e).__name__
                is_transient = isinstance(e, (
                    requests.exceptions.ConnectionError,
                    requests.exceptions.Timeout,
                    requests.exceptions.ChunkedEncodingError,
                ))
                
                if attempt < MAX_RETRIES - 1 and is_transient:
                    backoff_seconds = BASE_BACKOFF_SECONDS * (2 ** attempt)
                    logger.warning(
                        f"⚠️ Download attempt {attempt + 1}/{MAX_RETRIES} failed ({error_name}), "
                        f"retrying in {backoff_seconds:.1f}s..."
                    )
                    time.sleep(backoff_seconds)
                else:
                    logger.error(
                        f"❌ Download failed (attempt {attempt + 1}/{MAX_RETRIES}): {error_name}: {e}"
                    )
                    if attempt == MAX_RETRIES - 1:
                        return None
        
        return None
    
    def _validate_downloaded_file(
        self, file_path: str, expected_format: str, expected_size_bytes: int = 0
    ) -> Dict[str, Any]:
        """
        Post-Download Validierung (Magic Bytes + Größe).
        
        Behebt Root Cause des 0.2KB-Bugs:
        - Prüfe dass die Datei nicht beschädigt/unvollständig ist
        - Validiere Magic Bytes (erste Bytes der Datei)
        - Vergleiche gegen Server-Größe
        
        Returns:
            {
                "valid": bool,
                "reason": str,
                "actual_size_bytes": int,
                "magic_bytes": str,
            }
        """
        # Magic Bytes für verschiedene Dateiformate
        MAGIC_BYTES: Dict[str, Tuple[bytes, ...]] = {
            "PDF": (b"%PDF-",),
            "DOCX": (b"PK\x03\x04",),  # ZIP-Format
            "XLSX": (b"PK\x03\x04",),
            "PPTX": (b"PK\x03\x04",),
            "HTML": (b"<", b"\xef\xbb\xbf<"),  # UTF-8 mit/ohne BOM
            "PNG": (b"\x89PNG",),
            "JPG": (b"\xff\xd8\xff",),
            "JPEG": (b"\xff\xd8\xff",),
            "TIFF": (b"II*\x00", b"MM\x00*"),  # Intel/Motorola byte order
        }
        
        try:
            if not os.path.exists(file_path):
                return {
                    "valid": False,
                    "reason": "Downloaded file does not exist",
                }
            
            actual_size = os.path.getsize(file_path)
            
            # 1. Größen-Validierung
            if actual_size == 0:
                return {
                    "valid": False,
                    "reason": "Downloaded file is empty (0 bytes)",
                    "actual_size_bytes": actual_size,
                }
            
            if expected_size_bytes > 0 and actual_size < expected_size_bytes * 0.8:
                # Größe < 80% von Expected → möglicherweise unvollständig
                logger.warning(
                    f"⚠️ File size mismatch: "
                    f"expected ~{expected_size_bytes} bytes, got {actual_size} bytes "
                    f"({actual_size/expected_size_bytes*100:.1f}%)"
                )
                # Nicht kritisch, aber warnen (manche Server geben falsche Content-Length)
            
            # 2. Magic Byte Validierung
            magic_bytes_expected = MAGIC_BYTES.get(expected_format)
            file_header = b""
            if magic_bytes_expected:
                with open(file_path, 'rb') as f:
                    file_header = f.read(16)
                
                magic_hex = file_header[:8].hex() if file_header else ""
                has_valid_magic = any(
                    file_header.startswith(magic) for magic in magic_bytes_expected
                )
                
                if not has_valid_magic:
                    return {
                        "valid": False,
                        "reason": f"Invalid magic bytes for {expected_format}. "
                                  f"Expected {magic_bytes_expected}, "
                                  f"got {magic_hex}",
                        "actual_size_bytes": actual_size,
                        "magic_bytes": magic_hex,
                    }
            
            # ✅ All checks passed
            return {
                "valid": True,
                "actual_size_bytes": actual_size,
                "magic_bytes": file_header[:8].hex() if file_header else "unknown",
            }
            
        except Exception as e:
            return {
                "valid": False,
                "reason": f"Post-download validation error: {e}",
            }
    
    # ═══════════════════════════════════════════════════════════════════
    # Stats & Diagnostics
    # ═══════════════════════════════════════════════════════════════════
    
    def get_stats(self) -> Dict[str, Any]:
        """Diagnostik-Informationen"""
        return {
            "initialized": self._initialized,
            "model_load_time_s": self._model_load_time,
            "conversions_count": self._conversions_count,
            "total_processing_time_s": self._total_processing_time,
            "avg_processing_time_s": (
                self._total_processing_time / self._conversions_count
                if self._conversions_count > 0 else 0
            ),
            "device": getattr(self, '_device', 'cpu'),
            "table_mode": "accurate",
            "ocr": "easyocr",
            "local_only": self._local_only,
        }
    
    def cleanup(self) -> None:
        """Ressourcen freigeben (idempotent, inkl. CUDA-Cache-Freigabe).

        SOTA (2026-08-28): Entlädt die Docling-Pipeline (inkl. interner
        OCR/Modelle) aus GPU/RAM und gibt den CUDA-Cache an das OS zurück,
        damit das VRAM der 3060 Ti wirklich frei wird.
        Lazy: Das Modell wird beim nächsten convert_file()-Aufruf transparent
        neu geladen. Heiße Query-Path-Modelle (Reranker/NLI/Embeddings) sind
        nicht betroffen.
        """
        had_converter = self._converter is not None
        if self._converter is not None:
            try:
                del self._converter
            except Exception:
                pass
            self._converter = None
        self._initialized = False
        gc.collect()
        # SOTA: CUDA-Cache freigeben, damit VRAM wirklich an das OS zurückgeht
        try:
            import torch
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:
            pass
        if had_converter:
            logger.info("DoclingProcessor: Resources cleaned up (VRAM freed)")
        else:
            logger.debug("DoclingProcessor.cleanup(): nichts zu entladen (nicht initialisiert)")
