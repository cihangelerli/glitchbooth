#!/usr/bin/env python3
"""
Glitch Booth Kiosk Presentation Engine - pygame_display.py (v8.3)
Production-Ready Freeze: Implements atomic snapshot frame decoupling, micro-profiling
telemetry, dynamic layout invariants, and unified event-driven shutdown integration.
"""

import logging
import os
import sys
import time

import numpy as np

# Force SDL to target the Pi 5 direct hardware DRM pipeline
os.environ["SDL_VIDEODRIVER"] = "kmsdrm"
# Ensure audio queries are immediately stubbed out to prevent ALSA context blocks
os.environ["SDL_AUDIODRIVER"] = "alsa"
os.environ["SDL_VIDEO_KMSDRM_SCALING"] = "1"

import random
import threading
from pathlib import Path

import pygame

# Import backend shared state and layout buffers directly from server context.
# Importing it also sets up logging for the whole process (terminal + session file).
import server

logger = logging.getLogger("glitchbooth.display")

logger.info("[DISPLAY INIT] Backend module imported; starting the backend engine in embedded mode")

# Launch the backend workers and Flask API seamlessly without blocking Pygame
# This module owns the process: it installs the signal handlers and runs the shutdown sequence.
server.install_signal_handlers()
server.start_backend_engine(blocking=False)

logger.info("[DISPLAY INIT] Backend engine started")

# ==============================================================================
# DYNAMIC STARTUP INVARIANTS & ENFORCEMENT
# ==============================================================================
logger.info("[DISPLAY INIT] Executing structural memory layout assertions...")
assert server.UI_DISPLAY_BUFFER.ndim == 3, (
    f"CRITICAL: Shape mismatch! Expected 3 dimensions (H, W, C), got {server.UI_DISPLAY_BUFFER.ndim}"
)
assert server.UI_DISPLAY_BUFFER.shape[2] == 3, (
    f"CRITICAL: Channel mismatch! Expected 3 color channels, got {server.UI_DISPLAY_BUFFER.shape[2]}"
)
assert server.UI_DISPLAY_BUFFER.flags["C_CONTIGUOUS"] == True, (
    "CRITICAL: Memory layout fault! Shared buffer array is not C-contiguous."
)

# Ingest current resolution parameters dynamically from backend memory allocation
OP_FRAME_H, OP_FRAME_W = (
    server.UI_DISPLAY_BUFFER.shape[0],
    server.UI_DISPLAY_BUFFER.shape[1],
)
logger.info(
    "[DISPLAY INIT] Invariants verified. Ingested resolution: %dx%d",
    OP_FRAME_W,
    OP_FRAME_H,
)

# ==============================================================================
# PRESENTATION CANVASES & PERFECT PORTRAIT GEOMETRY MATH
# ==============================================================================
# SCREEN SETTINGS: every number, colour and sentence of the kiosk screen is set here, once.
# Values the booth shares with server.py are NOT typed again; they are read from it:
#   server.FRAME_W / FRAME_H (video size), server.POSE_COUNTDOWN_SECONDS, server.FLASH_COLOUR,
#   server.FLASH_DURATION, server.BOOTH_VERSION and the UI_* colours.
# Only values this screen alone needs are set below.
DISPLAY_W, DISPLAY_H = 768, 1365  # the portrait screen, in pixels
TARGET_FPS = 60

# Video render frame target properties: the same size the server produces
VIDEO_RENDER_W = server.FRAME_W
VIDEO_RENDER_H = server.FRAME_H

# Colours (R, G, B). The shared ones come from server.py (UI_* constants).
UI_GREEN = server.UI_GREEN
UI_HIGHLIGHT = server.UI_HIGHLIGHT
UI_GLOW_DIM = server.UI_GLOW_DIM
UI_ALERT = server.UI_ALERT
UI_NOTICE = server.UI_NOTICE
UI_VIDEO_PLACEHOLDER = server.UI_VIDEO_PLACEHOLDER
UI_BLACK = (0, 0, 0)

# Fonts (name, size). A fallback font is used when the named one is not installed.
UI_FONT_NAME, UI_FONT_SIZE = "Courier", 24
COUNTDOWN_FONT_NAME, COUNTDOWN_FONT_SIZE = "Impact", 165
PRINTING_FONT_NAME, PRINTING_FONT_SIZE = "Impact", 84
NOTICE_FONT_NAME, NOTICE_FONT_SIZE, NOTICE_FONT_FALLBACK_SIZE = "Courier", 30, 34
QR_GUIDE_FONT_NAME, QR_GUIDE_FONT_RATIO, QR_GUIDE_FONT_FALLBACK_RATIO = "Impact", 0.55, 0.6

