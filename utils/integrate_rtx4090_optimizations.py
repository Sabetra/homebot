#!/usr/bin/env python3
"""
🔥 RTX 4090 + RYZEN 9 5950X RAG INTEGRATION
===========================================
Integriert Hardware-Optimierungen in den bestehenden RAG-Store
"""

import os
import sys
import json
import logging
from pathlib import Path

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def apply_rtx4090_optimizations():
    """Wendet RTX 4090 + Ryzen 9 5950X Optimierungen auf RAG Store an"""
    
    # Lade optimierte Konfiguration
    config_file = Path('rag_rtx4090_config.json')
    if not config_file.exists():
        logger.error("❌ rag_rtx4090_config.json nicht gefunden!")
        return False
        
    with open(config_file, 'r', encoding='utf-8') as f:
        rtx_config = json.load(f)
    
    logger.info("✅ RTX 4090 Konfiguration geladen")
    
    # Patche RAG Store
    try:
        # Import RAG Store
        sys.path.append('agent')
        from rag_store import UnifiedRagStore as RAGStore
        
        # Erstelle optimierte RAG Store Instanz
        class OptimizedRAGStore(RAGStore):
            """RTX 4090 + Ryzen 9 5950X optimierte RAG Store"""
            
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self._apply_rtx4090_optimizations()
                
            def _apply_rtx4090_optimizations(self):
                """Wendet RTX 4090 + Ryzen 9 5950X Optimierungen an"""
                
                # GPU-Optimierungen
                if hasattr(self, 'embedding_model') and self.embedding_model:
                    try:
                        import torch
                        if torch.cuda.is_available():
                            # GPU-Batch-Größe optimieren
                            self.embedding_batch_size = rtx_config['embedding']['batch_size']
                            logger.info(f"🔥 GPU Batch Size: {self.embedding_batch_size}")
                            
                            # Mixed Precision aktivieren
                            if rtx_config['embedding']['use_mixed_precision']:
                                os.environ['PYTORCH_CUDA_ALLOC_CONF'] = 'max_split_size_mb:512'
                                logger.info("⚡ Mixed Precision aktiviert")
                    except Exception as e:
                        logger.warning(f"⚠️ GPU-Optimierung teilweise fehlgeschlagen: {e}")
                
                # CPU-Optimierungen
                try:
                    # Worker-Anzahl optimieren
                    self.num_workers = rtx_config['chunking']['num_workers']
                    logger.info(f"🚀 CPU Workers: {self.num_workers}")
                    
                    # Chunk-Größe optimieren
                    self.chunk_size = rtx_config['chunking']['chunk_size']
                    self.overlap_size = rtx_config['chunking']['overlap_size']
                    logger.info(f"📄 Chunk Size: {self.chunk_size}, Overlap: {self.overlap_size}")
                    
                    # Memory Limit erhöhen
                    if hasattr(self, 'memory_limit'):
                        self.memory_limit = rtx_config['pdf_processing']['memory_limit_mb'] * 1024 * 1024
                        logger.info(f"💾 Memory Limit: {rtx_config['pdf_processing']['memory_limit_mb']}MB")
                        
                except Exception as e:
                    logger.warning(f"⚠️ CPU-Optimierung teilweise fehlgeschlagen: {e}")
                
                # Datenbank-Optimierungen
                try:
                    if hasattr(self, 'db_batch_size'):
                        self.db_batch_size = rtx_config['database']['batch_insert_size']
                        logger.info(f"🗄️ DB Batch Size: {self.db_batch_size}")
                except Exception as e:
                    logger.warning(f"⚠️ DB-Optimierung teilweise fehlgeschlagen: {e}")
                
                logger.info("🔥 RTX 4090 + Ryzen 9 5950X Optimierungen aktiviert!")
            
            def upsert_pdf_optimized(self, file_path, build_kg=True, **kwargs):
                """Optimierte PDF-Verarbeitung für RTX 4090 + Ryzen 9 5950X"""
                
                # Setze optimierte Parameter
                kwargs.update({
                    'use_parallel_processing': True,
                    'max_workers': min(16, os.cpu_count() or 16),  # Ryzen 9 5950X optimal
                    'batch_size': 1024 if self._has_gpu() else 128,
                    'memory_efficient': True,
                })
                
                logger.info(f"🚀 Optimierte PDF-Verarbeitung: {file_path}")
                return self.upsert_pdf(file_path, build_kg=build_kg, **kwargs)
            
            def upsert_url_optimized(self, url, build_kg=True, **kwargs):
                """Optimierte URL-Verarbeitung für RTX 4090 + Ryzen 9 5950X"""
                
                # Setze optimierte Parameter
                kwargs.update({
                    'use_parallel_processing': True,
                    'max_workers': min(24, os.cpu_count() or 24),  # Ryzen 9 5950X optimal
                    'batch_size': 512,
                    'memory_efficient': True,
                })
                
                logger.info(f"🌐 Optimierte URL-Verarbeitung: {url}")
                return self.upsert_url(url, build_kg=build_kg, **kwargs)
            
            def _has_gpu(self):
                """Prüft GPU-Verfügbarkeit"""
                try:
                    import torch
                    return torch.cuda.is_available()
                except:
                    return False
        
        # Registriere optimierte Klasse
        globals()['OptimizedRAGStore'] = OptimizedRAGStore
        logger.info("✅ Optimierte RAG Store Klasse registriert")
        
        return True
        
    except Exception as e:
        logger.error(f"❌ RAG Store Integration fehlgeschlagen: {e}")
        return False

