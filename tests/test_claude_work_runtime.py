"""Work sandbox startup rejection stays explicit, private and query-free."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from cc_remote.protocol import NewSession
from cc_remote.wrapper import sdk as sdk_module
from cc_remote.wrapper.claude_work_runtime import (
    ClaudeWorkSandboxUnavailable,
    WORK_SANDBOX_UNAVAILABLE,
    WORK_SANDBOX_UNAVAILABLE_MESSAGE,
)
from cc_remote.wrapper.sdk import SdkHandle
from tests.test_multisession import _mk_ctx, _mk_machine

SANDBOX_FAILURE = (
    "Claude Code returned an error result: Sandbox required but unavailable: "
    "the Windows sandbox is not active on this session (feature gate off). "
    "Set sandbox.failIfUnavailable=false to allow unsandboxed execution."
)


@pytest.mark.parametrize("work_mode", [False, True])
@pytest.mark.parametrize("diagnostic", [SANDBOX_FAILURE, "permission denied", "authentication failed"])
def test_only_explicit_work_sandbox_rejection_is_classified(monkeypatch, work_mode, diagnostic):
    failure = RuntimeError(diagnostic)

    class Client:
        def __init__(self, **kwargs):
            pass

        async def connect(self):
            raise failure

    monkeypatch.setattr(sdk_module, "ClaudeSDKClient", Client)

    async def run():
        machine, _ = _mk_machine()
        handle = SdkHandle(machine.cfg)
        handle.work_mode = work_mode
        with pytest.raises(RuntimeError) as caught:
            await handle.connect(cwd=str(machine.cfg.state_dir))
        if work_mode and diagnostic == SANDBOX_FAILURE:
            assert isinstance(caught.value, ClaudeWorkSandboxUnavailable)
            assert str(caught.value) == WORK_SANDBOX_UNAVAILABLE_MESSAGE
            assert caught.value.__cause__ is failure
        else:
            assert caught.value is failure

    asyncio.run(run())


@pytest.mark.parametrize("cleanup_fails", [False, True])
def test_failed_work_creation_cleans_provisional_state_and_correlates_error(monkeypatch, cleanup_fails):
    clients = []

    class Client:
        def __init__(self, options, **kwargs):
            self.options = options
            self.connect = AsyncMock(side_effect=RuntimeError(SANDBOX_FAILURE))
            self.disconnect = AsyncMock(side_effect=RuntimeError("cleanup failed") if cleanup_fails else None)
            self.query = AsyncMock()
            clients.append(self)

    monkeypatch.setattr(sdk_module, "ClaudeSDKClient", Client)
    monkeypatch.setattr(SdkHandle, "preflight", staticmethod(lambda _: None))

    async def run():
        machine, transport = _mk_machine()
        old = _mk_ctx("old-session", "old-session")
        machine.sessions[old.key] = old
        machine.focused_sid = old.key
        machine._run_turn = AsyncMock()
        store = machine._work.for_engine("claude")
        created = []
        create_session = store.create_session

        def capture_create(*args, **kwargs):
            record = create_session(*args, **kwargs)
            created.append(record)
            return record

        monkeypatch.setattr(store, "create_session", capture_create)
        error = await machine._handle_new_session(NewSession(
            engine="claude", space="work", prompt="hello", msg_id="message-work",
            request_id="create-work", client_id="browser-work",
        ))

        assert transport.sent == [error]
        assert error.code == WORK_SANDBOX_UNAVAILABLE
        assert error.message == WORK_SANDBOX_UNAVAILABLE_MESSAGE
        assert error.request_id == "create-work" and error.to == "browser-work"
        assert error.sid is None
        assert machine.focused_sid == old.key
        assert machine.sessions == {old.key: old}
        assert old.buffer.tail_seq == 0
        machine._run_turn.assert_not_awaited()
        assert len(clients) == len(created) == 1
        clients[0].connect.assert_awaited_once()
        clients[0].disconnect.assert_awaited_once()
        clients[0].query.assert_not_awaited()
        record = created[0]
        assert store.get_by_work_id(record.work_id) is None
        assert not Path(record.cwd).exists()
        assert not Path(clients[0].options.settings).exists()
        assert clients[0].options.setting_sources == []
        assert "safe-mode" in clients[0].options.extra_args

    asyncio.run(run())


def test_work_bootstrap_sandbox_rejection_never_retries_a_fresh_session(monkeypatch):
    async def run():
        machine, transport = _mk_machine()
        store = machine._work.for_engine("claude")
        profile_id = machine._claude_profiles.default.id
        record = store.create_session(claude_profile_id=profile_id)
        store.bind_session(record.work_id, "native-work", claude_profile_id=profile_id)
        monkeypatch.setattr(SdkHandle, "preflight", staticmethod(lambda _: None))
        connect = AsyncMock(side_effect=ClaudeWorkSandboxUnavailable())
        disconnect = AsyncMock()
        monkeypatch.setattr(SdkHandle, "connect", connect)
        monkeypatch.setattr(SdkHandle, "disconnect", disconnect)
        # Only the native catalog lookup is stubbed; the real Work policy and
        # spawn/reject routing run, including bootstrap's normal retry branch.
        monkeypatch.setattr("cc_remote.wrapper.machine.get_session_info", lambda _: type(
            "SessionInfo", (), {"cwd": record.cwd, "model": None})())
        ctx = await machine._spawn(
            resume_id="native-work", engine="claude", space="work", bootstrap=True)
        assert ctx is None and not machine.sessions
        connect.assert_awaited_once()
        assert connect.await_args.kwargs["resume_id"] == "native-work"
        disconnect.assert_awaited_once()
        errors = [event for event in transport.sent if event.type == "error"]
        assert len(errors) == 1 and errors[0].sid == "native-work"
        assert errors[0].code == WORK_SANDBOX_UNAVAILABLE
        assert store.get_by_session("native-work") is not None
        policy = json.loads((store.root / "policies" / f"{record.work_id}.json").read_text())
        assert policy["sandbox"]["enabled"] is True
        assert policy["sandbox"]["failIfUnavailable"] is True

    asyncio.run(run())
