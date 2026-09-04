#!/usr/bin/env python3
"""
BEREINIGUNG DER BESTEHENDEN TRIPLE-DUPLIKATE
==========================================

Entfernt bestehende Triple-Duplikate und erstellt UNIQUE Index auf triple_hash.
"""

import os
import sys
import sqlite3
import time
from datetime import datetime

def cleanup_triple_duplicates():
    """
    Bereinigt bestehende Triple-Duplikate und erstellt UNIQUE Index.
    """
    print("🧹 BEREINIGUNG DER TRIPLE-DUPLIKATE")
    print("=" * 50)
    
    try:
        # Backup-Hinweis
        print("⚠️  WICHTIG: Stelle sicher, dass ein Backup der Datenbank existiert!")
        print("📁 Datenbank: rag_store.db")
        
        # Warte auf Bestätigung (für Produktionsumgebung)
        # input("Drücke Enter zum Fortfahren oder Ctrl+C zum Abbrechen...")
        
        conn = sqlite3.connect('rag_store.db')
        cur = conn.cursor()
        
        # 1. ANALYSE: Aktuelle Situation
        print("\n📊 ANALYSE DER AKTUELLEN SITUATION:")
        print("-" * 40)
        
        # Gesamte Triple-Anzahl
        cur.execute("SELECT COUNT(*) FROM triples")
        total_triples = cur.fetchone()[0]
        print(f"📋 Gesamt-Triples: {total_triples}")
        
        # Triples mit Hash
        cur.execute("SELECT COUNT(*) FROM triples WHERE triple_hash IS NOT NULL")
        triples_with_hash = cur.fetchone()[0]
        print(f"🔑 Triples mit Hash: {triples_with_hash}")
        
        # Eindeutige Hashes
        cur.execute("SELECT COUNT(DISTINCT triple_hash) FROM triples WHERE triple_hash IS NOT NULL")
        unique_hashes = cur.fetchone()[0]
        print(f"✅ Eindeutige Hashes: {unique_hashes}")
        
        # Duplikate
        duplicates_count = triples_with_hash - unique_hashes
        print(f"⚠️  Duplikate: {duplicates_count}")
        
        if duplicates_count == 0:
            print("🎉 Keine Duplikate gefunden! Erstelle trotzdem UNIQUE Index...")
        else:
            print(f"\n🔍 TOP 10 DUPLIKAT-GRUPPEN:")
            cur.execute("""
                SELECT triple_hash, COUNT(*) as count,
                       MIN(subject) as example_subject,
                       MIN(predicate) as example_predicate,
                       MIN(object) as example_object
                FROM triples 
                WHERE triple_hash IS NOT NULL
                GROUP BY triple_hash
                HAVING COUNT(*) > 1
                ORDER BY count DESC
                LIMIT 10
            """)
            
            for triple_hash, count, subj, pred, obj in cur.fetchall():
                print(f"   {count}x: {subj[:25]}... → {pred[:15]}... → {obj[:25]}...")
                print(f"       Hash: {triple_hash}")
        
        # 2. BEREINIGUNG DURCHFÜHREN
        if duplicates_count > 0:
            print(f"\n🧹 BEREINIGUNG STARTEN:")
            print("-" * 30)
            
            # Strategie: Behalte das Triple mit der niedrigsten triple_id (ältestes)
            print("📋 Strategie: Behalte jeweils das älteste Triple (niedrigste triple_id)")
            
            start_time = time.time()
            
            # Lösche Duplikate (behalte nur MIN(triple_id) pro Hash)
            cur.execute("""
                DELETE FROM triples 
                WHERE triple_hash IS NOT NULL 
                  AND triple_id NOT IN (
                      SELECT MIN(triple_id) 
                      FROM triples 
                      WHERE triple_hash IS NOT NULL
                      GROUP BY triple_hash
                  )
            """)
            
            deleted_count = cur.rowcount
            duration = time.time() - start_time
            
            print(f"✅ {deleted_count} Duplikate entfernt in {duration:.2f}s")
            
            # Neue Statistiken
            cur.execute("SELECT COUNT(*) FROM triples")
            new_total = cur.fetchone()[0]
            print(f"📊 Neue Gesamt-Anzahl: {new_total} (vorher: {total_triples})")
            
            conn.commit()
        
        # 3. UNIQUE INDEX ERSTELLEN
        print(f"\n🔧 UNIQUE INDEX ERSTELLEN:")
        print("-" * 30)
        
        # Lösche alten normalen Index
        try:
            cur.execute("DROP INDEX IF EXISTS idx_triples_hash")
            print("🗑️  Alter Index entfernt")
        except Exception as e:
            print(f"⚠️  Alter Index-Entfernung: {e}")
        
        # Erstelle UNIQUE Index
        try:
            start_time = time.time()
            cur.execute("CREATE UNIQUE INDEX idx_triples_hash_unique ON triples(triple_hash)")
            duration = time.time() - start_time
            print(f"✅ UNIQUE Index erstellt in {duration:.2f}s")
            conn.commit()
            
        except sqlite3.IntegrityError as e:
            print(f"❌ UNIQUE Index-Erstellung fehlgeschlagen: {e}")
            print("⚠️  Es sind immer noch Duplikate vorhanden!")
            return False
        except Exception as e:
            print(f"❌ Unerwarteter Fehler: {e}")
            return False
        
        # 4. VERIFIKATION
        print(f"\n🔍 VERIFIKATION:")
        print("-" * 20)
        
        # Prüfe Index
        cur.execute("SELECT name, sql FROM sqlite_master WHERE type='index' AND name LIKE '%hash%'")
        indices = cur.fetchall()
        print(f"📇 Hash-Indices: {len(indices)}")
        for name, sql in indices:
            print(f"   {name}: {sql}")
        
        # Finale Statistiken
        cur.execute("SELECT COUNT(*) FROM triples")
        final_total = cur.fetchone()[0]
        
        cur.execute("SELECT COUNT(DISTINCT triple_hash) FROM triples WHERE triple_hash IS NOT NULL")
        final_unique = cur.fetchone()[0]
        
        cur.execute("SELECT COUNT(*) FROM triples WHERE triple_hash IS NOT NULL")
        final_with_hash = cur.fetchone()[0]
        
        print(f"📊 FINALE STATISTIKEN:")
        print(f"   Gesamt-Triples: {final_total}")
        print(f"   Mit Hash: {final_with_hash}")
        print(f"   Eindeutige Hashes: {final_unique}")
        print(f"   Duplikate: {final_with_hash - final_unique}")
        
        if final_with_hash == final_unique:
            print("🎉 BEREINIGUNG ERFOLGREICH! Keine Duplikate mehr!")
        else:
            print("❌ Bereinigung unvollständig!")
            
        # 5. TEST DER DUPLIKAT-PRÄVENTION
        print(f"\n🧪 TESTE DUPLIKAT-PRÄVENTION:")
        print("-" * 30)
        
        # Versuche ein Duplikat einzufügen
        test_hash = "test_duplicate_prevention_hash"
        
        try:
            cur.execute("""
                INSERT INTO triples(doc_id, subject, predicate, object, triple_hash)
                VALUES (?, ?, ?, ?, ?)
            """, ("test", "Test Subject", "Test Predicate", "Test Object", test_hash))
            print("✅ Erstes Test-Triple eingefügt")
            
            # Versuche Duplikat
            cur.execute("""
                INSERT INTO triples(doc_id, subject, predicate, object, triple_hash)
                VALUES (?, ?, ?, ?, ?)
            """, ("test", "Test Subject Duplicate", "Test Predicate", "Test Object", test_hash))
            print("❌ Duplikat wurde eingefügt - UNIQUE Index funktioniert nicht!")
            
        except sqlite3.IntegrityError:
            print("✅ Duplikat-Einfügung verhindert - UNIQUE Index funktioniert!")
        
        # Cleanup Test-Triple
        cur.execute("DELETE FROM triples WHERE triple_hash = ?", (test_hash,))
        conn.commit()
        
        conn.close()
        
        print("\n🎉 BEREINIGUNG ABGESCHLOSSEN!")
        return True
        
    except Exception as e:
        print(f"❌ Bereinigung fehlgeschlagen: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    success = cleanup_triple_duplicates()
    sys.exit(0 if success else 1)
