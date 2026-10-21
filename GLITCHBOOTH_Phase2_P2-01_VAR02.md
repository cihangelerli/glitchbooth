# GLITCH BOOTH — Phase 2 Data Mosh Proposal P2-01 (human-readable edition)

**Status:** Approved as P2-01 by the owner; implemented and verified **in a scratch checkout only**. **No project file on the booth has been modified by the assistant.** This document restates the whole proposal and its evidence as readable text. The patch file `phase2_P2-01.patch` is the machine-applicable form of exactly the same content; this document is complete without it.

**Target:** Raspberry Pi 5 1 GB, 640×480 BGR pipeline, 10–15 FPS.  
**Primary file:** `server.py` (plus two label lines, the A/B harness, and two new test scripts).  
**Baseline:** git tag `pre-phase2` on `dev` (created and pushed by the owner).  
**Decision sought:** (1) accept this readable record of P2-01 for the Pi trial; (2) decide the open **strength default** in section 4; (3) confirm the open items in section 14.

---

## 1. Purpose and the owner's conditions

Phase 2 separates the old fused "Data Mosh + Packet Corruption" effect on knob 2 into two independent effects, and rebuilds Data Mosh as the "frame hold + colour-plane bleed" effect approved in P2-00.

| Owner condition | How P2-01 meets it | Verified by |
|---|---|---|
| Packet Corruption must not affect Data Mosh at all | The line `CORRUPTION_ENERGY[DATAMOSH_VX != 0] += 0.1` is deleted. The two effects share no state, no buffers, no constants, no random numbers and no function calls. | T1 |
| Data Mosh is its own effect, controlled only by its knob strength | `apply_data_mosh(raw_knob, current_time)` reads nothing but its own knob value and its own state. | T1, T9, T10 |
| Packet Corruption isolated, ready to be used later on any knob | Extracted into a self-contained effect with its own functions and constants, **unwired by default** (`PACKET_CORRUPTION_KNOB = None`). Setting that one constant to 0–5 connects it to any knob. | T2, T10 |
| Ready for the later Packet Corruption + Pixellation fusion | Two plain helpers, `corruption_update(strength)` and `corruption_render(frame)`, take the strength as an argument and read no knobs. | T1, T2 |
| Performance at least as optimised as the Melt work | No per-cell Python loop, preallocated buffers, `dst=` on every OpenCV call, and work limited to the rows that contain held cells. | T3, T8, benchmark |
| Aesthetics first | The approved look is kept; the optimised code is byte-identical to a plain reference implementation. Strength against today's effect is measured in section 4 and one decision is left open. | T3, section 4 |
| Knob 2 belongs to Data Mosh, and lowering the knob cancels all moshing at once | Dormant below `strength < 0.02`; the moment the knob drops under it every hold is cancelled and nothing is left over. | T9 |
| Always ask when not sure | Open questions are listed in section 14 rather than guessed. | — |

## 2. What the old effect actually does (findings that shaped the design)

1. **Per-channel motion test.** It sums per-channel absolute differences at each cell's centre, so it also reacts to colour-only changes. Comparing grey levels instead gave a quarter to a half as many held cells in my tests.
2. **Stale reference picture.** Its history is refreshed only inside held cells (apart from a 90 s purge). A held cell therefore keeps showing an old picture until the live picture matches it again. This produces persistent "ghost trails".
3. **Per-cell Python loop.** Each held cell runs about 15 small NumPy operations, up to 390 cells per frame. This is the same GIL-holding pattern the old Melt had. With many cells moving it keeps the GIL for 5–12 ms in one go in the sandbox (section 11).
4. **Coupling.** `CORRUPTION_ENERGY[DATAMOSH_VX != 0] += 0.1` fed Data Mosh into corruption. It disappears.
5. **Run-to-run randomness.** Its sign pool is random at every import, so its measured strength varies by roughly ±15% between runs.

## 3. Visual design of Data Mosh v2 (as approved in P2-00)

1. **Trigger.** A cell is triggered by motion at its centre between consecutive pictures, using today's per-channel metric and threshold `int(18 + (1 − s) × 40)` (18…58), where `s = (knob/1023)²`.
2. **Frame-hold.** A triggered cell freezes the picture from just before the motion and drags it ±4 px horizontally per frame with today's 161/256 blend. New motion in a held cell restarts its timer.
3. **Hold length.** `hold = round(MIN + (MAX − MIN) × s)` frames. Shipped default 6–16 (see the open decision in section 4).
4. **Release.** A cell dissolves from its edges during the last 3 frames, on top of today's random 0–2 px edge erosion. This keeps the glitchy rectangular character instead of a soft cross-fade.
5. **Colour-plane bleed.** Inside held cells the brightness and the colour can come from different pictures. Default mode `shape_live_colour_held`: the shape follows the live picture, the colour stays held. Mix = `0.8 × s`. The luma/chroma swap is done with a grey-level conversion and a few saturating 8-bit operations, without converting the picture to another colour space and back.
6. **Same grid as today.** 15 × 26 cells of 32 × 24 px. The right-most 16 px are never touched.
7. **Duplicate camera frames** (the camera delivers fewer frames than the pipeline runs) are detected by comparing the 390 sample points and trigger nothing.
8. **Position** is selectable (A/B/C, section 8); default is today's position.

### Two corrections found while implementing

- **Exact cell alignment.** The P2-00 sketch resized its masks to the full 640 px width, which misaligned cells by up to 16 px at the right. The code uses exact 24 × 32 px cells and leaves the right-most 16 px untouched, as today.
- **Hold memory is always a complete picture.** Test T4 showed that after a reset the engine's memory still contained old pictures, and the ±4 px drag reads across cell borders, so stale or black pixels could leak into the edges of newly held cells (this is also the dark edge bars on the earlier scratch preview). The memory now follows the previous frame everywhere outside held cells. A separate "capture" step became unnecessary and was removed. Cell content for the first frame of a hold is unchanged; only the ≤ 4 px border fringes are fixed. `phase2_engine_preview.png` shows the final engine.

## 4. Strength comparison against today's effect (acceptance gate — **OPEN**)

