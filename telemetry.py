#!/usr/bin/env python3
"""
Glitch Booth Telemetry Engine - telemetry.py (v2.7-Production-Frozen)
Implements non-blocking queue-driven execution architecture to eliminate UI latency.
Maintains an immutable append-only historical log file alongside thread-safe
in-memory stats counters to eliminate line-by-line parsing overhead during sync loops.
"""

import os
import json
import time
import base64
import threading
import queue
from pathlib import Path
import requests

# Base Configuration & Directory Enforcement
TELEMETRY_DIR = Path(__file__).parent / "telemetry"
TELEMETRY_DIR.mkdir(exist_ok=True)

CAPTURE_LOG_PATH = TELEMETRY_DIR / "capture_log.jsonl"
STATS_JSON_PATH = TELEMETRY_DIR / "stats.json"

KNOB_ACTIVATION_THRESHOLD = 10

ALL_EFFECTS = [
    "ascii_matrix",
    "signal_drift",
    "data_corruption",
    "pixel_damage",
    "melt",
    "chroma_shift",
    "no_glitch",
]

# Threading Locks, Global State Injection, and Channels
_telemetry_io_lock = threading.Lock()
_init_lock = threading.Lock()
_telemetry_queue = queue.Queue(maxsize=5000)
_telemetry_initialized = False
_shutdown_event = None  # Injected via init_telemetry to prevent circular imports

# System Running Baselines
BOOT_TIMESTAMP = time.time()
START_TIME = time.time()

# --- CORE IN-MEMORY ACCUMULATOR ---
LIVE_STATS = {
    "total_captures": 0,
    "first_capture_iso": None,
    "last_capture_iso": None,
    "effect_counts": {eff: 0 for eff in ALL_EFFECTS},
    "combo_tracker": {},
}


def _increment_ram_metrics_internal(knobs: list, ts_epoch: int):
    """Surgically mutates running statistics in RAM with O(1) constant complexity."""
    global LIVE_STATS
    LIVE_STATS["total_captures"] += 1

    ts_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts_epoch))
    if not LIVE_STATS["first_capture_iso"]:
        LIVE_STATS["first_capture_iso"] = ts_iso
    LIVE_STATS["last_capture_iso"] = ts_iso

    chosen_effects = []
    for idx, val in enumerate(knobs):
        if idx < 6 and val >= KNOB_ACTIVATION_THRESHOLD:
            eff_name = ALL_EFFECTS[idx]
            LIVE_STATS["effect_counts"][eff_name] += 1
            chosen_effects.append(eff_name)

    if not chosen_effects:
        LIVE_STATS["effect_counts"]["no_glitch"] += 1
        chosen_effects.append("no_glitch")

    combo_key = tuple(sorted(chosen_effects))
    LIVE_STATS["combo_tracker"][combo_key] = (
        LIVE_STATS["combo_tracker"].get(combo_key, 0) + 1
    )


def init_telemetry(shutdown_event=None):
    """
    Guarantees execution paths exist and performs a single-pass recovery scan
    at startup to cleanly rebuild memory states from the append-only ledger log.
    Accepts an optional threading.Event instance to decouple from server.py.
    """
    global _telemetry_initialized, LIVE_STATS, _shutdown_event

    if shutdown_event is not None:
        _shutdown_event = shutdown_event

    if _telemetry_initialized:
        return

    with _init_lock:
        if _telemetry_initialized:
            return

        TELEMETRY_DIR.mkdir(exist_ok=True)
        if not CAPTURE_LOG_PATH.exists():
            with open(CAPTURE_LOG_PATH, "w") as f:
                pass

        LIVE_STATS = {
            "total_captures": 0,
            "first_capture_iso": None,
            "last_capture_iso": None,
            "effect_counts": {eff: 0 for eff in ALL_EFFECTS},
            "combo_tracker": {},
        }

        print(
            "[TELEMETRY] Executing single-pass transaction log replay to sync live RAM metrics..."
        )
        try:
            with open(CAPTURE_LOG_PATH, "r") as f:
                for line in f:
                    if not line.strip():
                        continue
                    try:
                        rec = json.loads(line)
                        knobs = rec.get("knobs", [0] * 6)
                        ts_epoch = rec.get("ts", int(time.time()))
                        _increment_ram_metrics_internal(knobs, ts_epoch)
                    except Exception:
                        continue
            print(
                f"[TELEMETRY] State restoration complete. Restored {LIVE_STATS['total_captures']} historical events."
            )
        except Exception as e:
            print(
                f"[TELEMETRY ERROR] Critical error during startup state restoration: {e}"
            )

        _telemetry_initialized = True


def request_shutdown():
    """Exposed clean boundary function for server.py to trigger a graceful flush."""
    print("[TELEMETRY] Shutdown requested. Injecting sentinel token to flush queue...")
    try:
        # Bounded wait: guarantees sentinel delivery if the consumer clears a slot 
        # within 2 seconds, but prevents an infinite hang if the thread is bricked.
        _telemetry_queue.put(None, timeout=2.0)
    except queue.Full:
        print("[TELEMETRY CRITICAL] Failed to inject shutdown sentinel; queue remained full.")


def record_capture(session_id: str, knobs: list, *args, **kwargs):
    """
    Asynchronous entry point for sessions. Instantly mutates memory arrays
    and streams the transaction frame down to the disk tracking worker thread.
    """
    init_telemetry()
    ts_now = int(time.time())

    payload = {
        "ts": ts_now,
        "session_id": session_id,
        "knobs": [int(k) for k in knobs],
    }

    with _telemetry_io_lock:
        _increment_ram_metrics_internal(payload["knobs"], payload["ts"])

    try:
        _telemetry_queue.put(payload, block=False)
    except queue.Full:
        print(
            "[TELEMETRY CRITICAL] Async disk queue is full! Discarding disk payload to save memory."
        )


