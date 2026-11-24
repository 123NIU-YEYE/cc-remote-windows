"""Small OS compatibility helpers that preserve Unix durability semantics."""
from __future__ import annotations

import os
import shutil
import stat
import sys
import time
from os import PathLike

# Captured at import time, before install_windows_shims() replaces them. The
# pread/pwrite fallbacks below must never look these up on ``os`` again: on
# Windows the attribute *is* the shim, so a fresh lookup recursed forever
# (surfacing as "preview capability store unavailable ... RecursionError").
_NATIVE_PREAD = getattr(os, "pread", None)
_NATIVE_PWRITE = getattr(os, "pwrite", None)


def current_uid() -> int:
    """Return the Unix uid, or the stable Windows ``st_uid`` placeholder."""
    getuid = getattr(os, "getuid", None)
    return getuid() if getuid is not None else 0


def fchmod(fd: int, path: str | PathLike[str], mode: int) -> None:
    """Set a file mode using the descriptor where the platform supports it."""
    native_fchmod = getattr(os, "fchmod", None)
    if native_fchmod is not None:
        native_fchmod(fd, mode)
    else:
        os.chmod(path, mode)


def clear_readonly(path: str | PathLike[str]) -> None:
    """Let a file be deleted or replaced whatever its mode, as POSIX does.

    Windows refuses both for a file with the read-only attribute, which
    ``os.chmod`` sets for any mode without owner write. No-op elsewhere.
    """
    if sys.platform != "win32":
        return
    try:
        mode = os.lstat(path).st_mode
    except FileNotFoundError:
        return
    if stat.S_ISREG(mode) and not mode & stat.S_IWRITE:
        os.chmod(path, stat.S_IMODE(mode) | stat.S_IWRITE)


def fsync_directory(path: str | PathLike[str]) -> None:
    """Persist a directory entry on Unix; Windows has no directory fsync."""
    if sys.platform == "win32":
        return
    directory_fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def _win32_long_path(path: str) -> str:
    """Prefix an absolute Windows path with \\\\?\\ so it bypasses MAX_PATH.

    Without this, any path at or beyond 260 characters silently misbehaves
    on delete (directory entries can be enumerated but not reliably
    unlinked/rmdir'd) unless the machine has opted into the
    ``LongPathsEnabled`` registry setting, which cc-remote cannot assume.
    """
    absolute = os.path.abspath(path)
    if absolute.startswith("\\\\?\\"):
        return absolute
    if absolute.startswith("\\\\"):
        return "\\\\?\\UNC\\" + absolute[2:]
    return "\\\\?\\" + absolute


def force_rmtree(path: str | PathLike[str]) -> None:
    """Remove a directory tree, clearing Windows read-only bits first.

    Git always creates loose objects read-only. POSIX directory write
    permission is enough to unlink them; Windows blocks the unlink outright
    unless the read-only attribute is cleared first. The path is also
    resolved through the \\\\?\\ long-path form so deletion isn't silently
    unreliable for paths at or beyond MAX_PATH (260 chars), which ordinary
    temp-directory nesting reaches easily. A short retry absorbs the
    transient "directory not empty" Windows reports while another process
    (antivirus, search indexing) briefly holds a just-deleted file.
    """
    if sys.platform != "win32":
        shutil.rmtree(path)
        return

    def _clear_readonly_and_retry(func, target, exc_info):
        try:
            os.chmod(target, stat.S_IWRITE)
            func(target)
        except FileNotFoundError:
            pass

    long_path = _win32_long_path(os.fspath(path))
    attempts = 5
    for attempt in range(attempts):
        try:
            shutil.rmtree(long_path, onerror=_clear_readonly_and_retry)
            return
        except OSError:
            if not os.path.exists(long_path):
                return
            if attempt == attempts - 1:
                raise
            time.sleep(min(0.05 * (attempt + 1), 0.5))


def open_with_share_delete(
    path: str | PathLike[str], flags: int, mode: int = 0o600,
) -> int:
    """Open a file so it can still be unlinked/replaced while this fd is open.

    Plain ``os.open`` on Windows does not grant ``FILE_SHARE_DELETE``, so any
    unlink or rename of the path from another descriptor (including a second
    open in this same process) raises ``PermissionError: [WinError 32]``
    while this fd remains open. Unix descriptors never carry that
    restriction, so this only changes behavior on Windows; unlike
    ``os.O_TEMPORARY`` it does not delete the file when the fd closes, so it
    is safe for paths that may legitimately outlive this open.
    """
    if sys.platform != "win32":
        return os.open(path, flags, mode)

    from cc_remote.wrapper.windows_fd import open_descriptor
    return open_descriptor(path, flags, mode)


