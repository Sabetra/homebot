"""
Response Builder für Agent System
==================================

Zentrale Response-Generierung und Formatting.

Features:
- Prompt Building (Summarizer, Verifier)
- Evidence Block Rendering
- Citation Management
- Response Formatting
- Source Filtering
- Multimodal Vorbereitung

Author: Implementation 2025-10-09
Updated: 2025-10-09 (Step 6 - Orchestrator Integration)
"""

from __future__ import annotations
from typing import List, Dict, Any, Callable, Optional
import logging
import re

logger = logging.getLogger(__name__)

try:
    from i18n import get_current_language as i18n_get_current_language
except Exception:
    i18n_get_current_language = None


def _language_instruction() -> str:
    """Return deterministic response-language instruction based on active i18n locale."""
    lang = "de"
    if callable(i18n_get_current_language):
        try:
            lang = i18n_get_current_language() or "de"
        except Exception:
            lang = "de"

    if lang == "bg":
        return "\n\nSPRACHREGEL: Antworte ausschliesslich auf Bulgarisch."
    if lang == "en":
        return "\n\nLANGUAGE RULE: Respond in English only."
    return "\n\nSPRACHREGEL: Antworte ausschliesslich auf Deutsch."

# Import Prompts - pre-declare with proper types for conditional import
render_evidence_block: Optional[Callable[..., str]] = None
render_extras_block: Optional[Callable[..., str]] = None
SUMMARIZER_SYSTEM: Optional[str] = None
SUMMARIZER_USER_TEMPLATE: Optional[str] = None
SUMMARIZER_FALLBACK_SYSTEM: Optional[str] = None
SUMMARIZER_FALLBACK_USER_TEMPLATE: Optional[str] = None
VERIFIER_SYSTEM: Optional[str] = None
VERIFIER_USER_TEMPLATE: Optional[str] = None
VERIFIER_FALLBACK_SYSTEM: Optional[str] = None
PROMPTS_AVAILABLE = False

try:
    from agent.prompts import (
        render_evidence_block,  # type: ignore[assignment,no-redef]
        render_extras_block,  # type: ignore[assignment,no-redef]
        SUMMARIZER_SYSTEM,  # type: ignore[assignment,no-redef]
        SUMMARIZER_USER_TEMPLATE,  # type: ignore[assignment,no-redef]
        SUMMARIZER_FALLBACK_SYSTEM,  # type: ignore[assignment,no-redef]
        SUMMARIZER_FALLBACK_USER_TEMPLATE,  # type: ignore[assignment,no-redef]
        VERIFIER_SYSTEM,  # type: ignore[assignment,no-redef]
        VERIFIER_USER_TEMPLATE,  # type: ignore[assignment,no-redef]
        VERIFIER_FALLBACK_SYSTEM  # type: ignore[assignment,no-redef]
    )
    PROMPTS_AVAILABLE = True
    logger.info("✅ Prompts erfolgreich geladen")
except ImportError as e:
    logger.warning(f"Prompts nicht verfügbar: {e}")