def compile_summary_stats():
    """Generates website json structures cleanly from memory snapshots."""
    init_telemetry()

    with _telemetry_io_lock:
        total_captures = LIVE_STATS["total_captures"]
        first_capture_iso = LIVE_STATS["first_capture_iso"]
        last_capture_iso = LIVE_STATS["last_capture_iso"]
        effect_counts_snapshot = LIVE_STATS["effect_counts"].copy()
        combo_tracker_snapshot = LIVE_STATS["combo_tracker"].copy()

    uptime_hours = round((time.time() - BOOT_TIMESTAMP) / 3600.0, 2)
    total_effect_invocations = sum(effect_counts_snapshot.values())

    effects_popularity = {}
    for eff in ALL_EFFECTS:
        key_name = f"{eff}_usage_pct"
        if total_effect_invocations > 0:
            percentage = (effect_counts_snapshot[eff] / total_effect_invocations) * 100
            effects_popularity[key_name] = round(percentage, 1)
        else:
            effects_popularity[key_name] = 0.0

    if combo_tracker_snapshot:
        winning_key = max(combo_tracker_snapshot, key=combo_tracker_snapshot.get)
        most_popular_combo = " + ".join(
            [e.replace("_", " ").title() for e in winning_key]
        )
    else:
        most_popular_combo = "RAW_CAPTURE_FLOW"

    stats_payload = {
        "total_captures": total_captures,
        "uptime_hours": uptime_hours,
        "first_capture_iso": first_capture_iso,
        "last_capture_iso": last_capture_iso,
        "effects_popularity": effects_popularity,
        "most_popular_combo": most_popular_combo,
    }

    try:
        with open(STATS_JSON_PATH, "w") as f:
            json.dump(stats_payload, f, indent=2)
        print("[TELEMETRY] Local stats.json refreshed cleanly from memory.")
    except Exception as e:
        print(f"[TELEMETRY ERROR] Failed writing local stats.json payload: {e}")


def upload_stats_to_imagekit() -> bool:
    private_key = os.getenv("IK_PRIVATE_KEY")
    if not private_key:
        print(
            "[TELEMETRY WARNING] Sync bypassed: IK_PRIVATE_KEY is missing from execution context."
        )
        return False

    if not STATS_JSON_PATH.exists():
        return False

    try:
        with _telemetry_io_lock:
            with open(STATS_JSON_PATH, "r") as f:
                file_content = f.read()

        auth_string = f"{private_key}:"
        auth_bytes = base64.b64encode(auth_string.encode("utf-8")).decode("utf-8")

        headers = {"Authorization": f"Basic {auth_bytes}"}
        files = {"file": (str(STATS_JSON_PATH.name), file_content, "application/json")}
        data = {
            "fileName": "stats.json",
            "useUniqueFileName": "false",
            "folder": "/telemetry",
        }

        response = requests.post(
            "https://upload.imagekit.io/api/v1/files/upload",
            headers=headers,
            files=files,
            data=data,
            timeout=15,
        )

        if response.status_code in [200, 201]:
            print(
                "[TELEMETRY CLOUD] stats.json synchronized cleanly to ImageKit distribution node."
            )
            return True
        else:
            print(
                f"[TELEMETRY ERROR] ImageKit Rejected Post: Status {response.status_code} - {response.text}"
            )
            return False
    except Exception as e:
        print(f"[TELEMETRY ERROR] Cloud pipeline unexpected fault exception: {e}")
        return False


def telemetry_sync_worker(interval_seconds: int = 120):
    """Target loop for periodic stats compilation and cloud synchronization."""
    if _shutdown_event is None:
        raise RuntimeError(
            "init_telemetry(SHUTDOWN_EVENT) must be called before starting telemetry workers"
        )

    print(
        f"[TELEMETRY ENGINE] Asynchronous tracking sync container activated. Intervals: {interval_seconds}s."
    )
    
    while True:
        if _shutdown_event.wait(timeout=interval_seconds):
            break

        compile_summary_stats()
        upload_stats_to_imagekit()

    print("[TELEMETRY] Final stats flush sequence engaged...")
    compile_summary_stats()
    print("[TELEMETRY] Periodic sync worker terminated gracefully.")


def telemetry_consumer_worker():
    """CONSUMER NODE: Pulls log events from memory channel and writes to disk out-of-band."""
    print("[TELEMETRY ENGINE] Asynchronous tracking consumer backend initialized.")
    
    while True:
        try:
            payload = _telemetry_queue.get()
            
            if payload is None:
                _telemetry_queue.task_done()
                break

            with _telemetry_io_lock:
                try:
                    with open(CAPTURE_LOG_PATH, "a") as f:
                        f.write(json.dumps(payload) + "\n")
                    print(
                        f"[TELEMETRY ASYNC] Capture session {payload['session_id']} safely written to disk."
                    )
                except Exception as append_err:
                    print(
                        f"[TELEMETRY ERROR] Disk queue execution worker append failure: {append_err}"
                    )

            _telemetry_queue.task_done()

        except Exception as queue_fault:
            print(
                f"[TELEMETRY SYSTEM CRITICAL] Consumer loop crash intercept: {queue_fault}"
            )
            time.sleep(1)

    print("[TELEMETRY] Storage consumer worker terminated gracefully.")

