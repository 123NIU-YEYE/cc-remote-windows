"""Backpressure and slow bodies have finite deadlines without replaying writes."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import httpx
import pytest

from cc_remote.wrapper import codex_handle, dsh_client
from cc_remote.wrapper.codex_handle import CodexHandle
from cc_remote.wrapper.dsh_client import DshError
from tests.test_dsh_client import Host


@pytest.mark.asyncio
@pytest.mark.parametrize("blocked_at", ["send", "response"])
async def test_codex_request_deadline_includes_blocked_send(monkeypatch, blocked_at):
    # Isolate the actual request bookkeeping from native process/model setup.
    handle = CodexHandle.__new__(CodexHandle)
    handle._id = 0
    handle._pending = {}
    handle._pending_response_boundaries = {}
    sent = []
    cancelled = asyncio.Event()

    async def send(obj):
        sent.append(obj)
        if blocked_at == "send":
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

    released = []
    monkeypatch.setattr(handle, "_send", send)
    monkeypatch.setattr(codex_handle, "_REQ_TIMEOUT", 0.02)
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(handle._request(
            "turn/start", {"threadId": "existing"},
            response_boundary=SimpleNamespace(release=lambda: released.append(True)),
        ), 1)
    assert len(sent) == 1, "a stalled mutating request must never be resubmitted"
    assert cancelled.is_set() == (blocked_at == "send")
    assert handle._pending == handle._pending_response_boundaries == {}
    assert released == [True]


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint", ["session/list", "session/prompt", "metadata"])
async def test_dsh_slow_response_has_total_deadline_and_preserves_unknown_writes(monkeypatch, endpoint):
    host = Host()
    client = host.client()
    closed = asyncio.Event()
    posted = []

    class Body(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'{'
            await asyncio.Event().wait()

        async def aclose(self):
            closed.set()

    def request(req):
        if req.url.path == "/connect":
            return host.request(req)
        posted.append(req.url.path)
        return httpx.Response(200, stream=Body())

    client._http_transport = httpx.MockTransport(request)
    monkeypatch.setattr(dsh_client, "RPC_TIMEOUT", 0.02)
    try:
        with pytest.raises(DshError) as error:
            operation = client.read_route("/api/cc-remote.metadata", {}) if endpoint == "metadata" else client.rpc(endpoint)
            await asyncio.wait_for(operation, 1)
        assert error.value.code == ("read_failed" if endpoint == "metadata" else "request_failed")
        assert error.value.outcome_unknown == (endpoint == "session/prompt")
        assert posted == ["/api/cc-remote.metadata" if endpoint == "metadata" else "/api/" + endpoint]
        assert closed.is_set(), "timed-out streamed HTTP bodies must be closed"
    finally:
        await client.close()