The metric is the one used as an acceptance gate in the independent P2-02 proposal: mean absolute difference between output and input frame, and the percentage of pixels changed by more than 12 levels, for the same synthetic moving-shapes scenes, frames 20–79. Today's effect and the new effect are both run through the real server functions. Scenes are synthetic (coloured shapes on a textured background) and **are not the owner's scene**; the owner's own scene must be re-measured.

### Shipped default (hold 6–16, bleed `shape_live_colour_held`)

| Scene | Raw knob | Today (mean diff / % > 12) | New (mean diff / % > 12) | Ratio new / today |
|---|---:|---:|---:|---:|
| busy | 150 | 4.47 / 5.7 % | 2.38 / 2.5 % | 0.53 |
| busy | 512 | 6.28 / 9.1 % | 6.96 / 10.2 % | 1.11 |
| busy | 1023 | 11.21 / 19.4 % | 8.85 / 14.8 % | 0.79 |
| sparse | 150 | 0.02 / 0.0 % | 0.04 / 0.1 % | ≈ 2 (both near zero) |
| sparse | 512 | 1.08 / 1.9 % | 1.29 / 2.3 % | 1.20 |
| sparse | 1023 | 2.75 / 6.1 % | 2.20 / 4.1 % | 0.80 |

**Finding:** the default is comparable at mid-range but about 20 % weaker than today's effect at raw 1023, so the "at least as strong at maximum" gate is **not met** by the shipped default on these scenes. Today's own numbers move by about ±15 % between runs.

### What moves the needle (busy scene, ratio new / today of mean difference)

| Hold range (frames) | raw 150 | raw 512 | raw 1023 | Sparse scene raw 1023 |
|---|---:|---:|---:|---:|
| 6–16 (shipped) | 0.54 | 1.09 | 0.83 | 0.76 |
| 8–22 | 0.63 | 1.16 | 0.93 | 1.06 |
| **10–26** | 0.69 | 1.24 | **1.01** | 1.15 |
| 12–30 | 0.81 | 1.30 | 1.09 | 1.18 |

Other settings at raw 1023 (busy, mean diff / % > 12): default 8.85 / 14.8 %; bleed off 8.69 / 14.8 %; **bleed mode `shape_held_colour_live` only 1.43 / 3.9 %** (far weaker than today — keep the default mode); drag 8 px 8.34 / 13.3 %; hold 12–30 12.17 / 21.3 %; hold 12–30 + drag 8 px + bleed off 10.86 / 18.1 %. Lowering the motion threshold base to 8 changed nothing on this scene.

### Recommendation and open decision **D-S**

Change the default hold to **10–26 frames** (`MOSH_HOLD_MIN_FRAMES = 10`, `MOSH_HOLD_MAX_FRAMES = 26`), which reaches parity at maximum on both scenes, then tune live. This is a one-line tunable change. The shipped patch keeps the P2-00 value (6–16) because changing an approved look is the owner's decision. At mid-range (raw 512) the longer hold is 1.2–2× today's strength, because today's effect is weak there, while at raw 150 it is still only about 0.7× on the busy scene. The owner may therefore prefer a middle value such as 8–22.

## 5. Independence architecture

| | Data Mosh v2 | Packet Corruption |
|---|---|---|
| State | One `DataMoshEngine` instance `_MOSH`: held picture memory, input copy, per-cell timers and vectors, sample points, scratch buffers (≈ 10 MiB, allocated once) | `CORRUPTION_ENERGY`, `CORRUPTION_MODE` (15 × 26), `GLOBAL_NOISE_BUFFER`, `STATIC_BLOCK_BUFFER` |
| Constants | `MOSH_*` including its own cell size | `CORRUPTION_*` including its own cell size |
| Random numbers | Private `np.random.Generator` (seed `MOSH_RNG_SEED`, default fresh entropy) | Global `np.random` and `cv2.randu`, exactly as the old stage, so its look is unchanged |
| Entry point | `apply_data_mosh(raw_knob, current_time, motion_samples=None)` | `apply_packet_corruption(raw_knob, current_time=0.0)` |
| Helpers | `data_mosh_reset()`, `data_mosh_take_samples(frame)` | `corruption_update(strength)`, `corruption_render(frame)`, `corruption_reset()` |
| Knob wiring | `DATAMOSH_KNOB = 2` | `PACKET_CORRUPTION_KNOB = None` |

- **Cross-effect guarantees.** Neither effect reads or writes the other's names; Data Mosh never consumes a global random number, so toggling or future-wiring corruption cannot change Data Mosh's choices and vice versa. T1 checks this functionally and by scanning the source text.
- **Packet Corruption on any knob.** Set `PACKET_CORRUPTION_KNOB` to 0–5. Tested on knob 2, on knob 3, and on knob 2 together with Data Mosh (both act independently; a notice is printed that two effects share a knob). A bad value prints a warning and falls back to `None`.
- **For the fusion.** `corruption_update(strength)` and `corruption_render(frame)` are the two reusable pieces; the Phase 3 fusion can call them with its own strength and add Pixellation around them.
- **Random-number policy.** A/B and test harnesses call `seed_private_rngs(seed)` before every step so that results are reproducible even though Data Mosh owns its generator.

## 6. Tunables and acceptance rules

| Setting | Starting value | Range / notes |
|---|---:|---|
| `DATAMOSH_KNOB` | 2 | 0–5 or `None` |
| `PACKET_CORRUPTION_KNOB` | `None` | 0–5 or `None` |
| `MOSH_STAGE_POSITION` | `"before_drift"` | `"before_drift"`, `"after_melt_own_motion"`, `"after_melt_pre_melt_motion"` |
| `MOSH_DORMANT_BELOW` | 0.02 | strength `(raw/1023)²` below this cancels everything at once (raw 144 dormant, 145 active) |
| `MOSH_THRESH_BASE` / `MOSH_THRESH_SPAN` | 18 / 40 | threshold 18…58 as today |
| `MOSH_HOLD_MIN_FRAMES` / `MOSH_HOLD_MAX_FRAMES` | 6 / 16 | 1–60; **recommended 10 / 26** (section 4) |
| `MOSH_VECTOR_PX` | 4 | 1–8 |
| `MOSH_DRAG_NEW_WEIGHT` | 161/256 | exactly today's blend |
| `MOSH_EDGE_ERODE_MAX_PX` | 2 | random 0…N px on held cells, as today |
| `MOSH_RELEASE_FRAMES` / `MOSH_RELEASE_ERODE_PX` | 3 / 4 | edges dissolve over the last N frames |
| `MOSH_BLEED_MODE` | `shape_live_colour_held` | or `shape_held_colour_live` (much weaker) or `off` |
| `MOSH_BLEED_MAX` | 0.8 | bleed mix = MAX × strength |
| `MOSH_RNG_SEED` | `None` | fresh entropy each start |
| `MOSH_CELL_H` / `MOSH_CELL_W` | 32 / 24 | 15 × 26 grid; right-most 16 px never touched |
| `CORRUPTION_*` | today's values | spawn 0.33, squares 80 %, decay 0.9, etc. — see code |

