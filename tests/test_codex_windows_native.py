"""Windows native daemon routing and account/generation fences; no model calls."""
import asyncio
import json
import os
import sys
from types import SimpleNamespace

import pytest

from cc_remote.wrapper import codex_daemon as module
from cc_remote.wrapper.process_scan import ProcessIdentity


def windows(monkeypatch):
    # Replace module references, not global os.name/Path's platform selection.
    monkeypatch.setattr(module, "os", SimpleNamespace(**{**vars(os), "name": "nt"}))
    monkeypatch.setattr(module, "sys", SimpleNamespace(**{**vars(sys), "platform": "win32"}))


@pytest.mark.parametrize("change", ["none", "generation", "account", "owner"])
def test_windows_pid_record_is_bound_to_generation_account_and_user(tmp_path, monkeypatch, change):
    windows(monkeypatch)
    home = tmp_path.resolve()
    state = home / "app-server-daemon"
    state.mkdir()
    started = 1700000000123
    (state / "daemon.pid").write_text(json.dumps({
        "pid": 4321,
        "processStartTime": str((started + 11644473600000) * 10000),
    }), encoding="utf-8")
    monkeypatch.setattr(module, "process_identity", lambda _: ProcessIdentity(
        4321, started + (20 if change == "generation" else 0)))
    monkeypatch.setattr(module, "process_owner_uid", lambda _: (
        None if change == "owner" else os.getuid()))
    monkeypatch.setattr(module, "process_command_environment_value", lambda *_: (
        True, (b"codex.exe", b"app-server"), str(home / "other" if change == "account" else home)))
    assert (module._managed_daemon_process_identity(home) is not None) == (change == "none")


def test_windows_native_auto_reuses_daemon_and_official_proxy(tmp_path, monkeypatch):
    windows(monkeypatch)
    calls = []
    identity = ProcessIdentity(4321, 123)
    monkeypatch.setattr(module, "_managed_daemon_process_identity", lambda _: identity)
    monkeypatch.setattr(module, "_ensure_managed_daemon_nofile", lambda *_args, **_kw: None)
    manager = module.CodexDaemonManager("auto", allow_restart=False, require_shared=True)

    async def run(binary, env, *args):
        calls.append(args)
        result = {"status": "running", "cliVersion": "0.158.0", "appServerVersion": "0.158.0",
                  "managedCodexVersion": "0.158.0", "managedCodexPath": str(tmp_path / "codex.exe"),
                  "socketPath": str(tmp_path / "app-server-control/app-server-control.sock")}
        return module._CommandResult(0, json.dumps(result).encode(), b"")

    manager._run = run
    argv = asyncio.run(manager.proxy_args(str(tmp_path / "codex.exe"), {"CODEX_HOME": str(tmp_path)}))
    assert argv == [str(tmp_path / "codex.exe"), "app-server", "proxy", "--sock",
                    str(tmp_path / "app-server-control/app-server-control.sock")]
    assert manager.current_process_identity() == identity
    assert calls == [("app-server", "daemon", "--help"), ("app-server", "proxy", "--help"),
                     ("app-server", "daemon", "version")]
    assert manager.strict_shared_affinity


def test_windows_native_auto_does_not_silently_start_stdio(monkeypatch):
    windows(monkeypatch)
    manager = module.CodexDaemonManager("auto", allow_restart=False, require_shared=True)

    async def unavailable(*_args):
        return module._CommandResult(1, b"", b"unavailable")

    manager._run = unavailable
    with pytest.raises(module.CodexProfileDaemonUnavailable):
        asyncio.run(manager.proxy_args("codex.exe", {}))
