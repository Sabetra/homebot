#!/usr/bin/env python3
"""
PDF READABILITY CHECKER
========================

Prüft ob eine PDF-Datei lesbar ist, bevor sie verarbeitet wird.
Wenn die PDF nicht lesbar ist, wird sie übersprungen.
"""

import os
import logging
from typing import Dict, Any, Tuple
from pathlib import Path

logger = logging.getLogger(__name__)

class PDFReadabilityChecker:
    """
    Prüft PDF-Dateien auf Lesbarkeit vor der Verarbeitung
    """
    
    def __init__(self):
        # Root-Cause-Fix 2026-07-14: _check_with_advanced_processor entfernt.
        # Er delegierte an DoclingProcessor (volle Konversion nur fuer einen
        # Readability-Check) und war nach dem pypdf-Check faktisch unerreichbar.
        self.fallback_methods = [
            self._check_with_pymupdf,
            self._check_with_pypdf,
            self._check_with_pdfminer,
        ]
    
    def is_pdf_readable(self, file_path: str) -> Tuple[bool, Dict[str, Any]]:
        """
        Prüft ob eine PDF-Datei lesbar ist
        
        Args:
            file_path: Pfad zur PDF-Datei
            
        Returns:
            Tuple von (is_readable, metadata_dict)
        """
        # Basis-Validierung
        if not os.path.exists(file_path):
            return False, {
                "error": "File not found",
                "file_path": file_path,
                "readable": False
            }
        
        if not file_path.lower().endswith('.pdf'):
            return False, {
                "error": "Not a PDF file",
                "file_path": file_path,
                "readable": False
            }
        
        # Datei-Größe prüfen
        try:
            file_size = os.path.getsize(file_path)
            if file_size == 0:
                return False, {
                    "error": "Empty file",
                    "file_path": file_path,
                    "file_size": 0,
                    "readable": False
                }
        except Exception as e:
            return False, {
                "error": f"Cannot read file size: {e}",
                "file_path": file_path,
                "readable": False
            }
        
        # Versuche verschiedene Methoden um PDF zu öffnen
        last_error = None
        for method in self.fallback_methods:
            try:
                is_readable, method_metadata = method(file_path)
                if is_readable:
                    return True, {
                        "readable": True,
                        "file_path": file_path,
                        "file_size": file_size,
                        "check_method": method.__name__,
                        **method_metadata
                    }
                else:
                    last_error = method_metadata.get("error", "Unknown error")
            except Exception as e:
                last_error = str(e)
                continue
        
        # Alle Methoden fehlgeschlagen
        return False, {
            "error": f"PDF not readable with any method. Last error: {last_error}",
            "file_path": file_path,
            "file_size": file_size,
            "readable": False,
            "attempted_methods": [m.__name__ for m in self.fallback_methods]
        }
    
    def _check_with_pymupdf(self, file_path: str) -> Tuple[bool, Dict[str, Any]]:
        """Prüft PDF mit PyMuPDF (fitz)"""
        try:
            import fitz
            
            with fitz.open(file_path) as doc:
                # Prüfe ob PDF öffenbar ist
                if doc.page_count == 0:
                    return False, {"error": "PDF has no pages"}
                
                # Versuche ersten Seiten-Text zu extrahieren
                try:
                    first_page = doc[0]
                    test_text = str(first_page.get_text())
                    
                    # PDF ist lesbar wenn:
                    # 1. Kein Fehler beim Text-Extraktion
                    # 2. Text existiert ODER PDF hat Bilder (scanierte PDFs)
                    if test_text.strip() or len(first_page.get_images()) > 0:
                        return True, {
                            "method": "pymupdf",
                            "page_count": doc.page_count,
                            "has_text": bool(test_text.strip()),
                            "has_images": len(first_page.get_images()) > 0,
                            "is_protected": doc.needs_pass
                        }
                    else:
                        return False, {"error": "No extractable text or images found"}
                        
                except Exception as e:
                    return False, {"error": f"Text extraction failed: {e}"}
                    
        except ImportError:
            return False, {"error": "PyMuPDF not available"}
        except Exception as e:
            return False, {"error": f"PyMuPDF error: {e}"}
    
    def _check_with_pdfminer(self, file_path: str) -> Tuple[bool, Dict[str, Any]]:
        """Prüft PDF mit pdfminer"""
        try:
            from pdfminer.high_level import extract_text
            from pdfminer.pdfpage import PDFPage
            from pdfminer.pdfparser import PDFParser
            from pdfminer.pdfdocument import PDFDocument
            
            # Versuche PDF-Seiten zu lesen
            with open(file_path, 'rb') as file:
                parser = PDFParser(file)
                document = PDFDocument(parser)
                
                # Prüfe ob PDF geschützt ist
                if document.is_extractable:
                    # Versuche Text-Extraktion einer kleinen Probe
                    test_text = extract_text(file_path, maxpages=1)
                    
                    if test_text and test_text.strip():
                        return True, {
                            "method": "pdfminer",
                            "extractable": True,
                            "has_text": True
                        }
                    else:
                        # Auch ohne Text können PDFs verarbeitbar sein (Bilder)
                        return True, {
                            "method": "pdfminer", 
                            "extractable": True,
                            "has_text": False,
                            "note": "No text found but PDF is accessible"
                        }
                else:
                    return False, {"error": "PDF is password protected and not extractable"}
                    
        except ImportError:
            return False, {"error": "pdfminer not available"}
        except Exception as e:
            return False, {"error": f"pdfminer error: {e}"}
    
    def _check_with_pypdf(self, file_path: str) -> Tuple[bool, Dict[str, Any]]:
        """Prüft PDF mit pypdf (zusätzliche Fallback-Methode)"""
        try:
            import pypdf
            
            with open(file_path, 'rb') as file:
                pdf_reader = pypdf.PdfReader(file)
                
                # Prüfe grundlegende PDF-Eigenschaften
                if len(pdf_reader.pages) == 0:
                    return False, {"error": "PDF has no pages"}
                
                # Versuche ersten Seiten-Text zu extrahieren
                try:
                    first_page = pdf_reader.pages[0]
                    test_text = first_page.extract_text()
                    
                    if test_text.strip():
                        return True, {
                            "method": "pypdf",
                            "page_count": len(pdf_reader.pages),
                            "has_text": True,
                            "is_encrypted": pdf_reader.is_encrypted
                        }
                    else:
                        # Könnte eine bildbasierte PDF sein
                        return True, {
                            "method": "pypdf",
                            "page_count": len(pdf_reader.pages),
                            "has_text": False,
                            "note": "No text extracted but PDF is accessible",
                            "is_encrypted": pdf_reader.is_encrypted
                        }
                        
                except Exception as e:
                    return False, {"error": f"Text extraction failed: {e}"}
                    
        except ImportError:
            return False, {"error": "pypdf not available"}
        except Exception as e:
            return False, {"error": f"pypdf error: {e}"}


