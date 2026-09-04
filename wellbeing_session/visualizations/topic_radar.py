"""
Topic Radar Chart — radar/polar visualization of topic distribution.

Shows how conversation topics are distributed across categories
(e.g. relationships, work, health, emotions, goals).

✅ Phase 10: Multi-Modal Visualizations.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

try:
    import plotly.graph_objects as go
    PLOTLY_AVAILABLE = True
except ImportError:
    PLOTLY_AVAILABLE = False
    go = None


# Default topic categories for psychological sessions (German)
DEFAULT_CATEGORIES: List[str] = [
    "Beziehungen",
    "Arbeit/Beruf",
    "Gesundheit",
    "Emotionen",
    "Ziele",
    "Selbstbild",
    "Familie",
    "Bewältigung",
]


class TopicRadarChart:
    """Generates radar/polar charts of topic distribution.

    Usage::

        chart = TopicRadarChart()
        fig = chart.create_radar({"Beziehungen": 0.8, "Arbeit": 0.3, ...})
        st.plotly_chart(fig)
    """

    def __init__(
        self,
        height: int = 450,
        fill_colour: str = "rgba(46, 204, 113, 0.3)",
        line_colour: str = "#2ecc71",
    ) -> None:
        self.height = height
        self.fill_colour = fill_colour
        self.line_colour = line_colour

    @staticmethod
    def is_available() -> bool:
        return PLOTLY_AVAILABLE

    def create_radar(
        self,
        topic_scores: Dict[str, float],
        title: str = "Themenverteilung",
        categories: Optional[List[str]] = None,
    ) -> Optional[Any]:
        """Create an interactive radar chart.

        Args:
            topic_scores: Mapping of topic → score (0.0–1.0).
            title: Chart title.
            categories: Explicit category ordering (optional).

        Returns:
            plotly Figure or None.
        """
        if not PLOTLY_AVAILABLE or go is None:
            logger.warning("Plotly not installed — skipping topic radar")
            return None

        cats = categories or DEFAULT_CATEGORIES
        values = [topic_scores.get(c, 0.0) for c in cats]
        # Close the polygon
        values_closed = values + [values[0]]
        cats_closed = cats + [cats[0]]

        fig = go.Figure()
        fig.add_trace(go.Scatterpolar(
            r=values_closed,
            theta=cats_closed,
            fill="toself",
            fillcolor=self.fill_colour,
            line=dict(color=self.line_colour, width=2),
            name="Aktuell",
            hovertemplate="<b>%{theta}</b>: %{r:.2f}<extra></extra>",
        ))

        fig.update_layout(
            title=dict(text=title, font=dict(size=16)),
            polar=dict(
                radialaxis=dict(visible=True, range=[0, 1]),
            ),
            height=self.height,
            template="plotly_white",
            showlegend=False,
        )

        return fig

    def create_comparison_radar(
        self,
        current_scores: Dict[str, float],
        previous_scores: Dict[str, float],
        title: str = "Themenvergleich",
        categories: Optional[List[str]] = None,
    ) -> Optional[Any]:
        """Create a comparison radar with current vs. previous session.

        Args:
            current_scores: Current topic scores.
            previous_scores: Previous topic scores.
            title: Chart title.
            categories: Category ordering.

        Returns:
            plotly Figure or None.
        """
        if not PLOTLY_AVAILABLE or go is None:
            return None

        cats = categories or DEFAULT_CATEGORIES

        def _closed(scores: Dict[str, float]) -> tuple:
            vals = [scores.get(c, 0.0) for c in cats]
            return vals + [vals[0]], cats + [cats[0]]

        cur_vals, cur_cats = _closed(current_scores)
        prev_vals, prev_cats = _closed(previous_scores)

        fig = go.Figure()
        fig.add_trace(go.Scatterpolar(
            r=cur_vals, theta=cur_cats,
            fill="toself", fillcolor="rgba(46, 204, 113, 0.25)",
            line=dict(color="#2ecc71", width=2), name="Aktuelle Session",
        ))
        fig.add_trace(go.Scatterpolar(
            r=prev_vals, theta=prev_cats,
            fill="toself", fillcolor="rgba(52, 152, 219, 0.15)",
            line=dict(color="#3498db", width=2, dash="dash"), name="Vorherige Session",
        ))

        fig.update_layout(
            title=dict(text=title, font=dict(size=16)),
            polar=dict(radialaxis=dict(visible=True, range=[0, 1])),
            height=self.height,
            template="plotly_white",
            legend=dict(orientation="h", yanchor="bottom", y=-0.2),
        )

        return fig

    def extract_topics_from_triples(
        self,
        triples: List[Dict[str, Any]],
        categories: Optional[List[str]] = None,
    ) -> Dict[str, float]:
        """Heuristic topic extraction from KG triples.

        Maps predicate/object keywords to topic categories.

        Args:
            triples: List of KG triple dicts with subject/predicate/object.
            categories: Category list to score against.

        Returns:
            Dict of category → normalised score.
        """
        cats = categories or DEFAULT_CATEGORIES
        keyword_map: Dict[str, List[str]] = {
            "Beziehungen": ["partner", "freund", "beziehung", "liebe", "trennung", "ehe"],
            "Arbeit/Beruf": ["arbeit", "beruf", "job", "kollege", "chef", "karriere", "projekt"],
            "Gesundheit": ["gesundheit", "schlaf", "schmerz", "krank", "arzt", "therapie"],
            "Emotionen": ["angst", "trauer", "freude", "wut", "stress", "gefühl", "emotion"],
            "Ziele": ["ziel", "plan", "vorhaben", "wunsch", "veränderung", "zukunft"],
            "Selbstbild": ["selbst", "identität", "wert", "stärke", "schwäche", "selbstbewusst"],
            "Familie": ["familie", "eltern", "kind", "geschwister", "mutter", "vater"],
            "Bewältigung": ["bewältigung", "coping", "strategie", "umgang", "routine"],
        }

        scores: Dict[str, float] = {c: 0.0 for c in cats}
        total_triples = max(len(triples), 1)

        for triple in triples:
            text = " ".join([
                str(triple.get("subject", "")),
                str(triple.get("predicate", "")),
                str(triple.get("object", "")),
            ]).lower()

            for cat, keywords in keyword_map.items():
                if cat in scores:
                    for kw in keywords:
                        if kw in text:
                            scores[cat] += 1.0
                            break

        # Normalise to 0–1
        max_score = max(scores.values()) if scores else 1.0
        if max_score > 0:
            scores = {k: v / max_score for k, v in scores.items()}

        return scores
