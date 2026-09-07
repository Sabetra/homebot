from contextlib import nullcontext
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path


_DASHBOARD_PATH = Path(__file__).resolve().parents[1] / "refactored_gui" / "quality_dashboard.py"
_DASHBOARD_SPEC = spec_from_file_location("quality_dashboard_under_test", _DASHBOARD_PATH)
assert _DASHBOARD_SPEC is not None and _DASHBOARD_SPEC.loader is not None
dashboard = module_from_spec(_DASHBOARD_SPEC)
_DASHBOARD_SPEC.loader.exec_module(dashboard)


class _SessionState(dict):
    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError as exc:
            raise AttributeError(name) from exc

    def __setattr__(self, name, value):
        self[name] = value


class _DummyCtx:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class _DummyPlaceholder:
    def info(self, _msg):
        return None

    def progress(self, *_args, **_kwargs):
        return None


class _FakeStreamlit:
    def __init__(self):
        self.session_state = _SessionState()
        self._button_by_key = {
            "regen_execute": True,
        }
        self.success_messages = []

    def header(self, *_args, **_kwargs):
        return None

    def caption(self, *_args, **_kwargs):
        return None

    def subheader(self, *_args, **_kwargs):
        return None

    def markdown(self, *_args, **_kwargs):
        return None

    def divider(self, *_args, **_kwargs):
        return None

    def metric(self, *_args, **_kwargs):
        return None

    def write(self, *_args, **_kwargs):
        return None

    def text(self, *_args, **_kwargs):
        return None

    def json(self, *_args, **_kwargs):
        return None

    def warning(self, *_args, **_kwargs):
        return None

    def error(self, *_args, **_kwargs):
        return None

    def info(self, *_args, **_kwargs):
        return None

    def success(self, message, *_args, **_kwargs):
        self.success_messages.append(str(message))

    def spinner(self, *_args, **_kwargs):
        return nullcontext()

    def expander(self, *_args, **_kwargs):
        return _DummyCtx()

    def checkbox(self, _label, **kwargs):
        return kwargs.get("value", False)

    def progress(self, *_args, **_kwargs):
        return _DummyPlaceholder()

    def empty(self):
        return _DummyPlaceholder()

    def columns(self, count, *_args, **_kwargs):
        return [_DummyCtx() for _ in range(count)]

    def button(self, _label, key=None, **_kwargs):
        return bool(self._button_by_key.get(key, False))

    def number_input(self, _label, **kwargs):
        return kwargs.get("value", 100)

    def slider(self, _label, *_args, **kwargs):
        return kwargs.get("value", 0.3)

    def selectbox(self, _label, options, **_kwargs):
        return options[0]


class _FakeQualityManager:
    def __init__(self):
        self.calls = []

    def get_db_health_stats(self):
        return {
            "total_chunks": 1,
            "total_triples": 1,
            "total_documents": 1,
            "avg_structural_score": 0.8,
            "min_structural_score": 0.8,
            "max_structural_score": 0.8,
            "avg_grounding_score": 0.9,
            "quarantine_count": 0,
            "audit_log_count": 0,
        }

    def regenerate_quarantined_triples(self, **kwargs):
        self.calls.append(kwargs)
        return {
            "dry_run": False,
            "quarantine_entries_processed": 2,
            "recoverable_quarantine_entries": 1,
            "unrecoverable_quarantine_entries": 1,
            "source_chunks_found": 1,
            "source_chunks_missing": 1,
            "already_regenerated_skipped": 0,
            "triples_extracted": 1,
            "triples_grounded": 1,
            "triples_inserted": 1,
            "triples_duplicate_skipped": 0,
            "triples_ungrounded_skipped": 0,
            "quarantine_marked_completed": 1,
            "quarantine_marked_failed": 1,
            "duration_seconds": 0.1,
            "errors": [],
        }


def test_dashboard_regeneration_execute_invokes_quality_manager(monkeypatch):
    fake_st = _FakeStreamlit()
    fake_qm = _FakeQualityManager()

    monkeypatch.setattr(dashboard, "st", fake_st)
    monkeypatch.setattr(dashboard, "_get_quality_manager", lambda: fake_qm)

    dashboard.render_quality_dashboard()

    assert len(fake_qm.calls) == 1
    call = fake_qm.calls[0]
    assert call["dry_run"] is False
    assert call["batch_size"] == 100
    assert call["min_grounding_score"] == 0.3
    assert callable(call["progress_callback"])
    assert any("Nicht rekonstruierbar" in msg for msg in fake_st.success_messages)
