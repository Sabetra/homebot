"""
Processing Config - Configuration Classes
=========================================

Configuration classes for RAG Store performance optimization.

Classes:
    - ProcessingConfig: Configuration for performance optimizations
"""

from dataclasses import dataclass


@dataclass
class ProcessingConfig:
    """
    Konfiguration für Performance-Optimierungen
    
    Optimiert für moderne Hardware:
    - Multi-Core CPUs (z.B. Ryzen 9 5950X mit 32 Threads)
    - Moderne GPUs
    - SQLite WAL-Mode für bessere Concurrency
    
    Attributes:
        max_workers_pdf: Maximum workers for PDF processing
        max_workers_embedding: Maximum workers for embedding generation
        batch_size_embedding: Batch size for embedding operations
        gpu_batch_size: Batch size when using GPU
        use_gpu: Whether to use GPU acceleration if available
        sqlite_wal_mode: Use SQLite WAL mode for better concurrency
        connection_pool_size: Size of SQLite connection pool
        fallback_to_sequential: Fallback to sequential processing on errors
    """
    max_workers_pdf: int = 24  # Optimiert für Ryzen 9 5950X (32 Threads)
    max_workers_embedding: int = 8  # Erhöht für bessere GPU/CPU Balance
    batch_size_embedding: int = 64  # Größere Batches für bessere Throughput
    gpu_batch_size: int = 256  # Erhöht für moderne GPUs
    use_gpu: bool = True
    sqlite_wal_mode: bool = True
    connection_pool_size: int = 16  # Mehr Verbindungen für parallele Zugriffe
    fallback_to_sequential: bool = True


# Public API
__all__ = [
    'ProcessingConfig',
]
