"""Data Melt effect.

Moved from server.py unchanged (function and class bodies identical, names unchanged).
No thread, file access or log line when this module is imported; it only creates its
buffers and tables.
"""

import cv2
import numpy as np
from numpy.lib.stride_tricks import as_strided

from core.scale import n
from state import MUTABLE_PROCESSING_BUFFER, STATIC_MELT_GRAY_BUFFER
from tuning import (
    FRAME_H,
    FRAME_W,
    MAX_MELT_DRIPS,
    MELT_ACTIVE_ABOVE,
    MELT_BASE_LENGTH_PX,
    MELT_BOTTOM_MARGIN_PX,
    MELT_CHUNK_ROWS,
    MELT_DRIP_WIDTH,
    MELT_LENGTH_FIXED_SHARE,
    MELT_LENGTH_FRACTION,
    MELT_NEON_MIX,
    MELT_NEON_RAMP_SPAN,
    MELT_NEON_START_PCT,
    MELT_REROLL_HZ,
    MELT_SEED_STEP_MAX,
    MELT_SEED_STEP_MIN,
    MELT_SEED_STEP_ZERO_AT,
    MELT_SEEDS_HIGH,
    MELT_SEEDS_HIGH_FROM,
    MELT_SEEDS_LOW,
    MELT_STRETCH_MAX,
    MELT_STRIDE_HIGH,
    MELT_STRIDE_HIGH_FROM,
    MELT_STRIDE_LOW,
    MELT_STRIDE_MID,
    MELT_STRIDE_MID_FROM,
    MELT_THRESHOLD_FALL,
    MELT_THRESHOLD_FLOOR,
    NEON_PALETTES,
    NEON_PALETTES_FLOAT,
)


# --- MELT BUFFERS ---

STATIC_MELT_SNAPSHOT = np.empty((FRAME_H, FRAME_W, 3), dtype=np.uint8, order="C")


# =========================================================================
# MELT ENGINE - VECTORIZED
# =========================================================================
# apply_melt_pass() is the entry point. Drip seeds are found without a Python loop
# over columns, and the drips are drawn with a few dozen large NumPy calls per frame
# (many small NumPy calls hold the GIL and would starve the pygame thread; large ones
# release it). Overlapping drips composite in (reach, x0) draw order.


