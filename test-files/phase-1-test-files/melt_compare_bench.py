#!/usr/bin/env python3
"""
melt_compare_bench.py  -  READ-ONLY comparison of Melt implementations.

Nothing in your project is modified. The script imports your current server.py,
defines "candidate B" (the other AI's vectorized collector + renderer, logic
unchanged) in memory only, and compares:

    A-adaptive : your current apply_melt_pass (half resolution at strength >= 0.72)
    A-full     : your current renderer forced to full resolution
    B-vector   : candidate B, always full resolution (numpy gather/scatter)

For each variant, scene and strength it reports
    * Melt time (p50 / p95)
    * how often a display-like thread's 0.9 MB frame copy takes longer than 2 ms
      while Melt runs (this mimics the "[PERF WARNING] Frame copy latency spike"
      message) -> the number that matters for the latency problem
    * how far B's pixels are from A-full's (equivalence check)

Run on the Pi, from the project folder, with the kiosk STOPPED:
    sudo systemctl stop glitchbooth.service
    cd ~/glitchbooth && source .venv/bin/activate
    python3 melt_compare_bench.py                  # synthetic scenes, ~3 minutes
    python3 melt_compare_bench.py --frame my_still.png --save-images ~/melt_cmp
    sudo systemctl start glitchbooth.service       # afterwards

Reading the result:
    * Prefer the variant with fewer ">2ms" events and a Melt time you can live with.
    * A laptop result is only a rough hint; the Pi result is the one that counts.
"""
import argparse
import os
import subprocess
import sys
import threading
import time

import cv2
import numpy as np
from numpy.lib.stride_tricks import as_strided

os.environ.setdefault("IK_URL_ENDPOINT", "https://ik.imagekit.io/mock")
os.environ.setdefault("IK_PRIVATE_KEY", "mock_key")
sys.path.insert(0, os.getcwd())

import server as S  # noqa: E402

