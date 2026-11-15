"""Palette helpers of the Posterize effect.

Moved from server.py unchanged (function and class bodies identical, names unchanged).
No thread, file access or log line when this module is imported; it only creates its
buffers and tables.
"""

import numpy as np

from tuning import NEON_PALETTES


def _posterize_palette():
    """Melt's neon palette ordered from the darkest hue to the brightest."""
    luma = NEON_PALETTES.astype(np.float32) @ np.array(
        [0.114, 0.587, 0.299], np.float32
    )
    return np.ascontiguousarray(NEON_PALETTES[np.argsort(luma)])


def _ramp_luts(positions, colours_bgr):
    """Blend colour stops into three 256-entry LUTs (B, G, R)."""
    x = np.arange(256, dtype=np.float32)
    colours = np.asarray(colours_bgr, dtype=np.float32)
    out = np.zeros((3, 256, 1), dtype=np.uint8)
    for ch in range(3):
        out[ch, :, 0] = np.clip(np.interp(x, positions, colours[:, ch]), 0, 255)
    return out
