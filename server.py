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

DATAMOSH_BLOCK_H = 32  # [320x240: 16] [480x360: 24] [640x480: 32]
DATAMOSH_BLOCK_W = 24  # [320x240: 12] [480x360: 18] [640x480: 24]

STATIC_BLOCK_BUFFER = np.empty(
    (DATAMOSH_BLOCK_H, DATAMOSH_BLOCK_W, 3), dtype=np.uint8, order="C"
)

DATAMOSH_START_Y = DATAMOSH_BLOCK_H // 2
DATAMOSH_START_X = DATAMOSH_BLOCK_W // 2
DATAMOSH_STRIDE_Y = DATAMOSH_BLOCK_H
DATAMOSH_STRIDE_X = DATAMOSH_BLOCK_W

GLOBAL_NOISE_BUFFER = np.empty(
    (DATAMOSH_BLOCK_H, DATAMOSH_BLOCK_W, 3), dtype=np.uint8, order="C"
)

GLITCH_BOX_MIN = 16  # [320x240: 8 ] [480x360: 12] [640x480: 16]
GLITCH_BOX_SCALE = 48  # [320x240: 24] [480x360: 36] [640x480: 48]
GLITCH_STRIP_MIN = 8  # [320x240: 4 ] [480x360: 6 ] [640x480: 8 ]
GLITCH_STRIP_SCALE = 24  # [320x240: 12] [480x360: 18] [640x480: 24]

DRIFT_MAX_AMOUNT = 60  # [320x240: 30] [480x360: 45] [640x480: 60]
COMPRESSION_TILE_BASE = 12  # [320x240: 6 ] [480x360: 9 ] [640x480: 12]
COMPRESSION_TILE_SCALE = 9  # [320x240: 3 ] [480x360: 4 ] [640x480: 6 ]

ANA_SHIFT_MAX = 48  # [320x240: 18] [480x360: 27] [640x480: 36]
ANA_GHOST_OFFSET = 40  # [320x240: 20] [480x360: 30] [640x480: 40]
SLICE_MIN_H = 8  # [320x240: 4 ] [480x360: 6 ] [640x480: 8 ]
SLICE_MAX_H = 36  # [320x240: 12] [480x360: 18] [640x480: 24]
SLICE_MAX_SHIFT = 50  # [320x240: 15] [480x360: 23] [640x480: 30]

# MELT DRIP ENGINE CONSTANTS
# ==============================================================================

# PREALLOCATED ARITHMETIC SCRATCH BUFFERS
_RANGE_BUFFER = np.arange(FRAME_H, dtype=np.float32)
_SCRATCH_NORM = np.empty(FRAME_H, dtype=np.float32)
_SCRATCH_DENOM = np.empty(FRAME_H, dtype=np.float32)
_SCRATCH_YFLOAT = np.empty(FRAME_H, dtype=np.float32)
_SCRATCH_FLOOR_FLOAT = np.empty(FRAME_H, dtype=np.float32)
_SCRATCH_YFLOOR = np.empty(FRAME_H, dtype=np.int32)
_SCRATCH_YCEIL = np.empty(FRAME_H, dtype=np.int32)
_SCRATCH_W_CEIL = np.empty((FRAME_H, 1, 1), dtype=np.float32)
_SCRATCH_W_FLOOR = np.empty((FRAME_H, 1, 1), dtype=np.float32)
_SCRATCH_NW_1D = np.empty(FRAME_H, dtype=np.float32)
_SCRATCH_NEON_WEIGHT = np.empty((FRAME_H, 1, 1), dtype=np.float32)

## ABOVE ARE ADDED FOR THE PREALLOCATED BUFFERS VERSION OF MELT FOR TESTING

# Vectorized Melt renderer: persistent OpenCV remap buffers.
# Allocated once; reused on every frame.

MELT_MAP_X = np.empty((FRAME_H, FRAME_W), dtype=np.float32)
MELT_MAP_Y = np.empty((FRAME_H, FRAME_W), dtype=np.float32)

MELT_REMAP_BUFFER = np.empty((FRAME_H, FRAME_W, 3), dtype=np.uint8)

MELT_ALPHA_MAP = np.zeros((FRAME_H, FRAME_W), dtype=np.float32)

MELT_COLOR_MAP = np.zeros((FRAME_H, FRAME_W, 3), dtype=np.float32)

MELT_REMAP_FLOAT = np.empty((FRAME_H, FRAME_W, 3), dtype=np.float32)

MELT_BLEND_TEMP = np.empty((FRAME_H, FRAME_W, 3), dtype=np.float32)

MELT_BLEND_ALPHA = np.empty((FRAME_H, FRAME_W, 3), dtype=np.float32)

_MELT_IDENTITY_X = np.broadcast_to(
    np.arange(FRAME_W, dtype=np.float32)[None, :],
    (FRAME_H, FRAME_W),
).copy()

_MELT_IDENTITY_Y = np.broadcast_to(
    np.arange(FRAME_H, dtype=np.float32)[:, None],
    (FRAME_H, FRAME_W),
).copy()

## ABOVE ARE ADDED FOR THE VECTORIZED VERSION OF MELT FOR TESTING

MELT_STRIDE_LOW = 6
MELT_STRIDE_MID = 3
MELT_STRIDE_HIGH = 2  # Safe min stride for maintaining high FPS on Pi 5 - 1 GB is 2
MAX_MELT_DRIPS = 20  # Balanced aesthetic ceiling 100
MELT_DRIP_WIDTH = 20  # Thick, substantial drips when the number is bigger. 75 drips and 10 width, 100 drips and 8 width are good combos
MELT_NEON_START_PCT = 0.75  # Where neon begins: 0.0 = top of drip, 0.5 = halfway down
MELT_NEON_RAMP_SPAN = (
    0.20  # Length of the transition ramp to full neon (Start + Span should equal 1.0)
)


