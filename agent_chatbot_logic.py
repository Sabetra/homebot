import copy
import json
import logging
import os
import queue
import re
import sys
import threading
import time
from datetime import datetime, timezone
from typing import Optional, Dict, List, Any, Iterator
from functools import lru_cache, wraps  # NEU: Für Caching und Retry-Decorator
import hashlib  # NEU: Für Cache-Keys
from utils.runtime_policy import parse_bool_env
from utils import token_scaling  # Adaptive max_tokens (Token-Skalierung, docs/20)
from chatbot_logic import ChatbotLogic  # Base ChatbotLogic mit Context Manager
from chat_context_manager import ChatContextManager  # NEU: Context Manager Import
from scripts.model_loader import LLM_CONTEXT_SIZE  # Single Source of Truth für Context-Window
from agent_toolkit import AgentToolkit
from agent.orchestrator import AgentOrchestrator
from agent.agent_types import ToolCall, AgentTrace
from agent.generic_intent_classifier import GenericIntentClassifier
from agent.react_agent import ReActAgent
from agent.streaming_events import (
    ActiveRunRegistry,
    ChatEvent,
    ChatRunResult,
    RouteSelected,
    RunCancelled,
    RunCompleted,
    RunFailed,
    RunStarted,
    SourcesUpdated,
    StepFinished,
    StepStarted,
    StreamingCancelled,
    StreamingContext,
    TextDelta,
    TextFinished,
    TextStarted,
    UsageUpdated,
)
# NEU: Intelligent Routing Integration
from agent.hybrid_search import create_hybrid_search_engine, smart_search
from agent.intelligent_routing import QueryType, get_search_strategy
from utils.token_manager import estimate_prompt_tokens, estimate_structured_output_tokens
from utils.followup_question_extractor import FOLLOWUP_PERSPECTIVE_INSTRUCTION
# NEU: Optimized Research Engine für beste Performance
from agent.optimized_research_engine import create_optimized_research_engine, optimized_research
# NEU: Psychologische Integration
from utils.wellbeing_orchestrator_integration import (
    integrate_wellbeing_orchestrator,
    set_current_user_id,
    set_wellbeing_context_enabled,
)
from config.user_id_config import get_current_user_id
# NEU: Structured Outputs (Phase 1 - Roadmap Implementation)
from llm_structured_wrapper import LLMStructuredWrapper
from llm_output_schemas import ToolExtractionOutput
# SOTA: Query Intent Classifier für Smart RAG-Routing (Branch B)
from wellbeing_session.context.query_intent_classifier import QueryIntent, QueryClassification
# RC-3 FIX: LLM-basierter Intent Classifier (ersetzt reine Regex-Klassifikation)
from wellbeing_session.context.llm_intent_classifier import classify_query_llm
# CARE_SYSTEM_PROMPT_BASE: single source of truth for the care identity.
# Used as fallback in wellbeing_chat() when ResponseGenerator is unavailable.
from wellbeing_session.handlers.response_generator import (
    CARE_SYSTEM_PROMPT_BASE,
    _normalize_role_alternation,
)
from wellbeing_session.response_provenance import (
    build_psych_web_provenance_instruction,
    finalize_wellbeing_response_provenance,
)
# SOTA Variant 4: Central, intent-aware, complexity-driven decomposition engine.
# Single source of truth for sub-query generation across main and psycho paths.
from agent.decomposition_engine import (
    DecompositionEngine,
    DecompositionResult,
    SubQuerySource,
    TaggedSubQuery,
)

# ── Module-level constants for Branch B Intent Router ──
_KG_LIMITS_BY_INTENT = {
    QueryIntent.PERSONAL: 20,   # Personal: more KG, no RAG
    QueryIntent.MIXED: 12,      # Mixed: balanced
    QueryIntent.FACTUAL: 5,     # Factual: minimal KG, full RAG
}

# NEU: Redis Semantic Caching (Tag 3 - KURZFRISTIG Priority)
# Feature Flag: REDIS_CACHING_ENABLED (Default: False für Opt-In Testing)
REDIS_CACHING_ENABLED = False
SEMANTIC_CACHE_AVAILABLE = False
get_semantic_cache: Optional[Any] = None  # Will be set if available
if REDIS_CACHING_ENABLED:
    try:
        from cache.semantic_cache import get_semantic_cache as _get_semantic_cache
        get_semantic_cache = _get_semantic_cache
        SEMANTIC_CACHE_AVAILABLE = True
        print("✅ Redis Semantic Cache erfolgreich importiert")
    except ImportError as e:
        SEMANTIC_CACHE_AVAILABLE = False
        print(f"⚠️ Redis Semantic Cache nicht verfügbar: {e}")

# 🔒 DATENSCHUTZ: Deaktiviere HTTP-Client-Logging (verhindert Leaking von persönlichen Daten)
# primp loggt alle HTTP-Requests inkl. Query-Parameter auf INFO-Level!
logging.getLogger('primp').setLevel(logging.WARNING)
logging.getLogger('httpx').setLevel(logging.WARNING)
logging.getLogger('urllib3').setLevel(logging.WARNING)
logging.getLogger('requests').setLevel(logging.WARNING)

# NEU: Adaptive Planning (Optional)
ADAPTIVE_PLANNING_AVAILABLE = False
AdaptivePlanner: type[Any] | None = None
try:
    from agent.adaptive_planner import AdaptivePlanner
    ADAPTIVE_PLANNING_AVAILABLE = True
except ImportError:
    logging.getLogger(__name__).debug("Adaptive Planning Modul nicht verfuegbar")

# NEU: LLM Reasoning Optimizer (für LLM-basierte Complexity-Klassifikation)
REASONING_OPTIMIZER_AVAILABLE = False
MinistralReasoningOptimizer: type[Any] | None = None
try:
    from ministral_reasoning_optimizer import MinistralReasoningOptimizer
    REASONING_OPTIMIZER_AVAILABLE = True
    print("✅ LLM Reasoning Optimizer erfolgreich importiert")
except ImportError as e:
    print(f"⚠️ LLM Reasoning Optimizer nicht verfügbar: {e}")
    pass  # Reasoning Optimizer ist optional

# Logger Setup
logger = logging.getLogger(__name__)
_ACTIVE_CHAT_RUNS = ActiveRunRegistry()