# Layout in pixels
VIDEO_BORDER_PX = 2  # border around the video / photo
HEADER_ROW1_Y, HEADER_ROW2_Y = 12, 34  # the two status rows on the latest screen
HEADER_STATUS_X = 280  # x offset of the second status text on row 1
KNOB_GLOW_OFFSET = (2, 2)  # shadow of a just-moved knob row (x, y)
LATEST_QR_PX = 240  # QR on the latest screen (a square)
LATEST_QR_GAP = 64  # gap between the photo and that QR
LATEST_FOOTER_GAP = 45  # gap between that QR and the footer sentence
NOTICE_SIDE_MARGIN = 60  # the notice text wraps this much narrower than the video
NOTICE_BOX_PADDING = (40, 24)  # extra width and height of the notice box
NOTICE_LINE_GAP = 6
NOTICE_BORDER_PX = 2
NOTICE_EDGE_GAP = 24  # gap from the top of the video, or below the PRINTING text

# Sentences. They follow the web pages (latest.html, index.html), which this file cannot see.
UI_HEADER = f"> GLITCH BOOTH {server.BOOTH_VERSION}"
UI_KNOB_ERROR = "HATA!"  # shown instead of the value of a knob whose effect was switched off
PRINTING_TEXT = "PRINTING"
QR_GUIDE_TEXT = "QR"
UI_INSTRUCTIONS = [
    "> SOL <- DAHA ÇOK             DAHA AZ -> SAĞ",
    ">  -     EFEKT                EFEKT       + ",
    "---------------------------------------------",
    "> AYARLARLA OYNA, KENDİ EFEKTİNİ YARAT,",
    "> GLITCH ETKİSİNİ İSTEDİĞİN GİBİ AYARLA,",
    f"> DÜĞMEYE BAS, POZ VERMEK İÇİN {server.POSE_COUNTDOWN_SECONDS} SANİYEN VAR!",
    "> GLITCH FOTOGRAFIN HIZLICA BASILACAK. :)",
]
UI_LATEST_SYS_ID = "> SYS_ID: {}"
UI_LATEST_SAVED = "> STATUS: CAPTURE_SAVED"
UI_LATEST_UPLOAD = {
    "SUCCESS": "> STATUS: UPLOAD_COMPLETE",
    "FAILED": "> CLOUD_UPLOAD_FAILED",
}
UI_LATEST_UPLOAD_PENDING = "> INIT_UPLOADING..."
UI_LATEST_FOOTER = "> FOTOĞRAF ÇEKME MODUNA GERİ DÖNMEK İÇİN DÜĞMEYE BAS!"

# Ticker tape of photos under the video, and the glitch sound loop
TICKER_MAX_ITEMS = 100  # photos kept in the ticker (also the most picked at start)
TICKER_SPEED = 0.9  # pixels per frame
AUDIO_DIR = "static/sounds"
AUDIO_PATTERN = "glitch-*.wav"
AUDIO_PAUSE_SECONDS = (0.5, 1.2)  # random pause between two sounds
AUDIO_POLL_SECONDS = 0.05

# Exact portrait canvas grid spacing: horizontally center, fix 64px top margin
PADDING_LEFT = (DISPLAY_W - VIDEO_RENDER_W) // 2  # (768 - 640) // 2 = 64px
PADDING_TOP = (DISPLAY_W - VIDEO_RENDER_W) // 2

# video bounds calculation
VIDEO_RECT = pygame.Rect(PADDING_LEFT, PADDING_TOP, VIDEO_RENDER_W, VIDEO_RENDER_H)

# ==============================================================================
# LIVE FEED QR GUIDE INDICATOR CONFIGURATION
# ==============================================================================
QR_GUIDE_ENABLED = True
QR_GUIDE_SIZE = 130  # px — square side length
QR_GUIDE_MARGIN = 2  # px — gap from image edges
QR_GUIDE_CORNER = (
    "bottom-left"  # Options: "bottom-left", "bottom-right", "top-left", "top-right"
)
QR_GUIDE_OPACITY = 151  # 0–255: 0 = fully transparent, 255 = fully opaque

# Ticker Tape Presentation Layer Geometry
TICKER_ITEM_W = 240
TICKER_ITEM_H = 180
TICKER_PADDING = 10
TICKER_STRIDE = TICKER_ITEM_W + TICKER_PADDING

# Structural Layout Spacing Constants

CLEARANCE_MARGIN = 32  # Standard gap between distinct UI blocks
LINE_PADDING = CLEARANCE_MARGIN // 2  # Gap between a divider line and text contents
ROW_HEIGHT = 42


TICKER_START_Y = (
    VIDEO_RECT.bottom + CLEARANCE_MARGIN
)  # safe structural clearance beneath the video window

# Pre-allocate steady workspace memory for our isolated frame snapshot copies
local_frame_buffer = np.empty((OP_FRAME_H, OP_FRAME_W, 3), dtype=np.uint8, order="C")

# Local presentation tracking registers
last_valid_surface = None
last_seq = -1  # last server.UI_FRAME_SEQ we copied
scaled_video_surface = None  # cached mirrored + scaled video frame
last_processed_session_id = None
latest_display_surface = None
latest_qr_surface = None

