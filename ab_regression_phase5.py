#!/usr/bin/env python3
"""
A/B regression guard for PHASE 5 (Posterize): ab_regression_phase5.py

Question it answers: "Did adding Posterize to the pool leave the booth exactly as it was, and
does Posterize itself behave as specified when it is attached to a knob?"

Run it from the repo root with the kiosk stopped:
    git tag pre-phase5 <commit>        # once, on the commit BEFORE Phase 5
    python3 ab_regression_phase5.py --ref pre-phase5
    python3 ab_regression_phase5.py --quick            # shorter run (use this on the Pi)

OLD = server.py at the git tag, NEW = ./server.py.

What it checks
  1. PIPELINE IDENTITY. Posterize ships in the pool but attached to no knob, so every case
     (all knobs, warm-up included) must be byte-for-byte identical between OLD and NEW.
  2. POSTERIZE (NEW only):
       - the response curve (off up to knob 12, monotonic, 1.0 at the top)
       - dormant means untouched: knob values up to 12 leave the frame byte-identical
       - blacks stay exactly as they were, at every strength
       - same seed = same picture, different seed = different outlines
       - it really acts: a clear change of the picture, only a few tone bands at full strength
       - no frame-sized memory allocation per frame
       - attached to a knob it changes the picture, at knob 0 it does not; a knob window works
       - the governor counts its cost weight, the statistics count it from knob 13, the knob
         label shows "Posterize" when it is the primary effect
       - the three outline styles all run
       - telemetry has a fixed Posterize bar (checked on a temporary copy of telemetry.py)
  3. A/A SELF-TEST: two fresh copies of NEW must be identical.

IMPORTANT - READ A FAIL WITH CARE
  The baseline tag is a snapshot. If you tuned effect settings after tagging (strengths,
  wiring, order, constants, ...), OLD and NEW legitimately differ and this test can FAIL.
  That kind of FAIL is NOT important. Tag the tuned state you consider good
  (git tag -f pre-phaseN) before starting a phase and run it again when you need a strict
  answer. Exit code 0 = PASS, 1 = FAIL.
"""

import argparse
import hashlib
import importlib.util
import os
import random
import shutil
import subprocess
import sys
import tempfile
import threading
import tracemalloc

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


