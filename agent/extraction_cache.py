"""
Extraction Cache - Content-Addressable Caching für Multimodal RAG
==================================================================

State-of-the-Art Caching (2025):
- SHA256 für exakte Duplikat-Erkennung
- Perceptual Hashing (pHash) für ähnliche Bilder
- SQLite-Index für schnelle Lookups
- LRU-Eviction mit konfigurierbarer Max-Größe
- TTL (Time-to-Live) für automatisches Expiry
- Request-Deduplication für parallele Anfragen

Features:
- VisionCache: Cached Vision-LLM-Analysen von Bildern
- EmbeddingCache: Cached generierte Embeddings
- RequestDeduplicator: Verhindert doppelte Verarbeitung
"""

import os
import sqlite3
import hashlib
import json
import logging
import threading
import time
from pathlib import Path
from typing import Optional, Dict, Any, Callable, TypeVar, List, Tuple, Generator
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from contextlib import contextmanager
import tempfile
import shutil

logger = logging.getLogger(__name__)

T = TypeVar('T')


@dataclass
class CacheEntry:
    """Einzelner Cache-Eintrag"""
    key: str                    # SHA256 oder pHash
    content_hash: str           # SHA256 des Originalinhalts
    result: str                 # JSON-serialisiertes Ergebnis
    created_at: float           # Unix Timestamp
    last_accessed: float        # Für LRU
    access_count: int = 0       # Häufigkeit
    size_bytes: int = 0         # Größe des Ergebnisses
    source_type: str = "unknown"  # "image", "url", "pdf"
    metadata: str = "{}"        # Zusätzliche Metadaten als JSON


@dataclass
class CacheStats:
    """Cache-Statistiken"""
    total_entries: int = 0
    total_size_mb: float = 0.0
    hit_count: int = 0
    miss_count: int = 0
    hit_rate: float = 0.0
    oldest_entry_age_hours: float = 0.0
    most_accessed_key: str = ""
    eviction_count: int = 0


