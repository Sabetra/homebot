"""Contracts for internal code execution versus user-program delivery."""

from __future__ import annotations

from pathlib import Path

from agent.agent_types import AgentTrace, ToolCall, ToolResult
from agent.orchestrator import AgentOrchestrator
from agent.streaming_events import ChatRunResult
from agent_toolkit import AgentToolkit
from code_executor_engine import CodeExecutorEngine, ExecutionResult, StructuredError


def test_save_user_program_sanitizes_name_and_preserves_source(tmp_path: Path) -> None:
    engine = CodeExecutorEngine(sandbox_base_dir=str(tmp_path))
    source = "print('ready')\n"

    artifact = engine.save_user_program(source, "../My Tetris!!.py")

    artifact_path = Path(artifact["path"])
    assert artifact_path.parent == (tmp_path / "user_programs").resolve()
    assert artifact_path.name.startswith("My_Tetris_")
    assert artifact_path.suffix == ".py"
    assert artifact_path.read_text(encoding="utf-8") == source
    assert artifact["media_type"] == "text/x-python"


def test_internal_execution_does_not_create_user_program(tmp_path: Path) -> None:
    engine = CodeExecutorEngine(sandbox_base_dir=str(tmp_path))

    result = engine.execute("print(6 * 7)", auto_retry=False, auto_install=False)

    assert result.success
    assert result.stdout.strip() == "42"
    assert not (tmp_path / "user_programs").exists()


def test_toolkit_delivers_final_autofixed_source() -> None:
    final_source = "print('fixed')\n"

    class FakeEngine:
        saved: tuple[str, str | None] | None = None

        def execute(self, **_kwargs):
            return ExecutionResult(success=True, code_versions=["broken(", final_source])

        def save_user_program(self, code: str, name: str | None):
            self.saved = (code, name)
            return {"path": r"C:\code_sandbox\user_programs\app.py", "name": "app.py", "size": 15, "media_type": "text/x-python"}

    toolkit = object.__new__(AgentToolkit)
    toolkit._code_engine = FakeEngine()
    result = toolkit._code_executor({"code": "broken(", "deliver_to_user": True, "artifact_name": "app.py"})

    assert toolkit._code_engine.saved == (final_source, "app.py")
    assert result["files"][0]["name"] == "app.py"


def test_toolkit_never_delivers_failed_program() -> None:
    class FakeEngine:
        save_called = False

        def execute(self, **_kwargs):
            return ExecutionResult(
                success=False,
                error=StructuredError(error_type="SyntaxError", message="invalid syntax"),
                code_versions=["broken("],
            )

        def save_user_program(self, _code: str, _name: str | None):
            self.save_called = True
            raise AssertionError("failed code must not be delivered")

    toolkit = object.__new__(AgentToolkit)
    toolkit._code_engine = FakeEngine()
    result = toolkit._code_executor({"code": "broken(", "deliver_to_user": True, "artifact_name": "broken.py"})

    assert not result["success"]
    assert not toolkit._code_engine.save_called
    assert "files" not in result


def test_generated_program_survives_standard_completion_contract() -> None:
    program = {
        "path": r"C:\code_sandbox\user_programs\tetris.py",
        "name": "tetris.py",
        "size": 1234,
        "media_type": "text/x-python",
    }
    tool_result = ToolResult(
        tool="code_executor",
        success=True,
        message="Programm erfolgreich getestet",
        meta={"raw_payload": {"files": [program]}},
    )
    orchestrator = object.__new__(AgentOrchestrator)
    orchestrator.tools = type("Tools", (), {"run": lambda _self, _calls: [tool_result]})()
    orchestrator._build_finance_grounding_block = lambda _results: None

    _, _, _, answer = orchestrator._execute_tools_with_rag_postprocessing(
        query="Erstelle Tetris",
        planned_calls=[ToolCall(tool="code_executor", parameters={})],
        trace=AgentTrace(),
        skip_web_search=False,
        rag_first_results=None,
        rag_result_count=0,
        rag_max_score=0.0,
    )

    assert answer is not None
    run_result = ChatRunResult(text=answer.text, files=answer.files)
    assert run_result.files[0].name == "tetris.py"
    assert run_result.files[0].media_type == "text/x-python"