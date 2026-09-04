"""
Image Utilities - Hilfsfunktionen für Bildverarbeitung
========================================================

Dieses Modul bietet nützliche Utility-Funktionen für die Arbeit mit Bildern:
- Bildformat-Validierung
- Größen-Konvertierung
- Thumbnail-Generierung
- Format-Konvertierung
"""

import os
import logging
from typing import Tuple, Optional
from pathlib import Path
from PIL import Image

logger = logging.getLogger(__name__)


# Unterstützte Bildformate
SUPPORTED_IMAGE_FORMATS = {'.png', '.jpg', '.jpeg', '.gif', '.bmp', '.tiff', '.webp'}


def is_supported_image(file_path: str) -> bool:
    """
    Prüft ob eine Datei ein unterstütztes Bildformat ist
    
    Args:
        file_path: Pfad zur Datei
        
    Returns:
        bool: True wenn unterstützt
    """
    ext = Path(file_path).suffix.lower()
    return ext in SUPPORTED_IMAGE_FORMATS


def get_image_size(image_path: str) -> Tuple[int, int]:
    """
    Ermittelt Bildgröße ohne das ganze Bild zu laden
    
    Args:
        image_path: Pfad zum Bild
        
    Returns:
        Tuple[width, height]: Bildgröße in Pixeln
    """
    try:
        with Image.open(image_path) as img:
            w: int = img.size[0]
            h: int = img.size[1]
            return (w, h)
    except Exception as e:
        logger.error(f"Error getting image size for {image_path}: {e}")
        return (0, 0)


def get_file_size(file_path: str) -> int:
    """
    Ermittelt Dateigröße in Bytes
    
    Args:
        file_path: Pfad zur Datei
        
    Returns:
        int: Größe in Bytes
    """
    try:
        return os.path.getsize(file_path)
    except Exception as e:
        logger.error(f"Error getting file size for {file_path}: {e}")
        return 0


def create_thumbnail(
    image_path: str,
    output_path: str,
    max_size: Tuple[int, int] = (200, 200)
) -> bool:
    """
    Erstellt ein Thumbnail eines Bildes
    
    Args:
        image_path: Pfad zum Original-Bild
        output_path: Pfad für das Thumbnail
        max_size: Maximale Größe (Breite, Höhe)
        
    Returns:
        bool: True bei Erfolg
    """
    try:
        with Image.open(image_path) as img:
            img.thumbnail(max_size, Image.Resampling.LANCZOS)
            img.save(output_path)
            logger.debug(f"Created thumbnail: {output_path}")
            return True
    except Exception as e:
        logger.error(f"Error creating thumbnail: {e}")
        return False


def convert_image_format(
    image_path: str,
    output_path: str,
    output_format: str = 'PNG'
) -> bool:
    """
    Konvertiert ein Bild in ein anderes Format
    
    Args:
        image_path: Pfad zum Original-Bild
        output_path: Pfad für konvertiertes Bild
        output_format: Zielformat (PNG, JPEG, etc.)
        
    Returns:
        bool: True bei Erfolg
    """
    try:
        with Image.open(image_path) as img:
            # Konvertiere zu RGB wenn JPEG (keine Alpha-Kanal)
            if output_format.upper() == 'JPEG' and img.mode in ('RGBA', 'LA', 'P'):
                rgb_img = Image.new('RGB', img.size, (255, 255, 255))
                if img.mode == 'P':
                    img = img.convert('RGBA')
                rgb_img.paste(img, mask=img.split()[-1] if img.mode == 'RGBA' else None)
                img = rgb_img
            
            img.save(output_path, format=output_format)
            logger.debug(f"Converted {image_path} to {output_format}")
            return True
    except Exception as e:
        logger.error(f"Error converting image format: {e}")
        return False


def optimize_image_size(
    image_path: str,
    output_path: Optional[str] = None,
    max_size_mb: float = 1.0,
    quality: int = 85
) -> bool:
    """
    Optimiert Bildgröße durch Kompression
    
    Args:
        image_path: Pfad zum Original-Bild
        output_path: Pfad für optimiertes Bild (None = überschreiben)
        max_size_mb: Maximale Größe in MB
        quality: JPEG Qualität (1-100)
        
    Returns:
        bool: True bei Erfolg
    """
    if output_path is None:
        output_path = image_path
    
    try:
        with Image.open(image_path) as img:
            # Konvertiere zu RGB für JPEG
            if img.mode in ('RGBA', 'LA', 'P'):
                rgb_img = Image.new('RGB', img.size, (255, 255, 255))
                if img.mode == 'P':
                    img = img.convert('RGBA')
                rgb_img.paste(img, mask=img.split()[-1] if img.mode == 'RGBA' else None)
                img = rgb_img
            
            # Speichere mit Kompression
            img.save(output_path, format='JPEG', quality=quality, optimize=True)
            
            # Prüfe Größe
            size_mb = os.path.getsize(output_path) / (1024 * 1024)
            if size_mb > max_size_mb:
                logger.warning(f"Optimized image still > {max_size_mb}MB: {size_mb:.2f}MB")
            else:
                logger.debug(f"Optimized image: {size_mb:.2f}MB")
            
            return True
    except Exception as e:
        logger.error(f"Error optimizing image: {e}")
        return False


def is_image_readable(image_path: str) -> bool:
    """
    Prüft ob ein Bild lesbar/gültig ist
    
    Args:
        image_path: Pfad zum Bild
        
    Returns:
        bool: True wenn lesbar
    """
    try:
        with Image.open(image_path) as img:
            img.verify()
        return True
    except Exception:
        return False


def get_image_format(image_path: str) -> Optional[str]:
    """
    Ermittelt das Bildformat
    
    Args:
        image_path: Pfad zum Bild
        
    Returns:
        str: Format-String (PNG, JPEG, etc.) oder None
    """
    try:
        with Image.open(image_path) as img:
            fmt: Optional[str] = img.format
            return fmt
    except Exception as e:
        logger.error(f"Error getting image format for {image_path}: {e}")
        return None


def calculate_aspect_ratio(width: int, height: int) -> float:
    """
    Berechnet Seitenverhältnis
    
    Args:
        width: Bildbreite
        height: Bildhöhe
        
    Returns:
        float: Seitenverhältnis (width/height)
    """
    if height == 0:
        return 0.0
    return width / height


def resize_image(
    image_path: str,
    output_path: str,
    target_size: Tuple[int, int],
    maintain_aspect_ratio: bool = True
) -> bool:
    """
    Ändert Bildgröße
    
    Args:
        image_path: Pfad zum Original-Bild
        output_path: Pfad für skaliertes Bild
        target_size: Zielgröße (Breite, Höhe)
        maintain_aspect_ratio: Seitenverhältnis beibehalten
        
    Returns:
        bool: True bei Erfolg
    """
    try:
        with Image.open(image_path) as img:
            if maintain_aspect_ratio:
                img.thumbnail(target_size, Image.Resampling.LANCZOS)
            else:
                img = img.resize(target_size, Image.Resampling.LANCZOS)
            
            img.save(output_path)
            logger.debug(f"Resized image to {target_size}: {output_path}")
            return True
    except Exception as e:
        logger.error(f"Error resizing image: {e}")
        return False


def format_size(size_bytes: int) -> str:
    """
    Formatiert Dateigröße lesbar
    
    Args:
        size_bytes: Größe in Bytes
        
    Returns:
        str: Formatierte Größe (z.B. "1.5 MB")
    """
    size = float(size_bytes)
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size < 1024.0:
            return f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{size:.1f} TB"
