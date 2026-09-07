"""
RAG Store - Modular Implementation
===================================

State-of-the-art modular RAG (Retrieval-Augmented Generation) Store.

Modules:
    - utils: Resource management, memory management, configuration, batch processing
    - core: Database, embeddings, search (Iteration 5+)
    - advanced: PDF processing, knowledge graphs, upsert (coming soon)
"""

# Version
__version__ = "3.0.0-modular"

# Utils (Iteration 1-2)
from .utils import (
    ResourceManager,
    ManagedResource,
    managed_resource,
    _resource_manager,
    MemoryManager,
    _memory_manager,
    calculate_triple_hash,
    ProcessingConfig,
    chunk_list,
    batch_embed_texts,
    batch_sql_load,
    batch_insert,
)

# Core Modules (Iteration 5+)
from .core import (
    DatabaseManager,
    RAGQualityManager,
)

# Lazy import UnifiedRagStore to avoid circular import
# (unified_rag_store imports from rag_store, rag_store imports unified_rag_store)
# Uses PEP 562 __getattr__ so `from agent.rag_store import RagStore` triggers
# the lazy load instead of returning None.
_UnifiedRagStore = None
_RagStore = None


def _get_unified_rag_store():
    """Lazy import to avoid circular dependency."""
    global _UnifiedRagStore, _RagStore
    if _UnifiedRagStore is None:
        try:
            from agent.unified_rag_store import UnifiedRagStore as _URS
            _UnifiedRagStore = _URS
            _RagStore = _URS  # Backward compatibility alias
        except ImportError as e:
            import logging
            logging.warning(f"Could not import UnifiedRagStore: {e}")
    return _UnifiedRagStore


def __getattr__(name: str):
    """PEP 562: module-level __getattr__ for lazy loading."""
    if name in ("UnifiedRagStore", "RagStore"):
        _get_unified_rag_store()
        val = _UnifiedRagStore if name == "UnifiedRagStore" else _RagStore
        if val is not None:
            return val
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    # Utils
    'ResourceManager',
    'ManagedResource',
    'managed_resource',
    '_resource_manager',
    'MemoryManager',
    '_memory_manager',
    'calculate_triple_hash',
    'ProcessingConfig',
    # Batch Processing
    'chunk_list',
    'batch_embed_texts',
    'batch_sql_load',
    'batch_insert',
    # Core Modules
    'DatabaseManager',
    'RAGQualityManager',
    # Main RAG Store
    'RagStore',
    'UnifiedRagStore',
]
