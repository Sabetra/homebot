"""Web-Search Routing Guard Tests (2026-08-21, P4)
===================================================

Regresstests für den Fehlerfall vom 2026-08-21: Das LLM (Gemma4 12B) hat
bei Web-/Benchmark-Fragen `list_directory(q=..., num=...)` "erfunden"
(Tool-Name aus dem Dateisystem-Profil + Parameter aus web_search), weil
(a) die Route `agent_no_rag` das web_search-Tool aus dem aktiven Schema
    entfernte, während der Prompt weiter "IMMER web_search" sagte und
(b) kein Guard Web-Parameter an FS-Tools als Fehlzuordnung erkannte.

Gedeckt:
  - suggest_web_search_for_fs_misuse()  (Erkennungslogik, pure Funktion)
  - AgentToolkit.execute_tool() Guard   (strukturierte Korrektur-Antwort)
  - ReactAgent._tool_schemas_for_state  (web_search bleibt unter agent_no_rag)
"""

import shutil
import sys
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest


# ═══════════════════════════════════════════════════════════════════════════════
# 1. Pure Erkennungslogik: suggest_web_search_for_fs_misuse
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.fixture(scope="module")
def suggest():
    from agent_toolkit import suggest_web_search_for_fs_misuse

    return suggest_web_search_for_fs_misuse


class TestSuggestWebSearchForFsMisuse:
    """Trigger-Logik des P2-Guards (ohne Toolkit-Instanz)."""

    def test_list_directory_with_q_and_num_triggers(self, suggest):
        """Original-Fehlerfall: list_directory(q=..., num=...) → Korrektur."""
        result = suggest("list_directory", {"q": "bitcoin price 2026", "num": 5})
        assert result is not None
        assert result["query"] == "bitcoin price 2026"
        assert result["num_results"] == 5

    def test_search_files_with_web_like_params_triggers(self, suggest):
        """search_files mit query/num → ebenfalls Web-Parameter-Muster."""
        result = suggest("search_files", {"query": "champions league 2025 participants", "num": 3})
        assert result is not None
        assert result["query"] == "champions league 2025 participants"
        assert result["num_results"] == 3

    def test_file_reader_with_q_triggers(self, suggest):
        """file_reader mit q/num → Guard greift."""
        result = suggest("file_reader", {"q": "aktuelles wetter berlin", "num": 1})
        assert result is not None
        assert result["query"] == "aktuelles wetter berlin"
        assert result["num_results"] == 1

    def test_num_results_key_variant(self, suggest):
        """num_results (statt num) wird erkannt und durchgereicht."""
        result = suggest("list_directory", {"query": "tesla quarterly earnings 2026", "num_results": 7})
        assert result is not None
        assert result["num_results"] == 7

    def test_uppercase_keys_still_trigger(self, suggest):
        """Keys werden fallunabhängig erkannt (LLM schreibt z.B. 'Q')."""
        result = suggest("list_directory", {"Q": "gold price today", "NUM": 2})
        assert result is not None
        assert result["query"] == "gold price today"
        assert result["num_results"] == 2

    def test_path_present_no_trigger(self, suggest):
        """Pfad vorhanden → mutmaßliche FS-Operation, kein Guard."""
        assert suggest("list_directory", {"q": "weird", "path": "C:/Projekt"}) is None

    def test_root_path_and_pattern_no_trigger(self, suggest):
        """search_files mit legitimen Parametern bleibt unangetastet."""
        result = suggest(
            "search_files",
            {"root_path": "C:/Projekt", "pattern": "*.py", "q": "accidentally mixed"},
        )
        assert result is None

    def test_file_path_present_no_trigger(self, suggest):
        """file_reader mit file_path ist eine legitime FS-Operation."""
        assert suggest("file_reader", {"file_path": "C:/tmp/a.txt", "q": "x"}) is None

    def test_pattern_only_no_trigger(self, suggest):
        """'pattern' zählt als Pfad-ähnlicher Key → kein Guard."""
        assert suggest("search_files", {"pattern": "*.md", "num": 5}) is None

    def test_non_guarded_tool_no_trigger(self, suggest):
        """calculator/code_executor/web_search sind nicht geschützt."""
        assert suggest("calculator", {"q": "2+2"}) is None
        assert suggest("code_executor", {"query": "print(1)", "num": 3}) is None
        assert suggest("web_search", {"q": "bitcoin", "num": 5}) is None

    def test_no_web_like_keys_no_trigger(self, suggest):
        """Nur legitime FS-Parameter → kein Guard."""
        assert suggest("list_directory", {"path": "C:/tmp", "max_depth": 1}) is None

    def test_non_dict_parameters_no_trigger(self, suggest):
        """Defensive: keine Dict-Parameter → kein Guard, kein Crash."""
        assert suggest("list_directory", "bitcoin price") is None  # type: ignore[arg-type]

    def test_out_of_range_num_omitted(self, suggest):
        """num außerhalb 1..10 wird nicht übernommen (Schema-Konformität)."""
        result = suggest("list_directory", {"q": "bitcoin price", "num": 99})
        assert result is not None
        assert result["query"] == "bitcoin price"
        assert "num_results" not in result

    def test_non_numeric_num_omitted(self, suggest):
        """Nicht numerisches num wird still verworfen, Query bleibt."""
        result = suggest("search_files", {"query": "news", "num": "viel"})
        assert result is not None
        assert result["query"] == "news"
        assert "num_results" not in result


