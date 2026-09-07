"""
Web Search Strategies
=====================

Search engine implementations.
"""

from .duckduckgo import DuckDuckGoStrategy
from .brave import BraveSearchStrategy

__all__ = ["DuckDuckGoStrategy", "BraveSearchStrategy"]
