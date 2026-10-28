"""Room dispatch: mention routing to per-member turns, results back to the room."""
from __future__ import annotations

import asyncio

import pytest

from cc_remote.wrapper.rooms import (
    MEMBER_DONE,
    MEMBER_IDLE,
    MEMBER_FAILED,
    MEMBER_QUEUED,
    MEMBER_RUNNING,
    RoomDispatcher,
    RoomError,
    RoomMember,
    RoomStore,
)


class FakeHost:
    """Records what the room asks for; optionally blocks inside a dispatch."""

    def __init__(self) -> None:
        self.sessions: dict[str, str] = {}
        self.queries: list[tuple[str, str, str, str]] = []
        self.steers: list[tuple[str, str]] = []
        self.cancels: list[str] = []
        self.gate: asyncio.Event | None = None
        self.fail_dispatch = False

    async def ensure_session(self, room, member) -> str:
        session_id = f"s-{member.member_id}"
        self.sessions[member.member_id] = session_id
        return session_id

    async def dispatch_query(self, session_id, text, *, msg_id, delivery) -> None:
        if self.fail_dispatch:
            raise RuntimeError("engine unavailable")
        self.queries.append((session_id, text, msg_id, delivery))
        if self.gate is not None:
            await self.gate.wait()

    async def steer_session(self, session_id, text) -> None:
        self.steers.append((session_id, text))

    async def cancel_session(self, session_id) -> None:
        self.cancels.append(session_id)


def _room(tmp_path, max_parallel=2):
    store = RoomStore(tmp_path)
    room = store.create("cc-remote 协作", "~/cooperation", [
        RoomMember(member_id="m-claude", label="Claude · iris", engine="claude"),
        RoomMember(member_id="m-codex", label="Codex · nyx", engine="codex"),
    ])
    host = FakeHost()
    # No retry pause in tests: a real 2s wait would race wait_idle() and make
    # the failure assertions flaky.
    dispatcher = RoomDispatcher(store, host, max_parallel=max_parallel,
                                retry_delay=0.0)
    return store, room, host, dispatcher


def test_message_without_mention_only_records(tmp_path):
    store, room, host, dispatcher = _room(tmp_path)

    async def scenario():
        message = await dispatcher.post(room.room_id, "大家先别动，我先看下代码")
        await dispatcher.wait_idle()
        return message

    message = asyncio.run(scenario())
    assert message.mentions == []
    assert host.queries == []
    assert len(store.get(room.room_id).messages) == 1


def test_mention_dispatches_to_that_member_session(tmp_path):
    store, room, host, dispatcher = _room(tmp_path)

    async def scenario():
        await dispatcher.post(room.room_id, "@Claude 先整理一下架构思路。")
        await dispatcher.wait_idle()

    asyncio.run(scenario())

    assert len(host.queries) == 1
    session_id, text, msg_id, delivery = host.queries[0]
    assert session_id == "s-m-claude"
    # The member must know which room/cwd it is working in, and see the raw ask.
    assert text.startswith("[房间 cc-remote 协作 · 目录 ~/cooperation]")
    assert "先整理一下架构思路。" in text
    assert delivery == "queue"
    # The session binding is persisted so a restart resumes the same conversation.
    assert store.get(room.room_id).member("m-claude").session_id == "s-m-claude"
    assert store.get(room.room_id).member("m-codex").session_id is None

    states = {state.member_id: state for state in dispatcher.runtime(room.room_id)}
    assert states["m-claude"].state == MEMBER_RUNNING
    assert states["m-claude"].session_id == "s-m-claude"
    assert states["m-codex"].state == "idle"


def test_broadcast_dispatches_to_every_member(tmp_path):
    store, room, host, dispatcher = _room(tmp_path)

    async def scenario():
        await dispatcher.post(room.room_id, "@全体 各自检查一下当前方案，先不要改代码。")
        await dispatcher.wait_idle()

    asyncio.run(scenario())
    assert {query[0] for query in host.queries} == {"s-m-claude", "s-m-codex"}


