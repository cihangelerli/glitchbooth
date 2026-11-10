"""Tuning: how the booth looks, feels and performs. ONE source of truth for every tunable.

Plain values you edit in this file. Moved from server.py unchanged and in the same order.
At the very end the startup check (the crash-limit tables and the two functions that apply
them) runs, so every importer of this file always sees the final values. Values that .env
controls are in config/settings.py.
"""

import logging

import numpy as np

logger = logging.getLogger("glitchbooth.tuning")

RECEIPT_WIDTH_PX = 576  # printer paper width in dots (a fact of the printer)
RECEIPT_QR_MIN_PX = 64  # the QR is never shrunk below this to fit
RECEIPT_DITHER = "floyd"  # how the receipt photo becomes black dots: "floyd" (smooth) or "none" (hard threshold)
FLASH_COLOUR = (
    255,
    255,
    255,
)  # (R, G, B) of the capture flash; the kiosk screen reads this too
REVIEW_DIM_FACTOR = 0.4  # after the flash the frozen photo is shown at this brightness (0-1) during the countdown screen
UI_GREEN = (0, 255, 0)  # (R, G, B) main terminal colour: text, borders, lines
UI_HIGHLIGHT = (209, 255, 201)  # a knob row that was just moved (#d1ffc9)
UI_GLOW_DIM = (0, 80, 0)  # soft shadow behind a highlighted knob row
UI_ALERT = (255, 60, 60)  # "HATA!" mark on a knob whose effect was switched off
UI_NOTICE = (255, 90, 90)  # message box text and border (camera or print problem)
UI_VIDEO_PLACEHOLDER = (20, 20, 25)  # video area while no camera frame has arrived
UI_KNOB_HIGHLIGHT_SECONDS = 0.2  # how long a moved knob row stays highlighted

# The pose countdown: the ONE place its length is set. The capture flow, the digits on the
# kiosk screen and the sentence on it all read this (pygame_display.py uses server.POSE_COUNTDOWN_SECONDS).
POSE_COUNTDOWN_SECONDS = 3
CAPTURE_LOCK_SECONDS = (
    1.2  # the button is ignored this long after a refused capture or a return to live
)
CAPTURE_KEEP_COUNT = (
    100  # newest captures kept in captures/ (older ones move to the archives)
)
GALLERY_PER_PAGE = 12  # captures per page on the gallery
# One source for the frame rate: what the camera is asked for (open_camera), the top rate of the
# processing loop and the pace of the live stream. When the active effects are expensive the loop
# slows down: GOVERNOR_STEPS = ((effect cost at or above, frames per second), ...), highest cost first.
CAMERA_FPS = 15
GOVERNOR_STEPS = ((0.75, 10), (0.55, 12))
# Live preview (the web page and control page; the saved photo is not affected)
STREAM_QUALITY_BEST = 65  # JPEG quality at the lowest effect cost ...
STREAM_QUALITY_WORST = 35  # ... falling to this at the highest
STREAM_QUALITY_IDLE = 20  # quality of the occasional frame encoded while nobody watches
PREVIEW_DOWNSCALE_FROM_INTENSITY = (
    0.55  # from this effect cost the preview is scaled down (PREVIEW_DOWNSCALE_WIDTH)
)
IDLE_PREVIEW_ENCODE_EVERY = (
    15  # with nobody watching, one frame in this many is still encoded
)

CRT_ENABLED = True
CRT_SCANLINE_STRENGTH = 0.12
CRT_APERTURE_STRENGTH = 0.04
CRT_GREEN_GLOW = 0.07

# RIGID CONTIGUOUS MEMORY ALLOCATIONS (ORDER='C') WITH SIMD LAYOUT PROTECTION
# ==============================================================================
FRAME_H = 480
FRAME_W = 640

# SCALABLE GLITCH MATRIX CONSTANTS
# ==============================================================================

# EFFECT TUNABLES: ORDER, KNOB ATTACHMENTS, WINDOWS, COST WEIGHTS, LABELS
# ==============================================================================
# Every effect is a handler. EFFECT_ORDER is the processing order, top to bottom:
# an effect works on the result of the effects above it, so LATER = ON TOP.
# An effect that is not attached to any knob is skipped and costs nothing.
# The CRT scanline overlay always runs last and is not part of this list.
EFFECT_ORDER = [
    "pixellation",
    "signal_drift",
    "melt",
    "data_mosh",
    "packet_corruption",
    "ascii_matrix",
    "posterize",
    "chroma_shift",
    "slice_glitch",
]

# Knob (0-5) -> effects. The FIRST effect is its visible primary name; remaining
# effects are secondary and react to the same knob without appearing in the UI.
KNOB_ATTACHMENTS = {
    0: ["ascii_matrix"],
    1: ["signal_drift"],
    2: ["posterize"],
    3: ["pixellation"],
    4: ["melt", "data_mosh"],
    5: ["chroma_shift", "slice_glitch", "packet_corruption"],
}

# Optional range of the knob in which an effect works, as fractions of the knob travel.
# Inside its window the effect sees the full 0-1023 range; outside it is off.
# Example: {"chroma_shift": (0.0, 0.7), "packet_corruption": (0.35, 1.0)}
EFFECT_KNOB_WINDOW = {
    "chroma_shift": (0.20, 1.0),
    "slice_glitch": (0.0, 1.0),
    "packet_corruption": (0.0, 1.0),
}

# Cost weights follow effects when they move between knobs.
EFFECT_COST_WEIGHT = {
    "ascii_matrix": 0.10,
    "signal_drift": 0.07,
    "data_mosh": 0.27,
    "pixellation": 0.07,
    "melt": 0.27,
    "chroma_shift": 0.05,
    "slice_glitch": 0.02,
    "packet_corruption": 0.0,
    "posterize": 0.15,
}

