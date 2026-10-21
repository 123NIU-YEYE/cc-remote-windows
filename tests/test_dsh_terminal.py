import asyncio
import json

import pytest

from cc_remote.protocol import ReadDsh, SetDshControl, is_downstream
from tests.test_dsh_runtime import setup_runtime


def cmd(action, browser="browser", sid="dsh@root", key="command", **data):
    return SetDshControl(sid=sid, client_id=browser, cmd_id=key, kind="terminal", value=json.dumps({"action": action, **data}))


@pytest.mark.asyncio
async def test_terminal_lifetime_private_cursor_input_ack_and_browser_boundary():
    machine, _, runtime, client = setup_runtime()
    frames = asyncio.Queue()
    calls = []
    async def rpc(endpoint, args=None):
        calls.append((endpoint, args))
        return [{"id": "pty", "title": "shell", "state": "running", "cwd": "/tmp", "cols": 80, "rows": 24}] if endpoint == "terminal/list" else None
    async def stream(endpoint, args, *, on_open):
        calls.append((endpoint, args))
        async def send(value):
            calls.append(("input", value))
            await frames.put({"type": "ack", "sequence": value["sequence"]})
        on_open(send)
        yield {"type": "snapshot", "sequence": 0, "screen": "ready", "info": {}, "writable": True}
        try:
            while True:
                yield await frames.get()
        finally:
            calls.append(("detach", None))
    client.rpc, client.stream = rpc, stream
    listing = await machine._handle(ReadDsh(sid="dsh@root", kind="terminals", client_id="browser", cmd_id="list"))
    assert listing.items[0].id == "pty" and not machine.sessions and not is_downstream(listing)
    assert (await machine._handle(cmd("attach", id="pty", key="lease"))).status == "success"
    assert not machine.sessions
    for browser, sid in [("other", "dsh@root"), ("browser", "dsh@other")]:
        result = await machine._handle(cmd("input", browser, sid, lease="lease", value={"type": "write", "data": "forbidden"}))
        assert result.status == "error"
    assert not any(call[0] == "input" for call in calls)
    for i in range(2):
        assert (await machine._handle(cmd("input", lease="lease", value={"type": "write", "data": str(i)}))).status == "success"
    assert [row[1]["sequence"] for row in calls if row[0] == "input"] == [1, 2]
    read = ReadDsh(sid="dsh@root", kind="terminal_output", query="lease", client_id="browser", cmd_id="read")
    page = await machine._handle(read)
    assert page.items[0].data["screen"] == "ready" and page.next_seq == 1 and not is_downstream(page)
    assert (await machine._handle(read)).items == page.items  # lost response can be reread
    read.before_seq = 1
    assert (await machine._handle(read)).items == []
    await runtime.close()
    assert any(c[0] == "detach" for c in calls)
    assert not any(c[0] == "terminal/close" for c in calls)


@pytest.mark.asyncio
async def test_terminal_detach_does_not_create_or_close_process_and_rejects_stale_input():
    machine, _, runtime, _ = setup_runtime()
    result = await machine._handle(cmd("input", lease="expired", value={"type": "write", "data": "no"}))
    assert result.status == "error" and not machine.sessions
    await runtime.close()
