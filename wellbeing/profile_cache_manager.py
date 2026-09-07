#!/usr/bin/env python3
"""
PSYCHOLOGICAL PROFILE CACHE MANAGER
====================================

Intelligent caching layer for psychological profiles with:
- In-memory cache (fast access)
- DB-backed persistence (survival across restarts)
- TTL-based expiration
- Smart invalidation triggers
- LRU eviction for memory management

Features:
- Cache hit rate: 90%+ target
- Cache miss: automatic regeneration
- Invalidation: event-driven (new KG triples, session end, etc.)
- Thread-safe operations
"""

import logging
import sqlite3
import threading
import time
from datetime import datetime, timezone, timedelta
from typing import Dict, Optional, Any
import json

logger = logging.getLogger(__name__)


class ProfileCacheManager:
    """
    Intelligent caching layer for psychological profiles
    
    Features:
    - In-memory LRU cache (max 100 profiles)
    - DB-backed persistence
    - TTL: 30 minutes default
    - Event-driven invalidation
    - Thread-safe
    """
    
    def __init__(self, wellbeing_db: Any, profile_synthesizer: Any,
                 ttl_minutes: int = 30,
                 max_cache_size: int = 100) -> None:
        """
        Initialize profile cache manager
        
        Args:
            wellbeing_db: WellbeingDatabase instance
            profile_synthesizer: ProfileSynthesizer instance
            ttl_minutes: Cache TTL in minutes
            max_cache_size: Maximum number of profiles in memory cache
        """
        self.db = wellbeing_db
        self.synthesizer = profile_synthesizer
        self.ttl_minutes = ttl_minutes
        self.max_cache_size = max_cache_size
        
        # In-memory cache: {user_id: (profile, expiry_time)}
        self._cache: Dict[str, tuple] = {}
        self._cache_lock = threading.RLock()
        self._user_locks: Dict[str, threading.Lock] = {}
        
        # LRU tracking: {user_id: last_access_time}
        self._access_times: Dict[str, datetime] = {}
        
        # Cache statistics
        self._stats = {
            'hits': 0,
            'misses': 0,
            'evictions': 0,
            'regenerations': 0
        }
        
        # Initialize DB schema
        self._init_db_schema()
        
        logger.info(f"✅ ProfileCacheManager initialized (TTL: {ttl_minutes}min, max_size: {max_cache_size})")
    
    # ── Dual-Cadence Constants ─────────────────────────────
    DELTA_COUNT_BEFORE_FULL = 10      # After 10 delta merges → force full re-synthesis
    MAX_HISTORY_VERSIONS = 20         # Keep last 20 profile versions in history
    
    def _init_db_schema(self) -> None:
        """Initialize database schema for profile caching"""
        try:
            with self.db.get_connection() as conn:
                # Main psychological profiles cache table
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS wellbeing_profiles (
                        user_id TEXT PRIMARY KEY,
                        profile_version INTEGER NOT NULL,
                        profile_data TEXT NOT NULL,
                        synthesis_prompt_hash TEXT,
                        synthesis_model TEXT,
                        confidence_score REAL,
                        source_kg_count INTEGER,
                        source_session_count INTEGER,
                        source_insight_count INTEGER,
                        created_at TIMESTAMP NOT NULL,
                        updated_at TIMESTAMP NOT NULL,
                        expires_at TIMESTAMP,
                        synthesis_type TEXT DEFAULT 'full',
                        delta_count_since_full INTEGER DEFAULT 0,
                        last_synthesis_at TIMESTAMP
                    )
                """)
                
                # ── Profile version history (keeps N most recent versions per user) ──
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS profile_history (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        user_id TEXT NOT NULL,
                        profile_version INTEGER NOT NULL,
                        profile_data TEXT NOT NULL,
                        synthesis_type TEXT NOT NULL,
                        confidence_score REAL,
                        created_at TIMESTAMP NOT NULL,
                        UNIQUE(user_id, profile_version)
                    )
                """)
                
                # Profile invalidation log
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS profile_invalidation_log (
                        id TEXT PRIMARY KEY,
                        user_id TEXT NOT NULL,
                        trigger_type TEXT NOT NULL,
                        trigger_source_id TEXT,
                        triggered_at TIMESTAMP NOT NULL,
                        profile_regenerated BOOLEAN DEFAULT FALSE
                    )
                """)
                
                # Indices
                conn.execute("CREATE INDEX IF NOT EXISTS idx_profiles_user ON wellbeing_profiles(user_id)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_profiles_expires ON wellbeing_profiles(expires_at)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_invalidation_user ON profile_invalidation_log(user_id, triggered_at)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_history_user ON profile_history(user_id, profile_version)")
                
                # ── Schema migration: add new columns if they don't exist yet ──
                self._migrate_schema(conn)
                
                logger.info("✅ Profile cache DB schema initialized")
                
        except Exception as e:
            logger.error(f"❌ DB schema initialization failed: {e}")
            raise RuntimeError("Profile cache DB schema initialization failed") from e
    
    def _migrate_schema(self, conn: Any) -> None:
        """Add missing columns to existing tables deterministically."""
        new_columns = [
            ("wellbeing_profiles", "synthesis_type", "TEXT DEFAULT 'full'"),
            ("wellbeing_profiles", "delta_count_since_full", "INTEGER DEFAULT 0"),
            ("wellbeing_profiles", "last_synthesis_at", "TIMESTAMP"),
        ]

        existing_cols = {
            str(row[1])
            for row in conn.execute("PRAGMA table_info(wellbeing_profiles)").fetchall()
        }

        for table, column, col_type in new_columns:
            if column in existing_cols:
                continue
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")
            logger.info(f"✅ Added column {column} to {table}")
    
    def get_cached_profile(self, user_id: str, force_regenerate: bool = False) -> Any:
        """
        Get cached profile or regenerate via dual-cadence synthesis.
        
        Flow:
        1. Memory cache hit → return immediately
        2. DB cache valid → load into memory, return
        3. Cache miss or expired →
           a. Load previous profile from DB (even if expired)
           b. Decide: delta-merge or full re-synthesis
           c. Synthesize, save with version history, cache
        
        Returns:
            WellbeingProfile or None
        """
        user_lock = self._get_user_lock(user_id)
        with user_lock:
            # 1. CHECK IN-MEMORY CACHE
            if not force_regenerate:
                with self._cache_lock:
                    if user_id in self._cache:
                        profile, expiry_time = self._cache[user_id]
                        if datetime.now(timezone.utc) < expiry_time:
                            self._stats['hits'] += 1
                            self._access_times[user_id] = datetime.now(timezone.utc)
                            logger.debug(f"✅ Cache HIT for user {user_id[:10]}...")
                            return profile
                        logger.debug(f"⏰ Cache EXPIRED for user {user_id[:10]}...")
                        del self._cache[user_id]

            # 2. CHECK DB CACHE (unexpired)
            if not force_regenerate:
                db_row = self._load_from_db(user_id)
                if db_row:
                    expires_at_str = db_row.get('_db_expires_at')
                    try:
                        if expires_at_str:
                            expires_at = datetime.fromisoformat(expires_at_str.replace('Z', '+00:00'))
                            if datetime.now(timezone.utc) < expires_at:
                                profile = self._deserialize_profile(db_row)
                                with self._cache_lock:
                                    self._add_to_memory_cache(user_id, profile)
                                    self._stats['hits'] += 1
                                logger.debug(f"✅ Cache HIT (DB) for user {user_id[:10]}...")
                                return profile
                    except Exception as e:
                        logger.warning(f"⚠️ Failed to parse expires_at: {e}")

            # 3. CACHE MISS — REGENERATE via dual-cadence
            with self._cache_lock:
                self._stats['misses'] += 1

            if self.synthesizer is None:
                logger.info(
                    f"ℹ️ Cannot regenerate profile for {user_id[:10]}... "
                    f"— ProfileSynthesizer not yet initialized (model_loader pending)"
                )
                return None

            with self._cache_lock:
                self._stats['regenerations'] += 1

            # 3a. Load previous profile from DB (even expired — we need it for delta-merge)
            previous_profile = None
            delta_count = 0
            db_row = self._load_from_db(user_id)
            if db_row:
                previous_profile = self._deserialize_profile(db_row)
                delta_count = db_row.get('_db_delta_count_since_full', 0) or 0

            # 3b. Decide: full or delta
            if force_regenerate or previous_profile is None or self._should_full_resynthesize(delta_count):
                synthesis_type = 'full'
                new_delta_count = 0
                logger.info(f"🔄 Full re-synthesis for user {user_id[:10]}... (delta_count={delta_count})")
            else:
                synthesis_type = 'delta'
                new_delta_count = delta_count + 1
                logger.info(f"🔄 Delta merge (#{new_delta_count}) for user {user_id[:10]}...")

            # 3c. Synthesize
            profile = self.synthesizer.synthesize_profile(
                user_id,
                force_regenerate=True,
                previous_profile=previous_profile if synthesis_type == 'delta' else None,
                synthesis_type=synthesis_type,
            )

            if not profile:
                logger.warning(f"⚠️ Failed to synthesize profile for user {user_id}")
                return None

            # 4. SAVE WITH VERSION HISTORY
            self._save_to_db(user_id, profile, delta_count_since_full=new_delta_count)
            with self._cache_lock:
                self._add_to_memory_cache(user_id, profile)

            return profile

    def _get_user_lock(self, user_id: str) -> threading.Lock:
        """Get or create a per-user lock to avoid duplicate parallel regeneration."""
        with self._cache_lock:
            lock = self._user_locks.get(user_id)
            if lock is None:
                lock = threading.Lock()
                self._user_locks[user_id] = lock
            return lock

    def purge_user_memory(self, user_id: str) -> None:
        """Remove all in-memory cache and lock state for a deleted user."""
        with self._cache_lock:
            self._cache.pop(user_id, None)
            self._access_times.pop(user_id, None)
            self._user_locks.pop(user_id, None)
    
    def _should_full_resynthesize(self, delta_count: int) -> bool:
        """Determine whether a full re-synthesis from raw data is needed.
        
        This is the 'periodic review' a therapist does — stepping back from incremental
        notes to rebuild their full understanding from the original data.
        """
        return delta_count >= self.DELTA_COUNT_BEFORE_FULL
    
    def invalidate_profile(self, user_id: str, trigger_type: str, trigger_source_id: Optional[str] = None) -> None:
        """
        Soft-invalidate cached profile.
        
        IMPORTANT: Does NOT delete the DB row — the profile data is preserved for
        delta-merge on the next synthesis. Only the TTL is set to NOW so the next
        `get_cached_profile()` call triggers a re-synthesis.
        
        A therapist doesn't throw away their notes when they receive new information.
        They mark their current understanding as "needs update" and incorporate the
        new data during the next review.
        """
        with self._cache_lock:
            # Remove from memory cache (force DB lookup on next access)
            if user_id in self._cache:
                del self._cache[user_id]
                logger.info(f"🗑️ Profile invalidated (memory) for user {user_id[:10]}... (trigger: {trigger_type})")
            
            # Soft-expire DB row: set expires_at = NOW (but KEEP the data for delta-merge)
            now = datetime.now(timezone.utc).isoformat()
            max_attempts = 4

            for attempt in range(1, max_attempts + 1):
                try:
                    with self.db.get_connection() as conn:
                        self._invalidate_profile_db(
                            conn=conn,
                            user_id=user_id,
                            trigger_type=trigger_type,
                            trigger_source_id=trigger_source_id,
                            now=now,
                        )

                        conn.commit()

                    logger.info(
                        f"⏰ Profile soft-expired (DB) for user {user_id[:10]}... "
                        f"(trigger: {trigger_type}, attempt={attempt})"
                    )
                    return

                except sqlite3.OperationalError as e:
                    error_text = str(e).lower()
                    if "database is locked" not in error_text and "database table is locked" not in error_text:
                        logger.error(f"❌ DB invalidation failed: {e}")
                        return

                    if attempt >= max_attempts:
                        logger.warning(
                            f"⚠️ DB invalidation skipped after {max_attempts} attempts due to lock: {e}"
                        )
                        return

                    sleep_s = 0.15 * attempt
                    logger.warning(
                        f"⚠️ DB invalidation lock on attempt {attempt}/{max_attempts}; retry in {sleep_s:.2f}s"
                    )
                    time.sleep(sleep_s)

                except Exception as e:
                    logger.error(f"❌ DB invalidation failed: {e}")
                    return

    def invalidate_profile_in_transaction(
        self,
        user_id: str,
        trigger_type: str,
        trigger_source_id: Optional[str],
        conn: Any,
    ) -> None:
        """Invalidate a profile using an already-open DB transaction."""
        with self._cache_lock:
            if user_id in self._cache:
                del self._cache[user_id]
                logger.info(f"🗑️ Profile invalidated (memory) for user {user_id[:10]}... (trigger: {trigger_type})")

            now = datetime.now(timezone.utc).isoformat()
            self._invalidate_profile_db(
                conn=conn,
                user_id=user_id,
                trigger_type=trigger_type,
                trigger_source_id=trigger_source_id,
                now=now,
            )
            logger.info(
                f"⏰ Profile soft-expired (DB/in-tx) for user {user_id[:10]}... "
                f"(trigger: {trigger_type})"
            )

    def _invalidate_profile_db(
        self,
        conn: Any,
        user_id: str,
        trigger_type: str,
        trigger_source_id: Optional[str],
        now: str,
    ) -> None:
        """Apply the DB invalidation statements on an existing connection."""
        conn.execute(
            "UPDATE wellbeing_profiles SET expires_at = ? WHERE user_id = ?",
            (now, user_id)
        )

        import uuid
        conn.execute("""
            INSERT INTO profile_invalidation_log (id, user_id, trigger_type, trigger_source_id, triggered_at)
            VALUES (?, ?, ?, ?, ?)
        """, (str(uuid.uuid4()), user_id, trigger_type, trigger_source_id, now))
    
    def _add_to_memory_cache(self, user_id: str, profile: Any) -> None:
        """Add profile to memory cache with TTL"""
        # Evict if cache is full
        if len(self._cache) >= self.max_cache_size:
            self._evict_lru()
        
        expiry_time = datetime.now(timezone.utc) + timedelta(minutes=self.ttl_minutes)
        self._cache[user_id] = (profile, expiry_time)
        self._access_times[user_id] = datetime.now(timezone.utc)
        
        logger.debug(f"💾 Added to memory cache: {user_id[:10]}... (expires: {expiry_time.isoformat()})")
    
    def _evict_lru(self) -> None:
        """Evict least recently used profile from memory cache"""
        if not self._access_times:
            return
        
        # Find LRU user
        lru_user = min(self._access_times.keys(), key=lambda k: self._access_times[k])
        
        # Evict
        if lru_user in self._cache:
            del self._cache[lru_user]
        del self._access_times[lru_user]
        
        self._stats['evictions'] += 1
        logger.debug(f"🗑️ Evicted LRU profile: {lru_user[:10]}...")
    
    def _save_to_db(self, user_id: str, profile: Any,
                    delta_count_since_full: int = 0) -> None:
        """Save profile to database with version history.
        
        1. Archive the PREVIOUS profile version to profile_history (if it exists)
        2. Overwrite the current row in wellbeing_profiles
        3. Prune old history entries beyond MAX_HISTORY_VERSIONS
        """
        try:
            profile_dict = profile.to_dict()
            profile_json = json.dumps(profile_dict)
            encrypted_data = self.db._encrypt_data(profile_json)
            
            expires_at = datetime.now(timezone.utc) + timedelta(minutes=self.ttl_minutes)
            now = datetime.now(timezone.utc).isoformat()
            
            with self.db.get_connection() as conn:
                # ── Step 1: Archive previous version to profile_history ──
                previous = conn.execute(
                    "SELECT profile_version, profile_data, synthesis_type, confidence_score, created_at "
                    "FROM wellbeing_profiles WHERE user_id = ?",
                    (user_id,)
                ).fetchone()
                
                if previous:
                    try:
                        conn.execute("""
                            INSERT OR IGNORE INTO profile_history
                                (user_id, profile_version, profile_data, synthesis_type, confidence_score, created_at)
                            VALUES (?, ?, ?, ?, ?, ?)
                        """, (
                            user_id,
                            previous['profile_version'],
                            previous['profile_data'],
                            previous['synthesis_type'] or 'full',
                            previous['confidence_score'],
                            previous['created_at'],
                        ))
                    except Exception as e:
                        logger.warning(f"⚠️ Could not archive previous profile version: {e}")
                    
                    # ── Step 1b: Prune old history ──
                    try:
                        conn.execute("""
                            DELETE FROM profile_history
                            WHERE user_id = ? AND id NOT IN (
                                SELECT id FROM profile_history
                                WHERE user_id = ?
                                ORDER BY profile_version DESC
                                LIMIT ?
                            )
                        """, (user_id, user_id, self.MAX_HISTORY_VERSIONS))
                    except Exception as e:
                        logger.warning(f"⚠️ History pruning failed: {e}")
                
                # ── Step 2: Upsert current profile ──
                conn.execute("""
                    INSERT OR REPLACE INTO wellbeing_profiles (
                        user_id, profile_version, profile_data, synthesis_prompt_hash,
                        synthesis_model, confidence_score, source_kg_count,
                        source_session_count, source_insight_count, created_at,
                        updated_at, expires_at, synthesis_type,
                        delta_count_since_full, last_synthesis_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    user_id,
                    profile.version,
                    encrypted_data,
                    profile.synthesis_prompt_hash,
                    profile.synthesis_model,
                    profile.overall_confidence,
                    profile.data_sources.get('kg_triples_used', 0),
                    profile.data_sources.get('sessions_used', 0),
                    profile.data_sources.get('insights_used', 0),
                    profile.created_at,
                    profile.updated_at,
                    expires_at.isoformat(),
                    profile.synthesis_type,
                    delta_count_since_full,
                    now,
                ))
            
            logger.debug(
                f"💾 Saved to DB: {user_id[:10]}... "
                f"v{profile.version} ({profile.synthesis_type}, delta_count={delta_count_since_full})"
            )
            
        except Exception as e:
            logger.error(f"❌ DB save failed: {e}")
            raise RuntimeError(f"DB save failed for user {user_id}") from e
    
    def _load_from_db(self, user_id: str) -> Optional[Dict[str, Any]]:
        """Load profile from database (including metadata columns)."""
        try:
            with self.db.get_connection() as conn:
                cursor = conn.execute("""
                    SELECT profile_data, expires_at, delta_count_since_full, last_synthesis_at
                    FROM wellbeing_profiles
                    WHERE user_id = ?
                """, (user_id,))
                
                row = cursor.fetchone()
                if not row:
                    return None
                
                # Decrypt profile data
                decrypted_data = self.db._decrypt_data(row['profile_data'])
                profile_dict_raw: Any = json.loads(decrypted_data)
                
                if not isinstance(profile_dict_raw, dict):
                    logger.warning(f"⚠️ Loaded profile is not a dict: {type(profile_dict_raw)}")
                    return None
                
                profile_dict: Dict[str, Any] = profile_dict_raw
                # Attach DB-level metadata (not part of the WellbeingProfile dataclass)
                profile_dict['_db_expires_at'] = row['expires_at']
                profile_dict['_db_delta_count_since_full'] = row['delta_count_since_full']
                profile_dict['_db_last_synthesis_at'] = row['last_synthesis_at']
                
                return profile_dict
                
        except Exception as e:
            logger.error(f"❌ DB load failed: {e}")
            raise RuntimeError(f"DB load failed for user {user_id}") from e
    
    def _deserialize_profile(self, profile_dict: Dict[str, Any]) -> Any:
        """Deserialize profile from dictionary, stripping non-dataclass keys."""
        from wellbeing.profile_synthesizer import WellbeingProfile
        # Remove DB-level metadata keys that are NOT part of the dataclass
        clean = {k: v for k, v in profile_dict.items() if not k.startswith('_db_')}
        clean.pop('expires_at', None)  # Legacy key from old schema
        return WellbeingProfile.from_dict(clean)
    
    def get_cache_stats(self) -> Dict[str, Any]:
        """Get cache statistics"""
        with self._cache_lock:
            total_requests = self._stats['hits'] + self._stats['misses']
            hit_rate = (self._stats['hits'] / total_requests * 100) if total_requests > 0 else 0
            
            return {
                'cache_size': len(self._cache),
                'max_cache_size': self.max_cache_size,
                'hits': self._stats['hits'],
                'misses': self._stats['misses'],
                'hit_rate': f"{hit_rate:.2f}%",
                'evictions': self._stats['evictions'],
                'regenerations': self._stats['regenerations']
            }
    
    def clear_cache(self, user_id: Optional[str] = None) -> None:
        """Clear cache (all or specific user)"""
        with self._cache_lock:
            if user_id:
                if user_id in self._cache:
                    del self._cache[user_id]
                if user_id in self._access_times:
                    del self._access_times[user_id]
                self._user_locks.pop(user_id, None)
                logger.info(f"🗑️ Cleared cache for user {user_id[:10]}...")
            else:
                self._cache.clear()
                self._access_times.clear()
                self._user_locks.clear()
                logger.info("🗑️ Cleared entire cache")


# Factory function
def create_profile_cache_manager(wellbeing_db: Any, profile_synthesizer: Any, ttl_minutes: int = 30, max_cache_size: int = 100) -> "ProfileCacheManager":
    """Create ProfileCacheManager instance"""
    return ProfileCacheManager(wellbeing_db, profile_synthesizer, ttl_minutes, max_cache_size)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    
    print("💾 Psychological Profile Cache Manager")
    print("=" * 60)
    print("✅ Intelligent caching for psychological profiles")
    print("📋 Features:")
    print("   • In-memory LRU cache")
    print("   • DB-backed persistence")
    print("   • TTL-based expiration")
    print("   • Smart invalidation triggers")
    print("   • Thread-safe operations")