# --------------------------------------------------------------------------
# Deterministic input frames and one pipeline step
# --------------------------------------------------------------------------
def base_frames(h, w):
    rng = np.random.default_rng(99)
    small = rng.integers(0, 256, (h // 8, w // 8, 3), dtype=np.uint8)
    textured = cv2.GaussianBlur(
        cv2.resize(small, (w, h), interpolation=cv2.INTER_CUBIC), (0, 0), 5
    )
    portrait = np.zeros((h, w, 3), np.uint8)
    portrait[100:380, 200:440] = 180
    portrait[140:300, 260:380] = 230
    dark = rng.integers(0, 60, (h, w, 3), dtype=np.uint8)
    bright = np.full((h, w, 3), 255, np.uint8)
    bright[60:200, 100:300] = 40
    bright[300:420, 350:600] = 90
    # smooth shading with a black corner: many tone bands, and pixels that must stay black
    yy, xx = np.mgrid[0:h, 0:w]
    ramp = np.clip(xx * 255.0 / w * 0.7 + yy * 255.0 / h * 0.3, 0, 255).astype(np.uint8)
    shaded = np.stack(
        [
            ramp,
            np.clip(ramp * 0.8 + 20, 0, 255).astype(np.uint8),
            np.clip(255 - ramp, 0, 255).astype(np.uint8),
        ],
        -1,
    )
    shaded[:120, :200] = 0
    shaded[400:, 500:] = 5
    return {
        k: np.ascontiguousarray(v)
        for k, v in {
            "textured": textured,
            "portrait": portrait,
            "dark": dark,
            "bright": bright,
            "shaded": shaded,
        }.items()
    }


def frame_sequence(base, steps):
    return [
        np.ascontiguousarray(np.roll(base, (i * 3, i * 5), axis=(0, 1)))
        for i in range(steps)
    ]


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
    out = {
        "name": name,
        "knobs": tuple(knobs),
        "identical": True,
        "first_diff": None,
        "vs_input_differs": 0,
        "all_black": False,
    }
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
            out["first_diff"] = (
                i,
                int(diff.sum()),
                (int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())),
                hashlib.md5(a.tobytes()).hexdigest()[:8],
                hashlib.md5(b.tobytes()).hexdigest()[:8],
            )
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
# Posterize checks (NEW only)
# --------------------------------------------------------------------------
def posterize_checks(new, bases, new_tel_bytes):
    results = []

    def check(name, ok, detail=""):
        results.append((name, ok))
        print(
            f" [{'ok' if ok else 'FAIL'}] {name}" + (f" - {detail}" if detail else "")
        )

    new.WARMUP_COUNTER = new.WARMUP_FRAMES
    if getattr(new, "CRT_ENABLED", False):
        new.initialize_crt_mask()  # without the mask the CRT step would blank every picture
    P = new._POSTER
    saved = (
        list(new.EFFECT_ORDER),
        {k: list(v) for k, v in new.KNOB_ATTACHMENTS.items()},
        dict(new.EFFECT_KNOB_WINDOW),
    )
    gray_of = lambda f: cv2.cvtColor(f, cv2.COLOR_BGR2GRAY)

    def apply(frame, raw, seed=3, t=100.0):
        P.reseed(seed)
        new.MUTABLE_PROCESSING_BUFFER[:] = frame
        new.apply_posterize(raw, t)
        return new.MUTABLE_PROCESSING_BUFFER.copy()

    # curve
    s = P.strength
    grid = [s(r) for r in range(0, 1024)]
    check(
        "curve: off up to knob 12, on from 13, never decreasing, 1.0 at the top",
        s(0) == 0.0
        and s(12) == 0.0
        and s(13) > 0.0
        and all(b >= a for a, b in zip(grid, grid[1:]))
        and abs(s(1023) - 1.0) < 1e-9,
    )
    check(
        "curve: passes through its own points",
        all(abs(s(x) - y) < 1e-9 for x, y in new.POSTERIZE_CURVE),
    )

    # dormant = untouched
    ok = all(np.array_equal(apply(f, r), f) for f in bases.values() for r in (0, 5, 12))
    check("dormant (knob 0-12): the frame is byte-identical", ok)

    # blacks stay exactly as they were
    bad = []
    for name, f in bases.items():
        mask = gray_of(f) < new.POSTERIZE_BLACK_THRESHOLD
        for r in (13, 150, 400, 700, 1023):
            out = apply(f, r)
            if mask.any() and not np.array_equal(out[mask], f[mask]):
                bad.append(f"{name}@{r}")
    check(
        "blacks (grey below the threshold) stay byte-identical at every strength",
        not bad,
        ", ".join(bad[:3]),
    )

    # determinism
    f = bases["textured"]
    a, b = apply(f, 700, seed=5), apply(f, 700, seed=5)
    c = apply(f, 700, seed=6)
    check(
        "same seed gives the same picture, another seed another outline pattern",
        np.array_equal(a, b) and not np.array_equal(a, c),
    )

    # it acts, and few tone bands remain at full strength
    d150 = float(
        np.abs(
            apply(bases["shaded"], 150).astype(int) - bases["shaded"].astype(int)
        ).mean()
    )
    out = apply(bases["shaded"], 1023)
    nonblack = gray_of(bases["shaded"]) >= new.POSTERIZE_BLACK_RAMP
    levels = np.unique(out[nonblack][:, 1] // 64)  # coarse look at one channel
    check("it acts: clear change at knob 150", d150 > 15.0, f"mean change {d150:.1f}")
    check(
        "full strength on smooth shading gives a handful of flat tones, not a gradient",
        len(np.unique(out[nonblack].reshape(-1, 3), axis=0)) < 400,
        f"{len(np.unique(out[nonblack].reshape(-1, 3), axis=0))} distinct colours",
    )

    # no frame-sized allocation
    P.reseed(1)
    fr = bases["textured"]
    for _ in range(5):
        new.MUTABLE_PROCESSING_BUFFER[:] = fr
        new.apply_posterize(400, 100.0)
    worst = 0
    tracemalloc.start()
    for i in range(8):
        new.MUTABLE_PROCESSING_BUFFER[:] = fr
        tracemalloc.reset_peak()
        base = tracemalloc.get_traced_memory()[0]
        new.apply_posterize(700, 100.0 + i)
        worst = max(worst, tracemalloc.get_traced_memory()[1] - base)
    tracemalloc.stop()
    check(
        "no frame-sized allocation per frame",
        worst < 256 * 1024,
        f"worst extra allocation {worst / 1024:.0f} KiB (one frame = 900 KiB)",
    )

    # all three outline styles run and differ
    outs = {}
    saved_style = new.POSTERIZE_OUTLINE_STYLE
    try:
        for style in ("glow", "white", "black"):
            new.POSTERIZE_OUTLINE_STYLE = style
            outs[style] = apply(bases["textured"], 700, seed=2)
    finally:
        new.POSTERIZE_OUTLINE_STYLE = saved_style
    check(
        "the three outline styles all run and look different",
        not np.array_equal(outs["glow"], outs["white"])
        and not np.array_equal(outs["glow"], outs["black"]),
    )

    # attachment through the real pipeline
    try:
        att = {k: list(v) for k, v in saved[1].items()}
        base_att = {k: [e for e in v if e != "posterize"] for k, v in att.items()}
        with_att = {k: list(v) for k, v in base_att.items()}
        with_att[3].append("posterize")
        order = list(saved[0])
        if "posterize" not in order:
            order.insert(0, "posterize")
        probe = frame_sequence(bases["shaded"], 2)

        def pipe(attachments, knobs, windows=None):
            new.set_effect_config(
                order=order,
                attachments=attachments,
                windows=windows if windows is not None else {},
            )
            return [
                run_step(new, f, knobs, BASE_TIME + i * 0.1, 4242 + i)
                for i, f in enumerate(probe)
            ]

        off = pipe(base_att, [0, 0, 0, 700, 0, 0])
        on = pipe(with_att, [0, 0, 0, 700, 0, 0])
        zero = pipe(with_att, [0, 0, 0, 0, 0, 0])
        zero_off = pipe(base_att, [0, 0, 0, 0, 0, 0])
        check(
            "attached to a knob it changes the picture; at knob 0 it does not",
            not all(np.array_equal(x, y) for x, y in zip(on, off))
            and all(np.array_equal(x, y) for x, y in zip(zero, zero_off)),
        )
        win_low = pipe(
            with_att, [0, 0, 0, 300, 0, 0], windows={"posterize": (0.5, 1.0)}
        )
        win_off = pipe(base_att, [0, 0, 0, 300, 0, 0])
        win_hi = pipe(
            with_att, [0, 0, 0, 1023, 0, 0], windows={"posterize": (0.5, 1.0)}
        )
        hi_full = pipe(with_att, [0, 0, 0, 1023, 0, 0])
        check(
            "a knob window works (silent below it, full strength at its top)",
            all(np.array_equal(x, y) for x, y in zip(win_low, win_off))
            and all(np.array_equal(x, y) for x, y in zip(win_hi, hi_full)),
        )
        # governor, statistics, labels
        new.set_effect_config(order=order, attachments=base_att, windows={})
        g0 = new.compute_effect_intensity([0, 0, 0, 700, 0, 0])
        new.set_effect_config(order=order, attachments=with_att, windows={})
        g1 = new.compute_effect_intensity([0, 0, 0, 700, 0, 0])
        want = new.EFFECT_COST_WEIGHT["posterize"] * new.n(700)
        check(
            "governor counts Posterize's cost weight",
            abs((g1 - g0) - want) < 1e-9 and new.EFFECT_COST_WEIGHT["posterize"] > 0,
        )
        check(
            "statistics: counted from knob 13 as 'posterize', not at 12",
            "posterize" in new.get_active_effects([0, 0, 0, 13, 0, 0])
            and "posterize" not in new.get_active_effects([0, 0, 0, 12, 0, 0])
            and "posterize" in new.get_wiring_label(),
        )
        prim = {k: list(v) for k, v in base_att.items()}
        prim[5] = ["posterize"] + prim[5]
        new.set_effect_config(order=order, attachments=prim, windows={})
        check(
            "as the primary effect of a knob the label reads Posterize",
            new.get_knob_labels()[5] == new.EFFECT_LABELS["posterize"],
        )
    except Exception as exc:
        check("attachment, window, governor, statistics, label", False, repr(exc))
    finally:
        new.set_effect_config(order=saved[0], attachments=saved[1], windows=saved[2])

    # telemetry: fixed bar, on a temporary copy
    tmp = tempfile.mkdtemp(prefix="tel_p5_")
    try:
        path = os.path.join(tmp, "telemetry.py")
        with open(path, "wb") as fh:
            fh.write(new_tel_bytes)
        spec = importlib.util.spec_from_file_location("telemetry_p5", path)
        tel = importlib.util.module_from_spec(spec)
        sys.modules["telemetry_p5"] = tel
        spec.loader.exec_module(tel)
        tel.init_telemetry(threading.Event())
        tel.record_capture(
            "P5", [0, 0, 0, 500, 0, 0], effects=["posterize"], wiring="x"
        )
        ok = (
            "posterize" in tel.ALL_EFFECTS
            and tel.ALL_EFFECTS.index("posterize") < tel.ALL_EFFECTS.index("no_glitch")
            and tel.LIVE_STATS["effect_counts"].get("posterize") == 1
        )
        check("telemetry: fixed Posterize bar before no_glitch, and it is counted", ok)
    except Exception as exc:
        check("telemetry: fixed Posterize bar", False, repr(exc))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return results


# --------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="A/B regression guard for Phase 5")
    ap.add_argument(
        "--ref", default="pre-phase5", help="git ref holding the OLD server.py"
    )
    ap.add_argument(
        "--old-file", help="path to an OLD server.py (instead of a git ref)"
    )
    ap.add_argument("--frames", type=int, default=6)
    ap.add_argument("--random-cases", type=int, default=6)
    ap.add_argument("--seed", type=int, default=2024)
    ap.add_argument("--no-crt", action="store_true")
    ap.add_argument(
        "--quick", action="store_true", help="3 frames, 3 random cases, 2 input frames"
    )
    ap.add_argument("--skip-selftest", action="store_true")
    args = ap.parse_args()
    if args.quick:
        args.frames, args.random_cases = 3, 3

    print("=" * 74)
    print(" GLITCHBOOTH A/B REGRESSION GUARD - PHASE 5 (POSTERIZE)")
    print("=" * 74)
    print(
        f" numpy {np.__version__}, opencv {cv2.__version__}, python {sys.version.split()[0]}"
    )
    old, label, old_bytes = load_old_server(args.ref, args.old_file)
    import server as new  # noqa: E402

    new_bytes = open(new.__file__, "rb").read()
    new_tel_bytes = open(
        os.path.join(os.path.dirname(new.__file__), "telemetry.py"), "rb"
    ).read()
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
        bad = [
            pn
            for pn, pk in (
                ("knob0=1023", [1023, 0, 0, 0, 0, 0]),
                ("knob2+5=1023", [0, 0, 1023, 0, 0, 1023]),
                ("all=1023", [1023] * 6),
            )
            if not run_case(
                clones[0], clones[1], f"A/A {pn}", pk, frame_sequence(probe, 3), 7
            )["identical"]
        ]
        for tag in ("server_clone_a", "server_clone_b"):
            del sys.modules[tag]
        del clones
        if bad:
            sys.exit(
                "[A/B] SELF-TEST FAILED: two identical copies of NEW differ on "
                + ", ".join(bad)
            )
        print(" Self-test (A/A, two copies of NEW): identical")

    bases = base_frames(new.FRAME_H, new.FRAME_W)
    id_bases = {
        k: v
        for k, v in bases.items()
        if k
        in (
            ("textured", "bright")
            if args.quick
            else ("textured", "portrait", "dark", "bright")
        )
    }
    rng = np.random.default_rng(args.seed)

    print(
        "\n--- 1. PIPELINE IDENTITY: Posterize is in the pool but attached to no knob; nothing may change ---"
    )
    print(f"     order  : {' > '.join(new.EFFECT_ORDER)}")
    print(f"     wiring : {dict(new.KNOB_ATTACHMENTS)}")
    res, case_no = [], 0
    for cname, knobs in identity_cases(args.random_cases, rng):
        for fname, base in id_bases.items():
            case_no += 1
            n_frames = (
                max(args.frames, 12) if cname.startswith("warm-up") else args.frames
            )
            r = run_case(
                old,
                new,
                f"{cname} / {fname}",
                knobs,
                frame_sequence(base, n_frames),
                args.seed + case_no,
            )
            res.append(r)
            mark = "same" if r["identical"] else "DIFF"
            extra = ""
            if not r["identical"]:
                i, n_px, bbox, ho, hn = r["first_diff"]
                extra = f"  first at step {i}: {n_px} px, bbox {bbox}, md5 {ho} vs {hn}"
            print(f" [{mark}] {r['name']:<40} knobs={r['knobs']}{extra}")
    n_diff = sum(1 for r in res if not r["identical"])
    if n_diff:
        problems.append(
            f"1. pipeline: {n_diff}/{len(res)} cases differ between OLD and NEW"
        )
    black = [r["name"] for r in res if r["all_black"]]
    if black:
        problems.append(
            f"{len(black)} cases produced an all-black output (comparison is vacuous), e.g. {black[0]}"
        )
    changed = sum(1 for r in res if r["vs_input_differs"] > 0)
    if changed < 0.5 * len(res):
        problems.append(
            f"only {changed}/{len(res)} cases altered their input; the pipeline may not be doing anything"
        )

    print("\n--- 2. POSTERIZE CHECKS ---")
    pz = posterize_checks(new, bases, new_tel_bytes)
    bad_pz = [n_ for n_, ok in pz if not ok]
    if bad_pz:
        problems.append("posterize checks failed: " + ", ".join(bad_pz))

    print("\n" + "=" * 74)
    print(
        f" 1. pipeline cases compared : {len(res)}  (differing: {n_diff})   altered the input: {changed}/{len(res)}"
    )
    print(f" 2. posterize checks        : {sum(1 for _, ok in pz if ok)}/{len(pz)} ok")
    if problems:
        print(" RESULT: FAIL")
        for p in problems:
            print(f"   - {p}")
        print(TUNING_NOTE)
        print("=" * 74)
        return 1
    print(" RESULT: PASS - the booth is unchanged; Posterize behaves as specified")
    print("=" * 74)
    return 0


if __name__ == "__main__":
    sys.exit(main())
