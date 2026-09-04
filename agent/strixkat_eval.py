"""
P2-3: StrixKAT EVAL – SOTA RAG Quality Evaluation Framework

Comprehensive evaluation system for measuring and securing RAG quality.
Provides offline batch evaluation, continuous monitoring, and auto-rollback.

SOTA Features:
- Multi-dimensional quality scoring (faithfulness, answer relevance, context precision)
- Statistical significance testing for regressions
- Auto-rollback triggered on quality degradation
- Evaluation datasets from production traces
- Model-agnostic eval adapters
- Dashboard-ready metrics export

Author: SOTA RAG Quality Pipeline
Date: 2026-06-24
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import random
import re
import sqlite3
import threading
import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Kanonische DB-Pfade aus dem zentralen Resolver (Root-Cause-Fix 2026-08-10).
# Die früheren Literal-Defaults ("agent/rag_store.db", "agent/eval_results.db")
# wurden CWD-relativ aufgelöst und umgingen .db_root / BOT6_DB_ROOT.
from utils.db_path_resolver import get_agent_rag_path as _get_agent_rag_path
from utils.db_path_resolver import get_db_path as _get_db_path

_FEEDBACK_DB_DEFAULT: str = str(_get_agent_rag_path())
_EVAL_RESULTS_DB_DEFAULT: str = str(_get_db_path("agent", "eval_results.db"))

# ====================================================================
# EVALUATION METRICS
# ====================================================================

class MetricCategory(Enum):
    FAITHFULNESS = "faithfulness"
    ANSWER_RELEVANCE = "answer_relevance"
    CONTEXT_PRECISION = "context_precision"
    CONTEXT_RECALL = "context_recall"
    ANSWER_CORRECTNESS = "answer_correctness"
    LATENCY = "latency"
    COST = "cost"


class ContentType(Enum):
    TEXT = "text"
    TABLE = "table"
    FIGURE = "figure"
    FORMULA = "formula"
    MIXED = "mixed"


@dataclass
class EvalMetric:
    """Single metric measurement."""
    name: str
    category: MetricCategory
    value: float
    min_value: float = 0.0
    max_value: float = 1.0
    weight: float = 1.0
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def normalized(self) -> float:
        """Normalized value between 0 and 1."""
        if self.max_value == self.min_value:
            return 0.0
        return max(0.0, min(1.0, (self.value - self.min_value) / (self.max_value - self.min_value)))


@dataclass
class EvalSample:
    """Single evaluation sample."""
    sample_id: str
    query: str
    ground_truth: str
    generated_answer: str
    context_chunks: List[str] = field(default_factory=list)
    source_file: str = ""
    content_type: ContentType = ContentType.TEXT
    metrics: List[EvalMetric] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    @property
    def overall_score(self) -> float:
        """Weighted average of all metrics."""
        if not self.metrics:
            return 0.0
        total_weight = sum(m.weight for m in self.metrics)
        if total_weight == 0:
            return 0.0
        return sum(m.normalized * m.weight for m in self.metrics) / total_weight

    @property
    def is_passing(self) -> bool:
        """Whether this sample passes quality thresholds."""
        return self.overall_score >= 0.7


@dataclass
class EvalResult:
    """Complete evaluation result."""
    eval_id: str
    eval_name: str
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    samples: List[EvalSample] = field(default_factory=list)
    config: Dict[str, Any] = field(default_factory=dict)
    summary: Dict[str, Any] = field(default_factory=dict)

    @property
    def overall_score(self) -> float:
        """Overall evaluation score."""
        if not self.samples:
            return 0.0
        return sum(s.overall_score for s in self.samples) / len(self.samples)

    @property
    def pass_rate(self) -> float:
        """Percentage of passing samples."""
        if not self.samples:
            return 0.0
        return sum(1 for s in self.samples if s.is_passing) / len(self.samples)

    @property
    def failing_samples(self) -> List[EvalSample]:
        """Samples that failed quality thresholds."""
        return [s for s in self.samples if not s.is_passing]

    def metric_breakdown(self) -> Dict[str, Dict[str, float]]:
        """Breakdown by metric category."""
        breakdown: Dict[str, List[float]] = {}
        for sample in self.samples:
            for metric in sample.metrics:
                breakdown.setdefault(metric.category.value, []).append(metric.normalized)
        return {
            category: {
                "mean": sum(values) / len(values),
                "min": min(values),
                "max": max(values),
                "count": len(values),
            }
            for category, values in breakdown.items()
        }

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dictionary."""
        return {
            "eval_id": self.eval_id,
            "eval_name": self.eval_name,
            "timestamp": self.timestamp,
            "overall_score": self.overall_score,
            "pass_rate": self.pass_rate,
            "sample_count": len(self.samples),
            "metric_breakdown": self.metric_breakdown(),
            "failing_count": len(self.failing_samples),
            "config": self.config,
        }


# ====================================================================
# EVALUATION ADAPTERS
# ====================================================================

class EvalAdapter(ABC):
    """Abstract adapter for computing a specific metric."""

    @abstractmethod
    def compute(self, sample: EvalSample) -> Optional[float]:
        """Compute metric value for a sample."""
        pass

    @abstractmethod
    def category(self) -> MetricCategory:
        """Metric category."""
        pass

    @abstractmethod
    def name(self) -> str:
        """Metric name."""
        pass