### Acceptance gates

- **Independence:** with knob 2 at 0 or 20 and every other knob busy (Melt included), output is byte-identical to `pre-phase2`.
- **Cancel:** lowering the knob below the threshold leaves no timers, vectors or remembered picture; the next activation starts clean with an untouched first frame.
- **Temporal:** duplicate camera frames create no holds; all holds expire.
- **Edges:** columns 624–639 stay byte-identical.
- **Strength:** at raw 1023 at least as strong as today's effect on the owner's scene (**open**, section 4).
- **Performance:** no frame-sized allocation per frame; Pi cost and display latency still to be measured on the Pi.

## 7. `server.py` proposed state

All code below is copied from the verified scratch build, not retyped.

### 7.1 Tunables and independent cell geometry

This replaces `DATAMOSH_BLOCK_H/W`, `DATAMOSH_START_X/Y`, `DATAMOSH_STRIDE_X/Y` and the two noise/block buffers that used them. It sits in the existing "scalable glitch matrix constants" area, right after `FRAME_H` / `FRAME_W`.

```python
# PHASE 2 TUNABLES: EFFECT WIRING, DATA MOSH, PACKET CORRUPTION (readme R7)
# ==============================================================================
# --- wiring: which knob (0-5) drives which effect, or None = not connected -------
DATAMOSH_KNOB = 2
PACKET_CORRUPTION_KNOB = None  # standalone effect, ready: set to a knob index to connect it

# --- Data Mosh -------------------------------------------------------------------
# Where in the pipeline it runs:
#   "before_drift"               today's position (after chroma, before Signal Drift)
#   "after_melt_own_motion"      after Melt, motion measured on its own input
#   "after_melt_pre_melt_motion" after Melt, motion measured on the picture before Melt
MOSH_STAGE_POSITION = "before_drift"
MOSH_DORMANT_BELOW = 0.02  # strength = (knob/1023)**2 below this cancels everything at once
MOSH_THRESH_BASE = 18  # motion threshold = BASE + (1 - strength) * SPAN   (18 .. 58)
MOSH_THRESH_SPAN = 40
MOSH_HOLD_MIN_FRAMES = 6  # hold time rises from MIN to MAX with strength (1-60)
MOSH_HOLD_MAX_FRAMES = 16
MOSH_VECTOR_PX = 4  # horizontal drag per frame (1-8)
MOSH_DRAG_NEW_WEIGHT = 161 / 256  # share of the dragged picture per frame (as the old effect)
MOSH_EDGE_ERODE_MAX_PX = 2  # random edge erosion 0..N px on held cells (as before)
MOSH_RELEASE_FRAMES = 3  # edges dissolve over the last N frames of a hold
MOSH_RELEASE_ERODE_PX = 4  # extra erosion per release frame
# Colour-plane bleed inside held cells: "shape_live_colour_held", "shape_held_colour_live", "off"
MOSH_BLEED_MODE = "shape_live_colour_held"
MOSH_BLEED_MAX = 0.8  # bleed mix = MAX * strength (0-1)
MOSH_RNG_SEED = None  # None = fresh entropy each start; tests reseed via seed_private_rngs()
MOSH_CELL_H = 32  # [320x240: 16] [480x360: 24] [640x480: 32]
MOSH_CELL_W = 24  # [320x240: 12] [480x360: 18] [640x480: 24]

# --- Packet Corruption (values are exactly the old inline stage) ------------------
CORRUPTION_CELL_H = 32
CORRUPTION_CELL_W = 24
CORRUPTION_GRID_H = FRAME_H // CORRUPTION_CELL_H
CORRUPTION_GRID_W = FRAME_W // CORRUPTION_CELL_W
CORRUPTION_CLEAR_BELOW = 0.01  # below this strength all damage is cleared
CORRUPTION_SPAWN_ABOVE = 0.02  # new damage only spawns above this strength
CORRUPTION_SPAWN_RATE = 0.33  # spawn chance per frame = strength * RATE
CORRUPTION_SQUARE_PROB = 0.80  # square block (else a full-height column)
CORRUPTION_SQUARE_MAX_CELLS = 3  # square block is 1..N cells each way
CORRUPTION_THIN_COLUMN_PROB = 0.9  # column is 1 cell wide (else 2)
CORRUPTION_COLUMN_NOISE_PROB = 0.60  # column kind: noise below this roll
CORRUPTION_COLUMN_BLACK_PROB_END = 0.90  # black up to this roll, white above
CORRUPTION_DECAY = 0.9  # energy multiplier per frame
CORRUPTION_CLEAR_ENERGY = 0.005  # damage at or below this energy disappears
CORRUPTION_BLEND_BELOW = 0.95  # below this energy the damage is blended, not solid

STATIC_BLOCK_BUFFER = np.empty(
    (CORRUPTION_CELL_H, CORRUPTION_CELL_W, 3), dtype=np.uint8, order="C"
)

GLOBAL_NOISE_BUFFER = np.empty(
    (CORRUPTION_CELL_H, CORRUPTION_CELL_W, 3), dtype=np.uint8, order="C"
)
```

### 7.2 Buffers and state

The old Data Mosh state is removed: `GRID_H`, `GRID_W`, `DATAMOSH_PREV_FRAME`, `DATAMOSH_WORK_BUFFER`, `DATAMOSH_DIFF_BUFFER`, `DATAMOSH_GRAY_DIFF`, `DATAMOSH_VX`, `DATAMOSH_CYCLE_COUNTER`, `RANDOM_SIGN_POOL`, `RNG_PTR_SIGN`, and the whole function `apply_datamosh`. `WARMUP_*`, `LAST_TEMPORAL_PURGE`, `PREV_FRAME_BUFFER` and `SHARED_GRAY_BUFFER` are left untouched (see "found, not touched" in section 12). A repository-wide search must confirm no other consumer of the removed names before they are deleted.

