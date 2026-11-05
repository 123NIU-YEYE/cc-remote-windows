"""Keep native sharing on authenticated local routes without model turns."""
import asyncio
import os
import sys

import pytest

from cc_remote.codex_windows_shared import websocket_upgrade
from cc_remote import codex_windows_shared as desktop
from cc_remote.wrapper.process_scan import ProcessIdentity
from cc_remote.wrapper.dsh_client import DshClient, DshConnection, DshError


@pytest.mark.parametrize("version", ["0.2.1-rc.2", "0.2.1-rc.5"])
def test_verified_dsh_versions_keep_the_same_metadata_contract(version):
    client = DshClient(DshConnection("http://127.0.0.1:3080", "a" * 43))

    async def route(_route, params):
        return {"contract": 2, "version": version, "skills": [], "commands": [],
                "sessionId": params.get("sessionId")}

    client.read_route = route
    assert asyncio.run(client.draft_catalog("/workspace"))["version"] == version
    assert asyncio.run(client.metadata("dsh@test"))["version"] == version


@pytest.mark.parametrize("contract,version", [
    (1, "0.2.1-rc.5"), (2, "0.2.2"), (2, "0.2.1-rc.99"), (2, []), (2, {}), (2, None),
])
def test_unverified_dsh_contracts_are_rejected(contract, version):
    client = DshClient(DshConnection("http://127.0.0.1:3080", "a" * 43))

    async def route(*_args):
        return {"contract": contract, "version": version}

    client.read_route = route
    with pytest.raises(DshError):
        asyncio.run(client.draft_catalog("/workspace"))


@pytest.mark.parametrize("connection", ["Upgrade", "keep-alive,Upgrade", "keep-alive, Upgrade"])
def test_native_desktop_bridge_accepts_only_root_websocket_upgrades(connection):
    header = (f"GET / HTTP/1.1\r\nHost: 127.0.0.1:8766\r\nUpgrade: websocket\r\n"
              f"Connection: {connection}\r\nSec-WebSocket-Version: 13\r\n"
              "Sec-WebSocket-Key: YWJjZGVmZ2hpamtsbW5vcA==\r\n\r\n").encode()
    assert websocket_upgrade(header, 8766)
    assert not websocket_upgrade(header.replace(b"GET / ", b"GET /other "), 8766)
    assert not websocket_upgrade(header.replace(b"127.0.0.1:8766", b"127.0.0.1:9999"), 8766)
    assert not websocket_upgrade(header.replace(b"\r\n\r\n", b"\r\nOrigin: null\r\n\r\n"), 8766)
    assert not websocket_upgrade(header.replace(b"\r\n\r\n", b"\r\nOrigin: https://example.com\r\n\r\n"), 8766)
    assert not websocket_upgrade(header.replace(b"\r\n\r\n", b"\r\nHost: 127.0.0.1:8766\r\n\r\n"), 8766)


@pytest.mark.parametrize("case", ["valid", "foreign_process", "account", "owner", "daemon", "pid_type"])
def test_desktop_bridge_reuse_requires_actual_process_and_account(tmp_path, monkeypatch, case):
    home = tmp_path.resolve()
    identity = ProcessIdentity(1234, 10)
    monkeypatch.setattr(desktop, "process_identity", lambda _: identity)
    monkeypatch.setattr(desktop, "process_owner_uid", lambda _: None if case == "owner" else os.getuid())
    monkeypatch.setattr(desktop, "_managed_daemon_process_identity", lambda _: ProcessIdentity(5678, 20))
    args = (sys.executable, "-m", "cc_remote.codex_windows_shared", "serve",
            "--home", str(home), "--port", "8766")
    monkeypatch.setattr(desktop, "process_command_environment_value", lambda *_: (
        True, tuple(os.fsencode(a) for a in (("unrelated.exe",) if case == "foreign_process" else args)),
        str(home / "other") if case == "account" else str(home)))
    value = {"schema": 1, "pid": "1234" if case == "pid_type" else 1234,
             "profile": str(home), "daemon_pid": 9999 if case == "daemon" else 5678}
    assert desktop.bridge_matches(value, home, 8766) == (case == "valid")
