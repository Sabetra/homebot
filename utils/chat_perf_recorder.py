"""
Low-Overhead Chat-Performance-Telemetrie (SOTA, 2026-09-11)
============================================================

Ziel: Pipeline-Bottlenecks pro Chat-Run messbar machen, OHNE UX-Latenz.

Design-Prinzipien (siehe docs/WORKDOC_chat_perf_telemetry.md):
- Kein per-Token-Timing und keine I/O im Hot-Path. Die Recording-Sink
  beobachtet nur Boundary-Events; per TextDelta kostet das genau eine
  Attribut-Vergleichsoperation.
- Persistenz über einen Daemon-Writer: bounded In-Memory-Queue ->
  batched SQLite-Inserts (WAL). Telemetrie ist Best-Effort: Verlust
  unter Last ist akzeptabel, Fehler dürfen die App NIE beeinflussen.
- Engine-Metriken (Prefill-/Decode-Speed, Graph-Reuse) kommen aus dem
  llama.cpp C++-Perf-Context (``llama_perf_context``) -- keine
  Python-seitige Token-Timer.
- Kill-Switch: Env ``HOMEBOT_CHAT_PERF_DISABLED=1`` (pro Event geprüft,
  keine Import-Kosten über die stdlib hinaus).

Nutzung:
    from utils import chat_perf_recorder as cpr

    # 1) Sink-Wrapping (einmal pro Run in agent_chatbot_logic.py):
    context = StreamingContext(session_id=..., sink=cpr.make_recording_sink(event_queue.put))

    # 2) Engine-Metriken um LLM-Calls (scripts/model_loader.py):
    cpr.begin_llm_call(self.llm)
    try:
        result = self.llm.create_completion(...)
    finally:
        cpr.end_llm_call(self.llm)

    # 3) Report: python scripts/perf_report.py
"""

from __future__ import annotations

import atexit
import logging
import os
import queue
import sqlite3
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

__all__ = [
    "DISABLED_ENV",
    "DB_FILENAME",
    "ChatPerfRecord",
    "ChatPerfRecorder",
    "get_recorder",
    "make_recording_sink",
    "begin_llm_call",
    "end_llm_call",
    "consume_engine_metrics",
]

logger = logging.getLogger(__name__)

DISABLED_ENV = "HOMEBOT_CHAT_PERF_DISABLED"
DB_FILENAME = "chat_perf.db"

_TERMINAL_TYPES = ("run_completed", "run_cancelled", "run_failed")
_MAX_STEPS_IN_SUMMARY = 24
_SENTINEL = object()