# NOTE: Ensure your global buffer allocation matches MELT_DRIP_WIDTH!
# e.g., MELT_DRIP_WORK_BUFFER = np.zeros((max_possible_height, MELT_DRIP_WIDTH, 3), dtype=np.float32)

# NEON_PALETTES defined in native OpenCV BGR format: [Blue, Green, Red]
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

# MELT_DRIP_WORK_BUFFER = np.empty((FRAME_H, 4, 3), dtype=np.float32, order="C")
MELT_DRIP_WORK_BUFFER = np.zeros((FRAME_H, MELT_DRIP_WIDTH, 3), dtype=np.float32)

assert STATIC_MELT_SNAPSHOT.flags["C_CONTIGUOUS"], (
    "CRITICAL: STATIC_MELT_SNAPSHOT layout fault!"
)
assert MELT_DRIP_WORK_BUFFER.flags["C_CONTIGUOUS"], (
    "CRITICAL: MELT_DRIP_WORK_BUFFER layout fault!"
)

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


# ISOLATED DATAMOSH TEMPORAL MEMORY REGISTERS (ZERO RUNTIME HEAP CHURN)
# =========================================================================
WARMUP_FRAMES = 10
WARMUP_COUNTER = 0
LAST_TEMPORAL_PURGE = time.time()

GRID_H = FRAME_H // DATAMOSH_BLOCK_H
GRID_W = FRAME_W // DATAMOSH_BLOCK_W

PREV_FRAME_BUFFER = np.empty((FRAME_H, FRAME_W, 3), dtype=np.uint8, order="C")
DATAMOSH_PREV_FRAME = np.empty((FRAME_H, FRAME_W, 3), dtype=np.uint8, order="C")
SHARED_GRAY_BUFFER = np.empty((FRAME_H, FRAME_W), dtype=np.uint8, order="C")

DATAMOSH_WORK_BUFFER = np.zeros((FRAME_H, FRAME_W, 3), dtype=np.uint8, order="C")
DATAMOSH_DIFF_BUFFER = np.zeros((FRAME_H, FRAME_W, 3), dtype=np.uint8, order="C")
DATAMOSH_GRAY_DIFF = np.zeros((FRAME_H, FRAME_W), dtype=np.uint8, order="C")

DATAMOSH_VX = np.zeros((GRID_H, GRID_W), dtype=np.int8, order="C")

CORRUPTION_ENERGY = np.zeros((GRID_H, GRID_W), dtype=np.float32, order="C")

CORRUPTION_MODE = np.zeros((GRID_H, GRID_W), dtype=np.uint8, order="C")

DATAMOSH_CYCLE_COUNTER = 0
RANDOM_SIGN_POOL = np.where(np.random.rand(4096) > 0.5, 4, -4).astype(np.int8)
RNG_PTR_SIGN = 0

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

# PATH B STABLE GLITCH STORAGE (ZERO RUNTIME HEAP CHURN)
# =========================================================================
MAX_GLITCHES = 8

glitch_active = np.zeros(MAX_GLITCHES, dtype=bool)
glitch_type = np.zeros(MAX_GLITCHES, dtype=np.uint8)
glitch_x = np.zeros(MAX_GLITCHES, dtype=np.int16)
glitch_y = np.zeros(MAX_GLITCHES, dtype=np.int16)
glitch_size = np.zeros(MAX_GLITCHES, dtype=np.int16)
glitch_life = np.zeros(MAX_GLITCHES, dtype=np.int16)
glitch_max = np.zeros(MAX_GLITCHES, dtype=np.int16)

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
cam_raw_lock = threading.Lock()

printer_hardware_lock = threading.Lock()
cache_file_lock = threading.Lock()

SHUTDOWN_EVENT = threading.Event()
CRITICAL_WORKERS = []

# 1. Initialize telemetry and inject the global event object
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


# ISOLATED DATAMOSH ENGINE
# =========================================================================


