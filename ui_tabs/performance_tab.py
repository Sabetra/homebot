"""Renderer for the main Streamlit performance tab."""

from __future__ import annotations

import logging
from datetime import datetime

import streamlit as st


def _get_strixkat_dashboard() -> dict | None:
    """Lazy-Hole des StrixKAT Dashboard (keine harte Import-Dependency)."""
    try:
        from agent.strixkat_eval import StrixKATEvalEngine
        engine = StrixKATEvalEngine.get_instance()
        return engine.get_dashboard_metrics()
    except Exception as exc:
        logging.getLogger(__name__).debug("StrixKAT dashboard unavailable: %s", exc)
        return None


def _get_feedback_summary() -> dict | None:
    """Lazy-Hole des Feedback-Zusammenfassungs."""
    try:
        from utils.feedback_logger import FeedbackLogger
        fl = FeedbackLogger.get_instance()
        return fl.get_statistics()
    except Exception as exc:
        logging.getLogger(__name__).debug("FeedbackLogger unavailable: %s", exc)
        return None


def render_performance_tab(logger: logging.Logger) -> None:
    st.header("📊 System Performance Monitor")

    # ── Row 1: Core metrics ──────────────────────────────────────────
    col1, col2, col3, col4 = st.columns(4)

    with col1:
        st.metric(
            "💬 Chat-Nachrichten",
            len(st.session_state.chat_history),
            help="Anzahl der Nachrichten in der aktuellen Session",
        )

    with col2:
        fb = _get_feedback_summary()
        if fb:
            positive_feedback = fb.get("positive_count", 0)
            negative_feedback = fb.get("negative_count", 0)
            total_feedback = positive_feedback + negative_feedback
            satisfaction_rate = fb.get("satisfaction_rate", 0.0)
        else:
            positive_feedback = len(
                [f for f in st.session_state.feedback_data if f.get("type") == "positive"]
            )
            negative_feedback = len(
                [f for f in st.session_state.feedback_data if f.get("type") == "negative"]
            )
            total_feedback = positive_feedback + negative_feedback
            satisfaction_rate = (
                positive_feedback / total_feedback * 100 if total_feedback > 0 else 0.0
            )

        st.metric(
            "📈 Feedback",
            total_feedback,
            delta=f"+{positive_feedback - negative_feedback}" if total_feedback > 0 else None,
            help="Gesamtes Feedback mit Sentiment-Delta",
        )

    with col3:
        st.metric(
            "😊 Satisfaction",
            f"{satisfaction_rate:.0f}%",
            help="Prozent der positiven Feedbacks vom Gesamtfedback",
        )

    with col4:
        ai_status = "✅ Geladen" if st.session_state.initialized else "❌ Nicht geladen"
        st.metric("🤖 AI-Status", ai_status, help="Status des AI-Systems")

    st.divider()

    # ── Row 2: StrixKAT Eval Metrics ────────────────────────────────
    strixkat = _get_strixkat_dashboard()
    if strixkat and strixkat.get("status") == "ok":
        st.subheader("🎯 StrixKAT Eval-Metriken")

        e1, e2, e3, e4 = st.columns(4)
        with e1:
            score = strixkat.get("latest_score")
            st.metric(
                "📊 Gesamt-Score",
                f"{score:.2f}" if score is not None else "N/A",
                help="Gewichteter Gesamt-Score aller Eval-Metriken",
            )
        with e2:
            trend = strixkat.get("trend", "unknown")
            trend_icon = {"improving": "📈", "stable": "➡️", "degrading": "📉"}.get(trend, "❓")
            st.metric(
                "📈 Trend",
                f"{trend_icon} {trend}",
                help="Trend-Richtung der Eval-Scores",
            )
        with e3:
            baseline = strixkat.get("baseline_mean")
            st.metric(
                "🎯 Baseline",
                f"{baseline:.2f}" if baseline is not None else "N/A",
                help="Baseline-Mittelwert zum Regression-Vergleich",
            )
        with e4:
            count = strixkat.get("eval_count", 0)
            st.metric(
                "🔢 Eval-Count",
                count,
                help="Anzahl der durchgeführten Evaluationen",
            )

        # Per-metric breakdown
        if "per_metric" in strixkat:
            pm = strixkat["per_metric"]
            m1, m2, m3, m4 = st.columns(4)
            with m1:
                v = pm.get("faithfulness")
                st.metric("Faithfulness", f"{v:.2f}" if v is not None else "N/A")
            with m2:
                v = pm.get("answer_relevance")
                st.metric("Answer Relevance", f"{v:.2f}" if v is not None else "N/A")
            with m3:
                v = pm.get("context_precision")
                st.metric("Context Precision", f"{v:.2f}" if v is not None else "N/A")
            with m4:
                v = pm.get("answer_correctness")
                st.metric("Answer Correctness", f"{v:.2f}" if v is not None else "N/A")

        st.divider()

    col_left, col_right = st.columns(2)

    with col_left:
        st.subheader("🔍 System-Diagnose")

        if st.button("🔍 Speicher-Diagnose", key="memory_diag"):
            with st.spinner("Analysiere System-Performance..."):
                import gc
                import psutil

                memory = psutil.virtual_memory()

                st.write("**💾 Arbeitsspeicher:**")
                st.metric("RAM Gesamt", f"{memory.total / (1024**3):.1f} GB")
                st.metric("RAM Verwendet", f"{memory.used / (1024**3):.1f} GB ({memory.percent:.1f}%)")
                st.metric("RAM Verfügbar", f"{memory.available / (1024**3):.1f} GB")

                cpu_percent = psutil.cpu_percent(interval=1)
                cpu_count = psutil.cpu_count()

                st.write("**🖥️ Prozessor:**")
                st.metric("CPU Auslastung", f"{cpu_percent:.1f}%")
                st.metric("CPU Kerne", f"{cpu_count} Kerne")

                gc.collect()
                objects_count = len(gc.get_objects())
                st.write("**🐍 Python-Laufzeit:**")
                st.metric("Python Objekte", f"{objects_count:,}")

                st.write("**🎮 GPUs:**")

                all_gpus: list = []
                placement_desc = ""
                try:
                    from utils import vram_monitor as _vram
                    from utils.gpu_devices import get_placement

                    all_gpus = _vram.get_all_gpu_snapshots()
                    placement_desc = get_placement().describe()
                except Exception as exc:
                    logger.debug(f"GPU-Platzierung/Snapshot nicht verfügbar: {exc}", exc_info=True)

                if placement_desc:
                    st.caption(f"🎯 {placement_desc}")

                if all_gpus:
                    for g in all_gpus:
                        role = g.get("role") or "GPU"
                        st.write(f"**{role} — {g['name']}** (NVML {g['nvml_index']})")
                        st.metric("Speicher", f"{g['total_gb']:.1f} GB")
                        st.metric("Verwendet", f"{g['used_gb']:.2f} GB ({g['utilization_pct']:.0f}%)")
                        st.metric("Frei", f"{g['free_gb']:.2f} GB")
                        if g.get("temp_c") is not None:
                            st.metric("Temperatur", f"{g['temp_c']}°C")
                else:
                    torch_ok = False
                    try:
                        import torch

                        torch_ok = torch.cuda.is_available()
                        if torch_ok:
                            gpu_memory = torch.cuda.get_device_properties(0).total_memory
                            gpu_allocated = torch.cuda.memory_allocated(0)
                            gpu_cached = torch.cuda.memory_reserved(0)

                            st.write("**🎮 GPU (CUDA, Prozess):**")
                            st.metric("GPU Speicher", f"{gpu_memory / (1024**3):.1f} GB")
                            st.metric("GPU Verwendet", f"{gpu_allocated / (1024**3):.2f} GB")
                            st.metric("GPU Cache", f"{gpu_cached / (1024**3):.2f} GB")
                    except ImportError:
                        st.info("🎮 PyTorch nicht verfügbar für GPU-Monitoring")
                    except Exception as exc:
                        logger.debug(f"GPU-Info probe failed: {exc}")
                    if not torch_ok:
                        st.warning("🎮 GPUs nicht sichtbar (nvidia-smi/CUDA nicht verfügbar)")

                st.write("**⚙️ Python-Prozesse:**")
                python_processes = []
                for proc in psutil.process_iter(["pid", "name", "memory_info", "cpu_percent"]):
                    try:
                        if "python" in proc.info["name"].lower():
                            python_processes.append(
                                {
                                    "PID": proc.info["pid"],
                                    "Name": proc.info["name"],
                                    "Memory (MB)": f"{proc.info['memory_info'].rss / (1024**2):.1f}",
                                    "CPU %": f"{proc.info['cpu_percent']:.1f}",
                                }
                            )
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        continue

                if python_processes:
                    st.dataframe(python_processes, width="stretch")

    with col_right:
        st.subheader("📊 Performance-Verlauf")

        if len(st.session_state.chat_history) > 1:
            timestamps = [msg["timestamp"] for msg in st.session_state.chat_history]
            message_counts = list(range(1, len(timestamps) + 1))

            import pandas as pd

            df = pd.DataFrame({"Zeit": timestamps, "Nachrichten": message_counts})

            st.line_chart(df.set_index("Zeit")["Nachrichten"])
            st.caption("📈 Chat-Aktivität über Zeit")
        else:
            st.info("📊 Noch nicht genügend Daten für Verlaufsdiagramm")

        if st.session_state.initialized and st.session_state.chat_logic:
            st.write("**🤖 Modell-Informationen:**")

            model_info = getattr(st.session_state.chat_logic, "model_name", "Unbekannt")
            st.text(f"Modell: {model_info}")

            try:
                import torch

                if torch.cuda.is_available():
                    device = torch.cuda.current_device()
                    device_name = torch.cuda.get_device_name(device)
                    st.text(f"GPU: {device_name}")

                    memory_allocated = torch.cuda.memory_allocated(device)
                    memory_cached = torch.cuda.memory_reserved(device)
                    st.text(f"VRAM: {memory_allocated / (1024**3):.2f} GB belegt")
                    st.text(f"Cache: {memory_cached / (1024**3):.2f} GB")
            except (ImportError, RuntimeError) as exc:
                logger.debug(f"GPU-Info konnte nicht gelesen werden: {exc}", exc_info=True)
                st.text("GPU-Info nicht verfügbar")

        st.write("**⚡ Schnellaktionen:**")

        if st.button("🧹 Garbage Collection", key="gc_action"):
            import gc

            collected = gc.collect()
            st.success(f"✅ {collected} Objekte bereinigt")

        if st.button("🔄 Cache löschen", key="clear_cache"):
            from gpu_optimizer import clear_gpu_cache

            status = clear_gpu_cache()
            if status == "cleared":
                st.success("✅ GPU-Cache geleert")
            elif status == "unavailable":
                st.info("ℹ️ Kein GPU-Cache verfügbar")
            else:
                st.warning("⚠️ Cache-Leerung fehlgeschlagen")
