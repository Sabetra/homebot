"""Tests for schema-first diagram backend selection in AgentToolkit."""

from agent_toolkit import AgentToolkit


def _toolkit_for_helpers() -> AgentToolkit:
    # We only call pure helper methods, so skipping full __init__ is safe in this test.
    return AgentToolkit.__new__(AgentToolkit)


def test_select_diagram_backend_prefers_explicit_backend() -> None:
    toolkit = _toolkit_for_helpers()
    backend = toolkit._select_diagram_backend({"type": "network", "backend": "graphviz"})
    assert backend == "graphviz"


def test_select_diagram_backend_uses_structural_graphviz_signals() -> None:
    toolkit = _toolkit_for_helpers()
    backend = toolkit._select_diagram_backend(
        {
            "type": "network",
            "graph_type": "digraph",
            "nodes": [{"id": "A"}, {"id": "B"}],
            "edges": [{"source": "A", "target": "B"}],
        }
    )
    assert backend == "graphviz"


def test_select_diagram_backend_keeps_native_when_only_title_contains_keywords() -> None:
    toolkit = _toolkit_for_helpers()
    backend = toolkit._select_diagram_backend(
        {
            "type": "network",
            "title": "Dependency map for modules",
            "nodes": [{"id": "A"}, {"id": "B"}],
            "edges": [{"source": "A", "target": "B"}],
        }
    )
    assert backend == "native"


def test_prepare_graphviz_description_converts_steps_sequence() -> None:
    toolkit = _toolkit_for_helpers()
    prepared = toolkit._prepare_graphviz_description(
        {
            "type": "flowchart",
            "title": "Flow",
            "steps": [
                {"id": "start", "label": "Start"},
                {"id": "check", "label": "Check"},
                {"id": "end", "label": "End"},
            ],
        }
    )

    assert prepared["type"] == "graphviz"
    assert prepared["graph_type"] == "digraph"
    assert prepared["nodes"][0]["id"] == "start"
    assert prepared["edges"][0] == {"source": "start", "target": "check"}
