"""Shared state of the booth: the objects that more than one part of the booth reads or writes.

Moved from server.py unchanged (same values, same comments, same order inside each group). This
module imports only the standard library, numpy and tuning.py. It starts no thread, reads no
file and writes no log when it is imported; it only creates objects.

Two kinds of names live here:

1. NEVER REBOUND (buffers filled in place, the session dict, locks, events, queues). They are
   created once, so they may be imported by name: `from state import frame_lock`.

2. REBOUND while the booth runs (the second group below). NEVER import these by value: a
   `from state import UI_FRAME_SEQ` would keep the value it had at import time for ever. Read and
   write them through the module: `import state` ... `state.UI_FRAME_SEQ += 1`. (server.py imports
   this module as shared_state, because it also has a route function called state().)

   name                         written by                         read by
   ACTIVE_STREAM_VIEWERS        generate_frames (frame_lock)       frame loop, generate_frames
   CAMERA_THREAD                frame loop (at its start)          health monitor, /api/health
   FRAME_ID                     frame loop (frame_lock)            generate_frames, /state
   GLOBAL_KNOBS, GLOBAL_BUTTON  update_knobs (hw_state_lock)       frame loop (GLOBAL_KNOBS: /state)
   SHARED_JPEG_STREAM_BUFFER    frame loop (frame_lock)            generate_frames
   SYSTEM_TELEMETRY             telemetry_worker (swapped whole)   /state, /api/stats
   UI_FRAME_SEQ                 frame loop (frame_lock)            the display, every frame
   DISPLAY_HEARTBEAT            the display                        health monitor
   LOOP_HEARTBEAT               frame loop                         health monitor
   CAMERA_HEARTBEAT             camera loop                        health monitor
   PRINT_JOB_STARTED            print worker                       health monitor
   camera_fail_count            camera loop (hw_state_lock)        /state
   last_frame_time              camera loop (hw_state_lock)        camera_is_fresh, /state
   capture_lock_until           frame loop, reset_to_live          frame loop
   PREVIOUS_FAULT               _load_previous_fault (start-up)    /api/health

server.py must have set OMP_NUM_THREADS and cv2.setNumThreads(1) before it imports this module
(numpy is imported here); it does, at the top of the file.
"""

import queue
import threading
import time

import numpy as np

from tuning import FRAME_H, FRAME_W, POSE_COUNTDOWN_SECONDS

# =========================================================================
# 1. NEVER REBOUND (importable by name)
# =========================================================================

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

FROZEN_DISPLAY_BUFFER = np.empty((FRAME_H, FRAME_W, 3), dtype=np.uint8, order="C")
FROZEN_DISPLAY_BUFFER.fill(0)

FROZEN_PRINT_BUFFER = np.empty((FRAME_W, FRAME_H), dtype=np.uint8, order="C")
FROZEN_PRINT_BUFFER.fill(255)

# Shared luminance buffer: utilized sequentially during Melt pass and post-capture receipt contrast prep
STATIC_MELT_GRAY_BUFFER = np.empty((FRAME_H, FRAME_W), dtype=np.uint8, order="C")

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
cam_raw_lock = threading.Lock()

printer_hardware_lock = threading.Lock()
cache_file_lock = threading.Lock()

hw_state_lock = threading.Lock()

SHUTDOWN_EVENT = threading.Event()
CRITICAL_WORKERS = []

upload_queue = queue.Queue(maxsize=32)
print_queue = queue.Queue(maxsize=4)

# Set while the last upload failed for good (offline); any upload that works clears it.
# The receipt uses it to choose between the normal and the offline text.
NETWORK_OFFLINE = threading.Event()

# The ONLINE / OFFLINE mark of the kiosk screen, kept up to date by network_status_worker().
# NETWORK_ONLINE is set while the booth has a network address and can reach the upload host.
# NETWORK_CHECKED is set once the first check is done (until then the screen shows nothing).
NETWORK_ONLINE = threading.Event()
NETWORK_CHECKED = threading.Event()

# =========================================================================
# 2. REBOUND WHILE THE BOOTH RUNS (use state.NAME, never `from state import NAME`)
# =========================================================================

GLOBAL_KNOBS = (0, 0, 0, 0, 0, 0)
GLOBAL_BUTTON = 1

SHARED_JPEG_STREAM_BUFFER = memoryview(b"")

UI_FRAME_SEQ = (
    0  # bumped (under frame_lock) each time UI_DISPLAY_BUFFER is fully rewritten
)

ACTIVE_STREAM_VIEWERS = 0
capture_lock_until = 0

last_frame_time = time.time()
camera_fail_count = 0

SYSTEM_TELEMETRY = {"ip": "0.0.0.0", "cpu_temp": "N/A", "memory_rss_mb": -1}

FRAME_ID = 0

PREVIOUS_FAULT = None  # the fault record of the previous run, loaded at start

# Heartbeats: one float assignment each, written by the thread they describe.
LOOP_HEARTBEAT = None
CAMERA_HEARTBEAT = None
DISPLAY_HEARTBEAT = None  # set by the display; None when the booth runs without one
PRINT_JOB_STARTED = None
CAMERA_THREAD = None