EFFECT_SKIP_DURING_WARMUP = ("data_mosh",)

EFFECT_LABELS = {
    "ascii_matrix": "ASCII Matrix",
    "signal_drift": "Signal Drift",
    "data_mosh": "Data Corruption",
    "pixellation": "Pixel Damage",
    "melt": "Data Melt",
    "chroma_shift": "Chroma Shift",
    "slice_glitch": "Slice Glitch",
    "packet_corruption": "Packet Corruption",
    "posterize": "Posterize",
}
KNOB_EMPTY_LABEL = "-"
# The clean (unprocessed) frame is saved as <id>_clean.jpg and uploaded next to the processed
# photo, so the website can offer a clean/processed slider.
CLEAN_JPEG_QUALITY = 88  # 85-90
# JPEG quality of the processed colour and black-and-white photos (1-100). Lower = smaller files,
# faster uploads, softer picture. It used to be OpenCV's unnamed default of 95.
CAPTURE_JPEG_QUALITY = 90

# STATISTICS TUNABLES
# ==============================================================================
# Every capture is counted by effect NAME (all attached effects, primary and hidden), when the
# effect was acting (see EFFECT ACTIVITY below).
# Name under which each effect is counted in stats.json and in the capture ledger.
# Keep these stable: renaming one splits its history. Pixellation keeps its historical key.
EFFECT_STATS_NAME = {
    "ascii_matrix": "ascii_matrix",
    "signal_drift": "signal_drift",
    "data_mosh": "data_mosh",
    "pixellation": "pixel_damage",
    "melt": "melt",
    "chroma_shift": "chroma_shift",
    "slice_glitch": "slice_glitch",
    "packet_corruption": "packet_corruption",
    "posterize": "posterize",
}

# EFFECT ACTIVITY: ONE DEFINITION OF "THIS EFFECT IS ACTING"
# ------------------------------------------------------------------------------
# The effect handlers, the statistics and Posterize's ASCII check all ask the same question,
# acting(effect, raw_knob) (defined next to the effect handlers), and it is answered from the
# numbers below and from each effect's own tunables (MOSH_DORMANT_BELOW, CORRUPTION_SPAWN_ABOVE,
# POSTERIZE_CURVE, DRIFT_MAX_AMOUNT). "Acting" means the picture really changes. Change a number
# here and the effect and its statistics move together. Strengths are the knob after its window,
# as a share of the travel (0-1), unless the name says KNOB (units 0-1023).
PIXELLATION_ACTIVE_ABOVE = 0.0  # acts above this strength (0 = from knob 1)
ASCII_ACTIVE_ABOVE = 0.02  # knob ~21
MELT_ACTIVE_ABOVE = 0.03  # knob ~31
CHROMA_ACTIVE_ABOVE = 0.05  # knob ~52
SLICE_ACTIVE_FROM_KNOB = 11  # first slice appears at this knob value (0-1023)
DRIFT_AMOUNT_SCALE = 0.6  # sideways drift in px = strength * DRIFT_MAX_AMOUNT * this
DRIFT_MIN_AMOUNT = (
    2  # smaller amounts round to a 0 px shift, so drift starts acting at this
)
# Signal Drift's vertical roll above this strength acts whatever the sideways amount is.
DRIFT_ROLL_ABOVE = 0.85

# Statistics count an effect only when it is acting AND its (windowed) knob is at least this,
# so a knob resting at 1-10 (electrical noise) does not count Pixel Damage or Drift as used.
EFFECT_STATS_MIN_KNOB = 11

# NEON_PALETTES defined in native OpenCV BGR format: [Blue, Green, Red]
# these palettes are shared by Melt and Posterize, CORRECT hexcodes should ALWAYS be here for reference.

NEON_PALETTES = np.array(
    [
        [0, 255, 255],  # Neon Yellow           (B: 0,   G: 255, R: 255) #FFFF00
        [255, 0, 255],  # Hot Magenta           (B: 255, G: 0,   R: 255) #FF00FF
        [255, 255, 0],  # Electric Cyan         (B: 255, G: 255, R: 0)   #00FFFF
        [0, 255, 0],  # Acid Green            (B: 0,   G: 255, R: 0)   #00FF00
        [255, 0, 128],  # Deep Violet           (B: 255, G: 0,   R: 128) #8000FF
        [0, 165, 255],  # Tangerine             (B: 0,   G: 165, R: 255) #FFA500
        [128, 0, 255],  # Neon Rose             (B: 128, G: 0,   R: 255) #FF0080
        [0, 255, 127],  # Chartreuse            (B: 0,   G: 255, R: 127) #7FFF00
        # [132, 83, 255],  # Electric Pink         (B: 132, G: 83,  R: 255) #FF5384
        # [104, 223, 255],  # Bright Yellow         (B: 104, G: 223, R: 255) #FFDF68
        [199, 228, 101],  # Neon Teal             (B: 199, G: 228, R: 101) #65E4C7
        # [137, 231, 110],  # Neon Limey Green      (B: 137, G: 231, R: 110) #6EE789
        [51, 18, 255],  # Neon Red              (B: 51,  G: 18,  R: 255) #FF1233
        [52, 112, 255],  # Neon Orange           (B: 52,  G: 112, R: 255) #FF7034
    ],
    dtype=np.uint8,
    order="C",
)
NEON_PALETTES_FLOAT = np.ascontiguousarray(NEON_PALETTES.astype(np.float32))

