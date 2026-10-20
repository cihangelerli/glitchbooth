#!/usr/bin/env python3
"""
Phase 1 Complete Invariant Test Suite: test_melt_phase1.py
Tests:
1. Pure-Python Reference vs. Production Renderer across multiple rows and (L, s) pairs.
2. Boundary Safety & Full-Canvas Framing Isolation with Active Negative Control (>90% mutates).
3. Overlap Precedence with inline reference calculation.
4. Defensive Guards: Invalid L and out-of-bounds coords leave canvas byte-identical.
5. Spatial Spread & Balance: Perfectly uniform left/right balance (0.8 <= left/right <= 1.25).
6. Determinism: Identical inputs produce bit-exact identical output.
7. Dormancy Invariant: melt_raw <= 0.03 leaves buffer 100% byte-identical.
"""
import os
import tracemalloc
import numpy as np

# Set mock env vars if .env is missing so server can be imported for offline testing
os.environ.setdefault("IK_URL_ENDPOINT", "https://ik.imagekit.io/mock")
os.environ.setdefault("IK_PRIVATE_KEY", "mock_key")

import server

print("==================================================")
print(" GLITCHBOOTH PHASE 1 PRODUCTION INVARIANT SUITE")
print("==================================================")

# --- TEST 1: Pure-Python Mathematical Reference vs Production Renderer ---
print("\n[TEST 1] Verifying production sampling against pure-Python reference equation...")
# Wrap-safe gradient: Red channel = y % 256
gradient = (np.arange(server.FRAME_H) % 256).astype(np.uint8)[:, None]
server.MUTABLE_PROCESSING_BUFFER.fill(0)
server.MUTABLE_PROCESSING_BUFFER[:, 320:324, 2] = gradient
server.STATIC_MELT_SNAPSHOT[:] = server.MUTABLE_PROCESSING_BUFFER[:]

test_drips = [
    # reach, x0, y0, L, s, p1, p2
    (250, 321, 100, 150, 2.5, 0, 1),
    (320, 321, 200, 120, 1.2, 2, 3),
]

