"""Read the existing desktop integration's private, process-bound DSH state."""
from __future__ import annotations

import json
import os
from pathlib import Path
import stat

from cc_remote.wrapper.dsh_client import DshConnection, DshError
from cc_remote.wrapper.process_scan import process_identity, process_owner_uid


def load_connection(path: Path | None = None) -> DshConnection:
    override = os.environ.get("CC_REMOTE_DSH_DESKTOP_FILE", "").strip()
    home = os.environ.get("DSH_HOME", "").strip()
    target = path or (Path(override).expanduser() if override else
                      (Path(home).expanduser() if home else Path.home() / ".dsh") / "cc-remote-desktop.json")
    try:
        flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0)
        with os.fdopen(os.open(target, flags), "rb") as stream:
            info = os.fstat(stream.fileno())
            if os.name == "nt":
                from cc_remote.windows_privacy import owned_descriptor
                private = owned_descriptor(stream.fileno(), private=True)
            else:
                private = info.st_uid == os.getuid() and not info.st_mode & 0o077
            if not stat.S_ISREG(info.st_mode) or not private or info.st_nlink != 1 or info.st_size > 8192:
                raise ValueError
            data = json.loads(stream.read(8193))
        if (not isinstance(data, dict) or data.get("schema") != 1
                or type(data.get("pid")) is not int or data["pid"] <= 0
                or type(data.get("port")) is not int):
            raise ValueError
        identity = process_identity(data["pid"])
        if identity is None or process_owner_uid(data["pid"]) != os.getuid():
            raise ValueError
        if os.name == "nt":
            import psutil
            try:
                if psutil.Process(identity.pid).create_time() > info.st_mtime:
                    raise ValueError
            except psutil.Error:
                raise ValueError from None
        connection = DshConnection(data.get("origin"), data.get("token"))
        if not connection.origin.endswith(":" + str(data["port"])) or process_identity(identity.pid) != identity:
            raise ValueError
        return connection
    except (OSError, ValueError, TypeError, KeyError):
        raise DshError("desktop_invalid", "桌面版 DSH 配对信息不可用，请确认桌面版仍在运行，且配对文件仅当前用户可读写。") from None
