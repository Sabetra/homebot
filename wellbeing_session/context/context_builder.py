"""
Psychological Context Builder - Main Orchestrator.

This module provides a clean abstraction for building comprehensive user context
in psychological support sessions. Supports both legacy (monolithic) and V2 (modular)
building strategies.

Features:
- Dual-mode operation (legacy/V2)
- Type-safe with Pydantic V2 models
- Performance monitoring integration
- Graceful degradation on errors
- Dependency injection ready
- 100% backward compatible

Usage:
    >>> from wellbeing_session.context.context_builder import create_context_builder
    >>> from wellbeing_session.context.models import ContextBuildRequest
    >>> 
    >>> builder = create_context_builder(session_manager, use_v2=False)
    >>> request = ContextBuildRequest(
    ...     user_id="user_123",
    ...     current_session_id="session_456",
    ...     user_input="I feel anxious"
    ... )
    >>> result = builder.build(request)
    >>> print(result.context['knowledge_graph'])
"""

import time
import logging
from typing import Dict, Any, Optional, Protocol, List

from .models import ContextBuildRequest, ContextBuildResult

logger = logging.getLogger(__name__)


# ============================================================================
# PROTOCOL DEFINITIONS (for Dependency Injection)
# ============================================================================

class SessionManagerProtocol(Protocol):
    """
    Protocol defining the expected interface for session managers.
    
    This allows dependency injection while maintaining type safety.
    """
    
    @property
    def manager(self) -> Any:
        """Access to the underlying session manager."""
        ...


# ============================================================================
# MAIN CONTEXT BUILDER CLASS
# ============================================================================

