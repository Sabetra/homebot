#!/usr/bin/env python3
"""
Erweiterung des Session-Managers um gezieltes Session-Laden
==========================================================
Erweitert den WellbeingSessionManager um Funktionen zum
gezielten Laden und Verwalten von Sessions.
"""

import os
import shutil
from datetime import datetime

def extend_session_manager_with_loader():
    """Erweitert session_manager.py um Session-Loading-Funktionen"""
    
    session_manager_file = "wellbeing/session_manager.py"
    
    if not os.path.exists(session_manager_file):
        print(f"❌ {session_manager_file} nicht gefunden!")
        return False
    
    # Backup erstellen
    backup_file = f"wellbeing/session_manager_backup_loader_{datetime.now().strftime('%Y%m%d_%H%M%S')}.py"
    shutil.copy2(session_manager_file, backup_file)
    print(f"✓ Backup erstellt: {backup_file}")
    
    # Datei lesen
    with open(session_manager_file, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # Neue Methoden hinzufügen
    new_methods = '''
    
    def load_specific_session(self, session_id: str) -> bool:
        """
        Lädt eine spezifische Session in den aktiven Kontext
        
        Args:
            session_id: Session-ID die geladen werden soll
            
        Returns:
            True wenn erfolgreich geladen
        """
        try:
            # Prüfe ob Session existiert
            sessions = self.db.get_user_sessions("default_user")  # Einfache Implementierung
            target_session = next((s for s in sessions if s['id'] == session_id), None)
            
            if not target_session:
                logger.warning(f"⚠️ Session nicht gefunden: {session_id}")
                return False
            
            # Lade Session-Kontext
            self._load_session_context(session_id)
            
            # Setze als aktuelle Session
            self._current_session_id = session_id
            
            logger.info(f"✅ Session erfolgreich geladen: {session_id}")
            return True
            
        except Exception as e:
            logger.error(f"❌ Session-Laden fehlgeschlagen: {e}")
            return False
    
    def get_session_list(self, user_id: str = "default_user", include_closed: bool = True) -> List[Dict[str, Any]]:
        """
        Holt Liste aller Sessions eines Benutzers
        
        Args:
            user_id: Benutzer-ID
            include_closed: Auch geschlossene Sessions einschließen
            
        Returns:
            Liste von Sessions mit Metadaten
        """
        try:
            # Lade Sessions aus Datenbank
            if include_closed:
                sessions = self.db.get_user_sessions(user_id)
            else:
                sessions = self.db.get_user_sessions(user_id, status='active')
            
            # Ergänze um Interaktions-Anzahl
            for session in sessions:
                try:
                    history = self.db.get_session_history(session['id'])
                    session['interaction_count'] = len(history)
                    session['user_message_count'] = len([h for h in history if h['role'] == 'user'])
                except Exception:
                    session['interaction_count'] = 0
                    session['user_message_count'] = 0
            
            logger.info(f"📋 {len(sessions)} Sessions gefunden für User: {user_id}")
            return sessions
            
        except Exception as e:
            logger.error(f"❌ Session-Liste laden fehlgeschlagen: {e}")
            return []
    
    def get_current_session_id(self) -> Optional[str]:
        """
        Gibt die ID der aktuell aktiven Session zurück
        
        Returns:
            Session-ID oder None
        """
        return getattr(self, '_current_session_id', None)
    
    def switch_to_session(self, session_id: str) -> bool:
        """
        Wechselt zur angegebenen Session
        
        Args:
            session_id: Ziel-Session-ID
            
        Returns:
            True wenn erfolgreich gewechselt
        """
        try:
            # Speichere aktuelle Session wenn vorhanden
            current_session_id = self.get_current_session_id()
            if current_session_id and current_session_id in self._active_sessions:
                self._save_session_state(current_session_id)
            
            # Lade neue Session
            success = self.load_specific_session(session_id)
            
            if success:
                logger.info(f"🔄 Zu Session gewechselt: {session_id}")
                return True
            else:
                logger.warning(f"⚠️ Session-Wechsel fehlgeschlagen: {session_id}")
                return False
                
        except Exception as e:
            logger.error(f"❌ Session-Wechsel-Fehler: {e}")
            return False
    
    def _save_session_state(self, session_id: str):
        """
        Speichert den aktuellen Zustand einer Session
        
        Args:
            session_id: Session-ID
        """
        try:
            if session_id in self._active_sessions:
                context = self._active_sessions[session_id]
                
                # Aktualisiere Datenbank mit letzter Interaktion
                # (Implementierung kann erweitert werden)
                logger.debug(f"💾 Session-Zustand gespeichert: {session_id}")
                
        except Exception as e:
            logger.warning(f"⚠️ Session-Zustand speichern fehlgeschlagen: {e}")
    
    def create_session_summary(self, session_id: str) -> Optional[str]:
        """
        Erstellt eine Zusammenfassung einer Session
        
        Args:
            session_id: Session-ID
            
        Returns:
            Session-Zusammenfassung oder None
        """
        try:
            history = self.db.get_session_history(session_id)
            
            if not history:
                return None
            
            # Einfache Zusammenfassung erstellen
            user_messages = [h for h in history if h['role'] == 'user']
            total_words = sum(len(h['content'].split()) for h in user_messages)
            
            # Extrahiere erste und letzte Nachricht
            first_msg = user_messages[0]['content'][:100] + "..." if user_messages else "Keine Nachrichten"
            last_msg = user_messages[-1]['content'][:100] + "..." if len(user_messages) > 1 else ""
            
            summary = f"Session mit {len(history)} Nachrichten ({total_words} Wörter). "
            summary += f"Begann mit: '{first_msg}'"
            
            if last_msg:
                summary += f" | Zuletzt: '{last_msg}'"
            
            return summary
            
        except Exception as e:
            logger.error(f"❌ Session-Zusammenfassung fehlgeschlagen: {e}")
            return None
    
    def get_session_statistics(self) -> Dict[str, Any]:
        """
        Gibt Statistiken über Session-Management zurück
        
        Returns:
            Dictionary mit Statistiken
        """
        try:
            stats = {
                'total_active_sessions': len(self._active_sessions),
                'current_session_id': self.get_current_session_id(),
                'database_stats': self.db.get_database_stats() if self.db else {},
                'cache_size': len(getattr(self, '_session_cache', {}))
            }
            
            return stats
            
        except Exception as e:
            logger.error(f"❌ Session-Statistiken fehlgeschlagen: {e}")
            return {}'''
    
    # Füge neue Methoden vor der letzten Zeile der Klasse ein
    # Finde das Ende der Klasse (vereinfachte Implementierung)
    class_end_pattern = "\n\n# "  # Annahme: Klasse endet vor Kommentaren oder neuen Definitionen
    
    if class_end_pattern in content:
        insertion_point = content.find(class_end_pattern)
        content = content[:insertion_point] + new_methods + content[insertion_point:]
    else:
        # Fallback: Füge am Ende hinzu
        content = content.rstrip() + new_methods + "\n"
    
    # Datei zurückschreiben
    with open(session_manager_file, 'w', encoding='utf-8') as f:
        f.write(content)
    
    print("✅ Session-Manager erfolgreich erweitert!")
    print("✓ load_specific_session() hinzugefügt")
    print("✓ get_session_list() hinzugefügt")
    print("✓ switch_to_session() hinzugefügt")
    print("✓ create_session_summary() hinzugefügt")
    print("✓ get_session_statistics() hinzugefügt")
    
    return True

def create_session_interface_gui():
    """Erstellt GUI-Interface für Session-Management"""
    
    gui_content = '''#!/usr/bin/env python3
"""
GUI-Interface für Session-Management
===================================
Benutzerfreundliche Oberfläche für das Laden und Verwalten 
von psychologischen Sessions.
"""

import sys
import os
from datetime import datetime
from typing import List, Dict, Any, Optional

# GUI-Imports
try:
    from PyQt6.QtWidgets import (QApplication, QMainWindow, QVBoxLayout, QHBoxLayout, 
                                QWidget, QPushButton, QListWidget, QListWidgetItem, 
                                QLabel, QTextEdit, QGroupBox, QSplitter, QMessageBox,
                                QComboBox, QCheckBox, QLineEdit)
    from PyQt6.QtCore import Qt, pyqtSignal, QThread
    from PyQt6.QtGui import QFont
except ImportError:
    print("❌ PyQt6 nicht verfügbar - installiere mit: pip install PyQt6")
    sys.exit(1)

# Projektspezifische Imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

class SessionListWidget(QListWidget):
    """Erweiterte Liste für Sessions mit Metadaten"""
    
    session_selected = pyqtSignal(str)  # Sendet Session-ID
    
    def __init__(self):
        super().__init__()
        self.sessions_data = {}
        self.itemClicked.connect(self._on_item_clicked)
        
    def add_session(self, session: Dict[str, Any]):
        """Fügt Session zur Liste hinzu"""
        session_id = session['id']
        self.sessions_data[session_id] = session
        
        # Erstelle Anzeige-Text
        topics = ', '.join(session.get('main_topics', ['Unbekannt']))
        interactions = session.get('interaction_count', 0)
        days_ago = session.get('last_interaction_days_ago', 0)
        
        display_text = f"{session_id[:12]}... | {topics} | {interactions} Nachrichten"
        if days_ago > 0:
            display_text += f" | vor {days_ago} Tagen"
        
        item = QListWidgetItem(display_text)
        item.setData(Qt.ItemDataRole.UserRole, session_id)
        
        # Färbung basierend auf Aktualität
        if days_ago == 0:
            item.setBackground(Qt.GlobalColor.lightGreen)
        elif days_ago <= 7:
            item.setBackground(Qt.GlobalColor.lightYellow)
        
        self.addItem(item)
    
    def _on_item_clicked(self, item):
        """Handle Session-Auswahl"""
        session_id = item.data(Qt.ItemDataRole.UserRole)
        if session_id:
            self.session_selected.emit(session_id)
    
    def get_selected_session_id(self) -> Optional[str]:
        """Gibt aktuell ausgewählte Session-ID zurück"""
        current_item = self.currentItem()
        if current_item:
            return current_item.data(Qt.ItemDataRole.UserRole)
        return None

class SessionManagerGUI(QMainWindow):
    """Hauptfenster für Session-Management"""
    
    def __init__(self):
        super().__init__()
        self.session_loader = None
        self.current_session_id = None
        
        self.init_ui()
        self.init_session_loader()
        self.load_sessions()
    
    def init_ui(self):
        """Initialisiert die Benutzeroberfläche"""
        self.setWindowTitle("🧠 Psychologische Sessions - Verwalten & Laden")
        self.setGeometry(200, 200, 1000, 700)
        
        # Hauptwidget
        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        layout = QHBoxLayout(main_widget)
        
        # Splitter für Links/Rechts-Layout
        splitter = QSplitter(Qt.Orientation.Horizontal)
        layout.addWidget(splitter)
        
        # Linke Seite: Session-Liste
        left_widget = self._create_session_list_widget()
        splitter.addWidget(left_widget)
        
        # Rechte Seite: Session-Details
        right_widget = self._create_session_details_widget()
        splitter.addWidget(right_widget)
        
        # Splitter-Größen
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)
    
    def _create_session_list_widget(self) -> QWidget:
        """Erstellt Session-Listen-Widget"""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        
        # Header
        header = QLabel("📋 Verfügbare Sessions")
        header.setFont(QFont("Arial", 12, QFont.Weight.Bold))
        layout.addWidget(header)
        
        # Filter-Optionen
        filter_layout = QHBoxLayout()
        
        self.include_closed_cb = QCheckBox("Geschlossene Sessions")
        self.include_closed_cb.setChecked(True)
        self.include_closed_cb.stateChanged.connect(self.load_sessions)
        filter_layout.addWidget(self.include_closed_cb)
        
        refresh_btn = QPushButton("🔄 Aktualisieren")
        refresh_btn.clicked.connect(self.load_sessions)
        filter_layout.addWidget(refresh_btn)
        
        layout.addLayout(filter_layout)
        
        # Session-Liste
        self.session_list = SessionListWidget()
        self.session_list.session_selected.connect(self.show_session_details)
        layout.addWidget(self.session_list)
        
        # Aktionen
        actions_layout = QVBoxLayout()
        
        self.load_session_btn = QPushButton("📂 Session laden")
        self.load_session_btn.clicked.connect(self.load_selected_session)
        self.load_session_btn.setEnabled(False)
        actions_layout.addWidget(self.load_session_btn)
        
        self.delete_session_btn = QPushButton("🗑️ Session löschen")
        self.delete_session_btn.clicked.connect(self.delete_selected_session)
        self.delete_session_btn.setEnabled(False)
        actions_layout.addWidget(self.delete_session_btn)
        
        layout.addLayout(actions_layout)
        
        return widget
    
    def _create_session_details_widget(self) -> QWidget:
        """Erstellt Session-Details-Widget"""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        
        # Header
        self.details_header = QLabel("💭 Session-Details")
        self.details_header.setFont(QFont("Arial", 12, QFont.Weight.Bold))
        layout.addWidget(self.details_header)
        
        # Session-Info
        self.session_info_group = QGroupBox("Session-Informationen")
        info_layout = QVBoxLayout(self.session_info_group)
        
        self.session_info_label = QLabel("Keine Session ausgewählt")
        info_layout.addWidget(self.session_info_label)
        
        layout.addWidget(self.session_info_group)
        
        # Session-Verlauf
        self.history_group = QGroupBox("Gesprächsverlauf")
        history_layout = QVBoxLayout(self.history_group)
        
        self.history_text = QTextEdit()
        self.history_text.setReadOnly(True)
        history_layout.addWidget(self.history_text)
        
        layout.addWidget(self.history_group)
        
        return widget
    
    def init_session_loader(self):
        """Initialisiert Session-Loader"""
        try:
            from psychological_session_loader import PsychologicalSessionLoader
            self.session_loader = PsychologicalSessionLoader()
            
            if self.session_loader.db:
                self.statusBar().showMessage("✅ Session-Loader bereit")
            else:
                self.statusBar().showMessage("❌ Session-Loader Fehler")
                
        except Exception as e:
            QMessageBox.critical(self, "Fehler", f"Session-Loader konnte nicht initialisiert werden:\\n{e}")
    
    def load_sessions(self):
        """Lädt Sessions in die Liste"""
        if not self.session_loader:
            return
        
        self.session_list.clear()
        include_closed = self.include_closed_cb.isChecked()
        
        try:
            sessions = self.session_loader.list_user_sessions("default_user", include_closed)
            
            for session in sessions:
                self.session_list.add_session(session)
            
            self.statusBar().showMessage(f"📋 {len(sessions)} Sessions geladen")
            
        except Exception as e:
            QMessageBox.warning(self, "Warnung", f"Sessions konnten nicht geladen werden:\\n{e}")
    
    def show_session_details(self, session_id: str):
        """Zeigt Details einer Session"""
        self.current_session_id = session_id
        self.load_session_btn.setEnabled(True)
        self.delete_session_btn.setEnabled(True)
        
        if not self.session_loader:
            return
        
        try:
            details = self.session_loader.get_session_details(session_id)
            
            if details:
                # Session-Info aktualisieren
                info_text = f"""
Session-ID: {session_id}
Gesamtinteraktionen: {details['total_interactions']}
Benutzer-Nachrichten: {details['user_messages']}
Assistent-Nachrichten: {details['assistant_messages']}
Hauptthemen: {', '.join(details['main_topics'])}
Gesprächsverlauf: {' → '.join(details['conversation_flow'])}
Stimmung: {', '.join(details['mood_progression'])}
"""
                self.session_info_label.setText(info_text.strip())
                
                # Historie anzeigen (begrenzt)
                history = details['full_history']
                history_text = ""
                
                for i, interaction in enumerate(history[-10:], 1):  # Letzte 10 Nachrichten
                    role = "👤 Du" if interaction['role'] == 'user' else "🤖 Assistent"
                    timestamp = interaction['timestamp'][:19]
                    content = interaction['content'][:200] + "..." if len(interaction['content']) > 200 else interaction['content']
                    
                    history_text += f"{i}. {role} ({timestamp}):\\n{content}\\n\\n"
                
                self.history_text.setPlainText(history_text)
                
                self.details_header.setText(f"💭 Session-Details: {session_id[:12]}...")
                
        except Exception as e:
            QMessageBox.warning(self, "Warnung", f"Session-Details konnten nicht geladen werden:\\n{e}")
    
    def load_selected_session(self):
        """Lädt die ausgewählte Session"""
        if not self.current_session_id or not self.session_loader:
            return
        
        try:
            success = self.session_loader.load_session_context(self.current_session_id)
            
            if success:
                QMessageBox.information(self, "Erfolg", 
                    f"Session erfolgreich geladen!\\n\\n"
                    f"Session-ID: {self.current_session_id}\\n"
                    f"Du kannst jetzt im psychologischen Modus weiterführende Gespräche führen.")
            else:
                QMessageBox.warning(self, "Fehler", "Session konnte nicht geladen werden.")
                
        except Exception as e:
            QMessageBox.critical(self, "Fehler", f"Session-Laden fehlgeschlagen:\\n{e}")
    
    def delete_selected_session(self):
        """Löscht die ausgewählte Session"""
        if not self.current_session_id:
            return
        
        reply = QMessageBox.question(self, "Session löschen", 
            f"Möchtest du diese Session wirklich löschen?\\n\\n"
            f"Session-ID: {self.current_session_id}\\n\\n"
            f"Diese Aktion kann nicht rückgängig gemacht werden!",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        
        if reply == QMessageBox.StandardButton.Yes:
            # TODO: Implementiere Session-Löschung
            QMessageBox.information(self, "Info", "Session-Löschung noch nicht implementiert.")

def main():
    """Hauptfunktion"""
    app = QApplication(sys.argv)
    
    window = SessionManagerGUI()
    window.show()
    
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
'''
    
    with open('session_manager_gui.py', 'w', encoding='utf-8') as f:
        f.write(gui_content)
    
    print("✓ GUI-Interface erstellt: session_manager_gui.py")

if __name__ == "__main__":
    print("🧠📋 Session-Management Erweiterung")
    print("="*50)
    
    # Session-Manager erweitern
    success = extend_session_manager_with_loader()
    
    if success:
        # GUI-Interface erstellen
        create_session_interface_gui()
        
        print("\\n✅ SESSION-MANAGEMENT ERFOLGREICH ERWEITERT!")
        print("\\n📋 Was wurde hinzugefügt:")
        print("✓ load_specific_session() - Lädt bestimmte Session")
        print("✓ get_session_list() - Listet alle Sessions auf")
        print("✓ switch_to_session() - Wechselt zwischen Sessions")
        print("✓ create_session_summary() - Erstellt Session-Zusammenfassungen")
        print("✓ session_manager_gui.py - Benutzerfreundliche GUI")
        
        print("\\n📋 Verwendung:")
        print("1. python psychological_session_loader.py  # Test & Demo")
        print("2. python session_manager_gui.py  # GUI-Interface")
        print("3. Im Bot: Sessions automatisch laden/wechseln")
        
    else:
        print("\\n❌ SESSION-MANAGEMENT ERWEITERUNG FEHLGESCHLAGEN")
        print("🔧 Überprüfe session_manager.py manuell")
