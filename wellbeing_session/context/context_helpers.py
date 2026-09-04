"""
Context building helper functions.

This module provides utility functions for building comprehensive user context
from various data sources (Knowledge Graph, Session Summaries, etc.).
"""
import logging
import math
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)


def get_query_adaptive_selection_params(
    user_input: str,
    triples: List[Dict[str, Any]],
    baseline_aware: bool = False,
) -> Dict[str, float]:
    """
    Compute query-adaptive selection parameters for hybrid KG retrieval.

    This follows a query-aware retrieval strategy (adaptive recall/precision
    balance) instead of static thresholds.

    Args:
        user_input: Current user message
        triples: Candidate triple set before adaptive selection
        baseline_aware: If True, keep drop-threshold relaxed because semantic
            and baseline pools are intentionally mixed.

    Returns:
        Dict with min_relevance, max_triples, similarity_drop_threshold.
    """
    text = str(user_input or "").strip()
    words = [w for w in text.split() if w]

    if not words:
        # Conservative defaults for empty/invalid user input.
        return {
            "min_relevance": 0.35,
            "max_triples": 32.0,
            "similarity_drop_threshold": 0.99 if baseline_aware else 0.14,
        }

    # Query complexity proxy: grows sub-linearly with token length.
    # 0.0 => trivial query, 1.0 => high-complexity query.
    complexity = min(1.0, math.log1p(len(words)) / math.log(32.0))

    # Candidate-pool pressure: larger pools permit larger context windows.
    pool_pressure = min(1.0, len(triples) / 80.0) if triples else 0.0

    # For complex queries, lower min_relevance to improve recall.
    min_relevance = 0.42 - (0.14 * complexity)
    min_relevance = max(0.25, min(0.45, min_relevance))

    # Expand triple budget with complexity and candidate pressure.
    max_triples = 24 + (14 * complexity) + (10 * pool_pressure)
    max_triples = float(max(20, min(48, int(round(max_triples)))))

    # Drop-threshold controls how aggressively the tail is cut.
    # For complex queries we allow a slightly larger drop before stopping.
    drop_threshold = 0.10 + (0.07 * complexity)
    drop_threshold = max(0.08, min(0.20, drop_threshold))

    if baseline_aware:
        # Baseline pools intentionally mix medium-relevance personal facts.
        # Keep drop-stop effectively relaxed to avoid pruning all baseline items.
        drop_threshold = 0.99

    return {
        "min_relevance": float(min_relevance),
        "max_triples": float(max_triples),
        "similarity_drop_threshold": float(drop_threshold),
    }


def _safe_float(value: Any, default: float = 0.0) -> float:
    """Parse score-like values defensively."""
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def calculate_relevance_score(triple: Dict[str, Any], current_time: datetime) -> float:
    """
    Calculate relevance score for a KG triple.
    
    Combines multiple factors:
    - Semantic similarity (from search)
    - Confidence (from extraction)
    - Temporal recency (decay over time)
    
    Args:
        triple: Knowledge graph triple with metadata
        current_time: Current timestamp for temporal scoring
        
    Returns:
        Combined relevance score (0-1)
    """
    similarity = _safe_float(triple.get('similarity', 0.5), 0.5)
    confidence = _safe_float(triple.get('confidence', 0.5), 0.5)
    
    # SOTA FIX: Use cross-encoder reranking score when available
    # The combined_score from SOTA v4 pipeline (entity_score × 0.4 + rerank_score × 0.6)
    # is the highest-quality relevance signal and should override raw similarity.
    combined_score = _safe_float(triple.get('combined_score', 0.0), 0.0)
    if combined_score > 0:
        similarity = combined_score  # Cross-encoder output replaces raw FAISS similarity
    
    # Temporal scoring: recent = more relevant
    temporal_score = 0.5  # Default if no date
    source_date = triple.get('source_date')
    
    if source_date and source_date != 'N/A':
        try:
            if isinstance(source_date, str):
                source_date = datetime.fromisoformat(source_date.replace('Z', '+00:00'))
            if source_date.tzinfo is None:
                source_date = source_date.replace(tzinfo=timezone.utc)
            
            age_days = (current_time - source_date).days
            # Exponential decay: half-life = 30 days
            temporal_score = max(0.1, 1.0 * (0.5 ** (age_days / 30)))
        except (ValueError, TypeError, AttributeError) as e:
            logger.debug(f"Failed to parse date {source_date}: {e}")
    
    # Combined score: 40% similarity, 30% confidence, 30% temporal
    relevance = (
        similarity * 0.4 +
        confidence * 0.3 +
        temporal_score * 0.3
    )
    
    return float(relevance)