class WellbeingContextBuilder:
    """
    Orchestrates building comprehensive user context for psychological sessions.
    
    This class provides a unified interface for building user context, supporting
    both the legacy monolithic approach and the new V2 modular provider system.
    
    The builder automatically detects V2 availability and falls back to legacy
    mode if necessary. All operations are type-safe and validated with Pydantic V2.
    
    Attributes:
        session_manager: Session management adapter for data access
        use_v2_by_default: Whether to prefer V2 builder when available
        enable_monitoring: Whether to track build metrics
    
    Examples:
        >>> builder = WellbeingContextBuilder(session_manager)
        >>> request = ContextBuildRequest(
        ...     user_id="user_123",
        ...     current_session_id="session_456",
        ...     user_input="How can I manage stress?"
        ... )
        >>> result = builder.build(request)
        >>> print(f"Built context in {result.duration_ms:.2f}ms")
        Built context in 145.67ms
    """
    
    def __init__(
        self,
        session_manager: SessionManagerProtocol,
        profile_cache: Optional[Any] = None,
        use_v2_by_default: bool = False,
        enable_monitoring: bool = True,
        v2_builder: Optional[Any] = None
    ) -> None:
        """
        Initialize the psychological context builder.
        
        Args:
            session_manager: Session management adapter providing data access
            profile_cache: ProfileCacheManager instance for persistent profile retrieval
            use_v2_by_default: Default to V2 builder if available (default: False)
            enable_monitoring: Track build metrics for monitoring (default: True)
            v2_builder: Pre-configured UserContextBuilder instance with registered providers.
                If None and V2 is requested, providers are created inline.
        """
        self.session_manager = session_manager
        self.profile_cache = profile_cache
        self.use_v2_by_default = use_v2_by_default
        self.enable_monitoring = enable_monitoring
        self._v2_builder = v2_builder
        
        # Check V2 availability at initialization
        self._v2_available = self._check_v2_availability()
        logger.info(f"Context builder initialized: V2_available={self._v2_available}, "
                   f"use_v2_default={use_v2_by_default}")
        
        # Setup monitoring if available and enabled
        self._monitor: Optional[Any] = None
        if enable_monitoring:
            self._monitor = self._setup_monitoring()
    
    def _check_v2_availability(self) -> bool:
        """
        Check if V2 modular builder components are available.
        
        Returns:
            True if V2 components can be imported, False otherwise
        """
        try:
            from user_context_builder import UserContextBuilder, UserContextRequest
            logger.info("✅ V2 context builder imports successful")
            return True
        except ImportError as e:
            logger.warning(f"⚠️ V2 context builder not available: {e}")
            return False
    
    def _setup_monitoring(self) -> Optional[Any]:
        """
        Setup monitoring system if available.
        
        Returns:
            Monitor instance if available, None otherwise
        """
        try:
            from monitoring.context_builder_monitor import get_monitor
            monitor = get_monitor()
            logger.info("✅ Monitoring system initialized")
            return monitor
        except ImportError as e:
            logger.debug(f"Monitoring not available: {e}")
            return None
        except Exception as e:
            logger.warning(f"⚠️ Failed to initialize monitoring: {e}")
            return None
    
    def build(self, request: ContextBuildRequest) -> ContextBuildResult:
        """
        Build comprehensive user context based on request parameters.
        
        This is the main entry point for context building. It automatically
        selects the appropriate builder (legacy or V2) based on availability
        and request preferences.
        
        The method tracks performance metrics and handles errors gracefully,
        always returning a valid ContextBuildResult even if the build fails.
        
        Args:
            request: Context build parameters (validated Pydantic model)
        
        Returns:
            ContextBuildResult with context data and build metadata
        
        Raises:
            ValueError: If request validation fails (handled by Pydantic)
            Exception: If critical build error occurs (logged and re-raised)
        
        Examples:
            >>> request = ContextBuildRequest(
            ...     user_id="user_123",
            ...     current_session_id="session_456",
            ...     user_input="I need help with anxiety"
            ... )
            >>> result = builder.build(request)
            >>> if result.success:
            ...     context = result.context
            ...     print(f"Built with {result.builder_version} in {result.duration_ms}ms")
        """
        start_time = time.time()
        
        # Determine which builder to use
        use_v2 = request.use_v2_builder and self._v2_available
        builder_version = "v2" if use_v2 else "legacy"
        
        logger.info(f"🏗️ Building context: version={builder_version}, "
                   f"user={request.user_id[:12]}..., "
                   f"session={request.current_session_id[:12]}...")
        
        try:
            # Build context using selected builder
            if use_v2:
                context = self._build_v2_context(request)
            else:
                context = self._build_legacy_context(request)
            
            # Calculate metrics
            duration_ms = (time.time() - start_time) * 1000
            token_estimate = self._estimate_tokens(context)
            sources_used = self._extract_sources(context)
            
            # Create successful result
            result = ContextBuildResult(
                context=context,
                builder_version=builder_version,
                duration_ms=duration_ms,
                token_estimate=token_estimate,
                sources_used=sources_used,
                success=True,
                error=None
            )
            
            logger.info(f"✅ Context built: {duration_ms:.2f}ms, "
                       f"{token_estimate} tokens, "
                       f"{len(sources_used)} sources")
            
            # Track metrics if monitoring enabled
            if self._monitor:
                self._track_build(request, result)
            
            return result
            
        except Exception as e:
            duration_ms = (time.time() - start_time) * 1000
            
            logger.error(f"❌ Context build failed: {e}", exc_info=True)
            
            # Create error result
            result = ContextBuildResult(
                context={},
                builder_version=builder_version,
                duration_ms=duration_ms,
                token_estimate=0,
                sources_used=[],
                success=False,
                error=str(e)
            )
            
            # Track error metrics
            if self._monitor:
                self._track_build(request, result)
            
            # Re-raise for caller to handle
            raise

    def _extract_sources(self, context: Dict[str, Any]) -> List[str]:
        """Extract list of data sources that provided data."""
        sources: List[str] = []
        
        if context.get('knowledge_graph'):
            sources.append('knowledge_graph')
        if context.get('session_summaries') or context.get('previous_sessions'):
            sources.append('session_summaries')
        if context.get('mood_progression'):
            sources.append('mood_progression')
        if context.get('care_goals'):
            sources.append('care_goals')
        if context.get('user_insights'):
            sources.append('user_insights')
        if context.get('persistent_profile'):
            sources.append('persistent_profile')
        
        return sources
    
    def _build_v2_context(self, request: ContextBuildRequest) -> Dict[str, Any]:
        """
        Build context using V2 modular provider system.
        
        This method delegates to the UserContextBuilder V2 system which uses
        independent providers for each data source (knowledge graph, session
        summaries, mood progression, etc.).
        
        Args:
            request: Validated context build request
        
        Returns:
            Context dictionary in legacy-compatible format
        
        Raises:
            ImportError: If V2 components not available (shouldn't happen)
            Exception: If V2 build fails
        """
        from user_context_builder import UserContextBuilder, UserContextRequest
        
        logger.debug("Using V2 modular context builder")
        
        # ✅ CRITICAL FIX: Use pre-configured builder (with providers) instead of
        # creating a new empty UserContextBuilder(). The old code created
        # `builder = UserContextBuilder()` → 0 providers → 0 results.
        builder = self._v2_builder
        if builder is None:
            # Fallback: create with proper providers inline
            logger.warning("⚠️ V2 builder not pre-configured — creating with default providers")
            try:
                from user_context_builder.providers import (
                    KnowledgeGraphProvider,
                    SessionSummariesProvider,
                    MoodProgressionProvider,
                    CareGoalsProvider,
                    PersistentProfileProvider,
                )
                builder = UserContextBuilder(providers=[
                    KnowledgeGraphProvider(max_triples=100, priority=10),
                    SessionSummariesProvider(max_sessions=5, priority=20),
                    MoodProgressionProvider(max_mood_triples=20, priority=30),
                    CareGoalsProvider(max_goals=10, priority=40),
                    PersistentProfileProvider(priority=50),
                ])
            except Exception as prov_err:
                logger.error(f"❌ V2 provider creation failed: {prov_err}")
                builder = UserContextBuilder()
        
        # Create V2-compatible request
        v2_request = UserContextRequest(
            user_id=request.user_id,
            current_session_id=request.current_session_id,
            user_input=request.user_input,
            max_tokens=2000  # Default token budget
        )
        
        # Build context using V2 system
        v2_result = builder.build(v2_request, self.session_manager)
        
        # Convert to legacy-compatible format
        context = v2_result.to_dict()
        context['current_user_input'] = request.user_input
        context['treatment_plan'] = self._gather_treatment_context(
            request.user_id,
            request.current_session_id,
        )
        
        logger.debug(f"V2 build complete: {len(v2_result.sources_used)} sources used")
        
        return context
    
    def _build_legacy_context(self, request: ContextBuildRequest) -> Dict[str, Any]:
        """
        Build context using legacy monolithic approach.
        
        This method implements the original context building logic with all
        data sources gathered in a single function. While less modular than V2,
        it remains stable and well-tested.
        
        Args:
            request: Validated context build request
        
        Returns:
            Context dictionary with all gathered data
        
        Raises:
            Exception: If critical error occurs (logged but gracefully handled)
        """
        logger.debug("Using legacy context builder")

        user_id = request.user_id
        current_session_id = request.current_session_id
        user_input = request.user_input
        
        # Initialize empty context structure
        context: Dict[str, Any] = {
            'current_user_input': user_input,
            'knowledge_graph': [],
            'session_summaries': [],
            'mood_progression': None,
            'care_goals': [],
            'user_insights': [],
            'persistent_profile': None,
            'context_token_estimate': 0
        }
        
        # === 1. KNOWLEDGE GRAPH RETRIEVAL ===
        context['knowledge_graph'] = self._gather_knowledge_graph(
            user_id, current_session_id, user_input
        )
        logger.debug(f"KG: {len(context['knowledge_graph'])} triples retrieved")
        
        # === 2. SESSION SUMMARIES ===
        context['session_summaries'] = self._gather_session_summaries(
            user_id, current_session_id, user_input
        )
        logger.debug(f"Summaries: {len(context['session_summaries'])} loaded")
        
        # === 3. MOOD PROGRESSION ===
        context['mood_progression'] = self._gather_mood_progression(current_session_id)
        logger.debug(f"Mood: {bool(context['mood_progression'])}")
        
        # === 4. CARE GOALS ===
        context['care_goals'] = self._gather_care_goals(current_session_id)
        logger.debug(f"Goals: {len(context['care_goals'])} loaded")

        # === 4b. SOTA TREATMENT PLAN CONTEXT ===
        context['treatment_plan'] = self._gather_treatment_context(
            user_id, current_session_id,
        )
        tp = context['treatment_plan']
        if tp:
            logger.debug(
                "CarePlan: %d active goals, focus=%s, stage=%s",
                len(tp.get('active_goals') or []),
                bool(tp.get('focus')),
                (tp.get('stage') or {}).get('stage'),
            )
        
        # === 5. PERSISTENT PROFILE ===
        context['persistent_profile'] = self._gather_persistent_profile(user_id)
        logger.debug(f"Profile: {bool(context['persistent_profile'])}")
        
        # === 6. TOKEN ESTIMATION ===
        context['context_token_estimate'] = self._calculate_token_estimate(context)
        
        logger.debug(f"Legacy build complete: ~{context['context_token_estimate']} tokens")
        
        return context
    
    # ========================================================================
    # LEGACY CONTEXT GATHERING METHODS
    # ========================================================================
    
    def _gather_knowledge_graph(
        self,
        user_id: str,
        current_session_id: str,
        user_input: str
    ) -> List[Dict[str, Any]]:
        """
        Gather knowledge graph triples using semantic search + family entity boost.
        
        Pipeline:
        1. Semantic search (FAISS + cross-encoder reranking)
        2. Family entity boost (targeted LIKE search for family/person references)
        3. Deduplication + adaptive selection
        """
        from wellbeing_session.context import adaptive_triple_selection
        from wellbeing_session.context.family_entity_boost import family_entity_kg_boost

        # Check DB availability
        if not hasattr(self.session_manager, 'manager') or \
           not hasattr(self.session_manager.manager, 'db'):
            raise RuntimeError("SessionManager.manager.db not available")

        db = self.session_manager.manager.db
        
        # Check semantic search method availability
        if not hasattr(db, 'search_knowledge_graph_semantic'):
            raise RuntimeError("search_knowledge_graph_semantic method not available")
        
        # ── Step 1: Semantic Search ──
        kg_results = db.search_knowledge_graph_semantic(
            query=user_input,
            user_id=user_id,
            session_id=None,
            limit=50,
            min_confidence=0.4,
            similarity_threshold=0.40
        )
        
        # Format results — PRESERVE all SOTA v4 scoring fields
        raw_triples: List[Dict[str, Any]] = []
        if kg_results:
            for triple in kg_results:
                raw_triples.append({
                    'subject': triple.get('subject'),
                    'predicate': triple.get('predicate'),
                    'object': triple.get('object'),
                    'confidence': triple.get('confidence', 0),
                    'similarity': triple.get('similarity', 0),
                    'combined_score': triple.get('combined_score', 0),
                    'rerank_score': triple.get('rerank_score', 0),
                    'entity_score': triple.get('entity_score', 0),
                    'source_date': triple.get('interaction_date') or \
                                  triple.get('created_at') or 'N/A'
                })

        # ── Step 1b: Baseline High-Confidence Triples ──
        # Semantic search uses similarity_threshold=0.40, which means personal facts
        # (family relationships, life events, personality patterns) are EXCLUDED when
        # the user's current question is semantically unrelated (e.g., meta-questions
        # like "do you have any info about me from previous sessions?").
        # Solution: always merge the top-N highest-confidence triples so that the LLM
        # always has the user's most important personal data in its context.
        if hasattr(db, 'get_high_confidence_triples'):
            baseline_raw = db.get_high_confidence_triples(
                user_id=user_id,
                limit=30,
                min_confidence=0.5,
            )
            if baseline_raw:
                existing_keys = {
                    (t.get('subject', ''), t.get('predicate', ''), t.get('object', ''))
                    for t in raw_triples
                }
                added = 0
                for bt in baseline_raw:
                    key = (bt.get('subject', ''), bt.get('predicate', ''), bt.get('object', ''))
                    if key not in existing_keys:
                        raw_triples.append(bt)
                        existing_keys.add(key)
                        added += 1
                logger.info(
                    f"📚 [BASELINE-KG] +{added} baseline triples merged "
                    f"(semantic={len(kg_results) if kg_results else 0}, "
                    f"total_before_selection={len(raw_triples)})"
                )
        
        # ── Step 2: Family Entity Boost ──
        # Detects family/person entities in query, does targeted LIKE searches,
        # injects missing triples at high priority (before adaptive selection)
        raw_triples = family_entity_kg_boost(
            db=db,
            query=user_input,
            existing_triples=raw_triples,
            user_id=user_id,
        )
        
        # ── Step 3: Adaptive Selection ──
        # NOTE: similarity_drop_threshold is set to 0.99 (effectively disabled) because
        # the pool now intentionally mixes semantic triples (high relevance) and baseline
        # triples (moderate relevance). The drop threshold would otherwise cut all baseline
        # triples at the relevance boundary. min_relevance=0.35 remains as the floor guard.
        from wellbeing_session.context.context_helpers import get_query_adaptive_selection_params
        adaptive_params = get_query_adaptive_selection_params(
            user_input=user_input,
            triples=raw_triples,
            baseline_aware=True,
        )
        logger.info(
            "🎛️ [ADAPTIVE-KG] min_rel=%.2f max=%d drop=%.2f",
            adaptive_params['min_relevance'],
            int(adaptive_params['max_triples']),
            adaptive_params['similarity_drop_threshold'],
        )

        selected_triples = adaptive_triple_selection(
            raw_triples,
            min_relevance=adaptive_params['min_relevance'],
            max_triples=int(adaptive_params['max_triples']),
            similarity_drop_threshold=adaptive_params['similarity_drop_threshold'],
        )
        
        return selected_triples
    
    def _gather_session_summaries(
        self,
        user_id: str,
        current_session_id: str,
        user_input: str
    ) -> List[Dict[str, Any]]:
        """Gather session summaries ranked by relevance."""
        from wellbeing_session.context import rank_summaries_by_relevance
        from wellbeing_session.utils.datetime_utils import get_sort_time
        
        # Get all user sessions
        sessions = self.session_manager.manager.db.get_user_sessions(
            user_id, status=None
        )
        
        # Sort by date (newest first)
        sorted_sessions = sorted(sessions, key=get_sort_time, reverse=True)
        
        # Gather summaries (exclude current session)
        summaries: List[Dict[str, Any]] = []
        for session in sorted_sessions[:5]:  # Top 5 sessions
            sess_id = session.get('id', 'N/A')
            is_current = (sess_id == current_session_id)
            
            if not is_current:
                summary_text = session.get('session_summary')
                if summary_text and len(summary_text.strip()) > 20:
                    summaries.append({
                        'session_id': session.get('id'),
                        'date': session.get('created_at'),
                        'summary': summary_text[:500]  # Max 500 chars
                    })
        
        # Rank by relevance to current input
        if summaries and len(summaries) > 1:
            return rank_summaries_by_relevance(
                summaries, user_input, max_summaries=5
            )
        
        return summaries
    
    def _gather_mood_progression(
        self,
        current_session_id: str
    ) -> Optional[Dict[str, Any]]:
        """Gather mood progression data for current session."""
        # Check mood tracker availability
        mood_tracker = None
        if hasattr(self.session_manager, 'manager') and self.session_manager.manager:
            if hasattr(self.session_manager.manager, 'mood_tracker'):
                mood_tracker = self.session_manager.manager.mood_tracker
        
        if not mood_tracker:
            logger.debug("Mood tracker not available")
            return None
        
        # Get mood data
        mood_data = mood_tracker.get_progression_for_session(current_session_id)
        
        if mood_data and isinstance(mood_data, dict):
            return {
                'current_mood': mood_data.get('current_mood'),
                'trend': mood_data.get('trend'),
                'average_valence': mood_data.get('average_valence'),
                'confidence': mood_data.get('confidence'),
                'significant_change': mood_data.get('significant_change', False),
                'related_triples': []  # Could be enriched later
            }
        
        return None
    
    def _gather_treatment_context(
        self,
        user_id: str,
        current_session_id: str,
    ) -> Optional[Dict[str, Any]]:
        """Pull SOTA treatment plan context (plan, formulation, focus, stage,
        risk, MBC) from the session manager's CarePlanManager."""
        sm = getattr(self.session_manager, 'manager', None) or self.session_manager
        tm = getattr(sm, 'treatment_manager', None)
        if tm is None:
            return None
        try:
            ctx = tm.build_context(user_id=user_id, session_id=current_session_id)
        except RuntimeError:
            raise
        except Exception as exc:
            raise RuntimeError(f"CarePlanManager.build_context failed: {exc}") from exc
        if ctx.plan is None:
            return None

        def _goal_to_dict(g: Any) -> Dict[str, Any]:
            return {
                'id': g.id,
                'title': g.title,
                'status': g.status.value,
                'priority': g.priority,
                'progress': g.last_progress_score,
                'parent_goal_id': g.parent_goal_id,
                'metric': g.target_metric,
            }

        return {
            'plan': {
                'id': ctx.plan.id,
                'status': ctx.plan.status.value,
                'version': ctx.plan.version,
            },
            'formulation': (
                {
                    'presenting': ctx.formulation.presenting,
                    'predisposing': ctx.formulation.predisposing,
                    'precipitating': ctx.formulation.precipitating,
                    'perpetuating': ctx.formulation.perpetuating,
                    'protective': ctx.formulation.protective,
                    'confidence': ctx.formulation.confidence,
                    'version': ctx.formulation.version,
                } if ctx.formulation else None
            ),
            'active_goals': [_goal_to_dict(g) for g in ctx.active_goals],
            'primary_goal': _goal_to_dict(ctx.primary_goal) if ctx.primary_goal else None,
            'secondary_goals': [_goal_to_dict(g) for g in ctx.secondary_goals],
            'focus': (
                {
                    'primary_goal_id': ctx.focus.primary_goal_id,
                    'planned_steps': ctx.focus.planned_steps,
                    'carry_forward_notes': ctx.focus.carry_forward_notes,
                    'mode': ctx.focus.focus_mode,
                } if ctx.focus else None
            ),
            'previous_focus': (
                {
                    'session_id': ctx.previous_focus.session_id,
                    'planned_steps': ctx.previous_focus.planned_steps,
                    'carry_forward_notes': ctx.previous_focus.carry_forward_notes,
                } if ctx.previous_focus else None
            ),
            'stage': (
                {
                    'stage': ctx.stage.stage.value,
                    'confidence': ctx.stage.confidence,
                } if ctx.stage else None
            ),
            'latest_risk': (
                {
                    'level': ctx.latest_risk.level.value,
                    'confidence': ctx.latest_risk.confidence,
                    'drivers': ctx.latest_risk.drivers,
                    'protective_factors': ctx.latest_risk.protective_factors,
                } if ctx.latest_risk else None
            ),
            'safety_episode': tm.repo.get_safety_episode(current_session_id),
            'mbc': [
                {
                    'instrument': m.instrument,
                    'item_key': m.item_key,
                    'derived_score': m.derived_score,
                    'created_at': m.created_at,
                }
                for m in ctx.latest_mbc
            ],
        }

    def _gather_care_goals(
        self,
        current_session_id: str
    ) -> List[Dict[str, Any]]:
        """Gather care goals — derived view from the canonical
        CarePlanManager (single source of truth).

        The dict shape is kept stable for legacy formatters and the
        ``response_generator`` handoff. The active treatment plan is
        user-scoped, so we resolve the user_id from the session row first.
        """
        try:
            inner = (
                getattr(self.session_manager, 'manager', None)
                or self.session_manager
            )
            tm = getattr(inner, 'treatment_manager', None)
            db = getattr(inner, 'db', None) or getattr(inner, 'database', None)
            if tm is None or db is None:
                return []

            session_row = (
                db.get_session_record(current_session_id)
                if hasattr(db, 'get_session_record') else None
            )
            user_id = session_row.get('user_id') if session_row else None
            if not user_id:
                return []

            from wellbeing.care_plans.models import GoalStatus

            plan = tm.repo.get_active_plan(user_id)
            if plan is None or not plan.id:
                return []

            goals = tm.repo.list_goals(
                plan.id,
                statuses=[GoalStatus.ACTIVE, GoalStatus.ACHIEVED],
            )
            return [g.to_context_dict() for g in goals[:5]]
        except RuntimeError:
            raise
        except Exception as exc:
            raise RuntimeError(
                "_gather_care_goals failed to resolve active plan/goals"
            ) from exc
    
    def _gather_persistent_profile(
        self,
        user_id: str
    ) -> Optional[Dict[str, Any]]:
        """Gather persistent user profile from cache/synthesis pipeline."""
        if self.profile_cache is None:
            logger.debug("No profile_cache injected — skipping persistent profile")
            return None
        
        try:
            profile = self.profile_cache.get_cached_profile(user_id)
            if profile is None:
                logger.info(
                    f"ℹ️ No profile available for {user_id[:12]}... "
                    f"(will be generated when model_loader is available)"
                )
                return None
            
            # Use the dataclass method for DRY conversion
            if hasattr(profile, 'to_context_dict'):
                profile_dict = profile.to_context_dict()
            else:
                # Fallback for dict-based profiles (e.g. from DB deserialization)
                profile_dict = {
                    'core_personality': getattr(profile, 'core_personality', {}),
                    'current_state': getattr(profile, 'current_state', {}),
                    'relationships': getattr(profile, 'relationships', {}),
                    'goals_and_growth': getattr(profile, 'goals_and_growth', {}),
                    'coping_and_resources': getattr(profile, 'coping_and_resources', {}),
                    'therapeutic_focus': getattr(profile, 'therapeutic_focus', {}),
                    'overall_confidence': getattr(profile, 'overall_confidence', 0.0),
                    'version': getattr(profile, 'version', 0),
                    'updated_at': getattr(profile, 'updated_at', ''),
                }
            
            logger.info(
                f"✅ Persistent profile loaded for {user_id[:12]}... "
                f"(confidence: {profile_dict.get('overall_confidence', 0):.2f}, "
                f"version: {profile_dict.get('version', 0)})"
            )
            return profile_dict
        except RuntimeError:
            # Critical profile cache errors (DB corruption/persistence failures)
            # must propagate instead of silently degrading.
            raise
        
        except Exception as e:
            raise RuntimeError(f"Persistent profile load failed: {e}") from e
    
    def _calculate_token_estimate(self, context: Dict[str, Any]) -> int:
        """Calculate estimated token count for context."""
        token_estimate: int = 0
        
        # Knowledge graph: ~35 tokens per triple
        kg_triples = context.get('knowledge_graph', [])
        if isinstance(kg_triples, list):
            token_estimate += len(kg_triples) * 35
        
        # Session summaries: ~0.25 tokens per character
        summaries = context.get('session_summaries', [])
        if isinstance(summaries, list):
            for s in summaries:
                if isinstance(s, dict):
                    summary_text = s.get('summary', '')
                    if isinstance(summary_text, str):
                        token_estimate += len(summary_text) // 4
        
        # Mood progression: ~150 tokens if present
        if context.get('mood_progression'):
            token_estimate += 150
        
        # Care goals: ~70 tokens per goal
        goals = context.get('care_goals', [])
        if isinstance(goals, list):
            token_estimate += len(goals) * 70
        
        # User insights: ~25 tokens per insight
        insights = context.get('user_insights', [])
        if isinstance(insights, list):
            token_estimate += len(insights) * 25
        
        # Persistent profile: ~200 tokens if present
        if context.get('persistent_profile'):
            token_estimate += 200
        
        return token_estimate
    
    # ========================================================================
    # UTILITY METHODS
    # ========================================================================
    
    def _estimate_tokens(self, context: Dict[str, Any]) -> int:
        """Extract token estimate from context."""
        estimate = context.get('context_token_estimate', 0)
        if isinstance(estimate, int):
            return estimate
        return 0
    
    def _track_build(
        self,
        request: ContextBuildRequest,
        result: ContextBuildResult
    ) -> None:
        """Track build metrics for monitoring."""
        if not self._monitor:
            return
        
        try:
            # Convert sources to dict format
            provider_results = {source: True for source in result.sources_used}
            
            # Track metrics
            self._monitor.track_context_build(
                builder_version=result.builder_version,
                session_id=request.current_session_id,
                user_id=request.user_id,
                duration_ms=result.duration_ms,
                token_count=result.token_estimate,
                provider_results=provider_results,
                error=result.error,
                context_size_bytes=len(str(result.context).encode('utf-8'))
            )
        except Exception:
            # Monitoring must never alter business-path behavior.
            logger.exception("Monitoring tracking failed")


