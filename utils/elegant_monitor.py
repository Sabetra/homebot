#!/usr/bin/env python3
"""
Elegantes Live-Monitoring für Chunk-Processing
==============================================

Kontinuierliche Überwachung mit schöner Anzeige und regelmäßiger Aktualisierung.
"""

import os
import sys
import sqlite3
import time
from datetime import datetime, timedelta
from typing import Dict, List, Tuple

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

class ElegantMonitor:
    """
    Elegantes Live-Monitoring mit schöner Anzeige
    """
    
    def __init__(self, db_path: str = "rag_store.db"):
        self.db_path = db_path
        self.start_time = time.time()
        self.last_update = time.time()
        self.history = []  # [(timestamp, chunks_processed, triples_count)]
        self.last_chunks_count = 0
        self.last_triples_count = 0
        
    def get_current_stats(self) -> Dict:
        """Holt aktuelle Statistiken aus der Datenbank"""
        conn = None
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # Basis-Counts
            cursor.execute('SELECT COUNT(*) FROM chunks')
            total_chunks = cursor.fetchone()[0]
            
            cursor.execute('SELECT COUNT(*) FROM triples')
            total_triples = cursor.fetchone()[0]
            
            cursor.execute('SELECT COUNT(DISTINCT doc_id) FROM triples')
            successful_docs = cursor.fetchone()[0]
            
            cursor.execute('SELECT COUNT(DISTINCT doc_id) FROM chunks')
            total_docs = cursor.fetchone()[0]
            
            # Top 5 Entitäten
            cursor.execute('''
                SELECT subject, COUNT(*) as count 
                FROM triples 
                WHERE subject IS NOT NULL AND subject != '' 
                GROUP BY subject 
                ORDER BY count DESC 
                LIMIT 5
            ''')
            top_entities = cursor.fetchall()
            
            # Geschätzte verarbeitete Chunks (Docs mit Triples)
            # Approximation: Docs mit Triples * durchschnittliche Chunks pro Doc
            avg_chunks_per_doc = total_chunks / total_docs if total_docs > 0 else 1
            estimated_processed_chunks = successful_docs * avg_chunks_per_doc
            
            return {
                'total_chunks': total_chunks,
                'total_triples': total_triples,
                'successful_docs': successful_docs,
                'total_docs': total_docs,
                'estimated_processed_chunks': int(estimated_processed_chunks),
                'top_entities': top_entities
            }
            
        except sqlite3.Error as e:
            print(f"❌ Datenbankfehler: {e}")
            return {}
        finally:
            if conn:
                conn.close()
    
    def calculate_progress_metrics(self, stats: Dict) -> Dict:
        """Berechnet Fortschritts-Metriken"""
        current_time = time.time()
        
        # Aktualisiere Historie
        self.history.append((
            current_time,
            stats['estimated_processed_chunks'],
            stats['total_triples']
        ))
        
        # Halte nur letzte 60 Einträge (für Stunden-Berechnung)
        if len(self.history) > 60:
            self.history = self.history[-60:]
        
        # Berechne Rate
        chunks_processed = stats['estimated_processed_chunks']
        triples_count = stats['total_triples']
        
        # Rate pro Minute
        elapsed_minutes = (current_time - self.start_time) / 60
        chunks_rate = chunks_processed / elapsed_minutes if elapsed_minutes > 0 else 0
        
        # Neue Triples in letzter Stunde
        hour_ago = current_time - 3600
        hour_history = [h for h in self.history if h[0] > hour_ago]
        
        if len(hour_history) >= 2:
            triples_last_hour = hour_history[-1][2] - hour_history[0][2]
        else:
            triples_last_hour = triples_count - self.last_triples_count
        
        # Geschätzte Restzeit
        remaining_chunks = stats['total_chunks'] - chunks_processed
        if chunks_rate > 0:
            remaining_minutes = remaining_chunks / chunks_rate
            remaining_hours = remaining_minutes / 60
        else:
            remaining_minutes = 999 * 60
            remaining_hours = 999
        
        # Fortschritt in Prozent
        progress_percent = (chunks_processed / stats['total_chunks'] * 100) if stats['total_chunks'] > 0 else 0
        
        return {
            'chunks_processed': chunks_processed,
            'progress_percent': progress_percent,
            'chunks_rate': chunks_rate,
            'triples_last_hour': triples_last_hour,
            'remaining_hours': remaining_hours,
            'remaining_minutes': remaining_minutes % 60 if remaining_hours < 999 else 0
        }
    
    def create_progress_bar(self, percent: float, width: int = 50) -> str:
        """Erstellt einen schönen Fortschrittsbalken"""
        filled = int(width * percent / 100)
        bar = '█' * filled + '░' * (width - filled)
        return f"[{bar}] {percent:.1f}%"
    
    def format_time_remaining(self, hours: float, minutes: float) -> str:
        """Formatiert verbleibende Zeit schön"""
        if hours >= 999:
            return "∞"
        elif hours >= 24:
            days = int(hours / 24)
            remaining_hours = int(hours % 24)
            return f"{days}d {remaining_hours}h"
        else:
            return f"{int(hours)}h {int(minutes)}m"
    
    def display_status(self, stats: Dict, metrics: Dict):
        """Zeigt den aktuellen Status schön formatiert an"""
        current_time = datetime.now()
        
        # Header mit Zeit
        print(f"\n{'='*70}")
        print(f"🕒 {current_time.strftime('%H:%M:%S')}")
        
        # Fortschritt
        print(f"📈 Fortschritt: {metrics['chunks_processed']:,} / {stats['total_chunks']:,} Chunks ({metrics['progress_percent']:.1f}%)")
        print(f"📊 Triples insgesamt: {stats['total_triples']:,}")
        print(f"🔥 Neue Triples (letzte Stunde): {metrics['triples_last_hour']:,}")
        print(f"⚡ Rate: {metrics['chunks_rate']:.1f} Chunks/min")
        print(f"⏰ Geschätzte Restzeit: {self.format_time_remaining(metrics['remaining_hours'], metrics['remaining_minutes'])}")
        
        # Fortschrittsbalken
        progress_bar = self.create_progress_bar(metrics['progress_percent'])
        print(progress_bar)
        
        # Top Entitäten
        if stats['top_entities']:
            print(f"\n🏆 Top Entitäten:")
            for entity, count in stats['top_entities']:
                display_entity = entity[:30] + '...' if len(entity) > 30 else entity
                print(f"   • {display_entity}: {count:,} Referenzen")
        
        # Zusätzliche Metriken
        success_rate = (stats['successful_docs'] / stats['total_docs'] * 100) if stats['total_docs'] > 0 else 0
        print(f"\n📄 Dokumente erfolgreich: {stats['successful_docs']:,}/{stats['total_docs']:,} ({success_rate:.1f}%)")
        
        # Triples pro erfolgreichem Dokument
        if stats['successful_docs'] > 0:
            triples_per_doc = stats['total_triples'] / stats['successful_docs']
            print(f"🎯 Durchschn. Triples/Dokument: {triples_per_doc:.1f}")
        
        print(f"{'='*70}")
        
        # Update letzte Werte
        self.last_chunks_count = metrics['chunks_processed']
        self.last_triples_count = stats['total_triples']
    
    def run_monitoring(self, update_interval: int = 30):
        """Startet das kontinuierliche Monitoring"""
        print(f"🚀 ELEGANTES CHUNK-PROCESSING MONITORING")
        print(f"   Aktualisierung alle {update_interval} Sekunden")
        print(f"   Datenbank: {self.db_path}")
        print(f"   Gestartet: {datetime.now().strftime('%H:%M:%S')}")
        print(f"   Drücke Ctrl+C zum Beenden")
        
        try:
            while True:
                # Hole aktuelle Daten
                stats = self.get_current_stats()
                
                if stats:
                    # Berechne Metriken
                    metrics = self.calculate_progress_metrics(stats)
                    
                    # Zeige Status an
                    self.display_status(stats, metrics)
                else:
                    print(f"\n❌ Konnte Statistiken nicht laden - {datetime.now().strftime('%H:%M:%S')}")
                
                # Warte bis zur nächsten Aktualisierung
                time.sleep(update_interval)
                
        except KeyboardInterrupt:
            self.print_final_summary()
        except Exception as e:
            print(f"\n❌ Monitoring-Fehler: {e}")
            self.print_final_summary()
    
    def print_final_summary(self):
        """Druckt finale Zusammenfassung"""
        print(f"\n{'='*70}")
        print("🏁 MONITORING BEENDET")
        print(f"{'='*70}")
        
        total_runtime = time.time() - self.start_time
        runtime_hours = total_runtime / 3600
        
        print(f"⏱️  Laufzeit: {self.format_time_remaining(runtime_hours, (runtime_hours * 60) % 60)}")
        
        if len(self.history) >= 2:
            start_chunks = self.history[0][1]
            end_chunks = self.history[-1][1]
            start_triples = self.history[0][2]
            end_triples = self.history[-1][2]
            
            chunks_processed = end_chunks - start_chunks
            triples_created = end_triples - start_triples
            
            print(f"📈 Chunks verarbeitet: {chunks_processed:,}")
            print(f"📊 Triples erstellt: {triples_created:,}")
            
            if runtime_hours > 0:
                chunks_per_hour = chunks_processed / runtime_hours
                triples_per_hour = triples_created / runtime_hours
                print(f"⚡ Durchschn. Rate: {chunks_per_hour:.1f} Chunks/h, {triples_per_hour:.1f} Triples/h")
        
        print(f"🕒 Beendet: {datetime.now().strftime('%H:%M:%S')}")
        print(f"{'='*70}")

def main():
    """Hauptfunktion"""
    import argparse
    
    parser = argparse.ArgumentParser(description='Elegantes Live-Monitoring für Chunk-Processing')
    parser.add_argument('--interval', type=int, default=30, help='Aktualisierungsintervall in Sekunden (Standard: 30)')
    parser.add_argument('--db-path', default='rag_store.db', help='Pfad zur RAG-Datenbank')
    
    args = parser.parse_args()
    
    monitor = ElegantMonitor(args.db_path)
    monitor.run_monitoring(args.interval)

if __name__ == "__main__":
    main()
