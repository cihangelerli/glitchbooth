"""Signal Drift effect.

Applies the horizontal, row-wise signal displacement only.
Vertical frame roll is handled independently by core.effects.vertical_roll.
"""

import numpy as np

from core import noise
from core.scale import n
from state import MUTABLE_PROCESSING_BUFFER
from tuning import (
    DRIFT_AMOUNT_SCALE,
    DRIFT_MAX_AMOUNT,
    DRIFT_MIN_AMOUNT,
    DRIFT_MUTATION_ENABLED,
    DRIFT_MUTATION_FULL_AT,
    DRIFT_MUTATION_MAX,
    DRIFT_SHAPE_AMOUNT,
    DRIFT_SHAPE_ROWS,
    DRIFT_SHAPE_SPEED,
    DRIFT_STRENGTH_CONTRAST,
    DRIFT_STRENGTH_ROWS,
    DRIFT_STRENGTH_SPEED,
    DRIFT_WAVE_PER_ROW,
    DRIFT_WAVE_SPEED,
    FRAME_W,
)

STATIC_ROW_BUFFER = np.empty((FRAME_W, 3), dtype=np.uint8, order="C")

# The noise comes from core/noise.py
_TABLE = noise.table_for("signal_drift")


def _drift_waves(h, drift, current_time):
    rows = np.arange(h, dtype=np.float64)
    wave = np.sin(rows * DRIFT_WAVE_PER_ROW + current_time * DRIFT_WAVE_SPEED)
    grow = min(1.0, drift / DRIFT_MUTATION_FULL_AT)
    if not noise.is_on(DRIFT_MUTATION_ENABLED) or grow <= 0.0 or DRIFT_MUTATION_MAX <= 0.0:
        return wave
    shape = DRIFT_SHAPE_AMOUNT * noise.lane(
        _TABLE, rows, current_time, DRIFT_SHAPE_ROWS, DRIFT_SHAPE_SPEED
    )
    zone = 0.5 + 0.5 * noise.lane(
        _TABLE, rows, current_time, DRIFT_STRENGTH_ROWS, DRIFT_STRENGTH_SPEED, 1
    )
    zone = np.clip((zone - 0.5) * (1.0 + 2.2 * DRIFT_STRENGTH_CONTRAST) + 0.5, 0.0, 1.0)
    mix = np.clip(zone * DRIFT_MUTATION_MAX * grow, 0.0, 1.0)
    return np.clip((1.0 - mix) * wave + mix * shape, -1.0, 1.0)


def apply_signal_drift(raw_knob, current_time):
    if not _acts_signal_drift(raw_knob):
        return
    drift = n(raw_knob)
    h, w = MUTABLE_PROCESSING_BUFFER.shape[:2]
    amount = int(drift * DRIFT_MAX_AMOUNT * DRIFT_AMOUNT_SCALE)
    shifts = (_drift_waves(h, drift, current_time) * amount).astype(np.int64)
    for i in range(h):
        shift = int(shifts[i])
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
    # Signal Drift acts only when the horizontal displacement can move at least 2 px.
    drift = n(raw)
    return int(drift * DRIFT_MAX_AMOUNT * DRIFT_AMOUNT_SCALE) >= DRIFT_MIN_AMOUNT
