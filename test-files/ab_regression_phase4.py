#!/usr/bin/env python3
"""
A/B regression guard for PHASE 4 (telemetry update): ab_regression_phase4.py

Question it answers: "Did the telemetry update leave the picture and the old statistics alone,
and does the new counting by effect name (hidden effects included) behave as specified?"

Run it from the repo root with the kiosk stopped:
    git tag pre-phase4 <commit>        # once, on the commit BEFORE Phase 4
    python3 ab_regression_phase4.py --ref pre-phase4
    python3 ab_regression_phase4.py --quick            # shorter run (use this on the Pi)

OLD = server.py and telemetry.py at the git tag, NEW = ./server.py and ./telemetry.py.

What it checks
  1. PIPELINE IDENTITY. Phase 4 does not touch the picture, so every case (all knobs, warm-up
     included) must be byte-for-byte identical between OLD and NEW.
  2. TELEMETRY (always in temporary copies of telemetry.py: your real telemetry/ folder,
     ledger and stats.json are never read or written by these checks):
       a. old ledger rows (knob numbers only) give the same counts, combos and stats.json as OLD
       b. new rows count the effect names, hidden effects included; nothing acting = no_glitch
       c. the old call record_capture(session_id, knobs) still works and counts the old way
       d. a restart on a mixed (old + new rows) ledger rebuilds exactly the live counts
       e. stats.json keeps its shape, adds effect_counts and picks up unknown future names
  3. SERVER SIDE: get_active_effects() and get_wiring_label() (thresholds, windows,
     re-attachment, unattached effects, name mapping, short knob lists) and the thresholds
     against the real dormancy limits of the effects.
  4. A/A SELF-TEST: two fresh copies of NEW must be identical.

IMPORTANT - READ A FAIL WITH CARE
  The baseline tag is a snapshot. If you tuned effect settings after tagging (strengths,
  thresholds, wiring, cell sizes, ...), OLD and NEW legitimately differ and this test can
  FAIL. That kind of FAIL is NOT important. Tag the tuned state you consider good
  (git tag -f pre-phaseN) before starting a phase and run it again when you need a strict
  answer. Exit code 0 = PASS, 1 = FAIL.
"""
import argparse
import hashlib
import importlib.util
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
import threading
import time

import cv2
import numpy as np

os.environ.setdefault("IK_URL_ENDPOINT", "https://ik.imagekit.io/mock")
os.environ.setdefault("IK_PRIVATE_KEY", "mock_key")
sys.path.insert(0, os.getcwd())

BASE_TIME = 5000.0
TUNING_NOTE = (
    "   NOTE: if you tuned effect settings after the baseline tag was made, a FAIL here is\n"
    "   expected and NOT important. Re-tag the tuned state and run again for a strict check."
)


# --------------------------------------------------------------------------
# Loading the two versions
# --------------------------------------------------------------------------
def git_show(ref, name):
    res = subprocess.run(["git", "show", f"{ref}:{name}"], capture_output=True)
    return res.stdout if res.returncode == 0 else None


