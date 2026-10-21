"""Explicit offline acceptance for management, child history and native PTY."""
import asyncio
import json
from uuid import uuid4

from cc_remote.protocol import GetAgentDetail, ReadDsh, SetDshControl


async def exercise(machine, runtime, sid, child):
    browser = "native-surfaces"
    async def read(kind, **fields):
        result = await machine._handle(ReadDsh(sid=sid, kind=kind, client_id=browser, cmd_id=uuid4().hex, **fields))
        assert not result.error, result
        return result
    async def control(body):
        result = await machine._handle(SetDshControl(sid=sid, kind="terminal", value=json.dumps(body), client_id=browser, cmd_id=uuid4().hex))
        assert result.status == "success", result
        return result
    for kind in ("settings", "presets", "bundles", "schedules", "workflows", "trajectory", "terminals"):
        result = await read(kind)
        print("PASS native surface", kind, len(result.items), flush=True)
    detail = await machine._handle(GetAgentDetail(session_id=sid, run_id="dsh@" + child["id"],
        request_id=uuid4().hex, client_id=browser))
    assert detail.authoritative and not detail.error and detail.events, detail
    print("PASS native completed child process history", flush=True)
    terminal = uuid4().hex
    await control({"action": "create", "id": terminal})
    try:
        assert any(item.id == terminal for item in (await read("terminals")).items)
        attachment = await control({"action": "attach", "id": terminal})
        lease = attachment.request_id
        await control({"action": "input", "lease": lease, "value": {"type": "resize", "cols": 82, "rows": 25}})
        # A shell-only harmless output marker; no model/network access.
        await control({"action": "input", "lease": lease, "value": {"type": "write", "data": "printf 'cc_remote_pty_ok\\n'\r"}})
        output, cursor = "", 0
        async with asyncio.timeout(10):
            while "cc_remote_pty_ok" not in output:
                page = await read("terminal_output", query=lease, before_seq=cursor)
                cursor = page.next_seq
                output += "".join(str(item.data.get("screen", item.data.get("data", ""))) for item in page.items)
                await asyncio.sleep(0.05)
        await control({"action": "detach", "lease": lease})
        assert any(item.id == terminal for item in (await read("terminals")).items)
        print("PASS native PTY create, resize, input, output and detach retention", flush=True)
    finally:
        await control({"action": "close", "id": terminal})
    assert not any(item.id == terminal for item in (await read("terminals")).items)
    print("PASS explicit native PTY process close", flush=True)
