"""
Multimodal PDF Processor - UnifiedRagStore Extension
=====================================================

This module extends UnifiedRagStore with multimodal (Vision OCR) capabilities
without modifying the oversized unified_rag_store.py file directly.

🆕 STATE-OF-THE-ART Features:
- PDF-Typ-Erkennung (text_native, scanned, hybrid)
- Vision-LLM (aktuell geladenes multimodales LLM) für Infografiken/Charts/Tabellen
- pymupdf4llm für optimale Text-Extraktion
- Automatische Auswahl der besten Extraktor-Strategie

Usage:
------
from agent.multimodal_pdf_processor import MultimodalPDFProcessor

# Initialize
processor = MultimodalPDFProcessor(rag_store)

# Process PDF with Vision OCR
result = processor.process_pdf_multimodal(
    pdf_path="document.pdf",
    extract_images=True,
    perform_ocr=True,
    use_vision_ocr=True
)
"""

import logging
import os
import asyncio
from typing import Dict, Any, Optional, List
from pathlib import Path

logger = logging.getLogger(__name__)


class MultimodalPDFProcessor:
    """
    Extension for UnifiedRagStore to process PDFs with Vision OCR.
    
    🆕 State-of-the-Art Features:
    - Automatische PDF-Typ-Erkennung
    - Vision-LLM für Infografiken (primär)
    - Fallback zu Standard-OCR
    
    This class wraps the existing upsert_pdf() method and adds multimodal
    processing capabilities (image extraction + OCR) without modifying
    the core UnifiedRagStore code.
    """
    
    def __init__(self, rag_store, model_loader=None):
        """
        Initialize the multimodal PDF processor.
        
        Args:
            rag_store: UnifiedRagStore instance
            model_loader: Optional ModelLoader instance for Vision OCR
        """
        self.rag_store = rag_store
        self.model_loader = model_loader
        self._multimodal_pipeline = None
        self._vision_extractor = None
        
    def _get_vision_extractor(self) -> Optional[Any]:
        """Lazy initialization of PDFVisionExtractor (State-of-the-Art)"""
        if self._vision_extractor is None:
            try:
                from agent.pdf_vision_extractor import PDFVisionExtractor
                self._vision_extractor = PDFVisionExtractor(
                    model_loader=self.model_loader,
                    text_threshold=100,
                    dpi_for_vision=150,
                    enable_vision_for_text_pages=False,  # Nur für scanned/hybrid
                    max_vision_pages=30
                )
                logger.info("✅ PDFVisionExtractor initialized (State-of-the-Art)")
            except ImportError as e:
                logger.warning(f"PDFVisionExtractor not available: {e}")
                self._vision_extractor = None
        return self._vision_extractor
        
    def _get_multimodal_pipeline(self, use_vision_ocr: bool = True):
        """
        Lazy initialization of multimodal pipeline (Fallback).
        
        Args:
            use_vision_ocr: Whether to use Vision OCR model
            
        Returns:
            MultimodalRAGPipeline instance
        """
        if self._multimodal_pipeline is None:
            try:
                from agent.multimodal_integration import MultimodalRAGPipeline
                
                # Initialize ModelLoader if not provided
                if self.model_loader is None and use_vision_ocr:
                    try:
                        from scripts.model_loader import ModelLoader
                        self.model_loader = ModelLoader()
                        logger.info("ModelLoader initialized for Vision OCR")
                    except Exception as e:
                        logger.warning(f"Could not initialize ModelLoader: {e}")
                        use_vision_ocr = False
                
                # Create pipeline
                self._multimodal_pipeline = MultimodalRAGPipeline(
                    rag_store=self.rag_store,
                    output_dir="./data/extracted_images",
                    enable_ocr=use_vision_ocr,
                    model_loader=self.model_loader
                )
                
                logger.info(f"MultimodalRAGPipeline initialized (vision_ocr={use_vision_ocr})")
                
            except ImportError as e:
                logger.error(f"Failed to import MultimodalRAGPipeline: {e}")
                raise
            except Exception as e:
                logger.error(f"Failed to initialize MultimodalRAGPipeline: {e}")
                raise
        
        return self._multimodal_pipeline
    
    def process_pdf_multimodal(
        self,
        file_path: str,
        doc_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        chunk_size: int = 1500,
        chunk_overlap: int = 200,
        extract_tables: bool = True,
        build_kg: bool = True,
        extract_images: bool = True,
        perform_ocr: bool = True,
        use_vision_ocr: bool = True,
        table_doc_suffix: str = "#tables",
        force_vision_all_pages: bool = False
    ) -> Dict[str, Any]:
        """
        🆕 STATE-OF-THE-ART: PDF mit Vision-LLM verarbeiten.
        
        STRATEGIE:
        1. PDF-Typ erkennen (text_native, scanned, hybrid)
        2. Für scanned/hybrid: Vision-LLM für Infografiken (primär!)
        3. Text via pymupdf4llm extrahieren
        4. Fallback: Standard OCR für verbleibende Bilder
        
        Args:
            file_path: Path to PDF file
            doc_id: Optional document ID
            metadata: Additional metadata
            chunk_size: Chunk size for text splitting
            chunk_overlap: Chunk overlap
            extract_tables: Whether to extract tables
            build_kg: Whether to build knowledge graph
            extract_images: Whether to extract images from PDF
            perform_ocr: Whether to perform OCR on images
            use_vision_ocr: Whether to use the Vision-LLM (loaded multimodal LLM) for OCR
            table_doc_suffix: Suffix for table documents
            force_vision_all_pages: Vision für ALLE Seiten (auch text_native)
            
        Returns:
            Dict with processing statistics
        """
        
        # Validate file exists
        if not os.path.exists(file_path):
            return {
                "success": False,
                "error": f"File not found: {file_path}",
                "chunks_added": 0
            }
        
        # Initialize result dict
        result: Dict[str, Any] = {
            "success": False,
            "chunks_added": 0,
            "images_extracted": 0,
            "ocr_attempted": 0,
            "ocr_successful": 0,
            "model_used": None,
            "pdf_type": "unknown",
            "vision_pages_analyzed": 0,
            "infographics_found": 0,
            "tables_found": 0,
            "errors": []
        }
        
        try:
            # ============================================================
            # 🆕 STEP 1: State-of-the-Art Vision-Extraktion (PRIMÄR!)
            # ============================================================
            if use_vision_ocr:
                vision_result = self._process_with_vision_extractor(
                    file_path, 
                    force_vision=force_vision_all_pages,
                    metadata=metadata
                )
                
                if vision_result.get("success"):
                    result.update(vision_result)
                    result["extraction_method"] = "vision_enhanced"
                    
                    # Wenn Vision erfolgreich, direkt zu RAG hinzufügen
                    if vision_result.get("vision_text"):
                        rag_result = self._add_vision_text_to_rag(
                            vision_result["vision_text"],
                            file_path,
                            doc_id,
                            metadata,
                            chunk_size,
                            chunk_overlap,
                            build_kg
                        )
                        result["chunks_added"] = rag_result.get("chunks_added", 0)
                        result["kg_triples"] = rag_result.get("kg_triples", 0)
                        result["success"] = True
                        
                        logger.info(f"✅ Vision-Enhanced PDF: {result['chunks_added']} Chunks, "
                                   f"{result['vision_pages_analyzed']} Vision-Seiten")
                        return result
            
            # ============================================================
            # STEP 2: Standard PDF processing (Fallback)
            # ============================================================
            logger.info(f"Processing PDF (standard): {file_path}")
            
            standard_result = self.rag_store.upsert_pdf(
                file_path,
                doc_id=doc_id,
                metadata=metadata,
                chunk_size=chunk_size,
                chunk_overlap=chunk_overlap,
                extract_tables=extract_tables,
                build_kg=build_kg,
                table_doc_suffix=table_doc_suffix
            )
            
            # Copy standard result
            result.update(standard_result)
            result["extraction_method"] = "standard"
            
            if not standard_result.get("success"):
                logger.warning(f"Standard PDF processing failed: {standard_result.get('error')}")
                return result
            
            logger.info(f"Standard PDF processing successful: {standard_result.get('chunks_added', 0)} chunks")
            
            # ============================================================
            # STEP 3: Additional multimodal processing (images + OCR)
            # ============================================================
            if extract_images and perform_ocr:
                multimodal_stats = self._process_remaining_images(
                    file_path, metadata, use_vision_ocr, result
                )
                result.update(multimodal_stats)
            
            # Mark as success if we got this far
            result['success'] = True
            
        except Exception as e:
            logger.error(f"PDF processing failed: {e}", exc_info=True)
            result['success'] = False
            result['error'] = str(e)
            result['errors'].append(str(e))
        
        return result
    
    def _process_with_vision_extractor(
        self, 
        file_path: str, 
        force_vision: bool = False,
        metadata: Optional[Dict] = None
    ) -> Dict[str, Any]:
        """
        🆕 Primäre Methode: State-of-the-Art Vision-Extraktion
        """
        result = {
            "success": False,
            "pdf_type": "unknown",
            "vision_text": "",
            "vision_pages_analyzed": 0,
            "infographics_found": 0,
            "tables_found": 0
        }
        
        try:
            extractor = self._get_vision_extractor()
            if extractor is None:
                logger.warning("PDFVisionExtractor not available, skipping Vision processing")
                return result
            
            # Vision-Extraktion durchführen
            vision_result = extractor.extract_complete(file_path, force_vision=force_vision)
            
            result["pdf_type"] = vision_result.pdf_type
            result["vision_pages_analyzed"] = vision_result.vision_analyzed_pages
            result["infographics_found"] = len(vision_result.infographic_descriptions)
            result["tables_found"] = len(vision_result.table_descriptions)
            
            # Wähle besten Text
            if vision_result.vision_enhanced_text and vision_result.vision_analyzed_pages > 0:
                result["vision_text"] = vision_result.vision_enhanced_text
                result["model_used"] = "vision-llm"
            else:
                result["vision_text"] = vision_result.full_text
                result["model_used"] = "pymupdf4llm"
            
            result["success"] = len(list(vision_result.errors)) == 0 and len(str(result["vision_text"])) > 100
            
            logger.info(f"📊 Vision-Extraktion: Typ={result['pdf_type']}, "
                       f"Vision-Seiten={result['vision_pages_analyzed']}, "
                       f"Infografiken={result['infographics_found']}")
            
        except Exception as e:
            logger.error(f"Vision extraction failed: {e}", exc_info=True)
            result["errors"] = [str(e)]
        
        return result
    
    def _add_vision_text_to_rag(
        self,
        text: str,
        file_path: str,
        doc_id: Optional[str],
        metadata: Optional[Dict],
        chunk_size: int,
        chunk_overlap: int,
        build_kg: bool
    ) -> Dict[str, Any]:
        """Fügt Vision-extrahierten Text zum RAG Store hinzu"""
        
        base_doc_id = doc_id or os.path.abspath(file_path)
        base_meta = {
            **(metadata or {}),
            "source": os.path.basename(file_path),
            "extraction_method": "vision_enhanced",
            "source_type": "pdf_vision"
        }
        
        # Text in Seiten aufteilen (falls Vision-Marker vorhanden)
        if "\n---\n" in text:
            page_texts = text.split("\n---\n")
        else:
            page_texts = [text]
        
        docs = []
        for i, page_text in enumerate(page_texts, 1):
            if page_text.strip():
                docs.append({
                    "id": f"{base_doc_id}#p{i}",
                    "text": page_text.strip(),
                    "metadata": {**base_meta, "page": i}
                })
        
        if not docs:
            return {"chunks_added": 0, "kg_triples": 0}
        
        # Zu RAG hinzufügen
        upsert_result = self.rag_store.upsert_documents(
            docs,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap
        )
        
        return {
            "chunks_added": upsert_result.get("inserted", 0),
            "kg_triples": upsert_result.get("kg_triples", 0)
        }
    
    def _process_remaining_images(
        self,
        file_path: str,
        metadata: Optional[Dict],
        use_vision_ocr: bool,
        current_result: Dict
    ) -> Dict[str, Any]:
        """Verarbeitet verbleibende Bilder mit Standard-OCR (Fallback)"""
        stats = {
            "images_extracted": 0,
            "ocr_attempted": 0,
            "ocr_successful": 0
        }
        
        try:
            logger.info("Starting additional image OCR processing...")
            
            pipeline = self._get_multimodal_pipeline(use_vision_ocr=use_vision_ocr)
            
            multimodal_result = pipeline.process_pdf_multimodal(
                pdf_path=file_path,
                extract_images=True,
                run_ocr=True,
                add_to_rag=False
            )
            
            images_count = multimodal_result.get('images_extracted', 0)
            ocr_results = multimodal_result.get('ocr_results', [])
            images = multimodal_result.get('images', [])
            
            stats["images_extracted"] = images_count
            stats["ocr_attempted"] = len(ocr_results)
            
            # Process OCR results
            for idx, ocr_result in enumerate(ocr_results):
                ocr_text = ocr_result.text.strip() if hasattr(ocr_result, 'text') else str(ocr_result).strip()
                
                if ocr_text and len(ocr_text) > 20:  # Minimum text length
                    stats["ocr_successful"] += 1
                    
                    # Get page info
                    image_info = images[idx] if idx < len(images) else None
                    page_num = image_info.page_number if image_info and hasattr(image_info, 'page_number') else 0
                    
                    # Create OCR chunk
                    ocr_chunk = f"[Bild von Seite {page_num}]: {ocr_text}"
                    
                    # Add to RAG
                    try:
                        pdf_doc_id = current_result.get('doc_id') or Path(file_path).stem
                        ocr_doc = {
                            "text": ocr_chunk,
                            "id": f"{pdf_doc_id}_ocr_img{idx}",
                            "metadata": {
                                **(metadata or {}),
                                'source_type': 'pdf_image_ocr',
                                'source_pdf': file_path,
                                'page_number': page_num
                            }
                        }
                        
                        ocr_result_db = self.rag_store.upsert_documents([ocr_doc])
                        if ocr_result_db.get('success'):
                            current_result['chunks_added'] = current_result.get('chunks_added', 0) + ocr_result_db.get('inserted', 0)
                            
                    except Exception as e:
                        logger.warning(f"Failed to add OCR chunk: {e}")
            
            logger.info(f"Additional OCR: {stats['images_extracted']} images, "
                       f"{stats['ocr_successful']}/{stats['ocr_attempted']} successful")
            
        except Exception as e:
            logger.warning(f"Additional image processing failed: {e}")
        
        return stats


def create_multimodal_processor(rag_store, model_loader=None) -> MultimodalPDFProcessor:
    """
    Factory function to create a MultimodalPDFProcessor.
    
    Args:
        rag_store: UnifiedRagStore instance
        model_loader: Optional ModelLoader instance
        
    Returns:
        MultimodalPDFProcessor instance
    """
    return MultimodalPDFProcessor(rag_store, model_loader)