def _collect_melt_drips(
    melt_raw: float, current_time: float, h: int = FRAME_H, w: int = FRAME_W
):
    # Seed discovery without a Python loop over columns.

    # Returns a float64 array of shape (n, 7):
    #     [reach, x0, start_y, L, s, p1, p2]
    # or None when there is nothing to draw.

    max_melt_length = int(MELT_BASE_LENGTH_PX + melt_raw * (h * MELT_LENGTH_FRACTION))
    threshold = max(
        MELT_THRESHOLD_FLOOR, int(255 * (1.0 - melt_raw * MELT_THRESHOLD_FALL))
    )
    stride = (
        MELT_STRIDE_LOW
        if melt_raw < MELT_STRIDE_MID_FROM
        else (MELT_STRIDE_MID if melt_raw < MELT_STRIDE_HIGH_FROM else MELT_STRIDE_HIGH)
    )
    seed_step = max(
        MELT_SEED_STEP_MIN,
        int((MELT_SEED_STEP_ZERO_AT - melt_raw) * MELT_SEED_STEP_MAX),
    )
    max_seeds_per_column = (
        MELT_SEEDS_LOW if melt_raw < MELT_SEEDS_HIGH_FROM else MELT_SEEDS_HIGH
    )
    time_tick = int(current_time * MELT_REROLL_HZ) & 0xFF

    # One threshold over every sampled column at once.
    mask = STATIC_MELT_GRAY_BUFFER[:, 1 : w - 2 : stride] > threshold  # (h, ncols)
    col_idx, ys = np.nonzero(mask.T)  # ordered by column, then by y
    if ys.size == 0:
        return None

    # Position of each hit inside its own column -> every seed_step-th hit,
    # at most max_seeds_per_column per column, nothing in the bottom MELT_BOTTOM_MARGIN_PX rows.
    counts = np.bincount(col_idx, minlength=mask.shape[1])
    first = np.cumsum(counts) - counts
    rank = np.arange(ys.size) - first[col_idx]
    keep = (
        (rank % seed_step == 0)
        & (rank // seed_step < max_seeds_per_column)
        & (ys < h - MELT_BOTTOM_MARGIN_PX)
    )
    col_idx, ys, rank = col_idx[keep], ys[keep], rank[keep]
    if ys.size == 0:
        return None
    seed_rank = rank // seed_step
    xs = 1 + col_idx * stride

    # Order: all rank-0 seeds (by column), then rank-1, ...
    order = np.lexsort((xs, seed_rank))
    xs, ys, seed_rank = xs[order], ys[order], seed_rank[order]
    bounds = np.searchsorted(seed_rank, np.arange(max_seeds_per_column + 1))

    # Apply the drip cap rank by rank, thinning evenly when a rank overflows.
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

    # Per-drip pseudo-random parameters (integer hashes of position and time).
    x = xs[sel].astype(np.int64)
    y = ys[sel].astype(np.int64)
    h1 = (x * 73856093) ^ (y * 19349663) ^ (time_tick * 83492791)
    h2 = (x * 2654435761) ^ (y * 2246822519) ^ (time_tick * 3266489917)
    rand_len_mult = (h1 % 1000) / 1000.0
    rand_stretch_mult = ((h1 >> 10) % 1000) / 1000.0
    n_pal = len(NEON_PALETTES)
    p1 = (h2 >> 4) % n_pal
    p2 = (p1 + 1 + ((h2 >> 12) % (n_pal - 1))) % n_pal
    base_len = int(max_melt_length * MELT_LENGTH_FIXED_SHARE)
    rand_len = (
        max_melt_length * (1.0 - MELT_LENGTH_FIXED_SHARE) * rand_len_mult
    ).astype(np.int64)
    L = base_len + rand_len
    s = 1.0 + rand_stretch_mult * (MELT_STRETCH_MAX - 1.0)
    reach = y + L

    live = L > 0
    out = np.column_stack((reach, x, y, L, s, p1, p2)).astype(np.float64)
    return out[live] if not live.all() else out


def _melt_window_view(buf, width: int, writeable: bool):
    # View of a C-contiguous (H, W, 3) uint8 frame where view[y, x] is the
    # 3*width bytes of `width` consecutive pixels starting at column x.
    # Lets one fancy-index gather/scatter move a whole drip row (8 px).
    h, w = buf.shape[:2]
    return as_strided(
        buf,
        shape=(h, w - width + 1, 3 * width),
        strides=(buf.strides[0], 3, 1),
        writeable=writeable,
    )


def _render_melt_drips(drips, h: int = FRAME_H, w: int = FRAME_W):
    if drips is None or len(drips) == 0:
        return

    # Draw order: ascending (reach, x0). Stable, so ties keep discovery order.
    drips = drips[np.lexsort((drips[:, 1], drips[:, 0]))]

    left = MELT_DRIP_WIDTH // 2
    right = MELT_DRIP_WIDTH - left

    x0 = drips[:, 1].astype(np.int32)
    y0 = drips[:, 2].astype(np.int32)
    L = drips[:, 3].astype(np.int32)
    s = drips[:, 4].astype(np.float32)
    p1 = drips[:, 5].astype(np.int32)
    p2 = drips[:, 6].astype(np.int32)

    nr = np.minimum(y0 + L, h) - y0  # rows each drip covers
    ok = (x0 - left >= 0) & (x0 + right <= w) & (L > 0) & (y0 < h - 1) & (nr > 0)
    if not ok.any():
        return
    if not ok.all():
        x0, y0, L, s, p1, p2, nr = (a[ok] for a in (x0, y0, L, s, p1, p2, nr))

    n = x0.size
    total = int(nr.sum())

    # ---- geometry: one flat row list covering every drip, in draw order ----
    starts = np.cumsum(nr) - nr
    did = np.repeat(np.arange(n, dtype=np.int32), nr)  # drip id per row
    y_rel_i = np.arange(total, dtype=np.int32) - starts[did]  # row within drip
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

    # ---- per-row blend weights --------------------------------------------
    # out = (1-nw)*((1-wc)*floor_px + wc*ceil_px) + nw*neon
    #     =  a*floor_px + b*ceil_px + nw*neon
    nw = np.subtract(norm, np.float32(MELT_NEON_START_PCT))
    nw /= np.float32(MELT_NEON_RAMP_SPAN)
    np.clip(nw, 0.0, 1.0, out=nw)

    one_minus_nw = 1.0 - nw
    wt_floor = one_minus_nw * (1.0 - w_ceil)
    wt_ceil = one_minus_nw * w_ceil

    neon_color = (1.0 - MELT_NEON_MIX) * NEON_PALETTES_FLOAT[
        p1
    ] + MELT_NEON_MIX * NEON_PALETTES_FLOAT[p2]  # (n,3)
    neon_term = nw[:, None] * neon_color[did]  # (total, 3)

    # ---- pixels: gather, blend, scatter, in cache-sized chunks ------------
    src_win = _melt_window_view(STATIC_MELT_SNAPSHOT, MELT_DRIP_WIDTH, False)
    dst_win = _melt_window_view(MUTABLE_PROCESSING_BUFFER, MELT_DRIP_WIDTH, True)

    for i in range(0, total, MELT_CHUNK_ROWS):
        j = min(total, i + MELT_CHUNK_ROWS)
        m = j - i
        xs_c = x_start[i:j]

        acc = src_win[y_floor[i:j], xs_c].astype(np.float32)  # (m, 24)
        acc *= wt_floor[i:j, None]
        tmp = src_win[y_ceil[i:j], xs_c].astype(np.float32)
        tmp *= wt_ceil[i:j, None]
        acc += tmp

        acc3 = acc.reshape(m, MELT_DRIP_WIDTH, 3)
        acc3 += neon_term[i:j, None, :]

        np.rint(acc, out=acc)
        np.clip(acc, 0.0, 255.0, out=acc)

        # Rows are written in draw order, so later drips overwrite earlier
        # ones exactly as the per-drip loop did.
        dst_win[y_dest[i:j], xs_c] = acc.astype(np.uint8)


def apply_melt_pass(melt_raw: float, current_time: float):
    # Melt entry point (apply_melt has already checked that Melt is acting).
    STATIC_MELT_SNAPSHOT[:] = MUTABLE_PROCESSING_BUFFER[:]
    cv2.cvtColor(STATIC_MELT_SNAPSHOT, cv2.COLOR_BGR2GRAY, dst=STATIC_MELT_GRAY_BUFFER)
    drips = _collect_melt_drips(melt_raw, current_time)
    if drips is not None:
        _render_melt_drips(drips)


def apply_melt(raw_knob, current_time):
    if _acts_melt(raw_knob):
        apply_melt_pass(n(raw_knob), current_time)


def _acts_melt(raw):
    return n(raw) > MELT_ACTIVE_ABOVE
