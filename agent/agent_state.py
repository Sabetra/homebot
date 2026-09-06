"""
LangGraph Agent State -- TypedDict für den ReAct Agent
======================================================

Definiert den vollständigen State, der durch den LangGraph StateGraph fließt.

SOTA Pattern: TypedDict State mit Annotated Reducers für akkumulative Felder.

Required-Felder (immer bei Start gesetzt) werden von Optional-Feldern
(durch Nodes gesetzt/aktualisiert) getrennt, um Typfehler zu vermeiden.
"""

from __future__ import annotations
from typing import Any, Callable, Dict, List, Optional, TypedDict, Annotated, Required
import operator


class AgentState(TypedDict, total=False):
    """Vollständiger State für den LangGraph ReAct Agent.
    
    Felder mit ``Required`` müssen beim Start gesetzt werden.
    Felder mit ``Annotated[..., operator.add]`` werden akkumuliert.
    Alle anderen Felder sind optional und werden durch Nodes gesetzt.
    """
    
    # ═══════════════════════════════════════════════════════════════════
    # INPUT (einmal gesetzt bei Start) -- Required
    # ═══════════════════════════════════════════════════════════════════
    query: Required[str]                            # Ursprüngliche User-Anfrage
    history: Required[List[Dict[str, Any]]]         # Chat-History
    settings: Required[Dict[str, Any]]              # Runtime-Settings
    
    # ═══════════════════════════════════════════════════════════════════
    # INPUT (optional)
    # ═══════════════════════════════════════════════════════════════════
    image_path: Optional[str]               # Optional: Bild für multimodale Anfragen
    tab_mode: str                           # Progressive Disclosure (2026-08-24):
                                            #   "main_chat" | "finance_tab" | "wellbeing_tab" | "settings_tab"
                                            #   Quelle: settings["tab_mode"] (UI aktivierter Tab), Default "main_chat"
                                            #   Wirkt auf: _tool_schemas_for_state → Tool-Pool-Filter (tool_profiles.py)
    tool_pool: Optional[List[str]]          # Explizite Tool-Pool-Override (Capability-Gap-Retry, max 1x).
                                            #   Set → Profile-Filter wird umgangen (Pool ist bereits final bereinigt).
    capability_gap_retry: bool              # True, wenn die aktuelle Ausführung die EINZIGE Gap-Retry ist.
                                            #   _maybe_capability_gap_retry prüft dieses Feld → Retry maximal 1x.
    
    # ═══════════════════════════════════════════════════════════════════
    # LLM MESSAGES (wächst mit jeder Iteration)
    # ═══════════════════════════════════════════════════════════════════
    messages: List[Dict[str, Any]]          # Vollständige Message-History für create_chat_completion
                                            # Enthält: system, user, assistant (mit tool_calls), tool (Ergebnisse)
    
    # ═══════════════════════════════════════════════════════════════════
    # AGENT LOOP CONTROL
    # ═══════════════════════════════════════════════════════════════════
    route: str                              # "simple" | "agent" | "image"
    iteration: int                          # Aktuelle Iteration (0-basiert)
    max_iterations: int                     # Maximale Iterationen (default: 8)
    should_continue: bool                   # Loop-Control: Soll der Agent weitermachen?
    
    # ═══════════════════════════════════════════════════════════════════
    # TOOL EXECUTION
    # ═══════════════════════════════════════════════════════════════════
    pending_tool_calls: List[Dict[str, Any]]  # Tool-Calls vom LLM (noch nicht ausgeführt)
    tool_results: Annotated[List[Dict[str, Any]], operator.add]  # Akkumulierte Tool-Ergebnisse
    
    # ═══════════════════════════════════════════════════════════════════
    # OUTPUT
    # ═══════════════════════════════════════════════════════════════════
    final_answer: str                       # Finale Antwort
    sources: List[Dict[str, Any]]           # Quellen für GUI
    artifacts: Annotated[List[Dict[str, Any]], operator.add]  # Datei-Artefakte (Diagramme, Plots, etc.)
    
    # ═══════════════════════════════════════════════════════════════════
    # TRACE (für GUI-Anzeige)
    # ═══════════════════════════════════════════════════════════════════
    trace: Dict[str, Any]                   # AgentTrace-kompatible Daten
    
    # ═══════════════════════════════════════════════════════════════════
    # REFLECTION (Reflexion Quality Gate)
    # ═══════════════════════════════════════════════════════════════════
    reflection_done: bool                   # True wenn Reflection bereits gelaufen ist
    reflection_confidence: float            # Confidence-Score 0.0-1.0 aus Reflection
    reflection_guidance: str                # Zusätzliche Guidance für Re-Entry in agent_step
    
    # ═══════════════════════════════════════════════════════════════════
    # RAG PREFETCH (SOTA Pipeline)
    # ═══════════════════════════════════════════════════════════════════
    rag_prefetch_context: str               # RAG-Kontext aus Prefetch (HyDE+CRAG+Compression)
    rag_prefetch_done: bool                 # True wenn RAG-Prefetch bereits gelaufen ist
    
    # ═══════════════════════════════════════════════════════════════════
    # PLANNING / DECOMPOSITION (SOTA: Khot et al. 2023, "Decomposed Prompting")
    # ═══════════════════════════════════════════════════════════════════
    plan_steps: List[str]                   # Dekomponierte Sub-Fragen / Schritte
    plan_done: bool                         # True wenn Planning bereits gelaufen ist
    
    # ═══════════════════════════════════════════════════════════════════
    # WORKING MEMORY / SCRATCHPAD (SOTA: Park et al. 2023)
    # ═══════════════════════════════════════════════════════════════════
    working_memory: Annotated[List[str], operator.add]  # Akkumulierte Fakten/Zwischenergebnisse
    
    # ═══════════════════════════════════════════════════════════════════
    # CORRELATION ID (SOTA: Production Observability)
    # ═══════════════════════════════════════════════════════════════════
    correlation_id: str                     # Unique ID für End-to-End Request Tracing
    
    # ═══════════════════════════════════════════════════════════════════
    # VERIFICATION
    # ═══════════════════════════════════════════════════════════════════
    verification: Optional[Dict[str, Any]]  # Verification-Ergebnis
    
    # ═══════════════════════════════════════════════════════════════════
    # TIMING
    # ═══════════════════════════════════════════════════════════════════
    start_time: float                       # Startzeit für Performance-Messung
    iteration_times: Annotated[List[float], operator.add]  # Zeit pro Iteration (akkumuliert)
    
    # ═══════════════════════════════════════════════════════════════════
    # STREAMING CALLBACK (SOTA: Real-time token delivery to UI)
    # ═══════════════════════════════════════════════════════════════════
    stream_callback: Optional[Callable[[str], None]]  # Token-by-token callback for UI streaming
