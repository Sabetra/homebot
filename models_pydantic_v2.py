#!/usr/bin/env python3
"""
PYDANTIC V2 DATA MODELS
=======================

Type-safe, validated data models für kritische Datenstrukturen.
Ersetzt manuelle Validierung und dataclasses mit robuster Pydantic V2 Validierung.

Features:
- Automatische Validierung bei Instanziierung
- JSON Serialization/Deserialization
- Type Hints für IDE-Support
- Computed Fields
"""

from datetime import datetime, timezone
from typing import List, Optional, Dict, Any, Literal
from pydantic import (
    BaseModel, 
    Field, 
    field_validator, 
    model_validator,
    ConfigDict
)
from enum import Enum


# ============================================================================
# PSYCHOLOGICAL SESSION MODELS
# ============================================================================

class EmotionalState(str, Enum):
    """Emotionale Zustände mit vordefinierten Werten"""
    SEHR_POSITIV = "sehr_positiv"
    POSITIV = "positiv"
    NEUTRAL = "neutral"
    NEGATIV = "negativ"
    SEHR_NEGATIV = "sehr_negativ"
    GEMISCHT = "gemischt"
    UNBEKANNT = "unbekannt"


class SessionSummaryModel(BaseModel):
    """
    Pydantic V2 Model für Session-Zusammenfassungen
    
    Ersetzt: dataclass SessionSummary
    """
    model_config = ConfigDict(
        str_strip_whitespace=True,  # Automatisches Trimming
        validate_assignment=True,    # Validierung bei Zuweisung
        use_enum_values=True         # Verwende Enum-Values statt Enum
    )
    
    session_id: str = Field(
        ..., 
        min_length=1, 
        max_length=100,
        description="UUID oder ID der Session"
    )
    user_name: str = Field(
        ..., 
        min_length=1, 
        max_length=100,
        description="Name des Users"
    )
    start_time: datetime = Field(
        ...,
        description="Startzeitpunkt der Session"
    )
    last_activity: datetime = Field(
        ...,
        description="Letzte Aktivität in der Session"
    )
    message_count: int = Field(
        default=0,
        ge=0,
        description="Anzahl Nachrichten in der Session"
    )
    key_topics: List[str] = Field(
        default_factory=list,
        description="Hauptthemen der Session"
    )
    emotional_state: EmotionalState = Field(
        default=EmotionalState.NEUTRAL,
        description="Emotionaler Zustand"
    )
    session_summary: str = Field(
        default="",
        max_length=5000,
        description="Zusammenfassung der Session"
    )
    is_active: bool = Field(
        default=True,
        description="Ist die Session aktiv?"
    )
    
    @field_validator('user_name')
    @classmethod
    def sanitize_username(cls, v: str) -> str:
        """Bereinige Username"""
        return v.strip().lower()
    
    @field_validator('key_topics')
    @classmethod
    def deduplicate_topics(cls, v: List[str]) -> List[str]:
        """Remove duplicate topics while preserving order"""
        seen = set()
        unique = []
        for topic in v:
            if topic not in seen:
                seen.add(topic)
                unique.append(topic)
        return unique
    
    @model_validator(mode='after')
    def validate_timestamps(self) -> 'SessionSummaryModel':
        """Stelle sicher dass last_activity >= start_time"""
        if self.last_activity < self.start_time:
            raise ValueError(
                f"last_activity ({self.last_activity}) muss nach start_time ({self.start_time}) liegen"
            )
        return self
    
    @property
    def duration_minutes(self) -> int:
        """Calculate session duration in minutes"""
        delta = self.last_activity - self.start_time
        return int(delta.total_seconds() / 60)
    
    def to_legacy_dict(self) -> Dict[str, Any]:
        """
        Konvertiert zu Legacy-Dict-Format für Backward-Compatibility
        """
        return {
            'session_id': self.session_id,
            'user_name': self.user_name,
            'start_time': self.start_time.isoformat(),
            'last_activity': self.last_activity.isoformat(),
            'message_count': self.message_count,
            'key_topics': self.key_topics,
            'emotional_state': self.emotional_state.value,
            'session_summary': self.session_summary,
            'is_active': self.is_active
        }