# POSTERIZE TUNABLES (neon tone bands with glitchy outlines)
# ==============================================================================
# Posterize acts only while it is attached to a knob in KNOB_ATTACHMENTS (a knob can
# drive several effects, for example 3: ["pixellation", "posterize"]).
POSTERIZE_CURVE = (
    (0, 0.00),
    (12, 0.00),
    (25, 0.02),
    (50, 0.05),
    (75, 0.08),
    (110, 0.12),
    (150, 0.17),
    (190, 0.22),
    (250, 0.29),
    (325, 0.36),
    (400, 0.43),
    (475, 0.50),
    (580, 0.60),
    (700, 0.72),
    (760, 0.78),
    (830, 0.85),
    (900, 0.92),
    (960, 0.97),
    (1023, 1.00),
)

POSTERIZE_BLACK_THRESHOLD = (
    24  # grey level below which the original pixel is kept exactly
)
POSTERIZE_BLACK_RAMP = (
    34  # between the two values the neon is mixed in at half strength
)
POSTERIZE_BANDS_LOW = 3  # tone bands at the weakest strength ...
POSTERIZE_BANDS_HIGH = 11  # ... and at full strength
POSTERIZE_DETAIL_LOW = (
    0.95  # how much of the original brightness modulates the neon (weak) ...
)
POSTERIZE_DETAIL_HIGH = 0.37  # ... and at full strength (faces stay readable)
POSTERIZE_MIX_START = 0.05  # neon share at the weakest strength ...
POSTERIZE_MIX_FULL_AT = 0.31  # ... reaching 1.0 at this strength (0-1)
POSTERIZE_DROPOUT_LOW = 0.05  # share of the outline that is missing (weak) ...
POSTERIZE_DROPOUT_HIGH = 0.01  # ... and at full strength
POSTERIZE_JITTER_MAX_PX = (
    0  # outline rows shift sideways by up to 1 .. this many px (0 = off, step skipped)
)
# Flashing outline sections (the "flicker"): a share of the outline sections lights up for one frame.
POSTERIZE_FLICKER_ENABLED = (
    True  # False = no flashing sections at all (the whole step is skipped)
)
POSTERIZE_FLICKER_PROB = 0.05  # share of outline sections that flash
# "neon" = each flashing section takes a random colour of the neon palette, "white" = white-hot
POSTERIZE_FLICKER_COLOUR = "neon"
# ASCII CLARITY: ONE DIAL for how readable Posterize stays while the ASCII effect is acting.
# 0 = Posterize looks the same with and without ASCII; 1 = the most readable faces and details.
# While ASCII acts, clarity does three things together:
#   1. the solarize fold is pushed toward the highlights (so skin tones are no longer folded back),
#   2. the brightness detail of the "bands" fill falls only (1 - clarity) as far as it normally does,
#   3. extra outline sections are removed (POSTERIZE_ASCII_CLARITY_DROPOUT).
# It is read every frame, so it can be tuned live.
POSTERIZE_ASCII_CLARITY = 0.75
POSTERIZE_ASCII_CLARITY_DROPOUT = (
    0.5  # advanced: share of the remaining outline sections removed at clarity 1
)
POSTERIZE_ASCII_OUTLINE_STYLE = (
    "glow"  # outline colour while ASCII acts (same choices as POSTERIZE_OUTLINE_STYLE)
)
POSTERIZE_BLACK_RAMP_MIX = 0.5  # in the narrow ramp above black: share of the neon picture mixed with the original
POSTERIZE_HALFTONE_COVER_GAIN = (
    1.12  # dot coverage gain; above 1 lets the highlights close up fully
)
POSTERIZE_GLOW_CORE_LIFT = (
    0.5  # the glow core is the glow colour lifted this far toward white (0-1)
)
# Outline colour: "glow" = the brightest neon next to the border (often near-white), "white", "black" (ink)
POSTERIZE_OUTLINE_STYLE = "black"
POSTERIZE_OUTLINE_MID_FROM = 0.85  # strength from which outlines are MID px thick
POSTERIZE_OUTLINE_MAX_FROM = 0.99  # strength from which outlines are MAX px thick
POSTERIZE_OUTLINE_MAX_PX = 1  # maximum outline thickness (min to mid to max)
POSTERIZE_OUTLINE_MID_PX = 1  # mid-level outline thickness
POSTERIZE_OUTLINE_MIN_PX = 1  # minimum outline thickness
POSTERIZE_SECTION_PX = (
    2  # size of the dropout / flicker sections (must divide 640 and 480)
)
POSTERIZE_FRAGMENT_FROM = 0.61  # strength from which fine detail starts to break apart
POSTERIZE_FRAGMENT_AMOUNT = 1  # grey levels of break-up noise at full strength
POSTERIZE_ROTATE_SECONDS = (
    7  # 0 = fixed colours; N = the colour assignment shifts every N seconds
)

