"""
LLM Utilities Package
=====================

Robuste, wiederverwendbare Komponenten für LLM-Interaktionen:
- GuaranteedLLMCaller: Niemals leere Responses
- RobustResponseHandler: Multi-Methoden JSON-Parsing
- LLMLanguageDetector: Sprachunabhängige Spracherkennung

Author: AI System Evolution
Date: 2025-10-05
"""

from .guaranteed_caller import GuaranteedLLMCaller
from .robust_response_handler import RobustResponseHandler
from .language_detector import LLMLanguageDetector

__all__ = [
    'GuaranteedLLMCaller',
    'RobustResponseHandler',
    'LLMLanguageDetector'
]
