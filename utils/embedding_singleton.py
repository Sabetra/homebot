#!/usr/bin/env python3
"""
SINGLETON EMBEDDING MODEL MANAGER
===================================

Zentrale Verwaltung des Embedding-Models als Singleton.
Verhindert mehrfaches Laden und erlaubt Zugriff von allen Modulen.

WICHTIG: 
- Nur EINE Instanz des Embedding-Models im Speicher
- Wird von normalem Chat UND Psycho-Modul genutzt
- STRIKTE TRENNUNG: Nur das Model wird geteilt, NICHT die Daten!

Author: AI System Evolution
Date: 2025-10-31
"""

# 🛡️ CRITICAL: Pre-import yaml BEFORE any imports that might trigger sentence_transformers
# This prevents the yaml circular import issue with streamlit
import yaml  # type: ignore[import-untyped]

import logging
import threading
from typing import Optional, List, Union, cast, TYPE_CHECKING
import numpy as np
from numpy.typing import NDArray
import os
from pathlib import Path
from utils.runtime_policy import parse_bool_env

# Offline-Flags nur in explizitem Local-Only-Modus setzen.
if parse_bool_env("APP_LOCAL_ONLY", "0"):
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

# TYPE_CHECKING is only True during static analysis, not at runtime
# This prevents the RecursionError while still providing type hints
if TYPE_CHECKING:
    from typing import Type

logger = logging.getLogger(__name__)

# Global Singleton
_embedding_model_instance = None
_embedding_model_lock = threading.RLock()


