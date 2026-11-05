"""An upgrade must exclude launches through the real check/stop boundary."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import json
import os
from pathlib import Path
import shlex
import socket
import subprocess
import sys
import threading

import pytest

from cc_remote.wrapper import codex_daemon, codex_launch_barrier as launch
from cc_remote.wrapper.codex_service import DaemonIsolationError
from cc_remote.wrapper.process_scan import ProcessIdentity
from deploy import check_codex_lifetime as guard


@pytest.fixture
def upgrade(monkeypatch, tmp_path):
    barrier = launch.LaunchBarrier()
    unit = guard.UnitProcess(42, "/system.slice/wrapper.service", "invocation")
    identity = ProcessIdentity(42, 100)
    receipt = tmp_path / "attempted.json"
    calls = []
    monkeypatch.setattr(codex_daemon, "barrier", barrier)
    monkeypatch.setattr(codex_daemon, "needs_isolation", lambda: False)
    monkeypatch.setattr(guard, "unit_process", lambda _: unit)
    monkeypatch.setattr(guard, "process_identity", lambda _: identity)
    monkeypatch.setattr(guard, "inherited_daemons", lambda *_: [])
    monkeypatch.setattr(launch, "request", lambda _identity, _uid, command: barrier.request(command))

    def execute(argv, **kwargs):
        calls.append(argv)
        if argv[:2] == ["systemctl", "stop"]:
            monkeypatch.setattr(guard, "unit_process", lambda _: None)
        return subprocess.CompletedProcess(argv, 0, b"{}", b"")

    monkeypatch.setattr(subprocess, "run", execute)
    return barrier, receipt, calls


@pytest.mark.parametrize("operation", ["start", "restart", "bootstrap", "enable-remote-control", "stop"])
def test_launch_arriving_after_scan_cannot_cross_stop(monkeypatch, tmp_path, upgrade, operation):
    barrier, receipt, calls = upgrade
    attempts = []

    def stop(argv, **kwargs):
        assert argv == ["systemctl", "stop", "wrapper.service"]
        assert json.loads(receipt.read_text())["pid"] == 42
        # This is the exact former race window: the scan succeeded, but stop
        # has not happened. A different request thread now tries to launch.
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(codex_daemon._run_command,
                                 ("codex", "app-server", "daemon", operation),
                                 {"HOME": str(tmp_path)}, 1)
            with pytest.raises(DaemonIsolationError, match="paused"):
                future.result(timeout=2)
        attempts.append(operation)
        monkeypatch.setattr(guard, "unit_process", lambda _: None)

    monkeypatch.setattr(subprocess, "run", stop)
    guard.guarded_stop("wrapper.service", os.getuid(), receipt)
    assert attempts == [operation] and calls == []
    assert barrier.request(b"resume") == b"committed"


def test_in_flight_launch_refuses_upgrade_without_stopping(monkeypatch, tmp_path, upgrade):
    barrier, receipt, calls = upgrade
    started, finish = threading.Event(), threading.Event()

    def execute(argv, **kwargs):
        started.set()
        assert finish.wait(3)
        return subprocess.CompletedProcess(argv, 0, b"{}", b"")

    monkeypatch.setattr(subprocess, "run", execute)
    monkeypatch.setattr(guard, "inherited_daemons", lambda *_: pytest.fail("must quiesce before scanning"))
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(codex_daemon._run_command, ("codex", "app-server", "daemon", "start"),
                             {"HOME": str(tmp_path)}, 1)
        try:
            assert started.wait(2)
            with pytest.raises(RuntimeError, match="in-flight"):
                guard.guarded_stop("wrapper.service", os.getuid(), receipt)
            assert not receipt.exists() and calls == []
        finally:
            finish.set()
            future.result(timeout=2)
    assert barrier.request(b"quiesce") == b"ready"


@pytest.mark.parametrize("failure", ["legacy", "inherited", "generation", "lost-quiesce-reply"])
def test_pre_stop_failure_does_not_stop_or_arm_rollback(monkeypatch, upgrade, failure):
    barrier, receipt, calls = upgrade
    if failure == "legacy":
        monkeypatch.setattr(launch, "request", lambda *_: (_ for _ in ()).throw(ConnectionRefusedError()))
    elif failure == "inherited":
        monkeypatch.setattr(guard, "inherited_daemons", lambda *_: [99])
    elif failure == "generation":
        first = guard.unit_process("wrapper.service")
        generations = iter([first, guard.UnitProcess(43, first.group, "new")])
        monkeypatch.setattr(guard, "unit_process", lambda _: next(generations))
    else:
        def dropped(_identity, _uid, command):
            result = barrier.request(command)
            if command == b"quiesce":
                raise TimeoutError()
            return result
        monkeypatch.setattr(launch, "request", dropped)
    with pytest.raises(RuntimeError):
        guard.guarded_stop("wrapper.service", os.getuid(), receipt)
    assert calls == [] and not receipt.exists()
    with barrier.launch():
        pass  # A checked abort reopened the exact process without restarting it.


@pytest.mark.parametrize("failure", ["lost-commit-reply", "receipt-write", "stop-timeout"])
def test_uncertain_stop_keeps_barrier_closed_even_on_next_abort(monkeypatch, upgrade, failure):
    barrier, receipt, calls = upgrade
    if failure == "lost-commit-reply":
        def dropped(_identity, _uid, command):
            result = barrier.request(command)
            if command == b"commit-stop":
                raise TimeoutError()
            return result
        monkeypatch.setattr(launch, "request", dropped)
    elif failure == "receipt-write":
        receipt = receipt / "missing-parent"
    else:
        monkeypatch.setattr(subprocess, "run", lambda *a, **kw:
                            (_ for _ in ()).throw(subprocess.TimeoutExpired(a[0], 90)))
    with pytest.raises((OSError, subprocess.SubprocessError)):
        guard.guarded_stop("wrapper.service", os.getuid(), receipt)
    assert receipt.exists() is (failure == "stop-timeout")
    assert calls == [] and barrier.request(b"resume") == b"committed"
    with pytest.raises(DaemonIsolationError):
        with barrier.launch():
            pytest.fail("uncertain stop reopened launches")
    # A later retry may reject an unrelated check; it must not undo the
    # previous installer's committed exclusion while stop is still uncertain.
    monkeypatch.setattr(launch, "request", lambda _i, _u, command: barrier.request(command))
    monkeypatch.setattr(guard, "inherited_daemons", lambda *_: [99])
    with pytest.raises(RuntimeError):
        guard.guarded_stop("wrapper.service", os.getuid(), receipt)
    assert barrier.request(b"resume") == b"committed"


def test_closed_barrier_still_allows_read_only_native_queries(tmp_path, upgrade):
    barrier, _receipt, calls = upgrade
    assert barrier.request(b"quiesce") == b"ready"
    result = codex_daemon._run_command(("codex", "app-server", "daemon", "version"),
                                       {"HOME": str(tmp_path)}, 1)
    assert result.returncode == 0 and len(calls) == 1


def test_stopped_service_needs_no_barrier(monkeypatch, upgrade):
    _barrier, receipt, calls = upgrade
    monkeypatch.setattr(guard, "unit_process", lambda _: None)
    monkeypatch.setattr(launch, "request", lambda *_: pytest.fail("no live service"))
    guard.guarded_stop("wrapper.service", os.getuid(), receipt)
    assert calls == [] and not receipt.exists()


@pytest.mark.parametrize("state,pid,group,expected", [
    ("active", 42, "/system.slice/wrapper.service", "running"),
    ("inactive", 0, "", "stopped"),
    ("failed", 0, "", "stopped"),
    ("activating", 0, "", "reject"),
    ("deactivating", 42, "/system.slice/wrapper.service", "reject"),
    ("failed", 0, "/system.slice/wrapper.service", "reject"),
])
def test_unit_transitions_and_lingering_children_cannot_bypass_guard(monkeypatch, state, pid, group, expected):
    monkeypatch.setattr(subprocess, "check_output", lambda *a, **kw:
                        f"MainPID={pid}\nControlGroup={group}\nActiveState={state}\nInvocationID=test\n")
    if expected == "reject":
        with pytest.raises(RuntimeError, match="changing state"):
            guard.unit_process("wrapper.service")
    else:
        unit = guard.unit_process("wrapper.service")
        assert (unit is None) is (expected == "stopped")


@pytest.mark.parametrize("recorded", [False, True])
def test_installer_rollback_uses_durable_stop_intent(tmp_path, recorded):
    root = Path(__file__).parents[1]
    receipt, restarted = tmp_path / "attempted.json", tmp_path / "restarted"
    if recorded:
        receipt.write_text('{"pid":42}')
    source = (root / "deploy/install-wrapper.sh").read_text()
    helpers = source[source.index("cleanup() {"):].split('if [ -e "$service_file" ]; then', 1)[0]
    settings = dict(activation_committed="0", snapshot_created="0", switched="0",
                    service_changed="0", device_changed="0", service_stopped="0",
                    retention_generation="", stage="", service_backup="", device_backup="",
                    unit_verify_dir="", codex_stop_receipt=str(receipt))
    script = "set -euo pipefail\n" + "\n".join(
        f"{key}={shlex.quote(value)}" for key, value in settings.items())
    script += "\n" + helpers + f"\nrestart_after_rollback() {{ touch {shlex.quote(str(restarted))}; }}\nexit 42\n"
    result = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=5)
    assert result.returncode == 42, result.stderr
    assert restarted.exists() is recorded


def test_barrier_is_served_before_wrapper_initialization(monkeypatch):
    import asyncio
    from cc_remote.wrapper import __main__ as main
    events = []

    @contextmanager
    def serve():
        events.append("barrier-ready")
        yield
        events.append("barrier-closed")

    async def run():
        assert events == ["barrier-ready"]
        events.append("wrapper")

    monkeypatch.setattr(main, "serve_launch_barrier", serve)
    monkeypatch.setattr(main, "run_wrapper", run)
    asyncio.run(main.main())
    assert events == ["barrier-ready", "wrapper", "barrier-closed"]


@pytest.mark.skipif(sys.platform != "linux", reason="Linux abstract Unix socket and SO_PEERCRED")
def test_linux_peer_verification_and_lost_commit_reply(monkeypatch):
    barrier = launch.LaunchBarrier()
    monkeypatch.setattr(launch, "barrier", barrier)
    monkeypatch.setattr(launch, "needs_isolation", lambda: True)
    identity = launch.process_identity(os.getpid())
    assert identity is not None
    with launch.serve():
        with pytest.raises(RuntimeError, match="identity"):
            launch.request(identity, os.getuid() + 1, b"quiesce")
        assert launch.request(identity, os.getuid(), b"quiesce") == b"ready"
        # Commit without receiving the reply, just as a disconnected installer
        # can do. Subsequent connections must not release the stop barrier.
        with socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET) as connection:
            connection.connect(launch.address(identity))
            connection.sendall(b"commit-stop")
        assert launch.request(identity, os.getuid(), b"resume") == b"committed"
        with pytest.raises(DaemonIsolationError):
            with barrier.launch():
                pytest.fail("disconnect reopened launches")
