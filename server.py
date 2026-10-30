import base64
import json
import os
import shutil
from pathlib import Path

from dotenv import load_dotenv
from flask import (
    Flask,
    Response,
    jsonify,
    redirect,
    render_template,
    request,
    send_from_directory,
)

load_dotenv(Path(__file__).parent / ".env", override=False)

os.environ["OMP_NUM_THREADS"] = "1"

import cv2

cv2.setNumThreads(1)

import logging
import math
import queue
import resource
import socket
import subprocess

# import random
import threading
import time

import numpy as np
import qrcode
import requests
import serial
from escpos.printer import File

# numpy as_strided based vectorized melt implementations for testing
from numpy.lib.stride_tricks import as_strided
from PIL import Image, ImageDraw, ImageFont

# import gc
import telemetry

logger = logging.getLogger("glitchbooth.server")

app = Flask(__name__)


# CONFIGURATION & HARDWARE CONSTANTS
# ==================================================

CAPTURE_DIR = "captures"
os.makedirs(CAPTURE_DIR, exist_ok=True)

SAVED_DIR = "saved"
os.makedirs(SAVED_DIR, exist_ok=True)

ARCHIVE_DIR = "captures_archive"
os.makedirs(ARCHIVE_DIR, exist_ok=True)

# Device-only, unprocessed source frame captured alongside a processed capture.
NAKED_DIR = "captures_naked"
os.makedirs(NAKED_DIR, exist_ok=True)

IK_URL_ENDPOINT = os.getenv("IK_URL_ENDPOINT")
IK_PRIVATE_KEY = os.getenv("IK_PRIVATE_KEY")

if not IK_URL_ENDPOINT:
    raise ValueError("CRITICAL: IK_URL_ENDPOINT is not set in the .env file!")

if not IK_PRIVATE_KEY:
    raise ValueError("CRITICAL: IK_PRIVATE_KEY is not set in the .env file!")

IK_AUTH_STRING = base64.b64encode(f"{IK_PRIVATE_KEY}:".encode()).decode()

HTTP_SESSION = requests.Session()

# =========================================================================
# EVENT MODE CONFIGURATION
# =========================================================================
# - NAKED MODE: Set EVENT_SLUG = ""
#   Target: /booth_captures
#   URL:    https://glitchbooth.online/p/{timestamp}
#
# - EVENT MODE: Set EVENT_SLUG = "your-event-slug" (e.g., "synthistan2026")
#   Target: /booth_captures/your-event-slug
#   URL:    https://glitchbooth.online/your-event-slug/p/{timestamp}
#
# CRITICAL CHECKLIST WHEN CHANGING EVENT_SLUG:
# 1. WEBSITE SLUG MATCH: 'EVENT_SLUG' MUST match 'SLUG' in 'event.config.ts' EXACTLY.
# 2. IMAGEKIT FOLDER: Ensure '/booth_captures/<EVENT_SLUG>/' exists on ImageKit.
# 3. THERMAL RECEIPT: Manually update event title, subtitle, and social handles
#    inside 'print_booth_receipt()' below!
# 4. PENDING UPLOADS: Ensure 'pending_uploads.txt' is empty or flushed before
#    switching EVENT_SLUG to prevent previous event captures syncing to the new folder.
#    To quickly flush/empty in bash:
#        > pending_uploads.txt      (or: rm -f pending_uploads.txt)
# =========================================================================

EVENT_SLUG = os.getenv("EVENT_SLUG", "")
IK_BASE_FOLDER = os.getenv("IK_BASE_FOLDER", "booth_captures")
BASE_DOMAIN = os.getenv("BASE_DOMAIN", "https://glitchbooth.online")

# Print contrast variables (1.2 & 11 for bright days, 1.4 & 37 for night)
PRINT_ALPHA = float(os.getenv("PRINT_ALPHA", "1.5"))
PRINT_BETA = int(os.getenv("PRINT_BETA", "11"))

# Receipt QR composite configuration
RECEIPT_QR_SIZE = int(os.getenv("RECEIPT_QR_SIZE", "120"))
RECEIPT_QR_MARGIN = int(os.getenv("RECEIPT_QR_MARGIN", "0"))
RECEIPT_QR_ROTATION = int(
    os.getenv("RECEIPT_QR_ROTATION", "90")
)  # 90 degrees clockwise
RECEIPT_QR_CORNER = os.getenv(
    "RECEIPT_QR_CORNER", "bottom-left"
)  # Options: "bottom-left", "bottom-right", "top-left", "top-right"
# the qr corner options are for vertical placementon the thermal receipt! :)

# On-screen live countdown placement ("bottom-center", "bottom-middle", "top-right", "top-left", "bottom-right", "bottom-left")
_VALID_CORNERS = {
    "bottom-center",
    "bottom-middle",
    "top-right",
    "top-left",
    "bottom-right",
    "bottom-left",
}
_raw_corner = os.getenv("COUNTDOWN_CORNER", "bottom-center").lower().strip()
COUNTDOWN_CORNER = _raw_corner if _raw_corner in _VALID_CORNERS else "bottom-center"
FLASH_DURATION = float(os.getenv("FLASH_DURATION", "0.3"))


def get_ik_target_folder() -> str:
    """
    Computes the ImageKit folder path dynamically at call-time.
    - Naked Mode (""):      '/booth_captures'
    - Event Mode ("slug"):  '/booth_captures/slug'
    """
    slug = (EVENT_SLUG or "").strip().strip("/")
    if slug:
        return f"/{IK_BASE_FOLDER}/{slug}"
    return f"/{IK_BASE_FOLDER}"


def get_landing_url(ts: str) -> str:
    """
    Constructs the landing URL for capture timestamp 'ts'.
    - Naked Mode (""):      'https://glitchbooth.online/p/{ts}'
    - Event Mode ("slug"):  'https://glitchbooth.online/slug/p/{ts}'
    """
    slug = (EVENT_SLUG or "").strip().strip("/")
    domain = BASE_DOMAIN.rstrip("/")
    if slug:
        return f"{domain}/{slug}/p/{ts}"
    return f"{domain}/p/{ts}"


print("==================================================")
print(" GLITCHBOOTH SERVER RUNTIME CONFIGURATION")
print(
    f" [MODE]          : {'EVENT MODE (' + EVENT_SLUG + ')' if EVENT_SLUG else 'NAKED MODE'}"
)
print(f" [IMAGEKIT PATH] : {get_ik_target_folder()}")
print(f" [PRINT CONTRAST]: ALPHA={PRINT_ALPHA}, BETA={PRINT_BETA}")
print(f" [SAMPLE URL]    : {get_landing_url(str(int(time.time() * 1000)))}")
print("==================================================")


SERIAL_PORTS = ["/dev/ttyUSB0", "/dev/ttyACM0"]
CAMERA_STALE_TIMEOUT = 5
CAMERA_REFRESH_INTERVAL = 21600
STREAM_FPS = 15
# Disabling button-knobs combo shutdown feature
# SHUTDOWN_THRESHOLD = 20

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

# PHASE 3 TUNABLES: EFFECT ORDER, KNOB ATTACHMENTS, WINDOWS, COST WEIGHTS, LABELS
# ==============================================================================
# Every effect is a handler. EFFECT_ORDER is the processing order, top to bottom:
# an effect works on the result of the effects above it, so LATER = ON TOP.
# An effect that is not attached to any knob is skipped and costs nothing.
# The CRT scanline overlay always runs last and is not part of this list.
EFFECT_ORDER = [
    "pixellation",
    "posterize",
    "signal_drift",
    "melt",
    "data_mosh",
    "chroma_shift",
    "packet_corruption",
    "ascii_matrix",
]

# Knob (0-5) -> effects. The FIRST effect is its visible primary name; remaining
# effects are secondary and react to the same knob without appearing in the UI.
KNOB_ATTACHMENTS = {
    0: ["ascii_matrix"],
    1: ["signal_drift"],
    2: ["posterize"],
    3: ["pixellation"],
    4: ["melt"],
    5: ["chroma_shift", "packet_corruption"],
}

# Optional range of the knob in which an effect works, as fractions of the knob travel.
# Inside its window the effect sees the full 0-1023 range; outside it is off.
# Example: {"chroma_shift": (0.0, 0.7), "packet_corruption": (0.35, 1.0)}
EFFECT_KNOB_WINDOW = {}

# Cost weights follow effects when they move between knobs.
EFFECT_COST_WEIGHT = {
    "ascii_matrix": 0.10,
    "signal_drift": 0.07,
    "data_mosh": 0.35,
    "pixellation": 0.07,
    "melt": 0.34,
    "chroma_shift": 0.07,
    "packet_corruption": 0.0,
    "posterize": 0.15,
}

EFFECT_SKIP_DURING_WARMUP = ("data_mosh",)

EFFECT_LABELS = {
    "ascii_matrix": "ASCII Matrix",
    "signal_drift": "Signal Drift",
    "data_mosh": "Data Corruption",
    "pixellation": "Pixel Damage",
    "melt": "Melt",
    "chroma_shift": "Chroma Shift",
    "packet_corruption": "Packet Corruption",
    "posterize": "Posterize",
}
KNOB_EMPTY_LABEL = "-"

NAKED_CAPTURE_ENABLED = True
TELEMETRY_ENABLED = (
    True  # Set False to disable all capture logging and telemetry threads
)

# PHASE 4 TUNABLES: STATISTICS
# ==============================================================================
# Every capture is counted by effect NAME (all attached effects, primary and hidden).
# Name under which each effect is counted in stats.json and in the capture ledger.
# Keep these stable: renaming one splits its history. Pixellation keeps its historical key.
EFFECT_STATS_NAME = {
    "ascii_matrix": "ascii_matrix",
    "signal_drift": "signal_drift",
    "data_mosh": "data_mosh",
    "pixellation": "pixel_damage",
    "melt": "melt",
    "chroma_shift": "chroma_shift",
    "packet_corruption": "packet_corruption",
    "posterize": "posterize",
}

# From which strength (0-1023, after the effect's knob window) an effect counts as "used".
# The values are where each effect really starts to act (its own dormancy threshold), so the
# statistics reflect what the visitor could actually see. Set them all to 10 to count like the
# old statistics did (knob at 10 or more). If you change a threshold inside an effect, change it here.
EFFECT_STATS_ACTIVE_FROM = {
    "pixellation": 11,  # acts above 0
    "signal_drift": 11,  # acts above 0
    "melt": 31,  # acts above 0.03
    "data_mosh": 101,  # acts from strength (knob/1023)**2 >= MOSH_DORMANT_BELOW (0.02)
    "chroma_shift": 11,  # acts above 0.05
    "packet_corruption": 11,  # first damage appears above CORRUPTION_SPAWN_ABOVE (0.012)
    "ascii_matrix": 21,  # acts above 0.02
    "posterize": 13,  # acts above the first non-zero point of POSTERIZE_CURVE (knob 12)
}

# PHASE 5 TUNABLES: POSTERIZE (neon tone bands with glitchy outlines)
# ==============================================================================
# Posterize is in the pool but attached to no knob until you add "posterize" to
# KNOB_ATTACHMENTS (for example 3: ["pixellation", "posterize"], or its own knob).
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
POSTERIZE_BANDS_HIGH = 8  # ... and at full strength
POSTERIZE_DETAIL_LOW = (
    0.95  # how much of the original brightness modulates the neon (weak) ...
)
POSTERIZE_DETAIL_HIGH = 0.55  # ... and at full strength (faces stay readable)
POSTERIZE_MIX_START = 0.05  # neon share at the weakest strength ...
POSTERIZE_MIX_FULL_AT = 0.95  # ... reaching 1.0 at this strength (0-1)
POSTERIZE_OUTLINE_2PX_FROM = 0.70  # strength from which outlines are 2 px thick
POSTERIZE_OUTLINE_3PX_FROM = 0.90  # strength from which outlines are 3 px thick
POSTERIZE_DROPOUT_LOW = 0.05  # share of the outline that is missing (weak) ...
POSTERIZE_DROPOUT_HIGH = 0.05  # ... and at full strength
POSTERIZE_JITTER_MAX_PX = 2  # outline rows shift sideways by up to 1 .. this many px
POSTERIZE_FLICKER_PROB = 0.20  # share of outline sections that flash white-hot
# Outline colour: "glow" = the brightest neon next to the border (often near-white), "white", "black" (ink)
POSTERIZE_OUTLINE_STYLE = "glow"
POSTERIZE_SECTION_PX = (
    4  # size of the dropout / flicker sections (must divide 640 and 480)
)
POSTERIZE_FRAGMENT_FROM = 0.85  # strength from which fine detail starts to break apart
POSTERIZE_FRAGMENT_AMOUNT = 7  # grey levels of break-up noise at full strength
POSTERIZE_ROTATE_SECONDS = (
    2  # 0 = fixed colours; N = the colour assignment shifts every N seconds
)
POSTERIZE_RNG_SEED = (
    None  # None = fresh entropy each start; tests reseed via seed_private_rngs()
)

# PHASE 2 TUNABLES: DATA MOSH, PACKET CORRUPTION
# ==============================================================================

# --- Data Mosh -------------------------------------------------------------------
MOSH_DORMANT_BELOW = (
    0.02  # strength = (knob/1023)**2 below this cancels everything at once
)
MOSH_THRESH_BASE = 17  # motion threshold = BASE + (1 - strength) * SPAN   (18 .. 58)
MOSH_THRESH_SPAN = 53
MOSH_HOLD_MIN_FRAMES = 17  # hold time rises from MIN to MAX with strength (1-111)
MOSH_HOLD_MAX_FRAMES = 49
# Per-frame drag distance in any of four directions: left, right, up, or down.
MOSH_VECTOR_PX = 9  # pixels per frame (1-11)
MOSH_DRAG_NEW_WEIGHT = 222 / 256  # share of the dragged picture per frame
MOSH_EDGE_ERODE_MAX_PX = 0  # random edge erosion 0..N px on held cells (as before)
MOSH_RELEASE_FRAMES = 11  # edges dissolve over the last N frames of a hold
MOSH_RELEASE_ERODE_PX = 1  # extra erosion per release frame
# Colour-plane bleed inside held cells: "shape_live_colour_held", "shape_held_colour_live", "off"
MOSH_BLEED_MODE = "shape_live_colour_held"
MOSH_BLEED_MAX = 0.23  # bleed mix = MAX * strength (0-1)

MOSH_RNG_SEED = (
    None  # None = fresh entropy each start; tests reseed via seed_private_rngs()
)
MOSH_CELL_H = 16
MOSH_CELL_W = 12

# These above are connected to and kept in check by _validate_mosh_settings() below the line...

# --- Corruption -------------------------------------------------------------------
CORRUPTION_CELL_H = 12
CORRUPTION_CELL_W = 16
CORRUPTION_GRID_H = FRAME_H // CORRUPTION_CELL_H
CORRUPTION_GRID_W = FRAME_W // CORRUPTION_CELL_W
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