class CitationManager:
    """Verwaltet Citations im Text"""
    
    def augment_citations(
        self,
        text: str,
        sources: List[Any],
        include_details: bool = True
    ) -> str:
        """
        Erweitert [n] Citations mit Details (Orchestrator-kompatibel)
        
        Ersetzt [n] mit [n] (Title -- Domain/Seite)
        
        Args:
            text: Text mit [n] Citations
            sources: Liste von Sources
            include_details: Details hinzufügen (Title, Domain, Page)
            
        Returns:
            Text mit erweiterten Citations
        """
        if not text or not sources or not include_details:
            return text
        
        # Build formatter für jeden Source-Index
        def fmt(i: int, s: Any) -> str:
            """Formatiert Citation mit Details"""
            title = getattr(s, 'title', None) or 'Quelle'
            url = getattr(s, 'url', None) or ''
            page = getattr(s, 'page', None)
            
            try:
                from urllib.parse import urlparse
            except ImportError:
                urlparse = None  # type: ignore
            
            # Web sources: show domain (and short path)
            if url.startswith('http') and urlparse is not None:
                try:
                    pu = urlparse(url)
                    domain = pu.netloc or ''
                    path = (pu.path or '').rstrip('/')
                    short_path = ''
                    if path:
                        seg = path.split('/')[-1]
                        if seg:
                            short_path = f"/{seg}"
                    if short_path:
                        return f"[{i}] ({title} -- {domain}{short_path})"
                    return f"[{i}] ({title} -- {domain})"
                except Exception as e:
                    logger.debug(f"URL-Parsing-Fehler bei Citation: {e}")
                    return f"[{i}] ({title})"
            
            # RAG sources: include page number
            if page is not None:
                return f"[{i}] ({title}, S. {page})"
            
            return f"[{i}] ({title})"
        
        # Replace bare [n] with enriched form (avoid double-augmenting)
        out = text
        for idx, s in enumerate(sources, start=1):
            rich = fmt(idx, s)
            # Replace [n] but not [n] (...) to avoid double-augmentation
            out = re.sub(rf"\[{idx}\](?!\s*\()", rich, out)
        
        return out
    
    def filter_actually_used_sources(
        self,
        text: str,
        sources: List[Any]
    ) -> List[Any]:
        """
        Filtert Sources: nur tatsächlich im Text referenzierte [n]
        
        Orchestrator-kompatibel: Erkennt auch irrelevance statements
        
        Args:
            text: Text mit [n] References
            sources: Alle Sources
            
        Returns:
            Liste von tatsächlich verwendeten Sources
        """
        if not text or not sources:
            return []
        
        # Check for irrelevance indicators
        irrelevant_phrases = [
            "bereitgestellten daten sind nicht relevant",
            "daten sind nicht relevant für die frage",
            "keine relevanten informationen",
            "nicht relevant für",
            "basiert nicht auf den bereitgestellten quellen",
            "aus allgemeinem wissen",
            "allgemein bekannt",
            "allgemeine information",
            "nicht aus den bereitgestellten",
            "ohne spezifische quellen",
            "bereitgestellten quellen keine",
            "enthalten leider keine relevanten informationen",
            "keine aktuellen informationen",
            "da sie sich auf andere themen",
            "beziehen sich auf andere themen"
        ]
        
        text_lower = text.lower()
        for phrase in irrelevant_phrases:
            if phrase in text_lower:
                logger.debug(f"Sources als irrelevant markiert: '{phrase}'")
                return []
        
        # Check for range-based irrelevance patterns
        range_irrelevant_patterns = [
            r'bereitgestellten? quellen? \[\d+\](?:-\[\d+\])? enthalten.*keine.*relevanten?',
            r'quellen? \[\d+\](?:-\[\d+\])? .*keine.*informationen',
            r'bereitgestellten? .*\[\d+\](?:-\[\d+\])? .*nicht relevant',
        ]
        
        for pattern in range_irrelevant_patterns:
            if re.search(pattern, text_lower):
                logger.debug(f"Sources als irrelevant markiert (Range-Pattern): '{pattern}'")
                return []
        
        # Find all [n] references (exclude those in irrelevance statements)
        referenced_indices = set()
        
        # Split into sentences
        sentences = re.split(r'[.!?]+', text)
        for sentence in sentences:
            sentence_lower = sentence.lower()
            
            # Skip sentences about source irrelevance
            skip_sentence = any(phrase in sentence_lower for phrase in [
                "keine relevanten informationen",
                "nicht relevant",
                "enthalten leider keine",
                "sich auf andere themen"
            ])
            
            if not skip_sentence:
                # Count [n] references
                for match in re.finditer(r'\[(\d+)\]', sentence):
                    try:
                        idx = int(match.group(1))
                        if 1 <= idx <= len(sources):
                            referenced_indices.add(idx - 1)  # 0-based
                    except ValueError:
                        continue
        
        # Return only referenced sources
        filtered = [sources[i] for i in sorted(referenced_indices)]
        
        if referenced_indices:
            logger.debug(f"Gefilterte Sources: {len(filtered)}/{len(sources)} tatsächlich verwendet")
        else:
            logger.debug("Keine gültigen Source-Referenzen gefunden")
        
        return filtered
    
    def append_sources_block(
        self,
        text: str,
        sources: List[Any]
    ) -> str:
        """
        Fügt Sources-Block am Ende an (Orchestrator-kompatibel)
        
        Zeigt nur tatsächlich verwendete Sources mit HTML-Links
        
        Args:
            text: Haupt-Text
            sources: Liste von Sources
            
        Returns:
            Text mit Sources-Block
        """
        if not sources:
            return text
        
        # Filter: nur tatsächlich verwendete Sources
        used_sources = self.filter_actually_used_sources(text, sources)
        
        if not used_sources:
            # Keine Sources wurden verwendet
            return text or ""
        
        # Build sources block
        lines: List[str] = []
        lines.append("\n\nQuellen:")
        
        for idx, s in enumerate(used_sources, start=1):
            title = getattr(s, 'title', None) or "Quelle"
            entry = self._format_source_entry(idx, s, title)
            lines.append(entry)
        
        return (text or "") + "\n" + "\n".join(lines) + "\n"
    
    def _format_source_entry(self, idx: int, s: Any, title: str) -> str:
        """
        Formatiert einen einzelnen Source-Eintrag (Orchestrator-kompatibel)
        
        Args:
            idx: Source-Index (1-based)
            s: Source-Objekt
            title: Source-Titel
            
        Returns:
            Formatierter Source-Entry mit HTML-Link
        """
        url = getattr(s, 'url', None) or ''
        page = getattr(s, 'page', None)
        
        # Web sources: HTML-Link mit Domain
        if url.startswith('http'):
            try:
                from urllib.parse import urlparse
                pu = urlparse(url)
                domain = pu.netloc or ''
                path = (pu.path or '').rstrip('/')
                short_path = ''
                if path:
                    seg = path.split('/')[-1]
                    if seg:
                        short_path = f"/{seg}"
                tail = f" -- {domain}{short_path}" if domain else ''
                # Clickable HTML link
                return f"[{idx}] <a href=\"{url}\">{title}</a>{tail}"
            except Exception as e:
                logger.debug(f"HTML-Link-Erstellung fehlgeschlagen für Source {idx}: {e}")
                return f"[{idx}] {title}"
        
        # RAG source: Seite anzeigen
        if page is not None:
            return f"[{idx}] {title} -- S. {page}"
        
        return f"[{idx}] {title}"


