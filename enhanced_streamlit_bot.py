"""
Enhanced AI-Powered Streamlit Chatbot
=====================================

Clean orchestrator for the Streamlit UI with modular tab rendering.
"""

from __future__ import annotations

# Pre-import yaml before streamlit to avoid import-hook race issues.
import yaml
_ = yaml.error

import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator, Optional, Tuple

import streamlit as st

from wellbeing_session_interface import WellbeingSessionInterface
from model_loader import (
    DEFAULT_MODEL as MODEL_LOADER_DEFAULT,
    LLM_CONTEXT_SIZE,
    ModelLoader,
    get_available_models,
)
from utils.model_registry import scan_models, models_root
from agent_chatbot_logic import AgentChatbotLogic
from agent.streaming_events import ChatEvent
from utils.followup_question_extractor import extract_followup_questions
from ui_tabs.chat_tab import render_chat_tab
from ui_tabs.feedback_tab import render_feedback_tab
from ui_tabs.performance_tab import render_performance_tab
from ui_tabs.wellbeing_tab import render_wellbeing_tab
from ui_tabs.rag_documents_tab import render_rag_documents_tab
from ui_tabs.settings_tab import render_settings_tab
from utils.runtime_policy import parse_bool_env
from utils.tab_runtime_health import collect_tab_health_snapshot, TabHealthError
from i18n import t as i18n_t, set_language as i18n_set_language, LocaleNegotiator

# Finance tab policy is explicit and deployment-controlled.
_FINANCE_POLICY_DEFAULT = os.getenv("SHOW_FINANCE_TAB", "1")
FINANCE_TAB_ENABLED_BY_POLICY = parse_bool_env("APP_ENABLE_FINANCE_TAB", _FINANCE_POLICY_DEFAULT)

# Finance tab is optional -- bot still works if the finance stack is missing.
if FINANCE_TAB_ENABLED_BY_POLICY:
    try:
        from finance.tab import render_finance_tab
        FINANCE_TAB_AVAILABLE = True
    except Exception as _finance_exc:  # pragma: no cover - import guard
        render_finance_tab = None
        FINANCE_TAB_AVAILABLE = False
        _FINANCE_IMPORT_ERROR: Optional[str] = str(_finance_exc)
    else:
        _FINANCE_IMPORT_ERROR = None
else:
    render_finance_tab = None
    FINANCE_TAB_AVAILABLE = False
    _FINANCE_IMPORT_ERROR = (
        "Disabled by APP_ENABLE_FINANCE_TAB=0 "
        "(legacy fallback SHOW_FINANCE_TAB is supported)."
    )

# Cache and runtime env setup.
PROJECT_ROOT = Path(__file__).parent.absolute()
MODELS_CACHE_DIR = PROJECT_ROOT / "models_cache"
MODELS_CACHE_DIR.mkdir(exist_ok=True)
os.environ["HF_HOME"] = str(MODELS_CACHE_DIR / "huggingface")
Path(os.environ["HF_HOME"]).mkdir(parents=True, exist_ok=True)
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
os.environ["TOKENIZERS_PARALLELISM"] = "false"
os.environ["TF_CPP_MIN_LOG_LEVEL"] = "2"
os.environ["TF_ENABLE_ONEDNN_OPTS"] = "0"

# Optional CUDA scheduler tuning.
try:
    from utils.cuda_init import configure_cuda_scheduling
    configure_cuda_scheduling()
except Exception:
    pass

# Ensure local imports work from workspace root.
current_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, current_dir)

# Optional components.
try:
    from utils.feedback_logger import feedback_logger
    FEEDBACK_LOGGER_AVAILABLE = True
except Exception:
    FEEDBACK_LOGGER_AVAILABLE = False
    feedback_logger = None

try:
    from utils.smart_hints import generate_smart_hint
    SMART_HINTS_AVAILABLE = True
except Exception:
    SMART_HINTS_AVAILABLE = False
    generate_smart_hint = None

try:
    import pandas as pd
    PANDAS_AVAILABLE = True
except Exception:
    pd = None
    PANDAS_AVAILABLE = False

try:
    from utils.docling_processor import DoclingProcessor  # noqa: F401
    PDF_PROCESSOR_AVAILABLE = True
except Exception:
    PDF_PROCESSOR_AVAILABLE = False

