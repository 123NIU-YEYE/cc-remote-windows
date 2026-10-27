"""Room service: command -> store/dispatcher -> protocol frames."""
from __future__ import annotations

import asyncio

import pytest

from cc_remote import protocol
from cc_remote.wrapper.room_service import RoomService


class FakeCtx:
    def __init__(self, sid: str) -> None:
        self.sid = sid


class FakeTransport:
    def __init__(self) -> None:
        self.frames: list = []

    async def send(self, frame) -> None:
        self.frames.append(frame)

    def of_type(self, kind: str) -> list:
        return [frame for frame in self.frames if getattr(frame, "type", None) == kind]


class FakeMachine:
    def __init__(self) -> None:
        self.transport = FakeTransport()
        self.spawned: list[dict] = []
        self.queries: list[protocol.Query] = []
        self.steers: list[protocol.Steer] = []
        self.interrupts: list[protocol.Interrupt] = []

    async def _spawn(self, resume_id, **kwargs):
        self.spawned.append(kwargs)
        return FakeCtx("wire-1")

    def _ctx_wire_sid(self, ctx):
        return ctx.sid

    async def _handle_query(self, cmd):
        self.queries.append(cmd)

    async def _handle_steer(self, cmd):
        self.steers.append(cmd)

    async def _handle_interrupt(self, cmd):
        self.interrupts.append(cmd)


def _service(tmp_path):
    machine = FakeMachine()
    service = RoomService(machine, tmp_path)
    # Rooms now refuse a missing directory, so give every test a real one.
    workdir = tmp_path / "cooperation"
    workdir.mkdir(exist_ok=True)
    service._test_cwd = str(workdir)
    return machine, service


def _create(service, name="cc-remote 协作", cwd=None):
    return asyncio.run(service.on_create_room(protocol.CreateRoom(
        cmd_id="c1", name=name, cwd=cwd or service._test_cwd,
        members=[
            protocol.RoomMemberSpec(member_id="m1", label="Claude · iris",
                                    engine="claude"),
            protocol.RoomMemberSpec(member_id="m2", label="Codex · nyx",
                                    engine="codex"),
        ])))


def test_create_room_emits_list_and_snapshot(tmp_path):
    machine, service = _service(tmp_path)
    _create(service)

    rooms = service.store.list_rooms()
    assert len(rooms) == 1 and rooms[0].name == "cc-remote 协作"

    listing = machine.transport.of_type("room_list")
    snapshot = machine.transport.of_type("room_snapshot")
    assert len(listing) == 1 and len(listing[0].rooms) == 1
    assert listing[0].rooms[0]["messages"] == []          # sidebar stays small
    assert len(snapshot) == 1
    assert [m["label"] for m in snapshot[0].room["members"]] == [
        "Claude · iris", "Codex · nyx"]


def test_post_message_dispatches_and_emits_frames(tmp_path):
    machine, service = _service(tmp_path)
    _create(service)
    room_id = service.store.list_rooms()[0].room_id

    async def scenario():
        await service.on_post(protocol.PostRoomMessage(
            cmd_id="c2", room_id=room_id, text="@Claude 先整理一下架构思路。"))
        await service._dispatcher.wait_idle()

    asyncio.run(scenario())

    # One dispatch, into a session created in the room's directory.
    assert len(machine.spawned) == 1
    assert machine.spawned[0]["cwd"] == service._test_cwd
    assert len(machine.queries) == 1
    assert machine.queries[0].sid == "wire-1"

    events = machine.transport.of_type("room_message")
    assert [event.message["author_kind"] for event in events] == ["user"]
    assert events[0].message["mentions"] == ["m1"]

    states = machine.transport.of_type("room_state")
    assert states, "member state transitions must reach the client"
    assert states[-1].state == "running"
    assert states[-1].session_id == "wire-1"


def test_turn_terminal_appends_member_result(tmp_path):
    machine, service = _service(tmp_path)
    _create(service)
    room_id = service.store.list_rooms()[0].room_id

    async def scenario():
        await service.on_post(protocol.PostRoomMessage(
            cmd_id="c3", room_id=room_id, text="@nyx 看下工具链"))
        await service._dispatcher.wait_idle()
        await service.on_turn_terminal("wire-1", "工具链正常，无需改动。")

    asyncio.run(scenario())

    timeline = service.store.get(room_id).messages
    assert [item.author_kind for item in timeline] == ["user", "member"]
    assert timeline[-1].author_label == "Codex · nyx"
    assert timeline[-1].text == "工具链正常，无需改动。"

    events = machine.transport.of_type("room_message")
    assert events[-1].message["author_kind"] == "member"
    assert machine.transport.of_type("room_state")[-1].state == "done"


def test_terminal_for_unknown_session_is_ignored(tmp_path):
    machine, service = _service(tmp_path)
    _create(service)
    asyncio.run(service.on_turn_terminal("not-a-room-session", "hello"))
    assert machine.transport.of_type("room_message") == []


def test_get_room_returns_window(tmp_path):
    machine, service = _service(tmp_path)
    _create(service)
    room_id = service.store.list_rooms()[0].room_id

    async def scenario():
        await service.on_post(protocol.PostRoomMessage(
            cmd_id="c4", room_id=room_id, text="先别动代码"))
        await service.on_post(protocol.PostRoomMessage(
            cmd_id="c5", room_id=room_id, text="补充：看下测试"))
        await service._dispatcher.wait_idle()

    asyncio.run(scenario())
    asyncio.run(service.on_get_room(protocol.GetRoom(
        cmd_id="c6", room_id=room_id, limit=1)))

    snapshot = machine.transport.of_type("room_snapshot")[-1]
    assert len(snapshot.room["messages"]) == 1
    assert snapshot.room["messages"][0]["text"] == "补充：看下测试"
    assert [state["member_id"] for state in snapshot.states] == ["m1", "m2"]


