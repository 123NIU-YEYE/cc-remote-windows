"""Read-ahead changes latency, never the official history/cursor contract."""
from __future__ import annotations

import asyncio
from copy import deepcopy

import pytest

from cc_remote.wrapper import codex_history_prefetch as prefetch_module
from cc_remote.wrapper.codex_history import (
    CodexHistoryCursorError, CodexOfficialHistory,
)
from cc_remote.wrapper.codex_history_prefetch import CodexHistoryPrefetch
from cc_remote.wrapper.codex_rpc import CodexRpcRejected
from cc_remote.wrapper.history_store import HistorySourceFingerprint
from cc_remote.protocol import GetHistory, History
from tests.test_codex_history import _agent, _turn, _user
from tests.test_multisession import _mk_ctx, _mk_machine


def _page(number: int = 1, *, status: str = "completed") -> dict:
    return {"data": [_turn(
        f"native-{number}", [_user(f"user-{number}", f"question {number}"),
                             _agent(f"answer-{number}", "**answer**")], status=status,
    )], "nextCursor": f"opaque-{number - 1}" if number else None}


async def _drain(cache: CodexHistoryPrefetch) -> None:
    await asyncio.gather(*(task for _source, task in cache._tasks.values()))
    await asyncio.sleep(0)  # flush the task's forget callback


def test_prefetch_is_raw_and_requested_projection_matches_uncached(tmp_path, monkeypatch):
    monkeypatch.setattr(prefetch_module, "_MIN_PREFETCH_SOURCE_BYTES", 0)
    path = tmp_path / "rollout.jsonl"
    path.write_text("frozen source\n")
    source = HistorySourceFingerprint.capture(path)
    calls = []

    async def rpc(method, params, cwd=None):
        calls.append((method, dict(params)))
        assert method == "thread/turns/list"
        return _page(2 if params["cursor"] is None else 1)

    async def run():
        reader = CodexOfficialHistory(65536, rpc=rpc)
        head = await reader.summary_page("profile@thread", before=None, limit=4, source=source)
        reader.prefetch_summary_page("profile@thread", head.oldest_id)
        await _drain(reader._prefetch)
        assert len(calls) == 2
        assert calls[-1][1] == {
            "threadId": "profile@thread", "cursor": "opaque-1", "limit": 12,
            "sortDirection": "desc", "itemsView": "summary",
        }
        # Read-ahead must not expose cursors, turn events or identities to the UI.
        assert reader.summary_events("profile@thread", "user-1") is None
        assert ("profile@thread", "user-1") not in reader._before_cursors
        cached = await reader.summary_page(
            "profile@thread", before=head.oldest_id, limit=12, source=source)
        assert len(calls) == 2
        baseline = CodexOfficialHistory(65536, rpc=rpc)
        await baseline.summary_page("profile@thread", before=None, limit=4)
        uncached = await baseline.summary_page("profile@thread", before=head.oldest_id, limit=12)
        assert cached == uncached
        # Only an explicit request can advance read-ahead; there is no crawl.
        assert not reader._prefetch._tasks
        await reader.close()

    asyncio.run(run())


@pytest.mark.parametrize("mutation", ["append", "rewrite", "truncate", "replace"])
def test_any_source_change_rejects_cached_page(tmp_path, mutation):
    path = tmp_path / "rollout.jsonl"
    path.write_text("old data\n")
    source = HistorySourceFingerprint.capture(path)
    calls = []

    async def fetch(*args):
        calls.append(args)
        return _page(len(calls))

    async def run():
        cache = CodexHistoryPrefetch(fetch, CodexOfficialHistory._cacheable_summary)
        first = await cache.read("thread", "opaque", 12, source)
        assert await cache.read("thread", "opaque", 12, source) == first
        if mutation == "replace":
            replacement = tmp_path / "new.jsonl"
            replacement.write_text("new data\n")
            replacement.replace(path)
        elif mutation == "append":
            with path.open("a") as file:
                file.write("new data\n")
        else:
            path.write_text("new data\n" if mutation == "rewrite" else "")
        current = HistorySourceFingerprint.capture(path)
        assert current != source
        assert await cache.read("thread", "opaque", 12, current) != first
        assert len(calls) == 2
        await cache.close()

    asyncio.run(run())


