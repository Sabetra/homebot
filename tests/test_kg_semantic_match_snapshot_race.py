import numpy as np

from agent.rag_store.core.search import SearchManager


class _DummyDBManager:
    def __init__(self) -> None:
        self.db_path = ":memory:"


class _InvalidatingEmbeddingManager:
    def __init__(self, search_manager: SearchManager) -> None:
        self.dimension = 2
        self._search_manager = search_manager

    def embed_texts(self, texts):
        # Simulate concurrent KG write path invalidating the index while
        # semantic match is already in-flight.
        self._search_manager.invalidate_entity_index()
        return np.asarray([[1.0, 0.0]], dtype=np.float32)


def test_semantic_match_uses_snapshot_during_invalidation():
    mgr = SearchManager(
        db_manager=_DummyDBManager(),
        embedding_manager=None,
        embedding_dim=2,
        debug=True,
    )

    # Pre-built in-memory entity index
    mgr._entity_embeddings = np.asarray([[1.0, 0.0], [0.0, 1.0]], dtype=np.float32)
    mgr._entity_texts = ["alpha", "beta"]
    mgr._entity_index_built = True
    mgr._entity_index_version = 7

    mgr.embedding_manager = _InvalidatingEmbeddingManager(mgr)

    results = mgr._semantic_entity_match("alpha related", top_k=2)

    # The in-flight read must not fail even if invalidation occurs mid-flight.
    assert results
    assert results[0][0] == "alpha"

    # Invalidation still marks the index stale for subsequent rebuilds.
    assert mgr._entity_index_built is False
    assert mgr._entity_index_version == 8

    # Non-destructive invalidation keeps the snapshot data available to current read.
    assert isinstance(mgr._entity_embeddings, np.ndarray)
    assert mgr._entity_texts is not None
