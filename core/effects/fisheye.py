"""Fisheye effect (new).

A bulge in the middle of the picture (a wide-angle lens), with an optional colour fringe that
grows towards the edges (the red and blue channels are magnified slightly differently). The
edges of the picture move only slightly. With FISHEYE_NOISE_ENABLED a smooth, moving noise makes
the bulge "breathe" and its centre wander.

Place it early in the order: later effects then act on the warped picture.
"""

import cv2
import numpy as np

from core import noise
from core.scale import n
from state import MUTABLE_PROCESSING_BUFFER
from tuning import (
    DISABLED_EFFECTS,
    FISHEYE_ACTIVE_ABOVE,
    FISHEYE_CA,
    FISHEYE_FALLOFF,
    FISHEYE_MAX,
    FISHEYE_NOISE_AMOUNT,
    FISHEYE_NOISE_CENTER_PX,
    FISHEYE_NOISE_ENABLED,
    FISHEYE_NOISE_SPEED,
    FRAME_H,
    FRAME_W,
)


class FisheyeEngine:
    """Buffers (about 8 MiB, allocated once) and the per-frame work of Fisheye."""

    def __init__(self):
        H, W = FRAME_H, FRAME_W
        self.qh, self.qw = H // 4, W // 4
        self.qx = np.arange(self.qw, dtype=np.float32) * 4.0 + 1.5  # full-resolution x of each map point
        self.qy = np.arange(self.qh, dtype=np.float32) * 4.0 + 1.5
        self.mapx_q = np.zeros((self.qh, self.qw), np.float32)
        self.mapy_q = np.zeros((self.qh, self.qw), np.float32)
        self.mapx = np.zeros((H, W), np.float32)
        self.mapy = np.zeros((H, W), np.float32)
        self.cax = np.zeros((H, W), np.float32)
        self.cay = np.zeros((H, W), np.float32)
        self.out3 = np.zeros((H, W, 3), np.uint8)
        self.plane = np.zeros((H, W), np.uint8)
        self.planes_out = [np.zeros((H, W), np.uint8) for _ in range(3)]
        self.table = noise.table_for("fisheye")

    def step(self, frame, k, t):
        H, W = FRAME_H, FRAME_W
        strength = k * FISHEYE_MAX
        cx, cy = W / 2.0, H / 2.0
        if noise.is_on(FISHEYE_NOISE_ENABLED):
            strength *= 1.0 + FISHEYE_NOISE_AMOUNT * float(noise.noise2(self.table, 0.3, t * FISHEYE_NOISE_SPEED))
            cx += FISHEYE_NOISE_CENTER_PX * float(noise.noise2(self.table, 1.3, t * FISHEYE_NOISE_SPEED + 20.0))
            cy += FISHEYE_NOISE_CENTER_PX * float(noise.noise2(self.table, 2.3, t * FISHEYE_NOISE_SPEED + 40.0))
        nx = (self.qx - cx) / (W / 2.0)
        ny = (self.qy - cy) / (H / 2.0)
        r2 = nx[None, :] ** 2 + ny[:, None] ** 2
        f = 1.0 - strength * np.exp(-r2 * FISHEYE_FALLOFF)  # < 1 near the centre: samples closer in = magnified
        self.mapx_q[:] = cx + nx[None, :] * f * (W / 2.0)
        self.mapy_q[:] = cy + ny[:, None] * f * (H / 2.0)
        cv2.resize(self.mapx_q, (W, H), dst=self.mapx, interpolation=cv2.INTER_LINEAR)
        cv2.resize(self.mapy_q, (W, H), dst=self.mapy, interpolation=cv2.INTER_LINEAR)
        if FISHEYE_CA > 0:
            for c, scale in ((0, 1.0 - FISHEYE_CA), (1, 1.0), (2, 1.0 + FISHEYE_CA)):  # B, G, R
                if scale == 1.0:
                    mx, my = self.mapx, self.mapy
                else:
                    np.multiply(self.mapx, scale, out=self.cax)
                    self.cax += cx * (1.0 - scale)
                    np.multiply(self.mapy, scale, out=self.cay)
                    self.cay += cy * (1.0 - scale)
                    mx, my = self.cax, self.cay
                cv2.extractChannel(frame, c, dst=self.plane)
                cv2.remap(self.plane, mx, my, cv2.INTER_LINEAR, dst=self.planes_out[c], borderMode=cv2.BORDER_REFLECT)
            cv2.merge(self.planes_out, dst=frame)
        else:
            cv2.remap(frame, self.mapx, self.mapy, cv2.INTER_LINEAR, dst=self.out3, borderMode=cv2.BORDER_REFLECT)
            np.copyto(frame, self.out3)


_FISHEYE = FisheyeEngine() if "fisheye" not in DISABLED_EFFECTS else None


def apply_fisheye(raw_knob, current_time):
    # Fisheye entry point. raw_knob 0-1023. Modifies MUTABLE_PROCESSING_BUFFER in place.
    if not _acts_fisheye(raw_knob):
        return
    _FISHEYE.step(MUTABLE_PROCESSING_BUFFER, n(raw_knob), current_time)


def _acts_fisheye(raw):
    return n(raw) > FISHEYE_ACTIVE_ABOVE
