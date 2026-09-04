"""
Session Context Builder - builds full session context for psychological_chat().

Collects ALL available psychological context data WITH relevance filtering:
- Knowledge Graph Triples (filtered by relevance to user_query)
- Session Summaries (current + previous)
- Mood Progression
- Care Goals
- User Insights

Extracted from wellbeing_session_interface.py as part of Phase 6b refactoring.
"""

import logging
from typing import Dict, Any, List, Optional

try:
    import streamlit as st
except ModuleNotFoundError:
    class _SessionState(dict):
        def __getattr__(self, key: str) -> Any:
            return self.get(key)

        def __setattr__(self, key: str, value: Any) -> None:
            self[key] = value

    class _StreamlitShim:
        session_state = _SessionState()

    st = _StreamlitShim()  # type: ignore[assignment]

logger = logging.getLogger(__name__)


class SessionContextBuilder:
    """
    Builds full session context dictionaries for psychological_chat().

    Collects knowledge-graph triples, previous session summaries,
    mood progression, care goals and user insights into a
    single context dictionary.
    """

    def __init__(self, session_manager: Any, profile_cache: Optional[Any] = None) -> None:
        """
        Args:
            session_manager: SessionManagerAdapter instance.
            profile_cache: ProfileCacheManager for persistent profile retrieval.
        """
        self.session_manager = session_manager
        self.profile_cache = profile_cache

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def build_session_context(
        self, user_query: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Build FULL session context for psychological_chat().

        Collects ALL available context data WITH relevance filtering:
        - Knowledge Graph Triples (filtered by relevance to user_query)
        - Session Summaries (current + previous)
        - Mood Progression
        - Care Goals
        - User Insights

        Args:
            user_query: Current user query for relevance filtering (optional).

        Returns:
            Dictionary with full, relevant context or ``None``.
        """
        if not st.session_state.psych_current_session:
            logger.warning("❌ [BUILD_CONTEXT] Keine aktive Session!")
            return None

        session_id: str = st.session_state.psych_current_session

        # Get user ID from session summary (not from session_state!)
        session_summary_dict = self.session_manager.get_session_summary(session_id)

        if not session_summary_dict:
            logger.warning(
                f"❌ [BUILD_CONTEXT] Keine Session-Summary für {session_id[:12]}..."
            )
            return None

        user_id = str(session_summary_dict.get("user_id") or "").strip()
        if not user_id:
            raise RuntimeError(
                f"[BUILD_CONTEXT] Session {session_id[:12]}... has no persisted user identity"
            )

        logger.info(
            f"🔍 [BUILD_CONTEXT] START - Session: {session_id[:12]}..., "
            f"User: {user_id}, "
            f"Query: '{user_query[:50] if user_query else 'None'}'"
        )

        # === COLLECT FULL, RELEVANT CONTEXT ===
        kg_triples = self._load_kg_triples(user_id, user_query)
        previous_sessions = self._load_previous_sessions(user_id, session_id)
        mood_progression = self._load_mood_progression(user_id)
        user_insights = self._load_user_insights(user_id)
        care_goals = self._load_care_goals(session_id)
        persistent_profile = self._load_persistent_profile(user_id)

        # === BUILD FULL CONTEXT ===
        context: Dict[str, Any] = {
            "user_id": user_id,
            "user_name": st.session_state.psych_current_user,
            "session_id": session_id,
            "mood": session_summary_dict.get("emotional_state", "Neutral"),
            "goals": (
                care_goals
                if care_goals
                else session_summary_dict.get("key_topics", [])
            ),
            "summary": session_summary_dict.get("session_summary", ""),
            "knowledge_graph": kg_triples,
            "previous_sessions": previous_sessions,
            "mood_progression": mood_progression,
            "user_insights": user_insights,
            "persistent_profile": persistent_profile,
        }

        logger.info(
            f"✅ Vollständiger Session-Kontext gebaut: "
            f"KG={len(kg_triples)}, PrevSessions={len(previous_sessions)}, "
            f"Insights={len(user_insights)}, "
            f"Profile={'JA' if persistent_profile else 'NEIN'}"
        )

        return context

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _load_kg_triples(
        self, user_id: str, user_query: Optional[str]
    ) -> List[Dict[str, Any]]:
        """Load Knowledge Graph triples with relevance filtering + family entity boost."""
        kg_triples: List[Dict[str, Any]] = []
        logger.info("🔍 [BUILD_CONTEXT] Lade KG-Triples...")

        if not (
            hasattr(self.session_manager, "manager")
            and hasattr(self.session_manager.manager, "db")
        ):
            raise RuntimeError("[BUILD_CONTEXT] Kein DB-Zugriff möglich")

        db = self.session_manager.manager.db
        logger.info(f"✅ [BUILD_CONTEXT] DB gefunden: {type(db).__name__}")

        kg_search_results: List[Dict[str, Any]] = []

        # ── Step 1: Semantic Search ──
        if hasattr(db, "search_knowledge_graph_semantic"):
            if user_query and len(user_query.strip()) > 0:
                logger.info(
                    f"🔍 [BUILD_CONTEXT] Semantische KG-Suche (query-basiert) "
                    f"für: '{user_query[:50]}...'"
                )
                kg_search_results = db.search_knowledge_graph_semantic(
                    query=user_query,
                    user_id=user_id,
                    limit=20,
                    min_confidence=0.5,
                    similarity_threshold=0.25,
                )
            else:
                logger.info(
                    "🔍 [BUILD_CONTEXT] Semantische KG-Suche (generisch) für User-Profil"
                )
                generic_query = (
                    "Wichtige Informationen über den Benutzer, "
                    "emotionale Themen, persönliche Herausforderungen"
                )
                kg_search_results = db.search_knowledge_graph_semantic(
                    query=generic_query,
                    user_id=user_id,
                    limit=20,
                    min_confidence=0.5,
                    similarity_threshold=0.20,
                )
            logger.info(
                f"📊 [BUILD_CONTEXT] Semantische Suche ergab "
                f"{len(kg_search_results)} Ergebnisse"
            )

        elif hasattr(db, "search_knowledge_graph"):
                # Fallback: LIKE-based search
                logger.info(
                    "⚠️ [BUILD_CONTEXT] Semantische Suche nicht verfügbar - nutze LIKE-Suche"
                )
                query = user_query if (user_query and len(user_query.strip()) > 0) else ""
                kg_search_results = db.search_knowledge_graph(
                    query=query,
                    user_id=user_id,
                    limit=20,
                    min_confidence=0.6,
                )
                logger.info(
                    f"📊 [BUILD_CONTEXT] LIKE-Suche ergab {len(kg_search_results)} Ergebnisse"
                )
        else:
            raise RuntimeError("[BUILD_CONTEXT] Keine KG-Suchfunktion verfügbar")

        # Normalise results — PRESERVE all SOTA v4 scoring fields
        kg_triples = [
            {
                "subject": r.get("subject", ""),
                "predicate": r.get("predicate", ""),
                "object": r.get("object", ""),
                "confidence": r.get("confidence", 0.0),
                "similarity": r.get("similarity", 0.0),
                "combined_score": r.get("combined_score", 0.0),
                "rerank_score": r.get("rerank_score", 0.0),
                "entity_score": r.get("entity_score", 0.0),
                "source_date": r.get("interaction_date") or r.get("created_at") or "N/A",
            }
            for r in kg_search_results
        ]
            
        # ── Step 2: Family Entity Boost ──
        # Detects family/person entities in query, does targeted LIKE searches,
        # injects missing triples at high priority (before adaptive selection)
        if user_query and len(user_query.strip()) > 0:
            from wellbeing_session.context.family_entity_boost import family_entity_kg_boost
            kg_triples = family_entity_kg_boost(
                db=db,
                query=user_query,
                existing_triples=kg_triples,
                user_id=user_id,
            )
            
        # ── Step 3: Adaptive Selection ──
        from wellbeing_session.context import adaptive_triple_selection
        from wellbeing_session.context.context_helpers import get_query_adaptive_selection_params

        adaptive_params = get_query_adaptive_selection_params(
            user_input=user_query or "",
            triples=kg_triples,
            baseline_aware=False,
        )
        logger.info(
            "🎛️ [BUILD_CONTEXT] Adaptive KG params: min_rel=%.2f max=%d drop=%.2f",
            adaptive_params["min_relevance"],
            int(adaptive_params["max_triples"]),
            adaptive_params["similarity_drop_threshold"],
        )

        kg_triples = adaptive_triple_selection(
            kg_triples,
            min_relevance=adaptive_params["min_relevance"],
            max_triples=int(adaptive_params["max_triples"]),
            similarity_drop_threshold=adaptive_params["similarity_drop_threshold"],
        )

        logger.info(
            f"✅ [BUILD_CONTEXT] {len(kg_triples)} KG-Triples geladen "
            f"(nach family boost + adaptive selection)"
        )

        return kg_triples

    def _load_previous_sessions(
        self, user_id: str, current_session_id: str
    ) -> List[Dict[str, Any]]:
        """Load previous session summaries for long-term context."""
        previous_sessions: List[Dict[str, Any]] = []
        if hasattr(self.session_manager, "get_user_sessions"):
            # ✅ FIXED: limit=10 (statt 5) um genug Sessions nach Filterung zu haben
            all_sessions = self.session_manager.get_user_sessions(user_id, limit=10)
            previous_sessions = [
                {
                    "session_id": s.get("id", ""),
                    "summary": s.get("session_summary", ""),
                    "date": s.get("start_time", ""),
                    "mood": s.get("mood_progression", ""),
                    "goals": s.get("care_goals", ""),
                }
                for s in all_sessions
                if s.get("id") != current_session_id
                and (s.get("session_summary") or s.get("care_goals"))
            ][:5]  # Top 5 vorherige Sessions mit Inhalt
            logger.info(
                f"✅ {len(previous_sessions)} frühere Sessions für Kontext geladen "
                f"(von {len(all_sessions)} Total-Sessions)"
            )
            if previous_sessions:
                for i, prev in enumerate(previous_sessions):
                    logger.info(
                        f"🔍 Previous Session {i+1}: ID={prev['session_id'][:12]}..., "
                        f"Summary={'JA' if prev['summary'] else 'LEER'} ({len(prev['summary'] or '')} chars), "
                        f"Goals={'JA' if prev['goals'] else 'LEER'}"
                    )
        return previous_sessions

    def _load_mood_progression(self, user_id: str) -> Optional[Dict[str, Any]]:
        """Load mood progression data."""
        if hasattr(self.session_manager, "manager"):
            mood_tracker = getattr(self.session_manager.manager, "mood_tracker", None)
            if mood_tracker and hasattr(mood_tracker, "get_mood_trend"):
                result: Dict[str, Any] = mood_tracker.get_mood_trend(user_id, days=7)
                logger.info(f"✅ Mood Progression geladen: {result}")
                return result
        return None

    def _load_user_insights(self, user_id: str) -> List[Dict[str, Any]]:
        """Load user insights from wellbeing_insights table.

        Ranking: ``confidence`` primär, ``mention_count`` als Tiebreaker
        (Insights, die in vielen Sessions wiederholt auftraten, gewinnen
        gegenüber einmaligen Einzelnennungen), ``last_seen_at`` als finaler
        Tiebreaker (Aktualität). Math-Funktionen werden bewusst nicht in SQL
        verwendet (nicht in allen SQLite-Builds verfügbar) — wir reranken in
        Python mit log-Skalierung.
        """
        user_insights: List[Dict[str, Any]] = []
        if not (
            hasattr(self.session_manager, "manager")
            and hasattr(self.session_manager.manager, "db")
        ):
            return user_insights

        import math

        db = self.session_manager.manager.db
        with db.get_connection() as conn:
            # ``mention_count``/``first_session_id`` etc. wurden via Migration
            # hinzugefügt; ``COALESCE`` schützt vor partiell migrierten DBs
            # (z.B. wenn der Insight-Extractor noch nicht initialisiert wurde).
            cursor = conn.execute(
                """
                SELECT insight_type, category, value, confidence,
                       temporal_context,
                       COALESCE(mention_count, 1)        AS mention_count,
                       COALESCE(first_session_id, session_id) AS first_session_id,
                       session_id                       AS last_session_id,
                       COALESCE(first_seen_at, created_at) AS first_seen_at,
                       COALESCE(last_seen_at, created_at)  AS last_seen_at
                FROM wellbeing_insights
                WHERE user_id = ?
                  AND COALESCE(correction_status, 'active') = 'active'
                ORDER BY confidence DESC, mention_count DESC, last_seen_at DESC
                LIMIT 50
                """,
                (user_id,),
            )
            rows = cursor.fetchall()

        # Rerank in Python (log-Bayes-Skalierung), dann Top-15.
        scored = []
        for row in rows:
            confidence = float(row[3] or 0.0)
            mention_count = int(row[5] or 1)
            score = confidence * (1.0 + math.log1p(mention_count))
            scored.append((score, row))
        scored.sort(key=lambda t: t[0], reverse=True)

        for _score, row in scored[:15]:
            user_insights.append(
                {
                    "type": row[0],
                    "category": row[1],
                    "value": row[2],
                    "confidence": row[3],
                    "temporal_context": row[4],
                    "mention_count": row[5],
                    "first_session_id": row[6],
                    "last_session_id": row[7],
                    "first_seen_at": row[8],
                    "last_seen_at": row[9],
                }
            )

        if user_insights:
            logger.info(
                f"✅ {len(user_insights)} User Insights aus DB geladen "
                f"(Bayesian-ranked, mit Provenance)"
            )
        return user_insights

    def _load_care_goals(self, session_id: str) -> List[Dict[str, Any]]:
        """Load care goals from the SOTA CarePlanManager.

        Goals are user-scoped (cross-session) — we resolve the user_id from
        the session row and pull the active treatment plan's active+achieved
        goals from the canonical repository.
        """
        care_goals: List[Dict[str, Any]] = []
        inner = (
            getattr(self.session_manager, "manager", None)
            or self.session_manager
        )
        tm = getattr(inner, "treatment_manager", None)
        db = getattr(inner, "db", None) or getattr(inner, "database", None)
        if tm is None or db is None:
            return care_goals

        session_row = db.get_session_record(session_id) if hasattr(
            db, "get_session_record"
        ) else None
        user_id = session_row.get("user_id") if session_row else None
        if not user_id:
            return care_goals

        from wellbeing.care_plans.models import GoalStatus

        plan = tm.repo.get_active_plan(user_id)
        if plan is None or not plan.id:
            return care_goals

        goals = tm.repo.list_goals(
            plan.id,
            statuses=[GoalStatus.ACTIVE, GoalStatus.ACHIEVED],
        )
        care_goals = [g.to_context_dict() for g in goals]
        if care_goals:
            logger.info(
                f"✅ {len(care_goals)} Care Goals geladen "
                f"(CarePlanManager, plan v{plan.version})"
            )
        return care_goals

    def _load_persistent_profile(self, user_id: str) -> Optional[Dict[str, Any]]:
        """Load persistent psychological profile from cache/synthesis pipeline."""
        if self.profile_cache is None:
            return None
        try:
            profile = self.profile_cache.get_cached_profile(user_id)
            if profile is None:
                return None
            # Use the dataclass method for DRY conversion
            if hasattr(profile, 'to_context_dict'):
                profile_dict = profile.to_context_dict()
            else:
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
                f"(confidence: {profile_dict.get('overall_confidence', 0):.2f})"
            )
            return profile_dict
        except RuntimeError:
            # Data integrity / persistence failures must not be downgraded to
            # "no profile" because that would hide root causes.
            raise
        except Exception as e:
            raise RuntimeError(f"Persistent profile load failed: {e}") from e

