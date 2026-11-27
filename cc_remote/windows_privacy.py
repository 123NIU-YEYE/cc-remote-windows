"""Native Windows ownership/DACL checks; never infer privacy from POSIX bits."""
from __future__ import annotations

from pathlib import Path


def _security():
    import ntsecuritycon
    import win32api
    import win32con
    import win32security

    token = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32con.TOKEN_QUERY)
    try:
        owner = win32security.GetTokenInformation(token, win32security.TokenUser)[0]
    finally:
        token.Close()
    return win32security, ntsecuritycon, owner


def owned_path(path: str | Path, *, private: bool = False) -> bool:
    security, _rights, current = _security()
    path = Path(path)
    info = path.lstat()
    if getattr(info, "st_file_attributes", 0) & 0x400:  # no reparse points
        return False
    descriptor = security.GetNamedSecurityInfo(
        str(path), security.SE_FILE_OBJECT,
        security.OWNER_SECURITY_INFORMATION | security.DACL_SECURITY_INFORMATION)
    if descriptor.GetSecurityDescriptorOwner() != current:
        return False
    if not private:
        return True
    acl = descriptor.GetSecurityDescriptorDacl()
    if acl is None:
        return False
    # OWNER RIGHTS refers to the verified owner above, not an arbitrary group.
    allowed = {security.ConvertSidToStringSid(current), "S-1-3-4", "S-1-5-18", "S-1-5-32-544"}
    for index in range(acl.GetAceCount()):
        ace = acl.GetAce(index)
        if (ace[0][0] != security.ACCESS_DENIED_ACE_TYPE
                and security.ConvertSidToStringSid(ace[-1]) not in allowed):
            return False
    return True


def private_directory(path: str | Path) -> None:
    """Protect newly created state; reject an unsafe existing directory."""
    import pywintypes
    import win32file

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        win32file.CreateDirectory(str(path), _private_attributes(directory=True))
    except pywintypes.error as exc:
        if exc.winerror != 183:  # ERROR_ALREADY_EXISTS
            raise OSError(exc.winerror, exc.strerror, str(path)) from exc
    if not path.is_dir() or not owned_path(path, private=True):
        raise PermissionError("State directory must be private and owned by this user")


def _private_attributes(*, directory: bool = False):
    import pywintypes
    security, rights, owner = _security()
    acl = security.ACL()
    acl.AddAccessAllowedAceEx(security.ACL_REVISION, 3 if directory else 0, rights.FILE_ALL_ACCESS, owner)
    descriptor = security.SECURITY_DESCRIPTOR()
    descriptor.SetSecurityDescriptorOwner(owner, False)
    descriptor.SetSecurityDescriptorDacl(True, acl, False)
    descriptor.SetSecurityDescriptorControl(security.SE_DACL_PROTECTED, security.SE_DACL_PROTECTED)
    attributes = pywintypes.SECURITY_ATTRIBUTES()
    attributes.bInheritHandle = False
    attributes.SECURITY_DESCRIPTOR = descriptor
    return attributes


def create_private_file(path: str | Path) -> int:
    """Create an exclusive descriptor with a private ACL before any bytes exist."""
    import msvcrt
    import os
    import win32con
    import win32file

    handle = win32file.CreateFile(str(path), win32con.GENERIC_READ | win32con.GENERIC_WRITE,
                                 0, _private_attributes(), win32con.CREATE_NEW, win32con.FILE_ATTRIBUTE_NORMAL, None)
    raw = handle.Detach()
    try:
        return msvcrt.open_osfhandle(raw, os.O_RDWR | os.O_BINARY)
    except Exception:
        win32file.CloseHandle(raw)
        raise
