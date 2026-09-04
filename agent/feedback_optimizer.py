"""
🔄 Feedback-Based Optimization System
=====================================

Schließt die Feedback-Schleife: User-Feedback → Gewichts-Anpassung

Author: AI Assistant
Date: 2025-10-22
"""

import logging
from typing import Dict, Any, List, Optional, Tuple
import os
import sys

# Füge utils zum Path hinzu
parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)

logger = logging.getLogger(__name__)


class FeedbackOptimizer:
    """
    Optimiert Fusion-Gewichte basierend auf User-Feedback
    
    Features:
    - Analysiert User-Feedback aus feedback_logger
    - Berechnet optimale Gewichts-Anpassungen
    - Graduelle, konservative Updates
    - Rollback bei Verschlechterung
    """
    
    def __init__(
        self,
        initial_faiss_weight: float = 0.7,
        initial_kg_weight: float = 0.3,
        learning_rate: float = 0.05,
        min_samples_for_update: int = 10
    ):
        """
        Initialize FeedbackOptimizer
        
        Args:
            initial_faiss_weight: Start-Gewicht für FAISS
            initial_kg_weight: Start-Gewicht für KG
            learning_rate: Wie stark Gewichte angepasst werden (0.01-0.1)
            min_samples_for_update: Min Feedback-Samples vor Update
        """
        self.faiss_weight = initial_faiss_weight
        self.kg_weight = initial_kg_weight
        self.learning_rate = learning_rate
        self.min_samples = min_samples_for_update
        
        # History für Rollback
        self.weight_history = [(initial_faiss_weight, initial_kg_weight)]
        self.satisfaction_history: List[float] = []
        
        # Statistiken
        self.total_updates = 0
        self.successful_updates = 0
        
        logger.info(
            f"✅ FeedbackOptimizer initialized: "
            f"FAISS={initial_faiss_weight}, KG={initial_kg_weight}, "
            f"lr={learning_rate}"
        )
    
    def get_current_weights(self) -> Tuple[float, float]:
        """
        Liefert aktuelle Gewichte
        
        Returns:
            (faiss_weight, kg_weight)
        """
        return self.faiss_weight, self.kg_weight
    
    def update_from_feedback(self, feedback_insights: Dict[str, Any]) -> Dict[str, Any]:
        """
        Aktualisiert Gewichte basierend auf Feedback-Insights
        
        Args:
            feedback_insights: Dict von FeedbackLogger.get_optimization_insights()
        
        Returns:
            Dict mit Update-Informationen
        """
        # Check ob genug Daten
        if feedback_insights.get("status") != "ready":
            return {
                "updated": False,
                "reason": feedback_insights.get("status", "unknown"),
                "samples": feedback_insights.get("samples", 0)
            }
        
        samples = feedback_insights.get("samples", 0)
        if samples < self.min_samples:
            return {
                "updated": False,
                "reason": "insufficient_samples",
                "samples": samples,
                "required": self.min_samples
            }
        
        # Aktuelle Zufriedenheit
        satisfaction = feedback_insights.get("satisfaction_rate", 0.5)
        self.satisfaction_history.append(satisfaction)
        
        # Empfehlungen verarbeiten
        recommendations = feedback_insights.get("recommendations", [])
        
        if not recommendations and satisfaction >= 0.7:
            logger.info(f"✅ No changes needed. Satisfaction: {satisfaction:.1%}")
            return {
                "updated": False,
                "reason": "satisfaction_good",
                "satisfaction": satisfaction,
                "faiss_weight": self.faiss_weight,
                "kg_weight": self.kg_weight
            }
        
        # Backup aktuelle Gewichte
        old_faiss = self.faiss_weight
        old_kg = self.kg_weight
        
        # Anwende Empfehlungen
        adjustments_made = []
        
        for rec in recommendations:
            action = rec.get('action')
            adjustment = rec.get('suggested_adjustment', 0.1) * self.learning_rate
            
            if action == 'increase_kg_weight':
                # Mehr KG, weniger FAISS
                self.kg_weight = min(0.9, self.kg_weight + adjustment)
                self.faiss_weight = 1.0 - self.kg_weight
                adjustments_made.append(f"KG +{adjustment:.2f}")
                
            elif action == 'increase_faiss_weight':
                # Mehr FAISS, weniger KG
                self.faiss_weight = min(0.9, self.faiss_weight + adjustment)
                self.kg_weight = 1.0 - self.faiss_weight
                adjustments_made.append(f"FAISS +{adjustment:.2f}")
        
        # Bei schlechter Zufriedenheit ohne spezifische Empfehlung: Zurück zu Defaults
        if not adjustments_made and satisfaction < 0.5:
            self.faiss_weight = 0.7
            self.kg_weight = 0.3
            adjustments_made.append("Reset to defaults")
        
        # Speichere neuen Zustand
        if adjustments_made:
            self.weight_history.append((self.faiss_weight, self.kg_weight))
            self.total_updates += 1
            
            logger.info(
                f"🔄 Weights updated: "
                f"FAISS {old_faiss:.2f}→{self.faiss_weight:.2f}, "
                f"KG {old_kg:.2f}→{self.kg_weight:.2f} "
                f"(Satisfaction: {satisfaction:.1%})"
            )
            
            return {
                "updated": True,
                "old_weights": (old_faiss, old_kg),
                "new_weights": (self.faiss_weight, self.kg_weight),
                "adjustments": adjustments_made,
                "satisfaction": satisfaction,
                "recommendations_applied": len(recommendations)
            }
        
        return {
            "updated": False,
            "reason": "no_adjustments_needed",
            "satisfaction": satisfaction
        }
    
    def rollback(self) -> bool:
        """
        Rollback zu vorherigen Gewichten
        
        Returns:
            True wenn Rollback erfolgreich
        """
        if len(self.weight_history) <= 1:
            logger.warning("⚠️ Cannot rollback: no history")
            return False
        
        # Entferne aktuelle Gewichte
        self.weight_history.pop()
        
        # Restore vorherige
        self.faiss_weight, self.kg_weight = self.weight_history[-1]
        
        logger.info(
            f"↩️ Rolled back to: FAISS={self.faiss_weight:.2f}, "
            f"KG={self.kg_weight:.2f}"
        )
        return True
    
    def should_rollback(self) -> bool:
        """
        Entscheidet ob Rollback nötig ist
        
        Kriterium: Zufriedenheit hat sich verschlechtert
        
        Returns:
            True wenn Rollback empfohlen
        """
        if len(self.satisfaction_history) < 2:
            return False
        
        # Vergleiche letzte 2 Perioden
        recent = self.satisfaction_history[-1]
        previous = self.satisfaction_history[-2]
        
        # Rollback wenn deutliche Verschlechterung (>10%)
        if recent < previous - 0.1:
            logger.warning(
                f"⚠️ Satisfaction dropped: {previous:.1%} → {recent:.1%}. "
                "Recommending rollback."
            )
            return True
        
        return False
    
    def get_statistics(self) -> Dict[str, Any]:
        """
        Liefert Optimierungs-Statistiken
        
        Returns:
            Dict mit Stats
        """
        return {
            "current_weights": {
                "faiss": round(self.faiss_weight, 3),
                "kg": round(self.kg_weight, 3)
            },
            "total_updates": self.total_updates,
            "successful_updates": self.successful_updates,
            "weight_history": [
                {"faiss": round(w[0], 3), "kg": round(w[1], 3)}
                for w in self.weight_history[-5:]  # Last 5
            ],
            "satisfaction_history": [
                round(s, 3) for s in self.satisfaction_history[-5:]
            ],
            "current_satisfaction": (
                round(self.satisfaction_history[-1], 3)
                if self.satisfaction_history else None
            )
        }


