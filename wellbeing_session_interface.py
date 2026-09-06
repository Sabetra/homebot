#!/usr/bin/env python3
"""
SESSION-BASIERTE WELLBEING-INTEGRATION
========================================

Moderne Integration des Session-Management-Systems in den Streamlit-Bot
für personalisierte, kontinuierliche Wellbeing-Begleitung.

Features:
- Session-Auswahl und -erstellung per Benutzer
- Kontinuierliche Gespräche mit Zusammenfassungen
- Multi-User-Isolation
- Intelligente Kontextbewahrung
- Live Session-Management

🔄 PYDANTIC V2 MIGRATION - PHASE 3 (Session Management)
   - SessionSummary/SessionMessage jetzt Pydantic-powered
   - Runtime-Validierung für alle Session-Daten
   - Zero-downtime migration mit Fallback
   - Kompatibel mit legacy code

✅ PHASE 7: Service Layer + Dependency Injection
   - ServiceContainer centralises all dependency wiring
   - StartupService handles orphan cleanup & summary generation
   - SessionEndService handles session-end workflow
   - __init__ reduced from 228 to ~40 lines
"""

import streamlit as st
import logging
import time
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Callable, List, Optional, Dict, Any
from dataclasses import dataclass
from i18n import t as i18n_t

logger = logging.getLogger(__name__)

# ✅ PHASE 7: Service Layer -- single import for all DI wiring
from wellbeing_session.services import (
    ServiceContainer,
    ServiceConfig,
    StartupService,
    SessionEndService,
)

# ✅ PHASE 4: Handler imports (still needed for type references)
from wellbeing_session.handlers import (
    MessageHandler,
    ChatInputHandler,
    ResponseGenerator,
)

# ✅ PHASE 5: UI renderer imports
from wellbeing_session.ui import (
    SessionManagementRenderer,
    ActiveSessionRenderer,
    WelcomeRenderer,
)

# ✅ PHASE 6: Lifecycle imports
from wellbeing_session.lifecycle import (
    SessionLifecycleManager,
)

# ✅ PHASE 3+4: Pydantic V2 Models für Session Management
try:
    from models_pydantic_v2 import (
        SessionSummaryModel,
        SessionMessageModel,  # Phase 4
        EmotionalState,
    )
    from pydantic_migration_adapter import (
        legacy_to_pydantic,
        pydantic_to_legacy_dict,
        adapt_session_message,  # Phase 4
    )
    PYDANTIC_AVAILABLE = True
    logger.info("✅ Phase 3+4: Pydantic V2 session models loaded successfully")
except ImportError as e:
    PYDANTIC_AVAILABLE = False
    SessionSummaryModel: Any = None  # type: ignore[no-redef]  # Fallback type
    SessionMessageModel: Any = None  # type: ignore[no-redef]  # Fallback type
    EmotionalState: Any = None  # type: ignore[no-redef]  # Fallback type
    legacy_to_pydantic: Any = None  # type: ignore[no-redef]  # Fallback function
    pydantic_to_legacy_dict: Any = None  # type: ignore[no-redef]  # Fallback function
    adapt_session_message: Any = None  # type: ignore[no-redef]  # Fallback function
    logger.warning(f"⚠️ Pydantic models not available (fallback to legacy): {e}")

# ✅ REFACTORED: Use modern wellbeing module (51 sessions!)
from wellbeing.session_manager import WellbeingSessionManager
from chat_context_manager import ChatContextManager
from emotional_analyzer import LLMEmotionalAnalyzer, EmotionalAnalysis

# ✅ SOTA: WellbeingPipeline (Session-Lifecycle hooks)
try:
    from wellbeing.conversation_core import WellbeingPipeline
    WELLBEING_PIPELINE_AVAILABLE = True
except ImportError:
    WELLBEING_PIPELINE_AVAILABLE = False
    WellbeingPipeline = None  # type: ignore[assignment,misc]
    logger.warning("⚠️ WellbeingPipeline not available")

# ── SOTA: Check if recursive summarizer is available ──
try:
    from wellbeing_session.workflow.recursive_summarizer import RecursiveLLMSummarizer as _RLS
    RECURSIVE_SUMMARIZER_AVAILABLE = True
except ImportError:
    RECURSIVE_SUMMARIZER_AVAILABLE = False

# 🔥 NEW: Refactored utils from wellbeing_session package
from wellbeing_session.utils import (
    normalize_datetime,
    get_sort_time,
    get_german_datetime_info,
    format_datetime_section,
    get_relevance_indicator,
    get_trend_emoji,
    get_valence_description,
    get_status_emoji,
)
from wellbeing_session.adapters import SessionManagerAdapter
from wellbeing_session.context import (
    calculate_relevance_score,
    adaptive_triple_selection,
    rank_summaries_by_relevance,
    # ✅ Phase 6a: Context Builder
    WellbeingContextBuilder,
    ContextBuildRequest,
    ContextBuildResult,
    create_context_builder,
    # ✅ Phase 6b: Session Context Builder, Insight Extractor, Context Formatter
    SessionContextBuilder,
    UserInsightExtractor,
    ContextFormatter,
)

# ✅ PHASE 9b: Real LangGraph workflow (SOTA) + legacy fallback
USE_WORKFLOW_GRAPH = True  # Feature flag -- SOTA LangGraph is now default
USE_REAL_LANGGRAPH = True  # Feature flag -- use real LangGraph (not "inspired")
try:
    from wellbeing_session.workflow import (
        SessionWorkflowGraph,
        SessionState as WorkflowSessionState,
        build_default_session_graph,
        # SOTA: Real LangGraph
        WellbeingSessionState,
        build_langgraph_session_graph,
        LANGGRAPH_AVAILABLE,
        get_dependency_registry,
        # SOTA: LangChain adapter
        LocalLlamaCppChat,
        LANGCHAIN_ADAPTER_AVAILABLE,
        # SOTA: Recursive summarizer
        RecursiveLLMSummarizer,
    )
    WORKFLOW_AVAILABLE = True
    logger.info(
        "✅ Phase 9b: Workflow available (LangGraph=%s, LangChainAdapter=%s)",
        LANGGRAPH_AVAILABLE,
        LANGCHAIN_ADAPTER_AVAILABLE,
    )
except ImportError:
    WORKFLOW_AVAILABLE = False
    LANGGRAPH_AVAILABLE = False
    LANGCHAIN_ADAPTER_AVAILABLE = False
    WellbeingSessionState = None  # type: ignore[assignment,misc]
    build_langgraph_session_graph = None  # type: ignore[assignment]
    LocalLlamaCppChat = None  # type: ignore[assignment,misc]
    RecursiveLLMSummarizer = None  # type: ignore[assignment,misc]
    get_dependency_registry = None  # type: ignore[assignment]
    logger.info("ℹ️ Phase 9: Workflow graph not available (optional)")

