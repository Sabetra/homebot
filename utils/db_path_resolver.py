"""
SOTA Central Database Path Resolver
===================================

All database modules MUST import their path from this module to ensure
absolute paths are used regardless of the process's current working
directory (e.g., Streamlit may spawn workers from temp directories).

Usage:
    from utils.db_path_resolver import get_db_path

    # Simple usage – creates file under the DB root
    db_path = get_db_path("rag_store.db")

    # Nested path
    db_path = get_db_path("data", "models", "embedding.db")

DB-Root-Auflösung (Single Source of Truth):
    1. Env-Variable ``BOT6_DB_ROOT``
    2. Marker-Datei ``.db_root`` im Projekt-Root (Ziel muss existieren)
    3. Portable Default: ``~/.local/share/bot6_dbs`` (wird angelegt)
    DBs landen damit NIEMALS im Repository-Verzeichnis.
"""

import os
from pathlib import Path
from typing import Optional, Union


# ── Project root resolution ───────────────────────────────────────────
# Walk upwards from this file to find the repository root (marker: .git)
def _resolve_project_root() -> Path:
    """Return the absolute path to the project root directory."""
    current = Path(__file__).resolve().parent  # utils/
    for ancestor in current.parents:
        if (ancestor / ".git").exists():
            return ancestor
    # Fallback: parent of utils/
    return current.parent


_PROJECT_ROOT: Path = _resolve_project_root()

# Optional override via environment variable or .db_root marker file.
# Priority: (1) BOT6_DB_ROOT env var, (2) .db_root file content, (3) project root.
_ENV_DB_ROOT: Optional[Path] = None

# Check env var first
_env_var_path = os.environ.get("BOT6_DB_ROOT", "").strip()
if _env_var_path:
    p = Path(_env_var_path)
    if p.exists():
        _ENV_DB_ROOT = p.resolve()

# Fall back to .db_root marker file (allows persistent override without env config)
if _ENV_DB_ROOT is None:
    _db_root_marker = Path(__file__).resolve().parent.parent / ".db_root"
    if _db_root_marker.exists():
        try:
            content = _db_root_marker.read_text().strip()
            if content:
                p = Path(content)
                if p.exists():
                    _ENV_DB_ROOT = p.resolve()
        except Exception:
            pass


# Portable Default-DB-Root (wird bei Bedarf von get_db_path angelegt).
# Damit bleiben DBs bei jeder Installation außerhalb des Repositories.
_DEFAULT_DB_ROOT: Path = Path.home() / ".local" / "share" / "bot6_dbs"


def get_project_root() -> Path:
    """DB-Root: Env-Override > .db_root-Marker > ``~/.local/share/bot6_dbs``."""
    return _ENV_DB_ROOT if _ENV_DB_ROOT is not None else _DEFAULT_DB_ROOT


def get_db_path(*path_parts: Union[str, Path]) -> Path:
    """
    Return an absolute path under the DB root.

    Parameters
    ----------
    *path_parts : Path components joined together.
        ``get_db_path("data", "rag_store.db")`` → ``<root>/data/rag_store.db``

    Returns
    -------
    Path
        Absolute, resolved path ready for SQLite drivers.
    """
    root = get_project_root()
    target = root.joinpath(*path_parts).resolve()
    # Ensure parent directory exists
    target.parent.mkdir(parents=True, exist_ok=True)
    return target


# ── Convenience aliases (backward compat) ─────────────────────────────
def get_rag_store_path() -> Path:
    """Main RAG store at project root."""
    return get_db_path("rag_store.db")


def get_chat_history_path() -> Path:
    """Chat history DB at project root."""
    return get_db_path("chat_history.db")


def get_wellbeing_path() -> Path:
    """Wellbeing store DB at project root (renamed from psychological_support.db)."""
    return get_db_path("wellbeing_store.db")


def get_finance_path() -> Path:
    """Finance DB under database/ subdirectory."""
    return get_db_path("database", "finance.db")


def get_kg_path() -> Path:
    """Knowledge graph DB (shared with wellbeing store)."""
    return get_db_path("wellbeing_store.db")


def get_pdf_chunks_path() -> Path:
    """PDF chunks cached inside rag_store."""
    return get_db_path("rag_store.db")


def get_performance_metrics_path() -> Path:
    """Performance metrics at project root."""
    return get_db_path("performance_metrics.db")


def get_agent_rag_path() -> Path:
    """Agent-specific RAG store under agent/."""
    return get_db_path("agent", "rag_store.db")


def get_web_policy_path() -> Path:
    """Web policy cache at project root."""
    return get_db_path("web_policy.db")


def get_metrics_monitoring_path() -> Path:
    """Monitoring metrics under monitoring/."""
    return get_db_path("monitoring", "metrics.db")


def get_psych_sessions_path() -> Path:
    """Psychological sessions under data/."""
    return get_db_path("data", "wellbeing_sessions.db")


__all__ = [
    "get_project_root",
    "get_db_path",
    "get_rag_store_path",
    "get_chat_history_path",
    "get_wellbeing_path",
    "get_finance_path",
    "get_kg_path",
    "get_pdf_chunks_path",
    "get_performance_metrics_path",
    "get_agent_rag_path",
    "get_web_policy_path",
    "get_metrics_monitoring_path",
    "get_psych_sessions_path",
]