#!/usr/bin/env python3
"""
Melt Performance Benchmark (v3): test_melt_benchmark.py

Run from the repo root, with the kiosk stopped:
    python3 test_melt_benchmark.py
    python3 test_melt_benchmark.py --frame raw_camera_frame.png --budget-ms 15

What it measures, per scenario and knob value
    prep     : snapshot copy + grayscale conversion (done inside apply_melt_pass)
    collect  : _collect_melt_drips
    render   : _render_melt_drips
    e2e      : apply_melt_pass() end to end (the number that matters)
and derives the per-drip render cost and a suggested MAX_MELT_DRIPS.

Pass/fail
    The criterion (--budget-ms, default 20) is applied to the p95 of e2e.
    It is ENFORCED (non-zero exit code) only on a Raspberry Pi, or with --enforce.
    On any other machine the verdict is printed as INDICATIVE ONLY.

Repeatability
    Timestamps come from a fixed sequence, so the drip pattern is identical run to run.
"""
import argparse
import os
import subprocess
import sys
import time

import cv2
import numpy as np

# Allow importing server.py on a machine without a .env (tests never upload anything)
os.environ.setdefault("IK_URL_ENDPOINT", "https://ik.imagekit.io/mock")
os.environ.setdefault("IK_PRIVATE_KEY", "mock_key")

import server  # noqa: E402

CAP_HEADROOM = 0.70  # use at most 70% of the budget when suggesting a cap


# --------------------------------------------------------------------------
# Environment helpers
# --------------------------------------------------------------------------
def device_model():
    try:
        with open("/proc/device-tree/model", "rb") as fh:
            return fh.read().decode(errors="ignore").strip("\x00\n ")
    except OSError:
        return None


def run_cmd(cmd):
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=3)
        return out.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None


def pi_health():
    """Temperature, throttle flags and CPU clock (Raspberry Pi only)."""
    parts = []
    temp = run_cmd(["vcgencmd", "measure_temp"])
    if temp:
        parts.append(temp)
    thr = run_cmd(["vcgencmd", "get_throttled"])
    if thr:
        parts.append(thr)
    try:
        with open("/sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq") as fh:
            parts.append(f"cpu0={int(fh.read().strip()) / 1000:.0f} MHz")
    except (OSError, ValueError):
        pass
    return " | ".join(parts) if parts else "n/a"


def kiosk_running():
    out = run_cmd(["pgrep", "-af", "pygame_display.py"])
    return [ln for ln in (out or "").splitlines() if "pgrep" not in ln]


# --------------------------------------------------------------------------
# Scenario frames
# --------------------------------------------------------------------------
def make_portrait():
    buf = np.zeros((server.FRAME_H, server.FRAME_W, 3), dtype=np.uint8)
    buf[100:380, 200:440] = 180
    buf[140:300, 260:380] = 230
    return buf


def make_noise(seed=1337):
    rng = np.random.default_rng(seed)
    return rng.integers(140, 256, (server.FRAME_H, server.FRAME_W, 3), dtype=np.uint8)


