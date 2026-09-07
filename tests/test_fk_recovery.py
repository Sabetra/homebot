"""
Test root-cause FK recovery in SessionManagerAdapter

Tests that SessionManagerAdapter.add_message_with_result() validates sessions BEFORE calling
save_interaction(), preventing FK constraint errors at the source:

1. SessionManagerAdapter validates session exists (via _validate_session_exists)
2. Auto-recovery creates missing session if needed
3. save_interaction() is only called after validation succeeds
4. DB-level FK check is now backup-only (should never be triggered)
"""

import pytest
import sqlite3
import tempfile
import os
from pathlib import Path
from datetime import datetime, timezone
import logging

# Add parent dir to path
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wellbeing.wellbeing_db import WellbeingDatabase
from wellbeing.session_manager import WellbeingSessionManager
from wellbeing_session.adapters.session_manager_adapter import SessionManagerAdapter

# Suppress debug logging for cleaner test output
logging.getLogger("wellbeing_session").setLevel(logging.WARNING)



@pytest.fixture
def temp_db_and_adapter():
    """Create temporary test database with SessionManagerAdapter"""
    temp_dir = tempfile.mkdtemp()
    db_path = os.path.join(temp_dir, "test_fk_recovery.db")
    
    db = WellbeingDatabase(db_path=db_path)
    manager = WellbeingSessionManager(db=db)
    adapter = SessionManagerAdapter(wellbeing_manager=manager)
    
    yield db, adapter
    
    # Cleanup
    if os.path.exists(db_path):
        os.unlink(db_path)


@pytest.fixture
def temp_db():
    """Create temporary test database"""
    temp_dir = tempfile.mkdtemp()
    db_path = os.path.join(temp_dir, "test_fk_recovery.db")
    
    db = WellbeingDatabase(db_path=db_path)
    yield db
    
    # Cleanup
    if os.path.exists(db_path):
        os.unlink(db_path)


def test_adapter_add_message_with_valid_session(temp_db_and_adapter):
    """Test normal case: explicit add-message result with valid session."""
    db, adapter = temp_db_and_adapter
    
    # Create a session
    user_id = "test_user"
    session_id = db.create_session(user_id)
    
    # Add message via adapter (should succeed without error)
    result = adapter.add_message_with_result(
        session_id=session_id,
        role="user",
        content="Hello, how are you?"
    )
    assert result.success
    assert result.session_id == session_id
    
    # Verify interaction was saved to DB
    with db.get_connection() as conn:
        interaction = conn.execute(
            "SELECT id, session_id, role FROM session_interactions WHERE session_id = ?",
            (session_id,)
        ).fetchone()
        assert interaction is not None, "Interaction should have been saved"
        assert interaction[1] == session_id
        assert interaction[2] == "user"


def test_adapter_rejects_unknown_session_without_inventing_user_identity(temp_db_and_adapter):
    """
    An unknown session ID contains no trustworthy user identity for recovery.
    """
    db, adapter = temp_db_and_adapter
    
    # Use a session_id that doesn't exist
    orphaned_session_id = "orphaned_session_12345"
    
    # Verify session doesn't exist
    with db.get_connection() as conn:
        exists = conn.execute(
            "SELECT id FROM wellbeing_sessions WHERE id = ?",
            (orphaned_session_id,)
        ).fetchone()
        assert exists is None, "Session should not exist initially"
    
    result = adapter.add_message_with_result(
        session_id=orphaned_session_id,
        role="user",
        content="Message via adapter to missing session"
    )
    assert not result.success
    assert result.session_id is None
    
    with db.get_connection() as conn:
        interaction = conn.execute(
            "SELECT id, session_id, role FROM session_interactions WHERE role = 'user'",
        ).fetchone()
        assert interaction is None


def test_adapter_rebinds_stale_cache_instead_of_writing_to_missing_session(temp_db_and_adapter, monkeypatch):
    """Cached identity may enable rebind, but cannot authorize the stale session ID."""
    db, adapter = temp_db_and_adapter
    session_id = adapter.manager.create_or_restore_session(
        user_id="stale_cache_user",
        restore_if_recent=False,
    )

    with db.get_connection() as conn:
        conn.execute("DELETE FROM wellbeing_sessions WHERE id = ?", (session_id,))
        conn.commit()

    written_session_ids = []
    original_save_interaction = db.save_interaction

    def track_save_interaction(*args, **kwargs):
        written_session_ids.append(kwargs.get("session_id", args[0] if args else None))
        return original_save_interaction(*args, **kwargs)

    monkeypatch.setattr(db, "save_interaction", track_save_interaction)

    result = adapter.add_message_with_result(
        session_id=session_id,
        role="user",
        content="This must not reach the interaction writer",
    )

    assert result.success
    assert result.rebinding_occurred
    assert result.session_id != session_id
    assert written_session_ids == [result.session_id]


def test_db_save_interaction_with_existing_session(temp_db):
    """Test normal case: db.save_interaction() with existing session"""
    # Create a session
    user_id = "test_user"
    session_id = temp_db.create_session(user_id)
    
    # Save interaction directly to DB (after valid session exists)
    interaction_id = temp_db.save_interaction(
        session_id=session_id,
        role="user",
        content="Hello, how are you?"
    )
    
    assert interaction_id is not None
    assert isinstance(interaction_id, int)
    assert interaction_id > 0


def test_db_save_interaction_fk_error_with_missing_session(temp_db):
    """
    Test DB-level backup check: save_interaction() with missing session
    
    IMPORTANT: Behavior changed - now returns None instead of raising FK error.
    The pre-check (before INSERT) returns None cleanly instead of letting
    the FK constraint fail during INSERT.
    
    This is the backup validation (should never trigger in normal flow,
    but handles edge cases where session is deleted between validation and INSERT).
    """
    orphaned_session_id = "orphaned_session_should_fail"
    
    # Verify session doesn't exist
    with temp_db.get_connection() as conn:
        exists = conn.execute(
            "SELECT id FROM wellbeing_sessions WHERE id = ?",
            (orphaned_session_id,)
        ).fetchone()
        assert exists is None
    
    # Try to save interaction directly to non-existent session
    # Should return None (clean failure) instead of raising FK error
    result = temp_db.save_interaction(
        session_id=orphaned_session_id,
        role="user",
        content="This should fail - direct DB call without adapter validation"
    )
    
    # Should return None (backup FK check prevented the error)
    assert result is None, "save_interaction() should return None when session doesn't exist"


def test_save_interaction_duplicate_protection_still_works(temp_db):
    """Test that duplicate protection still works (unaffected by root-cause fix)"""
    user_id = "test_user"
    session_id = temp_db.create_session(user_id)
    
    content = "Duplicate test message"
    
    # Save first interaction
    id1 = temp_db.save_interaction(
        session_id=session_id,
        role="user",
        content=content
    )
    assert id1 is not None
    
    # Try to save identical interaction (should be caught by content_hash UNIQUE)
    id2 = temp_db.save_interaction(
        session_id=session_id,
        role="user",
        content=content
    )
    
    # Should return same ID (duplicate protection still works)
    assert id2 == id1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
