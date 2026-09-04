"""
RAG (Retrieval-Augmented Generation) configuration tab.
Handles PDF ingestion, vector database management, and RAG-specific settings.
"""

import logging
from typing import Dict, Any, List, Optional

from PySide6.QtWidgets import (
    QVBoxLayout, QHBoxLayout, QLabel, QSpinBox, QDoubleSpinBox, 
    QPushButton, QCheckBox, QGroupBox, QScrollArea, QFileDialog,
    QProgressBar, QTextEdit, QComboBox, QWidget
)
from PySide6.QtCore import Signal, QTimer

from ..widgets import BaseTab, StyledLabel, MonospaceTextWidget
from ..config import UIConstants, RAGDefaults, MultiQueryDefaults
from ..workers import FileProcessorWorker

logger = logging.getLogger(__name__)


class RAGTab(BaseTab):
    """RAG system configuration and management tab."""
    
    # Signals
    rag_settings_changed = Signal(dict)
    pdf_ingestion_requested = Signal(list)  # list of file paths
    xlsx_ingestion_requested = Signal(list)
    url_ingestion_requested = Signal(list)  # list of URLs
    reset_rag_database = Signal()
    
    def __init__(self, parent=None):
        self.rag_defaults = RAGDefaults()
        self.mq_defaults = MultiQueryDefaults()
        self.current_processor: Optional[FileProcessorWorker] = None
        super().__init__(UIConstants.TAB_RAG, parent)
    
    def setup_ui(self):
        """Setup the RAG configuration interface."""
        # Make the tab scrollable
        scroll_area = QScrollArea()
        scroll_widget = self._create_scroll_content()
        scroll_area.setWidget(scroll_widget)
        scroll_area.setWidgetResizable(True)
        self.main_layout.addWidget(scroll_area)
    
    def _create_scroll_content(self) -> QWidget:
        """Create the scrollable content widget."""
        content_widget = QWidget()
        content_layout = QVBoxLayout(content_widget)
        
        # RAG Status
        self._setup_rag_status(content_layout)
        
        # Basic RAG Settings
        self._setup_basic_rag_settings(content_layout)
        
        # Multi-Query RAG Settings
        self._setup_multiquery_settings(content_layout)
        
        # Document Ingestion
        self._setup_document_ingestion(content_layout)
        
        # Advanced Settings
        self._setup_advanced_settings(content_layout)
        
        # Database Management
        self._setup_database_management(content_layout)
        
        return content_widget
    
    def _setup_rag_status(self, layout: QVBoxLayout):
        """Setup RAG system status display."""
        status_group = QGroupBox("RAG System Status")
        status_layout = QVBoxLayout(status_group)
        
        self.rag_status_label = StyledLabel("RAG System: Nicht initialisiert", "warning")
        status_layout.addWidget(self.rag_status_label)
        
        self.rag_enabled_checkbox = QCheckBox("RAG System aktivieren")
        self.rag_enabled_checkbox.toggled.connect(self._on_rag_enabled_changed)
        status_layout.addWidget(self.rag_enabled_checkbox)
        
        layout.addWidget(status_group)
    
    def _setup_basic_rag_settings(self, layout: QVBoxLayout):
        """Setup basic RAG configuration."""
        basic_group = QGroupBox("Grundeinstellungen")
        basic_layout = QVBoxLayout(basic_group)
        
        # K value (number of documents to retrieve)
        k_layout = QHBoxLayout()
        k_layout.addWidget(QLabel("Anzahl Dokumente (k):"))
        self.rag_k_spin = QSpinBox()
        self.rag_k_spin.setRange(1, 20)
        self.rag_k_spin.setValue(self.rag_defaults.k)
        self.rag_k_spin.valueChanged.connect(self._on_settings_changed)
        k_layout.addWidget(self.rag_k_spin)
        basic_layout.addLayout(k_layout)
        
        # Minimum score threshold
        score_layout = QHBoxLayout()
        score_layout.addWidget(QLabel("Mindest-Relevanz-Score:"))
        self.rag_min_score_spin = QDoubleSpinBox()
        self.rag_min_score_spin.setRange(0.0, 1.0)
        self.rag_min_score_spin.setSingleStep(0.1)
        self.rag_min_score_spin.setDecimals(2)
        self.rag_min_score_spin.setValue(self.rag_defaults.min_score)
        self.rag_min_score_spin.valueChanged.connect(self._on_settings_changed)
        score_layout.addWidget(self.rag_min_score_spin)
        basic_layout.addLayout(score_layout)
        
        # Chunk size
        chunk_layout = QHBoxLayout()
        chunk_layout.addWidget(QLabel("Chunk-Größe:"))
        self.rag_chunk_size_spin = QSpinBox()
        self.rag_chunk_size_spin.setRange(100, 5000)
        self.rag_chunk_size_spin.setValue(self.rag_defaults.chunk_size)
        self.rag_chunk_size_spin.valueChanged.connect(self._on_settings_changed)
        chunk_layout.addWidget(self.rag_chunk_size_spin)
        basic_layout.addLayout(chunk_layout)
        
        # Chunk overlap
        overlap_layout = QHBoxLayout()
        overlap_layout.addWidget(QLabel("Chunk-Überlappung:"))
        self.rag_chunk_overlap_spin = QSpinBox()
        self.rag_chunk_overlap_spin.setRange(0, 1000)
        self.rag_chunk_overlap_spin.setValue(self.rag_defaults.chunk_overlap)
        self.rag_chunk_overlap_spin.valueChanged.connect(self._on_settings_changed)
        overlap_layout.addWidget(self.rag_chunk_overlap_spin)
        basic_layout.addLayout(overlap_layout)
        
        # Persistence
        self.rag_persist_checkbox = QCheckBox("Persistente Speicherung")
        self.rag_persist_checkbox.setChecked(self.rag_defaults.persist)
        self.rag_persist_checkbox.toggled.connect(self._on_settings_changed)
        basic_layout.addWidget(self.rag_persist_checkbox)
        
        layout.addWidget(basic_group)
    
    def _setup_multiquery_settings(self, layout: QVBoxLayout):
        """Setup Multi-Query RAG settings."""
        mq_group = QGroupBox("Multi-Query RAG")
        mq_layout = QVBoxLayout(mq_group)
        
        self.mq_enabled_checkbox = QCheckBox("Multi-Query RAG aktivieren")
        self.mq_enabled_checkbox.setChecked(self.mq_defaults.enabled)
        self.mq_enabled_checkbox.toggled.connect(self._on_mq_enabled_changed)
        mq_layout.addWidget(self.mq_enabled_checkbox)
        
        # Number of query variations
        mq_n_layout = QHBoxLayout()
        mq_n_layout.addWidget(QLabel("Anzahl Query-Variationen:"))
        self.mq_n_spin = QSpinBox()
        self.mq_n_spin.setRange(2, 10)
        self.mq_n_spin.setValue(self.mq_defaults.n)
        self.mq_n_spin.valueChanged.connect(self._on_settings_changed)
        self.mq_n_spin.setEnabled(self.mq_defaults.enabled)
        mq_n_layout.addWidget(self.mq_n_spin)
        mq_layout.addLayout(mq_n_layout)
        
        # K per query
        mq_k_layout = QHBoxLayout()
        mq_k_layout.addWidget(QLabel("Dokumente pro Query:"))
        self.mq_k_spin = QSpinBox()
        self.mq_k_spin.setRange(1, 10)
        self.mq_k_spin.setValue(self.mq_defaults.k)
        self.mq_k_spin.valueChanged.connect(self._on_settings_changed)
        self.mq_k_spin.setEnabled(self.mq_defaults.enabled)
        mq_k_layout.addWidget(self.mq_k_spin)
        mq_layout.addLayout(mq_k_layout)
        
        layout.addWidget(mq_group)
    
    def _setup_document_ingestion(self, layout: QVBoxLayout):
        """Setup document ingestion controls."""
        ingestion_group = QGroupBox("Dokument-Import")
        ingestion_layout = QVBoxLayout(ingestion_group)
        
        # PDF Ingestion
        pdf_layout = QHBoxLayout()
        self.pdf_button = QPushButton("PDFs importieren")
        self.pdf_button.clicked.connect(self._select_pdfs)
        pdf_layout.addWidget(self.pdf_button)
        
        self.extract_tables_checkbox = QCheckBox("Tabellen extrahieren")
        self.extract_tables_checkbox.setChecked(True)
        pdf_layout.addWidget(self.extract_tables_checkbox)
        
        self.build_kg_checkbox = QCheckBox("Knowledge Graph erstellen")
        self.build_kg_checkbox.setChecked(True)
        pdf_layout.addWidget(self.build_kg_checkbox)
        
        ingestion_layout.addLayout(pdf_layout)
        
        # XLSX Ingestion
        xlsx_layout = QHBoxLayout()
        self.xlsx_button = QPushButton("Excel-Dateien importieren")
        self.xlsx_button.clicked.connect(self._select_xlsx)
        xlsx_layout.addWidget(self.xlsx_button)
        ingestion_layout.addLayout(xlsx_layout)
        
        # URL Ingestion
        url_layout = QVBoxLayout()
        url_input_layout = QHBoxLayout()
        url_input_layout.addWidget(QLabel("URLs (eine pro Zeile):"))
        url_layout.addLayout(url_input_layout)
        
        self.url_text_edit = QTextEdit()
        self.url_text_edit.setMaximumHeight(100)
        self.url_text_edit.setPlaceholderText("https://example.com\\nhttps://another-site.com")
        url_layout.addWidget(self.url_text_edit)
        
        self.url_button = QPushButton("URLs importieren")
        self.url_button.clicked.connect(self._import_urls)
        url_layout.addWidget(self.url_button)
        
        ingestion_layout.addLayout(url_layout)
        
        # Progress bar
        self.ingestion_progress = QProgressBar()
        self.ingestion_progress.setVisible(False)
        ingestion_layout.addWidget(self.ingestion_progress)
        
        layout.addWidget(ingestion_group)
    
    def _setup_advanced_settings(self, layout: QVBoxLayout):
        """Setup advanced RAG settings."""
        advanced_group = QGroupBox("Erweiterte Einstellungen")
        advanced_layout = QVBoxLayout(advanced_group)
        
        # Max workers for parallel processing
        workers_layout = QHBoxLayout()
        workers_layout.addWidget(QLabel("Parallel-Verarbeitung (Workers):"))
        self.max_workers_spin = QSpinBox()
        self.max_workers_spin.setRange(1, 16)
        self.max_workers_spin.setValue(self.rag_defaults.max_workers)
        self.max_workers_spin.valueChanged.connect(self._on_settings_changed)
        workers_layout.addWidget(self.max_workers_spin)
        advanced_layout.addLayout(workers_layout)
        
        # Evidence settings
        evidence_candidates_layout = QHBoxLayout()
        evidence_candidates_layout.addWidget(QLabel("Evidence Max Candidates:"))
        self.evidence_max_candidates_spin = QSpinBox()
        self.evidence_max_candidates_spin.setRange(5, 50)
        self.evidence_max_candidates_spin.setValue(self.rag_defaults.evidence_max_candidates)
        self.evidence_max_candidates_spin.valueChanged.connect(self._on_settings_changed)
        evidence_candidates_layout.addWidget(self.evidence_max_candidates_spin)
        advanced_layout.addLayout(evidence_candidates_layout)
        
        layout.addWidget(advanced_group)
    
    def _setup_database_management(self, layout: QVBoxLayout):
        """Setup database management controls."""
        db_group = QGroupBox("Datenbank-Verwaltung")
        db_layout = QVBoxLayout(db_group)
        
        # Database info
        self.db_info_label = StyledLabel("Datenbank: Nicht geladen", "warning")
        db_layout.addWidget(self.db_info_label)
        
        # Database controls
        db_controls_layout = QHBoxLayout()
        
        self.reset_db_button = QPushButton("Datenbank zurücksetzen")
        self.reset_db_button.clicked.connect(self._reset_database)
        db_controls_layout.addWidget(self.reset_db_button)
        
        self.refresh_stats_button = QPushButton("Statistiken aktualisieren")
        self.refresh_stats_button.clicked.connect(self._refresh_stats)
        db_controls_layout.addWidget(self.refresh_stats_button)
        
        db_layout.addLayout(db_controls_layout)
        
        # Stats display
        self.stats_display = MonospaceTextWidget()
        self.stats_display.setMaximumHeight(150)
        db_layout.addWidget(self.stats_display)
        
        layout.addWidget(db_group)
    
    def _on_rag_enabled_changed(self, enabled: bool):
        """Handle RAG system enable/disable."""
        if enabled:
            self.rag_status_label.setText("RAG System: Aktiviert")
            self.rag_status_label.apply_style("success")
        else:
            self.rag_status_label.setText("RAG System: Deaktiviert")
            self.rag_status_label.apply_style("warning")
        
        self._on_settings_changed()
    
    def _on_mq_enabled_changed(self, enabled: bool):
        """Handle Multi-Query RAG enable/disable."""
        self.mq_n_spin.setEnabled(enabled)
        self.mq_k_spin.setEnabled(enabled)
        self._on_settings_changed()
    
    def _on_settings_changed(self):
        """Handle settings changes."""
        settings = self.get_current_settings()
        self.rag_settings_changed.emit(settings)
    
    def _select_pdfs(self):
        """Handle PDF file selection."""
        files, _ = QFileDialog.getOpenFileNames(
            self,
            "PDF-Dateien auswählen",
            "",
            "PDF-Dateien (*.pdf);;Alle Dateien (*)"
        )
        
        if files:
            self.pdf_ingestion_requested.emit(files)
            logger.info(f"PDF ingestion requested for {len(files)} files")
    
    def _select_xlsx(self):
        """Handle Excel file selection."""
        files, _ = QFileDialog.getOpenFileNames(
            self,
            "Excel-Dateien auswählen",
            "",
            "Excel-Dateien (*.xlsx *.xls);;Alle Dateien (*)"
        )
        
        if files:
            self.xlsx_ingestion_requested.emit(files)
            logger.info(f"XLSX ingestion requested for {len(files)} files")
    
    def _import_urls(self):
        """Handle URL import."""
        url_text = self.url_text_edit.toPlainText().strip()
        if not url_text:
            return
        
        urls = [url.strip() for url in url_text.split('\n') if url.strip()]
        if urls:
            self.url_ingestion_requested.emit(urls)
            self.url_text_edit.clear()
            logger.info(f"URL ingestion requested for {len(urls)} URLs")
    
    def _reset_database(self):
        """Handle database reset."""
        self.reset_rag_database.emit()
        logger.info("RAG database reset requested")
    
    def _refresh_stats(self):
        """Handle stats refresh."""
        # This would be connected to a signal from the main controller
        # For now, just update the display
        self.stats_display.setPlainText("Lade Statistiken...")
        
        # Use a timer to simulate async operation
        QTimer.singleShot(1000, self._update_mock_stats)
    
    def _update_mock_stats(self):
        """Update with mock statistics (to be replaced with real data)."""
        mock_stats = """
RAG Database Statistics:
========================
Documents: 42
Chunks: 1,337
Last Updated: 2025-09-30 14:30:00
Storage Size: 15.7 MB
Index Status: Ready
        """.strip()
        self.stats_display.setPlainText(mock_stats)
    
    def get_current_settings(self) -> Dict[str, Any]:
        """Get current RAG settings."""
        return {
            "rag_enabled": self.rag_enabled_checkbox.isChecked(),
            "rag_k": self.rag_k_spin.value(),
            "rag_min_score": self.rag_min_score_spin.value(),
            "rag_chunk_size": self.rag_chunk_size_spin.value(),
            "rag_chunk_overlap": self.rag_chunk_overlap_spin.value(),
            "rag_persist": self.rag_persist_checkbox.isChecked(),
            "mq_enabled": self.mq_enabled_checkbox.isChecked(),
            "mq_n": self.mq_n_spin.value(),
            "mq_k": self.mq_k_spin.value(),
            "extract_tables": self.extract_tables_checkbox.isChecked(),
            "build_kg": self.build_kg_checkbox.isChecked(),
            "max_workers": self.max_workers_spin.value(),
            "evidence_max_candidates": self.evidence_max_candidates_spin.value(),
        }
    
    def set_settings(self, settings: Dict[str, Any]):
        """Apply settings to the UI controls."""
        if "rag_enabled" in settings:
            self.rag_enabled_checkbox.setChecked(settings["rag_enabled"])
        if "rag_k" in settings:
            self.rag_k_spin.setValue(settings["rag_k"])
        if "rag_min_score" in settings:
            self.rag_min_score_spin.setValue(settings["rag_min_score"])
        if "rag_chunk_size" in settings:
            self.rag_chunk_size_spin.setValue(settings["rag_chunk_size"])
        if "rag_chunk_overlap" in settings:
            self.rag_chunk_overlap_spin.setValue(settings["rag_chunk_overlap"])
        if "rag_persist" in settings:
            self.rag_persist_checkbox.setChecked(settings["rag_persist"])
        if "mq_enabled" in settings:
            self.mq_enabled_checkbox.setChecked(settings["mq_enabled"])
        if "mq_n" in settings:
            self.mq_n_spin.setValue(settings["mq_n"])
        if "mq_k" in settings:
            self.mq_k_spin.setValue(settings["mq_k"])
        if "extract_tables" in settings:
            self.extract_tables_checkbox.setChecked(settings["extract_tables"])
        if "build_kg" in settings:
            self.build_kg_checkbox.setChecked(settings["build_kg"])
        if "max_workers" in settings:
            self.max_workers_spin.setValue(settings["max_workers"])
        if "evidence_max_candidates" in settings:
            self.evidence_max_candidates_spin.setValue(settings["evidence_max_candidates"])
    
    def set_processing_state(self, processing: bool, message: str = ""):
        """Set processing state for ingestion operations."""
        self.ingestion_progress.setVisible(processing)
        
        buttons = [self.pdf_button, self.xlsx_button, self.url_button, self.reset_db_button]
        for button in buttons:
            button.setEnabled(not processing)
        
        if processing and message:
            self.ingestion_progress.setFormat(f"{message} (%p%)")
    
    def update_database_stats(self, stats: Dict[str, Any]):
        """Update database statistics display."""
        if stats:
            stats_text = "RAG Database Statistics:\\n"
            stats_text += "=" * 24 + "\\n"
            for key, value in stats.items():
                stats_text += f"{key}: {value}\\n"
            
            self.stats_display.setPlainText(stats_text)
            self.db_info_label.setText("Datenbank: Geladen und bereit")
            self.db_info_label.apply_style("success")
        else:
            self.stats_display.setPlainText("Keine Statistiken verfügbar")
            self.db_info_label.setText("Datenbank: Nicht verfügbar")
            self.db_info_label.apply_style("error")
