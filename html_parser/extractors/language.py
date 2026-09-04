"""
HTML Parser V2 - Language Detector

Detect language (German/English) using simple heuristics.
Target CC: <5
"""
from typing import Optional


class LanguageDetector:
    """
    Detect language from text using word frequency heuristics.
    
    Supports: German (de), English (en)
    Target CC: 4
    """
    
    GERMAN_WORDS = [" der ", " die ", " das ", " und ", " ist ", " nicht ", " mit "]
    ENGLISH_WORDS = [" the ", " and ", " is ", " not ", " with ", " from "]
    MIN_HITS = 2
    
    def detect(self, text: str) -> Optional[str]:
        """
        Detect language from text.
        
        Args:
            text: Text content to analyze
            
        Returns:
            Language code ("de" or "en") or None if uncertain
        """
        if not text:
            return None
        
        # Normalize text
        text_lower = f" {text.lower()} "
        
        # Count language-specific words
        de_hits = self._count_words(text_lower, self.GERMAN_WORDS)
        en_hits = self._count_words(text_lower, self.ENGLISH_WORDS)
        
        # Determine language
        return self._determine_language(de_hits, en_hits)
    
    def _count_words(self, text: str, words: list[str]) -> int:
        """Count occurrences of words in text."""
        return sum(1 for word in words if word in text)
    
    def _determine_language(self, de_hits: int, en_hits: int) -> Optional[str]:
        """Determine language from word counts."""
        if de_hits > en_hits and de_hits >= self.MIN_HITS:
            return "de"
        elif en_hits > de_hits and en_hits >= self.MIN_HITS:
            return "en"
        return None