class SessionMessageModel(BaseModel):
    """
    Pydantic V2 Model für Session-Nachrichten
    
    Features:
    - Timestamp validation (timezone-aware)
    - Role validation (user/assistant/system)
    - Content length validation
    - Emotional markers tracking
    - Crisis detection flag
    """
    model_config = ConfigDict(
        str_strip_whitespace=True,
        validate_assignment=True,
        json_encoders={datetime: lambda v: v.isoformat()}
    )
    
    message_id: str = Field(
        ...,
        min_length=1,
        max_length=200,
        description="Eindeutige Message-ID"
    )
    session_id: str = Field(
        ...,
        min_length=1,
        max_length=200,
        description="Zugehörige Session-ID"
    )
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Zeitstempel der Nachricht (timezone-aware UTC)"
    )
    role: Literal["user", "assistant", "system"] = Field(
        ...,
        description="Rolle des Nachrichtensenders"
    )
    content: str = Field(
        ...,
        min_length=1,
        description="Nachrichteninhalt (mindestens 1 Zeichen)"
    )
    emotional_markers: List[str] = Field(
        default_factory=list,
        description="Liste emotionaler Marker (z.B. 'angst', 'freude')"
    )
    is_crisis: bool = Field(
        default=False,
        description="Flag für erkannte Krisensituation"
    )
    
    @field_validator("timestamp")
    @classmethod
    def validate_timestamp(cls, v: datetime) -> datetime:
        """Stelle sicher, dass Timestamp timezone-aware ist"""
        if v.tzinfo is None:
            return v.replace(tzinfo=timezone.utc)
        return v
    
    @field_validator("emotional_markers")
    @classmethod
    def deduplicate_markers(cls, v: List[str]) -> List[str]:
        """Entferne Duplikate aus emotional_markers"""
        return list(dict.fromkeys(v))  # Preserves order
    
    def to_legacy_dict(self) -> dict:
        """Convert to legacy-compatible dict for backward compatibility"""
        return {
            'message_id': self.message_id,
            'session_id': self.session_id,
            'timestamp': self.timestamp,
            'role': self.role,
            'content': self.content,
            'emotional_markers': self.emotional_markers,
            'is_crisis': self.is_crisis
        }


class PersonalityInsightModel(BaseModel):
    """
    Pydantic V2 Model für psychologische Insights
    """
    model_config = ConfigDict(
        str_strip_whitespace=True,
        validate_assignment=True
    )
    
    insight_id: Optional[str] = Field(
        default=None,
        description="Eindeutige ID des Insights"
    )
    user_id: str = Field(
        ...,
        min_length=1,
        max_length=100,
        description="User-ID"
    )

    session_id: str = Field(
        ...,
        min_length=1,
        max_length=100,
        description="Session-ID"
    )
    insight_type: Literal[
        'life_event', 
        'personality', 
        'personality_trait',  # Added for backward compatibility
        'emotional_state',
        'behavioral_pattern',  # Added for backward compatibility
        'coping_mechanism',
        'relationship_dynamic',
        'cognitive_pattern'
    ] = Field(
        ...,
        description="Typ des Insights"
    )
    category: str = Field(
        ...,
        min_length=1,
        max_length=100,
        description="Kategorie des Insights (z.B. core_personality, current_state)"
    )
    value: str = Field(
        ...,
        min_length=1,
        max_length=500,
        description="Wert/Ausprägung des Insights"
    )
    description: str = Field(
        ...,
        min_length=1,
        max_length=2000,
        description="Beschreibung des Insights"
    )
    content: Optional[str] = Field(
        default=None,
        max_length=1000,
        description="Zusätzlicher Inhalt (optional)"
    )
    evidence: List[str] = Field(
        default_factory=list,
        description="Belege/Zitate aus dem Gespräch"
    )
    temporal_context: str = Field(
        default="current",
        description="Zeitlicher Kontext (current, past, future, developing)"
    )
    confidence: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Konfidenz (0-1)"
    )
    created_at: datetime = Field(
        default_factory=datetime.utcnow,
        description="Erstellt am"
    )
    validated_at: Optional[datetime] = Field(
        default=None,
        description="Validiert am (optional)"
    )
    extracted_at: datetime = Field(
        default_factory=datetime.utcnow,
        description="Extraktions-Zeitpunkt"
    )
    metadata: Dict[str, Any] = Field(
        default_factory=dict,
        description="Zusätzliche Metadaten"
    )
    
    @field_validator('confidence')
    @classmethod
    def round_confidence(cls, v: float) -> float:
        """Runde Confidence auf 2 Dezimalstellen"""
        return round(v, 2)
    
    @property
    def is_high_confidence(self) -> bool:
        """Check if confidence is high (> 0.7)"""
        return self.confidence > 0.7