def test_foreground_joins_pending_prefetch_without_duplicate_rpc(tmp_path, monkeypatch):
    monkeypatch.setattr(prefetch_module, "_MIN_PREFETCH_SOURCE_BYTES", 0)
    path = tmp_path / "rollout.jsonl"
    path.write_text("source\n")
    source = HistorySourceFingerprint.capture(path)

    async def run():
        started, release = asyncio.Event(), asyncio.Event()
        calls = []

        async def fetch(*args):
            calls.append(args)
            started.set()
            await release.wait()
            return _page()

        cache = CodexHistoryPrefetch(fetch, CodexOfficialHistory._cacheable_summary)
        cache.prefetch("thread", "opaque", 12, source)
        await started.wait()
        request = asyncio.create_task(cache.read("thread", "opaque", 12, source))
        await asyncio.sleep(0)
        assert not request.done() and len(calls) == 1
        release.set()
        assert await request == _page()
        assert len(calls) == 1
        await cache.close()

    asyncio.run(run())


def test_prefetch_failure_does_not_hide_or_cache_foreground_error(tmp_path, monkeypatch):
    monkeypatch.setattr(prefetch_module, "_MIN_PREFETCH_SOURCE_BYTES", 0)
    path = tmp_path / "rollout.jsonl"
    path.write_text("source\n")
    source = HistorySourceFingerprint.capture(path)
    calls = []

    async def fetch(*args):
        calls.append(args)
        raise CodexRpcRejected("read failed", code=-32000)

    async def run():
        cache = CodexHistoryPrefetch(fetch, CodexOfficialHistory._cacheable_summary)
        cache.prefetch("thread", "opaque", 12, source)
        await _drain(cache)
        assert not cache._pages
        with pytest.raises(CodexRpcRejected):
            await cache.read("thread", "opaque", 12, source)
        assert len(calls) == 2
        await cache.close()

    asyncio.run(run())


@pytest.mark.parametrize("kind", ["moving", "missing_source", "head", "changed_during_read"])
def test_unproven_or_live_pages_are_not_reused(tmp_path, kind):
    path = tmp_path / "rollout.jsonl"
    path.write_text("source\n")
    source = HistorySourceFingerprint.capture(path)
    calls = []

    async def fetch(*args):
        calls.append(args)
        if kind == "changed_during_read":
            path.write_text("changed source\n")
        return _page(status="inProgress" if kind == "moving" else "completed")

    async def run():
        cache = CodexHistoryPrefetch(fetch, CodexOfficialHistory._cacheable_summary)
        for _ in range(2):
            await cache.read("thread", None if kind == "head" else "opaque", 12,
                             None if kind == "missing_source" else source)
        assert len(calls) == 2 and not cache._pages
        await cache.close()

    asyncio.run(run())


def test_cache_bounds_isolation_copy_and_invalidation(tmp_path, monkeypatch):
    monkeypatch.setattr(prefetch_module, "_MAX_PAGES", 2)
    path = tmp_path / "rollout.jsonl"
    path.write_text("source\n")
    source = HistorySourceFingerprint.capture(path)
    calls = []

    async def fetch(*args):
        calls.append(args)
        return _page()

    async def run():
        cache = CodexHistoryPrefetch(fetch, CodexOfficialHistory._cacheable_summary)
        for thread in ("one@thread", "two@thread", "three@thread"):
            await cache.read(thread, "opaque", 12, source)
        assert len(cache._pages) == 2
        again = await cache.read("three@thread", "opaque", 12, source)
        again["data"].clear()
        assert (await cache.read("three@thread", "opaque", 12, source))["data"]
        assert len(calls) == 3
        cache.invalidate("two@thread")
        assert len(cache._pages) == 1
        await cache.read("two@thread", "opaque", 12, source)
        assert len(calls) == 4
        await cache.read("two@thread", "opaque", 4, source)
        assert len(calls) == 5  # page size is part of the exact cache key
        assert cache._bytes == sum(page.size for page in cache._pages.values())
        monkeypatch.setattr(prefetch_module, "_MAX_BYTES", 1)
        await cache.read("oversized", "opaque", 12, source)
        assert not any(key[0] == "oversized" for key in cache._pages)
        await cache.close()
        assert cache._bytes == 0 and not cache._pages

    asyncio.run(run())


