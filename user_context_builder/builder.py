"""
User Context Builder V2 - Main Builder/Orchestrator

Main orchestrator that coordinates context data providers.
"""
import logging
import math
import re
import time
import unicodedata
from datetime import datetime, timezone
from typing import List, Optional, Any, Dict, Set, Tuple

from .base import ContextProvider
from .models import (
    UserContextRequest,
    UserContextResult,
    KnowledgeGraphData,
    SessionSummaryData,
    MoodProgressionData,
    CareGoalsData
)
from .utils import TokenBudgetEstimator

logger = logging.getLogger(__name__)

# Stable ordering is part of the selection contract.
_BASELINE_CATEGORIES = ("core_personality", "current_state", "relationships")
_MAX_INSIGHTS_INJECTED = 8
_TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)
_STOP_WORDS = {
    "aber", "als", "am", "an", "auch", "auf", "aus", "bei", "bin", "das",
    "dem", "den", "der", "die", "ein", "eine", "einer", "er", "es", "fuehle",
    "fühle", "hat", "ich", "im", "in", "ist", "mein", "meine", "mit", "mich",
    "oder", "sich", "sie", "und", "von", "war", "wie", "zu",
}
_TOKEN_ALIASES = {
    "haeufig": "oft",
    "häufig": "oft",
    "regelmaessig": "oft",
    "regelmäßig": "oft",
    "belastet": "stress",
    "gestresst": "stress",
    "stressig": "stress",
}
_NEGATION_TOKENS = {"kein", "keine", "keinen", "nicht", "nie", "ohne"}
_TRANSIENT_TYPES = {"emotional_state", "current_state", "mood", "stress_level"}
_DEVELOPING_TYPES = {"behavioral_pattern", "coping_mechanism", "cognitive_pattern"}


def _normalized_tokens(text: Any) -> Set[str]:
    normalized = unicodedata.normalize("NFKC", str(text or "")).casefold()
    tokens = set()
    for token in _TOKEN_RE.findall(normalized):
        if len(token) < 2 or token in _STOP_WORDS:
            continue
        tokens.add(_TOKEN_ALIASES.get(token, token))
    return tokens


def _insight_tokens(insight: Dict[str, Any]) -> Set[str]:
    return _normalized_tokens(
        " ".join(
            str(insight.get(field) or "")
            for field in ("value", "description", "category", "type")
        )
    )


def _is_near_duplicate(left: Set[str], right: Set[str]) -> bool:
    if not left or not right:
        return False
    if (left & _NEGATION_TOKENS) != (right & _NEGATION_TOKENS):
        return False
    intersection = len(left & right)
    containment = intersection / min(len(left), len(right))
    jaccard = intersection / len(left | right)
    return containment >= 0.85 or jaccard >= 0.72


