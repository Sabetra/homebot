"""
Web Vision Extractor - Vision-LLM für Webseiten-Bilder
======================================================

Analysiert Bilder auf Webseiten (Infografiken, Charts, Diagramme) mit dem Vision-Modell des geladenen LLMs.

Features:
- Erkennt relevante Bilder auf Webseiten (filtert Logos, Icons, Werbung)
- Lädt Bilder herunter und analysiert sie mit Vision-LLM
- Extrahiert strukturierte Beschreibungen
- Kombiniert Text + Bild-Analysen für vollständiges RAG
- NEU: Content-Addressable Caching (State-of-the-Art 2025)
- NEU: RAGAS-inspirierte Qualitätsmetriken
- NEU: Async Batch Processing

Verwendung:
    >>> from agent.web_vision_extractor import WebVisionExtractor
    >>> extractor = WebVisionExtractor()
    >>> result = extractor.extract_with_vision(url)
    >>> print(result['text'], result['image_descriptions'])
"""

import os
import logging
import tempfile
import base64
import re
from typing import Dict, Any, Optional, List, Tuple
from urllib.parse import urljoin, urlparse
from dataclasses import dataclass, field
import time
import threading

logger = logging.getLogger(__name__)

# Import cuda_lock for thread-safe LLM access
try:
    from scripts.model_loader import cuda_lock as _cuda_lock
except ImportError:
    _cuda_lock = threading.RLock()

# Performance & Quality Module (State-of-the-Art 2025)
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
class WebImageAnalysis:
    """Analyse eines einzelnen Web-Bildes"""
    src: str
    alt_text: str = ""
    width: int = 0
    height: int = 0
    is_relevant: bool = False  # Infografik/Chart vs. Logo/Icon
    vision_description: str = ""
    error: str = ""


@dataclass 
class WebVisionResult:
    """Gesamtergebnis der Web-Vision-Analyse"""
    url: str
    page_title: str = ""
    page_text: str = ""
    
    # Bild-Analyse
    total_images_found: int = 0
    relevant_images: int = 0
    analyzed_images: int = 0
    cached_images: int = 0  # NEU: Cache-Hits
    image_analyses: List[WebImageAnalysis] = field(default_factory=list)
    
    # Kombinierter Output
    vision_enhanced_text: str = ""  # Text + Bild-Beschreibungen
    
    # Metadaten
    processing_time: float = 0.0
    errors: List[str] = field(default_factory=list)
    
    # Quality Metrics (NEU: RAGAS-inspiriert)
    quality_score: float = 0.0
    faithfulness: float = 0.0
    context_precision: float = 0.0
    multimodal_coherence: float = 0.0


