"""
Evidence Manager für Agenten-System
====================================

Zentrale Verwaltung von Evidence Collection, Ranking und Processing.

Features:
- Evidence Collection (Web + RAG)
- Cross-Encoder Reranking Integration
- Deduplication
- Quality Filtering
- Source Management Integration

Author: Implementation 2025-10-09
"""

from __future__ import annotations
from typing import List, Dict, Any, Optional, Tuple, Set
from dataclasses import dataclass, field
import logging
import hashlib
import re
import json

logger = logging.getLogger(__name__)

# Import Components
try:
    from agent.evidence_processor import EvidenceProcessor
    from agent.source_manager import SourceManager
    from agent.hybrid_reasoning import Evidence
    EVIDENCE_COMPONENTS_AVAILABLE = True
except ImportError as e:
    logger.warning(f"Evidence components nicht verfügbar: {e}")
    EVIDENCE_COMPONENTS_AVAILABLE = False
    Evidence = None  # type: ignore

try:
    from models_pydantic_v2 import DistilledFactBatchModel
except ImportError:
    DistilledFactBatchModel = None  # type: ignore[assignment]


@dataclass
class EvidenceCollectionResult:
    """Ergebnis der Evidence Collection"""
    evidences: List[Any]
    total_collected: int
    total_deduplicated: int
    total_ranked: int
    sources_web: int
    sources_rag: int
    processing_time_ms: float
    metadata: Dict[str, Any]


@dataclass
class SourceSelectionResult:
    """Result of source selection pipeline"""
    sources: List[Any]
    candidates_count: int
    ranked_count: int
    shortlist_count: int
    final_count: int
    web_sources_count: int
    rag_sources_count: int
    processing_time_ms: float
    validation_stats: Dict[str, Any] = field(default_factory=dict)


class EvidenceDeduplicator:
    """SOTA Semantic Evidence Deduplication.
    
    Two-phase approach:
    1. Fast MD5 hash → exact duplicate removal (O(1) per item)
    2. Embedding cosine similarity → near-duplicate removal (O(n²) but n is small after phase 1)
    
    Falls back to MD5-only if embedding model is unavailable.
    """
    
    def __init__(self, similarity_threshold: float = 0.92, use_semantic: bool = True):
        """
        Args:
            similarity_threshold: Cosine similarity threshold for semantic dedup (0-1).
                                  0.92 is aggressive enough to catch paraphrases but safe enough
                                  to keep genuinely different evidence.
            use_semantic: Whether to use embedding-based dedup (falls back to MD5 if unavailable)
        """
        self.similarity_threshold = similarity_threshold
        self.use_semantic = use_semantic
        self._embedding_model = None
        self._semantic_available = False
        
        if use_semantic:
            try:
                from utils.embedding_singleton import get_embedding_model
                self._embedding_model_fn = get_embedding_model
                self._semantic_available = True
            except ImportError:
                logger.debug("Embedding model not available for semantic dedup -- using MD5 fallback")
    
    def deduplicate(self, evidences: List[Any]) -> Tuple[List[Any], int]:
        """
        Remove duplicates using two-phase deduplication.
        
        Returns:
            (unique_evidences, num_duplicates_removed)
        """
        if not evidences:
            return [], 0
        
        # Phase 1: Exact duplicate removal via MD5 hash
        phase1_unique = []
        seen_hashes: Set[str] = set()
        exact_dupes = 0
        
        for ev in evidences:
            content = getattr(ev, 'content', '') or getattr(ev, 'text', '')
            content_hash = self._hash_content(content)
            
            if content_hash not in seen_hashes:
                phase1_unique.append(ev)
                seen_hashes.add(content_hash)
            else:
                exact_dupes += 1
        
        # Phase 2: Semantic near-duplicate removal
        if self._semantic_available and len(phase1_unique) > 1:
            try:
                unique, semantic_dupes = self._semantic_dedup(phase1_unique)
                total_removed = exact_dupes + semantic_dupes
                logger.info(
                    f"🔄 Deduplication: {len(evidences)} → {len(unique)} "
                    f"({exact_dupes} exact + {semantic_dupes} semantic duplicates)"
                )
                return unique, total_removed
            except Exception as e:
                logger.warning(f"Semantic dedup failed, using MD5-only results: {e}", exc_info=True)
        
        logger.info(f"🔄 Deduplication: {len(evidences)} → {len(phase1_unique)} ({exact_dupes} exact duplicates)")
        return phase1_unique, exact_dupes
    
    def _semantic_dedup(self, evidences: List[Any]) -> Tuple[List[Any], int]:
        """Phase 2: Remove near-duplicates via embedding cosine similarity."""
        import numpy as np
        
        model = self._embedding_model_fn()
        
        # Extract content texts
        texts = []
        for ev in evidences:
            content = getattr(ev, 'content', '') or getattr(ev, 'text', '')
            # Use first 512 chars for embedding efficiency
            texts.append(content[:512])
        
        # Batch encode all texts and normalize explicitly for cosine similarity.
        embeddings = model.encode(texts, show_progress_bar=False)
        embeddings = np.asarray(embeddings)
        norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        embeddings = embeddings / norms
        
        # Greedy selection: keep evidence if not too similar to any already-kept evidence
        keep_indices = [0]  # Always keep the first (highest-ranked) evidence
        removed = 0
        
        for i in range(1, len(embeddings)):
            is_duplicate = False
            for j in keep_indices:
                similarity = float(np.dot(embeddings[i], embeddings[j]))
                if similarity >= self.similarity_threshold:
                    is_duplicate = True
                    break
            
            if not is_duplicate:
                keep_indices.append(i)
            else:
                removed += 1
        
        unique = [evidences[i] for i in keep_indices]
        return unique, removed
    
    def _hash_content(self, content: str) -> str:
        """Create content hash for exact duplicate detection.
        
        Uses FULL normalized content (not truncated) for accurate matching.
        """
        normalized = ' '.join(content.lower().split())
        return hashlib.md5(normalized.encode()).hexdigest()


