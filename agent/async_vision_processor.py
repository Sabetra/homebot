"""
Async Vision Processor - Parallele Vision-Verarbeitung
=======================================================

State-of-the-Art Async Processing (2025):
- Asyncio-basierte parallele Bildverarbeitung
- Semaphore-gesteuertes Rate-Limiting (GPU-Memory-Aware)
- Progressive Loading (Low-DPI Preview → High-DPI Detail)
- Batch-Processing für mehrere Bilder
- Request-Deduplication integriert

Features:
- AsyncBatchProcessor: Verarbeitet mehrere Bilder parallel
- ProgressiveLoader: Schnelle Vorschau, dann Details
- RateLimiter: Verhindert GPU-Überlastung
"""

import os
import asyncio
import logging
import tempfile
import time
from pathlib import Path
from typing import List, Dict, Any, Optional, Callable, Tuple
from dataclasses import dataclass, field
from concurrent.futures import ThreadPoolExecutor
import threading

logger = logging.getLogger(__name__)

# Import cuda_lock for thread-safe LLM access.
# _vision_analyze_sync runs in ThreadPoolExecutor and calls
# loader.llm.create_chat_completion() directly -- without this lock,
# those calls race with main-thread inference → access-violation crash.
try:
    from scripts.model_loader import cuda_lock as _cuda_lock
except ImportError:
    _cuda_lock = threading.RLock()  # Fallback


@dataclass
class VisionResult:
    """Ergebnis einer Vision-Analyse"""
    image_path: str
    description: str = ""
    confidence: float = 0.0
    processing_time_ms: float = 0.0
    from_cache: bool = False
    error: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class BatchResult:
    """Ergebnis einer Batch-Verarbeitung"""
    total_images: int = 0
    successful: int = 0
    failed: int = 0
    cached: int = 0
    total_time_ms: float = 0.0
    results: List[VisionResult] = field(default_factory=list)
    
    @property
    def success_rate(self) -> float:
        return self.successful / self.total_images if self.total_images > 0 else 0.0
    
    @property
    def cache_hit_rate(self) -> float:
        return self.cached / self.total_images if self.total_images > 0 else 0.0


class RateLimiter:
    """
    Token-Bucket Rate Limiter für GPU-Ressourcen.
    
    Begrenzt parallele Vision-LLM-Aufrufe um GPU-Memory-Exhaustion zu vermeiden.
    """
    
    def __init__(
        self,
        max_concurrent: int = 3,
        tokens_per_second: float = 1.0,
        max_tokens: int = 5
    ):
        self.max_concurrent = max_concurrent
        self.tokens_per_second = tokens_per_second
        self.max_tokens = max_tokens
        
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._tokens = max_tokens
        self._last_update = time.time()
        self._lock = asyncio.Lock()
    
    async def acquire(self):
        """Wartet auf verfügbaren Slot"""
        await self._semaphore.acquire()
        
        async with self._lock:
            # Token-Bucket auffüllen
            now = time.time()
            elapsed = now - self._last_update
            self._tokens = min(
                self.max_tokens,
                self._tokens + elapsed * self.tokens_per_second
            )
            self._last_update = now
            
            # Warten wenn keine Tokens
            while self._tokens < 1:
                await asyncio.sleep(0.1)
                elapsed = time.time() - self._last_update
                self._tokens = min(
                    self.max_tokens,
                    self._tokens + elapsed * self.tokens_per_second
                )
                self._last_update = time.time()
            
            self._tokens -= 1
    
    def release(self):
        """Gibt Slot frei"""
        self._semaphore.release()
    
    async def __aenter__(self):
        await self.acquire()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        self.release()


