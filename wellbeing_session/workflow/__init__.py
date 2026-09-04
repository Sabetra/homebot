"""
Workflow package -- LangGraph-based session orchestration (SOTA).

✅ Phase 9:  LangGraph-inspired state machine (session_graph.py) -- legacy
✅ Phase 9b: Real LangGraph StateGraph (langgraph_real.py) -- SOTA
✅ Phase 9b: Recursive LLM summarizer (recursive_summarizer.py) -- SOTA
✅ Phase 9b: LangChain adapter (langchain_adapter.py) -- SOTA
"""

# ── Legacy LangGraph-inspired graph (always available) ──
from .session_graph import (
    SessionState,
    SessionWorkflowGraph,
    GraphNode,
    ConditionalEdge,
    NodeStatus,
    NodeFunc,
    AsyncNodeFunc,
    # Pre-built nodes
    validate_input_node,
    analyze_emotion_node,
    crisis_router,
    build_context_node,
    generate_response_node,
    enhance_response_node,
    record_messages_node,
    # Factory
    build_default_session_graph,
)

# ── Real LangGraph (requires langgraph + langchain-core) ──
try:
    from .langgraph_real import (
        PsychSessionState,
        build_langgraph_session_graph,
        LANGGRAPH_AVAILABLE,
        get_dependency_registry,
    )
except ImportError:
    PsychSessionState = None  # type: ignore[assignment,misc]
    build_langgraph_session_graph = None  # type: ignore[assignment]
    LANGGRAPH_AVAILABLE = False
    get_dependency_registry = None  # type: ignore[assignment]

# ── LangChain adapter for local LLMs ──
try:
    from .langchain_adapter import LocalLlamaCppChat
    LANGCHAIN_ADAPTER_AVAILABLE = True
except ImportError:
    LocalLlamaCppChat = None  # type: ignore[assignment,misc]
    LANGCHAIN_ADAPTER_AVAILABLE = False

# ── Recursive LLM summarizer (always available, degrades gracefully) ──
from .recursive_summarizer import RecursiveLLMSummarizer, SalienceScorer

__all__ = [
    # Legacy
    "SessionState",
    "SessionWorkflowGraph",
    "GraphNode",
    "ConditionalEdge",
    "NodeStatus",
    "NodeFunc",
    "AsyncNodeFunc",
    "validate_input_node",
    "analyze_emotion_node",
    "crisis_router",
    "build_context_node",
    "generate_response_node",
    "enhance_response_node",
    "record_messages_node",
    "build_default_session_graph",
    # SOTA: Real LangGraph
    "PsychSessionState",
    "build_langgraph_session_graph",
    "LANGGRAPH_AVAILABLE",
    "get_dependency_registry",
    # SOTA: LangChain adapter
    "LocalLlamaCppChat",
    "LANGCHAIN_ADAPTER_AVAILABLE",
    # SOTA: Recursive summarizer
    "RecursiveLLMSummarizer",
    "SalienceScorer",
]