class EvidenceQualityFilter:
    """
    SOTA Evidence-Quality-Filter (3-Layer Defense-in-Depth)
    
    Layer 3 der Content-Quality-Pipeline:
    Filtert Noise-Chunks die trotz Trafilatura (Layer 1) und 
    Chunk-Validator (Layer 2) durchgekommen sind -- z.B. aus
    bereits existierenden Chunks im Store.
    
    Prüft:
    - Score, Länge, Leer-Content (Original)
    - Content-Informationsgehalt (NEU)
    - Code/Tracking/Noise-Erkennung (NEU)
    """
    
    # Kompilierte Patterns (Class-Level, einmalig)
    _NOISE_PATTERNS = re.compile(
        r'(?:'
        r'function\s*\([^)]*\)\s*\{'
        r'|var\s+\w+\s*='
        r'|const\s+\w+\s*='
        r'|let\s+\w+\s*='
        r'|=>\s*\{'
        r'|document\.\w+'
        r'|window\.\w+'
        r'|getElementById'
        r'|addEventListener'
        r'|querySelector'
        r'|\.prototype\.'
        r'|gtag\s*\('
        r'|dataLayer\s*\.'
        r'|fbq\s*\('
        r'|_linkedin_partner_id'
        r'|className\s*='
        r'|\{\s*display\s*:'
        r'|@media\s*\('
        r'|\.replace\(\s*(?:new\s+RegExp|/)'
        r'|\.push\(\s*arguments\s*\)'
        r')',
        re.IGNORECASE
    )
    
    def __init__(
        self,
        min_score: float = 0.1,
        min_length: int = 20,
        max_length: int = 5000
    ):
        """
        Args:
            min_score: Minimaler Score
            min_length: Minimale Content-Länge
            max_length: Maximale Content-Länge
        """
        self.min_score = min_score
        self.min_length = min_length
        self.max_length = max_length
    
    def _is_noise_content(self, content: str) -> bool:
        """
        Erkennt nicht-natürlichsprachlichen Content in Evidence-Chunks.
        
        Verwendet die gleiche Logik wie der Chunk-Validator im RAG Store,
        aber als letzte Sicherheitslinie bei der Retrieval-Phase.
        
        Returns:
            True wenn Content als Noise erkannt wird.
        """
        if not content or len(content) < 10:
            return True
        
        text = content.strip()
        text_len = len(text)
        
        # Signal 1: Code-Pattern-Häufigkeit
        code_matches = len(self._NOISE_PATTERNS.findall(text))
        
        # Signal 2: Alphabetischer Anteil
        alpha_chars = sum(1 for c in text if c.isalpha())
        alpha_ratio = alpha_chars / max(text_len, 1)
        
        # Signal 3: Code-Sonderzeichen-Dichte
        code_chars = sum(1 for c in text if c in '{}()[];=<>|&!~^`\\')
        code_char_ratio = code_chars / max(text_len, 1)
        
        # Entscheidung
        noise_score = 0.0
        
        if code_matches >= 5:
            noise_score += 0.7
        elif code_matches >= 3:
            noise_score += 0.4
        elif code_matches >= 1:
            noise_score += 0.15
        
        if alpha_ratio < 0.40:
            noise_score += 0.5
        elif alpha_ratio < 0.55:
            noise_score += 0.25
        
        if code_char_ratio > 0.10:
            noise_score += 0.4
        elif code_char_ratio > 0.05:
            noise_score += 0.15
        
        return noise_score >= 0.65
    
    def filter(self, evidences: List[Any]) -> Tuple[List[Any], int]:
        """
        Filtert low-quality Evidences
        
        Returns:
            (filtered_evidences, num_filtered)
        """
        if not evidences:
            return [], 0
        
        filtered = []
        filtered_count = 0
        noise_filtered = 0
        
        for ev in evidences:
            # Score Check
            score = getattr(ev, 'score', 1.0)
            if score < self.min_score:
                filtered_count += 1
                continue
            
            # Length Check
            content = getattr(ev, 'content', '') or getattr(ev, 'text', '')
            if len(content) < self.min_length or len(content) > self.max_length:
                filtered_count += 1
                continue
            
            # Empty Content Check
            if not content.strip():
                filtered_count += 1
                continue
            
            # LAYER 3: Content-Quality-Check (Noise-Erkennung)
            if self._is_noise_content(content):
                filtered_count += 1
                noise_filtered += 1
                logger.debug(f"[NOISE] Evidence rejected: '{content[:80]}...'")
                continue
            
            filtered.append(ev)
        
        noise_info = f", {noise_filtered} noise" if noise_filtered > 0 else ""
        logger.info(f"🔍 Quality Filter: {len(evidences)} → {len(filtered)} ({filtered_count} gefiltert{noise_info})")
        
        return filtered, filtered_count


