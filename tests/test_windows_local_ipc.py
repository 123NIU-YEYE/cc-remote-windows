"""Same-user loopback endpoints in place of Unix sockets. No live engines."""
import asyncio
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from uuid import uuid4

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="native Windows loopback endpoint contract")

from cc_remote import windows_ipc  # noqa: E402
from cc_remote.config import WrapperConfig, validate_wrapper_config  # noqa: E402
from cc_remote.session_messages import MAX_FRAME, parse_message, reference, rpc  # noqa: E402
from cc_remote.wrapper.local_control import LocalControl  # noqa: E402
from cc_remote.wrapper.session_messages import MessageStore  # noqa: E402
from tests.test_local_control import NATIVE, released_machine  # noqa: E402
from tests.test_session_messages import SOURCE, TARGET, backend  # noqa: E402


async def request(endpoint, payload):
    reader, writer = await windows_ipc.open_connection(endpoint, limit=4096)
    writer.write((payload if isinstance(payload, bytes) else json.dumps(payload).encode()) + b"\n")
    await writer.drain()
    reply = json.loads(await reader.readline())
    writer.close()
    return reply


def private_root(tmp_path):
    from cc_remote.windows_privacy import private_directory
    root = tmp_path / "private"
    private_directory(root)
    return root


@pytest.mark.asyncio
async def test_private_endpoint_releases_by_account_directory_and_rejects_anything_else():
    from cc_remote.windows_privacy import owned_path

    machine, _, profile, ctx = released_machine()
    control = LocalControl(machine)
    await control.start()
    try:
        assert control.server is not None
        assert owned_path(control.root, private=True)
        assert owned_path(control.socket, private=True)
        release = {"op": "release_claude_session", "config_dir": str(profile.config_dir), "session_id": NATIVE}
        assert await request(control.socket, release) == {"status": "released"}
        assert await request(control.socket, release) == {"status": "not_resident"}
        for bad in [{**release, "op": "send"}, {**release, "extra": 1}, {**release, "session_id": "../x"},
                    {**release, "config_dir": "relative"}, b"not json"]:
            reply = await request(control.socket, bad)
            assert reply["status"] == "rejected"
    finally:
        await control.close()
        await machine._command_scheduler.close()
    assert not control.socket.exists()


@pytest.mark.asyncio
async def test_endpoint_timeout_keeps_release_unknown_on_retry(monkeypatch):
    from cc_remote.wrapper import local_control

    machine, _, profile, ctx = released_machine()
    entered = asyncio.Event()

    async def disconnect():
        entered.set()
        await asyncio.Event().wait()

    ctx.sdk.disconnect.side_effect = disconnect
    # Longer than the Unix test's 0.05 s: the client verifies the server's user
    # before it writes, and that scan counts against the server's read timeout.
    monkeypatch.setattr(local_control, "REQUEST_TIMEOUT", 2)
    control = LocalControl(machine)
    await control.start()
    release = {"op": "release_claude_session", "config_dir": str(profile.config_dir), "session_id": NATIVE}
    try:
        reply = await asyncio.wait_for(request(control.socket, release), timeout=10)
        assert entered.is_set() and reply["status"] == "unknown"
        assert (await request(control.socket, release))["status"] == "unknown"
        exited, _ = machine._released_claude_sessions[ctx.key]
        assert exited.done() and exited.result() is False
        ctx.sdk.disconnect.assert_awaited_once()
    finally:
        await control.close()
        await machine._command_scheduler.close()


@pytest.mark.asyncio
async def test_a_second_wrapper_does_not_take_over_the_endpoint():
    machine, _, _, _ = released_machine()
    first, second = LocalControl(machine), LocalControl(machine)
    await first.start()
    await second.start()
    try:
        assert first.server is not None and second.server is None
        assert windows_ipc.endpoint_port(first.socket) == first.server.sockets[0].getsockname()[1]
    finally:
        await second.close()
        await first.close()
        await machine._command_scheduler.close()


@pytest.mark.asyncio
async def test_a_stale_endpoint_is_replaced_but_an_unrelated_path_is_not():
    from cc_remote.windows_privacy import private_directory

    machine, _, profile, _ = released_machine()
    control = LocalControl(machine)
    private_directory(control.root)
    control.socket.write_text(json.dumps({"port": 1, "pid": 1}))
    await control.start()
    try:
        assert control.server is not None
        assert windows_ipc.endpoint_port(control.socket) == control.server.sockets[0].getsockname()[1]
    finally:
        await control.close()
    control.socket.mkdir()
    await control.start()
    try:
        assert control.server is None and control.socket.is_dir()
    finally:
        await control.close()
        await machine._command_scheduler.close()


