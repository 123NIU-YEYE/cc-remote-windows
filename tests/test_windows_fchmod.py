"""os.fchmod must give owner-only modes an owner-only ACL on Windows.

Python 3.13's native Windows fchmod only toggles the read-only attribute, so a
file created in a shared directory stayed readable by everyone.
"""
import os
import sys

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="native Windows ACLs")


@pytest.fixture
def shared_directory(tmp_path):
    import ntsecuritycon
    import win32security as security

    directory = tmp_path / "shared"
    directory.mkdir()
    acl = security.ACL()
    acl.AddAccessAllowedAceEx(security.ACL_REVISION, 3, ntsecuritycon.FILE_ALL_ACCESS,
                             security.CreateWellKnownSid(security.WinWorldSid))
    security.SetNamedSecurityInfo(
        str(directory), security.SE_FILE_OBJECT,
        security.DACL_SECURITY_INFORMATION | security.PROTECTED_DACL_SECURITY_INFORMATION,
        None, None, acl, None)
    return directory


def test_owner_only_mode_protects_a_file_in_a_shared_directory(shared_directory):
    from cc_remote.windows_privacy import owned_path

    path = shared_directory / "secret.json"
    with open(path, "wb") as stream:
        assert not owned_path(path, private=True)
        os.fchmod(stream.fileno(), 0o600)
        stream.write(b"{}")
    assert owned_path(path, private=True)


def test_owner_only_mode_protects_a_directory_and_its_new_children(shared_directory):
    from cc_remote.windows_privacy import owned_path

    directory = shared_directory / "uploads"
    directory.mkdir()
    descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fchmod(descriptor, 0o700)
    finally:
        os.close(descriptor)
    child = directory / "attachment.txt"
    child.write_text("x", encoding="utf-8")
    assert owned_path(directory, private=True)
    assert owned_path(child, private=True)


def test_shared_mode_keeps_the_inherited_acl(shared_directory):
    from cc_remote.windows_privacy import owned_path

    path = shared_directory / "install.lock"
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT)
    try:
        os.fchmod(descriptor, 0o644)
    finally:
        os.close(descriptor)
    assert not owned_path(path, private=True)


def test_work_upload_directory_binds_and_is_private(tmp_path):
    from cc_remote.windows_privacy import owned_path, private_directory
    from cc_remote.wrapper.machine import WrapperMachine

    work = tmp_path / "work"
    private_directory(work)
    for msg_id in ("first", "second"):
        with WrapperMachine._open_work_upload_dir(str(work), msg_id) as (directory, _fd):
            assert owned_path(directory, private=True)
    assert owned_path(work / "uploads", private=True)
