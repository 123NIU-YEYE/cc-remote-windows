"""Exercise real Windows ACLs rather than synthetic POSIX permission bits."""
import sys

import pytest

from cc_remote.windows_privacy import owned_path, private_directory

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="native Windows DACL contract")


def test_new_private_directory_and_inherited_file_are_private(tmp_path):
    directory = tmp_path / "state"
    private_directory(directory)
    private_directory(directory)
    path = directory / "receipt.json"
    path.write_text("{}")
    assert owned_path(directory, private=True)
    assert owned_path(path, private=True)


def test_existing_public_directory_is_rejected_without_acl_repair(tmp_path):
    import ntsecuritycon
    import win32security as security

    directory = tmp_path / "public"
    private_directory(directory)
    descriptor = security.GetNamedSecurityInfo(
        str(directory), security.SE_FILE_OBJECT, security.DACL_SECURITY_INFORMATION)
    acl = descriptor.GetSecurityDescriptorDacl()
    acl.AddAccessAllowedAceEx(security.ACL_REVISION, 3, ntsecuritycon.FILE_GENERIC_READ,
                             security.CreateWellKnownSid(security.WinWorldSid))
    security.SetNamedSecurityInfo(str(directory), security.SE_FILE_OBJECT,
                                 security.DACL_SECURITY_INFORMATION, None, None, acl, None)
    assert owned_path(directory)
    assert not owned_path(directory, private=True)
    with pytest.raises(PermissionError):
        private_directory(directory)
    assert not owned_path(directory, private=True)


def test_owner_rights_ace_refers_to_verified_owner(tmp_path):
    import ntsecuritycon
    import win32security as security

    directory = tmp_path / "owner"
    private_directory(directory)
    acl = security.ACL()
    acl.AddAccessAllowedAceEx(security.ACL_REVISION, 3, ntsecuritycon.FILE_ALL_ACCESS,
                             security.ConvertStringSidToSid("S-1-3-4"))
    security.SetNamedSecurityInfo(str(directory), security.SE_FILE_OBJECT,
                                 security.DACL_SECURITY_INFORMATION, None, None, acl, None)
    assert owned_path(directory, private=True)


def test_private_file_is_protected_at_creation_in_a_public_parent(tmp_path):
    import os
    import ntsecuritycon
    import win32security as security
    from cc_remote.windows_privacy import create_private_file

    directory = tmp_path / "shared"
    private_directory(directory)
    descriptor = security.GetNamedSecurityInfo(str(directory), security.SE_FILE_OBJECT,
                                             security.DACL_SECURITY_INFORMATION)
    acl = descriptor.GetSecurityDescriptorDacl()
    acl.AddAccessAllowedAceEx(security.ACL_REVISION, 3, ntsecuritycon.FILE_GENERIC_READ,
                             security.CreateWellKnownSid(security.WinWorldSid))
    security.SetNamedSecurityInfo(str(directory), security.SE_FILE_OBJECT,
                                 security.DACL_SECURITY_INFORMATION, None, None, acl, None)
    path = directory / "new.json"
    fd = create_private_file(path)
    try:
        assert owned_path(path, private=True)
        assert os.fstat(fd).st_size == 0
        os.write(fd, b"{}")
    finally:
        os.close(fd)
    assert owned_path(path, private=True)
