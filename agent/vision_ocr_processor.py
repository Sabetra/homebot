"""
Vision OCR Processor - OCR via Vision-LLM (aktuell geladenes LLM) + EasyOCR-Fallback
================================================================

Robuste OCR-Implementierung mit:
- Vision-Modell des aktuell geladenen LLMs (Produktion: Gemma 4 12B); Fallback: Standard-Vision-Modell (DEFAULT_MODEL)
- Fallback zu EasyOCR
- Fehlerbehandlung
- Batch-Verarbeitung
"""

import os
import gc
import time
import logging
import threading
import weakref
from typing import Any, List, Dict, Optional
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

# SOTA (2026-08-28): Registry aktiver VisionOCRProcessor-Instanzen (WeakSet).
# Parallel zur OCRProcessor-Registry (agent/ocr_processor.py) — ermöglicht das
# zentrale Entladen aller geladenen EasyOCR-Reader nach Import-/OCR-Peaks
# (siehe utils/aux_model_release.py). WeakRefs, damit Instanzen normal per GC
# abgebaut werden können.
_active_vision_ocr_processors: "weakref.WeakSet['VisionOCRProcessor']" = weakref.WeakSet()

# Import cuda_lock for thread-safe LLM access
try:
    from scripts.model_loader import cuda_lock as _cuda_lock
except ImportError:
    _cuda_lock = threading.RLock()


@dataclass
class VisionOCRResult:
    """Container für Vision-OCR-Ergebnis"""
    image_path: str
    text: str
    confidence: float  # 0.0 - 1.0 (geschätzt bei Vision Models)
    model_used: str  # 'vision-llm' | 'easyocr' (jeweils + '-failed' bei Fehler)
    processing_time: float  # Sekunden
    word_count: int = 0
    char_count: int = 0
    created_at: datetime = field(default_factory=datetime.now)
    
    def __post_init__(self):
        """Berechne Statistiken"""
        self.char_count = len(self.text)
        self.word_count = len(self.text.split())
    
    def to_dict(self) -> Dict:
        """Konvertiert zu Dictionary"""
        return {
            'image_path': self.image_path,
            'text': self.text,
            'confidence': self.confidence,
            'model_used': self.model_used,
            'processing_time': self.processing_time,
            'word_count': self.word_count,
            'char_count': self.char_count,
            'created_at': self.created_at.isoformat()
        }


