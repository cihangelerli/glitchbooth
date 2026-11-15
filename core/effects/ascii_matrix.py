"""ASCII Matrix effect.

Moved from server.py unchanged (function and class bodies identical, names unchanged).
No thread, file access or log line when this module is imported; it only creates its
buffers and tables.
"""

import logging

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from core.scale import n
from state import MUTABLE_PROCESSING_BUFFER
from tuning import (
    ASCII_ACTIVE_ABOVE,
    ASCII_AUTO_CONTRAST,
    ASCII_BLOOM_BLUR,
    ASCII_BLOOM_GAIN,
    ASCII_CLAHE_CLIP,
    ASCII_CLAHE_TILES,
    ASCII_COLOUR,
    ASCII_FONT_EXTRA_PX,
    ASCII_FONT_LIFT_PX,
    ASCII_GRID_H,
    ASCII_GRID_W,
    ASCII_MIX_CURVE,
    ASCII_SPARK_COLOUR,
    ASCII_SPARK_INTERVAL,
    ASCII_SPARK_LENGTH,
    ASCII_SPARK_RARITY,
    ASCII_SPARK_START_FRAMES,
    ASCII_TEXT_GAIN,
    DISABLED_EFFECTS,
    FRAME_H,
    FRAME_W,
)

logger = logging.getLogger("glitchbooth.core.effects.ascii_matrix")


# ASCII ENGINE PRE-ALLOCATED STORAGE
# =========================================================================
CELL_W = FRAME_W // ASCII_GRID_W
CELL_H = FRAME_H // ASCII_GRID_H

ASCII_LOWRES_GRAY = np.zeros((ASCII_GRID_H, ASCII_GRID_W), dtype=np.uint8, order="C")
ASCII_UPRES_GRAY = np.zeros((FRAME_H, FRAME_W), dtype=np.uint8, order="C")

ASCII_BLOCK_BUFFER = np.zeros(
    (ASCII_GRID_H, ASCII_GRID_W, CELL_H, CELL_W), dtype=np.uint8, order="C"
)

ASCII_TILES = np.zeros((16, CELL_H, CELL_W), dtype=np.uint8, order="C")

ASCII_GREEN_BUFFER = np.zeros((FRAME_H, FRAME_W, 3), dtype=np.uint8, order="C")
ASCII_NOISE_LOW = np.zeros((ASCII_GRID_H, ASCII_GRID_W), dtype=np.uint8, order="C")
ASCII_NOISE_UP = np.zeros((FRAME_H, FRAME_W), dtype=np.uint8, order="C")

clahe = cv2.createCLAHE(
    clipLimit=ASCII_CLAHE_CLIP,
    tileGridSize=tuple(ASCII_CLAHE_TILES)
    if "ascii_matrix" not in DISABLED_EFFECTS
    else (8, 8),
)

SPARK_COUNTER = 0
SPARK_INTERVAL = ASCII_SPARK_START_FRAMES  # re-rolled within ASCII_SPARK_INTERVAL later


# ASCII TILES
# ==================================================


