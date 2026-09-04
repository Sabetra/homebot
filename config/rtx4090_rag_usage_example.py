
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
