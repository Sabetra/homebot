"""Phase E (2026-09-01): Tests für die Datei-/Key-/Cache-Namen-Migration (E2).

Covers:
- Happy Path: Legacy DB + Key + Cache-Dir werden nach neutralen Namen
  verschoben (Daten + Schlüsselmaterial erhalten).
- WAL/SHM-Sidecars werden mitgenommen.
- Idempotenz (zweiter Lauf ist ein No-Op).
- Fresh-Install ist ein No-Op.
- Fehlender Legacy-Key -> RuntimeError (DB wäre unlesbar).
- Ungültiger (kein Fernet) Legacy-Key -> RuntimeError.
- Konflikt: Legacy- UND neue DB vorhanden -> RuntimeError.
- Konflikt: neue Key vorhanden, Legacy-DB noch da -> RuntimeError.
- Cache-Konflikt: beide Verzeichnisse bleiben erhalten (kein Datenverlust).
- Integration: WellbeingDatabase migriert die Legacy-Artefakte und entschlüsselt
  danach die Daten mit dem (verschobenen) Key — beweist, dass die Datei-Migration
  VOR der Key-Auflösung läuft.
"""
from __future__ import annotations

import base64
import sqlite3
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from wellbeing.file_migration import (
    LEGACY_CACHE_DIR,
    LEGACY_DB_NAME,
    LEGACY_KEY_NAME,
    NEW_CACHE_DIR,
    NEW_DB_NAME,
    NEW_KEY_NAME,
    migrate_wellbeing_files,
)


def _new_key() -> bytes:
    """Frischer Fernet-Key, damit Tests niemals produktiven Key-Material berühren."""
    return Fernet.generate_key()


def _make_legacy_store(
    dir_path: Path,
    key: bytes | None = None,
    with_key: bool = True,
    with_cache: bool = True,
    with_row: bool = True,
    with_wal_sidecars: bool = False,
) -> bytes:
    """Erzeugt Legacy-nannte Artefakte in ``dir_path`` und liefert den Key zurück."""
    key = key if key is not None else _new_key()
    cipher = Fernet(key)

    db = dir_path / LEGACY_DB_NAME
    conn = sqlite3.connect(db)
    # EXACTES reales Schema (wellbeing_db.py _init_schema), damit die DDL
    # (CREATE TABLE IF NOT EXISTS + Indexe auf user_id & Co.) sauber überspringt.
    conn.execute(
        """CREATE TABLE wellbeing_sessions (
               id TEXT PRIMARY KEY,
               user_id TEXT NOT NULL,
               start_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
               end_time TIMESTAMP NULL,
               session_summary TEXT NULL,
               mood_progression TEXT NULL,
               care_goals TEXT NULL,
               privacy_level INTEGER DEFAULT 1,
               anonymized INTEGER DEFAULT 1,
               created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
               updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
           )"""
    )
    if with_row:
        # Gleiche Codierung wie WellbeingDatabase._encrypt_data (Fernet -> base64url).
        token = base64.urlsafe_b64encode(cipher.encrypt(b"hallo welt")).decode()
        conn.execute(
            "INSERT INTO wellbeing_sessions (id, user_id, session_summary) "
            "VALUES ('s1', 'u1', ?)",
            (token,),
        )
    conn.commit()
    conn.close()

    if with_key:
        (dir_path / LEGACY_KEY_NAME).write_bytes(key)

    if with_cache:
        cache = dir_path / LEGACY_CACHE_DIR
        cache.mkdir()
        (cache / "index.faiss").write_bytes(b"FAKE-FAISS-CONTENT")

    if with_wal_sidecars:
        (dir_path / (LEGACY_DB_NAME + "-wal")).write_bytes(b"")
        (dir_path / (LEGACY_DB_NAME + "-shm")).write_bytes(b"")

    return key


