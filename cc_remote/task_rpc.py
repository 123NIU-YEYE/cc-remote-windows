"""Async tasks use only the selected account's existing official daemon."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, suppress
import json
from pathlib import Path
import socket
import subprocess
import sys

from websockets.asyncio.client import connect as websocket_connect, unix_connect
from websockets.protocol import State

from cc_remote.task_store import parse_task_receipt
from cc_remote.wrapper.codex_daemon import socket_identity

_PROXY_EXIT_TIMEOUT = 2


def _proxy_argv(codex_bin: str, path: Path) -> list[str]:
    return [codex_bin, "app-server", "proxy", "--sock", str(path)]


@asynccontextmanager
async def _proxy_socket(path: Path):
    """A local socket carrying the official proxy's stdio to the daemon.

    Python cannot connect to the daemon's AF_UNIX socket on Windows. The
    official ``codex app-server proxy`` connects instead; it forwards raw
    bytes, so the WebSocket handshake still reaches the daemon itself.
    """
    from cc_remote.wrapper.codex_runtime import codex_env, resolve_codex_bin
    from cc_remote.wrapper.os_compat import kill_process_tree

    binary = resolve_codex_bin()
    process = await asyncio.create_subprocess_exec(
        *_proxy_argv(binary, path), stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        env=codex_env(binary, str(path.parent.parent)),
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    ours, theirs = socket.socketpair()
    ours.setblocking(False)
    loop = asyncio.get_running_loop()

    async def to_proxy():
        while data := await loop.sock_recv(ours, 65536):
            process.stdin.write(data)
            await process.stdin.drain()
        process.stdin.close()

    async def from_proxy():
        while data := await process.stdout.read(65536):
            await loop.sock_sendall(ours, data)
        ours.shutdown(socket.SHUT_WR)

    pumps = [asyncio.create_task(to_proxy()), asyncio.create_task(from_proxy())]
    try:
        yield theirs
    finally:
        theirs.close()
        with suppress(Exception):
            await asyncio.wait_for(asyncio.shield(pumps[0]), _PROXY_EXIT_TIMEOUT)
        if process.stdin is not None and not process.stdin.is_closing():
            process.stdin.close()
        try:
            await asyncio.wait_for(process.wait(), _PROXY_EXIT_TIMEOUT)
        except TimeoutError:
            # codex.cmd runs the proxy under cmd.exe; stop the whole tree.
            await asyncio.to_thread(kill_process_tree, process.pid)
            await process.wait()
        for pump in pumps:
            pump.cancel()
        await asyncio.gather(*pumps, return_exceptions=True)
        ours.close()


@asynccontextmanager
async def daemon_connection(path: Path, **options):
    """Open a WebSocket to an account daemon's control socket."""
    if sys.platform != "win32":
        async with unix_connect(str(path), uri="ws://localhost/rpc", **options) as ws:
            yield ws
        return
    async with _proxy_socket(path) as sock:
        async with websocket_connect("ws://localhost/rpc", sock=sock, **options) as ws:
            yield ws


class Rejected(RuntimeError):
    """An explicit native rejection with a sanitized retry classification."""

    def __init__(self, message, *, error=None):
        super().__init__(message)
        self.retryable = False
        if isinstance(error, dict) and type(error.get("code")) is int:
            code, data = error["code"], error.get("data")
            # Overload rejects before dispatch. Draining returns NotSubmitted.
            self.retryable = code == -32001 or (
                code == -32600 and isinstance(data, dict)
                and data.get("reason") == "serverShuttingDown"
            )


