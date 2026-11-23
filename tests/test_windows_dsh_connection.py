"""DSH pairing checks real Windows ACLs before reading or writing secrets."""
import os
import sys

import pytest

from cc_remote.windows_privacy import owned_descriptor
from cc_remote.wrapper.dsh_client import DshConnection, DshError
from cc_remote.wrapper.dsh_pair import save_connection

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="native Windows DACL contract")


def test_dsh_private_connection_roundtrip_and_hardlink_rejection(tmp_path):
    connection = DshConnection("http://127.0.0.1:3080", "a" * 43)
    path = tmp_path / "private" / "connection.json"
    save_connection(connection, path)
    assert DshConnection.load(path) == connection
    with path.open("rb") as stream:
        assert owned_descriptor(stream.fileno(), private=True)
    os.link(path, path.with_name("hardlink.json"))
    with pytest.raises(DshError, match="权限"):
        DshConnection.load(path)


def test_dsh_rejects_world_readable_acl_despite_posix_mode(tmp_path):
    import ntsecuritycon
    import win32security as security

    path = tmp_path / "private" / "connection.json"
    save_connection(DshConnection("http://127.0.0.1:3080", "a" * 43), path)
    descriptor = security.GetNamedSecurityInfo(str(path), security.SE_FILE_OBJECT,
        security.DACL_SECURITY_INFORMATION)
    acl = descriptor.GetSecurityDescriptorDacl()
    acl.AddAccessAllowedAceEx(security.ACL_REVISION, 0, ntsecuritycon.FILE_GENERIC_READ,
        security.CreateWellKnownSid(security.WinWorldSid))
    security.SetNamedSecurityInfo(str(path), security.SE_FILE_OBJECT,
        security.DACL_SECURITY_INFORMATION, None, None, acl, None)
    path.chmod(0o600)
    with path.open("rb") as stream:
        assert not owned_descriptor(stream.fileno(), private=True)
    with pytest.raises(DshError, match="权限"):
        DshConnection.load(path)


def test_desktop_connection_is_private_and_owned_by_a_live_process(tmp_path):
    import json
    from cc_remote.windows_privacy import create_private_file, private_directory
    from cc_remote.wrapper.dsh_desktop import load_connection

    directory = tmp_path / "desktop"
    private_directory(directory)
    path = directory / "state.json"
    data = {"schema": 1, "pid": os.getpid(), "port": 3080,
            "origin": "http://127.0.0.1:3080", "token": "a" * 43}
    with os.fdopen(create_private_file(path), "w") as stream:
        json.dump(data, stream)
    assert load_connection(path) == DshConnection(data["origin"], data["token"])
    data["pid"] = 2_000_000_000
    path.write_text(json.dumps(data))
    with pytest.raises(DshError, match="桌面版"):
        load_connection(path)
