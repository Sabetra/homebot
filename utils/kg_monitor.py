#!/usr/bin/env python3
"""
Monitor für den Bulk Knowledge Graph Generator
Zeigt den aktuellen Fortschritt der KG-Erstellung an
"""

import sqlite3
import sys
import time
import os
from datetime import datetime, timedelta

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)
from utils.db_path_resolver import get_rag_store_path

def monitor_kg_progress():
    """Überwacht den Fortschritt der KG-Generierung"""
    db_path = str(get_rag_store_path())
    
    print("🔍 KG-Generator Fortschritts-Monitor")
    print("=" * 50)
    
    # Startzeit und initiale Werte
    start_time = datetime.now()
    initial_triples = get_triple_count(db_path)
    initial_chunks_without_kg = get_chunks_without_kg_count(db_path)
    
    print(f"⏰ Start: {start_time.strftime('%H:%M:%S')}")
    print(f"📊 Initiale Triples: {initial_triples:,}")
    print(f"📋 Chunks ohne KG: {initial_chunks_without_kg:,}")
    print("-" * 50)
    
    last_triple_count = initial_triples
    last_check_time = start_time
    
    try:
        while True:
            time.sleep(30)  # Alle 30 Sekunden prüfen
            
            current_time = datetime.now()
            current_triples = get_triple_count(db_path)
            current_chunks_without_kg = get_chunks_without_kg_count(db_path)
            
            # Fortschritt berechnen
            new_triples = current_triples - initial_triples
            processed_chunks = initial_chunks_without_kg - current_chunks_without_kg
            triples_since_last = current_triples - last_triple_count
            time_since_last = (current_time - last_check_time).total_seconds()
            
            # Geschwindigkeiten berechnen
            if time_since_last > 0:
                triples_per_minute = (triples_since_last / time_since_last) * 60
            else:
                triples_per_minute = 0
            
            elapsed = current_time - start_time
            if elapsed.total_seconds() > 0:
                avg_triples_per_minute = (new_triples / elapsed.total_seconds()) * 60
                if processed_chunks > 0:
                    avg_time_per_chunk = elapsed.total_seconds() / processed_chunks
                else:
                    avg_time_per_chunk = 0
            else:
                avg_triples_per_minute = 0
                avg_time_per_chunk = 0
            
            # ETA berechnen
            if avg_time_per_chunk > 0 and current_chunks_without_kg > 0:
                eta_seconds = current_chunks_without_kg * avg_time_per_chunk
                eta = current_time + timedelta(seconds=eta_seconds)
                eta_str = eta.strftime('%H:%M:%S')
            else:
                eta_str = "N/A"
            
            # Fortschritt anzeigen
            print(f"\r🕐 {current_time.strftime('%H:%M:%S')} | "
                  f"📈 Neue Triples: {new_triples:,} (+{triples_since_last}) | "
                  f"📋 Chunks übrig: {current_chunks_without_kg:,} | "
                  f"⚡ {triples_per_minute:.1f} T/min | "
                  f"🎯 ETA: {eta_str}", end="", flush=True)
            
            # Detaillierte Statistik alle 5 Minuten
            if elapsed.total_seconds() % 300 < 30:  # Alle ~5 Minuten
                print(f"\n📊 Detaillierte Statistik ({elapsed}):")
                print(f"   Verarbeitete Chunks: {processed_chunks:,}")
                print(f"   Durchschn. Zeit/Chunk: {avg_time_per_chunk:.1f}s")
                print(f"   Durchschn. Triples/Min: {avg_triples_per_minute:.1f}")
                if processed_chunks > 0:
                    print(f"   Durchschn. Triples/Chunk: {new_triples/processed_chunks:.1f}")
                print("-" * 50)
            
            # Werte für nächste Iteration
            last_triple_count = current_triples
            last_check_time = current_time
            
            # Prüfen ob fertig
            if current_chunks_without_kg == 0:
                print(f"\n\n🎉 FERTIG! Alle Chunks haben jetzt Knowledge Graphs!")
                print(f"📊 Gesamt: {new_triples:,} neue Triples in {elapsed}")
                break
                
    except KeyboardInterrupt:
        print(f"\n\n⏹️ Monitor gestoppt.")
        elapsed = datetime.now() - start_time
        final_triples = get_triple_count(db_path)
        new_triples = final_triples - initial_triples
        print(f"📊 Fortschritt: {new_triples:,} neue Triples in {elapsed}")

def get_triple_count(db_path):
    """Gibt die aktuelle Anzahl von Triples zurück"""
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute('SELECT COUNT(*) FROM triples')
        count = cursor.fetchone()[0]
        conn.close()
        return count
    except Exception as e:
        print(f"Fehler beim Abrufen der Triple-Anzahl: {e}")
        return 0

def get_chunks_without_kg_count(db_path):
    """Gibt die Anzahl der Chunks ohne KG zurück"""
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute('''
            SELECT COUNT(DISTINCT c.chunk_id) 
            FROM chunks c 
            LEFT JOIN triples t ON c.doc_id = t.doc_id 
            WHERE t.triple_id IS NULL
        ''')
        count = cursor.fetchone()[0]
        conn.close()
        return count
    except Exception as e:
        print(f"Fehler beim Abrufen der Chunk-Anzahl: {e}")
        return 0

if __name__ == '__main__':
    monitor_kg_progress()