def adaptive_triple_selection(
    triples: List[Dict[str, Any]], 
    min_relevance: float = 0.4,
    max_triples: int = 40,
    similarity_drop_threshold: float = 0.15
) -> List[Dict[str, Any]]:
    """
    Select triples adaptively based on relevance scores.
    
    Stops when:
    1. Max limit reached (40)
    2. Relevance falls below threshold (0.4)
    3. Large relevance drop between triples (>0.15)
    
    Args:
        triples: List of KG triples (already sorted by similarity)
        min_relevance: Minimum relevance score (default: 0.4)
        max_triples: Maximum number (default: 40)
        similarity_drop_threshold: Max allowed drop between triples
        
    Returns:
        Filtered and re-ranked triple list
    """
    if not triples:
        return []
    
    current_time = datetime.now(timezone.utc)
    
    # Calculate relevance scores for all triples
    scored_triples = []
    for triple in triples:
        relevance = calculate_relevance_score(triple, current_time)
        triple_copy = triple.copy()
        triple_copy['relevance_score'] = relevance
        scored_triples.append(triple_copy)
    
    # Sort by relevance score (highest first)
    scored_triples.sort(key=lambda x: x['relevance_score'], reverse=True)
    
    # Adaptive selection
    selected: List[Dict[str, Any]] = []
    prev_relevance: Optional[float] = None  # Start with None instead of 1.0
    
    for triple in scored_triples:
        relevance = triple['relevance_score']
        
        # Check: Below minimum?
        if relevance < min_relevance:
            logger.debug(f"Stopping: relevance {relevance:.3f} < min {min_relevance}")
            break
        
        # Check: Too large drop? (only if not first triple)
        if prev_relevance is not None and prev_relevance - relevance > similarity_drop_threshold:
            logger.debug(f"Stopping: drop {prev_relevance:.3f} -> {relevance:.3f} > threshold {similarity_drop_threshold}")
            break
        
        # Check: Max reached?
        if len(selected) >= max_triples:
            break
        
        selected.append(triple)
        prev_relevance = relevance
    
    logger.info(f"🎯 [ADAPTIVE-SELECT] {len(selected)}/{len(triples)} triples selected (min_rel={min_relevance})")
    
    return selected


def rank_summaries_by_relevance(
    summaries: List[Dict[str, Any]], 
    user_input: str,
    max_summaries: int = 5
) -> List[Dict[str, Any]]:
    """
    Rank session summaries by relevance using embeddings.
    
    Uses semantic similarity (embeddings) instead of keyword matching
    for smarter relevance scoring.
    
    Args:
        summaries: List of session summaries
        user_input: Current user message
        max_summaries: Maximum number
        
    Returns:
        Ranked summary list by semantic relevance
    """
    if not summaries:
        return []
    
    current_time = datetime.now(timezone.utc)
    
    # Try embedding-based ranking
    try:
        from utils.embedding_singleton import get_embedding_model
        embedding_model = get_embedding_model()
        
        if embedding_model:
            # Batch embedding for lower latency and stable ranking behavior.
            summary_texts = [str(s.get('summary', '') or '') for s in summaries]
            payload = [user_input] + summary_texts
            embeddings = embedding_model.encode(payload)

            query_embedding = embeddings[0]
            summary_embeddings = embeddings[1:]

            ranked = []
            for summary, summary_embedding in zip(summaries, summary_embeddings):
                summary_text = summary.get('summary', '')
                summary_date = summary.get('date')

                if summary_text:
                    import numpy as np

                    query_norm = np.linalg.norm(query_embedding)
                    summary_norm = np.linalg.norm(summary_embedding)
                    denom = query_norm * summary_norm
                    if denom <= 1e-12:
                        similarity = 0.0
                    else:
                        similarity = float(np.dot(query_embedding, summary_embedding) / denom)
                else:
                    similarity = 0.0
                
                # Temporal score
                temporal_score = 0.5
                if summary_date:
                    try:
                        if isinstance(summary_date, str):
                            summary_date = datetime.fromisoformat(summary_date.replace('Z', '+00:00'))
                        if summary_date.tzinfo is None:
                            summary_date = summary_date.replace(tzinfo=timezone.utc)
                        
                        age_days = (current_time - summary_date).days
                        temporal_score = max(0.2, 1.0 * (0.5 ** (age_days / 14)))
                    except (ValueError, TypeError, AttributeError) as exc:
                        logger.debug(f"Temporal score parse failed: {exc}")
                
                # Combined score: 70% semantic, 30% temporal
                combined_score = float(similarity) * 0.7 + temporal_score * 0.3
                
                ranked.append({
                    **summary,
                    'relevance_score': float(combined_score),
                    'semantic_similarity': float(similarity)
                })
            
            ranked.sort(key=lambda x: x['relevance_score'], reverse=True)
            if ranked:
                logger.info(f"📝 [RANK-SUMMARIES-EMB] Top similarity: {ranked[0]['semantic_similarity']:.3f}")
            
            return ranked[:max_summaries]
    
    except Exception as e:
        logger.debug(f"📝 [RANK-SUMMARIES] Embedding-based ranking not available: {e}")
    
    # Fallback: Time-based ranking only
    def get_temporal_score(summary: Dict[str, Any]) -> float:
        summary_date = summary.get('date')
        if not summary_date:
            return 0.5
        try:
            if isinstance(summary_date, str):
                summary_date = datetime.fromisoformat(summary_date.replace('Z', '+00:00'))
            if summary_date.tzinfo is None:
                summary_date = summary_date.replace(tzinfo=timezone.utc)
            age_days = (current_time - summary_date).days
            score = 1.0 * (0.5 ** (age_days / 14))
            # Ensure float return
            return float(max(0.2, score))
        except (ValueError, TypeError, AttributeError) as exc:
            logger.debug(f"Temporal score parse failed: {exc}")
            return 0.5
    
    ranked = [{**s, 'relevance_score': get_temporal_score(s)} for s in summaries]
    ranked.sort(key=lambda x: x['relevance_score'], reverse=True)
    
    return ranked[:max_summaries]
