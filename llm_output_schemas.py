"""
LLM Output Schemas - Pydantic V2 Models
========================================

Alle Pydantic Models für LLM Structured Outputs.
Jedes Schema definiert die erwartete Struktur eines LLM Responses.

Author: Roadmap Implementation Phase 1
Date: 2026-02-13
"""

from pydantic import BaseModel, Field, field_validator, model_validator
from typing import List, Dict, Any, Optional, Literal
from enum import Enum


# ============================================================================
# TOOL EXTRACTION SCHEMAS
# ============================================================================

class ToolCallSchema(BaseModel):
    """Schema für LLM-generierte Tool Calls"""
    
    tool: Literal[
        "create_diagram", 
        "canvas",
        "web_search", 
        "calculator", 
        "code_executor", 
        "file_reader", 
        "file_writer"
    ] = Field(description="Tool name (must be one of the listed tools)")
    
    parameters: Dict[str, Any] = Field(
        default_factory=dict,
        description="Tool parameters as key-value pairs"
    )
    
    reasoning: str = Field(
        description="Why is this tool needed for the query?"
    )
    
    confidence: float = Field(
        ge=0.0, 
        le=1.0, 
        default=1.0,
        description="Confidence score for this tool suggestion"
    )
    
    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "tool": "web_search",
                    "parameters": {"query": "Latest AI news"},
                    "reasoning": "User asked for current information",
                    "confidence": 0.95
                }
            ]
        }
    }


class ToolExtractionOutput(BaseModel):
    """Output des Tool-Extraction LLM Calls"""
    
    tools: List[ToolCallSchema] = Field(
        default_factory=list,
        description="List of suggested tools (empty if no tools needed)"
    )
    
    needs_tools: bool = Field(
        description="Whether any tools are needed for this query"
    )
    
    reasoning: str = Field(
        default="",
        description="Overall reasoning for tool selection"
    )
    
    @field_validator('tools')
    @classmethod
    def validate_tools(cls, v: List[ToolCallSchema]) -> List[ToolCallSchema]:
        """Filter out invalid tools (zusätzliche Sicherheit)"""
        if not v:
            return []
        
        valid_tools = {
            "create_diagram", "canvas", "web_search", "calculator",
            "code_executor", "file_reader", "file_writer"
        }
        
        return [t for t in v if t.tool in valid_tools]
    
    @model_validator(mode='after')
    def check_consistency(self):
        """Validiere Konsistenz zwischen needs_tools und tools list"""
        if self.needs_tools and not self.tools:
            raise ValueError("needs_tools is True but tools list is empty")
        if not self.needs_tools and self.tools:
            # Auto-correct: If tools exist, needs_tools should be True
            self.needs_tools = True
        return self


# ============================================================================
# EMOTIONAL ANALYSIS SCHEMAS
# ============================================================================

class EmotionType(str, Enum):
    """Standard emotion types"""
    JOY = "Freude"
    SADNESS = "Trauer"
    ANGER = "Wut"
    FEAR = "Angst"
    SURPRISE = "Überraschung"
    DISGUST = "Ekel"
    TRUST = "Vertrauen"
    ANTICIPATION = "Erwartung"
    ANXIETY = "Ängstlichkeit"
    HOPE = "Hoffnung"
    FRUSTRATION = "Frustration"
    CONTENTMENT = "Zufriedenheit"
    OTHER = "Andere"


class EmotionalMarker(BaseModel):
    """Emotional Marker aus User-Nachricht"""
    
    emotion: str = Field(
        description="Emotion name (z.B. Angst, Freude, Trauer)"
    )
    
    intensity: float = Field(
        ge=0.0, 
        le=1.0,
        description="Intensity of the emotion (0.0 = weak, 1.0 = strong)"
    )
    
    indicators: List[str] = Field(
        default_factory=list,
        description="Text indicators that suggest this emotion"
    )
    
    confidence: float = Field(
        ge=0.0, 
        le=1.0,
        description="Confidence in this emotion detection"
    )