class VisionOCRProcessor:
    """OCR via Vision-LLM (aktuell geladenes LLM) + EasyOCR-Fallback"""
    
    def __init__(
        self,
        use_vision_model: bool = True,
        model_loader = None,  # ModelLoader Singleton
        fallback_to_easyocr: bool = True,
        easyocr_languages: Optional[List[str]] = None
    ):
        """
        Args:
            use_vision_model: Ob das Vision-Modell des geladenen LLMs verwendet werden soll
            model_loader: ModelLoader Singleton-Instanz (optional, wird automatisch geholt)
            fallback_to_easyocr: Bei Fehler zu EasyOCR fallback
            easyocr_languages: Sprachen für EasyOCR (default: ['de', 'en'])
        """
        self.use_vision_model = use_vision_model
        self.fallback_to_easyocr = fallback_to_easyocr
        self.easyocr_languages = easyocr_languages or ['de', 'en']
        
        # ModelLoader Singleton verwenden
        if model_loader is None:
            try:
                from scripts.model_loader import ModelLoader
                self.model_loader: Optional[Any] = ModelLoader()
                logger.info("✅ Using ModelLoader Singleton")
            except ImportError:
                logger.warning("ModelLoader not available, will use direct loading")
                self.model_loader = None
        else:
            self.model_loader = model_loader
        
        # Lazy loading
        self.easyocr_reader = None
        
        # SOTA (2026-08-28): In Registry aufnehmen, damit nach Import-Peaks
        # zentral entladen werden kann (utils/aux_model_release.py).
        # WeakRef → kein Leak; bleibt für die Lebensdauer der Instanz erhalten.
        _active_vision_ocr_processors.add(self)
        
        logger.info(f"VisionOCRProcessor initialized (vision: {use_vision_model}, "
                   f"fallback: {fallback_to_easyocr}, using ModelLoader: {self.model_loader is not None})")
    
    def _ensure_vision_model(self):
        """Lazy loading des Vision Models über ModelLoader Singleton

        Nutzt die Vision-Fähigkeit des aktuell geladenen LLMs. Nur wenn das
        aktuelle LLM keine Vision hat (oder keines geladen ist) wird das
        Standard-Modell (DEFAULT_MODEL) geladen — konsistent mit
        agent/pdf_vision_extractor.py. Kein hartkodierter Magistral-Pfad.
        In diesem Fallback wird das geteilte Haupt-LLM-Slot ersetzt (es wird
        nie ein zweites großes Vision-Modell parallel gehalten).
        """
        if self.use_vision_model:
            try:
                # Verwende ModelLoader Singleton
                if self.model_loader is None:
                    raise RuntimeError("ModelLoader not available - cannot load Vision model")

                # Prüfe ob Vision Model bereits geladen ist
                if self.model_loader.llm is None or not self.model_loader.is_multimodal:
                    from scripts.model_loader import DEFAULT_MODEL
                    logger.info(f"Loading Vision Model ({DEFAULT_MODEL}) via ModelLoader...")

                    success = self.model_loader.load_model_by_config(DEFAULT_MODEL)

                    if not success:
                        raise RuntimeError("Failed to load Vision model via ModelLoader")

                    logger.info("✅ Vision Model loaded successfully via ModelLoader")
                else:
                    logger.info("✅ Vision Model already loaded in ModelLoader")
                
            except Exception as e:
                logger.error(f"Failed to load Vision Model: {e}")
                if not self.fallback_to_easyocr:
                    raise
                logger.warning("Will use EasyOCR fallback")
                self.use_vision_model = False
    
    def _ensure_easyocr_reader(self):
        """Lazy loading des EasyOCR Readers"""
        if self.easyocr_reader is None:
            try:
                import easyocr  # type: ignore[import-untyped]
                logger.info("Loading EasyOCR model...")
                
                # Dual-GPU: EasyOCR (AUX-Modell) auf der AUX-GPU (RTX 3060 Ti)
                try:
                    from utils.gpu_devices import get_placement
                    _gpu_dev: Any = get_placement().aux_device_string
                except Exception:
                    _gpu_dev = True
                self.easyocr_reader = easyocr.Reader(
                    self.easyocr_languages,
                    gpu=_gpu_dev,
                    verbose=False
                )
                
                logger.info("✅ EasyOCR model loaded successfully")
                
            except Exception as e:
                logger.error(f"Failed to load EasyOCR: {e}")
                raise RuntimeError(f"EasyOCR not available: {e}")
    
    def extract_text_with_vision(self, image_path: str) -> VisionOCRResult:
        """
        Extrahiert Text mit dem Vision-LLM (aktuell geladenes multimodales LLM, Fallback: DEFAULT_MODEL)
        
        Args:
            image_path: Pfad zum Bild
            
        Returns:
            VisionOCRResult
        """
        self._ensure_vision_model()
        
        if not os.path.exists(image_path):
            raise FileNotFoundError(f"Image not found: {image_path}")
        
        # Verwende das geladene Model aus dem ModelLoader
        if self.model_loader is None or self.model_loader.llm is None:
            raise RuntimeError("Vision model not loaded - cannot perform OCR")
        
        start_time = time.time()
        
        try:
            # Konvertiere zu absoluten Pfad
            abs_path = os.path.abspath(image_path)
            
            # Option 1: Versuche mit base64-encoded Bild (EMPFOHLEN für llama.cpp)
            import base64
            
            with open(abs_path, 'rb') as img_file:
                img_data = base64.b64encode(img_file.read()).decode('utf-8')
            
            # Erkenne Bildformat
            img_ext = os.path.splitext(abs_path)[1].lower()
            mime_type = {
                '.jpg': 'image/jpeg',
                '.jpeg': 'image/jpeg',
                '.png': 'image/png',
                '.gif': 'image/gif',
                '.webp': 'image/webp'
            }.get(img_ext, 'image/jpeg')
            
            data_url = f"data:{mime_type};base64,{img_data}"
            
            logger.debug(f"Image path: {abs_path}")
            logger.debug(f"Image format: {mime_type}")
            logger.debug(f"Data URL length: {len(data_url)}")
            
            # Vision Model Prompt für OCR
            prompt = (
                "Please extract all visible text from this image. "
                "Return ONLY the text content, without any explanations or descriptions. "
                "If there is no text, return an empty response."
            )
            
            # Chat completion mit Bild über ModelLoader
            # ── CRITICAL: cuda_lock prevents concurrent llama.cpp access ──
            with _cuda_lock:
                response = self.model_loader.llm.create_chat_completion(
                    messages=[
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": prompt},
                                {"type": "image_url", "image_url": {"url": data_url}}
                            ]
                        }
                    ],
                    max_tokens=512,
                    temperature=0.1,  # Niedrig für konsistente Ergebnisse
                    stream=False  # Kein Streaming für einfacheren Zugriff
                )
            
            # Extrahiere Text (mit Type-Safe Zugriff)
            if isinstance(response, dict) and 'choices' in response:
                content = response['choices'][0]['message'].get('content', '')
                extracted_text = content.strip() if content else ""
            else:
                raise ValueError("Invalid response format from Vision model")
            
            processing_time = time.time() - start_time
            
            # Schätze Konfidenz basierend auf Textlänge (Vision Models geben keine Konfidenz)
            confidence = min(0.95, 0.5 + (len(extracted_text) / 1000))
            
            result = VisionOCRResult(
                image_path=image_path,
                text=extracted_text,
                confidence=confidence,
                model_used='vision-llm',
                processing_time=processing_time
            )
            
            logger.info(f"✅ Vision OCR: {result.char_count} chars, {processing_time:.2f}s")
            
            return result
            
        except Exception as e:
            logger.error(f"Vision OCR failed for {image_path}: {e}")
            
            # Fallback zu EasyOCR?
            if self.fallback_to_easyocr:
                logger.info("Falling back to EasyOCR...")
                return self.extract_text_with_easyocr(image_path)
            
            # Sonst leeres Ergebnis
            processing_time = time.time() - start_time
            return VisionOCRResult(
                image_path=image_path,
                text="",
                confidence=0.0,
                model_used='vision-llm-failed',
                processing_time=processing_time
            )
    
    def extract_text_with_easyocr(self, image_path: str) -> VisionOCRResult:
        """
        Extrahiert Text mit EasyOCR
        
        Args:
            image_path: Pfad zum Bild
            
        Returns:
            VisionOCRResult
        """
        self._ensure_easyocr_reader()
        
        if not os.path.exists(image_path):
            raise FileNotFoundError(f"Image not found: {image_path}")
        
        start_time = time.time()
        
        try:
            # OCR durchführen (mit Type-Safe Zugriff)
            if self.easyocr_reader is None:
                raise RuntimeError("EasyOCR reader not initialized")
            
            results = self.easyocr_reader.readtext(image_path, detail=1, paragraph=True)  # type: ignore
            
            # Text kombinieren
            if results:
                # Type-Safe: results ist eine Liste von Listen/Tupeln
                texts = [str(result[1]) for result in results if len(result) > 1 and result[1]]  # type: ignore
                confidences = [float(result[2]) for result in results if len(result) > 2]  # type: ignore
                
                combined_text = ' '.join(texts)
                avg_confidence = sum(confidences) / len(confidences) if confidences else 0.0
            else:
                combined_text = ""
                avg_confidence = 0.0
            
            processing_time = time.time() - start_time
            
            result = VisionOCRResult(
                image_path=image_path,
                text=combined_text.strip(),
                confidence=avg_confidence,
                model_used='easyocr',
                processing_time=processing_time
            )
            
            logger.info(f"✅ EasyOCR: {result.char_count} chars, "
                       f"{avg_confidence:.2%} confidence, {processing_time:.2f}s")
            
            return result
            
        except Exception as e:
            logger.error(f"EasyOCR failed for {image_path}: {e}")
            processing_time = time.time() - start_time
            
            # Leeres Ergebnis bei Fehler
            return VisionOCRResult(
                image_path=image_path,
                text="",
                confidence=0.0,
                model_used='easyocr-failed',
                processing_time=processing_time
            )
    
    def extract_text(self, image_path: str) -> VisionOCRResult:
        """
        Extrahiert Text (automatische Model-Auswahl)
        
        Args:
            image_path: Pfad zum Bild
            
        Returns:
            VisionOCRResult
        """
        if self.use_vision_model:
            return self.extract_text_with_vision(image_path)
        else:
            return self.extract_text_with_easyocr(image_path)
    
    def batch_extract(
        self,
        image_paths: List[str],
        show_progress: bool = True
    ) -> List[VisionOCRResult]:
        """
        Batch-OCR für mehrere Bilder
        
        Args:
            image_paths: Liste von Bildpfaden
            show_progress: Fortschritt anzeigen
            
        Returns:
            List[VisionOCRResult]
        """
        logger.info(f"Starting batch OCR for {len(image_paths)} images...")
        
        results = []
        
        for i, img_path in enumerate(image_paths, 1):
            if show_progress and i % 10 == 0:
                logger.info(f"Progress: {i}/{len(image_paths)} images processed")
            
            result = self.extract_text(img_path)
            results.append(result)
        
        # Statistiken
        total_text = sum(len(r.text) for r in results)
        avg_conf = sum(r.confidence for r in results) / len(results) if results else 0.0
        total_time = sum(r.processing_time for r in results)
        
        logger.info(f"✅ Batch OCR completed: {total_text} total chars, "
                   f"{avg_conf:.2%} avg confidence, {total_time:.2f}s total")
        
        return results
    
    def cleanup(self):
        """Cleanup: Kalte Ressourcen (EasyOCR-Reader) freigeben — idempotent.

        SOTA (2026-08-28): Entlädt den EasyOCR-Reader aus GPU/RAM und gibt
        den CUDA-Cache an das OS zurück (gc.collect + torch.cuda.empty_cache).
        Das Vision-Modell (aktuell geladenes LLM) lebt im geteilten ModelLoader-Singleton
        (dieselbe Slot wie das Produktiv-LLM) und wird hier bewusst NICHT
        berührt — sonst würde das Haupt-LLM entladen. Lazy: Der Reader wird
        beim nächsten EasyOCR-Aufruf via _ensure_easyocr_reader() transparent
        neu geladen. Bewusst NICHT aus der Registry entfernt (wie
        ocr_processor.cleanup) — der Lazy-Reload muss erreichbar bleiben.
        """
        released = False

        # Kalt-Modell: EasyOCR-Reader (AUX-GPU)
        if getattr(self, "easyocr_reader", None) is not None:
            del self.easyocr_reader
            self.easyocr_reader = None
            released = True
            logger.info("VisionOCRProcessor: EasyOCR reader cleaned up")

        # Defensiv: falls ein vision_model-Attribut in einem Code-Pfad gesetzt
        # wurde, entfernen. Das eigentliche Modell lebt im geteilten ModelLoader
        # und wird hier bewusst nicht berührt (Schutz des Haupt-LLM).
        if getattr(self, "vision_model", None) is not None:
            del self.vision_model
            self.vision_model = None
            released = True
            logger.info("VisionOCRProcessor: vision_model-Attribut entfernt")

        if released:
            gc.collect()
            try:
                import torch
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
            except Exception:
                pass
            logger.info("VisionOCRProcessor: VRAM freigegeben (gc + CUDA-Cache)")


def get_active_vision_ocr_processors() -> List["VisionOCRProcessor"]:
    """Liefert eine defensive Liste der aktiven VisionOCRProcessor-Instanzen.

    SOTA (2026-08-28): Für das zentrale VRAM-Release nach Import-/OCR-Peaks
    (utils/aux_model_release.py). Instanzen ohne geladenen EasyOCR-Reader
    werden mitgeliefert; cleanup() ist idempotent und no-op ohne Reader.
    """
    return list(_active_vision_ocr_processors)