def check_pdf_readable(file_path: str) -> Tuple[bool, Dict[str, Any]]:
    """
    Convenience-Funktion zur PDF-Lesbarkeits-Prüfung
    
    Args:
        file_path: Pfad zur PDF-Datei
        
    Returns:
        Tuple von (is_readable, metadata_dict)
    """
    checker = PDFReadabilityChecker()
    return checker.is_pdf_readable(file_path)


def log_readability_result(file_path: str, is_readable: bool, metadata: Dict[str, Any]):
    """
    Loggt das Ergebnis der Lesbarkeits-Prüfung
    """
    filename = os.path.basename(file_path)
    
    if is_readable:
        method = metadata.get("check_method", "unknown")
        logger.info(f"✅ PDF readable: {filename} (method: {method})")
        
        # Zusätzliche Details loggen
        if metadata.get("was_protected"):
            logger.info(f"   🔓 Was protected, bypassed with: {metadata.get('bypass_methods', [])}")
        if not metadata.get("has_text", True):
            logger.info(f"   📷 No text found, likely image-based PDF")
            
    else:
        error = metadata.get("error", "Unknown error")
        logger.warning(f"❌ PDF not readable: {filename} - {error}")
        
        # Versuche mögliche Lösungen zu empfehlen
        if "password" in error.lower():
            logger.info(f"   💡 Try: PDF is password protected, consider manual unlock")
        elif "corrupt" in error.lower():
            logger.info(f"   💡 Try: PDF may be corrupted, check file integrity")
        elif "empty" in error.lower():
            logger.info(f"   💡 Try: PDF file is empty or has no content")


if __name__ == "__main__":
    # Test der PDF-Lesbarkeits-Prüfung
    import sys
    
    if len(sys.argv) > 1:
        test_file = sys.argv[1]
        is_readable, metadata = check_pdf_readable(test_file)
        log_readability_result(test_file, is_readable, metadata)
        
        print(f"\nResult: {'READABLE' if is_readable else 'NOT READABLE'}")
        print(f"Details: {metadata}")
    else:
        print("Usage: python pdf_readability_checker.py <pdf_file>")
