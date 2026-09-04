from contextlib import contextmanager
import json
import sqlite3
from types import SimpleNamespace

import pytest

import wellbeing_session.context.session_context_builder as session_context_module
import wellbeing_session.handlers.response_generator as response_module
from wellbeing_session.context.session_context_builder import SessionContextBuilder
from wellbeing_session.handlers.response_generator import ResponseGenerator
from wellbeing.mood_progression_tracker import MoodProgressionTracker


class _SessionManagerWithoutIdentity:
    def get_session_summary(self, session_id):
        return {"session_id": session_id, "user_id": None}


def test_response_generator_rejects_missing_persisted_identity(monkeypatch) -> None:
    monkeypatch.setattr(
        response_module.st,
        "session_state",
        SimpleNamespace(psych_current_session="session-1"),
    )
    generator = ResponseGenerator(
        _SessionManagerWithoutIdentity(),
        context_manager=object(),
    )

    with pytest.raises(RuntimeError, match="no persisted user identity"):
        generator.generate_psychological_response(
            "hello",
            build_context_func=lambda **kwargs: {},
            format_context_func=lambda context: "",
        )


def test_session_context_builder_rejects_missing_persisted_identity(monkeypatch) -> None:
    monkeypatch.setattr(
        session_context_module.st,
        "session_state",
        SimpleNamespace(psych_current_session="session-1"),
    )
    builder = SessionContextBuilder(_SessionManagerWithoutIdentity())

    with pytest.raises(RuntimeError, match="no persisted user identity"):
        builder.build_session_context("hello")


class _MoodDatabase:
    def __init__(self, db_path) -> None:
        self.db_path = str(db_path)

    @contextmanager
    def get_connection(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def _decrypt_data(self, value):
        return value


def test_mood_series_uses_only_user_turns(tmp_path) -> None:
    db_path = tmp_path / "mood.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "CREATE TABLE session_interactions ("
            "session_id TEXT, role TEXT, mood_indicators TEXT, created_at TEXT)"
        )
        conn.executemany(
            "INSERT INTO session_interactions VALUES (?, ?, ?, datetime('now'))",
            [
                ("session-1", "user", json.dumps({"overall_mood": "sad"})),
                ("session-1", "assistant", json.dumps({"overall_mood": "hopeful"})),
            ],
        )

    series = MoodProgressionTracker(_MoodDatabase(db_path))._load_mood_series("session-1")

    assert [item["mood"] for item in series] == ["sad"]