"""Posterize effect.

Moved from server.py unchanged (function and class bodies identical, names unchanged).
No thread, file access or log line when this module is imported; it only creates its
buffers and tables.
"""

import logging

import cv2
import numpy as np

from core import noise as smooth_noise  # not "noise": step() has a local variable of that name
from core.palettes import _posterize_palette, _ramp_luts
from state import MUTABLE_PROCESSING_BUFFER
from tuning import (
    DISABLED_EFFECTS,
    FRAME_H,
    FRAME_W,
    POSTERIZE_ASCII_CLARITY,
    POSTERIZE_ASCII_CLARITY_DROPOUT,
    POSTERIZE_ASCII_OUTLINE_STYLE,
    POSTERIZE_BANDS_HIGH,
    POSTERIZE_BANDS_LOW,
    POSTERIZE_BLACK_RAMP,
    POSTERIZE_BLACK_RAMP_MIX,
    POSTERIZE_BLACK_THRESHOLD,
    POSTERIZE_COLOR_MODE,
    POSTERIZE_CURVE,
    POSTERIZE_DETAIL_HIGH,
    POSTERIZE_DETAIL_LOW,
    POSTERIZE_DROPOUT_HIGH,
    POSTERIZE_DROPOUT_LOW,
    POSTERIZE_EDGE_GLOW,
    POSTERIZE_FLICKER_COLOUR,
    POSTERIZE_FLICKER_ENABLED,
    POSTERIZE_FLICKER_PROB,
    POSTERIZE_FRAGMENT_AMOUNT,
    POSTERIZE_FRAGMENT_FROM,
    POSTERIZE_GLOW_CLEAN,
    POSTERIZE_GLOW_COLOR,
    POSTERIZE_GLOW_CORE,
    POSTERIZE_GLOW_CORE_LIFT,
    POSTERIZE_GLOW_DIM,
    POSTERIZE_GLOW_FROM,
    POSTERIZE_GLOW_GAIN,
    POSTERIZE_GLOW_KEEP_BLACKS,
    POSTERIZE_GLOW_RADIUS_PX,
    POSTERIZE_GRADIENT_STOPS,
    POSTERIZE_HALFTONE,
    POSTERIZE_HALFTONE_ALTERNATE,
    POSTERIZE_HALFTONE_ANGLE,
    POSTERIZE_HALFTONE_COVER_GAIN,
    POSTERIZE_HALFTONE_FROM,
    POSTERIZE_HALFTONE_GAP,
    POSTERIZE_HALFTONE_PITCH,
    POSTERIZE_HALFTONE_SOFTNESS,
    POSTERIZE_HALFTONE_TONE_FROM,
    POSTERIZE_HALFTONE_TONE_TO,
    POSTERIZE_JITTER_MAX_PX,
    POSTERIZE_KNOB_MODE_HYSTERESIS,
    POSTERIZE_KNOB_MODES,
    POSTERIZE_MIX_FULL_AT,
    POSTERIZE_MIX_START,
    POSTERIZE_MODE_FADE_SECONDS,
    POSTERIZE_MODE_SCHEDULE,
    POSTERIZE_MODE_SOURCE,
    POSTERIZE_NOISE_AMOUNT,
    POSTERIZE_NOISE_ENABLED,
    POSTERIZE_NOISE_SIZE_PX,
    POSTERIZE_NOISE_SPEED,
    POSTERIZE_OUTLINE_MAX_FROM,
    POSTERIZE_OUTLINE_MAX_PX,
    POSTERIZE_OUTLINE_MID_FROM,
    POSTERIZE_OUTLINE_MID_PX,
    POSTERIZE_OUTLINE_MIN_PX,
    POSTERIZE_OUTLINE_STYLE,
    POSTERIZE_OVERLAY_MIX,
    POSTERIZE_ROTATE_SECONDS,
    POSTERIZE_SECTION_PX,
    POSTERIZE_SOLARIZE,
    POSTERIZE_SOLARIZE_POINT,
    POSTERIZE_SOLARIZE_STYLE,
    POSTERIZE_THERMAL_STOPS,
)

logger = logging.getLogger("glitchbooth.core.effects.posterize")


