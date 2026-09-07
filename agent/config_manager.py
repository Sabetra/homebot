"""Centralized Configuration Manager.

Single source of truth for all runtime configuration:
  - LLM model selection & paths
  - RAG pipeline toggles (Self-RAG, CRAG, Agentic RAG, etc.)
  - GPU / CUDA tuning parameters
  - Finance module settings
  - Psychological session settings

Priority order (highest wins):
  1. Environment variables (prefix: AGENT_)
  2. Local override file: .agent_env (git-ignored)
  3. Built-in defaults

Usage:
    from agent.config_manager import cfg
    model = cfg.get_model()          # -> "gemma-4-e4b"
    ctx  = cfg.get_context_size()    # -> 32768
"""

from __future__ import annotations

import os
import json
import logging
import threading
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional, Any, Dict

logger = logging.getLogger(__name__)

# =============================================================================
# Data Contracts
# =============================================================================

@dataclass
class LLMConfig:
    """LLM / model-level configuration."""
    model_id: str = "gemma-4-e4b"
    n_ctx: int = 32768
    n_gpu_layers: int = -1  # -1 = all layers on GPU
    verbose: bool = False
    # RTX 4090 defaults (24 GB VRAM)
    n_batch: int = 4096
    n_ubatch: int = 2048
    # Fallback chain (v6.04.0)
    fallback_models: list[str] = field(default_factory=lambda: [
        "gemma-3-12b-it", "magistral-small"
    ])
    enable_fallback: bool = True
    # Circuit breaker (tenacity)
    max_retries: int = 3
    retry_backoff: float = 1.5


@dataclass
class RAGConfig:
    """RAG pipeline configuration."""
    # Core
    embedding_dim: int = 1024  # nomic-embed-text / bge
    similarity_threshold: float = 0.75
    max_evidence_chunks: int = 8
    # SOTA toggles
    self_rag_enabled: bool = False
    self_rag_critiquity_threshold: float = 0.6
    crag_enabled: bool = False
    crag_web_search_fallback: bool = True
    agentic_rag_enabled: bool = False
    # Cross-Encoder reranking (already available)
    cross_encoder_enabled: bool = True
    # SSoT for the active model is agent/reranker.py (RERANKER_MODEL_NAME / _CROSS_ENCODER_MODELS).
    # Must stay in sync with models/manifest.json (enforced by tests/test_model_manifest_consistency.py).
    cross_encoder_model: str = "BAAI/bge-reranker-v2-m3"
    cross_encoder_gpu: bool = True
    # IRCoT
    ircot_max_steps: int = 3
    # Decomposition
    decomposition_enabled: bool = False


@dataclass
class GPUConfig:
    """GPU / CUDA tuning."""
    device: str = "cuda"
    memory_fraction: float = 0.9  # reserve 10% for OS/driver
    enable_flash_attention: bool = True
    # ONNX runtime
    onnx_execution_provider: str = "cuda"  # "cuda" | "cpu" | "tensorrt"


@dataclass
class FinanceConfig:
    """Finance module configuration."""
    cache_ttl_seconds: int = 600
    cache_max_entries: int = 512
    grammar_constrained_decoding: bool = True
    enable_query_planning: bool = True
    enable_reflection: bool = True
    max_reflection_iterations: int = 2


@dataclass
class SessionConfig:
    """Psychological session configuration."""
    default_duration_minutes: int = 60
    auto_extend_threshold: float = 0.8
    cleanup_idle_minutes: int = 30
    async_startup: bool = True


@dataclass
class LoggingConfig:
    """Logging configuration."""
    level: str = "INFO"
    format_json: bool = True
    file_rotation_mb: int = 50
    file_rotation_count: int = 5


# =============================================================================
# Config Manager Singleton
# =============================================================================