# ============================================================================
# CONVENIENCE FUNCTIONS
# ============================================================================

def create_context_builder(
    session_manager: SessionManagerProtocol,
    profile_cache: Optional[Any] = None,
    use_v2: bool = False,
    enable_monitoring: bool = True,
    v2_builder: Optional[Any] = None
) -> WellbeingContextBuilder:
    """
    Factory function to create a context builder instance.
    
    This is the recommended way to instantiate a context builder as it
    provides sensible defaults and clear semantics.
    
    Args:
        session_manager: Session management adapter
        profile_cache: ProfileCacheManager for persistent profile retrieval
        use_v2: Default to V2 builder if available (default: False)
        enable_monitoring: Track build metrics (default: True)
        v2_builder: Pre-configured UserContextBuilder with providers
    
    Returns:
        Configured WellbeingContextBuilder instance
    
    Examples:
        >>> from wellbeing_session.adapters import SessionManagerAdapter
        >>> session_manager = SessionManagerAdapter(...)
        >>> builder = create_context_builder(session_manager, use_v2=True)
    """
    return WellbeingContextBuilder(
        session_manager=session_manager,
        profile_cache=profile_cache,
        use_v2_by_default=use_v2,
        enable_monitoring=enable_monitoring,
        v2_builder=v2_builder
    )
