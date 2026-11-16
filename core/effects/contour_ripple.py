"""Contour Ripple effect (new).

Every pixel is pushed sideways by a sine of the picture's own brightness, so waves follow the
shapes in the picture (a face ripples along its contours). The ripples crawl over time. With
RIPPLE_NOISE_ENABLED a smooth, moving noise makes some zones calm and others wild.

Place it right after Signal Drift: the two waves then add up.
"""

import cv2
import numpy as np

from core import noise
from core.scale import n
from state import MUTABLE_PROCESSING_BUFFER
from tuning import (
    DISABLED_EFFECTS,
    FRAME_H,
    FRAME_W,
    RIPPLE_ACTIVE_ABOVE,
    RIPPLE_CYCLES,
    RIPPLE_MAX_SHIFT_PX,
    RIPPLE_NOISE_AMOUNT,
    RIPPLE_NOISE_ENABLED,
    RIPPLE_NOISE_SIZE_PX,
    RIPPLE_NOISE_SPEED,
    RIPPLE_SPEED,
    RIPPLE_TONE_BLUR_PX,
)

_ZONE_STEP_PX = 32  # the noise zones are computed on a grid this coarse, then grown smoothly


class RippleEngine:
    """Buffers (about 6 MiB, allocated once) and the per-frame work of Contour Ripple."""

    def __init__(self):
        H, W = FRAME_H, FRAME_W
        self.qh, self.qw = H // 4, W // 4
        self.small3 = np.zeros((self.qh, self.qw, 3), np.uint8)
        self.small_g = np.zeros((self.qh, self.qw), np.uint8)
        self.shift_q = np.zeros((self.qh, self.qw), np.float32)
        self.shift_full = np.zeros((H, W), np.float32)
        self.xs = np.tile(np.arange(W, dtype=np.float32), (H, 1))
        self.mapx = self.xs.copy()
        self.mapy = np.tile(np.arange(H, dtype=np.float32)[:, None], (1, W))
        self.out = np.zeros((H, W, 3), np.uint8)
        self.zone_x = np.arange(0, W, _ZONE_STEP_PX, dtype=np.float64)
        self.zone_y = np.arange(0, H, _ZONE_STEP_PX, dtype=np.float64)
        self.table = noise.table_for("contour_ripple")

    def step(self, frame, k, t):
        H, W = FRAME_H, FRAME_W
        cv2.resize(frame, (self.qw, self.qh), dst=self.small3, interpolation=cv2.INTER_AREA)
        cv2.cvtColor(self.small3, cv2.COLOR_BGR2GRAY, dst=self.small_g)
        if RIPPLE_TONE_BLUR_PX > 0:
            cv2.GaussianBlur(self.small_g, (0, 0), RIPPLE_TONE_BLUR_PX, dst=self.small_g)
        phase = (2.0 * np.pi * RIPPLE_CYCLES / 255.0) * self.small_g + t * RIPPLE_SPEED
        np.sin(phase, out=self.shift_q, casting="unsafe")
        amp = k * RIPPLE_MAX_SHIFT_PX
        self.shift_q *= amp
        if noise.is_on(RIPPLE_NOISE_ENABLED):
            zone = noise.field2(
                self.table, self.zone_x, self.zone_y, t, RIPPLE_NOISE_SIZE_PX, RIPPLE_NOISE_SPEED
            )
            zone = np.clip(0.5 + 0.5 * zone * 1.6, 0.0, 1.0)  # 0 = calm .. 1 = wild
            zone = (1.0 - RIPPLE_NOISE_AMOUNT) + RIPPLE_NOISE_AMOUNT * zone
            zone_q = cv2.resize(zone.astype(np.float32), (self.qw, self.qh), interpolation=cv2.INTER_LINEAR)
            self.shift_q *= zone_q
        cv2.resize(self.shift_q, (W, H), dst=self.shift_full, interpolation=cv2.INTER_LINEAR)
        cv2.add(self.xs, self.shift_full, dst=self.mapx)
        cv2.remap(frame, self.mapx, self.mapy, cv2.INTER_LINEAR, dst=self.out, borderMode=cv2.BORDER_REFLECT)
        np.copyto(frame, self.out)


_RIPPLE = RippleEngine() if "contour_ripple" not in DISABLED_EFFECTS else None


def apply_contour_ripple(raw_knob, current_time):
    # Contour Ripple entry point. raw_knob 0-1023. Modifies MUTABLE_PROCESSING_BUFFER in place.
    if not _acts_contour_ripple(raw_knob):
        return
    _RIPPLE.step(MUTABLE_PROCESSING_BUFFER, n(raw_knob), current_time)


def _acts_contour_ripple(raw):
    return n(raw) > RIPPLE_ACTIVE_ABOVE
