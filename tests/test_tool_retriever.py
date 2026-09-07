"""
Test suite für das hybride Tool-Retrieval (BM25 + Cosine → RRF) — agent/tool_retriever.py

Coverage (2026-08-24):
- rrf_fuse(): Fusion-Logik, Tiebreaks, Determinismus (rein, ohne Modell)
- ToolRetriever.rank(): Core-Prefix-Garantie, top_k, leere Queries,
  unbekannte Kandidaten, Alias retrieve()
- Degradations-Pfade (explizit, nie still): Modell nicht ladbar, Encode-Fehler,
  rank_bm25 nicht importierbar
- get_tool_retriever(): Singleton-Semantik, lazy Model-Loading
- ReActAgent._apply_tool_retrieval(): Pool-Einschränkung (core + top_k),
  Pass-through kleiner Pools, Exception-Safety, Finance-Core-Erhalt

Design: deterministischer FakeEmbedder (Keyword-Vektoren, kein echtes Modell).
"""

from __future__ import annotations

import sys
from typing import Any, Dict, List, Optional

import pytest

import agent.tool_retriever as tr
from agent.tool_retriever import ToolRetriever, get_tool_retriever, rrf_fuse


def _schema(name: str, desc: str) -> Dict[str, Any]:
    return {"function": {"name": name, "description": desc}}


# ── Deterministisches Fake-Embedding-Modell ─────────────────────────────────
class FakeEmbedder:
    """Deterministische 'Embeddings': 4-D-Keyword-Vektoren, kein echtes Modell.

    - ``fail_query_encode``: encode mit exakt 1 Text (Query-Pfad) wirft
      → Cosine-Pfad degradiert auf BM25-only.
    - ``load_fails``: load_model() liefert False → Matrix-Build deaktiviert.
    """

    DIM = 4
    KEYWORDS = (("pdf", 0), ("audio", 1), ("web", 2), ("rechen", 3))

    def __init__(self, fail_query_encode: bool = False, load_fails: bool = False):
        self.fail_query_encode = fail_query_encode
        self.load_fails = load_fails
        self._loaded = False
        self.matrix_builds = 0

    def is_loaded(self) -> bool:
        return self._loaded

    def load_model(self) -> bool:
        if self.load_fails:
            return False
        self._loaded = True
        return True

    def _vector(self, text: str) -> List[float]:
        t = str(text).lower()
        v = [0.0] * self.DIM
        for kw, idx in self.KEYWORDS:
            if kw in t:
                v[idx] += 1.0
        v[-1] += 0.001  # Baseline → keine Nullvektoren
        return v

    def encode(self, texts, batch_size: Optional[int] = None):
        if self.fail_query_encode and len(texts) == 1:
            raise RuntimeError("simulated encode failure (query)")
        if len(texts) > 1:
            self.matrix_builds += 1
        return [self._vector(t) for t in texts]


# ── Registries (unterschiedliche Name-Tupel → verschiedene Singleton-Instanzen) ─
FAKE_TOOLS: List[Dict[str, Any]] = [
    _schema("pdf_extract", "Extract text and tables from PDF documents (pdf)"),
    _schema("audio_transcribe", "Transcribe audio files to text (audio)"),
    _schema("web_search", "Search the web and internet (web, browser)"),
    _schema("calculator", "Calculate math expressions (rechen, math)"),
    _schema("code_executor", "Run python programs and scripts"),
    _schema("rag_search", "Search the local knowledge base (documents)"),
    _schema("canvas_draw", "Draw charts and graphics (grafik)"),
    _schema("file_reader", "Read local files (datei)"),
    _schema("file_writer", "Write local files (datei)"),
    _schema("list_directory", "List directory entries (ordner)"),
]
FAKE_NAMES = [s["function"]["name"] for s in FAKE_TOOLS]

# 18 Tools > MIN_POOL (12) → Retrieval-Narrowing ist in den Integrationstests aktiv.
BIG_REGISTRY = FAKE_TOOLS + [
    _schema(f"dummy_tool_{i}", f"Dummy filler tool number {i} (padding)")
    for i in range(1, 9)
]

# Finance-Registry: 15 Tools > MIN_POOL, mit genau einem Finance-Core-Tool.
FINANCE_REGISTRY = (
    FAKE_TOOLS[:9]
    + [_schema("finance_sql_query", "Query the local finance database (read-only)")]
    + [
        _schema(f"fin_dummy_{i}", f"Finance filler tool {i} (padding)")
        for i in range(1, 6)
    ]
)


@pytest.fixture(autouse=True)
def _reset_retriever_singleton():
    """Modul-Singleton pro Test isolieren (frische Instanz je Test)."""
    tr._retriever = None
    tr._retriever_key = None
    yield
    tr._retriever = None
    tr._retriever_key = None