def retry_on_failure(max_retries: int = 3, delay: float = 1.0, backoff_factor: float = 2.0):
    """
    Decorator für Retry-Mechanismus mit exponential backoff
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            retries = 0
            current_delay = delay
            
            while retries < max_retries:
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    retries += 1
                    if retries >= max_retries:
                        print(f"❌ {func.__name__} fehlgeschlagen nach {max_retries} Versuchen: {e}")
                        raise e
                    
                    print(f"⚠️ {func.__name__} Versuch {retries} fehlgeschlagen: {e}")
                    print(f"🔄 Retry in {current_delay:.1f}s...")
                    time.sleep(current_delay)
                    current_delay *= backoff_factor
            
            return None
        return wrapper
    return decorator

class AgentChatbotLogic(ChatbotLogic):
    """
    Erweiterte ChatbotLogic mit Agent-Fähigkeiten
    Das LLM entscheidet selbst über Tool-Verwendung
    """
    
    def __init__(self, model_loader, settings: Optional[Dict] = None):
        # Korrekte Initialisierung der Elternklasse
        super().__init__(model_loader, settings)
        
        # Agent-spezifische Eigenschaften
        self.logger = logging.getLogger(__name__)
        try:
            self.agent_toolkit = AgentToolkit()
            # Privacy Handler mit LLM-Client initialisieren
            if model_loader:
                self.agent_toolkit.set_llm_client(model_loader)
            # 🆕 Chat-Funktion für Vision-Validierung setzen
            if hasattr(self, 'chat') and callable(self.chat):
                self.agent_toolkit.set_chat_function(self.chat)
            print("✅ AgentToolkit erfolgreich geladen")
        except Exception as e:
            print(f"❌ Fehler beim Laden des AgentToolkit: {e}")
            self.agent_toolkit = None  # type: ignore[assignment]
            
        self.agent_mode_enabled = True  # STANDARDMÄSSIG AKTIVIERT für Web-Suche!
        self.max_tool_iterations = 5  # Verhindert Endlosschleifen
        self.settings.setdefault("use_react_agent", True)
        # Neu: Zuletzt verwendete Quellen (für GUI-Rendering der Quellenliste)
        self.last_sources: List[Dict[str, Any]] = []
        self.last_graphics: List[Dict[str, Any]] = []
        self.last_files: List[Dict[str, Any]] = []
        # NEU: Orchestrator basierend auf geladenem Modell
        try:
            n_ctx = self.settings.get("n_ctx", LLM_CONTEXT_SIZE)  # Single Source of Truth (16384)
            
            # WICHTIG: Setze ENV-Variablen für größere Token-Limits BEVOR der Orchestrator erstellt wird
            import os
            os.environ["SUMMARIZER_MAX_TOKENS"] = "4096"  # Für vollständige Antworten
            os.environ["PLANNER_MAX_TOKENS"] = "2048"     # Für bessere Planung
            
            self.orchestrator = AgentOrchestrator(self.model_loader, n_ctx=n_ctx)
            
            # WICHTIG: Erhöhe max_tokens für vollständige Antworten (zusätzliche Sicherheit)
            self.orchestrator.set_generation_limits(
                planner_max_tokens=2048,    # Für bessere Planungsentscheidungen
                summarizer_max_tokens=4096, # KRITISCH: Für vollständige Antworten (war 1024!)
                verifier_max_tokens=1024    # Für Verifikation ausreichend
            )
            
            # DEBUG: Prüfe die tatsächlichen Werte
            print(f"✅ AgentOrchestrator erstellt mit Token-Limits:")
            print(f"   Planner: {self.orchestrator.planner_max_tokens}")
            print(f"   Summarizer: {self.orchestrator.summarizer_max_tokens}")
            print(f"   Verifier: {self.orchestrator.verifier_max_tokens}")
            print(f"   Multiquery enabled: {self.orchestrator.multiquery_enabled}")
            print(f"   RAG enabled: {self.orchestrator.rag_enabled}")
            print(f"   Multiquery N: {self.orchestrator.mq_n}, K: {self.orchestrator.mq_k}")
            
            # NEU: Initialisiere Privacy Handler mit LLM-Client
            if model_loader:
                self.orchestrator.initialize_privacy_handler()
                print("✅ Privacy Handler im Orchestrator initialisiert")
        except Exception as e:
            print(f"❌ Fehler beim Erstellen des AgentOrchestrator: {e}")
            self.orchestrator = None  # type: ignore[assignment]

        # SOTA Variant 4 — central decomposition engine.
        # Reuses the orchestrator's LLM wrapper so the engine never holds its
        # own model state. If the orchestrator failed to come up, the engine
        # is still constructed but with llm_callable=None — it will then
        # always passthrough, which is the documented safe default.
        decomp_llm = getattr(self.orchestrator, "_llm_wrapper", None) if self.orchestrator else None
        self.decomposition_engine = DecompositionEngine(llm_callable=decomp_llm)
        print(
            "✅ DecompositionEngine initialisiert "
            f"(LLM verfügbar: {decomp_llm is not None})"
        )

        # D5 CLEANUP: Psycho Chat V2 (State Machine) wurde nie aktiviert (USE_PSYCHO_CHAT_V2=False).
        # Entfernt: Dead-Code-Pfad eliminiert zugunsten des konsolidierten Pipelines.
        
        # NEU: Generischer Intent-Klassifikator statt hart codierte Keywords
        try:
            self.intent_classifier = GenericIntentClassifier()
            print("✅ GenericIntentClassifier geladen")
        except Exception as e:
            print(f"❌ Fehler beim Laden des GenericIntentClassifier: {e}")
            self.intent_classifier = None  # type: ignore[assignment]
    
        # NEU: Letzte Trace für GUI
        self.last_trace: Optional[AgentTrace] = None
        self.last_followup_questions: list = []
        self.react_agent: Optional[ReActAgent] = None
        
        # NEU: Hybrid Search Configuration
        self.search_method = "intelligent_routing"  # Default
        self.hybrid_search_engine = None
        
        # NEU: Optimized Research Engine für beste Performance
        self.optimized_research_engine = None
        self._initialize_research_engines()
        
        # NEU: Psychologische Integration für session-übergreifende Familiendaten
        self.wellbeing_integration = None
        self._wellbeing_integration_attempted = False
        
        # NEU: Response Cache für häufige Anfragen
        self.response_cache = {}
        self._cache = {}  # Zusätzlicher Cache für interne Operationen
        self.cache_max_size = 100  # Max gecachte Responses
        self.cache_enabled = True  # Cache-Kontrolle
        
        # NEU: Debug-Info für Streamlit
        self._last_routing_debug = "Noch keine Routing-Entscheidung"
        
        # NEU: Performance-Metriken
        self.performance_stats = {
            'total_requests': 0,
            'cache_hits': 0,
            'cache_misses': 0,
            'avg_response_time': 0.0
        }
        
        # NEU: Bildanalyse-Konfiguration
        self.image_analysis_config = {
            'mode': 'Standard',
            'detail_level': 'hoch',
            'include_explicit': True,
            'custom_prompt': ''
        }
        self.image_system_prompt = ("Du bist ein hilfreicher Assistent für Bildanalyse. "
                                    "Beschreibe Bilder genau und objektiv. "
                                    "Beginne mit [THINK]deiner systematischen Bildanalyse und Beobachtungen[/THINK], "
                                    "gefolgt von einer strukturierten, vollständigen Bildbeschreibung. "
                                    "Verwende Markdown-Formatierung für bessere Lesbarkeit.")
        
        # NEU: RAG-Chunk-Konfiguration
        self.rag_chunk_config = {
            'chunk_size': 2000,
            'chunk_overlap': 300
        }
        
        # NEU: Content-Präferenz-Konfiguration (LÖSUNG FÜR SNIPPET-PROBLEM)
        self.content_preference = {
            'prefer_fulltext': True,  # RAG-Volltext über Web-Snippets bevorzugen
            'rag_weight': 0.8,        # Gewichtung für RAG-Ergebnisse
            'web_weight': 0.2,        # Gewichtung für Web-Snippets
            'min_rag_results': 2,     # Mindest-RAG-Ergebnisse vor Web-Fallback
            'fulltext_threshold': 500 # Min. Zeichen für "Volltext"-Klassifikation
        }

        # NEU: Konfiguration für intelligentes Chat-Routing
        self.chat_routing_config = {
            "enabled": True,  # Intelligentes Routing aktiviert
            "use_llm_routing": True,  # LLM-basierte Entscheidung ohne Pattern-Overrides
        }

        # NEU: Psychologische Unterstützung wird in enhanced_streamlit_bot.py verwaltet
        # Kein eigenes Interface mehr - zu viele API-Inkompatibilitäten
        # Das WellbeingSessionInterface wird zentral in st.session_state.wellbeing_interface verwaltet
        self.wellbeing_support_interface = None
        # Alias für ältere Zugriffe
        self.wellbeing_interface = None
        print("ℹ️ Psychologisches Interface wird zentral in Streamlit verwaltet")

        # NEU: Tracking für psychologische Nachrichten-Prüfung
        self._last_message_checked = False

        # Adaptive Planning (NEU - nach allen anderen Initialisierungen!)
        self.adaptive_planning_enabled = False  # Default: disabled
        self.adaptive_max_reflections = 2       # Max. Reflections
        self.adaptive_confidence_done = 0.85    # Threshold für "done"
        self.adaptive_confidence_tools = 0.4    # Threshold für "more tools needed"
        
        if ADAPTIVE_PLANNING_AVAILABLE and AdaptivePlanner is not None:
            self.adaptive_planner = AdaptivePlanner(self)
            logger.info("✅ Adaptive Planner initialisiert")
        else:
            self.adaptive_planner = None
            logger.warning("⚠️ Adaptive Planner nicht verfügbar")
        
        # NEU: LLM Reasoning Optimizer (LLM-basierte Complexity-Klassifikation)
        if REASONING_OPTIMIZER_AVAILABLE and MinistralReasoningOptimizer is not None:
            try:
                # Reasoning Optimizer benötigt llama_model (llm-Attribut vom ModelLoader)
                # ModelLoader hat .llm Attribut (Llama-Objekt), nicht .model
                llama_model = getattr(self.model_loader, 'llm', None)
                
                if llama_model is None:
                    # Fallback: Versuche .model Attribut (für Mock-Loader)
                    llama_model = getattr(self.model_loader, 'model', None)
                
                if llama_model is not None:
                    # Ermittle aktuellen Modell-Namen für korrektes Logging
                    model_name = getattr(self.model_loader, 'current_model_id', 'Unknown Model')
                    
                    self.reasoning_optimizer = MinistralReasoningOptimizer(
                        llama_model=llama_model,
                        model_name=model_name,  # NEU: Übergib Modell-Namen
                        temperature=self.settings.get('temperature', 0.7),
                        max_tokens_limit=self.settings.get('n_ctx', LLM_CONTEXT_SIZE),
                        max_reasoning_traces=2,
                        debug=self.settings.get('debug', False)
                    )
                    print(f"✅ Reasoning Optimizer erfolgreich initialisiert ({model_name})")
                    logger.info(f"✅ Reasoning Optimizer initialisiert ({model_name})")
                else:
                    print("⚠️ Kein Llama-Model gefunden (model_loader.llm ist None)")
                    logger.warning("Kein Llama-Model gefunden")
                    self.reasoning_optimizer = None
            except Exception as e:
                print(f"❌ Fehler bei Reasoning Optimizer Initialisierung: {e}")
                logger.error(f"Reasoning Optimizer Fehler: {e}")
                self.reasoning_optimizer = None
        else:
            self.reasoning_optimizer = None
            print("⚠️ Reasoning Optimizer nicht verfügbar")
            logger.warning("⚠️ Reasoning Optimizer nicht verfügbar")
        
    def _initialize_research_engines(self):
        """Initialisiert Research-Engines basierend auf verfügbaren Ressourcen"""
        try:
            if self.orchestrator and hasattr(self.orchestrator, 'tools'):
                rag_store = self.orchestrator.tools.rag
                if rag_store:
                    # Optimized Research Engine
                    self.optimized_research_engine = create_optimized_research_engine(
                        rag_store, 
                        tool_manager=self.orchestrator.tools,
                        max_parallel_strategies=3
                    )
                    print("✅ Optimized Research Engine initialisiert")
                    
                    # Hybrid Search Engine (fallback)
                    self.hybrid_search_engine = create_hybrid_search_engine(
                        rag_store, 
                        web_search_enabled=True
                    )
                    print("✅ Hybrid Search Engine initialisiert")
                else:
                    print("⚠️ RAG Store nicht verfügbar - Research Engines nicht initialisiert")
            else:
                print("⚠️ Orchestrator nicht verfügbar - Research Engines nicht initialisiert")
        except Exception as e:
            print(f"⚠️ Research Engines Initialisierung fehlgeschlagen: {e}")
            self.optimized_research_engine = None
            self.hybrid_search_engine = None

    def set_adaptive_planning_config(
        self,
        *,
        enabled: Optional[bool] = None,
        max_reflections: Optional[int] = None,
        confidence_threshold_done: Optional[float] = None,
        confidence_threshold_tools: Optional[float] = None
    ) -> None:
        """
        Konfiguriert Adaptive Planning zur Laufzeit.
        
        Args:
            enabled: Aktiviert/deaktiviert Adaptive Planning
            max_reflections: Max. Anzahl Reflections (1-3)
            confidence_threshold_done: Threshold für "done" (0.0-1.0)
            confidence_threshold_tools: Threshold für "more tools" (0.0-1.0)
        """
        if enabled is not None:
            self.adaptive_planning_enabled = bool(enabled)
            logger.info(f"🔄 Adaptive Planning: {'aktiviert' if enabled else 'deaktiviert'}")
        
        if max_reflections is not None:
            self.adaptive_max_reflections = max(1, min(3, int(max_reflections)))
            logger.info(f"🔄 Max Reflections: {self.adaptive_max_reflections}")
        
        if confidence_threshold_done is not None:
            self.adaptive_confidence_done = float(confidence_threshold_done)
        
        if confidence_threshold_tools is not None:
            self.adaptive_confidence_tools = float(confidence_threshold_tools)
        
        # Update Planner-Thresholds falls verfügbar
        if self.adaptive_planner:
            self.adaptive_planner.confidence_done_threshold = self.adaptive_confidence_done
            self.adaptive_planner.confidence_tools_threshold = self.adaptive_confidence_tools
        
        # WICHTIG: Konfiguration an Orchestrator weiterleiten
        if self.orchestrator and hasattr(self.orchestrator, 'set_adaptive_planning_config'):
            self.orchestrator.set_adaptive_planning_config(
                enabled=self.adaptive_planning_enabled,
                max_reflections=self.adaptive_max_reflections,
                confidence_done_threshold=self.adaptive_confidence_done,
                confidence_tools_threshold=self.adaptive_confidence_tools
            )

    # ========================================================================
    # REASONING OPTIMIZER HELPER METHODS
    # ========================================================================
    
    def enable_reasoning_optimizer(self):
        """Aktiviert den Reasoning Optimizer für alle nachfolgenden Queries"""
        if not self.reasoning_optimizer:
            logger.warning("⚠️ Reasoning Optimizer nicht verfügbar")
            return False
        
        print("✅ Reasoning Optimizer aktiviert")
        logger.info("Reasoning Optimizer aktiviert")
        return True
    
    def disable_reasoning_optimizer(self):
        """Deaktiviert den Reasoning Optimizer (nutzt Standard-Flow)"""
        print("⚠️ Reasoning Optimizer deaktiviert (nutze Standard-Flow)")
        logger.info("Reasoning Optimizer deaktiviert")
        return True
    
    def get_reasoning_optimizer_stats(self) -> Dict[str, Any]:
        """Gibt Reasoning-Optimizer-Statistiken zurück"""
        if not self.reasoning_optimizer:
            return {
                "available": False,
                "message": "Reasoning Optimizer nicht verfügbar"
            }
        
        return {
            "available": True,
            "max_tokens_limit": self.reasoning_optimizer.max_tokens_limit,
            "temperature": self.reasoning_optimizer.temperature,
            "debug": self.reasoning_optimizer.debug,
            "memory_traces": len(self.reasoning_optimizer.reasoning_memory.traces),
            "max_reasoning_traces": self.reasoning_optimizer.reasoning_memory.max_traces
        }
    
    def clear_reasoning_memory(self):
        """Löscht alle Reasoning-Traces aus dem Memory"""
        if not self.reasoning_optimizer:
            logger.warning("⚠️ Reasoning Optimizer nicht verfügbar")
            return
        
        self.reasoning_optimizer.reasoning_memory.clear()
        print("🧹 Reasoning Memory gelöscht")
        logger.info("Reasoning Memory gelöscht")
    
    def set_reasoning_debug(self, enabled: bool):
        """Aktiviert/Deaktiviert Debug-Logging für Reasoning Optimizer"""
        if not self.reasoning_optimizer:
            logger.warning("⚠️ Reasoning Optimizer nicht verfügbar")
            return
        
        self.reasoning_optimizer.debug = enabled
        if enabled:
            logger.setLevel(logging.DEBUG)
            print("🐛 Reasoning Debug aktiviert")
        else:
            logger.setLevel(logging.INFO)
            print("ℹ️ Reasoning Debug deaktiviert")
    
    def force_reasoning_complexity(self, complexity: str):
        """
        Erzwingt eine bestimmte Komplexitätsstufe (überschreibt LLM-Klassifikation)
        
        Args:
            complexity: "simple", "medium", "complex" oder None (automatisch)
        """
        if not self.reasoning_optimizer:
            logger.warning("⚠️ Reasoning Optimizer nicht verfügbar")
            return
        
        if complexity not in ["simple", "medium", "complex", None]:
            logger.error(f"❌ Ungültige Komplexität: {complexity}")
            return
        
        # Speichere Override für nächste Query
        self._forced_complexity = complexity
        
        if complexity:
            print(f"🎯 Reasoning-Komplexität erzwungen: {complexity}")
            logger.info(f"Forced complexity: {complexity}")
        else:
            print("🔄 Automatische Reasoning-Komplexität wiederhergestellt")
            logger.info("Automatic complexity restored")
    
    # ========================================================================
    # END REASONING OPTIMIZER HELPER METHODS
    # ========================================================================

    def set_search_method(self, method: str):
        """
        Konfiguriert die Suchmethode für RAG
        
        Args:
            method: "intelligent_routing", "optimized_research", "enhanced_rag", "balanced_hybrid", 
                   "web_focused_hybrid", "rag_only", "web_only"
        """
        self.search_method = method
        print(f"🔍 Suchmethode gesetzt: {method}")
        
        # Optimized Research Engine bei Bedarf initialisieren
        if method == "optimized_research" and not self.optimized_research_engine:
            self._initialize_research_engines()
        
        # Hybrid Search Engine bei Bedarf initialisieren
        elif method != "rag_only" and method != "optimized_research" and not self.hybrid_search_engine:
            try:
                if self.orchestrator and hasattr(self.orchestrator, 'tools'):
                    rag_store = self.orchestrator.tools.rag
                    if rag_store:
                        self.hybrid_search_engine = create_hybrid_search_engine(
                            rag_store, 
                            web_search_enabled=(method != "rag_only")
                        )
                        print("✅ Hybrid Search Engine initialisiert")
                    else:
                        print("⚠️ RAG Store nicht verfügbar")
                else:
                    print("⚠️ Orchestrator nicht verfügbar")
            except Exception as e:
                print(f"⚠️ Hybrid Search Engine Fehler: {e}")
                self.hybrid_search_engine = None

    def set_content_preference(self, prefer_fulltext: bool = True, rag_weight: float = 0.8):
        """
        Konfiguriert Content-Präferenz: RAG-Volltext vs Web-Snippets
        
        Args:
            prefer_fulltext: True = RAG-Volltext bevorzugen, False = Web-Snippets bevorzugen
            rag_weight: Gewichtung für RAG-Ergebnisse (0.0-1.0)
        """
        self.content_preference.update({
            'prefer_fulltext': prefer_fulltext,
            'rag_weight': rag_weight,
            'web_weight': 1.0 - rag_weight
        })
        
        mode = "RAG-Volltext" if prefer_fulltext else "Web-Snippets"
        print(f"📚 Content-Präferenz: {mode} (RAG: {rag_weight:.1f}, Web: {1.0-rag_weight:.1f})")
        
        # Note: Die Content-Präferenz wird intern in der Chat-Logik berücksichtigt
        # Der Orchestrator verwendet seine eigenen Evidenz-Auswahl-Algorithmen
        print(f"✅ Content-Präferenz konfiguriert: {mode}")

    def get_content_preference(self) -> Dict[str, Any]:
        """Gibt aktuelle Content-Präferenz zurück"""
        return self.content_preference.copy()

    def _prioritize_rag_over_web(self, rag_results: List[Dict], web_results: List[Dict]) -> List[Dict]:
        """
        Priorisiert RAG-Volltext über Web-Snippets basierend auf Content-Präferenz
        
        Args:
            rag_results: Liste der RAG-Suchergebnisse
            web_results: Liste der Web-Suchergebnisse
            
        Returns:
            Priorisierte und gewichtete Ergebnisliste
        """
        if not self.content_preference['prefer_fulltext']:
            # Web-Snippets bevorzugen (alter Modus)
            return web_results + rag_results
        
        # RAG-Volltext bevorzugen (neuer Modus)
        prioritized = []
        
        # 1. RAG-Ergebnisse mit höherer Gewichtung
        for result in rag_results:
            result_copy = result.copy()
            result_copy['source_type'] = 'rag_fulltext'
            result_copy['priority_score'] = result.get('score', 0.5) * self.content_preference['rag_weight']
            result_copy['content_length'] = len(result.get('content', ''))
            prioritized.append(result_copy)
        
        # 2. Web-Ergebnisse nur als Fallback oder mit niedrigerer Gewichtung
        min_rag = self.content_preference['min_rag_results']
        if len(rag_results) < min_rag:
            # Nicht genug RAG-Ergebnisse -> Web-Snippets als Fallback
            for result in web_results:
                result_copy = result.copy()
                result_copy['source_type'] = 'web_snippet'
                result_copy['priority_score'] = result.get('score', 0.3) * self.content_preference['web_weight']
                result_copy['content_length'] = len(result.get('snippet', ''))
                prioritized.append(result_copy)
        
        # 3. Nach Priorität sortieren
        prioritized.sort(key=lambda x: x.get('priority_score', 0), reverse=True)
        
        return prioritized

    def enable_agent_mode(self, enable: bool = True):
        """Aktiviert/deaktiviert Agent-Modus"""
        if enable and not self.agent_toolkit:
            print("⚠️ Agent-Modus kann nicht aktiviert werden - AgentToolkit nicht verfügbar")
            return False
            
        self.agent_mode_enabled = enable
        logging.info(f"Agent-Modus {'aktiviert' if enable else 'deaktiviert'}")
        return True

    def reset_context(self):
        """Erweiterte Context-Reset mit Chat-Zusammenfassungs-Reset"""
        super().reset_context()
        # Cache bei Context-Reset leeren
        self._cache.clear()
        
        # NEU: Context Manager auch zurücksetzen
        if hasattr(self, 'context_manager'):
            # Erstelle neuen Context Manager für frischen Start mit exakter Tokenisierung
            context_window = self.settings.get("n_ctx", LLM_CONTEXT_SIZE)
            
            # ✅ KORREKT: ModelLoader ist jetzt Typ-kompatibel nach Migration zu scripts.model_loader
            self.context_manager = ChatContextManager(
                model_loader=self.model_loader,  # Typ-sichere Übergabe nach Migration
                max_context_tokens=None,  # Auto-detect from model
                rag_reserve_tokens=2048,  # Conservative RAG reserve (will be adjusted dynamically)
                system_prompt_tokens=512,
                summary_target_tokens=2000,  # ✅ ERHÖHT: Für psych summaries (max ~1828 tokens)
                min_recent_messages=6,
                tools_reserve_tokens=256
            )
            logging.info("🔄 Chat Context Manager zurückgesetzt (mit manueller Context-Window-Angabe)")

    def _generate_cache_key(self, user_prompt: str, image_path: Optional[str] = None) -> Optional[str]:
        """Generiert eindeutigen Cache-Key basierend auf Prompt und optional Bildpfad"""
        if not self.cache_enabled:
            return None
        
        # Bilder werden nicht gecacht
        if image_path:
            return None
            
        # Hash aus prompt erstellen
        cache_content = f"{user_prompt.strip().lower()}"
        return hashlib.md5(cache_content.encode()).hexdigest()
    
    def _get_cached_response(self, cache_key: Optional[str]) -> Optional[str]:
        """Holt gecachte Response falls vorhanden"""
        if not cache_key or not self.cache_enabled:
            return None
            
        return self.response_cache.get(cache_key)
    
    def _cache_response(self, cache_key: Optional[str], response: str):
        """Speichert Response im Cache mit LRU-ähnlicher Verwaltung"""
        if (
            not cache_key
            or not self.cache_enabled
            or not response
            or response.lstrip().upper().startswith("[ERROR]")
        ):
            return
            
        # Cache-Größe begrenzen (einfache LRU-Implementierung)
        if len(self.response_cache) >= self.cache_max_size:
            # Älteste Einträge entfernen (einfach: ersten Key löschen)
            oldest_key = next(iter(self.response_cache))
            del self.response_cache[oldest_key]
        
        self.response_cache[cache_key] = response

    @staticmethod
    def _can_use_response_cache(execution_mode: str) -> bool:
        """Only direct text answers are replay-safe; tool runs may have side effects."""
        return execution_mode == "SIMPLE"
    
    def clear_cache(self):
        """Leert den Response-Cache"""
        self.response_cache.clear()
        print("🗑️ Response-Cache geleert")
    
    def _initialize_wellbeing_integration(self):
        """Initialisiert die psychologische Integration für session-übergreifende Familiendaten"""
        try:
            if self.orchestrator:
                # Importiere die psychologischen Komponenten
                from wellbeing.wellbeing_db import WellbeingDatabase
                from wellbeing_user_insight_extractor import WellbeingUserInsightExtractor
                
                # Initialisiere psychologische Datenbank
                wellbeing_db = WellbeingDatabase()
                
                # Initialisiere User Insight Extractor  
                user_insight_extractor = WellbeingUserInsightExtractor(wellbeing_db)
                
                # Integriere in Orchestrator
                self.wellbeing_integration = integrate_wellbeing_orchestrator(
                    orchestrator=self.orchestrator,
                    wellbeing_db=wellbeing_db,
                    user_insight_extractor=user_insight_extractor
                )
                
                print("✅ Psychologische Orchestrator-Integration aktiviert")
                print("   📊 Session-übergreifende Familiendaten verfügbar")
                print("   🧠 User-Profile werden in Prompts integriert")
                
            else:
                print("⚠️ Orchestrator nicht verfügbar - Psychologische Integration übersprungen")
                
        except Exception as e:
            print(f"⚠️ Psychologische Integration fehlgeschlagen: {e}")
            self.wellbeing_integration = None

    def _ensure_wellbeing_integration(self) -> None:
        """Lazy-init psych integration only when AGENT path really needs it.

        .. deprecated:: 2026-08-28
            Wird im normalen Chat-Pfad (`_agent_chat`) NICHT mehr aufgerufen
            (Privacy-Guard: normales Chattab erhält keine Psych-Tab-Daten).
            Bleibt nur für Rollback-Zwecke erhalten.
        """
        if self._wellbeing_integration_attempted:
            return
        self._wellbeing_integration_attempted = True
        self._initialize_wellbeing_integration()

    def _should_enable_wellbeing_integration(self, user_prompt: str) -> bool:
        """Gate psycho enrichment for AGENT requests using intent + explicit opt-in.

        .. deprecated:: 2026-08-28
            Das Ergebnis wird im normalen Chat ignoriert (Privacy-Guard in
            `_agent_chat`). Bleibt nur für Rollback-Zwecke erhalten.

        Default is OFF for normal chat to prevent cross-module bleed-over.
        Enable only when:
        1) explicit runtime opt-in is active, and
        2) query intent is PERSONAL or MIXED with reasonable confidence.
        """
        if not parse_bool_env("ENABLE_AGENT_PSYCH_INTEGRATION", "0"):
            return False

        if not user_prompt or not user_prompt.strip() or not self.model_loader:
            return False

        try:
            classification: QueryClassification = classify_query_llm(
                user_prompt, model_loader=self.model_loader
            )
            intent = classification.intent
            confidence = float(classification.confidence or 0.0)
            enabled = intent in (QueryIntent.PERSONAL, QueryIntent.MIXED) and confidence >= 0.60
            logger.info(
                "Psych-integration gate: enabled=%s intent=%s confidence=%.2f",
                enabled,
                intent.value,
                confidence,
            )
            return enabled
        except Exception as exc:
            logger.warning(
                "Psych-integration gate failed (%s); defaulting to disabled.",
                exc,
            )
            return False
    
    def get_current_user_id(self) -> str:
        """Gibt die aktuelle User-ID für psychologische Integration zurück"""
        try:
            return get_current_user_id()
        except Exception as e:
            print(f"⚠️ User-ID nicht verfügbar: {e}")
            return "default_user"
    
    def was_last_message_wellbeing_checked(self) -> bool:
        """Gibt zurück, ob die letzte Nachricht psychologisch geprüft wurde"""
        return getattr(self, '_last_message_checked', False)
    
    def set_image_analysis_config(self, config: Dict[str, Any]):
        """
        Setzt die Bildanalyse-Konfiguration
        
        Args:
            config: Dictionary mit Bildanalyse-Einstellungen
                   - mode: 'Standard', 'Detaillierte Bildanalyse', 'Benutzerdefiniert'
                   - detail_level: 'niedrig', 'mittel', 'hoch'
                   - include_explicit: bool
                   - custom_prompt: str
        """
        self.image_analysis_config.update(config)
        print(f"🖼️ Bildanalyse-Konfiguration aktualisiert: {config}")
    
    def set_rag_chunk_config(self, chunk_size: int, chunk_overlap: int):
        """
        Setzt die RAG-Chunk-Konfiguration
        
        Args:
            chunk_size: Größe der Text-Chunks
            chunk_overlap: Überlappung zwischen Chunks
        """
        self.rag_chunk_config = {
            'chunk_size': max(200, chunk_size),
            'chunk_overlap': max(0, chunk_overlap)
        }
        print(f"📚 RAG-Chunk-Konfiguration aktualisiert: {self.rag_chunk_config}")
        
        # Orchestrator entsprechend konfigurieren
        if self.orchestrator and hasattr(self.orchestrator, 'tools') and self.orchestrator.tools.rag:
            try:
                # Setze Default-Chunk-Konfiguration im RAG Store
                rag_store = self.orchestrator.tools.rag
                if hasattr(rag_store, 'default_chunk_size'):
                    rag_store.default_chunk_size = chunk_size
                    rag_store.default_chunk_overlap = chunk_overlap
                    print("✅ RAG Store Chunk-Konfiguration aktualisiert")
            except Exception as e:
                print(f"⚠️ RAG Store Konfiguration fehlgeschlagen: {e}")
        
        # Übertrage Konfiguration an Orchestrator falls verfügbar
        if self.orchestrator and hasattr(self.orchestrator, 'tools') and self.orchestrator.tools.rag:
            rag_store = self.orchestrator.tools.rag
            if hasattr(rag_store, 'set_default_chunk_config'):
                rag_store.set_default_chunk_config(chunk_size, chunk_overlap)
    
    def get_rag_chunk_config(self) -> Dict[str, int]:
        """Gibt die aktuelle RAG-Chunk-Konfiguration zurück"""
        return self.rag_chunk_config.copy()
    
    # ──────────────────────────────────────────────────────────────────
    # ★ SOTA v3: Feedback-Loop-Brücke (Root-Cause-Fix, 2026-08-21)
    # ──────────────────────────────────────────────────────────────────
    # Die UI (ui_tabs/chat_tab.py) liest NACH jeder Antwort diese Attribute
    # und übergibt sie an FeedbackLogger →
    # RAGQualityManager.record_retrieval_feedback (Wilson-Chunk-Utility).
    # Früher wurden diese Attribute NIEMALS gesetzt → chunk_ids war immer
    # [] → retrieval_feedback blieb leer → die Feedback-Schleife war tot.
    # Die Properties sind Live-Proxy auf den SearchManager des geteilten
    # RAG-Stores (Single Source of Truth, keine Zustandsdopplung).

    @property
    def _last_rag_chunk_ids(self) -> List[str]:
        """Chunk-IDs der letzten RAG-Antwort (für Qualitätsfeedback)."""
        sm = self._get_last_search_manager()
        if sm is None:
            return []
        return list(getattr(sm, '_last_search_chunk_ids', []) or [])

    @property
    def _last_rag_chunk_scores(self) -> List[float]:
        """Chunk-Scores der letzten RAG-Antwort (für Qualitätsfeedback)."""
        sm = self._get_last_search_manager()
        if sm is None:
            return []
        return list(getattr(sm, '_last_search_chunk_scores', []) or [])

    def _get_last_search_manager(self):
        """Best-effort-Auflösung des SearchManagers des geteilten RAG-Stores.

        Bevorzugt ``self.orchestrator.tools.rag`` (Produktivpfad); Fallback
        auf die geteilte Instanz aus ``utils.db_path_resolver`` (gleicher
        kanonischer DB-Pfad → dieselbe Instanz).
        """
        try:
            tools = getattr(self.orchestrator, 'tools', None)
            rag_store = getattr(tools, 'rag', None) if tools else None
            if rag_store is None:
                from agent.unified_rag_store import UnifiedRagStore
                rag_store = UnifiedRagStore.get_existing_shared()
            if rag_store is None:
                return None
            return getattr(rag_store, '_search_manager', None)
        except Exception:
            return None

    def get_image_system_prompt(self) -> str:
        """Gibt den aktuellen System-Prompt für Bildanalyse zurück"""
        return getattr(self, 'image_system_prompt', "Du bist ein hilfreicher Assistent für Bildanalyse. Beschreibe Bilder genau und objektiv.")

    def _apply_image_analysis_config(self):
        """Wendet die aktuelle Bildanalyse-Konfiguration auf die Basisklasse an"""
        config = self.image_analysis_config
        mode = config.get('mode', 'Standard')
        
        if mode == "Standard":
            # Standard-Modus: Verwende normalen System-Prompt
            self.set_system_prompt(self.image_system_prompt or "Du bist ein hilfreicher Assistent für Bildanalyse. Beschreibe Bilder genau und objektiv.")
            
        elif mode == "Detaillierte Bildanalyse":
            # Aktiviere detaillierte Bildanalyse der Basisklasse
            self.use_detailed_image_analysis(True)
            
        elif mode == "Benutzerdefiniert":
            # Erstelle benutzerdefinierten Prompt
            detail_level = config.get('detail_level', 'hoch')
            include_explicit = config.get('include_explicit', True)
            custom_prompt = config.get('custom_prompt', '')
            
            if custom_prompt:
                # Verwende den benutzerdefinierten Prompt direkt
                self.set_system_prompt(custom_prompt)
            else:
                # Erstelle Prompt basierend auf Einstellungen
                generated_prompt = self.create_custom_image_prompt(
                    base_prompt=None,
                    detail_level=detail_level,
                    include_explicit=include_explicit
                )
                self.set_system_prompt(generated_prompt)
        
        print(f"🖼️ Bildanalyse-Konfiguration angewendet: {mode}")

    # ─────────────────────────────────────────────────────────────────────
    # _response_shows_uncertainty wurde 2026-06-02 als Anti-Pattern entfernt.
    # Begründung: Pattern-Match auf Wörter wie "wahrscheinlich"/"möglicherweise"
    # triggerte einen automatischen AGENT-Web-Recherche-Pfad selbst dann,
    # wenn die SIMPLE-Antwort vollständig dokument-gegrundet war (PDF im
    # Kontext). Das blockierte die UI durch fan-out an externe Web-Fetches.
    # Falls eine selbstkritische Konfidenz-Bewertung wieder gebraucht wird,
    # muss sie LLM-basiert (kein Keyword/Pattern-Match) und ohne Web-Fetch
    # im SIMPLE-Pfad implementiert werden — siehe Memory-Note "Audit Anti-
    # Patterns": "Verwende keine Lösung, die auf Keywords oder Patterns
    # basiert, ausser es ist tatsächlich die nachhaltigste und beste Lösung."
    # ─────────────────────────────────────────────────────────────────────

    def _should_use_normal_chat(self, user_prompt: str) -> bool:
        """Backward-compatible helper mapped to the new execution-mode router."""
        return self._select_agent_execution_mode(user_prompt) == "SIMPLE"

    def _is_react_enabled(self) -> bool:
        """Returns whether ReAct mode is enabled via runtime settings."""
        settings_obj = getattr(self, "settings", {})
        if isinstance(settings_obj, dict):
            return bool(settings_obj.get("use_react_agent", True))
        return False

    def _extract_execution_mode(self, raw: Optional[str]) -> Optional[str]:
        """Extract SIMPLE/PLAN_EXECUTE/REACT from strict classifier output."""
        if not raw:
            return None
        upper = raw.upper()
        import re as _re
        marked = _re.search(r"FINAL_MODE\s*[:=]\s*(SIMPLE|PLAN_EXECUTE|REACT)", upper)
        if marked:
            return marked.group(1)
        matches = _re.findall(r"\b(SIMPLE|PLAN_EXECUTE|REACT)\b", upper)
        if matches:
            unique = set(matches)
            if len(unique) == 1:
                return matches[-1]
        return None

    def _is_meta_capability_query(self, user_prompt: str) -> bool:
        """Detect whether the prompt asks about the bot's own capabilities/tools.

        Root-cause fix: Meta-Fragen wie 'Was kannst du?' wurden als SIMPLE
        klassifiziert, wodurch der Bot seine eigenen Tools nicht kannte.
        Dieser deterministische Gate leitet solche Fragen an PLAN_EXECUTE weiter,
        damit der Agent-Pfad die Tool-Liste bereitstellen kann.
        """
        text = (user_prompt or "").strip()
        if not text:
            return False

        lower = text.lower()

        # Meta-Fragen nach Fähigkeiten/Tools des Bots selbst
        meta_patterns = [
            # Deutsch
            "was kannst du",
            "was können sie",
            "was kannst du so",
            "welche fähigkeiten",
            "welche tools",
            "was kannst du alles",
            "kannst du",
            "was bist du",
            "wer bist du",
            "was für tools",
            "welche fähigkeiten hast",
            "was kannst du mir",
            "welche möglichkeiten",
            "was kannst du tun",
            "was sind deine fähigkeiten",
            "was sind deine tools",
            "welche tools hast du",
            "welche tools kannst du",
            # Englisch
            "what can you do",
            "what are your capabilities",
            "what tools do you have",
            "what tools can you",
            "what are your tools",
            "what can you",
            "who are you",
            "what are you",
            "what abilities",
            "what functions",
            "list your tools",
            "show me your tools",
            "tell me what you can",
            # Kombiniert
            "deine fähigkeiten",
            "deine tools",
            "deine möglichkeiten",
            "kannst du dateien",
            "kannst du suchen",
            "kannst du code",
            "kannst du web",
            "kannst du bilder",
            "kannst du finanz",
        ]

        return any(pattern in lower for pattern in meta_patterns)

    def _requires_filesystem_tooling(self, user_prompt: str) -> bool:
        """Detect whether the prompt explicitly requires local filesystem tools.

        Root-cause fix: LLM-only routing can classify filesystem requests as SIMPLE,
        which prevents tool execution and leads to generic "no filesystem access"
        answers. This deterministic gate ensures filesystem intents reach
        PLAN_EXECUTE.
        """
        text = (user_prompt or "").strip()
        if not text:
            return False

        import re as _re

        lower = text.lower()

        # Strong signal: explicit absolute path (Windows or Unix style)
        if _re.search(r"[a-zA-Z]:[\\/]", text):
            return True
        if _re.search(r"(^|\s)/(?:[^\s]+/)*[^\s]+", text):
            return True

        nouns = {
            "datei", "dateien", "ordner", "verzeichnis", "pfad",
            "file", "files", "folder", "directory", "path",
        }
        verbs = {
            "suche", "such", "finde", "finden", "durchsuche", "durchsuchen",
            "liste", "auflisten", "zeige", "öffne", "lesen",
            "search", "find", "list", "show", "open", "read",
        }

        has_noun = any(token in lower for token in nouns)
        has_verb = any(token in lower for token in verbs)
        return has_noun and has_verb

    def _select_agent_execution_mode(self, user_prompt: str) -> str:
        """Semantic execution routing: SIMPLE vs PLAN_EXECUTE vs REACT.

        Returns one of: SIMPLE, PLAN_EXECUTE, REACT.
        """
        if not self.agent_mode_enabled:
            return "SIMPLE"

        # Deterministische Gates (vor LLM-Routing, um Race-Conditions zu vermeiden)
        if self._requires_filesystem_tooling(user_prompt):
            self._last_routing_debug = (
                "🔍 Execution-Mode Routing\n"
                "   deterministic_override=filesystem_intent\n"
                "   selected_mode=PLAN_EXECUTE"
            )
            return "PLAN_EXECUTE"

        if self._is_meta_capability_query(user_prompt):
            self._last_routing_debug = (
                "🔍 Execution-Mode Routing\n"
                "   deterministic_override=meta_capability_query\n"
                "   selected_mode=PLAN_EXECUTE"
            )
            return "PLAN_EXECUTE"

        use_llm_routing = self.chat_routing_config.get("use_llm_routing", True)
        if not (use_llm_routing and hasattr(self, 'model_loader') and self.model_loader):
            self._last_routing_debug = (
                "⚠️ LLM-Routing nicht verfügbar -> PLAN_EXECUTE (sicherer Default)."
            )
            return "PLAN_EXECUTE"

        react_allowed = self._is_react_enabled()
        mode_instructions = (
            "SIMPLE = direkte Antwort ohne externe Tools oder mehrstufige Planung.\n"
            "PLAN_EXECUTE = kompakte Tool-Aufrufe, deren vollständige Parameter vor der Ausführung feststehen.\n"
            "REACT = iteratives Beobachten->Handeln mit strukturierten Tool-Argumenten und möglichem Re-Planning.\n"
            "Wähle REACT für ein vollständiges ausführbares Programm, Skript, Spiel oder eine App, "
            "weil Code ausgeführt, geprüft, gegebenenfalls repariert und als Datei ausgeliefert werden muss.\n"
            "Wähle REACT außerdem bei hoher Unsicherheit, widersprüchlicher Evidenz oder klar nötiger iterativer Nachrecherche.\n"
            "Wähle bei Unsicherheit PLAN_EXECUTE.\n"
            "WICHTIG: Fragen nach eigenen Fähigkeiten, Tools oder Identität ('Was kannst du?', "
            "'Welche Tools hast du?', 'Wer bist du?') sind NICHT simple — "
            "sie erfordern PLAN_EXECUTE, damit der Agent seine Tool-Liste bereitstellen kann."
        )
        if not react_allowed:
            mode_instructions += "\nREACT ist deaktiviert. Nutze nur SIMPLE oder PLAN_EXECUTE."

        strict_prompt = (
            "Klassifiziere die Anfrage semantisch in genau einen Ausführungsmodus:\n"
            f"{mode_instructions}\n\n"
            f"Anfrage: {user_prompt}\n"
            "Antworte NUR mit: FINAL_MODE=<SIMPLE|PLAN_EXECUTE|REACT>"
        )

        try:
            text_response = self.model_loader.generate_response(
                max_tokens=160,
                temperature=0.0,
                messages=[{"role": "user", "content": strict_prompt}],
            )
            mode = self._extract_execution_mode(text_response)
            if mode == "REACT" and not react_allowed:
                mode = "PLAN_EXECUTE"
            if mode is None:
                mode = "PLAN_EXECUTE"
            self._last_routing_debug = (
                f"🔍 Execution-Mode Routing\n"
                f"   react_allowed={react_allowed}\n"
                f"   llm_response={str(text_response).strip()}\n"
                f"   selected_mode={mode}"
            )
            return mode
        except Exception as e:
            self._last_routing_debug = (
                f"❌ Execution-Mode Routing Fehler: {type(e).__name__} -> PLAN_EXECUTE"
            )
            return "PLAN_EXECUTE"

    def _get_or_create_react_agent(self) -> ReActAgent:
        """Create and cache ReActAgent from current runtime components."""
        if self.react_agent is not None:
            return self.react_agent
        if self.orchestrator is None:
            raise RuntimeError("ReAct-Agent kann nicht initialisiert werden: Orchestrator fehlt.")
        self.react_agent = ReActAgent(
            model_loader=self.model_loader,
            toolkit=self.agent_toolkit,
            tool_manager=getattr(self.orchestrator, "tools", None),
            verification_manager=getattr(self.orchestrator, "verification_manager", None),
            summarizer_max_tokens=getattr(self.orchestrator, "summarizer_max_tokens", 4096),
            max_iterations=int(self.settings.get("react_max_iterations", 8)),
        )
        return self.react_agent

    def _react_agent_chat(self, user_prompt: str, image_path: Optional[str] = None, progress_callback=None, stream_context: Optional[StreamingContext] = None) -> str:
        """Execute iterative ReAct pipeline and synchronize UI-visible state."""
        if stream_context is not None and stream_context.is_cancelled:
            raise StreamingCancelled()
        if progress_callback:
            progress_callback("ReAct-Agent", "Starte iterativen ReAct-Workflow")

        react_agent = self._get_or_create_react_agent()
        result = react_agent.run(
            query=user_prompt,
            history=self.message_history,
            image_path=image_path,
            settings=self.settings,
            stream_callback=None,
        )

        if stream_context is not None and stream_context.is_cancelled:
            raise StreamingCancelled()

        text = str(result.get("text", "")).strip()
        if not text:
            raise RuntimeError("ReAct-Agent lieferte keine finale Antwort.")

        raw_sources = result.get("sources", []) or []
        normalized_sources: List[Dict[str, Any]] = []
        for source in raw_sources:
            if isinstance(source, dict):
                normalized_sources.append({
                    "title": source.get("title", ""),
                    "url": source.get("url"),
                    "date": source.get("date"),
                    "snippet": source.get("snippet"),
                })

        self.last_sources = normalized_sources
        self.last_graphics = []
        self.last_files = []
        for artifact in result.get("artifacts", []) or []:
            if not isinstance(artifact, dict):
                continue
            if artifact.get("type") == "file":
                file_path = artifact.get("path")
                file_name = artifact.get("name") or (os.path.basename(file_path) if file_path else "")
                if file_path and file_name:
                    self.last_files.append({
                        "path": file_path,
                        "name": file_name,
                        "size": int(artifact.get("size", 0) or 0),
                        "media_type": artifact.get("media_type") or "application/octet-stream",
                        "caption": "Erzeugtes Programm" if file_name.lower().endswith(".py") else "Erzeugte Datei",
                    })
                continue
            path = artifact.get("path")
            data_base64 = artifact.get("data_base64") or artifact.get("plot_base64")
            if bool(path) == bool(data_base64):
                continue
            self.last_graphics.append({
                "type": "image",
                "path": path or None,
                "data_base64": data_base64 or None,
                "media_type": artifact.get("media_type") or "image/png",
                "caption": artifact.get("caption") or "Generiertes Diagramm",
                "diagram_type": artifact.get("diagram_type") or None,
                "backend": artifact.get("backend") or artifact.get("tool") or None,
            })
        trace_data = result.get("trace", {}) or {}
        self.last_trace = AgentTrace(
            planner_output="react_agent",
            planned_tools=list(trace_data.get("tools_used", []) or []),
            ran_tools=list(trace_data.get("tools_used", []) or []),
            evidence_domains=[],
            extras_count=0,
            summarizer_draft_chars=len(text),
            verifier_changed=False,
            planner_ms=0,
            reasoning="ReAct iterative execution",
            critique=None,
        )
        self.last_followup_questions = []
        self.message_history.append({"role": "user", "content": user_prompt})
        self.message_history.append({"role": "assistant", "content": text})
        return text

    def enable_llm_routing(self, enabled: bool = True):
        """
        Aktiviert/deaktiviert das LLM-basierte intelligente Routing.
        
        Args:
            enabled: True fuer LLM-basierte Entscheidung, False fuer sicheren PLAN_EXECUTE-Default
        """
        self.chat_routing_config["use_llm_routing"] = enabled
        mode = "LLM-basiert" if enabled else "Sicherer PLAN_EXECUTE-Default"
        print(f"🧠 Chat-Routing Modus: {mode}")

    def configure_chat_routing(self, **kwargs):
        """
        Konfiguriere das intelligente Chat-Routing.
        
        Beispiel:
        configure_chat_routing(
            enabled=True,
            use_llm_routing=True
        )
        """
        for key, value in kwargs.items():
            if key in self.chat_routing_config:
                self.chat_routing_config[key] = value
                print(f"🔧 Chat-Routing: {key} = {value}")
            else:
                print(f"⚠️ Unbekannte Chat-Routing-Option: {key}")

    def stream_chat_events(
        self,
        user_prompt: str,
        *,
        session_id: str,
        image_path: Optional[str] = None,
        search_depth: Optional[int] = None,
        faiss_min_confidence: Optional[float] = None,
    ) -> Iterator[ChatEvent]:
        """Run chat work in a producer thread and yield ordered typed events."""
        event_queue: queue.Queue[ChatEvent | object] = queue.Queue()
        sentinel = object()
        context = StreamingContext(session_id=session_id, sink=event_queue.put)

        def produce() -> None:
            history_snapshot = copy.deepcopy(self.message_history)
            partial_text: list[str] = []
            text_started = False
            active_step_id: str | None = None
            step_counter = 0

            def finish_active_step(status: str = "completed") -> None:
                nonlocal active_step_id
                if active_step_id is not None and not context.is_terminal:
                    context.emit(
                        StepFinished,
                        step_id=active_step_id,
                        status=status,
                    )
                    active_step_id = None

            def on_progress(step: str, details: str = "") -> None:
                nonlocal active_step_id, step_counter
                if context.is_cancelled:
                    raise StreamingCancelled("".join(partial_text))
                finish_active_step()
                step_counter += 1
                active_step_id = f"step-{step_counter}"
                label = f"{step}: {details}" if details else step
                context.emit(StepStarted, step_id=active_step_id, label=label)

            def on_text(chunk: str) -> None:
                nonlocal text_started
                if context.is_cancelled:
                    raise StreamingCancelled("".join(partial_text))
                if not text_started:
                    context.emit(TextStarted, message_id=context.message_id)
                    text_started = True
                partial_text.append(chunk)
                context.emit(TextDelta, message_id=context.message_id, delta=chunk)

            _ACTIVE_CHAT_RUNS.register(context)
            try:
                context.emit(RunStarted, message_id=context.message_id)
                response = self.chat(
                    user_prompt,
                    image_path=image_path,
                    progress_callback=on_progress,
                    stream_callback=on_text,
                    search_depth=search_depth,
                    faiss_min_confidence=faiss_min_confidence,
                    stream_context=context,
                )
                if context.is_cancelled:
                    raise StreamingCancelled("".join(partial_text))

                finish_active_step()
                if not text_started:
                    context.emit(TextStarted, message_id=context.message_id)
                    text_started = True
                    if response:
                        partial_text.append(response)
                        context.emit(
                            TextDelta,
                            message_id=context.message_id,
                            delta=response,
                        )
                context.emit(TextFinished, message_id=context.message_id)

                sources = list(self.last_sources or [])
                if sources:
                    context.emit(SourcesUpdated, sources=sources)
                context.emit(UsageUpdated, ttft_ms=(
                    int((context.first_text_at - context.started_at) * 1000)
                    if context.first_text_at is not None
                    else None
                ))
                last_trace = self.last_trace
                trace = last_trace.model_dump() if last_trace is not None and hasattr(last_trace, "model_dump") else None
                result = ChatRunResult(
                    text=response,
                    sources=sources,
                    followup_questions=list(self.last_followup_questions or []),
                    graphics=list(getattr(self, "last_graphics", []) or []),
                    files=list(getattr(self, "last_files", []) or []),
                    trace=trace,
                    metrics={"duration_ms": context.elapsed_ms()},
                )
                context.emit(RunCompleted, result=result)
            except StreamingCancelled as exc:
                self.message_history = history_snapshot
                finish_active_step("cancelled")
                if text_started and not context.is_terminal:
                    context.emit(TextFinished, message_id=context.message_id)
                if not context.is_terminal:
                    context.emit(
                        RunCancelled,
                        partial_text=exc.partial_text or "".join(partial_text),
                    )
            except Exception as exc:
                self.message_history = history_snapshot
                logger.exception("Streaming chat run failed")
                finish_active_step("failed")
                if text_started and not context.is_terminal:
                    context.emit(TextFinished, message_id=context.message_id)
                if not context.is_terminal:
                    context.emit(
                        RunFailed,
                        error_code=type(exc).__name__,
                        message=str(exc),
                        partial_text="".join(partial_text),
                    )
            finally:
                _ACTIVE_CHAT_RUNS.finish(context)
                event_queue.put(sentinel)

        worker = threading.Thread(
            target=produce,
            name=f"chat-stream-{context.run_id[:8]}",
            daemon=True,
        )
        worker.start()
        while True:
            item = event_queue.get()
            if item is sentinel:
                break
            yield item  # type: ignore[misc]
        worker.join()

    @staticmethod
    def cancel_stream(session_id: str) -> bool:
        """Request cooperative cancellation for the active session run."""
        return _ACTIVE_CHAT_RUNS.cancel(session_id)

    def chat(self, user_prompt: str, image_path: Optional[str] = None, progress_callback=None, stream_callback=None, stream_context: Optional[StreamingContext] = None, search_depth: Optional[int] = None, faiss_min_confidence: Optional[float] = None) -> str:
        """
        Hauptchat-Methode — Wrapper mit GARANTIERTER Follow-Up-Fragen-Generierung.

        Delegiert an _chat_core() und stellt sicher, dass self.last_followup_questions
        IMMER befüllt wird, unabhängig vom internen Code-Pfad.

        Args:
            user_prompt: Die Benutzereingabe
            image_path: Optionaler Pfad zu einem Bild
            progress_callback: Optional function(step: str, details: str = "") für Live-Updates
            stream_callback: Optional function(chunk: str) für Token-by-Token Streaming (nur SIMPLE-Pfad)
            search_depth: Optional RAG-K Wert aus GUI (überschreibt Standard)
            faiss_min_confidence: Optional FAISS Confidence Threshold aus GUI (überschreibt adaptiven Wert)
        """
        # Reset vor jedem Chat
        self.last_followup_questions = []
        self._last_route_mode = "UNKNOWN"

        # Eigentliche Chat-Logik
        response = self._chat_core(
            user_prompt,
            image_path,
            progress_callback,
            search_depth,
            faiss_min_confidence,
            stream_callback,
            stream_context,
        )
        
        # ====================================================================
        # GARANTIERTE Follow-Up-Fragen-Generierung (Post-Processing)
        # Wenn kein interner Pfad Fragen gesetzt hat → hier nachholen.
        # ====================================================================
        if not self.last_followup_questions and response and isinstance(response, str) and len(response.strip()) > 50:
            try:
                # Schicht 1: Extrahiere aus dem Response-Text (z.B. [FOLLOW_UP] Block)
                from utils.followup_question_extractor import extract_followup_questions
                _, extracted = extract_followup_questions(response)
                if extracted:
                    self.last_followup_questions = extracted
                    logger.info(f"✅ Post-Processing: {len(extracted)} Follow-Up-Fragen aus Response extrahiert")
                elif (
                    getattr(self, '_last_route_mode', "UNKNOWN") == "AGENT"
                    and hasattr(self, 'orchestrator')
                    and self.orchestrator
                ):
                    # Schicht 2: Dedizierter LLM-Call als Fallback
                    self.last_followup_questions = self.orchestrator._generate_followup_questions(user_prompt, response)
                    logger.info(f"✅ Post-Processing: {len(self.last_followup_questions)} Follow-Up-Fragen per LLM generiert")
            except Exception as e:
                logger.warning(f"⚠️ Garantierte Follow-Up-Generierung fehlgeschlagen: {e}")
                self.last_followup_questions = []
        
        return response

    def _try_handle_pending_permission_consent(self, user_prompt: str) -> Optional[str]:
        """Apply pending sandbox permission requests when the user gives explicit consent."""
        if not self.agent_toolkit or not hasattr(self.agent_toolkit, "get_pending_permission_request"):
            return None

        pending = self.agent_toolkit.get_pending_permission_request()
        if not pending:
            return None

        text = (user_prompt or "").strip().lower()
        if not text:
            return None

        deny_tokens = ["nein", "nicht", "kein zugriff", "ablehnen", "don't", "do not", "deny"]
        if any(token in text for token in deny_tokens):
            self.agent_toolkit.clear_pending_permission_request()
            return (
                "Freigabeanfrage verworfen. Der Pfad bleibt gesperrt. "
                "Wenn du später Zugriff erlauben willst, sage z.B. 'temporär freigeben'."
            )

        approve_tokens = ["ja", "erlaube", "freigeben", "zugriff", "ok", "okay", "yes", "allow"]
        if not any(token in text for token in approve_tokens):
            return None

        persistent_tokens = ["dauerhaft", "permanent", "persist", "allowlist", "ständig"]
        grant_mode = "persistent" if any(token in text for token in persistent_tokens) else "temporary"
        result = self.agent_toolkit.grant_pending_path_access(mode=grant_mode)
        if not result.get("success"):
            return (
                "Freigabe konnte nicht angewendet werden: "
                f"{result.get('error', 'unbekannter Fehler')}"
            )

        requested_path = str(pending.get("requested_path") or "")
        mode_label = "dauerhaft (Allowlist)" if result.get("grant_mode") == "persistent" else "temporär (nur aktuelle Runtime)"
        return (
            f"Freigabe aktiv: {mode_label} für {result.get('granted_base_dir')}. "
            f"Du kannst die ursprüngliche Datei-Anfrage jetzt erneut stellen ({requested_path}). "
            "Hinweis: Dieser Zugriffsweg liefert nur Stream-Inhalt für die Antwort und speichert nichts im RAG."
        )

    def _extract_windows_file_path(self, user_prompt: str) -> Optional[str]:
        """Extract first explicit Windows-style absolute file path from user prompt."""
        text = user_prompt or ""
        # Capture C:\... style paths ending with common text/pdf-like extensions.
        pattern = r"([A-Za-z]:\\[^\n\r\"']+\.(?:pdf|txt|csv|md|json|py|log))"
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if not match:
            return None
        return match.group(1).strip()

    def _try_direct_local_file_query(self, user_prompt: str) -> Optional[str]:
        """Fast path for explicit local-file questions to avoid planner stalls.

        This path is intentionally deterministic for prompts that contain an
        explicit Windows file path and ask to read/extract content.
        """
        if not self.agent_toolkit:
            return None

        lower = (user_prompt or "").lower()
        if not any(token in lower for token in ["lies", "read", "datei", "file", "pdf"]):
            return None

        file_path = self._extract_windows_file_path(user_prompt)
        if not file_path:
            return None

        if file_path.lower().endswith(".pdf"):
            result = self.agent_toolkit.execute_tool(
                "pdf_extract",
                {"file_path": file_path, "max_chars": 20_000},
            )
            content = str(result.get("text", "")) if isinstance(result, dict) else ""
        else:
            result = self.agent_toolkit.execute_tool(
                "file_reader",
                {"file_path": file_path, "encoding": "utf-8"},
            )
            content = str(result.get("content", "")) if isinstance(result, dict) else ""

        if not isinstance(result, dict):
            return None

        if not result.get("success"):
            if result.get("needs_user_permission"):
                suggested = str(
                    result.get("suggested_user_prompt")
                    or "Ich habe keinen Zugriff auf diesen Pfad. Soll ich eine Freigabe anfragen?"
                )
                return f"{suggested}\nAngefragter Pfad: {file_path}"
            return f"Dateizugriff fehlgeschlagen: {result.get('error', 'Unbekannter Fehler')}"

        # If user explicitly asks for a project title, extract it deterministically.
        if "projekttitel" in lower or "project title" in lower:
            title_match = re.search(r"(?im)^\s*projekttitel\s*:\s*(.+)$", content)
            if title_match:
                return title_match.group(1).strip()

        snippet = content.strip()[:1200]
        if not snippet:
            return "Die Datei wurde gelesen, enthält aber keinen extrahierbaren Text."
        return f"Dateiinhalt (Auszug):\n{snippet}"

    def _chat_core(self, user_prompt: str, image_path: Optional[str] = None, progress_callback=None, search_depth: Optional[int] = None, faiss_min_confidence: Optional[float] = None, stream_callback=None, stream_context: Optional[StreamingContext] = None) -> str:
        """
        Interne Chat-Logik mit allen Routing-Pfaden.
        
        Args:
            user_prompt: Die Benutzereingabe
            image_path: Optionaler Pfad zu einem Bild
            progress_callback: Optional function(step: str, details: str = "") für Live-Updates
            search_depth: Optional RAG-K Wert aus GUI (überschreibt Standard)
            faiss_min_confidence: Optional FAISS Confidence Threshold aus GUI (überschreibt adaptiven Wert)
        """
        
        self.last_graphics = []
        self.last_files = []

        permission_reply = self._try_handle_pending_permission_consent(user_prompt)
        if permission_reply is not None:
            self.message_history.append({"role": "user", "content": user_prompt})
            self.message_history.append({"role": "assistant", "content": permission_reply})
            self.last_followup_questions = []
            return permission_reply

        direct_file_reply = self._try_direct_local_file_query(user_prompt)
        if direct_file_reply is not None:
            self.message_history.append({"role": "user", "content": user_prompt})
            self.message_history.append({"role": "assistant", "content": direct_file_reply})
            self.last_followup_questions = []
            return direct_file_reply

        # NEU: Setze RAG-K und FAISS-Confidence wenn von GUI übergeben
        self.logger.info(f"🔍 DEBUG: search_depth Parameter = {search_depth}, faiss_min_confidence = {faiss_min_confidence}, orchestrator = {self.orchestrator is not None}")
        if self.orchestrator:
            # RAG-K konfigurieren
            if search_depth is not None:
                self.orchestrator.set_rag_config(k=search_depth)
                self.logger.info(f"🔍 RAG-K von GUI überschrieben: {search_depth}")
            elif search_depth is None:
                self.logger.warning(f"⚠️ search_depth ist None - nutze Standard-Wert")
            
            # FAISS-Confidence konfigurieren (NEW 2025-10-11)
            if faiss_min_confidence is not None:
                self.orchestrator.set_rag_config(faiss_min_confidence=faiss_min_confidence)
                self.logger.info(f"🎯 FAISS Confidence von GUI überschrieben: {faiss_min_confidence}")
            else:
                self.logger.info(f"ℹ️ faiss_min_confidence ist None - nutze adaptive Confidence")
        else:
            self.logger.warning(f"⚠️ Orchestrator ist None - kann RAG-Konfiguration nicht setzen")
        
        # === CONTEXT WINDOW MANAGEMENT ===
        # Automatische Context-Optimierung mit ChatContextManager
        # Setze Flag, um doppelte Verarbeitung in der Basisklasse zu verhindern
        self._context_managed_by_agent = True
        
        if hasattr(self, 'context_manager') and self.context_manager:
            try:
                # Konvertiere Message History in LLM-Format für Context-Analyse
                llm_messages = []
                
                # System-Prompt hinzufügen
                if self.system_prompt:
                    llm_messages.append({
                        'role': 'system',
                        'content': self.system_prompt
                    })
                
                # Bisherige Message History hinzufügen
                for msg in self.message_history:
                    llm_messages.append({
                        'role': msg.get('role', 'user'),
                        'content': msg.get('content', '')
                    })
                
                # Aktuelle User-Message hinzufügen
                llm_messages.append({
                    'role': 'user',
                    'content': user_prompt
                })
                
                # Prüfe ob Context-Optimierung nötig ist
                if self.context_manager.should_summarize(llm_messages):
                    if progress_callback:
                        progress_callback("Context-Optimierung", "Automatische Chat-Zusammenfassung zur Token-Optimierung...")
                    
                    # Wende Context-Management an
                    optimized_messages, summary_info = self.context_manager.manage_context(llm_messages)
                    
                    if summary_info and summary_info.get('saved_tokens', 0) > 0:
                        print(f"🧠 AgentChatbotLogic: Context-Optimierung durchgeführt: {summary_info.get('saved_tokens', 0)} Token eingespart")
                        
                        # Aktualisiere Message History mit optimierten Messages
                        self.message_history = []
                        for msg in optimized_messages:
                            if msg['role'] != 'system':  # System-Prompt separat behandeln
                                self.message_history.append({
                                    'role': msg['role'],
                                    'content': msg['content']
                                })
                        
                        # Entferne die aktuelle User-Message aus der History (wird später hinzugefügt)
                        if self.message_history and self.message_history[-1]['role'] == 'user':
                            self.message_history.pop()
                
            except Exception as e:
                print(f"⚠️ Context-Management-Fehler: {e}")
                # Fahre normal fort, wenn Context-Management fehlschlägt
        else:
            print("⚠️ Context Manager nicht verfügbar in AgentChatbotLogic")
        
        complexity_info = None
        original_max_tokens = self.settings.get('max_tokens', 4096)
        
        # === PSYCHOLOGISCHE UNTERSTÜTZUNG: Wird zentral in Streamlit verwaltet ===
        # Das WellbeingSessionInterface wird nicht mehr in agent_chatbot_logic initialisiert,
        # sondern zentral in enhanced_streamlit_bot.py (st.session_state.wellbeing_interface)
        # Dort erfolgt auch das Routing zum Psychologie-Tab
        print(f"[DEBUG] Psychologisches Interface: Zentral in Streamlit verwaltet")
        
        # Zurücksetzen des Check-Status zu Beginn
        self._last_message_checked = False
        
        # WICHTIG: Bei Bildern SOFORT das multimodale Modell nutzen
        # Kein Agent-System für Bildanalyse - das kann das Modell selbst!
        # Bilder werden NICHT gecacht!
        if image_path and os.path.exists(image_path):
            if stream_context is not None:
                stream_context.emit(RouteSelected, selected_route="vision", route="vision")
            print(f"[DEBUG] Bild erkannt: {image_path} - nutze multimodales Modell direkt")
            
            # Wende Bildanalyse-Konfiguration an
            self._apply_image_analysis_config()
            
            # WICHTIG: Verwende ChatbotLogic OHNE doppelte Context-Verarbeitung
            # Context wurde bereits oben behandelt, deshalb direkt build_message_block verwenden
            try:
                messages = self.build_message_block(user_prompt, image_path)
                response = self.model_loader.generate_response(
                    max_tokens=token_scaling.main_generation_max_tokens(
                        fallback=2048, current=self.settings.get("max_tokens", 2048)
                    ),
                    temperature=self.settings.get("temperature", 0.7),
                    messages=messages,
                    image_path=image_path
                )
                
                # Message History aktualisieren
                self.message_history.append(messages[-1])
                self.message_history.append({"role": "assistant", "content": response})
                
                return response
            except Exception as e:
                if stream_context is not None:
                    raise
                print(f"❌ Fehler bei Bildverarbeitung: {e}")
                return f"Fehler bei der Bildanalyse: {e}"
        
        # Progress-Callback-System für Live-Updates
        def notify_progress(step: str, details: str = ""):
            """Benachrichtige über Fortschritt"""
            if progress_callback:
                try:
                    progress_callback(step, details)
                except StreamingCancelled:
                    raise
                except Exception as exc:
                    logger.debug(f"progress_callback raised: {exc}")
            print(f"🔄 [{step}] {details}")  # Console fallback
        
        start_time = time.time()
        self.performance_stats['total_requests'] += 1
        
        # Cache-Key vorbereiten. Der Lookup erfolgt erst nach dem Routing, weil
        # Tool-Läufe und Dateiartefakte nicht durch reinen Text ersetzt werden dürfen.
        cache_key = self._generate_cache_key(user_prompt, image_path)
        
        try:
            # Bestimme Chat-Modus (Standard vs Agent)
            notify_progress("Routing-Analyse", "Bestimme ob Agent-Modus oder normaler Chat erforderlich")
            
            if not self.agent_mode_enabled:
                notify_progress("Standard Chat", "Agent-Modus deaktiviert - verwende normalen Chat")
                self._last_route_mode = "STANDARD"
                if stream_context is not None:
                    stream_context.emit(RouteSelected, selected_route="simple", route="simple")
                return self._normal_chat_with_time_context(
                    user_prompt,
                    image_path,
                    stream_callback,
                    stream_context,
                )

            execution_mode = self._select_agent_execution_mode(user_prompt)

            if execution_mode == "SIMPLE":
                cached_response = self._get_cached_response(cache_key)
                if cached_response:
                    self.performance_stats['cache_hits'] += 1
                    if stream_context is not None:
                        stream_context.emit(RouteSelected, selected_route="cache", route="cache")
                    notify_progress("Cache-Hit", "Antwort aus Cache geladen")
                    return cached_response
                self.performance_stats['cache_misses'] += 1
                notify_progress("Einfacher Chat", "Semantisches Routing: SIMPLE")
                self._last_route_mode = "SIMPLE"
                if stream_context is not None:
                    stream_context.emit(RouteSelected, selected_route="simple", route="simple")
                simple_response = self._normal_chat_with_time_context(
                    user_prompt,
                    image_path,
                    stream_callback,
                    stream_context,
                )
                self._cache_response(cache_key, simple_response)
                # Hinweis: Pattern-basiertes Auto-Retry („Uncertainty Detection“) wurde
                # 2026-06-02 als Root-Cause-Fix entfernt. Es feuerte auf normale
                # Hedging-Wörter wie „wahrscheinlich“/„möglicherweise“ und triggerte
                # selbst bei vollständig dokument-gegrundeten Antworten einen
                # AGENT-Web-Recherche-Pfad, der die UI blockierte. Falls eine
                # selbstkritische Konfidenz-Bewertung nötig wird, muß sie LLM-
                # basiert (kein Pattern-Match) und ohne Web-Fetch im SIMPLE-
                # Pfad implementiert werden — siehe Memory-Note Anti-Patterns.
                return simple_response

            if execution_mode == "REACT":
                notify_progress("ReAct-Agent", "Semantisches Routing: iterativer ReAct-Modus")
                self._last_route_mode = "REACT"
                if stream_context is not None:
                    stream_context.emit(RouteSelected, selected_route="react", route="react")
                result = self._react_agent_chat(
                    user_prompt,
                    image_path,
                    notify_progress,
                    stream_context,
                )
                elapsed = time.time() - start_time
                self.performance_stats['avg_response_time'] = (
                    (self.performance_stats['avg_response_time'] * (self.performance_stats['total_requests'] - 1) + elapsed)
                    / self.performance_stats['total_requests']
                )
                return result

            else:
                notify_progress("Agent-Modus", "Semantisches Routing: PLAN_EXECUTE")
                self._last_route_mode = "AGENT"
                if stream_context is not None:
                    stream_context.emit(
                        RouteSelected,
                        selected_route="plan_execute",
                        route="plan_execute",
                    )

                # LLM complexity classification is only needed in AGENT mode.
                if self.reasoning_optimizer and user_prompt:
                    try:
                        complexity_info = self.reasoning_optimizer.estimate_complexity_with_llm(
                            query=user_prompt,
                            has_image=(image_path is not None)
                        )

                        if complexity_info in ["simple", "medium", "complex"]:
                            from ministral_reasoning_optimizer import TOKEN_BUDGETS
                            budget = TOKEN_BUDGETS[complexity_info]
                            self.settings['max_tokens'] = budget.max_tokens

                            if progress_callback:
                                progress_callback(
                                    "🧠 Reasoning-Optimizer",
                                    f"Komplexität: {complexity_info.upper()} | Token-Budget: {budget.max_tokens} | Reasoning: {'✓' if budget.enable_reasoning else '✗'}"
                                )

                            print(f"🧠 Reasoning-Optimizer: Komplexität={complexity_info}, Token-Budget={budget.max_tokens} (vorher: {original_max_tokens})")
                            self.logger.info(f"Reasoning: {complexity_info}, Budget: {budget.max_tokens}, Reasoning: {budget.enable_reasoning}")
                            self._last_complexity = complexity_info
                            self._last_token_budget = budget.max_tokens
                            self._last_reasoning_enabled = budget.enable_reasoning
                        else:
                            self.logger.warning(f"⚠️ Unbekannte Komplexität: {complexity_info} - nutze Standard-Budget")
                            complexity_info = None
                    except Exception as e:
                        print(f"⚠️ Reasoning-Optimizer Fehler: {e}")
                        self.logger.warning(f"Reasoning-Optimizer Fehler: {e}")
                        complexity_info = None
                        self.settings['max_tokens'] = original_max_tokens
                elif not self.reasoning_optimizer:
                    self.logger.debug("ℹ️ Reasoning-Optimizer nicht verfügbar - nutze Standard-Budget")

                result = self._agent_chat(user_prompt, image_path, notify_progress, search_depth)
                
                # Performance tracking
                elapsed = time.time() - start_time
                self.performance_stats['avg_response_time'] = (
                    (self.performance_stats['avg_response_time'] * (self.performance_stats['total_requests'] - 1) + elapsed) 
                    / self.performance_stats['total_requests']
                )
                
                return result
                
        except Exception as e:
            if isinstance(e, StreamingCancelled) or stream_context is not None:
                raise
            notify_progress("Fehler", f"Chat-Fehler aufgetreten: {str(e)}")
            print(f"❌ Chat-Fehler: {e}")
            return f"Entschuldigung, es gab einen Fehler bei der Verarbeitung Ihrer Anfrage: {str(e)}"
        
        finally:
            # ================================================================
            # NEU: REASONING-OPTIMIZER - Token-Budget wiederherstellen
            # ================================================================
            if complexity_info is not None and 'original_max_tokens' in locals():
                self.settings['max_tokens'] = original_max_tokens
                self.logger.debug(f"🔄 Token-Budget wiederhergestellt: {original_max_tokens}")

    
    def _optimized_research_chat(self, user_prompt: str, image_path: Optional[str] = None, search_depth: Optional[int] = None, progress_callback=None) -> str:
        """Optimized Research Chat mit der neuen 2025 Research Engine"""
        
        # NEU: Setze RAG-K wenn von Parametern übergeben
        if search_depth is not None and self.orchestrator:
            self.orchestrator.set_rag_config(k=search_depth)
            self.logger.info(f"🔍 RAG-K in _optimized_research_chat gesetzt: {search_depth}")
        
        if not self.optimized_research_engine:
            # Fallback zur Standard-Agent-Chat
            return self._standard_agent_chat(
                user_prompt,
                image_path,
                progress_callback,
                search_depth,
            )
        
        try:
            start_time = time.time()
            
            # NEU: Generiere detailliertes Reasoning mit LLM Reasoning Optimizer
            detailed_reasoning = None
            if self.reasoning_optimizer:
                try:
                    model_name = getattr(self.reasoning_optimizer, 'model_name', 'LLM')
                    if progress_callback:
                        progress_callback(
                            "Reasoning-Analyse",
                            f"{model_name} strukturiert die Recherche intern",
                        )
                    print(f"🧠 Generiere detailliertes Reasoning mit {model_name}...")
                    reasoning_result = self.reasoning_optimizer.chat(
                        query=user_prompt,
                        image_path=image_path,
                        conversation_history=self.message_history[-4:] if len(self.message_history) > 0 else [],
                        force_complexity="complex"  # Für Research-Queries immer detailliertes Reasoning
                    )
                    
                    if reasoning_result and "reasoning" in reasoning_result:
                        detailed_reasoning = reasoning_result["reasoning"]
                        print(f"✅ Reasoning generiert: {len(detailed_reasoning)} Zeichen")
                        
                        # Zeige Reasoning-Preview im Log
                        reasoning_lines = detailed_reasoning.split('\n')
                        print(f"   📋 Reasoning-Steps: {len(reasoning_lines)} Zeilen")
                        for i, line in enumerate(reasoning_lines[:5], 1):
                            if line.strip():
                                print(f"      {i}. {line.strip()[:80]}...")
                except Exception as e:
                    print(f"⚠️ Reasoning-Generation fehlgeschlagen: {e}")
                    detailed_reasoning = None
            
            # 1. Optimized Research ausführen
            if progress_callback:
                progress_callback(
                    "Quellensuche",
                    "Durchsuche lokale Wissensbasis und verfügbare Quellen",
                )
            research_result = self.optimized_research_engine.research(
                query=user_prompt,
                k=10,  # Mehr Ergebnisse für bessere Qualität
                max_search_time=20.0,  # Zeitlimit
                quality_threshold=0.7  # Hohe Qualität
            )
            
            print(f"🎯 Research completed: {research_result.strategy_used}, "
                  f"Quality: {research_result.quality_score:.2f}, "
                  f"Confidence: {research_result.confidence.value}")

            if progress_callback:
                progress_callback(
                    "Quellenbewertung",
                    f"{len(research_result.results)} Treffer | Qualität: {research_result.quality_score:.2f}",
                )
            
            # 2. Ergebnisse für LLM aufbereiten
            relevant_results: List[Dict[str, Any]] = []
            sources_text = ""
            if research_result.results:
                # Per-result relevance gate: only include results whose individual
                # similarity score meets a minimum threshold. Without this filter,
                # tangentially related documents (e.g. tax returns or life-values
                # docs surfaced for a budgeting question) are injected verbatim
                # into the prompt — the model then cites them as [Quelle N] even
                # while acknowledging they are off-topic.
                # Threshold 0.7: for broad advisory questions we only keep
                # high-similarity documents; otherwise generic-but-off-topic
                # personal docs leak into the answer as pseudo-citations.
                MIN_RESULT_SCORE = 0.7
                relevant_results = [
                    r for r in research_result.results
                    if r.get("score", 0.0) >= MIN_RESULT_SCORE
                ]
                # Additional domain gate for budget-planning intents:
                # If the query is about personal budgeting, keep only sources
                # that actually contain finance/budget terminology. This avoids
                # unrelated "Haushalt-Routine" pages being treated as evidence.
                query_l = user_prompt.lower()
                budget_query = any(
                    token in query_l
                    for token in [
                        "budget",
                        "haushaltsbudget",
                        "ausgaben",
                        "einnahmen",
                        "spar",
                        "fixkosten",
                    ]
                )
                if budget_query:
                    finance_terms = [
                        "budget",
                        "ausgabe",
                        "einnahme",
                        "kosten",
                        "sparen",
                        "finanz",
                        "fixkosten",
                        "variable kosten",
                        "schuld",
                    ]

                    def _is_budget_relevant(result: dict) -> bool:
                        meta = result.get("metadata", {}) if isinstance(result, dict) else {}
                        title = str(meta.get("title", ""))
                        content = str(result.get("content", "")) if isinstance(result, dict) else ""
                        hay = f"{title}\n{content}".lower()
                        hits = sum(1 for t in finance_terms if t in hay)
                        return hits >= 2

                    relevant_results = [r for r in relevant_results if _is_budget_relevant(r)]
                # If no result clears the threshold, relevant_results is empty.
                # The loop produces nothing, sources_text stays "", and the
                # existing `if sources_text: ... else:` branch below routes
                # to the LLM-knowledge-only prompt — which is the correct
                # behaviour: irrelevant documents must not enter the prompt.
                for i, result in enumerate(relevant_results[:8], 1):
                    content = result.get("content", "")[:1500]
                    title = result.get("metadata", {}).get("title", "Ohne Titel")
                    sources_text += f"{i}. {title}\n{content}...\n\n"
            
            # 3. LLM-Synthese mit Research-Ergebnissen — analytisch, nicht nur zusammenfassend
            if sources_text:
                summary_prompt = f"""Analysiere die folgenden Informationen KRITISCH und beantworte die Frage tiefgehend:

