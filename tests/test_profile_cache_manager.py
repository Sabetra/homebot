from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

import pytest

from wellbeing.profile_cache_manager import ProfileCacheManager
from wellbeing.profile_synthesizer import WellbeingProfile


class _InMemoryDB:
    def __init__(self) -> None:
        self._conn = sqlite3.connect(":memory:")
        self._conn.row_factory = sqlite3.Row

    @contextmanager
    def get_connection(self):
        try:
            yield self._conn
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

    def _encrypt_data(self, value: str) -> str:
        return value

    def _decrypt_data(self, value: str) -> str:
        return value


class _BrokenEncryptDB(_InMemoryDB):
    def _encrypt_data(self, value: str) -> str:
        raise ValueError("encryption failed")


class _SynthesizerStub:
    def synthesize_profile(self, user_id: str, **_: object) -> WellbeingProfile:
        now = datetime.now(timezone.utc).isoformat()
        return WellbeingProfile(
            user_id=user_id,
            version=1,
            synthesis_type="full",
            core_personality={"traits": ["reflective"], "confidence": 0.7},
            current_state={"primary_concerns": ["stress"], "stress_level": "medium", "confidence": 0.7},
            relationships={"confidence": 0.7},
            goals_and_growth={"current_goals": ["rest"], "confidence": 0.7},
            coping_and_resources={"strategies": ["breathing"], "confidence": 0.7},
            therapeutic_focus={"priority_areas": ["sleep"], "confidence": 0.7},
            overall_confidence=0.7,
            data_sources={"kg_triples_used": 1, "sessions_used": 1, "insights_used": 1},
            synthesis_model="unit-test-model",
            synthesis_prompt_hash="hash",
            created_at=now,
            updated_at=now,
        )


def test_migrate_schema_is_idempotent():
    db = _InMemoryDB()
    manager = ProfileCacheManager(wellbeing_db=db, profile_synthesizer=None)

    with db.get_connection() as conn:
        manager._migrate_schema(conn)
        manager._migrate_schema(conn)

    with db.get_connection() as conn:
        cols = {
            row[1]
            for row in conn.execute("PRAGMA table_info(wellbeing_profiles)").fetchall()
        }

    assert "synthesis_type" in cols
    assert "delta_count_since_full" in cols
    assert "last_synthesis_at" in cols


def test_load_from_db_raises_for_corrupt_payload():
    db = _InMemoryDB()
    manager = ProfileCacheManager(wellbeing_db=db, profile_synthesizer=None)

    with db.get_connection() as conn:
        conn.execute(
            """
            INSERT INTO wellbeing_profiles (
                user_id, profile_version, profile_data, created_at, updated_at, expires_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            ("u1", 1, "not-json", "2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00", "2099-01-01T00:00:00+00:00"),
        )

    with pytest.raises(RuntimeError, match="DB load failed"):
        manager._load_from_db("u1")


def test_get_cached_profile_raises_when_persistence_fails():
    db = _BrokenEncryptDB()
    manager = ProfileCacheManager(wellbeing_db=db, profile_synthesizer=_SynthesizerStub())

    with pytest.raises(RuntimeError, match="DB save failed"):
        manager.get_cached_profile("user-1", force_regenerate=True)


def test_load_from_db_success_with_metadata_fields():
    db = _InMemoryDB()
    manager = ProfileCacheManager(wellbeing_db=db, profile_synthesizer=None)

    profile_payload = {
        "user_id": "u2",
        "version": 3,
        "synthesis_type": "delta",
        "core_personality": {"confidence": 0.7},
        "current_state": {"confidence": 0.7, "stress_level": "low", "primary_concerns": []},
        "relationships": {"confidence": 0.7},
        "goals_and_growth": {"confidence": 0.7, "current_goals": []},
        "coping_and_resources": {"confidence": 0.7, "strategies": []},
        "therapeutic_focus": {"confidence": 0.7, "priority_areas": []},
        "overall_confidence": 0.7,
        "data_sources": {"kg_triples_used": 1, "sessions_used": 1, "insights_used": 1},
        "synthesis_model": "m",
        "synthesis_prompt_hash": "h",
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-01T00:00:00+00:00",
    }

    with db.get_connection() as conn:
        conn.execute(
            """
            INSERT INTO wellbeing_profiles (
                user_id, profile_version, profile_data, synthesis_type,
                delta_count_since_full, last_synthesis_at,
                created_at, updated_at, expires_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "u2",
                3,
                json.dumps(profile_payload),
                "delta",
                2,
                "2026-01-01T00:00:00+00:00",
                "2026-01-01T00:00:00+00:00",
                "2026-01-01T00:00:00+00:00",
                "2099-01-01T00:00:00+00:00",
            ),
        )

    loaded = manager._load_from_db("u2")

    assert loaded is not None
    assert loaded["version"] == 3
    assert loaded["_db_delta_count_since_full"] == 2
    assert loaded["_db_last_synthesis_at"] == "2026-01-01T00:00:00+00:00"


def test_get_user_lock_is_reused_per_user_and_distinct_between_users():
    db = _InMemoryDB()
    manager = ProfileCacheManager(wellbeing_db=db, profile_synthesizer=None)

    lock_a1 = manager._get_user_lock("u-a")
    lock_a2 = manager._get_user_lock("u-a")
    lock_b = manager._get_user_lock("u-b")

    assert lock_a1 is lock_a2
    assert lock_a1 is not lock_b


def test_clear_cache_user_removes_user_lock_entry():
    db = _InMemoryDB()
    manager = ProfileCacheManager(wellbeing_db=db, profile_synthesizer=None)

    _ = manager._get_user_lock("u-clear")
    assert "u-clear" in manager._user_locks

    manager.clear_cache("u-clear")

    assert "u-clear" not in manager._user_locks
