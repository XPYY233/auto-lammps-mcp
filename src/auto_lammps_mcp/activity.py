"""Coordinate local execution with service replacement across threads and processes."""
from contextlib import contextmanager
import fcntl
import os


@contextmanager
def activity_lock(home, *, replacement=False):
    # Each caller opens its own descriptor: shared worker locks remain independent
    # across threads, and the kernel releases ownership when a process exits.
    fd = os.open(home / "activity.lock", os.O_RDWR | os.O_CREAT, 0o600)
    try:
        mode = fcntl.LOCK_EX if replacement else fcntl.LOCK_SH
        try:
            fcntl.flock(fd, mode | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
        else:
            try:
                yield True
            finally:
                fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)
