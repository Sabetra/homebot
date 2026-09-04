"""
Emotion Trajectory Chart — time-series visualization of emotional states.

Renders an interactive line/area chart showing how dominant emotions
shift across messages within a session or across sessions.

✅ Phase 10: Multi-Modal Visualizations.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence

logger = logging.getLogger(__name__)

# Optional plotly import (graceful degradation)
try:
    import plotly.graph_objects as go
    PLOTLY_AVAILABLE = True
except ImportError:
    PLOTLY_AVAILABLE = False
    go = None


@dataclass
class EmotionDataPoint:
    """A single emotional data point in the trajectory."""
    timestamp: datetime
    emotion: str
    confidence: float = 0.5
    message_index: int = 0
    is_crisis: bool = False


# Emotion → colour mapping (SOTA: perceptually balanced, colour-blind friendly)
EMOTION_COLOURS: Dict[str, str] = {
    "freude": "#2ecc71",       # green
    "trauer": "#3498db",       # blue
    "angst": "#e67e22",        # orange
    "wut": "#e74c3c",          # red
    "stress": "#f39c12",       # yellow-orange
    "einsamkeit": "#9b59b6",   # purple
    "neutral": "#95a5a6",      # grey
    "hoffnung": "#1abc9c",     # teal
    "frustration": "#d35400",  # dark orange
    "verwirrung": "#8e44ad",   # dark purple
}


class EmotionTrajectoryChart:
    """Generates interactive emotional trajectory visualizations.

    Usage::

        chart = EmotionTrajectoryChart()
        fig = chart.create_trajectory(data_points)
        st.plotly_chart(fig)
    """

    def __init__(self, height: int = 400, show_crisis_markers: bool = True) -> None:
        self.height = height
        self.show_crisis_markers = show_crisis_markers

    @staticmethod
    def is_available() -> bool:
        """Check if plotly is available for rendering."""
        return PLOTLY_AVAILABLE

    def create_trajectory(
        self,
        data_points: Sequence[EmotionDataPoint],
        title: str = "Emotionale Entwicklung",
    ) -> Optional[Any]:
        """Create an interactive emotion trajectory chart.

        Args:
            data_points: Sequence of emotional data points.
            title: Chart title.

        Returns:
            plotly Figure or None if plotly unavailable.
        """
        if not PLOTLY_AVAILABLE or go is None:
            logger.warning("Plotly not installed — skipping emotion chart")
            return None

        if not data_points:
            return self._empty_figure(title)

        fig = go.Figure()

        # Group by emotion for stacked area
        emotions: Dict[str, List[EmotionDataPoint]] = {}
        for dp in data_points:
            emotions.setdefault(dp.emotion, []).append(dp)

        for emotion, points in emotions.items():
            colour = EMOTION_COLOURS.get(emotion, "#95a5a6")
            sorted_pts = sorted(points, key=lambda p: p.timestamp)
            fig.add_trace(go.Scatter(
                x=[p.timestamp for p in sorted_pts],
                y=[p.confidence for p in sorted_pts],
                mode="lines+markers",
                name=emotion.capitalize(),
                line=dict(color=colour, width=2),
                marker=dict(size=6),
                fill="tozeroy",
                opacity=0.7,
                hovertemplate=(
                    "<b>%{text}</b><br>"
                    "Konfidenz: %{y:.2f}<br>"
                    "Zeit: %{x}<extra></extra>"
                ),
                text=[emotion.capitalize()] * len(sorted_pts),
            ))

        # Crisis markers
        if self.show_crisis_markers:
            crisis_pts = [dp for dp in data_points if dp.is_crisis]
            if crisis_pts:
                fig.add_trace(go.Scatter(
                    x=[p.timestamp for p in crisis_pts],
                    y=[p.confidence for p in crisis_pts],
                    mode="markers",
                    name="🚨 Krise",
                    marker=dict(
                        color="red", size=14, symbol="x",
                        line=dict(width=2, color="darkred"),
                    ),
                    hovertemplate="<b>🚨 Krisenmoment</b><br>Zeit: %{x}<extra></extra>",
                ))

        fig.update_layout(
            title=dict(text=title, font=dict(size=16)),
            xaxis_title="Zeit",
            yaxis_title="Emotionale Intensität",
            yaxis=dict(range=[0, 1.05]),
            height=self.height,
            template="plotly_white",
            legend=dict(orientation="h", yanchor="bottom", y=-0.3),
            hovermode="x unified",
        )

        return fig

    def create_from_session_messages(
        self,
        messages: Sequence[Dict[str, Any]],
        title: str = "Session-Emotionsverlauf",
    ) -> Optional[Any]:
        """Create trajectory from raw session message dicts.

        Each message dict should have:
        - ``timestamp`` or ``created_at``
        - ``emotional_markers`` (list of str) or ``dominant_emotion``
        - ``confidence`` (float, optional)
        - ``is_crisis`` (bool, optional)

        Args:
            messages: List of message dicts.
            title: Chart title.

        Returns:
            plotly Figure or None.
        """
        data_points: List[EmotionDataPoint] = []
        for i, msg in enumerate(messages):
            ts_raw = msg.get("timestamp") or msg.get("created_at")
            if ts_raw is None:
                continue
            ts = ts_raw if isinstance(ts_raw, datetime) else datetime.fromisoformat(str(ts_raw))

            markers = msg.get("emotional_markers", [])
            emotion = markers[0] if markers else msg.get("dominant_emotion", "neutral")
            confidence = msg.get("confidence", 0.5)
            is_crisis = msg.get("is_crisis", False)

            data_points.append(EmotionDataPoint(
                timestamp=ts,
                emotion=str(emotion),
                confidence=float(confidence),
                message_index=i,
                is_crisis=bool(is_crisis),
            ))

        return self.create_trajectory(data_points, title=title)

    @staticmethod
    def _empty_figure(title: str) -> Any:
        """Return a placeholder figure when no data is available."""
        fig = go.Figure()
        fig.add_annotation(
            text="Noch keine Emotionsdaten verfügbar",
            xref="paper", yref="paper", x=0.5, y=0.5,
            showarrow=False, font=dict(size=16, color="#95a5a6"),
        )
        fig.update_layout(
            title=title, height=300, template="plotly_white",
            xaxis=dict(visible=False), yaxis=dict(visible=False),
        )
        return fig
