"""
Context Formatter for psychological sessions.

Formats comprehensive user context into LLM-ready prompt text.
Includes mood context retrieval, goal progress tracking, and relevance scoring.

Extracted from wellbeing_session_interface.py as part of Phase 6 refactoring.
"""

import logging
import re
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional, Callable

from wellbeing_session.utils import (
    format_datetime_section,
    get_relevance_indicator,
    get_trend_emoji,
    get_valence_description,
    get_status_emoji,
)
from wellbeing_session.ui.goal_renderer import GoalUIRenderer

logger = logging.getLogger(__name__)


class ContextFormatter:
    """Formats comprehensive user context for LLM prompts."""

    def __init__(self, chat_logic: Optional[Any] = None) -> None:
        self.chat_logic = chat_logic

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def format_context_for_llm(self, context: Dict[str, Any]) -> str:
        """
        Formatiert den umfassenden User-Kontext für LLM-Prompt.

        Args:
            context: User-Kontext aus _build_comprehensive_user_context()

        Returns:
            Formatierter String für LLM-Prompt
        """
        logger.info(
            f"🔧 [FORMAT-CONTEXT] START - KG={len(context.get('knowledge_graph', []))}, "
            f"Summaries={len(context.get('session_summaries', context.get('previous_sessions', [])))}"
        )

        prompt_parts: List[str] = []

        # === 🆕 AKTUELLES DATUM & UHRZEIT ===
        prompt_parts.extend(format_datetime_section(datetime.now()))

        prompt_parts.append("=" * 80)
        prompt_parts.append("REFLEXIONS-KONTEXT (aus bisherigen Sessions)")
        prompt_parts.append("=" * 80)

        # === 🔥 PERSISTENT PSYCHOLOGICAL PROFILE ===
        self._format_persistent_profile(context, prompt_parts)

        # === KNOWLEDGE GRAPH ===
        self._format_knowledge_graph(context, prompt_parts)

        # === SESSION-SUMMARIES ===
        self._format_session_summaries(context, prompt_parts)

        # === MOOD PROGRESSION ===
        self._format_mood_progression(context, prompt_parts)

        # === SOTA: TREATMENT PLAN (Single Source of Truth across sessions) ===
        self._format_treatment_plan(context, prompt_parts)

        # === CARE ZIELE ===
        if not context.get("treatment_plan"):
            self._format_care_goals(context, prompt_parts)

        # === USER INSIGHTS ===
        self._format_user_insights(context, prompt_parts)

        # === CARE ANWEISUNGEN ===
        # Note: These instructions address the LLM (imperative, German informal).
        # They are embedded inside the formatted_context block which is then
        # prepended with CARE_SYSTEM_PROMPT_BASE by ResponseGenerator.
        prompt_parts.append("\n✅ KONTEXT-NUTZUNG:")
        prompt_parts.append("- Führe konkrete Aufgaben (Tabellen, Listen, Analysen) sofort und vollständig aus — Kontext fließt in den Inhalt ein, nicht als Ersatz für die Aufgabe")
        prompt_parts.append("- Beziehe vergangene Themen natürlich ein, wenn sie für die aktuelle Anfrage relevant sind")
        prompt_parts.append("- Erkenne Entwicklungen und Fortschritte des Benutzers an")
        prompt_parts.append("- Bleib empathisch, warm und unterstützend")
        prompt_parts.append("=" * 80 + "\n")

        formatted_context = "\n".join(prompt_parts)

        # 🔍 DEBUG: Zeige finale Context-Größe
        logger.info(f"✅ [FORMAT-CONTEXT] FINAL - {len(formatted_context)} Zeichen")
        logger.info(f"   - KG-Triples: {len(context.get('knowledge_graph', []))}")
        logger.info(f"   - Session-Summaries: {len(context.get('session_summaries', []))}")
        logger.info(f"   - Care Goals: {len(context.get('care_goals', []))}")
        logger.info(f"   - User Insights: {len(context.get('user_insights', []))}")
        logger.info(f"   - Preview: {formatted_context[:500]}...")

        return formatted_context

    def get_triples_for_mood_context(
        self,
        mood_data: Dict[str, Any],
        all_triples: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """
        🆕 LLM-BASIERTE Auswahl von Triples die zum Mood-Kontext passen.

        Args:
            mood_data: Mood Progression Daten (trend, current_mood, etc.)
            all_triples: Alle verfügbaren Triples

        Returns:
            Liste von Triples die Mood-Kontext erklären könnten
        """
        if not mood_data or not all_triples:
            return []

        trend = mood_data.get("trend", "stable")
        current_mood = mood_data.get("current_mood", "neutral")

        # Wenn LLM verfügbar: Intelligente Auswahl
        if self.chat_logic and hasattr(self.chat_logic, "model_loader"):
            try:
                triples_text = "\n".join(
                    [
                        f"{i+1}. {t.get('subject', '?')} → {t.get('predicate', '?')} → {t.get('object', '?')}"
                        for i, t in enumerate(all_triples[:25])
                    ]
                )

                prompt = f"""Der Benutzer zeigt folgenden emotionalen Zustand:
- Aktuelle Stimmung: {current_mood}
- Trend: {trend}

Hier sind Fakten aus seinen Gesprächen:
{triples_text}

Welche dieser Fakten (Nummern) könnten den emotionalen Zustand erklären oder beeinflussen?
Antworte NUR mit den Nummern, komma-getrennt (z.B.: 1, 5, 12)
Wenn keine relevant sind, antworte mit: KEINE"""

                response = self.chat_logic.model_loader.generate_response(
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=100, temperature=0.2
                )

                if "KEINE" in response.upper():
                    return []

                numbers: List[int] = []
                for token in response.replace("\n", " ").replace(";", ",").split(","):
                    cleaned = token.strip()
                    if cleaned.isdigit():
                        numbers.append(int(cleaned))

                selected: List[Dict[str, Any]] = []
                for num in numbers:
                    if 1 <= num <= len(all_triples):
                        selected.append(all_triples[num - 1])

                logger.info(f"📈 [MOOD-LLM] {len(selected)} mood-relevante Triples via LLM ausgewählt")
                return selected[:5]

            except Exception as e:
                logger.warning(f"📈 [MOOD-LLM] Fehler: {e}")

        # Fallback: score-basierte Priorisierung ohne keyword/pattern matching.
        sorted_triples = sorted(all_triples, key=self._score_triple_priority, reverse=True)
        return sorted_triples[:5]

    def get_goal_progress_triples(
        self, goal_text: str, all_triples: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        🆕 EMBEDDING-BASIERTE Erkennung von Goal-relevanten Triples.

        Args:
            goal_text: Text des Care-Ziels
            all_triples: Alle verfügbaren Triples

        Returns:
            Liste von Triples die zum Ziel passen
        """
        if not goal_text or not all_triples:
            return []

        # Versuche Embedding-basiertes Matching
        try:
            from utils.embedding_singleton import get_embedding_model

            embedding_model = get_embedding_model()

            if embedding_model:
                import numpy as np

                goal_embedding = embedding_model.encode([goal_text])[0]

                scored: List[Dict[str, Any]] = []
                for triple in all_triples:
                    triple_text = (
                        f"{triple.get('subject', '')} "
                        f"{triple.get('predicate', '')} "
                        f"{triple.get('object', '')}"
                    )

                    if triple_text.strip():
                        triple_embedding = embedding_model.encode([triple_text])[0]

                        similarity = float(
                            np.dot(goal_embedding, triple_embedding)
                            / (np.linalg.norm(goal_embedding) * np.linalg.norm(triple_embedding))
                        )

                        if similarity > 0.4:
                            scored.append({**triple, "goal_match_score": similarity})

                scored.sort(key=lambda x: x["goal_match_score"], reverse=True)
                logger.info(f"🎯 [GOAL-PROGRESS-EMB] {len(scored)} matching triples found")
                return scored[:3]

        except Exception as e:
            logger.debug(f"🎯 [GOAL-PROGRESS] Embedding nicht verfügbar: {e}")

        # Fallback: stable score-basierte Auswahl statt Text-/Keyword-Heuristik.
        ranked = sorted(all_triples, key=self._score_triple_priority, reverse=True)
        top = ranked[:3]
        for t in top:
            t["goal_match_score"] = self._score_triple_priority(t)
        return top

    @staticmethod
    def _score_triple_priority(triple: Dict[str, Any]) -> float:
        """Unified priority score for robust, pattern-free fallback ranking."""
        confidence = float(triple.get("confidence", 0.0) or 0.0)
        similarity = float(triple.get("similarity", 0.0) or 0.0)
        combined = float(triple.get("combined_score", 0.0) or 0.0)
        rerank = float(triple.get("rerank_score", 0.0) or 0.0)
        relevance = float(triple.get("relevance_score", 0.0) or 0.0)
        entity = float(triple.get("entity_score", 0.0) or 0.0)
        return (
            0.30 * confidence
            + 0.25 * similarity
            + 0.20 * combined
            + 0.10 * rerank
            + 0.10 * relevance
            + 0.05 * entity
        )

    # ------------------------------------------------------------------
    # Private formatting helpers
    # ------------------------------------------------------------------

    def _format_persistent_profile(
        self, context: Dict[str, Any], parts: List[str]
    ) -> None:
        if not context.get("persistent_profile"):
            return

        profile = context["persistent_profile"]
        parts.append("\n🧠 PSYCHOLOGISCHES PROFIL (LLM-synthetisiert):")
        parts.append(
            f"Confidence: {profile.get('overall_confidence', 0):.2f} | "
            f"Based on: {profile.get('data_sources', {}).get('kg_triples_used', 0)} KG triples, "
            f"{profile.get('data_sources', {}).get('sessions_used', 0)} sessions\n"
        )

        cp = profile.get("core_personality")
        if cp:
            if cp.get("traits"):
                parts.append("  Persönlichkeitsmerkmale:")
                for trait in cp["traits"][:5]:
                    parts.append(f"    • {trait}")
            if cp.get("communication_style"):
                parts.append(f"  Kommunikationsstil: {cp['communication_style']}")

        cs = profile.get("current_state")
        if cs:
            if cs.get("emotional_tone"):
                parts.append(f"  Emotionaler Grundton: {cs['emotional_tone']}")
            if cs.get("life_phase"):
                parts.append(f"  Lebensphase: {cs['life_phase']}")
            if cs.get("primary_concerns"):
                parts.append("  Hauptanliegen:")
                for concern in cs["primary_concerns"][:3]:
                    parts.append(f"    • {concern}")

        tf = profile.get("therapeutic_focus")
        if tf and tf.get("priority_areas"):
            parts.append("  Schwerpunkte:")
            for area in tf["priority_areas"][:3]:
                parts.append(f"    • {area}")

        parts.append("")  # Empty line

    def _format_knowledge_graph(
        self, context: Dict[str, Any], parts: List[str]
    ) -> None:
        if not context.get("knowledge_graph"):
            return

        logger.info(
            f"   [FORMAT-CONTEXT] Formatiere {len(context['knowledge_graph'])} KG-Triples..."
        )
        parts.append("\n📊 BISHERIGES WISSEN (Knowledge Graph):")
        parts.append("Wichtige Fakten und Beziehungen aus vergangenen Gesprächen:\n")

        for triple in context["knowledge_graph"]:
            similarity = triple.get("similarity", 0)
            relevance = triple.get("relevance_score", 0)
            relevance_indicator = get_relevance_indicator(relevance)

            parts.append(
                f"  {relevance_indicator} [{similarity*100:.0f}%] {triple.get('subject')} "
                f"→ [{triple.get('predicate')}] → "
                f"{triple.get('object')}"
            )

    def _format_session_summaries(
        self, context: Dict[str, Any], parts: List[str]
    ) -> None:
        summaries = context.get("session_summaries") or context.get("previous_sessions") or []
        if not summaries:
            return

        parts.append("\n📝 VORHERIGE SESSIONS:")
        parts.append("Zusammenfassungen der letzten Gespräche (nach Relevanz geordnet):\n")

        for i, summary in enumerate(summaries[:5], 1):
            date = summary.get("date", "N/A")[:10] if summary.get("date") else "N/A"
            keyword_matches = summary.get("keyword_matches", 0)
            relevance_score = summary.get("relevance_score", 0)

            rel_indicator = (
                "🔥" if relevance_score > 0.6 else ("⭐" if relevance_score > 0.3 else "")
            )
            match_info = f" [{keyword_matches} Treffer]" if keyword_matches > 0 else ""

            parts.append(f"  {rel_indicator}Session {i} ({date}){match_info}:")
            parts.append(
                f"  {summary.get('summary', 'Keine Zusammenfassung')}\n"
            )

    def _format_mood_progression(
        self, context: Dict[str, Any], parts: List[str]
    ) -> None:
        if not context.get("mood_progression"):
            return

        mood_data = context["mood_progression"]
        parts.append("\n📈 EMOTIONALE ENTWICKLUNG:")

        current_mood = mood_data.get("current_mood", "unbekannt")
        trend = mood_data.get("trend", "stable")
        avg_valence = mood_data.get("average_valence", 0.5)

        trend_emoji = get_trend_emoji(trend)
        valenz_desc = get_valence_description(avg_valence)

        parts.append(f"  Aktueller Zustand: {current_mood}")
        parts.append(f"  Trend: {trend_emoji}")
        parts.append(f"  Durchschnitt: {valenz_desc} ({avg_valence:.2f})")

        if mood_data.get("significant_change"):
            parts.append("  ⚠️ WICHTIG: Signifikante emotionale Veränderung erkannt!")

        related_triples = mood_data.get("related_triples", [])
        if related_triples:
            parts.append("  Mögliche Auslöser:")
            for triple in related_triples[:3]:
                parts.append(
                    f"    • {triple.get('subject')} → {triple.get('predicate')} → {triple.get('object')}"
                )

    def _format_care_goals(
        self, context: Dict[str, Any], parts: List[str]
    ) -> None:
        if not context.get("care_goals"):
            return

        parts.append("\n🎯 PERSÖNLICHE ZIELE:")
        parts.append(
            "Nutzergetragene Ziele aus früheren Sessions; aktive Ziele dürfen "
            "die Unterstützung orientieren, erreichte Ziele dienen nur als Fortschrittskontext:\n"
        )

        for goal in context["care_goals"][:5]:
            goal_text = goal.get("goal", "N/A")
            status = goal.get("status", "active")
            progress_score = goal.get("progress")  # Use actual score, not boolean

            # Use new state machine renderer for correct emoji + full information
            status_emoji = GoalUIRenderer.emoji(status, progress_score)
            status_label = "Erreicht" if status == "achieved" else "Aktiv"
            parts.append(f"  {status_emoji} [{status_label}] {goal_text}")

            progress_triples = goal.get("progress_triples", [])
            if progress_triples:
                parts.append(
                    f"      Fortschritt ({len(progress_triples)} Entwicklungen):"
                )
                for pt in progress_triples[:2]:
                    parts.append(
                        f"        • {pt.get('subject')} → {pt.get('object')}"
                    )

    def _format_treatment_plan(
        self, context: Dict[str, Any], parts: List[str]
    ) -> None:
        """Render the SOTA treatment plan: case formulation, active goals,
        TTM stage, current focus, carry-forward notes, latest risk."""
        tp = context.get("treatment_plan")
        if not tp or not tp.get("plan"):
            return

        parts.append("\n📋 BEHANDLUNGSPLAN (sitzungsübergreifend):")

        focus = tp.get("focus") or {}
        focus_relevant = self._is_focus_relevant(
            context.get("current_user_input", ""),
            tp,
        )

        formulation = tp.get("formulation")
        if formulation:
            parts.append("  Fallkonzeption (5P):")
            for label, key in (
                ("Anliegen", "presenting"),
                ("Prädisposition", "predisposing"),
                ("Auslöser", "precipitating"),
                ("Aufrechterhaltend", "perpetuating"),
                ("Ressourcen", "protective"),
            ):
                items = formulation.get(key) or []
                if items:
                    parts.append(f"    • {label}: " + "; ".join(items[:3]))

        stage = tp.get("stage")
        if stage and stage.get("stage"):
            parts.append(
                f"  TTM-Stadium: {stage['stage']} "
                f"(Konfidenz {float(stage.get('confidence') or 0):.2f})"
            )

        primary = tp.get("primary_goal")
        if primary and focus_relevant:
            progress = primary.get("progress")
            progress_s = (
                f"{float(progress):.0%}" if isinstance(progress, (int, float)) else "—"
            )
            parts.append(
                f"  🎯 Primärziel: {primary['title']} "
                f"[Status={primary['status']}, Fortschritt={progress_s}]"
            )
            if primary.get("metric"):
                parts.append(f"     Maß: {primary['metric']}")

        secondary = (tp.get("secondary_goals") or []) if focus_relevant else []
        if secondary:
            parts.append("  Sekundärziele:")
            for g in secondary[:2]:
                parts.append(f"    • {g['title']} (P{g['priority']})")

        active = (tp.get("active_goals") or []) if focus_relevant else []
        non_focus = [g for g in active if not (
            primary and g["id"] == primary["id"]
        ) and not any(g["id"] == s["id"] for s in secondary)]
        if non_focus:
            parts.append(f"  Weitere aktive Ziele ({len(non_focus)}):")
            for g in non_focus[:4]:
                progress = g.get("progress")
                progress_s = (
                    f"{float(progress):.0%}" if isinstance(progress, (int, float))
                    else "—"
                )
                parts.append(
                    f"    · P{g['priority']} | {progress_s} | {g['title']}"
                )

        if focus_relevant and focus.get("planned_steps"):
            parts.append("  Geplante Interventionen für diese Session:")
            for it in focus["planned_steps"][:3]:
                parts.append(f"    → {it}")

        prev = tp.get("previous_focus")
        if focus_relevant and prev and prev.get("carry_forward_notes"):
            parts.append("  Aus letzter Session offen geblieben:")
            parts.append(f"    {prev['carry_forward_notes'][:400]}")

        risk = tp.get("latest_risk")
        safety_state = (tp.get("safety_episode") or {}).get("state")
        if (
            risk
            and risk.get("level")
            and risk["level"] != "none"
            and safety_state in {"check_required", "acute_active"}
        ):
            parts.append(
                f"  ⚠️ Risiko-Level: {risk['level']} "
                f"(Konfidenz {float(risk.get('confidence') or 0):.2f})"
            )
            drivers = risk.get("drivers") or []
            if drivers:
                parts.append("     Treiber: " + "; ".join(drivers[:3]))
            protect = risk.get("protective_factors") or []
            if protect:
                parts.append("     Schutzfaktoren: " + "; ".join(protect[:3]))

        mbc = tp.get("mbc") or []
        if mbc:
            scores = [
                f"{m['item_key']}={float(m['derived_score']):.2f}"
                for m in mbc if isinstance(m.get("derived_score"), (int, float))
            ]
            if scores:
                parts.append("  Selbst-Beobachtung (WHO5-like): " + ", ".join(scores))

    @staticmethod
    def _is_focus_relevant(user_input: str, treatment_plan: Dict[str, Any]) -> bool:
        focus = treatment_plan.get("focus") or {}
        if focus.get("mode") != "confirmed":
            return False
        query_tokens = {
            token for token in re.findall(r"[^\W_]+", str(user_input).casefold())
            if len(token) >= 4
        }
        if query_tokens & {"ziel", "fokus", "fortsetzen", "weitermachen"}:
            return True
        goal_texts = []
        primary = treatment_plan.get("primary_goal") or {}
        goal_texts.append(str(primary.get("title") or ""))
        goal_texts.extend(
            str(goal.get("title") or "")
            for goal in treatment_plan.get("secondary_goals") or []
        )
        goal_tokens = {
            token
            for token in re.findall(r"[^\W_]+", " ".join(goal_texts).casefold())
            if len(token) >= 4
        }
        return bool(query_tokens & goal_tokens)

    def _format_user_insights(
        self, context: Dict[str, Any], parts: List[str]
    ) -> None:
        if not context.get("user_insights"):
            return

        parts.append("\n💡 ERKANNTE MUSTER (aus bisherigen Sessions):")
        parts.append("Wiederkehrende Themen und Verhaltensmuster:\n")

        for insight in context["user_insights"][:8]:
            insight_text = insight.get("insight") or insight.get("value", "N/A")
            confidence = insight.get("confidence", 0)
            insight_type = insight.get("type", insight.get("insight_type", "unknown"))
            category = insight.get("category", "")

            type_emoji = {
                "dominant_theme": "📌",
                "emotional_pattern": "💭",
                "relationship_cluster": "🔗",
                "behavioral_pattern": "🔄",
                "personality_trait": "🧠",
                "emotional_state": "💙",
                "life_event": "📅",
                "coping_strategy": "💪",
                "coping": "💪",
                "relationships": "👥",
            }.get(insight_type, "💡")

            conf_normalized = confidence if confidence <= 1 else confidence / 100
            confidence_stars = "⭐" * min(4, int(conf_normalized * 4))
            category_info = f" ({category})" if category else ""

            # Provenance-Hinweise (aus session_context_builder._load_user_insights)
            mention_count = insight.get("mention_count")
            temporal = insight.get("temporal_context")
            provenance_parts: List[str] = []
            if isinstance(mention_count, int) and mention_count > 1:
                provenance_parts.append(f"{mention_count}× wiederholt")
            if temporal and temporal != "current":
                provenance_parts.append(f"Zeitbezug: {temporal}")
            provenance_info = (
                f" [{'; '.join(provenance_parts)}]" if provenance_parts else ""
            )

            parts.append(
                f"  {type_emoji} {confidence_stars} {insight_text}{category_info}{provenance_info}"
            )