# Knob telemetry tracking registers & layout order mapping from index.html
last_knob_values = [None] * 6
knob_changed_times = [0.0] * 6
# Row order stays fixed; names follow the primary effect attached to each knob.
KNOB_ROW_ORDER = [0, 1, 3, 4, 2, 5]
_knob_names = server.get_knob_labels()
# True for a knob whose effect the server switched off at startup (bad tunable): its row
# shows a warning instead of the knob value.
_knob_failed = server.get_knob_failures()
KNOB_ROWS = [(k_id, _knob_names[k_id]) for k_id in KNOB_ROW_ORDER]

# ==============================================================================
# RUN-ONCE BOOT DRIVER ENCODING ANALYSIS
# ==============================================================================
logger.info("[DISPLAY INIT] Testing zero-copy frame buffer path")

USE_FROMBUFFER = True
try:
    test_matrix = np.zeros((OP_FRAME_H, OP_FRAME_W, 3), dtype=np.uint8, order="C")
    _ = pygame.image.frombuffer(test_matrix.data, (OP_FRAME_W, OP_FRAME_H), "BGR")
except ValueError as exc:
    USE_FROMBUFFER = False
    logger.warning(
        "[DISPLAY INIT] Zero-copy frame buffer unavailable; using surfarray fallback: %s",
        exc,
    )

logger.info(
    "[DISPLAY INIT] Execution pipeline selected: %s",
    "zero-copy frame view" if USE_FROMBUFFER else "surfarray matrix fallback",
)

# ==============================================================================
# SURGICAL SUBSYSTEM INITIALIZATION (ALSA AUDIOLOCK BYPASS)
# ==============================================================================
logger.info(
    "[DISPLAY INIT] Initializing Pygame display. SDL_VIDEODRIVER=%s SDL_AUDIODRIVER=%s",
    os.getenv("SDL_VIDEODRIVER"),
    os.getenv("SDL_AUDIODRIVER"),
)

pygame.display.init()
pygame.font.init()

# Cold compile retro terminal style typography engine
try:
    ui_font = pygame.font.SysFont(UI_FONT_NAME, UI_FONT_SIZE, bold=True)
except Exception:
    ui_font = pygame.font.Font(None, UI_FONT_SIZE)

flags = pygame.DOUBLEBUF | pygame.HWSURFACE | pygame.FULLSCREEN

logger.info(
    "[DISPLAY INIT] Creating fullscreen hardware surface: %sx%s flags=%s",
    DISPLAY_W,
    DISPLAY_H,
    flags,
)

hardware_screen = pygame.display.set_mode((DISPLAY_W, DISPLAY_H), flags)
virtual_canvas = pygame.Surface((DISPLAY_W, DISPLAY_H))

logger.info("[DISPLAY INIT] Fullscreen hardware surface created")

pygame.mouse.set_visible(False)
clock = pygame.time.Clock()

# ==============================================================================
# ASSET CACHE GENERATION & PRE-CONVERSION
# ==============================================================================
logger.info("[DISPLAY INIT] Cold compiling asset caches and memory gallery lookups...")

# 1. Configurable Corner Placement (authoritative setting from server.py)
COUNTDOWN_CORNER = getattr(server, "COUNTDOWN_CORNER", "bottom-center")
COUNTDOWN_MARGIN_X = int(os.getenv("COUNTDOWN_MARGIN_X", "20"))
COUNTDOWN_MARGIN_Y = int(os.getenv("COUNTDOWN_MARGIN_Y", "16"))

CORNER_COUNTDOWN_SURFACES = {}
CORNER_COUNTDOWN_RECTS = {}
try:
    corner_font = pygame.font.SysFont(COUNTDOWN_FONT_NAME, COUNTDOWN_FONT_SIZE)
except Exception:
    corner_font = pygame.font.Font(None, COUNTDOWN_FONT_SIZE)

for count in range(server.POSE_COUNTDOWN_SECONDS, 0, -1):  # e.g. 3, 2, 1
    surf = corner_font.render(str(count), True, UI_GREEN)
    CORNER_COUNTDOWN_SURFACES[count] = surf.convert_alpha()
    rect = surf.get_rect()

    # Dynamic positioning based on COUNTDOWN_CORNER
    if COUNTDOWN_CORNER in ("bottom-center", "bottom-middle"):
        rect.midbottom = (
            VIDEO_RECT.centerx,
            VIDEO_RECT.bottom - COUNTDOWN_MARGIN_Y,
        )
    elif COUNTDOWN_CORNER == "top-left":
        rect.topleft = (
            VIDEO_RECT.left + COUNTDOWN_MARGIN_X,
            VIDEO_RECT.top + COUNTDOWN_MARGIN_Y,
        )
    elif COUNTDOWN_CORNER == "bottom-right":
        rect.bottomright = (
            VIDEO_RECT.right - COUNTDOWN_MARGIN_X,
            VIDEO_RECT.bottom - COUNTDOWN_MARGIN_Y,
        )
    elif COUNTDOWN_CORNER == "bottom-left":
        rect.bottomleft = (
            VIDEO_RECT.left + COUNTDOWN_MARGIN_X,
            VIDEO_RECT.bottom - COUNTDOWN_MARGIN_Y,
        )
    elif COUNTDOWN_CORNER == "top-right":
        rect.topright = (
            VIDEO_RECT.right - COUNTDOWN_MARGIN_X,
            VIDEO_RECT.top + COUNTDOWN_MARGIN_Y,
        )
    else:  # default bottom-center
        rect.midbottom = (
            VIDEO_RECT.centerx,
            VIDEO_RECT.bottom - COUNTDOWN_MARGIN_Y,
        )

    CORNER_COUNTDOWN_RECTS[count] = rect