def apply_datamosh(effect_val, current_time):
    global DATAMOSH_CYCLE_COUNTER, RNG_PTR_SIGN, WARMUP_COUNTER

    if WARMUP_COUNTER < WARMUP_FRAMES:
        DATAMOSH_PREV_FRAME[:] = MUTABLE_PROCESSING_BUFFER
        DATAMOSH_VX[:] = 0
        return

    BLOCK_X = FRAME_W // GRID_W
    BLOCK_Y = FRAME_H // GRID_H

    if effect_val < 0.02:
        DATAMOSH_VX[:] = 0
        cv2.addWeighted(
            MUTABLE_PROCESSING_BUFFER,
            0.8,  # 80% clean active frame
            DATAMOSH_PREV_FRAME,
            0.2,  # 20% residual history
            0,
            dst=DATAMOSH_PREV_FRAME,
        )
        return

    DATAMOSH_CYCLE_COUNTER += 1
    vx = DATAMOSH_VX

    if DATAMOSH_CYCLE_COUNTER & 1:
        cv2.absdiff(
            MUTABLE_PROCESSING_BUFFER, DATAMOSH_PREV_FRAME, dst=DATAMOSH_DIFF_BUFFER
        )
        cv2.cvtColor(DATAMOSH_DIFF_BUFFER, cv2.COLOR_BGR2GRAY, dst=DATAMOSH_GRAY_DIFF)

        motion_thresh = int(18 + (1.0 - effect_val) * 40)
        center_diffs = DATAMOSH_GRAY_DIFF[
            DATAMOSH_START_Y::DATAMOSH_STRIDE_Y, DATAMOSH_START_X::DATAMOSH_STRIDE_X
        ][:GRID_H, :GRID_W]
        motion_mask = center_diffs > motion_thresh
        new_motion_trigger = (vx == 0) & motion_mask

        if np.any(new_motion_trigger):
            ys, xs = np.nonzero(new_motion_trigger)
            count = len(ys)
            pool_len = len(RANDOM_SIGN_POOL)
            for idx in range(count):
                vx[ys[idx], xs[idx]] = RANDOM_SIGN_POOL[(RNG_PTR_SIGN + idx) % pool_len]
            RNG_PTR_SIGN = (RNG_PTR_SIGN + count) % pool_len

        decay_eligible = ~motion_mask
        np.subtract(vx, 1, out=vx, where=(vx > 0) & decay_eligible)
        np.add(vx, 1, out=vx, where=(vx < 0) & decay_eligible)

    DATAMOSH_WORK_BUFFER[:] = MUTABLE_PROCESSING_BUFFER
    active_y, active_x = np.nonzero(vx)

    for gy, gx in zip(active_y, active_x):
        block_v = vx[gy, gx]
        by = gy * BLOCK_Y
        bx = gx * BLOCK_X
        shift_bx = max(0, min(FRAME_W - BLOCK_X, bx + block_v))
        target_block = DATAMOSH_WORK_BUFFER[by : by + BLOCK_Y, bx : bx + BLOCK_X]
        history_source = DATAMOSH_PREV_FRAME[
            by : by + BLOCK_Y, shift_bx : shift_bx + BLOCK_X
        ]
        history_dest = DATAMOSH_PREV_FRAME[by : by + BLOCK_Y, bx : bx + BLOCK_X]
        erosion_seed = gy ^ gx ^ DATAMOSH_CYCLE_COUNTER
        top_e = erosion_seed % 3 if (erosion_seed & 1) else 0
        bottom_e = (erosion_seed >> 1) % 3 if (erosion_seed & 2) else 0
        left_e = (erosion_seed >> 2) % 3 if (erosion_seed & 4) else 0
        right_e = (erosion_seed >> 3) % 3 if (erosion_seed & 8) else 0
        target_interior = target_block[
            top_e : BLOCK_Y - bottom_e, left_e : BLOCK_X - right_e
        ]
        source_interior = history_source[
            top_e : BLOCK_Y - bottom_e, left_e : BLOCK_X - right_e
        ]
        target_interior[:] = source_interior
        history_dest[:] = (
            (target_block.astype(np.uint16) * 161 + history_dest.astype(np.uint16) * 95)
            >> 8
        ).astype(np.uint8)

    MUTABLE_PROCESSING_BUFFER[:] = DATAMOSH_WORK_BUFFER


# =========================================================================
# ISOLATED GRAVITATIONAL MELT ENGINE (STAGE 6)
# =========================================================================

# original numpy-based melt code
'''
def _collect_melt_drips(
    melt_raw: float, current_time: float, h: int = FRAME_H, w: int = FRAME_W
):
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
    g = STATIC_MELT_GRAY_BUFFER
    col_seeds = []
    for x0 in range(1, w - 2, stride):
        ys = np.flatnonzero(g[:, x0] > threshold)
        if ys.size > 0:
            seeds = [
                int(v) for v in ys[::seed_step][:max_seeds_per_column] if v < h - 4
            ]
            if seeds:
                col_seeds.append((x0, seeds))
    drips = []
    for rank in range(max_seeds_per_column):
        eligible = [(x0, seeds[rank]) for x0, seeds in col_seeds if rank < len(seeds)]
        remaining = MAX_MELT_DRIPS - len(drips)
        if len(eligible) > remaining:
            step = len(eligible) / float(remaining)
            eligible = [eligible[int(i * step)] for i in range(remaining)]
        for x0, start_y in eligible:
            h1 = (x0 * 73856093) ^ (start_y * 19349663) ^ (time_tick * 83492791)
            h2 = (x0 * 2654435761) ^ (start_y * 2246822519) ^ (time_tick * 3266489917)
            rand_len_mult = (h1 % 1000) / 1000.0
            rand_stretch_mult = ((h1 >> 10) % 1000) / 1000.0
            p1 = (h2 >> 4) % len(NEON_PALETTES)
            p2 = (p1 + 1 + ((h2 >> 12) % (len(NEON_PALETTES) - 1))) % len(NEON_PALETTES)
            base_len = int(max_melt_length * 0.45)
            rand_len = int(max_melt_length * 0.55 * rand_len_mult)
            L = base_len + rand_len
            if L <= 0:
                continue
            s = 1.0 + (rand_stretch_mult * 2.0)
            reach = start_y + L
            drips.append((reach, x0, start_y, L, s, p1, p2))
        if len(drips) >= MAX_MELT_DRIPS:
            break
    return drips


def _render_melt_drips(drips, h: int = FRAME_H, w: int = FRAME_W):
    if not drips:
        return
    sorted_drips = sorted(drips, key=lambda item: (item[0], item[1]))

    left_offset = MELT_DRIP_WIDTH // 2
    right_offset = MELT_DRIP_WIDTH - left_offset

    for _, x0_raw, y0_raw, L_raw, s, p1, p2 in sorted_drips:
        x0 = int(x0_raw)
        y0 = int(y0_raw)
        L = int(L_raw)

        if x0 - left_offset < 0 or x0 + right_offset > w:
            continue
        if L <= 0 or y0 >= h - 1:
            continue

        end_y = min(h, y0 + L)
        num_rows = end_y - y0
        if num_rows <= 0:
            continue

        y_dest = np.arange(y0, end_y, dtype=np.int32)
        y_rel = (y_dest - y0).astype(np.float32)
        norm_dist = np.clip(y_rel / float(L), 0.0, 1.0)
        denom = np.maximum(1.0, 1.0 + s * np.sqrt(norm_dist))
        y_float = np.clip(y0 + (y_rel / denom), 0.0, float(h - 1))

        y_floor = np.floor(y_float).astype(np.int32)
        y_ceil = np.clip(y_floor + 1, 0, h - 1)
        w_ceil = (y_float - y_floor).astype(np.float32)[:, None, None]
        w_floor = 1.0 - w_ceil

        cols_slice = slice(x0 - left_offset, x0 + right_offset)
        src_floor = STATIC_MELT_SNAPSHOT[y_floor, cols_slice].astype(np.float32)
        src_ceil = STATIC_MELT_SNAPSHOT[y_ceil, cols_slice].astype(np.float32)

        # Warped source image pixels
        src_warped = w_floor * src_floor + w_ceil * src_ceil

        # GRADIENT NEON BLEND: 0% neon for first 25% of drip length, ramping linearly to 100% full neon at tip
        neon_weight = np.clip((norm_dist - 0.25) / 0.75, 0.0, 1.0)[:, None, None]
        neon_color = 0.5 * NEON_PALETTES_FLOAT[p1] + 0.5 * NEON_PALETTES_FLOAT[p2]

        work_view = MELT_DRIP_WORK_BUFFER[:num_rows, :MELT_DRIP_WIDTH]
        work_view[:] = (1.0 - neon_weight) * src_warped + neon_weight * neon_color

        MUTABLE_PROCESSING_BUFFER[y0:end_y, cols_slice] = np.clip(
            np.round(work_view), 0, 255
        ).astype(np.uint8)


def apply_melt_pass(melt_raw: float, current_time: float):
    """Stage 6: Gravitational Melt Deformation Public Entry Point."""
    if melt_raw <= 0.03:
        return
    STATIC_MELT_SNAPSHOT[:] = MUTABLE_PROCESSING_BUFFER[:]
    cv2.cvtColor(STATIC_MELT_SNAPSHOT, cv2.COLOR_BGR2GRAY, dst=STATIC_MELT_GRAY_BUFFER)
    drips = _collect_melt_drips(melt_raw, current_time)
    if drips:
        _render_melt_drips(drips)

'''


