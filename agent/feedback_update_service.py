"""
🔄 Feedback Update Service
==========================

Periodischer Service der:
1. Feedback aus feedback_logger liest
2. FeedbackOptimizer aktualisiert
3. SmartFusionEngine mit neuen Gewichten updated
4. ★ SOTA v3: Wilson-Score Chunk-Utility Scores laden + an FusionEngine übergeben

Author: AI Assistant
Date: 2025-10-22, Updated: 2026-03-23 (SOTA v3)
"""

import logging
import time
from typing import Optional
import os
import sys

# Add paths
parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)

from utils.feedback_logger import feedback_logger
from agent.feedback_optimizer import get_global_optimizer

logger = logging.getLogger(__name__)


class FeedbackUpdateService:
    """
    Service der Feedback-Schleife schließt
    
    Workflow:
    1. Liest Feedback-Daten
    2. Berechnet Optimierungs-Insights
    3. Updated Optimizer
    4. Propagiert zu Fusion Engine
    """
    
    def __init__(
        self,
        smart_fusion_engine=None,
        update_interval_seconds: int = 300,  # 5 Minuten
        min_feedbacks_for_update: int = 10
    ):
        """
        Initialize Service
        
        Args:
            smart_fusion_engine: SmartFusionEngine Instanz
            update_interval_seconds: Wie oft Updates prüfen
            min_feedbacks_for_update: Min Feedbacks vor Update
        """
        self.fusion_engine = smart_fusion_engine
        self.update_interval = update_interval_seconds
        self.min_feedbacks = min_feedbacks_for_update
        self.optimizer = get_global_optimizer()
        self._db_path: Optional[str] = None  # ★ SOTA v3: DB path for Wilson scores
        
        self.last_update_time: float = 0.0
        self.total_updates: int = 0
        
        logger.info(
            f"✅ FeedbackUpdateService initialized "
            f"(interval={update_interval_seconds}s)"
        )
    
    def set_db_path(self, db_path: str) -> None:
        """★ SOTA v3: Set DB path for Wilson score computation."""
        self._db_path = db_path
        logger.info(f"✅ FeedbackUpdateService DB path set: {db_path}")
    
    def load_wilson_utility_scores(self) -> int:
        """
        ★ SOTA v3: Load Wilson-Score chunk utility from DB and push to FusionEngine.
        
        This activates the Wilson boost in SmartFusionEngine._apply_wilson_boost()
        which was previously always returning 0 (dead code) because
        set_utility_scores() was never called.
        
        Returns: number of chunks with utility scores
        """
        if not self.fusion_engine or not self._db_path:
            return 0
        
        try:
            from agent.rag_store.core.quality import RAGQualityManager
            qm = RAGQualityManager(db_path=self._db_path)
            conn = qm._get_connection()
            try:
                utility_scores = qm.compute_chunk_utility_scores(conn)
            finally:
                conn.close()
            
            if utility_scores:
                self.fusion_engine.set_utility_scores(utility_scores)
                logger.info(f"★ Wilson utility scores loaded: {len(utility_scores)} chunks")
            return len(utility_scores)
        except Exception as e:
            logger.warning(f"⚠️ Wilson score loading failed: {e}")
            return 0
    
    def set_fusion_engine(self, fusion_engine):
        """Setzt Fusion Engine (falls nicht beim Init verfügbar)"""
        self.fusion_engine = fusion_engine
        logger.info("✅ Fusion Engine connected to FeedbackUpdateService")
    
    def check_and_update(self, force: bool = False) -> dict:
        """
        Prüft ob Update nötig und führt es durch
        
        Args:
            force: Force Update unabhängig von Interval
        
        Returns:
            Dict mit Update-Info
        """
        current_time = time.time()
        
        # Check Interval
        if not force and (current_time - self.last_update_time) < self.update_interval:
            return {
                "checked": False,
                "reason": "interval_not_reached",
                "next_check_in": self.update_interval - (current_time - self.last_update_time)
            }
        
        try:
            # ★ SOTA v4: Wilson-Chunk-Utility unabhängig von der Insights-
            # Readiness aktualisieren. Wilson-Lower-Bounds sind bereits ab
            # 1 Feedback-Sample konservativ sinnvoll; nur die FAISS/KG-
            # Fusionsgewichte brauchen stabile Stichproben (min_feedbacks).
            # Vorher lief das Refresh erst NACH dem "ready"-Gate -> bei
            # < min_feedbacks Samples blieb der adaptive Chunk-Boost bei 0.0.
            self.load_wilson_utility_scores()

            # 1. Hole Feedback-Insights
            insights = feedback_logger.get_optimization_insights(
                min_samples=self.min_feedbacks
            )
            
            if insights.get("status") != "ready":
                logger.debug(
                    f"📊 Feedback not ready: {insights.get('status')} "
                    f"({insights.get('samples', 0)}/{self.min_feedbacks} samples)"
                )
                self.last_update_time = current_time
                return {
                    "checked": True,
                    "updated": False,
                    "reason": insights.get("status"),
                    "insights": insights
                }
            
            # 2. Update Optimizer
            update_result = self.optimizer.update_from_feedback(insights)
            
            # 3. Propagiere zu Fusion Engine
            if update_result.get("updated") and self.fusion_engine:
                new_faiss, new_kg = self.optimizer.get_current_weights()
                self.fusion_engine.set_adaptive_weights(new_faiss, new_kg)
                
                logger.info(
                    f"✅ Feedback-based update applied: "
                    f"FAISS={new_faiss:.2f}, KG={new_kg:.2f} "
                    f"(Satisfaction: {insights.get('satisfaction_rate', 0):.1%})"
                )
                
                self.total_updates += 1            
            # 4. Check ob Rollback nötig
            if self.optimizer.should_rollback():
                if self.optimizer.rollback():
                    # Propagiere Rollback
                    if self.fusion_engine:
                        rolled_back_faiss, rolled_back_kg = self.optimizer.get_current_weights()
                        self.fusion_engine.set_adaptive_weights(rolled_back_faiss, rolled_back_kg)
                    
                    logger.warning("↩️ Rolled back weights due to satisfaction drop")
            
            self.last_update_time = current_time
            
            return {
                "checked": True,
                "updated": update_result.get("updated", False),
                "insights": insights,
                "update_result": update_result,
                "optimizer_stats": self.optimizer.get_statistics()
            }
            
        except Exception as e:
            logger.error(f"❌ Feedback update failed: {e}")
            return {
                "checked": True,
                "updated": False,
                "error": str(e)
            }
    
    def get_status(self) -> dict:
        """
        Liefert aktuellen Status
        
        Returns:
            Dict mit Status-Info
        """
        current_weights = self.optimizer.get_current_weights()
        
        return {
            "total_updates": self.total_updates,
            "last_update_time": self.last_update_time,
            "current_weights": {
                "faiss": round(current_weights[0], 3),
                "kg": round(current_weights[1], 3)
            },
            "optimizer_stats": self.optimizer.get_statistics(),
            "fusion_engine_connected": self.fusion_engine is not None
        }


