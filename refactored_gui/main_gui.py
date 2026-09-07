"""
Main GUI controller that orchestrates all components.
This is the refactored main window that connects all tabs and manages the application state.
"""

import sys
import os
import logging
from typing import Optional, Dict, Any
from datetime import datetime

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QTabWidget, QFileDialog, QMessageBox,
    QProgressDialog
)
from PySide6.QtCore import QCoreApplication, QTimer, Qt
from PySide6.QtGui import QKeySequence, QShortcut

from .config import (
    APP_NAME, APP_VERSION, APP_ORGANIZATION, DEFAULT_WINDOW_GEOMETRY,
    ModelDefaults, UIConstants
)
from .widgets import StatusWidget
from .workers import ChatWorker, ModelLoaderWorker
from .tabs.chat_tab import ChatTab
from .tabs.setup_tab import SetupTab
from .tabs.rag_tab import RAGTab
from .tabs.performance_tab import PerformanceTab

# Import dependencies
try:
    from scripts.model_loader import ModelLoader
    MODEL_LOADER_AVAILABLE = True
except ImportError as e:
    logging.warning(f"Model loader not available: {e}")
    MODEL_LOADER_AVAILABLE = False
    ModelLoader = None

try:
    from agent_chatbot_logic import AgentChatbotLogic
    CHAT_LOGIC_AVAILABLE = True
except ImportError as e:
    logging.warning(f"Chat logic not available: {e}")
    CHAT_LOGIC_AVAILABLE = False
    AgentChatbotLogic = None

# Configure logging
try:
    from agent.logging_setup import configure_logging, set_log_level
    configure_logging()
    set_log_level("DEBUG")
    LOGGING_CONFIGURED = True
except ImportError:
    logging.basicConfig(level=logging.INFO)
    LOGGING_CONFIGURED = False

logger = logging.getLogger(__name__)


