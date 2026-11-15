"""Pixel Damage (pixellation) effect.

Moved from server.py unchanged (function and class bodies identical, names unchanged).
No thread, file access or log line when this module is imported; it only creates its
buffers and tables.
"""

import cv2
import numpy as np

from core.scale import n
from state import MUTABLE_PROCESSING_BUFFER
from tuning import (
    COMPRESSION_TILE_BASE,
    COMPRESSION_TILE_SCALE,
    FRAME_H,
    FRAME_W,
    PIXELLATION_ACTIVE_ABOVE,
)


COMPRESSION_STAGE_BUFFER = np.empty((FRAME_H, FRAME_W, 3), dtype=np.uint8, order="C")


def apply_pixellation(raw_knob, current_time):
    comp = n(raw_knob)
    if _acts_pixellation(raw_knob):
        tile = int(COMPRESSION_TILE_BASE + comp * COMPRESSION_TILE_SCALE)
        tw, th = max(1, FRAME_W // tile), max(1, FRAME_H // tile)
        sub = COMPRESSION_STAGE_BUFFER[:th, :tw]
        cv2.resize(
            MUTABLE_PROCESSING_BUFFER,
            (tw, th),
            dst=sub,
            interpolation=cv2.INTER_NEAREST,
        )
        cv2.resize(
            sub,
            (FRAME_W, FRAME_H),
            dst=MUTABLE_PROCESSING_BUFFER,
            interpolation=cv2.INTER_NEAREST,
        )




def _acts_pixellation(raw):
    return n(raw) > PIXELLATION_ACTIVE_ABOVE
