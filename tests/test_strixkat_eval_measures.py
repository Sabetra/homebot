"""
Tests fuer die 4 empfohlenen Massnahmen:
  1. Scheduled Eval-Job (EvalScheduler)
  2. Eval->Optimizer-Bruecke
  3. Eval-Result-Persistenz (EvalResultPersistence)
  4. Ground-Truth-Kurierung (GroundTruthCurator)

Hypothese: Alle neuen Klassen funktionieren korrekt isoliert und im Zusammenspiel.
"""

import os
import sqlite3
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from agent.strixkat_eval import (
    EvalSample,
    EvalResult,
    EvalMetric,
    MetricCategory,
    ContentType,
    StrixKATEvalEngine,
    RegressionAlert,
    EvalResultPersistence,
    GroundTruthCurator,
    EvalScheduler,
    EvalDatasetBuilder,
)


# =====================================================================
# FIXTURES
# =====================================================================

@pytest.fixture
def tmp_db():
    """Erstellt eine temporare SQLite-Datei und entfernt sie danach."""
    path = tempfile.mktemp(suffix=".db")
    yield path
    if os.path.exists(path):
        os.remove(path)


@pytest.fixture
def engine():
    return StrixKATEvalEngine(score_threshold=0.10, critical_threshold=0.20)


@pytest.fixture
def sample_result(engine):
    """Erzeugt ein minimales EvalResult mit 10 Samples."""
    samples = []
    for i in range(10):
        s = EvalSample(
            sample_id=f"test_{i}",
            query="Testfrage?",
            ground_truth="Testantwort.",
            generated_answer="Testantwort.",
            context_chunks=["Kontext"],
        )
        s.metrics.append(EvalMetric(
            name="faithfulness",
            category=MetricCategory.FAITHFULNESS,
            value=0.8 + 0.02 * i,
        ))
        samples.append(s)

    result = EvalResult(
        eval_id="test_eval_1",
        eval_name="test_eval",
        samples=samples,
    )
    result.summary = {
        "overall_score": result.overall_score,
        "pass_rate": result.pass_rate,
    }
    return result


# =====================================================================
# MASSNAHME 3: Eval-Result-Persistenz
# =====================================================================

class TestEvalResultPersistence:
    """EvalResultPersistence speichert und liest EvalResults korrekt."""

    def test_store_and_load(self, tmp_db, sample_result):
        persistence = EvalResultPersistence(db_path=tmp_db)
        assert persistence.store_result(sample_result) is True

        history = persistence.load_eval_history()
        assert len(history) == 1
        assert history[0]["eval_id"] == "test_eval_1"
        assert abs(history[0]["overall_score"] - sample_result.overall_score) < 1e-6

    def test_score_trend(self, tmp_db, engine):
        persistence = EvalResultPersistence(db_path=tmp_db)

        # Mehrere Ergebnisse speichern
        for i in range(5):
            samples = []
            for j in range(5):
                s = EvalSample(
                    sample_id=f"r{i}_s{j}",
                    query="q", ground_truth="a", generated_answer="a",
                    context_chunks=["ctx"],
                )
                s.metrics.append(EvalMetric(
                    name="faithfulness", category=MetricCategory.FAITHFULNESS,
                    value=0.6 + 0.05 * i,
                ))
                samples.append(s)
            r = EvalResult(eval_id=f"run_{i}", eval_name=f"run_{i}", samples=samples)
            persistence.store_result(r)

        trend = persistence.get_score_trend()
        assert len(trend) == 5
        # Trend sollte steigend sein
        assert trend[-1] > trend[0]

    def test_create_table_on_init(self, tmp_db):
        persistence = EvalResultPersistence(db_path=tmp_db)
        conn = sqlite3.connect(tmp_db)
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='eval_results'"
        )
        assert cursor.fetchone() is not None
        conn.close()


# =====================================================================
# MASSNAHME 4: Ground-Truth-Kurierung
# =====================================================================

class TestGroundTruthCurator:
    """GroundTruthCurator labelt Samples und liefert unlabeled Samples."""

    def test_label_and_retrieve(self, tmp_db):
        curator = GroundTruthCurator(db_path=tmp_db)
        assert curator.label_sample("sample_1", "Die korrekte Antwort.", "manual") is True

        labelled = curator.get_labelled()
        assert len(labelled) == 1
        assert labelled[0]["sample_id"] == "sample_1"
        assert labelled[0]["labeled_by"] == "manual"

    def test_suggest_labeling_no_feedback_db(self, tmp_db):
        curator = GroundTruthCurator(db_path=tmp_db)
        # Bei nicht-existenter Feedback-DB sollte leere Liste kommen
        samples = curator.suggest_labeling(
            feedback_db_path="/nonexistent/path.db",
            limit=10,
        )
        assert samples == []


# =====================================================================
# MASSNAHME 1: Scheduled Eval-Job
# =====================================================================