# --------------------------------------------------------------------------
# Candidate B: the other AI's vectorized Melt (logic unchanged, names suffixed)
# --------------------------------------------------------------------------
CANDIDATE_B = r'''
MELT_CHUNK_ROWS = 4096

def _collect_melt_drips_B(melt_raw, current_time, h=FRAME_H, w=FRAME_W):
    max_melt_length = int(20 + melt_raw * (h * 0.85))
    threshold = max(75, int(255 * (1.0 - melt_raw * 0.65)))
    stride = (MELT_STRIDE_LOW if melt_raw < 0.35 else (MELT_STRIDE_MID if melt_raw < 0.7 else MELT_STRIDE_HIGH))
    seed_step = max(2, int((1.1 - melt_raw) * 8))
    max_seeds_per_column = 3 if melt_raw < 0.65 else 6
    time_tick = int(current_time * 4) & 0xFF
    mask = STATIC_MELT_GRAY_BUFFER[:, 1 : w - 2 : stride] > threshold
    col_idx, ys = np.nonzero(mask.T)
    if ys.size == 0:
        return None
    counts = np.bincount(col_idx, minlength=mask.shape[1])
    first = np.cumsum(counts) - counts
    rank = np.arange(ys.size) - first[col_idx]
    keep = ((rank % seed_step == 0) & (rank // seed_step < max_seeds_per_column) & (ys < h - 4))
    col_idx, ys, rank = col_idx[keep], ys[keep], rank[keep]
    if ys.size == 0:
        return None
    seed_rank = rank // seed_step
    xs = 1 + col_idx * stride
    order = np.lexsort((xs, seed_rank))
    xs, ys, seed_rank = xs[order], ys[order], seed_rank[order]
    bounds = np.searchsorted(seed_rank, np.arange(max_seeds_per_column + 1))
    picked = []
    total = 0
    for r in range(max_seeds_per_column):
        remaining = MAX_MELT_DRIPS - total
        if remaining <= 0:
            break
        idx = np.arange(bounds[r], bounds[r + 1])
        if idx.size > remaining:
            step = idx.size / float(remaining)
            idx = idx[(np.arange(remaining) * step).astype(np.int64)]
        if idx.size:
            picked.append(idx)
            total += idx.size
    if not picked:
        return None
    sel = np.concatenate(picked)
    x = xs[sel].astype(np.int64)
    y = ys[sel].astype(np.int64)
    h1 = (x * 73856093) ^ (y * 19349663) ^ (time_tick * 83492791)
    h2 = (x * 2654435761) ^ (y * 2246822519) ^ (time_tick * 3266489917)
    rand_len_mult = (h1 % 1000) / 1000.0
    rand_stretch_mult = ((h1 >> 10) % 1000) / 1000.0
    n_pal = len(NEON_PALETTES)
    p1 = (h2 >> 4) % n_pal
    p2 = (p1 + 1 + ((h2 >> 12) % (n_pal - 1))) % n_pal
    base_len = int(max_melt_length * 0.45)
    rand_len = (max_melt_length * 0.55 * rand_len_mult).astype(np.int64)
    L = base_len + rand_len
    s = 1.0 + rand_stretch_mult * 2.0
    reach = y + L
    live = L > 0
    out = np.column_stack((reach, x, y, L, s, p1, p2)).astype(np.float64)
    return out[live] if not live.all() else out

def _melt_window_view_B(buf, width, writeable):
    h, w = buf.shape[:2]
    return as_strided(buf, shape=(h, w - width + 1, 3 * width), strides=(buf.strides[0], 3, 1), writeable=writeable)

def _render_melt_drips_B(drips, h=FRAME_H, w=FRAME_W):
    if drips is None or len(drips) == 0:
        return
    drips = drips[np.lexsort((drips[:, 1], drips[:, 0]))]
    left = MELT_DRIP_WIDTH // 2
    right = MELT_DRIP_WIDTH - left
    x0 = drips[:, 1].astype(np.int32)
    y0 = drips[:, 2].astype(np.int32)
    L = drips[:, 3].astype(np.int32)
    s = drips[:, 4].astype(np.float32)
    p1 = drips[:, 5].astype(np.int32)
    p2 = drips[:, 6].astype(np.int32)
    nr = np.minimum(y0 + L, h) - y0
    ok = (x0 - left >= 0) & (x0 + right <= w) & (L > 0) & (y0 < h - 1) & (nr > 0)
    if not ok.any():
        return
    if not ok.all():
        x0, y0, L, s, p1, p2, nr = (a[ok] for a in (x0, y0, L, s, p1, p2, nr))
    n = x0.size
    total = int(nr.sum())
    starts = np.cumsum(nr) - nr
    did = np.repeat(np.arange(n, dtype=np.int32), nr)
    y_rel_i = np.arange(total, dtype=np.int32) - starts[did]
    y_rel = y_rel_i.astype(np.float32)
    norm = np.divide(y_rel, L.astype(np.float32)[did])
    np.clip(norm, 0.0, 1.0, out=norm)
    denom = np.sqrt(norm)
    denom *= s[did]
    denom += 1.0
    np.maximum(denom, 1.0, out=denom)
    y_float = np.divide(y_rel, denom)
    y_float += y0.astype(np.float32)[did]
    np.clip(y_float, 0.0, float(h - 1), out=y_float)
    y_fl = np.floor(y_float)
    w_ceil = y_float - y_fl
    y_floor = y_fl.astype(np.int32)
    y_ceil = np.minimum(y_floor + 1, h - 1)
    y_dest = y0[did] + y_rel_i
    x_start = (x0 - left)[did]
    nw = np.subtract(norm, np.float32(MELT_NEON_START_PCT))
    nw /= np.float32(MELT_NEON_RAMP_SPAN)
    np.clip(nw, 0.0, 1.0, out=nw)
    one_minus_nw = 1.0 - nw
    wt_floor = one_minus_nw * (1.0 - w_ceil)
    wt_ceil = one_minus_nw * w_ceil
    neon_color = 0.5 * NEON_PALETTES_FLOAT[p1] + 0.5 * NEON_PALETTES_FLOAT[p2]
    neon_term = nw[:, None] * neon_color[did]
    src_win = _melt_window_view_B(STATIC_MELT_SNAPSHOT, MELT_DRIP_WIDTH, False)
    dst_win = _melt_window_view_B(MUTABLE_PROCESSING_BUFFER, MELT_DRIP_WIDTH, True)
    for i in range(0, total, MELT_CHUNK_ROWS):
        j = min(total, i + MELT_CHUNK_ROWS)
        m = j - i
        xs_c = x_start[i:j]
        acc = src_win[y_floor[i:j], xs_c].astype(np.float32)
        acc *= wt_floor[i:j, None]
        tmp = src_win[y_ceil[i:j], xs_c].astype(np.float32)
        tmp *= wt_ceil[i:j, None]
        acc += tmp
        acc3 = acc.reshape(m, MELT_DRIP_WIDTH, 3)
        acc3 += neon_term[i:j, None, :]
        np.rint(acc, out=acc)
        np.clip(acc, 0.0, 255.0, out=acc)
        dst_win[y_dest[i:j], xs_c] = acc.astype(np.uint8)

def apply_melt_pass_B(melt_raw, current_time):
    if melt_raw <= 0.03:
        return
    STATIC_MELT_SNAPSHOT[:] = MUTABLE_PROCESSING_BUFFER[:]
    cv2.cvtColor(STATIC_MELT_SNAPSHOT, cv2.COLOR_BGR2GRAY, dst=STATIC_MELT_GRAY_BUFFER)
    drips = _collect_melt_drips_B(melt_raw, current_time)
    if drips is not None:
        _render_melt_drips_B(drips)
'''
_ns = vars(S)
_ns["as_strided"] = as_strided
exec(CANDIDATE_B, _ns)


