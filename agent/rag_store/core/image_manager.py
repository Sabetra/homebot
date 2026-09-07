"""
Image Manager - Verwaltet Bild-Metadaten und OCR-Ergebnisse
============================================================

Dieses Modul bietet zentrale Verwaltung für multimodale Daten:
- Speichern von Bild-Metadaten in der Datenbank
- Verknüpfung von Bildern mit Dokumenten
- OCR-Ergebnis-Speicherung
- Abrufen von Bildverweisen

Extrahiert aus unified_rag_store.py für bessere Modularität.
"""

import sqlite3
import logging
from typing import List, Dict, Optional, Any
from datetime import datetime

logger = logging.getLogger(__name__)


class ImageManager:
    """Verwaltet Bild-Metadaten und OCR-Ergebnisse im RAG Store"""
    
    def __init__(self, db_manager):
        """
        Args:
            db_manager: DatabaseManager Instanz
        """
        self.db = db_manager
        logger.info("ImageManager initialized")
    
    def add_image_metadata(
        self,
        doc_id: str,
        image_path: str,
        page_number: Optional[int] = None,
        image_index: int = 0,
        image_format: Optional[str] = None,
        width: Optional[int] = None,
        height: Optional[int] = None,
        size_bytes: int = 0,
        image_hash: Optional[str] = None,
        alt_text: Optional[str] = None,
        ocr_text: Optional[str] = None,
        ocr_confidence: Optional[float] = None,
        source: str = "pdf"
    ) -> int:
        """
        Speichert Bild-Metadaten in der Datenbank
        
        Args:
            doc_id: ID des Dokuments
            image_path: Pfad zum gespeicherten Bild
            page_number: Seitennummer (für PDFs)
            image_index: Position des Bildes auf der Seite
            image_format: Bildformat (png, jpg, etc.)
            width: Bildbreite in Pixeln
            height: Bildhöhe in Pixeln
            size_bytes: Dateigröße in Bytes
            image_hash: MD5-Hash für Duplikaterkennung
            alt_text: Alt-Text (für Web-Bilder)
            ocr_text: Extrahierter Text via OCR
            ocr_confidence: OCR-Konfidenz (0.0-1.0)
            source: Quelle ('pdf' oder 'web')
            
        Returns:
            int: ID des eingefügten Eintrags
        """
        with self.db.connection() as conn:
            cur = conn.cursor()
            
            created_at = datetime.now().isoformat()
            
            cur.execute(
                """
                INSERT INTO image_metadata (
                    doc_id, page_number, image_index, image_path, image_format,
                    width, height, size_bytes, image_hash, alt_text,
                    ocr_text, ocr_confidence, source, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    doc_id, page_number, image_index, image_path, image_format,
                    width, height, size_bytes, image_hash, alt_text,
                    ocr_text, ocr_confidence, source, created_at
                )
            )
            
            conn.commit()
            image_id: int = cur.lastrowid or 0
            
            logger.debug(f"Added image metadata: {image_path} (ID: {image_id})")
            return image_id
    
    def get_images_for_document(self, doc_id: str) -> List[Dict[str, Any]]:
        """
        Holt alle Bilder für ein Dokument
        
        Args:
            doc_id: Dokument-ID
            
        Returns:
            List[Dict]: Liste von Bild-Metadaten
        """
        with self.db.connection() as conn:
            cur = conn.cursor()
            
            cur.execute(
                """
                SELECT 
                    image_id, doc_id, page_number, image_index, image_path,
                    image_format, width, height, size_bytes, image_hash,
                    alt_text, ocr_text, ocr_confidence, source, created_at
                FROM image_metadata
                WHERE doc_id = ?
                ORDER BY page_number, image_index
                """,
                (doc_id,)
            )
            
            rows = cur.fetchall()
            
            images = []
            for row in rows:
                images.append({
                    'image_id': row[0],
                    'doc_id': row[1],
                    'page_number': row[2],
                    'image_index': row[3],
                    'image_path': row[4],
                    'image_format': row[5],
                    'width': row[6],
                    'height': row[7],
                    'size_bytes': row[8],
                    'image_hash': row[9],
                    'alt_text': row[10],
                    'ocr_text': row[11],
                    'ocr_confidence': row[12],
                    'source': row[13],
                    'created_at': row[14]
                })
            
            return images
    
    def get_images_by_page(self, doc_id: str, page_number: int) -> List[Dict[str, Any]]:
        """
        Holt alle Bilder für eine bestimmte Seite
        
        Args:
            doc_id: Dokument-ID
            page_number: Seitennummer
            
        Returns:
            List[Dict]: Liste von Bild-Metadaten
        """
        with self.db.connection() as conn:
            cur = conn.cursor()
            
            cur.execute(
                """
                SELECT 
                    image_id, doc_id, page_number, image_index, image_path,
                    image_format, width, height, size_bytes, image_hash,
                    alt_text, ocr_text, ocr_confidence, source, created_at
                FROM image_metadata
                WHERE doc_id = ? AND page_number = ?
                ORDER BY image_index
                """,
                (doc_id, page_number)
            )
            
            rows = cur.fetchall()
            
            images = []
            for row in rows:
                images.append({
                    'image_id': row[0],
                    'doc_id': row[1],
                    'page_number': row[2],
                    'image_index': row[3],
                    'image_path': row[4],
                    'image_format': row[5],
                    'width': row[6],
                    'height': row[7],
                    'size_bytes': row[8],
                    'image_hash': row[9],
                    'alt_text': row[10],
                    'ocr_text': row[11],
                    'ocr_confidence': row[12],
                    'source': row[13],
                    'created_at': row[14]
                })
            
            return images
    
    def update_ocr_results(
        self,
        image_id: int,
        ocr_text: str,
        ocr_confidence: float
    ) -> None:
        """
        Aktualisiert OCR-Ergebnisse für ein Bild
        
        Args:
            image_id: Bild-ID
            ocr_text: Extrahierter Text
            ocr_confidence: Konfidenz (0.0-1.0)
        """
        with self.db.connection() as conn:
            cur = conn.cursor()
            
            cur.execute(
                """
                UPDATE image_metadata
                SET ocr_text = ?, ocr_confidence = ?
                WHERE image_id = ?
                """,
                (ocr_text, ocr_confidence, image_id)
            )
            
            conn.commit()
            logger.debug(f"Updated OCR results for image {image_id}")
    
    def search_images_with_ocr(
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
            List[Dict]: Gefundene Bilder mit OCR-Text
        """
        with self.db.connection() as conn:
            cur = conn.cursor()
            
            cur.execute(
                """
                SELECT 
                    image_id, doc_id, page_number, image_index, image_path,
                    image_format, width, height, size_bytes, image_hash,
                    alt_text, ocr_text, ocr_confidence, source, created_at
                FROM image_metadata
                WHERE ocr_text LIKE ? 
                AND ocr_confidence >= ?
                ORDER BY ocr_confidence DESC
                """,
                (f'%{search_text}%', min_confidence)
            )
            
            rows = cur.fetchall()
            
            images = []
            for row in rows:
                images.append({
                    'image_id': row[0],
                    'doc_id': row[1],
                    'page_number': row[2],
                    'image_index': row[3],
                    'image_path': row[4],
                    'image_format': row[5],
                    'width': row[6],
                    'height': row[7],
                    'size_bytes': row[8],
                    'image_hash': row[9],
                    'alt_text': row[10],
                    'ocr_text': row[11],
                    'ocr_confidence': row[12],
                    'source': row[13],
                    'created_at': row[14]
                })
            
            return images
    
    def get_statistics(self) -> Dict[str, Any]:
        """
        Holt Statistiken über gespeicherte Bilder
        
        Returns:
            Dict mit Statistiken
        """
        with self.db.connection() as conn:
            cur = conn.cursor()
            
            # Gesamtzahl Bilder
            cur.execute("SELECT COUNT(*) FROM image_metadata")
            total_images = cur.fetchone()[0]
            
            # Bilder mit OCR
            cur.execute("SELECT COUNT(*) FROM image_metadata WHERE ocr_text IS NOT NULL")
            images_with_ocr = cur.fetchone()[0]
            
            # Durchschnittliche OCR-Konfidenz
            cur.execute(
                "SELECT AVG(ocr_confidence) FROM image_metadata WHERE ocr_confidence IS NOT NULL"
            )
            avg_confidence = cur.fetchone()[0] or 0.0
            
            # Gesamtgröße
            cur.execute("SELECT SUM(size_bytes) FROM image_metadata")
            total_size = cur.fetchone()[0] or 0
            
            # Nach Quelle gruppiert
            cur.execute(
                "SELECT source, COUNT(*) FROM image_metadata GROUP BY source"
            )
            by_source = {row[0]: row[1] for row in cur.fetchall()}
            
            return {
                'total_images': total_images,
                'images_with_ocr': images_with_ocr,
                'avg_ocr_confidence': avg_confidence,
                'total_size_bytes': total_size,
                'total_size_mb': total_size / (1024 * 1024),
                'by_source': by_source
            }
    
    def delete_images_for_document(self, doc_id: str) -> int:
        """
        Löscht alle Bilder für ein Dokument
        
        Args:
            doc_id: Dokument-ID
            
        Returns:
            int: Anzahl gelöschter Einträge
        """
        with self.db.connection() as conn:
            cur = conn.cursor()
            
            cur.execute("DELETE FROM image_metadata WHERE doc_id = ?", (doc_id,))
            conn.commit()
            
            deleted: int = cur.rowcount or 0
            logger.debug(f"Deleted {deleted} image metadata entries for {doc_id}")
            return deleted
