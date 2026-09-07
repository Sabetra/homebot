from types import SimpleNamespace

import agent.cross_encoder_reranker as adapter_module


class _FakeUpstream:
    def __init__(self):
        self._ensure_loaded_calls = 0
        self._model = SimpleNamespace(predict=lambda *args, **kwargs: [0.1, 0.9])
        self.model_name = "fake-model"
        self.max_length = 512
        self._available = False

    def _ensure_loaded(self):
        self._ensure_loaded_calls += 1
        self._available = True

    @property
    def is_available(self):
        return self._available


def test_legacy_lazy_init_and_model_proxy(monkeypatch):
    fake_upstream = _FakeUpstream()
    monkeypatch.setattr(adapter_module, "_get_canonical_reranker", lambda **kwargs: fake_upstream)

    reranker = adapter_module.CrossEncoderReranker()

    assert reranker.is_available is False
    reranker._lazy_init()
    assert fake_upstream._ensure_loaded_calls == 1
    assert reranker.is_available is True
    assert reranker.model.predict([["q", "p"]]) == [0.1, 0.9]
