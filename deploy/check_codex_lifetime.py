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


_READ_ONLY_DAEMON_COMMANDS = frozenset({b"version", b"status", b"help", b"--help", b"-h"})


def blocks_wrapper_stop(args: tuple[bytes, ...]) -> bool:
    # _run_command first execs Python's child-only resource-limit helper.
    # Inspect its target too: no native listener exists yet in this window.
    if (len(args) >= 4 and Path(os.fsdecode(args[1])).name == "rlimit_exec.py"
            and args[2].isdigit()):
        args = args[3:]
    if not args or Path(os.fsdecode(args[0])).name != "codex":
        return False
    for i, arg in enumerate(args):
        if arg != b"app-server":
            continue
        tail = args[i + 1:]
        if tail[:1] == (b"daemon",):
            # Include every mutating lifecycle command and the supervisor,
            # rather than only the resulting listener. Unknown operations
            # block conservatively so future launch commands cannot bypass it.
            if len(tail) > 1 and tail[1] not in _READ_ONLY_DAEMON_COMMANDS:
                return True
        elif (any(a == b"--listen" or a.startswith(b"--listen=") for a in tail)
              or b"--remote-control" in tail):
            return True
    return False


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
            if blocks_wrapper_stop(args):
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
                "Shared Codex daemons or lifecycle commands still belong to the Wrapper service "
                f"(PIDs: {', '.join(map(str, blocked))}). Wrapper remains running. "
                "Wait for lifecycle commands to finish and retry. If a daemon "
                "remains in this cgroup, wait for native tasks to finish, then restart each affected "
                "account's official daemon from an independent login terminal "
                "and retry the upgrade. Do not stop an active daemon."
            )
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        raise SystemExit(str(exc)) from None


if __name__ == "__main__":
    main()