# ------------------------------------------------------------------------------
# OPTIONAL LOOKS. They are read every frame, so they can be switched live (set the module
# variable); only the halftone pitch / angle rebuild a small table when they change.
# ------------------------------------------------------------------------------
# Colour mode of the neon fill:
#   "bands"    = hard tone bands from the neon palette (the original Posterize)
#   "gradient" = smooth gradient map through the neon palette (dark -> bright)
#   "thermal"  = fixed thermal-camera ramp (ignores the neon palette)
POSTERIZE_COLOR_MODE = "bands"  # used by POSTERIZE_MODE_SOURCE = "fixed" (and when a table below is invalid)
# What chooses the colour mode:
#   "knob"     = the position of the Posterize knob (POSTERIZE_KNOB_MODES below)
#   "schedule" = time (POSTERIZE_MODE_SCHEDULE below)
#   "fixed"    = always POSTERIZE_COLOR_MODE
POSTERIZE_MODE_SOURCE = "knob"
# Mode by knob position: (lower edge, upper edge, mode), edges as a share of the knob travel
# (0.0 = knob fully down, 1.0 = fully up; 0.35 is about knob value 358). Where the ranges of two
# entries OVERLAP the two modes are blended across the overlap (a gradual change); where they only
# touch the mode changes at once. A mode can itself be an overlay of two, "bands+thermal"
# (shown mixed by POSTERIZE_OVERLAY_MIX). Example with blending: ((0.0, 0.40, "bands"),
# (0.30, 0.75, "gradient"), (0.65, 1.0, "thermal")). A gap between two entries keeps the mode below it.
POSTERIZE_KNOB_MODES = (
    (0.0, 0.45, "bands"),  # lowest zone
    (
        0.35,
        0.75,
        "gradient",
    ),  # middle zone (blended with the zone below where they overlap)
    (0.75, 1.0, "thermal"),  # top zone
)
# The knob must move this far (share of the travel) before the mode selection follows, so a knob
# resting on an edge does not flip the mode back and forth. 0 = off.
POSTERIZE_KNOB_MODE_HYSTERESIS = 0.01
# Timed mode changes, like POSTERIZE_ROTATE_SECONDS but per mode: (mode, seconds) pairs (used by "schedule")
# played in a loop. Use 2 or 3 of the modes, with any duration each. A mode can also be an
# overlay of two modes, written "bands+thermal" (shown mixed by POSTERIZE_OVERLAY_MIX).
# Example: (("bands", 40), ("thermal", 15), ("gradient", 5)).   () = fixed mode above.
POSTERIZE_MODE_SCHEDULE = (("bands+gradient", 25), ("thermal", 10))
POSTERIZE_MODE_FADE_SECONDS = 0  # cross-fade length at each change (0 = hard cut)
POSTERIZE_OVERLAY_MIX = 0.4  # share of the 2nd mode in an "a+b" overlay (0-1)
POSTERIZE_GRADIENT_STOPS = 11  # palette colours the gradient map blends through (2+)
POSTERIZE_THERMAL_STOPS = (  # (grey level 0-255, (B, G, R)), blended in between
    (0, (0, 0, 0)),
    (45, (140, 0, 40)),  # deep blue-violet
    (90, (160, 0, 200)),  # magenta
    (130, (30, 30, 255)),  # red
    (170, (0, 140, 255)),  # orange
    (210, (0, 235, 255)),  # yellow
    (255, (255, 255, 255)),  # white-hot
)

# Solarize (Sabattier): folds the tone curve, so highlights fall back to dark tones.
# Works on the tones before the colour step, so it combines with every colour mode.
POSTERIZE_SOLARIZE = True
POSTERIZE_SOLARIZE_STYLE = "fold"  # "fold" = smooth triangle, "invert" = hard flip
POSTERIZE_SOLARIZE_POINT = 150  # grey level (16-239) where the curve turns around

# Halftone: the neon fill becomes a dot screen (bigger dots = brighter tone).
POSTERIZE_HALFTONE = False
POSTERIZE_HALFTONE_PITCH = 11  # px between dot centres (8+ survives phone JPEG)
POSTERIZE_HALFTONE_ANGLE = 45  # screen angle in degrees
POSTERIZE_HALFTONE_SOFTNESS = 0.5  # dot edge softness in px (lower = harder dots)
POSTERIZE_HALFTONE_GAP = 0.0  # brightness left between the dots (0 = black)
POSTERIZE_HALFTONE_FROM = 0.2  # strength (0-1) from which the dots appear
# Which tone bands get dots (0 = darkest band, 1 = brightest band; the rest stay solid):
POSTERIZE_HALFTONE_TONE_FROM = 0.0  # e.g. 0.0 / 0.5 = only the darker half gets dots
POSTERIZE_HALFTONE_TONE_TO = 0.5  # e.g. 0.5 / 1.0 = only the brighter half gets dots
POSTERIZE_HALFTONE_ALTERNATE = False  # True = only every 2nd band inside that range

# Neon edge glow: a halo (and optionally a bright neon core) on the outline.
POSTERIZE_EDGE_GLOW = False
POSTERIZE_GLOW_COLOR = (255, 255, 0)
# (B, G, R) (255, 255, 0) electric cyan, or "palette" = local neon
POSTERIZE_GLOW_RADIUS_PX = 7  # halo size
POSTERIZE_GLOW_GAIN = 1.2  # halo brightness (about 0.6 - 3)
POSTERIZE_GLOW_CLEAN = 7  # 3, 5, 7: larger = the halo follows only bigger tone borders
POSTERIZE_GLOW_DIM = (
    0.9  # darkens the picture a little so the glow reads as light (1 = off)
)
POSTERIZE_GLOW_CORE = True  # also paint the outline itself in bright neon
POSTERIZE_GLOW_KEEP_BLACKS = True  # no glow spill onto the untouched black areas
POSTERIZE_GLOW_FROM = 0.0  # strength (0-1) from which the glow appears


# DATA MOSH AND PACKET CORRUPTION TUNABLES
# ==============================================================================

