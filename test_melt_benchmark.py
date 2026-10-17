#!/usr/bin/env python3
"""
Melt Hardware Performance Benchmark: test_melt_benchmark.py
Measures collect vs render latencies separately.
Uses real capture image if present, reports p50, p95, max,
and validates against pass criterion (p95 <= 20.0 ms).
"""
import os
import time
import cv2
import numpy as np

# Set mock env vars if .env is missing so server can be imported for offline testing
os.environ.setdefault("IK_URL_ENDPOINT", "https://ik.imagekit.io/mock")
os.environ.setdefault("IK_PRIVATE_KEY", "mock_key")

import server

print("==================================================")
print(" GLITCHBOOTH MELT HARDWARE BENCHMARK")
print("==================================================")

NUM_WARMUP = 5
NUM_RUNS = 40
PASS_CRITERION_MS = 20.0
rng = np.random.default_rng(1337)

# Load real capture photo if available, otherwise synthetic high-contrast scene
real_photo = None
if os.path.exists(server.CAPTURE_DIR):
    for f in os.listdir(server.CAPTURE_DIR):
        if f.endswith("_color.jpg"):
            loaded = cv2.imread(os.path.join(server.CAPTURE_DIR, f))
            if loaded is not None and loaded.shape[:2] == (server.FRAME_H, server.FRAME_W):
                real_photo = loaded
                print(f"[BENCHMARK] Loaded real capture asset: {f}")
                break

if real_photo is None:
    print("[BENCHMARK] Using synthetic high-contrast portrait canvas.")
    real_photo = np.zeros((server.FRAME_H, server.FRAME_W, 3), dtype=np.uint8)
    real_photo[100:380, 200:440] = 180
    real_photo[140:300, 260:380] = 230

def benchmark_scenario(name, canvas, knob_val):
    total_lats, collect_lats, render_lats = [], [], []

    for i in range(NUM_WARMUP + NUM_RUNS):
        # Reset canvas each run
        server.MUTABLE_PROCESSING_BUFFER[:] = canvas[:]
        cur_time = time.time()

        # Step 1: Time Collect
        server.STATIC_MELT_SNAPSHOT[:] = server.MUTABLE_PROCESSING_BUFFER[:]
        cv2.cvtColor(server.STATIC_MELT_SNAPSHOT, cv2.COLOR_BGR2GRAY, dst=server.STATIC_MELT_GRAY_BUFFER)
        
        t0 = time.perf_counter()
        drips = server._collect_melt_drips(melt_raw=knob_val, current_time=cur_time)
        t1 = time.perf_counter()
        
        # Step 2: Time Render
        server._render_melt_drips(drips)
        t2 = time.perf_counter()

        if i >= NUM_WARMUP:
            collect_ms = (t1 - t0) * 1000.0
            render_ms = (t2 - t1) * 1000.0
            collect_lats.append(collect_ms)
            render_lats.append(render_ms)
            total_lats.append(collect_ms + render_ms)

    p50 = np.percentile(total_lats, 50)
    p95 = np.percentile(total_lats, 95)
    max_t = np.max(total_lats)
    c_p50 = np.percentile(collect_lats, 50)
    r_p50 = np.percentile(render_lats, 50)

    print(f"[{name}] Knob {knob_val:0.2f} ({len(drips)} drips) -> "
          f"Total p50: {p50:5.2f} ms (Collect: {c_p50:4.2f} ms, Render: {r_p50:4.2f} ms) | "
          f"p95: {p95:5.2f} ms | max: {max_t:5.2f} ms")
    return p95

print("\n--- Realistic Canvas Latencies ---")
worst_p95 = 0.0
for k in (0.2, 0.5, 0.8, 1.0):
    p95 = benchmark_scenario("Real/Portrait", real_photo, k)
    worst_p95 = max(worst_p95, p95)

print("\n--- Worst-Case Noise Canvas Latencies ---")
noise_canvas = rng.integers(140, 256, (server.FRAME_H, server.FRAME_W, 3), dtype=np.uint8)
for k in (0.5, 1.0):
    p95 = benchmark_scenario("White Noise  ", noise_canvas, k)
    worst_p95 = max(worst_p95, p95)

print("\n==================================================")
if worst_p95 <= PASS_CRITERION_MS:
    print(f" PASS: Worst-case p95 ({worst_p95:.2f} ms) <= {PASS_CRITERION_MS:.1f} ms target.")
else:
    print(f" WARNING: Worst-case p95 ({worst_p95:.2f} ms) exceeded {PASS_CRITERION_MS:.1f} ms target. Consider tuning MAX_MELT_DRIPS.")
print("==================================================")
