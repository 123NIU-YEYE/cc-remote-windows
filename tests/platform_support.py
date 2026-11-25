"""Platform differences for tests.

Each helper behaves exactly as the inline POSIX code it replaces on Linux and
macOS, so those runs exercise what they did before. Only Windows differs.
"""
from __future__ import annotations

import os
import shutil
import stat
import sys
import tempfile
from pathlib import Path

WINDOWS = sys.platform == "win32"

# A short directory that exists. Tests use /tmp to keep AF_UNIX socket paths
# under the 104 bytes macOS allows. On Windows "/tmp" is \tmp on the current
# drive, which exists on some machines and not on others.
TMP = tempfile.gettempdir() if WINDOWS else "/tmp"

# Windows starts System32\bash.exe (WSL, often without a distribution) for
# "bash" before searching PATH; PATH finds Git Bash, as an interactive shell does.
BASH = (shutil.which("bash") or "bash") if WINDOWS else "bash"


def native_path(path: str) -> str:
    """An absolute POSIX test path, absolute on this platform too.

    Product code rejects relative working directories, and "/repo" is not
    absolute on Windows. There it is rooted on the current drive
    ("C:\\repo"). Nothing is created.
    """
    return os.path.abspath(path) if WINDOWS else path


def extended_path(path) -> str:
    """``path`` that plain open() can use beyond MAX_PATH (260) on Windows.

    Native engines write with long-path-aware runtimes, so files of a deep
    project can exceed that limit. Unchanged on POSIX.
    """
    if not WINDOWS:
        return os.fspath(path)
    absolute = os.path.abspath(path)
    return absolute if absolute.startswith("\\\\?\\") else "\\\\?\\" + absolute


def set_home(monkeypatch, path) -> None:
    """Redirect ``~``. Windows resolves it from USERPROFILE and ignores HOME."""
    monkeypatch.setenv("HOME", str(path))
    if WINDOWS:
        monkeypatch.setenv("USERPROFILE", str(path))


def is_private(path) -> bool:
    """Only the owner can access ``path``: mode bits on POSIX, the ACL on Windows."""
    if WINDOWS:
        from cc_remote.windows_privacy import owned_path
        return owned_path(path, private=True)
    return not stat.S_IMODE(os.stat(path).st_mode) & 0o077


def has_mode(path, mode: int) -> bool:
    """POSIX: the permission bits are exactly ``mode``.

    Windows has no mode bits. An owner-only ``mode`` must be a private ACL; a
    wider mode leaves the inherited ACL, which a test cannot tell apart, so
    it is accepted.
    """
    if WINDOWS:
        return is_private(path) if not mode & 0o077 else True
    return stat.S_IMODE(os.stat(path).st_mode) == mode


def make_public(path, mode: int) -> None:
    """Give ``path`` a POSIX ``mode`` that may expose it to other users.

    POSIX: exactly ``os.chmod``. Windows has no mode bits: a mode granting
    group or other access adds an Everyone ACE (read, plus write when the mode
    grants it), which is what makes a file non-private there.
    """
    if not WINDOWS:
        os.chmod(path, mode)
        return
    if not mode & 0o077:
        return
    import ntsecuritycon
    import win32security as security

    descriptor = security.GetNamedSecurityInfo(
        str(path), security.SE_FILE_OBJECT, security.DACL_SECURITY_INFORMATION)
    acl = descriptor.GetSecurityDescriptorDacl() or security.ACL()
    rights = ntsecuritycon.FILE_GENERIC_READ
    if mode & 0o022:
        rights |= ntsecuritycon.FILE_GENERIC_WRITE
    acl.AddAccessAllowedAce(security.ACL_REVISION, rights, security.CreateWellKnownSid(security.WinWorldSid))
    security.SetNamedSecurityInfo(str(path), security.SE_FILE_OBJECT,
                                  security.DACL_SECURITY_INFORMATION, None, None, acl, None)


def replace_open_file(source, target) -> None:
    """``os.replace(source, target)`` while a reader still has ``target`` open.

    Windows refuses to replace an open file even when the reader shares
    delete (WinError 5), but lets it be deleted. Deleting it first is the same
    rewrite for the reader, and it also fails unless the reader shares delete,
    as it must so engines can replace their own files.
    """
    if WINDOWS:
        os.unlink(target)
    os.replace(source, target)


def fake_executable(path, source: str) -> Path:
    """A runnable fake CLI that executes Python ``source``; returns what to run.

    POSIX: ``path`` itself, with a shebang for this interpreter. Windows cannot
    run shebang scripts, so ``path`` + ".cmd" launches the source with this
    interpreter. cmd.exe re-parses arguments: keep them free of ``^&|<>%``.
    """
    path = Path(path)
    if not WINDOWS:
        path.write_text(f"#!{sys.executable}\n{source}", encoding="utf-8")
        path.chmod(0o755)
        return path
    script = path.with_name(path.name + ".py")
    script.write_text(source, encoding="utf-8")
    launcher = path.with_name(path.name + ".cmd")
    launcher.write_text(f'@"{sys.executable}" "{script}" %*\r\n', encoding="utf-8")
    return launcher