```python
# TEMPORAL REGISTERS (WARM-UP, PURGE) AND PACKET CORRUPTION STATE
# =========================================================================
WARMUP_FRAMES = 10
WARMUP_COUNTER = 0
LAST_TEMPORAL_PURGE = time.time()

PREV_FRAME_BUFFER = np.empty((FRAME_H, FRAME_W, 3), dtype=np.uint8, order="C")
SHARED_GRAY_BUFFER = np.empty((FRAME_H, FRAME_W), dtype=np.uint8, order="C")

CORRUPTION_ENERGY = np.zeros(
    (CORRUPTION_GRID_H, CORRUPTION_GRID_W), dtype=np.float32, order="C"
)

CORRUPTION_MODE = np.zeros(
    (CORRUPTION_GRID_H, CORRUPTION_GRID_W), dtype=np.uint8, order="C"
)
```

### 7.3 Data Mosh v2 engine

Replaces the old `apply_datamosh` section ("ISOLATED DATAMOSH ENGINE").

```python
# =========================================================================
# DATA MOSH v2 (PHASE 2) - INDEPENDENT EFFECT, DRIVEN BY ITS OWN KNOB ONLY
# =========================================================================
# Self-contained (readme R13). It owns its buffers, its timers and its own random
# generator. It never reads, writes or calls anything that belongs to Packet
# Corruption (or any other effect), and Packet Corruption never touches it.
#
# Per frame it makes a fixed, small number of large OpenCV/NumPy calls (no per-cell
# Python loop), all inside preallocated buffers and restricted to the row band that
# contains the held cells. OpenCV calls release the GIL, so the display thread is
# not starved the way the old per-cell loop could starve it.
#
# Look (approved in P2-00): a cell that shows motion freezes the PREVIOUS picture,
# drags it +-MOSH_VECTOR_PX per frame, holds it for MOSH_HOLD_*_FRAMES (rising with
# strength), then dissolves from its edges. Inside held cells the brightness and the
# colour can come from different pictures (colour-plane bleed).
#
# Cancel rule (protected behaviour): the moment the knob drops below the dormancy
# threshold every hold is cancelled at once and nothing is left over.
# =========================================================================

MOSH_GRID_H = FRAME_H // MOSH_CELL_H
MOSH_GRID_W = FRAME_W // MOSH_CELL_W
MOSH_GRID_PX_W = MOSH_GRID_W * MOSH_CELL_W  # columns beyond this are never touched

_MOSH_GRAY_WEIGHTS = np.array([0.114, 0.587, 0.299], dtype=np.float32)  # B, G, R
_MOSH_SAMPLE_Y0 = MOSH_CELL_H // 2
_MOSH_SAMPLE_X0 = MOSH_CELL_W // 2


class DataMoshEngine:
    """Temporal memory and scratch buffers of Data Mosh (about 10 MiB, allocated once)."""

    def __init__(self, seed=None):
        H, W = FRAME_H, FRAME_W
        gh, gw = MOSH_GRID_H, MOSH_GRID_W
        z3 = lambda: np.zeros((H, W, 3), dtype=np.uint8, order="C")
        z1 = lambda: np.zeros((H, W), dtype=np.uint8, order="C")
        # picture memory: `hold` is the PREVIOUS picture everywhere except inside held cells,
        # where it is the frozen, dragged picture. It is always a complete picture, so the
        # drag never pulls stale or empty pixels in from a neighbouring cell.
        self.hold, self.cur = z3(), z3()
        # scratch (each used only inside one step)
        self.sh_right, self.sh_left, self.blend = z3(), z3(), z3()
        self.comp, self.pos3, self.neg3 = z3(), z3(), z3()
        self.m_keep, self.m_act, self.m_neg = z1(), z1(), z1()
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
        self.vx = np.zeros((gh, gw), dtype=np.int8)
        self.samp = np.zeros((gh, gw, 3), dtype=np.int16)
        self.prev_samp = np.zeros((gh, gw, 3), dtype=np.int16)
        self.have_prev = False
        self.rng = np.random.default_rng(seed)

    def reseed(self, seed):
        self.rng = np.random.default_rng(seed)

    def is_dirty(self):
        return self.have_prev or bool(self.timer.any())

    def reset(self):
        """Forget everything: no hold, no vector, no remembered picture."""
        self.timer[:] = 0
        self.vx[:] = 0
        self.have_prev = False

    @staticmethod
    def _cells_to_pixels(cells_u8, row0, row1, dst):
        """Exact cell-grid upsample of cell rows row0..row1 into dst (pixel rows of those cells)."""
        rows = row1 - row0 + 1
        cv2.resize(
            cells_u8[row0 : row1 + 1],
            (MOSH_GRID_PX_W, rows * MOSH_CELL_H),
            dst=dst[row0 * MOSH_CELL_H : (row1 + 1) * MOSH_CELL_H, :MOSH_GRID_PX_W],
            interpolation=cv2.INTER_NEAREST,
        )

    def step(self, frame, strength, motion_samples=None):
        """Apply one frame of Data Mosh to `frame` (modified in place)."""
        gh, gw, W = MOSH_GRID_H, MOSH_GRID_W, FRAME_W
        vec = MOSH_VECTOR_PX

        if motion_samples is None:
            motion_samples = frame[
                _MOSH_SAMPLE_Y0 :: MOSH_CELL_H, _MOSH_SAMPLE_X0 :: MOSH_CELL_W
            ][:gh, :gw]
        np.copyto(self.samp, motion_samples, casting="unsafe")

        if not self.have_prev:  # first frame after start / cancel: only remember
            np.copyto(self.hold, frame)
            np.copyto(self.prev_samp, self.samp)
            self.have_prev = True
            return

        # ---- 1. which cells show motion (same metric and threshold as before) ----
        threshold = int(MOSH_THRESH_BASE + (1.0 - strength) * MOSH_THRESH_SPAN)
        if np.array_equal(self.samp, self.prev_samp):
            motion = np.zeros((gh, gw), dtype=bool)  # duplicate camera frame
        else:
            motion = (
                np.abs(self.samp - self.prev_samp) * _MOSH_GRAY_WEIGHTS
            ).sum(-1) > threshold
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
            self.vx[new_cells] = np.where(self.rng.random(count) > 0.5, vec, -vec)
        self.timer[motion] = hold_frames
        active = self.timer > 0
        band = None

        # ---- 3. drag, erode, composite - only inside the band of rows with held cells ----
        if active.any():
            np.copyto(self.cur, frame)  # this stage's INPUT (frame is modified below)
            rows = np.flatnonzero(active.any(axis=1))
            r0, r1 = int(rows[0]), int(rows[-1])
            band = slice(r0 * MOSH_CELL_H, (r1 + 1) * MOSH_CELL_H)
            self._cells_to_pixels(active.view(np.uint8), r0, r1, self.m_act)
            self._cells_to_pixels((self.vx < 0).view(np.uint8), r0, r1, self.m_neg)

            hold_band = self.hold[band]
            # +-vec px horizontal shift with edge clamp, picked per cell by its vector sign
            self.sh_right[band, : W - vec] = hold_band[:, vec:]
            self.sh_right[band, W - vec :] = hold_band[:, W - 1 : W]
            self.sh_left[band, vec:] = hold_band[:, : W - vec]
            self.sh_left[band, :vec] = hold_band[:, 0:1]
            cv2.copyTo(self.sh_left[band], self.m_neg[band], self.sh_right[band])
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
                self.edge_dist[band], self.m_inset[band], cv2.CMP_GE, dst=self.m_tmp[band]
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
        self.vx[self.timer == 0] = 0
        still_held = self.timer > 0
        if band is None:  # nothing was held: the frame is unchanged, remember it directly
            np.copyto(self.hold, frame)
        elif not still_held.any():
            np.copyto(self.hold, self.cur)
        else:
            y0, y1 = band.start, band.stop
            np.copyto(self.hold[:y0], self.cur[:y0])
            np.copyto(self.hold[y1:], self.cur[y1:])
            self._cells_to_pixels(still_held.view(np.uint8), r0, r1, self.m_keep)
            cv2.compare(self.m_keep[band], 0, cv2.CMP_EQ, dst=self.m_tmp[band])  # 255 outside held cells
            cv2.copyTo(self.cur[band], self.m_tmp[band], self.hold[band])
        np.copyto(self.prev_samp, self.samp)


_MOSH = DataMoshEngine(MOSH_RNG_SEED)


def data_mosh_reset():
    """Cancel every hold and forget the remembered picture."""
    _MOSH.reset()


def data_mosh_take_samples(frame):
    """Snapshot of the per-cell motion sample points (for MOSH_STAGE_POSITION C)."""
    return frame[_MOSH_SAMPLE_Y0 :: MOSH_CELL_H, _MOSH_SAMPLE_X0 :: MOSH_CELL_W][
        :MOSH_GRID_H, :MOSH_GRID_W
    ].copy()


def apply_data_mosh(raw_knob, current_time, motion_samples=None):
    """Data Mosh entry point. raw_knob 0-1023. Modifies MUTABLE_PROCESSING_BUFFER in place."""
    strength = n(raw_knob) ** 2  # response curve unchanged from the old effect
    if strength < MOSH_DORMANT_BELOW:
        if _MOSH.is_dirty():  # lowering the knob cancels all moshing at once
            _MOSH.reset()
        return
    _MOSH.step(MUTABLE_PROCESSING_BUFFER, strength, motion_samples)
```

