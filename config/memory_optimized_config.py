"""
MEMORY-OPTIMIZED RAG STORE CONFIGURATION
========================================

Reduziert Memory-Verbrauch des RAG Stores
"""

# Memory-optimierte Konfiguration
MEMORY_OPTIMIZED_CONFIG = {
    # Reduzierte Embedding-Dimension
    'embedding_model': 'all-MiniLM-L6-v2',  # 384 dim statt 768
    
    # Kleinere Batch-Größen
    'batch_size': 8,  # statt 32
    'chunk_size': 400,  # statt 800
    'max_chunks_per_doc': 50,  # Limit chunks
    
    # Connection Pool reduzieren
    'max_connections': 3,  # statt 10
    
    # Memory Cleanup Intervalle
    'cleanup_interval': 10,  # Nach 10 Operationen
    
    # Disable Features die Memory kosten
    'enable_knowledge_graph': False,
    'enable_advanced_chunking': False,
    'enable_parallel_processing': False,
    
    # SQLite Memory Optimierungen
    'sqlite_cache_size': 2000,  # Reduziert von Standard
    'sqlite_temp_store': 'memory',
    'sqlite_journal_mode': 'DELETE',  # Wie schon implementiert
}

def apply_memory_config(rag_store):
    """Wendet Memory-Konfiguration auf RAG Store an"""
    if hasattr(rag_store, 'embedding_model'):
        # Leichteres Embedding-Modell
        rag_store.embedding_model = MEMORY_OPTIMIZED_CONFIG['embedding_model']
    
    if hasattr(rag_store, 'batch_size'):
        rag_store.batch_size = MEMORY_OPTIMIZED_CONFIG['batch_size']
    
    print("✅ Memory-optimierte RAG Store Konfiguration angewendet")
    return rag_store