# --- Data Mosh -------------------------------------------------------------------
MOSH_DORMANT_BELOW = (
    0.02  # strength = (knob/1023)**2 below this cancels everything at once
)
MOSH_THRESH_BASE = (
    37  # motion threshold = BASE + (1 - strength) * SPAN   (37 .. 90 now)
)
MOSH_THRESH_SPAN = 53
MOSH_HOLD_MIN_FRAMES = 17  # hold time (frames) rises from MIN to MAX with strength
MOSH_HOLD_MAX_FRAMES = 49
# Per-frame drag distance in any of four directions: left, right, up, or down.
MOSH_VECTOR_PX = 9  # pixels per frame (the engine limits it to 0 .. frame height)
MOSH_DRAG_NEW_WEIGHT = 222 / 256  # share of the dragged picture per frame
MOSH_EDGE_ERODE_MAX_PX = 0  # random edge erosion 0..N px on held cells
MOSH_RELEASE_FRAMES = 11  # edges dissolve over the last N frames of a hold
MOSH_RELEASE_ERODE_PX = 1  # extra erosion per release frame
# Colour-plane bleed inside held cells: "shape_live_colour_held", "shape_held_colour_live", "off"
MOSH_BLEED_MODE = "shape_live_colour_held"
MOSH_BLEED_MAX = 0.23  # bleed mix = MAX * strength (0-1)

MOSH_CELL_H = 16
MOSH_CELL_W = 12

# All Data Mosh tuning lives in this block. Nothing limits or resets these values;
# only an unknown MOSH_BLEED_MODE name is reported at startup.

# --- Corruption -------------------------------------------------------------------
CORRUPTION_CELL_H = 12
CORRUPTION_CELL_W = 16
CORRUPTION_CLEAR_BELOW = 0.01  # below this strength all damage is cleared
CORRUPTION_SPAWN_ABOVE = 0.012  # new damage only spawns above this strength
CORRUPTION_SPAWN_RATE = 0.23  # spawn chance per frame = strength * RATE
CORRUPTION_SQUARE_PROB = 0.60  # square block (else a full-height column)
CORRUPTION_SQUARE_MAX_CELLS = 4  # square block is 1..N cells each way
CORRUPTION_THIN_COLUMN_PROB = 0.9  # column is 1 cell wide (else 2)
CORRUPTION_COLUMN_NOISE_PROB = 0.70  # column kind: noise below this roll
CORRUPTION_COLUMN_BLACK_PROB_END = 0.95  # black up to this roll, white above
CORRUPTION_DECAY = 0.9  # energy multiplier per frame
CORRUPTION_CLEAR_ENERGY = 0.005  # damage at or below this energy disappears
CORRUPTION_BLEND_BELOW = 0.95  # below this energy the damage is blended, not solid

# --- Signal Drift -------------------------------------------------------------------
DRIFT_MAX_AMOUNT = 60  # [320x240: 30] [480x360: 45] [640x480: 60]
DRIFT_SEAM_FRACTION = (
    0.04  # thickness of the black seam at the vertical roll (share of the frame height)
)
DRIFT_WAVE_PER_ROW = (
    0.05  # radians per picture row: the wave is about 2*pi/this = 126 rows long
)
DRIFT_WAVE_SPEED = 0.15  # radians per second: one wave cycle in about 42 s
DRIFT_ROLL_MAX_FRACTION = (
    0.5  # the vertical roll scrolls at most this share of the height (0-0.5)
)

# --- Pixellation -------------------------------------------------------------------
COMPRESSION_TILE_BASE = 4  # [320x240: 6 ] [480x360: 9 ] [640x480: 12]
COMPRESSION_TILE_SCALE = 9  # [320x240: 3 ] [480x360: 4 ] [640x480: 6 ]

# --- Chroma Shift -------------------------------------------------------------------
ANA_SHIFT_MAX = 48  # [320x240: 18] [480x360: 27] [640x480: 36]
ANA_GHOST_OFFSET = 40  # [320x240: 20] [480x360: 30] [640x480: 40]
CHROMA_GHOST_MIX = (
    0.20  # share of the shifted ghost copy blended over the picture (0-1)
)

# --- Slice Glitch -------------------------------------------------------------------
SLICE_CLOCK_HZ = 15  # the slices are re-rolled on this clock ...
SLICE_EVERY_N_STEPS = (
    3  # ... on every Nth step of it (15 / 3 = 5 new slice sets a second)
)
SLICE_COUNT_MAX = (
    4  # slices at full strength = this * strength ** SLICE_COUNT_CURVE (at least 1)
)
SLICE_COUNT_CURVE = 0.5  # below 1 = more slices sooner at low strength
SLICE_MIN_H = 8  # [320x240: 4 ] [480x360: 6 ] [640x480: 8 ]
SLICE_MAX_H = 36  # [320x240: 12] [480x360: 18] [640x480: 24]
SLICE_MAX_SHIFT = 50  # [320x240: 15] [480x360: 23] [640x480: 30]

# --- Melt -------------------------------------------------------------------
MELT_CHUNK_ROWS = 4096  # drip-rows processed per chunk. Keeps temporaries in cache.
# Tune 2048-16384 on the Pi; it only affects speed.

