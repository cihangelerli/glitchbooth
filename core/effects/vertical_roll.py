"""Vertical Roll effect.

Moves the frame vertically when its assigned knob travel is active.
Deliberately independent from Signal Drift's horizontal row displacement.
"""

import numpy as np

from core.scale import n
from state import MUTABLE_PROCESSING_BUFFER
from tuning import (
    FRAME_H,
    FRAME_W,
    ROLL_ABOVE,
    ROLL_MAX_FRACTION,
    ROLL_SEAM_FRACTION,
)

# Sized for half the frame height (the maximum allowable roll fraction).
STATIC_V_BUFFER = np.zeros((FRAME_H // 2, FRAME_W, 3), dtype=np.uint8, order="C")


def apply_vertical_roll(raw_knob, current_time=None):
    if not _acts_vertical_roll(raw_knob):
        return

    roll = n(raw_knob)
    h, w = MUTABLE_PROCESSING_BUFFER.shape[:2]

    # Calculate shift amount, clamped to the buffer allocation (h // 2)
    max_shift = int(h * ROLL_MAX_FRACTION)
    v_shift = int(
        ((roll - ROLL_ABOVE) / max(1.0 - ROLL_ABOVE, 1e-6))
        * max_shift
    )
    v_shift = min(max(0, v_shift), h // 2)
    if v_shift <= 0:
        return

    # Preserve top rows, shift upward, and wrap to bottom
    STATIC_V_BUFFER[:v_shift] = MUTABLE_PROCESSING_BUFFER[:v_shift]
    MUTABLE_PROCESSING_BUFFER[:-v_shift] = MUTABLE_PROCESSING_BUFFER[v_shift:]
    MUTABLE_PROCESSING_BUFFER[-v_shift:] = STATIC_V_BUFFER[:v_shift]

    seam = h - v_shift
    thickness = int(h * ROLL_SEAM_FRACTION)
    if thickness > 0:
        MUTABLE_PROCESSING_BUFFER[
            max(0, seam - thickness // 2) : min(h, seam + thickness // 2)
        ] = 0


def _acts_vertical_roll(raw):
    return n(raw) > ROLL_ABOVE
