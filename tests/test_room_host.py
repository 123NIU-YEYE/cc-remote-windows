"""The room host adapter maps room capabilities onto wrapper entry points."""
from __future__ import annotations

import asyncio

from cc_remote import protocol
from cc_remote.wrapper.room_host import MachineRoomHost
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
