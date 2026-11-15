"""CRT overlay: the scan-line mask and the function that applies it.

Moved from server.py unchanged (function and class bodies identical, names unchanged).
No thread, file access or log line when this module is imported; it only creates its
buffers and tables.
"""

import logging

import cv2
import numpy as np

from state import MUTABLE_PROCESSING_BUFFER
from tuning import (
    CRT_APERTURE_PERIOD,
    CRT_APERTURE_STRENGTH,
    CRT_ENABLED,
    CRT_GREEN_GLOW,
    CRT_SCANLINE_PERIOD,
    CRT_SCANLINE_STRENGTH,
    CRT_SCANLINE_THICKNESS,
    FRAME_H,
    FRAME_W,
)

logger = logging.getLogger("glitchbooth.core.effects.crt")


CRT_MASK = np.empty((FRAME_H, FRAME_W, 3), dtype=np.uint8, order="C")


def initialize_crt_mask():
    global CRT_MASK
    logger.info(
        "[SYSTEM SETUP] Materializing resolution-agnostic CRT mask (%dx%d)...",
        FRAME_W,
        FRAME_H,
    )

    float_mask = np.ones((FRAME_H, FRAME_W, 3), dtype=np.float32)

    y_indices = np.arange(FRAME_H)
    scanline_rows = y_indices % CRT_SCANLINE_PERIOD >= (
        CRT_SCANLINE_PERIOD - CRT_SCANLINE_THICKNESS
    )
    float_mask[scanline_rows, :] *= 1.0 - CRT_SCANLINE_STRENGTH

    float_mask[scanline_rows, 1] += CRT_GREEN_GLOW * CRT_SCANLINE_STRENGTH

    x_indices = np.arange(FRAME_W)
    for i in range(CRT_APERTURE_PERIOD):
        col_idx = i % 3
        channel = 2 - col_idx
        float_mask[:, x_indices % CRT_APERTURE_PERIOD == i, channel] += (
            CRT_APERTURE_STRENGTH
        )

    np.clip(float_mask, 0.0, 1.0, out=float_mask)
    CRT_MASK[:] = (float_mask * 255.0).astype(np.uint8)
    logger.info("[SYSTEM SETUP] CRT Mask locked into contiguous C-memory.")


def apply_crt_overlay():
    if CRT_ENABLED:
        cv2.multiply(
            MUTABLE_PROCESSING_BUFFER,
            CRT_MASK,
            MUTABLE_PROCESSING_BUFFER,
            scale=1.0 / 255.0,
        )