MELT_STRIDE_LOW = 6
MELT_STRIDE_MID = 3
MELT_STRIDE_HIGH = 2  # Safe min stride for maintaining high FPS on Pi 5 - 1 GB is 2
MAX_MELT_DRIPS = 120  # Balanced aesthetic ceiling 100
MELT_DRIP_WIDTH = 8  # Thick, substantial drips when the number is bigger. 75 drips and 10 width, 100 drips and 8 width are good combos
MELT_NEON_START_PCT = 0.67  # Where neon begins: 0.0 = top of drip, 0.5 = halfway down
MELT_NEON_RAMP_SPAN = (
    0.23  # Length of the transition ramp to full neon (Start + Span should equal 1.0)
)
MELT_NEON_MIX = (
    0.5  # drip neon = (1 - this) of one palette colour + this of another (0-1)
)
MELT_BASE_LENGTH_PX = (
    20  # longest drip = this + strength * MELT_LENGTH_FRACTION * frame height
)
MELT_LENGTH_FRACTION = 0.85
MELT_LENGTH_FIXED_SHARE = 0.45  # a drip is this share fixed, the rest random (0-1)
MELT_STRETCH_MAX = 3.0  # stretch of the drip texture is random between 1 and this
MELT_THRESHOLD_FLOOR = (
    75  # a pixel starts a drip above 255 * (1 - FALL * strength), never below this
)
MELT_THRESHOLD_FALL = 0.65
MELT_STRIDE_MID_FROM = (
    0.35  # strength from which the columns are sampled at MELT_STRIDE_MID ...
)
MELT_STRIDE_HIGH_FROM = 0.7  # ... and from which at MELT_STRIDE_HIGH
MELT_SEED_STEP_MAX = (
    8  # spacing between drips in a column = (ZERO_AT - strength) * this ...
)
MELT_SEED_STEP_ZERO_AT = 1.1
MELT_SEED_STEP_MIN = 2  # ... but never closer than this
MELT_SEEDS_LOW = 3  # drips per column below MELT_SEEDS_HIGH_FROM ...
MELT_SEEDS_HIGH = 6  # ... and from it on
MELT_SEEDS_HIGH_FROM = 0.65
MELT_REROLL_HZ = 4  # the drip pattern is re-rolled this many times a second
MELT_BOTTOM_MARGIN_PX = 4  # no drips start in the bottom rows


# --- CRT SCANLINES -------------------------------------------------------------------
CRT_SCANLINE_PERIOD = 8  # [320x240: 4 ] [480x360: 6 ] [640x480: 8 ]
CRT_SCANLINE_THICKNESS = 4  # [320x240: 2 ] [480x360: 3 ] [640x480: 4 ]
CRT_APERTURE_PERIOD = 6  # [320x240: 3 ] [480x360: 4 ] [640x480: 6 ]

PREVIEW_DOWNSCALE_WIDTH = (
    480  # [320x240: 240] [480x360: 320] [640x480: 480 or keep 240]
)

# --- ASCII grid -------------------------------------------------------------------
ASCII_GRID_W = 80  # character columns
ASCII_GRID_H = 60  # character rows
ASCII_COLOUR = (
    0,
    255,
    0,
)  # text colour (B, G, R): the brightness of each character times this / 255
ASCII_AUTO_CONTRAST = (
    True  # stretch the character grid to the full brightness range every frame
)
ASCII_MIX_CURVE = 1.0  # ASCII share in the picture = strength ** this (1 = linear)
ASCII_FONT_EXTRA_PX = 2  # character size = the cell size + this
ASCII_FONT_LIFT_PX = 1  # characters are drawn this many px higher inside their cell
# Sparks: a few characters flash bright green; they are re-rolled every few frames.
ASCII_SPARK_INTERVAL = (7, 16)  # frames between re-rolls, from ... to (inclusive)
ASCII_SPARK_START_FRAMES = 14  # frames until the very first re-roll
ASCII_SPARK_LENGTH = (1, 6)  # spark length in cells, from ... to (inclusive)
# Rarity: threshold on a random 0-255 noise = base - slope * strength, kept between floor and base.
ASCII_SPARK_RARITY = (
    254,
    3,
    240,
)  # (base, slope, floor): a lower threshold = more sparks
ASCII_SPARK_COLOUR = (31, 255, 31)  # (B, G, R)
ASCII_BLOOM_BLUR = 3  # blur size of the glow in cells (odd number)
ASCII_TEXT_GAIN = 0.8  # brightness of the sharp text ...
ASCII_BLOOM_GAIN = 0.4  # ... and of the glow added to it (together above 1 on purpose)
ASCII_CLAHE_CLIP = (
    3.0  # contrast boost of the character grid (higher = harsher, 1 = almost none)
)
ASCII_CLAHE_TILES = (
    8,
    8,
)  # the picture is split into this many tiles (across, down) for local contrast

