import json

import pytest

from cc_remote.protocol import GetAgentDetail, ReadDsh, SetDshControl, is_downstream
from tests.test_dsh_runtime import setup_runtime


def command(action, **values):
    return SetDshControl(sid="dsh@root", client_id="browser", cmd_id="manage", kind="manage", value=json.dumps({"action": action, **values}))


@pytest.mark.asyncio
async def test_settings_exclude_credentials_and_reject_stale_revision():
    machine, _, runtime, client = setup_runtime()
    calls = []
    async def rpc(endpoint, args=None):
        calls.append((endpoint, args))
        if endpoint == "settings/describe":
            return {"writable": True, "namespaces": [
                {"ns": "llm-deepseek", "revision": 1, "value": {"key": "private"}},
                {"ns": "subagent", "revision": 4, "value": {"maxConcurrency": 6, "secret": "private", "proxy": "private", "nested": {"key": "private"}}, "secrets": [{"path": ["proxy"], "set": True}]},
            ]}
        return {}
    client.rpc = rpc
    view = await machine._handle(ReadDsh(sid="dsh@root", kind="settings", client_id="browser", cmd_id="read"))
    assert len(view.items) == 1
    assert view.items[0].data == {"revision": 4, "values": {"maxConcurrency": 6}}
    assert not is_downstream(view) and not machine.sessions
    for revision, patch in [(3, {"maxConcurrency": 3}), (4, {"proxy": "bad"})]:
        result = await machine._handle(command("settings", ns="subagent", revision=revision, patch=patch))
        assert result.status == "error"
    assert not any(e == "settings/update" for e, _ in calls)
    result = await machine._handle(command("settings", ns="subagent", revision=4, patch={"maxConcurrency": 3}))
    assert result.status == "success"
    assert calls[-1] == ("settings/update", {"ns": "subagent", "expectedRevision": 4, "patch": {"maxConcurrency": 3}})
    assert not machine.sessions
    await runtime.close()


@pytest.mark.asyncio
async def test_schedule_edit_preserves_original_session_and_observed_record():
    machine, _, runtime, client = setup_runtime()
    record = {"id": "task", "kind": "every", "title": "Review", "prompt": "Check", "everySeconds": 300, "scheduledAt": "2026-10-22T00:00:00Z"}
    calls = []
    async def rpc(endpoint, args=None):
        calls.append((endpoint, args))
        if endpoint == "schedule/catalog":
            return [{**record, "sessionId": "other", "status": "active"}]
        if endpoint == "schedule/history":
            return {"records": [{"messageId": "delivery", "deliveredAt": "2026-10-22T00:00:00Z"}]}
        return {"updated": True, "record": record}
    client.rpc = rpc
    result = await machine._handle(command("schedule_update", id="task", sid="dsh@other", expected=record, title="Review code", prompt="New", change={"kind": "every", "every_seconds": 600}))
    assert result.status == "success"
    assert calls[-1] == ("schedule/update", {"request": {"sessionId": "other", "id": "task", "expected": record, "title": "Review code", "prompt": "New", "change": {"kind": "every", "every_seconds": 600}}})
    page = await machine._handle(ReadDsh(sid="dsh@root", target_sid="dsh@other", kind="schedule_history", query="task", client_id="browser", cmd_id="history"))
    assert page.items[0].detail == "delivery"
    assert not machine.sessions
    await runtime.close()


@pytest.mark.asyncio
async def test_child_detail_is_private_and_does_not_follow_or_reside():
    machine, _, runtime, client = setup_runtime()
    client.summaries = {
        "root": {"sessionId": "root"},
        "child": {"sessionId": "child", "parentSessionId": "root", "origin": "subagent", "running": False},
        "grandchild": {"sessionId": "grandchild", "parentSessionId": "child", "origin": "subagent", "running": False},
    }
    async def catalog(sid):
        return {"entries": [{"id": "grandchild", "label": "Regression"}]}
    client.list_subagents = catalog
    result = await machine._handle(GetAgentDetail(session_id="dsh@root", run_id="dsh@grandchild", request_id="read-child", client_id="browser"))
    assert result.authoritative and not result.error and result.parent_run_id == "dsh@child"
    assert result.title == "Regression" and result.to == "browser"
    assert not machine.sessions and not is_downstream(result)
    result = await machine._handle(GetAgentDetail(session_id="dsh@root", run_id="dsh@foreign", request_id="bad-child", client_id="browser"))
    assert not result.authoritative and result.error
    assert not any(call[0] == "snapshot" and call[1] == "dsh@foreign" for call in client.calls)
    await runtime.close()


@pytest.mark.asyncio
async def test_paged_workflow_keeps_partial_terminal_identity_for_older_page_merge():
    machine, _, runtime, client = setup_runtime()
    async def snapshot(sid, **kwargs):
        return {"hasMore": True, "records": [
            {"event": {"seq": 100, "type": "tool-workflow/agent-end", "data": {"runId": "run", "seq": 2, "outcome": "failed"}}},
            {"event": {"seq": 101, "type": "tool-workflow/run-end", "data": {"runId": "run", "stopReason": "completed"}}},
        ]}
    client.history_snapshot = snapshot
    page = await machine._handle(ReadDsh(sid="dsh@root", kind="workflows", client_id="browser", cmd_id="read"))
    assert page.next_seq == 100 and page.has_more
    assert page.items[0].state == "completed"
    assert page.items[0].data == {"name_known": False, "members": [{"seq": 2, "state": "failed"}]}
    assert not machine.sessions
    await runtime.close()
