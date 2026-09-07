#!/usr/bin/env python3
"""
Elegantes Chunk-Processing Monitor
=================================

Kontinuierliches Monitoring mit Top 10 Entitäten, Beziehungen, Objekten und erweiterten Metriken.
"""

import os
import sys
import sqlite3
import time
import datetime
from typing import Dict, List, Tuple

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

class ElegantChunkMonitor:
    """
    Elegantes kontinuierliches Monitoring für Chunk-Processing
    """
    
    def __init__(self, db_path: str = "rag_store.db"):
        self.db_path = db_path
        self.start_time = time.time()
        self.last_chunks_count = 0
        self.last_triples_count = 0
        self.last_hour_triples = 0
        self.last_hour_time = time.time()
        self.processing_rates = []
        
    def get_current_stats(self) -> Dict:
        """Sammelt aktuelle Statistiken"""
        conn = None
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # Basis-Statistiken
            cursor.execute('SELECT COUNT(*) FROM chunks')
            total_chunks = cursor.fetchone()[0]
            
            cursor.execute('SELECT COUNT(*) FROM triples')
            total_triples = cursor.fetchone()[0]
            
            cursor.execute('SELECT COUNT(DISTINCT doc_id) FROM triples')
            successful_docs = cursor.fetchone()[0]
            
            cursor.execute('SELECT COUNT(DISTINCT doc_id) FROM chunks')
            total_docs = cursor.fetchone()[0]
            
            # Top 20 Entitäten (Subjects)
            cursor.execute('''
                SELECT subject, COUNT(*) as count 
                FROM triples 
                WHERE subject IS NOT NULL AND subject != '' 
                GROUP BY subject 
                ORDER BY count DESC 
                LIMIT 20
            ''')
            top_subjects = cursor.fetchall()
            
            # Top 20 Beziehungen (Predicates)
            cursor.execute('''
                SELECT predicate, COUNT(*) as count 
                FROM triples 
                WHERE predicate IS NOT NULL AND predicate != '' 
                GROUP BY predicate 
                ORDER BY count DESC 
                LIMIT 20
            ''')
            top_predicates = cursor.fetchall()
            
            # Top 20 Objekte (Objects)
            cursor.execute('''
                SELECT object, COUNT(*) as count 
                FROM triples 
                WHERE object IS NOT NULL AND object != '' 
                GROUP BY object 
                ORDER BY count DESC 
                LIMIT 20
            ''')
            top_objects = cursor.fetchall()
            
            # Erfolgsrate nach Dokumenttyp
            cursor.execute('''
                SELECT 
                    CASE 
                        WHEN doc_id LIKE '%.pdf%' THEN 'PDF'
                        WHEN doc_id LIKE '%.txt%' THEN 'TXT'
                        WHEN doc_id LIKE '%.html%' THEN 'HTML'
                        WHEN doc_id LIKE 'http%' THEN 'WEB'
                        ELSE 'OTHER'
                    END as doc_type,
                    COUNT(DISTINCT doc_id) as total_docs,
                    COUNT(DISTINCT CASE WHEN triple_count > 0 THEN doc_id END) as successful_docs
                FROM (
                    SELECT c.doc_id, COUNT(t.triple_id) as triple_count
                    FROM chunks c
                    LEFT JOIN triples t ON c.doc_id = t.doc_id
                    GROUP BY c.doc_id
                )
                GROUP BY doc_type
                ORDER BY successful_docs DESC
            ''')
            doc_type_stats = cursor.fetchall()
            
            # Problematische Chunks
            cursor.execute('''
                SELECT COUNT(*) FROM chunks c
                LEFT JOIN triples t ON c.doc_id = t.doc_id
                WHERE t.doc_id IS NULL
            ''')
            chunks_without_triples = cursor.fetchone()[0]
            
            return {
                'total_chunks': total_chunks,
                'total_triples': total_triples,
                'successful_docs': successful_docs,
                'total_docs': total_docs,
                'chunks_without_triples': chunks_without_triples,
                'top_subjects': top_subjects,
                'top_predicates': top_predicates,
                'top_objects': top_objects,
                'doc_type_stats': doc_type_stats
            }
            
        except sqlite3.Error as e:
            print(f"❌ Datenbankfehler: {e}")
            return {}
        finally:
            if conn:
                conn.close()
    
    def calculate_rates_and_eta(self, current_stats: Dict) -> Dict:
        """Berechnet Verarbeitungsraten und ETA"""
        current_time = time.time()
        elapsed_time = current_time - self.start_time
        
        total_chunks = current_stats.get('total_chunks', 0)
        total_triples = current_stats.get('total_triples', 0)
        successful_docs = current_stats.get('successful_docs', 0)
        
        # Chunks mit Triples (erfolgreich verarbeitete)
        processed_chunks = total_chunks - current_stats.get('chunks_without_triples', 0)
        
        # Processing Rate berechnen
        chunks_per_minute = 0
        if elapsed_time > 60:  # Mindestens 1 Minute für sinnvolle Rate
            chunks_per_minute = (processed_chunks - self.last_chunks_count) / (elapsed_time / 60)
            if len(self.processing_rates) >= 10:
                self.processing_rates.pop(0)
            self.processing_rates.append(chunks_per_minute)
            avg_rate = sum(self.processing_rates) / len(self.processing_rates)
        else:
            avg_rate = 0
        
        # Neue Triples in der letzten Stunde
        if current_time - self.last_hour_time >= 3600:  # 1 Stunde
            self.last_hour_triples = total_triples - self.last_triples_count
            self.last_hour_time = current_time
            self.last_triples_count = total_triples
        
        # ETA berechnen
        remaining_chunks = total_chunks - processed_chunks
        eta_hours = 0
        eta_minutes = 0
        if avg_rate > 0:
            eta_total_minutes = remaining_chunks / avg_rate
            eta_hours = int(eta_total_minutes // 60)
            eta_minutes = int(eta_total_minutes % 60)
        
        return {
            'processed_chunks': processed_chunks,
            'chunks_per_minute': avg_rate,
            'new_triples_last_hour': self.last_hour_triples,
            'eta_hours': eta_hours,
            'eta_minutes': eta_minutes,
            'success_rate': (successful_docs / current_stats.get('total_docs', 1)) * 100
        }
    
    def create_progress_bar(self, processed: int, total: int, width: int = 50) -> str:
        """Erstellt eine Fortschrittsbalken"""
        if total == 0:
            return '[' + '░' * width + ']'
        
        progress = processed / total
        filled = int(width * progress)
        bar = '█' * filled + '░' * (width - filled)
        return f'[{bar}]'
    
    def format_number(self, num: int) -> str:
        """Formatiert Zahlen mit Tausender-Trennzeichen"""
        return f"{num:,}".replace(',', '.')
    
    def truncate_text(self, text: str, max_length: int = 25) -> str:
        """Kürzt Text auf maximale Länge"""
        if len(text) <= max_length:
            return text
        return text[:max_length-3] + '...'
    
    def display_elegant_monitor(self, stats: Dict, rates: Dict):
        """Zeigt das elegante Monitoring-Interface"""
        current_time = datetime.datetime.now().strftime("%H:%M:%S")
        
        total_chunks = stats['total_chunks']
        processed_chunks = rates['processed_chunks']
        progress_percent = (processed_chunks / total_chunks * 100) if total_chunks > 0 else 0
        
        # Header mit Zeit und Fortschritt
        print(f"\n🕒 {current_time}")
        print(f"📈 Fortschritt: {self.format_number(processed_chunks)} / {self.format_number(total_chunks)} Chunks ({progress_percent:.1f}%)")
        print(f"📊 Triples insgesamt: {self.format_number(stats['total_triples'])}")
        print(f"🔥 Neue Triples (letzte Stunde): {self.format_number(rates['new_triples_last_hour'])}")
        print(f"⚡ Rate: {rates['chunks_per_minute']:.1f} Chunks/min")
        print(f"⏰ Geschätzte Restzeit: {rates['eta_hours']}h {rates['eta_minutes']}m")
        
        # Fortschrittsbalken
        progress_bar = self.create_progress_bar(processed_chunks, total_chunks)
        print(f"{progress_bar} {progress_percent:.1f}%")
        
        # Erfolgsrate
        print(f"✅ Erfolgsrate: {rates['success_rate']:.1f}% ({self.format_number(stats['successful_docs'])}/{self.format_number(stats['total_docs'])} Dokumente)")
        
        # Top 20 Entitäten
        if stats['top_subjects']:
            print(f"\n🏆 Top 20 Entitäten:")
            for i, (subject, count) in enumerate(stats['top_subjects'], 1):
                display_subject = self.truncate_text(subject, 25)
                print(f"   {i:2}. {display_subject:<27}: {self.format_number(count)} Referenzen")
        
        # Top 20 Beziehungen
        if stats['top_predicates']:
            print(f"\n🔗 Top 20 Beziehungen:")
            for i, (predicate, count) in enumerate(stats['top_predicates'], 1):
                display_predicate = self.truncate_text(predicate, 25)
                print(f"   {i:2}. {display_predicate:<27}: {self.format_number(count)} Mal")
        
        # Top 20 Objekte
        if stats['top_objects']:
            print(f"\n🎯 Top 20 Objekte:")
            for i, (obj, count) in enumerate(stats['top_objects'], 1):
                display_object = self.truncate_text(obj, 25)
                print(f"   {i:2}. {display_object:<27}: {self.format_number(count)} Referenzen")
        
        # Dokumenttyp-Statistiken
        if stats['doc_type_stats']:
            print(f"\n📁 Erfolgsrate nach Dokumenttyp:")
            for doc_type, total_docs, successful_docs in stats['doc_type_stats']:
                success_rate = (successful_docs / total_docs * 100) if total_docs > 0 else 0
                print(f"   {doc_type:<8}: {self.format_number(successful_docs)}/{self.format_number(total_docs)} ({success_rate:5.1f}%)")
        
        # Problematische Chunks
        problem_chunks = stats['chunks_without_triples']
        problem_percent = (problem_chunks / total_chunks * 100) if total_chunks > 0 else 0
        print(f"\n⚠️  Problematische Chunks: {self.format_number(problem_chunks)} ({problem_percent:.1f}%)")
        
        print(f"\n{'='*80}")
    
    def start_monitoring(self, update_interval: int = 30):
        """Startet das kontinuierliche Monitoring"""
        print(f"🚀 ELEGANTES CHUNK-PROCESSING MONITOR GESTARTET")
        print(f"   Update-Intervall: {update_interval} Sekunden")
        print(f"   Datenbank: {self.db_path}")
        print(f"   Drücke Ctrl+C zum Beenden")
        
        # Initiale Werte setzen
        initial_stats = self.get_current_stats()
        if initial_stats:
            self.last_chunks_count = initial_stats['total_chunks'] - initial_stats.get('chunks_without_triples', 0)
            self.last_triples_count = initial_stats['total_triples']
        
        current_stats = {}
        try:
            while True:
                # Bildschirm löschen (Windows/Unix kompatibel)
                os.system('cls' if os.name == 'nt' else 'clear')
                
                # Aktuelle Statistiken sammeln
                current_stats = self.get_current_stats()
                
                if current_stats:
                    # Raten und ETA berechnen
                    rates_and_eta = self.calculate_rates_and_eta(current_stats)
                    
                    # Elegante Anzeige
                    self.display_elegant_monitor(current_stats, rates_and_eta)
                else:
                    print("❌ Fehler beim Laden der Statistiken")
                
                # Warten bis zum nächsten Update
                time.sleep(update_interval)
                
        except KeyboardInterrupt:
            print(f"\n\n🛑 Monitoring beendet vom Benutzer")
            print(f"📊 Finale Statistiken:")
            if current_stats:
                total_time = time.time() - self.start_time
                hours = int(total_time // 3600)
                minutes = int((total_time % 3600) // 60)
                print(f"   Monitoring-Dauer: {hours}h {minutes}m")
                print(f"   Chunks verarbeitet: {self.format_number(current_stats['total_chunks'])}")
                print(f"   Triples erstellt: {self.format_number(current_stats['total_triples'])}")
        except Exception as e:
            print(f"\n❌ Monitoring-Fehler: {e}")

def main():
    """Hauptfunktion"""
    import argparse
    
    parser = argparse.ArgumentParser(description='Elegantes Chunk-Processing Monitor')
    parser.add_argument('--interval', type=int, default=30, help='Update-Intervall in Sekunden (Standard: 30)')
    parser.add_argument('--db-path', default='rag_store.db', help='Pfad zur RAG-Datenbank')
    
    args = parser.parse_args()
    
    monitor = ElegantChunkMonitor(args.db_path)
    monitor.start_monitoring(args.interval)

if __name__ == "__main__":
    main()
