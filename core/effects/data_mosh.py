"""Data Mosh effect.

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
    DISABLED_EFFECTS,
    FRAME_H,
    FRAME_W,
    MOSH_BLEED_MAX,
    MOSH_BLEED_MODE,
    MOSH_CELL_H,
    MOSH_CELL_W,
    MOSH_DORMANT_BELOW,
    MOSH_DRAG_NEW_WEIGHT,
    MOSH_EDGE_ERODE_MAX_PX,
    MOSH_HOLD_MAX_FRAMES,
    MOSH_HOLD_MIN_FRAMES,
    MOSH_NOISE_AMOUNT,
    MOSH_NOISE_ENABLED,
    MOSH_NOISE_SIZE_CELLS,
    MOSH_NOISE_SPEED,
    MOSH_RELEASE_ERODE_PX,
    MOSH_RELEASE_FRAMES,
    MOSH_THRESH_BASE,
    MOSH_THRESH_SPAN,
    MOSH_VECTOR_PX,
)

# =========================================================================
# DATA MOSH - INDEPENDENT EFFECT (SHARES NO STATE WITH OTHER EFFECTS)
# =========================================================================
# Self-contained: it owns its buffers, its timers and its own random generator, and it
# never reads, writes or calls anything that belongs to another effect.
#
# Per frame it makes a fixed, small number of large OpenCV/NumPy calls (no per-cell
# Python loop), all inside preallocated buffers and restricted to the row band that
# contains the held cells. OpenCV calls release the GIL, so the display thread is
# not starved.
#
# Look: a cell that shows motion freezes the PREVIOUS picture, drags it
# +-MOSH_VECTOR_PX per frame, holds it for MOSH_HOLD_*_FRAMES (rising with strength),
# then dissolves from its edges. Inside held cells the brightness and the colour can
# come from different pictures (colour-plane bleed).
#
# Cancel rule: the moment the strength drops below MOSH_DORMANT_BELOW every hold is
# cancelled at once and nothing is left over.
# =========================================================================

MOSH_GRID_H = FRAME_H // MOSH_CELL_H
MOSH_GRID_W = FRAME_W // MOSH_CELL_W
MOSH_GRID_PX_W = MOSH_GRID_W * MOSH_CELL_W  # columns beyond this are never touched

_MOSH_GRAY_WEIGHTS = np.array([0.114, 0.587, 0.299], dtype=np.float32)  # B, G, R
_MOSH_SAMPLE_Y0 = MOSH_CELL_H // 2
_MOSH_SAMPLE_X0 = MOSH_CELL_W // 2


class DataMoshEngine:
    """Temporal memory and scratch buffers of Data Mosh (about 10 MiB, allocated once)."""

    def __init__(self):
        H, W = FRAME_H, FRAME_W
        gh, gw = MOSH_GRID_H, MOSH_GRID_W
        z3 = lambda: np.zeros((H, W, 3), dtype=np.uint8, order="C")
        z1 = lambda: np.zeros((H, W), dtype=np.uint8, order="C")
        # picture memory: `hold` is the PREVIOUS picture everywhere except inside held cells,
        # where it is the frozen, dragged picture. It is always a complete picture, so the
        # drag never pulls stale or empty pixels in from a neighbouring cell.
        self.hold, self.cur = z3(), z3()

        # Scratch buffers.  The four shift buffers contain held content displaced
        # in the four possible visual directions.
        self.sh_left, self.sh_right = z3(), z3()
        self.sh_up, self.sh_down = z3(), z3()
        self.blend = z3()
        self.comp, self.pos3, self.neg3 = z3(), z3(), z3()

        # Pixel masks, expanded from the per-cell state when needed.
        self.m_keep, self.m_act = z1(), z1()
        self.m_left, self.m_right = z1(), z1()
        self.m_up, self.m_down = z1(), z1()
        self.m_inset, self.m_tmp, self.m_fin = z1(), z1(), z1()

        self.y_live, self.y_hold, self.y_pos, self.y_neg = z1(), z1(), z1(), z1()
        # distance of every pixel from its cell border (for edge erosion)
        yy, xx = np.mgrid[0:H, 0:W]
        iy, ix = yy % MOSH_CELL_H, xx % MOSH_CELL_W
        self.edge_dist = np.minimum(
            np.minimum(iy, MOSH_CELL_H - 1 - iy), np.minimum(ix, MOSH_CELL_W - 1 - ix)
        ).astype(np.uint8)
        # per-cell state

        self.timer = np.zeros((gh, gw), dtype=np.int16)

        # Per-cell direction, meaningful only while timer > 0:
        # 0 = left, 1 = right, 2 = up, 3 = down.
        self.direction = np.zeros((gh, gw), dtype=np.uint8)

        self.samp = np.zeros((gh, gw, 3), dtype=np.int16)
        self.prev_samp = np.zeros((gh, gw, 3), dtype=np.int16)
        self.have_prev = False
        self.rng = np.random.default_rng()
        # noise (MOSH_NOISE_ENABLED): sensitivity zones over the cell grid
        self.noise_table = noise.table_for("data_mosh")
        self.noise_x = np.arange(gw, dtype=np.float64)
        self.noise_y = np.arange(gh, dtype=np.float64)

    def is_dirty(self):
        return self.have_prev or bool(self.timer.any())

    def reset(self):
        # Forget everything: no hold, no vector, no remembered picture.
        self.timer[:] = 0

        self.direction[:] = 0

        self.have_prev = False

    @staticmethod
    def _cells_to_pixels(cells_u8, row0, row1, dst):
        # Exact cell-grid upsample of cell rows row0..row1 into dst (pixel rows of those cells).
        rows = row1 - row0 + 1
        cv2.resize(
            cells_u8[row0 : row1 + 1],
            (MOSH_GRID_PX_W, rows * MOSH_CELL_H),
            dst=dst[row0 * MOSH_CELL_H : (row1 + 1) * MOSH_CELL_H, :MOSH_GRID_PX_W],
            interpolation=cv2.INTER_NEAREST,
        )

    def step(self, frame, strength, current_time=0.0):
        # Apply one frame of Data Mosh to `frame` (modified in place).
        gh, gw, H, W = MOSH_GRID_H, MOSH_GRID_W, FRAME_H, FRAME_W

        vec = max(
            0, min(MOSH_VECTOR_PX, FRAME_H)
        )  # longer than the frame or negative would crash

        motion_samples = frame[
            _MOSH_SAMPLE_Y0::MOSH_CELL_H, _MOSH_SAMPLE_X0::MOSH_CELL_W
        ][:gh, :gw]
        np.copyto(self.samp, motion_samples, casting="unsafe")

        if not self.have_prev:  # first frame after start / cancel: only remember
            np.copyto(self.hold, frame)
            np.copyto(self.prev_samp, self.samp)
            self.have_prev = True
            return

        # ---- 1. which cells show motion ----
        threshold = int(MOSH_THRESH_BASE + (1.0 - strength) * MOSH_THRESH_SPAN)
        if noise.is_on(
            MOSH_NOISE_ENABLED
        ):  # one threshold per cell: easy and stubborn zones
            zones = noise.field2(
                self.noise_table,
                self.noise_x,
                self.noise_y,
                current_time,
                MOSH_NOISE_SIZE_CELLS,
                MOSH_NOISE_SPEED,
            )
            threshold = np.maximum(threshold * (1.0 + MOSH_NOISE_AMOUNT * zones), 0.0)
        if np.array_equal(self.samp, self.prev_samp):
            motion = np.zeros((gh, gw), dtype=bool)  # duplicate camera frame
        else:
            motion = (np.abs(self.samp - self.prev_samp) * _MOSH_GRAY_WEIGHTS).sum(
                -1
            ) > threshold
        hold_frames = int(
            round(
                MOSH_HOLD_MIN_FRAMES
                + (MOSH_HOLD_MAX_FRAMES - MOSH_HOLD_MIN_FRAMES) * strength
            )
        )
        new_cells = motion & (self.timer == 0)

        # ---- 2. newly held cells start from the picture of just before the motion ----
        # (`hold` already is that picture; each new cell only needs a drag direction)

        if new_cells.any():
            count = int(new_cells.sum())

            # Every new hold chooses one visual direction with equal probability:
            # 0 = left, 1 = right, 2 = up, 3 = down.
            self.direction[new_cells] = self.rng.integers(
                0, 4, size=count, dtype=np.uint8
            )

            # Only newly held cells get a fresh timer. Existing holds continue
            # toward release instead of being perpetually renewed.
            self.timer[new_cells] = hold_frames

        active = self.timer > 0
        band = None

        # ---- 3. drag, erode, composite - only inside the band of rows with held cells ----
        if active.any():
            np.copyto(self.cur, frame)  # this stage's INPUT (frame is modified below)
            rows = np.flatnonzero(active.any(axis=1))
            r0, r1 = int(rows[0]), int(rows[-1])
            band = slice(r0 * MOSH_CELL_H, (r1 + 1) * MOSH_CELL_H)

            self._cells_to_pixels(active.view(np.uint8), r0, r1, self.m_act)
            self._cells_to_pixels(
                (self.direction == 0).view(np.uint8), r0, r1, self.m_left
            )
            self._cells_to_pixels(
                (self.direction == 1).view(np.uint8), r0, r1, self.m_right
            )
            self._cells_to_pixels(
                (self.direction == 2).view(np.uint8), r0, r1, self.m_up
            )
            self._cells_to_pixels(
                (self.direction == 3).view(np.uint8), r0, r1, self.m_down
            )

            hold_band = self.hold[band]
            y0, y1 = band.start, band.stop

            # Horizontal candidates. Pixels clamp at the frame's left/right edges.
            # "Right" means the visible held content moves right.
            self.sh_right[band, vec:] = hold_band[:, : W - vec]
            self.sh_right[band, :vec] = hold_band[:, :1]

            # "Left" means the visible held content moves left.
            self.sh_left[band, : W - vec] = hold_band[:, vec:]
            self.sh_left[band, W - vec :] = hold_band[:, W - 1 : W]

            # Vertical candidates. These read from the full held frame, rather than
            # hold_band, because an active row can pull pixels from just outside the
            # active band. Pixels clamp at the top/bottom frame edges.

            # "Up": visible content moves up, so each destination row reads below it.
            src0 = min(y0 + vec, H - 1)
            copy_rows = min(y1 - y0, H - src0)
            if copy_rows:
                self.sh_up[y0 : y0 + copy_rows] = self.hold[src0 : src0 + copy_rows]
            if y0 + copy_rows < y1:
                self.sh_up[y0 + copy_rows : y1] = self.hold[H - 1 : H]

            # "Down": visible content moves down, so each destination row reads above it.
            top_clamp_rows = max(0, vec - y0)
            if top_clamp_rows:
                self.sh_down[y0 : y0 + top_clamp_rows] = self.hold[0:1]
            src0 = max(0, y0 - vec)
            copy_rows = y1 - y0 - top_clamp_rows
            if copy_rows:
                self.sh_down[y0 + top_clamp_rows : y1] = self.hold[
                    src0 : src0 + copy_rows
                ]

            # Start with the right-going candidate, then replace pixels belonging to
            # left, up, and down cells. Direction masks are mutually exclusive.
            cv2.copyTo(self.sh_left[band], self.m_left[band], self.sh_right[band])
            cv2.copyTo(self.sh_up[band], self.m_up[band], self.sh_right[band])
            cv2.copyTo(self.sh_down[band], self.m_down[band], self.sh_right[band])

            cv2.addWeighted(
                self.sh_right[band],
                MOSH_DRAG_NEW_WEIGHT,
                hold_band,
                1.0 - MOSH_DRAG_NEW_WEIGHT,
                0,
                dst=self.blend[band],
            )
            cv2.copyTo(self.blend[band], self.m_act[band], hold_band)

            # edges erode a little always, and progressively during the last frames
            inset = self.rng.integers(
                0, MOSH_EDGE_ERODE_MAX_PX + 1, (gh, gw), dtype=np.int16
            ) + (
                np.clip(MOSH_RELEASE_FRAMES - self.timer, 0, MOSH_RELEASE_FRAMES)
                * MOSH_RELEASE_ERODE_PX
            )
            inset = np.clip(inset, 0, 255).astype(np.uint8)
            self._cells_to_pixels(inset, r0, r1, self.m_inset)
            cv2.compare(
                self.edge_dist[band],
                self.m_inset[band],
                cv2.CMP_GE,
                dst=self.m_tmp[band],
            )
            cv2.bitwise_and(self.m_tmp[band], self.m_act[band], dst=self.m_fin[band])

            if MOSH_BLEED_MODE == "off":
                cv2.copyTo(hold_band, self.m_fin[band], frame[band])
            else:
                bleed = MOSH_BLEED_MAX * strength
                cv2.cvtColor(self.cur[band], cv2.COLOR_BGR2GRAY, dst=self.y_live[band])
                cv2.cvtColor(hold_band, cv2.COLOR_BGR2GRAY, dst=self.y_hold[band])
                if MOSH_BLEED_MODE == "shape_live_colour_held":
                    luma_a, luma_b, base = self.y_live, self.y_hold, hold_band
                else:  # "shape_held_colour_live"
                    luma_a, luma_b, base = self.y_hold, self.y_live, self.cur[band]
                cv2.subtract(luma_a[band], luma_b[band], dst=self.y_pos[band])
                cv2.subtract(luma_b[band], luma_a[band], dst=self.y_neg[band])
                cv2.convertScaleAbs(self.y_pos[band], dst=self.y_pos[band], alpha=bleed)
                cv2.convertScaleAbs(self.y_neg[band], dst=self.y_neg[band], alpha=bleed)
                cv2.merge([self.y_pos[band]] * 3, dst=self.pos3[band])
                cv2.merge([self.y_neg[band]] * 3, dst=self.neg3[band])
                cv2.add(base, self.pos3[band], dst=self.comp[band])
                cv2.subtract(self.comp[band], self.neg3[band], dst=self.comp[band])
                cv2.copyTo(self.comp[band], self.m_fin[band], frame[band])

        # ---- 4. time moves on; the memory follows this frame outside the cells still held ----
        self.timer[self.timer > 0] -= 1
        self.direction[self.timer == 0] = 0

        still_held = self.timer > 0
        if (
            band is None
        ):  # nothing was held: the frame is unchanged, remember it directly
            np.copyto(self.hold, frame)
        elif not still_held.any():
            np.copyto(self.hold, self.cur)
        else:
            y0, y1 = band.start, band.stop
            np.copyto(self.hold[:y0], self.cur[:y0])
            np.copyto(self.hold[y1:], self.cur[y1:])
            self._cells_to_pixels(still_held.view(np.uint8), r0, r1, self.m_keep)
            cv2.compare(
                self.m_keep[band], 0, cv2.CMP_EQ, dst=self.m_tmp[band]
            )  # 255 outside held cells
            cv2.copyTo(self.cur[band], self.m_tmp[band], self.hold[band])
        np.copyto(self.prev_samp, self.samp)


_MOSH = DataMoshEngine() if "data_mosh" not in DISABLED_EFFECTS else None


def apply_data_mosh(raw_knob, current_time):
    # Data Mosh entry point. raw_knob 0-1023. Modifies MUTABLE_PROCESSING_BUFFER in place.
    if not _acts_data_mosh(raw_knob):  # below MOSH_DORMANT_BELOW
        if _MOSH.is_dirty():  # lowering the knob cancels all moshing at once
            _MOSH.reset()
        return
    _MOSH.step(MUTABLE_PROCESSING_BUFFER, n(raw_knob) ** 2, current_time)


def _acts_data_mosh(raw):
    # Strength is the knob squared; below MOSH_DORMANT_BELOW every mosh is cancelled at once.
    return n(raw) ** 2 >= MOSH_DORMANT_BELOW
