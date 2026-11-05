"""Attach the Wrapper to a running DeepSeek Harness **desktop app**.

The desktop app serves the same loopback API as ``dsh web``, but its launch
token is minted per process and never persisted, so the interactive
``dsh web`` pairing flow cannot see it. The companion DSH extension
``integrations/dsh/cc-remote-desktop-url.mjs`` — loaded through the desktop
profile's patch layer — publishes ``{schema, pid, port, origin, token}`` to a
private file at startup. This module reads and validates that file.

Nothing here mutates DSH: it only reads a file the extension wrote and checks
that the owning process is still alive.
"""
from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path

from cc_remote.wrapper.dsh_client import DshConnection, DshError
from cc_remote.wrapper.os_compat import private_file_ok

SCHEMA = 1
MAX_BYTES = 8192
ENV_OVERRIDE = "CC_REMOTE_DSH_DESKTOP_FILE"

_MISSING = "未找到桌面版 DSH 的配对信息。请确认桌面版正在运行，且已通过 cordis.patch.yml 加载 cc-remote 扩展。"
_STALE = "桌面版 DSH 的配对信息已过期（写入它的进程已退出）。请重启桌面版，或重新运行本命令。"
_INVALID = "桌面版 DSH 的配对文件无效，请检查文件权限或重新生成。"


def default_state_path() -> Path:
    """Where the extension writes its state, honouring explicit overrides."""
    override = os.environ.get(ENV_OVERRIDE, "").strip()
    if override:
        return Path(override).expanduser()
    home = os.environ.get("DSH_HOME", "").strip()
    base = Path(home).expanduser() if home else Path.home() / ".dsh"
    return base / "cc-remote-desktop.json"


def pid_alive(pid: int) -> bool:
    """True when ``pid`` currently names a live process this user can see."""
    if pid <= 0:
        return False
    if sys.platform != "win32":
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            # Exists but owned by someone else; we cannot use it either way.
            return True
        return True
    import ctypes

    process_query_limited_information = 0x1000
    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
    if not handle:
        return False
    kernel32.CloseHandle(handle)
    return True


def read_state(path: Path | None = None) -> dict:
    """Read and structurally validate the extension's state file."""
    target = path or default_state_path()
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        fd = os.open(target, flags)
    except OSError:
        raise DshError("desktop_not_running", _MISSING) from None
    try:
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if (not stat.S_ISREG(info.st_mode) or not private_file_ok(info)
                    or info.st_size > MAX_BYTES):
                raise DshError("desktop_invalid", _INVALID)
            raw = stream.read(MAX_BYTES + 1)
    except OSError:
        raise DshError("desktop_invalid", _INVALID) from None
    try:
        data = json.loads(raw)
    except ValueError:
        raise DshError("desktop_invalid", _INVALID) from None
    if (not isinstance(data, dict) or data.get("schema") != SCHEMA
            or not isinstance(data.get("pid"), int)
            or not isinstance(data.get("port"), int)):
        raise DshError("desktop_invalid", _INVALID)
    return data


def load_connection(path: Path | None = None) -> DshConnection:
    """Build a validated connection to the running desktop app."""
    data = read_state(path)
    if not pid_alive(data["pid"]):
        raise DshError("desktop_stale", _STALE)
    origin = data.get("origin")
    token = data.get("token")
    port = data["port"]
    if not isinstance(origin, str) or not origin.endswith(f":{port}"):
        raise DshError("desktop_invalid", _INVALID)
    # DshConnection re-validates the loopback origin and the 43-character token.
    return DshConnection(origin, token)