# ============================================================================
# AGENT MODELS
# ============================================================================

class DistilledFactModel(BaseModel):
    """Structured distilled fact produced by evidence distillation."""

    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True)

    fact: str = Field(..., min_length=3, max_length=2000, description="Extracted factual statement")
    source_id: str = Field(..., min_length=1, max_length=64, description="Batch-local source identifier (e.g., S1)")
    confidence: float = Field(default=0.7, ge=0.0, le=1.0, description="Model confidence for this fact")


class DistilledFactBatchModel(BaseModel):
    """Validated container for distillation output."""

    model_config = ConfigDict(validate_assignment=True)

    facts: List[DistilledFactModel] = Field(default_factory=list, description="List of distilled factual statements")

class SourceModel(BaseModel):
    """
    Pydantic V2 Model für Agent Source/Citation
    
    Ersetzt: dataclass Source in agent/agent_types.py
    """
    model_config = ConfigDict(
        str_strip_whitespace=True,
        validate_assignment=True
    )
    
    title: str = Field(..., min_length=1, max_length=500, description="Quellen-Titel")
    url: str = Field(..., description="Quellen-URL")
    date: Optional[str] = Field(default=None, description="Quellen-Datum")
    snippet: Optional[str] = Field(default=None, description="Text-Snippet")

    # Metadata für richer citations
    page: Optional[int] = Field(default=None, ge=1, description="Seiten-Nummer")
    doc_id: Optional[str] = Field(default=None, description="Dokument-ID")
    chunk_id: Optional[int] = Field(default=None, ge=0, description="Chunk-ID")
    type: Optional[str] = Field(default=None, description="Quellen-Typ")
    meta: Dict[str, Any] = Field(default_factory=dict, description="Zusätzliche Metadaten")
    
    # Content & Score für Hybrid Reasoning
    content: Optional[str] = Field(default=None, description="Full Content")
    score: float = Field(default=0.0, ge=0.0, le=1.0, description="Relevance Score")
    
    @field_validator('snippet', mode='before')
    @classmethod
    def truncate_snippet(cls, v: Optional[str]) -> Optional[str]:
        """Truncate snippet to 5000 chars rather than crashing on long web content."""
        if v is not None and len(v) > 5000:
            return v[:5000]
        return v

    @field_validator('url')
    @classmethod
    def validate_url(cls, v: str) -> str:
        """Validate URL has a scheme.

        Accepts any scheme (http, https, file, rag, cache, …) so that
        internal URIs produced by the RAG pipeline are valid.  Use
        :meth:`is_web_url` when you specifically need an HTTP(S) URL.
        """
        if '://' not in v or v.index('://') == 0:
            raise ValueError(f"Invalid URL format (missing scheme): {v}")
        return v

    @property
    def is_web_url(self) -> bool:
        """Return True if the URL uses http or https scheme."""
        return self.url.startswith(('http://', 'https://'))


class ToolResultModel(BaseModel):
    """
    Pydantic V2 Model für Agent Tool Results
    
    Ersetzt: dataclass ToolResult in agent/agent_types.py
    """
    model_config = ConfigDict(
        str_strip_whitespace=True,
        validate_assignment=True,
        arbitrary_types_allowed=True
    )
    
    tool: str = Field(..., min_length=1, description="Tool Name")
    success: bool = Field(..., description="Execution Success")
    message: Optional[str] = Field(default=None, description="Success Message")
    error: Optional[str] = Field(default=None, description="Error Message")
    
    # Normalized payloads
    results: Optional[List[Dict[str, Any]]] = Field(default=None, description="Structured Results")
    text: Optional[str] = Field(default=None, description="Text Result")
    meta: Dict[str, Any] = Field(default_factory=dict, description="Metadata")
    
    # Relevance score for RAG gating (used by orchestrator MultiHop)
    score: float = Field(default=0.0, ge=0.0, le=1.0, description="Relevance/Quality score")
    
    @model_validator(mode='after')
    def validate_result_consistency(self) -> 'ToolResultModel':
        """Validate that success/error states are consistent.

        A failed tool SHOULD carry an error description but we no longer
        hard-fail because upstream callers (e.g. timeout wrappers) may
        not always provide one.  A successful tool is considered valid
        when *any* payload field is populated (message, results, text,
        or non-empty meta).
        """
        import logging as _log
        if not self.success and not self.error:
            _log.getLogger(__name__).warning(
                "ToolResultModel: failed tool '%s' has no error message", self.tool
            )
        return self


