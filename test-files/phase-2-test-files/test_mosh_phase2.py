#!/usr/bin/env python3
"""
Phase 2 tests: test_mosh_phase2.py   (Data Mosh v2 + standalone Packet Corruption)

Run from the repo root with the kiosk stopped:
    python3 test_mosh_phase2.py                     # OLD = git tag pre-phase2
    python3 test_mosh_phase2.py --old-file path/to/old_server.py
    python3 test_mosh_phase2.py --quick             # fewer frames (use this on the Pi)

What it proves
    T1  Independence: Data Mosh and Packet Corruption share no state, no random draws,
        and no code (functional test + a source-level cross-reference check).
    T2  Packet Corruption extraction is look-identical to the old inline stage
        (byte for byte, same seeds, coupling to Data Mosh switched off in OLD).
    T3  The optimised Data Mosh equals a plain, slow reference implementation
        (byte for byte; all bleed modes; busy / sparse / every-cell-moving scenes).
    T4  reset() leaves no residue.
    T5  The first frame after start / cancel is never modified.
    T6  Duplicate camera frames create no new holds.
    T7  Sweep of every knob range: no exception, right-most 16 px never touched.
    T8  No frame-sized allocation per frame.
    T9  Lowering the knob below the dormancy threshold cancels all moshing at once.
    T10 Wiring: Packet Corruption can be connected to ANY knob; the three Data Mosh
        positions agree when drift and melt are off; bad settings fall back safely.
Exit code 0 = every test passed (a SKIP is reported loudly and does not count as a pass).
"""
import argparse
import importlib.util
import inspect
import os
import random
import re
import subprocess
import sys
import tempfile
import tracemalloc

import cv2
import numpy as np

os.environ.setdefault("IK_URL_ENDPOINT", "https://ik.imagekit.io/mock")
os.environ.setdefault("IK_PRIVATE_KEY", "mock_key")
sys.path.insert(0, os.getcwd())

BASE_TIME = 5000.0
RESULTS = []  # (name, status, detail)


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def scene(kind, n, seed=3):
    """Synthetic frames (coloured shapes on a textured background; no people)."""
    rng = np.random.default_rng(seed)
    bg = cv2.GaussianBlur(rng.integers(0, 256, (480, 640, 3), np.uint8), (0, 0), 9)
    out = []
    for t in range(n):
        if kind == "chaos":  # every cell moves every frame
            out.append(rng.integers(0, 256, (480, 640, 3), np.uint8))
            continue
        f = bg.copy()
        if kind == "busy":
            x = 40 + (t * 11) % 480
            cv2.rectangle(f, (x, 120), (x + 110, 300), (30, 120, 255), -1)
            cv2.circle(f, (600 - (t * 9) % 560, 80 + (t * 5) % 330), 55, (255, 200, 0), -1)
        else:  # sparse
            cv2.circle(f, (200 + (t * 6) % 260, 240), 60, (30, 120, 255), -1)
        out.append(f)
    return out


