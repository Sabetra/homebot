"""
Image Extractor - Extrahiert Bilder aus PDFs und Webseiten
============================================================

Dieses Modul bietet Funktionen zum Extrahieren von Bildern aus verschiedenen Quellen:
- PDF-Dateien (via PyMuPDF/fitz)
- Webseiten (via BeautifulSoup + requests)

Features:
- Duplikaterkennung via MD5 Hash
- Metadaten-Erfassung (Größe, Format, Position)
- Automatische Speicherung in Output-Verzeichnis
"""

import os
import hashlib
from typing import List, Dict, Optional
from dataclasses import dataclass, field
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


@dataclass
class ImageData:
    """Container für extrahierte Bilddaten"""
    source: str  # 'pdf' oder 'web'
    source_path: str  # PDF-Pfad oder URL
    page_number: Optional[int]  # Nur für PDFs
    index: int  # Position im Dokument
    image_path: str  # Wo das Bild gespeichert wurde
    format: str  # png, jpg, etc.
    width: Optional[int] = None
    height: Optional[int] = None
    size_bytes: int = 0
    hash: str = ""  # MD5 für Duplikaterkennung
    alt_text: Optional[str] = None  # Nur für Web-Bilder
    created_at: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> Dict:
        """Konvertiert zu Dictionary für DB-Speicherung"""
        return {
            'source': self.source,
            'source_path': self.source_path,
            'page_number': self.page_number,
            'index': self.index,
            'image_path': self.image_path,
            'format': self.format,
            'width': self.width,
            'height': self.height,
            'size_bytes': self.size_bytes,
            'hash': self.hash,
            'alt_text': self.alt_text,
            'created_at': self.created_at.isoformat()
        }


