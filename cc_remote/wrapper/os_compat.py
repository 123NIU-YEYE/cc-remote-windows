"""Small OS compatibility helpers that preserve Unix durability semantics."""
from __future__ import annotations

import errno
import os
import shutil
import stat
import sys
import time
from os import PathLike


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

    import ctypes
    import msvcrt
    from ctypes import wintypes

    generic_read = 0x80000000
    generic_write = 0x40000000
    file_share_read = 0x00000001
    file_share_write = 0x00000002
    file_share_delete = 0x00000004
    create_new = 1
    open_existing = 3
    file_attribute_normal = 0x80
    invalid_handle_value = wintypes.HANDLE(-1).value

    if flags & os.O_RDWR:
        access, access_flags = generic_read | generic_write, os.O_RDWR
    elif flags & os.O_WRONLY:
        access, access_flags = generic_write, os.O_WRONLY
    else:
        access, access_flags = generic_read, os.O_RDONLY
    disposition = (
        create_new if flags & os.O_CREAT and flags & os.O_EXCL
        else open_existing
    )

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    ]
    create_file.restype = wintypes.HANDLE
    handle = create_file(
        os.fspath(path), access,
        file_share_read | file_share_write | file_share_delete,
        None, disposition, file_attribute_normal, None,
    )
    if handle == invalid_handle_value:
        raise ctypes.WinError(ctypes.get_last_error())
    return msvcrt.open_osfhandle(handle, access_flags)


def pread(fd: int, length: int, offset: int) -> bytes:
    """Positioned read, backed by lseek+read where the platform lacks pread.

    Callers only use this on descriptors they exclusively own for the
    duration of the call, so relocating the file position is safe.
    """
    native_pread = getattr(os, "pread", None)
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
    native_pwrite = getattr(os, "pwrite", None)
    if native_pwrite is not None:
        return native_pwrite(fd, data, offset)
    saved = os.lseek(fd, 0, os.SEEK_CUR)
    try:
        os.lseek(fd, offset, os.SEEK_SET)
        return os.write(fd, data)
    finally:
        os.lseek(fd, saved, os.SEEK_SET)


