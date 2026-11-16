"""JPEG Crush effect (new).

The picture is squeezed through a real JPEG at a very low quality and decoded again: blocky,
banded, colour-bled, like a badly compressed web picture. Options: several passes with a small
shift between them (generational loss), bigger blocks (compress at half or quarter size), and a few
flipped bytes in the compressed data (real databending; if the damaged file cannot be decoded the
clean result is used). With JPEG_NOISE_ENABLED a smooth, moving noise decides WHERE the picture
is crushed (zones that drift); the rest stays clean.

Place it before Posterize: its halftone dots would turn to mush after a JPEG.
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
    JPEG_ACTIVE_ABOVE,
    JPEG_BLOCK_SCALE,
    JPEG_BYTE_FLIPS,
    JPEG_CHROMA_FACTOR,
    JPEG_CURVE,
    JPEG_NOISE_AMOUNT,
    JPEG_NOISE_ENABLED,
    JPEG_NOISE_SIZE_PX,
    JPEG_NOISE_SPEED,
    JPEG_PASS_SHIFT_PX,
    JPEG_PASSES,
    JPEG_QUALITY_END,
    JPEG_QUALITY_START,
)

_ZONE_STEP_PX = 32  # the noise zones are computed on a grid this coarse, then grown smoothly
_HEADER_BYTES = 700  # the start of a JPEG (tables) is never damaged
_CHROMA_PARAM = getattr(cv2, "IMWRITE_JPEG_CHROMA_QUALITY", None)
_LUMA_PARAM = getattr(cv2, "IMWRITE_JPEG_LUMA_QUALITY", None)


class CrushEngine:
    """Buffers (about 2 MiB, allocated once) and the per-frame work of JPEG Crush."""

    def __init__(self):
        H, W = FRAME_H, FRAME_W
        self.rng = np.random.default_rng()
        self.mask_q = np.zeros((H // 4, W // 4), np.uint8)
        self.mask = np.zeros((H, W), np.uint8)
        self.zone_x = np.arange(0, W, _ZONE_STEP_PX, dtype=np.float64)
        self.zone_y = np.arange(0, H, _ZONE_STEP_PX, dtype=np.float64)
        self.table = noise.table_for("jpeg_crush")

    def _params(self, quality):
        quality = int(min(max(quality, 1), 100))
        if _CHROMA_PARAM is not None and _LUMA_PARAM is not None:
            chroma = int(min(max(quality * JPEG_CHROMA_FACTOR, 1), 100))
            return [_LUMA_PARAM, quality, _CHROMA_PARAM, chroma]
        return [cv2.IMWRITE_JPEG_QUALITY, quality]

    def _crush(self, frame, k):
        H, W = FRAME_H, FRAME_W
        quality = JPEG_QUALITY_START + (JPEG_QUALITY_END - JPEG_QUALITY_START) * (k ** JPEG_CURVE)
        params = self._params(quality)
        scale = max(1, int(JPEG_BLOCK_SCALE))
        src = frame if scale == 1 else cv2.resize(frame, (W // scale, H // scale), interpolation=cv2.INTER_AREA)
        flips = int(round(k * JPEG_BYTE_FLIPS))
        clean = None
        passes = max(1, int(JPEG_PASSES))
        for p in range(passes):
            if p:  # a small shift between passes: the blocks of one pass do not line up with the last
                shift = int(JPEG_PASS_SHIFT_PX)
                src = np.roll(src, (shift, shift), axis=(0, 1))
            ok, enc = cv2.imencode(".jpg", src, params)
            if not ok:
                return None
            clean = cv2.imdecode(enc, cv2.IMREAD_COLOR)
            if clean is None:
                return None
            src = clean
        result = clean
        if flips > 0 and enc.size > _HEADER_BYTES + 2:  # damage the last pass, keep the clean one as a fallback
            data = enc.copy()
            pos = self.rng.integers(_HEADER_BYTES, data.size, flips)
            data[pos] = self.rng.integers(0, 256, flips, dtype=np.uint8)
            damaged = cv2.imdecode(data, cv2.IMREAD_COLOR)
            if damaged is not None and damaged.shape == clean.shape:
                result = damaged
        if scale > 1:
            result = cv2.resize(result, (W, H), interpolation=cv2.INTER_NEAREST)
        return result

    def step(self, frame, k, t):
        H, W = FRAME_H, FRAME_W
        try:
            crushed = self._crush(frame, k)
        except cv2.error:
            return  # never stop the booth for a bad frame: leave the picture as it is
        if crushed is None or crushed.shape != frame.shape:
            return
        if noise.is_on(JPEG_NOISE_ENABLED) and JPEG_NOISE_AMOUNT > 0:
            zone = noise.field2(self.table, self.zone_x, self.zone_y, t, JPEG_NOISE_SIZE_PX, JPEG_NOISE_SPEED)
            zone = np.clip(0.5 + 0.8 * zone, 0.0, 1.0).astype(np.float32)
            big = cv2.resize(zone, (W // 4, H // 4), interpolation=cv2.INTER_LINEAR)
            cut = 0.5 * min(JPEG_NOISE_AMOUNT, 1.0)  # 0 = crush everywhere, 1 = only the wilder half
            self.mask_q[:] = (big >= cut) * 255
            cv2.resize(self.mask_q, (W, H), dst=self.mask, interpolation=cv2.INTER_NEAREST)
            cv2.copyTo(crushed, self.mask, frame)
        else:
            np.copyto(frame, crushed)


_CRUSH = CrushEngine() if "jpeg_crush" not in DISABLED_EFFECTS else None


def apply_jpeg_crush(raw_knob, current_time):
    # JPEG Crush entry point. raw_knob 0-1023. Modifies MUTABLE_PROCESSING_BUFFER in place.
    if not _acts_jpeg_crush(raw_knob):
        return
    _CRUSH.step(MUTABLE_PROCESSING_BUFFER, n(raw_knob), current_time)


def _acts_jpeg_crush(raw):
    return n(raw) > JPEG_ACTIVE_ABOVE