class AsyncBatchProcessor:
    """
    Async Batch-Processor für Vision-LLM-Analysen.
    
    Verarbeitet mehrere Bilder parallel mit konfigurierbarem
    Rate-Limiting und Cache-Integration.
    """
    
    def __init__(
        self,
        model_loader=None,
        max_concurrent: int = 3,
        enable_cache: bool = True,
        enable_metrics: bool = True
    ):
        """
        Args:
            model_loader: ModelLoader Singleton
            max_concurrent: Max parallele Verarbeitungen (GPU-abhängig)
            enable_cache: VisionCache verwenden
            enable_metrics: Metriken erfassen
        """
        self._model_loader = model_loader
        self.max_concurrent = max_concurrent
        self.enable_cache = enable_cache
        self.enable_metrics = enable_metrics
        
        # Rate Limiter
        self._rate_limiter = RateLimiter(
            max_concurrent=max_concurrent,
            tokens_per_second=0.5,  # Max 1 Request pro 2 Sekunden pro Slot
            max_tokens=max_concurrent * 2
        )
        
        # Thread-Pool für synchrone Operationen
        self._executor = ThreadPoolExecutor(max_workers=max_concurrent * 2)
        
        # Cache (lazy loaded)
        self._cache = None
        self._metrics = None
        
        logger.info(f"AsyncBatchProcessor initialized (max_concurrent={max_concurrent})")
    
    @property
    def model_loader(self):
        """Lazy-loads ModelLoader"""
        if self._model_loader is None:
            try:
                from scripts.model_loader import ModelLoader
                self._model_loader = ModelLoader()
            except Exception as e:
                logger.error(f"ModelLoader nicht verfügbar: {e}")
        return self._model_loader
    
    @property
    def cache(self):
        """Lazy-loads VisionCache"""
        if self._cache is None and self.enable_cache:
            try:
                from .extraction_cache import get_vision_cache
                self._cache = get_vision_cache()
            except Exception as e:
                logger.warning(f"VisionCache nicht verfügbar: {e}")
        return self._cache
    
    @property
    def metrics(self):
        """Lazy-loads MetricsEvaluator"""
        if self._metrics is None and self.enable_metrics:
            try:
                from .extraction_metrics import get_quality_evaluator
                self._metrics = get_quality_evaluator()
            except Exception as e:
                logger.warning(f"MetricsEvaluator nicht verfügbar: {e}")
        return self._metrics
    
    async def _analyze_single_async(
        self,
        image_path: str,
        prompt: Optional[str] = None
    ) -> VisionResult:
        """
        Analysiert einzelnes Bild asynchron.
        """
        start_time = time.time()
        result = VisionResult(image_path=image_path)
        
        try:
            # Cache-Check
            if self.cache:
                cached = self.cache.get_by_image(image_path)
                if cached:
                    result.description = cached.get('description', '')
                    result.confidence = cached.get('confidence', 0.8)
                    result.from_cache = True
                    result.processing_time_ms = (time.time() - start_time) * 1000
                    return result
            
            # Rate-limited Vision-Analyse
            async with self._rate_limiter:
                # In Thread-Pool ausführen (ModelLoader ist synchron)
                loop = asyncio.get_event_loop()
                description = await loop.run_in_executor(
                    self._executor,
                    self._vision_analyze_sync,
                    image_path,
                    prompt
                )
            
            result.description = description
            result.confidence = 0.85  # Default-Confidence
            result.processing_time_ms = (time.time() - start_time) * 1000
            
            # Cachen
            if self.cache and description:
                self.cache.put_image_result(
                    image_path,
                    {
                        'description': description,
                        'confidence': result.confidence,
                        'timestamp': time.time()
                    }
                )
            
            return result
            
        except Exception as e:
            result.error = str(e)
            result.processing_time_ms = (time.time() - start_time) * 1000
            logger.error(f"Vision-Analyse fehlgeschlagen für {image_path}: {e}")
            return result
    
    def _vision_analyze_sync(
        self,
        image_path: str,
        prompt: Optional[str] = None
    ) -> str:
        """Synchrone Vision-Analyse (wird im ThreadPool ausgeführt)"""
        loader = self.model_loader
        if not loader:
            raise RuntimeError("ModelLoader nicht verfügbar")
        
        # Default-Prompt
        if not prompt:
            prompt = """Analysiere dieses Bild detailliert:

1. Was zeigt das Bild? (Typ: Foto, Diagramm, Chart, Infografik, etc.)
2. Welche Informationen werden dargestellt?
3. Falls Zahlen/Daten vorhanden: Extrahiere die wichtigsten Werte.
4. Falls Text vorhanden: Transkribiere die wichtigsten Textelemente.
5. Relevanz: Warum ist dieses Bild informativ?

Antworte präzise und strukturiert."""

        # Vision-LLM aufrufen via llama-cpp-python
        import base64
        try:
            with open(image_path, 'rb') as f:
                img_data = base64.b64encode(f.read()).decode('utf-8')
            
            # MIME-Type bestimmen
            ext = os.path.splitext(image_path)[1].lower()
            mime_type = {
                '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
                '.png': 'image/png', '.gif': 'image/gif',
                '.webp': 'image/webp'
            }.get(ext, 'image/png')
            
            data_url = f"data:{mime_type};base64,{img_data}"
            
            if loader.llm:
                # ── CRITICAL: cuda_lock prevents concurrent llama.cpp access ──
                with _cuda_lock:
                    response = loader.llm.create_chat_completion(
                        messages=[
                            {
                                "role": "user",
                                "content": [
                                    {"type": "text", "text": prompt},
                                    {"type": "image_url", "image_url": {"url": data_url}}
                                ]
                            }
                        ],
                        max_tokens=1500,
                        temperature=0.2
                    )
                
                if isinstance(response, dict) and 'choices' in response:
                    content = response['choices'][0]['message'].get('content', '')
                    return content.strip() if content else ""
            
            return ""
            
        except Exception as e:
            logger.error(f"Vision-Analyse fehlgeschlagen: {e}")
            return ""
    
    async def process_batch(
        self,
        image_paths: List[str],
        prompt: Optional[str] = None,
        on_progress: Optional[Callable[[int, int], None]] = None
    ) -> BatchResult:
        """
        Verarbeitet mehrere Bilder parallel.
        
        Args:
            image_paths: Liste von Bildpfaden
            prompt: Optional, Custom-Prompt für alle Bilder
            on_progress: Callback (completed, total)
            
        Returns:
            BatchResult mit allen Ergebnissen
        """
        start_time = time.time()
        result = BatchResult(total_images=len(image_paths))
        
        if not image_paths:
            return result
        
        logger.info(f"Batch-Verarbeitung gestartet: {len(image_paths)} Bilder")
        
        # Async Tasks erstellen
        tasks = [
            self._analyze_single_async(path, prompt)
            for path in image_paths
        ]
        
        # Mit Progress-Tracking ausführen
        completed = 0
        for coro in asyncio.as_completed(tasks):
            vision_result = await coro
            result.results.append(vision_result)
            
            if vision_result.error:
                result.failed += 1
            else:
                result.successful += 1
                if vision_result.from_cache:
                    result.cached += 1
            
            completed += 1
            if on_progress:
                on_progress(completed, len(image_paths))
        
        result.total_time_ms = (time.time() - start_time) * 1000
        
        logger.info(
            f"Batch-Verarbeitung abgeschlossen: "
            f"{result.successful}/{result.total_images} erfolgreich, "
            f"{result.cached} aus Cache, "
            f"{result.total_time_ms:.0f}ms"
        )
        
        return result
    
    def process_batch_sync(
        self,
        image_paths: List[str],
        prompt: Optional[str] = None,
        on_progress: Optional[Callable[[int, int], None]] = None
    ) -> BatchResult:
        """
        Synchroner Wrapper für process_batch.
        
        Für Verwendung in nicht-async Code.
        """
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        
        return loop.run_until_complete(
            self.process_batch(image_paths, prompt, on_progress)
        )


