"""Desktop sharing requires live identity-bound connections, not history files."""
from types import SimpleNamespace

import pytest

from cc_remote import codex_windows_shared as shared
from cc_remote.wrapper.process_scan import ProcessIdentity


@pytest.mark.parametrize("failure", [None, "home", "url", "reverse", "disconnected", "reused", "daemon"])
def test_app_acceptance_requires_same_profile_and_both_live_connection_ends(tmp_path, monkeypatch, failure):
    psutil = pytest.importorskip("psutil", reason="Windows Desktop bridge dependency")
    home = tmp_path.resolve()
    executable = (home / "Official.exe").resolve()
    value = {"pid": 20, "daemon_pid": 30}
    health_calls = []

    def health(_port):
        health_calls.append(True)
        return {**value, "daemon_pid": 31} if failure == "daemon" and len(health_calls) > 1 else value

    monkeypatch.setattr(shared, "bridge_health", health)
    monkeypatch.setattr(shared, "bridge_matches", lambda *_args: True)
    identity = ProcessIdentity(10, 100)
    identities = []

    def process_identity(_pid):
        identities.append(True)
        return ProcessIdentity(10, 200) if failure == "reused" and len(identities) > 1 else identity

    monkeypatch.setattr(shared, "process_identity", process_identity)
    env = {"CODEX_HOME": str(home / "other" if failure == "home" else home),
           "CODEX_APP_SERVER_WS_URL": "ws://127.0.0.1:9999" if failure == "url" else "ws://127.0.0.1:8766"}
    monkeypatch.setattr(shared, "process_command_environment_value", lambda _identity, key: (True, (), env[key]))
    monkeypatch.setattr(psutil, "Process", lambda: SimpleNamespace(username=lambda: "user"))
    monkeypatch.setattr(psutil, "process_iter", lambda _fields: [
        SimpleNamespace(pid=10, info={"exe": str(executable), "username": "user"})])
    local, remote = SimpleNamespace(ip="127.0.0.1", port=5000), SimpleNamespace(ip="127.0.0.1", port=8766)
    connections = [SimpleNamespace(pid=10, laddr=local, raddr=remote, status=psutil.CONN_ESTABLISHED)]
    if failure != "reverse":
        connections.append(SimpleNamespace(pid=20, laddr=remote, raddr=local, status=psutil.CONN_ESTABLISHED))
    monkeypatch.setattr(psutil, "net_connections", lambda **_kwargs: [] if failure == "disconnected" else connections)
    result = shared.app_acceptance(home, 8766, executable)
    assert result["state"] == ("verified" if failure is None else "awaiting_reopen")
    assert result["daemon_pid"] == 30


@pytest.mark.parametrize("extra", ["Origin: https://example.com\r\n", "Host: evil\r\n"])
def test_bridge_rejects_browser_origins_and_duplicate_headers(extra):
    header = ("GET / HTTP/1.1\r\nHost: 127.0.0.1:8766\r\nUpgrade: websocket\r\n"
              "Connection: Upgrade\r\nSec-WebSocket-Version: 13\r\nSec-WebSocket-Key: test\r\n")
    assert shared.websocket_upgrade((header + "\r\n").encode(), 8766)
    assert not shared.websocket_upgrade((header + extra + "\r\n").encode(), 8766)
