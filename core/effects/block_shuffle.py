"""Block Shuffle effect (new).

Square tiles of the picture are replaced by tiles from their neighbourhood, like a decoder
that put pieces in the wrong place. Each shuffle is held for a few frames (a stutter, not a
flicker), then a new one is drawn with another tile size. With BLOCK_NOISE_ENABLED a smooth,
moving noise decides WHERE tiles are shuffled (clusters that drift over the picture);
BLOCK_CENTER_CALM keeps the middle of the picture calmer.

It is block based like Pixellation and Packet Corruption: keep it off their knob.
"""

import numpy as np

from core import noise
from core.scale import n
from state import MUTABLE_PROCESSING_BUFFER
from tuning import (
    BLOCK_ACTIVE_ABOVE,
    BLOCK_CENTER_CALM,
    BLOCK_HOLD_MAX_FRAMES,
    BLOCK_HOLD_MIN_FRAMES,
    BLOCK_MAX_FRACTION,
    BLOCK_NOISE_AMOUNT,
    BLOCK_NOISE_ENABLED,
    BLOCK_NOISE_SIZE_TILES,
    BLOCK_NOISE_SPEED,
    BLOCK_RADIUS_TILES,
    BLOCK_TILE_SIZES,
    DISABLED_EFFECTS,
    FRAME_H,
    FRAME_W,
)


class ShuffleEngine:
    """Tile tables and the current shuffle (a few KiB, allocated once)."""

    def __init__(self):
        H, W = FRAME_H, FRAME_W
        self.rng = np.random.default_rng()
        self.sizes = tuple(int(s) for s in BLOCK_TILE_SIZES)
        self.calm = {}  # per tile size: 0 in the middle .. 1 at the edges
        self.cols = {}
        self.rows = {}
        for s in self.sizes:
            gh, gw = H // s, W // s
            ry, rx = np.mgrid[0:gh, 0:gw].astype(np.float64)
            dy = (ry - (gh - 1) / 2.0) / max((gh - 1) / 2.0, 0.5)
            dx = (rx - (gw - 1) / 2.0) / max((gw - 1) / 2.0, 0.5)
            self.calm[s] = np.clip(np.hypot(dx, dy), 0.0, 1.0)
            self.cols[s] = np.arange(gw, dtype=np.float64)
            self.rows[s] = np.arange(gh, dtype=np.float64)
        self.table = noise.table_for("block_shuffle")
        self.size = self.sizes[0]
        self.sel_r = self.sel_c = self.src_r = self.src_c = np.zeros(0, dtype=np.int64)
        self.frames_left = 0

    def _new_shuffle(self, k, t):
        H, W = FRAME_H, FRAME_W
        s = int(self.rng.choice(self.sizes))
        gh, gw = H // s, W // s
        p = np.full((gh, gw), k * BLOCK_MAX_FRACTION)
        p *= 1.0 - BLOCK_CENTER_CALM * (1.0 - self.calm[s])  # the middle is calmer
        if noise.is_on(BLOCK_NOISE_ENABLED):  # clusters: some zones shuffle a lot, others hardly
            zone = noise.field2(
                self.table, self.cols[s], self.rows[s], t, BLOCK_NOISE_SIZE_TILES, BLOCK_NOISE_SPEED
            )
            zone = np.clip(0.5 + 0.8 * zone, 0.0, 1.0)
            p *= (1.0 - BLOCK_NOISE_AMOUNT) + 2.0 * BLOCK_NOISE_AMOUNT * zone
        rs, cs = np.nonzero(self.rng.random((gh, gw)) < p)
        radius = max(0, int(BLOCK_RADIUS_TILES))
        self.sel_r, self.sel_c = rs, cs
        self.src_r = np.clip(rs + self.rng.integers(-radius, radius + 1, rs.size), 0, gh - 1)
        self.src_c = np.clip(cs + self.rng.integers(-radius, radius + 1, cs.size), 0, gw - 1)
        self.size = s
        lo = max(1, int(BLOCK_HOLD_MIN_FRAMES))
        self.frames_left = int(self.rng.integers(lo, max(lo, int(BLOCK_HOLD_MAX_FRAMES)) + 1))

    def step(self, frame, k, t):
        if self.frames_left <= 0:
            self._new_shuffle(k, t)
        self.frames_left -= 1
        if self.sel_r.size == 0:
            return
        s = self.size
        gh, gw = FRAME_H // s, FRAME_W // s
        tiles = frame.reshape(gh, s, gw, s, 3)  # a view of the frame, tile by tile
        pieces = tiles[self.src_r, :, self.src_c]  # copies of the source tiles (read first)
        tiles[self.sel_r, :, self.sel_c] = pieces  # then written to the chosen places


_SHUFFLE = ShuffleEngine() if "block_shuffle" not in DISABLED_EFFECTS else None


def apply_block_shuffle(raw_knob, current_time):
    # Block Shuffle entry point. raw_knob 0-1023. Modifies MUTABLE_PROCESSING_BUFFER in place.
    if not _acts_block_shuffle(raw_knob):
        return
    _SHUFFLE.step(MUTABLE_PROCESSING_BUFFER, n(raw_knob), current_time)


def _acts_block_shuffle(raw):
    return n(raw) > BLOCK_ACTIVE_ABOVE
