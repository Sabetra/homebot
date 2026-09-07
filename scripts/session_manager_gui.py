#!/usr/bin/env python3
"""
GUI für Session-Management der psychologischen Unterstützung
===========================================================
Benutzerfreundliche Oberfläche zum Verwalten von psychologischen Sessions
"""

import sys
import os
from datetime import datetime
from typing import Dict, List, Optional, Any

# PyQt6 imports
try:
    from PyQt6.QtWidgets import (QApplication, QMainWindow, QVBoxLayout, QHBoxLayout, 
                                QWidget, QPushButton, QListWidget, QListWidgetItem, 
                                QTextEdit, QLabel, QGroupBox, QCheckBox, QMessageBox,
                                QSplitter, QFrame, QScrollArea)
    from PyQt6.QtCore import Qt, QTimer, pyqtSignal
    from PyQt6.QtGui import QFont, QIcon, QPalette, QColor
except ImportError:
    print("❌ PyQt6 nicht installiert. Installiere mit: pip install PyQt6")
    sys.exit(1)

# Für den Import der psychologischen Module
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    from wellbeing_session_loader import WellbeingSessionLoader
except ImportError:
    print("❌ WellbeingSessionLoader nicht verfügbar")
    sys.exit(1)

class SessionListWidget(QListWidget):
    """Erweiterte Liste für Sessions mit Rich-Content"""
    
    def __init__(self):
        super().__init__()
        self.setMinimumWidth(350)
        self.setMaximumHeight(400)
    
    def add_session(self, session: Dict[str, Any]):
        """Fügt eine Session zur Liste hinzu"""
        # Session-Info formatieren
        session_text = self._format_session_text(session)
        
        item = QListWidgetItem(session_text)
        item.setData(Qt.ItemDataRole.UserRole, session)
        
        # Status-abhängige Farben
        if session['status'] == 'active':
            item.setBackground(QColor(230, 255, 230))  # Hellgrün
        elif session['status'] == 'closed':
            item.setBackground(QColor(245, 245, 245))  # Hellgrau
        
        self.addItem(item)
    
    def _format_session_text(self, session: Dict[str, Any]) -> str:
        """Formatiert Session-Text für Anzeige"""
        session_id = session['id'][:12] + "..."
        created = session['created_at'][:19].replace('T', ' ')
        msg_count = session.get('interaction_count', 0)
        topics = ", ".join(session.get('main_topics', [])[:2])
        
        if not topics:
            topics = "Allgemeine Unterstützung"
        
        status_icon = "🟢" if session['status'] == 'active' else "⚪"
        
        return f"{status_icon} {session_id}\n📅 {created}\n💬 {msg_count} Nachrichten\n🏷️ {topics}"

