"""Process-wide logging: one queue-based setup for server, display and telemetry.

Moved from server.py unchanged (names, behaviour, log format). The only difference is where
the default log folder comes from (config/paths.py). server.py calls setup_logging() at the
same point in its start-up as before.
"""

import atexit
import faulthandler
import logging
import logging.handlers
import os
import queue
import re
import sys
import threading
import time
from pathlib import Path

from config.paths import DEFAULT_LOG_DIR

# ==================================================
# One logging setup for the whole process (server, display and telemetry all use it).
# Records go through a queue to a listener thread, so no worker (camera, processing loop,
# display) ever waits on the disk or the terminal. Two outputs:
#   - terminal (stderr: tty1 under systemd, or your ssh session): LOG_CONSOLE_LEVEL
#   - one file per service start in LOG_DIR: LOG_LEVEL, size-capped
# The newest LOG_KEEP_SESSIONS sessions are kept. The previous session is always kept
# (it is the one that just ended or crashed); short sessions, such as a crash loop, are
# pruned before long ones.
def _env_float(name, default, minimum):
    try:
        value = float(os.getenv(name, default))
    except (TypeError, ValueError):
        return default
    return value if value >= minimum else default


def _env_level(name, default):
    level = getattr(logging, str(os.getenv(name, "")).strip().upper(), None)
    return level if isinstance(level, int) else default


LOG_DIR = Path(os.getenv("LOG_DIR") or DEFAULT_LOG_DIR)
LOG_KEEP_SESSIONS = int(_env_float("LOG_KEEP_SESSIONS", 3, 1))
LOG_FILE_MAX_MB = _env_float("LOG_FILE_MAX_MB", 25, 1)  # per file ...
LOG_FILE_SEGMENTS = int(
    _env_float("LOG_FILE_SEGMENTS", 6, 1)
)  # ... plus this many older ones
LOG_SHORT_SESSION_SECONDS = 120

_LOG_FORMAT = (
    "%(asctime)s.%(msecs)03d %(levelname)-8s [%(threadName)s] %(name)s: %(message)s"
)
_LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
_LOG_STEM_RE = re.compile(r"^(glitchbooth-\d{8}-\d{6})\.")
_log_state = {
    "listener": None,
    "queue_handler": None,
    "fault_file": None,
    "fault_path": None,
    "closed": False,
}
_log_state_lock = threading.Lock()
_throttle_state = {}
_throttle_lock = threading.Lock()


def _prune_old_log_sessions(current_stem):
    sessions = {}
    for entry in LOG_DIR.iterdir():
        match = _LOG_STEM_RE.match(entry.name)
        if match and match.group(1) != current_stem:
            sessions.setdefault(match.group(1), []).append(entry)

    def last_write(files):
        newest = 0.0
        for path in files:
            try:
                newest = max(newest, path.stat().st_mtime)
            except OSError:
                pass
        return newest

    def seconds(stem, files):
        try:
            started = time.mktime(
                time.strptime(stem[len("glitchbooth-") :], "%Y%m%d-%H%M%S")
            )
        except ValueError:
            return 0.0
        return last_write(files) - started

    newest_first = sorted(
        sessions, key=lambda stem: last_write(sessions[stem]), reverse=True
    )
    slots = LOG_KEEP_SESSIONS - 1  # the session being started takes one slot
    keep = newest_first[: min(1, slots)]
    rest = newest_first[1:]
    long_ones = [
        stem
        for stem in rest
        if seconds(stem, sessions[stem]) >= LOG_SHORT_SESSION_SECONDS
    ]
    short_ones = [stem for stem in rest if stem not in long_ones]
    for stem in long_ones + short_ones:
        if len(keep) >= slots:
            break
        keep.append(stem)
    for stem, files in sessions.items():
        if stem not in keep:
            for path in files:
                try:
                    path.unlink()
                except OSError:
                    pass


def _log_uncaught_exception(exc_type, exc, tb):
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc, tb)
        return
    logging.getLogger("glitchbooth").critical(
        "[CRASH] Uncaught exception in the main thread", exc_info=(exc_type, exc, tb)
    )


def _log_uncaught_thread_exception(args):
    if args.exc_type is SystemExit:
        return
    thread_name = args.thread.name if args.thread else "unknown"
    logging.getLogger("glitchbooth").critical(
        "[CRASH] Uncaught exception in thread %s",
        thread_name,
        exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
    )


