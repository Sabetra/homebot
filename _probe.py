import io

def scan(path, needles):
    src = io.open(path, encoding="utf-8").read().splitlines()
    for i, l in enumerate(src):
        s = l.strip()
        if any(n in l for n in needles):
            print(path, i + 1, s[:100])

scan("agent/tool_schemas.py", ['"name": "finance_'])
print("---- profiles done ----")
scan("finance/tools.py", ["def upcoming_bills", "def subscription_audit", "def cash_flow_forecast",
                          "def _recurring_groups", "def _estimate_cadence", "def pause_series",
                          "def skip_occurrence", "def change_occurrence_amount", "def confirm_candidate",
                          "def create_series", "def _next_due_on_or_after", "def _bootstrap_interval",
                          "def _facts_up_to", "def series_calendar", "def list_series", "def detect_series_candidates",
                          "def set_series_status", "def _series_to_dict"])
print("---- db_schema ----")
scan("finance/db_schema.py", ["def create_series", "def update_series", "def confirm_candidate",
                              "def reject_candidate", "def set_series_status", "def list_series",
                              "def set_series_exception", "def skip_occurrence", "def list_analysis_facts",
                              "def _init_schema", "def _series_from_row", "def _recurring_series_from_row",
                              "def set_series_note", "def _journal"])
print("---- series_engine ----")
scan("finance/series_engine.py", ["def estimate_cadence", "def expand_series", "def next_occurrences",
                                  "def fit_cadence", "class SeriesSpec", "class CadenceEstimate", "def anchor_date",
                                  "def next_due_on_or_after"])
print("---- tab ----")
scan("finance/tab.py", ["_render_forecast_tab", "_render_forecast_bills", "_render_forecast_audit",
                        "_render_forecast_series", "_render_forecast_candidates", "def render_finance_tab"])
print("---- chat ----")
scan("finance/chat.py", ["finance_upcoming_bills", "finance_subscription_audit", "def route", "ROUTING"])
print("---- reflector ----")
scan("finance/query_reflector.py", ["retry_upcoming_bills", "retry_subscription_audit", "RETRY"])
print("---- tests ----")
scan("tests/test_finance_forecast_ux_stage2.py", ["def test_"])
print("----")
