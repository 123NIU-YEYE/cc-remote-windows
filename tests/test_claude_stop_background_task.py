"""Stopping a Claude background task from the Web, by its public item id."""
import asyncio

from claude_agent_sdk import SystemMessage

from cc_remote.claude_service.client import RemoteClient
from cc_remote.protocol import (
    ERR_INTERNAL, ERR_NOT_RUNNING, Error, StopBackgroundTask,
    deserialize, is_client_message, serialize,
)
from cc_remote.wrapper.stream import (
    StreamTranslator, _agent_process_id, _wire_id, claude_background_item_id,
)
from tests.test_multisession import _mk_ctx, _mk_machine


class _StopRecorder:
    def __init__(self, failure: Exception | None = None):
        self.stopped: list[str] = []
        self.failure = failure

    async def stop_task(self, task_id: str) -> None:
        if self.failure is not None:
            raise self.failure
        self.stopped.append(task_id)


def _command(item_id: str) -> StopBackgroundTask:
    return StopBackgroundTask(
        sid="claude-session", item_id=item_id,
        cmd_id="cmd-stop", client_id="client-a")


def _claude_ctx(sdk):
    ctx = _mk_ctx("claude-session", "claude-session")
    ctx.engine = "claude"
    ctx.sdk = sdk
    return ctx


def test_stop_background_task_is_a_reliable_client_command():
    command = _command("bash-1")
    restored = deserialize(serialize(command))
    assert isinstance(restored, StopBackgroundTask)
    assert restored.item_id == "bash-1"
    assert is_client_message(restored)


def test_public_item_id_matches_the_background_projection():
    meta: dict = {}
    translator = StreamTranslator(4096, item_meta=meta)
    # An Agent task is remembered with its parent tool call before it runs.
    meta[_wire_id("agent task/7", "task")] = ("agent", "toolu_parent")
    synced = translator._feed_background_tasks_changed(SystemMessage(
        subtype="background_tasks_changed",
        data={"tasks": [
            {"task_id": "bash-1", "task_type": "local_bash", "description": "dev server"},
            {"task_id": "agent task/7", "task_type": "local_agent", "description": "review"},
        ]},
    ))
    published = [item.item_id for item in synced[0].items]
    assert published == [
        claude_background_item_id("bash-1", meta),
        claude_background_item_id("agent task/7", meta),
    ]
    assert published[1] == _agent_process_id("toolu_parent")


def test_stop_resolves_the_live_native_task_and_calls_the_engine():
    async def run():
        machine, _transport = _mk_machine()
        sdk = _StopRecorder()
        ctx = _claude_ctx(sdk)
        machine.sessions[ctx.key] = ctx
        ctx.claude_active_tasks = {"bash-1", "agent task/7"}
        ctx.claude_item_meta[_wire_id("agent task/7", "task")] = (
            "agent", "toolu_parent")

        assert await machine._handle_stop_background_task(
            _command(_agent_process_id("toolu_parent"))) is None
        assert await machine._handle_stop_background_task(
            _command("bash-1")) is None
        assert sdk.stopped == ["agent task/7", "bash-1"]

    asyncio.run(run())


def test_stop_rejects_unknown_unsupported_and_failed_requests():
    async def run():
        machine, transport = _mk_machine()
        sdk = _StopRecorder()
        ctx = _claude_ctx(sdk)
        machine.sessions[ctx.key] = ctx
        ctx.claude_active_tasks = {"bash-1"}

        # Only tasks Claude currently reports as live may be stopped.
        unknown = await machine._handle_stop_background_task(_command("bash-2"))
        assert isinstance(unknown, Error) and unknown.code == ERR_NOT_RUNNING
        assert unknown.request_id == "cmd-stop" and unknown.to == "client-a"
        assert sdk.stopped == []

        ctx.sdk = _StopRecorder(failure=RuntimeError("control timeout"))
        failed = await machine._handle_stop_background_task(_command("bash-1"))
        assert isinstance(failed, Error) and failed.code == ERR_INTERNAL

        ctx.engine = "codex"
        unsupported = await machine._handle_stop_background_task(
            _command("bash-1"))
        assert isinstance(unsupported, Error)
        assert "暂不支持" in unsupported.message

        missing = await machine._handle_stop_background_task(StopBackgroundTask(
            sid="gone", item_id="bash-1", cmd_id="cmd-2", client_id="client-a"))
        assert isinstance(missing, Error) and missing.code == ERR_NOT_RUNNING
        assert any(isinstance(event, Error) for event in transport.sent)

    asyncio.run(run())


def test_service_client_forwards_stop_as_a_generic_control_request():
    async def run():
        sent = []

        async def call(method, params=None, timeout=None):
            sent.append((method, params))
            return None

        client = RemoteClient.__new__(RemoteClient)
        client.call = call
        await client.stop_task("bash-1")
        assert sent == [("control", {
            "request": {"subtype": "stop_task", "task_id": "bash-1"},
            "timeout": 60,
        })]

    asyncio.run(run())
