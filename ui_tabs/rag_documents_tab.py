"""Renderer for the main Streamlit RAG documents tab."""

from __future__ import annotations

from collections.abc import Callable

import streamlit as st


def render_rag_documents_tab(render_reclassify_panel: Callable[[], None]) -> None:
    """Render RAG document management and legacy reclassification controls."""
    st.header("📚 RAG-Dokumente Verwaltung")

    from kg_dashboard import render_kg_dashboard

    render_kg_dashboard()

    st.divider()
    with st.expander("🔧 Wartung — Legacy-Inhalte neu klassifizieren", expanded=False):
        render_reclassify_panel()