STATIC_BLOCK_BUFFER = np.empty(
    (CORRUPTION_CELL_H, CORRUPTION_CELL_W, 3), dtype=np.uint8, order="C"
)

GLOBAL_NOISE_BUFFER = np.empty(
    (CORRUPTION_CELL_H, CORRUPTION_CELL_W, 3), dtype=np.uint8, order="C"
)

GLITCH_BOX_MIN = 8  # [320x240: 8 ] [480x360: 12] [640x480: 16]
GLITCH_BOX_SCALE = 48  # [320x240: 24] [480x360: 36] [640x480: 48]
GLITCH_STRIP_MIN = 4  # [320x240: 4 ] [480x360: 6 ] [640x480: 8 ]
GLITCH_STRIP_SCALE = 24  # [320x240: 12] [480x360: 18] [640x480: 24]

# --- Signal Drift -------------------------------------------------------------------
DRIFT_MAX_AMOUNT = 60  # [320x240: 30] [480x360: 45] [640x480: 60]

# --- Pixellation -------------------------------------------------------------------
COMPRESSION_TILE_BASE = 4  # [320x240: 6 ] [480x360: 9 ] [640x480: 12]
COMPRESSION_TILE_SCALE = 9  # [320x240: 3 ] [480x360: 4 ] [640x480: 6 ]

# --- Chroma Shift -------------------------------------------------------------------
ANA_SHIFT_MAX = 48  # [320x240: 18] [480x360: 27] [640x480: 36]
ANA_GHOST_OFFSET = 40  # [320x240: 20] [480x360: 30] [640x480: 40]
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
MELT_NEON_START_PCT = 0.85  # Where neon begins: 0.0 = top of drip, 0.5 = halfway down
MELT_NEON_RAMP_SPAN = (
    0.10  # Length of the transition ramp to full neon (Start + Span should equal 1.0)
)

# NEON_PALETTES defined in native OpenCV BGR format: [Blue, Green, Red]
# these palettes might be shared later with future POSTERIZE effect addition
NEON_PALETTES = np.array(
    [
        [0, 255, 255],  # Neon Yellow      (B: 0,   G: 255, R: 255)
        [255, 0, 255],  # Hot Magenta      (B: 255, G: 0,   R: 255)
        [255, 255, 0],  # Electric Cyan    (B: 255, G: 255, R: 0)
        [0, 255, 0],  # Acid Green       (B: 0,   G: 255, R: 0)
        [255, 0, 128],  # Deep Violet      (B: 255, G: 0,   R: 128)
        [0, 165, 255],  # Tangerine        (B: 0,   G: 165, R: 255)
        [128, 0, 255],  # Neon Rose        (B: 128, G: 0,   R: 255)
        [0, 255, 127],  # Chartreuse       (B: 0,   G: 255, R: 127)
    ],
    dtype=np.uint8,
    order="C",
)
NEON_PALETTES_FLOAT = np.ascontiguousarray(NEON_PALETTES.astype(np.float32))


CRT_SCANLINE_PERIOD = 8  # [320x240: 4 ] [480x360: 6 ] [640x480: 8 ]
CRT_SCANLINE_THICKNESS = 4  # [320x240: 2 ] [480x360: 3 ] [640x480: 4 ]
CRT_APERTURE_PERIOD = 6  # [320x240: 3 ] [480x360: 4 ] [640x480: 6 ]

PREVIEW_DOWNSCALE_WIDTH = (
    480  # [320x240: 240] [480x360: 320] [640x480: 480 or keep 240]
)

LATEST_CAMERA_FRAME = np.zeros((FRAME_H, FRAME_W, 3), dtype=np.uint8, order="C")
MUTABLE_PROCESSING_BUFFER = np.empty((FRAME_H, FRAME_W, 3), dtype=np.uint8, order="C")
UI_DISPLAY_BUFFER = np.empty((FRAME_H, FRAME_W, 3), dtype=np.uint8, order="C")
NAKED_FRAME_BUFFER = np.empty((FRAME_H, FRAME_W, 3), dtype=np.uint8, order="C")


SHARED_JPEG_STREAM_BUFFER = memoryview(b"")
SHARED_JPEG_ARRAY = None

FROZEN_DISPLAY_BUFFER = np.empty((FRAME_H, FRAME_W, 3), dtype=np.uint8, order="C")
FROZEN_DISPLAY_BUFFER.fill(0)

FROZEN_PRINT_BUFFER = np.empty((FRAME_W, FRAME_H), dtype=np.uint8, order="C")
FROZEN_PRINT_BUFFER.fill(255)

STATIC_NOISE_BUFFER = np.empty((FRAME_H, FRAME_W, 3), dtype=np.uint8, order="C")

# Shared luminance buffer: utilized sequentially during Melt pass and post-capture receipt contrast prep
STATIC_MELT_GRAY_BUFFER = np.empty((FRAME_H, FRAME_W), dtype=np.uint8, order="C")

# --- PHASE 1: MELT PERSISTENT GRAVITATIONAL DEFORMATION BUFFERS ---

STATIC_MELT_SNAPSHOT = np.empty((FRAME_H, FRAME_W, 3), dtype=np.uint8, order="C")

assert STATIC_MELT_SNAPSHOT.flags["C_CONTIGUOUS"], (
    "CRITICAL: STATIC_MELT_SNAPSHOT layout fault!"
)

# Used with the earlier versions of numpy based melt, vectorized melt. Not used in the current version.
"""
# NOTE: Ensure MELT_DRIP_WIDTH is defined before these lines!
MELT_DRIP_WORK_BUFFER = np.zeros((FRAME_H, MELT_DRIP_WIDTH, 3), dtype=np.float32)

assert MELT_DRIP_WORK_BUFFER.flags["C_CONTIGUOUS"], (
    "CRITICAL: MELT_DRIP_WORK_BUFFER layout fault!"
)
"""

COMPRESSION_STAGE_BUFFER = np.empty((FRAME_H, FRAME_W, 3), dtype=np.uint8, order="C")

STATIC_ROW_BUFFER = np.empty((FRAME_W, 3), dtype=np.uint8, order="C")
CHANNEL_SHIFT_BUFFER = np.empty((FRAME_H, FRAME_W), dtype=np.uint8, order="C")
SLICE_SHIFT_BUFFER = np.empty((FRAME_H, FRAME_W, 3), dtype=np.uint8, order="C")
GHOST_FRAME_BUFFER = np.empty((FRAME_H, FRAME_W, 3), dtype=np.uint8, order="C")
GHOST_GREEN_BUFFER = np.empty((FRAME_H, FRAME_W), dtype=np.uint8, order="C")

STATIC_V_BUFFER = None

CRT_MASK = np.empty((FRAME_H, FRAME_W, 3), dtype=np.uint8, order="C")


def initialize_crt_mask():
    global CRT_MASK
    print(
        f"[SYSTEM SETUP] Materializing resolution-agnostic CRT mask ({FRAME_W}x{FRAME_H})..."
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
    print("[SYSTEM SETUP] CRT Mask locked into contiguous C-memory.")


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

# ASCII ENGINE PRE-ALLOCATED STORAGE (PHASE 1 MVP)
# =========================================================================
ASCII_GRID_W = 80
ASCII_GRID_H = 60

CELL_W = FRAME_W // ASCII_GRID_W
CELL_H = FRAME_H // ASCII_GRID_H

ASCII_LOWRES_GRAY = np.zeros((ASCII_GRID_H, ASCII_GRID_W), dtype=np.uint8, order="C")
ASCII_UPRES_GRAY = np.zeros((FRAME_H, FRAME_W), dtype=np.uint8, order="C")

ASCII_BLOCK_BUFFER = np.zeros(
    (ASCII_GRID_H, ASCII_GRID_W, CELL_H, CELL_W), dtype=np.uint8, order="C"
)

ASCII_TILES = np.zeros((16, CELL_H, CELL_W), dtype=np.uint8, order="C")

ASCII_COLOR_BUFFER = np.zeros((FRAME_H, FRAME_W, 3), dtype=np.uint8, order="C")
ASCII_GREEN_BUFFER = np.zeros((FRAME_H, FRAME_W, 3), dtype=np.uint8, order="C")
ASCII_NOISE_LOW = np.zeros((ASCII_GRID_H, ASCII_GRID_W), dtype=np.uint8, order="C")
ASCII_NOISE_UP = np.zeros((FRAME_H, FRAME_W), dtype=np.uint8, order="C")

clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))

SPARK_COUNTER = 0
SPARK_INTERVAL = 14  # Will dynamically re-roll between 7 and 17


# ATOMIC REFERENCE GLOBALS & PIPELINE LOCKS
# ==================================================

GLOBAL_KNOBS = (0, 0, 0, 0, 0, 0)
GLOBAL_BUTTON = 1

# UNIFIED TRANSACTING SESSION STATE MATRIX
# ==============================================================================
ACTIVE_SESSION = {
    "mode": "live",
    "session_id": None,
    "upload_complete": False,
    "upload_failed": False,
    "composite_ready": False,
    "print_dispatched": False,
    "countdown_start_time": 0.0,
    "countdown_remaining": 3,
    "knobs": [0, 0, 0, 0, 0, 0],
}

mode_lock = threading.Lock()
frame_lock = threading.Lock()
UI_FRAME_SEQ = (
    0  # NEW: bumped (under frame_lock) each time UI_DISPLAY_BUFFER is fully rewritten
)
cam_raw_lock = threading.Lock()

printer_hardware_lock = threading.Lock()
cache_file_lock = threading.Lock()

SHUTDOWN_EVENT = threading.Event()
CRITICAL_WORKERS = []

# 1. Initialize telemetry and inject the global event object
if TELEMETRY_ENABLED:
    telemetry.init_telemetry(SHUTDOWN_EVENT)

STREAM_SEMAPHORE = threading.Semaphore(4)
ACTIVE_STREAM_VIEWERS = 0

cam_thread_alive = True
capture_lock_until = 0

last_good_camera = time.time()
camera_started_at = time.time()
# Disabling button-knobs combo shutdown feature
# shutdown_hold_start = None
last_frame_time = time.time()
camera_fail_count = 0

upload_status_msg = "IDLE"
SYSTEM_TELEMETRY = {"ip": "0.0.0.0", "cpu_temp": "N/A", "memory_rss_mb": -1}
last_button_state = 1

FRAME_ID = 0
STREAM_FRAME_ID = -1

upload_queue = queue.Queue(maxsize=32)


# ASCII TILES
# ==================================================


def init_ascii_tiles():
    global ASCII_TILES

    char_ramp = [
        ".",
        ",",
        ":",
        ";",
        "i",
        "o",
        "*",
        "?",
        "%",
        "0",
        "$",
        "&",
        "@",
        "▒",
        "▓",
        "█",
    ]

    pi_font_paths = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationMono-Bold.ttf",
        "/usr/share/fonts/truetype/freefont/FreeMonoBold.ttf",
        "Courier New Bold",  # Windows/Mac fallback
    ]

    font = None
    target_font_size = max(CELL_W, CELL_H) + 2

    for path in pi_font_paths:
        try:
            font = ImageFont.truetype(path, target_font_size)
            print("ASCII font loaded.")
            break
        except OSError:
            continue

    if font is None:
        print(
            "[WARNING] Monospace TTF not found. Using system default. Text may appear small."
        )
        font = ImageFont.load_default()

    for i, char in enumerate(char_ramp):
        img = Image.new("L", (CELL_W, CELL_H), color=0)
        draw = ImageDraw.Draw(img)

        left, top, right, bottom = draw.textbbox((0, 0), char, font=font)
        w, h = right - left, bottom - top

        draw.text(((CELL_W - w) // 2, (CELL_H - h) // 2 - 1), char, font=font, fill=255)

        ASCII_TILES[i] = np.array(img, dtype=np.uint8)


init_ascii_tiles()


# COHERENT CONTROL SNAPSHOT DATA MODEL
# ==================================================


class ControlPacket:
    """Immutable data record representing a single, frozen control plane epoch."""

    def __init__(self, frozen_knobs, frozen_button, frozen_mode):
        self.knobs = frozen_knobs
        self.button = frozen_button
        self.mode = frozen_mode


# SUBSYSTEM DRIVERS
# ==================================================


def get_actual_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        IP = s.getsockname()[0]
    except Exception:
        IP = "127.0.0.1"
    finally:
        s.close()
    return IP


def get_cpu_temp():
    try:
        res = subprocess.check_output(["vcgencmd", "measure_temp"]).decode("utf-8")
        return res.replace("temp=", "").strip()
    except Exception:
        return "N/A"


def get_memory_usage():
    try:
        return round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0, 2)
    except Exception:
        return -1


def telemetry_worker():
    global SYSTEM_TELEMETRY
    print("[INIT] Telemetry Worker: Active and bound.")

    while not SHUTDOWN_EVENT.is_set():
        try:
            SYSTEM_TELEMETRY["ip"] = get_actual_ip()
            SYSTEM_TELEMETRY["cpu_temp"] = get_cpu_temp()
            SYSTEM_TELEMETRY["memory_rss_mb"] = get_memory_usage()
        except Exception as e:
            print(f"[TELEMETRY ERROR] Subprocess acquisition fault: {e}")

        if SHUTDOWN_EVENT.wait(timeout=2.0):
            break
    print("[SHUTDOWN] Telemetry worker exited cleanly.")


def open_camera():
    for index in [0, 1, 2]:
        cap = cv2.VideoCapture(index, cv2.CAP_V4L2)
        if cap.isOpened():
            ret, frame = cap.read()
            if ret and frame is not None:
                cap.set(cv2.CAP_PROP_FRAME_WIDTH, FRAME_W)
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, FRAME_H)
                cap.set(cv2.CAP_PROP_FPS, 10)
                cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                # --- TRACK HARDWARE NEGOTIATION ---
                actual_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
                actual_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
                print(
                    f"SUCCESS: V4L2 pipeline pinned to index {index} | "
                    f"Requested {FRAME_W}x{FRAME_H}, Negotiated {actual_w}x{actual_h}"
                )
                return cap
            else:
                cap.release()
    print("CRITICAL V4L2 ERROR: Physical acquisition failed.")
    return None