@pytest.fixture()
def fake_embedder(monkeypatch):
    """Embedding-Factory monkeypatchen → deterministische Vektoren, kein Modell."""
    import utils.embedding_singleton as emb

    fake = FakeEmbedder()
    monkeypatch.setattr(emb, "get_embedding_model", lambda: fake)
    return fake


# ═══════════════════════════════════════════════════════════════════════════
# 1. rrf_fuse (rein, ohne Modell)
# ═══════════════════════════════════════════════════════════════════════════


class TestRRFFuse:
    def test_empty_rankings_returns_empty(self):
        assert rrf_fuse([]) == []
        assert rrf_fuse([[], []]) == []

    def test_single_ranking_preserves_order(self):
        assert rrf_fuse([["a", "b", "c"]]) == ["a", "b", "c"]

    def test_high_rank_in_both_lists_wins(self):
        # x ist in beiden Listen ganz oben → höchste RRF-Summe.
        r = rrf_fuse([["x", "y", "z", "w"], ["x", "z", "y", "w"]])
        assert r[0] == "x"
        assert set(r) == {"x", "y", "z", "w"}

    def test_tie_broken_by_tiebreak_order(self):
        # a und b in beiden Listen (vertauschte Positionen) → gleiche RRF-Summe.
        r = rrf_fuse([["a", "b"], ["b", "a"]], tiebreak_order=["a", "b"])
        assert r == ["a", "b"]

    def test_tie_without_tiebreak_stays_stable(self):
        # Ohne Tiebreak: stabile Einfügereihenfolge (hier: Listenreihenfolge).
        assert rrf_fuse([["b"], ["a"]]) == ["b", "a"]


# ═══════════════════════════════════════════════════════════════════════════
# 2. ToolRetriever-Basics + rank()-Semantik (mit Fake-Modell)
# ═══════════════════════════════════════════════════════════════════════════


class TestToolRetrieverBasics:
    def test_names_follow_registry_order(self):
        r = get_tool_retriever(FAKE_TOOLS)
        assert r._names == FAKE_NAMES

    def test_docs_combine_name_and_description(self):
        r = get_tool_retriever(FAKE_TOOLS)
        assert r._docs["pdf_extract"].startswith("pdf_extract.")
        assert "PDF documents" in r._docs["pdf_extract"]

    def test_lazy_no_model_load_before_rank(self, fake_embedder):
        r = get_tool_retriever(FAKE_TOOLS)
        assert r._matrix is None
        assert r._matrix_failed is False
        assert fake_embedder._loaded is False  # load_model() noch NICHT aufgerufen
        assert fake_embedder.matrix_builds == 0

    def test_matrix_built_once_across_rank_calls(self, fake_embedder):
        r = get_tool_retriever(FAKE_TOOLS)
        r.rank("pdf", candidates=FAKE_NAMES, top_k=3, core=[])
        r.rank("audio", candidates=FAKE_NAMES, top_k=3, core=[])
        assert fake_embedder.matrix_builds == 1  # nach erstem Build gecacht
        assert r._matrix is not None