# ═══════════════════════════════════════════════════════════════════════════════
# 2. execute_tool(): strukturierte Korrektur-Antwort (LLM-sichtbar)
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.fixture(scope="module")
def toolkit():
    """Echte Toolkit-Instanz (gleiche Praxis wie die FS-Integrationstests).

    Der Guard feuert VOR dem Dispatch, daher werden bei den Guard-Tests
    weder Web-Suche noch Dateisystem touchiert.
    """
    from agent_toolkit import AgentToolkit

    return AgentToolkit()


@pytest.fixture()
def sandbox_tmp_dir():
    """Deterministisches Temp-Verzeichnis IN der Sandbox (Workspace-Root).

    pytest's tmp_path liegt hier unter C:\\Temp -- auERhalb der PathSandbox
    (Base: C:\\Projekt) und wuerde mit sandbox_error abgelehnt.
    Fuer "gueltige Aufrufe" brauchen wir einen Pfad, den die Sandbox
    erlaubt: ein Temp-Verzeichnis unter der Workspace-Root, danach cleanup.
    """
    import tempfile

    workspace_root = Path(__file__).resolve().parent.parent
    d = Path(tempfile.mkdtemp(prefix="guard_fs_valid_", dir=str(workspace_root)))
    yield d
    shutil.rmtree(d, ignore_errors=True)


class TestExecuteToolMismatchGuard:
    """execute_tool liefert bei FS-Missuse die web_search-Korrektur."""

    def test_list_directory_mismatch_returns_structured_correction(self, toolkit):
        """Original-Fehlerfall → tool_param_mismatch + suggested_tool."""
        result = toolkit.execute_tool(
            "list_directory", {"q": "bitcoin price 2026", "num": 5}
        )
        assert result["success"] is False
        assert result["error_class"] == "tool_param_mismatch"
        assert result["suggested_tool"] == "web_search"
        assert result["suggested_parameters"]["query"] == "bitcoin price 2026"
        assert result["suggested_parameters"]["num_results"] == 5
        assert result["action"] == "call_suggested_tool"
        # Fehlermeldung ist für das LLM formuliert.
        assert "web_search" in result["error"]
        assert "q" in result["error"]

    def test_search_files_mismatch_returns_structured_correction(self, toolkit):
        result = toolkit.execute_tool(
            "search_files", {"query": "champions league 2025", "num": 3}
        )
        assert result["error_class"] == "tool_param_mismatch"
        assert result["suggested_tool"] == "web_search"
        assert result["suggested_parameters"]["query"] == "champions league 2025"
        assert result["suggested_parameters"]["num_results"] == 3

    def test_file_reader_mismatch_returns_structured_correction(self, toolkit):
        result = toolkit.execute_tool("file_reader", {"q": "news today", "num": 2})
        assert result["error_class"] == "tool_param_mismatch"
        assert result["suggested_tool"] == "web_search"

    def test_valid_list_directory_call_not_guarded(self, toolkit, sandbox_tmp_dir):
        """Legitime FS-Aufrufe dürfen NICHT als Mismatch markiert werden."""
        (sandbox_tmp_dir / "probe.txt").write_text("hello", encoding="utf-8")
        result = toolkit.execute_tool(
            "list_directory", {"path": str(sandbox_tmp_dir), "max_depth": 1}
        )
        assert result.get("success") is True
        assert result.get("error_class") != "tool_param_mismatch"

    def test_valid_search_files_call_not_guarded(self, toolkit, sandbox_tmp_dir):
        """search_files mit root_path+pattern bleibt unangetastet.

        Achtung: `pattern` ist ein REGEX (path_sandbox.search_files_safe nutzt
        re.compile), kein Glob -- daher `.*\\.py` statt `*.py`.
        """
        (sandbox_tmp_dir / "x.py").write_text("x = 1", encoding="utf-8")
        result = toolkit.execute_tool(
            "search_files", {"root_path": str(sandbox_tmp_dir), "pattern": ".*\\.py"}
        )
        assert result.get("success") is True
        assert result.get("error_class") != "tool_param_mismatch"


# ═══════════════════════════════════════════════════════════════════════════════
# 3. ReactAgent._tool_schemas_for_state: Routing vs. Tool-Verfügbarkeit
# ═══════════════════════════════════════════════════════════════════════════════