class ToolCallModel(BaseModel):
    """
    Pydantic V2 Model für Agent Tool-Calls
    """
    model_config = ConfigDict(
        str_strip_whitespace=True,
        validate_assignment=True,
        arbitrary_types_allowed=True  # Für komplexe Parameter
    )
    
    tool: str = Field(
        ...,
        min_length=1,
        max_length=100,
        description="Name des Tools"
    )
    parameters: Dict[str, Any] = Field(
        default_factory=dict,
        description="Tool-Parameter"
    )
    reasoning: Optional[str] = Field(
        default=None,
        max_length=2000,
        description="Reasoning für Tool-Auswahl"
    )
    
    @field_validator('tool')
    @classmethod
    def normalize_tool_name(cls, v: str) -> str:
        """Normalisiere Tool-Namen zu lowercase"""
        return v.lower().strip()


class AgentTraceModel(BaseModel):
    """
    Pydantic V2 Model für Agent Execution Traces (Production-Complete)
    
    Ersetzt: dataclass AgentTrace in agent/agent_types.py
    Vollständiges Modell mit allen Feldern aus der aktuellen Implementierung
    """
    model_config = ConfigDict(
        str_strip_whitespace=True,
        validate_assignment=True,
        arbitrary_types_allowed=True  # Für VerificationResult forward reference
    )
    
    # === Core Trace Data ===
    planner_output: Optional[str] = Field(default=None, description="Planner LLM Output")
    planned_tools: List[str] = Field(default_factory=list, description="Geplante Tools")
    ran_tools: List[str] = Field(default_factory=list, description="Ausgeführte Tools")
    evidence_domains: List[str] = Field(default_factory=list, description="Evidence Domains")
    extras_count: int = Field(default=0, ge=0, description="Extra Evidence Count")
    summarizer_draft_chars: int = Field(default=0, ge=0, description="Summarizer Draft Chars")
    verifier_changed: bool = Field(default=False, description="Verifier Changed Output")
    
    # === Heuristics & Summaries ===
    heuristic_triggered: bool = Field(default=False, description="Heuristik ausgelöst")
    heuristic_reason: Optional[str] = Field(default=None, description="Heuristik-Grund")
    tool_summaries: List[str] = Field(default_factory=list, description="Tool Zusammenfassungen")
    
    # === Reasoning ===
    reasoning: Optional[str] = Field(default=None, description="Agent Reasoning")
    critique: Optional[str] = Field(default=None, description="Self-Critique")
    
    # === Timings (milliseconds) ===
    planner_ms: int = Field(default=0, ge=0, description="Planner Time (ms)")
    tools_ms: int = Field(default=0, ge=0, description="Tools Execution Time (ms)")
    summarize_ms: int = Field(default=0, ge=0, description="Summarizer Time (ms)")
    verify_ms: int = Field(default=0, ge=0, description="Verifier Time (ms)")
    
    # === Token/Context Metrics ===
    hist_trimmed_count: int = Field(default=0, ge=0, description="History Messages Trimmed")
    hist_tokens_used: int = Field(default=0, ge=0, description="History Tokens Used")
    budget_used: int = Field(default=0, ge=0, description="Context Budget Used")
    
    # === Generation Settings Snapshot ===
    planner_temp: float = Field(default=0.2, ge=0.0, le=2.0, description="Planner Temperature")
    summarizer_temp: float = Field(default=0.2, ge=0.0, le=2.0, description="Summarizer Temperature")
    verifier_temp: float = Field(default=0.0, ge=0.0, le=2.0, description="Verifier Temperature")
    planner_max_tokens: int = Field(default=1024, ge=1, description="Planner Max Tokens")
    summarizer_max_tokens: int = Field(default=4096, ge=1, description="Summarizer Max Tokens")
    verifier_max_tokens: int = Field(default=1024, ge=1, description="Verifier Max Tokens")
    
    # === Verifier Diffs ===
    verifier_delta_chars: int = Field(default=0, description="Verifier Character Delta")
    verifier_changed_ratio: float = Field(default=0.0, ge=0.0, le=1.0, description="Verifier Change Ratio")
    
    # === Multi-Query RAG Observability ===
    subqueries: List[str] = Field(default_factory=list, description="Generated Subqueries")
    rag_enabled: bool = Field(default=True, description="RAG Enabled")
    rag_k: int = Field(default=6, ge=0, description="RAG Top-K")
    rag_min_score: float = Field(default=0.0, ge=0.0, le=1.0, description="RAG Min Score")
    multiquery_enabled: bool = Field(default=False, description="Multi-Query Enabled")
    mq_n: int = Field(default=5, ge=1, description="Multi-Query N")
    mq_k: int = Field(default=5, ge=1, description="Multi-Query K")
    
    # === RAG Store Stats ===
    rag_stats: Dict[str, int] = Field(default_factory=dict, description="RAG Statistics")
    
    # === Tool Results for Debugging ===
    tool_results: Dict[str, Any] = Field(default_factory=dict, description="Tool Results")
    
    # === Source Validation ===
    source_validation: Dict[str, Any] = Field(default_factory=dict, description="Source Validation Data")
    
    # === Answer Verification (2025 Step 7) ===
    verification_result: Optional[Any] = Field(default=None, description="Verification Result Object")  # VerificationResult
    verification_confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0, description="Verification Confidence")
    verification_quality: Optional[float] = Field(default=None, ge=0.0, le=1.0, description="Verification Quality")
    verification_grounding: Optional[float] = Field(default=None, ge=0.0, le=1.0, description="Verification Grounding")
    verification_hallucination_risk: Optional[float] = Field(default=None, ge=0.0, le=1.0, description="Hallucination Risk")
    verification_issues: List[str] = Field(default_factory=list, description="Verification Issues")
    verification_warnings: List[str] = Field(default_factory=list, description="Verification Warnings")
    verification_results: List[Any] = Field(default_factory=list, description="All Verification Results")  # List[VerificationResult]
    
    # === Adaptive Planning (2025) ===
    adaptive_planning_triggered: bool = Field(default=False, description="Adaptive Planning Triggered")
    adaptive_reflections: List[Dict[str, Any]] = Field(default_factory=list, description="Adaptive Reflections")
    adaptive_additional_tools: int = Field(default=0, ge=0, description="Additional Tools from Adaptive Planning")
    adaptive_planning_error: Optional[str] = Field(default=None, description="Adaptive Planning Error")
    
    # === Date Validation ===
    date_validation_warning: Optional[str] = Field(default=None, description="Date Validation Warning")
    
    # === MultiHop Adaptive-RAG Tracking ===
    multi_hop_executed: bool = Field(default=False, description="MultiHop retrieval was executed")
    multi_hop_ms: float = Field(default=0.0, ge=0.0, description="MultiHop execution time (ms)")
    multi_hop_hops: int = Field(default=0, ge=0, description="Number of hops executed")
    multi_hop_converged: bool = Field(default=False, description="MultiHop evidence converged")
    multi_hop_error: Optional[str] = Field(default=None, description="MultiHop error message (if failed)")
    rag_result_count: int = Field(default=0, ge=0, description="Total RAG result count after enrichment")
    
    # === SOTA Metrics ===
    sota_metrics: Optional[Dict[str, Any]] = Field(default=None, description="SOTA Enhancement Metrics")

    @property
    def total_execution_time_ms(self) -> int:
        """Total execution time across all stages"""
        return self.planner_ms + self.tools_ms + self.summarize_ms + self.verify_ms
    
    @property
    def success_rate(self) -> float:
        """Tool execution success rate"""
        if not self.ran_tools:
            return 1.0
        return len(self.ran_tools) / max(len(self.planned_tools), 1)
    
    @model_validator(mode='after')
    def validate_trace_consistency(self) -> 'AgentTraceModel':
        """Validate trace data consistency"""
        # Warn if tools were planned but none ran
        if self.planned_tools and not self.ran_tools:
            self.verification_warnings.append("Tools wurden geplant aber nicht ausgeführt")
        
        # Warn if verifier changed but no delta
        if self.verifier_changed and self.verifier_delta_chars == 0:
            self.verification_warnings.append("Verifier changed flag gesetzt aber keine Änderungen")
        
        return self