### 7.4 Packet Corruption (standalone, look unchanged)

The numbers and the order of random draws are exactly those of the old inline stage; only the literals became named constants.

```python
# =========================================================================
# PACKET CORRUPTION - STANDALONE EFFECT (PHASE 2: EXTRACTED, LOOK UNCHANGED)
# =========================================================================
# Unwired by default (PACKET_CORRUPTION_KNOB = None) and ready to be connected to
# any knob. It has no connection to Data Mosh. Same random draws, in the same order,
# as the old inline stage, so its look is unchanged.
#   corruption_update(strength)  - spawn new damage, age the existing damage
#   corruption_render(frame)     - paint the current damage onto `frame`
# Both helpers take the strength as an argument and read no knobs, so the later
# Packet Corruption + Pixellation fusion can call them directly.
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
    """Packet Corruption entry point. raw_knob 0-1023. Modifies MUTABLE_PROCESSING_BUFFER in place."""
    strength = n(raw_knob)
    if strength < CORRUPTION_CLEAR_BELOW:
        # dormant: identical to the old stage (everything cleared, nothing drawn, no random draws)
        if CORRUPTION_MODE.any() or CORRUPTION_ENERGY.any():
            corruption_reset()
        return
    corruption_update(strength)
    corruption_render(MUTABLE_PROCESSING_BUFFER)
```

### 7.5 Private random generators and the wiring check

`_validate_effect_wiring()` never raises: a bad value is reported and replaced by a safe default so the booth keeps running.

