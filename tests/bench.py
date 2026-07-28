#!/usr/bin/env python3
"""Performance budget — "barely noticeable" has to be a number, not a claim.

Every figure below is measured on the machine running it and checked against a
budget. CI fails if any budget is exceeded, so a future change cannot quietly
make the tool sluggish.

Run: python3 tests/bench.py [--json]
"""

import json
import os
import statistics
import subprocess
import sys
import time
import tracemalloc

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Budgets are deliberately generous vs. observed values so CI is not flaky,
# but tight enough that a real regression trips them.
BUDGETS = {
    "config_load_ms": 25.0,        # parsing + layer resolution
    "artifact_generate_ms": 25.0,  # generating a platform artifact
    "engine_us_per_event": 25.0,   # the hot path — must be microseconds
    "tui_frame_ms": 20.0,          # a full repaint (50 fps headroom)
    "cli_startup_ms": 900.0,       # cold interpreter + import + check
    "import_ms": 250.0,            # importing the package
    "engine_kb": 512.0,            # steady-state engine allocation
}


def _timed(fn, n=200):
    times = []
    for _ in range(n):
        t0 = time.perf_counter()
        fn()
        times.append((time.perf_counter() - t0) * 1000.0)
    return statistics.median(times)


def measure() -> dict:
    from keyremap.config import load
    from keyremap.backends import macos, windows
    from keyremap.engine import Engine
    from keyremap.config import Action

    cfg_path = os.path.join(ROOT, "config.yaml")
    out = {}

    out["config_load_ms"] = _timed(lambda: load(cfg_path, plat="darwin",
                                                host="bench"), 100)
    cfg_mac = load(cfg_path, plat="darwin", host="bench")
    cfg_win = load(cfg_path, plat="windows", host="bench")
    out["artifact_generate_ms"] = max(
        _timed(lambda: macos.generate(cfg_mac), 100),
        _timed(lambda: windows.generate_interception(cfg_win), 100))

    # --- the hot path: one key event through the engine
    act = Action(tap=[(["accel"], "a")], hold=[(["accel"], "c")], hold_ms=400)
    e = Engine({1: act, 2: Action(press=[([], "end")])})
    N = 20000
    t0 = time.perf_counter()
    for i in range(N):
        e.feed(2, 1, i)      # simple remap down
        e.feed(2, 0, i)      # up
    elapsed = time.perf_counter() - t0
    out["engine_us_per_event"] = (elapsed / (N * 2)) * 1e6
    out["engine_events_per_sec"] = int((N * 2) / elapsed)

    tracemalloc.start()
    e2 = Engine({1: act})
    for i in range(5000):
        e2.feed(1, 1, i)
        e2.due(i + 500)
        e2.feed(1, 0, i + 600)
    out["engine_kb"] = tracemalloc.get_traced_memory()[1] / 1024.0
    tracemalloc.stop()

    # --- TUI frame
    from keyremap.tui.app import App
    app = App(cfg_path)
    app.status = None
    out["tui_frame_ms"] = _timed(lambda: app.render(), 100)

    # --- cold start of the real CLI
    starts = []
    for _ in range(3):
        t0 = time.perf_counter()
        subprocess.run([sys.executable, os.path.join(ROOT, "remap.py"), "check"],
                       capture_output=True)
        starts.append((time.perf_counter() - t0) * 1000.0)
    out["cli_startup_ms"] = min(starts)

    t0 = time.perf_counter()
    subprocess.run([sys.executable, "-c", "import keyremap.config"],
                   capture_output=True, cwd=ROOT)
    out["import_ms"] = (time.perf_counter() - t0) * 1000.0
    return out


def main() -> int:
    from keyremap.console import utf8
    utf8()
    got = measure()
    as_json = "--json" in sys.argv
    if as_json:
        print(json.dumps(got, indent=2))

    print("\nkeyremap performance\n")
    worst = 0
    for key, budget in BUDGETS.items():
        val = got[key]
        ok = val <= budget
        worst = max(worst, 0 if ok else 1)
        unit = "µs" if key.endswith("_us_per_event") else (
            "KB" if key.endswith("_kb") else "ms")
        mark = "✓" if ok else "✗"
        print(f"  {mark} {key:<26}{val:8.2f} {unit}   (budget {budget:.0f})")
    print(f"\n  engine throughput: {got['engine_events_per_sec']:,} events/sec")
    print("\n  " + ("ALL WITHIN BUDGET" if not worst else "OVER BUDGET — fix before shipping"))
    return worst


if __name__ == "__main__":
    sys.exit(main())
