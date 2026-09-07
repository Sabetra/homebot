"""
Web Search Enrichment
======================

Content enrichment strategies.
"""

from .html import HTMLEnrichment
from .ai import AIEnrichment
from .snippet_extractor import AnswerSnippetExtractor, get_snippet_extractor

__all__ = ["HTMLEnrichment", "AIEnrichment", "AnswerSnippetExtractor", "get_snippet_extractor"]
