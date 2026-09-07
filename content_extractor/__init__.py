"""
Content Extractor V2 - Modern Content Extraction System

Modular, strategy-based content extraction system.
Refactored from _modern_content_extract (CC: 49 → <10).

Usage:
    from content_extractor import create_default_orchestrator
    
    orchestrator = create_default_orchestrator()
    result = orchestrator.extract(url="https://example.com")

Architecture:
    - ModernContentOrchestrator: Strategy coordinator
    - Extraction Strategies: Trafilatura, Readability, Newspaper3k, Jina AI
    - Pydantic V2 Models: Type-safe data structures
    - Utilities: Quality scoring, result formatting
"""
from .base import ContentExtractor, BaseContentExtractor
from .models import ExtractionContext, ExtractionResult, ContentMetadata
from .orchestrator import ModernContentOrchestrator
from .strategies import (
    TrafilaturaExtractor,
    ReadabilityExtractor,
    Newspaper3kExtractor,
    JinaAIExtractor
)

__version__ = "2.0.0"
__all__ = [
    "ContentExtractor",
    "BaseContentExtractor",
    "ExtractionContext",
    "ExtractionResult",
    "ContentMetadata",
    "ModernContentOrchestrator",
    "TrafilaturaExtractor",
    "ReadabilityExtractor",
    "Newspaper3kExtractor",
    "JinaAIExtractor",
    "create_default_orchestrator"
]


def create_default_orchestrator() -> ModernContentOrchestrator:
    """
    Create orchestrator with default extraction strategies.
    
    Strategies are added in priority order:
        1. Trafilatura (priority 10) - Best for news/articles
        2. Readability (priority 20) - Good for structured content
        3. Newspaper3k (priority 30) - Good for news articles
        4. Jina AI (priority 40) - API-based fallback
    
    Returns:
        Configured ModernContentOrchestrator
    """
    orchestrator = ModernContentOrchestrator()
    
    # Add strategies (will be auto-sorted by priority)
    orchestrator.add_strategy(TrafilaturaExtractor())
    orchestrator.add_strategy(ReadabilityExtractor())
    orchestrator.add_strategy(Newspaper3kExtractor())
    orchestrator.add_strategy(JinaAIExtractor())
    
    return orchestrator
