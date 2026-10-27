"""Member turn result collection: streaming deltas -> room timeline entry."""
from __future__ import annotations

import asyncio

import pytest

from cc_remote import protocol
from cc_remote.wrapper.room_service import RoomService, RoomTurnCollector


def _start(message_id="m1"):
    return protocol.AssistantMsgStart(message_id=message_id)


def _delta(text, message_id="m1", replace=False):
    return protocol.Delta(message_id=message_id, text=text, replace=replace)


def _end(is_error=False):
    return protocol.TurnEnd(result=protocol.TurnResult(
        subtype="error_during_execution" if is_error else "success",
        duration_ms=1, is_error=is_error))


def test_deltas_accumulate_and_turn_end_returns_text():
    collector = RoomTurnCollector()
    collector.observe("s1", _start())
    collector.observe("s1", _delta("工具链"))
    collector.observe("s1", _delta("正常"))
    assert collector.observe("s1", _end()) == "工具链正常"
    # Consumed: a later turn must not resurrect the old text.
    assert collector.observe("s1", _end()) == ""


def test_replace_rewrites_the_prefix():
    collector = RoomTurnCollector()
    collector.observe("s1", _start())
    collector.observe("s1", _delta("draft text"))
    collector.observe("s1", _delta("final", replace=True))
    assert collector.observe("s1", _end()) == "final"


def test_last_message_with_content_wins():
    collector = RoomTurnCollector()
    collector.observe("s1", _start("m1"))
    collector.observe("s1", _delta("先说一句", message_id="m1"))
    collector.observe("s1", _start("m2"))
    collector.observe("s1", _delta("结论在这里", message_id="m2"))
    assert collector.observe("s1", _end()) == "结论在这里"


def test_empty_turn_returns_empty_string():
    collector = RoomTurnCollector()
    collector.observe("s1", _start())
    assert collector.observe("s1", _end()) == ""


def test_sessions_are_isolated():
    collector = RoomTurnCollector()
    collector.observe("s1", _start())
    collector.observe("s1", _delta("A 的结论"))
    collector.observe("s2", _start())
    collector.observe("s2", _delta("B 的结论"))
    assert collector.observe("s1", _end()) == "A 的结论"
    assert collector.observe("s2", _end()) == "B 的结论"


def test_unrelated_frames_are_ignored():
    collector = RoomTurnCollector()
    assert collector.observe("s1", protocol.Ping(n=1)) is None


def test_message_buckets_are_bounded():
    collector = RoomTurnCollector(max_messages=3)
    for index in range(10):
        collector.observe("s1", _start(f"m{index}"))
        collector.observe("s1", _delta(f"text {index}", message_id=f"m{index}"))
    assert len(collector._text["s1"]) == 3


def _wrapper_machine():
    """Import the machine lazily: its dependency chain pulls mcp/pywin32."""
    try:
        from cc_remote.wrapper.machine import WrapperMachine
    except Exception as exc:  # noqa: BLE001 - environment-specific
        pytest.skip(f"wrapper machine unavailable here: {type(exc).__name__}: {exc}")
    return WrapperMachine

# ---- service level ------------------------------------------------------

class _FakeTransport:
    def __init__(self):
        self.frames = []

    async def send(self, frame):
        self.frames.append(frame)

    def of_type(self, kind):
        return [f for f in self.frames if getattr(f, "type", None) == kind]


class _FakeMachine:
    def __init__(self):
        self.transport = _FakeTransport()

    async def _spawn(self, resume_id, **kwargs):
        raise AssertionError("terminal collection must not spawn")

    def _ctx_wire_sid(self, ctx):
        return "wire-1"

    async def _handle_query(self, cmd):
        raise AssertionError("terminal collection must not send a query")


def test_observe_frame_collects_through_the_service(tmp_path):
    service = RoomService(_FakeMachine(), tmp_path)
    room = service.store.create("协作", "C:\\w", [
        protocol and __import__("cc_remote.wrapper.rooms", fromlist=["RoomMember"])
        .RoomMember(member_id="m1", label="Claude · iris"),
    ])
    service.store.bind_session(room.room_id, "m1", "wire-1")

    service.observe_frame("wire-1", _start())
    service.observe_frame("wire-1", _delta("结论：可以合并"))
    assert service.observe_frame("wire-1", _end()) == "结论：可以合并"


def test_machine_observer_only_reacts_to_turn_end(tmp_path):
    WrapperMachine = _wrapper_machine()

    machine = _FakeMachine()
    machine._room_service = RoomService(machine, tmp_path)
    room = machine._room_service.store.create("协作", "C:\\w", [
        __import__("cc_remote.wrapper.rooms", fromlist=["RoomMember"])
        .RoomMember(member_id="m1", label="Claude · iris"),
    ])
    machine._room_service.store.bind_session(room.room_id, "m1", "wire-1")
    observe = WrapperMachine._observe_room_frame.__get__(machine, type(machine))

    async def scenario():
        # Mid-turn frames are recorded but must not write a timeline entry.
        observe(None, _start())
        observe(None, _delta("还在写"))
        assert machine.transport.of_type("room_message") == []
        observe(None, _end())
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        return machine.transport.of_type("room_message")

    events = asyncio.run(scenario())
    assert events, "turn_end must append the member result"
    assert events[0].message["text"] == "还在写"


def test_machine_observer_ignores_sessions_outside_rooms(tmp_path):
    WrapperMachine = _wrapper_machine()

    machine = _FakeMachine()
    machine._room_service = RoomService(machine, tmp_path)
    observe = WrapperMachine._observe_room_frame.__get__(machine, type(machine))

    async def scenario():
        observe(None, _start())
        observe(None, _delta("普通会话"))
        observe(None, _end())
        await asyncio.sleep(0)

    asyncio.run(scenario())
    assert machine.transport.of_type("room_message") == []


def test_machine_observer_is_free_without_a_room_service():
    WrapperMachine = _wrapper_machine()

    machine = _FakeMachine()          # no _room_service attribute at all
    observe = WrapperMachine._observe_room_frame.__get__(machine, type(machine))
    observe(None, _end())             # must not raise


def test_collector_follows_a_renamed_session():
    """Deltas arrive under the temp key, the terminal under the real one."""
    collector = RoomTurnCollector()
    collector.observe("tmp-1", _start())
    collector.observe("tmp-1", _delta("已收到"))
    collector.rebind("tmp-1", "real-1")
    assert collector.observe("real-1", _end()) == "已收到"
    assert collector.observe("real-1", _end()) == ""


def test_collector_rebind_is_a_noop_for_empty_or_same_ids():
    collector = RoomTurnCollector()
    collector.observe("tmp-1", _start())
    collector.observe("tmp-1", _delta("x"))
    collector.rebind("", "real-1")
    collector.rebind("tmp-1", "tmp-1")
    assert collector.observe("tmp-1", _end()) == "x"