class RefactoredChatbotGUI(QMainWindow):
    """
    Main application window for the refactored chatbot GUI.
    
    This class orchestrates all components and manages the application state.
    It follows a more modular architecture compared to the original monolithic GUI.
    """
    
    def __init__(self):
        super().__init__()
        
        # Initialize core components
        self.model_loader = ModelLoader() if MODEL_LOADER_AVAILABLE and ModelLoader else None
        self.chat_logic = None  # Will be set when model is loaded
        self.current_worker: Optional[ChatWorker] = None
        self.model_worker: Optional[ModelLoaderWorker] = None
        
        # Progress dialog for long operations
        self.progress_dialog: Optional[QProgressDialog] = None
        
        # Application settings
        self.settings = ModelDefaults().__dict__.copy()
        
        # Setup UI
        self._setup_window()
        self._setup_ui()
        self._setup_shortcuts()
        self._connect_signals()
        
        logger.info("Refactored ChatbotGUI initialized successfully")
    
    def _setup_window(self):
        """Setup main window properties."""
        self.setWindowTitle(APP_NAME)
        self.setGeometry(*DEFAULT_WINDOW_GEOMETRY)
        
        # Status bar
        self.status_widget = StatusWidget()
        self.statusBar().addPermanentWidget(self.status_widget)
        self.status_widget.set_success("Bereit")
    
    def _setup_ui(self):
        """Setup the main user interface."""
        # Main tab widget
        self.tabs = QTabWidget()
        self.setCentralWidget(self.tabs)
        
        # Create tabs
        self.chat_tab = ChatTab()
        self.setup_tab = SetupTab()
        self.rag_tab = RAGTab()
        self.performance_tab = PerformanceTab()
        
        # Add tabs to main widget
        self.tabs.addTab(self.chat_tab, UIConstants.TAB_CHAT)
        self.tabs.addTab(self.setup_tab, UIConstants.TAB_SETUP)
        self.tabs.addTab(self.rag_tab, UIConstants.TAB_RAG)
        self.tabs.addTab(self.performance_tab, UIConstants.TAB_PERFORMANCE)
        
        logger.debug("UI setup completed")
    
    def _setup_shortcuts(self):
        """Setup keyboard shortcuts."""
        # Escape key for cancellation
        escape_shortcut = QShortcut(QKeySequence("Escape"), self)
        escape_shortcut.activated.connect(self._handle_escape)
        
        # Ctrl+R for reset
        reset_shortcut = QShortcut(QKeySequence("Ctrl+R"), self)
        reset_shortcut.activated.connect(self._handle_reset_context)
    
    def _connect_signals(self):
        """Connect signals between components."""
        # Chat tab signals
        self.chat_tab.send_message.connect(self._handle_send_message)
        self.chat_tab.reset_context.connect(self._handle_reset_context)
        self.chat_tab.image_selected.connect(self._handle_image_selection)
        self.chat_tab.image_removed.connect(self._handle_image_removal)
        self.chat_tab.cancel_request.connect(self._handle_cancel_request)
        
        # Setup tab signals
        self.setup_tab.model_settings_changed.connect(self._handle_settings_changed)
        self.setup_tab.load_model_requested.connect(self._handle_load_model)
        
        # RAG tab signals
        self.rag_tab.rag_settings_changed.connect(self._handle_rag_settings_changed)
        self.rag_tab.pdf_ingestion_requested.connect(self._handle_pdf_ingestion)
        self.rag_tab.xlsx_ingestion_requested.connect(self._handle_xlsx_ingestion)
        self.rag_tab.url_ingestion_requested.connect(self._handle_url_ingestion)
        self.rag_tab.reset_rag_database.connect(self._handle_rag_reset)
        
        # Performance tab signals
        self.performance_tab.refresh_requested.connect(self._handle_performance_refresh)
    
    def _handle_send_message(self, prompt: str, image_path: str):
        """Handle message sending from chat tab."""
        if not self.chat_logic:
            self.chat_tab.display_error("Kein Modell geladen. Bitte laden Sie zuerst ein Modell.")
            return
        
        if self.current_worker and self.current_worker.isRunning():
            logger.warning("Chat worker already running")
            return
        
        # Update UI state
        self.chat_tab.set_processing_state(True)
        self.status_widget.set_processing("Verarbeite Anfrage...")
        
        # Start chat worker
        self.current_worker = ChatWorker(
            self.chat_logic, 
            prompt, 
            image_path if image_path else None
        )
        
        # Connect worker signals
        self.current_worker.response_ready.connect(self._handle_response_ready)
        self.current_worker.error_occurred.connect(self._handle_response_error)
        self.current_worker.progress_update.connect(self._handle_progress_update)
        self.current_worker.finished.connect(self._handle_worker_finished)
        
        self.current_worker.start()
        logger.debug(f"Started chat worker for prompt: {prompt[:50]}...")
    
    def _handle_response_ready(self, response: str):
        """Handle successful response from chat worker."""
        self.chat_tab.display_message("Assistant", response)
        self.status_widget.set_success("Antwort empfangen")
        logger.debug("Response received and displayed")
    
    def _handle_response_error(self, error: str):
        """Handle error from chat worker."""
        self.chat_tab.display_error(error)
        self.status_widget.set_error("Fehler bei der Verarbeitung")
        logger.error(f"Chat worker error: {error}")
    
    def _handle_progress_update(self, message: str):
        """Handle progress updates from workers."""
        self.status_widget.set_processing(message)
    
    def _handle_worker_finished(self):
        """Handle worker thread completion."""
        self.chat_tab.set_processing_state(False)
        self.status_widget.set_success("Bereit")
        
        if self.current_worker:
            self.current_worker.deleteLater()
            self.current_worker = None
    
    def _handle_reset_context(self):
        """Handle context reset request."""
        if self.chat_logic:
            # Reset chat logic context
            try:
                # Try various reset methods
                reset_methods = ['reset_context', 'reset', 'clear_context']
                reset_successful = False
                
                for method_name in reset_methods:
                    if hasattr(self.chat_logic, method_name):
                        method = getattr(self.chat_logic, method_name)
                        if callable(method):
                            method()
                            reset_successful = True
                            logger.debug(f"Used {method_name} for context reset")
                            break
                
                # Fallback: try to clear conversation history
                if not reset_successful:
                    conv_attrs = ['conversation_history', 'history', 'messages', 'chat_history']
                    for attr_name in conv_attrs:
                        if hasattr(self.chat_logic, attr_name):
                            attr = getattr(self.chat_logic, attr_name)
                            if hasattr(attr, 'clear') and callable(attr.clear):
                                attr.clear()
                                reset_successful = True
                                logger.debug(f"Cleared {attr_name}")
                                break
                
                if reset_successful:
                    self.chat_tab.clear_chat()
                    self.status_widget.set_success("Kontext zurückgesetzt")
                    logger.info("Context reset completed")
                else:
                    logger.warning("No suitable reset method found")
                    self.chat_tab.clear_chat()
                    self.status_widget.set_warning("Chat geleert (Kontext-Reset unsicher)")
                
            except Exception as e:
                logger.error(f"Error resetting context: {e}")
                self.chat_tab.display_error(f"Fehler beim Zurücksetzen: {e}")
        else:
            self.chat_tab.display_error("Kein Modell geladen")
    
    def _handle_image_selection(self, current_path: str):
        """Handle image selection from chat tab."""
        if current_path:  # If path is provided, use it
            self.chat_tab.set_image(current_path)
        else:  # Otherwise open file dialog
            file_path, _ = QFileDialog.getOpenFileName(
                self,
                "Bild auswählen",
                "",
                "Bilder (*.png *.jpg *.jpeg);;Alle Dateien (*)"
            )
            
            if file_path:
                self.chat_tab.set_image(file_path)
                logger.debug(f"Image selected: {file_path}")
    
    def _handle_image_removal(self):
        """Handle image removal from chat tab."""
        self.chat_tab.set_image(None)
        logger.debug("Image removed")
    
    def _handle_cancel_request(self):
        """Handle cancellation request."""
        if self.current_worker and self.current_worker.isRunning():
            self.current_worker.cancel()
            self.current_worker.terminate()
            self.current_worker.wait(3000)  # Wait up to 3 seconds
            
            self.chat_tab.set_processing_state(False)
            self.status_widget.set_warning("Vorgang abgebrochen")
            logger.info("Chat worker cancelled")
    
    def _handle_escape(self):
        """Handle escape key press."""
        if self.current_worker and self.current_worker.isRunning():
            self._handle_cancel_request()
    
    def _handle_settings_changed(self, settings: Dict[str, Any]):
        """Handle settings changes from setup tab."""
        self.settings.update(settings)
        
        # Apply settings to chat logic if available
        if self.chat_logic:
            try:
                # Apply relevant settings to chat logic
                for key, value in settings.items():
                    if hasattr(self.chat_logic, key):
                        setattr(self.chat_logic, key, value)
                logger.debug("Settings applied to chat logic")
            except Exception as e:
                logger.error(f"Error applying settings: {e}")
    
    def _handle_load_model(self):
        """Handle model loading request."""
        if not MODEL_LOADER_AVAILABLE:
            self._show_error("Model Loader nicht verfügbar", 
                           "Der Model Loader konnte nicht importiert werden.")
            return
        
        # Open file dialog for model selection
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Modell auswählen",
            "",
            "GGUF Modelle (*.gguf);;Alle Dateien (*)"
        )
        
        if not file_path:
            return
        
        # Start model loading worker
        self.setup_tab.set_loading_state(True)
        self.chat_tab.set_model_status("Lade Modell...", False)
        
        self.model_worker = ModelLoaderWorker(self.model_loader, file_path)
        self.model_worker.model_loaded.connect(self._handle_model_loaded)
        self.model_worker.error_occurred.connect(self._handle_model_error)
        self.model_worker.progress_update.connect(self._handle_progress_update)
        self.model_worker.finished.connect(self._handle_model_worker_finished)
        
        self.model_worker.start()
        logger.info(f"Started model loading: {file_path}")
    
    def _handle_model_loaded(self, model):
        """Handle successful model loading."""
        try:
            # Create chat logic with loaded model
            if not CHAT_LOGIC_AVAILABLE or not AgentChatbotLogic or not self.model_loader:
                self._handle_model_error("Chat Logic oder Model Loader nicht verfügbar")
                return
                
            self.chat_logic = AgentChatbotLogic(self.model_loader)
            
            # Apply current settings
            for key, value in self.settings.items():
                if hasattr(self.chat_logic, key):
                    setattr(self.chat_logic, key, value)
            
            # Update UI
            model_name = getattr(model, 'model_path', 'Unbekanntes Modell')
            self.setup_tab.set_model_status(f"Modell geladen: {model_name}", True)
            self.chat_tab.set_model_status(f"Modell geladen: {model_name}", True)
            
            self.status_widget.set_success("Modell erfolgreich geladen")
            logger.info(f"Model loaded successfully: {model_name}")
            
        except Exception as e:
            logger.error(f"Error creating chat logic: {e}")
            self._handle_model_error(f"Fehler beim Initialisieren: {e}")
    
    def _handle_model_error(self, error: str):
        """Handle model loading error."""
        self.setup_tab.set_model_status(f"Fehler: {error}", False)
        self.chat_tab.set_model_status(f"Fehler: {error}", False)
        self.status_widget.set_error("Modell-Ladefehler")
        logger.error(f"Model loading error: {error}")
    
    def _handle_model_worker_finished(self):
        """Handle model worker completion."""
        self.setup_tab.set_loading_state(False)
        
        if self.model_worker:
            self.model_worker.deleteLater()
            self.model_worker = None
    
    def _handle_rag_settings_changed(self, settings: Dict[str, Any]):
        """Handle RAG settings changes."""
        logger.info(f"RAG settings changed: {settings}")
        # Apply RAG settings to chat logic if available
        if self.chat_logic and hasattr(self.chat_logic, 'rag_manager'):
            try:
                rag_manager = getattr(self.chat_logic, 'rag_manager')
                for key, value in settings.items():
                    if hasattr(rag_manager, key):
                        setattr(rag_manager, key, value)
                logger.debug("RAG settings applied successfully")
            except Exception as e:
                logger.error(f"Error applying RAG settings: {e}")
    
    def _handle_pdf_ingestion(self, file_paths: list):
        """Handle PDF file ingestion for RAG."""
        logger.info(f"PDF ingestion requested for {len(file_paths)} files")
        self.rag_tab.set_processing_state(True, "Processing PDFs")
        
        # Here you would integrate with your PDF processing logic
        # For now, simulate processing
        from PySide6.QtCore import QTimer
        QTimer.singleShot(2000, lambda: self._complete_ingestion("PDF"))
    
    def _handle_xlsx_ingestion(self, file_paths: list):
        """Handle Excel file ingestion for RAG."""
        logger.info(f"XLSX ingestion requested for {len(file_paths)} files")
        self.rag_tab.set_processing_state(True, "Processing Excel files")
        
        # Here you would integrate with your Excel processing logic
        # For now, simulate processing
        from PySide6.QtCore import QTimer
        QTimer.singleShot(1500, lambda: self._complete_ingestion("Excel"))
    
    def _handle_url_ingestion(self, urls: list):
        """Handle URL ingestion for RAG."""
        logger.info(f"URL ingestion requested for {len(urls)} URLs")
        self.rag_tab.set_processing_state(True, "Processing URLs")
        
        # Here you would integrate with your URL processing logic
        # For now, simulate processing
        from PySide6.QtCore import QTimer
        QTimer.singleShot(3000, lambda: self._complete_ingestion("URL"))
    
    def _handle_rag_reset(self):
        """Handle RAG database reset."""
        logger.info("RAG database reset requested")
        try:
            # Here you would integrate with your RAG reset logic
            self.rag_tab.update_database_stats({})
            self.status_widget.set_success("RAG database reset completed")
        except Exception as e:
            logger.error(f"Error resetting RAG database: {e}")
            self.status_widget.set_error("RAG reset failed")
    
    def _handle_performance_refresh(self):
        """Handle performance metrics refresh."""
        logger.debug("Performance refresh requested")
        # Performance tab handles its own refresh, just log it
    
    def _complete_ingestion(self, ingestion_type: str):
        """Complete ingestion process (simulation)."""
        self.rag_tab.set_processing_state(False)
        self.status_widget.set_success(f"{ingestion_type} ingestion completed")
        
        # Update mock database stats
        mock_stats = {
            "Documents": "42",
            "Chunks": "1,337",
            "Last Updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "Storage Size": "15.7 MB",
            "Index Status": "Ready"
        }
        self.rag_tab.update_database_stats(mock_stats)
    
    def _show_error(self, title: str, message: str):
        """Show error message dialog."""
        QMessageBox.critical(self, title, message)
    
    def _show_info(self, title: str, message: str):
        """Show information message dialog."""
        QMessageBox.information(self, title, message)
    
    def closeEvent(self, event):
        """Handle application closure."""
        try:
            # Cancel any running workers
            if self.current_worker and self.current_worker.isRunning():
                self.current_worker.cancel()
                self.current_worker.terminate()
                self.current_worker.wait(3000)
            
            if self.model_worker and self.model_worker.isRunning():
                self.model_worker.cancel()
                self.model_worker.terminate()
                self.model_worker.wait(3000)
            
            # Clean up chat logic and resources
            if self.chat_logic:
                try:
                    # Try various cleanup methods that might exist
                    cleanup_methods = ['cleanup', 'close', 'shutdown', 'destroy']
                    for method_name in cleanup_methods:
                        if hasattr(self.chat_logic, method_name):
                            method = getattr(self.chat_logic, method_name)
                            if callable(method):
                                method()
                                logger.debug(f"Called {method_name} on chat_logic")
                                break
                except Exception as e:
                    logger.warning(f"Error during chat logic cleanup: {e}")
            
            logger.info("Application closing gracefully")
            
        except Exception as e:
            logger.error(f"Error during application closure: {e}")
        finally:
            event.accept()


def main():
    """Main application entry point."""
    app = QApplication(sys.argv)
    
    # Set application properties
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(APP_VERSION)
    app.setOrganizationName(APP_ORGANIZATION)
    
    try:
        window = RefactoredChatbotGUI()
        window.show()
        
        logger.info("Application started successfully")
        sys.exit(app.exec())
        
    except Exception as e:
        logger.critical(f"GUI startup error: {e}")
        
        try:
            error_msg = QMessageBox()
            error_msg.setIcon(QMessageBox.Icon.Critical)
            error_msg.setWindowTitle("GUI Startup Fehler")
            error_msg.setText(f"Die GUI konnte nicht gestartet werden:\n\n{str(e)}")
            
            import traceback
            error_msg.setDetailedText(traceback.format_exc())
            error_msg.exec()
            
        except Exception:
            pass  # If even the error dialog fails, just exit
        
        sys.exit(1)


if __name__ == "__main__":
    main()
