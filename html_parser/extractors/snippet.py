"""
HTML Parser V2 - Snippet Generator

Generate query-aware snippets from text content.
Target CC: <8
"""
from typing import Optional


class SnippetGenerator:
    """
    Generate snippets from text with optional query highlighting.
    
    Strategy:
    - If query provided: highlight query with context (500 chars before, 1500 after)
    - If no query: first 8000 chars
    
    Target CC: 7
    """
    
    MAX_SNIPPET_LEN = 8000
    CONTEXT_BEFORE = 500
    CONTEXT_AFTER = 1500
    
    def generate(self, text: str, query: Optional[str] = None) -> str:
        """
        Generate snippet from text.
        
        Args:
            text: Full text content
            query: Optional search query for highlighting
            
        Returns:
            Generated snippet (max 8000 chars)
        """
        text = (text or "").strip()
        if not text:
            return ""
        
        # Query-aware snippet
        if query:
            snippet = self._generate_with_query(text, query)
            if snippet:
                return snippet
        
        # Default snippet: first 8000 chars
        return self._generate_default(text)
    
    def _generate_with_query(self, text: str, query: str) -> str:
        """Generate snippet with query highlighting."""
        query_lower = query.strip().lower()
        text_lower = text.lower()
        
        # Find query position
        pos = text_lower.find(query_lower)
        if pos == -1:
            return ""  # Query not found, use default
        
        # Calculate snippet boundaries
        start = max(0, pos - self.CONTEXT_BEFORE)
        end = min(len(text), pos + len(query) + self.CONTEXT_AFTER)
        
        # Extract snippet
        snippet = text[start:end].strip()
        
        # Add ellipsis if truncated
        if end < len(text):
            snippet += "…"
        
        return snippet
    
    def _generate_default(self, text: str) -> str:
        """Generate default snippet (first N chars)."""
        if len(text) <= self.MAX_SNIPPET_LEN:
            return text
        
        snippet = text[:self.MAX_SNIPPET_LEN].strip()
        return snippet + "…"
