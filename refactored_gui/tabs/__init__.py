"""
Tab Components for the Refactored GUI

This module contains all tab implementations for the main application.
Each tab is a self-contained component that can be easily extended or modified.

Available Tabs:
- ChatTab: Main chat interface
- SetupTab: Model and application configuration
- RAGTab: RAG system configuration and document management
- PerformanceTab: System monitoring and performance metrics

Planned Tabs:
- AgentTab: AI agent settings (future)
- FeedbackTab: User feedback analysis (future)
"""

from .chat_tab import ChatTab
from .setup_tab import SetupTab
from .rag_tab import RAGTab
from .performance_tab import PerformanceTab

# Import additional tabs as they are implemented
# from .agent_tab import AgentTab
# from .feedback_tab import FeedbackTab

__all__ = [
    "ChatTab",
    "SetupTab", 
    "RAGTab",
    "PerformanceTab",
    # Additional tabs will be added here
]
