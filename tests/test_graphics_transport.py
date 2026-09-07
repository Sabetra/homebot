"""Focused contract tests for locally generated chat graphics."""

from __future__ import annotations

import base64

import pytest
from pydantic import ValidationError

from agent.agent_types import AgentTrace, ToolCall, ToolResult
from agent.orchestrator import AgentOrchestrator
from agent.streaming_events import ChatRunResult, GraphicArtifact


def test_collect_graphics_normalizes_file_and_base64_results() -> None:
    encoded = base64.b64encode(b"png-bytes").decode("ascii")
    results = [
        ToolResult(
            tool="create_diagram",
            success=True,
            meta={"raw_payload": {
                "output_path": r"C:\tmp\diagram.png",
                "diagram_type": "comparison",
                "backend": "native",
            }},
        ),
        ToolResult(
            tool="code_executor",
            success=True,
            meta={"raw_payload": {"plot_base64": encoded, "plot_format": "png"}},
        ),
    ]

    graphics = AgentOrchestrator._collect_graphics(results)
    run_result = ChatRunResult(text="done", graphics=graphics)

    assert run_result.graphics[0].path == r"C:\tmp\diagram.png"
    assert run_result.graphics[0].diagram_type == "comparison"
    assert run_result.graphics[1].data_base64 == encoded
    assert run_result.graphics[1].backend == "code_executor"


def test_graphic_artifact_requires_exactly_one_payload() -> None:
    with pytest.raises(ValidationError):
        GraphicArtifact()
    with pytest.raises(ValidationError):
        GraphicArtifact(path="diagram.png", data_base64="abc")


def test_collect_graphics_infers_file_media_type() -> None:
    result = ToolResult(
        tool="create_diagram",
        success=True,
        meta={"raw_payload": {"output_path": r"C:\tmp\diagram.svg"}},
    )

    graphic = AgentOrchestrator._collect_graphics([result])[0]

    assert graphic["media_type"] == "image/svg+xml"


def test_graphics_only_run_returns_before_evidence_pipeline() -> None:
    orchestrator = object.__new__(AgentOrchestrator)
    result = ToolResult(
        tool="create_diagram",
        success=True,
        message="Diagramm erstellt",
        meta={"raw_payload": {"output_path": r"C:\tmp\diagram.png"}},
    )
    orchestrator.tools = type("Tools", (), {"run": lambda _self, _calls: [result]})()
    orchestrator._build_finance_grounding_block = lambda _results: None

    _, _, _, answer = orchestrator._execute_tools_with_rag_postprocessing(
        query="Erstelle ein Diagramm",
        planned_calls=[ToolCall(tool="create_diagram", parameters={})],
        trace=AgentTrace(),
        skip_web_search=False,
        rag_first_results=None,
        rag_result_count=0,
        rag_max_score=0.0,
    )

    assert answer is not None
    assert answer.text == "Diagramm erstellt"
    assert answer.graphics[0]["path"] == r"C:\tmp\diagram.png"