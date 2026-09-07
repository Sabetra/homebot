"""
User Feedback Tracking System
==============================

Sammelt und analysiert User-Feedback zu Chatbot-Antworten
für langfristige Optimierung und Qualitätssicherung.

Unterstützt sowohl JSONL- als auch SQLite-Backend für maximale Flexibilität.
"""

import json
import os
import sqlite3
from datetime import datetime
from typing import Optional, Dict, Any, List, Literal
import logging
from pathlib import Path
import hashlib

logger = logging.getLogger(__name__)


# Module-level singleton instance
_feedback_logger_instance: Optional["FeedbackLogger"] = None


class FeedbackLogger:
    """Sammelt und speichert User-Feedback mit Hybrid-Backend (JSONL + SQLite)."""

    def __init__(
        self,
        feedback_file: str = "user_feedback.jsonl",
        db_path: Optional[str] = None,
        backend: Literal["jsonl", "sqlite", "hybrid"] = "hybrid",
        user_id: Optional[str] = None
    ):
        """
        Initialize feedback logger with hybrid backend support.
        
        Args:
            feedback_file: Path to JSONL file for storing feedback
            db_path: Path to SQLite database (default: agent/rag_store.db)
            backend: Storage backend - "jsonl", "sqlite", or "hybrid"
            user_id: Optional user identifier for tracking
        """
        self.feedback_file = feedback_file
        self.db_path = db_path or "agent/rag_store.db"
        self.backend = backend
        self.user_id = user_id
        self._quality_forward_failures: int = 0
        
        # Ensure backend is available
        if self.backend in ["jsonl", "hybrid"]:
            self._ensure_file_exists()
        
        if self.backend in ["sqlite", "hybrid"]:
            self._ensure_db_schema()
    
    def _ensure_file_exists(self):
        """Stelle sicher, dass die Feedback-Datei existiert."""
        if not os.path.exists(self.feedback_file):
            # Erstelle leere Datei
            with open(self.feedback_file, 'w', encoding='utf-8') as f:
                pass
            logger.info(f"Created feedback file: {self.feedback_file}")
    
    def _ensure_db_schema(self):
        """Stelle sicher, dass die Datenbank-Schema existiert."""
        try:
            # Check if database file exists
            if not os.path.exists(self.db_path):
                logger.warning(f"Database not found at {self.db_path}. Creating new database.")
                # Create directory if needed
                os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
            
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # Check if feedback_responses table exists
            cursor.execute("""
                SELECT name FROM sqlite_master 
                WHERE type='table' AND name='feedback_responses'
            """)
            
            if not cursor.fetchone():
                # Create schema from SQL file
                schema_path = "config/feedback_schema.sql"
                if os.path.exists(schema_path):
                    with open(schema_path, 'r', encoding='utf-8') as f:
                        schema_sql = f.read()
                    cursor.executescript(schema_sql)
                    conn.commit()
                    logger.info("Created feedback tables in database")
                else:
                    # Fallback: create minimal schema
                    cursor.executescript("""
                        CREATE TABLE IF NOT EXISTS feedback_responses (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            user_id TEXT,
                            conversation_id TEXT,
                            response_id TEXT,
                            feedback_type TEXT,
                            feedback_value INTEGER,
                            feedback_category TEXT,
                            comment TEXT,
                            context_metadata TEXT,
                            created_at TEXT,
                            ip_hash TEXT
                        );
                        
                        CREATE INDEX IF NOT EXISTS idx_feedback_user_id 
                            ON feedback_responses(user_id);
                        CREATE INDEX IF NOT EXISTS idx_feedback_created_at 
                            ON feedback_responses(created_at);
                    """)
                    conn.commit()
                    logger.info("Created minimal feedback schema in database")
            
            conn.close()
            
        except Exception as e:
            logger.error(f"Failed to ensure database schema: {e}")
    
    def _generate_response_id(self, query: str, response: str) -> str:
        """Generate unique response ID for tracking."""
        content = f"{query[:100]}{response[:200]}{datetime.now().isoformat()}"
        return hashlib.sha256(content.encode()).hexdigest()[:16]
    
    def log_feedback(
        self,
        query: str,
        response: str,
        feedback: str,
        search_depth: Optional[int] = None,
        confidence: Optional[float] = None,
        reason: Optional[str] = None,
        comment: Optional[str] = None,
        response_time_ms: Optional[float] = None,
        num_rag_results: Optional[int] = None,
        conversation_id: Optional[str] = None,
        category: Optional[str] = None,
        chunk_ids: Optional[List[str]] = None,
        chunk_scores: Optional[List[float]] = None,
    ) -> bool:
        """
        Log user feedback to configured backend(s).
        
        Args:
            query: User query (truncated for privacy)
            response: Bot response (truncated)
            feedback: "positive" or "negative" (or "thumbs_up"/"thumbs_down")
            search_depth: RAG search depth (k value)
            confidence: Confidence threshold used
            reason: Reason for negative feedback (optional)
            comment: Additional user comment (optional)
            response_time_ms: Response time in milliseconds
            num_rag_results: Number of RAG results returned
            conversation_id: Session/conversation identifier
            category: Feedback category (quality, sources, completeness)
        
        Returns:
            True if successful on at least one backend, False otherwise
        """
        success = False
        
        # Normalize feedback type
        feedback_normalized = feedback
        if feedback == "positive":
            feedback_normalized = "thumbs_up"
        elif feedback == "negative":
            feedback_normalized = "thumbs_down"
        
        # Log to JSONL backend
        if self.backend in ["jsonl", "hybrid"]:
            success = self._log_to_jsonl(
                query, response, feedback, search_depth, confidence,
                reason, comment, response_time_ms, num_rag_results
            ) or success
        
        # Log to SQLite backend
        if self.backend in ["sqlite", "hybrid"]:
            success = self._log_to_db(
                query, response, feedback_normalized, search_depth, confidence,
                reason, comment, response_time_ms, num_rag_results,
                conversation_id, category
            ) or success
        
        # ★ SOTA v3: Forward to RAG Quality retrieval feedback for adaptive scoring
        # Now with actual chunk_ids from the search pipeline
        try:
            self._forward_to_quality_feedback(
                query=query,
                response=response,
                feedback_normalized=feedback_normalized,
                chunk_ids=chunk_ids or [],
                chunk_scores=chunk_scores or [],
            )
        except Exception as e:
            self._quality_forward_failures += 1
            logger.warning(
                "Quality feedback forwarding failed (%d failures total): %s",
                self._quality_forward_failures,
                e,
            )
        
        return success
    
    def _forward_to_quality_feedback(
        self,
        query: str,
        response: str,
        feedback_normalized: str,
        chunk_ids: Optional[List[str]] = None,
        chunk_scores: Optional[List[float]] = None,
    ) -> None:
        """
        ★ SOTA v3: Forward user feedback to the RAG Quality module's retrieval_feedback table.
        
        This bridges the existing feedback UI (thumbs up/down) with the quality module's
        adaptive chunk utility scoring. The quality module uses Wilson score intervals
        on aggregated feedback to identify high/low-value chunks.
        """
        try:
            from agent.rag_store.core.quality import RAGQualityManager
        except ImportError:
            return
        
        # Map feedback to integer
        if feedback_normalized in ("thumbs_up", "positive"):
            score = 1
        elif feedback_normalized in ("thumbs_down", "negative"):
            score = -1
        else:
            return  # Unknown feedback type
        
        try:
            from agent.unified_rag_store import UnifiedRagStore
        except ImportError:
            return

        store = UnifiedRagStore.get_existing_shared()
        if store is None:
            return
        
        qm = RAGQualityManager(db_path=store.db_path)
        conn = qm._get_connection()
        try:
            qm.record_retrieval_feedback(
                conn=conn,
                query=query,
                chunk_ids=chunk_ids or [],
                chunk_scores=chunk_scores or [],
                answer_excerpt=response[:300],
                user_feedback=score,
            )
        finally:
            conn.close()

    def _log_to_jsonl(
        self,
        query: str,
        response: str,
        feedback: str,
        search_depth: Optional[int],
        confidence: Optional[float],
        reason: Optional[str],
        comment: Optional[str],
        response_time_ms: Optional[float],
        num_rag_results: Optional[int]
    ) -> bool:
        """Log feedback to JSONL file."""
        try:
            data = {
                "timestamp": datetime.now().isoformat(),
                "query": query[:100],  # Privacy: truncate
                "response": response[:200],  # Privacy: truncate
                "feedback": feedback,
                "search_depth": search_depth,
                "confidence": confidence,
                "reason": reason,
                "comment": comment,
                "response_time_ms": response_time_ms,
                "num_rag_results": num_rag_results
            }
            
            # Append to JSONL file
            with open(self.feedback_file, 'a', encoding='utf-8') as f:
                f.write(json.dumps(data, ensure_ascii=False) + '\n')
            
            logger.info(f"Feedback logged to JSONL: {feedback} (k={search_depth}, conf={confidence})")
            return True
            
        except Exception as e:
            logger.error(f"Failed to log feedback to JSONL: {e}")
            return False
    
    def _log_to_db(
        self,
        query: str,
        response: str,
        feedback: str,
        search_depth: Optional[int],
        confidence: Optional[float],
        reason: Optional[str],
        comment: Optional[str],
        response_time_ms: Optional[float],
        num_rag_results: Optional[int],
        conversation_id: Optional[str],
        category: Optional[str]
    ) -> bool:
        """Log feedback to SQLite database."""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # Generate response ID
            response_id = self._generate_response_id(query, response)
            
            # Prepare context metadata
            context_metadata = json.dumps({
                "search_depth": search_depth,
                "confidence": confidence,
                "response_time_ms": response_time_ms,
                "num_rag_results": num_rag_results,
                "query_preview": query[:100],
                "response_preview": response[:200]
            })
            
            # Convert feedback to value
            feedback_value = 1 if feedback in ["positive", "thumbs_up"] else -1
            
            # Determine category
            if not category and reason:
                # Map reason to category
                if "Irrelevant" in reason:
                    category = "quality"
                elif "Quelle" in reason or "Source" in reason:
                    category = "sources"
                elif "wenig" in reason or "incomplete" in reason.lower():
                    category = "completeness"
                else:
                    category = "quality"
            
            # Insert into database
            cursor.execute("""
                INSERT INTO feedback_responses (
                    user_id, conversation_id, response_id, feedback_type,
                    feedback_value, feedback_category, comment, 
                    context_metadata, created_at, ip_hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                self.user_id,
                conversation_id,
                response_id,
                feedback,
                feedback_value,
                category,
                comment or reason,  # Use reason as comment if no explicit comment
                context_metadata,
                datetime.now().isoformat(),
                None  # ip_hash - not implemented yet
            ))
            
            conn.commit()
            conn.close()
            
            logger.info(f"Feedback logged to DB: {feedback} (k={search_depth}, conf={confidence})")
            return True
            
        except Exception as e:
            logger.error(f"Failed to log feedback to DB: {e}")
            return False
    
    def get_statistics(self, source: Literal["jsonl", "sqlite", "combined"] = "combined") -> Dict[str, Any]:
        """
        Berechne Statistiken aus gesammelten Feedbacks.
        
        Args:
            source: Data source - "jsonl", "sqlite", or "combined"
        
        Returns:
            Dictionary mit Statistiken
        """
        try:
            # Determine which source to use
            if source == "combined" and self.backend == "hybrid":
                # Merge data from both sources
                jsonl_stats = self._get_statistics_from_jsonl()
                db_stats = self._get_statistics_from_db()
                return self._merge_statistics(jsonl_stats, db_stats)
            elif source == "sqlite" or (source == "combined" and self.backend == "sqlite"):
                return self._get_statistics_from_db()
            else:
                return self._get_statistics_from_jsonl()
                
        except Exception as e:
            logger.error(f"Failed to compute statistics: {e}")
            return {"error": str(e)}
    
    def _get_statistics_from_jsonl(self) -> Dict[str, Any]:
        """Get statistics from JSONL file."""
        try:
            if not os.path.exists(self.feedback_file):
                return {"error": "No feedback data available", "source": "jsonl"}
            
            feedbacks = []
            with open(self.feedback_file, 'r', encoding='utf-8') as f:
                for line in f:
                    if line.strip():
                        feedbacks.append(json.loads(line))
            
            if not feedbacks:
                return {"total": 0, "message": "No feedback collected yet", "source": "jsonl"}
            
            return self._compute_statistics(feedbacks, source="jsonl")
            
        except Exception as e:
            logger.error(f"Failed to get statistics from JSONL: {e}")
            return {"error": str(e), "source": "jsonl"}
    
    def _get_statistics_from_db(self) -> Dict[str, Any]:
        """Get statistics from SQLite database."""
        try:
            if not os.path.exists(self.db_path):
                return {"error": "Database not found", "source": "sqlite"}
            
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # Check if table exists
            cursor.execute("""
                SELECT name FROM sqlite_master 
                WHERE type='table' AND name='feedback_responses'
            """)
            
            if not cursor.fetchone():
                conn.close()
                return {"error": "Feedback table not found", "source": "sqlite"}
            
            # Fetch all feedback
            cursor.execute("""
                SELECT 
                    feedback_type, feedback_value, feedback_category,
                    comment, context_metadata, created_at
                FROM feedback_responses
                ORDER BY created_at DESC
            """)
            
            rows = cursor.fetchall()
            conn.close()
            
            if not rows:
                return {"total": 0, "message": "No feedback in database", "source": "sqlite"}
            
            # Convert to feedback dict format
            feedbacks = []
            for row in rows:
                feedback_type, feedback_value, category, comment, metadata_json, created_at = row
                
                # Parse metadata
                try:
                    metadata = json.loads(metadata_json) if metadata_json else {}
                except:
                    metadata = {}
                
                # Normalize to common format
                feedbacks.append({
                    "timestamp": created_at,
                    "feedback": "positive" if feedback_value > 0 else "negative",
                    "feedback_type": feedback_type,
                    "search_depth": metadata.get("search_depth"),
                    "confidence": metadata.get("confidence"),
                    "response_time_ms": metadata.get("response_time_ms"),
                    "num_rag_results": metadata.get("num_rag_results"),
                    "reason": comment,
                    "category": category
                })
            
            return self._compute_statistics(feedbacks, source="sqlite")
            
        except Exception as e:
            logger.error(f"Failed to get statistics from DB: {e}")
            return {"error": str(e), "source": "sqlite"}
    
    def _compute_statistics(self, feedbacks: List[Dict], source: str) -> Dict[str, Any]:
        """Compute statistics from feedback list."""
        total = len(feedbacks)
        positive = sum(1 for f in feedbacks if f['feedback'] == 'positive')
        negative = total - positive
        
        # Gruppiere nach search_depth
        by_depth = {}
        for f in feedbacks:
            k = f.get('search_depth')
            if k is not None:
                if k not in by_depth:
                    by_depth[k] = {'positive': 0, 'negative': 0}
                by_depth[k][f['feedback']] += 1
        
        # Berechne Satisfaction Rate pro Depth
        satisfaction_by_depth = {}
        for k, counts in by_depth.items():
            total_k = counts['positive'] + counts['negative']
            satisfaction_by_depth[k] = round(counts['positive'] / total_k * 100, 1) if total_k > 0 else 0
        
        # Negative Feedback Gründe
        negative_reasons: Dict[str, int] = {}
        for f in feedbacks:
            if f['feedback'] == 'negative' and f.get('reason'):
                reason = f['reason']
                negative_reasons[reason] = negative_reasons.get(reason, 0) + 1
        
        # Category breakdown (if available)
        by_category = {}
        for f in feedbacks:
            cat = f.get('category')
            if cat:
                if cat not in by_category:
                    by_category[cat] = {'positive': 0, 'negative': 0}
                by_category[cat][f['feedback']] += 1
        
        return {
            "total": total,
            "positive": positive,
            "negative": negative,
            "satisfaction_rate": round(positive / total * 100, 1) if total > 0 else 0,
            "by_search_depth": satisfaction_by_depth,
            "by_category": by_category if by_category else None,
            "negative_reasons": negative_reasons,
            "avg_response_time_ms": self._avg_response_time(feedbacks),
            "source": source
        }
    
    def _merge_statistics(self, jsonl_stats: Dict, db_stats: Dict) -> Dict[str, Any]:
        """Merge statistics from both backends.
        
        In hybrid mode each feedback is written to BOTH backends, so the
        row-counts are identical.  Instead of adding them (which would
        double-count) we prefer the SQLite stats (richer schema) and fall
        back to JSONL when SQLite is unavailable.
        """
        if "error" in jsonl_stats and "error" in db_stats:
            return {"error": "No data in either backend", "source": "combined"}
        
        if "error" in db_stats:
            jsonl_stats["source"] = "combined (jsonl only)"
            return jsonl_stats
        
        # SQLite available — use it as single source of truth (no double-count)
        db_stats["source"] = "combined (sqlite preferred)"
        return db_stats
    
    def _avg_response_time(self, feedbacks: List[Dict]) -> Optional[float]:
        """Berechne durchschnittliche Response Time."""
        times: List[float] = [f['response_time_ms'] for f in feedbacks if f.get('response_time_ms') is not None]
        if times:
            return round(float(sum(times)) / len(times), 2)
        return None
    
    def get_recent_feedbacks(
        self, 
        limit: int = 10, 
        source: Literal["jsonl", "sqlite", "combined"] = "combined"
    ) -> List[Dict[str, Any]]:
        """
        Hole die letzten N Feedbacks.
        
        Args:
            limit: Anzahl der Feedbacks
            source: Data source - "jsonl", "sqlite", or "combined"
        
        Returns:
            Liste der letzten Feedbacks
        """
        try:
            feedbacks = []
            
            # Get from JSONL
            if source in ["jsonl", "combined"] and self.backend in ["jsonl", "hybrid"]:
                if os.path.exists(self.feedback_file):
                    with open(self.feedback_file, 'r', encoding='utf-8') as f:
                        for line in f:
                            if line.strip():
                                feedbacks.append(json.loads(line))
            
            # Get from SQLite
            if source in ["sqlite", "combined"] and self.backend in ["sqlite", "hybrid"]:
                db_feedbacks = self._get_recent_from_db(limit)
                feedbacks.extend(db_feedbacks)
            
            # Sort by timestamp and return last N
            feedbacks.sort(key=lambda x: x.get('timestamp', ''), reverse=True)
            return feedbacks[:limit] if feedbacks else []
            
        except Exception as e:
            logger.error(f"Failed to get recent feedbacks: {e}")
            return []
    
    def _get_recent_from_db(self, limit: int) -> List[Dict[str, Any]]:
        """Get recent feedbacks from database."""
        try:
            if not os.path.exists(self.db_path):
                return []
            
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT 
                    feedback_type, feedback_value, comment,
                    context_metadata, created_at
                FROM feedback_responses
                ORDER BY created_at DESC
                LIMIT ?
            """, (limit,))
            
            rows = cursor.fetchall()
            conn.close()
            
            feedbacks = []
            for row in rows:
                feedback_type, feedback_value, comment, metadata_json, created_at = row
                
                try:
                    metadata = json.loads(metadata_json) if metadata_json else {}
                except:
                    metadata = {}
                
                feedbacks.append({
                    "timestamp": created_at,
                    "feedback": "positive" if feedback_value > 0 else "negative",
                    "query": metadata.get("query_preview", ""),
                    "response": metadata.get("response_preview", ""),
                    "search_depth": metadata.get("search_depth"),
                    "confidence": metadata.get("confidence"),
                    "reason": comment
                })
            
            return feedbacks
            
        except Exception as e:
            logger.error(f"Failed to get recent feedbacks from DB: {e}")
            return []
    
    def _get_all_from_db(self) -> List[Dict[str, Any]]:
        """Get all feedbacks from database."""
        try:
            if not os.path.exists(self.db_path):
                return []
            
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT 
                    feedback_type, feedback_value, comment,
                    context_metadata, created_at, feedback_category
                FROM feedback_responses
                ORDER BY created_at DESC
            """)
            
            rows = cursor.fetchall()
            conn.close()
            
            feedbacks = []
            for row in rows:
                feedback_type, feedback_value, comment, metadata_json, created_at, category = row
                
                try:
                    metadata = json.loads(metadata_json) if metadata_json else {}
                except:
                    metadata = {}
                
                feedbacks.append({
                    "timestamp": created_at,
                    "feedback": "positive" if feedback_value > 0 else "negative",
                    "search_depth": metadata.get("search_depth"),
                    "confidence": metadata.get("confidence"),
                    "response_time_ms": metadata.get("response_time_ms"),
                    "num_rag_results": metadata.get("num_rag_results"),
                    "reason": comment,
                    "category": category
                })
            
            return feedbacks
            
        except Exception as e:
            logger.error(f"Failed to get all feedbacks from DB: {e}")
            return []
    
    def get_optimization_insights(
        self,
        min_samples: int = 10,
        source: Literal["jsonl", "sqlite", "combined"] = "sqlite"
    ) -> Dict[str, Any]:
        """
        Analysiert Feedback für Optimierungs-Insights.
        
        Liefert konkrete Empfehlungen für Gewichts-Anpassungen
        basierend auf User-Feedback.
        
        Args:
            min_samples: Minimum Anzahl Samples pro Analyse
            source: Data source — default "sqlite" to avoid double-counting
                   in hybrid mode (each feedback is written to both backends)
        
        Returns:
            Dict mit Optimierungs-Empfehlungen
        """
        try:
            # Collect feedbacks from selected source(s)
            feedbacks = []
            
            if source == "combined" and self.backend == "hybrid":
                # Hybrid: use only ONE backend to avoid double-counting
                db_feedbacks = self._get_all_from_db()
                feedbacks = db_feedbacks if db_feedbacks else feedbacks
                if not feedbacks and os.path.exists(self.feedback_file):
                    with open(self.feedback_file, 'r', encoding='utf-8') as f:
                        for line in f:
                            if line.strip():
                                feedbacks.append(json.loads(line))
            else:
                if source in ["jsonl", "combined"] and self.backend in ["jsonl", "hybrid"]:
                    if os.path.exists(self.feedback_file):
                        with open(self.feedback_file, 'r', encoding='utf-8') as f:
                            for line in f:
                                if line.strip():
                                    feedbacks.append(json.loads(line))
                
                if source in ["sqlite", "combined"] and self.backend in ["sqlite", "hybrid"]:
                    db_feedbacks = self._get_all_from_db()
                    feedbacks.extend(db_feedbacks)
            
            if not feedbacks:
                return {"error": "No feedback data available"}
            
            if len(feedbacks) < min_samples:
                return {
                    "status": "insufficient_data",
                    "samples": len(feedbacks),
                    "required": min_samples,
                    "recommendation": "neutral"
                }
            
            # Analyse: Korrelation zwischen Search-Depth und Satisfaction
            depth_satisfaction = {}
            for fb in feedbacks:
                depth = fb.get('search_depth')
                if depth is not None:
                    if depth not in depth_satisfaction:
                        depth_satisfaction[depth] = {'positive': 0, 'negative': 0}
                    depth_satisfaction[depth][fb['feedback']] += 1
            
            # Finde optimale Search-Depth
            best_depth = None
            best_rate = 0.0
            for depth, counts in depth_satisfaction.items():
                total = counts['positive'] + counts['negative']
                if total >= 3:  # Min 3 samples
                    rate = counts['positive'] / total
                    if rate > best_rate:
                        best_rate = rate
                        best_depth = depth
            
            # Analyse: Häufigste Negativ-Gründe
            negative_reasons: Dict[str, int] = {}
            for fb in feedbacks:
                if fb['feedback'] == 'negative' and fb.get('reason'):
                    reason = fb['reason']
                    negative_reasons[reason] = negative_reasons.get(reason, 0) + 1
            
            # Empfehlungen basierend auf häufigsten Problemen
            recommendations = []
            
            if negative_reasons.get('Zu wenig Ergebnisse', 0) > len(feedbacks) * 0.2:
                recommendations.append({
                    'issue': 'Zu wenig Ergebnisse',
                    'action': 'increase_kg_weight',
                    'reason': '>20% der negativen Feedbacks',
                    'suggested_adjustment': +0.1
                })
            
            if negative_reasons.get('Irrelevante Ergebnisse', 0) > len(feedbacks) * 0.3:
                recommendations.append({
                    'issue': 'Irrelevante Ergebnisse',
                    'action': 'increase_faiss_weight',
                    'reason': '>30% der negativen Feedbacks',
                    'suggested_adjustment': +0.1
                })
            
            if negative_reasons.get('Veraltete Informationen', 0) > len(feedbacks) * 0.15:
                recommendations.append({
                    'issue': 'Veraltete Informationen',
                    'action': 'increase_recency_boost',
                    'reason': '>15% der negativen Feedbacks',
                    'suggested_adjustment': +0.02
                })
            
            # Gesamtzufriedenheit
            total = len(feedbacks)
            positive = sum(1 for fb in feedbacks if fb['feedback'] == 'positive')
            satisfaction_rate = positive / total if total > 0 else 0
            
            return {
                "status": "ready",
                "samples": len(feedbacks),
                "satisfaction_rate": round(satisfaction_rate, 3),
                "optimal_search_depth": best_depth,
                "optimal_depth_satisfaction": round(best_rate, 3),
                "negative_reasons": negative_reasons,
                "recommendations": recommendations,
                "requires_action": len(recommendations) > 0 or satisfaction_rate < 0.7
            }
            
        except Exception as e:
            logger.error(f"Failed to get optimization insights: {e}")
            return {"error": str(e)}
    
    def get_advanced_analytics(self) -> Dict[str, Any]:
        """
        Get advanced analytics from database (DB-only feature).
        
        Returns rich analytics including:
        - Temporal trends
        - Category breakdown
        - User engagement metrics
        - RAG parameter correlations
        """
        try:
            if not os.path.exists(self.db_path):
                return {"error": "Database not found"}
            
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # Check if table exists
            cursor.execute("""
                SELECT name FROM sqlite_master 
                WHERE type='table' AND name='feedback_responses'
            """)
            
            if not cursor.fetchone():
                conn.close()
                return {"error": "Feedback table not found"}
            
            # Get total counts
            cursor.execute("SELECT COUNT(*) FROM feedback_responses")
            total = cursor.fetchone()[0]
            
            if total == 0:
                conn.close()
                return {"total": 0, "message": "No feedback data"}
            
            # Get feedback breakdown by type
            cursor.execute("""
                SELECT feedback_type, feedback_value, COUNT(*) 
                FROM feedback_responses 
                GROUP BY feedback_type, feedback_value
            """)
            feedback_breakdown = {}
            for row in cursor.fetchall():
                feedback_type, value, count = row
                feedback_breakdown[feedback_type] = {
                    "count": count,
                    "value": value,
                    "percentage": round(count / total * 100, 1)
                }
            
            # Get category breakdown
            cursor.execute("""
                SELECT feedback_category, 
                       SUM(CASE WHEN feedback_value > 0 THEN 1 ELSE 0 END) as positive,
                       SUM(CASE WHEN feedback_value < 0 THEN 1 ELSE 0 END) as negative
                FROM feedback_responses 
                WHERE feedback_category IS NOT NULL
                GROUP BY feedback_category
            """)
            category_breakdown = {}
            for row in cursor.fetchall():
                category, pos, neg = row
                total_cat = pos + neg
                category_breakdown[category] = {
                    "positive": pos,
                    "negative": neg,
                    "satisfaction_rate": round(pos / total_cat * 100, 1) if total_cat > 0 else 0
                }
            
            # Get temporal trends (last 30 days)
            cursor.execute("""
                SELECT DATE(created_at) as date, 
                       SUM(CASE WHEN feedback_value > 0 THEN 1 ELSE 0 END) as positive,
                       SUM(CASE WHEN feedback_value < 0 THEN 1 ELSE 0 END) as negative
                FROM feedback_responses 
                WHERE created_at >= datetime('now', '-30 days')
                GROUP BY DATE(created_at)
                ORDER BY date
            """)
            temporal_trends = []
            for row in cursor.fetchall():
                date, pos, neg = row
                temporal_trends.append({
                    "date": date,
                    "positive": pos,
                    "negative": neg,
                    "total": pos + neg
                })
            
            # Get recent comments (top 5 negative)
            cursor.execute("""
                SELECT comment, created_at
                FROM feedback_responses
                WHERE feedback_value < 0 AND comment IS NOT NULL
                ORDER BY created_at DESC
                LIMIT 5
            """)
            recent_negative_comments = [
                {"comment": row[0], "timestamp": row[1]}
                for row in cursor.fetchall()
            ]
            
            conn.close()
            
            return {
                "total": total,
                "feedback_breakdown": feedback_breakdown,
                "category_breakdown": category_breakdown,
                "temporal_trends": temporal_trends,
                "recent_negative_comments": recent_negative_comments,
                "analytics_available": True
            }
            
        except Exception as e:
            logger.error(f"Failed to get advanced analytics: {e}")
            return {"error": str(e)}

    # ----------------------------------------------------------------
    # Singleton Access
    # ----------------------------------------------------------------

    @classmethod
    def get_instance(cls, **kwargs) -> "FeedbackLogger":
        """Singleton-Access zum FeedbackLogger."""
        global _feedback_logger_instance
        if _feedback_logger_instance is None:
            _feedback_logger_instance = cls(
                feedback_file=kwargs.pop("feedback_file", "user_feedback.jsonl"),
                db_path=kwargs.pop("db_path", "agent/rag_store.db"),
                backend=kwargs.pop("backend", "hybrid"),
                user_id=kwargs.pop("user_id", None),
            )
        return _feedback_logger_instance


def reset_feedback_logger():
    """Reset des FeedbackLogger-Singletons (fuer Tests)."""
    global _feedback_logger_instance
    _feedback_logger_instance = None


# Global feedback logger instance (hybrid backend by default)
feedback_logger = FeedbackLogger(
    feedback_file="user_feedback.jsonl",
    db_path="agent/rag_store.db",
    backend="hybrid"
)


def log_user_feedback(
    query: str,
    response: str,
    feedback: str,
    **kwargs
) -> bool:
    """
    Convenience function to log user feedback.
    
    Args:
        query: User query
        response: Bot response
        feedback: "positive" or "negative"
        **kwargs: Additional parameters (search_depth, confidence, reason, etc.)
    
    Returns:
        True if successful
    """
    return feedback_logger.log_feedback(query, response, feedback, **kwargs)


def get_feedback_statistics() -> Dict[str, Any]:
    """
    Get feedback statistics.
    
    Returns:
        Dictionary with statistics
    """
    return feedback_logger.get_statistics()
