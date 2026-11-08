import base64
import io
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

from config.paths import (
    ARCHIVE_DIR,
    CAPTURE_DIR,
    ENV_FILE,
    FAULT_RECORD_PATH,
    NAKED_ARCHIVE_DIR,
    NAKED_DIR,
    PENDING_LEDGER_PATH,
    ROOT_DIR,
    SAVED_DIR,
    UNRESOLVED_LEDGER_PATH,
)

load_dotenv(ENV_FILE, override=False)

os.environ["OMP_NUM_THREADS"] = "1"

import cv2

cv2.setNumThreads(1)

import atexit
import faulthandler
import logging
import logging.handlers
import math
import queue
import re
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import uuid
from collections import namedtuple
from urllib.parse import quote, unquote

import numpy as np
import qrcode
import requests
import serial
from escpos.printer import File
from numpy.lib.stride_tricks import as_strided
from PIL import Image, ImageDraw, ImageFont

import telemetry

logger = logging.getLogger("glitchbooth.server")


# LOGGING
# ==================================================
# The logging setup (one queue-based setup for the whole process) lives in
# ops/logging_setup.py. It is imported and started here, at the same point as before.
from ops.logging_setup import (
    _log_state,
    log_throttled,
    setup_logging,
    shutdown_logging,
)

setup_logging()


def _cfg_number(name, default, cast, minimum=None):
    # Number from the environment with a safe fallback: a typo in .env must never stop the
    # kiosk from starting. Only values that would break something are replaced (not a
    # number, not finite, or below `minimum`); the default is the one written at the call.
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = cast(raw.strip())
    except (TypeError, ValueError):
        logger.warning(
            "[CONFIG] %s=%r is not a valid %s, using %s",
            name,
            raw,
            cast.__name__,
            default,
        )
        return default
    if not math.isfinite(value) or (minimum is not None and value < minimum):
        logger.warning(
            "[CONFIG] %s=%r is not usable (must be a finite number%s), using %s",
            name,
            raw,
            f" >= {minimum}" if minimum is not None else "",
            default,
        )
        return default
    return value


app = Flask(__name__)


# CONFIGURATION & HARDWARE CONSTANTS
# ==================================================

# The folder and file paths (CAPTURE_DIR, SAVED_DIR, ...) are defined in config/paths.py.
os.makedirs(CAPTURE_DIR, exist_ok=True)

os.makedirs(SAVED_DIR, exist_ok=True)

os.makedirs(ARCHIVE_DIR, exist_ok=True)

# Unprocessed source frame of every capture (the very frame the effects run on), saved as
# <id>_clean.jpg and uploaded to the clean ImageKit folder for the website slider.
os.makedirs(NAKED_DIR, exist_ok=True)

# Where the files of a capture live:
#   captures/           newest captures: <id>_color.jpg, <id>_bw.jpg, <id>_composite.png
#   saved/              <id>_qr.png
#   captures_naked/     <id>_clean.jpg of the newest captures
#   captures_archive/   <id>_color.jpg of older captures (the cleanup keeps the newest 100 in captures/)
#   naked_archive/      <id>_clean.jpg of those older captures: a clean photo follows its capture
# Nothing is ever deleted by the cleanup; B&W, QR and composite are recreated from nothing, so they
# are the only files it removes. A capture deleted from the gallery loses all of its files.
try:
    os.makedirs(NAKED_ARCHIVE_DIR, exist_ok=True)
except (
    OSError
) as _exc:  # the booth must still start; moving clean photos then fails with a warning
    logger.error("[CONFIG] Could not create %s: %s", NAKED_ARCHIVE_DIR, _exc)

IK_PRIVATE_KEY = os.getenv("IK_PRIVATE_KEY")
# Without the key the booth still runs and prints; nothing is uploaded, and every capture
# stays in the pending list until the key is set and the service restarted.
IK_UPLOADS_ENABLED = bool(IK_PRIVATE_KEY)
IK_AUTH_STRING = (
    base64.b64encode(f"{IK_PRIVATE_KEY}:".encode()).decode()
    if IK_UPLOADS_ENABLED
    else ""
)
if not IK_UPLOADS_ENABLED:
    logger.critical(
        "[CONFIG] IK_PRIVATE_KEY is not set in the .env file: uploads are OFF, captures stay in the pending list"
    )

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
#    Clean photos go to '/booth_captures_clean/<EVENT_SLUG>/' (same slug, sibling base
#    folder). ImageKit creates a missing folder on the first upload into it.
# 3. THERMAL RECEIPT: Manually update event title, subtitle, and social handles
#    inside 'print_booth_receipt()' below!
# 4. PENDING UPLOADS: Nothing to flush. A capture that is not uploaded yet keeps the event it
#    was taken in (its slug is stored with it as an 'id|slug' line in 'pending_uploads.txt')
#    and uploads to THAT event's folders, even after EVENT_SLUG has changed. The startup log
#    lists what is waiting and where it will go.
# =========================================================================

EVENT_SLUG = os.getenv("EVENT_SLUG", "")
IK_BASE_FOLDER = os.getenv("IK_BASE_FOLDER", "booth_captures")
IK_CLEAN_BASE_FOLDER = os.getenv("IK_CLEAN_BASE_FOLDER", "booth_captures_clean")
BASE_DOMAIN = os.getenv("BASE_DOMAIN", "https://glitchbooth.online")

# Print contrast variables (1.2 & 11 for bright days, 1.4 & 37 for night)
PRINT_ALPHA = _cfg_number("PRINT_ALPHA", 1.5, float)
PRINT_BETA = _cfg_number("PRINT_BETA", 11, int)