class _SchemaStub:
    """Trägt nur die Attribute, die _tool_schemas_for_state liest.

    Vermeidet, eine komplette ReactAgent zu instantiieren (Modell-Laden,
    LangGraph, RAG) -- die Methode ist ein reiner Filter über self.tool_schemas.

    2026-08-24 (Progressive Disclosure): trägt zusätzlich die Pool-Logik
    (_resolve_tool_pool_names, echte Klassennbindung). _apply_tool_retrieval
    (Phase 2, Hybrid-Retrieval) ist hier bewusst ein Identity-Passthrough --
    diese Testklasse prüft Routing-Semantik, kein Retrieval.
    """

    def __init__(self, schemas: List[Dict[str, Any]]):
        from agent.react_agent import ReActAgent

        self.tool_schemas = schemas
        self._resolve_tool_pool_names = ReActAgent._resolve_tool_pool_names.__get__(self)

        def _identity_retrieval(state, active):
            return active

        self._apply_tool_retrieval = _identity_retrieval


def _names(schemas) -> set:
    return {s["function"]["name"] for s in schemas}


@pytest.fixture(scope="module")
def method():
    from agent.react_agent import ReActAgent

    return ReActAgent._tool_schemas_for_state


@pytest.fixture(scope="module")
def stub():
    from agent.tool_schemas import get_tool_schemas

    return _SchemaStub(get_tool_schemas())


class TestToolSchemasForState:
    """web_search bleibt verfügbar; nur rag_search entfällt bei agent_no_rag."""

    def test_baseline_route_keeps_all_tools(self, method, stub):
        """Route 'agent' (Default): vollständiges Tool-Set inkl. web_search."""
        result = method(
            stub, {"route": "agent", "query": "Wie viele Spieler Champions League 2025?"}
        )
        names = _names(result)
        assert "web_search" in names
        assert "rag_search" in names
        assert "list_directory" in names

    def test_agent_no_rag_keeps_web_search(self, method, stub):
        """REGRESSION (2026-08-21): agent_no_rag entfernt NUR rag_search.

        Der ursprüngliche Bug: web_search wurde hier mit entfernt, das LLM
        verlor sein einziges Web-Tool und halluzinierte stattdessen
        list_directory(q=..., num=...).
        """
        result = method(
            stub,
            {
                "route": "agent_no_rag",
                "query": "Wie viele Spieler nahmen an der Champions League 2025 teil?",
            },
        )
        names = _names(result)
        assert "web_search" in names, (
            "web_search darf unter agent_no_rag NICHT entfernt werden"
        )
        assert "rag_search" not in names, "rag_search entfällt korrekt ohne RAG-Pfad"
        assert "list_directory" in names

    def test_agent_no_rag_code_profile_keeps_web_search(self, method, stub):
        """Code-Profil: code_executor + web_search, kein rag_search."""
        result = method(
            stub, {"route": "agent_no_rag", "query": "Schreib ein python skript zum umrechnen"}
        )
        names = _names(result)
        assert "code_executor" in names
        assert "web_search" in names
        assert "rag_search" not in names

    def test_web_search_schema_shape_intact(self, method, stub):
        """web_search-Schema bleibt funktionsfähig (parameters mit query)."""
        result = method(stub, {"route": "agent_no_rag", "query": "bitcoin kurs heute"})
        schema = next(s for s in result if s["function"]["name"] == "web_search")
        props = schema["function"]["parameters"]["properties"]
        assert "query" in props


# ═══════════════════════════════════════════════════════════════════════════════
# 4. End-to-End-Kette: Guard-Vorschlag → web_search-Schema-Kompatibilität
# ═══════════════════════════════════════════════════════════════════════════════


def test_guard_suggestion_is_valid_web_search_call():
    """Die suggested_parameters des Guards sind gültige web_search-Argumente.

    Schließt die Lücke zwischen Korrektur-Signal und aktuellem Schema:
    sonst würde das LLM den "Korrektur"-Vorschlag erneut ablehnen.
    """
    from agent.tool_schemas import get_tool_schemas
    from agent_toolkit import suggest_web_search_for_fs_misuse

    suggestion = suggest_web_search_for_fs_misuse(
        "list_directory", {"q": "bitcoin price 2026", "num": 5}
    )
    assert suggestion is not None

    web_schema = next(
        s for s in get_tool_schemas() if s["function"]["name"] == "web_search"
    )
    allowed = set(web_schema["function"]["parameters"]["properties"].keys())
    required = set(web_schema["function"]["parameters"].get("required", []))
    assert set(suggestion.keys()) <= allowed, (
        f"Guard-Vorschlag verwendet Keys, die web_search nicht kennt: "
        f"{set(suggestion.keys()) - allowed}"
    )
    assert required <= set(suggestion.keys()), (
        f"Required web_search-Keys fehlen im Vorschlag: {required - set(suggestion.keys())}"
    )
