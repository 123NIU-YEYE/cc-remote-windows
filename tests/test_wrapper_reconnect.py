"""Transport recovery must preserve native work and release dead routes."""
import asyncio
import json
from types import SimpleNamespace

import pytest

from cc_remote.protocol import Delta, Hello, Ping, Pong, serialize
from cc_remote.relay import pairing
from cc_remote.relay.forward import ClientConn
from cc_remote.relay.pairing import RelayHub
from cc_remote.wrapper import transport as transport_module
from cc_remote.wrapper.transport import WrapperTransport
from tests.test_relay_forward import ScriptedWs


def test_running_turn_cannot_send_before_reconnect_hello(monkeypatch):
    async def run():
        announcing = asyncio.Event()
        greeting_sent = asyncio.Event()
        sent = []

        class Socket:
            def __aiter__(self):
                return self

            async def __anext__(self):
                await asyncio.Event().wait()

            async def send(self, raw):
                frame = json.loads(raw)
                sent.append(frame)
                if len(sent) == 1 and frame["type"] != "hello":
                    raise ConnectionError("relay requires Hello first")
                if frame["type"] == "hello":
                    greeting_sent.set()

        class Connection:
            async def __aenter__(self):
                return Socket()

            async def __aexit__(self, *_args):
                pass

        monkeypatch.setattr(transport_module, "connect", lambda *_a, **_k: Connection())
        transport = WrapperTransport("ws://127.0.0.1:8765/ws", "secret")

        async def announce():
            announcing.set()
            # A native Claude delta is ready while Hello is being constructed.
            await asyncio.sleep(0)
            await transport.send(Hello(role="wrapper"))

        async def native_output():
            await announcing.wait()
            await transport.send(Delta(message_id="before", text="replayed later"))
            await greeting_sent.wait()
            await transport.send(Delta(message_id="after", text="live"))

        transport.on_connected = announce
        producer = asyncio.create_task(native_output())
        await transport.start()
        try:
            await asyncio.wait_for(producer, 1)
            for _ in range(5):
                await asyncio.sleep(0)
            assert [frame["type"] for frame in sent] == ["hello", "delta"]
            assert sent[1]["message_id"] == "after"
        finally:
            await transport.stop()

    asyncio.run(run())


def test_rejected_socket_upgrade_does_not_reset_reconnect_backoff(monkeypatch):
    async def run():
        transport = WrapperTransport("ws://127.0.0.1:8765/ws", "secret")
        delays = []
        sleep = asyncio.sleep

        class Socket:
            def __aiter__(self):
                return self

            async def __anext__(self):
                raise ConnectionError("old wrapper connection still owns slot")

            async def send(self, _raw):
                pass

        class Connection:
            async def __aenter__(self):
                return Socket()

            async def __aexit__(self, *_args):
                pass

        async def wait(delay):
            delays.append(delay)
            if len(delays) == 3:
                transport._stop = True
            await sleep(0)

        async def keepalive(_socket):
            await asyncio.Event().wait()

        monkeypatch.setattr(transport, "_keepalive", keepalive)
        monkeypatch.setattr(transport_module, "connect", lambda *_a, **_k: Connection())
        monkeypatch.setattr(transport_module.asyncio, "sleep", wait)
        await transport._run()
        assert delays == [1.0, 2.0, 4.0]

    asyncio.run(run())


@pytest.mark.parametrize("blocked_write", [False, True])
def test_application_heartbeat_deadline_covers_write_and_reply(monkeypatch, blocked_write):
    monkeypatch.setattr(transport_module, "_APPLICATION_PING_INTERVAL", 0.001)
    monkeypatch.setattr(transport_module, "_APPLICATION_PING_TIMEOUT", 0.02)

    async def run():
        transport = WrapperTransport("ws://127.0.0.1:8765/ws", "secret")
        sent = []

        class Socket:
            async def send(self, raw):
                sent.append(json.loads(raw))
                if blocked_write:
                    await asyncio.Event().wait()

        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(transport._keepalive(Socket()), 0.5)
        assert sent[0]["type"] == "ping"
        assert transport._heartbeat_ack is None

    asyncio.run(run())


