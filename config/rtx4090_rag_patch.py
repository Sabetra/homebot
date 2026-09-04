
# RTX 4090 + Ryzen 9 5950X Performance Patch
import torch
import os

class RTX4090PerformancePatch:
    @staticmethod
    def apply_to_rag_store(rag_store):
        """Wendet RTX 4090 + Ryzen 9 5950X Optimierungen an"""
        
        # GPU-Optimierungen
        if hasattr(rag_store, 'embedding_model') and torch.cuda.is_available():
            rag_store.embedding_model.to('cuda')
            if hasattr(rag_store.embedding_model, 'eval'):
                rag_store.embedding_model.eval()
                
        # CPU-Parallelisierung
        if hasattr(rag_store, 'config'):
            rag_store.config.num_workers = min(24, os.cpu_count())
            rag_store.config.batch_size = 1024 if torch.cuda.is_available() else 128
            rag_store.config.enable_parallel_processing = True
            
        # Memory-Optimierungen
        if hasattr(rag_store, 'memory_limit'):
            rag_store.memory_limit = 48 * 1024 * 1024 * 1024  # 48GB
            
        print("🔥 RTX 4090 + Ryzen 9 5950X Optimierungen aktiviert!")
        return rag_store