def make_textured(seed=7):
    """Soft blobs on a dark background: closer to a real lit scene than flat blocks."""
    rng = np.random.default_rng(seed)
    base = rng.integers(0, 256, (server.FRAME_H // 8, server.FRAME_W // 8, 3), dtype=np.uint8)
    img = cv2.resize(base, (server.FRAME_W, server.FRAME_H), interpolation=cv2.INTER_CUBIC)
    img = cv2.GaussianBlur(img, (0, 0), 6)
    return np.ascontiguousarray(img)


def load_user_frame(path):
    img = cv2.imread(path)
    if img is None:
        sys.exit(f"[BENCHMARK] Could not read --frame {path}")
    if img.shape[:2] != (server.FRAME_H, server.FRAME_W):
        print(f"[BENCHMARK] Resizing {path} {img.shape[1]}x{img.shape[0]} -> "
              f"{server.FRAME_W}x{server.FRAME_H}")
        img = cv2.resize(img, (server.FRAME_W, server.FRAME_H))
    return np.ascontiguousarray(img)


# --------------------------------------------------------------------------
# Measurement
# --------------------------------------------------------------------------
def pct(values, q):
    return float(np.percentile(values, q))


def benchmark_scenario(name, canvas, knob, warmup, runs):
    n_total = warmup + runs
    prep, collect, render, e2e, n_drips = [], [], [], [], []

    for i in range(n_total):
        t_sim = 1000.0 + i * 0.25  # fixed, repeatable timestamps (a new tick every run)

        # End-to-end pass exactly as the pipeline calls it
        server.MUTABLE_PROCESSING_BUFFER[:] = canvas
        t0 = time.perf_counter()
        server.apply_melt_pass(melt_raw=knob, current_time=t_sim)
        t1 = time.perf_counter()

        # Same pass again, split into its phases
        server.MUTABLE_PROCESSING_BUFFER[:] = canvas
        a = time.perf_counter()
        server.STATIC_MELT_SNAPSHOT[:] = server.MUTABLE_PROCESSING_BUFFER[:]
        cv2.cvtColor(server.STATIC_MELT_SNAPSHOT, cv2.COLOR_BGR2GRAY,
                     dst=server.STATIC_MELT_GRAY_BUFFER)
        b = time.perf_counter()
        drips = server._collect_melt_drips(melt_raw=knob, current_time=t_sim)
        c = time.perf_counter()
        server._render_melt_drips(drips)
        d = time.perf_counter()

        if i >= warmup:
            e2e.append((t1 - t0) * 1e3)
            prep.append((b - a) * 1e3)
            collect.append((c - b) * 1e3)
            render.append((d - c) * 1e3)
            n_drips.append(len(drips))

    mean_drips = float(np.mean(n_drips))
    per_drip_us = (pct(render, 95) * 1e3 / mean_drips) if mean_drips > 0 else 0.0
    row = {
        "name": name, "knob": knob, "drips": mean_drips,
        "e2e_p50": pct(e2e, 50), "e2e_p95": pct(e2e, 95), "e2e_max": float(np.max(e2e)),
        "prep_p95": pct(prep, 95), "collect_p95": pct(collect, 95),
        "render_p95": pct(render, 95), "per_drip_us": per_drip_us,
    }
    print(f"[{name:<9}] knob {knob:0.2f} ({mean_drips:5.1f} drips) "
          f"e2e p50 {row['e2e_p50']:6.2f} p95 {row['e2e_p95']:6.2f} max {row['e2e_max']:6.2f} ms | "
          f"prep {row['prep_p95']:4.2f} collect {row['collect_p95']:4.2f} "
          f"render {row['render_p95']:5.2f} (p95) | {per_drip_us:5.0f} us/drip")
    return row


def suggest_cap(rows, budget_ms):
    """Cap such that prep + collect + cap * per_drip stays within 70% of the budget."""
    heavy = [r for r in rows if r["knob"] >= 0.8 and r["drips"] > 0]
    if not heavy:
        return None
    worst_per_drip_ms = max(r["per_drip_us"] for r in heavy) / 1e3
    fixed_ms = max(r["prep_p95"] + r["collect_p95"] for r in heavy)
    room = CAP_HEADROOM * budget_ms - fixed_ms
    if room <= 0 or worst_per_drip_ms <= 0:
        return 0
    return int(room / worst_per_drip_ms)


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="Melt stage benchmark")
    ap.add_argument("--runs", type=int, default=40)
    ap.add_argument("--warmup", type=int, default=5)
    ap.add_argument("--budget-ms", type=float, default=20.0,
                    help="p95 budget for apply_melt_pass (also drives the cap suggestion)")
    ap.add_argument("--frame", help="raw camera frame (png/jpg). Do NOT use captures/*_color.jpg: "
                                    "those are post-effects images")
    ap.add_argument("--enforce", action="store_true",
                    help="return a non-zero exit code on failure even when not on a Pi")
    args = ap.parse_args()

    model = device_model()
    on_pi = bool(model and "Raspberry Pi" in model)

    print("=" * 74)
    print(" GLITCHBOOTH MELT BENCHMARK (v3)")
    print("=" * 74)
    print(f" Machine   : {model or 'NOT a Raspberry Pi (' + sys.platform + ')'}")
    print(f" Libraries : numpy {np.__version__}, opencv {cv2.__version__}, python {sys.version.split()[0]}")
    print(f" Settings  : MAX_MELT_DRIPS={server.MAX_MELT_DRIPS}, runs={args.runs}, "
          f"warmup={args.warmup}, budget={args.budget_ms} ms")
    if on_pi:
        print(f" Pi health : {pi_health()}")
    running = kiosk_running()
    if running:
        print(" WARNING   : the kiosk appears to be running; results will be inflated:")
        for ln in running:
            print(f"             {ln}")
    print("=" * 74)

    scenarios = [("portrait", make_portrait()), ("textured", make_textured()),
                 ("noise", make_noise())]
    if args.frame:
        scenarios.insert(0, ("userframe", load_user_frame(args.frame)))
    else:
        print(" Note      : no --frame given; synthetic scenes only. Use a raw camera frame\n"
              "             for the most representative numbers.")

    rows = []
    for name, canvas in scenarios:
        print(f"\n--- {name} ---")
        for knob in (0.2, 0.5, 0.8, 1.0):
            rows.append(benchmark_scenario(name, canvas, knob, args.warmup, args.runs))

    worst = max(r["e2e_p95"] for r in rows)
    cap = suggest_cap(rows, args.budget_ms)

    print("\n" + "=" * 74)
    print(f" Worst e2e p95 : {worst:6.2f} ms   (budget {args.budget_ms:.1f} ms)")
    if cap is not None:
        print(f" Suggested MAX_MELT_DRIPS for this machine: {cap} "
              f"(current {server.MAX_MELT_DRIPS}; {int(CAP_HEADROOM * 100)}% of budget)")
    noisy = [r for r in rows if r["e2e_p95"] > 1.5 * r["e2e_p50"]]
    if noisy:
        print(f" NOISY RUN     : {len(noisy)}/{len(rows)} rows have p95 > 1.5x p50 (background load, "
              "thermal throttling or a shared CPU).\n"
              "                 Treat the cap suggestion and the verdict as unreliable; "
              "re-run on an idle machine.")
    if on_pi:
        print(f" Pi health (after): {pi_health()}")

    passed = worst <= args.budget_ms
    enforce = on_pi or args.enforce
    if passed:
        verdict = "PASS" if enforce else "PASS (INDICATIVE ONLY: not a Raspberry Pi)"
    else:
        verdict = "FAIL" if enforce else "OVER BUDGET (INDICATIVE ONLY: not a Raspberry Pi)"
    print(f" Verdict       : {verdict}")
    print("=" * 74)
    return 0 if (passed or not enforce) else 1


if __name__ == "__main__":
    sys.exit(main())
