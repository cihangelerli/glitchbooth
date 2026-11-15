"""Knob scale: the one helper that turns a raw knob value (0 to 1023) into 0.0 to 1.0.

Moved from server.py unchanged (function and class bodies identical, names unchanged).
No thread, file access or log line when this module is imported; it only creates its
buffers and tables.
"""


def n(val):
    return val / 1023.0