class WebVisionExtractor:
    """
    Extrahiert und analysiert Bilder von Webseiten mit Vision-LLM.
    
    Filtert automatisch irrelevante Bilder (Logos, Icons, Werbung) und
    konzentriert sich auf informative Visualisierungen.
    """
    
    # Minimale Bildgröße für Relevanz (filtert kleine Icons/Logos)
    MIN_IMAGE_WIDTH = 200
    MIN_IMAGE_HEIGHT = 150
    MIN_IMAGE_AREA = 40000  # 200x200 Pixel
    
    # Maximale Anzahl Bilder pro Seite (Performance)
    MAX_IMAGES_PER_PAGE = 15
    
    # URL-Patterns für irrelevante Bilder
    IRRELEVANT_PATTERNS = [
        r'logo', r'icon', r'avatar', r'favicon', r'sprite',
        r'button', r'banner', r'ad[s]?[-_]', r'tracking',
        r'pixel', r'spacer', r'blank', r'placeholder',
        r'social[-_]?media', r'share', r'twitter', r'facebook',
        r'linkedin', r'instagram', r'youtube', r'pinterest',
        r'emoji', r'smiley', r'arrow', r'bullet'
    ]
    
    # Alt-Text-Patterns die auf relevante Bilder hinweisen
    RELEVANT_ALT_PATTERNS = [
        r'chart', r'graph', r'diagram', r'infographic',
        r'statistic', r'data', r'figure', r'table',
        r'visuali[sz]ation', r'overview', r'summary',
        r'prozent', r'percent', r'studie', r'study',
        r'ergebnis', r'result', r'analyse', r'analysis'
    ]
    
    def __init__(
        self,
        model_loader=None,
        min_width: int = 200,
        min_height: int = 150,
        max_images: int = 15,
        timeout: int = 10,
        enable_cache: bool = True,
        enable_metrics: bool = True
    ):
        """
        Args:
            model_loader: ModelLoader Singleton (optional, wird auto-initialisiert)
            min_width: Minimale Bildbreite für Relevanz
            min_height: Minimale Bildhöhe für Relevanz
            max_images: Maximale Anzahl zu analysierender Bilder
            timeout: HTTP-Timeout für Bild-Downloads
            enable_cache: Content-Addressable Caching aktivieren (State-of-the-Art 2025)
            enable_metrics: RAGAS-Qualitätsmetriken aktivieren (State-of-the-Art 2025)
        """
        self.MIN_IMAGE_WIDTH = min_width
        self.MIN_IMAGE_HEIGHT = min_height
        self.MAX_IMAGES_PER_PAGE = max_images
        self.timeout = timeout
        
        # ModelLoader (lazy loading)
        self._model_loader = model_loader
        self._model_loaded = False
        
        # State-of-the-Art 2025: Caching & Metrics
        self._cache: Any = None
        self._metrics: Any = None
        self.enable_cache = enable_cache and CACHE_AVAILABLE
        self.enable_metrics = enable_metrics and METRICS_AVAILABLE
        
        # Compiled Regex für Performance
        self._irrelevant_pattern = re.compile(
            '|'.join(self.IRRELEVANT_PATTERNS), 
            re.IGNORECASE
        )
        self._relevant_pattern = re.compile(
            '|'.join(self.RELEVANT_ALT_PATTERNS),
            re.IGNORECASE
        )
        
        logger.info(f"WebVisionExtractor initialized (min_size={min_width}x{min_height}, max_images={max_images}, cache={self.enable_cache}, metrics={self.enable_metrics})")
    
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
                return None
        return self._model_loader
    
    def _ensure_vision_model(self) -> bool:
        """Stellt sicher, dass das Vision-Modell geladen ist"""
        if self._model_loaded:
            return True
        
        try:
            loader = self.model_loader
            if loader is None:
                return False
            
            # Prüfe ob multimodal
            if not getattr(loader, 'is_multimodal', False):
                logger.warning("Model ist nicht multimodal - Vision nicht verfügbar")
                return False
            
            # Model laden wenn nötig (ModelLoader verwendet intern das konfigurierte Modell)
            if loader.llm is None:
                # ModelLoader.load_model() ohne Argumente nutzt den Standard-Pfad
                if hasattr(loader, 'load_model'):
                    loader.load_model()  # type: ignore
            
            self._model_loaded = True
            return True
            
        except Exception as e:
            logger.error(f"Vision-Model laden fehlgeschlagen: {e}")
            return False
    
    def _is_relevant_image(self, src: str, alt: str, width: int, height: int) -> Tuple[bool, str]:
        """
        Prüft ob ein Bild relevant ist (Infografik/Chart vs. Logo/Icon).
        
        Returns:
            Tuple (is_relevant, reason)
        """
        # Größen-Check
        if width > 0 and width < self.MIN_IMAGE_WIDTH:
            return False, f"zu klein (width={width})"
        if height > 0 and height < self.MIN_IMAGE_HEIGHT:
            return False, f"zu klein (height={height})"
        if width > 0 and height > 0 and (width * height) < self.MIN_IMAGE_AREA:
            return False, f"Fläche zu klein ({width}x{height})"
        
        # URL-Pattern-Check (irrelevant)
        if self._irrelevant_pattern.search(src):
            return False, "irrelevantes URL-Pattern"
        
        # Alt-Text-Check (relevant)
        if alt and self._relevant_pattern.search(alt):
            return True, "relevanter Alt-Text"
        
        # Dateiendung-Check
        src_lower = src.lower()
        if any(ext in src_lower for ext in ['.svg', '.gif']):
            # SVGs und GIFs sind oft Icons/Animationen
            if not (alt and len(alt) > 30):  # Außer sie haben langen Alt-Text
                return False, "SVG/GIF ohne beschreibenden Alt-Text"
        
        # Default: Wenn groß genug, als relevant betrachten
        if width >= 300 or height >= 200:
            return True, "ausreichend groß"
        
        # Unbekannte Größe aber kein irrelevantes Pattern
        if width == 0 and height == 0:
            return True, "Größe unbekannt, wird geprüft"
        
        return False, "nicht relevant genug"
    
    def _download_image(self, url: str, base_url: str) -> Optional[str]:
        """
        Lädt ein Bild herunter und speichert es temporär.
        
        Returns:
            Pfad zur temporären Datei oder None bei Fehler
        """
        try:
            import requests
            
            # Relative URLs auflösen
            if not url.startswith(('http://', 'https://', 'data:')):
                url = urljoin(base_url, url)
            
            # Data-URLs direkt verarbeiten
            if url.startswith('data:'):
                return self._process_data_url(url)
            
            # HTTP-Download
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            }
            
            response = requests.get(url, headers=headers, timeout=self.timeout)
            response.raise_for_status()
            
            # Content-Type prüfen
            content_type = response.headers.get('Content-Type', '').lower()
            if not any(img_type in content_type for img_type in ['image/', 'application/octet-stream']):
                logger.debug(f"Kein Bild: {content_type}")
                return None
            
            # Größe prüfen (max 10 MB)
            if len(response.content) > 10 * 1024 * 1024:
                logger.warning(f"Bild zu groß: {len(response.content) / (1024*1024):.1f} MB")
                return None
            
            # Extension bestimmen
            ext = '.png'
            if 'jpeg' in content_type or 'jpg' in content_type:
                ext = '.jpg'
            elif 'gif' in content_type:
                ext = '.gif'
            elif 'webp' in content_type:
                ext = '.webp'
            
            # Temporär speichern
            temp_path = tempfile.mktemp(suffix=ext)
            with open(temp_path, 'wb') as f:
                f.write(response.content)
            
            return temp_path
            
        except Exception as e:
            logger.debug(f"Bild-Download fehlgeschlagen für {url}: {e}")
            return None
    
    def _process_data_url(self, data_url: str) -> Optional[str]:
        """Verarbeitet Data-URLs (base64-kodierte Bilder)"""
        try:
            # Format: data:image/png;base64,XXXXXX
            if ';base64,' not in data_url:
                return None
            
            header, data = data_url.split(';base64,', 1)
            
            # Extension bestimmen
            ext = '.png'
            if 'jpeg' in header or 'jpg' in header:
                ext = '.jpg'
            elif 'gif' in header:
                ext = '.gif'
            
            # Dekodieren und speichern
            img_data = base64.b64decode(data)
            
            temp_path = tempfile.mktemp(suffix=ext)
            with open(temp_path, 'wb') as f:
                f.write(img_data)
            
            return temp_path
            
        except Exception as e:
            logger.debug(f"Data-URL Verarbeitung fehlgeschlagen: {e}")
            return None
    
    def _analyze_image_with_vision(self, image_path: str, context: str = "") -> Tuple[str, bool]:
        """
        Analysiert ein Bild mit dem Vision-LLM.
        
        Args:
            image_path: Pfad zum Bild
            context: Kontext von der Webseite (Titel, umgebender Text)
            
        Returns:
            Tuple (beschreibung, from_cache)
        """
        # State-of-the-Art 2025: Cache-Check zuerst
        if self.cache:
            cached = self.cache.get_by_image(image_path)
            if cached:
                logger.debug(f"Cache HIT für {image_path}")
                return cached.get('description', ''), True
        
        if not self._ensure_vision_model():
            return "", False
        
        try:
            # Bild als Base64 kodieren
            with open(image_path, 'rb') as img_file:
                img_data = base64.b64encode(img_file.read()).decode('utf-8')
            
            # MIME-Type bestimmen
            ext = os.path.splitext(image_path)[1].lower()
            mime_type = {
                '.jpg': 'image/jpeg',
                '.jpeg': 'image/jpeg',
                '.png': 'image/png',
                '.gif': 'image/gif',
                '.webp': 'image/webp'
            }.get(ext, 'image/png')
            
            data_url = f"data:{mime_type};base64,{img_data}"
            
            # Prompt für Web-Bilder
            prompt = f"""Analysiere dieses Bild von einer Webseite.
{f'Kontext: {context}' if context else ''}

Beschreibe detailliert:

1. **Typ**: Was für ein Bild ist das? (Infografik, Diagramm, Chart, Foto, Screenshot, etc.)

2. **Inhalt**: Was zeigt das Bild?
   - Bei Diagrammen: Struktur, Elemente, Beschriftungen, Farben
   - Bei Charts: Achsen, Werte, Trends
   - Bei Infografiken: Alle Zahlen, Prozente, Fakten
   - Bei Fotos: Relevante Details

3. **Kernaussage**: Was ist die Hauptbotschaft?

4. **Daten**: Alle sichtbaren Zahlen, Statistiken, Beschriftungen

Format: Strukturierter Markdown-Text. Sei präzise und vollständig."""
            
            # Vision Model Query
            loader = self.model_loader
            if not loader or not loader.llm:
                logger.error("LLM nicht geladen")
                return "", False
            
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
                    temperature=0.2,
                )
            
            description = ""
            if isinstance(response, dict) and 'choices' in response:
                content = response['choices'][0]['message'].get('content', '')
                description = content.strip() if content else ""
            
            # State-of-the-Art 2025: Ergebnis cachen
            if self.cache and description:
                self.cache.put_image_result(
                    image_path,
                    {
                        'description': description,
                        'context': context,
                        'timestamp': time.time()
                    },
                    metadata={'source': 'web_vision'}
                )
            
            return description, False
            
        except Exception as e:
            logger.error(f"Vision-Analyse fehlgeschlagen: {e}")
            return "", False
        
        finally:
            # Temporäres Bild aufräumen
            try:
                if os.path.exists(image_path):
                    os.remove(image_path)
            except OSError:
                pass
    
    def extract_images_from_html(self, html: str, base_url: str) -> List[WebImageAnalysis]:
        """
        Extrahiert relevante Bilder aus HTML.
        
        Returns:
            Liste von WebImageAnalysis Objekten
        """
        images = []
        
        try:
            from bs4 import BeautifulSoup
            
            soup = BeautifulSoup(html, 'html.parser')
            
            # Alle <img> Tags finden
            img_tags = soup.find_all('img')
            
            for img in img_tags:
                src = img.get('src', '') or img.get('data-src', '') or img.get('data-lazy-src', '')
                if not src:
                    continue
                
                # Sicherstellen dass src ein String ist
                src = str(src) if src else ""
                
                alt = img.get('alt', '') or img.get('title', '')
                alt = str(alt) if alt else ""
                
                # Größe extrahieren
                width = 0
                height = 0
                try:
                    width_attr = img.get('width')
                    height_attr = img.get('height')
                    width = int(str(width_attr)) if width_attr else 0
                    height = int(str(height_attr)) if height_attr else 0
                except (ValueError, TypeError):
                    pass
                
                # Relevanz prüfen
                is_relevant, reason = self._is_relevant_image(src, alt, width, height)
                
                analysis = WebImageAnalysis(
                    src=src,
                    alt_text=alt,
                    width=width,
                    height=height,
                    is_relevant=is_relevant
                )
                
                if is_relevant:
                    images.append(analysis)
                    logger.debug(f"Relevantes Bild gefunden: {src[:50]}... ({reason})")
                else:
                    logger.debug(f"Bild übersprungen: {src[:50]}... ({reason})")
            
            # Auch <figure> mit <img> prüfen
            for figure in soup.find_all('figure'):
                img_tag = figure.find('img')
                if img_tag and img_tag.get('src'):
                    figcaption = figure.find('figcaption')
                    caption_text = figcaption.get_text(strip=True) if figcaption else ""
                    
                    # Figure-Bilder sind oft relevanter
                    fig_src = str(img_tag.get('src', ''))
                    fig_alt = str(img_tag.get('alt', ''))
                    if fig_src and not any(a.src == fig_src for a in images):
                        analysis = WebImageAnalysis(
                            src=fig_src,
                            alt_text=caption_text or fig_alt,
                            is_relevant=True  # Figure-Bilder sind meist relevant
                        )
                        images.append(analysis)
            
            del soup
            
        except ImportError:
            logger.warning("BeautifulSoup nicht verfügbar für HTML-Parsing")
        except Exception as e:
            logger.error(f"HTML-Parsing fehlgeschlagen: {e}")
        
        return images[:self.MAX_IMAGES_PER_PAGE]
    
    def extract_with_vision(
        self,
        url: str,
        html: Optional[str] = None,
        page_text: Optional[str] = None,
        page_title: Optional[str] = None
    ) -> WebVisionResult:
        """
        Vollständige Web-Extraktion mit Vision-Enhancement.
        
        Diese Methode:
        1. Extrahiert relevante Bilder aus HTML
        2. Analysiert sie mit Vision-LLM
        3. Kombiniert Text + Bild-Beschreibungen
        
        Args:
            url: URL der Webseite
            html: Optional: bereits geladenes HTML
            page_text: Optional: bereits extrahierter Text
            page_title: Optional: Seitentitel
            
        Returns:
            WebVisionResult mit allen Daten
        """
        start_time = time.time()
        
        result = WebVisionResult(
            url=url,
            page_title=page_title or "",
            page_text=page_text or ""
        )
        
        try:
            import requests
            from bs4 import BeautifulSoup
            
            # HTML laden wenn nicht übergeben
            if not html:
                headers = {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
                }
                response = requests.get(url, headers=headers, timeout=self.timeout)
                response.raise_for_status()
                html = response.text
            
            # Titel extrahieren wenn nicht übergeben
            if not page_title:
                soup = BeautifulSoup(html, 'html.parser')
                title_tag = soup.find('title')
                result.page_title = title_tag.get_text(strip=True) if title_tag else ""
                del soup
            
            # Bilder extrahieren
            images = self.extract_images_from_html(html, url)
            result.total_images_found = len(images)
            result.relevant_images = sum(1 for img in images if img.is_relevant)
            
            logger.info(f"🖼️ {result.total_images_found} Bilder gefunden, {result.relevant_images} relevant")
            
            if not images:
                result.processing_time = time.time() - start_time
                return result
            
            # Vision-Analyse für relevante Bilder
            context = f"Seite: {result.page_title}" if result.page_title else ""
            analyzed_count = 0
            cached_count = 0
            image_descriptions = []
            
            for img_analysis in images:
                if not img_analysis.is_relevant:
                    continue
                
                # Bild herunterladen
                img_path = self._download_image(img_analysis.src, url)
                if not img_path:
                    img_analysis.error = "Download fehlgeschlagen"
                    continue
                
                # Mit Vision analysieren (mit Cache-Support)
                description, from_cache = self._analyze_image_with_vision(img_path, context)
                
                if description:
                    img_analysis.vision_description = description
                    analyzed_count += 1
                    if from_cache:
                        cached_count += 1
                    
                    # Für kombinierten Output
                    cache_tag = " [cached]" if from_cache else ""
                    image_descriptions.append(
                        f"\n\n### 🖼️ Bild-Analyse {analyzed_count}{cache_tag}\n"
                        f"**Quelle**: {img_analysis.src[:80]}...\n"
                        f"**Alt-Text**: {img_analysis.alt_text}\n\n"
                        f"{description}"
                    )
                    
                    logger.info(f"✅ Bild {analyzed_count} analysiert: {len(description)} Zeichen{cache_tag}")
                else:
                    img_analysis.error = "Vision-Analyse fehlgeschlagen"
            
            result.analyzed_images = analyzed_count
            result.cached_images = cached_count
            result.image_analyses = images
            
            # Kombinierten Text erstellen
            if image_descriptions:
                result.vision_enhanced_text = (
                    result.page_text + 
                    "\n\n---\n## 🖼️ Bild-Analysen von dieser Seite\n" +
                    "\n".join(image_descriptions)
                )
            else:
                result.vision_enhanced_text = result.page_text
            
        except ImportError as e:
            result.errors.append(f"Fehlende Abhängigkeit: {e}")
            logger.error(f"Import-Fehler: {e}")
        except Exception as e:
            result.errors.append(str(e))
            logger.error(f"Web-Vision-Extraktion fehlgeschlagen: {e}")
        
        result.processing_time = time.time() - start_time
        
        # State-of-the-Art 2025: Qualitätsmetriken erfassen
        if self.metrics_evaluator and result.analyzed_images > 0:
            try:
                metrics = self.metrics_evaluator.evaluate_url_extraction(
                    url=url,
                    extracted_text=result.vision_enhanced_text,
                    image_descriptions=[
                        img.vision_description 
                        for img in result.image_analyses 
                        if img.vision_description
                    ],
                    processing_time_ms=result.processing_time * 1000
                )
                result.quality_score = metrics.overall_quality
                result.faithfulness = metrics.faithfulness
                result.context_precision = metrics.context_precision
                result.multimodal_coherence = metrics.multimodal_coherence
                
                logger.info(f"📊 Qualitätsmetriken: Score={result.quality_score:.2f}, "
                           f"Coherence={result.multimodal_coherence:.2f}")
            except Exception as e:
                logger.warning(f"Metriken-Erfassung fehlgeschlagen: {e}")
        
        logger.info(f"Web-Vision abgeschlossen in {result.processing_time:.1f}s: "
                   f"{result.analyzed_images}/{result.relevant_images} Bilder analysiert "
                   f"({result.cached_images} aus Cache)")
        
        return result