def _parse_timestamp(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _recency_factor(insight: Dict[str, Any], now: datetime) -> float:
    insight_type = str(insight.get("type") or "").casefold()
    category = str(insight.get("category") or "").casefold()
    if category == "core_personality" or insight_type in {"personality", "personality_trait", "life_event"}:
        return 1.0

    last_seen = _parse_timestamp(insight.get("last_seen_at") or insight.get("created_at"))
    if last_seen is None:
        return 1.0
    age_days = max(0.0, (now - last_seen).total_seconds() / 86400.0)
    half_life_days = 30.0 if insight_type in _TRANSIENT_TYPES or category == "current_state" else 180.0
    if insight_type in _DEVELOPING_TYPES:
        half_life_days = 365.0
    return max(0.35, math.exp(-math.log(2.0) * age_days / half_life_days))


def _selection_score(
    insight: Dict[str, Any],
    query_tokens: Set[str],
    now: datetime,
) -> float:
    confidence = min(1.0, max(0.0, float(insight.get("confidence") or 0.0)))
    mention_count = max(1, int(insight.get("mention_count") or 1))
    evidence_score = confidence * (1.0 + math.log1p(mention_count))
    tokens = _insight_tokens(insight)
    query_relevance = len(tokens & query_tokens) / len(query_tokens) if query_tokens else 0.0
    return evidence_score * _recency_factor(insight, now) * (1.0 + 0.75 * query_relevance)


def _select_hybrid_top_n(
    insights: List[Dict[str, Any]],
    user_query: Optional[str],
    n: int = _MAX_INSIGHTS_INJECTED,
    *,
    now: Optional[datetime] = None,
) -> List[Dict[str, Any]]:
    """Select deterministic, explainable context with coverage and relevance."""
    if not insights or n <= 0:
        return []

    query_tokens = _normalized_tokens(user_query)
    selection_time = now or datetime.now(timezone.utc)
    ranked: List[Tuple[float, int, Dict[str, Any], Set[str]]] = []
    for position, insight in enumerate(insights):
        if not isinstance(insight, dict) or not str(insight.get("value") or "").strip():
            continue
        ranked.append(
            (
                _selection_score(insight, query_tokens, selection_time),
                position,
                insight,
                _insight_tokens(insight),
            )
        )
    ranked.sort(key=lambda item: (-item[0], item[1]))

    deduplicated: List[Tuple[float, int, Dict[str, Any], Set[str]]] = []
    for candidate in ranked:
        if any(_is_near_duplicate(candidate[3], existing[3]) for existing in deduplicated):
            continue
        deduplicated.append(candidate)

    selected: List[Dict[str, Any]] = []
    selected_ids: Set[int] = set()

    def add_best(predicate: Any) -> None:
        if len(selected) >= n:
            return
        for _score, position, insight, _tokens in deduplicated:
            if position not in selected_ids and predicate(insight):
                selected.append(insight)
                selected_ids.add(position)
                return

    for category in _BASELINE_CATEGORIES:
        add_best(lambda insight, expected=category: insight.get("category") == expected)

    insight_types = sorted(
        {str(item[2].get("type") or "") for item in deduplicated if item[2].get("type")}
    )
    for insight_type in insight_types:
        add_best(lambda insight, expected=insight_type: str(insight.get("type") or "") == expected)

    for _score, position, insight, _tokens in deduplicated:
        if len(selected) >= n:
            break
        if position not in selected_ids:
            selected.append(insight)
            selected_ids.add(position)

    return selected




class UserContextBuilder:
    """
    Orchestrates user context building using multiple data providers.
    
    Collects data from various sources (KG, sessions, mood, goals, profile)
    and assembles a comprehensive user context.
    
    Target CC: <8
    """
    
    def __init__(self, providers: Optional[List[ContextProvider]] = None):
        """
        Initialize builder with data providers.
        
        Args:
            providers: List of context providers (if None, uses empty list)
        """
        self._providers: List[ContextProvider] = providers or []
        self._sort_providers()
    
    def add_provider(self, provider: ContextProvider) -> None:
        """Add a new context provider."""
        self._providers.append(provider)
        self._sort_providers()
    
    def _sort_providers(self) -> None:
        """Sort providers by priority (lower = higher priority)."""
        self._providers.sort(key=lambda p: p.priority)
    
    def build(
        self,
        request: UserContextRequest,
        session_manager: Any
    ) -> UserContextResult:
        """
        Build comprehensive user context.
        
        Args:
            request: Context request with user info
            session_manager: Session manager instance for data access
            
        Returns:
            Complete user context result
        """
        start_time = time.perf_counter()
        
        # Initialize result components
        kg_data = KnowledgeGraphData()
        session_data = SessionSummaryData()
        mood_data = MoodProgressionData()
        goals_data = CareGoalsData()
        persistent_profile = None
        user_insights: List[Any] = []
        sources_used: List[str] = []
        errors: List[str] = []
        
        logger.info(
            f"🔧 [CONTEXT-BUILDER-V2] Building context for user={request.user_id[:12]}..., "
            f"session={request.current_session_id[:12]}..."
        )
        
        # Collect data from each provider
        for provider in self._providers:
            if not provider.can_handle(request):
                continue
            
            try:
                logger.debug(f"   → Provider: {provider.name}")
                data = provider.provide(request, session_manager)
                
                if data is not None:
                    # Assign data to appropriate component.
                    # Only mark source as "used" if non-trivial data returned.
                    if provider.name == "knowledge_graph":
                        kg_data = data
                        if data.triples:
                            sources_used.append("knowledge_graph")
                    elif provider.name == "session_summaries":
                        session_data = data
                        if data.summaries:
                            sources_used.append("session_summaries")
                    elif provider.name == "mood_progression":
                        mood_data = data
                        if data.current_mood:
                            sources_used.append("mood_progression")
                    elif provider.name == "care_goals":
                        goals_data = data
                        if data.goals:
                            sources_used.append("care_goals")
                    elif provider.name == "persistent_profile":
                        persistent_profile = data
                        if data:
                            sources_used.append("persistent_profile")
                    elif provider.name == "user_insights":
                        # data is a list of insight dicts (or None)
                        if data:
                            user_insights.extend(data)
                            sources_used.append("user_insights")
                    
                    logger.debug(f"      ✅ Data collected from {provider.name}")
                
            except Exception as e:
                error_msg = f"{provider.name}: {str(e)}"
                errors.append(error_msg)
                logger.warning(f"⚠️ Provider {provider.name} failed: {e}")
                continue
        
        # Combine insights from KG
        if kg_data and kg_data.insights:
            user_insights.extend(kg_data.insights)

        # Hybrid Top-N Auswahl (P1): Baseline-Garantie + Confidence-Ranking,
        # Deduplizierung, max _MAX_INSIGHTS_INJECTED.
        user_insights = _select_hybrid_top_n(
            user_insights, getattr(request, "user_input", None)
        )
        if len(user_insights) < _MAX_INSIGHTS_INJECTED:
            logger.debug(
                f"[CONTEXT-BUILDER-V2] Nur {len(user_insights)}/"
                f"{_MAX_INSIGHTS_INJECTED} Insights für Injektion verfügbar"
            )

        # Build result dict for token estimation
        result_dict = {
            'knowledge_graph': kg_data.triples if kg_data else [],
            'session_summaries': session_data.summaries if session_data else [],
            'mood_progression': {
                'current_mood': mood_data.current_mood if mood_data else None
            },
            'user_insights': user_insights,
            'care_goals': goals_data.goals if goals_data else [],
            'persistent_profile': persistent_profile
        }
        
        # Estimate tokens
        token_estimate = TokenBudgetEstimator.estimate(result_dict)
        
        # Calculate build time
        build_time_ms = (time.perf_counter() - start_time) * 1000
        
        # Create result
        result = UserContextResult(
            user_id=request.user_id,
            current_session_id=request.current_session_id,
            knowledge_graph=kg_data,
            session_summaries=session_data,
            mood_progression=mood_data,
            care_goals=goals_data,
            persistent_profile=persistent_profile,
            user_insights=user_insights,
            context_token_estimate=token_estimate,
            build_time_ms=build_time_ms,
            sources_used=sources_used,
            errors=errors
        )
        
        logger.info(
            f"✅ [CONTEXT-BUILDER-V2] Context built in {build_time_ms:.1f}ms: "
            f"{len(sources_used)} sources, {token_estimate} tokens"
        )
        
        return result
