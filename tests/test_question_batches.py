"""Native and MCP questions share one atomic, recoverable interaction."""
import asyncio
import json

import pytest
from mcp.types import CallToolRequest, CallToolRequestParams, ListToolsRequest
from pydantic import ValidationError

from cc_remote.protocol import AnswerQuestion, AskQuestion, AskUser, Hello, deserialize, serialize
from cc_remote.wrapper.ask import make_ask_server
from cc_remote.wrapper.claude_questions import AskCancelled, AskTimeout
from tests.test_multisession import _mk_ctx, _mk_machine


def questions():
    return [AskQuestion(question="Target?", options=[{"label": "Mac"}, {"label": "Linux"}]),
            AskQuestion(question="Checks?", options=[{"label": "Tests"}, {"label": "Lint"}],
                        allow_text=True, multi_select=True)]


def test_batch_wire_bounds_and_exclusive_answers():
    qs = questions()
    event = AskUser(ask_id="batch", **qs[0].model_dump(), questions=qs)
    assert deserialize(serialize(event)) == event
    for values in ([], qs * 3):
        with pytest.raises(ValidationError):
            AskUser(ask_id="batch", **qs[0].model_dump(), questions=values)
    with pytest.raises(ValidationError):
        AskUser(ask_id="batch", **qs[1].model_dump(), questions=qs)
    for payload in ({}, {"answer": "a", "answers": ["a"]}, {"answers": []},
                    {"answers": ["a"] * 5}, {"answers": [["a"] * 6]},
                    {"answers": ["x" * 4097]}):
        with pytest.raises(ValidationError):
            AnswerQuestion(ask_id="batch", **payload)


@pytest.mark.asyncio
async def test_batch_rejects_partial_invalid_and_wrong_client_without_advancing():
    machine, transport = _mk_machine()
    ctx = _mk_ctx("batch-session", "batch-session")
    machine.sessions[ctx.key] = ctx
    task = asyncio.create_task(machine._on_ask_batch(ctx, questions(), to="owner"))
    async with asyncio.timeout(2):
        while not ctx.pending_asks:
            await asyncio.sleep(0)
    ask_id = next(iter(ctx.pending_asks))
    for answers, client in [(["Mac"], "owner"), (["Other", ["Tests"]], "owner"),
                            (["Mac", ["Tests", "Tests"]], "owner"),
                            (["Mac", [" "]], "owner"), (["Mac", ["Tests"]], "other")]:
        error = await machine._handle_answer_question(AnswerQuestion(
            sid=ctx.key, ask_id=ask_id, answers=answers, client_id=client))
        assert error.code in {"bad_prompt", "auth"}
        assert ask_id in ctx.pending_asks and not task.done()
    # A hello restores the complete batch, not just its summary/first page.
    await machine._handle_client_hello(Hello(role="client", client_id="owner"))
    restored = [e for e in transport.sent if isinstance(e, AskUser)][-1]
    assert restored.questions == questions() and restored.ask_id == ask_id
    accepted = AnswerQuestion(sid=ctx.key, ask_id=ask_id,
                              answers=["Mac", ["Lint", "Extra audit"]], client_id="owner")
    assert await machine._handle_answer_question(accepted) is None
    assert await asyncio.wait_for(task, 2) == ["Mac", ["Lint", "Extra audit"]]
    assert (await machine._handle_answer_question(accepted)).code == "not_running"
    assert not ctx.pending_asks


@pytest.mark.asyncio
async def test_batch_cancel_and_timeout_release_the_whole_slot():
    machine, _ = _mk_machine()
    ctx = _mk_ctx("batch-session", "batch-session")
    machine.sessions[ctx.key] = ctx
    with pytest.raises(AskTimeout):
        await machine._on_ask_batch(ctx, questions(), timeout=0)
    assert not ctx.pending_asks
    task = asyncio.create_task(machine._on_ask_batch(ctx, questions()))
    async with asyncio.timeout(2):
        while not ctx.pending_asks:
            await asyncio.sleep(0)
    await machine._cancel_pending_asks(ctx)
    with pytest.raises(AskCancelled):
        await task
    assert not ctx.pending_asks and not ctx.ask_lock.locked()


@pytest.mark.asyncio
async def test_real_mcp_handler_accepts_legacy_and_batch_and_rejects_mixed_input():
    called = []

    async def single(question, options):
        called.append((question, options))
        return "custom"

    async def batch(qs):
        called.append(qs)
        return json.dumps({"answers": [{"question": q.question, "answer": "Mac"} for q in qs]})

    async def mode(_):
        pass

    server = make_ask_server(single, mode, ask_batch=batch)
    listed = (await server.request_handlers[ListToolsRequest](ListToolsRequest())).root
    schema = listed.tools[0].inputSchema
    assert "questions" in schema["properties"]
    # Native Claude may drop a tool with root combinators, while keeping its
    # MCP server connected. Do not depend on its optional schema normalizer.
    assert schema["type"] == "object"
    assert not {"oneOf", "anyOf", "allOf"} & schema.keys()

    async def call(arguments):
        request = CallToolRequest(params=CallToolRequestParams(name="ask_user", arguments=arguments))
        return (await server.request_handlers[CallToolRequest](request)).root

    legacy = {"question": "Target?", "options": [{"label": "Mac"}, {"label": "Linux"}]}
    assert (await call(legacy)).content[0].text == "custom"
    group = {"questions": [{**legacy, "multiSelect": True}]}
    result = await call(group)
    assert not result.isError
    assert json.loads(result.content[0].text)["answers"][0]["question"] == "Target?"
    assert called[-1][0].multi_select and called[-1][0].allow_text
    count = len(called)
    for args in ({}, {"question": "Target?"}, {"options": legacy["options"]},
                 {**legacy, "extra": True}, {**group, "extra": True},
                 {**legacy, **group}, {"questions": []}, {"questions": group["questions"] * 5},
                 {"questions": [{**legacy, "multiSelect": "yes"}]}):
        result = await call(args)
        assert result.isError or "rejected" in result.content[0].text
        assert len(called) == count


@pytest.mark.asyncio
async def test_mcp_batch_runs_through_machine_and_returns_ordered_answers():
    machine, transport = _mk_machine()
    ctx = _mk_ctx("mcp-batch", "mcp-batch")
    machine.sessions[ctx.key] = ctx

    async def mode(_):
        pass

    server = make_ask_server(
        lambda q, opts: machine._on_mcp_ask(ctx, q, opts), mode,
        ask_batch=lambda qs: machine._on_mcp_ask_batch(ctx, qs))
    payload = {"questions": [
        {"question": "Order?", "options": [{"label": "Recent"}, {"label": "Name"}]},
        {"question": "Include?", "options": [{"label": "Images"}, {"label": "Docs"}], "multiSelect": True},
    ]}
    task = asyncio.create_task(server.request_handlers[CallToolRequest](CallToolRequest(
        params=CallToolRequestParams(name="ask_user", arguments=payload))))
    async with asyncio.timeout(2):
        while not ctx.pending_asks:
            await asyncio.sleep(0)
    event = next(event for event in transport.sent if isinstance(event, AskUser))
    assert len(event.questions) == 2 and not task.done()
    await machine._handle_answer_question(AnswerQuestion(
        sid=ctx.key, ask_id=event.ask_id, answers=["Name", ["Images", "Docs"]]))
    result = (await asyncio.wait_for(task, 2)).root
    assert json.loads(result.content[0].text) == {"answers": [
        {"question": "Order?", "answer": "Name"},
        {"question": "Include?", "answer": ["Images", "Docs"]},
    ]}
