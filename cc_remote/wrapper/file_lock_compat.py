"""Cross-platform advisory file locking for Windows and Unix."""
from __future__ import annotations

import os
import sys

if sys.platform == "win32":
    import ctypes
    import errno
    import msvcrt
    from ctypes import wintypes

    LOCK_SH = 1
    LOCK_EX = 2
    LOCK_NB = 4
    LOCK_UN = 8

    class _Overlapped(ctypes.Structure):
        _fields_ = [("internal", ctypes.c_size_t), ("internal_high", ctypes.c_size_t),
                    ("offset", wintypes.DWORD), ("offset_high", wintypes.DWORD),
                    ("event", wintypes.HANDLE)]

    def flock(fd: int, operation: int) -> None:
        """Use real byte-range locks, including shared locks on read-only FDs."""
        if hasattr(fd, "fileno"):
            fd = fd.fileno()
        base = operation & ~LOCK_NB
        if base not in {LOCK_SH, LOCK_EX, LOCK_UN}:
            raise ValueError(f"Unsupported lock operation: {operation}")
        msvcrt.setmode(fd, os.O_BINARY)
        handle = msvcrt.get_osfhandle(fd)
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        overlapped = _Overlapped()
        if base == LOCK_UN:
            unlock = kernel.UnlockFileEx
            unlock.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD,
                               wintypes.DWORD, ctypes.POINTER(_Overlapped)]
            unlock.restype = wintypes.BOOL
            succeeded = unlock(handle, 0, 1, 0, ctypes.byref(overlapped))
        else:
            lock = kernel.LockFileEx
            lock.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD,
                             wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(_Overlapped)]
            lock.restype = wintypes.BOOL
            flags = (2 if base == LOCK_EX else 0) | (1 if operation & LOCK_NB else 0)
            succeeded = lock(handle, flags, 0, 1, 0, ctypes.byref(overlapped))
        if not succeeded:
            error = ctypes.get_last_error()
            if operation & LOCK_NB and error == 33:
                raise BlockingIOError(errno.EAGAIN, "file is already locked")
            raise ctypes.WinError(error)

else:
    import fcntl

    LOCK_SH = fcntl.LOCK_SH
    LOCK_EX = fcntl.LOCK_EX
    LOCK_UN = fcntl.LOCK_UN
    LOCK_NB = fcntl.LOCK_NB
    flock = fcntl.flock