def _collect_melt_drips(
    melt_raw: float, current_time: float, h: int = FRAME_H, w: int = FRAME_W
):
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
    g = STATIC_MELT_GRAY_BUFFER
    col_seeds = []
    for x0 in range(1, w - 2, stride):
        ys = np.flatnonzero(g[:, x0] > threshold)
        if ys.size > 0:
            seeds = [
                int(v) for v in ys[::seed_step][:max_seeds_per_column] if v < h - 4
            ]
            if seeds:
                col_seeds.append((x0, seeds))
    drips = []
    for rank in range(max_seeds_per_column):
        eligible = [(x0, seeds[rank]) for x0, seeds in col_seeds if rank < len(seeds)]
        remaining = MAX_MELT_DRIPS - len(drips)

        # DEFENSIVE GUARD: Prevent division by zero if cap is met or exceeded
        if remaining <= 0:
            break

        if len(eligible) > remaining:
            step = len(eligible) / float(remaining)
            eligible = [eligible[int(i * step)] for i in range(remaining)]

        for x0, start_y in eligible:
            h1 = (x0 * 73856093) ^ (start_y * 19349663) ^ (time_tick * 83492791)
            h2 = (x0 * 2654435761) ^ (start_y * 2246822519) ^ (time_tick * 3266489917)
            rand_len_mult = (h1 % 1000) / 1000.0
            rand_stretch_mult = ((h1 >> 10) % 1000) / 1000.0
            p1 = (h2 >> 4) % len(NEON_PALETTES)
            p2 = (p1 + 1 + ((h2 >> 12) % (len(NEON_PALETTES) - 1))) % len(NEON_PALETTES)
            base_len = int(max_melt_length * 0.45)
            rand_len = int(max_melt_length * 0.55 * rand_len_mult)
            L = base_len + rand_len
            if L <= 0:
                continue
            s = 1.0 + (rand_stretch_mult * 2.0)
            reach = start_y + L
            drips.append((reach, x0, start_y, L, s, p1, p2))
        if len(drips) >= MAX_MELT_DRIPS:
            break
    return drips


### non-vectorized version with non-localized buffers
'''
def _render_melt_drips(drips, h: int = FRAME_H, w: int = FRAME_W):
    if not drips:
        return
    sorted_drips = sorted(drips, key=lambda item: (item[0], item[1]))

    left_offset = MELT_DRIP_WIDTH // 2
    right_offset = MELT_DRIP_WIDTH - left_offset

    for _, x0_raw, y0_raw, L_raw, s, p1, p2 in sorted_drips:
        x0 = int(x0_raw)
        y0 = int(y0_raw)
        L = int(L_raw)

        if x0 - left_offset < 0 or x0 + right_offset > w:
            continue
        if L <= 0 or y0 >= h - 1:
            continue

        end_y = min(h, y0 + L)
        num_rows = end_y - y0
        if num_rows <= 0:
            continue

        # Gravitational mapping
        y_rel = _RANGE_BUFFER[:num_rows]
        norm_dist = _SCRATCH_NORM[:num_rows]
        np.clip(y_rel / float(L), 0.0, 1.0, out=norm_dist)

        denom = _SCRATCH_DENOM[:num_rows]
        np.sqrt(norm_dist, out=denom)
        denom *= s
        denom += 1.0
        np.maximum(1.0, denom, out=denom)

        y_float = _SCRATCH_YFLOAT[:num_rows]
        np.divide(y_rel, denom, out=y_float)
        y_float += y0
        np.clip(y_float, 0.0, float(h - 1), out=y_float)

        # Floor extraction using separate scratch buffer to preserve y_float fractions
        y_floor_float = _SCRATCH_FLOOR_FLOAT[:num_rows]
        np.floor(y_float, out=y_floor_float)

        y_floor = _SCRATCH_YFLOOR[:num_rows]
        y_floor[:] = y_floor_float

        y_ceil = _SCRATCH_YCEIL[:num_rows]
        np.clip(y_floor + 1, 0, h - 1, out=y_ceil)

        # Bilinear interpolation weights
        w_ceil = _SCRATCH_W_CEIL[:num_rows]
        np.subtract(y_float, y_floor_float, out=w_ceil[:, 0, 0])

        w_floor = _SCRATCH_W_FLOOR[:num_rows]
        np.subtract(1.0, w_ceil[:, 0, 0], out=w_floor[:, 0, 0])

        cols_slice = slice(x0 - left_offset, x0 + right_offset)
        src_floor = STATIC_MELT_SNAPSHOT[y_floor, cols_slice].astype(np.float32)
        src_ceil = STATIC_MELT_SNAPSHOT[y_ceil, cols_slice].astype(np.float32)

        work_view = MELT_DRIP_WORK_BUFFER[:num_rows, :MELT_DRIP_WIDTH]
        np.multiply(w_floor, src_floor, out=work_view)
        work_view += w_ceil * src_ceil

        """
        # Gradient neon blend (0% at top 25%, ramping linearly to 100% at tip)
        nw_1d = _SCRATCH_NW_1D[:num_rows]
        np.subtract(norm_dist, 0.25, out=nw_1d)
        nw_1d /= 0.75
        np.clip(nw_1d, 0.0, 1.0, out=nw_1d)
        """

        # Tunable gradient neon blend using top-level constants
        nw_1d = _SCRATCH_NW_1D[:num_rows]
        np.subtract(norm_dist, MELT_NEON_START_PCT, out=nw_1d)
        nw_1d /= MELT_NEON_RAMP_SPAN
        np.clip(nw_1d, 0.0, 1.0, out=nw_1d)

        nw_view = _SCRATCH_NEON_WEIGHT[:num_rows]
        nw_view[:, 0, 0] = nw_1d

        neon_color = 0.5 * NEON_PALETTES_FLOAT[p1] + 0.5 * NEON_PALETTES_FLOAT[p2]

        work_view *= 1.0 - nw_view
        work_view += nw_view * neon_color

        MUTABLE_PROCESSING_BUFFER[y0:end_y, cols_slice] = np.clip(
            np.round(work_view), 0, 255
        ).astype(np.uint8)
'''

