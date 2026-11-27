"""One Wrapper owns each private state directory across reconnects/releases."""
from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
import stat
from typing import Iterator

from cc_remote.wrapper import file_lock_compat as fcntl


@contextmanager
def wrapper_instance(state_dir: Path) -> Iterator[bool]:
    state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    # Keep the inode permanently. Unlinking it after unlock allows two owners
    # to lock different files at the same path. OS close/crash releases the lock;
    # stale PID files and process-name heuristics are unnecessary.
    fd = os.open(state_dir / "wrapper.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError("Wrapper lock must be a regular file")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        yield True
    finally:
        os.close(fd)
