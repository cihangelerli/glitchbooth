"""Signal Drift effect.

Moved from server.py unchanged (function and class bodies identical, names unchanged).
No thread, file access or log line when this module is imported; it only creates its
buffers and tables.
"""

import math

import numpy as np

from core.scale import n
from state import MUTABLE_PROCESSING_BUFFER
from tuning import (
    DRIFT_AMOUNT_SCALE,
    DRIFT_MAX_AMOUNT,
    DRIFT_MIN_AMOUNT,
    DRIFT_ROLL_ABOVE,
    DRIFT_ROLL_MAX_FRACTION,
    DRIFT_SEAM_FRACTION,
    DRIFT_WAVE_PER_ROW,
    DRIFT_WAVE_SPEED,
    FRAME_W,
)


STATIC_ROW_BUFFER = np.empty((FRAME_W, 3), dtype=np.uint8, order="C")

STATIC_V_BUFFER = None


def apply_signal_drift(raw_knob, current_time):
    global STATIC_V_BUFFER
    if not _acts_signal_drift(raw_knob):
        return
    drift = n(raw_knob)
    h, w = MUTABLE_PROCESSING_BUFFER.shape[:2]
    if STATIC_V_BUFFER is None:
        # large enough for the longest roll the tunable allows (at most half the height)
        STATIC_V_BUFFER = np.zeros(
            (h // 2, w) + MUTABLE_PROCESSING_BUFFER.shape[2:],
            dtype=MUTABLE_PROCESSING_BUFFER.dtype,
        )
    if drift > DRIFT_ROLL_ABOVE:
        v_shift = int(
            ((drift - DRIFT_ROLL_ABOVE) / (1.0 - DRIFT_ROLL_ABOVE))
            * (h * DRIFT_ROLL_MAX_FRACTION)
        )
        if v_shift:
            STATIC_V_BUFFER[:v_shift] = MUTABLE_PROCESSING_BUFFER[:v_shift]
            MUTABLE_PROCESSING_BUFFER[:-v_shift] = MUTABLE_PROCESSING_BUFFER[v_shift:]
            MUTABLE_PROCESSING_BUFFER[-v_shift:] = STATIC_V_BUFFER[:v_shift]
            seam, thickness = h - v_shift, int(h * DRIFT_SEAM_FRACTION)
            MUTABLE_PROCESSING_BUFFER[
                max(0, seam - thickness // 2) : min(h, seam + thickness // 2)
            ] = 0
    amount = int(drift * DRIFT_MAX_AMOUNT * DRIFT_AMOUNT_SCALE)
    for i in range(h):
        shift = int(
            math.sin(i * DRIFT_WAVE_PER_ROW + current_time * DRIFT_WAVE_SPEED) * amount
        )
        if shift > 0:
            s = shift % w
            if s:
                row = MUTABLE_PROCESSING_BUFFER[i]
                STATIC_ROW_BUFFER[:s], STATIC_ROW_BUFFER[s:] = row[-s:], row[:-s]
                row[:] = STATIC_ROW_BUFFER
        elif shift < 0:
            s = (-shift) % w
            if s:
                row = MUTABLE_PROCESSING_BUFFER[i]
                STATIC_ROW_BUFFER[:-s], STATIC_ROW_BUFFER[-s:] = row[s:], row[:s]
                row[:] = STATIC_ROW_BUFFER


def _acts_signal_drift(raw):
    # The sideways shift is int(sin * amount), which is 0 for an amount below 2; the vertical
    # roll near the top of the knob acts whatever the amount is.
    drift = n(raw)
    if drift > DRIFT_ROLL_ABOVE:
        return True
    return int(drift * DRIFT_MAX_AMOUNT * DRIFT_AMOUNT_SCALE) >= DRIFT_MIN_AMOUNT
