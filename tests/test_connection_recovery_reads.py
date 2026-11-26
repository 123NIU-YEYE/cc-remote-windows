"""Slow native metadata cannot hold a reconnecting browser's handshake."""
import asyncio

import pytest

from cc_remote.protocol import GetClaudePlan, GetGoal, Hello, SwitchSession
from tests.test_multisession import _mk_machine


@pytest.mark.parametrize("read", [
    Hello(role="client", client_id="browser"),
    GetGoal(sid="session", client_id="browser", cmd_id="read-goal"),
    GetClaudePlan(sid="session", client_id="browser", cmd_id="read-plan"),
])
def test_metadata_and_hello_do_not_wait_for_slow_switch(monkeypatch, read):
    async def scenario():
        machine, _ = _mk_machine()
        entered, release, received = (asyncio.Event() for _ in range(3))

        async def slow(_command):
            entered.set()
            await release.wait()

        async def independent(_command):
            received.set()

        monkeypatch.setattr(machine, "_handle_switch_session", slow)
        handler = ("_handle_client_hello" if read.type == "hello"
                   else "_handle_" + read.type)
        monkeypatch.setattr(machine, handler, independent)
        try:
            await machine._dispatch_incoming_command(SwitchSession(session_id="cold"))
            await asyncio.wait_for(entered.wait(), 1)
            await machine._dispatch_incoming_command(read)
            await asyncio.wait_for(received.wait(), 1)
            assert not release.is_set()
        finally:
            release.set()
            await machine._command_scheduler.close()
            tasks = list(machine._history_command_tasks.values())
            await asyncio.gather(*tasks, return_exceptions=True)

    asyncio.run(scenario())

@pytest.mark.parametrize("command", [
    GetGoal(sid="session", client_id="browser", cmd_id="goal-timeout"),
    GetClaudePlan(sid="session", client_id="browser", cmd_id="plan-timeout"),
])
def test_read_timeout_has_correlated_reply_and_ack(monkeypatch, command):
    async def scenario():
        machine, transport = _mk_machine()

        async def expired(_command):
            raise TimeoutError()

        monkeypatch.setattr(machine, "_process_command", expired)
        await machine._process_command_safely(command)
        errors = [frame for frame in transport.sent if frame.type == "error"]
        assert len(errors) == 1
        assert errors[0].to == "browser"
        assert errors[0].request_id == command.cmd_id
        assert any(frame.type == "command_ack" and frame.cmd_id == command.cmd_id
                   for frame in transport.sent)

    asyncio.run(scenario())
