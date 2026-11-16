"""Moire Overlay (new): a screen-grid overlay, like the CRT overlay.

Two fine dot grids, a few degrees apart, are laid over the picture; where they interfere,
wide moire bands appear, as when a screen is photographed. With MOIRE_NOISE_ENABLED the angle
between the grids drifts smoothly, so the bands move. It is not an effect on a knob: it runs
at the end of the pipeline (just before the CRT overlay) while MOIRE_ENABLED is True.

The grids are prepared once at start (MOIRE_STEPS pictures, about 0.3 MiB each, only when
enabled). Each frame costs one blend of two prepared pictures and one multiply.
"""

import cv2
import numpy as np

from core import noise
from state import MUTABLE_PROCESSING_BUFFER
from tuning import (
    DISABLED_EFFECTS,
    FRAME_H,
    FRAME_W,
    MOIRE_ANGLE_DEG,
    MOIRE_ANGLE_SPAN_DEG,
    MOIRE_ANGLE_SPEED,
    MOIRE_ENABLED,
    MOIRE_KEEP_BRIGHTNESS,
    MOIRE_NOISE_ENABLED,
    MOIRE_PERIOD_PX,
    MOIRE_STEPS,
    MOIRE_STRENGTH,
)


def _dots(xx, yy, period, angle_deg):
    a = np.deg2rad(angle_deg)
    u = (xx * np.cos(a) + yy * np.sin(a)) / period
    v = (-xx * np.sin(a) + yy * np.cos(a)) / period
    return (0.5 + 0.5 * np.cos(2.0 * np.pi * u)) * (0.5 + 0.5 * np.cos(2.0 * np.pi * v))


class MoireEngine:
    def __init__(self):
        H, W = FRAME_H, FRAME_W
        yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
        period = max(float(MOIRE_PERIOD_PX), 1e-3)
        s = min(max(float(MOIRE_STRENGTH), 0.0), 1.0)
        steps = max(2, int(MOIRE_STEPS))
        grid_a = 1.0 - s * (1.0 - _dots(xx, yy, period, 0.0))
        self.stack = np.zeros((steps, H, W), np.uint8)
        total = 0.0
        for i in range(steps):
            ang = MOIRE_ANGLE_DEG + MOIRE_ANGLE_SPAN_DEG * (2.0 * i / (steps - 1) - 1.0)
            both = grid_a * (1.0 - s * (1.0 - _dots(xx, yy, period, ang)))
            self.stack[i] = np.clip(both * 255.0 + 0.5, 0, 255).astype(np.uint8)
            total += float(both.mean())
        mean = max(total / steps, 1e-3)
        self.scale = (1.0 / mean if MOIRE_KEEP_BRIGHTNESS else 1.0) / 255.0
        self.tex = np.zeros((H, W), np.uint8)
        self.tex3 = np.zeros((H, W, 3), np.uint8)
        self.table = noise.table_for("moire_overlay")
        self.steps = steps

    def step(self, frame, t):
        top = self.steps - 1
        if noise.is_on(MOIRE_NOISE_ENABLED):
            pos = (0.5 + 0.5 * float(noise.noise2(self.table, 0.37, t * MOIRE_ANGLE_SPEED))) * top
        else:
            pos = top / 2.0
        i0 = min(int(pos), top - 1) if top > 0 else 0
        a = min(max(pos - i0, 0.0), 1.0)
        cv2.addWeighted(self.stack[i0], 1.0 - a, self.stack[i0 + 1], a, 0, dst=self.tex)
        cv2.merge([self.tex, self.tex, self.tex], dst=self.tex3)
        cv2.multiply(frame, self.tex3, dst=frame, scale=self.scale)


_MOIRE = MoireEngine() if MOIRE_ENABLED and "moire_overlay" not in DISABLED_EFFECTS else None


def apply_moire_overlay(current_time):
    # Moire overlay entry point; called by the pipeline just before the CRT overlay.
    if _MOIRE is None:
        return
    _MOIRE.step(MUTABLE_PROCESSING_BUFFER, current_time)