class SessionManagerGUI(QMainWindow):
    """Hauptfenster für Session-Management"""
    
    def __init__(self):
        super().__init__()
        self.session_loader = None
        self.current_session_id = None
        
        self.init_ui()
        self.init_session_loader()
        self.load_sessions()
        
        # Auto-Update Timer
        self.timer = QTimer()
        self.timer.timeout.connect(self.auto_refresh)
        self.timer.start(30000)  # 30 Sekunden
    
    def init_ui(self):
        """Initialisiert die Benutzeroberfläche"""
        self.setWindowTitle("🧠 Psychologische Session-Verwaltung")
        self.setGeometry(100, 100, 900, 700)
        
        # Hauptwidget
        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        
        # Hauptlayout
        main_layout = QVBoxLayout(main_widget)
        
        # Titel
        title_label = QLabel("🧠📋 Session-Management für psychologische Unterstützung")
        title_label.setFont(QFont("Arial", 16, QFont.Weight.Bold))
        title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        main_layout.addWidget(title_label)
        
        # Splitter für Links/Rechts
        splitter = QSplitter(Qt.Orientation.Horizontal)
        main_layout.addWidget(splitter)
        
        # Linke Seite: Session-Liste
        left_widget = self.create_session_list_widget()
        splitter.addWidget(left_widget)
        
        # Rechte Seite: Session-Details
        right_widget = self.create_session_details_widget()
        splitter.addWidget(right_widget)
        
        # Button-Leiste
        button_layout = QHBoxLayout()
        
        self.load_btn = QPushButton("📥 Session laden")
        self.load_btn.clicked.connect(self.load_selected_session)
        button_layout.addWidget(self.load_btn)
        
        self.refresh_btn = QPushButton("🔄 Aktualisieren")
        self.refresh_btn.clicked.connect(self.load_sessions)
        button_layout.addWidget(self.refresh_btn)
        
        self.new_session_btn = QPushButton("➕ Neue Session")
        self.new_session_btn.clicked.connect(self.create_new_session)
        button_layout.addWidget(self.new_session_btn)
        
        self.delete_btn = QPushButton("🗑️ Löschen")
        self.delete_btn.clicked.connect(self.delete_selected_session)
        self.delete_btn.setStyleSheet("QPushButton { background-color: #ffcccc; }")
        button_layout.addWidget(self.delete_btn)
        
        button_layout.addStretch()
        
        self.help_btn = QPushButton("❓ Hilfe")
        self.help_btn.clicked.connect(self.show_help)
        button_layout.addWidget(self.help_btn)
        
        main_layout.addLayout(button_layout)
        
        # Statusbar
        self.statusBar().showMessage("🔄 Initialisiere Session-Manager...")
    
    def create_session_list_widget(self) -> QWidget:
        """Erstellt das Widget für die Session-Liste"""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        
        # Header
        list_header = QLabel("📋 Verfügbare Sessions")
        list_header.setFont(QFont("Arial", 12, QFont.Weight.Bold))
        layout.addWidget(list_header)
        
        # Filter-Optionen
        filter_group = QGroupBox("Filter")
        filter_layout = QVBoxLayout(filter_group)
        
        self.include_closed_cb = QCheckBox("Geschlossene Sessions anzeigen")
        self.include_closed_cb.setChecked(True)
        self.include_closed_cb.stateChanged.connect(self.load_sessions)
        filter_layout.addWidget(self.include_closed_cb)
        
        layout.addWidget(filter_group)
        
        # Session-Liste
        self.session_list = SessionListWidget()
        self.session_list.itemSelectionChanged.connect(self.on_session_selected)
        layout.addWidget(self.session_list)
        
        # Session-Counter
        self.session_count_label = QLabel("📊 Keine Sessions geladen")
        layout.addWidget(self.session_count_label)
        
        return widget
    
    def create_session_details_widget(self) -> QWidget:
        """Erstellt das Widget für Session-Details"""
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
        self.history_text.setMaximumHeight(300)
        history_layout.addWidget(self.history_text)
        
        layout.addWidget(self.history_group)
        
        # Empfehlungen
        self.recommendations_group = QGroupBox("🎯 Session-Empfehlungen")
        rec_layout = QVBoxLayout(self.recommendations_group)
        
        self.recommendations_text = QTextEdit()
        self.recommendations_text.setReadOnly(True)
        self.recommendations_text.setMaximumHeight(150)
        rec_layout.addWidget(self.recommendations_text)
        
        layout.addWidget(self.recommendations_group)
        
        return widget
    
    def init_session_loader(self):
        """Initialisiert Session-Loader"""
        try:
            self.session_loader = WellbeingSessionLoader()
            
            if self.session_loader.db:
                self.statusBar().showMessage("✅ Session-Loader bereit")
            else:
                self.statusBar().showMessage("❌ Session-Loader Fehler")
                
        except Exception as e:
            QMessageBox.critical(self, "Fehler", f"Session-Loader konnte nicht initialisiert werden:\n{e}")
    
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
            
            self.session_count_label.setText(f"📊 {len(sessions)} Sessions geladen")
            self.statusBar().showMessage(f"📋 {len(sessions)} Sessions geladen")
            
            # Lade Empfehlungen
            self.load_recommendations()
            
        except Exception as e:
            QMessageBox.warning(self, "Warnung", f"Sessions konnten nicht geladen werden:\n{e}")
    
    def load_recommendations(self):
        """Lädt Session-Empfehlungen"""
        if not self.session_loader:
            return
        
        try:
            recommendations = self.session_loader.recommend_sessions("default_user", 3)
            
            if recommendations:
                rec_text = "🎯 Empfohlene Sessions zum Fortsetzen:\n\n"
                for i, rec in enumerate(recommendations, 1):
                    rec_text += f"{i}. {rec['recommendation_text']}\n"
                    rec_text += f"   📊 Score: {rec['score']}/10\n\n"
            else:
                rec_text = "💡 Keine spezifischen Empfehlungen.\nStarte eine neue Session oder lade eine bestehende."
            
            self.recommendations_text.setText(rec_text)
            
        except Exception as e:
            self.recommendations_text.setText(f"❌ Empfehlungen konnten nicht geladen werden: {e}")
    
    def on_session_selected(self):
        """Behandelt Session-Auswahl"""
        current_item = self.session_list.currentItem()
        
        if not current_item:
            return
        
        session = current_item.data(Qt.ItemDataRole.UserRole)
        self.current_session_id = session['id']
        
        # Session-Info anzeigen
        self.display_session_info(session)
        
        # Session-History laden
        self.load_session_history(session['id'])
    
    def display_session_info(self, session: Dict[str, Any]):
        """Zeigt Session-Informationen an"""
        info_text = f"""
🆔 Session-ID: {session['id']}
📅 Erstellt: {session['created_at'][:19].replace('T', ' ')}
🔄 Letztes Update: {session['updated_at'][:19].replace('T', ' ')}
📊 Status: {session['status']}
💬 Nachrichten: {session.get('interaction_count', 0)}
👤 User-Nachrichten: {session.get('user_message_count', 0)}
⏱️ Dauer: {session.get('duration_minutes', 0)} Minuten
🏷️ Themen: {', '.join(session.get('main_topics', []))}
📝 Vorschau: {session.get('preview', 'Keine Vorschau verfügbar')}
"""
        
        self.session_info_label.setText(info_text)
    
    def load_session_history(self, session_id: str):
        """Lädt Session-Verlauf"""
        if not self.session_loader:
            return
        
        try:
            history = self.session_loader.db.get_session_history(session_id)
            
            if history:
                history_text = "📜 Gesprächsverlauf:\n\n"
                
                for entry in history[-10:]:  # Letzte 10 Nachrichten
                    role = "👤 Benutzer" if entry['role'] == 'user' else "🤖 Assistant"
                    timestamp = entry.get('timestamp', '')[11:19]  # Nur Zeit
                    content = entry['content'][:200] + "..." if len(entry['content']) > 200 else entry['content']
                    
                    history_text += f"{role} ({timestamp}):\n{content}\n\n"
            else:
                history_text = "📝 Keine Nachrichten in dieser Session."
            
            self.history_text.setText(history_text)
            
        except Exception as e:
            self.history_text.setText(f"❌ Session-Verlauf konnte nicht geladen werden: {e}")
    
    def load_selected_session(self):
        """Lädt die ausgewählte Session"""
        if not self.current_session_id or not self.session_loader:
            return
        
        try:
            success = self.session_loader.load_session_context(self.current_session_id)
            
            if success:
                QMessageBox.information(self, "Erfolg", 
                    f"Session erfolgreich geladen!\n\n"
                    f"Session-ID: {self.current_session_id}\n"
                    f"Du kannst jetzt im psychologischen Modus weiterführende Gespräche führen.")
            else:
                QMessageBox.warning(self, "Fehler", "Session konnte nicht geladen werden.")
                
        except Exception as e:
            QMessageBox.critical(self, "Fehler", f"Session-Laden fehlgeschlagen:\n{e}")
    
    def delete_selected_session(self):
        """Löscht die ausgewählte Session"""
        if not self.current_session_id:
            return
        
        reply = QMessageBox.question(self, "Session löschen", 
            f"Möchtest du diese Session wirklich löschen?\n\n"
            f"Session-ID: {self.current_session_id}\n\n"
            f"⚠️ Diese Aktion kann nicht rückgängig gemacht werden!",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        
        if reply == QMessageBox.StandardButton.Yes:
            try:
                self.session_loader.db.delete_session(self.current_session_id)
                QMessageBox.information(self, "Gelöscht", "Session wurde erfolgreich gelöscht.")
                self.load_sessions()  # Liste aktualisieren
                
            except Exception as e:
                QMessageBox.critical(self, "Fehler", f"Session-Löschung fehlgeschlagen:\n{e}")
    
    def create_new_session(self):
        """Erstellt eine neue Session"""
        if not self.session_loader:
            return
        
        try:
            new_session_id = self.session_loader.session_manager.create_or_restore_session("default_user", False)
            
            QMessageBox.information(self, "Neue Session", 
                f"Neue Session erstellt!\n\n"
                f"Session-ID: {new_session_id}\n\n"
                f"Du kannst jetzt eine neue psychologische Sitzung beginnen.")
            
            self.load_sessions()  # Liste aktualisieren
            
        except Exception as e:
            QMessageBox.critical(self, "Fehler", f"Session-Erstellung fehlgeschlagen:\n{e}")
    
    def auto_refresh(self):
        """Automatische Aktualisierung"""
        self.load_sessions()
    
    def show_help(self):
        """Zeigt Hilfe-Dialog"""
        help_text = """
🧠📋 Session-Management Hilfe

🔍 Session-Liste:
• Grüne Sessions sind aktiv
• Graue Sessions sind geschlossen
• Klick auf Session zeigt Details

📥 Session laden:
1. Session in Liste auswählen
2. "Session laden" klicken
3. Im Bot weiterführende Gespräche

🎯 Empfehlungen:
• Zeigt relevante Sessions zum Fortsetzen
• Basiert auf Aktivität und Themen

➕ Neue Session:
• Erstellt komplett neue Session
• Für neue Themen/Probleme

🗑️ Session löschen:
• Löscht Session permanent
• Datenschutz-konform (DSGVO)

🔄 Auto-Update:
• Liste aktualisiert sich alle 30 Sekunden
• Manuell mit "Aktualisieren"
"""
        
        QMessageBox.information(self, "Hilfe", help_text)

def main():
    """Hauptfunktion"""
    app = QApplication(sys.argv)
    
    # Dark Theme (optional)
    app.setStyle('Fusion')
    
    # Hauptfenster
    window = SessionManagerGUI()
    window.show()
    
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
