"""Async and timed tasks reach the daemon through the official proxy on Windows.

A fake daemon (WebSocket server) and a fake `codex app-server proxy` (a byte
pump between stdio and that server) stand in for the real ones.
"""
import asyncio
import json
import os
import subprocess
import sys
import textwrap

import pytest

from cc_remote import async_tasks, task_rpc, timed_tasks
from cc_remote.task_store import TaskStore

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="native Windows proxy transport")

FAKE_PROXY = textwrap.dedent("""
    import os, socket, sys, threading
    port, pid_file = int(sys.argv[1]), sys.argv[2]
    open(pid_file, "w").write(str(os.getpid()))
    with socket.create_connection(("127.0.0.1", port)) as daemon:
        def upstream():
            while data := sys.stdin.buffer.read1(65536):
                daemon.sendall(data)
            daemon.shutdown(socket.SHUT_WR)
        threading.Thread(target=upstream, daemon=True).start()
        while data := daemon.recv(65536):
            sys.stdout.buffer.write(data)
            sys.stdout.buffer.flush()
""")


@pytest.fixture
def fake_daemon(tmp_path, monkeypatch):
    from websockets.asyncio.server import serve

    calls = []

    async def handler(ws):
        assert ws.request.path == "/rpc"
        async for raw in ws:
            message = json.loads(raw)
            calls.append(message["method"])
            if "id" not in message:
                continue
            result = {"thread": {"status": {"type": "idle"}}}
            if message["method"] == "thread/queue/add":
                result = {"queuedSubmission": {
                    "id": "queue-id", "clientUserMessageId": message["params"]["clientUserMessageId"]}}
            await ws.send(json.dumps({"id": message["id"], "result": result}))

    script = tmp_path / "fake_proxy.py"
    script.write_text(FAKE_PROXY, encoding="utf-8")
    pid_file = tmp_path / "proxy.pid"
    state = {}

    async def start():
        state["server"] = await serve(handler, "127.0.0.1", 0)
        port = state["server"].sockets[0].getsockname()[1]
        monkeypatch.setattr(task_rpc, "_proxy_argv",
                            lambda _bin, path: [sys.executable, str(script), str(port), str(pid_file)])

    from cc_remote.wrapper import codex_daemon, codex_runtime
    monkeypatch.setattr(codex_runtime, "resolve_codex_bin", lambda: sys.executable)
    monkeypatch.setattr(task_rpc, "socket_identity", lambda _path: (1, 2, 3))
    monkeypatch.setattr(codex_daemon, "socket_identity", lambda _path: (1, 2, 3))
    return start, state, calls, pid_file


def _gone(pid):
    import psutil
    return not psutil.pid_exists(pid) or psutil.Process(pid).status() == psutil.STATUS_ZOMBIE


def test_async_task_connection_round_trips_and_stops_its_proxy(tmp_path, fake_daemon):
    start, state, calls, pid_file = fake_daemon
    home = tmp_path / "account"
    home.mkdir()

    async def run():
        await start()
        try:
            async with task_rpc.connect(home) as client:
                result = await client.rpc("thread/read", {"threadId": "t", "includeTurns": False})
                assert result["thread"]["status"]["type"] == "idle"
        finally:
            state["server"].close()
            await state["server"].wait_closed()

    asyncio.run(asyncio.wait_for(run(), 60))
    assert calls[:3] == ["initialize", "initialized", "thread/read"]
    assert _gone(int(pid_file.read_text()))


def test_timed_message_is_queued_through_the_proxy(tmp_path, fake_daemon, monkeypatch):
    start, state, calls, _pid_file = fake_daemon
    home = tmp_path / "codex"
    home.mkdir()
    store = timed_tasks.TimedTaskStore(tmp_path / "state")
    task_id = store.create(str(home), "00000000-0000-4000-8000-000000000001", "t", "hello", 60, 60, 1)
    delivery = store.begin_delivery(task_id)

    async def run():
        await start()
        try:
            await timed_tasks.deliver(store.get(task_id), delivery, lambda: store.accepted(task_id, delivery))
        finally:
            state["server"].close()
            await state["server"].wait_closed()

    asyncio.run(asyncio.wait_for(run(), 60))
    assert calls == ["initialize", "initialized", "thread/read", "thread/queue/add",
                     "thread/read", "thread/queue/start"]
    assert store.get(task_id)["sent"] == 1


def test_worker_launch_does_not_pass_the_lock_on_windows(tmp_path, monkeypatch):
    home = tmp_path / "account"
    home.mkdir()
    store = TaskStore(home)
    launched = []

    def popen(command, **kwargs):
        launched.append((command, kwargs))

    monkeypatch.setattr(async_tasks.subprocess, "Popen", popen)
    async_tasks.ensure_worker(store)
    (command, kwargs), = launched
    assert "--lock-fd" not in command and "pass_fds" not in kwargs
    assert kwargs["creationflags"] & subprocess.CREATE_NO_WINDOW
    # The child takes the released lock itself.
    fd = async_tasks.lock_worker(store)
    assert fd is not None
    os.close(fd)