class EmotionalAnalysisOutput(BaseModel):
    """Output des Emotional Analysis LLM Calls"""
    
    markers: List[EmotionalMarker] = Field(
        default_factory=list,
        description="List of detected emotional markers"
    )
    
    dominant_emotion: Optional[str] = Field(
        default=None,
        description="The most prominent emotion"
    )
    
    overall_valence: float = Field(
        ge=-1.0, 
        le=1.0, 
        default=0.0,
        description="Overall emotional valence (-1.0 = negative, 0.0 = neutral, 1.0 = positive)"
    )
    
    summary: str = Field(
        default="",
        description="Brief summary of emotional state"
    )


# ============================================================================
# INSIGHTS EXTRACTION SCHEMAS
# ============================================================================

class InsightCategory(str, Enum):
    """Categories for personality insights"""
    VALUES = "Werte"
    NEEDS = "Bedürfnisse"
    FEARS = "Ängste"
    GOALS = "Ziele"
    BELIEFS = "Überzeugungen"
    STRENGTHS = "Stärken"
    CHALLENGES = "Herausforderungen"
    PATTERNS = "Muster"
    RELATIONSHIPS = "Beziehungen"
    COPING = "Bewältigungsstrategien"
    OTHER = "Andere"


class PersonalityInsight(BaseModel):
    """Extrahiertes Personality Insight"""
    
    category: str = Field(
        description="Insight category (z.B. Werte, Bedürfnisse, Ängste)"
    )
    
    insight: str = Field(
        min_length=10,
        description="The insight text"
    )
    
    evidence: List[str] = Field(
        default_factory=list,
        description="Supporting evidence from conversation"
    )
    
    confidence: float = Field(
        ge=0.0, 
        le=1.0,
        description="Confidence in this insight"
    )
    
    session_ids: List[str] = Field(
        default_factory=list,
        description="Session IDs where this insight was observed"
    )
    
    importance: float = Field(
        ge=0.0, 
        le=1.0, 
        default=0.5,
        description="Importance/relevance of this insight"
    )


class InsightsExtractionOutput(BaseModel):
    """Output des Insights Extraction LLM Calls"""
    
    insights: List[PersonalityInsight] = Field(
        default_factory=list,
        description="List of extracted insights"
    )
    
    total_confidence: float = Field(
        ge=0.0, 
        le=1.0,
        description="Overall confidence in the extraction"
    )
    
    summary: str = Field(
        default="",
        description="Brief summary of key insights"
    )


# ============================================================================
# KNOWLEDGE GRAPH SCHEMAS
# ============================================================================

class KGTriple(BaseModel):
    """Knowledge Graph Triple (Subject-Predicate-Object)"""
    
    subject: str = Field(
        min_length=1,
        description="Subject entity"
    )
    
    predicate: str = Field(
        min_length=1,
        description="Relationship/predicate"
    )
    
    object: str = Field(
        min_length=1,
        description="Object entity",
        alias="object_"  # 'object' is reserved in Python
    )
    
    confidence: float = Field(
        ge=0.0, 
        le=1.0, 
        default=0.8,
        description="Confidence in this triple"
    )
    
    source: Optional[str] = Field(
        default=None,
        description="Source of this triple (e.g., session ID)"
    )
    
    model_config = {"populate_by_name": True}  # Allow both 'object' and 'object_'


class KGExtractionOutput(BaseModel):
    """Output des Knowledge Graph Extraction LLM Calls"""
    
    triples: List[KGTriple] = Field(
        default_factory=list,
        description="Extracted knowledge graph triples"
    )
    
    entities: List[str] = Field(
        default_factory=list,
        description="All unique entities mentioned"
    )
    
    summary: str = Field(
        default="",
        description="Summary of extracted knowledge"
    )


# ============================================================================
# DIAGRAM QUALITY SCHEMAS
# ============================================================================

class DiagramAspect(str, Enum):
    """Aspects of diagram quality"""
    SYNTAX = "Syntax"
    SEMANTICS = "Semantik"
    COMPLETENESS = "Vollständigkeit"
    CLARITY = "Klarheit"
    LAYOUT = "Layout"
    STYLE = "Stil"