def install_windows_shims() -> None:
    """Fill in the ``os`` attributes Windows lacks, with Unix-equivalent meaning.

    The wrapper is written against Unix semantics and calls ``os.getuid()`` /
    ``os.O_NOFOLLOW`` throughout its ownership and file-safety checks. Editing
    every call site is invasive, so define the missing attributes once instead;
    only genuinely absent attributes are added, leaving Unix untouched.

    * ``getuid``/``geteuid`` return ``0``, matching the ``st_uid`` placeholder
      Windows reports, so "owned by the current user" comparisons still hold.
      This is the same value :func:`current_uid` returns on Windows.
    * ``O_NOFOLLOW``/``O_DIRECTORY`` become ``0``: Windows ``os.open`` rejects
      them. Symlink/reparse safety is handled by :func:`force_rmtree` and
      :func:`open_with_share_delete` instead.
    * ``fchmod`` becomes a no-op (no POSIX mode bits), and ``pread``/``pwrite``
      fall back to the positioned helpers above.
    """
    if sys.platform != "win32":
        return

    if not hasattr(os, "getuid"):
        os.getuid = lambda: 0  # type: ignore[attr-defined]
    if not hasattr(os, "geteuid"):
        os.geteuid = lambda: 0  # type: ignore[attr-defined]
    if not hasattr(os, "O_NOFOLLOW"):
        os.O_NOFOLLOW = 0  # type: ignore[attr-defined]
    if not hasattr(os, "O_DIRECTORY"):
        os.O_DIRECTORY = 0  # type: ignore[attr-defined]
    if not hasattr(os, "fchmod"):
        os.fchmod = lambda _fd, _mode: None  # type: ignore[attr-defined]

    # Bind the helpers directly: a lambda that called ``pread`` by name would
    # re-enter the just-patched ``os.pread`` attribute and recurse forever.
    local_pread, local_pwrite = pread, pwrite
    if not hasattr(os, "pread"):
        os.pread = lambda fd, length, offset: local_pread(fd, length, offset)  # type: ignore[attr-defined]
    if not hasattr(os, "pwrite"):
        os.pwrite = lambda fd, data, offset: local_pwrite(fd, data, offset)  # type: ignore[attr-defined]

    # Windows synthesises ``st_mode`` as ``0o666`` for ordinary files, so every
    # "only the owner may read this" check of the form ``S_IMODE(mode) & 0o077``
    # would reject perfectly private files (ACLs, not mode bits, govern access
    # here). Report owner-only bits so those checks pass; group/other bits carry
    # no meaning on this platform. Patching the module function covers the many
    # call sites that write ``stat.S_IMODE(...)``.
    native_s_imode = stat.S_IMODE

    def _s_imode_owner_only(mode: int) -> int:
        return native_s_imode(mode) & 0o700

    stat.S_IMODE = _s_imode_owner_only  # type: ignore[assignment]

    # The wrapper fsyncs a directory after every atomic rename so the rename is
    # durable. POSIX permits ``open(dir, O_RDONLY)`` for that; Windows rejects
    # it with PermissionError ("Access is denied"), which aborted every
    # store migration. Windows has no directory fsync at all, so hand such
    # callers a disposable handle on the NUL device: fsync and close then
    # succeed and do nothing. Real file opens are untouched, and write-mode
    # directory opens still fail loudly.
    native_open = os.open

    # Windows has no ``*at()`` family: os.open/os.stat/os.unlink/os.mkdir/
    # os.replace raise NotImplementedError("dir_fd unavailable on this platform")
    # whenever dir_fd/src_dir_fd is passed, which aborted Claude turns while
    # staging workspace files. POSIX uses dir_fd to pin the operation to a
    # already-opened directory and dodge symlink races. Emulate it by recording
    # the path every directory descriptor came from, then joining names onto it.
    directory_fd_paths: dict[int, str] = {}

    def _dir_base(dir_fd: int | None) -> str | None:
        if dir_fd is None:
            return None
        base = directory_fd_paths.get(dir_fd)
        if base is None:
            raise NotImplementedError(
                "dir_fd unavailable: this descriptor was not opened through the "
                "compatibility layer")
        return base

    def _remember(handle: int, resolved: str) -> None:
        try:
            info = os.fstat(handle)
        except OSError:
            return
        if stat.S_ISDIR(info.st_mode):
            directory_fd_paths[handle] = resolved

    def _open_directory_tolerant(path, flags, mode=0o777, *, dir_fd=None):  # type: ignore[no-untyped-def]
        base = _dir_base(dir_fd)
        resolved = os.path.join(base, os.fspath(path)) if base is not None else os.fspath(path)
        if not flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT):
            try:
                is_dir = os.path.isdir(resolved)
            except (TypeError, ValueError, OSError):
                is_dir = False
            if is_dir:
                # Directories cannot be opened as file descriptors on Windows;
                # hand out the NUL placeholder and still record the path so any
                # later dir_fd use resolves to the right directory.
                handle = native_open(os.devnull, os.O_RDONLY)
                directory_fd_paths[handle] = os.path.abspath(resolved)
                return handle
        handle = native_open(resolved, flags, mode)
        _remember(handle, os.path.abspath(resolved))
        return handle

    os.open = _open_directory_tolerant  # type: ignore[assignment]

    native_stat = os.stat
    native_lstat = os.lstat
    native_unlink = os.unlink
    native_mkdir = os.mkdir
    native_rename = os.rename
    native_replace = os.replace
    native_close = os.close

    def _stat_tolerant(path, *args, dir_fd=None, **kwargs):  # type: ignore[no-untyped-def]
        base = _dir_base(dir_fd)
        target = os.path.join(base, os.fspath(path)) if base is not None else path
        return native_stat(target, *args, **kwargs)

    def _lstat_tolerant(path, *args, dir_fd=None, **kwargs):  # type: ignore[no-untyped-def]
        base = _dir_base(dir_fd)
        target = os.path.join(base, os.fspath(path)) if base is not None else path
        return native_lstat(target, *args, **kwargs)

    def _unlink_tolerant(path, *, dir_fd=None):  # type: ignore[no-untyped-def]
        base = _dir_base(dir_fd)
        return native_unlink(os.path.join(base, os.fspath(path)) if base is not None else path)

    def _mkdir_tolerant(path, mode=0o777, *, dir_fd=None):  # type: ignore[no-untyped-def]
        base = _dir_base(dir_fd)
        return native_mkdir(
            os.path.join(base, os.fspath(path)) if base is not None else path, mode)

    def _replace_tolerant(src, dst, *, src_dir_fd=None, dst_dir_fd=None):  # type: ignore[no-untyped-def]
        src_base = _dir_base(src_dir_fd)
        dst_base = _dir_base(dst_dir_fd)
        source = os.path.join(src_base, os.fspath(src)) if src_base is not None else src
        target = os.path.join(dst_base, os.fspath(dst)) if dst_base is not None else dst
        return native_replace(source, target)

    def _rename_tolerant(src, dst, *, src_dir_fd=None, dst_dir_fd=None):  # type: ignore[no-untyped-def]
        src_base = _dir_base(src_dir_fd)
        dst_base = _dir_base(dst_dir_fd)
        source = os.path.join(src_base, os.fspath(src)) if src_base is not None else src
        target = os.path.join(dst_base, os.fspath(dst)) if dst_base is not None else dst
        return native_rename(source, target)

    def _close_tolerant(handle: int) -> None:
        directory_fd_paths.pop(handle, None)
        native_close(handle)

    os.stat = _stat_tolerant  # type: ignore[assignment]
    os.lstat = _lstat_tolerant  # type: ignore[assignment]
    os.unlink = _unlink_tolerant  # type: ignore[assignment]
    os.mkdir = _mkdir_tolerant  # type: ignore[assignment]
    os.replace = _replace_tolerant  # type: ignore[assignment]
    os.rename = _rename_tolerant  # type: ignore[assignment]
    os.close = _close_tolerant  # type: ignore[assignment]

    # The placeholder above (and any real directory handle) cannot be synced on
    # Windows. Treat "not syncable" as "already durable": that is exactly the
    # Windows contract for directory entries, and it must not mask a genuine
    # write failure on a regular file.
    native_fsync = os.fsync
    unsyncable = {errno.EBADF, errno.EINVAL}

    def _fsync_tolerant(fd: int) -> None:
        try:
            native_fsync(fd)
        except OSError as exc:
            if exc.errno not in unsyncable:
                raise

    os.fsync = _fsync_tolerant  # type: ignore[assignment]

    # POSIX mode bits are meaningless on Windows, where ACLs govern access, and
    # chmod on a directory (or a file the caller does not own) can raise
    # PermissionError. Callers use chmod purely to tighten Unix permissions, so
    # a refusal here must not abort session bookkeeping.
    native_chmod = os.chmod

    def _chmod_tolerant(path, mode, *args, **kwargs):  # type: ignore[no-untyped-def]
        try:
            return native_chmod(path, mode, *args, **kwargs)
        except (PermissionError, NotImplementedError):
            return None

    os.chmod = _chmod_tolerant  # type: ignore[assignment]


