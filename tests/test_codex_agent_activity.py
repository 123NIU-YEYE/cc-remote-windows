"""Native agents populate the dock without a model call or lifecycle mutation."""
import json
import sqlite3
from types import SimpleNamespace
from uuid import uuid4

import pytest

from cc_remote.wrapper.codex_agent_activity import CodexAgentActivity
from tests.test_codex_agents import rollout, task
from tests.test_codex_task_activity import task as detached_task
from tests.test_multisession import _mk_ctx, _mk_machine


def catalog(home, sid, parent=None, rows=(), **kwargs):
    path = rollout(home, sid, parent, rows=rows, **kwargs)
    source = {"subagent": {"thread_spawn": {"parent_thread_id": parent}}} if parent else "cli"
    with sqlite3.connect(home / "state_5.sqlite") as db:
        db.execute("CREATE TABLE IF NOT EXISTS threads (id TEXT PRIMARY KEY, rollout_path TEXT, source TEXT)")
        db.execute("INSERT OR REPLACE INTO threads VALUES (?,?,?)",
                   (sid, str(path), json.dumps(source)))
    return path


def boundary(sid):
    return "event_msg", {"type": "thread_settings_applied", "thread_id": sid}


def terminal(turn, kind="task_complete"):
    return "event_msg", {"type": kind, "turn_id": turn}


def append(path, *rows):
    with path.open("a") as stream:
        for typ, payload in rows:
            stream.write(json.dumps({"type": typ, "payload": payload}) + "\n")


def test_native_lifecycle_ignores_inherited_turn_and_unrelated_terminals(tmp_path):
    catalog(tmp_path, "parent", rows=[task("parent-turn")])
    child = catalog(tmp_path, "child", "parent", rows=[
        task("parent-turn"), boundary("child"), task("child-turn"),
        terminal("parent-turn"), terminal("other-turn"),
    ])
    reader = CodexAgentActivity()
    initial = reader.read(tmp_path, {"parent"})["parent"]
    assert len(initial) == 1
    assert initial[0].item_id == "codex-agent:child"
    assert initial[0].kind == "agent" and initial[0].status == "running"
    assert initial[0].title == "/root/child"
    assert reader.read(tmp_path, {"parent"})["parent"] == initial
    assert CodexAgentActivity().read(tmp_path, {"parent"})["parent"] == initial
    append(child, terminal("child-turn"))
    assert reader.read(tmp_path, {"parent"})["parent"] == []
    append(child, task("followup"))
    assert reader.read(tmp_path, {"parent"})["parent"][0].status == "running"
    append(child, terminal("child-turn"))  # Delayed old receipt is not a new finish.
    assert reader.read(tmp_path, {"parent"})["parent"][0].status == "running"
    append(child, terminal("followup", "turn_aborted"))
    assert reader.read(tmp_path, {"parent"})["parent"] == []


def test_legacy_native_agent_excludes_parent_context(tmp_path):
    catalog(tmp_path, "parent", rows=[task("inherited")])
    child = catalog(tmp_path, "child", "parent", rows=[
        task("inherited"), task("own"), terminal("inherited")])
    reader = CodexAgentActivity()
    assert reader.read(tmp_path, {"parent"})["parent"][0].status == "running"
    append(child, terminal("own"))
    assert reader.read(tmp_path, {"parent"})["parent"] == []


def test_only_direct_agents_in_same_account_and_not_ordinary_forks(tmp_path):
    home, other = tmp_path / "account", tmp_path / "other"
    catalog(home, "parent")
    catalog(home, "child", "parent", rows=[boundary("child"), task("own")])
    catalog(home, "nested", "child", rows=[boundary("nested"), task("nested")])
    catalog(home, "sibling", "someone", rows=[boundary("sibling"), task("sibling")])
    catalog(other, "foreign", "parent", rows=[boundary("foreign"), task("foreign")])
    catalog(home, "ordinary", rows=[task("user-fork")])
    with sqlite3.connect(home / "state_5.sqlite") as db:
        db.execute("UPDATE threads SET source=? WHERE id='ordinary'",
                   (json.dumps({"forked_from_id": "parent"}),))
    data = CodexAgentActivity().read(home, {"parent", "child"})
    assert [item.item_id for item in data["parent"]] == ["codex-agent:child"]
    assert [item.item_id for item in data["child"]] == ["codex-agent:nested"]


def test_foreign_catalog_path_and_changed_identity_never_report_live(tmp_path):
    home, other = tmp_path / "account", tmp_path / "other"
    catalog(home, "parent")
    local = catalog(home, "child", "parent", rows=[boundary("child"), task("own")])
    foreign = catalog(other, "child", "parent", rows=[boundary("child"), task("secret")])
    with sqlite3.connect(home / "state_5.sqlite") as db:
        db.execute("UPDATE threads SET rollout_path=? WHERE id='child'", (str(foreign),))
    reader = CodexAgentActivity()
    assert reader.read(home, {"parent"})["parent"][0].status == "unknown"
    with sqlite3.connect(home / "state_5.sqlite") as db:
        db.execute("UPDATE threads SET rollout_path=? WHERE id='child'", (str(local),))
    assert reader.read(home, {"parent"})["parent"][0].status == "running"
    local.write_text(local.read_text().replace('"parent_thread_id": "parent"', '"parent_thread_id": "other"'))
    assert reader.read(home, {"parent"})["parent"][0].status == "unknown"