class DiagramQualityScore(BaseModel):
    """Quality Score für einzelnen Aspekt"""
    
    aspect: str = Field(
        description="Quality aspect (z.B. Syntax, Semantik)"
    )
    
    score: float = Field(
        ge=0.0, 
        le=1.0,
        description="Score for this aspect (0.0 = poor, 1.0 = excellent)"
    )
    
    explanation: str = Field(
        description="Explanation of the score"
    )
    
    suggestions: List[str] = Field(
        default_factory=list,
        description="Improvement suggestions"
    )


class DiagramQualityOutput(BaseModel):
    """Output des Diagram Quality Assessment"""
    
    scores: List[DiagramQualityScore] = Field(
        description="Individual aspect scores"
    )
    
    overall_score: float = Field(
        ge=0.0, 
        le=1.0,
        description="Overall quality score"
    )
    
    is_valid: bool = Field(
        description="Whether the diagram is syntactically valid"
    )
    
    summary: str = Field(
        default="",
        description="Overall quality summary"
    )
    
    @property
    def calculated_overall(self) -> float:
        """Berechne Overall Score aus Individual Scores"""
        if not self.scores:
            return 0.0
        return sum(s.score for s in self.scores) / len(self.scores)
    
    @model_validator(mode='after')
    def validate_overall_score(self):
        """Validiere dass overall_score konsistent ist"""
        calculated = self.calculated_overall
        # Allow small deviation (0.05)
        if abs(self.overall_score - calculated) > 0.05 and self.scores:
            # Auto-correct to calculated value
            self.overall_score = calculated
        return self


class DiagramValidationOutput(BaseModel):
    """
    Output für Diagram Quality Validator (diagram_quality_validator.py).
    Entspricht dem erwarteten Format mit quality_score, issues, suggestions, is_acceptable.
    """
    
    quality_score: float = Field(
        ge=0.0,
        le=100.0,
        description="Overall quality score (0-100 scale)"
    )
    
    issues: List[str] = Field(
        default_factory=list,
        description="List of identified issues"
    )
    
    suggestions: List[str] = Field(
        default_factory=list,
        description="List of improvement suggestions"
    )
    
    is_acceptable: bool = Field(
        description="Whether the diagram is acceptable (quality_score >= 70)"
    )
    
    @model_validator(mode='after')
    def validate_acceptability(self):
        """Validiere dass is_acceptable konsistent mit quality_score ist"""
        expected_acceptable = self.quality_score >= 70.0
        if self.is_acceptable != expected_acceptable:
            # Auto-correct
            self.is_acceptable = expected_acceptable
        return self
    
    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "quality_score": 85.0,
                    "issues": ["Kleine Überlappungen"],
                    "suggestions": ["Abstand zwischen Nodes vergrößern"],
                    "is_acceptable": True
                }
            ]
        }
    }


# ============================================================================
# SESSION SUMMARY SCHEMAS
# ============================================================================

class SessionSentiment(str, Enum):
    """Session sentiment types"""
    POSITIVE = "positive"
    NEUTRAL = "neutral"
    NEGATIVE = "negative"
    MIXED = "mixed"


class SessionSummaryOutput(BaseModel):
    """Output des Session Summary LLM Calls"""
    
    summary: str = Field(
        min_length=50,
        description="Summary of the session"
    )
    
    key_topics: List[str] = Field(
        default_factory=list,
        description="Main topics discussed"
    )
    
    sentiment: SessionSentiment = Field(
        default=SessionSentiment.NEUTRAL,
        description="Overall session sentiment"
    )
    
    word_count: int = Field(
        ge=0,
        description="Approximate word count of summary"
    )
    
    highlights: List[str] = Field(
        default_factory=list,
        description="Key highlights from the session"
    )


# ============================================================================
# MOOD TRACKING SCHEMAS
# ============================================================================

