"""Shared native startup must outlive the disposable Wrapper service."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from cc_remote.wrapper import codex_daemon, codex_service
from deploy import check_codex_lifetime


@pytest.mark.parametrize("group,expected", [
    ("/system.slice/cc-remote-wrapper.service", True),
    ("/system.slice/cc-remote-wrapper.service/child", True),
    ("/user.slice/user-1000.slice/user@1000.service/app.slice/wrapper.service", True),
    ("/user.slice/user-1000.slice/session-4.scope", False),
    ("/user.slice/user-1000.slice/user@1000.service/app.slice/run.scope", False),
    ("/user.slice/user-1000.slice/user@1000.service", False),
    ("/", False),
])
def test_only_disposable_systemd_services_need_isolation(monkeypatch, group, expected):
    monkeypatch.setattr(codex_service.sys, "platform", "linux")
    monkeypatch.setattr(Path, "is_dir", lambda _: True)
    monkeypatch.setattr(codex_service, "process_cgroup", lambda _: group)
    assert codex_service.needs_isolation() is expected


@pytest.mark.parametrize("text,expected", [
    ("0::/system.slice/wrapper.service\n", "/system.slice/wrapper.service"),
    ("2:cpu:/elsewhere\n1:name=systemd:/system.slice/wrapper.service\n",
     "/system.slice/wrapper.service"),
    ("0::/\n1:name=systemd:/system.slice/wrapper.service\n",
     "/system.slice/wrapper.service"),
])
def test_reads_actual_systemd_hierarchy(tmp_path, text, expected):
    (tmp_path / "self").mkdir()
    (tmp_path / "self/cgroup").write_text(text)
    assert codex_service.process_cgroup("self", tmp_path) == expected


def test_unknown_service_hierarchy_never_falls_back(monkeypatch):
    monkeypatch.setattr(codex_service.sys, "platform", "linux")
    monkeypatch.setattr(Path, "is_dir", lambda _: True)
    monkeypatch.setattr(codex_service, "process_cgroup", lambda _: (_ for _ in ()).throw(PermissionError()))
    with pytest.raises(codex_service.DaemonIsolationError):
        codex_service.needs_isolation()


def test_account_environment_is_private_and_exact(monkeypatch, tmp_path):
    env = {"HOME": str(tmp_path), "CODEX_HOME": str(tmp_path / "sibling"),
           "MODEL_TEST_SECRET": "not-a-real-key", "PATH": os.defpath}
    argv = (sys.executable, "-c", "import json,os; print(json.dumps(dict(os.environ)))")
    manager = {"PATH": os.defpath, "MODEL_TEST_OTHER_ACCOUNT": "must-not-leak"}
    monkeypatch.setattr(codex_service.shutil, "which", lambda *a, **k: "/usr/bin/systemd-run")
    monkeypatch.setattr(codex_service, "user_manager_environment", lambda: manager)
    run = subprocess.run

    def launch(command, **kwargs):
        assert "--property=KillMode=process" in command
        assert "--property=LimitCORE=0" in command
        assert "--user" in command and "--pipe" in command
        assert "--wait" in command and "--collect" in command
        assert env["MODEL_TEST_SECRET"] not in str(command)
        assert "MODEL_TEST_SECRET" not in kwargs["env"]
        payload = json.loads(kwargs["input"])
        assert payload == {"argv": list(argv), "env": env, "cwd": str(tmp_path)}
        # Execute the real stdin handoff, without requiring systemd on macOS.
        return run(command[command.index("--") + 1:], **kwargs)

    monkeypatch.setattr(codex_service.subprocess, "run", launch)
    result = codex_service.run_isolated(argv, env, str(tmp_path), 5)
    assert result.returncode == 0
    observed = json.loads(result.stdout)
    assert all(observed[k] == v for k, v in env.items())
    assert "MODEL_TEST_OTHER_ACCOUNT" not in observed


@pytest.mark.parametrize("failure", ["missing-runner", "missing-manager", "timeout"])
def test_isolation_failure_never_launches_directly(monkeypatch, tmp_path, failure):
    calls = []
    monkeypatch.setattr(codex_daemon, "needs_isolation", lambda: True)
    monkeypatch.setattr(codex_service.shutil, "which",
                        lambda *a, **k: None if failure == "missing-runner" else "/usr/bin/systemd-run")

    def environment():
        if failure == "missing-manager":
            raise codex_service.DaemonIsolationError("manager unavailable")
        return {}

    def run(command, **kwargs):
        calls.append(command)
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setattr(codex_service, "user_manager_environment", environment)
    monkeypatch.setattr(codex_service.subprocess, "run", run)
    with pytest.raises(codex_service.DaemonIsolationError):
        codex_daemon._run_command(("codex", "app-server", "daemon", "start"),
                                  {"HOME": str(tmp_path)}, 1)
    assert all(command[0] == "/usr/bin/systemd-run" for command in calls)


def test_read_only_native_commands_stay_local(monkeypatch, tmp_path):
    monkeypatch.setattr(codex_daemon, "needs_isolation", lambda: pytest.fail("read must not launch a service"))
    monkeypatch.setattr(codex_daemon.subprocess, "run", lambda argv, **kw:
                        subprocess.CompletedProcess(argv, 0, b"{}", b""))
    assert codex_daemon._run_command(("codex", "app-server", "daemon", "version"),
                                    {"HOME": str(tmp_path)}, 1).returncode == 0


def _process(root, pid, group, argv):
    directory = root / str(pid)
    directory.mkdir()
    (directory / "cgroup").write_text("0::" + group + "\n")
    (directory / "cmdline").write_bytes(b"\0".join(os.fsencode(arg) for arg in argv) + b"\0")
    # Linux stat fields 3..22; the shared reader uses ppid and starttime.
    fields = ["0"] * 20
    fields[0], fields[1], fields[19] = "S", "1", str(pid * 100)
    (directory / "stat").write_text(f"{pid} (codex) " + " ".join(fields))


def test_guard_blocks_old_listeners_but_not_proxies_private_stdio_or_other_units(tmp_path):
    group = "/system.slice/wrapper.service"
    _process(tmp_path, 10, group, ["/bin/codex", "app-server", "--listen", "unix:///account.sock"])
    _process(tmp_path, 11, group + "/child", ["/bin/codex", "app-server", "daemon", "pid-update-loop"])
    _process(tmp_path, 12, group, ["/bin/codex", "app-server", "proxy", "--sock", "/account.sock"])
    _process(tmp_path, 13, group, ["/bin/codex", "app-server"])
    _process(tmp_path, 14, group + "-other", ["/bin/codex", "app-server", "--remote-control"])
    _process(tmp_path, 15, "/user.slice/launch.service", ["/bin/codex", "app-server", "--remote-control"])
    _process(tmp_path, 16, group, ["/bin/codex", "app-server", "daemon", "version"])
    assert check_codex_lifetime.inherited_daemons(group, os.getuid(), tmp_path) == [10, 11]
    assert check_codex_lifetime.inherited_daemons(group, os.getuid() + 1, tmp_path) == []


def test_guard_refuses_incomplete_inspection(tmp_path):
    group = "/system.slice/wrapper.service"
    _process(tmp_path, 10, group, ["/bin/codex", "app-server"])
    (tmp_path / "10/stat").write_text("unreadable identity")
    with pytest.raises(RuntimeError, match="inspect"):
        check_codex_lifetime.inherited_daemons(group, os.getuid(), tmp_path)


def test_install_guard_runs_before_stop_or_rollback_restart_is_armed():
    installer = (Path(__file__).parents[1] / "deploy/install-wrapper.sh").read_text()
    body = installer.split('if [ "$service_was_running" -eq 1 ]; then\n', 1)[1]
    assert body.index("check_codex_lifetime.py") < body.index("service_stopped=1") < body.index("stop_wrapper_service")


@pytest.mark.asyncio
async def test_readiness_reports_isolation_failure_without_claiming_shared(tmp_path):
    from unittest.mock import AsyncMock
    from cc_remote.wrapper.codex_readiness import check_profile
    manager = AsyncMock()
    manager.ensure_started.side_effect = codex_service.DaemonIsolationError("private diagnostics")
    row = await check_profile("account", str(tmp_path), "/bin/codex", "/bin/codex", {}, manager)
    assert row["status"] == "unavailable" and row["reason"] == "isolation_unavailable"
    assert "private diagnostics" not in str(row)