def init_ascii_tiles():
    global ASCII_TILES

    char_ramp = [
        ".",
        ",",
        ":",
        ";",
        "i",
        "o",
        "*",
        "?",
        "%",
        "0",
        "$",
        "&",
        "@",
        "▒",
        "▓",
        "█",
    ]

    pi_font_paths = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationMono-Bold.ttf",
        "/usr/share/fonts/truetype/freefont/FreeMonoBold.ttf",
        "Courier New Bold",  # Windows/Mac fallback
    ]

    font = None
    target_font_size = max(CELL_W, CELL_H) + ASCII_FONT_EXTRA_PX

    for path in pi_font_paths:
        try:
            font = ImageFont.truetype(path, target_font_size)
            logger.info("ASCII font loaded.")
            break
        except OSError:
            continue

    if font is None:
        logger.warning(
            "Monospace TTF not found. Using system default. Text may appear small."
        )
        font = ImageFont.load_default()

    for i, char in enumerate(char_ramp):
        img = Image.new("L", (CELL_W, CELL_H), color=0)
        draw = ImageDraw.Draw(img)

        left, top, right, bottom = draw.textbbox((0, 0), char, font=font)
        w, h = right - left, bottom - top

        draw.text(
            ((CELL_W - w) // 2, (CELL_H - h) // 2 - ASCII_FONT_LIFT_PX),
            char,
            font=font,
            fill=255,
        )

        ASCII_TILES[i] = np.array(img, dtype=np.uint8)


def apply_ascii_matrix(raw_knob, current_time):
    global SPARK_COUNTER, SPARK_INTERVAL
    if not _acts_ascii_matrix(raw_knob):
        return
    ascii_val = n(raw_knob)
    cv2.cvtColor(MUTABLE_PROCESSING_BUFFER, cv2.COLOR_BGR2GRAY, dst=ASCII_UPRES_GRAY)
    ASCII_UPRES_GRAY[:] = clahe.apply(ASCII_UPRES_GRAY)
    cv2.resize(
        ASCII_UPRES_GRAY,
        (ASCII_GRID_W, ASCII_GRID_H),
        dst=ASCII_LOWRES_GRAY,
        interpolation=cv2.INTER_NEAREST,
    )
    if ASCII_AUTO_CONTRAST:
        cv2.normalize(ASCII_LOWRES_GRAY, ASCII_LOWRES_GRAY, 0, 255, cv2.NORM_MINMAX)
    np.right_shift(
        ASCII_LOWRES_GRAY, 4, out=ASCII_LOWRES_GRAY
    )  # 16 levels = 16 characters
    np.take(ASCII_TILES, ASCII_LOWRES_GRAY, axis=0, out=ASCII_BLOCK_BUFFER)
    ASCII_UPRES_GRAY[:] = ASCII_BLOCK_BUFFER.transpose(0, 2, 1, 3).reshape(
        FRAME_H, FRAME_W
    )
    for channel, level in enumerate(ASCII_COLOUR):  # (B, G, R)
        if level <= 0:
            ASCII_GREEN_BUFFER[:, :, channel] = 0
        elif level >= 255:
            ASCII_GREEN_BUFFER[:, :, channel] = ASCII_UPRES_GRAY
        else:
            ASCII_GREEN_BUFFER[:, :, channel] = cv2.convertScaleAbs(
                ASCII_UPRES_GRAY, alpha=level / 255.0
            )
    SPARK_COUNTER += 1
    if SPARK_COUNTER >= SPARK_INTERVAL:
        cv2.randu(ASCII_NOISE_LOW, 0, 255)
        rarity_base, rarity_slope, rarity_floor = ASCII_SPARK_RARITY
        threshold = max(
            rarity_floor, min(rarity_base, rarity_base - int(ascii_val * rarity_slope))
        )
        cv2.threshold(
            ASCII_NOISE_LOW, threshold, 255, cv2.THRESH_BINARY, dst=ASCII_NOISE_LOW
        )
        ASCII_NOISE_LOW[ASCII_LOWRES_GRAY == 0] = 0
        cv2.dilate(
            ASCII_NOISE_LOW,
            np.ones(
                (
                    np.random.randint(ASCII_SPARK_LENGTH[0], ASCII_SPARK_LENGTH[1] + 1),
                    1,
                ),
                dtype=np.uint8,
            ),
            dst=ASCII_NOISE_LOW,
        )
        SPARK_COUNTER, SPARK_INTERVAL = (
            0,
            np.random.randint(ASCII_SPARK_INTERVAL[0], ASCII_SPARK_INTERVAL[1] + 1),
        )
    cv2.resize(
        ASCII_NOISE_LOW,
        (FRAME_W, FRAME_H),
        dst=ASCII_NOISE_UP,
        interpolation=cv2.INTER_NEAREST,
    )
    ASCII_GREEN_BUFFER[(ASCII_NOISE_UP > 0) & (ASCII_UPRES_GRAY > 0)] = (
        ASCII_SPARK_COLOUR
    )
    bloom = cv2.resize(
        ASCII_GREEN_BUFFER, (ASCII_GRID_W, ASCII_GRID_H), interpolation=cv2.INTER_LINEAR
    )
    bloom = cv2.resize(
        cv2.GaussianBlur(bloom, (ASCII_BLOOM_BLUR, ASCII_BLOOM_BLUR), 0),
        (FRAME_W, FRAME_H),
        interpolation=cv2.INTER_LINEAR,
    )
    cv2.addWeighted(
        ASCII_GREEN_BUFFER,
        ASCII_TEXT_GAIN,
        bloom,
        ASCII_BLOOM_GAIN,
        0,
        dst=ASCII_GREEN_BUFFER,
    )
    ascii_mix = ascii_val**ASCII_MIX_CURVE
    cv2.addWeighted(
        ASCII_GREEN_BUFFER,
        ascii_mix,
        MUTABLE_PROCESSING_BUFFER,
        1.0 - ascii_mix,
        0,
        dst=MUTABLE_PROCESSING_BUFFER,
    )


def _acts_ascii_matrix(raw):
    return n(raw) > ASCII_ACTIVE_ABOVE
