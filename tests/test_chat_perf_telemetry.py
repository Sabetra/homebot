"""Tests für utils/chat_perf_recorder (Low-Overhead Chat-Perf-Telemetrie).

Abdeckung:
- Kill-Switch (HOMEBOT_CHAT_PERF_DISABLED)
- Observer-Run: run_started → Boundary-Events → Terminal-Event
- Bounded-Queue + Dropped-Counter (kein Blocking im Hot-Path)
- Asynchrone Writer-Persistenz: exakt eine Zeile pro Run
- Engine-Metriken-Capture (Stub-LLM/Perf-Context)
- Thread-lokale consume_engine_metrics-Reset-Semantik
"""

from __future__ import annotations

import queue
import sqlite3
import threading
import time
from types import SimpleNamespace
from typing import Any, List, Optional

import pytest

import utils.chat_perf_recorder as cpr
from utils.chat_perf_recorder import (
    ChatPerfRecord,
    ChatPerfRecorder,
    begin_llm_call,
    consume_engine_metrics,
    end_llm_call,
    make_recording_sink,
)


# ── Fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _reset_recorder_singleton(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Singleton + Thread-Locals pro Test isolieren (keine Test-Abhängigkeit)."""
    monkeypatch.delenv(cpr.DISABLED_ENV, raising=False)
    cpr._set_default_recorder(None)
    cpr._tls.acc = None
    yield
    cpr._set_default_recorder(None)
    cpr._tls.acc = None


@pytest.fixture
def recorder(tmp_path) -> ChatPerfRecorder:
    """Recorder mit Temp-DB; wird als Singleton registriert."""
    rec = ChatPerfRecorder(db_path=tmp_path / "chat_perf.db")
    cpr._set_default_recorder(rec)
    yield rec
    rec.stop(timeout=5.0)


def _record(run_id: str, **overrides: Any) -> ChatPerfRecord:
    base = dict(
        ts_utc="2026-09-11T12:00:00.000+00:00",
        run_id=run_id,
        session_id="sess-1",
        route="simple",
        status="completed",
        total_ms=1000,
    )
    base.update(overrides)
    return ChatPerfRecord(**base)


def _event(etype: str, run_id: str = "run-1", **payload: Any) -> SimpleNamespace:
    """Minimal-Event-Stub im ChatEvent-Format (type + run_id + Payload)."""
    base = {"type": etype, "run_id": run_id, "session_id": "sess-1"}
    base.update(payload)
    return SimpleNamespace(**base)


def _drive(sink: Any, *events: Any) -> None:
    for event in events:
        sink(event)


def _rows(rec: ChatPerfRecorder, wait_s: float = 5.0) -> List[Any]:
    """Warten bis der Writer flushte, dann Zeilen aus der DB lesen."""
    deadline = time.monotonic() + wait_s
    rows: List[Any] = []
    while time.monotonic() < deadline:
        try:
            conn = sqlite3.connect(str(rec.db_path))
            try:
                rows = list(conn.execute("SELECT run_id, route, status, ttft_ms, total_ms, llm_calls FROM chat_perf_runs ORDER BY id"))
            finally:
                conn.close()
        except sqlite3.OperationalError:
            rows = []
        if len(rows) >= 1:
            break
        time.sleep(0.02)
    return rows


# ── Kill-Switch ───────────────────────────────────────────────────────────


