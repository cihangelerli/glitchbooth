"""Chroma Bleed effect (new).

Like a worn tape or a bad cable: the COLOUR of the picture smears sideways and trails behind
the sharp black-and-white detail. Different from Chroma Shift, which moves a whole colour
channel. Three optional looks, each with its own switch:

  A  BLEED_LONG_ENABLED   a much longer smear and lag (colour spills far past the edges)
  B  BLEED_TRAIL_ENABLED  the colour follows the picture from older frames (trails behind movement)
  C  BLEED_SLABS_ENABLED  the colour is held sideways in blocks of random length (tape colour slabs)

BLEED_SATURATION (1 = unchanged) gives all of them more colour. With BLEED_NOISE_ENABLED a smooth,
moving noise makes the trailing length (and the slab length) vary from row to row.

Place it before Chroma Shift.
"""

import cv2
import numpy as np

from core import noise
from core.scale import n
from state import MUTABLE_PROCESSING_BUFFER
from tuning import (
    BLEED_ACTIVE_ABOVE,
    BLEED_LAG_PX,
    BLEED_LONG_ENABLED,
    BLEED_LONG_LAG_PX,
    BLEED_LONG_SMEAR_PX,
    BLEED_MAX_PX,
    BLEED_NOISE_AMOUNT,
    BLEED_NOISE_ENABLED,
    BLEED_NOISE_ROWS,
    BLEED_NOISE_SPEED,
    BLEED_SATURATION,
    BLEED_SLAB_LENGTH_PX,
    BLEED_SLAB_REFRESH_FRAMES,
    BLEED_SLAB_ROWS_PX,
    BLEED_SLABS_ENABLED,
    BLEED_TRAIL_DECAY,
    BLEED_TRAIL_ENABLED,
    DISABLED_EFFECTS,
    FRAME_H,
    FRAME_W,
)