# STARTUP CHECK: VALUES THAT WOULD CRASH THE BOOTH
# ==============================================================================
# This is NOT a tuning range: it lists the smallest value each tunable can have before the
# booth crashes (a size of 0 divides by zero, a negative range is empty). Everything at or
# above the minimum is yours and is never touched, and you never edit this table when tuning.
# A tunable below its minimum switches off ONLY the effect named here, for this run, with an
# ERROR line in the log and a warning on the booth's knob panel. The placeholder minimum only
# lets the buffers below be built; an effect that is switched off never uses them.
# Format: name: (minimum, effect, strict). strict=True means the value must be ABOVE the minimum.
_CRASH_LIMITS = {
    "ASCII_GRID_W": (1, "ascii_matrix", False),
    "ASCII_GRID_H": (1, "ascii_matrix", False),
    "MOSH_CELL_H": (1, "data_mosh", False),
    "MOSH_CELL_W": (1, "data_mosh", False),
    "MOSH_EDGE_ERODE_MAX_PX": (0, "data_mosh", False),
    "CORRUPTION_CELL_H": (1, "packet_corruption", False),
    "CORRUPTION_CELL_W": (1, "packet_corruption", False),
    "CORRUPTION_SQUARE_MAX_CELLS": (1, "packet_corruption", False),
    "POSTERIZE_SECTION_PX": (1, "posterize", False),
    "POSTERIZE_MIX_FULL_AT": (0, "posterize", True),
    "POSTERIZE_JITTER_MAX_PX": (0, "posterize", False),
    "POSTERIZE_OUTLINE_MIN_PX": (0, "posterize", False),
    "POSTERIZE_OUTLINE_MID_PX": (0, "posterize", False),
    "POSTERIZE_OUTLINE_MAX_PX": (0, "posterize", False),
    "MELT_CHUNK_ROWS": (1, "melt", False),
    "MELT_STRIDE_LOW": (1, "melt", False),
    "MELT_STRIDE_MID": (1, "melt", False),
    "MELT_STRIDE_HIGH": (1, "melt", False),
    "MELT_DRIP_WIDTH": (1, "melt", False),
    "SLICE_MIN_H": (0, "slice_glitch", False),
    "SLICE_MAX_SHIFT": (1, "slice_glitch", False),
    "SLICE_EVERY_N_STEPS": (1, "slice_glitch", False),  # a modulo by it
    "MELT_SEED_STEP_MIN": (1, "melt", False),  # a modulo by it
    "DRIFT_ROLL_MAX_FRACTION": (0, "signal_drift", False),
    "POSTERIZE_BANDS_LOW": (1, "posterize", False),
    "POSTERIZE_BANDS_HIGH": (1, "posterize", False),
}
# The largest value a tunable can have before the booth crashes (same rule as above).
_CRASH_LIMITS_MAX = {
    "DRIFT_ROLL_MAX_FRACTION": (
        0.5,
        "signal_drift",
    ),  # the roll buffer holds half the height
    "POSTERIZE_BANDS_LOW": (256, "posterize"),  # the tone table has 256 entries
    "POSTERIZE_BANDS_HIGH": (256, "posterize"),
}

DISABLED_EFFECTS = set()


def _disable_effect(effect, name, value, rule):
    DISABLED_EFFECTS.add(effect)
    logger.error(
        "[CONFIG] %s=%r %s: the %s effect is switched off for this run",
        name,
        value,
        rule,
        effect,
    )


def _apply_crash_limits():
    for name, (minimum, effect, strict) in _CRASH_LIMITS.items():
        value = globals()[name]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue  # a wrong type is not covered here
        if value < minimum or (strict and value <= minimum):
            above = "above" if strict else "at least"
            _disable_effect(
                effect,
                name,
                value,
                f"would crash the booth (must be {above} {minimum})",
            )
            if not strict:
                globals()[name] = (
                    minimum  # placeholder so the buffers below can be built
                )
    sp = POSTERIZE_SECTION_PX
    if "posterize" not in DISABLED_EFFECTS and (FRAME_W % sp or FRAME_H % sp):
        _disable_effect(
            "posterize",
            "POSTERIZE_SECTION_PX",
            sp,
            f"must divide {FRAME_W} and {FRAME_H}",
        )
    if "slice_glitch" not in DISABLED_EFFECTS and not (
        SLICE_MIN_H < SLICE_MAX_H < FRAME_H
    ):
        _disable_effect(
            "slice_glitch",
            "SLICE_MAX_H",
            SLICE_MAX_H,
            f"must be above SLICE_MIN_H ({SLICE_MIN_H}) and below {FRAME_H}",
        )
    if MOSH_BLEED_MODE not in (
        "shape_live_colour_held",
        "shape_held_colour_live",
        "off",
    ):
        logger.warning(
            "[CONFIG] MOSH_BLEED_MODE=%r is not a known mode; Data Mosh will behave like 'shape_held_colour_live'",
            MOSH_BLEED_MODE,
        )


def _is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _is_int_pair(value, low=1):
    # (from, to) with low <= from <= to, whole numbers
    return (
        isinstance(value, (tuple, list))
        and len(value) == 2
        and all(isinstance(v, int) and not isinstance(v, bool) for v in value)
        and low <= value[0] <= value[1]
    )


def _is_triple(value, low=None, high=None):
    return (
        isinstance(value, (tuple, list))
        and len(value) == 3
        and all(_is_number(v) for v in value)
        and all(low is None or v >= low for v in value)
        and all(high is None or v <= high for v in value)
    )