# Helper function to normalize datetime objects (avoid offset-naive vs offset-aware issues)


# 🔄 PHASE 3+4: Pydantic-powered Session Types with Legacy Fallback
# =================================================================
# Primary: Use Pydantic V2 models (runtime validation, serialization, etc.)
# Fallback: Use dataclasses if Pydantic not available (graceful degradation)

SessionSummary: type
SessionMessage: type

if PYDANTIC_AVAILABLE and SessionSummaryModel is not None and SessionMessageModel is not None:
    # ✅ Pydantic V2 Models (Primary Interface)
    SessionSummary = SessionSummaryModel
    SessionMessage = SessionMessageModel
    
    logger.info("✅ Phase 3+4: Using Pydantic V2 models (SessionSummary + SessionMessage)")
else:
    # ⚠️ Fallback: Legacy dataclasses (no validation)
    @dataclass
    class _SessionSummaryFallback:
        """Legacy SessionSummary (fallback)"""
        session_id: str
        user_name: str
        start_time: datetime
        last_activity: datetime
        message_count: int
        key_topics: List[str]
        emotional_state: str
        session_summary: str
        is_active: bool
    
    @dataclass  
    class _SessionMessageFallback:
        """Legacy SessionMessage (fallback)"""
        message_id: str
        session_id: str
        timestamp: datetime
        role: str
        content: str
        emotional_markers: List[str]
        is_crisis: bool
    
    SessionSummary = _SessionSummaryFallback  # type: ignore[assignment]
    SessionMessage = _SessionMessageFallback  # type: ignore[assignment]
    logger.warning("⚠️ Phase 3+4: Using legacy dataclasses (Pydantic unavailable)")



