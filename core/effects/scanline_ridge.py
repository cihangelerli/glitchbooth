"""Scanline Ridge effect (new).

The picture becomes a stack of horizontal lines; each line is lifted by the brightness beneath
it, so bright shapes rise like mountain ridges (the Rutt-Etra / "Unknown Pleasures" look). Lower
lines hide the ones behind them. With RIDGE_NOISE_ENABLED a smooth, moving noise makes some
lines tall and others flat and gives every line an uneven wobble.

Place it FIRST in EFFECT_ORDER: later effects (drift, chroma shift, ...) then act on the lines.
At the low end of the knob the lines sit on the fading photo, so faces stay readable.
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
    NEON_PALETTES,
    RIDGE_ACTIVE_ABOVE,
    RIDGE_ANTIALIAS,
    RIDGE_COLOUR_SOURCE,
    RIDGE_COLOUR_STOPS,
    RIDGE_GLOW_GAIN,
    RIDGE_GLOW_RADIUS_PX,
    RIDGE_HEIGHT,
    RIDGE_LINE_SPACING,
    RIDGE_LINE_THICKNESS,
    RIDGE_LINES_FULL_AT,
    RIDGE_NOISE_BEND_PX,
    RIDGE_NOISE_BEND_SIZE_PX,
    RIDGE_NOISE_BEND_SPEED,
    RIDGE_NOISE_CONTRAST,
    RIDGE_NOISE_ENABLED,
    RIDGE_NOISE_ZONE_LINES,
    RIDGE_NOISE_ZONE_SPEED,
    RIDGE_PHOTO_FADE_FROM,
    RIDGE_PHOTO_FADE_TO,
    RIDGE_ROTATE_SECONDS,
    RIDGE_SAMPLE_STEP_PX,
)


def _colour_luts(rotation=0):
    # Three 256-entry tables (B, G, R): tone -> line colour. `rotation` shifts which colours of the
    # booth palette are used (palette source only).
    if RIDGE_COLOUR_SOURCE == "palette":
        luma = NEON_PALETTES.astype(np.float32) @ np.array([0.114, 0.587, 0.299], np.float32)
        pal = np.roll(NEON_PALETTES[np.argsort(luma)], -int(rotation), axis=0)  # darkest hue to brightest, rotated
        stops = pal[np.round(np.linspace(0, len(pal) - 1, 4)).astype(int)]
    else:
        stops = np.asarray(RIDGE_COLOUR_STOPS, dtype=np.float32)
    x = np.arange(256, dtype=np.float32)
    pos = np.linspace(0, 255, len(stops))
    luts = np.zeros((3, 256, 1), dtype=np.uint8)
    for ch in range(3):
        luts[ch, :, 0] = np.clip(np.interp(x, pos, stops[:, ch]), 0, 255)
    return luts


class RidgeEngine:
    """Buffers (about 5 MiB, allocated once) and the per-frame work of Scanline Ridge."""

    def __init__(self):
        H, W = FRAME_H, FRAME_W
        self.qh, self.qw = H // 4, W // 4
        step = max(1, int(RIDGE_LINE_SPACING))
        self.base = np.arange(step, H + step, step)  # resting row of every line
        self.nlines = len(self.base)
        self.q_rows = np.clip(self.base // 4, 0, self.qh - 1)
        xs = np.append(np.arange(0, W, max(1, int(RIDGE_SAMPLE_STEP_PX))), W - 1)
        self.xs = xs.astype(np.float64)
        self.npts = len(xs)
        self.small3 = np.zeros((self.qh, self.qw, 3), np.uint8)
        self.small_g = np.zeros((self.qh, self.qw), np.uint8)
        self.col_q = np.zeros((self.qh, self.qw, 3), np.uint8)
        self.cb, self.cg, self.cr = (np.zeros((self.qh, self.qw), np.uint8) for _ in range(3))
        self.col_full = np.zeros((H, W, 3), np.uint8)
        self.mask = np.zeros((H, W), np.uint8)
        self.mask3 = np.zeros((H, W, 3), np.uint8)
        self.lines = np.zeros((H, W, 3), np.uint8)
        self.halo_q = np.zeros((self.qh, self.qw, 3), np.uint8)
        self.halo = np.zeros((H, W, 3), np.uint8)
        self.poly = np.zeros((self.npts + 2, 2), np.int32)
        self.poly[: self.npts, 0] = xs
        self.line_idx = np.arange(self.nlines, dtype=np.float64)
        self.luts = _colour_luts()
        self.rotation = 0
        self.table = noise.table_for("scanline_ridge")
        self.lo, self.hi = 0.0, 255.0  # smoothed brightness range (no flicker)

    def step(self, frame, k, t):
        H, W = FRAME_H, FRAME_W
        if RIDGE_COLOUR_SOURCE == "palette" and RIDGE_ROTATE_SECONDS > 0:  # the palette colours rotate
            rotation = int(t / RIDGE_ROTATE_SECONDS) % len(NEON_PALETTES)
            if rotation != self.rotation:
                self.luts = _colour_luts(rotation)
                self.rotation = rotation
        # tones at quarter resolution, stretched to the full range (smoothed over time)
        cv2.resize(frame, (self.qw, self.qh), dst=self.small3, interpolation=cv2.INTER_AREA)
        cv2.cvtColor(self.small3, cv2.COLOR_BGR2GRAY, dst=self.small_g)
        lo, hi = np.percentile(self.small_g, (2, 98))
        self.lo += 0.15 * (lo - self.lo)
        self.hi += 0.15 * (hi - self.hi)
        tone = np.clip((self.small_g - self.lo) / max(self.hi - self.lo, 1.0), 0.0, 1.0)
        tone = tone.astype(np.float32)
        lines_tone = cv2.resize(
            tone[self.q_rows], (self.npts, self.nlines), interpolation=cv2.INTER_LINEAR
        )

        # ridge height per line and the line heights
        height = k * RIDGE_HEIGHT * H
        hmul = np.ones(self.nlines)
        bend = 0.0
        if noise.is_on(RIDGE_NOISE_ENABLED):
            zone = 0.5 + 0.5 * noise.lane(
                self.table, self.line_idx, t, RIDGE_NOISE_ZONE_LINES, RIDGE_NOISE_ZONE_SPEED, 1
            )
            zone = np.clip((zone - 0.5) * 2.2 + 0.5, 0.0, 1.0)
            hmul = (1.0 - RIDGE_NOISE_CONTRAST) + RIDGE_NOISE_CONTRAST * zone
            if RIDGE_NOISE_BEND_PX > 0:
                bend = (k * RIDGE_NOISE_BEND_PX) * noise.noise2(
                    self.table,
                    self.xs[None, :] / max(RIDGE_NOISE_BEND_SIZE_PX, 1e-6),
                    self.line_idx[:, None] * 0.35 + t * RIDGE_NOISE_BEND_SPEED + 90.0,
                )
        ys = self.base[:, None] - height * hmul[:, None] * lines_tone + bend
        ys = np.clip(ys, 0, H - 1).astype(np.int32)

        # draw top to bottom; each lower line hides what lies behind it
        self.mask.fill(0)
        thick = max(1, int(RIDGE_LINE_THICKNESS))
        line_type = cv2.LINE_AA if RIDGE_ANTIALIAS else cv2.LINE_8
        for i in range(self.nlines):
            self.poly[: self.npts, 1] = ys[i]
            bottom = min(H - 1, int(self.base[i]) + thick)
            self.poly[self.npts] = (W - 1, bottom)
            self.poly[self.npts + 1] = (0, bottom)
            cv2.fillPoly(self.mask, [self.poly], 0)
            cv2.polylines(
                self.mask, [self.poly[: self.npts].reshape(-1, 1, 2)], False, 255, thick, line_type
            )

        # colour from the tone (quarter resolution, grown), only where the lines are
        idx = (tone * 255.0).astype(np.uint8)
        cv2.LUT(idx, self.luts[0], dst=self.cb)
        cv2.LUT(idx, self.luts[1], dst=self.cg)
        cv2.LUT(idx, self.luts[2], dst=self.cr)
        cv2.merge([self.cb, self.cg, self.cr], dst=self.col_q)
        cv2.resize(self.col_q, (W, H), dst=self.col_full, interpolation=cv2.INTER_LINEAR)
        cv2.merge([self.mask, self.mask, self.mask], dst=self.mask3)
        cv2.multiply(self.col_full, self.mask3, dst=self.lines, scale=1.0 / 255.0)
        if RIDGE_GLOW_GAIN > 0:
            cv2.resize(self.lines, (self.qw, self.qh), dst=self.halo_q, interpolation=cv2.INTER_AREA)
            cv2.GaussianBlur(self.halo_q, (0, 0), max(0.5, RIDGE_GLOW_RADIUS_PX / 4.0), dst=self.halo_q)
            cv2.resize(self.halo_q, (W, H), dst=self.halo, interpolation=cv2.INTER_LINEAR)
            cv2.convertScaleAbs(self.halo, dst=self.halo, alpha=RIDGE_GLOW_GAIN)
            cv2.add(self.lines, self.halo, dst=self.lines)

        # the photo fades out as the knob rises; the lines fade in
        fade = max(RIDGE_PHOTO_FADE_TO - RIDGE_PHOTO_FADE_FROM, 1e-6)
        photo = float(np.clip(1.0 - (k - RIDGE_PHOTO_FADE_FROM) / fade, 0.0, 1.0))
        alpha = float(np.clip(k / max(RIDGE_LINES_FULL_AT, 1e-6), 0.0, 1.0))
        cv2.addWeighted(frame, photo, self.lines, alpha, 0, dst=frame)


_RIDGE = RidgeEngine() if "scanline_ridge" not in DISABLED_EFFECTS else None


def apply_scanline_ridge(raw_knob, current_time):
    # Scanline Ridge entry point. raw_knob 0-1023. Modifies MUTABLE_PROCESSING_BUFFER in place.
    if not _acts_scanline_ridge(raw_knob):
        return
    _RIDGE.step(MUTABLE_PROCESSING_BUFFER, n(raw_knob), current_time)


def _acts_scanline_ridge(raw):
    return n(raw) > RIDGE_ACTIVE_ABOVE