@pytest.mark.parametrize("stop", ["invalidate", "close", "disconnect"])
def test_cancellation_does_not_publish_stale_cache_or_cancel_other_requesters(
    tmp_path, monkeypatch, stop,
):
    monkeypatch.setattr(prefetch_module, "_MIN_PREFETCH_SOURCE_BYTES", 0)
    path = tmp_path / "rollout.jsonl"
    path.write_text("source\n")
    source = HistorySourceFingerprint.capture(path)

    async def run():
        started, release = asyncio.Event(), asyncio.Event()
        calls = []

        async def fetch(*args):
            calls.append(args)
            started.set()
            await release.wait()
            return _page()

        cache = CodexHistoryPrefetch(fetch, CodexOfficialHistory._cacheable_summary)
        cache.prefetch("thread", "opaque", 12, source)
        await started.wait()
        foreground = asyncio.create_task(cache.read("thread", "opaque", 12, source))
        await asyncio.sleep(0)
        if stop == "disconnect":
            foreground.cancel()
            with pytest.raises(asyncio.CancelledError):
                await foreground
            release.set()
            await _drain(cache)
            assert await cache.read("thread", "opaque", 12, source) == _page()
            assert len(calls) == 1
        else:
            if stop == "invalidate":
                cache.invalidate("thread")
            else:
                await cache.close()
            assert not cache._pages
            release.set()
            assert await foreground == _page()
            assert len(calls) == 2
        await cache.close()

    asyncio.run(run())


def test_reader_invalidation_drops_cursor_and_read_ahead_source(tmp_path):
    path = tmp_path / "rollout.jsonl"
    path.write_text("source\n")

    async def rpc(*args):
        return _page()

    async def run():
        reader = CodexOfficialHistory(65536, rpc=rpc)
        page = await reader.summary_page(
            "thread", before=None, limit=4, source=HistorySourceFingerprint.capture(path))
        reader.invalidate_thread("thread")
        assert "thread" not in reader._read_sources
        with pytest.raises(CodexHistoryCursorError):
            await reader.summary_page("thread", before=page.oldest_id, limit=12)
        await reader.close()

    asyncio.run(run())


@pytest.mark.parametrize("bad_locator", [False, True])
@pytest.mark.parametrize("server_page_cap", [1, 200])
def test_detail_uses_one_skip_page_and_keeps_exact_native_identity(bad_locator, server_page_cap):
    calls = []
    rows = [_turn(f"native-{n}", [_user(f"user-{n}", "question"), _agent(f"a-{n}", "answer")])
            for n in range(12)]

    async def rpc(method, params, cwd=None):
        calls.append((method, deepcopy(params)))
        if method == "thread/items/list":
            raise CodexRpcRejected("unsupported", code=-32601)
        if params["itemsView"] == "summary":
            return {"data": deepcopy(rows), "nextCursor": "next"}
        if params["itemsView"] == "notLoaded":
            offset = int(params["cursor"]) if params["cursor"] is not None else 0
            assert params["limit"] == 11 - offset
            end = min(11, offset + server_page_cap)
            return {"data": [{**row, "items": [], "itemsView": "notLoaded"}
                             for row in rows[offset:end]],
                    "nextCursor": "target" if end == 11 else str(end)}
        assert params["itemsView"] == "full" and params["cursor"] == "target"
        return {"data": [{**rows[0 if bad_locator else 11], "itemsView": "full"}],
                "nextCursor": "next"}

    async def run():
        reader = CodexOfficialHistory(65536, rpc=rpc)
        await reader.summary_page("thread", before=None, limit=12)
        if bad_locator:
            from cc_remote.wrapper.codex_history import CodexHistoryUnsupported
            with pytest.raises(CodexHistoryUnsupported):
                await reader.turn_events("thread", "user-11")
        else:
            events = await reader.turn_events("thread", "user-11")
            assert events[0]["msg_id"] == "user-11"
        # summary, unsupported items, bounded skip pages, exact full
        assert len(calls) == (14 if server_page_cap == 1 else 4)
        await reader.close()

    asyncio.run(run())


@pytest.mark.parametrize("response", [
    {"data": [], "nextCursor": "still-more"},
    {"data": [{"id": "bad"}], "nextCursor": None},
    {"data": _page()["data"] * 2, "nextCursor": None},
])
def test_malformed_summaries_are_never_cached(response):
    assert not CodexOfficialHistory._cacheable_summary(response, 12)


def test_small_sources_do_not_start_speculative_work(tmp_path):
    path = tmp_path / "rollout.jsonl"
    path.write_text("source\n")

    async def fetch(*args):
        pytest.fail("small history did not need speculative I/O")

    async def run():
        cache = CodexHistoryPrefetch(fetch, CodexOfficialHistory._cacheable_summary)
        cache.prefetch("thread", "opaque", 12, HistorySourceFingerprint.capture(path))
        assert not cache._tasks
        await cache.close()

    asyncio.run(run())