### non-vectorized version with localized buffers
"""
def _collect_melt_drips(
    melt_raw: float, current_time: float, h: int = FRAME_H, w: int = FRAME_W
):
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
    g = STATIC_MELT_GRAY_BUFFER
    col_seeds = []
    for x0 in range(1, w - 2, stride):
        ys = np.flatnonzero(g[:, x0] > threshold)
        if ys.size > 0:
            seeds = [
                int(v) for v in ys[::seed_step][:max_seeds_per_column] if v < h - 4
            ]
            if seeds:
                col_seeds.append((x0, seeds))
    drips = []
    for rank in range(max_seeds_per_column):
        eligible = [(x0, seeds[rank]) for x0, seeds in col_seeds if rank < len(seeds)]
        remaining = MAX_MELT_DRIPS - len(drips)
        
        if remaining <= 0:
            break
            
        if len(eligible) > remaining:
            step = len(eligible) / float(remaining)
            eligible = [eligible[int(i * step)] for i in range(remaining)]
            
        for x0, start_y in eligible:
            h1 = (x0 * 73856093) ^ (start_y * 19349663) ^ (time_tick * 83492791)
            h2 = (x0 * 2654435761) ^ (start_y * 2246822519) ^ (time_tick * 3266489917)
            rand_len_mult = (h1 % 1000) / 1000.0
            rand_stretch_mult = ((h1 >> 10) % 1000) / 1000.0
            p1 = (h2 >> 4) % len(NEON_PALETTES)
            p2 = (p1 + 1 + ((h2 >> 12) % (len(NEON_PALETTES) - 1))) % len(NEON_PALETTES)
            base_len = int(max_melt_length * 0.45)
            rand_len = int(max_melt_length * 0.55 * rand_len_mult)
            L = base_len + rand_len
            if L <= 0:
                continue
            s = 1.0 + (rand_stretch_mult * 2.0)
            reach = start_y + L
            drips.append((reach, x0, start_y, L, s, p1, p2))
        if len(drips) >= MAX_MELT_DRIPS:
            break
    return drips

def _render_melt_drips(drips, h: int = FRAME_H, w: int = FRAME_W):
    if not drips:
        return
    sorted_drips = sorted(drips, key=lambda item: (item[0], item[1]))
    
    left_offset = MELT_DRIP_WIDTH // 2
    right_offset = MELT_DRIP_WIDTH - left_offset

    # Localize globals for speed inside the active drip loop
    _snapshot = STATIC_MELT_SNAPSHOT
    _work_buf = MELT_DRIP_WORK_BUFFER
    _processing_buf = MUTABLE_PROCESSING_BUFFER
    _palettes_f = NEON_PALETTES_FLOAT
    _range_buf = _RANGE_BUFFER
    _scratch_norm = _SCRATCH_NORM
    _scratch_denom = _SCRATCH_DENOM
    _scratch_yfloat = _SCRATCH_YFLOAT
    _scratch_floor = _SCRATCH_FLOOR_FLOAT
    _scratch_yfloor = _SCRATCH_YFLOOR
    _scratch_yceil = _SCRATCH_YCEIL
    _scratch_w_ceil = _SCRATCH_W_CEIL
    _scratch_w_floor = _SCRATCH_W_FLOOR
    _scratch_nw_1d = _SCRATCH_NW_1D
    _scratch_nw_weight = _SCRATCH_NEON_WEIGHT

    for _, x0_raw, y0_raw, L_raw, s, p1, p2 in sorted_drips:
        x0 = int(x0_raw)
        y0 = int(y0_raw)
        L = int(L_raw)
        
        if x0 - left_offset < 0 or x0 + right_offset > w:
            continue
        if L <= 0 or y0 >= h - 1:
            continue
            
        end_y = min(h, y0 + L)
        num_rows = end_y - y0
        if num_rows <= 0:
            continue
            
        # Gravitational mapping
        y_rel = _range_buf[:num_rows]
        norm_dist = _scratch_norm[:num_rows]
        np.clip(y_rel / float(L), 0.0, 1.0, out=norm_dist)
        
        denom = _scratch_denom[:num_rows]
        np.sqrt(norm_dist, out=denom)
        denom *= s
        denom += 1.0
        np.maximum(1.0, denom, out=denom)
        
        y_float = _scratch_yfloat[:num_rows]
        np.divide(y_rel, denom, out=y_float)
        y_float += y0
        np.clip(y_float, 0.0, float(h - 1), out=y_float)
        
        y_floor_float = _scratch_floor[:num_rows]
        np.floor(y_float, out=y_floor_float)
        
        y_floor = _scratch_yfloor[:num_rows]
        y_floor[:] = y_floor_float
        
        y_ceil = _scratch_yceil[:num_rows]
        np.clip(y_floor + 1, 0, h - 1, out=y_ceil)
        
        w_ceil = _scratch_w_ceil[:num_rows]
        np.subtract(y_float, y_floor_float, out=w_ceil[:, 0, 0])
        
        w_floor = _scratch_w_floor[:num_rows]
        np.subtract(1.0, w_ceil[:, 0, 0], out=w_floor[:, 0, 0])
        
        cols_slice = slice(x0 - left_offset, x0 + right_offset)
        src_floor = _snapshot[y_floor, cols_slice].astype(np.float32)
        src_ceil = _snapshot[y_ceil, cols_slice].astype(np.float32)
        
        work_view = _work_buf[:num_rows, :MELT_DRIP_WIDTH]
        np.multiply(w_floor, src_floor, out=work_view)
        work_view += w_ceil * src_ceil
        
        # Tunable gradient neon blend
        nw_1d = _scratch_nw_1d[:num_rows]
        np.subtract(norm_dist, MELT_NEON_START_PCT, out=nw_1d)
        nw_1d /= MELT_NEON_RAMP_SPAN
        np.clip(nw_1d, 0.0, 1.0, out=nw_1d)
        
        nw_view = _scratch_nw_weight[:num_rows]
        nw_view[:, 0, 0] = nw_1d
        
        neon_color = (0.5 * _palettes_f[p1] + 0.5 * _palettes_f[p2])
        
        work_view *= (1.0 - nw_view)
        work_view += nw_view * neon_color
        
        _processing_buf[y0:end_y, cols_slice] = np.clip(
            np.round(work_view), 0, 255
        ).astype(np.uint8)

"""


