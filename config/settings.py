"""Settings: where things are and how the booth talks to the world.

Everything here comes from .env (read with os.getenv / _cfg_number) or is a deployment fact
(cloud, ports, upload retry policy, safety timeouts, feature switches). Moved from server.py
unchanged and in the same order. Looks, feel and performance live in tuning.py.
"""

import base64
import logging
import os
from urllib.parse import urlsplit

from config.env import _cfg_number

logger = logging.getLogger("glitchbooth.config.settings")

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

# KIOSK SCREEN (pygame_display.py reads these, so every screen colour and label has one source)
# ------------------------------------------------------------------------------
BOOTH_VERSION = "beta 04"  # shown in the screen header and written to the log at start

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

# ONLINE / OFFLINE mark on the kiosk screen. The probe is one TCP connection to the host the
# uploads need, opened and closed at once: one DNS lookup and one handshake per interval, no data.
NETWORK_PROBE_HOST = urlsplit(IK_UPLOAD_URL).hostname
NETWORK_PROBE_PORT = 443
# Seconds between two probes: while the last one connected, and while it did not.
NETWORK_PROBE_INTERVAL = 120.0
NETWORK_RETRY_INTERVAL = 30.0
NETWORK_PROBE_TIMEOUT = 3.0  # seconds a probe may take to connect
# Shortest time between two probes (also after a failed upload), and how often the
# address and the upload flag are looked at (no traffic).
NETWORK_PROBE_MIN_GAP = 10.0
NETWORK_CHECK_STEP = 5.0


SERIAL_PORTS = ["/dev/ttyUSB0", "/dev/ttyACM0"]
CAMERA_STALE_TIMEOUT = 5
CAMERA_REFRESH_INTERVAL = 21600

NAKED_CAPTURE_ENABLED = True
TELEMETRY_ENABLED = (
    True  # Set False to disable all capture logging and telemetry threads
)