# Tunables that belong to no single effect: a value outside its range would crash a loop, so it is
# replaced by the default and the log says so (ERROR). (minimum, maximum, default, whole number only)
_FEATURE_LIMITS = {
    "CAMERA_FPS": (1, 120, 15, False),
    "STREAM_QUALITY_BEST": (1, 100, 65, True),
    "STREAM_QUALITY_WORST": (1, 100, 35, True),
    "STREAM_QUALITY_IDLE": (1, 100, 20, True),
    "CAPTURE_JPEG_QUALITY": (1, 100, 90, True),
    "POSE_COUNTDOWN_SECONDS": (1, 99, 3, True),
    "CAPTURE_LOCK_SECONDS": (0, 60, 1.2, False),
    "CAPTURE_KEEP_COUNT": (1, 1000000, 100, True),
    "GALLERY_PER_PAGE": (1, 1000, 12, True),
    "IDLE_PREVIEW_ENCODE_EVERY": (1, 1000000, 15, True),
    "RECEIPT_WIDTH_PX": (8, 4000, 576, True),
    "RECEIPT_QR_MIN_PX": (1, 4000, 64, True),
    "UI_KNOB_HIGHLIGHT_SECONDS": (0, 60, 0.2, False),
    # characters must stay at least 1 px tall
    # (max(..., 1): this table is built before the check below can replace a 0 grid size, and a
    # 0 here used to crash the import with ZeroDivisionError instead of switching ASCII off)
    "ASCII_FONT_EXTRA_PX": (
        1 - max(FRAME_W // max(ASCII_GRID_W, 1), FRAME_H // max(ASCII_GRID_H, 1)),
        200,
        2,
        True,
    ),
}
_COLOUR_TUNABLES = {  # (R, G, B), each 0-255
    "FLASH_COLOUR": (255, 255, 255),
    "UI_GREEN": (0, 255, 0),
    "UI_HIGHLIGHT": (209, 255, 201),
    "UI_GLOW_DIM": (0, 80, 0),
    "UI_ALERT": (255, 60, 60),
    "UI_NOTICE": (255, 90, 90),
    "UI_VIDEO_PLACEHOLDER": (20, 20, 25),
}


def _apply_new_limits():
    # 1. Upper limits of effect tunables (the minimums are in _CRASH_LIMITS).
    for name, (maximum, effect) in _CRASH_LIMITS_MAX.items():
        value = globals()[name]
        if _is_number(value) and value > maximum:
            _disable_effect(
                effect,
                name,
                value,
                f"would crash the booth (must be at most {maximum})",
            )
            globals()[name] = maximum  # placeholder so the buffers below can be built
    # 2. Structure of the ASCII tunables (a bad one switches ASCII off for this run).
    for name, ok, rule in (
        (
            "ASCII_SPARK_INTERVAL",
            _is_int_pair,
            "must be two whole numbers (from, to) with 1 <= from <= to",
        ),
        (
            "ASCII_SPARK_LENGTH",
            _is_int_pair,
            "must be two whole numbers (from, to) with 1 <= from <= to",
        ),
        (
            "ASCII_SPARK_RARITY",
            _is_triple,
            "must be three numbers (base, slope, floor)",
        ),
        ("ASCII_COLOUR", _is_triple, "must be three numbers (B, G, R)"),
        ("ASCII_SPARK_COLOUR", _is_triple, "must be three numbers (B, G, R)"),
    ):
        if "ascii_matrix" not in DISABLED_EFFECTS and not ok(globals()[name]):
            _disable_effect("ascii_matrix", name, globals()[name], rule)
    blur = ASCII_BLOOM_BLUR
    if "ascii_matrix" not in DISABLED_EFFECTS and not (
        isinstance(blur, int)
        and not isinstance(blur, bool)
        and blur >= 1
        and blur % 2 == 1
    ):
        _disable_effect(
            "ascii_matrix",
            "ASCII_BLOOM_BLUR",
            blur,
            "must be an odd whole number, 1 or more",
        )
    tiles = ASCII_CLAHE_TILES
    if "ascii_matrix" not in DISABLED_EFFECTS and not (
        isinstance(tiles, (tuple, list))
        and len(tiles) == 2
        and all(
            isinstance(v, int) and not isinstance(v, bool) and v >= 1 for v in tiles
        )
        and _is_number(ASCII_CLAHE_CLIP)
    ):
        _disable_effect(
            "ascii_matrix",
            "ASCII_CLAHE_TILES",
            tiles,
            "must be two whole numbers, 1 or more (and ASCII_CLAHE_CLIP a number)",
        )
    # 3. Values of features that belong to no effect: reset to the default.
    for name, (minimum, maximum, default, whole) in _FEATURE_LIMITS.items():
        value = globals()[name]
        good = _is_number(value) and minimum <= value <= maximum
        if good and whole:
            good = isinstance(value, int) or float(value).is_integer()
            if good:
                globals()[name] = int(value)
        if not good:
            logger.error(
                "[CONFIG] %s=%r is not usable (%s%s to %s): the default %r is used",
                name,
                value,
                "a whole number, " if whole else "",
                minimum,
                maximum,
                default,
            )
            globals()[name] = default
    if STREAM_QUALITY_WORST > STREAM_QUALITY_BEST:
        logger.error(
            "[CONFIG] STREAM_QUALITY_WORST (%s) is above STREAM_QUALITY_BEST (%s): the defaults are used",
            STREAM_QUALITY_WORST,
            STREAM_QUALITY_BEST,
        )
        globals()["STREAM_QUALITY_BEST"], globals()["STREAM_QUALITY_WORST"] = 65, 35
    for name, default in _COLOUR_TUNABLES.items():
        if not _is_triple(globals()[name], 0, 255):
            logger.error(
                "[CONFIG] %s=%r must be three numbers 0-255 (R, G, B): the default %r is used",
                name,
                globals()[name],
                default,
            )
            globals()[name] = default
    steps = globals()["GOVERNOR_STEPS"]
    if not (
        isinstance(steps, (tuple, list))
        and all(
            isinstance(step, (tuple, list))
            and len(step) == 2
            and _is_number(step[0])
            and _is_number(step[1])
            and step[1] > 0
            for step in steps
        )
    ):
        logger.error(
            "[CONFIG] GOVERNOR_STEPS=%r must be pairs (effect cost, frames per second above 0): the default is used",
            steps,
        )
        globals()["GOVERNOR_STEPS"] = ((0.75, 10), (0.55, 12))
    else:
        ordered = tuple(sorted((tuple(step) for step in steps), reverse=True))
        if ordered != tuple(tuple(step) for step in steps):
            logger.warning(
                "[CONFIG] GOVERNOR_STEPS was not highest cost first; it is sorted that way"
            )
        globals()["GOVERNOR_STEPS"] = ordered
    if RECEIPT_DITHER not in ("floyd", "none"):
        logger.warning(
            "[CONFIG] RECEIPT_DITHER=%r is not 'floyd' or 'none'; 'floyd' is used",
            RECEIPT_DITHER,
        )


_apply_crash_limits()
_apply_new_limits()