class ImageExtractor:
    """Extrahiert Bilder aus verschiedenen Quellen"""
    
    def __init__(self, output_dir: str = "./data/extracted_images") -> None:
        """
        Args:
            output_dir: Verzeichnis für extrahierte Bilder
        """
        self.output_dir = output_dir
        os.makedirs(output_dir, exist_ok=True)
        self.seen_hashes: set[str] = set()  # Duplikaterkennung
        logger.info(f"ImageExtractor initialized (output: {output_dir})")
    
    def extract_from_pdf(self, pdf_path: str, skip_duplicates: bool = True) -> List[ImageData]:
        """
        Extrahiert alle Bilder aus einem PDF
        
        Args:
            pdf_path: Pfad zur PDF-Datei
            skip_duplicates: Ob Duplikate übersprungen werden sollen
            
        Returns:
            List[ImageData]: Liste der extrahierten Bilder
        """
        try:
            import fitz  # PyMuPDF
        except ImportError:
            logger.error("PyMuPDF nicht installiert: pip install pymupdf")
            return []
        
        images = []
        
        try:
            doc = fitz.open(pdf_path)
            pdf_name = os.path.splitext(os.path.basename(pdf_path))[0]
            
            logger.info(f"Extracting images from {pdf_path} ({len(doc)} pages)")
            
            for page_num in range(len(doc)):
                page = doc[page_num]
                image_list = page.get_images(full=True)
                
                logger.debug(f"Page {page_num+1}: {len(image_list)} images found")
                
                for img_index, img in enumerate(image_list):
                    try:
                        xref = img[0]
                        base_image = doc.extract_image(xref)
                        image_bytes = base_image["image"]
                        image_ext = base_image["ext"]
                        
                        # Duplikaterkennung
                        image_hash = hashlib.md5(image_bytes).hexdigest()
                        if skip_duplicates and image_hash in self.seen_hashes:
                            logger.debug(f"Skipping duplicate image: {image_hash[:8]}...")
                            continue
                        self.seen_hashes.add(image_hash)
                        
                        # Dateiname generieren
                        filename = f"{pdf_name}_page{page_num+1:03d}_img{img_index:02d}.{image_ext}"
                        filepath = os.path.join(self.output_dir, filename)
                        
                        # Bild speichern
                        with open(filepath, "wb") as img_file:
                            img_file.write(image_bytes)
                        
                        # Metadaten sammeln
                        image_data = ImageData(
                            source='pdf',
                            source_path=pdf_path,
                            page_number=page_num + 1,
                            index=img_index,
                            image_path=filepath,
                            format=image_ext,
                            width=base_image.get('width'),
                            height=base_image.get('height'),
                            size_bytes=len(image_bytes),
                            hash=image_hash
                        )
                        
                        images.append(image_data)
                        logger.debug(f"Extracted: {filename} ({len(image_bytes)} bytes)")
                        
                    except Exception as e:
                        logger.error(f"Error extracting image {img_index} from page {page_num}: {e}")
                        continue
            
            doc.close()
            logger.info(f"✅ Extracted {len(images)} unique images from {pdf_path}")
            
        except Exception as e:
            logger.error(f"Error processing PDF {pdf_path}: {e}")
        
        return images
    
    def extract_from_web(self, url: str, html_content: Optional[str] = None,
                        skip_duplicates: bool = True) -> List[ImageData]:
        """
        Lädt Bilder von einer Webseite herunter
        
        Args:
            url: URL der Webseite
            html_content: Optional vorhandener HTML-Content
            skip_duplicates: Ob Duplikate übersprungen werden sollen
            
        Returns:
            List[ImageData]: Liste der heruntergeladenen Bilder
        """
        try:
            import requests
            from bs4 import BeautifulSoup
            from urllib.parse import urljoin, urlparse
        except ImportError:
            logger.error("Benötigte Pakete fehlen: pip install requests beautifulsoup4")
            return []
        
        images = []
        
        try:
            # HTML holen falls nicht übergeben
            raw_content: str | bytes
            if html_content is None:
                logger.info(f"Fetching {url}...")
                response = requests.get(url, timeout=10, headers={
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
                })
                raw_content = response.content
            else:
                raw_content = html_content
            
            soup = BeautifulSoup(raw_content, 'html.parser')
            img_tags = soup.find_all('img')
            
            logger.info(f"Found {len(img_tags)} <img> tags on {url}")
            
            domain = urlparse(url).netloc.replace('.', '_')
            
            for idx, img in enumerate(img_tags):
                img_src_raw = img.get('src')
                if not img_src_raw:
                    continue
                
                # Ensure img_url is a str
                img_url: str = str(img_src_raw) if not isinstance(img_src_raw, str) else img_src_raw
                
                # Resolve relative URLs
                img_url = urljoin(url, img_url)
                
                try:
                    # Download image
                    logger.debug(f"Downloading: {img_url}")
                    img_response = requests.get(img_url, timeout=10, headers={
                        'User-Agent': 'Mozilla/5.0'
                    })
                    
                    if img_response.status_code != 200:
                        logger.warning(f"Failed to download {img_url}: HTTP {img_response.status_code}")
                        continue
                    
                    image_bytes = img_response.content
                    
                    # Duplikaterkennung
                    image_hash = hashlib.md5(image_bytes).hexdigest()
                    if skip_duplicates and image_hash in self.seen_hashes:
                        logger.debug(f"Skipping duplicate web image: {image_hash[:8]}...")
                        continue
                    self.seen_hashes.add(image_hash)
                    
                    # Determine extension
                    ext = img_url.split('.')[-1].split('?')[0].lower()
                    if ext not in ['jpg', 'jpeg', 'png', 'gif', 'webp', 'svg']:
                        ext = 'jpg'
                    
                    # Filename
                    filename = f"{domain}_img{idx:03d}.{ext}"
                    filepath = os.path.join(self.output_dir, filename)
                    
                    # Save
                    with open(filepath, 'wb') as f:
                        f.write(image_bytes)
                    
                    # Get dimensions if possible
                    width, height = None, None
                    try:
                        from PIL import Image
                        with Image.open(filepath) as pil_img:
                            width, height = pil_img.size
                    except Exception:
                        pass
                    
                    # Metadaten
                    alt_raw = img.get('alt', '')
                    alt_text: str = str(alt_raw) if not isinstance(alt_raw, str) else alt_raw
                    image_data = ImageData(
                        source='web',
                        source_path=url,
                        page_number=None,
                        index=idx,
                        image_path=filepath,
                        format=ext,
                        width=width,
                        height=height,
                        size_bytes=len(image_bytes),
                        hash=image_hash,
                        alt_text=alt_text
                    )
                    
                    images.append(image_data)
                    logger.debug(f"Downloaded: {filename} ({len(image_bytes)} bytes)")
                    
                except Exception as e:
                    logger.warning(f"Failed to download {img_url}: {e}")
                    continue
            
            logger.info(f"✅ Downloaded {len(images)} unique images from {url}")
            
        except Exception as e:
            logger.error(f"Error processing URL {url}: {e}")
        
        return images
    
    def cleanup_old_images(self, days: int = 7) -> int:
        """
        Löscht Bilder älter als X Tage
        
        Args:
            days: Alter in Tagen
            
        Returns:
            Anzahl gelöschter Dateien
        """
        import time
        cutoff = time.time() - (days * 24 * 60 * 60)
        
        deleted = 0
        for filename in os.listdir(self.output_dir):
            filepath = os.path.join(self.output_dir, filename)
            if os.path.isfile(filepath):
                if os.path.getmtime(filepath) < cutoff:
                    try:
                        os.remove(filepath)
                        deleted += 1
                    except Exception as e:
                        logger.warning(f"Could not delete {filepath}: {e}")
        
        logger.info(f"🗑️ Deleted {deleted} old images (>{days} days)")
        return deleted
    
    def reset_duplicate_tracking(self):
        """Reset der Duplikatserkennung (für neue Extraktions-Session)"""
        self.seen_hashes.clear()
        logger.info("Duplicate tracking reset")


# Convenience functions
def extract_pdf_images(pdf_path: str, output_dir: str = "./data/extracted_images") -> List[ImageData]:
    """Quick helper: Extrahiere Bilder aus PDF"""
    extractor = ImageExtractor(output_dir)
    return extractor.extract_from_pdf(pdf_path)


def extract_web_images(url: str, output_dir: str = "./data/extracted_images") -> List[ImageData]:
    """Quick helper: Extrahiere Bilder von Webseite"""
    extractor = ImageExtractor(output_dir)
    return extractor.extract_from_web(url)
