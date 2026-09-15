from pathlib import Path
import sys

import pytest

# Ensure repository root is importable for direct pytest invocations.
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


@pytest.fixture(autouse=True)
def _isolate_model_loader_singleton():
    """Test-Isolation für das ModelLoader-Singleton (Root-Cause-Fix 2026-09-04).

    ``ModelLoader`` ist ein Singleton: ``ModelLoader.__new__(ModelLoader)``
    und ``ModelLoader()`` liefern IMMER dieselbe Instanz. Tests setzen auf
    ihr Instanz-Attribute (z. B. ``_render_chat_template``, ``_jinja_template``,
    ``_log_progress``) -- ohne Reset durchschlagen sie in alle folgenden
    Tests und machen die Suite lauffolgenrependent.

    Regression, die dies fixt: ``test_model_loader_streaming.py`` setzte ein
    ``_render_chat_template``-Lambda auf dem geteilten Singleton; alle
    ``_render_chat_template``-Tests in ``test_model_loader_chat_template_normalization.py``
    lieferten danach "prompt" statt das gerenderte Template (7 FAILED in
    Kombination, 11 PASSED solo).

    Bewusst NUR wirksam, wenn ``scripts.model_loader`` bereits importiert
    ist (``sys.modules``-Check, keine harten Imports): Das Modul zieht
    ``torch``/``llama_cpp`` nach -- für die vielen GPU-unabhängigen Tests
    darf die Isolation keinen Import auslösen.
    """
    module = sys.modules.get("scripts.model_loader")
    model_loader = getattr(module, "ModelLoader", None)
    if model_loader is None or not hasattr(model_loader, "_instance"):
        yield
        return
    model_loader._instance = None
    yield
    model_loader._instance = None


@pytest.fixture(autouse=True)
def _disable_chat_perf_telemetry(monkeypatch: pytest.MonkeyPatch):
    """Die Test-Suite darf NICHT in die produktive chat_perf-DB schreiben.

    ``agent_chatbot_logic.stream_chat_events`` hüllt die UI-Sink in eine
    Recording-Sink ein; ohne Isolation landen synthetische Test-Runs
    (0–1 ms, ohne LLM-Metriken, route=NULL) in der produktiven Telemetrie-DB
    unter ``~/.local/share/homebot_dbs/chat_perf.db`` und verzerren den
    perf_report (beobachtet 2026-09-12: 8 Noise-Rows aus Suite-Runs).

    ``enabled`` liest die Variable pro Zugriff dynamisch (chat_perf_recorder,
    Property ``enabled``) -- der Kill-Switch greift auch für bereits
    konstruierte Recorder und erzeugt keine DB-Datei.

    Die Telemetrie-Tests selbst (``tests/test_chat_perf_telemetry.py``)
    entfernen die Variable in ihrem eigenen autouse-Fixture (läuft nach dem
    hier) und nutzen eine tmp-DB.
    """
    monkeypatch.setenv("HOMEBOT_CHAT_PERF_DISABLED", "1")
