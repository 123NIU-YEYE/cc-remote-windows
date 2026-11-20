"""One-level workspace browsing, including Windows' virtual drive list."""
from __future__ import annotations

import ntpath
import os
import re

# Navigation-only location: it cannot be a Windows directory or session cwd.
# ListDir/DirList keep their existing string fields; no wire shape changes.
WINDOWS_DRIVES_PATH = "::drives::"
IS_WINDOWS = os.name == "nt"


def drive_entries() -> list[dict[str, str]]:
    # Get the OS drive inventory without probing roots. Empty removable drives
    # and disconnected mapped drives must not block this navigation page.
    roots = os.listdrives()
    drives = {root[0].upper() + ":\\" for root in roots
              if re.fullmatch(r"[A-Za-z]:[\\/]", root)}
    return [{"name": root, "path": root} for root in sorted(drives)]


def directory_parent(base: str) -> str | None:
    path_module = ntpath if IS_WINDOWS else os.path
    parent = path_module.dirname(base)
    if parent and parent != base:
        return parent
    return WINDOWS_DRIVES_PATH if IS_WINDOWS else None


def scan_directory(path: str | None) -> tuple[str, str | None, list[dict[str, str]]]:
    if path == WINDOWS_DRIVES_PATH:
        if not IS_WINDOWS:
            raise FileNotFoundError("Drive navigation is only available on Windows")
        return WINDOWS_DRIVES_PATH, None, drive_entries()
    base = os.path.realpath(os.path.expanduser(path or "~"))
    if not os.path.isdir(base):
        raise FileNotFoundError(base)
    dirs: list[dict[str, str]] = []
    try:
        with os.scandir(base) as entries:
            for entry in entries:
                if entry.name.startswith("."):
                    continue
                if entry.is_dir(follow_symlinks=True):
                    dirs.append({"name": entry.name[:255], "path": entry.path[:4096]})
                    if len(dirs) >= 1000:
                        break
        dirs.sort(key=lambda item: item["name"])
    except PermissionError:
        pass
    return base, directory_parent(base), dirs
