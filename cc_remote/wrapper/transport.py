"""WebSocket client to the relay. Outbound-only (no inbound ports on the
company machine). Auto-reconnects with backoff; on each (re)connect it calls
`on_connected` so the wrapper can re-announce itself (hello).

Live sends are best-effort: while disconnected, `send` drops frames (the ring
buffer + client replay is the source of truth, so nothing is lost). The send
queue is drained on disconnect to avoid double-delivery on reconnect.
"""
from __future__ import annotations

import asyncio
import os
from collections import deque
from typing import AsyncIterator, Awaitable, Callable, Optional
from urllib.parse import urlsplit
from urllib.request import getproxies_environment, proxy_bypass_environment

from websockets.asyncio.client import ClientConnection, connect

from cc_remote import __version__
from cc_remote.log import logger
from cc_remote.protocol import Error, Ping, Pong, ProtocolError, deserialize, serialize

log = logger("cc_remote.wrapper.transport")

# Keepalive tuning. The library defaults (20s ping, 20s pong timeout) assume a
# healthy path; a relay reached through a proxy and a tunnel needs more slack,
# otherwise a latency spike tears the connection down and the UI stalls.
_DEFAULT_PING_INTERVAL = 30.0
_DEFAULT_PING_TIMEOUT = 90.0
_APPLICATION_PING_INTERVAL = 20.0
_APPLICATION_PING_TIMEOUT = 60.0


class RelayClientConnection(ClientConnection):
    """Handle an HTTP proxy losing TLS before connection_made (websockets 16).

    start_tls can report connection_lost before websockets initializes its
    receive assembler. Preserve the original handshake failure and resolve the
    close waiter instead of raising AttributeError in the event-loop callback.
    """

    def connection_lost(self, exc: Exception | None) -> None:
        if hasattr(self, "recv_messages"):
            super().connection_lost(exc)
            return
        self.protocol.receive_eof()
        self.set_recv_exc(exc)
        if not self.connection_lost_waiter.done():
            self.connection_lost_waiter.set_result(None)


def _ws_ping_interval() -> float | None:
    raw = os.environ.get("CC_REMOTE_WS_PING_INTERVAL", "").strip()
    try:
        value = float(raw) if raw else _DEFAULT_PING_INTERVAL
    except ValueError:
        value = _DEFAULT_PING_INTERVAL
    return value if value > 0 else None


def _ws_ping_timeout() -> float | None:
    raw = os.environ.get("CC_REMOTE_WS_PING_TIMEOUT", "").strip()
    try:
        value = float(raw) if raw else _DEFAULT_PING_TIMEOUT
    except ValueError:
        value = _DEFAULT_PING_TIMEOUT
    return value if value > 0 else None



class _ByteQueue:
    """FIFO with simultaneous item and serialized-byte backpressure."""

    def __init__(self, max_items: int, max_bytes: int):
        self.maxsize = max(1, max_items)
        self.max_bytes = max(1024, max_bytes)
        self._items: deque[tuple[object, int]] = deque()
        self._bytes = 0
        self._closed = False
        self._condition = asyncio.Condition()

    @property
    def byte_size(self) -> int:
        return self._bytes

    def qsize(self) -> int:
        return len(self._items)

    async def put(self, item: object, size: int) -> None:
        if size < 0 or size > self.max_bytes:
            raise ValueError(
                f"queue item size {size} exceeds byte cap {self.max_bytes}"
            )
        async with self._condition:
            await self._condition.wait_for(lambda: (
                self._closed
                or (len(self._items) < self.maxsize
                    and self._bytes + size <= self.max_bytes)
            ))
            if self._closed:
                raise RuntimeError("queue is closed")
            self._items.append((item, size))
            self._bytes += size
            self._condition.notify_all()

    async def get(self):
        async with self._condition:
            await self._condition.wait_for(lambda: self._closed or self._items)
            if not self._items:
                return None
            item, size = self._items.popleft()
            self._bytes = max(0, self._bytes - size)
            self._condition.notify_all()
            return item

    async def drain(self) -> int:
        async with self._condition:
            count = len(self._items)
            self._items.clear()
            self._bytes = 0
            self._condition.notify_all()
            return count

    async def close(self) -> None:
        async with self._condition:
            self._closed = True
            self._items.clear()
            self._bytes = 0
            self._condition.notify_all()