class ProgressiveLoader:
    """
    Progressive Bild-Ladestrategie.
    
    Lädt zuerst niedrig aufgelöste Versionen für schnelle Vorschau,
    dann bei Bedarf hochauflösende für Details.
    """
    
    def __init__(
        self,
        low_dpi: int = 72,
        high_dpi: int = 200,
        preview_timeout_ms: int = 500
    ):
        self.low_dpi = low_dpi
        self.high_dpi = high_dpi
        self.preview_timeout_ms = preview_timeout_ms
        self._temp_files: List[str] = []
    
    def create_preview(self, pdf_path: str, page_num: int) -> Optional[str]:
        """
        Erstellt niedrig aufgelöste Vorschau einer PDF-Seite.
        
        Returns:
            Pfad zur Vorschau-Datei
        """
        try:
            import fitz
            
            doc = fitz.open(pdf_path)
            page = doc[page_num]
            
            # Niedrige Auflösung
            mat = fitz.Matrix(self.low_dpi / 72, self.low_dpi / 72)
            pix = page.get_pixmap(matrix=mat)
            
            # Temporär speichern
            temp_path = tempfile.mktemp(suffix='_preview.png')
            pix.save(temp_path)
            self._temp_files.append(temp_path)
            
            doc.close()
            return temp_path
            
        except Exception as e:
            logger.debug(f"Preview-Erstellung fehlgeschlagen: {e}")
            return None
    
    def create_high_res(self, pdf_path: str, page_num: int) -> Optional[str]:
        """
        Erstellt hochauflösende Version einer PDF-Seite.
        
        Returns:
            Pfad zur High-Res-Datei
        """
        try:
            import fitz
            
            doc = fitz.open(pdf_path)
            page = doc[page_num]
            
            # Hohe Auflösung
            mat = fitz.Matrix(self.high_dpi / 72, self.high_dpi / 72)
            pix = page.get_pixmap(matrix=mat)
            
            # Temporär speichern
            temp_path = tempfile.mktemp(suffix='_highres.png')
            pix.save(temp_path)
            self._temp_files.append(temp_path)
            
            doc.close()
            return temp_path
            
        except Exception as e:
            logger.debug(f"High-Res-Erstellung fehlgeschlagen: {e}")
            return None
    
    async def analyze_progressive(
        self,
        processor: AsyncBatchProcessor,
        pdf_path: str,
        page_num: int,
        quick_prompt: str = "Beschreibe kurz was auf diesem Bild zu sehen ist.",
        detailed_prompt: str = "Analysiere dieses Bild sehr detailliert."
    ) -> Tuple[VisionResult, Optional[VisionResult]]:
        """
        Progressive Analyse: Erst schnelle Vorschau, dann Details.
        
        Returns:
            Tuple (preview_result, detail_result_or_none)
        """
        # 1. Quick Preview
        preview_path = self.create_preview(pdf_path, page_num)
        if not preview_path:
            return VisionResult(image_path=pdf_path, error="Preview fehlgeschlagen"), None
        
        preview_result = await processor._analyze_single_async(preview_path, quick_prompt)
        
        # 2. Entscheiden ob Detail-Analyse nötig
        needs_detail = self._needs_detail_analysis(preview_result)
        
        if not needs_detail:
            return preview_result, None
        
        # 3. Detail-Analyse
        high_res_path = self.create_high_res(pdf_path, page_num)
        if not high_res_path:
            return preview_result, None
        
        detail_result = await processor._analyze_single_async(high_res_path, detailed_prompt)
        
        return preview_result, detail_result
    
    def _needs_detail_analysis(self, preview: VisionResult) -> bool:
        """Entscheidet ob Detail-Analyse nötig ist"""
        if preview.error:
            return False
        
        desc = preview.description.lower()
        
        # Trigger für Detail-Analyse
        detail_triggers = [
            'chart', 'graph', 'diagram', 'tabelle', 'table',
            'statistik', 'daten', 'data', 'zahlen', 'numbers',
            'infografik', 'infographic', 'prozent', 'percent'
        ]
        
        for trigger in detail_triggers:
            if trigger in desc:
                return True
        
        return False
    
    def cleanup(self):
        """Entfernt temporäre Dateien und fährt Executor herunter"""
        for path in self._temp_files:
            try:
                if os.path.exists(path):
                    os.remove(path)
            except OSError as exc:
                logger.debug(f"Temp-File-Cleanup fehlgeschlagen ({path}): {exc}")
        self._temp_files.clear()
        # ✅ SOTA: Shutdown ThreadPoolExecutor to release worker threads
        executor = getattr(self, "_executor", None)
        if executor is not None:
            try:
                executor.shutdown(wait=False)
            except Exception as exc:
                logger.debug(f"Executor-Shutdown fehlgeschlagen: {exc}")
            self._executor = None
    
    def __del__(self):
        self.cleanup()


# Convenience-Funktionen

async def process_images_async(
    image_paths: List[str],
    max_concurrent: int = 3,
    prompt: Optional[str] = None
) -> BatchResult:
    """
    Convenience-Funktion für Batch-Verarbeitung.
    """
    processor = AsyncBatchProcessor(max_concurrent=max_concurrent)
    return await processor.process_batch(image_paths, prompt)


def process_images_sync(
    image_paths: List[str],
    max_concurrent: int = 3,
    prompt: Optional[str] = None
) -> BatchResult:
    """
    Synchrone Convenience-Funktion für Batch-Verarbeitung.
    """
    processor = AsyncBatchProcessor(max_concurrent=max_concurrent)
    return processor.process_batch_sync(image_paths, prompt)