# 2. Pre-render centered "PRINTING" overlay for post-capture state
try:
    printing_font = pygame.font.SysFont(PRINTING_FONT_NAME, PRINTING_FONT_SIZE)
except Exception:
    printing_font = pygame.font.Font(None, PRINTING_FONT_SIZE)

PRINTING_SURF = printing_font.render(PRINTING_TEXT, True, UI_GREEN).convert_alpha()
PRINTING_RECT = PRINTING_SURF.get_rect()
PRINTING_RECT.center = VIDEO_RECT.center

# 3. Notice box (camera problem in the live view, print problem while printing)
try:
    notice_font = pygame.font.SysFont(NOTICE_FONT_NAME, NOTICE_FONT_SIZE, bold=True)
except Exception:
    notice_font = pygame.font.Font(None, NOTICE_FONT_FALLBACK_SIZE)
NOTICE_COLOR = UI_NOTICE
_notice_cache = {"text": None, "surfaces": []}


def wrap_notice(text, font, max_width):
    lines, current = [], ""
    for word in text.split():
        trial = word if not current else current + " " + word
        if not current or font.size(trial)[0] <= max_width:
            current = trial
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def notice_surfaces(text):
    if text != _notice_cache["text"]:
        _notice_cache["text"] = text
        _notice_cache["surfaces"] = [
            notice_font.render(line, True, NOTICE_COLOR)
            for line in wrap_notice(
                text, notice_font, VIDEO_RECT.width - NOTICE_SIDE_MARGIN
            )
        ]
    return _notice_cache["surfaces"]


