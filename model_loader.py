"""Backward-compat re-export shim.

This top-level ``model_loader`` module previously contained a stub
``ModelLoader`` with mock JSON responses for ``"sentiment"`` and
``"trend"`` prompts. That stub was a latent silent-hallucination bug:
whenever any caller imported ``model_loader`` from the workspace root
(rather than ``scripts.model_loader``), generation would return fake
data without raising.

Root cause fix: the canonical implementation lives in
``scripts.model_loader``. This shim transparently re-exports it so that
all six historical import sites (`llm_feedback_analyzer.py`,
`launch_enhanced_chatbot.py`, `utils/enhanced_chunk_monitor.py`,
`utils/complete_cleanup.py`, `agent/rag_store/maintenance/reclassifier.py`,
and the legacy notebooks) reach the real module.

Do not add functionality here. New code should import from
``scripts.model_loader`` directly.
"""

from __future__ import annotations

from scripts.model_loader import *  # noqa: F401,F403  (re-export everything)
from scripts.model_loader import (  # noqa: F401  (explicit re-exports for IDE/IDEs)
    DEFAULT_MODEL,
    LLM_CONTEXT_SIZE,
    ModelLoader,
    cuda_lock,
    cuda_safe,
    get_available_models,
    get_model_config,
    get_model_loader,
)
