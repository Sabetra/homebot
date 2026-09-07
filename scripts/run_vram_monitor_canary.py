"""Run a VRAM observability canary loop with adaptive thresholds.

This script does not generate GPU load by itself. It is intended to run in
parallel with a target workload (for example model loading or eval runs) and
verify:
  - adaptive thresholds are computed,
  - alerts/defrag decisions are stable,
  - optional telemetry export path does not break runtime.
"""

from __future__ import annotations

import argparse
import os
import sys
import statistics
import time
from typing import List

# Allow direct execution via `python scripts/run_vram_monitor_canary.py`.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from utils.vram_monitor import VRAMSnapshot, get_vram_monitor


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="VRAM monitor canary runner")
    parser.add_argument("--duration-sec", type=int, default=90, help="Canary duration in seconds")
    parser.add_argument("--interval-sec", type=float, default=2.0, help="Sampling interval in seconds")
    parser.add_argument("--workload", type=str, default="canary", help="Workload profile name")
    parser.add_argument("--model-family", type=str, default="unknown", help="Model family label")
    parser.add_argument("--n-ctx", type=int, default=16384, help="Context window label for adaptive policy")
    parser.add_argument(
        "--enable-defrag",
        action="store_true",
        help="Allow adaptive defragmentation during canary",
    )
    return parser.parse_args()


def summarize(samples: List[VRAMSnapshot], alerts: int, defrags: int) -> str:
    if not samples:
        return "No samples collected."

    util = [s.utilization_pct for s in samples]
    frag = [s.torch_fragmentation_gb for s in samples]

    parts = [
        "VRAM canary summary:",
        f"  samples: {len(samples)}",
        f"  utilization avg/max: {statistics.mean(util):.2f}% / {max(util):.2f}%",
        f"  fragmentation avg/max: {statistics.mean(frag):.3f} GB / {max(frag):.3f} GB",
        f"  alerts observed: {alerts}",
        f"  defrags executed: {defrags}",
    ]
    return "\n".join(parts)


def main() -> int:
    args = parse_args()
    monitor = get_vram_monitor()
    monitor.set_runtime_profile(
        model_family=args.model_family,
        n_ctx=args.n_ctx,
        workload=args.workload,
    )

    start = time.time()
    alerts = 0
    defrags = 0
    snapshots: List[VRAMSnapshot] = []

    print(
        "Starting VRAM canary "
        f"(duration={args.duration_sec}s, interval={args.interval_sec}s, "
        f"workload={args.workload}, n_ctx={args.n_ctx})"
    )

    while (time.time() - start) < args.duration_sec:
        thresholds = monitor.get_adaptive_thresholds()
        snap = monitor.check_and_alert()
        if snap is None:
            print("No GPU monitoring backend available. Exiting canary.")
            return 1

        snapshots.append(snap)

        if snap.utilization_pct >= thresholds.alert_pct:
            alerts += 1

        if args.enable_defrag and snap.torch_fragmentation_gb >= thresholds.defrag_frag_gb:
            if monitor.defragment_if_needed():
                defrags += 1

        print(
            f"t={int(time.time() - start):>3}s "
            f"util={snap.utilization_pct:>6.2f}% "
            f"used={snap.used_gb:>6.2f}GB "
            f"frag={snap.torch_fragmentation_gb:>5.2f}GB "
            f"alert@{thresholds.alert_pct:>5.1f}% "
            f"defrag@{thresholds.defrag_frag_gb:>4.2f}GB"
        )
        time.sleep(args.interval_sec)

    print(summarize(snapshots, alerts=alerts, defrags=defrags))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
