#!/usr/bin/env python
"""Chat-Performance-Report (SOTA, 2026-09-11).

Liest die Telemetrie-DB ``chat_perf.db`` (Single Source of Truth:
``utils/db_path_resolver.get_db_path``) und berichtet:

- p50/p90/p99 fuer ``ttft_ms``, ``total_ms``, ``tokens_per_second``
- Engine-Metriken (llama.cpp Perf-Context): ``llm_prefill_ms``,
  ``llm_generation_ms``, ``llm_prompt_tokens``, ``llm_generation_tokens``,
  ``llm_n_reused``
- Pipeline-Overhead-Schatzung: ``total_ms - (prefill_ms + generation_ms)``
  (nur Runs mit Engine-Metriken; umfasst RAG-Routing, Tool-Loops, UI-Pfade)

Gruppierung: gesamt, pro ``route``, pro Tag (``ts_utc``-Kalenderdatum UTC).

Nutzung:
    python scripts/perf_report.py                 # Letzten 14 Tage
    python scripts/perf_report.py --days 30       # Letzten 30 Tage
    python scripts/perf_report.py --all           # Alle Daten
    python scripts/perf_report.py --json          # Maschinell lesbar (stdout)
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from utils.db_path_resolver import get_db_path  # noqa: E402
from utils import chat_perf_recorder as cpr  # noqa: E402  (DB_FILENAME)

_METRIC_COLUMNS: Dict[str, str] = {
    "ttft_ms": "ttft_ms",
    "total_ms": "total_ms",
    "tokens_per_second": "tokens_per_second",
    "llm_prefill_ms": "llm_prefill_ms",
    "llm_generation_ms": "llm_generation_ms",
    "llm_prompt_tokens": "llm_prompt_tokens",
    "llm_generation_tokens": "llm_generation_tokens",
    "llm_n_reused": "llm_n_reused",
}


def _percentile(values: Sequence[float], pct: float) -> Optional[float]:
    """Linear-Interpolierter Perzentilwert (numpy-Default-Verhalten)."""
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    rank = (pct / 100.0) * (len(ordered) - 1)
    lo = int(rank)
    hi = min(lo + 1, len(ordered) - 1)
    frac = rank - lo
    return float(ordered[lo] * (1.0 - frac) + ordered[hi] * frac)


def _stats(values: Sequence[float]) -> Dict[str, Optional[float]]:
    if not values:
        return {"n": 0, "avg": None, "p50": None, "p90": None, "p99": None}
    return {
        "n": len(values),
        "avg": round(sum(values) / len(values), 2),
        "p50": round(_percentile(values, 50), 2),
        "p90": round(_percentile(values, 90), 2),
        "p99": round(_percentile(values, 99), 2),
    }


def _query_rows(db_path: str, since: Optional[datetime]) -> List[Dict[str, Any]]:
    """Liest Runs read-only; optionale Spalten (step_summary/trace_summary)
    werden nur gewählt, wenn sie in der DB existieren (Schema-V1- und V2-DBs)."""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        conn.row_factory = sqlite3.Row
        available = {row[1] for row in conn.execute("PRAGMA table_info(chat_perf_runs)")}
        extra = [c for c in ("step_summary", "trace_summary") if c in available]
        sql = (
            "SELECT ts_utc, run_id, route, status, ttft_ms, total_ms, tokens_per_second,"
            " llm_calls, llm_prefill_ms, llm_generation_ms, llm_prompt_tokens,"
            " llm_generation_tokens, llm_n_reused, step_count, error_code"
            + (", " + ", ".join(extra) if extra else "")
            + " FROM chat_perf_runs"
        )
        params: List[Any] = []
        if since is not None:
            sql += " WHERE ts_utc >= ?"
            params.append(since.isoformat(timespec="seconds"))
        sql += " ORDER BY ts_utc ASC"
        rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()
    return rows


def _group_metric(rows: List[Dict[str, Any]], column: str) -> List[float]:
    return [float(r[column]) for r in rows if isinstance(r.get(column), (int, float))]


def _summarize(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "runs": len(rows),
        "by_status": {},
    }
    for status in ("completed", "cancelled", "failed"):
        out["by_status"][status] = sum(1 for r in rows if r.get("status") == status)
    for label, column in _METRIC_COLUMNS.items():
        out[label] = _stats(_group_metric(rows, column))
    # Pipeline-Overhead: total - (prefill + generation), nur vollstaendige Engine-Metriken
    overhead = [
        float(r["total_ms"]) - (float(r["llm_prefill_ms"]) + float(r["llm_generation_ms"]))
        for r in rows
        if all(isinstance(r.get(c), (int, float)) for c in ("total_ms", "llm_prefill_ms", "llm_generation_ms"))
    ]
    out["pipeline_overhead_ms"] = _stats(overhead)
    return out


def build_report(days: Optional[int]) -> Dict[str, Any]:
    db_path = str(get_db_path(cpr.DB_FILENAME))
    if not os.path.exists(db_path):
        return {"error": f"DB nicht gefunden: {db_path}", "runs": 0}
    try:
        since = None if days is None else datetime.now(timezone.utc) - timedelta(days=days)
        rows = _query_rows(db_path, since)
    except sqlite3.Error as exc:
        return {"error": f"DB-Lesefehler: {exc}", "runs": 0}
    if not rows:
        window = f"letzte {days} Tage" if days is not None else "Gesamtzeitraum"
        return {"error": f"Keine Daten ({window})", "runs": 0, "db_path": db_path}

    by_route: Dict[str, List[Dict[str, Any]]] = {}
    by_day: Dict[str, List[Dict[str, Any]]] = {}
    for r in rows:
        route = r.get("route") or "unknown"
        by_route.setdefault(route, []).append(r)
        day = str(r.get("ts_utc") or "")[:10] or "unknown"
        by_day.setdefault(day, []).append(r)

    recent = sorted(rows, key=lambda r: str(r.get("ts_utc") or ""), reverse=True)[:15]
    return {
        "db_path": db_path,
        "window": (f"letzte {days} Tage" if days is not None else "Gesamtzeitraum"),
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall": _summarize(rows),
        "by_route": {k: _summarize(v) for k, v in sorted(by_route.items())},
        "by_day": {k: _summarize(v) for k, v in sorted(by_day.items())},
        "recent_runs": [
            {
                k: r.get(k)
                for k in (
                    "ts_utc", "run_id", "route", "status", "ttft_ms", "total_ms",
                    "tokens_per_second", "llm_calls", "llm_prefill_ms",
                    "llm_generation_ms", "step_summary", "trace_summary",
                )
            }
            for r in recent
        ],
    }


def _fmt(v: Optional[float]) -> str:
    return "-" if v is None else (f"{v:,.0f}" if v == int(v) else f"{v:,.1f}")


def print_text(report: Dict[str, Any]) -> None:
    if report.get("error"):
        print(f"⚠️  {report['error']}")
        if report.get("db_path"):
            print(f"   DB: {report['db_path']}")
        return

    print(f"Chat-Performance-Report — {report['window']}")
    print(f"DB: {report['db_path']}")
    print(f"Erstellt: {report['generated_at_utc']}")

    def print_block(title: str, s: Dict[str, Any]) -> None:
        print(f"\n=== {title} ===")
        print(
            f"Runs: {s['runs']}  "
            f"(completed={s['by_status']['completed']}, "
            f"cancelled={s['by_status']['cancelled']}, failed={s['by_status']['failed']})"
        )
        header = f"{'Metrik':<24}{'avg':>12}{'p50':>12}{'p90':>12}{'p99':>12}{'n':>8}"
        print(header)
        print("-" * len(header))
        for label in (
            "ttft_ms", "total_ms", "tokens_per_second",
            "llm_prefill_ms", "llm_generation_ms",
            "llm_prompt_tokens", "llm_generation_tokens", "llm_n_reused",
            "pipeline_overhead_ms",
        ):
            m = s.get(label, {})
            print(
                f"{label:<24}{_fmt(m.get('avg')):>12}{_fmt(m.get('p50')):>12}"
                f"{_fmt(m.get('p90')):>12}{_fmt(m.get('p99')):>12}{m.get('n', 0):>8}"
            )

    print_block("Gesamt", report["overall"])
    for route, s in report["by_route"].items():
        print_block(f"Route: {route}", s)
    for day, s in report["by_day"].items():
        print_block(f"Tag: {day}", s)

    recent = report.get("recent_runs") or []
    if recent:
        print("\n=== Recent Runs (neueste zuerst) ===")
        for r in recent:
            print(
                f"{str(r.get('ts_utc') or '')[:16]}  {(r.get('route') or '?'):<13}"
                f"total={_fmt(r.get('total_ms')):>12}ms  "
                f"tok_s={_fmt(r.get('tokens_per_second')):>8}  "
                f"llm_calls={r.get('llm_calls') or 0}"
            )
            if r.get("step_summary"):
                print(f"    steps: {r['step_summary'][:220]}")
            if r.get("trace_summary"):
                print(f"    trace: {r['trace_summary'][:220]}")
    print(
        "\nHinweis: pipeline_overhead_ms = total_ms - (prefill_ms + generation_ms); "
        "negativ wenn Engine-Zeit die Run-Dauer uebersteigt (z.B. parallele Pfade)."
    )


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Chat-Performance-Report aus chat_perf.db")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--days", type=int, default=14, help="Zeitraum in Tagen (Default: 14)")
    group.add_argument("--all", action="store_true", help="Alle Daten ohne Zeitfenster")
    parser.add_argument("--json", action="store_true", help="JSON-Output statt Text-Tabelle")
    args = parser.parse_args(argv)

    days = None if args.all else max(args.days, 1)
    report = build_report(days)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print_text(report)
    return 1 if report.get("error") else 0


if __name__ == "__main__":
    raise SystemExit(main())