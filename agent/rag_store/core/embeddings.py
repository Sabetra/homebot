"""
Embedding Manager Module for RAG Store
=======================================

Extrahiert aus unified_rag_store.py (Iteration 6, Oktober 2025)

Verwaltet alle Text-Embedding-Operationen:
- HuggingFace Sentence Transformers
- GPU-Optimierung (CUDA Support)
- Batch Processing
- L2-Normalisierung

REMOVED in Iteration 6 (ungenutzte Provider):
- ❌ OpenAI API (text-embedding-3-small/large)
- ❌ Voyage AI API (voyage-large-2)
- ❌ Random-Indexing Fallback (gefährlich, Silent-Failure)

Design-Prinzip: FAIL-FAST statt Silent-Fallback
→ Wenn Model nicht geladen, sofort Exception werfen
→ Keine falschen Dimensionen durch Fallback-Embeddings
"""

from __future__ import annotations

import os
import logging
from typing import Any, List, Optional, TYPE_CHECKING
import numpy as np

# Import ProcessingConfig from utils
try:
    from ..utils import ProcessingConfig
except ImportError:
    from agent.rag_store.utils import ProcessingConfig

logger = logging.getLogger(__name__)

# PyTorch für GPU-Support
try:
    import torch
    TORCH_AVAILABLE = True
    CUDA_AVAILABLE = torch.cuda.is_available() if hasattr(torch, 'cuda') else False
except ImportError:
    torch = None  # type: ignore[assignment]
    TORCH_AVAILABLE = False
    CUDA_AVAILABLE = False

# Type alias for SentenceTransformer (resolved at runtime)
SentenceTransformer: Any = None


def _check_sentence_transformers_available():
    """
    Runtime-Check für sentence-transformers (nicht beim Module Load!)
    
    Returns:
        tuple: (SentenceTransformer class or None, bool available)
    """
    try:
        # Import zur RUNTIME, nicht beim Module Load
        from sentence_transformers import SentenceTransformer
        return SentenceTransformer, True
    except ImportError:
        return None, False


