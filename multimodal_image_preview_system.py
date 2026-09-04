"""
🖼️ MULTIMODALES BILDVORSCHAU-SYSTEM
================================

Quick Win Feature #1: Echtzeit-Bildvorschau mit Drag & Drop Enhancement
⭐⭐⭐⭐⭐⭐⭐ 34/35 Punkte - Sofortiger UX-Gewinn

Funktionen:
- Echtzeit-Bildvorschau mit Thumbnails
- Drag & Drop Interface
- Metadaten-Extraktion (EXIF, Größe, Format)
- Bildqualitäts-Assessment
- Multi-Format Support (JPG, PNG, WEBP, TIFF, BMP)
- Memory-optimierte Verarbeitung
"""

import os
import io
import json
import asyncio
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass, asdict
from datetime import datetime
import base64
import hashlib
from pathlib import Path

import streamlit as st
from PIL import Image, ImageStat, ExifTags
import cv2
import numpy as np
from concurrent.futures import ThreadPoolExecutor
import logging

# Azure Computer Vision imports (falls verfügbar)
try:
    from azure.cognitiveservices.vision.computervision import ComputerVisionClient
    from azure.cognitiveservices.vision.computervision.models import ReadOperationResult
    from msrest.authentication import CognitiveServicesCredentials
    AZURE_VISION_AVAILABLE = True
except ImportError:
    AZURE_VISION_AVAILABLE = False
    logging.warning("Azure Computer Vision SDK nicht verfügbar")

@dataclass
class ImageMetadata:
    """Umfassende Bildmetadaten für die Vorschau"""
    filename: str
    size_bytes: int
    dimensions: Tuple[int, int]  # width, height
    format: str
    color_mode: str
    has_transparency: bool
    quality_score: float  # 0-100
    brightness: float
    contrast: float
    sharpness: float
    dominant_colors: List[str]
    exif_data: Dict[str, Any]
    file_hash: str
    upload_timestamp: str
    preview_base64: str  # Thumbnail als base64

