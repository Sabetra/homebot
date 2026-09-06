from types import SimpleNamespace

import wellbeing_session_interface as psi_module


def test_wellbeing_rag_bootstrap_is_lazy_and_idempotent(monkeypatch):
    bootstrap_calls = []

    class FakeRagBootstrapper:
        def bootstrap_into_rag(self, rag_manager):
            bootstrap_calls.append(rag_manager)
            return 1

    class FakeWellbeingPipeline:
        def __init__(self):
            self.rag_bootstrapper = FakeRagBootstrapper()

    class FakeContextBuilder:
        def build(self, request):
            return SimpleNamespace(
                context={"context_token_estimate": 0},
                sources_used=[],
                token_estimate=0,
                duration_ms=0.0,
                builder_version="test",
            )

    monkeypatch.setattr(
        psi_module,
        "WellbeingPipeline",
        FakeWellbeingPipeline,
    )
    monkeypatch.setattr(psi_module, "WELLBEING_PIPELINE_AVAILABLE", True)
    monkeypatch.setattr(psi_module, "WORKFLOW_AVAILABLE", False)
    monkeypatch.setattr(psi_module, "USE_WORKFLOW_GRAPH", False)
    monkeypatch.setattr(psi_module, "USE_REAL_LANGGRAPH", False)
    monkeypatch.setattr(psi_module, "LANGGRAPH_AVAILABLE", False)
    monkeypatch.setattr(psi_module, "build_langgraph_session_graph", None)

    fake_store = object()
    monkeypatch.setattr("agent.tools.get_global_rag_store", lambda: fake_store)

    iface = psi_module.WellbeingSessionInterface.__new__(psi_module.WellbeingSessionInterface)
    iface._wellbeing_pipeline = FakeWellbeingPipeline()
    iface._wellbeing_rag_bootstrapped = False
    iface.context_builder = FakeContextBuilder()
    iface.use_v2_context_builder = False

    result = iface._build_comprehensive_user_context("user-1", "session-1", "Hallo")
    assert result["context_token_estimate"] == 0
    assert bootstrap_calls == [fake_store]

    iface._build_comprehensive_user_context("user-1", "session-1", "Noch einmal")
    assert bootstrap_calls == [fake_store]