def load_old(ref, old_file):
    if old_file:
        path = old_file
    else:
        res = subprocess.run(["git", "show", f"{ref}:server.py"], capture_output=True)
        if res.returncode != 0:
            return None, f"cannot read server.py at git ref '{ref}'"
        tmp = tempfile.mkdtemp(prefix="p2_old_")
        path = os.path.join(tmp, "server_old.py")
        with open(path, "wb") as fh:
            fh.write(res.stdout)
    spec = importlib.util.spec_from_file_location("server_old", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["server_old"] = mod
    spec.loader.exec_module(mod)
    return mod, path


class Ctx:
    """Temporarily change server tunables; always restored."""

    def __init__(self, mod, **kv):
        self.mod, self.kv, self.saved = mod, kv, {}

    def __enter__(self):
        for k, v in self.kv.items():
            self.saved[k] = getattr(self.mod, k)
            setattr(self.mod, k, v)
        return self

    def __exit__(self, *a):
        for k, v in self.saved.items():
            setattr(self.mod, k, v)


def record(name, ok, detail="", skip=False):
    status = "SKIP" if skip else ("PASS" if ok else "FAIL")
    RESULTS.append((name, status, detail))
    print(f" [{status}] {name}" + (f" - {detail}" if detail else ""))


# --------------------------------------------------------------------------
# reference implementation (plain, slow, full-frame, allocation-heavy)
# --------------------------------------------------------------------------
class ReferenceMosh:
    def __init__(self, S, seed):
        self.S = S
        H, W = S.FRAME_H, S.FRAME_W
        self.gh, self.gw = S.MOSH_GRID_H, S.MOSH_GRID_W
        self.rng = np.random.default_rng(seed)
        self.hold = np.zeros((H, W, 3), np.uint8)  # previous picture, except inside held cells
        self.timer = np.zeros((self.gh, self.gw), np.int16)
        self.vx = np.zeros((self.gh, self.gw), np.int8)
        self.prev_samp, self.have_prev = None, False
        yy, xx = np.mgrid[0:H, 0:W]
        iy, ix = yy % S.MOSH_CELL_H, xx % S.MOSH_CELL_W
        self.edge = np.minimum(
            np.minimum(iy, S.MOSH_CELL_H - 1 - iy), np.minimum(ix, S.MOSH_CELL_W - 1 - ix)
        ).astype(np.uint8)

    def up(self, c):
        S = self.S
        out = np.zeros((S.FRAME_H, S.FRAME_W), c.dtype)
        out[:, : self.gw * S.MOSH_CELL_W] = np.repeat(np.repeat(c, S.MOSH_CELL_H, 0), S.MOSH_CELL_W, 1)
        return out

    def step(self, frame, strength):
        S = self.S
        H, W, vec = S.FRAME_H, S.FRAME_W, S.MOSH_VECTOR_PX
        gh, gw = self.gh, self.gw
        samp = frame[S.MOSH_CELL_H // 2 :: S.MOSH_CELL_H, S.MOSH_CELL_W // 2 :: S.MOSH_CELL_W][:gh, :gw].astype(np.int16)
        out = frame.copy()
        if not self.have_prev:
            self.hold, self.prev_samp, self.have_prev = frame.copy(), samp.copy(), True
            return out
        thr = int(S.MOSH_THRESH_BASE + (1.0 - strength) * S.MOSH_THRESH_SPAN)
        w3 = np.array([0.114, 0.587, 0.299], np.float32)
        if np.array_equal(samp, self.prev_samp):
            motion = np.zeros((gh, gw), bool)
        else:
            motion = (np.abs(samp - self.prev_samp) * w3).sum(-1) > thr
        hold_n = int(round(S.MOSH_HOLD_MIN_FRAMES + (S.MOSH_HOLD_MAX_FRAMES - S.MOSH_HOLD_MIN_FRAMES) * strength))
        new_cells = motion & (self.timer == 0)
        cur = frame.copy()
        if new_cells.any():
            self.vx[new_cells] = np.where(self.rng.random(int(new_cells.sum())) > 0.5, vec, -vec)
        self.timer[motion] = hold_n
        active = self.timer > 0
        if active.any():
            act = self.up(active.astype(np.uint8)) > 0
            neg = self.up((self.vx < 0).astype(np.uint8)) > 0
            xs = np.arange(W)
            right = self.hold[:, np.minimum(xs + vec, W - 1)]
            left = self.hold[:, np.maximum(xs - vec, 0)]
            shifted = np.where(neg[..., None], left, right)
            blend = cv2.addWeighted(shifted, S.MOSH_DRAG_NEW_WEIGHT, self.hold, 1.0 - S.MOSH_DRAG_NEW_WEIGHT, 0)
            self.hold[act] = blend[act]
            inset = self.rng.integers(0, S.MOSH_EDGE_ERODE_MAX_PX + 1, (gh, gw), dtype=np.int16) + (
                np.clip(S.MOSH_RELEASE_FRAMES - self.timer, 0, S.MOSH_RELEASE_FRAMES) * S.MOSH_RELEASE_ERODE_PX
            )
            inset = np.clip(inset, 0, 255).astype(np.uint8)
            mask = (self.edge >= self.up(inset)) & act
            if S.MOSH_BLEED_MODE == "off":
                out[mask] = self.hold[mask]
            else:
                bleed = S.MOSH_BLEED_MAX * strength
                yl = cv2.cvtColor(cur, cv2.COLOR_BGR2GRAY)
                yh = cv2.cvtColor(self.hold, cv2.COLOR_BGR2GRAY)
                if S.MOSH_BLEED_MODE == "shape_live_colour_held":
                    a, b, base = yl, yh, self.hold
                else:
                    a, b, base = yh, yl, cur
                pos = cv2.convertScaleAbs(cv2.subtract(a, b), alpha=bleed)
                neg_ = cv2.convertScaleAbs(cv2.subtract(b, a), alpha=bleed)
                comp = cv2.subtract(cv2.add(base, cv2.merge([pos] * 3)), cv2.merge([neg_] * 3))
                out[mask] = comp[mask]
        self.timer[self.timer > 0] -= 1
        self.vx[self.timer == 0] = 0
        keep = self.up((self.timer > 0).astype(np.uint8)) > 0
        self.hold[~keep] = cur[~keep]  # memory follows this frame outside the cells still held
        self.prev_samp = samp.copy()
        return out


# --------------------------------------------------------------------------
# tests
# --------------------------------------------------------------------------
def t1_independence(S, quick):
    n_frames = 25 if quick else 50
    frames = scene("busy", n_frames)
    dummy_src = scene("chaos", n_frames, seed=9)
    ok, why = True, ""

    # (a) Data Mosh identical with and without Packet Corruption running in between
    a, b = S.DataMoshEngine(seed=11), S.DataMoshEngine(seed=11)
    for i, f in enumerate(frames):
        fa, fb = f.copy(), f.copy()
        a.step(fa, 1.0)
        np.random.seed(i)
        cv2.setRNGSeed(i)
        dummy = dummy_src[i].copy()
        S.corruption_update(1.0)
        S.corruption_render(dummy)
        b.step(fb, 1.0)
        same = (
            np.array_equal(fa, fb)
            and np.array_equal(a.timer, b.timer)
            and np.array_equal(a.vx, b.vx)
            and np.array_equal(a.hold, b.hold)
        )
        if not same:
            ok, why = False, f"Data Mosh changed when corruption ran (frame {i})"
            break
    S.corruption_reset()

    # (b) Packet Corruption identical with and without Data Mosh running in between
    def run_corruption(with_mosh):
        S.corruption_reset()
        eng = S.DataMoshEngine(seed=4)
        outs = []
        for i in range(n_frames):
            np.random.seed(100 + i)
            cv2.setRNGSeed(100 + i)
            if with_mosh:
                eng.step(frames[i].copy(), 1.0)
            fr = dummy_src[i].copy()
            S.corruption_update(0.9)
            S.corruption_render(fr)
            outs.append((fr, S.CORRUPTION_ENERGY.copy(), S.CORRUPTION_MODE.copy()))
        return outs

    if ok:
        r1, r2 = run_corruption(False), run_corruption(True)
        for i, (x, y) in enumerate(zip(r1, r2)):
            if not all(np.array_equal(p, q) for p, q in zip(x, y)):
                ok, why = False, f"Packet Corruption changed when Data Mosh ran (frame {i})"
                break
    S.corruption_reset()

    # (c) Data Mosh never touches the global random streams
    if ok:
        np.random.seed(5)
        before = np.random.get_state()[1].copy()
        cv2.setRNGSeed(5)
        probe_a = np.zeros((4, 4, 3), np.uint8)
        cv2.randu(probe_a, (0, 0, 0), (255, 255, 255))
        eng = S.DataMoshEngine(seed=1)
        np.random.seed(5)
        cv2.setRNGSeed(5)
        for f in frames[:15]:
            eng.step(f.copy(), 1.0)
        after = np.random.get_state()[1]
        probe_b = np.zeros((4, 4, 3), np.uint8)
        cv2.randu(probe_b, (0, 0, 0), (255, 255, 255))
        if not np.array_equal(before, after):
            ok, why = False, "Data Mosh consumed numpy global random numbers"
        elif not np.array_equal(probe_a, probe_b):
            ok, why = False, "Data Mosh consumed OpenCV random numbers"

    # (d) source-level cross-reference check
    if ok:
        mosh_src = "\n".join(
            inspect.getsource(o)
            for o in (S.DataMoshEngine, S.apply_data_mosh, S.data_mosh_reset, S.data_mosh_take_samples)
        )
        corr_src = "\n".join(
            inspect.getsource(o)
            for o in (S.corruption_update, S.corruption_render, S.corruption_reset, S.apply_packet_corruption)
        )
        if re.search(r"corruption|packet", mosh_src, re.I):
            ok, why = False, "Data Mosh source mentions corruption"
        elif re.search(r"mosh", corr_src, re.I):
            ok, why = False, "Packet Corruption source mentions Data Mosh"
    record("T1 independence (state, random draws, source)", ok, why)


def t2_corruption_identity(S, old, quick):
    if old is None:
        record("T2 corruption identical to old inline stage", False, "no OLD server available (use --ref/--old-file)", skip=True)
        return
    steps = 12 if quick else 25
    ok, why, seen_cells = True, "", 0
    real_old_mosh = old.apply_datamosh
    old.apply_datamosh = lambda *a, **k: None  # switch the old Data Mosh -> corruption coupling off
    try:
        with Ctx(S, DATAMOSH_KNOB=None, PACKET_CORRUPTION_KNOB=2):
            for mod in (old, S):
                mod.WARMUP_COUNTER = mod.WARMUP_FRAMES
                mod.LAST_TEMPORAL_PURGE = BASE_TIME - 1.0
            bases = {"textured": scene("busy", steps, seed=21), "portrait": scene("sparse", steps, seed=22)}
            for raw in (150, 400, 700, 1023):
                for bname, frames in bases.items():
                    for mod in (old, S):
                        mod.CORRUPTION_ENERGY[:] = 0
                        mod.CORRUPTION_MODE[:] = 0
                    for i, fr in enumerate(frames):
                        outs = []
                        for mod in (old, S):
                            np.random.seed(900 + i)
                            random.seed(900 + i)
                            cv2.setRNGSeed(900 + i)
                            if hasattr(mod, "seed_private_rngs"):
                                mod.seed_private_rngs(900 + i)
                            mod.MUTABLE_PROCESSING_BUFFER[:] = fr
                            mod.apply_glitch_pipeline(BASE_TIME + i * 0.1, mod.ControlPacket([0, 0, raw, 0, 0, 0], 0, "live"))
                            outs.append(mod.MUTABLE_PROCESSING_BUFFER.copy())
                        seen_cells += int(np.count_nonzero(S.CORRUPTION_MODE))
                        if not (
                            np.array_equal(outs[0], outs[1])
                            and np.array_equal(old.CORRUPTION_ENERGY, S.CORRUPTION_ENERGY)
                            and np.array_equal(old.CORRUPTION_MODE, S.CORRUPTION_MODE)
                        ):
                            ok, why = False, f"differs at knob {raw}, {bname}, step {i}"
                            break
                    if not ok:
                        break
                if not ok:
                    break
    finally:
        old.apply_datamosh = real_old_mosh
        S.corruption_reset()
    if ok and seen_cells == 0:
        ok, why = False, "vacuous: corruption never produced any damage"
    record("T2 corruption identical to old inline stage", ok, why or f"{seen_cells} damaged-cell observations compared")


def t3_mosh_identity(S, quick):
    n_frames = 25 if quick else 60
    seeds = (1, 2) if quick else (1, 2, 3)
    ok, why, total = True, "", 0
    scenes = {"busy": scene("busy", n_frames), "sparse": scene("sparse", n_frames), "chaos": scene("chaos", n_frames, seed=5)}
    for mode in ("shape_live_colour_held", "shape_held_colour_live", "off"):
        with Ctx(S, MOSH_BLEED_MODE=mode):
            for sname, frames in scenes.items():
                for knob in (0.2, 0.5, 0.8, 1.0):
                    strength = knob * knob
                    for seed in seeds:
                        eng, ref = S.DataMoshEngine(seed), ReferenceMosh(S, seed)
                        for i, f in enumerate(frames):
                            got = f.copy()
                            eng.step(got, strength)
                            want = ref.step(f, strength)
                            total += 1
                            if not np.array_equal(got, want):
                                ok, why = False, f"mode={mode} scene={sname} knob={knob} seed={seed} frame={i}"
                                break
                        if not ok:
                            break
                    if not ok:
                        break
                if not ok:
                    break
        if not ok:
            break
    record("T3 optimised Data Mosh == plain reference", ok, why or f"{total} frames compared")


def t4_reset(S):
    frames = scene("busy", 40)
    tail = scene("busy", 25, seed=8)
    e = S.DataMoshEngine(1)
    for f in frames:
        e.step(f.copy(), 1.0)
    held_before = int((e.timer > 0).sum())
    e.reset()
    e.reseed(5)
    f_new = S.DataMoshEngine(5)
    ok, why = True, ""
    if e.timer.any() or e.vx.any() or e.have_prev:
        ok, why = False, "reset left timers/vectors/remembered picture"
    for i, f in enumerate(tail):
        a, b = f.copy(), f.copy()
        e.step(a, 1.0)
        f_new.step(b, 1.0)
        if ok and not np.array_equal(a, b):
            ok, why = False, f"residue visible at frame {i}"
            break
    record("T4 reset leaves no residue", ok and held_before > 0, why or f"{held_before} holds existed before reset")


def t5_first_frame(S):
    f = scene("busy", 1)[0]
    e = S.DataMoshEngine(1)
    out = f.copy()
    e.step(out, 1.0)
    ok = np.array_equal(out, f)
    e.step(scene("busy", 2)[1].copy(), 1.0)
    e.reset()
    out2 = f.copy()
    e.step(out2, 1.0)
    ok = ok and np.array_equal(out2, f)
    record("T5 first frame after start / reset is untouched", ok)


def t6_duplicates(S):
    frames = scene("busy", 30)
    e = S.DataMoshEngine(2)
    ok, why, dup_checked = True, "", 0
    for i, f in enumerate(frames):
        e.step(f.copy(), 1.0)
        before = e.timer.copy()
        e.step(f.copy(), 1.0)  # the same camera frame arrives again
        dup_checked += 1
        if np.any(e.timer > before):
            ok, why = False, f"duplicate of frame {i} created or renewed a hold"
            break
    record("T6 duplicate camera frames create no holds", ok, why or f"{dup_checked} duplicates checked")


def t7_sweep(S, quick):
    ok, why, steps = True, "", 0
    raws = sorted(set(list(range(0, 1024, 37 if quick else 13)) + [0, 1, 20, 21, 144, 145, 1022, 1023]))
    chaos = scene("chaos", 6, seed=13)
    right = slice(S.MOSH_GRID_PX_W, S.FRAME_W)
    try:
        for raw in raws:
            S.data_mosh_reset()
            S.corruption_reset()
            for i, fr in enumerate(chaos):
                for effect in ("mosh", "corruption"):
                    S.MUTABLE_PROCESSING_BUFFER[:] = fr
                    if effect == "mosh":
                        S.apply_data_mosh(raw, BASE_TIME + i)
                    else:
                        S.apply_packet_corruption(raw, BASE_TIME + i)
                    steps += 1
                    if not np.array_equal(S.MUTABLE_PROCESSING_BUFFER[:, right], fr[:, right]):
                        ok, why = False, f"{effect}: right-most columns modified (knob {raw})"
                        break
                if not ok:
                    break
            if not ok:
                break
    except Exception as exc:  # any exception would restart the service in a loop
        ok, why = False, f"exception {type(exc).__name__}: {exc}"
    finally:
        S.data_mosh_reset()
        S.corruption_reset()
    record("T7 knob sweep: no exception, right edge untouched", ok, why or f"{len(raws)} knob values, {steps} calls")


def t8_allocation(S):
    frames = scene("busy", 50)
    e = S.DataMoshEngine(1)
    for f in frames[:30]:
        e.step(f.copy(), 1.0)
    worst = 0
    tracemalloc.start()
    for f in frames[30:]:
        buf = f.copy()  # the test's own frame copy is not counted
        tracemalloc.reset_peak()
        base = tracemalloc.get_traced_memory()[0]
        e.step(buf, 1.0)
        worst = max(worst, tracemalloc.get_traced_memory()[1] - base)
    tracemalloc.stop()
    limit = 256 * 1024  # a single frame-sized buffer is 900 KiB
    record("T8 no frame-sized allocation per frame", worst < limit, f"worst extra allocation {worst / 1024:.0f} KiB (limit {limit // 1024} KiB; one frame = 900 KiB)")


def t9_cancel(S):
    ok, why = True, ""
    moving = scene("busy", 40)
    S.data_mosh_reset()
    for i, f in enumerate(moving):
        S.MUTABLE_PROCESSING_BUFFER[:] = f
        S.apply_data_mosh(1023, BASE_TIME + i)
    held = int(S._MOSH.timer.any())
    # boundary of the dormancy threshold (strength = (raw/1023)**2, active from 0.02)
    b_lo, b_hi = (144 / 1023.0) ** 2 < S.MOSH_DORMANT_BELOW, (145 / 1023.0) ** 2 >= S.MOSH_DORMANT_BELOW
    S.MUTABLE_PROCESSING_BUFFER[:] = moving[-1]
    S.apply_data_mosh(100, BASE_TIME + 99)  # knob lowered
    if S._MOSH.timer.any() or S._MOSH.vx.any() or S._MOSH.have_prev:
        ok, why = False, "holds survived lowering the knob"
    if not np.array_equal(S.MUTABLE_PROCESSING_BUFFER, moving[-1]):
        ok, why = False, "frame modified while dormant"
    # raising it again starts clean: untouched first frame, no instant holds
    S.MUTABLE_PROCESSING_BUFFER[:] = moving[0]
    S.apply_data_mosh(1023, BASE_TIME + 100)
    if not np.array_equal(S.MUTABLE_PROCESSING_BUFFER, moving[0]) or S._MOSH.timer.any():
        ok, why = False, "not clean after raising the knob again"
    # same through the real pipeline
    with Ctx(S, DATAMOSH_KNOB=2, PACKET_CORRUPTION_KNOB=None):
        S.WARMUP_COUNTER = S.WARMUP_FRAMES
        S.LAST_TEMPORAL_PURGE = BASE_TIME - 1.0
        for i, f in enumerate(moving[:20]):
            S.MUTABLE_PROCESSING_BUFFER[:] = f
            S.apply_glitch_pipeline(BASE_TIME + i * 0.07, S.ControlPacket([0, 0, 1023, 0, 0, 0], 0, "live"))
        active_in_pipeline = bool(S._MOSH.timer.any())
        S.MUTABLE_PROCESSING_BUFFER[:] = moving[20]
        S.apply_glitch_pipeline(BASE_TIME + 5, S.ControlPacket([0, 0, 0, 0, 0, 0], 0, "live"))
        if S._MOSH.timer.any():
            ok, why = False, "pipeline: holds survived knob 2 -> 0"
    S.data_mosh_reset()
    record(
        "T9 lowering the knob cancels all moshing at once",
        ok and held and b_lo and b_hi and active_in_pipeline,
        why or "raw 144 dormant, raw 145 active; cancel is immediate and clean",
    )


def t10_wiring(S, quick):
    ok, why = True, ""
    frames = scene("busy", 20 if quick else 30)

    def run(knobs, n=None, **tun):
        with Ctx(S, **tun):
            S.WARMUP_COUNTER = S.WARMUP_FRAMES
            S.LAST_TEMPORAL_PURGE = BASE_TIME - 1.0
            S.data_mosh_reset()
            S.corruption_reset()
            held = damaged = 0
            outs = []
            for i, f in enumerate(frames[:n]):
                S.seed_private_rngs(7)
                np.random.seed(i)
                cv2.setRNGSeed(i)
                S.MUTABLE_PROCESSING_BUFFER[:] = f
                S.apply_glitch_pipeline(BASE_TIME + i * 0.07, S.ControlPacket(list(knobs), 0, "live"))
                held += int(S._MOSH.timer.any())
                damaged += int(np.count_nonzero(S.CORRUPTION_MODE))
                outs.append(S.MUTABLE_PROCESSING_BUFFER.copy())
            S.data_mosh_reset()
            S.corruption_reset()
            return held, damaged, outs

    # default wiring: knob 2 = Data Mosh, corruption silent
    held, damaged, _ = run([0, 0, 1023, 0, 0, 0])
    if not (held > 0 and damaged == 0):
        ok, why = False, f"default wiring wrong (held={held}, damage={damaged})"
    # corruption connected to knob 2 (Data Mosh unmapped), then to another knob
    if ok:
        held, damaged, _ = run([0, 0, 1023, 0, 0, 0], PACKET_CORRUPTION_KNOB=2, DATAMOSH_KNOB=None)
        if not (held == 0 and damaged > 0):
            ok, why = False, f"corruption on knob 2 wrong (held={held}, damage={damaged})"
    if ok:
        held, damaged, _ = run([0, 0, 0, 1023, 0, 0], PACKET_CORRUPTION_KNOB=3, DATAMOSH_KNOB=None)
        if not damaged > 0:
            ok, why = False, "corruption did not respond on knob 3"
    if ok:  # both on the same knob: both act, independently
        held, damaged, _ = run([0, 0, 1023, 0, 0, 0], PACKET_CORRUPTION_KNOB=2)
        if not (held > 0 and damaged > 0):
            ok, why = False, f"shared knob: expected both to act (held={held}, damage={damaged})"
    # three positions agree when drift and melt are off
    if ok:
        base = run([0, 0, 1023, 0, 0, 0], MOSH_STAGE_POSITION="before_drift")[2]
        for pos in ("after_melt_own_motion", "after_melt_pre_melt_motion"):
            other = run([0, 0, 1023, 0, 0, 0], MOSH_STAGE_POSITION=pos)[2]
            if not all(np.array_equal(a, b) for a, b in zip(base, other)):
                ok, why = False, f"position {pos} differs from before_drift with drift and melt off"
                break
    # positions all do something with melt on, without errors
    if ok:
        for pos in ("before_drift", "after_melt_own_motion", "after_melt_pre_melt_motion"):
            held, _, outs = run([0, 0, 1023, 0, 1023, 0], n=15, MOSH_STAGE_POSITION=pos)
            if held == 0:
                ok, why = False, f"position {pos}: no holds with melt on"
                break
    # invalid settings fall back safely and never raise
    if ok:
        saved = (S.DATAMOSH_KNOB, S.PACKET_CORRUPTION_KNOB, S.MOSH_STAGE_POSITION, S.MOSH_BLEED_MODE)
        try:
            S.DATAMOSH_KNOB, S.PACKET_CORRUPTION_KNOB = 9, "x"
            S.MOSH_STAGE_POSITION, S.MOSH_BLEED_MODE = "nowhere", "sideways"
            notes = S._validate_effect_wiring()
            good = (
                len(notes) == 4
                and S.DATAMOSH_KNOB == 2
                and S.PACKET_CORRUPTION_KNOB is None
                and S.MOSH_STAGE_POSITION == "before_drift"
                and S.MOSH_BLEED_MODE == "shape_live_colour_held"
            )
            S.DATAMOSH_KNOB, S.PACKET_CORRUPTION_KNOB = 2, 2
            notes2 = S._validate_effect_wiring()
            good = good and any("both listen" in m for m in notes2)
            if not good:
                ok, why = False, "wiring validation did not behave as specified"
        finally:
            S.DATAMOSH_KNOB, S.PACKET_CORRUPTION_KNOB, S.MOSH_STAGE_POSITION, S.MOSH_BLEED_MODE = saved
    record("T10 wiring: any-knob corruption, positions agree, safe fallbacks", ok, why)


# --------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="Phase 2 tests")
    ap.add_argument("--ref", default="pre-phase2", help="git ref holding the OLD server.py")
    ap.add_argument("--old-file", help="path to an OLD server.py (instead of a git ref)")
    ap.add_argument("--quick", action="store_true", help="fewer frames (Pi)")
    args = ap.parse_args()

    print("=" * 74)
    print(" PHASE 2 TESTS: Data Mosh v2 + standalone Packet Corruption")
    print("=" * 74)
    print(f" numpy {np.__version__}, opencv {cv2.__version__}, python {sys.version.split()[0]}")
    import server as S  # noqa: E402

    old, where = load_old(args.ref, args.old_file)
    print(f" OLD = {where if old is not None else 'not available (' + str(where) + ')'}")
    print(f" NEW = {S.__file__}\n")

    t1_independence(S, args.quick)
    t2_corruption_identity(S, old, args.quick)
    t3_mosh_identity(S, args.quick)
    t4_reset(S)
    t5_first_frame(S)
    t6_duplicates(S)
    t7_sweep(S, args.quick)
    t8_allocation(S)
    t9_cancel(S)
    t10_wiring(S, args.quick)

    n_pass = sum(1 for _, s, _ in RESULTS if s == "PASS")
    n_fail = sum(1 for _, s, _ in RESULTS if s == "FAIL")
    n_skip = sum(1 for _, s, _ in RESULTS if s == "SKIP")
    print("\n" + "=" * 74)
    print(f" passed {n_pass}, failed {n_fail}, skipped {n_skip}")
    if n_skip:
        print(" NOTE: a skipped test proves nothing. Provide the OLD server (--ref / --old-file).")
    print(" RESULT: " + ("FAIL" if n_fail else ("INCOMPLETE (skips)" if n_skip else "PASS")))
    print("=" * 74)
    return 1 if (n_fail or n_skip) else 0


if __name__ == "__main__":
    sys.exit(main())
