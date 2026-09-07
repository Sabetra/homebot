"""
Chat tab component for the main chat interface.
Handles user input, message display, and chat interactions.
"""

import logging
from typing import Optional
from datetime import datetime

from PySide6.QtWidgets import (
    QVBoxLayout, QHBoxLayout, QLineEdit, QPushButton, QLabel
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont, QKeySequence, QShortcut

from ..widgets import BaseTab, DragDropTextEdit, StyledLabel
from ..config import UIConstants, TABLE_CSS

logger = logging.getLogger(__name__)


class ChatTab(BaseTab):
    """Main chat interface tab."""
    
    # Signals
    send_message = Signal(str, str)  # prompt, image_path
    reset_context = Signal()
    image_selected = Signal(str)
    image_removed = Signal()
    cancel_request = Signal()
    
    def __init__(self, parent=None):
        self.current_image_path: Optional[str] = None
        self.is_processing = False
        super().__init__(UIConstants.TAB_CHAT, parent)
        self._setup_shortcuts()
    
    def setup_ui(self):
        """Setup the chat interface UI."""
        # Model status
        self.model_status_label = StyledLabel("Kein Modell geladen.", "error")
        self.main_layout.addWidget(self.model_status_label)
        
        # Chat display
        self.chat_display = DragDropTextEdit()
        self.chat_display.setReadOnly(True)
        self.chat_display.setFont(QFont(UIConstants.DEFAULT_FONT_FAMILY, UIConstants.DEFAULT_FONT_SIZE))
        self.chat_display.image_dropped.connect(self._handle_image_drop)
        
        # Setup chat display styling
        self._setup_chat_display_styling()
        self.main_layout.addWidget(self.chat_display)
        
        # Image selection area
        self._setup_image_area()
        
        # Input area
        self._setup_input_area()
    
    def _setup_chat_display_styling(self):
        """Setup styling for the chat display area."""
        try:
            self.chat_display.document().setDefaultStyleSheet(TABLE_CSS)
            logger.debug("Chat display styling applied successfully")
        except AttributeError as e:
            logger.warning(f"CSS styling error (Document attribute): {e}")
        except Exception as e:
            logger.error(f"Critical CSS error: {type(e).__name__}: {e}")
    
    def _setup_image_area(self):
        """Setup the image selection and display area."""
        image_layout = QHBoxLayout()
        
        self.image_label = StyledLabel("Kein Bild ausgewählt.", "italic")
        
        self.image_button = QPushButton("Bild auswählen")
        self.image_button.clicked.connect(self._select_image)
        
        self.remove_image_button = QPushButton("Bild entfernen")
        self.remove_image_button.clicked.connect(self._remove_image)
        self.remove_image_button.setEnabled(False)
        
        image_layout.addWidget(self.image_label)
        image_layout.addWidget(self.image_button)
        image_layout.addWidget(self.remove_image_button)
        
        self.main_layout.addLayout(image_layout)
    
    def _setup_input_area(self):
        """Setup the input area with text field and buttons."""
        input_layout = QHBoxLayout()
        
        self.input_field = QLineEdit()
        self.input_field.setPlaceholderText("Frage eingeben...")
        self.input_field.returnPressed.connect(self._handle_send_cancel_button)
        self.input_field.textChanged.connect(self._update_send_button_state)
        
        self.send_button = QPushButton("Senden")
        self.send_button.setEnabled(False)
        self.send_button.clicked.connect(self._handle_send_cancel_button)
        
        self.reset_button = QPushButton("Kontext zurücksetzen")
        self.reset_button.clicked.connect(self._handle_reset_context)
        
        input_layout.addWidget(self.input_field)
        input_layout.addWidget(self.send_button)
        input_layout.addWidget(self.reset_button)
        
        self.main_layout.addLayout(input_layout)
    
    def _setup_shortcuts(self):
        """Setup keyboard shortcuts."""
        # Escape key for cancellation
        escape_shortcut = QShortcut(QKeySequence("Escape"), self)
        escape_shortcut.activated.connect(self._handle_escape)
    
    def _handle_image_drop(self, image_path: str):
        """Handle image drop from drag and drop."""
        self.set_image(image_path)
        self.image_selected.emit(image_path)
    
    def _select_image(self):
        """Handle image selection button click."""
        # This will be connected to the main window's file dialog
        # For now, just emit a signal
        self.image_selected.emit("")
    
    def _remove_image(self):
        """Handle image removal."""
        self.set_image(None)
        self.image_removed.emit()
    
    def _handle_send_cancel_button(self):
        """Handle send/cancel button click."""
        if self.is_processing:
            self.cancel_request.emit()
        else:
            self._send_message()
    
    def _send_message(self):
        """Send the current message."""
        prompt = self.input_field.text().strip()
        if not prompt:
            return
        
        # Validate input
        is_valid, error_msg = self._validate_input(prompt)
        if not is_valid:
            self.display_error(error_msg)
            return
        
        # Clear input field
        self.input_field.clear()
        
        # Display user message
        self.display_message("Du", prompt)
        
        # Emit signal to send message
        self.send_message.emit(prompt, self.current_image_path or "")
    
    def _validate_input(self, text: str) -> tuple[bool, str]:
        """Validate user input."""
        # Basic validation
        if len(text) > 10000:
            return False, "Eingabe ist zu lang (max. 10.000 Zeichen)"
        
        # Check for potentially malicious patterns
        suspicious_patterns = ['<script', 'javascript:', 'data:']
        text_lower = text.lower()
        for pattern in suspicious_patterns:
            if pattern in text_lower:
                return False, f"Verdächtiges Muster erkannt: {pattern}"
        
        return True, ""
    
    def _handle_reset_context(self):
        """Handle context reset."""
        self.reset_context.emit()
    
    def _handle_escape(self):
        """Handle escape key press."""
        if self.is_processing:
            self.cancel_request.emit()
    
    def _update_send_button_state(self):
        """Update send button state based on input."""
        has_text = bool(self.input_field.text().strip())
        self.send_button.setEnabled(has_text and not self.is_processing)
    
    def set_processing_state(self, processing: bool):
        """Set the processing state and update UI accordingly."""
        self.is_processing = processing
        
        if processing:
            self.send_button.setText("Abbrechen")
            self.send_button.setEnabled(True)
            self.input_field.setEnabled(False)
            self.reset_button.setEnabled(False)
        else:
            self.send_button.setText("Senden")
            self.input_field.setEnabled(True)
            self.reset_button.setEnabled(True)
            self._update_send_button_state()
    
    def set_model_status(self, status: str, is_loaded: bool):
        """Update the model status display."""
        if is_loaded:
            self.model_status_label.setText(f"✅ {status}")
            self.model_status_label.apply_style("success")
        else:
            self.model_status_label.setText(f"❌ {status}")
            self.model_status_label.apply_style("error")
    
    def set_image(self, image_path: Optional[str]):
        """Set the current image and update UI."""
        self.current_image_path = image_path
        
        if image_path:
            filename = image_path.split('/')[-1] if '/' in image_path else image_path.split('\\\\')[-1]
            self.image_label.setText(f"📸 {filename}")
            self.image_label.apply_style("success")
            self.remove_image_button.setEnabled(True)
        else:
            self.image_label.setText("Kein Bild ausgewählt.")
            self.image_label.apply_style("italic")
            self.remove_image_button.setEnabled(False)
    
    def display_message(self, sender: str, message: str):
        """Display a message in the chat area."""
        timestamp = datetime.now().strftime("%H:%M:%S")
        
        # Basic HTML formatting
        formatted_message = self._format_message_html(message)
        
        chat_entry = f"""
        <div style="margin: 10px 0; padding: 8px; border-radius: 6px; 
                    background: {'#e3f2fd' if sender == 'Du' else '#f8f9fa'};
                    border-left: 3px solid {'#2196F3' if sender == 'Du' else '#6c757d'};">
            <strong style="color: {'#1976D2' if sender == 'Du' else '#495057'};">{sender}</strong>
            <span style="color: #6c757d; font-size: 11px; margin-left: 10px;">{timestamp}</span>
            <div style="margin-top: 5px;">{formatted_message}</div>
        </div>
        """
        
        self.chat_display.append(chat_entry)
        
        # Scroll to bottom
        scrollbar = self.chat_display.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())
    
    def _format_message_html(self, message: str) -> str:
        """Basic HTML formatting for messages."""
        # Escape HTML characters but preserve newlines
        import html
        message = html.escape(message)
        
        # Convert newlines to <br>
        message = message.replace('\n', '<br>')
        
        # Basic markdown-like formatting
        message = message.replace('**', '<strong>', 1).replace('**', '</strong>', 1)
        message = message.replace('*', '<em>', 1).replace('*', '</em>', 1)
        
        return message
    
    def display_error(self, error_message: str):
        """Display an error message."""
        self.display_message("System", f"❌ {error_message}")
    
    def clear_chat(self):
        """Clear the chat display."""
        self.chat_display.clear()
        self.display_message("System", "Chat-Verlauf wurde zurückgesetzt.")
