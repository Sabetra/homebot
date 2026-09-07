"""
Base widget classes and utilities for the GUI application.
Provides reusable components with common functionality.
"""

from PySide6.QtWidgets import QTextBrowser, QWidget, QVBoxLayout, QLabel
from PySide6.QtCore import Signal, Qt, QUrl
from PySide6.QtGui import QFont
from typing import Optional
import logging

from .config import SUPPORTED_IMAGE_EXTENSIONS, UIConstants

logger = logging.getLogger(__name__)


class DragDropTextEdit(QTextBrowser):
    """Enhanced QTextBrowser with drag-drop support for images and secure link handling."""
    
    image_dropped = Signal(str)  # Signal with image path
    
    def __init__(self, parent=None):
        super().__init__(parent)
        logger.debug("Initializing DragDropTextEdit")
        self.setAcceptDrops(True)
        self.viewport().setAcceptDrops(True)
        
        # IMPORTANT: No automatic external links! All through handler
        self.setOpenExternalLinks(False)  # Prevents direct loading in widget
        self.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)

    def loadResource(self, type, name):
        """
        Override loadResource to prevent loading external URLs.
        This is important protection against chat history overwriting!
        """
        url_str = name.toString() if hasattr(name, 'toString') else str(name)
        
        # Only allow local resources and internal anchors
        if url_str.startswith(('http://', 'https://')):
            logger.warning(f"BLOCKED: loadResource for external URL: {url_str}")
            return None  # Block external URLs completely
            
        # For all other resources: default behavior
        return super().loadResource(type, name)

    def setSource(self, url, type=None):
        """
        Override setSource to prevent direct loading of URLs.
        This prevents QTextBrowser from loading external pages.
        """
        url_str = url.toString() if hasattr(url, 'toString') else str(url)
        
        logger.debug(f"setSource called: {url_str}")
        
        # Block external URLs completely
        if url_str.startswith(('http://', 'https://')):
            logger.warning(f"BLOCKED: setSource for external URL: {url_str}")
            return  # Do nothing - block the loading
            
        # For internal anchors: default behavior
        if url_str.startswith('#'):
            if type is not None:
                super().setSource(url, type)
            else:
                super().setSource(url)
            return
            
        # For everything else: block
        logger.warning(f"BLOCKED: setSource for unknown URL: {url_str}")

    def dragEnterEvent(self, event):
        """Handle drag enter events for image files."""
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                if (url.isLocalFile() and 
                    url.toLocalFile().lower().endswith(SUPPORTED_IMAGE_EXTENSIONS)):
                    event.acceptProposedAction()
                    return
        event.ignore()

    def dragMoveEvent(self, event):
        """Handle drag move events."""
        self.dragEnterEvent(event)

    def dropEvent(self, event):
        """Handle drop events for image files."""
        for url in event.mimeData().urls():
            if (url.isLocalFile() and 
                url.toLocalFile().lower().endswith(SUPPORTED_IMAGE_EXTENSIONS)):
                image_path = url.toLocalFile()
                self.image_dropped.emit(image_path)
                break
        event.acceptProposedAction()


class StyledLabel(QLabel):
    """Label with predefined styles for common use cases."""
    
    def __init__(self, text: str = "", style_type: str = "default", parent=None):
        super().__init__(text, parent)
        self.apply_style(style_type)
    
    def apply_style(self, style_type: str):
        """Apply predefined styles based on type."""
        styles = {
            "error": UIConstants.ERROR_COLOR,
            "success": UIConstants.SUCCESS_COLOR,
            "warning": UIConstants.WARNING_COLOR,
            "italic": "font-style: italic;",
            "bold": "font-weight: bold;",
            "default": ""
        }
        
        if style_type in styles:
            self.setStyleSheet(styles[style_type])
        else:
            logger.warning(f"Unknown style type: {style_type}")


class BaseTab(QWidget):
    """Base class for tab widgets with common functionality."""
    
    def __init__(self, tab_name: str, parent=None):
        super().__init__(parent)
        self.tab_name = tab_name
        self.main_layout = QVBoxLayout(self)
        self.setup_ui()
    
    def setup_ui(self):
        """Override this method to setup the UI for specific tabs."""
        pass
    
    def add_section(self, title: str) -> QVBoxLayout:
        """Add a new section with a title to the tab."""
        section_layout = QVBoxLayout()
        if title:
            title_label = StyledLabel(title, "bold")
            section_layout.addWidget(title_label)
        self.main_layout.addLayout(section_layout)
        return section_layout
    
    def add_separator(self):
        """Add a visual separator to the tab."""
        separator = QLabel()
        separator.setFixedHeight(1)
        separator.setStyleSheet("background-color: #ddd; margin: 10px 0;")
        self.main_layout.addWidget(separator)


class MonospaceTextWidget(QTextBrowser):
    """Text widget optimized for displaying code and logs."""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFont(QFont(UIConstants.CODE_FONT_FAMILY, 9))
        self.setStyleSheet("""
            QTextBrowser {
                background-color: #f8f9fa;
                border: 1px solid #dee2e6;
                border-radius: 4px;
                padding: 8px;
            }
        """)


class StatusWidget(QWidget):
    """Widget for displaying status information with color coding."""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.status_layout = QVBoxLayout(self)
        self.status_label = StyledLabel("Ready", "success")
        self.status_layout.addWidget(self.status_label)
    
    def set_status(self, message: str, status_type: str = "default"):
        """Update the status message with optional styling."""
        self.status_label.setText(message)
        self.status_label.apply_style(status_type)
    
    def set_error(self, message: str):
        """Set an error status."""
        self.set_status(f"❌ {message}", "error")
    
    def set_success(self, message: str):
        """Set a success status."""
        self.set_status(f"✅ {message}", "success")
    
    def set_warning(self, message: str):
        """Set a warning status."""
        self.set_status(f"⚠️ {message}", "warning")
    
    def set_processing(self, message: str):
        """Set a processing status."""
        self.set_status(f"🔄 {message}", "default")
