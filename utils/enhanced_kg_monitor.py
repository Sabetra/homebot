#!/usr/bin/env python3
"""
Enhanced Knowledge Graph Generation Progress Monitor
==================================================
Überwacht den Fortschritt der KG-Erstellung mit erweiterten Metriken:
- Top-20 Entitäten (Subjects)
- Top-20 Beziehungen (Predicates)  
- Top-20 Objekte (Objects)
- Performance-Metriken
- 20-Sekunden Update-Intervall
"""

import sqlite3
import sys
import time
import json
import os
from datetime import datetime, timedelta
from collections import Counter

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)
from utils.db_path_resolver import get_rag_store_path

def get_enhanced_kg_stats():
    """Holt erweiterte KG-Statistiken aus der Datenbank"""
    try:
        db_path = str(get_rag_store_path())
        
        conn = sqlite3.connect(db_path)
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
        
        # Triples der letzten 20 Minuten (für bessere Rate-Berechnung)
        twenty_min_ago = datetime.now() - timedelta(minutes=20)
        cursor.execute('''
            SELECT COUNT(*) FROM triples 
            WHERE json_extract(metadata, '$.timestamp') > ?
        ''', (twenty_min_ago.isoformat(),))
        recent_triples = cursor.fetchone()[0]
        
        # Top-20 Entitäten (Subjects)
        cursor.execute('''
            SELECT subject, COUNT(*) as count
            FROM triples 
            GROUP BY subject 
            ORDER BY count DESC 
            LIMIT 20
        ''')
        top_subjects = cursor.fetchall()
        
        # Top-20 Beziehungen (Predicates)
        cursor.execute('''
            SELECT predicate, COUNT(*) as count
            FROM triples 
            GROUP BY predicate 
            ORDER BY count DESC 
            LIMIT 20
        ''')
        top_predicates = cursor.fetchall()
        
        # Top-20 Objekte (Objects)
        cursor.execute('''
            SELECT object, COUNT(*) as count
            FROM triples 
            GROUP BY object 
            ORDER BY count DESC 
            LIMIT 20
        ''')
        top_objects = cursor.fetchall()
        
        # Durchschnittliche Triples pro Chunk
        avg_triples_per_chunk = total_triples / chunks_with_kg if chunks_with_kg > 0 else 0
        
        # Eindeutige Entitäten, Beziehungen, Objekte
        cursor.execute('SELECT COUNT(DISTINCT subject) FROM triples')
        unique_subjects = cursor.fetchone()[0]
        
        cursor.execute('SELECT COUNT(DISTINCT predicate) FROM triples')
        unique_predicates = cursor.fetchone()[0]
        
        cursor.execute('SELECT COUNT(DISTINCT object) FROM triples')
        unique_objects = cursor.fetchone()[0]
        
        # Neueste Chunk-Aktivität
        cursor.execute('''
            SELECT doc_id, COUNT(*) as triple_count
            FROM triples 
            WHERE json_extract(metadata, '$.timestamp') > ?
            GROUP BY doc_id
            ORDER BY json_extract(metadata, '$.timestamp') DESC
            LIMIT 5
        ''', (twenty_min_ago.isoformat(),))
        recent_chunks = cursor.fetchall()
        
        conn.close()
        
        return {
            'total_chunks': total_chunks,
            'chunks_with_kg': chunks_with_kg,
            'chunks_without_kg': total_chunks - chunks_with_kg,
            'total_triples': total_triples,
            'recent_triples': recent_triples,
            'completion_percentage': (chunks_with_kg / total_chunks * 100) if total_chunks > 0 else 0,
            'avg_triples_per_chunk': avg_triples_per_chunk,
            'unique_subjects': unique_subjects,
            'unique_predicates': unique_predicates,
            'unique_objects': unique_objects,
            'top_subjects': top_subjects,
            'top_predicates': top_predicates,
            'top_objects': top_objects,
            'recent_chunks': recent_chunks
        }
        
    except Exception as e:
        print(f"❌ Fehler beim Abrufen der Statistiken: {e}")
        return None