class PromptBuilder:
    """Baut Prompts für LLM-Calls"""
    
    def __init__(self) -> None:
        """Initialisiert PromptBuilder"""
        self.citation_manager = CitationManager()
    
    def build_summarizer_prompt(
        self,
        query: str,
        sources: List[Any],
        extras: Optional[List[str]] = None,
        history: Optional[List[Dict[str, Any]]] = None,
        fallback: bool = False,
        user_system_prompt: str = ""
    ) -> Dict[str, Any]:
        """
        Baut Summarizer Prompt (Orchestrator-kompatibel)
        
        Args:
            query: User Query
            sources: Liste von Sources (nicht evidences!)
            extras: Optional Extras (non-web tool results)
            history: Optional Chat History
            fallback: Fallback-Modus (keine Sources)
            user_system_prompt: User-defined System Prompt
            
        Returns:
            Dict mit 'system_message', 'user_message', 'messages'
        """
        # System Prompt
        if fallback:
            system_prompt = SUMMARIZER_FALLBACK_SYSTEM if (PROMPTS_AVAILABLE and SUMMARIZER_FALLBACK_SYSTEM) else "Du bist ein hilfreicher Assistent."
        else:
            system_prompt = SUMMARIZER_SYSTEM if (PROMPTS_AVAILABLE and SUMMARIZER_SYSTEM) else "Du bist ein hilfreicher Assistent."
        
        # User System Prompt Integration
        if user_system_prompt:
            system_prompt = f"{system_prompt}\n\n{user_system_prompt}"

        system_prompt = f"{system_prompt}{_language_instruction()}"
        
        # Convert Sources to evidence list format (for render_evidence_block)
        ev_list = []
        if sources and not fallback:
            for s in sources:
                meta = getattr(s, 'meta', None) or {}
                ev_list.append({
                    "title": getattr(s, 'title', None) or '',
                    "url": getattr(s, 'url', None) or '',
                    "date": getattr(s, 'date', None) or '',
                    "snippet": getattr(s, 'snippet', None) or '',
                    "page": getattr(s, 'page', None),
                    "kg_context": meta.get('kg_context', []),
                })
        
        # User Content
        if fallback:
            # Fallback mode: no evidence
            if PROMPTS_AVAILABLE and SUMMARIZER_FALLBACK_USER_TEMPLATE and render_extras_block:  # type: ignore[unreachable]
                extras_block = render_extras_block(extras) if extras else ""  # type: ignore[unreachable]
                user_content = SUMMARIZER_FALLBACK_USER_TEMPLATE.format(
                    query=query,
                    extras_block=extras_block
                )
            else:
                user_content = f"Frage: {query}"
        else:
            # Normal mode: with evidence
            if PROMPTS_AVAILABLE and SUMMARIZER_USER_TEMPLATE and render_evidence_block and render_extras_block:  # type: ignore[unreachable]
                evidence_block = render_evidence_block(ev_list) if ev_list else ""  # type: ignore[unreachable]
                extras_block = render_extras_block(extras) if extras else ""
                user_content = SUMMARIZER_USER_TEMPLATE.format(
                    query=query,
                    evidence_block=evidence_block,
                    extras_block=extras_block
                )
            else:
                user_content = f"Frage: {query}"
        
        # Build messages
        sys_msg = {"role": "system", "content": system_prompt}
        user_msg = {"role": "user", "content": user_content}
        
        # Include history if provided
        messages = [sys_msg]
        if history:
            messages.extend(history)
        messages.append(user_msg)
        
        return {
            'system_message': system_prompt,
            'user_message': user_content,
            'messages': messages
        }
    
    def build_verifier_prompt(
        self,
        query: str,
        draft: str,
        sources: List[Any],
        fallback: bool = False
    ) -> Dict[str, Any]:
        """
        Baut Verifier Prompt (Orchestrator-kompatibel)
        
        Args:
            query: Original Query
            draft: Draft Answer (statt 'answer')
            sources: Sources (evidence)
            fallback: Fallback-Modus
            
        Returns:
            Dict mit 'system_message', 'user_message', 'messages'
        """
        # System Prompt
        if fallback:
            system_prompt = VERIFIER_FALLBACK_SYSTEM if (PROMPTS_AVAILABLE and VERIFIER_FALLBACK_SYSTEM) else "Du bist ein Verifikations-Experte."
        else:
            system_prompt = VERIFIER_SYSTEM if (PROMPTS_AVAILABLE and VERIFIER_SYSTEM) else "Du bist ein Verifikations-Experte."

        system_prompt = f"{system_prompt}{_language_instruction()}"
        
        # User Content
        if fallback:
            # Fallback: simple verification without evidence
            user_content = f"Frage: {query}\n\nEntwurf der Antwort (zu prüfen):\n{draft}\n\nGib nur die bereinigte Endfassung zurück."
        else:
            # Normal: verification with evidence
            # Convert Sources to evidence list
            ev_list = []
            for s in sources:
                ev_list.append({
                    "title": getattr(s, 'title', None) or '',
                    "url": getattr(s, 'url', None) or '',
                    "date": getattr(s, 'date', None) or '',
                    "snippet": getattr(s, 'snippet', None) or '',
                    "page": getattr(s, 'page', None)
                })
            
            if PROMPTS_AVAILABLE and VERIFIER_USER_TEMPLATE and render_evidence_block:  # type: ignore[unreachable]
                evidence_block = render_evidence_block(ev_list) if ev_list else ""  # type: ignore[unreachable]
                user_content = VERIFIER_USER_TEMPLATE.format(
                    query=query,
                    evidence_block=evidence_block,
                    draft=draft
                )
            else:
                user_content = f"""Verifiziere diese Antwort:

Frage: {query}

Entwurf: {draft}

Ist der Entwurf korrekt?"""
        
        # Build messages
        sys_msg = {"role": "system", "content": system_prompt}
        user_msg = {"role": "user", "content": user_content}
        messages = [sys_msg, user_msg]
        
        return {
            'system_message': system_prompt,
            'user_message': user_content,
            'messages': messages
        }
    
    def _fallback_evidence_block(self, evidences: List[Any]) -> str:
        """Fallback Evidence Block Rendering"""
        if not evidences:
            return "[Keine Evidences verfügbar]"
        
        block = ""
        for i, ev in enumerate(evidences, 1):
            content = getattr(ev, 'content', getattr(ev, 'text', str(ev)))
            source = getattr(ev, 'source', 'unknown')
            
            block += f"[{i}] {content[:300]}...\n"
            block += f"    Quelle: {source}\n\n"
        
        return block


