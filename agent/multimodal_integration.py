"""
Multimodal RAG Integration - Verbindet Bildextraktion, OCR und RAG Store
=========================================================================

Dieses Modul orchestriert die multimodale Pipeline:
1. PDF/Web -> Image Extraction
2. Images -> OCR
3. Text + Images -> RAG Store
4. Query -> Multimodal Results

Verwendung:
    >>> from agent.multimodal_integration import MultimodalRAGPipeline
    >>> pipeline = MultimodalRAGPipeline(rag_store, output_dir="./data/images")
    >>> result = pipeline.process_pdf_multimodal("document.pdf", extract_images=True, run_ocr=True)
"""

import os
import logging
from typing import List, Dict, Any, Optional, Tuple
from pathlib import Path

logger = logging.getLogger(__name__)


class MultimodalRAGPipeline:
    """Orchestriert multimodale Dokumentenverarbeitung"""
    
    def __init__(
        self,
        rag_store,
        output_dir: str = "./data/extracted_images",
        enable_ocr: bool = True,
        ocr_languages: Optional[List[str]] = None,
        model_loader = None  # Optional: ModelLoader Singleton für Vision OCR
    ):
        """
        Args:
            rag_store: UnifiedRagStore oder ähnliches mit ImageManager
            output_dir: Verzeichnis für extrahierte Bilder
            enable_ocr: OCR aktivieren
            ocr_languages: OCR-Sprachen (default: ['de', 'en'])
            model_loader: Optional ModelLoader Singleton (für Vision OCR)
        """
        self.rag_store = rag_store
        self.output_dir = output_dir
        self.enable_ocr = enable_ocr
        self.model_loader = model_loader
        
        # Initialisiere Komponenten
        self._init_components(ocr_languages or ['de', 'en'])
        
        logger.info(f"MultimodalRAGPipeline initialized (OCR: {enable_ocr})")
    
    def _init_components(self, ocr_languages: List[str]):
        """Initialisiert Image Extractor und OCR Processor"""
        try:
            from agent.image_extractor import ImageExtractor
            self.image_extractor = ImageExtractor(output_dir=self.output_dir)
        except ImportError as e:
            logger.error(f"ImageExtractor not available: {e}")
            self.image_extractor = None
        
        if self.enable_ocr:
            # Try Vision OCR Processor first (Vision-LLM + EasyOCR fallback)
            try:
                from agent.vision_ocr_processor import VisionOCRProcessor
                self.ocr_processor = VisionOCRProcessor(
                    use_vision_model=True,
                    model_loader=self.model_loader,  # Übergebe ModelLoader Singleton
                    fallback_to_easyocr=True,
                    easyocr_languages=ocr_languages
                )
                logger.info("✅ Vision OCR Processor initialized (Vision-LLM + EasyOCR fallback)")
            except ImportError as e:
                logger.warning(f"VisionOCRProcessor not available: {e}, trying legacy OCRProcessor...")
                
                # Fallback to legacy OCRProcessor
                try:
                    from agent.ocr_processor import OCRProcessor
                    self.ocr_processor = OCRProcessor(languages=ocr_languages)
                    logger.warning("⚠️ Using legacy OCRProcessor (EasyOCR only)")
                except ImportError as e2:
                    logger.error(f"OCRProcessor not available: {e2}")
                    self.ocr_processor = None
        else:
            self.ocr_processor = None
    
    def process_pdf_multimodal(
        self,
        pdf_path: str,
        doc_id: Optional[str] = None,
        extract_images: bool = True,
        run_ocr: bool = True,
        skip_duplicates: bool = True,
        add_to_rag: bool = True
    ) -> Dict[str, Any]:
        """
        Verarbeitet PDF mit multimodalen Features
        
        Args:
            pdf_path: Pfad zur PDF-Datei
            doc_id: Dokument-ID für RAG Store (None = auto-generiert)
            extract_images: Bilder extrahieren
            run_ocr: OCR auf Bildern ausführen
            skip_duplicates: Duplikate überspringen
            add_to_rag: Ergebnisse zu RAG Store hinzufügen
            
        Returns:
            Dict mit Verarbeitungsergebnissen
        """
        if doc_id is None:
            doc_id = Path(pdf_path).stem
        
        result = {
            'doc_id': doc_id,
            'pdf_path': pdf_path,
            'images_extracted': 0,
            'ocr_performed': 0,
            'total_ocr_text': '',
            'images': [],
            'ocr_results': [],
            'errors': []
        }
        
        # 1. Extrahiere Bilder
        if extract_images and self.image_extractor:
            try:
                images = self.image_extractor.extract_from_pdf(
                    pdf_path=pdf_path,
                    skip_duplicates=skip_duplicates
                )
                result['images_extracted'] = len(images)
                result['images'] = images
                
                logger.info(f"Extracted {len(images)} images from {pdf_path}")
                
                # 2. Führe OCR aus
                if run_ocr and self.ocr_processor and images:
                    ocr_texts = []
                    
                    for img_data in images:
                        try:
                            ocr_result = self.ocr_processor.extract_text(img_data.image_path)
                            result['ocr_results'].append(ocr_result)
                            
                            if ocr_result.text.strip():
                                ocr_texts.append(ocr_result.text)
                                result['ocr_performed'] += 1
                                
                                logger.debug(
                                    f"OCR: {ocr_result.word_count} words "
                                    f"(conf: {ocr_result.confidence:.2%}) from {img_data.image_path}"
                                )
                        except Exception as e:
                            error_msg = f"OCR failed for {img_data.image_path}: {e}"
                            logger.error(error_msg)
                            result['errors'].append(error_msg)
                    
                    result['total_ocr_text'] = '\n\n'.join(ocr_texts)
                    logger.info(f"OCR performed on {result['ocr_performed']}/{len(images)} images")
                
                # 3. Speichere in RAG Store
                if add_to_rag and hasattr(self.rag_store, 'image_manager'):
                    for i, img_data in enumerate(images):
                        try:
                            # OCR-Ergebnisse hinzufügen wenn vorhanden
                            ocr_text = None
                            ocr_confidence = None
                            
                            if i < len(result['ocr_results']):
                                ocr_result = result['ocr_results'][i]
                                ocr_text = ocr_result.text
                                ocr_confidence = ocr_result.confidence
                            
                            # Speichere Metadaten
                            self.rag_store.image_manager.add_image_metadata(
                                doc_id=doc_id,
                                image_path=img_data.image_path,
                                page_number=img_data.page_number,
                                image_index=img_data.index,
                                image_format=img_data.format,
                                width=img_data.width,
                                height=img_data.height,
                                size_bytes=img_data.size_bytes,
                                image_hash=img_data.hash,
                                alt_text=img_data.alt_text,
                                ocr_text=ocr_text,
                                ocr_confidence=ocr_confidence,
                                source=img_data.source
                            )
                        except Exception as e:
                            error_msg = f"Failed to save image metadata: {e}"
                            logger.error(error_msg)
                            result['errors'].append(error_msg)
                    
                    logger.info(f"Saved {len(images)} image metadata entries to RAG store")
                
            except Exception as e:
                error_msg = f"Image extraction failed: {e}"
                logger.error(error_msg)
                result['errors'].append(error_msg)
        
        return result
    
    def process_web_images(
        self,
        url: str,
        doc_id: Optional[str] = None,
        run_ocr: bool = True,
        add_to_rag: bool = True
    ) -> Dict[str, Any]:
        """
        Extrahiert und verarbeitet Bilder von einer Webseite
        
        Args:
            url: URL der Webseite
            doc_id: Dokument-ID für RAG Store
            run_ocr: OCR auf Bildern ausführen
            add_to_rag: Ergebnisse zu RAG Store hinzufügen
            
        Returns:
            Dict mit Verarbeitungsergebnissen
        """
        if doc_id is None:
            # Nutze Domain als doc_id
            from urllib.parse import urlparse
            doc_id = f"web_{urlparse(url).netloc}"
        
        result = {
            'doc_id': doc_id,
            'url': url,
            'images_extracted': 0,
            'ocr_performed': 0,
            'images': [],
            'ocr_results': [],
            'errors': []
        }
        
        if not self.image_extractor:
            result['errors'].append("ImageExtractor not available")
            return result
        
        try:
            # Extrahiere Bilder von Webseite
            images = self.image_extractor.extract_from_web(url=url)
            result['images_extracted'] = len(images)
            result['images'] = images
            
            logger.info(f"Extracted {len(images)} images from {url}")
            
            # OCR auf Bildern
            if run_ocr and self.ocr_processor and images:
                for img_data in images:
                    try:
                        ocr_result = self.ocr_processor.extract_text(img_data.image_path)
                        result['ocr_results'].append(ocr_result)
                        
                        if ocr_result.text.strip():
                            result['ocr_performed'] += 1
                    except Exception as e:
                        error_msg = f"OCR failed for {img_data.image_path}: {e}"
                        logger.error(error_msg)
                        result['errors'].append(error_msg)
            
            # Speichere in RAG Store
            if add_to_rag and hasattr(self.rag_store, 'image_manager'):
                for i, img_data in enumerate(images):
                    try:
                        ocr_text = None
                        ocr_confidence = None
                        
                        if i < len(result['ocr_results']):
                            ocr_result = result['ocr_results'][i]
                            ocr_text = ocr_result.text
                            ocr_confidence = ocr_result.confidence
                        
                        self.rag_store.image_manager.add_image_metadata(
                            doc_id=doc_id,
                            image_path=img_data.image_path,
                            page_number=None,
                            image_index=img_data.index,
                            image_format=img_data.format,
                            width=img_data.width,
                            height=img_data.height,
                            size_bytes=img_data.size_bytes,
                            image_hash=img_data.hash,
                            alt_text=img_data.alt_text,
                            ocr_text=ocr_text,
                            ocr_confidence=ocr_confidence,
                            source='web'
                        )
                    except Exception as e:
                        error_msg = f"Failed to save image metadata: {e}"
                        logger.error(error_msg)
                        result['errors'].append(error_msg)
        
        except Exception as e:
            error_msg = f"Web image extraction failed: {e}"
            logger.error(error_msg)
            result['errors'].append(error_msg)
        
        return result
    
    def get_document_images(self, doc_id: str) -> List[Dict[str, Any]]:
        """
        Holt alle Bilder für ein Dokument
        
        Args:
            doc_id: Dokument-ID
            
        Returns:
            Liste von Bild-Metadaten
        """
        if not hasattr(self.rag_store, 'image_manager'):
            logger.warning("RAG store has no image_manager")
            return []
        
        return self.rag_store.image_manager.get_images_for_document(doc_id)
    
    def search_images_by_text(
        self,
        search_text: str,
        min_confidence: float = 0.5
    ) -> List[Dict[str, Any]]:
        """
        Sucht Bilder anhand von OCR-Text
        
        Args:
            search_text: Suchbegriff
            min_confidence: Minimale OCR-Konfidenz
            
        Returns:
            Gefundene Bilder
        """
        if not hasattr(self.rag_store, 'image_manager'):
            logger.warning("RAG store has no image_manager")
            return []
        
        return self.rag_store.image_manager.search_images_with_ocr(
            search_text=search_text,
            min_confidence=min_confidence
        )
    
    def get_statistics(self) -> Dict[str, Any]:
        """
        Holt Statistiken über verarbeitete Bilder
        
        Returns:
            Dict mit Statistiken
        """
        if not hasattr(self.rag_store, 'image_manager'):
            return {
                'error': 'RAG store has no image_manager',
                'total_images': 0
            }
        
        return self.rag_store.image_manager.get_statistics()