@pytest.mark.parametrize("scenario", [
    "idle", "running", "claude", "error", "moving", "rollout", "background",
])
def test_machine_only_prefetches_after_explicit_idle_official_history_delivery(
    monkeypatch, scenario,
):
    async def run():
        machine, transport = _mk_machine()
        ctx = _mk_ctx("thread", "thread")
        ctx.engine = "claude" if scenario == "claude" else "codex"
        machine.sessions[ctx.key] = ctx
        calls = []

        class Reader:
            def prefetch_summary_page(self, sid, before):
                assert isinstance(transport.sent[-1], History)
                calls.append((sid, before))

        async def build(*_args, **_kwargs):
            return History(
                session_id=ctx.key, detail="summary", has_more=True, oldest_id="user-1",
                revision=machine._history_revision(ctx.key),
                authoritative=scenario != "moving", in_progress=scenario == "running",
                error="unavailable" if scenario == "error" else None,
            )

        machine._codex_history = Reader()
        monkeypatch.setattr(machine, "_build_requested_history", build)
        monkeypatch.setattr(machine, "_codex_rollout_history_active", lambda _sid: scenario == "rollout")
        if scenario == "background":
            machine._schedule_history_refresh(
                ctx.key, before=None, limit=4, cwd=None, detail="summary")
            await asyncio.gather(*machine._history_refresh_tasks.values())
        else:
            await machine._handle_get_history(GetHistory(session_id=ctx.key, detail="summary"))
        assert any(isinstance(frame, History) for frame in transport.sent)
        assert calls == ([(ctx.key, "user-1")] if scenario == "idle" else [])

    asyncio.run(run())


def test_only_one_speculative_request_per_reader_and_close_stops_new_work(tmp_path, monkeypatch):
    monkeypatch.setattr(prefetch_module, "_MIN_PREFETCH_SOURCE_BYTES", 0)
    path = tmp_path / "rollout.jsonl"
    path.write_text("source\n")
    source = HistorySourceFingerprint.capture(path)

    async def run():
        started = asyncio.Event()
        calls = []

        async def fetch(*args):
            calls.append(args)
            started.set()
            await asyncio.Event().wait()

        cache = CodexHistoryPrefetch(fetch, CodexOfficialHistory._cacheable_summary)
        cache.prefetch("thread", "opaque", 12, source)
        await started.wait()
        cache.prefetch("thread", "another-cursor", 12, source)
        cache.prefetch("another-thread", "opaque", 12, source)
        assert len(cache._tasks) == 1 and len(calls) == 1
        await cache.close()
        cache.prefetch("thread", "opaque", 12, source)
        assert not cache._tasks and len(calls) == 1

    asyncio.run(run())


def test_materialized_prefetch_is_reused_and_refreshes_live_state(tmp_path, monkeypatch):
    from dataclasses import replace
    from types import SimpleNamespace
    from cc_remote.wrapper.history_metrics import history_profile

    path = tmp_path / 'rollout'
    path.write_text('fixture\n')
    source = replace(HistorySourceFingerprint.capture(path), size=32 * 1024 * 1024)

    async def run():
        machine, _transport = _mk_machine()
        ctx = _mk_ctx('thread', 'thread')
        ctx.engine = 'codex'
        machine.sessions[ctx.key] = ctx
        locators_valid = True
        machine._codex_history = SimpleNamespace(can_restore_page=lambda *_a: locators_valid)
        started, release = asyncio.Event(), asyncio.Event()
        calls = []
        async def read_source(*args):
            return source
        async def read(sid, **kwargs):
            calls.append(kwargs)
            if history_profile.get().prefetch:
                started.set()
                await release.wait()
            return History(session_id=sid, revision=machine._history_revision(sid),
                detail='summary', before=kwargs['before'], has_more=True, oldest_id='old',
                turns=[{'id': 'old', 'prompt': 'previous', 'done': True, 'forkPointId': 'native'}])
        monkeypatch.setattr(machine, '_history_page_source', read_source)
        monkeypatch.setattr(machine, '_read_requested_history', read)
        head = History(session_id='thread', revision=machine._history_revision('thread'),
                       detail='summary', oldest_id='cursor', has_more=True)
        machine._prefetch_finished_history_page('thread', head)
        await started.wait()
        async def foreground():
            return await machine._build_requested_history('thread', before='cursor', limit=12,
                                                          cwd=None, detail='summary')
        pending = asyncio.create_task(foreground())
        await asyncio.sleep(0)
        release.set()
        first = await pending
        assert len(calls) == 1 and first.turns[0].id == 'old'
        ctx.state = 'running'
        ctx.seq = 123
        cached = await foreground()
        assert len(calls) == 1 and cached.in_progress and cached.live_seq == 123
        # Native locator eviction requires a rebuild even if narrative bytes survive.
        locators_valid = False
        await foreground()
        assert len(calls) == 2
        locators_valid = True
        machine._bump_history_revision('thread')
        await foreground()
        assert len(calls) == 3
        assert machine._history_page_prefetch[1].done()  # no recursive crawl
    asyncio.run(run())