def test_max_parallel_bounds_running_turns(tmp_path):
    store, room, host, dispatcher = _room(tmp_path, max_parallel=1)
    host.gate = asyncio.Event()

    async def scenario():
        await dispatcher.post(room.room_id, "@全体 开始")
        # Let the scheduler start the first dispatch; the second must wait for a
        # permit because only one turn may run at a time.
        for _ in range(5):
            await asyncio.sleep(0)
        started = len(host.queries)
        first = host.queries[0][0]
        member = "m-claude" if first == "s-m-claude" else "m-codex"
        await dispatcher.on_turn_terminal(room.room_id, member, "done one")
        host.gate.set()
        await dispatcher.wait_idle()
        return started

    started = asyncio.run(scenario())
    assert started == 1
    assert len(host.queries) == 2


def test_terminal_appends_member_message_and_frees_member(tmp_path):
    store, room, host, dispatcher = _room(tmp_path)

    async def scenario():
        await dispatcher.post(room.room_id, "@nyx 看一下工具链")
        await dispatcher.wait_idle()
        return await dispatcher.on_turn_terminal(
            room.room_id, "m-codex", "工具链正常，无需改动。")

    message = asyncio.run(scenario())
    assert message is not None
    assert message.author_kind == "member"
    assert message.author_label == "Codex · nyx"
    assert message.state == MEMBER_DONE

    timeline = store.get(room.room_id).messages
    assert [item.author_kind for item in timeline] == ["user", "member"]
    state = {s.member_id: s for s in dispatcher.runtime(room.room_id)}["m-codex"]
    assert state.state == MEMBER_DONE
    assert state.started_at is None


def test_failed_turn_is_recorded_as_failed(tmp_path):
    store, room, host, dispatcher = _room(tmp_path)

    async def scenario():
        await dispatcher.post(room.room_id, "@Claude 试试")
        await dispatcher.wait_idle()
        return await dispatcher.on_turn_terminal(
            room.room_id, "m-claude", "", ok=False)

    message = asyncio.run(scenario())
    assert message is not None and message.state == MEMBER_FAILED


def test_dispatch_failure_fails_closed_and_releases_permit(tmp_path):
    store, room, host, dispatcher = _room(tmp_path, max_parallel=1)
    host.fail_dispatch = True

    async def scenario():
        await dispatcher.post(room.room_id, "@全体 开始")
        await dispatcher.wait_idle()

    asyncio.run(scenario())

    states = {state.member_id: state for state in dispatcher.runtime(room.room_id)}
    assert all(state.holding_permit is False for state in states.values())
    failures = [m for m in store.get(room.room_id).messages if m.state == MEMBER_FAILED]
    assert failures, "a failed dispatch must be visible in the timeline"


def test_steer_routes_to_running_session(tmp_path):
    store, room, host, dispatcher = _room(tmp_path)

    async def scenario():
        await dispatcher.post(room.room_id, "@Claude 开始")
        await dispatcher.wait_idle()
        await dispatcher.steer(room.room_id, "m-claude", "顺便看下测试覆盖")

    asyncio.run(scenario())
    assert host.steers == [("s-m-claude", "顺便看下测试覆盖")]


def test_steer_when_idle_becomes_queued_followup(tmp_path):
    store, room, host, dispatcher = _room(tmp_path)

    async def scenario():
        await dispatcher.steer(room.room_id, "m-claude", "补充一下结论")
        await dispatcher.wait_idle()

    asyncio.run(scenario())
    assert host.steers == []
    assert len(host.queries) == 1
    assert "补充一下结论" in host.queries[0][1]


def test_cancel_routes_to_session(tmp_path):
    store, room, host, dispatcher = _room(tmp_path)

    async def scenario():
        await dispatcher.post(room.room_id, "@Codex 跑一下")
        await dispatcher.wait_idle()
        await dispatcher.cancel(room.room_id, "m-codex")

    asyncio.run(scenario())
    assert host.cancels == ["s-m-codex"]


def test_unknown_room_and_member_are_rejected(tmp_path):
    store, room, host, dispatcher = _room(tmp_path)

    async def scenario():
        with pytest.raises(RoomError):
            await dispatcher.post("room-missing", "@Claude 你好")
        with pytest.raises(RoomError):
            await dispatcher.steer(room.room_id, "m-missing", "hi")
        with pytest.raises(RoomError):
            await dispatcher.cancel(room.room_id, "m-missing")
        with pytest.raises(RoomError):
            await dispatcher.post(room.room_id, "   ")

    asyncio.run(scenario())


