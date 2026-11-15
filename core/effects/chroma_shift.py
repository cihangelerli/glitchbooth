"""Chroma Shift effect.

Moved from server.py unchanged (function and class bodies identical, names unchanged).
No thread, file access or log line when this module is imported; it only creates its
buffers and tables.
"""

import cv2
import numpy as np

from core.scale import n
from state import MUTABLE_PROCESSING_BUFFER
from tuning import (
    ANA_GHOST_OFFSET,
    ANA_SHIFT_MAX,
    CHROMA_ACTIVE_ABOVE,
    CHROMA_GHOST_MIX,
    FRAME_H,
    FRAME_W,
)


CHANNEL_SHIFT_BUFFER = np.empty((FRAME_H, FRAME_W), dtype=np.uint8, order="C")
GHOST_FRAME_BUFFER = np.empty((FRAME_H, FRAME_W, 3), dtype=np.uint8, order="C")


def apply_chroma_shift(raw_knob, current_time):
    if not _acts_chroma_shift(raw_knob):
        return
    ana_val = n(raw_knob)
    h, w = FRAME_H, FRAME_W
    shift = int(ana_val * ANA_SHIFT_MAX)
    red = MUTABLE_PROCESSING_BUFFER[:, :, 2]
    if shift > 0:
        s = shift % w
        if s:
            CHANNEL_SHIFT_BUFFER[:, :-s], CHANNEL_SHIFT_BUFFER[:, -s:] = (
                red[:, s:],
                red[:, :s],
            )
            red[:] = CHANNEL_SHIFT_BUFFER
    elif shift < 0:
        s = (-shift) % w
        if s:
            CHANNEL_SHIFT_BUFFER[:, :s], CHANNEL_SHIFT_BUFFER[:, s:] = (
                red[:, -s:],
                red[:, :-s],
            )
            red[:] = CHANNEL_SHIFT_BUFFER
    s_ghost = ANA_GHOST_OFFSET % w
    if s_ghost:
        GHOST_FRAME_BUFFER[:, :s_ghost], GHOST_FRAME_BUFFER[:, s_ghost:] = (
            MUTABLE_PROCESSING_BUFFER[:, -s_ghost:],
            MUTABLE_PROCESSING_BUFFER[:, :-s_ghost],
        )
        cv2.addWeighted(
            MUTABLE_PROCESSING_BUFFER,
            1.0 - CHROMA_GHOST_MIX,
            GHOST_FRAME_BUFFER,
            CHROMA_GHOST_MIX,
            0,
            dst=MUTABLE_PROCESSING_BUFFER,
        )


def _acts_chroma_shift(raw):
    return n(raw) > CHROMA_ACTIVE_ABOVE