class ImagePreviewSystem:
    """
    🎯 KERNKLASSE: Bildvorschau-System
    
    Implementiert:
    - Echtzeit-Thumbnails
    - Qualitäts-Assessment  
    - Metadaten-Extraktion
    - Memory-optimierte Verarbeitung
    """
    
    def __init__(self, max_preview_size: Tuple[int, int] = (300, 300)):
        self.max_preview_size = max_preview_size
        self.supported_formats = {'.jpg', '.jpeg', '.png', '.webp', '.tiff', '.bmp', '.gif'}
        self.executor = ThreadPoolExecutor(max_workers=4)
        
        # Setup logging
        logging.basicConfig(level=logging.INFO)
        self.logger = logging.getLogger(__name__)
        
    def create_thumbnail(self, image: Image.Image) -> str:
        """
        Erstellt memory-optimiertes Thumbnail
        
        Returns:
            str: Base64-kodiertes Thumbnail
        """
        try:
            # Thumbnail erstellen (behält Seitenverhältnis bei)
            thumbnail = image.copy()
            thumbnail.thumbnail(self.max_preview_size, Image.Resampling.LANCZOS)
            
            # In Memory Buffer konvertieren
            buffer = io.BytesIO()
            
            # Format für optimale Kompression wählen
            if thumbnail.mode in ('RGBA', 'P'):
                thumbnail = thumbnail.convert('RGB')
            
            # Als JPEG mit optimaler Qualität speichern
            thumbnail.save(buffer, format='JPEG', quality=85, optimize=True)
            
            # Base64 kodieren
            thumbnail_b64 = base64.b64encode(buffer.getvalue()).decode('utf-8')
            
            return f"data:image/jpeg;base64,{thumbnail_b64}"
            
        except Exception as e:
            self.logger.error(f"Thumbnail-Erstellung fehlgeschlagen: {str(e)}")
            return ""
    
    def calculate_image_quality(self, image: Image.Image) -> Dict[str, float]:
        """
        Berechnet umfassende Bildqualitäts-Metriken
        
        Returns:
            Dict mit Qualitäts-Scores (0-100)
        """
        try:
            # Konvertiere zu RGB falls nötig
            if image.mode != 'RGB':
                rgb_image = image.convert('RGB')
            else:
                rgb_image = image
            
            # Zu NumPy Array für OpenCV
            img_array = np.array(rgb_image)
            
            # Brightness (Helligkeit)
            gray = cv2.cvtColor(img_array, cv2.COLOR_RGB2GRAY)
            gray_array = np.asarray(gray)
            brightness = float(np.mean(gray_array)) / 255.0 * 100
            
            # Contrast (Kontrast)
            contrast = float(np.std(gray_array)) / 255.0 * 100
            
            # Sharpness (Schärfe) via Laplacian Variance
            laplacian_var = cv2.Laplacian(gray, cv2.CV_64F).var()
            sharpness = min(laplacian_var / 500.0 * 100, 100)  # Normalisiert auf 0-100
            
            # Overall Quality Score (gewichteter Durchschnitt)
            quality_score = (
                sharpness * 0.4 +           # Schärfe ist wichtigster Faktor
                min(contrast, 50) * 0.3 +   # Moderate Kontrast ist gut
                (100 - abs(brightness - 50)) * 0.3  # Optimale Helligkeit ~50%
            )
            
            return {
                'quality_score': round(quality_score, 1),
                'brightness': round(brightness, 1),
                'contrast': round(contrast, 1), 
                'sharpness': round(sharpness, 1)
            }
            
        except Exception as e:
            self.logger.error(f"Qualitäts-Berechnung fehlgeschlagen: {str(e)}")
            return {
                'quality_score': 0.0,
                'brightness': 0.0,
                'contrast': 0.0,
                'sharpness': 0.0
            }
    
    def extract_dominant_colors(self, image: Image.Image, num_colors: int = 5) -> List[str]:
        """
        Extrahiert dominante Farben aus dem Bild
        
        Returns:
            List[str]: Hex-Farbcodes der dominanten Farben
        """
        try:
            # Bild verkleinern für Performance
            small_image = image.resize((150, 150))
            
            # Zu RGB konvertieren
            if small_image.mode != 'RGB':
                small_image = small_image.convert('RGB')
            
            # Zu NumPy Array
            img_array = np.array(small_image)
            img_array = img_array.reshape((-1, 3))
            
            # K-Means Clustering für dominante Farben
            import sklearn.cluster
            
            kmeans = sklearn.cluster.KMeans(n_clusters=num_colors, random_state=42, n_init=10)
            kmeans.fit(img_array)
            
            # Cluster-Zentren als Hex-Codes
            colors = []
            for center in kmeans.cluster_centers_:
                hex_color = '#{:02x}{:02x}{:02x}'.format(
                    int(center[0]), int(center[1]), int(center[2])
                )
                colors.append(hex_color)
            
            return colors
            
        except Exception as e:
            self.logger.error(f"Farbextraktion fehlgeschlagen: {str(e)}")
            return ['#000000']  # Fallback: Schwarz
    
    def extract_exif_data(self, image: Image.Image) -> Dict[str, Any]:
        """
        Extrahiert EXIF-Metadaten aus dem Bild
        
        Returns:
            Dict: Aufbereitete EXIF-Daten
        """
        exif_data = {}
        
        try:
            # EXIF-Daten auslesen
            exif = image._getexif()
            
            if exif is not None:
                for tag_id, value in exif.items():
                    tag = ExifTags.TAGS.get(tag_id, tag_id)
                    
                    # Nur relevante Tags behalten
                    relevant_tags = {
                        'DateTime', 'DateTimeOriginal', 'Make', 'Model', 
                        'Software', 'Orientation', 'XResolution', 'YResolution',
                        'Flash', 'FocalLength', 'ExposureTime', 'FNumber', 'ISO'
                    }
                    
                    if tag in relevant_tags:
                        # Werte serialisierbar machen
                        if isinstance(value, bytes):
                            try:
                                value = value.decode('utf-8', errors='ignore')
                            except:
                                value = str(value)
                        elif not isinstance(value, (str, int, float, bool)):
                            value = str(value)
                        
                        exif_data[tag] = value
                        
        except Exception as e:
            self.logger.error(f"EXIF-Extraktion fehlgeschlagen: {str(e)}")
        
        return exif_data
    
    def calculate_file_hash(self, file_content: bytes) -> str:
        """
        Berechnet SHA-256 Hash der Datei für Duplikat-Erkennung
        """
        return hashlib.sha256(file_content).hexdigest()[:16]  # Ersten 16 Zeichen
    
    async def process_image_async(self, uploaded_file) -> Optional[ImageMetadata]:
        """
        Verarbeitet hochgeladenes Bild asynchron und erstellt umfassende Metadaten
        
        Args:
            uploaded_file: Streamlit UploadedFile object
            
        Returns:
            ImageMetadata: Vollständige Bildmetadaten oder None bei Fehler
        """
        try:
            # Datei-Content lesen
            file_content = uploaded_file.read()
            uploaded_file.seek(0)  # Reset für weitere Verarbeitung
            
            # File Hash für Duplikat-Erkennung
            file_hash = self.calculate_file_hash(file_content)
            
            # Bild laden
            image = Image.open(io.BytesIO(file_content))
            
            # Basis-Informationen
            file_size = len(file_content)
            dimensions = image.size  # (width, height)
            format_name = image.format or 'Unknown'
            color_mode = image.mode
            has_transparency = 'A' in color_mode or 'transparency' in image.info
            
            # Thumbnail erstellen
            thumbnail_b64 = self.create_thumbnail(image)
            
            # Qualitäts-Assessment
            quality_metrics = self.calculate_image_quality(image)
            
            # Dominante Farben
            dominant_colors = self.extract_dominant_colors(image)
            
            # EXIF-Daten
            exif_data = self.extract_exif_data(image)
            
            # Metadaten-Objekt erstellen
            metadata = ImageMetadata(
                filename=uploaded_file.name,
                size_bytes=file_size,
                dimensions=dimensions,
                format=format_name,
                color_mode=color_mode,
                has_transparency=has_transparency,
                quality_score=quality_metrics['quality_score'],
                brightness=quality_metrics['brightness'],
                contrast=quality_metrics['contrast'],
                sharpness=quality_metrics['sharpness'],
                dominant_colors=dominant_colors,
                exif_data=exif_data,
                file_hash=file_hash,
                upload_timestamp=datetime.now().isoformat(),
                preview_base64=thumbnail_b64
            )
            
            self.logger.info(f"Bild erfolgreich verarbeitet: {uploaded_file.name}")
            return metadata
            
        except Exception as e:
            self.logger.error(f"Bildverarbeitung fehlgeschlagen für {uploaded_file.name}: {str(e)}")
            return None
    
    def format_file_size(self, size_bytes: int) -> str:
        """Formatiert Dateigröße human-readable"""
        for unit in ['B', 'KB', 'MB', 'GB']:
            if size_bytes < 1024.0:
                return f"{size_bytes:.1f} {unit}"
            size_bytes /= 1024.0
        return f"{size_bytes:.1f} TB"
    
    def render_image_preview_card(self, metadata: ImageMetadata):
        """
        Rendert eine schöne Vorschau-Karte für das Bild
        """
        with st.container():
            col1, col2 = st.columns([1, 2])
            
            with col1:
                # Thumbnail anzeigen
                if metadata.preview_base64:
                    st.markdown(
                        f'<img src="{metadata.preview_base64}" style="width: 100%; border-radius: 8px; box-shadow: 0 2px 8px rgba(0,0,0,0.1);">',
                        unsafe_allow_html=True
                    )
            
            with col2:
                # Datei-Informationen
                st.markdown(f"**📁 {metadata.filename}**")
                st.markdown(f"📏 **{metadata.dimensions[0]} × {metadata.dimensions[1]}** Pixel")
                st.markdown(f"💾 **{self.format_file_size(metadata.size_bytes)}** ({metadata.format})")
                
                # Qualitäts-Indikator
                quality = metadata.quality_score
                if quality >= 80:
                    quality_color = "🟢"
                    quality_text = "Ausgezeichnet"
                elif quality >= 60:
                    quality_color = "🟡"
                    quality_text = "Gut"
                else:
                    quality_color = "🔴"
                    quality_text = "Verbesserungswürdig"
                
                st.markdown(f"⭐ **Qualität:** {quality_color} {quality:.1f}/100 ({quality_text})")
                
                # Dominante Farben
                if metadata.dominant_colors:
                    colors_html = ""
                    for color in metadata.dominant_colors[:3]:  # Top 3 Farben
                        colors_html += f'<span style="display: inline-block; width: 20px; height: 20px; background-color: {color}; border-radius: 50%; margin-right: 5px; border: 1px solid #ddd;"></span>'
                    
                    st.markdown(f"🎨 **Dominante Farben:** {colors_html}", unsafe_allow_html=True)