def _render_melt_drips(drips, h=FRAME_H, w=FRAME_W):
    """
    Vectorized gravitational Melt renderer.

    Builds a single source-coordinate map for all drips, then performs
    one OpenCV remap and a vectorized neon blend.

    Preserves the existing sorted drip order: later drips overwrite
    the mapping and blend parameters of earlier overlapping drips.
    """
    if not drips:
        return

    # The persistent buffers are sized for the configured frame.
    # This renderer expects the standard FRAME_H x FRAME_W pipeline.
    if h != FRAME_H or w != FRAME_W:
        raise ValueError("Melt remap buffers must match FRAME_H and FRAME_W")

    # Identity mapping: untouched pixels sample their original position.
    np.copyto(MELT_MAP_X, _MELT_IDENTITY_X)
    np.copyto(MELT_MAP_Y, _MELT_IDENTITY_Y)

    MELT_ALPHA_MAP.fill(0.0)
    MELT_COLOR_MAP.fill(0.0)

    left_offset = MELT_DRIP_WIDTH // 2
    right_offset = MELT_DRIP_WIDTH - left_offset

    # Match the original renderer's overwrite order.
    sorted_drips = sorted(drips, key=lambda item: (item[0], item[1]))

    for _, x0_raw, y0_raw, L_raw, s, p1, p2 in sorted_drips:
        x0 = int(x0_raw)
        y0 = int(y0_raw)
        L = int(L_raw)

        if x0 - left_offset < 0 or x0 + right_offset > w:
            continue
        if L <= 0 or y0 >= h - 1:
            continue

        end_y = min(h, y0 + L)
        num_rows = end_y - y0
        if num_rows <= 0:
            continue

        # Gravitational source-coordinate mapping.
        y_rel = np.arange(num_rows, dtype=np.float32)
        norm_dist = np.clip(y_rel / float(L), 0.0, 1.0)

        denom = np.maximum(
            1.0 + s * np.sqrt(norm_dist),
            1.0,
        )

        y_float = np.clip(
            y0 + y_rel / denom,
            0.0,
            float(h - 1),
        )

        # The same source Y coordinates are used across the drip width.
        cols_slice = slice(x0 - left_offset, x0 + right_offset)

        MELT_MAP_Y[y0:end_y, cols_slice] = y_float[:, None]

        # Match the current configurable neon ramp.
        neon_weight = np.clip(
            (norm_dist - MELT_NEON_START_PCT) / MELT_NEON_RAMP_SPAN,
            0.0,
            1.0,
        )

        neon_color = 0.5 * NEON_PALETTES_FLOAT[p1] + 0.5 * NEON_PALETTES_FLOAT[p2]

        MELT_ALPHA_MAP[y0:end_y, cols_slice] = neon_weight[:, None]
        MELT_COLOR_MAP[y0:end_y, cols_slice] = neon_color

    # Warp the immutable original snapshot once for the entire frame.
    cv2.remap(
        STATIC_MELT_SNAPSHOT,
        MELT_MAP_X,
        MELT_MAP_Y,
        interpolation=cv2.INTER_LINEAR,
        dst=MELT_REMAP_BUFFER,
        borderMode=cv2.BORDER_REPLICATE,
    )

    # Expand the alpha map to three channels without allocating a
    # repeated NumPy array.
    for channel in range(3):
        MELT_BLEND_ALPHA[:, :, channel] = MELT_ALPHA_MAP

    # Blend the warped pixels with the per-pixel neon color.
    np.subtract(
        1.0,
        MELT_BLEND_ALPHA,
        out=MELT_BLEND_TEMP,
    )

    np.multiply(
        MELT_REMAP_BUFFER,
        MELT_BLEND_TEMP,
        out=MELT_REMAP_FLOAT,
        casting="unsafe",
    )

    np.multiply(
        MELT_COLOR_MAP,
        MELT_BLEND_ALPHA,
        out=MELT_BLEND_TEMP,
    )

    np.add(
        MELT_REMAP_FLOAT,
        MELT_BLEND_TEMP,
        out=MELT_REMAP_FLOAT,
    )

    np.clip(
        MELT_REMAP_FLOAT,
        0.0,
        255.0,
        out=MELT_REMAP_FLOAT,
    )

    np.copyto(
        MUTABLE_PROCESSING_BUFFER,
        MELT_REMAP_FLOAT,
        casting="unsafe",
    )


