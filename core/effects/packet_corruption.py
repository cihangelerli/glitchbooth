"""Packet Corruption effect.

Moved from server.py unchanged (function and class bodies identical, names unchanged).
No thread, file access or log line when this module is imported; it only creates its
buffers and tables.
"""

import cv2
import numpy as np

from core.scale import n
from state import MUTABLE_PROCESSING_BUFFER
from tuning import (
    CORRUPTION_BLEND_BELOW,
    CORRUPTION_CELL_H,
    CORRUPTION_CELL_W,
    CORRUPTION_CLEAR_BELOW,
    CORRUPTION_CLEAR_ENERGY,
    CORRUPTION_COLUMN_BLACK_PROB_END,
    CORRUPTION_COLUMN_NOISE_PROB,
    CORRUPTION_DECAY,
    CORRUPTION_SPAWN_ABOVE,
    CORRUPTION_SPAWN_RATE,
    CORRUPTION_SQUARE_MAX_CELLS,
    CORRUPTION_SQUARE_PROB,
    CORRUPTION_THIN_COLUMN_PROB,
    FRAME_H,
    FRAME_W,
)


# Values and buffers derived from the tunables above (they come after the check).
CORRUPTION_GRID_H = FRAME_H // CORRUPTION_CELL_H
CORRUPTION_GRID_W = FRAME_W // CORRUPTION_CELL_W

STATIC_BLOCK_BUFFER = np.empty(
    (CORRUPTION_CELL_H, CORRUPTION_CELL_W, 3), dtype=np.uint8, order="C"
)

GLOBAL_NOISE_BUFFER = np.empty(
    (CORRUPTION_CELL_H, CORRUPTION_CELL_W, 3), dtype=np.uint8, order="C"
)

CORRUPTION_ENERGY = np.zeros(
    (CORRUPTION_GRID_H, CORRUPTION_GRID_W), dtype=np.float32, order="C"
)

CORRUPTION_MODE = np.zeros(
    (CORRUPTION_GRID_H, CORRUPTION_GRID_W), dtype=np.uint8, order="C"
)


# =========================================================================
# PACKET CORRUPTION - STANDALONE EFFECT
# =========================================================================
# It has no connection to Data Mosh and works on whichever knob it is attached to.
#   corruption_update(strength)  - spawn new damage, age the existing damage
#   corruption_render(frame)     - paint the current damage onto `frame`
# Both helpers take the strength as an argument and read no knobs.
# =========================================================================


def corruption_reset():
    CORRUPTION_ENERGY[:] = 0.0
    CORRUPTION_MODE[:] = 0


def corruption_update(strength):
    global CORRUPTION_ENERGY, CORRUPTION_MODE
    gh, gw = CORRUPTION_GRID_H, CORRUPTION_GRID_W

    if strength < CORRUPTION_CLEAR_BELOW:
        CORRUPTION_ENERGY[:] = 0.0
        CORRUPTION_MODE[:] = 0

    if strength > CORRUPTION_SPAWN_ABOVE and np.random.rand() < (
        strength * CORRUPTION_SPAWN_RATE
    ):
        if np.random.rand() < CORRUPTION_SQUARE_PROB:
            gy = np.random.randint(0, gh)
            gx = np.random.randint(0, gw)

            block_size_y = np.random.randint(1, CORRUPTION_SQUARE_MAX_CELLS + 1)
            block_size_x = np.random.randint(1, CORRUPTION_SQUARE_MAX_CELLS + 1)

            gy_end = min(gh, gy + block_size_y)
            gx_end = min(gw, gx + block_size_x)

            CORRUPTION_ENERGY[gy:gy_end, gx:gx_end] = 1.0
            CORRUPTION_MODE[gy:gy_end, gx:gx_end] = 1

        else:
            col_width = 1 if np.random.rand() < CORRUPTION_THIN_COLUMN_PROB else 2
            gx = np.random.randint(0, max(1, gw - col_width))
            gx_end = gx + col_width

            col_roll = np.random.rand()
            if col_roll < CORRUPTION_COLUMN_NOISE_PROB:
                mode_selection = 2  # Noise Column
            elif col_roll < CORRUPTION_COLUMN_BLACK_PROB_END:
                mode_selection = 3  # Black Column
            else:
                mode_selection = 4  # White Column

            CORRUPTION_ENERGY[:, gx:gx_end] = 1.0
            CORRUPTION_MODE[:, gx:gx_end] = mode_selection

    CORRUPTION_ENERGY *= CORRUPTION_DECAY
    np.clip(CORRUPTION_ENERGY, 0.0, 1.0, out=CORRUPTION_ENERGY)

    CORRUPTION_MODE[CORRUPTION_ENERGY <= CORRUPTION_CLEAR_ENERGY] = 0


def corruption_render(frame):
    if np.any(CORRUPTION_MODE):
        active_gy, active_gx = np.nonzero(CORRUPTION_MODE)
        bh = CORRUPTION_CELL_H
        bw = CORRUPTION_CELL_W

        if np.any((CORRUPTION_MODE == 1) | (CORRUPTION_MODE == 2)):
            cv2.randu(GLOBAL_NOISE_BUFFER, (0, 0, 0), (255, 255, 255))

        for gy, gx in zip(active_gy, active_gx):
            y1, y2 = gy * bh, (gy + 1) * bh
            x1, x2 = gx * bw, (gx + 1) * bw

            energy = float(CORRUPTION_ENERGY[gy, gx])
            mode = CORRUPTION_MODE[gy, gx]

            block_view = frame[y1:y2, x1:x2]

            need_blend = energy < CORRUPTION_BLEND_BELOW
            if need_blend:
                scratch = STATIC_BLOCK_BUFFER[: (y2 - y1), : (x2 - x1)]
                scratch[:] = block_view

            # Values: 0 = Clean, 1 = Square Noise Block, 2 = Noise Column, 3 = Black Column, 4 = White Column
            if mode == 1 or mode == 2:
                block_view[:] = GLOBAL_NOISE_BUFFER[: (y2 - y1), : (x2 - x1)]
            elif mode == 3:
                block_view[:] = 0
            elif mode == 4:
                block_view[:] = 255

            if need_blend:
                cv2.addWeighted(
                    block_view, energy, scratch, 1.0 - energy, 0, dst=block_view
                )


def apply_packet_corruption(raw_knob, current_time=0.0):
    # Packet Corruption entry point. raw_knob 0-1023. Modifies MUTABLE_PROCESSING_BUFFER in place.
    strength = n(raw_knob)
    if strength < CORRUPTION_CLEAR_BELOW:
        # dormant: everything cleared, nothing drawn, no random draws
        if CORRUPTION_MODE.any() or CORRUPTION_ENERGY.any():
            corruption_reset()
        return
    corruption_update(strength)
    corruption_render(MUTABLE_PROCESSING_BUFFER)


def _acts_packet_corruption(raw):
    # New damage only spawns above CORRUPTION_SPAWN_ABOVE. The handler itself keeps its own
    # gate (CORRUPTION_CLEAR_BELOW), because that one also clears old damage.
    return n(raw) > CORRUPTION_SPAWN_ABOVE