def test_member_sees_another_members_advice(tmp_path):
    """The whole point of a room: the second agent can act on the first's advice."""
    store, room, host, dispatcher = _room(tmp_path)

    async def scenario():
        await dispatcher.post(room.room_id, "@Claude 给个建议，只回一句。")
        await dispatcher.wait_idle()
        await dispatcher.on_turn_terminal(
            room.room_id, "m-claude", "建议：先加集成测试，再改接口。")
        await dispatcher.post(room.room_id, "@nyx 按上面的建议执行。")
        await dispatcher.wait_idle()

    asyncio.run(scenario())

    codex_prompt = [text for session, text, *_ in host.queries if session == "s-m-codex"][0]
    assert "[房间近期记录]" in codex_prompt
    assert "Claude · iris: 建议：先加集成测试，再改接口。" in codex_prompt
    assert "[本次请求]" in codex_prompt
    # The ask is followed by the cc-plan instruction, so assert containment and
    # that the ask is the last piece of user content, not the last line overall.
    assert "按上面的建议执行。" in codex_prompt
    assert codex_prompt.index("按上面的建议执行。") < codex_prompt.index("cc-plan")


def test_transcript_excludes_the_current_message(tmp_path):
    store, room, host, dispatcher = _room(tmp_path)

    async def scenario():
        await dispatcher.post(room.room_id, "@Claude 第一句")
        await dispatcher.wait_idle()

    asyncio.run(scenario())
    prompt = host.queries[0][1]
    body = prompt.split("[本次请求]")[-1] if "[本次请求]" in prompt else prompt
    assert body.count("第一句") == 1, "the ask must not also appear as history"


def test_transcript_respects_depth(tmp_path):
    store, room, host, dispatcher = _room(tmp_path)
    store.update(room.room_id, name=None)  # no-op, keeps the room object
    room.context_depth = 2

    async def scenario():
        for index in range(4):
            await dispatcher.post(room.room_id, f"@Claude 第{index}句")
            await dispatcher.wait_idle()
            # Each dispatch holds a concurrency permit until its turn terminal,
            # so a loop must finish the turn or the third dispatch would wait.
            await dispatcher.on_turn_terminal(room.room_id, "m-claude", f"答{index}")

    asyncio.run(scenario())
    last = [text for session, text, *_ in host.queries if session == "s-m-claude"][-1]
    # Only the two entries before the current one are included.
    assert "第0句" not in last
    assert "第1句" not in last
    assert "第2句" in last


def test_context_can_be_disabled(tmp_path):
    store, room, host, dispatcher = _room(tmp_path)
    room.context_depth = 0

    async def scenario():
        await dispatcher.post(room.room_id, "@Claude 第一句")
        await dispatcher.wait_idle()
        await dispatcher.on_turn_terminal(room.room_id, "m-claude", "回答")
        await dispatcher.post(room.room_id, "@nyx 第二句")
        await dispatcher.wait_idle()

    asyncio.run(scenario())
    codex_prompt = [text for session, text, *_ in host.queries if session == "s-m-codex"][0]
    assert "[房间近期记录]" not in codex_prompt
    # The previous member reply must not leak in. Check for the transcript label
    # rather than the bare word: the cc-plan instruction also contains it.
    assert "Claude · iris:" not in codex_prompt

# ---- L4.2 context budget -------------------------------------------------

def test_quoted_entry_is_not_repeated_automatically(tmp_path):
    """Paying for the same text twice is waste: the quote already carries it."""
    store, room, host, dispatcher = _room(tmp_path)

    async def scenario():
        await dispatcher.post(room.room_id, "@Claude 给建议")
        await dispatcher.wait_idle()
        await dispatcher.on_turn_terminal(room.room_id, "m-claude", "建议：先加集成测试")
        await dispatcher.post(
            room.room_id,
            "[引用]\n【Claude · iris】\n建议：先加集成测试\n\n[本次请求]\n@nyx 照着做")
        await dispatcher.wait_idle()

    asyncio.run(scenario())
    prompt = [text for session, text, *_ in host.queries if session == "s-m-codex"][0]
    assert prompt.count("建议：先加集成测试") == 1, prompt
    # The earlier user turn is still legitimate context, so the transcript block
    # may exist; what must not happen is the quoted body appearing in it as well.
    history = prompt.split("[房间近期记录]")[-1].split("[本次请求]")[0]
    assert "建议：先加集成测试" not in history, history