class ResponseFormatter:
    """Formatiert finale Responses"""
    
    def __init__(self) -> None:
        """Initialisiert ResponseFormatter"""
        self.citation_manager = CitationManager()
    
    def format_response(
        self,
        raw_answer: str,
        sources: List[Any],
        include_citations: bool = True,
        append_sources: bool = True,
        verification_result: Optional[Dict[str, Any]] = None
    ) -> str:
        """
        Formatiert finale Antwort
        
        Args:
            raw_answer: Rohe LLM-Antwort
            sources: Liste von Sources
            include_citations: Citations erweitern
            append_sources: Sources-Block anhängen
            verification_result: Optional Verification Result
            
        Returns:
            Formatierte Antwort
        """
        formatted = raw_answer
        
        # 1. Citation Augmentation
        if include_citations and sources:
            formatted = self.citation_manager.augment_citations(
                formatted,
                sources,
                include_details=True
            )
        
        # 2. Append Sources Block
        if append_sources and sources:
            formatted = self.citation_manager.append_sources_block(
                formatted,
                sources
            )
        
        # 3. Verification Warning (optional)
        if verification_result and not verification_result.get('passed', True):
            warning = "\n\n⚠️ *Hinweis: Diese Antwort konnte nicht vollständig verifiziert werden.*"
            formatted += warning
        
        return formatted