_INSERT_SQL = (
    "INSERT INTO chat_perf_runs (ts_utc, run_id, session_id, route, status,"
    " ttft_ms, total_ms, prompt_tokens, completion_tokens, tokens_per_second,"
    " llm_calls, llm_prefill_ms, llm_generation_ms, llm_prompt_tokens,"
    " llm_generation_tokens, llm_n_reused, step_count, step_summary, error_code, trace_summary)"
    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS chat_perf_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_utc TEXT NOT NULL,
    run_id TEXT NOT NULL,
    session_id TEXT,
    route TEXT,
    status TEXT NOT NULL,
    ttft_ms INTEGER,
    total_ms INTEGER,
    prompt_tokens INTEGER,
    completion_tokens INTEGER,
    tokens_per_second REAL,
    llm_calls INTEGER,
    llm_prefill_ms REAL,
    llm_generation_ms REAL,
    llm_prompt_tokens INTEGER,
    llm_generation_tokens INTEGER,
    llm_n_reused INTEGER,
    step_count INTEGER,
    step_summary TEXT,
    error_code TEXT,
    trace_summary TEXT
);
CREATE INDEX IF NOT EXISTS idx_chat_perf_runs_ts ON chat_perf_runs (ts_utc);
CREATE INDEX IF NOT EXISTS idx_chat_perf_runs_route_ts ON chat_perf_runs (route, ts_utc);
"""


@dataclass(frozen=True)
class ChatPerfRecord:
    """Eine strukturierte Messzeile pro Chat-Run (Single Row per Run)."""

    ts_utc: str
    run_id: str
    status: str  # "completed" | "cancelled" | "failed"
    total_ms: int
    session_id: Optional[str] = None
    route: Optional[str] = None
    ttft_ms: Optional[int] = None
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None
    tokens_per_second: Optional[float] = None
    # Engine-Metriken aus llama_perf_context() (akkumuliert über alle
    # LLM-Calls des Runs): None = kein LLM-Aufruf gemessen (z.B. cache-Hit).
    llm_calls: Optional[int] = None
    llm_prefill_ms: Optional[float] = None
    llm_generation_ms: Optional[float] = None
    llm_prompt_tokens: Optional[int] = None
    llm_generation_tokens: Optional[int] = None
    llm_n_reused: Optional[int] = None
    step_count: int = 0
    step_summary: Optional[str] = None
    error_code: Optional[str] = None
    # Kompakte AgentTrace-Zusammenfassung (Tools, Phasen-Timings, RAG-Stats)
    trace_summary: Optional[str] = None


class _RunState:
    """Request-scope Akkumulator der Recording-Sink (ein Objekt pro Run)."""

    __slots__ = (
        "started", "session_id", "route", "first_text_at", "ttft_ms",
        "prompt_tokens", "completion_tokens", "tokens_per_second",
        "step_labels", "steps",
    )

    def __init__(self, started: float, session_id: str) -> None:
        self.started = started
        self.session_id = session_id
        self.route: Optional[str] = None
        self.first_text_at: Optional[float] = None
        self.ttft_ms: Optional[int] = None
        self.prompt_tokens: Optional[int] = None
        self.completion_tokens: Optional[int] = None
        self.tokens_per_second: Optional[float] = None
        self.step_labels: Dict[str, str] = {}
        self.steps: List[str] = []


class ChatPerfRecorder:
    """Thread-sichere, bounded, Best-Effort-Persistenz von ChatPerfRecord.

    - ``record()`` ist O(1) und I/O-frei (Queue-put auf dem Producer-Thread).
    - Ein Daemon-Thread flushet Batches nach SQLite (WAL, synchronous=NORMAL).
    - Queue voll: älteste Zeile wird verworfen (neueste behalten).
    - Alle Fehler werden geschluckt und geloggt -- Telemetrie crasht nie die App.
    """

    def __init__(
        self,
        db_path: Optional[Any] = None,
        *,
        max_queue: int = 10_000,
        max_batch: int = 500,
    ) -> None:
        self._db_path: Optional[Path] = Path(db_path) if db_path else None
        self._max_batch = max(1, int(max_batch))
        self._queue: "queue.Queue[Any]" = queue.Queue(maxsize=max(1, int(max_queue)))
        self._lifecycle_lock = threading.Lock()
        self._io_lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._conn: Optional[sqlite3.Connection] = None
        self._dropped = 0

    @property
    def enabled(self) -> bool:
        """Kill-Switch: ``HOMEBOT_CHAT_PERF_DISABLED=1`` deaktiviert alles."""
        return os.environ.get(DISABLED_ENV, "").strip() != "1"

    @property
    def db_path(self) -> Path:
        if self._db_path is None:
            # Lazy Import: der Resolver darf die Telemetrie nie beim
            # Import blockieren (und Tests können den Pfad injizieren).
            from utils.db_path_resolver import get_db_path

            self._db_path = get_db_path(DB_FILENAME)
        return self._db_path

    @property
    def dropped(self) -> int:
        """Anzahl verworfener Records (Queue voll) -- für Diagnosen."""
        return self._dropped

    def record(self, rec: ChatPerfRecord) -> None:
        """O(1), I/O-frei, wirft nie. Startet den Writer-Thread bei Bedarf."""
        if not self.enabled:
            return
        try:
            self._queue.put_nowait(rec)
        except queue.Full:
            # Bounded: älteste Zeile verwerfen, neueste behalten.
            try:
                self._queue.get_nowait()
                self._dropped += 1
                self._queue.put_nowait(rec)
            except queue.Empty:
                self._queue.put_nowait(rec)
        self._ensure_writer()

    def ensure_running(self) -> None:
        """Writer-Thread starten (idempotent)."""
        self._ensure_writer()

    def flush_now(self) -> int:
        """Synchron Queue leeren + persistieren. Returns: Anzahl Records."""
        batch: List[ChatPerfRecord] = []
        while True:
            try:
                batch.append(self._queue.get_nowait())
            except queue.Empty:
                break
        if batch:
            self._safe_flush(batch)
        return len(batch)

    def stop(self, timeout: float = 5.0) -> None:
        """Writer beenden, Rest flushen, Connection schließen. Idempotent."""
        with self._lifecycle_lock:
            thread = self._thread
            self._thread = None
        if thread is not None:
            self._queue.put(_SENTINEL)
            thread.join(timeout)
        self.flush_now()
        self._close_connection()

    # ── Internals (nur Writer-Thread / stop() aufrufen) ──────────────────

    def _ensure_writer(self) -> None:
        with self._lifecycle_lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._thread = threading.Thread(
                target=self._writer_loop, name="chat-perf-writer", daemon=True
            )
            self._thread.start()

    def _writer_loop(self) -> None:
        while True:
            item = self._queue.get()
            if item is _SENTINEL:
                batch: List[ChatPerfRecord] = []
                while True:
                    try:
                        batch.append(self._queue.get_nowait())
                    except queue.Empty:
                        break
                if batch:
                    self._safe_flush(batch)
                return
            batch = [item]
            while len(batch) < self._max_batch:
                try:
                    nxt = self._queue.get_nowait()
                except queue.Empty:
                    break
                if nxt is _SENTINEL:
                    # Sentinel nicht in den Batch ziehen: wieder einreihen,
                    # damit die nächste Schleifendurchlauf sauber terminiert.
                    self._queue.put_nowait(nxt)
                    break
                batch.append(nxt)
            self._safe_flush(batch)

    def _safe_flush(self, batch: List[ChatPerfRecord]) -> None:
        try:
            with self._io_lock:
                conn = self._connection()
                conn.executemany(_INSERT_SQL, [self._row(r) for r in batch])
                conn.commit()
        except Exception:
            # Best-Effort-Telemetrie: Fehler dürfen nie die App erreichen.
            logger.warning(
                "chat_perf: Persistenz fehlgeschlagen, %d Records verworfen",
                len(batch), exc_info=True,
            )

    def _connection(self) -> sqlite3.Connection:
        if self._conn is None:
            path = self.db_path
            path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(str(path), check_same_thread=False)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.executescript(_SCHEMA)
            # Migration: Bestands-DBs (Schema V1) ohne trace_summary
            cols = {row[1] for row in conn.execute("PRAGMA table_info(chat_perf_runs)")}
            if "trace_summary" not in cols:
                conn.execute("ALTER TABLE chat_perf_runs ADD COLUMN trace_summary TEXT")
            conn.commit()
            self._conn = conn
        return self._conn

    def _close_connection(self) -> None:
        with self._lifecycle_lock:
            conn, self._conn = self._conn, None
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass

    @staticmethod
    def _row(r: ChatPerfRecord) -> tuple:
        return (
            r.ts_utc, r.run_id, r.session_id, r.route, r.status,
            r.ttft_ms, r.total_ms, r.prompt_tokens, r.completion_tokens,
            r.tokens_per_second, r.llm_calls, r.llm_prefill_ms,
            r.llm_generation_ms, r.llm_prompt_tokens, r.llm_generation_tokens,
            r.llm_n_reused, r.step_count, r.step_summary, r.error_code,
            r.trace_summary,
        )


# ── Singleton ─────────────────────────────────────────────────────────────

_RECORDER: Optional[ChatPerfRecorder] = None
_RECORDER_LOCK = threading.Lock()


def get_recorder() -> ChatPerfRecorder:
    """Process-weiter Recorder (lazy; Writer-Thread startet erst bei 1. Record)."""
    global _RECORDER
    if _RECORDER is None:
        with _RECORDER_LOCK:
            if _RECORDER is None:
                _RECORDER = ChatPerfRecorder()
    return _RECORDER


def _set_default_recorder(recorder: Optional[ChatPerfRecorder]) -> None:
    """Test-Hook: Singleton austauschen (None = zurücksetzen)."""
    global _RECORDER
    with _RECORDER_LOCK:
        _RECORDER = recorder


def _atexit_stop() -> None:
    global _RECORDER
    if _RECORDER is not None:
        try:
            _RECORDER.stop(timeout=3.0)
        except Exception:
            logger.debug("chat_perf: atexit-Stop fehlgeschlagen", exc_info=True)


atexit.register(_atexit_stop)


# ── Recording-Sink (Hot-Path-Wrapper) ─────────────────────────────────────


def make_recording_sink(base_sink: Callable[[Any], None]) -> Callable[[Any], None]:
    """Hüllt die UI-Sink ein: FORWARD ZUERST (UI-Pfad bleibt unverändert),
    dann Telemetrie.

    Kosten pro TextDelta: genau eine String-Vergleichsoperation.
    Alle übrigen Operationen laufen nur auf Boundary-Events (Handvoll pro Run).
    """
    runs: Dict[str, _RunState] = {}

    def sink(event: Any) -> None:
        base_sink(event)
        if not get_recorder().enabled:
            return
        try:
            _observe(runs, event)
        except Exception:
            logger.debug("chat_perf: Observer-Fehler", exc_info=True)

    return sink


def _observe(runs: Dict[str, _RunState], event: Any) -> None:
    etype = getattr(event, "type", None)
    if not isinstance(etype, str):
        return
    run_id = getattr(event, "run_id", None)
    if not isinstance(run_id, str):
        return

    if etype == "run_started":
        # Stale Engine-Metriken eines abgestürzten Vorgänger-Runs verwerfen.
        _tls.acc = None
        runs[run_id] = _RunState(
            time.perf_counter(), str(getattr(event, "session_id", "") or "")
        )
        return

    state = runs.get(run_id)
    if state is None:
        return  # Unbekannter Run (z.B. Events vor run_started) -- ignorieren.

    if etype == "route_selected":
        route = getattr(event, "selected_route", None)
        if route:
            state.route = str(route)
    elif etype == "text_delta":
        if state.first_text_at is None:
            state.first_text_at = time.perf_counter()
    elif etype == "step_started":
        step_id = getattr(event, "step_id", None)
        label = getattr(event, "label", None)
        if step_id and label:
            state.step_labels[str(step_id)] = str(label)
    elif etype == "step_finished":
        step_id = getattr(event, "step_id", None)
        duration_ms = getattr(event, "duration_ms", None)
        label = state.step_labels.get(str(step_id), str(step_id or "?"))
        if isinstance(duration_ms, (int, float)):
            state.steps.append(f"{label}={int(duration_ms)}ms")
        else:
            state.steps.append(label)
        if len(state.steps) > _MAX_STEPS_IN_SUMMARY:
            del state.steps[: len(state.steps) - _MAX_STEPS_IN_SUMMARY]
    elif etype == "usage_updated":
        state.ttft_ms = getattr(event, "ttft_ms", None)
        state.prompt_tokens = getattr(event, "prompt_tokens", None)
        state.completion_tokens = getattr(event, "completion_tokens", None)
        state.tokens_per_second = getattr(event, "tokens_per_second", None)
    elif etype in _TERMINAL_TYPES:
        runs.pop(run_id, None)
        _finalize(run_id, state, event)


_TRACE_SUMMARY_MAX_LEN = 2000


def _fmt_tool_list(values: Any) -> Optional[str]:
    """Kompakte Tool-Liste (komma-separiert, max. 16); None wenn leer/ungültig."""
    if isinstance(values, (list, tuple)) and values:
        return ",".join(str(v) for v in values[:16])
    return None


def _build_trace_summary(trace: Any) -> Optional[str]:
    """AgentTrace (dict aus ``ChatRunResult.trace``) als kompakte,
    durchsuchbare Zeile zusammenfassen: Tools, Phasen-Timings, RAG-Stats.

    None/leer/kein dict -> None. Wirft nie (Best-Effort-Telemetrie).
    Schwere Debug-Felder (``tool_results``, ``verification_results``, ...)
    werden bewusst ausgeschlossen, damit die DB-Zeile klein bleibt.
    """
    if not isinstance(trace, dict):
        return None
    parts: List[str] = []

    for key in ("planner_ms", "tools_ms", "summarize_ms", "verify_ms"):
        value = trace.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
            parts.append(f"{key}={int(value)}ms")

    for key in ("multi_hop_ms", "multi_hop_hops"):
        value = trace.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
            parts.append(f"{key}={int(value)}")

    planned = _fmt_tool_list(trace.get("planned_tools"))
    if planned:
        parts.append(f"planned=[{planned}]")
    ran = _fmt_tool_list(trace.get("ran_tools"))
    if ran:
        parts.append(f"ran=[{ran}]")

    subqueries = trace.get("subqueries")
    if isinstance(subqueries, (list, tuple)) and subqueries:
        parts.append(f"subq={len(subqueries)}")

    rag_stats = trace.get("rag_stats")
    if isinstance(rag_stats, dict) and rag_stats:
        stats = ",".join(f"{k}={v}" for k, v in list(rag_stats.items())[:12])
        parts.append(f"ragStats[{{{stats}}}]")

    if not parts:
        return None
    summary = " ".join(parts)
    if len(summary) <= _TRACE_SUMMARY_MAX_LEN:
        return summary
    return summary[: _TRACE_SUMMARY_MAX_LEN - 1] + "…"


def _finalize(run_id: str, state: _RunState, event: Any) -> None:
    etype = event.type
    if etype == "run_completed":
        status, error_code = "completed", None
    elif etype == "run_cancelled":
        status, error_code = "cancelled", None
    else:
        status, error_code = "failed", str(getattr(event, "error_code", "") or "")

    total_ms = int((time.perf_counter() - state.started) * 1000)
    trace_summary: Optional[str] = None
    if etype == "run_completed":
        # Autoritative Pipeline-Dauer (wird von stream_chat_events gesetzt).
        result = getattr(event, "result", None)
        metrics = getattr(result, "metrics", None)
        if isinstance(metrics, dict):
            duration = metrics.get("duration_ms")
            if isinstance(duration, (int, float)):
                total_ms = int(duration)
        trace_summary = _build_trace_summary(getattr(result, "trace", None))

    if state.ttft_ms is None and state.first_text_at is not None:
        state.ttft_ms = int((state.first_text_at - state.started) * 1000)

    engine = consume_engine_metrics()

    # Fallback: ``usage_updated`` liefert in der App nur ``ttft_ms`` (siehe
    # agent_chatbot_logic.py). ``tokens_per_second`` daher aus den Engine-
    # Metriken ableiten: Summe(generierte Tokens) / Summe(Generation-Zeit)
    # = gewichtete Durchschnitts-Generationsgeschwindigkeit des Runs
    # (llama.cpp-Perf-Context, C++-Seite — höchste verfügbare Präzision).
    if state.tokens_per_second is None and engine:
        gen_tokens = engine.get("generation_tokens")
        gen_ms = engine.get("generation_ms")
        if (
            isinstance(gen_tokens, (int, float)) and gen_tokens > 0
            and isinstance(gen_ms, (int, float)) and gen_ms > 0
        ):
            state.tokens_per_second = round(gen_tokens / (gen_ms / 1000.0), 2)

    record = ChatPerfRecord(
        ts_utc=datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        run_id=run_id,
        status=status,
        total_ms=total_ms,
        session_id=state.session_id or None,
        route=state.route,
        ttft_ms=state.ttft_ms,
        prompt_tokens=state.prompt_tokens,
        completion_tokens=state.completion_tokens,
        tokens_per_second=state.tokens_per_second,
        llm_calls=engine.get("llm_calls") if engine else None,
        llm_prefill_ms=engine.get("prefill_ms") if engine else None,
        llm_generation_ms=engine.get("generation_ms") if engine else None,
        llm_prompt_tokens=engine.get("prompt_tokens") if engine else None,
        llm_generation_tokens=engine.get("generation_tokens") if engine else None,
        llm_n_reused=engine.get("n_reused") if engine else None,
        step_count=len(state.steps),
        step_summary="; ".join(state.steps) if state.steps else None,
        error_code=error_code or None,
        trace_summary=trace_summary,
    )

    get_recorder().record(record)
    logger.info(
        "chat_perf run_id=%s route=%s status=%s ttft_ms=%s total_ms=%s "
        "prompt_tokens=%s completion_tokens=%s tok_s=%s llm_calls=%s "
        "llm_prefill_ms=%s llm_generation_ms=%s llm_n_reused=%s steps=%d",
        record.run_id, record.route, record.status, record.ttft_ms,
        record.total_ms, record.prompt_tokens, record.completion_tokens,
        record.tokens_per_second, record.llm_calls, record.llm_prefill_ms,
        record.llm_generation_ms, record.llm_n_reused, record.step_count,
    )


# ── Engine-Metriken-Brücke (llama.cpp Perf-Context, thread-lokal) ─────────
#
# Ein Chat-Run kann mehrere LLM-Calls enthalten (Routing + Antwort). Die
# Metriken werden pro Thread akkumuliert und beim Run-Finalize konsumiert.
# Hintergrund-Threads (KG-Extraktion etc.) haben eigene Thread-Locals und
# kontaminieren den Chat-Run nicht.

_tls = threading.local()


def _non_negative(value: Any) -> float:
    """Value als nicht-negatives Float normalisieren (None/Junk -> 0.0)."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return 0.0
    return v if v > 0 else 0.0