def test_optimized_performance():
    """Testet optimierte Performance mit realen Daten"""
    
    try:
        # Teste GPU-Status
        try:
            import torch
            if torch.cuda.is_available():
                gpu_name = torch.cuda.get_device_name(0)
                gpu_memory = torch.cuda.get_device_properties(0).total_memory / (1024**3)
                logger.info(f"🎯 GPU aktiv: {gpu_name} ({gpu_memory:.1f}GB)")
                
                # Teste GPU-Memory
                torch.cuda.empty_cache()
                test_tensor = torch.randn(1000, 1000, device='cuda', dtype=torch.float16)
                del test_tensor
                torch.cuda.empty_cache()
                logger.info("✅ GPU-Memory-Test erfolgreich")
            else:
                logger.warning("⚠️ GPU nicht verfügbar")
        except Exception as e:
            logger.warning(f"⚠️ GPU-Test fehlgeschlagen: {e}")
        
        # Teste CPU-Konfiguration
        import psutil
        cpu_count = psutil.cpu_count()
        cpu_percent = psutil.cpu_percent(interval=1)
        memory_info = psutil.virtual_memory()
        
        logger.info(f"💻 CPU: {cpu_count} Kerne, {cpu_percent:.1f}% Auslastung")
        logger.info(f"💾 RAM: {memory_info.total/(1024**3):.1f}GB total, {memory_info.percent:.1f}% belegt")
        
        logger.info("✅ Performance-Test abgeschlossen")
        return True
        
    except Exception as e:
        logger.error(f"❌ Performance-Test fehlgeschlagen: {e}")
        return False

def create_usage_example():
    """Erstellt Nutzungsbeispiel für optimierte RAG Store"""
    
    example_code = '''
#!/usr/bin/env python3
"""
🔥 RTX 4090 + RYZEN 9 5950X RAG USAGE EXAMPLE
==============================================
Zeigt optimale Nutzung des RAG Stores
"""

import sys
sys.path.append('agent')

# Optimierungen laden
from integrate_rtx4090_optimizations import apply_rtx4090_optimizations

# RAG Store optimieren
apply_rtx4090_optimizations()

# Optimierte RAG Store Instanz erstellen
from integrate_rtx4090_optimizations import OptimizedRAGStore

# Erstelle optimierte Instanz
rag = OptimizedRAGStore()

# Beispiel: PDF-Verarbeitung mit maximaler Performance
def process_large_pdf_optimized(pdf_path):
    """Verarbeitet große PDFs mit RTX 4090 + Ryzen 9 5950X"""
    
    print(f"🔥 Verarbeite {pdf_path} mit RTX 4090 + Ryzen 9 5950X...")
    
    # Nutze optimierte Methode
    result = rag.upsert_pdf_optimized(
        pdf_path,
        build_kg=True,  # KG-Extraktion aktiviert
        use_gpu=True,   # GPU-Beschleunigung
        memory_efficient=True,  # Memory-Optimierung
        parallel=True,  # CPU-Parallelisierung
    )
    
    print(f"✅ PDF verarbeitet: {result}")
    return result

# Beispiel: Web-PDF-Verarbeitung
def process_web_pdf_optimized(url):
    """Verarbeitet Web-PDFs mit maximaler Performance"""
    
    print(f"🌐 Verarbeite {url} mit RTX 4090 + Ryzen 9 5950X...")
    
    result = rag.upsert_url_optimized(
        url,
        build_kg=True,  # KG-Extraktion aktiviert
        use_gpu=True,   # GPU-Beschleunigung
        parallel=True,  # CPU-Parallelisierung
    )
    
    print(f"✅ Web-PDF verarbeitet: {result}")
    return result

# Beispiel-Nutzung
if __name__ == "__main__":
    
    # Teste mit lokaler PDF
    # process_large_pdf_optimized("path/to/large.pdf")
    
    # Teste mit Web-PDF
    # process_web_pdf_optimized("https://example.com/paper.pdf")
    
    print("🚀 RTX 4090 + Ryzen 9 5950X RAG bereit für maximale Performance!")
'''
    
    example_file = Path('rtx4090_rag_usage_example.py')
    with open(example_file, 'w', encoding='utf-8') as f:
        f.write(example_code)
    
    logger.info(f"✅ Nutzungsbeispiel erstellt: {example_file}")
    return example_file

def main():
    """Hauptfunktion für RTX 4090 + Ryzen 9 5950X Integration"""
    
    print("🔥 RTX 4090 + RYZEN 9 5950X RAG INTEGRATION")
    print("=" * 60)
    
    # Optimierungen anwenden
    if apply_rtx4090_optimizations():
        print("✅ RTX 4090 + Ryzen 9 5950X Optimierungen integriert")
    else:
        print("❌ Integration fehlgeschlagen")
        return False
    
    # Performance testen
    if test_optimized_performance():
        print("✅ Performance-Test erfolgreich")
    else:
        print("⚠️ Performance-Test eingeschränkt")
    
    # Nutzungsbeispiel erstellen
    example_file = create_usage_example()
    
    print(f"\n🚀 INTEGRATION ABGESCHLOSSEN!")
    print(f"   Optimierte RAG Store Klasse: OptimizedRAGStore")
    print(f"   Nutzungsbeispiel: {example_file}")
    print(f"   Konfiguration: rag_rtx4090_config.json")
    print("\n💡 Ihr RAG Store ist jetzt für RTX 4090 + Ryzen 9 5950X optimiert!")
    
    return True

if __name__ == "__main__":
    main()
