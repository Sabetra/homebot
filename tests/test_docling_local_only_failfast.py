"""Tests for strict local-only Docling behavior and fail-fast classification."""

import os
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.unified_rag_store import UnifiedRagStore
from utils.docling_processor import DoclingProcessor
from utils.runtime_policy import OutboundNetworkBlockedError


class TestDoclingLocalOnlyFailFast:
    def test_convert_file_classifies_network_block(self):
        processor = DoclingProcessor()
        processor._local_only = True
        processor._ensure_initialized = Mock()  # type: ignore[method-assign]
        processor._converter = Mock(
            convert=Mock(
                side_effect=OutboundNetworkBlockedError(
                    "APP_LOCAL_ONLY active: outbound HTTP network request blocked"
                )
            )
        )

        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
            tmp.write(b"%PDF-1.4\n")
            path = tmp.name

        try:
            result = processor.convert_file(path)
            assert result.success is False
            assert result.error_code == "local_only_network_blocked"
        finally:
            os.unlink(path)

    def test_convert_file_classifies_local_resource_missing(self):
        processor = DoclingProcessor()
        processor._local_only = True
        processor._ensure_initialized = Mock()  # type: ignore[method-assign]
        processor._converter = Mock(
            convert=Mock(
                side_effect=RuntimeError(
                    "Local-only tokenizer artifact missing for intfloat/multilingual-e5-large"
                )
            )
        )

        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
            tmp.write(b"%PDF-1.4\n")
            path = tmp.name

        try:
            result = processor.convert_file(path)
            assert result.success is False
            assert result.error_code == "local_only_resource_missing"
        finally:
            os.unlink(path)

    def test_load_e5_tokenizer_local_only_requires_snapshot(self):
        with patch("glob.glob", return_value=[]):
            with pytest.raises(RuntimeError, match="Local-only tokenizer artifact missing"):
                DoclingProcessor._load_e5_tokenizer(max_tokens=384, local_only=True)

    def test_nonrecoverable_docling_failure_classification(self):
        fatal = SimpleNamespace(
            error_code="local_only_network_blocked",
            error="APP_LOCAL_ONLY active: outbound HTTP network request blocked",
        )
        recoverable = SimpleNamespace(
            error_code="conversion_failure",
            error="Docling conversion failed: unknown failure",
        )

        assert UnifiedRagStore._is_nonrecoverable_docling_failure(fatal) is True
        assert UnifiedRagStore._is_nonrecoverable_docling_failure(recoverable) is False
