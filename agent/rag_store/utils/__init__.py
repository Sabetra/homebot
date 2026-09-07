"""
RAG Store Utilities
===================

Utility modules for RAG Store:
- resource_manager: Thread-safe resource management
- memory: Memory management and optimization
- config: Configuration classes
- batch: Batch processing utilities
"""

from .resource_manager import (
    ResourceManager,
    ManagedResource,
    managed_resource,
    _resource_manager
)

from .memory import (
    MemoryManager,
    _memory_manager,
    calculate_triple_hash
)

from .config import ProcessingConfig

from .batch import (
    chunk_list,
    batch_embed_texts,
    batch_sql_load,
    batch_insert
)

__all__ = [
    # Resource Management
    'ResourceManager',
    'ManagedResource',
    'managed_resource',
    '_resource_manager',
    # Memory Management
    'MemoryManager',
    '_memory_manager',
    'calculate_triple_hash',
    # Configuration
    'ProcessingConfig',
    # Batch Processing
    'chunk_list',
    'batch_embed_texts',
    'batch_sql_load',
    'batch_insert',
]