@pytest.mark.parametrize("content", [b"not json", b'{"port": true}', b'{"port": 0}',
                                     b'{"port": 65536}', b"{}", b" " * 300])
def test_endpoint_rejects_malformed_or_oversized_contents(tmp_path, content):
    endpoint = private_root(tmp_path) / "control.sock"
    endpoint.write_bytes(content)
    with pytest.raises(ValueError):
        windows_ipc.endpoint_port(endpoint)


def test_endpoint_in_a_shared_directory_is_rejected(tmp_path):
    import ntsecuritycon
    import win32security as security

    root = private_root(tmp_path)
    endpoint = root / "control.sock"
    windows_ipc.publish(endpoint, 4321)
    assert windows_ipc.endpoint_port(endpoint) == 4321
    descriptor = security.GetNamedSecurityInfo(
        str(root), security.SE_FILE_OBJECT, security.DACL_SECURITY_INFORMATION)
    acl = descriptor.GetSecurityDescriptorDacl()
    acl.AddAccessAllowedAceEx(security.ACL_REVISION, 0, ntsecuritycon.FILE_GENERIC_READ,
                             security.CreateWellKnownSid(security.WinWorldSid))
    security.SetNamedSecurityInfo(str(root), security.SE_FILE_OBJECT,
                                 security.DACL_SECURITY_INFORMATION, None, None, acl, None)
    with pytest.raises(ValueError, match="private"):
        windows_ipc.endpoint_port(endpoint)


def test_missing_endpoint_is_an_os_error(tmp_path):
    with pytest.raises(FileNotFoundError):
        windows_ipc.endpoint_port(private_root(tmp_path) / "control.sock")


