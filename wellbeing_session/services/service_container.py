"""
Service Container — Lightweight DI container for the psychological session system.

Responsibilities:
1. Centralises ALL dependency wiring in one place (no scattered ``try/except`` imports).
2. Provides lazy-initialisation so expensive services (profile cache, V2 context builder)
   are only created when first accessed.
3. Exposes ``update_chat_logic()`` / ``update_model_loader()`` to propagate late-bound
   dependencies (LLM handle, model loader) to every component that needs them.

Design decisions:
- **No 3rd-party DI framework** — keeps the footprint minimal and avoids extra deps.
- **Protocol-based contracts** — see ``protocols.py``.
- **Streamlit ``st.session_state`` caching** stays in the ``_get_or_create_session_manager``
  helper so the container itself is Streamlit-agnostic for unit-testing.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Configuration value-object
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ServiceConfig:
    """Immutable configuration for the service container.

    All magic numbers from the original ``__init__`` are captured here so
    they can be overridden in tests or different environments.
    """

    # Context window budgets (tokens)
    total_context_tokens: int = 12288
    system_messages_tokens: int = 1000
    session_summary_tokens: int = 1500
    current_prompt_tokens: int = 500
    buffer_tokens: int = 1024
    min_recent_messages: int = 6

    # Profile cache
    profile_cache_ttl_minutes: int = 30
    profile_cache_max_size: int = 100

    # V2 context builder
    v2_context_builder_enabled: bool = True
    v2_kg_max_triples: int = 50
    v2_max_sessions: int = 10
    v2_max_mood_triples: int = 20
    v2_max_goals: int = 10
    v2_max_insights: int = 15

    # Feature flags
    enable_monitoring: bool = True

    @property
    def available_history_tokens(self) -> int:
        """Tokens available for chat history after reserving system/summary/prompt/buffer."""
        return (
            self.total_context_tokens
            - self.system_messages_tokens
            - self.session_summary_tokens
            - self.current_prompt_tokens
            - self.buffer_tokens
        )


# ---------------------------------------------------------------------------
# Feature-availability singletons (cached at module level on first import)
# ---------------------------------------------------------------------------

def _probe_imports() -> Dict[str, bool]:
    """Probe optional imports once and cache the results."""
    avail: Dict[str, bool] = {}

    # Pydantic V2
    try:
        from models_pydantic_v2 import SessionSummaryModel, SessionMessageModel, EmotionalState  # noqa: F401
        from pydantic_migration_adapter import legacy_to_pydantic, pydantic_to_legacy_dict, adapt_session_message  # noqa: F401
        avail["pydantic"] = True
    except ImportError:
        avail["pydantic"] = False

    # Profile cache
    try:
        from wellbeing.profile_cache_manager import ProfileCacheManager  # noqa: F401
        avail["profile_cache"] = True
    except ImportError:
        avail["profile_cache"] = False

    # Structured outputs
    try:
        from llm_structured_wrapper import LLMStructuredWrapper  # noqa: F401
        from llm_output_schemas import EmotionalAnalysisOutput, InsightsExtractionOutput  # noqa: F401
        avail["structured_outputs"] = True
    except ImportError:
        avail["structured_outputs"] = False

    # User context builder V2
    try:
        from user_context_builder import UserContextBuilder, UserContextRequest  # noqa: F401
        from user_context_builder.providers import (  # noqa: F401
            KnowledgeGraphProvider,
            SessionSummariesProvider,
            MoodProgressionProvider,
            CareGoalsProvider,
            PersistentProfileProvider,
            UserInsightsProvider,
        )
        avail["v2_context_builder"] = True
    except ImportError:
        avail["v2_context_builder"] = False

    # Monitoring
    try:
        from monitoring.context_builder_monitor import get_monitor  # noqa: F401
        avail["monitoring"] = True
    except ImportError:
        avail["monitoring"] = False

    # User insight extractor (external)
    try:
        from wellbeing_user_insight_extractor import WellbeingUserInsightExtractor  # noqa: F401
        avail["user_insight_extractor"] = True
    except ImportError:
        avail["user_insight_extractor"] = False

    return avail


# Module-level cache
_FEATURE_AVAILABILITY: Optional[Dict[str, bool]] = None


def get_feature_availability() -> Dict[str, bool]:
    """Return cached feature-availability dict (probed once)."""
    global _FEATURE_AVAILABILITY
    if _FEATURE_AVAILABILITY is None:
        _FEATURE_AVAILABILITY = _probe_imports()
        logger.info(f"🔍 Feature availability probed: {_FEATURE_AVAILABILITY}")
    return _FEATURE_AVAILABILITY


# ---------------------------------------------------------------------------
# Service Container
# ---------------------------------------------------------------------------

class ServiceContainer:
    """Lightweight DI container that owns and wires all service instances.

    Usage::

        config = ServiceConfig(total_context_tokens=8192)
        container = ServiceContainer(config)
        container.build()  # creates all services

        # Late-bind LLM
        container.update_chat_logic(my_chat_logic)

        # Access services
        handler = container.message_handler
    """

    def __init__(self, config: Optional[ServiceConfig] = None) -> None:
        self.config: ServiceConfig = config or ServiceConfig()
        self._features: Dict[str, bool] = get_feature_availability()

        # Core services (always available)
        self.session_manager: Any = None
        self.context_manager: Any = None
        self.emotional_analyzer: Any = None

        # Handlers
        self.message_handler: Any = None
        self.chat_input_handler: Any = None
        self.response_generator: Any = None

        # UI renderers
        self.session_management_renderer: Any = None
        self.active_session_renderer: Any = None
        self.welcome_renderer: Any = None

        # Lifecycle & context
        self.lifecycle_manager: Any = None
        self.context_builder: Any = None
        self.context_builder_v2: Any = None

        # Phase 6b sub-components
        self.session_context_builder: Any = None
        self.user_insight_extractor_module: Any = None
        self.context_formatter: Any = None

        # Optional services
        self.profile_cache: Any = None
        self.insight_extractor: Any = None
        self.chat_logic: Any = None

        # Startup service (deferred)
        self._startup_service: Any = None

        # Feature flags (resolved from config + availability)
        self.use_v2_context_builder: bool = (
            self.config.v2_context_builder_enabled
            and self._features.get("v2_context_builder", False)
        )

    # ------------------------------------------------------------------
    # Build
    # ------------------------------------------------------------------

    def build(self, session_manager: Optional[Any] = None) -> "ServiceContainer":
        """Create and wire all services.

        Args:
            session_manager: Pre-built ``SessionManagerAdapter`` (e.g. from
                ``st.session_state``).  If *None*, a fresh one is created.

        Returns:
            ``self`` for fluent chaining.
        """
        self._build_session_manager(session_manager)
        self._build_profile_cache()
        self._build_context_manager()
        self._build_emotional_analyzer()
        self._build_handlers()
        self._build_ui_renderers()
        self._build_v2_context_builder()     # ✅ FIX: V2 builder BEFORE context builder
        self._build_context_builders()       # ✅ FIX: Now can receive v2_builder
        self._build_lifecycle_manager()
        self._build_phase6b_components()
        self._build_insight_extractor()

        logger.info("✅ ServiceContainer: all services built successfully")
        return self

    # ------------------------------------------------------------------
    # Late-binding updates
    # ------------------------------------------------------------------

    def update_chat_logic(self, chat_logic: Any) -> None:
        """Propagate the chat-logic handle to every component that needs it."""
        self.chat_logic = chat_logic

        # Emotional analyzer
        if self.emotional_analyzer is not None:
            if hasattr(self.emotional_analyzer, "set_chat_logic"):
                self.emotional_analyzer.set_chat_logic(chat_logic)
            else:
                self.emotional_analyzer.chat_logic = chat_logic
                self.emotional_analyzer.model_loader = getattr(chat_logic, "model_loader", None)

        # Handlers
        for handler in (self.message_handler, self.chat_input_handler, self.response_generator):
            if handler is not None:
                handler.chat_logic = chat_logic

        # Phase 6b
        if self.user_insight_extractor_module is not None:
            self.user_insight_extractor_module.chat_logic = chat_logic
        if self.context_formatter is not None:
            self.context_formatter.chat_logic = chat_logic

        # External insight extractor
        if self.insight_extractor is not None and chat_logic is not None:
            # ✅ SOTA FIX: Direkter model_loader-Aufruf statt chat_logic.chat()!
            # Vorher: chat_logic.chat(prompt, use_web_search=False, use_agent_toolkit=False)
            #   Problem 1: use_web_search/use_agent_toolkit existieren NICHT als Parameter
            #     von AgentChatbotLogic.chat() → werden ignoriert oder verursachen Fehler
            #   Problem 2: chat_logic.chat() leitet durch volle Agent-Pipeline
            #     (Planner → Tool-Bridge → Summarizer → Verifier → Hybrid Reasoning)
            #     → ~9s Overhead pro Insight-Extraction, Planner-Logs in Psycho-Context
            # Jetzt: model_loader.generate_response() direkt → reine LLM-Textgenerierung
            #   in ~1-2s, KEINE Pipeline, KEINE ungültigen kwargs.
            model_loader = getattr(chat_logic, 'model_loader', None)
            if model_loader and hasattr(model_loader, 'generate_response'):
                def _chat_wrapper(prompt: str) -> str:
                    try:
                        # Adaptive token budget: insight JSON can be large.
                        # ~1 output char per input char is a safe upper bound;
                        # clamp to [2048, 6144] so short prompts don't over-allocate
                        # and long prompts (full conversation + profile) get enough room.
                        _budget = max(2048, min(6144, len(prompt)))
                        return str(model_loader.generate_response(
                            messages=[{'role': 'user', 'content': prompt}],
                            max_tokens=_budget,
                            temperature=0.3,
                        ))
                    except Exception as exc:
                        logger.error(f"❌ chat_function_wrapper error: {exc}")
                        return ""
                self.insight_extractor.chat_function = _chat_wrapper
            else:
                logger.warning("⚠️ model_loader nicht verfügbar für insight_extractor chat_function")

        # Model loader via chat_logic
        if chat_logic is not None and hasattr(chat_logic, "model_loader"):
            self.update_model_loader(chat_logic.model_loader)

        logger.info("🔗 ServiceContainer: chat_logic propagated to all components")

    def update_model_loader(self, model_loader: Any) -> None:
        """Propagate model-loader to session manager, profile cache, and context manager."""
        if self.session_manager is not None and hasattr(self.session_manager, "set_model_loader"):
            self.session_manager.set_model_loader(model_loader)

        if self.emotional_analyzer is not None:
            if hasattr(self.emotional_analyzer, "set_model_loader"):
                self.emotional_analyzer.set_model_loader(model_loader)
            else:
                self.emotional_analyzer.model_loader = model_loader

        if self.profile_cache is not None and self._features.get("profile_cache", False):
            self._lazy_init_profile_synthesizer(model_loader)

        # ✅ PHASE 9b: Update recursive summarizer in context manager
        if self.context_manager is not None and hasattr(self.context_manager, '_recursive_summarizer'):
            if self.context_manager._recursive_summarizer is not None:
                self.context_manager._recursive_summarizer.model_loader = model_loader
                self.context_manager._recursive_summarizer.scorer.model_loader = model_loader
                logger.info("🔗 ServiceContainer: model_loader propagated to recursive summarizer")
            else:
                # Try to create the summarizer now that we have a model_loader
                try:
                    from wellbeing_session.workflow.recursive_summarizer import RecursiveLLMSummarizer
                    self.context_manager._recursive_summarizer = RecursiveLLMSummarizer(
                        model_loader=model_loader,
                        chunk_size=8,
                        max_summary_tokens=self.config.session_summary_tokens,
                        salience_threshold=4,
                    )
                    logger.info("✅ ServiceContainer: RecursiveLLMSummarizer late-initialized")
                except ImportError:
                    pass

        logger.info("🔗 ServiceContainer: model_loader propagated")

    # ------------------------------------------------------------------
    # Startup service (lazy)
    # ------------------------------------------------------------------

    @property
    def startup_service(self) -> Any:
        """Return (and lazily create) the startup service."""
        if self._startup_service is None:
            from wellbeing_session.services.startup_service import StartupService
            self._startup_service = StartupService(session_manager=self.session_manager)
        return self._startup_service

    # ------------------------------------------------------------------
    # Private builders
    # ------------------------------------------------------------------

    def _build_session_manager(self, existing: Optional[Any] = None) -> None:
        if existing is not None:
            self.session_manager = existing
            return

        from wellbeing.session_manager import WellbeingSessionManager
        from wellbeing_session.adapters import SessionManagerAdapter

        psych_manager = WellbeingSessionManager(
            db=None,
            privacy_handler=None,
            model_loader=None,
        )
        self.session_manager = SessionManagerAdapter(psych_manager)
        logger.info("🔄 ServiceContainer: SessionManager created")

    def _build_profile_cache(self) -> None:
        if not self._features.get("profile_cache", False):
            self.profile_cache = None
            return

        try:
            from wellbeing.profile_cache_manager import ProfileCacheManager

            wellbeing_db = (
                self.session_manager.manager.db
                if hasattr(self.session_manager, "manager")
                else None
            )
            if wellbeing_db is not None:
                self.profile_cache = ProfileCacheManager(
                    wellbeing_db=wellbeing_db,
                    profile_synthesizer=None,  # lazy — set when model_loader arrives
                    ttl_minutes=self.config.profile_cache_ttl_minutes,
                    max_cache_size=self.config.profile_cache_max_size,
                )
                # Inject back into DB for KG-update → profile invalidation
                if hasattr(wellbeing_db, 'set_profile_cache'):
                    wellbeing_db.set_profile_cache(self.profile_cache)
                logger.info("✅ ServiceContainer: ProfileCacheManager created (lazy synthesizer)")
            else:
                self.profile_cache = None
                logger.warning("⚠️ ServiceContainer: no wellbeing_db → profile cache disabled")
        except Exception as exc:
            self.profile_cache = None
            logger.warning(f"⚠️ ServiceContainer: ProfileCacheManager init failed: {exc}")

    def _build_context_manager(self) -> None:
        from chat_context_manager import ChatContextManager

        self.context_manager = ChatContextManager(
            max_context_tokens=self.config.available_history_tokens,
            system_prompt_tokens=self.config.system_messages_tokens,
            summary_target_tokens=self.config.session_summary_tokens,
            min_recent_messages=self.config.min_recent_messages,
        )

    def _build_emotional_analyzer(self) -> None:
        from emotional_analyzer import LLMEmotionalAnalyzer

        self.emotional_analyzer = LLMEmotionalAnalyzer()

    def _build_handlers(self) -> None:
        from wellbeing_session.handlers import (
            MessageHandler,
            ChatInputHandler,
            ResponseGenerator,
        )

        self.message_handler = MessageHandler(
            session_manager=self.session_manager,
            emotional_analyzer=self.emotional_analyzer,
            chat_logic=self.chat_logic,
            profile_cache=self.profile_cache,
        )
        self.chat_input_handler = ChatInputHandler(
            session_manager=self.session_manager,
            emotional_analyzer=self.emotional_analyzer,
            context_manager=self.context_manager,
            chat_logic=self.chat_logic,
        )
        self.response_generator = ResponseGenerator(
            session_manager=self.session_manager,
            context_manager=self.context_manager,
            chat_logic=self.chat_logic,
        )
        logger.info("✅ ServiceContainer: handlers created")

    def _build_ui_renderers(self) -> None:
        from wellbeing_session.ui import (
            SessionManagementRenderer,
            ActiveSessionRenderer,
            WelcomeRenderer,
        )

        self.session_management_renderer = SessionManagementRenderer(
            session_manager=self.session_manager,
            profile_cache=self.profile_cache,
        )
        self.active_session_renderer = ActiveSessionRenderer(
            session_manager=self.session_manager,
            context_manager=self.context_manager,
        )
        self.welcome_renderer = WelcomeRenderer()
        logger.info("✅ ServiceContainer: UI renderers created")

    def _build_context_builders(self) -> None:
        from wellbeing_session.context import (
            create_context_builder,
        )

        self.context_builder = create_context_builder(
            session_manager=self.session_manager,
            profile_cache=self.profile_cache,
            use_v2=self.use_v2_context_builder,
            enable_monitoring=self.config.enable_monitoring,
            v2_builder=self.context_builder_v2,  # ✅ FIX: Pass pre-configured V2 builder
        )
        logger.info(
            f"✅ ServiceContainer: context builder created "
            f"(v2={self.use_v2_context_builder}, monitoring={self.config.enable_monitoring})"
        )

    def _build_lifecycle_manager(self) -> None:
        from wellbeing_session.lifecycle import SessionLifecycleManager

        self.lifecycle_manager = SessionLifecycleManager(
            session_manager=self.session_manager,
            insight_extractor=self.insight_extractor,
        )
        logger.info("✅ ServiceContainer: lifecycle manager created")

    def _build_phase6b_components(self) -> None:
        from wellbeing_session.context import (
            SessionContextBuilder,
            UserInsightExtractor,
            ContextFormatter,
        )

        # Resolve structured-output classes (may be None)
        llm_wrapper_cls: Any = None
        insights_cls: Any = None
        so_available = self._features.get("structured_outputs", False)
        if so_available:
            try:
                from llm_structured_wrapper import LLMStructuredWrapper as _W
                from llm_output_schemas import InsightsExtractionOutput as _I
                llm_wrapper_cls = _W
                insights_cls = _I
            except ImportError:
                so_available = False

        self.session_context_builder = SessionContextBuilder(
            session_manager=self.session_manager,
            profile_cache=self.profile_cache,
        )
        self.user_insight_extractor_module = UserInsightExtractor(
            chat_logic=self.chat_logic,
            structured_outputs_available=so_available,
            llm_structured_wrapper_cls=llm_wrapper_cls,
            insights_extraction_output_cls=insights_cls,
        )
        self.context_formatter = ContextFormatter(chat_logic=self.chat_logic)
        logger.info("✅ ServiceContainer: Phase 6b components created")

    def _build_insight_extractor(self) -> None:
        if not self._features.get("user_insight_extractor", False):
            self.insight_extractor = None
            return

        try:
            from wellbeing_user_insight_extractor import WellbeingUserInsightExtractor

            self.insight_extractor = WellbeingUserInsightExtractor(
                session_manager=self.session_manager,
                wellbeing_db=None,
                chat_function=None,  # set when chat_logic is available
            )
            logger.info("✅ ServiceContainer: WellbeingUserInsightExtractor created")
        except Exception as exc:
            self.insight_extractor = None
            logger.warning(f"⚠️ ServiceContainer: insight extractor init failed: {exc}")

    def _build_v2_context_builder(self) -> None:
        if not self.use_v2_context_builder:
            self.context_builder_v2 = None
            return

        try:
            from user_context_builder import UserContextBuilder
            from user_context_builder.providers import (
                KnowledgeGraphProvider,
                SessionSummariesProvider,
                MoodProgressionProvider,
                CareGoalsProvider,
                PersistentProfileProvider,
                UserInsightsProvider,
            )

            cfg = self.config
            providers: Any = [
                KnowledgeGraphProvider(max_triples=cfg.v2_kg_max_triples, priority=10),
                SessionSummariesProvider(max_sessions=cfg.v2_max_sessions, priority=20),
                MoodProgressionProvider(max_mood_triples=cfg.v2_max_mood_triples, priority=30),
                CareGoalsProvider(max_goals=cfg.v2_max_goals, priority=40),
                UserInsightsProvider(max_insights=cfg.v2_max_insights, priority=45),
                PersistentProfileProvider(profile_cache=self.profile_cache, priority=50),
            ]
            self.context_builder_v2 = UserContextBuilder(providers=providers)
            logger.info("✅ ServiceContainer: V2 context builder created with 6 providers")
        except Exception as exc:
            self.context_builder_v2 = None
            self.use_v2_context_builder = False
            logger.warning(f"⚠️ ServiceContainer: V2 context builder init failed: {exc}")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _lazy_init_profile_synthesizer(self, model_loader: Any) -> None:
        """Attach a ``ProfileSynthesizer`` to the profile cache when the model loader becomes available."""
        if self.profile_cache is None:
            return
        if getattr(self.profile_cache, "synthesizer", None) is not None:
            return  # already initialised

        try:
            from wellbeing.profile_synthesizer import ProfileSynthesizer

            wellbeing_db = (
                self.session_manager.manager.db
                if hasattr(self.session_manager, "manager")
                else None
            )
            if wellbeing_db is not None:
                self.profile_cache.synthesizer = ProfileSynthesizer(
                    wellbeing_db=wellbeing_db,
                    model_loader=model_loader,
                )
                logger.info("✅ ServiceContainer: ProfileSynthesizer lazy-initialized")
        except Exception as exc:
            logger.warning(f"⚠️ ServiceContainer: ProfileSynthesizer init failed: {exc}")
