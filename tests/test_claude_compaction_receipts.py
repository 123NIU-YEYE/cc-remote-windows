"""Compaction summaries cannot consume or rename an accepted human input."""

import asyncio
from unittest.mock import AsyncMock

import pytest
from claude_agent_sdk.types import ResultMessage

from cc_remote.claude_steering import PendingSteers, is_human_result, is_managed_input
from cc_remote.config import WrapperConfig
from cc_remote.wrapper.sdk import SdkHandle
from cc_remote.wrapper.stream import replayed_user_message_id
from tests.test_claude_autocompact import _machine_with_sdk
from tests.test_claude_service import environment, released
from tests.test_claude_steering import NativeClient, assistant, result, until, user

HUMAN = "11111111-1111-4111-8111-111111111111"
GUIDE = "22222222-2222-4222-8222-222222222222"
SUMMARY = "33333333-3333-4333-8333-333333333333"


def summary(flag="isSynthetic"):
    # The live SDK envelope uses isSynthetic; native JSONL uses isCompactSummary.
    # The marker, not a particular language or prefix, identifies the summary.
    return {**user(SUMMARY, [{"type": "text", "text": "Context summary"}]), flag: True}


@pytest.mark.parametrize("flag", ["isSynthetic", "isCompactSummary"])
def test_summary_preserves_latest_receipt_and_cannot_consume_a_pending_steer(flag):
    pending = PendingSteers()
    pending.annotate(user(HUMAN), managed_active=True)
    pending.add(SUMMARY, {"id": "pending-guide"})
    value = pending.annotate(summary(flag), managed_active=True)
    assert not is_managed_input(value, pending_compact=True)
    assert "__cc_steer" not in value and SUMMARY in pending.pending
    assert replayed_user_message_id(SdkHandle._parse_compat_message(value)) is None
    value = pending.annotate({**result(), "user_message_uuid": HUMAN}, managed_active=True)
    assert is_human_result(value) and value["__cc_steer_intermediate"]
    # A genuine human message with the same text still owns its own receipt.
    echo = pending.annotate(user(SUMMARY, "Context summary"), managed_active=True)
    assert echo["__cc_steer"]["id"] == "pending-guide"
    assert replayed_user_message_id(SdkHandle._parse_compat_message(echo)) == SUMMARY
    value = pending.annotate({**result(), "user_message_uuid": SUMMARY}, managed_active=True)
    assert is_human_result(value) and not value.get("__cc_steer_intermediate")


@pytest.mark.asyncio
@pytest.mark.parametrize("persistent", [False, True])
@pytest.mark.parametrize("steered", [False, True])
async def test_autocompact_result_completes_the_original_turn(persistent, steered):
    async with environment() as (service, attach):
        client = await attach() if persistent else NativeClient()
        worker = service.sessions[client.id] if persistent else None
        native = worker.client if worker else client
        sdk = SdkHandle(WrapperConfig())
        sdk.client = client
        sdk._start_message_pump()
        consumer = None
        try:
            sdk.next_turn_id = "root"
            await sdk.query("work")

            async def read():
                return [message async for message in sdk.receive_response()]

            consumer = asyncio.create_task(read())
            await native.queue.put(user(HUMAN, "work"))
            await until(lambda: sdk._managed_input_seen)
            receipt = HUMAN
            if steered:
                await sdk.steer("guide", native_id=GUIDE, metadata={"id": "guide-ui"})
                await native.queue.put(user(GUIDE, "guide"))
                await until(lambda: not sdk._steers.pending)
                receipt = GUIDE
            await native.queue.put({"type": "system", "subtype": "compact_boundary",
                                    "compact_metadata": {"trigger": "auto", "pre_tokens": 500000}})
            await native.queue.put(summary())
            await native.queue.put(assistant("answer", [{"type": "text", "text": "done"}]))
            await native.queue.put({**result(), "user_message_uuid": receipt,
                                    "user_message_uuids": [receipt]})
            messages = await asyncio.wait_for(consumer, 2)
            assert isinstance(messages[-1], ResultMessage)
            assert [mid for m in messages if (mid := replayed_user_message_id(m))] == (
                [HUMAN, GUIDE] if steered else [HUMAN])
            if worker:
                assert worker.terminal_seq is not None and worker.turn["id"] == "root"
            await sdk.ack_service_message(messages[-1], turn_id="root")
            sdk.release_background_messages()
            if worker:
                assert worker.turn is None
            assert not sdk._turn_active and native.interrupts == 0
        finally:
            if consumer:
                consumer.cancel()
                await asyncio.gather(consumer, return_exceptions=True)
            await sdk._stop_message_pump()


@pytest.mark.asyncio
async def test_summary_before_human_echo_does_not_adopt_older_background_output():
    native = NativeClient()
    sdk = SdkHandle(WrapperConfig())
    sdk.client = native
    seen = []

    async def background(message, origin):
        seen.append(message)

    sdk.background_message_callback = background
    sdk._start_message_pump()
    try:
        await sdk.query("new work")
        await native.queue.put(summary())
        await native.queue.put(assistant("older", [{"type": "text", "text": "older output"}]))
        await until(lambda: len(seen) == 2)
        assert not sdk._managed_input_seen and sdk._turn_messages.empty()
        await native.queue.put(user(HUMAN))
        await native.queue.put({**result(), "user_message_uuid": HUMAN})
        async with asyncio.timeout(2):
            messages = [m async for m in sdk.receive_response()]
        assert isinstance(messages[-1], ResultMessage)
    finally:
        await sdk._stop_message_pump()


@pytest.mark.asyncio
async def test_compacted_completion_replays_after_detach_without_resubmitting():
    async with environment() as (service, attach):
        first = await attach()
        worker = service.sessions[first.id]
        native = worker.client
        first.next_turn = {"id": "root", "prompt": "work"}
        await first.query("work")
        await native.queue.put(user(HUMAN))
        await until(lambda: worker.managed_input_seen)
        await first.detach()
        await released(worker)
        await native.queue.put(summary())
        await native.queue.put(assistant("answer", [{"type": "text", "text": "done"}]))
        await native.queue.put({**result(), "user_message_uuid": HUMAN})
        await until(lambda: worker.journal.seq >= 4)

        sdk = SdkHandle(WrapperConfig(claude_service_socket=first.connection.socket_path))
        sdk.service_defer_events = True
        machine, transport, ctx = _machine_with_sdk(sdk)
        machine._configure_claude_sdk_callbacks(ctx, sdk)
        sdk.refresh_goal = AsyncMock(return_value=None)
        sdk.service_metadata = worker.metadata.copy()
        try:
            await sdk.connect(resume_id="native-session", cwd="/tmp")
            from cc_remote.wrapper.claude_service import activate
            await activate(machine, ctx)
            await until(lambda: worker.turn is None)
            await until(lambda: ctx.turn_task is None)
            assert ctx.state == "idle"
            assert sum(e.type == "turn_end" for e in transport.sent) == 1
            assert len(native.prompts) == 1 and native.interrupts == 0
        finally:
            if ctx.turn_task:
                ctx.turn_task.cancel()
                await asyncio.gather(ctx.turn_task, return_exceptions=True)
            await sdk.detach_for_shutdown()