class EmbeddingManager:
    """
    Verwaltet Text-Embedding-Generierung für RAG Store
    
    Features:
    - HuggingFace Sentence Transformers
    - Automatische GPU/CPU-Auswahl
    - Batch Processing mit konfigurierbarer Größe
    - L2-Normalisierung
    - Fail-fast bei Fehlern (keine Silent-Fallbacks)
    
    Supported Models:
    - intfloat/multilingual-e5-large (1024 dim, German/multilingual)
    - BAAI/bge-large-en-v1.5 (1024 dim, English-optimized)
    - sentence-transformers/paraphrase-multilingual-mpnet-base-v2 (768 dim)
    
    Usage:
        >>> manager = EmbeddingManager(model_name="intfloat/multilingual-e5-large")
        >>> embeddings = manager.embed_texts(["Hello", "World"])
        >>> query_emb = manager.embed_query("Search query")
    
    Raises:
        RuntimeError: Wenn Model nicht geladen werden kann
        ImportError: Wenn SentenceTransformers nicht installiert
    """
    
    # Unterstützte Modelle mit Metadaten
    SUPPORTED_MODELS = {
        "multilingual-e5-large": {
            "full_name": "intfloat/multilingual-e5-large",
            "dim": 1024,
            "description": "Best for German/multilingual content"
        },
        "intfloat/multilingual-e5-large": {
            "full_name": "intfloat/multilingual-e5-large",
            "dim": 1024,
            "description": "Best for German/multilingual content"
        },
        "bge-large-en-v1.5": {
            "full_name": "BAAI/bge-large-en-v1.5",
            "dim": 1024,
            "description": "English-optimized"
        },
        "BAAI/bge-large-en-v1.5": {
            "full_name": "BAAI/bge-large-en-v1.5",
            "dim": 1024,
            "description": "English-optimized"
        },
        "paraphrase-multilingual-mpnet-base-v2": {
            "full_name": "sentence-transformers/paraphrase-multilingual-mpnet-base-v2",
            "dim": 768,
            "description": "Smaller, faster multilingual model"
        }
    }
    
    def __init__(
        self,
        model_name: str = "multilingual-e5-large",
        device: Optional[str] = None,
        config: Optional[ProcessingConfig] = None,
        debug: bool = False
    ):
        """
        Initialisiert EmbeddingManager
        
        Args:
            model_name: HuggingFace Model-Name oder Alias
            device: "cuda" oder "cpu" (None = auto-detect)
            config: Performance-Konfiguration
            debug: Debug-Modus aktivieren
            
        Raises:
            ImportError: Wenn SentenceTransformers nicht verfügbar
            RuntimeError: Wenn Model nicht geladen werden kann
        """
        # RUNTIME CHECK statt Top-Level Import Check
        global SentenceTransformer
        SentenceTransformer, st_available = _check_sentence_transformers_available()
        
        if not st_available:
            raise ImportError(
                "SentenceTransformers nicht installiert!\n"
                "Installation: pip install sentence-transformers"
            )
        
        self.model_name = model_name
        self.config = config or ProcessingConfig()
        self.debug = debug or os.getenv("RAG_DEBUG", "").lower() in {"1", "true", "yes", "on"}
        
        # Device-Auswahl (Dual-GPU: AUX-GPU via utils.gpu_devices)
        if device:
            self.device = device
        elif CUDA_AVAILABLE:
            try:
                from utils.gpu_devices import get_placement
                self.device = get_placement().aux_device_string
            except Exception:
                self.device = "cuda"
        else:
            self.device = "cpu"
        
        # Model laden
        self.model: Any = None
        self._dim: int = 1024  # Default, wird in _setup_model überschrieben
        self._setup_model()
        
        if self.debug:
            logger.info(f"✅ EmbeddingManager initialized: {model_name} on {self.device}")
    
    def _setup_model(self) -> None:
        """
        Lädt HuggingFace Sentence Transformer via EmbeddingSingleton.
        
        Uses the process-wide singleton to prevent double model loading.
        FP16 on CUDA is handled by the singleton (SOTA VRAM optimization).
        
        Raises:
            RuntimeError: Wenn Model nicht geladen werden kann
        """
        try:
            # Resolve full model name
            if self.model_name in self.SUPPORTED_MODELS:
                full_name = str(self.SUPPORTED_MODELS[self.model_name]["full_name"])
                self._dim = int(str(self.SUPPORTED_MODELS[self.model_name]["dim"]))
            else:
                full_name = self.model_name
                logger.warning(f"⚠️ Model '{self.model_name}' nicht in SUPPORTED_MODELS")
            
            if self.debug:
                logger.info(f"🔄 Loading SentenceTransformer via EmbeddingSingleton: {full_name}")
            
            # ── Use EmbeddingSingleton: ONE model copy process-wide ──
            from utils.embedding_singleton import EmbeddingSingleton
            singleton = EmbeddingSingleton()
            
            if not singleton.is_loaded() or singleton.model_name != full_name:
                success = singleton.load_model(full_name)
                if not success:
                    raise RuntimeError(f"EmbeddingSingleton failed to load '{full_name}'")
            
            # Share the model reference (NOT a copy) — FP16 if on CUDA
            self.model = singleton.model
            self.device = singleton.device or self.device
            
            # Read dimension from loaded model
            if hasattr(self.model, 'get_sentence_embedding_dimension'):
                dim_value = self.model.get_sentence_embedding_dimension()
                if dim_value is not None:
                    self._dim = int(dim_value)
            
            if self.debug:
                logger.info(f"✅ Model loaded via Singleton: {full_name}")
                logger.info(f"   ├─ Device: {self.device}")
                logger.info(f"   ├─ Dimension: {self._dim}")
                logger.info(f"   └─ Batch Size: {self.config.gpu_batch_size}")
                
        except Exception as e:
            error_msg = (
                f"❌ Failed to load embedding model '{self.model_name}'!\n"
                f"Error: {e}\n"
                f"Ensure the model is installed: pip install sentence-transformers"
            )
            logger.error(error_msg)
            raise RuntimeError(error_msg) from e
    
    def embed_texts(self, texts: List[str]) -> np.ndarray:
        """
        Generiert Embeddings für Text-Liste
        
        Args:
            texts: Liste von Texten
            
        Returns:
            np.ndarray: (N, dim) float32 embeddings, L2-normalisiert
            
        Raises:
            RuntimeError: Wenn Model nicht geladen
        """
        if not self.model:
            raise RuntimeError(
                "Embedding model not loaded! "
                f"Model '{self.model_name}' failed to initialize."
            )
        
        if not texts:
            return np.zeros((0, self._dim), dtype=np.float32)
        
        try:
            # GPU-optimierte Batch-Verarbeitung
            embeddings = self.model.encode(
                texts,
                convert_to_numpy=True,
                normalize_embeddings=True,  # L2-Normalisierung
                batch_size=self.config.gpu_batch_size,
                show_progress_bar=False
            )
            
            result: np.ndarray = embeddings.astype("float32")
            return result
            
        except Exception as e:
            logger.error(f"❌ Embedding generation failed: {e}")
            raise RuntimeError(f"Failed to generate embeddings: {e}") from e
    
    def embed_query(self, query: str) -> np.ndarray:
        """
        Convenience wrapper für einzelne Query
        
        Args:
            query: Suchquery
            
        Returns:
            np.ndarray: (dim,) float32 embedding, L2-normalisiert
        """
        result: np.ndarray = self.embed_texts([query])[0]
        return result
    
    @property
    def dimension(self) -> int:
        """Embedding-Dimensionalität"""
        return self._dim
    
    @property
    def is_gpu_enabled(self) -> bool:
        """Prüft ob GPU verwendet wird (auch 'cuda:N' für Dual-GPU)"""
        return bool(self.device) and self.device.startswith("cuda") and CUDA_AVAILABLE
    
    def get_info(self) -> dict:
        """Gibt Informationen über aktuelles Setup zurück"""
        return {
            "model_name": self.model_name,
            "dimension": self._dim,
            "device": self.device,
            "gpu_available": CUDA_AVAILABLE,
            "gpu_enabled": self.is_gpu_enabled,
            "batch_size": self.config.gpu_batch_size,
            "model_loaded": self.model is not None
        }
    
    def __repr__(self) -> str:
        return f"EmbeddingManager(model='{self.model_name}', dim={self._dim}, device='{self.device}')"
