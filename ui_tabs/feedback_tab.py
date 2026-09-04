"""Renderer for the main Streamlit feedback-analysis tab."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

import streamlit as st


def _get_pie_autotexts(pie_result: Any) -> list[Any]:
    if hasattr(pie_result, "texts"):
        text_groups = pie_result.texts
        return list(text_groups[1]) if len(text_groups) > 1 else []
    if isinstance(pie_result, tuple) and len(pie_result) == 3:
        return list(pie_result[2])
    return []


def render_feedback_tab(
    logger: logging.Logger,
    *,
    feedback_logger_available: bool,
    feedback_logger: Any,
) -> None:
    st.header("📈 Intelligente Feedback-Analyse")

    with st.expander("🔍 Debug: Session State Info", expanded=False):
        st.caption(f"**Session State Feedbacks:** {len(st.session_state.feedback_data)}")
        if st.session_state.feedback_data:
            st.json(st.session_state.feedback_data[-5:])
        else:
            st.info("Keine Feedbacks im Session State")

    db_stats = {}
    advanced_analytics = {}

    if feedback_logger_available and feedback_logger:
        db_stats = feedback_logger.get_statistics(source="combined")
        advanced_analytics = feedback_logger.get_advanced_analytics()

    if db_stats.get("source"):
        st.caption(f"📊 Datenquelle: {db_stats['source']}")

    has_session_feedback = bool(st.session_state.feedback_data)
    has_db_feedback = int(db_stats.get("total", 0)) > 0

    if has_session_feedback or has_db_feedback:
        if has_session_feedback:
            positive = len([f for f in st.session_state.feedback_data if f.get("type") == "positive"])
            negative = len([f for f in st.session_state.feedback_data if f.get("type") == "negative"])
            total = positive + negative
            satisfaction_rate = (positive / total * 100) if total > 0 else 0
        elif has_db_feedback:
            total = int(db_stats.get("total", 0))
            positive = int(db_stats.get("positive", 0))
            negative = int(db_stats.get("negative", 0))
            satisfaction_rate = float(db_stats.get("satisfaction_rate", 0))
        else:
            total = positive = negative = satisfaction_rate = 0
            satisfaction_rate = (positive / total * 100) if total > 0 else 0

        col1, col2, col3, col4 = st.columns(4)

        with col1:
            st.metric(
                "👍 Positives Feedback",
                positive,
                delta=f"+{positive}" if int(positive) > 0 else None,
                help="Anzahl der positiven Bewertungen",
            )

        with col2:
            st.metric(
                "👎 Negatives Feedback",
                negative,
                delta=f"-{negative}" if int(negative) > 0 else None,
                delta_color="inverse",
                help="Anzahl der negativen Bewertungen",
            )

        with col3:
            if int(total) > 0:
                satisfaction = (int(positive) / int(total)) * 100
                delta_satisfaction = satisfaction - 50
                st.metric(
                    "😊 Zufriedenheitsrate",
                    f"{satisfaction:.1f}%",
                    delta=f"{delta_satisfaction:+.1f}%" if abs(delta_satisfaction) > 0.1 else None,
                    help="Prozentsatz positiver Bewertungen",
                )
            else:
                st.metric("😊 Zufriedenheitsrate", "Keine Daten")

        with col4:
            if int(total) >= 2:
                recent_feedback = st.session_state.feedback_data[-5:] if st.session_state.feedback_data else []
                if recent_feedback and len(recent_feedback) > 0:
                    recent_positive = len([f for f in recent_feedback if f.get("type") == "positive"])
                    recent_rate = (recent_positive / len(recent_feedback)) * 100
                    st.metric(
                        "📊 Trend (5 neueste)",
                        f"{recent_rate:.0f}%",
                        help="Zufriedenheitsrate der letzten 5 Bewertungen",
                    )
                else:
                    st.metric("📊 Trend", "Zu wenig Daten")
            else:
                st.metric("📊 Trend", "Zu wenig Daten")

        st.divider()

        col_left, col_right = st.columns([3, 2])

        with col_left:
            st.subheader("📊 Feedback-Visualisierung")

            if int(total) > 0:
                import matplotlib
                import matplotlib.pyplot as plt

                matplotlib.rcParams["font.family"] = "sans-serif"
                matplotlib.rcParams["font.sans-serif"] = ["Segoe UI", "DejaVu Sans", "Arial", "sans-serif"]

                fig, ax = plt.subplots(figsize=(12, 10))

                sizes = [int(positive), int(negative)]
                labels = ["Positiv (+)", "Negativ (−)"]
                colors = ["#2ecc71", "#e74c3c"]
                explode = (0.05, 0.05)

                pie_result = ax.pie(
                    sizes,
                    labels=labels,
                    autopct="%1.1f%%",
                    colors=colors,
                    explode=explode,
                    shadow=True,
                    startangle=90,
                )

                for autotext in _get_pie_autotexts(pie_result):
                    autotext.set_color("white")
                    autotext.set_fontweight("bold")
                    autotext.set_fontsize(12)

                ax.set_title("Feedback-Verteilung", fontsize=16, fontweight="bold", pad=20)

                plt.tight_layout()
                st.pyplot(fig)

                if len(st.session_state.feedback_data) >= 3:
                    st.subheader("📈 Feedback-Verlauf")

                    import pandas as pd

                    feedback_data = []
                    for i, feedback in enumerate(st.session_state.feedback_data):
                        feedback_data.append(
                            {
                                "Index": i + 1,
                                "Typ": 1 if feedback.get("type") == "positive" else -1,
                                "Label": "(+) Positiv" if feedback.get("type") == "positive" else "(−) Negativ",
                            }
                        )

                    df = pd.DataFrame(feedback_data)

                    fig, ax = plt.subplots(figsize=(14, 8))

                    colors = ["#2ecc71" if val == 1 else "#e74c3c" for val in df["Typ"]]
                    ax.bar(df["Index"], df["Typ"], color=colors, alpha=0.7)

                    ax.set_xlabel("Feedback-Nummer")
                    ax.set_ylabel("Bewertung")
                    ax.set_title("Feedback-Verlauf über Zeit")
                    ax.set_ylim(-1.5, 1.5)
                    ax.set_yticks([-1, 0, 1])
                    ax.set_yticklabels(["Negativ (−)", "Neutral", "Positiv (+)"])
                    ax.grid(True, alpha=0.3)

                    plt.tight_layout()
                    st.pyplot(fig)
            else:
                st.info("📊 Keine Daten für Visualisierung verfügbar")

        with col_right:
            st.subheader("🎯 Handlungsempfehlungen")

            if int(total) > 0:
                satisfaction_rate = (int(positive) / int(total)) * 100

                if satisfaction_rate >= 80:
                    st.success("🎉 **Exzellente Performance!**")
                    st.markdown(
                        """
                        **Ihre AI-Antworten sind sehr zufriedenstellend:**
                        - Hohe Benutzeranzufriedenheit ({:.1f}%)
                        - Kontinuierliche Qualität beibehalten
                        - Erfolgreiche Antwortmuster dokumentieren
                        """.format(satisfaction_rate)
                    )

                elif satisfaction_rate >= 60:
                    st.info("👍 **Gute Performance**")
                    st.markdown(
                        """
                        **Solide AI-Performance mit Verbesserungspotential:**
                        - Zufriedenheitsrate: {:.1f}%
                        - Negative Feedback analysieren
                        - Antwortqualität weiter optimieren
                        - Spezifische Verbesserungsbereiche identifizieren
                        """.format(satisfaction_rate)
                    )

                else:
                    st.warning("⚠️ **Verbesserung erforderlich**")
                    st.markdown(
                        """
                        **Niedriger Zufriedenheitswert erfordert Aufmerksamkeit:**
                        - Aktuelle Rate: {:.1f}%
                        - Dringende Analyse der negativen Bewertungen
                        - Anpassung der AI-Parameter erwägen
                        - Benutzererwartungen überprüfen
                        """.format(satisfaction_rate)
                    )

            if len(st.session_state.feedback_data) >= 3:
                st.subheader("🔍 Trend-Analyse")

                recent_3 = st.session_state.feedback_data[-3:]
                recent_positive = len([f for f in recent_3 if f.get("type") == "positive"])
                recent_trend = recent_positive / len(recent_3)

                if recent_trend > 0.66:
                    st.markdown("📈 **Aufwärtstrend** - Letzte Antworten sehr positiv!")
                elif recent_trend < 0.33:
                    st.markdown("📉 **Abwärtstrend** - Letzte Antworten problematisch!")
                else:
                    st.markdown("📊 **Stabiler Trend** - Gemischte Bewertungen")

            st.subheader("✅ Nächste Schritte")

            if negative > 0:
                st.markdown("🔍 **Sofortige Maßnahmen:**")
                st.markdown(f"- {negative} negative Bewertung(en) analysieren")
                st.markdown("- Häufige Probleme identifizieren")
                st.markdown("- AI-Parameter adjustieren")

            if positive > 0:
                st.markdown("🎯 **Erfolg verstärken:**")
                st.markdown(f"- {positive} positive Muster dokumentieren")
                st.markdown("- Erfolgreiche Antworttypen notieren")
                st.markdown("- Best Practices ableiten")

            st.subheader("📤 Daten-Export")

            if st.button("📊 Feedback-Report erstellen", key="generate_report"):
                satisfaction_rate = (positive / total) * 100 if total > 0 else 0

                report = {
                    "generated_at": datetime.now().isoformat(),
                    "total_feedback": total,
                    "positive_feedback": positive,
                    "negative_feedback": negative,
                    "satisfaction_rate": satisfaction_rate,
                    "feedback_details": st.session_state.feedback_data,
                }

                report_json = json.dumps(report, indent=2, default=str)

                st.download_button(
                    label="💾 Report als JSON herunterladen",
                    data=report_json,
                    file_name=f"feedback_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json",
                    mime="application/json",
                )

                st.success("✅ Report generiert! Klicken Sie oben zum Download.")

        if advanced_analytics.get("analytics_available"):
            st.divider()
            st.subheader("🔬 Erweiterte Datenbank-Analytik")

            if advanced_analytics.get("category_breakdown"):
                st.markdown("### 📂 Feedback nach Kategorie")

                cat_cols = st.columns(len(advanced_analytics["category_breakdown"]))
                for idx, (category, stats) in enumerate(advanced_analytics["category_breakdown"].items()):
                    with cat_cols[idx]:
                        st.metric(
                            f"📊 {category.title()}",
                            f"{stats['satisfaction_rate']}%",
                            delta=f"+{stats['positive']} / -{stats['negative']}",
                        )

            if advanced_analytics.get("temporal_trends") and len(advanced_analytics["temporal_trends"]) > 0:
                st.markdown("### 📅 Zeitlicher Verlauf (30 Tage)")

                import pandas as pd
                import plotly.express as px

                df_trends = pd.DataFrame(advanced_analytics["temporal_trends"])

                fig = px.line(
                    df_trends,
                    x="date",
                    y=["positive", "negative"],
                    title="Feedback-Trend über Zeit",
                    labels={"value": "Anzahl", "variable": "Typ"},
                    color_discrete_map={"positive": "#2ecc71", "negative": "#e74c3c"},
                )

                st.plotly_chart(fig, width="stretch")

            if advanced_analytics.get("recent_negative_comments"):
                st.markdown("### 💬 Neueste Negativ-Kommentare")

                for comment_data in advanced_analytics["recent_negative_comments"]:
                    with st.expander(f"📝 {comment_data['timestamp'][:10]}", expanded=False):
                        st.markdown(f"**Kommentar:** {comment_data['comment']}")
                        st.caption(f"Zeitstempel: {comment_data['timestamp']}")

            st.markdown("### 🎯 Optimierungs-Empfehlungen")

            if feedback_logger_available and feedback_logger:
                insights = feedback_logger.get_optimization_insights(min_samples=5)

                if insights.get("status") == "ready":
                    st.success(f"✅ Analyse bereit ({insights['samples']} Samples)")

                    col_opt1, col_opt2 = st.columns(2)

                    with col_opt1:
                        st.metric(
                            "🎯 Gesamtzufriedenheit",
                            f"{insights['satisfaction_rate']*100:.1f}%",
                            delta="Gut" if insights["satisfaction_rate"] >= 0.7 else "Verbesserung nötig",
                        )

                        if insights.get("optimal_search_depth"):
                            st.metric(
                                "🔍 Optimale Search-Depth",
                                insights["optimal_search_depth"],
                                delta=f"{insights['optimal_depth_satisfaction']*100:.1f}% Erfolg",
                            )

                    with col_opt2:
                        if insights.get("recommendations"):
                            st.markdown("**🔧 Aktionen:**")
                            for rec in insights["recommendations"]:
                                st.info(
                                    f"**{rec['issue']}** → {rec['action']} ({rec['suggested_adjustment']:+.2f})"
                                )
                        else:
                            st.success("✅ Keine Anpassungen nötig - System läuft optimal!")

                elif insights.get("status") == "insufficient_data":
                    st.info(f"📊 Sammle mehr Daten ({insights['samples']}/{insights['required']} Samples)")
            else:
                st.info("ℹ️ Erweiterte Optimierungs-Insights sind in der aktuellen Konfiguration nicht verfügbar.")

        if has_db_feedback and db_stats.get("by_category"):
            st.divider()
            st.subheader("📊 Statistik-Details")

            with st.expander("📈 Detaillierte Statistiken", expanded=False):
                st.json(db_stats)
