"""
Psychologische Unterstützung - Hauptmodul
=========================================
Modulare Integration für therapeutische Chat-Features mit Session-Management,
Kontext-Zusammenfassung und DSGVO-konformer Datenspeicherung.

Features:
- Session-basierte Benutzerführung
- KI-gestützte Kontext-Zusammenfassung
- Separate, verschlüsselte Datenspeicherung
- Datenschutz-konforme Anonymisierung
- Integration in bestehende Chat-Architektur
"""

__version__ = "1.0.0"
__author__ = "GitHub Copilot"

# Lazy exports to avoid heavy top-level imports during test collection
__all__ = [
    "WellbeingSessionManager",
    "ContextSummarizer",
    "WellbeingDatabase",
    "ConversationPromptManager",
    "PrivacyHandler",
]


def __getattr__(name: str):
    """Lazily import submodules on attribute access.

    This prevents importing heavy modules (and their side-effects) at package
    import time (helps pytest collection and tooling).
    """
    if name == 'WellbeingSessionManager':
        from .session_manager import WellbeingSessionManager
        return WellbeingSessionManager
    if name == 'ContextSummarizer':
        from .context_summarizer import ContextSummarizer
        return ContextSummarizer
    if name == 'WellbeingDatabase':
        from .wellbeing_db import WellbeingDatabase
        return WellbeingDatabase
    if name == 'ConversationPromptManager':
        from .conversation_prompts import ConversationPromptManager
        return ConversationPromptManager
    if name == 'PrivacyHandler':
        from .privacy_handler import PrivacyHandler
        return PrivacyHandler
    raise AttributeError(f"module {__name__} has no attribute {name}")