```python
# =========================================================================
# PRIVATE RANDOM GENERATORS AND WIRING CHECK
# =========================================================================


def seed_private_rngs(seed):
    """Reseed every effect that owns a private random generator (tests / A-B harness)."""
    _MOSH.reseed(seed)


def _validate_effect_wiring():
    """Check the Phase 2 tunables. Never raises: a bad value is reported and replaced by
    its safe default so the booth keeps running (readme R14). Returns the messages."""
    global DATAMOSH_KNOB, PACKET_CORRUPTION_KNOB, MOSH_STAGE_POSITION, MOSH_BLEED_MODE
    global MOSH_HOLD_MIN_FRAMES, MOSH_HOLD_MAX_FRAMES, MOSH_VECTOR_PX
    notes = []

    def knob_ok(v):
        return v is None or (isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= 5)

    if not knob_ok(DATAMOSH_KNOB):
        notes.append(f"DATAMOSH_KNOB={DATAMOSH_KNOB!r} invalid, using 2")
        DATAMOSH_KNOB = 2
    if not knob_ok(PACKET_CORRUPTION_KNOB):
        notes.append(f"PACKET_CORRUPTION_KNOB={PACKET_CORRUPTION_KNOB!r} invalid, using None")
        PACKET_CORRUPTION_KNOB = None
    if MOSH_STAGE_POSITION not in (
        "before_drift",
        "after_melt_own_motion",
        "after_melt_pre_melt_motion",
    ):
        notes.append(f"MOSH_STAGE_POSITION={MOSH_STAGE_POSITION!r} invalid, using 'before_drift'")
        MOSH_STAGE_POSITION = "before_drift"
    if MOSH_BLEED_MODE not in ("shape_live_colour_held", "shape_held_colour_live", "off"):
        notes.append(f"MOSH_BLEED_MODE={MOSH_BLEED_MODE!r} invalid, using 'shape_live_colour_held'")
        MOSH_BLEED_MODE = "shape_live_colour_held"
    if not (1 <= MOSH_VECTOR_PX <= 8):
        notes.append(f"MOSH_VECTOR_PX={MOSH_VECTOR_PX} outside 1-8, using 4")
        MOSH_VECTOR_PX = 4
    if not (1 <= MOSH_HOLD_MIN_FRAMES <= MOSH_HOLD_MAX_FRAMES <= 60):
        notes.append("MOSH_HOLD_*_FRAMES must satisfy 1 <= min <= max <= 60, using 6 and 16")
        MOSH_HOLD_MIN_FRAMES, MOSH_HOLD_MAX_FRAMES = 6, 16

    if (
        DATAMOSH_KNOB is not None
        and PACKET_CORRUPTION_KNOB is not None
        and DATAMOSH_KNOB == PACKET_CORRUPTION_KNOB
    ):
        notes.append(
            f"NOTE: Data Mosh and Packet Corruption both listen to knob {DATAMOSH_KNOB}"
        )
    for msg in notes:
        print(f"[EFFECT WIRING] {msg}")
    return notes


_validate_effect_wiring()
```

## 8. Pipeline placement and removal of the old corruption coupling

### Position definitions