# non-diagnostic version
'''
def apply_melt_pass(melt_raw: float, current_time: float):
    """Stage 6: Gravitational Melt Deformation Public Entry Point."""
    if melt_raw <= 0.03:
        return
    STATIC_MELT_SNAPSHOT[:] = MUTABLE_PROCESSING_BUFFER[:]
    cv2.cvtColor(STATIC_MELT_SNAPSHOT, cv2.COLOR_BGR2GRAY, dst=STATIC_MELT_GRAY_BUFFER)
    drips = _collect_melt_drips(melt_raw, current_time)
    if drips:
        _render_melt_drips(drips)
'''


# diagnostic version
def apply_melt_pass(melt_raw: float, current_time: float):
    """Stage 6: Gravitational Melt Deformation Public Entry Point with Profiling."""
    if melt_raw <= 0.03:
        return

    # Phase 1: Snapshot and Grayscale conversion
    t0 = time.perf_counter()
    STATIC_MELT_SNAPSHOT[:] = MUTABLE_PROCESSING_BUFFER[:]
    cv2.cvtColor(STATIC_MELT_SNAPSHOT, cv2.COLOR_BGR2GRAY, dst=STATIC_MELT_GRAY_BUFFER)
    t1 = time.perf_counter()

    # Phase 2: Drip Collection (column scanning & filtering)
    drips = _collect_melt_drips(melt_raw, current_time)
    t2 = time.perf_counter()

    # Phase 3: Rendering (Remap / pixel warping & neon blending)
    if drips:
        _render_melt_drips(drips)
    t3 = time.perf_counter()

    # Print granular timings (converting seconds to milliseconds)
    snapshot_ms = (t1 - t0) * 1000.0
    collect_ms = (t2 - t1) * 1000.0
    render_ms = (t3 - t2) * 1000.0
    total_ms = (t3 - t0) * 1000.0

    print(
        f"[MELT PROFILE] Total: {total_ms:.2f}ms | "
        f"Snap: {snapshot_ms:.2f}ms | "
        f"Collect ({len(drips)} drips): {collect_ms:.2f}ms | "
        f"Render: {render_ms:.2f}ms"
    )


# RADICAL COMPUTATION UNROLLED ENGINE
# ==================================================


def n(val):
    return val / 1023.0