def test_cancelled_prefetch_does_not_cancel_foreground_history(tmp_path, monkeypatch):
    from dataclasses import replace
    from types import SimpleNamespace
    from cc_remote.wrapper.history_metrics import history_profile
    path = tmp_path / 'rollout'
    path.write_text('fixture\n')
    source = replace(HistorySourceFingerprint.capture(path), size=32 * 1024 * 1024)
    async def run():
        machine, _transport = _mk_machine()
        ctx = _mk_ctx('thread', 'thread')
        ctx.engine = 'codex'
        machine.sessions[ctx.key] = ctx
        machine._codex_history = SimpleNamespace(can_restore_page=lambda *_a: True)
        started = asyncio.Event()
        async def read_source(*args):
            return source
        async def read(sid, **kwargs):
            if history_profile.get().prefetch:
                started.set()
                await asyncio.Event().wait()
            return History(session_id=sid, revision=machine._history_revision(sid),
                           detail='summary', before=kwargs['before'])
        monkeypatch.setattr(machine, '_history_page_source', read_source)
        monkeypatch.setattr(machine, '_read_requested_history', read)
        head = History(session_id='thread', revision=machine._history_revision('thread'), oldest_id='cursor')
        machine._prefetch_finished_history_page('thread', head)
        await started.wait()
        request = asyncio.create_task(machine._build_requested_history(
            'thread', before='cursor', limit=12, cwd=None, detail='summary'))
        await asyncio.sleep(0.01)
        machine._history_page_prefetch[1].cancel()
        assert (await request).before == 'cursor'
    asyncio.run(run())


def test_prefetch_never_learns_new_browser_aliases(tmp_path, monkeypatch):
    from cc_remote.wrapper.codex_client_messages import CodexClientMessageAliases
    from cc_remote.wrapper.history_metrics import HistoryReadProfile, history_profile
    path = tmp_path / 'rollout'
    path.write_text('fixture\n')
    source = HistorySourceFingerprint.capture(path)
    async def run():
        machine, _ = _mk_machine()
        monkeypatch.setattr(machine, '_codex_history_client_message_ids',
                            lambda _path: CodexClientMessageAliases({}, {}))
        def put(*args, **kwargs):
            pytest.fail('speculative history must not persist a browser identity')
        monkeypatch.setattr(machine._codex_client_messages, 'put', put)
        profile = HistoryReadProfile(prefetch=True)
        token = history_profile.set(profile)
        revision = machine._history_revision('thread')
        try:
            await machine._backfill_official_codex_client_message_ids(
                None, 'thread', str(path), source, {'client'}, (('native', 'item', 'client'),))
        finally:
            history_profile.reset(token)
        assert not profile.cache_allowed and machine._history_revision('thread') == revision
    asyncio.run(run())


def test_revision_change_cancels_pending_materialized_prefetch():
    async def run():
        machine, _ = _mk_machine()
        task = asyncio.create_task(asyncio.Event().wait())
        machine._history_page_prefetch = (('thread', 'cursor', 12, 'r', None), task)
        machine._bump_history_revision('thread')
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not machine._finished_history_pages.pages
    asyncio.run(run())


def test_sidecar_change_invalidates_finished_page_without_transcript_append(tmp_path, monkeypatch):
    from types import SimpleNamespace
    path = tmp_path / 'rollout'
    path.write_text('fixture\n')
    source = HistorySourceFingerprint.capture(path)
    async def run():
        machine, _ = _mk_machine()
        ctx = _mk_ctx('thread', 'thread')
        ctx.engine = 'codex'
        machine.sessions[ctx.key] = ctx
        machine._codex_history = SimpleNamespace(can_restore_page=lambda *_args: True)
        calls = []
        async def read_source(*args):
            return source
        async def build(sid, **kwargs):
            calls.append(1)
            return History(session_id=sid, revision=machine._history_revision(sid), before='cursor',
                           turns=[{'id': 'old', 'prompt': 'question', 'done': True}])
        monkeypatch.setattr(machine, '_history_page_source', read_source)
        monkeypatch.setattr(machine, '_read_requested_history', build)
        async def read():
            return await machine._build_requested_history('thread', before='cursor', limit=12,
                                                          cwd=None, detail='summary')
        await read()
        await read()
        assert len(calls) == 1
        machine._codex_process_clocks.path.write_text('sidecar update')
        await read()
        assert len(calls) == 2
    asyncio.run(run())