def camera_io_worker():
    global last_good_camera, camera_started_at, cam_thread_alive
    global LATEST_CAMERA_FRAME
    cap = open_camera()
    consecutive_failures = 0

    while cam_thread_alive and not SHUTDOWN_EVENT.is_set():
        try:
            if cap is None or not cap.isOpened():
                time.sleep(2)
                cap = open_camera()
                continue
            ret, frame = cap.read()
            global last_frame_time, camera_fail_count
            if ret and frame is not None:
                consecutive_failures = 0
                camera_fail_count = 0
                last_frame_time = time.time()
                with cam_raw_lock:
                    if frame.shape == LATEST_CAMERA_FRAME.shape:
                        LATEST_CAMERA_FRAME[:] = frame
                    else:
                        print(
                            f"[CAMERA WARNING] Resolution mismatch! Expected {LATEST_CAMERA_FRAME.shape}, got {frame.shape}. Resizing fallback applied."
                        )
                        cv2.resize(frame, (FRAME_W, FRAME_H), dst=LATEST_CAMERA_FRAME)
                last_good_camera = time.time()
            else:
                consecutive_failures += 1
                camera_fail_count += 1
                print(f"CAMERA FAIL at {time.time()}")
                time.sleep(0.01)
            now = time.time()
            if (
                (consecutive_failures > 15)
                or (now - last_good_camera > CAMERA_STALE_TIMEOUT)
                or (now - camera_started_at > CAMERA_REFRESH_INTERVAL)
            ):
                print("RESETTING CAMERA SUB-SOCKET ENGINE DUE TO STALL...")
                if cap is not None:
                    try:
                        cap.release()
                        del cap
                    except:
                        pass
                time.sleep(0.3)
                cap = open_camera()
                consecutive_failures = 0
                last_good_camera = time.time()
                camera_started_at = time.time()
        except Exception as fatal_e:
            print(f"CAMERA IO WORKER FATAL EXCEPTION: {fatal_e}")
            time.sleep(1)


# NOT IN USE CURRENTLY BUT NOT DELETED YET. MAY BE USED FOR FUTURE HARDWARE INTEGRATION
def open_serial():
    while True:
        for port in SERIAL_PORTS:
            try:
                ser = serial.Serial(port, 9600, timeout=0.05)
                logger.info("[HARDWARE LINK] Connected to serial port: %s", port)
                return ser
            except (OSError, serial.SerialException) as exc:
                logger.debug(
                    "[HARDWARE LINK] Serial port unavailable: %s: %s",
                    port,
                    exc,
                )
                continue

        time.sleep(2)