def apply_glitch_pipeline(current_time, control_packet):
    global WARMUP_COUNTER, LAST_TEMPORAL_PURGE, CORRUPTION_ENERGY, CORRUPTION_MODE
    global \
        ASCII_LOWRES_GRAY, \
        ASCII_UPRES_GRAY, \
        ASCII_BLOCK_BUFFER, \
        ASCII_COLOR_BUFFER, \
        ASCII_GREEN_BUFFER, \
        ASCII_NOISE_LOW, \
        ASCII_NOISE_UP
    global SPARK_COUNTER, SPARK_INTERVAL
    h, w = FRAME_H, FRAME_W
    pkt_knobs = control_packet.knobs
    is_warming_up = WARMUP_COUNTER < WARMUP_FRAMES

    # DIGITAL CORRUPTION GRID STATE EVALUATION ENGINE (DRIVEN BY KNOB 2)
    # =========================================================================
    knob2_val = n(pkt_knobs[2])

    if knob2_val < 0.01:
        CORRUPTION_ENERGY[:] = 0.0
        CORRUPTION_MODE[:] = 0

    if not is_warming_up:
        CORRUPTION_ENERGY[DATAMOSH_VX != 0] += 0.1

    if knob2_val > 0.02 and np.random.rand() < (knob2_val * 0.33):
        if np.random.rand() < 0.80:
            gy = np.random.randint(0, GRID_H)
            gx = np.random.randint(0, GRID_W)

            block_size_y = np.random.randint(1, 4)
            block_size_x = np.random.randint(1, 4)

            gy_end = min(GRID_H, gy + block_size_y)
            gx_end = min(GRID_W, gx + block_size_x)

            CORRUPTION_ENERGY[gy:gy_end, gx:gx_end] = 1.0
            CORRUPTION_MODE[gy:gy_end, gx:gx_end] = 1

        else:
            col_width = 1 if np.random.rand() < 0.9 else 2
            gx = np.random.randint(0, max(1, GRID_W - col_width))
            gx_end = gx + col_width

            col_roll = np.random.rand()
            if col_roll < 0.60:
                mode_selection = 2  # Noise Column
            elif col_roll < 0.90:
                mode_selection = 3  # Black Column
            else:
                mode_selection = 4  # White Column

            CORRUPTION_ENERGY[:, gx:gx_end] = 1.0
            CORRUPTION_MODE[:, gx:gx_end] = mode_selection

    CORRUPTION_ENERGY *= 0.9
    np.clip(CORRUPTION_ENERGY, 0.0, 1.0, out=CORRUPTION_ENERGY)

    CORRUPTION_MODE[CORRUPTION_ENERGY <= 0.005] = 0

    # SPATIAL METADATA RENDERING ENGINE (DRIVEN BY KNOB 2)
    # =========================================================================
    if np.any(CORRUPTION_MODE):
        active_gy, active_gx = np.nonzero(CORRUPTION_MODE)
        bh = DATAMOSH_BLOCK_H
        bw = DATAMOSH_BLOCK_W

        if np.any((CORRUPTION_MODE == 1) | (CORRUPTION_MODE == 2)):
            cv2.randu(GLOBAL_NOISE_BUFFER, (0, 0, 0), (255, 255, 255))

        for gy, gx in zip(active_gy, active_gx):
            y1, y2 = gy * bh, (gy + 1) * bh
            x1, x2 = gx * bw, (gx + 1) * bw

            energy = float(CORRUPTION_ENERGY[gy, gx])
            mode = CORRUPTION_MODE[gy, gx]

            block_view = MUTABLE_PROCESSING_BUFFER[y1:y2, x1:x2]

            need_blend = energy < 0.95
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

    # PIXEL DAMAGE (KNOB 3)
    # =========================================================================
    comp = n(pkt_knobs[3])
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

    # CHROMA SHIFT (KNOB 5)
    # =========================================================================
    ana_val = n(pkt_knobs[5])
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

    # TEMPORAL ISOLATION & DATAMOSH MODULE (RELATED TO KNOB 2)
    # =========================================================================
    cv2.cvtColor(MUTABLE_PROCESSING_BUFFER, cv2.COLOR_BGR2GRAY, dst=SHARED_GRAY_BUFFER)

    if is_warming_up:
        PREV_FRAME_BUFFER[:] = MUTABLE_PROCESSING_BUFFER
        DATAMOSH_PREV_FRAME[:] = MUTABLE_PROCESSING_BUFFER
        WARMUP_COUNTER += 1

    if (current_time - LAST_TEMPORAL_PURGE) > 90.0:
        DATAMOSH_VX[:] = 0
        DATAMOSH_PREV_FRAME[:] = MUTABLE_PROCESSING_BUFFER
        PREV_FRAME_BUFFER[:] = MUTABLE_PROCESSING_BUFFER
        LAST_TEMPORAL_PURGE = current_time

    # === ISOLATED KNOB 2 ROUTING BLOCK ===
    # =========================================================================

    if not is_warming_up:
        datamosh_intensity = n(pkt_knobs[2]) ** 2
        apply_datamosh(datamosh_intensity, current_time)

    # HORIZONTAL SIGNAL DRIFT (KNOB 1)
    # =========================================================================
    drift = n(pkt_knobs[1])
    if drift > 0:
        h, w = MUTABLE_PROCESSING_BUFFER.shape[:2]

        global STATIC_V_BUFFER
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

    # STAGE 6: GRAVITATIONAL MELT DEFORMATION (KNOB 4)
    # =========================================================================
    apply_melt_pass(n(pkt_knobs[4]), current_time)

    # HIGH-DENSITY ASCII LAYER ENGINE (KNOB 0)
    # =========================================================================
    ascii_val = n(pkt_knobs[0])
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

    # LIGHTWEIGHT CRT SCANLINE OVERLAY (ZERO RUNTIME ALLOCATION)
    # =========================================================================
    if CRT_ENABLED:
        cv2.multiply(
            MUTABLE_PROCESSING_BUFFER,
            CRT_MASK,
            MUTABLE_PROCESSING_BUFFER,
            scale=1.0 / 255.0,
        )


# HARDWARE OUTPUT EXECUTION STATIONS
# ==================================================


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

            apply_glitch_pipeline(loop_execution_time, packet)

            # OPTIMIZATION: Convert knobs to 0.0 - 1.0 scale once per frame
            norm_knobs = [k / 1023.0 for k in packet.knobs]

            try:
                effect_intensity = (
                    norm_knobs[0] * 0.10
                    + norm_knobs[1] * 0.07
                    + norm_knobs[2] * 0.42
                    + norm_knobs[4] * 0.27
                    + norm_knobs[3] * 0.07
                    + norm_knobs[5] * 0.07
                )
                effect_intensity = max(0.0, min(1.0, effect_intensity))
            except Exception:
                effect_intensity = 0.5

            if loop_mode in ["countdown", "processing", "latest"]:
                UI_DISPLAY_BUFFER[:] = FROZEN_DISPLAY_BUFFER[:]
                if loop_mode == "countdown":
                    if (loop_monotonic - c_start) < FLASH_DURATION:
                        UI_DISPLAY_BUFFER.fill(255)  # Pure white camera flash
                    else:
                        cv2.convertScaleAbs(
                            UI_DISPLAY_BUFFER, dst=UI_DISPLAY_BUFFER, alpha=0.4, beta=0
                        )
            else:
                # Live preview used in "live" AND "pre-capture-countdown"
                UI_DISPLAY_BUFFER[:] = MUTABLE_PROCESSING_BUFFER[:]

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
            def async_storage_worker(disk_id, color_mat, bw_mat, active_knobs):
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

                try:
                    print(
                        f"[TELEMETRY ENGINE] Executing core capture ledger entry for session: {disk_id}"
                    )
                    telemetry.record_capture(
                        session_id=disk_id,
                        knobs=active_knobs,
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

    telemetry.compile_summary_stats()

    worker_configs = [
        ("UploadWorker", upload_queue_worker),
        (
            "TelemetryWorker",
            telemetry_worker,
        ),  # telemetry worker from server.py for state route
        (
            "TelemetryQueueConsumer",
            telemetry.telemetry_consumer_worker,
        ),  # telemetry worker from telemetry.py
        (
            "TelemetryCloudSyncWorker",
            lambda: telemetry.telemetry_sync_worker(interval_seconds=120),
        ),  # telemetry worker from telemetry.py
        ("IdleSyncWorker", idle_sync_worker),
        ("BackgroundLoop", background_loop),
    ]

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