class NativeTasks:
    def __init__(self, ws, on_output=None):
        self.ws = ws
        self.on_output = on_output
        self.pending: dict[int, asyncio.Future] = {}
        self.sequence = 0
        self.reader = asyncio.create_task(self.read())

    async def read(self):
        try:
            async for raw in self.ws:
                msg = json.loads(raw)
                if not isinstance(msg, dict):
                    continue
                if "method" in msg:
                    if "id" in msg:
                        # This client never grants approvals on behalf of a user.
                        await self.ws.send(json.dumps({"id": msg["id"], "error": {
                            "code": -32601, "message": "Unsupported task client request"}}))
                    elif msg["method"] == "command/exec/outputDelta" and self.on_output:
                        self.on_output(msg.get("params", {}))
                    continue
                future = self.pending.get(msg.get("id"))
                if future is not None and not future.done():
                    if "error" in msg:
                        future.set_exception(Rejected(
                            "Official daemon rejected the operation", error=msg["error"]))
                    else:
                        future.set_result(msg.get("result"))
        finally:
            for future in self.pending.values():
                if not future.done():
                    future.set_exception(ConnectionError("Official daemon connection lost"))

    async def rpc(self, method, params, *, timeout=20, before_send=None):
        # A known closed socket has not taken this request. Check before the
        # caller records that a write might have reached the daemon.
        if self.reader.done() or self.ws.state is not State.OPEN:
            raise ConnectionError("Official daemon connection closed")
        self.sequence += 1
        key = self.sequence
        future = asyncio.get_running_loop().create_future()
        self.pending[key] = future
        try:
            if before_send:
                before_send()
            await self.ws.send(json.dumps({"id": key, "method": method, "params": params}))
            return await asyncio.wait_for(future, timeout)
        finally:
            self.pending.pop(key, None)

    async def thread(self, sid):
        result = await self.rpc("thread/read", {"threadId": sid, "includeTurns": False})
        thread = result.get("thread", {})
        if thread.get("id") != sid:
            raise Rejected("Original thread identity was not confirmed")
        return thread

    async def _turn_pages(self, sid, *, items_view, limit):
        await self.thread(sid)
        cursor = None
        seen = set()
        for _ in range(8):
            result = await self.rpc("thread/turns/list", {
                "threadId": sid, "cursor": cursor, "limit": limit,
                "sortDirection": "desc", "itemsView": items_view,
            })
            rows = result.get("data")
            if not isinstance(rows, list):
                raise ValueError("Invalid native turn page")
            yield rows
            cursor = result.get("nextCursor")
            if not isinstance(cursor, str) or not cursor or cursor in seen:
                break
            seen.add(cursor)

    async def turn_status(self, sid, turn_id):
        """Read the exact callback turn without loading items or resuming it."""
        async for turns in self._turn_pages(sid, items_view="notLoaded", limit=50):
            for turn in turns:
                if isinstance(turn, dict) and turn.get("id") == turn_id:
                    status = turn.get("status")
                    return status if isinstance(status, str) else None
        return None

    async def find_task_receipt(self, sid, task_id):
        """Return positive evidence only; queued output can be missing from history."""
        async for turns in self._turn_pages(sid, items_view="full", limit=5):
            for turn in turns:
                if not isinstance(turn, dict):
                    continue
                turn_id, status, items = turn.get("id"), turn.get("status"), turn.get("items")
                if (not isinstance(turn_id, str) or not 1 <= len(turn_id) <= 512
                        or status not in ("inProgress", "completed", "failed", "interrupted")
                        or not isinstance(items, list)):
                    continue
                for item in items:
                    if not isinstance(item, dict) or item.get("type") != "functionCallOutput":
                        continue
                    receipt = parse_task_receipt(item)
                    if receipt is not None and receipt["task_id"] == task_id:
                        return turn_id, status
        return None

    async def close(self):
        self.reader.cancel()
        with suppress(asyncio.CancelledError, Exception):
            await self.reader


@asynccontextmanager
async def connect(home: Path, on_output=None):
    path = home / "app-server-control/app-server-control.sock"
    identity = socket_identity(str(path))
    async with daemon_connection(path, compression=None, max_size=4 * 1024 * 1024,
                                 open_timeout=5, close_timeout=1) as ws:
        if socket_identity(str(path)) != identity:
            raise ConnectionError("Selected account's daemon socket changed while connecting")
        client = NativeTasks(ws, on_output)
        try:
            await client.rpc("initialize", {
                "clientInfo": {"name": "cc-remote-async-tasks", "version": "1"},
                "capabilities": {"experimentalApi": True},
            })
            await ws.send(json.dumps({"method": "initialized"}))
            yield client
        finally:
            await client.close()