FRAGE: {user_prompt}

VERFÜGBARE INFORMATIONEN:
{sources_text}

ANWEISUNGEN:
- Antworte DIREKT auf die Nutzerfrage. Kein Meta-Text wie "Als Qualitaetssicherer pruefe ich...".
- QUELLENTREUE (HÖCHSTE PRIORITÄT): Gib den Inhalt jeder Quelle EXAKT wieder. Verdrehe NIEMALS
  den Sinn, die Richtung oder die Bedeutung einer Quellenaussage.
  VERBOTEN: Wenn eine Quelle sagt "X wurde nach Y verlagert", darfst du NICHT schreiben
  "X wurde von Y verlagert". Lies den Quellentext WÖRTLICH bevor du paraphrasierst.
- Fasse nicht nur zusammen, sondern ANALYSIERE und BEWERTE die Informationen.
- Ziehe eigenständige Schlussfolgerungen und verbinde Informationen aus verschiedenen Quellen.
- Eigene Analyse baut AUF den korrekt wiedergegebenen Fakten auf — sie verändert die Fakten NICHT.
- Bei Fragen wie "ist das realistisch?", "denke nach", "bewerte" → gib eine substanzielle eigene Einschätzung
  mit konkreten Pro/Contra-Argumenten.
- Benenne Lücken in der Evidenz und was daraus folgt.
- Kennzeichne eigene Analysen als solche (z.B. "Eigene Einschätzung:").
- Strukturiere die Antwort mit Markdown.

