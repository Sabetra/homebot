"""
Memory Manager - Memory Monitoring and Cleanup
===============================================

Provides memory monitoring and automatic cleanup functionality.

Classes:
    - MemoryManager: Memory management for RAG Store
    
Functions:
    - calculate_triple_hash: Calculate unique hash for knowledge graph triples
"""

import logging
import gc
import hashlib
import psutil

logger = logging.getLogger(__name__)


class MemoryManager:
    """Memory-Management für RAG-Store"""
    
    def __init__(self, memory_limit_mb: int = 8000):
        self.memory_limit_mb = memory_limit_mb
    
    def get_memory_mb(self) -> float:
        """Aktueller Memory-Verbrauch in MB"""
        try:
            return float(psutil.Process().memory_info().rss / (1024**2))
        except Exception as e:
            logger.warning(f"Could not get memory usage: {e}")
            return 0.0
    
    def check_memory_and_cleanup(self, operation: str = "") -> bool:
        """
        Memory-Check mit automatischem Cleanup
        
        Args:
            operation: Optional description of current operation
            
        Returns:
            True if memory is below limit after cleanup, False otherwise
        """
        current_mb = self.get_memory_mb()
        
        if current_mb > self.memory_limit_mb:
            logger.warning(
                f"🧠 Memory-Limit: {current_mb:.1f}MB > {self.memory_limit_mb}MB ({operation})"
            )
            
            # Aggressive Garbage Collection
            for i in range(3):
                collected = gc.collect()
                if collected > 0:
                    logger.info(f"🧹 GC Pass {i+1}: {collected} objects freed")
            
            # Memory nach Cleanup
            new_mb = self.get_memory_mb()
            freed_mb = current_mb - new_mb
            logger.info(f"✅ Memory nach Cleanup: {new_mb:.1f}MB (freed: {freed_mb:.1f}MB)")
            
            return new_mb < self.memory_limit_mb
        
        return True
    
    def force_cleanup(self) -> float:
        """
        Führt aggressive Garbage Collection durch
        
        Returns:
            Memory freed in MB
        """
        mem_before = self.get_memory_mb()
        
        # Multiple GC passes
        for _ in range(3):
            gc.collect()
        
        mem_after = self.get_memory_mb()
        freed = mem_before - mem_after
        
        if freed > 0:
            logger.info(f"🧹 Forced cleanup: {freed:.1f}MB freed")
        
        return freed


# Global Memory Manager
_memory_manager = MemoryManager()


def calculate_triple_hash(subject: str, predicate: str, obj: str) -> str:
    """
    Berechnet einen eindeutigen Hash für ein Triple (subject, predicate, object).
    Verwendet MD5 für Konsistenz mit bestehenden Hashes aus fix_kg_duplicates.py.
    
    Args:
        subject: Subject of the triple
        predicate: Predicate/relation of the triple
        obj: Object of the triple
        
    Returns:
        MD5 hash of the triple
        
    Example:
        >>> calculate_triple_hash("Berlin", "is_capital_of", "Germany")
        'a1b2c3d4e5f6...'
    """
    # Normalisiere die Werte (entferne nur Leerzeichen, KEIN lowercase für Kompatibilität)
    normalized_subject = str(subject).strip() if subject else ""
    normalized_predicate = str(predicate).strip() if predicate else ""
    normalized_object = str(obj).strip() if obj else ""
    
    # Erstelle einen eindeutigen String für das Triple (gleich wie fix_kg_duplicates.py)
    triple_content = f"{normalized_subject}|{normalized_predicate}|{normalized_object}"
    
    # Berechne MD5 Hash für Kompatibilität mit bestehenden Hashes
    return hashlib.md5(triple_content.encode('utf-8')).hexdigest()


# Public API
__all__ = [
    'MemoryManager',
    '_memory_manager',
    'calculate_triple_hash',
]
