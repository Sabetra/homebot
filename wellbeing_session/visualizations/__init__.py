"""
Multi-Modal Visualizations for psychological sessions.

Provides interactive, data-driven visualizations of:
- Emotional trajectory over time (line/area charts)
- Topic distribution (radar/pie charts)
- Session engagement metrics (bar charts)
- Crisis event timeline (scatter plots)
- Mood heatmap (calendar view)

✅ Phase 10: Multi-Modal Visualization foundation.
"""

from .emotion_chart import EmotionTrajectoryChart
from .topic_radar import TopicRadarChart
from .session_metrics import SessionMetricsVisualizer
from .mood_heatmap import MoodHeatmap

__all__ = [
    "EmotionTrajectoryChart",
    "TopicRadarChart",
    "SessionMetricsVisualizer",
    "MoodHeatmap",
]