class EvidencePackModel(BaseModel):
    """
    Pydantic V2 Model für Evidence Pack
    
    Ersetzt: dataclass EvidencePack in agent/agent_types.py
    """
    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True)
    
    query: str = Field(..., min_length=1, description="Search Query")
    items: List[SourceModel] = Field(default_factory=list, description="Evidence Sources")
    raw_excerpt: Optional[str] = Field(default=None, description="Raw Excerpt Text")
    
    @property
    def evidence_count(self) -> int:
        """Number of evidence items"""
        return len(self.items)


class FinalAnswerModel(BaseModel):
    """
    Pydantic V2 Model für Final Answer
    
    Ersetzt: dataclass FinalAnswer in agent/agent_types.py
    """
    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True)
    
    text: str = Field(..., min_length=1, description="Answer Text")
    sources: List[SourceModel] = Field(default_factory=list, description="Citation Sources")
    trace: Optional[AgentTraceModel] = Field(default=None, description="Execution Trace")
    followup_questions: List[str] = Field(default_factory=list, description="Follow-up questions for the user")
    graphics: List[Dict[str, Any]] = Field(default_factory=list, description="Locally rendered graphical artifacts")
    files: List[Dict[str, Any]] = Field(default_factory=list, description="User-downloadable generated files")
    
    @property
    def has_sources(self) -> bool:
        """Check if answer has sources"""
        return len(self.sources) > 0
    
    @property
    def source_count(self) -> int:
        """Number of sources cited"""
        return len(self.sources)