def extract_web_with_vision(url: str, **kwargs) -> Dict[str, Any]:
    """
    Convenience-Funktion für Web-Vision-Extraktion.
    
    Args:
        url: URL der Webseite
        **kwargs: Weitere Parameter für WebVisionExtractor
        
    Returns:
        Dict mit text, images, metadata
    """
    extractor = WebVisionExtractor(**kwargs)
    result = extractor.extract_with_vision(url)
    
    return {
        "url": result.url,
        "title": result.page_title,
        "text": result.vision_enhanced_text or result.page_text,
        "images_found": result.total_images_found,
        "images_analyzed": result.analyzed_images,
        "image_descriptions": [
            {
                "src": img.src,
                "alt": img.alt_text,
                "description": img.vision_description
            }
            for img in result.image_analyses
            if img.vision_description
        ],
        "processing_time": result.processing_time,
        "errors": result.errors
    }


# Test
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    
    # Test mit einer Beispiel-URL
    test_url = "https://de.wikipedia.org/wiki/Psychologie"
    
    print(f"Testing WebVisionExtractor with: {test_url}")
    result = extract_web_with_vision(test_url)
    
    print(f"\nResults:")
    print(f"  Title: {result['title']}")
    print(f"  Images found: {result['images_found']}")
    print(f"  Images analyzed: {result['images_analyzed']}")
    print(f"  Processing time: {result['processing_time']:.1f}s")
    
    if result['image_descriptions']:
        print(f"\nImage descriptions:")
        for i, desc in enumerate(result['image_descriptions'][:3]):
            print(f"\n  Image {i+1}: {desc['src'][:50]}...")
            print(f"  Description: {desc['description'][:200]}...")