def update_knobs(ser):
    global GLOBAL_KNOBS, GLOBAL_BUTTON
    try:
        if ser.in_waiting > 0:
            line = ser.readline().decode("utf-8", errors="ignore").strip()
            if not line:
                return
            vals = line.split(",")
            if len(vals) != 7:
                logger.debug("[SERIAL] Ignoring malformed packet: %r", line)
                return
            try:
                parsed = [int(v) for v in vals]
            except ValueError:
                logger.debug("[SERIAL] Ignoring non-integer packet: %r", line)
                return
            GLOBAL_KNOBS = tuple(parsed[:6])
            GLOBAL_BUTTON = parsed[6]
    except Exception as e:
        print(f"SERIAL NODE PARSE WARNING: {e}")
        logger.debug("[SERIAL] Error reading from serial: %s", e)
        raise e


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

        # Start of change for vertical drag
        """
        # scratch (each used only inside one step)
        self.sh_right, self.sh_left, self.blend = z3(), z3(), z3()
        self.comp, self.pos3, self.neg3 = z3(), z3(), z3()
        self.m_keep, self.m_act, self.m_neg = z1(), z1(), z1()
        self.m_inset, self.m_tmp, self.m_fin = z1(), z1(), z1()
        """

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
        # End of change for vertical drag

        self.y_live, self.y_hold, self.y_pos, self.y_neg = z1(), z1(), z1(), z1()
        # distance of every pixel from its cell border (for edge erosion)
        yy, xx = np.mgrid[0:H, 0:W]
        iy, ix = yy % MOSH_CELL_H, xx % MOSH_CELL_W
        self.edge_dist = np.minimum(
            np.minimum(iy, MOSH_CELL_H - 1 - iy), np.minimum(ix, MOSH_CELL_W - 1 - ix)
        ).astype(np.uint8)
        # per-cell state

        # Start of change for vertical drag
        """
        self.timer = np.zeros((gh, gw), dtype=np.int16)
        self.vx = np.zeros((gh, gw), dtype=np.int8)
        """
        self.timer = np.zeros((gh, gw), dtype=np.int16)

        # Per-cell direction, meaningful only while timer > 0:
        # 0 = left, 1 = right, 2 = up, 3 = down.
        self.direction = np.zeros((gh, gw), dtype=np.uint8)
        # End of change for vertical drag

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

        # Start of change for vertical drag
        """
        self.vx[:] = 0
        """
        self.direction[:] = 0
        # End of change for vertical drag

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

        # Start of change for vertical drag
        """
        gh, gw, W = MOSH_GRID_H, MOSH_GRID_W, FRAME_W
        """
        gh, gw, H, W = MOSH_GRID_H, MOSH_GRID_W, FRAME_H, FRAME_W
        # End of change for vertical drag

        vec = MOSH_VECTOR_PX

        if motion_samples is None:
            motion_samples = frame[
                _MOSH_SAMPLE_Y0::MOSH_CELL_H, _MOSH_SAMPLE_X0::MOSH_CELL_W
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
        """
        if new_cells.any():
            count = int(new_cells.sum())
            self.vx[new_cells] = np.where(self.rng.random(count) > 0.5, vec, -vec)
        self.timer[motion] = hold_frames
        """

        # Start of change for vertical drag
        """
        if new_cells.any():
            count = int(new_cells.sum())
            self.vx[new_cells] = np.where(self.rng.random(count) > 0.5, vec, -vec)
            self.timer[new_cells] = (
                hold_frames  # Only new cells get the timer; active cells count down freely
            )
        """
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
        # End of change for vertical drag

        active = self.timer > 0
        band = None

        # ---- 3. drag, erode, composite - only inside the band of rows with held cells ----
        if active.any():
            np.copyto(self.cur, frame)  # this stage's INPUT (frame is modified below)
            rows = np.flatnonzero(active.any(axis=1))
            r0, r1 = int(rows[0]), int(rows[-1])
            band = slice(r0 * MOSH_CELL_H, (r1 + 1) * MOSH_CELL_H)

            # Start of change for vertical drag
            """
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
            """
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
            # End of change for vertical drag

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
        # Start of change for vertical drag
        """
        self.timer[self.timer > 0] -= 1
        self.vx[self.timer == 0] = 0
        """
        self.timer[self.timer > 0] -= 1
        self.direction[self.timer == 0] = 0
        # End of change for vertical drag

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


_MOSH = DataMoshEngine(MOSH_RNG_SEED)


def data_mosh_reset():
    """Cancel every hold and forget the remembered picture."""
    _MOSH.reset()


def data_mosh_take_samples(frame):
    """Snapshot of the per-cell motion sample points (for MOSH_STAGE_POSITION C)."""
    return frame[_MOSH_SAMPLE_Y0::MOSH_CELL_H, _MOSH_SAMPLE_X0::MOSH_CELL_W][
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


# =========================================================================
# PRIVATE RANDOM GENERATORS AND WIRING CHECK
# =========================================================================


def seed_private_rngs(seed):
    """Reseed every effect that owns a private random generator (tests / A-B harness)."""
    _MOSH.reseed(seed)
    _POSTER.reseed(seed)


def _validate_mosh_settings():
    """Check Data Mosh tunables without changing the owner's tuned safe limits."""
    global MOSH_BLEED_MODE
    global MOSH_HOLD_MIN_FRAMES, MOSH_HOLD_MAX_FRAMES, MOSH_VECTOR_PX
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
    if not (1 <= MOSH_VECTOR_PX <= 11):
        notes.append(f"MOSH_VECTOR_PX={MOSH_VECTOR_PX} outside 1-11, using 7")
        MOSH_VECTOR_PX = 7
    if not (1 <= MOSH_HOLD_MIN_FRAMES <= MOSH_HOLD_MAX_FRAMES <= 111):
        notes.append(
            "MOSH_HOLD_*_FRAMES must satisfy 1 <= min <= max <= 111, using 6, 33"
        )
        MOSH_HOLD_MIN_FRAMES, MOSH_HOLD_MAX_FRAMES = 6, 33

    for msg in notes:
        print(f"[MOSH SETTINGS] {msg}")
    return notes


_validate_mosh_settings()


# =========================================================================
# ISOLATED GRAVITATIONAL MELT ENGINE (STAGE 6) -- VECTORIZED
# =========================================================================
# Drop-in replacement for _collect_melt_drips / _render_melt_drips in server.py.
# apply_melt_pass() stays exactly as it is.
#
# Needs this extra import at the top of server.py:
#     from numpy.lib.stride_tricks import as_strided
#
# Why: the old renderer made ~30 tiny NumPy calls per drip (~3,000 per frame
# at the 100-drip cap). Small NumPy calls hold the GIL, so the pygame thread
# had to wait for it. This version makes a few dozen large NumPy calls per
# frame; those release the GIL while they run.
#
# Visual behaviour is kept: same seed selection, same hashing, same gravity
# curve, same bilinear sampling, same neon ramp, same (reach, x0) draw order
# so overlapping drips composite the same way.


def _collect_melt_drips(
    melt_raw: float, current_time: float, h: int = FRAME_H, w: int = FRAME_W
):
    """Seed discovery without a Python loop over columns.

    Returns a float64 array of shape (n, 7):
        [reach, x0, start_y, L, s, p1, p2]
    or None when there is nothing to draw.
    """
    max_melt_length = int(20 + melt_raw * (h * 0.85))
    threshold = max(75, int(255 * (1.0 - melt_raw * 0.65)))
    stride = (
        MELT_STRIDE_LOW
        if melt_raw < 0.35
        else (MELT_STRIDE_MID if melt_raw < 0.7 else MELT_STRIDE_HIGH)
    )
    seed_step = max(2, int((1.1 - melt_raw) * 8))
    max_seeds_per_column = 3 if melt_raw < 0.65 else 6
    time_tick = int(current_time * 4) & 0xFF

    # One threshold over every sampled column at once.
    mask = STATIC_MELT_GRAY_BUFFER[:, 1 : w - 2 : stride] > threshold  # (h, ncols)
    col_idx, ys = np.nonzero(mask.T)  # ordered by column, then by y
    if ys.size == 0:
        return None

    # Position of each hit inside its own column -> every seed_step-th hit,
    # at most max_seeds_per_column per column, nothing in the bottom 4 rows.
    counts = np.bincount(col_idx, minlength=mask.shape[1])
    first = np.cumsum(counts) - counts
    rank = np.arange(ys.size) - first[col_idx]
    keep = (
        (rank % seed_step == 0)
        & (rank // seed_step < max_seeds_per_column)
        & (ys < h - 4)
    )
    col_idx, ys, rank = col_idx[keep], ys[keep], rank[keep]
    if ys.size == 0:
        return None
    seed_rank = rank // seed_step
    xs = 1 + col_idx * stride

    # Same ordering as before: all rank-0 seeds (by column), then rank-1, ...
    order = np.lexsort((xs, seed_rank))
    xs, ys, seed_rank = xs[order], ys[order], seed_rank[order]
    bounds = np.searchsorted(seed_rank, np.arange(max_seeds_per_column + 1))

    # Apply the drip cap rank by rank, thinning evenly when a rank overflows.
    picked = []
    total = 0
    for r in range(max_seeds_per_column):
        remaining = MAX_MELT_DRIPS - total
        if remaining <= 0:
            break
        idx = np.arange(bounds[r], bounds[r + 1])
        if idx.size > remaining:
            step = idx.size / float(remaining)
            idx = idx[(np.arange(remaining) * step).astype(np.int64)]
        if idx.size:
            picked.append(idx)
            total += idx.size
    if not picked:
        return None
    sel = np.concatenate(picked)

    # Per-drip pseudo-random parameters (same integer hashes as before).
    x = xs[sel].astype(np.int64)
    y = ys[sel].astype(np.int64)
    h1 = (x * 73856093) ^ (y * 19349663) ^ (time_tick * 83492791)
    h2 = (x * 2654435761) ^ (y * 2246822519) ^ (time_tick * 3266489917)
    rand_len_mult = (h1 % 1000) / 1000.0
    rand_stretch_mult = ((h1 >> 10) % 1000) / 1000.0
    n_pal = len(NEON_PALETTES)
    p1 = (h2 >> 4) % n_pal
    p2 = (p1 + 1 + ((h2 >> 12) % (n_pal - 1))) % n_pal
    base_len = int(max_melt_length * 0.45)
    rand_len = (max_melt_length * 0.55 * rand_len_mult).astype(np.int64)
    L = base_len + rand_len
    s = 1.0 + rand_stretch_mult * 2.0
    reach = y + L

    live = L > 0
    out = np.column_stack((reach, x, y, L, s, p1, p2)).astype(np.float64)
    return out[live] if not live.all() else out


def _melt_window_view(buf, width: int, writeable: bool):
    """View of a C-contiguous (H, W, 3) uint8 frame where view[y, x] is the
    3*width bytes of `width` consecutive pixels starting at column x.
    Lets one fancy-index gather/scatter move a whole drip row (8 px)."""
    h, w = buf.shape[:2]
    return as_strided(
        buf,
        shape=(h, w - width + 1, 3 * width),
        strides=(buf.strides[0], 3, 1),
        writeable=writeable,
    )


def _render_melt_drips(drips, h: int = FRAME_H, w: int = FRAME_W):
    if drips is None or len(drips) == 0:
        return

    # Draw order: ascending (reach, x0). Stable, so ties keep discovery order.
    drips = drips[np.lexsort((drips[:, 1], drips[:, 0]))]

    left = MELT_DRIP_WIDTH // 2
    right = MELT_DRIP_WIDTH - left

    x0 = drips[:, 1].astype(np.int32)
    y0 = drips[:, 2].astype(np.int32)
    L = drips[:, 3].astype(np.int32)
    s = drips[:, 4].astype(np.float32)
    p1 = drips[:, 5].astype(np.int32)
    p2 = drips[:, 6].astype(np.int32)

    nr = np.minimum(y0 + L, h) - y0  # rows each drip covers
    ok = (x0 - left >= 0) & (x0 + right <= w) & (L > 0) & (y0 < h - 1) & (nr > 0)
    if not ok.any():
        return
    if not ok.all():
        x0, y0, L, s, p1, p2, nr = (a[ok] for a in (x0, y0, L, s, p1, p2, nr))

    n = x0.size
    total = int(nr.sum())

    # ---- geometry: one flat row list covering every drip, in draw order ----
    starts = np.cumsum(nr) - nr
    did = np.repeat(np.arange(n, dtype=np.int32), nr)  # drip id per row
    y_rel_i = np.arange(total, dtype=np.int32) - starts[did]  # row within drip
    y_rel = y_rel_i.astype(np.float32)

    norm = np.divide(y_rel, L.astype(np.float32)[did])
    np.clip(norm, 0.0, 1.0, out=norm)

    denom = np.sqrt(norm)
    denom *= s[did]
    denom += 1.0
    np.maximum(denom, 1.0, out=denom)

    y_float = np.divide(y_rel, denom)
    y_float += y0.astype(np.float32)[did]
    np.clip(y_float, 0.0, float(h - 1), out=y_float)

    y_fl = np.floor(y_float)
    w_ceil = y_float - y_fl
    y_floor = y_fl.astype(np.int32)
    y_ceil = np.minimum(y_floor + 1, h - 1)
    y_dest = y0[did] + y_rel_i
    x_start = (x0 - left)[did]

    # ---- per-row blend weights --------------------------------------------
    # out = (1-nw)*((1-wc)*floor_px + wc*ceil_px) + nw*neon
    #     =  a*floor_px + b*ceil_px + nw*neon
    nw = np.subtract(norm, np.float32(MELT_NEON_START_PCT))
    nw /= np.float32(MELT_NEON_RAMP_SPAN)
    np.clip(nw, 0.0, 1.0, out=nw)

    one_minus_nw = 1.0 - nw
    wt_floor = one_minus_nw * (1.0 - w_ceil)
    wt_ceil = one_minus_nw * w_ceil

    neon_color = 0.5 * NEON_PALETTES_FLOAT[p1] + 0.5 * NEON_PALETTES_FLOAT[p2]  # (n,3)
    neon_term = nw[:, None] * neon_color[did]  # (total, 3)

    # ---- pixels: gather, blend, scatter, in cache-sized chunks ------------
    src_win = _melt_window_view(STATIC_MELT_SNAPSHOT, MELT_DRIP_WIDTH, False)
    dst_win = _melt_window_view(MUTABLE_PROCESSING_BUFFER, MELT_DRIP_WIDTH, True)

    for i in range(0, total, MELT_CHUNK_ROWS):
        j = min(total, i + MELT_CHUNK_ROWS)
        m = j - i
        xs_c = x_start[i:j]

        acc = src_win[y_floor[i:j], xs_c].astype(np.float32)  # (m, 24)
        acc *= wt_floor[i:j, None]
        tmp = src_win[y_ceil[i:j], xs_c].astype(np.float32)
        tmp *= wt_ceil[i:j, None]
        acc += tmp

        acc3 = acc.reshape(m, MELT_DRIP_WIDTH, 3)
        acc3 += neon_term[i:j, None, :]

        np.rint(acc, out=acc)
        np.clip(acc, 0.0, 255.0, out=acc)

        # Rows are written in draw order, so later drips overwrite earlier
        # ones exactly as the per-drip loop did.
        dst_win[y_dest[i:j], xs_c] = acc.astype(np.uint8)


def apply_melt_pass(melt_raw: float, current_time: float):
    """Stage 6: Gravitational Melt Deformation Public Entry Point."""
    if melt_raw <= 0.03:
        return
    STATIC_MELT_SNAPSHOT[:] = MUTABLE_PROCESSING_BUFFER[:]
    cv2.cvtColor(STATIC_MELT_SNAPSHOT, cv2.COLOR_BGR2GRAY, dst=STATIC_MELT_GRAY_BUFFER)
    drips = _collect_melt_drips(melt_raw, current_time)
    # NOTE: drips is now a NumPy array (or None), so no `if drips:` truth test.
    if drips is not None:
        _render_melt_drips(drips)


# RADICAL COMPUTATION UNROLLED ENGINE
# ==================================================


def n(val):
    return val / 1023.0


# =========================================================================
# PHASE 3: EFFECT HANDLERS, ATTACHMENTS, ORDER AND PER-EFFECT KNOB WINDOWS
# =========================================================================
def apply_pixellation(raw_knob, current_time):
    comp = n(raw_knob)
    if comp > 0:
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


def apply_chroma_shift(raw_knob, current_time):
    ana_val = n(raw_knob)
    if ana_val <= 0.05:
        return
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
            0.80,
            GHOST_FRAME_BUFFER,
            0.20,
            0,
            dst=MUTABLE_PROCESSING_BUFFER,
        )
    if int(current_time * 15) % 3 == 0:
        # Gives a smoother curve where low values yield slices much sooner
        num_slices = int((ana_val**0.5) * 4)

        for _ in range(int(num_slices)):
            y, height, shift = (
                np.random.randint(0, h - SLICE_MAX_H),
                np.random.randint(SLICE_MIN_H, SLICE_MAX_H),
                np.random.randint(-SLICE_MAX_SHIFT, SLICE_MAX_SHIFT),
            )
            if shift:
                view = MUTABLE_PROCESSING_BUFFER[y : y + height]
                s = abs(shift) % w
                if s:
                    scratch = SLICE_SHIFT_BUFFER[:height, :w]
                    if shift > 0:
                        scratch[:, :s], scratch[:, s:] = view[:, -s:], view[:, :-s]
                    else:
                        scratch[:, :-s], scratch[:, -s:] = view[:, s:], view[:, :s]
                    view[:] = scratch


def apply_signal_drift(raw_knob, current_time):
    global STATIC_V_BUFFER
    drift = n(raw_knob)
    if drift <= 0:
        return
    h, w = MUTABLE_PROCESSING_BUFFER.shape[:2]
    if STATIC_V_BUFFER is None:
        STATIC_V_BUFFER = np.zeros(
            (h // 2, w) + MUTABLE_PROCESSING_BUFFER.shape[2:],
            dtype=MUTABLE_PROCESSING_BUFFER.dtype,
        )
    if drift > 0.85:
        v_shift = int(((drift - 0.85) / 0.15) * (h // 2))
        if v_shift:
            STATIC_V_BUFFER[:v_shift] = MUTABLE_PROCESSING_BUFFER[:v_shift]
            MUTABLE_PROCESSING_BUFFER[:-v_shift] = MUTABLE_PROCESSING_BUFFER[v_shift:]
            MUTABLE_PROCESSING_BUFFER[-v_shift:] = STATIC_V_BUFFER[:v_shift]
            seam, thickness = h - v_shift, int(h * 0.04)
            MUTABLE_PROCESSING_BUFFER[
                max(0, seam - thickness // 2) : min(h, seam + thickness // 2)
            ] = 0
    amount = int(drift * DRIFT_MAX_AMOUNT * 0.6)
    for i in range(h):
        shift = int(math.sin(i * 0.05 + current_time * 0.15) * amount)
        if shift > 0:
            s = shift % w
            if s:
                row = MUTABLE_PROCESSING_BUFFER[i]
                STATIC_ROW_BUFFER[:s], STATIC_ROW_BUFFER[s:] = row[-s:], row[:-s]
                row[:] = STATIC_ROW_BUFFER
        elif shift < 0:
            s = (-shift) % w
            if s:
                row = MUTABLE_PROCESSING_BUFFER[i]
                STATIC_ROW_BUFFER[:-s], STATIC_ROW_BUFFER[-s:] = row[s:], row[:s]
                row[:] = STATIC_ROW_BUFFER


def apply_melt(raw_knob, current_time):
    apply_melt_pass(n(raw_knob), current_time)


def apply_ascii_matrix(raw_knob, current_time):
    global SPARK_COUNTER, SPARK_INTERVAL
    ascii_val = n(raw_knob)
    if ascii_val <= 0.02:
        return
    cv2.cvtColor(MUTABLE_PROCESSING_BUFFER, cv2.COLOR_BGR2GRAY, dst=ASCII_UPRES_GRAY)
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
    ASCII_UPRES_GRAY[:] = ASCII_BLOCK_BUFFER.transpose(0, 2, 1, 3).reshape(
        FRAME_H, FRAME_W
    )
    (
        ASCII_GREEN_BUFFER[:, :, 0],
        ASCII_GREEN_BUFFER[:, :, 1],
        ASCII_GREEN_BUFFER[:, :, 2],
    ) = 0, ASCII_UPRES_GRAY, 0
    SPARK_COUNTER += 1
    if SPARK_COUNTER >= SPARK_INTERVAL:
        cv2.randu(ASCII_NOISE_LOW, 0, 255)
        threshold = max(240, min(254, 254 - int(ascii_val * 3)))
        cv2.threshold(
            ASCII_NOISE_LOW, threshold, 255, cv2.THRESH_BINARY, dst=ASCII_NOISE_LOW
        )
        ASCII_NOISE_LOW[ASCII_LOWRES_GRAY == 0] = 0
        cv2.dilate(
            ASCII_NOISE_LOW,
            np.ones((np.random.randint(1, 7), 1), dtype=np.uint8),
            dst=ASCII_NOISE_LOW,
        )
        SPARK_COUNTER, SPARK_INTERVAL = 0, np.random.randint(7, 17)
    cv2.resize(
        ASCII_NOISE_LOW,
        (FRAME_W, FRAME_H),
        dst=ASCII_NOISE_UP,
        interpolation=cv2.INTER_NEAREST,
    )
    ASCII_GREEN_BUFFER[(ASCII_NOISE_UP > 0) & (ASCII_UPRES_GRAY > 0)] = [31, 255, 31]
    bloom = cv2.resize(
        ASCII_GREEN_BUFFER, (ASCII_GRID_W, ASCII_GRID_H), interpolation=cv2.INTER_LINEAR
    )
    bloom = cv2.resize(
        cv2.GaussianBlur(bloom, (3, 3), 0),
        (FRAME_W, FRAME_H),
        interpolation=cv2.INTER_LINEAR,
    )
    cv2.addWeighted(ASCII_GREEN_BUFFER, 0.8, bloom, 0.4, 0, dst=ASCII_GREEN_BUFFER)
    cv2.addWeighted(
        ASCII_GREEN_BUFFER,
        ascii_val,
        MUTABLE_PROCESSING_BUFFER,
        1.0 - ascii_val,
        0,
        dst=MUTABLE_PROCESSING_BUFFER,
    )


def apply_crt_overlay():
    if CRT_ENABLED:
        cv2.multiply(
            MUTABLE_PROCESSING_BUFFER,
            CRT_MASK,
            MUTABLE_PROCESSING_BUFFER,
            scale=1.0 / 255.0,
        )


# =========================================================================
# POSTERIZE (PHASE 5): NEON TONE BANDS WITH GLITCHY OUTLINES
# =========================================================================
# Colour effect: it does not move pixels. Blacks stay untouched; every other tone becomes a
# neon colour of the Melt palette (dark tones get the deep hues, bright tones the bright ones);
# the borders between tone bands get a 1-3 px outline that is broken up, jittered and flickers,
# so it looks like damaged video, not like pop art.
#
# Per frame it makes a fixed number of large OpenCV calls (about 35) in preallocated buffers,
# no Python loop over pixels or rows. The tone map and the neon fill are computed at half
# resolution (chunky, and four times cheaper); the outlines are drawn at full resolution.


def _posterize_palette():
    """Melt's neon palette ordered from the darkest hue to the brightest."""
    luma = NEON_PALETTES.astype(np.float32) @ np.array(
        [0.114, 0.587, 0.299], np.float32
    )
    return np.ascontiguousarray(NEON_PALETTES[np.argsort(luma)])


class PosterizeEngine:
    """Buffers (about 14 MiB, allocated once) and the per-frame work of Posterize."""

    def __init__(self, seed=None):
        H, W = FRAME_H, FRAME_W
        hh, hw = H // 2, W // 2
        sp = POSTERIZE_SECTION_PX
        self.rng = np.random.default_rng(seed)
        z = lambda h=H, w=W: np.zeros((h, w), dtype=np.uint8, order="C")
        z3 = lambda h=H, w=W: np.zeros((h, w, 3), dtype=np.uint8, order="C")
        self.gray, self.black, self.nonblack, self.ramp = z(), z(), z(), z()
        self.band_full, self.edge, self.edge_tmp = z(), z(), z()
        self.drop_full, self.flick_full, self.wmask = z(), z(), z()
        self.g_half, self.band_half, self.scale_half = z(hh, hw), z(hh, hw), z(hh, hw)
        self.pl_b, self.pl_g, self.pl_r = z(hh, hw), z(hh, hw), z(hh, hw)
        self.neon_half, self.scale3 = z3(hh, hw), z3(hh, hw)
        self.neon_full, self.poster, self.avg, self.edge_col = z3(), z3(), z3(), z3()
        self.white = np.full((H, W, 3), 255, dtype=np.uint8)
        self.ink = z3()
        self.noise_bank = self.rng.integers(0, 256, (4, hh, hw), dtype=np.uint8)
        self.xs = np.tile(np.arange(W, dtype=np.float32), (H, 1))
        self.mapx = self.xs.copy()
        self.mapy = np.tile(np.arange(H, dtype=np.float32)[:, None], (1, W))
        self.kernels = {
            px: np.ones((2 * px + 1, 2 * px + 1), dtype=np.uint8) for px in (1, 2, 3)
        }
        self.drop_small = z(H // sp, W // sp)
        self.flick_small = z(H // sp, W // sp)
        self.palette = _posterize_palette()
        self.curve_x = np.array([p[0] for p in POSTERIZE_CURVE], dtype=np.float64)
        self.curve_y = np.array([p[1] for p in POSTERIZE_CURVE], dtype=np.float64)
        self._lut_key = None
        self.band_lut = np.zeros((256, 1), np.uint8)
        self.color_lut = np.zeros((3, 256, 1), np.uint8)

    def reseed(self, seed):
        """Restart the random sequence; the break-up noise is regenerated too, so the same
        seed always gives the same picture."""
        self.rng = np.random.default_rng(seed)
        self.noise_bank[:] = self.rng.integers(
            0, 256, self.noise_bank.shape, dtype=np.uint8
        )

    def strength(self, raw_knob):
        """Knob value 0-1023 -> strength 0-1 through the response curve (0 = off)."""
        return float(np.interp(raw_knob, self.curve_x, self.curve_y))

    def _luts(self, bands, rotation):
        key = (bands, rotation)
        if key != self._lut_key:
            self.band_lut[:, 0] = (np.arange(256) * bands // 256).astype(np.uint8)
            n_pal = len(self.palette)
            self.color_lut[:] = 0
            for b in range(bands):
                idx = (
                    int(round(b * (n_pal - 1) / max(1, bands - 1))) + rotation
                ) % n_pal
                for ch in range(3):
                    self.color_lut[ch, b, 0] = self.palette[idx, ch]
            self._lut_key = key

    def step(self, frame, raw_knob, current_time):
        s = self.strength(raw_knob)
        if s <= 0.0:
            return
        H, W = FRAME_H, FRAME_W
        sp = POSTERIZE_SECTION_PX
        bands = int(
            round(
                POSTERIZE_BANDS_LOW + (POSTERIZE_BANDS_HIGH - POSTERIZE_BANDS_LOW) * s
            )
        )
        detail = (
            POSTERIZE_DETAIL_LOW + (POSTERIZE_DETAIL_HIGH - POSTERIZE_DETAIL_LOW) * s
        )
        mix = POSTERIZE_MIX_START + (1.0 - POSTERIZE_MIX_START) * min(
            1.0, s / POSTERIZE_MIX_FULL_AT
        )
        px = (
            3
            if s >= POSTERIZE_OUTLINE_3PX_FROM
            else (2 if s >= POSTERIZE_OUTLINE_2PX_FROM else 1)
        )
        dropout = (
            POSTERIZE_DROPOUT_LOW + (POSTERIZE_DROPOUT_HIGH - POSTERIZE_DROPOUT_LOW) * s
        )
        jitter = 1 + int(s * (POSTERIZE_JITTER_MAX_PX - 1) + 1e-9)
        if POSTERIZE_ROTATE_SECONDS > 0 and len(self.palette) > 0:
            rotation = int(current_time / POSTERIZE_ROTATE_SECONDS) % len(self.palette)
        else:
            rotation = 0
        self._luts(bands, rotation)

        # 1. tones at half resolution, with break-up noise at high strength
        cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY, dst=self.gray)
        cv2.resize(
            self.gray, (W // 2, H // 2), dst=self.g_half, interpolation=cv2.INTER_AREA
        )
        if s > POSTERIZE_FRAGMENT_FROM:
            amp = (
                (s - POSTERIZE_FRAGMENT_FROM)
                / (1.0 - POSTERIZE_FRAGMENT_FROM)
                * POSTERIZE_FRAGMENT_AMOUNT
            )
            noise = self.noise_bank[int(self.rng.integers(0, 4))]
            cv2.addWeighted(
                self.g_half, 1.0, noise, amp / 255.0, -amp / 2.0, dst=self.g_half
            )
        cv2.LUT(self.g_half, self.band_lut, dst=self.band_half)

        # 2. neon fill: band -> palette colour, modulated by the original brightness
        cv2.LUT(self.band_half, self.color_lut[0], dst=self.pl_b)
        cv2.LUT(self.band_half, self.color_lut[1], dst=self.pl_g)
        cv2.LUT(self.band_half, self.color_lut[2], dst=self.pl_r)
        cv2.merge([self.pl_b, self.pl_g, self.pl_r], dst=self.neon_half)
        cv2.convertScaleAbs(
            self.g_half, dst=self.scale_half, alpha=detail, beta=255.0 * (1.0 - detail)
        )
        cv2.merge([self.scale_half, self.scale_half, self.scale_half], dst=self.scale3)
        cv2.multiply(self.neon_half, self.scale3, dst=self.neon_half, scale=1.0 / 255.0)
        cv2.resize(
            self.neon_half, (W, H), dst=self.neon_full, interpolation=cv2.INTER_NEAREST
        )
        cv2.resize(
            self.band_half, (W, H), dst=self.band_full, interpolation=cv2.INTER_NEAREST
        )

        # 3. mix with the original; blacks untouched, a narrow ramp mixes half and half
        cv2.addWeighted(frame, 1.0 - mix, self.neon_full, mix, 0, dst=self.poster)
        cv2.compare(self.gray, POSTERIZE_BLACK_THRESHOLD, cv2.CMP_LT, dst=self.black)
        cv2.compare(self.gray, POSTERIZE_BLACK_RAMP, cv2.CMP_LT, dst=self.ramp)
        cv2.bitwise_xor(self.ramp, self.black, dst=self.ramp)
        cv2.addWeighted(frame, 0.5, self.poster, 0.5, 0, dst=self.avg)
        cv2.copyTo(self.avg, self.ramp, self.poster)
        cv2.copyTo(frame, self.black, self.poster)
        cv2.bitwise_not(self.black, dst=self.nonblack)

        # 4. outlines on the tone borders: broken sections, sideways jitter, white-hot flicker
        cv2.dilate(self.band_full, self.kernels[px], dst=self.edge_tmp)
        cv2.compare(self.edge_tmp, self.band_full, cv2.CMP_NE, dst=self.edge)
        self.drop_small[:] = (self.rng.random(self.drop_small.shape) >= dropout) * 255
        cv2.resize(
            self.drop_small, (W, H), dst=self.drop_full, interpolation=cv2.INTER_NEAREST
        )
        cv2.bitwise_and(self.edge, self.drop_full, dst=self.edge)
        offsets = np.repeat(self.rng.integers(-jitter, jitter + 1, H // 4), 4).astype(
            np.float32
        )
        np.add(self.xs, offsets[:, None], out=self.mapx)
        cv2.remap(self.edge, self.mapx, self.mapy, cv2.INTER_NEAREST, dst=self.edge_tmp)
        cv2.bitwise_and(self.edge_tmp, self.nonblack, dst=self.edge_tmp)
        if POSTERIZE_OUTLINE_STYLE == "glow":
            cv2.dilate(self.neon_full, self.kernels[px], dst=self.edge_col)
            outline_colour = self.edge_col
        elif POSTERIZE_OUTLINE_STYLE == "black":
            outline_colour = self.ink
        else:
            outline_colour = self.white
        cv2.copyTo(outline_colour, self.edge_tmp, self.poster)
        self.flick_small[:] = (
            self.rng.random(self.flick_small.shape) < POSTERIZE_FLICKER_PROB
        ) * 255
        cv2.resize(
            self.flick_small,
            (W, H),
            dst=self.flick_full,
            interpolation=cv2.INTER_NEAREST,
        )
        cv2.bitwise_and(self.edge_tmp, self.flick_full, dst=self.wmask)
        cv2.copyTo(self.white, self.wmask, self.poster)

        np.copyto(frame, self.poster)


_POSTER = PosterizeEngine(POSTERIZE_RNG_SEED)


def apply_posterize(raw_knob, current_time):
    """Posterize entry point. raw_knob 0-1023. Modifies MUTABLE_PROCESSING_BUFFER in place."""
    _POSTER.step(MUTABLE_PROCESSING_BUFFER, raw_knob, current_time)


EFFECT_HANDLERS = {
    "pixellation": apply_pixellation,
    "posterize": apply_posterize,
    "chroma_shift": apply_chroma_shift,
    "signal_drift": apply_signal_drift,
    "melt": apply_melt,
    "data_mosh": apply_data_mosh,
    "packet_corruption": apply_packet_corruption,
    "ascii_matrix": apply_ascii_matrix,
}
_EFFECT_PLAN = []
_KNOB_LABELS = [KNOB_EMPTY_LABEL] * 6


def rebuild_effect_plan():
    global _EFFECT_PLAN, _KNOB_LABELS
    notes, order, knob_of = [], [], {}
    for effect in EFFECT_ORDER:
        if effect not in EFFECT_HANDLERS:
            notes.append(f"EFFECT_ORDER: unknown effect {effect!r} ignored")
        elif effect in order:
            notes.append(f"EFFECT_ORDER: {effect!r} listed twice, first entry kept")
        else:
            order.append(effect)
    by_knob = {k: [] for k in range(6)}
    valid_knobs = sorted(
        knob
        for knob in KNOB_ATTACHMENTS
        if isinstance(knob, int) and not isinstance(knob, bool) and 0 <= knob <= 5
    )
    for knob in KNOB_ATTACHMENTS:
        if knob not in valid_knobs:
            notes.append(f"KNOB_ATTACHMENTS: knob {knob!r} is not 0-5, ignored")
    for knob in valid_knobs:
        for effect in KNOB_ATTACHMENTS[knob]:
            if effect not in order:
                notes.append(
                    f"knob {knob}: {effect!r} is unknown or missing from EFFECT_ORDER, ignored"
                )
            elif effect in knob_of:
                notes.append(
                    f"knob {knob}: {effect!r} is already attached to knob {knob_of[effect]}, ignored"
                )
            else:
                knob_of[effect] = knob
                by_knob[knob].append(effect)
    windows = {}
    for effect, window in EFFECT_KNOB_WINDOW.items():
        try:
            lo, hi = float(window[0]), float(window[1])
            valid = 0 <= lo < hi <= 1 and effect in order
        except Exception:
            valid = False
        if valid:
            windows[effect] = (lo, hi)
        else:
            notes.append(
                f"EFFECT_KNOB_WINDOW: {effect!r}: {window!r} needs 0 <= low < high <= 1 and a known effect, ignored"
            )
    _EFFECT_PLAN = [
        (
            effect,
            EFFECT_HANDLERS[effect],
            knob_of[effect],
            windows.get(effect, (0.0, 1.0)),
            effect in EFFECT_SKIP_DURING_WARMUP,
        )
        for effect in order
        if effect in knob_of
    ]
    _KNOB_LABELS = [
        EFFECT_LABELS.get(by_knob[k][0], by_knob[k][0])
        if by_knob[k]
        else KNOB_EMPTY_LABEL
        for k in range(6)
    ]
    for note in notes:
        print(f"[EFFECT CONFIG] {note}")
    return notes


def set_effect_config(order=None, attachments=None, windows=None, weights=None):
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
    return list(_KNOB_LABELS)


def _windowed_raw(raw_knob, window):
    lo, hi = window
    if lo <= 0 and hi >= 1:
        return raw_knob
    x = (raw_knob / 1023.0 - lo) / (hi - lo)
    return 0 if x <= 0 else 1023 if x >= 1 else x * 1023.0


def compute_effect_intensity(knobs):
    return max(
        0.0,
        min(
            1.0,
            sum(
                EFFECT_COST_WEIGHT.get(effect, 0.0)
                * n(_windowed_raw(knobs[knob], window))
                for effect, _handler, knob, window, _skip in _EFFECT_PLAN
            ),
        ),
    )


rebuild_effect_plan()


def get_active_effects(knobs):
    """Statistics names of the attached effects that really act at these knob values
    (after their knob windows), in processing order. Hidden (secondary) effects count too."""
    names = []
    for effect, _handler, knob, window, _skip in _EFFECT_PLAN:
        raw = knobs[knob] if knob < len(knobs) else 0
        if _windowed_raw(raw, window) >= EFFECT_STATS_ACTIVE_FROM.get(effect, 10):
            names.append(EFFECT_STATS_NAME.get(effect, effect))
    return names


def get_wiring_label():
    """Compact text of the current attachments (primary first) and windows, stored with every
    capture, e.g. 0:ascii_matrix|1:signal_drift|...|5:chroma_shift+packet_corruption|w:packet_corruption=0.35-1"""
    knob_of = {effect: knob for effect, _h, knob, _w, _s in _EFFECT_PLAN}
    parts = []
    for knob in range(6):
        names = [
            EFFECT_STATS_NAME.get(effect, effect)
            for effect in KNOB_ATTACHMENTS.get(knob, [])
            if knob_of.get(effect) == knob
        ]
        parts.append(f"{knob}:" + ("+".join(names) if names else "-"))
    windows = [
        f"{EFFECT_STATS_NAME.get(effect, effect)}={lo:g}-{hi:g}"
        for effect, _h, _k, (lo, hi), _s in _EFFECT_PLAN
        if (lo, hi) != (0.0, 1.0)
    ]
    if windows:
        parts.append("w:" + ",".join(windows))
    return "|".join(parts)


def apply_glitch_pipeline(current_time, control_packet):
    global WARMUP_COUNTER, LAST_TEMPORAL_PURGE
    is_warming_up = WARMUP_COUNTER < WARMUP_FRAMES
    cv2.cvtColor(MUTABLE_PROCESSING_BUFFER, cv2.COLOR_BGR2GRAY, dst=SHARED_GRAY_BUFFER)
    if is_warming_up:
        PREV_FRAME_BUFFER[:] = MUTABLE_PROCESSING_BUFFER
        WARMUP_COUNTER += 1
    if current_time - LAST_TEMPORAL_PURGE > 90.0:
        PREV_FRAME_BUFFER[:] = MUTABLE_PROCESSING_BUFFER
        LAST_TEMPORAL_PURGE = current_time
    for _effect, handler, knob, window, skip in _EFFECT_PLAN:
        if not (skip and is_warming_up):
            handler(_windowed_raw(control_packet.knobs[knob], window), current_time)
    apply_crt_overlay()


# HARDWARE OUTPUT EXECUTION STATIONS
# ==================================================


def _write_naked_capture(disk_id, frame):
    """Write a device-only naked capture without affecting capture success."""
    if frame is None:
        return False
    try:
        ok = cv2.imwrite(os.path.join(NAKED_DIR, f"{disk_id}_naked.jpg"), frame)
        if not ok:
            logger.error(
                "[NAKED] could not write naked capture for session %s", disk_id
            )
        return bool(ok)
    except Exception as exc:
        logger.error("[NAKED] naked capture failed for session %s: %s", disk_id, exc)
        return False


def cleanup_sessions(limit=100):
    try:
        files = os.listdir(CAPTURE_DIR)
        timestamps = sorted(
            [f.replace("_color.jpg", "") for f in files if "_color.jpg" in f]
        )
        if len(timestamps) > limit:
            to_archive = timestamps[:-limit]
            archived_count = 0
            for ts in to_archive:
                # 1. Safely move the Color capture to the Archive folder
                src_color = os.path.join(CAPTURE_DIR, f"{ts}_color.jpg")
                dst_color = os.path.join(ARCHIVE_DIR, f"{ts}_color.jpg")
                if os.path.exists(src_color):
                    try:
                        if os.path.exists(dst_color):
                            os.remove(dst_color)
                        shutil.move(src_color, dst_color)
                        archived_count += 1
                    except Exception as move_err:
                        logger.error(
                            f"[ARCHIVE ERROR] Failed to archive {src_color}: {move_err}"
                        )

                # 2. Delete Black & White receipt print file
                bw_path = os.path.join(CAPTURE_DIR, f"{ts}_bw.jpg")
                if os.path.exists(bw_path):
                    try:
                        os.remove(bw_path)
                    except Exception:
                        pass

                # 3. Delete saved QR code image
                qr_path = os.path.join(SAVED_DIR, f"{ts}_qr.png")
                if os.path.exists(qr_path):
                    try:
                        os.remove(qr_path)
                    except Exception:
                        pass

                # 4. Delete composite receipt image
                comp_png = os.path.join(CAPTURE_DIR, f"{ts}_composite.png")
                if os.path.exists(comp_png):
                    try:
                        os.remove(comp_png)
                    except Exception:
                        pass

            if archived_count > 0:
                logger.info(
                    f"[ARCHIVE] Safely relocated {archived_count} color captures to {ARCHIVE_DIR}"
                )
                print(
                    f"[ARCHIVE] Relocated {archived_count} excess captures to {ARCHIVE_DIR} (kept newest {limit})"
                )
    except Exception as e:
        logger.error(f"Archival pipeline exception: {e}")
        print(f"Archival pipeline exception: {e}")


def generate_composite_receipt_image(ts: str) -> str:
    """
    Composites the QR code into a corner of the rotated B&W capture.
    Saves as a lossless PNG intermediate (captures/{ts}_composite.png).
    Leaves '{ts}_bw.jpg' unchanged.
    Returns composite path on verified success, or None on failure.
    """
    composite_path = os.path.join(CAPTURE_DIR, f"{ts}_composite.png")
    if os.path.exists(composite_path) and os.path.getsize(composite_path) > 0:
        return composite_path

    bw_path = os.path.join(CAPTURE_DIR, f"{ts}_bw.jpg")
    qr_path = os.path.join(SAVED_DIR, f"{ts}_qr.png")

    if not os.path.exists(bw_path):
        logger.error("[COMPOSITE] Base B&W capture missing for %s: %s", ts, bw_path)
        return None

    # Verify or generate QR upfront
    if not os.path.exists(qr_path):
        try:
            landing_url = get_landing_url(ts)
            qr_img = qrcode.make(landing_url)
            qr_img.save(qr_path)
            logger.info("[COMPOSITE] QR code generated on demand for session %s", ts)
        except Exception as qr_err:
            logger.error("[COMPOSITE] QR generation failed for %s: %s", ts, qr_err)
            return None

    try:
        with Image.open(bw_path) as bw_img, Image.open(qr_path) as qr_img:
            composite = bw_img.convert("L").copy()
            bw_w, bw_h = composite.size

            # 1. Orientation assertion: receipt frame is expected in portrait orientation (height > width)
            if bw_h <= bw_w:
                logger.warning(
                    "[COMPOSITE] Base image for %s is landscape (%dx%d); expected portrait.",
                    ts,
                    bw_w,
                    bw_h,
                )

            # 2. Dimension validation: ensure QR and margins fit comfortably within frame
            max_allowed_qr = min(bw_w, bw_h) - (2 * RECEIPT_QR_MARGIN)
            effective_qr_size = RECEIPT_QR_SIZE
            if effective_qr_size > max_allowed_qr:
                logger.warning(
                    "[COMPOSITE] QR size (%dpx) exceeds safe boundary (%dpx). Clamping to fit.",
                    effective_qr_size,
                    max_allowed_qr,
                )
                effective_qr_size = max(64, max_allowed_qr)

            # 3. Resize QR with NEAREST resampling to keep pure 1-bit square modules
            qr_resized = qr_img.convert("L").resize(
                (effective_qr_size, effective_qr_size), Image.Resampling.NEAREST
            )

            # 4. Apply rotation (90° clockwise by default to match capture viewing axis)
            if RECEIPT_QR_ROTATION == 90:
                qr_rotated = qr_resized.rotate(-90, expand=True)
            elif RECEIPT_QR_ROTATION == 180:
                qr_rotated = qr_resized.rotate(180, expand=True)
            elif RECEIPT_QR_ROTATION == 270:
                qr_rotated = qr_resized.rotate(90, expand=True)
            else:
                qr_rotated = qr_resized

            # 5. Calculate position based on specified corner
            corner = RECEIPT_QR_CORNER.lower()
            if corner == "bottom-right":
                x_pos = bw_w - effective_qr_size - RECEIPT_QR_MARGIN
                y_pos = bw_h - effective_qr_size - RECEIPT_QR_MARGIN
            elif corner == "top-left":
                x_pos = RECEIPT_QR_MARGIN
                y_pos = RECEIPT_QR_MARGIN
            elif corner == "top-right":
                x_pos = bw_w - effective_qr_size - RECEIPT_QR_MARGIN
                y_pos = RECEIPT_QR_MARGIN
            else:  # default: "bottom-left"
                x_pos = RECEIPT_QR_MARGIN
                y_pos = bw_h - effective_qr_size - RECEIPT_QR_MARGIN

            x_pos = max(0, min(x_pos, bw_w - effective_qr_size))
            y_pos = max(0, min(y_pos, bw_h - effective_qr_size))

            # 6. Paste QR into position (including its white quiet zone)
            composite.paste(qr_rotated, (x_pos, y_pos))

            # 7. Save as lossless PNG to avoid compression artifacts on print edges
            composite.save(composite_path, format="PNG")

            # Final validation check
            if (
                not os.path.exists(composite_path)
                or os.path.getsize(composite_path) == 0
            ):
                logger.error(
                    "[COMPOSITE] Verification failed: %s is empty or missing.",
                    composite_path,
                )
                return None

            logger.info(
                "[COMPOSITE] Lossless composite created successfully: %s",
                composite_path,
            )
            return composite_path

    except Exception as comp_err:
        logger.error(
            "[COMPOSITE] Unexpected failure generating composite for %s: %s",
            ts,
            comp_err,
        )
        return None


def print_booth_receipt(ts):
    print(f"PRINTER: Spooling hardware receipt pipeline for session {ts}...")

    with printer_hardware_lock:  # Serializes multi-threaded physical hardware access
        printer = None
        try:
            is_offline_capture = False
            if os.path.exists("pending_uploads.txt"):
                try:
                    with open("pending_uploads.txt", "r") as cache_log:
                        if ts in cache_log.read():
                            is_offline_capture = True
                except Exception:
                    pass

            TARGET_WIDTH = 576
            printer = File("/dev/usb/lp0")
            printer.hw("INIT")
            # -------------------------------------------------------------
            # MANUAL RECEIPT TEXT: Update header text below per event
            # -------------------------------------------------------------
            printer.text("@->>--     =========================     --<<-@\n")
            printer.text("   --<<-@    GLITCHBOOTH HATIRA$I    @->>--   \n")
            printer.text("                  powered by                   \n")
            printer.text("    --<<-@      Dirtcake Studio     @->>--     \n")
            printer.text(
                f"              {time.strftime('%Y-%m-%d %H:%M:%S')}              \n"
            )
            printer.text("@->>--     =========================     --<<-@\n")

            # Ensure composite image is ready
            composite_path = os.path.join(CAPTURE_DIR, f"{ts}_composite.png")
            if not os.path.exists(composite_path):
                composite_path = generate_composite_receipt_image(ts)

            using_composite = bool(composite_path and os.path.exists(composite_path))
            target_image_path = (
                composite_path if using_composite else f"{CAPTURE_DIR}/{ts}_bw.jpg"
            )

            if os.path.exists(target_image_path):
                with Image.open(target_image_path) as img:
                    aspect_ratio = img.height / img.width
                    target_height = int(TARGET_WIDTH * aspect_ratio)
                    resized_img = img.resize(
                        (TARGET_WIDTH, target_height), Image.Resampling.LANCZOS
                    )
                    dithered_img = resized_img.convert("1")
                    printer.image(dithered_img)
                # printer.text("\n")

                if not using_composite:
                    # Explicit fallback notice printed directly on paper if composite failed
                    logger.warning(
                        "[PRINTER FALLBACK] Session %s printed without embedded QR.", ts
                    )
                    printer.text(" > NOTICE: QR EMBEDDING UNAVAILABLE  \n")
                    printer.text(f" > RETRIEVE AT: {get_landing_url(ts)}\n\n")
            else:
                logger.error(
                    "[PRINTER ERROR] No printable photo found for session %s", ts
                )
                print(f"[PRINTER ERROR] No printable photo found for session {ts}")

            if is_offline_capture:
                printer.text(" > NETWORK OFFLINE // CACHED LOCALLY  \n")
                printer.text(" > Upload link down. Capture is stored safely. \n")
                printer.text(" > Sync will auto-resume when booth is online. \n")
                printer.text(" > You can get your digital copy online later: \n")
                printer.text(f"   {get_landing_url(ts)}\n")
            else:
                printer.text(" > Scan to save your digital copy!   \n")
                printer.text(" > Follow us on socials: @dirtcakestudio   \n")
                printer.text(" > Follow us on socials: @glitchbooth.online   \n")

            printer.text("@->>--     =========================     --<<-@\n")
            printer.ln(2)
            printer.cut()
            time.sleep(0.5)
            print("PRINTER: Hardware receipt complete.")

        except Exception as e:
            print(f"PRINTER HARDWARE FAULT: {e}")
        finally:
            if printer is not None:
                try:
                    printer.close()
                except Exception as close_err:
                    print(f"PRINTER UNHOOK EXCEPTION: {close_err}")


def idle_sync_worker():
    """
    Low-priority background worker that scans for offline captures cached in
    pending_uploads.txt and re-attempts synchronization when grid link recovers.
    """
    print("IO IDLE SYNC ENGINE: Resilient background scheduler active.")

    while not SHUTDOWN_EVENT.is_set():
        try:
            if SHUTDOWN_EVENT.wait(timeout=30):
                break

            if not os.path.exists("pending_uploads.txt"):
                continue

            with open("pending_uploads.txt", "r") as cache_log:
                backlog = [line.strip() for line in cache_log if line.strip()]

            if not backlog:
                continue

            still_pending = []
            for ts in backlog:
                local_color_path = f"{CAPTURE_DIR}/{ts}_color.jpg"
                remote_filename = f"{ts}_color.jpg"

                if not os.path.exists(local_color_path):
                    continue

                try:
                    upload_url = "https://upload.imagekit.io/api/v1/files/upload"
                    with open(local_color_path, "rb") as f:
                        files = {
                            "file": (remote_filename, f, "image/jpeg"),
                            "fileName": (None, remote_filename),
                            "useUniqueFileName": (None, "false"),
                            "folder": (None, get_ik_target_folder()),
                        }
                        headers = {"Authorization": f"Basic {IK_AUTH_STRING}"}

                        # Check network dynamically by issuing a timeout-protected post request
                        response = HTTP_SESSION.post(
                            upload_url, headers=headers, files=files, timeout=(4, 10)
                        )

                    if response.status_code == 200:
                        print(
                            f"[IDLE SYNC SUCCESS] Latent sync accomplished for session {ts}. Evicting from cache."
                        )
                    else:
                        still_pending.append(ts)
                except Exception:
                    still_pending.append(ts)

            if len(still_pending) != len(backlog):
                tmp_path = "pending_uploads.tmp"
                final_path = "pending_uploads.txt"

                try:
                    with cache_file_lock:
                        with open(tmp_path, "w") as tmp_log:
                            for ts in still_pending:
                                tmp_log.write(f"{ts}\n")
                            tmp_log.flush()
                            os.fsync(tmp_log.fileno())
                        os.replace(tmp_path, final_path)

                    print(
                        f"[OFFLINE CACHE] Sync logging file updated atomically. Remaining: {len(still_pending)}"
                    )
                except Exception as file_err:
                    print(
                        f"[OFFLINE CRITICAL] Atomic file sync write failure: {file_err}"
                    )
                if os.path.exists(tmp_path):
                    try:
                        os.remove(tmp_path)
                    except:
                        pass

        except Exception as sync_fault:
            print(f"IDLE SYNC WORKER ERROR: {sync_fault}")


# CLOUD INTERFACE BACKGROUND ENGINE
# ==================================================


def upload_queue_worker():
    global upload_status_msg
    print("[INIT] Cloud Upload Worker: Active and bound.")

    while not SHUTDOWN_EVENT.is_set():
        try:
            try:
                ts = upload_queue.get(timeout=1.0)
            except queue.Empty:
                continue
            local_color_path = f"{CAPTURE_DIR}/{ts}_color.jpg"
            remote_filename = f"{ts}_color.jpg"
            qr_filename = f"{ts}_qr.png"
            qr_path = os.path.join(SAVED_DIR, qr_filename)

            # --- PHASE A: Unified QR Code Generation ---
            landing_url = get_landing_url(ts)
            qr_generated = False
            try:
                qr_img = qrcode.make(landing_url)
                qr_img.save(qr_path)
                qr_generated = True
            except Exception as qr_err:
                logger.error(
                    "[QR GENERATION ERROR] Session %s QR creation failed: %s",
                    ts,
                    qr_err,
                )
                print(f"[QR ERROR] Failed to generate/save QR code for {ts}: {qr_err}")

            # --- PHASE B: Pre-generate Composite Receipt Image ---
            composite_ready = False
            if qr_generated:
                comp_result = generate_composite_receipt_image(ts)
                if comp_result:
                    composite_ready = True
                else:
                    logger.error(
                        "[COMPOSITE WARNING] Composite prep failed for %s; print pipeline will handle fallback.",
                        ts,
                    )

            with mode_lock:
                ACTIVE_SESSION["composite_ready"] = composite_ready

            # --- PHASE C: ImageKit Cloud Upload (Independent of composite) ---

            upload_status_msg = "UPLOADING"
            upload_success = False
            MAX_UPLOAD_RETRIES = 2

            for attempt in range(MAX_UPLOAD_RETRIES):
                try:
                    upload_url = "https://upload.imagekit.io/api/v1/files/upload"
                    with open(local_color_path, "rb") as f:
                        files = {
                            "file": (remote_filename, f, "image/jpeg"),
                            "fileName": (None, remote_filename),
                            "useUniqueFileName": (None, "false"),
                            "folder": (None, get_ik_target_folder()),
                        }
                        headers = {"Authorization": f"Basic {IK_AUTH_STRING}"}

                        response = HTTP_SESSION.post(
                            upload_url, headers=headers, files=files, timeout=(3, 8)
                        )

                    if response.status_code == 200:
                        res_data = response.json()

                        upload_status_msg = "SUCCESS"
                        upload_success = True

                        with mode_lock:
                            ACTIVE_SESSION["upload_complete"] = True
                        break
                    else:
                        print(
                            f"CLOUD SERVER REFUSAL ERROR: {response.status_code} (Attempt {attempt + 1})"
                        )
                        time.sleep(1)

                except Exception as e:
                    print(f"API BUS NETWORK LOSS: {e} (Attempt {attempt + 1})")
                    time.sleep(1)

            if not upload_success:
                upload_status_msg = "FAILED"

                try:
                    with cache_file_lock:
                        with open("pending_uploads.txt", "a") as cache_log:
                            cache_log.write(f"{ts}\n")

                    print(
                        f"[OFFLINE FALLBACK] Link offline. Cached session {ts} locally."
                    )
                except Exception as fallback_err:
                    print(
                        f"[OFFLINE CRITICAL] Failed to execute local asset fallback: {fallback_err}"
                    )

                with mode_lock:
                    ACTIVE_SESSION["upload_failed"] = True

            upload_queue.task_done()

        except Exception as fatal_e:
            print(f"UPLOAD QUEUE WORKER FATAL EXCEPTION: {fatal_e}")
            time.sleep(1)


# SYNCHRONOUS MATERIALIZATION BARRIER CORE LOOP
# ==================================================


def background_loop():
    global last_button_state, capture_lock_until, FRAME_ID
    global UI_FRAME_SEQ  # NEW
    # Disabling button-knobs combo shutdown feature
    # global shutdown_hold_start
    global SHARED_JPEG_STREAM_BUFFER, SHARED_JPEG_ARRAY
    global PRINT_ALPHA, PRINT_BETA

    threading.Thread(target=camera_io_worker, daemon=True).start()

    serial_connection = None
    last_serial_retry = 0.0

    while True:
        frame_start = time.time()

        try:
            if serial_connection is None:
                now_time = time.time()
                if now_time - last_serial_retry > 2.0:
                    last_serial_retry = now_time
                    for port in SERIAL_PORTS:
                        try:
                            serial_connection = serial.Serial(port, 9600, timeout=0.05)
                            print(f"[HARDWARE LINK] Connected to serial port: {port}")
                            logger.info(
                                "[HARDWARE LINK] Connected to serial port: %s", port
                            )
                            break
                        except Exception as exc:
                            logger.debug(
                                "[HARDWARE LINK] Serial port unavailable: %s: %s",
                                port,
                                exc,
                            )
                            continue
            else:
                try:
                    update_knobs(serial_connection)
                except Exception as serial_err:
                    print(f"[HARDWARE LINK] Serial connection lost: {serial_err}")
                    try:
                        serial_connection.close()
                    except Exception as exc:
                        logger.debug(
                            "[HARDWARE LINK] Error occurred while closing serial connection: %s",
                            exc,
                        )
                    serial_connection = None

            with mode_lock:
                ACTIVE_SESSION["knobs"] = list(GLOBAL_KNOBS)
                loop_mode = ACTIVE_SESSION["mode"]
                session_key = ACTIVE_SESSION["session_id"]
                upload_done = ACTIVE_SESSION["upload_complete"]
                upload_lost = ACTIVE_SESSION["upload_failed"]
                already_printed = ACTIVE_SESSION["print_dispatched"]
                c_start = ACTIVE_SESSION["countdown_start_time"]

            packet = ControlPacket(GLOBAL_KNOBS, GLOBAL_BUTTON, loop_mode)
            loop_execution_time = time.time()
            loop_monotonic = time.monotonic()

            button_pressed_edge = last_button_state == 1 and packet.button == 0

            with cam_raw_lock:
                latest_frame = LATEST_CAMERA_FRAME
                if latest_frame is not None:
                    fh, fw = latest_frame.shape[:2]
                    if fh == FRAME_H and fw == FRAME_W:
                        MUTABLE_PROCESSING_BUFFER[:] = latest_frame
                    elif fh == FRAME_H and fw > FRAME_W:
                        start_x = (fw - FRAME_W) // 2
                        MUTABLE_PROCESSING_BUFFER[:] = latest_frame[
                            :, start_x : start_x + FRAME_W
                        ]
                    else:
                        cv2.resize(
                            latest_frame,
                            (FRAME_W, FRAME_H),
                            dst=MUTABLE_PROCESSING_BUFFER,
                        )

            if NAKED_CAPTURE_ENABLED and loop_mode == "pre-capture-countdown":
                NAKED_FRAME_BUFFER[:] = MUTABLE_PROCESSING_BUFFER

            apply_glitch_pipeline(loop_execution_time, packet)

            try:
                effect_intensity = compute_effect_intensity(packet.knobs)
            except Exception:
                effect_intensity = 0.5

            with frame_lock:
                if loop_mode in ["countdown", "processing", "latest"]:
                    UI_DISPLAY_BUFFER[:] = FROZEN_DISPLAY_BUFFER[:]
                    if loop_mode == "countdown":
                        if (loop_monotonic - c_start) < FLASH_DURATION:
                            UI_DISPLAY_BUFFER.fill(255)  # Pure white camera flash
                        else:
                            cv2.convertScaleAbs(
                                UI_DISPLAY_BUFFER,
                                dst=UI_DISPLAY_BUFFER,
                                alpha=0.4,
                                beta=0,
                            )
                else:
                    # Live preview used in "live" AND "pre-capture-countdown"
                    UI_DISPLAY_BUFFER[:] = MUTABLE_PROCESSING_BUFFER[:]
                UI_FRAME_SEQ += 1

            stream_jpeg_quality = max(35, min(65, int(65 - (effect_intensity * 30))))

            h, w = UI_DISPLAY_BUFFER.shape[:2]

            if effect_intensity >= 0.55:
                target_preview_width = PREVIEW_DOWNSCALE_WIDTH
            else:
                target_preview_width = w

            scale_factor = target_preview_width / w

            if scale_factor < 1.0:
                preview_matrix = cv2.resize(
                    UI_DISPLAY_BUFFER,
                    (target_preview_width, int(h * scale_factor)),
                    interpolation=cv2.INTER_NEAREST,
                )
            else:
                preview_matrix = UI_DISPLAY_BUFFER

            should_encode = (ACTIVE_STREAM_VIEWERS > 0) or (FRAME_ID % 15 == 0)
            quality = stream_jpeg_quality if ACTIVE_STREAM_VIEWERS > 0 else 20

            if should_encode:
                ret, encoded_jpeg = cv2.imencode(
                    ".jpg",
                    preview_matrix,
                    [int(cv2.IMWRITE_JPEG_QUALITY), quality],
                )
            else:
                # don't encode, but don't drop the connection
                ret = False

            if ret:
                with frame_lock:
                    SHARED_JPEG_ARRAY = encoded_jpeg
                    SHARED_JPEG_STREAM_BUFFER = memoryview(SHARED_JPEG_ARRAY)
                    FRAME_ID += 1

            # Disabling button-knobs combo shutdown feature
            """
            shutdown_ready = all(0 <= k <= SHUTDOWN_THRESHOLD for k in packet.knobs)
            if shutdown_ready and packet.button == 0:
                if shutdown_hold_start is None:
                    shutdown_hold_start = loop_execution_time
                elif loop_execution_time - shutdown_hold_start > 5:
                    global cam_thread_alive
                    cam_thread_alive = False
                    time.sleep(0.5)
                    subprocess.Popen(["systemctl", "poweroff"])
                    return
            else:
                shutdown_hold_start = None
            """

            # CENTRAL CONTROL MATRIX & TRANSITION MACHINE
            # ------------------------------------------------------------------

            # Reusable Storage Worker (Scoped inside state_machine_loop)
            def async_storage_worker(
                disk_id, color_mat, bw_mat, active_knobs, naked_mat=None
            ):
                _write_naked_capture(disk_id, naked_mat)
                color_ok = cv2.imwrite(
                    os.path.join(CAPTURE_DIR, f"{disk_id}_color.jpg"), color_mat
                )
                bw_ok = cv2.imwrite(
                    os.path.join(CAPTURE_DIR, f"{disk_id}_bw.jpg"), bw_mat
                )

                if not color_ok or not bw_ok:
                    print(
                        f"[STATE ENGINE CRITICAL] Disk IO failure writing session {disk_id}. Tripping fallback flag."
                    )
                    logger.error(
                        "[STATE ENGINE] Disk IO failure writing session %s. color_ok=%s bw_ok=%s",
                        disk_id,
                        color_ok,
                        bw_ok,
                    )
                    with mode_lock:
                        ACTIVE_SESSION["upload_failed"] = True
                    return

                logger.info(
                    "[CAPTURE] Session files written: %s color_ok=%s bw_ok=%s",
                    disk_id,
                    color_ok,
                    bw_ok,
                )

                try:
                    upload_queue.put(disk_id, block=False)
                    logger.info("[UPLOAD QUEUE] Session queued for upload: %s", disk_id)
                    cleanup_sessions(limit=100)
                except queue.Full:
                    print(
                        f"[STATE ENGINE WARNING] Queue full. Enqueuing {disk_id} directly to offline cache file."
                    )
                    try:
                        with cache_file_lock:
                            with open("pending_uploads.txt", "a") as cache_log:
                                cache_log.write(f"{disk_id}\n")
                    except Exception as cache_err:
                        print(
                            f"[STATE ENGINE CRITICAL] Failed to write queue fallback to cache: {cache_err}"
                        )

                    with mode_lock:
                        ACTIVE_SESSION["upload_failed"] = True

                if TELEMETRY_ENABLED:
                    try:
                        print(
                            f"[TELEMETRY ENGINE] Executing core capture ledger entry for session: {disk_id}"
                        )
                        telemetry.record_capture(
                            session_id=disk_id,
                            knobs=active_knobs,
                            effects=get_active_effects(active_knobs),
                            wiring=get_wiring_label(),
                        )
                    except Exception as telemetry_fault:
                        print(
                            f"[TELEMETRY HOOK ERROR] Non-fatal capture entry failure: {telemetry_fault}"
                        )

            # STATE 1: LIVE VIEWER - Initiates pre-capture countdown
            if loop_mode == "live":
                if button_pressed_edge and loop_execution_time >= capture_lock_until:
                    with mode_lock:
                        ACTIVE_SESSION["mode"] = "pre-capture-countdown"
                        ACTIVE_SESSION["countdown_start_time"] = loop_monotonic
                        ACTIVE_SESSION["countdown_remaining"] = 3

                    print(
                        "[STATE ENGINE] Button pressed. Starting pre-capture countdown (3s pose window)."
                    )

            # STATE 1B: PRE-CAPTURE COUNTDOWN - Live pose timer (3.. 2.. 1..) then snapshot capture
            elif loop_mode == "pre-capture-countdown":
                time_delta = loop_monotonic - c_start

                if time_delta < 3.0:
                    remaining_steps = max(1, 3 - math.floor(time_delta))
                    with mode_lock:
                        ACTIVE_SESSION["countdown_remaining"] = remaining_steps
                else:
                    # EXACT 3.0s DEADLINE REACHED: EXECUTE CAPTURE IMMEDIATELY
                    high_res_id = f"{int(time.time_ns() // 1000000)}"

                    FROZEN_DISPLAY_BUFFER[:] = MUTABLE_PROCESSING_BUFFER[:]

                    cv2.cvtColor(
                        MUTABLE_PROCESSING_BUFFER,
                        cv2.COLOR_BGR2GRAY,
                        dst=STATIC_MELT_GRAY_BUFFER,
                    )
                    contrast_boosted_gray = cv2.convertScaleAbs(
                        STATIC_MELT_GRAY_BUFFER, alpha=PRINT_ALPHA, beta=PRINT_BETA
                    )
                    monochrome_rotated_frame = cv2.rotate(
                        contrast_boosted_gray, cv2.ROTATE_90_CLOCKWISE
                    )

                    FROZEN_PRINT_BUFFER[:] = monochrome_rotated_frame

                    with mode_lock:
                        ACTIVE_SESSION["mode"] = "countdown"
                        ACTIVE_SESSION["session_id"] = high_res_id
                        ACTIVE_SESSION["upload_complete"] = False
                        ACTIVE_SESSION["upload_failed"] = False
                        ACTIVE_SESSION["composite_ready"] = False
                        ACTIVE_SESSION["print_dispatched"] = False
                        ACTIVE_SESSION["countdown_start_time"] = loop_monotonic
                        ACTIVE_SESSION["countdown_remaining"] = 0

                    print(
                        f"[STATE ENGINE] Pose countdown finished. Captured frame for {high_res_id}. Advanced to post-capture latency mask ('PRINTING')."
                    )

                    captured_knobs = list(GLOBAL_KNOBS)
                    threading.Thread(
                        target=async_storage_worker,
                        args=(
                            high_res_id,
                            FROZEN_DISPLAY_BUFFER.copy(),
                            FROZEN_PRINT_BUFFER.copy(),
                            captured_knobs,
                            NAKED_FRAME_BUFFER.copy()
                            if NAKED_CAPTURE_ENABLED
                            else None,
                        ),
                        daemon=True,
                    ).start()

            # STATE 2: POST-CAPTURE LATENCY MASK - Displays "PRINTING" overlay while offloading disk/upload
            elif loop_mode == "countdown":
                time_delta = loop_monotonic - c_start

                if time_delta >= 3.0:
                    with mode_lock:
                        ACTIVE_SESSION["mode"] = "processing"

                    logger.info(
                        "[STATE ENGINE] Post-capture latency mask complete. Session %s advanced to processing.",
                        session_key,
                    )

            # STATE 3: UNIFIED PROCESSING & PRINT DISPATCH GATING NODE
            elif loop_mode == "processing":
                with mode_lock:
                    if (
                        ACTIVE_SESSION["upload_complete"]
                        or ACTIVE_SESSION["upload_failed"]
                    ) and not ACTIVE_SESSION["print_dispatched"]:
                        ACTIVE_SESSION["mode"] = "latest"
                        ACTIVE_SESSION["print_dispatched"] = True
                        local_session_key = ACTIVE_SESSION["session_id"]

                        print(
                            f"[STATE ENGINE] Single Print Authority: Dispatching print thread for key: {local_session_key}"
                        )
                        threading.Thread(
                            target=print_booth_receipt,
                            args=(local_session_key,),
                            daemon=True,
                        ).start()

            # STATE 4: REVIEW VIEW GATING NODE - Displays confirmation elements until the button is pressed
            elif loop_mode == "latest":
                if button_pressed_edge:
                    print(
                        f"[STATE ENGINE] Clearing session {session_key}. Returning control to the live feed."
                    )
                    capture_lock_until = loop_execution_time + 1.2

                    global upload_status_msg
                    upload_status_msg = "IDLE"

                    with mode_lock:
                        ACTIVE_SESSION["mode"] = "live"
                        ACTIVE_SESSION["session_id"] = None
                        ACTIVE_SESSION["countdown_remaining"] = 3

                    # Force a comprehensive GC collection during idle state transition
                    # gc.collect()

            last_button_state = packet.button

            # Adaptive FPS governor
            if effect_intensity >= 0.75:
                dynamic_fps = 10
            elif effect_intensity >= 0.55:
                dynamic_fps = 12
            else:
                dynamic_fps = 15

            target_dt = 1.0 / dynamic_fps

            elapsed = time.time() - frame_start
            remaining = target_dt - elapsed

            if remaining > 0:
                time.sleep(remaining)

        except Exception as loop_fault:
            print(
                f"[STATE ENGINE CRITICAL FAULT] Core execution exception: {loop_fault}"
            )
            time.sleep(1.0)


# READ-ISOLATED MJPEG STREAM GATEWAY
# ==================================================


def generate_frames():
    global ACTIVE_STREAM_VIEWERS

    local_stream_frame_id = -1
    last_sent = 0

    try:
        frame_interval = 1.0 / STREAM_FPS if STREAM_FPS > 0 else 1.0 / 15.0

        with frame_lock:
            ACTIVE_STREAM_VIEWERS += 1

        while True:
            now = time.time()
            if now - last_sent < frame_interval:
                time.sleep(0.005)
                continue

            with frame_lock:
                if local_stream_frame_id == FRAME_ID:
                    frame_is_stale = True
                else:
                    frame_is_stale = False
                    local_stream_frame_id = FRAME_ID  # Track state locally
                    local_jpeg_payload = SHARED_JPEG_STREAM_BUFFER

            if frame_is_stale:
                time.sleep(0.01)
                continue

            last_sent = now

            if not len(local_jpeg_payload):
                time.sleep(0.01)
                continue

            if FRAME_ID % 1000 == 0:
                print(f"[STREAM Sending frame {FRAME_ID}]")
            yield (
                b"--frame\r\n"
                b"Content-Type: image/jpeg\r\n"
                b"Content-Length: "
                + str(len(local_jpeg_payload)).encode()
                + b"\r\n\r\n"
                + local_jpeg_payload.tobytes()
                + b"\r\n"
            )

    except (BrokenPipeError, ConnectionResetError, GeneratorExit):
        return
    except Exception as e:
        print(f"[CRITICAL STREAM ERROR]: {e}")
        return
    finally:
        with frame_lock:
            ACTIVE_STREAM_VIEWERS = max(0, ACTIVE_STREAM_VIEWERS - 1)


# GRACEFUL SHUTDOWN HANDLER
# ==================================================


def graceful_shutdown_handler(signum, frame):
    print("\n\n[SHUTDOWN] Signal intercepted! Initiating formal teardown...")
    SHUTDOWN_EVENT.set()

    if TELEMETRY_ENABLED:
        try:
            telemetry.request_shutdown()
        except Exception as e:
            print(f"  [WARNING] Failed to request telemetry shutdown boundary: {e}")

    try:
        import subprocess

        # If disable was requested via web route, handle it now
        if ACTIVE_SESSION.get("disable_service_on_exit", False):
            print("[SHUTDOWN] Softly disabling glitchbooth.service for next boot...")
            subprocess.run(
                ["sudo", "systemctl", "disable", "glitchbooth.service"], check=False
            )

        # ONLY run TTY/getty rescue if we are exiting to terminal (NOT powering off or rebooting)
        if ACTIVE_SESSION.get("exit_to_terminal", False):
            print("[SHUTDOWN TTY RESCUE] Resetting console input streams...")
            subprocess.run(["stty", "sane"], check=False)
            subprocess.run(["sudo", "kbd_mode", "-a"], check=False)

            print("[SHUTDOWN TTY RESCUE] Spawning fresh login prompt on tty1...")
            subprocess.run(
                ["sudo", "systemctl", "start", "getty@tty1.service"], check=False
            )
            subprocess.run(["sudo", "chvt", "1"], check=False)
            print("[SHUTDOWN TTY RESCUE] Local terminal login prompt restored!")
        else:
            print("[SHUTDOWN] System reboot/poweroff in progress. Skipping TTY rescue.")

    except Exception as tty_err:
        print(f"[SHUTDOWN TTY RESCUE] Terminal rescue routine failed: {tty_err}")

    print("[SYSTEM EXIT] Teardown complete. Process terminated.")
    os._exit(0)


# BACKEND ENGINE STARTING ANd OWNING ALL THE WORKERS
# ==================================================


def start_backend_engine(blocking=True):
    """
    Set blocking=True for standalone server operation.
    Set blocking=False when embedded inside Pygame.
    """
    import signal

    signal.signal(signal.SIGINT, graceful_shutdown_handler)
    signal.signal(signal.SIGTERM, graceful_shutdown_handler)

    if CRT_ENABLED:
        initialize_crt_mask()

    if TELEMETRY_ENABLED:
        telemetry.compile_summary_stats()

    worker_configs = [
        ("UploadWorker", upload_queue_worker),
        (
            "TelemetryWorker",
            telemetry_worker,
        ),  # telemetry worker from server.py for state route
    ]
    if TELEMETRY_ENABLED:
        worker_configs.extend(
            [
                (
                    "TelemetryQueueConsumer",
                    telemetry.telemetry_consumer_worker,
                ),  # telemetry worker from telemetry.py
                (
                    "TelemetryCloudSyncWorker",
                    lambda: telemetry.telemetry_sync_worker(interval_seconds=120),
                ),  # telemetry worker from telemetry.py
            ]
        )
    worker_configs.extend(
        [
            ("IdleSyncWorker", idle_sync_worker),
            ("BackgroundLoop", background_loop),
        ]
    )

    for name, target_func in worker_configs:
        t = threading.Thread(target=target_func, name=name, daemon=True)
        CRITICAL_WORKERS.append(t)
        t.start()

    # --- CROSS REFERENCE FIX: Handle web server instantiation based on execution context ---
    from waitress import serve

    if blocking:
        print("[SERVER] Starting Waitress web server in blocking mode...")
        serve(app, host="0.0.0.0", port=5000, threads=8)
    else:
        print("[SERVER] Starting Waitress web server asynchronously behind Pygame...")
        web_thread = threading.Thread(
            target=lambda: serve(app, host="0.0.0.0", port=5000, threads=8),
            name="WaitressWebServer",
            daemon=True,
        )
        CRITICAL_WORKERS.append(web_thread)
        web_thread.start()


# APPLICATION SERVER WEB ENDPOINTS
# ==================================================


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/latest")
def latest_page():
    req_id = request.args.get("id")
    if not req_id:
        with mode_lock:
            req_id = ACTIVE_SESSION["session_id"]

    if not req_id:
        return redirect("/")

    return render_template("latest.html", latest_id=req_id)


@app.route("/audio")
def audio_node():
    return render_template("sounds.html")


@app.route("/control")
def control_panel():
    return render_template("control.html")


@app.route("/gallery")
def gallery():
    page = int(request.args.get("page", 1))
    per_page = 12

    try:
        all_files = os.listdir(CAPTURE_DIR)
        modern_files = []
        for f in all_files:
            if f.endswith("_color.jpg"):
                base = f.replace("_color.jpg", "")

                if base.isdigit():
                    modern_files.append(f)

        files = sorted(modern_files, reverse=True)
    except Exception as e:
        print(f"Gallery efficient listing error: {e}")
        files = []

    captures = []
    seen = set()

    for f in files:
        base = f.replace("_color.jpg", "")
        if base in seen:
            continue
        seen.add(base)
        captures.append(
            {"id": base, "bw": f"{base}_bw.jpg", "color": f"{base}_color.jpg"}
        )

    total_pages = max(1, (len(captures) + per_page - 1) // per_page)
    start, end = (page - 1) * per_page, page * per_page
    return render_template(
        "gallery.html", captures=captures[start:end], page=page, total_pages=total_pages
    )


@app.route("/print_capture/<session_id>", methods=["POST"])
def print_capture(session_id):
    if ".." in session_id or "/" in session_id:
        return (
            jsonify({"status": "error", "message": "Invalid session identifier"}),
            400,
        )

    bw_path = os.path.join(CAPTURE_DIR, f"{session_id}_bw.jpg")
    if not os.path.exists(bw_path):
        return (
            jsonify(
                {
                    "status": "error",
                    "message": "Black & White print file not found on disk",
                }
            ),
            404,
        )

    print(f"[MANUAL PRINT] Dispatching manual print thread for key: {session_id}")

    threading.Thread(
        target=print_booth_receipt, args=(session_id,), daemon=True
    ).start()

    return jsonify(
        {
            "status": "success",
            "message": f"Reprint job spawned for session {session_id}",
        }
    )


@app.route("/delete_capture/<session_id>", methods=["POST"])
def delete_capture(session_id):
    if ".." in session_id or "/" in session_id:
        return (
            jsonify({"status": "error", "message": "Invalid session identifier"}),
            400,
        )

    color_path = os.path.join(CAPTURE_DIR, f"{session_id}_color.jpg")
    bw_path = os.path.join(CAPTURE_DIR, f"{session_id}_bw.jpg")
    qr_path = os.path.join(SAVED_DIR, f"{session_id}_qr.png")
    comp_png = os.path.join(CAPTURE_DIR, f"{session_id}_composite.png")

    deleted_any = False
    for path in [color_path, bw_path, qr_path, comp_png]:
        if os.path.exists(path):
            try:
                os.remove(path)
                deleted_any = True
            except Exception as e:
                print(f"Failed to delete file asset {path}: {e}")

    if deleted_any:
        return jsonify(
            {"status": "success", "message": f"Session {session_id} removed cleanly"}
        )
    return (
        jsonify(
            {"status": "error", "message": "No matching session assets found on disk"}
        ),
        404,
    )


@app.route("/video_feed")
def video_feed():
    client_ip = request.remote_addr if request else "Unknown IP"
    user_agent = request.headers.get("User-Agent", "Unknown Browser")

    if not STREAM_SEMAPHORE.acquire(blocking=False):
        print(
            f"[GUARDRAIL] Rejected stream connection from {client_ip} to protect server threads."
        )
        return "Too Many Concurrent Streams", 429

    print(f"[STREAM CONNECT] IP: {client_ip} | Agent: {user_agent}")

    def stream_lifecycle_wrapper():
        try:
            yield from generate_frames()
        finally:
            STREAM_SEMAPHORE.release()
            print(f"[STREAM DISCONNECT] Released streaming slot for IP: {client_ip}")

    response = Response(
        stream_lifecycle_wrapper(), mimetype="multipart/x-mixed-replace; boundary=frame"
    )

    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"

    return response


@app.route("/state")
def state():
    with mode_lock:
        current_mode = ACTIVE_SESSION["mode"]
        current_session_id = ACTIVE_SESSION["session_id"]
        countdown_secs = ACTIVE_SESSION["countdown_remaining"]
        c_start = ACTIVE_SESSION["countdown_start_time"]

    is_flash = (
        current_mode == "countdown" and (time.monotonic() - c_start) < FLASH_DURATION
    )

    return jsonify(
        {
            "knobs": list(GLOBAL_KNOBS),
            "labels": get_knob_labels(),
            "ip": SYSTEM_TELEMETRY["ip"],
            "last_frame_time": last_frame_time,
            "camera_fail_count": camera_fail_count,
            "camera_age": time.time() - last_frame_time,
            "cpu_temp": SYSTEM_TELEMETRY["cpu_temp"],
            "memory_rss_mb": SYSTEM_TELEMETRY["memory_rss_mb"],
            "frame_id": FRAME_ID,
            "upload_status": upload_status_msg,
            "mode": current_mode,
            "is_flash": is_flash,
            "countdown_seconds": countdown_secs,
            "countdown_corner": COUNTDOWN_CORNER,  # Injected for web UI positioning
            "session_id": current_session_id,
            "latest_id": current_session_id,
        }
    )


@app.route("/saved/<filename>")
def saved_files(filename):
    return send_from_directory(SAVED_DIR, filename)


@app.route("/captures/<filename>")
def captures(filename):
    return send_from_directory(CAPTURE_DIR, filename)


@app.route("/shutdown", methods=["POST"])
def remote_shutdown():
    global cam_thread_alive
    cam_thread_alive = False
    client_ip = request.remote_addr if request else "unknown"
    logger.warning("[REMOTE CONTROL] Shutdown requested from %s", client_ip)

    def delayed_poweroff():
        time.sleep(0.5)  # Allow HTTP response to flush back to client first
        subprocess.run(["sudo", "systemctl", "poweroff"], check=False)

    threading.Thread(target=delayed_poweroff, daemon=True).start()

    return jsonify({"status": "shutting_down", "message": "Powering off now..."})


@app.route("/reboot", methods=["POST"])
def remote_reboot():
    global cam_thread_alive
    cam_thread_alive = False
    client_ip = request.remote_addr if request else "unknown"
    logger.warning("[REMOTE CONTROL] Reboot requested from %s", client_ip)

    def delayed_reboot():
        time.sleep(0.5)  # Allow HTTP response to flush back to client first
        subprocess.run(["sudo", "systemctl", "reboot"], check=False)

    threading.Thread(target=delayed_reboot, daemon=True).start()

    return jsonify({"status": "rebooting", "message": "Rebooting system now..."})


@app.route("/service/disable", methods=["POST"])
def remote_service_disable():
    global cam_thread_alive
    cam_thread_alive = False
    client_ip = request.remote_addr if request else "unknown"
    logger.warning(
        "[REMOTE CONTROL] Service disable & reboot requested from %s", client_ip
    )

    def delayed_disable_and_reboot():
        time.sleep(
            0.8
        )  # Allows HTTP response packet to transmit cleanly back to phone/browser
        subprocess.run(
            ["sudo", "systemctl", "disable", "glitchbooth.service"], check=False
        )
        subprocess.run(["sudo", "systemctl", "reboot"], check=False)

    threading.Thread(target=delayed_disable_and_reboot, daemon=True).start()

    return jsonify(
        {
            "status": "disabling_and_rebooting",
            "message": "Kiosk disabled. Rebooting Pi to console login. SSH or manual restart required to restore!",
        }
    )


@app.route("/ping")
def ping():
    return "ok"


@app.route("/clear_session", methods=["POST"])
def clear_session():
    """Allows client-side watchdogs to force the booth back to live streaming mode"""
    with mode_lock:
        if ACTIVE_SESSION["mode"] == "latest":
            ACTIVE_SESSION["mode"] = "live"
            ACTIVE_SESSION["session_id"] = None
            print(
                "[STATE ENGINE] Client-side watchdog timeout. Returning to live stream."
            )
    return jsonify({"status": "success"})


@app.route("/api/stats", methods=["GET"])
def api_live_telemetry():
    """Generates a complete status read directly from current active hardware frameworks."""
    total_captures = 0
    if telemetry.STATS_JSON_PATH.exists():
        try:
            with open(telemetry.STATS_JSON_PATH, "r") as f:
                stats_data = json.load(f)
                total_captures = stats_data.get("total_captures", 0)
        except Exception:
            pass

    uptime_hours = round((time.time() - telemetry.BOOT_TIMESTAMP) / 3600.0, 1)

    camera_status = "OK" if globals().get("cam_thread_alive", True) else "FAULT"

    return jsonify(
        {
            "captures": total_captures,
            "uptime_hours": uptime_hours,
            "online": True,
            "cpu_temp": SYSTEM_TELEMETRY["cpu_temp"],
            "memory_mb": SYSTEM_TELEMETRY["memory_rss_mb"],
            "camera_status": camera_status,
        }
    )


@app.route("/stats", methods=["GET"])
def server_local_stats_page():
    """Renders the compiled structural representation of telemetry.json summary states."""
    if not telemetry.STATS_JSON_PATH.exists():
        return (
            jsonify(
                {
                    "status": "error",
                    "message": "Telemetry matrix collection processing incomplete.",
                }
            ),
            404,
        )

    try:
        with open(telemetry.STATS_JSON_PATH, "r") as f:
            data = json.load(f)
        return jsonify(data)
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


if __name__ == "__main__":
    LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

    logging.basicConfig(
        level=getattr(logging, LOG_LEVEL, logging.INFO),
        format="%(asctime)s %(levelname)s [%(threadName)s] %(name)s: %(message)s",
    )

    # Running independently: block on Waitress natively
    start_backend_engine(blocking=True)
