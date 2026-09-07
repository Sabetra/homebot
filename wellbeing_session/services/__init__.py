"""
Service Layer + Dependency Injection for Psychological Session.

This package implements a lightweight DI container and service layer
that decouples component creation from usage, enabling:
- Testability (mock any service in tests)
- Configurability (swap implementations)
- Lazy initialization (services created on first access)
- Clear dependency graph (all wiring in one place)
- Async-first DB operations (aiosqlite) with sync bridge for Streamlit
- OpenTelemetry-instrumented async service container

Modules:
    - protocols: Protocol/interface definitions (sync + async)
    - service_container: DI container that wires all dependencies
    - async_service_container: Async-capable DI container (dual-mode)
    - startup_service: Application startup tasks (sync)
    - async_startup_service: Application startup tasks (async)
    - session_end_service: Session ending logic (insights, UI, cleanup)
"""

from wellbeing_session.services.protocols import (
    ChatLogicProtocol,
    ModelLoaderProtocol,
    SessionManagerProtocol,
    EmotionalAnalyzerProtocol,
    ContextManagerProtocol,
    ProfileCacheProtocol,
    AsyncSessionManagerProtocol,
    AsyncStartupServiceProtocol,
    AsyncDBPoolProtocol,
)
from wellbeing_session.services.service_container import (
    ServiceContainer,
    ServiceConfig,
)
from wellbeing_session.services.startup_service import (
    StartupService,
)
from wellbeing_session.services.session_end_service import (
    SessionEndService,
)

# Async services (optional — require aiosqlite)
try:
    from wellbeing_session.services.async_startup_service import (
        AsyncStartupService,
        run_startup_cleanup_sync,
    )
    from wellbeing_session.services.async_service_container import (
        AsyncServiceContainer,
    )
    _ASYNC_AVAILABLE = True
except ImportError:
    _ASYNC_AVAILABLE = False

__all__ = [
    # Protocols (sync)
    "ChatLogicProtocol",
    "ModelLoaderProtocol",
    "SessionManagerProtocol",
    "EmotionalAnalyzerProtocol",
    "ContextManagerProtocol",
    "ProfileCacheProtocol",
    # Protocols (async)
    "AsyncSessionManagerProtocol",
    "AsyncStartupServiceProtocol",
    "AsyncDBPoolProtocol",
    # Container
    "ServiceContainer",
    "ServiceConfig",
    # Services (sync)
    "StartupService",
    "SessionEndService",
]

if _ASYNC_AVAILABLE:
    __all__ += [
        "AsyncStartupService",
        "run_startup_cleanup_sync",
        "AsyncServiceContainer",
    ]