# --------------------------------------------------------------------------
# Variants (all operate on S.MUTABLE_PROCESSING_BUFFER, like the pipeline does)
# --------------------------------------------------------------------------
def variant_A_adaptive(k, t):
    S.apply_melt_pass(k, t)


def variant_A_full(k, t):
    S.MELT_HALF_RESOLUTION_ACTIVE = False
    np.copyto(S.STATIC_MELT_SNAPSHOT, S.MUTABLE_PROCESSING_BUFFER)
    cv2.cvtColor(S.STATIC_MELT_SNAPSHOT, cv2.COLOR_BGR2GRAY, dst=S.STATIC_MELT_GRAY_BUFFER)
    d = S._collect_melt_drips(k, t, h=S.FRAME_H, w=S.FRAME_W,
                              gray_buffer=S.STATIC_MELT_GRAY_BUFFER, stride_scale=1.0)
    if d:
        S._render_melt_drips_vectorized(d, S.STATIC_MELT_SNAPSHOT,
                                        S.MUTABLE_PROCESSING_BUFFER, S.MELT_FULL_WORKSPACE)


def variant_B(k, t):
    S.apply_melt_pass_B(k, t)


VARIANTS = [("A-adaptive", variant_A_adaptive), ("A-full", variant_A_full), ("B-vector", variant_B)]


# --------------------------------------------------------------------------
# Scenes
# --------------------------------------------------------------------------
def scenes(user_frame):
    rng = np.random.default_rng(5)
    out = {}
    if user_frame is not None:
        out["yourframe"] = user_frame
    p = np.zeros((480, 640, 3), np.uint8)
    p[100:380, 200:440] = 180
    p[140:300, 260:380] = 230
    out["portrait"] = p
    sm = rng.integers(0, 256, (60, 80, 3), dtype=np.uint8)
    out["textured"] = np.ascontiguousarray(
        cv2.GaussianBlur(cv2.resize(sm, (640, 480), interpolation=cv2.INTER_CUBIC), (0, 0), 6))
    out["noise"] = rng.integers(140, 256, (480, 640, 3), dtype=np.uint8)
    return out


# --------------------------------------------------------------------------
# One measurement: pipeline thread (15 FPS) + display-like thread (60 Hz copy probe)
# --------------------------------------------------------------------------
def measure(fn, canvas, knob, seconds, fps=15):
    ui = np.zeros((480, 640, 3), np.uint8)
    local = np.empty_like(ui)
    lock = threading.Lock()
    stop = threading.Event()
    melt_ms, copies = [], []

    def pipeline():
        i = 0
        while not stop.is_set():
            t0 = time.perf_counter()
            S.MUTABLE_PROCESSING_BUFFER[:] = canvas
            a = time.perf_counter()
            fn(knob, 1000.0 + i * 0.25)
            melt_ms.append((time.perf_counter() - a) * 1e3)
            ui[:] = S.MUTABLE_PROCESSING_BUFFER
            i += 1
            time.sleep(max(0.0, 1.0 / fps - (time.perf_counter() - t0)))

    def display():
        nxt = time.perf_counter()
        while not stop.is_set():
            if lock.acquire(blocking=False):
                try:
                    s = time.perf_counter()
                    local[:] = ui
                    copies.append((time.perf_counter() - s) * 1e3)
                finally:
                    lock.release()
            nxt += 1 / 60.0
            d = nxt - time.perf_counter()
            if d > 0:
                time.sleep(d)
            else:
                nxt = time.perf_counter()

    threads = [threading.Thread(target=pipeline, daemon=True), threading.Thread(target=display, daemon=True)]
    for th in threads:
        th.start()
    time.sleep(seconds)
    stop.set()
    for th in threads:
        th.join(timeout=3)
    m = np.array(melt_ms[2:] if len(melt_ms) > 4 else melt_ms)
    c = np.array(copies)
    over = float(np.sum(c > 2.0)) * (10.0 / seconds)
    return (np.percentile(m, 50), np.percentile(m, 95), over, float(c.max()) if c.size else 0.0)


