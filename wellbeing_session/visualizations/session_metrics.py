"""
Session Metrics Visualizer — bar/gauge charts for session engagement metrics.

Shows metrics like:
- Messages per session
- Average response time
- Session duration
- Emotional diversity score

✅ Phase 10: Multi-Modal Visualizations.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

try:
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots
    PLOTLY_AVAILABLE = True
except ImportError:
    PLOTLY_AVAILABLE = False
    go = None
    make_subplots = None


class SessionMetricsVisualizer:
    """Renders engagement metrics for psychological sessions.

    Usage::

        viz = SessionMetricsVisualizer()
        fig = viz.create_metrics_dashboard({
            "messages": 24, "duration_min": 45, ...
        })
        st.plotly_chart(fig)
    """

    def __init__(self, height: int = 350) -> None:
        self.height = height

    @staticmethod
    def is_available() -> bool:
        return PLOTLY_AVAILABLE

    def create_engagement_bars(
        self,
        sessions: List[Dict[str, Any]],
        title: str = "Session-Engagement",
    ) -> Optional[Any]:
        """Create a grouped bar chart comparing session engagement.

        Each session dict should have:
        - ``session_id`` (str)
        - ``message_count`` (int)
        - ``duration_min`` (float)
        - ``emotional_diversity`` (float, 0–1)

        Args:
            sessions: List of session metric dicts.
            title: Chart title.

        Returns:
            plotly Figure or None.
        """
        if not PLOTLY_AVAILABLE or go is None:
            return None
        if not sessions:
            return None

        labels = [s.get("session_id", f"S{i}")[:8] for i, s in enumerate(sessions)]
        messages = [s.get("message_count", 0) for s in sessions]
        durations = [s.get("duration_min", 0) for s in sessions]

        fig = go.Figure()
        fig.add_trace(go.Bar(
            name="Nachrichten", x=labels, y=messages,
            marker_color="#2ecc71", opacity=0.85,
        ))
        fig.add_trace(go.Bar(
            name="Dauer (min)", x=labels, y=durations,
            marker_color="#3498db", opacity=0.85,
        ))

        fig.update_layout(
            title=dict(text=title, font=dict(size=16)),
            barmode="group",
            height=self.height,
            template="plotly_white",
            legend=dict(orientation="h", yanchor="bottom", y=-0.25),
        )
        return fig

    def create_emotion_diversity_gauge(
        self,
        diversity_score: float,
        title: str = "Emotionale Diversität",
    ) -> Optional[Any]:
        """Create a gauge indicator for emotional diversity.

        Args:
            diversity_score: Score from 0.0 (monotone) to 1.0 (highly diverse).
            title: Chart title.

        Returns:
            plotly Figure or None.
        """
        if not PLOTLY_AVAILABLE or go is None:
            return None

        fig = go.Figure(go.Indicator(
            mode="gauge+number+delta",
            value=diversity_score * 100,
            title=dict(text=title),
            gauge=dict(
                axis=dict(range=[0, 100]),
                bar=dict(color="#2ecc71"),
                steps=[
                    dict(range=[0, 33], color="#fee2e2"),
                    dict(range=[33, 66], color="#fef3c7"),
                    dict(range=[66, 100], color="#d1fae5"),
                ],
                threshold=dict(
                    line=dict(color="red", width=2),
                    thickness=0.75, value=80,
                ),
            ),
            number=dict(suffix="%"),
        ))

        fig.update_layout(height=280, template="plotly_white")
        return fig

    def create_metrics_summary(
        self,
        metrics: Dict[str, Any],
        title: str = "Session-Übersicht",
    ) -> Optional[Any]:
        """Create a combined metrics view with indicators.

        Args:
            metrics: Dict with keys like total_messages, avg_response_time_s,
                     session_count, crisis_count.
            title: Chart title.

        Returns:
            plotly Figure or None.
        """
        if not PLOTLY_AVAILABLE or go is None or make_subplots is None:
            return None

        fig = make_subplots(
            rows=1, cols=4,
            specs=[[{"type": "indicator"}] * 4],
        )

        indicators = [
            ("Nachrichten", metrics.get("total_messages", 0), None, "#2ecc71"),
            ("Sessions", metrics.get("session_count", 0), None, "#3498db"),
            ("Ø Antwortzeit", metrics.get("avg_response_time_s", 0), "s", "#e67e22"),
            ("Krisen", metrics.get("crisis_count", 0), None, "#e74c3c"),
        ]

        for i, (label, value, suffix, colour) in enumerate(indicators, 1):
            fig.add_trace(go.Indicator(
                mode="number",
                value=value,
                title=dict(text=label, font=dict(size=13)),
                number=dict(
                    suffix=suffix or "",
                    font=dict(color=colour, size=28),
                ),
            ), row=1, col=i)

        fig.update_layout(
            title=dict(text=title, font=dict(size=16)),
            height=200,
            template="plotly_white",
        )
        return fig
