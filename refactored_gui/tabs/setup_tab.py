"""
Setup tab component for model and application configuration.
Handles model parameters, system settings, and basic preferences.
"""

import logging
from typing import Dict, Any

from PySide6.QtWidgets import (
    QVBoxLayout, QHBoxLayout, QLabel, QSpinBox, QDoubleSpinBox, 
    QTextEdit, QComboBox, QPushButton, QCheckBox, QGroupBox, QScrollArea, QWidget
)
from PySide6.QtCore import Signal

from ..widgets import BaseTab, StyledLabel
from ..config import UIConstants, ModelDefaults

logger = logging.getLogger(__name__)


class SetupTab(BaseTab):
    """Configuration tab for model and system settings."""
    
    # Signals
    model_settings_changed = Signal(dict)  # settings dictionary
    load_model_requested = Signal()
    
    def __init__(self, parent=None):
        self.model_defaults = ModelDefaults()
        super().__init__(UIConstants.TAB_SETUP, parent)
    
    def setup_ui(self):
        """Setup the configuration interface."""
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
        
        # GPU Status
        self._setup_gpu_status(content_layout)
        
        # Model Configuration
        self._setup_model_config(content_layout)
        
        # System Prompt
        self._setup_system_prompt(content_layout)
        
        # Image Analysis Settings
        self._setup_image_analysis(content_layout)
        
        # Model Loading
        self._setup_model_loading(content_layout)
        
        return content_widget
    
    def _setup_gpu_status(self, layout: QVBoxLayout):
        """Setup GPU status display."""
        gpu_group = QGroupBox("GPU Status")
        gpu_layout = QVBoxLayout(gpu_group)
        
        self.gpu_status_label = StyledLabel(
            "🔥 GPU: Automatisch optimiert für maximale Leistung", 
            "success"
        )
        gpu_layout.addWidget(self.gpu_status_label)
        
        layout.addWidget(gpu_group)
    
    def _setup_model_config(self, layout: QVBoxLayout):
        """Setup model configuration controls."""
        config_group = QGroupBox("Model Configuration")
        config_layout = QVBoxLayout(config_group)
        
        # Temperature
        temp_layout = QHBoxLayout()
        temp_layout.addWidget(QLabel("Temperature:"))
        self.temp_spin = QDoubleSpinBox()
        self.temp_spin.setDecimals(2)
        self.temp_spin.setSingleStep(0.05)
        self.temp_spin.setRange(0.0, 2.0)
        self.temp_spin.setValue(self.model_defaults.temperature)
        self.temp_spin.valueChanged.connect(self._on_settings_changed)
        temp_layout.addWidget(self.temp_spin)
        config_layout.addLayout(temp_layout)
        
        # Max Tokens
        tokens_layout = QHBoxLayout()
        tokens_layout.addWidget(QLabel("Max Tokens (Basis-Antworten):"))
        self.token_spin = QSpinBox()
        self.token_spin.setRange(1, 32768)
        self.token_spin.setValue(self.model_defaults.max_tokens)
        self.token_spin.valueChanged.connect(self._on_settings_changed)
        tokens_layout.addWidget(self.token_spin)
        config_layout.addLayout(tokens_layout)
        
        # Context Size
        ctx_layout = QHBoxLayout()
        ctx_layout.addWidget(QLabel("Kontextgröße (n_ctx):"))
        self.ctx_spin = QSpinBox()
        self.ctx_spin.setRange(512, 131072)
        self.ctx_spin.setValue(self.model_defaults.n_ctx)
        self.ctx_spin.valueChanged.connect(self._on_settings_changed)
        ctx_layout.addWidget(self.ctx_spin)
        config_layout.addLayout(ctx_layout)
        
        layout.addWidget(config_group)
    
    def _setup_system_prompt(self, layout: QVBoxLayout):
        """Setup system prompt configuration."""
        prompt_group = QGroupBox("System Prompt")
        prompt_layout = QVBoxLayout(prompt_group)
        
        prompt_layout.addWidget(QLabel("Systemprompt (leer = Standard):"))
        self.system_prompt_edit = QTextEdit()
        self.system_prompt_edit.setMaximumHeight(100)
        self.system_prompt_edit.setPlaceholderText("Geben Sie hier einen benutzerdefinierten Systemprompt ein...")
        self.system_prompt_edit.textChanged.connect(self._on_settings_changed)
        prompt_layout.addWidget(self.system_prompt_edit)
        
        layout.addWidget(prompt_group)
    
    def _setup_image_analysis(self, layout: QVBoxLayout):
        """Setup image analysis configuration."""
        image_group = QGroupBox("Bildanalyse-Einstellungen")
        image_layout = QVBoxLayout(image_group)
        
        # Image Mode Selection
        mode_layout = QHBoxLayout()
        mode_layout.addWidget(QLabel("Modus:"))
        self.image_mode_combo = QComboBox()
        self.image_mode_combo.addItems([
            "Standard", 
            "Detaillierte Bildanalyse", 
            "Benutzerdefiniert"
        ])
        self.image_mode_combo.currentTextChanged.connect(self._on_image_mode_changed)
        mode_layout.addWidget(self.image_mode_combo)
        image_layout.addLayout(mode_layout)
        
        # Detail Level (for custom mode)
        detail_layout = QHBoxLayout()
        detail_layout.addWidget(QLabel("Detailgrad:"))
        self.detail_level_combo = QComboBox()
        self.detail_level_combo.addItems(["niedrig", "mittel", "hoch"])
        self.detail_level_combo.setCurrentText("hoch")
        self.detail_level_combo.setEnabled(False)  # Disabled by default
        detail_layout.addWidget(self.detail_level_combo)
        image_layout.addLayout(detail_layout)
        
        # Include explicit content checkbox
        self.include_explicit_checkbox = QCheckBox("Explizite Inhalte analysieren")
        self.include_explicit_checkbox.setEnabled(False)
        image_layout.addWidget(self.include_explicit_checkbox)
        
        layout.addWidget(image_group)
    
    def _setup_model_loading(self, layout: QVBoxLayout):
        """Setup model loading controls."""
        loading_group = QGroupBox("Model Loading")
        loading_layout = QVBoxLayout(loading_group)
        
        self.load_button = QPushButton("Modell laden")
        self.load_button.clicked.connect(self._on_load_model)
        loading_layout.addWidget(self.load_button)
        
        self.model_status_label = StyledLabel("Kein Modell geladen", "error")
        loading_layout.addWidget(self.model_status_label)
        
        layout.addWidget(loading_group)
    
    def _on_settings_changed(self):
        """Handle settings changes."""
        settings = self.get_current_settings()
        self.model_settings_changed.emit(settings)
    
    def _on_image_mode_changed(self, mode: str):
        """Handle image mode changes."""
        is_custom = mode == "Benutzerdefiniert"
        self.detail_level_combo.setEnabled(is_custom)
        self.include_explicit_checkbox.setEnabled(is_custom)
        self._on_settings_changed()
    
    def _on_load_model(self):
        """Handle model loading request."""
        self.load_model_requested.emit()
    
    def get_current_settings(self) -> Dict[str, Any]:
        """Get current configuration settings."""
        return {
            "temperature": self.temp_spin.value(),
            "max_tokens": self.token_spin.value(),
            "n_ctx": self.ctx_spin.value(),
            "system_prompt": self.system_prompt_edit.toPlainText(),
            "image_mode": self.image_mode_combo.currentText(),
            "detail_level": self.detail_level_combo.currentText(),
            "include_explicit": self.include_explicit_checkbox.isChecked(),
            "debug": False  # Could be added as a checkbox later
        }
    
    def set_settings(self, settings: Dict[str, Any]):
        """Apply settings to the UI controls."""
        if "temperature" in settings:
            self.temp_spin.setValue(settings["temperature"])
        if "max_tokens" in settings:
            self.token_spin.setValue(settings["max_tokens"])
        if "n_ctx" in settings:
            self.ctx_spin.setValue(settings["n_ctx"])
        if "system_prompt" in settings:
            self.system_prompt_edit.setPlainText(settings["system_prompt"])
        if "image_mode" in settings:
            self.image_mode_combo.setCurrentText(settings["image_mode"])
        if "detail_level" in settings:
            self.detail_level_combo.setCurrentText(settings["detail_level"])
        if "include_explicit" in settings:
            self.include_explicit_checkbox.setChecked(settings["include_explicit"])
    
    def set_model_status(self, status: str, is_loaded: bool):
        """Update model loading status."""
        if is_loaded:
            self.model_status_label.setText(f"✅ {status}")
            self.model_status_label.apply_style("success")
            self.load_button.setText("Modell neu laden")
        else:
            self.model_status_label.setText(f"❌ {status}")
            self.model_status_label.apply_style("error")
            self.load_button.setText("Modell laden")
    
    def set_loading_state(self, loading: bool):
        """Set loading state for model operations."""
        self.load_button.setEnabled(not loading)
        if loading:
            self.model_status_label.setText("🔄 Lade Modell...")
            self.model_status_label.apply_style("default")
