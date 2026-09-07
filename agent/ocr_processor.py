"""
OCR Processor - Extrahiert Text aus Bildern
============================================

Dieses Modul bietet OCR (Optical Character Recognition) Funktionen:
- EasyOCR für mehrsprachige Text-Erkennung
- Batch-Verarbeitung für multiple Bilder
- Confidence-Scoring
- GPU-Beschleunigung (optional)

Verwendung:
    >>> from agent.ocr_processor import OCRProcessor
    >>> ocr = OCRProcessor(languages=['de', 'en'])
    >>> result = ocr.extract_text('screenshot.png')
    >>> print(f"Text: {result.text}, Confidence: {result.confidence:.2%}")
"""

import os
import gc
import logging
import weakref
from typing import Any, Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from datetime import datetime
import time

logger = logging.getLogger(__name__)

# SOTA (2026-08-28): Registry aktiver OCRProcessor-Instanzen (WeakSet).
# Ermöglicht das zentrale Entladen aller geladenen EasyOCR-Reader nach
# Import-/OCR-Peaks (siehe utils/aux_model_release.py). WeakRefs, damit
# Instanzen normal per GC abgebaut werden können.
_active_ocr_processors: "weakref.WeakSet[\"OCRProcessor\"]" = weakref.WeakSet()


@dataclass
class OCRResult:
    """Container für OCR-Ergebnis"""
    image_path: str
    text: str
    confidence: float  # 0.0 - 1.0
    language: str  # Erkannte/verwendete Sprachen
    processing_time: float  # Sekunden
    word_count: int = 0
    char_count: int = 0
    created_at: datetime = field(default_factory=datetime.now)
    
    def __post_init__(self):
        """Berechne Statistiken"""
        self.char_count = len(self.text)
        self.word_count = len(self.text.split())
    
    def to_dict(self) -> Dict:
        """Konvertiert zu Dictionary für DB-Speicherung"""
        return {
            'image_path': self.image_path,
            'text': self.text,
            'confidence': self.confidence,
            'language': self.language,
            'processing_time': self.processing_time,
            'word_count': self.word_count,
            'char_count': self.char_count,
            'created_at': self.created_at.isoformat()
        }


