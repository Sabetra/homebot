"""
Session Workflow Graph -- LangGraph-inspired state machine for session orchestration.

Implements a typed, directed acyclic graph (DAG) that models the complete
psychological session workflow as discrete nodes with typed state transitions.

This replaces ad-hoc if/else orchestration with a declarative, testable graph:

    START → validate_input → analyze_emotion → build_context → generate_response
         → enhance_response → record_messages → END

Each node is a pure-ish function: ``(SessionState) -> SessionState``.
Edges can be conditional (routing based on crisis detection, missing context, etc.).

Supports both sync and async execution modes.

✅ Phase 9: LangGraph-style state machine foundation.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import (
    Any,
    Callable,
    Dict,
    List,
    Optional,
    Sequence,
    Tuple,
    Union,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

class NodeStatus(Enum):
    """Execution status of a graph node."""
    PENDING = auto()
    RUNNING = auto()
    SUCCESS = auto()
    FAILED = auto()
    SKIPPED = auto()


@dataclass
class SessionState:
    """Typed, immutable-ish state object flowing through the graph.

    Every node reads from and writes to this state.  The graph runner
    creates a fresh copy per invocation to avoid cross-request leakage.
    """

    # -- Input --
    user_input: str = ""
    session_id: str = ""
    user_id: str = ""
    wellbeing_enabled: bool = True

    # -- Emotional analysis --
    emotional_markers: List[str] = field(default_factory=list)
    is_crisis: bool = False
    dominant_emotion: str = "neutral"
    emotion_confidence: float = 0.0

    # -- Context --
    comprehensive_context: Dict[str, Any] = field(default_factory=dict)
    formatted_context: str = ""
    context_token_estimate: int = 0
    session_messages: List[Dict[str, Any]] = field(default_factory=list)

    # -- Response --
    ai_response: str = ""
    enhanced_response: str = ""

    # -- Metadata --
    node_trace: List[str] = field(default_factory=list)
    node_timings: Dict[str, float] = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)
    is_valid: bool = True


# ---------------------------------------------------------------------------
# Node type
# ---------------------------------------------------------------------------

# A node is a callable that takes state and returns state.
NodeFunc = Callable[[SessionState], SessionState]
AsyncNodeFunc = Callable[[SessionState], Any]  # returns Awaitable[SessionState]


@dataclass
class GraphNode:
    """A node in the session workflow graph."""
    name: str
    func: Union[NodeFunc, AsyncNodeFunc]
    is_async: bool = False
    status: NodeStatus = NodeStatus.PENDING


@dataclass
class ConditionalEdge:
    """A conditional edge: routes to different next nodes based on state."""
    source: str
    condition: Callable[[SessionState], str]  # returns next node name
    targets: Dict[str, str]  # condition_result → target_node_name


# ---------------------------------------------------------------------------
# Graph
# ---------------------------------------------------------------------------

class SessionWorkflowGraph:
    """Directed graph of nodes that process ``SessionState``.

    Usage::

        graph = SessionWorkflowGraph()
        graph.add_node("validate", validate_input_node)
        graph.add_node("analyze", analyze_emotion_node)
        graph.add_edge("validate", "analyze")
        graph.add_conditional_edge("analyze", crisis_router, {
            "crisis": "crisis_response",
            "normal": "build_context",
        })
        graph.set_entry("validate")

        result = graph.run(SessionState(user_input="Ich fühle mich traurig"))
    """

    def __init__(self, name: str = "session_workflow") -> None:
        self.name = name
        self._nodes: Dict[str, GraphNode] = {}
        self._edges: Dict[str, List[str]] = {}  # source → [targets]
        self._conditional_edges: Dict[str, ConditionalEdge] = {}
        self._entry: Optional[str] = None
        self._end_nodes: set[str] = set()

    # ------------------------------------------------------------------
    # Graph construction API
    # ------------------------------------------------------------------

    def add_node(
        self,
        name: str,
        func: Union[NodeFunc, AsyncNodeFunc],
        is_async: bool = False,
    ) -> "SessionWorkflowGraph":
        """Add a processing node."""
        self._nodes[name] = GraphNode(name=name, func=func, is_async=is_async)
        if name not in self._edges:
            self._edges[name] = []
        return self

    def add_edge(self, source: str, target: str) -> "SessionWorkflowGraph":
        """Add a direct edge from source to target."""
        if source not in self._edges:
            self._edges[source] = []
        self._edges[source].append(target)
        return self

    def add_conditional_edge(
        self,
        source: str,
        condition: Callable[[SessionState], str],
        targets: Dict[str, str],
    ) -> "SessionWorkflowGraph":
        """Add a conditional edge from source."""
        self._conditional_edges[source] = ConditionalEdge(
            source=source, condition=condition, targets=targets,
        )
        return self

    def set_entry(self, node_name: str) -> "SessionWorkflowGraph":
        """Set the entry (start) node."""
        self._entry = node_name
        return self

    def set_end(self, *node_names: str) -> "SessionWorkflowGraph":
        """Mark one or more nodes as terminal (end) nodes."""
        self._end_nodes.update(node_names)
        return self

    # ------------------------------------------------------------------
    # Sync execution
    # ------------------------------------------------------------------

    def run(self, state: SessionState) -> SessionState:
        """Execute the graph synchronously.

        Traverses nodes from entry to end, applying each node function
        to the state.  Handles conditional routing automatically.

        Args:
            state: Initial session state.

        Returns:
            Final session state after all nodes have executed.
        """
        if self._entry is None:
            raise ValueError("No entry node set -- call set_entry() first")

        current = self._entry
        visited: set[str] = set()

        while current and current not in visited:
            visited.add(current)
            node = self._nodes.get(current)
            if node is None:
                state.errors.append(f"Node '{current}' not found")
                break

            # Execute node
            t0 = time.perf_counter()
            try:
                node.status = NodeStatus.RUNNING
                state = node.func(state)
                node.status = NodeStatus.SUCCESS
            except Exception as exc:
                node.status = NodeStatus.FAILED
                state.errors.append(f"{current}: {exc}")
                logger.error("Graph node '%s' failed: %s", current, exc)
                break
            finally:
                elapsed = (time.perf_counter() - t0) * 1000
                state.node_trace.append(current)
                state.node_timings[current] = elapsed

            # Terminal node?
            if current in self._end_nodes:
                break

            # Determine next node
            current = self._resolve_next(current, state) or ""

        return state

    # ------------------------------------------------------------------

    async def run_async(self, state: SessionState) -> SessionState:
        """Execute the graph asynchronously.

        Supports both sync and async node functions.

        Args:
            state: Initial session state.

        Returns:
            Final session state.
        """
        import asyncio

        if self._entry is None:
            raise ValueError("No entry node set -- call set_entry() first")

        current: Optional[str] = self._entry
        visited: set[str] = set()

        while current and current not in visited:
            visited.add(current)
            node = self._nodes.get(current)
            if node is None:
                state.errors.append(f"Node '{current}' not found")
                break

            t0 = time.perf_counter()
            try:
                node.status = NodeStatus.RUNNING
                if node.is_async:
                    result = await node.func(state)  # type: ignore[misc]
                    if isinstance(result, SessionState):
                        state = result
                else:
                    state = node.func(state)
                node.status = NodeStatus.SUCCESS
            except Exception as exc:
                node.status = NodeStatus.FAILED
                state.errors.append(f"{current}: {exc}")
                logger.error("Async graph node '%s' failed: %s", current, exc)
                break
            finally:
                elapsed = (time.perf_counter() - t0) * 1000
                state.node_trace.append(current)
                state.node_timings[current] = elapsed

            if current in self._end_nodes:
                break

            current = self._resolve_next(current, state) or ""

        return state

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    @property
    def node_names(self) -> List[str]:
        """Return all registered node names."""
        return list(self._nodes.keys())

    @property
    def edge_count(self) -> int:
        """Total number of edges (direct + conditional)."""
        direct = sum(len(targets) for targets in self._edges.values())
        return direct + len(self._conditional_edges)

    def get_node_status(self, name: str) -> Optional[NodeStatus]:
        """Return execution status of a node."""
        node = self._nodes.get(name)
        return node.status if node else None

    def to_mermaid(self) -> str:
        """Generate a Mermaid graph diagram."""
        lines = ["graph LR"]
        for src, targets in self._edges.items():
            for tgt in targets:
                lines.append(f"    {src} --> {tgt}")
        for src, cedge in self._conditional_edges.items():
            for label, tgt in cedge.targets.items():
                lines.append(f"    {src} -->|{label}| {tgt}")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _resolve_next(self, current: str, state: SessionState) -> Optional[str]:
        """Resolve the next node from edges/conditional edges."""
        # Conditional edge first (takes priority)
        if current in self._conditional_edges:
            cedge = self._conditional_edges[current]
            result = cedge.condition(state)
            next_node = cedge.targets.get(result)
            if next_node:
                return next_node
            logger.warning(
                "Conditional edge from '%s' returned '%s' -- no target mapped",
                current, result,
            )

        # Direct edges
        targets = self._edges.get(current, [])
        if targets:
            return targets[0]  # First direct edge

        return None  # No more nodes -- end of graph


# ---------------------------------------------------------------------------
# Pre-built Node Functions (reusable building blocks)
# ---------------------------------------------------------------------------

def validate_input_node(state: SessionState) -> SessionState:
    """Validate user input is non-empty and session is active."""
    if not state.user_input or not state.user_input.strip():
        state.is_valid = False
        state.errors.append("Empty user input")
    if not state.session_id:
        state.is_valid = False
        state.errors.append("No active session")
    return state


def analyze_emotion_node(state: SessionState) -> SessionState:
    """Placeholder emotion analysis node (override with real analyzer)."""
    # Default: neutral -- concrete implementations inject real analyzer
    if not state.is_valid:
        return state
    state.dominant_emotion = "neutral"
    state.emotion_confidence = 0.5
    state.emotional_markers = ["neutral"]
    return state


def crisis_router(state: SessionState) -> str:
    """Route based on crisis detection."""
    return "crisis" if state.is_crisis else "normal"


def build_context_node(state: SessionState) -> SessionState:
    """Placeholder context building node."""
    if not state.is_valid:
        return state
    # In real use, this calls the context builder
    state.context_token_estimate = len(state.formatted_context) // 4
    return state


def generate_response_node(state: SessionState) -> SessionState:
    """Placeholder response generation node."""
    if not state.is_valid:
        state.ai_response = "Entschuldigung, Ihre Eingabe konnte nicht verarbeitet werden."
        return state
    state.ai_response = f"Ich verstehe, dass Sie über '{state.user_input}' sprechen möchten."
    return state


def enhance_response_node(state: SessionState) -> SessionState:
    """Enhance response with crisis info or session context."""
    if state.is_crisis:
        state.enhanced_response = (
            state.ai_response
            + "\n\n🚨 **Krisenhinweis:** Telefonseelsorge: 0800 111 0 111"
        )
    else:
        state.enhanced_response = (
            state.ai_response
            + "\n\n💭 *Antwort berücksichtigt Ihren Gesprächskontext.*"
        )
    return state


def record_messages_node(state: SessionState) -> SessionState:
    """Record user and assistant messages (placeholder)."""
    # In real use, this writes to DB
    state.node_trace.append("messages_recorded")
    return state


# ---------------------------------------------------------------------------
# Factory: Build the default session workflow graph
# ---------------------------------------------------------------------------

def build_default_session_graph() -> SessionWorkflowGraph:
    """Build the standard psychological session workflow graph.

    Graph::

        validate_input → analyze_emotion →[crisis?]→ crisis_response
                                                   → build_context → generate_response
                                                     → enhance_response → record_messages

    Returns:
        Configured ``SessionWorkflowGraph`` ready to ``run()``.
    """
    graph = SessionWorkflowGraph("wellbeing_session")

    # Nodes
    graph.add_node("validate_input", validate_input_node)
    graph.add_node("analyze_emotion", analyze_emotion_node)
    graph.add_node("build_context", build_context_node)
    graph.add_node("generate_response", generate_response_node)
    graph.add_node("enhance_response", enhance_response_node)
    graph.add_node("record_messages", record_messages_node)

    # Edges
    graph.add_edge("validate_input", "analyze_emotion")
    graph.add_conditional_edge(
        "analyze_emotion",
        crisis_router,
        {"crisis": "enhance_response", "normal": "build_context"},
    )
    graph.add_edge("build_context", "generate_response")
    graph.add_edge("generate_response", "enhance_response")
    graph.add_edge("enhance_response", "record_messages")

    # Entry / End
    graph.set_entry("validate_input")
    graph.set_end("record_messages")

    return graph
