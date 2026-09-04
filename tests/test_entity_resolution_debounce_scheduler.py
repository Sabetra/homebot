from concurrent.futures import Future
import threading

from agent.unified_rag_store import UnifiedRagStore


class _ImmediateExecutor:
    def __init__(self, result=None, exc=None):
        self.result = result
        self.exc = exc
        self.submit_calls = 0

    def submit(self, fn, *args, **kwargs):
        self.submit_calls += 1
        future = Future()
        if self.exc is not None:
            future.set_exception(self.exc)
        else:
            value = self.result
            if value is None:
                value = fn(*args, **kwargs)
            future.set_result(value)
        return future


def _build_store_for_scheduler_tests(executor):
    store = UnifiedRagStore.__new__(UnifiedRagStore)
    store.debug = False
    store._entity_resolution_lock = threading.RLock()
    store._entity_resolution_executor = executor
    store._entity_resolution_timer = None
    store._entity_resolution_inflight = False
    store._entity_resolution_inflight_payload = None
    store._entity_resolution_pending_ingests = 0
    store._entity_resolution_pending_triples = 0
    store._entity_resolution_last_run_monotonic = 0.0
    store._entity_resolution_min_ingests = 2
    store._entity_resolution_cooldown_sec = 60.0
    store._is_interpreter_shutting_down = lambda: False
    store._is_shutdown_exception = lambda _exc: False
    return store


def test_schedule_entity_resolution_debounces_when_threshold_not_reached(monkeypatch):
    executor = _ImmediateExecutor(result={"merged_groups": 0})
    store = _build_store_for_scheduler_tests(executor)

    timer_calls = []
    monkeypatch.setattr(
        store,
        "_arm_entity_resolution_timer_locked",
        lambda delay: timer_calls.append(delay),
    )

    outcome = store._schedule_entity_resolution(3)

    assert outcome["scheduled"] is True
    assert outcome["mode"] == "debounced"
    assert store._entity_resolution_pending_ingests == 1
    assert store._entity_resolution_pending_triples == 3
    assert executor.submit_calls == 0
    assert timer_calls, "Expected trailing-edge timer to be armed"


def test_schedule_entity_resolution_submits_once_after_threshold(monkeypatch):
    executor = _ImmediateExecutor(result={"merged_groups": 1, "entities_before": 10, "entities_after": 9})
    store = _build_store_for_scheduler_tests(executor)
    store._entity_resolution_min_ingests = 1
    store._entity_resolution_cooldown_sec = 1.0
    store._entity_resolution_last_run_monotonic = 0.0
    store.resolve_duplicate_entities = lambda similarity_threshold=0.90: {
        "merged_groups": 1,
        "entities_before": 10,
        "entities_after": 9,
        "threshold": similarity_threshold,
    }

    outcome = store._schedule_entity_resolution(5)

    assert outcome["scheduled"] is True
    assert outcome["mode"] == "submitted_now"
    assert executor.submit_calls == 1
    assert store._entity_resolution_inflight is False
    assert store._entity_resolution_inflight_payload is None
    assert store._entity_resolution_last_run_monotonic > 0


def test_entity_resolution_failure_requeues_pending_work(monkeypatch):
    executor = _ImmediateExecutor(exc=RuntimeError("boom"))
    store = _build_store_for_scheduler_tests(executor)
    store._entity_resolution_min_ingests = 1
    store._entity_resolution_cooldown_sec = 5.0

    timer_calls = []
    monkeypatch.setattr(
        store,
        "_arm_entity_resolution_timer_locked",
        lambda delay: timer_calls.append(delay),
    )

    outcome = store._schedule_entity_resolution(7)

    assert outcome["scheduled"] is True
    assert executor.submit_calls == 1
    assert store._entity_resolution_pending_ingests == 1
    assert store._entity_resolution_pending_triples == 7
    assert timer_calls, "Expected retry timer after async resolution failure"
