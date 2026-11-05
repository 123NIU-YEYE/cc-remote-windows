"""Explicit capabilities for filesystem integration tests, not logic skips."""
from functools import lru_cache
from pathlib import Path
import sys
import tempfile

import pytest


@lru_cache(maxsize=1)
def long_paths_available():
    if sys.platform != "win32":
        return True
    with tempfile.TemporaryDirectory(prefix="cc-long-path-") as root:
        folder = Path(root) / ("x" * 200)
        try:
            folder.mkdir()
            (folder / ("y" * 60)).write_text("probe", encoding="utf-8")
            return True
        except OSError as exc:
            if getattr(exc, "winerror", None) in {3, 206}:
                return False
            raise


def require_path(path):
    if sys.platform == "win32" and len(str(path)) >= 248 and not long_paths_available():
        pytest.skip("native long-path integration requires a long-path-enabled Windows Python runtime")


def assert_private(path, mode=0o600):
    path = Path(path)
    if sys.platform == "win32":
        from cc_remote.windows_privacy import owned_path
        assert owned_path(path, private=True)
    else:
        assert path.stat().st_mode & 0o777 == mode


def make_public(path):
    if sys.platform == "win32":
        import ntsecuritycon
        import win32security as security
        descriptor = security.GetNamedSecurityInfo(
            str(path), security.SE_FILE_OBJECT, security.DACL_SECURITY_INFORMATION)
        acl = descriptor.GetSecurityDescriptorDacl()
        acl.AddAccessAllowedAceEx(security.ACL_REVISION, 0, ntsecuritycon.FILE_GENERIC_READ,
                                 security.CreateWellKnownSid(security.WinWorldSid))
        security.SetNamedSecurityInfo(str(path), security.SE_FILE_OBJECT,
                                     security.DACL_SECURITY_INFORMATION, None, None, acl, None)
    else:
        path.chmod(0o644)