FOLGEFRAGEN:
Generiere am Ende deiner Antwort 2-4 weiterführende Folgefragen im Format:
[FOLLOW_UP]Frage1|Frage2|Frage3[/FOLLOW_UP]
Die Fragen sollen konkret zum Thema passen und verschiedene Perspektiven abdecken.
{FOLLOWUP_PERSPECTIVE_INSTRUCTION}"""
            else:
                summary_prompt = f"""Beantworte die folgende Frage basierend auf deinem Wissen. Denke analytisch und tiefgehend:

FRAGE: {user_prompt}

Hinweis: Es konnten keine aktuellen Informationen gefunden werden.
Nutze dein Wissen und logische Schlussfolgerungen für eine fundierte Antwort.

Generiere am Ende 2-4 weiterführende Folgefragen im Format:
[FOLLOW_UP]Frage1|Frage2|Frage3[/FOLLOW_UP]
{FOLLOWUP_PERSPECTIVE_INSTRUCTION}"""
            
            # 4. LLM-Antwort generieren
            if progress_callback:
                progress_callback(
                    "Antwortsynthese",
                    "Formuliere die Antwort aus den freigegebenen Quellen",
                )
            response = self.model_loader.generate_response(
                prompt=summary_prompt,
                max_tokens=token_scaling.main_generation_max_tokens(
                    fallback=4096, current=self.settings.get("max_tokens", 4096)
                )
            )
            
            # 5. Quellen für GUI aufbereiten
            self.last_sources = []
            for result in relevant_results[:10]:
                metadata = result.get("metadata", {})
                self.last_sources.append({
                    "title": metadata.get("title", "Ohne Titel"),
                    "url": metadata.get("url", ""),
                    "date": metadata.get("date", ""),
                    "snippet": result.get("content", "")[:200] + "..."
                })
            
            # 6. Trace für GUI - NEU: Mit detailliertem Reasoning
            reasoning_text = detailed_reasoning if detailed_reasoning else f"Quality: {research_result.quality_score:.2f}, Confidence: {research_result.confidence.value}"
            
            self.last_trace = AgentTrace(
                planner_output=f"Optimized Research: {research_result.strategy_used}",
                planned_tools=["optimized_research"],
                ran_tools=["optimized_research"],
                evidence_domains=[research_result.strategy_used],
                extras_count=len(research_result.results),
                summarizer_draft_chars=len(response),
                verifier_changed=False,
                planner_ms=int(research_result.search_time * 1000),
                reasoning=reasoning_text,  # ✅ NEU: Detailliertes Reasoning vom LLM Reasoning Optimizer
                critique=None,
            )
            
            # 7. Follow-Up-Fragen aus Response extrahieren (Prompt enthält [FOLLOW_UP] Anweisung)
            if progress_callback:
                progress_callback(
                    "Ausgabe finalisieren",
                    "Bereinige Metadaten und bereite Quellen sowie Folgefragen vor",
                )
            try:
                from utils.followup_question_extractor import extract_followup_questions
                clean_response, extracted_followups = extract_followup_questions(response)
                if extracted_followups:
                    self.last_followup_questions = extracted_followups
                    response = clean_response  # [FOLLOW_UP] Block aus Display-Text entfernen
                    logger.info(f"✅ _optimized_research_chat: {len(extracted_followups)} Follow-Up-Fragen extrahiert")
                elif hasattr(self, 'orchestrator') and self.orchestrator:
                    self.last_followup_questions = self.orchestrator._generate_followup_questions(user_prompt, response)
                    logger.info(f"✅ _optimized_research_chat: {len(self.last_followup_questions)} Follow-Up-Fragen generiert (LLM)")
            except Exception as fq_e:
                logger.warning(f"Follow-Up-Generierung fehlgeschlagen (_optimized_research): {fq_e}")
                self.last_followup_questions = []
            
            # 8. History updaten
            self.message_history.append({"role": "user", "content": user_prompt})
            self.message_history.append({"role": "assistant", "content": response})
            
            total_time = time.time() - start_time
            print(f"✅ Optimized Research Chat in {total_time:.2f}s")
            
            return response
            
        except Exception as e:
            print(f"❌ Optimized Research Chat Fehler: {e}")
            # Fallback zur Standard-Agent-Chat
            return self._standard_agent_chat(
                user_prompt,
                image_path,
                progress_callback,
                search_depth,
            )
    
    def _standard_agent_chat(self, user_prompt: str, image_path: Optional[str] = None, progress_callback=None, search_depth: Optional[int] = None) -> str:
        """Standard Agent-Chat-Flow (Backup für Optimized Research)"""
        
        # ========================================================================
        # NEU: REDIS SEMANTIC CACHING (Tag 3 - KURZFRISTIG Priority)
        # Check cache BEFORE orchestrator for semantically similar queries
        # ========================================================================
        if REDIS_CACHING_ENABLED and SEMANTIC_CACHE_AVAILABLE and get_semantic_cache is not None:
            try:
                cache = get_semantic_cache()
                
                # Try to get cached response for semantically similar query
                cached_response = cache.get(user_prompt, query_type='agent')
                
                if cached_response:
                    self.logger.info(f"✅ Semantic Cache HIT for agent query: {user_prompt[:50]}...")
                    print(f"⚡ Semantic Cache HIT - skipping orchestrator")
                    
                    # Update message history and return cached response
                    self.message_history.append({"role": "user", "content": user_prompt})
                    self.message_history.append({"role": "assistant", "content": cached_response})
                    
                    return cached_response
                else:
                    self.logger.debug(f"🔍 Semantic Cache MISS for agent query: {user_prompt[:50]}...")
            
            except Exception as e:
                # Graceful degradation: Continue without cache on error
                self.logger.warning(f"⚠️ Semantic cache error (continuing without cache): {e}")
        
        # Call original implementation
        response = self._standard_agent_chat_impl(user_prompt, image_path, progress_callback, search_depth)
        
        # ========================================================================
        # NEU: CACHE SET after successful response generation
        # ========================================================================
        if REDIS_CACHING_ENABLED and SEMANTIC_CACHE_AVAILABLE and response and get_semantic_cache is not None:
            try:
                cache = get_semantic_cache()
                cache.set(user_prompt, response, query_type='agent')
                self.logger.debug(f"💾 Cached agent response for: {user_prompt[:50]}...")
            except Exception as e:
                # Graceful degradation: Continue without caching on error
                self.logger.warning(f"⚠️ Semantic cache set error (ignoring): {e}")
        
        return response
    
    def _standard_agent_chat_impl(self, user_prompt: str, image_path: Optional[str] = None, progress_callback=None, search_depth: Optional[int] = None) -> str:
        """Standard Agent-Chat-Flow Implementation (wrapped by caching layer)"""
        debug_msg = f"🔍 DEBUG: _standard_agent_chat called.\n   self.orchestrator = {self.orchestrator}\n   orchestrator type = {type(self.orchestrator)}\n   search_depth parameter = {search_depth}"
        print(debug_msg)
        
        # Speichere für Streamlit-Anzeige
        if not hasattr(self, '_last_routing_debug'):
            self._last_routing_debug = ""
        self._last_routing_debug += f"\n\n🔄 [Agent-Modus] Orchestrator-Details:\n{debug_msg}"
        
        # NEU: Setze RAG-K wenn von Parametern übergeben
        if search_depth is not None and self.orchestrator:
            self.orchestrator.set_rag_config(k=search_depth)
            self.logger.info(f"🔍 RAG-K in _standard_agent_chat gesetzt: {search_depth}")
        
        if not self.orchestrator:
            print("❌ DEBUG: No orchestrator available, falling back to super().chat()")
            return self._normal_chat_with_time_context(user_prompt, image_path)
        
        try:
            # Standard-Planner-Flow mit erweiterten System-Prompt
            agent_system_prompt = self._create_agent_system_prompt()
            original_system_prompt = self.system_prompt
            self.system_prompt = agent_system_prompt

            if progress_callback:
                progress_callback(
                    "Analyse vorbereiten",
                    "Kontext und sichere Verarbeitungsschritte werden vorbereitet",
                )
            
            # Zeitkontext setzen
            self._current_time_context = self._build_current_time_context_block()
            
            try:
                # NEU: Generiere detailliertes Reasoning mit Reasoning Optimizer (FALLS VERFÜGBAR)
                detailed_reasoning_from_optimizer = None
                if self.reasoning_optimizer:
                    model_name = getattr(self.reasoning_optimizer, 'model_name', 'Reasoning Optimizer')
                    try:
                        if progress_callback:
                            progress_callback(
                                "Reasoning-Analyse",
                                f"{model_name} strukturiert die Aufgabe intern",
                            )
                        print(f"🧠 Generiere detailliertes Reasoning mit {model_name}...")
                        
                        # 🆕 BEREINIGTE History: Entferne Reasoning-Blöcke aus vorherigen Messages
                        clean_history = []
                        for msg in self.message_history[-4:] if len(self.message_history) > 0 else []:
                            clean_msg = msg.copy()
                            # Entferne Reasoning-Blöcke aus Content
                            if isinstance(clean_msg.get("content"), str):
                                content = clean_msg["content"]
                                # Entferne <thinking>...</thinking> Tags
                                import re
                                content = re.sub(r'<thinking>.*?</thinking>\s*', '', content, flags=re.DOTALL | re.IGNORECASE)
                                # Entferne <answer>...</answer> Tags (behalte nur Inhalt)
                                content = re.sub(r'<answer>(.*?)</answer>', r'\1', content, flags=re.DOTALL | re.IGNORECASE)
                                clean_msg["content"] = content.strip()
                            clean_history.append(clean_msg)
                        
                        reasoning_result = self.reasoning_optimizer.chat(
                            query=user_prompt,
                            image_path=image_path,
                            conversation_history=clean_history,  # ⬅️ Bereinigte History verwenden
                            force_complexity="complex"  # Für Agent-Queries detailliertes Reasoning
                        )
                        
                        if reasoning_result and "reasoning" in reasoning_result:
                            detailed_reasoning_from_optimizer = reasoning_result["reasoning"]
                            
                            # Prüfe ob Reasoning leer ist
                            if not detailed_reasoning_from_optimizer or len(detailed_reasoning_from_optimizer.strip()) == 0:
                                print(f"⚠️ {model_name} generierte leeres Reasoning")
                                print(f"   Answer preview: {reasoning_result.get('answer', 'N/A')[:200]}...")
                                print(f"   Complexity: {reasoning_result.get('complexity', 'N/A')}")
                                print(f"   Tokens used: {reasoning_result.get('tokens_used', 0)}/{reasoning_result.get('token_budget', 0)}")
                                
                                # 🆕 DEBUG: Zeige bereinigte History
                                print(f"   📝 Clean history messages: {len(clean_history)}")
                                for i, msg in enumerate(clean_history):
                                    role = msg.get("role", "?")
                                    content_preview = str(msg.get("content", ""))[:100]
                                    print(f"      {i+1}. [{role}] {content_preview}...")
                                
                                detailed_reasoning_from_optimizer = None
                            else:
                                print(f"✅ {model_name} Reasoning generiert: {len(detailed_reasoning_from_optimizer)} Zeichen")
                                
                                # Zeige Reasoning-Preview im Log
                                reasoning_lines = detailed_reasoning_from_optimizer.split('\n')
                                print(f"   📋 Reasoning-Steps: {len(reasoning_lines)} Zeilen")
                                for i, line in enumerate(reasoning_lines[:5], 1):
                                    if line.strip():
                                        print(f"      {i}. {line.strip()[:80]}...")
                        else:
                            print(f"⚠️ {model_name} Optimizer lieferte keine Reasoning-Daten")
                            print(f"   Result keys: {list(reasoning_result.keys()) if reasoning_result else 'None'}")
                    except Exception as e:
                        print(f"❌ {model_name} Reasoning-Generation fehlgeschlagen: {e}")
                        detailed_reasoning_from_optimizer = None
                
                # Planner verwenden
                time_context = getattr(self, '_current_time_context', None)
                if progress_callback:
                    progress_callback(
                        "Planung",
                        "Erstelle einen ausführbaren Antwort- und Werkzeugplan",
                    )
                calls, final_text, raw_planner, elapsed_ms, reasoning_from_planner, critique = self.orchestrator.planner_step(user_prompt, self.message_history, time_context)

                if progress_callback:
                    progress_callback(
                        "Planung abgeschlossen",
                        f"Direkte Antwort: {'ja' if final_text else 'nein'} | Vorgeschlagene Tools: {len(calls)}",
                    )
                
                # ✅ ÜBERSCHREIBE Orchestrator-Reasoning mit Modell-Reasoning (falls vorhanden)
                # WICHTIG: Diese Variable wird für Trace-Anzeige verwendet!
                final_reasoning_for_trace = reasoning_from_planner  # Default: Orchestrator
                if detailed_reasoning_from_optimizer:
                    model_name = getattr(self.reasoning_optimizer, 'model_name', 'Reasoning Optimizer')
                    final_reasoning_for_trace = detailed_reasoning_from_optimizer
                    print(f"✅ {model_name} Reasoning für Trace adaptiert (ersetzt Basis-Reasoning)")
                else:
                    print(f"✅ Nutze primäres Orchestrator-Reasoning (erfolgreich extrahiert)")
                
                # ================================================================
                # NEU: LLM-BASIERTE POST-PROCESSING BRIDGE für Reasoning → Tool-Execution
                # ================================================================
                # Problem: Planner erstellt gutes Reasoning, aber keine Tool-Calls
                # Lösung: LLM analysiert Reasoning und erstellt strukturierte Tool-Calls
                if not calls and final_reasoning_for_trace and self.agent_toolkit and self.model_loader:
                    import json  # Import hier, damit es im except-Block verfügbar ist

                    if progress_callback:
                        progress_callback(
                            "Tool-Auswahl",
                            "Prüfe den Plan gegen die verfügbaren lokalen Werkzeuge",
                        )
                    
                    print(f"🤖 LLM-BASED BRIDGE: Analysiere Reasoning für Tool-Bedarf...")
                    runtime_tools_block = self._render_runtime_tools_for_prompt(max_tools=28)
                    runtime_tool_names = self._render_allowed_tool_names()
                    
                    response = ""  # Initialize für except-Block
                    try:
                        # LLM-Prompt für Tool-Extraktion und Parameter-Generierung
                        tool_extraction_prompt = f"""Analysiere das folgende Reasoning und bestimme, welche Tools benötigt werden.

