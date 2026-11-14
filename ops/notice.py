"""Session notices: the short text the booth screen and the web page show to visitors.

Moved from server.py unchanged (function bodies identical, names unchanged).
A notice is two fields of the shared session dict (ACTIVE_SESSION["notice"] and
["notice_until"]). Callers that already hold mode_lock use the *_locked helpers,
everyone else uses set_notice(). current_notice() stays in server.py for now: it also
reads the power action and the camera state, which do not live in their final modules
yet. Imports only the standard library and state.py.
"""

import time

from state import ACTIVE_SESSION, mode_lock


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