class ConfigManager:
    """Loads, merges, and provides typed configuration."""

    # Environment variable prefix
    ENV_PREFIX = "AGENT_"

    # Well-known env var mappings: env_key -> (section, attr, cast)
    _ENV_MAP: list[tuple[str, str, str, type]] = [
        # LLM
        ("MODEL_ID", "llm", "model_id", str),
        ("N_CTX", "llm", "n_ctx", int),
        ("N_GPU_LAYERS", "llm", "n_gpu_layers", int),
        ("N_BATCH", "llm", "n_batch", int),
        ("N_UBATCH", "llm", "n_ubatch", int),
        ("ENABLE_FALLBACK", "llm", "enable_fallback", bool),
        ("MAX_RETRIES", "llm", "max_retries", int),
        # RAG
        ("SELF_RAG_ENABLED", "rag", "self_rag_enabled", bool),
        ("CRAG_ENABLED", "rag", "crag_enabled", bool),
        ("AGENTIC_RAG_ENABLED", "rag", "agentic_rag_enabled", bool),
        ("CROSS_ENCODER_ENABLED", "rag", "cross_encoder_enabled", bool),
        ("CROSS_ENCODER_GPU", "rag", "cross_encoder_gpu", bool),
        ("DECOMPOSITION_ENABLED", "rag", "decomposition_enabled", bool),
        ("SIMILARITY_THRESHOLD", "rag", "similarity_threshold", float),
        ("MAX_EVIDENCE_CHUNKS", "rag", "max_evidence_chunks", int),
        # GPU
        ("GPU_DEVICE", "gpu", "device", str),
        ("GPU_MEMORY_FRACTION", "gpu", "memory_fraction", float),
        ("FLASH_ATTENTION", "gpu", "enable_flash_attention", bool),
        # Finance
        ("FINANCE_CACHE_TTL", "finance", "cache_ttl_seconds", int),
        ("FINANCE_GRAMMAR_CONSTRAINED", "finance", "grammar_constrained_decoding", bool),
        ("FINANCE_QUERY_PLANNING", "finance", "enable_query_planning", bool),
        # Session
        ("SESSION_DURATION", "session", "default_duration_minutes", int),
        ("SESSION_ASYNC_STARTUP", "session", "async_startup", bool),
        # Logging
        ("LOG_LEVEL", "logging", "level", str),
        ("LOG_JSON", "logging", "format_json", bool),
    ]

    def __init__(self, override_path: Optional[str] = None):
        self._lock = threading.RLock()
        self._override_path = (
            Path(override_path) if override_path else Path(__file__).parent.parent / ".agent_env"
        )
        self._override_mtime: Optional[float] = None

        self._reset_defaults()
        self.reload(force=True)

    def _reset_defaults(self) -> None:
        self.llm = LLMConfig()
        self.rag = RAGConfig()
        self.gpu = GPUConfig()
        self.finance = FinanceConfig()
        self.session = SessionConfig()
        self.logging_cfg = LoggingConfig()

    def _read_override_mtime(self) -> Optional[float]:
        try:
            if self._override_path.exists():
                return self._override_path.stat().st_mtime
        except OSError as exc:
            logger.warning("Config: cannot read override mtime for %s: %s", self._override_path, exc)
        return None

    # ------------------------------------------------------------------
    # Public convenience accessors
    # ------------------------------------------------------------------
    def get_model(self) -> str:
        return self.llm.model_id

    def get_context_size(self) -> int:
        return self.llm.n_ctx

    def to_dict(self) -> dict:
        return {
            "llm": asdict(self.llm),
            "rag": asdict(self.rag),
            "gpu": asdict(self.gpu),
            "finance": asdict(self.finance),
            "session": asdict(self.session),
            "logging": asdict(self.logging_cfg),
        }

    def reload(self, *, force: bool = False) -> Dict[str, Any]:
        """Reload all config layers and return a structured diff summary."""
        with self._lock:
            previous = self.to_dict()
            current_mtime = self._read_override_mtime()

            if not force and current_mtime == self._override_mtime:
                return {
                    "reloaded": False,
                    "reason": "override_not_changed",
                    "override_path": str(self._override_path),
                }

            self._reset_defaults()

            if self._override_path.exists():
                self._apply_env_file(self._override_path)
            self._apply_env_vars()

            self._override_mtime = current_mtime
            current = self.to_dict()
            changed_sections = [
                section for section in current.keys()
                if current.get(section) != previous.get(section)
            ]
            return {
                "reloaded": True,
                "override_path": str(self._override_path),
                "changed_sections": changed_sections,
            }

    def reload_if_override_changed(self) -> Dict[str, Any]:
        """Reload only when the override file changed on disk."""
        return self.reload(force=False)

    # ------------------------------------------------------------------
    # Internal: layer application
    # ------------------------------------------------------------------
    def _apply_env_file(self, path: Path) -> None:
        """Read key=value or JSON override file."""
        try:
            raw_text = path.read_text(encoding="utf-8")
        except OSError as e:
            logger.warning("Failed to read override file %s: %s", path, e)
            return

        # Try JSON first
        try:
            data = json.loads(raw_text)
            self._apply_dict(data)
            return
        except json.JSONDecodeError:
            pass

        # Fall back to KEY=VALUE lines
        for line in raw_text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            k, v = line.split("=", 1)
            k, v = k.strip(), v.strip()
            # Strip AGENT_ prefix if present
            if k.startswith(self.ENV_PREFIX):
                k = k[len(self.ENV_PREFIX):]
            self._resolve_and_set(k, v)

    def _apply_env_vars(self) -> None:
        for env_key, section, attr, cast in self._ENV_MAP:
            full_key = f"{self.ENV_PREFIX}{env_key}"
            val = os.environ.get(full_key)
            if val is None:
                # Also check legacy keys without prefix (e.g. LLM_N_CTX)
                legacy = env_key.replace("MODEL_ID", "LLM_MODEL").replace("N_CTX", "LLM_N_CTX")
                if env_key == "N_CTX":
                    legacy = "LLM_N_CTX"
                val = os.environ.get(legacy)
            if val is not None:
                self._resolve_and_set(env_key, val, cast=cast, section=section, attr=attr)

    def _resolve_and_set(
        self,
        key: str,
        raw: str,
        cast: type = str,
        section: Optional[str] = None,
        attr: Optional[str] = None,
    ) -> None:
        """Look up (section, attr) from the env map, cast, and set."""
        if section and attr:
            pass  # already resolved
        else:
            # Resolve from env map
            for env_key, s, a, c in self._ENV_MAP:
                if env_key == key:
                    section, attr, cast = s, a, c
                    break
            if not section or not attr:
                return  # unknown key, skip silently

        # Cast
        try:
            if cast is bool:
                val = raw.lower() in ("1", "true", "yes", "on")
            else:
                val = cast(raw)
        except (ValueError, TypeError):
            logger.warning("Config: cannot cast %s=%s to %s", key, raw, cast)
            return

        # Set on the right section
        section_map = {
            "llm": self.llm,
            "rag": self.rag,
            "gpu": self.gpu,
            "finance": self.finance,
            "session": self.session,
            "logging": self.logging_cfg,
        }
        target = section_map.get(section)
        if target and hasattr(target, attr):
            setattr(target, attr, val)

    def _apply_dict(self, data: dict) -> None:
        """Apply nested dict (from JSON override file)."""
        section_map = {
            "llm": self.llm,
            "rag": self.rag,
            "gpu": self.gpu,
            "finance": self.finance,
            "session": self.session,
            "logging": self.logging_cfg,
        }
        for section_name, target in section_map.items():
            section_data = data.get(section_name)
            if not section_data:
                continue
            for k, v in section_data.items():
                if hasattr(target, k):
                    setattr(target, k, v)


# =============================================================================
# Module-level singleton
# =============================================================================
cfg = ConfigManager()