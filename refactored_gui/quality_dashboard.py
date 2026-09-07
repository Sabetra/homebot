"""
RAG Quality Dashboard — Streamlit Tab Component (SOTA v2)
==========================================================

Provides a manual-trigger quality dashboard for the RAG database.
Integrates with the main Streamlit chatbot as an additional tab.

Features:
    - DB health overview (chunk/triple/document counts, avg scores)
    - Manual structural audit trigger (no LLM, safe to run live)
    - ★ Content-Type + Defect + Age distribution views
    - Manual reranker-based KG grounding audit trigger
    - Quarantine management (view, restore, purge)
    - Audit log viewer
    - ★ Dry-run remediation with severity tiers + feedback protection
    - ★ Trend tracking (quality over time)
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from typing import Any, Dict, List, Optional

import streamlit as st

logger = logging.getLogger(__name__)

# Ensure parent directory is importable
parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)


def _get_quality_manager() -> Optional[Any]:
    """Lazy-load the RAGQualityManager singleton."""
    if "quality_manager" not in st.session_state or st.session_state.quality_manager is None:
        try:
            from agent.rag_store.core.quality import RAGQualityManager

            # Try to find DB path from existing RAG store
            db_path = None
            try:
                from agent.unified_rag_store import UnifiedRagStore

                # Prefer the already-running store from session state
                store = None
                if hasattr(st, "session_state"):
                    chat_logic = getattr(st.session_state, "chat_logic", None)
                    if chat_logic is not None:
                        orch = getattr(chat_logic, "orchestrator", None)
                        if orch is not None:
                            tools = getattr(orch, "tools", None)
                            if tools is not None:
                                store = getattr(tools, "rag", None)

                # Fallback: shared factory (returns existing instance if same db_path)
                if store is None:
                    store = UnifiedRagStore.get_shared()

                if hasattr(store, 'db_path'):
                    db_path = store.db_path
            except Exception:
                pass
            
            if not db_path:
                # Fallback: try common paths
                for p in ["rag_store.db", os.path.join(parent_dir, "rag_store.db")]:
                    if os.path.exists(p):
                        db_path = p
                        break
            
            if not db_path:
                logger.warning("RAG DB path not found")
                return None

            # Try to get reranker
            reranker = None
            try:
                from agent.reranker import get_reranker
                reranker = get_reranker()
                # Trigger lazy-load: CrossEncoderReranker defers model loading
                # until first use. is_available returns False before loading.
                # Without this, the QualityManager gets reranker=None.
                if hasattr(reranker, '_ensure_loaded'):
                    reranker._ensure_loaded()
                if hasattr(reranker, 'is_available') and not reranker.is_available:
                    reranker = None
            except Exception:
                pass

            st.session_state.quality_manager = RAGQualityManager(
                db_path=db_path,
                reranker=reranker,
            )
        except Exception as e:
            logger.error(f"Failed to init quality manager: {e}")
            st.session_state.quality_manager = None
    
    return st.session_state.quality_manager


def render_quality_dashboard():
    """Render the RAG Quality Dashboard tab."""
    st.header("📊 RAG Quality Dashboard (SOTA v2)")
    st.caption("Manuell gesteuerte Qualitätsüberwachung & Remediation — mit Content-Type, Defects, Trend-Tracking")

    qm = _get_quality_manager()
    if qm is None:
        st.error("⚠️ Quality Manager konnte nicht geladen werden. Ist die RAG-Datenbank vorhanden?")
        return

    # ── Section 1: Health Overview ──────────────────────────────
    st.subheader("🏥 DB Health Overview")
    
    if st.button("🔄 Refresh Stats", key="refresh_stats"):
        st.session_state.pop("health_stats", None)
    
    if "health_stats" not in st.session_state:
        with st.spinner("Lade Statistiken..."):
            st.session_state.health_stats = qm.get_db_health_stats()
    
    stats = st.session_state.health_stats
    
    if "error" in stats:
        st.error(f"Fehler: {stats['error']}")
    else:
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            st.metric("📄 Dokumente", stats.get("total_documents", "?"))
        with col2:
            st.metric("📝 Chunks", stats.get("total_chunks", "?"))
        with col3:
            st.metric("🔗 Triples", stats.get("total_triples", "?"))
        with col4:
            q_stats = stats.get("quarantine", {})
            st.metric("🗑️ Quarantäne", q_stats.get("total", 0))
        
        col5, col6, col7 = st.columns(3)
        with col5:
            avg_q = stats.get("chunk_quality_avg")
            st.metric("⭐ Ø Chunk-Qualität", f"{avg_q:.3f}" if avg_q is not None else "–")
        with col6:
            avg_g = stats.get("avg_grounding_score")
            st.metric("🎯 Ø Grounding", f"{avg_g:.3f}" if avg_g is not None else "–")
        with col7:
            st.metric("📝 Letztes Audit", stats.get("last_audit", "Nie") or "Nie")

    st.divider()

    # ── Section 2: Structural Audit ─────────────────────────────
    st.subheader("🔍 Strukturelles Audit (SOTA v2)")
    st.caption("Prüft Orphans, Duplikate, Boilerplate, Content-Types, Defects, Predicate-IDF. Kein LLM nötig.")
    
    if st.button("▶️ Strukturelles Audit starten", key="run_structural_audit"):
        with st.spinner("Audit läuft... (kann 1-5 Min dauern bei großer DB)"):
            progress_bar = st.progress(0)
            step_counter = [0]
            total_steps = 10
            
            def progress_cb(msg: str):
                step_counter[0] += 1
                pct = min(step_counter[0] / total_steps, 1.0)
                progress_bar.progress(pct, text=msg)
            
            report = qm.run_structural_audit(progress_callback=progress_cb)
            progress_bar.progress(1.0, text="✅ Fertig!")
            st.session_state.last_audit_report = report
            # Invalidate health stats
            st.session_state.pop("health_stats", None)
    
    if "last_audit_report" in st.session_state:
        report = st.session_state.last_audit_report
        st.success(report.summary())
        
        # Show details as expandable
        with st.expander("📋 Audit-Details", expanded=True):
            col1, col2, col3, col4 = st.columns(4)
            with col1:
                st.metric("🔗 Orphan Chunks", report.orphan_chunks)
                st.metric("🔗 Orphan Triples", report.orphan_triples)
                st.metric("📏 Embedding-Mismatch", report.embedding_dim_mismatch)
            with col2:
                st.metric("📎 Near-Duplicates", report.near_duplicates)
                st.metric("📰 Boilerplate", report.boilerplate_chunks)
                st.metric("🐛 Defect Chunks", report.defect_chunks)
            with col3:
                st.metric("📏 Kurz-Chunks", report.short_chunks)
                st.metric("🔗 URL-Dumps", report.url_dump_chunks)
                st.metric("⚠️ Low Quality", report.low_quality_chunks)
            with col4:
                st.metric("❓ Ungrounded Triples", report.ungrounded_triples)
                st.metric("📝 Generische Prädikate", report.generic_predicate_triples)
                st.metric("🔄 Regex-Fallback Triples", report.regex_fallback_triples)
        
        # ★ Content-Type Distribution
        ct_dist = getattr(report, 'content_type_distribution', {})
        if ct_dist:
            with st.expander("📊 Content-Type Verteilung", expanded=False):
                for ctype, count in sorted(ct_dist.items(), key=lambda x: x[1], reverse=True):
                    pct = (count / max(report.total_chunks, 1)) * 100
                    st.write(f"**{ctype}**: {count:,} ({pct:.1f}%)")
        
        # ★ Age Distribution
        age_dist = getattr(report, 'age_distribution', {})
        if age_dist:
            with st.expander("📅 Altersverteilung (informational)", expanded=False):
                st.caption("Alter ist KEIN Qualitätssignal. Historisches Wissen ist wertvoll.")
                for bucket, count in age_dist.items():
                    st.write(f"**{bucket}**: {count:,}")
        
        if report.errors:
            st.error(f"Fehler: {'; '.join(report.errors)}")

    st.divider()

    # ── Section 3: Reranker KG Audit ───────────────────────────
    st.subheader("🎯 KG Grounding Audit (Cross-Encoder)")
    st.caption("Verifiziert KG-Triples gegen Quelltext via Cross-Encoder Reranker. Kein LLM nötig, aber dauert 5-15 Min.")
    
    kg_sample_size = st.number_input(
        "Sample-Größe (Anzahl Triples)", 
        min_value=100, max_value=500000, value=5000, step=1000,
        help="Anzahl der Triples, die pro Durchlauf verifiziert werden. Mehr = genauer, aber dauert länger.",
        key="kg_audit_sample_size",
    )
    
    if st.button("▶️ KG Grounding Audit starten", key="run_reranker_audit"):
        estimated_min = max(1, kg_sample_size // 1000)
        with st.spinner(f"Verifikation läuft... (~{estimated_min}-{estimated_min * 3} Min für {kg_sample_size:,} Triples)"):
            result = qm.run_reranker_audit(sample_limit=kg_sample_size)
            st.session_state.last_reranker_audit = result
            st.session_state.pop("health_stats", None)
    
    if "last_reranker_audit" in st.session_state:
        r = st.session_state.last_reranker_audit
        col1, col2, col3 = st.columns(3)
        with col1:
            st.metric("Verifiziert", r.get("total_verified", 0))
        with col2:
            st.metric("✅ Grounded", r.get("grounded_count", 0))
        with col3:
            st.metric("❌ Ungrounded", r.get("ungrounded_count", 0))

    st.divider()

    # ── Section 4: Remediation (SOTA v2) ───────────────────────
    st.subheader("🧹 Remediation (SOTA v2)")
    st.caption("3-stufiges Quarantäne-System mit Feedback-Schutz. Erst Audit durchführen!")
    
    with st.expander("⚙️ Remediation Einstellungen"):
        st.markdown("""
        **Tier 0 (AUTO)**: Orphans, Encoding-Garbage, Trivial → immer sicher  
        **Tier 1 (EMPFOHLEN)**: Duplikate, Boilerplate, Cookie-Banner → Quarantäne empfohlen  
        **Tier 2 (BEOBACHTEN)**: Low Quality, generische Prädikate → nur loggen  
        
        🛡️ **Feedback-Schutz**: Chunks mit positivem User-Feedback werden NIE auto-quarantiniert.
        """)
        q_orphans = st.checkbox("Tier 0: Orphans quarantinieren", value=True)
        q_defects = st.checkbox("Tier 0: Defect-Chunks quarantinieren", value=True)
        q_duplicates = st.checkbox("Tier 1: Near-Duplicates quarantinieren", value=True)
        q_boilerplate = st.checkbox("Tier 1: Boilerplate quarantinieren", value=True)
        q_ungrounded = st.checkbox("Tier 1: Ungrounded Triples quarantinieren", value=False, 
                                    help="Erst nach KG-Grounding-Audit aktivieren!")
        q_generic_pred = st.checkbox("Tier 2: Generische Prädikate quarantinieren", value=False)
        min_structural = st.slider("Min. Structural Score", 0.0, 1.0, 0.2, 0.05)
        min_grounding = st.slider("Min. Grounding Score", 0.0, 1.0, 0.15, 0.05)
    
    col_dry, col_exec = st.columns(2)
    
    with col_dry:
        if st.button("🔍 Dry-Run (Vorschau)", key="dry_run_remediation"):
            with st.spinner("Dry-Run läuft..."):
                result = qm.run_remediation(
                    quarantine_orphans=q_orphans,
                    quarantine_duplicates=q_duplicates,
                    quarantine_boilerplate=q_boilerplate,
                    quarantine_defects=q_defects,
                    quarantine_ungrounded=q_ungrounded,
                    quarantine_generic_predicates=q_generic_pred,
                    min_structural_score=min_structural,
                    min_grounding_score=min_grounding,
                    dry_run=True,
                )
                st.session_state.remediation_preview = result
            
            st.info(f"🔍 Dry-Run Ergebnis:")
            st.json({k: v for k, v in result.items() if k != "plan"})
            if result.get("plan"):
                with st.expander(f"📋 Geplante Aktionen ({len(result['plan'])} Schritte)"):
                    for action in result["plan"][:20]:
                        st.write(f"- **{action.get('action', '?')}**: {action.get('reason', '?')} "
                                 f"(Tier {action.get('tier', '?')}, {action.get('count', '?')} Items)")
            if result.get("feedback_protected", 0) > 0:
                st.success(f"🛡️ {result['feedback_protected']} Chunks durch positives Feedback geschützt")
    
    with col_exec:
        if st.button("🧹 Remediation ausführen", key="run_remediation", type="primary"):
            with st.spinner("Remediation läuft..."):
                result = qm.run_remediation(
                    quarantine_orphans=q_orphans,
                    quarantine_duplicates=q_duplicates,
                    quarantine_boilerplate=q_boilerplate,
                    quarantine_defects=q_defects,
                    quarantine_ungrounded=q_ungrounded,
                    quarantine_generic_predicates=q_generic_pred,
                    min_structural_score=min_structural,
                    min_grounding_score=min_grounding,
                    dry_run=False,
                )
                st.session_state.pop("health_stats", None)
            
            total = (result.get("tier_0_quarantined", 0) + 
                     result.get("tier_1_quarantined", 0))
            st.success(f"✅ {total} Items in Quarantäne verschoben, "
                       f"{result.get('tier_2_logged', 0)} Tier-2 Items geloggt, "
                       f"{result.get('feedback_protected', 0)} durch Feedback geschützt")
            st.json({k: v for k, v in result.items() if k != "plan"})

    st.divider()

    # ── Section 5: Quarantine Management ───────────────────────
    st.subheader("🗑️ Quarantäne-Verwaltung")

    col1, col2 = st.columns(2)
    with col1:
        if st.button("📋 Quarantäne-Items anzeigen", key="show_quarantine"):
            items = qm.get_quarantine_items()
            st.session_state.quarantine_items = items
    
    with col2:
        if st.button("🗑️ Abgelaufene Items löschen", key="purge_expired"):
            conn = qm._get_connection()
            try:
                count = qm.purge_expired_quarantine(conn)
                st.info(f"🗑️ {count} abgelaufene Items gelöscht")
            finally:
                conn.close()

    # ── Section 5b: Triple Regeneration from Quarantine ────────
    st.markdown("---")
    st.markdown("#### 🔄 Triple-Regeneration aus Quarantäne")
    st.caption(
        "Extrahiert neue LLM-basierte Triples aus den Source-Chunks der quarantinierten Triples. "
        "Nur rekonstruierbare Source-Chunks werden verarbeitet; irrecoverable Einträge werden im Execute-Lauf "
        "als failed markiert und eskalieren nach Retry-Limit zu permanent_failed. "
        "Nur gegrounded Triples (Cross-Encoder + Lexik-Check) werden eingefügt. "
        "Benötigt: LLM geladen + Reranker verfügbar."
    )

    regen_batch = st.number_input(
        "Batch-Größe (Quarantine-Einträge pro Durchlauf)",
        min_value=10, max_value=5000, value=100, step=50,
        help="Wie viele quarantinierte Triples pro Durchlauf verarbeitet werden. "
             "Jeder Eintrag löst LLM-Extraktion aus → dauert ~2-5s pro Chunk.",
        key="regen_batch_size",
    )
    regen_min_grounding = st.slider(
        "Min. Grounding-Score für neue Triples",
        0.0, 1.0, 0.3, 0.05,
        help="Neue Triples unter diesem Score werden verworfen.",
        key="regen_min_grounding",
    )

    col_regen_dry, col_regen_exec = st.columns(2)
    with col_regen_dry:
        if st.button("🔍 Regeneration Dry-Run", key="regen_dry_run"):
            with st.spinner("Analysiere quarantinierte Triples..."):
                result = qm.regenerate_quarantined_triples(
                    dry_run=True,
                    batch_size=regen_batch,
                    min_grounding_score=regen_min_grounding,
                )
                st.session_state.regen_preview = result
            if result.get("errors"):
                for err in result["errors"]:
                    st.error(f"❌ {err}")
            else:
                st.info(
                    f"🔍 **Dry-Run Ergebnis:**\n"
                    f"- {result['quarantine_entries_processed']} Quarantine-Einträge zu verarbeiten\n"
                    f"- {result.get('recoverable_quarantine_entries', 0)} rekonstruierbar\n"
                    f"- {result.get('unrecoverable_quarantine_entries', 0)} nicht rekonstruierbar\n"
                    f"- {result['source_chunks_found']} Source-Chunks gefunden\n"
                    f"- {result['source_chunks_missing']} Source-Chunks fehlen\n"
                    f"- {result['already_regenerated_skipped']} bereits regeneriert (übersprungen)\n"
                    f"- Statusänderungen im Dry-Run: completed={result.get('quarantine_marked_completed', 0)}, "
                    f"failed={result.get('quarantine_marked_failed', 0)}"
                )

    with col_regen_exec:
        if st.button("🔄 Regeneration ausführen", key="regen_execute", type="primary"):
            progress_container = st.empty()
            def regen_progress(msg):
                progress_container.info(f"🔄 {msg}")
            with st.spinner("Triple-Regeneration läuft... (LLM + Cross-Encoder)"):
                result = qm.regenerate_quarantined_triples(
                    dry_run=False,
                    batch_size=regen_batch,
                    min_grounding_score=regen_min_grounding,
                    progress_callback=regen_progress,
                )
                st.session_state.pop("health_stats", None)
                st.session_state.pop("quarantine_items", None)
            
            if result.get("errors"):
                for err in result["errors"]:
                    st.error(f"❌ {err}")
            
            inserted = result.get("triples_inserted", 0)
            extracted = result.get("triples_extracted", 0)
            grounded = result.get("triples_grounded", 0)
            duration = result.get("duration_seconds", 0)
            
            if inserted > 0:
                st.success(
                    f"✅ **{inserted} neue Triples eingefügt** "
                    f"({extracted} extrahiert → {grounded} gegrounded → {inserted} eingefügt)\n\n"
                    f"⏱️ Dauer: {duration}s | "
                    f"Rekonstruierbar: {result.get('recoverable_quarantine_entries', 0)} | "
                    f"Nicht rekonstruierbar: {result.get('unrecoverable_quarantine_entries', 0)} | "
                    f"Duplikate übersprungen: {result.get('triples_duplicate_skipped', 0)} | "
                    f"Ungrounded verworfen: {result.get('triples_ungrounded_skipped', 0)} | "
                    f"Quarantäne-Status: completed={result.get('quarantine_marked_completed', 0)}, "
                    f"failed={result.get('quarantine_marked_failed', 0)}, "
                    f"permanent_failed={result.get('quarantine_marked_permanent_failed', 0)}"
                )
                if result.get("kg_reload_recommended"):
                    st.warning("♻️ Neue Triples eingefügt — KG/FAISS-Reload empfohlen, damit sie im Retrieval wirksam werden.")
            else:
                st.info(
                    f"Keine neuen Triples eingefügt. "
                    f"({extracted} extrahiert, {grounded} gegrounded, "
                    f"{result.get('recoverable_quarantine_entries', 0)} rekonstruierbar, "
                    f"{result.get('unrecoverable_quarantine_entries', 0)} nicht rekonstruierbar, "
                    f"{result.get('triples_duplicate_skipped', 0)} Duplikate, "
                    f"{result.get('triples_ungrounded_skipped', 0)} ungrounded, "
                    f"completed={result.get('quarantine_marked_completed', 0)}, "
                    f"failed={result.get('quarantine_marked_failed', 0)})"
                )
            st.json({k: v for k, v in result.items() if k != "errors"})

    
    if "quarantine_items" in st.session_state and st.session_state.quarantine_items:
        items = st.session_state.quarantine_items
        st.write(f"**{len(items)} Items in Quarantäne:**")
        
        for item in items[:50]:  # Show max 50
            with st.expander(f"[{item['source_table']}] {item['source_id']} — {item['reason']}"):
                st.text(f"Quarantiniert: {item['quarantined_at']}")
                st.text(f"Auto-Delete: {item.get('auto_delete_after', 'N/A')}")
                if st.button(f"↩️ Wiederherstellen #{item['id']}", key=f"restore_{item['id']}"):
                    conn = qm._get_connection()
                    try:
                        qm.restore_from_quarantine(conn, [item['id']])
                        st.success(f"✅ Item {item['id']} wiederhergestellt!")
                        st.session_state.pop("quarantine_items", None)
                        st.session_state.pop("health_stats", None)
                    finally:
                        conn.close()

    st.divider()

    # ── Section 6: Quality Trends ─────────────────────────────
    st.subheader("📈 Quality Trends")
    st.caption("Zeigt Qualitätsmetriken über Zeit — wird bei jedem Audit automatisch aktualisiert.")
    
    trend_metric = st.selectbox(
        "Metrik wählen",
        ["total_chunks", "total_triples", "orphan_chunks", "orphan_triples",
         "near_duplicates", "boilerplate_chunks", "defect_chunks",
         "low_quality_chunks", "ungrounded_triples", "generic_predicate_triples"],
        key="trend_metric_select"
    )
    
    if st.button("📈 Trend laden", key="load_trend"):
        trend_data = qm.get_trend_data(trend_metric, limit=20)
        if trend_data:
            st.session_state.trend_data = trend_data
            st.session_state.trend_metric_name = trend_metric
        else:
            st.info("Keine Trend-Daten vorhanden. Führe zuerst ein Audit durch.")
    
    if "trend_data" in st.session_state and st.session_state.trend_data:
        data = st.session_state.trend_data
        metric_name = st.session_state.get("trend_metric_name", "?")
        st.write(f"**{metric_name}** — letzte {len(data)} Audit-Läufe:")
        
        # Simple table display
        for entry in reversed(data):
            ts = entry.get("audit_timestamp", "")[:19]
            val = entry.get("metric_value", 0)
            delta = entry.get("delta_from_previous")
            delta_str = ""
            if delta is not None and delta != 0:
                delta_str = f" (Δ {'+' if delta > 0 else ''}{delta})"
            st.text(f"  [{ts}]  {val:>8}{delta_str}")

    st.divider()

    # ── Section 7: Audit Log ──────────────────────────────────
    st.subheader("📋 Audit Log")
    
    if st.button("📋 Log laden", key="load_audit_log"):
        log_entries = qm.get_audit_log(limit=30)
        st.session_state.audit_log = log_entries
    
    if "audit_log" in st.session_state and st.session_state.audit_log:
        for entry in st.session_state.audit_log:
            ts = entry.get("timestamp", "")
            action = entry.get("action", "")
            target = entry.get("target_table", "") or ""
            tid = entry.get("target_id", "") or ""
            details_raw = entry.get("details", "") or ""
            
            # Truncate long details
            details_short = details_raw[:120] + "..." if len(details_raw) > 120 else details_raw
            
            st.text(f"[{ts[:19]}] {action} {target}/{tid} — {details_short}")