class OCRProcessor:
    """OCR für Text-Extraktion aus Bildern"""
    
    def __init__(self, 
                 languages: Optional[List[str]] = None,
                 use_gpu: bool = True,
                 model_storage_directory: Optional[str] = None) -> None:
        """
        Args:
            languages: Liste der zu erkennenden Sprachen (default: ['de', 'en'])
            use_gpu: Ob GPU verwendet werden soll
            model_storage_directory: Wo EasyOCR Modelle gespeichert werden
        """
        self.languages = languages or ['de', 'en']
        self.use_gpu = use_gpu
        self.model_storage_directory = model_storage_directory
        self.reader: Any = None  # Lazy loading

        # SOTA: In Registry aufnehmen, damit nach Import-Peaks zentral entladen
        # werden kann (utils/aux_model_release.py). WeakRef → kein Leak.
        _active_ocr_processors.add(self)

        logger.info(f"OCR Processor initialized (languages: {self.languages}, GPU: {use_gpu})")
    
    def _ensure_reader(self):
        """Lazy loading des OCR-Readers (nur beim ersten Aufruf)"""
        if self.reader is None:
            try:
                import easyocr  # type: ignore[import-untyped]
                logger.info("Loading EasyOCR model (this may take a moment)...")
                
                # Dual-GPU: EasyOCR (AUX-Modell) auf der AUX-GPU (RTX 3060 Ti)
                _gpu_arg: Any = self.use_gpu
                if self.use_gpu:
                    try:
                        from utils.gpu_devices import get_placement
                        _gpu_arg = get_placement().aux_device_string
                    except Exception:
                        _gpu_arg = True
                kwargs: Dict[str, Any] = {
                    'gpu': _gpu_arg,
                    'verbose': False
                }
                
                if self.model_storage_directory:
                    kwargs['model_storage_directory'] = self.model_storage_directory
                
                self.reader = easyocr.Reader(self.languages, **kwargs)
                logger.info("✅ EasyOCR model loaded successfully")
                
            except ImportError:
                logger.error("EasyOCR not installed: pip install easyocr")
                raise RuntimeError("EasyOCR nicht verfügbar. Installation: pip install easyocr")
            except Exception as e:
                logger.error(f"Error loading EasyOCR: {e}")
                raise
    
    def extract_text(self, 
                    image_path: str,
                    detail_level: int = 0,
                    paragraph: bool = True) -> OCRResult:
        """
        Extrahiert Text aus einem Bild
        
        Args:
            image_path: Pfad zum Bild
            detail_level: 0=fast, 1=balanced, 2=accurate
            paragraph: Ob Text zu Absätzen gruppiert werden soll
            
        Returns:
            OCRResult mit erkanntem Text
        """
        self._ensure_reader()
        
        if not os.path.exists(image_path):
            raise FileNotFoundError(f"Image not found: {image_path}")
        
        start_time = time.time()
        
        try:
            logger.debug(f"Running OCR on {image_path} (detail={detail_level})")
            
            # OCR durchführen
            results = self.reader.readtext(
                image_path,
                detail=detail_level,
                paragraph=paragraph
            )
            
            # Text kombinieren
            if results:
                # Format: [(bbox, text, confidence), ...]
                texts = [result[1] for result in results]
                confidences = [result[2] for result in results]
                
                combined_text = ' '.join(texts)
                avg_confidence = sum(confidences) / len(confidences) if confidences else 0.0
            else:
                combined_text = ""
                avg_confidence = 0.0
            
            processing_time = time.time() - start_time
            
            result = OCRResult(
                image_path=image_path,
                text=combined_text.strip(),
                confidence=avg_confidence,
                language=','.join(self.languages),
                processing_time=processing_time
            )
            
            logger.info(f"✅ OCR completed: {result.char_count} chars, "
                       f"{avg_confidence:.2%} confidence, {processing_time:.2f}s")
            
            return result
            
        except Exception as e:
            logger.error(f"OCR failed for {image_path}: {e}")
            processing_time = time.time() - start_time
            
            # Return empty result on error
            return OCRResult(
                image_path=image_path,
                text="",
                confidence=0.0,
                language=','.join(self.languages),
                processing_time=processing_time
            )
    
    def batch_extract(self, 
                     image_paths: List[str],
                     max_workers: int = 4,
                     detail_level: int = 0) -> List[OCRResult]:
        """
        Batch-OCR für mehrere Bilder (parallel)
        
        Args:
            image_paths: Liste von Bildpfaden
            max_workers: Anzahl paralleler Worker
            detail_level: OCR Detail-Level (0-2)
            
        Returns:
            List[OCRResult]: Ergebnisse für alle Bilder
        """
        from concurrent.futures import ThreadPoolExecutor
        
        logger.info(f"Starting batch OCR for {len(image_paths)} images (workers={max_workers})")
        
        # Ensure reader is loaded before threading
        self._ensure_reader()
        
        def process_one(img_path):
            return self.extract_text(img_path, detail_level=detail_level)
        
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            results = list(executor.map(process_one, image_paths))
        
        # Statistiken
        total_text = sum(len(r.text) for r in results)
        avg_conf = sum(r.confidence for r in results) / len(results) if results else 0.0
        total_time = sum(r.processing_time for r in results)
        
        logger.info(f"✅ Batch OCR completed: {total_text} total chars, "
                   f"{avg_conf:.2%} avg confidence, {total_time:.2f}s total")
        
        return results
    
    def is_text_rich(self, image_path: str, char_threshold: int = 50) -> bool:
        """
        Prüft ob Bild viel Text enthält
        
        Args:
            image_path: Pfad zum Bild
            char_threshold: Minimale Zeichenanzahl
            
        Returns:
            True wenn >= threshold Zeichen erkannt
        """
        result = self.extract_text(image_path)
        return result.char_count >= char_threshold
    
    def get_text_only(self, image_path: str) -> str:
        """
        Convenience: Extrahiert nur den Text (ohne Metadaten)
        
        Args:
            image_path: Pfad zum Bild
            
        Returns:
            Extrahierter Text
        """
        result = self.extract_text(image_path)
        return result.text
    
    def extract_with_positions(self, image_path: str) -> List[Tuple[str, Tuple, float]]:
        """
        Extrahiert Text mit Positionen (Bounding Boxes)
        
        Args:
            image_path: Pfad zum Bild
            
        Returns:
            List[(text, bbox, confidence)]: Text mit Position und Confidence
        """
        self._ensure_reader()
        
        if not os.path.exists(image_path):
            raise FileNotFoundError(f"Image not found: {image_path}")
        
        try:
            # OCR mit voller Detail-Info
            results = self.reader.readtext(image_path, detail=1)
            
            # Format: [(bbox, text, confidence), ...]
            formatted_results = []
            for bbox, text, conf in results:
                formatted_results.append((text, bbox, conf))
            
            logger.debug(f"Extracted {len(formatted_results)} text regions from {image_path}")
            return formatted_results
            
        except Exception as e:
            logger.error(f"Position extraction failed for {image_path}: {e}")
            return []
    
    def cleanup(self):
        """Cleanup: Gibt Reader-Ressourcen frei (idempotent, inkl. CUDA-Cache).

        SOTA (2026-08-28): Entlädt den EasyOCR-Reader aus GPU/RAM und gibt
        den CUDA-Cache an das OS zurück. Lazy: Der Reader wird beim nächsten
        OCR-Aufruf via _ensure_reader() transparent neu geladen.
        """
        if self.reader is not None:
            del self.reader
            self.reader = None
            gc.collect()
            try:
                import torch
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
            except Exception:
                pass
            logger.info("OCR reader cleaned up (VRAM freed)")
        # SOTA (2026-08-28): Bewusst KEIN discard aus der Registry. Die Instanz
        # bleibt im WeakSet für ihre gesamte Lebensdauer (GC räumt via WeakRef
        # auf). Lazy-Reload via _ensure_reader() würde sonst die Instanz
        # "verlieren" und ein nachfolgendes Release den neu geladenen Reader
        # nicht mehr finden. cleanup() ist idempotent (Guard oben).


