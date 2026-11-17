"""Moire Overlay (new): a screen-grid overlay, like the CRT overlay.

Real moire needs HARD-EDGED grids: the soft, smooth dot grids of the first version give moire
bands of only about 1 to 2 percent brightness, so they are nearly invisible. The overlay now has
four layers, each with its own switch; any mix works:

  MOIRE_SOFT_ENABLED      the original soft dot grids (very faint bands)
  MOIRE_LINES_ENABLED  A  two line grids a few degrees apart. MOIRE_LINES_MODE "bands" draws the
                          moire bands directly plus a fine line texture (gentle, the default);
                          "hard" uses hard dark line grids (strong, darkens the picture)
  MOIRE_RGB_ENABLED    B  hard red / green / blue sub-pixel dots, two grids a few degrees apart
                          (coloured moire, like a photographed LED screen)
  MOIRE_RESAMPLE_ENABLED C  a fine line texture is laid on the picture, which is then rotated and
                          scaled slightly WITHOUT smoothing, so the lines alias into bands

With MOIRE_NOISE_ENABLED the angle between the grids drifts smoothly, so the bands move. It is
not an effect on a knob: it runs at the end of the pipeline (just before the CRT overlay) while
MOIRE_ENABLED is True. The grids are prepared once at start (MOIRE_STEPS pictures per grid).
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
    MOIRE_BANDS_LINE_STRENGTH,
    MOIRE_BANDS_STRENGTH,
    MOIRE_ENABLED,
    MOIRE_KEEP_BRIGHTNESS,
    MOIRE_LINES_ENABLED,
    MOIRE_LINES_MODE,
    MOIRE_LINES_PERIOD_PX,
    MOIRE_LINES_STRENGTH,
    MOIRE_NOISE_ENABLED,
    MOIRE_PERIOD_PX,
    MOIRE_RESAMPLE_ANGLE_DEG,
    MOIRE_RESAMPLE_ANGLE_SPAN_DEG,
    MOIRE_RESAMPLE_ENABLED,
    MOIRE_RESAMPLE_PERIOD_PX,
    MOIRE_RESAMPLE_SCALE,
    MOIRE_RESAMPLE_STRENGTH,
    MOIRE_RGB_ENABLED,
    MOIRE_RGB_PIXEL_PX,
    MOIRE_RGB_ROW_PX,
    MOIRE_RGB_STRENGTH,
    MOIRE_SOFT_ENABLED,
    MOIRE_STEPS,
    MOIRE_STRENGTH,
)


def _dots(xx, yy, period, angle_deg):
    a = np.deg2rad(angle_deg)
    u = (xx * np.cos(a) + yy * np.sin(a)) / period
    v = (-xx * np.sin(a) + yy * np.cos(a)) / period
    return (0.5 + 0.5 * np.cos(2.0 * np.pi * u)) * (0.5 + 0.5 * np.cos(2.0 * np.pi * v))


def _coords(xx, yy, angle_deg):
    a = np.deg2rad(angle_deg)
    return xx * np.cos(a) + yy * np.sin(a), -xx * np.sin(a) + yy * np.cos(a)


def _hard_lines(xx, yy, period, angle_deg, dark_fraction, strength):
    # Hard-edged lines: dark for `dark_fraction` of every period.
    _, v = _coords(xx, yy, angle_deg)
    return 1.0 - strength * (((v / period) % 1.0) < dark_fraction)


def _subpixels(xx, yy, angle_deg, strength, pixel_px, row_px):
    # Hard red / green / blue stripes (one third of `pixel_px` each) cut into dots every `row_px` rows.
    u, v = _coords(xx, yy, angle_deg)
    out = np.zeros(xx.shape + (3,), np.float32)
    for c in range(3):  # B, G, R
        on = ((u / pixel_px - c / 3.0) % 1.0 < 1.0 / 3.0) & ((v / row_px) % 1.0 < 0.66)
        out[..., c] = 1.0 - strength * (~on)
    return out


class MoireEngine:
    def __init__(self):
        H, W = FRAME_H, FRAME_W
        yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
        steps = max(2, int(MOIRE_STEPS))
        self.steps = steps
        self.rgb = bool(MOIRE_RGB_ENABLED)
        s = min(max(float(MOIRE_STRENGTH), 0.0), 1.0)
        soft_period = max(float(MOIRE_PERIOD_PX), 1e-3)
        lines_period = max(float(MOIRE_LINES_PERIOD_PX), 1e-3)
        soft_a = 1.0 - s * (1.0 - _dots(xx, yy, soft_period, 0.0)) if MOIRE_SOFT_ENABLED else None
        lines_a = None
        if MOIRE_LINES_ENABLED and MOIRE_LINES_MODE == "hard":
            lines_a = _hard_lines(xx, yy, lines_period, 0.0, 0.5, MOIRE_LINES_STRENGTH)
        elif MOIRE_LINES_ENABLED:  # "bands"
            lines_a = _hard_lines(xx, yy, lines_period, 0.0, 0.5, MOIRE_BANDS_LINE_STRENGTH)
        shape = (steps, H, W, 3) if self.rgb else (steps, H, W)
        self.stack = np.zeros(shape, np.uint8)
        total = 0.0
        any_layer = bool(MOIRE_SOFT_ENABLED or MOIRE_LINES_ENABLED or self.rgb)
        for i in range(steps):
            ang = MOIRE_ANGLE_DEG + MOIRE_ANGLE_SPAN_DEG * (2.0 * i / (steps - 1) - 1.0)
            tex = np.ones((H, W), np.float32)
            if soft_a is not None:
                tex = tex * (soft_a * (1.0 - s * (1.0 - _dots(xx, yy, soft_period, ang))))
            if MOIRE_LINES_ENABLED:
                if MOIRE_LINES_MODE == "hard":
                    tex = tex * (lines_a * _hard_lines(xx, yy, lines_period, ang, 0.5, MOIRE_LINES_STRENGTH))
                else:
                    _, v0 = _coords(xx, yy, 0.0)
                    _, va = _coords(xx, yy, ang)
                    beat = 0.5 + 0.5 * np.cos(2.0 * np.pi * (v0 - va) / lines_period)
                    tex = tex * ((1.0 - MOIRE_BANDS_STRENGTH * (1.0 - beat)) * lines_a)
            if self.rgb:
                tex = tex[..., None] * (
                    _subpixels(xx, yy, 0.0, MOIRE_RGB_STRENGTH, MOIRE_RGB_PIXEL_PX, MOIRE_RGB_ROW_PX)
                    * _subpixels(xx, yy, ang, MOIRE_RGB_STRENGTH, MOIRE_RGB_PIXEL_PX, MOIRE_RGB_ROW_PX)
                )
            self.stack[i] = np.clip(tex * 255.0 + 0.5, 0, 255).astype(np.uint8)
            total += float(tex.mean())
        mean = max(total / steps, 1e-3)
        self.scale = (1.0 / mean if MOIRE_KEEP_BRIGHTNESS else 1.0) / 255.0
        self.any_layer = any_layer
        self.tex = np.zeros((H, W, 3) if self.rgb else (H, W), np.uint8)
        self.tex3 = np.zeros((H, W, 3), np.uint8)
        self.table = noise.table_for("moire_overlay")
        # C: the fine line texture and the rotated copy
        self.resample = bool(MOIRE_RESAMPLE_ENABLED)
        if self.resample:
            fine = _hard_lines(xx, yy, max(float(MOIRE_RESAMPLE_PERIOD_PX), 1e-3), 0.0, 0.34, MOIRE_RESAMPLE_STRENGTH)
            self.fine3 = np.zeros((H, W, 3), np.uint8)
            self.fine3[:] = np.clip(fine * 255.0 + 0.5, 0, 255).astype(np.uint8)[..., None]
            self.fine_scale = (1.0 / max(float(fine.mean()), 1e-3) if MOIRE_KEEP_BRIGHTNESS else 1.0) / 255.0
            self.warp = np.zeros((H, W, 3), np.uint8)

    def _resample(self, frame, t):
        H, W = FRAME_H, FRAME_W
        cv2.multiply(frame, self.fine3, dst=frame, scale=self.fine_scale)
        ang = MOIRE_RESAMPLE_ANGLE_DEG
        if noise.is_on(MOIRE_NOISE_ENABLED):
            ang += MOIRE_RESAMPLE_ANGLE_SPAN_DEG * float(noise.noise2(self.table, 2.7, t * MOIRE_ANGLE_SPEED + 5.0))
        m = cv2.getRotationMatrix2D((W / 2.0, H / 2.0), ang, MOIRE_RESAMPLE_SCALE)
        cv2.warpAffine(frame, m, (W, H), dst=self.warp, flags=cv2.INTER_NEAREST, borderMode=cv2.BORDER_REFLECT)
        np.copyto(frame, self.warp)

    def step(self, frame, t):
        if self.resample:  # C first: the grids are laid on top of the resampled picture
            self._resample(frame, t)
        if not self.any_layer:
            return
        top = self.steps - 1
        if noise.is_on(MOIRE_NOISE_ENABLED):
            pos = (0.5 + 0.5 * float(noise.noise2(self.table, 0.37, t * MOIRE_ANGLE_SPEED))) * top
        else:
            pos = top / 2.0
        i0 = min(int(pos), top - 1) if top > 0 else 0
        a = min(max(pos - i0, 0.0), 1.0)
        cv2.addWeighted(self.stack[i0], 1.0 - a, self.stack[i0 + 1], a, 0, dst=self.tex)
        if self.rgb:
            cv2.multiply(frame, self.tex, dst=frame, scale=self.scale)
        else:
            cv2.merge([self.tex, self.tex, self.tex], dst=self.tex3)
            cv2.multiply(frame, self.tex3, dst=frame, scale=self.scale)


_MOIRE = MoireEngine() if MOIRE_ENABLED and "moire_overlay" not in DISABLED_EFFECTS else None


def apply_moire_overlay(current_time):
    # Moire overlay entry point; called by the pipeline just before the CRT overlay.
    if _MOIRE is None:
        return
    _MOIRE.step(MUTABLE_PROCESSING_BUFFER, current_time)
