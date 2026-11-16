"""Noise: smooth, slowly moving random curves and fields for the effects.

One small shared module. It makes no thread, reads no file and logs nothing. Each owner (an
effect name) gets its own table of random directions, made once; every frame an effect only
READS the table at a new point in time, so the result changes smoothly and never jumps.
Switches and table sharing are tunables in tuning.py (NOISE_*).
"""

import zlib

import numpy as np

from tuning import NOISE_ENABLED, NOISE_SEED, NOISE_SHARE_ALL, NOISE_TABLE_GROUPS

_N = 256  # the table repeats seamlessly after 256 noise cells
_MASK = _N - 1
_TABLES = {}


class NoiseTable:
    """Random directions on a 256 x 256 grid (about 0.5 MiB)."""

    __slots__ = ("gx", "gy")

    def __init__(self, key):
        seed = None if NOISE_SEED is None else [int(NOISE_SEED), zlib.crc32(key.encode())]
        angles = np.random.default_rng(seed).uniform(0.0, 2.0 * np.pi, (_N, _N))
        self.gx = np.cos(angles).astype(np.float32)
        self.gy = np.sin(angles).astype(np.float32)


def table_for(owner):
    # The table of `owner` (an effect name). Own table by default; with NOISE_SHARE_ALL every
    # owner gets the one shared table; NOISE_TABLE_GROUPS puts chosen owners into a named group.
    key = "shared" if NOISE_SHARE_ALL else str(NOISE_TABLE_GROUPS.get(owner, owner))
    table = _TABLES.get(key)
    if table is None:
        table = _TABLES[key] = NoiseTable(key)
    return table


def is_on(effect_switch):
    # True when the master switch and the effect's own switch are both on.
    return bool(NOISE_ENABLED and effect_switch)


def _fade(t):
    return t * t * t * (t * (t * 6.0 - 15.0) + 10.0)


def noise2(table, x, y):
    # Smooth 2-D noise, about -1 to 1. x and y: numbers or arrays that broadcast together.
    x = np.asarray(x, dtype=np.float64)
    y = np.asarray(y, dtype=np.float64)
    fx, fy = np.floor(x), np.floor(y)
    xf, yf = x - fx, y - fy
    xi, yi = fx.astype(np.int64), fy.astype(np.int64)
    x0, x1 = xi & _MASK, (xi + 1) & _MASK
    y0, y1 = yi & _MASK, (yi + 1) & _MASK
    gx, gy = table.gx, table.gy
    n00 = gx[y0, x0] * xf + gy[y0, x0] * yf
    n10 = gx[y0, x1] * (xf - 1.0) + gy[y0, x1] * yf
    n01 = gx[y1, x0] * xf + gy[y1, x0] * (yf - 1.0)
    n11 = gx[y1, x1] * (xf - 1.0) + gy[y1, x1] * (yf - 1.0)
    u, v = _fade(xf), _fade(yf)
    top = n00 + u * (n10 - n00)
    bottom = n01 + u * (n11 - n01)
    return np.clip((top + v * (bottom - top)) * 1.6, -1.0, 1.0).astype(np.float32)


def lane(table, positions, t, size, speed, lane_id=0):
    # A curve along `positions` (rows, lines, ...) that changes with time t: about -1 to 1.
    # size = positions per noise cell (bigger = smoother); speed = noise cells per second.
    # lane_id picks another, unrelated curve from the same table.
    size = max(float(size), 1e-6)
    return noise2(
        table,
        np.asarray(positions, dtype=np.float64) / size + 50.0 * lane_id,
        t * speed + 30.0 * lane_id,
    )


def field2(table, xs, ys, t, size, speed, lane_id=0):
    # A 2-D field (len(ys) x len(xs)), about -1 to 1, that morphs with time t. Two layers
    # scroll in different directions, so it changes shape instead of only sliding along.
    size = max(float(size), 1e-6)
    gx = (np.asarray(xs, dtype=np.float64) / size + 50.0 * lane_id)[None, :]
    gy = (np.asarray(ys, dtype=np.float64) / size + 30.0 * lane_id)[:, None]
    a = noise2(table, gx + t * speed, gy + t * speed * 0.6)
    b = noise2(table, gx * 2.3 - t * speed * 0.8 + 17.0, gy * 2.3 + t * speed * 0.5 + 41.0)
    return 0.7 * a + 0.3 * b
