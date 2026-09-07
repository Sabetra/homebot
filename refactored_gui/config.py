"""
Core configuration and constants for the GUI application.
Centralizes all configuration values for better maintainability.
"""

from dataclasses import dataclass
from typing import Dict, Any
import os

# Application Constants
APP_NAME = "Mistral 3.2 GGUF Chatbot"
APP_VERSION = "1.0"
APP_ORGANIZATION = "Local AI"

# Window Configuration
DEFAULT_WINDOW_GEOMETRY = (100, 100, 950, 800)

# Model Configuration
@dataclass
class ModelDefaults:
    temperature: float = 0.7
    max_tokens: int = 8192
    n_ctx: int = 16384
    system_prompt: str = ""
    debug: bool = False

# UI Constants
class UIConstants:
    # Colors
    ERROR_COLOR = "color: red; font-weight: bold;"
    SUCCESS_COLOR = "color: green; font-weight: bold;"
    WARNING_COLOR = "color: orange; font-weight: bold;"
    
    # Fonts
    DEFAULT_FONT_FAMILY = "Arial"
    DEFAULT_FONT_SIZE = 10
    CODE_FONT_FAMILY = "Courier New"
    
    # Tab Names
    TAB_CHAT = "Chat"
    TAB_SETUP = "Setup"
    TAB_RAG = "RAG"
    TAB_AGENT = "Agent"
    TAB_TRACE = "Trace"
    TAB_FEEDBACK = "Feedback"
    TAB_PERFORMANCE = "Performance"
    TAB_PSYCHOLOGY = "Psychology"

# File Extensions
SUPPORTED_IMAGE_EXTENSIONS = ('.png', '.jpg', '.jpeg')
SUPPORTED_DOCUMENT_EXTENSIONS = ('.pdf', '.txt', '.docx')

# RAG Configuration
@dataclass
class RAGDefaults:
    k: int = 3
    min_score: float = 0.3
    chunk_size: int = 1000
    chunk_overlap: int = 200
    persist: bool = True
    evidence_max_candidates: int = 10
    evidence_shortlist_m: int = 3
    evidence_diversity: float = 0.5
    news_min_k: int = 2
    news_max_k: int = 8
    planner_tokens: int = 1024
    sum_tokens: int = 2048
    ver_tokens: int = 512
    max_workers: int = 4

# Multi-Query RAG Configuration
@dataclass
class MultiQueryDefaults:
    enabled: bool = False
    n: int = 3
    k: int = 2

# Performance Settings
@dataclass
class PerformanceSettings:
    auto_refresh_interval: int = 5000  # milliseconds
    max_log_entries: int = 20
    progress_dialog_delay: int = 100  # milliseconds

# CSS Styles
TABLE_CSS = """
table, .markdown-table { 
    border-collapse: collapse; 
    width: 100%; 
    margin: 15px 0;
    font-family: Arial, sans-serif;
    font-size: 12px;
    background-color: white;
    box-shadow: 0 2px 4px rgba(0,0,0,0.1);
    border-radius: 6px;
    overflow: hidden;
}
th, td { 
    border: 1px solid #ddd; 
    padding: 12px 15px; 
    text-align: left;
    vertical-align: top;
    word-wrap: break-word;
    max-width: 300px;
    line-height: 1.4;
}
th { 
    background: linear-gradient(135deg, #f8f9fa 0%, #e9ecef 100%); 
    color: #333; 
    font-weight: bold;
    text-align: center;
    border-bottom: 2px solid #007bff;
    font-size: 13px;
}
tr:nth-child(even) {
    background-color: #f8f9fa;
}
tr:nth-child(odd) {
    background-color: #ffffff;
}
tr:hover {
    background-color: #e3f2fd !important;
    transition: background-color 0.2s ease;
}
table code, .markdown-table code {
    background-color: #f1f3f4;
    padding: 3px 6px;
    border-radius: 4px;
    font-family: 'Courier New', monospace;
    font-size: 11px;
    color: #d63384;
}
pre {
    background-color: #f8f9fa;
    padding: 12px;
    border-radius: 6px;
    border-left: 4px solid #007bff;
    overflow-x: auto;
    font-family: 'Courier New', monospace;
    font-size: 12px;
    line-height: 1.4;
}
"""

# Environment Variables
def get_env_config() -> Dict[str, Any]:
    """Get configuration from environment variables."""
    return {
        'rag_db_path': os.environ.get('RAG_DB_PATH', 'rag_store.db'),
        'debug_mode': os.environ.get('DEBUG_MODE', 'false').lower() == 'true',
        'log_level': os.environ.get('LOG_LEVEL', 'INFO'),
    }
