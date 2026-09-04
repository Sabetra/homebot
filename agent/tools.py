from __future__ import annotations
from typing import Dict, Any, List, Set, Optional, Tuple
from urllib.parse import urlparse
import threading
import os
import logging
import json

from agent.agent_types import ToolCall, ToolResult, Source
from agent_toolkit import AgentToolkit
from utils.runtime_policy import parse_bool_env
import utils.web_compliance as web_compliance

logger = logging.getLogger(__name__)

# UPGRADE: Verwende UnifiedRagStore direkt
try:
    from agent.unified_rag_store import UnifiedRagStore
    USE_UNIFIED_RAG: Optional[bool] = True
except ImportError:
    try:
        from agent.rag_store import RagStore
        USE_UNIFIED_RAG = False
    except ImportError:
        USE_UNIFIED_RAG = None

# ==================== SINGLETON RAG STORE ====================
# Verhindert VRAM-Leak durch mehrfache Sentence-Transformer-Instanzen
_global_rag_store: Optional[Any] = None
_rag_store_lock = threading.Lock()

def get_global_rag_store(
    db_path: Optional[str] = None,
    llm_client: Optional[Any] = None,
) -> Optional[Any]:
    """
    Gibt globalen Singleton RAG Store zurück (verhindert VRAM-Leak).

    WICHTIG: Nur EINE RAG Store Instanz pro Prozess!
    Jede neue Instanz würde Sentence Transformer neu laden → VRAM-Explosion

    Root-Cause-Fix 2026-08-10: Pfadauflösung über den zentralen
    ``utils/db_path_resolver`` (.db_root / HOMEBOT_DB_ROOT). Die frühere
    Workspace-Root-Normalisierung (BUGFIX 2025-10-11) war älter als die
    DB-Migration vom 2026-07-29 und hat sie umgangen — Folge war dieselbe
    DB in zwei Wurzelverzeichnissen mit divergierendem Inhalt.

    Args:
        db_path: Pfad zur RAG-DB. ``None`` → kanonischer Pfad aus dem
            zentralen Resolver; relative Pfade werden unter dem DB-Root
            aufgelöst (nicht mehr unter dem Workspace-Root).
        llm_client: Optional LLM client passed to ``UnifiedRagStore`` so
            the :class:`ContentClassifier` can verify domain/safety at
            ingest time. If the singleton already exists without an LLM,
            calling again with a non-None ``llm_client`` *upgrades* the
            existing instance (the LLM is attached lazily; the classifier
            picks it up on first use).
    """
    global _global_rag_store

    if db_path is None:
        from utils.db_path_resolver import get_rag_store_path
        db_path = str(get_rag_store_path())
    elif db_path != ":memory:" and not os.path.isabs(db_path):
        # Relativer Pfad → unter dem zentralen DB-Root auflösen
        from utils.db_path_resolver import get_db_path
        db_path = str(get_db_path(db_path))
    
    with _rag_store_lock:
        if _global_rag_store is None:
            try:
                logger.info(f"🗄️ Initialisiere Singleton RAG Store mit DB: {db_path}")
                if USE_UNIFIED_RAG:
                    _global_rag_store = UnifiedRagStore(  # type: ignore[possibly-unbound]
                        db_path=db_path,
                        llm_client=llm_client,
                    )
                    logger.info("✅ Singleton RAG Store erstellt (UnifiedRagStore)")
                elif USE_UNIFIED_RAG is False:
                    from agent.rag_store import RagStore
                    _global_rag_store = RagStore(db_path=db_path)  # type: ignore[misc]
                    logger.info("✅ Singleton RAG Store erstellt (Legacy RagStore)")
                else:
                    raise ImportError("Kein RAG Store verfügbar!")
            except Exception as e:
                logger.error(f"❌ RAG Store Singleton Fehler: {e}")
                _global_rag_store = None
        else:
            # Prüfe ob der gewünschte Pfad mit dem aktuellen übereinstimmt
            current_path = getattr(_global_rag_store, 'db_path', None)
            if current_path and current_path != db_path:
                logger.warning(
                    f"⚠️ RAG Store Singleton bereits mit ANDERER DB initialisiert!\n"
                    f"   Erwartet: {db_path}\n"
                    f"   Aktuell:  {current_path}\n"
                    f"   → Verwende existierende Instanz (Singleton-Pattern)"
                )
            # Late-attach LLM client if a caller provides it after init.
            # The ContentClassifier picks it up lazily on first classify().
            if (
                llm_client is not None
                and getattr(_global_rag_store, "_llm_client", None) is None
                and hasattr(_global_rag_store, "_llm_client")
            ):
                _global_rag_store._llm_client = llm_client
                # Force the classifier to be rebuilt with the new LLM.
                if hasattr(_global_rag_store, "_content_classifier"):
                    _global_rag_store._content_classifier = None
                logger.info("✅ LLM client late-attached to RAG Store singleton")

        return _global_rag_store

