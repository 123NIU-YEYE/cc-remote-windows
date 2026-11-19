"""Windows activation must preserve private control files and committed WAL data."""
import json
import os
import sqlite3
import sys

import pytest

from cc_remote.windows_privacy import create_private_file, owned_path, private_directory
from deploy.work_registry_snapshot import create_snapshot, restore_snapshot, WorkRegistrySnapshotError

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows snapshot DACL contract")


def test_windows_snapshot_restores_private_state_and_database(tmp_path):
    roots = {engine: tmp_path / engine for engine in ("claude", "codex")}
    for root in roots.values():
        private_directory(root)
        connection = sqlite3.connect(root / "registry.sqlite3")
        try:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("CREATE TABLE proof (value TEXT)")
            connection.execute("INSERT INTO proof VALUES ('before')")
            connection.commit()
        finally:
            connection.close()
    state = tmp_path / "state"
    private_directory(state)
    controls = state / "claude-session-controls.json"
    with os.fdopen(create_private_file(controls), "w", encoding="utf-8") as stream:
        json.dump({"session": "before"}, stream)
    destination = tmp_path / "snapshot"
    manifest = create_snapshot(destination, roots, state_dir=state)
    metadata = json.loads(manifest.read_text())
    assert metadata["wrapper_state"]["files"][controls.name]["mode"] == 0o600
    assert owned_path(destination, private=True)
    controls.write_text('{"session":"after"}', encoding="utf-8")
    restore_snapshot(destination)
    assert json.loads(controls.read_text())["session"] == "before"
    assert owned_path(controls, private=True)
    for root in roots.values():
        connection = sqlite3.connect(root / "registry.sqlite3")
        try:
            assert connection.execute("SELECT value FROM proof").fetchone() == ("before",)
        finally:
            connection.close()


def test_snapshot_rejects_public_windows_control_state(tmp_path):
    state = tmp_path / "public-state"
    state.mkdir()
    (state / "claude-session-controls.json").write_text("{}")
    import ntsecuritycon
    import win32security as security
    target = state / "claude-session-controls.json"
    info = security.GetNamedSecurityInfo(str(target), security.SE_FILE_OBJECT,
                                        security.DACL_SECURITY_INFORMATION)
    acl = info.GetSecurityDescriptorDacl()
    acl.AddAccessAllowedAceEx(security.ACL_REVISION, 0, ntsecuritycon.FILE_GENERIC_READ,
                             security.CreateWellKnownSid(security.WinWorldSid))
    security.SetNamedSecurityInfo(str(target), security.SE_FILE_OBJECT,
                                 security.DACL_SECURITY_INFORMATION, None, None, acl, None)
    roots = {engine: tmp_path / engine for engine in ("claude", "codex")}
    with pytest.raises(WorkRegistrySnapshotError, match="DACL"):
        create_snapshot(tmp_path / "snapshot", roots, state_dir=state)