USER QUERY:
{user_prompt}

REASONING (vom Planner):
{final_reasoning_for_trace}

VERFÜGBARE TOOLS (RUNTIME):
{runtime_tools_block}

ERLAUBTE TOOL-NAMEN:
{runtime_tool_names}

AUFGABE:
Wenn das Reasoning Tools erwähnt oder Tools sinnvoll wären, gib eine JSON-Liste der benötigten Tools zurück.
Wenn KEINE Tools benötigt werden, gib eine leere Liste zurück: []

FORMAT (NUR JSON, KEIN ANDERER TEXT):
[
  {{
    "tool": "tool_name",
    "parameters": {{"param1": "value1", "param2": "value2"}},
    "reasoning": "Warum dieses Tool?"
  }}
]

BEISPIELE:
Query: "Zeige Timeline der KI-Entwicklung"
Reasoning: "Tool canvas erforderlich. Timeline wäre passend."
→ [{{"tool": "canvas", "parameters": {{"description": {{"type": "timeline", "title": "KI-Timeline", "events": [{{"year": 1950, "label": "Anfänge"}}, {{"year": 2024, "label": "Generative KI"}}]}}}}, "reasoning": "Timeline-Visualisierung gewünscht"}}]

Query: "Was ist 25 * 17?"
Reasoning: "Mathematische Berechnung. Tool calculator nutzen."
→ [{{"tool": "calculator", "parameters": {{"expression": "25 * 17"}}, "reasoning": "Berechnung erforderlich"}}]

Query: "Erkläre mir Python"
Reasoning: "Einfache Erklärungsfrage. Keine Tools nötig."
→ []

WICHTIG:
- Wenn Reasoning explizit Tools erwähnt, NUTZE SIE!
- Erstelle sinnvolle, vollständige Parameter
- Wenn Tool ein `description`-Objekt verlangt (z.B. canvas/create_diagram), erzeuge ein valides Objekt
- Verwende nur Tool-Namen aus der erlaubten Runtime-Liste
- Wenn unsicher: Lieber Tool verwenden als nicht"""

                        # ✅ PHASE 1: Structured Output mit Pydantic Validation + Retry
                        try:
                            # Erstelle Wrapper (lazy, wird gecacht)
                            if not hasattr(self, '_structured_wrapper'):
                                self._structured_wrapper = LLMStructuredWrapper(
                                    self.model_loader,
                                    max_retries=2,  # 2 Retries für Tool-Extraction
                                    retry_delay=0.5,
                                    temperature=0.1,
                                    enable_logging=False  # Weniger Logs
                                )
                            
                            # Structured Output mit Validation
                            prompt_tokens = estimate_prompt_tokens(tool_extraction_prompt)
                            model_n_ctx = getattr(self.model_loader, "get_max_context_tokens", lambda: LLM_CONTEXT_SIZE)() or LLM_CONTEXT_SIZE
                            max_tokens_dynamic = estimate_structured_output_tokens(
                                prompt_tokens=prompt_tokens,
                                model_context_window=model_n_ctx,
                                min_output_tokens=384,
                                max_output_tokens=3072,
                            )

                            tool_output = self._structured_wrapper.generate_structured_safe(
                                prompt=tool_extraction_prompt,
                                output_schema=ToolExtractionOutput,
                                max_tokens=max_tokens_dynamic,
                                fallback=ToolExtractionOutput(
                                    tools=[],
                                    needs_tools=False,
                                    reasoning="Fallback: Could not parse tool suggestions"
                                )
                            )
                            
                            if tool_output and tool_output.needs_tools and tool_output.tools:
                                print(f"✅ LLM schlug {len(tool_output.tools)} Tool(s) vor:")
                                
                                # Konvertiere zu ToolCall-Objekten
                                calls = []
                                for tool_schema in tool_output.tools:
                                    calls.append(ToolCall(
                                        tool=tool_schema.tool,
                                        parameters=tool_schema.parameters
                                    ))
                                    print(f"   → {tool_schema.tool}: {tool_schema.reasoning}")
                                
                                if calls:
                                    print(f"✅ LLM-BRIDGE: {len(calls)} Tool-Call(s) erstellt und werden ausgeführt")
                            else:
                                print(f"ℹ️ LLM entschied: Keine Tools benötigt")
                        
                        except Exception as inner_e:
                            print(f"⚠️ Structured Output Fehler: {inner_e}")
                            # Fallback: Keine Tools
                            calls = []
                    
                    except Exception as e:
                        print(f"⚠️ LLM-Bridge Fehler (äußerer Block): {e}")
                        import traceback
                        traceback.print_exc()
                        calls = []
                
                # 🔧 WICHTIG: Nach Auto-Korrektur könnten calls jetzt gefüllt sein!
                # Prüfe ZUERST ob calls existieren (auch wenn final_text vorhanden ist)
                if not calls and final_text:
                    if progress_callback:
                        progress_callback(
                            "Antwort finalisieren",
                            "Direkte Planner-Antwort wird für die Ausgabe vorbereitet",
                        )
                    # Direkte Antwort ohne Tools (echte FINAL-Antwort ohne Code)
                    self.message_history.append({"role": "user", "content": user_prompt})
                    self.message_history.append({"role": "assistant", "content": final_text})
                    
                    self.last_trace = AgentTrace(
                        planner_output=raw_planner or "FINAL (kein Tool erforderlich)",
                        planned_tools=[],
                        ran_tools=[],
                        evidence_domains=[],
                        extras_count=0,
                        summarizer_draft_chars=0,
                        verifier_changed=False,
                        planner_ms=elapsed_ms or 0,
                        reasoning=final_reasoning_for_trace,  # ✅ LLM Reasoning (model-agnostic)!
                        critique=critique,
                    )
                    # Follow-Up-Fragen generieren für direkte Antworten
                    try:
                        if hasattr(self, 'orchestrator') and self.orchestrator:
                            self.last_followup_questions = self.orchestrator._generate_followup_questions(user_prompt, final_text)
                        else:
                            self.last_followup_questions = []
                    except Exception as fq_e:
                        logger.warning(f"Follow-Up-Generierung fehlgeschlagen (direkt): {type(fq_e).__name__}: {fq_e}")
                        self.last_followup_questions = []
                    return final_text
                
                # Tools ausführen über Orchestrator
                if calls:
                    # Setze User-ID für psychologische Integration im Thread-Local-Context
                    set_current_user_id(self.get_current_user_id())

                    if progress_callback:
                        progress_callback(
                            "Werkzeuge und Quellen",
                            f"Führe {len(calls)} geplante Verarbeitungsschritte aus",
                        )
                    
                    final_answer = self.orchestrator.run_tools_and_summarize(
                        query=user_prompt,
                        planned_calls=calls,
                        history=self.message_history,
                        reasoning=final_reasoning_for_trace,  # ✅ LLM Reasoning (model-agnostic)!
                        critique=critique,
                        planner_ms=elapsed_ms,
                        planner_raw=raw_planner,
                    )

                    if progress_callback:
                        progress_callback(
                            "Qualitätssicherung",
                            "Synthese, Quellenprüfung und Sicherheitsprüfungen abgeschlossen",
                        )
                    
                    # Update state
                    self.last_sources = [
                        {"title": s.title, "url": s.url, "date": s.date, "snippet": s.snippet}
                        for s in (final_answer.sources or [])
                    ]
                    self.last_graphics = list(getattr(final_answer, "graphics", []) or [])
                    self.last_files = list(getattr(final_answer, "files", []) or [])
                    self.last_trace = final_answer.trace
                    self.last_followup_questions = getattr(final_answer, 'followup_questions', []) or []
                    self.message_history.append({"role": "user", "content": user_prompt})
                    self.message_history.append({"role": "assistant", "content": final_answer.text})
                    
                    return final_answer.text
                
                # ✅ INTELLIGENTE LOGIK: Respektiere LLM-Planner-Entscheidung!
                if not calls and self.agent_toolkit:
                    print("🔍 DEBUG: No tool calls from planner")
                    
                    # 🎯 FALL 1: Planner hat REASONING/Text ausgegeben → Das IST die Antwort!
                    if final_text:
                        print(f"✅ Planner hat entschieden: Keine Tools nötig - REASONING vorhanden")
                        print(f"   Respektiere LLM-Entscheidung und gebe final_text zurück")
                        
                        # History aktualisieren
                        self.message_history.append({"role": "user", "content": user_prompt})
                        self.message_history.append({"role": "assistant", "content": final_text})
                        
                        # Leere Sources/Trace (keine Tools verwendet)
                        self.last_sources = []
                        self.last_trace = None
                        # Follow-Up-Fragen generieren für direkte Planner-Antworten
                        try:
                            if hasattr(self, 'orchestrator') and self.orchestrator:
                                self.last_followup_questions = self.orchestrator._generate_followup_questions(user_prompt, final_text)
                            else:
                                self.last_followup_questions = []
                        except Exception as fq_e:
                            logger.warning(f"Follow-Up-Generierung fehlgeschlagen (planner): {type(fq_e).__name__}: {fq_e}")
                            self.last_followup_questions = []
                        
                        return final_text
                    
                    # 🎯 FALL 2: Planner hat GAR NICHTS unter FINAL: ausgegeben, ABER hat REASONING produziert.
                    # Dies passiert oft, wenn CoT-Modelle das Token-Limit erreichen oder strukturierte Ausgabe abbrechen.
                    # -> SOTA-Lösung: Sende das generierte Reasoning (als Kontext/Extra) an den Summarizer, 
                    # damit die harte Arbeit des Planners genutzt und eine endgültige Antwort formuliert wird!
                    if final_reasoning_for_trace:
                        # Designed path: Reasoning-Modelle (CoT) brechen die strukturierte
                        # Ausgabe (FINAL:/Tools) gelegentlich ab, wenn das Reasoning lang ist.
                        # Wir leiten die produzierte Reasoning-Spur als Kontext an den
                        # Summarizer weiter und erzeugen daraus die finale Antwort \u2014
                        # kein Fehlerfall, daher Debug-Log statt Warning.
                        logger.debug(
                            "Planner emitted reasoning without FINAL:/Tools \u2014 routing reasoning to summarizer."
                        )
                        
                        # Wir gaukeln dem Orchestrator vor, es gäbe keine Tools, aber wir übergeben das Reasoning.
                        # Dadurch baut der Orchestrator-Summarizer aus dem Reasoning + History die finale Antwort.
                        set_current_user_id(self.get_current_user_id())
                        if progress_callback:
                            progress_callback(
                                "Antwortsynthese",
                                "Formuliere die geprüfte Antwort aus dem Analyseergebnis",
                            )
                        final_answer = self.orchestrator.run_tools_and_summarize(
                            query=user_prompt,
                            planned_calls=[],
                            history=self.message_history,
                            reasoning=final_reasoning_for_trace,
                            critique=critique,
                            planner_ms=elapsed_ms,
                            planner_raw=raw_planner,
                        )

                        if progress_callback:
                            progress_callback(
                                "Qualitätssicherung",
                                "Synthese und Sicherheitsprüfungen abgeschlossen",
                            )
                        
                        # Update state
                        self.last_sources = [
                            {"title": s.title, "url": s.url, "date": s.date, "snippet": s.snippet}
                            for s in (final_answer.sources or [])
                        ]
                        self.last_trace = final_answer.trace
                        self.last_followup_questions = getattr(final_answer, 'followup_questions', []) or []
                        self.message_history.append({"role": "user", "content": user_prompt})
                        self.message_history.append({"role": "assistant", "content": final_answer.text})
                        
                        return final_answer.text
                    
                    # 🎯 FALL 3: Planner hat WIRKLICH GAR NICHTS ausgegeben (komplett leer, auch kein Reasoning)
                    # Nur DANN greifen wir auf Fallback zurück
                    print(f"⚠️ Planner gab weder Tools, noch REASONING, noch Text aus - nutze normalen Chat-Fallback")
                    normal_response = self._normal_chat_with_time_context(user_prompt, image_path)
                    
                    # Prüfe ob Response Code enthält (für Code-Execution)
                    extracted_code = self.orchestrator._extract_python_code_from_text(normal_response)
                    
                    if extracted_code:
                        print(f"🔧 DEBUG: Code in Chat-Response gefunden - führe aus")
                        if self.agent_toolkit:
                            result = self.agent_toolkit.execute_tool("code_executor", {"code": extracted_code})
                            if result.get("success") and result.get("plot_base64"):
                                self.message_history.append({"role": "user", "content": user_prompt})
                                self.message_history.append({"role": "assistant", "content": normal_response})
                                return f"{normal_response}\n\n[PLOT_BASE64:{result['plot_base64']}]"
                    
                    # Normaler Chat-Response
                    self.message_history.append({"role": "user", "content": user_prompt})
                    self.message_history.append({"role": "assistant", "content": normal_response})
                    # Follow-Up-Fragen generieren für Fallback-Antworten
                    try:
                        if hasattr(self, 'orchestrator') and self.orchestrator:
                            self.last_followup_questions = self.orchestrator._generate_followup_questions(user_prompt, normal_response)
                        else:
                            self.last_followup_questions = []
                    except Exception as fq_e:
                        logger.warning(f"Follow-Up-Generierung fehlgeschlagen (fallback): {type(fq_e).__name__}: {fq_e}")
                        self.last_followup_questions = []
                    return normal_response
                
                # Wenn wir hier ankommen: Planner hat Tools geplant → normale Verarbeitung
                # (wird weiter unten im Code behandelt)
                
            finally:
                # System-Prompt wiederherstellen
                self.system_prompt = original_system_prompt
            
            # Fallback: Standard-Chat
            fallback_response = self._normal_chat_with_time_context(user_prompt, image_path)
            try:
                if hasattr(self, 'orchestrator') and self.orchestrator:
                    self.last_followup_questions = self.orchestrator._generate_followup_questions(user_prompt, fallback_response)
                else:
                    self.last_followup_questions = []
            except Exception as fq_e:
                logger.warning(f"Follow-Up-Generierung fehlgeschlagen (super-fallback): {type(fq_e).__name__}: {fq_e}")
                self.last_followup_questions = []
            return fallback_response
            
        except Exception as e:
            print(f"❌ Standard Agent-Chat Fehler: {e}")
            return f"Entschuldigung, es gab einen Fehler: {str(e)}"

    def _agent_chat(self, user_prompt: str, image_path: Optional[str] = None, progress_callback=None, search_depth: Optional[int] = None) -> str:
        """Agent-basierte Chat-Verarbeitung (via Orchestrator + LLM-Summarizer)

        PRIVACY GUARD (2026-08-28): Der normale Chat-Pfad erhält NIEMALS
        psychologische Profil-/Familien-/Session-Daten (Psych-KG). Die frühere
        Intent-Option `ENABLE_AGENT_PSYCH_INTEGRATION` ist im normalen Chat
        deaktiviert: Der Thread-Local-Schalter wird hier explizit auf False
        gesetzt, damit selbst ein andwo aktiver Orchestrator-Patch
        (`integrate_wellbeing_orchestrator`) keine Psych-Daten in RAG-Queries
        injizieren kann. Das Psych-Tab nutzt seinen eigenen Pfad
        (`wellbeing_chat`) und bleibt davon unberührt.
        """
        set_wellbeing_context_enabled(False)

        try:
            # Spezielle Behandlung für optimierte Research
            if self.search_method == "optimized_research" and self.optimized_research_engine:
                return self._optimized_research_chat(
                    user_prompt,
                    image_path,
                    search_depth,
                    progress_callback,
                )

            # Standard Agent-Chat-Flow (enthält jetzt intelligentes Routing Fallback)
            return self._standard_agent_chat(user_prompt, image_path, progress_callback, search_depth)
        finally:
            # Never leak request-level psych context to subsequent chats.
            set_wellbeing_context_enabled(False)
    def _build_current_time_context_block(self) -> str:
        """Erzeugt einen robusten, lokalzeit-basierten Zeitkontextblock für Prompts."""
        current_local = datetime.now().astimezone()
        date_str = current_local.strftime("%Y-%m-%d")
        time_str = current_local.strftime("%H:%M:%S")
        weekday_str = current_local.strftime("%A")
        tz_name = current_local.tzname() or "local"
        return (
            "AKTUELLER ZEITKONTEXT:\n"
            f"- Datum (lokal): {date_str}\n"
            f"- Wochentag: {weekday_str}\n"
            f"- Uhrzeit (lokal): {time_str}\n"
            f"- Zeitzone: {tz_name}\n"
            "Verwende diese Angaben bei Datums-/Zeitfragen als Prioritaetskontext."
        )

    def _normal_chat_with_time_context(self, user_prompt: str, image_path: Optional[str] = None, stream_callback=None, stream_context: Optional[StreamingContext] = None) -> str:
        """Normaler Chat mit automatischem Zeitkontext für bessere Antworten."""
        # Temporär den System-Prompt mit Zeitkontext erweitern
        original_system_prompt = self.system_prompt
        
        try:
            # ROBUSTE ZEITKONTEXT-ERSTELLUNG: Immer aktuelle lokale Datum/Zeit verwenden
            time_context = self._build_current_time_context_block()
            
            # System-Prompt KRAFTVOLL erweitern
            enhanced_system_prompt = f"{original_system_prompt}\n\n{time_context}"
            self.set_system_prompt(enhanced_system_prompt)
            print("🕐 Zeitkontext in Standard-Chat integriert")
            
            # Auch den user_prompt verstärken falls zeitbezogen
            enhanced_user_prompt = user_prompt
            prompt_lower = user_prompt.lower()
            if any(word in prompt_lower for word in ['tag', 'datum', 'heute', 'zeit', 'wann']):
                current_local = datetime.now().astimezone()
                date_str = current_local.strftime("%Y-%m-%d")
                time_str = current_local.strftime("%H:%M:%S")
                enhanced_user_prompt = f"{user_prompt}\n\n[KONTEXT: Datum {date_str}, Uhrzeit {time_str}]"
                print(f"🎯 Zeit-Anfrage erkannt - User-Prompt verstärkt")
            
            # Normalen Chat ausführen (mit verstärktem System-Prompt und optional verstärktem User-Prompt)
            result = super().chat(
                enhanced_user_prompt,
                image_path,
                stream_callback=stream_callback,
                stream_context=stream_context,
            )
            
            return result
            
        finally:
            # System-Prompt zurücksetzen
            self.set_system_prompt(original_system_prompt)

    def _normalize_params(self, tool_call: Dict[str, Any]) -> Dict[str, Any]:
        """Konvertiert extrahierte Parameter-Strings in Parameter-Dicts je nach Tool."""
        tool_name = (tool_call.get("tool") or "").lower()
        parameters = tool_call.get("parameters")
        # Wenn bereits Dict
        if isinstance(parameters, dict):
            return parameters
        # sonst String → je Tool interpretieren
        s = str(parameters or "")
        if tool_name == "calculator":
            return {"expression": s}
        if tool_name == "web_search":
            return {"query": s}
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
        return {"query": s}

    def _get_runtime_tool_catalog(self) -> Dict[str, Dict[str, Any]]:
        """Build a runtime tool catalog from canonical toolkit/orchestrator sources."""
        catalog: Dict[str, Dict[str, Any]] = {}

        toolkit_tools = getattr(self.agent_toolkit, "tools", {}) if self.agent_toolkit else {}
        if isinstance(toolkit_tools, dict):
            for name, info in toolkit_tools.items():
                if isinstance(info, dict):
                    catalog[name] = info

        # rag_search ist im Toolkit absichtlich entfernt, aber im Orchestrator/ToolManager verfügbar.
        if self.orchestrator and getattr(self.orchestrator, "tools", None):
            has_rag = hasattr(self.orchestrator.tools, "rag_search")
            if has_rag and "rag_search" not in catalog:
                catalog["rag_search"] = {
                    "description": "Durchsucht die lokale RAG-Wissensbasis (FAISS/SQLite).",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string"},
                            "k": {"type": "integer", "default": 6},
                        },
                        "required": ["query"],
                    },
                }

        return catalog

    def _render_runtime_tools_for_prompt(self, *, max_tools: Optional[int] = None) -> str:
        """Render runtime tools as a concise prompt block to avoid schema drift."""
        catalog = self._get_runtime_tool_catalog()
        if not catalog:
            return "- (keine Tool-Metadaten verfügbar)"

        lines: List[str] = []
        items = sorted(catalog.items(), key=lambda kv: kv[0])
        if max_tools is not None and max_tools > 0:
            items = items[:max_tools]

        for tool_name, tool_info in items:
            description = str(tool_info.get("description", "")).strip().replace("\n", " ")
            description = (description[:180] + "...") if len(description) > 180 else description
            params = ((tool_info.get("parameters") or {}).get("properties") or {})
            required = set((tool_info.get("parameters") or {}).get("required") or [])

            if params:
                param_labels: List[str] = []
                for param_name in params.keys():
                    marker = "*" if param_name in required else ""
                    param_labels.append(f"{param_name}{marker}")
                param_text = ", ".join(param_labels)
                lines.append(f"- {tool_name}: {description} | params: {param_text}")
            else:
                lines.append(f"- {tool_name}: {description}")

        return "\n".join(lines)

    def _render_allowed_tool_names(self) -> str:
        """Return comma-separated runtime tool names as explicit allowlist hint."""
        catalog = self._get_runtime_tool_catalog()
        if not catalog:
            return ""
        return ", ".join(sorted(catalog.keys()))

    def _create_agent_system_prompt(self) -> str:
        """Erstellt Agent-System-Prompt mit aktuellem Zeitkontext"""
        if not self.agent_toolkit:
            # Fallback falls AgentToolkit nicht verfügbar
            base_prompt = self.system_prompt
            if hasattr(self, '_current_time_context'):
                return f"{base_prompt}\n\n{self._current_time_context}"
            return base_prompt
            
        # Zeitkontext einbauen, falls verfügbar
        time_context_section = ""
        if hasattr(self, '_current_time_context'):
            time_context_section = f"\n{self._current_time_context}\n"

        runtime_tools_block = self._render_runtime_tools_for_prompt(max_tools=24)
        runtime_tool_names = self._render_allowed_tool_names()
            
        return f"""{self.system_prompt}{time_context_section}