class TestRank:
    def test_core_always_prefix_in_pool_order(self, fake_embedder):
        r = get_tool_retriever(FAKE_TOOLS)
        core = ["pdf_extract", "web_search", "calculator"]
        ranked = r.rank("anything at all", candidates=FAKE_NAMES, top_k=4, core=core)
        assert ranked[:3] == ["pdf_extract", "web_search", "calculator"]
        assert set(ranked) == set(FAKE_NAMES)
        assert len(ranked) == len(FAKE_NAMES)

    def test_core_prefix_independent_of_query(self, fake_embedder):
        r = get_tool_retriever(FAKE_TOOLS)
        ranked = r.rank(
            "transcribe this audio file",
            candidates=FAKE_NAMES,
            top_k=4,
            core=["web_search", "calculator"],
        )
        assert ranked[:2] == ["web_search", "calculator"]
        assert "audio_transcribe" in ranked  # bleibt im Pool

    def test_relevance_ranks_matching_tool_first(self, fake_embedder):
        r = get_tool_retriever(FAKE_TOOLS)
        ranked = r.rank(
            "extract text from the pdf document",
            candidates=FAKE_NAMES,
            top_k=5,
            core=[],
        )
        assert ranked[0] == "pdf_extract"  # BM25 UND Cosine einigen sich

    def test_empty_query_returns_pool_order(self, fake_embedder):
        r = get_tool_retriever(FAKE_TOOLS)
        ranked = r.rank("", candidates=FAKE_NAMES, top_k=5, core=[])
        assert ranked == FAKE_NAMES

    def test_whitespace_query_returns_pool_order(self, fake_embedder):
        r = get_tool_retriever(FAKE_TOOLS)
        ranked = r.rank("   ", candidates=FAKE_NAMES, top_k=5, core=[])
        assert ranked == FAKE_NAMES

    def test_unknown_candidate_is_safe(self, fake_embedder):
        """Name, der nicht registriert ist, muss das Ranking nicht brechen."""
        r = get_tool_retriever(FAKE_TOOLS)
        ranked = r.rank(
            "extract pdf",
            candidates=["pdf_extract", "ghost_tool_not_registered"],
            top_k=5,
            core=["pdf_extract"],
        )
        assert "pdf_extract" in ranked
        assert "ghost_tool_not_registered" in ranked

    def test_top_k_limits_selected_rest_kept_in_pool_order(self, fake_embedder):
        r = get_tool_retriever(FAKE_TOOLS)
        ranked = r.rank("pdf document", candidates=FAKE_NAMES, top_k=1, core=[])
        assert ranked[0] == "pdf_extract"
        rest = [n for n in FAKE_NAMES if n != "pdf_extract"]
        assert ranked[1:] == rest

    def test_top_k_zero_keeps_full_pool_after_core(self, fake_embedder):
        r = get_tool_retriever(FAKE_TOOLS)
        ranked = r.rank(
            "pdf document", candidates=FAKE_NAMES, top_k=0, core=["calculator"]
        )
        assert ranked[0] == "calculator"
        assert ranked[1:] == [n for n in FAKE_NAMES if n != "calculator"]

    def test_rank_deterministic_across_instances(self, fake_embedder):
        q = "how do I extract a table from a pdf?"
        a = ToolRetriever(FAKE_TOOLS).rank(q, candidates=FAKE_NAMES, top_k=5, core=[])
        b = ToolRetriever(FAKE_TOOLS).rank(q, candidates=FAKE_NAMES, top_k=5, core=[])
        assert a == b

    def test_retrieve_alias_matches_rank(self, fake_embedder):
        r = get_tool_retriever(FAKE_TOOLS)
        kwargs = dict(candidates=FAKE_NAMES, top_k=5, core=["web_search"])
        assert r.retrieve("pdf", **kwargs) == r.rank("pdf", **kwargs)


# ═══════════════════════════════════════════════════════════════════════════
# 3. Explizite Degradation (nie still, nie leer)
# ═══════════════════════════════════════════════════════════════════════════


class TestRankDegradation:
    def test_cosine_failure_falls_back_to_bm25(self, monkeypatch):
        import utils.embedding_singleton as emb

        fake = FakeEmbedder(fail_query_encode=True)
        monkeypatch.setattr(emb, "get_embedding_model", lambda: fake)
        r = get_tool_retriever(FAKE_TOOLS)
        ranked = r.rank(
            "extract text from the pdf document",
            candidates=FAKE_NAMES,
            top_k=5,
            core=[],
        )
        assert ranked[0] == "pdf_extract"  # BM25-only, trotzdem relevant
        assert set(ranked) == set(FAKE_NAMES)

    def test_model_unavailable_bm25_only(self, monkeypatch):
        import utils.embedding_singleton as emb

        fake = FakeEmbedder(load_fails=True)
        monkeypatch.setattr(emb, "get_embedding_model", lambda: fake)
        r = get_tool_retriever(FAKE_TOOLS)
        ranked = r.rank("pdf document", candidates=FAKE_NAMES, top_k=5, core=[])
        assert ranked[0] == "pdf_extract"
        assert r._matrix_failed is True  # kein Retry nach Fehlschlag

    def test_rank_bm25_missing_cosine_only(self, monkeypatch, fake_embedder):
        monkeypatch.setitem(sys.modules, "rank_bm25", None)
        r = get_tool_retriever(FAKE_TOOLS)
        ranked = r.rank("extract pdf", candidates=FAKE_NAMES, top_k=5, core=[])
        assert ranked[0] == "pdf_extract"  # Cosine-only

    def test_both_signals_dead_returns_unchanged_pool(self, monkeypatch):
        import utils.embedding_singleton as emb

        fake = FakeEmbedder(fail_query_encode=True)
        monkeypatch.setattr(emb, "get_embedding_model", lambda: fake)
        monkeypatch.setitem(sys.modules, "rank_bm25", None)
        r = get_tool_retriever(FAKE_TOOLS)
        ranked = r.rank("xyzzy", candidates=FAKE_NAMES, top_k=5, core=[])
        assert ranked == FAKE_NAMES  # expliziter Fallback, keine Silent-Liste


# ═══════════════════════════════════════════════════════════════════════════
# 4. Singleton-Semantik
# ═══════════════════════════════════════════════════════════════════════════