# Globale Optimizer-Instanz (Singleton)
_global_optimizer: Optional[FeedbackOptimizer] = None


def get_global_optimizer() -> FeedbackOptimizer:
    """
    Gibt globale Optimizer-Instanz zurück (Singleton)
    
    Returns:
        FeedbackOptimizer Instanz
    """
    global _global_optimizer
    if _global_optimizer is None:
        _global_optimizer = FeedbackOptimizer()
    return _global_optimizer


def reset_optimizer():
    """Reset globale Optimizer-Instanz (für Tests)"""
    global _global_optimizer
    _global_optimizer = None


if __name__ == "__main__":
    # Test
    logging.basicConfig(level=logging.INFO)
    
    print("=" * 60)
    print("🔄 FeedbackOptimizer Test")
    print("=" * 60)
    
    optimizer = FeedbackOptimizer()
    
    # Simuliere Feedback-Insights
    insights = {
        "status": "ready",
        "samples": 20,
        "satisfaction_rate": 0.65,
        "recommendations": [
            {
                "issue": "Irrelevante Ergebnisse",
                "action": "increase_faiss_weight",
                "suggested_adjustment": 0.1
            }
        ]
    }
    
    print("\n📊 Initial Weights:")
    print(f"   FAISS: {optimizer.faiss_weight:.2f}")
    print(f"   KG: {optimizer.kg_weight:.2f}")
    
    print("\n🔄 Applying feedback...")
    result = optimizer.update_from_feedback(insights)
    
    print(f"\n✅ Update Result:")
    print(f"   Updated: {result['updated']}")
    if result['updated']:
        print(f"   New FAISS: {result['new_weights'][0]:.2f}")
        print(f"   New KG: {result['new_weights'][1]:.2f}")
        print(f"   Adjustments: {result['adjustments']}")
    
    print("\n📈 Statistics:")
    stats = optimizer.get_statistics()
    print(f"   Total Updates: {stats['total_updates']}")
    print(f"   Current Satisfaction: {stats['current_satisfaction']}")
    
    print("\n✅ Test completed!")