def setup_logging():
    """Idempotent: every entry point may call it; the first call does the work."""
    with _log_state_lock:
        if _log_state["listener"] is not None or _log_state["closed"]:
            return
        formatter = logging.Formatter(_LOG_FORMAT, _LOG_DATE_FORMAT)
        handlers = []

        console = logging.StreamHandler(sys.stderr)
        console.setLevel(_env_level("LOG_CONSOLE_LEVEL", logging.INFO))
        console.setFormatter(formatter)
        handlers.append(console)

        stem = time.strftime("glitchbooth-%Y%m%d-%H%M%S")
        file_error = None
        try:
            LOG_DIR.mkdir(parents=True, exist_ok=True)
            file_handler = logging.handlers.RotatingFileHandler(
                LOG_DIR / f"{stem}.log",
                maxBytes=int(LOG_FILE_MAX_MB * 1024 * 1024),
                backupCount=LOG_FILE_SEGMENTS,
                encoding="utf-8",
            )
            file_handler.setLevel(_env_level("LOG_LEVEL", logging.INFO))
            file_handler.setFormatter(formatter)
            handlers.append(file_handler)
            _prune_old_log_sessions(stem)
            # Native crashes (OpenCV, SDL) write their traceback here.
            fault_path = LOG_DIR / f"{stem}.fault"
            fault_file = open(fault_path, "w")
            faulthandler.enable(file=fault_file)
            _log_state["fault_file"], _log_state["fault_path"] = fault_file, fault_path
        except OSError as exc:
            file_error = exc

        log_queue = queue.Queue(-1)
        queue_handler = logging.handlers.QueueHandler(log_queue)
        root = logging.getLogger()
        # A library imported before this point (python-escpos does) can attach its own console
        # handler to the root logger; it printed every message a second time, in a plain format.
        # Our listener is the only console writer from here on.
        foreign_console = [
            handler
            for handler in root.handlers
            if type(handler) is logging.StreamHandler
            and handler.stream in (sys.stderr, sys.stdout)
        ]
        for handler in foreign_console:
            root.removeHandler(handler)
        root.setLevel(min(handler.level for handler in handlers))
        root.addHandler(queue_handler)
        listener = logging.handlers.QueueListener(
            log_queue, *handlers, respect_handler_level=True
        )
        listener.start()
        _log_state["listener"], _log_state["queue_handler"] = listener, queue_handler

        for noisy in ("urllib3", "PIL", "waitress", "werkzeug", "requests", "asyncio"):
            logging.getLogger(noisy).setLevel(logging.WARNING)
        sys.excepthook = _log_uncaught_exception
        threading.excepthook = _log_uncaught_thread_exception
        atexit.register(shutdown_logging)

    boot = logging.getLogger("glitchbooth")
    for handler in foreign_console:
        boot.debug(
            "[LOG] Removed a console handler that a library had attached to the root logger: %r",
            handler,
        )
    if file_error is None:
        boot.info(
            "[LOG] Session %s started. Terminal >= %s, file >= %s, keeping %d sessions in %s",
            stem,
            logging.getLevelName(console.level),
            logging.getLevelName(file_handler.level),
            LOG_KEEP_SESSIONS,
            LOG_DIR,
        )
    else:
        boot.error(
            "[LOG] Session file logging is unavailable (%s); logging to the terminal only",
            file_error,
        )


def shutdown_logging():
    """Flush and stop the log listener. Safe to call more than once."""
    with _log_state_lock:
        listener = _log_state["listener"]
        if listener is None:
            return
        _log_state["listener"] = None
        _log_state["closed"] = True
        queue_handler = _log_state["queue_handler"]
        fault_file, fault_path = _log_state["fault_file"], _log_state["fault_path"]
    listener.stop()  # drains the queue before it returns
    root = logging.getLogger()
    root.removeHandler(queue_handler)
    root.addHandler(
        logging.StreamHandler(sys.stderr)
    )  # late messages still reach the terminal
    for handler in listener.handlers:
        try:
            handler.flush()
            handler.close()
        except Exception:
            pass
    if fault_file is not None:
        try:
            faulthandler.disable()
            fault_file.close()
            if fault_path.stat().st_size == 0:
                fault_path.unlink()
        except OSError:
            pass


def log_throttled(log, level, key, interval, msg, *args, exc_info=False):
    """Log at most once per `interval` seconds for each `key`. When messages were
    suppressed in between, the next one says how many. Returns True if it logged."""
    now = time.monotonic()
    with _throttle_lock:
        last, suppressed = _throttle_state.get(key, (None, 0))
        if last is not None and now - last < interval:
            _throttle_state[key] = (last, suppressed + 1)
            return False
        _throttle_state[key] = (now, 0)
    if suppressed:
        msg += " (%d similar messages suppressed)"
        args += (suppressed,)
    log.log(level, msg, *args, exc_info=exc_info)
    return True
