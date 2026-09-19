"""Temporaere Mapping-Helper (nach Lauf loeschen)."""
import io
import os

FILES = {
    "finance/tools.py": [
        "def create_series",
        "def update_series",
        "def undo_series_change",
        "def _series_spec",
        "def _series_exceptions_map",
        "def _cadence_equivalents",
        "def _series_error_class",
        "def _coerce_series_id",
        "def _coerce_iso_date",
    ],
    "agent/toolkit.py": ["finance_"],
    "agent/tool_schemas.py": [
        "finance_upcoming_bills",
        "finance_subscription_audit",
        "finance_series_calendar",
        "finance_pause_series",
        "finance_skip_occurrence",
        "finance_confirm_candidate",
        "finance_detect_series_candidates",
        "PROFILE",
    ],
    "finance/chat.py": [
        "upcoming_bills",
        "subscription_audit",
        "series",
        "suppress",
        "ROUTING",
        "retry",
    ],
    "finance/query_reflector.py": [
        "upcoming_bills",
        "subscription_audit",
        "series",
        "suppress",
        "retry",
    ],
}


def main() -> None:
    for path, ns in FILES.items():
        if not os.path.exists(path):
            print(path, "MISSING")
            continue
        lines = io.open(path, encoding="utf-8").read().splitlines()
        print("====", path, len(lines))
        for i, line in enumerate(lines):
            if any(n in line for n in ns):
                print(i + 1, line.strip()[:110])


if __name__ == "__main__":
    main()
