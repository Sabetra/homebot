"""
P3-2: CHANGE DETECTOR – RAG Quality Pipeline Component

Detects changes to source documents (PDFs, Docs, Finance DB, Web) using:
- File watching via watchdog
- SHA-256 hash-based change detection
- Event-driven reindexing triggers
- Support for addition, modification, and deletion of sources

SOTA Features:
- Async-compatible with asyncio event loop
- Batch change detection (debouncing rapid changes)
- Configurable watch directories
- Hash cache persistence (survives restarts)
- Integration with async_startup_service.py

Author: SOTA RAG Quality Pipeline
Date: 2026-06-24
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set

try:
    from watchdog.events import FileSystemEvent, FileSystemEventHandler
    from watchdog.observers import Observer
    WATCHDOG_AVAILABLE = True
except ImportError:
    WATCHDOG_AVAILABLE = False
    FileSystemEventHandler = None  # type: ignore
    Observer = None  # type: ignore

logger = logging.getLogger(__name__)

# ====================================================================
# DATA MODELS
# ====================================================================

@dataclass
class DocumentFingerprint:
    """Immutable fingerprint for a document."""
    file_path: str
    sha256_hash: str
    size_bytes: int
    modified_time: float
    created_time: float
    last_scanned: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "file_path": self.file_path,
            "sha256_hash": self.sha256_hash,
            "size_bytes": self.size_bytes,
            "modified_time": self.modified_time,
            "created_time": self.created_time,
            "last_scanned": self.last_scanned,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> DocumentFingerprint:
        return cls(
            file_path=data["file_path"],
            sha256_hash=data["sha256_hash"],
            size_bytes=data["size_bytes"],
            modified_time=data["modified_time"],
            created_time=data["created_time"],
            last_scanned=data.get("last_scanned", time.time()),
        )


@dataclass
class ChangeEvent:
    """Represents a detected change."""
    event_id: str
    change_type: str  # "added", "modified", "deleted"
    file_path: str
    old_hash: Optional[str] = None
    new_hash: Optional[str] = None
    timestamp: float = field(default_factory=time.time)
    processed: bool = False
    trigger_reindex: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_id": self.event_id,
            "change_type": self.change_type,
            "file_path": self.file_path,
            "old_hash": self.old_hash,
            "new_hash": self.new_hash,
            "timestamp": self.timestamp,
            "processed": self.processed,
            "trigger_reindex": self.trigger_reindex,
        }


@dataclass
class WatchConfig:
    """Configuration for a watched directory."""
    directory: str
    extensions: Set[str] = field(default_factory=lambda: {".pdf", ".docx", ".doc", ".txt", ".md"})
    recursive: bool = True
    debounce_seconds: float = 2.0
    ignored_patterns: List[str] = field(default_factory=lambda: [
        "~*", "*.tmp", "*.bak", ".DS_Store", "Thumbs.db"
    ])


# ====================================================================
# HASH CACHE
# ====================================================================

class HashCache:
    """Persistent cache for file hashes. Survives restarts."""

    def __init__(self, cache_file: str = "rag_change_detector_cache.json"):
        self.cache_file = cache_file
        self._cache: Dict[str, DocumentFingerprint] = {}
        self._lock = threading.Lock()
        self._load()

    def _load(self):
        """Load cache from disk."""
        try:
            if os.path.exists(self.cache_file):
                with open(self.cache_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                for path, fingerprint_dict in data.items():
                    self._cache[path] = DocumentFingerprint.from_dict(fingerprint_dict)
                logger.info(f"HashCache: Loaded {len(self._cache)} fingerprints from cache")
            else:
                logger.info("HashCache: Starting with empty cache")
        except (json.JSONDecodeError, IOError) as e:
            logger.warning(f"HashCache: Failed to load cache: {e}. Starting fresh.")
            self._cache = {}

    def _save(self):
        """Persist cache to disk."""
        try:
            data = {path: fp.to_dict() for path, fp in self._cache.items()}
            with open(self.cache_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except IOError as e:
            logger.error(f"HashCache: Failed to save cache: {e}")

    def get(self, file_path: str) -> Optional[DocumentFingerprint]:
        with self._lock:
            return self._cache.get(file_path)

    def set(self, file_path: str, fingerprint: DocumentFingerprint):
        with self._lock:
            self._cache[file_path] = fingerprint
            self._save()

    def remove(self, file_path: str):
        with self._lock:
            self._cache.pop(file_path, None)
            self._save()

    def exists(self, file_path: str) -> bool:
        with self._lock:
            return file_path in self._cache

    def has_changed(self, file_path: str, current_hash: str) -> bool:
        """Check if a file has changed since last scan."""
        with self._lock:
            fp = self._cache.get(file_path)
            if fp is None:
                return True  # New file
            return fp.sha256_hash != current_hash

    def clear(self):
        with self._lock:
            self._cache.clear()
            self._save()

    def __len__(self) -> int:
        return len(self._cache)


# ====================================================================
# CHANGE DETECTOR
# ====================================================================

class ChangeDetector:
    """
    SOTA change detector for RAG source documents.

    Features:
    - SHA-256 hash-based change detection
    - File system watching with watchdog
    - Debouncing for batch change processing
    - Async-compatible event dispatching
    - Configurable watch directories
    """

    def __init__(self, watch_configs: Optional[List[WatchConfig]] = None, cache_file: str = "rag_change_detector_cache.json"):
        self.watch_configs: List[WatchConfig] = watch_configs or []
        self.hash_cache = HashCache(cache_file)
        self._observers: List[Any] = []
        self._handlers: Dict[str, Any] = {}
        self._callbacks: List[Callable[[ChangeEvent], None]] = []
        self._async_callbacks: List[Callable[[ChangeEvent], Any]] = []
        self._running = False
        self._lock = threading.Lock()
        self._event_counter = 0
        self._debounce_timers: Dict[str, Any] = {}
        self._change_log: List[Dict[str, Any]] = []
        self._max_log_size = 1000

    # ----------------------------------------------------------------
    # Configuration
    # ----------------------------------------------------------------

    def add_watch_directory(self, directory: str, extensions: Optional[Set[str]] = None, 
                           recursive: bool = True, debounce_seconds: float = 2.0):
        """Add a directory to watch."""
        config = WatchConfig(
            directory=directory,
            extensions=extensions or {".pdf", ".docx", ".doc", ".txt", ".md"},
            recursive=recursive,
            debounce_seconds=debounce_seconds,
        )
        self.watch_configs.append(config)
        logger.info(f"ChangeDetector: Added watch directory: {directory}")

    def remove_watch_directory(self, directory: str) -> bool:
        """Remove a watched directory."""
        for i, config in enumerate(self.watch_configs):
            if config.directory == directory:
                self.watch_configs.pop(i)
                logger.info(f"ChangeDetector: Removed watch directory: {directory}")
                return True
        return False

    # ----------------------------------------------------------------
    # Callbacks
    # ----------------------------------------------------------------

    def on_change(self, callback: Callable[[ChangeEvent], None]):
        """Register a synchronous callback for change events."""
        self._callbacks.append(callback)

    def on_change_async(self, callback: Callable[[ChangeEvent], Any]):
        """Register an asynchronous callback for change events."""
        self._async_callbacks.append(callback)

    # ----------------------------------------------------------------
    # Hashing
    # ----------------------------------------------------------------

    @staticmethod
    def compute_hash(file_path: str, chunk_size: int = 8192) -> str:
        """Compute SHA-256 hash of a file."""
        sha256 = hashlib.sha256()
        try:
            with open(file_path, "rb") as f:
                while chunk := f.read(chunk_size):
                    sha256.update(chunk)
            return sha256.hexdigest()
        except (IOError, OSError) as e:
            logger.error(f"ChangeDetector: Failed to hash {file_path}: {e}")
            return ""

    @staticmethod
    def get_file_fingerprint(file_path: str) -> Optional[DocumentFingerprint]:
        """Create a fingerprint for a file."""
        try:
            stat = os.stat(file_path)
            file_hash = ChangeDetector.compute_hash(file_path)
            if not file_hash:
                return None
            return DocumentFingerprint(
                file_path=file_path,
                sha256_hash=file_hash,
                size_bytes=stat.st_size,
                modified_time=stat.st_mtime,
                created_time=stat.st_ctime,
            )
        except (IOError, OSError) as e:
            logger.error(f"ChangeDetector: Failed to fingerprint {file_path}: {e}")
            return None

    # ----------------------------------------------------------------
    # Event Generation
    # ----------------------------------------------------------------

    def _generate_event_id(self) -> str:
        with self._lock:
            self._event_counter += 1
            return f"evt_{int(time.time())}_{self._event_counter}"

    def _create_change_event(self, change_type: str, file_path: str, 
                            old_hash: Optional[str] = None, 
                            new_hash: Optional[str] = None) -> ChangeEvent:
        """Create a change event."""
        event = ChangeEvent(
            event_id=self._generate_event_id(),
            change_type=change_type,
            file_path=file_path,
            old_hash=old_hash,
            new_hash=new_hash,
        )
        # Add to log
        self._change_log.append(event.to_dict())
        if len(self._change_log) > self._max_log_size:
            self._change_log = self._change_log[-self._max_log_size:]
        return event

    # ----------------------------------------------------------------
    # Debouncing
    # ----------------------------------------------------------------

    def _debounce(self, file_path: str, delay: float, callback: Callable):
        """Debounce rapid changes to the same file."""
        with self._lock:
            # Cancel existing timer for this file
            if file_path in self._debounce_timers:
                self._debounce_timers[file_path].cancel()
            
            timer = threading.Timer(delay, callback)
            self._debounce_timers[file_path] = timer
            timer.start()

    # ----------------------------------------------------------------
    # File Event Handling
    # ----------------------------------------------------------------

    def _should_process(self, file_path: str, config: WatchConfig) -> bool:
        """Check if a file should be processed."""
        path = Path(file_path)
        
        # Check extension
        if path.suffix.lower() not in config.extensions:
            return False
        
        # Check ignored patterns
        for pattern in config.ignored_patterns:
            import fnmatch
            if fnmatch.fnmatch(path.name, pattern):
                return False
        
        return True

    def _process_file_change(self, file_path: str, event_type: str, config: WatchConfig):
        """Process a file change with hash verification."""
        if not self._should_process(file_path, config):
            return

        # Resolve to absolute path
        file_path = os.path.abspath(file_path)

        if event_type in ("created", "modified"):
            if not os.path.exists(file_path):
                return
            
            new_fingerprint = self.get_file_fingerprint(file_path)
            if not new_fingerprint:
                return

            old_fingerprint = self.hash_cache.get(file_path)
            
            if old_fingerprint is None:
                # New file
                event = self._create_change_event("added", file_path, new_hash=new_fingerprint.sha256_hash)
                self.hash_cache.set(file_path, new_fingerprint)
                self._dispatch_event(event)
            elif self.hash_cache.has_changed(file_path, new_fingerprint.sha256_hash):
                # Modified file
                event = self._create_change_event(
                    "modified", file_path, 
                    old_hash=old_fingerprint.sha256_hash, 
                    new_hash=new_fingerprint.sha256_hash
                )
                self.hash_cache.set(file_path, new_fingerprint)
                self._dispatch_event(event)
            # else: No actual change (same hash), ignore

        elif event_type == "deleted":
            old_fingerprint = self.hash_cache.get(file_path)
            if old_fingerprint is not None:
                event = self._create_change_event("deleted", file_path, old_hash=old_fingerprint.sha256_hash)
                self.hash_cache.remove(file_path)
                self._dispatch_event(event)

    def _dispatch_event(self, event: ChangeEvent):
        """Dispatch a change event to all registered callbacks."""
        logger.info(f"ChangeDetector: {event.change_type.upper()} {event.file_path}")
        
        # Synchronous callbacks
        for callback in self._callbacks:
            try:
                callback(event)
            except Exception as e:
                logger.error(f"ChangeDetector: Callback error: {e}")
        
        # Async callbacks (schedule in event loop)
        for callback in self._async_callbacks:
            try:
                import asyncio
                loop = asyncio.new_event_loop()
                loop.run_until_complete(callback(event))
                loop.close()
            except Exception as e:
                logger.error(f"ChangeDetector: Async callback error: {e}")

    # ----------------------------------------------------------------
    # File System Handler
    # ----------------------------------------------------------------

    def _create_handler(self, config: WatchConfig) -> Any:
        """Create a file system event handler for a watch config."""
        if not WATCHDOG_AVAILABLE or FileSystemEventHandler is None:
            logger.warning("ChangeDetector: watchdog not available. Using polling fallback.")
            return None

        class RAGChangeHandler(FileSystemEventHandler):  # type: ignore
            def __init__(self, detector: ChangeDetector, config: WatchConfig):
                self.detector = detector
                self.config = config

            def _to_str(self, path: Any) -> str:
                if isinstance(path, bytes):
                    return path.decode("utf-8", errors="replace")
                return path

            def on_created(self, event: FileSystemEvent):  # type: ignore
                if event.is_directory:
                    return
                src = self._to_str(event.src_path)
                self.detector._debounce(
                    src,
                    self.config.debounce_seconds,
                    lambda: self.detector._process_file_change(src, "created", self.config)
                )

            def on_modified(self, event: FileSystemEvent):  # type: ignore
                if event.is_directory:
                    return
                src = self._to_str(event.src_path)
                self.detector._debounce(
                    src,
                    self.config.debounce_seconds,
                    lambda: self.detector._process_file_change(src, "modified", self.config)
                )

            def on_deleted(self, event: FileSystemEvent):  # type: ignore
                if event.is_directory:
                    return
                self.detector._process_file_change(self._to_str(event.src_path), "deleted", self.config)

        return RAGChangeHandler(self, config)

    # ----------------------------------------------------------------
    # Start/Stop
    # ----------------------------------------------------------------

    def start(self):
        """Start watching all configured directories."""
        if self._running:
            logger.warning("ChangeDetector: Already running")
            return

        if not WATCHDOG_AVAILABLE:
            logger.warning("ChangeDetector: watchdog not installed. Install with: pip install watchdog")
            return

        for config in self.watch_configs:
            directory = os.path.abspath(config.directory)
            if not os.path.isdir(directory):
                logger.warning(f"ChangeDetector: Directory does not exist: {directory}")
                continue

            handler = self._create_handler(config)
            if handler is None:
                continue

            observer_class = Observer  # type: ignore
            if observer_class is None:
                logger.warning("ChangeDetector: Observer not available (watchdog not installed)")
                continue
            observer = observer_class()
            observer.schedule(handler, directory, recursive=config.recursive)
            observer.start()
            self._observers.append(observer)
            self._handlers[directory] = handler
            logger.info(f"ChangeDetector: Watching {directory} (recursive={config.recursive})")

        self._running = True
        logger.info(f"ChangeDetector: Started with {len(self._observers)} observers")

    def stop(self):
        """Stop watching all directories."""
        if not self._running:
            return

        for observer in self._observers:
            try:
                observer.stop()
                observer.join(timeout=5)
            except Exception as e:
                logger.error(f"ChangeDetector: Error stopping observer: {e}")

        self._observers.clear()
        self._handlers.clear()
        
        # Cancel all debounce timers
        with self._lock:
            for timer in self._debounce_timers.values():
                timer.cancel()
            self._debounce_timers.clear()

        self._running = False
        logger.info("ChangeDetector: Stopped")

    @property
    def is_running(self) -> bool:
        return self._running

    # ----------------------------------------------------------------
    # Manual Scanning
    # ----------------------------------------------------------------

    def scan_directory(self, directory: str, extensions: Optional[Set[str]] = None) -> List[ChangeEvent]:
        """Manually scan a directory for changes."""
        events: List[ChangeEvent] = []
        directory = os.path.abspath(directory)
        extensions = extensions or {".pdf", ".docx", ".doc", ".txt", ".md"}

        if not os.path.isdir(directory):
            logger.warning(f"ChangeDetector: Directory does not exist: {directory}")
            return events

        for root, dirs, files in os.walk(directory):
            for file_name in files:
                file_path = os.path.join(root, file_name)
                path = Path(file_path)

                if path.suffix.lower() not in extensions:
                    continue

                current_fingerprint = self.get_file_fingerprint(file_path)
                if not current_fingerprint:
                    continue

                old_fingerprint = self.hash_cache.get(file_path)

                if old_fingerprint is None:
                    event = self._create_change_event("added", file_path, new_hash=current_fingerprint.sha256_hash)
                    self.hash_cache.set(file_path, current_fingerprint)
                    events.append(event)
                elif self.hash_cache.has_changed(file_path, current_fingerprint.sha256_hash):
                    event = self._create_change_event(
                        "modified", file_path,
                        old_hash=old_fingerprint.sha256_hash,
                        new_hash=current_fingerprint.sha256_hash
                    )
                    self.hash_cache.set(file_path, current_fingerprint)
                    events.append(event)

        # Check for deleted files
        for cached_path in list(self.hash_cache._cache.keys()):
            if not os.path.exists(cached_path):
                old_fp = self.hash_cache.get(cached_path)
                if old_fp:
                    event = self._create_change_event("deleted", cached_path, old_hash=old_fp.sha256_hash)
                    self.hash_cache.remove(cached_path)
                    events.append(event)

        logger.info(f"ChangeDetector: Scan complete. Found {len(events)} changes.")
        return events

    def scan(self) -> List[Dict[str, Any]]:
        """Compatibility wrapper used by the SOTA pipeline."""
        changes: List[Dict[str, Any]] = []
        for config in self.watch_configs:
            for event in self.scan_directory(config.directory, config.extensions):
                payload = event.to_dict()
                payload["status"] = event.change_type
                changes.append(payload)
        return changes

    # ----------------------------------------------------------------
    # Status
    # ----------------------------------------------------------------

    def get_status(self) -> Dict[str, Any]:
        """Get the current status of the change detector."""
        return {
            "running": self._running,
            "watch_directories": [c.directory for c in self.watch_configs],
            "cached_files": len(self.hash_cache),
            "observers": len(self._observers),
            "change_log_size": len(self._change_log),
        }

    def get_change_log(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Get the recent change log."""
        return self._change_log[-limit:]

    def get_novelty_score(
        self,
        query: Optional[str] = None,
        *,
        limit: int = 50,
        half_life_seconds: float = 86400.0,
    ) -> float:
        """
        Estimate how much fresh source activity is currently present.

        The score is intentionally query-agnostic because the change detector only
        observes source mutations, not semantic relevance. The optional ``query``
        parameter exists for orchestrator compatibility.
        """
        recent_events = self.get_change_log(limit=max(1, limit))
        if not recent_events:
            return 0.0

        now = time.time()
        decay = max(1.0, float(half_life_seconds))
        type_weights = {
            "added": 1.0,
            "modified": 0.75,
            "deleted": 0.35,
        }

        weighted_activity = 0.0
        unique_paths: Set[str] = set()
        for event in recent_events:
            timestamp = float(event.get("timestamp") or 0.0)
            age_seconds = max(0.0, now - timestamp)
            recency_weight = math.exp(-age_seconds / decay)
            event_type = str(event.get("change_type") or event.get("status") or "modified")
            weighted_activity += recency_weight * type_weights.get(event_type, 0.5)

            file_path = event.get("file_path")
            if isinstance(file_path, str) and file_path:
                unique_paths.add(file_path)

        activity_score = weighted_activity / max(1, len(recent_events))
        diversity_score = min(1.0, len(unique_paths) / max(1.0, min(float(limit), 10.0)))
        watcher_bonus = 0.05 if self._running else 0.0

        novelty_score = (0.70 * activity_score) + (0.25 * diversity_score) + watcher_bonus
        return max(0.0, min(1.0, novelty_score))

    def reset_cache(self):
        """Reset the hash cache."""
        self.hash_cache.clear()
        self._change_log.clear()
        self._event_counter = 0
        logger.info("ChangeDetector: Cache reset")


# ====================================================================
# MODULE ENTRY POINT
# ====================================================================

def create_default_detector() -> ChangeDetector:
    """Create a ChangeDetector with default configuration."""
    # Default watch directories
    watch_dirs = [
        os.path.join(os.path.expanduser("~"), "homebot", "data"),
        os.path.join(os.path.expanduser("~"), "homebot", "document_processors"),
    ]
    
    configs = []
    for dir_path in watch_dirs:
        if os.path.isdir(dir_path):
            configs.append(WatchConfig(directory=dir_path))

    detector = ChangeDetector(watch_configs=configs)
    return detector


if __name__ == "__main__":
    # Quick test
    logging.basicConfig(level=logging.INFO)
    
    detector = create_default_detector()
    
    @detector.on_change
    def handle_change(event: ChangeEvent):
        print(f"[CHANGE] {event.change_type}: {event.file_path}")
        if event.old_hash:
            print(f"  Old: {event.old_hash[:16]}...")
        if event.new_hash:
            print(f"  New: {event.new_hash[:16]}...")
    
    print("ChangeDetector starting...")
    print(f"Status: {detector.get_status()}")
    
    detector.start()
    
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nShutting down...")
        detector.stop()