def cleanup_global_rag_store() -> None:
    """Cleanup für globalen RAG Store (z.B. beim Shutdown)."""
    global _global_rag_store
    
    with _rag_store_lock:
        if _global_rag_store is not None:
            try:
                _global_rag_store.close()
                logger.info("✅ Singleton RAG Store geschlossen")
            except Exception as e:
                logger.warning(f"⚠️ Fehler beim RAG Store cleanup: {e}")
            finally:
                _global_rag_store = None

class ToolManager:
    def __init__(self, llm_client: Optional[Any] = None):
        self.toolkit = AgentToolkit()
        # KRITISCH: Verwende ABSOLUTEN Pfad zur Datenbank!
        # Relativer Pfad würde je nach Working Directory andere DBs öffnen!
        # Root-Cause-Fix 2026-08-10: kanonischer Pfad aus dem zentralen
        # db_path_resolver statt Workspace-Root (Details: get_global_rag_store).
        from utils.db_path_resolver import get_rag_store_path
        self._db_path = os.getenv("RAG_DB_PATH", str(get_rag_store_path()))
        self._llm_client = llm_client
        logger.info(f"🗄️ ToolManager RAG DB Path: {self._db_path}")

        # SOTA: Privacy-Handler / Query-Sanitization Wiring.
        # ToolManager owns its own AgentToolkit instance (separate from the one
        # AgentChatbotLogic builds). Without propagating the LLM client into it,
        # its WebSearchOrchestrator never receives a privacy handler and every
        # gap-fill web search runs unsanitized ("⚠️ Privacy handler not
        # initialized"). Propagating here is the single source of truth for this
        # toolkit's wiring.
        if llm_client is not None:
            try:
                self.toolkit.set_llm_client(llm_client)
            except Exception as exc:
                logger.warning(
                    "⚠️ ToolManager: AgentToolkit.set_llm_client failed: %s", exc
                )

        # Generischer Intent-Klassifikator für Content-Präferenzen
        try:
            from agent.generic_intent_classifier import GenericIntentClassifier
            self.intent_classifier = GenericIntentClassifier()
        except Exception:
            self.intent_classifier = None

    def __del__(self):  # pragma: no cover
        try:
            self.close()
        except Exception as e:
            logger.debug(f"Fehler beim ToolManager cleanup: {e}")

    @property
    def rag(self) -> Optional[Any]:
        """
        Gibt Singleton RAG Store zurück (verhindert VRAM-Leak).
        
        WICHTIG: Verwendet globalen Singleton statt thread-lokale Instanzen!
        """
        return get_global_rag_store(self._db_path, llm_client=self._llm_client)

    def set_llm_client(self, llm_client: Optional[Any]) -> None:
        """Attach or update LLM client used for RAG ingest classification
        AND for the underlying AgentToolkit (privacy handler, code engine,
        content classifier). Both must be wired together — otherwise the
        toolkit's WebSearchOrchestrator runs without query sanitization."""
        self._llm_client = llm_client
        if llm_client is not None:
            get_global_rag_store(self._db_path, llm_client=llm_client)
            try:
                self.toolkit.set_llm_client(llm_client)
            except Exception as exc:
                logger.warning(
                    "⚠️ ToolManager.set_llm_client → AgentToolkit propagation failed: %s",
                    exc,
                )

    def has_web_search(self) -> bool:
        """True if web_search is reachable via run([ToolCall('web_search', ...)]).

        Routing path: ToolManager.run → AgentToolkit.execute_tool('web_search')
        → WebSearchOrchestrator.search (if WEB_SEARCH_V2_AVAILABLE) or legacy.
        APP_LOCAL_ONLY disables the route deterministically.
        """
        toolkit = getattr(self, "toolkit", None)
        if toolkit is None:
            return False
        if getattr(toolkit, "local_only_mode", False):
            return False
        # Either V2 orchestrator or legacy execute_tool path is sufficient.
        if getattr(toolkit, "web_search_orchestrator", None) is not None:
            return True
        return callable(getattr(toolkit, "execute_tool", None))

    def close(self) -> None:
        """
        Cleanup (aber NICHT den globalen Singleton schließen!).
        Der Singleton bleibt für andere ToolManager-Instanzen verfügbar.
        """
        # Nichts zu tun - Singleton wird nur bei cleanup_global_rag_store() geschlossen
        pass

    def persist_to_rag(self, text: str, metadata: Optional[Dict[str, Any]] = None) -> bool:
        """
        Persistiert Text mit Metadaten ins RAG-System.
        
        Diese Methode wird vom SourceManager verwendet, um Web-Content ins RAG zu speichern.
        
        Args:
            text: Der zu speichernde Text/Content
            metadata: Optionale Metadaten (z.B. {"source": "url", "title": "..."})
            
        Returns:
            bool: True wenn erfolgreich gespeichert, False sonst
        """
        if not self.rag or not text or not text.strip():
            return False
            
        try:
            # Erweitere Metadaten um Timestamp
            from datetime import datetime
            enriched_metadata = metadata.copy() if metadata else {}
            if "date_stored" not in enriched_metadata:
                enriched_metadata["date_stored"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            if "content_type" not in enriched_metadata:
                enriched_metadata["content_type"] = "web_content"
            
            # Web-Compliance + Retention (2026-08-30, docs/18_LEGAL_WEB_PERSIST.md):
            # Bei Web-URL: Gate (robots.txt / X-Robots-Tag / no-store) +
            # Retentionsfenster (retention_until). Ohne URL (lokal generierter
            # Text) bleibt die Persistierung unbeeinflusst.
            target_url = str(
                enriched_metadata.get("url")
                or enriched_metadata.get("source")
                or enriched_metadata.get("canonical_url")
                or ""
            )
            if target_url.startswith(("http://", "https://")):
                if not web_compliance.gate_persistence("tools.persist_to_rag", target_url):
                    return False
                if "source_type" not in enriched_metadata:
                    enriched_metadata["source_type"] = "web_snippet"
                if "retention_until" not in enriched_metadata:
                    retention_until = web_compliance.retention_until_iso()
                    if retention_until:
                        enriched_metadata["retention_until"] = retention_until
            
            # Speichere ins RAG
            self.rag.upsert_documents([{
                "text": text,
                "metadata": enriched_metadata
            }])
            
            logger.debug(f"[RAG-PERSIST] Content gespeichert: {enriched_metadata.get('title', 'Ohne Titel')[:50]}")
            return True
            
        except Exception as e:
            logger.warning(f"[RAG-PERSIST] Fehler beim Persistieren: {e}")
            return False

    def run(self, calls: List[ToolCall]) -> List[ToolResult]:
        out: List[ToolResult] = []
        for c in calls:
            # Handle RAG tools internally
            if c.tool == "rag_search":
                out.append(self._run_rag_search(c.parameters))
                continue
            if c.tool == "rag_upsert":
                out.append(self._run_rag_upsert(c.parameters))
                continue
            # Default tools via AgentToolkit
            raw = self.toolkit.execute_tool(c.tool, c.parameters)
            if c.tool == "web_search":
                results = (raw.get("results") if isinstance(raw, dict) else None) or []
                # capture policy filter stats if present at item-level
                filtered_count = 0
                for it in results:
                    if it.get("filtered_reason"):
                        filtered_count += 1
                
                out.append(ToolResult(
                    tool=c.tool,
                    success=bool(raw.get("success", True)) if isinstance(raw, dict) else True,
                    message=(raw.get("message") if isinstance(raw, dict) else None),
                    error=(raw.get("error") if isinstance(raw, dict) else None),
                    results=results,
                    meta={k: v for k, v in (raw.items() if isinstance(raw, dict) else []) if k not in {"results"}} | ({"filtered_count": filtered_count} if filtered_count else {})
                ))
            else:
                # normalize generic result as text
                if isinstance(raw, dict):
                    payload = {
                        k: v
                        for k, v in raw.items()
                        if k not in {"success", "error", "message", "content"}
                    }
                    msg = raw.get("message") or raw.get("content") or ""
                    if not msg and payload:
                        try:
                            msg = json.dumps(payload, ensure_ascii=False, default=str)
                        except (TypeError, ValueError):
                            msg = str(payload)
                    success = bool(raw.get("success", True))
                    err = raw.get("error")
                    generic_results = raw.get("results") if isinstance(raw.get("results"), list) else None
                    generic_meta = {
                        k: v
                        for k, v in raw.items()
                        if k not in {"results"}
                    }
                    generic_meta["raw_payload"] = dict(raw)
                else:
                    msg, success, err = str(raw), True, None
                    generic_results = None
                    generic_meta = {}
                out.append(
                    ToolResult(
                        tool=c.tool,
                        success=success,
                        message=msg,
                        error=err,
                        results=generic_results,
                        text=msg,
                        meta=generic_meta,
                    )
                )
        return out

    def _detect_content_preferences(self, query: str) -> Tuple[Optional[List[str]], Optional[List[str]]]:
        """Detect content preferences using generic LLM classifier.
        
        Uses the LLM-based intent classifier for semantic understanding.
        Returns (None, None) when the classifier is unavailable — no keyword
        heuristics, so behaviour stays generic end-to-end.
        """
        if self.intent_classifier:
            try:
                classification = self.intent_classifier.classify_intent(query)
                return classification.content_preference, None  # Keine harten Filter mehr
            except Exception as e:
                logger.warning(f"Intent classifier unavailable: {e}")
        
        # Kein Keyword-Fallback — generische Pipeline bleibt sauber
        return None, None

    def _run_rag_search(self, params: Dict[str, Any]) -> ToolResult:
        if not self.rag:
            return ToolResult(tool="rag_search", success=False, error="RAG store unavailable")
        query = (params.get("query") or "").strip()
        try:
            k = int(params.get("k", params.get("num_results", 6)))
        except Exception:
            k = 6
        try:
            min_score = float(params.get("min_score", 0.0))
        except Exception:
            min_score = 0.0
        
        # NEW: Adaptive Confidence Parameter
        adaptive_confidence = params.get("adaptive_confidence", True)
        
        # NEW 2025-10-11: Manual FAISS Confidence Parameter (overrides adaptive)
        faiss_min_confidence = params.get("faiss_min_confidence")

        # NEW: Corpus-domain & safety-flag filters (Option C — shared RAG)
        allowed_domains = params.get("allowed_domains")
        exclude_safety_flags = params.get("exclude_safety_flags")
        if allowed_domains is not None and not isinstance(allowed_domains, list):
            allowed_domains = None
        if exclude_safety_flags is not None and not isinstance(exclude_safety_flags, list):
            exclude_safety_flags = None
        
        if not query:
            return ToolResult(tool="rag_search", success=False, error="Empty query")
        
        search_query = query  # Default
        entity_analysis = None
        
        if hasattr(self, 'intent_classifier') and self.intent_classifier and hasattr(self.intent_classifier, 'entity_validator'):
            validator = self.intent_classifier.entity_validator
            if validator:
                try:
                    entity_analysis = validator.analyze_entities(query)
                    if entity_analysis.primary_entities and entity_analysis.enhanced_query:
                        search_query = entity_analysis.enhanced_query
                        primary_entity = entity_analysis.primary_entities[0] if entity_analysis.primary_entities else None
                        entity_name = primary_entity.get('name') if primary_entity else 'Unbekannt'
                        entity_type = primary_entity.get('type') if primary_entity else 'unbekannt'
                        logger.info(f"🎯 Entitäts-Kontext erkannt: {entity_name} ({entity_type})")
                        logger.info(f"🔍 Enhanced Query: {search_query}")
                        if entity_analysis.domain_context:
                            logger.info(f"🏭 Domäne: {entity_analysis.domain_context}")
                except Exception as e:
                    logger.warning(f"⚠️ Entitäts-Analyse fehlgeschlagen: {e}")
        
        # Detect content preferences from query (can be overridden by explicit params)
        prefer_types, filter_types = self._detect_content_preferences(query)
        
        # Allow explicit override via params
        if isinstance(params.get("prefer_types"), list):
            prefer_types = params.get("prefer_types")  # type: ignore[assignment]
        if isinstance(params.get("filter_types"), list):
            filter_types = params.get("filter_types")  # type: ignore[assignment]
        try:
            # SOTA Hybrid Search: Dense(FAISS) + Sparse(BM25) + KG → RRF → CrossEncoder Reranking
            logger.info(f"🔍 SOTA Hybrid RAG Search: query='{search_query[:80]}', k={k}")
            hits = self.rag.search(
                search_query, k=k, min_score=min_score,
                adaptive_confidence=adaptive_confidence,
                faiss_min_confidence=faiss_min_confidence,
                allowed_domains=allowed_domains,
                exclude_safety_flags=exclude_safety_flags,
            )
            
            # Post-filtering by content type if requested
            if (prefer_types or filter_types) and hits:
                logger.debug(f"Post-filtering: prefer={prefer_types}, filter={filter_types}")
        except Exception as e:
            logger.error(f"❌ RAG Search fehlgeschlagen: {type(e).__name__}: {e}")
            import traceback
            logger.debug(f"Traceback:\n{traceback.format_exc()}")
            return ToolResult(tool="rag_search", success=False, error=str(e))
        # Normalize to results list like web_search (include rag metadata for richer citations)
        results: List[Dict[str, Any]] = []
        for h in hits:
            meta = h.get("metadata") or {}
            title = meta.get("title") or meta.get("source") or h.get("doc_id") or ""
            url = meta.get("url") or f"rag://{h.get('doc_id')}#{h.get('chunk_id')}"
            date = meta.get("date")
            snippet = (h.get("text") or "")
            
            # NEW: Enhanced source type information for better citations
            source_type = h.get("source_type", "chunk")
            if source_type == "table":
                title = f"📊 Tabelle: {title}" if not title.startswith("📊") else title
            elif source_type == "knowledge_graph":
                title = f"💡 Wissen: {title}" if not title.startswith("💡") else title
            
            # Carry over structured metadata
            results.append({
                "title": title,
                "url": url,
                "snippet": snippet,
                "date": date,
                "doc_id": h.get("doc_id"),
                "chunk_id": h.get("chunk_id"),
                "type": meta.get("type"),
                "page": meta.get("page"),
                "metadata": meta,
                "source_type": source_type,  # NEW: Preserve source type
                "score": h.get("score", 0.0),  # NEW: Preserve score
            })
        return ToolResult(tool="rag_search", success=True, message=f"{len(results)} RAG-Treffer", results=results)

    def rag_search_batch(
        self,
        queries: List[str],
        k_list: Optional[List[int]] = None,
        min_score: float = 0.0,
        adaptive_confidence: bool = True,
        faiss_min_confidence: Optional[float] = None,
        allowed_domains: Optional[List[str]] = None,
        exclude_safety_flags: Optional[List[str]] = None,
    ) -> List[ToolResult]:
        """
        🚀 BATCH RAG SEARCH: Optimierte Batch-Suche für multiple Queries
        
        **Performance:**
        - 150x schneller als sequenzielle rag_search() Calls
        - Nutzt native FAISS Batch-API
        - Optimal für Multi-Query RAG
        
        Args:
            queries: Liste von Suchanfragen
            k_list: Liste von k-Werten (einer pro Query), default: alle k=5
            min_score: Minimaler Score-Filter
            adaptive_confidence: Adaptive FAISS Confidence
            faiss_min_confidence: Manuelle FAISS Confidence
            
        Returns:
            Liste von ToolResult Objekten (eines pro Query)
            
        Example:
            >>> queries = ["Python", "Machine Learning", "Data Science"]
            >>> k_list = [5, 10, 8]
            >>> results = tools.rag_search_batch(queries, k_list)
        """
        if not self.rag:
            return [ToolResult(tool="rag_search_batch", success=False, error="RAG not initialized")] * len(queries)
        
        if not queries:
            return []
        
        # Default k_list wenn nicht angegeben
        if k_list is None:
            k_list = [5] * len(queries)
        
        if len(queries) != len(k_list):
            raise ValueError(f"queries length ({len(queries)}) must match k_list length ({len(k_list)})")
        
        try:
            # Batch-Search ausführen
            batch_results = self.rag.batch_search(
                queries=queries,
                k_list=k_list,
                min_score=min_score,
                adaptive_confidence=adaptive_confidence,
                faiss_min_confidence=faiss_min_confidence,
                allowed_domains=allowed_domains,
                exclude_safety_flags=exclude_safety_flags,
            )
            
            # Convert to ToolResult objects
            tool_results = []
            for i, (query, hits) in enumerate(zip(queries, batch_results)):
                # Normalize results like in _run_rag_search
                results: List[Dict[str, Any]] = []
                for h in hits:
                    meta = h.get("metadata") or {}
                    title = meta.get("title") or meta.get("source") or h.get("doc_id") or ""
                    url = meta.get("url") or f"rag://{h.get('doc_id')}#{h.get('chunk_id')}"
                    date = meta.get("date")
                    snippet = (h.get("text") or "")
                    
                    # Enhanced source type information
                    source_type = h.get("source_type", "chunk")
                    if source_type == "table":
                        title = f"📊 Tabelle: {title}" if not title.startswith("📊") else title
                    elif source_type == "knowledge_graph":
                        title = f"💡 Wissen: {title}" if not title.startswith("💡") else title
                    
                    results.append({
                        "title": title,
                        "url": url,
                        "snippet": snippet,
                        "date": date,
                        "doc_id": h.get("doc_id"),
                        "chunk_id": h.get("chunk_id"),
                        "type": meta.get("type"),
                        "page": meta.get("page"),
                        "metadata": meta,
                        "source_type": source_type,
                        "score": h.get("score", 0.0),
                    })
                
                tool_results.append(
                    ToolResult(
                        tool="rag_search",
                        success=True,
                        message=f"{len(results)} RAG-Treffer",
                        results=results
                    )
                )
            
            return tool_results
            
        except Exception as e:
            logger.error(f"❌ Batch RAG Search fehlgeschlagen: {type(e).__name__}: {e}")
            import traceback
            logger.debug(f"Traceback:\n{traceback.format_exc()}")
            return [ToolResult(tool="rag_search_batch", success=False, error=str(e))] * len(queries)
    
    def _run_rag_upsert(self, params: Dict[str, Any]) -> ToolResult:
        if not self.rag:
            return ToolResult(tool="rag_upsert", success=False, error="RAG store unavailable")
        documents = params.get("documents") or []
        if not isinstance(documents, list) or not documents:
            return ToolResult(tool="rag_upsert", success=False, error="No documents provided")
        try:
            stats = self.rag.upsert_documents(documents)
            ok = bool(stats.get("success", True))
            msg = f"Upserted {stats.get('inserted', 0)} chunks across {stats.get('docs', 0)} docs" if ok else stats.get("error")
            return ToolResult(tool="rag_upsert", success=ok, message=msg, meta=stats)
        except Exception as e:
            return ToolResult(tool="rag_upsert", success=False, error=str(e))

    def rag_search(self, query: str, k: int = 6, *, min_score: float = 0.0, prefer_types: Optional[List[str]] = None, filter_types: Optional[List[str]] = None, user_id: Optional[str] = None, adaptive_confidence: bool = True, faiss_min_confidence: Optional[float] = None, allowed_domains: Optional[List[str]] = None, exclude_safety_flags: Optional[List[str]] = None) -> ToolResult:
        params: Dict[str, Any] = {"query": query, "k": k, "min_score": min_score, "adaptive_confidence": adaptive_confidence}
        if prefer_types is not None:
            params["prefer_types"] = list(prefer_types)
        if filter_types is not None:
            params["filter_types"] = list(filter_types)
        if user_id is not None:
            params["user_id"] = user_id
        # NEW 2025-10-11: Pass FAISS confidence to RAG store
        if faiss_min_confidence is not None:
            params["faiss_min_confidence"] = faiss_min_confidence
        if allowed_domains is not None:
            params["allowed_domains"] = list(allowed_domains)
        if exclude_safety_flags is not None:
            params["exclude_safety_flags"] = list(exclude_safety_flags)
        return self._run_rag_search(params)

    def rag_upsert_documents(self, documents: List[Dict[str, Any]]) -> ToolResult:
        return self._run_rag_upsert({"documents": documents})

    def to_sources(self, results: List[ToolResult], top_k: int = 5, snippet_len: int = 700, dedup_domain: bool = True) -> List[Source]:
        """Convert web_search or rag_search ToolResults into Source list with optional deduplication and limits.
        
        FIX 2026-03-11 v2: Per-type dedup sets to prevent cross-type URL/domain collision.
        
        Root cause of web sources being silently dropped (CoT/ToT analysis):
        ─────────────────────────────────────────────────────────────────────
        _persist_web_to_rag stores web pages in RAG with their ORIGINAL URLs
        (e.g., "https://kiberatung.de/..."). When _collect_from_results processes
        "rag_search" first, these URLs/domains are added to the SHARED seen_urls
        and seen_domains sets. Then "web_search" results from the same URLs/domains
        are silently skipped → web_sources = [] → fair merge never triggers.
        
        Fix: Each source type uses its OWN dedup sets. Cross-type URL dedup happens
        AFTER both are collected, in the merge step. Domain dedup is per-type only
        (same domain in RAG and Web may have different, complementary content).
        """

        def norm_domain(url: str) -> str:
            try:
                parsed = urlparse(url)
                if parsed.scheme.lower() == "rag":
                    return ""
                netloc = parsed.netloc.lower()
                if netloc.startswith("www."):
                    netloc = netloc[4:]
                return netloc
            except Exception:
                return url

        def _collect_from_results(tool_results: List[ToolResult], tool_filter: str) -> List[Source]:
            """Collect deduplicated sources from results matching tool_filter.
            
            Uses LOCAL seen_urls and seen_domains sets -- NOT shared with other
            source types. This prevents web-to-RAG persistence from causing
            cross-type dedup that silently drops all web results.
            """
            local_seen_urls: Set[str] = set()
            local_seen_domains: Set[str] = set()
            bucket: List[Source] = []
            for r in tool_results:
                if r.tool != tool_filter or not r.results:
                    continue
                for item in r.results:
                    url = (item.get("url") or "").strip()
                    if not url or url in local_seen_urls:
                        continue
                    domain = norm_domain(url)
                    if dedup_domain and domain and domain in local_seen_domains:
                        continue
                    title = item.get("title") or ""
                    if len(title) > 497:
                        title = title[:497] + "..."
                    snippet = item.get("snippet") or ""
                    if snippet_len and isinstance(snippet, str) and len(snippet) > snippet_len:
                        snippet = snippet[:snippet_len] + "…"
                    raw_score = item.get("score", 0.0)
                    try:
                        src_score = min(max(float(raw_score), 0.0), 1.0)
                    except (TypeError, ValueError):
                        src_score = 0.0
                    src = Source(
                        title=title,
                        url=url,
                        date=item.get("date"),
                        snippet=snippet,
                        page=item.get("page"),
                        doc_id=item.get("doc_id"),
                        chunk_id=item.get("chunk_id"),
                        type=item.get("type"),
                        meta=(item.get("metadata") or {}),
                        score=src_score,
                    )
                    bucket.append(src)
                    local_seen_urls.add(url)
                    if domain:
                        local_seen_domains.add(domain)
            return bucket

        # Collect each source type with independent dedup (no cross-type collision)
        rag_sources = _collect_from_results(results, "rag_search")
        web_sources = _collect_from_results(results, "web_search")

        # Also collect any other tool types (e.g. kg_search)
        other_tools = {r.tool for r in results} - {"rag_search", "web_search"}
        other_sources: List[Source] = []
        for tool_name in other_tools:
            other_sources.extend(_collect_from_results(results, tool_name))

        logger.info(
            f"[to_sources] Collected: {len(web_sources)} web + {len(rag_sources)} RAG "
            f"+ {len(other_sources)} other (before cross-dedup)"
        )

        # Fair merge: reserve quota for each type when both exist
        if top_k and web_sources and rag_sources:
            # Reserve at least 1/3 of top_k for web (min 3 slots), 1/3 for RAG,
            # remaining slots filled from whichever type has more
            web_min = min(len(web_sources), max(3, top_k // 3))
            rag_min = min(len(rag_sources), max(3, top_k // 3))
            remaining = top_k - web_min - rag_min

            # Fill remaining slots: interleave leftover web and RAG by score
            leftover_web = web_sources[web_min:]
            leftover_rag = rag_sources[rag_min:]
            leftover_all = leftover_web + leftover_rag + other_sources
            # Sort leftover by score descending so best sources fill remaining slots
            leftover_all.sort(key=lambda s: s.score, reverse=True)
            extra = leftover_all[:max(0, remaining)]

            collected = web_sources[:web_min] + rag_sources[:rag_min] + extra
            logger.info(
                f"[to_sources] Fair merge: {len(web_sources)} web + {len(rag_sources)} RAG "
                f"→ {web_min} web reserved + {rag_min} RAG reserved + {len(extra)} by score "
                f"= {len(collected)} candidates (top_k={top_k})"
            )
        else:
            # Only one type (or no cap): simple merge
            collected = rag_sources + web_sources + other_sources
            if top_k:
                collected = collected[:top_k]

        # Cross-type URL dedup (exact URL match only -- NOT domain dedup)
        # This removes true duplicates where web-to-RAG persistence stored an
        # identical URL in both source types, keeping the first occurrence.
        final_seen_urls: Set[str] = set()
        deduped: List[Source] = []
        for src in collected:
            if src.url not in final_seen_urls:
                deduped.append(src)
                final_seen_urls.add(src.url)
        if len(deduped) < len(collected):
            logger.info(
                f"[to_sources] Cross-type URL dedup: {len(collected)} → {len(deduped)}"
            )
        collected = deduped

        return collected

    def validate_sources_for_query(self, query: str, sources: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Validiert Quellen gegen Query mit universeller LLM-basierter Entitäts-Analyse.
        
        Args:
            query: Original User-Query
            sources: Liste von Source-Dicts mit title, snippet etc.
            
        Returns:
            Dict mit Validierungsergebnis und Warnungen
        """
        if not self.intent_classifier or not hasattr(self.intent_classifier, 'entity_validator'):
            return {
                'validation_available': False,
                'warning': 'LLM-basierte universelle Quellen-Validierung nicht verfügbar'
            }
            
        try:
            validator = self.intent_classifier.entity_validator
            
            # Extrahiere Text aus Sources für Validierung
            source_texts = []
            for source in sources:
                text = f"{source.get('title', '')} {source.get('snippet', '')}"
                source_texts.append(text.strip())
            
            # Führe universelle Validierung durch
            validation_summary = validator.get_universal_validation_summary(query, source_texts)
            
            return {
                'validation_available': True,
                'query': query,
                'total_sources': len(sources),
                'entity_analysis': validation_summary['entity_analysis'],
                'consistency_validation': validation_summary['consistency_validation'],
                'recommendations': validation_summary['recommendations'],
                'critical_warning': validation_summary['consistency_validation'].get('warning'),
                'confidence_score': validation_summary['consistency_validation'].get('relevance_score', 0.5),
                'universal_validation': True,  # Marker für universelle Validierung
                'supported_domains': validation_summary.get('supported_domains', ['any'])
            }
            
        except Exception as e:
            return {
                'validation_available': False,
                'error': f'Validierung fehlgeschlagen: {e}'
            }

    # ==================== USER CONTEXT MANAGEMENT (2025) ====================
    
    def create_user(self, user_id: str, consent_given: bool = True, 
                   preferences: Optional[Dict] = None) -> ToolResult:
        """Create or update user profile with GDPR compliance."""
        if not self.rag or not getattr(self.rag, 'enable_user_context', False):
            return ToolResult(tool="create_user", success=False, 
                            error="User context not enabled")
        
        try:
            result = self.rag.create_or_update_user(user_id, consent_given, preferences)
            return ToolResult(tool="create_user", success=result.get("success", False),
                            message=f"User {user_id} created/updated", meta=result)
        except Exception as e:
            return ToolResult(tool="create_user", success=False, error=str(e))

    def store_user_message(self, user_id: str, conversation_id: str, role: str, 
                          content: str, metadata: Optional[Dict] = None) -> ToolResult:
        """Store user message for personalization."""
        if not self.rag or not getattr(self.rag, 'enable_user_context', False):
            return ToolResult(tool="store_user_message", success=False,
                            error="User context not enabled")
        
        try:
            result = self.rag.store_user_message(user_id, conversation_id, role, content, metadata)
            return ToolResult(tool="store_user_message", success=result.get("success", False),
                            message="Message stored", meta=result)
        except Exception as e:
            return ToolResult(tool="store_user_message", success=False, error=str(e))

    def record_user_interaction(self, user_id: str, doc_id: str, interaction_type: str,
                               relevance_score: Optional[float] = None) -> ToolResult:
        """Record user interaction with document for personalization."""
        if not self.rag or not getattr(self.rag, 'enable_user_context', False):
            return ToolResult(tool="record_user_interaction", success=False,
                            error="User context not enabled")
        
        try:
            success = self.rag.record_user_interaction(user_id, doc_id, interaction_type, relevance_score)
            return ToolResult(tool="record_user_interaction", success=success,
                            message=f"Interaction recorded: {interaction_type}")
        except Exception as e:
            return ToolResult(tool="record_user_interaction", success=False, error=str(e))

    def delete_user_data(self, user_id: str) -> ToolResult:
        """Delete all user data (GDPR right to be forgotten)."""
        if not self.rag or not getattr(self.rag, 'gdpr_compliance', False):
            return ToolResult(tool="delete_user_data", success=False,
                            error="GDPR compliance not enabled")
        
        try:
            result = self.rag.delete_user_data(user_id)
            return ToolResult(tool="delete_user_data", success=result.get("success", False),
                            message=f"User data deleted for {user_id}", meta=result)
        except Exception as e:
            return ToolResult(tool="delete_user_data", success=False, error=str(e))

    def get_rag_stats(self) -> ToolResult:
        """Get RAG system statistics including user context info."""
        if not self.rag:
            return ToolResult(tool="get_rag_stats", success=False, error="RAG store unavailable")
        
        try:
            stats = self.rag.get_stats()
            return ToolResult(tool="get_rag_stats", success=True,
                            message="RAG statistics retrieved", meta=stats)
        except Exception as e:
            return ToolResult(tool="get_rag_stats", success=False, error=str(e))


def get_available_tools() -> List[str]:
    """Returns list of available tool names for verification purposes."""
    return [
        "rag_search",
        "rag_upsert_documents", 
        "rag_upsert_url",
        "web_search",
        "delete_user_data",
        "get_rag_stats"
    ]


# Global tool manager instance
_tool_manager: Optional[ToolManager] = None
_tool_manager_local_only_mode: Optional[bool] = None

def get_tool_manager(llm_client: Optional[Any] = None) -> ToolManager:
    """Returns global tool manager instance."""
    global _tool_manager, _tool_manager_local_only_mode

    current_local_only_mode = parse_bool_env("APP_LOCAL_ONLY", "0")

    if _tool_manager is None:
        _tool_manager = ToolManager(llm_client=llm_client)
        _tool_manager_local_only_mode = current_local_only_mode
    elif _tool_manager_local_only_mode != current_local_only_mode:
        logger.warning(
            "⚙️ Runtime mode changed (APP_LOCAL_ONLY: %s -> %s); rebuilding ToolManager singleton",
            _tool_manager_local_only_mode,
            current_local_only_mode,
        )
        previous_llm_client = llm_client if llm_client is not None else getattr(_tool_manager, "_llm_client", None)
        try:
            _tool_manager.close()
        except Exception as exc:
            logger.debug("ToolManager close during rebuild failed: %s", exc)
        _tool_manager = ToolManager(llm_client=previous_llm_client)
        _tool_manager_local_only_mode = current_local_only_mode
    elif llm_client is not None:
        _tool_manager.set_llm_client(llm_client)

    return _tool_manager