def test_happy_path_moves_db_key_and_cache(tmp_path):
    key = _make_legacy_store(tmp_path)
    stats = migrate_wellbeing_files(tmp_path / NEW_DB_NAME)

    assert stats == {"db": "moved", "key": "moved", "cache": "moved", "moved": True}

    # Legacy-Artefakte sind verschwunden ...
    assert not (tmp_path / LEGACY_DB_NAME).exists()
    assert not (tmp_path / LEGACY_KEY_NAME).exists()
    assert not (tmp_path / LEGACY_CACHE_DIR).exists()
    # ... neue Namen sind da.
    assert (tmp_path / NEW_DB_NAME).exists()
    assert (tmp_path / NEW_KEY_NAME).exists()
    assert (tmp_path / NEW_CACHE_DIR).is_dir()

    # Key-Material wurde 1:1 übernommen (kein Neu-Generieren!).
    assert (tmp_path / NEW_KEY_NAME).read_bytes() == key

    # Daten sind intakt.
    conn = sqlite3.connect(tmp_path / NEW_DB_NAME)
    row = conn.execute(
        "SELECT session_summary FROM wellbeing_sessions WHERE id='s1'"
    ).fetchone()
    conn.close()
    assert row is not None and row[0]

    # Cache-Inhalt wanderte mit.
    assert (tmp_path / NEW_CACHE_DIR / "index.faiss").read_bytes() == b"FAKE-FAISS-CONTENT"


def test_wal_shm_sidecars_are_carried_over(tmp_path):
    _make_legacy_store(tmp_path, with_wal_sidecars=True)
    migrate_wellbeing_files(tmp_path / NEW_DB_NAME)

    # Legacy-Sidecars dürfen nicht zurückbleiben.
    assert not (tmp_path / (LEGACY_DB_NAME + "-wal")).exists()
    assert not (tmp_path / (LEGACY_DB_NAME + "-shm")).exists()
    # ... und wurden unter dem neuen Namen übernommen.
    assert (tmp_path / (NEW_DB_NAME + "-wal")).exists()
    assert (tmp_path / (NEW_DB_NAME + "-shm")).exists()
    # DB bleibt lesbar.
    conn = sqlite3.connect(tmp_path / NEW_DB_NAME)
    assert conn.execute("SELECT COUNT(*) FROM wellbeing_sessions").fetchone()[0] == 1
    conn.close()


def test_second_run_is_idempotent_noop(tmp_path):
    _make_legacy_store(tmp_path)
    first = migrate_wellbeing_files(tmp_path / NEW_DB_NAME)
    assert first["moved"] is True

    second = migrate_wellbeing_files(tmp_path / NEW_DB_NAME)
    assert second == {"db": "skipped", "key": "skipped", "cache": "skipped", "moved": False}

    # Zustand bleibt stabil.
    assert (tmp_path / NEW_DB_NAME).exists()
    assert (tmp_path / NEW_KEY_NAME).exists()
    assert (tmp_path / NEW_CACHE_DIR).is_dir()


def test_fresh_install_is_noop(tmp_path):
    stats = migrate_wellbeing_files(tmp_path / NEW_DB_NAME)

    assert stats == {"db": "skipped", "key": "skipped", "cache": "skipped", "moved": False}
    # Fresh Install: nichts darf erschaffen werden.
    assert not (tmp_path / NEW_DB_NAME).exists()
    assert not (tmp_path / NEW_KEY_NAME).exists()
    assert not (tmp_path / NEW_CACHE_DIR).exists()


def test_missing_legacy_key_raises_and_touches_nothing(tmp_path):
    _make_legacy_store(tmp_path, with_key=False)
    legacy_db = tmp_path / LEGACY_DB_NAME

    with pytest.raises(RuntimeError, match="MISSING"):
        migrate_wellbeing_files(tmp_path / NEW_DB_NAME)

    # Kein partieller Move: Legacy-DB steht noch, nichts Neues wurde angelegt.
    assert legacy_db.exists()
    assert not (tmp_path / NEW_DB_NAME).exists()
    assert not (tmp_path / NEW_KEY_NAME).exists()


