"""A partial append must not turn a large Claude transcript into an empty chat."""

import asyncio
import json

import pytest

from cc_remote.wrapper import machine as mm
from tests.test_multisession import _mk_machine


@pytest.mark.parametrize("before", [None, "new-user"])
def test_large_claude_partial_append_is_not_authoritative(monkeypatch, tmp_path, before):
    source = tmp_path / "large-compact.jsonl"
    records = [
        {"type": "user", "uuid": "old-user", "parentUuid": None,
         "message": {"role": "user", "content": "old question"}},
        {"type": "assistant", "uuid": "old-answer", "parentUuid": "old-user",
         "message": {"role": "assistant", "content": [{"type": "text", "text": "old answer"}]}},
        {"type": "system", "subtype": "compact_boundary", "uuid": "boundary",
         "parentUuid": None, "logicalParentUuid": "old-answer"},
        {"type": "user", "uuid": "new-user", "parentUuid": "boundary",
         "message": {"role": "user", "content": "new question"}},
        {"type": "assistant", "uuid": "new-answer", "parentUuid": "new-user",
         "message": {"role": "assistant", "content": [{"type": "text", "text": "new answer"}]}},
        {"type": "api-request-blob", "data": "x" * (1024 * 1024)},
    ]
    source.write_text("".join(json.dumps(row) + "\n" for row in records), encoding="utf-8")
    monkeypatch.setattr(mm, "transcript_path", lambda _sid: str(source))
    monkeypatch.setattr(mm, "get_session_info", lambda _sid: None)
    monkeypatch.setattr(mm, "translate_subagent_history", lambda *_args, **_kwargs: [])

    def no_sdk_parse(*_args, **_kwargs):
        raise AssertionError("large history must stay on the bounded graph reader")

    monkeypatch.setattr(mm, "get_session_messages", no_sdk_parse)

    async def run():
        machine, _ = _mk_machine()
        machine.cfg.history_source_max_bytes = 1024 * 1024
        newest = await machine._build_history(
            "large-claude", limit=1, detail="summary", allow_stale=False)
        assert newest.has_more
        assert newest.oldest_id == "new-user"

        # A reader can sample the source between two writes of the same JSONL row.
        tail = json.dumps({"type": "assistant", "uuid": "tail", "parentUuid": "new-answer",
                           "message": {"role": "assistant", "content": [{"type": "text", "text": "tail"}]}})
        split = len(tail) // 2
        with source.open("a", encoding="utf-8") as stream:
            stream.write(tail[:split])
        failed = await machine._build_history(
            "large-claude", before=before, limit=1, detail="summary", allow_stale=False)
        assert failed.authoritative is False
        assert failed.error
        assert failed.before == before

        # Recover on the next read with the same cursor once the writer finishes.
        with source.open("a", encoding="utf-8") as stream:
            stream.write(tail[split:] + "\n")
        recovered = await machine._build_history(
            "large-claude", before=before, limit=1, detail="summary", allow_stale=False)
        assert recovered.authoritative is not False
        assert recovered.error is None
        assert [turn.prompt for turn in recovered.turns] == [
            "new question" if before is None else "old question"]
        assert recovered.has_more is (before is None)

    asyncio.run(run())
