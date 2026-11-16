"""Effect pipeline: which effects act on which knob, the plan, and the per-frame run.

Moved from server.py unchanged (function and class bodies identical, names unchanged).
No thread, file access or log line when this module is imported; it only creates its
buffers and tables. (Importing it imports every effect module.)
"""

import logging

from core.effects.ascii_matrix import _acts_ascii_matrix, apply_ascii_matrix
from core.effects.chroma_bleed import _acts_chroma_bleed, apply_chroma_bleed
from core.effects.chroma_shift import _acts_chroma_shift, apply_chroma_shift
from core.effects.contour_ripple import _acts_contour_ripple, apply_contour_ripple
from core.effects.crt import apply_crt_overlay
from core.effects.data_mosh import _acts_data_mosh, apply_data_mosh
from core.effects.melt import _acts_melt, apply_melt
from core.effects.moire_overlay import apply_moire_overlay
from core.effects.packet_corruption import (
    _acts_packet_corruption,
    apply_packet_corruption,
)
from core.effects.pixellation import _acts_pixellation, apply_pixellation
from core.effects.posterize import _acts_posterize, apply_posterize
from core.effects.scanline_ridge import _acts_scanline_ridge, apply_scanline_ridge
from core.effects.signal_drift import _acts_signal_drift, apply_signal_drift
from core.effects.slice_glitch import _acts_slice_glitch, apply_slice_glitch
from core.scale import n
# EFFECT_COST_WEIGHT, EFFECT_LABELS and EFFECT_STATS_NAME are also read by name from
# globals() in _check_effect_tables(), so they must stay imported here.
from tuning import (
    DISABLED_EFFECTS,
    EFFECT_COST_WEIGHT,
    EFFECT_KNOB_WINDOW,
    EFFECT_LABELS,
    EFFECT_ORDER,
    EFFECT_SKIP_DURING_WARMUP,
    EFFECT_STATS_MIN_KNOB,
    EFFECT_STATS_NAME,
    KNOB_ATTACHMENTS,
    KNOB_EMPTY_LABEL,
)

logger = logging.getLogger("glitchbooth.core.pipeline")


# WARM-UP REGISTER AND PACKET CORRUPTION STATE
# =========================================================================
WARMUP_FRAMES = 10
WARMUP_COUNTER = 0


# =========================================================================
# EFFECT ACTIVITY: acting(effect, raw_knob)
# =========================================================================
# One question, one answer: does this effect really change the picture at this knob value?
# raw_knob is 0-1023 after the effect's knob window. The handlers use these functions as their
# early return, the statistics use acting() (get_active_effects) and Posterize uses it to see
# whether ASCII is on. The numbers are the tunables in EFFECT ACTIVITY and each effect's own
# tunables, so there is no second copy that can drift.
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
    "scanline_ridge": _acts_scanline_ridge,
    "contour_ripple": _acts_contour_ripple,
    "chroma_bleed": _acts_chroma_bleed,
}


def acting(effect, raw_knob):
    # True when `effect` really changes the picture at this knob value (0-1023, after its
    # window). An effect with no entry in _ACTING (a startup warning says so) counts from knob 1.
    check = _ACTING.get(effect)
    return check(raw_knob) if check is not None else raw_knob > 0


# =========================================================================
# EFFECT HANDLERS, ATTACHMENTS, ORDER AND PER-EFFECT KNOB WINDOWS
# =========================================================================
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
    "scanline_ridge": apply_scanline_ridge,
    "contour_ripple": apply_contour_ripple,
    "chroma_bleed": apply_chroma_bleed,
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
    apply_moire_overlay(current_time)  # grid overlay, just before the CRT overlay
    apply_crt_overlay()