class EvidenceManager:
    """
    Zentrale Evidence-Verwaltung
    
    Koordiniert:
    - Evidence Collection (Web + RAG)
    - Deduplication
    - Quality Filtering
    - Cross-Encoder Reranking
    - Source Management
    """
    
    def __init__(
        self,
        evidence_processor: Optional[Any] = None,
        source_manager: Optional[Any] = None,
        tools_manager: Optional[Any] = None
    ):
        """
        Args:
            evidence_processor: EvidenceProcessor für Ranking
            source_manager: SourceManager für Source-Handling
            tools_manager: ToolManager für RAG/Web-Search
        """
        self.evidence_processor = evidence_processor
        self.source_manager = source_manager
        self.tools = tools_manager
        
        # Sub-Komponenten
        self.deduplicator = EvidenceDeduplicator(similarity_threshold=0.85)
        self.quality_filter = EvidenceQualityFilter(
            min_score=0.1,
            min_length=20,
            max_length=5000
        )
        
        logger.info("✅ EvidenceManager initialisiert")
    
    def collect_evidences(
        self,
        query: str,
        k: int = 6,
        use_web: bool = True,
        use_rag: bool = True,
        use_cross_encoder: bool = True,
        sub_queries: Optional[List[str]] = None
    ) -> EvidenceCollectionResult:
        """
        Sammelt Evidences aus allen Quellen
        
        Args:
            query: Haupt-Query
            k: Anzahl Evidences
            use_web: Web-Search verwenden
            use_rag: RAG verwenden
            use_cross_encoder: Cross-Encoder Reranking verwenden
            sub_queries: Optional Sub-Queries für Multi-Query Search
            
        Returns:
            EvidenceCollectionResult
        """
        import time
        start_time = time.time()
        
        logger.info(f"📦 Evidence Collection: k={k}, Web={use_web}, RAG={use_rag}")
        
        all_evidences = []
        web_count = 0
        rag_count = 0
        
        # 1. RAG Evidences
        if use_rag and self.tools:
            try:
                rag_evidences = self._collect_rag_evidences(query, k, sub_queries)
                all_evidences.extend(rag_evidences)
                rag_count = len(rag_evidences)
                logger.info(f"   RAG: {rag_count} evidences")
            except Exception as e:
                logger.warning(f"RAG Collection fehlgeschlagen: {e}")
        
        # 2. Web Evidences
        if use_web and self.tools:
            try:
                web_evidences = self._collect_web_evidences(query, k, sub_queries)
                all_evidences.extend(web_evidences)
                web_count = len(web_evidences)
                logger.info(f"   Web: {web_count} evidences")
            except Exception as e:
                logger.warning(f"Web Collection fehlgeschlagen: {e}")
        
        total_collected = len(all_evidences)
        logger.info(f"   Total Collected: {total_collected}")
        
        # 3. Deduplication
        unique_evidences, duplicates = self.deduplicator.deduplicate(all_evidences)
        
        # 4. Quality Filtering
        filtered_evidences, filtered_count = self.quality_filter.filter(unique_evidences)
        
        # 5. Cross-Encoder Reranking (wenn verfügbar)
        if use_cross_encoder and self.evidence_processor and filtered_evidences:
            try:
                ranked_evidences = self.evidence_processor.rank_and_optimize(
                    evidences=filtered_evidences,
                    query=query,
                    target_m=k,
                    diversity_lambda=0.7,
                    use_cross_encoder=True
                )
                logger.info(f"   Cross-Encoder Ranked: {len(ranked_evidences)}")
            except Exception as e:
                logger.warning(f"Cross-Encoder Reranking fehlgeschlagen: {e}")
                ranked_evidences = filtered_evidences[:k]
        else:
            # Fallback: Top-k by score
            ranked_evidences = sorted(
                filtered_evidences,
                key=lambda ev: getattr(ev, 'score', 0.0),
                reverse=True
            )[:k]
        
        processing_time_ms = (time.time() - start_time) * 1000
        
        result = EvidenceCollectionResult(
            evidences=ranked_evidences,
            total_collected=total_collected,
            total_deduplicated=len(unique_evidences),
            total_ranked=len(ranked_evidences),
            sources_web=web_count,
            sources_rag=rag_count,
            processing_time_ms=processing_time_ms,
            metadata={
                'duplicates_removed': duplicates,
                'quality_filtered': filtered_count,
                'cross_encoder_used': use_cross_encoder and self.evidence_processor is not None
            }
        )
        
        logger.info(f"✅ Evidence Collection Complete: {len(ranked_evidences)} evidences in {processing_time_ms:.1f}ms")
        
        return result
    
    def _collect_rag_evidences(
        self,
        query: str,
        k: int,
        sub_queries: Optional[List[str]] = None
    ) -> List[Any]:
        """Sammelt RAG Evidences"""
        evidences = []
        
        # Main Query RAG
        try:
            if self.tools is None:
                return []
            rag_result = self.tools.rag_search(query, k=k)  # type: ignore
            if rag_result:
                evidences.extend(self._convert_to_evidences(rag_result, source_type='rag'))
        except Exception as e:
            logger.warning(f"RAG Search fehlgeschlagen: {e}")
        
        # Sub-Queries RAG (wenn vorhanden)
        if sub_queries:
            sub_k = max(2, k // len(sub_queries))  # Teile k auf
            for sub_q in sub_queries:
                try:
                    if self.tools is None:
                        continue
                    sub_result = self.tools.rag_search(sub_q, k=sub_k)  # type: ignore
                    if sub_result:
                        evidences.extend(self._convert_to_evidences(sub_result, source_type='rag'))
                except Exception as e:
                    logger.warning(f"RAG Sub-Query Search fehlgeschlagen: {e}")
        
        return evidences
    
    def _collect_web_evidences(
        self,
        query: str,
        k: int,
        sub_queries: Optional[List[str]] = None
    ) -> List[Any]:
        """Sammelt Web Evidences"""
        evidences = []
        
        # Main Query Web Search
        try:
            if self.tools is None:
                return []
            web_result = self.tools.web_search(query, k=k)  # type: ignore
            if web_result:
                evidences.extend(self._convert_to_evidences(web_result, source_type='web'))
        except Exception as e:
            logger.warning(f"Web Search fehlgeschlagen: {e}")
        
        # Sub-Queries Web Search (optional, bei Multi-Query)
        if sub_queries and len(sub_queries) <= 3:  # Limit für Web-Calls
            sub_k = max(2, k // len(sub_queries))
            for sub_q in sub_queries[:3]:  # Max 3 Sub-Queries für Web
                try:
                    if self.tools is None:
                        continue
                    sub_result = self.tools.web_search(sub_q, k=sub_k)  # type: ignore
                    if sub_result:
                        evidences.extend(self._convert_to_evidences(sub_result, source_type='web'))
                except Exception as e:
                    logger.warning(f"Web Sub-Query Search fehlgeschlagen: {e}")
        
        return evidences
    
    def _convert_to_evidences(
        self,
        results: Any,
        source_type: str = 'unknown'
    ) -> List[Any]:
        """Konvertiert Search Results zu Evidence-Format"""
        evidences = []
        
        # Handle verschiedene Result-Typen
        if isinstance(results, list):
            items = results
        elif hasattr(results, 'results'):
            items = results.results
        elif hasattr(results, 'chunks'):
            items = results.chunks
        else:
            items = [results]
        
        for item in items:
            # Extrahiere Felder
            if hasattr(item, 'content'):
                content = item.content
            elif hasattr(item, 'text'):
                content = item.text
            elif hasattr(item, 'chunk'):
                content = item.chunk
            elif isinstance(item, str):
                content = item
            else:
                content = str(item)
            
            score = getattr(item, 'score', getattr(item, 'relevance_score', 0.5))
            source_url = getattr(item, 'url', getattr(item, 'source', 'unknown'))
            
            # Erstelle Evidence-Objekt
            if Evidence is not None:
                ev = Evidence(
                    content=content,
                    source=source_url,
                    score=float(score),
                    domain=self._extract_domain(source_url)
                )
                evidences.append(ev)
            else:
                # Fallback: Dict
                evidences.append({  # type: ignore[unreachable]
                    'content': content,
                    'source': source_url,
                    'score': float(score),
                    'type': source_type
                })
        
        return evidences
    
    def _extract_domain(self, url: str) -> str:
        """Extrahiert Domain aus URL"""
        try:
            from urllib.parse import urlparse
            parsed = urlparse(url)
            domain = parsed.netloc
            # Entferne www.
            if domain.startswith('www.'):
                domain = domain[4:]
            return domain
        except Exception:
            return 'unknown'
    
    def select_evidence_from_tool_results(
        self,
        query: str,
        tool_results: List[Any],
        evidence_max_candidates: int = 20,
        evidence_shortlist_m: int = 12,
        evidence_diversity_lambda: float = 0.7,
        is_news_query: bool = False,
        news_min_k: int = 5,
        news_max_k: int = 10,
        model_loader: Optional[Any] = None,
        use_llm_evidence_selection: bool = True,
        validation_enabled: bool = True,
        validation_max_iterations: int = 2,
        validation_min_sources: int = 3
    ) -> 'SourceSelectionResult':
        """
        Orchestrator-kompatible Evidence-Selection Pipeline
        
        Führt vollständige Evidence-Pipeline aus:
        1. Tool Results → Source Candidates (via tools.to_sources)
        2. Source Validation & Extension (optional)
        3. Ranking mit Scores
        4. Shortlist Selection (Top M)
        5. Per-Source Summarization
        6. Relevance Judging (LLM oder Rule-based)
        7. Adaptive Top-K Selection
        8. Diversity-aware Final Selection (MMR)
        
        Args:
            query: User query
            tool_results: List of ToolResult objects
            evidence_max_candidates: Max candidates before ranking
            evidence_shortlist_m: Shortlist size (M)
            evidence_diversity_lambda: Diversity weight (0-1)
            is_news_query: Whether this is a news query
            news_min_k: Min sources for news queries
            news_max_k: Max sources for news queries
            model_loader: Model for LLM-based operations
            use_llm_evidence_selection: Use LLM for relevance judging
            validation_enabled: Enable intelligent source validation
            validation_max_iterations: Max validation iterations
            validation_min_sources: Min relevant sources required
            
        Returns:
            SourceSelectionResult with selected sources and stats
        """
        import time
        start_time = time.time()
        
        logger.info("📊 Evidence Selection Pipeline gestartet")
        
        # STEP 1: Convert tool results to Source candidates
        if self.tools is None:
            logger.warning("Tools Manager nicht verfügbar - keine Evidence-Selection möglich")
            return SourceSelectionResult(
                sources=[],
                candidates_count=0,
                ranked_count=0,
                shortlist_count=0,
                final_count=0,
                web_sources_count=0,
                rag_sources_count=0,
                processing_time_ms=0.0
            )
        
        candidates = self.tools.to_sources(
            tool_results,
            top_k=evidence_max_candidates,
            snippet_len=5000,  # ✅ STATE-OF-ART: Preserve full context (was 400 → 50% loss!)
            dedup_domain=True,
        )
        
        logger.info(f"   Step 1: {len(candidates)} candidates from tool results")
        
        # STEP 2: Source Validation & Extension (if enabled and orchestrator methods available)
        validation_stats: Dict[str, Any] = {
            "initial_sources": len(candidates),
            "iterations": 0,
            "web_searches": 0,
            "rag_searches": 0,
            "rejected_sources": 0,
            "final_sources": 0,
            "validation_reason": "skipped"
        }
        
        if validation_enabled and hasattr(self, '_orchestrator_delegate'):
            try:
                validated_sources, validation_stats = self._orchestrator_delegate.validate_and_extend_sources(
                    query, candidates, validation_max_iterations, validation_min_sources
                )
                candidates = validated_sources
                logger.info(f"   Step 2: Source validation: {validation_stats['initial_sources']} → {validation_stats['final_sources']} sources")
            except Exception as e:
                logger.warning(f"Source validation fehlgeschlagen: {e}")
                validation_stats["validation_reason"] = "error"
        else:
            validation_stats["final_sources"] = len(candidates)
            logger.info(f"   Step 2: Source validation skipped (not enabled or delegate unavailable)")
        
        if not candidates:
            logger.warning("Keine Candidates nach Validation - returning empty result")
            return SourceSelectionResult(
                sources=[],
                candidates_count=0,
                ranked_count=0,
                shortlist_count=0,
                final_count=0,
                web_sources_count=0,
                rag_sources_count=0,
                processing_time_ms=(time.time() - start_time) * 1000,
                validation_stats=validation_stats
            )
        
        # STEP 3: Ranking with scores
        if self.evidence_processor:
            ranked_with_scores = self.evidence_processor.rank_with_scores(query, candidates)
        else:
            # Fallback: simple scoring
            ranked_with_scores = [(src, 0.5) for src in candidates]
        
        logger.info(f"   Step 3: {len(ranked_with_scores)} sources ranked")
        
        # STEP 3.1: Sort by score descending so shortlist gets BEST sources, not just first M
        ranked_with_scores.sort(key=lambda x: x[1], reverse=True)
        
        # STEP 3.2: Propagate ranking scores to source objects
        # ✅ FIX: Without this, SourceModel.score stays at 0.0 (default),
        # causing downstream Quality Filters to drop ALL evidences
        for src, rank_score in ranked_with_scores:
            try:
                src.score = min(max(float(rank_score), 0.0), 1.0)
            except (AttributeError, ValueError, TypeError) as e:
                logger.debug(f"Ranking-Score-Propagation fehlgeschlagen: {e}")
        
        if ranked_with_scores:
            best_score = ranked_with_scores[0][1]
            worst_score = ranked_with_scores[-1][1]
            logger.info(f"   Step 3.1-3.2: Sorted & propagated scores (best={best_score:.3f}, worst={worst_score:.3f})")
        
        # ✅ STEP 3.5: ADAPTIVE TOKEN BUDGET ALLOCATION (STATE-OF-ART)
        # Allocate more context to top-ranked sources, less to lower-ranked ones
        # This prevents token budget explosion while maximizing information from best sources
        ranked_with_scores = self._apply_adaptive_budget_allocation(
            ranked_with_scores, 
            max_snippet_len=5000,  # Upper bound (already set in to_sources)
            min_snippet_len=800,   # Lower bound for low-ranked sources
            decay_factor=0.7       # Exponential decay for lower ranks
        )
        logger.info(f"   Step 3.5: Applied adaptive budget allocation to {len(ranked_with_scores)} sources")
        
        # STEP 4: Shortlist selection (Top M)
        M = min(evidence_shortlist_m, len(ranked_with_scores))
        shortlist = [src for src, _ in ranked_with_scores[:M]]
        
        logger.info(f"   Step 4: {M} sources in shortlist")
        
        # STEP 5: Per-source summarization (now using local method)
        try:
            per_source_summary = self._summarize_sources(query, shortlist, model_loader)
            logger.info(f"   Step 5: {len(per_source_summary)} source summaries generated")
        except Exception as e:
            logger.warning(f"Source summarization fehlgeschlagen: {e}")
            # Fallback: use snippets
            per_source_summary = {}
            for s in shortlist:
                key = getattr(s, 'url', None) or f"idx:{id(s)}"
                snippet = getattr(s, 'snippet', '') or ''
                per_source_summary[key] = str(snippet)[:400]
            logger.info(f"   Step 5: Using {len(per_source_summary)} source snippets (fallback)")
        
        # STEP 6: Relevance judging (now using local method with LLM or rule-based)
        try:
            # Get evidence_selector from orchestrator delegate if available
            evidence_selector = getattr(self, '_orchestrator_delegate', None)
            judged = self._judge_relevance_rule(
                query, shortlist, per_source_summary,
                use_llm_evidence_selection=use_llm_evidence_selection,
                evidence_selector=evidence_selector
            )
            logger.info(f"   Step 6: {len(judged)} sources judged for relevance")
        except Exception as e:
            logger.warning(f"Relevance judging fehlgeschlagen: {e}")
            # Fallback: use ranking scores or default scoring
            judged = [(src, score) for src, score in ranked_with_scores[:M]] if ranked_with_scores else [(src, 0.5) for src in shortlist]
        
        # STEP 6.5: Propagate final relevance scores to source objects
        # ✅ CRITICAL FIX: Without this, SourceModel.score stays at its default (0.0)
        # causing the Cross-Encoder Quality Filter (score > 0.1) in rank_and_optimize()
        # to drop ALL evidences in Hybrid Reasoning → zero grounding.
        # The evidence selection pipeline computes accurate relevance scores but only
        # carries them as tuple values -- they must be written back to the source objects
        # so downstream consumers see the actual scores.
        scores_propagated = 0
        for src, relevance_score in judged:
            try:
                src.score = min(max(float(relevance_score), 0.0), 1.0)
                scores_propagated += 1
            except (AttributeError, ValueError, TypeError) as e:
                logger.debug(f"Relevance-Score-Propagation fehlgeschlagen: {e}")
        if judged:
            best_judged = max(s for _, s in judged)
            logger.info(f"   Step 6.5: Propagated relevance scores to {scores_propagated}/{len(judged)} sources (best={best_judged:.3f})")
        
        # STEP 7: Adaptive top-K selection
        if self.evidence_processor:
            K = self.evidence_processor.adaptive_top_k(len(judged))
        else:
            K = min(6, len(judged))  # Fallback default
        
        # News query adjustment
        if is_news_query:
            K = min(max(K, news_min_k), min(news_max_k, len(judged)))
        
        logger.info(f"   Step 7: Adaptive K={K} (news_query={is_news_query})")
        
        # STEP 8: Diversity-aware final selection (MMR)
        if self.evidence_processor and hasattr(self, '_orchestrator_delegate'):
            try:
                final_sources = self._orchestrator_delegate._select_diverse_top_k(
                    query, judged, per_source_summary, K, lambda_rel=evidence_diversity_lambda
                )
                logger.info(f"   Step 8: {len(final_sources)} sources selected with diversity (λ={evidence_diversity_lambda})")
            except Exception as e:
                logger.warning(f"Diversity selection fehlgeschlagen: {e}")
                final_sources = [src for src, _ in judged[:K]]
        else:
            # Fallback: simple top-K
            final_sources = [src for src, _ in judged[:K]]
            logger.info(f"   Step 8: {len(final_sources)} sources selected (simple top-K)")
        
        # Count source types
        web_sources = sum(1 for s in final_sources if not s.url.startswith("rag://"))
        rag_sources = sum(1 for s in final_sources if s.url.startswith("rag://"))
        
        processing_time_ms = (time.time() - start_time) * 1000
        
        result = SourceSelectionResult(
            sources=final_sources,
            candidates_count=len(candidates),
            ranked_count=len(ranked_with_scores),
            shortlist_count=M,
            final_count=len(final_sources),
            web_sources_count=web_sources,
            rag_sources_count=rag_sources,
            processing_time_ms=processing_time_ms,
            validation_stats=validation_stats
        )
        
        logger.info(f"✅ Evidence Selection Complete: {len(final_sources)} sources ({web_sources} web, {rag_sources} RAG) in {processing_time_ms:.1f}ms")
        
        return result
    
    def set_orchestrator_delegate(self, orchestrator: Any) -> None:
        """
        Sets orchestrator as delegate for methods not yet migrated
        
        This allows gradual migration of logic from orchestrator to manager.
        """
        self._orchestrator_delegate = orchestrator
        logger.info("✅ Orchestrator delegate set for EvidenceManager")
    
    # ==================== SOURCE SUMMARIZATION & RELEVANCE JUDGING ====================
    
    def _summarize_sources(
        self,
        query: str,
        sources: List[Any],
        model_loader: Optional[Any] = None
    ) -> Dict[str, str]:
        """
        Summarize each source focused on answering the query
        
        Args:
            query: The user query
            sources: List of Source objects
            model_loader: Model loader for LLM summarization
            
        Returns:
            Dict mapping source URL to summary text
        """
        summaries: Dict[str, str] = {}
        
        # Use provided model_loader or try orchestrator delegate
        ml = model_loader or getattr(self, '_orchestrator_delegate', None)
        if ml is None or not hasattr(ml, 'model_loader'):
            logger.warning("No model_loader available for summarization - using snippets")
            for s in sources:
                key = getattr(s, 'url', None) or f"idx:{id(s)}"
                snippet = getattr(s, 'snippet', '') or getattr(s, 'content', '')
                summaries[key] = str(snippet).strip()[:400]
            return summaries
        
        # Get actual model_loader
        if hasattr(ml, 'model_loader'):
            ml = ml.model_loader
        
        for s in sources:
            # Extract attributes before try block to avoid scoping issues
            title = getattr(s, 'title', '') or ''
            url = getattr(s, 'url', '') or ''
            date = getattr(s, 'date', '') or ''
            snippet = getattr(s, 'snippet', '') or getattr(s, 'content', '') or ''
            key = url or f"idx:{id(s)}"
            
            try:
                sys_msg = {"role": "system", "content": (
                    "Fasse die folgende Quelle in 2-3 Sätzen knapp zusammen, fokussiert auf die Beantwortung der Frage. "
                    "Nur Fakten, keine Halluzinationen. Antworte auf Deutsch."
                )}
                
                content = (
                    f"Frage: {query}\n\n"
                    f"Titel: {title}\nURL: {url}\nDatum: {date}\n\n"
                    f"Snippet/Excerpt:\n{str(snippet)[:1200]}\n"
                )
                user_msg = {"role": "user", "content": content}
                
                out = ml.generate_response(
                    messages=[sys_msg, user_msg],
                    max_tokens=160,
                    temperature=0.2,
                    image_path=None
                )
                
                text = (out or "").strip()
                if not text:
                    text = str(snippet).strip()[:400]
                
                summaries[key] = text
                
            except (ConnectionError, TimeoutError) as e:
                logger.warning(f"LLM-Verbindungsfehler beim Zusammenfassen von Quelle {url}: {e}")
                summaries[key] = str(snippet).strip()[:400]
            except (ValueError, TypeError) as e:
                logger.warning(f"LLM-Eingabefehler beim Zusammenfassen von Quelle {url}: {e}")
                summaries[key] = str(snippet).strip()[:400]
            except AttributeError as e:
                logger.debug(f"LLM-Attributfehler beim Zusammenfassen von Quelle {url}: {e}")
                summaries[key] = str(snippet).strip()[:400]
            except Exception as e:
                logger.error(f"Unerwarteter Fehler beim LLM-Zusammenfassen von Quelle {url}: {type(e).__name__}: {e}")
                summaries[key] = str(snippet).strip()[:400]
        
        return summaries

    # ==================== DISTILLATION (SOTA Pre-Processing) ====================

    def distill_web_evidence(
        self,
        sources: List[Any],
        query: str,
        model_loader: Optional[Any] = None,
        batch_size: int = 3,
        top_k_web_sources: int = 3,
        max_regen_attempts: int = 1,
    ) -> Dict[str, str]:
        """
        Query-aware factual distillation of web evidence — SOTA Pre-Processing layer.

        Transforms raw web snippets (high noise, high token cost) into dense,
        query-relevant fact statements (low noise, low token cost) BEFORE they
        enter the final synthesis prompt or IRCoT evidence block.

                Strategy:
                    - Only top-k reranked web sources are distilled (RAG chunks skipped)
                    - Parallel batched calls: batch_size sources per LLM call, all batches run
                        concurrently via ThreadPoolExecutor → low wall-clock latency
                    - Schema-enforced JSON output (Pydantic validation + one regeneration retry)
                    - Prompt uses factual extraction (not generic summarization)

        Args:
            sources:      List of Source objects (mix of web and RAG is fine)
            query:        The original user query — used to focus extraction
            model_loader: ModelLoader instance; falls back to orchestrator delegate
            batch_size: Number of sources per LLM batch call
            top_k_web_sources: Number of highest-scored web sources to distill
            max_regen_attempts: Retry count for invalid JSON output

        Returns:
            Dict mapping source URL → distilled fact text.
            Sources for which distillation failed fall back to their raw snippet.
        """
        from concurrent.futures import ThreadPoolExecutor, as_completed

        # Resolve model_loader
        ml = model_loader or getattr(self, '_orchestrator_delegate', None)
        if ml is not None and hasattr(ml, 'model_loader'):
            ml = ml.model_loader

        # Separate web sources (distill) from RAG sources (skip, already dense)
        web_sources = [
            s for s in sources
            if not (getattr(s, 'url', '') or '').startswith('rag://')
        ]

        # Re-ranking gate before distillation: only highest scored web evidence.
        web_sources.sort(key=lambda s: float(getattr(s, 'score', 0.0) or 0.0), reverse=True)
        web_sources = web_sources[:max(1, top_k_web_sources)]

        if not web_sources or ml is None or not hasattr(ml, 'generate_response'):
            logger.debug("[Distill] Skipping distillation (no web sources or no model)")
            return {}

        # Build batches
        batches: List[List[Any]] = [
            web_sources[i:i + batch_size]
            for i in range(0, len(web_sources), batch_size)
        ]

        distilled: Dict[str, str] = {}

        def _normalize_facts_payload(parsed: Any) -> Dict[str, Any]:
            """Normalize model output into the canonical {'facts': [...]} schema."""
            if isinstance(parsed, dict):
                if isinstance(parsed.get("facts"), list):
                    facts_raw = parsed.get("facts", [])
                elif "fact" in parsed:
                    facts_raw = [parsed]
                else:
                    facts_raw = []
            elif isinstance(parsed, list):
                facts_raw = parsed
            else:
                facts_raw = []

            normalized_facts: List[Dict[str, Any]] = []
            for item in facts_raw:
                if not isinstance(item, dict):
                    continue
                fact_text = str(item.get("fact", "")).strip()
                source_id = str(item.get("source_id", "")).strip()
                confidence = item.get("confidence", 0.5)
                try:
                    confidence = float(confidence)
                except (TypeError, ValueError):
                    confidence = 0.5
                confidence = max(0.0, min(1.0, confidence))

                if not fact_text or not source_id:
                    continue

                normalized_facts.append(
                    {
                        "fact": fact_text,
                        "source_id": source_id,
                        "confidence": confidence,
                    }
                )

            return {"facts": normalized_facts}

        def _extract_json(raw_text: str) -> Optional[str]:
            text = (raw_text or "").strip()
            if not text:
                return None

            if text.startswith("```"):
                text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
                text = re.sub(r"```\s*$", "", text)

            decoder = json.JSONDecoder()

            try:
                parsed = json.loads(text)
                normalized = _normalize_facts_payload(parsed)
                return json.dumps(normalized)
            except Exception:
                pass

            first_json_start = -1
            for ch in ("{", "["):
                idx = text.find(ch)
                if idx >= 0 and (first_json_start == -1 or idx < first_json_start):
                    first_json_start = idx
            if first_json_start < 0:
                return None

            candidate = text[first_json_start:]
            try:
                parsed, _end = decoder.raw_decode(candidate)
                normalized = _normalize_facts_payload(parsed)
                return json.dumps(normalized)
            except Exception:
                return None

        def _fallback_batch(batch: List[Any]) -> Dict[str, str]:
            fallback: Dict[str, str] = {}
            for s in batch:
                url = getattr(s, 'url', '') or f"idx:{id(s)}"
                snippet = getattr(s, 'snippet', '') or getattr(s, 'content', '') or ''
                fallback[url] = str(snippet)[:400]
            return fallback

        def _distill_batch(batch: List[Any]) -> Dict[str, str]:
            """Single LLM call for a batch of sources with schema validation + retry."""
            model_n_ctx = getattr(ml, "_cached_n_ctx", None) or 16384
            # Keep enough headroom for completion + stop tokens.
            desired_output_tokens = min(max(384, 180 * len(batch)), 2048)
            min_prompt_headroom = 256

            # Estimate prompt budget and adapt snippet size to avoid context pressure.
            base_prompt_overhead_chars = 1200 + len(query)
            per_source_meta_chars = 180
            prompt_char_budget = max(
                2400,
                int(max(model_n_ctx - desired_output_tokens - min_prompt_headroom, 1024) * 4),
            )
            available_snippet_chars_total = max(
                800,
                prompt_char_budget - base_prompt_overhead_chars - (per_source_meta_chars * len(batch)),
            )
            snippet_chars_per_source = max(
                300,
                min(1200, available_snippet_chars_total // max(len(batch), 1)),
            )

            combined_parts: List[str] = []
            id_to_url: Dict[str, str] = {}
            for i, s in enumerate(batch, 1):
                source_id = f"S{i}"
                url = getattr(s, 'url', '') or f"idx:{id(s)}"
                id_to_url[source_id] = url
                title = getattr(s, 'title', '') or ''
                snippet = getattr(s, 'snippet', '') or getattr(s, 'content', '') or ''
                combined_parts.append(
                    f"[{source_id}] Titel: {title}\nURL: {url}\nText:\n{str(snippet)[:snippet_chars_per_source]}"
                )

            combined_context = "\n---\n".join(combined_parts)
            schema_hint = (
                '{"facts":[{"fact":"...","source_id":"S1","confidence":0.0}]}'
            )

            base_prompt = (
                f"Nutzeranfrage: {query}\n\n"
                "Extrahiere aus den Quellen NUR Fakten, die direkt die Anfrage beantworten. "
                "Entferne Navigation, Boilerplate, Werbung und Duplikate. "
                "Antworte ausschließlich als JSON ohne Markdown.\n\n"
                f"Quellen:\n{combined_context}\n\n"
                f"JSON-Schema-Beispiel: {schema_hint}"
            )

            prompt = base_prompt
            # Context-aware output budget: target high recall, but never overbook n_ctx.
            approx_prompt_tokens = max(1, len(base_prompt) // 4)
            available_tokens = max(model_n_ctx - approx_prompt_tokens, 64)
            max_tokens_dynamic = min(desired_output_tokens, max(128, available_tokens - 32))
            
            for attempt in range(max_regen_attempts + 1):
                try:
                    raw = ml.generate_response(
                        messages=[
                            {
                                "role": "system",
                                "content": (
                                    "Du bist ein Fakten-Extraktor. Gib ausschließlich valides JSON "
                                    "im geforderten Schema zurück."
                                ),
                            },
                            {"role": "user", "content": prompt},
                        ],
                        max_tokens=max_tokens_dynamic,
                        temperature=0.0,
                        image_path=None,
                    )
                    
                    # 🔍 DETECTION: Check if output was truncated
                    # If raw is dict (from model_loader), check finish_reason
                    truncated_warning = None
                    if isinstance(raw, dict):
                        finish_reason = raw.get('choices', [{}])[0].get('finish_reason', 'unknown')
                        if finish_reason == 'length':
                            truncated_warning = f"[Distill] WARNING: LLM output truncated (finish_reason=length, max_tokens={max_tokens_dynamic})"
                            logger.warning(truncated_warning)
                    
                except Exception as e:
                    logger.warning("[Distill] Batch LLM call failed: %s", e)
                    return _fallback_batch(batch)

                # Extract text from response (handle both dict and string formats)
                raw_text: str
                if isinstance(raw, dict):
                    raw_text = raw.get('choices', [{}])[0].get('text', '') or raw.get('text', '')
                else:
                    raw_text = str(raw or "")

                json_payload = _extract_json(raw_text)

                if not json_payload:
                    if attempt < max_regen_attempts:
                        prompt = (
                            f"{base_prompt}\n\n"
                            "Der letzte Output war kein valides JSON. Liefere jetzt NUR valides JSON. "
                            "WICHTIG: Schließe alle Klammern und Anführungszeichen vollständig ab!"
                        )
                        # Erhöhe max_tokens für den nächsten Versuch, falls das JSON abgeschnitten war
                        if attempt == 0 and max_tokens_dynamic < 2048:
                            max_tokens_dynamic = min(max_tokens_dynamic * 2, 2048)
                        logger.info(f"[Distill] Retry with increased max_tokens={max_tokens_dynamic}")
                        continue  # Springt zum nächsten Schleifendurchlauf (Retry)
                    else:
                        return _fallback_batch(batch)

                try:
                    if DistilledFactBatchModel is not None:
                        validated = DistilledFactBatchModel.model_validate_json(json_payload)
                        facts = validated.facts

                        batch_result: Dict[str, str] = {url: "" for url in id_to_url.values()}
                        for fact_item in facts:
                            source_id = getattr(fact_item, "source_id", None)
                            fact_text = getattr(fact_item, "fact", None)

                            if not source_id or not fact_text or source_id not in id_to_url:
                                continue

                            url = id_to_url[source_id]
                            if batch_result[url]:
                                batch_result[url] += " "
                            batch_result[url] += str(fact_text).strip()
                    else:
                        parsed = json.loads(json_payload)
                        facts = parsed.get("facts", []) if isinstance(parsed, dict) else []
                        batch_result = {url: "" for url in id_to_url.values()}
                        for fact_item in facts:
                            if not isinstance(fact_item, dict):
                                continue
                            source_id = fact_item.get("source_id")
                            fact_text = fact_item.get("fact")

                            if not source_id or not fact_text or source_id not in id_to_url:
                                continue

                            url = id_to_url[source_id]
                            if batch_result[url]:
                                batch_result[url] += " "
                            batch_result[url] += str(fact_text).strip()

                    # Ensure every source gets at least fallback snippet if model omitted it.
                    for s in batch:
                        url = getattr(s, 'url', '') or f"idx:{id(s)}"
                        if not batch_result.get(url):
                            snippet = getattr(s, 'snippet', '') or getattr(s, 'content', '') or ''
                            batch_result[url] = str(snippet)[:400]

                    return batch_result

                except Exception as parse_exc:
                    json_preview = (json_payload or "")[:200]
                    logger.debug(
                        "[Distill] Schema validation failed: %s, json_payload_preview=%s...",
                        str(parse_exc)[:300],
                        json_preview,
                    )
                    if attempt < max_regen_attempts:
                        prompt = (
                            f"{base_prompt}\n\n"
                            "Der letzte Output war JSON, aber Schema-validierung ist fehlgeschlagen: "
                            f"{str(parse_exc)[:200]}. "
                            "Liefere erneut NUR valides JSON im exakt geforderten Schema. "
                            "Stelle sicher, dass alle Felder vollständig sind!"
                        )
                        if attempt == 0 and max_tokens_dynamic < 2048:
                            max_tokens_dynamic = min(max_tokens_dynamic * 2, 2048)
                            logger.info(f"[Distill] Retry with increased max_tokens={max_tokens_dynamic}")
                        continue
                    logger.warning("[Distill] Schema validation failed after retry: %s", parse_exc)
                    return _fallback_batch(batch)

            return _fallback_batch(batch)

        # Run all batches in parallel
        logger.info("[Distill] Distilling %d web sources in %d batch(es)", len(web_sources), len(batches))
        with ThreadPoolExecutor(max_workers=min(len(batches), 4)) as executor:
            futures = {executor.submit(_distill_batch, batch): batch for batch in batches}
            for future in as_completed(futures):
                try:
                    batch_result = future.result()
                    distilled.update(batch_result)
                except Exception as e:
                    logger.warning("[Distill] Batch future failed: %s", e)

        logger.info("[Distill] Distillation complete: %d sources processed", len(distilled))
        return distilled

    def _judge_relevance_rule(
        self,
        query: str,
        sources: List[Any],
        summaries: Dict[str, str],
        use_llm_evidence_selection: bool = True,
        evidence_selector: Optional[Any] = None
    ) -> List[Tuple[Any, float]]:
        """
        Judge relevance of sources for the query
        
        Uses LLM-based evidence selection if available and enabled,
        otherwise falls back to rule-based scoring.
        
        Args:
            query: User query
            sources: List of Source objects
            summaries: Dict of source URL to summary text
            use_llm_evidence_selection: Whether to use LLM-based selection
            evidence_selector: UniversalEvidenceSelector instance
            
        Returns:
            List of (Source, relevance_score) tuples, sorted by score descending
        """
        # Try LLM-based Evidence Selection first
        if use_llm_evidence_selection:
            # Use provided selector or try to get from orchestrator delegate
            selector = evidence_selector or getattr(self, '_orchestrator_delegate', None)
            
            if selector and hasattr(selector, 'evidence_selector'):
                try:
                    logger.info(f"Verwende LLM-basierte Evidence-Selection für {len(sources)} Quellen")
                    scored: List[Tuple[Any, float]] = selector.evidence_selector.select_evidence(query, sources)
                    logger.info(f"LLM Evidence-Selection: {len(scored)} Quellen bewertet")
                    return scored
                except (ConnectionError, TimeoutError) as e:
                    logger.warning(f"LLM Evidence-Selection Verbindungsfehler: {e}, Fallback zu Regeln")
                except (ValueError, TypeError) as e:
                    logger.warning(f"LLM Evidence-Selection Eingabefehler: {e}, Fallback zu Regeln")
                except AttributeError as e:
                    logger.warning(f"LLM Evidence-Selection nicht verfügbar: {e}, Fallback zu Regeln")
                except Exception as e:
                    logger.error(f"LLM Evidence-Selection unerwarteter Fehler: {type(e).__name__}: {e}, Fallback zu Regeln")
                    import traceback
                    logger.debug(f"LLM Evidence-Selection Traceback:\n{traceback.format_exc()}")
        
        # Fallback: Rule-based relevance scoring
        logger.info("Verwende regel-basierte Evidence-Selection")
        q_tokens = self._tokenize(query)
        judged: List[Tuple[Any, float]] = []
        
        for s in sources:
            url = getattr(s, 'url', None) or f"idx:{id(s)}"
            snippet = getattr(s, 'snippet', '') or getattr(s, 'content', '') or ''
            
            key = url
            summ = summaries.get(key) or str(snippet)
            s_tokens = self._tokenize(summ.lower())
            base = self._overlap(q_tokens, s_tokens)
            
            # Domain authority provides a small boost
            boost = self._domain_authority_score(url)
            score = min(1.0, base + boost)
            judged.append((s, score))
        
        judged.sort(key=lambda x: x[1], reverse=True)
        return judged
    
    def _tokenize(self, text: str) -> Set[str]:
        """
        Simple tokenization for text matching
        
        Args:
            text: Text to tokenize
            
        Returns:
            Set of lowercase tokens
        """
        return set(re.findall(r'\w+', text.lower()))
    
    def _overlap(self, tokens_a: Set[str], tokens_b: Set[str]) -> float:
        """
        Calculate token overlap ratio
        
        Args:
            tokens_a: First token set
            tokens_b: Second token set
            
        Returns:
            Overlap ratio (0-1)
        """
        if not tokens_a or not tokens_b:
            return 0.0
        intersection = len(tokens_a & tokens_b)
        union = len(tokens_a | tokens_b)
        return intersection / union if union > 0 else 0.0
    
    def _domain_authority_score(self, url: str) -> float:
        """
        Simple domain authority scoring
        
        Args:
            url: URL to score
            
        Returns:
            Authority boost score (0.0-0.15)
        """
        if not url:
            return 0.0
        
        url_lower = url.lower()
        
        # High authority domains (government, education, major news)
        high_authority = [
            'gov.', 'edu.', 'ac.uk', 'bbc.com', 'reuters.com',
            'nytimes.com', 'wikipedia.org', 'nature.com', 'science.org'
        ]
        
        # Medium authority domains
        medium_authority = [
            '.org', 'guardian.com', 'washingtonpost.com', 'cnn.com',
            'bloomberg.com', 'techcrunch.com'
        ]
        
        for domain in high_authority:
            if domain in url_lower:
                return 0.15
        
        for domain in medium_authority:
            if domain in url_lower:
                return 0.08
        
        return 0.0

    def _apply_adaptive_budget_allocation(
        self,
        ranked_sources: List[Tuple[Any, float]],
        max_snippet_len: int = 5000,
        min_snippet_len: int = 800,
        decay_factor: float = 0.7
    ) -> List[Tuple[Any, float]]:
        """
        ✅ STATE-OF-ART: Apply adaptive token budget allocation to ranked sources
        
        Top-ranked sources get more context (up to max_snippet_len),
        lower-ranked sources get progressively less (down to min_snippet_len).
        This prevents token budget explosion while maximizing information density.
        
        Args:
            ranked_sources: List of (Source, score) tuples, ranked by relevance
            max_snippet_len: Maximum snippet length for the top source (5000 chars)
            min_snippet_len: Minimum snippet length for the lowest-ranked sources (800 chars)
            decay_factor: Exponential decay factor for allocating token budget (0.7 = aggressive)
            
        Returns:
            List of (Source, score) tuples with adaptively truncated snippets
        """
        if not ranked_sources:
            return []
        
        adjusted_sources = []
        
        for i, (source, score) in enumerate(ranked_sources):
            # Exponential decay of snippet length based on rank
            # Rank 0 (top): 5000 chars, Rank 1: 3500 chars, Rank 2: 2450 chars, etc.
            rank_decay = decay_factor ** i
            target_snippet_len = int(max_snippet_len * rank_decay)
            
            # Ensure snippet length is within bounds
            target_snippet_len = max(min_snippet_len, min(target_snippet_len, max_snippet_len))
            
            # ✅ CRITICAL: Actually truncate the snippet to target length
            if hasattr(source, 'snippet') and source.snippet:
                original_len = len(source.snippet)
                if original_len > target_snippet_len:
                    # Truncate with ellipsis
                    source.snippet = source.snippet[:target_snippet_len] + "..."
                    logger.debug(f"   Rank {i}: Truncated snippet {original_len} → {target_snippet_len} chars (score={score:.3f})")
            
            adjusted_sources.append((source, score))
        
        return adjusted_sources