def format_time_estimate(chunks_remaining, chunks_per_minute):
    """Berechnet und formatiert Zeitschätzung"""
    if chunks_per_minute <= 0:
        return "Unbekannt"
    
    minutes_remaining = chunks_remaining / chunks_per_minute
    hours = int(minutes_remaining // 60)
    mins = int(minutes_remaining % 60)
    
    if hours > 24:
        days = int(hours // 24)
        hours = hours % 24
        return f"{days}d {hours}h {mins}m"
    elif hours > 0:
        return f"{hours}h {mins}m"
    else:
        return f"{mins}m"

def clear_screen():
    """Löscht den Bildschirm für bessere Darstellung"""
    os.system('cls' if os.name == 'nt' else 'clear')

def display_top_items(title, items, icon):
    """Zeigt Top-Items in einer formatierten Liste"""
    print(f"\n{icon} {title}:")
    for i, (item, count) in enumerate(items[:20], 1):
        # Kürze sehr lange Items
        display_item = item[:50] + "..." if len(item) > 50 else item
        print(f"   {i:2d}. {display_item:<53} ({count:,})")

def monitor_enhanced_progress():
    """Überwacht den KG-Generierungsfortschritt mit erweiterten Metriken"""
    print("🚀 ENHANCED KNOWLEDGE GRAPH GENERATION MONITOR")
    print("=" * 80)
    print("📊 Top-20 Entitäten, Beziehungen & Objekte | 20s Update-Intervall")
    print("=" * 80)
    
    last_chunks_with_kg = 0
    last_total_triples = 0
    start_time = datetime.now()
    
    while True:
        stats = get_enhanced_kg_stats()
        if not stats:
            time.sleep(20)
            continue
        
        current_time = datetime.now()
        elapsed = current_time - start_time
        
        # Berechne Raten
        if last_chunks_with_kg > 0:
            chunks_processed = stats['chunks_with_kg'] - last_chunks_with_kg
            triples_generated = stats['total_triples'] - last_total_triples
        else:
            chunks_processed = 0
            triples_generated = 0
        
        elapsed_minutes = elapsed.total_seconds() / 60
        chunks_per_minute = chunks_processed / (20/60) if chunks_processed > 0 else 0  # Rate über letzte 20s
        triples_per_minute = triples_generated / (20/60) if triples_generated > 0 else 0
        
        # Zeitschätzung
        remaining_chunks = stats['chunks_without_kg']
        eta = format_time_estimate(remaining_chunks, chunks_per_minute)
        
        # Clear screen für bessere Darstellung
        clear_screen()
        
        # Header
        print("🚀 ENHANCED KNOWLEDGE GRAPH GENERATION MONITOR")
        print("=" * 80)
        print(f"🕒 {current_time.strftime('%Y-%m-%d %H:%M:%S')} | Aktualisierung alle 20s")
        print("=" * 80)
        
        # Haupt-Statistiken
        print(f"\n📈 FORTSCHRITT:")
        print(f"   Chunks verarbeitet: {stats['chunks_with_kg']:,} / {stats['total_chunks']:,} ({stats['completion_percentage']:.1f}%)")
        print(f"   Chunks verbleibend: {stats['chunks_without_kg']:,}")
        
        # Progress Bar
        progress = stats['completion_percentage'] / 100
        bar_length = 50
        filled_length = int(bar_length * progress)
        bar = '█' * filled_length + '░' * (bar_length - filled_length)
        print(f"   [{bar}] {stats['completion_percentage']:.1f}%")
        
        print(f"\n📊 TRIPLES:")
        print(f"   Triples gesamt: {stats['total_triples']:,}")
        print(f"   Neue Triples (20min): {stats['recent_triples']:,}")
        print(f"   Ø Triples/Chunk: {stats['avg_triples_per_chunk']:.1f}")
        
        print(f"\n⚡ PERFORMANCE:")
        print(f"   Chunks/Min: {chunks_per_minute:.1f}")
        print(f"   Triples/Min: {triples_per_minute:.1f}")
        print(f"   Geschätzte Restzeit: {eta}")
        
        print(f"\n🔍 DIVERSITÄT:")
        print(f"   Eindeutige Entitäten: {stats['unique_subjects']:,}")
        print(f"   Eindeutige Beziehungen: {stats['unique_predicates']:,}")
        print(f"   Eindeutige Objekte: {stats['unique_objects']:,}")
        
        # Top-Listen
        if stats['top_subjects']:
            display_top_items("TOP-20 ENTITÄTEN (SUBJECTS)", stats['top_subjects'], "👑")
        
        if stats['top_predicates']:
            display_top_items("TOP-20 BEZIEHUNGEN (PREDICATES)", stats['top_predicates'], "🔗")
        
        if stats['top_objects']:
            display_top_items("TOP-20 OBJEKTE (OBJECTS)", stats['top_objects'], "🎯")
        
        # Neueste Aktivität
        if stats['recent_chunks']:
            print(f"\n🔥 NEUESTE CHUNK-AKTIVITÄT (letzte 20min):")
            for doc_id, triple_count in stats['recent_chunks'][:5]:
                # Kürze doc_id für bessere Darstellung
                display_doc = doc_id[:60] + "..." if len(doc_id) > 60 else doc_id
                print(f"   • {display_doc:<63} ({triple_count} Triples)")
        
        print("\n" + "=" * 80)
        print("Drücke Ctrl+C zum Beenden...")
        
        # Update für nächste Iteration
        last_chunks_with_kg = stats['chunks_with_kg']
        last_total_triples = stats['total_triples']
        
        time.sleep(20)

if __name__ == "__main__":
    try:
        monitor_enhanced_progress()
    except KeyboardInterrupt:
        print("\n\n👋 Enhanced Monitoring beendet.")
    except Exception as e:
        print(f"\n❌ Fehler: {e}")
        import traceback
        traceback.print_exc()
