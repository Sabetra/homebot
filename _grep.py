import io

PATS = [
    "def upcoming_bills",
    "def subscription_audit",
    "def _recurring_groups",
    "def _next_due_on_or_after",
    "def list_series",
    "def pause_series",
    "def confirm_candidate",
    "def skip_occurrence",
    "def change_occurrence_amount",
    "def set_series_status",
    "def create_series",
    "def update_series",
    "def resume_series",
    "def end_series",
    "def reject_candidate",
    "def detect_series_candidates",
    "def _plan_relevant_series",
    "def _series_window_occurrences",
    "def _series_scope_pairs",
    "forecast_suppressions",
    "def suppress",
    "def restore",
]
FILES = [
    "finance/tools.py",
    "finance/db_schema.py",
    "agent/tool_schemas.py",
    "agent/tool_profiles.py",
    "agent/agent_toolkit.py",
    "finance/tab.py",
]

for path in FILES:
    print("==", path)
    try:
        lines = io.open(path, encoding="utf-8").read().splitlines()
    except OSError as exc:
        print("  ERR", exc)
        continue
    for i, line in enumerate(lines, 1):
        for p in PATS:
            if p in line:
                print("  %d: %s" % (i, line.strip()[:110]))
                break
