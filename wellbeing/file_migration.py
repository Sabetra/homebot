"""Phase E (2026-09-01): idempotente Umbenennung der Wellbeing-Datei-/Key-/Cache-Artefakte.

Renamed legacy Wellbeing artifacts to neutral names:

    psychological_support.db         ->  wellbeing_store.db
    psychological_support.db.key     ->  wellbeing_store.db.key
    psychological_support_kg_cache/  ->  wellbeing_kg_cache/

Why this must run in ``WellbeingDatabase.__init__`` (before ``_init_encryption``):
    The Fernet key path is derived from the DB path (``<db_path>.key``). If the
    resolver is switched to ``wellbeing_store.db`` while the on-disk files are
    still ``psychological_support.db*``, encryption would generate a FRESH key
    next to a FRESH empty DB and orphan the real (encrypted) legacy data --
    silent, unrecoverable data loss. So the files MUST be moved first, as a
    DB+key pair, before any key is resolved/generated.

Safety contract (fail loud, never silent):
- Legacy DB present + legacy key MISSING  -> RuntimeError (DB would be undecryptable).
- Legacy key not a valid Fernet key       -> RuntimeError (mismatched/corrupt key).
- Legacy AND new DB (or new key) present -> RuntimeError (ambiguous; resolve manually).
- WAL is checkpointed (TRUNCATE) before the move so the main .db is self-contained.
- DB and key are moved as a pair; if the key move fails, the DB is restored.
- Idempotent: if the legacy names are absent (fresh install / already migrated)
  it is a clean no-op.

This is a *renaming* migration, not a security fix: the same key material is
preserved, the same rows are preserved, only the file/directory names change.
"""
from __future__ import annotations

import logging
import os
import shutil
import sqlite3
from pathlib import Path
from typing import Any, Dict, Union

from cryptography.fernet import Fernet

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Single source of truth for the file-level rename (legacy -> neutral).
# ---------------------------------------------------------------------------
LEGACY_DB_NAME = "psychological_support.db"
LEGACY_KEY_NAME = "psychological_support.db.key"
LEGACY_CACHE_DIR = "psychological_support_kg_cache"
NEW_DB_NAME = "wellbeing_store.db"
NEW_KEY_NAME = "wellbeing_store.db.key"
NEW_CACHE_DIR = "wellbeing_kg_cache"


def _checkpoint_wal(db_path: Path) -> None:
    """Flush + truncate the WAL so the main .db file is self-contained before move."""
    try:
        conn = sqlite3.connect(str(db_path))
        try:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            conn.commit()
        finally:
            conn.close()
    except sqlite3.Error as exc:
        # DB may not be in WAL mode, or already consistent -- non-fatal.
        logger.warning("WAL checkpoint skipped for %s: %s", db_path, exc)


def _validate_fernet_key(key_path: Path) -> bytes:
    """Read + validate the key is a usable Fernet key; raise on any problem."""
    raw = key_path.read_bytes()
    if not raw:
        raise ValueError("key file is empty")
    Fernet(raw)  # raises InvalidToken/ValueError if not a valid URL-safe token
    return raw


