"""
Zentrale Freigabe kühler AUX-GPU-Modelle nach Import-/OCR-Peaks.
================================================================

Kontext (Dual-GPU, 2026-08-28):
  - LLM (Gemma4 12B) läuft auf der RTX 4090 und braucht dort dauerhaft VRAM.
  - Die AUX-GPUs-Modelle (RTX 3060 Ti, 8 GB) teilen sich den VRAM:
      * Heiß (Query-Path, bleiben resident):  Reranker, NLI, Embeddings
      * Kalt (Import-/OCR-Peaks, entladbar): Docling, EasyOCR

Diese Module entladen die KALTEN Modelle nach Dokument-Importen, damit
die 3060 Ti-VRAM für die heißen Query-Path-Modelle und für Headroom frei
ist. Alle Modelle sind LAZY — sie laden sich bei nächster Verwendung
transparent neu (kein Feature-Verlust).

Single Source of Truth für GPU-Platzierung: utils/gpu_devices.py
Kein hardcodiertes cuda:0/cuda:1; torch.cuda.empty_cache() ist GPU-agnostisch.
"""
from __future__ import annotations

import gc
import logging
from typing import List

logger = logging.getLogger(__name__)


def _empty_cuda_cache() -> None:
    """Gibt den CUDA-Cache an das OS zurück (no-op ohne CUDA/Torch)."""
    try:
        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        pass


def release_cold_aux_models(reason: str = "import") -> List[str]:
    """Entlädt die kalten AUX-Modelle (Docling, EasyOCR) aus GPU/RAM.

    Idempotent und defensive: Ein Fehler bei einem Modell blockiert die
    Freigabe der anderen nicht. Die heißen Modelle (Reranker/NLI/Embeddings)
    werden NICHT berührt, um wiederholte Reload-Latenzen im Query-Path zu
    vermeiden.

    Args:
        reason: Kontext für Logging (z. B. "pdf_import").

    Returns:
        Liste der freigegebenen Komponenten (z. B. ["docling", "easyocr"]).
    """
    freed: List[str] = []

    # 1) Docling (Singleton; entlädt Pipeline inkl. interner OCR-Modelle)
    try:
        from utils.docling_processor import DoclingProcessor
        proc = DoclingProcessor.get_instance()
        if getattr(proc, "_initialized", False) or getattr(proc, "_converter", None) is not None:
            proc.cleanup()
            freed.append("docling")
    except Exception as exc:
        logger.debug(f"Docling-Release übersprungen ({reason}): {exc}")

    # 2) EasyOCR (alle kalten OCR-Instanzen via WeakSet-Registries)
    #    OCRProcessor (self.reader) UND VisionOCRProcessor (self.easyocr_reader).
    #    Beide cleanup()-Methoden sind idempotent und intern gegentestet;
    #    deshalb wird cleanup() ohne zusätzliche Attribut-Prüfung aufgerufen.
    ocr_instances = []
    try:
        from agent.ocr_processor import get_active_ocr_processors
        ocr_instances.extend(get_active_ocr_processors())
    except Exception as exc:
        logger.debug(f"OCRProcessor-Registry nicht verfügbar ({reason}): {exc}")
    try:
        from agent.vision_ocr_processor import get_active_vision_ocr_processors
        ocr_instances.extend(get_active_vision_ocr_processors())
    except Exception as exc:
        logger.debug(f"VisionOCRProcessor-Registry nicht verfügbar ({reason}): {exc}")

    released_ocr = 0
    for ocr in ocr_instances:
        try:
            if getattr(ocr, "cleanup", None) is not None:
                ocr.cleanup()
                released_ocr += 1
        except Exception as exc:
            logger.debug(f"OCR-Cleanup fehlgeschlagen ({reason}): {exc}")
    if released_ocr:
        freed.append(f"easyocr x{released_ocr}")

    # 3) Aggressiv GC + CUDA-Cache freigeben, damit VRAM wirklich frei wird
    gc.collect()
    _empty_cuda_cache()

    if freed:
        logger.info(
            f"🧹 Kühle AUX-Modelle freigegeben ({reason}): {', '.join(freed)} "
            f"— VRAM der AUX-GPU für LLM-Query-Path frei"
        )
    else:
        logger.debug(
            f"AUX-Release ({reason}): nichts zu entladen "
            f"(kalte Modelle waren nicht geladen)"
        )
    return freed