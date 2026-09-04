"""Renderer for the main Streamlit settings tab."""

from __future__ import annotations

import logging
from typing import Any

import streamlit as st


def render_settings_tab(logger: logging.Logger, tab_health_snapshot: Any) -> None:
    """Render the settings tab while keeping business logic in one place."""
    st.header("🔧 System-Einstellungen")

    st.subheader("🤖 Agent-Modus")
    use_react = st.toggle(
        "ReAct Agent aktivieren",
        value=st.session_state.get("use_react_agent", True),
        help=(
            "Aktiviert Hybrid-Routing mit ReAct-Eskalation. SIMPLE- und "
            "PLAN_EXECUTE-Fälle bleiben schnell, komplexe mehrstufige Fälle "
            "dürfen auf den iterativen ReAct-Agent (LangGraph) wechseln."
        ),
    )
    changed = use_react != st.session_state.get("use_react_agent")
    st.session_state.use_react_agent = use_react
    if st.session_state.initialized and hasattr(st.session_state, "chat_logic"):
        st.session_state.chat_logic.settings["use_react_agent"] = use_react
    if changed:
        st.toast(f"{'✅ Hybrid mit ReAct-Eskalation aktiviert' if use_react else '⚙️ ReAct-Eskalation deaktiviert (SIMPLE/PLAN_EXECUTE)'}")

    st.divider()

    st.subheader("⚙️ RAG-Parameter")
    st.caption("Diese Einstellungen werden **direkt** bei jeder Anfrage angewendet.")

    col1, col2 = st.columns(2)

    with col1:
        # Search depth (rag_k = Anzahl Dokumente pro Query)
        search_depth = st.slider(
            "🔍 Search Depth (k)",
            min_value=1,
            max_value=20,
            value=st.session_state.get("search_depth", 5),
            help="Anzahl der RAG-Ergebnisse pro Query. Wird direkt an orchestrator.set_rag_config(k=…) übergeben.",
        )
        st.session_state.search_depth = search_depth

        # FAISS Confidence -- SYNCHRONISIERT mit dem Tab-1-Control
        # Beide schreiben auf den gleichen session_state Key 'faiss_confidence'
        faiss_conf = st.slider(
            "🎯 FAISS Confidence Threshold",
            min_value=0.50,
            max_value=0.95,
            value=st.session_state.get("faiss_confidence", 0.70),
            step=0.05,
            help="Mindest-Ähnlichkeit für FAISS-Suchergebnisse. Gleicher Wert wie im Chat-Tab.",
        )
        st.session_state.faiss_confidence = faiss_conf

    with col2:
        # Multi-query N -- gleicher Range wie Tab 1 (1-20)
        mq_n = st.slider(
            "🔢 Multi-Query N",
            min_value=1,
            max_value=20,
            value=st.session_state.get("mq_n", 5),
            help="Anzahl der generierten Sub-Queries. Wird sofort an den Orchestrator übermittelt.",
        )
        if mq_n != st.session_state.get("mq_n"):
            st.session_state.mq_n = mq_n
            # Sofort an Orchestrator übergeben (kein "Apply"-Button nötig)
            if st.session_state.initialized and hasattr(st.session_state, "chat_logic"):
                if hasattr(st.session_state.chat_logic, "orchestrator"):
                    if st.session_state.chat_logic.orchestrator:
                        st.session_state.chat_logic.orchestrator.set_multiquery_config(n=mq_n)
                        st.toast(f"✅ Multi-Query N auf {mq_n} gesetzt")
        else:
            st.session_state.mq_n = mq_n

    st.divider()

    st.subheader("🧠 Adaptive RAG")
    st.caption(
        "Query-Komplexitäts-Routing mit LLM-Router: einfache Queries erhalten "
        "schnelles One-Shot Retrieval, komplexe Queries lösen Multi-Hop Reasoning aus "
        "(basierend auf DOTRAG / Adaptive-RAG, SOTA 2026)."
    )

    adaptive_rag_enabled = st.toggle(
        "Adaptive-RAG-Strategie aktivieren",
        value=st.session_state.get("adaptive_strategy", True),
        help=(
            "Aktiviert den LLM-Router, der Queries nach Komplexität klassifiziert "
            "(shallow → One-Shot FAISS, deep → Multi-Hop BFS Retrieval). "
            "~200ms Overhead pro Query, ~50 Tokens."
        ),
    )
    adaptive_changed = adaptive_rag_enabled != st.session_state.get("adaptive_strategy")
    st.session_state.adaptive_strategy = adaptive_rag_enabled
    if st.session_state.initialized and hasattr(st.session_state, "chat_logic"):
        orch = getattr(st.session_state.chat_logic, "orchestrator", None)
        if orch is not None and hasattr(orch, "adaptive_strategy"):
            orch.adaptive_strategy = adaptive_rag_enabled
    if adaptive_changed:
        st.toast(
            f"{'✅ Adaptive-RAG aktiviert (LLM-Router + Multi-Hop)' if adaptive_rag_enabled else '⚙️ Adaptive-RAG deaktiviert (plain RAG)'}"
        )

    st.divider()

    st.subheader("💾 System-Informationen")

    col1, col2, col3 = st.columns(3)

    with col1:
        st.metric("🔧 Streamlit Version", st.__version__)

    with col2:
        if st.session_state.initialized:
            st.metric("🤖 AI Status", "Geladen", delta="Aktiv")
        else:
            st.metric("🤖 AI Status", "Nicht geladen", delta="Inaktiv", delta_color="off")

    with col3:
        session_count = len(st.session_state.get("chat_history", []))
        st.metric("💬 Nachrichten", session_count)

    with st.expander("🩺 Tab Runtime Health", expanded=False):
        st.caption(
            "Strict runtime contracts for core tab dependencies "
            "(fail-fast validation at startup)."
        )
        st.json(tab_health_snapshot.to_dict())

    st.divider()

    st.subheader("🧁 Knowledge Graph Wartung")
    st.caption(
        "Bereinigt Inkonsistenzen im Knowledge Graph: verwaiste Einträge, "
        "falsche Frequenzen, Garbage-Entities, Case-Duplikate. "
        "Normalerweise **nicht nötig** — Root-Cause-Fixes verhindern neuen Drift automatisch. "
        "Nützlich nach manuellem DB-Eingriff oder abgebrochenem Import."
    )

    if st.button(
        "🔧 KG-Konsistenz rebuilden",
        help="Führt alle 8 Phasen der KG-Bereinigung aus (30–90 Sek.)",
        disabled=not st.session_state.initialized,
    ):
        if st.session_state.initialized and hasattr(st.session_state, "chat_logic"):
            # SOTA: RAG-Store über ToolManager.rag Property (konsistent mit
            # agent_chatbot_logic.py:387 und chat_tab.py:1234).
            # Fallback auf get_global_rag_store() für Robustheit.
            rag_store = None
            chat_logic = st.session_state.chat_logic

            # Primär: orchestrator.tools.rag (etabliertes Muster)
            orchestrator = getattr(chat_logic, "orchestrator", None)
            if orchestrator is not None:
                tools = getattr(orchestrator, "tools", None)
                if tools is not None:
                    rag_store = getattr(tools, "rag", None)

            # Fallback: direkter Singleton-Zugriff (falls orchestrator/tools None)
            if rag_store is None:
                try:
                    from agent.tools import get_global_rag_store
                    rag_store = get_global_rag_store()
                except Exception:
                    rag_store = None

            if rag_store and hasattr(rag_store, "rebuild_kg_consistency"):
                progress_bar = st.progress(0, text="Starte KG-Rebuild...")
                status_text = st.empty()

                def _ui_progress(phase: int, total: int, desc: str) -> None:
                    progress_bar.progress(phase / total, text=f"Phase {phase}/{total}: {desc}")
                    status_text.caption(f"⏳ {desc}...")

                rebuild_stats = rag_store.rebuild_kg_consistency(progress_callback=_ui_progress)

                progress_bar.progress(1.0, text="✅ Fertig!")

                if rebuild_stats.get("success"):
                    phases = rebuild_stats.get("phases", {})
                    duration = rebuild_stats.get("total_time_seconds", 0)
                    st.success(
                        f"✅ KG-Rebuild in {duration}s abgeschlossen:\n"
                        f"- Garbage-Entities entfernt: {phases.get('garbage_entities', 0)}\n"
                        f"- Verwaiste triple_quality: {phases.get('orphan_triple_quality', 0)}\n"
                        f"- Case-Duplikate gemergt: {phases.get('case_groups_merged', 0)}\n"
                        f"- Frequenzen korrigiert: {phases.get('frequency_corrected', 0)}\n"
                        f"- Tote Entities entfernt: {phases.get('dead_entities_pruned', 0)}\n"
                        f"- Fehlende Entities ergänzt: {phases.get('untracked_entities_added', 0)}"
                    )
                else:
                    st.error(f"❌ KG-Rebuild fehlgeschlagen: {rebuild_stats.get('error', 'Unbekannt')}")
            else:
                st.warning("⚠️ RAG Store nicht verfügbar — bitte AI-System laden")
        else:
            st.warning("⚠️ AI-System nicht geladen")

    st.divider()

    st.subheader("🗑️ System-Verwaltung")

    col1, col2, col3 = st.columns(3)

    with col1:
        if st.button("🔄 Chat zurücksetzen"):
            st.session_state.chat_history = []
            # ✅ FIX: Feedback-Daten NICHT löschen - sind permanente Bewertungen!
            st.success("✅ Chat zurückgesetzt (Feedback bleibt erhalten)")
            st.rerun()

    with col2:
        # ✅ NEU: Separater Button zum Feedback löschen
        if st.button("🗑️ Feedback löschen", help="Löscht nur die Feedback-Daten, Chat bleibt erhalten"):
            feedback_count = len(st.session_state.feedback_data)
            st.session_state.feedback_data = []
            st.success(f"✅ {feedback_count} Feedbacks gelöscht")
            st.rerun()

    with col3:
        if st.button("🧹 GPU-Cache leeren"):
            from gpu_optimizer import clear_gpu_cache

            status = clear_gpu_cache()
            if status == "cleared":
                st.success("✅ GPU-Cache geleert")
            elif status == "unavailable":
                st.info("ℹ️ Kein GPU-Cache verfügbar")
            else:
                st.warning("⚠️ Cache-Leerung fehlgeschlagen")
