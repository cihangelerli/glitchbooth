"""Slice glitch effect.

Moved from server.py unchanged (function and class bodies identical, names unchanged).
No thread, file access or log line when this module is imported; it only creates its
buffers and tables.
"""

import numpy as np

from core.scale import n
from state import MUTABLE_PROCESSING_BUFFER
from tuning import (
    FRAME_H,
    FRAME_W,
    SLICE_ACTIVE_FROM_KNOB,
    SLICE_CLOCK_HZ,
    SLICE_COUNT_CURVE,
    SLICE_COUNT_MAX,
    SLICE_EVERY_N_STEPS,
    SLICE_MAX_H,
    SLICE_MAX_SHIFT,
    SLICE_MIN_H,
)


SLICE_SHIFT_BUFFER = np.empty((FRAME_H, FRAME_W, 3), dtype=np.uint8, order="C")


def apply_slice_glitch(raw_knob, current_time):
    if not _acts_slice_glitch(raw_knob):
        return
    if int(current_time * SLICE_CLOCK_HZ) % SLICE_EVERY_N_STEPS != 0:
        return
    val = n(raw_knob)
    h, w = FRAME_H, FRAME_W
    # Gives a smoother curve where low values yield slices much sooner; the first slice
    # appears as soon as the effect acts (SLICE_ACTIVE_FROM_KNOB)
    num_slices = max(1, int((val**SLICE_COUNT_CURVE) * SLICE_COUNT_MAX))

    for _ in range(int(num_slices)):
        y, height, shift = (
            np.random.randint(0, h - SLICE_MAX_H),
            np.random.randint(SLICE_MIN_H, SLICE_MAX_H),
            np.random.randint(-SLICE_MAX_SHIFT, SLICE_MAX_SHIFT),
        )
        if shift:
            view = MUTABLE_PROCESSING_BUFFER[y : y + height]
            s = abs(shift) % w
            if s:
                scratch = SLICE_SHIFT_BUFFER[:height, :w]
                if shift > 0:
                    scratch[:, :s], scratch[:, s:] = view[:, -s:], view[:, :-s]
                else:
                    scratch[:, :-s], scratch[:, -s:] = view[:, s:], view[:, :s]
                view[:] = scratch


def _acts_slice_glitch(raw):
    return raw >= SLICE_ACTIVE_FROM_KNOB
