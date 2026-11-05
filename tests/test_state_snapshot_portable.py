"""A complete stopped-Wrapper snapshot restores files, Work SQLite and journals."""
from contextlib import closing
import os
import sqlite3

from deploy.work_registry_snapshot import create_snapshot, restore_snapshot


def private_dir(path):
    if os.name == "nt":
        from cc_remote.windows_privacy import private_directory
        private_directory(path)
    else:
        path.mkdir(parents=True, mode=0o700)


def test_stopped_snapshot_restores_private_state_databases_and_checkpoint_tree(tmp_path):
    roots = {engine: tmp_path / engine for engine in ("claude", "codex")}
    for root in roots.values():
        private_dir(root)
        with closing(sqlite3.connect(root / "registry.sqlite3")) as db, db:
            db.execute("CREATE TABLE marker(value TEXT)")
            db.execute("INSERT INTO marker VALUES ('before')")
    state = tmp_path / "state"
    private_dir(state)
    controls = state / "codex-session-controls.json"
    controls.write_bytes(b'{"version":1}\r\n')
    controls.chmod(0o600)
    checkpoint = state / "codex-checkpoints/repo/session/objects/blob"
    checkpoint.parent.mkdir(parents=True, mode=0o700)
    checkpoint.write_bytes(b"object\x1a\r\n\x00")
    snapshot = tmp_path / "snapshot"
    create_snapshot(snapshot, roots, state_dir=state)
    controls.write_bytes(b'{"version":2}')
    checkpoint.write_bytes(b"after")
    for root in roots.values():
        with closing(sqlite3.connect(root / "registry.sqlite3")) as db, db:
            db.execute("UPDATE marker SET value='after'")
    restore_snapshot(snapshot)
    assert controls.read_bytes() == b'{"version":1}\r\n'
    assert checkpoint.read_bytes() == b"object\x1a\r\n\x00"
    for root in roots.values():
        with closing(sqlite3.connect(root / "registry.sqlite3")) as db:
            assert db.execute("SELECT value FROM marker").fetchone() == ("before",)
    assert list(state.glob('.checkpoint-displaced-*'))
