import json
import os
import re
import subprocess
import urllib.request
import urllib.parse
from pathlib import Path
from typing import Dict, List, Any, Optional, Callable, Sequence, TYPE_CHECKING
import logging
from datetime import datetime
import sys
from agent.web_policy import WebFetchPolicy, FetchDecision
from agent.tool_schemas import get_toolkit_format_schemas
from agent.path_sandbox import (
    PathSandbox,
    PathSandboxError,
    DEFAULT_READ_LINE_LIMIT,
    DEFAULT_RG_MAX_RESULTS,
    DEFAULT_RG_TIMEOUT,
    DEFAULT_MAX_SEARCH_RESULTS,
    RipgrepNotFoundError,
)
from utils.runtime_policy import parse_bool_env

# Logger Setup (muss VOR den bedingten Imports stehen)
logger = logging.getLogger(__name__)


# ✅ SOTA: Code Executor Engine (AST security, persistent sessions, auto-retry)
try:
    from code_executor_engine import CodeExecutorEngine
    CODE_EXECUTOR_ENGINE_AVAILABLE = True
    logger.info("✅ CodeExecutorEngine verfügbar")
except ImportError as _ce_err:
    CodeExecutorEngine = None  # type: ignore[assignment,misc]
    CODE_EXECUTOR_ENGINE_AVAILABLE = False
    logger.warning(f"⚠️ CodeExecutorEngine nicht verfügbar: {_ce_err}")

# ✅ SOTA 2026: Hybrid Structured Data Extraction (Schema.org + LLM)
STRUCTURED_EXTRACTOR_AVAILABLE = False
try:
    from structured_data_extractor import extract_structured_data
    STRUCTURED_EXTRACTOR_AVAILABLE = True
    logging.getLogger(__name__).info("✅ Structured Data Extractor verfügbar")
except ImportError:
    extract_structured_data = None  # type: ignore[assignment,unused-ignore]
    STRUCTURED_EXTRACTOR_AVAILABLE = False

# ✅ PHASE 2A: Modular Web Search (Refactored from CC:95 → CC:8)
WEB_SEARCH_V2_AVAILABLE = False
try:
    from web_search import (
        WebSearchOrchestrator,
        DuckDuckGoStrategy,
        BlacklistFilter,
        PrivacyFilter,
        SourceDiversityFilter,
        FreshnessBoostFilter,
        SearchParams,
        SearchStrategy,
        FilterStrategy,
        HTMLEnrichment,
    )
    from web_search.strategies.brave import BraveSearchStrategy
    WEB_SEARCH_V2_AVAILABLE = True
    logger.info("✅ Web Search V2 (Modular) verfügbar")
except ImportError as e:
    logger.warning(f"⚠️ Web Search V2 nicht verfügbar, verwende Legacy: {e}")
    WebSearchOrchestrator = None  # type: ignore
    HTMLEnrichment = None  # type: ignore
    SourceDiversityFilter = None  # type: ignore
    FreshnessBoostFilter = None  # type: ignore
    WEB_SEARCH_V2_AVAILABLE = False

# Enhanced Privacy Handler (robuste LLM-Calls, deutsche Spracherkennung)
PRIVACY_HANDLER_AVAILABLE = False  # Default value
if TYPE_CHECKING:
    from agent.privacy_handler_enhanced import EnhancedPrivacyHandler as ChainOfThoughtPrivacyHandler
    PRIVACY_HANDLER_AVAILABLE = True
else:
    try:
        from agent.privacy_handler_enhanced import EnhancedPrivacyHandler as ChainOfThoughtPrivacyHandler
        PRIVACY_HANDLER_AVAILABLE = True
    except ImportError:
        ChainOfThoughtPrivacyHandler = None  # type: ignore
        PRIVACY_HANDLER_AVAILABLE = False
        logging.warning("⚠️ Enhanced Privacy Handler nicht verfügbar")

# CuPy für GPU-Beschleunigung (optional)
CUPY_AVAILABLE = False
try:
    import cupy as cp  # type: ignore[import]
    CUPY_AVAILABLE = True
    logger.info("✅ CuPy verfügbar - GPU-Beschleunigung aktiviert")
except ImportError:
    cp = None  # type: ignore
    CUPY_AVAILABLE = False
    logger.info("ℹ️ CuPy nicht verfügbar - nutze NumPy Fallback")

# Newspaper3k für News-Scraping (optional)
NEWSPAPER_AVAILABLE = False
try:
    import newspaper  # type: ignore[import]
    NEWSPAPER_AVAILABLE = True
    logger.info("✅ Newspaper3k verfügbar - News-Scraping aktiviert")
except ImportError:
    newspaper = None  # type: ignore
    NEWSPAPER_AVAILABLE = False
    logger.info("ℹ️ Newspaper3k nicht verfügbar - nutze BeautifulSoup Fallback")

# ✅ PHASE 2D: Modern Content Extraction V2 (Refactored from CC:49 → CC:8)
CONTENT_EXTRACTOR_V2_AVAILABLE = False
try:
    from content_extractor import create_default_orchestrator, ModernContentOrchestrator
    CONTENT_EXTRACTOR_V2_AVAILABLE = True
    logger.info("✅ Content Extractor V2 (Modular) verfügbar")
except ImportError as e:
    logger.warning(f"⚠️ Content Extractor V2 nicht verfügbar, verwende Legacy: {e}")
    ModernContentOrchestrator = None  # type: ignore
    CONTENT_EXTRACTOR_V2_AVAILABLE = False


# ------------------------------------------------------------------
# SOTA 2026-08-21: Tool-Param-Mismatch Guard (Root-Cause-Fix, Phase 2)
# ------------------------------------------------------------------
# Beobachteter Fehlerfall (2026-08-21, Gemma4 12B): Das LLM rief
# `list_directory(q="bitcoin ...", num=...)` auf -- d.h. den Tool-NAMEN
# aus dem Dateisystem-Profil + die Parameter aus `web_search`. Das war
# eine Halluzination aus Tool-Verwechslung (nicht aus fehlendem Tool).
#
# Der Guard erkennt dieses Muster und antwortet mit einem strukturierten
# Korrektur-Signal (error_class='tool_param_mismatch', suggested_tool,
# suggested_parameters), damit das LLM in der nächsten Iteration das
# korrekte Tool aufruft -- statt eines leisen Sandbox-Fehlers, der das
# LLM in eine Wiederholungs-Schleife trieb.
_WEB_SEARCH_PARAM_KEYS = frozenset({
    "q", "query", "num", "num_results", "search_query",
})
_PATH_PARAM_KEYS = frozenset({
    "path", "root_path", "file_path", "pattern",
})
_FS_TOOLS_GUARDED = frozenset({"list_directory", "search_files", "file_reader"})


def suggest_web_search_for_fs_misuse(
    tool_name: str, parameters: Dict[str, Any]
) -> Optional[Dict[str, Any]]:
    """Erkennt web_search-Parameter an einem Dateisystem-Tool.

    Args:
        tool_name: Name des aufgerufenen Tools.
        parameters: Übergebene Parameter (dict).

    Returns:
        dict mit `query`/`num_results` als Vorschlag für `web_search`,
        wenn die Parameter eindeutig web-search-geprägt sind und KEINE
        Pfad-Keys enthalten; sonst None (kein Missuse, kein Guard-Trigger).
    """
    if tool_name not in _FS_TOOLS_GUARDED or not isinstance(parameters, dict):
        return None
    # Case-insensitive: Das LLM kann Keys mit beliebiger Groß-/Kleinschreibung
    # senden ("q"/"Q", "num"/"NUM"). Erkennung UND Wert-Extraktion laufen
    # deshalb über ein normalisiertes dict -- sonst wäre die Korrektur-Leer
    # (Trigger ja, aber suggested.query = "").
    params_ci = {str(k).lower(): v for k, v in parameters.items()}
    keys = set(params_ci)
    if not (keys & _WEB_SEARCH_PARAM_KEYS):
        return None
    if keys & _PATH_PARAM_KEYS:
        # Pfad vorhanden → mutmaßlich beabsichtigte Dateisystem-Operation.
        return None

    query = (
        params_ci.get("query")
        or params_ci.get("q")
        or params_ci.get("search_query")
        or ""
    )
    suggested: Dict[str, Any] = {"query": str(query).strip()}
    num_raw = params_ci.get("num_results", params_ci.get("num"))
    if num_raw is not None:
        try:
            num = int(num_raw)
            if 1 <= num <= 10:
                suggested["num_results"] = num
        except (TypeError, ValueError):
            pass
    return suggested


