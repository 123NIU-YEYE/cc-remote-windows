"""The room host adapter maps room capabilities onto wrapper entry points."""
from __future__ import annotations

import asyncio

import pytest

from cc_remote import protocol
from cc_remote.wrapper.room_host import MachineRoomHost, RoomHostError
from cc_remote.wrapper.rooms import Room, RoomMember


class FakeCtx:
    def __init__(self, sid: str) -> None:
        self.sid = sid


class FakeMachine:
    """Records the wrapper calls the adapter is supposed to make."""

    def __init__(self) -> None:
        self.spawned: list[tuple] = []
        self.queries: list[protocol.Query] = []
        self.steers: list[protocol.Steer] = []
        self.interrupts: list[protocol.Interrupt] = []
        self.next_sid = "wire-1"

    async def _spawn(self, resume_id, **kwargs):
        self.spawned.append((resume_id, kwargs))
        return FakeCtx(self.next_sid)

    def _ctx_wire_sid(self, ctx):
        return ctx.sid

    async def _handle_query(self, cmd):
        self.queries.append(cmd)

    async def _handle_steer(self, cmd):
        self.steers.append(cmd)

    async def _handle_interrupt(self, cmd):
        self.interrupts.append(cmd)


def _room():
    return Room(room_id="r1", name="协作", cwd="C:\\work\\cooperation",
                members=[RoomMember(member_id="m1", label="Claude · iris",
                                    engine="claude")])


def test_ensure_session_passes_room_directory_and_engine():
    machine = FakeMachine()
    host = MachineRoomHost(machine)
    room, member = _room(), _room().members[0]

    session_id = asyncio.run(host.ensure_session(room, member))

    assert session_id == "wire-1"
    resume_id, kwargs = machine.spawned[0]
    assert resume_id is None                      # a room member resumes nothing
    assert kwargs["cwd"] == room.cwd
    assert kwargs["engine"] == "claude"
    assert kwargs["raise_on_failure"] is True


def test_dispatch_query_targets_the_member_session():
    machine = FakeMachine()
    host = MachineRoomHost(machine)

    asyncio.run(host.dispatch_query(
        "wire-1", "[房间 协作 · 目录 C:\\work\\cooperation]\n看下架构",
        msg_id="msg-9", delivery="queue"))

    cmd = machine.queries[0]
    assert isinstance(cmd, protocol.Query)
    assert cmd.sid == "wire-1"
    assert cmd.msg_id == "msg-9"
    assert cmd.delivery == "queue"
    assert cmd.cmd_id and cmd.client_id            # deferred query needs both
    assert "看下架构" in cmd.prompt


def test_dispatch_immediate_is_preserved():
    machine = FakeMachine()
    host = MachineRoomHost(machine)
    asyncio.run(host.dispatch_query("wire-1", "hi", msg_id="m", delivery="immediate"))
    assert machine.queries[0].delivery == "immediate"


def test_steer_targets_the_member_session():
    machine = FakeMachine()
    host = MachineRoomHost(machine)

    asyncio.run(host.steer_session("wire-1", "顺便补测试"))

    cmd = machine.steers[0]
    assert cmd.sid == "wire-1"
    assert cmd.prompt == "顺便补测试"
    assert cmd.cmd_id and cmd.client_id


def test_cancel_targets_the_member_session():
    machine = FakeMachine()
    host = MachineRoomHost(machine)

    asyncio.run(host.cancel_session("wire-1"))

    cmd = machine.interrupts[0]
    # Interrupt resolves its target from sid, so a room can cancel one member
    # without touching whichever session the browser happens to be focused on.
    assert cmd.sid == "wire-1"


def test_adapter_exposes_the_room_host_surface():
    host = MachineRoomHost(FakeMachine())
    for name in ("ensure_session", "dispatch_query", "steer_session", "cancel_session"):
        assert callable(getattr(host, name)), name


def test_dsh_member_fails_with_a_readable_message():
    """DSH sessions cannot be created from a directory: say so, do not assert.

    The wrapper's DSH runtime only adopts an existing native session, and _spawn
    has no dsh branch (it would fail an internal claude-profile assertion with an
    empty message, which is what the user saw in the room timeline).
    """
    machine = FakeMachine()
    host = MachineRoomHost(machine)
    member = RoomMember(member_id="d1", label="DSH · nyy", engine="dsh")

    with pytest.raises(RoomHostError) as info:
        asyncio.run(host.ensure_session(_room(), member))

    message = str(info.value)
    assert "DSH" in message
    assert "已存在的会话" in message
    assert machine.spawned == [], "must not reach _spawn for an unspawnable engine"


def test_claude_and_codex_members_still_spawn():
    for engine in ("claude", "codex"):
        machine = FakeMachine()
        host = MachineRoomHost(machine)
        member = RoomMember(member_id="m", label="Agent", engine=engine)
        assert asyncio.run(host.ensure_session(_room(), member)) == "wire-1"
        assert machine.spawned[0][1]["engine"] == engine

def test_member_with_a_bound_session_skips_spawn():
    """A pre-bound member (the only way a DSH member works) is used as-is."""
    machine = FakeMachine()
    host = MachineRoomHost(machine)
    member = RoomMember(member_id="d1", label="DSH · nyy", engine="dsh",
                        session_id="dsh@abc123")

    assert asyncio.run(host.ensure_session(_room(), member)) == "dsh@abc123"
    assert machine.spawned == []

def test_member_model_and_effort_reach_spawn():
    """Per-member controls must reach _spawn, which already supports them."""
    machine = FakeMachine()
    host = MachineRoomHost(machine)
    member = RoomMember(member_id="m1", label="Claude · iris", engine="claude",
                        model="claude-opus-5-5", effort="high")

    asyncio.run(host.ensure_session(_room(), member))

    _resume, kwargs = machine.spawned[0]
    assert kwargs["model"] == "claude-opus-5-5"
    assert kwargs["effort"] == "high"


def test_omitted_model_keeps_the_engine_default():
    machine = FakeMachine()
    host = MachineRoomHost(machine)
    asyncio.run(host.ensure_session(_room(), _room().members[0]))
    _resume, kwargs = machine.spawned[0]
    assert kwargs["model"] is None
    assert kwargs["effort"] is None