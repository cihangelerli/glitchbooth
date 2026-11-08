"""Where everything lives.

Pure on purpose: standard library only, no environment access, no logging, no side effects.
It is imported before .env is loaded.
"""

import os
from pathlib import Path

# The project folder, i.e. the folder that holds server.py (this file is <project>/config/paths.py).
# abspath, not resolve(): symlinks are NOT followed, so a captures/ symlink to a data disk
# keeps working exactly as before.
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT = Path(ROOT_DIR)

ENV_FILE = ROOT / ".env"
DEFAULT_LOG_DIR = ROOT / "logs"  # LOG_DIR in .env still overrides it

# Capture folders (plain strings: the code joins them with os.path.join and f-strings).
CAPTURE_DIR = os.path.join(ROOT_DIR, "captures")
SAVED_DIR = os.path.join(ROOT_DIR, "saved")
ARCHIVE_DIR = os.path.join(ROOT_DIR, "captures_archive")
NAKED_DIR = os.path.join(ROOT_DIR, "captures_naked")
NAKED_ARCHIVE_DIR = os.path.join(ROOT_DIR, "naked_archive")

# Bookkeeping files.
PENDING_LEDGER_PATH = os.path.join(ROOT_DIR, "pending_uploads.txt")
UNRESOLVED_LEDGER_PATH = os.path.join(ROOT_DIR, "unresolved_uploads.txt")
FAULT_RECORD_PATH = os.path.join(ROOT_DIR, "last_fault.json")
