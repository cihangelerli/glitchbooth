#!/usr/bin/env python3
"""
A/B Regression Guard (Phase 2): ab_regression_phase2.py

Question it answers: "Did Phase 2 change anything it was NOT supposed to change?"

How: it loads two versions of server.py side by side in ONE Python process
(same CPU, same NumPy/OpenCV, same everything):
    OLD = the version before the phase  (git tag, e.g. pre-phase2, or a file)
    NEW = the working-tree server.py    (imported normally)
Both run the full apply_glitch_pipeline() on identical frames, identical knob
settings and identical RNG seeds. Wherever the phase is supposed to be inert
(knob 2 dormant: Data Mosh and the old corruption both idle, every other knob
busy, Melt included), the outputs must be byte-identical. Because both versions
run on the same libraries, any difference can only come from the code change.

Run from the repo root (kiosk stopped):
    git tag pre-phase2 <commit-before-phase-2>      # once, on the commit BEFORE Phase 2
    python3 ab_regression_phase2.py --ref pre-phase2
or, without git:
    python3 ab_regression_phase2.py --old-file path/to/old_server.py

Checks
    1. DORMANT cases (knob 2 raw 0 and 20): OLD == NEW, byte for byte, for every frame
       of a multi-frame sequence, with all other knobs free to be busy (Melt included).
    2. BOUNDARY: raw 20 (0.0196 <= 0.02: old corruption does not spawn, Data Mosh idle)
       with the other knobs busy must match.
    3. NEGATIVE CONTROL: with knob 2 active, OLD and NEW must differ (old fused effect
       against Data Mosh v2). If they do not, the comparison cannot tell the versions
       apart and the PASS above means nothing.
    4. VACUITY: outputs must not be all black and must differ from their inputs in
       most cases (a black output makes every hash match; this is a real trap).
Exit code 0 = everything holds, 1 = something failed.
"""
import argparse
import hashlib
import importlib.util
import os
import random
import subprocess
import sys
import tempfile
import time

import cv2
import numpy as np

os.environ.setdefault("IK_URL_ENDPOINT", "https://ik.imagekit.io/mock")
os.environ.setdefault("IK_PRIVATE_KEY", "mock_key")
sys.path.insert(0, os.getcwd())

FOCUS_KNOB = 2  # Data Mosh (the old fused corruption + mosh knob)
BASE_TIME = 5000.0
DORMANT_RAW = (0, 20)  # n(20) = 0.0196 <= 0.02: nothing on knob 2 acts in OLD or NEW

# Module-level state that is randomised at IMPORT time (before any seed can be set).
# The two server modules would otherwise start with different random pools and the
# comparison would report differences that have nothing to do with the code change.
# Add a name with --sync NAME if the self-test below flags new unseeded state.
# (Phase 2 removed RANDOM_SIGN_POOL; Data Mosh now owns a private generator that
# seed_private_rngs() reseeds on every step, so nothing needs syncing today.)
SYNC_GLOBALS = []
RESET_TO_ZERO = []  # pointers into those pools


