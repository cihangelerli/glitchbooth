# GLITCH BOOTH — Phase 3 Proposal P3-01
## Effect handlers, free knob attachment, editable processing order, knob windows, naked capture

**Status:** PROPOSED, not applied. Implemented and verified in a scratch checkout built from your latest `server.py` (`server_329e02.py`). Nothing on the booth and nothing in your repository has been changed.  
**Target:** Raspberry Pi 5 1 GB, 640×480 BGR pipeline, 10–15 FPS.  
**Baseline:** git tag `pre-phase3` on `dev` (you create it in step 0, on your current tuned state).  
**Files touched:** `server.py`, `pygame_display.py`, `index.html`, `.gitignore`. **New file:** `ab_regression_phase3.py` (full code in Appendix A). **Not touched:** `telemetry.py`, `StatsDashboard.tsx`, the service file, audio.  
**For an AI assistant applying this document:** apply the steps in order. Every FIND text must match exactly once; if one does not, stop and report it instead of guessing. After the steps run the compile check and the harness (section 5, steps 12 and 13). Do not commit, tag or push; the owner does that.

---

## 0. At a glance

| | |
|---|---|
| **What changes for visitors** | Nothing, except: Packet Corruption now runs **after Data Mosh and just before ASCII Matrix**, so packets lie on top of the other effects instead of being pixellated, shifted or melted. With Packet Corruption quiet, the picture is **byte-identical** to today (tested). A naked photo is saved with every capture. |
| **What changes for you** | Three settings blocks in `server.py` decide everything: the processing order (`EFFECT_ORDER`), which effects each knob drives (`KNOB_ATTACHMENTS`, first = primary, shown as the knob's name, the rest hidden) and an optional knob range per effect (`EFFECT_KNOB_WINDOW`). Change them, restart the service, try the combination. |
| **Risk** | Low for the wrapping (proved byte-identical to your tuned file in the old order, 104 of 104 cases), medium for the capture path (it cannot be exercised without the hardware, so it needs your booth check). |
| **Size** | `server.py`: about 270 lines of effect code move into functions without any change, about 170 new lines (pool, plan builder with validation, window and governor helpers, the new pipeline), about 25 lines for the naked capture; small edits in the kiosk and the web page. |

## 1. Revised master plan (as of this proposal)

| Phase | Content | Status |
|---|---|---|
| 1 | Melt, vectorized | done |
| 2 | Data Mosh v2 and independent Packet Corruption | done, on the booth, tuned by you |
| **3** | **This proposal:** effect handlers, free attachment (primary and secondary), editable order, knob windows, per-effect governor weights, labels from the configuration, naked capture | proposed |
| 4 | Telemetry update: count **every** attached effect by name (primary and hidden), plus a short wiring label per capture | planned |
| 5 | Posterize | planned |
| 6 | Code housekeeping, further tuning, release hardening | planned |

**Dropped:** the Packet Corruption + Pixellation fusion (independent effects plus an editable order make it unnecessary); the idea of a separate `effects_config.py` (the configuration lives in the `server.py` settings blocks, like every other tunable); `MOSH_STAGE_POSITION`.  
**Parked until the overhaul is finished:** Data Mosh fixes and tuning (the weak top of the knob, release settings for the 16×12 cells, mosh-cell life through the 3-second pose countdown).  
**Test policy:** on-device testing is the main test for future phases. Only the A/B harness is updated per phase (new `ab_regression_phaseN.py`) and used with a git tag taken right before the phase. A FAIL caused by tuning between phases is not important (section 6).

## 2. Decisions recorded from you

1. Processing order, top to bottom (later = on top): Pixellation, Chroma Shift, Signal Drift, Melt, Data Mosh, Packet Corruption, ASCII Matrix; the CRT overlay is always on and always last.
2. Default attachments: Pixellation → knob 3, Chroma Shift → knob 5 (primary), Signal Drift → knob 1, Melt → knob 4, Data Mosh → knob 2, Packet Corruption → knob 5 (secondary, hidden), ASCII Matrix → knob 0.
3. Six knobs, numbered 0 to 5. The first effect on a knob is its primary and gives the name shown on the kiosk and on the web page; the others are secondary and never shown.
4. Each effect can get its own range on a shared knob (for example Chroma Shift 0–70 %, Packet Corruption 35–100 %); this is part of Phase 3.
5. Free attachment: any effect on any knob; effects that are attached to no knob are skipped and cost nothing.
6. Telemetry is updated in Phase 4 and counts hidden effects too; Phase 3 does not touch it.
7. Naked capture is part of Phase 3: folder `captures_naked`, file `<same id>_naked.jpg`, no upload, no disk growth control, not tracked by git; a reminder about the on-device storage path is in step 10.
8. The CRT scanline overlay is not a mutation; it stays always on.

## 3. Things I assumed or found while checking — please confirm or change

| # | Item | What I did (default) |
|---|---|---|
| Q1 | **Load governor.** Today's weights belong to knob numbers (knob 4 = 0.42, knob 2 = 0.27, ...). With free attachment, moving Melt to another knob would silently lower the protection against heavy load. | The weights now belong to the effects (`EFFECT_COST_WEIGHT`) and follow them to whatever knob they sit on. The values are exactly today's, so the result is the same as before (largest difference 2.2e-16 in 200 random tests). Packet Corruption starts at 0.0 because it was never counted; raise it if you find it costs too much. |
| Q2 | **Data Mosh position settings.** With an order list, `MOSH_STAGE_POSITION` (A/B/C) and the pre-Melt sampling hook have no job. | They are removed. Data Mosh runs where `EFFECT_ORDER` puts it (position B today). Position C is not available any more; the unused helper `data_mosh_take_samples` stays until Phase 6. |
| Q3 | **One effect, one knob.** | An effect listens to one knob only; if it is listed twice, the lower knob wins and a notice is printed at start-up. |
| Q4 | **Names.** | Names on screen stay as they are today: knob 3 shows "Pixel Damage" (the effect is called Pixellation in this document). Change them in `EFFECT_LABELS`. |
| Q5 | **`/delete_capture` (gallery delete)** removes the processed files only. | The naked file stays (device only, as you asked). Tell me if deleting a capture should also delete its naked file. |
| Q6 | **Telemetry until Phase 4** still counts by knob number. | Knob 2 is still counted as "data_corruption" (since Phase 2), hidden effects are not counted and re-attachments are not reflected. Do not read the statistics between Phase 3 and Phase 4 as meaningful. |
| Q7 | **Row order** on the kiosk and the web page (ASCII, Drift, Pixel, Melt, Data Mosh, Chroma). | Fixed as today; only the names follow the attachments. A knob with nothing attached shows `-`. |
| Q8 | **Windows.** | Empty by default (every effect uses the whole knob). The example values are only in a comment; set them after trying them on the booth. |
| Q9 | **Files I have not seen.** I only have the pre-Phase-2 versions of `pygame_display.py` and `index.html`, and no `.gitignore`. | Steps 9 and 11 are anchored on those versions and explain how to adapt if your text differs (for example a different name in a row). Upload the current files and I will re-anchor. |
| Q10 | **Changing a configuration needs a restart** of the service. | Yes, like every other tunable. `set_effect_config()` exists for experiments in a Python shell and for the A/B harness; nothing in the booth calls it. |
| Q11 | **Posterize (Phase 5).** | It becomes one handler plus entries in `EFFECT_HANDLERS`, `EFFECT_ORDER`, `EFFECT_LABELS` and `EFFECT_COST_WEIGHT`, then an attachment. Its place in the order is decided then. |

## 4. Design

### 4.1 Processing order: later = on top

The pipeline runs the effects top to bottom, and every effect works on the result of the ones above it. So an effect that is **later** in the list lies **on top**, and effects below it cannot touch it. That is what you want for Packet Corruption: after Pixellation, Chroma Shift, Drift, Melt and Data Mosh, but before ASCII, which blends over everything and is the last effect. The CRT overlay is applied after all of them.

| # | Effect id | Attached to | Role | Name shown | Cost weight |
|---|---|---|---|---|---|
| 1 | `pixellation` | knob 3 | primary | Pixel Damage | 0.07 |
| 2 | `chroma_shift` | knob 5 | primary | Chroma Shift | 0.07 |
| 3 | `signal_drift` | knob 1 | primary | Signal Drift | 0.07 |
| 4 | `melt` | knob 4 | primary | Melt | 0.42 |
| 5 | `data_mosh` | knob 2 | primary | Data Mosh | 0.27 |
| 6 | `packet_corruption` | knob 5 | secondary | hidden | 0.00 |
| 7 | `ascii_matrix` | knob 0 | primary | ASCII Matrix | 0.10 |
| 8 | CRT overlay | always on | — | — | — |

Today's order differs in one place only: Packet Corruption runs **first**, so everything after it mutates its packets. Everything else keeps its position.

ASCII blends with the knob value (at 50 % half ASCII, half the picture below it), so packets stay crisp at low ASCII values and turn into characters as the ASCII knob rises. The CRT overlay is applied last to everything, packets included.

### 4.2 Handler contract

Every effect is a function `handler(raw_knob, current_time)`: `raw_knob` is 0–1023 and has already passed through the effect's window; the handler edits `MUTABLE_PROCESSING_BUFFER` in place and reads nothing else from the outside. Data Mosh, Packet Corruption and Melt already were handlers (Melt gets a thin wrapper because its engine takes a 0–1 strength). The four inline stages of `apply_glitch_pipeline` (Pixellation, Chroma Shift, Signal Drift, ASCII Matrix) move into their own functions **without any change of logic**. I checked your latest file two ways before choosing this: no variable flows from one inline stage to another, and no module-level buffer is shared between them, so the move is mechanical.

### 4.3 Attachments: primary and secondary

`KNOB_ATTACHMENTS = {knob: [effects]}`. The first effect on a knob is its **primary** and gives the knob's name; the others are **secondary** and are never shown. Both kinds react to the same knob value. Effects that are in `EFFECT_ORDER` but attached to no knob are skipped and cost nothing, so the pool can be larger than the six knobs.

### 4.4 Knob windows

`EFFECT_KNOB_WINDOW = {effect: (low, high)}` in fractions of the knob travel. Inside its window an effect sees the full 0–1023 range, outside it is off:

`effect_value = clamp((knob/1023 − low) / (high − low), 0, 1) × 1023`

| Example, knob 5 | at 20 % | at 35 % | at 60 % | at 70 % | at 100 % |
|---|---|---|---|---|---|
| Chroma Shift (0.0, 0.7) | 29 % | 50 % | 86 % | 100 % | 100 % |
| Packet Corruption (0.35, 1.0) | off | off | 38 % | 54 % | 100 % |

Without a window the effect receives the knob value untouched, which is why the default behaviour is byte-identical to today's.

### 4.5 Load governor

`effect_intensity` (it chooses the FPS tier and the JPEG quality) is now the sum over all attached effects of `cost weight × windowed knob strength`, limited to 0–1. The default weights reproduce today's per-knob numbers.

### 4.6 Warm-up, housekeeping and CRT

Data Mosh still does not run during the 10 start-up warm-up frames (`EFFECT_SKIP_DURING_WARMUP`). The small housekeeping block (warm-up counter, 90 s purge, the unused `PREV_FRAME_BUFFER` / `SHARED_GRAY_BUFFER` writes) stays as it is, only moved to the start of the pipeline; it does not change the picture and is cleaned up in Phase 6. The CRT overlay is its own function, called last, still switched by `CRT_ENABLED`.

### 4.7 Names on the kiosk and the web page

`get_knob_labels()` returns the six names (primary effect of each knob). The kiosk builds its rows from it at start-up. The web page receives the names in the `/state` answer it already polls and writes them into the rows, so the names follow the configuration without editing HTML. An old server without `labels` leaves the page untouched.

### 4.8 Naked capture

- **What:** the unprocessed camera frame that produced the processed photo (the exact frame of the loop pass that makes the capture, at the end of the 3-second pose countdown, not at the button press).
- **Where and how:** `captures_naked/<same session id>_naked.jpg`, written by the existing background storage thread before it writes the normal files, through a helper that never raises. A failure is logged and can never mark the session as failed or block printing and upload.
- **Cost:** one 0.9 MB copy per loop pass, only during the pose countdown.
- **Same orientation as the processed photo** (unmirrored; mirroring happens only on the display), so pairs line up.
- **Device only:** not uploaded, not served, not pruned, not tracked by git. Switch: `NAKED_CAPTURE_ENABLED`.

### 4.9 Recipes for trying combinations

All of them are edits of the settings block at the top of `server.py`, followed by `sudo systemctl restart glitchbooth`.

| Try this | Edit |
|---|---|
| Packets under everything again (like Phase 2) | In `EFFECT_ORDER` move `"packet_corruption"` to the first line |
| Packets also on knob 3 instead of 5 | `3: ["pixellation", "packet_corruption"]` and `5: ["chroma_shift"]` |
| Packets become the knob's name on knob 5 | `5: ["packet_corruption", "chroma_shift"]` |
| Chroma 0–70 % and packets 35–100 % on knob 5 | `EFFECT_KNOB_WINDOW = {"chroma_shift": (0.0, 0.7), "packet_corruption": (0.35, 1.0)}` |
| Take an effect off the booth without deleting it | Remove it from `KNOB_ATTACHMENTS` (it stays in the pool) |
| Melt on knob 0 | Swap the attachments; the governor follows Melt's weight |

If a setting is wrong (unknown effect, bad knob number, bad window), the server prints `[EFFECT CONFIG] ...`, drops that entry and keeps running.

## 5. Step-by-step procedure

Text to find is quoted exactly as it is in `server_329e02.py` (line numbers are those of that file and are only a guide). Work on branch `dev`, with the kiosk stopped.

### Step 0 — Safety net (once)

```
cd ~/glitchbooth
git checkout dev
git status                          # must be clean
git tag pre-phase3                  # baseline for the A/B test (local tag; push it if you want it elsewhere)
sudo systemctl stop glitchbooth     # while editing and testing
```

### Step 1 — `server.py`: the Phase 3 settings block

This is the block you edit to try combinations (section 4.9). It replaces the Phase 2 wiring constants `DATAMOSH_KNOB`, `PACKET_CORRUPTION_KNOB` and `MOSH_STAGE_POSITION` (and the comment above the last one); every other Data Mosh and Packet Corruption setting below stays exactly as you tuned it. **Keep your tuned `MOSH_*` and `CORRUPTION_*` values** — the replacement text ends right where `MOSH_DORMANT_BELOW` begins.

**S1 · Tunables: replace the Phase 2 wiring constants with the Phase 3 block** — `server.py`, lines 191–202 of the file I read (12 lines).

FIND a block: it starts with the line `# PHASE 2 TUNABLES: EFFECT WIRING, DATA MOSH, PACKET CORRUPTION (readme R7)` and ends with the line `MOSH_STAGE_POSITION = "after_melt_own_motion"`. Select the whole block.

The block as it is today:

```python
# PHASE 2 TUNABLES: EFFECT WIRING, DATA MOSH, PACKET CORRUPTION (readme R7)
# ==============================================================================
# --- wiring: which knob (0-5) drives which effect, or None = not connected -------
DATAMOSH_KNOB = 2
PACKET_CORRUPTION_KNOB = 5  # standalone effect, ready: set to a knob index to connect it, "None" if not connected, 0-5 to connect to a knob

# --- Data Mosh -------------------------------------------------------------------
# Where in the pipeline it runs:
#   "before_drift"               today's position (after chroma, before Signal Drift)
#   "after_melt_own_motion"      after Melt, motion measured on its own input
#   "after_melt_pre_melt_motion" after Melt, motion measured on the picture before Melt
MOSH_STAGE_POSITION = "after_melt_own_motion"
```

REPLACE THE WHOLE BLOCK WITH:

```python
# PHASE 3 TUNABLES: EFFECT ORDER, KNOB ATTACHMENTS, WINDOWS, COST WEIGHTS, LABELS
# ==============================================================================
# Every effect is a handler. EFFECT_ORDER is the processing order, top to bottom:
# an effect works on the result of the effects above it, so LATER = ON TOP.
# An effect that is not attached to any knob is skipped and costs nothing.
# The CRT scanline overlay always runs last and is not part of this list.
EFFECT_ORDER = [
    "pixellation",
    "chroma_shift",
    "signal_drift",
    "melt",
    "data_mosh",
    "packet_corruption",
    "ascii_matrix",
]

# Knob (0-5) -> the effects it drives. The FIRST effect is the primary: its name is the
# knob's name on the kiosk screen and on the web page. The others are secondary: they react
# to the same knob but are not shown. An effect listens to ONE knob only (if it is listed
# on two knobs, the lower knob wins and a notice is printed at start-up).
KNOB_ATTACHMENTS = {
    0: ["ascii_matrix"],
    1: ["signal_drift"],
    2: ["data_mosh"],
    3: ["pixellation"],
    4: ["melt"],
    5: ["chroma_shift", "packet_corruption"],
}

# Optional range of the knob in which an effect works, as fractions of the knob travel.
# Inside its window the effect sees the full 0-1023 range; outside it is off.
# Example: {"chroma_shift": (0.0, 0.7), "packet_corruption": (0.35, 1.0)}
EFFECT_KNOB_WINDOW = {}  # empty = every effect uses the whole knob

# Cost weight of each effect for the load governor (FPS tier and JPEG quality).
# These are today's per-knob weights moved onto the effects, so the governor follows an
# effect when you re-attach it. Packet Corruption starts at 0.0 because it was never counted.
EFFECT_COST_WEIGHT = {
    "ascii_matrix": 0.10,
    "signal_drift": 0.07,
    "data_mosh": 0.27,
    "pixellation": 0.07,
    "melt": 0.42,
    "chroma_shift": 0.07,
    "packet_corruption": 0.0,
}

# Effects that must not run during the start-up warm-up frames (as Data Mosh today).
EFFECT_SKIP_DURING_WARMUP = ("data_mosh",)

# Names shown on the kiosk and the web page (the primary effect of each knob).
EFFECT_LABELS = {
    "ascii_matrix": "ASCII Matrix",
    "signal_drift": "Signal Drift",
    "data_mosh": "Data Mosh",
    "pixellation": "Pixel Damage",
    "melt": "Melt",
    "chroma_shift": "Chroma Shift",
    "packet_corruption": "Packet Corruption",
}
KNOB_EMPTY_LABEL = "-"  # shown for a knob with no effect attached

# Naked capture: also save the unprocessed camera frame behind every capture,
# in NAKED_DIR as <session id>_naked.jpg. Device only: never uploaded, never pruned.
NAKED_CAPTURE_ENABLED = True

# PHASE 2 TUNABLES: DATA MOSH, PACKET CORRUPTION (readme R7)
# ==============================================================================

# --- Data Mosh -------------------------------------------------------------------
```

### Step 2 — `server.py`: the naked-capture folder and buffer

**S2 · Naked-capture folder next to the other capture folders** — `server.py`, near line 64 of the file I read.

FIND (appears exactly once):

```python
ARCHIVE_DIR = "captures_archive"
os.makedirs(ARCHIVE_DIR, exist_ok=True)
```

REPLACE WITH:

```python
ARCHIVE_DIR = "captures_archive"
os.makedirs(ARCHIVE_DIR, exist_ok=True)

# Unprocessed camera frame behind every capture: <session id>_naked.jpg. Device only,
# never uploaded, never pruned. (On the Pi this folder is a symlink to the data disk.)
NAKED_DIR = "captures_naked"
os.makedirs(NAKED_DIR, exist_ok=True)
```

**S3 · Preallocated buffer for the naked frame** — `server.py`, near line 431 of the file I read.

FIND (appears exactly once):

```python
UI_DISPLAY_BUFFER = np.empty((FRAME_H, FRAME_W, 3), dtype=np.uint8, order="C")
```

REPLACE WITH:

```python
UI_DISPLAY_BUFFER = np.empty((FRAME_H, FRAME_W, 3), dtype=np.uint8, order="C")
NAKED_FRAME_BUFFER = np.empty((FRAME_H, FRAME_W, 3), dtype=np.uint8, order="C")
```

### Step 3 — `server.py`: Data Mosh settings check

The old check also validated the knob and position constants that no longer exist; the version below keeps only the value checks. The function is renamed, so its one call must be renamed too.

**S4 · Data Mosh settings check: keep the value checks, drop the knob and position checks** — `server.py`, lines 1241–1299 of the file I read (59 lines).

FIND a block: it starts with the line `def _validate_effect_wiring():` and ends with the line `return notes`. Select the whole block.

The block as it is today:

```python
def _validate_effect_wiring():
    """Check the Phase 2 tunables. Never raises: a bad value is reported and replaced by
    its safe default so the booth keeps running (readme R14). Returns the messages."""
    global DATAMOSH_KNOB, PACKET_CORRUPTION_KNOB, MOSH_STAGE_POSITION, MOSH_BLEED_MODE
    global MOSH_HOLD_MIN_FRAMES, MOSH_HOLD_MAX_FRAMES, MOSH_VECTOR_PX
    notes = []

    def knob_ok(v):
        return v is None or (
            isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= 5
        )

    if not knob_ok(DATAMOSH_KNOB):
        notes.append(f"DATAMOSH_KNOB={DATAMOSH_KNOB!r} invalid, using 2")
        DATAMOSH_KNOB = 2
    if not knob_ok(PACKET_CORRUPTION_KNOB):
        notes.append(
            f"PACKET_CORRUPTION_KNOB={PACKET_CORRUPTION_KNOB!r} invalid, using None"
        )
        PACKET_CORRUPTION_KNOB = None
    if MOSH_STAGE_POSITION not in (
        "before_drift",
        "after_melt_own_motion",
        "after_melt_pre_melt_motion",
    ):
        notes.append(
            f"MOSH_STAGE_POSITION={MOSH_STAGE_POSITION!r} invalid, using 'before_drift'"
        )
        MOSH_STAGE_POSITION = "before_drift"
    if MOSH_BLEED_MODE not in (
        "shape_live_colour_held",
        "shape_held_colour_live",
        "off",
    ):
        notes.append(
            f"MOSH_BLEED_MODE={MOSH_BLEED_MODE!r} invalid, using 'shape_live_colour_held'"
        )
        MOSH_BLEED_MODE = "shape_live_colour_held"
    if not (1 <= MOSH_VECTOR_PX <= 8):
        notes.append(f"MOSH_VECTOR_PX={MOSH_VECTOR_PX} outside 1-8, using 4")
        MOSH_VECTOR_PX = 4
    if not (1 <= MOSH_HOLD_MIN_FRAMES <= MOSH_HOLD_MAX_FRAMES <= 60):
        notes.append(
            "MOSH_HOLD_*_FRAMES must satisfy 1 <= min <= max <= 60, using 6 and 16"
        )
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
```

REPLACE THE WHOLE BLOCK WITH:

```python
def _validate_mosh_settings():
    """Check the Data Mosh tunables. Never raises: a bad value is reported and replaced by
    its safe default so the booth keeps running (readme R14). Returns the messages."""
    global MOSH_BLEED_MODE, MOSH_HOLD_MIN_FRAMES, MOSH_HOLD_MAX_FRAMES, MOSH_VECTOR_PX
    notes = []

    if MOSH_BLEED_MODE not in (
        "shape_live_colour_held",
        "shape_held_colour_live",
        "off",
    ):
        notes.append(
            f"MOSH_BLEED_MODE={MOSH_BLEED_MODE!r} invalid, using 'shape_live_colour_held'"
        )
        MOSH_BLEED_MODE = "shape_live_colour_held"
    if not (1 <= MOSH_VECTOR_PX <= 8):
        notes.append(f"MOSH_VECTOR_PX={MOSH_VECTOR_PX} outside 1-8, using 4")
        MOSH_VECTOR_PX = 4
    if not (1 <= MOSH_HOLD_MIN_FRAMES <= MOSH_HOLD_MAX_FRAMES <= 60):
        notes.append(
            "MOSH_HOLD_*_FRAMES must satisfy 1 <= min <= max <= 60, using 6 and 16"
        )
        MOSH_HOLD_MIN_FRAMES, MOSH_HOLD_MAX_FRAMES = 6, 16

    for msg in notes:
        print(f"[MOSH SETTINGS] {msg}")
    return notes
```

**S4b · Call of the renamed check (module level, right after the function)** — `server.py`, near line 1241 of the file I read.

FIND (appears exactly once):

```python
_validate_effect_wiring()
```

REPLACE WITH:

```python
_validate_mosh_settings()
```

### Step 4 — `server.py`: the handlers, the pool, the plan builder and the new pipeline

This is the main step. The whole old `apply_glitch_pipeline` function (from its `def` line down to the last line of the CRT block, just before the comment `# HARDWARE OUTPUT EXECUTION STATIONS`) is replaced by the code below. It contains the four former inline stages as functions, copied without logic changes, the effect pool, the plan builder with its validation, the window and governor helpers, and the new `apply_glitch_pipeline`. Keep `def n(val)` above it; it is used by the handlers.

**S5 · Replace the whole apply_glitch_pipeline function by the Phase 3 block (handlers, pool, plan, new pipeline)** — `server.py`, lines 2403–2710 of the file I read (308 lines).

FIND a block: it starts with the line `def apply_glitch_pipeline(current_time, control_packet):` and ends with the line `)`. Select the whole block.

REPLACE THE WHOLE BLOCK WITH:

```python
# =========================================================================
# PHASE 3: EFFECT HANDLERS, ATTACHMENTS, ORDER AND PER-EFFECT KNOB WINDOWS
# =========================================================================
# Every effect is a handler with the same contract (readme R13):
#       handler(raw_knob, current_time)
# raw_knob is 0-1023 and already passed through the effect's knob window; the handler
# edits MUTABLE_PROCESSING_BUFFER in place and reads nothing but its own knob value.
# Data Mosh, Packet Corruption and Melt were already handlers; the four effects below
# are the former inline stages of apply_glitch_pipeline, moved here without changes.


def apply_pixellation(raw_knob, current_time):
    """Pixellation (shown as "Pixel Damage"): blocky compression tiles."""
    h, w = FRAME_H, FRAME_W
    comp = n(raw_knob)
    if comp > 0:
        tile = int(COMPRESSION_TILE_BASE + comp * COMPRESSION_TILE_SCALE)
        tw = max(1, w // tile)
        th = max(1, h // tile)
        sub_buffer = COMPRESSION_STAGE_BUFFER[:th, :tw]
        cv2.resize(
            MUTABLE_PROCESSING_BUFFER,
            (tw, th),
            dst=sub_buffer,
            interpolation=cv2.INTER_NEAREST,
        )
        cv2.resize(
            sub_buffer,
            (w, h),
            dst=MUTABLE_PROCESSING_BUFFER,
            interpolation=cv2.INTER_NEAREST,
        )


def apply_chroma_shift(raw_knob, current_time):
    """Chroma Shift: red channel shift, ghost image and shifted slices."""
    h, w = FRAME_H, FRAME_W
    ana_val = n(raw_knob)
    if ana_val > 0.05:
        shift = int(ana_val * ANA_SHIFT_MAX)
        if shift != 0:
            red = MUTABLE_PROCESSING_BUFFER[:, :, 2]

            if shift > 0:
                s = shift % w
                if s != 0:
                    CHANNEL_SHIFT_BUFFER[:, :-s] = red[:, s:]
                    CHANNEL_SHIFT_BUFFER[:, -s:] = red[:, :s]
                    red[:] = CHANNEL_SHIFT_BUFFER

            elif shift < 0:
                s = (-shift) % w
                if s != 0:
                    CHANNEL_SHIFT_BUFFER[:, :s] = red[:, -s:]
                    CHANNEL_SHIFT_BUFFER[:, s:] = red[:, :-s]
                    red[:] = CHANNEL_SHIFT_BUFFER

        s_ghost = ANA_GHOST_OFFSET % w

        if s_ghost != 0:
            GHOST_FRAME_BUFFER[:, :s_ghost] = MUTABLE_PROCESSING_BUFFER[:, -s_ghost:]
            GHOST_FRAME_BUFFER[:, s_ghost:] = MUTABLE_PROCESSING_BUFFER[:, :-s_ghost]

            cv2.addWeighted(
                MUTABLE_PROCESSING_BUFFER,
                0.80,
                GHOST_FRAME_BUFFER,
                0.20,
                0,
                dst=MUTABLE_PROCESSING_BUFFER,
            )

        if int(current_time * 15) % 3 == 0:
            for _ in range(int(ana_val * 3)):
                y_slice = np.random.randint(0, h - SLICE_MAX_H)
                h_slice = np.random.randint(SLICE_MIN_H, SLICE_MAX_H)
                s_shift = np.random.randint(-SLICE_MAX_SHIFT, SLICE_MAX_SHIFT)

                if s_shift != 0:
                    slice_view = MUTABLE_PROCESSING_BUFFER[
                        y_slice : y_slice + h_slice, :
                    ]
                    if s_shift > 0:
                        s_s = s_shift % w
                        if s_s != 0:
                            scratch = SLICE_SHIFT_BUFFER[:h_slice, :w, :]
                            scratch[:, :s_s] = slice_view[:, -s_s:]
                            scratch[:, s_s:] = slice_view[:, :-s_s]
                            slice_view[:] = scratch
                    elif s_shift < 0:
                        s_s = (-s_shift) % w
                        if s_s != 0:
                            scratch = SLICE_SHIFT_BUFFER[:h_slice, :w, :]
                            scratch[:, :-s_s] = slice_view[:, s_s:]
                            scratch[:, -s_s:] = slice_view[:, :s_s]
                            slice_view[:] = scratch


def apply_signal_drift(raw_knob, current_time):
    """Signal Drift: V-hold roll above 85 % and the horizontal sine drift."""
    global STATIC_V_BUFFER
    drift = n(raw_knob)
    if drift > 0:
        h, w = MUTABLE_PROCESSING_BUFFER.shape[:2]

        if STATIC_V_BUFFER is None:
            STATIC_V_BUFFER = np.zeros(
                (h // 2, w) + MUTABLE_PROCESSING_BUFFER.shape[2:],
                dtype=MUTABLE_PROCESSING_BUFFER.dtype,
            )

        # --- PHASE A: VERTICAL V-HOLD ROLL (Triggers past 65% intensity) ---
        if drift > 0.85:
            # Normalize the 85% - 100% knob range to a 0.0 - 1.0 scale
            v_t = (drift - 0.85) / 0.15
            # Reaches exactly half-screen height (h // 2) at 100% intensity
            v_shift = int(v_t * (h // 2))

            if v_shift > 0:
                # 1. Save only the top rows that are about to be pushed off the screen
                STATIC_V_BUFFER[:v_shift] = MUTABLE_PROCESSING_BUFFER[:v_shift]

                # 2. Shift the remaining lower portion of the image up to the top
                MUTABLE_PROCESSING_BUFFER[:-v_shift] = MUTABLE_PROCESSING_BUFFER[
                    v_shift:
                ]

                # 3. Paste the saved top rows back into the bottom of the frame
                MUTABLE_PROCESSING_BUFFER[-v_shift:] = STATIC_V_BUFFER[:v_shift]

                # Draw the authentic black Vertical Blanking Interval (VBI) bar at the seam
                seam = h - v_shift
                bar_thickness = int(h * 0.04)  # Thick black bar (~4% of screen height)
                bar_start = max(0, seam - (bar_thickness // 2))
                bar_end = min(h, seam + (bar_thickness // 2))

                # Blank out the seam lines to black
                MUTABLE_PROCESSING_BUFFER[bar_start:bar_end] = 0

        # --- PHASE B: HORIZONTAL DRIFT (Your existing loop) ---
        amount = int(drift * DRIFT_MAX_AMOUNT * 0.6)
        for i in range(h):
            shift = int(math.sin(i * 0.05 + current_time * 0.15) * amount)
            if shift > 0:
                s = shift % w
                if s != 0:
                    row = MUTABLE_PROCESSING_BUFFER[i]
                    STATIC_ROW_BUFFER[:s] = row[-s:]
                    STATIC_ROW_BUFFER[s:] = row[:-s]
                    row[:] = STATIC_ROW_BUFFER
            elif shift < 0:
                s = (-shift) % w
                if s != 0:
                    row = MUTABLE_PROCESSING_BUFFER[i]
                    STATIC_ROW_BUFFER[:-s] = row[s:]
                    STATIC_ROW_BUFFER[-s:] = row[:s]
                    row[:] = STATIC_ROW_BUFFER


def apply_melt(raw_knob, current_time):
    """Melt: gravitational drips (the vectorized engine takes a 0-1 strength)."""
    apply_melt_pass(n(raw_knob), current_time)


def apply_ascii_matrix(raw_knob, current_time):
    """ASCII Matrix: character layer blended over the picture by the knob value."""
    global SPARK_COUNTER, SPARK_INTERVAL
    ascii_val = n(raw_knob)
    if ascii_val > 0.02:
        cv2.cvtColor(
            MUTABLE_PROCESSING_BUFFER, cv2.COLOR_BGR2GRAY, dst=ASCII_UPRES_GRAY
        )

        ASCII_UPRES_GRAY[:] = clahe.apply(ASCII_UPRES_GRAY)

        cv2.resize(
            ASCII_UPRES_GRAY,
            (ASCII_GRID_W, ASCII_GRID_H),
            dst=ASCII_LOWRES_GRAY,
            interpolation=cv2.INTER_NEAREST,
        )

        cv2.normalize(ASCII_LOWRES_GRAY, ASCII_LOWRES_GRAY, 0, 255, cv2.NORM_MINMAX)

        np.right_shift(ASCII_LOWRES_GRAY, 4, out=ASCII_LOWRES_GRAY)

        np.take(ASCII_TILES, ASCII_LOWRES_GRAY, axis=0, out=ASCII_BLOCK_BUFFER)

        structured = ASCII_BLOCK_BUFFER.transpose(0, 2, 1, 3)
        ASCII_UPRES_GRAY[:] = structured.reshape(FRAME_H, FRAME_W)

        ASCII_GREEN_BUFFER[:, :, 0] = 0
        ASCII_GREEN_BUFFER[:, :, 1] = ASCII_UPRES_GRAY
        ASCII_GREEN_BUFFER[:, :, 2] = 0

        MAX_SPARK_DROP = 3
        SPARK_COUNTER += 1

        if SPARK_COUNTER >= SPARK_INTERVAL:
            cv2.randu(ASCII_NOISE_LOW, 0, 255)
            noise_threshold = 254 - int(ascii_val * MAX_SPARK_DROP)
            noise_threshold = max(240, min(254, noise_threshold))

            cv2.threshold(
                ASCII_NOISE_LOW,
                noise_threshold,
                255,
                cv2.THRESH_BINARY,
                dst=ASCII_NOISE_LOW,
            )

            ASCII_NOISE_LOW[ASCII_LOWRES_GRAY == 0] = 0

            streak_length = np.random.randint(1, 7)
            kernel = np.ones((streak_length, 1), dtype=np.uint8)
            cv2.dilate(ASCII_NOISE_LOW, kernel, dst=ASCII_NOISE_LOW)

            SPARK_COUNTER = 0
            SPARK_INTERVAL = np.random.randint(7, 17)

        # Everything below runs EVERY frame to map the held noise to live movement

        cv2.resize(
            ASCII_NOISE_LOW,
            (FRAME_W, FRAME_H),
            dst=ASCII_NOISE_UP,
            interpolation=cv2.INTER_NEAREST,
        )

        glow_mask = (ASCII_NOISE_UP > 0) & (ASCII_UPRES_GRAY > 0)
        ASCII_GREEN_BUFFER[glow_mask] = [31, 255, 31]  # Bright phosphor highlights

        bloom_small = cv2.resize(
            ASCII_GREEN_BUFFER,
            (ASCII_GRID_W, ASCII_GRID_H),
            interpolation=cv2.INTER_LINEAR,
        )
        bloom_blur = cv2.GaussianBlur(bloom_small, (3, 3), 0)
        bloom_up = cv2.resize(
            bloom_blur, (FRAME_W, FRAME_H), interpolation=cv2.INTER_LINEAR
        )

        cv2.addWeighted(
            ASCII_GREEN_BUFFER, 0.8, bloom_up, 0.4, 0, dst=ASCII_GREEN_BUFFER
        )

        cv2.addWeighted(
            src1=ASCII_GREEN_BUFFER,
            alpha=ascii_val,
            src2=MUTABLE_PROCESSING_BUFFER,
            beta=1.0 - ascii_val,
            gamma=0,
            dst=MUTABLE_PROCESSING_BUFFER,
        )


def apply_crt_overlay():
    """LIGHTWEIGHT CRT SCANLINE OVERLAY (ZERO RUNTIME ALLOCATION). Always the last step."""
    if CRT_ENABLED:
        cv2.multiply(
            MUTABLE_PROCESSING_BUFFER,
            CRT_MASK,
            MUTABLE_PROCESSING_BUFFER,
            scale=1.0 / 255.0,
        )


# The pool: effect id -> handler. A new effect (for example Posterize) is added here,
# in EFFECT_ORDER and in EFFECT_LABELS / EFFECT_COST_WEIGHT, and then attached to a knob.
EFFECT_HANDLERS = {
    "pixellation": apply_pixellation,
    "chroma_shift": apply_chroma_shift,
    "signal_drift": apply_signal_drift,
    "melt": apply_melt,
    "data_mosh": apply_data_mosh,
    "packet_corruption": apply_packet_corruption,
    "ascii_matrix": apply_ascii_matrix,
}

_EFFECT_PLAN = []  # [(effect id, handler, knob, window, skip during warm-up)], in order
_KNOB_LABELS = [KNOB_EMPTY_LABEL] * 6


def rebuild_effect_plan():
    """Check EFFECT_ORDER / KNOB_ATTACHMENTS / EFFECT_KNOB_WINDOW and build the run plan.
    Never raises: a bad entry is dropped and reported so the booth keeps running (R14).
    Called at start-up and by set_effect_config(). Returns the list of messages."""
    global _EFFECT_PLAN, _KNOB_LABELS
    notes = []

    order = []
    for eff in EFFECT_ORDER:
        if eff not in EFFECT_HANDLERS:
            notes.append(f"EFFECT_ORDER: unknown effect {eff!r} ignored")
        elif eff in order:
            notes.append(f"EFFECT_ORDER: {eff!r} listed twice, first entry kept")
        else:
            order.append(eff)

    knob_of = {}
    knob_effects = {k: [] for k in range(6)}
    valid_knobs = sorted(
        k
        for k in KNOB_ATTACHMENTS
        if isinstance(k, int) and not isinstance(k, bool) and 0 <= k <= 5
    )
    for k in KNOB_ATTACHMENTS:
        if k not in valid_knobs:
            notes.append(f"KNOB_ATTACHMENTS: knob {k!r} is not 0-5, ignored")
    for knob in valid_knobs:
        for eff in KNOB_ATTACHMENTS[knob]:
            if eff not in order:
                notes.append(
                    f"knob {knob}: {eff!r} is unknown or missing from EFFECT_ORDER, ignored"
                )
            elif eff in knob_of:
                notes.append(
                    f"knob {knob}: {eff!r} is already attached to knob {knob_of[eff]}, ignored"
                )
            else:
                knob_of[eff] = knob
                knob_effects[knob].append(eff)

    windows = {}
    for eff, win in EFFECT_KNOB_WINDOW.items():
        try:
            lo, hi = float(win[0]), float(win[1])
            ok = 0.0 <= lo < hi <= 1.0
        except Exception:
            ok = False
        if ok and eff in order:
            windows[eff] = (lo, hi)
        else:
            notes.append(
                f"EFFECT_KNOB_WINDOW: {eff!r}: {win!r} needs 0 <= low < high <= 1 and a known effect, ignored"
            )

    plan = []
    for eff in order:
        if eff in knob_of:
            plan.append(
                (
                    eff,
                    EFFECT_HANDLERS[eff],
                    knob_of[eff],
                    windows.get(eff, (0.0, 1.0)),
                    eff in EFFECT_SKIP_DURING_WARMUP,
                )
            )
            if eff not in EFFECT_COST_WEIGHT:
                notes.append(f"EFFECT_COST_WEIGHT: no weight for {eff!r}, counted as 0.0")

    _EFFECT_PLAN = plan
    _KNOB_LABELS = [
        EFFECT_LABELS.get(knob_effects[k][0], knob_effects[k][0])
        if knob_effects[k]
        else KNOB_EMPTY_LABEL
        for k in range(6)
    ]
    for msg in notes:
        print(f"[EFFECT CONFIG] {msg}")
    return notes


def set_effect_config(order=None, attachments=None, windows=None, weights=None):
    """Replace parts of the effect configuration at run time (used by the A/B harness
    and by experiments in a Python shell), then rebuild the run plan."""
    global EFFECT_ORDER, KNOB_ATTACHMENTS, EFFECT_KNOB_WINDOW, EFFECT_COST_WEIGHT
    if order is not None:
        EFFECT_ORDER = list(order)
    if attachments is not None:
        KNOB_ATTACHMENTS = {k: list(v) for k, v in attachments.items()}
    if windows is not None:
        EFFECT_KNOB_WINDOW = dict(windows)
    if weights is not None:
        EFFECT_COST_WEIGHT = dict(weights)
    return rebuild_effect_plan()


def get_knob_labels():
    """Six names for the kiosk and web page: the primary effect of each knob."""
    return list(_KNOB_LABELS)


def _windowed_raw(raw_knob, window):
    """Stretch the part of the knob travel inside `window` to the full 0-1023 range."""
    lo, hi = window
    if lo <= 0.0 and hi >= 1.0:
        return raw_knob  # no window: the effect sees the knob exactly as before
    x = (raw_knob / 1023.0 - lo) / (hi - lo)
    if x <= 0.0:
        return 0
    if x >= 1.0:
        return 1023
    return x * 1023.0


def compute_effect_intensity(knobs):
    """Load-governor input 0..1: the cost weight of every attached effect times its strength."""
    total = 0.0
    for eff, _handler, knob, window, _skip in _EFFECT_PLAN:
        total += EFFECT_COST_WEIGHT.get(eff, 0.0) * n(_windowed_raw(knobs[knob], window))
    return max(0.0, min(1.0, total))


rebuild_effect_plan()


def apply_glitch_pipeline(current_time, control_packet):
    """Run every attached effect in EFFECT_ORDER on MUTABLE_PROCESSING_BUFFER, then the CRT overlay."""
    global WARMUP_COUNTER, LAST_TEMPORAL_PURGE
    pkt_knobs = control_packet.knobs
    is_warming_up = WARMUP_COUNTER < WARMUP_FRAMES

    # TEMPORAL REGISTERS (WARM-UP / PURGE)
    # =========================================================================
    cv2.cvtColor(MUTABLE_PROCESSING_BUFFER, cv2.COLOR_BGR2GRAY, dst=SHARED_GRAY_BUFFER)

    if is_warming_up:
        PREV_FRAME_BUFFER[:] = MUTABLE_PROCESSING_BUFFER
        WARMUP_COUNTER += 1

    if (current_time - LAST_TEMPORAL_PURGE) > 90.0:
        PREV_FRAME_BUFFER[:] = MUTABLE_PROCESSING_BUFFER
        LAST_TEMPORAL_PURGE = current_time

    # THE EFFECTS, IN ORDER (later = on top)
    # =========================================================================
    for _effect, handler, knob, window, skip_during_warmup in _EFFECT_PLAN:
        if skip_during_warmup and is_warming_up:
            continue
        handler(_windowed_raw(pkt_knobs[knob], window), current_time)

    # CRT SCANLINE OVERLAY: always on, always last
    # =========================================================================
    apply_crt_overlay()
```

### Step 5 — `server.py`: load governor

**S6 · Load governor: per-effect weights (part 1 of 2)** — `server.py`, near line 3280 of the file I read.

FIND (appears exactly once):

```python
            try:
                effect_intensity = (
                    norm_knobs[0] * 0.10
                    + norm_knobs[1] * 0.07
                    + norm_knobs[2] * 0.27
                    + norm_knobs[4] * 0.42
                    + norm_knobs[3] * 0.07
                    + norm_knobs[5] * 0.07
                )
                effect_intensity = max(0.0, min(1.0, effect_intensity))
            except Exception:
                effect_intensity = 0.5
```

REPLACE WITH:

```python
            try:
                # per-effect cost weights (EFFECT_COST_WEIGHT): follows an effect to any knob
                effect_intensity = compute_effect_intensity(packet.knobs)
            except Exception:
                effect_intensity = 0.5
```

**S6b · Load governor: drop the now-unused norm_knobs line (part 2 of 2)** — `server.py`, near line 3277 of the file I read.

FIND (appears exactly once):

```python
            # OPTIMIZATION: Convert knobs to 0.0 - 1.0 scale once per frame
            norm_knobs = [k / 1023.0 for k in packet.knobs]

```

REPLACE WITH: *nothing* — delete the lines.

### Step 6 — `server.py`: names for the web page

**S7 · /state: send the knob names to the web page** — `server.py`, near line 3925 of the file I read.

FIND (appears exactly once):

```python
            "knobs": list(GLOBAL_KNOBS),
            "ip": SYSTEM_TELEMETRY["ip"],
```

REPLACE WITH:

```python
            "knobs": list(GLOBAL_KNOBS),
            "labels": get_knob_labels(),  # primary effect name of each knob
            "ip": SYSTEM_TELEMETRY["ip"],
```

### Step 7 — `server.py`: naked capture

Four small edits. Together: the raw frame is copied during the pose countdown, handed to the storage thread, and written first by a helper that cannot raise.

**S8 · Naked capture: the writer function (just above cleanup_sessions)** — `server.py`, near line 2718 of the file I read.

FIND (appears exactly once):

```python
def cleanup_sessions(limit=100):
```

REPLACE WITH:

```python
def _write_naked_capture(disk_id, frame):
    """Save the unprocessed camera frame behind a capture as <id>_naked.jpg in NAKED_DIR.
    Device only. Never raises and never affects the real capture; returns True on success."""
    if frame is None:
        return False
    try:
        ok = cv2.imwrite(os.path.join(NAKED_DIR, f"{disk_id}_naked.jpg"), frame)
        if not ok:
            logger.error("[NAKED] could not write naked capture for session %s", disk_id)
        return bool(ok)
    except Exception as exc:
        logger.error("[NAKED] naked capture failed for session %s: %s", disk_id, exc)
        return False


def cleanup_sessions(limit=100):
```

**S9 · Naked capture: copy the raw frame during the pose countdown (in state_machine_loop, right before the pipeline call)** — `server.py`, near line 3275 of the file I read.

FIND (appears exactly once):

```python
            apply_glitch_pipeline(loop_execution_time, packet)
```

REPLACE WITH:

```python
            if NAKED_CAPTURE_ENABLED and loop_mode == "pre-capture-countdown":
                # the raw frame of this loop pass: the last copy before the capture is its naked twin
                NAKED_FRAME_BUFFER[:] = MUTABLE_PROCESSING_BUFFER

            apply_glitch_pipeline(loop_execution_time, packet)
```

**S10 · Naked capture: the storage worker takes the naked frame and saves it first** — `server.py`, near line 3370 of the file I read.

FIND (appears exactly once):

```python
            def async_storage_worker(disk_id, color_mat, bw_mat, active_knobs):
                color_ok = cv2.imwrite(
```

REPLACE WITH:

```python
            def async_storage_worker(
                disk_id, color_mat, bw_mat, active_knobs, naked_mat=None
            ):
                _write_naked_capture(disk_id, naked_mat)  # extra, isolated, never blocks the session
                color_ok = cv2.imwrite(
```

**S11 · Naked capture: hand the naked frame to the worker thread** — `server.py`, near line 3492 of the file I read.

FIND (appears exactly once):

```python
                            FROZEN_PRINT_BUFFER.copy(),
                            captured_knobs,
                        ),
```

REPLACE WITH:

```python
                            FROZEN_PRINT_BUFFER.copy(),
                            captured_knobs,
                            NAKED_FRAME_BUFFER.copy() if NAKED_CAPTURE_ENABLED else None,
                        ),
```

### Step 8 — `server.py`: two comment clean-ups (optional)

They only remove references to settings that no longer exist. Skip them if a FIND does not match; nothing depends on them.

**S12 · Comment clean-up (1 of 2)** — `server.py`, near line 1102 of the file I read.

FIND (appears exactly once):

```python
    """Snapshot of the per-cell motion sample points (for MOSH_STAGE_POSITION C)."""
```

REPLACE WITH:

```python
    """Snapshot of the per-cell motion sample points (unused since Phase 3; kept for a later motion-source option)."""
```

**S12b · Comment clean-up (2 of 2)** — `server.py`, near line 1121 of the file I read.

FIND (appears exactly once):

```python
# Unwired by default (PACKET_CORRUPTION_KNOB = None) and ready to be connected to
```

REPLACE WITH:

```python
# Ready to be attached to any knob in KNOB_ATTACHMENTS (any position in EFFECT_ORDER) and ready to be connected to
```

### Step 9 — `pygame_display.py`: knob names from the server

I only have the version of this file from before Phase 2. Find the list called `KNOB_ROWS` (six `(knob id, name)` tuples; near line 131) and replace the **whole list** with the four lines below, whatever names it holds today. The row order `0, 1, 3, 4, 2, 5` is the order on screen; keep it. `server` is already imported at the top of the file.

**D1 · Kiosk: knob names come from the server (primary effect of each knob)** — `pygame_display.py`, near line 131 of the file I read.

FIND (appears exactly once):

```python
KNOB_ROWS = [
    (0, "ASCII Matrix"),
    (1, "Signal Drift"),
    (3, "Pixel Damage"),
    (4, "Melt"),
    (2, "Data Corruption"),
    (5, "Chroma Shift"),
]
```

REPLACE WITH:

```python
# Row order on screen (knob ids). The names come from the server: the primary effect of each knob.
KNOB_ROW_ORDER = [0, 1, 3, 4, 2, 5]
_knob_names = server.get_knob_labels()
KNOB_ROWS = [(k_id, _knob_names[k_id]) for k_id in KNOB_ROW_ORDER]
```

### Step 10 — `.gitignore` and the storage-path reminder

Append one line to `.gitignore` (create the file if it does not exist) and check that git no longer sees the folder:

```
echo "captures_naked" >> .gitignore
git status        # captures_naked must NOT be listed
```

Use the name **without a trailing slash**: your capture folders are symlinks on the Pi, and a pattern ending in `/` matches directories only, not symlinks. If git already tracks the folder by accident: `git rm -r --cached captures_naked`.

> **REMINDER — writing path on the Pi.** Make `captures_naked` live on the data disk, the same way the other capture folders do (a symlink to the data part), **before the first run of Phase 3**. If the folder does not exist when the server starts, the server creates an ordinary folder on the code partition. You said you handle the details yourself; this is only the reminder.

### Step 11 — `index.html`: names from the server

Again I only have the pre-Phase-2 file. In each of the six knob rows the visible name must be wrapped in a `<span class="nm" id="nN">` element (N = the knob id already used in that row's `kN` span). **Keep whatever name text your row has** (for example `Data Mosh` in the knob-2 row); the examples below use the text of the file I read and the page replaces the text with the server's name on the first poll anyway. The extra CSS rule is needed because the existing rule `.row span` floats every span to the right (that is how the values are placed). The JavaScript goes inside the existing function that handles the `/state` answer `d`, just above the existing `/* HARDENED KNOB LOOP */` comment.

**H0 · Web page: row of knob 0 gets a name element** — `index.html`, near line 301 of the file I read.

FIND (appears exactly once):

```html
<div class="row">ASCII Matrix <span id="k0"></span></div>
```

REPLACE WITH:

```html
<div class="row"><span class="nm" id="n0">ASCII Matrix</span> <span id="k0"></span></div>
```

**H1 · Web page: row of knob 1 gets a name element** — `index.html`, near line 302 of the file I read.

FIND (appears exactly once):

```html
<div class="row">Signal Drift <span id="k1"></span></div>
```

REPLACE WITH:

```html
<div class="row"><span class="nm" id="n1">Signal Drift</span> <span id="k1"></span></div>
```

**H3 · Web page: row of knob 3 gets a name element** — `index.html`, near line 303 of the file I read.

FIND (appears exactly once):

```html
<div class="row">Pixel Damage <span id="k3"></span></div>
```

REPLACE WITH:

```html
<div class="row"><span class="nm" id="n3">Pixel Damage</span> <span id="k3"></span></div>
```

**H4 · Web page: row of knob 4 gets a name element** — `index.html`, near line 304 of the file I read.

FIND (appears exactly once):

```html
<div class="row">Melt <span id="k4"></span></div>
```

REPLACE WITH:

```html
<div class="row"><span class="nm" id="n4">Melt</span> <span id="k4"></span></div>
```

**H2 · Web page: row of knob 2 gets a name element** — `index.html`, near line 305 of the file I read.

FIND (appears exactly once):

```html
<div class="row">Data Corruption <span id="k2"></span></div>
```

REPLACE WITH:

```html
<div class="row"><span class="nm" id="n2">Data Corruption</span> <span id="k2"></span></div>
```

**H5 · Web page: row of knob 5 gets a name element** — `index.html`, near line 306 of the file I read.

FIND (appears exactly once):

```html
<div class="row">Chroma Shift <span id="k5"></span></div>
```

REPLACE WITH:

```html
<div class="row"><span class="nm" id="n5">Chroma Shift</span> <span id="k5"></span></div>
```

**HC · Web page: CSS so the name does not float right like the value** — `index.html`, near line 94 of the file I read.

FIND (appears exactly once):

```html
.row span{
    float:right;
}
```

REPLACE WITH:

```html
.row span{
    float:right;
}

/* knob name inside a row (the value span above floats right) */
.row .nm{
    float:none;
}
```

**HJ · Web page: write the names received from /state (just above the existing knob loop)** — `index.html`, near line 434 of the file I read.

FIND (appears exactly once):

```html
                    /* HARDENED KNOB LOOP */
```

REPLACE WITH:

```html
                    /* KNOB NAMES: primary effect of each knob, from the server */
                    if(d.labels && d.labels.length >= 6){
                        for(var j=0; j<6; j++){
                            var nameEl = document.getElementById('n' + j);
                            if(nameEl && nameEl.textContent !== d.labels[j]){
                                nameEl.textContent = d.labels[j];
                            }
                        }
                    }

                    /* HARDENED KNOB LOOP */
```

### Step 12 — New file `ab_regression_phase3.py`

Create it in the repo root with the exact content of Appendix A. (The earlier `ab_regression.py` / `ab_regression_phase2.py` files can stay; they are not used any more.)

### Step 13 — Compile check and A/B test

```
python3 -m py_compile server.py pygame_display.py
python3 ab_regression_phase3.py --quick     # about two minutes on a fast machine; expect several times longer on the Pi
python3 ab_regression_phase3.py             # full run, when you have time
```

Expected last lines: `RESULT: PASS - wrapping changed nothing; the new features behave as specified`. A FAIL that appears because you tuned effects after making the `pre-phase3` tag is **not important** (section 6).

### Step 14 — Booth checks, then commit

Run the checklist in section 8 on the Pi. When satisfied (commands for you to run; nothing is pushed):

```
git add server.py pygame_display.py index.html .gitignore ab_regression_phase3.py
git commit -m "Phase 3: effect handlers, free attachments, editable order, knob windows, naked capture (P3-01)"
```

## 6. `ab_regression_phase3.py`: what it checks and how to read it

Full code in Appendix A. It loads the old `server.py` from the git tag and the new one from the working directory, runs the full pipeline on identical frames, knobs and random seeds, and compares the outputs byte for byte.

1. **Legacy-order identity.** It reads the wiring of the *old* file, rebuilds the same order and attachments in the new code with `set_effect_config()`, and every case (dormant and active, warm-up included) must then be identical. This is what proves the wrapping changed nothing.
2. **Default order.** With the shipped order, cases where Packet Corruption is quiet must still equal the old output (only the corruption moved). With it active they must differ; if they do not, the comparison would be blind.
3. **Feature checks:** windows, the order list really reorders, the governor equals the old formula, labels, bad configuration never raises, the naked helper (file written, no frame means no file, unwritable folder returns False).
4. **A/A self-test:** two fresh copies of the new code must be identical, otherwise the harness is not deterministic.

**How to read a FAIL.** The baseline tag is a snapshot. If you tuned effect settings after tagging, the old and the new file legitimately differ and the test can FAIL. That kind of FAIL is **not important**: tag the tuned state you consider good (`git tag -f pre-phaseN`) before starting a phase and run it again when you need a strict answer. The harness prints this note itself.

## 7. Verification done (scratch checkout, x86 sandbox, synthetic frames)

| Check | Result |
|---|---|
| Edit list replayed on a clean copy of your files | reproduces the verified `server.py`, `pygame_display.py` and `index.html` byte for byte |
| Legacy-order identity (full run) | **104 of 104** cases byte-identical, all 104 altered their input, warm-up probe included |
| Default order, Packet Corruption quiet | **40 of 40** identical to your current file |
| Default order, Packet Corruption active (negative control) | **3 of 3** differ, as intended |
| Feature checks | **15 of 15** pass |
| A/A self-test | identical |
| Quick mode (`--quick`) | 42 of 42 identical, 10 same-expected, 3 differ-expected, 15 of 15 |
| Harness sensitivity | fails as it should when a constant inside a wrapped effect changes by 1 (3 of 4 identical), when two effects are swapped in the order (3 of 4) and when a knob value is off by one (1 of 4) |
| Governor against the old formula | largest difference 2.2e-16 over 200 random knob sets |
| `/state` | answers 200 with the six names; swapping a primary changes the name |
| Kiosk row builder | produces the rows in the same order as today with the new names |
| Web page | whole page script passes `node --check`; the label snippet works against a fake document and leaves the page untouched when `labels` is absent; element ids unique, `div`/`span` balanced |
| Runtime overhead (old order, both versions doing identical work) | ratio new/old 1.04, 0.92, 1.04 and 1.00 on four knob sets (about 0.4 ms with everything off, about 20 ms with everything at maximum): no measurable overhead |

## 8. Booth acceptance checklist (on the Pi)

1. **Start-up:** no `[EFFECT CONFIG]` or `[MOSH SETTINGS]` lines on the console. Kiosk and web page show the six names in the same row order as before.
2. **Order check:** knob 5 high with knob 3 (Pixel Damage) high: the packets are crisp, not pixellated and not shifted. With ASCII at 0 the packets look exactly as generated; raising ASCII turns them into characters together with the rest.
3. **Every knob** 0 → maximum → 0, especially knob 2 dropping to 0 (all moshing disappears at once) and knob 5 (Chroma Shift with hidden packets).
4. **Naked capture, three captures:** `captures_naked/<id>_naked.jpg` exists for each, with the same id as `captures/<id>_color.jpg`, the same orientation, no effects visible, and **no** upload of it (it must not appear in ImageKit or on the website). Confirm the files land on the data disk (see the reminder in step 10).
5. **Attachment experiment:** attach Packet Corruption to knob 3 as a secondary, restart: the effect follows knob 3, the names do not change.
6. **Window experiment:** `EFFECT_KNOB_WINDOW = {"packet_corruption": (0.35, 1.0)}`: no packets in the lower third of knob 5, full range at the top.
7. **Governor:** all knobs at maximum for one minute: the FPS tier behaves as before (the default weights are unchanged).
8. **Normal flow:** one full capture, print, QR and website cycle; the countdown flash; no traceback or `[NAKED]` error on the console during a 10–15 minute soak; temperature as before.

## 9. Rollback

```
sudo systemctl stop glitchbooth
git checkout pre-phase3 -- server.py pygame_display.py index.html
rm ab_regression_phase3.py
sudo systemctl start glitchbooth
```

The `captures_naked` folder and its files can stay (harmless) or be deleted. The `.gitignore` line can stay.

## 10. Impact on other documents and hand-off to the next phases

**Readme (for later, nothing is changed now):** the master plan table (new Phase 3 to 6), the pipeline order and wiring sections, the tunables list (new Phase 3 block, `MOSH_STAGE_POSITION` and the two wiring constants gone), the effect cost weights, the file list (`ab_regression_phase3.py`, no more fusion), the naked capture and its folder.  
**Phase 4 (telemetry):** the data it needs is ready: `_EFFECT_PLAN` lists the attached effects with their knob and window, and `_windowed_raw()` gives the strength each effect sees. Counting by name means: an effect counts as used when its windowed value passes the activation threshold. A short wiring label stored with each capture lets you separate "popular effect" from "popular place on a knob" when you re-attach between trials.  
**Phase 5 (Posterize):** one handler plus four dictionary entries (section 3, Q11).

## 11. Not verified

- The capture path with the real state machine (the loop copy and the worker thread) could not be run without the hardware; I reviewed it in context and tested the writer helper separately.
- Everything on the Pi: timings, display latency, temperature, the look of the new order with real faces.
- The current `pygame_display.py`, `index.html` and `.gitignore` (steps 9 and 11 are anchored on older versions).
- Your service file and the kiosk start-up were not exercised.

## 12. Approval requested

Confirm or change the assumptions Q1–Q11 in section 3, then reply **APPROVED P3-01** (or the changes you want). The steps can then be applied by you or by an AI assistant exactly as written.

---

## Appendix A — `ab_regression_phase3.py` (complete file)

Create this file in the repo root. It is the only new file.

```python
#!/usr/bin/env python3
"""
A/B regression guard for PHASE 3: ab_regression_phase3.py

Question it answers: "Did wrapping the effects into handlers (and adding the effect order,
the knob attachments, the knob windows and the naked capture) change how the picture
looks, apart from the one change we meant to make?"

Run it from the repo root with the kiosk stopped:
    git tag pre-phase3 <commit>        # once, on the commit BEFORE Phase 3 (already done)
    python3 ab_regression_phase3.py --ref pre-phase3
    python3 ab_regression_phase3.py --quick            # shorter run (use this on the Pi)

OLD = server.py at the git tag (the version before Phase 3), NEW = ./server.py.

What it checks
  1. LEGACY ORDER IDENTITY. The wiring of OLD is read from the tagged file and rebuilt in NEW
     with set_effect_config() (the old processing order and the old knob wiring). Every case,
     dormant or active, must then be byte-for-byte identical. This is what proves the
     wrapping changed nothing.
  2. DEFAULT ORDER. With NEW's shipped order, cases where Packet Corruption is dormant must
     still be identical to OLD (only the corruption moved), and with it active they must
     differ (negative control: otherwise the comparison could not tell the versions apart).
  3. FEATURE CHECKS (no OLD needed): knob windows, governor equals the old formula, knob
     labels, config validation never raising, the order list really reorders, the naked
     capture helper.
  4. A/A SELF-TEST: two fresh copies of NEW must be identical, otherwise the harness itself
     is not deterministic and no verdict can be trusted.

IMPORTANT - READ A FAIL WITH CARE
  The baseline tag is a snapshot. If you tuned effect settings (constants, strengths, cell
  sizes, ...) after tagging, OLD and NEW legitimately differ and this test can FAIL. That
  kind of FAIL is NOT important. Tag the tuned state that you consider good
  (git tag -f pre-phaseN) before starting a phase, and run the test again when you need a
  strict answer. Exit code 0 = PASS, 1 = FAIL.
"""
import argparse
import copy
import hashlib
import importlib.util
import os
import random
import subprocess
import sys
import tempfile
import time

import cv2
import numpy as np

os.environ.setdefault("IK_URL_ENDPOINT", "https://ik.imagekit.io/mock")
os.environ.setdefault("IK_PRIVATE_KEY", "mock_key")
sys.path.insert(0, os.getcwd())

BASE_TIME = 5000.0
TUNING_NOTE = (
    "   NOTE: if you tuned effect settings after the baseline tag was made, a FAIL here is\n"
    "   expected and NOT important. Re-tag the tuned state and run again for a strict check."
)


# --------------------------------------------------------------------------
# Loading the two versions
# --------------------------------------------------------------------------
def load_old_module(ref, old_file):
    if old_file:
        path, label = old_file, f"file {old_file}"
    else:
        res = subprocess.run(["git", "show", f"{ref}:server.py"], capture_output=True)
        if res.returncode != 0:
            sys.exit(
                f"[A/B] Could not read server.py at git ref '{ref}':\n"
                f"      {res.stderr.decode(errors='ignore').strip()}\n"
                f"      Create it on the commit BEFORE the phase:  git tag {ref} <commit>\n"
                f"      (tags are local; on the Pi: git fetch --tags)"
            )
        tmp = tempfile.mkdtemp(prefix="ab_old_")
        path = os.path.join(tmp, "server_old.py")
        with open(path, "wb") as fh:
            fh.write(res.stdout)
        label = f"git ref {ref}"
    with open(path, "rb") as fh:
        old_bytes = fh.read()
    spec = importlib.util.spec_from_file_location("server_old", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["server_old"] = mod
    spec.loader.exec_module(mod)
    return mod, label, old_bytes


def legacy_config(old):
    """Describe OLD's wiring in the new vocabulary: (order, attachments, windows).
    A Phase 3+ OLD already has them; a Phase 2 OLD is translated from its constants."""
    if hasattr(old, "EFFECT_ORDER") and hasattr(old, "KNOB_ATTACHMENTS"):
        return (
            list(old.EFFECT_ORDER),
            {k: list(v) for k, v in old.KNOB_ATTACHMENTS.items()},
            dict(getattr(old, "EFFECT_KNOB_WINDOW", {})),
        )
    mosh_knob = getattr(old, "DATAMOSH_KNOB", 2)
    corr_knob = getattr(old, "PACKET_CORRUPTION_KNOB", None)
    position = getattr(old, "MOSH_STAGE_POSITION", "before_drift")
    if position == "after_melt_pre_melt_motion":
        sys.exit(
            "[A/B] OLD uses MOSH_STAGE_POSITION='after_melt_pre_melt_motion', which Phase 3 removed.\n"
            "      Set it to 'after_melt_own_motion' in the tagged state, or compare against another tag."
        )
    order = ["packet_corruption", "pixellation", "chroma_shift"]
    if position == "before_drift":
        order += ["data_mosh", "signal_drift", "melt"]
    else:
        order += ["signal_drift", "melt", "data_mosh"]
    order += ["ascii_matrix"]
    attachments = {0: ["ascii_matrix"], 1: ["signal_drift"], 2: [], 3: ["pixellation"], 4: ["melt"], 5: ["chroma_shift"]}
    if mosh_knob is not None:
        attachments[mosh_knob].append("data_mosh")
    if corr_knob is not None:
        attachments[corr_knob].append("packet_corruption")
    return order, attachments, {}


# --------------------------------------------------------------------------
# Deterministic input frames
# --------------------------------------------------------------------------
def base_frames(h, w, user_frame):
    rng = np.random.default_rng(99)
    small = rng.integers(0, 256, (h // 8, w // 8, 3), dtype=np.uint8)
    textured = cv2.GaussianBlur(cv2.resize(small, (w, h), interpolation=cv2.INTER_CUBIC), (0, 0), 5)
    portrait = np.zeros((h, w, 3), np.uint8)
    portrait[100:380, 200:440] = 180
    portrait[140:300, 260:380] = 230
    dark = rng.integers(0, 60, (h, w, 3), dtype=np.uint8)
    # Very bright frame with a few dark blocks: ordinary frames give Melt no seeds at low strength
    bright = np.full((h, w, 3), 255, np.uint8)
    bright[60:200, 100:300] = 40
    bright[300:420, 350:600] = 90
    frames = {"textured": textured, "portrait": portrait, "dark": dark, "bright": bright}
    if user_frame is not None:
        frames["userframe"] = user_frame
    return {k: np.ascontiguousarray(v) for k, v in frames.items()}


def frame_sequence(base, steps):
    """Slow diagonal motion, so temporal effects (Data Mosh, ghosting) have something to work on."""
    return [np.ascontiguousarray(np.roll(base, (i * 3, i * 5), axis=(0, 1))) for i in range(steps)]


# --------------------------------------------------------------------------
# Running one pipeline call on a module
# --------------------------------------------------------------------------
def run_step(mod, frame, knobs, t, seed):
    np.random.seed(seed)
    random.seed(seed)
    cv2.setRNGSeed(seed)  # cv2.randu (packet corruption noise, ASCII sparks) uses OpenCV's own RNG
    if hasattr(mod, "seed_private_rngs"):  # effects with their own generator (Data Mosh)
        mod.seed_private_rngs(seed)
    mod.MUTABLE_PROCESSING_BUFFER[:] = frame
    pkt = mod.ControlPacket(list(knobs), 0, "live")
    t0 = time.perf_counter()
    mod.apply_glitch_pipeline(t, pkt)
    dt = (time.perf_counter() - t0) * 1e3
    return mod.MUTABLE_PROCESSING_BUFFER.copy(), dt


def run_case(old, new, name, knobs, frames, seed0):
    out = {"name": name, "knobs": tuple(knobs), "identical": True, "first_diff": None,
           "old_ms": [], "new_ms": [], "vs_input_differs": 0, "all_black": False}
    for i, frame in enumerate(frames):
        t = BASE_TIME + i * 0.1
        seed = seed0 * 1000 + i
        a, ta = run_step(old, frame, knobs, t, seed)
        b, tb = run_step(new, frame, knobs, t, seed)
        out["old_ms"].append(ta)
        out["new_ms"].append(tb)
        if not np.any(b):
            out["all_black"] = True
        if not np.array_equal(b, frame):
            out["vs_input_differs"] += 1
        if out["identical"] and not np.array_equal(a, b):
            out["identical"] = False
            diff = np.any(a != b, axis=2)
            ys, xs = np.nonzero(diff)
            out["first_diff"] = (
                i, int(diff.sum()), (int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())),
                hashlib.md5(a.tobytes()).hexdigest()[:8], hashlib.md5(b.tobytes()).hexdigest()[:8],
            )
    return out


def show(res, expect_same):
    ok = res["identical"] if expect_same else not res["identical"]
    mark = ("same" if res["identical"] else "DIFF") if expect_same else ("differ" if not res["identical"] else "SAME!")
    extra = ""
    if expect_same and not res["identical"]:
        i, n_px, bbox, ho, hn = res["first_diff"]
        extra = f"  first at step {i}: {n_px} px, bbox {bbox}, md5 {ho} vs {hn}"
    print(f" [{mark}] {res['name']:<40} knobs={res['knobs']}{extra}")
    return ok


# --------------------------------------------------------------------------
# Case lists
# --------------------------------------------------------------------------
def identity_cases(n_random, rng, corr_knob):
    """Cases for the legacy-order identity check: everything, dormant and active."""
    cases = [("warm-up probe (k2,k5 max)", [0, 0, 1023, 0, 0, 1023])]
    for k in range(6):
        for level in (512, 1023):
            knobs = [0] * 6
            knobs[k] = level
            cases.append((f"knob{k}={level}", knobs))
    for level in (400, 1023):
        cases.append((f"all={level}", [level] * 6))
    cases.append(("all but melt=1023", [1023, 1023, 1023, 1023, 0, 1023]))
    cases.append(("melt+mosh+ascii", [600, 0, 1023, 0, 1023, 0]))
    cases.append(("pixel+chroma+corruption", [0, 0, 0, 900, 0, 1023]))
    for i in range(n_random):
        cases.append((f"random#{i}", [int(v) for v in rng.integers(0, 1024, 6)]))
    return cases


def corruption_dormant_cases(n_random, rng, corr_knob):
    """Packet Corruption's knob at <= 8 (dormant) with the other knobs busy."""
    cases = []
    for level in (400, 1023):
        k = [level] * 6
        k[corr_knob] = 0
        cases.append((f"corruption off, others={level}", k))
    for i in range(n_random):
        k = [int(v) for v in rng.integers(0, 1024, 6)]
        k[corr_knob] = (0, 8)[i % 2]
        cases.append((f"corruption dormant random#{i}", k))
    return cases


def corruption_active_cases(corr_knob):
    out = []
    for name, level in (("corruption only 0.7", 700), ("corruption only max", 1023)):
        k = [0] * 6
        k[corr_knob] = level
        out.append((name, k, "textured"))
    k = [300, 200, 800, 400, 500, 0]
    k[corr_knob] = 1023
    out.append(("corruption + others", k, "portrait"))
    return out


# --------------------------------------------------------------------------
# Feature checks (NEW only)
# --------------------------------------------------------------------------
def feature_checks(new, defaults, bases, no_crt):
    results = []

    def check(name, ok, detail=""):
        results.append((name, ok))
        print(f" [{'ok' if ok else 'FAIL'}] {name}" + (f" - {detail}" if detail else ""))

    order0, att0, win0 = defaults
    probe = frame_sequence(bases["textured"], 3)

    def run(knobs, order=None, att=None, windows=None):
        new.set_effect_config(order=order or order0, attachments=att or att0, windows=windows if windows is not None else {})
        outs = []
        new.data_mosh_reset() if hasattr(new, "data_mosh_reset") else None
        new.corruption_reset() if hasattr(new, "corruption_reset") else None
        for i, f in enumerate(probe):
            outs.append(run_step(new, f, knobs, BASE_TIME + i * 0.1, 4242 + i)[0])
        return outs

    for m_ in (new,):
        m_.WARMUP_COUNTER = m_.WARMUP_FRAMES  # warm-up is covered by the identity section

    # 1. window arithmetic
    wr = new._windowed_raw
    check("window: inside range is stretched to 0-1023",
          abs(wr(700, (0.35, 1.0)) - ((700 / 1023 - 0.35) / 0.65) * 1023) < 1e-9 and wr(1023, (0.35, 1.0)) == 1023)
    check("window: below the low edge is off, above the high edge is full",
          wr(300, (0.35, 1.0)) == 0 and wr(900, (0.0, 0.7)) == 1023)
    check("window: no window returns the raw value untouched", wr(517, (0.0, 1.0)) == 517)

    # 2. a windowed effect behaves exactly like an unattached one below its window, and like an
    #    unwindowed one at full knob
    k_low = [0, 0, 0, 0, 0, 300]
    k_max = [0, 0, 0, 0, 0, 1023]
    corr_att = {kk: [e for e in v if e != "chroma_shift" and e != "packet_corruption"] for kk, v in att0.items()}
    corr_att[5] = ["packet_corruption"]
    base_att = {kk: list(v) for kk, v in corr_att.items()}
    base_att[5] = []
    a = run(k_low, att=corr_att, windows={"packet_corruption": (0.35, 1.0)})
    b = run(k_low, att=base_att)
    check("window: corruption inside (0.35,1.0) is silent at knob 29 %", all(np.array_equal(x, y) for x, y in zip(a, b)))
    a = run(k_max, att=corr_att, windows={"packet_corruption": (0.35, 1.0)})
    b = run(k_max, att=corr_att)
    check("window: top of the window equals the full-range effect", all(np.array_equal(x, y) for x, y in zip(a, b)))
    a = run([0, 0, 0, 0, 0, 800], att=corr_att, windows={"packet_corruption": (0.35, 1.0)})
    b = run([0, 0, 0, 0, 0, 800], att=corr_att)
    check("window: mid-window differs from the full-range effect", not all(np.array_equal(x, y) for x, y in zip(a, b)))

    # 3. the order list really reorders
    k = [0, 0, 0, 900, 0, 1023]
    a = run(k)
    reordered = [e for e in order0 if e != "packet_corruption"]
    reordered.insert(0, "packet_corruption")
    b = run(k, order=reordered)
    check("order: moving Packet Corruption first changes the picture", not all(np.array_equal(x, y) for x, y in zip(a, b)))

    # 4. governor equals the old per-knob formula for the default wiring
    new.set_effect_config(order=order0, attachments=att0, windows={})
    rng = np.random.default_rng(5)
    worst = 0.0
    for _ in range(200):
        kn = [int(v) for v in rng.integers(0, 1024, 6)]
        nk = [v / 1023.0 for v in kn]
        old_formula = max(0.0, min(1.0, nk[0] * 0.10 + nk[1] * 0.07 + nk[2] * 0.27 + nk[4] * 0.42 + nk[3] * 0.07 + nk[5] * 0.07))
        worst = max(worst, abs(old_formula - new.compute_effect_intensity(kn)))
    check("governor: same value as the old per-knob formula (default wiring)", worst < 1e-12, f"largest difference {worst:.1e}")

    # 5. labels
    labels = new.get_knob_labels()
    check("labels: six names, primary effect of each knob", len(labels) == 6 and all(isinstance(x, str) and x for x in labels), str(labels))
    att_swap = {kk: list(v) for kk, v in att0.items()}
    att_swap[5] = ["packet_corruption", "chroma_shift"]
    new.set_effect_config(attachments=att_swap)
    check("labels: the first attachment is the one shown", new.get_knob_labels()[5] == new.EFFECT_LABELS["packet_corruption"])
    att_empty = {kk: list(v) for kk, v in att0.items()}
    att_empty[3] = []
    new.set_effect_config(attachments=att_empty)
    check("labels: an empty knob shows the placeholder", new.get_knob_labels()[3] == new.KNOB_EMPTY_LABEL)

    # 6. bad configuration never raises and still yields a working plan
    try:
        notes = new.set_effect_config(
            order=["nonsense", "pixellation", "pixellation"] + [e for e in order0 if e != "pixellation"],
            attachments={9: ["melt"], 0: ["ascii_matrix", "melt"], 1: ["melt", "unknown_effect"]},
            windows={"melt": (0.9, 0.2), "ghost": (0.0, 1.0)},
        )
        ok = len(notes) >= 5 and len(new._EFFECT_PLAN) >= 1
        run_step(new, probe[0], [500] * 6, BASE_TIME, 1)  # must still run
    except Exception as exc:
        ok = False
        print("   exception:", exc)
    check("validation: bad order / attachments / windows are reported, nothing raises", ok)
    new.set_effect_config(order=order0, attachments=att0, windows=win0)

    # 7. naked capture helper
    saved_dir = new.NAKED_DIR
    tmp = tempfile.mkdtemp(prefix="naked_")
    try:
        new.NAKED_DIR = tmp
        frame = probe[0]
        ok_write = new._write_naked_capture("12345", frame)
        path = os.path.join(tmp, "12345_naked.jpg")
        back = cv2.imread(path)
        check("naked: file <id>_naked.jpg is written and readable",
              ok_write and back is not None and back.shape == frame.shape)
        check("naked: no frame means no file and no error", new._write_naked_capture("99999", None) is False
              and not os.path.exists(os.path.join(tmp, "99999_naked.jpg")))
        new.NAKED_DIR = os.path.join(path, "not_a_folder")  # a path that cannot be written
        check("naked: an unwritable folder returns False and never raises", new._write_naked_capture("1", frame) is False)
    except Exception as exc:
        check("naked: helper must never raise", False, str(exc))
    finally:
        new.NAKED_DIR = saved_dir
    return results


# --------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="A/B regression guard for Phase 3")
    ap.add_argument("--ref", default="pre-phase3", help="git ref holding the OLD server.py")
    ap.add_argument("--old-file", help="path to an OLD server.py (instead of a git ref)")
    ap.add_argument("--frames", type=int, default=6, help="frames per sequence")
    ap.add_argument("--random-cases", type=int, default=8)
    ap.add_argument("--seed", type=int, default=2024)
    ap.add_argument("--frame", help="optional raw camera frame to add as an extra input")
    ap.add_argument("--no-crt", action="store_true", help="skip CRT mask initialisation")
    ap.add_argument("--quick", action="store_true", help="3 frames, 3 random cases, 2 input frames")
    ap.add_argument("--skip-selftest", action="store_true", help="skip the A/A determinism self-test")
    args = ap.parse_args()
    if args.quick:
        args.frames, args.random_cases = 3, 3

    print("=" * 74)
    print(" GLITCHBOOTH A/B REGRESSION GUARD - PHASE 3")
    print("=" * 74)
    print(f" numpy {np.__version__}, opencv {cv2.__version__}, python {sys.version.split()[0]}")

    old, label, old_bytes = load_old_module(args.ref, args.old_file)
    import server as new  # noqa: E402

    new_bytes = open(new.__file__, "rb").read()
    print(f" OLD = {label}   ({len(old_bytes)} bytes)")
    print(f" NEW = {new.__file__}   ({len(new_bytes)} bytes)")
    problems = []
    if old_bytes == new_bytes:
        problems.append("OLD and NEW are the same file; there is nothing to compare")

    if not args.no_crt:
        for m in (old, new):
            if getattr(m, "CRT_ENABLED", False):
                m.initialize_crt_mask()
    for m in (old, new):
        m.LAST_TEMPORAL_PURGE = BASE_TIME - 1.0

    # remember NEW's shipped configuration before the identity check rewires it
    defaults = (list(new.EFFECT_ORDER), {k: list(v) for k, v in new.KNOB_ATTACHMENTS.items()}, dict(new.EFFECT_KNOB_WINDOW))
    old_order, old_att, old_win = legacy_config(old)
    corr_knob = next((k for k, v in defaults[1].items() if "packet_corruption" in v), None)

    if not args.skip_selftest:
        clones = []
        for tag in ("server_clone_a", "server_clone_b"):
            spec = importlib.util.spec_from_file_location(tag, new.__file__)
            c = importlib.util.module_from_spec(spec)
            sys.modules[tag] = c
            spec.loader.exec_module(c)
            if not args.no_crt and getattr(c, "CRT_ENABLED", False):
                c.initialize_crt_mask()
            c.LAST_TEMPORAL_PURGE = BASE_TIME - 1.0
            clones.append(c)
        probe = base_frames(new.FRAME_H, new.FRAME_W, None)["textured"]
        bad = []
        for pname, pk in (("knob0=1023", [1023, 0, 0, 0, 0, 0]), ("knob2+5=1023", [0, 0, 1023, 0, 0, 1023]),
                          ("all=1023", [1023] * 6)):
            r = run_case(clones[0], clones[1], f"A/A {pname}", pk, frame_sequence(probe, 3), 7)
            if not r["identical"]:
                bad.append(pname)
        for tag in ("server_clone_a", "server_clone_b"):
            del sys.modules[tag]
        del clones
        if bad:
            sys.exit("[A/B] SELF-TEST FAILED: two identical copies of NEW differ on " + ", ".join(bad)
                     + ".\n      Some module-level state is randomised at import; the A/B result would be meaningless.")
        print(" Self-test (A/A, two copies of NEW): identical")

    user_frame = None
    if args.frame:
        user_frame = cv2.imread(args.frame)
        if user_frame is None:
            sys.exit(f"[A/B] cannot read --frame {args.frame}")
        user_frame = cv2.resize(user_frame, (new.FRAME_W, new.FRAME_H))
    bases = base_frames(new.FRAME_H, new.FRAME_W, user_frame)
    if args.quick:
        bases = {k: v for k, v in bases.items() if k in ("textured", "bright") or k == "userframe"}
    rng = np.random.default_rng(args.seed)
    case_no = 0

    # ---------------- 1. legacy order identity ----------------
    print("\n--- 1. LEGACY ORDER: NEW rewired to OLD's order and knobs must equal OLD (byte for byte) ---")
    print(f"     old order: {' > '.join(old_order)}")
    print(f"     old wiring: {old_att}")
    new.set_effect_config(order=old_order, attachments=old_att, windows=old_win)
    res_id = []
    for cname, knobs in identity_cases(args.random_cases, rng, corr_knob):
        for fname, base in bases.items():
            case_no += 1
            n_frames = max(args.frames, 12) if cname.startswith("warm-up") else args.frames
            res = run_case(old, new, f"{cname} / {fname}", knobs, frame_sequence(base, n_frames), args.seed + case_no)
            res_id.append(res)
            show(res, True)
    n_id_diff = sum(1 for r in res_id if not r["identical"])
    if n_id_diff:
        problems.append(f"1. legacy order: {n_id_diff}/{len(res_id)} cases differ between OLD and NEW")

    # ---------------- 2. default order ----------------
    print("\n--- 2. DEFAULT ORDER: corruption dormant must equal OLD; corruption active must differ ---")
    print(f"     new order: {' > '.join(defaults[0])}")
    new.set_effect_config(order=defaults[0], attachments=defaults[1], windows=defaults[2])
    res_dorm, res_act = [], []
    if corr_knob is None:
        print(" (no knob has Packet Corruption attached in NEW's defaults: section skipped)")
    else:
        for cname, knobs in corruption_dormant_cases(args.random_cases, rng, corr_knob):
            for fname, base in bases.items():
                case_no += 1
                res = run_case(old, new, f"{cname} / {fname}", knobs, frame_sequence(base, args.frames), args.seed + case_no)
                res_dorm.append(res)
                show(res, True)
        print("   --- negative control: corruption active ---")
        for cname, knobs, base_name in corruption_active_cases(corr_knob):
            if base_name not in bases:
                base_name = next(iter(bases))
            case_no += 1
            res = run_case(old, new, f"{cname} / {base_name}", knobs, frame_sequence(bases[base_name], args.frames), args.seed + case_no)
            res_act.append(res)
            show(res, False)
        n_dorm_diff = sum(1 for r in res_dorm if not r["identical"])
        if n_dorm_diff:
            problems.append(f"2. default order: {n_dorm_diff}/{len(res_dorm)} corruption-dormant cases differ from OLD")
        same_active = [r["name"] for r in res_act if r["identical"]]
        if same_active:
            problems.append("2. negative control failed (OLD == NEW with corruption active): " + ", ".join(same_active))

    # ---------------- 3. feature checks ----------------
    print("\n--- 3. FEATURE CHECKS (windows, order, governor, labels, validation, naked capture) ---")
    feats = feature_checks(new, defaults, bases, args.no_crt)
    bad_feats = [n_ for n_, ok in feats if not ok]
    if bad_feats:
        problems.append("3. feature checks failed: " + ", ".join(bad_feats))
    new.set_effect_config(order=defaults[0], attachments=defaults[1], windows=defaults[2])

    # ---------------- sanity ----------------
    allres = res_id + res_dorm + res_act
    black = [r["name"] for r in allres if r["all_black"]]
    if black:
        problems.append(f"{len(black)} cases produced an all-black output (comparison is vacuous), e.g. {black[0]}")
    changed = sum(1 for r in res_id if r["vs_input_differs"] > 0)
    if changed < 0.5 * len(res_id):
        problems.append(f"only {changed}/{len(res_id)} identity cases altered their input; the pipeline may not be doing anything")

    print("\n" + "=" * 74)
    print(f" 1. legacy-order cases compared : {len(res_id)}  (differing: {n_id_diff})   altered the input: {changed}/{len(res_id)}")
    print(f" 2. corruption dormant / active : {len(res_dorm)} same-expected, {len(res_act)} differ-expected")
    print(f" 3. feature checks              : {sum(1 for _, ok in feats if ok)}/{len(feats)} ok")
    if problems:
        print(" RESULT: FAIL")
        for p in problems:
            print(f"   - {p}")
        print(TUNING_NOTE)
        print("=" * 74)
        return 1
    print(" RESULT: PASS - wrapping changed nothing; the new features behave as specified")
    print("=" * 74)
    return 0


if __name__ == "__main__":
    sys.exit(main())
```