class AgentToolkit:
    """
    Agent-Toolkit für das Mistral Chatbot System
    Das LLM entscheidet selbst, welche Tools zu verwenden sind
    """
    
    # Type hints für Attribute
    web_policy: Optional[WebFetchPolicy]
    privacy_handler: Optional[Any]  # ChainOfThoughtPrivacyHandler might be None
    _llm_client: Optional[Any]
    python_session: Optional[Any]
    execution_log: List[Any]
    MAX_LOG_SIZE: int
    tools: Dict[str, Dict]
    web_search_orchestrator: Optional[Any]  # ✅ PHASE 2A: Modular Web Search
    content_extractor_orchestrator: Optional[Any]  # ✅ PHASE 2D: Modern Content Extraction
    
    def __init__(self):
        self.local_only_mode = parse_bool_env("APP_LOCAL_ONLY", "0")
        self.tools = self._initialize_tools()
        self.execution_log = []
        self.MAX_LOG_SIZE = 1000  # Memory-Leak-Schutz: Execution Log begrenzen
        # Lightweight negative cache / circuit breaker for web fetching
        # db_path=None → kanonischer Pfad aus utils/db_path_resolver (Fix 2026-08-10)
        self.web_policy = WebFetchPolicy(threshold=3, window_days=7)
        # ✅ SOTA: Pfad-Sandbox für alle dateibezogenen Tools (file_reader/writer,
        # pdf_extract, image_info, vision_describe). Workspace-Root als einziges
        # erlaubtes Base-Dir. Verhindert Path-Traversal/Symlink-Escape und
        # limitiert Datei-Größen.
        self.path_sandbox = PathSandbox()
        self._allowlist_state_file = (
            Path(__file__).resolve().parent / "config" / "path_allowlist.json"
        )
        self._default_base_dirs = list(self.path_sandbox.list_base_dirs())
        self._temporary_allowlist_dirs: List[str] = []
        self._persistent_allowlist_dirs: List[str] = self._load_persistent_allowlist_dirs()
        self._pending_permission_request: Optional[Dict[str, Any]] = None
        self._rebuild_path_sandbox()
        # Persistent Python session für optimierte Code-Ausführung
        self.python_session = None  # Lazy initialization for better startup time
        
        # ✅ PHASE 2A: Modular Web Search V2 (Refactored, CC: 95 → 8)
        self.web_search_orchestrator = None  # Lazy initialization
        self._init_web_search_v2()
        
        # ✅ PHASE 2D: Modern Content Extraction V2 (Refactored, CC: 49 → 8)
        self.content_extractor_orchestrator = None  # Lazy initialization
        self._init_content_extractor_v2()
        
        # ✅ SOTA: Code Executor Engine (AST security, auto-retry, persistent sessions)
        self._code_engine: Optional[Any] = None
        if CODE_EXECUTOR_ENGINE_AVAILABLE and CodeExecutorEngine is not None:
            try:
                self._code_engine = CodeExecutorEngine(
                    sandbox_base_dir=os.path.join(os.path.dirname(os.path.abspath(__file__)), "code_sandbox"),
                    max_retries=3,
                    default_timeout=30,
                )
                logging.info("✅ CodeExecutorEngine initialisiert")
            except Exception as _ce_init_err:
                logging.warning(f"⚠️ CodeExecutorEngine Init fehlgeschlagen: {_ce_init_err}")
                self._code_engine = None

        # 🔒 DATENSCHUTZ: Privacy Handler (Lazy-Init, wird erst bei Bedarf geladen)
        self.privacy_handler = None  # Lazy initialization
        self._llm_client = None  # Wird von außen gesetzt (auch für Vision-Diagramm-Validierung)
        self.chat_function = None  # Chat-Funktion für Vision-basierte Diagramm-Validierung
        if self.local_only_mode:
            logger.info("🔒 APP_LOCAL_ONLY aktiv: Web-Suche ist global deaktiviert")

    def _load_persistent_allowlist_dirs(self) -> List[str]:
        """Load persisted allowlist extensions from local config state."""
        try:
            if not self._allowlist_state_file.exists():
                return []
            raw = json.loads(self._allowlist_state_file.read_text(encoding="utf-8"))
            dirs = raw.get("extra_base_dirs", []) if isinstance(raw, dict) else []
            if not isinstance(dirs, list):
                return []
            normalized: List[str] = []
            for item in dirs:
                if not isinstance(item, str) or not item.strip():
                    continue
                p = Path(item.strip()).expanduser()
                if p.exists() and p.is_dir():
                    normalized.append(str(p.resolve()))
            return normalized
        except Exception as exc:
            logger.warning("⚠️ Konnte persistente Pfad-Allowlist nicht laden: %s", exc)
            return []

    def _save_persistent_allowlist_dirs(self) -> None:
        """Persist allowlist extensions on disk for future sessions."""
        self._allowlist_state_file.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "extra_base_dirs": sorted(set(self._persistent_allowlist_dirs)),
            "updated_at": datetime.now().isoformat(),
        }
        self._allowlist_state_file.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _rebuild_path_sandbox(self) -> None:
        """Rebuild sandbox with default + persistent + temporary allowlist dirs."""
        combined = list(dict.fromkeys(
            self._default_base_dirs
            + self._persistent_allowlist_dirs
            + self._temporary_allowlist_dirs
        ))
        self.path_sandbox = PathSandbox(base_dirs=combined)

    def _normalize_requested_base_dir(self, requested_path: str) -> str:
        """Resolve requested path to a directory that can be added as sandbox base."""
        if not requested_path or not isinstance(requested_path, str):
            raise PathSandboxError("Ungültiger Pfad für Freigabe")
        raw = Path(requested_path).expanduser()
        candidate = raw if raw.is_dir() else raw.parent
        if not candidate.exists() or not candidate.is_dir():
            raise PathSandboxError(f"Pfad für Freigabe existiert nicht: {candidate}")
        return str(candidate.resolve())

    def get_pending_permission_request(self) -> Optional[Dict[str, Any]]:
        """Return the latest pending sandbox permission request, if any."""
        return dict(self._pending_permission_request) if self._pending_permission_request else None

    def clear_pending_permission_request(self) -> None:
        """Clear pending sandbox permission request."""
        self._pending_permission_request = None

    def grant_pending_path_access(self, mode: str = "temporary") -> Dict[str, Any]:
        """Apply a pending permission request as temporary or persistent allowlist grant."""
        pending = self._pending_permission_request
        if not pending:
            return {
                "success": False,
                "error": "Keine ausstehende Freigabeanfrage vorhanden",
                "error_class": "no_pending_permission_request",
            }

        requested_path = str(pending.get("requested_path") or "").strip()
        if not requested_path:
            return {
                "success": False,
                "error": "Ausstehende Anfrage enthält keinen Pfad",
                "error_class": "missing_requested_path",
            }

        base_dir = self._normalize_requested_base_dir(requested_path)
        normalized_mode = "persistent" if str(mode).lower() in {"persistent", "permanent", "allowlist"} else "temporary"

        if normalized_mode == "persistent":
            if base_dir not in self._persistent_allowlist_dirs:
                self._persistent_allowlist_dirs.append(base_dir)
                self._save_persistent_allowlist_dirs()
        else:
            if base_dir not in self._temporary_allowlist_dirs:
                self._temporary_allowlist_dirs.append(base_dir)

        self._rebuild_path_sandbox()
        self._pending_permission_request = None

        return {
            "success": True,
            "grant_mode": normalized_mode,
            "granted_base_dir": base_dir,
            "base_dirs": self.path_sandbox.list_base_dirs(),
            "message": (
                "Pfadfreigabe dauerhaft gespeichert"
                if normalized_mode == "persistent"
                else "Temporäre Pfadfreigabe aktiv (nur aktuelle Runtime)"
            ),
        }

    def _refresh_runtime_mode(self) -> None:
        """Synchronize toolkit runtime mode with current APP_LOCAL_ONLY environment value."""
        current_mode = parse_bool_env("APP_LOCAL_ONLY", "0")
        if current_mode == self.local_only_mode:
            return

        previous_mode = self.local_only_mode
        self.local_only_mode = current_mode
        logger.warning(
            "⚙️ AgentToolkit Runtime-Mode-Wechsel erkannt (APP_LOCAL_ONLY: %s -> %s)",
            previous_mode,
            current_mode,
        )

        if current_mode:
            self.web_search_orchestrator = None
            logger.info("🔒 Web Search V2 deaktiviert (lokaler Modus aktiv)")
            return

        if self.web_search_orchestrator is None:
            self._init_web_search_v2()
    
    def set_chat_function(self, chat_function):
        """Setzt die Chat-Funktion für Vision-basierte Diagramm-Validierung."""
        self.chat_function = chat_function
        logging.info("✅ Chat-Funktion für Vision-Validierung gesetzt")
    
    def set_llm_client(self, llm_client):
        """Setzt den LLM-Client für Enhanced Privacy-Handler (muss von außen aufgerufen werden)"""
        self._llm_client = llm_client
        # ✅ SOTA: CodeExecutorEngine mit LLM für Auto-Retry verbinden
        if self._code_engine is not None:
            self._code_engine.model_loader = llm_client
            # SOTA: Research-Augmented Code Fixing -- web search + RAG for
            # escalated retries (ART: Paranjape et al. 2023)
            self._code_engine.web_search_fn = self._code_fix_web_search
            self._code_engine.rag_search_fn = self._code_fix_rag_search
            logging.info(
                "✅ CodeExecutorEngine mit LLM verbunden "
                "(Auto-Retry + Research-Augmented Fixing aktiv)"
            )
        # ✅ SOTA: ContentClassifier (RAG ingest) gets the same LLM for
        # domain/safety verification at upload-time. Late-attach pattern —
        # the singleton picks up the LLM lazily on the next classification.
        try:
            from agent.tools import get_global_rag_store
            get_global_rag_store(llm_client=llm_client)
            logging.info(
                "✅ RAG ContentClassifier mit LLM verbunden "
                "(Web-Ingest klassifiziert jetzt Domain + Safety)"
            )
        except Exception as exc:
            logging.warning(
                "⚠️ ContentClassifier-LLM-Wiring fehlgeschlagen: %s — "
                "Web-Ingest fällt auf Prototype-only zurück.", exc
            )
        # Enhanced Privacy Handler initialisieren (mit bestehendem LLM)
        if PRIVACY_HANDLER_AVAILABLE and ChainOfThoughtPrivacyHandler is not None:
            try:
                self.privacy_handler = ChainOfThoughtPrivacyHandler(llm_client=llm_client)
                logging.info("✅ Enhanced Privacy Handler initialisiert (robuste LLM-Calls, deutsche Spracherkennung)")
            except Exception as e:
                logging.error(f"❌ Privacy Handler Initialisierung fehlgeschlagen: {e}", exc_info=True)
                self.privacy_handler = None
        else:
            logging.warning("⚠️ Privacy Handler nicht verfügbar - keine Query-Sanitization")
        
        # ✅ PHASE 2A: Initialisiere Web Search V2 Orchestrator (jetzt mit Privacy Handler)
        if WEB_SEARCH_V2_AVAILABLE and self.web_search_orchestrator:
            self.web_search_orchestrator.set_privacy_handler(self.privacy_handler)
            logging.info("✅ Web Search V2 mit Privacy Handler verbunden")
        
        # ✅ SOTA v2: Model Loader an Orchestrator weitergeben (für Query Expansion)
        if WEB_SEARCH_V2_AVAILABLE and self.web_search_orchestrator:
            try:
                from scripts.model_loader import get_model_loader
                ml = get_model_loader()
                if ml and ml.llm is not None:
                    self.web_search_orchestrator.set_model_loader(ml)
                    logging.info("✅ Web Search V2 mit Query Expander verbunden")
            except Exception as e:
                logging.debug(f"Query Expander nicht verfügbar: {e}")
    
    # ──────────────────────────────────────────────────────────────────
    # RESEARCH-AUGMENTED CODE FIXING -- thin wrappers for CodeExecutorEngine
    # ──────────────────────────────────────────────────────────────────
    
    def _code_fix_web_search(self, query: str) -> Optional[str]:
        """Web search wrapper for CodeExecutorEngine's research-augmented fixing.
        
        Calls the existing _web_search() infrastructure and returns a plain-text
        summary suitable for injection into the LLM fix prompt.
        Returns None on failure or empty results.
        """
        try:
            result = self._web_search({"query": query, "num_results": 3})
            if not result.get("success") or not result.get("results"):
                return None
            
            # Format results as plain text for the fix prompt
            parts: list[str] = []
            for i, r in enumerate(result["results"][:5], 1):
                title = r.get("title", "")
                snippet = r.get("snippet", r.get("body", ""))
                url = r.get("url", r.get("href", ""))
                # Include enriched content if available
                enriched = r.get("enriched_content", r.get("full_content", ""))
                
                entry = f"[{i}] {title}\n    {url}\n    {snippet}"
                if enriched:
                    # Truncate enriched content per result
                    entry += f"\n    Inhalt: {enriched[:600]}"
                parts.append(entry)
            
            return "\n\n".join(parts) if parts else None
            
        except Exception as e:
            logging.warning(f"[CODE-FIX-RESEARCH] Web search failed: {e}")
            return None
    
    def _code_fix_rag_search(self, query: str) -> Optional[str]:
        """RAG search wrapper for CodeExecutorEngine's research-augmented fixing.
        
        Searches the local knowledge base for relevant code patterns,
        documentation, or previous solutions. Returns plain text or None.
        """
        try:
            # Try to get the global RAG store
            from agent.tools import get_global_rag_store
            rag_store = get_global_rag_store()
            if rag_store is None:
                return None
            
            results = rag_store.search(query, k=3, min_score=0.3)
            if not results:
                return None
            
            parts: list[str] = []
            for i, chunk in enumerate(results[:3], 1):
                # Handle both dict and object results
                if isinstance(chunk, dict):
                    text = chunk.get("text", chunk.get("content", ""))
                    source = chunk.get("source", chunk.get("title", ""))
                    score = chunk.get("score", 0)
                else:
                    text = getattr(chunk, "text", getattr(chunk, "content", str(chunk)))
                    source = getattr(chunk, "source", getattr(chunk, "title", ""))
                    score = getattr(chunk, "score", 0)
                
                if text:
                    parts.append(
                        f"[RAG-{i}] (Score: {score:.2f}) {source}\n"
                        f"    {text[:500]}"
                    )
            
            return "\n\n".join(parts) if parts else None
            
        except Exception as e:
            logging.debug(f"[CODE-FIX-RESEARCH] RAG search failed: {e}")
            return None

    def _init_web_search_v2(self):
        """
        ✅ PHASE 2A: Initialisiert das modulare Web Search System.
        Wird bei __init__ aufgerufen (lazy loading).
        """
        if self.local_only_mode:
            logger.info("🔒 APP_LOCAL_ONLY aktiv: Web Search V2 wird nicht initialisiert")
            self.web_search_orchestrator = None
            return

        if not WEB_SEARCH_V2_AVAILABLE:
            logger.debug("Web Search V2 nicht verfügbar, verwende Legacy _web_search")
            return
        
        try:
            # Setup strategies (cast to base types for invariant List)
            # SOTA: Multi-engine fusion -- DuckDuckGo + Brave Search for diversity
            strategies: List[Any] = [DuckDuckGoStrategy()]  # type: ignore[possibly-undefined]
            try:
                brave = BraveSearchStrategy()  # type: ignore[possibly-undefined]
                if brave.is_available():
                    strategies.append(brave)
                    logger.info("\u2705 Brave Search Engine added (multi-engine fusion)")
                else:
                    logger.debug("Brave Search not available (BRAVE_API_KEY not set)")
            except Exception as e:
                logger.debug(f"Brave Search initialization failed: {e}")
            
            # Setup filters
            filters: List[FilterStrategy] = [  # type: ignore[possibly-undefined]
                BlacklistFilter(self.web_policy),  # type: ignore[possibly-undefined]
                # PrivacyFilter wird später hinzugefügt (wenn privacy_handler gesetzt)
            ]
            
            # Create orchestrator
            self.web_search_orchestrator = WebSearchOrchestrator(  # type: ignore[misc]
                strategies=strategies,
                enrichment=None,  # Set below after toolkit methods are available
                filters=filters
            )
            
            # ✅ SOTA v2: Wire HTML enrichment into V2 path
            try:
                html_enrichment = HTMLEnrichment(  # type: ignore[possibly-undefined]
                    fetch_callback=self._fetch_and_parse,
                    web_policy=self.web_policy,
                )
                self.web_search_orchestrator.enrichment = html_enrichment
                logger.info("✅ Web Search V2 Orchestrator initialisiert (mit HTML-Enrichment)")
            except Exception as enrich_err:
                logger.debug(f"HTML-Enrichment für V2 nicht verfügbar: {enrich_err}")
                logger.info("✅ Web Search V2 Orchestrator initialisiert (ohne Enrichment)")
        except Exception as e:
            logger.error(f"❌ Web Search V2 Initialisierung fehlgeschlagen: {e}")
            self.web_search_orchestrator = None
    
    def _init_content_extractor_v2(self):
        """
        ✅ PHASE 2D: Initialisiert das modulare Content Extraction System.
        Wird bei __init__ aufgerufen (lazy loading).
        """
        if not CONTENT_EXTRACTOR_V2_AVAILABLE:
            logger.debug("Content Extractor V2 nicht verfügbar, verwende Legacy _modern_content_extract")
            return
        
        try:
            from content_extractor import create_default_orchestrator
            self.content_extractor_orchestrator = create_default_orchestrator()
            logger.info("✅ Content Extractor V2 Orchestrator initialisiert")
        except Exception as e:
            logger.error(f"❌ Content Extractor V2 Initialisierung fehlgeschlagen: {e}")
            self.content_extractor_orchestrator = None
    
    def _ensure_web_policy(self) -> WebFetchPolicy:
        """Stellt sicher, dass web_policy verfügbar ist. Wirft Exception wenn nicht."""
        if self.web_policy is None:
            raise RuntimeError("WebFetchPolicy wurde nicht initialisiert oder wurde bereits freigegeben")
        return self.web_policy
    
    def cleanup(self):
        """
        Explizites Cleanup aller Ressourcen zur Vermeidung von Memory Leaks.
        """
        try:
            # Schließe Web Policy DB-Connection
            if hasattr(self, 'web_policy') and self.web_policy is not None:
                try:
                    # Falls close() Methode existiert
                    if hasattr(self.web_policy, 'close'):
                        self._ensure_web_policy().close()  # type: ignore
                except Exception as e:
                    logging.debug(f"⚠️ Fehler beim Schließen der WebPolicy: {e}")
                self.web_policy = None
            
            # Schließe Python Session
            if hasattr(self, 'python_session') and self.python_session is not None:
                self.python_session = None
            
            # Privacy Handler freigeben
            if hasattr(self, 'privacy_handler') and self.privacy_handler is not None:
                self.privacy_handler = None
            
            # LLM Client Reference freigeben
            if hasattr(self, '_llm_client'):
                self._llm_client = None
            
            # Execution Log begrenzen (falls doch noch verwendet)
            if hasattr(self, 'execution_log') and hasattr(self, 'MAX_LOG_SIZE'):
                if len(self.execution_log) > self.MAX_LOG_SIZE:
                    self.execution_log = self.execution_log[-self.MAX_LOG_SIZE:]
            
            logging.debug("✅ AgentToolkit cleanup abgeschlossen")
            
        except Exception as e:
            logging.error(f"⚠️ Fehler beim AgentToolkit cleanup: {e}")
    
    def _initialize_tools(self) -> Dict[str, Dict]:
        """Definiert verfügbare Tools -- kanonische Quelle: agent/tool_schemas.py

        Shared tools (web_search, rag_search, calculator, code_executor,
        file_reader, file_writer, create_diagram, canvas) kommen aus tool_schemas.py.
        Toolkit-spezifische Tools (image_info, session_manager) werden hier
        ergänzt.  Beschreibungen können per Override erweitert werden.
        """
        # Basis: kanonische Schemas (OpenAI → Toolkit-Format)
        tools = get_toolkit_format_schemas()

        # Toolkit-spezifische Tool-Erweiterungen (nicht im ReAct-Agent)
        tools["image_info"] = {
            "description": (
                "Inspiziert eine lokale Bilddatei. Zwei Modi: "
                "'info' liefert Format/Dimensionen/Modus/EXIF-Subset/Größe (Pillow-basiert). "
                "'describe' nutzt das Vision-LLM um den Bildinhalt sachlich zu beschreiben."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "image_path": {"type": "string", "description": "Pfad zum Bild (innerhalb der Sandbox)"},
                    "mode": {
                        "type": "string",
                        "enum": ["info", "describe"],
                        "default": "info",
                        "description": "'info' = technische Metadaten, 'describe' = Vision-LLM-Beschreibung",
                    },
                    "prompt": {
                        "type": "string",
                        "description": "Optionaler Prompt für den 'describe'-Modus (sonst Default-Prompt).",
                    },
                },
                "required": ["image_path"],
            },
        }
        tools["session_manager"] = {
            "description": "Verwaltet persistent Python session (Variablen anzeigen, Session zurücksetzen)",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["show_vars", "clear_session", "session_info"],
                        "description": "Aktion: show_vars, clear_session, session_info"
                    }
                },
                "required": ["action"]
            }
        }

        # Toolkit-spezifische Enrichment-Params für web_search
        if "web_search" in tools:
            ws_params = tools["web_search"]["parameters"]["properties"]
            ws_params.setdefault("enrich", {
                "type": "boolean", "default": False,
                "description": "Ergebnisse per HTTP abrufen und Metadaten/Snippet extrahieren"
            })
            ws_params.setdefault("fetch_top", {
                "type": "integer", "default": 1,
                "description": "Wie viele Top-Ergebnisse sollen angereichert werden"
            })
            ws_params.setdefault("timeout", {
                "type": "integer", "default": 6,
                "description": "Timeout für HTTP Abruf (Sekunden)"
            })

        # ROOT-CAUSE FIX: Ohne konkrete, reale Pfade halluziniert das LLM
        # Unix-Konventionen (z.B. "/home/user/llms"), die auf Windows nie
        # existieren können -- die Sandbox lehnt sie ab, das Tool schlägt
        # fehl. Die tatsächlich erlaubten Windows-Basisverzeichnisse werden
        # hier dynamisch in die Tool-Beschreibung injiziert, statt eines
        # statischen (u.U. irreführenden) Beispielpfads.
        try:
            allowed_dirs = self.path_sandbox.list_base_dirs()
        except Exception:
            allowed_dirs = []
        if allowed_dirs:
            allowed_dirs_hint = (
                " Diese Windows-Verzeichnisse sind erlaubt (Sandbox-Basis, "
                "NIEMALS Unix-Pfade wie '/home/...' verwenden): "
                + ", ".join(allowed_dirs) + "."
            )
            for fs_tool in ("list_directory", "search_files"):
                if fs_tool in tools:
                    tools[fs_tool]["description"] += allowed_dirs_hint

        # code_executor: Toolkit benötigt use_persistent Param
        if "code_executor" in tools:
            ce_params = tools["code_executor"]["parameters"]["properties"]
            ce_params.setdefault("use_persistent", {
                "type": "boolean", "default": False,
                "description": "Persistent session nutzen (experimentell)"
            })

        # rag_search nicht im Toolkit-Execution-Pfad entfernen (wird über ToolManager bedient)
        tools.pop("rag_search", None)

        return tools
    
    def get_tools_prompt(self) -> str:
        """Erstellt Prompt mit verfügbaren Tools für das LLM"""
        tools_desc = "Du hast Zugriff auf folgende Tools:\n\n"
        
        for tool_name, tool_info in self.tools.items():
            tools_desc += f"**{tool_name}**: {tool_info['description']}\n"
            params = tool_info['parameters']['properties']
            required = tool_info['parameters'].get('required', [])
            
            tools_desc += f"Parameter: "
            for param, details in params.items():
                req_marker = " (erforderlich)" if param in required else ""
                tools_desc += f"{param}{req_marker}, "
            tools_desc = tools_desc.rstrip(", ") + "\n\n"
        
        tools_desc += """
Um ein Tool zu verwenden, antworte im folgenden JSON-Format:
{
    "tool_calls": [
        {
            "tool": "tool_name",
            "parameters": {
                "param1": "value1",
                "param2": "value2"
            }
        }
    ],
    "reasoning": "Warum verwendest du diese Tools?"
}

Nach der Tool-Ausführung bekommst du die Ergebnisse und kannst weitere Tools verwenden oder eine finale Antwort geben.
"""
        return tools_desc

    def _build_allowlist_permission_request(self, tool_name: str, parameters: Dict[str, Any]) -> Dict[str, Any]:
        """Build a structured permission-request payload for sandbox-denied paths."""
        requested_path = ""
        for key in ("file_path", "path", "root_path", "image_path"):
            value = parameters.get(key)
            if isinstance(value, str) and value.strip():
                requested_path = value.strip()
                break

        payload = {
            "needs_user_permission": True,
            "permission_action": "allowlist_extend_or_temp_grant",
            "requested_path": requested_path,
            "tool": tool_name,
            "ingest_policy": "stream_only_no_rag",
            "suggested_user_prompt": (
                "Ich habe keinen Zugriff auf diesen Pfad (Sandbox/Allowlist). "
                "Soll ich dich um eine Erweiterung der Allowlist oder eine temporäre Freigabe bitten?"
            ),
        }
        self._pending_permission_request = dict(payload)
        return payload
    
    def execute_tool(self, tool_name: str, parameters: Dict[str, Any]) -> Dict[str, Any]:
        """Führt ein Tool aus und gibt das Ergebnis zurück.

        Dispatch über Mapping (statt if/elif-Kette). Strukturierte Fehler
        werden direkt durchgereicht; nur Implementierungs-Bugs der Tool-Methode
        selbst werden hier abgefangen und mit Stacktrace geloggt.
        """
        self._refresh_runtime_mode()

        dispatch = {
            "web_search": self._web_search,
            "calculator": self._calculator,
            "file_reader": self._file_reader,
            "file_writer": self._file_writer,
            # SOTA Filesystem-Connector (2026)
            "list_directory": self._list_directory,
            "search_files": self._search_files,
            "code_executor": self._code_executor,
            "image_info": self._image_info,
            "session_manager": self._session_manager,
            "create_diagram": self._create_diagram,
            "canvas": self._canvas,
            "pdf_extract": self._pdf_extract,
            "finance_list_accounts": self._finance_list_accounts,
            "finance_get_schema_context": self._finance_get_schema_context,
            "finance_sql_query": self._finance_sql_query,
            "finance_search_transactions": self._finance_search_transactions,
            "finance_query_transactions": self._finance_query_transactions,
            "finance_aggregate": self._finance_aggregate,
            "finance_sum_counterparty_costs": self._finance_sum_counterparty_costs,
            "finance_sum_category_costs": self._finance_sum_category_costs,
            "finance_cost_structure_analysis": self._finance_cost_structure_analysis,
            "finance_recurring_expense_analysis": self._finance_recurring_expense_analysis,
            "finance_expense_forecast": self._finance_expense_forecast,
            "finance_expense_anomaly_detection": self._finance_expense_anomaly_detection,
            "finance_top_counterparty_expenses": self._finance_top_counterparty_expenses,
            "finance_balance_at": self._finance_balance_at,
            "finance_list_categories": self._finance_list_categories,
            "finance_assign_category": self._finance_assign_category,
            "finance_suggest_categories": self._finance_suggest_categories,
            "finance_list_rules": self._finance_list_rules,
            "finance_apply_rules": self._finance_apply_rules,
            "finance_set_budget": self._finance_set_budget,
            "finance_budget_status": self._finance_budget_status,
            "finance_budget_vs_actual_analysis": self._finance_budget_vs_actual_analysis,
            "finance_savings_potential_analysis": self._finance_savings_potential_analysis,
            "finance_expense_trend_break_detection": self._finance_expense_trend_break_detection,
            "finance_monthly_report": self._finance_monthly_report,
            "finance_list_transfer_candidates": self._finance_list_transfer_candidates,
            "finance_link_transfer": self._finance_link_transfer,
            "finance_unlink_transfer": self._finance_unlink_transfer,
            "finance_list_transfer_links": self._finance_list_transfer_links,
            "finance_detect_statement_settlement_gaps": self._finance_detect_statement_settlement_gaps,
            "finance_list_statements_with_incomplete_balances": self._finance_list_statements_with_incomplete_balances,
            "finance_check_statement_import_completeness": self._finance_check_statement_import_completeness,
            "finance_repair_statement_header": self._finance_repair_statement_header,
            "finance_relink_transfers": self._finance_relink_transfers,
        }
        handler = dispatch.get(tool_name)
        if handler is None:
            return {"success": False, "error": f"Unbekanntes Tool: {tool_name}", "error_class": "unknown_tool"}

        # SOTA 2026-08-21: Tool-Param-Mismatch Guard
        # web_search-Parameter (q/num/query/num_results) an Dateisystem-Tools
        # → strukturierte Korrektur statt leiser Sandbox-Fehlermeldung.
        # Das LLM bekommt suggested_tool + suggested_parameters und kann
        # in der nächsten Iteration web_search korrekt aufrufen.
        if isinstance(parameters, dict):
            suggested = suggest_web_search_for_fs_misuse(tool_name, parameters)
            if suggested is not None:
                _expected = {
                    "list_directory": "path",
                    "search_files": "root_path + pattern",
                    "file_reader": "file_path",
                }.get(tool_name, "path")
                _num_part = (
                    f", num_results={suggested['num_results']}"
                    if suggested.get("num_results")
                    else ""
                )
                logging.warning(
                    "AgentToolkit.execute_tool: Tool-Param-Mismatch: %s(keys=%s) "
                    "→ suggested web_search(%s)",
                    tool_name, sorted(parameters.keys()), suggested,
                )
                return {
                    "success": False,
                    "error_class": "tool_param_mismatch",
                    "error": (
                        f"'{tool_name}' erwartet einen lokalen Pfad ({_expected}), "
                        f"aber es wurden Websuch-Parameter übergeben "
                        f"({sorted(str(k) for k in parameters.keys())}). "
                        "Das ist KEINE Dateisystem-Operation. "
                        f"RUFE JETZT web_search AUF mit: query={suggested.get('query', '')!r}"
                        f"{_num_part}. "
                        "Wiederhole NICHT den fehlerhaften Aufruf."
                    ),
                    "suggested_tool": "web_search",
                    "suggested_parameters": suggested,
                    "action": "call_suggested_tool",
                }

        try:
            return handler(parameters)
        except PathSandboxError as exc:
            logging.warning(f"🔒 Sandbox-Verletzung in {tool_name}: {exc}")
            payload = self._build_allowlist_permission_request(tool_name, parameters)
            return {
                "success": False,
                "error": str(exc),
                "error_class": "sandbox_violation",
                **payload,
            }
        except Exception as exc:
            import traceback
            logging.error(f"Tool {tool_name} unerwarteter Fehler: {type(exc).__name__}: {exc}")
            logging.debug(f"Traceback:\n{traceback.format_exc()}")
            return {"success": False, "error": str(exc), "error_class": type(exc).__name__}
    
    # =========================================================================
    # 🎯 SMART QUERY DETECTION: Erkennt faktische Queries die Enrichment brauchen
    # =========================================================================
    
    # Patterns für Queries die spezifische Fakten aus Webseiten brauchen
    _FACTUAL_QUERY_PATTERNS: List[str] = [
        # Öffnungszeiten / Opening hours
        "öffnungszeit", "opening hour", "geöffnet", "offen von", "offen bis",
        "geschlossen", "wann auf", "wann offen", "wann geöffnet",
        # Adressen / Standorte
        "adresse", "standort", "wo ist", "wo befindet", "wo liegt",
        "anfahrt", "wegbeschreibung", "in der nähe",
        # Preise / Kosten
        "preis", "kosten", "wie teuer", "was kostet", "gebühr",
        "tarif", "preisliste", "menu", "menü", "speisekarte",
        # Kontakt
        "telefon", "nummer", "email", "e-mail", "kontakt",
        "anruf", "erreichbar", "hotline",
        # Spezifische Fakten
        "wie hoch", "wie lang", "wie weit", "wie schwer", "wie groß",
        "wie viel", "wieviel", "anzahl", "einwohner",
        # Veranstaltungen / Events
        "programm", "veranstaltung", "event", "termin", "datum",
        "spielzeit", "vorstellung", "konzert", "aufführung",
        # Rezensionen / Bewertungen  
        "bewertung", "review", "erfahrung", "empfehlung",
        # Restaurant / Geschäft spezifisch
        "reservier", "tisch", "platz", "lieferung", "bestell",
        "takeaway", "take away", "abhol",
    ]
    
    def _is_factual_detail_query(self, query: str) -> bool:
        """
        🎯 Erkennt ob eine Query spezifische Fakten benötigt, die NUR auf der
        Webseite selbst stehen (nicht in DuckDuckGo-Snippets).
        
        Beispiele:
        - "Öffnungszeiten von Dieci in Bern" → True (braucht Seiteninhalt)
        - "Was ist Machine Learning?" → False (Snippet reicht)
        - "Telefonnummer Migros Basel" → True (braucht Seiteninhalt)
        """
        q_lower = query.lower()
        # Normalisiere Umlaute: oe→ö, ae→ä, ue→ü (häufig bei Tastatur ohne Umlaute)
        q_normalized = (
            q_lower
            .replace("oe", "ö").replace("ae", "ä").replace("ue", "ü")
        )
        return any(
            pattern in q_lower or pattern in q_normalized
            for pattern in self._FACTUAL_QUERY_PATTERNS
        )
    
    def _web_search(self, params: Dict) -> Dict:
        """
        DuckDuckGo News/Websuche mit optionaler Anreicherung per HTML-Parsing (robust, mit Fallback).
        
        ✅ PHASE 2A: Nutzt modulares Web Search V2 System wenn verfügbar,
        sonst Legacy-Implementation (für Backward Compatibility).
        
        ✅ SOTA: Cross-Encoder Reranking in beiden Pfaden (V2 + Legacy).
        
        ✅ 2026-02-20: Smart Auto-Enrichment für faktische Queries
        (Öffnungszeiten, Preise, Adressen, etc.) -- DuckDuckGo-Snippets
        enthalten diese Details fast nie, daher MUSS der Seiteninhalt
        automatisch abgerufen werden.
        """
        self._refresh_runtime_mode()

        if self.local_only_mode:
            return {
                "success": False,
                "error": (
                    "APP_LOCAL_ONLY aktiv: web_search ist deaktiviert. "
                    "Nutze lokale RAG/Dateiquellen statt Internet-Suche."
                ),
                "error_class": "local_only_blocked",
                "results": [],
            }

        # 🎯 WICHTIG: Prüfe factual ZUERST, BEVOR V2 aufgerufen wird
        query = (params.get("query") or "").strip()
        is_factual = self._is_factual_detail_query(query) if query else False
        
        # ✅ Für faktische Queries: Enrichment-Flag in params setzen, damit V2 es nutzen kann
        if is_factual:
            params = {**params, "enrich": True, "fetch_top": params.get("fetch_top", 3)}
        
        # ✅ PHASE 2A: Route to V2 if available (jetzt auch für faktische Queries mit Enrichment)
        if WEB_SEARCH_V2_AVAILABLE and self.web_search_orchestrator:
            try:
                logger.debug(f"🔄 Using Web Search V2 for: '{query[:50]}' (factual={is_factual})")
                v2_result: Dict[Any, Any] = self.web_search_orchestrator.search(params)
                return v2_result
            except Exception as e:
                logger.warning(f"⚠️ Web Search V2 failed, falling back to Legacy: {e}")
                # Fall through to legacy implementation
        
        if is_factual:
            logger.info(f"🎯 FACTUAL QUERY → Legacy mit Auto-Enrichment: '{query[:60]}'")
        
        # ⚠️ LEGACY IMPLEMENTATION (CC: 95, wird durch V2 ersetzt)
        # This code is kept for backward compatibility but should be phased out
        logger.debug(f"🔄 Using Legacy Web Search for: '{query[:50]}'")
        
        # query und is_factual sind bereits oben gesetzt
        if not query:
            return {"success": False, "error": "Leere Suchanfrage"}
        
        if is_factual:
            logger.info(f"🎯 FACTUAL QUERY erkannt: '{query[:80]}' → Auto-Enrichment + mehr Ergebnisse aktiviert")
        
        try:
            # Für faktische Queries: mindestens 5 Ergebnisse (statt Standard 3)
            default_num = 5 if is_factual else 3
            num_results = max(1, int(params.get("num_results", default_num)))
        except (ValueError, TypeError) as e:
            logging.debug(f"Ungültiger num_results Wert: {e}, verwende Standard")
            num_results = 5 if is_factual else 3
        except Exception as e:
            logging.warning(f"Unerwarteter Fehler beim Parsen von num_results: {type(e).__name__}: {e}")
            num_results = 5 if is_factual else 3

        # 🔒 DATENSCHUTZ: Validiere Query-Länge und Inhalt
        if len(query) > 1000:
            logger.error(f"🚨 DATENSCHUTZ-FEHLER: Web-Search Query zu lang ({len(query)} Zeichen)! Möglicherweise wurde der gesamte Prompt übergeben!")
            query = query[:200]  # Kürze auf sinnvolle Länge
            logger.warning(f"🔧 Query gekürzt auf: '{query}'")
        
        if any(marker in query for marker in ["SYSTEM:", "ASSISTANT:", "USER:", "CONTEXT:"]):
            logger.error(f"🚨 DATENSCHUTZ-FEHLER: System-Prompt-Marker in Query entdeckt! Query wird NICHT ausgeführt!")
            return {"success": False, "error": "Ungültige Query (enthält System-Prompts)"}

        # 🔒 DATENSCHUTZ: Query VORHER bereinigen (nur wenn Privacy Handler geladen)
        # ✅ 2026-02-20: Bei faktischen Queries (Öffnungszeiten, Adressen, etc.)
        # ist Privacy-Sanitization KONTRAPRODUKTIV -- Geschäftsnamen und Ortsnamen
        # sind KEINE privaten Daten und dürfen NICHT entfernt werden!
        original_query = query
        if is_factual:
            logger.info(f"🎯 FACTUAL QUERY: Privacy-Sanitization ÜBERSPRUNGEN (Geschäftsnamen/Orte sind keine privaten Daten)")
        elif self.privacy_handler is not None:
            query = self.privacy_handler.extract_safe_query_for_web_search(query)
            if query != original_query:
                logger.info(f"🔒 Websuch-Query bereinigt: '{original_query[:50]}...' → '{query[:50]}...'")
        else:
            logger.warning("⚠️ Privacy Handler nicht initialisiert - Query nicht bereinigt")
        
        # 🌍 NEUE INTELLIGENTE SPRACH-ERKENNUNG
        detected_accept_language = self._detect_query_language(query)

        # Optional settings
        region = (params.get("region") or "de-de").lower()
        timelimit = params.get("timelimit")  # e.g., "d", "w", "m", "y"
        safesearch = params.get("safesearch") or "Moderate"  # On|Moderate|Off
        
        # ✅ 2026-02-20: Auto-Enrichment für faktische Queries
        enrich_flag = bool(
            params.get("enrich", False) 
            or os.getenv("WEB_FETCH_ENRICH", "").lower() in {"1","true","yes","on"}
            or is_factual  # 🎯 Automatisch für Öffnungszeiten, Preise, Adressen, etc.
        )
        
        # NEW: AI-enhanced content extraction flag (2025 upgrade)
        ai_extract = bool(params.get("ai_extract", False) or os.getenv("WEB_AI_EXTRACT", "").lower() in {"1","true","yes","on"})
        
        try:
            # Für faktische Queries: Top 3 Seiten abrufen (statt Standard 1)
            default_fetch_top = 3 if is_factual else 1
            fetch_top = max(0, int(params.get("fetch_top", os.getenv("WEB_FETCH_TOP", str(default_fetch_top)))))
        except (ValueError, TypeError) as e:
            logging.debug(f"Ungültiger fetch_top Wert: {e}, verwende Standard")
            fetch_top = 3 if is_factual else 1
        except Exception as e:
            logging.warning(f"Unerwarteter Fehler beim Parsen von fetch_top: {type(e).__name__}: {e}")
            fetch_top = 3 if is_factual else 1
        try:
            # Für faktische Queries: etwas mehr Timeout für Seitenabruf
            default_timeout = 10 if is_factual else 6
            timeout = max(2, int(params.get("timeout", os.getenv("WEB_FETCH_TIMEOUT", str(default_timeout)))))
        except (ValueError, TypeError) as e:
            logging.debug(f"Ungültiger timeout Wert: {e}, verwende Standard")
            timeout = 10 if is_factual else 6
        except Exception as e:
            logging.warning(f"Unerwarteter Fehler beim Parsen von timeout: {type(e).__name__}: {e}")
            timeout = 10 if is_factual else 6

        results: List[Dict[str, Any]] = []
        prefer_news = any(k in query.lower() for k in [
            "news", "aktuell", "aktuelle", "heute", "gestern", "latest", "breaking", "nachrichten"
        ])

        ddg_exc: Optional[str] = None
        try:
            from ddgs import DDGS  # type: ignore

            with DDGS() as ddgs:
                # Try news first when appropriate, but don't fail hard
                if prefer_news:
                    try:
                        for item in ddgs.news(query, max_results=num_results, region=region, safesearch=safesearch, timelimit=timelimit):
                            url = item.get("url") or item.get("link") or item.get("href") or ""
                            results.append({
                                "title": item.get("title") or "",
                                "url": url,
                                "snippet": item.get("body") or item.get("excerpt") or item.get("source") or "",
                                "date": item.get("date") or item.get("published") or item.get("pubDate") or ""
                            })
                    except (ConnectionError, TimeoutError) as e:
                        logging.warning(f"DuckDuckGo News-Verbindungsfehler für '{query}': {e}")
                        ddg_exc = f"News-Verbindungsfehler: {e}"
                        # fall through to text
                    except (ValueError, TypeError) as e:
                        logging.warning(f"DuckDuckGo News-Eingabefehler für '{query}': {e}")
                        ddg_exc = f"News-Eingabefehler: {e}"
                        # fall through to text
                    except RuntimeError as e:
                        # 🔧 FIX: DDGS Executor-Shutdown-Problem (News)
                        if "cannot schedule new futures after shutdown" in str(e):
                            logging.warning(f"🔄 DDGS Executor shutdown in News for '{query}', will fall through to text search...")
                            ddg_exc = f"Executor shutdown in News (will try text)"
                        else:
                            logging.error(f"DuckDuckGo News RuntimeError für '{query}': {e}")
                            ddg_exc = str(e)
                        # fall through to text
                    except Exception as e:
                        logging.error(f"DuckDuckGo News unerwarteter Fehler für '{query}': {type(e).__name__}: {e}")
                        ddg_exc = str(e)
                        # fall through to text

                if not results:
                    try:
                        for item in ddgs.text(query, max_results=num_results, region=region, safesearch=safesearch, timelimit=timelimit):
                            url = item.get("href") or item.get("url") or ""
                            results.append({
                                "title": item.get("title") or "",
                                "url": url,
                                "snippet": item.get("body") or item.get("excerpt") or ""
                            })
                    except (ConnectionError, TimeoutError) as e2:
                        logging.warning(f"DuckDuckGo Text-Verbindungsfehler für '{query}': {e2}")
                        ddg_exc = f"{ddg_exc or ''} | Text-Verbindungsfehler: {e2}"
                    except (ValueError, TypeError) as e2:
                        logging.warning(f"DuckDuckGo Text-Eingabefehler für '{query}': {e2}")
                        ddg_exc = f"{ddg_exc or ''} | Text-Eingabefehler: {e2}"
                    except RuntimeError as e2:
                        # 🔧 FIX: DDGS Executor-Shutdown-Problem
                        if "cannot schedule new futures after shutdown" in str(e2):
                            logging.warning(f"🔄 DDGS Executor shutdown detected for '{query}', retrying with fresh instance...")
                            try:
                                # Retry mit FRESH DDGS instance
                                import time
                                time.sleep(0.3)  # Kurze Pause
                                
                                with DDGS() as ddgs_retry:
                                    for item in ddgs_retry.text(query, max_results=num_results, region=region, safesearch=safesearch, timelimit=timelimit):
                                        url = item.get("href") or item.get("url") or ""
                                        results.append({
                                            "title": item.get("title") or "",
                                            "url": url,
                                            "snippet": item.get("body") or item.get("excerpt") or ""
                                        })
                                logging.info(f"✅ DDGS Retry successful for '{query[:50]}...'")
                            except Exception as retry_err:
                                logging.error(f"❌ DDGS Retry failed for '{query}': {retry_err}")
                                ddg_exc = f"{ddg_exc or ''} | Executor shutdown, retry failed: {retry_err}"
                        else:
                            # Andere RuntimeErrors normal behandeln
                            logging.error(f"DuckDuckGo Text RuntimeError für '{query}': {e2}")
                            ddg_exc = f"{ddg_exc or ''} | RuntimeError: {e2}"
                    except Exception as e2:
                        logging.error(f"DuckDuckGo Text unerwarteter Fehler für '{query}': {type(e2).__name__}: {e2}")
                        ddg_exc = f"{ddg_exc or ''} | {e2}"
        except (ConnectionError, TimeoutError) as outer_e:
            logging.error(f"DuckDuckGo-Service-Verbindungsfehler für '{query}': {outer_e}")
            ddg_exc = f"Service nicht erreichbar: {outer_e}"
        except (ImportError, ModuleNotFoundError) as outer_e:
            logging.error(f"DuckDuckGo-Module-Fehler: {outer_e}")
            ddg_exc = f"DuckDuckGo-Module nicht verfügbar: {outer_e}"
        except Exception as outer_e:
            logging.error(f"DuckDuckGo kritischer Fehler für '{query}': {type(outer_e).__name__}: {outer_e}")
            import traceback
            logging.debug(f"DuckDuckGo-Fehler Traceback:\n{traceback.format_exc()}")
            ddg_exc = str(outer_e)

        # Apply blacklist policy before returning
        filtered: List[Dict[str, Any]] = []
        for item in results:
            url = (item.get("url") or "").strip()
            if not url:
                continue
            decision: FetchDecision = self._ensure_web_policy().should_fetch(url)
            if not decision.allow:
                # Skip currently blacklisted URLs
                item["filtered_reason"] = decision.reason
                item["retry_at_unix"] = decision.retry_at_unix
                continue
            # Probe reachability in the background (quick HEAD) and record to policy; don't block or drop on failure now
            probe_ok, probe_err, probe_status = self._probe_and_record(url)
            if not probe_ok:
                item["probe_error_class"] = probe_err
                item["probe_status"] = probe_status
            else:
                item["probe_status"] = probe_status
            filtered.append(item)

        # Optional enrichment: fetch and parse top-N results (best-effort)
        if enrich_flag and filtered:
            to_enrich = filtered[: min(fetch_top or num_results, len(filtered))]
            enrich_success_count = 0
            structured_data_found = False  # Track if SOTA extraction succeeded
            for it in to_enrich:
                u = (it.get("url") or "").strip()
                if not u:
                    continue
                
                # Use AI-enhanced extraction if enabled, otherwise standard parsing
                if ai_extract:
                    enriched = self._ai_extract_content(u, timeout=timeout, query=query, accept_language=detected_accept_language)
                else:
                    enriched = self._fetch_and_parse(u, timeout=timeout, query=query, accept_language=detected_accept_language)
                    
                if enriched.get("success"):
                    enrich_success_count += 1
                    meta = it.get("metadata") or {}
                    meta.update({
                        "canonical_url": enriched.get("canonical_url") or u,
                        "domain": enriched.get("domain"),
                        "detected_lang": enriched.get("language"),
                        "content_length": enriched.get("text_len"),
                        "og": enriched.get("og", {}),
                        "meta": enriched.get("meta", {}),
                    })
                    it["metadata"] = meta
                    if enriched.get("title") and not it.get("title"):
                        it["title"] = enriched.get("title")

                    # ✅ SOTA 2026: Hybrid Structured Data Extraction
                    # Schema.org/JSON-LD → Pattern-Finder → LLM-Fallback
                    if is_factual and STRUCTURED_EXTRACTOR_AVAILABLE and not structured_data_found:
                        raw_html = enriched.get("_raw_html") or ""
                        if not raw_html:
                            # Fetch raw HTML if not cached in enriched result
                            try:
                                _, raw_html, _ = self._http_get(u, timeout=timeout, accept_language=detected_accept_language)
                            except Exception:
                                raw_html = ""
                        
                        if raw_html:
                            try:
                                struct_result = extract_structured_data(  # type: ignore[misc]
                                    html=raw_html,
                                    query=original_query,
                                    url=u,
                                    model_loader=getattr(self, '_llm_client', None),
                                )
                                if struct_result.get("success") and struct_result.get("data"):
                                    structured_data_found = True
                                    extracted_text = struct_result["data"]
                                    method = struct_result.get("method", "unknown")
                                    
                                    # Prepend structured data to snippet for maximum visibility
                                    existing_snippet = it.get("snippet") or ""
                                    it["snippet"] = f"📋 EXTRAHIERTE DATEN ({method}):\n{extracted_text}\n\n---\n{existing_snippet}"
                                    it["structured_data"] = struct_result.get("structured", {})
                                    meta["extraction_method"] = method
                                    it["metadata"] = meta
                                    
                                    logger.info(f"🏆 SOTA EXTRAKTION [{method}]: Strukturierte Daten gefunden für {u[:60]}")
                                    logger.info(f"   └─ Daten: {extracted_text[:200]}")
                                    continue  # Skip normal snippet replacement for this result
                            except Exception as e:
                                logger.warning(f"Structured Data Extraction Fehler für {u[:60]}: {e}")

                    # Standard enriched snippet logic (for non-factual or when SOTA extraction failed)
                    enriched_snippet = enriched.get("snippet") or ""
                    existing_snippet = it.get("snippet") or ""
                    if enriched_snippet and (
                        is_factual
                        or not existing_snippet 
                        or len(existing_snippet) < 40
                        or len(enriched_snippet) > len(existing_snippet) * 2
                    ):
                        it["snippet"] = enriched_snippet
                        if is_factual:
                            logger.info(f"🎯 FACTUAL ENRICHMENT: Snippet ersetzt für {u[:60]} ({len(enriched_snippet)} Zeichen)")
                    # normalize URL if canonical provided
                    if enriched.get("canonical_url"):
                        it["url"] = enriched.get("canonical_url")
                else:
                    # record failure in policy for adaptive backoff
                    self._ensure_web_policy().record_failure(u, status=enriched.get("status"), error_class=enriched.get("error_class"))
                    it["enrich_error"] = enriched.get("error") or enriched.get("error_class")
            
            if is_factual:
                logger.info(f"🎯 FACTUAL ENRICHMENT SUMMARY: {enrich_success_count}/{len(to_enrich)} Seiten, SOTA-Extraktion: {'✅' if structured_data_found else '❌'}")

        # --- NEW: AI-enhanced content extraction (2025 upgrade) ---
        if ai_extract and filtered:
            to_extract = filtered[: min(fetch_top or num_results, len(filtered))]
            for it in to_extract:
                u = (it.get("url") or "").strip()
                if not u:
                    continue
                ai_enriched = self._ai_extract_content(u, timeout=timeout)
                if ai_enriched.get("success"):
                    meta = it.get("metadata") or {}
                    meta.update({
                        "ai_summary": ai_enriched.get("summary"),
                        "ai_keywords": ai_enriched.get("keywords"),
                    })
                    it["metadata"] = meta
                else:
                    # record failure in policy for adaptive backoff
                    self._ensure_web_policy().record_failure(u, status=ai_enriched.get("status"), error_class=ai_enriched.get("error_class"))
                    it["ai_extract_error"] = ai_enriched.get("error") or ai_enriched.get("error_class")

        if not filtered:
            return {
                "success": True,
                "query": query,
                "results": [],
                "message": f"Keine Ergebnisse für '{query}' (alle gefiltert)",
                "error": ddg_exc
            }

        # 🏆 SOTA: Cross-Encoder Reranking (Legacy Path)
        try:
            from agent.reranker import get_reranker as _get_web_reranker
            _reranker = _get_web_reranker()
            if _reranker and _reranker.is_available:
                filtered = _reranker.rerank_web_results(
                    query=original_query,
                    results=filtered,
                    top_k=num_results,
                )
                logger.debug(f"🏆 Legacy web results reranked for '{original_query[:50]}'")
        except Exception as e:
            logger.debug(f"Reranking skipped (legacy path): {e}")

        return {
            "success": True,
            "query": query,
            "results": filtered[:num_results],
            "message": f"Gefunden: {len(filtered[:num_results])} Ergebnisse für '{query}'"
        }

    # --- Web policy helpers ---
    def _probe_and_record(self, url: str) -> tuple[bool, Optional[str], Optional[int]]:
        """Quick HEAD probe to classify availability and update the web policy. Returns (ok, error_class, status)."""
        status, retry_after = None, None
        try:
            status, headers = self._do_head(url, timeout=4)
            # Treat 405 (method not allowed for HEAD) as success because GET would likely work
            if status and (200 <= status < 400 or status == 405):
                self._ensure_web_policy().record_success(url)
                return True, None, status
            if status == 429:
                try:
                    ra = headers.get("Retry-After") if headers else None
                    retry_after = int(ra) if ra and str(ra).isdigit() else None
                except (ValueError, TypeError) as e:
                    logging.debug(f"Ungültiger Retry-After Header: {e}")
                    retry_after = None
                except Exception as e:
                    logging.warning(f"Unerwarteter Fehler beim Parsen von Retry-After: {type(e).__name__}: {e}")
                    retry_after = None
                    retry_after = None
                self._ensure_web_policy().record_failure(url, status=status, error_class="http_429", retry_after_seconds=retry_after)
                return False, "http_429", status
            if status in (404, 410):
                self._ensure_web_policy().record_failure(url, status=status, error_class="http_4xx")
                return False, "http_4xx", status
            if status and status >= 500:
                self._ensure_web_policy().record_failure(url, status=status, error_class="http_5xx")
                return False, "http_5xx", status
            # Other 4xx considered client/permalink issues
            if status and 400 <= status < 500:
                self._ensure_web_policy().record_failure(url, status=status, error_class="http_4xx")
                return False, "http_4xx", status
            # Fallback: mark as unknown failure
            self._ensure_web_policy().record_failure(url, status=status, error_class="unknown")
            return False, "unknown", status
        except Exception as e:
            # Classify urllib errors
            err_str = str(e).lower()
            if "timed out" in err_str or "timeout" in err_str:
                cls = "timeout"
            elif "name or service not known" in err_str or "nodename nor servname" in err_str or "dns" in err_str:
                cls = "dns"
            elif "connection refused" in err_str or "failed to establish a new connection" in err_str or "connection" in err_str:
                cls = "connect"
            else:
                cls = "unknown"
            self._ensure_web_policy().record_failure(url, status=None, error_class=cls)
            return False, cls, None

    def _do_head(self, url: str, timeout: int = 4) -> tuple[Optional[int], Optional[dict]]:
        req = urllib.request.Request(url, method="HEAD", headers={
            "User-Agent": "Mozilla/5.0 (compatible; AgentBot/1.0; +https://example.invalid/bot)",
            "Accept": "*/*",
            "Connection": "close",
        })
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                status = getattr(resp, "status", None) or getattr(resp, "code", None)
                headers = dict(resp.headers) if getattr(resp, "headers", None) is not None else {}
                return int(status) if status is not None else None, headers
        except (ConnectionError, TimeoutError, OSError) as e:
            # Network-/IO-spezifische Fehler
            try:
                if hasattr(e, "code"):
                    code = int(getattr(e, "code"))
                    headers = dict(getattr(e, "headers", {}) or {})
                    return code, headers
            except (AttributeError, ValueError, TypeError) as parse_exc:
                logging.debug(f"HTTP-Fehlercode konnte nicht aus Ausnahme extrahiert werden für {url}: {parse_exc}")
            raise
        except (ValueError, TypeError) as e:
            # HTTP-spezifische Fehler mit Code
            try:
                if hasattr(e, "code"):
                    code = int(getattr(e, "code"))
                    headers = dict(getattr(e, "headers", {}) or {})
                    return code, headers
            except (AttributeError, ValueError, TypeError) as parse_exc:
                logging.debug(f"HTTP-Fehlercode-Parsing fehlgeschlagen für {url}: {parse_exc}")
            raise
        except Exception as e:
            # HTTPError is a subclass of URLError with .code
            try:
                if hasattr(e, "code"):
                    code = int(getattr(e, "code"))
                    headers = dict(getattr(e, "headers", {}) or {})
                    return code, headers
            except (AttributeError, ValueError, TypeError) as parse_exc:
                logging.debug(f"Unerwarteter Fehlercode konnte nicht extrahiert werden für {url}: {parse_exc}")
            raise

    # --- New: HTML fetch + parse helpers ---
    def _fetch_and_parse(self, url: str, *, timeout: int = 6, query: Optional[str] = None, accept_language: Optional[str] = None) -> Dict[str, Any]:
        """Fetch a URL (GET) and parse HTML with BeautifulSoup. Returns dict with canonical_url, title, snippet, text_len, og/meta.
        Best-effort; does not raise.
        """
        try:
            # Respect policy gate again
            dec = self._ensure_web_policy().should_fetch(url)
            if not dec.allow:
                return {"success": False, "error": f"blacklisted: {dec.reason}", "error_class": dec.reason}
            # 🌍 ÜBERGEBE ACCEPT-LANGUAGE an HTTP GET
            status, html, final_url = self._http_get(url, timeout=timeout, accept_language=accept_language)
            if status is None or status >= 400:
                self._ensure_web_policy().record_failure(url, status=status, error_class="http")
                return {"success": False, "status": status, "error": f"HTTP {status}", "error_class": "http"}
            parsed = self._parse_html(final_url or url, html, query=query)
            # success => record success for policy
            self._ensure_web_policy().record_success(final_url or url)
            return {"success": True, **parsed}
        except (ConnectionError, TimeoutError) as e:
            logging.warning(f"Verbindungs-/Timeout-Fehler beim Fetchen von {url}: {e}")
            err = str(e).lower()
            cls = "timeout" if "timeout" in err else "connect"
            self._ensure_web_policy().record_failure(url, status=None, error_class=cls)
            return {"success": False, "error": str(e), "error_class": cls}
        except (ValueError, TypeError) as e:
            logging.warning(f"URL-/Eingabefehler beim Fetchen von {url}: {e}")
            cls = "url_error"
            self._ensure_web_policy().record_failure(url, status=None, error_class=cls)
            return {"success": False, "error": str(e), "error_class": cls}
        except Exception as e:
            logging.error(f"Unerwarteter Fehler beim Fetchen von {url}: {type(e).__name__}: {e}")
            import traceback
            logging.debug(f"Fetch-Fehler Traceback für {url}:\n{traceback.format_exc()}")
            err = str(e).lower()
            cls = "unknown"
            if "timeout" in err:
                cls = "timeout"
            elif "connection" in err:
                cls = "connect"
            self._ensure_web_policy().record_failure(url, status=None, error_class=cls)
            return {"success": False, "error": str(e), "error_class": cls}

    def _http_get(self, url: str, *, timeout: int = 6, accept_language: Optional[str] = None) -> tuple[Optional[int], str, Optional[str]]:
        """Minimal GET using urllib with rotating UA for better anti-bot evasion. Returns (status, html, final_url)."""
        # Use rotating user agent for 2025 best practices
        ua = self._get_rotating_user_agent()
        # Allow override via environment
        ua = os.getenv("WEB_FETCH_UA", ua)
        
        # 🌍 GENERISCHE SPRACHUNTERSTÜTZUNG: Accept-Language kann überschrieben werden
        # Standard: Neutral mit leichter Deutsch-Präferenz, aber akzeptiert ALLE Sprachen
        if accept_language is None:
            accept_language = os.getenv("WEB_FETCH_ACCEPT_LANGUAGE", 
                                       "de-DE,de;q=0.9,en-US;q=0.8,en;q=0.7,*;q=0.5")
        
        # SOTA: Encode non-ASCII URL characters (IRI → URI conversion)
        # urllib.request.Request fails with non-ASCII chars like ö, ä, ü
        try:
            from urllib.parse import urlsplit, urlunsplit, quote
            parts = urlsplit(url)
            # Only encode the path and query -- keep scheme, host, fragment as-is
            encoded_path = quote(parts.path, safe="/:@!$&'()*+,;=-._~")
            encoded_query = quote(parts.query, safe="/:@!$&'()*+,;=-._~?=")
            url = urlunsplit((parts.scheme, parts.netloc, encoded_path, encoded_query, parts.fragment))
        except (ValueError, TypeError) as exc:
            logging.debug(f"URL encoding failed for '{url}': {exc} — keeping original")
        
        req = urllib.request.Request(url, method="GET", headers={
            "User-Agent": ua,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": accept_language,
            "Connection": "close",
        })
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status = getattr(resp, "status", None) or getattr(resp, "code", None)
            final_url = getattr(resp, "url", None)
            charset = None
            try:
                ctype = resp.headers.get("Content-Type", "")
                if "charset=" in ctype:
                    charset = ctype.split("charset=")[-1].split(";")[0].strip()
            except (AttributeError, ValueError, IndexError, TypeError) as e:
                logging.debug(f"Content-Type-Parsing-Fehler für {url}: {e}")
                charset = None
            except (KeyError, OSError) as e:
                logging.debug(f"Header-Zugriffs-Fehler für {url}: {type(e).__name__}: {e}")
                charset = None
            except Exception as e:
                logging.warning(f"Unerwarteter Content-Type-Fehler für {url}: {type(e).__name__}: {e}")
                import traceback
                logging.debug(f"Content-Type-Fehler Traceback:\n{traceback.format_exc()}")
                charset = None
            raw = resp.read()
            try:
                html = raw.decode(charset or "utf-8", errors="ignore")
            except (UnicodeDecodeError, LookupError) as e:
                logging.debug(f"Charset-Dekodierungs-Fehler für {url} (charset: {charset}): {e}")
                html = raw.decode("utf-8", errors="ignore")
            except (AttributeError, TypeError) as e:
                logging.debug(f"Dekodierungs-Attribut-Fehler für {url}: {type(e).__name__}: {e}")
                html = raw.decode("utf-8", errors="ignore")
            except Exception as e:
                logging.warning(f"Unerwarteter Dekodierungs-Fehler für {url}: {type(e).__name__}: {e}")
                import traceback
                logging.debug(f"Dekodierungs-Fehler Traceback:\n{traceback.format_exc()}")
                html = raw.decode("utf-8", errors="ignore")
            return int(status) if status is not None else None, html, final_url

    def _parse_html(self, url: str, html: str, *, query: Optional[str] = None) -> Dict[str, Any]:
        """
        Parse HTML (V2 with fallback to legacy).
        
        V2 uses State Machine pattern (CC: 7) for better maintainability.
        Falls back to legacy parser (CC: 86) if V2 fails or quality is low.
        """
        # Feature flag for V2 parser (opt-in)
        use_v2 = getattr(self, 'use_v2_html_parser', True)
        
        if use_v2:
            try:
                from html_parser import parse_html_v2
                result = parse_html_v2(url, html, query=query)
                
                # Check extraction quality
                if result.get("extraction_quality", 0.0) >= 0.5:
                    return result
                else:
                    logging.debug(f"V2 parser quality too low for {url}, using legacy")
            
            except Exception as e:
                logging.warning(f"V2 HTML parser failed for {url}: {e}, falling back to legacy")
        
        # Fallback to legacy parser
        return self._parse_html_legacy(url, html, query=query)

    def _parse_html_legacy(self, url: str, html: str, *, query: Optional[str] = None) -> Dict[str, Any]:
        """
        LEGACY: Parse HTML using BeautifulSoup (CC: 86).
        
        This is the original implementation kept for fallback.
        Use _parse_html() instead (V2 with automatic fallback).
        """
        canonical_url = None
        title_final = None
        snippet = None
        og: Dict[str, Any] = {}
        meta: Dict[str, Any] = {}
        text = ""
        domain = urllib.parse.urlparse(url).netloc.lower()
        if domain.startswith("www."):
            domain = domain[4:]
        # Try using BeautifulSoup
        try:
            from bs4 import BeautifulSoup  # type: ignore
            from bs4.element import Tag  # type: ignore
            soup = BeautifulSoup(html or "", "html.parser")
            # Remove noisy elements
            for tag in soup(["script", "style", "noscript", "template", "iframe", "svg"]):
                    try:
                        tag.decompose()  # type: ignore[attr-defined]
                    except AttributeError as e:
                        logging.debug(f"Tag-Decompose-Fehler für {url}: {e}")
                        try:
                            tag.extract()  # type: ignore[attr-defined]
                        except AttributeError:
                            logging.debug(f"Tag-Extract-Fallback-Fehler für {url}")
                            pass
                    except (TypeError, ValueError) as e:
                        logging.debug(f"Tag-Cleanup-Typ-Fehler für {url}: {type(e).__name__}: {e}")
                        try:
                            tag.extract()  # type: ignore[attr-defined]
                        except Exception as extract_exc:
                            logging.debug(f"Tag-Extract nach Typ-Fehler fehlgeschlagen für {url}: {extract_exc}")
                    except Exception as e:
                        logging.debug(f"Tag-Cleanup unerwarteter Fehler für {url}: {type(e).__name__}: {e}")
                        try:
                            tag.extract()  # type: ignore[attr-defined]
                        except Exception as extract_exc:
                            logging.debug(f"Tag-Extract nach unerwartetem Cleanup-Fehler fehlgeschlagen für {url}: {extract_exc}")
            # Canonical URL: iterate links and check rel attr safely
            try:
                for l in soup.find_all("link"):
                    if not isinstance(l, Tag):
                        continue  # type: ignore[unreachable]
                    rel_val = l.get("rel")
                    rels = []
                    if isinstance(rel_val, list):
                        rels = [str(x).lower().strip() for x in rel_val if x]
                    elif rel_val:
                        rels = [str(rel_val).lower().strip()]
                    if any(r == "canonical" for r in rels):
                        href_val = l.get("href")
                        if href_val:
                            canonical_url = urllib.parse.urljoin(url, str(href_val))
                            break
            except (AttributeError, ValueError, TypeError) as e:
                logging.debug(f"Canonical-URL-Parsing-Fehler für {url}: {e}")
            except (ImportError, ModuleNotFoundError) as e:
                logging.debug(f"BeautifulSoup-Import-Fehler bei Canonical-URL-Parsing für {url}: {e}")
            except Exception as e:
                logging.warning(f"Canonical-URL unerwarteter Fehler für {url}: {type(e).__name__}: {e}")
                import traceback
                logging.debug(f"Canonical-URL-Fehler Traceback:\n{traceback.format_exc()}")
            # OpenGraph & meta
            for m in soup.find_all("meta"):
                if not isinstance(m, Tag):
                    continue  # type: ignore[unreachable]
                k_raw = m.get("property") or m.get("name") or ""
                k = str(k_raw).lower().strip()
                v_raw = m.get("content") or ""
                v = str(v_raw).strip()
                if not k or not v:
                    continue
                meta[k] = v
                if k.startswith("og:"):
                    og[k] = v
            # Title preference: h1 > og:title > <title>
            h1 = soup.find("h1")
            t_tag = soup.find("title")
            try:
                if isinstance(h1, Tag) and h1.get_text(strip=True):
                    title_final = h1.get_text(strip=True)
            except (AttributeError, TypeError) as e:
                logging.debug(f"H1-Title-Extraktion-Fehler für {url}: {e}")
                title_final = None
            except Exception as e:
                logging.debug(f"H1-Title unerwarteter Fehler für {url}: {type(e).__name__}: {e}")
                title_final = None
            if not title_final:
                title_final = og.get("og:title") or (t_tag.get_text(strip=True) if isinstance(t_tag, Tag) else None)
            # Main text from <article> or <main> else body paragraphs
            main = soup.find("article") or soup.find("main") or soup.body
            if isinstance(main, Tag):
                try:
                    paras = [p.get_text(" ", strip=True) for p in main.find_all(["p", "li"]) if isinstance(p, Tag) and p.get_text(strip=True)]
                    text = "\n".join(paras)
                except (AttributeError, ValueError) as e:
                    logging.debug(f"Text-Extraktion-Fehler für {url}: {e}")
                    text = main.get_text(" ", strip=True)
                except (TypeError, IndexError) as e:
                    logging.debug(f"Text-Extraktion-Typ-Fehler für {url}: {type(e).__name__}: {e}")
                    text = main.get_text(" ", strip=True)
                except Exception as e:
                    logging.warning(f"Text-Extraktion unerwarteter Fehler für {url}: {type(e).__name__}: {e}")
                    import traceback
                    logging.debug(f"Text-Extraktion-Fehler Traceback:\n{traceback.format_exc()}")
                    text = main.get_text(" ", strip=True)
            else:
                text = soup.get_text(" ", strip=True)
        except (ImportError, ModuleNotFoundError) as e:
            # Fallback minimal parsing ohne BeautifulSoup
            logging.debug(f"BeautifulSoup nicht verfügbar für {url}: {e}")
            try:
                start = (html or "").lower().find("<title>")
                end = (html or "").lower().find("</title>")
                if start != -1 and end != -1 and end > start:
                    title_final = (html[start+7:end] or "").strip()
            except (AttributeError, ValueError, IndexError) as e:
                logging.debug(f"Fallback-Title-Parsing-Fehler für {url}: {e}")
            except Exception as e:
                logging.debug(f"Fallback-Title unerwarteter Fehler für {url}: {type(e).__name__}: {e}")
            text = " ".join((html or "").split())
        except (AttributeError, TypeError, ValueError) as e:
            # BeautifulSoup-Parsing-Fehler
            logging.warning(f"HTML-Parsing-Fehler für {url}: {type(e).__name__}: {e}")
            try:
                start = (html or "").lower().find("<title>")
                end = (html or "").lower().find("</title>")
                if start != -1 and end != -1 and end > start:
                    title_final = (html[start+7:end] or "").strip()
            except Exception as title_exc:
                logging.debug(f"Fallback-Title-Parsing nach HTML-Fehler fehlgeschlagen für {url}: {title_exc}")
            text = " ".join((html or "").split())
        except Exception as e:
            # Fallback minimal parsing bei unerwarteten Fehlern
            logging.error(f"Unerwarteter HTML-Parsing-Fehler für {url}: {type(e).__name__}: {e}")
            import traceback
            logging.debug(f"HTML-Parsing-Fehler Traceback:\n{traceback.format_exc()}")
            try:
                start = (html or "").lower().find("<title>")
                end = (html or "").lower().find("</title>")
                if start != -1 and end != -1 and end > start:
                    title_final = (html[start+7:end] or "").strip()
            except Exception as title_exc:
                logging.debug(f"Fallback-Title-Parsing nach unerwartetem HTML-Fehler fehlgeschlagen für {url}: {title_exc}")
            text = " ".join((html or "").split())
        # Make a snippet - REPARIERT: Vollständiger Content statt 360-Zeichen Snippet
        def make_snippet(t: str, q: Optional[str]) -> str:
            t = (t or "").strip()
            if not t:
                return ""
            # REPARIERT: Erhöht von 360 auf 8000 Zeichen für RAG-System
            max_len = 8000  
            if q:
                ql = q.strip().lower()
                pos = t.lower().find(ql)
                if pos != -1:
                    start = max(0, pos - 500)  # Mehr Kontext
                    end = min(len(t), pos + len(ql) + 1500)  # Mehr Kontext
                    return (t[start:end] + ("…" if end < len(t) else "")).strip()
            return (t[:max_len] + ("…" if len(t) > max_len else "")).strip()
        snippet = make_snippet(text, query)
        # Language quick heuristic
        lang = None
        low = (text or "").lower()
        de_hits = sum(1 for w in [" der ", " die ", " das ", " und ", " ist ", " nicht ", " mit "] if w in f" {low} ")
        en_hits = sum(1 for w in [" the ", " and ", " is ", " not ", " with ", " from "] if w in f" {low} ")
        if de_hits > en_hits and de_hits >= 2:
            lang = "de"
        elif en_hits > de_hits and en_hits >= 2:
            lang = "en"
        return {
            "url": url,
            "canonical_url": canonical_url or url,
            "domain": domain,
            "title": title_final or "",
            "snippet": snippet,
            "full_text": text,  # SOTA v2.1: Full text for snippet extraction
            "text_len": len(text or ""),
            "language": lang,
            "og": og,
            "meta": meta,
        }

    def _get_rotating_user_agent(self) -> str:
        """
        2025 Best Practice: Rotating User-Agents for better anti-bot evasion
        """
        user_agents = [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Safari/605.1.15"
        ]
        import random
        return random.choice(user_agents)

    def _ai_extract_content(self, url: str, *, timeout: int = 6, query: Optional[str] = None, accept_language: Optional[str] = None) -> Dict[str, Any]:
        """
        State-of-the-art AI-enhanced content extraction using multiple modern techniques.
        Multi-stage approach with fallback for maximum content quality.
        """
        try:
            # Stage 1: Try modern content extractors first
            modern_result = self._modern_content_extract(url, timeout=timeout, query=query, accept_language=accept_language)
            if modern_result.get("success") and modern_result.get("quality_score", 0) > 0.7:
                return modern_result
            
            # Stage 2: Fallback to enhanced BeautifulSoup + LLM cleaning
            basic_result = self._fetch_and_parse(url, timeout=timeout, query=query, accept_language=accept_language)
            if not basic_result.get("success"):
                return basic_result
            
            # Stage 3: Use local LLM for content cleaning if available
            cleaned_result = self._llm_clean_content(basic_result, query=query)
            
            return cleaned_result
            
        except (ConnectionError, TimeoutError) as e:
            # Network-spezifische Fehler
            logging.warning(f"AI-Content-Extraction Netzwerk-Fehler für {url}: {type(e).__name__}: {e}")
            return self._fetch_and_parse(url, timeout=timeout, query=query, accept_language=accept_language)
        except (OSError, IOError) as e:
            # URL-/IO-spezifische Fehler (urllib.error.URLError ist IOError-Subklasse)
            logging.warning(f"AI-Content-Extraction IO-Fehler für {url}: {type(e).__name__}: {e}")
            return self._fetch_and_parse(url, timeout=timeout, query=query, accept_language=accept_language)
        except (ValueError, TypeError) as e:
            # Eingabe-/Parameter-Fehler
            logging.warning(f"AI-Content-Extraction Parameter-Fehler für {url}: {type(e).__name__}: {e}")
            return self._fetch_and_parse(url, timeout=timeout, query=query, accept_language=accept_language)
        except (ImportError, ModuleNotFoundError) as e:
            # Abhängigkeits-Fehler
            logging.debug(f"AI-Content-Extraction Import-Fehler für {url}: {e}")
            return self._fetch_and_parse(url, timeout=timeout, query=query, accept_language=accept_language)
        except Exception as e:
            # Ultimate fallback to basic extraction
            logging.error(f"Unerwarteter AI-Content-Extraction-Fehler für {url}: {type(e).__name__}: {e}")
            import traceback
            logging.debug(f"AI-Content-Extraction-Fehler Traceback:\n{traceback.format_exc()}")
            return self._fetch_and_parse(url, timeout=timeout, query=query, accept_language=accept_language)
    
    def _modern_content_extract(self, url: str, *, timeout: int = 6, query: Optional[str] = None, accept_language: Optional[str] = None) -> Dict[str, Any]:
        """
        Modern content extraction (V2 with fallback to legacy).
        
        V2 uses Strategy pattern (CC: 8) for better maintainability.
        Falls back to legacy extractor (CC: 49) if V2 fails or quality is low.
        """
        # Feature flag for V2 extractor (opt-in)
        use_v2 = getattr(self, 'use_v2_content_extractor', True)
        
        if use_v2 and self.content_extractor_orchestrator:
            try:
                result = self.content_extractor_orchestrator.extract(
                    url=url,
                    timeout=timeout,
                    query=query,
                    accept_language=accept_language
                )
                
                # Check extraction quality
                if result.get("success", False) and result.get("quality", 0.0) >= 0.4:
                    v2_extract_result: Dict[str, Any] = result
                    return v2_extract_result
                else:
                    logging.debug(f"V2 content extractor quality too low for {url}, using legacy")
            
            except Exception as e:
                logging.warning(f"V2 content extractor failed for {url}: {e}, falling back to legacy")
        
        # Fallback to legacy extractor
        return self._modern_content_extract_legacy(url, timeout=timeout, query=query, accept_language=accept_language)

    def _modern_content_extract_legacy(self, url: str, *, timeout: int = 6, query: Optional[str] = None, accept_language: Optional[str] = None) -> Dict[str, Any]:
        """
        LEGACY: Modern content extraction using state-of-the-art libraries (CC: 49).
        Priority: Trafilatura > Readability > Jina AI Reader
        
        This is the original implementation kept for fallback.
        Use _modern_content_extract() instead (V2 with automatic fallback).
        """
        try:
            # Method 1: Trafilatura (best for news/articles)
            try:
                import trafilatura
                
                # Get HTML first (mit Accept-Language)
                status, html, final_url = self._http_get(url, timeout=timeout, accept_language=accept_language)
                if status and status < 400 and html:
                    
                    # Extract clean content
                    clean_text = trafilatura.extract(
                        html,
                        include_comments=False,
                        include_tables=True,
                        include_links=False,
                        include_formatting=False,
                        output_format='txt'
                    )
                    
                    if clean_text and len(clean_text.strip()) > 100:
                        # Extract metadata
                        metadata = trafilatura.extract_metadata(html)
                        
                        quality_score = self._calculate_content_quality(clean_text, query)
                        
                        return {
                            "success": True,
                            "canonical_url": final_url or url,
                            "title": metadata.title if metadata else None,
                            "snippet": clean_text[:500] + "..." if len(clean_text) > 500 else clean_text,
                            "full_text": clean_text,
                            "text_len": len(clean_text),
                            "quality_score": quality_score,
                            "extraction_method": "trafilatura",
                            "domain": self._get_domain(url),
                            "language": metadata.language if metadata else None,
                            "meta": {
                                "author": metadata.author if metadata else None,
                                "date": metadata.date if metadata else None,
                                "description": metadata.description if metadata else None
                            }
                        }
                        
            except ImportError as e:
                logging.debug(f"Trafilatura nicht installiert für {url}: {e}")
            except (AttributeError, ValueError, TypeError) as e:
                logging.warning(f"Trafilatura-Parameter-Fehler für {url}: {type(e).__name__}: {e}")
            except (ConnectionError, TimeoutError, OSError) as e:
                logging.warning(f"Trafilatura-Netzwerk-Fehler für {url}: {type(e).__name__}: {e}")
            except Exception as e:
                logging.warning(f"Trafilatura unerwarteter Fehler für {url}: {type(e).__name__}: {e}")
            
            # Method 2: Readability (good for structured content)
            try:
                from readability import Document
                
                # Get HTML first
                status, html, final_url = self._http_get(url, timeout=timeout, accept_language=accept_language)
                if status and status < 400 and html:
                    doc = Document(html)
                    title = doc.title()
                    clean_content = doc.summary()
                    
                    # Parse the cleaned HTML
                    from bs4 import BeautifulSoup
                    soup = BeautifulSoup(clean_content, "html.parser")
                    clean_text = soup.get_text(separator=" ", strip=True)
                    
                    if clean_text and len(clean_text.strip()) > 100:
                        quality_score = self._calculate_content_quality(clean_text, query)
                        
                        return {
                            "success": True,
                            "canonical_url": final_url or url,
                            "title": title,
                            "snippet": clean_text[:500] + "..." if len(clean_text) > 500 else clean_text,
                            "full_text": clean_text,
                            "text_len": len(clean_text),
                            "quality_score": quality_score,
                            "extraction_method": "readability",
                            "domain": self._get_domain(url)
                        }
                        
            except ImportError as e:
                logging.debug(f"Readability nicht installiert für {url}: {e}")
            except (AttributeError, ValueError, TypeError) as e:
                logging.warning(f"Readability-Parameter-Fehler für {url}: {type(e).__name__}: {e}")
            except (ConnectionError, TimeoutError, OSError) as e:
                logging.warning(f"Readability-Netzwerk-Fehler für {url}: {type(e).__name__}: {e}")
            except Exception as e:
                logging.warning(f"Readability unerwarteter Fehler für {url}: {type(e).__name__}: {e}")
                import traceback
                logging.debug(f"Readability-Fehler Traceback:\n{traceback.format_exc()}")
            
            # Method 3: Newspaper3k (good for articles)
            if NEWSPAPER_AVAILABLE and newspaper is not None:
                try:
                    article = newspaper.Article(url)
                    article.download()
                    article.parse()
                    
                    if article.text and len(article.text.strip()) > 100:
                        quality_score = self._calculate_content_quality(article.text, query)
                        
                        return {
                            "success": True,
                            "canonical_url": article.canonical_link or url,
                            "title": article.title,
                            "snippet": article.text[:500] + "..." if len(article.text) > 500 else article.text,
                            "full_text": article.text,
                            "text_len": len(article.text),
                            "quality_score": quality_score,
                            "extraction_method": "newspaper3k",
                            "domain": self._get_domain(url),
                            "meta": {
                                "authors": article.authors,
                                "publish_date": str(article.publish_date) if article.publish_date else None,
                                "keywords": article.keywords,
                                "summary": article.summary
                            }
                        }
                except ImportError as e:
                    logging.debug(f"Newspaper3k nicht installiert für {url}: {e}")
                except (AttributeError, ValueError, TypeError) as e:
                    logging.warning(f"Newspaper3k-Parameter-Fehler für {url}: {type(e).__name__}: {e}")
                except (ConnectionError, TimeoutError, OSError) as e:
                    logging.warning(f"Newspaper3k-Netzwerk-Fehler für {url}: {type(e).__name__}: {e}")
                except Exception as e:
                    logging.warning(f"Newspaper3k unerwarteter Fehler für {url}: {type(e).__name__}: {e}")
                    import traceback
                    logging.debug(f"Newspaper3k-Fehler Traceback:\n{traceback.format_exc()}")
            
            # Method 4: Jina AI Reader (API-based)
            if os.getenv("JINA_API_ENABLED", "").lower() in {"1", "true", "yes", "on"}:
                try:
                    jina_url = f"https://r.jina.ai/{url}"
                    req = urllib.request.Request(
                        jina_url,
                        headers={"User-Agent": self._get_rotating_user_agent()}
                    )
                    
                    with urllib.request.urlopen(req, timeout=timeout) as resp:
                        clean_content = resp.read().decode('utf-8', errors='ignore')
                        
                        if clean_content and len(clean_content.strip()) > 100:
                            quality_score = self._calculate_content_quality(clean_content, query)
                            
                            return {
                                "success": True,
                                "canonical_url": url,
                                "snippet": clean_content[:500] + "..." if len(clean_content) > 500 else clean_content,
                                "full_text": clean_content,
                                "text_len": len(clean_content),
                                "quality_score": quality_score,
                                "extraction_method": "jina_ai",
                                "domain": self._get_domain(url)
                            }
                            
                except (ConnectionError, TimeoutError, OSError) as e:
                    logging.warning(f"Jina AI Reader Netzwerk-Fehler für {url}: {type(e).__name__}: {e}")
                except (ValueError, TypeError) as e:
                    logging.warning(f"Jina AI Reader Parameter-Fehler für {url}: {type(e).__name__}: {e}")
                except Exception as e:
                    logging.warning(f"Jina AI Reader unerwarteter Fehler für {url}: {type(e).__name__}: {e}")
                    import traceback
                    logging.debug(f"Jina AI Reader-Fehler Traceback:\n{traceback.format_exc()}")
            
            return {"success": False, "error": "Alle modernen Extraktoren fehlgeschlagen", "error_class": "all_modern_failed"}
            
        except (AttributeError, TypeError, ValueError) as e:
            logging.warning(f"Modern extraction Parameter-Fehler für {url}: {type(e).__name__}: {e}")
            return {"success": False, "error": f"Modern extraction parameter error: {e}", "error_class": "modern_extract_params"}
        except (ImportError, ModuleNotFoundError) as e:
            logging.debug(f"Modern extraction Import-Fehler für {url}: {e}")
            return {"success": False, "error": f"Modern extraction import error: {e}", "error_class": "modern_extract_import"}
        except Exception as e:
            logging.error(f"Modern extraction unerwarteter Fehler für {url}: {type(e).__name__}: {e}")
            import traceback
            logging.debug(f"Modern extraction-Fehler Traceback:\n{traceback.format_exc()}")
            return {"success": False, "error": f"Modern extraction failed: {e}", "error_class": "modern_extract"}
    
    def _llm_clean_content(self, basic_result: Dict[str, Any], *, query: Optional[str] = None) -> Dict[str, Any]:
        """
        Clean and improve extracted content using built-in text processing.
        Note: LLM cleaning temporarily disabled due to dependency issues.
        """
        try:
            # Simple text cleaning without external LLM dependency
            snippet = basic_result.get("snippet", "")
            if snippet and len(snippet) > 50:
                # Basic cleaning - remove common noise patterns
                lines = snippet.split('\n')
                cleaned_lines = []
                
                for line in lines:
                    line = line.strip()
                    # Skip empty lines, navigation, common spam
                    if (line and 
                        not line.lower().startswith(('menu', 'navigation', 'footer', 'header')) and
                        len(line) > 20 and
                        not line.count('|') > 3):  # Likely navigation
                        cleaned_lines.append(line)
                
                if cleaned_lines:
                    cleaned_text = '\n'.join(cleaned_lines[:10])  # Keep top 10 meaningful lines
                    if len(cleaned_text) > 100:  # Only if substantial content
                        return {
                            **basic_result,
                            "snippet": cleaned_text,
                            "quality_score": min(len(cleaned_text) / 500, 1.0),
                            "processing": "text_cleaned"
                        }
            
            return basic_result
            
        except (AttributeError, ValueError, TypeError) as e:
            logging.warning(f"Content cleaning Parameter-Fehler: {type(e).__name__}: {e}")
            return basic_result
        except (KeyError, IndexError) as e:
            logging.warning(f"Content cleaning Zugriffs-Fehler: {type(e).__name__}: {e}")
            return basic_result
        except Exception as e:
            logging.warning(f"Content cleaning unerwarteter Fehler: {type(e).__name__}: {e}")
            import traceback
            logging.debug(f"Content cleaning-Fehler Traceback:\n{traceback.format_exc()}")
            return basic_result
    
    def _calculate_content_quality(self, content: str, query: Optional[str] = None) -> float:
        """
        Calculate content quality score based on multiple factors.
        Returns score between 0.0 and 1.0.
        """
        if not content:
            return 0.0
        
        score = 0.0
        content_lower = content.lower()
        
        # Length factor (optimal around 200-2000 chars)
        length = len(content)
        if length > 2000:
            length_score = 1.0
        elif length > 500:
            length_score = 0.8
        elif length > 200:
            length_score = 0.6
        elif length > 100:
            length_score = 0.4
        else:
            length_score = 0.2
        score += length_score * 0.3
        
        # Content depth (sentences, paragraphs)
        sentence_count = content.count('.') + content.count('!') + content.count('?')
        paragraph_count = content.count('\n\n') + 1
        depth_score = min(1.0, (sentence_count * 0.1 + paragraph_count * 0.2))
        score += depth_score * 0.3
        
        # Noise detection (less is better)
        noise_indicators = ['click here', 'subscribe', 'advertisement', 'cookie', 'privacy policy', 'navigation']
        noise_count = sum(1 for indicator in noise_indicators if indicator in content_lower)
        noise_score = max(0.0, 1.0 - (noise_count * 0.2))
        score += noise_score * 0.2
        
        # Query relevance (if query provided)
        if query:
            query_words = query.lower().split()
            relevance_count = sum(1 for word in query_words if word in content_lower)
            relevance_score = min(1.0, relevance_count / max(1, len(query_words)))
            score += relevance_score * 0.2
        else:
            score += 0.1  # Base score if no query
        
        return min(1.0, max(0.0, score))
    
    def _get_domain(self, url: str) -> str:
        """Extract domain from URL."""
        try:
            domain = urllib.parse.urlparse(url).netloc.lower()
            if domain.startswith("www."):
                domain = domain[4:]
            return domain
        except (ValueError, TypeError, AttributeError) as e:
            logging.debug(f"Domain-Parsing-Fehler für URL {url}: {e}")
            return ""
        except Exception as e:
            logging.debug(f"Domain-Extraktion unerwarteter Fehler für URL {url}: {type(e).__name__}: {e}")
            return ""
    
    def _calculator(self, params: Dict) -> Dict:
        """GPU-beschleunigte mathematische Berechnungen mit CuPy/NumExpr fallback"""
        expression = params.get("expression", "").strip()
        use_gpu = params.get("use_gpu", True)  # GPU by default
        
        if not expression:
            return {
                "success": False,
                "expression": expression,
                "error": "Leere mathematische Expression",
                "error_class": "empty_expression",
                "message": "Bitte geben Sie eine mathematische Expression ein",
                "suggestions": ["Beispiel: 2 + 3 * 4", "Verfügbare Funktionen: sin, cos, tan, sqrt, log, abs"]
            }

        # Initialize variables
        result = None
       
        calculation_method = "unknown"
        
        try:
            # Versuche zuerst GPU-beschleunigte Berechnung mit CuPy
            if use_gpu and CUPY_AVAILABLE and cp is not None:
                try:
                    import math
                    import time
                    
                    start_time = time.perf_counter()
                    
                    # GPU-optimierte sichere mathematische Umgebung
                    gpu_safe_dict = {
                        'pi': cp.pi,
                        'e': cp.e,
                        'sqrt': cp.sqrt,
                        'sin': cp.sin,
                        'cos': cp.cos,
                        'tan': cp.tan,
                        'arcsin': cp.arcsin,  # CuPy uses arc* naming
                        'arccos': cp.arccos,
                        'arctan': cp.arctan,
                        'asin': cp.arcsin,   # Alias for compatibility
                        'acos': cp.arccos,
                        'atan': cp.arctan,
                        'log': cp.log,
                        'log10': cp.log10,
                        'exp': cp.exp,
                        'abs': cp.abs,
                        'ln': cp.log,
                        'ceil': cp.ceil,
                        'floor': cp.floor
                    }
                    
                    # GPU-beschleunigte Auswertung
                    result = self._safe_eval_gpu(expression, gpu_safe_dict)
                    gpu_time = time.perf_counter() - start_time
                    
                    # Convert CuPy array to Python scalar if needed
                    if hasattr(result, 'get'):  # CuPy array
                        result = float(result.get())
                    elif hasattr(result, 'item'):  # NumPy-like scalar
                        result = result.item()
                    
                    calculation_method = f"cupy_gpu (RTX4090, {gpu_time:.4f}s)"
                    
                except (ImportError, Exception) as gpu_error:
                    logging.debug(f"GPU calculation failed, falling back to CPU: {gpu_error}")
                    # Fall through to CPU methods
                    use_gpu = False
            
            if not use_gpu:
                # CPU Fallback: numexpr für bessere Performance und Sicherheit
                try:
                    import numexpr  # type: ignore[import]
                    import math
                    
                    # Erweiterte sichere mathematische Umgebung
                    safe_dict = {
                        'pi': math.pi,
                        'e': math.e,
                        'sqrt': math.sqrt,
                        'sin': math.sin,
                        'cos': math.cos,
                        'tan': math.tan,
                        'asin': math.asin,
                        'acos': math.acos,
                        'atan': math.atan,
                        'log': math.log,
                        'log10': math.log10,
                        'exp': math.exp,
                        'abs': abs,
                        'ln': math.log,  # Natural log alias
                        'ceil': math.ceil,
                        'floor': math.floor,
                    }
                    
                    # Numexpr für sichere und schnelle Auswertung
                    result = numexpr.evaluate(expression, local_dict=safe_dict)
                    calculation_method = "numexpr_cpu"
                    
                except ImportError:
                    # Final Fallback: Sichere AST-basierte Auswertung ohne eval()
                    import ast
                    import math
                    import operator
                    
                    result = self._safe_eval_ast(expression)
                    calculation_method = "ast_safe"
            
            # Ensure variables are defined (should never reach here without them)
            if 'result' not in locals():
                raise RuntimeError("Keine Berechnungsmethode verfügbar")
            
            # Formatierung des Ergebnisses
            if isinstance(result, (int, float)):
                if abs(result) > 1e15:
                    formatted_result = f"{result:.6e}"
                elif isinstance(result, float) and result.is_integer():
                    formatted_result = str(int(result))
                else:
                    formatted_result = str(result)
            else:
                formatted_result = str(result)
            
            return {
                "success": True,
                "expression": expression,
                "result": result,
                "formatted_result": formatted_result,
                "method": calculation_method,
                "message": f"{expression} = {formatted_result}"
            }
            
        except (SyntaxError, NameError) as e:
            logging.warning(f"Calculator Syntax-/Name-Fehler für '{expression}': {e}")
            return {
                "success": False,
                "expression": expression,
                "error": str(e),
                "error_class": "syntax_error",
                "message": f"Syntax-Fehler bei Berechnung von '{expression}': {e}",
                "suggestions": [
                    "Überprüfen Sie die Syntax der mathematischen Expression",
                    "Verwenden Sie nur erlaubte Funktionen: sin, cos, tan, sqrt, log, abs",
                    "Beispiel korrekter Syntax: 2 * pi * sqrt(16)"
                ]
            }
        except (ValueError, TypeError) as e:
            logging.warning(f"Calculator Wert-/Typ-Fehler für '{expression}': {e}")
            return {
                "success": False,
                "expression": expression,
                "error": str(e),
                "error_class": "value_error", 
                "message": f"Wert-Fehler bei Berechnung von '{expression}': {e}",
                "suggestions": [
                    "Verwenden Sie nur numerische Werte",
                    "Prüfen Sie Definitionsbereiche von Funktionen (z.B. sqrt nur für positive Zahlen)",
                    "Beispiel: sqrt(16) statt sqrt(-16)"
                ]
            }
        except ZeroDivisionError as e:
            logging.warning(f"Calculator Division-durch-Null für '{expression}': {e}")
            return {
                "success": False,
                "expression": expression,
                "error": "Division durch Null",
                "error_class": "division_by_zero",
                "message": f"Division durch Null bei Berechnung von '{expression}'",
                "suggestions": [
                    "Überprüfen Sie den Nenner der Division",
                    "Stellen Sie sicher, dass der Nenner nicht Null ist",
                    "Verwenden Sie Grenzwerte für Annäherungen an Null"
                ]
            }
        except OverflowError as e:
            logging.warning(f"Calculator Overflow-Fehler für '{expression}': {e}")
            return {
                "success": False,
                "expression": expression,
                "error": "Zahl zu groß für Berechnung",
                "error_class": "overflow_error",
                "message": f"Overflow-Fehler bei Berechnung von '{expression}': Das Ergebnis ist zu groß",
                "suggestions": [
                    "Verwenden Sie kleinere Zahlen",
                    "Überprüfen Sie Exponenten auf realistische Werte",
                    "Beispiel: 10**10 statt 10**100"
                ]
            }
        except Exception as e:
            logging.error(f"Calculator unerwarteter Fehler für '{expression}': {type(e).__name__}: {e}")
            import traceback
            logging.debug(f"Calculator-Fehler Traceback:\n{traceback.format_exc()}")
            return {
                "success": False,
                "expression": expression,
                "error": str(e),
                "error_class": "unexpected_error",
                "message": f"Unerwarteter Fehler bei Berechnung von '{expression}': {e}"
            }
    
    def _file_reader(self, params: Dict) -> Dict:
        """Datei lesen — sandbox-validiert + Zeilenfenster + Char-Backstop.

        P0: Hard-Char-Limit (Token-Budget-Schutz), Binary-Check.
        P1 (2026-08-24): 1-basiertes ``offset``/``limit``-Zeilenfenster
        (Claude Code Read-Modell) + ehrliche Line-Metadaten im Tool-Result.
        """
        file_path = params.get("file_path", "")
        encoding = params.get("encoding", "utf-8")

        if not file_path:
            return {"success": False, "error": "file_path ist erforderlich", "error_class": "invalid_params"}

        # P1: offset/limit — strikte int-Koerzision, kein silent Fallback.
        # LLMs senden numeric Strings gelegentlich als "100"; int("100") ist OK.
        # Nicht-konvertierbare Werte (z. B. "abc") → expliziter Fehler,
        # damit das Modell die Parameter korrigieren kann.
        offset_raw = params.get("offset", 1)
        limit_raw = params.get("limit")
        try:
            offset = int(offset_raw)
            limit = int(limit_raw) if limit_raw is not None else DEFAULT_READ_LINE_LIMIT
        except (TypeError, ValueError):
            return {
                "success": False,
                "error": (
                    f"offset/limit müssen Ganzzahlen sein "
                    f"(bekommen: offset={offset_raw!r}, limit={limit_raw!r})"
                ),
                "error_class": "invalid_params",
            }

        try:
            # 2026-08-24 (P0): read_file_safe — Hard-Char-Limit, Binary-Check.
            # P1: + Zeilenfenster (1-based offset/limit) als primäre Navigation;
            # das Char-Limit bleibt Sicherheits-Backstop für sehr lange Zeilen.
            resolved, content, was_truncated, total_chars, line_meta = (
                self.path_sandbox.read_file_safe(
                    file_path, encoding=encoding, offset=offset, limit=limit
                )
            )
            total_lines = line_meta["total_lines"]
            if total_lines == 0:
                window_desc = "leere Datei"
            elif line_meta["start_line"] > total_lines:
                window_desc = f"Offset {line_meta['start_line']} über Dateiende ({total_lines} Zeilen)"
            else:
                window_desc = f"Zeilen {line_meta['start_line']}–{line_meta['end_line']} von {total_lines}"

            result = {
                "success": True,
                "file_path": str(resolved),
                "content": content,
                "size": len(content),
                "total_chars": total_chars,
                "was_truncated": was_truncated,
                # P1: Zeilen-Metadaten (ehrliche Navigation, Claude Code Read-Modell)
                "total_lines": total_lines,
                "start_line": line_meta["start_line"],
                "end_line": line_meta["end_line"],
                "has_more_lines": line_meta["has_more_lines"],
                "next_offset": line_meta["next_offset"],
                "ingest_policy": "stream_only_no_rag",
                "rag_ingest": False,
                "message": f"Datei gelesen: {resolved} ({window_desc}, {total_chars} Zeichen gesamt)",
            }

            # Navigation-Hinweise — nur bei Handlungsbedarf (P0-Vertrag:
            # suggested_action fehlt, wenn alles sauber gelesen wurde).
            hints = []
            if line_meta["has_more_lines"]:
                hints.append(f"weitere Zeilen: erneut mit offset={line_meta['next_offset']}")
            if was_truncated:
                result["truncated_at"] = self.path_sandbox.policy.max_read_chars
                hints.append(
                    f"Fenster enthält sehr lange Zeilen (Char-Backstop bei "
                    f"{self.path_sandbox.policy.max_read_chars:,} Zeichen aktiv) — "
                    f"kleineres limit oder search_files nutzen"
                )
            if total_lines > 0 and line_meta["start_line"] > total_lines:
                hints.append(f"Nutze offset ≤ {total_lines}")
            if hints:
                result["suggested_action"] = "; ".join(hints)

            return result
        except PathSandboxError as exc:
            logging.warning(f"File reader: Sandbox-Fehler für {file_path}: {exc}")
            payload = self._build_allowlist_permission_request("file_reader", params)
            return {
                "success": False,
                "error": str(exc),
                "error_class": "sandbox_error",
                **payload,
            }
        except (UnicodeDecodeError, LookupError) as exc:
            logging.warning(f"File reader: Encoding-Fehler für {file_path} (encoding: {encoding}): {exc}")
            return {"success": False, "error": f"Encoding-Fehler ({encoding}): {exc}", "error_class": "encoding_error"}
        except (OSError, IOError) as exc:
            logging.warning(f"File reader: IO-Fehler für {file_path}: {exc}")
            return {"success": False, "error": f"IO-Fehler: {exc}", "error_class": "io_error"}

    def _file_writer(self, params: Dict) -> Dict:
        """Datei schreiben — sandbox-validiert + Größen-Limit."""
        file_path = params.get("file_path", "")
        content = params.get("content", "")
        encoding = params.get("encoding", "utf-8")

        try:
            resolved, bytes_written = self.path_sandbox.write_text(file_path, content, encoding=encoding)
            return {
                "success": True,
                "file_path": str(resolved),
                "bytes_written": bytes_written,
                "message": f"Datei geschrieben: {resolved}",
            }
        except PathSandboxError as exc:
            logging.warning(f"File writer: Sandbox-Fehler für {file_path}: {exc}")
            payload = self._build_allowlist_permission_request("file_writer", params)
            return {
                "success": False,
                "error": str(exc),
                "error_class": "sandbox_error",
                **payload,
            }
        except (UnicodeEncodeError, LookupError) as exc:
            logging.warning(f"File writer: Encoding-Fehler ({encoding}): {exc}")
            return {"success": False, "error": f"Encoding-Fehler ({encoding}): {exc}", "error_class": "encoding_error"}
        except (OSError, IOError) as exc:
            logging.warning(f"File writer: IO-Fehler für {file_path}: {exc}")
            return {"success": False, "error": f"IO-Fehler: {exc}", "error_class": "io_error"}
    
    # ------------------------------------------------------------------
    # SOTA Filesystem-Connector (2026) — list_directory + search_files
    # ------------------------------------------------------------------

    def _list_directory(self, params: Dict) -> Dict:
        """Verzeichnis auflisten — sandbox-validiert + Depth-Limiter."""
        dir_path = params.get("path", "")
        max_depth = params.get("max_depth", 2)

        try:
            resolved_dir = self.path_sandbox.resolve(dir_path, must_exist=True)
            entries = self.path_sandbox.list_directory_safe(str(resolved_dir), max_depth=max_depth)
            serialized_entries = [
                {
                    "name": e.name,
                    "path": e.path,
                    "is_dir": e.is_dir,
                    "size": e.size,
                    "modified": e.modified,
                }
                for e in entries
            ]
            return {
                "success": True,
                "path": str(resolved_dir),
                "entries": serialized_entries,
                "count": len(serialized_entries),
                "ingest_policy": "stream_only_no_rag",
                "rag_ingest": False,
                "message": f"Verzeichnis aufgelistet: {resolved_dir} "
                           f"({len(serialized_entries)} Einträge)",
            }
        except PathSandboxError as exc:
            logging.warning(f"List directory: Sandbox-Fehler für {dir_path}: {exc}")
            payload = self._build_allowlist_permission_request("list_directory", params)
            return {
                "success": False,
                "error": str(exc),
                "error_class": "sandbox_error",
                **payload,
            }
        except OSError as exc:
            logging.warning(f"List directory: OS-Fehler für {dir_path}: {exc}")
            return {"success": False, "error": f"OS-Fehler: {exc}", "error_class": "os_error"}

    def _search_files(self, params: Dict) -> Dict:
        """Datei-Namen oder -Inhalte suchen (P2: ripgrep-Backend, 2026-08-24).

        Modi:
        - Content-Modus (Default): ripgrep via ``search_content_rg`` —
          binär-sicher, Ergebnis-Cap (Default 50), Timeout (Default 10s)
          mit Partial-Ergebnis, Hidden-/gitignore-Schutz (PII),
          ``fixed_string`` (rg -F) für Literal-Suche.
        - Name-Modus (content_search=false): Python-Walker
          ``search_files_safe`` — Regex gegen Dateinamen (Default-Semantik
          des bestehenden Walker-Vertrags).

        Sandbox:
        - PathSandboxError → einheitliches ``sandbox_error`` +
          ``permission_request``-Contract (execute_tool; identisch zu
          file_reader).
        - rg nicht installiert → expliziter, markierter Python-Fallback
          (``backend``/``fallback_reason`` im Response; keine silent
          fallbacks, DoD 2026-08-24).
        """
        root_path = params.get("root_path")
        pattern = params.get("pattern")
        if not root_path:
            return {
                "success": False,
                "error": "Parameter 'root_path' ist erforderlich",
                "error_class": "missing_parameter",
            }
        if pattern is None or not str(pattern).strip():
            return {
                "success": False,
                "error": "Parameter 'pattern' ist erforderlich (nicht leer)",
                "error_class": "missing_parameter",
            }
        pattern = str(pattern)
        content_search = bool(params.get("content_search", True))

        def _int_param(value, default: int, lo: int, hi: int) -> int:
            try:
                return max(lo, min(hi, int(value)))
            except (TypeError, ValueError):
                return default

        max_depth = _int_param(params.get("max_depth", 5), 5, 1, 10)

        def _sandbox_denied(exc: Exception) -> Dict:
            logging.warning(f"Search files: Sandbox-Fehler für {root_path}: {exc}")
            payload = self._build_allowlist_permission_request("search_files", params)
            return {
                "success": False,
                "error": str(exc),
                "error_class": "sandbox_error",
                **payload,
            }

        # ── Name-Modus: Python-Walker (Regex gegen Dateinamen) ────────────
        if not content_search:
            try:
                resolved_root = self.path_sandbox.resolve(root_path, must_exist=True)
                matches = self.path_sandbox.search_files_safe(
                    str(resolved_root), pattern,
                    content_search=False, max_depth=max_depth,
                )
            except PathSandboxError as exc:
                return _sandbox_denied(exc)
            except re.error as exc:
                return {
                    "success": False,
                    "error": f"Ungültiges Namens-Regex: {exc}",
                    "error_class": "invalid_pattern",
                }
            except OSError as exc:
                logging.warning(f"Search files: OS-Fehler für {root_path}: {exc}")
                return {
                    "success": False,
                    "error": f"OS-Fehler: {exc}",
                    "error_class": "os_error",
                }
            return {
                "success": True,
                "mode": "name",
                "backend": "python",
                "root_path": str(resolved_root),
                "pattern": pattern,
                "count": len(matches),
                "results": matches,
                "truncated": False,
                "timed_out": False,
                "ingest_policy": "stream_only_no_rag",
                "rag_ingest": False,
                "message": (
                    f"{len(matches)} Dateinamen-Treffer für '{pattern}' "
                    f"in {resolved_root}"
                ),
            }

        # ── Content-Modus (Default): ripgrep ───────────────────────────────
        max_results = _int_param(
            params.get("max_results", DEFAULT_RG_MAX_RESULTS),
            DEFAULT_RG_MAX_RESULTS, 1, 500,
        )
        try:
            timeout = float(params.get("timeout", DEFAULT_RG_TIMEOUT))
        except (TypeError, ValueError):
            timeout = DEFAULT_RG_TIMEOUT
        if timeout <= 0:
            timeout = DEFAULT_RG_TIMEOUT
        timeout = min(timeout, 60.0)
        case_insensitive = bool(params.get("case_insensitive", True))
        fixed_string = bool(params.get("fixed_string", False))
        hidden = bool(params.get("hidden", False))
        context = _int_param(params.get("context", 0), 0, 0, 10)
        glob = params.get("glob") if isinstance(params.get("glob"), str) else None

        try:
            resolved_root = self.path_sandbox.resolve(root_path, must_exist=True)
            res = self.path_sandbox.search_content_rg(
                str(resolved_root), pattern,
                case_sensitive=not case_insensitive,
                fixed_string=fixed_string,
                glob=glob,
                hidden=hidden,
                context=context,
                max_results=max_results,
                timeout=timeout,
            )
        except PathSandboxError as exc:
            return _sandbox_denied(exc)
        except ValueError as exc:
            return {
                "success": False,
                "error": str(exc),
                "error_class": "invalid_pattern",
            }
        except OSError as exc:
            logging.warning(f"Search files: OS-Fehler für {root_path}: {exc}")
            return {
                "success": False,
                "error": f"OS-Fehler: {exc}",
                "error_class": "os_error",
            }
        except RipgrepNotFoundError:
            # Expliziter, markierter Python-Fallback (keine silent fallbacks)
            logging.warning(
                "Search files: ripgrep nicht installiert → Python-Fallback"
            )
            try:
                resolved_root = self.path_sandbox.resolve(root_path, must_exist=True)
                py_pattern = re.escape(pattern) if fixed_string else pattern
                matches = self.path_sandbox.search_files_safe(
                    str(resolved_root), py_pattern,
                    content_search=True, max_depth=max_depth,
                )
            except PathSandboxError as exc:
                return _sandbox_denied(exc)
            except re.error as exc:
                return {
                    "success": False,
                    "error": f"Ungültiges Regex: {exc}",
                    "error_class": "invalid_pattern",
                }
            except OSError as exc:
                logging.warning(f"Search files: OS-Fehler für {root_path}: {exc}")
                return {
                    "success": False,
                    "error": f"OS-Fehler: {exc}",
                    "error_class": "os_error",
                }
            truncated = len(matches) >= DEFAULT_MAX_SEARCH_RESULTS
            return {
                "success": True,
                "mode": "content",
                "backend": "python-fallback",
                "fallback_reason": (
                    "ripgrep (rg) nicht installiert — dokumentierter "
                    "Python-Backend"
                ),
                "root_path": str(resolved_root),
                "pattern": pattern,
                "count": len(matches),
                "results": matches,
                "truncated": truncated,
                "timed_out": False,
                "ingest_policy": "stream_only_no_rag",
                "rag_ingest": False,
                "message": (
                    f"{len(matches)} Treffer (Python-Fallback) für '{pattern}' "
                    f"in {resolved_root}"
                ),
            }

        if res.get("error"):
            return {
                "success": False,
                "error": res["error"],
                "error_class": res.get("error_class") or "search_error",
                "backend": res.get("backend", "ripgrep"),
                "root_path": str(resolved_root),
                "pattern": pattern,
            }

        matches = res["matches"]
        truncated = res["truncated"]
        timed_out = res["timed_out"]
        if timed_out:
            message = (
                f"Timeout nach {timeout:.2f}s: {len(matches)} Partial-Treffer "
                f"für '{pattern}' in {resolved_root}"
            )
        elif truncated:
            message = (
                f"{len(matches)} Treffer (Cap max_results={max_results}, "
                f"weitere vorhanden) für '{pattern}' in {resolved_root}"
            )
        else:
            message = f"{len(matches)} Treffer für '{pattern}' in {resolved_root}"
        return {
            "success": True,
            "mode": "content",
            "backend": "ripgrep",
            "root_path": str(resolved_root),
            "pattern": pattern,
            "count": len(matches),
            "results": matches,
            "truncated": truncated,
            "timed_out": timed_out,
            "elapsed_ms": res.get("elapsed_ms"),
            "ingest_policy": "stream_only_no_rag",
            "rag_ingest": False,
            "message": message,
        }

    def _code_executor(self, params: Dict) -> Dict:
        """
        SOTA Python-Code Ausführung via CodeExecutorEngine.
        
        Features:
        - AST-basierte Sicherheitsanalyse (nicht Regex)
        - LLM-basierter Auto-Retry bei Fehlern (max 3 Versuche)
        - Multi-Plot Support (matplotlib + Plotly)
        - Persistente Sessions (optionaler session_id Parameter)
        - Auto-Install von whitelisted Paketen bei ImportError
        - Jupyter-like Auto-Display der letzten Expression
        - Strukturierte Fehler mit Zeilen-Kontext
        - Datei-Output mit Sandbox-Verzeichnis
        - Detached-Modus für interaktive GUI-Apps und Spiele
        """
        code = params.get("code", "")
        timeout = params.get("timeout", 30)
        session_id = params.get("session_id", None)
        detached = params.get("detached", None)
        deliver_to_user = bool(params.get("deliver_to_user", False))
        artifact_name = params.get("artifact_name")
        
        # ✅ SOTA: Delegate to CodeExecutorEngine
        if self._code_engine is not None:
            try:
                result = self._code_engine.execute(
                    code=code,
                    timeout=timeout,
                    session_id=session_id,
                    auto_retry=True,
                    auto_install=True,
                    detached=detached,
                )
                if result.success and deliver_to_user:
                    final_code = result.code_versions[-1] if result.code_versions else code
                    program_file = self._code_engine.save_user_program(final_code, artifact_name)
                    result.files = [
                        file_info
                        for file_info in result.files
                        if file_info.get("path") != program_file["path"]
                    ]
                    result.files.append(program_file)
                return result.to_dict()
            except Exception as e:
                logging.error(f"CodeExecutorEngine error: {type(e).__name__}: {e}")
                return {
                    "success": False,
                    "error": str(e),
                    "error_class": type(e).__name__,
                }
        
        # ── Fallback: Legacy subprocess execution (if engine not available) ──
        if not code.strip():
            return {"success": False, "error": "Leerer Code", "error_class": "empty_code"}
        
        try:
            import re as _re_mod
            import tempfile
            import time
            
            # Basic regex security check (legacy fallback)
            dangerous = [
                (r'\bsubprocess\b', 'subprocess'), (r'\bshutil\b', 'shutil'),
                (r'__import__', '__import__'), (r'\beval\s*\(', 'eval()'),
                (r'\bexec\s*\(', 'exec()'), (r'\bsocket\b', 'socket'),
                (r'\brequests\b', 'requests'), (r'\burllib\b', 'urllib'),
            ]
            for pattern, name in dangerous:
                if _re_mod.search(pattern, code, _re_mod.IGNORECASE):
                    return {"success": False, "error": f"🔒 {name} blockiert", "error_class": "security"}
            
            start_time = time.perf_counter()
            temp_dir = tempfile.mkdtemp(prefix='python_executor_')
            plot_file = os.path.join(temp_dir, 'plot.png')
            
            enhanced_code = f"""
import sys, os
try:
    import matplotlib; matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    _HAS_MPL = True
except ImportError:
    _HAS_MPL = False
{code}
if _HAS_MPL:
    try:
        if plt.get_fignums():
            plt.savefig(r'{plot_file}', dpi=150, bbox_inches='tight')
        plt.close('all')
    except Exception as _plot_exc:
        import sys
        print(f"[plot-save-error] {{_plot_exc}}", file=sys.stderr)
"""
            with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False, encoding='utf-8') as f:
                f.write(enhanced_code)
                temp_file = f.name
            try:
                result = subprocess.run(
                    [sys.executable, temp_file], capture_output=True, text=True,
                    timeout=timeout, encoding='utf-8', errors='ignore',
                )
                execution_time = time.perf_counter() - start_time
                plot_b64 = None
                if os.path.exists(plot_file):
                    try:
                        import base64
                        with open(plot_file, 'rb') as pf:
                            plot_b64 = base64.b64encode(pf.read()).decode('utf-8')
                    except Exception as plot_read_exc:
                        logging.debug(f"Plot-Base64-Enkodierung fehlgeschlagen: {plot_read_exc}")
                resp: Dict[str, Any] = {
                    "success": result.returncode == 0, "stdout": result.stdout,
                    "stderr": result.stderr, "execution_time": execution_time,
                    "message": f"Code ausgeführt ({execution_time:.4f}s)",
                }
                if plot_b64:
                    resp["plot"] = plot_b64
                    resp["plot_base64"] = plot_b64
                    resp["plot_format"] = "png"
                return resp
            finally:
                try:
                    os.unlink(temp_file)
                except Exception as unlink_exc:
                    logging.debug(f"Temporäre Code-Datei konnte nicht gelöscht werden: {unlink_exc}")
                try:
                    import shutil; shutil.rmtree(temp_dir, ignore_errors=True)
                except Exception as rmtree_exc:
                    logging.debug(f"Temporäres Verzeichnis konnte nicht gelöscht werden: {rmtree_exc}")
        except subprocess.TimeoutExpired:
            return {"success": False, "error": f"Timeout nach {timeout}s", "error_class": "timeout"}
        except Exception as e:
            logging.error(f"Code executor error: {type(e).__name__}: {e}")
            return {"success": False, "error": str(e), "error_class": type(e).__name__}
    
    def _image_info(self, params: Dict) -> Dict:
        """Bild-Inspektion mit zwei Modi.

        modes:
          - "info"     (default): Format, Dimensionen, Modus, EXIF-Subset, Dateigröße.
          - "describe":            Vision-LLM beschreibt den Bildinhalt.
        """
        image_path = params.get("image_path", "")
        mode = params.get("mode", "info")
        prompt = params.get("prompt")

        # Sandbox-validierter Pfad (wirft PathSandboxError → von execute_tool gefangen)
        resolved = self.path_sandbox.resolve(image_path, must_exist=True)

        # Modus 1: technische Bildinfo via Pillow (ersetzt primitive Magic-Bytes-Heuristik).
        if mode == "info":
            try:
                from PIL import Image, ExifTags  # type: ignore
            except ImportError as exc:
                return {"success": False, "error": f"Pillow nicht verfügbar: {exc}", "error_class": "missing_dependency"}

            size_bytes = resolved.stat().st_size
            try:
                with Image.open(resolved) as img:
                    width, height = img.size
                    fmt = img.format or "unknown"
                    color_mode = img.mode
                    exif_data: Dict[str, Any] = {}
                    raw_exif = getattr(img, "_getexif", lambda: None)()
                    if raw_exif:
                        # Nur sichere/relevante Tags durchreichen, keine GPS-Daten standardmäßig.
                        wanted = {"Make", "Model", "DateTime", "Orientation", "Software"}
                        for tag_id, value in raw_exif.items():
                            tag = ExifTags.TAGS.get(tag_id, str(tag_id))
                            if tag in wanted:
                                exif_data[tag] = str(value)[:200]
            except (OSError, ValueError) as exc:
                return {
                    "success": False,
                    "error": f"Bild konnte nicht geöffnet werden: {exc}",
                    "error_class": "invalid_image",
                }

            return {
                "success": True,
                "file_path": str(resolved),
                "format": fmt,
                "width": width,
                "height": height,
                "color_mode": color_mode,
                "size_bytes": size_bytes,
                "exif": exif_data,
                "message": f"{fmt} {width}x{height} ({color_mode}), {size_bytes} Bytes",
            }

        # Modus 2: Inhaltliche Beschreibung via Vision-LLM — nutzt chat_function,
        # die image_path als zweites Argument akzeptiert.
        if mode == "describe":
            if self.chat_function is None:
                return {
                    "success": False,
                    "error": "Vision-LLM nicht verbunden (chat_function fehlt)",
                    "error_class": "vision_unavailable",
                }
            user_prompt = (
                prompt
                or "Beschreibe dieses Bild präzise und sachlich. "
                   "Nenne sichtbare Objekte, Text, Personen (ohne Identifikation), "
                   "Stimmung, Komposition. Keine Spekulation."
            )
            try:
                description = self.chat_function(user_prompt, str(resolved))
            except TypeError:
                # Fallback, falls chat_function image_path nicht unterstützt.
                return {
                    "success": False,
                    "error": "chat_function unterstützt keinen image_path-Parameter",
                    "error_class": "vision_unsupported",
                }
            return {
                "success": True,
                "file_path": str(resolved),
                "description": str(description) if description else "",
                "message": "Vision-Beschreibung erstellt",
            }

        return {
            "success": False,
            "error": f"Unbekannter Modus: {mode!r} (erlaubt: 'info', 'describe')",
            "error_class": "invalid_mode",
        }

    def _pdf_extract(self, params: Dict) -> Dict:
        """PDF-Text-Extraktion via DoclingProcessor (LLM-callable).

        Sandbox-validiert; Docling handhabt OCR und Layout-Analyse intern.
        """
        file_path = params.get("file_path", "")
        max_chars = int(params.get("max_chars", 50_000))

        resolved = self.path_sandbox.resolve(file_path, must_exist=True)
        if resolved.suffix.lower() != ".pdf":
            return {
                "success": False,
                "error": f"Datei ist keine PDF: {resolved.suffix}",
                "error_class": "invalid_format",
            }

        try:
            from utils.docling_processor import DoclingProcessor
        except ImportError as exc:
            return {
                "success": False,
                "error": f"PDF-Prozessor nicht verfügbar: {exc}",
                "error_class": "missing_dependency",
            }

        processor = DoclingProcessor.get_instance()
        result = processor.convert_file(str(resolved))
        text = result.text if result.success else ""

        if not text:
            return {
                "success": False,
                "error": f"Text-Extraktion fehlgeschlagen: {result.error}",
                "error_class": "extraction_failed",
                "metadata": result.metadata or {},
            }

        truncated = len(text) > max_chars
        if truncated:
            text = text[:max_chars] + "\n...[TRUNCATED]"
        return {
            "success": True,
            "file_path": str(resolved),
            "text": text,
            "chars": len(text),
            "truncated": truncated,
            "ingest_policy": "stream_only_no_rag",
            "rag_ingest": False,
            "extraction_method": result.extraction_method,
            "num_pages": result.num_pages,
            "num_tables": result.num_tables,
            "warnings": [],
        }

    def _session_manager(self, params: Dict) -> Dict:
        """Verwaltet die persistent Python session"""
        action = params.get("action", "session_info")
        
        try:
            if action == "show_vars":
                if self.python_session is None:
                    return {
                        "success": True,
                        "variables": {},
                        "message": "Keine aktive Session - noch keine Variablen"
                    }
                
                variables = self.python_session.get_variables()
                return {
                    "success": True,
                    "variables": variables,
                    "variable_count": len(variables),
                    "message": f"{len(variables)} Variablen in Session gefunden"
                }
                
            elif action == "clear_session":
                if self.python_session is None:
                    return {
                        "success": True,
                        "message": "Keine aktive Session zum Zurücksetzen"
                    }
                
                result = self.python_session.clear_session()
                return {
                    "success": True,
                    "message": "Session erfolgreich zurückgesetzt",
                    "details": result["message"]
                }
                
            elif action == "session_info":
                if self.python_session is None:
                    return {
                        "success": True,
                        "session_active": False,
                        "message": "Keine aktive persistent session",
                        "info": "Session wird bei erster Code-Ausführung initialisiert"
                    }
                
                return {
                    "success": True,
                    "session_active": True,
                    "execution_count": self.python_session.execution_count,
                    "total_time": f"{self.python_session.total_time:.4f}s",
                    "avg_time": f"{self.python_session.total_time / max(1, self.python_session.execution_count):.4f}s",
                    "variable_count": len(self.python_session.get_variables()),
                    "gpu_available": "GPU_AVAILABLE" in self.python_session.locals_dict and self.python_session.locals_dict.get("GPU_AVAILABLE", False),
                    "message": "Persistent Python Session aktiv und bereit"
                }
                
            else:
                return {
                    "success": False,
                    "error": f"Unbekannte Aktion: {action}",
                    "available_actions": ["show_vars", "clear_session", "session_info"]
                }
                
        except Exception as e:
            logging.error(f"Session manager error: {type(e).__name__}: {e}")
            return {
                "success": False,
                "error": str(e),
                "error_class": type(e).__name__
            }

    def _fast_content_extract(self, url: str, *, timeout: int = 6, query: Optional[str] = None) -> Dict[str, Any]:
        """
        FAST content extraction optimized for RAG storage.
        Only uses the fastest, most reliable method with strict timeouts.
        """
        try:
            # Respect policy gate
            dec = self._ensure_web_policy().should_fetch(url)
            if not dec.allow:
                return {"success": False, "error": f"blacklisted: {dec.reason}", "error_class": dec.reason}
            
            # FAST Method: Simple HTTP + BeautifulSoup with strict timeout
            import time
            start_time = time.time()
            
            status, html, final_url = self._http_get(url, timeout=min(timeout, 8))  # Max 8 seconds
            
            if not (status and status < 400 and html):
                return {"success": False, "error": f"HTTP {status}", "error_class": "http"}
            
            # Fast HTML parsing - only if we have BeautifulSoup
            try:
                from bs4 import BeautifulSoup
                
                soup = BeautifulSoup(html, "html.parser")
                
                # Remove unwanted elements quickly
                for unwanted in soup(["script", "style", "nav", "header", "footer", "aside", "iframe"]):
                    unwanted.decompose()
                
                # Try to find main content
                content_selectors = [
                    "article", "main", ".content", ".post", ".article", 
                    "[role='main']", ".entry-content", ".post-content"
                ]
                
                main_content = None
                for selector in content_selectors:
                    try:
                        elements = soup.select(selector)
                        if elements:
                            main_content = elements[0]
                            break
                    except (ValueError, NotImplementedError) as exc:
                        logging.debug(f"Selector '{selector}' failed: {exc}")
                        continue
                
                # Fallback to body if no main content found
                if not main_content:
                    main_content = soup.find("body") or soup
                
                # Extract text
                clean_text = main_content.get_text(separator=" ", strip=True)
                
                # Basic cleaning
                lines = clean_text.split('\n')
                cleaned_lines = []
                for line in lines:
                    line = line.strip()
                    if len(line) > 10 and not line.startswith(('©', 'Cookie', 'Privacy')):
                        cleaned_lines.append(line)
                
                final_text = ' '.join(cleaned_lines)
                
                # Title extraction
                title = ""
                title_tag = soup.find("title")
                if title_tag:
                    title = title_tag.get_text().strip()
                
                # Check if extraction was successful
                elapsed = time.time() - start_time
                
                if final_text and len(final_text.strip()) > 50:
                    return {
                        "success": True,
                        "canonical_url": final_url or url,
                        "title": title,
                        "snippet": final_text[:500] + "..." if len(final_text) > 500 else final_text,
                        "full_text": final_text,
                        "text_len": len(final_text),
                        "quality_score": 0.8,  # Assume good quality for fast mode
                        "extraction_method": "fast_beautifulsoup",
                        "domain": self._get_domain(url),
                        "extraction_time": elapsed
                    }
                
                return {"success": False, "error": "Insufficient content extracted"}
                
            except ImportError:
                # Even faster fallback - regex-based HTML cleaning
                import re
                
                # Remove HTML tags
                text = re.sub(r'<[^>]+>', ' ', html)
                # Clean up whitespace
                text = re.sub(r'\s+', ' ', text).strip()
                
                if len(text) > 100:
                    elapsed = time.time() - start_time
                    return {
                        "success": True,
                        "canonical_url": final_url or url,
                        "title": "Extracted Content",
                        "snippet": text[:500] + "..." if len(text) > 500 else text,
                        "full_text": text,
                        "text_len": len(text),
                        "quality_score": 0.6,  # Lower quality for regex method
                        "extraction_method": "fast_regex",
                        "domain": self._get_domain(url),
                        "extraction_time": elapsed
                    }
                
                return {"success": False, "error": "Regex extraction insufficient"}
                
        except Exception as e:
            return {"success": False, "error": f"Fast extraction failed: {e}", "error_class": "fast_extract"}

    def _safe_eval_ast(self, expression: str) -> Any:
        """
        Sichere AST-basierte Auswertung mathematischer Ausdrücke ohne eval()
        Nur erlaubte mathematische Operationen und Funktionen
        """
        import ast
        import math
        import operator
        
        # Erlaubte Operatoren
        safe_ops = {
            ast.Add: operator.add,
            ast.Sub: operator.sub,
            ast.Mult: operator.mul,
            ast.Div: operator.truediv,
            ast.FloorDiv: operator.floordiv,
            ast.Mod: operator.mod,
            ast.Pow: operator.pow,
            ast.USub: operator.neg,
            ast.UAdd: operator.pos,
        }
        
        # Erlaubte Funktionen
        safe_functions = {
            'abs': abs,
            'round': round,
            'sqrt': math.sqrt,
            'sin': math.sin,
            'cos': math.cos,
            'tan': math.tan,
            'asin': math.asin,
            'acos': math.acos,
            'atan': math.atan,
            'log': math.log,
            'log10': math.log10,
            'ln': math.log,
            'exp': math.exp,
            'ceil': math.ceil,
            'floor': math.floor,
            'pi': math.pi,
            'e': math.e
        }
        
        def safe_eval_node(node):
            if isinstance(node, ast.Constant):  # Python 3.8+
                return node.value
            elif isinstance(node, ast.Num):  # Legacy Python < 3.8
                return node.n
            elif isinstance(node, ast.Name):
                if node.id in safe_functions:
                    return safe_functions[node.id]
                else:
                    raise NameError(f"Unbekannte Variable oder Funktion: {node.id}")
            elif isinstance(node, ast.BinOp):
                left = safe_eval_node(node.left)
                right = safe_eval_node(node.right)
                op = safe_ops.get(type(node.op))
                if op:
                    return op(left, right)
                else:
                    raise ValueError(f"Nicht erlaubte Operation: {type(node.op).__name__}")
            elif isinstance(node, ast.UnaryOp):
                operand = safe_eval_node(node.operand)
                op = safe_ops.get(type(node.op))
                if op:
                    return op(operand)
                else:
                    raise ValueError(f"Nicht erlaubte Unary-Operation: {type(node.op).__name__}")
            elif isinstance(node, ast.Call):
                func = safe_eval_node(node.func)
                if callable(func) and func in safe_functions.values():
                    args = [safe_eval_node(arg) for arg in node.args]
                    return func(*args)
                else:
                    raise ValueError(f"Nicht erlaubter Funktionsaufruf: {node.func}")
            else:
                raise ValueError(f"Nicht erlaubter AST-Node-Typ: {type(node).__name__}")
        
        try:
            # Parse expression to AST
            tree = ast.parse(expression, mode='eval')
            return safe_eval_node(tree.body)
        except SyntaxError as e:
            raise SyntaxError(f"Ungültige Syntax: {e}")

    def _safe_eval_gpu(self, expression: str, gpu_safe_dict: dict) -> Any:
        """
        GPU-beschleunigte AST-basierte Auswertung mathematischer Ausdrücke mit CuPy
        Nutzt RTX4090 für massiv parallele Berechnungen
        
        Requires: CUPY_AVAILABLE=True and cp is not None
        """
        if not CUPY_AVAILABLE or cp is None:
            raise ImportError("CuPy nicht verfügbar")
        
        # Local import für Type-Checking (vermeidet Linter-Fehler)
        import cupy as cp_local  # type: ignore
            
        import ast
        import operator
        
        # Erlaubte Operatoren (gleich wie CPU-Version)
        safe_ops = {
            ast.Add: operator.add,
            ast.Sub: operator.sub, 
            ast.Mult: operator.mul,
            ast.Div: operator.truediv,
            ast.FloorDiv: operator.floordiv,
            ast.Mod: operator.mod,
            ast.Pow: operator.pow,
            ast.USub: operator.neg,
            ast.UAdd: operator.pos,
        }
        
        def gpu_eval_node(node):
            if isinstance(node, ast.Constant):  # Python 3.8+
                # Convert to CuPy array for GPU operations
                return cp_local.asarray(node.value)
            elif isinstance(node, ast.Num):  # Legacy Python < 3.8
                return cp_local.asarray(node.n)
            elif isinstance(node, ast.Name):
                if node.id in gpu_safe_dict:
                    val = gpu_safe_dict[node.id]
                    # Ensure it's a CuPy array/scalar
                    return val if hasattr(val, '__module__') and 'cupy' in val.__module__ else cp_local.asarray(val)
                else:
                    raise NameError(f"Unbekannte Variable oder Funktion: {node.id}")
            elif isinstance(node, ast.BinOp):
                left = gpu_eval_node(node.left)
                right = gpu_eval_node(node.right)
                op = safe_ops.get(type(node.op))
                if op:
                    # GPU-accelerated operation
                    return op(left, right)
                else:
                    raise ValueError(f"Nicht erlaubte Operation: {type(node.op).__name__}")
            elif isinstance(node, ast.UnaryOp):
                operand = gpu_eval_node(node.operand)
                op = safe_ops.get(type(node.op))
                if op:
                    return op(operand)
                else:
                    raise ValueError(f"Nicht erlaubte Unary-Operation: {type(node.op).__name__}")
            elif isinstance(node, ast.Call):
                func_name = node.func.id if isinstance(node.func, ast.Name) else str(node.func)
                if func_name in gpu_safe_dict:
                    func = gpu_safe_dict[func_name]
                    args = [gpu_eval_node(arg) for arg in node.args]
                    # GPU-accelerated function call
                    return func(*args)
                else:
                    raise ValueError(f"Nicht erlaubter Funktionsaufruf: {func_name}")
            else:
                raise ValueError(f"Nicht erlaubter AST-Node-Typ: {type(node).__name__}")
        
        try:
            # Parse expression to AST
            tree = ast.parse(expression, mode='eval')
            return gpu_eval_node(tree.body)
        except SyntaxError as e:
            raise SyntaxError(f"Ungültige Syntax: {e}")

    def _detect_query_language(self, query: str) -> str:
        """
        🌍 Intelligente Query-Sprach-Erkennung für optimierte Web-Suche
        
        Erkennt die Sprache der Suchanfrage und generiert passenden Accept-Language Header.
        Unterstützt: Deutsch, Englisch, Bulgarisch, Russisch, Französisch, Spanisch, etc.
        
        Args:
            query: Die Suchanfrage
            
        Returns:
            Accept-Language Header String (z.B. "bg-BG,bg;q=0.9,en;q=0.7,*;q=0.5")
        """
        query_lower = query.lower()
        
        # Sprachspezifische Keywords und Muster
        language_patterns = {
            # Bulgarisch
            'bg': {
                'keywords': ['новини', 'българия', 'софия', 'пловдив', 'варна', 'burgас', 'последни'],
                'chars': 'абвгдежзийклмнопрстуфхцчшщъьюя',
                'accept': "bg-BG,bg;q=0.9,en;q=0.7,de;q=0.5,*;q=0.3"
            },
            # Russisch
            'ru': {
                'keywords': ['новости', 'россия', 'москва', 'путин', 'украина', 'последние'],
                'chars': 'абвгдеёжзийклмнопрстуфхцчшщъыьэюя',
                'accept': "ru-RU,ru;q=0.9,en;q=0.7,de;q=0.5,*;q=0.3"
            },
            # Französisch
            'fr': {
                'keywords': ['actualités', 'france', 'paris', 'dernières', 'nouvelles', 'aujourd\'hui'],
                'chars': 'àâæçéèêëïîôùûüÿœ',
                'accept': "fr-FR,fr;q=0.9,en;q=0.7,de;q=0.5,*;q=0.3"
            },
            # Spanisch
            'es': {
                'keywords': ['noticias', 'españa', 'madrid', 'barcelona', 'últimas', 'hoy'],
                'chars': 'áéíóúñü¿¡',
                'accept': "es-ES,es;q=0.9,en;q=0.7,de;q=0.5,*;q=0.3"
            },
            # Italienisch
            'it': {
                'keywords': ['notizie', 'italia', 'roma', 'milano', 'ultime', 'oggi'],
                'chars': 'àèéìíîòóùú',
                'accept': "it-IT,it;q=0.9,en;q=0.7,de;q=0.5,*;q=0.3"
            },
            # Polnisch
            'pl': {
                'keywords': ['wiadomości', 'polska', 'warszawa', 'kraków', 'ostatnie'],
                'chars': 'ąćęłńóśźż',
                'accept': "pl-PL,pl;q=0.9,en;q=0.7,de;q=0.5,*;q=0.3"
            },
            # Tschechisch
            'cs': {
                'keywords': ['zprávy', 'česko', 'praha', 'brno', 'poslední'],
                'chars': 'áčďéěíňóřšťúůýž',
                'accept': "cs-CZ,cs;q=0.9,en;q=0.7,de;q=0.5,*;q=0.3"
            },
            # Türkisch
            'tr': {
                'keywords': ['haberler', 'türkiye', 'istanbul', 'ankara', 'son'],
                'chars': 'çğıöşü',
                'accept': "tr-TR,tr;q=0.9,en;q=0.7,de;q=0.5,*;q=0.3"
            },
            # Englisch
            'en': {
                'keywords': ['news', 'latest', 'today', 'breaking', 'headlines', 'update'],
                'chars': '',  # Keine speziellen Zeichen
                'accept': "en-US,en;q=0.9,de;q=0.7,*;q=0.5"
            },
            # Deutsch
            'de': {
                'keywords': ['nachrichten', 'deutschland', 'berlin', 'münchen', 'neuesten', 'aktuell', 'heute'],
                'chars': 'äöüß',
                'accept': "de-DE,de;q=0.9,en;q=0.7,*;q=0.5"
            }
        }
        
        # Schritt 1: Prüfe spezielle Zeichen
        char_scores = {}
        for lang, pattern in language_patterns.items():
            special_chars = pattern['chars']
            if special_chars:
                char_count = sum(1 for char in query_lower if char in special_chars)
                if char_count > 0:
                    char_scores[lang] = char_count
        
        # Schritt 2: Prüfe Keywords
        keyword_scores = {}
        for lang, pattern in language_patterns.items():
            keywords = pattern['keywords']
            keyword_count = sum(1 for keyword in keywords if keyword in query_lower)
            if keyword_count > 0:
                keyword_scores[lang] = keyword_count
        
        # Schritt 3: Kombiniere Scores
        combined_scores = {}
        for lang in language_patterns.keys():
            score = char_scores.get(lang, 0) * 2 + keyword_scores.get(lang, 0)
            if score > 0:
                combined_scores[lang] = score
        
        # Schritt 4: Wähle beste Sprache
        if combined_scores:
            best_lang = max(combined_scores.items(), key=lambda x: x[1])[0]
            detected_accept = str(language_patterns[best_lang]['accept'])
            logging.info(f"🌍 Query-Sprache erkannt: {best_lang.upper()} (Score: {combined_scores[best_lang]}) → {detected_accept}")
            return detected_accept
        
        # Fallback: Neutral mit leichter Deutsch-Präferenz (default)
        logging.debug(f"🌍 Keine spezifische Sprache erkannt, verwende neutrales Accept-Language")
        return "de-DE,de;q=0.9,en;q=0.8,*;q=0.6"

    def _select_diagram_backend(self, description: Dict[str, Any]) -> str:
        """Choose diagram backend based on explicit settings and structural signals."""
        explicit_backend = str(description.get("backend") or description.get("engine") or "auto").strip().lower()
        if explicit_backend in {"graphviz", "dot"}:
            return "graphviz"
        if explicit_backend in {"native", "matplotlib"}:
            return "native"

        diagram_type = str(description.get("type", "network")).strip().lower()
        if diagram_type == "graphviz":
            return "graphviz"

        graphviz_fields = {"dot_code", "graph_type", "graph_attrs", "node_attrs", "edge_attrs", "rankdir", "clusters"}
        if any(field in description for field in graphviz_fields):
            return "graphviz"

        graphviz_types = {
            "dependency",
            "dependencies",
            "dependency_graph",
            "module_dependencies",
            "state",
            "state_machine",
            "statemachine",
            "uml",
            "classdiagram",
            "class_diagram",
            "er",
            "erd",
            "entity_relationship",
            "package_graph",
            "call_graph",
        }
        if diagram_type in graphviz_types:
            return "graphviz"

        return "native"

    def _prepare_graphviz_description(self, description: Dict[str, Any]) -> Dict[str, Any]:
        """Coerce common conceptual formats into a Graphviz-compatible description."""
        normalized = dict(description)
        normalized["type"] = "graphviz"

        if normalized.get("dot_code"):
            return normalized

        nodes = normalized.get("nodes")
        edges = normalized.get("edges")
        if isinstance(nodes, list) and isinstance(edges, list):
            return normalized

        # Fallback: flowchart-like steps -> directed graph sequence
        steps = normalized.get("steps")
        if isinstance(steps, list) and steps:
            gv_nodes: List[Dict[str, Any]] = []
            gv_edges: List[Dict[str, Any]] = []
            prev_id: Optional[str] = None
            for idx, step in enumerate(steps):
                if isinstance(step, dict):
                    node_id = str(step.get("id") or f"S{idx+1}")
                    node_label = str(step.get("label") or node_id)
                else:
                    node_id = f"S{idx+1}"
                    node_label = str(step)
                gv_nodes.append({"id": node_id, "label": node_label})
                if prev_id is not None:
                    gv_edges.append({"source": prev_id, "target": node_id})
                prev_id = node_id

            normalized["nodes"] = gv_nodes
            normalized["edges"] = gv_edges
            normalized.setdefault("graph_type", "digraph")
            return normalized

        return normalized

    def _create_diagram(self, params: Dict) -> Dict:
        """
        🎨 Erstellt professionelle Diagramme mit dem generischen Visualisierungs-Tool.
        **NEU:** Validiert Diagramme mit Vision-LLM und erstellt ggf. verbesserte Version
        
        Args:
            params: {
                "description": dict mit Diagramm-Beschreibung (JSON-Struktur)
                "output_filename": str (optional, default: "diagram.png")
            }
            
        Returns:
            {"success": bool, "output_path": str, "diagram_type": str, "validation": dict}
        """
        try:
            # Import des generischen Visualisierungs-Tools
            from generic_visualization_tool import GenericVisualizationTool
            from diagram_quality_validator import validate_and_improve_diagram
            
            # Parse Parameter
            description = params.get("description")
            if not description:
                return {"success": False, "error": "Keine Diagramm-Beschreibung angegeben"}
            
            # Normalize description: LLM kann str statt dict liefern
            if isinstance(description, str):
                try:
                    description = json.loads(description)
                except (json.JSONDecodeError, ValueError):
                    # Kein valides JSON -- als generische Netzwerk-Beschreibung wrappen
                    description = {"type": "network", "title": description}
                    logging.info(f"📊 Diagramm-Beschreibung war str → wrapped als network: {description['title'][:80]}")

            if not isinstance(description, dict):
                return {"success": False, "error": "Ungültige Diagramm-Beschreibung: Muss ein JSON-Objekt sein"}

            backend = self._select_diagram_backend(description)
            if backend == "graphviz":
                prepared = self._prepare_graphviz_description(description)
                has_graphviz_payload = bool(prepared.get("dot_code")) or (
                    isinstance(prepared.get("nodes"), list) and isinstance(prepared.get("edges"), list)
                )
                if has_graphviz_payload:
                    description = prepared
                    logging.info("📊 Diagramm-Backend automatisch gewählt: graphviz")
                else:
                    logging.info("📊 Graphviz angefragt, aber keine DOT/Nodes/Edges verfügbar -> fallback auf native")
                    backend = "native"
            
            output_filename = params.get("output_filename", "diagram.png")
            
            # Sicherstellen, dass der Ausgabepfad in einem sicheren Verzeichnis ist
            if not output_filename.endswith('.png'):
                output_filename += '.png'
            
            # ABSOLUTER Pfad + eindeutiger Name (verhindert Überschreibungen)
            import uuid
            unique_id = uuid.uuid4().hex[:8]
            base_name = os.path.splitext(os.path.basename(output_filename))[0]
            safe_filename = f"{base_name}_{unique_id}.png"
            output_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "generated_diagrams")
            os.makedirs(output_dir, exist_ok=True)
            output_filename = os.path.join(output_dir, safe_filename)
            
            # Erstelle Tool-Instanz
            viz_tool = GenericVisualizationTool()
            
            # Erstelle initiales Diagramm
            output_path = viz_tool.visualize(
                description=description,
                output_path=output_filename
            )
            
            diagram_type = description.get("type", "unknown") if isinstance(description, dict) else "unknown"
            
            # 🆕 VISION-VALIDIERUNG (nutzt model_loader direkt für multimodale Analyse)
            validation_result: Dict[str, Any] = {}
            if self._llm_client and getattr(self._llm_client, 'is_multimodal', False):
                try:
                    logging.info("🔍 Starte Vision-basierte Diagramm-Validierung...")
                    final_path, validation_result = validate_and_improve_diagram(
                        diagram_path=output_path,
                        diagram_description=description,
                        model_loader=self._llm_client,
                        max_iterations=2
                    )
                    
                    if final_path != output_path:
                        output_path = final_path
                        logging.info(f"✅ Verbessertes Diagramm erstellt: {final_path}")
                    
                except Exception as val_e:
                    logging.warning(f"⚠️ Diagramm-Validierung fehlgeschlagen: {val_e}")
                    validation_result = {"validation_skipped": True, "error": str(val_e)}
            
            return {
                "success": True,
                "output_path": output_path,
                "diagram_type": diagram_type,
                "backend": backend,
                "validation": validation_result,
                "message": f"✅ {diagram_type.upper()}-Diagramm erstellt: {output_path}" +
                          (f" (Qualität: {validation_result.get('quality_score', 'N/A')}%)" 
                           if validation_result.get('quality_score') else "")
            }
            
        except ImportError as e:
            logging.error(f"❌ GenericVisualizationTool konnte nicht importiert werden: {e}")
            return {
                "success": False,
                "error": f"Visualisierungs-Tool nicht verfügbar: {e}"
            }
        except Exception as e:
            logging.error(f"❌ Fehler beim Erstellen des Diagramms: {e}")
            return {
                "success": False,
                "error": f"Fehler beim Erstellen des Diagramms: {str(e)}"
            }

    def _canvas(self, params: Dict) -> Dict:
        """Canvas alias for conceptual diagram generation."""
        return self._create_diagram(params)

    # ------------------------------------------------------------------
    # Finance tools (Kontoauszug-Auswertungen aus dedizierter SQLite-DB)
    # ------------------------------------------------------------------

    @property
    def _finance_tools(self):
        """Lazy singleton wrapper around ``FinanceTools``.

        Lazy, weil das Finance-Subsystem die Finanz-DB on-import initialisiert
        (Schema-Migration). Wir wollen die DB-Datei nur dann anlegen, wenn
        tatsächlich ein Finance-Tool ausgeführt wird. Wenn der LLM-Client
        nachträglich via ``set_llm_client`` gesetzt wird, instanziieren wir
        FinanceTools neu, damit ``suggest_categories`` ihn nutzen kann.
        """
        ft = getattr(self, "_FinanceTools_cache", None)
        cached_llm = getattr(self, "_FinanceTools_cached_llm", None)
        if ft is None or cached_llm is not self._llm_client:
            from finance.tools import FinanceTools
            ft = FinanceTools(llm_client=self._llm_client)
            self._FinanceTools_cache = ft
            self._FinanceTools_cached_llm = self._llm_client
        return ft

    def _finance_list_accounts(self, params: Dict) -> Dict:
        return self._finance_tools.list_accounts(params)

    def _finance_get_schema_context(self, params: Dict) -> Dict:
        return self._finance_tools.get_schema_context(params)

    def _finance_sql_query(self, params: Dict) -> Dict:
        return self._finance_tools.sql_query(params)

    def _finance_search_transactions(self, params: Dict) -> Dict:
        return self._finance_tools.search_transactions(params)

    def _finance_query_transactions(self, params: Dict) -> Dict:
        return self._finance_tools.query_transactions(params)

    def _finance_aggregate(self, params: Dict) -> Dict:
        return self._finance_tools.aggregate(params)

    def _finance_sum_counterparty_costs(self, params: Dict) -> Dict:
        return self._finance_tools.sum_counterparty_costs(params)

    def _finance_sum_category_costs(self, params: Dict) -> Dict:
        return self._finance_tools.sum_category_costs(params)

    def _finance_cost_structure_analysis(self, params: Dict) -> Dict:
        return self._finance_tools.cost_structure_analysis(params)

    def _finance_recurring_expense_analysis(self, params: Dict) -> Dict:
        return self._finance_tools.recurring_expense_analysis(params)

    def _finance_expense_forecast(self, params: Dict) -> Dict:
        return self._finance_tools.expense_forecast(params)

    def _finance_expense_anomaly_detection(self, params: Dict) -> Dict:
        return self._finance_tools.expense_anomaly_detection(params)

    def _finance_top_counterparty_expenses(self, params: Dict) -> Dict:
        return self._finance_tools.top_counterparty_expenses(params)

    def _finance_balance_at(self, params: Dict) -> Dict:
        return self._finance_tools.balance_at(params)

    def _finance_list_categories(self, params: Dict) -> Dict:
        return self._finance_tools.list_categories(params)

    def _finance_assign_category(self, params: Dict) -> Dict:
        return self._finance_tools.assign_category(params)

    def _finance_suggest_categories(self, params: Dict) -> Dict:
        return self._finance_tools.suggest_categories(params)

    def _finance_list_rules(self, params: Dict) -> Dict:
        return self._finance_tools.list_rules(params)

    def _finance_apply_rules(self, params: Dict) -> Dict:
        return self._finance_tools.apply_rules(params)

    def _finance_set_budget(self, params: Dict) -> Dict:
        return self._finance_tools.set_budget(params)

    def _finance_budget_status(self, params: Dict) -> Dict:
        return self._finance_tools.budget_status(params)

    def _finance_budget_vs_actual_analysis(self, params: Dict) -> Dict:
        return self._finance_tools.budget_vs_actual_analysis(params)

    def _finance_savings_potential_analysis(self, params: Dict) -> Dict:
        return self._finance_tools.savings_potential_analysis(params)

    def _finance_expense_trend_break_detection(self, params: Dict) -> Dict:
        return self._finance_tools.expense_trend_break_detection(params)

    def _finance_monthly_report(self, params: Dict) -> Dict:
        return self._finance_tools.monthly_report(params)

    def _finance_list_transfer_candidates(self, params: Dict) -> Dict:
        return self._finance_tools.list_transfer_candidates(params)

    def _finance_link_transfer(self, params: Dict) -> Dict:
        return self._finance_tools.link_transfer(params)

    def _finance_unlink_transfer(self, params: Dict) -> Dict:
        return self._finance_tools.unlink_transfer(params)

    def _finance_list_transfer_links(self, params: Dict) -> Dict:
        return self._finance_tools.list_transfer_links(params)

    def _finance_detect_statement_settlement_gaps(self, params: Dict) -> Dict:
        return self._finance_tools.detect_statement_settlement_gaps(params)

    def _finance_list_statements_with_incomplete_balances(self, params: Dict) -> Dict:
        return self._finance_tools.list_statements_with_incomplete_balances(params)

    def _finance_check_statement_import_completeness(self, params: Dict) -> Dict:
        return self._finance_tools.check_statement_import_completeness(params)

    def _finance_repair_statement_header(self, params: Dict) -> Dict:
        return self._finance_tools.repair_statement_header(params)

    def _finance_relink_transfers(self, params: Dict) -> Dict:
        return self._finance_tools.relink_transfers(params)