# Global service instance
_global_service: Optional[FeedbackUpdateService] = None


def get_global_service() -> FeedbackUpdateService:
    """
    Gibt globale Service-Instanz zurück (Singleton)
    
    Returns:
        FeedbackUpdateService
    """
    global _global_service
    if _global_service is None:
        _global_service = FeedbackUpdateService()
    return _global_service


def reset_service():
    """Reset globale Service-Instanz (für Tests)"""
    global _global_service
    _global_service = None


if __name__ == "__main__":
    # Test
    logging.basicConfig(level=logging.INFO)
    
    print("=" * 60)
    print("🔄 FeedbackUpdateService Test")
    print("=" * 60)
    
    service = FeedbackUpdateService(update_interval_seconds=0)
    
    print("\n📊 Initial Status:")
    status = service.get_status()
    print(f"   Total Updates: {status['total_updates']}")
    print(f"   Current Weights: FAISS={status['current_weights']['faiss']}, "
          f"KG={status['current_weights']['kg']}")
    
    print("\n🔄 Running check...")
    result = service.check_and_update(force=True)
    
    print(f"\n✅ Check Result:")
    print(f"   Checked: {result['checked']}")
    print(f"   Updated: {result.get('updated', False)}")
    if 'insights' in result:
        insights = result['insights']
        print(f"   Samples: {insights.get('samples', 0)}")
        print(f"   Satisfaction: {insights.get('satisfaction_rate', 0):.1%}")
    
    print("\n📊 Final Status:")
    status = service.get_status()
    print(f"   Total Updates: {status['total_updates']}")
    print(f"   Current Weights: FAISS={status['current_weights']['faiss']}, "
          f"KG={status['current_weights']['kg']}")
    
    print("\n✅ Test completed!")