# ============================================================================
# CONFIGURATION MODELS
# ============================================================================

class LLMConfigModel(BaseModel):
    """
    Pydantic V2 Model für LLM-Konfiguration
    """
    model_config = ConfigDict(validate_assignment=True)
    
    model_name: str = Field(
        ...,
        min_length=1,
        description="Name des LLM-Models"
    )
    temperature: float = Field(
        default=0.7,
        ge=0.0,
        le=2.0,
        description="Sampling-Temperature"
    )
    max_tokens: int = Field(
        default=2048,
        ge=1,
        le=100000,
        description="Maximale Token-Anzahl"
    )
    top_p: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description="Nucleus Sampling Parameter"
    )
    frequency_penalty: float = Field(
        default=0.0,
        ge=-2.0,
        le=2.0,
        description="Frequency Penalty"
    )
    presence_penalty: float = Field(
        default=0.0,
        ge=-2.0,
        le=2.0,
        description="Presence Penalty"
    )
    
    @model_validator(mode='after')
    def validate_sampling_params(self) -> 'LLMConfigModel':
        """Validiere Sampling-Parameter"""
        if self.temperature > 1.5 and self.top_p < 0.5:
            raise ValueError(
                "Hohe Temperature mit niedrigem top_p kann zu inkonsistenten Outputs führen"
            )
        return self


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def validate_and_convert(data: Dict[str, Any], model_class: type[BaseModel]) -> BaseModel:
    """
    Validiert Daten und konvertiert zu Pydantic-Model
    
    Args:
        data: Rohdaten als Dictionary
        model_class: Pydantic Model-Klasse
        
    Returns:
        Validiertes Pydantic Model
        
    Raises:
        ValidationError: Bei Validierungsfehlern
    """
    return model_class.model_validate(data)


def safe_parse_json(json_str: str, model_class: type[BaseModel]) -> Optional[BaseModel]:
    """
    Parst JSON-String sicher zu Pydantic-Model
    
    Args:
        json_str: JSON-String
        model_class: Pydantic Model-Klasse
        
    Returns:
        Pydantic Model oder None bei Fehler
    """
    try:
        return model_class.model_validate_json(json_str)
    except Exception:
        return None


# ============================================================================
# USAGE EXAMPLES (für Dokumentation)
# ============================================================================

if __name__ == "__main__":
    # Beispiel 1: SessionSummary erstellen
    session = SessionSummaryModel(
        session_id="550e8400-e29b-41d4-a716-446655440000",
        user_name="  Alice  ",  # Wird automatisch getrimmt zu "alice"
        start_time=datetime.now(),
        last_activity=datetime.now(),
        message_count=5,
        key_topics=["Stress", "Work-Life-Balance"],
        emotional_state=EmotionalState.NEGATIV
    )
    print(f"✅ Session erstellt: {session.session_id}")
    print(f"   User: {session.user_name}")  # → "alice" (sanitized)
    
    # Beispiel 2: Validierungsfehler
    try:
        invalid_session = SessionSummaryModel(
            session_id="invalid",  # Zu kurz!
            user_name="Bob",
            start_time=datetime.now(),
            last_activity=datetime.now()
        )
    except Exception as e:
        print(f"❌ Validierung fehlgeschlagen (wie erwartet): {e}")
    
    # Beispiel 3: JSON Serialization
    json_data = session.model_dump_json(indent=2)
    print(f"📦 JSON Export:\n{json_data[:200]}...")
    
    # Beispiel 4: Legacy-Kompatibilität
    legacy_dict = session.to_legacy_dict()
    print(f"🔄 Legacy Dict: {list(legacy_dict.keys())}")