ERWEITERTE AGENT-FÄHIGKEITEN:
Du hast Zugriff auf verschiedene Tools um komplexe Aufgaben zu lösen.

    VERFÜGBARE TOOLS (RUNTIME):
    {runtime_tools_block}

    Nutze ausschließlich Tool-Namen aus dieser Runtime-Liste.
    Erlaubte Tool-Namen: {runtime_tool_names}

TOOL-VERWENDUNG:
Um ein Tool zu verwenden, antworte in diesem Format:
[TOOL:tool_name:parameter]

Beispiele:
- [TOOL:calculator:sqrt(25) + 3]
- [TOOL:web_search:aktuelle KI News 2024]
- [TOOL:web_search:Gehalt Schweiz BWL Analytics 2025]
- [TOOL:file_writer:test.txt:Hello World]

AGENT-ANTWORT-FORMAT:
Für komplexe Aufgaben mit Tool-Nutzung: 
Beginne mit [THINK]deiner Problemanalyse, Lösungsplanung und Tool-Auswahl[/THINK], 
dann führe die benötigten Tool-Aufrufe durch und gib abschließend eine vollständige Antwort.
Verwende Markdown-Formatierung und LaTeX für mathematische Gleichungen.
Schreibe sowohl Denkprozess als auch Antwort in derselben Sprache wie die Eingabe.

WICHTIGE REGELN FÜR TOOL-VERWENDUNG:
1. Für aktuelle/zeitkritische Informationen (News, Wetter, Events) IMMER web_search verwenden
2. Für Marktdaten, Gehälter, Preise, Löhne IMMER web_search verwenden - lokale Daten sind oft veraltet
3. Bei Standort-spezifischen Informationen (Schweiz, Deutschland, etc.) web_search bevorzugen
4. Nutze den Zeitkontext um "heute", "gestern", "diese Woche" etc. korrekt einzuordnen
5. Für mathematische Berechnungen calculator verwenden
6. Für Datei-Operationen file_reader/file_writer/list_directory/search_files verwenden
7. Analysiere die Anfrage und verwende Tools proaktiv!
8. Gib eine finale, menschenfreundliche Antwort nach der Tool-Nutzung

ZEITKRITISCHE ANFRAGEN:
- Bei Fragen zu "heute", "gestern", "aktuell" etc. berücksichtige das obige Datum
- Für News und aktuelle Ereignisse sind Web-Quellen meist relevanter als lokale Dokumente
- Bei "neueste/aktuelle" Informationen bevorzuge frische Web-Inhalte

MARKT- UND GEHALTSINFORMATIONEN:
- Gehaltsfragen, Lohnvergleiche, Marktwerte: Verwende IMMER web_search
- Lokale RAG-Daten enthalten meist keine aktuellen Gehaltsinformationen
- Bei "Was sollte ich verdienen" oder "Wie viel kostet" → web_search

BEISPIEL-VERHALTEN:
User: "Was sind die neuesten News zu KI?"
Response: [TOOL:web_search:aktuelle KI Nachrichten 2024]

User: "Berechne 25 * 17"
Response: [TOOL:calculator:25 * 17]

User: "Wieviel sollte ich in der Schweiz verdienen?"
Response: [TOOL:web_search:Gehalt Schweiz BWL Analytics Fachführung 2025]

Analysiere die Anfrage und verwende Tools automatisch wenn nötig!
"""

    def _extract_tool_calls(self, response: str) -> List[Dict]:
        """Extrahiert Tool-Aufrufe aus LLM-Response (case-insensitive)"""
        import re
        
        tool_calls: List[Dict[str, str]] = []
        if not response:
            return tool_calls
        
        # Suche nach [TOOL:name:params] Pattern, case-insensitive
        pattern = re.compile(r"\[(?:TOOL|tool):([^:]+):([^\]]+)\]", re.IGNORECASE)
        matches = pattern.findall(response)
        
        for tool_name, params in matches:
            tool_calls.append({
                "tool": tool_name.strip().lower(),
                "parameters": params.strip()
            })
        
        if tool_calls:
            print(f"[DEBUG] Erkannte Tool-Aufrufe: {tool_calls}")
        
        return tool_calls
    
    def _execute_tools(self, tool_calls: List[Dict]) -> str:
        """(Alt) Führe Tool-Aufrufe aus und sammle Ergebnisse – belassen für Abwärtskompatibilität"""
        if not self.agent_toolkit:
            return "Agent-Tools nicht verfügbar"
            
        results: List[str] = []
        for tool_call in tool_calls:
            tool_name = tool_call.get("tool", "")
            parameters = tool_call.get("parameters", "")
            print(f"[DEBUG] Führe Tool aus: {tool_name} mit Parameters: {parameters}")
            try:
                # Konvertiere Parameter basierend auf Tool
                if tool_name == "calculator":
                    params_dict = {"expression": parameters}
                elif tool_name == "web_search":
                    params_dict = {"query": parameters}
                elif tool_name == "file_reader":
                    params_dict = {"file_path": parameters}
                elif tool_name == "file_writer":
                    parts = parameters.split(":", 1)
                    if len(parts) == 2:
                        params_dict = {"file_path": parts[0], "content": parts[1]}
                    else:
                        params_dict = {"file_path": parameters, "content": ""}
                elif tool_name == "code_executor":
                    params_dict = {"code": parameters}
                elif tool_name == "image_info":
                    params_dict = {"image_path": parameters}
                else:
                    params_dict = {"query": parameters }
                
                result = self.agent_toolkit.execute_tool(tool_name, params_dict)
                
                # Einheitliche, nutzbare Darstellung pro Tool
                if not isinstance(result, dict):
                    results.append(f"{tool_name}: Unbekanntes Ergebnisformat")
                    continue
                
                if not result.get("success", True) and "error" in result:
                    results.append(f"{tool_name}: Error - {result.get('error')}")
                    continue
                
                if tool_name == "web_search":
                    items = result.get("results", []) or []
                    self.last_sources = items
                    if not items:
                        results.append(f"web_search: keine Ergebnisse")
                    else:
                        formatted = [
                            "web_search:",
                        ]
                        for idx, it in enumerate(items, 1):
                            title = it.get("title", "(ohne Titel)")
                            url = it.get("url", "")
                            snip = it.get("snippet", "")
                            date = it.get("date") or ""
                            date_str = f" [{date}]" if date else ""
                            formatted.append(f"  {idx}. {title}{date_str}\n     {url}\n     {snip}")
                        results.append("\n".join(formatted))
                elif tool_name == "calculator":
                    expr = result.get("expression") or parameters
                    calc_res = result.get("result")
                    results.append(f"calculator: {expr} = {calc_res}")
                elif tool_name == "file_reader":
                    path = result.get("file_path", parameters)
                    content = result.get("content", "")
                    preview = content[:500]
                    suffix = "" if len(content) <= 500 else f"... (+{len(content)-500} weitere Zeichen)"
                    results.append(f"file_reader: {path}\n--- Inhalt (Auszug) ---\n{preview}{suffix}")
                elif tool_name == "file_writer":
                    path = result.get("file_path", "")
                    msg = result.get("message", "Datei geschrieben")
                    results.append(f"file_writer: {msg} ({path})")
                elif tool_name == "code_executor":
                    stdout = (result.get("stdout") or "").strip()
                    stderr = (result.get("stderr") or "").strip()
                    formatted = ["code_executor:"]
                    if stdout:
                        formatted.append("--- stdout ---\n" + stdout[:1000])
                        if len(stdout) > 1000:
                            formatted.append("... (gekürzt)")
                    if stderr:
                        formatted.append("--- stderr ---\n" + stderr[:800])
                        if len(stderr) > 800:
                            formatted.append("... (gekürzt)")
                    if len(formatted) == 1:
                        formatted.append("(keine Ausgabe)")
                    results.append("\n".join(formatted))
                elif tool_name == "image_info":
                    msg = result.get("message") or str({k: v for k, v in result.items() if k != 'success'})
                    results.append(f"image_info: {msg}")
                else:
                    msg = result.get("message") or json.dumps(result, ensure_ascii=False)
                    results.append(f"{tool_name}: {msg}")
            except Exception as e:
                results.append(f"{tool_name}: Exception - {str(e)}")
        return "\n".join(results)
    
    def get_agent_status(self) -> Dict:
        """Gibt Status des Agent-Systems zurück"""
        if not self.agent_toolkit:
            return {"agent_mode_enabled": self.agent_mode_enabled, "available_tools": [], "execution_log": []}
            
        return {
            "agent_mode_enabled": self.agent_mode_enabled,
            "available_tools": list(self.agent_toolkit.tools.keys()) if hasattr(self.agent_toolkit, 'tools') else [],
            "execution_log": self.agent_toolkit.execution_log[-10:] if hasattr(self.agent_toolkit, 'execution_log') else []
        }

    def _tool_calls_preview(self, calls: List[ToolCall]) -> str:
        """Kompakte Vorschau der geplanten Tool-Aufrufe für die Trace-Anzeige."""
        parts: List[str] = []
        for c in calls:
            try:
                # Handle both ToolCall objects and dicts
                if isinstance(c, dict):
                    tool_name = c.get("tool", c.get("name", "unknown"))
                    p = c.get("parameters", {})
                else:
                    tool_name = c.tool if hasattr(c, "tool") else "unknown"
                    p = c.parameters if hasattr(c, "parameters") else {}
                
                if isinstance(p, dict):
                    if "expression" in p:
                        s = str(p.get("expression", ""))
                    elif "query" in p:
                        s = str(p.get("query", ""))
                    elif "file_path" in p and "content" in p:
                        content = str(p.get("content", ""))
                        content_preview = (content[:30] + "...") if len(content) > 30 else content
                        s = f"{p.get('file_path','')}:" + content_preview
                    elif "file_path" in p:
                        s = str(p.get("file_path", ""))
                    elif "code" in p:
                        code = str(p.get("code", ""))
                        s = code[:60] + ("..." if len(code) > 60 else "")
                    elif "image_path" in p:
                        s = str(p.get("image_path", ""))
                    else:
                        import json as _json
                        s = _json.dumps(p, ensure_ascii=False)
                else:
                    s = str(p)
                if len(s) > 80:
                    s = s[:77] + "..."
                parts.append(f"[TOOL:{tool_name}:{s}]")
            except (KeyError, ValueError) as e:
                tool_name = "unknown"
                if isinstance(c, dict):
                    tool_name = c.get("tool", c.get("name", "unknown"))
                elif hasattr(c, "tool"):
                    tool_name = c.tool
                logging.debug(f"Tool-Parameter-Parsing-Fehler für {tool_name}: {e}")
                parts.append(f"[TOOL:{tool_name}:...]")
            except Exception as e:
                tool_name = "unknown"
                if isinstance(c, dict):
                    tool_name = c.get("tool", c.get("name", "unknown"))
                elif hasattr(c, "tool"):
                    tool_name = c.tool
                logging.warning(f"Unerwarteter Tool-Preview-Fehler für {tool_name}: {type(e).__name__}: {e}")
                parts.append(f"[TOOL:{tool_name}:...]")
        return "\n".join(parts)

    def _analyze_information_needs(self, query: str, context: Optional[str] = None) -> Dict[str, Any]:
        """
        LLM-BASIERTE ROUTING-ENTSCHEIDUNG (Robuste Version ohne JSON-Parsing)
        Das LLM entscheidet intelligent, ob diese Benutzeranfrage eine Internetrecherche benötigt.
        """
        try:
            routing_prompt = f"""BENUTZERANFRAGE: "{query}"

ENTSCHEIDUNGSLOGIK:
- AGENT: Wenn aktuelle Informationen, Preise, Anbieter, Verfügbarkeit, News, spezifische Produkte/Services oder Recherche benötigt wird
- SIMPLE: Wenn allgemeine Wissensfragen, Definitionen, Erklärungen oder persönliche Hilfe ausreichen

Antworte NUR mit: AGENT oder SIMPLE

