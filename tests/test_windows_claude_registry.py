"""Claude records procStart as the creation FILETIME on Windows."""
import json
import os
import sys
from uuid import uuid4

import pytest

from cc_remote.wrapper import claude_registry
from cc_remote.wrapper.process_scan import ProcessIdentity, process_identity

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="native Windows processes")
FILETIME_UNIX_EPOCH_MS = 11_644_473_600_000


def record(root, identity, proc_start, sid):
    path = root / "sessions" / f"{identity.pid}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"pid": identity.pid, "procStart": proc_start, "sessionId": sid,
                                "kind": "interactive"}), encoding="utf-8")


def test_registry_accepts_this_process_creation_filetime(tmp_path, monkeypatch):
    def no_ps(_identity):
        raise AssertionError("Windows must not shell out to ps")

    monkeypatch.setattr(claude_registry, "_native_process_start", no_ps)
    identity = process_identity(os.getpid())
    assert identity is not None
    sid = str(uuid4())
    filetime = (identity.start_ticks + FILETIME_UNIX_EPOCH_MS) * 10_000 + 1234
    for recorded, expected in (
        (str(filetime), (True, sid)),
        (str(filetime + 10_000), (False, None)),  # another generation of the PID
        (str(identity.start_ticks), (False, None)),
        ("Mon Nov 24 09:53:11 2026", (False, None)),
    ):
        record(tmp_path, identity, recorded, sid)
        assert claude_registry.registered_session(identity, str(tmp_path)) == expected


def test_registry_rejects_a_reused_pid(tmp_path, monkeypatch):
    monkeypatch.setattr(claude_registry, "_native_process_start", lambda _id: "")
    identity = process_identity(os.getpid())
    stale = ProcessIdentity(identity.pid, identity.start_ticks - 1000)
    filetime = (stale.start_ticks + FILETIME_UNIX_EPOCH_MS) * 10_000
    record(tmp_path, stale, str(filetime), str(uuid4()))
    assert claude_registry.registered_session(stale, str(tmp_path)) == (False, None)
