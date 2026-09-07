"""
Content Extractor V2 - Quality Scorer

Utility for scoring extraction quality based on content characteristics.
"""
import re
from typing import Optional


class QualityScorer:
    """
    Scores content quality based on various heuristics.
    
    Target CC: <5
    """
    
    # Quality thresholds
    MIN_LENGTH = 100
    GOOD_LENGTH = 500
    GREAT_LENGTH = 1500
    
    # Penalty patterns
    ERROR_PATTERNS = [
        r"403\s*forbidden",
        r"404\s*not\s*found",
        r"access\s*denied",
        r"captcha",
        r"please\s*enable\s*javascript"
    ]
    
    @staticmethod
    def score(
        text: str,
        title: Optional[str] = None,
        has_metadata: bool = False
    ) -> float:
        """
        Calculate quality score for extracted content.
        
        Args:
            text: Extracted text content
            title: Optional title
            has_metadata: Whether metadata was extracted
            
        Returns:
            Quality score between 0.0 and 1.0
        """
        if not text:
            return 0.0
        
        score = 0.0
        text_lower = text.lower()
        
        # Base score from length
        score += QualityScorer._length_score(len(text))
        
        # Bonus for title
        if title and len(title) > 5:
            score += 0.1
        
        # Bonus for metadata
        if has_metadata:
            score += 0.05
        
        # Penalty for error indicators
        for pattern in QualityScorer.ERROR_PATTERNS:
            if re.search(pattern, text_lower):
                score -= 0.2
                break
        
        # Ensure score is between 0 and 1
        return max(0.0, min(1.0, score))
    
    @staticmethod
    def _length_score(length: int) -> float:
        """
        Calculate score component based on text length.
        
        Args:
            length: Text length in characters
            
        Returns:
            Score component (0.0 to 0.75)
        """
        if length < QualityScorer.MIN_LENGTH:
            return 0.0
        elif length < QualityScorer.GOOD_LENGTH:
            return 0.4
        elif length < QualityScorer.GREAT_LENGTH:
            return 0.6
        else:
            return 0.75
