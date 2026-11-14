"""File helpers: fsync of a file or folder, and atomic writes.

Moved from server.py unchanged (function bodies identical, names unchanged).
Standard library only: no logging, no shared state, no thread and no file access
when this module is imported.
"""

import os
import threading
import uuid


def _fsync_dir(path):
    fd = os.open(os.path.dirname(os.path.abspath(path)) or ".", os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _fsync_file(path):
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic_write_bytes(path, data, fsync=False):
    # Temp file in the same folder, then os.replace. A plain open() is used (not mkstemp) so the
    # file gets the same permissions as every other file the booth writes.
    tmp = f"{path}.{os.getpid()}.{threading.get_ident()}.{uuid.uuid4().hex[:8]}.tmp"
    try:
        with open(tmp, "xb") as f:
            f.write(data)
            f.flush()
            if fsync:
                os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
