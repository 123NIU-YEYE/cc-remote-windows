"""Platform differences for tests.

Each helper behaves exactly as the inline POSIX code it replaces on Linux and
macOS, so those runs exercise what they did before. Only Windows differs.
"""
from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

WINDOWS = sys.platform == "win32"


def native_path(path: str) -> str:
    """An absolute POSIX test path, absolute on this platform too.

    Product code rejects relative working directories, and "/repo" is not
    absolute on Windows. There it is rooted on the current drive
    ("C:\\repo"). Nothing is created.
    """
    return os.path.abspath(path) if WINDOWS else path


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