# Receipt QR composite configuration
RECEIPT_QR_SIZE = _cfg_number("RECEIPT_QR_SIZE", 120, int, minimum=1)
RECEIPT_QR_MARGIN = _cfg_number("RECEIPT_QR_MARGIN", 0, int, minimum=0)
RECEIPT_WIDTH_PX = 576  # printer paper width in dots (a fact of the printer)
RECEIPT_QR_MIN_PX = 64  # the QR is never shrunk below this to fit
RECEIPT_DITHER = "floyd"  # how the receipt photo becomes black dots: "floyd" (smooth) or "none" (hard threshold)
RECEIPT_QR_ROTATION = _cfg_number(
    "RECEIPT_QR_ROTATION", 90, int
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
FLASH_DURATION = _cfg_number("FLASH_DURATION", 0.3, float, minimum=0.0)
FLASH_COLOUR = (
    255,
    255,
    255,
)  # (R, G, B) of the capture flash; the kiosk screen reads this too
REVIEW_DIM_FACTOR = 0.4  # after the flash the frozen photo is shown at this brightness (0-1) during the countdown screen

# KIOSK SCREEN (pygame_display.py reads these, so every screen colour and label has one source)
# ------------------------------------------------------------------------------
BOOTH_VERSION = "beta 04"  # shown in the screen header and written to the log at start
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


def _normalize_slug(slug) -> str:
    return (slug or "").strip().strip("/")


def current_event_slug() -> str:
    return _normalize_slug(EVENT_SLUG)


def get_ik_target_folder(slug=None) -> str:

    # Computes the ImageKit folder path dynamically at call-time. 'slug' is the event a
    # capture was taken in; None means the event running now.
    # - Naked Mode (""):      '/booth_captures'
    # - Event Mode ("slug"):  '/booth_captures/slug'

    slug = current_event_slug() if slug is None else _normalize_slug(slug)
    if slug:
        return f"/{IK_BASE_FOLDER}/{slug}"
    return f"/{IK_BASE_FOLDER}"


def get_ik_clean_folder(slug=None) -> str:

    # Same structure as get_ik_target_folder(), under the sibling clean base folder.
    # - Naked Mode (""):      '/booth_captures_clean'
    # - Event Mode ("slug"):  '/booth_captures_clean/slug'

    slug = current_event_slug() if slug is None else _normalize_slug(slug)
    if slug:
        return f"/{IK_CLEAN_BASE_FOLDER}/{slug}"
    return f"/{IK_CLEAN_BASE_FOLDER}"


def get_landing_url(ts: str, slug=None) -> str:

    # Constructs the landing URL for capture timestamp 'ts'.
    # - Naked Mode (""):      'https://glitchbooth.online/p/{ts}'
    # - Event Mode ("slug"):  'https://glitchbooth.online/slug/p/{ts}'

    slug = current_event_slug() if slug is None else _normalize_slug(slug)
    domain = BASE_DOMAIN.rstrip("/")
    if slug:
        return f"{domain}/{slug}/p/{ts}"
    return f"{domain}/p/{ts}"


if not re.fullmatch(r"[A-Za-z0-9._-]*", current_event_slug()):
    logger.warning(
        "[CONFIG] EVENT_SLUG=%r contains characters other than letters, digits, '.', '_' and '-'. It is used exactly as written; check that it matches the website's slug.",
        EVENT_SLUG,
    )

# UPLOAD DURABILITY TUNABLES
# ==============================================================================
IDLE_SYNC_INTERVAL = (
    30  # seconds between idle sync passes when the last pass uploaded nothing
)
IDLE_SYNC_MAX_PER_PASS = 5  # entries tried per idle sync pass
# Seconds to wait before an upload the cloud REJECTED (HTTP 400, 404, 413 ...) is tried again:
# after the 1st, 2nd, 3rd and later rejection of the same capture.
UPLOAD_REJECT_BACKOFF = (300, 1800, 7200)
UPLOAD_REJECT_LIMIT = (
    8  # after this many rejections the capture moves to unresolved_uploads.txt
)
UPLOAD_AUTH_BACKOFF = (
    600  # seconds the idle sync waits after ImageKit refused the key (401/403)
)
# A pending line without a slug was written before slugs existed. "" = it belongs to no event.
LEGACY_LEDGER_SLUG = ""
# Emergency switch: True = an upload only counts when ImageKit answers with a JSON that has a
# fileId and a filePath ending in the file name. False = HTTP 200 alone counts (old behaviour).
UPLOAD_VALIDATE_RESPONSE = True
IK_UPLOAD_URL = "https://upload.imagekit.io/api/v1/files/upload"

for _name, _ok in (
    ("IDLE_SYNC_INTERVAL", IDLE_SYNC_INTERVAL >= 1),
    ("IDLE_SYNC_MAX_PER_PASS", IDLE_SYNC_MAX_PER_PASS >= 1),
    ("UPLOAD_REJECT_LIMIT", UPLOAD_REJECT_LIMIT >= 1),
    ("UPLOAD_AUTH_BACKOFF", UPLOAD_AUTH_BACKOFF >= 0),
    (
        "UPLOAD_REJECT_BACKOFF",
        len(UPLOAD_REJECT_BACKOFF) >= 1 and all(v >= 0 for v in UPLOAD_REJECT_BACKOFF),
    ),
):
    if not _ok:
        logger.warning(
            "[CONFIG] %s is outside what the upload code can use; the code treats it as the nearest usable value",
            _name,
        )


logger.info("[CONFIG] Glitch Booth %s", BOOTH_VERSION)
logger.info(
    "[CONFIG] mode=%s | imagekit path=%s | print contrast alpha=%s beta=%s | sample url=%s",
    "EVENT MODE (" + EVENT_SLUG + ")" if EVENT_SLUG else "NAKED MODE",
    get_ik_target_folder(),
    PRINT_ALPHA,
    PRINT_BETA,
    get_landing_url(str(int(time.time() * 1000))),
)


SERIAL_PORTS = ["/dev/ttyUSB0", "/dev/ttyACM0"]
CAMERA_STALE_TIMEOUT = 5
CAMERA_REFRESH_INTERVAL = 21600
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

NAKED_CAPTURE_ENABLED = True
# The clean (unprocessed) frame is saved as <id>_clean.jpg and uploaded next to the processed
# photo, so the website can offer a clean/processed slider.
CLEAN_JPEG_QUALITY = 88  # 85-90
# JPEG quality of the processed colour and black-and-white photos (1-100). Lower = smaller files,
# faster uploads, softer picture. It used to be OpenCV's unnamed default of 95.
CAPTURE_JPEG_QUALITY = 90
TELEMETRY_ENABLED = (
    True  # Set False to disable all capture logging and telemetry threads
)

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
    "ASCII_FONT_EXTRA_PX": (
        1 - max(FRAME_W // ASCII_GRID_W, FRAME_H // ASCII_GRID_H),
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

# Values and buffers derived from the tunables above (they come after the check).
CORRUPTION_GRID_H = FRAME_H // CORRUPTION_CELL_H
CORRUPTION_GRID_W = FRAME_W // CORRUPTION_CELL_W

STATIC_BLOCK_BUFFER = np.empty(
    (CORRUPTION_CELL_H, CORRUPTION_CELL_W, 3), dtype=np.uint8, order="C"
)

GLOBAL_NOISE_BUFFER = np.empty(
    (CORRUPTION_CELL_H, CORRUPTION_CELL_W, 3), dtype=np.uint8, order="C"
)

# WHO TOUCHES THE FRAME BUFFERS (a reading aid; nothing here needs a change)
#   LATEST_CAMERA_FRAME        written by the camera thread, copied by the processing loop, both
#                              under cam_raw_lock.
#   MUTABLE_PROCESSING_BUFFER  owned by the processing loop thread (effects run in it). So are
#                              the effect scratch buffers and NAKED_FRAME_BUFFER.
#   UI_DISPLAY_BUFFER          written by the processing loop under frame_lock, copied by the
#                              display thread under frame_lock.
#   FROZEN_DISPLAY_BUFFER      written by the processing loop at the moment of capture, WITHOUT
#                              frame_lock; read by the processing loop (countdown / review) and by
#                              the display thread under frame_lock (pygame_display.py, review
#                              fallback). That is safe only because of the order: the capture
#                              writes it while the mode is "pre-capture-countdown", BEFORE the mode
#                              becomes "countdown"; the display reads it only in "countdown" and
#                              "latest"; the next write needs a new capture, which starts from
#                              "live". Anyone adding a second writer, or a write in another mode,
#                              must take frame_lock around it.
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

# Shared luminance buffer: utilized sequentially during Melt pass and post-capture receipt contrast prep
STATIC_MELT_GRAY_BUFFER = np.empty((FRAME_H, FRAME_W), dtype=np.uint8, order="C")

# --- MELT BUFFERS ---

STATIC_MELT_SNAPSHOT = np.empty((FRAME_H, FRAME_W, 3), dtype=np.uint8, order="C")

COMPRESSION_STAGE_BUFFER = np.empty((FRAME_H, FRAME_W, 3), dtype=np.uint8, order="C")

STATIC_ROW_BUFFER = np.empty((FRAME_W, 3), dtype=np.uint8, order="C")
CHANNEL_SHIFT_BUFFER = np.empty((FRAME_H, FRAME_W), dtype=np.uint8, order="C")
SLICE_SHIFT_BUFFER = np.empty((FRAME_H, FRAME_W, 3), dtype=np.uint8, order="C")
GHOST_FRAME_BUFFER = np.empty((FRAME_H, FRAME_W, 3), dtype=np.uint8, order="C")

STATIC_V_BUFFER = None

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


# WARM-UP REGISTER AND PACKET CORRUPTION STATE
# =========================================================================
WARMUP_FRAMES = 10
WARMUP_COUNTER = 0

CORRUPTION_ENERGY = np.zeros(
    (CORRUPTION_GRID_H, CORRUPTION_GRID_W), dtype=np.float32, order="C"
)

CORRUPTION_MODE = np.zeros(
    (CORRUPTION_GRID_H, CORRUPTION_GRID_W), dtype=np.uint8, order="C"
)

# ASCII ENGINE PRE-ALLOCATED STORAGE
# =========================================================================
CELL_W = FRAME_W // ASCII_GRID_W
CELL_H = FRAME_H // ASCII_GRID_H

ASCII_LOWRES_GRAY = np.zeros((ASCII_GRID_H, ASCII_GRID_W), dtype=np.uint8, order="C")
ASCII_UPRES_GRAY = np.zeros((FRAME_H, FRAME_W), dtype=np.uint8, order="C")

ASCII_BLOCK_BUFFER = np.zeros(
    (ASCII_GRID_H, ASCII_GRID_W, CELL_H, CELL_W), dtype=np.uint8, order="C"
)

ASCII_TILES = np.zeros((16, CELL_H, CELL_W), dtype=np.uint8, order="C")

ASCII_GREEN_BUFFER = np.zeros((FRAME_H, FRAME_W, 3), dtype=np.uint8, order="C")
ASCII_NOISE_LOW = np.zeros((ASCII_GRID_H, ASCII_GRID_W), dtype=np.uint8, order="C")
ASCII_NOISE_UP = np.zeros((FRAME_H, FRAME_W), dtype=np.uint8, order="C")

clahe = cv2.createCLAHE(
    clipLimit=ASCII_CLAHE_CLIP,
    tileGridSize=tuple(ASCII_CLAHE_TILES)
    if "ascii_matrix" not in DISABLED_EFFECTS
    else (8, 8),
)

SPARK_COUNTER = 0
SPARK_INTERVAL = ASCII_SPARK_START_FRAMES  # re-rolled within ASCII_SPARK_INTERVAL later


# ATOMIC REFERENCE GLOBALS & PIPELINE LOCKS
# ==================================================

GLOBAL_KNOBS = (0, 0, 0, 0, 0, 0)
GLOBAL_BUTTON = 1

# UNIFIED TRANSACTING SESSION STATE MATRIX
# ==============================================================================
ACTIVE_SESSION = {
    "mode": "live",  # live, pre-capture-countdown, countdown (flash + PRINTING), latest
    "session_id": None,
    "upload_status": "IDLE",  # IDLE, UPLOADING, SUCCESS, FAILED
    "print_state": "idle",  # idle, waiting, printing, retrying, done, failed
    "notice": "",  # message for the kiosk screen and the web pages
    "notice_until": None,  # monotonic expiry of the notice (None = until the session ends)
    "countdown_start_time": 0.0,
    "countdown_remaining": POSE_COUNTDOWN_SECONDS,
    "latest_since": 0.0,  # monotonic time the review screen started
    "knobs": [0, 0, 0, 0, 0, 0],
}

mode_lock = threading.Lock()
frame_lock = threading.Lock()
UI_FRAME_SEQ = (
    0  # bumped (under frame_lock) each time UI_DISPLAY_BUFFER is fully rewritten
)
cam_raw_lock = threading.Lock()

printer_hardware_lock = threading.Lock()
cache_file_lock = threading.Lock()

hw_state_lock = threading.Lock()

SHUTDOWN_EVENT = threading.Event()
CRITICAL_WORKERS = []

# 1. Initialize telemetry and inject the global event object
if TELEMETRY_ENABLED:
    telemetry.init_telemetry(SHUTDOWN_EVENT)

STREAM_SEMAPHORE = threading.Semaphore(4)
ACTIVE_STREAM_VIEWERS = 0

capture_lock_until = 0

last_good_camera = time.time()
camera_started_at = time.time()
last_frame_time = time.time()
camera_fail_count = 0

SYSTEM_TELEMETRY = {"ip": "0.0.0.0", "cpu_temp": "N/A", "memory_rss_mb": -1}
last_button_state = 1

FRAME_ID = 0

upload_queue = queue.Queue(maxsize=32)
print_queue = queue.Queue(maxsize=4)
_print_ids = set()  # captures queued or printing: one receipt per id at a time
_print_ids_lock = threading.Lock()

# CAPTURE -> PRINT FLOW TUNABLES AND MESSAGES
# ==================================================
# The booth screen waits for the printer, never for the cloud upload.
LATEST_TIMEOUT_SECONDS = 20.0  # the review screen returns to the live view by itself
PRINT_ATTEMPTS = 2  # a failed print is retried automatically
PRINT_RETRY_DELAY = 1.0
PRINT_OVERLAY_TIMEOUT = 10.0  # the PRINTING screen never lasts longer than this
CAMERA_FRESH_MAX_AGE = 5.0  # an older camera frame counts as "camera not working"
CAMERA_NOTICE_SECONDS = 5.0
MSG_CAMERA = "GLITCH BOOTH KÜÇÜK BİR GLITCH YAŞADI :( LÜTFEN TEKRAR DENEYİN!"
MSG_PRINT_RETRY = "BASKIDA BİR SORUN YAŞANDI :( TEKRAR DENİYORUM!"
MSG_PRINT_FAILED = "BASKIDA BİR SORUN YAŞANDI :("

# Set while the last upload failed for good (offline); any upload that works clears it.
# The receipt uses it to choose between the normal and the offline text.
NETWORK_OFFLINE = threading.Event()


# SESSION STATE HELPERS
# ==================================================
# Everything that changes the active session from a worker thread goes through
# session_update(), which ignores updates for a session that is no longer the active one.
# Everything that returns the booth to the live view goes through reset_to_live().


def session_update(session_id, **fields):
    with mode_lock:
        if ACTIVE_SESSION["session_id"] != session_id:
            return False
        ACTIVE_SESSION.update(fields)
        return True


def _set_notice_locked(text, seconds=None):
    ACTIVE_SESSION["notice"] = text
    ACTIVE_SESSION["notice_until"] = (time.monotonic() + seconds) if seconds else None


def _notice_locked():
    until = ACTIVE_SESSION["notice_until"]
    if until is not None and time.monotonic() >= until:
        ACTIVE_SESSION["notice"] = ""
        ACTIVE_SESSION["notice_until"] = None
    return ACTIVE_SESSION["notice"]


def set_notice(text, seconds=None):
    with mode_lock:
        _set_notice_locked(text, seconds)


def reset_to_live(reason, expected_session_id=None, only_if_mode=None):
    # The single way back to the live view (button, review timeout, camera problem, web).
    global capture_lock_until
    with mode_lock:
        if only_if_mode is not None and ACTIVE_SESSION["mode"] not in only_if_mode:
            return False
        if (
            expected_session_id is not None
            and ACTIVE_SESSION["session_id"] != expected_session_id
        ):
            return False
        previous_session = ACTIVE_SESSION["session_id"]
        ACTIVE_SESSION.update(
            mode="live",
            session_id=None,
            upload_status="IDLE",
            print_state="idle",
            countdown_remaining=POSE_COUNTDOWN_SECONDS,
            latest_since=0.0,
        )
        _set_notice_locked("", None)
        capture_lock_until = time.time() + CAPTURE_LOCK_SECONDS
    logger.info(
        "[STATE ENGINE] Returning to the live view (%s). Cleared session %s.",
        reason,
        previous_session,
    )
    return True


def camera_is_fresh():
    with hw_state_lock:
        age = time.time() - last_frame_time
    return age <= CAMERA_FRESH_MAX_AGE, age


def enqueue_print(ts, source, wait=0.0):
    # Returns "queued", "duplicate" (already queued or printing) or "busy" (queue full).
    with _print_ids_lock:
        if ts in _print_ids:
            return "duplicate"
        _print_ids.add(ts)
    try:
        print_queue.put((ts, source), timeout=wait)
    except queue.Full:
        with _print_ids_lock:
            _print_ids.discard(ts)
        return "busy"
    return "queued"


# UPLOAD LEDGER, ATOMIC FILE WRITES, UPLOAD RESULTS
# ==================================================
# pending_uploads.txt is the single record of captures that are not fully uploaded yet. One
# line per capture: "<id>|<slug>" (the slug is percent-encoded), or "<id>|<slug>|c" when only
# the clean photo is left. A bare "<id>" is a legacy line written before slugs existed.
# Rules for every helper below:
#   - the whole read-modify-write happens under cache_file_lock, and the lock is never nested;
#   - no network call is ever made while the lock is held;
#   - removals rewrite through a temp file in the same folder and os.replace, so a reader (or a
#     power cut) sees the old file or the complete new one;
#   - a line only counts once its newline is there (a cut-off last line is never trusted);
#   - lines that cannot be read are moved to unresolved_uploads.txt, never silently dropped.

LedgerEntry = namedtuple("LedgerEntry", "id slug clean_only legacy")
_LEDGER_ID_RE = re.compile(r"[0-9]{1,20}")


def _fsync_dir(path):
    fd = os.open(os.path.dirname(os.path.abspath(path)) or ".", os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _fsync_file(path):
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic_write_bytes(path, data, fsync=False):
    # Temp file in the same folder, then os.replace. A plain open() is used (not mkstemp) so the
    # file gets the same permissions as every other file the booth writes.
    tmp = f"{path}.{os.getpid()}.{threading.get_ident()}.{uuid.uuid4().hex[:8]}.tmp"
    try:
        with open(tmp, "xb") as f:
            f.write(data)
            f.flush()
            if fsync:
                os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def _ledger_format(entry_id, slug, clean_only=False):
    return f"{entry_id}|{quote(slug or '', safe='')}" + ("|c" if clean_only else "")


def _ledger_parse(line):
    # A LedgerEntry, None for a blank line, or a string with the reason the line is bad.
    line = line.strip()
    if not line:
        return None
    parts = line.split("|")
    if not _LEDGER_ID_RE.fullmatch(parts[0]):
        return "id is not 1-20 digits"
    if len(parts) == 1:
        return LedgerEntry(parts[0], _normalize_slug(LEGACY_LEDGER_SLUG), False, True)
    if len(parts) == 2:
        return LedgerEntry(parts[0], unquote(parts[1]), False, False)
    if len(parts) == 3 and parts[2] == "c":
        return LedgerEntry(parts[0], unquote(parts[1]), True, False)
    return "unknown format"


def _ledger_read_locked():
    # (entries without duplicates in file order, [(raw line, reason)] for bad lines)
    try:
        with open(PENDING_LEDGER_PATH, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
    except FileNotFoundError:
        return [], []
    entries, bad, index = [], [], {}
    pieces = text.split("\n")
    if text and not text.endswith("\n"):
        # The last piece may be cut off (a power cut in the middle of an append); a cut-off
        # slug would be wrong, so it is not accepted.
        bad.append((pieces.pop().strip(), "incomplete last line (no newline)"))
    for raw in pieces:
        parsed = _ledger_parse(raw)
        if parsed is None:
            continue
        if isinstance(parsed, str):
            bad.append((raw.strip(), parsed))
            continue
        if parsed.id in index:
            # The same capture twice: keep the first; "colour still pending" wins.
            old = entries[index[parsed.id]]
            entries[index[parsed.id]] = old._replace(
                clean_only=old.clean_only and parsed.clean_only
            )
            continue
        index[parsed.id] = len(entries)
        entries.append(parsed)
    return entries, bad


def _unresolved_append_locked(rows):
    if not rows:
        return
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    with open(UNRESOLVED_LEDGER_PATH, "a", encoding="utf-8") as f:
        for ident, slug, reason in rows:
            f.write(
                f"{ident}|{quote(slug or '', safe='')}|{quote(reason, safe=' /,:()._-')}|{stamp}\n"
            )
        f.flush()
        os.fsync(f.fileno())


def _ledger_write_locked(entries, bad):
    # Lines that cannot be read go to the unresolved file first, then the ledger is replaced.
    _unresolved_append_locked(
        [
            (quote(raw, safe=""), "", f"unreadable ledger line: {why}")
            for raw, why in bad
        ]
    )
    data = "".join(
        _ledger_format(e.id, e.slug, e.clean_only) + "\n" for e in entries
    ).encode("utf-8")
    atomic_write_bytes(PENDING_LEDGER_PATH, data, fsync=True)
    _fsync_dir(PENDING_LEDGER_PATH)


def ledger_snapshot():
    with cache_file_lock:
        return _ledger_read_locked()[0]


def ledger_get(entry_id):
    for entry in ledger_snapshot():
        if entry.id == entry_id:
            return entry
    return None


def ledger_contains(entry_id):
    return ledger_get(entry_id) is not None


def ledger_add(entry_id, slug):
    # Appends one line and fsyncs it. False when the capture is already in the ledger.
    with cache_file_lock:
        entries, bad = _ledger_read_locked()
        if any(e.id == entry_id for e in entries):
            return False
        if bad:
            _ledger_write_locked(entries, bad)  # clears a cut-off tail first
        existed = os.path.exists(PENDING_LEDGER_PATH)
        with open(PENDING_LEDGER_PATH, "a", encoding="utf-8") as f:
            f.write(_ledger_format(entry_id, slug) + "\n")
            f.flush()
            os.fsync(f.fileno())
        if not existed:
            _fsync_dir(PENDING_LEDGER_PATH)
        return True


def ledger_remove(ids):
    # Removes exactly these ids from the CURRENT file. Returns how many were removed.
    ids = set(ids)
    if not ids:
        return 0
    with cache_file_lock:
        entries, bad = _ledger_read_locked()
        keep = [e for e in entries if e.id not in ids]
        removed = len(entries) - len(keep)
        if removed or bad:
            _ledger_write_locked(keep, bad)
        return removed


def ledger_set_clean_only(entry_id, clean_only):
    # Marks a capture as "only the clean photo is left" (or back).
    with cache_file_lock:
        entries, bad = _ledger_read_locked()
        out, changed = [], False
        for e in entries:
            if e.id == entry_id and e.clean_only != clean_only:
                e, changed = e._replace(clean_only=clean_only), True
            out.append(e)
        if changed or bad:
            _ledger_write_locked(out, bad)
        return changed


def ledger_to_unresolved(entry_id, slug, reason):
    # Unresolved list first (fsynced), then out of the ledger.
    with cache_file_lock:
        _unresolved_append_locked([(entry_id, slug, reason)])
        entries, bad = _ledger_read_locked()
        keep = [e for e in entries if e.id != entry_id]
        if len(keep) != len(entries) or bad:
            _ledger_write_locked(keep, bad)
    logger.error(
        "[LEDGER] Capture %s (slug %r) moved to %s: %s",
        entry_id,
        slug,
        UNRESOLVED_LEDGER_PATH,
        reason,
    )


def ledger_startup_check():
    # Runs once before any worker starts: stale temp files out, unreadable lines to the
    # unresolved list, legacy bare ids made explicit, and a summary of what is waiting.
    removed_tmp = 0
    for directory in (
        ROOT_DIR,
        CAPTURE_DIR,
        SAVED_DIR,
        NAKED_DIR,
        ARCHIVE_DIR,
        NAKED_ARCHIVE_DIR,
    ):
        try:
            names = os.listdir(directory)
        except OSError:
            continue
        for name in names:
            stale = name.endswith(".tmp") and (
                directory != ROOT_DIR or name.startswith("pending_uploads")
            )
            if stale or (directory == ROOT_DIR and name == "pending_uploads.tmp"):
                try:
                    os.remove(os.path.join(directory, name))
                    removed_tmp += 1
                except OSError:
                    pass
    with cache_file_lock:
        entries, bad = _ledger_read_locked()
        legacy = [e.id for e in entries if e.legacy]
        for raw, why in bad:
            logger.error(
                "[LEDGER] Unreadable line %r (%s): moved to %s",
                raw,
                why,
                UNRESOLVED_LEDGER_PATH,
            )
        if legacy:
            logger.warning(
                "[LEDGER] %d legacy line(s) without a slug are treated as slug %r: %s",
                len(legacy),
                _normalize_slug(LEGACY_LEDGER_SLUG),
                legacy[:5],
            )
        if bad or legacy:
            _ledger_write_locked([e._replace(legacy=False) for e in entries], bad)
    by_slug = {}
    for e in entries:
        by_slug[e.slug or "no event"] = by_slug.get(e.slug or "no event", 0) + 1
    unresolved = 0
    try:
        with open(UNRESOLVED_LEDGER_PATH, "r", encoding="utf-8", errors="replace") as f:
            unresolved = sum(1 for line in f if line.strip())
    except OSError:
        pass
    logger.info(
        "[LEDGER] %d pending (%s), %d line(s) in %s, %d stale temp file(s) removed",
        len(entries),
        ", ".join(f"{k}: {v}" for k, v in sorted(by_slug.items())) or "none",
        unresolved,
        UNRESOLVED_LEDGER_PATH,
        removed_tmp,
    )
    if unresolved:
        logger.warning(
            "[LEDGER] %s holds %d capture(s) that need a look",
            UNRESOLVED_LEDGER_PATH,
            unresolved,
        )


# In-flight claims: the upload worker and the idle sync must never upload the same capture at
# the same time. Whoever claims an id uploads it; the other one skips it.
_inflight_ids = set()
_inflight_lock = threading.Lock()


def claim_upload(entry_id):
    with _inflight_lock:
        if entry_id in _inflight_ids:
            return False
        _inflight_ids.add(entry_id)
        return True


def release_upload(entry_id):
    with _inflight_lock:
        _inflight_ids.discard(entry_id)


def inflight_snapshot():
    with _inflight_lock:
        return set(_inflight_ids)


# Rejections (HTTP 400, 404, 413 ...) per capture: how often, and when it may be tried again.
_upload_rejects = {}
_upload_rejects_lock = threading.Lock()
_auth_blocked_until = 0.0


def note_rejection(entry_id):
    # Returns how many times this capture has been rejected, and starts its backoff.
    with _upload_rejects_lock:
        count = _upload_rejects.get(entry_id, (0, 0.0))[0] + 1
        steps = tuple(UPLOAD_REJECT_BACKOFF) or (300,)
        wait = steps[min(count, len(steps)) - 1]
        _upload_rejects[entry_id] = (count, time.monotonic() + wait)
        return count


def backoff_active(entry_id):
    with _upload_rejects_lock:
        entry = _upload_rejects.get(entry_id)
        return bool(entry) and time.monotonic() < entry[1]


def clear_rejections(entry_id):
    with _upload_rejects_lock:
        _upload_rejects.pop(entry_id, None)


def interpret_upload_response(response, remote_filename):
    # ("ok", fileId) | ("retry", why) | ("auth", why) | ("rejected", why)
    code = response.status_code
    if code == 200:
        if not UPLOAD_VALIDATE_RESPONSE:
            return "ok", "(answer not checked)"
        try:
            data = response.json()
        except Exception:
            return (
                "retry",
                "HTTP 200 but the body is not JSON (captive portal or proxy?)",
            )
        if not isinstance(data, dict):
            return "retry", "HTTP 200 but the JSON is not an object"
        file_id = data.get("fileId")
        if not isinstance(file_id, str) or not file_id.strip():
            return "retry", "HTTP 200 but the answer has no fileId"
        path = data.get("filePath")
        if not isinstance(path, str) or not path.endswith("/" + remote_filename):
            return (
                "retry",
                f"HTTP 200 but filePath {path!r} does not end with /{remote_filename}",
            )
        return "ok", file_id
    if code in (401, 403):
        return "auth", f"HTTP {code}: ImageKit refused the key"
    if code in (408, 429) or code >= 500:
        return "retry", f"HTTP {code}"
    return "rejected", f"HTTP {code}"


def ik_upload(http, local_path, remote_filename, folder, timeout):
    # The one upload function (processed photo, clean photo, idle sync).
    # Returns (result, detail) with result "ok", "retry", "auth" or "rejected".
    try:
        with open(local_path, "rb") as f:
            files = {
                "file": (remote_filename, f, "image/jpeg"),
                "fileName": (None, remote_filename),
                "useUniqueFileName": (None, "false"),
                "folder": (None, folder),
            }
            headers = {"Authorization": f"Basic {IK_AUTH_STRING}"}
            response = http.post(
                IK_UPLOAD_URL, headers=headers, files=files, timeout=timeout
            )
    except Exception as exc:
        return "retry", f"{type(exc).__name__}: {exc}"
    result, detail = interpret_upload_response(response, remote_filename)
    if result == "retry" and response.status_code == 200:
        log_throttled(
            logger,
            logging.WARNING,
            "upload-bad-answer",
            300.0,
            "[UPLOAD] Unexpected answer from ImageKit (%s). Body starts: %r",
            detail,
            response.text[:200],
        )
    elif result == "ok" and UPLOAD_VALIDATE_RESPONSE:
        try:
            returned = response.json().get("filePath", "")
        except Exception:
            returned = ""
        if returned != f"{folder}/{remote_filename}":
            log_throttled(
                logger,
                logging.WARNING,
                "upload-path-differs",
                300.0,
                "[UPLOAD] ImageKit stored %r but %r was expected",
                returned,
                f"{folder}/{remote_filename}",
            )
    return result, detail


def jpeg_file_usable(path):
    # Exists, is not empty and starts with the JPEG marker (a file that was not fsynced may not
    # have survived a power cut intact).
    try:
        if os.path.getsize(path) <= 0:
            return False
        with open(path, "rb") as f:
            return f.read(2) == b"\xff\xd8"
    except OSError:
        return False


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
    target_font_size = max(CELL_W, CELL_H) + ASCII_FONT_EXTRA_PX

    for path in pi_font_paths:
        try:
            font = ImageFont.truetype(path, target_font_size)
            logger.info("ASCII font loaded.")
            break
        except OSError:
            continue

    if font is None:
        logger.warning(
            "Monospace TTF not found. Using system default. Text may appear small."
        )
        font = ImageFont.load_default()

    for i, char in enumerate(char_ramp):
        img = Image.new("L", (CELL_W, CELL_H), color=0)
        draw = ImageDraw.Draw(img)

        left, top, right, bottom = draw.textbbox((0, 0), char, font=font)
        w, h = right - left, bottom - top

        draw.text(
            ((CELL_W - w) // 2, (CELL_H - h) // 2 - ASCII_FONT_LIFT_PX),
            char,
            font=font,
            fill=255,
        )

        ASCII_TILES[i] = np.array(img, dtype=np.uint8)


init_ascii_tiles()


# COHERENT CONTROL SNAPSHOT DATA MODEL
# ==================================================


class ControlPacket:
    # Immutable data record representing a single, frozen control plane epoch.

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
    # Current resident memory of this process in MB (not the peak); -1 if unreadable.
    try:
        with open("/proc/self/statm") as statm:
            resident_pages = int(statm.read().split()[1])
        return round(resident_pages * os.sysconf("SC_PAGE_SIZE") / 1048576.0, 2)
    except Exception:
        return -1


def telemetry_worker():
    global SYSTEM_TELEMETRY
    logger.info("[INIT] System monitor (IP, CPU temperature, memory): active.")

    while not SHUTDOWN_EVENT.is_set():
        try:
            # A new dict is swapped in whole, so readers never see a half-updated one.
            SYSTEM_TELEMETRY = {
                "ip": get_actual_ip(),
                "cpu_temp": get_cpu_temp(),
                "memory_rss_mb": get_memory_usage(),
            }
        except Exception as e:
            log_throttled(
                logger,
                logging.WARNING,
                "sysmon-fault",
                60.0,
                "[SYSMON] Could not read system status: %s",
                e,
            )

        if SHUTDOWN_EVENT.wait(timeout=2.0):
            break
    logger.info("[SHUTDOWN] System monitor exited cleanly.")


def open_camera():
    for index in [0, 1, 2]:
        cap = None
        try:
            cap = cv2.VideoCapture(index, cv2.CAP_V4L2)
            if cap.isOpened():
                ret, frame = cap.read()
                if ret and frame is not None:
                    cap.set(cv2.CAP_PROP_FRAME_WIDTH, FRAME_W)
                    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, FRAME_H)
                    cap.set(cv2.CAP_PROP_FPS, CAMERA_FPS)
                    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                    # --- TRACK HARDWARE NEGOTIATION ---
                    actual_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
                    actual_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
                    actual_fps = cap.get(cv2.CAP_PROP_FPS)
                    logger.info(
                        "SUCCESS: V4L2 pipeline pinned to index %s | Requested %sx%s, Negotiated %sx%s, at %s FPS",
                        index,
                        FRAME_W,
                        FRAME_H,
                        actual_w,
                        actual_h,
                        actual_fps,
                    )
                    return cap
            cap.release()
        except Exception:
            # Whatever went wrong, the half-opened device must not stay open.
            log_throttled(
                logger,
                logging.ERROR,
                "camera-open-fault",
                30.0,
                "[CAMERA] Opening the camera at index %s raised an error",
                index,
                exc_info=True,
            )
            if cap is not None:
                try:
                    cap.release()
                except Exception:
                    pass
    log_throttled(
        logger,
        logging.ERROR,
        "camera-open",
        30.0,
        "[CAMERA] Could not open a camera on V4L2 indexes 0-2 (acquisition failed)",
    )
    return None


def camera_io_worker():
    # The camera thread. Whatever happens, the camera is released when it ends.
    cap = None
    try:
        cap = open_camera()
        cap = _camera_loop(cap)
    except Exception:
        logger.exception("[CAMERA] Camera worker died unexpectedly")
    finally:
        if cap is not None:
            try:
                cap.release()
            except Exception:
                pass
        logger.info("[SHUTDOWN] Camera worker ended; the camera is released.")


def _camera_loop(cap):
    # Returns the camera that is open when the loop ends (or None).
    global last_good_camera, camera_started_at, CAMERA_HEARTBEAT
    global LATEST_CAMERA_FRAME
    global last_frame_time, camera_fail_count
    consecutive_failures = 0
    camera_down_since = None

    while not SHUTDOWN_EVENT.is_set():
        CAMERA_HEARTBEAT = time.monotonic()
        try:
            if cap is None or not cap.isOpened():
                if camera_down_since is None:
                    camera_down_since = time.monotonic()
                elif time.monotonic() - camera_down_since > 30:
                    log_throttled(
                        logger,
                        logging.CRITICAL,
                        "camera-down",
                        60.0,
                        "[CAMERA] Still no camera after %.0f s; the booth cannot take photos",
                        time.monotonic() - camera_down_since,
                    )
                if SHUTDOWN_EVENT.wait(2):
                    break
                cap = open_camera()
                continue
            camera_down_since = None
            ret, frame = cap.read()

            if ret and frame is not None:
                consecutive_failures = 0
                with hw_state_lock:
                    camera_fail_count = 0
                    last_frame_time = time.time()
                with cam_raw_lock:
                    if frame.shape == LATEST_CAMERA_FRAME.shape:
                        LATEST_CAMERA_FRAME[:] = frame
                    else:
                        log_throttled(
                            logger,
                            logging.WARNING,
                            "camera-resolution",
                            60.0,
                            "[CAMERA] Resolution mismatch: expected %s, got %s. Resizing.",
                            LATEST_CAMERA_FRAME.shape,
                            frame.shape,
                        )
                        cv2.resize(frame, (FRAME_W, FRAME_H), dst=LATEST_CAMERA_FRAME)
                last_good_camera = time.time()
            else:
                consecutive_failures += 1
                with hw_state_lock:
                    camera_fail_count += 1
                log_throttled(
                    logger,
                    logging.WARNING,
                    "camera-read",
                    5.0,
                    "[CAMERA] Frame read failed (%d consecutive)",
                    consecutive_failures,
                )
                time.sleep(0.01)
            now = time.time()
            if (
                (consecutive_failures > 15)
                or (now - last_good_camera > CAMERA_STALE_TIMEOUT)
                or (now - camera_started_at > CAMERA_REFRESH_INTERVAL)
            ):
                if consecutive_failures > 15:
                    reset_reason = "%d consecutive read failures" % consecutive_failures
                    reset_level = logging.WARNING
                elif now - last_good_camera > CAMERA_STALE_TIMEOUT:
                    reset_reason = "no good frame for %.1f s" % (now - last_good_camera)
                    reset_level = logging.WARNING
                else:
                    reset_reason = "scheduled refresh"
                    reset_level = logging.INFO
                logger.log(
                    reset_level, "[CAMERA] Resetting the camera: %s", reset_reason
                )
                if cap is not None:
                    try:
                        cap.release()
                    except Exception:
                        pass
                    cap = None
                SHUTDOWN_EVENT.wait(0.3)
                cap = open_camera()
                consecutive_failures = 0
                last_good_camera = time.time()
                camera_started_at = time.time()
        except Exception:
            log_throttled(
                logger,
                logging.ERROR,
                "camera-worker-fault",
                10.0,
                "[CAMERA] Camera worker exception",
                exc_info=True,
            )
            SHUTDOWN_EVENT.wait(1)
    return cap


_serial_rx = bytearray()  # received bytes that do not yet form a complete line
_serial_synced = (
    False  # False until the first (possibly cut-off) line after a connect is dropped
)
_serial_bad_packets = 0


def reset_serial_input():
    # Called after every (re)connect: forget half-received data.
    global _serial_synced
    _serial_rx.clear()
    _serial_synced = False


def _parse_knob_packet(line):
    # A packet is seven integers: six knobs (0-1023) and the button (0 or 1).
    parts = line.split(",")
    if len(parts) != 7:
        return None
    try:
        values = [int(p) for p in parts]
    except ValueError:
        return None
    if any(not 0 <= v <= 1023 for v in values[:6]) or values[6] not in (0, 1):
        return None
    return tuple(values[:6]), values[6]


def update_knobs(ser):
    # Takes everything that has arrived without waiting for a line to finish, and uses the
    # newest valid packet, so the knobs never lag behind. A button press that arrived in an
    # older packet of the same batch is kept, so a short press is not lost.
    # Read errors propagate: the caller logs them and reconnects.
    global GLOBAL_KNOBS, GLOBAL_BUTTON, _serial_synced, _serial_bad_packets
    waiting = ser.in_waiting
    if waiting <= 0:
        return
    _serial_rx.extend(ser.read(waiting))
    *complete, rest = _serial_rx.split(b"\n")
    _serial_rx[:] = rest[-256:]  # a "line" this long is noise: keep only its tail
    if not _serial_synced:
        if not complete:
            return
        complete = complete[1:]  # the first line after a connect may be cut off
        _serial_synced = True

    newest = None
    pressed = False
    for raw in complete:
        line = raw.decode("utf-8", errors="ignore").strip()
        if not line:
            continue
        parsed = _parse_knob_packet(line)
        if parsed is None:
            _serial_bad_packets += 1
            log_throttled(
                logger,
                logging.WARNING,
                "serial-bad-packet",
                60.0,
                "[SERIAL] Rejected a damaged packet: %r (%d rejected since start)",
                line[:40],
                _serial_bad_packets,
            )
            continue
        newest = parsed
        pressed = pressed or parsed[1] == 0

    if newest is not None:
        with hw_state_lock:
            GLOBAL_KNOBS = newest[0]
            GLOBAL_BUTTON = 0 if pressed else newest[1]


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

    def step(self, frame, strength):
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
    _MOSH.step(MUTABLE_PROCESSING_BUFFER, n(raw_knob) ** 2)


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


# =========================================================================
# MELT ENGINE - VECTORIZED
# =========================================================================
# apply_melt_pass() is the entry point. Drip seeds are found without a Python loop
# over columns, and the drips are drawn with a few dozen large NumPy calls per frame
# (many small NumPy calls hold the GIL and would starve the pygame thread; large ones
# release it). Overlapping drips composite in (reach, x0) draw order.


def _collect_melt_drips(
    melt_raw: float, current_time: float, h: int = FRAME_H, w: int = FRAME_W
):
    # Seed discovery without a Python loop over columns.

    # Returns a float64 array of shape (n, 7):
    #     [reach, x0, start_y, L, s, p1, p2]
    # or None when there is nothing to draw.

    max_melt_length = int(MELT_BASE_LENGTH_PX + melt_raw * (h * MELT_LENGTH_FRACTION))
    threshold = max(
        MELT_THRESHOLD_FLOOR, int(255 * (1.0 - melt_raw * MELT_THRESHOLD_FALL))
    )
    stride = (
        MELT_STRIDE_LOW
        if melt_raw < MELT_STRIDE_MID_FROM
        else (MELT_STRIDE_MID if melt_raw < MELT_STRIDE_HIGH_FROM else MELT_STRIDE_HIGH)
    )
    seed_step = max(
        MELT_SEED_STEP_MIN,
        int((MELT_SEED_STEP_ZERO_AT - melt_raw) * MELT_SEED_STEP_MAX),
    )
    max_seeds_per_column = (
        MELT_SEEDS_LOW if melt_raw < MELT_SEEDS_HIGH_FROM else MELT_SEEDS_HIGH
    )
    time_tick = int(current_time * MELT_REROLL_HZ) & 0xFF

    # One threshold over every sampled column at once.
    mask = STATIC_MELT_GRAY_BUFFER[:, 1 : w - 2 : stride] > threshold  # (h, ncols)
    col_idx, ys = np.nonzero(mask.T)  # ordered by column, then by y
    if ys.size == 0:
        return None

    # Position of each hit inside its own column -> every seed_step-th hit,
    # at most max_seeds_per_column per column, nothing in the bottom MELT_BOTTOM_MARGIN_PX rows.
    counts = np.bincount(col_idx, minlength=mask.shape[1])
    first = np.cumsum(counts) - counts
    rank = np.arange(ys.size) - first[col_idx]
    keep = (
        (rank % seed_step == 0)
        & (rank // seed_step < max_seeds_per_column)
        & (ys < h - MELT_BOTTOM_MARGIN_PX)
    )
    col_idx, ys, rank = col_idx[keep], ys[keep], rank[keep]
    if ys.size == 0:
        return None
    seed_rank = rank // seed_step
    xs = 1 + col_idx * stride

    # Order: all rank-0 seeds (by column), then rank-1, ...
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

    # Per-drip pseudo-random parameters (integer hashes of position and time).
    x = xs[sel].astype(np.int64)
    y = ys[sel].astype(np.int64)
    h1 = (x * 73856093) ^ (y * 19349663) ^ (time_tick * 83492791)
    h2 = (x * 2654435761) ^ (y * 2246822519) ^ (time_tick * 3266489917)
    rand_len_mult = (h1 % 1000) / 1000.0
    rand_stretch_mult = ((h1 >> 10) % 1000) / 1000.0
    n_pal = len(NEON_PALETTES)
    p1 = (h2 >> 4) % n_pal
    p2 = (p1 + 1 + ((h2 >> 12) % (n_pal - 1))) % n_pal
    base_len = int(max_melt_length * MELT_LENGTH_FIXED_SHARE)
    rand_len = (
        max_melt_length * (1.0 - MELT_LENGTH_FIXED_SHARE) * rand_len_mult
    ).astype(np.int64)
    L = base_len + rand_len
    s = 1.0 + rand_stretch_mult * (MELT_STRETCH_MAX - 1.0)
    reach = y + L

    live = L > 0
    out = np.column_stack((reach, x, y, L, s, p1, p2)).astype(np.float64)
    return out[live] if not live.all() else out


def _melt_window_view(buf, width: int, writeable: bool):
    # View of a C-contiguous (H, W, 3) uint8 frame where view[y, x] is the
    # 3*width bytes of `width` consecutive pixels starting at column x.
    # Lets one fancy-index gather/scatter move a whole drip row (8 px).
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

    neon_color = (1.0 - MELT_NEON_MIX) * NEON_PALETTES_FLOAT[
        p1
    ] + MELT_NEON_MIX * NEON_PALETTES_FLOAT[p2]  # (n,3)
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
    # Melt entry point (apply_melt has already checked that Melt is acting).
    STATIC_MELT_SNAPSHOT[:] = MUTABLE_PROCESSING_BUFFER[:]
    cv2.cvtColor(STATIC_MELT_SNAPSHOT, cv2.COLOR_BGR2GRAY, dst=STATIC_MELT_GRAY_BUFFER)
    drips = _collect_melt_drips(melt_raw, current_time)
    if drips is not None:
        _render_melt_drips(drips)


# RADICAL COMPUTATION UNROLLED ENGINE
# ==================================================


def n(val):
    return val / 1023.0


# =========================================================================
# EFFECT HANDLERS, ATTACHMENTS, ORDER AND PER-EFFECT KNOB WINDOWS
# =========================================================================
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


def apply_chroma_shift(raw_knob, current_time):
    if not _acts_chroma_shift(raw_knob):
        return
    ana_val = n(raw_knob)
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
            1.0 - CHROMA_GHOST_MIX,
            GHOST_FRAME_BUFFER,
            CHROMA_GHOST_MIX,
            0,
            dst=MUTABLE_PROCESSING_BUFFER,
        )


def apply_slice_glitch(raw_knob, current_time):
    if not _acts_slice_glitch(raw_knob):
        return
    if int(current_time * SLICE_CLOCK_HZ) % SLICE_EVERY_N_STEPS != 0:
        return
    val = n(raw_knob)
    h, w = FRAME_H, FRAME_W
    # Gives a smoother curve where low values yield slices much sooner; the first slice
    # appears as soon as the effect acts (SLICE_ACTIVE_FROM_KNOB)
    num_slices = max(1, int((val**SLICE_COUNT_CURVE) * SLICE_COUNT_MAX))

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
    if not _acts_signal_drift(raw_knob):
        return
    drift = n(raw_knob)
    h, w = MUTABLE_PROCESSING_BUFFER.shape[:2]
    if STATIC_V_BUFFER is None:
        # large enough for the longest roll the tunable allows (at most half the height)
        STATIC_V_BUFFER = np.zeros(
            (h // 2, w) + MUTABLE_PROCESSING_BUFFER.shape[2:],
            dtype=MUTABLE_PROCESSING_BUFFER.dtype,
        )
    if drift > DRIFT_ROLL_ABOVE:
        v_shift = int(
            ((drift - DRIFT_ROLL_ABOVE) / (1.0 - DRIFT_ROLL_ABOVE))
            * (h * DRIFT_ROLL_MAX_FRACTION)
        )
        if v_shift:
            STATIC_V_BUFFER[:v_shift] = MUTABLE_PROCESSING_BUFFER[:v_shift]
            MUTABLE_PROCESSING_BUFFER[:-v_shift] = MUTABLE_PROCESSING_BUFFER[v_shift:]
            MUTABLE_PROCESSING_BUFFER[-v_shift:] = STATIC_V_BUFFER[:v_shift]
            seam, thickness = h - v_shift, int(h * DRIFT_SEAM_FRACTION)
            MUTABLE_PROCESSING_BUFFER[
                max(0, seam - thickness // 2) : min(h, seam + thickness // 2)
            ] = 0
    amount = int(drift * DRIFT_MAX_AMOUNT * DRIFT_AMOUNT_SCALE)
    for i in range(h):
        shift = int(
            math.sin(i * DRIFT_WAVE_PER_ROW + current_time * DRIFT_WAVE_SPEED) * amount
        )
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
    if _acts_melt(raw_knob):
        apply_melt_pass(n(raw_knob), current_time)


def apply_ascii_matrix(raw_knob, current_time):
    global SPARK_COUNTER, SPARK_INTERVAL
    if not _acts_ascii_matrix(raw_knob):
        return
    ascii_val = n(raw_knob)
    cv2.cvtColor(MUTABLE_PROCESSING_BUFFER, cv2.COLOR_BGR2GRAY, dst=ASCII_UPRES_GRAY)
    ASCII_UPRES_GRAY[:] = clahe.apply(ASCII_UPRES_GRAY)
    cv2.resize(
        ASCII_UPRES_GRAY,
        (ASCII_GRID_W, ASCII_GRID_H),
        dst=ASCII_LOWRES_GRAY,
        interpolation=cv2.INTER_NEAREST,
    )
    if ASCII_AUTO_CONTRAST:
        cv2.normalize(ASCII_LOWRES_GRAY, ASCII_LOWRES_GRAY, 0, 255, cv2.NORM_MINMAX)
    np.right_shift(
        ASCII_LOWRES_GRAY, 4, out=ASCII_LOWRES_GRAY
    )  # 16 levels = 16 characters
    np.take(ASCII_TILES, ASCII_LOWRES_GRAY, axis=0, out=ASCII_BLOCK_BUFFER)
    ASCII_UPRES_GRAY[:] = ASCII_BLOCK_BUFFER.transpose(0, 2, 1, 3).reshape(
        FRAME_H, FRAME_W
    )
    for channel, level in enumerate(ASCII_COLOUR):  # (B, G, R)
        if level <= 0:
            ASCII_GREEN_BUFFER[:, :, channel] = 0
        elif level >= 255:
            ASCII_GREEN_BUFFER[:, :, channel] = ASCII_UPRES_GRAY
        else:
            ASCII_GREEN_BUFFER[:, :, channel] = cv2.convertScaleAbs(
                ASCII_UPRES_GRAY, alpha=level / 255.0
            )
    SPARK_COUNTER += 1
    if SPARK_COUNTER >= SPARK_INTERVAL:
        cv2.randu(ASCII_NOISE_LOW, 0, 255)
        rarity_base, rarity_slope, rarity_floor = ASCII_SPARK_RARITY
        threshold = max(
            rarity_floor, min(rarity_base, rarity_base - int(ascii_val * rarity_slope))
        )
        cv2.threshold(
            ASCII_NOISE_LOW, threshold, 255, cv2.THRESH_BINARY, dst=ASCII_NOISE_LOW
        )
        ASCII_NOISE_LOW[ASCII_LOWRES_GRAY == 0] = 0
        cv2.dilate(
            ASCII_NOISE_LOW,
            np.ones(
                (
                    np.random.randint(ASCII_SPARK_LENGTH[0], ASCII_SPARK_LENGTH[1] + 1),
                    1,
                ),
                dtype=np.uint8,
            ),
            dst=ASCII_NOISE_LOW,
        )
        SPARK_COUNTER, SPARK_INTERVAL = (
            0,
            np.random.randint(ASCII_SPARK_INTERVAL[0], ASCII_SPARK_INTERVAL[1] + 1),
        )
    cv2.resize(
        ASCII_NOISE_LOW,
        (FRAME_W, FRAME_H),
        dst=ASCII_NOISE_UP,
        interpolation=cv2.INTER_NEAREST,
    )
    ASCII_GREEN_BUFFER[(ASCII_NOISE_UP > 0) & (ASCII_UPRES_GRAY > 0)] = (
        ASCII_SPARK_COLOUR
    )
    bloom = cv2.resize(
        ASCII_GREEN_BUFFER, (ASCII_GRID_W, ASCII_GRID_H), interpolation=cv2.INTER_LINEAR
    )
    bloom = cv2.resize(
        cv2.GaussianBlur(bloom, (ASCII_BLOOM_BLUR, ASCII_BLOOM_BLUR), 0),
        (FRAME_W, FRAME_H),
        interpolation=cv2.INTER_LINEAR,
    )
    cv2.addWeighted(
        ASCII_GREEN_BUFFER,
        ASCII_TEXT_GAIN,
        bloom,
        ASCII_BLOOM_GAIN,
        0,
        dst=ASCII_GREEN_BUFFER,
    )
    ascii_mix = ascii_val**ASCII_MIX_CURVE
    cv2.addWeighted(
        ASCII_GREEN_BUFFER,
        ascii_mix,
        MUTABLE_PROCESSING_BUFFER,
        1.0 - ascii_mix,
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
# POSTERIZE: NEON TONE BANDS WITH GLITCHY OUTLINES
# =========================================================================
# Colour effect: it does not move pixels. Blacks stay untouched; every other tone becomes a
# neon colour of the Melt palette (dark tones get the deep hues, bright tones the bright ones);
# the borders between tone bands get a 1-3 px outline that is broken up, can be shifted sideways (jitter, off at 0) and flashes,
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


def _ramp_luts(positions, colours_bgr):
    """Blend colour stops into three 256-entry LUTs (B, G, R)."""
    x = np.arange(256, dtype=np.float32)
    colours = np.asarray(colours_bgr, dtype=np.float32)
    out = np.zeros((3, 256, 1), dtype=np.uint8)
    for ch in range(3):
        out[ch, :, 0] = np.clip(np.interp(x, positions, colours[:, ch]), 0, 255)
    return out


class PosterizeEngine:
    """Buffers (about 14 MiB, allocated once) and the per-frame work of Posterize."""

    def __init__(self):
        H, W = FRAME_H, FRAME_W
        hh, hw = H // 2, W // 2
        sp = POSTERIZE_SECTION_PX
        self.rng = np.random.default_rng()
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
        # One dilation kernel for each outline thickness the tunables can ask for.
        self.kernels = {
            px: np.ones((2 * px + 1, 2 * px + 1), dtype=np.uint8)
            for px in {
                POSTERIZE_OUTLINE_MIN_PX,
                POSTERIZE_OUTLINE_MID_PX,
                POSTERIZE_OUTLINE_MAX_PX,
            }
        }
        self.drop_small = z(H // sp, W // sp)
        self.flick_small = z(H // sp, W // sp)
        # Colour of each flash section (neon flashes). Only the sections that flash are written
        # each frame; the mask decides which of them are used, so stale colours never show.
        self.flick_col_small = z3(H // sp, W // sp)
        self.flick_col_full = z3()
        self.palette = _posterize_palette()
        self.knob_zones = self._parse_knob_modes()
        self._mode_x = (
            None  # last knob position the mode selection followed (hysteresis)
        )
        self.curve_x = np.array([p[0] for p in POSTERIZE_CURVE], dtype=np.float64)
        self.curve_y = np.array([p[1] for p in POSTERIZE_CURVE], dtype=np.float64)
        self._lut_key = None
        self.band_lut = np.zeros((256, 1), np.uint8)
        self.color_lut = np.zeros((3, 256, 1), np.uint8)
        # optional looks (about 5 MiB more, allocated once)
        self.grad_lut = np.zeros((3, 256, 1), np.uint8)
        self.therm_lut = np.zeros((3, 256, 1), np.uint8)
        self.neon_half2 = z3(hh, hw)
        self.solar_lut = np.zeros((256, 1), np.uint8)
        self.solar_lut_clear = np.zeros((256, 1), np.uint8)  # the fold while ASCII acts
        self._clear_key = None
        self._ht_key = None
        self.ht_tile = z()
        self.ht_cover, self.ht_mask, self.ht_mask3 = z(), z(), z3()
        self.ht_cover_lut = np.zeros((256, 1), np.uint8)
        self.ht_small = z(2, 2)
        self.ht_gain = 1.0
        self.ht_sel_lut = np.zeros((256, 1), np.uint8)
        self.ht_sel_half, self.ht_sel = z(hh, hw), z()
        self.gl_small = z(H // 4, W // 4)
        self.gl_med, self.gl_edge, self.gl_tmp = z(hh, hw), z(hh, hw), z(hh, hw)
        self.k3 = np.ones((3, 3), dtype=np.uint8)
        self.gl_full, self.gl_full3, self.glow_col = z(), z3(), z3()
        self.glow_solid, self.glow_core_solid = z3(), z3()
        self._glow_key = None

    def strength(self, raw_knob):
        """Knob value 0-1023 -> strength 0-1 through the response curve (0 = off)."""
        return float(np.interp(raw_knob, self.curve_x, self.curve_y))

    def _luts(self, bands, rotation):
        key = (
            bands,
            rotation,
            POSTERIZE_GRADIENT_STOPS,
            POSTERIZE_THERMAL_STOPS,
            POSTERIZE_SOLARIZE_STYLE,
            POSTERIZE_SOLARIZE_POINT,
        )
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
            rolled = np.roll(self.palette, -rotation, axis=0)
            k = int(min(max(2, POSTERIZE_GRADIENT_STOPS), n_pal))
            pick = np.round(np.linspace(0, n_pal - 1, k)).astype(int)
            self.grad_lut[:] = _ramp_luts(np.linspace(0, 255, k), rolled[pick])
            pos = [p for p, _ in POSTERIZE_THERMAL_STOPS]
            col = [c for _, c in POSTERIZE_THERMAL_STOPS]
            self.therm_lut[:] = _ramp_luts(pos, col)
            v = np.arange(256, dtype=np.float32)
            t = float(min(max(POSTERIZE_SOLARIZE_POINT, 16), 239))
            if POSTERIZE_SOLARIZE_STYLE == "invert":
                sol = np.where(v < t, v, 255.0 - v)
            else:
                sol = np.where(v < t, v * 255.0 / t, (255.0 - v) * 255.0 / (255.0 - t))
            self.solar_lut[:, 0] = np.clip(sol, 0, 255).astype(np.uint8)
            self._lut_key = key

    @staticmethod
    def _mode_name_ok(name):
        parts = [p.strip() for p in str(name).split("+")]
        return 1 <= len(parts) <= 2 and all(
            p in ("bands", "gradient", "thermal") for p in parts
        )

    def _parse_knob_modes(self):
        """POSTERIZE_KNOB_MODES -> [(low, high, mode), ...], or None when the table is not usable
        (a WARNING says why; the colour mode is then POSTERIZE_COLOR_MODE)."""
        for name in [POSTERIZE_COLOR_MODE] + [m for m, _d in POSTERIZE_MODE_SCHEDULE]:
            if not self._mode_name_ok(name):
                logger.warning(
                    "[CONFIG] Posterize: %r is not a colour mode (bands, gradient, thermal, or two joined by +); it is drawn as 'bands'",
                    name,
                )
        if POSTERIZE_MODE_SOURCE not in ("knob", "schedule", "fixed"):
            logger.warning(
                "[CONFIG] POSTERIZE_MODE_SOURCE=%r must be 'knob', 'schedule' or 'fixed'; 'fixed' is used",
                POSTERIZE_MODE_SOURCE,
            )
        if POSTERIZE_MODE_SOURCE != "knob":
            return None
        zones = []
        try:
            for entry in POSTERIZE_KNOB_MODES:
                low, high, mode = float(entry[0]), float(entry[1]), entry[2]
                if not (0.0 <= low < high <= 1.0):
                    raise ValueError(
                        f"{entry!r}: needs 0 <= lower edge < upper edge <= 1"
                    )
                if not self._mode_name_ok(mode):
                    raise ValueError(f"{entry!r}: unknown mode {mode!r}")
                zones.append((low, high, mode))
            if not zones:
                raise ValueError("the table is empty")
            if min(z[0] for z in zones) > 0.0 or max(z[1] for z in zones) < 1.0:
                logger.warning(
                    "[CONFIG] POSTERIZE_KNOB_MODES does not cover the whole knob (0 to 1); the nearest zone is used outside it"
                )
        except Exception as exc:
            logger.warning(
                "[CONFIG] POSTERIZE_KNOB_MODES is not usable (%s); POSTERIZE_COLOR_MODE %r is used",
                exc,
                POSTERIZE_COLOR_MODE,
            )
            return None
        return zones

    @staticmethod
    def _add_mode(out, name, weight):
        """Adds `name` (a mode, or "a+b" = two modes shown mixed) with `weight` to out."""
        if weight <= 0:
            return
        parts = [p.strip() for p in name.split("+")][:2]
        if len(parts) == 2:
            m = min(max(POSTERIZE_OVERLAY_MIX, 0.0), 1.0)
            shares = [(parts[0], 1.0 - m), (parts[1], m)]
        else:
            shares = [(parts[0], 1.0)]
        for p, sh in shares:
            if sh > 0:
                out[p] = out.get(p, 0.0) + weight * sh

    def _knob_weights(self, x):
        """Knob position x (0-1) -> {mode: weight}. One zone = that mode. Overlapping zones are
        blended: a zone's weight is how far x is from the nearest edge of that zone."""
        hyst = POSTERIZE_KNOB_MODE_HYSTERESIS
        if self._mode_x is not None and abs(x - self._mode_x) < hyst:
            x = self._mode_x
        else:
            self._mode_x = x
        inside = [z for z in self.knob_zones if z[0] <= x <= z[1]]
        out = {}
        if not inside:  # a gap or outside the table: the nearest edge decides
            inside = [
                min(self.knob_zones, key=lambda z: min(abs(x - z[0]), abs(x - z[1])))
            ]
            self._add_mode(out, inside[0][2], 1.0)
        elif len(inside) == 1:
            self._add_mode(out, inside[0][2], 1.0)
        else:
            depth = [min(x - z[0], z[1] - x) for z in inside]
            total = sum(depth)
            if total <= 0:  # exactly on a shared edge: the first zone
                self._add_mode(out, inside[0][2], 1.0)
            else:
                for z, d in zip(inside, depth):
                    self._add_mode(out, z[2], d / total)
        return list(out.items()) or [("bands", 1.0)]

    def _mode_weights(self, t, knob_x=0.0):
        """-> [(mode, weight), ...]: what to show now (knob zones, schedule, cross-fade, a+b overlays)."""
        if self.knob_zones is not None:
            return self._knob_weights(knob_x)
        sched = POSTERIZE_MODE_SCHEDULE if POSTERIZE_MODE_SOURCE == "schedule" else ()
        if not sched:
            cur, nxt, w = POSTERIZE_COLOR_MODE, None, 0.0
        else:
            durs = [max(0.001, float(d)) for _, d in sched]
            pos = t % sum(durs)
            i = 0
            while i < len(durs) - 1 and pos >= durs[i]:
                pos -= durs[i]
                i += 1
            cur, nxt, w = sched[i][0], None, 0.0
            left = durs[i] - pos
            fade = POSTERIZE_MODE_FADE_SECONDS
            if fade > 0 and len(sched) > 1 and left < fade:
                nxt = sched[(i + 1) % len(sched)][0]
                w = 1.0 - left / fade
        out = {}
        self._add_mode(out, cur, 1.0 - w)
        if nxt is not None:
            self._add_mode(out, nxt, w)
        return list(out.items()) or [("bands", 1.0)]

    def _fill(self, mode, dst, detail):
        """Neon fill (half resolution) of one colour mode into dst."""
        if mode == "gradient":
            src, lut = self.g_half, self.grad_lut
        elif mode == "thermal":
            src, lut = self.g_half, self.therm_lut
        else:  # "bands"
            src, lut = self.band_half, self.color_lut
        cv2.LUT(src, lut[0], dst=self.pl_b)
        cv2.LUT(src, lut[1], dst=self.pl_g)
        cv2.LUT(src, lut[2], dst=self.pl_r)
        cv2.merge([self.pl_b, self.pl_g, self.pl_r], dst=dst)
        if src is self.band_half:  # bands keep the brightness modulation
            cv2.convertScaleAbs(
                self.g_half,
                dst=self.scale_half,
                alpha=detail,
                beta=255.0 * (1.0 - detail),
            )
            cv2.merge(
                [self.scale_half, self.scale_half, self.scale_half], dst=self.scale3
            )
            cv2.multiply(dst, self.scale3, dst=dst, scale=1.0 / 255.0)

    def _build_halftone(self):
        """Dot-screen threshold tile (static) + tone curve; rebuilt only when changed."""
        H, W = FRAME_H, FRAME_W
        pitch = max(4, int(POSTERIZE_HALFTONE_PITCH))
        key = (
            pitch,
            POSTERIZE_HALFTONE_ANGLE,
            POSTERIZE_HALFTONE_SOFTNESS,
            POSTERIZE_HALFTONE_COVER_GAIN,
        )
        if key == self._ht_key:
            return
        yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
        a = np.deg2rad(POSTERIZE_HALFTONE_ANGLE)
        u = (xx * np.cos(a) + yy * np.sin(a)) / pitch
        v = (-xx * np.sin(a) + yy * np.cos(a)) / pitch
        d = np.sqrt((u - np.floor(u) - 0.5) ** 2 + (v - np.floor(v) - 0.5) ** 2)
        d /= 0.7071  # 0 = dot centre, 1 = cell corner
        self.ht_tile[:] = np.clip(d * 235.0, 0, 255).astype(np.uint8)
        t = np.arange(256, dtype=np.float32) / 255.0
        # sqrt: dot AREA follows brightness; a gain above 1 lets the highlights close up fully
        self.ht_cover_lut[:, 0] = (
            255.0 * np.sqrt(np.clip(t * POSTERIZE_HALFTONE_COVER_GAIN, 0, 1))
        ).astype(np.uint8)
        self.ht_small = np.zeros((max(2, H // pitch), max(2, W // pitch)), np.uint8)
        # ramp width (grey levels) = softness in px * levels per px
        self.ht_gain = (
            255.0 * pitch * 0.7071 / (235.0 * max(0.3, POSTERIZE_HALFTONE_SOFTNESS))
        )
        self._ht_key = key

    def _halftone(self, bands):
        """Turns self.neon_full into a dot screen (coloured dots, dark gaps)."""
        H, W = FRAME_H, FRAME_W
        self._build_halftone()
        sh, sw = self.ht_small.shape
        cv2.resize(
            self.g_half, (sw, sh), dst=self.ht_small, interpolation=cv2.INTER_AREA
        )
        cv2.resize(
            self.ht_small, (W, H), dst=self.ht_cover, interpolation=cv2.INTER_LINEAR
        )
        cv2.LUT(self.ht_cover, self.ht_cover_lut, dst=self.ht_cover)
        cv2.subtract(self.ht_cover, self.ht_tile, dst=self.ht_mask)
        cv2.convertScaleAbs(
            self.ht_mask,
            dst=self.ht_mask,
            alpha=self.ht_gain,
            beta=255.0 * POSTERIZE_HALFTONE_GAP,
        )
        if (
            POSTERIZE_HALFTONE_TONE_FROM > 0.0
            or POSTERIZE_HALFTONE_TONE_TO < 1.0
            or POSTERIZE_HALFTONE_ALTERNATE
        ):  # only some bands get dots; the others are forced to full (solid) fill
            lut = self.ht_sel_lut
            lut[:] = 0
            for b in range(bands):
                pos = b / max(1, bands - 1)
                if (
                    POSTERIZE_HALFTONE_TONE_FROM <= pos <= POSTERIZE_HALFTONE_TONE_TO
                    and not (POSTERIZE_HALFTONE_ALTERNATE and b % 2)
                ):
                    lut[b, 0] = 255
            cv2.LUT(self.band_half, lut, dst=self.ht_sel_half)
            cv2.resize(
                self.ht_sel_half,
                (W, H),
                dst=self.ht_sel,
                interpolation=cv2.INTER_NEAREST,
            )
            cv2.bitwise_not(self.ht_sel, dst=self.ht_sel)
            cv2.bitwise_or(self.ht_mask, self.ht_sel, dst=self.ht_mask)
        cv2.merge([self.ht_mask, self.ht_mask, self.ht_mask], dst=self.ht_mask3)
        cv2.multiply(
            self.neon_full, self.ht_mask3, dst=self.neon_full, scale=1.0 / 255.0
        )

    def _edge_glow(self, px):
        """Halo (+ neon core) from the final outline mask in self.edge_tmp."""
        H, W = FRAME_H, FRAME_W
        palette_mode = POSTERIZE_GLOW_COLOR == "palette"
        key = (POSTERIZE_GLOW_COLOR, POSTERIZE_GLOW_CORE_LIFT)
        if not palette_mode and key != self._glow_key:
            col = np.array(POSTERIZE_GLOW_COLOR, dtype=np.float32)
            lift = POSTERIZE_GLOW_CORE_LIFT
            self.glow_solid[:] = col.astype(np.uint8)
            self.glow_core_solid[:] = (col * (1.0 - lift) + 255.0 * lift).astype(
                np.uint8
            )  # lighter core
            self._glow_key = key
        # halo source = only the BIG tone borders (speckle removed), found at half resolution,
        # so busy high-strength outlines do not flood the picture with light
        cv2.medianBlur(self.band_half, POSTERIZE_GLOW_CLEAN, dst=self.gl_med)
        cv2.dilate(self.gl_med, self.k3, dst=self.gl_tmp)
        cv2.compare(self.gl_tmp, self.gl_med, cv2.CMP_NE, dst=self.gl_edge)
        # halo at quarter resolution: shrink, blur, boost, grow
        cv2.resize(
            self.gl_edge,
            (W // 4, H // 4),
            dst=self.gl_small,
            interpolation=cv2.INTER_AREA,
        )
        cv2.GaussianBlur(
            self.gl_small,
            (0, 0),
            max(0.5, POSTERIZE_GLOW_RADIUS_PX / 4.0),
            dst=self.gl_small,
        )
        cv2.convertScaleAbs(self.gl_small, dst=self.gl_small, alpha=POSTERIZE_GLOW_GAIN)
        cv2.resize(
            self.gl_small, (W, H), dst=self.gl_full, interpolation=cv2.INTER_LINEAR
        )
        if POSTERIZE_GLOW_KEEP_BLACKS:
            cv2.bitwise_and(self.gl_full, self.nonblack, dst=self.gl_full)
        cv2.merge([self.gl_full, self.gl_full, self.gl_full], dst=self.gl_full3)
        if palette_mode:
            cv2.multiply(
                self.gl_full3, self.neon_full, dst=self.glow_col, scale=1.0 / 255.0
            )
        else:
            cv2.multiply(
                self.gl_full3, self.glow_solid, dst=self.glow_col, scale=1.0 / 255.0
            )
        cv2.addWeighted(
            self.poster, POSTERIZE_GLOW_DIM, self.glow_col, 1.0, 0, dst=self.poster
        )
        if POSTERIZE_GLOW_CORE:
            if palette_mode:
                cv2.dilate(self.neon_full, self.kernels[px], dst=self.edge_col)
                core = self.edge_col
            else:
                core = self.glow_core_solid
            cv2.copyTo(core, self.edge_tmp, self.poster)

    def step(self, frame, raw_knob, current_time, outline_style=None, clarity=0.0):
        # clarity: 0-1, how much of the picture's readability is protected (see
        # POSTERIZE_ASCII_CLARITY). apply_posterize passes it only while ASCII acts.
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

        clarity = min(max(float(clarity), 0.0), 1.0)
        # Brightness detail of the "bands" fill (the gradient and thermal fills do not use it):
        # from DETAIL_LOW at the weakest strength to DETAIL_HIGH at full strength, and under
        # clarity it falls only (1 - clarity) as far.
        detail = POSTERIZE_DETAIL_LOW + (
            (POSTERIZE_DETAIL_HIGH - POSTERIZE_DETAIL_LOW) * s * (1.0 - clarity)
        )

        mix = POSTERIZE_MIX_START + (1.0 - POSTERIZE_MIX_START) * min(
            1.0, s / POSTERIZE_MIX_FULL_AT
        )
        px = (
            POSTERIZE_OUTLINE_MAX_PX
            if s >= POSTERIZE_OUTLINE_MAX_FROM
            else (
                POSTERIZE_OUTLINE_MID_PX
                if s >= POSTERIZE_OUTLINE_MID_FROM
                else POSTERIZE_OUTLINE_MIN_PX
            )
        )
        dropout = (
            POSTERIZE_DROPOUT_LOW + (POSTERIZE_DROPOUT_HIGH - POSTERIZE_DROPOUT_LOW) * s
        )
        dropout += clarity * POSTERIZE_ASCII_CLARITY_DROPOUT * (1.0 - dropout)
        jitter = int(
            round(s * POSTERIZE_JITTER_MAX_PX)
        )  # 0 = the sideways shift is skipped
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
        if POSTERIZE_SOLARIZE:  # fold the tone curve before bands and colours
            lut = self.solar_lut
            if clarity > 0.0:
                # the turning point of the fold moves from POSTERIZE_SOLARIZE_POINT toward 239
                if self._clear_key != (clarity, self._lut_key):
                    t0 = float(min(max(POSTERIZE_SOLARIZE_POINT, 16), 239))
                    t = t0 + clarity * (239.0 - t0)
                    v = np.arange(256, dtype=np.float32)
                    if POSTERIZE_SOLARIZE_STYLE == "invert":
                        sol = np.where(v < t, v, 255.0 - v)
                    else:
                        sol = np.where(
                            v < t, v * 255.0 / t, (255.0 - v) * 255.0 / (255.0 - t)
                        )
                    self.solar_lut_clear[:, 0] = np.clip(sol, 0, 255).astype(np.uint8)
                    self._clear_key = (clarity, self._lut_key)
                lut = self.solar_lut_clear
            cv2.LUT(self.g_half, lut, dst=self.g_half)
        cv2.LUT(self.g_half, self.band_lut, dst=self.band_half)

        # 2. neon fill: band -> palette colour, modulated by the original brightness
        parts = self._mode_weights(current_time, raw_knob / 1023.0)
        self._fill(parts[0][0], self.neon_half, detail)
        acc = parts[0][1]
        for mode, w in parts[1:]:  # cross-fade / overlay: blend further fills in
            self._fill(mode, self.neon_half2, detail)
            tot = acc + w
            cv2.addWeighted(
                self.neon_half,
                acc / tot,
                self.neon_half2,
                w / tot,
                0,
                dst=self.neon_half,
            )
            acc = tot
        cv2.resize(
            self.neon_half, (W, H), dst=self.neon_full, interpolation=cv2.INTER_NEAREST
        )
        cv2.resize(
            self.band_half, (W, H), dst=self.band_full, interpolation=cv2.INTER_NEAREST
        )
        if POSTERIZE_HALFTONE and s >= POSTERIZE_HALFTONE_FROM:  # dot screen
            self._halftone(bands)

        # 3. mix with the original; blacks untouched, a narrow ramp mixes half and half
        cv2.addWeighted(frame, 1.0 - mix, self.neon_full, mix, 0, dst=self.poster)
        cv2.compare(self.gray, POSTERIZE_BLACK_THRESHOLD, cv2.CMP_LT, dst=self.black)
        cv2.compare(self.gray, POSTERIZE_BLACK_RAMP, cv2.CMP_LT, dst=self.ramp)
        cv2.bitwise_xor(self.ramp, self.black, dst=self.ramp)
        cv2.addWeighted(
            frame,
            1.0 - POSTERIZE_BLACK_RAMP_MIX,
            self.poster,
            POSTERIZE_BLACK_RAMP_MIX,
            0,
            dst=self.avg,
        )
        cv2.copyTo(self.avg, self.ramp, self.poster)
        cv2.copyTo(frame, self.black, self.poster)
        cv2.bitwise_not(self.black, dst=self.nonblack)

        # 4. outlines on the tone borders: broken sections, sideways jitter, flashing sections
        cv2.dilate(self.band_full, self.kernels[px], dst=self.edge_tmp)
        cv2.compare(self.edge_tmp, self.band_full, cv2.CMP_NE, dst=self.edge)
        self.drop_small[:] = (self.rng.random(self.drop_small.shape) >= dropout) * 255
        cv2.resize(
            self.drop_small, (W, H), dst=self.drop_full, interpolation=cv2.INTER_NEAREST
        )
        cv2.bitwise_and(self.edge, self.drop_full, dst=self.edge)
        if jitter > 0:
            offsets = np.repeat(
                self.rng.integers(-jitter, jitter + 1, H // 4), 4
            ).astype(np.float32)
            np.add(self.xs, offsets[:, None], out=self.mapx)
            cv2.remap(
                self.edge, self.mapx, self.mapy, cv2.INTER_NEAREST, dst=self.edge_tmp
            )
            cv2.bitwise_and(self.edge_tmp, self.nonblack, dst=self.edge_tmp)
        else:  # no sideways shift: the outline is the edge itself (same picture, no remap)
            cv2.bitwise_and(self.edge, self.nonblack, dst=self.edge_tmp)

        style = outline_style or POSTERIZE_OUTLINE_STYLE

        if style == "glow":
            cv2.dilate(self.neon_full, self.kernels[px], dst=self.edge_col)
            outline_colour = self.edge_col
        elif style == "black":
            outline_colour = self.ink
        else:
            outline_colour = self.white
        cv2.copyTo(outline_colour, self.edge_tmp, self.poster)
        if POSTERIZE_EDGE_GLOW and s >= POSTERIZE_GLOW_FROM:  # halo + neon core
            self._edge_glow(px)
        if POSTERIZE_FLICKER_ENABLED and POSTERIZE_FLICKER_PROB > 0:
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
            flash_colour = self.white
            if POSTERIZE_FLICKER_COLOUR == "neon":
                lit = np.flatnonzero(
                    self.flick_small
                )  # the sections that flash this frame
                if lit.size:
                    cells = self.flick_col_small.reshape(-1, 3)  # a view, not a copy
                    cells[lit] = self.palette[
                        self.rng.integers(0, len(self.palette), lit.size)
                    ]
                    cv2.resize(
                        self.flick_col_small,
                        (W, H),
                        dst=self.flick_col_full,
                        interpolation=cv2.INTER_NEAREST,
                    )
                    flash_colour = self.flick_col_full
            cv2.copyTo(flash_colour, self.wmask, self.poster)

        np.copyto(frame, self.poster)


_POSTER = PosterizeEngine() if "posterize" not in DISABLED_EFFECTS else None


def apply_posterize(raw_knob, current_time, ascii_active=False):
    """Posterize entry point. raw_knob 0-1023."""
    # While the ASCII effect is acting (on whichever knob it is attached to, in any processing
    # order) the outlines glow; otherwise they use POSTERIZE_OUTLINE_STYLE.
    outline_style = (
        POSTERIZE_ASCII_OUTLINE_STYLE if ascii_active else POSTERIZE_OUTLINE_STYLE
    )
    clarity = POSTERIZE_ASCII_CLARITY if ascii_active else 0.0
    _POSTER.step(
        MUTABLE_PROCESSING_BUFFER, raw_knob, current_time, outline_style, clarity
    )


# =========================================================================
# EFFECT ACTIVITY: acting(effect, raw_knob)
# =========================================================================
# One question, one answer: does this effect really change the picture at this knob value?
# raw_knob is 0-1023 after the effect's knob window. The handlers use these functions as their
# early return, the statistics use acting() (get_active_effects) and Posterize uses it to see
# whether ASCII is on. The numbers are the tunables in EFFECT ACTIVITY and each effect's own
# tunables, so there is no second copy that can drift.


def _acts_pixellation(raw):
    return n(raw) > PIXELLATION_ACTIVE_ABOVE


def _acts_ascii_matrix(raw):
    return n(raw) > ASCII_ACTIVE_ABOVE


def _acts_melt(raw):
    return n(raw) > MELT_ACTIVE_ABOVE


def _acts_chroma_shift(raw):
    return n(raw) > CHROMA_ACTIVE_ABOVE


def _acts_slice_glitch(raw):
    return raw >= SLICE_ACTIVE_FROM_KNOB


def _acts_data_mosh(raw):
    # Strength is the knob squared; below MOSH_DORMANT_BELOW every mosh is cancelled at once.
    return n(raw) ** 2 >= MOSH_DORMANT_BELOW


def _acts_signal_drift(raw):
    # The sideways shift is int(sin * amount), which is 0 for an amount below 2; the vertical
    # roll near the top of the knob acts whatever the amount is.
    drift = n(raw)
    if drift > DRIFT_ROLL_ABOVE:
        return True
    return int(drift * DRIFT_MAX_AMOUNT * DRIFT_AMOUNT_SCALE) >= DRIFT_MIN_AMOUNT


def _acts_packet_corruption(raw):
    # New damage only spawns above CORRUPTION_SPAWN_ABOVE. The handler itself keeps its own
    # gate (CORRUPTION_CLEAR_BELOW), because that one also clears old damage.
    return n(raw) > CORRUPTION_SPAWN_ABOVE


def _acts_posterize(raw):
    # The first non-zero point of POSTERIZE_CURVE (the same curve the effect uses).
    xs = [p[0] for p in POSTERIZE_CURVE]
    ys = [p[1] for p in POSTERIZE_CURVE]
    return float(np.interp(raw, xs, ys)) > 0.0


_ACTING = {
    "pixellation": _acts_pixellation,
    "ascii_matrix": _acts_ascii_matrix,
    "melt": _acts_melt,
    "chroma_shift": _acts_chroma_shift,
    "slice_glitch": _acts_slice_glitch,
    "data_mosh": _acts_data_mosh,
    "signal_drift": _acts_signal_drift,
    "packet_corruption": _acts_packet_corruption,
    "posterize": _acts_posterize,
}


def acting(effect, raw_knob):
    # True when `effect` really changes the picture at this knob value (0-1023, after its
    # window). An effect with no entry in _ACTING (a startup warning says so) counts from knob 1.
    check = _ACTING.get(effect)
    return check(raw_knob) if check is not None else raw_knob > 0


EFFECT_HANDLERS = {
    "pixellation": apply_pixellation,
    "posterize": apply_posterize,
    "chroma_shift": apply_chroma_shift,
    "slice_glitch": apply_slice_glitch,
    "signal_drift": apply_signal_drift,
    "melt": apply_melt,
    "data_mosh": apply_data_mosh,
    "packet_corruption": apply_packet_corruption,
    "ascii_matrix": apply_ascii_matrix,
}
_EFFECT_PLAN = []
_KNOB_LABELS = [KNOB_EMPTY_LABEL] * 6
_KNOB_FAILED = [False] * 6  # True: an effect on this knob was switched off at startup
_ASCII_ATTACHMENT = None  # (knob, window) of the ASCII effect while it is in the plan


def rebuild_effect_plan():
    global _EFFECT_PLAN, _KNOB_LABELS, _KNOB_FAILED, _ASCII_ATTACHMENT
    notes, order, knob_of = [], [], {}
    for effect in EFFECT_ORDER:
        if effect in DISABLED_EFFECTS:
            continue  # switched off by the startup check, which already reported it
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
            if effect in DISABLED_EFFECTS:
                continue
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
        if effect in DISABLED_EFFECTS:
            continue
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
    _ASCII_ATTACHMENT = next(
        (
            (knob, window)
            for effect, _h, knob, window, _s in _EFFECT_PLAN
            if effect == "ascii_matrix"
        ),
        None,
    )
    # The label names the first effect attached to the knob, including one that the startup
    # check switched off, so the panel can show its name with a warning.
    knob_chain = {
        k: [
            e
            for e in (KNOB_ATTACHMENTS.get(k, []) if k in valid_knobs else [])
            if e in DISABLED_EFFECTS or e in by_knob[k]
        ]
        for k in range(6)
    }
    _KNOB_LABELS = [
        EFFECT_LABELS.get(knob_chain[k][0], knob_chain[k][0])
        if knob_chain[k]
        else KNOB_EMPTY_LABEL
        for k in range(6)
    ]
    _KNOB_FAILED = [any(e in DISABLED_EFFECTS for e in knob_chain[k]) for k in range(6)]
    for note in notes:
        logger.warning("[CONFIG] Effect configuration: %s", note)


def get_knob_labels():
    return list(_KNOB_LABELS)


def _ascii_is_active(knobs):
    # True while the ASCII effect is acting: it is in the plan (not switched off at startup) and
    # the knob it is attached to, after its window, is where ASCII acts (acting()). Posterize
    # uses this to switch its outlines to glow, whichever knob ASCII is on and in any order.
    if _ASCII_ATTACHMENT is None:
        return False
    knob, window = _ASCII_ATTACHMENT
    return acting("ascii_matrix", _windowed_raw(knobs[knob], window))


def get_knob_failures():
    # One flag per knob: True when an effect attached to it was switched off at startup.
    return list(_KNOB_FAILED)


def _windowed_raw(raw_knob, window):
    lo, hi = window
    if lo <= 0 and hi >= 1:
        return raw_knob
    x = (raw_knob / 1023.0 - lo) / (hi - lo)
    return 0.0 if x <= 0.0 else 1023.0 if x >= 1.0 else x * 1023.0


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


def _check_effect_tables():
    # An effect missing from one of these tables silently falls back to a default
    # (cost weight 0, its raw name as label or statistics name, activity from knob 1).
    for table_name in (
        "EFFECT_COST_WEIGHT",
        "EFFECT_LABELS",
        "EFFECT_STATS_NAME",
        "_ACTING",
    ):
        table = globals()[table_name]
        for effect in EFFECT_HANDLERS:
            if effect not in table:
                logger.warning(
                    "[CONFIG] %s has no entry for %r; a default is used",
                    table_name,
                    effect,
                )


_check_effect_tables()


def get_active_effects(knobs):
    # Statistics names of the attached effects that really act at these knob values
    # (after their knob windows), in processing order. Hidden (secondary) effects count too.
    names = []
    for effect, _handler, knob, window, _skip in _EFFECT_PLAN:
        raw = knobs[knob] if knob < len(knobs) else 0
        windowed = _windowed_raw(raw, window)
        if windowed >= EFFECT_STATS_MIN_KNOB and acting(effect, windowed):
            names.append(EFFECT_STATS_NAME.get(effect, effect))
    return names


def get_wiring_label():
    # Compact text of the current attachments (primary first) and windows, stored with every
    # capture, e.g. 0:ascii_matrix|1:signal_drift|...|5:chroma_shift+packet_corruption|w:packet_corruption=0.35-1
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
    global WARMUP_COUNTER
    is_warming_up = WARMUP_COUNTER < WARMUP_FRAMES
    if is_warming_up:
        WARMUP_COUNTER += 1
    for _effect, handler, knob, window, skip in _EFFECT_PLAN:
        if not (skip and is_warming_up):
            if _effect == "posterize":
                ascii_active = _ascii_is_active(control_packet.knobs)
                handler(
                    _windowed_raw(control_packet.knobs[knob], window),
                    current_time,
                    ascii_active,
                )
            else:
                handler(
                    _windowed_raw(control_packet.knobs[knob], window),
                    current_time,
                )
    apply_crt_overlay()


# HARDWARE OUTPUT EXECUTION STATIONS
# ==================================================


def _write_naked_capture(disk_id, frame):
    # Write the clean (unprocessed) frame without affecting capture success. Encoded in memory,
    # written through a temp file and renamed (no fsync: the clean photo is optional).
    if frame is None:
        return False
    try:
        ok, buf = cv2.imencode(
            ".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), CLEAN_JPEG_QUALITY]
        )
        if not ok:
            logger.error(
                "[CLEAN] could not encode clean capture for session %s", disk_id
            )
            return False
        atomic_write_bytes(
            os.path.join(NAKED_DIR, f"{disk_id}_clean.jpg"), buf.tobytes()
        )
        return True
    except Exception as exc:
        logger.error("[CLEAN] clean capture failed for session %s: %s", disk_id, exc)
        return False


_cleanup_guard = threading.Lock()
_COLOR_FILE_RE = re.compile(r"([0-9]{1,20})_color\.jpg")
_CLEAN_FILE_RE = re.compile(r"([0-9]{1,20})_clean\.jpg")
# Clean photos that piled up before they followed their capture into naked_archive are moved
# in small steps (this many per cleanup pass), so no single capture's cleanup is slowed down.
NAKED_SWEEP_MAX_PER_PASS = 200
_naked_sweep_done = False


def _protected_capture_ids():
    # Captures that must stay where they are: still pending, being uploaded, the active session,
    # queued or running in the print worker. Each snapshot is taken briefly and on its own.
    protected = {entry.id for entry in ledger_snapshot()}
    protected |= inflight_snapshot()
    with mode_lock:
        active = ACTIVE_SESSION["session_id"]
    if active:
        protected.add(str(active))
    with _print_ids_lock:
        protected |= set(_print_ids)
    return protected


def _archive_file(src, dst):
    # Copy to a temp name in the archive, fsync, os.replace over the destination, then remove
    # the source. Any failure leaves the source and the old archive copy untouched.
    tmp = f"{dst}.{os.getpid()}.{threading.get_ident()}.tmp"
    try:
        shutil.copyfile(src, tmp)
        _fsync_file(tmp)
        os.replace(tmp, dst)
        _fsync_dir(dst)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
    os.remove(src)


def _move_clean_to_archive(ts):
    # Moves <ts>_clean.jpg from captures_naked/ to naked_archive/. Returns True when it moved,
    # False when there was no clean file or the move failed (a failure is logged and the file
    # stays where it is).
    src = os.path.join(NAKED_DIR, f"{ts}_clean.jpg")
    if not os.path.exists(src):
        return False  # older captures have none: normal
    try:
        _archive_file(src, os.path.join(NAKED_ARCHIVE_DIR, f"{ts}_clean.jpg"))
        return True
    except Exception as move_err:
        logger.warning(
            "[ARCHIVE] Could not move clean photo %s_clean.jpg to %s: %s (left in place)",
            ts,
            NAKED_ARCHIVE_DIR,
            move_err,
        )
        return False


def _sweep_naked_backlog():
    # Clean photos whose colour file is no longer in captures/ (archived before clean photos
    # followed their capture, or the colour file never got written) move to naked_archive/,
    # except those of protected captures. At most NAKED_SWEEP_MAX_PER_PASS per call; it keeps
    # being called by the cleanup until nothing is left, then it stops for this run.
    global _naked_sweep_done
    if _naked_sweep_done:
        return
    try:
        names = sorted(os.listdir(NAKED_DIR))
    except OSError as exc:
        logger.warning("[ARCHIVE] Could not list %s: %s", NAKED_DIR, exc)
        return
    protected = None
    moved = failed = 0
    more = False
    for name in names:
        match = _CLEAN_FILE_RE.fullmatch(name)
        if match is None:
            continue
        ts = match.group(1)
        if os.path.exists(os.path.join(CAPTURE_DIR, f"{ts}_color.jpg")):
            continue  # its capture is still in captures/: it goes with it later
        if protected is None:
            protected = _protected_capture_ids()
        if ts in protected:
            continue
        if moved + failed >= max(1, int(NAKED_SWEEP_MAX_PER_PASS)):
            more = True
            break
        if _move_clean_to_archive(ts):
            moved += 1
        else:
            failed += 1
    if not more:
        _naked_sweep_done = True
    if moved or failed:
        logger.info(
            "[ARCHIVE] Clean photo backlog: moved %d to %s, %d could not be moved%s",
            moved,
            NAKED_ARCHIVE_DIR,
            failed,
            ", more to follow" if more else "",
        )


def cleanup_sessions(limit=None):
    if limit is None:
        limit = CAPTURE_KEEP_COUNT
    if not _cleanup_guard.acquire(blocking=False):
        return  # another cleanup is running
    try:
        _sweep_naked_backlog()
        ids = sorted(
            (
                m.group(1)
                for f in os.listdir(CAPTURE_DIR)
                if (m := _COLOR_FILE_RE.fullmatch(f))
            ),
            key=int,
        )
        if len(ids) <= limit:
            return
        # A capture that was just written is always among the newest CAPTURE_KEEP_COUNT, and only
        # captures older than those are archived, so a fresh capture cannot be archived before it is
        # registered. The protection set matters for OLD captures that are still pending.
        protected = _protected_capture_ids()
        archived = skipped = clean_moved = 0
        for ts in ids[:-limit]:
            if ts in protected:
                skipped += 1
                continue
            name = f"{ts}_color.jpg"
            try:
                _archive_file(
                    os.path.join(CAPTURE_DIR, name), os.path.join(ARCHIVE_DIR, name)
                )
            except Exception as move_err:
                logger.error(
                    "[ARCHIVE] Failed to archive %s: %s (left in place)", name, move_err
                )
                continue
            archived += 1
            # The clean photo follows its colour file; if that move fails it stays in
            # captures_naked/ and the backlog sweep tries again at the next start.
            if _move_clean_to_archive(ts):
                clean_moved += 1
            # The other files of a capture go only together with its colour file.
            for path in (
                os.path.join(CAPTURE_DIR, f"{ts}_bw.jpg"),
                os.path.join(SAVED_DIR, f"{ts}_qr.png"),
                os.path.join(CAPTURE_DIR, f"{ts}_composite.png"),
            ):
                try:
                    os.remove(path)
                except FileNotFoundError:
                    pass
                except OSError as rm_err:
                    logger.warning("[ARCHIVE] Could not remove %s: %s", path, rm_err)
        if archived or skipped:
            logger.info(
                "[ARCHIVE] Relocated %d excess captures to %s and %d clean photos to %s (kept newest %d, %d left in place because they are pending, uploading, active or printing)",
                archived,
                ARCHIVE_DIR,
                clean_moved,
                NAKED_ARCHIVE_DIR,
                limit,
                skipped,
            )
    except Exception:
        logger.exception("[ARCHIVE] Archival pipeline exception")
    finally:
        _cleanup_guard.release()


def _save_qr_png(url, path):
    # Encoded in memory, then written through a temp file and renamed.
    buf = io.BytesIO()
    qrcode.make(url).save(buf, format="PNG")
    atomic_write_bytes(path, buf.getvalue())


def prepare_receipt_assets(ts):
    # QR code and lossless composite of the receipt, made before the print starts.
    # Returns True when the composite is ready; otherwise the print falls back to the plain photo.
    try:
        _save_qr_png(get_landing_url(ts), os.path.join(SAVED_DIR, f"{ts}_qr.png"))
    except Exception as qr_err:
        logger.error("[CAPTURE] QR creation failed for session %s: %s", ts, qr_err)
        return False
    if generate_composite_receipt_image(ts):
        return True
    logger.warning(
        "[CAPTURE] Composite preparation failed for %s; the print will fall back.", ts
    )
    return False


def _write_capture_image(path, mat):
    # Encoded in memory, written through a temp file and renamed; one retry. No fsync here:
    # that happens after the print is queued (see _store_capture).
    for attempt in (1, 2):
        try:
            ok, buf = cv2.imencode(
                ".jpg", mat, [int(cv2.IMWRITE_JPEG_QUALITY), CAPTURE_JPEG_QUALITY]
            )
            if ok:
                atomic_write_bytes(path, buf.tobytes())
                return True
        except OSError as exc:
            logger.warning(
                "[CAPTURE] Writing %s failed (attempt %d of 2): %s", path, attempt, exc
            )
        if attempt == 1:
            time.sleep(0.3)
    return False


def _register_capture(disk_id, slug):
    # Colour file and folder fsynced, then one ledger line appended and fsynced. Returns True
    # when the capture is durably recorded as pending.
    color_path = os.path.join(CAPTURE_DIR, f"{disk_id}_color.jpg")
    try:
        _fsync_file(color_path)
        _fsync_dir(color_path)
    except OSError as exc:
        logger.warning("[CAPTURE] Could not fsync %s: %s", color_path, exc)
    for attempt in range(1, 4):
        try:
            ledger_add(disk_id, slug)
            return True
        except Exception as exc:
            logger.error(
                "[LEDGER] Could not record session %s (attempt %d of 3): %s",
                disk_id,
                attempt,
                exc,
            )
            time.sleep(0.5)
    logger.critical(
        "[LEDGER] Session %s is NOT recorded as pending: if the upload fails it will not be retried",
        disk_id,
    )
    return False


def _store_capture(disk_id, color_mat, bw_mat, active_knobs, naked_mat):
    def since_capture():
        return int(time.time() * 1000) - int(disk_id)

    slug = current_event_slug()
    color_path = os.path.join(CAPTURE_DIR, f"{disk_id}_color.jpg")
    color_ok = _write_capture_image(color_path, color_mat)
    bw_ok = _write_capture_image(os.path.join(CAPTURE_DIR, f"{disk_id}_bw.jpg"), bw_mat)

    if not color_ok:
        logger.critical(
            "[STATE ENGINE] Disk IO failure writing the colour photo of session %s (bw_ok=%s); nothing to upload or print",
            disk_id,
            bw_ok,
        )
        session_update(
            disk_id,
            upload_status="FAILED",
            print_state="failed",
            notice=MSG_PRINT_FAILED,
            notice_until=None,
        )
        return

    logger.info(
        "[CAPTURE] Session files written: %s color_ok=%s bw_ok=%s",
        disk_id,
        color_ok,
        bw_ok,
    )
    t_files = since_capture()

    # Everything the print needs is made first, then the print is queued. If only the B&W file
    # failed, the colour photo is still registered and uploaded below; only the print fails.
    t_print = None
    try:
        if bw_ok:
            prepare_receipt_assets(disk_id)
            queued = enqueue_print(disk_id, "capture", wait=5.0)
            t_print = since_capture()
        else:
            queued = "no black and white file"
        if queued != "queued":
            logger.error(
                "[PRINTER] Could not queue session %s for printing (%s)",
                disk_id,
                queued,
            )
            session_update(
                disk_id,
                print_state="failed",
                notice=MSG_PRINT_FAILED,
                notice_until=None,
            )
    except Exception:
        logger.exception("[CAPTURE] Print preparation failed for session %s", disk_id)
        session_update(
            disk_id, print_state="failed", notice=MSG_PRINT_FAILED, notice_until=None
        )

    # Bookkeeping, in parallel with the printing. The clean frame is written before the upload
    # hint is queued, because the clean upload reads it.
    _write_naked_capture(disk_id, naked_mat)
    recorded = _register_capture(disk_id, slug)
    t_ledger = since_capture()

    try:
        upload_queue.put((disk_id, slug, recorded), block=False)
        logger.info("[UPLOAD QUEUE] Session queued for upload: %s", disk_id)
    except queue.Full:
        logger.warning(
            "[STATE ENGINE] Upload queue full; session %s waits for the idle sync.",
            disk_id,
        )
        if not recorded:
            _register_capture(disk_id, slug)
        NETWORK_OFFLINE.set()
        session_update(disk_id, upload_status="FAILED")
    t_hint = since_capture()
    logger.info(
        "[CAPTURE] %s timeline: files +%dms, print queued +%sms, ledger registered +%dms, upload hint +%dms",
        disk_id,
        t_files,
        t_print if t_print is not None else "-",
        t_ledger,
        t_hint,
    )

    if TELEMETRY_ENABLED:
        try:
            logger.debug("[TELEMETRY] Recording capture %s", disk_id)
            telemetry.record_capture(
                session_id=disk_id,
                knobs=active_knobs,
                effects=get_active_effects(active_knobs),
                wiring=get_wiring_label(),
            )
        except Exception as telemetry_fault:
            logger.warning(
                "[TELEMETRY] Could not record capture %s: %s",
                disk_id,
                telemetry_fault,
                exc_info=True,
            )

    cleanup_sessions()


def async_storage_worker(disk_id, color_mat, bw_mat, active_knobs, naked_mat=None):
    try:
        _store_capture(disk_id, color_mat, bw_mat, active_knobs, naked_mat)
    except Exception:
        logger.exception("[CAPTURE] Storage worker failed for session %s", disk_id)
        with mode_lock:
            if (
                ACTIVE_SESSION["session_id"] == disk_id
                and ACTIVE_SESSION["print_state"] == "waiting"
            ):
                ACTIVE_SESSION.update(
                    print_state="failed", notice=MSG_PRINT_FAILED, notice_until=None
                )


def generate_composite_receipt_image(ts: str) -> str:

    # Composites the QR code into a corner of the rotated B&W capture.
    # Saves as a lossless PNG intermediate (captures/{ts}_composite.png).
    # Leaves '{ts}_bw.jpg' unchanged.
    # Returns composite path on verified success, or None on failure.

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
            _save_qr_png(landing_url, qr_path)
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
                effective_qr_size = max(RECEIPT_QR_MIN_PX, max_allowed_qr)

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
            png_buffer = io.BytesIO()
            composite.save(png_buffer, format="PNG")
            atomic_write_bytes(composite_path, png_buffer.getvalue())

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

    except Exception:
        logger.exception(
            "[COMPOSITE] Unexpected failure generating composite for %s", ts
        )
        return None


def print_booth_receipt(ts, offline_notice=False, on_cut=None):
    # Prints the receipt of one capture. Returns True when it reached the printer, False when
    # the printer failed. `offline_notice` selects the offline text; `on_cut` is called right
    # after the paper is cut, so the booth screen does not wait for the final pause.
    logger.info("[PRINTER] Spooling hardware receipt for session %s", ts)
    printed = False

    with printer_hardware_lock:  # Serializes multi-threaded physical hardware access
        printer = None
        try:
            is_offline_capture = offline_notice

            TARGET_WIDTH = RECEIPT_WIDTH_PX
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
                    if RECEIPT_DITHER == "none":
                        dithered_img = resized_img.convert(
                            "1", dither=getattr(Image, "Dither", Image).NONE
                        )
                    else:  # "floyd": Pillow's default error-diffusion
                        dithered_img = resized_img.convert("1")
                    printer.image(dithered_img)

                if not using_composite:
                    # Explicit fallback notice printed directly on paper if composite failed
                    logger.warning(
                        "[PRINTER FALLBACK] Session %s printed without embedded QR.", ts
                    )
                    printer.text(" > NOTICE: QR EMBEDDING UNAVAILABLE  \n")
                    printer.text(f" > RETRIEVE AT: {get_landing_url(ts)}\n\n")
            else:
                logger.error("[PRINTER] No printable photo found for session %s", ts)

            if is_offline_capture:
                printer.text(" > NETWORK OFFLINE // CACHED LOCALLY  \n")
                printer.text(" > Upload link down. Capture is stored safely. \n")
                printer.text(" > Sync will auto-resume when booth is online. \n")
                printer.text(" > You can get your digital copy online later: \n")
                printer.text(f"   {get_landing_url(ts)}\n")
            else:
                printer.text(" > Scan QR to save your digital copy!   \n")
                printer.text(" > Follow us on socials: @dirtcakestudio   \n")
                printer.text(" > Follow us on socials: @glitchbooth.online   \n")

            printer.text("@->>--     =========================     --<<-@\n")
            printer.ln(2)
            printer.cut()
            printed = True
            if on_cut is not None:
                on_cut()
            time.sleep(0.5)
            logger.info("[PRINTER] Hardware receipt complete for session %s", ts)

        except Exception:
            logger.exception("[PRINTER] Hardware fault while printing session %s", ts)
        finally:
            if printer is not None:
                try:
                    printer.close()
                except Exception as close_err:
                    logger.warning(
                        "[PRINTER] Could not close the printer device: %s", close_err
                    )

    return printed


def _run_print_job(ts, source):
    # A capture print uses the live network state for the receipt text; a reprint from the
    # gallery uses whether that capture is still waiting for its upload.
    offline = NETWORK_OFFLINE.is_set() if source == "capture" else ledger_contains(ts)

    def mark_done():
        session_update(ts, print_state="done", notice="", notice_until=None)

    session_update(ts, print_state="printing")
    started = time.monotonic()
    for attempt in range(1, PRINT_ATTEMPTS + 1):
        if print_booth_receipt(ts, offline_notice=offline, on_cut=mark_done):
            mark_done()
            logger.info(
                "[PRINTER] Session %s printed in %.1f s (attempt %d)",
                ts,
                time.monotonic() - started,
                attempt,
            )
            return
        if attempt < PRINT_ATTEMPTS:
            logger.warning(
                "[PRINTER] Print of session %s failed (attempt %d of %d); trying again",
                ts,
                attempt,
                PRINT_ATTEMPTS,
            )
            session_update(
                ts, print_state="retrying", notice=MSG_PRINT_RETRY, notice_until=None
            )
            SHUTDOWN_EVENT.wait(PRINT_RETRY_DELAY)
    logger.error(
        "[PRINTER] Giving up on session %s after %d attempts", ts, PRINT_ATTEMPTS
    )
    session_update(ts, print_state="failed", notice=MSG_PRINT_FAILED, notice_until=None)


def print_worker():
    # The only thread that prints: captures and gallery reprints wait in one queue.
    logger.info("[INIT] Print worker: active.")
    while not SHUTDOWN_EVENT.is_set():
        try:
            ts, source = print_queue.get(timeout=1.0)
        except queue.Empty:
            continue
        global PRINT_JOB_STARTED
        PRINT_JOB_STARTED = (
            time.monotonic()
        )  # the health monitor watches for a job that never ends
        try:
            _run_print_job(ts, source)
        except Exception:
            logger.exception("[PRINTER] Print worker exception for session %s", ts)
        finally:
            # Written by the print worker, read by the health monitor. One whole value (a number or None)
            # assigned at once and read once into a local, which is atomic in CPython: no lock is needed.
            PRINT_JOB_STARTED = None
            with _print_ids_lock:
                _print_ids.discard(ts)
            print_queue.task_done()


def _clean_file_path(ts):
    return os.path.join(NAKED_DIR, f"{ts}_clean.jpg")


def upload_clean_capture(ts, slug, http, attempts=2):
    # Uploads <ts>_clean.jpg to the clean ImageKit folder of the capture's event. Returns "none"
    # (no usable clean file: nothing to upload), "ok", "retry", "auth" or "rejected". It never
    # raises; a failed clean upload never affects the processed upload, the print or the session.
    path = _clean_file_path(ts)
    if not jpeg_file_usable(path):
        logger.debug("[CLEAN UPLOAD] No usable clean file for session %s; skipping", ts)
        return "none"
    if not IK_UPLOADS_ENABLED:
        return "retry"
    result, detail = "retry", ""
    for attempt in range(1, attempts + 1):
        try:
            result, detail = ik_upload(
                http, path, f"{ts}_clean.jpg", get_ik_clean_folder(slug), (3, 8)
            )
        except Exception as exc:
            result, detail = "retry", f"{type(exc).__name__}: {exc}"
        if result == "ok":
            logger.info("[CLEAN UPLOAD] Session %s uploaded", ts)
            return "ok"
        logger.warning(
            "[CLEAN UPLOAD] Session %s not confirmed: %s (attempt %d of %d)",
            ts,
            detail,
            attempt,
            attempts,
        )
        if result != "retry" or attempt >= attempts:
            break
        time.sleep(2)
    return result


def _clean_stage_needed(ts):
    # The processed photo is confirmed. If there is a usable clean photo, the capture stays in the
    # ledger as "clean only" until that is confirmed too; otherwise it is finished.
    if jpeg_file_usable(_clean_file_path(ts)):
        ledger_set_clean_only(ts, True)
        return True
    ledger_remove({ts})
    return False


def _finish_clean(ts, slug, result):
    # Applies the result of a clean upload to the ledger.
    if result in ("ok", "none"):
        ledger_remove({ts})
        clear_rejections(ts)
    elif result == "rejected":
        if note_rejection(ts) >= UPLOAD_REJECT_LIMIT:
            ledger_to_unresolved(
                ts, slug, f"clean photo rejected {UPLOAD_REJECT_LIMIT} times"
            )
    else:
        logger.warning(
            "[CLEAN UPLOAD] Session %s: the clean photo stays pending; the idle sync retries it",
            ts,
        )


def _clean_upload_job(ts, slug):
    # Own short-lived thread, so even a hanging clean upload cannot delay the upload worker.
    # The upload worker hands its claim on this capture over to this thread.
    http = requests.Session()
    try:
        _finish_clean(ts, slug, upload_clean_capture(ts, slug, http))
    except Exception:
        logger.exception("[CLEAN UPLOAD] Unexpected failure for session %s", ts)
    finally:
        release_upload(ts)
        http.close()


def _booth_idle_for_sync():
    # The idle sync only runs while the booth is in live mode and no upload is waiting, so a
    # visitor's own upload never competes with a backlog for bandwidth.
    if not upload_queue.empty():
        return False
    with mode_lock:
        return ACTIVE_SESSION["mode"] == "live"


def _find_color_file(ts):
    for folder in (CAPTURE_DIR, ARCHIVE_DIR):
        path = os.path.join(folder, f"{ts}_color.jpg")
        if os.path.exists(path):
            return path
    return None


def _sync_failure(entry, result, detail):
    # Turns a failed upload into an outcome for the pass: "retry", "auth" or "rejected".
    global _auth_blocked_until
    if result == "retry":
        NETWORK_OFFLINE.set()
        log_throttled(
            logger,
            logging.WARNING,
            "idle-sync-retry",
            300.0,
            "[IDLE SYNC] Upload of session %s not confirmed: %s",
            entry.id,
            detail,
        )
        return "retry"
    if result == "auth":
        _auth_blocked_until = time.monotonic() + max(0, UPLOAD_AUTH_BACKOFF)
        log_throttled(
            logger,
            logging.CRITICAL,
            "ik-auth",
            3600.0,
            "[IDLE SYNC] ImageKit refused the key (%s): uploads are blocked, captures stay pending",
            detail,
        )
        return "auth"
    count = note_rejection(entry.id)
    log_throttled(
        logger,
        logging.WARNING,
        f"idle-sync-rejected-{entry.id}",
        300.0,
        "[IDLE SYNC] Upload of session %s rejected (%s); rejection %d of %d",
        entry.id,
        detail,
        count,
        UPLOAD_REJECT_LIMIT,
    )
    if count >= UPLOAD_REJECT_LIMIT:
        ledger_to_unresolved(
            entry.id, entry.slug, f"rejected {count} times, last: {detail}"
        )
    return "rejected"


def _sync_entry(http, entry):
    # Uploads one pending capture. Returns (outcome, progressed): outcome is "ok", "retry",
    # "auth", "rejected" or "unresolved"; progressed is True if something was confirmed.
    progressed = False
    if not entry.clean_only:
        path = _find_color_file(entry.id)
        if path is None:
            ledger_to_unresolved(
                entry.id,
                entry.slug,
                "colour file not found in captures/ or captures_archive/",
            )
            return "unresolved", False
        if not jpeg_file_usable(path):
            ledger_to_unresolved(
                entry.id, entry.slug, f"colour file is empty or not a JPEG: {path}"
            )
            return "unresolved", False
        result, detail = ik_upload(
            http,
            path,
            f"{entry.id}_color.jpg",
            get_ik_target_folder(entry.slug),
            (4, 10),
        )
        if result != "ok":
            return _sync_failure(entry, result, detail), False
        NETWORK_OFFLINE.clear()
        clear_rejections(entry.id)
        progressed = True
        logger.info("[IDLE SYNC] Session %s uploaded (fileId %s)", entry.id, detail)
        if not _clean_stage_needed(entry.id):
            return "ok", True
    result = upload_clean_capture(entry.id, entry.slug, http, attempts=1)
    if result in ("ok", "none"):
        _finish_clean(entry.id, entry.slug, result)
        return "ok", True
    if result == "rejected":
        _finish_clean(entry.id, entry.slug, result)
        return "rejected", progressed
    return _sync_failure(entry, result, "clean photo"), progressed


def _idle_sync_pass(http):
    # One pass over the ledger: a snapshot, uploads outside every lock, and each confirmed
    # capture removed from the CURRENT ledger (entries added meanwhile survive).
    progressed = False
    attempted = 0
    for entry in ledger_snapshot():
        if attempted >= max(1, int(IDLE_SYNC_MAX_PER_PASS)) or SHUTDOWN_EVENT.is_set():
            break
        if not _booth_idle_for_sync():
            break  # a visitor started: stop between entries
        if backoff_active(entry.id) or not claim_upload(entry.id):
            continue
        attempted += 1
        try:
            outcome, did = _sync_entry(http, entry)
        finally:
            release_upload(entry.id)
        progressed = progressed or did
        if outcome in ("retry", "auth"):
            break  # the network or the service is the problem: stop this pass
    return progressed


def idle_sync_worker():
    http = requests.Session()
    try:
        _idle_sync_loop(http)
    finally:
        http.close()


def _idle_sync_loop(http):

    # Low-priority background worker that retries the captures recorded in pending_uploads.txt
    # when the link recovers (and after a restart).

    logger.info("[INIT] Idle sync worker (pending upload retries): active.")
    interval = max(1.0, float(IDLE_SYNC_INTERVAL))
    next_wait = interval

    while not SHUTDOWN_EVENT.is_set():
        try:
            if SHUTDOWN_EVENT.wait(timeout=next_wait):
                break
            next_wait = interval

            if not IK_UPLOADS_ENABLED:
                log_throttled(
                    logger,
                    logging.CRITICAL,
                    "no-ik-key",
                    3600.0,
                    "[IDLE SYNC] IK_PRIVATE_KEY is not set: uploads are OFF, captures stay in the pending list",
                )
                continue
            if time.monotonic() < _auth_blocked_until:
                continue
            if not _booth_idle_for_sync():
                next_wait = min(interval, 5.0)
                continue
            if _idle_sync_pass(http):
                next_wait = 2.0  # a backlog drains without waiting a full interval

        except Exception:
            log_throttled(
                logger,
                logging.ERROR,
                "idle-sync-fault",
                300.0,
                "[IDLE SYNC] Worker exception",
                exc_info=True,
            )


# CLOUD INTERFACE BACKGROUND ENGINE
# ==================================================


MAX_UPLOAD_ATTEMPTS = 2


def _upload_processed(http, ts, slug, recorded):
    # Uploads the processed photo of one capture. Returns True when the claim on the capture was
    # handed over to a clean-upload thread (which then releases it).
    local_path = os.path.join(CAPTURE_DIR, f"{ts}_color.jpg")
    session_update(ts, upload_status="UPLOADING")
    result, detail = "retry", "uploads are off (no IK_PRIVATE_KEY)"

    if IK_UPLOADS_ENABLED:
        if not jpeg_file_usable(local_path):
            logger.error("[UPLOAD] Colour file of session %s is missing or empty", ts)
            session_update(ts, upload_status="FAILED")
            if recorded:
                ledger_to_unresolved(ts, slug, "colour file is missing or empty")
            return False
        for attempt in range(1, MAX_UPLOAD_ATTEMPTS + 1):
            result, detail = ik_upload(
                http, local_path, f"{ts}_color.jpg", get_ik_target_folder(slug), (3, 8)
            )
            if result == "ok":
                break
            logger.warning(
                "[UPLOAD] Session %s not confirmed: %s (attempt %d of %d)",
                ts,
                detail,
                attempt,
                MAX_UPLOAD_ATTEMPTS,
            )
            if result != "retry" or attempt >= MAX_UPLOAD_ATTEMPTS:
                break
            time.sleep(1)

    if result == "ok":
        NETWORK_OFFLINE.clear()
        clear_rejections(ts)
        session_update(ts, upload_status="SUCCESS")
        logger.info("[UPLOAD] Session %s uploaded (fileId %s)", ts, detail)
        if _clean_stage_needed(ts):
            threading.Thread(
                target=_clean_upload_job,
                args=(ts, slug),
                name="CleanUpload",
                daemon=True,
            ).start()
            return True
        return False

    # Not confirmed. The capture is already in the ledger, so nothing is lost; the idle sync
    # retries it.
    NETWORK_OFFLINE.set()
    session_update(ts, upload_status="FAILED")
    if result == "auth":
        log_throttled(
            logger,
            logging.CRITICAL,
            "ik-auth",
            3600.0,
            "[UPLOAD] ImageKit refused the key (%s): uploads are blocked, captures stay pending",
            detail,
        )
    elif result == "rejected":
        if note_rejection(ts) >= UPLOAD_REJECT_LIMIT and recorded:
            ledger_to_unresolved(
                ts, slug, f"rejected {UPLOAD_REJECT_LIMIT} times: {detail}"
            )
    if not recorded:
        try:
            ledger_add(
                ts, slug
            )  # safety net: the registration at capture time had failed
        except Exception as fallback_err:
            logger.critical(
                "[OFFLINE CACHE] Could not record session %s in the pending list: %s",
                ts,
                fallback_err,
            )
    logger.warning(
        "[OFFLINE FALLBACK] Session %s is not uploaded yet; it stays in the pending list.",
        ts,
    )
    return False


def upload_queue_worker():
    http = requests.Session()
    try:
        _upload_queue_loop(http)
    finally:
        http.close()


def _upload_queue_loop(http):
    logger.info("[INIT] Cloud Upload Worker: Active and bound.")

    while not SHUTDOWN_EVENT.is_set():
        try:
            hint = upload_queue.get(timeout=1.0)
        except queue.Empty:
            continue

        # The queue only carries hints; the ledger is the record. A hint is (id, slug, recorded).
        ts = None
        claimed = False
        handed_over = False
        try:
            ts, slug, recorded = hint
            entry = None
            if recorded:
                entry = ledger_get(ts)
                if entry is None:
                    logger.info(
                        "[UPLOAD] Session %s is no longer pending (deleted or already confirmed); skipping",
                        ts,
                    )
                    continue
                slug = entry.slug
            if not claim_upload(ts):
                logger.info(
                    "[UPLOAD] Session %s is already being uploaded; skipping", ts
                )
                continue
            claimed = True
            if entry is not None and entry.clean_only:
                threading.Thread(
                    target=_clean_upload_job,
                    args=(ts, slug),
                    name="CleanUpload",
                    daemon=True,
                ).start()
                handed_over = True
                continue
            handed_over = _upload_processed(http, ts, slug, recorded)
        except Exception:
            logger.exception("[UPLOAD WORKER] Worker exception")
            time.sleep(1)
        finally:
            if claimed and not handed_over:
                release_upload(ts)
            upload_queue.task_done()


# SYNCHRONOUS MATERIALIZATION BARRIER CORE LOOP
# ==================================================


def background_loop():
    global last_button_state, capture_lock_until, FRAME_ID
    global UI_FRAME_SEQ
    global SHARED_JPEG_STREAM_BUFFER, SHARED_JPEG_ARRAY
    global PRINT_ALPHA, PRINT_BETA
    global CAMERA_THREAD, LOOP_HEARTBEAT
    CAMERA_THREAD = threading.Thread(
        target=camera_io_worker, name="CameraThread", daemon=True
    )
    CAMERA_THREAD.start()

    serial_connection = None
    last_serial_retry = 0.0
    loop_faults = FaultCounter(LOOP_FAULT_LIMIT)

    while not SHUTDOWN_EVENT.is_set():
        LOOP_HEARTBEAT = time.monotonic()
        frame_start = time.time()

        try:
            if serial_connection is None:
                now_time = time.time()
                if now_time - last_serial_retry > 2.0:
                    last_serial_retry = now_time
                    for port in SERIAL_PORTS:
                        try:
                            serial_connection = serial.Serial(port, 9600, timeout=0.05)
                            reset_serial_input()
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
                    logger.error(
                        "[HARDWARE LINK] Serial connection lost: %s", serial_err
                    )
                    try:
                        serial_connection.close()
                    except Exception as exc:
                        logger.debug(
                            "[HARDWARE LINK] Error occurred while closing serial connection: %s",
                            exc,
                        )
                    serial_connection = None

            # One snapshot of knobs and button; everything below (the control packet, the
            # published session knobs, the capture) uses this same epoch. The locks are
            # taken one after the other, never nested.
            with hw_state_lock:
                _knobs = GLOBAL_KNOBS
                _button = GLOBAL_BUTTON

            with mode_lock:
                ACTIVE_SESSION["knobs"] = list(_knobs)
                loop_mode = ACTIVE_SESSION["mode"]
                session_key = ACTIVE_SESSION["session_id"]
                c_start = ACTIVE_SESSION["countdown_start_time"]
                latest_since = ACTIVE_SESSION["latest_since"]

            packet = ControlPacket(_knobs, _button, loop_mode)
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
                if loop_mode in ["countdown", "latest"]:
                    UI_DISPLAY_BUFFER[:] = FROZEN_DISPLAY_BUFFER[:]
                    if loop_mode == "countdown":
                        if (loop_monotonic - c_start) < FLASH_DURATION:
                            UI_DISPLAY_BUFFER[:] = FLASH_COLOUR[
                                ::-1
                            ]  # camera flash (RGB -> BGR)
                        else:
                            cv2.convertScaleAbs(
                                UI_DISPLAY_BUFFER,
                                dst=UI_DISPLAY_BUFFER,
                                alpha=REVIEW_DIM_FACTOR,
                                beta=0,
                            )
                else:
                    # Live preview used in "live" AND "pre-capture-countdown"
                    UI_DISPLAY_BUFFER[:] = MUTABLE_PROCESSING_BUFFER[:]
                UI_FRAME_SEQ += 1

            with frame_lock:
                _viewers = ACTIVE_STREAM_VIEWERS
                _fid_check = FRAME_ID
            should_encode = (_viewers > 0) or (
                _fid_check % IDLE_PREVIEW_ENCODE_EVERY == 0
            )

            if should_encode:
                # The preview is only scaled and encoded when it is actually used.
                stream_jpeg_quality = max(
                    STREAM_QUALITY_WORST,
                    min(
                        STREAM_QUALITY_BEST,
                        int(
                            STREAM_QUALITY_BEST
                            - (
                                effect_intensity
                                * (STREAM_QUALITY_BEST - STREAM_QUALITY_WORST)
                            )
                        ),
                    ),
                )
                quality = stream_jpeg_quality if _viewers > 0 else STREAM_QUALITY_IDLE

                h, w = UI_DISPLAY_BUFFER.shape[:2]

                if effect_intensity >= PREVIEW_DOWNSCALE_FROM_INTENSITY:
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

            # CENTRAL CONTROL MATRIX & TRANSITION MACHINE
            # ------------------------------------------------------------------

            # STATE 1: LIVE VIEWER - Initiates pre-capture countdown
            if loop_mode == "live":
                if button_pressed_edge and loop_execution_time >= capture_lock_until:
                    camera_ok, camera_age = camera_is_fresh()
                    if not camera_ok:
                        logger.warning(
                            "[STATE ENGINE] Button pressed but the camera frame is %.1f s old. Not starting a capture.",
                            camera_age,
                        )
                        set_notice(MSG_CAMERA, CAMERA_NOTICE_SECONDS)
                        capture_lock_until = loop_execution_time + CAPTURE_LOCK_SECONDS
                    else:
                        with mode_lock:
                            ACTIVE_SESSION["mode"] = "pre-capture-countdown"
                            ACTIVE_SESSION["countdown_start_time"] = loop_monotonic
                            ACTIVE_SESSION["countdown_remaining"] = (
                                POSE_COUNTDOWN_SECONDS
                            )
                            _set_notice_locked("", None)

                        logger.info(
                            "[STATE ENGINE] Button pressed. Starting pre-capture countdown (%ss pose window).",
                            POSE_COUNTDOWN_SECONDS,
                        )

            # STATE 1B: PRE-CAPTURE COUNTDOWN - Live pose timer (POSE_COUNTDOWN_SECONDS.. 2.. 1..) then snapshot capture
            elif loop_mode == "pre-capture-countdown":
                time_delta = loop_monotonic - c_start

                if time_delta < POSE_COUNTDOWN_SECONDS:
                    remaining_steps = max(
                        1, POSE_COUNTDOWN_SECONDS - math.floor(time_delta)
                    )
                    with mode_lock:
                        ACTIVE_SESSION["countdown_remaining"] = remaining_steps
                else:
                    camera_ok, camera_age = camera_is_fresh()
                    if not camera_ok:
                        logger.warning(
                            "[STATE ENGINE] The camera frame is %.1f s old at capture time. Cancelling the capture.",
                            camera_age,
                        )
                        reset_to_live("camera stale at capture")
                        set_notice(MSG_CAMERA, CAMERA_NOTICE_SECONDS)
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
                            ACTIVE_SESSION.update(
                                mode="countdown",
                                session_id=high_res_id,
                                upload_status="IDLE",
                                print_state="waiting",
                                countdown_start_time=loop_monotonic,
                                countdown_remaining=0,
                            )
                            _set_notice_locked("", None)

                        logger.info(
                            "[STATE ENGINE] Pose countdown finished. Captured frame for %s. The PRINTING screen stays until the print is done.",
                            high_res_id,
                        )

                        captured_knobs = list(packet.knobs)
                        threading.Thread(
                            name=f"CaptureStorage-{high_res_id}",
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

            # STATE 2: PRINTING - flash, then the "PRINTING" overlay until the receipt is printed
            elif loop_mode == "countdown":
                time_delta = loop_monotonic - c_start
                with mode_lock:
                    print_state = ACTIVE_SESSION["print_state"]
                finished = time_delta >= FLASH_DURATION and print_state in (
                    "done",
                    "failed",
                )
                timed_out = time_delta >= PRINT_OVERLAY_TIMEOUT

                if finished or timed_out:
                    with mode_lock:
                        ACTIVE_SESSION["mode"] = "latest"
                        ACTIVE_SESSION["latest_since"] = loop_monotonic
                    if finished:
                        logger.info(
                            "[STATE ENGINE] Session %s print %s %.1f s after capture. Showing the review screen.",
                            session_key,
                            print_state,
                            time_delta,
                        )
                    else:
                        logger.warning(
                            "[STATE ENGINE] Session %s is still '%s' %.0f s after capture. Showing the review screen anyway.",
                            session_key,
                            print_state,
                            time_delta,
                        )

            # STATE 3: REVIEW VIEW - confirmation screen until the button is pressed or the timeout ends
            elif loop_mode == "latest":
                if button_pressed_edge:
                    reset_to_live("button", expected_session_id=session_key)
                elif loop_monotonic - latest_since > LATEST_TIMEOUT_SECONDS:
                    reset_to_live("review timeout", expected_session_id=session_key)

            last_button_state = packet.button

            # Adaptive FPS governor
            dynamic_fps = CAMERA_FPS
            for cost_from, step_fps in GOVERNOR_STEPS:  # highest cost first
                if effect_intensity >= cost_from:
                    dynamic_fps = step_fps
                    break

            target_dt = 1.0 / dynamic_fps

            elapsed = time.time() - frame_start
            remaining = target_dt - elapsed

            loop_faults.ok()
            if remaining > 0:
                SHUTDOWN_EVENT.wait(remaining)

        except Exception:
            log_throttled(
                logger,
                logging.ERROR,
                "loop-fault",
                10.0,
                "[STATE ENGINE] Core loop exception",
                exc_info=True,
            )
            if loop_faults.fault():
                logger.critical(
                    "[STATE ENGINE] The core loop failed %d passes in a row",
                    loop_faults.count,
                )
                request_shutdown(
                    "fault",
                    1,
                    f"the core loop failed {loop_faults.count} passes in a row",
                )
            SHUTDOWN_EVENT.wait(1.0)

    # The loop ended because of a shutdown request: leave the hardware as it was found.
    if serial_connection is not None:
        try:
            serial_connection.close()
        except Exception as exc:
            logger.debug("[HARDWARE LINK] Error while closing the serial port: %s", exc)
    logger.info("[SHUTDOWN] Core loop ended; the serial port is closed.")


# READ-ISOLATED MJPEG STREAM GATEWAY
# ==================================================


def generate_frames():
    global ACTIVE_STREAM_VIEWERS

    local_stream_frame_id = -1
    last_sent = 0

    try:
        frame_interval = 1.0 / CAMERA_FPS

        with frame_lock:
            ACTIVE_STREAM_VIEWERS += 1

        while not SHUTDOWN_EVENT.is_set():
            now = time.time()
            if now - last_sent < frame_interval:
                SHUTDOWN_EVENT.wait(0.005)
                continue

            with frame_lock:
                if local_stream_frame_id == FRAME_ID:
                    frame_is_stale = True
                else:
                    frame_is_stale = False
                    local_stream_frame_id = FRAME_ID  # Track state locally
                    local_jpeg_payload = SHARED_JPEG_STREAM_BUFFER

            if frame_is_stale:
                SHUTDOWN_EVENT.wait(0.01)
                continue

            last_sent = now

            if not len(local_jpeg_payload):
                SHUTDOWN_EVENT.wait(0.01)
                continue

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
    except Exception:
        logger.exception("[STREAM] Unexpected error in the frame generator")
        return
    finally:
        with frame_lock:
            ACTIVE_STREAM_VIEWERS = max(0, ACTIVE_STREAM_VIEWERS - 1)


# LIFECYCLE: SHUTDOWN OWNERSHIP, SIGNALS, HEALTH MONITOR
# ==================================================
# Only the OWNER of the process (the display, or the standalone entry point at the bottom of
# this file) installs the signal handlers and runs the shutdown sequence, and it does so once.
# Everything else only REQUESTS a shutdown with request_shutdown(). A request with reason
# "fault" makes the process exit with code 1, so systemd (Restart=on-failure) restarts the
# kiosk; every other reason exits with code 0.

# LIFECYCLE TUNABLES
SHUTDOWN_JOIN_BUDGET = 10.0  # seconds all workers together get to finish at shutdown
HEALTH_CHECK_INTERVAL = 1.0  # seconds between health checks
LOOP_HEARTBEAT_MAX_AGE = (
    10.0  # the core loop has not completed a pass for this long = hung
)
CAMERA_HEARTBEAT_MAX_AGE = (
    15.0  # the camera thread is stuck (a read that never returns)
)
DISPLAY_HEARTBEAT_MAX_AGE = 15.0  # the screen loop is stuck
PRINT_STUCK_SECONDS = 120.0  # one print job running this long = stuck printer
FAULT_WAIT_FOR_LIVE = (
    30.0  # a visitor's session is not interrupted for a thread problem
)
#                             unless the problem lasts this long
HARD_EXIT_GRACE = (
    15.0  # after a fault request the process is ended from here if it is still up
)
#                        (must be longer than SHUTDOWN_JOIN_BUDGET, or it cuts the orderly shutdown short)
LOOP_FAULT_LIMIT = (
    30  # consecutive failed passes of the core loop before the booth restarts
)
POWER_ACTION_CANCEL_SECONDS = (
    30.0  # a power action that systemd has not carried out by then is cancelled
)
WEB_SELF_CHECK_ENABLED = (
    True  # the monitor asks the booth's own web server /ping now and then
)
WEB_SELF_CHECK_INTERVAL = 10.0
WEB_SELF_CHECK_FAILURES = 3  # misses in a row that count as a fault
WEB_SELF_CHECK_URL = "http://127.0.0.1:5000/ping"
SERVICE_UNIT_NAME = "glitchbooth.service"
POWER_ACTION_MESSAGES = {
    # Shown on the kiosk screen (and in /state) while a power action is on its way.
    "poweroff": "SİSTEM KAPATILIYOR...",
    "reboot": "SİSTEM YENİDEN BAŞLATILIYOR...",
    "service_disabled": "SERVİS DEVRE DIŞI, KONSOLA DÖNÜLÜYOR...",
}

for _name, _ok in (
    ("SHUTDOWN_JOIN_BUDGET", SHUTDOWN_JOIN_BUDGET > 0),
    ("HEALTH_CHECK_INTERVAL", HEALTH_CHECK_INTERVAL >= 0.1),
    ("LOOP_HEARTBEAT_MAX_AGE", LOOP_HEARTBEAT_MAX_AGE > 0),
    ("CAMERA_HEARTBEAT_MAX_AGE", CAMERA_HEARTBEAT_MAX_AGE > 0),
    ("DISPLAY_HEARTBEAT_MAX_AGE", DISPLAY_HEARTBEAT_MAX_AGE > 0),
    ("PRINT_STUCK_SECONDS", PRINT_STUCK_SECONDS > 0),
    ("LOOP_FAULT_LIMIT", LOOP_FAULT_LIMIT >= 1),
    ("HARD_EXIT_GRACE", HARD_EXIT_GRACE > SHUTDOWN_JOIN_BUDGET),
    ("WEB_SELF_CHECK_INTERVAL", WEB_SELF_CHECK_INTERVAL >= 1),
    ("WEB_SELF_CHECK_FAILURES", WEB_SELF_CHECK_FAILURES >= 1),
):
    if not _ok:
        logger.warning(
            "[CONFIG] %s is outside what the lifecycle code can use; the code treats it as the nearest usable value",
            _name,
        )

# Reasons after which the console is handed back to a login prompt. A fault exits with a
# failure code and systemd restarts the kiosk within seconds, so the console stays as it is.
RESCUE_REASONS = ("exit_to_terminal", "signal")

_shutdown_lock = threading.Lock()
SHUTDOWN_REASON = None  # the first request wins
SHUTDOWN_DETAIL = ""
EXIT_CODE = 0
PENDING_POWER_ACTION = None  # "poweroff" | "reboot" | "service_disabled"
_signal_received = None
_sequence_done = False
_sequence_lock = threading.Lock()
PROCESS_STARTED = time.monotonic()
PREVIOUS_FAULT = None  # the fault record of the previous run, loaded at start

# Heartbeats: one float assignment each, written by the thread they describe.
LOOP_HEARTBEAT = None
CAMERA_HEARTBEAT = None
DISPLAY_HEARTBEAT = None  # set by the display; None when the booth runs without one
PRINT_JOB_STARTED = None
CAMERA_THREAD = None


def request_shutdown(reason, exit_code=0, detail=""):
    # Callable from any thread, any number of times. The first reason wins.
    global SHUTDOWN_REASON, SHUTDOWN_DETAIL, EXIT_CODE
    with _shutdown_lock:
        if SHUTDOWN_REASON is None:
            SHUTDOWN_REASON, SHUTDOWN_DETAIL, EXIT_CODE = reason, detail, exit_code
    SHUTDOWN_EVENT.set()


def announce_power_action(action):
    # A web route is about to ask systemd for a power action. This does NOT stop anything: the
    # shutdown starts when systemd's SIGTERM arrives. It only tells /state, the health monitor
    # and the exit path which kind of shutdown is coming. Returns False if one is already announced.
    global PENDING_POWER_ACTION
    with _shutdown_lock:
        if PENDING_POWER_ACTION is not None:
            return False
        PENDING_POWER_ACTION = action
        return True


def cancel_power_action(why):
    global PENDING_POWER_ACTION
    with _shutdown_lock:
        was, PENDING_POWER_ACTION = PENDING_POWER_ACTION, None
    if was:
        logger.error(
            "[POWER] %s did not happen (%s); the booth carries on normally", was, why
        )


def pending_power_action():
    with _shutdown_lock:
        return PENDING_POWER_ACTION


def _on_signal(signum, frame):
    # Only sets flags: no logging, no locks held for long, no sleeping, no sys.exit inside a
    # signal handler (it could unwind into the middle of anything).
    global _signal_received
    _signal_received = signum
    request_shutdown(PENDING_POWER_ACTION or "signal", 0)


def install_signal_handlers():
    # Called once by the owner of the process, from the main thread.
    if threading.current_thread() is not threading.main_thread():
        raise RuntimeError("signal handlers can only be installed from the main thread")
    signal.signal(signal.SIGINT, _on_signal)
    signal.signal(signal.SIGTERM, _on_signal)


def _record_fault(reason, detail):
    # The next run reads this to say why the booth restarted by itself.
    try:
        data = json.dumps(
            {
                "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                "reason": reason,
                "detail": detail,
            }
        ).encode("utf-8")
        atomic_write_bytes(FAULT_RECORD_PATH, data, fsync=True)
    except Exception:
        logger.exception("[HEALTH] Could not write %s", FAULT_RECORD_PATH)


def _load_previous_fault():
    global PREVIOUS_FAULT
    try:
        with open(FAULT_RECORD_PATH, "r", encoding="utf-8") as f:
            PREVIOUS_FAULT = json.load(f)
    except FileNotFoundError:
        return
    except Exception:
        logger.exception("[HEALTH] Could not read %s", FAULT_RECORD_PATH)
        return
    logger.warning(
        "[HEALTH] An earlier run ended with a fault at %s: %s (%s)",
        PREVIOUS_FAULT.get("time"),
        PREVIOUS_FAULT.get("reason"),
        PREVIOUS_FAULT.get("detail"),
    )


def _terminal_rescue():
    # Hands the console back to a login prompt (the unit has Conflicts=getty@tty1.service, so
    # nothing else starts it). Every command is non-interactive and has a timeout.
    logger.info(
        "[SHUTDOWN TTY RESCUE] Resetting console input streams and starting a login prompt on tty1"
    )
    for argv in (
        ["stty", "sane"],
        ["sudo", "-n", "kbd_mode", "-a"],
        ["sudo", "-n", "systemctl", "start", "getty@tty1.service"],
        ["sudo", "-n", "chvt", "1"],
    ):
        try:
            result = subprocess.run(
                argv,
                check=False,
                timeout=5,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
            if result.returncode != 0:
                logger.warning(
                    "[SHUTDOWN TTY RESCUE] %s returned %d: %s",
                    " ".join(argv),
                    result.returncode,
                    result.stderr.decode("utf-8", "replace").strip()[:200],
                )
        except Exception as exc:
            logger.warning("[SHUTDOWN TTY RESCUE] %s failed: %s", " ".join(argv), exc)


def join_threads(threads, total_timeout):
    # One shared deadline for all of them. Returns the names of the threads still alive.
    deadline = time.monotonic() + total_timeout
    for t in threads:
        t.join(max(0.0, deadline - time.monotonic()))
    return [t.name for t in threads if t.is_alive()]


def _threads_named(*names, prefixes=()):
    me = threading.current_thread()
    return [
        t
        for t in threading.enumerate()
        if t is not me and (t.name in names or t.name.startswith(tuple(prefixes)))
    ]


def _shutdown_steps():
    def join_step(label, selector, before=None):
        def step(left):
            if before is not None:
                before()
            alive = join_threads(selector(), left)
            if alive:
                logger.warning(
                    "[SHUTDOWN] Still running after the wait (%s): %s",
                    label,
                    ", ".join(alive),
                )

        return label, step

    def telemetry_request():
        if TELEMETRY_ENABLED:
            try:
                telemetry.request_shutdown()
            except Exception as exc:
                logger.warning(
                    "[SHUTDOWN] Failed to request the telemetry shutdown: %s", exc
                )

    # Order matters: the core loop stops first (no new captures); a capture that is being stored
    # finishes (it also queues its telemetry record); only then is telemetry told to flush; a
    # running print finishes; uploads come last because the ledger keeps whatever is unfinished.
    return [
        join_step(
            "core loop and camera",
            lambda: _threads_named("BackgroundLoop", "CameraThread"),
        ),
        join_step(
            "capture storage and clean uploads",
            lambda: _threads_named(prefixes=("CaptureStorage-", "CleanUpload")),
        ),
        join_step(
            "telemetry",
            lambda: _threads_named(
                "TelemetryQueueConsumer", "TelemetryCloudSyncWorker", "TelemetryWorker"
            ),
            before=telemetry_request,
        ),
        join_step("printing", lambda: _threads_named("PrintWorker")),
        join_step("uploads", lambda: _threads_named("UploadWorker", "IdleSyncWorker")),
    ]


def run_shutdown_sequence(budget=None):
    # Runs once (a second call returns at once). Returns the process exit code.
    global _sequence_done
    with _sequence_lock:
        if _sequence_done:
            return EXIT_CODE
        _sequence_done = True
    budget = SHUTDOWN_JOIN_BUDGET if budget is None else budget
    started = time.monotonic()
    logger.info(
        "[SHUTDOWN] Reason: %s%s%s",
        SHUTDOWN_REASON or "unknown",
        f" (signal {_signal_received})" if _signal_received else "",
        f" - {SHUTDOWN_DETAIL}" if SHUTDOWN_DETAIL else "",
    )
    if SHUTDOWN_REASON == "fault":
        _record_fault(SHUTDOWN_REASON, SHUTDOWN_DETAIL)
    for label, step in _shutdown_steps():
        left = budget - (time.monotonic() - started)
        try:
            step(max(0.2, left))
        except Exception:
            logger.exception("[SHUTDOWN] Step '%s' failed", label)
    if SHUTDOWN_REASON in RESCUE_REASONS:
        try:
            _terminal_rescue()
        except Exception:
            logger.exception("[SHUTDOWN TTY RESCUE] Terminal rescue routine failed")
    else:
        logger.info(
            "[SHUTDOWN] No terminal rescue needed for reason '%s'", SHUTDOWN_REASON
        )
    logger.info(
        "[SYSTEM EXIT] Teardown complete in %.1f s, exit code %d",
        time.monotonic() - started,
        EXIT_CODE,
    )
    shutdown_logging()
    return EXIT_CODE


def sd_notify(message):
    # Tells systemd something (here: WATCHDOG=1) without any library. False outside systemd.
    path = os.environ.get("NOTIFY_SOCKET")
    if not path:
        return False
    if path.startswith("@"):
        path = "\0" + path[1:]
    with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as s:
        s.settimeout(1.0)
        s.connect(path)
        s.sendall(message.encode())
    return True


class FaultCounter:
    # Consecutive failed passes of a loop. A success resets it.
    def __init__(self, limit):
        self.limit, self.count = max(1, int(limit)), 0

    def ok(self):
        self.count = 0

    def fault(self):
        self.count += 1
        return self.count >= self.limit


def _hard_exit(code):
    # Last resort, used when the main thread did not get out after a fault request.
    logger.critical(
        "[HEALTH] The shutdown did not complete; ending the process with code %d", code
    )
    _record_fault(
        SHUTDOWN_REASON or "fault", SHUTDOWN_DETAIL or "shutdown did not complete"
    )
    try:
        fault_file = _log_state.get("fault_file")
        if fault_file is not None:
            fault_file.write(
                "\n--- thread dump taken by the health monitor before a forced exit ---\n"
            )
            fault_file.flush()
            faulthandler.dump_traceback(file=fault_file, all_threads=True)
            fault_file.flush()
    except Exception:
        pass
    try:
        shutdown_logging()
    except Exception:
        pass
    os._exit(code)


def booth_is_live():
    with mode_lock:
        return ACTIVE_SESSION["mode"] == "live"


class HealthMonitor(threading.Thread):
    # Once a second: are the critical threads alive, are the loops still ticking? A problem
    # requests a shutdown with a failure code, so systemd restarts the kiosk. A visitor's
    # session is not interrupted for a thread problem unless it lasts FAULT_WAIT_FOR_LIVE.

    def __init__(self, notify=None, hard_exit=_hard_exit, clock=time.monotonic):
        super().__init__(name="HealthMonitor", daemon=True)
        self.notify, self.hard_exit, self.clock = notify, hard_exit, clock
        self._first_problem = None
        self._fault_requested_at = None
        self._web_misses = 0
        self._last_web_check = clock()
        self._t_created = clock()

    def _critical_threads(self):
        threads = list(CRITICAL_WORKERS)
        if CAMERA_THREAD is not None:
            threads.append(CAMERA_THREAD)
        return threads

    def _heartbeats(self):
        beats = {}
        if LOOP_HEARTBEAT is not None:
            beats["core loop"] = (
                LOOP_HEARTBEAT,
                LOOP_HEARTBEAT_MAX_AGE,
                "has not completed a pass for",
            )
        if CAMERA_HEARTBEAT is not None:
            beats["camera thread"] = (
                CAMERA_HEARTBEAT,
                CAMERA_HEARTBEAT_MAX_AGE,
                "has not reported for",
            )
        if DISPLAY_HEARTBEAT is not None:
            beats["display loop"] = (
                DISPLAY_HEARTBEAT,
                DISPLAY_HEARTBEAT_MAX_AGE,
                "has not drawn a frame for",
            )
        started = PRINT_JOB_STARTED
        if started is not None:
            beats["print job"] = (started, PRINT_STUCK_SECONDS, "has been running for")
        return beats

    def _web_check(self, now):
        if not WEB_SELF_CHECK_ENABLED or now - self._t_created < 30.0:
            return None
        if now - self._last_web_check < max(1.0, WEB_SELF_CHECK_INTERVAL):
            return None
        self._last_web_check = now
        try:
            with urllib.request.urlopen(WEB_SELF_CHECK_URL, timeout=2.0) as response:
                ok = response.status == 200
        except Exception:
            ok = False
        self._web_misses = 0 if ok else self._web_misses + 1
        if self._web_misses >= max(1, WEB_SELF_CHECK_FAILURES):
            return f"the web server did not answer {self._web_misses} checks in a row"
        return None

    def problem(self):
        for t in self._critical_threads():
            if not t.is_alive():
                return f"thread {t.name} is not running"
        now = self.clock()
        for name, (last, max_age, text) in self._heartbeats().items():
            if now - last > max_age:
                return f"{name} {text} {now - last:.0f} s"
        return self._web_check(now)

    def check_once(self):
        if SHUTDOWN_EVENT.is_set():
            if (
                self._fault_requested_at
                and self.clock() - self._fault_requested_at > max(1.0, HARD_EXIT_GRACE)
            ):
                self.hard_exit(EXIT_CODE or 1)
            return
        if pending_power_action():
            return  # a power action is on its way; threads may legitimately be stopping
        problem = self.problem()
        if problem is None:
            self._first_problem = None
            if self.notify:
                try:
                    self.notify()
                except Exception:
                    log_throttled(
                        logger,
                        logging.WARNING,
                        "sd-notify",
                        600.0,
                        "[HEALTH] Could not notify systemd",
                        exc_info=True,
                    )
            return
        now = self.clock()
        if self._first_problem is None:
            self._first_problem = now
            logger.critical("[HEALTH] %s", problem)
        if booth_is_live() or now - self._first_problem >= FAULT_WAIT_FOR_LIVE:
            self._fault_requested_at = now
            logger.critical("[HEALTH] Restarting the booth: %s", problem)
            request_shutdown("fault", 1, problem)

    def run(self):
        while True:
            try:
                self.check_once()
            except Exception:
                log_throttled(
                    logger,
                    logging.ERROR,
                    "health-monitor-fault",
                    60.0,
                    "[HEALTH] The health monitor itself failed",
                    exc_info=True,
                )
            time.sleep(max(0.1, HEALTH_CHECK_INTERVAL))


def current_notice():
    # The notice shown to visitors: a timed session notice, else a power action in progress,
    # else (in live mode) the camera message while the camera has no fresh picture.
    with mode_lock:
        text = _notice_locked()
        live = ACTIVE_SESSION["mode"] == "live"
    if text:
        return text
    action = pending_power_action()
    if action:
        return POWER_ACTION_MESSAGES.get(action, "")
    if live and not camera_is_fresh()[0]:
        return MSG_CAMERA
    return ""


def check_service_unit():
    # One WARNING at start if the unit that the power routes address does not exist.
    try:
        result = subprocess.run(
            ["systemctl", "cat", SERVICE_UNIT_NAME],
            check=False,
            timeout=5,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        if result.returncode != 0:
            logger.warning(
                "[CONFIG] systemd has no unit called %s (%s); the 'service disabled' action will fail",
                SERVICE_UNIT_NAME,
                result.stderr.decode("utf-8", "replace").strip()[:120],
            )
    except Exception as exc:
        logger.warning(
            "[CONFIG] Could not check the systemd unit %s: %s", SERVICE_UNIT_NAME, exc
        )


# BACKEND ENGINE STARTING ANd OWNING ALL THE WORKERS
# ==================================================


_engine_lock = threading.Lock()
_engine_started = False


def start_backend_engine(blocking=False):

    # Starts every worker and the web server. Call it once. It does NOT install signal handlers:
    # the owner of the process does that (install_signal_handlers()).
    # blocking=True is for standalone operation: it waits for a shutdown request, runs the
    # shutdown sequence and exits the process. blocking=False returns after the start (embedded
    # in the display, which owns the process).

    global _engine_started
    with _engine_lock:
        if _engine_started:
            logger.error(
                "[SERVER] start_backend_engine was called twice; ignoring the second call"
            )
            return
        _engine_started = True

    if blocking:
        install_signal_handlers()

    if CRT_ENABLED:
        initialize_crt_mask()

    if TELEMETRY_ENABLED:
        telemetry.compile_summary_stats()

    try:
        ledger_startup_check()
    except Exception:
        logger.exception("[LEDGER] Startup check failed")

    _load_previous_fault()

    worker_configs = [
        ("UploadWorker", upload_queue_worker),
        ("PrintWorker", print_worker),
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

    # Web server: always in a daemon thread, so the main thread belongs to the owner.
    from waitress import serve

    logger.info("[SERVER] Starting the Waitress web server in its own thread...")
    web_thread = threading.Thread(
        target=lambda: serve(app, host="0.0.0.0", port=5000, threads=8),
        name="WaitressWebServer",
        daemon=True,
    )
    CRITICAL_WORKERS.append(web_thread)
    web_thread.start()

    # The health monitor starts last: every thread it watches exists by now.
    HealthMonitor(notify=lambda: sd_notify("WATCHDOG=1")).start()
    threading.Thread(target=check_service_unit, name="UnitCheck", daemon=True).start()

    if blocking:
        while not SHUTDOWN_EVENT.wait(1.0):
            pass
        sys.exit(run_shutdown_sequence())


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

    if not req_id or not valid_capture_id(req_id):
        return redirect("/")

    return render_template("latest.html", latest_id=req_id)


@app.route("/audio")
def audio_node():
    return render_template("sounds.html")


@app.route("/control")
def control_panel():
    return render_template("control.html")


def valid_capture_id(value):
    # Capture ids are millisecond timestamps: ASCII digits only. This is a format check, not
    # an authorization check.
    return (
        isinstance(value, str)
        and 1 <= len(value) <= 20
        and re.fullmatch(r"[0-9]+", value) is not None
    )


@app.route("/gallery")
def gallery():
    page = request.args.get(
        "page", 1, type=int
    )  # anything that is not a number: page 1
    per_page = GALLERY_PER_PAGE

    try:
        all_files = os.listdir(CAPTURE_DIR)
        modern_files = []
        for f in all_files:
            if f.endswith("_color.jpg"):
                base = f.replace("_color.jpg", "")

                if valid_capture_id(base):
                    modern_files.append(f)

        files = sorted(modern_files, reverse=True)
    except Exception:
        logger.exception("[GALLERY] Listing error")
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
    page = max(1, min(page, total_pages))
    start, end = (page - 1) * per_page, page * per_page
    return render_template(
        "gallery.html", captures=captures[start:end], page=page, total_pages=total_pages
    )


@app.route("/print_capture/<session_id>", methods=["POST"])
def print_capture(session_id):
    if not valid_capture_id(session_id):
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

    result = enqueue_print(session_id, "manual")
    if result == "duplicate":
        return (
            jsonify(
                {
                    "status": "error",
                    "message": f"Session {session_id} is already waiting or printing",
                }
            ),
            409,
        )
    if result == "busy":
        return (
            jsonify(
                {
                    "status": "error",
                    "message": "The print queue is full. Try again in a moment.",
                }
            ),
            429,
        )

    logger.info("[MANUAL PRINT] Queued session %s for printing", session_id)
    return jsonify(
        {
            "status": "success",
            "message": f"Reprint job queued for session {session_id}",
        }
    )


@app.route("/delete_capture/<session_id>", methods=["POST"])
def delete_capture(session_id):
    if not valid_capture_id(session_id):
        return (
            jsonify({"status": "error", "message": "Invalid session identifier"}),
            400,
        )

    # A capture that is printing (or queued to print) or is on the booth screen right now is
    # not deleted from under the print job.
    with _print_ids_lock:
        printing = session_id in _print_ids
    with mode_lock:
        on_screen = ACTIVE_SESSION["session_id"] == session_id
    uploading = (
        session_id in inflight_snapshot()
    )  # an upload attempt is running right now
    if printing or on_screen or uploading:
        return (
            jsonify(
                {
                    "status": "error",
                    "message": "This capture is printing, on screen or uploading right now; try again in a moment",
                }
            ),
            409,
        )

    color_path = os.path.join(CAPTURE_DIR, f"{session_id}_color.jpg")
    bw_path = os.path.join(CAPTURE_DIR, f"{session_id}_bw.jpg")
    qr_path = os.path.join(SAVED_DIR, f"{session_id}_qr.png")
    comp_png = os.path.join(CAPTURE_DIR, f"{session_id}_composite.png")
    clean_path = os.path.join(NAKED_DIR, f"{session_id}_clean.jpg")

    deleted_any = False
    for path in [color_path, bw_path, qr_path, comp_png, clean_path]:
        if os.path.exists(path):
            try:
                os.remove(path)
                deleted_any = True
            except Exception as e:
                logger.error("[GALLERY] Failed to delete file asset %s: %s", path, e)

    # A deleted capture must not stay in the upload ledger (it would only end up unresolved).
    try:
        if ledger_remove({session_id}):
            deleted_any = True
        clear_rejections(session_id)
    except Exception:
        logger.exception(
            "[GALLERY] Could not remove session %s from the ledger", session_id
        )

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
        logger.warning(
            "[STREAM] Rejected a stream connection from %s (limit reached; protecting server threads)",
            client_ip,
        )
        return "Too Many Concurrent Streams", 429

    logger.info("[STREAM] Connect: IP %s | Agent: %s", client_ip, user_agent)

    def stream_lifecycle_wrapper():
        try:
            yield from generate_frames()
        finally:
            STREAM_SEMAPHORE.release()
            logger.info("[STREAM] Disconnect: released the slot for IP %s", client_ip)

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
        current_upload_status = ACTIVE_SESSION["upload_status"]
        current_print_state = ACTIVE_SESSION["print_state"]

    # Each group below is one short snapshot under its own lock (never nested).
    with hw_state_lock:
        knobs_now = list(GLOBAL_KNOBS)
        frame_time_now = last_frame_time
        fail_count_now = camera_fail_count
    with frame_lock:
        frame_id_now = FRAME_ID
    system_now = SYSTEM_TELEMETRY  # replaced whole by the monitor thread, never edited
    notice_now = current_notice()
    power_action = pending_power_action()

    is_flash = (
        current_mode == "countdown" and (time.monotonic() - c_start) < FLASH_DURATION
    )
    camera_age_now = time.time() - frame_time_now
    # While a power action is on its way the pages are told so: "shutdown" for power off,
    # "restart" for a reboot (and for the service-disabled reboot); shutdown_reason says which.
    if power_action:
        mode_out = "shutdown" if power_action == "poweroff" else "restart"
    else:
        mode_out = current_mode

    return jsonify(
        {
            "knobs": knobs_now,
            "labels": get_knob_labels(),
            "ip": system_now["ip"],
            "last_frame_time": frame_time_now,
            "camera_fail_count": fail_count_now,
            "camera_age": camera_age_now,
            "camera_ok": camera_age_now <= CAMERA_FRESH_MAX_AGE,
            "cpu_temp": system_now["cpu_temp"],
            "memory_rss_mb": system_now["memory_rss_mb"],
            "frame_id": frame_id_now,
            "upload_status": current_upload_status,
            "print_state": current_print_state,
            "notice": notice_now,
            "mode": mode_out,
            "shutdown_reason": power_action or "",
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


def _power_error(message, status=500):
    return jsonify({"status": "error", "message": message}), status


def _sudo_systemctl(*args, timeout=15):
    return subprocess.run(
        ["sudo", "-n", "systemctl", *args],
        check=False,
        timeout=timeout,
        capture_output=True,
        text=True,
    )


def _sudo_allowed(*args):
    # Asks sudo (without running anything) whether this exact command is allowed without a
    # password. Returns (allowed, detail).
    try:
        result = subprocess.run(
            ["sudo", "-n", "-l", "systemctl", *args],
            check=False,
            timeout=5,
            capture_output=True,
            text=True,
        )
        return result.returncode == 0, (result.stderr or result.stdout).strip()[:160]
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


def _undo_power_change(undo_args):
    # Runs the undo command of a power action that did not happen (for "restart with service
    # disabled": enable the service again). WARNING when it worked, CRITICAL when it did not.
    if not undo_args:
        return
    cmd = " ".join(undo_args)
    try:
        undone = _sudo_systemctl(*undo_args)
    except Exception:
        logger.critical(
            "[POWER] Could NOT undo the earlier change: run 'sudo systemctl %s' by hand",
            cmd,
            exc_info=True,
        )
        return
    if undone.returncode == 0:
        logger.warning("[POWER] Undid the earlier change with 'systemctl %s'", cmd)
    else:
        logger.critical(
            "[POWER] Could NOT undo the earlier change ('systemctl %s' exit %d): run it by hand",
            cmd,
            undone.returncode,
        )


def _run_power_command(args, undo_args=None):
    # Runs after the HTTP response has gone out. systemd then stops the booth with SIGTERM; the
    # announced action only tells /state and the shutdown which kind of stop it is.
    time.sleep(0.5)
    try:
        result = _sudo_systemctl(*args)
        failed = result.returncode != 0
        detail = (result.stderr or result.stdout).strip()[:200]
    except Exception as exc:
        failed, detail = True, f"{type(exc).__name__}: {exc}"
    if failed:
        logger.error("[POWER] 'systemctl %s' failed: %s", " ".join(args), detail)
        _undo_power_change(undo_args)
        cancel_power_action(f"systemctl {' '.join(args)} failed")
        return
    if not SHUTDOWN_EVENT.wait(POWER_ACTION_CANCEL_SECONDS):
        logger.critical(
            "[POWER] 'systemctl %s' was accepted but systemd has not stopped the booth after %.0f s",
            " ".join(args),
            POWER_ACTION_CANCEL_SECONDS,
        )
        # The booth is still running: the service must not stay disabled, or the next start
        # would come up without the kiosk.
        _undo_power_change(undo_args)
        cancel_power_action("systemd did not stop the booth in time")


def _power_route(action, args, label, ok_status, ok_message):
    client_ip = request.remote_addr if request else "unknown"
    logger.warning("[REMOTE CONTROL] %s requested from %s", label, client_ip)
    if pending_power_action() or SHUTDOWN_EVENT.is_set():
        return _power_error("A power action is already in progress", 409)
    allowed, detail = _sudo_allowed(*args)
    if not allowed:
        logger.error(
            "[POWER] %s refused: sudo does not allow 'systemctl %s' without a password (%s)",
            label,
            " ".join(args),
            detail,
        )
        return _power_error(
            f"Not possible: sudo does not allow 'systemctl {' '.join(args)}' for the booth's user. Nothing was changed."
        )
    if not announce_power_action(action):
        return _power_error("A power action is already in progress", 409)
    threading.Thread(
        target=_run_power_command, args=(args,), name="PowerAction", daemon=True
    ).start()
    return jsonify({"status": ok_status, "message": ok_message})


@app.route("/shutdown", methods=["POST"])
def remote_shutdown():
    return _power_route(
        "poweroff", ["poweroff"], "Shutdown", "shutting_down", "Powering off now..."
    )


@app.route("/reboot", methods=["POST"])
def remote_reboot():
    return _power_route(
        "reboot", ["reboot"], "Reboot", "rebooting", "Rebooting system now..."
    )


@app.route("/service/disable", methods=["POST"])
def remote_service_disable():
    client_ip = request.remote_addr if request else "unknown"
    logger.warning(
        "[REMOTE CONTROL] Service disable & reboot requested from %s", client_ip
    )
    unit = SERVICE_UNIT_NAME
    if pending_power_action() or SHUTDOWN_EVENT.is_set():
        return _power_error("A power action is already in progress", 409)
    for args in (["disable", unit], ["enable", unit], ["reboot"]):
        allowed, detail = _sudo_allowed(*args)
        if not allowed:
            logger.error(
                "[POWER] Service disable refused: sudo does not allow 'systemctl %s' (%s)",
                " ".join(args),
                detail,
            )
            return _power_error(
                f"Not possible: sudo does not allow 'systemctl {' '.join(args)}' for the booth's user. Nothing was changed."
            )
    if not announce_power_action("service_disabled"):
        return _power_error("A power action is already in progress", 409)

    # The disable happens NOW and is verified, so the answer is true. Only then the reboot.
    try:
        result = _sudo_systemctl("disable", unit)
        if result.returncode != 0:
            raise RuntimeError(
                (result.stderr or result.stdout).strip()[:200]
                or f"exit {result.returncode}"
            )
        verify = subprocess.run(
            ["systemctl", "is-enabled", unit],
            check=False,
            timeout=5,
            capture_output=True,
            text=True,
        )
        state_text = (verify.stdout or "").strip()
        if state_text != "disabled":
            raise RuntimeError(
                f"'systemctl is-enabled' says '{state_text or verify.stderr.strip()}'"
            )
    except Exception as exc:
        logger.error("[POWER] Could not disable %s: %s", unit, exc)
        cancel_power_action(f"disabling {unit} failed")
        return _power_error(
            f"The kiosk service could NOT be disabled ({exc}). Nothing was changed, no reboot."
        )
    logger.warning(
        "[POWER] %s is disabled and verified. Rebooting to the console. To restore the kiosk: sudo systemctl enable %s, then reboot.",
        unit,
        unit,
    )
    threading.Thread(
        target=_run_power_command,
        args=(["reboot"], ["enable", unit]),
        name="PowerAction",
        daemon=True,
    ).start()
    return jsonify(
        {
            "status": "disabling_and_rebooting",
            "message": f"Kiosk service disabled (verified). Rebooting to the console login. To restore: sudo systemctl enable {unit}, then reboot.",
        }
    )


@app.route("/ping")
def ping():
    return "ok"


@app.route("/clear_session", methods=["POST"])
def clear_session():
    """Review screen -> live view on request of a web page. The server also does this by
    itself after LATEST_TIMEOUT_SECONDS, so no client has to."""
    reset_to_live("web request", only_if_mode=("latest",))
    return jsonify({"status": "success"})


@app.route("/force_reset", methods=["POST"])
def force_reset():
    """Operator recovery (control page): back to the live view from any other state."""
    reset_to_live(
        "operator force reset",
        only_if_mode=("pre-capture-countdown", "countdown", "latest"),
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

    camera_fresh, camera_age_now = camera_is_fresh()
    camera_status = "OK" if camera_fresh else "FAULT"
    system_now = SYSTEM_TELEMETRY  # replaced whole by the monitor thread, never edited

    return jsonify(
        {
            "captures": total_captures,
            "uptime_hours": uptime_hours,
            "online": True,
            "cpu_temp": system_now["cpu_temp"],
            "memory_mb": system_now["memory_rss_mb"],
            "camera_status": camera_status,
            "camera_age": camera_age_now,
        }
    )


@app.route("/api/health", methods=["GET"])
def api_health():
    """Everything the control page's health card shows: read from memory and the ledger only."""
    camera_fresh, camera_age_now = camera_is_fresh()
    try:
        pending = len(ledger_snapshot())
    except Exception:
        pending = -1
    try:
        with open(UNRESOLVED_LEDGER_PATH, "r", encoding="utf-8", errors="replace") as f:
            unresolved = sum(1 for line in f if line.strip())
    except OSError:
        unresolved = 0
    captures_total, last_capture = 0, None
    if TELEMETRY_ENABLED:
        try:
            captures_total = telemetry.LIVE_STATS.get("total_captures", 0)
            last_capture = telemetry.LIVE_STATS.get("last_capture_iso")
        except Exception:
            pass
    try:
        usage = shutil.disk_usage(CAPTURE_DIR)
        disk_free_gb = round(usage.free / 1e9, 1)
        disk_used_percent = round(100.0 * usage.used / usage.total, 1)
    except OSError:
        disk_free_gb = disk_used_percent = None
    workers_ok = all(t.is_alive() for t in CRITICAL_WORKERS) and (
        CAMERA_THREAD is None or CAMERA_THREAD.is_alive()
    )
    return jsonify(
        {
            "camera_ok": camera_fresh,
            "camera_age": camera_age_now,
            "uptime_s": int(time.monotonic() - PROCESS_STARTED),
            "captures_total": captures_total,
            "last_capture": last_capture,
            "pending_uploads": pending,
            "unresolved_uploads": unresolved,
            "offline": NETWORK_OFFLINE.is_set(),
            "uploads_enabled": IK_UPLOADS_ENABLED,
            "disk_free_gb": disk_free_gb,
            "disk_used_percent": disk_used_percent,
            "workers_ok": workers_ok,
            "previous_fault": PREVIOUS_FAULT,
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
    # Running independently: this process is its own owner (signals, shutdown, exit code).
    start_backend_engine(blocking=True)
