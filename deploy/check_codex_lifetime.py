"""Refuse to stop a Wrapper whose cgroup still contains a native Codex daemon.

Older releases started detached daemons inside the Wrapper's systemd cgroup.
Never silently kill those sessions while installing the isolated launcher.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import os
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cc_remote.wrapper.codex_service import process_cgroup
from cc_remote.wrapper import codex_launch_barrier
from cc_remote.wrapper.process_scan import process_command, process_identity


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
            if len(tail) > 1 and tail[1] not in codex_launch_barrier.READ_ONLY_DAEMON_COMMANDS:
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


@dataclass(frozen=True)
class UnitProcess:
    pid: int
    group: str
    invocation: str


def unit_process(unit: str) -> UnitProcess | None:
    output = subprocess.check_output([
        "systemctl", "show", unit, "--property=MainPID", "--property=ControlGroup",
        "--property=InvocationID", "--property=ActiveState",
    ], text=True, timeout=10)
    fields = dict(line.split("=", 1) for line in output.splitlines() if "=" in line)
    pid, group = int(fields["MainPID"]), fields["ControlGroup"]
    if not pid and not group and fields["ActiveState"] in {"inactive", "failed"}:
        return None
    if pid <= 0 or not group or not fields["InvocationID"] or fields["ActiveState"] != "active":
        raise RuntimeError("Wrapper service is changing state; retry the upgrade")
    return UnitProcess(pid, group, fields["InvocationID"])


def guarded_stop(unit: str, uid: int, receipt: Path) -> None:
    """Keep launches excluded from before the scan through the completed stop."""
    original = unit_process(unit)
    if original is None:
        return
    identity = process_identity(original.pid)
    if identity is None:
        raise RuntimeError("Cannot identify the running Wrapper")
    stop_attempted = False
    try:
        try:
            response = codex_launch_barrier.request(identity, uid, b"quiesce")
        except OSError as exc:
            raise RuntimeError(
                "Running Wrapper cannot acknowledge launch quiescence. No service was stopped. "
                "For an older release, wait for native tasks to finish, stop the old Wrapper "
                "during a maintenance window, then retry. A process scan alone is not safe."
            ) from exc
        if response != b"ready":
            raise RuntimeError("Wrapper has an in-flight Codex launch; wait and retry")
        blocked = inherited_daemons(original.group, uid)
        if blocked:
            raise RuntimeError(f"Wrapper still owns native Codex processes (PIDs: {blocked}); migrate them before upgrading")
        if unit_process(unit) != original or process_identity(original.pid) != identity:
            raise RuntimeError("Wrapper changed after quiescence; no stop was requested")
        # Persist intent before stop. A lost response must arm rollback recovery,
        # while a rejected check must never restart the untouched old service.
        stop_attempted = True
        if codex_launch_barrier.request(identity, uid, b"commit-stop") != b"committed":
            raise RuntimeError("Wrapper did not commit launch exclusion; no stop was requested")
        with receipt.open("x") as output:
            json.dump({"pid": original.pid, "start_ticks": identity.start_ticks,
                       "invocation": original.invocation, "group": original.group}, output)
            output.flush()
            os.fsync(output.fileno())
        subprocess.run(["systemctl", "stop", unit], check=True, timeout=90)
        if unit_process(unit) is not None:
            raise RuntimeError("Wrapper stop did not complete; launch barrier remains closed")
    finally:
        if not stop_attempted:
            # A checked abort can reopen this exact process. After requesting
            # stop (even on timeout), keep quiescence sticky for recovery.
            try:
                codex_launch_barrier.request(identity, uid, b"resume")
            except (OSError, RuntimeError):
                pass


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--unit", required=True)
    parser.add_argument("--uid", required=True, type=int)
    parser.add_argument("--stop-receipt", type=Path,
                        help="quiesce, inspect and stop; record stop intent at this private path")
    args = parser.parse_args()
    try:
        if args.stop_receipt is not None:
            guarded_stop(args.unit, args.uid, args.stop_receipt)
            return
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
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError) as exc:
        raise SystemExit(str(exc)) from None


if __name__ == "__main__":
    main()