- **A — `before_drift` (default):** today's position, after Chroma Shift and before Signal Drift.
- **B — `after_melt_own_motion`:** after Melt; motion is measured on the picture Data Mosh receives (so Melt's animation can trigger holds).
- **C — `after_melt_pre_melt_motion`:** held picture after Melt, but motion samples are taken just before Melt (390 samples × 3 bytes, cheap).

With Signal Drift and Melt off, all three positions produce byte-identical output (checked by T10).

### Pipeline edits

1. In `apply_glitch_pipeline`, the global line becomes `global WARMUP_COUNTER, LAST_TEMPORAL_PURGE` (the corruption arrays are no longer assigned there).
2. The whole inline block starting at `# DIGITAL CORRUPTION GRID STATE EVALUATION ENGINE (DRIVEN BY KNOB 2)` and ending just before `# PIXEL DAMAGE (KNOB 3)` is replaced by one call at the same stage:

```python
    # PACKET CORRUPTION (STANDALONE; UNWIRED UNLESS PACKET_CORRUPTION_KNOB IS SET)
    # =========================================================================
    apply_packet_corruption(
        pkt_knobs[PACKET_CORRUPTION_KNOB] if PACKET_CORRUPTION_KNOB is not None else 0,
        current_time,
    )
```

3. The warm-up block loses its `DATAMOSH_PREV_FRAME` line, the 90 s purge loses its two Data Mosh lines (Data Mosh has no stale reference to refresh), and the old "KNOB 2 routing block" becomes the position-A call:

```python
    # TEMPORAL REGISTERS (WARM-UP / PURGE)
    # =========================================================================
    cv2.cvtColor(MUTABLE_PROCESSING_BUFFER, cv2.COLOR_BGR2GRAY, dst=SHARED_GRAY_BUFFER)

    if is_warming_up:
        PREV_FRAME_BUFFER[:] = MUTABLE_PROCESSING_BUFFER
        WARMUP_COUNTER += 1

    if (current_time - LAST_TEMPORAL_PURGE) > 90.0:
        PREV_FRAME_BUFFER[:] = MUTABLE_PROCESSING_BUFFER
        LAST_TEMPORAL_PURGE = current_time

    # === DATA MOSH (KNOB = DATAMOSH_KNOB), POSITION "before_drift" ===
    # =========================================================================

    if (
        not is_warming_up
        and DATAMOSH_KNOB is not None
        and MOSH_STAGE_POSITION == "before_drift"
    ):
        apply_data_mosh(pkt_knobs[DATAMOSH_KNOB], current_time)
```

4. At the Melt stage, the sampling hook for position C and the position-B/C call:

```python
    # STAGE 6: GRAVITATIONAL MELT DEFORMATION (KNOB 4)
    # =========================================================================
    mosh_samples = None
    if (
        not is_warming_up
        and DATAMOSH_KNOB is not None
        and MOSH_STAGE_POSITION == "after_melt_pre_melt_motion"
        and n(pkt_knobs[DATAMOSH_KNOB]) ** 2 >= MOSH_DORMANT_BELOW
    ):
        mosh_samples = data_mosh_take_samples(MUTABLE_PROCESSING_BUFFER)

    apply_melt_pass(n(pkt_knobs[4]), current_time)

    # === DATA MOSH, POSITIONS "after_melt_*" ===
    # =========================================================================
    if (
        not is_warming_up
        and DATAMOSH_KNOB is not None
        and MOSH_STAGE_POSITION
        in ("after_melt_own_motion", "after_melt_pre_melt_motion")
    ):
        apply_data_mosh(pkt_knobs[DATAMOSH_KNOB], current_time, mosh_samples)
```

The warm-up counter still advances exactly once per frame. Data Mosh is called at most once per frame, at exactly one position.

## 9. UI label scope

- **`pygame_display.py` line 136:** `(2, "Data Corruption")` becomes `(2, "Data Mosh")`. Nothing else in the list is touched.
- **`index.html` line 305:** `<div class="row">Data Corruption <span id="k2"></span></div>` becomes `<div class="row">Data Mosh <span id="k2"></span></div>`. Row order, ids and CSS are untouched.
- **`telemetry.py` is not changed.** Until Phase 5 the website's statistics will still name knob-2 usage `data_corruption` (accepted in P2-00).

## 10. Test plan and results

### A/B harness (`ab_regression.py`, adapted from Phase 1)

- Focus knob is now 2. **Dormant cases** (knob 2 at raw 0 and 20) cover every other knob alone, all-others busy, random mixes and the raw-20 boundary, on four frame types — Melt is now allowed to be busy.
- **Negative control:** with knob 2 active, old and new must differ (otherwise the comparison proves nothing).
- The harness calls `seed_private_rngs(seed)` before each step when it exists; the old sync list for the removed sign pool is empty.

### New test script `test_mosh_phase2.py` (no hardware, no background workers)

| Test | What it requires |
|---|---|
| T1 Independence | Data Mosh state and output identical with and without corruption running between frames, and vice versa; Data Mosh consumes no numpy-global or OpenCV random numbers; neither effect's source text mentions the other |
| T2 Corruption identity | The extracted corruption equals the old inline stage byte for byte (old Data Mosh switched off so the coupling is inert), over several knob values and frame types; fails if no damage ever appears |
| T3 Data Mosh identity | The optimised engine equals a plain, slow reference implementation byte for byte: three bleed modes, busy / sparse / every-cell-moving scenes, several strengths and seeds |
| T4 Reset | After holds exist, `reset()` leaves no timers, vectors or remembered picture and later frames equal a fresh engine's |
| T5 First frame | The first frame after start or reset is never modified |
| T6 Duplicates | A repeated camera frame creates or renews no hold |
| T7 Sweep | Knob values 0…1023 (and 0, 1, 20, 21, 144, 145, 1022, 1023) on both effects: no exception, columns 624–639 untouched |
| T8 Allocation | No frame-sized allocation per frame (limit 256 KiB against a 900 KiB frame) |
| T9 Cancel | Raw 144 dormant, raw 145 active; lowering the knob cancels at once; also through the real pipeline |
| T10 Wiring | Default wiring; corruption on knob 2 and on knob 3; shared knob 2; the three positions agree with drift and melt off; melt-on smoke test per position; invalid settings fall back safely |

I also broke the code on purpose three ways in memory (a corruption constant changed by 0.01, Data Mosh drawing one global random number, `reset()` forgetting the timers) and confirmed each is caught.

### Results (scratch checkout, x86, synthetic frames)

```
 [PASS] T1 independence (state, random draws, source)
 [PASS] T2 corruption identical to old inline stage - 2640 damaged-cell observations compared
 [PASS] T3 optimised Data Mosh == plain reference - 6480 frames compared
 [PASS] T4 reset leaves no residue - 71 holds existed before reset
 [PASS] T5 first frame after start / reset is untouched
 [PASS] T6 duplicate camera frames create no holds - 30 duplicates checked
 [PASS] T7 knob sweep: no exception, right edge untouched - 86 knob values, 1032 calls
 [PASS] T8 no frame-sized allocation per frame - worst extra allocation 17 KiB (limit 256 KiB; one frame = 900 KiB)
 [PASS] T9 lowering the knob cancels all moshing at once - raw 144 dormant, raw 145 active; cancel is immediate and clean
 [PASS] T10 wiring: any-knob corruption, positions agree, safe fallbacks
 passed 10, failed 0, skipped 0
 RESULT: PASS
```

```
 Self-test (A/A, two copies of NEW): identical
 Dormant cases compared : 84  (differing: 0)
 Cases that altered the input frame: 84/84
 Knob-2-active cases that differ   : 4/4
 RESULT: PASS - the phase changed nothing outside its own effect
```

The patch was also applied to a fresh checkout of the `pre-phase2` state and both scripts were re-run there with the same results.

### Benchmark script `test_mosh_benchmark.py` (advisory)

Prints cost per call and the longest GIL-held stretch for old and new, for busy / sparse / every-cell-moving scenes at raw 300 / 600 / 1023. Run it on the Pi with the kiosk stopped.

## 11. Performance evidence (x86 sandbox, one core — **not Pi results**)

```
 cost = ms per call (median / p95 / max)   stretch = longest run without an OpenCV call (ms, mean / worst)
 scene   knob | version |                   cost |      GIL stretch | held cells
 busy     300 | OLD     |   1.14 /   1.64 /   1.75 |   0.97 /   1.46 |   40.2
 busy     300 | NEW     |   2.97 /   5.24 /   6.86 |   0.45 /   2.43 |   32.8
 busy     600 | OLD     |   1.52 /   2.01 /   2.23 |   1.27 /   1.77 |   52.4
 busy     600 | NEW     |   2.80 /   4.03 /   5.14 |   0.34 /   0.70 |   60.2
 busy    1023 | OLD     |   2.35 /   2.96 /   3.31 |   2.10 /   2.87 |  100.5
 busy    1023 | NEW     |   2.55 /   3.93 /   4.19 |   0.29 /   0.48 |   91.3
 sparse   300 | OLD     |   0.41 /   0.70 /   0.71 |   0.25 /   0.40 |    2.4
 sparse   300 | NEW     |   1.28 /   1.51 /   1.96 |   0.21 /   0.87 |    4.4
 sparse   600 | OLD     |   0.59 /   1.15 /   1.22 |   0.44 /   0.80 |   11.2
 sparse   600 | NEW     |   1.30 /   1.67 /   2.23 |   0.22 /   0.44 |   14.8
 sparse  1023 | OLD     |   0.99 /   1.30 /   1.80 |   0.77 /   1.31 |   29.8
 sparse  1023 | NEW     |   1.39 /   1.62 /   2.13 |   0.23 /   0.46 |   26.5
 chaos    300 | OLD     |   8.52 /  13.58 /  13.96 |   9.09 /  13.58 |  340.8
 chaos    300 | NEW     |   5.86 /   7.12 /   7.42 |   0.61 /   0.93 |  389.9
 chaos    600 | OLD     |   9.33 /  12.85 /  13.55 |   9.29 /  12.95 |  363.0
 chaos    600 | NEW     |   5.26 /   6.36 /   6.63 |   0.61 /   1.22 |  390.0
 chaos   1023 | OLD     |  12.57 /  15.91 /  28.98 |  11.60 /  28.98 |  389.8
 chaos   1023 | NEW     |   4.47 /   6.95 /  14.10 |   0.51 /   1.23 |  390.0
```

- **Cost:** milliseconds per call (median / p95 / max). In this run the new stage costs more than the old one in light scenes (about 1.3 ms against 0.4 ms sparse, about 3 ms against 1.1 ms busy at raw 300 — a fixed set of large operations), is about equal in a busy scene at maximum (2.6 against 2.4 ms) and costs one third to two thirds as much when every cell moves (4.5–5.9 ms against 8.5–12.6 ms). The sandbox is shared: an earlier quiet run gave lower absolute numbers (new about 1.7–1.8 ms busy, about 2.8 ms every-cell-moving; old about 2.2 and 5.5 ms), so compare old and new **within one run**, never across runs.
- **GIL stretch:** the longest run without an OpenCV call. OpenCV calls release the GIL, so this is an upper bound on how long the display thread can be kept waiting (NumPy's large copies also release it, so the real figure is lower still). The thread-switch interval is 5 ms. The old effect exceeds it whenever many cells move (9–12 ms mean, 29 ms worst frame in the every-cell-moving scene) — the same mechanism that made the old Melt stall the display's frame copy — while the new effect stays at about 0.3–0.6 ms mean (worst 2.4 ms on one noisy frame).
- **Python calls per frame at raw 1023:** about 104 against about 485 for the old effect in the busy scene, and about 100 against about 1,960 with every cell moving (counted with `sys.monitoring`; operator syntax is not counted, so both are undercounts).
- **Allocation:** 17 KiB per frame for the new effect (T8), the same order as the old effect; no frame-sized allocation. New state is about 10 MiB; about 3 MiB of old buffers are freed.
- **Honest limits:** a wake-up probe in the one-core sandbox could not separate GIL waits from CPU contention, so no measured GIL gain is claimed until the Pi run. Standalone corruption costs 0.3–0.6 ms on average at up to ~90 active cells, so extracting it without optimising it is acceptable while it is unwired.

## 12. Deviations from earlier text, and items found but not touched

### Deviations from the P2-00 / P2-01 wording

- The "capture previous picture" step is replaced by an always-complete hold memory (section 3). Same look, plus the border-fringe fix.
- The drag weight is exactly the old effect's 161/256 rather than 0.63.
- The extra input copy is made only when at least one cell is held.
- Memory numbers are updated to the final code (about 10 MiB new, about 3 MiB freed).

### Found, not touched (optional later proposal P2-02-style cleanup)

`SHARED_GRAY_BUFFER` and `PREV_FRAME_BUFFER` are written every frame and never read; the full-frame grayscale conversion feeding the first costs a little every frame. Removing them is a lossless saving but outside this proposal.

## 13. Live comparison and booth procedure

Use the live booth scene, one change at a time, and restart the service between variants. Keep all knobs except knob 2 at zero for the first pass.

1. **Strength pass at position A.** Raw 150, 300, 512, 700, 900, 1023. For each, record **Keep / Adjust / Reject**, whether the effect is immediately visible, whether the held colours remain legible, and whether it feels at least as strong as the old effect at maximum.
2. **Tune** `MOSH_HOLD_MIN_FRAMES` / `MOSH_HOLD_MAX_FRAMES` first (section 4), then `MOSH_VECTOR_PX`; keep the default bleed mode unless you want a much subtler look.
3. **Position pass.** Repeat with `MOSH_STAGE_POSITION = "after_melt_pre_melt_motion"` (C), then B only if the placement question is still open. With Melt at maximum on a static scene, B can hold cells because Melt animates; A and C should not.
4. **Cancel check.** Raise knob 2, then drop it to zero: everything must vanish at once.
5. **Packet Corruption check (optional).** Set `PACKET_CORRUPTION_KNOB` to a knob index, restart, confirm it behaves as before and does not change Data Mosh; set it back to `None`.
6. **Normal checks.** Countdown flash, capture, print, upload, labels on the kiosk and `index.html`, and the usual 10-minute smoke test.

## 14. Scope, rollback, sign-off and open items

### In scope

`server.py` (Data Mosh engine, Packet Corruption extraction, wiring constants, tunables block, pipeline hooks, removal of the old fused effect), the two label lines, adaptation of `ab_regression.py`, and the two new scripts `test_mosh_phase2.py` and `test_mosh_benchmark.py`.

### Out of scope

Phase 3 fusion, governor weights, telemetry migration, effect registry, other effects, capture/printing/QR/upload, audio, display rotation, website changes, and any git commit, tag, merge or push by the assistant.

### Applying (owner runs these)

```
cd ~/glitchbooth && git checkout dev && git status      # must be clean
git apply --check phase2_P2-01.patch && git apply phase2_P2-01.patch
python3 test_mosh_phase2.py --quick
python3 ab_regression.py --ref pre-phase2
python3 test_mosh_benchmark.py                           # kiosk stopped
```

If `git apply --check` complains, send the error and the patch will be regenerated against the current files.

### Rollback

Stop the service and return to the `pre-phase2` tag with the owner's own git procedure; the two new scripts are simply deleted. Nothing outside the listed files is touched.

### Sign-off

Accept Phase 2 only after: all invariant tests pass on the Pi; the A/B guard passes; strength at raw 1023 is acceptable on the owner's scene (after decision D-S); the position comparison is reviewed; Pi performance is recorded with no unacceptable display latency; labels are correct; and the 10-minute smoke test passes.

### Not verified

Everything on the Pi (cost, display latency, temperature); real camera frames (all tests use synthetic scenes); the look on the booth; whether the labels render as expected on the kiosk and web page.

### Open items

- **D-S (strength default):** keep 6–16 or change to 10–26 (or 8–22). Recommendation: 10–26, then tune live.
- **D-M (knob 2 after the overhaul):** this proposal gives all of knob 2 to Data Mosh now. The readme's final mapping lists a different effect on knob 2; if Data Mosh should keep knob 2 after the overhaul, that is a readme / Phase 4–6 mapping edit.
- **D-P (default position):** A is the default; confirm after the live A/C comparison.

## Approval requested

Confirm this document as the readable record of **P2-01** and decide **D-S**. The code is verified only in scratch (10 of 10 tests, 84 of 84 byte-identical dormant A/B cases, 4 of 4 active cases different); **no Pi measurement, no real-camera test and no booth viewing has been done.** Apply, test on the Pi, then judge the look live.
