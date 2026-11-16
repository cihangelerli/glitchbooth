"""Chroma Shift effect.

Moved from server.py unchanged (function and class bodies identical, names unchanged).
No thread, file access or log line when this module is imported; it only creates its
buffers and tables.
"""

import cv2
import numpy as np

from core import noise
from core.scale import n
from state import MUTABLE_PROCESSING_BUFFER
from tuning import (
    ANA_GHOST_OFFSET,
    ANA_SHIFT_MAX,
    CHROMA_ACTIVE_ABOVE,
    CHROMA_GHOST_MIX,
    CHROMA_NOISE_AMOUNT,
    CHROMA_NOISE_ENABLED,
    CHROMA_NOISE_ROWS,
    CHROMA_NOISE_SPEED,
    FRAME_H,
    FRAME_W,
)

CHANNEL_SHIFT_BUFFER = np.empty((FRAME_H, FRAME_W), dtype=np.uint8, order="C")
GHOST_FRAME_BUFFER = np.empty((FRAME_H, FRAME_W, 3), dtype=np.uint8, order="C")

# Noise: the red shift can vary from row to row (CHROMA_NOISE_ENABLED).
RED_NOISE_BUFFER = np.empty((FRAME_H, FRAME_W), dtype=np.uint8, order="C")
_ROWS = np.arange(FRAME_H, dtype=np.float64)
_MAPX = np.empty((FRAME_H, FRAME_W), dtype=np.float32, order="C")
_MAPY = np.tile(np.arange(FRAME_H, dtype=np.float32)[:, None], (1, FRAME_W))
_XS = np.tile(np.arange(FRAME_W, dtype=np.float32), (FRAME_H, 1))
_TABLE = noise.table_for("chroma_shift")


def _shift_red_with_noise(red, shift, current_time):
    # Same wrap-around shift as below, but every row gets its own amount from a smooth noise.
    lane = noise.lane(
        _TABLE, _ROWS, current_time, CHROMA_NOISE_ROWS, CHROMA_NOISE_SPEED
    )
    per_row = np.rint(
        shift * (1.0 + CHROMA_NOISE_AMOUNT * lane)
    )  # whole pixels, like int() below
    np.add(_XS, per_row[:, None].astype(np.float32), out=_MAPX)
    CHANNEL_SHIFT_BUFFER[:] = red
    cv2.remap(
        CHANNEL_SHIFT_BUFFER,
        _MAPX,
        _MAPY,
        cv2.INTER_NEAREST,
        dst=RED_NOISE_BUFFER,
        borderMode=cv2.BORDER_WRAP,  # a shift past the edge wraps around, as np.roll does
    )
    red[:] = RED_NOISE_BUFFER


def apply_chroma_shift(raw_knob, current_time):
    if not _acts_chroma_shift(raw_knob):
        return
    ana_val = n(raw_knob)
    h, w = FRAME_H, FRAME_W
    shift = int(ana_val * ANA_SHIFT_MAX)
    red = MUTABLE_PROCESSING_BUFFER[:, :, 2]
    if shift != 0 and noise.is_on(CHROMA_NOISE_ENABLED):
        _shift_red_with_noise(red, shift, current_time)
    elif shift > 0:
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