# =========================================================================
# POSTERIZE: NEON TONE BANDS WITH GLITCHY OUTLINES
# =========================================================================
# Colour effect: it does not move pixels. Blacks stay untouched; every other tone becomes a
# neon colour of the Melt palette (dark tones get the deep hues, bright tones the bright ones);
# the borders between tone bands get a 1-3 px outline that is broken up, can be shifted sideways (jitter, off at 0) and flashes,
# so it looks like damaged video, not like pop art.
#
# Per frame it makes a fixed number of large OpenCV calls (about 35) in preallocated buffers,
# no Python loop over pixels or rows. The tone map and the neon fill are computed at half
# resolution (chunky, and four times cheaper); the outlines are drawn at full resolution.


class PosterizeEngine:
    """Buffers (about 14 MiB, allocated once) and the per-frame work of Posterize."""

    def __init__(self):
        H, W = FRAME_H, FRAME_W
        hh, hw = H // 2, W // 2
        sp = POSTERIZE_SECTION_PX
        self.rng = np.random.default_rng()
        z = lambda h=H, w=W: np.zeros((h, w), dtype=np.uint8, order="C")
        z3 = lambda h=H, w=W: np.zeros((h, w, 3), dtype=np.uint8, order="C")
        self.gray, self.black, self.nonblack, self.ramp = z(), z(), z(), z()
        self.band_full, self.edge, self.edge_tmp = z(), z(), z()
        self.drop_full, self.flick_full, self.wmask = z(), z(), z()
        self.g_half, self.band_half, self.scale_half = z(hh, hw), z(hh, hw), z(hh, hw)
        self.pl_b, self.pl_g, self.pl_r = z(hh, hw), z(hh, hw), z(hh, hw)
        self.neon_half, self.scale3 = z3(hh, hw), z3(hh, hw)
        self.neon_full, self.poster, self.avg, self.edge_col = z3(), z3(), z3(), z3()
        self.white = np.full((H, W, 3), 255, dtype=np.uint8)
        self.ink = z3()
        self.noise_bank = self.rng.integers(0, 256, (4, hh, hw), dtype=np.uint8)
        self.xs = np.tile(np.arange(W, dtype=np.float32), (H, 1))
        self.mapx = self.xs.copy()
        self.mapy = np.tile(np.arange(H, dtype=np.float32)[:, None], (1, W))
        # One dilation kernel for each outline thickness the tunables can ask for.
        self.kernels = {
            px: np.ones((2 * px + 1, 2 * px + 1), dtype=np.uint8)
            for px in {
                POSTERIZE_OUTLINE_MIN_PX,
                POSTERIZE_OUTLINE_MID_PX,
                POSTERIZE_OUTLINE_MAX_PX,
            }
        }
        self.drop_small = z(H // sp, W // sp)
        self.flick_small = z(H // sp, W // sp)
        # Colour of each flash section (neon flashes). Only the sections that flash are written
        # each frame; the mask decides which of them are used, so stale colours never show.
        self.flick_col_small = z3(H // sp, W // sp)
        self.flick_col_full = z3()
        self.palette = _posterize_palette()
        self.knob_zones = self._parse_knob_modes()
        self._mode_x = (
            None  # last knob position the mode selection followed (hysteresis)
        )
        self.curve_x = np.array([p[0] for p in POSTERIZE_CURVE], dtype=np.float64)
        self.curve_y = np.array([p[1] for p in POSTERIZE_CURVE], dtype=np.float64)
        self._lut_key = None
        self.band_lut = np.zeros((256, 1), np.uint8)
        self.color_lut = np.zeros((3, 256, 1), np.uint8)
        # optional looks (about 5 MiB more, allocated once)
        self.grad_lut = np.zeros((3, 256, 1), np.uint8)
        self.therm_lut = np.zeros((3, 256, 1), np.uint8)
        self.neon_half2 = z3(hh, hw)
        self.solar_lut = np.zeros((256, 1), np.uint8)
        self.solar_lut_clear = np.zeros((256, 1), np.uint8)  # the fold while ASCII acts
        self._clear_key = None
        self._ht_key = None
        self.ht_tile = z()
        self.ht_cover, self.ht_mask, self.ht_mask3 = z(), z(), z3()
        self.ht_cover_lut = np.zeros((256, 1), np.uint8)
        self.ht_small = z(2, 2)
        self.ht_gain = 1.0
        self.ht_sel_lut = np.zeros((256, 1), np.uint8)
        self.ht_sel_half, self.ht_sel = z(hh, hw), z()
        self.gl_small = z(H // 4, W // 4)
        self.gl_med, self.gl_edge, self.gl_tmp = z(hh, hw), z(hh, hw), z(hh, hw)
        self.k3 = np.ones((3, 3), dtype=np.uint8)
        self.gl_full, self.gl_full3, self.glow_col = z(), z3(), z3()
        self.glow_solid, self.glow_core_solid = z3(), z3()
        self._glow_key = None
        # noise (POSTERIZE_NOISE_ENABLED): a coarse field grown to half resolution every frame
        self.noise_table = smooth_noise.table_for("posterize")
        self.flow_x = np.arange(0, hw, 8, dtype=np.float64)
        self.flow_y = np.arange(0, hh, 8, dtype=np.float64)
        self.flow_pos, self.flow_neg = z(hh, hw), z(hh, hw)

    def _tone_flow(self, current_time, s):
        # Smooth noise moves the tone borders: grey levels are added to / taken from the tones.
        field = smooth_noise.field2(
            self.noise_table,
            self.flow_x,
            self.flow_y,
            current_time,
            POSTERIZE_NOISE_SIZE_PX,
            POSTERIZE_NOISE_SPEED,
        )
        off = field * (POSTERIZE_NOISE_AMOUNT * s)
        hh, hw = self.g_half.shape
        up = np.clip(off, 0, 255).astype(np.uint8)
        down = np.clip(-off, 0, 255).astype(np.uint8)
        cv2.resize(up, (hw, hh), dst=self.flow_pos, interpolation=cv2.INTER_LINEAR)
        cv2.resize(down, (hw, hh), dst=self.flow_neg, interpolation=cv2.INTER_LINEAR)
        cv2.add(self.g_half, self.flow_pos, dst=self.g_half)
        cv2.subtract(self.g_half, self.flow_neg, dst=self.g_half)

    def strength(self, raw_knob):
        """Knob value 0-1023 -> strength 0-1 through the response curve (0 = off)."""
        return float(np.interp(raw_knob, self.curve_x, self.curve_y))

    def _luts(self, bands, rotation):
        key = (
            bands,
            rotation,
            POSTERIZE_GRADIENT_STOPS,
            POSTERIZE_THERMAL_STOPS,
            POSTERIZE_SOLARIZE_STYLE,
            POSTERIZE_SOLARIZE_POINT,
        )
        if key != self._lut_key:
            self.band_lut[:, 0] = (np.arange(256) * bands // 256).astype(np.uint8)
            n_pal = len(self.palette)
            self.color_lut[:] = 0
            for b in range(bands):
                idx = (
                    int(round(b * (n_pal - 1) / max(1, bands - 1))) + rotation
                ) % n_pal
                for ch in range(3):
                    self.color_lut[ch, b, 0] = self.palette[idx, ch]
            rolled = np.roll(self.palette, -rotation, axis=0)
            k = int(min(max(2, POSTERIZE_GRADIENT_STOPS), n_pal))
            pick = np.round(np.linspace(0, n_pal - 1, k)).astype(int)
            self.grad_lut[:] = _ramp_luts(np.linspace(0, 255, k), rolled[pick])
            pos = [p for p, _ in POSTERIZE_THERMAL_STOPS]
            col = [c for _, c in POSTERIZE_THERMAL_STOPS]
            self.therm_lut[:] = _ramp_luts(pos, col)
            v = np.arange(256, dtype=np.float32)
            t = float(min(max(POSTERIZE_SOLARIZE_POINT, 16), 239))
            if POSTERIZE_SOLARIZE_STYLE == "invert":
                sol = np.where(v < t, v, 255.0 - v)
            else:
                sol = np.where(v < t, v * 255.0 / t, (255.0 - v) * 255.0 / (255.0 - t))
            self.solar_lut[:, 0] = np.clip(sol, 0, 255).astype(np.uint8)
            self._lut_key = key

    @staticmethod
    def _mode_name_ok(name):
        parts = [p.strip() for p in str(name).split("+")]
        return 1 <= len(parts) <= 2 and all(
            p in ("bands", "gradient", "thermal") for p in parts
        )

    def _parse_knob_modes(self):
        """POSTERIZE_KNOB_MODES -> [(low, high, mode), ...], or None when the table is not usable
        (a WARNING says why; the colour mode is then POSTERIZE_COLOR_MODE)."""
        for name in [POSTERIZE_COLOR_MODE] + [m for m, _d in POSTERIZE_MODE_SCHEDULE]:
            if not self._mode_name_ok(name):
                logger.warning(
                    "[CONFIG] Posterize: %r is not a colour mode (bands, gradient, thermal, or two joined by +); it is drawn as 'bands'",
                    name,
                )
        if POSTERIZE_MODE_SOURCE not in ("knob", "schedule", "fixed"):
            logger.warning(
                "[CONFIG] POSTERIZE_MODE_SOURCE=%r must be 'knob', 'schedule' or 'fixed'; 'fixed' is used",
                POSTERIZE_MODE_SOURCE,
            )
        if POSTERIZE_MODE_SOURCE != "knob":
            return None
        zones = []
        try:
            for entry in POSTERIZE_KNOB_MODES:
                low, high, mode = float(entry[0]), float(entry[1]), entry[2]
                if not (0.0 <= low < high <= 1.0):
                    raise ValueError(
                        f"{entry!r}: needs 0 <= lower edge < upper edge <= 1"
                    )
                if not self._mode_name_ok(mode):
                    raise ValueError(f"{entry!r}: unknown mode {mode!r}")
                zones.append((low, high, mode))
            if not zones:
                raise ValueError("the table is empty")
            if min(z[0] for z in zones) > 0.0 or max(z[1] for z in zones) < 1.0:
                logger.warning(
                    "[CONFIG] POSTERIZE_KNOB_MODES does not cover the whole knob (0 to 1); the nearest zone is used outside it"
                )
        except Exception as exc:
            logger.warning(
                "[CONFIG] POSTERIZE_KNOB_MODES is not usable (%s); POSTERIZE_COLOR_MODE %r is used",
                exc,
                POSTERIZE_COLOR_MODE,
            )
            return None
        return zones

    @staticmethod
    def _add_mode(out, name, weight):
        """Adds `name` (a mode, or "a+b" = two modes shown mixed) with `weight` to out."""
        if weight <= 0:
            return
        parts = [p.strip() for p in name.split("+")][:2]
        if len(parts) == 2:
            m = min(max(POSTERIZE_OVERLAY_MIX, 0.0), 1.0)
            shares = [(parts[0], 1.0 - m), (parts[1], m)]
        else:
            shares = [(parts[0], 1.0)]
        for p, sh in shares:
            if sh > 0:
                out[p] = out.get(p, 0.0) + weight * sh

    def _knob_weights(self, x):
        """Knob position x (0-1) -> {mode: weight}. One zone = that mode. Overlapping zones are
        blended: a zone's weight is how far x is from the nearest edge of that zone."""
        hyst = POSTERIZE_KNOB_MODE_HYSTERESIS
        if self._mode_x is not None and abs(x - self._mode_x) < hyst:
            x = self._mode_x
        else:
            self._mode_x = x
        inside = [z for z in self.knob_zones if z[0] <= x <= z[1]]
        out = {}
        if not inside:  # a gap or outside the table: the nearest edge decides
            inside = [
                min(self.knob_zones, key=lambda z: min(abs(x - z[0]), abs(x - z[1])))
            ]
            self._add_mode(out, inside[0][2], 1.0)
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
        return list(out.items()) or [("bands", 1.0)]

    def _mode_weights(self, t, knob_x=0.0):
        """-> [(mode, weight), ...]: what to show now (knob zones, schedule, cross-fade, a+b overlays)."""
        if self.knob_zones is not None:
            return self._knob_weights(knob_x)
        sched = POSTERIZE_MODE_SCHEDULE if POSTERIZE_MODE_SOURCE == "schedule" else ()
        if not sched:
            cur, nxt, w = POSTERIZE_COLOR_MODE, None, 0.0
        else:
            durs = [max(0.001, float(d)) for _, d in sched]
            pos = t % sum(durs)
            i = 0
            while i < len(durs) - 1 and pos >= durs[i]:
                pos -= durs[i]
                i += 1
            cur, nxt, w = sched[i][0], None, 0.0
            left = durs[i] - pos
            fade = POSTERIZE_MODE_FADE_SECONDS
            if fade > 0 and len(sched) > 1 and left < fade:
                nxt = sched[(i + 1) % len(sched)][0]
                w = 1.0 - left / fade
        out = {}
        self._add_mode(out, cur, 1.0 - w)
        if nxt is not None:
            self._add_mode(out, nxt, w)
        return list(out.items()) or [("bands", 1.0)]

    def _fill(self, mode, dst, detail):
        """Neon fill (half resolution) of one colour mode into dst."""
        if mode == "gradient":
            src, lut = self.g_half, self.grad_lut
        elif mode == "thermal":
            src, lut = self.g_half, self.therm_lut
        else:  # "bands"
            src, lut = self.band_half, self.color_lut
        cv2.LUT(src, lut[0], dst=self.pl_b)
        cv2.LUT(src, lut[1], dst=self.pl_g)
        cv2.LUT(src, lut[2], dst=self.pl_r)
        cv2.merge([self.pl_b, self.pl_g, self.pl_r], dst=dst)
        if src is self.band_half:  # bands keep the brightness modulation
            cv2.convertScaleAbs(
                self.g_half,
                dst=self.scale_half,
                alpha=detail,
                beta=255.0 * (1.0 - detail),
            )
            cv2.merge(
                [self.scale_half, self.scale_half, self.scale_half], dst=self.scale3
            )
            cv2.multiply(dst, self.scale3, dst=dst, scale=1.0 / 255.0)

    def _build_halftone(self):
        """Dot-screen threshold tile (static) + tone curve; rebuilt only when changed."""
        H, W = FRAME_H, FRAME_W
        pitch = max(4, int(POSTERIZE_HALFTONE_PITCH))
        key = (
            pitch,
            POSTERIZE_HALFTONE_ANGLE,
            POSTERIZE_HALFTONE_SOFTNESS,
            POSTERIZE_HALFTONE_COVER_GAIN,
        )
        if key == self._ht_key:
            return
        yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
        a = np.deg2rad(POSTERIZE_HALFTONE_ANGLE)
        u = (xx * np.cos(a) + yy * np.sin(a)) / pitch
        v = (-xx * np.sin(a) + yy * np.cos(a)) / pitch
        d = np.sqrt((u - np.floor(u) - 0.5) ** 2 + (v - np.floor(v) - 0.5) ** 2)
        d /= 0.7071  # 0 = dot centre, 1 = cell corner
        self.ht_tile[:] = np.clip(d * 235.0, 0, 255).astype(np.uint8)
        t = np.arange(256, dtype=np.float32) / 255.0
        # sqrt: dot AREA follows brightness; a gain above 1 lets the highlights close up fully
        self.ht_cover_lut[:, 0] = (
            255.0 * np.sqrt(np.clip(t * POSTERIZE_HALFTONE_COVER_GAIN, 0, 1))
        ).astype(np.uint8)
        self.ht_small = np.zeros((max(2, H // pitch), max(2, W // pitch)), np.uint8)
        # ramp width (grey levels) = softness in px * levels per px
        self.ht_gain = (
            255.0 * pitch * 0.7071 / (235.0 * max(0.3, POSTERIZE_HALFTONE_SOFTNESS))
        )
        self._ht_key = key

    def _halftone(self, bands):
        """Turns self.neon_full into a dot screen (coloured dots, dark gaps)."""
        H, W = FRAME_H, FRAME_W
        self._build_halftone()
        sh, sw = self.ht_small.shape
        cv2.resize(
            self.g_half, (sw, sh), dst=self.ht_small, interpolation=cv2.INTER_AREA
        )
        cv2.resize(
            self.ht_small, (W, H), dst=self.ht_cover, interpolation=cv2.INTER_LINEAR
        )
        cv2.LUT(self.ht_cover, self.ht_cover_lut, dst=self.ht_cover)
        cv2.subtract(self.ht_cover, self.ht_tile, dst=self.ht_mask)
        cv2.convertScaleAbs(
            self.ht_mask,
            dst=self.ht_mask,
            alpha=self.ht_gain,
            beta=255.0 * POSTERIZE_HALFTONE_GAP,
        )
        if (
            POSTERIZE_HALFTONE_TONE_FROM > 0.0
            or POSTERIZE_HALFTONE_TONE_TO < 1.0
            or POSTERIZE_HALFTONE_ALTERNATE
        ):  # only some bands get dots; the others are forced to full (solid) fill
            lut = self.ht_sel_lut
            lut[:] = 0
            for b in range(bands):
                pos = b / max(1, bands - 1)
                if (
                    POSTERIZE_HALFTONE_TONE_FROM <= pos <= POSTERIZE_HALFTONE_TONE_TO
                    and not (POSTERIZE_HALFTONE_ALTERNATE and b % 2)
                ):
                    lut[b, 0] = 255
            cv2.LUT(self.band_half, lut, dst=self.ht_sel_half)
            cv2.resize(
                self.ht_sel_half,
                (W, H),
                dst=self.ht_sel,
                interpolation=cv2.INTER_NEAREST,
            )
            cv2.bitwise_not(self.ht_sel, dst=self.ht_sel)
            cv2.bitwise_or(self.ht_mask, self.ht_sel, dst=self.ht_mask)
        cv2.merge([self.ht_mask, self.ht_mask, self.ht_mask], dst=self.ht_mask3)
        cv2.multiply(
            self.neon_full, self.ht_mask3, dst=self.neon_full, scale=1.0 / 255.0
        )

    def _edge_glow(self, px):
        """Halo (+ neon core) from the final outline mask in self.edge_tmp."""
        H, W = FRAME_H, FRAME_W
        palette_mode = POSTERIZE_GLOW_COLOR == "palette"
        key = (POSTERIZE_GLOW_COLOR, POSTERIZE_GLOW_CORE_LIFT)
        if not palette_mode and key != self._glow_key:
            col = np.array(POSTERIZE_GLOW_COLOR, dtype=np.float32)
            lift = POSTERIZE_GLOW_CORE_LIFT
            self.glow_solid[:] = col.astype(np.uint8)
            self.glow_core_solid[:] = (col * (1.0 - lift) + 255.0 * lift).astype(
                np.uint8
            )  # lighter core
            self._glow_key = key
        # halo source = only the BIG tone borders (speckle removed), found at half resolution,
        # so busy high-strength outlines do not flood the picture with light
        cv2.medianBlur(self.band_half, POSTERIZE_GLOW_CLEAN, dst=self.gl_med)
        cv2.dilate(self.gl_med, self.k3, dst=self.gl_tmp)
        cv2.compare(self.gl_tmp, self.gl_med, cv2.CMP_NE, dst=self.gl_edge)
        # halo at quarter resolution: shrink, blur, boost, grow
        cv2.resize(
            self.gl_edge,
            (W // 4, H // 4),
            dst=self.gl_small,
            interpolation=cv2.INTER_AREA,
        )
        cv2.GaussianBlur(
            self.gl_small,
            (0, 0),
            max(0.5, POSTERIZE_GLOW_RADIUS_PX / 4.0),
            dst=self.gl_small,
        )
        cv2.convertScaleAbs(self.gl_small, dst=self.gl_small, alpha=POSTERIZE_GLOW_GAIN)
        cv2.resize(
            self.gl_small, (W, H), dst=self.gl_full, interpolation=cv2.INTER_LINEAR
        )
        if POSTERIZE_GLOW_KEEP_BLACKS:
            cv2.bitwise_and(self.gl_full, self.nonblack, dst=self.gl_full)
        cv2.merge([self.gl_full, self.gl_full, self.gl_full], dst=self.gl_full3)
        if palette_mode:
            cv2.multiply(
                self.gl_full3, self.neon_full, dst=self.glow_col, scale=1.0 / 255.0
            )
        else:
            cv2.multiply(
                self.gl_full3, self.glow_solid, dst=self.glow_col, scale=1.0 / 255.0
            )
        cv2.addWeighted(
            self.poster, POSTERIZE_GLOW_DIM, self.glow_col, 1.0, 0, dst=self.poster
        )
        if POSTERIZE_GLOW_CORE:
            if palette_mode:
                cv2.dilate(self.neon_full, self.kernels[px], dst=self.edge_col)
                core = self.edge_col
            else:
                core = self.glow_core_solid
            cv2.copyTo(core, self.edge_tmp, self.poster)

    def step(self, frame, raw_knob, current_time, outline_style=None, clarity=0.0):
        # clarity: 0-1, how much of the picture's readability is protected (see
        # POSTERIZE_ASCII_CLARITY). apply_posterize passes it only while ASCII acts.
        s = self.strength(raw_knob)
        if s <= 0.0:
            return
        H, W = FRAME_H, FRAME_W
        sp = POSTERIZE_SECTION_PX
        bands = int(
            round(
                POSTERIZE_BANDS_LOW + (POSTERIZE_BANDS_HIGH - POSTERIZE_BANDS_LOW) * s
            )
        )

        clarity = min(max(float(clarity), 0.0), 1.0)
        # Brightness detail of the "bands" fill (the gradient and thermal fills do not use it):
        # from DETAIL_LOW at the weakest strength to DETAIL_HIGH at full strength, and under
        # clarity it falls only (1 - clarity) as far.
        detail = POSTERIZE_DETAIL_LOW + (
            (POSTERIZE_DETAIL_HIGH - POSTERIZE_DETAIL_LOW) * s * (1.0 - clarity)
        )

        mix = POSTERIZE_MIX_START + (1.0 - POSTERIZE_MIX_START) * min(
            1.0, s / POSTERIZE_MIX_FULL_AT
        )
        px = (
            POSTERIZE_OUTLINE_MAX_PX
            if s >= POSTERIZE_OUTLINE_MAX_FROM
            else (
                POSTERIZE_OUTLINE_MID_PX
                if s >= POSTERIZE_OUTLINE_MID_FROM
                else POSTERIZE_OUTLINE_MIN_PX
            )
        )
        dropout = (
            POSTERIZE_DROPOUT_LOW + (POSTERIZE_DROPOUT_HIGH - POSTERIZE_DROPOUT_LOW) * s
        )
        dropout += clarity * POSTERIZE_ASCII_CLARITY_DROPOUT * (1.0 - dropout)
        jitter = int(
            round(s * POSTERIZE_JITTER_MAX_PX)
        )  # 0 = the sideways shift is skipped
        if POSTERIZE_ROTATE_SECONDS > 0 and len(self.palette) > 0:
            rotation = int(current_time / POSTERIZE_ROTATE_SECONDS) % len(self.palette)
        else:
            rotation = 0
        self._luts(bands, rotation)

        # 1. tones at half resolution, with break-up noise at high strength
        cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY, dst=self.gray)
        cv2.resize(
            self.gray, (W // 2, H // 2), dst=self.g_half, interpolation=cv2.INTER_AREA
        )
        if s > POSTERIZE_FRAGMENT_FROM:
            amp = (
                (s - POSTERIZE_FRAGMENT_FROM)
                / (1.0 - POSTERIZE_FRAGMENT_FROM)
                * POSTERIZE_FRAGMENT_AMOUNT
            )
            noise = self.noise_bank[int(self.rng.integers(0, 4))]
            cv2.addWeighted(
                self.g_half, 1.0, noise, amp / 255.0, -amp / 2.0, dst=self.g_half
            )
        if smooth_noise.is_on(POSTERIZE_NOISE_ENABLED):  # the tone borders flow slowly
            self._tone_flow(current_time, s)
        if POSTERIZE_SOLARIZE:  # fold the tone curve before bands and colours
            lut = self.solar_lut
            if clarity > 0.0:
                # the turning point of the fold moves from POSTERIZE_SOLARIZE_POINT toward 239
                if self._clear_key != (clarity, self._lut_key):
                    t0 = float(min(max(POSTERIZE_SOLARIZE_POINT, 16), 239))
                    t = t0 + clarity * (239.0 - t0)
                    v = np.arange(256, dtype=np.float32)
                    if POSTERIZE_SOLARIZE_STYLE == "invert":
                        sol = np.where(v < t, v, 255.0 - v)
                    else:
                        sol = np.where(
                            v < t, v * 255.0 / t, (255.0 - v) * 255.0 / (255.0 - t)
                        )
                    self.solar_lut_clear[:, 0] = np.clip(sol, 0, 255).astype(np.uint8)
                    self._clear_key = (clarity, self._lut_key)
                lut = self.solar_lut_clear
            cv2.LUT(self.g_half, lut, dst=self.g_half)
        cv2.LUT(self.g_half, self.band_lut, dst=self.band_half)

        # 2. neon fill: band -> palette colour, modulated by the original brightness
        parts = self._mode_weights(current_time, raw_knob / 1023.0)
        self._fill(parts[0][0], self.neon_half, detail)
        acc = parts[0][1]
        for mode, w in parts[1:]:  # cross-fade / overlay: blend further fills in
            self._fill(mode, self.neon_half2, detail)
            tot = acc + w
            cv2.addWeighted(
                self.neon_half,
                acc / tot,
                self.neon_half2,
                w / tot,
                0,
                dst=self.neon_half,
            )
            acc = tot
        cv2.resize(
            self.neon_half, (W, H), dst=self.neon_full, interpolation=cv2.INTER_NEAREST
        )
        cv2.resize(
            self.band_half, (W, H), dst=self.band_full, interpolation=cv2.INTER_NEAREST
        )
        if POSTERIZE_HALFTONE and s >= POSTERIZE_HALFTONE_FROM:  # dot screen
            self._halftone(bands)

        # 3. mix with the original; blacks untouched, a narrow ramp mixes half and half
        cv2.addWeighted(frame, 1.0 - mix, self.neon_full, mix, 0, dst=self.poster)
        cv2.compare(self.gray, POSTERIZE_BLACK_THRESHOLD, cv2.CMP_LT, dst=self.black)
        cv2.compare(self.gray, POSTERIZE_BLACK_RAMP, cv2.CMP_LT, dst=self.ramp)
        cv2.bitwise_xor(self.ramp, self.black, dst=self.ramp)
        cv2.addWeighted(
            frame,
            1.0 - POSTERIZE_BLACK_RAMP_MIX,
            self.poster,
            POSTERIZE_BLACK_RAMP_MIX,
            0,
            dst=self.avg,
        )
        cv2.copyTo(self.avg, self.ramp, self.poster)
        cv2.copyTo(frame, self.black, self.poster)
        cv2.bitwise_not(self.black, dst=self.nonblack)

        # 4. outlines on the tone borders: broken sections, sideways jitter, flashing sections
        cv2.dilate(self.band_full, self.kernels[px], dst=self.edge_tmp)
        cv2.compare(self.edge_tmp, self.band_full, cv2.CMP_NE, dst=self.edge)
        self.drop_small[:] = (self.rng.random(self.drop_small.shape) >= dropout) * 255
        cv2.resize(
            self.drop_small, (W, H), dst=self.drop_full, interpolation=cv2.INTER_NEAREST
        )
        cv2.bitwise_and(self.edge, self.drop_full, dst=self.edge)
        if jitter > 0:
            offsets = np.repeat(
                self.rng.integers(-jitter, jitter + 1, H // 4), 4
            ).astype(np.float32)
            np.add(self.xs, offsets[:, None], out=self.mapx)
            cv2.remap(
                self.edge, self.mapx, self.mapy, cv2.INTER_NEAREST, dst=self.edge_tmp
            )
            cv2.bitwise_and(self.edge_tmp, self.nonblack, dst=self.edge_tmp)
        else:  # no sideways shift: the outline is the edge itself (same picture, no remap)
            cv2.bitwise_and(self.edge, self.nonblack, dst=self.edge_tmp)

        style = outline_style or POSTERIZE_OUTLINE_STYLE

        if style == "glow":
            cv2.dilate(self.neon_full, self.kernels[px], dst=self.edge_col)
            outline_colour = self.edge_col
        elif style == "black":
            outline_colour = self.ink
        else:
            outline_colour = self.white
        cv2.copyTo(outline_colour, self.edge_tmp, self.poster)
        if POSTERIZE_EDGE_GLOW and s >= POSTERIZE_GLOW_FROM:  # halo + neon core
            self._edge_glow(px)
        if POSTERIZE_FLICKER_ENABLED and POSTERIZE_FLICKER_PROB > 0:
            self.flick_small[:] = (
                self.rng.random(self.flick_small.shape) < POSTERIZE_FLICKER_PROB
            ) * 255
            cv2.resize(
                self.flick_small,
                (W, H),
                dst=self.flick_full,
                interpolation=cv2.INTER_NEAREST,
            )
            cv2.bitwise_and(self.edge_tmp, self.flick_full, dst=self.wmask)
            flash_colour = self.white
            if POSTERIZE_FLICKER_COLOUR == "neon":
                lit = np.flatnonzero(
                    self.flick_small
                )  # the sections that flash this frame
                if lit.size:
                    cells = self.flick_col_small.reshape(-1, 3)  # a view, not a copy
                    cells[lit] = self.palette[
                        self.rng.integers(0, len(self.palette), lit.size)
                    ]
                    cv2.resize(
                        self.flick_col_small,
                        (W, H),
                        dst=self.flick_col_full,
                        interpolation=cv2.INTER_NEAREST,
                    )
                    flash_colour = self.flick_col_full
            cv2.copyTo(flash_colour, self.wmask, self.poster)

        np.copyto(frame, self.poster)


_POSTER = PosterizeEngine() if "posterize" not in DISABLED_EFFECTS else None


def apply_posterize(raw_knob, current_time, ascii_active=False):
    """Posterize entry point. raw_knob 0-1023."""
    # While the ASCII effect is acting (on whichever knob it is attached to, in any processing
    # order) the outlines glow; otherwise they use POSTERIZE_OUTLINE_STYLE.
    outline_style = (
        POSTERIZE_ASCII_OUTLINE_STYLE if ascii_active else POSTERIZE_OUTLINE_STYLE
    )
    clarity = POSTERIZE_ASCII_CLARITY if ascii_active else 0.0
    _POSTER.step(
        MUTABLE_PROCESSING_BUFFER, raw_knob, current_time, outline_style, clarity
    )


def _acts_posterize(raw):
    # The first non-zero point of POSTERIZE_CURVE (the same curve the effect uses).
    xs = [p[0] for p in POSTERIZE_CURVE]
    ys = [p[1] for p in POSTERIZE_CURVE]
    return float(np.interp(raw, xs, ys)) > 0.0
