"""
User Insights Provider for user context building.

Reads directly from the ``wellbeing_insights`` DB table (the same
persisted source used by ``SessionContextBuilder._load_user_insights``).
This ensures the V2 modular builder populates ``user_insights`` from real
data instead of only KG-derived insights.
"""

import logging
import math
from typing import Optional, List, Dict, Any

from user_context_builder.base import BaseContextProvider
from user_context_builder.models import UserContextRequest

logger = logging.getLogger(__name__)


class UserInsightsProvider(BaseContextProvider):
    """Provider for fetching persisted user insights from the DB."""

    def __init__(
        self,
        max_insights: int = 15,
        priority: int = 45,
    ):
        super().__init__(name="user_insights", priority=priority)
        self.max_insights = max_insights

    # -- public API -------------------------------------------------------

    def provide(
        self,
        request: UserContextRequest,
        session_manager: Any,
    ) -> Optional[List[Dict[str, Any]]]:
        """Return top user-insights for *request.user_id*."""
        db = self._resolve_db(session_manager)
        if db is None:
            logger.debug("DB not available — returning empty insights")
            return []

        try:
            insights = self._fetch_insights(db, request.user_id)
        except Exception as exc:
            logger.warning(
                "UserInsightsProvider failed for user %s: %s",
                request.user_id, exc, exc_info=True,
            )
            raise

        return insights if insights else None

    # -- internals --------------------------------------------------------

    @staticmethod
    def _resolve_db(session_manager: Any) -> Optional[Any]:
        """Best-effort DB resolution (mirrors existing provider patterns)."""
        db = getattr(session_manager, "db", None)
        if db is not None:
            return db
        inner = getattr(session_manager, "manager", None)
        if inner is not None:
            return getattr(inner, "db", None)
        return None

    def _fetch_insights(
        self,
        db: Any,
        user_id: str,
    ) -> List[Dict[str, Any]]:
        """Query ``wellbeing_insights`` and rerank with log-Bayes scoring.

        Identical logic to ``SessionContextBuilder._load_user_insights`` so
        both legacy and V2 paths return the same insights.
        """
        if not user_id:
            return []

        with db.get_connection() as conn:
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

        # Rerank in Python (log-Bayes scaling), then take Top-N.
        scored: List[tuple] = []
        for row in rows:
            confidence = float(row[3] or 0.0)
            mention_count = int(row[5] or 1)
            score = confidence * (1.0 + math.log1p(mention_count))
            scored.append((score, row))
        scored.sort(key=lambda t: t[0], reverse=True)

        insights: List[Dict[str, Any]] = []
        for _score, row in scored[: self.max_insights]:
            insights.append(
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

        if insights:
            logger.info(
                "✅ %d User Insights aus DB geladen (V2 provider, Bayesian-ranked)",
                len(insights),
            )
        return insights