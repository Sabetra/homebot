"""Closed-Loop Auto-Tuner: Eval → Config Bridge.
=============================================

Verbindet StrixKAT-Eval-Ergebnisse mit ConfigManager, sodass bei
Qualitäts-Regression automatisch Konfigurations-Anpassungen erfolgen.

Pipeline:
  StrixKAT.check_regression()
    -> Regression detected?
      -> AutoTuner.compute_tuning_recommendations()
        -> ConfigManager.apply overrides (live, mit Safeguards)

Safeguards:
  - Nur begrenzte Parameter werden automatisch angepasst
  - Alle Änderungen sind reversibel (snapshot/restore)
  - Schwellenwerte sind konfigurierbar
  - Jede Änderung wird geloggt

Usage:
    from agent.auto_tuner import AutoTuner
    tuner = AutoTuner(strixkat_engine, config_manager)
    tuner.run_diagnosis()  # einmalig oder periodic via SOTAPipeline
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data Contracts
# ---------------------------------------------------------------------------

@dataclass
class TuningAction:
    """Eine einzelne Auto-Tuning-Aktion."""
    parameter: str           # z.B. "rag.similarity_threshold"
    old_value: Any
    new_value: Any
    reason: str
    confidence: float        # 0.0 - 1.0, wie sicher ist die Empfehlung
    reversible: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "parameter": self.parameter,
            "old_value": self.old_value,
            "new_value": self.new_value,
            "reason": self.reason,
            "confidence": self.confidence,
            "reversible": self.reversible,
        }


@dataclass
class DiagnosisReport:
    """Bericht einer vollständigen Selbst-Diagnose."""
    timestamp: str
    overall_score: Optional[float]
    faithfulness: Optional[float]
    answer_relevance: Optional[float]
    context_precision: Optional[float]
    trend: str
    regression_detected: bool
    actions_taken: List[Dict[str, Any]]
    recommendations: List[str]
    feedback_insights: Optional[Dict[str, Any]] = None
    eval_count: int = 0
    baseline_mean: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "overall_score": self.overall_score,
            "faithfulness": self.faithfulness,
            "answer_relevance": self.answer_relevance,
            "context_precision": self.context_precision,
            "trend": self.trend,
            "regression_detected": self.regression_detected,
            "actions_taken": self.actions_taken,
            "recommendations": self.recommendations,
            "feedback_insights": self.feedback_insights,
            "eval_count": self.eval_count,
            "baseline_mean": self.baseline_mean,
        }


# ---------------------------------------------------------------------------
# Allowed auto-tuning parameters (Safeguard: nur diese d黵fen ge鋘dert werden)
# ---------------------------------------------------------------------------

ALLOWED_TUNING_PARAMS: Dict[str, Dict[str, Any]] = {
    "rag.similarity_threshold": {
        "min": 0.3,
        "max": 0.95,
        "step": 0.05,
        "default": 0.75,
    },
    "rag.max_evidence_chunks": {
        "min": 4,
        "max": 16,
        "step": 2,
        "default": 8,
    },
    "rag.self_rag_critiquity_threshold": {
        "min": 0.3,
        "max": 0.9,
        "step": 0.05,
        "default": 0.6,
    },
}


# ---------------------------------------------------------------------------
# AutoTuner
# ---------------------------------------------------------------------------

class AutoTuner:
    """
    Closed-Loop Auto-Tuner.

    Verbindet StrixKATEval mit ConfigManager und wendet automatisch
    Konfigurations-Anpassungen an, wenn die Qualit鋞 nachl鋝st.
    """

    def __init__(
        self,
        strixkat_engine: Any,
        config_manager: Any,
        feedback_logger: Any = None,
        quality_threshold: float = 0.75,
        regression_threshold: float = 0.05,
        min_eval_samples: int = 10,
        auto_apply: bool = True,
    ):
        """
        Args:
            strixkat_engine: StrixKATEvalEngine-Instanz
            config_manager: ConfigManager-Instanz
            feedback_logger: FeedbackLogger-Instanz (optional)
            quality_threshold: Untere Grenze f黵 "gute" Qualit鋞
            regression_threshold: Schwellenwert f黵 Regression-Detection
            min_eval_samples: Mindestens so viele Eval-Samples ben鰐igt
            auto_apply: Wenn True, werden Anpassungen automatisch angewendet
        """
        self._strixkat = strixkat_engine
        self._config = config_manager
        self._feedback = feedback_logger
        self._lock = threading.RLock()

        self.quality_threshold = quality_threshold
        self.regression_threshold = regression_threshold
        self.min_eval_samples = min_eval_samples
        self.auto_apply = auto_apply

        # History of diagnosis reports
        self._diagnosis_history: List[DiagnosisReport] = []

        # Config snapshots for rollback
        self._config_snapshots: List[Dict[str, Any]] = []
        self._max_snapshots = 5

        logger.info(
            "AutoTuner initialized: threshold=%.2f, regression=%.2f, auto_apply=%s",
            quality_threshold, regression_threshold, auto_apply,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run_diagnosis(self) -> DiagnosisReport:
        """
        F黨rt eine vollst鋘dige Selbst-Diagnose durch:
        1. Eval-Metriken aus StrixKAT lesen
        2. Regression pr黤en
        3. Feedback-Insights sammeln
        4. Tuning-Empfehlungen berechnen
        5. Anpassungen anwenden (wenn auto_apply=True)
        """
        with self._lock:
            timestamp = datetime.now(timezone.utc).isoformat()
            logger.info("AutoTuner: starting diagnosis at %s", timestamp)

            # Step 1: Dashboard metrics from StrixKAT
            dashboard = self._strixkat.get_dashboard_metrics()
            if dashboard.get("status") == "no_data":
                report = DiagnosisReport(
                    timestamp=timestamp,
                    overall_score=None,
                    faithfulness=None,
                    answer_relevance=None,
                    context_precision=None,
                    trend="no_data",
                    regression_detected=False,
                    actions_taken=[],
                    recommendations=["Noch keine Eval-Daten verf黦bar"],
                )
                self._diagnosis_history.append(report)
                logger.info("AutoTuner: no eval data available yet")
                return report

            overall_score = dashboard.get("latest_score")
            trend = dashboard.get("trend", "unknown")
            eval_count = dashboard.get("eval_count", 0)
            baseline_mean = dashboard.get("baseline_mean")

            # Step 2: Regression check
            regression = self._strixkat.check_regression()
            regression_detected = regression.get("regression_detected", False)

            # Step 3: Get per-metric breakdown
            latest_eval = None
            if hasattr(self._strixkat, "_eval_history") and self._strixkat._eval_history:
                latest_eval = self._strixkat._eval_history[-1]

            faithfulness = None
            answer_relevance = None
            context_precision = None

            if latest_eval:
                breakdown = latest_eval.metric_breakdown()
                faithfulness = breakdown.get("faithfulness", {}).get("mean")
                answer_relevance = breakdown.get("answer_relevance", {}).get("mean")
                context_precision = breakdown.get("context_precision", {}).get("mean")

            # Step 4: Feedback insights
            feedback_insights = None
            if self._feedback and hasattr(self._feedback, "get_optimization_insights"):
                try:
                    feedback_insights = self._feedback.get_optimization_insights()
                except Exception as exc:
                    logger.warning("AutoTuner: failed to get feedback insights: %s", exc)

            # Step 5: Compute tuning recommendations
            actions = self._compute_tuning_actions(
                overall_score=overall_score,
                faithfulness=faithfulness,
                answer_relevance=answer_relevance,
                context_precision=context_precision,
                trend=trend,
                regression=regression,
                feedback_insights=feedback_insights,
            )

            # Step 6: Apply actions (with safeguards)
            actions_taken: List[Dict[str, Any]] = []
            if self.auto_apply and actions:
                # Snapshot current config before changes
                self._take_config_snapshot()

                for action in actions:
                    success = self._apply_action(action)
                    actions_taken.append({
                        **action.to_dict(),
                        "applied": success,
                    })
                    if success:
                        logger.info(
                            "AutoTuner: applied %s = %s (was %s) - %s",
                            action.parameter, action.new_value, action.old_value, action.reason,
                        )

            # Step 7: Generate recommendations
            recommendations = self._generate_recommendations(
                overall_score, faithfulness, answer_relevance,
                context_precision, trend, regression_detected,
                feedback_insights,
            )

            report = DiagnosisReport(
                timestamp=timestamp,
                overall_score=overall_score,
                faithfulness=faithfulness,
                answer_relevance=answer_relevance,
                context_precision=context_precision,
                trend=trend,
                regression_detected=regression_detected,
                actions_taken=actions_taken,
                recommendations=recommendations,
                feedback_insights=feedback_insights,
                eval_count=eval_count,
                baseline_mean=baseline_mean,
            )

            self._diagnosis_history.append(report)
            logger.info(
                "AutoTuner: diagnosis complete. score=%.3f, trend=%s, regression=%s, actions=%d",
                overall_score, trend, regression_detected, len(actions_taken),
            )
            return report

    def get_latest_diagnosis(self) -> Optional[DiagnosisReport]:
        """Gibt den letzten Diagnose-Bericht zur點k."""
        return self._diagnosis_history[-1] if self._diagnosis_history else None

    def get_diagnosis_history(self) -> List[DiagnosisReport]:
        """Gibt die gesamte Diagnose-Historie zur點k."""
        return list(self._diagnosis_history)

    def rollback_last_snapshot(self) -> bool:
        """Rollt die letzte Konfigurations-Änderung zur點k."""
        if not self._config_snapshots:
            logger.warning("AutoTuner: no snapshot to rollback")
            return False

        snapshot = self._config_snapshots.pop()
        try:
            self._restore_snapshot(snapshot)
            logger.info("AutoTuner: rolled back to snapshot")
            return True
        except Exception as exc:
            logger.error("AutoTuner: rollback failed: %s", exc)
            return False

    def get_dashboard_data(self) -> Dict[str, Any]:
        """
        Liefert Daten f黵 das Performance-Dashboard.
        Kombiniert StrixKAT-Metriken + letzte Diagnose.
        """
        dashboard = self._strixkat.get_dashboard_metrics()
        latest = self.get_latest_diagnosis()

        result = {
            "strixkat": dashboard,
            "last_diagnosis": latest.to_dict() if latest else None,
            "diagnosis_count": len(self._diagnosis_history),
        }

        # Add trend history
        if dashboard.get("status") == "ok":
            result["scores_over_time"] = dashboard.get("scores_over_time", [])
            result["trend"] = dashboard.get("trend", "unknown")

        return result

    # ------------------------------------------------------------------
    # Internal: Tuning Logic
    # ------------------------------------------------------------------

    def _compute_tuning_actions(
        self,
        overall_score: Optional[float],
        faithfulness: Optional[float],
        answer_relevance: Optional[float],
        context_precision: Optional[float],
        trend: str,
        regression: Dict[str, Any],
        feedback_insights: Optional[Dict[str, Any]],
    ) -> List[TuningAction]:
        """
        Berechnet Tuning-Aktionen basierend auf Eval-Metriken.

        Regeln:
        - Niedrige Faithfulness -> similarity_threshold erh鰄en
        - Niedrige Context Precision -> max_evidence_chunks reduzieren
        - Niedrige Answer Relevance -> self_rag_critiquity_threshold senken
        - Regression detected -> konservative Anpassungen
        """
        actions: List[TuningAction] = []

        if overall_score is None:
            return actions

        # Rule 1: Faithfulness zu niedrig
        if faithfulness is not None and faithfulness < 0.7:
            current = self._config.rag.similarity_threshold
            new_val = min(current + 0.05, 0.95)
            if new_val != current:
                actions.append(TuningAction(
                    parameter="rag.similarity_threshold",
                    old_value=current,
                    new_value=new_val,
                    reason=f"Faithfulness low ({faithfulness:.2f} < 0.70), increasing similarity threshold",
                    confidence=0.8 if faithfulness < 0.5 else 0.6,
                ))

        # Rule 2: Context Precision zu niedrig
        if context_precision is not None and context_precision < 0.6:
            current = self._config.rag.max_evidence_chunks
            new_val = max(current - 2, 4)
            if new_val != current:
                actions.append(TuningAction(
                    parameter="rag.max_evidence_chunks",
                    old_value=current,
                    new_value=new_val,
                    reason=f"Context precision low ({context_precision:.2f} < 0.60), reducing evidence chunks",
                    confidence=0.7,
                ))

        # Rule 3: Answer Relevance zu niedrig
        if answer_relevance is not None and answer_relevance < 0.65:
            current = self._config.rag.self_rag_critiquity_threshold
            new_val = max(current - 0.05, 0.3)
            if new_val != current:
                actions.append(TuningAction(
                    parameter="rag.self_rag_critiquity_threshold",
                    old_value=current,
                    new_value=new_val,
                    reason=f"Answer relevance low ({answer_relevance:.2f} < 0.65), lowering critiquity threshold",
                    confidence=0.65,
                ))

        # Rule 4: Regression detected -> konservative Korrektur
        if regression.get("regression_detected"):
            # Leichte Erh鰄ung des similarity_threshold als Sicherheitsnetz
            current = self._config.rag.similarity_threshold
            new_val = min(current + 0.025, 0.95)
            if new_val != current:
                # Vermeide Duplikate wenn Rule 1 schon einen Eintrag erstellt hat
                existing_params = [a.parameter for a in actions]
                if "rag.similarity_threshold" not in existing_params:
                    actions.append(TuningAction(
                        parameter="rag.similarity_threshold",
                        old_value=current,
                        new_value=new_val,
                        reason="Regression detected, applying conservative safety adjustment",
                        confidence=0.5,
                    ))

        # Rule 5: Feedback-Insights ber點ksichtigen
        if feedback_insights:
            avg_search_depth = feedback_insights.get("avg_search_depth")
            satisfaction = feedback_insights.get("satisfaction_rate")

            if satisfaction is not None and satisfaction < 0.6 and avg_search_depth is not None:
                # Niedrige Zufriedenheit bei hoher Search-Depth -> zu viel Rauschen
                current = self._config.rag.similarity_threshold
                new_val = min(current + 0.05, 0.95)
                existing_params = [a.parameter for a in actions]
                if "rag.similarity_threshold" not in existing_params:
                    actions.append(TuningAction(
                        parameter="rag.similarity_threshold",
                        old_value=current,
                        new_value=new_val,
                        reason=f"Low satisfaction ({satisfaction:.2f}) with high search depth, tightening filter",
                        confidence=0.55,
                    ))

        # Safeguard: max 2 actions pro Diagnose
        if len(actions) > 2:
            # Priorisiere h鰄ere Confidence
            actions.sort(key=lambda a: a.confidence, reverse=True)
            actions = actions[:2]
            logger.info("AutoTuner: capped to %d actions (safeguard)", len(actions))

        return actions

    def _apply_action(self, action: TuningAction) -> bool:
        """Wendet eine Tuning-Aktion auf den ConfigManager an."""
        try:
            parts = action.parameter.split(".")
            if len(parts) != 2:
                logger.error("AutoTuner: invalid parameter path: %s", action.parameter)
                return False

            section_name, attr_name = parts

            # Safeguard: nur erlaubte Parameter
            if action.parameter not in ALLOWED_TUNING_PARAMS:
                logger.warning(
                    "AutoTuner: parameter %s not in allowed list, skipping",
                    action.parameter,
                )
                return False

            # Safeguard: Wertebereich pr黤en
            bounds = ALLOWED_TUNING_PARAMS[action.parameter]
            if not bounds["min"] <= action.new_value <= bounds["max"]:
                logger.error(
                    "AutoTuner: value %s out of bounds [%s, %s] for %s",
                    action.new_value, bounds["min"], bounds["max"], action.parameter,
                )
                return False

            # Section aufl鰏en
            section_map = {
                "llm": self._config.llm,
                "rag": self._config.rag,
                "gpu": self._config.gpu,
                "finance": self._config.finance,
                "session": self._config.session,
                "logging": self._config.logging_cfg,
            }
            target = section_map.get(section_name)
            if not target or not hasattr(target, attr_name):
                logger.error("AutoTuner: cannot resolve %s", action.parameter)
                return False

            setattr(target, attr_name, action.new_value)
            return True

        except Exception as exc:
            logger.error("AutoTuner: failed to apply action: %s", exc, exc_info=True)
            return False

    def _take_config_snapshot(self) -> None:
        """Speichert einen Snapshot der aktuellen Konfiguration."""
        snapshot = {
            "rag": {
                "similarity_threshold": self._config.rag.similarity_threshold,
                "max_evidence_chunks": self._config.rag.max_evidence_chunks,
                "self_rag_critiquity_threshold": self._config.rag.self_rag_critiquity_threshold,
            },
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        self._config_snapshots.append(snapshot)

        # Begrenze Snapshot-Anzahl
        if len(self._config_snapshots) > self._max_snapshots:
            self._config_snapshots.pop(0)

    def _restore_snapshot(self, snapshot: Dict[str, Any]) -> None:
        """Stellt eine Konfiguration aus einem Snapshot wieder her."""
        if "rag" in snapshot:
            for key, value in snapshot["rag"].items():
                if hasattr(self._config.rag, key):
                    setattr(self._config.rag, key, value)

    def _generate_recommendations(
        self,
        overall_score: Optional[float],
        faithfulness: Optional[float],
        answer_relevance: Optional[float],
        context_precision: Optional[float],
        trend: str,
        regression_detected: bool,
        feedback_insights: Optional[Dict[str, Any]],
    ) -> List[str]:
        """Generiert menschenlesbare Empfehlungen."""
        recs: List[str] = []

        if overall_score is None:
            return ["Keine Eval-Daten verf黦bar — warte auf erste Evaluationen"]

        if overall_score < self.quality_threshold:
            recs.append(
                f"Gesamt-Score ({overall_score:.2f}) unter Threshold ({self.quality_threshold:.2f}). "
                "Auto-Tuning wird aktiv sein."
            )

        if faithfulness is not None and faithfulness < 0.7:
            recs.append(
                f"Faithfulness ({faithfulness:.2f}) niedrig. "
                "Pr黤e RAG-Chunks auf Qualit鋞 und Dokumenten-Index."
            )

        if answer_relevance is not None and answer_relevance < 0.65:
            recs.append(
                f"Answer Relevance ({answer_relevance:.2f}) niedrig. "
                "Eventuell Self-RAG aktivieren oder Prompts anpassen."
            )

        if context_precision is not None and context_precision < 0.6:
            recs.append(
                f"Context Precision ({context_precision:.2f}) niedrig. "
                "Reduziere max_evidence_chunks oder verbessere Chunking-Strategie."
            )

        if trend == "degrading":
            recs.append(
                "Trend: verschlechternd. 黚erpr黤e neu hinzugef黦te Dokumente auf Qualit鋞."
            )

        if regression_detected:
            recs.append(
                "Regression erkannt! Konservative Anpassungen wurden angewendet. "
                "Manuelle 黚erpr黤ung empfohlen."
            )

        if feedback_insights:
            satisfaction = feedback_insights.get("satisfaction_rate")
            if satisfaction is not None and satisfaction < 0.6:
                recs.append(
                    f"User-Zufriedenheit ({satisfaction:.0%}) niedrig. "
                    "Pr黤e Query-Handling und Antwortqualit鋞."
                )

        if not recs:
            recs.append("Alle Metriken im gr黱en Bereich — keine Aktion erforderlich.")

        return recs


# ---------------------------------------------------------------------------
# Module-level convenience
# ---------------------------------------------------------------------------

def create_auto_tuner(
    strixkat: Any,
    config: Any,
    feedback: Any = None,
    **kwargs: Any,
) -> AutoTuner:
    """Factory-Funktion f黵 AutoTuner mit Standard-Parametern."""
    return AutoTuner(
        strixkat_engine=strixkat,
        config_manager=config,
        feedback_logger=feedback,
        **kwargs,
    )