class WellbeingSessionInterface:
    """
    Session-basierte Schnittstelle für die Wellbeing-Begleitung.

    ✅ PHASE 7: Uses ServiceContainer for dependency injection.
    All service creation and wiring is centralised in the container.
    """

    def __init__(self) -> None:
        """Initialise the session interface via ServiceContainer."""
        # ✅ PHASE 7: Retrieve/create and normalize SessionManager in st.session_state.
        # This hardens against legacy cached objects from earlier runtime versions.
        cached_raw = st.session_state.get("_wellbeing_session_manager")
        cached_manager, is_new_manager = self._normalize_cached_session_manager(cached_raw)
        st.session_state._wellbeing_session_manager = cached_manager

        if is_new_manager:
            logger.info("🔄 WellbeingSessionInterface: new SessionManager created")
        else:
            logger.info("✅ WellbeingSessionInterface: reusing existing SessionManager")

        # ✅ PHASE 7: Build the service container
        self._container: ServiceContainer = ServiceContainer(ServiceConfig()).build(
            session_manager=cached_manager
        )

        # ✅ Expose commonly-accessed services as direct attributes (backward-compat)
        self.session_manager: SessionManagerAdapter = self._container.session_manager
        self.context_manager: ChatContextManager = self._container.context_manager
        self.emotional_analyzer: LLMEmotionalAnalyzer = self._container.emotional_analyzer
        self.message_handler: MessageHandler = self._container.message_handler
        self.chat_input_handler: ChatInputHandler = self._container.chat_input_handler
        self.response_generator: ResponseGenerator = self._container.response_generator
        self.session_management_renderer: SessionManagementRenderer = self._container.session_management_renderer
        self.active_session_renderer: ActiveSessionRenderer = self._container.active_session_renderer
        self.welcome_renderer: WelcomeRenderer = self._container.welcome_renderer
        self.lifecycle_manager: SessionLifecycleManager = self._container.lifecycle_manager
        self.context_builder: WellbeingContextBuilder = self._container.context_builder
        self.session_context_builder: SessionContextBuilder = self._container.session_context_builder
        self.user_insight_extractor_module: UserInsightExtractor = self._container.user_insight_extractor_module
        self.context_formatter: ContextFormatter = self._container.context_formatter
        self.profile_cache: Optional[Any] = self._container.profile_cache
        self.insight_extractor: Optional[Any] = self._container.insight_extractor
        self.chat_logic: Optional[Any] = self._container.chat_logic
        self.use_v2_context_builder: bool = self._container.use_v2_context_builder
        self.context_builder_v2: Optional[Any] = self._container.context_builder_v2

        # ✅ PHASE 7: Run startup cleanup for new managers
        # generate_summaries=False: ModelLoader ist beim Startup noch nicht
        # verfügbar -- Summary-Generierung wird auf set_model_loader() deferred.
        if is_new_manager:
            self._container.startup_service.run_startup_cleanup(generate_summaries=False)

        # ✅ PHASE 7: Session-end service (lazy -- created with current insight extractor)
        self._session_end_service: Optional[SessionEndService] = None

        # ✅ PHASE 9b: Real LangGraph workflow (SOTA)
        self._workflow_graph: Optional[Any] = None
        self._langgraph_compiled: Optional[Any] = None
        self._langchain_model: Optional[Any] = None

        if WORKFLOW_AVAILABLE and USE_WORKFLOW_GRAPH:
            if USE_REAL_LANGGRAPH and LANGGRAPH_AVAILABLE and build_langgraph_session_graph is not None:
                try:
                    self._langgraph_compiled = build_langgraph_session_graph()
                    logger.info("✅ Phase 9b: Real LangGraph StateGraph compiled")
                except Exception as exc:
                    logger.warning("⚠️ Real LangGraph init failed, falling back: %s", exc)
                    self._langgraph_compiled = None

            # Legacy fallback
            if self._langgraph_compiled is None:
                from wellbeing_session.workflow import build_default_session_graph as _build_graph
                self._workflow_graph = _build_graph()
                logger.info("✅ Phase 9: Legacy workflow graph initialised")

        logger.info("✅ WellbeingSessionInterface initialised via ServiceContainer")

        # ✅ SOTA: WellbeingPipeline für Session-Lifecycle-Hooks
        self._wellbeing_pipeline: Optional[Any] = None
        self._wellbeing_rag_bootstrapped: bool = False
        if WELLBEING_PIPELINE_AVAILABLE and WellbeingPipeline is not None:
            # Step 1 — Pipeline construction is a soft dependency: a missing
            # DB or model loader must not prevent the SessionInterface from
            # starting. Failures here are logged and degrade gracefully.
            try:
                psych_db = None
                sm = getattr(self.session_manager, '_manager', None) or self.session_manager
                if hasattr(sm, 'db') and sm.db is not None:
                    psych_db = sm.db
                elif hasattr(sm, '_db') and sm._db is not None:
                    psych_db = sm._db

                self._wellbeing_pipeline = WellbeingPipeline(
                    db=psych_db,
                    model_loader=None  # wird in set_model_loader() nachgerüstet
                )
            except Exception as exc:
                logger.warning("⚠️ WellbeingPipeline init failed: %s", exc)
                self._wellbeing_pipeline = None
            if self._wellbeing_pipeline is not None:
                logger.info("✅ WellbeingPipeline in SessionInterface initialisiert (PsychoRAG bootstrap deferred)")

    def _normalize_cached_session_manager(self, cached_raw: Any) -> tuple[SessionManagerAdapter, bool]:
        """Return a valid SessionManagerAdapter and whether it was newly created.

        Accepted input variants:
        - SessionManagerAdapter: passthrough
        - WellbeingSessionManager: wrapped into SessionManagerAdapter
        - anything else / None: create a fresh manager + adapter
        """
        if isinstance(cached_raw, SessionManagerAdapter):
            return cached_raw, False

        if isinstance(cached_raw, WellbeingSessionManager):
            logger.info("🔄 Wrapping legacy cached WellbeingSessionManager in SessionManagerAdapter")
            return SessionManagerAdapter(cached_raw), False

        if cached_raw is not None:
            logger.warning(
                "⚠️ Invalid cached _wellbeing_session_manager type: %s -- recreating",
                type(cached_raw).__name__,
            )

        psych_manager = WellbeingSessionManager(
            db=None,
            privacy_handler=None,
            model_loader=None,
        )
        return SessionManagerAdapter(psych_manager), True

    def _ensure_wellbeing_rag_bootstrapped(self) -> None:
        """Bootstrap the psych corpus into the shared RAG exactly once."""
        if self._wellbeing_rag_bootstrapped:
            return
        if self._wellbeing_pipeline is None:
            return

        from agent.tools import get_global_rag_store

        rag_mgr = get_global_rag_store()
        if rag_mgr is None:
            raise RuntimeError(
                "Global UnifiedRagStore is not initialised — "
                "PsychoRAG bootstrap cannot proceed."
            )

        self._wellbeing_pipeline.rag_bootstrapper.bootstrap_into_rag(
            rag_manager=rag_mgr
        )
        self._wellbeing_rag_bootstrapped = True
        logger.info("✅ PsychoRAG bootstrap completed on first psych use")

    # ------------------------------------------------------------------
    # Late-binding setters
    # ------------------------------------------------------------------

    def set_chat_logic(self, chat_logic: Any) -> None:
        """Propagate the chat-logic handle to all components via the container."""
        self.chat_logic = chat_logic
        self._container.update_chat_logic(chat_logic)

        # ✅ RC-7 FIX: Setze wellbeing_interface auf chat_logic,
        # damit PostResponseHandler den SessionManager findet.
        # Vorher: chat_logic.wellbeing_interface = None (nie zugewiesen)
        # → PostResponseHandler konnte SessionManager nicht auflösen
        if chat_logic is not None:
            chat_logic.wellbeing_interface = self

        # Sync direct attribute references
        self.insight_extractor = self._container.insight_extractor

        # Wire the RAG search callable into the treatment-domain Reviewer.
        # This is the **single** RAG injection into the treatment domain —
        # see CarePlanManager docstring for the architectural rationale.
        # The Reviewer uses it only for crisis-adherence checks at
        # ELEVATED / ACUTE risk; no inference component sees this handle.
        self._wire_treatment_rag(chat_logic)

        logger.info("🔗 Chat Logic mit psychologischer Schnittstelle verknüpft")

    def _wire_treatment_rag(self, chat_logic: Any) -> None:
        """Build a list[str] adapter over the orchestrator's RAG manager and
        hand it to the CarePlanManager. Idempotent — safe to call multiple
        times; passes ``None`` if any component is missing."""
        try:
            sm = getattr(self.session_manager, '_manager', None) or self.session_manager
            tm = getattr(sm, 'treatment_manager', None)
            if tm is None or not hasattr(tm, 'set_rag_function'):
                return
            orch = getattr(chat_logic, 'orchestrator', None) if chat_logic else None
            rag_mgr = getattr(orch, 'rag_manager', None) if orch else None
            if rag_mgr is None or not hasattr(rag_mgr, 'execute_rag_with_gap_detection'):
                tm.set_rag_function(None)
                return

            def _rag_search_adapter(query: str) -> list:
                """Return list[str] of evidence snippets for the Reviewer."""
                if not query or not query.strip():
                    return []
                tool_results = rag_mgr.execute_rag_with_gap_detection(
                    query=query,
                    k=4,
                    persist_to_rag=False,
                    enable_web_fallback=False,
                )
                snippets: list = []
                for r in tool_results or []:
                    content = getattr(r, 'content', None) or getattr(r, 'text', None)
                    if content is None and isinstance(r, dict):
                        content = r.get('content') or r.get('text')
                    if content:
                        snippets.append(str(content))
                return snippets

            tm.set_rag_function(_rag_search_adapter)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Failed to wire RAG search into CarePlanManager: %s "
                "(crisis-adherence check will be inactive)", exc,
            )

    def set_model_loader(self, model_loader: Any) -> None:
        """Propagate the model-loader to session manager, profile cache, and LangChain adapter.
        
        Also runs deferred startup summary generation -- summaries are skipped
        during ``__init__`` because the model isn't available yet, and retried
        here once the ModelLoader is set for the first time.
        """
        try:
            self._container.update_model_loader(model_loader)
            
            # ✅ PHASE 9b: Wire LangChain adapter for real LangGraph
            if (
                LANGCHAIN_ADAPTER_AVAILABLE
                and LocalLlamaCppChat is not None
                and model_loader is not None
            ):
                try:
                    self._langchain_model = LocalLlamaCppChat(model_loader=model_loader)
                    logger.info("✅ Phase 9b: LocalLlamaCppChat adapter created")
                except Exception as exc:
                    logger.warning("⚠️ LangChain adapter creation failed: %s", exc)
                    self._langchain_model = None
            
            # ✅ Deferred Startup Summaries: Beim __init__ wurde
            # generate_summaries=False übergeben weil ModelLoader fehlte.
            # Jetzt ist er da → pendente Summaries generieren.
            if model_loader is not None:
                try:
                    self._container.startup_service.run_startup_cleanup(generate_summaries=True)
                except Exception as exc:
                    logger.warning("⚠️ Deferred summary generation failed: %s", exc)

                # ✅ SOTA: WellbeingPipeline-ModelLoader nachrüsten
                if self._wellbeing_pipeline is not None:
                    try:
                        self._wellbeing_pipeline.case_formulator.model_loader = model_loader
                        logger.info("✅ WellbeingPipeline model_loader gesetzt")
                    except Exception as exc:
                        logger.warning("⚠️ WellbeingPipeline model_loader update failed: %s", exc)
                    
        except Exception as e:
            logger.error(f"❌ Fehler beim Setzen des ModelLoaders: {e}")

    # ------------------------------------------------------------------
    # Startup / cleanup (delegated to StartupService)
    # ------------------------------------------------------------------

    def _cleanup_orphaned_sessions_on_startup(self) -> None:
        """Delegate to StartupService."""
        self._container.startup_service.run_startup_cleanup()

    def _generate_missing_summaries(self, sessions: List[tuple]) -> None:
        """Delegate to StartupService."""
        self._container.startup_service.generate_missing_summaries(
            [(s[0], s[1], s[2]) for s in sessions]
        )

    def render_session_management_ui(self):
        """
        Rendert die Session-Management-UI.
        
        ✅ PHASE 5: Delegiert an SessionManagementRenderer für modulare UI-Verwaltung.
        
        Returns:
            bool: True wenn Session aktiv ist, False sonst
        """
        return self.session_management_renderer.render_session_management_ui()
    
    def handle_wellbeing_message(self, user_message: str, ai_response: str) -> str:
        """
        Verarbeitet psychologische Nachrichten mit Session-Kontext.
        
        ✅ PHASE 9b: Routes through real LangGraph StateGraph (SOTA) if available.
        Falls back to legacy graph, then to direct delegation.
        
        Args:
            user_message: Benutzernachricht
            ai_response: Standard AI-Antwort
            
        Returns:
            Erweiterte AI-Antwort mit psychologischem Kontext
        """
        # ✅ PHASE 9b: SOTA -- Real LangGraph StateGraph
        if self._langgraph_compiled is not None:
            return self._handle_via_real_langgraph(user_message, ai_response)
        
        # ✅ PHASE 9: Legacy -- LangGraph-inspired workflow
        if self._workflow_graph is not None and WORKFLOW_AVAILABLE:
            return self._handle_via_legacy_workflow(user_message, ai_response)
        
        # Direct delegation (no graph)
        return self.message_handler.handle_wellbeing_message(
            user_message=user_message,
            ai_response=ai_response,
            build_context_func=self._build_comprehensive_user_context,
            format_context_func=self._format_context_for_llm
        )

    def _handle_via_real_langgraph(self, user_message: str, ai_response: str) -> str:
        """
        Process message through real LangGraph StateGraph (SOTA).
        
        ✅ PHASE 9b: Full LangGraph with TypedDict state, conditional edges,
        checkpointing, and LangChain model adapter.
        
        Args:
            user_message: User message
            ai_response: Pre-generated AI response (used as fallback)
            
        Returns:
            Enhanced AI response from LangGraph execution
        """
        try:
            session_id = getattr(st.session_state, 'wellbeing_current_session', '')
            user_id = (
                getattr(st.session_state, 'wellbeing_current_user_id', '')
                or self.session_manager.resolve_user_id(
                    getattr(st.session_state, 'wellbeing_current_user', '')
                )
            )
            thread_id = session_id or "default"
            
            # Register non-serializable dependencies in the registry
            if get_dependency_registry is not None:
                registry = get_dependency_registry()
                registry.register(thread_id, {
                    "emotional_analyzer": self.emotional_analyzer,
                    "context_builder": self.context_builder,
                    "context_formatter": self.context_formatter,
                    "chat_logic": self.chat_logic,
                    "model_loader": getattr(self.chat_logic, "model_loader", None) if self.chat_logic else None,
                    "session_manager": self.session_manager,
                    "langchain_model": self._langchain_model,
                    "wellbeing_pipeline": self._wellbeing_pipeline,
                })
            
            # Build the input state (serializable data only)
            input_state: Dict[str, Any] = {
                "user_input": user_message,
                "session_id": session_id,
                "user_id": user_id,
                "wellbeing_enabled": True,
                "pre_generated_response": ai_response,
                "errors": [],
                "node_trace": [],
                "node_timings": {},
                "_thread_id": thread_id,
            }
            
            # Invoke the compiled LangGraph with thread_id for checkpointing
            config = {"configurable": {"thread_id": thread_id}}
            compiled = self._langgraph_compiled
            assert compiled is not None  # Guarded by caller
            result = compiled.invoke(input_state, config=config)
            
            # Check for errors
            errors = result.get("errors", [])
            if errors:
                logger.warning("⚠️ LangGraph errors: %s", errors)
            
            node_trace = result.get("node_trace", [])
            node_timings = result.get("node_timings", {})
            logger.info(
                "✅ Real LangGraph complete: nodes=%s, timings=%s",
                node_trace, {k: f"{v:.1f}ms" for k, v in node_timings.items()},
            )
            
            enhanced = result.get("enhanced_response", "")
            ai_resp = result.get("ai_response", "")
            return enhanced or ai_resp or ai_response
            
        except Exception as e:
            logger.error("❌ Real LangGraph failed, falling back: %s", e, exc_info=True)
            # Fallback to legacy graph or direct
            if self._workflow_graph is not None:
                return self._handle_via_legacy_workflow(user_message, ai_response)
            return self.message_handler.handle_wellbeing_message(
                user_message=user_message,
                ai_response=ai_response,
                build_context_func=self._build_comprehensive_user_context,
                format_context_func=self._format_context_for_llm
            )

    def _handle_via_legacy_workflow(self, user_message: str, ai_response: str) -> str:
        """
        Process message through legacy LangGraph-inspired workflow.
        
        ✅ PHASE 9: Legacy graph-based orchestration (fallback).
        """
        try:
            from wellbeing_session.workflow import SessionState as WfState
            
            state = WfState(
                user_input=user_message,
                session_id=getattr(st.session_state, 'wellbeing_current_session', ''),
                user_id=(
                    getattr(st.session_state, 'wellbeing_current_user_id', '')
                    or self.session_manager.resolve_user_id(
                        getattr(st.session_state, 'wellbeing_current_user', '')
                    )
                ),
                ai_response=ai_response,
            )
            
            result = self._workflow_graph.run(state)  # type: ignore[union-attr]
            
            if result.errors:
                logger.warning(f"⚠️ Legacy workflow graph errors: {result.errors}")
                return ai_response
            
            logger.info(
                f"✅ Legacy workflow complete: {len(result.node_trace)} nodes, "
                f"timings={result.node_timings}"
            )
            return result.enhanced_response or result.ai_response or ai_response
            
        except Exception as e:
            logger.error(f"❌ Legacy workflow failed, falling back: {e}")
            return self.message_handler.handle_wellbeing_message(
                user_message=user_message,
                ai_response=ai_response,
                build_context_func=self._build_comprehensive_user_context,
                format_context_func=self._format_context_for_llm
            )
    
    def _extract_emotional_markers(self, message: str) -> List[str]:
        """
        LLM-basierte emotionale Marker-Extraktion
        
        ✅ PHASE 4: Delegiert an MessageHandler
        """
        return self.message_handler.extract_emotional_markers(message)
    
    def _detect_crisis(self, message: str) -> bool:
        """
        LLM-basierte Krisenerkennung
        
        ✅ PHASE 4: Delegiert an MessageHandler
        """
        return self.message_handler.detect_crisis(message)
    
    def _enhance_ai_response(self, original_response: str, contextual_prompt: str, is_crisis: bool) -> str:
        """
        Erweitert AI-Antwort mit psychologischem Kontext
        
        ✅ PHASE 4: Delegiert an MessageHandler
        """
        return self.message_handler.enhance_ai_response(original_response, contextual_prompt, is_crisis)
    
    def get_session_statistics(self) -> Dict[str, Any]:
        """Holt Session-Statistiken für Display"""
        try:
            if not st.session_state.wellbeing_current_session:
                return {}
            
            session_id = st.session_state.wellbeing_current_session
            summary = self.session_manager.get_session_summary(session_id)
            
            if not summary:
                return {}
            
            messages = self.session_manager.get_session_context(session_id)
            
            # Berechne Statistiken
            user_messages = [m for m in messages if m.get('role') == 'user']
            crisis_messages = [m for m in messages if m.get('is_crisis', False)]
            
            emotional_distribution: Dict[str, int] = {}
            for message in user_messages:
                for marker in message.get('emotional_markers', []):
                    emotional_distribution[marker] = emotional_distribution.get(marker, 0) + 1
            
            return {
                'total_messages': summary.get('message_count', 0),
                'user_messages': len(user_messages),
                'session_duration': datetime.now(timezone.utc) - normalize_datetime(summary.get('start_time', datetime.now(timezone.utc))),
                'emotional_state': summary.get('emotional_state', 'neutral'),
                'key_topics': summary.get('key_topics', []),
                'crisis_count': len(crisis_messages),
                'emotional_distribution': emotional_distribution,
                'last_activity': normalize_datetime(summary.get('last_activity', datetime.now(timezone.utc)))
            }
            
        except Exception as e:
            logger.error(f"❌ Fehler beim Abrufen der Session-Statistiken: {e}")
            return {}
    
    def cleanup_old_data(self, days: int = 30) -> int:
        """Bereinigt alte Session-Daten"""
        try:
            return self.session_manager.cleanup_old_sessions(days)
        except Exception as e:
            logger.error(f"❌ Fehler bei Datenbereinigung: {e}")
            return 0

    # Single source of truth for psych_* session-state defaults.
    # Each entry: (key, default-factory). Using a factory keeps mutable
    # defaults isolated per Streamlit session.
    _PSYCH_STATE_DEFAULTS: tuple[tuple[str, Any], ...] = (
        ("wellbeing_current_user", ""),
        ("wellbeing_current_user_id", ""),
        ("wellbeing_current_session", ""),
        ("wellbeing_enabled", False),
    )

    def _ensure_session_state_defaults(self) -> None:
        """Initialise required psych_* keys in st.session_state exactly once.

        Streamlit ≥ 1.30 raises AttributeError for missing keys when accessed
        via attribute syntax (st.session_state.foo). The session-management
        renderer relies on attribute access for these keys, so they must be
        seeded before the first render. setdefault is idempotent across reruns.
        """
        for key, default in self._PSYCH_STATE_DEFAULTS:
            if key not in st.session_state:
                st.session_state[key] = default

    def render_complete_interface(self):
        """Rendert die komplette Wellbeing-Benutzeroberfläche"""

        # Streamlit-idiomatic single source of truth for psych_* session state.
        # Must run before any renderer touches these keys -- the renderers use
        # direct attribute access (e.g. st.session_state.wellbeing_current_user)
        # which raises AttributeError on missing keys (Streamlit ≥ 1.30).
        self._ensure_session_state_defaults()

        # ── Rechtlicher Hinweis (Pflicht für öffentliche Freigabe) ──────────
        # Das Wellbeing-Modul ist KEIN medizinisches Angebot. Dieser
        # Banner muss in WILLKOMMEN- und AKTIVER-Sichtbarkeit rendern
        # (siehe docs/19_LICENSES_AND_COMPLIANCE.md).
        st.info(
            i18n_t(
                "wellbeing.disclaimer",
                "🧠 This is a support and self-reflection tool — it is not a "
                "substitute for medical advice, diagnosis, or therapy. If you "
                "are in crisis, please contact local emergency services or a "
                "crisis helpline (e.g. 112).",
            )
        )

        # ── EU AI Act Art. 50(1): AI-Offenlegung (Pflicht für öffentliche Freigabe) ──
        # Der Hinweis, dass mit einem KI-System interagiert wird, muss dem
        # Nutzer in WILLKOMMEN- und AKTIVER-Sichtbarkeit sichtbar sein
        # (siehe docs/19_LICENSES_AND_COMPLIANCE.md).
        st.info(
            i18n_t(
                "wellbeing_ui.welcome.ai_note",
                "ℹ️ You are chatting with an AI system (EU AI Act Art. 50(1) disclosure). "
                "This tool is not a substitute for professional help.",
            )
        )

        # ✅ PHASE 10: System-Status-Indicator in Sidebar
        self._render_system_status()
        
        # Header und Session Management
        self.render_session_management_ui()
        
        st.divider()
        
        # Wenn eine Session aktiv ist, zeige Chat-Interface
        if st.session_state.wellbeing_enabled and st.session_state.wellbeing_current_session:
            self._render_active_session_interface()
        else:
            self._render_welcome_interface()

    def _render_system_status(self) -> None:
        """Render a compact system status indicator in the sidebar.
        
        Shows which SOTA features are active so operators can confirm
        the latest code is running.
        """
        with st.sidebar:
            with st.expander("⚙️ System v10 -- SOTA Status", expanded=False):
                features = {
                    "Pydantic V2": PYDANTIC_AVAILABLE,
                    "Real LangGraph": self._langgraph_compiled is not None,
                    "LangChain Adapter": self._langchain_model is not None,
                    "Legacy Workflow": self._workflow_graph is not None,
                    "Wellbeing Pipeline": self._wellbeing_pipeline is not None,
                    "Recursive Summarizer": RECURSIVE_SUMMARIZER_AVAILABLE if 'RECURSIVE_SUMMARIZER_AVAILABLE' in dir() else False,
                    "Service Layer (DI)": self._container is not None,
                    "Connection Pooling": getattr(self._container, '_config', None) is not None,
                    "Semantic Cache": True,
                    "Async Ready": hasattr(self._container, 'startup_service'),
                    "PII Protection": True,
                    "OpenTelemetry": True,
                }
                
                active = sum(1 for v in features.values() if v)
                st.caption(f"**{active}/{len(features)}** Features aktiv")
                
                for name, status in features.items():
                    icon = "✅" if status else "⬜"
                    st.caption(f"{icon} {name}")
    
    def _render_active_session_interface(self):
        """
        Rendert die aktive Session-Interface.
        
        ✅ PHASE 5: Delegiert an ActiveSessionRenderer für modulare UI-Verwaltung.
        """
        # Delegate to active session renderer with required callbacks
        self.active_session_renderer.render_active_session_interface(
            handle_input_func=self._handle_wellbeing_chat_input,
            end_session_func=self._end_current_session,
            show_session_notes_func=self._show_session_notes
        )
    
    def _render_welcome_interface(self):
        """
        Rendert die Willkommens-Interface wenn keine Session aktiv ist.
        
        ✅ PHASE 5: Delegiert an WelcomeRenderer für modulare UI-Verwaltung.
        """
        # Delegate to welcome renderer with optional quick start callback
        self.welcome_renderer.render_welcome_interface(
            create_session_func=self._create_and_start_new_session
        )
    
    def _handle_wellbeing_chat_input(self, user_input: str):
        """
        Verarbeitet psychologische Chat-Eingaben mit Context-Management
        
        ✅ PHASE 4: Delegiert an ChatInputHandler
        """
        self.chat_input_handler.handle_wellbeing_chat_input(
            user_input=user_input,
            generate_response_func=self._generate_wellbeing_response
        )
    
    def _generate_wellbeing_response(self, user_input: str) -> str:
        """
        Generiert eine psychologische AI-Antwort mit ALLEN Core-Modulen integriert.
        
        ✅ PHASE 6B: Delegiert an ResponseGenerator (handlers/response_generator.py).
        
        Args:
            user_input: Benutzer-Nachricht
            
        Returns:
            Generierte AI-Antwort
        """
        return self.response_generator.generate_wellbeing_response(
            user_input=user_input,
            build_context_func=self._build_comprehensive_user_context,
            format_context_func=self._format_context_for_llm,
            load_history_func=self._load_session_history_into_chat_logic_dual
        )
    
    def _load_session_history_into_chat_logic_dual(
        self, messages: List[Dict[str, Any]], chat_logic_instance: Any
    ) -> None:
        """
        Unified helper to load session history into any chat_logic instance.
        
        Args:
            messages: Session messages from DB.
            chat_logic_instance: The chat logic to inject history into.
        """
        try:
            if not chat_logic_instance:
                return
            chat_logic_instance.message_history = []
            for msg in messages:
                chat_logic_instance.message_history.append({
                    'role': msg.get('role', 'user'),
                    'content': msg.get('content', '')
                })
            logger.info(f"✅ {len(messages)} Session-Nachrichten in chat_logic.message_history geladen")
        except Exception as e:
            logger.error(f"❌ Fehler beim Laden der Session-Historie: {e}")
    
    def _detect_emotional_state(self, text: str) -> str:
        """LLM-basierte emotionale Zustandserkennung (ersetzt Keyword-System)"""
        try:
            # Verwende LLM-basierten Emotional Analyzer
            analysis = self.emotional_analyzer.analyze_emotional_state(text)
            
            # Konvertiere zu deutschem Format (für Kompatibilität)
            emotion_mapping = {
                'freude': 'Glücklich',
                'trauer': 'Traurig', 
                'angst': 'Ängstlich',
                'wut': 'Verärgert',
                'stress': 'Gestresst',
                'einsamkeit': 'Einsam',
                'verwirrung': 'Verwirrt',
                'hoffnung': 'Hoffnungsvoll',
                'frustration': 'Frustriert'
            }
            
            german_emotion = emotion_mapping.get(analysis.dominant_emotion, analysis.dominant_emotion.capitalize())
            
            logger.debug(f"🎭 Emotionale Zustandserkennung: {german_emotion} "
                        f"(Konfidenz: {analysis.confidence:.2f})")
            
            return german_emotion
            
        except Exception as e:
            logger.warning(f"⚠️ LLM-Emotionserkennung fehlgeschlagen: {e}")
            return "Neutral"
    
    def _create_and_start_new_session(self):
        """Erstellt und startet eine neue psychologische Session"""
        try:
            session_id = self.session_manager.create_session(
                st.session_state.wellbeing_current_user
            )
            
            st.session_state.wellbeing_current_session = session_id
            st.session_state.wellbeing_enabled = True

            # ✅ SOTA: Homework Follow-up & Outcome-Baseline am Session-Start
            if self._wellbeing_pipeline is not None:
                user_id = (
                    st.session_state.get('wellbeing_current_user_id', '')
                    or self.session_manager.resolve_user_id(
                        st.session_state.get('wellbeing_current_user', '')
                    )
                )
                if user_id:
                    # Offene Hausaufgaben aus vorherigen Sessions prüfen
                    followup = self._wellbeing_pipeline.homework_manager.generate_followup_prompt(user_id)
                    if followup:
                        st.session_state['_psych_homework_followup'] = followup
                        logger.info("📋 Homework Follow-up für Session-Start vorbereitet")

                    # Screening-Vorschlag (periodisch, alle 14 Tage)
                    screening_suggestion = self._wellbeing_pipeline.screening.suggest_periodic_screening(user_id)
                    if screening_suggestion:
                        st.session_state['_psych_screening_suggestion'] = screening_suggestion
                        logger.info("📊 Screening-Vorschlag für Session gesetzt: %s", screening_suggestion)

            st.success(f"✅ Neue Wellbeing-Session erstellt: {session_id[:8]}...")
            st.rerun()
        
        except Exception as e:
            st.error(f"❌ Fehler beim Erstellen der Session: {e}")
    
    def _show_session_notes(self):
        """Zeigt Session-Notizen Dialog"""
        with st.expander("📝 Session-Notizen", expanded=True):
            st.markdown("**Persönliche Notizen für diese Session:**")
            
            # Hier könnte eine Notizen-Funktion implementiert werden
            notes = st.text_area(
                "Ihre Notizen:",
                height=100,
                placeholder="Persönliche Gedanken, Erkenntnisse, Ziele..."
            )
            
            if st.button("💾 Notizen speichern"):
                # Hier würden die Notizen gespeichert werden
                st.success("✅ Notizen gespeichert!")
    
    def _extract_session_insights(self, session_id: str) -> Dict[str, Any]:
        """
        Extrahiert psychologische Insights aus einer Session
        
        Args:
            session_id: Die Session-ID aus der Insights extrahiert werden sollen
            
        Returns:
            Dictionary mit extrahierten Insights (life_event, personality, emotional_state, etc.)
        """
        if not self.insight_extractor:
            return {}
        
        try:
            # Hole die Conversation History direkt aus dem Session-Manager.
            # Das ist die authoritative Quelle für psychologische Sessions.
            conversation_history = self.session_manager.get_session_context(
                session_id,
                max_messages=1000,
            )
            conversation_history = [
                {
                    "role": msg.get("role", "user"),
                    "content": msg.get("content", ""),
                }
                for msg in (conversation_history or [])
                if msg.get("content")
            ]

            if not conversation_history:
                logger.info(
                    "ℹ️ Keine Conversation-History für Insight-Extraktion in Session %s",
                    session_id[:12],
                )
                return {}

            # Verwende die psychologische User-ID, nicht die generische current_user_id.
            user_id = (
                st.session_state.get('wellbeing_current_user_id')
                or st.session_state.get('wellbeing_current_user')
                or st.session_state.get('current_user_id')
            )

            if not user_id:
                session_summary = self.session_manager.get_session_summary(session_id)
                if session_summary:
                    user_id = session_summary.get('user_id') or session_summary.get('user_name')

            if not user_id:
                logger.warning(
                    "⚠️ Keine user_id für Insight-Extraktion bestimmbar (Session %s)",
                    session_id[:12],
                )
                return {}
            
            # Extrahiere Insights mit dem WellbeingUserInsightExtractor
            insights = self.insight_extractor.extract_insights_from_session(
                session_id=session_id,
                user_id=user_id,
                conversation_history=conversation_history
            )

            # Strukturiere die Insights für die UI. Wichtig: alle realen Insight-Typen
            # erhalten, sonst werden korrekt extrahierte Einsichten fälschlich verworfen.
            structured_insights: Dict[str, List[Any]] = {}

            for insight in insights:
                insight_type = getattr(insight, 'insight_type', 'unknown') or 'unknown'
                structured_insights.setdefault(insight_type, []).append(insight)
            
            return structured_insights
            
        except Exception as e:
            st.error(f"❌ Fehler beim Extrahieren der Insights: {str(e)}")
            return {}
    
    def _end_current_session(self) -> None:
        """Delegate session-ending workflow to SessionEndService."""
        try:
            if not st.session_state.get("wellbeing_current_session"):
                return

            # ✅ SOTA: Outcome-Assessment & Screening-Score am Session-Ende
            if self._wellbeing_pipeline is not None:
                session_id = st.session_state.get("wellbeing_current_session", "")
                user_id = (
                    st.session_state.get('wellbeing_current_user_id', '')
                    or self.session_manager.resolve_user_id(
                        st.session_state.get('wellbeing_current_user', '')
                    )
                )
                if session_id and user_id:
                    try:
                        # Mikro-Outcome-Assessment (letzte N Nachrichten analysieren)
                        messages = self.session_manager.get_session_context(session_id)
                        if messages:
                            user_msgs = [m.get('content', '') for m in messages if m.get('role') == 'user']
                            assistant_msgs = [m.get('content', '') for m in messages if m.get('role') == 'assistant']
                            if user_msgs:
                                # Passiver Wellbeing-Signalscan basierend auf Session-Inhalt
                                full_text = " ".join(user_msgs[-5:])  # letzte 5 Nachrichten
                                mood_estimate = self._wellbeing_pipeline.screening.estimate_from_text(
                                    text=full_text, instrument='mood'
                                )
                                calm_estimate = self._wellbeing_pipeline.screening.estimate_from_text(
                                    text=full_text, instrument='calm'
                                )
                                if mood_estimate or calm_estimate:
                                    logger.info(
                                        "📊 Session-Ende Wellbeing-Scan: MoodCheck≈%s, CalmCheck≈%s",
                                        mood_estimate, calm_estimate
                                    )

                            # Outcome-Monitor: Session-Assessment speichern
                            self._wellbeing_pipeline.outcome_monitor.record_session_end(
                                user_id=user_id,
                                session_id=session_id,
                                message_count=len(messages)
                            )
                            logger.info("✅ Outcome-Assessment für Session %s gespeichert", session_id[:8])
                    except Exception as outcome_exc:
                        logger.warning("⚠️ Session-Ende Assessment failed: %s", outcome_exc)

            # Lazily build the end-service so it always has the latest insight extractor
            if self._session_end_service is None:
                extract_fn = (
                    self._extract_session_insights
                    if hasattr(self, "_extract_session_insights")
                    else None
                )
                self._session_end_service = SessionEndService(
                    session_manager=self.session_manager,
                    insight_extractor_fn=extract_fn,
                    profile_cache=self.profile_cache,
                )
            self._session_end_service.render_end_session_dialog()

        except Exception as e:
            logger.error(f"❌ Fehler beim Beenden der Session: {e}", exc_info=True)
            st.error(f"❌ Fehler beim Beenden der Session: {e}")
    
    def _build_session_context(self, user_query: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """
        Baut VOLLSTÄNDIGEN Session-Kontext für wellbeing_chat().
        
        ✅ PHASE 6B: Delegiert an SessionContextBuilder.
        
        Args:
            user_query: Aktuelle User-Anfrage für Relevanz-Filtering (optional)
        
        Returns:
            Dictionary mit vollständigem, relevantem Kontext oder None
        """
        return self.session_context_builder.build_session_context(user_query=user_query)
    
    
    def _load_session_history_into_chat_logic(self, session_messages: List[Dict[str, Any]]):
        """
        Lädt Session-Historie in die message_history der chat_logic
        
        Damit wellbeing_chat() die vollständige Gesprächshistorie hat!
        
        Args:
            session_messages: Liste von Session-Nachrichten aus der DB
        """
        try:
            if not self.chat_logic:
                logger.warning("⚠️ chat_logic nicht verfügbar, Historie kann nicht geladen werden")
                return
            
            # ✅ Setze message_history mit Session-Nachrichten
            self.chat_logic.message_history = []
            
            for msg in session_messages:
                self.chat_logic.message_history.append({
                    'role': msg.get('role', 'user'),
                    'content': msg.get('content', '')
                })
            
            logger.info(f"✅ {len(session_messages)} Session-Nachrichten in chat_logic.message_history geladen")
            
        except Exception as e:
            logger.error(f"❌ Fehler beim Laden der Session-Historie: {e}")
    
    def _load_session_history_into_chat_logic_fallback(self, session_messages: List[Dict[str, Any]], chat_logic_instance):
        """Fallback version für st.session_state.chat_logic"""
        try:
            if not chat_logic_instance:
                return
            
            chat_logic_instance.message_history = []
            
            for msg in session_messages:
                chat_logic_instance.message_history.append({
                    'role': msg.get('role', 'user'),
                    'content': msg.get('content', '')
                })
            
            logger.info(f"✅ {len(session_messages)} Session-Nachrichten in fallback chat_logic geladen")
        
        except Exception as e:
            logger.error(f"❌ Fehler beim Laden der Session-Historie (fallback): {e}")
    
    def _build_comprehensive_user_context(self, user_id: str, current_session_id: str, 
                                          user_input: str) -> Dict[str, Any]:
        """
        ✅ PHASE 6A: Wrapper für den neuen WellbeingContextBuilder
        
        Delegates to the refactored, type-safe context builder in
        wellbeing_session/context/context_builder.py
        
        Args:
            user_id: User-ID
            current_session_id: Aktuelle Session-ID

            user_input: Aktuelle Benutzer-Nachricht (für relevante KG-Retrieval)
            
        Returns:
            Umfassendes User-Profil für LLM-Kontext
        """
        logger.info(f"🔧 [BUILD-CONTEXT] WRAPPER - Delegating to Phase 6a context builder")

        self._ensure_wellbeing_rag_bootstrapped()
        
        # Create request with correct parameter names
        request = ContextBuildRequest(
            user_id=user_id,
            current_session_id=current_session_id,
            user_input=user_input,
            use_v2_builder=self.use_v2_context_builder
        )
        
        # Build context using Phase 6a builder
        result: ContextBuildResult = self.context_builder.build(request)
        
        # Return the context dict (already in legacy format)
        context = result.context
        
        logger.info(
            f"✅ [BUILD-CONTEXT] Complete: "
            f"{len(result.sources_used)} sources, {result.token_estimate} tokens, "
            f"duration={result.duration_ms:.1f}ms, version={result.builder_version}"
        )
        
        return context
    
    def _extract_keywords_from_input(self, user_input: str) -> List[str]:
        """Extrahiert relevante Keywords aus User-Input für KG-Suche"""
        # Einfache Keyword-Extraktion (kann später durch NLP verbessert werden)
        stopwords = {
            'ich', 'du', 'er', 'sie', 'es', 'wir', 'ihr', 'und', 'oder', 'aber',
            'der', 'die', 'das', 'ein', 'eine', 'ist', 'sind', 'war', 'waren',
            'haben', 'hat', 'hatte', 'kann', 'könnte', 'sollte', 'würde',
            'mir', 'mich', 'mein', 'meine', 'dein', 'deine', 'sein', 'seine'
        }
        
        # Splitte und filtere
        words = user_input.lower().split()
        keywords = [
            w.strip('.,!?;:') 
            for w in words 
            if len(w) > 3 and w.lower() not in stopwords
        ]
        
        # Entferne Duplikate, behalte Reihenfolge
        seen = set()
        unique_keywords = []
        for kw in keywords:
            if kw not in seen:
                seen.add(kw)
                unique_keywords.append(kw)
        
        return unique_keywords[:10]  # Max 10 Keywords
    
    # ============================================================================
    # 🆕 ENHANCED CONTEXT METHODS - Optimale Nutzung aller verfügbaren Daten
    # ============================================================================
    
    def _calculate_relevance_score(self, triple: Dict[str, Any], current_time: Optional[datetime] = None) -> float:
        """
        Calculate combined relevance score from multiple factors.
        
        🔥 REFACTORED: Now uses calculate_relevance_score from wellbeing_session.context
        
        Args:
            triple: KG triple with similarity, confidence, source_date
            current_time: Current time for temporal decay
            
        Returns:
            Combined score between 0.0 and 1.0
        """
        if current_time is None:
            from datetime import timezone
            current_time = datetime.now(timezone.utc)
        return calculate_relevance_score(triple, current_time)
    
    def _adaptive_triple_selection(self, triples: List[Dict[str, Any]], 
                                   min_relevance: float = 0.4,
                                   max_triples: int = 40,
                                   similarity_drop_threshold: float = 0.15) -> List[Dict[str, Any]]:
        """
        Select triples adaptively based on relevance scores.
        
        🔥 REFACTORED: Now uses adaptive_triple_selection from wellbeing_session.context
        
        Args:
            triples: List of KG triples
            min_relevance: Minimum relevance score
            max_triples: Maximum number
            similarity_drop_threshold: Max allowed drop
            
        Returns:
            Filtered triple list
        """
        return adaptive_triple_selection(triples, min_relevance, max_triples, similarity_drop_threshold)
    
    def _extract_user_insights(self, triples: List[Dict[str, Any]], user_input: str = "") -> List[Dict[str, Any]]:
        """
        LLM-basierte Insight-Extraktion aus KG-Triples.
        
        ✅ PHASE 6B: Delegiert an UserInsightExtractor.
        
        Args:
            triples: Liste von KG Triples
            user_input: Aktuelle User-Nachricht für Kontext
            
        Returns:
            Liste von Insight-Dicts mit insight, confidence, type
        """
        return self.user_insight_extractor_module.extract_user_insights(triples, user_input)
    
    def _extract_user_insights_fallback(self, triples: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Einfacher Fallback ohne LLM – nur Häufigkeitsanalyse.
        
        ✅ PHASE 6B: Delegiert an UserInsightExtractor.
        """
        return self.user_insight_extractor_module.extract_user_insights_fallback(triples)
    
    
    def _get_triples_for_mood_context(self, mood_data: Dict[str, Any], 
                                      all_triples: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        LLM-basierte Auswahl von Triples die zum Mood-Kontext passen.
        
        ✅ PHASE 6B: Delegiert an ContextFormatter.
        
        Args:
            mood_data: Mood Progression Daten
            all_triples: Alle verfügbaren Triples
            
        Returns:
            Liste von Triples die Mood-Kontext erklären könnten
        """
        return self.context_formatter.get_triples_for_mood_context(mood_data, all_triples)
    
    def _rank_summaries_by_relevance(self, summaries: List[Dict[str, Any]], 
                                     user_input: str,
                                     max_summaries: int = 5) -> List[Dict[str, Any]]:
        """
        Rank session summaries by relevance using embeddings.
        
        🔥 REFACTORED: Now uses rank_summaries_by_relevance from wellbeing_session.context
        """
        return rank_summaries_by_relevance(summaries, user_input, max_summaries)
    
    def _get_goal_progress_triples(self, goal_text: str, 
                                   all_triples: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Embedding-basierte Erkennung von Goal-relevanten Triples.
        
        ✅ PHASE 6B: Delegiert an ContextFormatter.
        
        Args:
            goal_text: Text des Care-Ziels
            all_triples: Alle verfügbaren Triples
            
        Returns:
            Liste von Triples die zum Ziel passen
        """
        return self.context_formatter.get_goal_progress_triples(goal_text, all_triples)
    
    
    def _format_context_for_llm(self, context: Dict[str, Any]) -> str:
        """
        Formatiert den umfassenden User-Kontext für LLM-Prompt.
        
        ✅ PHASE 6B: Delegiert an ContextFormatter.
        
        Args:
            context: User-Kontext aus _build_comprehensive_user_context()
            
        Returns:
            Formatierter String für LLM-Prompt
        """
        return self.context_formatter.format_context_for_llm(context)