# Streamlit GUI Integration
def create_image_preview_interface():
    """
    Erstellt die Streamlit-Benutzeroberfläche für die Bildvorschau
    """
    st.title("🖼️ Multimodales Bildvorschau-System")
    st.markdown("**Quick Win Feature #1** - Echtzeit-Bildvorschau mit erweiterten Metadaten")
    
    # Initialisiere das Preview-System
    if 'preview_system' not in st.session_state:
        st.session_state.preview_system = ImagePreviewSystem()
    
    # File Uploader mit Drag & Drop
    uploaded_files = st.file_uploader(
        "🎯 Bilder hochladen",
        type=['jpg', 'jpeg', 'png', 'webp', 'tiff', 'bmp', 'gif'],
        accept_multiple_files=True,
        help="Drag & Drop oder klicken Sie hier um Bilder hochzuladen"
    )
    
    if uploaded_files:
        st.markdown("---")
        st.subheader("📊 Bildanalyse-Ergebnisse")
        
        # Progress Bar für Batch-Verarbeitung
        progress_bar = st.progress(0)
        status_text = st.empty()
        
        # Verarbeite jedes Bild
        for i, uploaded_file in enumerate(uploaded_files):
            status_text.text(f"Verarbeite {uploaded_file.name}...")
            
            # Asynchrone Verarbeitung (in Streamlit simuliert)
            metadata = asyncio.run(st.session_state.preview_system.process_image_async(uploaded_file))
            
            if metadata:
                # Render Preview Card
                st.session_state.preview_system.render_image_preview_card(metadata)
                
                # Expander für detaillierte Metadaten
                with st.expander(f"🔍 Detaillierte Analyse - {metadata.filename}"):
                    col1, col2 = st.columns(2)
                    
                    with col1:
                        st.json({
                            "Technische Details": {
                                "Farbmodus": metadata.color_mode,
                                "Transparenz": metadata.has_transparency,
                                "Datei-Hash": metadata.file_hash,
                                "Upload-Zeit": metadata.upload_timestamp
                            },
                            "Qualitäts-Metriken": {
                                "Helligkeit": f"{metadata.brightness:.1f}/100",
                                "Kontrast": f"{metadata.contrast:.1f}/100", 
                                "Schärfe": f"{metadata.sharpness:.1f}/100"
                            }
                        })
                    
                    with col2:
                        if metadata.exif_data:
                            st.markdown("**📷 EXIF-Daten:**")
                            st.json(metadata.exif_data)
                        else:
                            st.info("Keine EXIF-Daten verfügbar")
                
                st.markdown("---")
            
            # Progress aktualisieren
            progress_bar.progress((i + 1) / len(uploaded_files))
        
        status_text.text("✅ Alle Bilder erfolgreich verarbeitet!")
        
        # Download-Button für Metadaten
        if uploaded_files:
            all_metadata = []
            for uploaded_file in uploaded_files:
                uploaded_file.seek(0)
                metadata = asyncio.run(st.session_state.preview_system.process_image_async(uploaded_file))
                if metadata:
                    all_metadata.append(asdict(metadata))
            
            if all_metadata:
                metadata_json = json.dumps(all_metadata, indent=2, ensure_ascii=False)
                st.download_button(
                    label="📥 Metadaten als JSON herunterladen",
                    data=metadata_json,
                    file_name=f"image_metadata_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json",
                    mime="application/json"
                )

if __name__ == "__main__":
    # Streamlit Konfiguration
    st.set_page_config(
        page_title="Multimodal Image Preview",
        page_icon="🖼️",
        layout="wide",
        initial_sidebar_state="expanded"
    )
    
    # CSS für besseres Styling
    st.markdown("""
    <style>
    .stApp {
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
    }
    .stContainer {
        background: white;
        padding: 20px;
        border-radius: 10px;
        box-shadow: 0 4px 20px rgba(0,0,0,0.1);
        margin: 20px 0;
    }
    </style>
    """, unsafe_allow_html=True)
    
    # Main Interface
    create_image_preview_interface()
