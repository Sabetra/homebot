#!/usr/bin/env python3
"""
Tests für UserInsightsProvider Fehler-Propagation (P0).

Sichert, dass der Provider Exceptions nicht verschluckt, sondern zum
Builder propagiert, der sie in `UserContextResult.errors` aufnimmt.
"""
import logging
from unittest.mock import MagicMock, patch
import pytest

from user_context_builder.providers.user_insights import UserInsightsProvider
from user_context_builder.models import UserContextRequest


class TestUserInsightsProviderErrorPropagation:
    """Stelle sicher, dass DB-Fehler zum Builder durchkommen."""

    def _make_request(self, user_id="usr_test123456789012"):
        return UserContextRequest(
            user_id=user_id,
            current_session_id="ses_test123456789012",
            user_input="Wie geht es dir?",
        )

    def _make_session_manager_with_db(self, db_mock):
        sm = MagicMock()
        sm.db = db_mock
        return sm

    # -- P0: Exception propagation -----------------------------------------

    def test_fetch_error_is_propagated(self):
        """Wenn `_fetch_insights` eine Exception wirft, muss `provide()` sie
        ebenfalls werfen (nicht mit `[]` verschlucken)."""
        provider = UserInsightsProvider()
        db_mock = MagicMock()
        conn_mock = MagicMock()
        db_mock.get_connection.return_value.__enter__ = MagicMock(return_value=conn_mock)
        db_mock.get_connection.return_value.__exit__ = MagicMock(return_value=False)

        # Simuliere einen DB-Fehler
        conn_mock.execute.side_effect = sqlite3_error = Exception("DB connection lost")

        session_manager = self._make_session_manager_with_db(db_mock)
        request = self._make_request()

        with pytest.raises(Exception) as exc_info:
            provider.provide(request, session_manager)

        assert "DB connection lost" in str(exc_info.value)

    def test_no_db_returns_empty_list(self):
        """Wenn keine DB verfügbar ist, wird `[]` zurückgegeben (kein Fehler)."""
        provider = UserInsightsProvider()
        session_manager = MagicMock()
        session_manager.db = None
        session_manager.manager = None

        request = self._make_request()
        result = provider.provide(request, session_manager)
        assert result == []

    def test_successful_fetch_returns_insights(self):
        """Bei erfolgreichem Fetch wird die Liste von Insights zurückgegeben."""
        provider = UserInsightsProvider()
        db_mock = MagicMock()
        conn_mock = MagicMock()
        db_mock.get_connection.return_value.__enter__ = MagicMock(return_value=conn_mock)
        db_mock.get_connection.return_value.__exit__ = MagicMock(return_value=False)

        # Simuliere eine erfolgreiche Abfrage mit einer Zeile
        row = (
            "emotional_state",  # insight_type
            "current_state",   # category
            "Hohes Stresslevel",  # value
            0.85,             # confidence
            "current",        # temporal_context
            3,               # mention_count
            "ses_001",       # first_session_id
            "ses_003",       # last_session_id
            "2026-07-01T00:00:00",  # first_seen_at
            "2026-08-01T00:00:00",  # last_seen_at
        )
        cursor_mock = MagicMock()
        cursor_mock.fetchall.return_value = [row]
        conn_mock.execute.return_value = cursor_mock

        session_manager = self._make_session_manager_with_db(db_mock)
        request = self._make_request()

        result = provider.provide(request, session_manager)

        assert result is not None
        assert len(result) >= 1
        assert result[0]["type"] == "emotional_state"
        assert result[0]["value"] == "Hohes Stresslevel"

    def test_builder_catches_provider_exception(self):
        """Der Builder muss die Exception fangen und in `errors` eintragen."""
        from user_context_builder.builder import UserContextBuilder

        provider = UserInsightsProvider()
        db_mock = MagicMock()
        conn_mock = MagicMock()
        db_mock.get_connection.return_value.__enter__ = MagicMock(return_value=conn_mock)
        db_mock.get_connection.return_value.__exit__ = MagicMock(return_value=False)
        conn_mock.execute.side_effect = Exception("Simulated DB crash")

        builder = UserContextBuilder(providers=[provider])
        session_manager = self._make_session_manager_with_db(db_mock)
        request = self._make_request()

        result = builder.build(request, session_manager)

        assert len(result.errors) >= 1
        assert any("Simulated DB crash" in e for e in result.errors)
        assert any("user_insights" in e for e in result.errors)


class TestUserInsightsProviderScoring:
    """Überprüfe, dass log-Bayes-Scoring korrekt funktioniert."""

    def _make_request(self):
        return UserContextRequest(
            user_id="usr_scoring12345678",
            current_session_id="ses_scoring12345678",
            user_input="Test",
        )

    def test_high_confidence_high_mentions_ranks_first(self):
        """Insights mit hoher Confidence und vielen Mentions erhalten den
        höchsten Score."""
        provider = UserInsightsProvider(max_insights=15)
        db_mock = MagicMock()
        conn_mock = MagicMock()
        db_mock.get_connection.return_value.__enter__ = MagicMock(return_value=conn_mock)
        db_mock.get_connection.return_value.__exit__ = MagicMock(return_value=False)

        # Zwei Insights: eines mit hoher Confidence+Mentions, eines niedrig
        rows = [
            # (type, category, value, confidence, temporal, mention, first_ses, last_ses, first_at, last_at)
            ("emotional_state", "current_state", "Niedriges Stresslevel", 0.3, "current", 1, "s1", "s1", "2026-01-01", "2026-01-01"),
            ("personality_trait", "core_personality", "Resilient", 0.95, "current", 10, "s2", "s5", "2026-01-01", "2026-06-01"),
        ]
        cursor_mock = MagicMock()
        cursor_mock.fetchall.return_value = rows
        conn_mock.execute.return_value = cursor_mock

        session_manager = MagicMock()
        session_manager.db = db_mock

        result = provider.provide(self._make_request(), session_manager)

        assert result is not None
        assert len(result) == 2
        # Das resiliente Insight muss an erster Stelle sein
        assert result[0]["value"] == "Resilient"


# sqlite3 Error stub for test environment
import sqlite3 as _sqlite3
sqlite3_error = _sqlite3.OperationalError("DB connection lost")