class FaithfulnessAdapter(EvalAdapter):
    """
    Measures whether the generated answer is faithful to the context.

    SOTA Approach: Checks if claims in the answer can be supported by the context.
    Uses sentence-level verification with semantic similarity heuristics.
    """

    def compute(self, sample: EvalSample) -> Optional[float]:
        if not sample.context_chunks or not sample.generated_answer:
            return None

        answer_sentences = self._split_sentences(sample.generated_answer)
        if not answer_sentences:
            return 0.0

        supported_count = 0
        for sentence in answer_sentences:
            if self._is_supported(sentence, sample.context_chunks):
                supported_count += 1

        return supported_count / len(answer_sentences)

    def category(self) -> MetricCategory:
        return MetricCategory.FAITHFULNESS

    def name(self) -> str:
        return "faithfulness"

    def _split_sentences(self, text: str) -> List[str]:
        sentences = re.split(r'(?<=[.!?])\s+', text)
        return [s.strip() for s in sentences if s.strip() and len(s.strip()) > 10]

    def _is_supported(self, sentence: str, contexts: List[str]) -> bool:
        """Check if a sentence is supported by any context chunk."""
        sentence_words = set(self._tokenize(sentence.lower()))
        if not sentence_words:
            return False

        for context in contexts:
            context_words = set(self._tokenize(context.lower()))
            if not context_words:
                continue
            overlap = len(sentence_words & context_words) / len(sentence_words)
            if overlap > 0.4:
                return True

        # Check for partial keyword matches
        key_terms = [w for w in sentence_words if len(w) > 3]
        if key_terms:
            for context in contexts:
                context_lower = context.lower()
                matches = sum(1 for term in key_terms if term in context_lower)
                if matches >= max(1, len(key_terms) // 3):
                    return True

        return False

    @staticmethod
    def _tokenize(text: str) -> List[str]:
        return re.findall(r'\b[a-zäöüßA-ZÄÖÜa-z]{2,}\b', text.lower())


class AnswerRelevanceAdapter(EvalAdapter):
    """
    Measures how relevant the answer is to the original query.

    Uses keyword overlap and semantic field analysis.
    """

    def compute(self, sample: EvalSample) -> Optional[float]:
        if not sample.query or not sample.generated_answer:
            return None

        query_keywords = set(self._extract_keywords(sample.query))
        answer_text = sample.generated_answer.lower()

        if not query_keywords:
            return 0.5  # Neutral when query is unclear

        matches = 0
        for keyword in query_keywords:
            if keyword in answer_text:
                matches += 1
            else:
                # Check for stemmed/related forms
                for answer_word in re.findall(r'\b[a-zäöüßA-ZÄÖÜa-z]{2,}\b', answer_text):
                    if self._lexical_overlap(keyword, answer_word) > 0.6:
                        matches += 1
                        break

        return min(1.0, matches / max(1, len(query_keywords)))

    def category(self) -> MetricCategory:
        return MetricCategory.ANSWER_RELEVANCE

    def name(self) -> str:
        return "answer_relevance"

    @staticmethod
    def _extract_keywords(text: str) -> List[str]:
        stop_words = {
            "der", "die", "das", "ein", "eine", "und", "oder", "ist", "sind",
            "in", "an", "auf", "mit", "für", "von", "zu", "den", "dem",
            "the", "a", "an", "is", "are", "and", "or", "in", "on", "at",
            "to", "for", "with", "from", "by", "this", "that", "these",
            "was", "were", "has", "have", "had", "been", "being",
            "es", "er", "sie", "man", "nicht", "auch", "noch", "sehr",
        }
        words = re.findall(r'\b[a-zäöüßA-ZÄÖÜa-z]{3,}\b', text.lower())
        return [w for w in words if w not in stop_words]

    @staticmethod
    def _lexical_overlap(word1: str, word2: str) -> float:
        """Simple lexical overlap score."""
        if not word1 or not word2:
            return 0.0
        common = 0
        for c in word1:
            if c in word2:
                common += 1
        return common / max(1, len(word1))


class ContextPrecisionAdapter(EvalAdapter):
    """
    Measures how precise the retrieved context is for answering the query.

    Higher score when context is focused and relevant.
    """

    def compute(self, sample: EvalSample) -> Optional[float]:
        if not sample.context_chunks or not sample.query:
            return None

        query_keywords = set(self._extract_keywords(sample.query))
        if not query_keywords:
            return 0.5

        relevant_chunks = 0
        for chunk in sample.context_chunks:
            chunk_text = chunk.lower()
            matches = sum(1 for kw in query_keywords if kw in chunk_text)
            if matches >= max(1, len(query_keywords) // 3):
                relevant_chunks += 1

        return relevant_chunks / max(1, len(sample.context_chunks))

    def category(self) -> MetricCategory:
        return MetricCategory.CONTEXT_PRECISION

    def name(self) -> str:
        return "context_precision"

    @staticmethod
    def _extract_keywords(text: str) -> List[str]:
        stop_words = {
            "der", "die", "das", "ein", "eine", "und", "oder", "ist", "sind",
            "the", "a", "an", "is", "are", "and", "or", "in", "on", "at",
            "es", "er", "sie", "man", "nicht", "auch", "noch", "sehr",
        }
        words = re.findall(r'\b[a-zäöüßA-ZÄÖÜa-z]{3,}\b', text.lower())
        return [w for w in words if w not in stop_words]


class GroundTruthOverlapAdapter(EvalAdapter):
    """
    Measures overlap between generated answer and ground truth.

    Uses normalized edit distance heuristics.
    """

    def compute(self, sample: EvalSample) -> Optional[float]:
        if not sample.ground_truth or not sample.generated_answer:
            return None

        gen_words = set(sample.generated_answer.lower().split())
        gt_words = set(sample.ground_truth.lower().split())

        if not gt_words:
            return 0.0

        overlap = len(gen_words & gt_words)
        precision = overlap / max(1, len(gen_words))
        recall = overlap / max(1, len(gt_words))

        # F1-like score
        if precision + recall == 0:
            return 0.0
        return 2 * (precision * recall) / (precision + recall)

    def category(self) -> MetricCategory:
        return MetricCategory.ANSWER_CORRECTNESS

    def name(self) -> str:
        return "ground_truth_overlap"


# ====================================================================
# STATISTICAL TESTING
# ====================================================================

class StatisticalTester:
    """Statistical significance testing for eval results."""

    @staticmethod
    def t_test_independent(scores_a: List[float], scores_b: List[float],
                          alpha: float = 0.05) -> Dict[str, Any]:
        """
        Independent samples t-test to check if two sets of scores differ significantly.

        Returns whether the difference is statistically significant.
        """
        if len(scores_a) < 2 or len(scores_b) < 2:
            return {
                "significant": False,
                "p_value": 1.0,
                "t_statistic": 0.0,
                "message": "Insufficient samples for t-test",
            }

        mean_a = sum(scores_a) / len(scores_a)
        mean_b = sum(scores_b) / len(scores_b)

        var_a = sum((x - mean_a) ** 2 for x in scores_a) / max(1, len(scores_a) - 1)
        var_b = sum((x - mean_b) ** 2 for x in scores_b) / max(1, len(scores_b) - 1)

        se = math.sqrt(var_a / len(scores_a) + var_b / len(scores_b))

        if se == 0:
            return {
                "significant": False,
                "p_value": 1.0,
                "t_statistic": 0.0,
                "message": "Zero standard error",
            }

        t_stat = (mean_a - mean_b) / se
        df = len(scores_a) + len(scores_b) - 2

        # Approximate p-value using normal distribution for large df
        p_value = StatisticalTester._normal_cdf_approx(abs(t_stat))

        return {
            "significant": p_value < alpha,
            "p_value": p_value,
            "t_statistic": t_stat,
            "mean_before": mean_a,
            "mean_after": mean_b,
            "difference": mean_b - mean_a,
            "message": "Significant difference detected" if p_value < alpha else "No significant difference",
        }

    @staticmethod
    def _normal_cdf_approx(x: float) -> float:
        """Approximate two-tailed p-value from z-score."""
        # Abramowitz and Stegun approximation
        if x < 0:
            x = abs(x)
        if x > 8:
            return 0.0

        t = 1.0 / (1.0 + 0.2316419 * x)
        d = 0.3989422804014327  # 1/sqrt(2*pi)

        p = d * math.exp(-x * x / 2.0) * (
            t * (0.319381530 +
                 t * (-0.356563782 +
                      t * (1.781477937 +
                           t * (-1.821255978 +
                                t * 1.330274429))))
        )
        # Two-tailed
        return max(0.0, min(1.0, 2.0 * p))

    @staticmethod
    def confidence_interval(scores: List[float], confidence: float = 0.95) -> Tuple[float, float]:
        """Calculate confidence interval for a set of scores."""
        if not scores:
            return (0.0, 0.0)

        mean = sum(scores) / len(scores)
        std = math.sqrt(sum((x - mean) ** 2 for x in scores) / max(1, len(scores) - 1))
        margin = 1.96 * std / math.sqrt(len(scores))  # 95% CI approximation

        return (max(0.0, mean - margin), min(1.0, mean + margin))


# ====================================================================
# REGRESSION DETECTOR
# ====================================================================

@dataclass
class RegressionAlert:
    """Alert when quality regression is detected."""
    alert_id: str
    timestamp: str
    severity: str  # "warning", "critical"
    message: str
    previous_score: float
    current_score: float
    threshold: float
    metric_breakdown: Dict[str, Any] = field(default_factory=dict)


class RegressionDetector:
    """
    Detects quality regressions by comparing eval results.

    Triggers alerts when quality drops below thresholds.
    """

    def __init__(self, score_threshold: float = 0.10, critical_threshold: float = 0.20,
                 min_samples: int = 5):
        self.score_threshold = score_threshold
        self.critical_threshold = critical_threshold
        self.min_samples = min_samples
        self._history: List[float] = []

    def check(self, previous_scores: List[float], current_scores: List[float]) -> Optional[RegressionAlert]:
        """
        Check if there's a quality regression.

        Returns an alert if regression is detected.
        """
        if len(previous_scores) < self.min_samples or len(current_scores) < self.min_samples:
            logger.warning("Insufficient samples for regression check")
            return None

        prev_mean = sum(previous_scores) / len(previous_scores)
        curr_mean = sum(current_scores) / len(current_scores)

        if curr_mean >= prev_mean:
            return None  # No regression

        drop = prev_mean - curr_mean

        # Statistical significance check
        test_result = StatisticalTester.t_test_independent(previous_scores, current_scores)

        if not test_result["significant"]:
            logger.info("Quality drop not statistically significant: %.3f", drop)
            return None

        severity = "critical" if drop >= self.critical_threshold else "warning"

        alert = RegressionAlert(
            alert_id=f"reg_{int(time.time())}_{hashlib.md5(str(current_scores).encode()).hexdigest()[:8]}",
            timestamp=datetime.now(timezone.utc).isoformat(),
            severity=severity,
            message=f"Quality regression detected: {prev_mean:.3f} -> {curr_mean:.3f} (drop: {drop:.3f})",
            previous_score=prev_mean,
            current_score=curr_mean,
            threshold=self.score_threshold,
            metric_breakdown=test_result,
        )

        logger.warning("REGRESSION ALERT [%s]: %s", severity.upper(), alert.message)
        return alert

    def record_score(self, score: float):
        """Record a score in history."""
        self._history.append(score)

    @property
    def history(self) -> List[float]:
        return list(self._history)


# ====================================================================
# STRIXKAT EVAL ENGINE
# ====================================================================

# Module-level singleton for StrixKATEvalEngine
_strixkat_engine_instance: Optional[StrixKATEvalEngine] = None


class StrixKATEvalEngine:
    """
    SOTA RAG Evaluation Engine.

    Provides comprehensive evaluation of RAG pipelines with:
    - Multi-dimensional quality metrics
    - Statistical significance testing
    - Regression detection
    - Auto-rollback recommendations
    """

    def __init__(self, adapters: Optional[List[EvalAdapter]] = None,
                 score_threshold: float = 0.10,
                 critical_threshold: float = 0.20):
        # Default adapters
        self.adapters = adapters or [
            FaithfulnessAdapter(),
            AnswerRelevanceAdapter(),
            ContextPrecisionAdapter(),
            GroundTruthOverlapAdapter(),
        ]

        # Regression detection
        self.regression_detector = RegressionDetector(
            score_threshold=score_threshold,
            critical_threshold=critical_threshold,
        )

        # History
        self._eval_history: List[EvalResult] = []
        self._baseline_scores: List[float] = []

    # ----------------------------------------------------------------
    # Evaluation
    # ----------------------------------------------------------------

    def evaluate_sample(self, sample: EvalSample) -> EvalSample:
        """Evaluate a single sample with all adapters."""
        for adapter in self.adapters:
            try:
                value = adapter.compute(sample)
                if value is not None:
                    sample.metrics.append(EvalMetric(
                        name=adapter.name(),
                        category=adapter.category(),
                        value=value,
                    ))
            except Exception as e:
                logger.error("Adapter %s failed: %s", adapter.name(), e)

        return sample

    def evaluate_batch(self, samples: List[EvalSample],
                      eval_name: str = "batch_eval",
                      config: Optional[Dict[str, Any]] = None) -> EvalResult:
        """
        Evaluate a batch of samples.

        Returns complete EvalResult with summary statistics.
        """
        logger.info("Starting batch evaluation: %s (%d samples)", eval_name, len(samples))

        evaluated = [self.evaluate_sample(s) for s in samples]

        eval_result = EvalResult(
            eval_id=f"eval_{int(time.time())}_{hashlib.md5(eval_name.encode()).hexdigest()[:8]}",
            eval_name=eval_name,
            samples=evaluated,
            config=config or {},
        )

        # Compute summary
        scores = [s.overall_score for s in evaluated]
        ci = StatisticalTester.confidence_interval(scores)
        eval_result.summary = {
            "overall_score": eval_result.overall_score,
            "pass_rate": eval_result.pass_rate,
            "sample_count": len(evaluated),
            "failing_count": len(eval_result.failing_samples),
            "confidence_interval_95": {"lower": ci[0], "upper": ci[1]},
            "metric_breakdown": eval_result.metric_breakdown(),
        }

        # Store in history
        self._eval_history.append(eval_result)

        # Record baseline if first eval
        if not self._baseline_scores:
            self._baseline_scores = scores
            logger.info("Baseline established: %.3f (+/-.3f)", ci[0], (ci[1] - ci[0]) / 2)

        logger.info("Evaluation complete: score=%.3f, pass_rate=%.1f%%",
                    eval_result.overall_score, eval_result.pass_rate * 100)

        return eval_result

    # ----------------------------------------------------------------
    # Regression Detection
    # ----------------------------------------------------------------

    def check_regression(self, current_result: EvalResult) -> Optional[RegressionAlert]:
        """
        Check if current eval result represents a regression.

        Compares against baseline or previous eval.
        """
        if not self._baseline_scores:
            return None  # No baseline yet

        current_scores = [s.overall_score for s in current_result.samples]
        return self.regression_detector.check(self._baseline_scores, current_scores)

    def should_rollback(self, eval_result: EvalResult) -> bool:
        """
        Determine if auto-rollback should be triggered.

        Returns True if quality has dropped significantly.
        """
        alert = self.check_regression(eval_result)
        return alert is not None and alert.severity == "critical"

    # ----------------------------------------------------------------
    # Baseline Management
    # ----------------------------------------------------------------

    def set_baseline(self, scores: List[float]):
        """Manually set baseline scores."""
        self._baseline_scores = list(scores)
        logger.info("Baseline manually set with %d scores, mean=%.3f",
                    len(scores), sum(scores) / len(scores))

    def update_baseline(self, window: int = 5):
        """Update baseline using recent eval results."""
        if len(self._eval_history) < window:
            return

        recent = self._eval_history[-window:]
        all_scores = []
        for result in recent:
            all_scores.extend([s.overall_score for s in result.samples])

        self._baseline_scores = all_scores
        logger.info("Baseline updated with %d scores from %d evals",
                    len(all_scores), window)

    # ----------------------------------------------------------------
    # Reporting
    # ----------------------------------------------------------------

    def generate_report(self) -> Dict[str, Any]:
        """Generate a comprehensive evaluation report."""
        if not self._eval_history:
            return {"status": "no_evaluations"}

        reports = []
        for result in self._eval_history:
            reports.append(result.to_dict())

        return {
            "status": "ok",
            "total_evaluations": len(self._eval_history),
            "baseline_size": len(self._baseline_scores),
            "baseline_mean": sum(self._baseline_scores) / len(self._baseline_scores) if self._baseline_scores else 0.0,
            "latest_eval": reports[-1] if reports else None,
            "evaluations": reports,
        }

    # ----------------------------------------------------------------
    # Singleton Access
    # ----------------------------------------------------------------

    @classmethod
    def get_instance(cls, **kwargs) -> "StrixKATEvalEngine":
        """Singleton-Access zur EvalEngine."""
        global _strixkat_engine_instance
        if _strixkat_engine_instance is None:
            _strixkat_engine_instance = cls(**kwargs)
        return _strixkat_engine_instance

    # ----------------------------------------------------------------
    # Dashboard Metrics
    # ----------------------------------------------------------------

    def get_dashboard_metrics(self) -> Dict[str, Any]:
        """Get metrics suitable for dashboard display."""
        if not self._eval_history:
            return {"status": "no_data"}

        latest = self._eval_history[-1]
        scores_over_time = [r.overall_score for r in self._eval_history]

        return {
            "status": "ok",
            "latest_score": latest.overall_score,
            "latest_pass_rate": latest.pass_rate,
            "trend": self._calculate_trend(scores_over_time),
            "scores_over_time": scores_over_time,
            "baseline_mean": sum(self._baseline_scores) / len(self._baseline_scores) if self._baseline_scores else None,
            "eval_count": len(self._eval_history),
        }

    @staticmethod
    def _calculate_trend(scores: List[float]) -> str:
        """Calculate trend direction from score history."""
        if len(scores) < 3:
            return "insufficient_data"

        recent = scores[-3:]
        older = scores[-6:-3] if len(scores) >= 6 else scores[:-3]

        if not older:
            return "stable"

        recent_mean = sum(recent) / len(recent)
        older_mean = sum(older) / len(older)

        diff = recent_mean - older_mean
        if diff > 0.05:
            return "improving"
        elif diff < -0.05:
            return "degrading"
        return "stable"

    def export_csv(self, filepath: str = "strixkat_eval_report.csv"):
        """Export evaluation results to CSV."""
        import csv

        with open(filepath, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([
                "eval_id", "eval_name", "timestamp", "overall_score",
                "pass_rate", "sample_count", "failing_count",
                "faithfulness", "answer_relevance", "context_precision",
                "answer_correctness"
            ])

            for result in self._eval_history:
                breakdown = result.metric_breakdown()
                writer.writerow([
                    result.eval_id,
                    result.eval_name,
                    result.timestamp,
                    f"{result.overall_score:.4f}",
                    f"{result.pass_rate:.4f}",
                    len(result.samples),
                    len(result.failing_samples),
                    f"{breakdown.get('faithfulness', {}).get('mean', 0):.4f}",
                    f"{breakdown.get('answer_relevance', {}).get('mean', 0):.4f}",
                    f"{breakdown.get('context_precision', {}).get('mean', 0):.4f}",
                    f"{breakdown.get('answer_correctness', {}).get('mean', 0):.4f}",
                ])

        logger.info("Report exported to %s", filepath)


def reset_strixkat_engine():
    """Reset des StrixKAT Singletons (fuer Tests)."""
    global _strixkat_engine_instance
    _strixkat_engine_instance = None


class StrixKATEval(StrixKATEvalEngine):
    """Compatibility wrapper used by orchestrator imports."""

    def __init__(self, quality_threshold: float = 0.75, auto_rollback: bool = True,
                 score_threshold: float = 0.10, critical_threshold: float = 0.20):
        super().__init__(score_threshold=score_threshold, critical_threshold=critical_threshold)
        self.quality_threshold = quality_threshold
        self.auto_rollback = auto_rollback
        self._last_good_snapshot_path: Optional[str] = None

    def evaluate(self, query: str, answer: str, sources: List[Any]) -> Dict[str, Any]:
        """
        Evaluate a production answer against the retrieved source set.

        This is the orchestrator-facing per-answer entry point. It complements
        grammar/schema validity with lightweight semantic quality metrics.
        """
        context_chunks: List[str] = []
        source_paths: List[str] = []

        for source in sources:
            chunk_text = (
                getattr(source, "content", None)
                or getattr(source, "snippet", None)
                or getattr(source, "text", None)
                or getattr(source, "title", None)
                or ""
            )
            chunk_text = str(chunk_text).strip()
            if chunk_text:
                context_chunks.append(chunk_text[:1500])

            source_path = (
                getattr(source, "file_path", None)
                or getattr(source, "source_file", None)
                or getattr(source, "url", None)
                or ""
            )
            if source_path:
                source_paths.append(str(source_path))

        sample = EvalSample(
            sample_id=f"live_{int(time.time())}_{hashlib.md5((query or '').encode('utf-8')).hexdigest()[:8]}",
            query=query,
            ground_truth="",
            generated_answer=answer,
            context_chunks=context_chunks[:12],
            source_file=" | ".join(source_paths[:3]),
            content_type=ContentType.TEXT,
        )
        evaluated = self.evaluate_sample(sample)

        metric_values = {
            metric.name: metric.value
            for metric in evaluated.metrics
        }
        overall_score = evaluated.overall_score
        return {
            "status": "ok",
            "overall_score": overall_score,
            "is_passing": evaluated.is_passing,
            "quality_threshold": self.quality_threshold,
            "metrics": {
                "sota_quality": overall_score,
                "strixkat_faithfulness": metric_values.get("faithfulness"),
                "strixkat_answer_relevance": metric_values.get("answer_relevance"),
                "strixkat_context_precision": metric_values.get("context_precision"),
                "strixkat_ground_truth_overlap": metric_values.get("ground_truth_overlap"),
                "strixkat_source_count": len(context_chunks),
            },
            "sample": evaluated,
        }

    def _resolve_db_path(self, unified_rag_store: Any) -> Optional[str]:
        """Resolve the physical SQLite path from the RAG store instance."""
        db_path = getattr(unified_rag_store, "db_path", None)
        if not db_path:
            return None
        db_path = str(db_path)
        if db_path == ":memory:" or db_path.startswith("file::memory"):
            return None
        return os.path.abspath(db_path)

    def _snapshot_dir(self, db_path: str) -> str:
        base_dir = os.path.dirname(db_path) or "."
        out_dir = os.path.join(base_dir, "backups", "strixkat")
        os.makedirs(out_dir, exist_ok=True)
        return out_dir

    def _create_live_snapshot(self, db_path: str) -> Optional[str]:
        """Create a consistent SQLite snapshot using the online backup API."""
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        digest = hashlib.md5(db_path.encode("utf-8")).hexdigest()[:8]
        snapshot_path = os.path.join(self._snapshot_dir(db_path), f"rag_store_{ts}_{digest}.sqlite")

        src = None
        dst = None
        try:
            src = sqlite3.connect(db_path, check_same_thread=False)
            dst = sqlite3.connect(snapshot_path, check_same_thread=False)
            src.backup(dst)
            dst.commit()
            return snapshot_path
        except Exception as exc:
            logger.error("Failed to create StrixKAT snapshot for %s: %s", db_path, exc)
            try:
                if os.path.exists(snapshot_path):
                    os.remove(snapshot_path)
            except OSError:
                pass
            return None
        finally:
            if dst is not None:
                dst.close()
            if src is not None:
                src.close()

    def _restore_live_snapshot(self, db_path: str, snapshot_path: str) -> Dict[str, Any]:
        """Restore a SQLite snapshot into the live DB using backup API."""
        if not os.path.exists(snapshot_path):
            return {"status": "failed", "reason": f"snapshot_missing:{snapshot_path}"}

        src = None
        dst = None
        try:
            src = sqlite3.connect(snapshot_path, check_same_thread=False)
            dst = sqlite3.connect(db_path, check_same_thread=False)
            src.backup(dst)
            dst.commit()
            return {
                "status": "restored",
                "db_path": db_path,
                "snapshot_path": snapshot_path,
            }
        except Exception as exc:
            logger.error("Failed to restore StrixKAT snapshot %s -> %s: %s", snapshot_path, db_path, exc)
            return {
                "status": "failed",
                "db_path": db_path,
                "snapshot_path": snapshot_path,
                "reason": str(exc),
            }
        finally:
            if dst is not None:
                dst.close()
            if src is not None:
                src.close()

    def evaluate_full_pipeline(self, unified_rag_store) -> Dict[str, Any]:
        """Compatibility wrapper used by the SOTA pipeline."""
        report = self.generate_report()
        overall_quality = report.get("baseline_mean", 0.0)
        latest_eval = report.get("latest_eval")
        if isinstance(latest_eval, dict):
            overall_quality = latest_eval.get("overall_score", overall_quality)
        if not report.get("evaluations"):
            overall_quality = 1.0

        db_path = self._resolve_db_path(unified_rag_store)
        snapshot_created = None
        if db_path and overall_quality >= float(self.quality_threshold):
            snapshot_created = self._create_live_snapshot(db_path)
            if snapshot_created:
                self._last_good_snapshot_path = snapshot_created

        return {
            "status": "ok",
            "overall_quality": overall_quality,
            "report": report,
            "auto_rollback": self.auto_rollback,
            "quality_threshold": self.quality_threshold,
            "last_good_snapshot": self._last_good_snapshot_path,
            "snapshot_created": snapshot_created,
        }

    def rollback_to_last_good_state(self, unified_rag_store) -> Dict[str, Any]:
        """Restore the last known good SQLite snapshot for the live RAG store."""
        if not self.auto_rollback:
            return {"status": "skipped", "reason": "auto_rollback_disabled"}

        db_path = self._resolve_db_path(unified_rag_store)
        if not db_path:
            return {"status": "skipped", "reason": "db_path_unavailable_or_inmemory"}

        if not self._last_good_snapshot_path:
            return {"status": "skipped", "reason": "no_last_good_snapshot"}

        return self._restore_live_snapshot(db_path, self._last_good_snapshot_path)


class StrixKATEvaluator(StrixKATEval):
    """Compatibility alias expected by the pipeline integration."""


# ====================================================================
# EVAL DATASET BUILDER
# ====================================================================

class EvalDatasetBuilder:
    """
    Builds evaluation datasets from production traces.

    Enables continuous evaluation by capturing real queries and answers.
    """

    def __init__(self, storage_path: str = "data/eval_datasets"):
        self.storage_path = Path(storage_path)
        self.storage_path.mkdir(parents=True, exist_ok=True)
        self._samples: List[Dict[str, Any]] = []

    def add_sample(self, query: str, answer: str, ground_truth: str = "",
                  context_chunks: Optional[List[str]] = None,
                  source_file: str = "",
                  content_type: ContentType = ContentType.TEXT):
        """Add a sample to the dataset."""
        self._samples.append({
            "query": query,
            "generated_answer": answer,
            "ground_truth": ground_truth,
            "context_chunks": context_chunks or [],
            "source_file": source_file,
            "content_type": content_type.value,
            "added_at": datetime.now(timezone.utc).isoformat(),
        })

    def build_samples(self) -> List[EvalSample]:
        """Convert stored data to EvalSample objects."""
        samples = []
        for idx, data in enumerate(self._samples):
            samples.append(EvalSample(
                sample_id=f"sample_{idx}_{hashlib.md5(data['query'].encode()).hexdigest()[:8]}",
                query=data["query"],
                ground_truth=data["ground_truth"],
                generated_answer=data["generated_answer"],
                context_chunks=data["context_chunks"],
                source_file=data["source_file"],
                content_type=ContentType(data["content_type"]),
            ))
        return samples

    def save_dataset(self, name: str = "default") -> str:
        """Save dataset to disk."""
        filepath = self.storage_path / f"{name}_{int(time.time())}.json"
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(self._samples, f, indent=2, ensure_ascii=False)
        logger.info("Dataset saved to %s (%d samples)", filepath, len(self._samples))
        return str(filepath)

    def load_dataset(self, filepath: str) -> List[EvalSample]:
        """Load dataset from disk."""
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)

        samples = []
        for idx, item in enumerate(data):
            samples.append(EvalSample(
                sample_id=f"loaded_{idx}",
                query=item["query"],
                ground_truth=item.get("ground_truth", ""),
                generated_answer=item["generated_answer"],
                context_chunks=item.get("context_chunks", []),
                source_file=item.get("source_file", ""),
                content_type=ContentType(item.get("content_type", "text")),
            ))

        logger.info("Dataset loaded from %s (%d samples)", filepath, len(samples))
        return samples

    @property
    def sample_count(self) -> int:
        return len(self._samples)

    def from_feedback(
        self,
        feedback_db_path: str = _FEEDBACK_DB_DEFAULT,
        min_negative_value: int = -1,
        limit: int = 200,
    ) -> List[EvalSample]:
        """
        Read negative user feedback from the FeedbackLogger SQLite table and
        materialise them as EvalSample objects for StrixKAT evaluation.

        This closes the Eval-Loop: User-Feedback → EvalDataset → StrixKAT metrics.

        Args:
            feedback_db_path: Path to the SQLite DB used by FeedbackLogger.
            min_negative_value: Only include rows with feedback_value <= this.
            limit: Maximum number of feedback rows to convert.

        Returns:
            List of EvalSample ready for StrixKAT batch evaluation.
        """
        import sqlite3

        if not os.path.exists(feedback_db_path):
            logger.warning("Feedback DB not found at %s — returning empty sample list", feedback_db_path)
            return []

        try:
            conn = sqlite3.connect(feedback_db_path)
            cursor = conn.cursor()

            # Verify table exists
            cursor.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='feedback_responses'"
            )
            if not cursor.fetchone():
                logger.warning("feedback_responses table not found in %s", feedback_db_path)
                conn.close()
                return []

            cursor.execute(
                """
                SELECT query, response, feedback_value, comment, created_at
                FROM feedback_responses
                WHERE feedback_value <= ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (min_negative_value, limit),
            )

            rows = cursor.fetchall()
            conn.close()

            if not rows:
                logger.info("No negative feedback entries found in %s", feedback_db_path)
                return []

            samples: List[EvalSample] = []
            for idx, row in enumerate(rows):
                query_text = str(row[0]) if row[0] else ""
                response_text = str(row[1]) if row[1] else ""
                feedback_value = row[2] or 0
                comment = str(row[3]) if row[3] else ""
                created_at = str(row[4]) if row[4] else ""

                sample = EvalSample(
                    sample_id=f"feedback_{idx}_{hashlib.md5((query_text or f'fb_{idx}').encode()).hexdigest()[:8]}",
                    query=query_text,
                    ground_truth=comment,  # User comment as partial ground truth
                    generated_answer=response_text,
                    context_chunks=[],
                    source_file="feedback_responses",
                    content_type=ContentType.TEXT,
                )
                sample.metadata = {
                    "feedback_value": feedback_value,
                    "created_at": created_at,
                    "source": "user_feedback",
                }
                samples.append(sample)

            logger.info(
                "Loaded %d EvalSample(s) from negative user feedback in %s",
                len(samples),
                feedback_db_path,
            )
            return samples

        except Exception as exc:
            logger.error("Failed to load feedback samples from %s: %s", feedback_db_path, exc)
            return []


# ====================================================================
# MASSNAHME 3: EVAL-RESULT-PERSISTENZ (SQLite)
# ====================================================================

class EvalResultPersistence:
    """
    Persistiert StrixKAT-Eval-Ergebnisse in SQLite statt nur in-memory.

    Tabelle eval_results:
      eval_id, eval_name, timestamp, overall_score, pass_rate,
      sample_count, failing_count, metric_breakdown_json, config_json
    """

    _CREATE_TABLE = """
        CREATE TABLE IF NOT EXISTS eval_results (
            eval_id TEXT PRIMARY KEY,
            eval_name TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            overall_score REAL NOT NULL,
            pass_rate REAL NOT NULL,
            sample_count INTEGER NOT NULL,
            failing_count INTEGER NOT NULL,
            metric_breakdown_json TEXT,
            config_json TEXT
        )
    """

    def __init__(self, db_path: str = _EVAL_RESULTS_DB_DEFAULT):
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        self._init_table()

    def _init_table(self):
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(self._CREATE_TABLE)
            conn.commit()
        finally:
            conn.close()

    def store_result(self, result: EvalResult) -> bool:
        """Speichert ein EvalResult in SQLite."""
        d = result.to_dict()
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(
                """
                INSERT OR REPLACE INTO eval_results
                    (eval_id, eval_name, timestamp, overall_score, pass_rate,
                     sample_count, failing_count, metric_breakdown_json, config_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    d["eval_id"],
                    d["eval_name"],
                    d["timestamp"],
                    d["overall_score"],
                    d["pass_rate"],
                    d["sample_count"],
                    d["failing_count"],
                    json.dumps(d.get("metric_breakdown", {}), ensure_ascii=False),
                    json.dumps(d.get("config", {}), ensure_ascii=False),
                ),
            )
            conn.commit()
            logger.info("EvalResult persisted: %s (score=%.4f)", d["eval_id"], d["overall_score"])
            return True
        except Exception as exc:
            logger.error("Failed to persist EvalResult %s: %s", result.eval_id, exc)
            return False
        finally:
            conn.close()

    def load_eval_history(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Liest die letzten Eval-Ergebnisse aus SQLite."""
        conn = sqlite3.connect(self.db_path)
        try:
            cursor = conn.execute(
                """
                SELECT eval_id, eval_name, timestamp, overall_score, pass_rate,
                       sample_count, failing_count, metric_breakdown_json, config_json
                FROM eval_results
                ORDER BY rowid DESC
                LIMIT ?
                """,
                (limit,),
            )
            rows = cursor.fetchall()
            results = []
            for row in rows:
                results.append({
                    "eval_id": row[0],
                    "eval_name": row[1],
                    "timestamp": row[2],
                    "overall_score": row[3],
                    "pass_rate": row[4],
                    "sample_count": row[5],
                    "failing_count": row[6],
                    "metric_breakdown": json.loads(row[7]) if row[7] else {},
                    "config": json.loads(row[8]) if row[8] else {},
                })
            return results
        finally:
            conn.close()

    def get_score_trend(self, limit: int = 20) -> List[float]:
        """Gibt die Score-Entwicklung als Liste von floats zurueck."""
        history = self.load_eval_history(limit)
        return [r["overall_score"] for r in reversed(history)]


# ====================================================================
# MASSNAHME 4: GROUND-TRUTH-KURIERUNG
# ====================================================================

class GroundTruthCurator:
    """
    Manuelles / LLM-gestuetztes Labeling von Feedback-Samples
    fuer aussagekraeftige Faithfulness-Metriken.

    Tabelle ground_truth_labels:
      sample_id, ground_truth, labeled_by, labeled_at
    """

    _CREATE_TABLE = """
        CREATE TABLE IF NOT EXISTS ground_truth_labels (
            sample_id TEXT PRIMARY KEY,
            ground_truth TEXT NOT NULL,
            labeled_by TEXT NOT NULL DEFAULT 'manual',
            labeled_at TEXT NOT NULL
        )
    """

    def __init__(self, db_path: str = _EVAL_RESULTS_DB_DEFAULT):
        self.db_path = db_path
        self._init_table()

    def _init_table(self):
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(self._CREATE_TABLE)
            conn.commit()
        finally:
            conn.close()

    def label_sample(self, sample_id: str, ground_truth: str,
                     labeled_by: str = "manual") -> bool:
        """Manuelles Labeling eines Samples."""
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(
                """
                INSERT OR REPLACE INTO ground_truth_labels
                    (sample_id, ground_truth, labeled_by, labeled_at)
                VALUES (?, ?, ?, ?)
                """,
                (
                    sample_id,
                    ground_truth,
                    labeled_by,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            conn.commit()
            logger.info("Ground truth labeled: sample_id=%s by=%s", sample_id, labeled_by)
            return True
        except Exception as exc:
            logger.error("Failed to label sample %s: %s", sample_id, exc)
            return False
        finally:
            conn.close()

    def get_labelled(self, limit: int = 100) -> List[Dict[str, str]]:
        """Alle gelabelten Samples zurueckgeben."""
        conn = sqlite3.connect(self.db_path)
        try:
            cursor = conn.execute(
                "SELECT sample_id, ground_truth, labeled_by, labeled_at "
                "FROM ground_truth_labels ORDER BY rowid DESC LIMIT ?",
                (limit,),
            )
            return [
                {"sample_id": r[0], "ground_truth": r[1],
                 "labeled_by": r[2], "labeled_at": r[3]}
                for r in cursor.fetchall()
            ]
        finally:
            conn.close()

    def suggest_labeling(
        self,
        feedback_db_path: str = _FEEDBACK_DB_DEFAULT,
        limit: int = 50,
    ) -> List[EvalSample]:
        """
        Liefert unlabeled Feedback-Samples die noch keinen Ground-Truth
        haben — ideal fuer manuelles/LLM Labeling.
        """
        # Hole bereits gelabelte IDs
        labelled_ids = {lt["sample_id"] for lt in self.get_labelled(limit=10000)}

        # Hole Feedback-Samples via EvalDatasetBuilder
        builder = EvalDatasetBuilder()
        samples = builder.from_feedback(feedback_db_path=feedback_db_path, limit=limit)

        # Filter: nur unlabeled
        unlabeled = [s for s in samples if s.sample_id not in labelled_ids]
        logger.info(
            "Suggested %d unlabeled sample(s) for ground-truth curation "
            "(%d already labelled)", len(unlabeled), len(labelled_ids)
        )
        return unlabeled


# ====================================================================
# MASSNAHME 1 + 2: SCHEDULED EVAL-JOB + EVAL->OPTIMIZER-BRUECKE
# ====================================================================

class EvalScheduler:
    """
    Periodischer Eval-Job:
      1. EvalDatasetBuilder.from_feedback()
      2. StrixKAT.evaluate_batch()
      3. Persistenz via EvalResultPersistence
      4. Regression-Check -> FeedbackOptimizer.update_from_feedback()
    """

    def __init__(
        self,
        engine: StrixKATEvalEngine,
        persistence: EvalResultPersistence,
        feedback_db_path: str = _FEEDBACK_DB_DEFAULT,
        interval_seconds: int = 600,
        feedback_limit: int = 200,
    ):
        self.engine = engine
        self.persistence = persistence
        self.feedback_db_path = feedback_db_path
        self.interval_seconds = interval_seconds
        self.feedback_limit = feedback_limit

        self._running = False
        self._timer: Optional[threading.Timer] = None
        self._cycle_count = 0
        self._lock = threading.Lock()

    # ---- Lifecycle ----

    def start(self):
        """Startet den periodischen Eval-Job."""
        if self._running:
            logger.warning("EvalScheduler already running")
            return
        self._running = True
        logger.info("EvalScheduler started (interval=%ds)", self.interval_seconds)
        self._schedule_next()

    def stop(self):
        """Stoppt den EvalScheduler."""
        self._running = False
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
        logger.info("EvalScheduler stopped (cycles=%d)", self._cycle_count)

    def run_once(self) -> Optional[EvalResult]:
        """Führt genau einen Eval-Zyklus synchron aus (Tests / Manuell)."""
        return self._run_cycle()

    # ---- Intern ----

    def _schedule_next(self):
        if not self._running:
            return
        self._timer = threading.Timer(self.interval_seconds, self._run_cycle_loop)
        self._timer.daemon = True
        self._timer.start()

    def _run_cycle_loop(self):
        self._run_cycle()
        self._schedule_next()

    def _run_cycle(self) -> Optional[EvalResult]:
        """Ein einziger Eval-Zyklus."""
        with self._lock:
            self._cycle_count += 1
            cycle = self._cycle_count

        logger.info("=== EvalScheduler cycle %d ===", cycle)

        # 1. Samples aus Feedback laden
        builder = EvalDatasetBuilder()
        samples = builder.from_feedback(
            feedback_db_path=self.feedback_db_path,
            limit=self.feedback_limit,
        )

        if not samples:
            logger.info("EvalScheduler cycle %d: no feedback samples — skipping", cycle)
            return None

        # 2. Batch-Evaluation
        eval_name = f"scheduled_feedback_eval_{cycle}"
        result = self.engine.evaluate_batch(samples, eval_name=eval_name)

        # 3. Persistenz
        self.persistence.store_result(result)

        # 4. Regression-Check -> Optimizer-Bridge (Maßnahme 2)
        alert = self.engine.check_regression(result)
        if alert is not None:
            self._trigger_optimizer_from_regression(alert, result)

        logger.info(
            "EvalScheduler cycle %d: score=%.4f pass=%.1f%% samples=%d regression=%s",
            cycle, result.overall_score, result.pass_rate * 100,
            len(result.samples), "YES" if alert else "NO",
        )
        return result

    @staticmethod
    def _trigger_optimizer_from_regression(alert: RegressionAlert, result: EvalResult):
        """
        Eval->Optimizer-Bruecke: Bei Regression-Alert wird
        FeedbackOptimizer.update_from_feedback() automatisch getriggert.
        """
        try:
            from agent.feedback_optimizer import get_global_optimizer
            optimizer = get_global_optimizer()

            # Synthetische Insights aus Regression-Alert bauen
            insights = {
                "status": "ready",
                "samples": len(result.samples),
                "satisfaction_rate": alert.current_score,
                "recommendations": [
                    {
                        "issue": alert.message,
                        "action": "increase_faiss_weight",
                        "suggested_adjustment": min(0.2, alert.previous_score - alert.current_score),
                    }
                ],
            }

            update_result = optimizer.update_from_feedback(insights)
            if update_result.get("updated"):
                logger.info(
                    "Eval->Optimizer bridge: weights adjusted after regression "
                    "(FAISS=%.2f KG=%.2f)",
                    update_result["new_weights"][0],
                    update_result["new_weights"][1],
                )
            else:
                logger.info("Eval->Optimizer bridge: no weight change needed")

        except ImportError:
            logger.warning("FeedbackOptimizer not available — skipping optimizer bridge")
        except Exception as exc:
            logger.error("Eval->Optimizer bridge failed: %s", exc)


# ====================================================================
# MODULE ENTRY POINT
# ====================================================================

def create_engine(score_threshold: float = 0.10,
                 critical_threshold: float = 0.20) -> StrixKATEvalEngine:
    """Create a StrixKAT eval engine with default adapters."""
    return StrixKATEvalEngine(
        score_threshold=score_threshold,
        critical_threshold=critical_threshold,
    )


def create_dataset_builder(storage_path: str = "data/eval_datasets") -> EvalDatasetBuilder:
    """Create an eval dataset builder."""
    return EvalDatasetBuilder(storage_path=storage_path)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    # Create engine
    engine = create_engine()

    # Create sample dataset
    builder = create_dataset_builder()

    # Add test samples
    builder.add_sample(
        query="Was war der Umsatz im Q3?",
        answer="Der Umsatz im Q3 betrug 1.5 Millionen Euro.",
        ground_truth="Der Umsatz im dritten Quartal lag bei 1.5 Millionen.",
        context_chunks=["Quartalszahlen: Q1=1.2M, Q2=1.4M, Q3=1.5M"],
        content_type=ContentType.TABLE,
    )

    builder.add_sample(
        query="Wie entwickelten sich die Kosten?",
        answer="Die Kosten sind von 0.8M auf 0.7M gesunken, was einer Reduktion von etwa 12.5 Prozent entspricht.",
        ground_truth="Kosten wurden um 5 Prozent reduziert.",
        context_chunks=["Kostenentwicklung: Q1=0.8M, Q2=0.75M, Q3=0.7M"],
        content_type=ContentType.TEXT,
    )

    builder.add_sample(
        query="Was zeigt das Umsatzdiagramm?",
        answer="Das Diagramm zeigt einen steigenden Trend vom Q1 zum Q3.",
        ground_truth="Liniendiagramm zeigt steigenden Trend.",
        context_chunks=["Umsatzentwicklung: Liniendiagramm mit steigendem Trend Q1-Q3"],
        content_type=ContentType.FIGURE,
    )

    # Build and evaluate
    samples = builder.build_samples()
    result = engine.evaluate_batch(samples, eval_name="sota_demo", config={"hardware": "RTX4090"})

    print(f"\n{'='*60}")
    print(f"StrixKAT Evaluation Report")
    print(f"{'='*60}")
    print(f"Overall Score:    {result.overall_score:.4f}")
    print(f"Pass Rate:        {result.pass_rate:.1%}")
    print(f"Samples:          {len(result.samples)}")
    print(f"Failing:          {len(result.failing_samples)}")
    print(f"")

    breakdown = result.metric_breakdown()
    print(f"Metric Breakdown:")
    for category, stats in breakdown.items():
        print(f"  {category:20s} mean={stats['mean']:.4f}  min={stats['min']:.4f}  max={stats['max']:.4f}")

    # Check regression
    alert = engine.check_regression(result)
    if alert:
        print(f"\n⚠️  REGRESSION ALERT: {alert.message}")
    else:
        print(f"\n✅ No regression detected")

    # Dashboard metrics
    metrics = engine.get_dashboard_metrics()
    print(f"\nDashboard:")
    print(f"  Trend:       {metrics.get('trend', 'N/A')}")
    print(f"  Eval Count:  {metrics.get('eval_count', 0)}")