def test_invalid_legacy_key_raises_and_touches_nothing(tmp_path):
    _make_legacy_store(tmp_path)
    # Key-Datei durch Müll ersetzen (kein gültiger Fernet-Token).
    (tmp_path / LEGACY_KEY_NAME).write_bytes(b"definitely-not-a-fernet-key")
    legacy_db = tmp_path / LEGACY_DB_NAME

    with pytest.raises(RuntimeError, match="Fernet"):
        migrate_wellbeing_files(tmp_path / NEW_DB_NAME)

    assert legacy_db.exists()
    assert (tmp_path / LEGACY_KEY_NAME).exists()
    assert not (tmp_path / NEW_DB_NAME).exists()


def test_conflict_both_dbs_present_raises(tmp_path):
    _make_legacy_store(tmp_path)
    # Neue DB "parallel" vorhanden -> zweideutig, Migration muss ablehnen.
    stray = tmp_path / NEW_DB_NAME
    conn = sqlite3.connect(stray)
    conn.execute("CREATE TABLE stray (id INTEGER PRIMARY KEY)")
    conn.commit()
    conn.close()

    with pytest.raises(RuntimeError, match="both legacy"):
        migrate_wellbeing_files(tmp_path / NEW_DB_NAME)

    # Beide DBs bleiben unangetastet.
    assert (tmp_path / LEGACY_DB_NAME).exists()
    assert stray.exists()
    assert not (tmp_path / NEW_KEY_NAME).exists()


def test_conflict_new_key_present_raises(tmp_path):
    _make_legacy_store(tmp_path)
    # Neue Key existiert bereits (Legacy-DB noch da) -> niemals überschreiben.
    (tmp_path / NEW_KEY_NAME).write_bytes(_new_key())

    with pytest.raises(RuntimeError, match="already exists"):
        migrate_wellbeing_files(tmp_path / NEW_DB_NAME)

    assert (tmp_path / LEGACY_DB_NAME).exists()
    assert (tmp_path / LEGACY_KEY_NAME).exists()
    assert not (tmp_path / NEW_DB_NAME).exists()


def test_cache_conflict_keeps_both_dirs(tmp_path):
    _make_legacy_store(tmp_path)
    # Neues Cache-Verzeichnis existiert bereits (z. B. halb migriert).
    new_cache = tmp_path / NEW_CACHE_DIR
    new_cache.mkdir()
    (new_cache / "index.faiss").write_bytes(b"NEW-CACHE")

    stats = migrate_wellbeing_files(tmp_path / NEW_DB_NAME)

    assert stats["db"] == "moved" and stats["key"] == "moved" and stats["moved"] is True
    assert stats["cache"] == "conflict_kept"
    # Beide Caches bleiben erhalten (kein Datenverlust, kein Überschreiben).
    assert (tmp_path / LEGACY_CACHE_DIR / "index.faiss").read_bytes() == b"FAKE-FAISS-CONTENT"
    assert (new_cache / "index.faiss").read_bytes() == b"NEW-CACHE"


def test_wellbeing_database_migrates_files_then_decrypts(tmp_path):
    """Integration: Legacy-Dateien im Zielfeld -> WellbeingDatabase verschoben
    DB+Key+Cache und kann die verschlüsselten Zeilen mit dem verschobenen Key
    lesen. Damit ist die Reihenfolge (Datei-Migration VOR Key-Auflösung) belegt."""
    from wellbeing.wellbeing_db import WellbeingDatabase

    _make_legacy_store(tmp_path)

    db = WellbeingDatabase(db_path=str(tmp_path / NEW_DB_NAME))
    try:
        # Artefakte migriert ...
        assert not (tmp_path / LEGACY_DB_NAME).exists()
        assert (tmp_path / NEW_DB_NAME).exists()
        assert (tmp_path / NEW_KEY_NAME).exists()
        assert (tmp_path / NEW_CACHE_DIR).is_dir()
        # ... und der verschobene Key ist der originale (Daten lesbar).
        conn = sqlite3.connect(tmp_path / NEW_DB_NAME)
        token = conn.execute(
            "SELECT session_summary FROM wellbeing_sessions WHERE id='s1'"
        ).fetchone()[0]
        conn.close()
        # _decrypt_data fällt bei Fehlschlag auf den Token zurück -> Plaintext-
        # Übereinstimmung beweist, dass der RICHTIGE Key geladen wurde.
        assert db._decrypt_data(token) == "hallo welt"
    finally:
        db.close()