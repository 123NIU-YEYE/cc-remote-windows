"""Incremental scans preserve lifecycle and fallback identities across appends."""

import json

import pytest

from cc_remote.wrapper import stream
from cc_remote.wrapper.history_scan_cache import IncrementalJsonlProjection


def test_incremental_scan_reads_only_new_complete_records(tmp_path):
    path = tmp_path / "history.jsonl"
    path.write_text('{"value": 1}\ninvalid\n')
    reads = []

    def update(state, row, index):
        reads.append(index)
        state["values"].append((index, row["value"]))

    cache = IncrementalJsonlProjection()

    def read():
        return cache.read(
            path, lambda: {"values": []}, update, stream._bounded_jsonl_lines
        )

    first = read()
    assert first == read() and reads == [0]
    first["values"].clear()  # caller cannot mutate the checkpoint
    with path.open("a") as file:
        file.write('{"value": 2}')
    assert read()["values"] == [(0, 1), (2, 2)]
    with path.open("a") as file:
        file.write("0\n")  # incomplete/invalid record must be read again
    assert read()["values"] == [(0, 1)]
    with path.open("a") as file:
        file.write('{"value": 3}\n')
    assert read()["values"] == [(0, 1), (3, 3)]
    assert reads == [0, 2, 3]


@pytest.mark.parametrize("mutation", ["rewrite", "truncate", "replace"])
def test_incremental_scan_rebuilds_after_source_mutation(tmp_path, mutation):
    path = tmp_path / "history.jsonl"
    path.write_text('{"value": "old"}\n')
    cache = IncrementalJsonlProjection()

    def read():
        return cache.read(
            path,
            lambda: {"values": []},
            lambda state, row, index: state["values"].append(row["value"]),
            stream._bounded_jsonl_lines,
        )

    assert read()["values"] == ["old"]
    if mutation == "replace":
        new = tmp_path / "new"
        new.write_text('{"value": "new"}\n')
        new.replace(path)
    else:
        path.write_text("" if mutation == "truncate" else '{"value": "new"}\n')
    assert read()["values"] == ([] if mutation == "truncate" else ["new"])


def test_projection_eviction_and_oversized_records_keep_identity(tmp_path, monkeypatch):
    monkeypatch.setattr(stream, "_MAX_TRANSCRIPT_RECORD_CHARS", 32)
    cache = IncrementalJsonlProjection(max_entries=1, max_bytes=100)
    calls = []

    def read(path):
        def update(state, row, index):
            calls.append((path.name, index))
            state["last"] = index

        return cache.read(path, dict, update, stream._bounded_jsonl_lines)

    a, b = tmp_path / "a", tmp_path / "b"
    a.write_text("x" * 100 + "\n{}\n")
    b.write_text("{}\n")
    assert read(a) == {"last": 0}
    read(b)
    read(a)
    assert calls == [("a", 0), ("b", 0), ("a", 0)]
    assert cache._bytes <= 100


def test_agent_late_completion_and_resume_match_fresh_scan(tmp_path, monkeypatch):
    path = tmp_path / "history.jsonl"
    cache = IncrementalJsonlProjection()
    monkeypatch.setattr(stream, "_subagent_projection", cache)
    launch = [
        {"type": "user", "message": {"role": "user", "content": "inspect"}},
        {
            "type": "assistant",
            "timestamp": "2026-10-01T01:00:00Z",
            "message": {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "name": "Agent",
                        "id": "tool-1",
                        "input": {"description": "inspect"},
                    }
                ],
            },
        },
        {
            "type": "user",
            "toolUseResult": {"agentId": "agent-1", "isAsync": True},
            "message": {
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": "tool-1"}],
            },
        },
    ]

    def append(rows):
        with path.open("a") as file:
            file.write("".join(json.dumps(row) + "\n" for row in rows))

    def read():
        return [
            e.model_dump(exclude={"ts"})
            for e in stream.translate_subagent_history("sid", 65536, path=str(path))
        ]

    def assert_fresh():
        incremental = read()
        monkeypatch.setattr(
            stream, "_subagent_projection", IncrementalJsonlProjection()
        )
        assert read() == incremental
        monkeypatch.setattr(stream, "_subagent_projection", cache)
        return incremental

    append(launch)
    assert assert_fresh()[0]["status"] == "unknown"
    append(
        [
            {
                "type": "user",
                "origin": {"kind": "task-notification"},
                "timestamp": "2026-10-01T01:01:00Z",
                "message": {
                    "role": "user",
                    "content": "<task-id>agent-1</task-id><status>completed</status><summary>done</summary>",
                },
            }
        ]
    )
    ended = assert_fresh()[0]
    assert ended["status"] == "succeeded" and ended["duration_ms"] == 60000
    append([launch[-1]])
    assert assert_fresh()[0]["status"] == "unknown"