def load_old_server(ref, old_file):
    if old_file:
        path, label = old_file, f"file {old_file}"
    else:
        data = git_show(ref, "server.py")
        if data is None:
            sys.exit(
                f"[A/B] Could not read server.py at git ref '{ref}'.\n"
                f"      Create it on the commit BEFORE the phase:  git tag {ref} <commit>\n"
                f"      (tags are local; on the Pi: git fetch --tags)"
            )
        tmp = tempfile.mkdtemp(prefix="ab_old_")
        path = os.path.join(tmp, "server_old.py")
        with open(path, "wb") as fh:
            fh.write(data)
        label = f"git ref {ref}"
    with open(path, "rb") as fh:
        old_bytes = fh.read()
    spec = importlib.util.spec_from_file_location("server_old", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["server_old"] = mod
    spec.loader.exec_module(mod)
    return mod, label, old_bytes


def legacy_config(old):
    """OLD's order, attachments and windows (Phase 3 or later files carry them)."""
    return (
        list(old.EFFECT_ORDER),
        {k: list(v) for k, v in old.KNOB_ATTACHMENTS.items()},
        dict(getattr(old, "EFFECT_KNOB_WINDOW", {})),
    )


# --------------------------------------------------------------------------
# Deterministic input frames and one pipeline step
# --------------------------------------------------------------------------
def base_frames(h, w):
    rng = np.random.default_rng(99)
    small = rng.integers(0, 256, (h // 8, w // 8, 3), dtype=np.uint8)
    textured = cv2.GaussianBlur(cv2.resize(small, (w, h), interpolation=cv2.INTER_CUBIC), (0, 0), 5)
    portrait = np.zeros((h, w, 3), np.uint8)
    portrait[100:380, 200:440] = 180
    portrait[140:300, 260:380] = 230
    dark = rng.integers(0, 60, (h, w, 3), dtype=np.uint8)
    bright = np.full((h, w, 3), 255, np.uint8)  # gives Melt seeds at low strength
    bright[60:200, 100:300] = 40
    bright[300:420, 350:600] = 90
    return {k: np.ascontiguousarray(v) for k, v in
            {"textured": textured, "portrait": portrait, "dark": dark, "bright": bright}.items()}


def frame_sequence(base, steps):
    return [np.ascontiguousarray(np.roll(base, (i * 3, i * 5), axis=(0, 1))) for i in range(steps)]


def run_step(mod, frame, knobs, t, seed):
    np.random.seed(seed)
    random.seed(seed)
    cv2.setRNGSeed(seed)
    if hasattr(mod, "seed_private_rngs"):
        mod.seed_private_rngs(seed)
    mod.MUTABLE_PROCESSING_BUFFER[:] = frame
    mod.apply_glitch_pipeline(t, mod.ControlPacket(list(knobs), 0, "live"))
    return mod.MUTABLE_PROCESSING_BUFFER.copy()


def run_case(old, new, name, knobs, frames, seed0):
    out = {"name": name, "knobs": tuple(knobs), "identical": True, "first_diff": None,
           "vs_input_differs": 0, "all_black": False}
    for i, frame in enumerate(frames):
        t = BASE_TIME + i * 0.1
        seed = seed0 * 1000 + i
        a = run_step(old, frame, knobs, t, seed)
        b = run_step(new, frame, knobs, t, seed)
        if not np.any(b):
            out["all_black"] = True
        if not np.array_equal(b, frame):
            out["vs_input_differs"] += 1
        if out["identical"] and not np.array_equal(a, b):
            out["identical"] = False
            diff = np.any(a != b, axis=2)
            ys, xs = np.nonzero(diff)
            out["first_diff"] = (i, int(diff.sum()), (int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())),
                                 hashlib.md5(a.tobytes()).hexdigest()[:8], hashlib.md5(b.tobytes()).hexdigest()[:8])
    return out


def identity_cases(n_random, rng):
    cases = [("warm-up probe (k2,k5 max)", [0, 0, 1023, 0, 0, 1023])]
    for k in range(6):
        for level in (512, 1023):
            knobs = [0] * 6
            knobs[k] = level
            cases.append((f"knob{k}={level}", knobs))
    for level in (400, 1023):
        cases.append((f"all={level}", [level] * 6))
    cases.append(("melt+mosh+ascii", [600, 0, 1023, 0, 1023, 0]))
    cases.append(("pixel+chroma+corruption", [0, 0, 0, 900, 0, 1023]))
    for i in range(n_random):
        cases.append((f"random#{i}", [int(v) for v in rng.integers(0, 1024, 6)]))
    return cases


# --------------------------------------------------------------------------
# Isolated telemetry copies
# --------------------------------------------------------------------------
def load_telemetry_copy(source_bytes, tag):
    """Import a private copy of telemetry.py from a temporary folder, so its telemetry/ directory
    (ledger and stats.json) lives in the temporary folder and never touches the real one."""
    tmp = tempfile.mkdtemp(prefix=f"tel_{tag}_")
    path = os.path.join(tmp, "telemetry.py")
    with open(path, "wb") as fh:
        fh.write(source_bytes)
    spec = importlib.util.spec_from_file_location(f"telemetry_{tag}", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[f"telemetry_{tag}"] = mod
    spec.loader.exec_module(mod)
    return mod, tmp


def write_ledger(mod, rows):
    with open(mod.CAPTURE_LOG_PATH, "w") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")


def start_fresh(source_bytes, tag, rows=None):
    mod, tmp = load_telemetry_copy(source_bytes, tag)
    if rows is not None:
        write_ledger(mod, rows)
    mod.init_telemetry(threading.Event())
    return mod, tmp


def drain(mod):
    """Run the ledger consumer until it has written everything queued so far."""
    th = threading.Thread(target=mod.telemetry_consumer_worker, daemon=True)
    th.start()
    mod.request_shutdown()
    th.join(timeout=10)
    return not th.is_alive()


def read_ledger(mod):
    with open(mod.CAPTURE_LOG_PATH) as fh:
        return [json.loads(line) for line in fh if line.strip()]


def stats(mod):
    mod.compile_summary_stats()
    with open(mod.STATS_JSON_PATH) as fh:
        return json.load(fh)


# --------------------------------------------------------------------------
def telemetry_checks(new, old_tel_bytes, new_tel_bytes):
    results = []

    def check(name, ok, detail=""):
        results.append((name, ok))
        print(f" [{'ok' if ok else 'FAIL'}] {name}" + (f" - {detail}" if detail else ""))

    rng = np.random.default_rng(321)
    legacy_rows = []
    for i in range(300):
        knobs = [int(v) for v in rng.integers(0, 1024, 6)]
        if i % 5 == 0:
            knobs = [int(rng.choice([0, 9, 10, 11])) for _ in range(6)]  # around the old threshold
        legacy_rows.append({"ts": 1790000000 + i * 37, "session_id": f"L{i}", "knobs": knobs})

    # ---- a. old rows: NEW must count exactly like OLD ----
    if old_tel_bytes is None:
        print(" (no telemetry.py at the baseline ref: check a. skipped)")
    else:
        t_old, d_old = start_fresh(old_tel_bytes, "old_a", legacy_rows)
        t_new, d_new = start_fresh(new_tel_bytes, "new_a", legacy_rows)
        s_old, s_new = stats(t_old), stats(t_new)
        same_counts = all(t_new.LIVE_STATS["effect_counts"].get(k, 0) == v for k, v in t_old.LIVE_STATS["effect_counts"].items())
        extra_zero = all(v == 0 for k, v in t_new.LIVE_STATS["effect_counts"].items() if k not in t_old.LIVE_STATS["effect_counts"])
        same_combo = t_old.LIVE_STATS["combo_tracker"] == t_new.LIVE_STATS["combo_tracker"]
        same_pop = all(s_new["effects_popularity"].get(k) == v for k, v in s_old["effects_popularity"].items())
        same_misc = all(s_old[k] == s_new[k] for k in ("total_captures", "first_capture_iso", "last_capture_iso", "most_popular_combo"))
        check("a. old ledger rows: same counts, combos and stats.json as OLD",
              same_counts and extra_zero and same_combo and same_pop and same_misc,
              f"{t_new.LIVE_STATS['total_captures']} rows")
        for d in (d_old, d_new):
            shutil.rmtree(d, ignore_errors=True)

    # ---- b/c/e. new rows ----
    t, d = start_fresh(new_tel_bytes, "new_b", legacy_rows)
    base_total = t.LIVE_STATS["total_captures"]
    t.record_capture("N1", [0, 0, 0, 0, 0, 1023], effects=["chroma_shift", "packet_corruption"], wiring="0:a|5:chroma_shift+packet_corruption")
    t.record_capture("N2", [0, 0, 0, 0, 0, 0], effects=[], wiring="x")
    t.record_capture("N3", [1, 2, 3, 4, 5, 6], effects=["melt", "melt", "data_mosh"])
    t.record_capture("N4", [0, 0, 0, 0, 0, 0], effects=["brand_new_effect"])
    t.record_capture("OLDSTYLE", [0, 0, 0, 500, 0, 0])  # old call without effects
    ec = t.LIVE_STATS["effect_counts"]
    check("b. new rows count effect names, hidden ones included",
          ec.get("packet_corruption") == 1 and ec.get("data_mosh") == 1 and ec.get("melt") == 1 + sum(1 for r in legacy_rows if r["knobs"][4] >= 10)
          and ec.get("brand_new_effect") == 1, "duplicate name in one capture counted once")
    check("b. nothing acting counts as no_glitch",
          ec.get("no_glitch") == sum(1 for r in legacy_rows if all(v < 10 for v in r["knobs"][:6])) + 1)
    check("c. old call record_capture(session_id, knobs) still counts the old way",
          ec.get("pixel_damage") == sum(1 for r in legacy_rows if r["knobs"][3] >= 10) + 1)
    drained = drain(t)
    rows = read_ledger(t)
    new_rows = [r for r in rows if r["session_id"] in ("N1", "N2", "N3", "N4", "OLDSTYLE")]
    r1 = next((r for r in new_rows if r["session_id"] == "N1"), {})
    r_old = next((r for r in new_rows if r["session_id"] == "OLDSTYLE"), {})
    check("b. ledger row has effects, wiring and version; old-style row stays old-style",
          drained and r1.get("effects") == ["chroma_shift", "packet_corruption"] and r1.get("v") == 2
          and r1.get("wiring", "").startswith("0:a") and r1.get("knobs") == [0, 0, 0, 0, 0, 1023]
          and "effects" not in r_old and "v" not in r_old)
    live_counts = dict(t.LIVE_STATS["effect_counts"])
    live_total = t.LIVE_STATS["total_captures"]
    live_combo = dict(t.LIVE_STATS["combo_tracker"])
    st = stats(t)
    check("e. stats.json keeps its shape and adds effect_counts and unknown names",
          all(k.endswith("_usage_pct") for k in st["effects_popularity"])
          and "brand_new_effect_usage_pct" in st["effects_popularity"] and "data_mosh_usage_pct" in st["effects_popularity"]
          and "packet_corruption_usage_pct" in st["effects_popularity"] and st["effect_counts"].get("brand_new_effect") == 1
          and abs(sum(st["effects_popularity"].values()) - 100.0) < 0.5 and st["total_captures"] == base_total + 5)
    # ---- d. restart on the mixed ledger ----
    shutil.copy(t.CAPTURE_LOG_PATH, os.path.join(d, "mixed_copy.jsonl"))
    t2, d2 = start_fresh(new_tel_bytes, "new_d", rows)
    check("d. restart on a mixed (old + new rows) ledger rebuilds the live counts exactly",
          {k: v for k, v in t2.LIVE_STATS["effect_counts"].items() if v or k in live_counts} == {k: v for k, v in live_counts.items() if v or k in t2.LIVE_STATS["effect_counts"]}
          and t2.LIVE_STATS["total_captures"] == live_total and t2.LIVE_STATS["combo_tracker"] == live_combo)
    for dd in (d, d2):
        shutil.rmtree(dd, ignore_errors=True)

    # ---- f. server side ----
    saved = (list(new.EFFECT_ORDER), {k: list(v) for k, v in new.KNOB_ATTACHMENTS.items()}, dict(new.EFFECT_KNOB_WINDOW))
    try:
        new.set_effect_config(order=saved[0], attachments=saved[1], windows={})
        gae, glab = new.get_active_effects, new.get_wiring_label
        check("server: nothing acting at zero, everything acting at maximum, hidden effect included",
              gae([0] * 6) == [] and set(gae([1023] * 6)) == {new.EFFECT_STATS_NAME.get(e, e) for e in new.EFFECT_ORDER if any(e in v for v in new.KNOB_ATTACHMENTS.values())})
        # thresholds against the real dormancy limits of the effects
        n_ = new.n
        lit = {"pixellation": 0.0, "signal_drift": 0.0, "chroma_shift": 0.05, "melt": 0.03, "ascii_matrix": 0.02}
        bad = []
        for eff, thr in new.EFFECT_STATS_ACTIVE_FROM.items():
            if eff == "data_mosh":
                ok = n_(thr) ** 2 >= new.MOSH_DORMANT_BELOW and n_(thr - 1) ** 2 < new.MOSH_DORMANT_BELOW
            elif eff == "packet_corruption":
                ok = n_(thr) > new.CORRUPTION_SPAWN_ABOVE and n_(thr - 1) <= new.CORRUPTION_SPAWN_ABOVE
            elif eff in lit:
                ok = n_(thr) > lit[eff] and n_(thr - 1) <= lit[eff]
            else:
                ok = True
            if not ok:
                bad.append(eff)
        check("server: EFFECT_STATS_ACTIVE_FROM matches each effect's own dormancy limit", not bad,
              ("mismatch: " + ", ".join(bad)) if bad else "")
        check("server: Pixellation is counted under its historical name pixel_damage",
              "pixel_damage" in gae([0, 0, 0, 500, 0, 0]) and "pixellation" not in gae([0, 0, 0, 500, 0, 0]))
        # windows
        new.set_effect_config(windows={"packet_corruption": (0.35, 1.0)})
        low, high = gae([0, 0, 0, 0, 0, 300]), gae([0, 0, 0, 0, 0, 1023])
        check("server: a knob window decides whether a hidden effect counts",
              "packet_corruption" not in low and "packet_corruption" in high and "w:packet_corruption=0.35-1" in glab())
        new.set_effect_config(windows={})
        # re-attachment and unattached
        att = {k: list(v) for k, v in saved[1].items()}
        for k in att:
            att[k] = [e for e in att[k] if e != "packet_corruption"]
        att[3].append("packet_corruption")
        new.set_effect_config(attachments=att)
        moved = gae([0, 0, 0, 500, 0, 0])
        check("server: an effect re-attached to another knob follows it",
              "packet_corruption" in moved and "packet_corruption" not in gae([0, 0, 0, 0, 0, 1023])
              and "3:pixel_damage+packet_corruption" in glab())
        att2 = {k: [e for e in v if e != "data_mosh"] for k, v in saved[1].items()}
        new.set_effect_config(attachments=att2)
        check("server: an unattached effect is never counted",
              "data_mosh" not in gae([1023] * 6) and "2:-" in glab())
        new.set_effect_config(order=saved[0], attachments=saved[1], windows=saved[2])
        check("server: wiring label lists primary first and is stable",
              glab() == glab() and glab().count("|") >= 5 and glab().split("|")[5].startswith("5:"))
        check("server: a short or odd knob list never raises", isinstance(gae([5, 5]), list) and isinstance(gae([]), list))
    except Exception as exc:
        check("server: helpers must not raise", False, repr(exc))
    finally:
        new.set_effect_config(order=saved[0], attachments=saved[1], windows=saved[2])

    # ---- g. the real call, as the storage worker makes it ----
    t3, d3 = start_fresh(new_tel_bytes, "new_g", [])
    knobs = [0, 0, 600, 0, 0, 1023]
    t3.record_capture(session_id="W1", knobs=knobs, effects=new.get_active_effects(knobs), wiring=new.get_wiring_label())
    drain(t3)
    row = read_ledger(t3)[-1]
    check("integration: the worker's call writes a complete ledger row",
          row["effects"] == new.get_active_effects(knobs) and row["wiring"] == new.get_wiring_label() and row["v"] == 2 and "data_mosh" in row["effects"])
    shutil.rmtree(d3, ignore_errors=True)
    return results


# --------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="A/B regression guard for Phase 4")
    ap.add_argument("--ref", default="pre-phase4", help="git ref holding the OLD server.py and telemetry.py")
    ap.add_argument("--old-file", help="path to an OLD server.py (instead of a git ref)")
    ap.add_argument("--old-telemetry", help="path to an OLD telemetry.py (with --old-file)")
    ap.add_argument("--frames", type=int, default=6)
    ap.add_argument("--random-cases", type=int, default=6)
    ap.add_argument("--seed", type=int, default=2024)
    ap.add_argument("--no-crt", action="store_true")
    ap.add_argument("--quick", action="store_true", help="3 frames, 3 random cases, 2 input frames")
    ap.add_argument("--skip-selftest", action="store_true")
    args = ap.parse_args()
    if args.quick:
        args.frames, args.random_cases = 3, 3

    print("=" * 74)
    print(" GLITCHBOOTH A/B REGRESSION GUARD - PHASE 4 (TELEMETRY)")
    print("=" * 74)
    print(f" numpy {np.__version__}, opencv {cv2.__version__}, python {sys.version.split()[0]}")
    old, label, old_bytes = load_old_server(args.ref, args.old_file)
    import server as new  # noqa: E402

    new_bytes = open(new.__file__, "rb").read()
    new_tel_bytes = open(os.path.join(os.path.dirname(new.__file__), "telemetry.py"), "rb").read()
    if args.old_telemetry:
        old_tel_bytes = open(args.old_telemetry, "rb").read()
    elif args.old_file:
        old_tel_bytes = None
    else:
        old_tel_bytes = git_show(args.ref, "telemetry.py")
    print(f" OLD = {label}   ({len(old_bytes)} bytes)")
    print(f" NEW = {new.__file__}   ({len(new_bytes)} bytes)")
    problems = []
    if old_bytes == new_bytes and old_tel_bytes == new_tel_bytes:
        problems.append("OLD and NEW are the same files; there is nothing to compare")

    if not args.no_crt:
        for m in (old, new):
            if getattr(m, "CRT_ENABLED", False):
                m.initialize_crt_mask()
    for m in (old, new):
        m.LAST_TEMPORAL_PURGE = BASE_TIME - 1.0

    if not args.skip_selftest:
        clones = []
        for tag in ("server_clone_a", "server_clone_b"):
            spec = importlib.util.spec_from_file_location(tag, new.__file__)
            c = importlib.util.module_from_spec(spec)
            sys.modules[tag] = c
            spec.loader.exec_module(c)
            if not args.no_crt and getattr(c, "CRT_ENABLED", False):
                c.initialize_crt_mask()
            c.LAST_TEMPORAL_PURGE = BASE_TIME - 1.0
            clones.append(c)
        probe = base_frames(new.FRAME_H, new.FRAME_W)["textured"]
        bad = [pn for pn, pk in (("knob0=1023", [1023, 0, 0, 0, 0, 0]), ("knob2+5=1023", [0, 0, 1023, 0, 0, 1023]), ("all=1023", [1023] * 6))
               if not run_case(clones[0], clones[1], f"A/A {pn}", pk, frame_sequence(probe, 3), 7)["identical"]]
        for tag in ("server_clone_a", "server_clone_b"):
            del sys.modules[tag]
        del clones
        if bad:
            sys.exit("[A/B] SELF-TEST FAILED: two identical copies of NEW differ on " + ", ".join(bad))
        print(" Self-test (A/A, two copies of NEW): identical")

    bases = base_frames(new.FRAME_H, new.FRAME_W)
    if args.quick:
        bases = {k: v for k, v in bases.items() if k in ("textured", "bright")}
    rng = np.random.default_rng(args.seed)
    order, att, win = legacy_config(old)
    new.set_effect_config(order=order, attachments=att, windows=win)

    print("\n--- 1. PIPELINE IDENTITY: Phase 4 must not change a single pixel ---")
    print(f"     order: {' > '.join(order)}")
    res, case_no = [], 0
    for cname, knobs in identity_cases(args.random_cases, rng):
        for fname, base in bases.items():
            case_no += 1
            n_frames = max(args.frames, 12) if cname.startswith("warm-up") else args.frames
            r = run_case(old, new, f"{cname} / {fname}", knobs, frame_sequence(base, n_frames), args.seed + case_no)
            res.append(r)
            mark = "same" if r["identical"] else "DIFF"
            extra = ""
            if not r["identical"]:
                i, n_px, bbox, ho, hn = r["first_diff"]
                extra = f"  first at step {i}: {n_px} px, bbox {bbox}, md5 {ho} vs {hn}"
            print(f" [{mark}] {r['name']:<40} knobs={r['knobs']}{extra}")
    n_diff = sum(1 for r in res if not r["identical"])
    if n_diff:
        problems.append(f"1. pipeline: {n_diff}/{len(res)} cases differ between OLD and NEW")
    black = [r["name"] for r in res if r["all_black"]]
    if black:
        problems.append(f"{len(black)} cases produced an all-black output (comparison is vacuous), e.g. {black[0]}")
    changed = sum(1 for r in res if r["vs_input_differs"] > 0)
    if changed < 0.5 * len(res):
        problems.append(f"only {changed}/{len(res)} cases altered their input; the pipeline may not be doing anything")

    print("\n--- 2./3. TELEMETRY AND SERVER-SIDE CHECKS (temporary copies; your telemetry/ folder is not touched) ---")
    tel = telemetry_checks(new, old_tel_bytes, new_tel_bytes)
    bad_tel = [n_ for n_, ok in tel if not ok]
    if bad_tel:
        problems.append("telemetry checks failed: " + ", ".join(bad_tel))
    new.set_effect_config(order=order, attachments=att, windows=win)

    print("\n" + "=" * 74)
    print(f" 1. pipeline cases compared : {len(res)}  (differing: {n_diff})   altered the input: {changed}/{len(res)}")
    print(f" 2./3. telemetry + server   : {sum(1 for _, ok in tel if ok)}/{len(tel)} ok")
    if problems:
        print(" RESULT: FAIL")
        for p in problems:
            print(f"   - {p}")
        print(TUNING_NOTE)
        print("=" * 74)
        return 1
    print(" RESULT: PASS - the picture is untouched; the new statistics behave as specified")
    print("=" * 74)
    return 0


if __name__ == "__main__":
    sys.exit(main())
