"""
PDF Vision Extractor - State-of-the-Art für Infografiken & Statistiken
======================================================================

Dieses Modul implementiert eine hybride PDF-Extraktion:

1. PDF-Typ-Erkennung (text_native, scanned, hybrid)
2. Text-Extraktion via pymupdf4llm
3. Vision-LLM-Analyse für Infografiken/Charts/Tabellen
4. Strukturierte Kombination der Ergebnisse

Verwendet das Vision-Modell des geladenen LLMs (Fallback: DEFAULT_MODEL) via ModelLoader Singleton.
Keine Daten verlassen das System.

NEU (State-of-the-Art 2025):
- Content-Addressable Caching (SHA256 + pHash)
- RAGAS-inspirierte Qualitätsmetriken
- Async Batch Processing Support

Verwendung:
    >>> from agent.pdf_vision_extractor import PDFVisionExtractor
    >>> extractor = PDFVisionExtractor()
    >>> result = extractor.extract_complete(pdf_path)
    >>> print(result['text'], result['infographics'])
"""

import os
import logging
import tempfile
import base64
import threading
from typing import Dict, Any, Optional, List, Tuple
from pathlib import Path
from dataclasses import dataclass, field
from datetime import datetime
import time

logger = logging.getLogger(__name__)

# Import cuda_lock for thread-safe LLM access
try:
    from scripts.model_loader import cuda_lock as _cuda_lock
except ImportError:
    _cuda_lock = threading.RLock()

# State-of-the-Art 2025: Performance & Quality Module
try:
    from .extraction_cache import get_vision_cache, VisionCache
    CACHE_AVAILABLE = True
except ImportError:
    CACHE_AVAILABLE = False
    VisionCache = None  # type: ignore
    get_vision_cache = None  # type: ignore
    logger.debug("VisionCache nicht verfügbar")

try:
    from .extraction_metrics import get_quality_evaluator, ExtractionQualityEvaluator
    METRICS_AVAILABLE = True
except ImportError:
    METRICS_AVAILABLE = False
    ExtractionQualityEvaluator = None  # type: ignore
    get_quality_evaluator = None  # type: ignore
    logger.debug("ExtractionMetrics nicht verfügbar")


@dataclass
class PDFPageAnalysis:
    """Analyse einer einzelnen PDF-Seite"""
    page_number: int
    text_content: str = ""
    text_char_count: int = 0
    has_images: bool = False
    image_count: int = 0
    is_text_native: bool = True  # True = eingebetteter Text, False = gescannt
    vision_analysis: str = ""  # Strukturierte Vision-LLM-Analyse
    tables_detected: int = 0
    infographics_detected: int = 0


@dataclass
class PDFAnalysisResult:
    """Gesamtergebnis der PDF-Analyse"""
    file_path: str
    total_pages: int
    pdf_type: str  # "text_native", "scanned", "hybrid"
    
    # Text-Extraktion
    full_text: str = ""
    extraction_method: str = "unknown"
    
    # Vision-Analyse
    vision_enhanced_text: str = ""  # Kombiniert Text + Vision-Analyse
    infographic_descriptions: List[str] = field(default_factory=list)
    table_descriptions: List[str] = field(default_factory=list)
    
    # Statistiken
    total_text_chars: int = 0
    total_images: int = 0
    vision_analyzed_pages: int = 0
    cached_pages: int = 0  # NEU: Cache-Hits
    processing_time: float = 0.0
    
    # Quality Metrics (NEU: RAGAS-inspiriert)
    quality_score: float = 0.0
    faithfulness: float = 0.0
    context_precision: float = 0.0
    multimodal_coherence: float = 0.0
    
    # Metadaten
    page_analyses: List[PDFPageAnalysis] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)