class VisionCache:
    """
    Content-Addressable Cache für Vision-LLM-Analysen.
    
    Verwendet SHA256 für exakte Duplikate und optional 
    Perceptual Hashing für ähnliche Bilder.
    """
    
    def __init__(
        self,
        cache_dir: Optional[Path] = None,
        max_size_mb: float = 500.0,
        ttl_hours: float = 168.0,  # 7 Tage
        enable_phash: bool = True,
        phash_threshold: int = 10  # Max Hamming-Distanz für Ähnlichkeit
    ):
        """
        Args:
            cache_dir: Verzeichnis für Cache-Dateien
            max_size_mb: Maximale Cache-Größe in MB
            ttl_hours: Time-to-Live in Stunden
            enable_phash: Perceptual Hashing aktivieren
            phash_threshold: Schwellwert für pHash-Ähnlichkeit
        """
        if cache_dir is None:
            cache_dir = Path(os.path.expanduser("~")) / ".cache" / "rag_vision_cache"
        
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        
        self.max_size_bytes = int(max_size_mb * 1024 * 1024)
        self.ttl_seconds = ttl_hours * 3600
        self.enable_phash = enable_phash
        self.phash_threshold = phash_threshold
        
        # SQLite für Index
        self.db_path = self.cache_dir / "cache_index.db"
        self._init_db()
        
        # Thread-Safety
        self._lock = threading.RLock()
        
        # Stats
        self._hit_count = 0
        self._miss_count = 0
        self._eviction_count = 0
        
        # pHash-Bibliothek (optional)
        self._imagehash = None
        if enable_phash:
            try:
                import imagehash
                self._imagehash = imagehash
                logger.info("✅ Perceptual Hashing (imagehash) aktiviert")
            except ImportError:
                logger.warning("⚠️ imagehash nicht installiert - nur SHA256-Caching")
                self.enable_phash = False
        
        logger.info(f"VisionCache initialized: {self.cache_dir} (max={max_size_mb}MB, ttl={ttl_hours}h)")
    
    def _init_db(self) -> None:
        """Initialisiert SQLite-Datenbank für Cache-Index"""
        with self._get_db() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS cache_entries (
                    key TEXT PRIMARY KEY,
                    content_hash TEXT NOT NULL,
                    result TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    last_accessed REAL NOT NULL,
                    access_count INTEGER DEFAULT 0,
                    size_bytes INTEGER DEFAULT 0,
                    source_type TEXT DEFAULT 'unknown',
                    metadata TEXT DEFAULT '{}'
                )
            """)
            
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_content_hash 
                ON cache_entries(content_hash)
            """)
            
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_last_accessed 
                ON cache_entries(last_accessed)
            """)
            
            # pHash-Tabelle für Ähnlichkeitssuche
            conn.execute("""
                CREATE TABLE IF NOT EXISTS phash_index (
                    content_hash TEXT PRIMARY KEY,
                    phash TEXT NOT NULL,
                    FOREIGN KEY (content_hash) REFERENCES cache_entries(content_hash)
                )
            """)
            
            conn.commit()
    
    @contextmanager
    def _get_db(self) -> Generator[sqlite3.Connection, None, None]:
        """Thread-sicherer Datenbankzugriff"""
        conn = sqlite3.connect(str(self.db_path), timeout=30.0)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()
    
    def _compute_sha256(self, data: bytes) -> str:
        """Berechnet SHA256-Hash"""
        return hashlib.sha256(data).hexdigest()
    
    def _compute_file_hash(self, file_path: str) -> str:
        """Berechnet SHA256 einer Datei"""
        sha256 = hashlib.sha256()
        with open(file_path, 'rb') as f:
            for chunk in iter(lambda: f.read(8192), b''):
                sha256.update(chunk)
        return sha256.hexdigest()
    
    def _compute_phash(self, image_path: str) -> Optional[str]:
        """Berechnet Perceptual Hash eines Bildes"""
        if not self._imagehash:
            return None
        
        try:
            from PIL import Image
            img = Image.open(image_path)
            phash = self._imagehash.phash(img)
            return str(phash)
        except Exception as e:
            logger.debug(f"pHash-Berechnung fehlgeschlagen: {e}")
            return None
    
    def _hamming_distance(self, hash1: str, hash2: str) -> int:
        """Berechnet Hamming-Distanz zwischen zwei Hex-Hashes"""
        try:
            # Hex zu Integer
            int1 = int(hash1, 16)
            int2 = int(hash2, 16)
            # XOR und Bits zählen
            xor_result = int1 ^ int2
            return bin(xor_result).count('1')
        except Exception:
            return 999  # Maximale Distanz bei Fehler
    
    def _find_similar_phash(self, phash: str) -> Optional[str]:
        """Sucht ähnlichen pHash im Index"""
        if not phash:
            return None
        
        with self._get_db() as conn:
            cursor = conn.execute("SELECT content_hash, phash FROM phash_index")
            
            for row in cursor:
                distance = self._hamming_distance(phash, row['phash'])
                if distance <= self.phash_threshold:
                    logger.debug(f"pHash-Match gefunden (Distanz={distance})")
                    return str(row['content_hash'])
        
        return None
    
    def get(self, key: str) -> Optional[Dict[str, Any]]:
        """
        Holt Eintrag aus Cache.
        
        Args:
            key: Cache-Key (normalerweise SHA256 des Bildes)
            
        Returns:
            Cached Result oder None
        """
        with self._lock:
            with self._get_db() as conn:
                cursor = conn.execute(
                    "SELECT * FROM cache_entries WHERE key = ?",
                    (key,)
                )
                row = cursor.fetchone()
                
                if row is None:
                    self._miss_count += 1
                    return None
                
                # TTL prüfen
                age = time.time() - row['created_at']
                if age > self.ttl_seconds:
                    # Abgelaufen - löschen
                    conn.execute("DELETE FROM cache_entries WHERE key = ?", (key,))
                    conn.commit()
                    self._miss_count += 1
                    logger.debug(f"Cache-Eintrag abgelaufen: {key[:16]}...")
                    return None
                
                # Access-Statistik aktualisieren
                conn.execute("""
                    UPDATE cache_entries 
                    SET last_accessed = ?, access_count = access_count + 1
                    WHERE key = ?
                """, (time.time(), key))
                conn.commit()
                
                self._hit_count += 1
                
                try:
                    result: Dict[str, Any] = json.loads(row['result'])
                    return result
                except json.JSONDecodeError:
                    return {'raw': row['result']}
    
    def get_by_content(self, content_hash: str) -> Optional[Dict[str, Any]]:
        """Sucht nach Content-Hash (für Deduplikation)"""
        with self._lock:
            with self._get_db() as conn:
                cursor = conn.execute(
                    "SELECT key FROM cache_entries WHERE content_hash = ?",
                    (content_hash,)
                )
                row = cursor.fetchone()
                
                if row:
                    return self.get(row['key'])
                
                return None
    
    def get_by_image(self, image_path: str) -> Optional[Dict[str, Any]]:
        """
        Sucht Cache-Eintrag für ein Bild.
        
        Verwendet SHA256 für exakte Matches und pHash für ähnliche Bilder.
        """
        # 1. Exakter Match via SHA256
        content_hash = self._compute_file_hash(image_path)
        result = self.get_by_content(content_hash)
        
        if result:
            logger.debug(f"Cache HIT (SHA256): {image_path}")
            return result
        
        # 2. Ähnlichkeitssuche via pHash
        if self.enable_phash:
            phash = self._compute_phash(image_path)
            if phash:
                similar_hash = self._find_similar_phash(phash)
                if similar_hash:
                    result = self.get_by_content(similar_hash)
                    if result:
                        logger.debug(f"Cache HIT (pHash): {image_path}")
                        return result
        
        logger.debug(f"Cache MISS: {image_path}")
        return None
    
    def put(
        self,
        key: str,
        result: Dict[str, Any],
        content_hash: Optional[str] = None,
        source_type: str = "unknown",
        metadata: Optional[Dict[str, Any]] = None,
        image_path: Optional[str] = None
    ) -> bool:
        """
        Speichert Eintrag im Cache.
        
        Args:
            key: Eindeutiger Schlüssel
            result: Zu cachendes Ergebnis
            content_hash: Optional, Hash des Originalinhalts
            source_type: "image", "url", "pdf"
            metadata: Zusätzliche Metadaten
            image_path: Für pHash-Berechnung
            
        Returns:
            True bei Erfolg
        """
        with self._lock:
            try:
                result_json = json.dumps(result, ensure_ascii=False)
                size_bytes = len(result_json.encode('utf-8'))
                
                if content_hash is None:
                    content_hash = hashlib.sha256(result_json.encode()).hexdigest()
                
                now = time.time()
                
                with self._get_db() as conn:
                    conn.execute("""
                        INSERT OR REPLACE INTO cache_entries 
                        (key, content_hash, result, created_at, last_accessed, 
                         access_count, size_bytes, source_type, metadata)
                        VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?)
                    """, (
                        key, content_hash, result_json, now, now,
                        size_bytes, source_type,
                        json.dumps(metadata or {})
                    ))
                    
                    # pHash speichern wenn verfügbar
                    if image_path and self.enable_phash:
                        phash = self._compute_phash(image_path)
                        if phash:
                            conn.execute("""
                                INSERT OR REPLACE INTO phash_index (content_hash, phash)
                                VALUES (?, ?)
                            """, (content_hash, phash))
                    
                    conn.commit()
                
                # Größe prüfen und ggf. evicten
                self._maybe_evict()
                
                logger.debug(f"Cache PUT: {key[:16]}... ({size_bytes} bytes)")
                return True
                
            except Exception as e:
                logger.error(f"Cache PUT fehlgeschlagen: {e}")
                return False
    
    def put_image_result(
        self,
        image_path: str,
        result: Dict[str, Any],
        metadata: Optional[Dict[str, Any]] = None
    ) -> bool:
        """Convenience-Methode für Bild-Caching"""
        content_hash = self._compute_file_hash(image_path)
        key = f"img_{content_hash}"
        
        return self.put(
            key=key,
            result=result,
            content_hash=content_hash,
            source_type="image",
            metadata=metadata,
            image_path=image_path
        )
    
    def get_or_compute(
        self,
        image_path: str,
        compute_fn: Callable[[], Dict[str, Any]],
        metadata: Optional[Dict[str, Any]] = None
    ) -> Tuple[Dict[str, Any], bool]:
        """
        Holt aus Cache oder berechnet und cached.
        
        Args:
            image_path: Pfad zum Bild
            compute_fn: Funktion zum Berechnen des Ergebnisses
            metadata: Zusätzliche Metadaten
            
        Returns:
            Tuple (result, was_cached)
        """
        # Cache-Check
        cached = self.get_by_image(image_path)
        if cached is not None:
            return cached, True
        
        # Berechnen
        result = compute_fn()
        
        # Cachen
        self.put_image_result(image_path, result, metadata)
        
        return result, False
    
    def _maybe_evict(self) -> None:
        """Entfernt alte Einträge wenn Cache zu groß"""
        with self._get_db() as conn:
            # Aktuelle Größe berechnen
            cursor = conn.execute("SELECT SUM(size_bytes) as total FROM cache_entries")
            row = cursor.fetchone()
            total_size = row['total'] or 0
            
            if total_size <= self.max_size_bytes:
                return
            
            # LRU-Eviction: Älteste zuerst löschen
            target_size = int(self.max_size_bytes * 0.8)  # Auf 80% reduzieren
            
            cursor = conn.execute("""
                SELECT key, size_bytes FROM cache_entries 
                ORDER BY last_accessed ASC
            """)
            
            to_delete = []
            freed = 0
            
            for row in cursor:
                if total_size - freed <= target_size:
                    break
                to_delete.append(row['key'])
                freed += row['size_bytes']
            
            if to_delete:
                placeholders = ','.join(['?' for _ in to_delete])
                conn.execute(f"DELETE FROM cache_entries WHERE key IN ({placeholders})", to_delete)
                conn.commit()
                self._eviction_count += len(to_delete)
                logger.info(f"Cache-Eviction: {len(to_delete)} Einträge gelöscht ({freed / (1024*1024):.2f} MB)")
    
    def clear(self) -> None:
        """Leert den gesamten Cache"""
        with self._lock:
            with self._get_db() as conn:
                conn.execute("DELETE FROM cache_entries")
                conn.execute("DELETE FROM phash_index")
                conn.commit()
            logger.info("Cache geleert")
    
    def get_stats(self) -> CacheStats:
        """Gibt Cache-Statistiken zurück"""
        with self._get_db() as conn:
            cursor = conn.execute("""
                SELECT 
                    COUNT(*) as count,
                    SUM(size_bytes) as total_size,
                    MIN(created_at) as oldest,
                    MAX(access_count) as max_access
                FROM cache_entries
            """)
            row = cursor.fetchone()
            
            # Meistgenutzter Eintrag
            cursor = conn.execute("""
                SELECT key FROM cache_entries 
                ORDER BY access_count DESC LIMIT 1
            """)
            most_accessed_row = cursor.fetchone()
            
            total_requests = self._hit_count + self._miss_count
            
            return CacheStats(
                total_entries=row['count'] or 0,
                total_size_mb=(row['total_size'] or 0) / (1024 * 1024),
                hit_count=self._hit_count,
                miss_count=self._miss_count,
                hit_rate=self._hit_count / total_requests if total_requests > 0 else 0.0,
                oldest_entry_age_hours=(time.time() - (row['oldest'] or time.time())) / 3600,
                most_accessed_key=most_accessed_row['key'] if most_accessed_row else "",
                eviction_count=self._eviction_count
            )


class EmbeddingCache:
    """
    Cache für generierte Embeddings.
    
    Speichert Embeddings als numpy-Arrays mit effizientem Zugriff.
    """
    
    def __init__(
        self,
        cache_dir: Optional[Path] = None,
        max_entries: int = 100000
    ):
        if cache_dir is None:
            cache_dir = Path(os.path.expanduser("~")) / ".cache" / "rag_embedding_cache"
        
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        
        self.max_entries = max_entries
        self.db_path = self.cache_dir / "embedding_index.db"
        self._init_db()
        self._lock = threading.RLock()
        
        logger.info(f"EmbeddingCache initialized: {self.cache_dir}")
    
    def _init_db(self) -> None:
        """Initialisiert Embedding-Index"""
        conn = sqlite3.connect(str(self.db_path))
        conn.execute("""
            CREATE TABLE IF NOT EXISTS embeddings (
                text_hash TEXT PRIMARY KEY,
                model_name TEXT NOT NULL,
                embedding_file TEXT NOT NULL,
                dimension INTEGER NOT NULL,
                created_at REAL NOT NULL
            )
        """)
        conn.commit()
        conn.close()
    
    def _text_hash(self, text: str, model_name: str) -> str:
        """Hash für Text + Model"""
        combined = f"{model_name}:{text}"
        return hashlib.sha256(combined.encode()).hexdigest()
    
    def get(self, text: str, model_name: str) -> Optional[List[float]]:
        """Holt Embedding aus Cache"""
        text_hash = self._text_hash(text, model_name)
        
        with self._lock:
            conn = sqlite3.connect(str(self.db_path))
            cursor = conn.execute(
                "SELECT embedding_file FROM embeddings WHERE text_hash = ?",
                (text_hash,)
            )
            row = cursor.fetchone()
            conn.close()
            
            if row is None:
                return None
            
            # Embedding laden
            embedding_path = self.cache_dir / row[0]
            if not embedding_path.exists():
                return None
            
            try:
                import numpy as np
                embedding = np.load(str(embedding_path))
                return list(embedding.tolist())
            except Exception:
                return None
    
    def put(self, text: str, model_name: str, embedding: List[float]) -> bool:
        """Speichert Embedding im Cache"""
        text_hash = self._text_hash(text, model_name)
        
        try:
            import numpy as np
            
            # Als .npy speichern
            embedding_file = f"{text_hash}.npy"
            embedding_path = self.cache_dir / embedding_file
            np.save(str(embedding_path), np.array(embedding))
            
            with self._lock:
                conn = sqlite3.connect(str(self.db_path))
                conn.execute("""
                    INSERT OR REPLACE INTO embeddings 
                    (text_hash, model_name, embedding_file, dimension, created_at)
                    VALUES (?, ?, ?, ?, ?)
                """, (text_hash, model_name, embedding_file, len(embedding), time.time()))
                conn.commit()
                conn.close()
            
            return True
        except Exception as e:
            logger.error(f"Embedding-Cache PUT fehlgeschlagen: {e}")
            return False
    
    def get_or_compute(
        self,
        text: str,
        model_name: str,
        compute_fn: Callable[[], List[float]]
    ) -> Tuple[List[float], bool]:
        """Holt aus Cache oder berechnet"""
        cached = self.get(text, model_name)
        if cached is not None:
            return cached, True
        
        embedding = compute_fn()
        self.put(text, model_name, embedding)
        return embedding, False


class RequestDeduplicator:
    """
    Verhindert doppelte Verarbeitung paralleler Anfragen.
    
    Wenn mehrere Threads das gleiche Bild/URL gleichzeitig verarbeiten wollen,
    wartet der zweite auf den ersten.
    """
    
    def __init__(self) -> None:
        self._in_progress: Dict[str, threading.Event] = {}
        self._results: Dict[str, Any] = {}
        self._lock = threading.Lock()
    
    def get_or_wait(self, key: str) -> Tuple[Optional[Any], bool]:
        """
        Prüft ob Anfrage bereits läuft.
        
        Returns:
            Tuple (result_or_none, should_compute)
            - (result, False): Ergebnis von anderem Thread
            - (None, True): Selbst berechnen
        """
        with self._lock:
            if key in self._results:
                # Bereits fertig
                return self._results[key], False
            
            if key in self._in_progress:
                # Läuft gerade - warten
                event = self._in_progress[key]
        
            else:
                # Neu starten
                self._in_progress[key] = threading.Event()
                return None, True
        
        # Außerhalb des Locks warten
        event.wait(timeout=300)  # Max 5 Minuten
        
        with self._lock:
            return self._results.get(key), False
    
    def set_result(self, key: str, result: Any) -> None:
        """Setzt Ergebnis und signalisiert wartende Threads"""
        with self._lock:
            self._results[key] = result
            
            if key in self._in_progress:
                self._in_progress[key].set()
                del self._in_progress[key]
    
    def clear(self, key: Optional[str] = None) -> None:
        """Entfernt Ergebnis(se)"""
        with self._lock:
            if key:
                self._results.pop(key, None)
            else:
                self._results.clear()


# Singleton-Instanzen
_vision_cache: Optional[VisionCache] = None
_embedding_cache: Optional[EmbeddingCache] = None
_request_dedup: Optional[RequestDeduplicator] = None


def get_vision_cache() -> VisionCache:
    """Gibt Singleton VisionCache zurück"""
    global _vision_cache
    if _vision_cache is None:
        _vision_cache = VisionCache()
    return _vision_cache


def get_embedding_cache() -> EmbeddingCache:
    """Gibt Singleton EmbeddingCache zurück"""
    global _embedding_cache
    if _embedding_cache is None:
        _embedding_cache = EmbeddingCache()
    return _embedding_cache


def get_request_deduplicator() -> RequestDeduplicator:
    """Gibt Singleton RequestDeduplicator zurück"""
    global _request_dedup
    if _request_dedup is None:
        _request_dedup = RequestDeduplicator()
    return _request_dedup