# --------------------------------------------------------------------------
# Loading the two versions
# --------------------------------------------------------------------------
def load_old_module(ref, old_file):
    if old_file:
        path = old_file
        label = f"file {old_file}"
    else:
        res = subprocess.run(["git", "show", f"{ref}:server.py"], capture_output=True)
        if res.returncode != 0:
            sys.exit(
                f"[A/B] Could not read server.py at git ref '{ref}':\n"
                f"      {res.stderr.decode(errors='ignore').strip()}\n"
                f"      Create it on the commit BEFORE the phase:  git tag {ref} <commit>\n"
                f"      (tags are local; on the Pi: git fetch --tags)"
            )
        tmp = tempfile.mkdtemp(prefix="ab_old_")
        path = os.path.join(tmp, "server_old.py")
        with open(path, "wb") as fh:
            fh.write(res.stdout)
        label = f"git ref {ref}"
    with open(path, "rb") as fh:
        old_bytes = fh.read()
    spec = importlib.util.spec_from_file_location("server_old", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["server_old"] = mod
    spec.loader.exec_module(mod)
    return mod, label, old_bytes


def sync_import_state(src, dst, extra):
    """Make dst start from the same import-time random state as src."""
    synced = []
    for name in SYNC_GLOBALS + list(extra):
        if hasattr(src, name) and hasattr(dst, name):
            getattr(dst, name)[...] = getattr(src, name)
            synced.append(name)
    for m in (src, dst):
        for name in RESET_TO_ZERO:
            if hasattr(m, name):
                setattr(m, name, 0)
    return synced


# --------------------------------------------------------------------------
# Deterministic input frames
# --------------------------------------------------------------------------
def base_frames(h, w, user_frame):
    rng = np.random.default_rng(99)
    small = rng.integers(0, 256, (h // 8, w // 8, 3), dtype=np.uint8)
    textured = cv2.GaussianBlur(cv2.resize(small, (w, h), interpolation=cv2.INTER_CUBIC), (0, 0), 5)
    portrait = np.zeros((h, w, 3), np.uint8)
    portrait[100:380, 200:440] = 180
    portrait[140:300, 260:380] = 230
    dark = rng.integers(0, 60, (h, w, 3), dtype=np.uint8)
    # Very bright frame with a few dark blocks: the only kind that gives Melt seeds at raw=31
    # (threshold there is ~249 of 255, so ordinary frames produce no drips at all)
    bright = np.full((h, w, 3), 255, np.uint8)
    bright[60:200, 100:300] = 40
    bright[300:420, 350:600] = 90
    frames = {"textured": textured, "portrait": portrait, "dark": dark, "bright": bright}
    if user_frame is not None:
        frames["userframe"] = user_frame
    return {k: np.ascontiguousarray(v) for k, v in frames.items()}


def frame_sequence(base, steps):
    """Slow diagonal motion, so temporal effects (Data Mosh, ghosting) have something to work on."""
    return [np.ascontiguousarray(np.roll(base, (i * 3, i * 5), axis=(0, 1))) for i in range(steps)]


# --------------------------------------------------------------------------
# Running one pipeline call on a module
# --------------------------------------------------------------------------
def run_step(mod, frame, knobs, t, seed):
    np.random.seed(seed)
    random.seed(seed)
    cv2.setRNGSeed(seed)  # cv2.randu (packet corruption noise) uses OpenCV's own RNG
    if hasattr(mod, "seed_private_rngs"):  # effects with their own generator (Data Mosh v2)
        mod.seed_private_rngs(seed)
    mod.MUTABLE_PROCESSING_BUFFER[:] = frame
    pkt = mod.ControlPacket(list(knobs), 0, "live")
    t0 = time.perf_counter()
    mod.apply_glitch_pipeline(t, pkt)
    dt = (time.perf_counter() - t0) * 1e3
    return mod.MUTABLE_PROCESSING_BUFFER.copy(), dt


def run_case(old, new, name, knobs, frames, seed0):
    """Run a multi-frame sequence on both modules. Returns a result dict."""
    out = {"name": name, "knobs": tuple(knobs), "identical": True, "first_diff": None,
           "old_ms": [], "new_ms": [], "vs_input_differs": 0, "all_black": False, "steps": len(frames)}
    for i, frame in enumerate(frames):
        t = BASE_TIME + i * 0.1
        seed = seed0 * 1000 + i
        a, ta = run_step(old, frame, knobs, t, seed)
        b, tb = run_step(new, frame, knobs, t, seed)
        out["old_ms"].append(ta)
        out["new_ms"].append(tb)
        if not np.any(b):
            out["all_black"] = True
        if not np.array_equal(b, frame):
            out["vs_input_differs"] += 1
        if out["identical"] and not np.array_equal(a, b):
            out["identical"] = False
            diff = np.any(a != b, axis=2)
            ys, xs = np.nonzero(diff)
            out["first_diff"] = (
                i, int(diff.sum()),
                (int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())),
                hashlib.md5(a.tobytes()).hexdigest()[:8], hashlib.md5(b.tobytes()).hexdigest()[:8],
            )
    return out


# --------------------------------------------------------------------------
# Case lists
# --------------------------------------------------------------------------
def dormant_cases(n_random, rng):
    cases = []
    for k in (0, 1, 3, 4, 5):  # every knob except the focus knob, alone
        for level in (512, 1023):
            knobs = [0] * 6
            knobs[k] = level
            cases.append((f"knob{k}={level}", knobs))
    for level in (400, 1023):
        cases.append((f"all-others={level}", [level if i != FOCUS_KNOB else 0 for i in range(6)]))
    for i in range(n_random):
        knobs = [int(v) for v in rng.integers(0, 1024, 6)]
        knobs[FOCUS_KNOB] = DORMANT_RAW[i % len(DORMANT_RAW)]
        cases.append((f"random#{i}", knobs))
    # boundary: still dormant, with the other knobs busy
    cases.append(("boundary raw=20", [300, 200, 20, 400, 600, 500]))
    return cases


def active_cases():
    """(name, knobs, input frame). Knob 2 acts in both versions, so OLD and NEW must differ."""
    return [
        ("knob2 only 0.59", [0, 0, 600, 0, 0, 0], "textured"),
        ("knob2 only 1.00", [0, 0, 1023, 0, 0, 0], "textured"),
        ("knob2 + others", [200, 300, 1023, 100, 400, 50], "textured"),
        ("knob2 on portrait", [0, 0, 800, 0, 0, 0], "portrait"),
    ]


# --------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="A/B regression guard for the effects pipeline (Phase 2)")
    ap.add_argument("--ref", default="pre-phase2", help="git ref holding the OLD server.py")
    ap.add_argument("--old-file", help="path to an OLD server.py (instead of a git ref)")
    ap.add_argument("--frames", type=int, default=6, help="frames per sequence")
    ap.add_argument("--random-cases", type=int, default=8)
    ap.add_argument("--seed", type=int, default=2024)
    ap.add_argument("--frame", help="optional raw camera frame to add as an extra input")
    ap.add_argument("--no-crt", action="store_true", help="skip CRT mask initialisation")
    ap.add_argument("--sync", action="append", default=[],
                    help="extra module-level array to copy from NEW into OLD (repeatable)")
    ap.add_argument("--skip-selftest", action="store_true", help="skip the A/A determinism self-test")
    args = ap.parse_args()

    print("=" * 74)
    print(" GLITCHBOOTH A/B REGRESSION GUARD (PHASE 2)")
    print("=" * 74)
    print(f" numpy {np.__version__}, opencv {cv2.__version__}, python {sys.version.split()[0]}")

    old, label, old_bytes = load_old_module(args.ref, args.old_file)
    import server as new  # noqa: E402

    new_bytes = open(new.__file__, "rb").read()
    print(f" OLD = {label}   ({len(old_bytes)} bytes)")
    print(f" NEW = {new.__file__}   ({len(new_bytes)} bytes)")
    problems = []
    if old_bytes == new_bytes:
        problems.append("OLD and NEW are the same file; there is nothing to compare")

    if not args.no_crt:
        for m in (old, new):
            if getattr(m, "CRT_ENABLED", False):
                m.initialize_crt_mask()
    for m in (old, new):
        m.LAST_TEMPORAL_PURGE = BASE_TIME - 1.0  # identical purge schedule in both

    synced = sync_import_state(new, old, args.sync)
    print(f" Synced import-time state: {', '.join(synced) if synced else 'none'}")

    if not args.skip_selftest:
        # A/A control: two fresh copies of NEW against each other. They must be identical,
        # otherwise the harness itself is non-deterministic and no A/B verdict can be trusted.
        # Fresh copies are used so that NEW and OLD themselves stay untouched (pristine state).
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
        sync_import_state(clones[0], clones[1], args.sync)
        probe = base_frames(new.FRAME_H, new.FRAME_W, None)["textured"]
        bad = []
        for pname, pk in (("knob0=1023", [1023, 0, 0, 0, 0, 0]), ("knob2=1023", [0, 0, 1023, 0, 0, 0]),
                          ("all=1023 (no melt)", [1023, 1023, 1023, 1023, 0, 1023])):
            r = run_case(clones[0], clones[1], f"A/A {pname}", pk, frame_sequence(probe, 3), 7)
            if not r["identical"]:
                bad.append(pname)
        for tag in ("server_clone_a", "server_clone_b"):
            del sys.modules[tag]
        del clones
        if bad:
            sys.exit(
                "[A/B] SELF-TEST FAILED: two identical copies of NEW differ on "
                + ", ".join(bad)
                + ".\n      Some module-level state is randomised at import. Find it "
                  "(grep for np.random / default_rng at module level), then pass --sync NAME.\n"
                  "      The A/B result would be meaningless until this passes."
            )
        print(" Self-test (A/A, two copies of NEW): identical")

    user_frame = None
    if args.frame:
        user_frame = cv2.imread(args.frame)
        if user_frame is None:
            sys.exit(f"[A/B] cannot read --frame {args.frame}")
        user_frame = cv2.resize(user_frame, (new.FRAME_W, new.FRAME_H))
    bases = base_frames(new.FRAME_H, new.FRAME_W, user_frame)
    rng = np.random.default_rng(args.seed)

    results_dormant, results_active = [], []
    case_no = 0

    print("\n--- KNOB 2 DORMANT: OLD must equal NEW (byte for byte) ---")
    for cname, knobs in dormant_cases(args.random_cases, rng):
        for fname, base in bases.items():
            case_no += 1
            res = run_case(old, new, f"{cname} / {fname}", knobs,
                           frame_sequence(base, args.frames), args.seed + case_no)
            results_dormant.append(res)
            mark = "same" if res["identical"] else "DIFF"
            extra = ""
            if not res["identical"]:
                i, n_px, bbox, ho, hn = res["first_diff"]
                extra = f"  first at step {i}: {n_px} px, bbox {bbox}, md5 {ho} vs {hn}"
            print(f" [{mark}] {res['name']:<34} knobs={res['knobs']}{extra}")

    print("\n--- KNOB 2 ACTIVE (negative control): OLD and NEW must differ ---")
    for cname, knobs, base_name in active_cases():
        case_no += 1
        res = run_case(old, new, f"{cname} / {base_name}", knobs,
                       frame_sequence(bases[base_name], args.frames), args.seed + case_no)
        results_active.append(res)
        print(f" [{'differ' if not res['identical'] else 'SAME!'}] {res['name']:<34} "
              f"old {np.mean(res['old_ms']):6.1f} ms  new {np.mean(res['new_ms']):6.1f} ms per frame")

    # ---------------- verdicts ----------------
    n_dormant_diff = sum(1 for r in results_dormant if not r["identical"])
    if n_dormant_diff:
        problems.append(f"{n_dormant_diff}/{len(results_dormant)} dormant cases differ between OLD and NEW "
                        f"(later ones may be knock-on effects of the first)")

    not_differing = [r["name"] for r in results_active if r["identical"]]
    if not_differing:
        problems.append("negative control failed (OLD == NEW with knob 2 active): " + ", ".join(not_differing))

    black = [r["name"] for r in results_dormant + results_active if r["all_black"]]
    if black:
        problems.append(f"{len(black)} cases produced an all-black output (comparison is vacuous), e.g. {black[0]}")

    changed = sum(1 for r in results_dormant if r["vs_input_differs"] > 0)
    if changed < 0.5 * len(results_dormant):
        problems.append(f"only {changed}/{len(results_dormant)} dormant cases altered their input; "
                        "the pipeline may not be doing anything")

    print("\n" + "=" * 74)
    print(f" Dormant cases compared : {len(results_dormant)}  (differing: {n_dormant_diff})")
    print(f" Cases that altered the input frame: {changed}/{len(results_dormant)}")
    print(f" Knob-2-active cases that differ   : "
          f"{sum(1 for r in results_active if not r['identical'])}/{len(results_active)}")
    if problems:
        print(" RESULT: FAIL")
        for p in problems:
            print(f"   - {p}")
        print("=" * 74)
        return 1
    print(" RESULT: PASS - the phase changed nothing outside its own effect")
    print("=" * 74)
    return 0


if __name__ == "__main__":
    sys.exit(main())