@pytest.mark.asyncio
async def test_peer_check_identifies_this_users_loopback_connection():
    server = await asyncio.start_server(lambda reader, writer: None, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    try:
        local = writer.get_extra_info("sockname")[1]
        assert await asyncio.to_thread(windows_ipc.same_user_peer, local, port)
        assert await asyncio.to_thread(windows_ipc.same_user_peer, port, local)
        assert not await asyncio.to_thread(windows_ipc.same_user_peer, local, local)
    finally:
        writer.close()
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_server_drops_a_peer_that_fails_the_user_check(tmp_path, monkeypatch):
    handled = []

    async def handler(reader, writer):
        handled.append(True)
        writer.close()

    endpoint = private_root(tmp_path) / "control.sock"
    server = await windows_ipc.start_server(handler, endpoint, limit=4096)
    monkeypatch.setattr(windows_ipc, "same_user_peer", lambda peer, port: False)
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", windows_ipc.endpoint_port(endpoint))
        writer.write(b"{}\n")
        await writer.drain()
        try:
            reply = await asyncio.wait_for(reader.read(), 5)
        except ConnectionResetError:
            # Closing a socket with unread input resets it on Windows.
            reply = b""
        assert reply == b""
        writer.close()
        assert handled == []
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_pending_peer_checks_are_bounded_before_admission(tmp_path, monkeypatch):
    import threading

    release, started = threading.Event(), []

    def slow_check(peer, port):
        started.append(peer)
        release.wait(10)
        return True

    async def close(reader, writer):
        writer.close()

    endpoint = private_root(tmp_path) / "control.sock"
    server = await windows_ipc.start_server(close, endpoint, limit=4096)
    monkeypatch.setattr(windows_ipc, "same_user_peer", slow_check)
    port = windows_ipc.endpoint_port(endpoint)
    writers = []
    try:
        for _ in range(windows_ipc.MAX_PENDING_CHECKS):
            writers.append((await asyncio.open_connection("127.0.0.1", port))[1])
        for _ in range(100):
            if len(started) == windows_ipc.MAX_PENDING_CHECKS:
                break
            await asyncio.sleep(0.05)
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writers.append(writer)
        assert await asyncio.wait_for(reader.read(), 5) == b""
        assert len(started) == windows_ipc.MAX_PENDING_CHECKS
    finally:
        release.set()
        for writer in writers:
            writer.close()
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_client_refuses_an_endpoint_served_by_another_user(tmp_path, monkeypatch):
    server = await asyncio.start_server(lambda reader, writer: None, "127.0.0.1", 0)
    endpoint = private_root(tmp_path) / "control.sock"
    windows_ipc.publish(endpoint, server.sockets[0].getsockname()[1])
    monkeypatch.setattr(windows_ipc, "same_user_peer", lambda peer, port: False)
    try:
        with pytest.raises(ValueError, match="this user"):
            await windows_ipc.open_connection(endpoint, limit=4096)
    finally:
        server.close()
        await server.wait_closed()


def test_store_survives_restart_conflicts_and_caller_isolation(tmp_path):
    from cc_remote.windows_privacy import owned_path

    store = MessageStore(tmp_path / "messages")
    original, fresh = store.claim(SOURCE, "r", TARGET, "hi")
    assert fresh and original["status"] == "unknown"
    recovered, fresh = MessageStore(store.root).claim(SOURCE, "r", TARGET, "hi")
    assert not fresh and recovered == original
    with pytest.raises(ValueError, match="different arguments"):
        store.claim(SOURCE, "r", TARGET, "different")
    with pytest.raises(ValueError, match="No receipt"):
        store.get(TARGET, "r")
    store.settle(original["message_id"], "accepted")
    store.settle(original["message_id"], "unknown")
    assert store.get(SOURCE, "r")["status"] == "accepted"
    assert owned_path(store.root, private=True)


@pytest.mark.asyncio
async def test_real_stdio_mcp_binds_codex_metadata_through_the_private_endpoint(monkeypatch, tmp_path):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    bus, machine, target, rows, source, recipient = backend(monkeypatch)
    bus.store = MessageStore(tmp_path / "messages")
    bus.socket = bus.store.root / "control.sock"
    home = tmp_path / "account"
    home.mkdir()
    machine._codex_profiles = [SimpleNamespace(id="test", home=home)]
    sid = str(uuid4())
    native_ref = reference("codex", "test@" + sid)
    rows[native_ref] = rows.pop(source)
    monkeypatch.setattr(machine, "_codex_wire_sid", lambda p, sid: "test@" + sid)
    sent = []

    async def query(ctx, cmd, **_):
        sent.append(parse_message(cmd.prompt))
        bus.accepted(cmd.msg_id)
    monkeypatch.setattr(machine, "_handle_immediate_query", query)
    await bus.start()
    try:
        parameters = StdioServerParameters(command=sys.executable,
            args=["-X", "utf8", "-m", "cc_remote.session_messages", "mcp",
                  "--socket", str(bus.socket), "--codex-home", str(home)],
            cwd=str(Path(__file__).resolve().parents[1]))
        # A hang guard only: a cold Windows interpreter can spend most of it importing.
        async with asyncio.timeout(120), stdio_client(parameters) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                assert {tool.name for tool in (await session.list_tools()).tools} == {
                    "list_sessions", "send_message", "message_status"}
                missing = await session.call_tool("list_sessions", {})
                assert missing.isError
                result = await session.call_tool("list_sessions", {}, meta={
                    "x-codex-turn-metadata": {"thread_id": sid}})
                assert not result.isError
                assert json.loads(result.content[0].text)["sessions"][0]["session_ref"] == recipient
                args = {"session_ref": recipient, "message": "🙂" * 32000, "request_id": "unicode"}
                receipt = await session.call_tool("send_message", args, meta={"threadId": sid})
                retry = await session.call_tool("send_message", args, meta={"threadId": sid})
                assert not receipt.isError
                assert json.loads(receipt.content[0].text)["status"] == "accepted"
                assert retry.content == receipt.content
                assert len(sent) == 1 and sent[0].source_thread_id == native_ref
                assert sent[0].prompt == args["message"]
        with pytest.raises(ValueError):
            await rpc(bus.socket, {"codex_home": str(tmp_path / "wrong"), "thread_id": sid,
                                   "tool": "list_sessions", "arguments": {}})
    finally:
        await bus.close()
        await machine._command_scheduler.close()
    assert not bus.socket.exists()


@pytest.mark.asyncio
async def test_corrupt_ack_is_unknown_not_a_rejection(tmp_path):
    received = []

    async def broken(reader, writer):
        received.append(await reader.readline())
        writer.write(b"not-json\n")
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    endpoint = private_root(tmp_path) / "control.sock"
    server = await windows_ipc.start_server(broken, endpoint, limit=MAX_FRAME)
    try:
        with pytest.raises(ConnectionError, match="acknowledgment"):
            await rpc(endpoint, {"tool": "send_message"})
        assert len(received) == 1
    finally:
        server.close()
        await server.wait_closed()


def test_session_messages_are_allowed_but_the_claude_service_is_not():
    cfg = WrapperConfig(session_messages_enabled=True)
    try:
        validate_wrapper_config(cfg)
    except ValueError as exc:
        assert "unavailable on Windows" not in str(exc)
    cfg.claude_service_socket = "C:/state/service.sock"
    with pytest.raises(ValueError, match="Claude service is unavailable on Windows"):
        validate_wrapper_config(cfg)