def test_automatic_depth_drops_when_the_user_quotes(tmp_path):
    """With explicit quotes, a smaller automatic transcript is enough."""
    store, room, host, dispatcher = _room(tmp_path)

    async def scenario():
        for index in range(4):
            await dispatcher.post(room.room_id, f"@Claude 第{index}句")
            await dispatcher.wait_idle()
            await dispatcher.on_turn_terminal(room.room_id, "m-claude", f"答{index}")
        await dispatcher.post(
            room.room_id,
            "[引用]\n【Claude · iris】\n答1\n\n[本次请求]\n@nyx 接着来")
        await dispatcher.wait_idle()

    asyncio.run(scenario())
    prompt = [text for session, text, *_ in host.queries if session == "s-m-codex"][0]
    history = prompt.split("[房间近期记录]")[-1].split("[本次请求]")[0]
    included = [line for line in history.splitlines() if line.startswith(("你:", "Claude"))]
    assert len(included) <= 2, history
    assert "答1" not in history, "the quoted entry must be skipped"


def test_context_budget_is_respected(tmp_path):
    """A very long history is filled newest-first up to the character budget."""
    store, room, host, dispatcher = _room(tmp_path)
    room.context_depth = 20

    async def scenario():
        for index in range(6):
            big = f"第{index}条" + "x" * 3000
            await dispatcher.post(room.room_id, f"@Claude {big}")
            await dispatcher.wait_idle()
            await dispatcher.on_turn_terminal(room.room_id, "m-claude", f"答{index}")
        await dispatcher.post(room.room_id, "@nyx 收尾")
        await dispatcher.wait_idle()

    asyncio.run(scenario())
    from cc_remote.wrapper.rooms import CONTEXT_BUDGET
    prompt = [text for session, text, *_ in host.queries if session == "s-m-codex"][0]
    history = prompt.split("[房间近期记录]")[-1].split("[本次请求]")[0]
    assert len(history) < CONTEXT_BUDGET + 400, len(history)
    assert "第5条" in history, "newest content is kept"
    assert "第0条" not in history, "oldest content is dropped when over budget"

# ---- L3.2 runtime persistence -------------------------------------------

def test_runtime_state_survives_a_restart(tmp_path):
    """A member mid-turn at shutdown must not reappear as "running" forever."""
    store, room, host, dispatcher = _room(tmp_path)
    # No gate: the dispatch completes (state = running, waiting for a terminal),
    # which is exactly the "wrapper died mid-turn" snapshot we want to persist.
    async def start_turn():
        await dispatcher.post(room.room_id, "@Claude 开始")
        await dispatcher.wait_idle()

    asyncio.run(start_turn())
    assert dispatcher.runtime(room.room_id)[0].state == MEMBER_RUNNING

    # A new dispatcher over the same directory = a wrapper restart.
    revived = RoomDispatcher(RoomStore(tmp_path), host)
    state = revived.runtime(room.room_id)[0]
    assert state.session_id == "s-m-claude", "the session binding is kept"
    assert state.state == MEMBER_IDLE
    assert state.error == "重启时该回合已中断"
    assert state.started_at is None
    assert state.holding_permit is False, "no permit leaks across a restart"


def test_runtime_snapshot_is_persisted_for_done_members(tmp_path):
    store, room, host, dispatcher = _room(tmp_path)

    async def scenario():
        await dispatcher.post(room.room_id, "@Claude 给个结论")
        await dispatcher.wait_idle()
        await dispatcher.on_turn_terminal(room.room_id, "m-claude", "结论")

    asyncio.run(scenario())
    revived = RoomDispatcher(RoomStore(tmp_path), host)
    states = {state.member_id: state for state in revived.runtime(room.room_id)}
    assert states["m-claude"].state == MEMBER_DONE
    assert states["m-claude"].session_id == "s-m-claude"