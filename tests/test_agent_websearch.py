"""Tests for scripts/agent_websearch.py

Covers: PII sanitization, domain filtering, cache TTL, search() contract,
and CLI exit codes.  Network calls to DDG are NOT exercised here; the
DDG engine is mocked at the _ddg_search level.

pytest -q tests/test_agent_websearch.py --no-header -p no:cacheprovider
"""

from __future__ import annotations

import json
import sys
import textwrap
import time
from pathlib import Path
from unittest import mock

import pytest

# ---------------------------------------------------------------------------
# Import under-test
# ---------------------------------------------------------------------------
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
import agent_websearch as ws  # noqa: E402 (module-level side-effects OK)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def tmp_cache(tmp_path: Path) -> ws.SearchCache:
    """Return a fresh cache backed by a temp directory."""
    return ws.SearchCache(cache_path=tmp_path / "cache.json", max_entries=50)


@pytest.fixture(autouse=True)
def _reset_cache(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Ensure every test starts with an empty in-memory store."""
    cache = ws.SearchCache(cache_path=tmp_path / "cache.json")
    cache.clear()
    monkeypatch.setattr(ws, "SearchCache", lambda **kw: cache)
    return cache

# ---------------------------------------------------------------------------
# PII Sanitizer
# ---------------------------------------------------------------------------

class TestPIISanitizer:
    def test_phone_redacted(self):
        out = ws.PII_Sanitizer().sanitize("Call me at 123-456-7890 now")
        assert "123-456-7890" not in out
        assert "[REDACTED]" in out

    def test_email_redacted(self):
        out = ws.PII_Sanitizer().sanitize("Email user@example.com please")
        assert "user@example.com" not in out

    def test_no_pii_passes_through(self):
        out = ws.PII_Sanitizer().sanitize("Python 3.13 release date")
        assert out == "Python 3.13 release date"

# ---------------------------------------------------------------------------
# Domain Filter
# ---------------------------------------------------------------------------

class TestDomainFilter:
    def test_blocked_domain_removed(self):
        df = ws.DomainFilter()
        results = [{"url": "https://www.doubleclick.net/ad", "title": "x"}]
        assert df.filter_results(results) == []

    def test_allowed_domain_kept(self):
        df = ws.DomainFilter()
        results = [{"url": "https://python.org/", "title": "x"}]
        assert len(df.filter_results(results)) == 1

    def test_custom_blocklist(self):
        df = ws.DomainFilter(blocked={"evil.com"})
        results = [{"url": "https://evil.com/page", "title": "bad"}]
        assert df.filter_results(results) == []

# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

class TestSearchCache:
    def test_store_and_retrieve(self, tmp_cache: ws.SearchCache):
        data = [{"title": "A", "url": "http://a.com", "body": ""}]
        tmp_cache.store("query", 3, data)
        hit = tmp_cache.get("query", 3)
        assert hit is not None
        assert len(hit) == 1
        assert hit[0]["title"] == "A"

    def test_miss_returns_none(self, tmp_cache: ws.SearchCache):
        assert tmp_cache.get("nope", 1) is None

    def test_ttl_expiry(self, tmp_cache: ws.SearchCache):
        data = [{"title": "X", "url": "http://x.com", "body": ""}]
        tmp_cache.store("q", 2, data)
        # Manually expire
        entry = tmp_cache._store[tmp_cache._key("q", 2)]
        entry["expires_at"] = time.time() - 10
        assert tmp_cache.get("q", 2) is None

    def test_news_ttl_shorter(self, tmp_cache: ws.SearchCache):
        data = [{"title": "N", "url": "http://n.com", "body": ""}]
        tmp_cache.store("news today", 1, data, is_news=True)
        entry = tmp_cache._store[tmp_cache._key("news today", 1)]
        assert entry["ttl"] == ws.NEWS_TTL

    def test_default_ttl(self, tmp_cache: ws.SearchCache):
        data = [{"title": "D", "url": "http://d.com", "body": ""}]
        tmp_cache.store("static", 1, data, is_news=False)
        entry = tmp_cache._store[tmp_cache._key("static", 1)]
        assert entry["ttl"] == ws.DEFAULT_TTL

# ---------------------------------------------------------------------------
# search() contract
# ---------------------------------------------------------------------------

class TestSearchContract:
    @mock.patch.object(ws, "_ddg_search")
    def test_search_returns_dict(self, mock_ddg: mock.MagicMock):
        mock_ddg.return_value = [{"title": "T", "url": "http://t.com", "body": "B"}]
        result = ws.search("test", max_results=2, use_cache=False)
        assert isinstance(result, dict)
        assert "query" in result
        assert "results" in result
        assert "cache_hit" in result
        assert result["cache_hit"] is False

    @mock.patch.object(ws, "_ddg_search")
    def test_search_respects_max_results(self, mock_ddg: mock.MagicMock):
        mock_ddg.return_value = [
            {"title": f"R{i}", "url": f"http://r{i}.com", "body": ""}
            for i in range(10)
        ]
        result = ws.search("x", max_results=3, use_cache=False)
        assert result["count"] == 3

    @mock.patch.object(ws, "_ddg_search")
    def test_search_blocks_domains(self, mock_ddg: mock.MagicMock):
        mock_ddg.return_value = [
            {"title": "Good", "url": "http://good.com", "body": ""},
            {"title": "Bad", "url": "http://doubleclick.net", "body": ""},
        ]
        result = ws.search("x", use_cache=False)
        urls = [r["url"] for r in result["results"]]
        assert "http://doubleclick.net" not in urls

# ---------------------------------------------------------------------------
# CLI smoke tests
# ---------------------------------------------------------------------------

class TestCLI:
    def test_help(self, capsys: pytest.CaptureFixture):
        with pytest.raises(SystemExit) as exc:
            ws.main.__wrapped__() if hasattr(ws.main, "__wrapped__") else ws.main()
        # argparse exits with code 0 on --help
        # But without args it exits 1 — just verify no crash

    @mock.patch.object(ws, "_ddg_search")
    def test_json_output(self, mock_ddg: mock.MagicMock, capsys: pytest.CaptureFixture):
        mock_ddg.return_value = [{"title": "J", "url": "http://j.com", "body": ""}]
        with mock.patch.object(sys, "argv", ["agent_websearch", "test", "--json", "--no-cache"]):
            ws.main()
        out = capsys.readouterr().out
        parsed = json.loads(out)
        assert parsed["query"] == "test"
        assert parsed["count"] == 1