class ResponseBuilder:
    """
    Zentrale Response-Generierung
    
    Koordiniert:
    - Prompt Building (Summarizer, Verifier)
    - Citation Management
    - Response Formatting
    """
    
    def __init__(self) -> None:
        """Initialisiert ResponseBuilder"""
        self.prompt_builder: PromptBuilder = PromptBuilder()
        self.response_formatter: ResponseFormatter = ResponseFormatter()
        
        logger.info("✅ ResponseBuilder initialisiert")
    
    def build_summarizer_prompt(
        self,
        query: str,
        sources: List[Any],
        **kwargs
    ) -> Dict[str, Any]:
        """
        Baut Summarizer Prompt (Orchestrator-kompatibel)
        
        Args:
            query: User Query
            sources: Sources (nicht evidences!)
            **kwargs: extras, history, fallback, user_system_prompt
            
        Returns:
            Dict mit 'messages', 'system_message', 'user_message'
        """
        return self.prompt_builder.build_summarizer_prompt(
            query=query,
            sources=sources,
            **kwargs
        )
    
    def build_verifier_prompt(
        self,
        query: str,
        draft: str,
        sources: List[Any],
        **kwargs
    ) -> Dict[str, Any]:
        """
        Baut Verifier Prompt (Orchestrator-kompatibel)
        
        Args:
            query: Original Query
            draft: Draft Answer
            sources: Sources
            **kwargs: fallback
            
        Returns:
            Dict mit 'messages', 'system_message', 'user_message'
        """
        return self.prompt_builder.build_verifier_prompt(
            query=query,
            draft=draft,
            sources=sources,
            **kwargs
        )
    
    def format_response(
        self,
        raw_answer: str,
        sources: List[Any],
        **kwargs
    ) -> str:
        """
        Formatiert finale Response
        
        Args:
            raw_answer: Rohe LLM-Antwort
            sources: Sources
            **kwargs: Zusätzliche Args
            
        Returns:
            Formatierte Antwort
        """
        return self.response_formatter.format_response(
            raw_answer=raw_answer,
            sources=sources,
            **kwargs
        )


# Singleton (optional)
_response_builder_instance: Optional[ResponseBuilder] = None


def get_response_builder() -> ResponseBuilder:
    """
    Gibt ResponseBuilder Singleton zurück
    
    Returns:
        ResponseBuilder Instance
    """
    global _response_builder_instance
    if _response_builder_instance is None:
        _response_builder_instance = ResponseBuilder()
    return _response_builder_instance