class TestSingleton:
    def test_same_registry_returns_same_instance(self):
        assert get_tool_retriever(FAKE_TOOLS) is get_tool_retriever(FAKE_TOOLS)

    def test_changed_registry_new_instance_original_restored(self):
        a = get_tool_retriever(FAKE_TOOLS)
        b = get_tool_retriever(BIG_REGISTRY)
        assert b is not a
        assert get_tool_retriever(FAKE_TOOLS) is a
        assert b._names != a._names

    def test_empty_registry_retriever_is_safe(self):
        r = get_tool_retriever([])
        assert r._names == []
        assert r.rank("anything", candidates=[], top_k=5, core=[]) == []


# ═══════════════════════════════════════════════════════════════════════════
# 5. Integration: ReActAgent._apply_tool_retrieval (Pool-Einschränkung)
# ═══════════════════════════════════════════════════════════════════════════


class _RetrievalStub:
    """Minimaler Stub: bindet nur die zu testende Methode."""

    def __init__(self, registry: List[Dict[str, Any]]):
        from agent.react_agent import ReActAgent

        self.tool_schemas = registry
        self._apply_tool_retrieval = ReActAgent._apply_tool_retrieval.__get__(self)


CORE_NAMES = {"web_search", "rag_search", "calculator", "code_executor"}


class TestApplyToolRetrieval:
    def test_small_pool_passes_through_unchanged(self, fake_embedder):
        stub = _RetrievalStub(FAKE_TOOLS)  # 10 ≤ MIN_POOL(12)
        active = list(FAKE_NAMES)
        result = stub._apply_tool_retrieval({"query": "pdf"}, active)
        assert result is active  # Identität, keine Kopie, kein Narrowing

    def test_large_pool_narrowed_to_core_plus_top_k(self, fake_embedder):
        import agent.react_agent as ra

        stub = _RetrievalStub(BIG_REGISTRY)  # 18 > MIN_POOL(12)
        active = [s["function"]["name"] for s in BIG_REGISTRY]
        result = stub._apply_tool_retrieval(
            {"query": "extract text from the pdf document"}, active
        )
        keep = len(CORE_NAMES) + ra._TOOL_RETRIEVAL_TOP_K
        assert len(result) == keep  # 4 Core + 8 gerankt
        assert CORE_NAMES <= set(result)
        assert set(result) <= set(active)
        assert len(set(result)) == len(result)  # keine Duplikate
        # Core-Präfix in Pool-Reihenfolge:
        assert result[:4] == ["web_search", "calculator", "code_executor", "rag_search"]
        # Relevantes Tool unter den gewählten Non-Core-Tools:
        assert "pdf_extract" in result[:5]

    def test_weak_query_still_narrows_keeps_core(self, fake_embedder):
        import agent.react_agent as ra

        stub = _RetrievalStub(BIG_REGISTRY)
        active = [s["function"]["name"] for s in BIG_REGISTRY]
        result = stub._apply_tool_retrieval({"query": "xyzzy"}, active)
        keep = len(CORE_NAMES) + ra._TOOL_RETRIEVAL_TOP_K
        assert len(result) == keep
        assert CORE_NAMES <= set(result)

    def test_missing_query_is_safe(self, fake_embedder):
        import agent.react_agent as ra

        stub = _RetrievalStub(BIG_REGISTRY)
        active = [s["function"]["name"] for s in BIG_REGISTRY]
        result = stub._apply_tool_retrieval({}, active)
        keep = len(CORE_NAMES) + ra._TOOL_RETRIEVAL_TOP_K
        assert len(result) == keep
        assert CORE_NAMES <= set(result)

    def test_finance_core_tool_preserved_when_narrowing(self, fake_embedder):
        import agent.react_agent as ra

        stub = _RetrievalStub(FINANCE_REGISTRY)  # 15 > MIN_POOL(12)
        active = [s["function"]["name"] for s in FINANCE_REGISTRY]
        result = stub._apply_tool_retrieval(
            {"query": "what is my account balance"}, active
        )
        core = CORE_NAMES | {"finance_sql_query"}
        keep = len(core) + ra._TOOL_RETRIEVAL_TOP_K
        assert len(result) == keep
        assert "finance_sql_query" in result[:5]  # Core-Präfix bleibt vorn
        assert core <= set(result)

    def test_retrieval_exception_keeps_active_pool(self, monkeypatch):
        import agent.tool_retriever as tr_mod

        def _boom(schemas):
            raise RuntimeError("simulated retriever failure")

        monkeypatch.setattr(tr_mod, "get_tool_retriever", _boom)
        stub = _RetrievalStub(BIG_REGISTRY)
        active = [s["function"]["name"] for s in BIG_REGISTRY]
        result = stub._apply_tool_retrieval({"query": "pdf"}, active)
        assert result is active  # explizit, kein Silent-Verhalten