class MoodType(str, Enum):
    """Standard mood types"""
    HAPPY = "Glücklich"
    SAD = "Traurig"
    ANXIOUS = "Ängstlich"
    CALM = "Ruhig"
    ENERGETIC = "Energiegeladen"
    TIRED = "Müde"
    STRESSED = "Gestresst"
    CONTENT = "Zufrieden"
    FRUSTRATED = "Frustriert"
    HOPEFUL = "Hoffnungsvoll"
    OTHER = "Andere"


class MoodIndicators(BaseModel):
    """Mood Indicators aus User-Nachricht"""
    
    mood: str = Field(
        description="Primary mood (z.B. Glücklich, Traurig)"
    )
    
    energy_level: float = Field(
        ge=0.0, 
        le=1.0,
        description="Energy level (0.0 = very low, 1.0 = very high)"
    )
    
    stress_level: float = Field(
        ge=0.0, 
        le=1.0,
        description="Stress level (0.0 = no stress, 1.0 = extreme stress)"
    )
    
    confidence_level: float = Field(
        ge=0.0, 
        le=1.0,
        description="Self-confidence level"
    )
    
    indicators: List[str] = Field(
        default_factory=list,
        description="Text indicators for this mood"
    )
    
    notes: str = Field(
        default="",
        description="Additional notes about the mood"
    )


# ============================================================================
# GENERIC JSON OUTPUT (FALLBACK)
# ============================================================================

class GenericJSONOutput(BaseModel):
    """Fallback für generische JSON Outputs"""
    
    data: Dict[str, Any] = Field(
        description="Generic data dictionary"
    )
    
    success: bool = Field(
        default=True,
        description="Whether the operation was successful"
    )
    
    error: Optional[str] = Field(
        default=None,
        description="Error message if success=False"
    )
    
    metadata: Dict[str, Any] = Field(
        default_factory=dict,
        description="Additional metadata"
    )


# ============================================================================
# EXPORT ALL SCHEMAS
# ============================================================================

__all__ = [
    # Tool Extraction
    "ToolCallSchema",
    "ToolExtractionOutput",
    
    # Emotional Analysis
    "EmotionType",
    "EmotionalMarker",
    "EmotionalAnalysisOutput",
    
    # Insights
    "InsightCategory",
    "PersonalityInsight",
    "InsightsExtractionOutput",
    
    # Knowledge Graph
    "KGTriple",
    "KGExtractionOutput",
    
    # Diagram Quality
    "DiagramAspect",
    "DiagramQualityScore",
    "DiagramQualityOutput",
    "DiagramValidationOutput",
    
    # Session Summary
    "SessionSentiment",
    "SessionSummaryOutput",
    
    # Mood Tracking
    "MoodType",
    "MoodIndicators",
    
    # Generic
    "GenericJSONOutput",
]


if __name__ == "__main__":
    # Quick validation test
    print("🧪 Testing LLM Output Schemas...")
    
    # Test Tool Extraction
    tool_output = ToolExtractionOutput(
        tools=[
            ToolCallSchema(
                tool="web_search",
                parameters={"query": "test"},
                reasoning="Testing",
                confidence=0.9
            )
        ],
        needs_tools=True,
        reasoning="Test reasoning"
    )
    print(f"✅ ToolExtractionOutput: {tool_output.model_dump_json()}")
    
    # Test Emotional Analysis
    emotion_output = EmotionalAnalysisOutput(
        markers=[
            EmotionalMarker(
                emotion="Freude",
                intensity=0.8,
                indicators=["lächeln", "glücklich"],
                confidence=0.9
            )
        ],
        dominant_emotion="Freude",
        overall_valence=0.8
    )
    print(f"✅ EmotionalAnalysisOutput validated")
    
    # Test KG Triple
    kg_output = KGExtractionOutput(
        triples=[
            KGTriple(
                subject="User",
                predicate="likes",
                object_="AI",
                confidence=0.9
            )
        ],
        entities=["User", "AI"]
    )
    print(f"✅ KGExtractionOutput validated")
    
    print("\n🎉 All schemas validated successfully!")
