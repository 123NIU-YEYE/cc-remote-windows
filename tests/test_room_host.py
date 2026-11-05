"""The room host adapter maps room capabilities onto wrapper entry points."""
from __future__ import annotations

import asyncio

import pytest

from cc_remote import protocol
from cc_remote.wrapper.room_host import MachineRoomHost, RoomHostError
from cc_remote.wrapper.rooms import Room, RoomMember


class FakeDsh:
    """Stands in for DshRuntime: adopting registers a context."""

    def __init__(self, machine: "FakeMachine") -> None:
        self.machine = machine
        self.adoptable: set[str] = set()

    async def _ctx(self, sid):
        if sid in self.adoptable:
            ctx = FakeCtx(sid)
            self.machine.sessions[sid] = ctx
            self.machine.resident.add(sid)
            return ctx
        raise RuntimeError("会话不存在")

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
        # Session ids the fake wrapper still holds; used by residency checks.
        self.resident: set[str] = set()
        # Claude/Codex live here; DSH adoption is simulated by FakeDsh.
        self.sessions: dict[str, object] = {}
        self._dsh = FakeDsh(self)

    def _ctx_for(self, sid):
        return object() if sid in self.resident else None

    async def _spawn(self, resume_id, **kwargs):
        self.spawned.append((resume_id, kwargs))
        self.resident.add(self.next_sid)
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
    # _spawn_for handles failure itself so it can resume-or-report clearly.
    assert kwargs["raise_on_failure"] is False


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
    machine.resident.add("dsh@abc123")

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

def test_stale_bound_session_is_resumed():
    """A binding that outlived its session must resume, not hang forever.

    Reusing a dead session id only produced an Error frame, so the member stayed
    at "处理中" and no turn terminal ever arrived.
    """
    machine = FakeMachine()
    machine.next_sid = "wire-new"
    host = MachineRoomHost(machine)
    member = RoomMember(member_id="m1", label="Claude · iris", engine="claude",
                        session_id="wire-dead")

    # "wire-dead" is not in machine.resident, so it is stale.
    session_id = asyncio.run(host.ensure_session(_room(), member))

    assert session_id == "wire-new"
    resume_id, kwargs = machine.spawned[0]
    assert resume_id == "wire-dead", "the old conversation should be resumed"
    assert kwargs["engine"] == "claude"


def test_dsh_binding_is_adopted_not_spawned():
    """DSH has no spawn path: a known native session is taken over instead.

    The first live run failed here because a residency check rejected every DSH
    member, so adoption has to be the fallback rather than an error.
    """
    machine = FakeMachine()
    machine._dsh.adoptable.add("dsh@session-abc")
    host = MachineRoomHost(machine)
    member = RoomMember(member_id="d1", label="DSH · nyy", engine="dsh",
                        session_id="dsh@session-abc")

    assert asyncio.run(host.ensure_session(_room(), member)) == "dsh@session-abc"
    assert machine.spawned == [], "DSH must never go through _spawn"
    assert "dsh@session-abc" in machine.resident, "adoption registers the context"


def test_unknown_dsh_session_reports_a_clear_failure():
    machine = FakeMachine()
    host = MachineRoomHost(machine)
    member = RoomMember(member_id="d1", label="DSH · nyy", engine="dsh",
                        session_id="dsh@gone")

    with pytest.raises(RoomHostError) as info:
        asyncio.run(host.ensure_session(_room(), member))

    assert "无法接管" in str(info.value)
    assert machine.spawned == []


def test_resident_binding_is_reused_without_spawning():
    machine = FakeMachine()
    machine.resident.add("wire-live")
    host = MachineRoomHost(machine)
    member = RoomMember(member_id="m1", label="Claude · iris", engine="claude",
                        session_id="wire-live")

    assert asyncio.run(host.ensure_session(_room(), member)) == "wire-live"
    assert machine.spawned == []

def test_unresumable_binding_falls_back_to_a_fresh_session():
    """If resume fails the session is truly gone; keep the member usable."""
    machine = FakeMachine()
    machine.next_sid = "wire-fresh"

    async def failing_resume(resume_id, **kwargs):
        machine.spawned.append((resume_id, kwargs))
        if resume_id is not None:
            return None          # resume failed: session no longer exists
        machine.resident.add(machine.next_sid)
        return FakeCtx(machine.next_sid)

    machine._spawn = failing_resume
    host = MachineRoomHost(machine)
    member = RoomMember(member_id="m1", label="Claude · iris", engine="claude",
                        session_id="wire-dead")

    assert asyncio.run(host.ensure_session(_room(), member)) == "wire-fresh"
    assert [entry[0] for entry in machine.spawned] == ["wire-dead", None]