def private_file_ok(info: os.stat_result) -> bool:
    """True when a stat result proves "only the current user may read this".

    POSIX mode bits do not exist on Windows: ``st_mode`` is synthesised as
    ``0o666`` for ordinary files, so the Unix check (``mode & 0o077 == 0``) would
    reject every file there. Windows privacy is governed by the DACL instead,
    which the caller hardens at write time via :func:`harden_private_file`; this
    returns ``True`` and leaves the regular-file/size checks to the caller.
    """
    if sys.platform == "win32":
        return True
    return (
        info.st_uid == os.getuid()
        and not info.st_mode & 0o077
        and info.st_nlink == 1
    )


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


def harden_private_file(path: str | PathLike[str]) -> None:
    """Best-effort: restrict a file's Windows DACL to the current user.

    Disables inheritance and grants full control only to the account running
    this process. Failure is deliberately non-fatal: the pairing file is still
    created under the user's own profile, and ``icacls`` is unavailable in some
    locked-down environments.
    """
    if sys.platform != "win32":
        return
    user = os.environ.get("USERNAME")
    if not user:
        return
    import subprocess

    try:
        subprocess.run(
            ["icacls", os.fspath(path), "/inheritance:r", "/grant:r", f"{user}:F"],
            capture_output=True,
            timeout=15,
            check=False,
        )
    except Exception:  # pragma: no cover - environment dependent
        pass