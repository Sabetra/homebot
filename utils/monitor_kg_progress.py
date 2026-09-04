#!/usr/bin/env python3
"""
Knowledge Graph Generation Progress Monitor
Überwacht den Fortschritt der KG-Erstellung in Echtzeit
"""

import os
import sqlite3
import sys
import time
import json
from datetime import datetime, timedelta

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)
from utils.db_path_resolver import get_rag_store_path

def get_kg_stats():
    """Holt aktuelle KG-Statistiken aus der Datenbank"""
    try:
        conn = sqlite3.connect(str(get_rag_store_path()))
        cursor = conn.cursor()
        
        # Gesamtanzahl Chunks
        cursor.execute('SELECT COUNT(*) FROM chunks')
        total_chunks = cursor.fetchone()[0]
        
        # Chunks mit KG
        cursor.execute('''
            SELECT COUNT(DISTINCT doc_id) 
            FROM triples
        ''')
        chunks_with_kg = cursor.fetchone()[0]
        
        # Gesamtanzahl Triples
        cursor.execute('SELECT COUNT(*) FROM triples')
        total_triples = cursor.fetchone()[0]
        
        # Triples der letzten Stunde
        one_hour_ago = datetime.now() - timedelta(hours=1)
        cursor.execute('''
            SELECT COUNT(*) FROM triples 
            WHERE json_extract(metadata, '$.timestamp') > ?
        ''', (one_hour_ago.isoformat(),))
        recent_triples = cursor.fetchone()[0]
        
        # Top Entitäten
        cursor.execute('''
            SELECT subject, COUNT(*) as count
            FROM triples 
            GROUP BY subject 
            ORDER BY count DESC 
            LIMIT 10
        ''')
        top_subjects = cursor.fetchall()
        
        conn.close()
        
        return {
            'total_chunks': total_chunks,
            'chunks_with_kg': chunks_with_kg,
            'chunks_without_kg': total_chunks - chunks_with_kg,
            'total_triples': total_triples,
            'recent_triples': recent_triples,
            'completion_percentage': (chunks_with_kg / total_chunks * 100) if total_chunks > 0 else 0,
            'top_subjects': top_subjects
        }
        
    except Exception as e:
        print(f"❌ Fehler beim Abrufen der Statistiken: {e}")
        return None

def format_time_estimate(chunks_remaining, chunks_per_minute):
    """Berechnet und formatiert Zeitschätzung"""
    if chunks_per_minute == 0:
        return "Unbekannt"
    
    minutes_remaining = chunks_remaining / chunks_per_minute
    hours = int(minutes_remaining // 60)
    mins = int(minutes_remaining % 60)
    
    if hours > 0:
        return f"{hours}h {mins}m"
    else:
        return f"{mins}m"

def monitor_progress(interval=30):
    """Überwacht den KG-Generierungsfortschritt"""
    print("📊 Knowledge Graph Generation Monitor")
    print("="*60)
    
    last_chunks_with_kg = 0
    start_time = datetime.now()
    
    while True:
        stats = get_kg_stats()
        if not stats:
            time.sleep(interval)
            continue
            
        current_time = datetime.now()
        elapsed = current_time - start_time
        
        # Berechne Rate
        chunks_processed_since_start = stats['chunks_with_kg'] - last_chunks_with_kg if last_chunks_with_kg > 0 else 0
        if last_chunks_with_kg == 0:
            last_chunks_with_kg = stats['chunks_with_kg']
        
        elapsed_minutes = elapsed.total_seconds() / 60
        chunks_per_minute = chunks_processed_since_start / elapsed_minutes if elapsed_minutes > 0 else 0
        
        # Zeitschätzung
        remaining_chunks = stats['chunks_without_kg']
        eta = format_time_estimate(remaining_chunks, chunks_per_minute)
        
        # Ausgabe
        print(f"\n🕒 {current_time.strftime('%H:%M:%S')}")
        print(f"📈 Fortschritt: {stats['chunks_with_kg']:,} / {stats['total_chunks']:,} Chunks ({stats['completion_percentage']:.1f}%)")
        print(f"📊 Triples insgesamt: {stats['total_triples']:,}")
        print(f"🔥 Neue Triples (letzte Stunde): {stats['recent_triples']:,}")
        print(f"⚡ Rate: {chunks_per_minute:.1f} Chunks/min")
        print(f"⏰ Geschätzte Restzeit: {eta}")
        
        # Progress Bar
        progress = stats['completion_percentage'] / 100
        bar_length = 40
        filled_length = int(bar_length * progress)
        bar = '█' * filled_length + '░' * (bar_length - filled_length)
        print(f"[{bar}] {stats['completion_percentage']:.1f}%")
        
        # Top Entitäten
        if stats['top_subjects']:
            print("\n🏆 Top Entitäten:")
            for subject, count in stats['top_subjects'][:5]:
                print(f"   • {subject}: {count:,} Referenzen")
        
        print("-" * 60)
        
        # Update für nächste Iteration
        if last_chunks_with_kg == 0:
            last_chunks_with_kg = stats['chunks_with_kg']
        
        time.sleep(interval)

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description='Monitor KG Generation Progress')
    parser.add_argument('--interval', type=int, default=30, help='Update interval in seconds (default: 30)')
    
    args = parser.parse_args()
    
    try:
        monitor_progress(args.interval)
    except KeyboardInterrupt:
        print("\n👋 Monitoring beendet.")
    except Exception as e:
        print(f"❌ Fehler: {e}")