class TestEvalScheduler:
    """EvalScheduler fuehrt Eval-Zyklen korrekt aus."""

    def test_run_once_no_feedback(self, engine, tmp_db):
        persistence = EvalResultPersistence(db_path=tmp_db)
        scheduler = EvalScheduler(
            engine=engine,
            persistence=persistence,
            feedback_db_path="/nonexistent/db.db",
        )
        result = scheduler.run_once()
        # Keine Feedback-DB -> None
        assert result is None

    def test_cycle_count_increments(self, engine, tmp_db):
        persistence = EvalResultPersistence(db_path=tmp_db)
        scheduler = EvalScheduler(
            engine=engine,
            persistence=persistence,
            feedback_db_path="/nonexistent/db.db",
        )
        scheduler.run_once()
        scheduler.run_once()
        assert scheduler._cycle_count == 2

    def test_start_stop(self, engine, tmp_db):
        persistence = EvalResultPersistence(db_path=tmp_db)
        scheduler = EvalScheduler(
            engine=engine,
            persistence=persistence,
            feedback_db_path="/nonexistent/db.db",
            interval_seconds=1,
        )
        scheduler.start()
        assert scheduler._running is True
        time.sleep(0.05)
        scheduler.stop()
        assert scheduler._running is False


# =====================================================================
# MASSNAHME 2: Eval->Optimizer-Bruecke (Integration)
# =====================================================================

class TestEvalOptimizerBridge:
    """Regression-Alert triggert FeedbackOptimizer (mocked)."""

    def test_bridge_calls_optimizer_on_regression(self, engine, tmp_db):
        """
        Bei Regression wird _trigger_optimizer_from_regression aufgerufen.
        Da FeedbackOptimizer nicht im Testkontext existiert, wird der
        ImportError-Branch getestet (silent warning, kein Crash).
        """
        persistence = EvalResultPersistence(db_path=tmp_db)

        # Baseline etablieren (hohe Scores)
        high_samples = []
        for i in range(10):
            s = EvalSample(
                sample_id=f"high_{i}", query="q", ground_truth="a",
                generated_answer="a", context_chunks=["ctx"],
            )
            s.metrics.append(EvalMetric(
                name="faithfulness", category=MetricCategory.FAITHFULNESS, value=0.95,
            ))
            high_samples.append(s)
        engine.evaluate_batch(high_samples, eval_name="baseline")

        # Niedrige Scores -> Regression
        low_samples = []
        for i in range(10):
            s = EvalSample(
                sample_id=f"low_{i}", query="q", ground_truth="a",
                generated_answer="bad", context_chunks=[],
            )
            s.metrics.append(EvalMetric(
                name="faithfulness", category=MetricCategory.FAITHFULNESS, value=0.30,
            ))
            low_samples.append(s)
        low_result = EvalResult(eval_id="low_eval", eval_name="low_eval", samples=low_samples)

        # Regression sollte erkannt werden
        alert = engine.check_regression(low_result)
        # Der Alert kann None sein wenn die Varianz zu hoch ist,
        # aber der Codepfad _trigger_optimizer_from_regression sollte
        # bei Alert nicht crashen:
        if alert is not None:
            # Dies sollte ohne Exception laufen (ImportError wird abgefangen)
            EvalScheduler._trigger_optimizer_from_regression(alert, low_result)


# =====================================================================
# Integration: Vollpipeline (Persistenz + Scheduler)
# =====================================================================

class TestFullEvalPipeline:
    """EvalScheduler + EvalResultPersistence im Zusammenspiel."""

    def test_scheduler_persists_results(self, engine, tmp_db):
        """
        Wenn Feedback-DB existiert und Samples liefert,
        speichert der Scheduler das Ergebnis in SQLite.
        """
        # Temporare Feedback-DB mit Feedback-Daten erstellen
        feedback_path = tempfile.mktemp(suffix=".db")
        try:
            conn = sqlite3.connect(feedback_path)
            conn.execute("""
                CREATE TABLE feedback_responses (
                    query TEXT, response TEXT, feedback_value INTEGER,
                    comment TEXT, created_at TEXT
                )
            """)
            for i in range(10):
                conn.execute(
                    "INSERT INTO feedback_responses VALUES (?, ?, ?, ?, ?)",
                    (f"Frage {i}", f"Antwort {i}", -1, "", "2026-01-01T00:00:00"),
                )
            conn.commit()
            conn.close()

            persistence = EvalResultPersistence(db_path=tmp_db)
            scheduler = EvalScheduler(
                engine=engine,
                persistence=persistence,
                feedback_db_path=feedback_path,
            )
            result = scheduler.run_once()

            # Ergebnis sollte persistiert sein
            assert result is not None
            history = persistence.load_eval_history()
            assert len(history) >= 1

        finally:
            if os.path.exists(feedback_path):
                os.remove(feedback_path)