def draw_notice(canvas, text, where):
    # where: "center" (live view), "below_printing" (under the PRINTING text), "top" (review screen)
    lines = notice_surfaces(text)
    if not lines:
        return
    line_h = lines[0].get_height() + NOTICE_LINE_GAP
    box = pygame.Rect(
        0,
        0,
        max(surf.get_width() for surf in lines) + NOTICE_BOX_PADDING[0],
        line_h * len(lines) + NOTICE_BOX_PADDING[1],
    )
    box.centerx = VIDEO_RECT.centerx
    if where == "below_printing":
        box.top = PRINTING_RECT.bottom + NOTICE_EDGE_GAP
    elif where == "top":
        box.top = VIDEO_RECT.top + NOTICE_EDGE_GAP
    else:
        box.centery = VIDEO_RECT.centery
    pygame.draw.rect(canvas, UI_BLACK, box)
    pygame.draw.rect(canvas, NOTICE_COLOR, box, NOTICE_BORDER_PX)
    y = box.top + NOTICE_BOX_PADDING[1] // 2
    for surf in lines:
        canvas.blit(surf, (box.centerx - surf.get_width() // 2, y))
        y += line_h

# QR Guide Indicator — pre-render once at startup
if QR_GUIDE_ENABLED:
    qr_guide_surface = pygame.Surface((QR_GUIDE_SIZE, QR_GUIDE_SIZE))
    qr_guide_surface.fill(UI_GREEN)
    try:
        qr_guide_font = pygame.font.SysFont(
            QR_GUIDE_FONT_NAME, int(QR_GUIDE_SIZE * QR_GUIDE_FONT_RATIO)
        )
    except Exception:
        qr_guide_font = pygame.font.Font(
            None, int(QR_GUIDE_SIZE * QR_GUIDE_FONT_FALLBACK_RATIO)
        )
    qr_text_surf = qr_guide_font.render(QR_GUIDE_TEXT, True, UI_BLACK)
    qr_text_rect = qr_text_surf.get_rect(
        center=(QR_GUIDE_SIZE // 2, QR_GUIDE_SIZE // 2)
    )
    qr_guide_surface.blit(qr_text_surf, qr_text_rect)
    qr_guide_surface.set_alpha(QR_GUIDE_OPACITY)
    _corner = QR_GUIDE_CORNER.lower()
    if _corner == "bottom-right":
        QR_GUIDE_POS = (
            VIDEO_RECT.right - QR_GUIDE_SIZE - QR_GUIDE_MARGIN,
            VIDEO_RECT.bottom - QR_GUIDE_SIZE - QR_GUIDE_MARGIN,
        )
    elif _corner == "top-left":
        QR_GUIDE_POS = (
            VIDEO_RECT.left + QR_GUIDE_MARGIN,
            VIDEO_RECT.top + QR_GUIDE_MARGIN,
        )
    elif _corner == "top-right":
        QR_GUIDE_POS = (
            VIDEO_RECT.right - QR_GUIDE_SIZE - QR_GUIDE_MARGIN,
            VIDEO_RECT.top + QR_GUIDE_MARGIN,
        )
    else:  # default: bottom-left
        QR_GUIDE_POS = (
            VIDEO_RECT.left + QR_GUIDE_MARGIN,
            VIDEO_RECT.bottom - QR_GUIDE_SIZE - QR_GUIDE_MARGIN,
        )

ticker_surface_array = []

# Target captures_archive folder (with fallback to captures if archive is empty or missing)
target_dir = getattr(server, "ARCHIVE_DIR", "captures_archive")
if not os.path.exists(target_dir):
    target_dir = server.CAPTURE_DIR

candidate_images = []

# 1. Collect all images from the archive folder (Name-Agnostic: accepts any valid image extension)
if os.path.exists(target_dir):
    for f in os.listdir(target_dir):
        if f.lower().endswith((".jpg", ".jpeg", ".png")):
            candidate_images.append(os.path.join(target_dir, f))

# 2. If archive has fewer than TICKER_MAX_ITEMS images, supplement with images from active captures
if (
    len(candidate_images) < TICKER_MAX_ITEMS
    and os.path.exists(server.CAPTURE_DIR)
    and server.CAPTURE_DIR != target_dir
):
    for f in os.listdir(server.CAPTURE_DIR):
        if f.lower().endswith((".jpg", ".jpeg", ".png")):
            candidate_images.append(os.path.join(server.CAPTURE_DIR, f))

# 3. Select up to TICKER_MAX_ITEMS random unique images across the entire pool
sample_size = min(TICKER_MAX_ITEMS, len(candidate_images))
selected_images = (
    random.sample(candidate_images, sample_size) if candidate_images else []
)
logger.info(
    "[DISPLAY INIT] Selected %d random photos from the archive (out of %d available).",
    len(selected_images),
    len(candidate_images),
)

# 4. Pre-scale and load the random pool into the ticker tape
for img_path in selected_images:
    try:
        loaded_surf = pygame.image.load(img_path)
        scaled_surf = pygame.transform.scale(
            loaded_surf, (TICKER_ITEM_W, TICKER_ITEM_H)
        )
        ticker_surface_array.append(scaled_surf.convert())
    except Exception as err:
        logger.warning("[DISPLAY INIT] Skipping unreadable image asset %s: %s", img_path, err)

ticker_scroll_x = 0.0
ticker_velocity = TICKER_SPEED

logger.info(
    "[DISPLAY INIT] System pre-cached. Ribbon tape references: %d",
    len(ticker_surface_array),
)
logger.info("[DISPLAY INIT] Core graphics pipeline entering the main execution loop")


# ==============================================================================
# AUDIO INITIALIZATION
# ==============================================================================
RUN_AUDIO = True


def audio_loop():

    sounds = []

    for wav in Path(AUDIO_DIR).glob(AUDIO_PATTERN):
        try:
            sounds.append(pygame.mixer.Sound(str(wav)))
        except Exception as err:
            logger.warning("[AUDIO INIT] Failed loading %s: %s", wav.name, err)

    logger.info("[AUDIO INIT] Total sounds loaded: %d", len(sounds))

    if not sounds:
        return

    while RUN_AUDIO:
        sound = random.choice(sounds)

        channel = sound.play()

        if channel:
            while channel.get_busy() and RUN_AUDIO:
                time.sleep(AUDIO_POLL_SECONDS)

        time.sleep(random.uniform(*AUDIO_PAUSE_SECONDS))


pygame.mixer.init()
logger.info("[AUDIO INIT] Mixer initialized")
threading.Thread(target=audio_loop, daemon=True).start()
logger.info("[AUDIO INIT] Glitch audio started")


# ==============================================================================
# PRIMARY 60Hz PRESENTATION MAIN LOOP
# ==============================================================================
try:
    server.DISPLAY_HEARTBEAT = time.monotonic()  # from here on the health monitor watches this loop
    while not server.SHUTDOWN_EVENT.is_set():
        server.DISPLAY_HEARTBEAT = time.monotonic()
        # Handle structural Pygame inputs and physical terminal interrupts
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                logger.info("[WATCHDOG] Window close event detected. Initiating clean exit...")
                raise KeyboardInterrupt
            elif event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    logger.info("[WATCHDOG] Escape key registered. Initiating exit...")
                    raise KeyboardInterrupt
                elif event.key == pygame.K_c and (
                    pygame.key.get_mods() & pygame.KMOD_CTRL
                ):
                    logger.info("[WATCHDOG] Ctrl+C shortcut detected. Initiating exit...")
                    raise KeyboardInterrupt

        # Refresh virtual display root bounds
        virtual_canvas.fill(UI_BLACK)

        # Atomic transaction extraction of session flags from state machine
        with server.mode_lock:
            current_mode = server.ACTIVE_SESSION["mode"]
            current_session_id = server.ACTIVE_SESSION["session_id"]
            countdown_val = server.ACTIVE_SESSION["countdown_remaining"]
            countdown_start_time = server.ACTIVE_SESSION.get("countdown_start_time", 0)
            upload_status = server.ACTIVE_SESSION["upload_status"]
            current_knobs = server.ACTIVE_SESSION.get("knobs", [0] * 6)
        # Session notice, power action in progress, or the camera message (locks taken one at a time)
        current_notice = server.current_notice()

        # Update micro-timers tracking live knob changes for dynamic UI highlighting
        if current_knobs and len(current_knobs) >= 6:
            for i in range(6):
                val = current_knobs[i]
                if last_knob_values[i] is not None and last_knob_values[i] != val:
                    knob_changed_times[i] = time.time()
                last_knob_values[i] = val

        # ----------------------------------------------------------------------
        # PROFILE 1 & 2: LIVE PREVIEW & COUNTDOWN MODES
        # ----------------------------------------------------------------------
        if current_mode in ("live", "pre-capture-countdown", "countdown"):
            # Attempt non-blocking lock acquisition to secure presentation smoothness
            new_frame = False
            lock_acquired = server.frame_lock.acquire(blocking=False)
            if lock_acquired:
                try:
                    seq = server.UI_FRAME_SEQ
                    if seq != last_seq:
                        new_frame = True
                        # --- MICRO-PROFILING HOOK ---
                        t_start = time.perf_counter()
                        local_frame_buffer[:] = server.UI_DISPLAY_BUFFER
                        t_duration = time.perf_counter() - t_start
                        if t_duration > 0.002:
                            server.log_throttled(
                                logger,
                                logging.WARNING,
                                "display-frame-copy",
                                10.0,
                                "[PERF] Frame copy latency spike: %.2f ms",
                                t_duration * 1000,
                            )
                        last_seq = seq
                finally:
                    server.frame_lock.release()

            if new_frame:
                # Materialize surface wrapper via boot-validated configuration routing
                if USE_FROMBUFFER:
                    raw_video_surface = pygame.image.frombuffer(
                        local_frame_buffer.data, (OP_FRAME_W, OP_FRAME_H), "BGR"
                    )
                else:
                    raw_video_surface = pygame.surfarray.make_surface(
                        np.transpose(local_frame_buffer, (1, 0, 2))
                    )
                last_valid_surface = raw_video_surface
            else:
                # No new backend frame (or backend holds the lock): reuse last verified frame
                raw_video_surface = last_valid_surface

            if raw_video_surface is not None:
                if new_frame or scaled_video_surface is None:
                    mirrored_video_surface = pygame.transform.flip(
                        raw_video_surface, True, False
                    )
                    scaled_video_surface = pygame.transform.scale(
                        mirrored_video_surface, (VIDEO_RENDER_W, VIDEO_RENDER_H)
                    )
                virtual_canvas.blit(scaled_video_surface, VIDEO_RECT)
            else:
                pygame.draw.rect(virtual_canvas, UI_VIDEO_PLACEHOLDER, VIDEO_RECT)

            # Draw crisp frame housing border
            pygame.draw.rect(virtual_canvas, UI_GREEN, VIDEO_RECT, VIDEO_BORDER_PX)

            # Draw QR placement guide indicator (live & countdown only)
            if QR_GUIDE_ENABLED and current_mode != "countdown":
                virtual_canvas.blit(qr_guide_surface, QR_GUIDE_POS)

            # Compute Ticker Tape Ribbon Physics
            ticker_scroll_x += ticker_velocity

            if ticker_surface_array:
                base_x = -(ticker_scroll_x % TICKER_STRIDE)
                start_idx = int(ticker_scroll_x // TICKER_STRIDE)
                x_offset = base_x
                idx = 0
                while x_offset < DISPLAY_W:
                    surf_to_draw = ticker_surface_array[
                        (start_idx + idx) % len(ticker_surface_array)
                    ]
                    virtual_canvas.blit(surf_to_draw, (int(x_offset), TICKER_START_Y))
                    x_offset += TICKER_STRIDE
                    idx += 1

            # Blit pre-capture countdown in configured corner over live feed
            if (
                current_mode == "pre-capture-countdown"
                and countdown_val in CORNER_COUNTDOWN_SURFACES
            ):
                virtual_canvas.blit(
                    CORNER_COUNTDOWN_SURFACES[countdown_val],
                    CORNER_COUNTDOWN_RECTS[countdown_val],
                )

            # Blit post-capture: FLASH then "PRINTING" overlay over frozen feed
            elif current_mode == "countdown":
                c_delta = time.monotonic() - countdown_start_time
                if c_delta < server.FLASH_DURATION:
                    virtual_canvas.fill(server.FLASH_COLOUR, VIDEO_RECT)
                else:
                    virtual_canvas.blit(PRINTING_SURF, PRINTING_RECT)

            # Notice over the video (camera problem, print problem)
            if current_notice:
                draw_notice(
                    virtual_canvas,
                    current_notice,
                    "below_printing" if current_mode == "countdown" else "center",
                )

            # Draw Header Title above the video stream
            header_surf = ui_font.render(UI_HEADER, True, UI_GREEN)
            virtual_canvas.blit(header_surf, (PADDING_LEFT, CLEARANCE_MARGIN))

            # Draw Telemetry Panel (Knobs matched directly with index.html layouts)
            panel_y = TICKER_START_Y + TICKER_ITEM_H + CLEARANCE_MARGIN
            pygame.draw.line(
                virtual_canvas,
                UI_GREEN,
                (PADDING_LEFT, panel_y),
                (PADDING_LEFT + VIDEO_RENDER_W, panel_y),
                1,
            )
            panel_y += CLEARANCE_MARGIN

            # Render Live Telemetry Panel matching index.html aesthetics and glow highlights
            for idx, (k_id, label) in enumerate(KNOB_ROWS):
                val = current_knobs[k_id] if k_id < len(current_knobs) else 0
                is_changing = (
                    time.time() - knob_changed_times[k_id]
                ) < server.UI_KNOB_HIGHLIGHT_SECONDS

                row_y = panel_y + (idx * CLEARANCE_MARGIN)

                if is_changing:
                    color = UI_HIGHLIGHT  # High-contrast highlight color (#d1ffc9)
                    # Create a subtle glow offset to replicate text-shadow
                    glow_surf = ui_font.render(f"> {label}", True, UI_GLOW_DIM)
                    virtual_canvas.blit(
                        glow_surf,
                        (PADDING_LEFT + KNOB_GLOW_OFFSET[0], row_y + KNOB_GLOW_OFFSET[1]),
                    )
                else:
                    color = UI_GREEN  # Standard matrix terminal green

                # Render label text
                lbl_surf = ui_font.render(f"> {label}", True, color)
                virtual_canvas.blit(lbl_surf, (PADDING_LEFT, row_y))

                # Render value right-aligned (a warning instead, if the effect is switched off)
                if _knob_failed[k_id]:
                    val_surf = ui_font.render(UI_KNOB_ERROR, True, UI_ALERT)
                else:
                    val_surf = ui_font.render(str(val), True, color)
                val_x = DISPLAY_W - PADDING_LEFT - val_surf.get_width()
                virtual_canvas.blit(val_surf, (val_x, row_y))

            # Draw Kiosk System Instructions Panel
            panel_y += len(KNOB_ROWS) * CLEARANCE_MARGIN + LINE_PADDING

            pygame.draw.line(
                virtual_canvas,
                UI_GREEN,
                (PADDING_LEFT, panel_y),
                (PADDING_LEFT + VIDEO_RENDER_W, panel_y),
                1,
            )
            panel_y += LINE_PADDING
            pygame.draw.line(
                virtual_canvas,
                UI_GREEN,
                (PADDING_LEFT, panel_y),
                (PADDING_LEFT + VIDEO_RENDER_W, panel_y),
                1,
            )
            panel_y += LINE_PADDING

            for line in UI_INSTRUCTIONS:
                inst_surf = ui_font.render(line, True, UI_GREEN)
                virtual_canvas.blit(inst_surf, (PADDING_LEFT, panel_y))
                panel_y += CLEARANCE_MARGIN

        # ----------------------------------------------------------------------
        # PROFILE 3: LATEST SCREEN DISPLAY (ZERO-SCAN SPLICE INSERTIONS)
        # ----------------------------------------------------------------------
        elif current_mode == "latest":
            if current_session_id is not None:
                if current_session_id != last_processed_session_id:
                    color_path = os.path.join(
                        server.CAPTURE_DIR, f"{current_session_id}_color.jpg"
                    )
                    qr_path = os.path.join(
                        server.SAVED_DIR, f"{current_session_id}_qr.png"
                    )

                    if os.path.exists(color_path) and os.path.exists(qr_path):
                        try:
                            new_image = pygame.image.load(color_path)
                            ticker_item = pygame.transform.scale(
                                new_image, (TICKER_ITEM_W, TICKER_ITEM_H)
                            ).convert()

                            # Zero-Scan Memory Array Splice Execution
                            ticker_surface_array.insert(0, ticker_item)
                            if len(ticker_surface_array) > TICKER_MAX_ITEMS:
                                ticker_surface_array.pop()

                            # flip the image, scale and put it on display surface
                            latest_display_surface = pygame.transform.scale(
                                pygame.transform.flip(new_image, True, False),
                                (VIDEO_RENDER_W, VIDEO_RENDER_H),
                            ).convert()

                            raw_qr = pygame.image.load(qr_path)
                            latest_qr_surface = pygame.transform.scale(
                                raw_qr, (LATEST_QR_PX, LATEST_QR_PX)
                            ).convert()

                            last_processed_session_id = current_session_id
                            logger.info(
                                "[DISPLAY LOGIC] Latest screen loaded for session %s",
                                current_session_id,
                            )
                        except Exception as load_err:
                            server.log_throttled(
                                logger,
                                logging.WARNING,
                                "display-latest-load",
                                5.0,
                                "[DISPLAY LOGIC] Files for session %s are not readable yet: %s",
                                current_session_id,
                                load_err,
                            )
                    else:
                        # Safety Fallback: Grab the exact frame state frozen during processing execution
                        try:
                            with server.frame_lock:
                                local_frozen = server.FROZEN_DISPLAY_BUFFER.copy()
                            if USE_FROMBUFFER:
                                raw_frozen = pygame.image.frombuffer(
                                    local_frozen.data, (OP_FRAME_W, OP_FRAME_H), "BGR"
                                )
                            else:
                                raw_frozen = pygame.surfarray.make_surface(
                                    np.transpose(local_frozen, (1, 0, 2))
                                )
                            flipped_frozen = pygame.transform.flip(
                                raw_frozen, True, False
                            )
                            latest_display_surface = pygame.transform.scale(
                                flipped_frozen, (VIDEO_RENDER_W, VIDEO_RENDER_H)
                            ).convert()

                        except Exception:
                            logger.exception(
                                "[DISPLAY LOGIC] Failed to build latest-screen frozen-frame fallback for session %s",
                                current_session_id,
                            )

                if latest_display_surface is not None:
                    virtual_canvas.blit(latest_display_surface, VIDEO_RECT)
                pygame.draw.rect(virtual_canvas, UI_GREEN, VIDEO_RECT, VIDEO_BORDER_PX)

                # Render copy text from latest.html above the portrait showcase
                sys_id_text = UI_LATEST_SYS_ID.format(current_session_id)
                status_text = UI_LATEST_SAVED
                upload_text = UI_LATEST_UPLOAD.get(
                    upload_status, UI_LATEST_UPLOAD_PENDING
                )

                sys_surf = ui_font.render(sys_id_text, True, UI_GREEN)
                status_surf = ui_font.render(status_text, True, UI_GREEN)
                upload_surf = ui_font.render(upload_text, True, UI_GREEN)

                virtual_canvas.blit(sys_surf, (PADDING_LEFT, HEADER_ROW1_Y))
                virtual_canvas.blit(
                    status_surf, (PADDING_LEFT + HEADER_STATUS_X, HEADER_ROW1_Y)
                )
                virtual_canvas.blit(upload_surf, (PADDING_LEFT, HEADER_ROW2_Y))

                if latest_qr_surface is not None:
                    qr_x = (DISPLAY_W - LATEST_QR_PX) // 2
                    qr_y = VIDEO_RECT.bottom + LATEST_QR_GAP
                    virtual_canvas.blit(latest_qr_surface, (qr_x, qr_y))
                    pygame.draw.rect(
                        virtual_canvas,
                        UI_GREEN,
                        (qr_x, qr_y, LATEST_QR_PX, LATEST_QR_PX),
                        2,
                    )
                if current_notice:
                    draw_notice(virtual_canvas, current_notice, "top")
                # Render terminal footer copy below the scannable area
                footer_surf = ui_font.render(UI_LATEST_FOOTER, True, UI_GREEN)
                virtual_canvas.blit(
                    footer_surf,
                    (
                        (DISPLAY_W - footer_surf.get_width()) // 2,
                        qr_y + LATEST_QR_PX + LATEST_FOOTER_GAP,
                    ),
                )

        if server.ACTIVE_SESSION.get("exit_to_terminal", False):
            logger.info("[WATCHDOG] Web interface requested clean exit to terminal.")
            break

        # Apply transformation matching hardware portrait presentation alignment configurations
        rotated_canvas = pygame.transform.rotate(virtual_canvas, -90)
        hardware_screen.blit(rotated_canvas, (0, 0))
        pygame.display.flip()
        clock.tick(TARGET_FPS)

except KeyboardInterrupt:
    # Esc, window close or Ctrl+C on the booth's keyboard: a deliberate exit to the terminal.
    logger.info("[WATCHDOG] Intercepted display exit signal")
    server.request_shutdown("exit_to_terminal", 0, "exit requested at the booth")

except Exception:
    # The traceback is logged here. The booth then exits with code 1, so systemd restarts it
    # (an exit code of 0 would be taken as "finished on purpose" and nothing would restart).
    logger.exception("[DISPLAY LOOP] Unexpected display loop crash")
    server.request_shutdown("fault", 1, "the display loop crashed")

finally:
    # 1. Make sure every worker has been told to stop (a no-op if already requested)
    server.request_shutdown("fault", 1, "the display loop ended without a shutdown request")

    # 2. Stop audio processing
    RUN_AUDIO = False
    try:
        pygame.mixer.quit()
        logger.info("[AUDIO SHUTDOWN] Glitch audio stopped")
    except Exception:
        pass

    # 3. Release Pygame graphics contexts and unmount KMSDRM buffers
    try:
        pygame.quit()
        logger.info("[UI SHUTDOWN] Kiosk graphics engine unmounted")
    except Exception:
        pass

    # 4. The one and only backend teardown; it returns the exit code for the reason.
    try:
        exit_code = server.run_shutdown_sequence()
    except Exception:
        logger.exception("[UI SHUTDOWN] Backend shutdown failed; exiting with a failure code")
        server.shutdown_logging()
        exit_code = 1

sys.exit(exit_code)