def get_active_ocr_processors() -> List["OCRProcessor"]:
    """Liefert eine defensive Liste der aktiven OCRProcessor-Instanzen.

    SOTA (2026-08-28): Für das zentrale VRAM-Release nach Import-Peaks
    (utils/aux_model_release.py). Instanzen ohne geladenen Reader werden
    mitgeliefert; cleanup() ist idempotent und no-op ohne Reader.
    """
    return list(_active_ocr_processors)


# Convenience functions
def quick_ocr(image_path: str, languages: Optional[List[str]] = None) -> str:
    """
    Quick helper: Extrahiere Text aus Bild (ohne Metadaten)
    
    Args:
        image_path: Pfad zum Bild
        languages: Sprachen (default: ['de', 'en'])
        
    Returns:
        Extrahierter Text
    """
    ocr = OCRProcessor(languages=languages or ['de', 'en'])
    result = ocr.extract_text(image_path)
    return result.text


def batch_ocr(image_paths: List[str], languages: Optional[List[str]] = None) -> List[str]:
    """
    Quick helper: Batch-OCR (nur Texte, keine Metadaten)
    
    Args:
        image_paths: Liste von Bildpfaden
        languages: Sprachen (default: ['de', 'en'])
        
    Returns:
        Liste von extrahierten Texten
    """
    ocr = OCRProcessor(languages=languages or ['de', 'en'])
    results = ocr.batch_extract(image_paths)
    return [r.text for r in results]
