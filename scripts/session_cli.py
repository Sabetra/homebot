#!/usr/bin/env python3
"""
Kommandozeilen-Interface für Session-Management
==============================================
Einfaches Interface zum Verwalten psychologischer Sessions
"""

import os
import sys
from datetime import datetime

# Für den Import der psychologischen Module
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    from wellbeing_session_loader import WellbeingSessionLoader
except ImportError:
    print("❌ WellbeingSessionLoader nicht verfügbar")
    sys.exit(1)

class SessionCLI:
    """Kommandozeilen-Interface für Session-Management"""
    
    def __init__(self):
        self.loader = WellbeingSessionLoader()
        self.current_session_id = None
    
    def run(self):
        """Hauptschleife des CLIs"""
        print("🧠📋 Session-Management für psychologische Unterstützung")
        print("=" * 60)
        
        if not self.loader.db:
            print("❌ Session-Loader konnte nicht initialisiert werden")
            return
        
        while True:
            try:
                self.show_menu()
                choice = input("\n🔹 Wähle eine Option (1-7): ").strip()
                
                if choice == '1':
                    self.list_sessions()
                elif choice == '2':
                    self.show_session_details()
                elif choice == '3':
                    self.load_session()
                elif choice == '4':
                    self.show_recommendations()
                elif choice == '5':
                    self.create_new_session()
                elif choice == '6':
                    self.delete_session()
                elif choice == '7':
                    print("\n👋 Auf Wiedersehen!")
                    break
                else:
                    print("\n❌ Ungültige Auswahl. Bitte wähle 1-7.")
                
                input("\n📋 Drücke Enter um fortzufahren...")
                
            except KeyboardInterrupt:
                print("\n\n👋 Programm beendet.")
                break
            except Exception as e:
                print(f"\n❌ Fehler: {e}")
    
    def show_menu(self):
        """Zeigt das Hauptmenü"""
        print("\n" + "=" * 60)
        print("📋 HAUPTMENÜ")
        print("=" * 60)
        print("1. 📜 Sessions auflisten")
        print("2. 🔍 Session-Details anzeigen") 
        print("3. 📥 Session laden")
        print("4. 🎯 Session-Empfehlungen")
        print("5. ➕ Neue Session erstellen")
        print("6. 🗑️ Session löschen")
        print("7. 🚪 Beenden")
        
        if self.current_session_id:
            print(f"\n🔷 Aktuelle Session: {self.current_session_id[:12]}...")
    
    def list_sessions(self):
        """Listet alle Sessions auf"""
        print("\n📜 SESSIONS AUFLISTEN")
        print("-" * 40)
        
        include_closed = input("Geschlossene Sessions einschließen? (j/N): ").lower().startswith('j')
        
        sessions = self.loader.list_user_sessions("default_user", include_closed)
        
        if not sessions:
            print("📋 Keine Sessions gefunden.")
            print("💡 Starte eine neue Session mit Option 5.")
            return
        
        print(f"\n✅ {len(sessions)} Sessions gefunden:\n")
        
        for i, session in enumerate(sessions, 1):
            status_icon = "🟢" if session['status'] == 'active' else "⚪"
            session_id = session['id'][:12] + "..."
            created = session['created_at'][:19].replace('T', ' ')
            msg_count = session.get('interaction_count', 0)
            topics = ", ".join(session.get('main_topics', [])[:2])
            
            if not topics:
                topics = "Allgemeine Unterstützung"
            
            print(f"{i:2d}. {status_icon} {session_id}")
            print(f"    📅 {created}")
            print(f"    💬 {msg_count} Nachrichten")
            print(f"    🏷️ {topics}")
            
            # Vorschau
            preview = session.get('preview', '')
            if preview:
                preview_short = preview[:80] + "..." if len(preview) > 80 else preview
                print(f"    📝 {preview_short}")
            
            print()
    
    def show_session_details(self):
        """Zeigt Details einer Session"""
        print("\n🔍 SESSION-DETAILS")
        print("-" * 40)
        
        session_id = self.get_session_id_input()
        if not session_id:
            return
        
        # Session finden
        sessions = self.loader.list_user_sessions("default_user", True)
        session = next((s for s in sessions if s['id'].startswith(session_id)), None)
        
        if not session:
            print(f"❌ Session nicht gefunden: {session_id}")
            return
        
        # Details anzeigen
        print(f"\n📋 Session-Details:")
        print(f"🆔 ID: {session['id']}")
        print(f"📅 Erstellt: {session['created_at'][:19].replace('T', ' ')}")
        print(f"🔄 Update: {session['updated_at'][:19].replace('T', ' ')}")
        print(f"📊 Status: {session['status']}")
        print(f"💬 Nachrichten: {session.get('interaction_count', 0)}")
        print(f"👤 User-Nachrichten: {session.get('user_message_count', 0)}")
        print(f"⏱️ Dauer: {session.get('duration_minutes', 0)} Minuten")
        print(f"🏷️ Themen: {', '.join(session.get('main_topics', []))}")
        
        # Vorschau
        preview = session.get('preview', '')
        if preview:
            print(f"\n📝 Gesprächsvorschau:")
            print(f"   {preview}")
        
        # Gesprächsverlauf
        try:
            history = self.loader.db.get_session_history(session['id'])
            if history:
                print(f"\n📜 Letzten 5 Nachrichten:")
                for entry in history[-5:]:
                    role = "👤" if entry['role'] == 'user' else "🤖"
                    timestamp = entry.get('timestamp', '')[11:19]
                    content = entry['content'][:100] + "..." if len(entry['content']) > 100 else entry['content']
                    print(f"   {role} ({timestamp}): {content}")
        except Exception as e:
            print(f"⚠️ Verlauf nicht verfügbar: {e}")
    
    def load_session(self):
        """Lädt eine Session"""
        print("\n📥 SESSION LADEN")
        print("-" * 40)
        
        session_id = self.get_session_id_input()
        if not session_id:
            return
        
        # Vollständige Session-ID finden
        sessions = self.loader.list_user_sessions("default_user", True)
        full_session = next((s for s in sessions if s['id'].startswith(session_id)), None)
        
        if not full_session:
            print(f"❌ Session nicht gefunden: {session_id}")
            return
        
        full_session_id = full_session['id']
        
        try:
            success = self.loader.load_session_context(full_session_id)
            
            if success:
                self.current_session_id = full_session_id
                print(f"✅ Session erfolgreich geladen!")
                print(f"🆔 Session-ID: {full_session_id}")
                print(f"💬 {full_session.get('interaction_count', 0)} Nachrichten verfügbar")
                print("\n🎯 Du kannst jetzt im psychologischen Modus des Bots weiterführende Gespräche führen.")
                print("   Der Kontext der Session ist wiederhergestellt.")
            else:
                print("❌ Session konnte nicht geladen werden.")
                
        except Exception as e:
            print(f"❌ Session-Laden fehlgeschlagen: {e}")
    
    def show_recommendations(self):
        """Zeigt Session-Empfehlungen"""
        print("\n🎯 SESSION-EMPFEHLUNGEN")
        print("-" * 40)
        
        try:
            recommendations = self.loader.recommend_sessions("default_user", 5)
            
            if not recommendations:
                print("💡 Keine spezifischen Empfehlungen verfügbar.")
                print("   Starte eine neue Session oder lade eine bestehende.")
                return
            
            print("🎯 Empfohlene Sessions zum Fortsetzen:\n")
            
            for i, rec in enumerate(recommendations, 1):
                session = rec['session']
                score = rec['score']
                reasons = rec['reasons']
                
                print(f"{i}. 📋 {session['id'][:12]}... (Score: {score}/10)")
                print(f"   📅 {session['created_at'][:19].replace('T', ' ')}")
                print(f"   💬 {session.get('interaction_count', 0)} Nachrichten")
                print(f"   🏷️ {', '.join(session.get('main_topics', []))}")
                print(f"   🎯 Grund: {', '.join(reasons)}")
                print(f"   📝 {session.get('preview', '')[:100]}...")
                print()
                
        except Exception as e:
            print(f"❌ Empfehlungen konnten nicht geladen werden: {e}")
    
    def create_new_session(self):
        """Erstellt eine neue Session"""
        print("\n➕ NEUE SESSION ERSTELLEN")
        print("-" * 40)
        
        confirm = input("Möchtest du eine neue Session erstellen? (j/N): ").lower().startswith('j')
        
        if not confirm:
            print("❌ Abgebrochen.")
            return
        
        try:
            new_session_id = self.loader.session_manager.create_or_restore_session("default_user", False)
            
            print(f"✅ Neue Session erstellt!")
            print(f"🆔 Session-ID: {new_session_id}")
            print("\n🎯 Du kannst jetzt eine neue psychologische Sitzung im Bot beginnen.")
            
            self.current_session_id = new_session_id
            
        except Exception as e:
            print(f"❌ Session-Erstellung fehlgeschlagen: {e}")
    
    def delete_session(self):
        """Löscht eine Session"""
        print("\n🗑️ SESSION LÖSCHEN")
        print("-" * 40)
        print("⚠️ WARNUNG: Diese Aktion kann nicht rückgängig gemacht werden!")
        
        session_id = self.get_session_id_input()
        if not session_id:
            return
        
        # Vollständige Session-ID finden
        sessions = self.loader.list_user_sessions("default_user", True)
        full_session = next((s for s in sessions if s['id'].startswith(session_id)), None)
        
        if not full_session:
            print(f"❌ Session nicht gefunden: {session_id}")
            return
        
        full_session_id = full_session['id']
        
        # Bestätigung
        print(f"\n🔍 Session-Details:")
        print(f"   🆔 ID: {full_session_id}")
        print(f"   📅 Erstellt: {full_session['created_at'][:19].replace('T', ' ')}")
        print(f"   💬 Nachrichten: {full_session.get('interaction_count', 0)}")
        
        confirm = input(f"\n❗ Wirklich löschen? (j/N): ").lower().startswith('j')
        
        if not confirm:
            print("❌ Löschung abgebrochen.")
            return
        
        try:
            self.loader.db.delete_session(full_session_id)
            print(f"✅ Session erfolgreich gelöscht.")
            
            if self.current_session_id == full_session_id:
                self.current_session_id = None
                
        except Exception as e:
            print(f"❌ Session-Löschung fehlgeschlagen: {e}")
    
    def get_session_id_input(self) -> str:
        """Holt Session-ID Eingabe vom Benutzer"""
        session_id = input("🆔 Session-ID (ersten 8+ Zeichen): ").strip()
        
        if len(session_id) < 8:
            print("❌ Session-ID zu kurz. Mindestens 8 Zeichen benötigt.")
            return ""
        
        return session_id

def main():
    """Hauptfunktion"""
    cli = SessionCLI()
    cli.run()

if __name__ == "__main__":
    main()
