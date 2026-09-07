from __future__ import annotations
from typing import List, Dict, Any, Optional, Tuple, Set, TYPE_CHECKING
import re
import time
from datetime import datetime
import os
import logging
import json
from functools import lru_cache
import threading
from collections import defaultdict
import hashlib
from dataclasses import dataclass
from enum import Enum

from agent.context import ContextManager
from agent.prompts import PLANNER_SYSTEM, PLANNER_USER_TEMPLATE
from agent.tools import ToolManager
from agent.agent_types import ToolCall, ToolResult, EvidencePack, FinalAnswer, Source, AgentTrace
from agent.universal_evidence_selector import UniversalEvidenceSelector
from agent.hybrid_reasoning import HybridReasoning, Evidence
from agent.evidence_processor import EvidenceProcessor
from agent.source_manager import SourceManager
from agent.grammars import get_rag_sufficiency_grammar
import utils.web_compliance as web_compliance

# SOTA Phase 2.2: Web-search query planning + reflection (privacy-first)
# Graceful fallback when module unavailable.
try:
    from agent.web_search_planner import WebSearchPlanner, WebSearchReflector
    WEB_SEARCH_PLANNER_AVAILABLE = True
except ImportError:
    WebSearchPlanner = None  # type: ignore
    WebSearchReflector = None  # type: ignore
    WEB_SEARCH_PLANNER_AVAILABLE = False

# SOTA Filesystem-Connector (2026): Declarative Tool Profiles
try:
    from agent.tool_profiles import (
        get_profile,
        is_tool_allowed,
        has_fs_read,
        has_fs_write,
        filter_tool_schemas,
    )
    TOOL_PROFILES_AVAILABLE = True
except ImportError:
    TOOL_PROFILES_AVAILABLE = False

# Manager Modules (Production-Ready Architecture 2025)
from agent.security_manager import SecurityManager
from agent.query_strategy_manager import QueryStrategyManager
from agent.evidence_manager import EvidenceManager
from agent.rag_manager import RAGManager
from agent.response_builder import ResponseBuilder
from agent.verification_manager import VerificationManager

# SOTA Variant 4: Central decomposition engine — single source of truth for
# sub-query generation across main agent and psychological session paths.
from agent.decomposition_engine import DecompositionEngine

# NEW: Refactored Processing Modules (2025-10-10)
from agent.source_processor import SourceProcessor

# SOTA RAG Quality Pipeline Components (2026)
from agent.change_detector import ChangeDetector
from agent.docling_parallel import DoclingParallel
from agent.multimodal_rag import MultimodalRAG
from agent.strixkat_eval import StrixKATEval

logger = logging.getLogger(__name__)

# SOTA Adaptive-RAG: LLM-Router (shallow/deep) + MultiHopRetriever
# Optional — graceful fallback when module unavailable.
ADAPTIVE_RAG_AVAILABLE = False
if TYPE_CHECKING:
    from agent.adaptive_rag import AdaptiveRAGRouter, AdaptiveRAGPipeline, MultiHopRetriever
else:
    AdaptiveRAGRouter = None  # type: ignore[assignment]
    AdaptiveRAGPipeline = None  # type: ignore[assignment]
    MultiHopRetriever = None  # type: ignore[assignment]
    try:
        from agent.adaptive_rag import AdaptiveRAGRouter, AdaptiveRAGPipeline, MultiHopRetriever
        ADAPTIVE_RAG_AVAILABLE = True
        logger.info("✅ Adaptive-RAG verfügbar: Router (shallow/deep) + MultiHopRetriever + Pipeline")
    except ImportError as e:
        logger.debug("ℹ️ Adaptive-RAG nicht verfügbar: %s", e)
from utils.followup_question_extractor import (
    FOLLOWUP_PERSPECTIVE_INSTRUCTION,
    extract_followup_questions,
)
from utils.runtime_policy import parse_bool_env
from utils.token_manager import estimate_response_tokens, estimate_prompt_tokens
from utils import token_scaling  # Adaptive max_tokens (Token-Skalierung, docs/20)
# Legacy web_prefilter replaced by QueryStrategyManager & EvidenceDeduplicator

try:
    from i18n import get_current_language as i18n_get_current_language
except Exception:
    i18n_get_current_language = None


def _planner_language_instruction() -> str:
    lang = "de"
    if callable(i18n_get_current_language):
        try:
            lang = i18n_get_current_language() or "de"
        except Exception:
            lang = "de"

    if lang == "bg":
        return "\n\nSPRACHREGEL: Antworte bei Planungsausgabe auf Bulgarisch."
    if lang == "en":
        return "\n\nLANGUAGE RULE: Produce planning output in English."
    return "\n\nSPRACHREGEL: Antworte bei Planungsausgabe auf Deutsch."


class RetrievalRoute(str, Enum):
    """Declarative retrieval routing modes."""
    INTERNAL_ONLY = "INTERNAL_ONLY"
    RAG_REQUIRED = "RAG_REQUIRED"
    WEB_REQUIRED = "WEB_REQUIRED"


@dataclass
class RetrievalRoutingDecision:
    """Routing outcome used to enforce retrieval-tool constraints."""
    route: RetrievalRoute
    reason: str
    confidence: float = 0.0
    focused_query: str = ""
    # --- Adaptive RAG: depth decision from router (shallow | deep) ---
    depth: Optional[str] = None


# Enhanced LLM Components (robuste, generische Implementierungen)
GenericIntentDetector = None
IntentType = None
wants_visualization = None
INTENT_DETECTION_AVAILABLE = False
try:
    # Nutze enhanced Version mit robusten LLM-Calls
    from agent.intent_detector_enhanced import EnhancedIntentDetector as GenericIntentDetector  # type: ignore[assignment]
    from agent.intent_detector_enhanced import IntentType  # type: ignore[assignment]
    from agent.intent_detector import wants_visualization  # type: ignore[assignment]
    INTENT_DETECTION_AVAILABLE = True
    logger.info("✅ Enhanced Intent Detection geladen")
except ImportError as e:
    logger.warning(f"⚠️ Intent Detection nicht verfügbar - fallback zu keyword-basiert: {e}")

# Enhanced Privacy Handler (deutsche Spracherkennung, keine Fachbegriffe als Namen)
ChainOfThoughtPrivacyHandler = None
COT_PRIVACY_AVAILABLE = False
try:
    from agent.privacy_handler_enhanced import EnhancedPrivacyHandler as ChainOfThoughtPrivacyHandler  # type: ignore[assignment]
    COT_PRIVACY_AVAILABLE = True
    logger.info("✅ Enhanced Privacy Handler geladen")
except ImportError as e:
    logger.warning(f"⚠️ Privacy Handler nicht verfügbar: {e}")

# Adaptive Planning Components (Feature-Flag based)
ADAPTIVE_PLANNING_AVAILABLE = False
if TYPE_CHECKING:
    from agent.adaptive_planner import AdaptivePlanner
else:
    AdaptivePlanner = None
    try:
        from agent.adaptive_planner import AdaptivePlanner
        ADAPTIVE_PLANNING_AVAILABLE = True
        logger.info("✅ Adaptive Planning verfügbar")
    except ImportError as e:
        logger.warning(f"⚠️ Adaptive Planning nicht verfügbar: {e}")

