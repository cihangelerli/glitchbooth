"""Signal Drift effect.

Moved from server.py unchanged (function and class bodies identical, names unchanged).
No thread, file access or log line when this module is imported; it only creates its
buffers and tables.
"""

import numpy as np

from core.scale import n
from state import MUTABLE_PROCESSING_BUFFER
from tuning import (
    DRIFT_AMOUNT_SCALE,
    DRIFT_MAX_AMOUNT,
    DRIFT_MIN_AMOUNT,
    DRIFT_MUTATION_ENABLED,
    DRIFT_MUTATION_FULL_AT,
    DRIFT_MUTATION_MAX,
    DRIFT_ROLL_ABOVE,
    DRIFT_ROLL_MAX_FRACTION,
    DRIFT_SEAM_FRACTION,
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

STATIC_V_BUFFER = None


# NOISE MUTATION
# =========================================================================
# A table of random directions is made once at startup (a new pattern each start). Every
# frame only READS it at a new point in time, so the noise changes smoothly, never jumps.
# The table repeats seamlessly after 256 noise cells (about 17 min at the default shape speed).
_NOISE_N = 256
_ANGLES = np.random.default_rng().uniform(0.0, 2.0 * np.pi, (_NOISE_N, _NOISE_N))
_GRAD_X = np.cos(_ANGLES)
_GRAD_Y = np.sin(_ANGLES)


def _fade(t):
    return t * t * t * (t * (t * 6.0 - 15.0) + 10.0)


def _noise2(x, y):
    # Smooth 2-D noise, about -1 to 1. x = array of positions along the rows, y = one number (time).
    xi = np.floor(x)
    yi = int(np.floor(y))
    xf = x - xi
    yf = y - yi
    xi = xi.astype(np.int64)
    x0, x1 = xi & (_NOISE_N - 1), (xi + 1) & (_NOISE_N - 1)
    y0, y1 = yi & (_NOISE_N - 1), (yi + 1) & (_NOISE_N - 1)
    n00 = _GRAD_X[y0, x0] * xf + _GRAD_Y[y0, x0] * yf
    n10 = _GRAD_X[y0, x1] * (xf - 1.0) + _GRAD_Y[y0, x1] * yf
    n01 = _GRAD_X[y1, x0] * xf + _GRAD_Y[y1, x0] * (yf - 1.0)
    n11 = _GRAD_X[y1, x1] * (xf - 1.0) + _GRAD_Y[y1, x1] * (yf - 1.0)
    u, v = _fade(xf), _fade(yf)
    top = n00 + u * (n10 - n00)
    bottom = n01 + u * (n11 - n01)
    return np.clip((top + v * (bottom - top)) * 1.6, -1.0, 1.0)


def _drift_waves(h, drift, current_time):
    # Wave value (-1 to 1) of every picture row: today's sine wave, blended with the
    # noise bends. The mix per row comes from a second, slower noise (calm and wild zones).
    rows = np.arange(h, dtype=np.float64)
    wave = np.sin(rows * DRIFT_WAVE_PER_ROW + current_time * DRIFT_WAVE_SPEED)
    grow = min(1.0, drift / DRIFT_MUTATION_FULL_AT)
    if not DRIFT_MUTATION_ENABLED or grow <= 0.0 or DRIFT_MUTATION_MAX <= 0.0:
        return wave
    shape = DRIFT_SHAPE_AMOUNT * _noise2(
        rows / DRIFT_SHAPE_ROWS, current_time * DRIFT_SHAPE_SPEED
    )
    zone = 0.5 + 0.5 * _noise2(
        rows / DRIFT_STRENGTH_ROWS + 50.0, current_time * DRIFT_STRENGTH_SPEED + 30.0
    )
    zone = np.clip((zone - 0.5) * (1.0 + 2.2 * DRIFT_STRENGTH_CONTRAST) + 0.5, 0.0, 1.0)
    mix = np.clip(zone * DRIFT_MUTATION_MAX * grow, 0.0, 1.0)
    return np.clip((1.0 - mix) * wave + mix * shape, -1.0, 1.0)


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
    # The sideways shift is int(sin * amount), which is 0 for an amount below 2; the vertical
    # roll near the top of the knob acts whatever the amount is.
    drift = n(raw)
    if drift > DRIFT_ROLL_ABOVE:
        return True
    return int(drift * DRIFT_MAX_AMOUNT * DRIFT_AMOUNT_SCALE) >= DRIFT_MIN_AMOUNT