class WrapperTransport:
    def __init__(self, url: str, token: str, *, inbox_cap: int = 1024,
                 send_cap: int = 8192, max_size: int = 16 * 1024 * 1024,
                 inbox_bytes: int = 32 * 1024 * 1024,
                 send_bytes: int = 32 * 1024 * 1024):
        self.url = url
        self.token = token
        self.max_size = max(1024, max_size)
        self.on_connected: Optional[Callable[[], Awaitable[None]]] = None
        self._inbox = _ByteQueue(inbox_cap, max(inbox_bytes, self.max_size))
        self._send_q = _ByteQueue(send_cap, max(send_bytes, self.max_size))
        self._connected = False
        self._generation = 0
        self._stop = False
        self._task: Optional[asyncio.Task] = None
        self._announcement_task: Optional[asyncio.Task] = None
        self._announcement_socket = None
        self._heartbeat_n = 0
        self._heartbeat_ack: Optional[asyncio.Event] = None

    async def start(self) -> None:
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        self._stop = True
        # Wake incoming(), sender(), and any producer blocked on byte pressure.
        await self._inbox.close()
        await self._send_q.close()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass

    async def send(self, msg) -> None:
        if (asyncio.current_task() is self._announcement_task
                and self._announcement_socket is not None):
            # The relay requires Hello as the first frame. A running Claude
            # turn can emit while a replacement socket is being announced;
            # keep those live frames in replay until Hello crosses the socket.
            await self._announcement_socket.send(serialize(msg))
            self._connected = True
            return
        if not self._connected:
            return  # dropped; ring buffer + client replay covers it
        raw = serialize(msg)
        generation = self._generation
        try:
            await self._send_q.put(
                (generation, raw), len(raw.encode("utf-8")))
        except (RuntimeError, ValueError) as exc:
            if not self._stop:
                log.warning("outbound frame rejected", error=str(exc))

    async def incoming(self) -> AsyncIterator:
        while True:
            item = await self._inbox.get()
            if item is None:
                break
            yield item

    async def _run(self) -> None:
        backoff = 1.0
        while not self._stop:
            try:
                headers = {"Authorization": f"Bearer {self.token}", "X-CC-Remote-Version": __version__}
                kw: dict = {
                    "create_connection": RelayClientConnection,
                    "open_timeout": 15,
                    "close_timeout": 5,
                    "additional_headers": headers,
                    "max_size": self.max_size,
                    # Select only the operator's explicit HTTP proxy variables.
                    # websockets 16 otherwise consults macOS system settings,
                    # which may select an unavailable SOCKS proxy.
                    "max_queue": 4,
                    # The library default (20s/20s) is too tight for a relay
                    # reached through a proxy and a tunnel: a single latency
                    # spike makes the client give up with
                    # "sent 1011 (internal error) keepalive ping timeout",
                    # which the UI shows as a stalled turn. Ping less often and
                    # allow a much longer pong wait; a truly dead link is still
                    # detected, just more patiently.
                    "ping_interval": _ws_ping_interval(),
                    "ping_timeout": _ws_ping_timeout(),
                }
                target = urlsplit(self.url)
                proxies = getproxies_environment()
                if (target.hostname in {"127.0.0.1", "::1", "localhost"}
                        or proxy_bypass_environment(
                            target.netloc, proxies)):
                    kw["proxy"] = None
                else:
                    kw["proxy"] = proxies.get("https") or proxies.get("http") or None
                async with connect(self.url, **kw) as ws:
                    log.info("connected to relay", url=self.url)
                    connected_at = asyncio.get_running_loop().time()
                    self._generation += 1
                    self._connected = self.on_connected is None
                    sender = asyncio.create_task(
                        self._sender(ws, self._generation))
                    receiver = asyncio.create_task(self._receiver(ws))
                    announcer = None
                    heartbeat = None
                    try:
                        # Announcing can fill the bounded queue. Its consumer
                        # must already be running, including during reconnect.
                        if self.on_connected:
                            self._announcement_socket = ws
                            announcer = asyncio.create_task(self.on_connected())
                            self._announcement_task = announcer
                            await asyncio.wait(
                                {announcer, sender, receiver},
                                return_when=asyncio.FIRST_COMPLETED,
                            )
                            if announcer.done():
                                announcer.result()
                                self._connected = True
                            # A socket can die while announcement is blocked on
                            # queue pressure. Reap that producer on this same
                            # generation instead of waiting forever for room.
                            else:
                                announcer.cancel()
                        if self._connected:
                            heartbeat = asyncio.create_task(self._keepalive(ws))
                        workers = {sender, receiver}
                        if heartbeat is not None:
                            workers.add(heartbeat)
                        _, pending = await asyncio.wait(
                            workers, return_when=asyncio.FIRST_COMPLETED,
                        )
                        for task in pending:
                            task.cancel()
                        # Observe a completed task's error so the reconnect log
                        # captures the real failure instead of emitting an
                        # unhandled-task warning later.
                        results = await asyncio.gather(
                            *workers, return_exceptions=True)
                        for result in results:
                            if (isinstance(result, BaseException)
                                    and not isinstance(
                                        result, asyncio.CancelledError)):
                                raise result
                    finally:
                        self._connected = False
                        self._announcement_socket = None
                        self._announcement_task = None
                        self._heartbeat_ack = None
                        # An accepted HTTP upgrade can still be rejected by the
                        # relay's Hello gate. Don't reset retry pacing on every
                        # such rejection while an old socket is being reaped.
                        if (asyncio.get_running_loop().time() - connected_at
                                >= _APPLICATION_PING_INTERVAL):
                            backoff = 1.0
                        self._generation += 1
                        # Parent cancellation (WrapperTransport.stop) can land
                        # while wait() is pending. Child tasks are not cancelled
                        # automatically, so reap them explicitly before leaving
                        # this socket generation.
                        tasks = [sender, receiver]
                        if announcer is not None:
                            tasks.append(announcer)
                        if heartbeat is not None:
                            tasks.append(heartbeat)
                        for task in tasks:
                            if not task.done():
                                task.cancel()
                        await asyncio.gather(*tasks, return_exceptions=True)
            except Exception as e:
                self._connected = False
                log.warning("relay connection lost, reconnecting",
                            error=str(e) or type(e).__name__,
                            error_type=type(e).__name__, backoff=backoff)
            await self._drain_send_queue()
            if not self._stop:
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 5.0)

    async def _drain_send_queue(self) -> None:
        dropped = await self._send_q.drain()
        if dropped:
            log.debug("drained send queue on disconnect", dropped=dropped)

    async def _sender(self, ws, generation: int) -> None:
        while True:
            item = await self._send_q.get()
            if item is None:
                return
            item_generation, raw = item
            if item_generation != generation or generation != self._generation:
                continue
            await ws.send(raw)

    async def _keepalive(self, ws) -> None:
        """Exercise the application path through HTTP proxies and tunnels.

        Native WebSocket pings don't prove that the relay's routing loop can
        receive frames. This exchange stays out of session queues and replay.
        Its deadline includes a blocked socket write as well as the reply.
        """
        while True:
            await asyncio.sleep(_APPLICATION_PING_INTERVAL)
            self._heartbeat_n = (self._heartbeat_n + 1) % 2_147_483_648
            ack = asyncio.Event()
            self._heartbeat_ack = ack
            try:
                async with asyncio.timeout(_APPLICATION_PING_TIMEOUT):
                    await ws.send(serialize(Ping(n=self._heartbeat_n)))
                    await ack.wait()
            finally:
                if self._heartbeat_ack is ack:
                    self._heartbeat_ack = None

    async def _receiver(self, ws) -> None:
        async for raw in ws:
            try:
                cmd = deserialize(raw)
            except ProtocolError as e:
                log.warning("dropping malformed frame from relay", error=str(e))
                continue
            if isinstance(cmd, Pong):
                if self._heartbeat_ack is not None and cmd.n == self._heartbeat_n:
                    self._heartbeat_ack.set()
                continue
            if isinstance(cmd, Error) and not cmd.sid:
                log.warning("relay rejected wrapper frame", code=cmd.code)
                continue
            try:
                await self._inbox.put(cmd, len(raw.encode("utf-8")))
            except (RuntimeError, ValueError) as exc:
                if not self._stop:
                    log.warning("inbound frame rejected", error=str(exc))
                return