def device_model():
    try:
        with open("/proc/device-tree/model", "rb") as fh:
            return fh.read().decode(errors="ignore").strip("\x00\n ")
    except OSError:
        return None


def run_cmd(cmd):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=3).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=10.0, help="seconds per measurement")
    ap.add_argument("--knobs", type=float, nargs="+", default=[0.5, 0.8, 1.0])
    ap.add_argument("--frame", help="your own still (png/jpg); NOT a captures/*_color.jpg (those are post-effects)")
    ap.add_argument("--save-images", help="folder to save one PNG per variant at the highest strength")
    args = ap.parse_args()

    model = device_model()
    print("=" * 78)
    print(" MELT COMPARISON (read-only)")
    print("=" * 78)
    print(f" Machine : {model or 'NOT a Raspberry Pi (' + sys.platform + '), indicative only'}")
    print(f" Libs    : numpy {np.__version__}, opencv {cv2.__version__}, python {sys.version.split()[0]}")
    print(f" Settings: MAX_MELT_DRIPS={S.MAX_MELT_DRIPS}, drip width={S.MELT_DRIP_WIDTH}, "
          f"strides={S.MELT_STRIDE_LOW}/{S.MELT_STRIDE_MID}/{S.MELT_STRIDE_HIGH}, "
          f"half-res from {S.MELT_HALF_ENTER}")
    if model and "Raspberry Pi" in model:
        print(f" Health  : {run_cmd(['vcgencmd', 'measure_temp'])} | {run_cmd(['vcgencmd', 'get_throttled'])}")
    running = [ln for ln in (run_cmd(["pgrep", "-af", "pygame_display.py"]) or "").splitlines() if "pgrep" not in ln]
    if running:
        print(" WARNING : the kiosk seems to be running; results will be inflated:")
        for ln in running:
            print("           " + ln)
    print(f" Each line below runs {args.seconds:.0f} s (pipeline at 15 FPS + a 60 Hz display-like copy probe).")
    print("=" * 78)

    user_frame = None
    if args.frame:
        user_frame = cv2.imread(args.frame)
        if user_frame is None:
            sys.exit(f"cannot read {args.frame}")
        user_frame = np.ascontiguousarray(cv2.resize(user_frame, (640, 480)))

    sc = scenes(user_frame)
    print(f"\n{'scene':10s} {'knob':>4s}  {'variant':11s} {'melt p50':>9s} {'p95':>7s}  {'copies >2ms /10s':>17s} {'max copy':>9s}")
    for name, canvas in sc.items():
        for k in args.knobs:
            for vname, fn in VARIANTS:
                p50, p95, over, mx = measure(fn, canvas, k, args.seconds)
                print(f"{name:10s} {k:4.2f}  {vname:11s} {p50:7.1f}ms {p95:5.1f}ms  {over:17.1f} {mx:7.2f}ms")
            print()

    print("Equivalence of B-vector against A-full (same frame, same time):")
    for name, canvas in sc.items():
        for k in (0.5, 1.0):
            S.MUTABLE_PROCESSING_BUFFER[:] = canvas
            variant_A_full(k, 1000.0)
            a = S.MUTABLE_PROCESSING_BUFFER.copy()
            S.MUTABLE_PROCESSING_BUFFER[:] = canvas
            variant_B(k, 1000.0)
            b = S.MUTABLE_PROCESSING_BUFFER.copy()
            d = np.abs(a.astype(int) - b.astype(int))
            print(f"  {name:10s} knob {k:.1f}: max difference {d.max()} levels, "
                  f"{100 * np.mean(d > 1):.4f}% of bytes differ by more than 1")

    if args.save_images:
        os.makedirs(args.save_images, exist_ok=True)
        k = max(args.knobs)
        for name, canvas in sc.items():
            for vname, fn in VARIANTS:
                S.MUTABLE_PROCESSING_BUFFER[:] = canvas
                fn(k, 1000.0)
                path = os.path.join(args.save_images, f"{name}_{vname}_k{k:.2f}.png")
                cv2.imwrite(path, S.MUTABLE_PROCESSING_BUFFER)
        print(f"\nImages saved in {args.save_images} (compare A-adaptive and B-vector side by side).")
    print("\nDone. Restart the kiosk:  sudo systemctl start glitchbooth.service")


if __name__ == "__main__":
    main()