def test_partial_record_missing_source_and_catalog_failure_are_not_completion(tmp_path):
    catalog(tmp_path, "parent")
    child = catalog(tmp_path, "child", "parent", rows=[boundary("child"), task("own")])
    reader = CodexAgentActivity()
    assert reader.read(tmp_path, {"parent"})["parent"][0].status == "running"
    ending = json.dumps({"type": "event_msg", "payload": terminal("own")[1]})
    with child.open("a") as stream:
        stream.write(ending[:20])
    assert reader.read(tmp_path, {"parent"})["parent"][0].status == "unknown"
    with child.open("a") as stream:
        stream.write(ending[20:] + "\n")
    assert reader.read(tmp_path, {"parent"})["parent"] == []
    append(child, task("next"))
    assert reader.read(tmp_path, {"parent"})["parent"][0].status == "running"
    child.unlink()
    assert reader.read(tmp_path, {"parent"})["parent"][0].status == "unknown"
    with sqlite3.connect(tmp_path / "state_5.sqlite") as db:
        db.execute("DELETE FROM threads WHERE id='child'")
    assert reader.read(tmp_path, {"parent"})["parent"][0].status == "unknown"
    (tmp_path / "state_5.sqlite").write_bytes(b"corrupt")
    with pytest.raises(sqlite3.DatabaseError):
        reader.read(tmp_path, {"parent"})


def test_incremental_budget_and_rewrite_do_not_reuse_old_terminal(tmp_path, monkeypatch):
    from cc_remote.wrapper import codex_agent_activity as module
    catalog(tmp_path, "parent")
    rows = [task("copied"), terminal("copied"), boundary("child"), task("own"), terminal("own")]
    child = catalog(tmp_path, "child", "parent", rows=rows)
    monkeypatch.setattr(module, "_READ_BUDGET", 1)
    reader = CodexAgentActivity()
    for _ in range(4):
        assert reader.read(tmp_path, {"parent"})["parent"][0].status == "unknown"
    assert reader.read(tmp_path, {"parent"})["parent"] == []
    # Replace in place with the same native identity, now a new live task.
    child = catalog(tmp_path, "child", "parent", rows=[boundary("child"), task("new")])
    assert reader.read(tmp_path, {"parent"})["parent"][0].status == "unknown"
    assert reader.read(tmp_path, {"parent"})["parent"][0].status == "running"
    assert reader._cursors["child"].offset == child.stat().st_size


def test_no_catalog_has_no_filesystem_side_effects(tmp_path):
    assert CodexAgentActivity().read(tmp_path, {"parent"}) == {"parent": []}
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_wrapper_merges_native_and_mcp_and_reseeds_after_parent_completion(tmp_path, monkeypatch):
    from cc_remote.protocol import Hello
    parent = str(uuid4())
    catalog(tmp_path, parent, rows=[task("main"), terminal("main")])
    child = catalog(tmp_path, "child", parent, rows=[boundary("child"), task("own")])
    store, key = detached_task(tmp_path, parent)
    machine, transport = _mk_machine()
    ctx, other = _mk_ctx(parent, parent), _mk_ctx("other", "other")
    for current in (ctx, other):
        current.engine = "codex"
        current.state = "idle"
        machine.sessions[current.key] = current
    monkeypatch.setattr(machine, "_codex_profile_for_ctx", lambda _: SimpleNamespace(home=tmp_path))
    await machine._refresh_codex_background_processes()
    frames = [m for m in transport.sent if m.type == "background_process_sync"]
    assert len(frames) == 1 and frames[0].sid == parent
    assert {item.item_id for item in frames[0].items} == {f"async-task:{key}", "codex-agent:child"}
    assert ctx.state == "idle" and other.codex_background_processes == {}
    transport.sent.clear()
    await machine._refresh_codex_background_processes()
    assert transport.sent == []
    await machine._handle_client_hello(Hello(
        role="client", client_id="fresh", route_id="route",
        cursors={parent: ctx.buffer.tail_seq}, generations={parent: machine.instance_id}))
    seeds = [m for m in transport.sent if m.type == "background_process_sync"
             and m.sid == parent and m.to == "fresh"]
    assert len(seeds) == 1 and len(seeds[0].items) == 2
    append(child, terminal("own"))
    await machine._refresh_codex_background_processes()
    assert list(ctx.codex_background_processes) == [f"async-task:{key}"]
    store.update(key, state="completed", delivery="delivered")
    await machine._refresh_codex_background_processes()
    assert ctx.codex_background_processes == {} and ctx.state == "idle"
