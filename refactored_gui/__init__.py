"""
Refactored GUI Package

This package contains the reorganized GUI components for better maintainability,
performance, and extensibility. It replaces the monolithic gui.py with a modular
architecture.

Main Components:
- config: Configuration and constants
- widgets: Reusable UI components
- workers: Background processing threads
- tabs: Individual tab implementations
- main_gui: Main application controller

Usage:
    from refactored_gui import RefactoredChatbotGUI
    
    app = QApplication(sys.argv)
    window = RefactoredChatbotGUI()
    window.show()
    app.exec()
"""

from .main_gui import RefactoredChatbotGUI, main
from .config import (
    APP_NAME, APP_VERSION, ModelDefaults, RAGDefaults, 
    UIConstants, PerformanceSettings
)
from .widgets import (
    DragDropTextEdit, BaseTab, StyledLabel, StatusWidget, MonospaceTextWidget
)
from .workers import ChatWorker, ModelLoaderWorker, FileProcessorWorker

__version__ = "2.0.0"
__author__ = "Refactored GUI Team"

__all__ = [
    "RefactoredChatbotGUI",
    "main",
    "DragDropTextEdit",
    "BaseTab", 
    "StyledLabel",
    "StatusWidget",
    "ChatWorker",
    "ModelLoaderWorker",
    "FileProcessorWorker",
    "APP_NAME",
    "APP_VERSION",
    "ModelDefaults",
    "RAGDefaults",
    "UIConstants",
]
