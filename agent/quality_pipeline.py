"""Compatibility wrapper for the SOTA RAG quality pipeline.

The canonical implementation lives in :mod:`agent.sota_pipeline`.
This module exists so older imports and documentation references keep working.
"""

from __future__ import annotations

from .sota_pipeline import PipelineConfig, PipelineDocument, PipelineResult, SOTAPipeline

QualityPipeline = SOTAPipeline

__all__ = [
    "PipelineConfig",
    "PipelineDocument",
    "PipelineResult",
    "QualityPipeline",
    "SOTAPipeline",
]