try:
    import importlib.util as _ilu
    _qd_spec = _ilu.spec_from_file_location(
        "quality_dashboard",
        os.path.join(os.path.dirname(__file__), "refactored_gui", "quality_dashboard.py"),
    )
    if _qd_spec is not None and _qd_spec.loader is not None:
        _qd_mod = _ilu.module_from_spec(_qd_spec)
        _qd_spec.loader.exec_module(_qd_mod)
        render_quality_dashboard = _qd_mod.render_quality_dashboard
        QUALITY_DASHBOARD_AVAILABLE = True
    else:
        raise ImportError("quality_dashboard spec/loader unavailable")
except Exception:
    QUALITY_DASHBOARD_AVAILABLE = False
    render_quality_dashboard = None

try:
    from agent.tools import get_global_rag_store
except Exception:
    get_global_rag_store = None

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DEFAULT_MODEL = os.environ.get("DEFAULT_MODEL", MODEL_LOADER_DEFAULT)
_LANGUAGE_NEGOTIATOR = LocaleNegotiator()


def _init_session_state() -> None:
    defaults: dict[str, Any] = {
        "initialized": False,
        "model_loader": None,
        "chat_logic": None,
        "chat_history": [],
        "feedback_data": [],
        "uploaded_documents": [],
        "pending_followup": None,
        "psych_interface": None,
        "use_react_agent": True,
        "search_depth": 5,
        "faiss_confidence": 0.70,
        "mq_n": 5,
        "selected_model_id": DEFAULT_MODEL,
        "last_generated_diagram_backend": None,
        "last_generated_diagram_type": None,
        "i18n_user_override": "auto",
        "i18n_session_language": "de",
        "i18n_auto_detect": True,
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def _is_psych_interface_compatible(iface: object) -> bool:
    return hasattr(iface, "render_complete_interface")


def _get_or_init_psych_interface() -> Optional[WellbeingSessionInterface]:
    current = st.session_state.get("psych_interface")
    if current is not None and _is_psych_interface_compatible(current):
        chat_logic = st.session_state.get("chat_logic")
        model_loader = st.session_state.get("model_loader")
        if chat_logic is not None and hasattr(current, "set_chat_logic"):
            current.set_chat_logic(chat_logic)
        if model_loader is not None and hasattr(current, "set_model_loader"):
            current.set_model_loader(model_loader)
        return current
    try:
        iface = WellbeingSessionInterface()
        chat_logic = st.session_state.get("chat_logic")
        model_loader = st.session_state.get("model_loader")
        if chat_logic is not None and hasattr(iface, "set_chat_logic"):
            iface.set_chat_logic(chat_logic)
        if model_loader is not None and hasattr(iface, "set_model_loader"):
            iface.set_model_loader(model_loader)
        st.session_state.psych_interface = iface
        return iface
    except Exception as exc:
        logger.warning("Psychological interface unavailable: %s", exc)
        st.session_state.psych_interface = None
        return None


def _default_dynamic_path(dynamic_models) -> str:
    """Default-Auswahl: Produktionsmodell (Gemma 4 12B), sonst erstes Modell."""
    for prefix in ("gemma-4-12b-it", "gemma-4-e4b", "magistral-small"):
        for m in dynamic_models:
            if m.model_id.startswith(prefix):
                return m.model_path
    return dynamic_models[0].model_path


def _dynamic_model_label(model_info) -> str:
    """Anzeige-String für die Modell-Selectbox (Name, Größe, Vision-Badge)."""
    badge = (
        i18n_t("gui.sidebar.model_vision") if model_info.is_vision
        else i18n_t("gui.sidebar.model_text")
    )
    return f"{model_info.display_name} ({model_info.size_gb:.1f} GB) · {badge}"


def _render_token_scaling_panel() -> None:
    """Token-Skalierungs-Panel (Sidebar): Auto-Vorschlag + User-Overrides.

    Zeigt den hardware-bewussten Auto-Vorschlag (``utils/token_scaling.py``)
    VOR dem Modell-Load an und erlaubt pro-Feld-Overrides (Präzedenz:
    UI > ENV > Auto). Overrides werden pro Modell außerhalb des Repos
    persistiert (``~/.cache/bot6/token_scaling_overrides.json``) und beim
    Laden an den Loader übergeben.
    """
    info = st.session_state.get("selected_model_info") or {}
    model_path = info.get("model_path")
    if not model_path:
        # Statische Fallback-Konfig (keine Registry) → Panel nicht verfügbar.
        st.sidebar.caption(i18n_t("gui.token_scaling.fallback_hint"))
        return

    from utils import token_scaling  # lokal: hält den Top-Level-Import leicht

    # Auto-Vorschlag (reine Berechnung, lädt KEIN Modell) → Panel-Baseline.
    try:
        auto = token_scaling.auto_proposal(
            model_path,
            requested_n_ctx=LLM_CONTEXT_SIZE,
            mmproj_path=info.get("mmproj_path"),
        )
    except Exception as exc:  # nie-feilend: Laden bleibt möglich (Auto im Loader)
        st.sidebar.warning(f"⚠️ {i18n_t('gui.token_scaling.proposal_failed')}: {exc}")
        st.session_state.ts_overrides = None
        return

    # Persistierte Overrides als Startwerte (pro Modell).
    stored = token_scaling.load_overrides(model_path)

    def _key(suffix: str) -> str:
        return f"ts_w_{suffix}::{model_path}"

    def _badge(field: str) -> str:
        val = getattr(stored, field)
        if val not in (None, ""):
            return f" · **{i18n_t('gui.token_scaling.src_user')}**"
        return f" · {i18n_t('gui.token_scaling.src_auto')}"

    def _kv_label(v: str) -> str:
        return i18n_t("gui.token_scaling.auto") if v == "auto" else v

    def _effort_label(v: str) -> str:
        if v == "auto":
            return i18n_t("gui.token_scaling.auto")
        if v == "off":
            return i18n_t("gui.token_scaling.off")
        return v

    st.sidebar.markdown(f"**🎛 {i18n_t('gui.token_scaling.title')}**")
    st.sidebar.caption(i18n_t("gui.token_scaling.subtitle"))

    # ── n_ctx ──
    st.sidebar.caption(i18n_t("gui.token_scaling.n_ctx") + _badge("n_ctx"))
    n_ctx_val = st.sidebar.number_input(
        i18n_t("gui.token_scaling.n_ctx"),
        min_value=512,
        max_value=1048576,
        value=int(stored.n_ctx if stored.n_ctx is not None else auto.n_ctx),
        step=1024,
        key=_key("nctx"),
        label_visibility="collapsed",
    )

    # ── KV-Quant ──
    # q8_0 ist seit 2026-09-04 Runtime-validiert (Nemotron-3-Nano-4B Q4_K_M,
    # n_ctx=4096, RTX 4090, NEBEN laufendem LM Studio: Load + Generation OK)
    # → wählbar. Historie: Streamlit hat KEIN `disabled_options` (dieses
    # KWarg crashte den App-Start mit TypeError, 2026-09-04) — die
    # Options-Liste IST das Gate; regressionsgesichert durch
    # tests/test_streamlit_token_scaling_panel.py.
    kv_options = ["auto", "f16", "q8_0"]
    kv_stored = stored.kv_quant
    st.sidebar.caption(
        i18n_t("gui.token_scaling.kv_quant") + _badge("kv_quant")
        + " · " + i18n_t("gui.token_scaling.q8_note")
    )
    # Default folgt dem Auto-Vorschlag (z.B. q8_0 auf dieser Hardware) --
    # konsistente Anzeige statt fiktivem "auto"-Default. Verhalten bleibt
    # identisch (overrides_from_values: Wert = Auto → kein Override);
    # "auto" bleibt als explizite Option und ist Fallback für Vorschlag-
    # Werte, die nicht im Drop-down stehen.
    kv_default = auto.kv_quant if auto.kv_quant in kv_options else "auto"
    kv_val = st.sidebar.selectbox(
        i18n_t("gui.token_scaling.kv_quant"),
        options=kv_options,
        index=(
            kv_options.index(kv_stored)
            if kv_stored in kv_options
            else kv_options.index(kv_default)
        ),
        format_func=_kv_label,
        key=_key("kv"),
        label_visibility="collapsed",
    )


    # ── Output-Budget ──
    st.sidebar.caption(i18n_t("gui.token_scaling.output_budget") + _badge("output_budget"))
    out_val = st.sidebar.number_input(
        i18n_t("gui.token_scaling.output_budget"),
        min_value=0,
        max_value=262144,
        value=int(stored.output_budget if stored.output_budget is not None else auto.output_budget),
        step=256,
        key=_key("out"),
        label_visibility="collapsed",
    )

    # ── Thinking-Budget ──
    st.sidebar.caption(i18n_t("gui.token_scaling.thinking_budget") + _badge("thinking_budget"))
    think_val = st.sidebar.number_input(
        i18n_t("gui.token_scaling.thinking_budget"),
        min_value=0,
        max_value=262144,
        value=int(stored.thinking_budget if stored.thinking_budget is not None else auto.thinking_budget),
        step=256,
        key=_key("think"),
        label_visibility="collapsed",
    )

    # ── Reasoning-Effort (Closed-Set des Chat-Templates + off) ──
    try:
        allowed = list(
            token_scaling.allowed_reasoning_efforts(
                token_scaling.model_architecture(model_path)
            )
        )
    except Exception:
        allowed = []
    effort_options = ["auto"] + allowed + ["off"]
    effort_stored = stored.reasoning_effort
    st.sidebar.caption(i18n_t("gui.token_scaling.reasoning_effort") + _badge("reasoning_effort"))
    effort_val = st.sidebar.selectbox(
        i18n_t("gui.token_scaling.reasoning_effort"),
        options=effort_options,
        index=(
            0
            if effort_stored is None
            else (effort_options.index(effort_stored) if effort_stored in effort_options else 0)
        ),
        format_func=_effort_label,
        key=_key("effort"),
        label_visibility="collapsed",
    )

    # ── Auto-Hinweise (VRAM-Kap, Hybrid-SSM, …) ──
    if auto.notes:
        st.sidebar.caption(i18n_t("gui.token_scaling.notes"))
        for _note in auto.notes:
            st.sidebar.caption(f"• {_note}")

    # ── Reset (alle Felder → Auto) ──
    if st.sidebar.button(
        i18n_t("gui.token_scaling.reset"),
        key=f"ts_reset::{model_path}",
        use_container_width=True,
    ):
        token_scaling.clear_overrides(model_path)
        for _suffix in ("nctx", "kv", "out", "think", "effort"):
            _k = _key(_suffix)
            if _k in st.session_state:
                del st.session_state[_k]
        st.rerun()

    # ── Effektive Overrides (Wert = Auto → kein Override) ──
    st.session_state.ts_overrides = token_scaling.overrides_from_values(
        auto,
        n_ctx=int(n_ctx_val),
        kv_quant=None if kv_val == "auto" else kv_val,
        output_budget=int(out_val),
        thinking_budget=int(think_val),
        reasoning_effort=None if effort_val == "auto" else effort_val,
    )


def initialize_ai() -> bool:
    try:
        model_loader = ModelLoader()
        selected_model = st.session_state.get("selected_model_id", DEFAULT_MODEL)
        dynamic_info = st.session_state.get("selected_model_info") or {}
        dynamic_path = dynamic_info.get("model_path")

        # UI-Token-Scaling-Overrides (Panel, pro Modell) → Loader (UI > ENV > Auto).
        ts_overrides = st.session_state.get("ts_overrides")
        ts_model_key = dynamic_path or selected_model

        if dynamic_path:
            # Dynamisches Modell aus der LM-Studio-Registry (utils/model_registry.py)
            load_fn = getattr(model_loader, "load_model_by_path", None)
            if not callable(load_fn):
                st.error("ModelLoader does not support load_model_by_path")
                return False
            if not load_fn(
                dynamic_path,
                mmproj_path=dynamic_info.get("mmproj_path"),
                token_scaling_overrides=ts_overrides,
            ):
                st.error(f"Model could not be loaded: {dynamic_path}")
                return False
        else:
            load_fn = getattr(model_loader, "load_model_by_config", None)
            if not callable(load_fn):
                st.error("ModelLoader does not support load_model_by_config")
                return False
            if not load_fn(selected_model, token_scaling_overrides=ts_overrides):
                st.error(f"Model could not be loaded: {selected_model}")
                return False

        # Effektive Overrides pro Modell persistieren (außerhalb des Repos).
        try:
            from utils import token_scaling
            if ts_overrides is not None:
                token_scaling.save_overrides(ts_model_key, ts_overrides)
            else:
                token_scaling.clear_overrides(ts_model_key)
        except Exception as _ov_exc:  # nie-feilend: der Load selbst bleibt bestehen
            logger.warning("Token-Scaling-Overrides nicht persistiert: %s", _ov_exc)

        chat_logic = AgentChatbotLogic(model_loader)
        if hasattr(chat_logic, "settings"):
            settings_obj = getattr(chat_logic, "settings", None)
            if isinstance(settings_obj, dict):
                settings_obj["use_react_agent"] = bool(st.session_state.get("use_react_agent", True))

        st.session_state.model_loader = model_loader
        st.session_state.chat_logic = chat_logic
        st.session_state.initialized = True

        psych_interface = st.session_state.get("psych_interface")
        if psych_interface is not None and _is_psych_interface_compatible(psych_interface):
            psych_interface.set_chat_logic(chat_logic)
            psych_interface.set_model_loader(model_loader)

        return True
    except Exception as exc:
        logger.exception("AI initialization failed")
        st.error(f"AI initialization failed: {exc}")
        st.session_state.initialized = False
        st.session_state.model_loader = None
        st.session_state.chat_logic = None
        return False


def unload_ai() -> None:
    model_loader = st.session_state.get("model_loader")
    if model_loader is not None and hasattr(model_loader, "unload_model"):
        try:
            model_loader.unload_model()
        except Exception:
            pass
    st.session_state.initialized = False
    st.session_state.model_loader = None
    st.session_state.chat_logic = None
    psych_interface = st.session_state.get("psych_interface")
    if psych_interface is not None and _is_psych_interface_compatible(psych_interface):
        if hasattr(psych_interface, "set_chat_logic"):
            psych_interface.set_chat_logic(None)
        if hasattr(psych_interface, "set_model_loader"):
            psych_interface.set_model_loader(None)


def get_ai_response_modern(
    message: str,
    image_path: Optional[str] = None,
    user_progress_callback=None,
) -> Tuple[str, Optional[str], Optional[str]]:
    st.session_state.last_generated_diagram_backend = None
    st.session_state.last_generated_diagram_type = None

    if not st.session_state.get("initialized") or st.session_state.get("chat_logic") is None:
        return i18n_t("gui.sidebar.ai_system_not_loaded"), None, None

    try:
        override = st.session_state.get("i18n_user_override", "auto")
        explicit = override if override != "auto" else None
        resolved = _LANGUAGE_NEGOTIATOR.resolve_language(
            explicit_language=explicit,
            session_language=st.session_state.get("i18n_session_language"),
            user_message=message,
            allow_auto_detect=bool(st.session_state.get("i18n_auto_detect", True)),
        )
        st.session_state.i18n_session_language = resolved.language
        i18n_set_language(resolved.language)

        chat_logic = st.session_state.chat_logic
        response = chat_logic.chat(
            message,
            image_path=image_path,
            progress_callback=user_progress_callback,
            search_depth=st.session_state.get("search_depth"),
            faiss_min_confidence=st.session_state.get("faiss_confidence"),
        )
        return response, None, None
    except Exception as exc:
        logger.exception("Chat response generation failed")
        return f"{i18n_t('gui.error.unknown_error')}: {exc}", None, None


def get_ai_response_events(
    message: str,
    *,
    session_id: str,
    image_path: Optional[str] = None,
) -> Iterator[ChatEvent]:
    """Resolve locale and return the request-scoped typed chat event stream."""
    st.session_state.last_generated_diagram_backend = None
    st.session_state.last_generated_diagram_type = None
    if not st.session_state.get("initialized") or st.session_state.get("chat_logic") is None:
        raise RuntimeError(i18n_t("gui.sidebar.ai_system_not_loaded"))

    override = st.session_state.get("i18n_user_override", "auto")
    explicit = override if override != "auto" else None
    resolved = _LANGUAGE_NEGOTIATOR.resolve_language(
        explicit_language=explicit,
        session_language=st.session_state.get("i18n_session_language"),
        user_message=message,
        allow_auto_detect=bool(st.session_state.get("i18n_auto_detect", True)),
    )
    st.session_state.i18n_session_language = resolved.language
    i18n_set_language(resolved.language)

    chat_logic = st.session_state.chat_logic
    return chat_logic.stream_chat_events(
        message,
        session_id=session_id,
        image_path=image_path,
        search_depth=st.session_state.get("search_depth"),
        faiss_min_confidence=st.session_state.get("faiss_confidence"),
    )


def _render_reclassify_panel() -> None:
    if get_global_rag_store is None:
        st.info("Legacy reclassification unavailable in this configuration.")
        return

    if st.button("Run legacy reclassification", key="run_legacy_reclassify"):
        loader = st.session_state.get("model_loader")
        if loader is None:
            st.warning("Load AI first.")
            return
        try:
            rag_store = get_global_rag_store(llm_client=loader)
        except Exception as exc:
            st.error(f"RAG store unavailable: {exc}")
            return
        if rag_store is None:
            st.error("RAG store unavailable")
            return

        count_fn = getattr(rag_store, "count_stale_chunks", None)
        reclassify_fn = getattr(rag_store, "reclassify_legacy_chunks", None)
        if not callable(count_fn) or not callable(reclassify_fn):
            st.info("Reclassification APIs are not available on current RAG store.")
            return

        try:
            stale_result = count_fn()
            if isinstance(stale_result, tuple) and len(stale_result) >= 2:
                st.caption(f"Stale docs: {stale_result[0]}, stale chunks: {stale_result[1]}")
            with st.spinner("Reclassifying legacy chunks..."):
                stats = reclassify_fn(batch_size=32, embed_batch_size=64, dry_run=False)
            if isinstance(stats, dict):
                st.success(json.dumps(stats, ensure_ascii=False, indent=2))
            else:
                st.success("Legacy reclassification completed.")
        except Exception as exc:
            st.error(f"Reclassification failed: {exc}")


_GLOBAL_CSS = """
<style>
.main .block-container { padding-top: 1.2rem; padding-bottom: 2rem; max-width: 1400px; }
section[data-testid="stSidebar"] { background: linear-gradient(180deg, #0f172a 0%, #1e293b 100%); }
section[data-testid="stSidebar"] * { color: #e2e8f0 !important; }
section[data-testid="stSidebar"] .stButton button {
    background: #2563eb; color: #fff; border: 0; font-weight: 600;
    border-radius: 8px; transition: background .15s ease;
}
section[data-testid="stSidebar"] .stButton button:hover { background: #1d4ed8; }
.stTabs [data-baseweb="tab-list"] { gap: 4px; border-bottom: 2px solid #e2e8f0; }
.stTabs [data-baseweb="tab"] {
    padding: 10px 18px; border-radius: 10px 10px 0 0;
    background: #f1f5f9; font-weight: 600;
}
.stTabs [aria-selected="true"] { background: #2563eb !important; color: #fff !important; }
div[data-testid="stMetricValue"] { font-size: 1.6rem; font-weight: 700; }
div[data-testid="stMetricLabel"] { font-weight: 600; opacity: .85; }
.stAlert { border-radius: 10px; }
hr { margin: 0.6rem 0; }
</style>
"""


def _build_tab_health_snapshot():
    try:
        return collect_tab_health_snapshot(
            ai_available=True,
            model_loader_cls=ModelLoader,
            chat_logic_cls=AgentChatbotLogic,
            feedback_enabled=FEEDBACK_LOGGER_AVAILABLE,
            feedback_logger=feedback_logger,
            quality_enabled=QUALITY_DASHBOARD_AVAILABLE,
            quality_renderer=render_quality_dashboard,
        )
    except TabHealthError as exc:
        logger.error("Tab health contract violated: %s", exc)
        raise


def _render_sidebar() -> None:
    i18n_set_language(st.session_state.get("i18n_session_language", "de"))

    st.sidebar.markdown(f"### 🤖 {i18n_t('gui.title')}")
    st.sidebar.caption(i18n_t("gui.subtitle"))
    st.sidebar.divider()

    st.sidebar.subheader(i18n_t("gui.sidebar.language"))
    language_options = ["auto", "de", "bg", "en"]
    current_override = st.session_state.get("i18n_user_override", "auto")
    lang_label_map = {
        "auto": i18n_t("gui.sidebar.language_auto"),
        "de": i18n_t("languages.de"),
        "bg": i18n_t("languages.bg"),
        "en": i18n_t("languages.en"),
    }
    current_idx = language_options.index(current_override) if current_override in language_options else 0
    selected_override = st.sidebar.selectbox(
        i18n_t("gui.sidebar.language_mode"),
        options=language_options,
        index=current_idx,
        format_func=lambda x: lang_label_map.get(x, x),
    )
    auto_detect = st.sidebar.checkbox(
        i18n_t("gui.sidebar.language_auto_detect"),
        value=bool(st.session_state.get("i18n_auto_detect", True)),
    )
    st.session_state.i18n_auto_detect = auto_detect
    if selected_override != current_override:
        st.session_state.i18n_user_override = selected_override
        if selected_override != "auto":
            st.session_state.i18n_session_language = selected_override
            i18n_set_language(selected_override)
        st.rerun()

    st.sidebar.subheader(i18n_t("gui.sidebar.model"))
    dynamic_models = scan_models()
    if dynamic_models:
        # Dynamische Registry: alle Modelle im LM-Studio-Community-Ordner (Live-Scan)
        paths = [m.model_path for m in dynamic_models]
        info_by_path = {m.model_path: m for m in dynamic_models}
        stale = st.session_state.get("selected_model_path")
        if stale is not None and stale not in paths:
            # Modell nicht mehr in der Registry (z. B. Ordner gelöscht) → sauberes Reset
            for key in ("selected_model_path", "selected_model_info", "model_selector"):
                if key in st.session_state:
                    del st.session_state[key]
        preferred = st.session_state.get("selected_model_path")
        default_path = preferred if preferred in paths else _default_dynamic_path(dynamic_models)

        def _label_by_path(path: str) -> str:
            # Streamlit ruft format_func mit dem ROHEN Optionswert (Pfad-String) auf,
            # _dynamic_model_label erwartet aber ModelInfo → Auflösung per Dict.
            return _dynamic_model_label(info_by_path[path])

        selected_path = st.sidebar.selectbox(
            i18n_t("gui.sidebar.model_config"),
            options=paths,
            index=paths.index(default_path),
            format_func=_label_by_path,
            key="model_selector",
        )
        selected_info = next(m for m in dynamic_models if m.model_path == selected_path)
        st.session_state.selected_model_path = selected_path
        st.session_state.selected_model_info = {
            "model_path": selected_info.model_path,
            "mmproj_path": selected_info.mmproj_path,
            "is_vision": selected_info.is_vision,
            "size_gb": selected_info.size_gb,
        }
        st.session_state.selected_model_id = selected_info.model_id
        st.sidebar.caption(
            f"📂 {i18n_t('gui.sidebar.models_root')} ({len(dynamic_models)}): `{models_root()}`"
        )
    else:
        # Fallback: statische Konfigurationen (z. B. Ordner fehlt)
        st.sidebar.warning(f"⚠️ {i18n_t('gui.sidebar.models_missing')}: `{models_root()}`")
        model_options = get_available_models()
        selected_id = st.session_state.get("selected_model_id", DEFAULT_MODEL)
        keys = list(model_options.keys())
        try:
            idx = keys.index(selected_id)
        except ValueError:
            idx = 0
        selected = st.sidebar.selectbox(
            i18n_t("gui.sidebar.model_config"),
            options=keys,
            index=idx,
            format_func=lambda x: model_options.get(x, x),
        )
        st.session_state.selected_model_id = selected
        st.session_state.selected_model_info = None

    # Token-Scaling: Auto-Vorschlag + User-Overrides (vor dem Load, pro Modell).
    _render_token_scaling_panel()

    c1, c2 = st.sidebar.columns(2)
    with c1:
        if st.button(i18n_t("gui.sidebar.load_button"), key="load_ai_sidebar", use_container_width=True):
            with st.spinner(i18n_t("gui.sidebar.loading_ai")):
                initialize_ai()
            st.rerun()
    with c2:
        if st.button(i18n_t("gui.sidebar.unload_button"), key="unload_ai_sidebar", use_container_width=True):
            unload_ai()
            st.rerun()

    st.sidebar.divider()
    st.sidebar.subheader(i18n_t("gui.sidebar.status"))
    if st.session_state.get("initialized"):
        st.sidebar.success(i18n_t("gui.sidebar.ai_system_loaded"))
    else:
        st.sidebar.warning(i18n_t("gui.sidebar.ai_system_not_loaded"))

    st.sidebar.metric(i18n_t("gui.sidebar.messages"), len(st.session_state.get("chat_history", [])))
    st.sidebar.metric(i18n_t("gui.sidebar.feedbacks"), len(st.session_state.get("feedback_data", [])))

    st.sidebar.divider()
    st.sidebar.subheader(i18n_t("gui.sidebar.quick_access"))
    if st.sidebar.button(i18n_t("gui.sidebar.reset_chat"), use_container_width=True):
        st.session_state.chat_history = []
        st.rerun()

    st.sidebar.caption(f"Finanztab-Policy: {'✅' if FINANCE_TAB_ENABLED_BY_POLICY else '🚫'}")
    st.sidebar.caption(f"Finanztab-Laufzeit: {'✅' if FINANCE_TAB_AVAILABLE else '❌'}")
    if not FINANCE_TAB_AVAILABLE and _FINANCE_IMPORT_ERROR:
        st.sidebar.caption(f"Finanztab-Info: {_FINANCE_IMPORT_ERROR}")
    st.sidebar.caption(f"Feedback-Logger: {'✅' if FEEDBACK_LOGGER_AVAILABLE else '❌'}")
    st.sidebar.caption(f"PDF-Processor: {'✅' if PDF_PROCESSOR_AVAILABLE else '❌'}")
    st.sidebar.caption(f"Quality-Dashboard: {'✅' if QUALITY_DASHBOARD_AVAILABLE else '❌'}")


def main() -> None:
    i18n_set_language(st.session_state.get("i18n_session_language", "de") if "i18n_session_language" in st.session_state else "de")
    st.set_page_config(
        page_title=i18n_t("gui.page_title"),
        layout="wide",
        page_icon="🤖",
        initial_sidebar_state="expanded",
    )
    st.markdown(_GLOBAL_CSS, unsafe_allow_html=True)

    _init_session_state()
    _render_sidebar()

    try:
        tab_health_snapshot = _build_tab_health_snapshot()
    except TabHealthError as exc:
        st.error(f"{i18n_t('gui.error.tab_health_violated')}: {exc}")
        st.stop()

    # Build tab list dynamically so Finance and Quality slots only appear
    # when feature policy and backend availability permit rendering.
    tab_names: list[str] = [
        i18n_t("gui.tabs.chat"),
        i18n_t("gui.tabs.wellbeing"),
        i18n_t("gui.tabs.performance"),
        i18n_t("gui.tabs.feedback"),
        i18n_t("gui.tabs.rag"),
    ]
    if QUALITY_DASHBOARD_AVAILABLE:
        tab_names.append(i18n_t("gui.tabs.rag_quality"))
    if FINANCE_TAB_AVAILABLE:
        tab_names.append(i18n_t("gui.tabs.finance"))
    tab_names.append(i18n_t("gui.tabs.settings"))

    tab_objects = st.tabs(tab_names)
    tab_map = dict(zip(tab_names, tab_objects))

    # ---- Chat ----
    with tab_map[i18n_t("gui.tabs.chat")]:
        pandas_module = sys.modules.get("pandas") if PANDAS_AVAILABLE else None
        render_chat_tab(
            logger=logger,
            get_ai_response_events=get_ai_response_events,
            extract_followup_questions=extract_followup_questions,
            feedback_logger_available=FEEDBACK_LOGGER_AVAILABLE,
            feedback_logger=feedback_logger,
            smart_hints_available=SMART_HINTS_AVAILABLE,
            generate_smart_hint=generate_smart_hint,
            pandas_available=PANDAS_AVAILABLE,
            pd=pandas_module,
            pdf_processor_available=PDF_PROCESSOR_AVAILABLE,
        )

    # ---- Psychology ----
    with tab_map[i18n_t("gui.tabs.wellbeing")]:
        try:
            render_wellbeing_tab(_get_or_init_psych_interface)
        except Exception as exc:
            logger.exception("Psychology tab failed")
            st.error(f"{i18n_t('gui.psychology.not_available')}: {exc}")

    # ---- Performance ----
    with tab_map[i18n_t("gui.tabs.performance")]:
        try:
            render_performance_tab(logger)
        except Exception as exc:
            logger.exception("Performance tab failed")
            st.error(f"{i18n_t('gui.performance.failed')}: {exc}")

    # ---- Feedback ----
    with tab_map[i18n_t("gui.tabs.feedback")]:
        try:
            render_feedback_tab(
                logger,
                feedback_logger_available=FEEDBACK_LOGGER_AVAILABLE,
                feedback_logger=feedback_logger,
            )
        except Exception as exc:
            logger.exception("Feedback tab failed")
            st.error(f"{i18n_t('gui.feedback.failed')}: {exc}")

    # ---- RAG Documents ----
    with tab_map[i18n_t("gui.tabs.rag")]:
        try:
            render_rag_documents_tab(_render_reclassify_panel)
        except Exception as exc:
            logger.exception("RAG documents tab failed")
            st.error(f"{i18n_t('gui.rag.failed')}: {exc}")

    # ---- RAG Quality (optional) ----
    if QUALITY_DASHBOARD_AVAILABLE and i18n_t("gui.tabs.rag_quality") in tab_map:
        with tab_map[i18n_t("gui.tabs.rag_quality")]:
            if callable(render_quality_dashboard):
                try:
                    render_quality_dashboard()
                except Exception as exc:
                    logger.exception("Quality dashboard failed")
                    st.error(f"{i18n_t('gui.quality.failed')}: {exc}")
            else:
                st.info(i18n_t("gui.quality.not_available"))

    # ---- Finance (optional) ----
    if FINANCE_TAB_AVAILABLE and i18n_t("gui.tabs.finance") in tab_map:
        with tab_map[i18n_t("gui.tabs.finance")]:
            try:
                assert render_finance_tab is not None
                render_finance_tab()
            except Exception as exc:
                logger.exception("Finance tab failed")
                st.error(f"{i18n_t('gui.finance.failed')}: {exc}")
    elif not FINANCE_TAB_AVAILABLE:
        # Surface the import reason once so the user sees why the tab is missing.
        logger.info("Finance tab disabled: %s", _FINANCE_IMPORT_ERROR)

    # ---- Settings ----
    with tab_map[i18n_t("gui.tabs.settings")]:
        try:
            render_settings_tab(logger, tab_health_snapshot)
        except Exception as exc:
            logger.exception("Settings tab failed")
            st.error(f"{i18n_t('gui.settings.failed')}: {exc}")


if __name__ == "__main__":
    main()
