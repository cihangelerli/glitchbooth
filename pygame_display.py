#!/usr/bin/env python3
"""
Glitch Booth Kiosk Presentation Engine - pygame_display.py (v8.2)
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

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s %(levelname)s [%(threadName)s] %(name)s: %(message)s",
)

logger = logging.getLogger("glitchbooth.display")

logger.info("[DISPLAY INIT] Importing backend server module")

# Import backend shared state and layout buffers directly from server context
import server

logger.info("[DISPLAY INIT] Starting backend engine in embedded mode")

# Launch the backend workers and Flask API seamlessly without blocking Pygame
server.start_backend_engine(blocking=False)

logger.info("[DISPLAY INIT] Backend engine started")

# ==============================================================================
# DYNAMIC STARTUP INVARIANTS & ENFORCEMENT
# ==============================================================================
print("\n[DISPLAY INIT] Executing structural memory layout assertions...")
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
print(
    f"[DISPLAY INIT] Invariants verified. Ingested Resolution: {OP_FRAME_W}x{OP_FRAME_H}"
)

# ==============================================================================
# PRESENTATION CANVASES & PERFECT PORTRAIT GEOMETRY MATH
# ==============================================================================
DISPLAY_W, DISPLAY_H = 768, 1365
TARGET_FPS = 60

# Video render frame target properties
VIDEO_RENDER_W = 640
VIDEO_RENDER_H = 480

# Exact portrait canvas grid spacing: horizontally center, fix 64px top margin
PADDING_LEFT = (DISPLAY_W - VIDEO_RENDER_W) // 2  # (768 - 640) // 2 = 64px
PADDING_TOP = (DISPLAY_W - VIDEO_RENDER_W) // 2

# video bounds calculation
VIDEO_RECT = pygame.Rect(PADDING_LEFT, PADDING_TOP, VIDEO_RENDER_W, VIDEO_RENDER_H)

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
last_processed_session_id = None
latest_display_surface = None
latest_qr_surface = None

latest_mode_start_time = None  # Tracks 20-second auto-fallback window

# Knob telemetry tracking registers & layout order mapping from index.html
last_knob_values = [None] * 6
knob_changed_times = [0.0] * 6
KNOB_ROWS = [
    (0, "ASCII Matrix"),
    (1, "Signal Drift"),
    (3, "Pixel Damage"),
    (4, "Melt"),
    (2, "Data Corruption"),
    (5, "Chroma Shift"),
]

# ==============================================================================
# RUN-ONCE BOOT DRIVER ENCODING ANALYSIS
# ==============================================================================
print("[DISPLAY INIT] Testing host OS image buffer driver capabilities...")
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

print(
    f"[DISPLAY INIT] Execution Pipeline Selected: {'Zero-Copy Frame View' if USE_FROMBUFFER else 'Surfarray Matrix Convert Fallback'}"
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
    ui_font = pygame.font.SysFont("Courier", 24, bold=True)
except Exception:
    ui_font = pygame.font.Font(None, 24)

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
print("[DISPLAY INIT] Cold compiling asset caches and memory gallery lookups...")

COUNTDOWN_SURFACES = {}
COUNTDOWN_RECTS = {}
try:
    font_engine = pygame.font.SysFont("Impact", 180)
except Exception:
    font_engine = pygame.font.Font(None, 180)

for count in [3, 2, 1, 0]:
    surf = font_engine.render(str(count), True, (0, 255, 0))
    COUNTDOWN_SURFACES[count] = surf.convert_alpha()
    rect = surf.get_rect()
    rect.center = VIDEO_RECT.center
    COUNTDOWN_RECTS[count] = rect

NOISE_CACHE = []
for _ in range(15):
    noise_arr = np.random.randint(
        0, 256, (VIDEO_RENDER_H, VIDEO_RENDER_W, 3), dtype=np.uint8
    )
    noise_surf = pygame.surfarray.make_surface(np.transpose(noise_arr, (1, 0, 2)))
    NOISE_CACHE.append(noise_surf.convert())

ticker_surface_array = []

all_images = []

if os.path.exists(server.CAPTURE_DIR):
    for f in os.listdir(server.CAPTURE_DIR):
        # Only include final color captures
        if not f.endswith("_color.jpg"):
            continue

        full_path = os.path.join(server.CAPTURE_DIR, f)

        # Extract timestamp from filename: "1781455701604_color.jpg" -> "1781455701604"
        try:
            timestamp_str = f.split("_")[0]
            timestamp = int(timestamp_str)

            # Store path and the extracted timestamp
            all_images.append((full_path, timestamp))

        except (ValueError, IndexError):
            # This handles files that might not follow the naming convention
            print(f"[DISPLAY INIT] Skipping malformed filename: {f}")
            continue

# newest first
all_images.sort(key=lambda x: x[1], reverse=True)

for img_path, _ in all_images[:100]:
    try:
        loaded_surf = pygame.image.load(img_path)
        scaled_surf = pygame.transform.scale(
            loaded_surf, (TICKER_ITEM_W, TICKER_ITEM_H)
        )
        ticker_surface_array.append(scaled_surf.convert())

    except Exception as err:
        print(f"[DISPLAY INIT] Skipping unreadable image asset {img_path}: {err}")

ticker_scroll_x = 0.0
ticker_velocity = 0.9
noise_animation_index = 0

print(
    f"[DISPLAY INIT] System pre-cached. Ribbon tape references: {len(ticker_surface_array)}"
)
print(
    "[DISPLAY INITIALIZATION] Core graphics pipeline entering main execution loop...\n"
)


# ==============================================================================
# AUDIO INITIALIZATION
# ==============================================================================
RUN_AUDIO = True


def audio_loop():

    sounds = []

    for wav in Path("static/sounds").glob("glitch-*.wav"):
        try:
            sounds.append(pygame.mixer.Sound(str(wav)))
            # print(f"[AUDIO INIT] Loaded {wav.name}")
        except Exception as err:
            print(f"[AUDIO INIT] Failed loading {wav.name}: {err}")

    print(f"[AUDIO INIT] Total sounds loaded: {len(sounds)}")

    if not sounds:
        return

    while RUN_AUDIO:
        sound = random.choice(sounds)

        channel = sound.play()

        if channel:
            while channel.get_busy() and RUN_AUDIO:
                time.sleep(0.05)

        time.sleep(random.uniform(0.5, 1.2))


pygame.mixer.init()
print("[AUDIO INIT] Mixer initialized")
threading.Thread(target=audio_loop, daemon=True).start()
print("[AUDIO INIT] Glitch Audio Started.")


# ==============================================================================
# PRIMARY 60Hz PRESENTATION MAIN LOOP
# ==============================================================================
try:
    while not server.SHUTDOWN_EVENT.is_set():
        # Handle structural Pygame inputs and physical terminal interrupts
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                print(
                    "[WATCHDOG] Window close event detected. Initiating clean exit..."
                )
                # server.graceful_shutdown_handler(None, None)
                raise KeyboardInterrupt
            elif event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    print(
                        "[WATCHDOG] Escape key override registered. Initiating systemic exit..."
                    )
                    # server.graceful_shutdown_handler(None, None)
                    raise KeyboardInterrupt
                elif event.key == pygame.K_c and (
                    pygame.key.get_mods() & pygame.KMOD_CTRL
                ):
                    print(
                        "[WATCHDOG] Ctrl+C shortcut combination detected. Initiating systemic exit..."
                    )
                    raise KeyboardInterrupt

        # Refresh virtual display root bounds
        virtual_canvas.fill((0, 0, 0))

        # Atomic transaction extraction of session flags from state machine
        with server.mode_lock:
            current_mode = server.ACTIVE_SESSION["mode"]
            current_session_id = server.ACTIVE_SESSION["session_id"]
            countdown_val = server.ACTIVE_SESSION["countdown_remaining"]
            upload_status = server.ACTIVE_SESSION.get("upload_status", "SUCCESS")
            current_knobs = server.ACTIVE_SESSION.get("knobs", [0] * 6)

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
        if current_mode in ("live", "countdown"):
            # Attempt non-blocking lock acquisition to secure presentation smoothness
            lock_acquired = server.frame_lock.acquire(blocking=False)
            if lock_acquired:
                try:
                    # --- MICRO-PROFILING HOOK ---
                    # Profiles memory footprint bandwidth timings for the 640x480x3 frame copies
                    t_start = time.perf_counter()

                    # Direct, fast memory copy directly inside lock context boundaries
                    local_frame_buffer[:] = server.UI_DISPLAY_BUFFER

                    t_duration = time.perf_counter() - t_start
                    if (
                        t_duration > 0.002
                    ):  # Log warning flags if copy exceeds 2.0ms window
                        print(
                            f"[PERF WARNING] Frame copy latency spike detected: {t_duration * 1000:.2f}ms"
                        )

                finally:
                    server.frame_lock.release()

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
                # Fallback implementation: Render last verified frame to sustain steady 60Hz cadence
                raw_video_surface = last_valid_surface

            if raw_video_surface is not None:
                mirrored_video_surface = pygame.transform.flip(
                    raw_video_surface, True, False
                )
                scaled_video_surface = pygame.transform.scale(
                    mirrored_video_surface, (VIDEO_RENDER_W, VIDEO_RENDER_H)
                )
                virtual_canvas.blit(scaled_video_surface, VIDEO_RECT)
            else:
                pygame.draw.rect(virtual_canvas, (20, 20, 25), VIDEO_RECT)

            # Draw crisp frame housing border
            pygame.draw.rect(virtual_canvas, (0, 255, 0), VIDEO_RECT, 2)

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

            # Blit countdown overlay if processing state calls for it
            if current_mode == "countdown" and countdown_val in COUNTDOWN_SURFACES:
                virtual_canvas.blit(
                    COUNTDOWN_SURFACES[countdown_val], COUNTDOWN_RECTS[countdown_val]
                )

            # Draw Header Title above the video stream
            header_surf = ui_font.render(
                "> GLITCH BOOTH release candidate 01", True, (0, 255, 0)
            )
            virtual_canvas.blit(header_surf, (PADDING_LEFT, CLEARANCE_MARGIN))

            # Draw Telemetry Panel (Knobs matched directly with index.html layouts)
            panel_y = TICKER_START_Y + TICKER_ITEM_H + CLEARANCE_MARGIN
            pygame.draw.line(
                virtual_canvas,
                (0, 255, 0),
                (PADDING_LEFT, panel_y),
                (PADDING_LEFT + VIDEO_RENDER_W, panel_y),
                1,
            )
            panel_y += CLEARANCE_MARGIN

            # Render Live Telemetry Panel matching index.html aesthetics and glow highlights
            # start_y = 750
            # row_height = 42
            for idx, (k_id, label) in enumerate(KNOB_ROWS):
                val = current_knobs[k_id] if k_id < len(current_knobs) else 0
                is_changing = (time.time() - knob_changed_times[k_id]) < 0.2

                row_y = panel_y + (idx * CLEARANCE_MARGIN)

                if is_changing:
                    color = (209, 255, 201)  # High-contrast highlight color (#d1ffc9)
                    # Create a subtle glow offset to replicate text-shadow
                    glow_surf = ui_font.render(f"> {label}", True, (0, 80, 0))
                    virtual_canvas.blit(glow_surf, (66, row_y + 2))
                else:
                    color = (0, 255, 0)  # Standard matrix terminal green

                # Render label text
                lbl_surf = ui_font.render(f"> {label}", True, color)
                virtual_canvas.blit(lbl_surf, (PADDING_LEFT, row_y))

                # Render value right-aligned
                val_surf = ui_font.render(str(val), True, color)
                val_x = DISPLAY_W - PADDING_LEFT - val_surf.get_width()
                virtual_canvas.blit(val_surf, (val_x, row_y))

            # Draw Kiosk System Instructions Panel
            panel_y += len(KNOB_ROWS) * CLEARANCE_MARGIN + LINE_PADDING

            pygame.draw.line(
                virtual_canvas,
                (0, 255, 0),
                (PADDING_LEFT, panel_y),
                (PADDING_LEFT + VIDEO_RENDER_W, panel_y),
                1,
            )
            panel_y += LINE_PADDING

            instructions = [
                "> -- <- LEFT          RIGHT -> ++",
                "> TURN THE KNOBS, COMBINE EFFECTS",
                "> SET GLITCH STRENGTH AS YOU WISH",
                "> PRESS BUTTON TO CAPTURE, WAIT 3 SEC",
                "> PRINT READY ALMOST INSTANTLY :)",
            ]

            for line in instructions:
                inst_surf = ui_font.render(line, True, (0, 255, 0))
                virtual_canvas.blit(inst_surf, (PADDING_LEFT, panel_y))
                panel_y += CLEARANCE_MARGIN

        # ----------------------------------------------------------------------
        # PROFILE 3: GLITCH COMPUTATION PROCESSING WINDOW
        # ----------------------------------------------------------------------
        elif current_mode == "processing":
            noise_animation_index += 1
            noise_frame = NOISE_CACHE[noise_animation_index % len(NOISE_CACHE)]
            virtual_canvas.blit(noise_frame, VIDEO_RECT)
            pygame.draw.rect(virtual_canvas, (0, 255, 0), VIDEO_RECT, 2)

        # ----------------------------------------------------------------------
        # PROFILE 4: LATEST SCREEN DISPLAY (ZERO-SCAN SPLICE INSERTIONS)
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

                    # Start timing the watchdog screen visibility duration
                    latest_mode_start_time = time.time()

                    if os.path.exists(color_path) and os.path.exists(qr_path):
                        try:
                            new_image = pygame.image.load(color_path)
                            ticker_item = pygame.transform.scale(
                                new_image, (TICKER_ITEM_W, TICKER_ITEM_H)
                            ).convert()

                            # Zero-Scan Memory Array Splice Execution
                            ticker_surface_array.insert(0, ticker_item)
                            if len(ticker_surface_array) > 100:
                                ticker_surface_array.pop()

                            # flip the image, scale and put it on display surface
                            latest_display_surface = pygame.transform.scale(
                                pygame.transform.flip(new_image, True, False),
                                (VIDEO_RENDER_W, VIDEO_RENDER_H),
                            ).convert()

                            raw_qr = pygame.image.load(qr_path)
                            latest_qr_surface = pygame.transform.scale(
                                raw_qr, (240, 240)
                            ).convert()

                            last_processed_session_id = current_session_id
                            print(
                                f"[DISPLAY LOGIC] Hot insertion successful for transaction id: {current_session_id}"
                            )
                        except Exception as load_err:
                            print(
                                f"[DISPLAY LOGIC] Post-processing storage file link syncing... {load_err}"
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
                pygame.draw.rect(virtual_canvas, (0, 255, 0), VIDEO_RECT, 2)

                # Render copy text from latest.html above the portrait showcase
                sys_id_text = f"> SYS_ID: {current_session_id}"
                status_text = "> STATUS: CAPTURE_SAVED"

                if upload_status == "SUCCESS":
                    upload_text = "> STATUS: UPLOAD_COMPLETE"
                elif upload_status == "FAILED":
                    upload_text = "> CLOUD_UPLOAD_FAILED"
                else:
                    upload_text = "> INIT_UPLOADING..."

                sys_surf = ui_font.render(sys_id_text, True, (0, 255, 0))
                status_surf = ui_font.render(status_text, True, (0, 255, 0))
                upload_surf = ui_font.render(upload_text, True, (0, 255, 0))

                virtual_canvas.blit(sys_surf, (PADDING_LEFT, 12))
                virtual_canvas.blit(status_surf, (PADDING_LEFT + 280, 12))
                virtual_canvas.blit(upload_surf, (PADDING_LEFT, 34))

                if latest_qr_surface is not None:
                    qr_x = (DISPLAY_W - 240) // 2
                    qr_y = VIDEO_RECT.bottom + 64
                    virtual_canvas.blit(latest_qr_surface, (qr_x, qr_y))
                    pygame.draw.rect(
                        virtual_canvas, (0, 255, 0), (qr_x, qr_y, 240, 240), 2
                    )
                # Watchdog Fallback: Automatically revert back to live loop after 20 seconds
                if latest_mode_start_time is not None and (
                    time.time() - latest_mode_start_time > 20.0
                ):
                    print(
                        "[WATCHDOG] 20-second display threshold reached. Reverting to live loop state."
                    )
                    with server.mode_lock:
                        server.ACTIVE_SESSION["mode"] = "live"
                    latest_mode_start_time = None
                # Render terminal footer copy below the scannable area
                footer_text = "> PRESS BUTTON TO RETURN TO LIVE VIEW"
                footer_surf = ui_font.render(footer_text, True, (0, 255, 0))
                virtual_canvas.blit(
                    footer_surf,
                    ((DISPLAY_W - footer_surf.get_width()) // 2, qr_y + 240 + 45),
                )

        # Inside your main while loop, right near the frame rendering blocks:
        if server.ACTIVE_SESSION.get("exit_to_terminal", False):
            print("[WATCHDOG] Web interface requested clean exit to terminal.")
            break

        # Apply transformation matching hardware portrait presentation alignment configurations
        rotated_canvas = pygame.transform.rotate(virtual_canvas, -90)
        hardware_screen.blit(rotated_canvas, (0, 0))
        pygame.display.flip()
        clock.tick(TARGET_FPS)

except KeyboardInterrupt:
    print("\n[WATCHDOG] Intercepted exit signal.")
    logger.info("[WATCHDOG] Intercepted display exit signal")
except Exception:
    logger.exception("[DISPLAY LOOP] Unexpected display loop crash")
    raise
finally:
    # 1. Alert the backend workers to stop processing immediately
    server.SHUTDOWN_EVENT.set()

    # 2. Alert the backend workers to stop processing immediately
    RUN_AUDIO = False
    pygame.mixer.quit()
    print("[AUDIO SHUTDOWN] Glitch Audio Stopped.")

    # 3. Release Pygame graphics contexts and unmount KMSDRM buffers
    pygame.quit()
    print("[UI SHUTDOWN COMPLETE] Kiosk graphics engine unmounted.")

    # 4. Hand over total execution control to the final server handler
    try:
        server.graceful_shutdown_handler(None, None)
    except Exception as e:
        print(f"[UI SHUTDOWN] Direct execution fallback required: {e}")
        sys.exit(0)
