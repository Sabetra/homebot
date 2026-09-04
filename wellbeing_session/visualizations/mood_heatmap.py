"""
Mood Heatmap — calendar-style heatmap of daily mood scores.

Visualises mood trends over weeks/months as a colour-coded grid,
similar to GitHub contribution graphs.

✅ Phase 10: Multi-Modal Visualizations.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Sequence

logger = logging.getLogger(__name__)

try:
    import plotly.graph_objects as go
    PLOTLY_AVAILABLE = True
except ImportError:
    PLOTLY_AVAILABLE = False
    go = None


# Mood → numeric score mapping
MOOD_SCORES: Dict[str, float] = {
    "sehr_schlecht": 0.0,
    "schlecht": 0.2,
    "traurig": 0.25,
    "trauer": 0.25,
    "angst": 0.3,
    "stress": 0.35,
    "neutral": 0.5,
    "okay": 0.55,
    "hoffnung": 0.65,
    "gut": 0.75,
    "freude": 0.85,
    "sehr_gut": 1.0,
}

WEEKDAY_LABELS = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]


class MoodHeatmap:
    """Generates calendar-style mood heatmaps.

    Usage::

        heatmap = MoodHeatmap()
        fig = heatmap.create_heatmap(daily_moods)
        st.plotly_chart(fig)
    """

    def __init__(self, height: int = 300) -> None:
        self.height = height

    @staticmethod
    def is_available() -> bool:
        return PLOTLY_AVAILABLE

    @staticmethod
    def mood_to_score(mood: str) -> float:
        """Convert a mood label to a numeric score (0–1)."""
        return MOOD_SCORES.get(mood.lower().replace(" ", "_"), 0.5)

    def create_heatmap(
        self,
        daily_moods: Dict[str, float],
        title: str = "Stimmungsverlauf",
        weeks: int = 8,
    ) -> Optional[Any]:
        """Create a calendar-style heatmap of daily mood scores.

        Args:
            daily_moods: Mapping of ISO date string → score (0.0–1.0).
            title: Chart title.
            weeks: Number of weeks to display.

        Returns:
            plotly Figure or None.
        """
        if not PLOTLY_AVAILABLE or go is None:
            return None

        today = datetime.now().date()
        start = today - timedelta(days=7 * weeks - 1)

        # Build grid: rows = weekdays (0-6), cols = weeks
        z: List[List[Optional[float]]] = [
            [None for _ in range(weeks)] for _ in range(7)
        ]
        text: List[List[str]] = [
            ["" for _ in range(weeks)] for _ in range(7)
        ]

        for day_offset in range(7 * weeks):
            d = start + timedelta(days=day_offset)
            weekday = d.weekday()  # 0=Mon
            week_idx = day_offset // 7
            if week_idx < weeks:
                score = daily_moods.get(d.isoformat(), None)
                z[weekday][week_idx] = score
                label = d.strftime("%d.%m")
                text[weekday][week_idx] = (
                    f"{label}: {score:.2f}" if score is not None else f"{label}: –"
                )

        # Week labels (column headers)
        week_labels = []
        for w in range(weeks):
            d = start + timedelta(days=7 * w)
            week_labels.append(f"KW{d.isocalendar()[1]}")

        fig = go.Figure(go.Heatmap(
            z=z,
            x=week_labels,
            y=WEEKDAY_LABELS,
            text=text,
            hovertemplate="%{text}<extra></extra>",
            colorscale=[
                [0.0, "#fee2e2"],   # red-ish (bad mood)
                [0.25, "#fecaca"],
                [0.5, "#fef3c7"],   # yellow (neutral)
                [0.75, "#d1fae5"],  # green-ish
                [1.0, "#059669"],   # dark green (great mood)
            ],
            zmin=0, zmax=1,
            colorbar=dict(
                title="Stimmung",
                tickvals=[0, 0.25, 0.5, 0.75, 1.0],
                ticktext=["Schlecht", "Mäßig", "Neutral", "Gut", "Sehr gut"],
            ),
            xgap=3, ygap=3,
        ))

        fig.update_layout(
            title=dict(text=title, font=dict(size=16)),
            height=self.height,
            template="plotly_white",
            yaxis=dict(autorange="reversed"),
        )

        return fig

    def create_from_sessions(
        self,
        sessions: Sequence[Dict[str, Any]],
        title: str = "Stimmungsverlauf",
        weeks: int = 8,
    ) -> Optional[Any]:
        """Create heatmap from session data.

        Each session dict should contain:
        - ``start_time`` or ``date`` (datetime or ISO string)
        - ``emotional_state`` (str) or ``mood_score`` (float)

        Args:
            sessions: List of session dicts.
            title: Chart title.
            weeks: Number of weeks to display.

        Returns:
            plotly Figure or None.
        """
        daily_moods: Dict[str, float] = {}

        for sess in sessions:
            ts = sess.get("start_time") or sess.get("date")
            if ts is None:
                continue
            if isinstance(ts, str):
                try:
                    ts = datetime.fromisoformat(ts)
                except ValueError:
                    continue
            date_key = ts.date().isoformat() if isinstance(ts, datetime) else str(ts)

            score = sess.get("mood_score")
            if score is None:
                emotion = sess.get("emotional_state", "neutral")
                score = self.mood_to_score(str(emotion))

            # Average if multiple sessions per day
            if date_key in daily_moods:
                daily_moods[date_key] = (daily_moods[date_key] + float(score)) / 2
            else:
                daily_moods[date_key] = float(score)

        return self.create_heatmap(daily_moods, title=title, weeks=weeks)
