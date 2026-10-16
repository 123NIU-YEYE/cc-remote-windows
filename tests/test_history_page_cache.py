"""Completed page reuse must not mask edits, rollback or account boundaries."""

import json

import pytest

from cc_remote.protocol import History
from cc_remote.wrapper.history_page_cache import HistoryPageCache
from cc_remote.wrapper.history_store import HistorySourceFingerprint


def _history(**kwargs):
    return History(
        session_id="account@thread",
        revision="r1",
        before="newer",
        detail="summary",
        turns=[
            {
                "id": "user-old",
                "prompt": "old question",
                "done": True,
                "forkPointId": "native-old",
            }
        ],
        **kwargs,
    )


def _source(path):
    return HistorySourceFingerprint.capture(path)


def _append(path, rows):
    with path.open("a") as file:
        file.write("".join(json.dumps(row) + "\n" for row in rows))


def test_older_page_can_survive_a_proven_unrelated_task_append(tmp_path):
    path = tmp_path / "rollout"
    path.write_text("original\n")
    cache = HistoryPageCache()
    key = ("account@thread", "newer", 12, "r1", None)
    cache.put(key, _source(path), _history(), native_ids={"native-old"})
    first = cache.get(key, _source(path))
    first.turns[0].prompt = "changed"
    assert cache.get(key, _source(path)).turns[0].prompt == "old question"
    _append(
        path,
        [
            {
                "type": "event_msg",
                "payload": {"type": "task_started", "turn_id": "new"},
            },
            {"type": "response_item", "payload": {"type": "message", "content": []}},
            {
                "type": "event_msg",
                "payload": {"type": "task_complete", "turn_id": "new"},
            },
        ],
    )
    assert cache.get(key, _source(path), codex=True) is not None
    assert cache.get(("other@thread", *key[1:]), _source(path), codex=True) is None
    cache.invalidate(key[0])
    assert cache.get(key, _source(path)) is None


@pytest.mark.parametrize(
    "mutation",
    [
        "late_old",
        "rollback",
        "compaction",
        "unknown",
        "partial",
        "replace",
        "truncate",
        "rewrite",
    ],
)
def test_unproven_source_change_cannot_reuse_page(tmp_path, mutation):
    path = tmp_path / "rollout"
    path.write_text("original\n")
    cache = HistoryPageCache()
    key = ("account@thread", "newer", 12, "r1", None)
    cache.put(key, _source(path), _history(), native_ids={"native-old"})
    if mutation in {"replace", "truncate", "rewrite"}:
        if mutation == "replace":
            new = tmp_path / "new"
            new.write_text("original\n")
            new.replace(path)
        else:
            path.write_text("" if mutation == "truncate" else "modified\n")
    elif mutation == "partial":
        with path.open("a") as file:
            file.write('{"type":')
    else:
        event = {
            "late_old": "task_started",
            "rollback": "thread_rolled_back",
            "compaction": "context_compacted",
            "unknown": "message",
        }[mutation]
        _append(
            path,
            [
                {
                    "type": "event_msg",
                    "payload": {
                        "type": event,
                        "turn_id": "native-old" if mutation == "late_old" else "new",
                    },
                }
            ],
        )
    assert cache.get(key, _source(path), codex=True) is None


def test_page_cache_is_bounded_and_rejects_incomplete_or_failed_pages(tmp_path):
    path = tmp_path / "rollout"
    path.write_text("source\n")
    source = _source(path)
    cache = HistoryPageCache(max_pages=1, max_bytes=5000)
    for sid in ["a", "b"]:
        cache.put((sid,), source, _history())
    assert cache.get(("a",), source) is None and cache.get(("b",), source)
    cache.put(("c",), source, _history(error="failed"))
    history = _history()
    history.turns[0].done = False
    cache.put(("d",), source, history)
    assert list(cache.pages) == [("b",)] and cache.bytes <= 5000
    history = _history()
    history.turns[0].prompt = "x" * 6000
    cache.put(("e",), source, history)
    assert list(cache.pages) == [("b",)]


def test_invalidation_during_append_validation_cannot_resurrect_page(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from cc_remote.wrapper import history_page_cache as module

    path = tmp_path / 'rollout'
    path.write_text('source\n')
    cache = HistoryPageCache()
    key = ('account@thread', 'newer', 12, 'r1', None)
    cache.put(key, _source(path), _history(), native_ids={'native-old'})
    with path.open('a') as file:
        file.write('new bytes\n')
    started, release = Event(), Event()
    def validate(*args):
        started.set()
        assert release.wait(timeout=5)
        return True
    monkeypatch.setattr(module, 'unrelated_codex_append', validate)
    with ThreadPoolExecutor() as executor:
        result = executor.submit(cache.get, key, _source(path), codex=True)
        assert started.wait(timeout=5)
        try:
            cache.invalidate(key[0])  # must not wait for worker I/O
        finally:
            release.set()
        assert result.result(timeout=5) is None
    assert cache.bytes == 0 and not cache.pages