for reach, x0, y0, L, s, p1, p2 in test_drips:
    server._render_melt_drips([(reach, x0, y0, L, s, p1, p2)])
    # Test multiple check points: y0, y0+1, midpoint, end-1
    check_rows = [y0, y0 + 1, y0 + L // 2, y0 + L - 1]
    for y_chk in check_rows:
        if y_chk >= server.FRAME_H:
            continue
        y_rel = float(y_chk - y0)
        norm_dist = y_rel / float(L)
        denom = max(1.0, 1.0 + s * np.sqrt(norm_dist))
        ys_ref = y0 + (y_rel / denom)
        
        y_fl = int(np.floor(ys_ref))
        y_ce = min(server.FRAME_H - 1, y_fl + 1)
        w_ce = ys_ref - y_fl
        expected_red = int(np.round((1.0 - w_ce) * (y_fl % 256) + w_ce * (y_ce % 256)))
        
        actual_red = server.MUTABLE_PROCESSING_BUFFER[y_chk, x0, 2]
        assert actual_red == expected_red, (
            f"Reference math fault at row {y_chk}! Expected {expected_red}, got {actual_red}"
        )

print(" -> PASSED: Production output matches pure-Python reference across multiple rows and (L, s) pairs.")

# --- TEST 2: Boundary Safety, Isolation & Strong Negative Control ---
print("\n[TEST 2] Verifying boundary isolation and strong negative control...")
rng = np.random.default_rng(42)
baseline = rng.integers(0, 256, (server.FRAME_H, server.FRAME_W, 3), dtype=np.uint8)
server.MUTABLE_PROCESSING_BUFFER[:] = baseline[:]
server.STATIC_MELT_SNAPSHOT[:] = baseline[:]

boundary_drips = [
    (150, 1, 50, 100, 2.0, 0, 1),
    (200, 637, 50, 150, 1.5, 2, 3),
]
server._render_melt_drips(boundary_drips)

expected_mask = np.zeros((server.FRAME_H, server.FRAME_W), dtype=bool)
expected_mask[50:150, 0:4] = True
expected_mask[50:200, 636:640] = True

# 1. Assert all non-drip pixels are 100% byte-identical
assert np.array_equal(server.MUTABLE_PROCESSING_BUFFER[~expected_mask], baseline[~expected_mask]), (
    "Framing violation: pixels outside drip boundaries were modified!"
)
# 2. Strong negative control: >90% of pixels inside bounding box must mutate
diff_count = np.count_nonzero(server.MUTABLE_PROCESSING_BUFFER[expected_mask] != baseline[expected_mask])
total_in_box = np.count_nonzero(expected_mask) * 3
mutation_ratio = diff_count / float(total_in_box)
assert mutation_ratio > 0.90, f"Negative control weak! Only {mutation_ratio*100:.1f}% of pixels mutated."
print(f" -> PASSED: Boundary isolation confirmed; {mutation_ratio*100:.1f}% of bounding box pixels mutated.")

# --- TEST 3: Overlap Precedence via Production Renderer ---
print("\n[TEST 3] Verifying overlap precedence through production renderer...")
for y in range(server.FRAME_H):
    server.MUTABLE_PROCESSING_BUFFER[y, :] = [0, 0, y % 256]
server.STATIC_MELT_SNAPSHOT[:] = server.MUTABLE_PROCESSING_BUFFER[:]

drip_short  = (100, 320, 50, 50, 1.0, 0, 1)   # Reach = 100
drip_deeper = (200, 320, 50, 150, 3.0, 2, 3)  # Reach = 200

# Inline reference calculations for y=80 (y_rel=30)
# Short: ys = 50 + 30 / (1.0 + 1.0 * sqrt(30/50)) = 66.90 -> Red = 67
# Deeper: ys = 50 + 30 / (1.0 + 3.0 * sqrt(30/150)) = 62.81 -> Red = 63
expected_deeper_red = 63
expected_short_red = 67

# Pass in reverse order. _render_melt_drips must self-sort by reach ascending
server._render_melt_drips([drip_deeper, drip_short])

actual_red = server.MUTABLE_PROCESSING_BUFFER[80, 320, 2]
assert actual_red == expected_deeper_red, (
    f"Precedence fault! Expected deeper red={expected_deeper_red}, got {actual_red} (short was {expected_short_red})"
)
print(" -> PASSED: Deeper drip cleanly overwrote shorter drip at the pixel level.")

# --- TEST 4: Defensive Parameter Guards ---
print("\n[TEST 4] Verifying defensive guards leave buffer unchanged...")
clean_baseline = server.MUTABLE_PROCESSING_BUFFER.copy()
server.STATIC_MELT_SNAPSHOT[:] = clean_baseline[:]

invalid_drips = [
    (100, 0, 50, 50, 1.0, 0, 1),       # x0 < 1
    (100, 638, 50, 50, 1.0, 0, 1),     # x0 > w - 3
    (50, 300, 50, 0, 1.0, 0, 1),       # L = 0
    (40, 300, 50, -10, 1.0, 0, 1),     # L < 0
    (480, 300, 479, 10, 1.0, 0, 1),    # y0 at bottom row
]
server._render_melt_drips(invalid_drips)
assert np.array_equal(server.MUTABLE_PROCESSING_BUFFER, clean_baseline), (
    "Defensive guards failed: invalid parameters modified canvas!"
)
print(" -> PASSED: Invalid drip parameters rejected safely; canvas remained unchanged.")

# --- TEST 5: Symmetrical Screen-Wide Spatial Balance at Max Knob ---
print("\n[TEST 5] Verifying symmetrical screen-wide spatial balance...")
server.STATIC_MELT_GRAY_BUFFER.fill(255)
drips = server._collect_melt_drips(melt_raw=1.0, current_time=1.0)

# Check invariants on all collected drips
for reach, x0, y0, L, s, p1, p2 in drips:
    assert 1 <= x0 <= server.FRAME_W - 3 and 0 <= y0 < server.FRAME_H - 4
    assert L > 0 and reach == y0 + L and 1.0 <= s <= 3.0 and p1 != p2

assert len(drips) == server.MAX_MELT_DRIPS, f"Cap fault! Expected {server.MAX_MELT_DRIPS}, got {len(drips)}"

left = sum(1 for d in drips if d[1] < server.FRAME_W // 2)
right = len(drips) - left
ratio = left / float(max(1, right))
print(f" -> Emitted {len(drips)} drips: Left={left}, Right={right} (Ratio: {ratio:.2f})")
assert 0.80 <= ratio <= 1.25, f"Left/right spatial bias regression! Ratio: {ratio:.2f}"
print(" -> PASSED: Drips are symmetrically balanced across the screen width.")

# --- TEST 6: Frame Determinism & Dormancy Invariants ---
print("\n[TEST 6] Verifying frame determinism and dormancy invariants...")
# 1. Determinism
frame_in = rng.integers(0, 256, (server.FRAME_H, server.FRAME_W, 3), dtype=np.uint8)
server.MUTABLE_PROCESSING_BUFFER[:] = frame_in[:]
server.apply_melt_pass(melt_raw=0.75, current_time=100.0)
pass1_out = server.MUTABLE_PROCESSING_BUFFER.copy()

server.MUTABLE_PROCESSING_BUFFER[:] = frame_in[:]
server.apply_melt_pass(melt_raw=0.75, current_time=100.0)
pass2_out = server.MUTABLE_PROCESSING_BUFFER.copy()
assert np.array_equal(pass1_out, pass2_out), "Determinism fault: identical inputs gave different outputs!"

# 2. Dormancy
server.MUTABLE_PROCESSING_BUFFER[:] = frame_in[:]
server.apply_melt_pass(melt_raw=0.01, current_time=100.0)
assert np.array_equal(server.MUTABLE_PROCESSING_BUFFER, frame_in), "Dormancy fault: melt_raw <= 0.03 modified canvas!"
print(" -> PASSED: Determinism and dormancy verified.")

# --- TEST 7: Memory Tracing Telemetry ---
print("\n[TEST 7] Reporting temporary memory allocation profile...")
tracemalloc.start()
server.apply_melt_pass(melt_raw=1.0, current_time=0.0)
_, peak_bytes = tracemalloc.get_traced_memory()
tracemalloc.stop()
print(f" -> Peak temporary heap allocation during worst-case pass: {peak_bytes / 1024:.1f} KiB")

print("\n==================================================")
print(" ALL 7 INVARIANT SUITE TESTS PASSED CLEANLY")
print("==================================================")
