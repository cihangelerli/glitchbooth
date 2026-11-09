"""Environment helper shared by config/settings.py (and any module that reads numbers from .env).

Moved from server.py unchanged. Standard library only; it logs through its own logger.
"""

import logging
import math
import os

logger = logging.getLogger("glitchbooth.config.env")


def _cfg_number(name, default, cast, minimum=None):
    # Number from the environment with a safe fallback: a typo in .env must never stop the
    # kiosk from starting. Only values that would break something are replaced (not a
    # number, not finite, or below `minimum`); the default is the one written at the call.
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = cast(raw.strip())
    except (TypeError, ValueError):
        logger.warning(
            "[CONFIG] %s=%r is not a valid %s, using %s",
            name,
            raw,
            cast.__name__,
            default,
        )
        return default
    if not math.isfinite(value) or (minimum is not None and value < minimum):
        logger.warning(
            "[CONFIG] %s=%r is not usable (must be a finite number%s), using %s",
            name,
            raw,
            f" >= {minimum}" if minimum is not None else "",
            default,
        )
        return default
    return value
