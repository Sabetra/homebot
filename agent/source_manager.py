"""
Source Management für AgentOrchestrator
=======================================

Dieses Modul übernimmt:
- Source-Deduplizierung (URL-basiert & Content-Hash)
- RAG-Persistence (Web-Sources → RAG-DB)
- Source-Formatting & Citation-Augmentation
- Used-Sources Filtering

Autor: Refactored from orchestrator.py (2025-10-08)
"""

from __future__ import annotations
from typing import List, Dict, Any, Optional
import hashlib
import logging
import re

logger = logging.getLogger(__name__)


class SourceManager:
    """Verwaltet Sources: Deduplizierung, RAG-Persistence, Formatierung"""
    
    def __init__(self, tools_manager=None, rag_persist_enabled: bool = True):
        """
        Args:
            tools_manager: ToolManager-Instanz für RAG-Persistence
            rag_persist_enabled: Ob Web-Sources in RAG persistiert werden sollen
        """
        self.tools = tools_manager
        self.rag_persist_enabled = rag_persist_enabled
        logger.info(f"✅ SourceManager initialisiert (RAG-Persist: {rag_persist_enabled})")
    
    def deduplicate_sources(self, sources: List[Any]) -> List[Any]:
        """Entfernt Duplikate basierend auf URL und Content-Hash"""
        seen_urls = set()
        seen_hashes = set()
        unique = []
        
        for src in sources:
            url = getattr(src, 'url', '')
            # BUG FIX 2026-03-10: SourceModel stores text in 'snippet', not 'content'.
            # Previously only checked 'content' (always None for RAG sources) and 'text'
            # (doesn't exist) → ALL sources hashed to MD5("") = same hash → 16 → 1.
            # Now checks content → snippet → text as fallback chain.
            content = (
                getattr(src, 'content', None)
                or getattr(src, 'snippet', None)
                or getattr(src, 'text', None)
                or ''
            )
            
            # URL-basierte Deduplizierung
            if url and url in seen_urls:
                continue
            
            # Content-Hash Deduplizierung
            content_hash = hashlib.md5(content.encode()).hexdigest()
            if content_hash in seen_hashes:
                continue
            
            if url:
                seen_urls.add(url)
            seen_hashes.add(content_hash)
            unique.append(src)
        
        logger.info(f"[DEDUP] {len(sources)} → {len(unique)} Sources nach Deduplizierung")
        return unique
    
    def maybe_persist_sources_to_rag(self, sources: List[Any]) -> None:
        """Persistiert Web-Sources optional in RAG-Datenbank"""
        if not self.rag_persist_enabled or not self.tools:
            return
        
        if not hasattr(self.tools, 'persist_to_rag'):
            logger.warning("[RAG-PERSIST] ToolManager hat keine persist_to_rag-Methode")
            return
        
        try:
            for src in sources:
                url = getattr(src, 'url', '')
                content = getattr(src, 'content', '') or getattr(src, 'text', '')
                title = getattr(src, 'title', url)
                
                if url and content:
                    self.tools.persist_to_rag(
                        text=content,
                        metadata={"source": url, "title": title}
                    )
            
            logger.info(f"[RAG-PERSIST] {len(sources)} Sources persistiert")
        except Exception as e:
            logger.warning(f"[RAG-PERSIST] Fehler beim Persistieren: {e}")
    
    def augment_citations(self, text: str, sources: List[Any]) -> str:
        """Fügt Quellenangaben als Inline-Citations hinzu"""
        if not sources:
            return text
        
        # Pattern: [1], [2], etc.
        pattern = r'\[(\d+)\]'
        
        def replace_citation(match):
            idx = int(match.group(1)) - 1
            if 0 <= idx < len(sources):
                url = getattr(sources[idx], 'url', '')
                return f"[{idx + 1}]({url})" if url else match.group(0)
            return match.group(0)
        
        augmented = re.sub(pattern, replace_citation, text)
        return augmented
    
    def append_sources_block(self, text: str, sources: List[Any]) -> str:
        """Fügt Quellen-Block am Ende der Antwort hinzu"""
        if not sources:
            return text
        
        sources_block = "\n\n**Quellen:**\n"
        for idx, src in enumerate(sources, 1):
            url = getattr(src, 'url', '')
            title = getattr(src, 'title', url)
            sources_block += self._format_source_entry(idx, src, title)
        
        return text + sources_block
    
    def _format_source_entry(self, idx: int, src: Any, title: str) -> str:
        """Formatiert einen einzelnen Quellen-Eintrag"""
        url = getattr(src, 'url', '')
        
        if url:
            return f"{idx}. [{title}]({url})\n"
        else:
            return f"{idx}. {title}\n"
    
    def filter_actually_used_sources(self, text: str, sources: List[Any]) -> List[Any]:
        """Filtert nur tatsächlich im Text referenzierte Sources"""
        if not sources:
            return []
        
        # Pattern: [1], [2], etc.
        pattern = r'\[(\d+)\]'
        matches = re.findall(pattern, text)
        used_indices = set(int(m) - 1 for m in matches)
        
        used_sources = [src for idx, src in enumerate(sources) if idx in used_indices]
        
        logger.info(f"[FILTER] {len(used_sources)}/{len(sources)} Sources tatsächlich verwendet")
        return used_sources
    
    def is_answer_based_on_general_knowledge(self, text: str) -> bool:
        """Prüft ob Antwort auf Allgemeinwissen basiert (keine Citations)"""
        # Suche nach [1], [2], etc.
        pattern = r'\[\d+\]'
        has_citations = bool(re.search(pattern, text))
        return not has_citations