class BleedEngine:
    """Buffers (about 5 MiB, allocated once) and the per-frame work of Chroma Bleed."""

    def __init__(self):
        H, W = FRAME_H, FRAME_W
        hh, hw = H // 2, W // 2
        self.ycc = np.zeros((H, W, 3), np.uint8)
        self.ycc_h = np.zeros((hh, hw, 3), np.uint8)
        self.cc_a = np.zeros((hh, hw, 2), np.uint8)
        self.cc_b = np.zeros((hh, hw, 2), np.uint8)
        self.cc_full = np.zeros((H, W, 2), np.uint8)
        self.xs_h = np.tile(np.arange(hw, dtype=np.float32), (hh, 1))
        self.mapx_h = self.xs_h.copy()
        self.mapy_h = np.tile(np.arange(hh, dtype=np.float32)[:, None], (1, hw))
        self.rows_px = np.arange(hh, dtype=np.float64) * 2.0  # full-resolution row of each half row
        self.table = noise.table_for("chroma_bleed")
        self.rng = np.random.default_rng()
        # B: colour memory (float, half resolution)
        self.hist = np.zeros((hh, hw, 2), np.float32)
        self.hist_ready = False
        # C: where each slab starts; the same slabs are kept for a few frames
        self.slab_rows = max(1, int(BLEED_SLAB_ROWS_PX) // 2)
        self.n_bands = -(-hh // self.slab_rows)
        self.cols = np.arange(hw)
        self.band_px = np.arange(self.n_bands, dtype=np.float64) * self.slab_rows * 2.0
        self.slab_idx = None
        self.slab_age = 0

    def reset(self):
        # Forget the colour memory and the slabs (the knob went down: nothing is left over).
        self.hist_ready = False
        self.slab_idx = None

    def _slabs(self, t):
        hh, hw = FRAME_H // 2, FRAME_W // 2
        if self.slab_idx is None or self.slab_age >= max(1, int(BLEED_SLAB_REFRESH_FRAMES)):
            length = np.full(self.n_bands, max(float(BLEED_SLAB_LENGTH_PX), 1.0) / 2.0)  # half resolution
            if noise.is_on(BLEED_NOISE_ENABLED):  # some rows get longer slabs, some shorter
                lane = noise.lane(self.table, self.band_px, t, BLEED_NOISE_ROWS, BLEED_NOISE_SPEED, 1)
                length *= np.clip(1.0 + BLEED_NOISE_AMOUNT * lane, 0.25, 4.0)
            starts = self.rng.random((self.n_bands, hw)) < (1.0 / length)[:, None]
            starts[:, 0] = True
            idx = np.maximum.accumulate(np.where(starts, self.cols[None, :], 0), axis=1)
            self.slab_idx = np.repeat(idx, self.slab_rows, axis=0)[:hh, :, None]
            self.slab_age = 0
        self.slab_age += 1
        np.copyto(self.cc_a, np.take_along_axis(self.cc_a, self.slab_idx, axis=1))

    def _trail(self, k):
        decay = min(max(float(BLEED_TRAIL_DECAY), 0.0), 0.99) * k  # more knob = longer memory
        if not self.hist_ready:
            self.hist[:] = self.cc_a
            self.hist_ready = True
        else:
            cv2.accumulateWeighted(self.cc_a, self.hist, 1.0 - decay)
        cv2.convertScaleAbs(self.hist, dst=self.cc_a)

    def step(self, frame, k, t):
        H, W = FRAME_H, FRAME_W
        hh, hw = H // 2, W // 2
        smear_px = BLEED_LONG_SMEAR_PX if BLEED_LONG_ENABLED else BLEED_MAX_PX
        lag_px = BLEED_LONG_LAG_PX if BLEED_LONG_ENABLED else BLEED_LAG_PX
        cv2.cvtColor(frame, cv2.COLOR_BGR2YCrCb, dst=self.ycc)
        cv2.resize(self.ycc, (hw, hh), dst=self.ycc_h, interpolation=cv2.INTER_AREA)
        cv2.mixChannels([self.ycc_h], [self.cc_a], [1, 0, 2, 1])  # Cr, Cb only
        kk = int(k * smear_px / 2.0) * 2 + 1  # horizontal smear length at half resolution
        if kk > 1:
            cv2.blur(self.cc_a, (kk, 1), dst=self.cc_b)
        else:
            np.copyto(self.cc_b, self.cc_a)
        lag = np.full(hh, k * lag_px / 2.0)
        if noise.is_on(BLEED_NOISE_ENABLED):
            lag *= 1.0 + BLEED_NOISE_AMOUNT * noise.lane(
                self.table, self.rows_px, t, BLEED_NOISE_ROWS, BLEED_NOISE_SPEED
            )
        np.subtract(self.xs_h, lag[:, None].astype(np.float32), out=self.mapx_h)
        cv2.remap(
            self.cc_b, self.mapx_h, self.mapy_h, cv2.INTER_LINEAR, dst=self.cc_a,
            borderMode=cv2.BORDER_REPLICATE,
        )
        if BLEED_SLABS_ENABLED:  # C
            self._slabs(t)
        if BLEED_TRAIL_ENABLED:  # B
            self._trail(k)
        if BLEED_SATURATION != 1.0:  # more colour (128 = no colour)
            cv2.addWeighted(self.cc_a, BLEED_SATURATION, self.cc_a, 0.0, 128.0 * (1.0 - BLEED_SATURATION), dst=self.cc_a)
        cv2.resize(self.cc_a, (W, H), dst=self.cc_full, interpolation=cv2.INTER_LINEAR)
        cv2.mixChannels([self.cc_full], [self.ycc], [0, 1, 1, 2])  # back into Cr, Cb
        cv2.cvtColor(self.ycc, cv2.COLOR_YCrCb2BGR, dst=frame)


_BLEED = BleedEngine() if "chroma_bleed" not in DISABLED_EFFECTS else None


def apply_chroma_bleed(raw_knob, current_time):
    # Chroma Bleed entry point. raw_knob 0-1023. Modifies MUTABLE_PROCESSING_BUFFER in place.
    if not _acts_chroma_bleed(raw_knob):
        _BLEED.reset()  # lowering the knob forgets the colour memory and the slabs at once
        return
    _BLEED.step(MUTABLE_PROCESSING_BUFFER, n(raw_knob), current_time)


def _acts_chroma_bleed(raw):
    return n(raw) > BLEED_ACTIVE_ABOVE