def test_unknown_room_emits_error(tmp_path):
    machine, service = _service(tmp_path)
    asyncio.run(service.on_get_room(protocol.GetRoom(cmd_id="c7", room_id="nope")))
    errors = machine.transport.of_type("error")
    assert errors and errors[0].code == protocol.ERR_BAD_PROMPT


def test_delete_room_invalidates(tmp_path):
    machine, service = _service(tmp_path)
    _create(service)
    room_id = service.store.list_rooms()[0].room_id

    asyncio.run(service.on_delete_room(protocol.DeleteRoom(
        cmd_id="c8", room_id=room_id)))

    assert service.store.list_rooms() == []
    assert machine.transport.of_type("room_invalidated")
    assert machine.transport.of_type("room_list")[-1].rooms == []


def test_steer_room_member_uses_member_session(tmp_path):
    machine, service = _service(tmp_path)
    _create(service)
    room_id = service.store.list_rooms()[0].room_id

    async def scenario():
        await service.on_post(protocol.PostRoomMessage(
            cmd_id="c9", room_id=room_id, text="@Claude 开始"))
        await service._dispatcher.wait_idle()
        await service.on_steer(protocol.SteerRoomMember(
            cmd_id="c10", client_id="browser", room_id=room_id,
            member_id="m1", text="顺便看下测试覆盖"))

    asyncio.run(scenario())
    assert machine.steers and machine.steers[0].sid == "wire-1"
    assert machine.steers[0].prompt == "顺便看下测试覆盖"


def test_rebinding_follows_the_real_session_id(tmp_path):
    """A fresh session is keyed by a temp id until the engine reveals its own.

    Without following that rename a member's terminal can never be attributed,
    so the room would show "running" forever.
    """
    machine, service = _service(tmp_path)
    _create(service)
    room_id = service.store.list_rooms()[0].room_id

    async def scenario():
        await service.on_post(protocol.PostRoomMessage(
            cmd_id="c11", room_id=room_id, text="@Claude 开始"))
        await service._dispatcher.wait_idle()

    asyncio.run(scenario())
    assert service.store.get(room_id).member("m1").session_id == "wire-1"
    assert service.member_for_session("wire-1") == (room_id, "m1")

    service.rebind_session("wire-1", "real-9")

    assert service.store.get(room_id).member("m1").session_id == "real-9"
    assert service.member_for_session("real-9") == (room_id, "m1")
    assert service.member_for_session("wire-1") is None
    assert service._dispatcher.runtime(room_id)[0].session_id == "real-9"


def test_rebinding_is_idempotent_and_ignores_empty_ids(tmp_path):
    machine, service = _service(tmp_path)
    _create(service)
    room_id = service.store.list_rooms()[0].room_id
    service.rebind_session("", "real-9")
    service.rebind_session("wire-1", "wire-1")
    assert service.store.get(room_id).member("m1").session_id is None

def test_create_room_rejects_a_missing_directory(tmp_path):
    """A room whose directory does not exist can never dispatch, so refuse it."""
    machine, service = _service(tmp_path)
    missing = str(tmp_path / "does-not-exist")
    asyncio.run(service.on_create_room(protocol.CreateRoom(
        cmd_id="c20", name="坏目录", cwd=missing,
        members=[protocol.RoomMemberSpec(member_id="m1", label="Claude · iris")])))
    errors = [f for f in machine.transport.of_type("error")]
    assert errors and "目录不存在" in errors[0].message
    assert service.store.list_rooms() == []


def test_update_room_retargets_the_directory(tmp_path):
    machine, service = _service(tmp_path)
    _create(service, cwd=str(tmp_path))
    room_id = service.store.list_rooms()[0].room_id
    other = tmp_path / "other"
    other.mkdir()

    asyncio.run(service.on_update_room(protocol.UpdateRoom(
        cmd_id="c21", room_id=room_id, cwd=str(other))))

    assert service.store.get(room_id).cwd == str(other)
    assert machine.transport.of_type("error") == []


def test_update_room_rejects_a_missing_directory(tmp_path):
    machine, service = _service(tmp_path)
    _create(service, cwd=str(tmp_path))
    room_id = service.store.list_rooms()[0].room_id
    before = service.store.get(room_id).cwd

    asyncio.run(service.on_update_room(protocol.UpdateRoom(
        cmd_id="c22", room_id=room_id, cwd=str(tmp_path / "gone"))))

    assert service.store.get(room_id).cwd == before
    errors = machine.transport.of_type("error")
    assert errors and "目录不存在" in errors[-1].message


def test_failed_dispatch_is_pushed_to_the_client(tmp_path):
    """A dispatch failure must appear in the timeline, not only in the member row."""
    machine, service = _service(tmp_path)
    _create(service, cwd=str(tmp_path))
    room_id = service.store.list_rooms()[0].room_id

    async def failing_spawn(resume_id, **kwargs):
        raise RuntimeError("_SpawnFailure: 目录不存在: boom")

    machine._spawn = failing_spawn

    async def scenario():
        await service.on_post(protocol.PostRoomMessage(
            cmd_id="c23", room_id=room_id, text="@Claude 试一下"))
        await service._dispatcher.wait_idle()

    asyncio.run(scenario())

    events = machine.transport.of_type("room_message")
    failures = [e for e in events if (e.message or {}).get("state") == "failed"]
    assert failures, "the failure entry must be pushed, not just stored"
    text = failures[0].message["text"]
    assert "_SpawnFailure" not in text, "internal exception prefix must be stripped"
    assert "目录不存在" in text