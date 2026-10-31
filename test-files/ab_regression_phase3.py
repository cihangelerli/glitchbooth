#!/usr/bin/env python3
"""Basic Phase 3 configuration and pipeline smoke checks.

Run from the repository root with the booth stopped:
    python3 ab_regression_phase3.py
"""
import os

os.environ.setdefault("IK_URL_ENDPOINT", "https://ik.imagekit.io/mock")
os.environ.setdefault("IK_PRIVATE_KEY", "mock_key")

import numpy as np
import server as S


def check(name, condition):
    print(f"[{'PASS' if condition else 'FAIL'}] {name}")
    return bool(condition)


def main():
    S.initialize_crt_mask()
    defaults = (
        list(S.EFFECT_ORDER),
        {k: list(v) for k, v in S.KNOB_ATTACHMENTS.items()},
        dict(S.EFFECT_KNOB_WINDOW),
        dict(S.EFFECT_COST_WEIGHT),
    )
    ok = []
    ok.append(check("all ordered effects have handlers", all(e in S.EFFECT_HANDLERS for e in S.EFFECT_ORDER)))
    ok.append(check("six primary labels are available", len(S.get_knob_labels()) == 6))
    ok.append(check("unwindowed raw value is unchanged", S._windowed_raw(517, (0.0, 1.0)) == 517))
    ok.append(check("window low edge is off", S._windowed_raw(300, (0.35, 1.0)) == 0))
    ok.append(check("window top is full", S._windowed_raw(1023, (0.35, 1.0)) == 1023))
    notes = S.set_effect_config(order=["unknown", "melt", "melt"], attachments={9: ["melt"], 4: ["melt"]}, windows={"melt": (0.9, 0.2)})
    ok.append(check("invalid configuration is handled", bool(notes) and len(S._EFFECT_PLAN) == 1))
    S.set_effect_config(order=defaults[0], attachments=defaults[1], windows=defaults[2], weights=defaults[3])
    frame = np.zeros((S.FRAME_H, S.FRAME_W, 3), dtype=np.uint8)
    S.MUTABLE_PROCESSING_BUFFER[:] = frame
    S.apply_glitch_pipeline(5000.0, S.ControlPacket([0] * 6, 0, "live"))
    ok.append(check("pipeline runs with all effects dormant", S.MUTABLE_PROCESSING_BUFFER.shape == frame.shape))
    return 0 if all(ok) else 1


if __name__ == "__main__":
    raise SystemExit(main())