def test_kill_switch_disables_recorder(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv(cpr.DISABLED_ENV, "1")
    rec = ChatPerfRecorder(db_path=tmp_path / "chat_perf.db")
    assert rec.enabled is False

    rec.record(_record("r1"))
    rec.record(_record("r2"))
    # Deaktivierte Records werden still verworfen (kein Drop-Zähler, kein I/O)
    assert rec.dropped == 0
    rec.stop(timeout=2.0)
    assert not rec.db_path.exists(), "Kill-Switch aktiv: keine DB darf angelegt werden"


def test_kill_switch_blocks_engine_capture(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(cpr.DISABLED_ENV, "true")

    def _fail(ctx: Any) -> Any:
        raise AssertionError("Bindungsaufruf bei aktivem Kill-Switch unerwartet")

    fake_bindings = SimpleNamespace(
        llama_perf_context_reset=_fail,
        llama_perf_context=_fail,
    )
    monkeypatch.setattr(cpr, "_llama_bindings", lambda: fake_bindings)

    llm = SimpleNamespace(ctx=object())
    begin_llm_call(llm)
    end_llm_call(llm)
    assert consume_engine_metrics() is None


def test_observer_completed_run(recorder: ChatPerfRecorder) -> None:
    base_calls: List[str] = []
    sink = make_recording_sink(lambda ev: base_calls.append(getattr(ev, "type", "?")))

    result = SimpleNamespace(metrics={"duration_ms": 4321})
    _drive(
        sink,
        _event("run_started", message_id="m1"),
        _event("route_selected", selected_route="plan_execute"),
        _event("step_started", step_id="s1", label="Routing"),
        _event("step_finished", step_id="s1", status="completed", duration_ms=120),
        _event("text_started", message_id="m1"),
        _event("text_delta", message_id="m1", delta="Hallo"),
        _event("text_delta", message_id="m1", delta=" Welt"),
        _event("usage_updated", ttft_ms=812, prompt_tokens=140, completion_tokens=57,
               tokens_per_second=61.5),
        _event("text_finished", message_id="m1"),
        _event("run_completed", result=result),
    )

    # Base-Sink muss ALLE Events (UI-Vertrag) erhalten
    assert base_calls == [
        "run_started", "route_selected", "step_started", "step_finished",
        "text_started", "text_delta", "text_delta", "usage_updated",
        "text_finished", "run_completed",
    ]

    rows = _rows(recorder)
    assert len(rows) == 1
    run_id, route, status, ttft_ms, total_ms, llm_calls = rows[0]
    assert run_id == "run-1"
    assert route == "plan_execute"
    assert status == "completed"
    assert ttft_ms == 812
    assert total_ms == 4321  # aus result.metrics.duration_ms (autoritativ)
    assert llm_calls is None  # kein Engine-Stub in diesem Test


def test_observer_failed_run_captures_error_code(recorder: ChatPerfRecorder) -> None:
    sink = make_recording_sink(lambda ev: None)
    _drive(
        sink,
        _event("run_started", message_id="m1"),
        _event("route_selected", selected_route="react"),
        _event("run_failed", error_code="LLMTimeout", message="zu langsam", partial_text=""),
    )
    rows = _rows(recorder)
    assert len(rows) == 1
    assert rows[0][2] == "failed"
    conn = sqlite3.connect(str(recorder.db_path))
    try:
        error_code = conn.execute("SELECT error_code FROM chat_perf_runs").fetchone()[0]
    finally:
        conn.close()
    assert error_code == "LLMTimeout"


def test_observer_cancelled_run(recorder: ChatPerfRecorder) -> None:
    sink = make_recording_sink(lambda ev: None)
    _drive(
        sink,
        _event("run_started", message_id="m1"),
        _event("text_delta", message_id="m1", delta="Halb"),
        _event("run_cancelled", partial_text="Halb"),
    )
    rows = _rows(recorder)
    assert len(rows) == 1
    assert rows[0][2] == "cancelled"


def test_observer_ignores_unknown_events_and_foreign_runs(
    recorder: ChatPerfRecorder,
) -> None:
    sink = make_recording_sink(lambda ev: None)
    _drive(
        sink,
        object(),  # Sentinel ohne .type -> wird nur weitergeleitet, nie beobachtet
        _event("tool_started", run_id="run-X", tool_call_id="t1", tool_name="web"),
        _event("run_started", run_id="run-1", message_id="m1"),
        _event("route_selected", run_id="run-2", selected_route="vision"),  # fremder Run
        _event("run_completed", run_id="run-1",
               result=SimpleNamespace(metrics={"duration_ms": 100})),
    )
    rows = _rows(recorder)
    assert len(rows) == 1
    assert rows[0][0] == "run-1"
    assert rows[0][1] is None  # run-2-Route darf nicht in run-1 landen


def test_tokens_per_second_fallback_from_engine_metrics(
    recorder: ChatPerfRecorder, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Real-App-Scenario (agent_chatbot_logic.py): ``usage_updated`` enthält
    nur ``ttft_ms``. ``tokens_per_second`` wird dann aus den Engine-Metriken
    abgeleitet: Summe(114 Tokens) / Summe(2400 ms) = 47.5 tok/s.

    Event-Reihenfolge = Produktions-Reihenfolge: ``run_started`` ZUERST
    (discardet Stale-Akkumulation), LLM-Calls in der Mitte, Terminal am Ende.
    """
    _fake_perf_bindings(monkeypatch, [])
    llm = SimpleNamespace(ctx="CTX")

    sink = make_recording_sink(lambda ev: None)
    _drive(sink, _event("run_started", message_id="m1"))
    begin_llm_call(llm)
    end_llm_call(llm)
    end_llm_call(llm)  # akkumuliert (2 Calls in einem Run)
    _drive(
        sink,
        _event("route_selected", selected_route="simple"),
        _event("usage_updated", ttft_ms=812),  # bewusst OHNE tokens_per_second
        _event("run_completed", result=SimpleNamespace(metrics={"duration_ms": 4321})),
    )

    deadline = time.monotonic() + 5.0
    tok_s: Optional[float]
    while time.monotonic() < deadline:
        try:
            conn = sqlite3.connect(str(recorder.db_path))
            try:
                rows = list(conn.execute(
                    "SELECT tokens_per_second, llm_calls FROM chat_perf_runs"
                ))
            finally:
                conn.close()
        except sqlite3.OperationalError:
            rows = []  # Tabelle erst nach dem ersten Writer-Flush vorhanden
        if rows:
            tok_s = rows[0][0]
            if tok_s is not None:
                break
        time.sleep(0.02)
    else:
        tok_s = None

    assert tok_s == pytest.approx(47.5, abs=0.01)


# ── Bounded Queue + Writer ────────────────────────────────────────────────


def test_record_never_blocks_when_queue_full(
    monkeypatch: pytest.MonkeyPatch, tmp_path,
) -> None:
    """Vollständige Queue: record() zahlt Drop, statt zu blockieren (Hot-Path)."""
    rec = ChatPerfRecorder(db_path=tmp_path / "chat_perf.db", max_queue=2)
    monkeypatch.setattr(rec, "_ensure_writer", lambda: None)  # Writer isoliert

    full = _record("fill-1")
    rec._queue.put_nowait(full)
    rec._queue.put_nowait(full)
    with pytest.raises(queue.Full):
        rec._queue.put_nowait(full)

    rec.record(_record("dropped-1"))
    assert rec.dropped == 1
    # Drop-oldest: die neue Zeile ist in der Queue, die älteste flog
    assert rec._queue.qsize() == 2
    rec.stop(timeout=2.0)
    assert rec.dropped == 1  # bleibt stabil (stop() zählt keine zusätzlichen Drops)


def test_writer_persists_one_row_per_run(tmp_path) -> None:
    """3 Runs -> exakt 3 Zeilen (Writer-Thread oder stop()-Flush, beides OK)."""
    rec = ChatPerfRecorder(db_path=tmp_path / "chat_perf.db")
    rec.record(_record("run-a", route="simple", total_ms=100))
    rec.record(_record("run-b", route="react", total_ms=250))
    rec.record(_record("run-c", route="react", total_ms=400, status="failed"))
    rec.stop(timeout=5.0)  # stop() flushet die verbleibende Queue + schließt

    conn = sqlite3.connect(str(rec.db_path))
    try:
        rows = list(conn.execute(
            "SELECT run_id, route, status, total_ms FROM chat_perf_runs ORDER BY run_id"
        ))
    finally:
        conn.close()

    assert [r[0] for r in rows] == ["run-a", "run-b", "run-c"]
    assert [r[2] for r in rows] == ["completed", "completed", "failed"]
    assert [r[3] for r in rows] == [100, 250, 400]
    assert [r[1] for r in rows] == ["simple", "react", "react"]


# ── Engine-Metriken (llama.cpp Perf-Context) ─────────────────────────────


def _fake_perf_bindings(monkeypatch: pytest.MonkeyPatch, reset_calls: List[Any]):
    """Fake llama_cpp-Bindungen: zählbar + deterministische Metriken."""
    data = SimpleNamespace(
        t_p_eval_ms=350.0, t_eval_ms=1200.0,
        n_p_eval=140, n_eval=57, n_reused=32,
    )
    bindings = SimpleNamespace(
        llama_perf_context_reset=lambda ctx: reset_calls.append(ctx),
        llama_perf_context=lambda ctx: data,
    )
    monkeypatch.setattr(cpr, "_llama_bindings", lambda: bindings)
    return data


def test_engine_metrics_capture_and_consume(monkeypatch: pytest.MonkeyPatch) -> None:
    reset_calls: List[Any] = []
    _fake_perf_bindings(monkeypatch, reset_calls)
    llm = SimpleNamespace(ctx="CTX")

    begin_llm_call(llm)
    assert len(reset_calls) == 1 and reset_calls[0] == "CTX"
    end_llm_call(llm)
    # Zweiter Call akkumuliert (mehrere LLM-Calls pro Run)
    begin_llm_call(llm)
    end_llm_call(llm)

    metrics = consume_engine_metrics()
    assert metrics is not None
    assert metrics["llm_calls"] == 2
    assert metrics["prefill_ms"] == 350.0 * 2
    assert metrics["generation_ms"] == 1200.0 * 2
    assert metrics["prompt_tokens"] == 280
    assert metrics["generation_tokens"] == 114
    assert metrics["n_reused"] == 64

    # Consume ist atomar: zweiter Aufruf liefert nichts
    assert consume_engine_metrics() is None


def test_engine_metrics_thread_local_isolation(monkeypatch: pytest.MonkeyPatch) -> None:
    reset_calls: List[Any] = []
    _fake_perf_bindings(monkeypatch, reset_calls)
    llm = SimpleNamespace(ctx="CTX")
    assert consume_engine_metrics() is None  # Main-Thread: noch nichts

    result: dict[str, Any] = {}
    barrier = threading.Event()

    def worker() -> None:
        end_llm_call(llm)
        result["worker_consume"] = consume_engine_metrics()
        barrier.set()

    t = threading.Thread(target=worker, daemon=True)
    t.start()
    assert barrier.wait(timeout=5.0)
    t.join(timeout=5.0)

    # Worker: akkumulierte Metriken; Main-Thread: VOLLSTÄNDIG unberührt
    assert result["worker_consume"] is not None
    assert result["worker_consume"]["llm_calls"] == 1
    assert consume_engine_metrics() is None


def test_engine_metrics_none_when_llm_has_no_ctx(monkeypatch: pytest.MonkeyPatch) -> None:
    reset_calls: List[Any] = []
    _fake_perf_bindings(monkeypatch, reset_calls)
    begin_llm_call(SimpleNamespace())  # kein .ctx
    end_llm_call(SimpleNamespace())
    assert consume_engine_metrics() is None
    assert reset_calls == []  # kein reset aufgerufen (kein ctx)


def test_engine_metrics_ignores_non_numeric_and_negative(monkeypatch: pytest.MonkeyPatch) -> None:
    """Defekte/negative Engine-Werte werden verworfen, nicht akkumuliert."""
    data = SimpleNamespace(t_p_eval_ms=-1.0, t_eval_ms=None, n_p_eval=-5, n_eval=10, n_reused=0)
    bindings = SimpleNamespace(
        llama_perf_context_reset=lambda ctx: None, llama_perf_context=lambda ctx: data,
    )
    monkeypatch.setattr(cpr, "_llama_bindings", lambda: bindings)
    llm = SimpleNamespace(ctx="CTX")

    begin_llm_call(llm)
    end_llm_call(llm)
    metrics = consume_engine_metrics()
    assert metrics is not None
    assert metrics["llm_calls"] == 1
    assert metrics["prefill_ms"] == 0.0
    assert metrics["generation_ms"] == 0.0
    assert metrics["prompt_tokens"] == 0
    assert metrics["generation_tokens"] == 10  # n_eval=10 ist gültig


# ── Trace-Summary (AgentTrace → trace_summary) ────────────────────────────


def _trace_payload() -> dict:
    return {
        "planner_ms": 56400,
        "tools_ms": 869000,
        "summarize_ms": 12000,
        "verify_ms": 800,
        "planned_tools": ["rag_search", "web_search"],
        "ran_tools": ["rag_search"],
        "subqueries": ["q1", "q2", "q3"],
        "rag_stats": {"docs": 12345, "chunks": 6},
        "tool_results": {"rag_search": {"huge": "payload" * 1000}},
    }


def test_build_trace_summary_compact() -> None:
    summary = cpr._build_trace_summary(_trace_payload())
    assert summary is not None
    assert "planner_ms=56400ms" in summary
    assert "tools_ms=869000ms" in summary
    assert "summarize_ms=12000ms" in summary
    assert "verify_ms=800ms" in summary
    assert "planned=[rag_search,web_search]" in summary
    assert "ran=[rag_search]" in summary
    assert "subq=3" in summary
    assert "ragStats[{docs=12345,chunks=6}]" in summary
    # Schwere Debug-Felder bleiben außen vor (DB-Zeilen bleiben klein)
    assert "payload" not in summary
    assert "tool_results" not in summary


def test_build_trace_summary_none_and_empty() -> None:
    assert cpr._build_trace_summary(None) is None
    assert cpr._build_trace_summary({}) is None
    assert cpr._build_trace_summary("junk") is None
    # Nur Null-/Leer-Werte -> keine aussagekräftige Zeile
    assert cpr._build_trace_summary({"planner_ms": 0, "ran_tools": []}) is None


def test_build_trace_summary_truncates() -> None:
    big = {"planned_tools": [f"tool-{i}" for i in range(200)]}
    summary = cpr._build_trace_summary(big)
    assert summary is not None
    assert len(summary) <= cpr._TRACE_SUMMARY_MAX_LEN


def test_observer_persists_trace_summary(recorder: ChatPerfRecorder) -> None:
    sink = make_recording_sink(lambda ev: None)
    result = SimpleNamespace(metrics={"duration_ms": 4321}, trace=_trace_payload())
    _drive(
        sink,
        _event("run_started", message_id="m1"),
        _event("route_selected", selected_route="plan_execute"),
        _event("run_completed", result=result),
    )

    deadline = time.monotonic() + 5.0
    row: Optional[Any] = None
    while time.monotonic() < deadline:
        conn = sqlite3.connect(str(recorder.db_path))
        try:
            row = conn.execute(
                "SELECT trace_summary FROM chat_perf_runs"
            ).fetchone()
        except sqlite3.OperationalError:
            row = None  # Tabelle erst nach dem ersten Writer-Flush vorhanden
        finally:
            conn.close()
        if row and row[0]:
            break
        time.sleep(0.02)

    assert row is not None and row[0] is not None
    assert "tools_ms=869000ms" in row[0]
    assert "planned=[rag_search,web_search]" in row[0]
    assert "payload" not in row[0]


def test_observer_no_trace_keeps_column_null(recorder: ChatPerfRecorder) -> None:
    sink = make_recording_sink(lambda ev: None)
    _drive(
        sink,
        _event("run_started", message_id="m1"),
        _event("run_completed", result=SimpleNamespace(metrics={"duration_ms": 100}, trace=None)),
    )
    deadline = time.monotonic() + 5.0
    row: Optional[Any] = None
    while time.monotonic() < deadline:
        conn = sqlite3.connect(str(recorder.db_path))
        try:
            row = conn.execute("SELECT trace_summary FROM chat_perf_runs").fetchone()
        except sqlite3.OperationalError:
            row = None
        finally:
            conn.close()
        if row is not None:
            break
        time.sleep(0.02)
    assert row is not None
    assert row[0] is None  # trace=None -> NULL-Spalte


def test_migration_adds_trace_summary_column(tmp_path) -> None:
    """Bestands-DB (Schema V1 ohne trace_summary) wird transparent migriert."""
    db = tmp_path / "chat_perf.db"
    conn = sqlite3.connect(str(db))
    conn.execute(
        "CREATE TABLE chat_perf_runs ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT, ts_utc TEXT NOT NULL,"
        " run_id TEXT NOT NULL, session_id TEXT, route TEXT, status TEXT NOT NULL,"
        " ttft_ms INTEGER, total_ms INTEGER, prompt_tokens INTEGER,"
        " completion_tokens INTEGER, tokens_per_second REAL, llm_calls INTEGER,"
        " llm_prefill_ms REAL, llm_generation_ms REAL, llm_prompt_tokens INTEGER,"
        " llm_generation_tokens INTEGER, llm_n_reused INTEGER, step_count INTEGER,"
        " step_summary TEXT, error_code TEXT)"
    )
    conn.commit()
    conn.close()

    rec = ChatPerfRecorder(db_path=db)
    rec.record(_record("legacy-1"))
    rec.stop(timeout=5.0)

    conn = sqlite3.connect(str(db))
    try:
        row = conn.execute(
            "SELECT run_id, trace_summary FROM chat_perf_runs WHERE run_id='legacy-1'"
        ).fetchone()
    finally:
        conn.close()
    assert row is not None  # Spalte existiert, Insert mit neuer Spalte ok
    assert row[1] is None