def migrate_wellbeing_files(new_db_path: Union[str, Path]) -> Dict[str, Any]:
    """One-shot, idempotent rename of legacy Wellbeing file/key/cache artifacts.

    Must run BEFORE ``WellbeingDatabase._init_encryption`` resolves/generates the
    key (the key path is ``<db_path>.key``). Returns a small stats dict and raises
    ``RuntimeError`` on any unsafe condition (missing/invalid key, name conflicts,
    or an in-use DB). On a fresh install or an already-migrated store it is a
    no-op and returns ``moved=False``.
    """
    new_db = Path(new_db_path).expanduser().resolve()
    parent = new_db.parent

    old_db = parent / LEGACY_DB_NAME
    old_key = parent / LEGACY_KEY_NAME
    new_key = parent / NEW_KEY_NAME
    old_cache = parent / LEGACY_CACHE_DIR
    new_cache = parent / NEW_CACHE_DIR

    stats: Dict[str, Any] = {
        "db": "skipped",
        "key": "skipped",
        "cache": "skipped",
        "moved": False,
    }

    # Fresh install or already migrated -> nothing to do.
    if not old_db.exists():
        return stats

    # Both legacy and new DB present -> ambiguous; refuse to guess.
    if new_db.exists():
        raise RuntimeError(
            f"Cannot migrate wellbeing files: both legacy '{old_db}' and new "
            f"'{new_db}' already exist. Back up both, keep only one, then start "
            f"the app to avoid data loss."
        )
    # A new key already present while the legacy DB still is -> never overwrite.
    if new_key.exists():
        raise RuntimeError(
            f"Cannot migrate wellbeing files: new key '{new_key}' already exists "
            f"while legacy DB '{old_db}' is still present. Refusing to overwrite "
            f"the key. Back up both, keep only one, then start the app."
        )

    # --- Fail fast on key integrity BEFORE moving anything -------------------
    if not old_key.exists():
        raise RuntimeError(
            f"Legacy wellbeing DB '{old_db}' exists but its key file "
            f"'{old_key}' is MISSING. Refusing to migrate: the database would "
            f"be undecryptable. Restore the key before starting the app."
        )
    try:
        _validate_fernet_key(old_key)
    except Exception as exc:
        raise RuntimeError(
            f"Legacy wellbeing key '{old_key}' could not be loaded as a valid "
            f"Fernet key ({exc!r}). Refusing to migrate to avoid data loss."
        ) from exc

    # --- Checkpoint WAL so the main .db file is self-contained ---------------
    _checkpoint_wal(old_db)

    # --- Move DB + key together (atomic-ish) --------------------------------
    # os.replace is atomic within the same filesystem (same parent directory).
    try:
        os.replace(str(old_db), str(new_db))
    except OSError as exc:
        raise RuntimeError(
            f"Failed to move legacy DB '{old_db}' -> '{new_db}': {exc}. If "
            f"another process has the database open, close it and retry."
        ) from exc
    try:
        os.replace(str(old_key), str(new_key))
    except BaseException:
        # Never split DB from key: restore the DB to its legacy name.
        try:
            os.replace(str(new_db), str(old_db))
        except OSError:
            logger.error(
                "CRITICAL: key move failed and DB restore FAILED. DB=%s KEY=%s",
                new_db, new_key,
            )
        raise

    # Move any leftover WAL/SHM sidecars (0-byte after checkpoint, but named
    # after the OLD db; SQLite looks for <new_db>-wal, so carry them over).
    # ACHTUNG: Ziel pflicht in parent zu liegen (rel. Pfade würden ins CWD wandern!).
    for suffix in ("-wal", "-shm"):
        side = parent / (LEGACY_DB_NAME + suffix)
        if side.exists():
            os.replace(str(side), str(parent / (NEW_DB_NAME + suffix)))

    stats["db"] = "moved"
    stats["key"] = "moved"

    # --- Cache dir (best-effort; a missing cache is normal on first run) ----
    if old_cache.exists():
        if new_cache.exists():
            logger.warning(
                "Both legacy and new KG cache dirs present; keeping both. "
                "Legacy=%s New=%s", old_cache, new_cache,
            )
            stats["cache"] = "conflict_kept"
        else:
            shutil.move(str(old_cache), str(new_cache))
            stats["cache"] = "moved"

    stats["moved"] = True
    logger.info(
        "Wellbeing-Artefakte umbenannt: db=%s key=%s cache=%s (dir=%s)",
        stats["db"], stats["key"], stats["cache"], parent,
    )
    return stats


__all__ = [
    "migrate_wellbeing_files",
    "LEGACY_DB_NAME",
    "LEGACY_KEY_NAME",
    "LEGACY_CACHE_DIR",
    "NEW_DB_NAME",
    "NEW_KEY_NAME",
    "NEW_CACHE_DIR",
]

