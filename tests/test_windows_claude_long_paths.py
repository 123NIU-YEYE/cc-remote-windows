"""Claude transcripts of deep projects exceed MAX_PATH (260) on Windows.

Claude writes them with a long-path-aware runtime even when the system's
LongPathsEnabled is off; plain open() then cannot read them, and the history
of such a session came back empty.
"""
import json
import os
import sys
from uuid import uuid4

import pytest
from claude_agent_sdk._internal.sessions import _sanitize_path
from claude_agent_sdk.types import SDKSessionInfo

from cc_remote.wrapper import claude_catalog
from cc_remote.wrapper.stream import transcript_timestamps
from tests.platform_support import extended_path

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows MAX_PATH")


def _deep_transcript(tmp_path):
    config = tmp_path / "account"
    cwd = os.path.join(tmp_path, *(["deep-project-component"] * 7))
    sid = str(uuid4())
    path = config / "projects" / _sanitize_path(cwd) / f"{sid}.jsonl"
    os.makedirs(extended_path(path.parent))
    rows = [
        {"type": "user", "uuid": "u1", "parentUuid": None, "sessionId": sid, "cwd": cwd,
         "timestamp": "2026-11-24T10:00:00.000Z",
         "message": {"role": "user", "content": "check the deep project"}},
        {"type": "assistant", "uuid": "a1", "parentUuid": "u1", "sessionId": sid, "cwd": cwd,
         "timestamp": "2026-11-24T10:00:05.000Z",
         "message": {"role": "assistant", "content": [{"type": "text", "text": "done"}]}},
    ]
    with open(extended_path(path), "w", encoding="utf-8") as stream:
        stream.write("".join(json.dumps(row) + "\n" for row in rows))
    assert len(str(path)) > 260
    return config, cwd, sid, path


def test_deep_project_session_is_listed_with_its_cwd(tmp_path):
    config, cwd, sid, path = _deep_transcript(tmp_path)
    listed = claude_catalog.list_sessions(config, directory=cwd)
    assert [item.cwd for item in listed if item.session_id == sid] == [cwd]
    info = SDKSessionInfo(session_id=sid, summary="deep", last_modified=0)
    assert claude_catalog.recover_session_cwd(info, path).cwd == cwd


def test_deep_project_history_is_not_empty(tmp_path):
    config, _cwd, sid, path = _deep_transcript(tmp_path)
    messages = claude_catalog.get_session_messages(config, sid)
    assert [message.uuid for message in messages] == ["u1", "a1"]
    assert transcript_timestamps(sid, path=str(path)) == {"u1": 1795514400.0, "a1": 1795514405.0}