def _llama_bindings() -> Any:
    """Native ctypes-Bindings (llama_perf_context[_reset]) lazy importieren."""
    from llama_cpp import llama_cpp as bindings  # noqa: PLC0415

    return bindings


def begin_llm_call(llm: Any) -> None:
    """Perf-Context vor einem LLM-Call zurücksetzen (Best-Effort, wirft nie)."""
    if not get_recorder().enabled:
        return
    try:
        ctx = getattr(llm, "ctx", None)
        if ctx is not None:
            _llama_bindings().llama_perf_context_reset(ctx)
    except Exception:
        pass


def end_llm_call(llm: Any) -> None:
    """Perf-Context nach einem LLM-Call auslesen und thread-lokal akkumulieren.

    Wird NUR nach erfolgreichem Call aufgerufen (failed Attempts tragen
    Partial-Stats, die der nächste ``begin_llm_call`` ohnehin resetet).
    """
    if not get_recorder().enabled:
        return
    try:
        ctx = getattr(llm, "ctx", None)
        if ctx is None:
            return
        data = _llama_bindings().llama_perf_context(ctx)
    except Exception:
        return
    acc: Optional[Dict[str, Any]] = getattr(_tls, "acc", None)
    if acc is None:
        acc = _tls.acc = {
            "llm_calls": 0,
            "prefill_ms": 0.0,
            "generation_ms": 0.0,
            "prompt_tokens": 0,
            "generation_tokens": 0,
            "n_reused": 0,
        }
    acc["llm_calls"] += 1
    t_p_eval = _non_negative(data.t_p_eval_ms)
    if t_p_eval:
        acc["prefill_ms"] += t_p_eval
    t_eval = _non_negative(data.t_eval_ms)
    if t_eval:
        acc["generation_ms"] += t_eval
    n_p_eval = int(_non_negative(data.n_p_eval))
    if n_p_eval:
        acc["prompt_tokens"] += n_p_eval
    n_eval = int(_non_negative(data.n_eval))
    if n_eval:
        acc["generation_tokens"] += n_eval
    n_reused = int(_non_negative(data.n_reused))
    if n_reused:
        acc["n_reused"] += n_reused


def consume_engine_metrics() -> Optional[Dict[str, Any]]:
    """Akkumulierte Engine-Metriken des aktuellen Threads abholen + löschen."""
    acc = getattr(_tls, "acc", None)
    _tls.acc = None
    return acc