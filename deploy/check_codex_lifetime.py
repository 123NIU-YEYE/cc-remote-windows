"""Refuse to stop a Wrapper whose cgroup still contains a native Codex daemon.

Older releases started detached daemons inside the Wrapper's systemd cgroup.
Never silently kill those sessions while installing the isolated launcher.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cc_remote.wrapper.codex_service import process_cgroup
from cc_remote.wrapper.process_scan import process_command, process_identity


def inherited_daemons(group: str, uid: int, proc_root: Path = Path("/proc")) -> list[int]:
    if not group.startswith("/") or group == "/" or ".." in group.split("/"):
        raise ValueError("invalid Wrapper control group")
    found = []
    for directory in proc_root.iterdir():
        if not directory.name.isdecimal():
            continue
        try:
            if directory.stat().st_uid != uid:
                continue
            current = process_cgroup(directory.name, proc_root)
            if current != group and not current.startswith(group + "/"):
                continue
            identity = process_identity(int(directory.name), proc_root=str(proc_root))
            if identity is None:
                if directory.exists():
                    raise RuntimeError("Cannot inspect a Wrapper child")
                continue
            args = process_command(identity, proc_root=str(proc_root))
            if args is None:
                raise RuntimeError("Wrapper child changed during inspection; retry")
            if not args or Path(os.fsdecode(args[0])).name != "codex":
                continue
            # Proxy and private stdio children belong to Wrapper. A native
            # listener or daemon supervisor has an independent lifetime.
            native = any(
                arg == b"app-server" and (
                    args[i + 1:i + 3] == (b"daemon", b"pid-update-loop")
                    or any(a == b"--listen" or a.startswith(b"--listen=")
                           for a in args[i + 1:])
                    or b"--remote-control" in args[i + 1:]
                ) for i, arg in enumerate(args)
            )
            if native:
                found.append(identity.pid)
        except FileNotFoundError:
            continue  # A process exited before we could inspect it.
    return sorted(found)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--unit", required=True)
    parser.add_argument("--uid", required=True, type=int)
    args = parser.parse_args()
    try:
        group = subprocess.check_output(
            ["systemctl", "show", args.unit, "--property=ControlGroup", "--value"],
            text=True, timeout=10,
        ).strip()
        if not group:
            return  # An inactive service has no processes to stop.
        blocked = inherited_daemons(group, args.uid)
        if blocked:
            raise RuntimeError(
                "Shared Codex daemons still belong to the Wrapper service "
                f"(PIDs: {', '.join(map(str, blocked))}). Wrapper remains running. "
                "Wait for native tasks to finish, then restart each affected "
                "account's official daemon from an independent login terminal "
                "and retry the upgrade. Do not stop an active daemon."
            )
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        raise SystemExit(str(exc)) from None


if __name__ == "__main__":
    main()