def test_heartbeat_reply_is_exact_and_bypasses_session_inbox():
    async def run():
        transport = WrapperTransport("ws://127.0.0.1:8765/ws", "secret", inbox_cap=1)
        await transport._inbox.put(object(), 1)
        ack = transport._heartbeat_ack = asyncio.Event()
        transport._heartbeat_n = 7

        class Socket:
            async def __aiter__(self):
                yield serialize(Pong(n=6))
                assert not ack.is_set()
                yield serialize(Pong(n=7))

        await asyncio.wait_for(transport._receiver(Socket()), 0.5)
        assert ack.is_set()
        assert transport._inbox.qsize() == 1

    asyncio.run(run())


def test_relay_heartbeat_is_private_and_expired_socket_releases_device(monkeypatch):
    monkeypatch.setattr(pairing, "WRAPPER_HEARTBEAT_IDLE_TIMEOUT", 0.02)

    async def run():
        hub = RelayHub(SimpleNamespace())
        browser_ws = ScriptedWs()
        browser = ClientConn(browser_ws, 8, "browser")
        hub._ensure_clients_for("device")["browser"] = browser
        browser.start()
        ws = ScriptedWs()
        await ws.incoming.put(serialize(Hello(role="wrapper", machine_id="device")))
        await ws.incoming.put(serialize(Ping(n=19)))
        try:
            await asyncio.wait_for(hub.serve_wrapper(ws, "device"), 0.5)
            await asyncio.sleep(0)
            assert json.loads(ws.sent[0])["type"] == "pong"
            assert json.loads(ws.sent[0])["n"] == 19
            assert hub._wrapper_for("device") is None
            assert ws.closed == [(1011, "wrapper heartbeat timeout")]
            assert [json.loads(raw)["type"] for raw in browser_ws.sent] == [
                "wrapper_reconnected", "wrapper_disconnected"]
        finally:
            await browser.stop()

    asyncio.run(run())


def test_idle_legacy_wrapper_is_not_expired_without_heartbeat(monkeypatch):
    monkeypatch.setattr(pairing, "WRAPPER_HEARTBEAT_IDLE_TIMEOUT", 0.005)

    async def run():
        hub = RelayHub(SimpleNamespace())
        ws = ScriptedWs()
        await ws.incoming.put(serialize(Hello(role="wrapper", machine_id="device")))
        task = asyncio.create_task(hub.serve_wrapper(ws, "device"))
        try:
            await asyncio.sleep(0.03)
            assert hub._wrapper_for("device") is ws
            assert ws.closed == []
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(run())


def test_expired_wrapper_releases_slot_before_close_finishes(monkeypatch):
    monkeypatch.setattr(pairing, "WRAPPER_HEARTBEAT_IDLE_TIMEOUT", 0.005)

    async def run():
        hub = RelayHub(SimpleNamespace())
        closing = asyncio.Event()
        release = asyncio.Event()

        class Socket(ScriptedWs):
            async def close(self, **_kwargs):
                closing.set()
                await release.wait()

        ws = Socket()
        await ws.incoming.put(serialize(Hello(role="wrapper", machine_id="device")))
        await ws.incoming.put(serialize(Ping(n=1)))
        task = asyncio.create_task(hub.serve_wrapper(ws, "device"))
        try:
            await asyncio.wait_for(closing.wait(), 0.5)
            assert hub._wrapper_for("device") is None
            replacement = ScriptedWs()
            hub._wrappers["device"] = replacement
            release.set()
            await asyncio.wait_for(task, 0.5)
            assert hub._wrapper_for("device") is replacement
        finally:
            release.set()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(run())