class AgentOrchestrator:
    """Coordinates planning, tool execution, evidence selection (web + RAG), summarization, and verification.

    Defaults are tuned for a local Mistral Small 3.2 (128k ctx). RAG and evidence selection are configurable via
    constructor params and environment variables to ease experimentation without code changes.
    """
    def __init__(self, model_loader, n_ctx: int = 128000, reserve: int = 4096,
                 # RAG settings
                 rag_enabled: bool = True,
                 rag_k: int = 6,
                 rag_min_score: float = 0.0,
                 rag_persist_from_web: bool = True,
                 rag_chunk_size: int = 1500,
                 rag_chunk_overlap: int = 200,
                 use_env_config: bool = True,
                 ):
        self.model_loader = model_loader
        self.local_only_mode: bool = parse_bool_env("APP_LOCAL_ONLY", "0")
        self.ctx = ContextManager(n_ctx=n_ctx, reserve=reserve)
        self.ctx.set_model_loader(model_loader)  # SOTA: Echter Tokenizer statt chars/4
        
        # WICHTIG: Verwende Singleton ToolManager (verhindert VRAM-Leak!)
        from agent.tools import get_tool_manager
        self.tools = get_tool_manager(llm_client=self.model_loader)
        
        # NEW: User System-Prompt Integration
        self.user_system_prompt: str = ""  # User-definierter System-Prompt aus GUI

        # RAG config
        self.rag_enabled = bool(rag_enabled)
        self.rag_k = int(rag_k)
        self.rag_min_score = float(rag_min_score)
        self.rag_persist_from_web = bool(rag_persist_from_web)
        self.web_search_k = rag_k
        self.rag_chunk_size = int(rag_chunk_size)
        self.rag_chunk_overlap = int(rag_chunk_overlap)
        
        # NEW 2025-10-11: FAISS Confidence Threshold (None = use adaptive)
        self.faiss_min_confidence: Optional[float] = None
        
        # Evidence selection / summarizer-verifier config
        self.evidence_max_candidates: int = 20  # initial merge cap before ranking
        self.evidence_shortlist_m: int = 12     # M for rank→shortlist
        self.evidence_diversity_lambda: float = 0.7  # 0..1 relevance-vs-diversity
        self.news_min_k: int = 5
        self.news_max_k: int = 6
        self.planner_max_tokens: int = 2048
        self.summarizer_max_tokens: int = 4096
        self.verifier_max_tokens: int = 1024
        # Presentation flags
        self.citation_inline_details: bool = False  # enrich [n] inline
        self.append_sources_block: bool = True      # add Quellen section
        # --- New: Multi-Query RAG (runtime, no DB changes) ---
        self.multiquery_enabled: bool = True  # Default aktiviert für bessere Recall-Performance
        self.mq_n: int = 5          # number of sub-queries
        self.mq_k: int = 5          # per-subquery k
        
        # NEW: URL DEDUPLICATION & THREADING LOCK (Performance Fix 2025)
        self.processed_urls: Set[str] = set()
        self.url_processing_lock = threading.Lock()
        self.url_processing_stats = {
            'total_requests': 0,
            'deduplication_saves': 0,
            'concurrent_blocks': 0,
            'processing_times': []
        }

        # NEW 2026-06-02: Async RAG-Persist Executor (Root-Cause Fix für
        # langsame Web/RAG-Antworten). `_persist_web_to_rag` blockierte den
        # Antwortpfad mit Trafilatura-Extraktion, Embedding-Compute,
        # LLM-KG-Extraktion und Entity-Resolution für jede einzelne Web-URL
        # (sequentiell, mit Lock). Da die frisch persistierten Chunks im
        # selben Turn ohnehin nicht durch FAISS abrufbar sind (Threshold-
        # basiertes Rebuild), ist die Persistierung ein future-investment
        # und gehört nicht auf den kritischen Pfad.
        from concurrent.futures import ThreadPoolExecutor
        import atexit
        self._persist_executor: Optional[ThreadPoolExecutor] = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="rag-persist"
        )
        # Graceful shutdown: warte begrenzt auf laufende Persist-Tasks beim
        # Prozess-Ende, damit kein Persistierungs-Fortschritt verloren geht.
        atexit.register(self._shutdown_persist_executor)
        logger.info(
            "Async RAG-Persist Executor aktiviert (1 worker, off-critical-path)"
        )

        # NEW 2025-01-15: Sub-Query Gap-Detection & Web-Fallback
        self.subquery_gap_detection_enabled: bool = True  # Aktiviere Gap-Detection für Sub-Queries
        self.subquery_min_results_threshold: int = 2      # Minimum RAG-Results vor Web-Fallback
        self.subquery_web_fallback_enabled: bool = not self.local_only_mode
        logger.info(
            "Sub-Query Gap-Detection aktiviert (min_results: 2, web_fallback: %s)",
            self.subquery_web_fallback_enabled,
        )
        if self.local_only_mode:
            logger.info("🔒 APP_LOCAL_ONLY aktiv: Orchestrator-Webfallback deaktiviert")
        
        # NEW 2026-03: Dynamic Context Window
        # rag_k from GUI serves as MAXIMUM (cap), actual k is determined
        # per-query by QueryStrategyManager based on complexity analysis.
        self.dynamic_k_enabled: bool = True
        logger.info("Dynamic Context Window aktiviert (GUI-K als Maximum, regelbasiertes Minimum)")
        
        # NEW: IRCoT (Interleaving Retrieval with Chain-of-Thought)
        self.ircot_enabled: bool = True   # Enable iterative retrieval refinement
        self.ircot_max_iterations: int = 3  # Max retrieval cycles
        self.ircot_min_confidence: float = 0.6  # Minimum evidence sufficiency
        
        # NEW: Intent Detector for semantic tool selection (NO MORE KEYWORDS!)
        self.intent_detector = None
        self.use_intent_detection = False
        if INTENT_DETECTION_AVAILABLE and GenericIntentDetector is not None:  # type: ignore[unreachable]
            try:  # type: ignore[unreachable]
                self.intent_detector = GenericIntentDetector(llm_client=model_loader)  # type: ignore[unreachable]
                self.use_intent_detection = True
                self.intent_confidence_threshold = 0.7
                logger.info("Intent Detection aktiviert - semantische Tool-Auswahl statt Keywords")
            except Exception as e:
                logger.error(f"Fehler beim Initialisieren des Intent Detectors: {e}")
                self.intent_detector = None
                self.use_intent_detection = False
        else:
            logger.warning("Intent Detection nicht verfuegbar - Keywords werden verwendet")

        # Semantic routing cache for web/live-data requirements (per-query).
        self.semantic_live_routing_enabled: bool = True
        self._live_data_assessment_cache: Dict[str, Dict[str, Any]] = {}
        self.retrieval_router_enabled: bool = True
        self._last_retrieval_route: RetrievalRoute = RetrievalRoute.RAG_REQUIRED

        # CRAG-style self-correction loop (verification -> retry retrieval)
        self.crag_self_correction_enabled: bool = True
        self.crag_max_retries: int = 2
        self.crag_grounding_threshold: float = 0.35
        
        # NEW: Universal Evidence Selector
        self.evidence_selector = UniversalEvidenceSelector(model_loader)
        self.use_llm_evidence_selection: bool = True  # Flag um LLM-Selection zu aktivieren/deaktivieren

        # NEW 2025: Agentic RAG Components (State-of-the-Art)
        self.agentic_rag_enabled: bool = False  # TODO: Implement for 2025 standards
        self.reranking_enabled: bool = False    # TODO: Cross-encoder reranking
        self.multimodal_enabled: bool = False   # TODO: Vision-language integration
        self.adaptive_strategy: bool = True    # SOTA: Query-complexity-based routing (AdaptiveRAGRouter)

        # Optional environment-driven overrides
        if use_env_config:
            try:
                val = os.getenv("RAG_ENABLED")
                if val is not None:
                    self.rag_enabled = val.strip().lower() in {"1", "true", "yes", "on"}
                val = os.getenv("RAG_K")
                if val is not None:
                    self.rag_k = max(1, int(val))
                val = os.getenv("RAG_MIN_SCORE")
                if val is not None:
                    self.rag_min_score = float(val)
                val = os.getenv("RAG_PERSIST_FROM_WEB")
                if val is not None:
                    self.rag_persist_from_web = val.strip().lower() in {"1", "true", "yes", "on"}
                val = os.getenv("RAG_CHUNK_SIZE")
                if val is not None:
                    self.rag_chunk_size = max(200, int(val))
                val = os.getenv("RAG_CHUNK_OVERLAP")
                if val is not None:
                    self.rag_chunk_overlap = max(0, int(val))
                # Evidence + summarizer/verifier
                val = os.getenv("EVIDENCE_MAX_CANDIDATES")
                if val is not None:
                    self.evidence_max_candidates = max(1, int(val))
                val = os.getenv("EVIDENCE_SHORTLIST_M")
                if val is not None:
                    self.evidence_shortlist_m = max(1, int(val))
                val = os.getenv("EVIDENCE_DIVERSITY_LAMBDA")
                if val is not None:
                    self.evidence_diversity_lambda = min(1.0, max(0.0, float(val)))
                val = os.getenv("NEWS_MIN_K")
                if val is not None:
                    self.news_min_k = max(1, int(val))
                val = os.getenv("NEWS_MAX_K")
                if val is not None:
                    self.news_max_k = max(self.news_min_k, int(val))
                val = os.getenv("SUMMARIZER_MAX_TOKENS")
                if val is not None:
                    self.summarizer_max_tokens = max(128, int(val))
                val = os.getenv("VERIFIER_MAX_TOKENS")
                if val is not None:
                    self.verifier_max_tokens = max(128, int(val))
                # Presentation flags
                val = os.getenv("CITATION_INLINE_DETAILS")
                if val is not None:
                    self.citation_inline_details = val.strip().lower() in {"1", "true", "yes", "on"}
                val = os.getenv("APPEND_SOURCES_BLOCK")
                if val is not None:
                    self.append_sources_block = val.strip().lower() in {"1", "true", "yes", "on"}
                # Multi-query
                val = os.getenv("RAG_MULTIQUERY")
                if val is not None:
                    self.multiquery_enabled = val.strip().lower() in {"1", "true", "yes", "on"}
                val = os.getenv("RAG_MQ_N")
                if val is not None:
                    self.mq_n = max(1, int(val))
                val = os.getenv("RAG_MQ_K")
                if val is not None:
                    self.mq_k = max(1, int(val))
                # IRCoT settings
                val = os.getenv("IRCOT_ENABLED")
                if val is not None:
                    self.ircot_enabled = val.strip().lower() in {"1", "true", "yes", "on"}
                val = os.getenv("IRCOT_MAX_ITERATIONS")
                if val is not None:
                    self.ircot_max_iterations = max(1, min(5, int(val)))
                val = os.getenv("IRCOT_MIN_CONFIDENCE")
                if val is not None:
                    self.ircot_min_confidence = min(1.0, max(0.0, float(val)))
            except (ValueError, TypeError) as e:
                logger.warning(f"Ungültige Umgebungsvariablen für Multi-Query RAG: {e}")
            except Exception as e:
                logger.error(f"Unerwarteter Fehler beim Lesen der Multi-Query RAG Konfiguration: {e}")

        logger.info(f"AgentOrchestrator initialized: RAG={self.rag_enabled}, MultiQuery={self.multiquery_enabled}")
        
        # NEW: Hybrid Reasoning Components (Refactored 2025-10-08)
        # Updated 2026-03-20: Cross-Encoder + Pipeline-Callbacks für SOTA Grounding
        self.evidence_processor = EvidenceProcessor(llm_callable=self._llm_wrapper)  # ✅ Mit LLM für Contradiction Detection
        
        # Cross-Encoder Singleton abrufen (wird von evidence_processor lazy-initialized)
        _cross_encoder = getattr(self.evidence_processor, 'cross_encoder', None)
        
        self.hybrid_reasoning = HybridReasoning(
            llm_callable=self._llm_wrapper,
            cross_encoder=_cross_encoder,
            summarize_fn=self.summarize,      # Für echte Re-Synthese bei Validierungsfehler
            verify_fn=self.verify_step,       # Für echte Re-Verifikation bei Validierungsfehler
        )
        self.source_manager = SourceManager(
            tools_manager=self.tools,
            rag_persist_enabled=self.rag_persist_from_web
        )
        logger.info("✅ Hybrid-Reasoning-Komponenten initialisiert")
        
        # NEW 2025: Manager Modules (Production-Ready Modular Architecture)
        from agent.security_manager import SecurityManager
        from agent.query_strategy_manager import QueryStrategyManager
        from agent.evidence_manager import EvidenceManager
        from agent.rag_manager import RAGManager
        from agent.response_builder import ResponseBuilder
        from agent.verification_manager import VerificationManager, VerificationLevel
        
        # Initialize all manager modules (singletons)
        self.security_manager = SecurityManager(llm_callable=self._llm_wrapper)
        self.query_strategy_manager = QueryStrategyManager(llm_callable=self._llm_wrapper)
        # SOTA Variant 4 — central decomposition engine. Same LLM wrapper as
        # the strategy manager, so behaviour stays consistent. The engine
        # owns the sub-query generation contract; QueryStrategyManager keeps
        # complexity analysis, refinement, and strategy selection.
        self.decomposition_engine = DecompositionEngine(llm_callable=self._llm_wrapper)
        self.evidence_manager = EvidenceManager(
            evidence_processor=self.evidence_processor,
            source_manager=self.source_manager,
            tools_manager=self.tools
        )
        # Set orchestrator delegate for gradual migration
        self.evidence_manager.set_orchestrator_delegate(self)
        
        self.rag_manager = RAGManager(
            tools_manager=self.tools,
            enable_gpu=True  # RTX 4090 GPU acceleration
        )
        self.response_builder = ResponseBuilder()
        self.verification_manager = VerificationManager(llm_callable=self._llm_wrapper)
        
        # Configure verification level (can be changed at runtime)
        self.verification_level = VerificationLevel.STANDARD
        self.enable_answer_verification = True  # Enable answer verification by default
        self.strict_mode_enabled = False  # Strict validation (PII, source restrictions)
        
        logger.info("✅ Manager-Module initialisiert (Security, Query, Evidence, RAG, Response, Verification)")
        
        # NEW: Processing Modules (Refactored 2025-10-10)
        self.source_processor = SourceProcessor()
        # Legacy web_prefilter removed - now handled by QueryStrategyManager & EvidenceDeduplicator
        logger.info("✅ Processing-Module initialisiert (SourceProcessor)")
        
        # NEW: Adaptive Planning (Feature-Flag based, Experimental 2025)
        self.adaptive_planning_enabled: bool = False  # Default: OFF (Feature-Flag)
        self.adaptive_planner: Optional['AdaptivePlanner'] = None  # Wird bei set_adaptive_planning_config initialisiert

        if ADAPTIVE_PLANNING_AVAILABLE:
            logger.info("Adaptive Planning verfügbar - kann über set_adaptive_planning_config() aktiviert werden")
        else:
            logger.info("Adaptive Planning nicht verfügbar (Module nicht geladen)")

        
        # CoT Privacy Protection - LAZY INIT (wird erst geladen wenn model_loader verfügbar)
        # NOTE: This will be REPLACED by SecurityManager in Phase 4B
        self._cot_privacy_handler = None
        self._privacy_handler_llm_client = None
        if COT_PRIVACY_AVAILABLE:
            logger.info("CoT Privacy Protection verfügbar - wird bei set_model_loader initialisiert")
        else:
            self._cot_privacy_handler = None

        # -----------------------------------------------------------------------
        # SOTA Pipeline Components (ANALYSE_OFFENE_PUNKTE 1a-1f)
        # ChangeDetector | DoclingParallel | MultimodalRAG | StrixKATEval | SOTAPipeline
        # -----------------------------------------------------------------------
        self._change_detector = None
        self._docling_processor = None
        self._multimodal_rag = None
        self._strixkat_eval = None
        self._sota_pipeline = None

        # 1a: ChangeDetector
        try:
            self._change_detector = ChangeDetector()
            logger.info("SOTA-Pipeline: ChangeDetector initialized")
        except Exception as e:
            logger.warning("SOTA-Pipeline: ChangeDetector nicht verfügbar: %s", e)

        # 1b: DoclingParallel
        try:
            self._docling_processor = DoclingParallel()
            logger.info("SOTA-Pipeline: DoclingParallel initialized")
        except Exception as e:
            logger.warning("SOTA-Pipeline: DoclingParallel nicht verfügbar: %s", e)

        # 1c: MultimodalRAG
        try:
            self._multimodal_rag = MultimodalRAG()
            logger.info("SOTA-Pipeline: MultimodalRAG initialized")
        except Exception as e:
            logger.warning("SOTA-Pipeline: MultimodalRAG nicht verfügbar: %s", e)

        # 1d: StrixKATEval
        try:
            self._strixkat_eval = StrixKATEval()
            logger.info("SOTA-Pipeline: StrixKATEval initialized")
        except Exception as e:
            logger.warning("SOTA-Pipeline: StrixKATEval nicht verfügbar: %s", e)

        # 1e: SOTAPipeline (koordiniert alle Komponenten)
        try:
            from agent.sota_pipeline import get_pipeline
            rag_store_ref = getattr(self.tools, 'rag', None)
            self._sota_pipeline = get_pipeline(unified_rag_store=rag_store_ref)
            logger.info("SOTA-Pipeline: SOTAPipeline singleton initialized")
        except Exception as e:
            logger.warning("SOTA-Pipeline: SOTAPipeline nicht verfügbar: %s", e)

        logger.info("SOTA-Pipeline: Komponenten-Initialisierung abgeschlossen")

        # 1f: Adaptive-RAG (Router + MultiHopRetriever + Pipeline)
        try:
            # retrieve_fn: (query, k) -> (texts, scores) — wraps self.tools.rag_search
            def _adaptive_retrieve(query: str, k: int) -> Tuple[List[str], List[float]]:
                """Bridge: MultiHopRetriever -> existing rag_search tool."""
                try:
                    results = self.tools.rag_search(query, k=k, min_score=0.0)
                    texts: List[str] = []
                    scores: List[float] = []
                    for r in (results or []):
                        txt = getattr(r, "content", None) or getattr(r, "text", None) or ""
                        sc = float(getattr(r, "score", 0.5) or 0.5)
                        if txt:
                            texts.append(txt)
                            scores.append(sc)
                    if not texts:
                        # Fallback: try dict-style results
                        for r in (results or []):
                            if isinstance(r, dict):
                                txt = r.get("content", r.get("text", ""))
                                sc = float(r.get("score", 0.5))
                                if txt:
                                    texts.append(str(txt))
                                    scores.append(sc)
                    return texts, scores
                except Exception as e:
                    logger.warning("[Adaptive-RAG] rag_search fehlgeschlagen: %s", e)
                    return [], []

            self._adaptive_router = AdaptiveRAGRouter(
                llm_callable=lambda p, m: self._llm_wrapper(p, max_tokens=m),
                max_tokens=80,
            )
            self._adaptive_multi_hop = MultiHopRetriever(
                llm_callable=lambda p, m: self._llm_wrapper(p, max_tokens=m),
                retrieve_fn=_adaptive_retrieve,
                max_hops=3,
                subqueries_per_hop=3,
                min_evidence_count=6,
            )
            self._adaptive_pipeline = AdaptiveRAGPipeline(
                router=self._adaptive_router,
                multi_hop=self._adaptive_multi_hop,
                default_k=self.rag_k,
            )
            logger.info("SOTA-Pipeline: Adaptive-RAG Router + MultiHopRetriever + Pipeline initialized")
        except Exception as e:
            logger.warning("SOTA-Pipeline: Adaptive-RAG nicht verfügbar: %s", e)
            self._adaptive_router = None
            self._adaptive_multi_hop = None
            self._adaptive_pipeline = None

        # 1g: WebSearchPlanner + WebSearchReflector (SOTA Phase 2.2)
        # Query-planning + reflection loop for live web searches.
        self._web_search_planner = None
        self._web_search_reflector = None
        if WEB_SEARCH_PLANNER_AVAILABLE:
            try:
                self._web_search_planner = WebSearchPlanner(
                    llm_callable=lambda p, m: self._llm_wrapper(p, max_tokens=m),
                    max_tokens=512,
                )
                self._web_search_reflector = WebSearchReflector(
                    llm_callable=lambda p, m: self._llm_wrapper(p, max_tokens=m),
                    max_tokens=512,
                )
                logger.info("SOTA-Pipeline: WebSearchPlanner + WebSearchReflector initialized")
            except Exception as e:
                logger.warning("SOTA-Pipeline: WebSearchPlanner/Reflector nicht verfuegbar: %s", e)
                self._web_search_planner = None
                self._web_search_reflector = None

    @property
    def change_detector(self):
        return self._change_detector

    @property
    def docling_processor(self):
        return self._docling_processor

    @property
    def multimodal_rag(self):
        return self._multimodal_rag

    @property
    def strixkat_eval(self):
        return self._strixkat_eval

    @property
    def strixkat_evaluator(self):
        return self._strixkat_eval

    @property
    def sota_pipeline(self):
        return self._sota_pipeline
    
    def set_adaptive_planning_config(
        self,
        enabled: bool,
        max_reflections: int = 2,
        confidence_done_threshold: float = 0.85,
        confidence_tools_threshold: float = 0.4
    ) -> None:
        """
        Konfiguriert Adaptive Planning zur Laufzeit (Feature-Flag).
        
        Args:
            enabled: Aktiviere/Deaktiviere Adaptive Planning
            max_reflections: Max. Anzahl Reflections (default: 2)
            confidence_done_threshold: Schwellwert für "fertig" (default: 0.85)
            confidence_tools_threshold: Schwellwert für "mehr Tools" (default: 0.4)
        """
        self.adaptive_planning_enabled = enabled
        
        if enabled and ADAPTIVE_PLANNING_AVAILABLE:
            if self.adaptive_planner is None:
                self.adaptive_planner = AdaptivePlanner(orchestrator=self)
                logger.info("✅ AdaptivePlanner initialisiert")
            
            # Update thresholds
            self.adaptive_planner.max_reflections = max_reflections
            self.adaptive_planner.confidence_done_threshold = confidence_done_threshold
            self.adaptive_planner.confidence_tools_threshold = confidence_tools_threshold
            
            logger.info(
                f"Adaptive Planning konfiguriert: max_reflections={max_reflections}, "
                f"confidence_done={confidence_done_threshold:.2f}, "
                f"confidence_tools={confidence_tools_threshold:.2f}"
            )
        elif enabled and not ADAPTIVE_PLANNING_AVAILABLE:
            logger.warning("⚠️ Adaptive Planning angefordert, aber Module nicht verfügbar!")
            self.adaptive_planning_enabled = False
        else:
            logger.info("Adaptive Planning deaktiviert")
    
    def _llm_wrapper(self, prompt: str, max_tokens: int = 1024) -> str:
        """Wrapper für LLM-Calls aus Hybrid-Reasoning-Komponenten"""
        messages = [
            {"role": "system", "content": "Du bist ein hilfreicher Assistent."},
            {"role": "user", "content": prompt}
        ]
        return self._call_model(messages, max_tokens=max_tokens)

    def _generate_subqueries(self, query: str) -> List[str]:
        """Generate sub-queries through the central DecompositionEngine.

        Returns the factual sub-queries when the engine decided to decompose
        (i.e. complexity ≥ COMPLEX). When the engine returns a passthrough
        (SIMPLE/MODERATE complexity, no LLM, or empty query) this returns
        an empty list — the caller's existing single-query path then runs
        unchanged.

        This is the *only* sub-query producer in the orchestrator; the old
        scattered ``sub_query_gen`` / ``analyze_and_route``-fallback chain
        is replaced by this single, intent-aware entry point.
        """
        decomp = self.decomposition_engine.decompose(query)
        if not decomp.decomposed:
            return []
        return decomp.factual_queries

    def close(self) -> None:
        """Release tool and RAG resources."""
        try:
            if getattr(self, "tools", None) is not None:
                self.tools.close()  # type: ignore[attr-defined]
        except AttributeError as e:
            logger.debug(f"Tools-Close-Attributfehler: {e}")
        except Exception as e:
            logger.warning(f"Fehler beim Schließen der Tools: {type(e).__name__}: {e}")

    def __del__(self):  # pragma: no cover
        try:
            self.close()
        except Exception as e:
            # In __del__ sollten wir nicht loggen, da Logger möglicherweise nicht mehr verfügbar ist
            pass

    def _refresh_runtime_mode(self) -> None:
        """Synchronize orchestrator runtime settings with current APP_LOCAL_ONLY."""
        current_local_only_mode = parse_bool_env("APP_LOCAL_ONLY", "0")
        if current_local_only_mode == self.local_only_mode:
            return

        previous_mode = self.local_only_mode
        self.local_only_mode = current_local_only_mode
        self.subquery_web_fallback_enabled = not current_local_only_mode
        self._live_data_assessment_cache.clear()

        # Re-bind ToolManager to ensure singleton runtime mode transitions are applied.
        from agent.tools import get_tool_manager
        self.tools = get_tool_manager(llm_client=self.model_loader)

        logger.warning(
            "⚙️ Orchestrator Runtime-Mode-Wechsel erkannt (APP_LOCAL_ONLY: %s -> %s)",
            previous_mode,
            current_local_only_mode,
        )

    def set_rag_config(self, *, enabled: Optional[bool] = None, k: Optional[int] = None, min_score: Optional[float] = None,
                        persist_from_web: Optional[bool] = None, chunk_size: Optional[int] = None, chunk_overlap: Optional[int] = None,
                        faiss_min_confidence: Optional[float] = None) -> None:
        """Adjust RAG configuration at runtime."""
        logger.info(f"🔍 DEBUG set_rag_config called: k={k}, enabled={enabled}, faiss_min_confidence={faiss_min_confidence}, current rag_k={self.rag_k}, current web_search_k={self.web_search_k}")
        if enabled is not None:
            self.rag_enabled = bool(enabled)
        if k is not None:
            try:
                self.rag_k = max(1, int(k))
                self.web_search_k = self.rag_k  # NEU: Synchronisiere Web-Search k-Wert
                self.mq_k = self.rag_k  # NEU: Auto-Synchronisiere Multi-Query k-Wert
                logger.info(f"🔍 RAG-K und Web-Search-K gesetzt auf: {self.rag_k}")
            except (ValueError, TypeError) as e:
                logger.warning(f"Ungültiger RAG-K Wert: {e}, behalte aktuellen Wert ({self.rag_k})")
            except Exception as e:
                logger.error(f"Unerwarteter Fehler beim Setzen von RAG-K: {type(e).__name__}: {e}")
        if min_score is not None:
            try:
                self.rag_min_score = float(min_score)
            except (ValueError, TypeError) as e:
                logger.warning(f"Ungültiger RAG-Min-Score Wert: {e}, behalte aktuellen Wert ({self.rag_min_score})")
            except Exception as e:
                logger.error(f"Unerwarteter Fehler beim Setzen von RAG-Min-Score: {type(e).__name__}: {e}")
        # NEW 2025-10-11: FAISS Confidence Configuration
        if faiss_min_confidence is not None:
            try:
                self.faiss_min_confidence = float(faiss_min_confidence)
                logger.info(f"🎯 FAISS Min Confidence gesetzt auf: {self.faiss_min_confidence}")
            except (ValueError, TypeError) as e:
                logger.warning(f"Ungültiger FAISS-Min-Confidence Wert: {e}, behalte aktuellen Wert ({getattr(self, 'faiss_min_confidence', None)})")
            except Exception as e:
                logger.error(f"Unerwarteter Fehler beim Setzen von FAISS-Min-Confidence: {type(e).__name__}: {e}")
        if persist_from_web is not None:
            self.rag_persist_from_web = bool(persist_from_web)
        if chunk_size is not None:
            try:
                self.rag_chunk_size = max(200, int(chunk_size))
            except (ValueError, TypeError) as e:
                logger.warning(f"Ungültiger RAG-Chunk-Size Wert: {e}, behalte aktuellen Wert ({self.rag_chunk_size})")
            except Exception as e:
                logger.error(f"Unerwarteter Fehler beim Setzen von RAG-Chunk-Size: {type(e).__name__}: {e}")
        if chunk_overlap is not None:
            try:
                self.rag_chunk_overlap = max(0, int(chunk_overlap))
            except (ValueError, TypeError) as e:
                logger.warning(f"Ungültiger RAG-Chunk-Overlap Wert: {e}, behalte aktuellen Wert ({self.rag_chunk_overlap})")
            except Exception as e:
                logger.error(f"Unerwarteter Fehler beim Setzen von RAG-Chunk-Overlap: {type(e).__name__}: {e}")
    
    def set_multiquery_config(self, *, enabled: Optional[bool] = None, n: Optional[int] = None, k: Optional[int] = None) -> None:
        """Enable/disable multi-query RAG and adjust its parameters at runtime."""
        if enabled is not None:
            self.multiquery_enabled = bool(enabled)
        if n is not None:
            try:
                self.mq_n = max(1, int(n))
                # NEU: Auto-Synchronisiere rag_k und mq_k wenn nur mq_n gesetzt wird
                if k is None:  # Nur wenn k nicht explizit übergeben wurde
                    self.rag_k = self.mq_n
                    self.mq_k = self.mq_n
                    self.web_search_k = self.mq_n
                    logger.info(f"🔄 Auto-Sync: rag_k, mq_k, web_search_k synchronisiert auf mq_n={self.mq_n}")
            except (ValueError, TypeError) as e:
                logger.warning(f"Ungültiger Multi-Query-N Wert: {e}, behalte aktuellen Wert ({self.mq_n})")
            except Exception as e:
                logger.error(f"Unerwarteter Fehler beim Setzen von Multi-Query-N: {type(e).__name__}: {e}")
        if k is not None:
            try:
                self.mq_k = max(1, int(k))
                # NEU: Synchronisiere auch rag_k wenn explizit k gesetzt wird
                self.rag_k = self.mq_k
                self.web_search_k = self.mq_k
                logger.info(f"🔄 Multi-Query-K und RAG-K synchronisiert auf: {self.mq_k}")
            except (ValueError, TypeError) as e:
                logger.warning(f"Ungültiger Multi-Query-K Wert: {e}, behalte aktuellen Wert ({self.mq_k})")
            except Exception as e:
                logger.error(f"Unerwarteter Fehler beim Setzen von Multi-Query-K: {type(e).__name__}: {e}")

    def set_adaptive_rag_config(self, *, enabled: Optional[bool] = None) -> None:
        """Enable/disable Adaptive-RAG routing (query-complexity-based) at runtime.

        When enabled, the AdaptiveRAGRouter evaluates each query's complexity
        and routes to shallow (direct FAISS) or deep (MultiHop) retrieval.
        """
        if enabled is not None:
            self.adaptive_strategy = bool(enabled)
            logger.info(f"Adaptive-RAG-Routing: {'aktiviert' if enabled else 'deaktiviert'}")

    # ==================== Dynamic Context Window ====================

    def _compute_effective_k(self, query: str) -> int:
        """
        Berechnet effektives k per Query via QueryStrategyManager.
        
        Dreistufiges Adaptive-K:
          1. QueryStrategyManager analysiert Komplexität → recommended_k
          2. GUI rag_k dient als Maximum (Cap)
          3. IRCoT (separat, bereits implementiert) kann bei Lücken nachfragen
        
        Returns:
            effective_k: min(recommended_k, self.rag_k)
        """
        if not self.dynamic_k_enabled:
            return self.rag_k
        
        strategy_result = self.query_strategy_manager.analyze_and_route(query, use_llm=False)
        recommended_k = strategy_result.recommended_k
        
        # GUI-Slider als Cap (Maximum), recommended_k als dynamisches Minimum
        effective_k = min(recommended_k, self.rag_k)
        
        # Sicherheits-Minimum: mindestens 2 Chunks für sinnvolle CRAG/Reranking
        effective_k = max(2, effective_k)
        
        if effective_k != self.rag_k:
            logger.info(
                f"🎯 Dynamic-K: {strategy_result.complexity.value} → "
                f"recommended={recommended_k}, gui_max={self.rag_k}, effective={effective_k}"
            )
        
        return effective_k

    def _compute_effective_k_with_strategy(self, query: str):
        """
        Wie _compute_effective_k, gibt aber auch das Strategy-Result zurück.
        Vermeidet doppelten analyze_and_route()-Aufruf wenn Sub-Queries benötigt werden.
        
        Returns:
            (effective_k, strategy_result)
        """
        if not self.dynamic_k_enabled:
            return self.rag_k, None
        
        strategy_result = self.query_strategy_manager.analyze_and_route(query, use_llm=False)
        recommended_k = strategy_result.recommended_k
        effective_k = max(2, min(recommended_k, self.rag_k))
        
        if effective_k != self.rag_k:
            logger.info(
                f"🎯 Dynamic-K: {strategy_result.complexity.value} → "
                f"recommended={recommended_k}, gui_max={self.rag_k}, effective={effective_k}"
            )
        
        return effective_k, strategy_result
            
    def set_llm_evidence_selection(self, enabled: bool) -> None:
        """Enable/disable LLM-based evidence selection at runtime."""
        self.use_llm_evidence_selection = bool(enabled)
        logger.info(f"LLM Evidence Selection: {'aktiviert' if enabled else 'deaktiviert'}")

    def set_evidence_config(self, *,
                            max_candidates: Optional[int] = None,
                            shortlist_m: Optional[int] = None,
                            diversity_lambda: Optional[float] = None,
                            news_min_k: Optional[int] = None,
                            news_max_k: Optional[int] = None) -> None:
        """Adjust evidence selection parameters at runtime."""
        if max_candidates is not None:
            try:
                self.evidence_max_candidates = max(1, int(max_candidates))
            except (ValueError, TypeError) as e:
                logger.warning(f"Ungültiger Evidence-Max-Candidates Wert: {e}, behalte aktuellen Wert ({self.evidence_max_candidates})")
        if shortlist_m is not None:
            try:
                self.evidence_shortlist_m = max(1, int(shortlist_m))
            except (ValueError, TypeError) as e:
                logger.warning(f"Ungültiger Evidence-Shortlist-M Wert: {e}, behalte aktuellen Wert ({self.evidence_shortlist_m})")
        if diversity_lambda is not None:
            try:
                self.evidence_diversity_lambda = min(1.0, max(0.0, float(diversity_lambda)))
            except (ValueError, TypeError) as e:
                logger.warning(f"Ungültiger Evidence-Diversity-Lambda Wert: {e}, behalte aktuellen Wert ({self.evidence_diversity_lambda})")
        if news_min_k is not None:
            try:
                self.news_min_k = max(1, int(news_min_k))
            except (ValueError, TypeError) as e:
                logger.warning(f"Ungültiger News-Min-K Wert: {e}, behalte aktuellen Wert ({self.news_min_k})")
        if news_max_k is not None:
            try:
                nm = max(1, int(news_max_k))
                self.news_max_k = max(self.news_min_k, nm)
            except (ValueError, TypeError) as e:
                logger.warning(f"Ungültiger News-Max-K Wert: {e}, behalte aktuellen Wert ({self.news_max_k})")

    def set_generation_limits(self, *, planner_max_tokens: Optional[int] = None, summarizer_max_tokens: Optional[int] = None, verifier_max_tokens: Optional[int] = None) -> None:
        """Adjust max token limits for planner, summarizer and verifier at runtime."""
        if planner_max_tokens is not None:
            try:
                self.planner_max_tokens = max(256, int(planner_max_tokens))
            except (ValueError, TypeError) as e:
                logger.warning(f"Ungültiger Planner-Max-Tokens Wert: {e}, behalte aktuellen Wert ({self.planner_max_tokens})")
        if summarizer_max_tokens is not None:
            try:
                self.summarizer_max_tokens = max(128, int(summarizer_max_tokens))
            except (ValueError, TypeError) as e:
                logger.warning(f"Ungültiger Summarizer-Max-Tokens Wert: {e}, behalte aktuellen Wert ({self.summarizer_max_tokens})")
        if verifier_max_tokens is not None:
            try:
                self.verifier_max_tokens = max(128, int(verifier_max_tokens))
            except (ValueError, TypeError) as e:
                logger.warning(f"Ungültiger Verifier-Max-Tokens Wert: {e}, behalte aktuellen Wert ({self.verifier_max_tokens})")

    def initialize_privacy_handler(self) -> None:
        """Initialize CoT privacy handler with the model_loader's LLM client.
        
        This should be called after the model_loader has loaded the LLM model.
        """
        if not COT_PRIVACY_AVAILABLE:
            logger.warning("CoT Privacy Protection nicht verfügbar - überspringe Initialisierung")
            return
            
        if self._cot_privacy_handler is not None:
            logger.info("CoT Privacy Handler bereits initialisiert - überspringe")  # type: ignore[unreachable]
            return
            
        if not self.model_loader:
            logger.warning("Model Loader nicht verfügbar - Privacy Handler kann nicht initialisiert werden")
            return
        
        if not ChainOfThoughtPrivacyHandler:
            logger.warning("Enhanced Privacy Handler nicht verfügbar - Import fehlgeschlagen")
            return
            
        try:  # type: ignore[unreachable]
            # Erstelle Privacy Handler DIREKT mit LLM-Client (verhindert doppeltes Model-Loading!)
            self._cot_privacy_handler = ChainOfThoughtPrivacyHandler(llm_client=self.model_loader)
            
            logger.info("✅ CoT Privacy Handler erfolgreich initialisiert mit LLM-Client")
            
        except Exception as e:
            logger.error(f"❌ Fehler beim Initialisieren des CoT Privacy Handlers: {e}")
            self._cot_privacy_handler = None

    def _get_current_tab_mode(self) -> str:
        """Returns the current tab mode for tool profile filtering.

        SOTA Filesystem-Connector (2026): Declarative tool availability
        per tab/mode. Falls back to 'main_chat' if mode is unknown.
        """
        # Check if we have a stored tab mode attribute
        tab_mode = getattr(self, "_current_tab_mode", None)
        if tab_mode is not None:
            return tab_mode
        # Check intent-based mode detection
        intent = getattr(self, "_current_intent", None)
        if intent == "finance":
            return "finance_tab"
        if intent == "psychological":
            return "wellbeing_tab"
        return "main_chat"

    def _is_tool_allowed_for_mode(self, tool_name: str) -> bool:
        """Check if a tool is allowed for the current tab mode."""
        if not TOOL_PROFILES_AVAILABLE:
            return True  # No profiles configured → allow all
        mode = self._get_current_tab_mode()
        return is_tool_allowed(tool_name, mode)

    def _build_runtime_planner_tool_block(self, *, max_tools: int = 20) -> str:
        """Build a compact, runtime-accurate tool list for the planner prompt."""
        tool_catalog: Dict[str, Dict[str, Any]] = {}

        toolkit = getattr(getattr(self, "tools", None), "toolkit", None)
        toolkit_tools = getattr(toolkit, "tools", None)
        if isinstance(toolkit_tools, dict):
            for name, info in toolkit_tools.items():
                if isinstance(info, dict):
                    # SOTA: Filter by tool profile
                    if self._is_tool_allowed_for_mode(name):
                        tool_catalog[name] = info

        # rag_search exists on ToolManager even if AgentToolkit removes it from its own map.
        if getattr(self, "tools", None) is not None and hasattr(self.tools, "rag_search"):
            if self._is_tool_allowed_for_mode("rag_search"):
                tool_catalog.setdefault(
                    "rag_search",
                    {
                        "description": "Durchsucht die lokale Wissensbasis (RAG/FAISS/SQLite).",
                        "parameters": {
                            "type": "object",
                            "properties": {"query": {"type": "string"}, "k": {"type": "integer"}},
                            "required": ["query"],
                        },
                    },
                )

        if not tool_catalog:
            return ""

        lines: List[str] = []
        for tool_name, tool_info in sorted(tool_catalog.items(), key=lambda kv: kv[0])[:max_tools]:
            description = str(tool_info.get("description", "")).strip().replace("\n", " ")
            description = (description[:150] + "...") if len(description) > 150 else description
            params = ((tool_info.get("parameters") or {}).get("properties") or {})
            required = set((tool_info.get("parameters") or {}).get("required") or [])

            if params:
                param_labels: List[str] = []
                for param_name in params.keys():
                    marker = "*" if param_name in required else ""
                    param_labels.append(f"{param_name}{marker}")
                lines.append(f"- {tool_name}: {description} | params: {', '.join(param_labels)}")
            else:
                lines.append(f"- {tool_name}: {description}")

        return "\n".join(lines)

    def planner_step(self, query: str, history: List[Dict[str, Any]], time_context: Optional[str] = None) -> Tuple[List[ToolCall], Optional[str], Optional[str], int, Optional[str], Optional[str]]:
        """Plan tool calls using compact planner prompt. Returns (calls, final_text_if_any, raw_planner_output, elapsed_ms, reasoning, critique)."""
        self._refresh_runtime_mode()
        
        # STEP 1: Semantic Intent Detection (REPLACES KEYWORDS!)
        intent_hint = ""
        if self.use_intent_detection and self.intent_detector:
            try:  # type: ignore[unreachable]
                # Build context for intent detection
                context = {
                    "history": history[-3:] if history else [],
                    "time_context": time_context
                }
                
                # Available tools for better suggestion
                available_tools = [
                    {
                        "name": "web_search",
                        "description": "Sucht im Web nach aktuellen Informationen",
                        "use_cases": ["Aktuelle News", "Fakten", "Definitionen"]
                    },
                    {
                        "name": "kg_search",
                        "description": "Durchsucht lokalen Knowledge Graph",
                        "use_cases": ["Persönliche Daten", "Historische Notizen", "Beziehungen"]
                    },
                    {
                        "name": "canvas",
                        "description": "Erstellt visuelle Diagramme auf dem Canvas (Netzwerk, Timeline, Hierarchie, Scatter, etc.)",
                        "use_cases": ["Beziehungen visualisieren", "Zeitverlaeufe darstellen", "Daten graphisch zeigen", "Verteilungen plotten"]
                    },
                    {
                        "name": "create_diagram",
                        "description": "Legacy-Alias fuer Canvas-Diagramme (abwaertskompatibel)",
                        "use_cases": ["Beziehungen visualisieren", "Zeitverlaeufe darstellen", "Daten graphisch zeigen", "Verteilungen plotten"]
                    }
                ]
                
                # DETECT INTENT SEMANTICALLY (NO KEYWORDS!)
                intent_result = self.intent_detector.detect_intent(
                    user_message=query,
                    context=context,
                    available_tools=available_tools
                )
                
                logger.info(f"Intent Detection: {intent_result.intent_type.value} (confidence: {intent_result.confidence:.2f})")
                
                # If confident, inject hint into planner prompt
                if intent_result.is_confident(self.intent_confidence_threshold):
                    suggested_tools = [t['tool_name'] for t in intent_result.suggested_tools[:3]]
                    
                    intent_hint = f"""

[SEMANTIC INTENT ANALYSIS - Use this to guide your tool selection!]
User Intent: {intent_result.intent_type.value}
Confidence: {intent_result.confidence:.1%}
Reasoning: {intent_result.reasoning[:200]}
Suggested Tools: {suggested_tools}

IMPORTANT: 
- If intent is 'visualization', you MUST use the 'canvas' tool (or 'create_diagram' if needed)!
- The user wants a visual representation, not just text!
- Choose the most appropriate diagram type based on the data.
"""
                    logger.info(f"Intent hint injected: {intent_result.intent_type.value} -> {suggested_tools}")
                
            except Exception as e:
                logger.error(f"Intent detection failed: {e}", exc_info=True)
                intent_hint = ""
        
        # STEP 2: Build planner prompt (with optional intent hint)
        sys_msg = {"role": "system", "content": f"{PLANNER_SYSTEM}{_planner_language_instruction()}"}
        
        # Erweitere User-Prompt um Zeitkontext, falls verfügbar
        user_content = PLANNER_USER_TEMPLATE.format(query=query)
        runtime_tools_block = self._build_runtime_planner_tool_block(max_tools=24)
        if runtime_tools_block:
            user_content += (
                "\n\nAKTIV VERFUEGBARE TOOLS (RUNTIME, priorisiere diese Liste):\n"
                f"{runtime_tools_block}\n"
                "Nur Tools aus dieser Runtime-Liste verwenden."
            )
        if time_context:
            user_content = f"{time_context}\n\n{user_content}"
        
        # Add intent hint if available
        if intent_hint:
            user_content = user_content + intent_hint
            
        user_msg = {"role": "user", "content": user_content}
        
        # Keep planner context very small – last 2 turns at most
        hist = list(history[-2:]) if history else []
        messages = [sys_msg, *hist, user_msg]
        
        t0 = time.perf_counter()
        # ✅ strip_think_blocks=False: Magistral packt Planner-Output oft in [THINK]-Blöcke.
        # Ohne diesen Flag wird der gesamte Output entfernt → Planner liefert "nichts".
        raw = self.model_loader.generate_response(
            messages=messages, max_tokens=self.planner_max_tokens,
            temperature=0.2, image_path=None, strip_think_blocks=False
        )
        elapsed_ms = int((time.perf_counter() - t0) * 1000)
        
        # Debug: Zeige rohen Planner-Output
        if raw:
            raw_preview = raw[:2000].replace('\n', '\\n')
            logger.info(f"[PLANNER RAW] ({len(raw)} chars, {elapsed_ms}ms): {raw_preview}")
        else:
            logger.warning(f"[PLANNER RAW] LEER! ({elapsed_ms}ms)")
        
        calls, final_text, reasoning, critique = self._parse_planner_output(raw)
        
        # Debug: Zeige Parse-Ergebnis
        logger.info(f"[PLANNER PARSED] calls={len(calls)}, final_text={'yes' if final_text else 'no'}, "
                     f"reasoning={'yes' if reasoning else 'no'}, critique={'yes' if critique else 'no'}")

        # Declarative retrieval routing (INTERNAL_ONLY / RAG_REQUIRED / WEB_REQUIRED)
        route_decision = self._decide_retrieval_route(query, time_context=time_context)
        calls = self._apply_retrieval_route(calls, route_decision)
        logger.info(
            "[RetrievalRouter] route=%s confidence=%.2f reason=%s",
            route_decision.route.value,
            route_decision.confidence,
            route_decision.reason,
        )
        
        return calls, final_text, raw, elapsed_ms, reasoning, critique

    # --- Tools + Summarizer ---
    def summarize(self, query: str, history: List[Dict[str, Any]], evidence: List[Source], extras: List[str], *, fallback: bool = False) -> Tuple[str, Dict[str, int]]:
        """Create a draft answer from selected evidence and extras. Returns (text, metrics)."""
        # PHASE 4 STEP 6: Use ResponseBuilder for prompt building
        # Build prompt via ResponseBuilder
        prompt_result = self.response_builder.build_summarizer_prompt(
            query=query,
            sources=evidence,
            extras=extras,
            history=history,
            fallback=fallback,
            user_system_prompt=""  # Could be configurable
        )
        
        # Extract messages
        messages = prompt_result['messages']
        
        # Token-aware trim (estimate tokens)
        sys_msg = messages[0]  # System message
        user_msg = messages[-1]  # User message (last)
        hist = messages[1:-1] if len(messages) > 2 else []  # History in between
        
        # Compute available budget for history and trim
        base_tokens = self.ctx.estimate_tokens([sys_msg, user_msg])
        budget_for_hist = max(0, self.ctx.n_ctx - self.ctx.reserve - base_tokens)
        initial_hist_len = len(hist)
        hist = self.ctx.trim_history(hist, budget_for_hist)
        trimmed_count = max(0, initial_hist_len - len(hist))
        
        # Rebuild messages with trimmed history
        final_messages = [sys_msg, *hist, user_msg]
        
        # Generate response
        t0 = time.perf_counter()
        text = self.model_loader.generate_response(
            messages=final_messages,
            max_tokens=token_scaling.main_generation_max_tokens(
                fallback=self.summarizer_max_tokens
            ),
            temperature=0.2,
            image_path=None
        )
        elapsed_ms = int((time.perf_counter() - t0) * 1000)
        
        metrics = {
            "hist_trimmed_count": trimmed_count,
            "hist_tokens_used": base_tokens,
            "budget_used": base_tokens + self.ctx.estimate_tokens(hist),
            "elapsed_ms": elapsed_ms,
        }
        return text, metrics

    # ==================== IRCoT: Iterative Retrieval ====================

    # ==================== FOLLOW-UP QUESTION GENERATION ====================
    def _generate_followup_questions(self, query: str, answer_text: str) -> List[str]:
        """Generate follow-up questions via a dedicated, lightweight LLM call.
        
        This is the fallback when the summarizer didn't produce a [FOLLOW_UP]
        block (e.g. due to token limits or the LLM ignoring the instruction).
        Uses a short, focused prompt with low max_tokens for fast generation.
        
        Returns:
            List of 2-4 follow-up question strings, or [] on failure.
        """
        if not answer_text or len(answer_text.strip()) < 50:
            return []
        
        # Truncate answer for the prompt (we only need the gist, not the full text)
        answer_snippet = answer_text[:2000] if len(answer_text) > 2000 else answer_text
        
        messages = [
            {
                "role": "system",
                "content": (
                    "Du generierst moegliche naechste Nutzerfragen zu einer gegebenen Antwort. "
                    "Die Fragen werden als klickbare Buttons angezeigt.\n"
                    "Regeln:\n"
                    "- Genau 3 Fragen, getrennt durch |\n"
                    "- Konkret und spezifisch zum Thema\n"
                    "- Verschiedene Perspektiven (Risiken, Vergleiche, Details, Zukunft)\n"
                    "- In derselben Sprache wie die Nutzerfrage\n"
                    f"- {FOLLOWUP_PERSPECTIVE_INSTRUCTION}\n"
                    "- NUR die Fragen ausgeben, kein anderer Text"
                )
            },
            {
                "role": "user",
                "content": (
                    f"Nutzerfrage: {query}\n\n"
                    f"Antwort (Zusammenfassung):\n{answer_snippet}\n\n"
                    "Generiere genau 3 moegliche naechste Nutzerfragen (getrennt durch |):"
                )
            }
        ]
        
        try:
            t0 = time.perf_counter()
            raw = self.model_loader.generate_response(
                messages=messages,
                max_tokens=256,
                temperature=0.4,
                image_path=None
            )
            elapsed = int((time.perf_counter() - t0) * 1000)
            logger.info(f"🔄 Follow-Up-Generierung: {elapsed}ms")
            
            if not raw or not raw.strip():
                return []
            
            # Parse pipe-separated questions
            from utils.followup_question_extractor import _parse_question_block, _validate_questions
            questions = _parse_question_block(raw.strip())
            questions = _validate_questions(questions)
            
            if questions:
                logger.info(f"✅ Dedizierte Follow-Up-Generierung: {len(questions)} Fragen")
                return questions[:5]
            
            return []
            
        except Exception as e:
            logger.warning(f"Follow-Up-Generierung fehlgeschlagen: {type(e).__name__}: {e}")
            return []

    def _finalize_answer(
        self,
        query: str,
        raw_answer: str,
        sources: List[Source],
        *,
        extracted_followups: Optional[List[str]] = None,
        include_citations: Optional[bool] = None,
        append_sources: Optional[bool] = None,
    ) -> Tuple[str, List[str]]:
        """Normalize final answer text and attach follow-up questions."""
        cleaned_answer, parsed_followups = extract_followup_questions(raw_answer or "")
        followups = list(extracted_followups or parsed_followups or [])
        if not followups:
            followups = self._generate_followup_questions(query, cleaned_answer or "")

        formatted_answer = self.response_builder.format_response(
            raw_answer=cleaned_answer,
            sources=sources,
            include_citations=self.citation_inline_details if include_citations is None else include_citations,
            append_sources=self.append_sources_block if append_sources is None else append_sources,
        )
        return formatted_answer, followups

    def _generate_answer_from_sources(
        self,
        query: str,
        history: List[Dict[str, Any]],
        sources: List[Source],
        extras: List[str],
        *,
        fallback: bool = False,
        trace: Optional[AgentTrace] = None,
        results: Optional[List[ToolResult]] = None,
    ) -> Tuple[str, Optional[Any], List[Source], List[ToolResult], List[str], Dict[str, int]]:
        """Generate, verify, and optionally self-correct an answer from evidence."""
        current_sources = list(sources)
        current_results = list(results or [])

        draft_text, sum_metrics = self.summarize(query, history, current_sources, extras, fallback=fallback)
        if trace is not None:
            trace.summarizer_draft_chars = len(draft_text or "")
            trace.hist_trimmed_count = sum_metrics.get("hist_trimmed_count", 0)
            trace.hist_tokens_used = sum_metrics.get("hist_tokens_used", 0)
            trace.budget_used = sum_metrics.get("elapsed_ms", 0)
            trace.summarize_ms = sum_metrics.get("elapsed_ms", 0)

        draft_text, extracted_followups = extract_followup_questions(draft_text or "")

        t_v0 = time.perf_counter()
        final_text, verification_result = self._verify_and_fix_tools(
            query=query,
            draft=draft_text,
            evidence=current_sources,
            fallback=fallback,
        )
        verify_ms = int((time.perf_counter() - t_v0) * 1000)
        if trace is not None:
            trace.verify_ms = verify_ms
            if verification_result is not None:
                trace.verification_result = verification_result
                trace.verification_confidence = verification_result.confidence_score
                trace.verification_quality = verification_result.quality_score
                trace.verification_grounding = verification_result.grounding_score
                trace.verification_hallucination_risk = verification_result.hallucination_risk
                trace.verification_issues = verification_result.issues
                trace.verification_warnings = verification_result.warnings

        crag_retry_result = self._run_crag_self_correction(
            query=query,
            history=history,
            extras=extras,
            current_sources=current_sources,
            current_results=current_results,
            current_verification_result=verification_result,
            fallback=fallback,
        )
        if crag_retry_result is not None:
            final_text, verification_result, current_sources, current_results = crag_retry_result
            if trace is not None and verification_result is not None:
                trace.verification_result = verification_result
                trace.verification_confidence = verification_result.confidence_score
                trace.verification_quality = verification_result.quality_score
                trace.verification_grounding = verification_result.grounding_score
                trace.verification_hallucination_risk = verification_result.hallucination_risk
                trace.verification_issues = verification_result.issues
                trace.verification_warnings = verification_result.warnings

        sota_eval = self._evaluate_sota_answer_quality(
            query=query,
            answer=final_text,
            sources=current_sources,
        )
        if trace is not None and sota_eval is not None:
            merged_metrics = dict(getattr(trace, "sota_metrics", {}) or {})
            merged_metrics.update(sota_eval.get("metrics", {}))
            trace.sota_metrics = merged_metrics

        if trace is not None:
            trace.verifier_changed = (final_text or "") != (draft_text or "")
            try:
                trace.verifier_delta_chars = abs(len(final_text or "") - len(draft_text or ""))
                denom = max(1, len(draft_text or ""))
                trace.verifier_changed_ratio = trace.verifier_delta_chars / denom
            except Exception:
                trace.verifier_delta_chars = 0
                trace.verifier_changed_ratio = 0.0

            trace.summarizer_temp = 0.2
            trace.verifier_temp = 0.0
            trace.summarizer_max_tokens = self.summarizer_max_tokens
            trace.verifier_max_tokens = self.verifier_max_tokens

        return final_text, verification_result, current_sources, current_results, extracted_followups, sum_metrics

    def _verify_and_fix_tools(
        self,
        query: str,
        draft: str,
        evidence: List[Source],
        *,
        fallback: bool = False,
    ) -> Tuple[str, Optional[Any]]:
        """Verify a draft and return the corrected answer if needed."""
        final_text, verification_result = self.verify_step(
            query=query,
            draft=draft,
            evidence=evidence,
            fallback=fallback,
        )
        return final_text, verification_result

    def _expand_query_for_multimodal(self, query: str) -> str:
        """Expand retrieval queries with cross-modal hints when available."""
        expanded_query = (query or "").strip()
        multimodal_rag = self.multimodal_rag
        if not expanded_query or multimodal_rag is None:
            return expanded_query

        try:
            candidate = multimodal_rag.expand_query(expanded_query)
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()
        except Exception as exc:
            logger.debug("Multimodal query expansion skipped: %s", exc)

        return expanded_query

    def _evaluate_sota_answer_quality(
        self,
        query: str,
        answer: str,
        sources: List[Source],
    ) -> Optional[Dict[str, Any]]:
        """Run post-answer StrixKAT evaluation on the finalized answer."""
        evaluator = self.strixkat_eval
        if evaluator is None or not answer or not sources:
            return None

        try:
            return evaluator.evaluate(query=query, answer=answer, sources=sources)
        except Exception as exc:
            logger.debug("[SOTA] StrixKAT post-answer evaluation skipped: %s", exc)
            return None

    def _live_search(
        self,
        query: str,
        *,
        focused_query: Optional[str] = None,
        num_results: Optional[int] = None,
    ) -> Tuple[List[Source], Dict[str, Any]]:
        """Execute a live web search with SOTA query planning + reflection.

        Uses WebSearchPlanner to decompose complex queries into focused sub-queries,
        then WebSearchReflector to assess sufficiency and refine if needed.
        Falls back to simple search when planner/reflector are unavailable.

        Parameters
        ----------
        query : str
            Original user query.
        focused_query : Optional[str]
            Refined query from retrieval router.
        num_results : Optional[int]
            Number of results per search (defaults to self.web_search_k).

        Returns
        -------
        Tuple[List[Source], Dict[str, Any]]
            Sources and statistics dict.
        """
        original_query = (focused_query or query or "").strip()
        search_query = self._expand_query_for_multimodal(original_query)
        result_stats: Dict[str, Any] = {
            "route": "WEB_REQUIRED",
            "original_query": original_query,
            "query": search_query,
            "sources": 0,
            "planner_used": False,
            "reflector_used": False,
            "refinement_rounds": 0,
        }
        if not search_query or self.local_only_mode:
            return [], result_stats

        # --- SOTA: Try Planner+Reflector pipeline ---
        if self._web_search_planner is not None and self._web_search_reflector is not None:
            try:
                sources = self._live_search_with_planning(
                    search_query, num_results, result_stats
                )
                return sources, result_stats
            except Exception as exc:
                logger.warning("[Orchestrator] Planner/Reflector pipeline failed, falling back: %s", exc)
                result_stats["planner_error"] = str(exc)

        # --- Fallback: Simple single search (original behavior) ---
        web_call = ToolCall(
            tool="web_search",
            parameters={"query": search_query, "num_results": num_results or self.web_search_k},
        )
        web_results = self.tools.run([web_call])
        sources: List[Source] = []
        if web_results and web_results[0].success and web_results[0].results:
            sources = self.tools.to_sources(web_results, top_k=5)
            result_stats["sources"] = len(sources)
            if self.rag_persist_from_web:
                try:
                    self._submit_persist_web_to_rag(web_results[0].results)
                except Exception as exc:
                    logger.debug("Web-to-RAG persistence skipped: %s", exc)
        return sources, result_stats

    def _live_search_with_planning(
        self,
        search_query: str,
        num_results: Optional[int],
        result_stats: Dict[str, Any],
    ) -> List[Source]:
        """Execute web search with query planning and reflection loop.

        Flow:
        1. Planner decomposes query into focused sub-queries
        2. Execute searches for each sub-query
        3. Reflector assesses sufficiency
        4. If insufficient & refinement suggested: repeat (max 2 rounds)
        """
        from agent.web_search_planner import WebSearchPlan

        k = num_results or self.web_search_k
        all_results: List[Any] = []
        max_refinement_rounds = 2
        current_query = search_query

        for round_idx in range(max_refinement_rounds + 1):
            if round_idx > 0:
                result_stats["refinement_rounds"] = round_idx

            # Step 1: Plan -- decompose into sub-queries
            plan: Optional[WebSearchPlan] = None
            if self._web_search_planner is not None:
                try:
                    plan = self._web_search_planner.plan(current_query, context=result_stats.get("original_query", ""))
                    result_stats["planner_used"] = True
                    result_stats[f"plan_round_{round_idx}"] = {
                        "strategy": plan.strategy,
                        "num_sub_queries": len(plan.sub_queries),
                        "confidence": plan.confidence,
                    }
                except Exception as exc:
                    logger.debug("[Orchestrator] Planner failed in round %d: %s", round_idx, exc)

            # Step 2: Execute searches
            if plan:
                # Run sub-queries (parallel for small sets, sequential for large)
                tool_calls = [
                    ToolCall(tool="web_search", parameters={"query": sq.query, "num_results": k})
                    for sq in plan.sub_queries
                ]
                round_results = self.tools.run(tool_calls)
            else:
                # No plan: single search
                tool_calls = [ToolCall(tool="web_search", parameters={"query": current_query, "num_results": k})]
                round_results = self.tools.run(tool_calls)

            # Collect successful results
            for r in round_results:
                if r.success and r.results:
                    all_results.append(r.results)
                    # Persist to RAG off-critical-path
                    if self.rag_persist_from_web:
                        try:
                            self._submit_persist_web_to_rag(r.results)
                        except Exception:
                            pass  # silent, non-critical

            # Step 3: Reflect -- are results sufficient?
            if self._web_search_reflector is not None and all_results:
                try:
                    # Flatten results for reflection
                    flat_results: List[Dict[str, Any]] = []
                    for batch in all_results:
                        if isinstance(batch, list):
                            flat_results.extend(batch[:5])  # limit per batch
                        else:
                            flat_results.append(batch)

                    verdict = self._web_search_reflector.reflect(current_query, flat_results)
                    result_stats["reflector_used"] = True
                    result_stats[f"verdict_round_{round_idx}"] = {
                        "sufficient": verdict.sufficient,
                        "confidence": verdict.confidence,
                        "missing": verdict.missing_aspects,
                    }

                    if verdict.sufficient:
                        break  # Done!

                    # Not sufficient -- refine if suggested
                    if verdict.suggested_refinement and round_idx < max_refinement_rounds:
                        current_query = verdict.suggested_refinement
                        result_stats["refinement_query"] = current_query
                        continue  # Next round with refined query
                    else:
                        break  # No refinement or max rounds reached
                except Exception as exc:
                    logger.debug("[Orchestrator] Reflector failed in round %d: %s", round_idx, exc)
            else:
                break  # No reflector or no results

        # Convert all collected results to Sources
        sources: List[Source] = []
        if all_results:
            # Reconstruct ToolResults for to_sources
            class _FakeToolResult:
                def __init__(self, data):
                    self.success = True
                    self.results = data
            fake_results = [_FakeToolResult(batch) for batch in all_results]
            sources = self.tools.to_sources(fake_results, top_k=10)
            result_stats["sources"] = len(sources)

        return sources

    def _rag_enhanced(
        self,
        query: str,
        history: List[Dict[str, Any]],
        extras: List[str],
        *,
        time_context: Optional[str] = None,
    ) -> Tuple[List[Source], Dict[str, Any]]:
        """Run retrieval with live-data routing and source validation."""
        decision = self._decide_retrieval_route(query, time_context=time_context)
        sources: List[Source] = []
        stats: Dict[str, Any] = {
            "route": decision.route.value,
            "reason": decision.reason,
            "confidence": decision.confidence,
            "focused_query": decision.focused_query,
        }
        rag_query = self._expand_query_for_multimodal(decision.focused_query or query)
        stats["rag_query"] = rag_query

        if decision.route == RetrievalRoute.WEB_REQUIRED:
            live_sources, live_stats = self._live_search(
                query,
                focused_query=decision.focused_query,
                num_results=self.web_search_k,
            )
            sources.extend(live_sources)
            stats["live_search"] = live_stats

        if self.rag_enabled and decision.route != RetrievalRoute.INTERNAL_ONLY:
            try:
                rag_result = self.tools.rag_search(
                    rag_query,
                    k=self.rag_k,
                    min_score=self.rag_min_score,
                    faiss_min_confidence=self.faiss_min_confidence,
                )
                if rag_result.success and rag_result.results:
                    sources.extend(self.tools.to_sources([rag_result], top_k=5))
                    stats["rag_sources"] = len(rag_result.results)
            except Exception as exc:
                logger.debug("Enhanced RAG search skipped: %s", exc)

        if sources:
            sources, validation_stats = self.validate_and_extend_sources(
                query=query,
                sources=sources,
                min_relevant_sources=2,
            )
            stats["validation"] = validation_stats

        return self._deduplicate_sources(sources), stats

    def _ircot_loop(
        self,
        query: str,
        sources: List[Source],
        results: List,
        trace: AgentTrace,
    ) -> List[Source]:
        """
        IRCoT -- Interleaving Retrieval with Chain-of-Thought.

        After initial evidence selection the LLM evaluates whether the
        collected evidence is *sufficient* to answer the query.  If gaps
        remain it generates a focused refinement query, a new RAG search
        is triggered, evidence is re-selected from the enlarged pool, and
        the loop repeats -- up to ``self.ircot_max_iterations`` times.

        Returns the (possibly enriched) source list.
        """
        import json as _json

        max_iter = self.ircot_max_iterations
        min_conf = self.ircot_min_confidence
        ircot_trace: List[Dict[str, Any]] = []

        # ── Pre-Processing: Distill web evidence before entering the loop ──
        # Query-aware factual extraction removes boilerplate/noise from web snippets
        # and compresses them to dense fact statements — preventing "Lost in the Middle"
        # when many sources are present in the evidence block.
        distilled_facts: Dict[str, str] = {}
        try:
            distilled_facts = self.evidence_manager.distill_web_evidence(
                sources=sources[:12],
                query=query,
                model_loader=self,
                top_k_web_sources=3,
            )
            if distilled_facts:
                logger.info("[IRCoT] Distilled %d web sources for evidence block", len(distilled_facts))
        except Exception as _dist_exc:
            logger.debug("[IRCoT] Distillation skipped: %s", _dist_exc)

        for iteration in range(1, max_iter + 1):
            # ── 1. Build evidence summary for the LLM ──
            evidence_summary_parts: List[str] = []
            for idx, s in enumerate(sources[:12], 1):
                src_key = getattr(s, 'url', '') or f"idx:{id(s)}"
                # Use distilled facts when available; fall back to raw snippet truncation
                if src_key in distilled_facts and distilled_facts[src_key].strip():
                    snippet = distilled_facts[src_key]
                else:
                    snippet = (s.snippet or "")[:800]
                evidence_summary_parts.append(f"[{idx}] {s.title or '?'}: {snippet}")
            evidence_block = "\n".join(evidence_summary_parts) if evidence_summary_parts else "(keine Evidenz)"

            gap_prompt = (
                f"<query>{query}</query>\n"
                f"<evidence count=\"{len(sources)}\">\n{evidence_block}\n</evidence>\n\n"
                "Analysiere, ob die Evidenz die Frage VOLLSTÄNDIG beantworten kann.\n"
                "Antworte NUR als JSON (kein Markdown):\n"
                '{"sufficient": true/false, "confidence": 0.0-1.0, '
                '"missing": ["fehlender Aspekt 1", ...], '
                '"refined_query": "präzisere Suchanfrage" oder null}'
            )

            try:
                raw = self.model_loader.generate_response(
                    messages=[
                        {"role": "system", "content": "Du bist ein Evidenz-Lücken-Analysator. Antworte NUR in JSON."},
                        {"role": "user", "content": gap_prompt},
                    ],
                    max_tokens=300,
                    temperature=0.1,
                )
            except Exception as e:
                logger.warning("[IRCoT] LLM gap-analysis failed: %s", e)
                break

            # ── 2. Parse LLM JSON response ──
            analysis: Dict[str, Any] = {}
            if raw:
                # Strip markdown fences if present
                cleaned = raw.strip()
                if cleaned.startswith("```"):
                    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
                    cleaned = re.sub(r"```\s*$", "", cleaned)
                try:
                    analysis = _json.loads(cleaned)
                except _json.JSONDecodeError:
                    # Try to extract JSON object with regex
                    m = re.search(r"\{.*\}", cleaned, re.DOTALL)
                    if m:
                        try:
                            analysis = _json.loads(m.group())
                        except _json.JSONDecodeError:
                            pass

            is_sufficient = analysis.get("sufficient", True)
            confidence = float(analysis.get("confidence", 1.0))
            refined_query = analysis.get("refined_query")

            logger.info(
                "[IRCoT] Iteration %d/%d -- sufficient=%s, confidence=%.2f",
                iteration, max_iter, is_sufficient, confidence,
            )

            ircot_entry = {
                "iteration": iteration,
                "sufficient": is_sufficient,
                "confidence": confidence,
                "missing": analysis.get("missing", []),
                "refined_query": refined_query,
            }

            if is_sufficient or confidence >= min_conf:
                ircot_entry["action"] = "stop_sufficient"
                ircot_trace.append(ircot_entry)
                break

            if not refined_query or not isinstance(refined_query, str) or len(refined_query.strip()) < 3:
                ircot_entry["action"] = "stop_no_query"
                ircot_trace.append(ircot_entry)
                break

            # ── 3. Execute refined retrieval ──
            refined_query = refined_query.strip()
            logger.info("[IRCoT] Retrieving for refined query: '%s'", refined_query[:80])

            try:
                # IRCoT refinement uses dynamic-k for the refined query
                ircot_effective_k = self._compute_effective_k(refined_query)
                new_results = self.rag_manager.execute_rag_with_multiquery(
                    query=refined_query,
                    k=ircot_effective_k,
                    min_score=self.rag_min_score,
                    multiquery_enabled=False,
                )
                results.extend(new_results)
                new_count = sum(len(getattr(r, "results", None) or []) for r in new_results)
            except Exception as e:
                logger.warning("[IRCoT] Refined retrieval failed: %s", e)
                ircot_entry["action"] = "stop_retrieval_error"
                ircot_entry["error"] = str(e)
                ircot_trace.append(ircot_entry)
                break

            # ── 4. Re-select evidence from enlarged pool ──
            try:
                evidence_result = self.evidence_manager.select_evidence_from_tool_results(
                    query=query,  # original query for relevance scoring
                    tool_results=results,
                    evidence_max_candidates=self.evidence_max_candidates,
                    evidence_shortlist_m=self.evidence_shortlist_m,
                    evidence_diversity_lambda=self.evidence_diversity_lambda,
                    model_loader=self.model_loader,
                )
                sources = evidence_result.sources
            except Exception as e:
                logger.warning("[IRCoT] Evidence re-selection failed: %s", e)
                ircot_entry["action"] = "stop_evidence_error"
                ircot_entry["error"] = str(e)
                ircot_trace.append(ircot_entry)
                break

            ircot_entry["action"] = "refined"
            ircot_entry["new_results"] = new_count
            ircot_entry["total_sources"] = len(sources)
            ircot_trace.append(ircot_entry)

            logger.info(
                "[IRCoT] Iteration %d: +%d results → %d total sources",
                iteration, new_count, len(sources),
            )

        # Store IRCoT trace
        if ircot_trace:
            try:
                trace.ircot_iterations = ircot_trace  # type: ignore[attr-defined]
                trace.ircot_total_iterations = len(ircot_trace)  # type: ignore[attr-defined]
            except Exception:
                pass

        return sources

    @staticmethod
    def _coerce_float(value: Any) -> Optional[float]:
        try:
            if value is None:
                return None
            return float(value)
        except (TypeError, ValueError):
            return None

    def _extract_tool_payload(self, tool_result: ToolResult) -> Dict[str, Any]:
        """Extract structured payload from ToolResult (meta/raw JSON fallback)."""
        payload: Dict[str, Any] = {}
        try:
            if isinstance(tool_result.meta, dict):
                raw_payload = tool_result.meta.get("raw_payload")
                if isinstance(raw_payload, dict):
                    payload = dict(raw_payload)
                elif isinstance(tool_result.meta, dict):
                    payload = {
                        k: v
                        for k, v in tool_result.meta.items()
                        if k not in {"success", "error", "message", "content", "results"}
                    }
            if payload:
                return payload
        except Exception:
            pass

        text = (tool_result.text or tool_result.message or "").strip()
        if text:
            try:
                decoded = json.loads(text)
                if isinstance(decoded, dict):
                    return decoded
            except (json.JSONDecodeError, TypeError, ValueError):
                pass
        return {}

    def _build_finance_fact_sections(self, results: List[ToolResult]) -> List[str]:
        """Build canonical, deterministic finance fact sections from tool payloads."""
        finance_results = [r for r in results if (r.tool or "").startswith("finance_") and r.success]
        if not finance_results:
            return []

        sections: List[str] = []
        for result in finance_results:
            payload = self._extract_tool_payload(result)
            if not payload:
                continue

            tool_name = result.tool
            if tool_name == "finance_monthly_report":
                month = payload.get("month", "unbekannt")
                income = self._coerce_float(payload.get("income"))
                expense = self._coerce_float(payload.get("expense"))
                net = self._coerce_float(payload.get("net"))
                tx_count = payload.get("tx_count", "?")

                section = ["Finanzreport (verifiziert)"]
                section.append(f"- Monat: {month}")
                if income is not None:
                    section.append(f"- Einnahmen: {income:.2f} CHF")
                if expense is not None:
                    section.append(f"- Ausgaben: {abs(expense):.2f} CHF (signiert: {expense:.2f})")
                if net is not None:
                    section.append(f"- Netto: {net:.2f} CHF")
                section.append(f"- Buchungen: {tx_count}")
                sections.append("\n".join(section))
                continue

            if tool_name == "finance_aggregate":
                groups = payload.get("groups")
                if not isinstance(groups, list):
                    continue

                group_by = payload.get("group_by", "group")
                totals_income = 0.0
                totals_expense = 0.0
                totals_net = 0.0
                totals_count = 0
                normalized_groups: List[Dict[str, Any]] = []

                for g in groups:
                    if not isinstance(g, dict):
                        continue
                    income = self._coerce_float(g.get("income")) or 0.0
                    expense = self._coerce_float(g.get("expense")) or 0.0
                    net = self._coerce_float(g.get("net")) or 0.0
                    count_val = g.get("count")
                    try:
                        count = int(count_val) if count_val is not None else 0
                    except (TypeError, ValueError):
                        count = 0

                    totals_income += income
                    totals_expense += expense
                    totals_net += net
                    totals_count += count
                    normalized_groups.append(
                        {
                            "key": str(g.get("key", "(leer)")),
                            "income": income,
                            "expense": expense,
                            "net": net,
                            "count": count,
                        }
                    )

                section = ["Finanzaggregation (verifiziert)"]
                section.append(f"- Gruppierung: {group_by}")
                section.append(f"- Gruppen: {len(normalized_groups)}")
                section.append(f"- Gesamteinnahmen: {totals_income:.2f} CHF")
                section.append(f"- Gesamtausgaben: {abs(totals_expense):.2f} CHF (signiert: {totals_expense:.2f})")
                section.append(f"- Gesamtnetto: {totals_net:.2f} CHF")
                section.append(f"- Gesamtanzahl Buchungen: {totals_count}")

                if normalized_groups:
                    top_groups = sorted(normalized_groups, key=lambda x: abs(x["net"]), reverse=True)[:5]
                    section.append("- Top-Gruppen nach |Netto|:")
                    for grp in top_groups:
                        section.append(
                            f"  - {grp['key']}: income={grp['income']:.2f}, expense={grp['expense']:.2f}, net={grp['net']:.2f}, count={grp['count']}"
                        )
                sections.append("\n".join(section))
                continue

            if tool_name == "finance_sum_counterparty_costs":
                counterparty = payload.get("counterparty", "")
                expense = self._coerce_float(payload.get("expense"))
                refunds = self._coerce_float(payload.get("refunds"))
                net = self._coerce_float(payload.get("net"))
                tx_count = payload.get("tx_count", "?")
                section = ["Gegenparteien-Kosten (verifiziert)"]
                if counterparty:
                    section.append(f"- Filter: {counterparty}")
                if expense is not None:
                    section.append(f"- Ausgaben: {abs(expense):.2f} CHF")
                if refunds is not None:
                    section.append(f"- Rueckerstattungen: {refunds:.2f} CHF")
                if net is not None:
                    section.append(f"- Netto: {net:.2f} CHF")
                section.append(f"- Buchungen: {tx_count}")
                sections.append("\n".join(section))
                continue

            compact_payload = dict(payload)
            compact_payload.pop("success", None)
            if compact_payload:
                try:
                    payload_text = json.dumps(compact_payload, ensure_ascii=False, default=str)
                except (TypeError, ValueError):
                    payload_text = str(compact_payload)
                sections.append(f"{tool_name} (verifiziert)\n- Payload: {payload_text}")

        return sections

    def _build_finance_grounding_block(self, results: List[ToolResult]) -> Optional[str]:
        sections = self._build_finance_fact_sections(results)
        if not sections:
            return None
        sections.append("Hinweis: Diese Werte stammen direkt aus den Finance-Tool-Payloads (deterministisch, ohne freie LLM-Arithmetik).")
        return "\n\n".join(sections)

    def _merge_with_finance_grounding(self, answer_text: str, finance_grounding_block: Optional[str]) -> str:
        if not finance_grounding_block:
            return answer_text
        base = (answer_text or "").strip()
        if not base:
            return finance_grounding_block
        return (
            f"{base}\n\n"
            "---\n"
            "Verifizierte Finanzdaten\n"
            f"{finance_grounding_block}"
        )

    def _try_build_deterministic_finance_answer(self, results: List[ToolResult]) -> Optional[str]:
        """Build a deterministic finance response from tool payloads without LLM arithmetic."""
        return self._build_finance_grounding_block(results)

    def _execute_tools_with_rag_postprocessing(
        self,
        query: str,
        planned_calls: List[ToolCall],
        trace: AgentTrace,
        skip_web_search: bool,
        rag_first_results: Optional[List[ToolResult]],
        rag_result_count: int,
        rag_max_score: float,
    ) -> Tuple[List[ToolCall], List[ToolResult], Optional[str], Optional[FinalAnswer]]:
        """Run planned tools, apply RAG fusion/follow-up retrieval, and return optional deterministic early answer."""
        if skip_web_search:
            original_calls = planned_calls.copy()
            planned_calls = [c for c in planned_calls if c.tool != "web_search"]
            logger.info(f"🔄 Modified tool plan: {[c.tool for c in original_calls]} → {[c.tool for c in planned_calls]}")

        t_tools0 = time.perf_counter()

        tools_to_execute = [c.tool for c in planned_calls]
        logger.info("🛠️" + "=" * 68)
        logger.info(f"🛠️ TOOL EXECUTION: {len(planned_calls)} Tools werden ausgeführt")
        logger.info(f"🛠️ Tools: {', '.join(tools_to_execute)}")
        if 'web_search' in tools_to_execute:
            logger.info("🌐 ✅ INTERNET-RECHERCHE WIRD DURCHGEFÜHRT")
        else:
            logger.info("🌐 ❌ KEINE INTERNET-RECHERCHE (nur lokale Daten)")
        logger.info("🛠️" + "=" * 68)

        results: List[ToolResult] = self.tools.run(planned_calls)
        finance_grounding_block = self._build_finance_grounding_block(results)

        graphics = self._collect_graphics(results)
        files = self._collect_files(results)
        graphics_only = bool(results) and all(
            result.tool in {"create_diagram", "canvas", "code_executor"}
            for result in results
        )
        if graphics_only and (graphics or files) and all(result.success for result in results):
            trace.tools_ms = int((time.perf_counter() - t_tools0) * 1000)
            trace.ran_tools = [result.tool for result in results]
            trace.tool_summaries = [f"{result.tool}: ok" for result in results]
            messages = [
                (result.message or result.text or "").strip()
                for result in results
                if (result.message or result.text or "").strip()
            ]
            final_text = "\n\n".join(messages) or "Die Visualisierung wurde lokal erstellt."
            return planned_calls, results, finance_grounding_block, FinalAnswer(
                text=final_text,
                sources=[],
                trace=trace,
                followup_questions=[],
                graphics=graphics,
                files=files,
            )

        only_finance_tools = bool(results) and all((r.tool or "").startswith("finance_") for r in results)
        if only_finance_tools:
            deterministic_finance_text = self._try_build_deterministic_finance_answer(results)
            if deterministic_finance_text:
                trace.tools_ms = int((time.perf_counter() - t_tools0) * 1000)
                try:
                    trace.ran_tools = [r.tool for r in results]
                except Exception:
                    trace.ran_tools = []
                trace.tool_summaries = [
                    f"{r.tool}: {'ok' if r.success else 'error'}" + (f" ({r.error})" if r.error else "")
                    for r in results
                ]
                trace.extras_count = 0
                trace.summarizer_draft_chars = len(deterministic_finance_text)
                trace.verifier_changed = False
                final_text, followups = self._finalize_answer(
                    query,
                    deterministic_finance_text,
                    [],
                    include_citations=False,
                    append_sources=False,
                )
                early_answer = FinalAnswer(text=final_text, sources=[], trace=trace, followup_questions=followups)
                return planned_calls, results, finance_grounding_block, early_answer

        if skip_web_search and rag_first_results:
            results.extend(rag_first_results)
            logger.info(f"✅ Using RAG-First results only ({len(rag_first_results)} result sets)")
        elif not skip_web_search and rag_first_results and rag_result_count > 0:
            if rag_max_score >= 0.60:
                results.extend(rag_first_results)
                logger.info("=" * 70)
                logger.info("🔀 HYBRID SOURCE FUSION: RAG + Web Ergebnisse kombiniert")
                logger.info(f"   ├─ Web Results: {sum(1 for r in results if r.tool == 'web_search')}")
                logger.info(f"   ├─ RAG Results: {rag_result_count} (max_score={rag_max_score:.2f})")
                logger.info(f"   └─ → Hybrid-Antwort mit beiden Quellentypen")
                logger.info("=" * 70)
            else:
                logger.debug(
                    f"RAG-First results discarded (score {rag_max_score:.2f} < 0.60 threshold)"
                )

        try:
            from agent.date_validator import validate_web_search_results

            web_results = [r for r in results if r.tool == "web_search" and r.success and r.results]
            if web_results and web_results[0].results is not None:
                validation = validate_web_search_results(
                    results=web_results[0].results,
                    query=query,
                    model_loader=self.model_loader,
                )

                if validation['has_warnings']:
                    logger.warning("🗓️" + "=" * 68)
                    logger.warning("🗓️ DATE VALIDATION: Möglicherweise veraltete Daten!")
                    for warning in validation['warnings'][:3]:
                        logger.warning(f"🗓️   {warning}")
                    logger.warning("🗓️" + "=" * 68)

                    if validation['warning_message']:
                        trace.date_validation_warning = validation['warning_message']

                    if validation.get('scores'):
                        avg_score = sum(s.get('final_score', 0.5) for s in validation['scores']) / len(validation['scores'])
                        logger.info(f"📊 Durchschnittlicher Relevanz-Score: {avg_score:.2f}")
        except ImportError:
            logger.debug("Date Validator nicht verfügbar")
        except Exception as e:
            logger.warning(f"Date Validation Fehler: {e}")
            import traceback
            logger.debug(traceback.format_exc())

        web_search_executed = False
        web_search_result_count = 0
        for result in results:
            if result.tool == "web_search":
                web_search_executed = True
                if result.success and result.results:
                    web_search_result_count = len(result.results)
                    logger.info("🌐" + "=" * 68)
                    logger.info("🌐 ✅ INTERNET-RECHERCHE ERFOLGREICH!")
                    logger.info(f"🌐    Gefundene Web-Ergebnisse: {web_search_result_count}")
                    logger.info("🌐" + "=" * 68)
                else:
                    logger.warning("🌐" + "=" * 68)
                    logger.warning("🌐 ⚠️ INTERNET-RECHERCHE FEHLGESCHLAGEN")
                    logger.warning(f"🌐    Fehler: {result.error if hasattr(result, 'error') else 'Unbekannt'}")
                    logger.warning("🌐" + "=" * 68)
                break

        if not web_search_executed:
            logger.info("🌐" + "=" * 68)
            logger.info("🌐 ℹ️ KEINE INTERNET-RECHERCHE DURCHGEFÜHRT")
            logger.info("🌐    (RAG-First entschied: lokale Daten ausreichend)")
            logger.info("🌐" + "=" * 68)

        if self.rag_enabled and self.rag_persist_from_web:
            for result in results:
                if result.tool == "web_search" and result.success and result.results:
                    try:
                        logger.info("🌐" + "=" * 68)
                        logger.info(f"🌐 WEB-ZU-RAG PERSISTIERUNG (async, off-critical-path)")
                        logger.info(f"   ├─ Web-Ergebnisse: {len(result.results)}")
                        logger.info(f"   ├─ Aktion: Full-Content-Extraction + RAG-Speicherung im Hintergrund")
                        logger.info(f"   └─ User-Antwort wird NICHT mehr blockiert.")
                        logger.info("🌐" + "=" * 68)
                        self._submit_persist_web_to_rag(result.results)
                    except AttributeError as e:
                        logger.debug(f"Web-zu-RAG-Persist nicht verfügbar: {e}")
                    except Exception as e:
                        logger.warning(f"Web-zu-RAG-Persist fehlgeschlagen: {type(e).__name__}: {e}")

        is_time_critical = self._is_time_critical_query(query)
        web_results_available = any(r.tool == "web_search" and r.results for r in results)

        logger.info("🔄" + "=" * 68)
        logger.info(f"🔄 RAG-STATUS-CHECK:")
        logger.info(f"   ├─ RAG aktiviert: {self.rag_enabled}")
        logger.info(f"   ├─ Multiquery aktiviert: {self.multiquery_enabled}")
        logger.info(f"   ├─ Web-zu-RAG Persistierung: {self.rag_persist_from_web}")
        logger.info(f"   ├─ Zeitkritische Query: {is_time_critical}")
        logger.info(f"   └─ Web-Ergebnisse vorhanden: {web_results_available}")
        logger.info("🔄" + "=" * 68)

        if self.rag_enabled and not skip_web_search:
            effective_k, strategy_result = self._compute_effective_k_with_strategy(query)
            subqs = self._generate_subqueries(query) if self.multiquery_enabled else []

            if self.multiquery_enabled:
                logger.info(f"✅ DecompositionEngine produced {len(subqs)} sub-queries")
                try:
                    trace.subqueries = list(subqs or [])
                except AttributeError as e:
                    logger.debug(f"Trace-Subqueries-Attributfehler: {e}")
                except Exception as e:
                    logger.warning(f"Unerwarteter Fehler beim Setzen der Trace-Subqueries: {type(e).__name__}: {e}")

            logger.info(f"🎯 RAG with Dynamic-K: effective_k={effective_k} (gui_max={self.rag_k})")

            if self.subquery_gap_detection_enabled:
                logger.info("✅ Using RAG with Sub-Query Gap-Detection")
                rag_results = self.rag_manager.execute_rag_with_gap_detection(
                    query=query,
                    k=effective_k,
                    min_score=self.rag_min_score,
                    multiquery_enabled=self.multiquery_enabled,
                    mq_n=self.mq_n,
                    mq_k=self.mq_k,
                    sub_queries=subqs,
                    is_time_critical=is_time_critical,
                    web_results_available=web_results_available,
                    min_results_threshold=self.subquery_min_results_threshold,
                    enable_web_fallback=self.subquery_web_fallback_enabled,
                    persist_to_rag=self.rag_persist_from_web,
                )
            else:
                logger.info("Using standard RAG (Gap-Detection disabled)")
                rag_results = self.rag_manager.execute_rag_with_multiquery(
                    query=query,
                    k=effective_k,
                    min_score=self.rag_min_score,
                    multiquery_enabled=self.multiquery_enabled,
                    mq_n=self.mq_n,
                    mq_k=self.mq_k,
                    sub_queries=subqs,
                    is_time_critical=is_time_critical,
                    web_results_available=web_results_available,
                )

            results.extend(rag_results)
        elif skip_web_search:
            logger.info("✅ Skipping redundant RAG execution (already used RAG-First results)")
        else:
            logger.info("RAG deaktiviert - keine lokale Suche wird durchgeführt")

        rag_result_count = sum(1 for r in results if r.tool == "rag_search")
        web_result_count = sum(1 for r in results if r.tool == "web_search")

        logger.info("=" * 70)
        logger.info("📊 TOOL-EXECUTION SUMMARY:")
        logger.info(f"   ├─ Web-Searches durchgeführt: {web_result_count}")
        logger.info(f"   ├─ RAG-Searches durchgeführt: {rag_result_count}")

        if rag_result_count > 0:
            total_rag_results = sum(len(r.results or []) for r in results if r.tool == "rag_search")
            logger.info(f"   ├─ RAG-Ergebnisse gesamt: {total_rag_results}")
            logger.info(f"   └─ ✅ RAG wird für Antwort verwendet: JA")
        else:
            logger.info(f"   └─ ❌ RAG wird für Antwort verwendet: NEIN")

        logger.info("=" * 70)

        trace.tools_ms = int((time.perf_counter() - t_tools0) * 1000)
        try:
            trace.ran_tools = [r.tool for r in results]
        except AttributeError as e:
            logger.debug(f"Tool-Recording-Attributfehler: {e}")
            trace.ran_tools = []
        except Exception as e:
            logger.warning(f"Unerwarteter Fehler beim Tool-Recording: {type(e).__name__}: {e}")
            trace.ran_tools = []

        return planned_calls, results, finance_grounding_block, None

    def _build_tool_summaries_and_trace_artifacts(
        self,
        query: str,
        results: List[ToolResult],
        reasoning: Optional[str],
        finance_grounding_block: Optional[str],
        trace: AgentTrace,
    ) -> List[str]:
        """Build extras/summaries and populate detailed trace artifacts from tool results."""
        extras: List[str] = []
        tool_summaries: List[str] = []
        for r in results:
            if r.tool == "web_search":
                cnt = len(r.results or [])
                err = f"; Fehler: {r.error}" if r.error else ""
                tool_summaries.append(f"web_search: {cnt} Ergebnisse{err}")
            elif r.tool == "rag_search":
                cnt = len(r.results or [])
                err = f"; Fehler: {r.error}" if r.error else ""
                tool_summaries.append(f"rag_search: {cnt} Treffer{err}")
            elif (r.tool or "").startswith("finance_"):
                status = "ok" if r.success else "error"
                err = f"; Fehler: {r.error}" if r.error else ""
                tool_summaries.append(f"{r.tool}: {status}{err}")
            else:
                summary = (r.message or r.text or "").strip()
                if len(summary) > 140:
                    summary = summary[:137] + "…"
                tool_summaries.append(f"{r.tool}: {summary}" if summary else r.tool)
                if r.tool not in {"web_search", "rag_search"} and (r.text or r.message):
                    extras.append((r.text or r.message or "").strip())

        if reasoning:
            extras.append(f"[Analytische CoT/ToT Vorüberlegung vom Planner]\n{reasoning.strip()}")
        if finance_grounding_block:
            extras.append(f"[Verifizierte Finanzdaten]\n{finance_grounding_block}")

        try:
            if self.multiquery_enabled:
                subquery_count = len(trace.subqueries or [])
                tool_summaries.append(f"multiquery: {subquery_count} Teilfragen")
        except AttributeError as e:
            logger.debug(f"Trace-Subqueries-Attributfehler beim Summary: {e}")
        except Exception as e:
            logger.warning(f"Unerwarteter Fehler beim Multiquery-Summary: {type(e).__name__}: {e}")
            if self.multiquery_enabled:
                tool_summaries.append("multiquery: Fehler beim Zählen der Teilfragen")

        trace.tool_summaries = tool_summaries
        try:
            tool_results = {}
            subquery_counter = 0
            for r in results:
                if r.tool in {"web_search", "rag_search"}:
                    actual_query = query
                    tool_suffix = ""

                    if r.tool == "rag_search" and self.multiquery_enabled and hasattr(trace, 'subqueries'):
                        subqueries = getattr(trace, 'subqueries', [])
                        if subquery_counter == 0:
                            actual_query = query
                            tool_suffix = "_original"
                        elif subquery_counter <= len(subqueries):
                            actual_query = subqueries[subquery_counter - 1]
                            tool_suffix = f"_subquery_{subquery_counter}"
                        subquery_counter += 1

                    tool_data: Dict[str, Any] = {
                        "tool": r.tool,
                        "query": actual_query,
                        "results_count": len(r.results or []),
                        "error": r.error,
                        "results": []
                    }
                    if r.results:
                        for result in r.results[:10]:
                            if hasattr(result, 'title') and hasattr(result, 'url'):
                                snippet = getattr(result, 'snippet', '') or ''
                                tool_data["results"].append({
                                    "title": getattr(result, 'title', '') or '',
                                    "url": getattr(result, 'url', '') or '',
                                    "snippet": snippet[:200]
                                })
                            elif hasattr(result, 'content'):
                                content = getattr(result, 'content', '') or ''
                                tool_data["results"].append({
                                    "source": getattr(result, 'source', '') or '',
                                    "content": content[:300],
                                    "score": getattr(result, 'score', 0.0)
                                })
                            else:
                                tool_data["results"].append({
                                    "data": str(result)[:200]
                                })

                    key = f"{r.tool}{tool_suffix}"
                    counter = 1
                    while key in tool_results:
                        counter += 1
                        key = f"{r.tool}{tool_suffix}_{counter}"
                    tool_results[key] = tool_data

            trace.tool_results = tool_results
        except AttributeError as e:
            logger.debug(f"Trace-Tool-Results-Attributfehler: {e}")
            trace.tool_results = {}
        except Exception as e:
            logger.warning(f"Fehler beim Sammeln der Tool-Ergebnisse: {type(e).__name__}: {e}")
            import traceback
            logger.debug(f"Tool-Results-Collection-Fehler Traceback:\n{traceback.format_exc()}")
            trace.tool_results = {}

        trace.extras_count = len(extras)
        return extras

    def _select_and_enrich_evidence(
        self,
        query: str,
        results: List[ToolResult],
        history: List[Dict[str, Any]],
        trace: AgentTrace,
    ) -> Tuple[List[Source], Any]:
        """Run evidence selection, optional enhanced retrieval, and trace/summary updates."""
        evidence_result = self.evidence_manager.select_evidence_from_tool_results(
            query=query,
            tool_results=results,
            evidence_max_candidates=self.evidence_max_candidates,
            evidence_shortlist_m=self.evidence_shortlist_m,
            evidence_diversity_lambda=self.evidence_diversity_lambda,
            is_news_query=self._is_news_query(query),
            news_min_k=self.news_min_k,
            news_max_k=self.news_max_k,
            model_loader=self.model_loader,
            use_llm_evidence_selection=self.use_llm_evidence_selection,
            validation_enabled=True,
            validation_max_iterations=2,
            validation_min_sources=3
        )

        sources: List[Source] = evidence_result.sources

        if len(sources) < 2:
            try:
                enhanced_sources, enhanced_stats = self._rag_enhanced(
                    query=query,
                    history=history,
                    extras=[],
                )
                if enhanced_sources:
                    sources = self._deduplicate_sources(sources + enhanced_sources)
                    logger.info(
                        "Enhanced retrieval added %d sources in orchestrate (route=%s)",
                        len(enhanced_sources),
                        enhanced_stats.get("route"),
                    )
            except Exception as exc:
                logger.debug("Enhanced retrieval skipped in orchestrate: %s", exc)

        try:
            trace.source_validation = evidence_result.validation_stats
        except AttributeError as e:
            logger.debug(f"Trace-Source-Validation-Attributfehler: {e}")
        except Exception as e:
            logger.warning(f"Fehler beim Setzen der Source-Validation-Stats: {type(e).__name__}: {e}")

        web_sources = evidence_result.web_sources_count
        rag_sources = evidence_result.rag_sources_count

        logger.info("📚" + "=" * 68)
        logger.info("📚 EVIDENCE SELECTION SUMMARY:")
        logger.info(f"   ├─ Kandidaten: {evidence_result.candidates_count}")
        logger.info(f"   ├─ Nach Ranking: {evidence_result.ranked_count}")
        logger.info(f"   ├─ Shortlist: {evidence_result.shortlist_count}")
        logger.info(f"   ├─ Finale Auswahl: {evidence_result.final_count}")
        logger.info(f"   ├─   └─ Web-Quellen: {web_sources}")
        logger.info(f"   └─   └─ RAG-Quellen: {rag_sources}")
        logger.info("📚" + "=" * 68)

        return sources, evidence_result

    def _apply_post_evidence_refinement(
        self,
        query: str,
        sources: List[Source],
        results: List[ToolResult],
        trace: AgentTrace,
    ) -> Tuple[List[Source], bool]:
        """Apply fallback decision, optional IRCoT refinement, and best-effort SOTA enhancement."""
        use_fallback = len(sources) == 0
        if use_fallback:
            trace.heuristic_triggered = True
            trace.heuristic_reason = "Keine Evidenz gefunden – nutze internes Wissen/Logik."

        # IRCoT: Iterative retrieval refinement (only when we have evidence to evaluate)
        if self.ircot_enabled and sources and not use_fallback:
            try:
                sources = self._ircot_loop(
                    query=query,
                    sources=sources,
                    results=results,
                    trace=trace,
                )
                # Update fallback status after IRCoT (sources may have grown)
                use_fallback = len(sources) == 0
            except Exception as e:
                logger.warning("[IRCoT] Loop failed, continuing with original evidence: %s", e, exc_info=True)

        # ─── SOTA Pipeline Enhancement (PHASE 7) ─────────────────────
        # Integrate retrieval-side SOTA components before answer generation.
        # Query expansion happens during retrieval; this hook keeps freshness
        # telemetry from ChangeDetector visible in the main path.
        if not use_fallback:
            try:
                sota_enhancement = self._run_sota_enhancement(query, sources, results, trace)
                if sota_enhancement:
                    # Apply SOTA-suggested source reordering/filtering
                    if "optimized_sources" in sota_enhancement and sota_enhancement["optimized_sources"]:
                        sources = sota_enhancement["optimized_sources"]
                        logger.info(
                            "[SOTA] Sources optimized: %d -> %d sources, "
                            "novelty=%0.3f, quality=%0.3f",
                            len(sota_enhancement.get("original_sources", [])),
                            len(sources),
                            sota_enhancement.get("novelty_score", 0.0),
                            sota_enhancement.get("quality_score", 0.0),
                        )
                    if trace is not None and "metrics" in sota_enhancement:
                        trace.sota_metrics = dict(sota_enhancement["metrics"])
            except Exception as _sota_err:
                logger.warning(
                    "[SOTA] Enhancement failed (non-blocking): %s",
                    _sota_err,
                    exc_info=True,
                )
                # Continue with original sources - SOTA is best-effort

        return sources, use_fallback

    def _populate_source_observability(
        self,
        sources: List[Source],
        trace: AgentTrace,
    ) -> None:
        """Populate source-domain list and RAG store stats for observability."""
        from urllib.parse import urlparse

        trace.evidence_domains = []
        for s in sources:
            try:
                domain = urlparse(s.url).netloc if s.url else "N/A"
                trace.evidence_domains.append(domain)
            except Exception:
                trace.evidence_domains.append("N/A")

        try:
            rag = getattr(self.tools, "rag", None)
            if rag is not None:
                trace.rag_stats = rag.get_stats()
        except AttributeError as e:
            logger.debug(f"RAG-Stats-Attributfehler: {e}")
        except Exception as e:
            logger.warning(f"Fehler beim Abrufen der RAG-Stats: {type(e).__name__}: {e}")

    def _run_rag_first_gating(
        self,
        query: str,
        planned_calls: List[ToolCall],
        initial_skip_web_search: bool,
    ) -> Tuple[bool, Optional[List[ToolResult]], int, float]:
        """Run RAG-first pre-check to decide whether web search can be skipped."""
        has_web_search = any(c.tool == "web_search" for c in planned_calls)
        has_explicit_rag = any(c.tool == "rag_search" for c in planned_calls)

        rag_first_results: Optional[List[ToolResult]] = None
        rag_result_count = 0
        rag_max_score = 0.0
        skip_web_search = initial_skip_web_search

        # NEU: RAG-First bei zeitkritischen Queries deaktivieren
        is_time_critical = self._is_time_critical_query(query)

        if has_web_search and self.rag_enabled and not has_explicit_rag and not is_time_critical:
            logger.info("🔍" + "=" * 68)
            logger.info("🔍 RAG-FIRST OPTIMIZATION: Checking local knowledge before web")
            logger.info("🔍" + "=" * 68)
        elif is_time_critical:
            logger.info("⚡" + "=" * 68)
            logger.info("⚡ RAG-FIRST SKIPPED: Query ist ZEITKRITISCH")
            logger.info("⚡ → Web-Search wird direkt ausgeführt für aktuelle Daten")
            logger.info("⚡" + "=" * 68)

        if has_web_search and self.rag_enabled and not has_explicit_rag and not is_time_critical:

            # Dynamic Context Window: compute effective k + get strategy (reuse for sub-queries)
            effective_k, strategy_result = self._compute_effective_k_with_strategy(query)

            # Generate sub-queries via central DecompositionEngine.
            # Engine-internal complexity gating: SIMPLE/MODERATE → empty
            # list (single-query path), COMPLEX/VERY_COMPLEX → fan-out.
            subqs = self._generate_subqueries(query) if self.multiquery_enabled else []

            # Execute RAG search first WITH GAP-DETECTION (Option A)
            # NEW 2025-01-22: RAG-FIRST nutzt jetzt auch Gap-Detection für Sub-Queries!
            if self.subquery_gap_detection_enabled:
                logger.info("✅ RAG-FIRST: Using Gap-Detection for sub-queries")
                rag_first_results = self.rag_manager.execute_rag_with_gap_detection(
                    query=query,
                    k=effective_k,
                    min_score=self.rag_min_score,
                    multiquery_enabled=self.multiquery_enabled,
                    mq_n=self.mq_n,
                    mq_k=self.mq_k,
                    sub_queries=subqs,
                    is_time_critical=False,  # Not time-critical for this check
                    web_results_available=False,
                    min_results_threshold=self.subquery_min_results_threshold,
                    enable_web_fallback=self.subquery_web_fallback_enabled,
                    persist_to_rag=self.rag_persist_from_web
                )
            else:
                logger.info("RAG-FIRST: Using standard RAG (Gap-Detection disabled)")
                rag_first_results = self.rag_manager.execute_rag_with_multiquery(
                    query=query,
                    k=effective_k,
                    min_score=self.rag_min_score,
                    multiquery_enabled=self.multiquery_enabled,
                    mq_n=self.mq_n,
                    mq_k=self.mq_k,
                    sub_queries=subqs,
                    is_time_critical=False,  # Not time-critical for this check
                    web_results_available=False
                )

            # STEP 3: Analyze RAG results quality
            rag_result_count = 0
            rag_max_score = 0.0
            rag_avg_score = 0.0

            if rag_first_results:
                for rag_result in rag_first_results:
                    if rag_result.success and rag_result.results:
                        rag_result_count += len(rag_result.results)
                        scores = [r.get('score', 0.0) for r in rag_result.results if isinstance(r, dict)]
                        if scores:
                            rag_max_score = max(scores)
                            rag_avg_score = sum(scores) / len(scores)

            # STEP 4: LLM-based decision: Is RAG sufficient?
            # NEU: Höhere Schwelle für RAG-First (10 statt 3) und Score-Check
            if rag_result_count >= 10 and rag_max_score >= 0.75:  # Höhere Qualitäts-Anforderung (>= statt >)
                # Ask LLM: Are these RAG results sufficient?
                # WICHTIG: Striktes Kurzformat statt ToT/CoT-Fließtext, um Truncation
                # und nicht-parsebare Antworten zu vermeiden.
                rag_quality_prompt = f"""Du bist ein strikter Klassifikator für RAG-Abdeckung.

USER-FRAGE: \"{query}\"

RAG-METRIKEN:
- result_count: {rag_result_count}
- max_score: {rag_max_score:.2f}
- avg_score: {rag_avg_score:.2f}

TOP-3 RAG-ERGEBNISSE:
"""
                # Add top 3 result snippets with full content
                top_results = []
                for rag_result in rag_first_results[:1]:
                    if rag_result.success and rag_result.results:
                        top_results = rag_result.results[:3]
                        break

                for i, res in enumerate(top_results, 1):
                    snippet = res.get('content', res.get('snippet', ''))[:1500]  # Erhöht für besseren Kontext
                    metadata = res.get('metadata', {})
                    source_date = metadata.get('date', metadata.get('created_at', 'unbekannt'))
                    source_url = metadata.get('url', metadata.get('source', ''))

                    rag_quality_prompt += f"\n{i}. {snippet}..."
                    if source_date != 'unbekannt':
                        rag_quality_prompt += f"\n   Datum: {source_date}"
                    if source_url:
                        rag_quality_prompt += f"\n   Quelle: {source_url}"

                is_time_critical = self._is_time_critical_query(query)
                rag_quality_prompt += f"""

ZEITKRITISCHKEIT: {'JA' if is_time_critical else 'NEIN'}

ENTSCHEIDUNGSREGEL:
- INSUFFICIENT wenn wesentliche Teilaspekte der Frage in den Top-3 Ergebnissen fehlen
- INSUFFICIENT bei zeitkritischer Frage ohne klar aktuelle Evidenz
- sonst SUFFICIENT

ANTWORTFORMAT (STRICT):
Gib EXAKT EIN WORT zurück, ohne Satzzeichen, ohne Begründung:
SUFFICIENT
oder
INSUFFICIENT
"""

                try:
                    decision_grammar = get_rag_sufficiency_grammar()
                    rag_quality_response = self.model_loader.generate_response(
                        messages=[
                            {"role": "system", "content": "Du bist ein binärer Klassifikator. Antworte strikt mit genau einem Wort: SUFFICIENT oder INSUFFICIENT."},
                            {"role": "user", "content": rag_quality_prompt}
                        ],
                        max_tokens=16,
                        temperature=0.0,
                        stop=["\n", "\r"],
                        grammar=decision_grammar,
                    )

                    decision_raw = str(rag_quality_response).strip().upper()
                    decision_norm = decision_raw.replace("\n", "").replace("\r", "").strip()

                    # Primärpfad: Grammar erzwingt eines der beiden Labels.
                    if decision_norm in {"SUFFICIENT", "INSUFFICIENT"}:
                        decision = decision_norm
                    else:
                        # Sollte unter Grammar nicht auftreten; falls Grammar backend
                        # unavailable ist, fällt das Modell auf Free-Decoding zurück.
                        logger.warning(
                            "RAG-First Gating: Non-conformant output (grammar unavailable or bypassed). "
                            "Using deterministic score fallback. Response (%d chars): %r",
                            len(decision_raw),
                            decision_raw[:150],
                        )
                        if rag_max_score >= 0.85 and rag_avg_score >= 0.70:
                            decision = "SUFFICIENT"
                            logger.info("RAG-First Gating fallback: score heuristic -> SUFFICIENT")
                        else:
                            decision = "INSUFFICIENT"
                            logger.info("RAG-First Gating fallback: score heuristic -> INSUFFICIENT")

                    if decision == "SUFFICIENT":
                        skip_web_search = True
                        logger.info("=" * 70)
                        logger.info("✅ RAG-FIRST DECISION: RAG results SUFFICIENT")
                        logger.info(f"   ├─ Results: {rag_result_count}")
                        logger.info(f"   ├─ Max Score: {rag_max_score:.2f}")
                        logger.info(f"   ├─ Avg Score: {rag_avg_score:.2f}")
                        logger.info(f"   ├─ LLM Raw: {decision_raw[:80]!r}")
                        logger.info(f"   └─ ⚡ SKIPPING WEB SEARCH (saves 15-30s!)")
                        logger.info("=" * 70)
                    else:
                        logger.info("=" * 70)
                        logger.info("⚠️ RAG-FIRST DECISION: RAG results INSUFFICIENT")
                        logger.info(f"   ├─ Results: {rag_result_count}")
                        logger.info(f"   ├─ Max Score: {rag_max_score:.2f}")
                        logger.info(f"   ├─ LLM Decision: {decision}")
                        logger.info(f"   ├─ LLM Raw: {decision_raw[:80]!r}")
                        logger.info(f"   └─ Proceeding with WEB SEARCH")
                        logger.info("=" * 70)

                except Exception as e:
                    logger.warning(f"RAG quality check failed: {e}, defaulting to web search")
                    skip_web_search = False
            else:
                logger.info("=" * 70)
                logger.info(f"⚠️ RAG-FIRST: Qualität unzureichend")
                logger.info(f"   ├─ Results: {rag_result_count} (Mindestens 10 benötigt)")
                logger.info(f"   ├─ Max Score: {rag_max_score:.2f} (Mindestens 0.75 benötigt)")
                logger.info(f"   └─ Proceeding with WEB SEARCH")
                logger.info("=" * 70)

        return skip_web_search, rag_first_results, rag_result_count, rag_max_score

    def _apply_retrieval_route_with_trace(
        self,
        query: str,
        planned_calls: List[ToolCall],
        trace: AgentTrace,
    ) -> Tuple[Any, List[ToolCall]]:
        """Apply retrieval routing and persist routing decision metadata into trace."""
        route_decision = self._decide_retrieval_route(query)
        planned_calls = self._apply_retrieval_route(planned_calls, route_decision)
        trace.source_validation = {
            **(trace.source_validation or {}),
            "retrieval_route": route_decision.route.value,
            "retrieval_route_reason": route_decision.reason,
            "retrieval_route_confidence": route_decision.confidence,
        }
        logger.info(
            "[RetrievalRouter] effective route=%s tools=%s",
            route_decision.route.value,
            [c.tool for c in planned_calls],
        )
        return route_decision, planned_calls

    def _apply_security_guard(
        self,
        query: str,
        trace: AgentTrace,
    ) -> Tuple[str, Optional[FinalAnswer]]:
        """Validate and sanitize input query; optionally return an early blocked response."""
        validation_result = self.security_manager.validate_input(query)

        if not validation_result.is_valid:
            logger.warning(f"🔒 Query validation failed: {validation_result.detected_issues}")
            return query, FinalAnswer(
                text=f"Die Anfrage konnte aus Sicherheitsgründen nicht verarbeitet werden: {'; '.join(validation_result.detected_issues)}",
                sources=[],
                trace=trace,
            )

        if validation_result.sanitized_content != query:
            logger.info(f"🔒 Using sanitized query")
            query = validation_result.sanitized_content

        if validation_result.warnings:
            for warning in validation_result.warnings:
                logger.info(f"⚠️ Security warning: {warning}")

        return query, None

    def _post_generation_housekeeping(
        self,
        sources: List[Source],
        trace: AgentTrace,
        evidence_result: Any,
    ) -> None:
        """Persist selected sources (best-effort), snapshot generation settings, and emit compact trace logs."""
        if self.rag_enabled and self.rag_persist_from_web and sources:
            try:
                self._maybe_persist_sources_to_rag(sources)
            except AttributeError as e:
                logger.debug(f"RAG-Persist-Methode nicht verfügbar: {e}")
            except Exception as e:
                logger.warning(f"Fehler beim Persistieren der Quellen in RAG: {type(e).__name__}: {e}")
                import traceback
                logger.debug(f"RAG-Persist-Fehler Traceback:\n{traceback.format_exc()}")

        trace.planner_temp = 0.2
        trace.planner_max_tokens = self.planner_max_tokens
        trace.summarizer_temp = 0.2
        trace.verifier_temp = 0.0
        trace.summarizer_max_tokens = self.summarizer_max_tokens
        trace.verifier_max_tokens = self.verifier_max_tokens

        try:
            logger.debug(
                "orchestrate_done | tools=%s ran=%s cand=%d shortlist=%d K=%d sources=%d domains=%s sum_ms=%d ver_ms=%d",
                trace.planned_tools,
                getattr(trace, "ran_tools", []),
                evidence_result.candidates_count,
                evidence_result.shortlist_count,
                evidence_result.final_count,
                len(sources),
                trace.evidence_domains,
                trace.summarize_ms,
                trace.verify_ms,
            )
        except AttributeError as e:
            logger.debug(f"Trace-Logging-Attributfehler: {e}")
        except Exception as e:
            logger.warning(f"Fehler beim Debug-Logging des Orchestrate-Done: {type(e).__name__}: {e}")

    def _apply_hybrid_reasoning_validation(
        self,
        query: str,
        history: List[Dict[str, Any]],
        sources: List[Source],
        extras: List[str],
        final_text: str,
    ) -> str:
        """Run hybrid reasoning validation/re-synthesis and return validated answer text."""
        try:
            logger.info("🧠" + "=" * 68)
            logger.info("🧠 HYBRID REASONING AKTIVIERT")
            logger.info("🧠" + "=" * 68)

            # Cross-Encoder Referenz aktualisieren falls lazy-initialized
            if self.hybrid_reasoning.cross_encoder is None:
                _ce = getattr(self.evidence_processor, 'cross_encoder', None)
                if _ce is not None:
                    self.hybrid_reasoning.cross_encoder = _ce
                    logger.info("🧠 Cross-Encoder Referenz aktualisiert (lazy-init)")

            # Konvertiere Sources zu Evidence-Format
            all_evidences = []
            for src in sources:
                # Reihenfolge: content > snippet > text > title (Fallback-Kette)
                ev_content = (
                    getattr(src, 'content', None)
                    or getattr(src, 'snippet', None)
                    or getattr(src, 'text', None)
                    or getattr(src, 'title', None)
                    or ''
                )
                all_evidences.append(Evidence(
                    content=ev_content,
                    source=getattr(src, 'url', 'unknown'),
                    score=getattr(src, 'score', 0.0),
                    domain=self.evidence_processor._domain_of(getattr(src, 'url', ''))
                ))

            ev_with_content = sum(1 for ev in all_evidences if ev.content)
            logger.info(f"🧠 Konvertiert: {len(sources)} Sources → {len(all_evidences)} Evidences ({ev_with_content} mit Content)")

            # Hybrid-Komponente 1: Meta-Orchestration
            strategy = self.hybrid_reasoning.meta_orchestrate(query, intent="factual")
            logger.info(f"🧠 Meta-Orchestration: Strategie = {strategy}")

            # Hybrid-Komponente 2: Cross-Encoder Reranking + Evidence-Optimization
            logger.info(f"🎯 Starte Cross-Encoder Reranking für {len(all_evidences)} Evidences...")
            optimized_evidences_raw = self.evidence_processor.rank_and_optimize(
                evidences=all_evidences,
                query=query,
                target_m=12,
                diversity_lambda=0.7,
                use_cross_encoder=True
            )

            # Sicherstellen dass alle im Evidence-Format sind
            optimized_evidences = []
            for ev in optimized_evidences_raw:
                if hasattr(ev, 'content') and hasattr(ev, 'score'):
                    optimized_evidences.append(ev)
                else:
                    ev_content = getattr(ev, 'content', None) or getattr(ev, 'snippet', None) or getattr(ev, 'text', '') or ''
                    optimized_evidences.append(Evidence(
                        content=ev_content,
                        source=getattr(ev, 'url', 'unknown'),
                        score=getattr(ev, 'score', 0.0),
                        domain=self.evidence_processor._domain_of(getattr(ev, 'url', ''))
                    ))

            logger.info(f"🧠 Cross-Encoder Reranking: {len(all_evidences)} → {len(optimized_evidences)} Evidences")

            # Hybrid-Komponente 3: Answer-Validation (SOTA: Cross-Encoder Semantic Grounding)
            # WICHTIG: Validierung auf UNFORMATIERTEM final_text (ohne Citations/Quellen-Block)
            validation = self.hybrid_reasoning.validate_answer(final_text, optimized_evidences, query)
            logger.info(f"🧠 Validation: {validation}")

            # Bei Validierungsfehler: Echte Re-Synthese (vollständiger Pipeline-Schritt)
            if not validation["passed"]:
                logger.warning("🧠 ⚠️ Validierung fehlgeschlagen, starte Re-Synthese")
                regenerated_answer = self.hybrid_reasoning.regenerate_with_full_pipeline(
                    query=query,
                    history=history,
                    sources=sources,
                    extras=extras,
                    evidences=optimized_evidences
                )
                if regenerated_answer:
                    # Re-Validierung
                    validation = self.hybrid_reasoning.validate_answer(
                        regenerated_answer, optimized_evidences, query
                    )
                    if validation["passed"]:
                        final_text = regenerated_answer
                        logger.info("🧠 ✅ Re-Synthese erfolgreich validiert")
                    else:
                        logger.warning(
                            f"🧠 ⚠️ Auch Re-Synthese nicht bestanden "
                            f"(grounding={validation['grounding_ratio']:.2f}), behalte Original"
                        )
                        # SOTA: Selective Answering / Abstain.
                        # Wenn auch die Re-Synthese unter dem Grounding-Threshold liegt,
                        # signalisieren wir das ehrlich an den Nutzer statt eine
                        # potentiell halluzinierte Antwort als verifiziert auszugeben.
                        # Sentinel-Wert -1.0 ("not applicable") wird ausgenommen.
                        gr = validation.get("grounding_ratio", 0.0)
                        if 0.0 <= gr < 0.15:
                            final_text = (
                                "⚠️ **Niedrige Beleg-Abdeckung** "
                                f"(grounding_ratio={gr:.2f}): Die folgende Antwort konnte "
                                "durch die verfügbaren Quellen nicht zuverlässig verankert werden. "
                                "Bitte mit Vorsicht behandeln und ggf. zusätzliche Quellen prüfen.\n\n"
                                + (final_text or "")
                            )
                else:
                    logger.warning("🧠 ⚠️ Re-Synthese fehlgeschlagen, behalte Original")

            # Metriken loggen
            self.hybrid_reasoning.log_quality_metrics(validation, optimized_evidences)

            logger.info("🧠" + "=" * 68)

        except Exception as e:
            # SOTA: Fail-fast. Silent fallback auf nicht-validierten Original-Antworten
            # versteckt Qualitätsverlust (Halluzinationen, ungroundete Behauptungen)
            # und widerspricht dem Antwort-Pipeline-Vertrag.
            logger.error(
                f"🧠 ❌ Hybrid Reasoning fehlgeschlagen: {type(e).__name__}: {e}",
                exc_info=True,
            )
            raise RuntimeError(
                f"Hybrid reasoning pipeline failed: {e}"
            ) from e

        return final_text

    def _apply_no_tools_hybrid_reranking(
        self,
        query: str,
        sources: List[Source],
    ) -> List[Source]:
        """Apply hybrid reranking in no-tools mode and return the (possibly) reranked sources."""
        try:
            logger.info("🧠" + "=" * 68)
            logger.info("🧠 HYBRID REASONING (NO-TOOLS MODE)")
            logger.info("🧠" + "=" * 68)

            # Konvertiere Sources zu Evidence-Format
            all_evidences = []
            for src in sources:
                # Reihenfolge: content > snippet > text > title (Fallback-Kette)
                ev_content = (
                    getattr(src, 'content', None)
                    or getattr(src, 'snippet', None)
                    or getattr(src, 'text', None)
                    or getattr(src, 'title', None)
                    or ''
                )
                all_evidences.append(Evidence(
                    content=ev_content,
                    source=getattr(src, 'url', 'unknown'),
                    score=getattr(src, 'score', 0.0),
                    domain=self.evidence_processor._domain_of(getattr(src, 'url', ''))
                ))

            ev_with_content = sum(1 for ev in all_evidences if ev.content)
            logger.info(f"🧠 Konvertiert: {len(sources)} Sources → {len(all_evidences)} Evidences ({ev_with_content} mit Content)")

            # Cross-Encoder Reranking + Evidence-Optimization
            logger.info(f"🎯 Starte Cross-Encoder Reranking für {len(all_evidences)} Evidences...")

            optimized_evidences_raw = self.evidence_processor.rank_and_optimize(
                evidences=all_evidences,
                query=query,
                target_m=12,
                diversity_lambda=0.7,
                use_cross_encoder=True
            )

            # Sicherstellen dass alle im Evidence-Format sind
            optimized_evidences = []
            for ev in optimized_evidences_raw:
                if hasattr(ev, 'content') and hasattr(ev, 'score'):
                    # Bereits Evidence-Objekt
                    optimized_evidences.append(ev)
                else:
                    # Konvertiere zu Evidence
                    ev_content = getattr(ev, 'content', None) or getattr(ev, 'snippet', None) or getattr(ev, 'text', '') or ''
                    optimized_evidences.append(Evidence(
                        content=ev_content,
                        source=getattr(ev, 'url', 'unknown'),
                        score=getattr(ev, 'score', 0.0),
                        domain=self.evidence_processor._domain_of(getattr(ev, 'url', ''))
                    ))

            logger.info(f"🧠 Cross-Encoder Reranking: {len(all_evidences)} → {len(optimized_evidences)} Evidences")

            # Zurück-Konvertierung zu Sources für Summarizer (mit optimierten Scores)
            sources_reranked = []
            for ev in optimized_evidences:
                # Finde Original-Source (für Metadata-Preservation)
                orig_src = next((s for s in sources if getattr(s, 'url', '') == ev.source), None)
                if orig_src:
                    # Update score mit Cross-Encoder-Ranking
                    orig_src.score = ev.score
                    sources_reranked.append(orig_src)
                else:
                    # Neu erstellen falls nicht gefunden
                    new_source = Source(
                        title=f"RAG Evidence ({ev.domain})",
                        url=ev.source
                    )
                    new_source.content = ev.content  # ✅ Als Attribut setzen
                    new_source.score = ev.score      # ✅ Als Attribut setzen
                    sources_reranked.append(new_source)

            logger.info(f"🧠 Hybrid Reasoning abgeschlossen: {len(sources_reranked)} final sources")
            logger.info("🧠" + "=" * 68)
            return sources_reranked

        except Exception as e:
            logger.warning(f"⚠️ Hybrid Reasoning Fehler in RAG-only mode: {type(e).__name__}: {e}")
            import traceback
            logger.debug(f"Hybrid Reasoning Traceback:\n{traceback.format_exc()}")
            # Fahre mit ursprünglichen Sources fort
            return sources

    def _finalize_and_build_answer(
        self,
        query: str,
        final_text: str,
        sources: List[Source],
        trace: AgentTrace,
        extracted_followups: Optional[List[str]] = None,
        finance_grounding_block: Optional[str] = None,
    ) -> FinalAnswer:
        """Apply final formatting steps and build the FinalAnswer payload."""
        if finance_grounding_block is not None:
            final_text = self._merge_with_finance_grounding(final_text or "", finance_grounding_block)

        final_text, extracted_followups = self._finalize_answer(
            query,
            final_text,
            sources,
            extracted_followups=extracted_followups,
        )

        return FinalAnswer(
            text=final_text,
            sources=sources,
            trace=trace,
            followup_questions=extracted_followups,
        )

    @staticmethod
    def _collect_graphics(results: List[ToolResult]) -> List[Dict[str, Any]]:
        """Normalize image artifacts without relying on answer-text markers."""
        graphics: List[Dict[str, Any]] = []
        for result in results:
            meta = result.meta if isinstance(result.meta, dict) else {}
            raw = meta.get("raw_payload")
            if not isinstance(raw, dict):
                raw = meta

            output_path = raw.get("output_path")
            if isinstance(output_path, str) and output_path:
                extension = os.path.splitext(output_path)[1].lower()
                media_type = {
                    ".jpg": "image/jpeg",
                    ".jpeg": "image/jpeg",
                    ".webp": "image/webp",
                    ".svg": "image/svg+xml",
                }.get(extension, "image/png")
                graphics.append({
                    "type": "image",
                    "path": output_path,
                    "media_type": media_type,
                    "caption": raw.get("message") or "Generiertes Diagramm",
                    "diagram_type": raw.get("diagram_type"),
                    "backend": raw.get("backend"),
                })

            plot_base64 = raw.get("plot_base64") or raw.get("plot")
            if isinstance(plot_base64, str) and plot_base64:
                plot_format = str(raw.get("plot_format", "png")).lower()
                media_type = "image/jpeg" if plot_format in {"jpg", "jpeg"} else "image/png"
                graphics.append({
                    "type": "image",
                    "data_base64": plot_base64,
                    "media_type": media_type,
                    "caption": "Ausgeführte Visualisierung",
                    "backend": result.tool,
                })
        return graphics

    @staticmethod
    def _collect_files(results: List[ToolResult]) -> List[Dict[str, Any]]:
        """Normalize downloadable files emitted by code execution."""
        files: List[Dict[str, Any]] = []
        for result in results:
            meta = result.meta if isinstance(result.meta, dict) else {}
            raw = meta.get("raw_payload")
            if not isinstance(raw, dict):
                raw = meta
            for file_info in raw.get("files", []):
                if not isinstance(file_info, dict):
                    continue
                path = file_info.get("path")
                name = file_info.get("name")
                if isinstance(path, str) and path and isinstance(name, str) and name:
                    files.append({
                        "path": path,
                        "name": name,
                        "size": int(file_info.get("size", 0) or 0),
                        "media_type": file_info.get("media_type") or "application/octet-stream",
                        "caption": "Erzeugtes Programm" if name.lower().endswith(".py") else "Erzeugte Datei",
                    })
        return files

    def run_tools_and_summarize(self, query: str, planned_calls: List[ToolCall], history: List[Dict[str, Any]], reasoning: Optional[str] = None, critique: Optional[str] = None, planner_ms: Optional[int] = None, planner_raw: Optional[str] = None) -> FinalAnswer:
        """Execute planned tools, pick evidence (incl. RAG), summarize, verify, and return the final answer with trace."""
        self._refresh_runtime_mode()
        trace = AgentTrace()
        trace.planned_tools = [c.tool for c in planned_calls]
        trace.reasoning = reasoning
        trace.critique = critique
        if planner_ms is not None:
            trace.planner_ms = planner_ms
        if planner_raw is not None:
            trace.planner_output = planner_raw
        # Snapshot RAG/MQ config
        trace.rag_enabled = bool(self.rag_enabled)
        trace.rag_k = int(self.rag_k)
        trace.rag_min_score = float(self.rag_min_score)
        trace.multiquery_enabled = bool(self.multiquery_enabled)
        trace.mq_n = int(self.mq_n)
        trace.mq_k = int(self.mq_k)

        query, security_blocked = self._apply_security_guard(query=query, trace=trace)
        if security_blocked is not None:
            return security_blocked

        route_decision, planned_calls = self._apply_retrieval_route_with_trace(
            query=query,
            planned_calls=planned_calls,
            trace=trace,
        )

        skip_web_search, rag_first_results, rag_result_count, rag_max_score = self._run_rag_first_gating(
            query=query,
            planned_calls=planned_calls,
            initial_skip_web_search=(route_decision.route == RetrievalRoute.INTERNAL_ONLY),
        )

        # --- MultiHop-Enforcement (SOTA: Adaptive-RAG, arxiv 2403.14403) ---
        # When router decides "deep", enrich initial RAG evidence via MultiHopRetriever.
        # This closes the gap where decision.depth was computed but never consumed.
        if (route_decision.depth == "deep"
                and self._adaptive_multi_hop is not None
                and self.adaptive_strategy
                and rag_first_results):
            try:
                initial_texts = [r.text for r in rag_first_results if r.text is not None and r.text != ""]
                initial_scores = [r.score for r in rag_first_results if getattr(r, "score", None) and r.score > 0]
                if initial_texts and initial_scores:
                    t_mh = time.perf_counter()
                    multi_hop_result = self._adaptive_multi_hop.retrieve(
                        query=query,
                        initial_evidence=(initial_texts, initial_scores),
                    )
                    mh_ms = (time.perf_counter() - t_mh) * 1000
                    trace.multi_hop_executed = True
                    trace.multi_hop_ms = round(mh_ms, 1)
                    trace.multi_hop_hops = multi_hop_result.hops_executed
                    trace.multi_hop_converged = multi_hop_result.converged

                    # Merge MultiHop evidence into rag_first_results as additional ToolResults
                    for idx, (txt, sc) in enumerate(
                        zip(multi_hop_result.total_evidence_texts,
                            multi_hop_result.total_evidence_scores)
                    ):
                        mh_tool_result = ToolResult(
                            tool="adaptive_multi_hop",
                            success=True,
                            text=txt,
                            meta={
                                "hop": idx + 1,
                                "score": sc,
                                "multi_hop_ms": mh_ms,
                                "hops_executed": multi_hop_result.hops_executed,
                            },
                        )
                        rag_first_results.append(mh_tool_result)

                    trace.rag_result_count = len(rag_first_results)
                    logger.info(
                        "[MultiHop] deep query enriched: %d hops, %.0fms, %d total chunks",
                        multi_hop_result.hops_executed,
                        mh_ms,
                        len(rag_first_results),
                    )
            except Exception as mh_err:
                logger.warning(
                    "[MultiHop] enrichment failed — falling back to plain RAG: %s",
                    mh_err,
                )
                trace.multi_hop_error = str(mh_err)

        planned_calls, results, finance_grounding_block, early_answer = self._execute_tools_with_rag_postprocessing(
            query=query,
            planned_calls=planned_calls,
            trace=trace,
            skip_web_search=skip_web_search,
            rag_first_results=rag_first_results,
            rag_result_count=rag_result_count,
            rag_max_score=rag_max_score,
        )
        if early_answer is not None:
            return early_answer

        sources, evidence_result = self._select_and_enrich_evidence(
            query=query,
            results=results,
            history=history,
            trace=trace,
        )

        extras = self._build_tool_summaries_and_trace_artifacts(
            query=query,
            results=results,
            reasoning=reasoning,
            finance_grounding_block=finance_grounding_block,
            trace=trace,
        )
        self._populate_source_observability(sources=sources, trace=trace)
        sources, use_fallback = self._apply_post_evidence_refinement(
            query=query,
            sources=sources,
            results=results,
            trace=trace,
        )
        
        final_text, verification_result, sources, results, extracted_followups, sum_metrics = self._generate_answer_from_sources(
            query=query,
            history=history,
            sources=sources,
            extras=extras,
            fallback=use_fallback,
            trace=trace,
            results=results,
        )
        self._post_generation_housekeeping(
            sources=sources,
            trace=trace,
            evidence_result=evidence_result,
        )
        # PHASE 4 STEP 6: Citation & Sources formatting via ResponseBuilder
        # HINWEIS: Formatierung wird NACH Hybrid-Reasoning-Validation durchgeführt,
        # damit die Validierung den reinen Inhalt ohne Citations/URLs prüft.
        # (Root-Cause-Fix 2026-03-20: Formatierter Text enthält URLs, Quellen-Blöcke
        #  die das Grounding-Measurement verfälschen)
        final_text = self._apply_hybrid_reasoning_validation(
            query=query,
            history=history,
            sources=sources,
            extras=extras,
            final_text=final_text,
        )

        final_answer = self._finalize_and_build_answer(
            query=query,
            final_text=final_text,
            sources=sources,
            trace=trace,
            extracted_followups=extracted_followups,
            finance_grounding_block=finance_grounding_block,
        )
        final_answer.graphics = self._collect_graphics(results)
        final_answer.files = self._collect_files(results)
        return final_answer

    def run_no_tools_and_summarize(self, query: str, history: List[Dict[str, Any]]) -> FinalAnswer:
        """
        Run summarizer + verifier without tools; still considers RAG evidence (incl. Multi-Query if enabled).
        
        ADAPTIVE PLANNING INTEGRATION (2025):
        - Reflection 1: Daten-Qualität bewerten (RAG-Ergebnisse)
        - Optional: Zusätzliche RAG-Suchen bei niedrigen Confidence-Scores
        - Feature-Flag: self.adaptive_planning_enabled
        
        Architektur:
        1. RAG Execution (QueryStrategyManager + RAGManager)
        2. ADAPTIVE: Optional Reflection 1 (Daten-Qualität)
        3. Evidence Selection (EvidenceManager mit Validation)
        4. Hybrid Reasoning (EvidenceProcessor Reranking)
        5. Summarize + Verify (ResponseBuilder)
        6. Citation & Sources Formatting
        """
        trace = AgentTrace()
        trace.planned_tools = []
        trace.ran_tools = []
        
        # Snapshot RAG/MQ config
        trace.rag_enabled = bool(self.rag_enabled)
        trace.rag_k = int(self.rag_k)
        trace.rag_min_score = float(self.rag_min_score)
        trace.multiquery_enabled = bool(self.multiquery_enabled)
        trace.mq_n = int(self.mq_n)
        trace.mq_k = int(self.mq_k)
        
        # ===================================================================
        # PHASE 1: RAG EXECUTION via RAGManager (RAG-only mode)
        # ===================================================================
        results: List[ToolResult] = []
        t_tools0 = time.perf_counter()
        
        if self.rag_enabled:
            # Dynamic Context Window: compute effective k + get strategy
            effective_k, strategy_result = self._compute_effective_k_with_strategy(query)
            
            # Generate sub-queries via central DecompositionEngine.
            subqs = self._generate_subqueries(query) if self.multiquery_enabled else []

            if self.multiquery_enabled:
                logger.info(f"✅ DecompositionEngine produced {len(subqs)} sub-queries (RAG-only mode)")
                try:
                    trace.subqueries = list(subqs or [])
                except AttributeError as e:
                    logger.debug(f"Trace-Subqueries-Attributfehler: {e}")
                except Exception as e:
                    logger.warning(f"Unerwarteter Fehler beim Setzen der Trace-Subqueries: {type(e).__name__}: {e}")
            
            # Execute RAG via RAGManager with Dynamic-K
            logger.info(f"🎯 RAG-only with Dynamic-K: effective_k={effective_k} (gui_max={self.rag_k})")
            results = self.rag_manager.execute_rag_with_multiquery(
                query=query,
                k=effective_k,
                min_score=self.rag_min_score,
                multiquery_enabled=self.multiquery_enabled,
                mq_n=self.mq_n,
                mq_k=self.mq_k,
                sub_queries=subqs if subqs else None,
                is_time_critical=False,  # RAG-only mode, no web results
                web_results_available=False
            )
        else:
            logger.info("RAG deaktiviert - keine lokale Suche in rag_only-Modus")
        
        trace.tools_ms = int((time.perf_counter() - t_tools0) * 1000)
        try:
            trace.ran_tools = [r.tool for r in results]
        except AttributeError as e:
            logger.debug(f"Trace-Ran-Tools-Attributfehler (rag_only): {e}")
            trace.ran_tools = []
        except Exception as e:
            logger.warning(f"Unerwarteter Fehler beim Tool-Recording (rag_only): {type(e).__name__}: {e}")
            trace.ran_tools = []
        
        # ===================================================================
        # PHASE 2: ADAPTIVE PLANNING (Reflection 1 - Daten-Qualität)
        # ===================================================================
        if self.adaptive_planning_enabled and self.adaptive_planner is not None:
            logger.info("🔄 Adaptive Planning aktiviert - starte Reflection 1 (Daten-Qualität)...")
            
            try:
                # Type guard: adaptive_planner is not None at this point
                planner = self.adaptive_planner
                
                # Reflection 1: Bewerte RAG-Daten-Qualität
                reflection_result = planner._reflect_data_quality(
                    query=query,
                    current_results=results,
                    iteration=1
                )
                
                # Store reflection in trace
                trace.adaptive_reflections = [reflection_result]
                trace.adaptive_planning_triggered = True
                
                confidence_done = reflection_result.get("confidence_done", 0)
                confidence_more_tools = reflection_result.get("confidence_more_tools", 0)
                
                logger.info(
                    f"📊 Reflection 1: confidence_done={confidence_done:.2f}, "
                    f"confidence_more_tools={confidence_more_tools:.2f}"
                )
                
                # Early Exit Check
                if confidence_done > planner.confidence_done_threshold:
                    logger.info(f"✅ Early Exit: Daten-Qualität ausreichend (threshold={planner.confidence_done_threshold:.2f})")
                
                # Check if more RAG searches needed
                elif confidence_more_tools > planner.confidence_tools_threshold:
                    logger.info(f"🔧 Adaptive Planner schlägt zusätzliche Tools vor...")
                    
                    # Get additional tools from reflection
                    additional_calls = reflection_result.get("additional_tools", [])
                    
                    # Filter nur rag_search tools (no web_search in RAG-only mode)
                    rag_calls = [call for call in additional_calls if call.tool == "rag_search"]
                    
                    if rag_calls:
                        logger.info(f"🔧 Führe {len(rag_calls)} zusätzliche RAG-Suchen aus...")
                        
                        # Execute additional RAG searches
                        additional_results = self.tools.run(rag_calls)
                        
                        # Merge results
                        results.extend(additional_results)
                        
                        # Update trace
                        trace.adaptive_additional_tools = len(rag_calls)
                        trace.ran_tools.extend([r.tool for r in additional_results])
                        
                        logger.info(f"✅ {len(additional_results)} zusätzliche Ergebnisse gesammelt")
                    else:
                        logger.info("ℹ️ Keine RAG-Suchen geplant (nur web_search vorgeschlagen, wird übersprungen)")
                
                else:
                    logger.info(f"⏭️ Keine zusätzlichen Suchen nötig (confidence_more_tools={confidence_more_tools:.2f} < threshold)")
            
            except Exception as e:
                logger.error(f"❌ Adaptive Planning Fehler in RAG-only mode: {type(e).__name__}: {e}")
                import traceback
                logger.debug(f"Adaptive Planning Traceback:\n{traceback.format_exc()}")
                trace.adaptive_planning_error = str(e)
        
        else:
            logger.debug("Adaptive Planning deaktiviert oder nicht verfügbar")
            trace.adaptive_planning_triggered = False
        
        # ===================================================================
        # PHASE 3: EVIDENCE SELECTION via EvidenceManager
        # ===================================================================
        t_evidence0 = time.perf_counter()
        
        evidence_result = self.evidence_manager.select_evidence_from_tool_results(
            query=query,
            tool_results=results,
            evidence_max_candidates=self.evidence_max_candidates,
            evidence_shortlist_m=self.evidence_shortlist_m,
            evidence_diversity_lambda=self.evidence_diversity_lambda,
            is_news_query=self._is_news_query(query),
            news_min_k=self.news_min_k,
            news_max_k=self.news_max_k,
            model_loader=self.model_loader,
            use_llm_evidence_selection=self.use_llm_evidence_selection,
            validation_enabled=True,
            validation_max_iterations=1,  # Reduced for RAG-only
            validation_min_sources=2  # Reduced for RAG-only
        )
        
        # Extract selected sources from result
        sources: List[Source] = evidence_result.sources
        
        # Store validation stats in trace for observability
        try:
            trace.source_validation = evidence_result.validation_stats
        except AttributeError as e:
            logger.debug(f"Trace-Source-Validation-Attributfehler (RAG-only): {e}")
        except Exception as e:
            logger.warning(f"Fehler beim Setzen der Source-Validation-Stats (RAG-only): {type(e).__name__}: {e}")
        
        logger.info(f"RAG-only evidence selection: {evidence_result.candidates_count} → {evidence_result.final_count} sources")
        
        # Tool summaries for trace
        tool_summaries: List[str] = []
        for r in results:
            if r.tool == "rag_search":
                cnt = len(r.results or [])
                err = f"; Fehler: {r.error}" if r.error else ""
                tool_summaries.append(f"rag_search: {cnt} Treffer{err}")
        
        try:
            if self.multiquery_enabled:
                subquery_count = len(trace.subqueries or [])
                tool_summaries.append(f"multiquery: {subquery_count} Teilfragen")
        except AttributeError as e:
            logger.debug(f"Trace-Subqueries-Attributfehler beim RAG-only-Summary: {e}")
        except Exception as e:
            logger.warning(f"Unerwarteter Fehler beim RAG-only-Multiquery-Summary: {type(e).__name__}: {e}")
            if self.multiquery_enabled:
                tool_summaries.append("multiquery: Fehler beim Zählen der Teilfragen")
        
        trace.tool_summaries = tool_summaries
        
        # ===================================================================
        # COLLECT DETAILED TOOL RESULTS (for debugging)
        # ===================================================================
        try:
            tool_results = {}
            subquery_counter = 0
            for r in results:
                if r.tool == "rag_search":
                    # Determine the actual query used for this result
                    actual_query = query  # Default to main query
                    tool_suffix = ""
                    
                    # For RAG with multiquery, try to match to specific subquery
                    if self.multiquery_enabled and hasattr(trace, 'subqueries'):
                        subqueries = getattr(trace, 'subqueries', [])
                        if subquery_counter == 0:
                            # First RAG result is the original query
                            actual_query = query
                            tool_suffix = "_original"
                        elif subquery_counter <= len(subqueries):
                            # Subsequent results are subqueries
                            actual_query = subqueries[subquery_counter - 1]
                            tool_suffix = f"_subquery_{subquery_counter}"
                        subquery_counter += 1
                    
                    # Store detailed results for rag_search
                    tool_data: Dict[str, Any] = {
                        "tool": r.tool,
                        "query": actual_query,
                        "results_count": len(r.results or []),
                        "error": r.error,
                        "results": []
                    }
                    # Store top results (limit to avoid too much data)
                    if r.results:
                        for result in r.results[:10]:  # Max 10 results
                            if hasattr(result, 'content'):
                                # RAG search result
                                content = getattr(result, 'content', '') or ''
                                tool_data["results"].append({
                                    "source": getattr(result, 'source', '') or '',
                                    "content": content[:300],  # Limit content length
                                    "score": getattr(result, 'score', 0.0)
                                })
                            else:
                                # Generic result (fallback)
                                tool_data["results"].append({
                                    "data": str(result)[:200]
                                })
                    
                    # Use tool name with counter if multiple of same type
                    key = f"{r.tool}{tool_suffix}"
                    counter = 1
                    while key in tool_results:
                        counter += 1
                        key = f"{r.tool}{tool_suffix}_{counter}"
                    tool_results[key] = tool_data
            
            trace.tool_results = tool_results
            
        except AttributeError as e:
            logger.debug(f"Trace-Tool-Results-Attributfehler (RAG-only): {e}")
            trace.tool_results = {}
        except Exception as e:
            logger.warning(f"Fehler beim Sammeln der Tool-Ergebnisse (RAG-only): {type(e).__name__}: {e}")
            import traceback
            logger.debug(f"RAG-only-Tool-Results-Collection-Fehler Traceback:\n{traceback.format_exc()}")
            trace.tool_results = {}
        
        # No extras in no-tools path
        extras: List[str] = []
        trace.extras_count = 0
        self._populate_source_observability(sources=sources, trace=trace)
        sources = self._apply_no_tools_hybrid_reranking(query=query, sources=sources)
        
        # ===================================================================
        # PHASE 5: SUMMARIZE + VERIFY
        # ===================================================================
        # Fallback if no evidence
        use_fallback = len(sources) == 0
        if use_fallback:
            trace.heuristic_triggered = True
            trace.heuristic_reason = "Keine Tools/Evidenz – nutze internes Wissen/Logik."
        
        final_text, verification_result, sources, results, extracted_followups, sum_metrics = self._generate_answer_from_sources(
            query=query,
            history=history,
            sources=sources,
            extras=extras,
            fallback=use_fallback,
            trace=trace,
            results=results,
        )
        
        try:
            logger.debug(
                "orchestrate_no_tools_done | cand=%d shortlist=%d K=%d sources=%d domains=%s sum_ms=%d",
                evidence_result.candidates_count,
                evidence_result.shortlist_count,
                evidence_result.final_count,
                len(sources),
                trace.evidence_domains,
                trace.summarize_ms,
            )
        except AttributeError as e:
            logger.debug(f"Debug-Logging-Attributfehler in run_no_tools: {e}")
        except (TypeError, ValueError) as e:
            logger.debug(f"Debug-Logging-Wertfehler in run_no_tools: {e}")
        except Exception as e:
            logger.warning(f"Unerwarteter Fehler beim Debug-Logging in run_no_tools: {type(e).__name__}: {e}")
        
        # ===================================================================
        # PHASE 6: CITATION & SOURCES FORMATTING via ResponseBuilder
        # ===================================================================
        return self._finalize_and_build_answer(
            query=query,
            final_text=final_text,
            sources=sources,
            trace=trace,
            extracted_followups=extracted_followups,
        )

    # --- Verifier ---
    def verify_step(
        self, 
        query: str, 
        draft: str, 
        evidence: List[Source], 
        *, 
        fallback: bool = False
    ) -> Tuple[str, Optional[Any]]:  # Use Any instead of forward reference
        """
        Verify and minimally correct the draft using the provided evidence.
        
        STEP 7: Enhanced with VerificationManager for quality checks.
        
        Returns:
            Tuple of (verified_answer, verification_result)
        """
        from agent.verification_manager import VerificationResult
        
        # PHASE 4 STEP 6: Use ResponseBuilder for prompt building
        prompt_result = self.response_builder.build_verifier_prompt(
            query=query,
            draft=draft,
            sources=evidence,
            fallback=fallback
        )
        
        # Extract messages
        messages = prompt_result['messages']
        
        # ── DYNAMISCHES TOKEN-BUDGET ──
        # Root-Cause-Fix 2026-04-18: Der Verifier muss die "korrigierte Endfassung"
        # vollständig ausgeben können.  Wenn der Draft z.B. 1413 Tokens hat, der
        # verifier_max_tokens aber nur 1024, wird die Antwort abgeschnitten und
        # strukturierte Elemente (Tabellen, Listen) am Ende gehen verloren.
        # → Token-Budget mindestens so groß wie der Draft + Buffer.
        draft_word_count = len(draft.split()) if draft else 0
        draft_token_estimate = int(draft_word_count * 1.5)  # ~1.3-1.5 Tokens/Wort (Deutsch)
        effective_verifier_max = max(self.verifier_max_tokens, draft_token_estimate + 256)
        
        # Sicherheits-Cap: n_ctx nicht überschreiten
        prompt_tokens = self.ctx.estimate_tokens(messages)
        available = max(256, self.ctx.n_ctx - self.ctx.reserve - prompt_tokens)
        effective_verifier_max = min(effective_verifier_max, available)
        
        if effective_verifier_max > self.verifier_max_tokens:
            logger.info(
                f"🔧 Verifier Token-Budget dynamisch erhöht: "
                f"{self.verifier_max_tokens} → {effective_verifier_max} "
                f"(Draft ~{draft_token_estimate} Tokens)"
            )
        
        # Generate response
        t0 = time.perf_counter()
        out = self.model_loader.generate_response(
            messages=messages,
            max_tokens=effective_verifier_max,
            temperature=0.0,
            image_path=None
        )
        _elapsed_ms = int((time.perf_counter() - t0) * 1000)
        
        # STEP 7: Verify answer quality using VerificationManager
        verification_result: Optional[VerificationResult] = None
        
        if self.enable_answer_verification and out and out.strip():
            try:
                # Convert Sources to evidence dict format for verification
                evidence_dicts = [
                    {
                        'text': src.content or src.snippet or src.title or '',
                        'content': src.content or src.snippet or '',
                        'url': src.url or '',
                        'title': src.title or ''
                    }
                    for src in evidence
                ]
                
                # Run verification
                verification_result = self.verification_manager.verify_answer(
                    answer=out,
                    evidence_list=evidence_dicts,
                    query=query,
                    level=self.verification_level
                )
                
                logger.info(
                    f"Answer verification: verified={verification_result.is_verified}, "
                    f"confidence={verification_result.confidence_score:.2f}, "
                    f"quality={verification_result.quality_score:.2f}"
                )

                # Derive declarative VerificationStatus for AdaptivePlanner consumers
                try:
                    from agent.verification_manager import VerificationStatus
                    status = verification_result.to_status()
                    verification_result.metadata["verification_status"] = status.value
                    logger.info(f"[VerificationStatus] → {status.value}")
                    if status in (
                        VerificationStatus.INSUFFICIENT_EVIDENCE,
                        VerificationStatus.HALLUCINATION_RISK,
                    ):
                        logger.warning(
                            "[VerificationStatus] %s — consider triggering AdaptivePlanner action",
                            status.value,
                        )
                except Exception as _vs_exc:
                    logger.debug("VerificationStatus derivation failed: %s", _vs_exc)

                # Log issues and warnings
                if verification_result.issues:
                    logger.warning(f"Verification issues: {verification_result.issues}")
                if verification_result.warnings:
                    logger.info(f"Verification warnings: {verification_result.warnings}")
                
            except AttributeError as e:
                logger.debug(f"VerificationManager not available: {e}")
            except (ValueError, TypeError) as e:
                logger.warning(f"Verification input error: {e}")
            except Exception as e:
                logger.error(f"Verification error: {type(e).__name__}: {e}")
                import traceback
                logger.debug(f"Verification traceback:\n{traceback.format_exc()}")
        
        return out, verification_result

    # --- Helpers ---
    def _parse_planner_output(self, text: str) -> Tuple[List[ToolCall], Optional[str], Optional[str], Optional[str]]:
        """Parse planner output: ToolCalls, FINAL, REASONING block, CRITIQUE."""
        calls: List[ToolCall] = []
        final_text: Optional[str] = None
        reasoning: Optional[str] = None
        critique: Optional[str] = None
        if not text:
            return calls, final_text, reasoning, critique
        
        # ✅ ROBUST: Entferne verbliebene [THINK]/[/THINK]-Tags (Safety-Net)
        import re as _re
        clean_text = _re.sub(r'\[/?THINK\]', '', str(text), flags=_re.IGNORECASE).strip()
        if not clean_text:
            logger.warning("[PLANNER PARSE] Text nach THINK-Bereinigung leer!")
            return calls, final_text, reasoning, critique
        
        lines = [ln.strip() for ln in clean_text.splitlines() if ln.strip()]
        reasoning_lines = []
        final_lines = []
        in_reasoning = False
        in_final = False
        for ln in lines:
            if ln.upper().startswith("REASONING:"):
                in_reasoning = True
                in_final = False
                reasoning_lines.append(ln)
                continue
            if ln.upper().startswith("FINAL:"):
                in_final = True
                in_reasoning = False
                # Erste Zeile vom FINAL: Block
                final_content = ln.split(":", 1)[1].strip() if ":" in ln else ""
                if final_content:
                    final_lines.append(final_content)
                continue
            if ln.upper().startswith("CRITIQUE:"):
                in_final = False
                in_reasoning = False
                critique = ln.split(":", 1)[1].strip() if ":" in ln else ln
                continue
            if ln.startswith("[TOOL:") or ln.startswith("[tool:"):
                in_final = False
                in_reasoning = False
            if in_reasoning:
                if not (ln.upper().startswith("CRITIQUE:") or ln.upper().startswith("FINAL:") or ln.startswith("[TOOL:") or ln.startswith("[tool:")):
                    reasoning_lines.append(ln)
            if in_final:
                if not (ln.upper().startswith("CRITIQUE:") or ln.startswith("[TOOL:") or ln.startswith("[tool:")):
                    final_lines.append(ln)
        # Parse tool calls - ROBUSTER: Erlaube Whitespace und Varianten
        # Strikt: [TOOL:name:params] oder [tool:name:params]
        pattern_strict = re.compile(r"^\[(?:TOOL|tool)\s*:\s*([^:\]]+?)\s*:\s*([^\]]+)\]\s*$")
        # Flexibel: auch mit Extra-Text davor/danach
        pattern_flex = re.compile(r"\[(?:TOOL|tool)\s*:\s*([^:\]]+?)\s*:\s*([^\]]+)\]")
        
        for ln in lines:
            m = pattern_strict.match(ln)
            if not m:
                m = pattern_flex.search(ln)
            if not m:
                continue
            tool_name = (m.group(1) or "").strip().lower()
            param_str = (m.group(2) or "").strip()
            params = self._normalize_params(tool_name, param_str)
            if tool_name:
                calls.append(ToolCall(tool=tool_name, parameters=params))
        reasoning = "\n".join(reasoning_lines) if reasoning_lines else None
        final_text = "\n".join(final_lines) if final_lines else None
        
        # ✅ FALLBACK: Wenn Parser nichts erkannt hat, aber Text vorhanden ist
        if not calls and not final_text and not reasoning and clean_text:
            # Prüfe ob der Text wie eine direkte Antwort aussieht (kein strukturiertes Format)
            has_planner_keywords = any(kw in clean_text.upper() for kw in ['REASONING:', 'FINAL:', '[TOOL:', 'CRITIQUE:'])
            if not has_planner_keywords:
                # LLM hat direkt geantwortet statt im Planner-Format
                logger.info("[PLANNER PARSE] Kein strukturiertes Format erkannt → verwende als FINAL-Text")
                final_text = clean_text
            else:
                # Es gibt Planner-Keywords aber nichts wurde geparst (Format-Fehler)
                logger.warning(f"[PLANNER PARSE] Planner-Keywords gefunden aber Parse fehlgeschlagen!")
                logger.warning(f"[PLANNER PARSE] Erste 300 Zeichen: {clean_text[:300]}")
                # Fallback: Verwende als final_text damit nicht alles verloren geht
                final_text = clean_text
        
        # 🔧 POST-PROCESSING: Wenn FINAL Python-Code enthält, wandle in code_executor um
        if final_text and not calls:
            extracted_code = self._extract_python_code_from_text(final_text)
            if extracted_code:
                logging.info("🔧 Auto-Korrektur: Python-Code in FINAL erkannt → wandle in code_executor Tool-Call um")
                calls.append(ToolCall(tool="code_executor", parameters={"code": extracted_code}))
                # Entferne Code aus final_text, behalte nur Erklärung
                final_text = self._remove_code_blocks_from_text(final_text)
        
        return calls, final_text, reasoning, critique

    def _normalize_params(self, tool_name: str, s: str) -> Dict[str, Any]:
        """Map simple param strings to tool parameter dicts."""
        if tool_name == "calculator":
            return {"expression": s}
        if tool_name == "web_search":
            params = {"query": s, "num_results": 5}
            
            # 🔧 FIX 2025-10-13: Zeit-Filter für aktuelle Queries
            # WICHTIG: Für Zukunfts-Events (nächstes Spiel) brauchen wir AKTUELLE Artikel
            # Verwende "m" (Monat) statt "w" (Woche) für bessere Abdeckung
            if self._is_time_critical_query(s):
                params["timelimit"] = "m"  # Last month - besser für Zukunfts-Events
                logger.info(f"⏰ Zeitkritische Query erkannt: '{s[:50]}...' → timelimit='m' (letzter Monat)")
            elif self._is_news_query(s):
                params["timelimit"] = "w"  # Last week for general news
                logger.info(f"📰 News-Query erkannt: '{s[:50]}...' → timelimit='w' (letzte Woche)")
            
            return params
        if tool_name == "rag_search":
            params = {"query": s, "k": self.rag_k}
            # NEW 2025-10-11: Add FAISS confidence if manually set
            if self.faiss_min_confidence is not None:
                params["faiss_min_confidence"] = self.faiss_min_confidence
            return params
        if tool_name == "file_reader":
            return {"file_path": s}
        if tool_name == "file_writer":
            parts = s.split(":", 1)
            if len(parts) == 2:
                return {"file_path": parts[0], "content": parts[1]}
            return {"file_path": s, "content": ""}
        if tool_name == "code_executor":
            return {"code": s}
        if tool_name == "image_info":
            return {"image_path": s}
        if tool_name in {"create_diagram", "canvas"}:
            # Keep planner-format tool calls aligned with toolkit schema contract.
            return {"description": s}
        return {"query": s}

    def _extract_python_code_from_text(self, text: str) -> Optional[str]:
        """
        Extrahiert Python-Code aus Text (mit oder ohne ```python Markdown).
        Erkennt: import statements, matplotlib, plt.show(), numpy, etc.
        
        Returns:
            str: Extrahierter Code, oder None falls kein Python-Code gefunden
        """
        if not text:
            return None
        
        # ── Literal \n Unescape (Defense-in-Depth) ──
        # Wenn der LLM-Output literal \n enthält (JSON-Encoding-Artefakt),
        # werden die Regex-Matches für Code-Blöcke und Zeilenstruktur
        # fehlschlagen. Unescape VOR der Analyse.
        working_text = text
        if r'\n' in text and text.count(r'\n') >= 3:
            code_kws = ('def ', 'import ', 'class ', 'for ', 'return ', 'print(')
            if sum(1 for kw in code_kws if kw in text) >= 2:
                working_text = text.replace(r'\n', '\n').replace(r'\t', '\t')
        
        # Methode 1: Markdown Code-Blocks
        import re
        code_block_pattern = r'```(?:python)?\s*\n(.*?)\n```'
        matches = re.findall(code_block_pattern, working_text, re.DOTALL | re.IGNORECASE)
        if matches:
            # Nimm den längsten Code-Block
            longest_code: str = max(matches, key=len)
            if len(longest_code.strip()) > 20:  # Mindestlänge
                return longest_code.strip()
        
        # Methode 2: Erkenne typische Python-Muster ohne Code-Block
        python_indicators = [
            'import matplotlib',
            'import numpy',
            'import pandas',
            'plt.show()',
            'plt.plot(',
            'plt.figure(',
            'def ',
            'for ',
            'if __name__'
        ]
        
        # Zähle Indikatoren
        indicator_count = sum(1 for indicator in python_indicators if indicator in working_text)
        
        # Wenn mehrere Indikatoren gefunden, extrahiere alle Zeilen die wie Code aussehen
        if indicator_count >= 2:
            lines = working_text.split('\n')
            code_lines = []
            in_code = False
            
            for line in lines:
                stripped = line.strip()
                # Start Code-Erkennung bei Import/Def
                if any(stripped.startswith(kw) for kw in ['import ', 'from ', 'def ', 'class ']):
                    in_code = True
                
                # Sammle Code-Zeilen
                if in_code:
                    # Stop bei Prosa-Text (lange Zeilen ohne Code-Syntax)
                    if len(stripped) > 100 and not any(c in stripped for c in ['=', '(', ')', '[', ']', ':']):
                        in_code = False
                        continue
                    code_lines.append(line)
            
            if code_lines:
                code = '\n'.join(code_lines).strip()
                if len(code) > 20:
                    return code
        
        return None
    
    def _remove_code_blocks_from_text(self, text: str) -> str:
        """
        Entfernt Code-Blocks aus Text, behält nur Erklärungen.
        
        Args:
            text: Original-Text mit Code-Blocks
            
        Returns:
            str: Text ohne Code-Blocks
        """
        if not text:
            return text
        
        import re
        
        # Entferne Markdown Code-Blocks
        text_without_code = re.sub(r'```(?:python)?\s*\n.*?\n```', '', text, flags=re.DOTALL | re.IGNORECASE)
        
        # Entferne mehrfache Leerzeilen
        text_without_code = re.sub(r'\n{3,}', '\n\n', text_without_code)
        
        return text_without_code.strip()
    
    # ==================== INTELLIGENT SOURCE VALIDATION (2025) ====================
    
    def validate_and_extend_sources(self, query: str, sources: List[Source], 
                                   max_iterations: int = 3, min_relevant_sources: int = 2) -> Tuple[List[Source], Dict[str, Any]]:
        """
        Validates if sources are sufficient to answer the query and automatically searches for additional sources if needed.
        
        Args:
            query: Original user question
            sources: Initial sources from web/RAG search
            max_iterations: Maximum number of additional search iterations
            min_relevant_sources: Minimum number of relevant sources required
            
        Returns:
            Tuple of (validated_sources, validation_stats)
        """
        validation_stats: Dict[str, Any] = {
            "initial_sources": len(sources),
            "iterations": 0,
            "web_searches": 0,
            "rag_searches": 0,
            "rejected_sources": 0,
            "final_sources": 0,
            "validation_reason": "initial"
        }
        
        if not sources:
            # No sources at all - trigger comprehensive search
            logger.info("No initial sources found, triggering comprehensive search")
            return self._comprehensive_source_search(query, validation_stats)
        
        relevant_sources = []
        iteration = 0
        
        while iteration < max_iterations:
            # Validate current sources
            validation_result = self._validate_source_relevance(query, sources)
            
            relevant_batch = validation_result.get("relevant_sources", [])
            relevance_score = validation_result.get("overall_relevance", 0.0)
            gaps = validation_result.get("information_gaps", [])
            
            relevant_sources.extend(relevant_batch)
            validation_stats["rejected_sources"] += len(sources) - len(relevant_batch)
            
            logger.info(f"Source validation iteration {iteration + 1}: {len(relevant_batch)}/{len(sources)} relevant, score: {relevance_score:.2f}")
            
            # Check if we have enough relevant sources
            if len(relevant_sources) >= min_relevant_sources and relevance_score >= 0.7:
                validation_stats["validation_reason"] = "sufficient_quality"
                break
                
            if not gaps:
                # No specific gaps identified but still insufficient
                if len(relevant_sources) < min_relevant_sources:
                    gaps = [f"Mehr Informationen zu: {query}"]
            
            # Search for additional sources to fill gaps
            new_sources = self._search_for_gaps(query, gaps, iteration, validation_stats)
            if not new_sources:
                validation_stats["validation_reason"] = "no_additional_sources"
                break
                
            sources = new_sources  # Update sources for next iteration
            iteration += 1
            validation_stats["iterations"] = iteration
        
        validation_stats["final_sources"] = len(relevant_sources)
        
        # Remove duplicates and limit to reasonable number
        deduplicated_sources = self._deduplicate_sources(relevant_sources)
        
        return deduplicated_sources[:15], validation_stats  # Max 15 sources to avoid context overflow

    def _validate_source_relevance(self, query: str, sources: List[Source]) -> Dict[str, Any]:
        """Use LLM to validate if sources are relevant and identify information gaps.
        
        SOTA Fix 2026-03-09:
        Root-cause of consistent 0/N relevant sources was CoT model (Magistral)
        generating extensive chain-of-thought reasoning that exhausted the
        max_tokens budget (512) before emitting the structured output format
        (RELEVANTE_QUELLEN: / GESAMTRELEVANZ: / LÜCKEN:).
        
        Fix:
        1. System prompt forces structured-only output (no reasoning)
        2. max_tokens increased to 1024 for safety margin
        3. Robust regex parsing searches ANYWHERE in response (not just line-prefix)
        4. Parsing-failure detection with diagnostic logging
        5. If LLM responds but parsing extracts nothing → all sources pass at 0.5
           (prevents cascading gap-search loops that waste 3-5 min each)
        """
        if not sources:
            return {"relevant_sources": [], "overall_relevance": 0.0, "information_gaps": []}
        
        # Prepare sources summary for LLM evaluation
        sources_text = ""
        for i, source in enumerate(sources, 1):
            title = source.title or "Ohne Titel"
            snippet = source.snippet or ""
            url = source.url or ""
            sources_text += f"{i}. {title}\n"
            if snippet:
                sources_text += f"   Inhalt: {snippet[:600]}...\n"
            sources_text += f"   URL: {url}\n\n"
        
        validation_prompt = f"""Analysiere ob die folgenden Quellen relevant für diese Frage sind.

FRAGE: {query}

QUELLEN:
{sources_text}

Antworte SOFORT und NUR in diesem exakten Format (3 Zeilen, KEINE Erklärungen):
RELEVANTE_QUELLEN: 1,3,5
GESAMTRELEVANZ: 0.8
LÜCKEN: Was noch fehlt"""

        try:
            response = self.model_loader.generate_response(
                messages=[
                    {"role": "system", "content": "Du gibst NUR strukturierte Antworten im geforderten Format aus. KEINE Erklärungen, KEINE Begründungen, KEIN Reasoning. Nur die 3 Zeilen: RELEVANTE_QUELLEN, GESAMTRELEVANZ, LÜCKEN."},
                    {"role": "user", "content": validation_prompt}
                ],
                max_tokens=1024,
                temperature=0.1,
                image_path=None
            )
            
            # ── Robust regex parsing -- searches ANYWHERE in response ──
            # Handles CoT models that may prefix reasoning before structured output
            relevant_indices = []
            relevance_score = 0.0
            gaps = []
            
            # 1. Parse RELEVANTE_QUELLEN -- find comma-separated numbers after keyword
            rq_match = re.search(r'RELEVANTE_QUELLEN\s*:\s*(.+?)(?:\n|$)', response, re.IGNORECASE)
            if rq_match:
                indices_str = rq_match.group(1).strip()
                if indices_str.lower() not in ("keine", "none", ""):
                    # Extract all integers from the match
                    relevant_indices = [int(x) - 1 for x in re.findall(r'\d+', indices_str)]
            
            # 2. Parse GESAMTRELEVANZ -- find float after keyword
            gr_match = re.search(r'GESAMTRELEVANZ\s*:\s*([0-9]*\.?[0-9]+)', response, re.IGNORECASE)
            if gr_match:
                relevance_score = float(gr_match.group(1))
                # Clamp to [0, 1]
                relevance_score = max(0.0, min(1.0, relevance_score))
            
            # 3. Parse LÜCKEN -- text after keyword
            gaps_match = re.search(r'LÜCKEN\s*:\s*(.+?)(?:\n|$)', response, re.IGNORECASE)
            if gaps_match:
                gaps_str = gaps_match.group(1).strip()
                if gaps_str.lower() not in ("keine", "keine lücken", "vollständig", "none", ""):
                    gaps = [gap.strip() for gap in gaps_str.split(',') if gap.strip()]
            
            # ── Parsing-failure detection ──
            # If LLM produced a response but we couldn't parse ANY structured fields,
            # the CoT likely consumed all tokens. Log diagnostic and pass all sources.
            parsed_any = rq_match is not None or gr_match is not None
            
            if not parsed_any and response and len(response.strip()) > 10:
                logger.warning(
                    f"Source-Validation Parsing-Failure: LLM antwortete ({len(response)} chars) "
                    f"aber kein RELEVANTE_QUELLEN/GESAMTRELEVANZ gefunden. "
                    f"Response-Anfang: {response[:200]!r}"
                )
                # Fallback: treat all sources as potentially relevant
                # This prevents the destructive gap-search cascade
                return {
                    "relevant_sources": sources,
                    "overall_relevance": 0.5,
                    "information_gaps": []
                }
            
            # Filter relevant sources by parsed indices
            relevant_sources = []
            for idx in relevant_indices:
                if 0 <= idx < len(sources):
                    relevant_sources.append(sources[idx])
            
            logger.debug(
                f"Source-Validation parsed: {len(relevant_indices)} relevant indices, "
                f"score={relevance_score:.2f}, gaps={len(gaps)}"
            )
            
            return {
                "relevant_sources": relevant_sources,
                "overall_relevance": relevance_score,
                "information_gaps": gaps
            }
            
        except (ConnectionError, TimeoutError) as e:
            logger.warning(f"LLM-Verbindungsfehler bei Source-Validation: {e}")
            return {
                "relevant_sources": sources,
                "overall_relevance": 0.5,
                "information_gaps": []
            }
        except (ValueError, TypeError) as e:
            logger.warning(f"LLM-Antwortformat-Fehler bei Source-Validation: {e}")
            return {
                "relevant_sources": sources,
                "overall_relevance": 0.5,
                "information_gaps": []
            }
        except AttributeError as e:
            logger.debug(f"Source-Validation-Attributfehler: {e}")
            return {
                "relevant_sources": sources,
                "overall_relevance": 0.5,
                "information_gaps": []
            }
        except Exception as e:
            logger.error(f"Unerwarteter Fehler bei Source-Validation: {type(e).__name__}: {e}")
            import traceback
            logger.debug(f"Source-Validation-Fehler Traceback:\n{traceback.format_exc()}")
            return {
                "relevant_sources": sources,
                "overall_relevance": 0.5,
                "information_gaps": []
            }

    def _search_for_gaps(self, original_query: str, gaps: List[str], iteration: int, 
                        validation_stats: Dict[str, Any]) -> List[Source]:
        """Search for additional sources to fill identified information gaps."""
        new_sources = []
        
        for gap in gaps[:3]:  # Limit to 3 gaps per iteration
            # Create focused search query
            focused_query = f"{original_query} {gap}"
            
            logger.info(f"Searching for gap: {gap}")
            
            # Try RAG search first (faster)
            if self.rag_enabled:
                try:
                    # NEW 2025-10-11: Pass FAISS confidence if set
                    # Use explicit parameters instead of dict unpacking for type safety
                    faiss_conf = self.faiss_min_confidence if self.faiss_min_confidence is not None else None
                    rag_result = self.tools.rag_search(
                        focused_query, 
                        k=3, 
                        min_score=self.rag_min_score,
                        faiss_min_confidence=faiss_conf
                    )
                    if rag_result.success and rag_result.results:
                        rag_sources = self.tools.to_sources([rag_result], top_k=3)
                        new_sources.extend(rag_sources)
                        validation_stats["rag_searches"] += 1
                        logger.info(f"Found {len(rag_sources)} RAG sources for gap: {gap}")
                except (ConnectionError, TimeoutError) as e:
                    logger.warning(f"RAG-Verbindungsfehler bei Gap-Suche: {e}")
                except (ValueError, TypeError) as e:
                    logger.warning(f"RAG-Eingabefehler bei Gap-Suche: {e}")
                except AttributeError as e:
                    logger.debug(f"RAG-URL-Status-Methode nicht verfügbar für Gap '{gap}': {e}")
                except Exception as e:
                    logger.error(f"Unerwarteter RAG-Fehler bei Gap-Suche: {type(e).__name__}: {e}")
            # Then try web search for fresh information (only for WEB_REQUIRED route)
            route_for_gap = self._decide_retrieval_route(original_query).route
            if route_for_gap == RetrievalRoute.WEB_REQUIRED:
                try:
                    web_call = ToolCall(tool="web_search", parameters={"query": focused_query, "num_results": self.web_search_k})
                    web_results = self.tools.run([web_call])
                    if web_results and web_results[0].success and web_results[0].results:
                        web_sources = self.tools.to_sources(web_results, top_k=3)
                        new_sources.extend(web_sources)
                        validation_stats["web_searches"] += 1
                        logger.info(f"Found {len(web_sources)} web sources for gap: {gap}")
                        
                        # Store web results in RAG for future use (async)
                        if self.rag_persist_from_web:
                            try:
                                self._submit_persist_web_to_rag(web_results[0].results)
                            except AttributeError as e:
                                logger.debug(f"RAG-Persistierung nicht verfügbar: {e}")
                            except Exception as e:
                                logger.warning(f"RAG-Persistierung-Submit fehlgeschlagen: {type(e).__name__}: {e}")
                                
                except (ConnectionError, TimeoutError) as e:
                    logger.warning(f"Web-Such-Verbindungsfehler bei Gap-Suche: {e}")
                except (ValueError, TypeError) as e:
                    logger.warning(f"Web-Such-Eingabefehler bei Gap-Suche: {e}")
                except AttributeError as e:
                    logger.debug(f"Web-Tools-Fehler bei Gap-Suche: {e}")
                except Exception as e:
                    logger.error(f"Unerwarteter Web-Such-Fehler bei Gap-Suche: {type(e).__name__}: {e}")
                    logger.warning(f"Web search for gap failed: {e}")
            else:
                logger.info("[RetrievalRouter] web_search skipped in gap search (route=%s)", route_for_gap.value)
        
        return new_sources

    def _comprehensive_source_search(self, query: str, validation_stats: Dict[str, Any]) -> Tuple[List[Source], Dict[str, Any]]:
        """Perform comprehensive search when no initial sources are found."""
        sources = []
        
        route_decision = self._decide_retrieval_route(query)

        # 1. Web search with multiple strategies (only if WEB_REQUIRED)
        search_variants = [
            query,
            f"{query} explanation",
            f"{query} guide tutorial",
            f"how to {query}" if not query.lower().startswith(("how", "what", "why", "when", "where")) else query
        ]
        
        if route_decision.route == RetrievalRoute.WEB_REQUIRED:
            for variant in search_variants[:2]: # Limit to avoid too many requests
                try:
                    web_call = ToolCall(tool="web_search", parameters={"query": variant, "num_results": self.web_search_k})
                    web_results = self.tools.run([web_call])
                    if web_results and web_results[0].success and web_results[0].results:
                        web_sources = self.tools.to_sources(web_results, top_k=5)
                        sources.extend(web_sources)
                        validation_stats["web_searches"] += 1
                        
                        # Store in RAG (async)
                        if self.rag_persist_from_web:
                            try:
                                self._submit_persist_web_to_rag(web_results[0].results)
                            except AttributeError as e:
                                logger.debug(f"RAG-Persistierung nicht verfügbar bei comprehensive search: {e}")
                            except Exception as e:
                                logger.warning(f"RAG-Persistierung fehlgeschlagen bei comprehensive search: {type(e).__name__}: {e}")
                                logger.warning(f"Failed to persist web results: {e}")
                except (ConnectionError, TimeoutError) as e:
                    logger.warning(f"Comprehensive Web-Verbindungsfehler für '{variant}': {e}")
                except (ValueError, TypeError) as e:
                    logger.warning(f"Comprehensive Web-Eingabefehler für '{variant}': {e}")
                except AttributeError as e:
                    logger.debug(f"Web-Tools-Fehler für '{variant}': {e}")
                except Exception as e:
                    logger.error(f"Comprehensive Web-Fehler für '{variant}': {type(e).__name__}: {e}")
                    logger.warning(f"Comprehensive web search failed for '{variant}': {e}")
        else:
            logger.info("[RetrievalRouter] comprehensive web search skipped (route=%s)", route_decision.route.value)
        
        # 2. RAG search with expanded queries
        if self.rag_enabled:
            for variant in search_variants[:2]:
                try:
                    # NEW 2025-10-11: Pass FAISS confidence if set
                    # Use explicit parameters instead of dict unpacking for type safety
                    faiss_conf = self.faiss_min_confidence if self.faiss_min_confidence is not None else None
                    rag_result = self.tools.rag_search(
                        variant, 
                        k=5, 
                        min_score=self.rag_min_score,
                        faiss_min_confidence=faiss_conf
                    )
                    if rag_result.success and rag_result.results:
                        rag_sources = self.tools.to_sources([rag_result], top_k=5)
                        sources.extend(rag_sources)
                        validation_stats["rag_searches"] += 1
                except (ConnectionError, TimeoutError) as e:
                    logger.warning(f"Comprehensive RAG-Verbindungsfehler für '{variant}': {e}")
                except (ValueError, TypeError) as e:
                    logger.warning(f"Comprehensive RAG-Eingabefehler für '{variant}': {e}")
                except AttributeError as e:
                    logger.debug(f"RAG-Tools-Fehler für '{variant}': {e}")
                except Exception as e:
                    logger.error(f"Comprehensive RAG-Fehler für '{variant}': {type(e).__name__}: {e}")
                    logger.warning(f"Comprehensive RAG search failed for '{variant}': {e}")
        
        validation_stats["validation_reason"] = "comprehensive_search"
        validation_stats["final_sources"] = len(sources)
        
        return self._deduplicate_sources(sources)[:10], validation_stats

    def _deduplicate_sources(self, sources: List[Source]) -> List[Source]:
        """Delegiert an SourceManager für konsistente Deduplizierung"""
        return self.source_manager.deduplicate_sources(sources)

    def _submit_persist_web_to_rag(self, web_results: List[Dict[str, Any]]) -> None:
        """Submit web persist work to background executor (off critical path).

        Root-Cause-Fix 2026-06-02:
        Die frisch persistierten Chunks sind im selben Turn ohnehin nicht
        über FAISS retrievebar (FAISS-Auto-Rebuild ist threshold-basiert).
        Das schwere Pipeline-Working (Trafilatura + Docling + Embeddings +
        LLM-KG-Extraktion + Entity-Resolution) gehört darum nicht auf den
        Antwortpfad. Persistierung läuft im Hintergrund-Worker; der User
        bekommt die Antwort sofort, der RAG-Store wird für künftige Turns
        angereichert.
        """
        if not web_results:
            return
        executor = self._persist_executor
        if executor is None:  # Falls bereits heruntergefahren
            try:
                self._persist_web_to_rag(web_results)
            except Exception as e:
                logger.warning(f"Inline-Persist fallback fehlgeschlagen: {e}")
            return
        try:
            future = executor.submit(self._persist_web_to_rag, list(web_results))
            future.add_done_callback(self._on_persist_done)
        except RuntimeError as e:
            # Executor wurde während der Anfrage geschlossen → inline fallback
            logger.debug(f"Persist-Executor nicht mehr aktiv: {e}, inline persist")
            try:
                self._persist_web_to_rag(web_results)
            except Exception as exc:
                logger.warning(f"Inline-Persist fallback fehlgeschlagen: {exc}")

    @staticmethod
    def _on_persist_done(future) -> None:
        """Log exceptions from background persist tasks (würden sonst stumm sterben)."""
        exc = future.exception()
        if exc is not None:
            logger.warning(
                "Background-RAG-Persist fehlgeschlagen: %s: %s",
                type(exc).__name__, exc,
            )

    def _shutdown_persist_executor(self, timeout: float = 30.0) -> None:
        """Atexit-Hook: warte begrenzt auf laufende Persist-Tasks."""
        executor = self._persist_executor
        if executor is None:
            return
        self._persist_executor = None
        try:
            # Python 3.9+: shutdown akzeptiert wait + cancel_futures.
            # Wir warten begrenzt auf laufende Tasks, queue-Tasks werden
            # verworfen (sie würden ohnehin nach Prozess-Ende keinen
            # Konsumenten mehr finden).
            executor.shutdown(wait=True, cancel_futures=True)
        except TypeError:
            executor.shutdown(wait=True)
        except Exception as e:
            logger.debug(f"Persist-Executor-Shutdown-Fehler: {e}")

    def _persist_web_to_rag(self, web_results: List[Dict[str, Any]]) -> None:
        """Store web search results in RAG for future use.
        
        PERFORMANCE OPTIMIZED 2025:
        - URL Deduplication: Skip already processed URLs in session
        - Threading Lock: Prevent concurrent database conflicts
        - Full content extraction with Trafilatura + LLM cleaning (quality preserved)

        2026-06-02: Wird nicht mehr direkt aus dem Antwortpfad aufgerufen,
        sondern über `_submit_persist_web_to_rag` in einen Hintergrund-Worker
        gepostet. Der Body ist unverändert; das Lock + Dedup-Set sind weiterhin
        thread-safe (relevant falls eine zukünftige Konfiguration den Worker
        auf >1 Threads erhöht).
        """
        if not web_results:
            return
        
        start_time = time.time()
        self.url_processing_stats['total_requests'] += 1
        
        # STEP 1: Acquire processing lock to prevent database conflicts
        with self.url_processing_lock:
            lock_acquired_time = time.time()
            if lock_acquired_time - start_time > 0.1:  # Log if we waited >100ms
                self.url_processing_stats['concurrent_blocks'] += 1
                logger.debug(f"URL processing lock acquired after {(lock_acquired_time - start_time)*1000:.1f}ms wait")
            
            # STEP 2: URL Deduplication - filter already processed URLs
            urls_to_process = []
            dedup_saves = 0
            
            for result in web_results:
                url = result.get("url", "")
                if not url or not url.startswith(('http://', 'https://')):
                    continue
                
                # Check if URL was already processed in this session
                if url in self.processed_urls:
                    dedup_saves += 1
                    logger.debug(f"DEDUP SKIP: {url}")
                    continue
                
                urls_to_process.append(result)
                self.processed_urls.add(url)  # Mark as processed
            
            # Update deduplication stats
            self.url_processing_stats['deduplication_saves'] += dedup_saves
            
            if dedup_saves > 0:
                logger.info(f"URL Deduplication: Skipped {dedup_saves} already processed URLs, processing {len(urls_to_process)} new URLs")
            else:
                logger.info(f"Persisting {len(urls_to_process)} new web results to RAG with full content extraction")
            
            if not urls_to_process:
                logger.info("All URLs already processed - skipping persist operation")
                return
        
            # STEP 3: Process remaining URLs with full content extraction
            for result in urls_to_process:
                url = result.get("url", "")
                
                # Check current import status for this URL to prefer full content
                rag_store = getattr(self.tools, 'rag', None)
                status = None
                try:
                    if rag_store and hasattr(rag_store, 'get_url_import_status'):
                        status = rag_store.get_url_import_status(url)
                except (ConnectionError, TimeoutError) as e:
                    logger.warning(f"RAG-Verbindungsfehler bei URL-Status-Abfrage für {url}: {e}")
                except AttributeError as e:
                    logger.debug(f"RAG-URL-Status-Methode nicht verfügbar für {url}: {e}")
                except (ValueError, TypeError) as e:
                    logger.debug(f"RAG-URL-Status-Eingabefehler für {url}: {e}")
                except Exception as e:
                    logger.warning(f"Unerwarteter Fehler bei URL-Status-Abfrage für {url}: {type(e).__name__}: {e}")
                    logger.debug(f"get_url_import_status failed for {url}: {e}")
                
                # If already fully imported, skip (additional safety check)
                if status == 'full':
                    logger.info(f"Skip persist: already fully imported {url}")
                    continue
                    
                # Try to extract full content using upsert_url (QUALITY PRESERVED)
                try:
                    if rag_store and hasattr(rag_store, 'upsert_url'):
                        success = rag_store.upsert_url(
                            url, 
                            metadata={
                                "title": result.get("title", ""),
                                "date": result.get("date", ""),
                                "source_type": "web_full",
                                "content_type": "text/html",
                                "original_snippet": result.get("snippet", ""),
                                "search_timestamp": datetime.now().isoformat(),
                                "canonical_url": url,
                            },
                            include_tables=True,
                            include_links=False,
                            timeout=5,  # FAST timeout - only 5 seconds!
                            chunk_size=self.rag_chunk_size,
                            chunk_overlap=self.rag_chunk_overlap,
                        )
                        
                        if success:
                            logger.info(f"Successfully extracted full content from: {url}")
                            # upsert_url will clean up snippet-only entries automatically
                            continue
                            
                except (ConnectionError, TimeoutError) as e:
                    logger.warning(f"Content-Extraction-Verbindungsfehler für {url}: {e}")
                except (ValueError, TypeError) as e:
                    logger.warning(f"Content-Extraction-Eingabefehler für {url}: {e}")
                except AttributeError as e:
                    logger.warning(f"RAG-Upsert-URL-Methode nicht verfügbar für {url}: {e}")
                except Exception as e:
                    logger.error(f"Unerwarteter Content-Extraction-Fehler für {url}: {type(e).__name__}: {e}")
                    logger.warning(f"Full content extraction failed for {url}: {e}")
                
                # Fallback: Store snippet only if we don't already have a snippet-only entry
                if status == 'snippet_only':
                    logger.info(f"Skip snippet fallback (snippet already exists) for: {url}")
                    continue
                
                snippet = result.get("snippet", "")
                if snippet:
                    # Web-Compliance-Gate (2026-08-30, docs/18_LEGAL_WEB_PERSIST.md):
                    # Snippets sind web-derived Content → robots.txt / X-Robots-Tag /
                    # no-store blockieren die Persistierung hart (fail-open bei
                    # Fetch-Fehlern). source_type wird als "web_snippet" geführt,
                    # damit das Retention-Pruning greift.
                    if not web_compliance.gate_persistence("orchestrator.snippet_fallback", url):
                        continue
                    logger.info(f"Fallback to snippet for: {url}")
                    meta = {
                        "title": result.get("title", ""),
                        "url": url,
                        "canonical_url": url,
                        "date": result.get("date", ""),
                        "source_type": "web_snippet",
                        "content_type": "text/html",
                        "search_timestamp": datetime.now().isoformat(),
                    }
                    retention_until = web_compliance.retention_until_iso()
                    if retention_until:
                        meta["retention_until"] = retention_until
                    self.tools.rag_upsert_documents([{
                        "text": snippet,
                        "metadata": meta,
                    }])
            
            # STEP 4: Log performance stats
            total_time = time.time() - start_time
            self.url_processing_stats['processing_times'].append(total_time)
            
            # Keep only last 100 processing times for moving average
            if len(self.url_processing_stats['processing_times']) > 100:
                self.url_processing_stats['processing_times'] = self.url_processing_stats['processing_times'][-100:]
            
            avg_time = sum(self.url_processing_stats['processing_times']) / len(self.url_processing_stats['processing_times'])
            
            logger.info(f"URL Processing completed in {total_time:.2f}s (avg: {avg_time:.2f}s), "
                       f"Total session savings: {self.url_processing_stats['deduplication_saves']} deduplicated URLs")

    # ─── SOTA Pipeline Enhancement (PHASE 7) ───────────────────────────
    def _run_sota_enhancement(self, query: str, sources: List[Source],
                              results, trace) -> Dict[str, Any]:
        """
        Integrates retrieval-side SOTA telemetry into the main inference path.

        MultimodalRAG query expansion is applied before retrieval. StrixKAT answer
        evaluation runs after answer synthesis. This hook keeps source freshness
        telemetry from ChangeDetector and pipeline health visible in the trace.

        Returns dict with keys:
            optimized_sources  - filtered/reordered sources
            novelty_score      - content novelty (0-1)
            quality_score      - reserved for post-answer evaluation (1.0 here)
            metrics            - dict of SOTA telemetry for trace
        """
        enhancement: Dict[str, Any] = {}

        # --- Step 1: ChangeDetector novelty scoring -------------------
        novelty_score = 0.0
        change_detector = getattr(self, "change_detector", None)
        if change_detector:
            try:
                scorer = change_detector.get_novelty_score(query)
                novelty_score = float(scorer) if scorer is not None else 0.0
                logger.debug("[SOTA] NoveltyScore for query: %.3f", novelty_score)
            except Exception as _e:
                logger.debug("[SOTA] NoveltyScore unavailable: %s", _e)

        # --- Step 2: Source optimization placeholder ------------------
        optimized = list(sources)

        # --- Step 3: Pipeline health telemetry ------------------------
        quality_score = 1.0
        pipeline_metrics: Dict[str, Any] = {}
        pipeline = self.sota_pipeline
        if pipeline is not None:
            try:
                pipeline_metrics = pipeline.get_metrics()
            except Exception as _e:
                logger.debug("[SOTA] Pipeline metrics unavailable: %s", _e)

        enhancement["optimized_sources"] = optimized
        enhancement["novelty_score"] = novelty_score
        enhancement["quality_score"] = quality_score
        enhancement["metrics"] = {
            "sota_novelty": novelty_score,
            "sota_quality": quality_score,
            "sota_sources_original": len(sources),
            "sota_sources_optimized": len(optimized),
            "sota_pipeline_runs": int(pipeline_metrics.get("pipeline_runs", 0)) if pipeline_metrics else 0,
            "sota_pipeline_documents_processed": int(pipeline_metrics.get("documents_processed", 0)) if pipeline_metrics else 0,
            "sota_pipeline_last_quality_score": pipeline_metrics.get("last_quality_score") if pipeline_metrics else None,
        }
        enhancement["original_sources"] = sources

        return enhancement

    def _maybe_persist_sources_to_rag(self, sources: List[Source]) -> None:
        """Delegiert an SourceManager für RAG-Persistence"""
        self.source_manager.maybe_persist_sources_to_rag(sources)

    # PHASE 4 STEP 6: Citation & Source formatting moved to ResponseBuilder
    # Legacy methods removed: _augment_citations, _append_sources_block,
    # _format_source_entry, _filter_actually_used_sources
    # All functionality now in response_builder.py

    def _is_answer_based_on_general_knowledge(self, text: str) -> bool:
        """Check if the answer is based on general knowledge rather than provided sources."""
        if not text:
            return False
            
        # Indicators that answer is from general knowledge
        general_knowledge_indicators = [
            "aus allgemeinem wissen",
            "allgemein bekannt",
            "basiert auf allgemeinem wissen",
            "ohne spezifische quellen",
            "allgemeine information",
            "bekanntermaßen",
            "es ist bekannt",
            "historisch gesehen",
            "allgemeine fakten",
            "allgemein anerkannt"
        ]
        
        text_lower = text.lower()
        return any(indicator in text_lower for indicator in general_knowledge_indicators)

    # PHASE 4: _generate_subqueries removed - now using QueryStrategyManager

    def set_user_system_prompt(self, prompt: str):
        """Setze User-System-Prompt aus GUI"""
        self.user_system_prompt = prompt or ""
        logger.debug(f"User system prompt set: {self.user_system_prompt[:100]}...")

    def _create_enhanced_system_prompt(self, base_prompt: str) -> str:
        """Kombiniere User System-Prompt mit Base-Prompt"""
        if self.user_system_prompt:
            return f"{self.user_system_prompt}\n\n{base_prompt}"
        return base_prompt

    def _call_model(self, messages: List[Dict[str, Any]], max_tokens: int = 1024, temperature: float = 0.2) -> str:
        """Zentrale LLM-Call Methode mit User System-Prompt Integration"""
        # Erweitere System-Prompt falls User-Prompt gesetzt und System-Message vorhanden
        enhanced_messages = messages.copy()
        if enhanced_messages and enhanced_messages[0].get("role") == "system":
            original_content = enhanced_messages[0]["content"]
            enhanced_content = self._create_enhanced_system_prompt(original_content)
            enhanced_messages[0] = {"role": "system", "content": enhanced_content}
        
        return self.model_loader.generate_response(
            messages=enhanced_messages, 
            max_tokens=max_tokens, 
            temperature=temperature, 
            image_path=None
        )

    def _looks_internal_only_query(self, query: str) -> bool:
        """Fast heuristic for queries that do not require retrieval tools."""
        q = (query or "").strip().lower()
        if not q:
            return True
        smalltalk_patterns = [
            r"^(hi|hallo|hey|moin|guten\s+tag|danke|thx|ok|okay)[!.?\s]*$",
            r"^(wie\s+geht\s+es\s+dir)[?.!\s]*$",
        ]
        for pattern in smalltalk_patterns:
            if re.match(pattern, q):
                return True
        # Simple arithmetic can be solved locally without retrieval.
        if re.fullmatch(r"[\d\s+\-*/().,=]+", q):
            return True
        return False

    def _decide_retrieval_route(
        self,
        query: str,
        time_context: Optional[str] = None,
    ) -> RetrievalRoutingDecision:
        """
        Decide retrieval route with hard constraints.

        Route semantics:
          - INTERNAL_ONLY: no web_search and no rag_search
          - RAG_REQUIRED: rag_search allowed/required, web_search blocked
          - WEB_REQUIRED: web_search required (unless APP_LOCAL_ONLY)

        Adaptive-RAG extension (SOTA 2026-07-31):
          When self.adaptive_strategy is True, the AdaptiveRAGRouter evaluates
          query complexity and sets decision.depth to "shallow" or "deep".
          Deep queries trigger MultiHopRetriever in _apply_retrieval_route().
        """
        if not self.retrieval_router_enabled:
            return RetrievalRoutingDecision(
                route=RetrievalRoute.RAG_REQUIRED if self.rag_enabled else RetrievalRoute.INTERNAL_ONLY,
                reason="router_disabled",
                confidence=0.0,
                focused_query=(query or "").strip(),
            )

        if self._looks_internal_only_query(query):
            return RetrievalRoutingDecision(
                route=RetrievalRoute.INTERNAL_ONLY,
                reason="smalltalk_or_local_reasoning",
                confidence=0.9,
                focused_query=(query or "").strip(),
            )

        if self.local_only_mode:
            return RetrievalRoutingDecision(
                route=RetrievalRoute.RAG_REQUIRED if self.rag_enabled else RetrievalRoute.INTERNAL_ONLY,
                reason="app_local_only",
                confidence=1.0,
                focused_query=(query or "").strip(),
            )

        assessment = self._assess_live_data_need(query, time_context=time_context)
        if bool(assessment.get("requires_web")) and float(assessment.get("confidence", 0.0) or 0.0) >= 0.35:
            return RetrievalRoutingDecision(
                route=RetrievalRoute.WEB_REQUIRED,
                reason=str(assessment.get("reason") or "semantic_live_data_required"),
                confidence=float(assessment.get("confidence", 0.0) or 0.0),
                focused_query=str(assessment.get("focused_query") or query).strip(),
            )

        # --- Adaptive-RAG: determine depth for RAG routes ---
        depth: Optional[str] = None
        if self.adaptive_strategy and self._adaptive_router is not None:
            try:
                router_decision = self._adaptive_router.route(query)
                depth = router_decision.depth.value  # "shallow" or "deep"
                logger.debug(
                    "[Adaptive-RAG] depth=%s conf=%.2f for query=%s",
                    depth, router_decision.confidence, query[:80],
                )
            except Exception as e:
                logger.debug("[Adaptive-RAG] route() failed: %s", e)

        return RetrievalRoutingDecision(
            route=RetrievalRoute.RAG_REQUIRED if self.rag_enabled else RetrievalRoute.INTERNAL_ONLY,
            reason="default_rag_first",
            confidence=0.6,
            focused_query=(query or "").strip(),
            depth=depth,
        )

    def _apply_retrieval_route(
        self,
        planned_calls: List[ToolCall],
        decision: RetrievalRoutingDecision,
    ) -> List[ToolCall]:
        """Apply route constraints to planned calls (hard enforcement)."""
        calls = list(planned_calls or [])

        if decision.route == RetrievalRoute.INTERNAL_ONLY:
            filtered = [c for c in calls if c.tool not in {"web_search", "rag_search"}]
            self._last_retrieval_route = decision.route
            return filtered

        if decision.route == RetrievalRoute.RAG_REQUIRED:
            filtered = [c for c in calls if c.tool != "web_search"]
            has_rag = any(c.tool == "rag_search" for c in filtered)
            if self.rag_enabled and not has_rag:
                filtered.append(ToolCall(tool="rag_search", parameters={"query": decision.focused_query or "", "k": self.rag_k}))
            self._last_retrieval_route = decision.route
            return filtered

        # WEB_REQUIRED
        filtered = list(calls)
        has_web = any(c.tool == "web_search" for c in filtered)
        if not self.local_only_mode and not has_web:
            filtered.append(
                ToolCall(
                    tool="web_search",
                    parameters={"query": decision.focused_query or "", "num_results": self.web_search_k},
                )
            )
        self._last_retrieval_route = decision.route
        return filtered

    def _generate_retry_query_from_verification(
        self,
        query: str,
        verification_result: Any,
        attempt: int,
    ) -> str:
        """Generate an alternative retrieval query from verification failure signals."""
        issues = "; ".join(getattr(verification_result, "issues", [])[:5])
        warnings = "; ".join(getattr(verification_result, "warnings", [])[:5])
        fallback = f"{query} alternative source perspective"
        try:
            prompt = (
                "Erzeuge eine EINZIGE alternative Suchanfrage für Retrieval. "
                "Ziel: schließe Evidenzlücken und reduziere Halluzinationsrisiko. "
                "Nur die Query ausgeben, ohne Erklärungen.\n\n"
                f"Originalfrage: {query}\n"
                f"Attempt: {attempt}\n"
                f"Verification Issues: {issues}\n"
                f"Verification Warnings: {warnings}\n"
            )
            out = self._call_model(
                messages=[
                    {"role": "system", "content": "Du bist ein Retrieval-Query-Refiner. Antworte nur mit einer Suchanfrage."},
                    {"role": "user", "content": prompt},
                ],
                max_tokens=80,
                temperature=0.0,
            )
            candidate = (out or "").strip().splitlines()[0].strip()
            return candidate if len(candidate) >= 3 else fallback
        except Exception as exc:
            logger.debug("CRAG retry-query generation failed: %s", exc)
            return fallback

    def _run_crag_self_correction(
        self,
        query: str,
        history: List[Dict[str, Any]],
        extras: List[str],
        current_sources: List[Source],
        current_results: List[ToolResult],
        current_verification_result: Optional[Any],
        *,
        fallback: bool,
    ) -> Optional[Tuple[str, Optional[Any], List[Source], List[ToolResult]]]:
        """
        CRAG loop: Verify -> Fail -> Generate new query -> Retry retrieval (max 2).

        Returns updated (final_text, verification_result, sources, results) on improvement,
        otherwise None.
        """
        if not self.crag_self_correction_enabled or current_verification_result is None:
            return None

        grounding = float(getattr(current_verification_result, "grounding_score", 1.0) or 1.0)
        if grounding >= self.crag_grounding_threshold:
            return None

        route_decision = self._decide_retrieval_route(query)
        if route_decision.route == RetrievalRoute.INTERNAL_ONLY:
            logger.info("[CRAG] Skip retries: route=INTERNAL_ONLY")
            return None

        best_final_text: Optional[str] = None
        best_verification_result: Optional[Any] = current_verification_result
        updated_sources = list(current_sources)
        updated_results = list(current_results)

        for attempt in range(1, self.crag_max_retries + 1):
            retry_query = self._generate_retry_query_from_verification(
                query=query,
                verification_result=best_verification_result,
                attempt=attempt,
            )

            retry_calls: List[ToolCall] = []
            if route_decision.route in {RetrievalRoute.RAG_REQUIRED, RetrievalRoute.WEB_REQUIRED} and self.rag_enabled:
                retry_calls.append(ToolCall(tool="rag_search", parameters={"query": retry_query, "k": self.rag_k}))
            if route_decision.route == RetrievalRoute.WEB_REQUIRED and not self.local_only_mode:
                retry_calls.append(ToolCall(tool="web_search", parameters={"query": retry_query, "num_results": self.web_search_k}))

            if not retry_calls:
                logger.info("[CRAG] No retry calls produced for route=%s", route_decision.route.value)
                break

            logger.info("[CRAG] retry %d/%d with query='%s'", attempt, self.crag_max_retries, retry_query[:120])

            # SOTA: Use Adaptive-RAG Pipeline when enabled for smarter retrieval
            if (self.adaptive_strategy
                    and self._adaptive_pipeline is not None
                    and self.rag_enabled
                    and route_decision.route in {RetrievalRoute.RAG_REQUIRED, RetrievalRoute.WEB_REQUIRED}):
                try:
                    import time as _time
                    _t0 = _time.perf_counter()
                    _adapt_result = self._adaptive_pipeline.execute(
                        query=retry_query,
                    )
                    _adapt_ms = int((_time.perf_counter() - _t0) * 1000)
                    logger.info(
                        "[CRAG] AdaptiveRAG route=%s hops=%d in %d ms",
                        _adapt_result.route.value,
                        _adapt_result.hops_used,
                        _adapt_ms,
                    )
                    # Convert AdaptiveRAGResult evidence_texts→evidence_scores into ToolResults
                    _adapt_tool_results: List[ToolResult] = []
                    for _idx, (_txt, _sc) in enumerate(zip(_adapt_result.evidence_texts, _adapt_result.evidence_scores)):
                        _adapt_tool_results.append(
                            ToolResult(
                                tool="rag_search",
                                success=True,
                                text=_txt,
                                meta={"score": _sc, "adaptive_hop": _idx, "crag_retry": attempt},
                            )
                        )
                    updated_results.extend(_adapt_tool_results)
                except Exception as _e:
                    logger.warning("[CRAG] AdaptiveRAG fehlgeschlagen, fallback zu tools.run: %s", _e)
                    # Fallback to plain tools.run
                    _plain_results = self.tools.run(retry_calls)
                    updated_results.extend(_plain_results)
            else:
                # Plain retrieval (non-adaptive)
                _plain_results = self.tools.run(retry_calls)
                updated_results.extend(_plain_results)

            evidence_result = self.evidence_manager.select_evidence_from_tool_results(
                query=query,
                tool_results=updated_results,
                evidence_max_candidates=self.evidence_max_candidates,
                evidence_shortlist_m=self.evidence_shortlist_m,
                evidence_diversity_lambda=self.evidence_diversity_lambda,
                is_news_query=self._is_news_query(query),
                news_min_k=self.news_min_k,
                news_max_k=self.news_max_k,
                model_loader=self,
                use_llm_evidence_selection=self.use_llm_evidence_selection,
            )
            if not evidence_result.sources:
                continue

            updated_sources = evidence_result.sources
            draft_text, _ = self.summarize(query, history, updated_sources, extras, fallback=fallback)
            draft_text, _ = extract_followup_questions(draft_text or "")
            candidate_text, candidate_verification = self.verify_step(
                query=query,
                draft=draft_text,
                evidence=updated_sources,
                fallback=fallback,
            )

            if candidate_verification is None:
                continue

            best_final_text = candidate_text
            best_verification_result = candidate_verification
            new_grounding = float(getattr(candidate_verification, "grounding_score", 0.0) or 0.0)
            if new_grounding >= self.crag_grounding_threshold:
                logger.info("[CRAG] success on retry %d: grounding=%.2f", attempt, new_grounding)
                break

        if best_final_text is None:
            return None
        return best_final_text, best_verification_result, updated_sources, updated_results

    # ==================== HELPER METHODS FOR SOURCE VALIDATION ====================
    
    def _assess_live_data_need(self, query: str, time_context: Optional[str] = None) -> Dict[str, Any]:
        """Semantic assessment for whether a query requires live web data."""
        self._refresh_runtime_mode()
        cache_key = (query or "").strip().lower()
        if not cache_key:
            return {
                "requires_web": False,
                "time_critical": False,
                "news_related": False,
                "confidence": 0.0,
                "focused_query": (query or "").strip(),
                "reason": "empty_query",
            }

        cached = self._live_data_assessment_cache.get(cache_key)
        if cached is not None:
            return cached

        default_result = {
            "requires_web": False,
            "time_critical": False,
            "news_related": False,
            "confidence": 0.0,
            "focused_query": (query or "").strip(),
            "reason": "default_local_answer_ok",
        }

        if self.local_only_mode or not self.semantic_live_routing_enabled:
            self._live_data_assessment_cache[cache_key] = default_result
            return default_result

        try:
            context_block = f"Time Context: {time_context}\n" if time_context else ""
            assessment_prompt = (
                "Bewerte ausschließlich, ob für die User-Anfrage zwingend LIVE-Webdaten benötigt werden. "
                "Antworte NUR als JSON-Objekt mit Feldern: "
                "requires_web (bool), time_critical (bool), news_related (bool), confidence (0..1), "
                "focused_query (string), reason (string).\n"
                "Leitregeln:\n"
                "- requires_web=true bei aktuellen Nachrichten, laufenden Ereignissen, Live-/heutigen Fakten, "
                "oder wenn die Frage explizit aktuelle Online-Information verlangt.\n"
                "- requires_web=false bei zeitlosen Grundlagen, allgemeinem Wissen, oder rein lokalen Dokumentfragen.\n"
                "- focused_query soll kurz und suchmaschinen-tauglich sein.\n\n"
                f"{context_block}User Query: {query}"
            )

            raw = self._call_model(
                messages=[
                    {"role": "system", "content": "Du bist ein präziser Routing-Klassifikator. Keine Prosa, nur valides JSON."},
                    {"role": "user", "content": assessment_prompt},
                ],
                max_tokens=220,
                temperature=0.0,
            )

            data: Dict[str, Any] = {}
            if raw:
                parsed: Optional[Dict[str, Any]] = None
                try:
                    loaded = json.loads(raw)
                    if isinstance(loaded, dict):
                        parsed = loaded
                except Exception:
                    pass
                if parsed is None:
                    start = raw.find("{")
                    end = raw.rfind("}")
                    if start != -1 and end != -1 and end > start:
                        try:
                            loaded = json.loads(raw[start : end + 1])
                            if isinstance(loaded, dict):
                                parsed = loaded
                        except Exception:
                            parsed = None
                if parsed:
                    data = parsed

            def _as_bool(value: Any) -> bool:
                if isinstance(value, bool):
                    return value
                if isinstance(value, (int, float)):
                    return bool(value)
                if isinstance(value, str):
                    return value.strip().lower() in {"1", "true", "yes", "ja", "on"}
                return False

            def _as_conf(value: Any) -> float:
                try:
                    c = float(value)
                    return max(0.0, min(1.0, c))
                except Exception:
                    return 0.0

            result = {
                "requires_web": _as_bool(data.get("requires_web")),
                "time_critical": _as_bool(data.get("time_critical")),
                "news_related": _as_bool(data.get("news_related")),
                "confidence": _as_conf(data.get("confidence", 0.0)),
                "focused_query": (str(data.get("focused_query") or query)).strip()[:300],
                "reason": str(data.get("reason") or "semantic_assessment").strip()[:300],
            }

            self._live_data_assessment_cache[cache_key] = result
            if len(self._live_data_assessment_cache) > 512:
                # Keep cache bounded in long-running Streamlit sessions.
                oldest_key = next(iter(self._live_data_assessment_cache))
                self._live_data_assessment_cache.pop(oldest_key, None)
            return result

        except Exception as exc:
            logger.warning("Semantic live-data assessment failed: %s", exc)
            self._live_data_assessment_cache[cache_key] = default_result
            return default_result

    def _is_time_critical_query(self, query: str) -> bool:
        """Check if query likely needs time-sensitive live data."""
        return bool(self._assess_live_data_need(query).get("time_critical", False))

    def _is_news_query(self, query: str) -> bool:
        """Check if query is news-related using semantic assessment."""
        assessment = self._assess_live_data_need(query)
        return bool(assessment.get("news_related", False) or assessment.get("requires_web", False))
    
    def _tokenize(self, text: str) -> List[str]:
        """Delegiert zu SourceProcessor."""
        return self.source_processor._tokenize(text)
    
    def _overlap(self, tokens1: List[str], tokens2: List[str]) -> float:
        """Delegiert zu SourceProcessor."""
        return self.source_processor._overlap(tokens1, tokens2)
    
    def _domain_authority_score(self, url: str) -> float:
        """Delegiert zu SourceProcessor."""
        return self.source_processor.get_domain_authority(url)
    
    def _select_diverse_top_k(self, query: str, judged_sources: List[Tuple[Source, Any]], 
                             per_source_summary: Dict[str, str], k: int, 
                             lambda_rel: float = 0.7) -> List[Source]:
        """Delegiert zu SourceProcessor (MMR-basierte Diversity-Selection)."""
        return self.source_processor.select_diverse_top_k(query, judged_sources, k, lambda_rel)

    def get_url_processing_stats(self) -> Dict[str, Any]:
        """Get URL processing performance statistics for monitoring."""
        stats = self.url_processing_stats.copy()
        
        # Add computed metrics
        if stats['processing_times']:
            stats['avg_processing_time'] = sum(stats['processing_times']) / len(stats['processing_times'])
            stats['max_processing_time'] = max(stats['processing_times'])
            stats['min_processing_time'] = min(stats['processing_times'])
        else:
            stats['avg_processing_time'] = 0.0
            stats['max_processing_time'] = 0.0
            stats['min_processing_time'] = 0.0
        
        stats['processed_urls_count'] = len(self.processed_urls)
        stats['deduplication_rate'] = (
            stats['deduplication_saves'] / max(1, stats['total_requests']) * 100
        )
        
        return stats
    
    def reset_url_processing_stats(self) -> None:
        """Reset URL processing statistics and processed URLs cache."""
        self.processed_urls.clear()
        self.url_processing_stats = {
            'total_requests': 0,
            'deduplication_saves': 0,
            'concurrent_blocks': 0,
            'processing_times': []
        }
        logger.info("🔄 URL processing stats and cache reset")