class EmbeddingSingleton:
    """
    Singleton Wrapper für SentenceTransformer Embedding Model
    
    Features:
    - Thread-safe Singleton Pattern
    - Lazy Loading (nur bei Bedarf)
    - Automatisches Fallback auf CPU wenn GPU nicht verfügbar
    - Embedding-Dimension: 384 (all-MiniLM-L6-v2)
    """
    
    # Fix RecursionError: Use None without type annotation, or use __future__ annotations
    _instance = None  # type: Optional[EmbeddingSingleton]
    _initialized: bool = False
    
    def __new__(cls):
        """Thread-safe Singleton Pattern"""
        with _embedding_model_lock:
            if cls._instance is None:
                instance = super().__new__(cls)
                cls._instance = instance
            return cls._instance
    
    def __init__(self):
        """Initialisiert Singleton (nur einmal)"""
        # Verhindere mehrfache Initialisierung
        if self._initialized:
            return
        
        with _embedding_model_lock:
            if self._initialized:
                return
            
            self.model = None
            self.model_name = None
            self.embedding_dim = None
            self.device = None
            self._initialized = True
            
            logger.info("✅ EmbeddingSingleton initialisiert (Model noch nicht geladen)")
    
    def load_model(self, model_name: str = 'intfloat/multilingual-e5-large', force_reload: bool = False) -> bool:
        """
        Lädt das Embedding-Model (nur einmal, außer force_reload=True)
        
        ⚠️ WICHTIG: Default-Model MUSS mit unified_rag_store.py übereinstimmen!
        → intfloat/multilingual-e5-large (1024 dim) für beste deutsche Texte
        
        Upgrade-Pfad (2024/2025 SOTA, selbe Dimension):
        → intfloat/multilingual-e5-large-instruct (1024 dim, instruction-tuned)
        → BAAI/bge-m3 (1024 dim, multi-granularity retrieval)
        
        Steuerung über Umgebungsvariable EMBEDDING_MODEL_NAME:
            set EMBEDDING_MODEL_NAME=intfloat/multilingual-e5-large-instruct
        
        Args:
            model_name: Name des SentenceTransformer Models
            force_reload: Erzwinge Neu-Laden auch wenn bereits geladen
            
        Returns:
            True wenn erfolgreich geladen
        """
        # Allow env-var override for easy model switching without code changes
        env_model = os.getenv("EMBEDDING_MODEL_NAME")
        if env_model and env_model.strip():
            model_name = env_model.strip()
            logger.info(f"🔧 Embedding-Modell über EMBEDDING_MODEL_NAME überschrieben: {model_name}")
        with _embedding_model_lock:
            # Model bereits geladen?
            if self.model is not None and not force_reload:
                logger.info(f"✅ Embedding-Model bereits geladen: {self.model_name}")
                return True
            
            try:
                # SENTENCE_TRANSFORMERS_HOME bleibt als Cache-Override unterstützt
                # (Vertrag: tests/test_embedding_singleton_local_cache.py).
                # Default ist das Repo-Verzeichnis models_cache/sentence_transformers.
                env_cache_home = os.environ.get("SENTENCE_TRANSFORMERS_HOME", "").strip()
                if env_cache_home:
                    cache_dir = str(Path(env_cache_home).expanduser())
                else:
                    cache_dir = str(Path(__file__).resolve().parents[1] / "models_cache" / "sentence_transformers")

                cache_path = Path(cache_dir).expanduser()
                cache_path.mkdir(parents=True, exist_ok=True)

                logger.info(f"📁 Cache-Verzeichnis konfiguriert: {cache_path}")

                def _candidate_cache_paths() -> List[Path]:
                    candidates: List[Path] = []
                    for suffix in [
                        model_name.replace('/', '_'),
                        f"models--{model_name.replace('/', '--')}",
                        f"models--{model_name.replace('/', '--')}--snapshots",
                    ]:
                        candidates.append(cache_path / suffix)
                    return candidates

                cache_candidates = _candidate_cache_paths()
                cache_available = any(candidate.exists() for candidate in cache_candidates)
                if cache_available:
                    logger.info(f"✅ Modell im Cache gefunden - wird aus lokalem Cache geladen")
                else:
                    logger.info(f"⬇️ Modell nicht im Cache - wird heruntergeladen und gecacht")

                use_local_files_only = parse_bool_env("APP_LOCAL_ONLY", "0") or cache_available
                if use_local_files_only:
                    os.environ.setdefault("HF_HUB_OFFLINE", "1")
                    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
                
                from sentence_transformers import SentenceTransformer
                
                logger.info(f"🔄 Lade Embedding-Model: {model_name}")
                
                # Auto-Device-Selection (AUX-GPU via utils.gpu_devices;
                # 4090 bleibt exklusiv für das LLM, s. docs/RTX4090_RYZEN9_GUIDE.md)
                import torch
                if torch.cuda.is_available():
                    from utils.gpu_devices import get_placement
                    device = get_placement().aux_device_string
                else:
                    device = 'cpu'
                
                # ── SOTA VRAM Optimization (Günther et al. 2024) ──────────
                # FP16 embeddings are indistinguishable from FP32 on ALL
                # MTEB tasks (<0.1% difference) but halve VRAM usage.
                # e5-large FP32 ≈ 2.2 GB → FP16 ≈ 1.1 GB on GPU.
                # This leaves ~4-6 GB headroom for KV-cache growth on a
                # 24 GB GPU running a 14 GB Q4_K_M LLM.
                use_fp16 = (device == 'cuda')
                model_kwargs = {}
                if use_fp16:
                    model_kwargs['dtype'] = torch.float16
                    logger.info("⚡ FP16-Modus aktiviert für VRAM-Optimierung")
                
                # Model laden
                self.model = SentenceTransformer(
                    model_name,
                    device=device,
                    cache_folder=str(cache_path),
                    local_files_only=use_local_files_only,
                    model_kwargs=model_kwargs,
                )
                
                # Double-ensure FP16 for older sentence-transformers versions
                # that may ignore model_kwargs
                if use_fp16:
                    try:
                        self.model.half()
                        logger.info("✅ Model auf FP16 konvertiert (VRAM halbiert)")
                    except Exception as fp16_err:
                        logger.warning(f"⚠️ FP16-Konvertierung fehlgeschlagen: {fp16_err}")
                
                self.model_name = model_name
                self.device = device
                self._use_fp16 = use_fp16
                self.embedding_dim = self.model.get_embedding_dimension()
                
                # Log VRAM after loading (explizit die AUX-GPU-Index abfragen)
                if isinstance(device, str) and device.startswith('cuda'):
                    try:
                        _dev_idx = int(device.split(':', 1)[1]) if ':' in device else 0
                        vram_alloc = torch.cuda.memory_allocated(_dev_idx) / (1024**3)
                        vram_reserved = torch.cuda.memory_reserved(_dev_idx) / (1024**3)
                        logger.info(
                            f"📊 AUX-GPU [{device}] VRAM nach Embedding-Load: "
                            f"allocated={vram_alloc:.2f} GB, reserved={vram_reserved:.2f} GB"
                        )
                    except Exception:
                        pass
                
                logger.info(f"✅ Embedding-Model geladen: {model_name}")
                logger.info(f"   Device: {device} {'(FP16)' if use_fp16 else '(FP32)'}")
                logger.info(f"   Embedding-Dim: {self.embedding_dim}")
                
                return True
                
            except ImportError:
                logger.error("❌ sentence-transformers nicht installiert!")
                logger.error("   Installieren Sie es mit: pip install sentence-transformers")
                return False
                
            except Exception as e:
                logger.error(f"❌ Fehler beim Laden des Embedding-Models: {e}")
                import traceback
                logger.error(traceback.format_exc())
                return False
    
    def encode(self, texts: Union[str, List[str]], batch_size: int = 32, 
               show_progress_bar: bool = False,
               defer_cache_cleanup: bool = False,
               normalize_embeddings: bool = False) -> NDArray[np.float64]:
        """
        Encodiert Texte zu Embeddings
        
        Args:
            texts: Text oder Liste von Texten
            batch_size: Batch-Größe für Encoding
            show_progress_bar: Progress-Bar anzeigen
            defer_cache_cleanup: Wenn True, wird torch.cuda.empty_cache() NICHT
                nach diesem Encode aufgerufen. Spart ~1-5ms GPU-Synchronisation
                pro Call. Für Bulk-Operationen auf True setzen und am Ende
                manuell flush_cuda_cache() aufrufen.
            normalize_embeddings: Wenn True, werden Embeddings L2-normalisiert
                (Länge 1). Wichtig für Cosine-Similarity-basierte Vergleiche.
            
        Returns:
            Numpy Array mit Embeddings (shape: [len(texts), embedding_dim])
        """
        if self.model is None:
            logger.warning("⚠️ Model nicht geladen - lade automatisch...")
            if not self.load_model():
                raise RuntimeError("Embedding-Model konnte nicht geladen werden!")
            
            # Type-Assertion: Nach erfolgreichem load_model() ist self.model garantiert nicht None
            if self.model is None:
                raise RuntimeError("Model ist None trotz erfolgreichem load_model() - sollte nie passieren!")
        
        # Single Text zu Liste konvertieren
        text_list: List[str]
        if isinstance(texts, str):
            text_list = [texts]
        else:
            text_list = texts
        
        # Encoding
        embeddings = self.model.encode(
            text_list,
            batch_size=batch_size,
            show_progress_bar=show_progress_bar,
            convert_to_numpy=True,
            normalize_embeddings=normalize_embeddings
        )
        
        # ── SOTA: Free transient CUDA memory after encode ─────────
        # Prevents VRAM fragmentation when embedding is called between
        # LLM inference rounds.  Cost: ~1-5ms (GPU sync).
        # ★ GPU-OPT: During bulk operations (PDF→RAG+KG), skip per-call
        # cleanup to avoid N×1-5ms GPU synchronization stalls.
        # Caller sets defer_cache_cleanup=True and calls flush_cuda_cache()
        # once at the end of the bulk operation.
        if self._is_cuda_device() and not defer_cache_cleanup:
            try:
                import torch
                torch.cuda.empty_cache()
            except Exception:
                pass
        
        # Type-safe return: model.encode mit convert_to_numpy=True gibt ndarray zurück
        return cast(NDArray[np.float64], embeddings)
    
    def compute_similarity(self, text1: str, text2: str) -> float:
        """
        Berechnet Cosine-Similarity zwischen zwei Texten
        
        Args:
            text1: Erster Text
            text2: Zweiter Text
            
        Returns:
            Similarity Score (0.0 - 1.0)
        """
        embeddings = self.encode([text1, text2])
        
        # Cosine Similarity
        from numpy.linalg import norm
        similarity = np.dot(embeddings[0], embeddings[1]) / (norm(embeddings[0]) * norm(embeddings[1]))
        
        return float(similarity)
    
    def is_loaded(self) -> bool:
        """Prüft ob Model geladen ist"""
        return self.model is not None
    
    def _is_cuda_device(self) -> bool:
        """True, wenn das Modell auf einer CUDA-GPU läuft ('cuda' oder 'cuda:<idx>')."""
        return isinstance(self.device, str) and self.device.startswith('cuda')


    def flush_cuda_cache(self):
        """Explicitly free transient CUDA memory from PyTorch's caching allocator.
        
        Call this ONCE after bulk embedding operations where defer_cache_cleanup=True
        was used. Frees up to ~500MB of cached VRAM.
        """
        if self._is_cuda_device():
            try:
                import torch
                torch.cuda.empty_cache()
                logger.debug("✅ CUDA cache flushed after bulk embedding")
            except Exception:
                pass
    
    def unload_model(self):
        """Entlädt das Model aus dem Speicher"""
        with _embedding_model_lock:
            if self.model is not None:
                del self.model
                self.model = None
                self.model_name = None
                self.embedding_dim = None
                self.device = None
                logger.info("✅ Embedding-Model entladen")


def get_embedding_model() -> EmbeddingSingleton:
    """
    Factory-Funktion für globalen Zugriff auf Embedding-Singleton
    
    Returns:
        EmbeddingSingleton Instanz
    """
    return EmbeddingSingleton()


# Convenience-Funktionen für direkten Zugriff
def encode_texts(texts: Union[str, List[str]], batch_size: int = 32) -> NDArray[np.float64]:
    """Encodiert Texte zu Embeddings (nutzt Singleton)"""
    model = get_embedding_model()
    return model.encode(texts, batch_size=batch_size)


def compute_text_similarity(text1: str, text2: str) -> float:
    """Berechnet Similarity zwischen zwei Texten (nutzt Singleton)"""
    model = get_embedding_model()
    return model.compute_similarity(text1, text2)
