"""Scanline Ridge effect (new).

The picture becomes a stack of horizontal lines; each line is lifted by the brightness beneath
it, so bright shapes rise like mountain ridges (the Rutt-Etra / "Unknown Pleasures" look). Lower
lines hide the ones behind them. With RIDGE_NOISE_ENABLED a smooth, moving noise makes some
lines tall and others flat and gives every line an uneven wobble.

Line colour modes (like Posterize's colour modes):
  "stops"    the colour ramp RIDGE_COLOUR_STOPS (tone -> colour)
  "palette"  the booth neon palette (rotating every RIDGE_ROTATE_SECONDS)
  "image"    the picture's own colours (RIDGE_IMAGE_COLOUR_AT: "row" = each line carries the colours
             of its own picture row, "drawn" = the colour of the picture where the line is drawn)
RIDGE_MODE_SOURCE "knob" chooses the mode by the knob position (RIDGE_KNOB_MODES: where two zones
overlap the modes are blended); "fixed" always uses RIDGE_COLOUR_SOURCE. A mode can be two joined
by "+" ("image+palette", mixed by RIDGE_OVERLAY_MIX).

Place it FIRST in EFFECT_ORDER: later effects (drift, chroma shift, ...) then act on the lines.
At the low end of the knob the lines sit on the fading photo, so faces stay readable.
"""

import logging

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
    RIDGE_IMAGE_COLOUR_AT,
    RIDGE_IMAGE_GAIN,
    RIDGE_IMAGE_SATURATION,
    RIDGE_KNOB_MODE_HYSTERESIS,
    RIDGE_KNOB_MODES,
    RIDGE_LINE_SPACING,
    RIDGE_LINE_THICKNESS,
    RIDGE_LINES_FULL_AT,
    RIDGE_MODE_SOURCE,
    RIDGE_NOISE_BEND_PX,
    RIDGE_NOISE_BEND_SIZE_PX,
    RIDGE_NOISE_BEND_SPEED,
    RIDGE_NOISE_CONTRAST,
    RIDGE_NOISE_ENABLED,
    RIDGE_NOISE_ZONE_LINES,
    RIDGE_NOISE_ZONE_SPEED,
    RIDGE_OVERLAY_MIX,
    RIDGE_PHOTO_FADE_FROM,
    RIDGE_PHOTO_FADE_TO,
    RIDGE_ROTATE_SECONDS,
    RIDGE_SAMPLE_STEP_PX,
)

logger = logging.getLogger("glitchbooth.core.effects.scanline_ridge")

_MODES = ("stops", "palette", "image")
_MAX_ROW_LINES = 254  # row mode numbers the lines in an 8-bit picture (0 = no line)


def _luts(kind, rotation=0):
    # Three 256-entry tables (B, G, R): tone -> line colour. kind "palette" = the booth neon
    # palette, rotated by `rotation` colours; anything else = RIDGE_COLOUR_STOPS.
    if kind == "palette":
        luma = NEON_PALETTES.astype(np.float32) @ np.array([0.114, 0.587, 0.299], np.float32)
        pal = np.roll(NEON_PALETTES[np.argsort(luma)], -int(rotation), axis=0)  # dark to bright, rotated
        stops = pal[np.round(np.linspace(0, len(pal) - 1, 4)).astype(int)]
    else:
        stops = np.asarray(RIDGE_COLOUR_STOPS, dtype=np.float32)
    x = np.arange(256, dtype=np.float32)
    pos = np.linspace(0, 255, len(stops))
    luts = np.zeros((3, 256, 1), dtype=np.uint8)
    for ch in range(3):
        luts[ch, :, 0] = np.clip(np.interp(x, pos, stops[:, ch]), 0, 255)
    return luts


def _mode_name_ok(name):
    parts = [p.strip() for p in str(name).split("+")]
    return 1 <= len(parts) <= 2 and all(p in _MODES for p in parts)


