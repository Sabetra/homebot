"""
HTML Parser V2 - Main Orchestrator

State Machine for coordinating HTML parsing pipeline.
Cyclomatic Complexity Target: <10
"""
import logging
import urllib.parse
from typing import Optional, Dict, Any

from html_parser.models import (
    HTMLParseContext,
    HTMLParseResult,
    CleanupResult,
    MetadataResult,
    ContentResult
)
from html_parser.base import ParsingState


class HTMLParserOrchestrator:
    """
    State Machine orchestrator for HTML parsing.
    
    Flow: CLEANUP → METADATA → CONTENT → POST_PROCESS → DONE
    Fallback: Any failure → FALLBACK → DONE
    
    Target CC: <10
    """
    
    def __init__(self) -> None:
        self.state = ParsingState.CLEANUP
        self.errors: list[str] = []
        
    def parse(self, url: str, html: str, *, query: Optional[str] = None) -> Dict[str, Any]:
        """
        Main entry point for HTML parsing.
        
        Args:
            url: URL of the HTML page
            html: Raw HTML content
            query: Optional search query for snippet highlighting
            
        Returns:
            Dict with parsed HTML data (matches legacy format)
        """
        try:
            # Validate input
            context = HTMLParseContext(url=url, html=html, query=query)
            
            # Execute state machine
            result = self._execute_pipeline(context)
            
            # Return as dict (matches legacy format)
            return result.model_dump()
            
        except Exception as e:
            logging.error(f"HTML parser orchestrator failed for {url}: {type(e).__name__}: {e}")
            # Ultimate fallback: use fallback parser
            return self._fallback_parse(url, html, query)
    
    def _execute_pipeline(self, context: HTMLParseContext) -> HTMLParseResult:
        """
        Execute the parsing pipeline through all states.
        
        CC: 8 (4 states + 1 try + 1 fallback + 1 quality + 1 return)
        """
        try:
            # Stage 1: Cleanup
            cleanup_result = self._stage_cleanup(context)
            
            # Stage 2: Metadata
            metadata_result = self._stage_metadata(cleanup_result, context)
            
            # Stage 3: Content
            content_result = self._stage_content(cleanup_result, context)
            
            # Stage 4: Post-process
            result = self._stage_post_process(context, metadata_result, content_result)
            
            return result
            
        except Exception as e:
            logging.warning(f"Pipeline failed for {context.url}: {e}, using fallback")
            return self._fallback_to_result(context)
    
    def _stage_cleanup(self, context: HTMLParseContext) -> CleanupResult:
        """Stage 1: Clean HTML by removing noisy tags."""
        from html_parser.cleanup.noisy_tags import NoisyTagsCleanup
        
        cleaner = NoisyTagsCleanup()
        return cleaner.cleanup(context.html, context.url)
    
    def _stage_metadata(self, cleanup: CleanupResult, context: HTMLParseContext) -> MetadataResult:
        """Stage 2: Extract metadata (canonical, og, meta)."""
        from html_parser.extractors.metadata import MetadataExtractor
        
        extractor = MetadataExtractor()
        return extractor.extract(cleanup.soup, context.url)
    
    def _stage_content(self, cleanup: CleanupResult, context: HTMLParseContext) -> ContentResult:
        """Stage 3: Extract content (title, text, snippet)."""
        from html_parser.extractors.title import TitleExtractor
        from html_parser.extractors.main_content import MainContentExtractor
        from html_parser.extractors.snippet import SnippetGenerator
        from html_parser.extractors.language import LanguageDetector
        
        # Extract components
        title_ext = TitleExtractor()
        content_ext = MainContentExtractor()
        snippet_gen = SnippetGenerator()
        lang_det = LanguageDetector()
        
        title = title_ext.extract(cleanup.soup)
        text = content_ext.extract(cleanup.soup)
        snippet = snippet_gen.generate(text, context.query)
        language = lang_det.detect(text)
        
        return ContentResult(
            title=title,
            text=text,
            snippet=snippet,
            language=language,
            text_len=len(text),
            errors=[]
        )
    
    def _stage_post_process(
        self,
        context: HTMLParseContext,
        metadata: MetadataResult,
        content: ContentResult
    ) -> HTMLParseResult:
        """Stage 4: Post-process and assemble final result."""
        # Extract domain
        domain = urllib.parse.urlparse(context.url).netloc.lower()
        if domain.startswith("www."):
            domain = domain[4:]
        
        # Calculate extraction quality
        quality = self._calculate_quality(metadata, content)
        
        # Assemble result
        return HTMLParseResult(
            url=context.url,
            canonical_url=metadata.canonical_url or context.url,
            domain=domain,
            title=content.title or "",
            snippet=content.snippet,
            text_len=content.text_len,
            language=content.language,
            og=metadata.og,
            meta=metadata.meta,
            extraction_quality=quality,
            fallback_used=False,
            errors=metadata.errors + content.errors,
            full_text=content.text or "",  # SOTA v2.1: Full text for snippet extraction
        )
    
    def _calculate_quality(self, metadata: MetadataResult, content: ContentResult) -> float:
        """Calculate extraction quality score (0.0 - 1.0)."""
        score = 0.0
        
        # Title found: +0.3
        if content.title:
            score += 0.3
        
        # Text found: +0.3
        if content.text_len > 100:
            score += 0.3
        
        # Metadata found: +0.2
        if metadata.og or metadata.meta:
            score += 0.2
        
        # Language detected: +0.2
        if content.language:
            score += 0.2
        
        return min(1.0, score)
    
    def _fallback_parse(self, url: str, html: str, query: Optional[str]) -> Dict[str, Any]:
        """Ultimate fallback: minimal regex-based parsing."""
        from html_parser.fallback.regex_parser import RegexFallbackParser
        
        parser = RegexFallbackParser()
        fallback_result = parser.parse(html, url)
        
        # Extract domain
        domain = urllib.parse.urlparse(url).netloc.lower()
        if domain.startswith("www."):
            domain = domain[4:]
        
        return {
            "url": url,
            "canonical_url": url,
            "domain": domain,
            "title": fallback_result.title,
            "snippet": fallback_result.text[:8000],
            "full_text": fallback_result.text,  # SOTA v2.1: Full text for snippet extraction
            "text_len": len(fallback_result.text),
            "language": None,
            "og": {},
            "meta": {},
            "extraction_quality": 0.2,
            "fallback_used": True,
            "errors": fallback_result.errors
        }
    
    def _fallback_to_result(self, context: HTMLParseContext) -> HTMLParseResult:
        """Convert fallback parse to HTMLParseResult."""
        fallback_dict = self._fallback_parse(context.url, context.html, context.query)
        return HTMLParseResult(**fallback_dict)
