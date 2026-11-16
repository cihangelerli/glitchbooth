"""Chroma Bleed effect (new).

Like a worn tape or a bad cable: the COLOUR of the picture smears sideways and trails behind
the sharp black-and-white detail. Different from Chroma Shift, which moves a whole colour
channel. With BLEED_NOISE_ENABLED a smooth, moving noise makes the trailing length vary from row
to row (some rows even trail the other way).

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
    BLEED_MAX_PX,
    BLEED_NOISE_AMOUNT,
    BLEED_NOISE_ENABLED,
    BLEED_NOISE_ROWS,
    BLEED_NOISE_SPEED,
    DISABLED_EFFECTS,
    FRAME_H,
    FRAME_W,
)


class BleedEngine:
    """Buffers (about 3 MiB, allocated once) and the per-frame work of Chroma Bleed."""

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

    def step(self, frame, k, t):
        H, W = FRAME_H, FRAME_W
        hh, hw = H // 2, W // 2
        cv2.cvtColor(frame, cv2.COLOR_BGR2YCrCb, dst=self.ycc)
        cv2.resize(self.ycc, (hw, hh), dst=self.ycc_h, interpolation=cv2.INTER_AREA)
        cv2.mixChannels([self.ycc_h], [self.cc_a], [1, 0, 2, 1])  # Cr, Cb only
        kk = int(k * BLEED_MAX_PX / 2.0) * 2 + 1  # horizontal smear length at half resolution
        if kk > 1:
            cv2.blur(self.cc_a, (kk, 1), dst=self.cc_b)
        else:
            np.copyto(self.cc_b, self.cc_a)
        lag = np.full(hh, k * BLEED_LAG_PX / 2.0)
        if noise.is_on(BLEED_NOISE_ENABLED):
            lag *= 1.0 + BLEED_NOISE_AMOUNT * noise.lane(
                self.table, self.rows_px, t, BLEED_NOISE_ROWS, BLEED_NOISE_SPEED
            )
        np.subtract(self.xs_h, lag[:, None].astype(np.float32), out=self.mapx_h)
        cv2.remap(
            self.cc_b, self.mapx_h, self.mapy_h, cv2.INTER_LINEAR, dst=self.cc_a,
            borderMode=cv2.BORDER_REPLICATE,
        )
        cv2.resize(self.cc_a, (W, H), dst=self.cc_full, interpolation=cv2.INTER_LINEAR)
        cv2.mixChannels([self.cc_full], [self.ycc], [0, 1, 1, 2])  # back into Cr, Cb
        cv2.cvtColor(self.ycc, cv2.COLOR_YCrCb2BGR, dst=frame)


_BLEED = BleedEngine() if "chroma_bleed" not in DISABLED_EFFECTS else None


def apply_chroma_bleed(raw_knob, current_time):
    # Chroma Bleed entry point. raw_knob 0-1023. Modifies MUTABLE_PROCESSING_BUFFER in place.
    if not _acts_chroma_bleed(raw_knob):
        return
    _BLEED.step(MUTABLE_PROCESSING_BUFFER, n(raw_knob), current_time)


def _acts_chroma_bleed(raw):
    return n(raw) > BLEED_ACTIVE_ABOVE
