"""Bitwise Databend effect (new).

The top bit planes of each colour are combined (XOR, OR or AND) with a sideways- and
vertically-shifted copy of the same picture, a different shift per colour. The result is hard,
electric colour patterns that follow the picture's own shapes (moire-like), as if the picture
data had been damaged by arithmetic. The knob raises both the number of bit planes and the shift.
With BITWISE_NOISE_ENABLED a smooth, moving noise decides WHERE the picture is bent (zones that
drift), so faces stay recognizable elsewhere.

Place it before Posterize: a gradient map turns the noise into coherent neon patterns.
"""

import cv2
import numpy as np

from core import noise
from core.scale import n
from state import MUTABLE_PROCESSING_BUFFER
from tuning import (
    BITWISE_ACTIVE_ABOVE,
    BITWISE_CHANNEL_SHIFTS,
    BITWISE_MAX_PLANES,
    BITWISE_MODE,
    BITWISE_NOISE_AMOUNT,
    BITWISE_NOISE_ENABLED,
    BITWISE_NOISE_SIZE_PX,
    BITWISE_NOISE_SPEED,
    DISABLED_EFFECTS,
    FRAME_H,
    FRAME_W,
)

_ZONE_STEP_PX = 32  # the noise zones are computed on a grid this coarse, then grown smoothly


class DatabendEngine:
    """Buffers (about 3 MiB, allocated once) and the per-frame work of Bitwise Databend."""

    def __init__(self):
        H, W = FRAME_H, FRAME_W
        self.rolled = np.zeros((H, W, 3), np.uint8)
        self.bits = np.zeros((H, W, 3), np.uint8)
        self.bent = np.zeros((H, W, 3), np.uint8)
        self.mask_q = np.zeros((H // 4, W // 4), np.uint8)
        self.mask = np.zeros((H, W), np.uint8)
        self.zone_x = np.arange(0, W, _ZONE_STEP_PX, dtype=np.float64)
        self.zone_y = np.arange(0, H, _ZONE_STEP_PX, dtype=np.float64)
        self.table = noise.table_for("bitwise_databend")

    def step(self, frame, k, t):
        H, W = FRAME_H, FRAME_W
        planes = int(min(max(round(1 + k * (BITWISE_MAX_PLANES - 1)), 1), 8))
        keep = (0xFF << (8 - planes)) & 0xFF  # the top `planes` bits
        self.bits[:] = keep
        for c in range(3):  # B, G, R: each colour reads its own shifted copy
            dx, dy = BITWISE_CHANNEL_SHIFTS[c]
            self.rolled[:, :, c] = np.roll(frame[:, :, c], (int(round(k * dy)), int(round(k * dx))), axis=(0, 1))
        if BITWISE_MODE == "or":
            cv2.bitwise_and(self.rolled, self.bits, dst=self.rolled)
            cv2.bitwise_or(frame, self.rolled, dst=self.bent)
        elif BITWISE_MODE == "and":
            cv2.bitwise_not(self.bits, dst=self.bits)  # AND only touches the top planes
            cv2.bitwise_or(self.rolled, self.bits, dst=self.rolled)
            cv2.bitwise_and(frame, self.rolled, dst=self.bent)
        else:  # "xor"
            cv2.bitwise_and(self.rolled, self.bits, dst=self.rolled)
            cv2.bitwise_xor(frame, self.rolled, dst=self.bent)
        if noise.is_on(BITWISE_NOISE_ENABLED) and BITWISE_NOISE_AMOUNT > 0:
            zone = noise.field2(self.table, self.zone_x, self.zone_y, t, BITWISE_NOISE_SIZE_PX, BITWISE_NOISE_SPEED)
            zone = np.clip(0.5 + 0.8 * zone, 0.0, 1.0).astype(np.float32)
            big = cv2.resize(zone, (W // 4, H // 4), interpolation=cv2.INTER_LINEAR)
            cut = 0.5 * min(BITWISE_NOISE_AMOUNT, 1.0)  # 0 = bend everywhere, 1 = only the wilder half
            self.mask_q[:] = (big >= cut) * 255
            cv2.resize(self.mask_q, (W, H), dst=self.mask, interpolation=cv2.INTER_NEAREST)
            cv2.copyTo(self.bent, self.mask, frame)
        else:
            np.copyto(frame, self.bent)


_BEND = DatabendEngine() if "bitwise_databend" not in DISABLED_EFFECTS else None


def apply_bitwise_databend(raw_knob, current_time):
    # Bitwise Databend entry point. raw_knob 0-1023. Modifies MUTABLE_PROCESSING_BUFFER in place.
    if not _acts_bitwise_databend(raw_knob):
        return
    _BEND.step(MUTABLE_PROCESSING_BUFFER, n(raw_knob), current_time)


def _acts_bitwise_databend(raw):
    return n(raw) > BITWISE_ACTIVE_ABOVE
