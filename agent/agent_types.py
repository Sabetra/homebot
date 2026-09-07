"""
Agent Type Definitions - PYDANTIC V2 MIGRATION (Phase 2)
=========================================================

This module provides data structures for the agent system.

🔄 MIGRATION STATUS:
- ✅ Pydantic V2 models available in models_pydantic_v2.py
- ✅ Backward compatibility maintained via aliases
- ✅ Gradual migration in progress (Phase 2 of 5)

USAGE:
------
# RECOMMENDED: Use Pydantic models (new code)
from agent.agent_types import ToolCall, Source, AgentTrace

# These are now Pydantic models with full validation!
# Legacy code continues to work without changes.

CoT Decision: Export Pydantic models as primary interface,
keep dataclass definitions as fallback.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, TYPE_CHECKING, Union

if TYPE_CHECKING:
    from agent.verification_manager import VerificationResult

# ============================================================================
# PRIMARY EXPORTS (Pydantic V2 Models)
# ============================================================================

try:
    from models_pydantic_v2 import (
        SourceModel as Source,
        ToolCallModel as ToolCall,
        ToolResultModel as ToolResult,
        EvidencePackModel as EvidencePack,
        AgentTraceModel as AgentTrace,
        FinalAnswerModel as FinalAnswer
    )
    _PYDANTIC_AVAILABLE = True
    __pydantic_migrated__ = True
    
except ImportError:
    # Fallback to dataclasses if Pydantic not available
    _PYDANTIC_AVAILABLE = False
    __pydantic_migrated__ = False
    
    # Define legacy dataclasses as fallback
    @dataclass
    class Source:  # type: ignore[no-redef]
        title: str
        url: str
        date: Optional[str] = None
        snippet: Optional[str] = None
        page: Optional[int] = None
        doc_id: Optional[str] = None
        chunk_id: Optional[int] = None
        type: Optional[str] = None
        meta: Dict[str, Any] = field(default_factory=dict)
        content: Optional[str] = None
        score: float = 0.0
        
    @dataclass
    class ToolCall:  # type: ignore[no-redef]
        tool: str
        parameters: Dict[str, Any]

    @dataclass
    class ToolResult:  # type: ignore[no-redef]
        tool: str
        success: bool
        message: Optional[str] = None
        error: Optional[str] = None
        results: Optional[List[Dict[str, Any]]] = None
        text: Optional[str] = None
        meta: Dict[str, Any] = field(default_factory=dict)

    @dataclass
    class EvidencePack:  # type: ignore[no-redef]
        """Aggregated evidence passed to LLM for summarization."""
        query: str
        items: List[Source] = field(default_factory=list)
        raw_excerpt: str | None = None

    @dataclass
    class AgentTrace:  # type: ignore[no-redef]
        """Minimal, structured trace for GUI display (no hidden chain-of-thought)."""
        planner_output: Optional[str] = None
        planned_tools: List[str] = field(default_factory=list)
        ran_tools: List[str] = field(default_factory=list)
        evidence_domains: List[str] = field(default_factory=list)
        extras_count: int = 0
        summarizer_draft_chars: int = 0
        verifier_changed: bool = False
        # --- New: heuristics & summaries ---
        heuristic_triggered: bool = False
        heuristic_reason: Optional[str] = None
        tool_summaries: List[str] = field(default_factory=list)
        # --- New: Reasoning ---
        reasoning: Optional[str] = None
        critique: Optional[str] = None
        # --- New: timings (milliseconds) ---
        planner_ms: int = 0
        tools_ms: int = 0
        summarize_ms: int = 0
        verify_ms: int = 0
        # --- New: token/context metrics ---
        hist_trimmed_count: int = 0
        hist_tokens_used: int = 0
        budget_used: int = 0
        # --- New: generation settings snapshot ---
        planner_temp: float = 0.2
        summarizer_temp: float = 0.2
        verifier_temp: float = 0.0
        planner_max_tokens: int = 1024
        summarizer_max_tokens: int = 4096
        verifier_max_tokens: int = 1024
        # --- New: verifier diffs ---
        verifier_delta_chars: int = 0
        verifier_changed_ratio: float = 0.0
        # --- New: Multi-Query RAG observability ---
        subqueries: List[str] = field(default_factory=list)
        rag_enabled: bool = True
        rag_k: int = 6
        rag_min_score: float = 0.0
        multiquery_enabled: bool = False
        mq_n: int = 5
        mq_k: int = 5
        # --- New: RAG store stats ---
        rag_stats: Dict[str, int] = field(default_factory=dict)
        # --- New: Tool results for debugging ---
        tool_results: Dict[str, Any] = field(default_factory=dict)
        # --- New: Source validation (2025) ---
        source_validation: Dict[str, Any] = field(default_factory=dict)
        # --- New: Answer verification (2025 Step 7) ---
        verification_result: Optional['VerificationResult'] = None
        verification_confidence: Optional[float] = None
        verification_quality: Optional[float] = None
        verification_grounding: Optional[float] = None
        verification_hallucination_risk: Optional[float] = None
        verification_issues: List[str] = field(default_factory=list)
        verification_warnings: List[str] = field(default_factory=list)
        # --- New: Verification results for Step 7 integration ---
        verification_results: List[VerificationResult] = field(default_factory=list)

        # --- New: Adaptive Planning (2025) ---
        adaptive_planning_triggered: bool = False
        adaptive_reflections: List[Dict[str, Any]] = field(default_factory=list)
        adaptive_additional_tools: int = 0
        adaptive_planning_error: Optional[str] = None
        
        # --- New: Date Validation (2025-10-11) ---
        date_validation_warning: Optional[str] = None

    @dataclass
    class FinalAnswer:  # type: ignore[no-redef]
        text: str
        sources: List[Source] = field(default_factory=list)
        trace: Optional[AgentTrace] = None
        followup_questions: List[str] = field(default_factory=list)
        graphics: List[Dict[str, Any]] = field(default_factory=list)
        files: List[Dict[str, Any]] = field(default_factory=list)


# ============================================================================
# MODULE EXPORTS
# ============================================================================

__all__ = [
    'Source', 'ToolCall', 'ToolResult', 
    'EvidencePack', 'AgentTrace', 'FinalAnswer'
]

# Migration status helpers
def is_pydantic_migrated() -> bool:
    """Check if module is using Pydantic models"""
    return __pydantic_migrated__

def get_model_type() -> str:
    """Get current model type (pydantic or dataclass)"""
    return "pydantic" if _PYDANTIC_AVAILABLE else "dataclass"
