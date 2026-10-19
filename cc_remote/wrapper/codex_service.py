"""Isolate native Codex startup from a disposable systemd Wrapper service.

The official CLI still owns the daemon. A transient user service only launches
that CLI, retaining its deliberately detached children after the CLI exits.
The Wrapper's own process cleanup policy is never weakened.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import uuid
from typing import Mapping


class DaemonIsolationError(RuntimeError):
    """Never fall back to starting a shared daemon inside the Wrapper."""


def process_cgroup(pid: str | int, proc_root: Path = Path("/proc")) -> str:
    """Read the unified or legacy systemd hierarchy without guessing a unit."""
    text = (proc_root / str(pid) / "cgroup").read_text()
    paths = {}
    for line in text.splitlines():
        hierarchy, controllers, path = line.split(":", 2)
        if hierarchy == "0" and not controllers:
            paths["unified"] = path
        if "name=systemd" in controllers.split(","):
            paths["legacy"] = path
    # Hybrid hosts can expose an unrelated unified root alongside the legacy
    # hierarchy that actually owns the service's lifecycle.
    path = paths.get("legacy", paths.get("unified"))
    if path is None:
        raise ValueError("systemd cgroup unavailable")
    if not path.startswith("/") or ".." in path.split("/"):
        raise ValueError("invalid systemd cgroup")
    return path.rstrip("/") or "/"


def needs_isolation() -> bool:
    if sys.platform != "linux" or not Path("/run/systemd/system").is_dir():
        return False
    try:
        group = process_cgroup("self")
    except (OSError, ValueError) as exc:
        raise DaemonIsolationError("Cannot verify Codex launcher lifetime") from exc
    for part in reversed(group.split("/")):
        if part.endswith(".scope"):
            return False
        if part.endswith(".service"):
            return not part.startswith("user@")
    return False


def user_manager_environment() -> dict[str, str]:
    runtime = Path("/run/user") / str(os.geteuid())
    try:
        directory, bus = runtime.lstat(), (runtime / "bus").lstat()
        if (not stat.S_ISDIR(directory.st_mode) or directory.st_uid != os.geteuid()
                or not stat.S_ISSOCK(bus.st_mode) or bus.st_uid != os.geteuid()):
            raise ValueError("invalid user manager endpoint")
    except (OSError, ValueError) as exc:
        raise DaemonIsolationError(
            "Codex needs the service user's systemd manager; enable lingering "
            "or start the official daemon from an independent login first"
        ) from exc
    # Account environment goes over stdin, not argv, a systemd property, a
    # temporary file, or the user manager's shared environment.
    return {"PATH": os.defpath, "XDG_RUNTIME_DIR": str(runtime),
            "DBUS_SESSION_BUS_ADDRESS": f"unix:path={runtime}/bus"}


_EXEC = (
    "import json,os,sys; p=json.load(sys.stdin); os.chdir(p['cwd']); "
    "os.execvpe(p['argv'][0],p['argv'],p['env'])"
)


def run_isolated(argv: tuple[str, ...], env: Mapping[str, str], cwd: str,
                 timeout: float) -> subprocess.CompletedProcess:
    runner = shutil.which("systemd-run", path=os.defpath)
    if runner is None:
        raise DaemonIsolationError("systemd-run is required for shared Codex startup")
    manager_env = user_manager_environment()
    payload = json.dumps({"argv": argv, "env": dict(env), "cwd": cwd}).encode()
    if len(payload) > 1024 * 1024:
        raise DaemonIsolationError("Codex launch environment exceeds limit")
    try:
        return subprocess.run(
            (runner, "--user", "--quiet", "--pipe", "--wait", "--collect",
             "--service-type=exec", "--property=KillMode=process",
             "--property=UMask=0077", "--property=NoNewPrivileges=yes",
             "--property=LimitCORE=0",
             "--unit=cc-remote-codex-launch-" + uuid.uuid4().hex,
             "--", sys.executable, "-I", "-c", _EXEC),
            input=payload, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=manager_env, cwd=cwd, timeout=timeout, check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        # A lost response is not permission to launch directly in the Wrapper.
        # The next request must probe native state before attempting startup.
        raise DaemonIsolationError("Isolated Codex launch could not be confirmed") from exc