def open_shared(path: str | PathLike[str], mode: str = "rb", **kwargs):
    """Builtin open that lets the owning program move or delete the file.

    Native engines rename and replace their own files while cc-remote reads
    them: Codex moves archived rollouts and rewrites its config, and Claude
    removes expired transcripts. A Windows handle without delete sharing makes
    those operations fail for as long as the read lasts.
    """
    if sys.platform == "win32":
        kwargs.setdefault("opener", open_with_share_delete)
    return open(path, mode, **kwargs)


def pread(fd: int, length: int, offset: int) -> bytes:
    """Positioned read, backed by lseek+read where the platform lacks pread.

    Callers only use this on descriptors they exclusively own for the
    duration of the call, so relocating the file position is safe.
    """
    # ``os.pread`` may already be this shim (install_windows_shims replaces it),
    # so only trust the captured native function, never a fresh attribute lookup.
    native_pread = _NATIVE_PREAD
    if native_pread is not None:
        return native_pread(fd, length, offset)
    saved = os.lseek(fd, 0, os.SEEK_CUR)
    try:
        os.lseek(fd, offset, os.SEEK_SET)
        return os.read(fd, length)
    finally:
        os.lseek(fd, saved, os.SEEK_SET)


def pwrite(fd: int, data: bytes, offset: int) -> int:
    """Positioned write, backed by lseek+write where the platform lacks pwrite."""
    native_pwrite = _NATIVE_PWRITE
    if native_pwrite is not None:
        return native_pwrite(fd, data, offset)
    saved = os.lseek(fd, 0, os.SEEK_CUR)
    try:
        os.lseek(fd, offset, os.SEEK_SET)
        return os.write(fd, data)
    finally:
        os.lseek(fd, saved, os.SEEK_SET)


def _windows_fchmod(fd: int, mode: int) -> None:
    """Owner-only modes become an owner-only ACL; other modes keep the
    inherited ACL, the Windows counterpart of a shared POSIX mode."""
    if not mode & 0o077:
        from cc_remote.windows_privacy import private_descriptor
        private_descriptor(fd)


def install_windows_shims() -> None:
    """Install only explicit Windows primitives; never fake permission checks.

    A Windows uid is a compatibility placeholder, not ownership evidence. Native
    ownership/privacy checks use windows_privacy. Relative reads and mutations
    use native root handles, preserving the pinned-directory boundary.
    """
    if sys.platform != "win32":
        return
    from cc_remote.wrapper.windows_fd import install
    install()
    if not hasattr(os, "getuid"):
        os.getuid = lambda: 0  # type: ignore[attr-defined]
    if not hasattr(os, "geteuid"):
        os.geteuid = lambda: 0  # type: ignore[attr-defined]
    # Python 3.13 added a native os.fchmod on Windows that only toggles the
    # read-only attribute (and rejects directories). Replace it either way so
    # an owner-only mode really gets an owner-only ACL.
    os.fchmod = _windows_fchmod  # type: ignore[attr-defined]
    if not hasattr(os, "pread"):
        os.pread = pread  # type: ignore[attr-defined]
    if not hasattr(os, "pwrite"):
        os.pwrite = pwrite  # type: ignore[attr-defined]


def kill_process_tree(pid: int) -> None:
    """Terminate ``pid`` and every descendant it spawned.

    On Windows ``Popen.terminate()`` kills only the direct child. The Codex
    app-server starts ``node.exe``/``node_repl.exe`` helpers that inherit the
    thread-writer locks, so terminating just the app-server leaves those helpers
    running: the locks stay held and the next ``thread/resume`` for the same
    session fails with "already has an active writer". ``taskkill /T`` walks the
    tree, which is what the POSIX ``killpg`` path does for Unix.
    """
    if sys.platform != "win32":
        return
    import subprocess

    try:
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(pid)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=15,
            check=False,
        )
    except Exception:  # pragma: no cover - environment dependent
        pass
