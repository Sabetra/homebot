"""
Resource Manager - Thread-Safe Resource Cleanup
================================================

Provides global resource management with automatic cleanup on shutdown.

Classes:
    - ResourceManager: Global resource manager for safe cleanup operations
    - ManagedResource: Base class for resources with automatic cleanup
    
Functions:
    - managed_resource: Context manager for temporary resources with file descriptor tracking
"""

import threading
import atexit
import logging
import gc
import io
from typing import Any, Set, Generator
from contextlib import contextmanager

logger = logging.getLogger(__name__)


class ResourceManager:
    """Globaler Resource Manager für sichere Cleanup-Operationen"""
    
    def __init__(self) -> None:
        self._resources: Set[Any] = set()
        self._lock = threading.RLock()
        self._shutdown_started = False
        
        # Registriere Cleanup bei Programm-Ende
        atexit.register(self._cleanup_all)
    
    def register_resource(self, resource: Any) -> None:
        """Registriert eine Resource für Cleanup"""
        with self._lock:
            if not self._shutdown_started:
                self._resources.add(resource)
    
    def unregister_resource(self, resource: Any) -> None:
        """Entfernt eine Resource aus dem Cleanup"""
        with self._lock:
            self._resources.discard(resource)
    
    def _cleanup_all(self) -> None:
        """Führt Cleanup aller registrierten Resourcen durch"""
        with self._lock:
            self._shutdown_started = True
            
            # Kopiere die Liste um Race Conditions zu vermeiden
            resources_to_cleanup = list(self._resources)
            
        for resource in resources_to_cleanup:
            try:
                if hasattr(resource, 'cleanup_resources'):
                    resource.cleanup_resources()
                elif hasattr(resource, 'close'):
                    resource.close()
            except Exception as e:
                # Log aber störe nicht das Shutdown
                logger.debug(f"Cleanup error für {resource}: {e}")
        
        with self._lock:
            self._resources.clear()


# Globaler Resource Manager
_resource_manager = ResourceManager()


class ManagedResource:
    """Base-Klasse für Resources mit automatischem Cleanup"""
    
    def __init__(self) -> None:
        self._cleaned_up = False
        _resource_manager.register_resource(self)
    
    def cleanup_resources(self) -> None:
        """Subclasses müssen diese Methode implementieren"""
        if self._cleaned_up:
            return
        self._cleaned_up = True
        _resource_manager.unregister_resource(self)
    
    def __del__(self) -> None:
        """Fallback Cleanup - aber nur als letzter Ausweg"""
        try:
            if not self._cleaned_up:
                self.cleanup_resources()
        except Exception:
            pass  # Während Shutdown können Exceptions auftreten


@contextmanager
def managed_resource(resource: Any) -> Generator[Any, None, None]:
    """
    Context Manager für temporary Resources mit File Descriptor Tracking
    
    Args:
        resource: Any resource that supports cleanup_resources() or close()
        
    Yields:
        The resource
        
    Example:
        >>> with managed_resource(some_resource) as res:
        ...     res.do_something()
    """
    # Sammle File Objects vor Operation
    before_objects = set()
    for obj in gc.get_objects():
        if isinstance(obj, (io.IOBase, io.TextIOWrapper)):
            if not obj.closed:
                before_objects.add(id(obj))
    
    try:
        yield resource
    finally:
        # Cleanup der Resource
        if hasattr(resource, 'cleanup_resources'):
            resource.cleanup_resources()
        elif hasattr(resource, 'close'):
            try:
                resource.close()
            except Exception:
                pass
        
        # Sammle und schließe neue File Objects
        after_objects = []
        for obj in gc.get_objects():
            if isinstance(obj, (io.IOBase, io.TextIOWrapper)):
                if not obj.closed and id(obj) not in before_objects:
                    after_objects.append(obj)
        
        # Schließe neue File Objects
        for obj in after_objects:
            try:
                # Überspringe Standard-Streams
                if hasattr(obj, 'name') and obj.name not in ('<stdin>', '<stdout>', '<stderr>'):
                    obj.close()
            except Exception:
                pass
        
        # Force Garbage Collection
        gc.collect()


# Public API
__all__ = [
    'ResourceManager',
    'ManagedResource',
    'managed_resource',
    '_resource_manager',  # Globaler Singleton
]