class PDFVisionExtractor:
    """
    State-of-the-Art PDF-Extraktion mit Vision-LLM für Infografiken.
    
    Diese Klasse kombiniert:
    - pymupdf4llm für optimale Text-Extraktion
    - Vision-LLM (aktuell geladenes multimodales Modell) für Infografik-/Tabellen-Analyse
    - Intelligente PDF-Typ-Erkennung
    
    Alles läuft lokal, keine externen API-Calls.
    """
    
    def __init__(
        self,
        model_loader=None,
        text_threshold: int = 100,  # Min Zeichen pro Seite für "text_native"
        dpi_for_vision: int = 200,  # DPI für Vision-Analyse (200 für bessere Qualität)
        enable_vision_for_text_pages: bool = True,  # Vision auch für Text-Seiten MIT Bildern
        max_vision_pages: int = 100,  # Max Seiten für Vision-Analyse (100 = ~5-7 Min)
        enable_cache: bool = True,  # State-of-the-Art 2025: Content-Addressable Caching
        enable_metrics: bool = True  # State-of-the-Art 2025: RAGAS-Qualitätsmetriken
    ):
        """
        Args:
            model_loader: ModelLoader Singleton (optional, wird auto-initialisiert)
            text_threshold: Minimale Zeichenzahl pro Seite für "text_native" Klassifizierung
            dpi_for_vision: DPI zum Rendern der Seiten für Vision-Analyse (200 empfohlen)
            enable_vision_for_text_pages: Auch Text-Seiten mit Bildern analysieren? (Standard: True)
            max_vision_pages: Maximale Seitenanzahl für Vision-Analyse (100 = ~5-7 Minuten)
                             Setze auf 0 oder None für unbegrenzt (nicht empfohlen für große PDFs)
            enable_cache: Content-Addressable Caching aktivieren (State-of-the-Art 2025)
            enable_metrics: RAGAS-Qualitätsmetriken aktivieren (State-of-the-Art 2025)
        """
        self.text_threshold = text_threshold
        self.dpi_for_vision = dpi_for_vision
        self.enable_vision_for_text_pages = enable_vision_for_text_pages
        self.max_vision_pages = max_vision_pages
        
        # ModelLoader (lazy loading)
        self._model_loader = model_loader
        self._model_loaded = False
        
        # State-of-the-Art 2025: Caching & Metrics
        self._cache: Any = None
        self._metrics: Any = None
        self.enable_cache = enable_cache and CACHE_AVAILABLE
        self.enable_metrics = enable_metrics and METRICS_AVAILABLE
        
        logger.info(f"PDFVisionExtractor initialized (text_threshold={text_threshold}, "
                   f"dpi={dpi_for_vision}, max_vision={max_vision_pages}, "
                   f"cache={self.enable_cache}, metrics={self.enable_metrics})")
    
    @property
    def cache(self) -> Any:
        """Lazy-loads VisionCache Singleton"""
        if self._cache is None and self.enable_cache and get_vision_cache is not None:
            try:
                self._cache = get_vision_cache()
                logger.debug("✅ VisionCache aktiviert")
            except Exception as e:
                logger.warning(f"VisionCache nicht verfügbar: {e}")
                self.enable_cache = False
        return self._cache
    
    @property
    def metrics_evaluator(self) -> Any:
        """Lazy-loads MetricsEvaluator Singleton"""
        if self._metrics is None and self.enable_metrics and get_quality_evaluator is not None:
            try:
                self._metrics = get_quality_evaluator()
                logger.debug("✅ MetricsEvaluator aktiviert")
            except Exception as e:
                logger.warning(f"MetricsEvaluator nicht verfügbar: {e}")
                self.enable_metrics = False
        return self._metrics
    
    @property
    def model_loader(self):
        """Lazy-loads ModelLoader Singleton"""
        if self._model_loader is None:
            try:
                from scripts.model_loader import ModelLoader
                self._model_loader = ModelLoader()
                logger.info("✅ ModelLoader Singleton geladen")
            except Exception as e:
                logger.error(f"ModelLoader nicht verfügbar: {e}")
                raise RuntimeError(f"ModelLoader required but not available: {e}")
        return self._model_loader
    
    def _ensure_vision_model(self) -> bool:
        """Stellt sicher, dass das Vision-Modell geladen ist"""
        if self._model_loaded:
            return True
        
        try:
            ml = self.model_loader
            if ml.llm is None or not ml.is_multimodal:
                from scripts.model_loader import DEFAULT_MODEL
                logger.info(f"🔄 Lade Vision-Modell: {DEFAULT_MODEL}...")
                success = ml.load_model_by_config(DEFAULT_MODEL)
                if not success:
                    logger.error("Vision-Modell konnte nicht geladen werden")
                    return False
            
            self._model_loaded = True
            logger.info("✅ Vision-Modell bereit")
            return True
            
        except Exception as e:
            logger.error(f"Fehler beim Laden des Vision-Modells: {e}")
            return False
    
    def detect_pdf_type(self, pdf_path: str) -> Tuple[str, List[PDFPageAnalysis]]:
        """
        Erkennt den PDF-Typ (text_native, scanned, hybrid).
        
        Args:
            pdf_path: Pfad zur PDF-Datei
            
        Returns:
            Tuple (pdf_type, page_analyses)
        """
        try:
            import fitz  # PyMuPDF
        except ImportError:
            logger.error("PyMuPDF nicht verfügbar")
            return "unknown", []
        
        page_analyses = []
        text_pages = 0
        image_only_pages = 0
        
        try:
            with fitz.open(pdf_path) as doc:
                for page_num in range(len(doc)):
                    page = doc[page_num]
                    
                    # Text extrahieren (get_text() ohne Optionen gibt immer str zurück)
                    raw_text = page.get_text()
                    text: str = str(raw_text) if raw_text else ""
                    text_char_count = len(text.strip())
                    
                    # Bilder zählen
                    images = page.get_images(full=True)
                    image_count = len(images)
                    has_images = image_count > 0
                    
                    # Klassifizierung
                    is_text_native = text_char_count >= self.text_threshold
                    
                    if is_text_native:
                        text_pages += 1
                    elif has_images:
                        image_only_pages += 1
                    
                    # Text für Vorschau kürzen
                    text_preview = text[:500] + "..." if len(text) > 500 else text
                    
                    analysis = PDFPageAnalysis(
                        page_number=page_num + 1,
                        text_content=text_preview,
                        text_char_count=text_char_count,
                        has_images=has_images,
                        image_count=image_count,
                        is_text_native=is_text_native
                    )
                    page_analyses.append(analysis)
                
                # PDF-Typ bestimmen
                total_pages = len(doc)
                if total_pages == 0:
                    return "empty", []
                
                text_ratio = text_pages / total_pages
                
                if text_ratio >= 0.8:
                    pdf_type = "text_native"
                elif text_ratio <= 0.2:
                    pdf_type = "scanned"
                else:
                    pdf_type = "hybrid"
                
                logger.info(f"PDF-Typ erkannt: {pdf_type} "
                           f"({text_pages}/{total_pages} Text-Seiten, "
                           f"{image_only_pages} Bild-Seiten)")
                
                return pdf_type, page_analyses
                
        except Exception as e:
            logger.error(f"PDF-Typ-Erkennung fehlgeschlagen: {e}")
            return "error", []
    
    def _render_page_as_image(self, pdf_path: str, page_num: int) -> Optional[str]:
        """
        Rendert eine PDF-Seite als Bild für Vision-Analyse.
        
        Args:
            pdf_path: Pfad zur PDF
            page_num: Seitennummer (0-basiert)
            
        Returns:
            Pfad zum temporären Bild oder None bei Fehler
        """
        try:
            import fitz
            
            with fitz.open(pdf_path) as doc:
                if page_num >= len(doc):
                    return None
                
                page = doc[page_num]
                
                # Render mit konfiguriertem DPI
                mat = fitz.Matrix(self.dpi_for_vision / 72, self.dpi_for_vision / 72)
                pix = page.get_pixmap(matrix=mat)
                
                # Als PNG speichern
                temp_path = tempfile.mktemp(suffix=f'_p{page_num}.png')
                pix.save(temp_path)
                
                return temp_path
                
        except Exception as e:
            logger.error(f"Seite {page_num} rendern fehlgeschlagen: {e}")
            return None
    
    def _analyze_page_with_vision(
        self,
        image_path: str,
        page_num: int,
        has_text: bool = True,
        existing_text: str = ""
    ) -> str:
        """
        Analysiert eine Seite mit dem Vision-LLM.
        
        Args:
            image_path: Pfad zum gerenderten Bild
            page_num: Seitennummer
            has_text: Ob bereits Text extrahiert wurde
            existing_text: Bereits extrahierter Text (zur Kontext-Ergänzung)
            
        Returns:
            Strukturierte Analyse als Text
        """
        if not self._ensure_vision_model():
            return ""
        
        try:
            # Bild als Base64 kodieren
            with open(image_path, 'rb') as img_file:
                img_data = base64.b64encode(img_file.read()).decode('utf-8')
            
            data_url = f"data:image/png;base64,{img_data}"
            
            # Kontext-abhängiger Prompt - optimiert für alle visuellen Elemente
            if has_text and existing_text:
                prompt = f"""Analysiere diese PDF-Seite {page_num}. 
                
Der bereits extrahierte Text lautet:
"{existing_text[:500]}..."

Deine Aufgabe - beschreibe ALLE visuellen Elemente detailliert:

1. **Diagramme & Schaubilder**: 
   - Was zeigt das Diagramm? (z.B. Zusammenhänge, Prozesse, Hierarchien)
   - Welche Elemente/Symbole sind zu sehen? (Kreise, Pfeile, Boxen, Linien)
   - Welche Beschriftungen/Labels sind vorhanden?
   - Welche Farben werden verwendet und was bedeuten sie?

2. **Tabellen & Listen**:
   - Alle Zeilen, Spalten, Werte exakt wiedergeben
   - Überschriften und Kategorien

3. **Konzeptuelle Visualisierungen**:
   - Flowcharts, Mindmaps, Beziehungsdiagramme
   - Symbole und ihre Bedeutung
   - Verbindungen zwischen Elementen

4. **Bildunterschriften & Quellenangaben**:
   - Abbildungsnummern (z.B. Abb. 4.1)
   - Copyright-Hinweise
   - Beschreibende Texte unter/über Bildern

5. **Fotos & Illustrationen**:
   - Was ist auf dem Bild zu sehen?
   - Relevante Details für den Kontext

Format: Strukturierter Markdown. Sei VOLLSTÄNDIG und DETAILLIERT.
Wenn ein Element vorhanden ist, beschreibe es - auch wenn es keine Zahlen enthält."""
            else:
                prompt = """Extrahiere ALLE Informationen aus diesem Bild einer PDF-Seite:

1. **Text**: Alle sichtbaren Texte, Überschriften, Beschriftungen
2. **Diagramme**: Struktur, Elemente, Verbindungen, Beschriftungen, Farben
3. **Tabellen**: Alle Zeilen und Spalten mit exakten Werten
4. **Schaubilder**: Konzepte, Symbole, Beziehungen zwischen Elementen
5. **Bilder/Fotos**: Was ist zu sehen? Relevante Details
6. **Bildunterschriften**: Abbildungsnummern, Quellenangaben

Format: Strukturierter Markdown-Text.
Bei Tabellen verwende | für Spalten.
Sei präzise, vollständig und beschreibe ALLE visuellen Elemente."""
            
            # Vision Model Query - sicherstellen dass LLM geladen ist
            if not self.model_loader.llm:
                logger.error("LLM nicht geladen")
                return ""
            
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
                    max_tokens=2048,
                    temperature=0.2,  # Niedrig für konsistente Extraktion
                )
            
            if isinstance(response, dict) and 'choices' in response:
                content = response['choices'][0]['message'].get('content', '')
                return content.strip() if content else ""
            
            return ""
            
        except Exception as e:
            logger.error(f"Vision-Analyse Seite {page_num} fehlgeschlagen: {e}")
            return ""
        
        finally:
            # Temporäres Bild aufräumen
            try:
                if os.path.exists(image_path):
                    os.remove(image_path)
            except OSError:
                pass
    
    def extract_text_pymupdf4llm(self, pdf_path: str) -> Tuple[str, str]:
        """
        State-of-the-Art Text-Extraktion mit pymupdf4llm.
        
        Returns:
            Tuple (text, extraction_method)
        """
        try:
            import pymupdf4llm
            
            md_text = pymupdf4llm.to_markdown(
                pdf_path,
                table_strategy='lines',  # Beste Tabellen-Erkennung
                show_progress=False
            )
            
            if md_text and md_text.strip():
                logger.info(f"✅ pymupdf4llm: {len(md_text)} Zeichen extrahiert")
                return md_text, "pymupdf4llm"
                
        except ImportError:
            logger.warning("pymupdf4llm nicht installiert")
        except Exception as e:
            logger.warning(f"pymupdf4llm fehlgeschlagen: {e}")
        
        # Fallback zu PyMuPDF direkt
        try:
            import fitz
            texts = []
            with fitz.open(pdf_path) as doc:
                for page in doc:
                    texts.append(page.get_text() or "")
            
            combined = "\n\n".join(texts)
            if combined.strip():
                logger.info(f"✅ PyMuPDF direkt: {len(combined)} Zeichen")
                return combined, "pymupdf_direct"
                
        except Exception as e:
            logger.error(f"PyMuPDF fehlgeschlagen: {e}")
        
        return "", "failed"
    
    def extract_complete(
        self,
        pdf_path: str,
        force_vision: bool = False,
        specific_pages: Optional[List[int]] = None
    ) -> PDFAnalysisResult:
        """
        Vollständige PDF-Extraktion mit Vision-Enhancement.
        
        Diese Methode:
        1. Erkennt PDF-Typ (text_native, scanned, hybrid)
        2. Extrahiert Text via pymupdf4llm
        3. Analysiert Bild-Seiten mit Vision-LLM
        4. Kombiniert Ergebnisse optimal
        
        Args:
            pdf_path: Pfad zur PDF-Datei
            force_vision: Vision auch für Text-Seiten erzwingen
            specific_pages: Nur bestimmte Seiten analysieren (1-basiert)
            
        Returns:
            PDFAnalysisResult mit allen Daten
        """
        start_time = time.time()
        
        result = PDFAnalysisResult(
            file_path=pdf_path,
            total_pages=0,
            pdf_type="unknown"
        )
        
        if not os.path.exists(pdf_path):
            result.errors.append(f"Datei nicht gefunden: {pdf_path}")
            return result
        
        try:
            # 1. PDF-Typ erkennen
            pdf_type, page_analyses = self.detect_pdf_type(pdf_path)
            result.pdf_type = pdf_type
            result.total_pages = len(page_analyses)
            result.page_analyses = page_analyses
            
            # 2. Text extrahieren
            full_text, extraction_method = self.extract_text_pymupdf4llm(pdf_path)
            result.full_text = full_text
            result.extraction_method = extraction_method
            result.total_text_chars = len(full_text)
            
            # 3. Vision-Analyse für relevante Seiten
            vision_texts = []
            pages_to_analyze = []
            
            for analysis in page_analyses:
                # Bestimme ob Vision-Analyse nötig
                needs_vision = (
                    (not analysis.is_text_native) or  # Gescannte/Bild-Seite
                    (analysis.has_images and (force_vision or self.enable_vision_for_text_pages)) or
                    (specific_pages and analysis.page_number in specific_pages)
                )
                
                if needs_vision:
                    pages_to_analyze.append(analysis)
            
            # Limitiere auf max_vision_pages (wenn gesetzt und > 0)
            if self.max_vision_pages and self.max_vision_pages > 0:
                if len(pages_to_analyze) > self.max_vision_pages:
                    skipped = len(pages_to_analyze) - self.max_vision_pages
                    logger.warning(
                        f"⚠️ Vision-Limit erreicht: Analysiere {self.max_vision_pages} von "
                        f"{len(pages_to_analyze)} Seiten mit Bildern. "
                        f"{skipped} Seiten werden übersprungen. "
                        f"Erhöhe max_vision_pages für vollständige Analyse."
                    )
                    pages_to_analyze = pages_to_analyze[:self.max_vision_pages]
            
            # Vision-Analyse durchführen
            # Aktiviert wenn: scanned/hybrid PDF ODER force_vision ODER Seiten mit Bildern gefunden
            should_run_vision = (
                pages_to_analyze and 
                (pdf_type in ["scanned", "hybrid"] or force_vision or self.enable_vision_for_text_pages)
            )
            
            if should_run_vision:
                logger.info(f"🔍 Vision-Analyse für {len(pages_to_analyze)} Seiten...")
                
                for analysis in pages_to_analyze:
                    page_num = analysis.page_number - 1  # 0-basiert
                    
                    # Seite rendern
                    img_path = self._render_page_as_image(pdf_path, page_num)
                    if not img_path:
                        continue
                    
                    # Vision-Analyse
                    vision_text = self._analyze_page_with_vision(
                        img_path,
                        analysis.page_number,
                        has_text=analysis.is_text_native,
                        existing_text=analysis.text_content
                    )
                    
                    if vision_text:
                        analysis.vision_analysis = vision_text
                        vision_texts.append(f"\n### Seite {analysis.page_number} (Vision-Analyse)\n{vision_text}")
                        result.vision_analyzed_pages += 1
                        
                        # Infografiken/Tabellen zählen
                        if "tabelle" in vision_text.lower() or "|" in vision_text:
                            analysis.tables_detected += 1
                            result.table_descriptions.append(vision_text)
                        if any(kw in vision_text.lower() for kw in ["diagramm", "chart", "grafik", "statistik"]):
                            analysis.infographics_detected += 1
                            result.infographic_descriptions.append(vision_text)
                    
                    # Zähle Bilder
                    result.total_images += analysis.image_count
            
            # 4. Ergebnisse kombinieren
            if vision_texts:
                result.vision_enhanced_text = f"{full_text}\n\n---\n## Vision-LLM Analyse\n" + "\n".join(vision_texts)
            else:
                result.vision_enhanced_text = full_text
            
            result.processing_time = time.time() - start_time
            
            logger.info(f"✅ PDF-Extraktion abgeschlossen: {result.total_pages} Seiten, "
                       f"{result.total_text_chars} Zeichen, "
                       f"{result.vision_analyzed_pages} Vision-Seiten, "
                       f"{result.processing_time:.2f}s")
            
            return result
            
        except Exception as e:
            logger.error(f"PDF-Extraktion fehlgeschlagen: {e}", exc_info=True)
            result.errors.append(str(e))
            result.processing_time = time.time() - start_time
            return result
    
    def extract_infographics_only(
        self,
        pdf_path: str,
        pages: Optional[List[int]] = None
    ) -> List[Dict[str, Any]]:
        """
        Extrahiert NUR Infografiken/Charts aus einer PDF.
        
        Optimiert für schnelle Analyse von Statistik-PDFs.
        
        Args:
            pdf_path: Pfad zur PDF
            pages: Spezifische Seiten (1-basiert), None = alle Bild-Seiten
            
        Returns:
            Liste von Infografik-Beschreibungen
        """
        infographics = []
        
        pdf_type, page_analyses = self.detect_pdf_type(pdf_path)
        
        # Nur Seiten mit Bildern analysieren
        pages_with_images = [
            a for a in page_analyses 
            if a.has_images and (pages is None or a.page_number in pages)
        ]
        
        if not pages_with_images:
            logger.info("Keine Seiten mit Bildern gefunden")
            return []
        
        logger.info(f"🔍 Analysiere {len(pages_with_images)} Seiten mit Bildern...")
        
        for analysis in pages_with_images[:self.max_vision_pages]:
            page_num = analysis.page_number - 1
            
            img_path = self._render_page_as_image(pdf_path, page_num)
            if not img_path:
                continue
            
            # Spezieller Prompt für Infografiken
            vision_text = self._analyze_infographic(img_path, analysis.page_number)
            
            if vision_text:
                infographics.append({
                    "page": analysis.page_number,
                    "description": vision_text,
                    "image_count": analysis.image_count
                })
        
        return infographics
    
    def _analyze_infographic(self, image_path: str, page_num: int) -> str:
        """Spezialisierte Infografik-Analyse"""
        if not self._ensure_vision_model():
            return ""
        
        try:
            with open(image_path, 'rb') as img_file:
                img_data = base64.b64encode(img_file.read()).decode('utf-8')
            
            data_url = f"data:image/png;base64,{img_data}"
            
            prompt = """Analysiere diese Infografik/Diagramm/Chart präzise:

1. TYP: Was für eine Visualisierung ist das? (Balkendiagramm, Kreisdiagramm, Liniendiagramm, Tabelle, Flowchart, etc.)

2. TITEL/ÜBERSCHRIFT: Falls vorhanden

3. ALLE DATENPUNKTE:
   - Bei Diagrammen: Alle Achsenbeschriftungen und Werte
   - Bei Tabellen: Alle Zeilen und Spalten
   - Bei Prozentangaben: Alle % mit Beschriftung
   
4. TRENDS/AUSSAGEN: Was zeigt die Grafik?

Format als strukturierten Markdown-Text.
Sei VOLLSTÄNDIG bei allen Zahlen und Beschriftungen."""
            
            # Vision Model Query - sicherstellen dass LLM geladen ist
            if not self.model_loader.llm:
                logger.error("LLM nicht geladen")
                return ""
            
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
                    max_tokens=1500,
                    temperature=0.1,
                )
            
            if isinstance(response, dict) and 'choices' in response:
                content = response['choices'][0]['message'].get('content', '')
                return content.strip() if content else ""
            
            return ""
            
        except Exception as e:
            logger.error(f"Infografik-Analyse fehlgeschlagen: {e}")
            return ""
        finally:
            try:
                if os.path.exists(image_path):
                    os.remove(image_path)
            except OSError:
                pass


# Convenience-Funktion
def extract_pdf_with_vision(
    pdf_path: str,
    force_vision: bool = False
) -> Dict[str, Any]:
    """
    Convenience-Funktion für State-of-the-Art PDF-Extraktion.
    
    Args:
        pdf_path: Pfad zur PDF
        force_vision: Vision für alle Seiten erzwingen
        
    Returns:
        Dict mit 'text', 'vision_text', 'infographics', 'tables', etc.
    """
    extractor = PDFVisionExtractor()
    result = extractor.extract_complete(pdf_path, force_vision=force_vision)
    
    return {
        "success": len(result.errors) == 0,
        "pdf_type": result.pdf_type,
        "text": result.full_text,
        "vision_enhanced_text": result.vision_enhanced_text,
        "infographics": result.infographic_descriptions,
        "tables": result.table_descriptions,
        "total_pages": result.total_pages,
        "vision_analyzed_pages": result.vision_analyzed_pages,
        "processing_time": result.processing_time,
        "errors": result.errors
    }