class RidgeEngine:
    """Buffers (about 7 MiB, allocated once) and the per-frame work of Scanline Ridge."""

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
        self.col_tmp = np.zeros((self.qh, self.qw, 3), np.uint8)
        self.col_img = np.zeros((self.qh, self.qw, 3), np.uint8)  # the picture's colours, boosted
        self.cb, self.cg, self.cr = (np.zeros((self.qh, self.qw), np.uint8) for _ in range(3))
        self.col_full = np.zeros((H, W, 3), np.uint8)
        self.mask = np.zeros((H, W), np.uint8)
        self.mask3 = np.zeros((H, W, 3), np.uint8)
        self.lines = np.zeros((H, W, 3), np.uint8)
        self.lines_row = np.zeros((H, W, 3), np.uint8)
        self.ids = np.zeros((H, W), np.uint8)  # row mode: line number + 1 at every line pixel
        self.row_ok = self.nlines <= _MAX_ROW_LINES
        if not self.row_ok and RIDGE_IMAGE_COLOUR_AT == "row":
            logger.warning("[CONFIG] Ridge: more than %d lines; the image mode draws the colour 'drawn' instead of 'row'", _MAX_ROW_LINES)
        self.halo_q = np.zeros((self.qh, self.qw, 3), np.uint8)
        self.halo = np.zeros((H, W, 3), np.uint8)
        self.poly = np.zeros((self.npts + 2, 2), np.int32)
        self.poly[: self.npts, 0] = xs
        self.line_idx = np.arange(self.nlines, dtype=np.float64)
        self.luts_stops = _luts("stops")
        self.luts_pal = _luts("palette", 0)
        self.rotation = 0
        gain, sat = float(RIDGE_IMAGE_GAIN), float(RIDGE_IMAGE_SATURATION)
        v = np.arange(256, dtype=np.float32)
        self.lut_y = np.clip(v * gain, 0, 255).astype(np.uint8)  # brightness of the picture's colours
        self.lut_c = np.clip(128.0 + (v - 128.0) * sat, 0, 255).astype(np.uint8)  # their saturation
        self.table = noise.table_for("scanline_ridge")
        self.lo, self.hi = 0.0, 255.0  # smoothed brightness range (no flicker)
        self.zones = self._parse_knob_modes()
        self._mode_x = None  # last knob position the mode selection followed (hysteresis)

    # ------------------------------------------------------------------ colour modes
    def _parse_knob_modes(self):
        # RIDGE_KNOB_MODES -> [(low, high, mode), ...], or None when the mode is RIDGE_COLOUR_SOURCE.
        if not _mode_name_ok(RIDGE_COLOUR_SOURCE):
            logger.warning("[CONFIG] Ridge: RIDGE_COLOUR_SOURCE %r is not a colour mode (stops, palette, image, or two joined by +); 'stops' is used", RIDGE_COLOUR_SOURCE)
        if RIDGE_MODE_SOURCE not in ("knob", "fixed"):
            logger.warning("[CONFIG] RIDGE_MODE_SOURCE=%r must be 'knob' or 'fixed'; 'fixed' is used", RIDGE_MODE_SOURCE)
        if RIDGE_MODE_SOURCE != "knob":
            return None
        zones = []
        try:
            for entry in RIDGE_KNOB_MODES:
                low, high, mode = float(entry[0]), float(entry[1]), entry[2]
                if not (0.0 <= low < high <= 1.0):
                    raise ValueError(f"{entry!r}: needs 0 <= lower edge < upper edge <= 1")
                if not _mode_name_ok(mode):
                    raise ValueError(f"{entry!r}: unknown mode {mode!r}")
                zones.append((low, high, mode))
            if not zones:
                raise ValueError("the table is empty")
            if min(z[0] for z in zones) > 0.0 or max(z[1] for z in zones) < 1.0:
                logger.warning("[CONFIG] RIDGE_KNOB_MODES does not cover the whole knob (0 to 1); the nearest zone is used outside it")
        except Exception as exc:
            logger.warning("[CONFIG] RIDGE_KNOB_MODES is not usable (%s); RIDGE_COLOUR_SOURCE %r is used", exc, RIDGE_COLOUR_SOURCE)
            return None
        return zones

    @staticmethod
    def _add_mode(out, name, weight):
        # Adds `name` (a mode, or "a+b" = two modes shown mixed) with `weight` to out.
        if weight <= 0:
            return
        parts = [p.strip() for p in str(name).split("+")][:2]
        if len(parts) == 2:
            m = min(max(RIDGE_OVERLAY_MIX, 0.0), 1.0)
            shares = [(parts[0], 1.0 - m), (parts[1], m)]
        else:
            shares = [(parts[0], 1.0)]
        for p, sh in shares:
            if sh > 0:
                p = p if p in _MODES else "stops"
                out[p] = out.get(p, 0.0) + weight * sh

    def _mode_weights(self, x):
        # Knob position x (0-1) -> [(mode, weight), ...]. One zone = that mode. Overlapping zones are
        # blended: a zone's weight is how far x is from the nearest edge of that zone.
        out = {}
        if self.zones is None:
            self._add_mode(out, RIDGE_COLOUR_SOURCE, 1.0)
            return list(out.items()) or [("stops", 1.0)]
        if self._mode_x is not None and abs(x - self._mode_x) < RIDGE_KNOB_MODE_HYSTERESIS:
            x = self._mode_x
        else:
            self._mode_x = x
        inside = [z for z in self.zones if z[0] <= x <= z[1]]
        if not inside:  # a gap or outside the table: the nearest edge decides
            self._add_mode(out, min(self.zones, key=lambda z: min(abs(x - z[0]), abs(x - z[1])))[2], 1.0)
        elif len(inside) == 1:
            self._add_mode(out, inside[0][2], 1.0)
        else:
            depth = [min(x - z[0], z[1] - x) for z in inside]
            total = sum(depth)
            if total <= 0:  # exactly on a shared edge: the first zone
                self._add_mode(out, inside[0][2], 1.0)
            else:
                for z, d in zip(inside, depth):
                    self._add_mode(out, z[2], d / total)
        return list(out.items()) or [("stops", 1.0)]

    def _field(self, mode, idx, dst):
        # Quarter-resolution colour field of one mode (stops / palette: tone -> colour; image: the picture).
        if mode == "image":
            np.copyto(dst, self.col_img)
            return
        luts = self.luts_pal if mode == "palette" else self.luts_stops
        cv2.LUT(idx, luts[0], dst=self.cb)
        cv2.LUT(idx, luts[1], dst=self.cg)
        cv2.LUT(idx, luts[2], dst=self.cr)
        cv2.merge([self.cb, self.cg, self.cr], dst=dst)

    def _row_lines(self):
        # Line colours from the picture row each line was cut from (row mode), only where lines are.
        H, W = FRAME_H, FRAME_W
        rows = cv2.resize(self.col_img[self.q_rows], (W, self.nlines), interpolation=cv2.INTER_LINEAR)
        flat = np.flatnonzero(self.mask)  # the line pixels (the mask is 0 or 255 in row mode)
        lid = self.ids.ravel()[flat].astype(np.int64) - 1  # the line number of every line pixel
        self.lines_row.fill(0)
        self.lines_row.reshape(-1, 3)[flat] = rows.reshape(-1, 3)[lid * W + flat % W]

    # ------------------------------------------------------------------ per frame
    def step(self, frame, k, t):
        H, W = FRAME_H, FRAME_W
        parts = self._mode_weights(k)
        used = {m for m, _ in parts}
        if "palette" in used and RIDGE_ROTATE_SECONDS > 0:  # the palette colours rotate
            rotation = int(t / RIDGE_ROTATE_SECONDS) % len(NEON_PALETTES)
            if rotation != self.rotation:
                self.luts_pal = _luts("palette", rotation)
                self.rotation = rotation
        use_row = "image" in used and RIDGE_IMAGE_COLOUR_AT == "row" and self.row_ok
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
        if use_row:  # row mode draws the line numbers (no anti-aliasing); the mask follows from them
            self.ids.fill(0)
        else:
            self.mask.fill(0)
        thick = max(1, int(RIDGE_LINE_THICKNESS))
        line_type = cv2.LINE_AA if RIDGE_ANTIALIAS else cv2.LINE_8
        for i in range(self.nlines):
            self.poly[: self.npts, 1] = ys[i]
            bottom = min(H - 1, int(self.base[i]) + thick)
            self.poly[self.npts] = (W - 1, bottom)
            self.poly[self.npts + 1] = (0, bottom)
            pts = [self.poly[: self.npts].reshape(-1, 1, 2)]
            if use_row:
                cv2.fillPoly(self.ids, [self.poly], 0)
                cv2.polylines(self.ids, pts, False, i + 1, thick, cv2.LINE_8)
            else:
                cv2.fillPoly(self.mask, [self.poly], 0)
                cv2.polylines(self.mask, pts, False, 255, thick, line_type)
        if use_row:
            cv2.compare(self.ids, 0, cv2.CMP_GT, dst=self.mask)

        # colours of the picture itself (boosted) for the "image" mode
        if "image" in used:
            ycc = cv2.cvtColor(self.small3, cv2.COLOR_BGR2YCrCb)
            ycc[:, :, 0] = self.lut_y[ycc[:, :, 0]]
            ycc[:, :, 1] = self.lut_c[ycc[:, :, 1]]
            ycc[:, :, 2] = self.lut_c[ycc[:, :, 2]]
            cv2.cvtColor(ycc, cv2.COLOR_YCrCb2BGR, dst=self.col_img)
        # line colours: the modes that give a colour field (blended by their weights) ...
        idx = (tone * 255.0).astype(np.uint8)
        field = [(m, w) for m, w in parts if not (m == "image" and use_row)]
        row_w = sum(w for m, w in parts if m == "image") if use_row else 0.0
        cv2.merge([self.mask, self.mask, self.mask], dst=self.mask3)
        if field:
            acc = field[0][1]
            self._field(field[0][0], idx, self.col_q)
            for mode, w in field[1:]:
                self._field(mode, idx, self.col_tmp)
                tot = acc + w
                cv2.addWeighted(self.col_q, acc / tot, self.col_tmp, w / tot, 0, dst=self.col_q)
                acc = tot
            cv2.resize(self.col_q, (W, H), dst=self.col_full, interpolation=cv2.INTER_LINEAR)
            cv2.multiply(self.col_full, self.mask3, dst=self.lines, scale=1.0 / 255.0)
        # ... and the row mode (each line carries the colours of its own picture row)
        if row_w > 0:
            self._row_lines()
            if field:
                total_w = sum(w for _m, w in field) + row_w
                cv2.addWeighted(self.lines, (total_w - row_w) / total_w, self.lines_row, row_w / total_w, 0, dst=self.lines)
            else:
                np.copyto(self.lines, self.lines_row)
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