Entscheidung:"""

            # Schnelle LLM-Entscheidung (wenige Tokens)
            messages = [
                {"role": "system", "content": "Du bist ein Routing-System. Antworte nur mit AGENT oder SIMPLE."},
                {"role": "user", "content": routing_prompt}
            ]
            
            response = self.model_loader.generate_response(
                max_tokens=10,  # Nur wenige Tokens für AGENT/SIMPLE
                temperature=0.0,  # Deterministisch
                messages=messages
            )
            
            # Extrahiere Entscheidung
            decision = response.strip().upper()
            needs_web_search = "AGENT" in decision
            
            print(f"🧠 [LLM-ROUTING] Entscheidung: {'AGENT' if needs_web_search else 'SIMPLE'} ('{response.strip()}')")
            
            # Rückgabe im erwarteten Format (kompatibel mit alter Logik)
            return {
                "search_strategy": "web_only" if needs_web_search else "rag_only",
                "needs_web_search": needs_web_search,
                "confidence": 0.9,
                "reasoning": f"LLM-basierte Entscheidung: {response.strip()}"
            }
                
        except Exception as e:
            print(f"❌ [LLM-ROUTING] Fehler: {e} - konservativer AGENT-Default")
            return {
                "search_strategy": "web_only",
                "needs_web_search": True,
                "confidence": 0.2,
                "reasoning": "LLM-Routing fehlgeschlagen; konservativer AGENT-Default ohne Heuristik",
            }

    def get_url_processing_performance(self) -> Dict[str, Any]:
        """Get URL processing performance stats from orchestrator for GUI display."""
        if self.orchestrator and hasattr(self.orchestrator, 'get_url_processing_stats'):
            return self.orchestrator.get_url_processing_stats()
        return {
            'total_requests': 0,
            'deduplication_saves': 0,
            'concurrent_blocks': 0,
            'processed_urls_count': 0,
            'deduplication_rate': 0.0,
            'avg_processing_time': 0.0,
            'max_processing_time': 0.0,
            'min_processing_time': 0.0
        }
    
    def reset_url_processing_cache(self) -> bool:
        """Reset URL processing cache and stats."""
        try:
            if self.orchestrator and hasattr(self.orchestrator, 'reset_url_processing_stats'):
                self.orchestrator.reset_url_processing_stats()
                return True
        except Exception as e:
            print(f"❌ Error resetting URL processing cache: {e}")
        return False

    def is_wellbeing_support_enabled(self) -> bool:
        """
        Prüft ob psychologische Unterstützung aktiviert ist.
        Wrapper für die GUI-Kompatibilität.
        """
        if not self.wellbeing_support_interface:
            return False
        return self.wellbeing_support_interface.is_enabled()

    def enable_wellbeing_support(self, chat_function=None) -> bool:
        """
        Aktiviert psychologische Unterstützung.
        Wrapper für die GUI-Kompatibilität.
        """
        if not self.wellbeing_support_interface:
            return False
        
        # Verwende die eigene chat-Methode als Standard
        if chat_function is None:
            chat_function = self.chat
            
        return self.wellbeing_support_interface.enable(chat_function)
    
    def disable_wellbeing_support(self) -> bool:
        """
        Deaktiviert psychologische Unterstützung.
        Wrapper für die GUI-Kompatibilität.
        """
        if not self.wellbeing_support_interface:
            return False
            
        return self.wellbeing_support_interface.disable()


    def get_wellbeing_status(self) -> Dict[str, Any]:
        """Gibt detaillierte Informationen über den psychologischen Status zurück"""
        if not self.wellbeing_support_interface:
            return {
                "available": False,
                "enabled": False,
                "last_checked": False,
                "status": "Interface nicht verfügbar"
            }
        
        return {
            "available": True,
            "enabled": self.wellbeing_support_interface.is_enabled(),
            "last_checked": self.was_last_message_wellbeing_checked(),
            "status": "Aktiv" if self.wellbeing_support_interface.is_enabled() else "Inaktiv"
        }

    def _sanitize_query_for_web_search(self, query: str, user_prompt: str) -> str:
        """
        Entfernt persönliche Informationen aus Web-Such-Queries für Datenschutz
        
        Args:
            query: Die Such-Query die sanitized werden soll
            user_prompt: Der ursprüngliche User-Prompt (um Kontext zu haben)
            
        Returns:
            Sanitized query ohne persönliche Informationen
        """
        # Prüfe ob es eine psychologische Anfrage ist
        user_lower = user_prompt.lower()
        is_wellbeing_query = any(word in user_lower for word in [
            'psychologisch', 'depression', 'angst', 'stress', 'therapie', 'beratung',
            'gefühle', 'trauer', 'einsamkeit', 'sorgen', 'probleme', 'hilfe',
            'unterstützung', 'seelisch', 'mental', 'burnout', 'krise'
        ]) or "WELLBEING-SUPPORT-MODUS" in user_prompt
        
        # Prüfe auf explizite Erlaubnis für persönliche Daten
        explicit_permission = any(phrase in user_lower for phrase in [
            'suche nach mir', 'über mich', 'meine informationen', 'persönliche daten',
            'suche meinen namen', 'finde mich', 'über meinen fall'
        ])
        
        if is_wellbeing_query and not explicit_permission:
            # Entferne potentielle persönliche Informationen
            sanitized = query
            
            # Entferne Namen (alles was wie ein Name aussieht - Großbuchstaben)
            import re
            # Entferne Wörter die nur Großbuchstaben am Anfang haben (potentielle Namen)
            sanitized = re.sub(r'\b[A-Z][a-z]+ [A-Z][a-z]+\b', '[NAME]', sanitized)
            sanitized = re.sub(r'\b[A-Z][a-z]+\b(?=\s|$)', '[NAME]', sanitized)
            
            # Entferne spezifische persönliche Marker
            personal_markers = [
                'ich', 'mein', 'meine', 'mir', 'mich', 
                'name ist', 'heiße', 'bin', 'wohne', 'arbeite'
            ]
            
            for marker in personal_markers:
                sanitized = sanitized.replace(marker, '[PERSON]')
            
            # Entferne Zahlen die Alter, Telefon, etc. sein könnten
            sanitized = re.sub(r'\b\d{1,3}\b(?=\s*(jahr|alt|jährig))', '[AGE]', sanitized, flags=re.IGNORECASE)
            sanitized = re.sub(r'\b\d{4,}\b', '[NUMBER]', sanitized)
            
            # Mache die Query allgemeiner und fokussiert auf das Thema
            if sanitized != query:
                print(f"🔒 Privacy-Filter: Query sanitized für Web-Suche")
                print(f"   Original: {query}")
                print(f"   Sanitized: {sanitized}")
                return sanitized
        
        return query

    def _is_personal_data_allowed(self, user_prompt: str) -> bool:
        """
        Prüft ob der User explizit erlaubt hat, persönliche Daten zu verwenden
        """
        user_lower = user_prompt.lower()
        return any(phrase in user_lower for phrase in [
            'suche nach mir', 'über mich', 'meine informationen', 'persönliche daten',
            'suche meinen namen', 'finde mich', 'über meinen fall', 'mit meinem namen'
        ])
    
    def cleanup(self):
        """
        Explizites Cleanup aller Ressourcen zur Vermeidung von Memory Leaks.
        Wird von Enhanced Streamlit Bot beim Unload aufgerufen.
        """
        try:
            # Cleanup Orchestrator
            if hasattr(self, 'orchestrator') and self.orchestrator:
                self.orchestrator = None
            
            # Cleanup AgentToolkit
            if hasattr(self, 'agent_toolkit') and self.agent_toolkit:
                # Rufe explizites cleanup() des AgentToolkits auf
                try:
                    if hasattr(self.agent_toolkit, 'cleanup'):
                        self.agent_toolkit.cleanup()
                except Exception as e:
                    print(f"⚠️ Fehler beim AgentToolkit cleanup: {e}")
                
                self.agent_toolkit = None
            
            # Cleanup Research Engines
            if hasattr(self, 'optimized_research_engine'):
                self.optimized_research_engine = None
            
            if hasattr(self, 'hybrid_search_engine'):
                self.hybrid_search_engine = None
            
            # Cleanup Psychological Integration
            if hasattr(self, 'wellbeing_integration'):
                self.wellbeing_integration = None
            
            # Cleanup Intent Classifier
            if hasattr(self, 'intent_classifier'):
                self.intent_classifier = None
            
            # Clear Caches
            if hasattr(self, 'response_cache'):
                self.response_cache.clear()
            
            if hasattr(self, '_cache'):
                self._cache.clear()
            
            # Force Garbage Collection
            import gc
            gc.collect()
            
            print("✅ AgentChatbotLogic cleanup abgeschlossen")
            
        except Exception as e:
            print(f"⚠️ Fehler beim AgentChatbotLogic cleanup: {e}")
    
    def wellbeing_chat(
        self, 
        user_prompt: str, 
        session_context: Optional[Dict[str, Any]] = None,
        progress_callback=None, 
        faiss_min_confidence: Optional[float] = None,
        session_history: Optional[List[Dict[str, Any]]] = None,
        care_system_prompt: Optional[str] = None,
        pre_formatted_context: Optional[str] = None,
    ) -> str:
        """
        Optimierte Chat-Methode für Care-Gespräche
        
        DESIGN:
        - Nutzt RAG für Psychologie-Fachwissen
        - Nutzt session_context für Care-Kontinuität
        - KEIN Agent-Modus (zu langsam für Therapie)
        - KEINE Web-Suche (nicht nötig, Fachwissen in RAG)
        
        PERFORMANCE:
        - Schnelle Antworten: 5-10 Sekunden
        - Optimiert für empathische, kontextbewusste Antworten
        
        Args:
            user_prompt: Die Benutzereingabe (kann bereits Session-History enthalten)
            session_context: Optional Session-Info für Kontext-Enrichment
                            {user_id, session_id, mood, goals, ...}
            progress_callback: Optional function(step: str, details: str = "") für Live-Updates
            faiss_min_confidence: Optional FAISS Confidence Threshold (default: 0.7)
            session_history: Optional DB-basierte Session-Historie
            care_system_prompt: Optional vom ResponseGenerator vorbereiteter
                Care-System-Prompt. Wird bevorzugt vor DEFAULT_SYSTEM_PROMPT.
            pre_formatted_context: Optional vorformatierter Kontext-Block. Wenn gesetzt, wird
                die lokale Kontext-Extraktion übersprungen (eliminiert doppelte Arbeit).
        
        Returns:
            LLM-Antwort als String
        """
        
        # Progress-Callback-System
        def notify_progress(step: str, details: str = ""):
            """Benachrichtige über Fortschritt"""
            if progress_callback:
                try:
                    progress_callback(step, details)
                except Exception as exc:
                    logger.debug(f"progress_callback raised: {exc}")
            print(f"🧠 [Wellbeing] [{step}] {details}")
        
        notify_progress("Wellbeing-Chat", "Reflexions-Modus: RAG + LLM (OHNE Agent)")
        
        # Session-Kontext für Logging (optional)
        if session_context:
            user_id = session_context.get('user_id', 'unknown')
            session_id = session_context.get('session_id', 'unknown')
            notify_progress("Session-Kontext", f"User: {user_id[:8]}..., Session: {session_id[:8]}...")
        
        try:
            # FAISS-Confidence konfigurieren (falls übergeben)
            if faiss_min_confidence is not None and self.orchestrator:
                self.orchestrator.set_rag_config(faiss_min_confidence=faiss_min_confidence)
                self.logger.info(f"🎯 Psycho-Chat: FAISS Confidence = {faiss_min_confidence}")
            
            # === CONTEXT WINDOW MANAGEMENT ===
            # ⚠️ CRITICAL FIX: Psycho-Chat darf NICHT self.message_history verwenden!
            # Das ist die NORMALE Chat-Historie, die würde vermischt werden!
            # Psycho-Sessions haben ihre eigene DB-basierte Historie!
            
            # ✅ FIX: Baue llm_messages NUR aus session_context, NICHT aus self.message_history
            llm_messages = []
            
            # RC-1 FIX: Verwende den Care-System-Prompt vom ResponseGenerator
            # anstatt DEFAULT_SYSTEM_PROMPT (der ist ein General-AI-Prompt mit [THINK]/[FOLLOW_UP])
            # Use CARE_SYSTEM_PROMPT_BASE as fallback — not DEFAULT_SYSTEM_PROMPT,
            # which contains [THINK]/[FOLLOW_UP]/image-analysis instructions that are
            # entirely inappropriate for a care conversation.
            effective_system_prompt = care_system_prompt or CARE_SYSTEM_PROMPT_BASE
            if care_system_prompt:
                print(f"✅ [RC-1 FIX] Care-System-Prompt aktiv ({len(care_system_prompt)} Zeichen)")
            else:
                print(f"⚠️ [RC-1] Kein Care-Prompt übergeben — Fallback auf DEFAULT_SYSTEM_PROMPT")
            
            # RC-4 FIX: Token Budget Manager — misst und begrenzt alle Prompt-Komponenten
            from wellbeing_session.context.token_budget_manager import TokenBudgetManager
            token_budget = TokenBudgetManager(model_loader=self.model_loader)
            
            if effective_system_prompt:
                token_budget.set_system_prompt(
                    effective_system_prompt,
                    immutable_text=CARE_SYSTEM_PROMPT_BASE,
                )
                llm_messages.append({'role': 'system', 'content': effective_system_prompt})
            
            token_budget.set_user_query(user_prompt)
            
            # Session-Historie einbinden (mit Token-Budget-Trimming)
            if session_history and isinstance(session_history, list):
                # Filtere aktuelle Nachricht raus (Duplikation vermeiden)
                history_msgs = [
                    msg for msg in session_history
                    if msg.get('role') in ['user', 'assistant'] and msg.get('content') != user_prompt
                ]
                # RC-4: Trimme History auf Budget
                trimmed_history = token_budget.trim_session_history(history_msgs)
                for msg in trimmed_history:
                    llm_messages.append({'role': msg.get('role'), 'content': msg.get('content')})
                        
            # Aktuelle User-Message hinzufügen
            llm_messages.append({'role': 'user', 'content': user_prompt})
            
            print(f"🔍 [PSYCHO-CHAT] llm_messages: {len(llm_messages)} Messages "
                  f"(system + {len(llm_messages) - 2} history + user)")
            
            # RC-4: Token-Budgetierung ersetzt den alten context_manager.should_summarize()
            # Der alte Check war VOR dem Enrichment → konnte Overflow nicht verhindern.
            # TokenBudgetManager misst NACH Assembly und trimmt gezielt.
            
            # === SESSION-KONTEXT ENRICHMENT ===
            # Baue VOLLSTÄNDIGEN Care-Kontext für das LLM
            enriched_prompt = user_prompt
            
            # Extrahiere minimale Session-Info (immer benötigt für Logging/Routing/Post-Processing)
            user_id = session_context.get('user_id', 'unknown') if session_context else 'unknown'
            user_name = session_context.get('user_name', '') if session_context else ''
            session_id = session_context.get('session_id', 'unknown') if session_context else 'unknown'
            
            # RC-3 FIX: LLM-basierte Intent Classification (statt reiner Regex)
            # Nutzt das lokale LLM für semantisches Verständnis der Query.
            # Fallback auf Regex wenn LLM nicht verfügbar.
            query_classification: QueryClassification = classify_query_llm(
                user_prompt, model_loader=self.model_loader
            )
            query_intent = query_classification.intent
            print(f"🎯 [INTENT-ROUTER] Intent={query_intent.value.upper()}, "
                  f"Confidence={query_classification.confidence:.2f}, "
                  f"Family={query_classification.family_entities}, "
                  f"Reason: {query_classification.reasoning}")
            
            # RC-2 FIX: Wenn ResponseGenerator bereits einen Care-System-Prompt
            # mit vollständigem Kontext übergeben hat, überspringe die lokale Kontext-Extraktion.
            # Der Kontext ist bereits im System-Prompt enthalten — Duplikation vermeiden.
            if care_system_prompt and session_context:
                print(f"✅ [RC-2 FIX] Kontext bereits im System-Prompt ({len(care_system_prompt)} Zeichen) — "
                      f"lokale Kontext-Extraktion ÜBERSPRUNGEN (eliminiert Duplikation)")
                notify_progress("Kontext-Enrichment", "✅ Kontext via System-Prompt (keine Duplikation)")
            
            elif session_context:
                notify_progress("Kontext-Enrichment", "Integriere vollständigen Care-Kontext...")
                
                # Extrahiere ALLE verfügbaren Kontext-Daten
                user_id = session_context.get('user_id', 'unknown')
                user_name = session_context.get('user_name', '')  # Anzeigename für persönliche Ansprache
                session_id = session_context.get('session_id', 'unknown')
                mood = session_context.get('mood')
                goals = session_context.get('goals')
                session_summary = session_context.get('summary')
                kg_triples = session_context.get('knowledge_graph', [])
                previous_sessions = session_context.get('previous_sessions', [])
                mood_progression = session_context.get('mood_progression')
                user_insights = session_context.get('user_insights')
                persistent_profile = session_context.get('persistent_profile')  # Synthesized psychological profile
                
                # 🔍 DEBUG: Zeige was wir haben
                print(f"🔍 [PSYCHO-CONTEXT-DEBUG] Session-Kontext Inhalt:")
                print(f"   - user_id: {user_id}")
                print(f"   - user_name: {user_name}")  # NEU
                print(f"   - session_id: {session_id[:12] if session_id else 'None'}...")
                print(f"   - mood: {mood}")
                print(f"   - goals: {goals}")
                print(f"   - session_summary: {session_summary[:50] if session_summary else 'None'}...")
                print(f"   - kg_triples: {len(kg_triples) if kg_triples else 0} Triples")
                print(f"   - previous_sessions: {len(previous_sessions) if previous_sessions else 0} Sessions")
                print(f"   - mood_progression: {mood_progression}")
                print(f"   - user_insights: {user_insights}")
                print(f"   - persistent_profile: {'vorhanden' if persistent_profile else 'None'}")
                
                # Dynamic KG triple limit based on intent (classification already done above)
                kg_triple_limit = _KG_LIMITS_BY_INTENT.get(query_intent, 12)
                
                # 🧠 INTELLIGENTE PRIORISIERUNG nach Query-Relevanz (NICHT nur Confidence!)
                # SOTA FIX: Sortierung nach relevance_score (similarity×0.4 + confidence×0.3 + temporal×0.3)
                # statt reiner Confidence — damit KG-Triples nach tatsächlicher Anfrage-Relevanz sortiert werden
                if kg_triples:
                    kg_triples_sorted = sorted(
                        kg_triples, 
                        key=lambda x: x.get('relevance_score', x.get('combined_score', x.get('confidence', 0.0))), 
                        reverse=True
                    )
                    # Log Top-5 Triples für Debugging
                    print(f"🎯 [PSYCHO-CHAT] Top KG-Triples nach Relevanz (limit={kg_triple_limit}):")
                    for i, triple in enumerate(kg_triples_sorted[:5], 1):
                        rel = triple.get('relevance_score', triple.get('combined_score', triple.get('confidence', 0.0)))
                        print(f"   {i}. {triple.get('subject', '')} → {triple.get('predicate', '')} → {triple.get('object', '')} (relevanz: {rel:.2f})")
                else:
                    kg_triples_sorted = []
                
                # NOTE: Family Entity Boost is now handled upstream in the context
                # builders (WellbeingContextBuilder._gather_knowledge_graph and
                # SessionContextBuilder._load_kg_triples) via family_entity_boost.py.
                # The KG triples in session_context already include family-boosted results.
                
                # Baue strukturierten Kontext-Präfix mit PRIORITÄT
                context_parts = []
                
                # PRIO 0: Name für persönliche Ansprache (WICHTIG!)
                if user_name and len(user_name.strip()) > 0:
                    context_parts.append(f"**WICHTIG - Persönliche Ansprache:** Die Person heißt {user_name}. Sprich sie mit ihrem Namen an, nicht als 'Benutzer', 'Klient' oder 'du'. Verwende den Namen natürlich im Gespräch (z.B. 'Hallo {user_name}', '{user_name}, ich verstehe...', 'Das klingt so, als ob du, {user_name},...').\n")
                    print(f"✅ [PSYCHO-CHAT] Persönliche Ansprache: {user_name}")
                
                # PRIO 0.5: Persistentes psychologisches Profil (holistische Persönlichkeitsübersicht)
                if persistent_profile and isinstance(persistent_profile, dict):
                    profile_parts = []
                    # Core personality
                    core = persistent_profile.get('core_personality')
                    if core and isinstance(core, dict):
                        traits = core.get('traits', [])
                        comm_style = core.get('communication_style', '')
                        if traits:
                            trait_str = ', '.join(str(t) for t in traits[:6])
                            profile_parts.append(f"  - Persönlichkeitsmerkmale: {trait_str}")
                        if comm_style:
                            profile_parts.append(f"  - Kommunikationsstil: {comm_style}")
                    # Current state
                    state = persistent_profile.get('current_state')
                    if state and isinstance(state, dict):
                        concerns = state.get('concerns', [])
                        life_phase = state.get('life_phase', '')
                        if life_phase:
                            profile_parts.append(f"  - Lebensphase: {life_phase}")
                        if concerns:
                            concern_str = ', '.join(str(c) for c in concerns[:4])
                            profile_parts.append(f"  - Aktuelle Anliegen: {concern_str}")
                    # Relationships
                    rels = persistent_profile.get('relationships')
                    if rels and isinstance(rels, dict):
                        family = rels.get('family_dynamics', '')
                        if family:
                            profile_parts.append(f"  - Familiendynamik: {family}")
                    # Coping & resources
                    coping = persistent_profile.get('coping_and_resources')
                    if coping and isinstance(coping, dict):
                        strengths = coping.get('strengths', [])
                        strategies = coping.get('strategies', [])
                        if strengths:
                            profile_parts.append(f"  - Stärken: {', '.join(str(s) for s in strengths[:4])}")
                        if strategies:
                            profile_parts.append(f"  - Bewältigungsstrategien: {', '.join(str(s) for s in strategies[:4])}")
                    # Therapeutic focus
                    focus = persistent_profile.get('therapeutic_focus')
                    if focus and isinstance(focus, dict):
                        priority = focus.get('priority_areas', [])
                        if priority:
                            profile_parts.append(f"  - Therapeutische Schwerpunkte: {', '.join(str(p) for p in priority[:3])}")
                    # Goals & growth
                    growth = persistent_profile.get('goals_and_growth')
                    if growth and isinstance(growth, dict):
                        current_goals = growth.get('current_goals', [])
                        progress = growth.get('progress', '')
                        if current_goals:
                            profile_parts.append(f"  - Aktuelle Ziele: {', '.join(str(g) for g in current_goals[:4])}")
                        if progress:
                            profile_parts.append(f"  - Fortschritt: {progress}")
                    
                    if profile_parts:
                        profile_confidence = persistent_profile.get('overall_confidence', 0.0)
                        profile_header = f"**Wellbeing-Profil von {user_name if user_name else 'dir'}**"
                        if profile_confidence > 0:
                            profile_header += f" (Konfidenz: {profile_confidence:.0%})"
                        profile_header += ":\n"
                        profile_text = profile_header + "\n".join(profile_parts) + "\n"
                        context_parts.append(profile_text)
                        print(f"✅ [PSYCHO-CHAT] Persistentes Profil hinzugefügt ({len(profile_parts)} Abschnitte, Konfidenz: {profile_confidence:.0%})")
                    else:
                        print(f"⚠️ [PSYCHO-CHAT] Persistentes Profil vorhanden, aber alle Sektionen leer")
                
                # PRIO 1: Knowledge Graph (wichtigste Informationen über den Nutzer)
                # Bereits nach Relevanz gefiltert durch search_knowledge_graph() in _build_session_context()
                # Dynamic limit: PERSONAL=20, MIXED=12, FACTUAL=5
                if kg_triples_sorted and len(kg_triples_sorted) > 0:
                    kg_text = (f"**PERSÖNLICHES WISSEN ÜBER {(user_name or 'den Nutzer').upper()} "
                               f"(aus Gesprächen gelernt — hat IMMER Vorrang bei persönlichen Fragen):**\n")
                    for triple in kg_triples_sorted[:kg_triple_limit]:
                        subj = triple.get('subject', '')
                        pred = triple.get('predicate', '')
                        obj = triple.get('object', '')
                        rel = triple.get('relevance_score', triple.get('combined_score', triple.get('confidence', 0.0)))
                        kg_text += f"- {subj} {pred} {obj} (Relevanz: {rel:.0%})\n"
                    context_parts.append(kg_text)
                    print(f"✅ [PSYCHO-CHAT] KG-Kontext hinzugefügt: {min(len(kg_triples_sorted), kg_triple_limit)} Triples (limit={kg_triple_limit}, intent={query_intent.value})")
                
                # PRIO 2: Aktuelle Session-Zusammenfassung (immer relevant)
                if session_summary and len(str(session_summary)) > 0:
                    context_parts.append(f"**Aktuelle Session-Zusammenfassung:**\n{session_summary}\n")
                    print(f"✅ [PSYCHO-CHAT] Session-Summary hinzugefügt ({len(session_summary)} chars)")
                
                # PRIO 3: Emotionaler Zustand & Progression (care-kritisch)
                if mood:
                    mood_text = f"**Aktueller emotionaler Zustand:** {mood}"
                    if mood_progression:
                        mood_text += f"\n**Stimmungsverlauf (7 Tage):** {mood_progression}"
                    context_parts.append(mood_text + "\n")
                    print(f"✅ [PSYCHO-CHAT] Mood-Kontext hinzugefügt")
                
                # PRIO 4: Care-Ziele (falls definiert)
                if goals and len(str(goals)) > 0:
                    if isinstance(goals, list):
                        goals_text = ", ".join(str(g) for g in goals[:5])  # Max 5 Goals
                    else:
                        goals_text = str(goals)[:200]  # Max 200 chars
                    context_parts.append(f"**Care-Ziele:** {goals_text}\n")
                    print(f"✅ [PSYCHO-CHAT] Goals hinzugefügt")
                
                # PRIO 5: User Insights (tiefere Muster - falls verfügbar)
                if user_insights and len(str(user_insights)) > 0:
                    insights_text = str(user_insights)[:300]  # Begrenzt auf 300 chars
                    context_parts.append(f"**Erkannte Muster:**\n{insights_text}\n")
                    print(f"✅ [PSYCHO-CHAT] User Insights hinzugefügt")
                
                # PRIO 6: Frühere Sessions (nur die letzten 3, kurze Zusammenfassungen)
                if previous_sessions and len(previous_sessions) > 0:
                    prev_text = "**Referenzen aus früheren Sessions:**\n"
                    for prev_session in previous_sessions[:3]:  # Max 3
                        prev_summary = prev_session.get('summary', '')
                        prev_date = prev_session.get('date', '')
                        prev_goals = prev_session.get('goals', '')  # ✅ NEU: Care-Ziele
                        if prev_summary or prev_goals:
                            # Session-Info (Datum + Summary)
                            prev_text += f"- [{prev_date}] {prev_summary[:80] if prev_summary else 'Keine Zusammenfassung'}...\n"
                            # Ziele falls vorhanden
                            if prev_goals:
                                prev_text += f"  └─ Ziele: {prev_goals[:60]}...\n"
                    context_parts.append(prev_text)
                    print(f"✅ [PSYCHO-CHAT] Previous Sessions hinzugefügt ({len(previous_sessions[:3])} Sessions)")
                
                # Kombiniere alle Context-Parts mit expliziter Quellen-Trennung
                if context_parts:
                    context_block = "\n".join(context_parts)
                    
                    # Explizite Source-Separation-Instruktion für das LLM
                    source_instruction = ""
                    if query_intent == QueryIntent.PERSONAL:
                        source_instruction = (
                            "\n**WICHTIGE ANWEISUNG:** Dies ist eine persönliche Frage. "
                            "Antworte ausschließlich auf Basis des PERSÖNLICHEN WISSENS oben. "
                            "Vermische KEINE allgemeinen Fachwissen-Informationen mit den persönlichen Daten. "
                            "Wenn du nicht genug persönliche Informationen hast, sage das ehrlich.\n"
                        )
                    elif query_intent == QueryIntent.MIXED:
                        source_instruction = (
                            "\n**WICHTIGE ANWEISUNG:** Diese Frage verbindet Persönliches mit Fachwissen. "
                            "Trenne klar zwischen dem, was du über die Person weißt (PERSÖNLICHES WISSEN), "
                            "und allgemeinem Fachwissen (PSYCHOLOGIE-FACHWISSEN). "
                            "Persönliches Wissen hat IMMER Vorrang und darf NICHT mit generischem Wissen verwechselt werden.\n"
                        )
                    
                    enriched_prompt = f"""**CARE-KONTEXT:**
{context_block}
{source_instruction}
**AKTUELLE ANFRAGE:**
{user_prompt}"""
                    notify_progress("Kontext-Enrichment", f"✅ Kontext hinzugefügt: {len(context_block)} Zeichen")
                    print(f"🧠 [PSYCHO-CHAT] Vollständiger Kontext integriert ({len(context_block)} chars, {len(context_parts)} Komponenten, intent={query_intent.value})")
            
            # === RAG-SUCHE UND LLM-ANTWORT ===
            # Branch B: Smart Query Classifier + Conditional RAG
            # PERSONAL → KG + Profile only, SKIP RAG (prevents contamination)
            # MIXED    → KG + Profile + RAG (with explicit source separation)
            # FACTUAL  → Full RAG + minimal KG background
            
            # query_intent ist immer gesetzt (wird vor dem Context-Block klassifiziert)
            effective_intent = query_intent
            
            # 1. RAG-Suche — nur bei MIXED oder FACTUAL
            rag_results = []
            web_results = []
            
            if effective_intent == QueryIntent.PERSONAL:
                # ── PERSONAL: Kein RAG, kein Web → nur KG + Profil ──
                print(f"🚫 [INTENT-ROUTER] RAG ÜBERSPRUNGEN (Intent=PERSONAL) — "
                      f"Vermeide Kontamination durch generisches Fachwissen")
                notify_progress("RAG übersprungen", "Persönliche Frage → nur persönliches Wissen")
            else:
                # ── MIXED / FACTUAL: RAG ausführen ──
                # SOTA Variant 4: decompose first, then fan out per factual sub-query.
                # Personal sub-queries (only produced for MIXED) are NOT used to drive
                # textbook RAG — they are auxiliary signals for downstream stages.
                # Below COMPLEX threshold the engine returns a single passthrough,
                # so behaviour for simple queries is byte-identical to the previous
                # single-shot path.
                decomp_result = self.decomposition_engine.decompose(
                    user_prompt, intent=effective_intent
                )
                factual_subqueries = decomp_result.factual_queries
                if not factual_subqueries:
                    # Engine produced only personal sub-queries (rare MIXED edge
                    # case) → fall back to the original prompt for RAG so we
                    # never silently skip retrieval.
                    factual_subqueries = [user_prompt]

                if decomp_result.decomposed:
                    print(
                        f"🧩 [DECOMP] {len(decomp_result.sub_queries)} sub-queries "
                        f"(factual={len(decomp_result.factual_queries)}, "
                        f"personal={len(decomp_result.personal_queries)}, "
                        f"complexity={decomp_result.complexity.value})"
                    )
                    notify_progress(
                        "Query-Zerlegung",
                        f"{len(factual_subqueries)} fachliche Sub-Fragen "
                        f"({decomp_result.complexity.value})",
                    )
                else:
                    print(f"🧩 [DECOMP] passthrough — {decomp_result.reasoning}")

                notify_progress(
                    "RAG-Suche",
                    "Durchsuche Psychologie-Datenbank nach relevantem Fachwissen...",
                )

                if self.orchestrator and hasattr(self.orchestrator, 'rag_manager'):
                    try:
                        notify_progress("FAISS-Suche", "Semantische Suche in Psychologie-Datenbank...")

                        effective_min_score = (
                            faiss_min_confidence
                            if faiss_min_confidence is not None
                            else 0.55
                        )

                        # Web-fallback gate: only pure FACTUAL intent may
                        # pull from the open web. MIXED stays inside the
                        # local clinical corpus to avoid contaminating a
                        # care answer with arbitrary web prose.
                        web_fallback_allowed = (
                            effective_intent == QueryIntent.FACTUAL
                            and not parse_bool_env("APP_LOCAL_ONLY", "0")
                        )

                        # Per-sub-query k: divide the original budget so total
                        # retrieval count stays in the same order of magnitude
                        # regardless of decomposition depth.
                        per_query_k = max(3, 8 // max(1, len(factual_subqueries)))

                        # ── Option C: corpus-domain routing by intent ──
                        # MIXED  → only the curated psych corpus (no general
                        #          web/textbook noise next to the persistent
                        #          user profile).
                        # FACTUAL → both psych and general corpora; psych is
                        #           still ranked higher via score.
                        # Crisis-flagged content is always excluded from
                        # retrieval — it can only surface through the
                        # dedicated emotional-crisis pipeline.
                        if effective_intent == QueryIntent.MIXED:
                            allowed_domains: Optional[List[str]] = ['psych']
                        else:  # FACTUAL (PERSONAL is short-circuited above)
                            allowed_domains = ['psych', 'general']
                        exclude_safety_flags: Optional[List[str]] = ['crisis']

                        # Track best (highest-score) instance per content hash to
                        # deduplicate across sub-queries. Web results are keyed
                        # by URL; chunk results by (source, first 200 chars).
                        rag_seen: Dict[str, Dict[str, Any]] = {}
                        web_seen: Dict[str, Dict[str, Any]] = {}

                        for sub_idx, sub_q in enumerate(factual_subqueries, 1):
                            print(f"   ↳ Sub-Query {sub_idx}/{len(factual_subqueries)}: {sub_q[:80]}")
                            rag_chunks = self.orchestrator.rag_manager.execute_rag_with_gap_detection(
                                query=sub_q,
                                k=per_query_k,
                                min_score=effective_min_score,
                                multiquery_enabled=False,  # decomposition handled at engine level
                                min_results_threshold=3,
                                enable_web_fallback=web_fallback_allowed,
                                persist_to_rag=True,
                                allowed_domains=allowed_domains,
                                exclude_safety_flags=exclude_safety_flags,
                            )

                            if not rag_chunks:
                                continue

                            for tool_result in rag_chunks:
                                if not tool_result.success or not tool_result.results:
                                    continue

                                is_web_source = (
                                    getattr(tool_result, 'tool', '') == 'web_search'
                                )

                                for chunk in tool_result.results:
                                    text = (
                                        chunk.get('snippet', '')
                                        or chunk.get('content', '')
                                        or chunk.get('text', '')
                                    )
                                    if not text.strip():
                                        continue

                                    score = float(chunk.get('score', 0.0) or 0.0)

                                    if is_web_source or not chunk.get('source_type'):
                                        url = chunk.get('url', '') or ''
                                        key = url or f"_no_url_{hash(text[:200])}"
                                        existing = web_seen.get(key)
                                        if existing is None or score > existing['score']:
                                            web_seen[key] = {
                                                'snippet': text[:400],
                                                'url': url,
                                                'title': chunk.get('title', ''),
                                                'score': score,
                                            }
                                    else:
                                        metadata = chunk.get('metadata', {}) or {}
                                        source_id = (
                                            metadata.get('source')
                                            or metadata.get('title')
                                            or chunk.get('title', '')
                                        )
                                        key = f"{source_id}::{hash(text[:200])}"
                                        existing = rag_seen.get(key)
                                        if existing is None or score > existing['score']:
                                            rag_seen[key] = {
                                                'content': text,
                                                'score': score,
                                                'metadata': metadata,
                                                'source_type': chunk.get('source_type', 'chunk'),
                                                'title': chunk.get('title', ''),
                                                'url': chunk.get('url', ''),
                                            }

                        rag_results = list(rag_seen.values())
                        web_results = list(web_seen.values())

                        if rag_results or web_results:
                            print(
                                f"✅ [PSYCHO-RAG] {len(rag_results)} RAG-Chunks + "
                                f"{len(web_results)} Web-Ergebnisse "
                                f"(intent={effective_intent.value}, "
                                f"sub_queries={len(factual_subqueries)}, "
                                f"deduped)"
                            )
                            notify_progress(
                                "RAG-Ergebnisse",
                                f"{len(rag_results)} RAG + {len(web_results)} Web "
                                f"über {len(factual_subqueries)} Sub-Fragen",
                            )
                        else:
                            print(f"⚠️ [PSYCHO-RAG] Keine RAG-Ergebnisse")
                            notify_progress("Keine RAG-Ergebnisse", "Reines LLM ohne RAG-Kontext")

                    except Exception as e:
                        print(f"⚠️ [PSYCHO-RAG] RAG-Suche fehlgeschlagen: {e}")
                        import traceback
                        traceback.print_exc()
                        notify_progress("RAG-Fehler", f"RAG-Suche fehlgeschlagen, nutze reines LLM")
                else:
                    print(f"⚠️ [PSYCHO-RAG] Kein Orchestrator oder RAGManager verfügbar!")
                    notify_progress("Warnung", "Orchestrator nicht verfügbar - nutze reines LLM")
            
            # 3. Baue Prompt mit RAG-Evidenz und Session-Kontext
            notify_progress("Prompt-Aufbereitung", "Integriere RAG-Evidenz in Prompt...")
            
            # RAG-Evidenz formatieren (nur wenn RAG ausgeführt wurde)
            rag_context = ""
            if rag_results:
                rag_results_sorted = sorted(rag_results, key=lambda x: x.get('score', 0.0), reverse=True)
                # Explizites Label: ALLGEMEINES Fachwissen, NICHT persönlich
                rag_context = ("\n**ALLGEMEINES PSYCHOLOGIE-FACHWISSEN (aus Fachliteratur-Datenbank — "
                               "NICHT mit persönlichem Wissen verwechseln!):**\n")
                for i, result in enumerate(rag_results_sorted[:5], 1):
                    content = result.get('content', '')[:500]
                    score = result.get('score', 0.0)
                    metadata = result.get('metadata', {})
                    source = metadata.get('source', metadata.get('title', result.get('title', 'Unbekannt')))
                    if content.strip():
                        rag_context += f"{i}. [Relevanz: {score:.2f}, Quelle: {source}]\n{content}\n\n"
                print(f"✅ [PSYCHO-RAG] RAG-Kontext aufbereitet ({len(rag_context)} chars)")
            
            # Web-Evidenz formatieren (falls vorhanden)
            web_context = ""
            if web_results:
                web_context = "\n**ERGÄNZENDE ONLINE-QUELLEN (allgemeines Fachwissen, NICHT persönlich):**\n"
                for i, result in enumerate(web_results[:3], 1):
                    snippet = result.get('snippet', '')[:300]
                    url = result.get('url', '')
                    title = result.get('title', '')
                    if snippet.strip():
                        web_context += f"{i}. [{title}]({url})\n{snippet}\n\n"
                print(f"✅ [PSYCHO-WEB] Web-Kontext aufbereitet ({len(web_context)} chars)")

            verified_web_urls = tuple(
                str(result.get('url', '')).strip()
                for result in web_results
                if str(result.get('url', '')).strip()
            )
            provenance_instruction = build_psych_web_provenance_instruction(
                verified_web_urls
            )
            for message in llm_messages:
                if message.get('role') == 'system':
                    message['content'] += provenance_instruction
                    break
            
            # Kombiniere ALLE Kontexte: Session + RAG + Web
            # ══════════════════════════════════════════════════════════════
            # CRITICAL FIX: RAG-Inhalte werden als SYSTEM-Kontext injiziert,
            # NICHT in die User-Nachricht gemischt. Andernfalls denkt das LLM,
            # der User habe den RAG-Text geschrieben, und antwortet darauf
            # statt auf die eigentliche User-Frage.
            # ══════════════════════════════════════════════════════════════
            
            # Die User-Nachricht bleibt IMMER die letzte Nachricht
            # CRITICAL FIX: RAG/Web-Kontext wird als Teil der User-Nachricht angehängt,
            # NICHT als separate System-Nachricht eingefügt.
            # Eine System-Nachricht zwischen assistant und user würde die
            # Rolle-Alternation (user/assistant/user/assistant) brechen.
            # ══════════════════════════════════════════════════════════════
            
            # RAG/Web-Kontext als Teil der User-Nachricht zusammenbauen
            supplementary_context_parts = []
            
            # Session-Kontext (aus enriched_prompt, falls es über user_prompt hinausgeht)
            if enriched_prompt != user_prompt and len(enriched_prompt) > len(user_prompt):
                supplementary_context_parts.append(enriched_prompt.replace(user_prompt, '').strip())
            
            # RC-4: Token-Budget-Trimming für RAG und Web
            if rag_context:
                rag_context = token_budget.trim_rag_context(rag_context)
                supplementary_context_parts.append(rag_context)
            
            if web_context:
                web_context = token_budget.trim_web_context(web_context)
                supplementary_context_parts.append(web_context)
            
            if supplementary_context_parts:
                supplementary_user_context = (
                    "\n\n--- ZUSÄTZLICHER KONTEXT (Hintergrundinformationen — "
                    "stammt aus meinem Wissenspool, nicht von dir): ---\n\n"
                    + "\n\n".join(supplementary_context_parts)
                    + "\n\n--- ENDE KONTEXT ---\n\n"
                    + "Meine eigentliche Frage/Nachricht war:\n" + user_prompt
                )
                # Ersetze die User-Nachricht mit kontextangereicherter Version
                llm_messages[-1]['content'] = supplementary_user_context
            else:
                # Kein zusätzlicher Kontext — User-Nachricht bleibt rein
                llm_messages[-1]['content'] = user_prompt
            
            print(f"🧠 [PSYCHO-PROMPT] Messages: {len(llm_messages)} "
                  f"(RAG/Web als System-Kontext, User-Nachricht unverändert)")
            
            # 4. LLM-Antwort generieren mit vollständigem Kontext
            notify_progress("LLM-Generation", "Generiere Care-Antwort mit vollem Kontext...")
            
            try:
                # User-Nachricht ist bereits korrekt in llm_messages[-1] (reiner User-Prompt)
                # RAG/Web-Kontext ist als System-Nachricht vor der User-Nachricht eingefügt
                
                # RC-4: Finale Validierung — passt alles in n_ctx?
                is_valid, total_tokens = token_budget.validate_messages(llm_messages)
                if not is_valid:
                    # Emergency-Trim: aggressive Reduktion (User-Msg → System-Prompt → History)
                    print(f"⚠️ [TOKEN-BUDGET] Emergency-Trimming: {total_tokens} tokens > Budget")
                    llm_messages = token_budget.emergency_trim_messages(llm_messages)
                    # Re-validation nach Emergency-Trim
                    is_valid_post, total_post = token_budget.validate_messages(llm_messages)
                    if not is_valid_post:
                        raise RuntimeError(
                            "Token budget invariant violated after emergency trim: "
                            f"{total_post} > {token_budget._prompt_budget}"
                        )
                
                # ═══ ROLE-ALTERNATION FIX ═══
                # Normalisiere Rollen-Alternation VOR dem LLM-Call.
                # session_history kann konsekutive Rollen enthalten (DB-Relikte,
                # Retries, schnelle User-Eingaben). Ohne Normalisierung wirft
                # llama-cpp-python "Conversation roles must alternate".
                llm_messages = _normalize_role_alternation(llm_messages)
                # ════════════════════════════

                # Generiere Antwort
                response = self.model_loader.generate_response(
                    max_tokens=token_scaling.main_generation_max_tokens(
                        fallback=3072, current=self.settings.get("max_tokens", 3072)
                    ),
                    temperature=self.settings.get("temperature", 0.7),
                    messages=llm_messages
                )

                def regenerate_with_provenance(correction_instruction: str) -> str:
                    corrected_messages = [dict(message) for message in llm_messages]
                    if corrected_messages and corrected_messages[0].get('role') == 'system':
                        corrected_messages[0]['content'] += correction_instruction
                    else:
                        corrected_messages.insert(
                            0,
                            {'role': 'system', 'content': correction_instruction},
                        )
                    retry_valid, _ = token_budget.validate_messages(corrected_messages)
                    if not retry_valid:
                        corrected_messages = token_budget.emergency_trim_messages(
                            corrected_messages
                        )
                    corrected_messages = _normalize_role_alternation(corrected_messages)
                    return str(self.model_loader.generate_response(
                        max_tokens=token_scaling.main_generation_max_tokens(
                            fallback=3072, current=self.settings.get("max_tokens", 3072)
                        ),
                        temperature=self.settings.get("temperature", 0.7),
                        messages=corrected_messages,
                    ))

                try:
                    from i18n import get_current_language
                    response_language = get_current_language() or "de"
                except Exception:
                    response_language = "de"

                response, provenance_replaced = finalize_wellbeing_response_provenance(
                    str(response),
                    verified_web_urls=verified_web_urls,
                    regenerate=regenerate_with_provenance,
                    language=response_language,
                )
                if provenance_replaced:
                    logger.warning(
                        "Psychotab-Draft wegen ungedeckter Web-Provenienz verworfen "
                        "und kontrolliert ersetzt (verified_urls=%d)",
                        len(verified_web_urls),
                    )
                
                # Message History NICHT aktualisieren! 
                # (Psycho-Chat hat eigene DB-basierte Historie)
                print(f"✅ [PSYCHO-LLM] Antwort generiert ({len(response)} chars)")
                notify_progress("Antwort generiert", f"Care-Antwort erstellt ({len(response)} Zeichen)")
                
            except Exception as e:
                print(f"❌ [PSYCHO-LLM] Fehler bei LLM-Generation: {e}")
                raise
            
            # === POST-RESPONSE: MOOD & GOAL TRACKING ===
            # RC-7 FIX: Delegiere an PostResponseHandler (keine 4-Level-Import-Ketten mehr)
            if session_context and response:
                try:
                    _session_id = session_context.get('session_id')

                    if _session_id:
                        from wellbeing_session.handlers.post_response_handler import PostResponseHandler
                        
                        # Hole SessionManager über wellbeing_interface (1 Level, nicht 4)
                        _sm = None
                        if hasattr(self, 'wellbeing_interface') and self.wellbeing_interface:
                            _sm = getattr(self.wellbeing_interface, 'session_manager', None)
                        
                        if _sm:
                            handler = PostResponseHandler(session_manager=_sm)
                            handler.process(
                                user_message=user_prompt,
                                session_id=_session_id,
                            )
                            notify_progress("Mood/Goal-Tracking", "✅ Analyse abgeschlossen")
                        else:
                            print(f"⚠️ [MOOD/GOAL-TRACKING] SessionManager nicht verfügbar")
                    
                except Exception as e:
                    # Graceful degradation: Tracking ist optional
                    print(f"⚠️ [MOOD/GOAL-TRACKING] Fehler (nicht-kritisch): {e}")
            
            notify_progress("Fertig", "Antwort generiert")
            
            return response
        
        except Exception as e:
            notify_progress("Fehler", f"Fehler im Wellbeing-Chat: {str(e)}")
            print(f"❌ Wellbeing-Chat-Fehler: {e}")
            return f"Entschuldigung, es gab einen Fehler: {str(e)}"

